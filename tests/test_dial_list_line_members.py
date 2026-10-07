# -*- coding: utf-8 -*-
"""Раздел «Удаленный КЦ»: сотрудник ДРУГОГО отдела на линии (запрос владельца 07.10.2026).

Что защищаем:

  * сажать на линию и снимать с неё сотрудника другого отдела вправе только глава
    СЗоВ и суперадмины; остальным руководителям раздела сервер отказывает и список
    чужих сотрудников не отдаёт;
  * собственные SIP-настройки такого сотрудника не трогаются: в users.sip_number у
    него телефония его отдела (у СЗоВ это логин Oktell, по нему работают табло и
    «Ограничитель») — привязка живёт в dial_list_line_members;
  * пока привязка действует, раздел и телефон считают его сотрудником отдела линии:
    тот же режим обзвона, та же база, те же показатели;
  * программу iCORE Phone скачивают глава СЗоВ и все, кого посадили на линию.

Без базы: поддельные курсоры и разбор исходников, как в tests/test_dial_list.py.
Люди и логины в тестах выдуманы.
"""
import ast
import contextlib
import inspect
import os
import re
import sys
import unittest
from datetime import date

import psycopg2.errors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import source_cache  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(ROOT, 'bot_schedule2.py')
APP_PATH = os.path.join(ROOT, 'src', 'App.jsx')

LINE_DEPARTMENT = 1954   # отдел линии — удалённый КЦ
HOME_DEPARTMENT = 1      # отдел человека — СЗоВ


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def _bot_function_source(name):
    """Текст функции монолита по номерам строк узла (get_source_segment там медленный)."""
    node = source_cache.function_node(BOT_PATH, name)
    lines = source_cache.read(BOT_PATH).splitlines()
    return '\n'.join(lines[node.lineno - 1:node.end_lineno])


def _bot_functions(names, namespace):
    """Исполняет функции bot_schedule2.py без импорта модуля (он поднимает пул к БД)."""
    for name in names:
        node = source_cache.function_copy(BOT_PATH, name)
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, f'<bot:{name}>', 'exec'), namespace)
    return namespace


def _bot_constant(name):
    for node in source_cache.tree(BOT_PATH).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f'константа {name} пропала из bot_schedule2.py')


class _Cursor:
    """Курсор, отвечающий по первой подошедшей подстроке SQL; всё исполненное записывает."""

    def __init__(self, rules=(), fail_on=None):
        self.rules = list(rules)
        self.executed = []
        self.fail_on = fail_on
        self._current = {}

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.fail_on and self.fail_on[0] in sql:
            raise self.fail_on[1]
        self._current = next((answer for sub, answer in self.rules if sub in sql), {})

    def fetchone(self):
        return self._current.get('one')

    def fetchall(self):
        return list(self._current.get('all', []))

    def sql_with(self, fragment):
        return [(sql, params) for sql, params in self.executed if fragment in sql]


class _Db:
    """Кусок Database, которого хватает сервису: карточка, запись SIP-настроек, курсор."""

    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

    def __init__(self, operators=None, cursor=None, save_error=None):
        self.operators = operators or {}
        self.cursor = cursor or _Cursor()
        self.saved = []
        self.save_error = save_error

    def get_sip_operator(self, user_id):
        card = self.operators.get(int(user_id))
        return dict(card) if card else None

    def save_user_sip_settings(self, user_id, payload, changed_by=None):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append((user_id, dict(payload), changed_by))
        return {}

    @contextlib.contextmanager
    def _get_cursor(self):
        yield self.cursor


# Сотрудник СЗоВ: в sip_number у него логин Oktell, провайдер отдела — не Binotel.
GUEST = {"id": 77, "name": "Гостев Глеб", "status": "working", "department_id": HOME_DEPARTMENT,
         "department_provider": "asterisk", "sip_number": "1024", "department_name": "СЗоВ"}
# Свой сотрудник удалённого КЦ.
OWN = {"id": 10, "name": "Своев Сава", "status": "working", "department_id": LINE_DEPARTMENT,
       "department_provider": "binotel", "sip_number": "", "department_name": "Удаленный КЦ"}
FIRED = dict(GUEST, id=78, name="Бывшев Борис", status="fired")
# Сотрудник Тез КЦ — отдел тоже на Binotel, то есть линии есть и у его собственного отдела.
TEZ = 560
TEZ_OP = {"id": 703, "name": "Тезов Тимур", "status": "working", "department_id": TEZ,
          "department_provider": "binotel", "sip_number": "913", "department_name": "Тез КЦ"}

EMPLOYEES = [
    {"employeeID": "1", "name": "Линия", "email": "a@x",
     "endpointData": {"internalNumber": "905", "login": "lg905", "password": "pw905",
                      "status": {"preparedStatus": "online"}}},
    {"employeeID": 0, "name": "", "email": "",
     "endpointData": {"internalNumber": "906", "login": "lg906", "password": "pw906",
                      "status": {"preparedStatus": "offline"}}},
]


def _service(db=None, members=None, holders=None, section=(LINE_DEPARTMENT,), users=None):
    """Сервис с подменёнными Binotel, замком линии и хранением привязок.

    members — user_id → действующая привязка к линии; holders — номер линии → кто её
    держит ({'id', 'name'}); section — отделы, подключённые к разделу. В log — что
    сервис сделал и в каком порядке (events)."""
    db = db or _Db({77: GUEST, 10: OWN, 78: FIRED, 703: TEZ_OP})
    svc = dial_service.DialListService(db)
    members = members if members is not None else {}
    holders = holders if holders is not None else {}
    log = {"seated": [], "unseated": [], "released": [], "dead": [], "locks": [], "events": []}
    svc._binotel_employees = lambda dep: [dict(e) for e in EMPLOYEES]
    svc.department_sip_server = lambda dep: "sip53.binotel.com"
    svc._department_provider = lambda dep: "binotel"
    svc.line_member = lambda uid: members.get(int(uid))
    svc.list_departments = lambda ids=None: [{"department_id": d} for d in (ids or []) if d in section]
    svc.department_users = lambda dep: list(users or [])
    lock_cursor = object()

    @contextlib.contextmanager
    def line_lock(dep, number):
        log["locks"].append((dep, number))
        log["events"].append("lock")
        yield lock_cursor
        log["events"].append("unlock")
    svc._line_lock = line_lock

    def line_holder(cur, dep, number, exclude_user_id=None):
        assert cur is lock_cursor, 'занятость линии проверяется не под её замком'
        holder = holders.get(number)
        return holder if holder and holder["id"] != exclude_user_id else None
    svc._line_holder = line_holder

    def seat(cur, department_id, user_id, internal_number, login, password, changed_by=None):
        assert cur is lock_cursor, 'привязка пишется не в транзакции замка'
        log["seated"].append((department_id, user_id, internal_number, login, password, changed_by))
        log["events"].append("seat")
    svc._seat_member = seat

    def release(cur, uid, changed_by=None):
        assert cur is lock_cursor
        log["released"].append((uid, changed_by))
        log["events"].append("release")
        return ""
    svc._release_member = release

    def dead(cur, dep, number, changed_by=None):
        assert cur is lock_cursor
        log["dead"].append((dep, number, changed_by))
        log["events"].append("dead")
    svc._release_dead_holders = dead
    svc._unseat_member = lambda uid, changed_by=None: log["unseated"].append((uid, changed_by)) or ""
    original_save = db.save_user_sip_settings

    def save(uid, payload, changed_by=None):
        log["events"].append("save")
        return original_save(uid, payload, changed_by=changed_by)
    db.save_user_sip_settings = save
    return svc, db, log


