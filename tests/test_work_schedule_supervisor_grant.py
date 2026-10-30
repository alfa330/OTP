# -*- coding: utf-8 -*-
"""Поимённый допуск к «Графикам работы» чужого отдела — с правами СВ этого отдела.

Решение владельца 23.10.2026: главе ООЗ (users.id 476) открыть графики Тез КЦ
на просмотр и правку, «как СВ этого отдела». Главе отдела раздел показывает
только её отдел: и список сотрудников, и каждая правка режутся по
возглавляемым отделам. Допуск расширяет обе границы на один отдел и только в
этом разделе: СВ отдела видит всех его сотрудников, правит графики операторов
(смены, выходные, статусы, штрафы, тренинги, тех. сбои, правила перерывов), но
не снимает ЧС и не трогает строки СВ.

Функции и ручки исполняются как код: вынимаются из монолита через AST и
работают на маленьком портале. Импортировать монолит нельзя — он на старте
поднимает пул к базе.
"""

import ast
import copy
import logging
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")

UNDER_TEST = (
    "_normalize_user_role",
    "_get_role_level",
    "_has_min_role",
    "_is_admin_role",
    "_is_super_admin_role",
    "_is_supervisor_role",
    "_is_global_admin_requester",
    "_department_scope_id_for_requester",
    "_effective_scoped_manager_role",
    "_effective_management_scope",
    "_normalize_int_id_list",
    "_operator_item_id",
    "_target_user_supervisor_id",
    "_requester_can_access_target_user",
    "_load_target_user_with_scope",
    "_work_schedule_granted_departments",
    "_work_schedule_granted_department_ids",
    "_work_schedule_granted_member_ids",
    "_work_schedule_granted_target",
    "_filter_operators_for_requester_scope",
    "_filter_operators_for_requester_role",
    "_resolve_scoped_operator_for_requester",
    "_load_training_target_with_scope",
    "_management_scope_for_targets",
    "_with_granted_break_rules",
    "_resolve_management_requester",
    "_resolve_work_schedule_viewer",
    "_late_shift_lock_message",
    "_late_shift_locked_days",
    "_late_shift_lock_may_apply",
    "_late_shift_lock_response",
    "_late_shift_lock_payload",
    "_late_shift_lock_applies",
    "_can_remove_dismissal_blacklist",
    "_normalize_management_role",
    "get_operators_with_schedules",
    "save_work_shift",
    "remove_work_schedule_dismissal_blacklist",
    "add_work_schedule_fine",
    "get_work_schedule_break_rules",
    "save_work_schedule_break_rules",
    "create_technical_issue",
    "create_offline_activity",
    "delete_work_shift",
    "toggle_work_day_off",
    "respond_shift_change_request",
    "delete_work_schedule_status_period",
    "apply_work_schedule_bulk_actions",
)


def _database_class(name):
    """Класс уровня модуля database.py (LateShiftLockedError) — настоящий, из исходника."""
    source = (ROOT / "database.py").read_text(encoding="utf-8-sig")
    node = next(n for n in source_cache.parse(source).body if isinstance(n, ast.ClassDef) and n.name == name)
    namespace = {}
    exec(ast.get_source_segment(source, node), namespace)  # noqa: S102
    return namespace[name]


def _constant(name):
    for node in source_cache.parse(BOT_SOURCE).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing constant {name} in bot_schedule2.py")


OOZ, TEZ, SZOV = 2008, 560, 1


