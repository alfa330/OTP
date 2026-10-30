# -*- coding: utf-8 -*-
"""Смену, на которую человек опоздал, СВ не меняет — только админ и выше.

Правило владельца (20.10.2026): «если человек опоздал на смену, сделать так,
чтобы СВ не смог ему после этого сменить смену, это могут сделать админ и
выше». Без него опоздание стирается правкой: пересчёт часов берёт начало смены
из графика, и сдвинутая смена «опаздывать» перестаёт — вместе со штрафом.

Проверяем каждую дорогу, которой управленец меняет смену: правка, удаление,
выходной, массовое сохранение, массовые действия, согласование заявки, импорт
Excel и админские правки аукциона (снять добор, добрать за оператора). Обмен
сменами между операторами на такой день тоже не создаётся и не принимается. Монолиты из тестов не импортируются (поднимают пул к базе), поэтому
роуты и методы берутся из исходника через ast, как в остальном наборе.
"""

import ast
import copy
import logging
import textwrap
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
APP_SOURCE = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8")


def _bot_functions(*names, namespace):
    module = source_cache.parse(BOT_SOURCE)
    by_name = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    missing = set(names) - set(by_name)
    if missing:
        raise AssertionError(f"Missing functions in bot_schedule2.py: {sorted(missing)}")
    selected = []
    for name in names:
        node = copy.deepcopy(by_name[name])
        node.decorator_list = []
        selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def _bot_constant(name):
    module = source_cache.parse(BOT_SOURCE)
    for node in module.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing constant {name} in bot_schedule2.py")


def _database_methods(*names, namespace):
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    by_name = {node.name: node for node in database_class.body if isinstance(node, ast.FunctionDef)}
    missing = set(names) - set(by_name)
    if missing:
        raise AssertionError(f"Missing Database methods: {sorted(missing)}")
    for name in names:
        exec(textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, by_name[name])), namespace)
    return namespace


def _database_class(name):
    """Класс уровня модуля database.py (LateShiftLockedError) — настоящий, из исходника."""
    module = source_cache.parse(DATABASE_SOURCE)
    node = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == name)
    namespace = {}
    exec(textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node)), namespace)
    return namespace[name]


LateShiftLockedError = _database_class("LateShiftLockedError")


def _function_source(name):
    module = source_cache.parse(BOT_SOURCE)
    node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(BOT_SOURCE, node)


def _user(user_id, role):
    # Роуты и проверки прав читают только id (0) и роль (3).
    return (user_id, None, f"User {user_id}", role)


LATE_DAY = date(2026, 10, 20)


class _RouteDB:
    """Опоздание: оператор 50 опоздал 20.10 на 17 минут."""

    def __init__(self, late=None):
        self.late = {(50, LATE_DAY): (17, "Иванова Анна")} if late is None else late
        self.late_queries = []
        self.calls = []

    def get_late_shift_days(self, operator_dates):
        pairs = []
        for operator_id, day in operator_dates:
            if isinstance(day, str):
                day = date.fromisoformat(day)
            pairs.append((int(operator_id), day))
        self.late_queries.append(pairs)
        return {pair: self.late[pair] for pair in pairs if pair in self.late}

    # Отделы для поимённого допуска «как СВ этого отдела» (WORK_SCHEDULE_SUPERVISOR_GRANTS):
    # 50 и 51 — Тез КЦ, 60 — ООЗ, которым руководит 476.
    def get_departments(self):
        return [{"id": 560, "code": "tez", "name": "Тез КЦ"},
                {"id": 2008, "code": "request_processing_department", "name": "ООЗ"}]

    def get_department_member_ids(self, department_id):
        return {560: {50, 51}, 2008: {60, 476}}.get(int(department_id), set())

    def save_shift(self, *args, **kwargs):
        self.calls.append(("save_shift", args, kwargs))
        return 501

    def delete_shift(self, *args, **kwargs):
        self.calls.append(("delete_shift", args, kwargs))
        return True

    def toggle_day_off(self, *args, **kwargs):
        self.calls.append(("toggle_day_off", args, kwargs))
        return True

    def save_shifts_bulk(self, *args, **kwargs):
        self.calls.append(("save_shifts_bulk", args, kwargs))
        return [1, 2]

    def apply_work_schedule_bulk_actions(self, *args, **kwargs):
        self.calls.append(("bulk_actions", args, kwargs))
        return {"applied": len(args[0])}

    def get_shift_change_request_operator(self, request_id):
        return 50

    def get_shift_change_request_days(self, request_id):
        self.calls.append(("request_days", request_id))
        return [(50, LATE_DAY)]

    def respond_shift_change_request(self, **kwargs):
        self.calls.append(("respond", kwargs))
        return {"id": kwargs["request_id"], "status": kwargs["action"]}

    def get_shift_auction_target_days(self, **kwargs):
        # Лот 7 — смена 20.10, снятие снимает добор оператора 50.
        self.calls.append(("target_days", kwargs))
        if kwargs.get("lot_id") != 7:
            return []
        return [(int(kwargs.get("operator_id") or 50), LATE_DAY)]

    def admin_unclaim_shift(self, **kwargs):
        self.calls.append(("unclaim", kwargs))
        return {"operator_id": 50}

    def admin_claim_shift_for_operator(self, **kwargs):
        self.calls.append(("claim_for_operator", kwargs))
        return {"operator_id": kwargs["operator_id"]}