class SeatAnyoneRightTests(unittest.TestCase):
    """«Доступ у главы отдела СЗоВ и у суперадминов» — буквально, роль «админ» права не даёт."""

    def test_super_admin_and_szov_head_only(self):
        svc = dial_service.DialListService(_Db())
        asked = []
        svc._heads_department_with_code = lambda ids, codes: asked.append((list(ids), codes)) or (7 in ids)
        self.assertTrue(svc.can_seat_anyone(True, []))
        self.assertEqual(asked, [], 'суперадмину отделы читать незачем')
        self.assertTrue(svc.can_seat_anyone(False, [7]))            # глава СЗоВ
        self.assertIs(asked[-1][1], dial_service.DIAL_LIST_SEAT_ANYONE_HEAD_CODES)
        self.assertFalse(svc.can_seat_anyone(False, [5]))           # глава другого отдела
        self.assertFalse(svc.can_seat_anyone(False, []))            # админ, не возглавляющий отдел
        self.assertFalse(svc.can_seat_anyone(False, None))

    def test_head_codes_are_szov_and_separate_from_overseers(self):
        self.assertEqual(dial_service.DIAL_LIST_SEAT_ANYONE_HEAD_CODES, frozenset({'szov'}))
        # Свой список: новый отдел-куратор раздела не должен получить это право молча.
        src = inspect.getsource(dial_service.DialListService.can_seat_anyone)
        self.assertIn('DIAL_LIST_SEAT_ANYONE_HEAD_CODES', src)
        self.assertNotIn('DIAL_LIST_OVERSEER_DEPARTMENT_CODES', src)

    def test_department_codes_are_read_for_headed_departments_only(self):
        cursor = _Cursor([('FROM departments WHERE id = ANY', {'all': [('szov',), ('',)]})])
        svc = dial_service.DialListService(_Db(cursor=cursor))
        self.assertTrue(svc._heads_department_with_code([1, 9], frozenset({'szov'})))
        self.assertEqual(cursor.executed[0][1], ([1, 9],))
        # Код в карточке отдела бывает записан как угодно — сравниваем без регистра.
        self.assertIn("SELECT LOWER(COALESCE(code, '')) FROM departments WHERE id = ANY(%s)", cursor.executed[0][0])
        self.assertFalse(svc._heads_department_with_code([1, 9], frozenset({'op'})))
        before = len(cursor.executed)
        self.assertFalse(svc._heads_department_with_code([], frozenset({'szov'})))
        self.assertEqual(len(cursor.executed), before, 'без отделов в базу не ходим')


class AssignLineTests(unittest.TestCase):
    def test_other_department_employee_needs_the_right(self):
        svc, db, log = _service()
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.assign_line(LINE_DEPARTMENT, 77, "906", changed_by=5)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(str(ctx.exception), dial_service.SEAT_ANYONE_DENIED)
        self.assertEqual((log["seated"], db.saved, log["locks"]), ([], [], []))

    def test_other_department_employee_is_seated_without_touching_his_sip_settings(self):
        """Главный инвариант: users.sip_number и user_sip_settings чужого сотрудника
        не переписываются — учётка линии уходит только в привязку."""
        svc, db, log = _service()
        result = svc.assign_line(LINE_DEPARTMENT, 77, "906", changed_by=5, seat_anyone=True)
        self.assertEqual(log["seated"], [(LINE_DEPARTMENT, 77, "906", "lg906", "pw906", 5)])
        self.assertEqual(db.saved, [], 'SIP-настройки чужого сотрудника тронуты')
        self.assertEqual(log["events"], ["lock", "seat", "unlock"])
        self.assertEqual(result, {"user_id": 77, "internal_number": "906",
                                  "sip_server": "sip53.binotel.com", "guest": True})
        self.assertNotIn("pw906", str(result))

    def test_own_employee_goes_the_old_way_and_bindings_are_closed_after_the_write(self):
        svc, db, log = _service()
        result = svc.assign_line(LINE_DEPARTMENT, 10, "906", changed_by=5)
        self.assertEqual(db.saved, [(10, {"sip_number": "906", "sip_login": "lg906", "sip_password": "pw906"}, 5)])
        self.assertEqual(log["seated"], [])
        # Его привязка «как чужого» (остаток после перевода) сильнее SIP-настроек, а
        # привязка уволенного к этой линии — мёртвый груз: обе закрываются ПОСЛЕ записи.
        self.assertEqual(log["released"], [(10, 5)])
        self.assertEqual(log["dead"], [(LINE_DEPARTMENT, "906", 5)])
        self.assertEqual(log["events"], ["lock", "save", "release", "dead", "unlock"])
        self.assertIs(result["guest"], False)

    def test_refused_write_keeps_the_previous_line(self):
        """Запись SIP-настроек отклонена (логин занят) — снимать человека с прежней
        линии было нельзя: разбор 07.10.2026 нашёл, что снятие шло ДО записи."""
        db = _Db({10: OWN}, save_error=ValueError("SIP-логин lg906 уже занят: Занятов Захар"))
        svc, db, log = _service(db=db, members={10: {"department_id": LINE_DEPARTMENT, "internal_number": "905"}})
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.assign_line(LINE_DEPARTMENT, 10, "906", changed_by=5)
        self.assertEqual((ctx.exception.status, str(ctx.exception)), (400, "SIP-логин lg906 уже занят: Занятов Захар"))
        self.assertEqual((log["released"], log["dead"], log["unseated"]), ([], [], []))

    def test_taking_someone_off_another_department_line_needs_the_right(self):
        """Обход права, найденный разбором: человек, посаженный главой СЗоВ на линию
        удалённого КЦ, — «свой» для собственного отдела (Тез КЦ). Назначение ему линии
        Теза сняло бы его с линии удалённого КЦ, а снимать чужих с линии вправе только
        глава СЗоВ и суперадмины."""
        seated = {703: {"department_id": LINE_DEPARTMENT, "internal_number": "903"}}
        svc, db, log = _service(members=seated, section=(LINE_DEPARTMENT, TEZ))
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.assign_line(TEZ, 703, "906", changed_by=169)
        self.assertEqual((ctx.exception.status, str(ctx.exception)), (403, dial_service.SEAT_ANYONE_DENIED))
        self.assertEqual((db.saved, log["released"], log["locks"]), ([], [], []))
        # С правом — можно: человек уходит на линию своего отдела, привязка закрывается.
        svc, db, log = _service(members=seated, section=(LINE_DEPARTMENT, TEZ))
        svc.assign_line(TEZ, 703, "906", changed_by=1, seat_anyone=True)
        self.assertEqual(db.saved[0][0], 703)
        self.assertEqual(log["released"], [(703, 1)])
        # Привязка к линии ЭТОГО же отдела (остаток после перевода) права не требует.
        svc, db, log = _service(members={10: {"department_id": LINE_DEPARTMENT, "internal_number": "905"}})
        svc.assign_line(LINE_DEPARTMENT, 10, "906", changed_by=169)
        self.assertEqual(log["released"], [(10, 169)])

    def test_stranger_is_seated_only_on_lines_of_the_section(self):
        """Чужого — только на линию отдела раздела: привязка к линии отдела вне раздела
        там не видна и снять её нечем (находка разбора)."""
        svc, db, log = _service(section=())
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.assign_line(TEZ, 77, "906", changed_by=1, seat_anyone=True)
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("не подключён к разделу", str(ctx.exception))
        self.assertEqual((log["seated"], log["locks"]), ([], []))
        # Своего сотрудника отдела это не касается — как и раньше.
        svc, db, log = _service(section=())
        svc.assign_line(LINE_DEPARTMENT, 10, "906", changed_by=1)
        self.assertEqual(len(db.saved), 1)

    def test_fired_employee_cannot_be_seated(self):
        svc, db, log = _service()
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.assign_line(LINE_DEPARTMENT, 78, "906", changed_by=5, seat_anyone=True)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual((log["seated"], db.saved), ([], []))

    def test_line_department_must_be_on_binotel(self):
        """Провайдер проверяется у отдела ЛИНИИ: у чужого сотрудника в карточке — провайдер его отдела."""
        svc, db, log = _service()
        asked = []
        svc._department_provider = lambda dep: asked.append(dep) or "asterisk"
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.assign_line(LINE_DEPARTMENT, 77, "906", changed_by=5, seat_anyone=True)
        self.assertEqual((ctx.exception.status, asked), (409, [LINE_DEPARTMENT]))
        svc._department_provider = lambda dep: "binotel"
        svc.assign_line(LINE_DEPARTMENT, 77, "906", changed_by=5, seat_anyone=True)   # у его отдела asterisk — не мешает
        self.assertEqual(len(log["seated"]), 1)

    def test_taken_line_is_refused_whoever_holds_it(self):
        holders = {"906": {"id": 11, "name": "Занятов Захар"}}
        for user_id, anyone in ((77, True), (10, False)):
            svc, db, log = _service(holders=holders)
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.assign_line(LINE_DEPARTMENT, user_id, "906", changed_by=5, seat_anyone=anyone)
            self.assertEqual(ctx.exception.status, 409)
            self.assertIn("Линия 906 уже у сотрудника Занятов Захар", str(ctx.exception))
            self.assertEqual((log["seated"], db.saved, log["released"]), ([], [], []))
            # Проверка шла под замком этой линии.
            self.assertEqual(log["locks"], [(LINE_DEPARTMENT, "906")])
        # Тот же человек на той же линии — не конфликт (повторное назначение обновляет учётку).
        svc, db, log = _service(holders={"906": {"id": 77, "name": "Гостев Глеб"}})
        svc.assign_line(LINE_DEPARTMENT, 77, "906", changed_by=5, seat_anyone=True)
        self.assertEqual(len(log["seated"]), 1)

    def test_a_race_lost_on_the_unique_index_is_a_conflict_not_a_crash(self):
        class _UserTaken(psycopg2.errors.UniqueViolation):
            diag = type("Diag", (), {"constraint_name": "uq_dial_list_line_members_user"})()

        for error, text in ((psycopg2.errors.UniqueViolation(), "Линию 906 только что заняли"),
                            (_UserTaken(), "посадили на другую линию")):
            svc, db, log = _service()

            def seat(*_args, **_kwargs):
                raise error
            svc._seat_member = seat
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.assign_line(LINE_DEPARTMENT, 77, "906", changed_by=5, seat_anyone=True)
            self.assertEqual(ctx.exception.status, 409)
            self.assertIn(text, str(ctx.exception))
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_line_members_user", ddl)

    def test_unknown_line_and_unknown_employee(self):
        svc, db, log = _service()
        for user_id, number, status in ((77, "999", 404), (4242, "906", 404), (77, "", 400)):
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.assign_line(LINE_DEPARTMENT, user_id, number, changed_by=5, seat_anyone=True)
            self.assertEqual(ctx.exception.status, status, (user_id, number))
        self.assertEqual(log["seated"], [])