class _Portal:
    """ООЗ, Тез КЦ и СЗоВ — как на проде 23.10.2026, только без ФИО."""

    DEPARTMENTS = [
        {"id": SZOV, "code": "szov", "name": "СЗоВ"},
        {"id": TEZ, "code": "tez", "name": "Тез КЦ"},
        {"id": OOZ, "code": "request_processing_department", "name": "ООЗ"},
    ]
    # id → (роль, отдел, СВ)
    PEOPLE = {
        476: ("admin", OOZ, None),      # глава ООЗ — у неё допуск
        523: ("operator", OOZ, None),
        300: ("admin", TEZ, None),      # глава Тез КЦ
        3: ("sv", TEZ, 2),              # СВ Тез КЦ, все операторы отдела — его
        1001: ("operator", TEZ, 3),
        1002: ("trainee", TEZ, 3),
        1003: ("operator", SZOV, 55),
        55: ("sv", SZOV, None),
        9: ("admin", None, None),       # админ портала
    }
    DIRECTIONS = {83: ("ТП линия", TEZ), 78: ("ОП линия", TEZ), 10: ("Основа", SZOV)}
    # id периода статуса → чей он (больничный оператора СЗоВ и Тез КЦ).
    STATUS_PERIODS = {5: 1003, 6: 1001}

    def __init__(self, *, heads=None, inactive=(), people=None, late=None):
        self.heads = {476: {OOZ}, 300: {TEZ}} if heads is None else heads
        self.inactive = set(inactive)
        if people is not None:
            self.PEOPLE = people
        # Опоздания: {(оператор, день): (минуты, ФИО)} — как Database.get_late_shift_days.
        self.late = dict(late or {})
        self.late_queries = []
        self.calls = []
        self.lookups = 0

    def user(self, user_id):
        self.lookups += 1
        person = self.PEOPLE.get(int(user_id))
        if person is None:
            return None
        role, _dept, supervisor_id = person
        # get_user: id (0), роль (3), СВ (6) — остальное ручкам не нужно.
        return (int(user_id), None, f"User {user_id}", role, None, None, supervisor_id)

    def department_of(self, user_id):
        self.lookups += 1
        person = self.PEOPLE.get(int(user_id))
        return person[1] if person else None

    def members(self, department_id):
        return {uid for uid, (_r, dept, _s) in self.PEOPLE.items() if dept == department_id}

    def departments(self):
        self.calls.append(("get_departments",))
        return [dict(item, is_active=item["id"] not in self.inactive) for item in self.DEPARTMENTS]

    def late_days(self, operator_dates):
        pairs = []
        for operator_id, day in operator_dates:
            pairs.append((int(operator_id), date.fromisoformat(day) if isinstance(day, str) else day))
        self.late_queries.append(pairs)
        return {pair: self.late[pair] for pair in pairs if pair in self.late}

    def rules(self, department_id=None):
        names = sorted(
            name for name, dept in self.DIRECTIONS.values()
            if department_id is None or dept == department_id)
        return [{"direction": name, "rules": [{"minMinutes": 360}]} for name in names]

    def db(self):
        return SimpleNamespace(
            get_user=lambda *, id: self.user(id),
            get_user_department_id=self.department_of,
            get_department_member_ids=self.members,
            get_departments=self.departments,
            get_direction_department_id=lambda dir_id: self.DIRECTIONS.get(int(dir_id), (None, None))[1],
            get_directions=lambda department_id=None: [
                {"id": dir_id, "name": name} for dir_id, (name, dept) in self.DIRECTIONS.items()
                if dept == department_id],
            get_work_schedule_break_rules=self.rules,
            save_work_schedule_break_rules=lambda rules: self.calls.append(("save_rules", list(rules))),
            get_operators_with_shifts=lambda *a, **k: [
                {"id": uid, "role": role, "department_id": dept}
                for uid, (role, dept, _s) in sorted(self.PEOPLE.items()) if role in ("operator", "trainee", "sv")],
            save_shift=lambda operator_id, *a, **k: self.calls.append(("save_shift", operator_id)) or 77,
            remove_schedule_dismissal_blacklist=lambda operator_id, actor_id=None: (
                self.calls.append(("unblacklist", operator_id)) or [{"id": 7}]),
            get_operator_with_shifts=lambda *a, **k: None,
            add_manual_fine_for_day=lambda **kwargs: self.calls.append(("fine", kwargs["operator_id"])) or {},
            create_operator_technical_issues=lambda **kwargs: self.calls.append(("tech", kwargs)) or {},
            create_operator_offline_activity=lambda **kwargs: self.calls.append(("offline", kwargs)) or {},
            delete_shift=lambda operator_id, *a, **k: self.calls.append(("delete_shift", operator_id)) or True,
            toggle_day_off=lambda operator_id, *a, **k: self.calls.append(("day_off", operator_id)) or True,
            get_shift_change_request_operator=lambda request_id: int(request_id),
            # Заявка N — на день 24.10 оператора N (как get_shift_change_request_operator).
            get_shift_change_request_days=lambda request_id: [(int(request_id), date(2026, 10, 24))],
            get_late_shift_days=self.late_days,
            respond_shift_change_request=lambda **kw: self.calls.append(
                ("respond", kw["request_id"], kw.get("late_lock"))) or {},
            delete_schedule_status_period=self.delete_status_period,
            apply_work_schedule_bulk_actions=lambda actions, actor_id=None: (
                self.calls.append(("bulk", [a["operator_id"] for a in actions])) or {}),
        )

    def delete_status_period(self, status_period_id, operator_id=None):
        owner = self.STATUS_PERIODS.get(int(status_period_id))
        if owner is None or (operator_id is not None and int(operator_id) != owner):
            return None
        self.calls.append(("delete_status", int(status_period_id)))
        return {"id": int(status_period_id), "operatorId": owner}

    def load(self, requester_id, *, args=None, body=None):
        portal = self
        headed = lambda user_id: frozenset(portal.heads.get(int(user_id or 0), ()))
        namespace = {
            "db": self.db(),
            "g": SimpleNamespace(user_id=requester_id),
            "logging": logging,
            "time": time,
            "datetime": datetime,
            "timedelta": timedelta,
            "jsonify": lambda payload: payload,
            "request": SimpleNamespace(
                args=dict(args or {}), headers={}, get_json=lambda silent=False: dict(body or {})),
            "ROLE_HIERARCHY": _constant("ROLE_HIERARCHY"),
            "TRAINING_TARGET_ROLES": _constant("TRAINING_TARGET_ROLES"),
            "WORK_SCHEDULE_SUPERVISOR_GRANTS": _constant("WORK_SCHEDULE_SUPERVISOR_GRANTS"),
            "LATE_SHIFT_LOCKED_CODE": _constant("LATE_SHIFT_LOCKED_CODE"),
            "LateShiftLockedError": _database_class("LateShiftLockedError"),
            "_get_authenticated_requester": lambda: (requester_id, portal.user(requester_id), None),
            "_headed_department_ids": headed,
            "_headed_department_id": lambda user_id: next(iter(sorted(headed(user_id))), None),
            "_is_employee_accounting_manager": lambda *_a, **_k: False,
            "_trainer_work_schedule_member_ids": lambda: set(),
            "_ws_query_bool": lambda _name, default=False: default,
            "_record_elapsed_server_timing": lambda *_a, **_k: None,
            "_work_schedule_operator_requester": lambda: (requester_id, portal.user(requester_id), None),
        }
        module = source_cache.parse(BOT_SOURCE)
        by_name = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
        missing = set(UNDER_TEST) - set(by_name)
        if missing:
            raise AssertionError(f"Missing functions in bot_schedule2.py: {sorted(missing)}")
        selected = []
        for name in UNDER_TEST:
            node = copy.deepcopy(by_name[name])
            node.decorator_list = []
            selected.append(node)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)  # noqa: S102
        return namespace