class _Request:
    method = "POST"

    def __init__(self, body):
        self.body = body

    def get_json(self, silent=False):
        return self.body


ROUTES = (
    "save_work_shift",
    "delete_work_shift",
    "toggle_work_day_off",
    "save_shifts_bulk",
    "apply_work_schedule_bulk_actions",
    "respond_shift_change_request",
    "api_shift_auction_admin_unclaim_shift",
    "api_shift_auction_admin_claim_for_operator",
)


class LateShiftLockRouteTests(unittest.TestCase):
    def _call(self, route, requester, body, *, db=None, heads=()):
        db = db or _RouteDB()
        namespace = {
            "ROLE_HIERARCHY": _bot_constant("ROLE_HIERARCHY"),
            "LATE_SHIFT_LOCKED_CODE": _bot_constant("LATE_SHIFT_LOCKED_CODE"),
            "WORK_SCHEDULE_SUPERVISOR_GRANTS": _bot_constant("WORK_SCHEDULE_SUPERVISOR_GRANTS"),
            "LateShiftLockedError": LateShiftLockedError,
            "g": SimpleNamespace(),
            "jsonify": lambda payload: payload,
            "request": _Request(body),
            "logging": logging,
            "db": db,
            "_get_authenticated_requester": lambda: (requester[0], requester, None),
            "_headed_department_id": lambda requester_id: (sorted(heads)[0] if heads else None),
            "_headed_department_ids": lambda requester_id: frozenset(heads),
            "_resolve_scoped_operator_for_requester": (
                lambda user_data, requester_id, operator_id: (_user(int(operator_id), "operator"), None)
            ),
            "_filter_operators_for_requester_scope": lambda user_data, requester_id, items: list(items),
            "_work_schedule_operator_requester": lambda: (requester[0], requester, None),
            "_build_cors_preflight_response": lambda: ({}, 204),
            "_shift_auction_test_error_response": lambda error: ({"error": str(error)}, 400),
        }
        _bot_functions(
            "_normalize_user_role",
            "_get_role_level",
            "_has_min_role",
            "_is_admin_role",
            "_is_supervisor_role",
            "_resolve_management_requester",
            "_late_shift_lock_message",
            "_late_shift_locked_days",
            "_late_shift_lock_response",
            "_late_shift_lock_payload",
            "_late_shift_lock_applies",
            "_late_shift_lock_may_apply",
            "_work_schedule_granted_departments",
            "_work_schedule_granted_department_ids",
            "_work_schedule_granted_member_ids",
            route,
            namespace=namespace,
        )
        payload, status = namespace[route]()
        return payload, status, db

    BODIES = {
        "save_work_shift": {
            "operator_id": 50, "shift_date": "2026-10-20", "start_time": "09:20", "end_time": "18:00",
            "previous_start_time": "09:00", "previous_end_time": "18:00",
        },
        "delete_work_shift": {
            "operator_id": 50, "shift_date": "2026-10-20", "start_time": "09:00", "end_time": "18:00",
        },
        "toggle_work_day_off": {"operator_id": 50, "day_off_date": "2026-10-20"},
        "save_shifts_bulk": {
            "operator_id": 50,
            "shifts": [
                {"date": "2026-10-21", "start": "09:00", "end": "18:00"},
                {"date": "2026-10-20", "start": "09:30", "end": "18:00"},
            ],
        },
        "apply_work_schedule_bulk_actions": {
            "actions": [
                {"action": "set_shift", "operator_id": 51, "date": "2026-10-20", "start": "09:00", "end": "18:00"},
                {"action": "delete_shifts", "operator_id": 50, "date": "2026-10-20"},
            ],
        },
        "respond_shift_change_request": {"request_id": 9, "action": "approve"},
        "api_shift_auction_admin_unclaim_shift": {"lot_id": 7},
        "api_shift_auction_admin_claim_for_operator": {"lot_id": 7, "operator_id": 50},
    }

    def _schedule_calls(self, db):
        return [call for call in db.calls if call[0] not in ("request_days", "target_days")]

    def test_plain_supervisor_is_refused_on_every_route(self):
        for route in ROUTES:
            with self.subTest(route):
                payload, status, db = self._call(route, _user(10, "sv"), copy.deepcopy(self.BODIES[route]))
                self.assertEqual(status, 403)
                self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
                self.assertEqual(
                    payload["error"],
                    "У Иванова Анна опоздание на смену 20.10 (17 мин) — "
                    "менять эту смену может только администратор",
                )
                self.assertEqual(self._schedule_calls(db), [])

    def test_department_head_with_sv_role_is_refused_too(self):
        # «Админ и выше» — роль уровня admin; назначение главой её не даёт.
        payload, status, db = self._call(
            "save_work_shift", _user(12, "sv"), dict(self.BODIES["save_work_shift"]), heads={3}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
        self.assertEqual(self._schedule_calls(db), [])

    def test_admin_and_super_admin_may_change_late_shift(self):
        for role in ("admin", "super_admin"):
            for route in ROUTES:
                with self.subTest(role=role, route=route):
                    payload, status, db = self._call(route, _user(15, role), copy.deepcopy(self.BODIES[route]))
                    self.assertEqual(status, 200, payload)
                    self.assertEqual(len(self._schedule_calls(db)), 1)
                    # Админа база даже не спрашивает — проверка ему не нужна.
                    self.assertEqual(db.late_queries, [])

    def test_admin_with_a_grant_is_locked_only_in_the_granted_department(self):
        """Владелец, 30.10.2026: в отделе допуска (476 → Тез КЦ) админ — «как СВ
        этого отдела», и смену с опозданием там не меняет; в своём ООЗ — меняет."""
        late = {(50, LATE_DAY): (17, "Иванова Анна"), (60, LATE_DAY): (11, "Сотрудник ООЗ")}
        for route in ROUTES:
            with self.subTest(route=route, operator="Тез КЦ"):
                payload, status, db = self._call(route, _user(476, "admin"), copy.deepcopy(self.BODIES[route]),
                                                 db=_RouteDB(late=late), heads={2008})
                self.assertEqual(status, 403)
                self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
                self.assertEqual(self._schedule_calls(db), [])
        body = dict(self.BODIES["save_work_shift"], operator_id=60)
        payload, status, db = self._call("save_work_shift", _user(476, "admin"), body,
                                         db=_RouteDB(late=late), heads={2008})
        self.assertEqual(status, 200, payload)
        self.assertEqual(db.late_queries, [])

    def test_bulk_actions_lock_every_date_key_the_database_reads(self):
        """База берёт день из date, shift_date или day_off_date — замок тоже:
        shift_date вместо date проходил мимо (разбор, 30.10.2026)."""
        for key in ("shift_date", "day_off_date"):
            with self.subTest(key=key):
                body = {"actions": [{"action": "set_day_off", "operator_id": 50, key: "2026-10-20"}]}
                payload, status, db = self._call("apply_work_schedule_bulk_actions", _user(10, "sv"), body)
                self.assertEqual(status, 403)
                self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
                self.assertEqual(db.late_queries, [[(50, LATE_DAY)]])
                self.assertEqual(self._schedule_calls(db), [])

    def test_approval_tells_the_database_whom_the_lock_holds(self):
        """Сокращение и доп. часть переписывают и соседний день — его база
        проверяет сама, если замок держит согласующего (late_lock)."""
        cases = ((_user(10, "sv"), (), True), (_user(15, "admin"), (), False),
                 (_user(476, "admin"), {2008}, True))
        for requester, heads, expected in cases:
            with self.subTest(role=requester[3], user_id=requester[0]):
                payload, status, db = self._call("respond_shift_change_request", requester,
                                                 {"request_id": 9, "action": "approve"},
                                                 db=_RouteDB(late={}), heads=heads)
                self.assertEqual(status, 200, payload)
                respond = [call for call in db.calls if call[0] == "respond"]
                self.assertEqual(respond[0][1]["late_lock"], expected)

    def test_refusal_from_inside_the_approval_is_the_usual_403(self):
        class NeighbourLateDB(_RouteDB):
            def respond_shift_change_request(self, **kwargs):
                if kwargs.get("late_lock"):
                    raise LateShiftLockedError("Смену с опозданием меняет администратор",
                                               {(50, date(2026, 10, 19)): (25, "Иванова Анна")})
                return super().respond_shift_change_request(**kwargs)

        payload, status, _db = self._call("respond_shift_change_request", _user(10, "sv"),
                                          {"request_id": 9, "action": "approve"}, db=NeighbourLateDB(late={}))
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
        self.assertEqual(payload["locked"], [{"operator_id": 50, "date": "2026-10-19"}])
        self.assertIn("19.10 (25 мин)", payload["error"])

    def test_supervisor_may_change_day_without_lateness(self):
        clean = _RouteDB(late={})
        for route in ROUTES:
            with self.subTest(route):
                db = _RouteDB(late={})
                payload, status, db = self._call(route, _user(10, "sv"), copy.deepcopy(self.BODIES[route]), db=db)
                self.assertEqual(status, 200, payload)
                self.assertEqual(len(self._schedule_calls(db)), 1)
        self.assertEqual(clean.calls, [])

    def test_lock_checks_exactly_the_touched_days(self):
        _, _, db = self._call("save_shifts_bulk", _user(10, "sv"), copy.deepcopy(self.BODIES["save_shifts_bulk"]))
        self.assertEqual(db.late_queries, [[(50, date(2026, 10, 21)), (50, LATE_DAY)]])
        _, _, db = self._call(
            "apply_work_schedule_bulk_actions", _user(10, "sv"),
            copy.deepcopy(self.BODIES["apply_work_schedule_bulk_actions"]),
        )
        self.assertEqual(db.late_queries, [[(51, LATE_DAY), (50, LATE_DAY)]])

    def test_lateness_on_other_operator_does_not_block(self):
        db = _RouteDB(late={(77, LATE_DAY): (5, "Другой")})
        payload, status, db = self._call(
            "save_work_shift", _user(10, "sv"), dict(self.BODIES["save_work_shift"]), db=db
        )
        self.assertEqual(status, 200, payload)

    def test_reject_and_cancel_of_change_request_are_not_locked(self):
        # Отказ и отзыв график не трогают — им замок не нужен.
        for action in ("reject", "cancel"):
            with self.subTest(action):
                payload, status, db = self._call(
                    "respond_shift_change_request", _user(10, "sv"), {"request_id": 9, "action": action}
                )
                self.assertEqual(status, 200, payload)
                self.assertNotIn(("request_days", 9), db.calls)

    def test_without_name_message_stays_neutral(self):
        db = _RouteDB(late={(50, LATE_DAY): (3, "")})
        payload, status, _ = self._call(
            "delete_work_shift", _user(10, "sv"), dict(self.BODIES["delete_work_shift"]), db=db
        )
        self.assertEqual(status, 403)
        self.assertTrue(payload["error"].startswith("У сотрудника опоздание на смену 20.10 (3 мин)"))


class LateLockMessagesAndQueueTests(unittest.TestCase):
    def _namespace(self, db):
        from datetime import datetime
        namespace = {
            "ROLE_HIERARCHY": _bot_constant("ROLE_HIERARCHY"),
            "LATE_SHIFT_LOCKED_CODE": _bot_constant("LATE_SHIFT_LOCKED_CODE"),
            "WORK_SCHEDULE_SUPERVISOR_GRANTS": _bot_constant("WORK_SCHEDULE_SUPERVISOR_GRANTS"),
            "g": SimpleNamespace(),
            "_headed_department_ids": lambda requester_id: frozenset({2008}) if requester_id == 476 else frozenset(),
            "jsonify": lambda payload: payload,
            "db": db,
            "datetime": datetime,
        }
        return _bot_functions(
            "_normalize_user_role", "_get_role_level", "_has_min_role", "_is_admin_role",
            "_late_shift_lock_message", "_late_shift_locked_days", "_late_shift_lock_response",
            "_late_shift_lock_payload", "_late_shift_lock_applies",
            "_late_shift_lock_may_apply", "_work_schedule_granted_departments",
            "_work_schedule_granted_department_ids", "_work_schedule_granted_member_ids",
            "_mark_late_locked_requests", namespace=namespace,
        )

    def test_bulk_refusal_names_every_late_day(self):
        db = _RouteDB(late={(50, LATE_DAY): (17, "Иванова Анна"), (51, date(2026, 10, 21)): (5, "Петров Олег")})
        namespace = self._namespace(db)
        payload, status = namespace["_late_shift_lock_response"](
            _user(10, "sv"), [(50, LATE_DAY), (51, date(2026, 10, 21)), (52, LATE_DAY)])
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "Опоздание на смены: Иванова Анна 20.10 (17 мин), Петров Олег 21.10 (5 мин) — "
                                           "менять эти смены может только администратор")
        self.assertEqual(payload["locked"], [{"operator_id": 50, "date": "2026-10-20"},
                                             {"operator_id": 51, "date": "2026-10-21"}])

    def test_review_queue_marks_requests_on_late_days(self):
        db = _RouteDB()
        namespace = self._namespace(db)
        requests = [
            {"id": 1, "status": "pending", "kind": "shorten", "shiftDate": "2026-10-20", "operator": {"id": 50}},
            {"id": 2, "status": "pending", "kind": "change", "shiftDate": "2026-10-19",
             "payload": {"newDate": "2026-10-20"}, "operator": {"id": 50}},
            {"id": 3, "status": "pending", "kind": "extra", "shiftDate": "2026-10-21", "operator": {"id": 50}},
            {"id": 4, "status": "approved", "kind": "shorten", "shiftDate": "2026-10-20", "operator": {"id": 50}},
        ]
        namespace["_mark_late_locked_requests"](_user(10, "sv"), requests)
        self.assertEqual([r.get("lateLocked") for r in requests], [
            {"date": "2026-10-20", "minutes": 17}, {"date": "2026-10-20", "minutes": 17}, None, None])
        # Админу замка нет — и база не спрашивается.
        clean = [dict(item) for item in requests]
        for item in clean:
            item.pop("lateLocked", None)
        admin_db = _RouteDB()
        self._namespace(admin_db)["_mark_late_locked_requests"](_user(15, "admin"), clean)
        self.assertEqual([r.get("lateLocked") for r in clean], [None] * 4)
        self.assertEqual(admin_db.late_queries, [])

    def test_review_queue_of_an_admin_with_a_grant_locks_only_the_granted_department(self):
        db = _RouteDB(late={(50, LATE_DAY): (17, "Иванова Анна"), (60, LATE_DAY): (11, "Сотрудник ООЗ")})
        requests = [
            {"id": 1, "status": "pending", "kind": "shorten", "shiftDate": "2026-10-20", "operator": {"id": 50}},
            {"id": 2, "status": "pending", "kind": "shorten", "shiftDate": "2026-10-20", "operator": {"id": 60}},
        ]
        self._namespace(db)["_mark_late_locked_requests"](_user(476, "admin"), requests)
        self.assertEqual([r.get("lateLocked") for r in requests], [{"date": "2026-10-20", "minutes": 17}, None])
        self.assertEqual(db.late_queries, [[(50, LATE_DAY)]])

    def test_auction_publish_is_locked_for_supervisor(self):
        """Публикация аукциона стирает и собирает заново прошедшие дни идущего
        периода — смена с опозданием в них тоже переписалась бы."""
        class PublishDB(_RouteDB):
            def get_shift_auction_publish_days(self, direction_mode):
                self.calls.append(("publish_days", direction_mode))
                return [(50, LATE_DAY), (51, LATE_DAY)]

            def publish_shift_auction_test_to_work_schedules(self, **kwargs):
                self.calls.append(("publish", kwargs))
                return {"shifts_saved": 3}

        def call(requester):
            db = PublishDB()
            namespace = self._namespace(db)
            namespace.update({
                "request": _Request({}),
                "_build_cors_preflight_response": lambda: ({}, 204),
                "_get_authenticated_requester": lambda: (requester[0], requester, None),
                "_is_shift_auction_manager": lambda requester_id, role: True,
                "_resolve_shift_auction_direction": lambda *args: "line",
                "_requested_shift_auction_direction": lambda payload: None,
                "_shift_auction_test_error_response": lambda error: ({"error": str(error)}, 400),
                "logging": logging,
            })
            _bot_functions("api_shift_auction_test_publish", namespace=namespace)
            payload, status = namespace["api_shift_auction_test_publish"]()
            return payload, status, db

        payload, status, db = call(_user(10, "sv"))
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "LATE_SHIFT_LOCKED")
        self.assertNotIn("publish", [c[0] for c in db.calls])
        payload, status, db = call(_user(15, "admin"))
        self.assertEqual(status, 200, payload)
        # Админу проверка не нужна — дни периода даже не собираются.
        self.assertEqual([c[0] for c in db.calls], ["publish"])
        # Админ с отделом допуска там «как СВ»: участник Тез КЦ опоздал — отказ.
        payload, status, db = call(_user(476, "admin"))
        self.assertEqual(status, 403)
        self.assertEqual(payload["locked"], [{"operator_id": 50, "date": "2026-10-20"}])
        self.assertNotIn("publish", [c[0] for c in db.calls])

    def test_frontend_shows_lock_instead_of_approve(self):
        # lateLocked сервер ставит только тому, кого замок держит, — фронт ему верит.
        self.assertIn("{item.lateLocked ? (", APP_SOURCE)
        self.assertNotIn("item.lateLocked && !plannerCanEditLateShifts", APP_SOURCE)
        self.assertIn("{modalTabShifts && modalBulkLateLocked.length > 0 && (", APP_SOURCE)
        self.assertIn("_mark_late_locked_requests(user_data, payload.get('requests'))", BOT_SOURCE)

    def test_locked_day_has_no_editing_badge(self):
        # Под замком смену не правят — метки «Редактируется» на ней нет ни в окне
        # дня на компьютере, ни в строке смены на телефоне.
        self.assertIn("const isEditing = !modalShiftEditLocked && modalState.editIndex === idx;", APP_SOURCE)
        self.assertIn("!modalShiftEditLocked && modalState.editIndex === idx\n"
                      "                                                            ? { label: 'Редактируется'", APP_SOURCE)