class ReleaseLineTests(unittest.TestCase):
    MEMBER = {"user_id": 77, "department_id": LINE_DEPARTMENT, "internal_number": "906"}

    def test_other_department_employee_is_released_by_the_same_right(self):
        svc, db, log = _service(members={77: dict(self.MEMBER)})
        with self.assertRaises(dial_service.DialListError) as ctx:
            svc.release_line(LINE_DEPARTMENT, 77, changed_by=5)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(log["unseated"], [])
        result = svc.release_line(LINE_DEPARTMENT, 77, changed_by=5, seat_anyone=True)
        self.assertEqual(log["unseated"], [(77, 5)])
        self.assertEqual(result, {"user_id": 77, "internal_number": ""})
        # Его собственные SIP-настройки (логин Oktell в sip_number) остались как были.
        self.assertEqual(db.saved, [])

    def test_own_employee_is_released_the_old_way(self):
        svc, db, log = _service()
        svc.release_line(LINE_DEPARTMENT, 10, changed_by=5)
        self.assertEqual(db.saved, [(10, {"sip_number": "", "sip_login": "", "sip_password": ""}, 5)])
        self.assertEqual(log["unseated"], [])

    def test_former_guest_now_in_the_department_needs_no_special_right(self):
        """Перевели в отдел линии, а привязка осталась — это уже свой сотрудник."""
        svc, db, log = _service(members={10: dict(self.MEMBER, user_id=10)})
        svc.release_line(LINE_DEPARTMENT, 10, changed_by=5)
        self.assertEqual(log["unseated"], [(10, 5)])
        self.assertEqual(db.saved, [])

    def test_stranger_without_a_binding_is_not_found(self):
        svc, db, log = _service()
        for user_id in (77, 4242):
            with self.assertRaises(dial_service.DialListError) as ctx:
                svc.release_line(LINE_DEPARTMENT, user_id, changed_by=5, seat_anyone=True)
            self.assertEqual(ctx.exception.status, 404)
        # Привязка к линии ДРУГОГО отдела этому отделу не принадлежит.
        svc, db, log = _service(members={77: dict(self.MEMBER, department_id=555)})
        with self.assertRaises(dial_service.DialListError):
            svc.release_line(LINE_DEPARTMENT, 77, changed_by=5, seat_anyone=True)
        self.assertEqual((log["unseated"], db.saved), ([], []))