def _requester(portal, user_id):
    return portal.user(user_id)


def _visible(portal, requester_id):
    ns = portal.load(requester_id)
    items = [{"id": uid} for uid in sorted(portal.PEOPLE)]
    return [item["id"] for item in ns["_filter_operators_for_requester_scope"](
        _requester(portal, requester_id), requester_id, items)]


class GrantDecisionTests(unittest.TestCase):
    def test_the_grant_is_exactly_the_owners_decision(self):
        """Один человек и один отдел. Новый — только по слову владельца."""
        self.assertEqual(_constant("WORK_SCHEDULE_SUPERVISOR_GRANTS"), {476: ("tez",)})


class VisibilityTests(unittest.TestCase):
    """Кого раздел показывает: та же функция режет список, историю, очередь заявок."""

    def test_head_sees_her_department_and_the_granted_one(self):
        self.assertEqual(_visible(_Portal(), 476), [3, 300, 476, 523, 1001, 1002])

    def test_nobody_else_gets_the_department(self):
        portal = _Portal()
        self.assertEqual(_visible(portal, 300), [3, 300, 1001, 1002], "глава Тез КЦ — свой отдел")
        self.assertEqual(_visible(portal, 55), [55, 1003], "СВ СЗоВ — свой отдел")
        self.assertEqual(_visible(portal, 523), [], "оператор ООЗ")
        self.assertEqual(_visible(portal, 9), sorted(portal.PEOPLE), "админ портала — все")

    def test_a_headed_department_is_not_a_grant(self):
        """Возглавь она Тез КЦ — там права главы, а не ограничения СВ."""
        portal = _Portal(heads={476: {OOZ, TEZ}, 300: set()})
        ns = portal.load(476)
        self.assertEqual(ns["_work_schedule_granted_departments"](476), [])

    def test_an_inactive_department_is_not_granted(self):
        portal = _Portal(inactive={TEZ})
        self.assertEqual(_visible(portal, 476), [476, 523])

    def test_departments_are_read_once_per_request(self):
        """Массовые правки зовут проверку на каждую строку — отделы из кэша запроса."""
        portal = _Portal()
        ns = portal.load(476)
        for _ in range(5):
            ns["_work_schedule_granted_department_ids"](476)
        self.assertEqual(portal.calls.count(("get_departments",)), 1)

    def test_people_without_a_grant_do_not_read_departments(self):
        portal = _Portal()
        ns = portal.load(55)
        self.assertEqual(ns["_work_schedule_granted_departments"](55), [])
        self.assertNotIn(("get_departments",), portal.calls)