class ExcelImportLateLockTests(unittest.TestCase):
    """Импорт месячного файла не валится целиком: дни с опозданием пропускаются."""

    def test_import_filters_late_days_and_reports_them(self):
        source = _function_source("import_work_schedules_excel")
        self.assertIn("_late_shift_locked_days(", source)
        # Фильтр стоит ДО записи в базу и до ранних ответов «нечего импортировать».
        self.assertLess(source.index("_late_shift_locked_days("), source.index("if not parsed_entries and not invalid_cells"))
        self.assertLess(source.index("_late_shift_locked_days("), source.index("db.import_work_schedule_excel_entries("))
        # Все три ответа несут пропущенные дни.
        self.assertEqual(source.count('"late_locked_total": len(late_locked_entries)'), 3)


class LateShiftDaysDatabaseTests(unittest.TestCase):
    class _Cursor:
        def __init__(self, rows):
            self.rows = rows
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            return list(self.rows)

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class _CursorCM:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            return self.cursor

        def __exit__(self, *exc):
            return False

    def _db(self, rows):
        from datetime import datetime  # noqa: F401 — нужен _normalize_schedule_date
        namespace = {"date": date, "datetime": __import__("datetime").datetime}
        _database_methods(
            "_normalize_schedule_date",
            "get_late_shift_days",
            "_late_shift_days_tx",
            "_refuse_swap_on_late_day_tx",
            "_refuse_swap_on_late_pairs_tx",
            "get_shift_change_request_days",
            "_normalize_shift_change_kind",
            "_shift_change_target_date",
            namespace=namespace,
        )
        cursor = self._Cursor(rows)

        class FakeDB:
            SHIFT_CHANGE_REQUEST_KINDS = ("extra", "shorten", "change", "day_off")
            _normalize_schedule_date = namespace["_normalize_schedule_date"]
            get_late_shift_days = namespace["get_late_shift_days"]
            _late_shift_days_tx = namespace["_late_shift_days_tx"]
            _refuse_swap_on_late_day_tx = namespace["_refuse_swap_on_late_day_tx"]
            _refuse_swap_on_late_pairs_tx = namespace["_refuse_swap_on_late_pairs_tx"]
            get_shift_change_request_days = namespace["get_shift_change_request_days"]
            _normalize_shift_change_kind = namespace["_normalize_shift_change_kind"]
            _shift_change_target_date = namespace["_shift_change_target_date"]

            def _get_cursor(self_inner):
                return LateShiftDaysDatabaseTests._CursorCM(cursor)

        return FakeDB(), cursor

    def test_returns_only_late_pairs_with_minutes_and_name(self):
        db, cursor = self._db([(50, LATE_DAY, 17, "Иванова Анна ")])
        result = db.get_late_shift_days([(50, "2026-10-20"), ("50", LATE_DAY), (None, "2026-10-21"), (51, "")])
        self.assertEqual(result, {(50, LATE_DAY): (17, "Иванова Анна")})
        sql, params = cursor.executed[0]
        self.assertIn("COALESCE(dh.late_minutes, 0) > 0", sql)
        # Пары без дублей и мусора: оператор без id и день без даты отброшены.
        self.assertEqual(params, ([50], [LATE_DAY]))

    def test_empty_input_does_not_query(self):
        db, cursor = self._db([])
        self.assertEqual(db.get_late_shift_days([]), {})
        self.assertEqual(cursor.executed, [])

    def test_change_request_days_include_move_target(self):
        db, _ = self._db([(50, "change", LATE_DAY, {"newDate": "2026-10-22"})])
        self.assertEqual(db.get_shift_change_request_days(9), [(50, LATE_DAY), (50, date(2026, 10, 22))])

    def test_swap_on_late_day_is_refused_without_a_colleagues_lateness(self):
        """Отказ читает оператор, а опоздать мог коллега по обмену: в тексте
        только день — ни ФИО, ни минут чужого опоздания (разбор, 30.10.2026)."""
        db, cursor = self._db([(51, LATE_DAY, 9, "Петров Олег")])
        with self.assertRaises(ValueError) as caught:
            db._refuse_swap_on_late_day_tx(cursor, [50, 51], "2026-10-20")
        self.assertEqual(str(caught.exception), "Обмен сменой за 20.10 недоступен — эту смену меняет администратор")
        self.assertNotIn("Петров", str(caught.exception))
        self.assertEqual(cursor.executed[0][1], ([50, 51], [LATE_DAY, LATE_DAY]))

    def test_swap_without_lateness_passes(self):
        db, cursor = self._db([])
        self.assertIsNone(db._refuse_swap_on_late_day_tx(cursor, [50, 51], LATE_DAY))

    def test_swap_create_and_accept_check_lateness_before_touching_shifts(self):
        module = source_cache.parse(DATABASE_SOURCE)
        database_class = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Database")
        wanted = ("create_shift_swap_request", "respond_shift_swap_request")
        methods = {n.name: ast.get_source_segment(DATABASE_SOURCE, n)
                   for n in database_class.body if isinstance(n, ast.FunctionDef) and n.name in wanted}
        create = methods["create_shift_swap_request"]
        self.assertIn("self._refuse_swap_on_late_day_tx(cursor, [requester_operator_id, target_operator_id]", create)
        self.assertLess(create.index("_refuse_swap_on_late_day_tx"), create.index("INSERT INTO work_shift_swap_requests"))
        respond = methods["respond_shift_swap_request"]
        # При принятии — по каждому дню, который обмен перепишет, и до очистки дней.
        self.assertIn("self._refuse_swap_on_late_pairs_tx(", respond)
        self.assertLess(respond.index("_refuse_swap_on_late_pairs_tx"), respond.index("self._clear_day_schedule_tx("))
        self.assertGreater(respond.index("_refuse_swap_on_late_pairs_tx"), respond.index("target_affected_dates.append"))

    def test_change_request_days_for_other_kinds_are_the_request_day(self):
        db, _ = self._db([(50, "shorten", LATE_DAY, {"newDate": "2026-10-22"})])
        self.assertEqual(db.get_shift_change_request_days(9), [(50, LATE_DAY)])