class BindingStorageTests(unittest.TestCase):
    def test_seat_releases_previous_and_dead_bindings_then_inserts(self):
        cursor = _Cursor()
        svc = dial_service.DialListService(_Db())
        svc._seat_member(cursor, LINE_DEPARTMENT, 77, "906", "lg906", "pw906", changed_by=5)
        sqls = [' '.join(sql.split()) for sql, _ in cursor.executed]
        self.assertEqual(len(sqls), 3)
        # 1) прежняя привязка самого человека: один человек — одна линия.
        self.assertIn("WHERE user_id = %s AND released_at IS NULL", sqls[0])
        self.assertEqual(cursor.executed[0][1], (5, 77))
        # 2) привязка УВОЛЕННОГО к этой линии; действующего сотрудника она не снимает.
        self.assertIn("LOWER(COALESCE(u.status, '')) = ANY(%s)", sqls[1])
        self.assertIn("m.department_id = %s AND m.internal_number = %s", sqls[1])
        self.assertEqual(cursor.executed[1][1], (5, LINE_DEPARTMENT, "906", ['fired', 'dismissal']))
        # 3) новая привязка с учёткой линии.
        self.assertTrue(sqls[2].startswith("INSERT INTO dial_list_line_members"))
        self.assertEqual(cursor.executed[2][1], (77, LINE_DEPARTMENT, "906", "lg906", "pw906", 5))
        for sql in sqls[:2]:
            # Строка остаётся историей, а пароль линии стирается.
            self.assertIn("released_at = CURRENT_TIMESTAMP", sql)
            self.assertIn("sip_password = ''", sql)
            self.assertNotIn("DELETE", sql)

    def test_conflict_messages_name_what_was_taken(self):
        class _Diag:
            def __init__(self, name):
                self.diag = type("Diag", (), {"constraint_name": name})()

        line = dial_service.DialListService._seat_conflict(_Diag("uq_dial_list_line_members_line"), "906")
        person = dial_service.DialListService._seat_conflict(_Diag("uq_dial_list_line_members_user"), "906")
        unknown = dial_service.DialListService._seat_conflict(Exception(), "906")
        self.assertEqual((line.status, str(line)), (409, "Линию 906 только что заняли — обновите список"))
        self.assertEqual((person.status, str(person)),
                         (409, "Сотрудника только что посадили на другую линию — обновите список"))
        self.assertEqual(str(unknown), str(line))

    def test_line_lock_is_a_transaction_lock_on_this_line(self):
        cursor = _Cursor()
        svc = dial_service.DialListService(_Db(cursor=cursor))
        with svc._line_lock(LINE_DEPARTMENT, "906") as cur:
            self.assertIs(cur, cursor, 'проверка и запись обязаны идти в транзакции замка')
        self.assertEqual(cursor.executed, [("SELECT pg_advisory_xact_lock(hashtext(%s))", ("dial_list_line:1954:906",))])

    def test_line_holder_counts_bindings_and_own_sip_settings(self):
        cursor = _Cursor([('FROM users u', {'one': (11, "Занятов Захар")})])
        svc = dial_service.DialListService(_Db())
        self.assertEqual(svc._line_holder(cursor, LINE_DEPARTMENT, "906", exclude_user_id=77),
                         {"id": 11, "name": "Занятов Захар"})
        sql, params = cursor.executed[0]
        flat = ' '.join(sql.split())
        self.assertIn("LEFT JOIN dial_list_line_members m ON m.user_id = u.id AND m.released_at IS NULL", flat)
        self.assertIn("WHERE u.id <> %s AND LOWER(COALESCE(u.status, '')) <> ALL(%s) "
                      "AND ((m.department_id = %s AND m.internal_number = %s) "
                      "OR (u.department_id = %s AND TRIM(COALESCE(u.sip_number, '')) = %s "
                      "AND (m.user_id IS NULL OR m.department_id <> %s)))", flat)
        self.assertEqual(params, (77, ['fired', 'dismissal'], LINE_DEPARTMENT, "906",
                                  LINE_DEPARTMENT, "906", LINE_DEPARTMENT))
        empty = _Cursor()
        self.assertIsNone(svc._line_holder(empty, LINE_DEPARTMENT, "906"))
        self.assertEqual(empty.executed[0][1][0], 0, 'без исключения сравниваем с id 0')

    def test_former_operators_are_those_who_called_the_base_and_left(self):
        cursor = _Cursor([('FROM dial_list_portions p', {'all': [(77, "Гостев Глеб", "gleb"), (78, None, "")]})])
        svc = dial_service.DialListService(_Db(cursor=cursor))
        self.assertEqual(svc.former_operators(LINE_DEPARTMENT, exclude_ids=[10, "11"]), [
            {"id": 77, "name": "Гостев Глеб", "login": "gleb"}, {"id": 78, "name": "", "login": ""}])
        sql, params = cursor.executed[0]
        self.assertIn("WHERE p.department_id = %s AND u.id <> ALL(%s)", ' '.join(sql.split()))
        self.assertEqual(params, (LINE_DEPARTMENT, [10, 11]))

    def test_schema_keeps_one_line_per_person_and_one_person_per_line(self):
        ddl = ' '.join(' '.join(s.split()) for s in dial_schema.DDL)
        self.assertIn("CREATE TABLE IF NOT EXISTS dial_list_line_members", ddl)
        self.assertIn("ON dial_list_line_members(user_id) WHERE released_at IS NULL", ddl)
        self.assertIn("ON dial_list_line_members(department_id, internal_number) WHERE released_at IS NULL", ddl)
        table = next(s for s in dial_schema.DDL if 'CREATE TABLE IF NOT EXISTS dial_list_line_members' in s)
        for column in ('user_id', 'department_id', 'internal_number', 'sip_login', 'sip_password',
                       'assigned_by', 'assigned_at', 'released_by', 'released_at'):
            self.assertIn(column, table)
        # Таблица — до первого индекса (порядок DDL однажды уже ронял прод).
        first_index = next(i for i, s in enumerate(dial_schema.DDL) if 'CREATE UNIQUE INDEX' in s or 'CREATE INDEX' in s)
        self.assertLess(dial_schema.DDL.index(table), first_index)

    def test_line_member_row_mapping(self):
        row = (77, LINE_DEPARTMENT, ' 906 ', 'lg906', 'pw906', 'binotel', ' sip53.binotel.com ', None, 4)
        cursor = _Cursor([('FROM dial_list_line_members m', {'one': row})])
        svc = dial_service.DialListService(_Db(cursor=cursor))
        self.assertEqual(svc.line_member(77), {
            "user_id": 77, "department_id": LINE_DEPARTMENT, "internal_number": "906",
            "sip_login": "lg906", "sip_password": "pw906", "provider": "binotel",
            "sip_server": "sip53.binotel.com", "auto_answer": None, "auto_answer_delay": 4,
        })
        sql, params = cursor.executed[0]
        self.assertIn("m.released_at IS NULL", sql)
        self.assertEqual(params, (77,))
        svc = dial_service.DialListService(_Db(cursor=_Cursor()))
        self.assertIsNone(svc.line_member(77))