class EditBoundaryTests(unittest.TestCase):
    """Чей график она правит: граница СВ отдела — только операторы."""

    def _resolve(self, operator_id, **kwargs):
        portal = _Portal()
        ns = portal.load(476)
        return ns["_resolve_scoped_operator_for_requester"](
            _requester(portal, 476), 476, operator_id, **kwargs)

    def test_operators_of_the_granted_department_are_editable(self):
        target, error = self._resolve(1001)
        self.assertIsNone(error)
        self.assertEqual(target[0], 1001)

    def test_her_own_department_keeps_working(self):
        target, error = self._resolve(523)
        self.assertIsNone(error)
        self.assertEqual(target[0], 523)

    def test_supervisor_rows_staff_outside_and_other_roles_stay_closed(self):
        for operator_id, why in ((3, "строка СВ Тез КЦ"), (300, "глава Тез КЦ"),
                                 (1002, "стажёр: у СВ правка графика — только операторам"),
                                 (1003, "оператор СЗоВ")):
            with self.subTest(why):
                target, error = self._resolve(operator_id)
                self.assertIsNone(target)
                self.assertEqual(error, ("Forbidden for this operator", 403))

    def test_unknown_operator_is_still_404(self):
        self.assertEqual(self._resolve(99999), (None, ("Operator not found", 404)))

    def test_allow_grant_false_keeps_the_old_boundary(self):
        self.assertEqual(self._resolve(1001, allow_grant=False)[1], ("Forbidden for this operator", 403))
        self.assertIsNone(self._resolve(523, allow_grant=False)[1])

    def test_the_grant_does_not_open_the_department_to_others(self):
        portal = _Portal()
        ns = portal.load(55)
        target, error = ns["_resolve_scoped_operator_for_requester"](_requester(portal, 55), 55, 1001)
        self.assertIsNone(target)
        self.assertEqual(error[1], 403)

    def test_trainings_follow_the_training_audience(self):
        """Тренинг СВ записывает и стажёру, и коллеге-СВ своего отдела."""
        portal = _Portal()
        ns = portal.load(476)
        load = lambda operator_id: ns["_load_training_target_with_scope"](_requester(portal, 476), 476, operator_id)
        for operator_id in (1001, 1002, 3, 523):
            with self.subTest(operator_id=operator_id):
                target, error = load(operator_id)
                self.assertIsNone(error)
                self.assertEqual(target[0], operator_id)
        for operator_id in (300, 1003):
            with self.subTest(operator_id=operator_id):
                self.assertEqual(load(operator_id)[1], ("Forbidden for this operator", 403))

    def test_day_records_are_written_in_the_granted_department(self):
        """Тех. сбой и офлайн-активность: запись целиком в отделе допуска — права
        СВ этого отдела; смешанная — прежняя граница, и база отвергнет чужих."""
        portal = _Portal()
        ns = portal.load(476)
        scope = lambda *a: ns["_management_scope_for_targets"](_requester(portal, 476), 476, *a)
        self.assertEqual(scope([1001]), ("sv", TEZ))
        self.assertEqual(scope([], [83]), ("sv", TEZ))
        self.assertEqual(scope([523]), ("sv", OOZ))
        self.assertEqual(scope([1001, 523]), ("sv", OOZ))
        self.assertEqual(scope([1003]), ("sv", OOZ))
        self.assertEqual(scope([None, "x"]), ("sv", OOZ))
        # Оператор без отдела — прежняя граница, а не падение на int(None).
        portal_none = _Portal(people={**_Portal.PEOPLE, 1005: ("operator", None, None)})
        ns_none = portal_none.load(476)
        self.assertEqual(
            ns_none["_management_scope_for_targets"](_requester(portal_none, 476), 476, [1005]), ("sv", OOZ))

        ns55 = portal.load(55)
        self.assertEqual(ns55["_management_scope_for_targets"](_requester(portal, 55), 55, [1001]), ("sv", SZOV))
        ns9 = portal.load(9)
        self.assertEqual(ns9["_management_scope_for_targets"](_requester(portal, 9), 9, [1001]), ("admin", None))