class FrontendLateLockTests(unittest.TestCase):
    def test_planner_shows_lock_and_server_message(self):
        self.assertIn("const plannerCanEditLateShifts = ['admin', 'super_admin'].includes(normalizeRole(user?.role));", APP_SOURCE)
        # В отделе допуска админ — «как СВ» (владелец, 30.10.2026): замок по оператору.
        self.assertIn("plannerCanEditLateShifts && !plannerGrantedDepartmentIds.has(String(op?.department_id ?? ''))",
                      APP_SOURCE)
        self.assertIn("const modalLateLockMinutes = (!plannerCanEditLateShiftOf(plannerModalOperator) && "
                      "!modalState?.multipleDates)", APP_SOURCE)
        self.assertIn("if (!op || plannerCanEditLateShiftOf(op)) return null;", APP_SOURCE)
        self.assertIn("const modalShiftEditLocked = plannerReadOnly || modalLateLockMinutes > 0;", APP_SOURCE)
        self.assertIn("payload?.code === 'LATE_SHIFT_LOCKED'", APP_SOURCE)
        # Обе версии окна дня (телефон и компьютер) прячут правку под замком.
        self.assertGreaterEqual(APP_SOURCE.count("modalShiftEditLocked"), 8)
        self.assertEqual(APP_SOURCE.count("{renderLateShiftLockNote()}"), 2)

    def test_bulk_refusal_shows_the_server_text_and_buttons_wait_for_late_days(self):
        """Отказ массовой правки называет запертые дни — их видно в тосте, а не
        «Ошибка массового…»; при плашке о днях с опозданием кнопки выключены."""
        self.assertIn("if (payload?.code === 'LATE_SHIFT_LOCKED') {", APP_SOURCE)
        self.assertIn("throw Object.assign(new Error(payload.error), { userMessage:", APP_SOURCE)
        for text in ("Ошибка массового сохранения смен", "Ошибка массового назначения выходного",
                     "Ошибка массовой очистки дней"):
            self.assertIn("emitAppToast(error?.userMessage || '%s', 'error');" % text, APP_SOURCE)
        self.assertEqual(APP_SOURCE.count("disabled={bulkActionState.loading || modalBulkLateLocked.length > 0}"), 3)

    def test_refusal_is_shown_inside_the_window(self):
        """Тосты в основном бандле не видны (window.showToast ставит только
        страница оценки звонков): отказ — красной строкой в самом окне, и данные
        перечитываются, чтобы замок встал сам (второй разбор, 30.10.2026)."""
        self.assertIn("const [plannerLateLockRefusal, setPlannerLateLockRefusal] = useState('');", APP_SOURCE)
        self.assertEqual(APP_SOURCE.count("notePlannerLateLockRefusal(error);"), 6)
        self.assertEqual(APP_SOURCE.count("{renderLateLockRefusal()}"), 2)
        # Не дублирует плашку: при замке дня или плашке массовой правки строки нет.
        self.assertIn("&& modalLateLockMinutes === 0 && modalBulkLateLocked.length === 0;", APP_SOURCE)
        self.assertIn("if (lateMessage) fetchPlannerSchedulesByMonths(plannerPreloadMonthKeys, { force: true })",
                      APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
