# -*- coding: utf-8 -*-
"""Раздел «Удаленный КЦ» супервайзерам СЗоВ (решение владельца 10.10.2026: «открыть
раздел удаленный КЦ всем супервайзерам СЗоВ с полным доступом»).

Что защищаем:

  * СВ СЗоВ получает в разделе то же, что глава СЗоВ: все отделы раздела (с
    подключением отделов) и посадку на линию сотрудника другого отдела;
  * отдел — тот, где СВ числится; СВ других отделов и рядовые СЗоВ раздела не получают;
  * отдел читается только у супервайзера: остальным лишний запрос ни к чему;
  * монолит передаёт разделу роль и отдел — без проводки правило молча не работает;
  * фронт держит тот же список отделов, что и сервер.

Правила — настоящие (DialListService, функции bot_schedule2.py); подменены только
ответы базы. Меню и предикат портала — tests/dial_list_section_access.test.mjs.
Люди и логины выдуманы.
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import source_cache  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(ROOT, 'bot_schedule2.py')
APP_PATH = os.path.join(ROOT, 'src', 'App.jsx')

SZOV = 1                 # id отделов — как на проде
OP = 367
LINE_DEPARTMENT = 1954   # удалённый КЦ
TEZ = 560                # ещё один отдел в периметре раздела — «чужой» для главы удалённого КЦ
DEPARTMENT_CODES = {SZOV: 'szov', OP: 'op', LINE_DEPARTMENT: 'remote_cc', TEZ: 'tez'}


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def _bot_functions(names, namespace):
    """Исполняет функции bot_schedule2.py без импорта модуля (он поднимает пул к БД)."""
    for name in names:
        node = source_cache.function_copy(BOT_PATH, name)
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, f'<bot:{name}>', 'exec'), namespace)
    return namespace


class _UserDb:
    """Кусок Database для _department_code_of_user: отдел каждого сотрудника."""

    def __init__(self, departments):
        self.departments = departments   # user_id -> (department_id, code из карточки отдела)
        self.reads = []

    def get_user_department(self, user_id):
        self.reads.append(user_id)
        return self.departments.get(user_id, (None, None))

    def get_user_department_id(self, user_id):
        return self.departments.get(user_id, (None, None))[0]


def _monolith_rules(user_db):
    """Настоящие _is_supervisor_role и _department_code_of_user из монолита."""
    namespace = _bot_functions(
        ['_normalize_user_role', '_is_supervisor_role', '_department_code_of_user'],
        {'db': user_db, 'AI_QA_OP_DEPARTMENT_ID': OP},
    )
    return namespace['_is_supervisor_role'], namespace['_department_code_of_user']


class _Svc(dial_service.DialListService):
    """Настоящие правила доступа (manager_scope, can_seat_anyone, _heads_overseer);
    вместо базы — коды отделов и записи о том, до чего дошёл запрос."""

    def __init__(self):
        super().__init__(db=None)
        self.calls = []

    def _heads_department_with_code(self, department_ids, codes):
        return any(DEPARTMENT_CODES.get(int(x), '') in codes for x in (department_ids or []))

    def list_departments(self, department_ids=None):
        section = [LINE_DEPARTMENT, TEZ]
        ids = section if department_ids is None else [i for i in department_ids if i in section]
        return [{"department_id": i} for i in ids]

    def candidate_departments(self):
        return [{"department_id": 2008}]

    def enroll_department(self, department_id, changed_by=None):
        self.calls.append(('enroll', department_id, changed_by))
        return {"department_id": department_id}

    def department_sip_server(self, department_id):
        return 'sip53.binotel.com'

    def list_lines(self, department_id):
        return []

    def department_users(self, department_id):
        return [{"id": 10, "name": "Своев Сава"}]

    def line_candidates(self, department_id):
        self.calls.append(('candidates', department_id))
        return [{"id": 77, "name": "Гостев Глеб", "login": "gleb", "department_name": "СЗоВ"}]

    def assign_line(self, department_id, user_id, internal_number, changed_by=None, seat_anyone=False):
        self.calls.append(('assign', department_id, user_id, internal_number, changed_by, seat_anyone))
        return {"user_id": user_id}

    def release_line(self, department_id, user_id, changed_by=None, seat_anyone=False):
        self.calls.append(('release', department_id, user_id, changed_by, seat_anyone))
        return {"user_id": user_id}


class SupervisorRuleTests(unittest.TestCase):
    """Само правило сервиса: код отдела СВ → весь раздел и посадка любого сотрудника."""

    def test_szov_supervisor_gets_what_the_szov_head_gets(self):
        svc = _Svc()
        head = (svc.manager_scope(False, [SZOV], login='head'), svc.can_seat_anyone(False, [SZOV]))
        supervisor = (svc.manager_scope(False, [], login='sv', supervisor_department_code='szov'),
                      svc.can_seat_anyone(False, [], supervisor_department_code='szov'))
        self.assertEqual(head, (None, True))
        self.assertEqual(supervisor, head)

    def test_code_is_compared_without_case_and_spaces(self):
        # Код в карточке отдела бывает записан как угодно.
        for code in ('SZOV', ' szov ', 'Szov'):
            self.assertIs(dial_service.full_access_supervisor(code), True, code)

    def test_other_departments_and_empty_code_get_nothing(self):
        svc = _Svc()
        for code in ('op', 'tez', 'remote_cc', '', None):
            self.assertEqual(svc.manager_scope(False, [], login='sv', supervisor_department_code=code), [], code)
            self.assertIs(svc.can_seat_anyone(False, [], supervisor_department_code=code), False, code)
            self.assertIs(dial_service.full_access_supervisor(code), False, code)

    def test_list_is_szov_only(self):
        self.assertEqual(dial_service.DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES, frozenset({'szov'}))

    def test_refusal_names_who_may_seat(self):
        self.assertIn('супервайзеры СЗоВ', dial_service.SEAT_ANYONE_DENIED)


class SupervisorRoutesTests(unittest.TestCase):
    """Настоящие ручки раздела + настоящие правила монолита о роли и отделе."""

    # user_id -> (отдел, код из карточки отдела)
    PEOPLE = {
        5: (SZOV, 'szov'),          # СВ СЗоВ
        6: (OP, 'op'),              # СВ отдела продаж
        7: (SZOV, ' SZOV '),        # СВ СЗоВ, код в карточке записан небрежно
        8: (SZOV, 'szov'),          # оператор СЗоВ
        9: (LINE_DEPARTMENT, 'remote_cc'),   # СВ удалённого КЦ — не глава
    }
    ROLES = {5: 'sv', 6: 'sv', 7: 'supervisor', 8: 'operator', 9: 'sv'}

    def _client(self, user_id, heads=(), svc=None):
        import flask
        svc = svc or _Svc()
        user_db = _UserDb(self.PEOPLE)
        is_supervisor_role, department_code_of = _monolith_rules(user_db)
        requester = (user_id, None, 'Сотрудник', self.ROLES[user_id], None, None, None, f'user{user_id}')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (user_id, requester, None),
            is_admin_role=lambda r: r in ('admin', 'super_admin'),
            headed_department_ids=lambda rid: list(heads),
            is_super_admin_role=lambda r: r == 'super_admin',
            is_supervisor_role=is_supervisor_role, department_code_of=department_code_of,
            service=svc)
        app = flask.Flask(f'dial_list_sv_{user_id}')
        app.register_blueprint(bp)
        return app.test_client(), svc, user_db

    LINES = f'/api/dial_list/departments/{LINE_DEPARTMENT}/lines'

    def test_szov_supervisor_works_in_the_whole_section(self):
        for user_id in (5, 7):
            client, svc, _db = self._client(user_id)
            body = client.get('/api/dial_list/departments').get_json()
            # Весь раздел, как у главы СЗоВ: все отделы и кандидаты на подключение.
            self.assertEqual([d["department_id"] for d in body["departments"]], [LINE_DEPARTMENT, TEZ], user_id)
            self.assertEqual(body["candidates"], [{"department_id": 2008}])
            self.assertEqual(client.post('/api/dial_list/departments/2008/enroll').status_code, 200)
            self.assertIn(('enroll', 2008, user_id), svc.calls)
            # Отдел, которого у главы удалённого КЦ в зоне нет, — тоже открыт.
            self.assertEqual(client.get(f'/api/dial_list/departments/{TEZ}/lines').status_code, 200)

    def test_szov_supervisor_seats_and_releases_an_employee_of_another_department(self):
        client, svc, _db = self._client(5)
        body = client.get(self.LINES).get_json()
        self.assertIs(body["can_seat_anyone"], True)
        self.assertEqual([c["id"] for c in body["candidates"]], [77])
        self.assertEqual(client.post(self.LINES + '/assign', json={"user_id": 77, "internal_number": "906"}).status_code, 200)
        self.assertEqual(client.post(self.LINES + '/release', json={"user_id": 77}).status_code, 200)
        self.assertIn(('assign', LINE_DEPARTMENT, 77, "906", 5, True), svc.calls)
        self.assertIn(('release', LINE_DEPARTMENT, 77, 5, True), svc.calls)

    def test_supervisors_of_other_departments_get_nothing(self):
        for user_id in (6, 9):
            client, svc, _db = self._client(user_id)
            self.assertEqual(client.get('/api/dial_list/departments').status_code, 403, user_id)
            self.assertEqual(client.get(self.LINES).status_code, 403, user_id)
            self.assertEqual(client.post(self.LINES + '/assign', json={"user_id": 77, "internal_number": "906"}).status_code, 403)
            self.assertEqual(client.post(self.LINES + '/release', json={"user_id": 77}).status_code, 403)
            self.assertEqual(svc.calls, [], 'до сервиса дело дойти не должно')

    def test_szov_operator_gets_nothing_and_his_department_is_not_read(self):
        client, svc, user_db = self._client(8)
        self.assertEqual(client.get('/api/dial_list/departments').status_code, 403)
        self.assertEqual(client.get(self.LINES).status_code, 403)
        self.assertEqual(user_db.reads, [], 'отдел нужен только супервайзеру')
        self.assertEqual(svc.calls, [])

    def test_head_of_the_line_department_keeps_his_old_zone(self):
        """Глава удалённого КЦ, сам не из СЗоВ, по-прежнему работает только в своём отделе."""
        client, _svc, _db = self._client(9, heads=[LINE_DEPARTMENT])
        self.assertEqual(client.get(self.LINES).status_code, 200)
        self.assertIs(client.get(self.LINES).get_json()["can_seat_anyone"], False)
        self.assertEqual(client.get(f'/api/dial_list/departments/{TEZ}/lines').status_code, 403)

    def test_without_wiring_supervisors_are_not_let_in(self):
        """Ручки без is_supervisor_role/department_code_of (старая проводка) — СВ не пускают."""
        import flask
        requester = (5, None, 'Сотрудник', 'sv', None, None, None, 'user5')
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (5, requester, None), is_admin_role=lambda r: False,
            headed_department_ids=lambda rid: [], service=_Svc())
        app = flask.Flask('dial_list_sv_unwired')
        app.register_blueprint(bp)
        self.assertEqual(app.test_client().get(self.LINES).status_code, 403)


class WiringTests(unittest.TestCase):
    def test_monolith_passes_role_and_department_to_the_section(self):
        # Тот же аргумент получают и другие Blueprint'ы монолита — ищем внутри вызова раздела.
        wiring = _read(BOT_PATH)
        start = wiring.index('_dial_list_bp = build_dial_list_blueprint(')
        call = wiring[start:wiring.index('app.register_blueprint(_dial_list_bp)', start)]
        self.assertIn('is_supervisor_role=_is_supervisor_role,', call)
        self.assertIn('department_code_of=_department_code_of_user,', call)

    def test_department_is_the_one_the_supervisor_belongs_to(self):
        """_department_code_of_user отдаёт код отдела, где человек числится, без регистра."""
        user_db = _UserDb({5: (SZOV, ' SZOV '), 6: (None, None)})
        is_supervisor_role, department_code_of = _monolith_rules(user_db)
        self.assertEqual(department_code_of(5), 'szov')
        self.assertEqual(department_code_of(6), '')
        self.assertIs(is_supervisor_role('supervisor'), True)
        self.assertIs(is_supervisor_role('trainer'), False)

    def test_frontend_mirrors_the_backend_list(self):
        app = _read(APP_PATH)
        match = re.search(r"const DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES = new Set\(\[([^\]]*)\]\);", app)
        self.assertIsNotNone(match, 'список СВ раздела пропал из App.jsx')
        codes = frozenset(ast.literal_eval(f'[{match.group(1)}]'))
        self.assertEqual(codes, dial_service.DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES)


if __name__ == '__main__':
    unittest.main()