class RouteTests(unittest.TestCase):
    """Ручки раздела с настоящей границей."""

    def test_operators_list_reports_the_granted_departments(self):
        portal = _Portal()
        payload, status = portal.load(476, args={"start_date": "2026-10-01", "end_date": "2026-10-31"})[
            "get_operators_with_schedules"]()
        self.assertEqual(status, 200)
        # С направлениями отдела: справочник направлений главе режется до её отдела,
        # а массовая тех. причина по Тез КЦ без них пуста.
        self.assertEqual(payload["granted_departments"], [{
            "id": TEZ, "code": "tez", "name": "Тез КЦ",
            "directions": [{"id": 83, "name": "ТП линия"}, {"id": 78, "name": "ОП линия"}],
        }])
        self.assertEqual([op["id"] for op in payload["operators"]], [3, 523, 1001, 1002])

        payload, _ = _Portal().load(55, args={"start_date": "2026-10-01", "end_date": "2026-10-31"})[
            "get_operators_with_schedules"]()
        self.assertEqual(payload["granted_departments"], [])

    def test_shift_is_saved_for_an_operator_of_the_granted_department(self):
        for operator_id, expected in ((1001, 200), (523, 200), (3, 403), (1003, 403)):
            with self.subTest(operator_id=operator_id):
                portal = _Portal()
                body = {"operator_id": operator_id, "shift_date": "2026-10-24",
                        "start_time": "09:00", "end_time": "18:00"}
                _payload, status = portal.load(476, body=body)["save_work_shift"]()
                self.assertEqual(status, expected)
                saved = [call for call in portal.calls if call[0] == "save_shift"]
                self.assertEqual(saved, [("save_shift", operator_id)] if expected == 200 else [])

    def test_blacklist_stays_with_the_head_of_the_department(self):
        portal = _Portal()
        _payload, status = portal.load(476, body={"operator_id": 1001})["remove_work_schedule_dismissal_blacklist"]()
        self.assertEqual(status, 403)
        self.assertNotIn(("unblacklist", 1001), portal.calls)

        portal = _Portal()
        _payload, status = portal.load(476, body={"operator_id": 523})["remove_work_schedule_dismissal_blacklist"]()
        self.assertEqual(status, 200)
        self.assertIn(("unblacklist", 523), portal.calls)

    def test_fine_stays_closed_in_the_granted_department(self):
        """СВ штрафует только своих подчинённых, а по допуску она ничей не СВ:
        штраф операторам Тез КЦ ей не открыт. Своему отделу — как раньше."""
        body = {"day": "2026-10-20", "amount": 500, "reason": "Опоздание"}
        for operator_id, expected in ((1001, 403), (1003, 403), (523, 200)):
            with self.subTest(operator_id=operator_id):
                portal = _Portal()
                _payload, status = portal.load(476, body=dict(body, operator_id=operator_id))["add_work_schedule_fine"]()
                self.assertEqual(status, expected)
                self.assertEqual(("fine", operator_id) in portal.calls, expected == 200)
        portal = _Portal()
        _payload, status = portal.load(300, body=dict(body, operator_id=1001))["add_work_schedule_fine"]()
        self.assertEqual(status, 200, "глава Тез КЦ штрафует, как и раньше")

    def test_status_period_is_not_deleted_by_bare_id(self):
        """Без operator_id удаление шло по одному id, а граница проверялась уже
        после него: глава с ролью admin стирала статус любого отдела и получала
        403 на уже удалённую запись. Теперь так может только глобальный админ."""
        for requester_id in (476, 300, 55):
            with self.subTest(requester_id=requester_id):
                portal = _Portal()
                _payload, status = portal.load(requester_id, body={"status_period_id": 5})[
                    "delete_work_schedule_status_period"]()
                self.assertEqual(status, 400)
                self.assertNotIn(("delete_status", 5), portal.calls)
        portal = _Portal()
        _payload, status = portal.load(9, body={"status_period_id": 5})["delete_work_schedule_status_period"]()
        self.assertEqual(status, 200)
        self.assertIn(("delete_status", 5), portal.calls)

    def test_status_period_of_the_granted_department_is_deleted_with_operator(self):
        portal = _Portal()
        _payload, status = portal.load(476, body={"status_period_id": 6, "operator_id": 1001})[
            "delete_work_schedule_status_period"]()
        self.assertEqual(status, 200)
        self.assertIn(("delete_status", 6), portal.calls)
        # Чужой период под своим оператором база не удалит — и сервер ответит 404.
        portal = _Portal()
        _payload, status = portal.load(476, body={"status_period_id": 5, "operator_id": 1001})[
            "delete_work_schedule_status_period"]()
        self.assertEqual(status, 404)
        self.assertNotIn(("delete_status", 5), portal.calls)

    def test_every_write_of_the_day_reaches_the_granted_department(self):
        """Каждая пишущая ручка — как код: оператор Тез КЦ да, СЗоВ нет."""
        cases = (
            ("delete_work_shift", "delete_shift",
             lambda op: {"operator_id": op, "shift_date": "2026-10-24", "start_time": "09:00", "end_time": "18:00"}),
            ("toggle_work_day_off", "day_off", lambda op: {"operator_id": op, "day_off_date": "2026-10-24"}),
            ("respond_shift_change_request", "respond", lambda op: {"request_id": op, "action": "approve"}),
            ("apply_work_schedule_bulk_actions", "bulk",
             lambda op: {"actions": [{"action": "set_day_off", "operator_id": op, "date": "2026-10-24"}]}),
        )
        for handler, call, body in cases:
            for operator_id, expected in ((1001, 200), (523, 200), (1003, 403)):
                with self.subTest(handler=handler, operator_id=operator_id):
                    portal = _Portal()
                    _payload, status = portal.load(476, body=body(operator_id))[handler]()
                    self.assertEqual(status, expected)
                    self.assertEqual(any(c[0] == call for c in portal.calls), expected == 200)

    def test_bulk_edit_costs_her_no_more_than_the_head_of_the_department(self):
        """Проверка цели по допуску запоминается на запрос: месяц одному
        оператору не превращается в вдвое больше запросов к базе."""
        actions = [{"action": "set_day_off", "operator_id": 1001, "date": "2026-10-%02d" % day}
                   for day in range(1, 32)]
        lookups = {}
        for requester_id in (476, 300):
            portal = _Portal()
            _payload, status = portal.load(requester_id, body={"actions": [dict(a) for a in actions]})[
                "apply_work_schedule_bulk_actions"]()
            self.assertEqual(status, 200)
            lookups[requester_id] = portal.lookups
        self.assertLessEqual(lookups[476], lookups[300] + 4, lookups)

    def test_break_rules_include_the_granted_department(self):
        """Перерывы смены сетка считает по этим правилам: без них правка смены
        в Тез КЦ ушла бы без автоперерывов."""
        directions = lambda requester_id: [
            item["direction"] for item in _Portal().load(requester_id)["get_work_schedule_break_rules"]()[0][
                "direction_rules"]]
        self.assertEqual(directions(476), ["ОП линия", "ТП линия"])
        self.assertEqual(directions(55), ["Основа"])
        self.assertEqual(directions(300), ["ОП линия", "ТП линия"])
        self.assertEqual(directions(9), ["ОП линия", "Основа", "ТП линия"])

    def test_break_rules_save_keeps_to_her_departments(self):
        portal = _Portal()
        body = {"direction_rules": [{"direction": "ТП линия", "rules": []}, {"direction": "Основа", "rules": []}]}
        payload, status = portal.load(476, body=body)["save_work_schedule_break_rules"]()
        self.assertEqual(status, 200)
        saved = [call[1] for call in portal.calls if call[0] == "save_rules"]
        self.assertEqual(saved, [[{"direction": "ТП линия", "rules": []}]])
        # Ответ — в той же области, что и чтение: окно правил не теряет Тез КЦ.
        self.assertEqual([item["direction"] for item in payload["direction_rules"]], ["ОП линия", "ТП линия"])