class CountsAsLineDepartmentEmployeeTests(unittest.TestCase):
    """«Показатели по этому сотруднику должны считаться как и для сотрудников удалённого КЦ»."""

    MEMBER = {"user_id": 77, "department_id": LINE_DEPARTMENT, "internal_number": "906",
              "sip_login": "lg906", "sip_password": "pw906", "provider": "binotel",
              "sip_server": "sip53.binotel.com", "auto_answer": None, "auto_answer_delay": None}

    def _svc(self, member):
        svc = dial_service.DialListService(_Db({77: GUEST, 10: OWN}))
        svc.line_member = lambda uid: member
        svc.department_settings = lambda dep: {
            "configured": dep == LINE_DEPARTMENT, "enabled": dep == LINE_DEPARTMENT, "portion_size": 25,
            "department_id": dep}
        svc.user_setting = lambda uid: None
        return svc

    def test_dial_operator_swaps_department_provider_and_line(self):
        card = self._svc(dict(self.MEMBER)).dial_operator(77)
        self.assertEqual((card["department_id"], card["department_provider"], card["sip_number"]),
                         (LINE_DEPARTMENT, "binotel", "906"))
        self.assertIs(card["line_member"], True)
        self.assertEqual(card["name"], "Гостев Глеб")
        # Без привязки карточка — его собственная, как была.
        self.assertEqual(self._svc(None).dial_operator(77), GUEST)
        self.assertIsNone(self._svc(None).dial_operator(4242))

    def test_already_read_binding_is_not_read_again(self):
        svc = self._svc(None)
        svc.line_member = lambda uid: self.fail('привязку читают второй раз')
        self.assertEqual(svc.dial_operator(77, member=dict(self.MEMBER))["sip_number"], "906")
        self.assertEqual(svc.dial_operator(77, member=None), GUEST)
        self.assertTrue(svc.phone_settings(77, member=dict(self.MEMBER))["enabled"])

    def test_phone_gets_the_mode_of_the_line_department(self):
        """У его отдела (СЗоВ) обзвона нет вовсе — режим берётся у отдела линии."""
        settings = self._svc(dict(self.MEMBER)).phone_settings(77)
        self.assertEqual((settings["enabled"], settings["portion_size"], settings["hide_numbers"]), (True, 25, True))
        self.assertEqual(self._svc(None).phone_settings(77), {"enabled": False})

    def test_personal_switch_still_wins_over_the_line_department(self):
        svc = self._svc(dict(self.MEMBER))
        svc.user_setting = lambda uid: False
        self.assertIs(svc.phone_settings(77)["enabled"], False)

    def test_operator_context_is_the_line_department_and_number(self):
        ctx = self._svc(dict(self.MEMBER)).operator_context(77)
        self.assertEqual((ctx["department_id"], ctx["internal_number"], ctx["name"]),
                         (LINE_DEPARTMENT, "906", "Гостев Глеб"))
        # Не сидит на линии — раздел для него закрыт: его отдел не на Binotel.
        with self.assertRaises(dial_service.DialListError) as raised:
            self._svc(None).operator_context(77)
        self.assertEqual(raised.exception.status, 409)

    def test_every_phone_route_resolves_the_operator_through_the_binding(self):
        """Порция, звонок, итог, «Мой прогресс», скрипт — все берут отдел из operator_context,
        а он — из dial_operator. Прямого чтения карточки мимо привязки быть не должно."""
        for name in ('operator_context', 'phone_settings'):
            src = inspect.getsource(getattr(dial_service.DialListService, name))
            self.assertIn('self.dial_operator(', src, name)
            self.assertNotIn('self.db.get_sip_operator(', src, name)
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        settings_route = src[src.index("def operator_settings(user_id)"):src.index("def lines(department_id)")]
        self.assertIn('svc.dial_operator(user_id)', settings_route)
        self.assertNotIn('db.get_sip_operator', settings_route)

    def test_department_users_include_people_seated_on_its_lines(self):
        rows = [
            (10, "Своев Сава", "sava", "operator", "", "working", None, False, "Удаленный КЦ"),
            (77, "Гостев Глеб", "gleb", "operator", "906", "working", True, True, "СЗоВ"),
        ]
        cursor = _Cursor([('FROM users u', {'all': rows})])
        svc = dial_service.DialListService(_Db(cursor=cursor))
        users = svc.department_users(LINE_DEPARTMENT)
        self.assertEqual(users[0], {"id": 10, "name": "Своев Сава", "login": "sava", "role": "operator",
                                    "sip_number": "", "status": "working", "dial_list_enabled": None,
                                    "guest": False, "department_name": ""})
        self.assertEqual((users[1]["guest"], users[1]["department_name"], users[1]["sip_number"],
                          users[1]["dial_list_enabled"]), (True, "СЗоВ", "906", True))
        sql, params = cursor.executed[0]
        flat = ' '.join(sql.split())
        # Линия посаженного — из привязки, а не users.sip_number (там логин Oktell его отдела).
        self.assertIn("COALESCE(m.internal_number, u.sip_number, '')", flat)
        # «Чужой» — только пока человек числится в другом отделе: переведённый в отдел
        # линии с ещё действующей привязкой уже свой.
        self.assertIn("(m.user_id IS NOT NULL AND u.department_id IS DISTINCT FROM m.department_id)", flat)
        self.assertIn("LEFT JOIN dial_list_line_members m ON m.user_id = u.id AND m.released_at IS NULL", flat)
        # Условие целиком: свои, не посаженные на линию другого отдела, плюс все, кто сидит
        # на линиях этого. Любая приписка к нему (даже «AND FALSE») обязана ронять тест.
        self.assertIn("WHERE LOWER(COALESCE(u.status, '')) <> ALL(%s) "
                      "AND ((u.department_id = %s AND (m.user_id IS NULL OR m.department_id = %s)) "
                      "OR m.department_id = %s) ORDER BY u.name", flat)
        self.assertEqual(params, (['fired', 'dismissal'], LINE_DEPARTMENT, LINE_DEPARTMENT, LINE_DEPARTMENT))

    def test_lines_show_the_seated_person_and_his_department(self):
        svc, _db, _log = _service(users=[
            {"id": 77, "name": "Гостев Глеб", "login": "gleb", "sip_number": "906", "guest": True,
             "department_name": "СЗоВ"},
            {"id": 10, "name": "Своев Сава", "login": "sava", "sip_number": "905"},
        ])
        lines = {l["internal_number"]: l for l in svc.list_lines(LINE_DEPARTMENT)}
        self.assertEqual(lines["906"]["icore_user"], {"id": 77, "name": "Гостев Глеб", "login": "gleb",
                                                      "guest": True, "department_name": "СЗоВ"})
        self.assertEqual(lines["905"]["icore_user"], {"id": 10, "name": "Своев Сава", "login": "sava",
                                                      "guest": False, "department_name": ""})
        self.assertNotIn("pw90", str(lines))
        self.assertNotIn("lg90", str(lines))

    def test_overview_is_scoped_by_the_base_not_by_the_person_department(self):
        """Сводка «Операторы»: отбор по users.department_id терял сотрудника другого отдела."""
        cursor = _Cursor([('FROM users u', {'all': [
            (77, "Гостев Глеб", HOME_DEPARTMENT, "СЗоВ", 20, 5, 7, 3, 240, 0, 1, 2)]})])
        svc = dial_service.DialListService(_Db(cursor=cursor))
        rows = svc.overview([LINE_DEPARTMENT], date(2026, 10, 7))
        self.assertEqual(rows, [{
            "operator_id": 77, "operator_name": "Гостев Глеб", "department_id": HOME_DEPARTMENT,
            "department_name": "СЗоВ", "issued": 20, "done": 5, "attempts": 7, "answered": 3,
            "talk_sec": 240, "failed": 0, "cancelled": 1, "successes": 2}])
        sql, params = cursor.executed[0]
        self.assertEqual(params, {"day": date(2026, 10, 7), "departments": [LINE_DEPARTMENT]})
        self.assertEqual(sql.count("AND l.department_id = ANY(%(departments)s)"), 3)   # попытки, выдачи, успешки
        self.assertNotIn("u.department_id = ANY", sql)
        self.assertEqual(sql.count("%(day)s"), 3)
        # Админ без выбранного отдела: отбора по отделу нет вовсе.
        cursor2 = _Cursor()
        dial_service.DialListService(_Db(cursor=cursor2)).overview(None, date(2026, 10, 7))
        sql2, params2 = cursor2.executed[0]
        self.assertEqual(params2, {"day": date(2026, 10, 7)})
        self.assertNotIn("%(departments)s", sql2)
        self.assertNotIn("l.department_id = ANY", sql2)

    def test_candidates_are_other_departments_people_not_yet_on_its_lines(self):
        cursor = _Cursor([('FROM users u', {'all': [(77, "Гостев Глеб", "gleb", "СЗоВ"), (90, None, "", "")]})])
        svc = dial_service.DialListService(_Db(cursor=cursor))
        self.assertEqual(svc.line_candidates(LINE_DEPARTMENT), [
            {"id": 77, "name": "Гостев Глеб", "login": "gleb", "department_name": "СЗоВ"},
            {"id": 90, "name": "", "login": "", "department_name": ""}])
        sql, params = cursor.executed[0]
        flat = ' '.join(sql.split())
        self.assertIn("u.department_id IS DISTINCT FROM %s", flat)
        self.assertIn("AND m.user_id IS NULL", flat)
        self.assertIn("LOWER(COALESCE(u.status, '')) <> ALL(%s)", flat)
        self.assertEqual(params, (LINE_DEPARTMENT, LINE_DEPARTMENT, ['fired', 'dismissal']))
        # Только то, что нужно для выбора по ФИО: ни номеров, ни учёток.
        for secret in ('sip_number', 'sip_password', 'sip_login', 'phone'):
            self.assertNotIn(secret, flat)