class LateShiftLockUnderGrantTests(unittest.TestCase):
    """Смену с опозданием меняют админ и выше (владелец, 20.10.2026), но в отделе
    допуска админ — «как СВ этого отдела» (владелец, 30.10.2026): глава ООЗ (476)
    смены Тез КЦ с опозданием не меняет, а в своём ООЗ меняет, как любой админ."""

    DAY = date(2026, 10, 24)
    LATE = {(1001, DAY): (14, "User 1001"), (523, DAY): (9, "User 523")}
    WRITES = (
        ("save_work_shift", "save_shift",
         lambda op: {"operator_id": op, "shift_date": "2026-10-24", "start_time": "09:30", "end_time": "18:00"}),
        ("delete_work_shift", "delete_shift",
         lambda op: {"operator_id": op, "shift_date": "2026-10-24", "start_time": "09:00", "end_time": "18:00"}),
        ("toggle_work_day_off", "day_off", lambda op: {"operator_id": op, "day_off_date": "2026-10-24"}),
        ("respond_shift_change_request", "respond", lambda op: {"request_id": op, "action": "approve"}),
        ("apply_work_schedule_bulk_actions", "bulk",
         lambda op: {"actions": [{"action": "set_day_off", "operator_id": op, "date": "2026-10-24"}]}),
    )

    def _write(self, requester_id, handler, body):
        portal = _Portal(late=self.LATE)
        payload, status = portal.load(requester_id, body=body)[handler]()
        return payload, status, portal

    def test_every_write_of_a_late_day_is_closed_to_her_in_the_granted_department(self):
        for handler, call, body in self.WRITES:
            with self.subTest(handler=handler):
                payload, status, portal = self._write(476, handler, body(1001))
                self.assertEqual(status, 403)
                self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
                self.assertFalse(any(c[0] == call for c in portal.calls))

    def test_her_own_department_keeps_the_admin_rule(self):
        for handler, call, body in self.WRITES:
            with self.subTest(handler=handler):
                payload, status, portal = self._write(476, handler, body(523))
                self.assertEqual(status, 200, payload)
                self.assertTrue(any(c[0] == call for c in portal.calls))
                # Опоздания своего отдела у неё даже не запрашиваются.
                self.assertEqual(portal.late_queries, [])

    def test_head_of_the_granted_department_and_its_supervisor_keep_their_rules(self):
        body = {"operator_id": 1001, "shift_date": "2026-10-24", "start_time": "09:30", "end_time": "18:00"}
        payload, status, portal = self._write(300, "save_work_shift", body)
        self.assertEqual(status, 200, "глава Тез КЦ с ролью admin меняет, как раньше")
        self.assertEqual(portal.late_queries, [])
        payload, status, _portal = self._write(3, "save_work_shift", body)
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")

    def test_bulk_edit_of_both_departments_names_only_the_granted_late_day(self):
        body = {"actions": [
            {"action": "set_day_off", "operator_id": 523, "date": "2026-10-24"},
            {"action": "set_day_off", "operator_id": 1001, "date": "2026-10-24"},
        ]}
        payload, status, portal = self._write(476, "apply_work_schedule_bulk_actions", body)
        self.assertEqual(status, 403)
        self.assertEqual(payload["locked"], [{"operator_id": 1001, "date": "2026-10-24"}])
        self.assertEqual(portal.late_queries, [[(1001, self.DAY)]])
        self.assertNotIn("bulk", [c[0] for c in portal.calls])

    def test_approval_tells_the_database_whom_the_lock_holds(self):
        """Соседний день склеенной ночи база проверяет сама — если замок держит
        согласующего: Дану на заявке оператора Тез КЦ, но не на своём ООЗ."""
        for operator_id, expected in ((1001, True), (523, False)):
            with self.subTest(operator_id=operator_id):
                portal = _Portal(late={})
                _payload, status = portal.load(476, body={"request_id": operator_id, "action": "approve"})[
                    "respond_shift_change_request"]()
                self.assertEqual(status, 200)
                self.assertIn(("respond", operator_id, expected), portal.calls)

    def test_a_day_without_lateness_stays_open_to_her(self):
        portal = _Portal(late={})
        body = {"operator_id": 1001, "shift_date": "2026-10-24", "start_time": "09:30", "end_time": "18:00"}
        _payload, status = portal.load(476, body=body)["save_work_shift"]()
        self.assertEqual(status, 200)
        self.assertEqual(portal.late_queries, [[(1001, self.DAY)]])


class DayRecordRouteTests(unittest.TestCase):
    """Тех. сбой и офлайн-активность из окна дня — с какой границей уходят в базу."""

    def _scope(self, handler, body, requester_id=476):
        portal = _Portal()
        _payload, status = portal.load(requester_id, body=body)[handler]()
        self.assertEqual(status, 201)
        (kind, kwargs), = [call for call in portal.calls if call[0] in ("tech", "offline")]
        return kwargs["requester_role"], kwargs["scope_department_id"]

    def test_technical_issue_of_the_granted_department_goes_with_its_scope(self):
        issue = lambda operators, directions=(): {
            "operator_ids": list(operators), "direction_ids": list(directions),
            "date": "2026-10-23", "start_time": "12:00", "end_time": "12:20", "reason": "x"}
        self.assertEqual(self._scope("create_technical_issue", issue([1001])), ("sv", TEZ))
        self.assertEqual(self._scope("create_technical_issue", issue([], [83])), ("sv", TEZ))
        self.assertEqual(self._scope("create_technical_issue", issue([523])), ("sv", OOZ))
        self.assertEqual(self._scope("create_technical_issue", issue([1001, 523])), ("sv", OOZ))
        self.assertEqual(self._scope("create_technical_issue", issue([1001]), requester_id=55), ("sv", SZOV))

    def test_offline_activity_of_the_granted_department_goes_with_its_scope(self):
        activity = {"operator_id": 1001, "date": "2026-10-23", "start_time": "13:00", "end_time": "13:30"}
        self.assertEqual(self._scope("create_offline_activity", activity), ("sv", TEZ))
        self.assertEqual(self._scope("create_offline_activity", dict(activity, operator_id=523)), ("sv", OOZ))
        # Запись по группе проверяет группу отдела — допуск её не расширяет.
        self.assertEqual(self._scope("create_offline_activity", dict(activity, group_id=40)), ("sv", OOZ))