class LinesRoutesTests(unittest.TestCase):
    """Право решает сервер: список чужих сотрудников и сама посадка — только с ним."""

    def _client(self, role, heads, svc, with_super_rule=True):
        import flask
        requester = (5, None, 'Руководитель', role, None, None, None, 'boss')
        kwargs = {"is_super_admin_role": (lambda r: r == 'super_admin')} if with_super_rule else {}
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda r: r in ('admin', 'super_admin'),
            headed_department_ids=lambda rid: list(heads), service=svc, **kwargs)
        app = flask.Flask('dial_list_lines_test')
        app.register_blueprint(bp)
        return app.test_client()

    class Svc:
        def __init__(self):
            self.calls = []

        def manager_scope(self, is_admin, heads, login=None):
            # Как на проде: админ и глава СЗоВ видят весь раздел, глава отдела линии —
            # только свой отдел, остальные — ничего.
            if is_admin or 1 in list(heads):
                return None
            return [LINE_DEPARTMENT] if LINE_DEPARTMENT in list(heads) else []

        def can_seat_anyone(self, is_super_admin, heads):
            self.calls.append(('right', bool(is_super_admin), list(heads)))
            return bool(is_super_admin) or 1 in list(heads)      # отдел 1 — СЗоВ

        def department_sip_server(self, dep):
            return 'sip53.binotel.com'

        def list_lines(self, dep):
            return []

        def department_users(self, dep):
            return [{"id": 10, "name": "Своев Сава"}]

        def former_operators(self, dep, exclude_ids=()):
            self.calls.append(('former', dep, list(exclude_ids)))
            return [{"id": 77, "name": "Гостев Глеб", "login": "gleb"}]

        def line_candidates(self, dep):
            self.calls.append(('candidates', dep))
            return [{"id": 77, "name": "Гостев Глеб", "login": "gleb", "department_name": "СЗоВ"}]

        def assign_line(self, dep, user_id, number, changed_by=None, seat_anyone=False):
            self.calls.append(('assign', dep, user_id, number, changed_by, seat_anyone))
            return {"user_id": user_id}

        def release_line(self, dep, user_id, changed_by=None, seat_anyone=False):
            self.calls.append(('release', dep, user_id, changed_by, seat_anyone))
            return {"user_id": user_id}

    URL = f'/api/dial_list/departments/{LINE_DEPARTMENT}/lines'

    def test_plain_admin_gets_no_candidates_and_cannot_seat_a_stranger(self):
        svc = self.Svc()
        client = self._client('admin', [], svc)
        body = client.get(self.URL).get_json()
        self.assertIs(body["can_seat_anyone"], False)
        self.assertEqual(body["candidates"], [])
        self.assertEqual(body["users"], [{"id": 10, "name": "Своев Сава"}])
        self.assertNotIn(('candidates', LINE_DEPARTMENT), svc.calls, 'список чужих сотрудников прочитан зря')
        client.post(self.URL + '/assign', json={"user_id": 77, "internal_number": "906"})
        client.post(self.URL + '/release', json={"user_id": 77})
        self.assertIn(('assign', LINE_DEPARTMENT, 77, "906", 5, False), svc.calls)
        self.assertIn(('release', LINE_DEPARTMENT, 77, 5, False), svc.calls)

    def test_super_admin_and_szov_head_get_candidates_and_the_right(self):
        for role, heads in (('super_admin', []), ('admin', [1]), ('sv', [1])):
            svc = self.Svc()
            client = self._client(role, heads, svc)
            body = client.get(self.URL).get_json()
            self.assertIs(body["can_seat_anyone"], True, (role, heads))
            self.assertEqual([c["id"] for c in body["candidates"]], [77])
            client.post(self.URL + '/assign', json={"user_id": 77, "internal_number": "906"})
            client.post(self.URL + '/release', json={"user_id": 77})
            self.assertIn(('assign', LINE_DEPARTMENT, 77, "906", 5, True), svc.calls)
            self.assertIn(('release', LINE_DEPARTMENT, 77, 5, True), svc.calls)
            self.assertIn(('right', role == 'super_admin', heads), svc.calls)

    def test_role_admin_alone_is_not_super_admin(self):
        """Суперадмином считает переданное правило, а не «любой админ»; без правила — никто."""
        svc = self.Svc()
        self._client('super_admin', [], svc, with_super_rule=False).get(self.URL)
        self.assertEqual(svc.calls[0], ('right', False, []))
        # Проводка именно ЭТОГО раздела: тот же аргумент получают и другие Blueprint'ы
        # монолита, поэтому ищем его внутри вызова build_dial_list_blueprint, а не в файле.
        wiring = _read(BOT_PATH)
        start = wiring.index('_dial_list_bp = build_dial_list_blueprint(')
        call = wiring[start:wiring.index('app.register_blueprint(_dial_list_bp)', start)]
        self.assertIn('is_super_admin_role=_is_super_admin_role,', call)
        self.assertIn('is_admin_role=_is_admin_role,', call)
        self.assertIn('headed_department_ids=_headed_department_ids,', call)

    def test_admin_heading_another_department_gets_no_right(self):
        """Главный боевой случай: почти все админы на проде — главы своих отделов. Сервер
        пускает их в ручки раздела (давняя нестыковка с меню), но права сажать и снимать
        чужих это не даёт."""
        for heads in ([367], [560], [367, 560]):
            svc = self.Svc()
            client = self._client('admin', heads, svc)
            body = client.get(self.URL).get_json()
            self.assertIs(body["can_seat_anyone"], False, heads)
            self.assertEqual(body["candidates"], [])
            self.assertNotIn(('candidates', LINE_DEPARTMENT), svc.calls)
            client.post(self.URL + '/assign', json={"user_id": 77, "internal_number": "906"})
            client.post(self.URL + '/release', json={"user_id": 77})
            self.assertIn(('assign', LINE_DEPARTMENT, 77, "906", 5, False), svc.calls)
            self.assertIn(('release', LINE_DEPARTMENT, 77, 5, False), svc.calls)

    def test_section_stranger_gets_nothing(self):
        svc = self.Svc()
        client = self._client('operator', [], svc)
        resp = client.get(self.URL)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(client.post(self.URL + '/assign', json={"user_id": 77, "internal_number": "906"}).status_code, 403)
        self.assertEqual(client.post(self.URL + '/release', json={"user_id": 77}).status_code, 403)
        self.assertEqual(svc.calls, [], 'до права и до сервиса дело дойти не должно')

    def test_head_of_the_line_department_works_only_in_it(self):
        """Глава отдела линии: свои линии — да, линии чужого отдела раздела — нет."""
        svc = self.Svc()
        client = self._client('sv', [LINE_DEPARTMENT], svc)
        self.assertEqual(client.get(self.URL).status_code, 200)
        other = '/api/dial_list/departments/560/lines'
        self.assertEqual(client.get(other).status_code, 403)
        self.assertEqual(client.post(other + '/assign', json={"user_id": 77, "internal_number": "906"}).status_code, 403)
        self.assertEqual(client.post(other + '/release', json={"user_id": 77}).status_code, 403)
        self.assertFalse([c for c in svc.calls if c[0] in ('assign', 'release') and c[1] == 560])

    def test_users_route_lists_former_operators_for_the_journal(self):
        svc = self.Svc()
        client = self._client('admin', [], svc)
        body = client.get(f'/api/dial_list/departments/{LINE_DEPARTMENT}/users').get_json()
        self.assertEqual(body["users"], [{"id": 10, "name": "Своев Сава"}])
        self.assertEqual(body["former"], [{"id": 77, "name": "Гостев Глеб", "login": "gleb"}])
        # Бывшие — за вычетом нынешнего состава.
        self.assertIn(('former', LINE_DEPARTMENT, [10]), svc.calls)
        stranger = self._client('operator', [], self.Svc())
        self.assertEqual(stranger.get(f'/api/dial_list/departments/{LINE_DEPARTMENT}/users').status_code, 403)

    def test_lines_answer_never_carries_line_credentials(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        lines_part = src[src.index("def lines(department_id)"):src.index("def lines_assign")]
        self.assertNotIn("password", lines_part)
        self.assertNotIn("line_member", lines_part)


class IcorePhoneDownloadTests(unittest.TestCase):
    """Кнопка и сама ссылка: глава СЗоВ и все, кого посадили на линию в разделе."""

    def _gate(self, *, headed=None, headed_departments=(), member=None, department=None, role='operator',
              dial_departments=(LINE_DEPARTMENT,)):
        import logging

        class Db:
            @staticmethod
            def get_headed_departments_for_user(user_id):
                return list(headed_departments)

            @staticmethod
            def get_user_department_id(user_id):
                return department

        namespace = _bot_functions(['_heads_icore_phone_department', '_can_download_icore_phone'], {
            'logging': logging, 'db': Db,
            'ICORE_PHONE_DEPARTMENT_IDS': _bot_constant('ICORE_PHONE_DEPARTMENT_IDS'),
            'ICORE_PHONE_HEAD_DEPARTMENT_CODES': _bot_constant('ICORE_PHONE_HEAD_DEPARTMENT_CODES'),
            '_is_admin_role': lambda r: r in ('admin', 'super_admin'),
            '_headed_department_id': lambda uid: headed,
            '_dial_list_department_allowed': lambda dep: dep in dial_departments,
            '_dial_list_line_member': lambda uid: member,
        })
        return namespace['_can_download_icore_phone'](77, role)

    def test_szov_head_downloads_whatever_his_base_role(self):
        szov = [{"id": HOME_DEPARTMENT, "name": "СЗоВ", "code": "szov"}]
        self.assertTrue(self._gate(role='sv', headed=HOME_DEPARTMENT, headed_departments=szov, department=HOME_DEPARTMENT))
        # Глава двух отделов: СЗоВ — не первый по алфавиту, _headed_department_id отдаёт другой.
        both = [{"id": 5, "name": "Архив", "code": "archive"}, {"id": HOME_DEPARTMENT, "name": "СЗоВ", "code": " SZOV "}]
        self.assertTrue(self._gate(role='sv', headed=5, headed_departments=both, department=5))
        # Глава другого отдела и рядовой сотрудник СЗоВ кнопку не получают.
        self.assertFalse(self._gate(role='sv', headed=5, headed_departments=both[:1], department=5))
        self.assertFalse(self._gate(role='operator', department=HOME_DEPARTMENT))

    def test_everyone_seated_on_a_line_downloads(self):
        member = {"user_id": 77, "department_id": LINE_DEPARTMENT, "internal_number": "906"}
        self.assertTrue(self._gate(member=member, department=HOME_DEPARTMENT))
        # И человек без отдела: проверка привязки стоит выше «отдела нет — отказ».
        self.assertTrue(self._gate(member=member, department=None))
        self.assertFalse(self._gate(member=None, department=None))

    def test_a_failure_to_read_headed_departments_gives_nothing(self):
        """Сбой базы при чтении возглавляемых отделов — отказ, а не программа любому."""
        import logging

        class Db:
            @staticmethod
            def get_headed_departments_for_user(user_id):
                raise RuntimeError('pool timeout')

            @staticmethod
            def get_user_department_id(user_id):
                return HOME_DEPARTMENT

        namespace = _bot_functions(['_heads_icore_phone_department', '_can_download_icore_phone'], {
            'logging': logging, 'db': Db,
            'ICORE_PHONE_DEPARTMENT_IDS': _bot_constant('ICORE_PHONE_DEPARTMENT_IDS'),
            'ICORE_PHONE_HEAD_DEPARTMENT_CODES': _bot_constant('ICORE_PHONE_HEAD_DEPARTMENT_CODES'),
            '_is_admin_role': lambda r: False,
            '_headed_department_id': lambda uid: None,
            '_dial_list_department_allowed': lambda dep: False,
            '_dial_list_line_member': lambda uid: None,
        })
        with self.assertLogs(level='WARNING'):
            self.assertIs(namespace['_heads_icore_phone_department'](77), False)
        with self.assertLogs(level='WARNING'):
            self.assertIs(namespace['_can_download_icore_phone'](77, 'operator'), False)

    def test_old_rules_stay(self):
        self.assertTrue(self._gate(role='admin'))
        self.assertTrue(self._gate(department=367))
        self.assertTrue(self._gate(department=560))
        self.assertTrue(self._gate(department=LINE_DEPARTMENT))          # отдел удалённого КЦ
        self.assertFalse(self._gate(department=909))

    def test_head_codes_match_on_both_sides(self):
        back = set(_bot_constant('ICORE_PHONE_HEAD_DEPARTMENT_CODES'))
        front = re.search(r"ICORE_PHONE_HEAD_DEPARTMENT_CODES = new Set\(\[([^\]]*)\]\)", _read(APP_PATH))
        self.assertIsNotNone(front, 'константа пропала из src/App.jsx')
        self.assertEqual(back, set(re.findall(r"'([a-z_]+)'", front.group(1))))
        self.assertEqual(back, {'szov'})

    def test_profile_carries_the_flag_and_the_menu_reads_it(self):
        payload = _bot_function_source('_get_user_payload')
        self.assertIn('dial_list_line_member = bool(_dial_list_line_member(user_id)) if user_id is not None else False',
                      payload)
        self.assertIn('"dial_list_line_member": dial_list_line_member,', payload)
        app = _read(APP_PATH)
        gate = app[app.index('const canDownloadIcorePhone = isAdminLikeRole'):]
        gate = gate[:gate.index('const canAccessOktellGuard')]
        self.assertIn('user?.dial_list_line_member === true', gate)
        self.assertIn('ICORE_PHONE_HEAD_DEPARTMENT_CODES.has(code)', gate)
        self.assertIn('isDepartmentHead(user)', gate)

    def test_binding_is_read_once_per_request_and_never_breaks_the_phone(self):
        """Сбой раздела не должен оставить без регистрации весь парк телефонов:
        ручка настроек у них общая, а привязку спрашивают у каждого."""
        import logging

        class G:
            pass

        class Svc:
            def __init__(self):
                self.reads = 0
                self.fail = False

            def line_member(self, user_id):
                self.reads += 1
                if self.fail:
                    raise RuntimeError('таблицы нет')
                return {"user_id": user_id, "internal_number": "906"}

        svc = Svc()
        namespace = _bot_functions(['_dial_list_line_member'], {
            'logging': logging, 'g': G(), '_dial_list_service': svc})
        read = namespace['_dial_list_line_member']
        self.assertEqual(read(77)["internal_number"], "906")
        self.assertEqual(read(77)["internal_number"], "906")
        self.assertEqual(svc.reads, 1)
        svc.fail = True
        self.assertIsNone(read(78))
        self.assertIsNone(read(78))
        self.assertEqual(svc.reads, 2, 'отказ тоже запоминается: один запрос на человека')
        namespace['_dial_list_service'] = None
        self.assertIsNone(namespace['_dial_list_line_member'](79))
        self.assertIsNone(read(None))

    def test_phone_registration_and_dial_block_share_one_binding(self):
        """Регистрация и блок dial_list одного ответа собираются по одной и той же привязке:
        линия удалённого КЦ без вкладки обзвона (или наоборот) телефону уйти не должна."""
        import logging
        member = {"user_id": 77, "department_id": LINE_DEPARTMENT, "internal_number": "906", "provider": "binotel"}
        seen = {"accounts": [], "phone": []}

        class Db:
            @staticmethod
            def get_user_sip_account(user_id, line_member=None):
                seen["accounts"].append((user_id, line_member))
                if line_member == "boom":
                    raise RuntimeError('база недоступна')
                return {"provider": "binotel", "main": {"number": line_member["internal_number"]}}

        class Svc:
            @staticmethod
            def phone_settings(user_id, member=dial_service.MEMBER_NOT_GIVEN):
                seen["phone"].append((user_id, member))
                return {"enabled": bool(member)}

        current = {"member": member}
        namespace = _bot_functions(['_dial_list_line_account', '_dial_list_phone_settings'], {
            'logging': logging, 'db': Db, '_dial_list_service': Svc,
            '_dial_list_line_member': lambda uid: current["member"]})
        account = namespace['_dial_list_line_account']('77')
        self.assertEqual(account["main"]["number"], "906")
        self.assertEqual(seen["accounts"], [(77, member)])
        self.assertEqual(namespace['_dial_list_phone_settings'](77), {"enabled": True})
        self.assertIs(seen["phone"][-1][1], member, 'блок dial_list собран не по той же привязке')
        # Привязки нет — регистрации «на линии» нет, ручка пойдёт по настройкам его отдела.
        current["member"] = None
        self.assertIsNone(namespace['_dial_list_line_account'](77))
        self.assertEqual(len(seen["accounts"]), 1, 'без привязки базу лишний раз не трогаем')
        self.assertEqual(namespace['_dial_list_phone_settings'](77), {"enabled": False})
        self.assertIsNone(seen["phone"][-1][1])
        # Сбой сборки — None (остаётся телефония своего отдела), а не падение ручки всем.
        current["member"] = "boom"
        self.assertIsNone(namespace['_dial_list_line_account'](77))
        namespace['_dial_list_service'] = None
        self.assertEqual(namespace['_dial_list_phone_settings'](77), {"enabled": False})


class SectionNameTests(unittest.TestCase):
    """Раздел «Обзвон из телефона» переименован в «Удаленный КЦ»; ключ раздела прежний."""

    def test_menu_and_header_carry_the_new_name(self):
        app = _read(APP_PATH)
        item = '<FaIcon className="fas fa-list-check"></FaIcon> <span className="sidebar-text">Удаленный КЦ</span>'
        self.assertEqual(app.count(item), 2)                                   # ветка админа и ветка главы отдела
        self.assertNotIn('<span className="sidebar-text">Обзвон из телефона</span>', app)
        self.assertEqual(app.count("handleSidebarViewNavigation(e, 'dial_list')"), 2)
        view = _read(os.path.join(ROOT, 'src', 'components', 'dial_list', 'DialListView.jsx'))
        self.assertIn("const SECTION_TITLE = 'Удаленный КЦ';", view)
        header = view[view.index('fas fa-list-check'):view.index('<IosHint')]
        self.assertIn('{SECTION_TITLE}', header)
        self.assertNotIn('Обзвон из телефона', header)
        # Открытый выбор «Кому» — состояние вкладки: при смене отдела он сбрасывается.
        self.assertIsNotNone(re.search(r'<DialListLinesPanel\s+key=\{selected\.department_id\}', view),
                             'панель «Линии» без key — выбор «Кому» переживёт смену отдела')
        # Название раздела совпало с названием отдела: плашка единственного отдела не
        # должна повторять его в той же строке шапки (само правило исполняется в
        # tests/dial_list_line_picker.test.mjs).
        self.assertIn("import { sameSectionTitle } from './linePicker';", view)
        self.assertIn('departments.length === 1 && !sameSectionTitle(selected?.department_name, SECTION_TITLE) && (',
                      view)

    def test_pointers_to_the_section_use_the_new_name(self):
        sip = _read(os.path.join(ROOT, 'src', 'components', 'sip', 'SipSettingsView.jsx'))
        self.assertIn('в разделе «Удаленный КЦ», вкладка «Линии»', sip)
        self.assertNotIn('разделе «Обзвон из телефона»', sip)
        routes = inspect.getsource(dial_routes.build_dial_list_blueprint)
        self.assertIn('Раздел «Удаленный КЦ» вам недоступен', routes)


if __name__ == '__main__':
    unittest.main()