def _calls_in(func_node, name):
    return [node for node in ast.walk(func_node)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name]


class WiringTests(unittest.TestCase):
    """Где допуск подключён — и где намеренно нет. По дереву разбора: имя в
    комментарии или лишний фильтр рядом проверку не обманут."""

    FUNCS = {node.name: node for node in source_cache.parse(BOT_SOURCE).body if isinstance(node, ast.FunctionDef)}

    # Пишущие ручки раздела, где допуск обязан действовать, и где он закрыт.
    GRANTED_WRITES = {
        "add_training_rejection", "recalculate_work_schedule_breaks", "save_work_shift",
        "delete_work_shift", "toggle_work_day_off", "save_work_schedule_status_period",
        "delete_work_schedule_status_period", "save_shifts_bulk", "apply_work_schedule_bulk_actions",
        "aggregate_work_schedule_metrics", "resolve_work_schedule_auto_flag",
    }
    CLOSED = {"remove_work_schedule_dismissal_blacklist", "add_work_schedule_fine"}

    def test_every_write_resolves_through_the_grant(self):
        callers = {}
        for name, node in self.FUNCS.items():
            for call in _calls_in(node, "_resolve_scoped_operator_for_requester"):
                callers.setdefault(name, []).append(call)
        self.assertEqual(set(callers), self.GRANTED_WRITES | self.CLOSED)
        for name, calls in callers.items():
            for call in calls:
                keywords = {item.arg: item.value for item in call.keywords}
                with self.subTest(name=name):
                    if name in self.CLOSED:
                        self.assertIs(getattr(keywords.get("allow_grant"), "value", None), False)
                    else:
                        self.assertNotIn("allow_grant", keywords)

    def test_section_reads_call_the_granted_filter_and_only_it(self):
        for name in ("get_operators_with_schedules", "get_work_schedule_history",
                     "review_shift_change_requests", "respond_shift_change_request",
                     "get_work_schedule_status_track", "get_work_schedule_training_news",
                     "export_work_schedules_excel", "_work_schedule_operator_ids"):
            with self.subTest(name=name):
                self.assertTrue(_calls_in(self.FUNCS[name], "_filter_operators_for_requester_scope"))
                self.assertFalse(_calls_in(self.FUNCS[name], "_filter_operators_for_requester_role"))

    def test_trainings_resolve_through_the_granted_loader(self):
        for name in ("add_training", "update_training", "delete_training"):
            with self.subTest(name=name):
                self.assertTrue(_calls_in(self.FUNCS[name], "_load_training_target_with_scope"))
                self.assertFalse(_calls_in(self.FUNCS[name], "_load_target_user_with_scope"))

    def test_day_records_use_the_granted_scope(self):
        for name in ("create_technical_issue", "create_offline_activity"):
            with self.subTest(name=name):
                self.assertTrue(_calls_in(self.FUNCS[name], "_management_scope_for_targets"))

    def test_other_sections_keep_their_boundary(self):
        """Правка и удаление тех. сбоев и офлайн-активностей живут в своих
        разделах — их граница прежняя."""
        for name in ("update_technical_issue_batch", "delete_technical_issue",
                     "update_offline_activity", "delete_offline_activity",
                     "list_technical_issues", "list_offline_activities"):
            with self.subTest(name=name):
                self.assertFalse(_calls_in(self.FUNCS[name], "_management_scope_for_targets"))
        for name in ("_requester_can_access_target_user", "_authorize_operator_scope", "_load_target_user_with_scope"):
            with self.subTest(name=name):
                called = {getattr(n.func, "id", "") for n in ast.walk(self.FUNCS[name]) if isinstance(n, ast.Call)}
                self.assertFalse({c for c in called if c.startswith("_work_schedule_granted")}, name)


if __name__ == "__main__":
    unittest.main()
