# -*- coding: utf-8 -*-
"""Удалённый КЦ: глава не из отдела, выбор отдела при заведении сотрудника,
набор разделов рядового.

Просьба владельца 08.10.2026, три пункта:

  1. «У операторов отдела удалённый КЦ должны отображаться лишь раздел профиль,
     мои смены и вики, который не доступен без сканирования QR».
  2. «Добавить возможность делать главой отдела того человека, которого нету в
     этом отделе».
  3. «Когда она добавляла сотрудников, у неё был выбор отдела, куда добавить
     сотрудника. Но без sip номера, если это удалённый КЦ».

Ручки и помощники сервера здесь исполняются по-настоящему: их текст берётся из
`bot_schedule2.py`, подменены только база, запрос и вход. Поведение карты разделов
во фронте — tests/remote_cc_department_views.test.mjs.
"""

import ast
import json
import logging
import re
import shutil
import subprocess
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications import sources  # noqa: E402
from tests import source_cache  # noqa: E402

BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
ROLES_PATH = ROOT / "src" / "utils" / "roles.js"
MODAL_PATH = ROOT / "src" / "components" / "modals" / "UserEditModal.jsx"
DEPARTMENTS_VIEW_PATH = ROOT / "src" / "components" / "departments" / "DepartmentsView.jsx"
APP_PATH = ROOT / "src" / "App.jsx"

BOT_SOURCE = source_cache.read(BOT_PATH)
BOT_TREE = source_cache.tree(BOT_PATH)
_BOT_LINES = BOT_SOURCE.splitlines(keepends=True)

SZOV, SALES, OOZ, REMOTE = 1, 367, 2008, 1954
DEPARTMENTS = {
    SZOV: {'id': SZOV, 'code': 'szov', 'name': 'СЗоВ'},
    SALES: {'id': SALES, 'code': 'op', 'name': 'Отдел продаж'},
    OOZ: {'id': OOZ, 'code': 'request_processing_department', 'name': 'ООЗ'},
    REMOTE: {'id': REMOTE, 'code': 'remote_cc', 'name': 'Удаленный КЦ'},
}
# направление -> отдел
DIRECTIONS = {70: SZOV, 71: SALES, 900: REMOTE}
GROUPS = {
    10: {'id': 10, 'department_id': SZOV, 'status': 'active'},
    30: {'id': 30, 'department_id': SALES, 'status': 'active'},
    40: {'id': 40, 'department_id': OOZ, 'status': 'active'},
    90: {'id': 90, 'department_id': REMOTE, 'status': 'active'},
}


def _segment(node):
    """Исходник оператора по номерам строк: `ast.get_source_segment` на этом
    файле стоит 0,3 с за вызов."""
    chunk = list(_BOT_LINES[node.lineno - 1:node.end_lineno])
    chunk[0] = chunk[0][node.col_offset:]
    return "".join(chunk)


def _real(*names):
    """Настоящие функции и присваивания верхнего уровня монолита — в порядке
    объявления в модуле. Каждое имя обязано найтись: заглушка, подсунутая вместо
    пропавшего помощника, проверяла бы уже не код."""
    wanted = set(names)
    found = []
    chunks = []
    for node in BOT_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            found.append(node.name)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in wanted for target in node.targets):
            found.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        else:
            continue
        chunks.append(_segment(node))
    missing = wanted - set(found)
    assert not missing, 'в bot_schedule2.py нет: %s' % sorted(missing)
    return "\n".join(chunks)


def _endpoint(name):
    """Ручка без декораторов — по копии узла (дерево общее на весь набор)."""
    node = source_cache.function_copy(BOT_PATH, name)
    node.decorator_list = []
    return compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                   str(BOT_PATH), "exec")


def _function_source(name):
    return _segment(source_cache.function_node(BOT_PATH, name))


def _module_literal(name):
    """Значение присваивания верхнего уровня: литерал либо frozenset({...})."""
    for node in BOT_TREE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = node.value
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == 'frozenset' and len(value.args) == 1):
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise AssertionError('%s не найден в bot_schedule2.py' % name)


def _run_node(script):
    """Скрипт — через stdin: сетка пользователей не помещается в командную
    строку Windows (WinError 206)."""
    node = shutil.which('node')
    if not node:
        return None
    out = subprocess.run([node, '--input-type=module'], input=script.encode('utf-8'),
                         capture_output=True, check=True)
    return json.loads(out.stdout.decode('utf-8'))


def _answer(result):
    return (result[1], result[0]) if isinstance(result, tuple) else (200, result)


_ROLE_HELPERS = ('_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role',
                 '_is_super_admin_role', '_is_admin_role', '_is_global_admin_requester')

# Запрашивающие. headed — первый возглавляемый отдел (то, что отдаёт
# headed_department_id_for_user), headed_ids — все возглавляемые.
SUPER_ADMIN = {'id': 1, 'role': 'super_admin', 'department': None, 'headed': None, 'headed_ids': ()}
PLAIN_ADMIN = {'id': 2, 'role': 'admin', 'department': None, 'headed': None, 'headed_ids': ()}
# Глава двух отделов: числится в СЗоВ, возглавляет СЗоВ и удалённый КЦ.
TWO_HEAD = {'id': 300, 'role': 'admin', 'department': SZOV, 'headed': SZOV, 'headed_ids': (SZOV, REMOTE)}
# Глава одного отдела, в котором сама не числится.
OUTSIDE_HEAD = {'id': 301, 'role': 'sv', 'department': SALES, 'headed': REMOTE, 'headed_ids': (REMOTE,)}
SZOV_HEAD = {'id': 302, 'role': 'admin', 'department': SZOV, 'headed': SZOV, 'headed_ids': (SZOV,)}
SZOV_SV = {'id': 303, 'role': 'sv', 'department': SZOV, 'headed': None, 'headed_ids': (),
           'direction': 'Основа'}
OPERATOR = {'id': 304, 'role': 'operator', 'department': SZOV, 'headed': None, 'headed_ids': ()}


def _requester_row(requester):
    return (requester['id'], 'Заводящий', 'login', requester['role'], requester.get('direction'))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Главой назначают человека, которого нет в отделе
# ─────────────────────────────────────────────────────────────────────────────

class _HeadDb:
    def __init__(self, users):
        self.users = dict(users)
        self.assigned = []

    def get_department_by_id(self, department_id):
        return DEPARTMENTS.get(int(department_id))

    def get_user(self, id):  # noqa: A002 — так зовёт ручка
        return self.users.get(int(id))

    def get_user_department_id(self, _user_id):
        raise AssertionError('ручка не должна спрашивать отдел назначаемого')

    def set_department_head(self, department_id, user_id, changed_by=None):
        self.assigned.append((department_id, user_id, changed_by))
        return dict(DEPARTMENTS[department_id], head_user_id=user_id)


def _set_head(department_id, payload, requester=SUPER_ADMIN, users=None):
    db = _HeadDb(users if users is not None else {500: (500, None, 'Руководитель'), 501: (501, None, 'Свой')})
    errors = []
    namespace = {
        'db': db,
        'request': SimpleNamespace(method='PUT', get_json=lambda *a, **k: dict(payload)),
        'jsonify': lambda body: body,
        'logging': SimpleNamespace(error=lambda *a, **k: errors.append(a)),
        '_build_cors_preflight_response': lambda: 'preflight',
        '_get_authenticated_requester': lambda: (requester['id'], _requester_row(requester), None),
        '_headed_department_id': lambda _requester_id: requester.get('headed'),
    }
    exec(_real(*_ROLE_HELPERS), namespace)
    exec(_endpoint("api_admin_set_department_head"), namespace)
    status, body = _answer(namespace["api_admin_set_department_head"](department_id))
    if status == 500:
        raise AssertionError('ручка упала: %s' % errors)
    return status, body, db


class HeadFromAnotherDepartmentTests(unittest.TestCase):

    def test_person_outside_the_department_becomes_its_head(self):
        # 500 — человек из другого отдела: ручка его отдел не спрашивает вовсе
        # (_HeadDb.get_user_department_id роняет тест, если спросит).
        status, body, db = _set_head(REMOTE, {'user_id': 500})

        self.assertEqual(status, 200, body)
        self.assertEqual(db.assigned, [(REMOTE, 500, SUPER_ADMIN['id'])])
        self.assertEqual(body['department']['head_user_id'], 500)

    def test_id_comes_as_a_string_too(self):
        status, _body, db = _set_head(REMOTE, {'user_id': '500'}, requester=PLAIN_ADMIN)

        self.assertEqual(status, 200)
        self.assertEqual(db.assigned, [(REMOTE, 500, PLAIN_ADMIN['id'])])

    def test_head_is_still_removed_with_an_empty_id(self):
        for empty in (None, ''):
            with self.subTest(user_id=empty):
                status, _body, db = _set_head(REMOTE, {'user_id': empty})
                self.assertEqual(status, 200)
                self.assertEqual(db.assigned, [(REMOTE, None, SUPER_ADMIN['id'])])

    def test_unknown_person_department_and_garbage_are_refused(self):
        for department_id, payload, expected in (
            (REMOTE, {'user_id': 999}, (404, {"error": "User not found"})),
            (424242, {'user_id': 500}, (404, {"error": "Department not found"})),
            (REMOTE, {'user_id': 'abc'}, (400, {"error": "Invalid user_id"})),
        ):
            with self.subTest(payload=payload, department=department_id):
                status, body, db = _set_head(department_id, payload)
                self.assertEqual((status, body), expected)
                self.assertEqual(db.assigned, [])

    def test_only_a_global_admin_assigns_heads(self):
        # Снятая проверка отдела не расширила круг тех, кто назначает: глава
        # отдела (даже с ролью админа), супервайзер и оператор — по-прежнему нет.
        for requester in (TWO_HEAD, SZOV_HEAD, OUTSIDE_HEAD, SZOV_SV, OPERATOR):
            with self.subTest(requester=requester['id']):
                status, body, db = _set_head(REMOTE, {'user_id': 500}, requester=requester)
                self.assertEqual((status, body), (403, {"error": "Only admins can assign a department head"}))
                self.assertEqual(db.assigned, [])

    def test_membership_check_is_gone_from_the_source(self):
        endpoint = _function_source("api_admin_set_department_head")
        self.assertNotIn("Head must belong to this department", endpoint)
        self.assertNotIn("get_user_department_id", endpoint)
        self.assertIn("if not _is_global_admin_requester(requester_role, requester_id):", endpoint)

    def test_picker_offers_people_outside_the_department(self):
        """Проводка окна «Глава отдела»; сами правила списка (кто, в каком
        порядке, сколько) исполняет tests/remote_cc_department_views.test.mjs."""
        view = source_cache.read(DEPARTMENTS_VIEW_PATH)
        # Фильтра «только свой отдел» больше нет.
        self.assertNotIn("Number(dep) !== Number(headDept.id)) return false;", view)
        # Супервайзеров /api/admin/users админу не отдаёт — их добирают отдельно,
        # и оба ответа доходят до списка.
        self.assertIn("load('/api/admin/sv_list', (data) => data.sv_list),", view)
        self.assertIn("load('/api/admin/users',", view)
        self.assertIn("const list = resp.ok ? pick(data) : [];", view)
        self.assertIn("setUsers(mergePeople(employees, supervisors));", view)
        self.assertIn(
            "const { shown: headCandidates, total: headCandidatesTotal } = useMemo(() => pickHeadCandidates({\n"
            "        people: users, department: headDept, query: headQuery, departmentNameOf,\n"
            "    }), [users, headDept, headQuery, departmentNameOf]);", view)
        self.assertIn("import { mergePeople, pickHeadCandidates, roleLabel } from './headCandidates.js';", view)
        # Отдел рядом с должностью: тёзок и вторые учётки иначе не различить.
        self.assertIn("{[roleLabel(u.role), departmentNameOf(u)].filter(Boolean).join(' · ')}", view)
        self.assertIn("return dep != null ? (departmentNameById.get(Number(dep)) || '') : '';", view)
        # Список обрезан — человеку сказано, что это не все.
        self.assertIn("{headCandidatesTotal > headCandidates.length && (", view)
        self.assertIn("{headCandidates.length === 0 ? (", view)


class FirstHeadedDepartmentTests(unittest.TestCase):
    """«Первый» отдел главы нескольких отделов — тот, в котором она числится.

    На него опирается всё, что работает с одним отделом главы (отдел нового
    сотрудника по умолчанию, её группы, учёт часов). Пока первым шёл отдел с
    названием раньше по алфавиту, назначение главой ещё одного отдела могло
    молча переключить основной: «Удаленный КЦ» стоит раньше «Фронт офисов».

    Настоящий SQL обоих методов database.py исполняется на SQLite (тот же текст,
    плейсхолдеры %s → ?); на PostgreSQL 18 запросы сверены вручную 08.10.2026.
    """

    @classmethod
    def setUpClass(cls):
        import contextlib
        import sqlite3
        import textwrap

        database_source = source_cache.read(DATABASE_PATH)
        lines = database_source.splitlines()
        body = next(node.body for node in source_cache.tree(DATABASE_PATH).body
                    if isinstance(node, ast.ClassDef) and node.name == 'Database')
        wanted = ('_HEADED_DEPARTMENTS_ORDER_SQL', 'headed_department_id_for_user',
                  'get_headed_departments_for_user')
        chunks = []
        for node in body:
            name = getattr(node, 'name', None) or next(
                (target.id for target in getattr(node, 'targets', []) if isinstance(target, ast.Name)), None)
            if name in wanted:
                chunks.append(textwrap.dedent('\n'.join(lines[node.lineno - 1:node.end_lineno])))
        assert len(chunks) == len(wanted), 'в database.py нет: %s' % (wanted,)
        namespace = {}
        exec('\n\n'.join(chunks), namespace)
        cls.sources = dict(zip(wanted, chunks))

        connection = sqlite3.connect(':memory:')
        connection.executescript("""
            CREATE TABLE users (id integer PRIMARY KEY, department_id integer);
            CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                                      is_active boolean, head_user_id integer);
            INSERT INTO users VALUES (1, 1), (2, 909), (3, 367), (4, NULL), (5, 2008), (6, 1);
            INSERT INTO departments VALUES
                (1, 'СЗоВ — Служба заботы о водителях', 'szov', TRUE, 1),
                (1954, 'Удаленный КЦ', 'remote_cc', TRUE, 1),
                (2010, 'А-отключённый', 'off', FALSE, 1),
                (909, 'Фронт офисы', 'front_office', TRUE, 2),
                (1955, 'Удаленный КЦ 2', 'remote_cc2', NULL, 2),
                (560, 'Тез КЦ', 'tez', TRUE, 3),
                (561, 'Аналитика', 'analytik', TRUE, 3),
                (562, 'Яндекс', NULL, TRUE, 4),
                (563, 'Бухгалтерия', 'accounting', TRUE, 4),
                (2008, 'ООЗ', 'request_processing_department', TRUE, 5),
                (2009, 'Архив', 'old', FALSE, 5);
        """)

        class _Cursor:
            def __init__(self):
                self.cursor = connection.cursor()

            def execute(self, sql, params=()):
                self.cursor.execute(sql.replace('%s', '?'), params)

            def fetchone(self):
                return self.cursor.fetchone()

            def fetchall(self):
                return self.cursor.fetchall()

        class _Db:
            _HEADED_DEPARTMENTS_ORDER_SQL = namespace['_HEADED_DEPARTMENTS_ORDER_SQL']
            headed_department_id_for_user = namespace['headed_department_id_for_user']
            get_headed_departments_for_user = namespace['get_headed_departments_for_user']

            @contextlib.contextmanager
            def _get_cursor(self):
                yield _Cursor()

        cls.db = _Db()

    def _ids(self, user_id):
        return [item['id'] for item in self.db.get_headed_departments_for_user(user_id)]

    def test_own_department_goes_first_whatever_the_names_are(self):
        # Числится в СЗоВ, возглавляет СЗоВ и удалённый КЦ.
        self.assertEqual(self.db.headed_department_id_for_user(1), SZOV)
        self.assertEqual(self._ids(1), [SZOV, REMOTE])
        # Свой отдел по алфавиту ПОСЛЕ второго («Фронт офисы» против «Удаленный
        # КЦ 2») — всё равно первый: по названию первым был бы 1955.
        self.assertEqual(self.db.headed_department_id_for_user(2), 909)
        self.assertEqual(self._ids(2), [909, 1955])

    def test_without_an_own_department_among_them_the_name_decides(self):
        # Возглавляет отделы, в которых не числится, и вовсе без отдела — как раньше.
        self.assertEqual(self.db.headed_department_id_for_user(3), 561)
        self.assertEqual(self._ids(3), [561, 560])
        self.assertEqual(self.db.headed_department_id_for_user(4), 563)
        self.assertEqual(self._ids(4), [563, 562])

    def test_disabled_departments_and_strangers_are_left_out(self):
        self.assertEqual(self._ids(5), [2008])
        self.assertEqual(self.db.headed_department_id_for_user(5), 2008)
        self.assertNotIn(2010, self._ids(1))
        for nobody in (6, 999, None, 0):
            self.assertIsNone(self.db.headed_department_id_for_user(nobody), nobody)
            self.assertEqual(self.db.get_headed_departments_for_user(nobody), [], nobody)

    def test_the_first_of_the_list_is_the_single_answer(self):
        """Профиль отдаёт и список, и «первый» отдел — разойтись им нельзя: по
        первому сервер заводит сотрудника, когда отдел в карточке не выбран."""
        for user_id in (1, 2, 3, 4, 5):
            self.assertEqual(self._ids(user_id)[0], self.db.headed_department_id_for_user(user_id), user_id)
        self.assertEqual(self.db.get_headed_departments_for_user(4)[1],
                         {'id': 562, 'name': 'Яндекс', 'code': ''})

    def test_both_queries_share_one_order(self):
        for name in ('headed_department_id_for_user', 'get_headed_departments_for_user'):
            source = self.sources[name]
            self.assertIn('ORDER BY """ + self._HEADED_DEPARTMENTS_ORDER_SQL + """', source, name)
            self.assertIn('(user_id, user_id))', source, name)
            # Отключённый отдел прав главы не даёт — как и было.
            self.assertIn('COALESCE(is_active, TRUE) = TRUE', source, name)
        self.assertIn('DESC NULLS LAST, name, id', self.sources['_HEADED_DEPARTMENTS_ORDER_SQL'])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Глава нескольких отделов выбирает, в какой завести сотрудника
# ─────────────────────────────────────────────────────────────────────────────

class HeadedDepartmentChoiceTests(unittest.TestCase):

    def _choice(self, headed_ids):
        namespace = {'_headed_department_ids': lambda _requester_id: frozenset(headed_ids)}
        exec(_real('_headed_department_choice'), namespace)
        return namespace['_headed_department_choice']

    def test_only_a_headed_department_is_a_choice(self):
        choice = self._choice({SZOV, REMOTE})
        self.assertEqual(choice(300, REMOTE), REMOTE)
        self.assertEqual(choice(300, str(REMOTE)), REMOTE)
        self.assertEqual(choice(300, ' %d ' % REMOTE), REMOTE)
        self.assertEqual(choice(300, SZOV), SZOV)
        # Чужой отдел, пустое значение и мусор — «выбора нет».
        for raw in (SALES, str(SALES), None, '', '  ', 'abc', '1954a', [REMOTE], {'id': REMOTE},
                    0, -REMOTE, 10 ** 12):
            self.assertIsNone(choice(300, raw), repr(raw))

    def test_true_and_floats_are_not_department_ids(self):
        # int(True) == 1, а отдел с id 1 существует — СЗоВ: «department_id: true»
        # не должен превращаться в выбор отдела.
        choice = self._choice({SZOV, REMOTE})
        for raw in (True, False, 1.0, float(REMOTE), '1.0', '1954.0'):
            self.assertIsNone(choice(300, raw), repr(raw))

    def test_nobody_else_gets_a_choice(self):
        # Не глава — возглавляемых отделов нет, и названный отдел ничего не даёт.
        choice = self._choice(())
        for raw in (SZOV, REMOTE, str(REMOTE)):
            self.assertIsNone(choice(303, raw), repr(raw))


_ADD_USER_REAL = _ROLE_HELPERS + (
    'KZ_PHONE_REGEX', '_is_valid_kz_phone', 'SENSITIVE_ACCESS_ROLE_LABELS',
    'OPERATOR_FIELDS_HIDDEN_DEPARTMENT_CODES', 'EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENT_CODES',
    'EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENT_CODES', 'EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENT_CODES',
    'BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE', 'BACK_OFFICE_EMPLOYEE_ROLES',
    '_back_office_employee_role', '_department_hides_operator_line_fields',
    '_department_hides_employee_direction', '_department_has_optional_employee_direction',
    '_department_hides_employee_sip_input', '_headed_department_choice',
)


class _UsersDb:
    def __init__(self, requester):
        self.requester = requester
        self.created = []
        self.memberships = []
        self.department_lookups = 0

    def headed_department_id_for_user(self, _user_id):
        return self.requester.get('headed')

    def get_user_department_id(self, user_id):
        return self.requester.get('department') if int(user_id) == self.requester['id'] else None

    def get_department_by_id(self, department_id):
        self.department_lookups += 1
        return DEPARTMENTS.get(int(department_id))

    def get_group(self, group_id):
        return GROUPS.get(int(group_id))

    def get_group_active_supervisor_id(self, _group_id):
        return None

    def get_direction_department_id(self, direction_id):
        return DIRECTIONS.get(int(direction_id))

    def get_directions(self):
        return [{'id': 70, 'name': 'Основа'}]

    def create_user(self, **fields):
        self.created.append(fields)
        return 900 + len(self.created)

    def add_operator_to_group(self, group_id, user_id, **options):
        self.memberships.append((group_id, user_id, options))

    def update_user(self, *args, **kwargs):
        return True


def _add_user(payload, requester):
    db = _UsersDb(requester)
    errors = []
    namespace = {
        're': re, 'datetime': datetime, 'uuid': uuid,
        'logging': SimpleNamespace(error=errors.append, warning=errors.append, info=lambda *_: None),
        'db': db,
        'request': SimpleNamespace(get_json=lambda *args, **kwargs: dict(payload)),
        'jsonify': lambda body: body,
        '_get_authenticated_requester': lambda: (requester['id'], _requester_row(requester), None),
        '_is_employee_accounting_manager': lambda _requester_id: False,
        '_headed_department_id': lambda _requester_id: requester.get('headed'),
        '_headed_department_ids': lambda _requester_id: frozenset(requester.get('headed_ids') or ()),
    }
    exec(_proxy_status_source(), namespace)
    exec(_real(*_ADD_USER_REAL), namespace)
    exec(_endpoint("add_user"), namespace)
    status, body = _answer(namespace["add_user"]())
    if status == 500:
        raise AssertionError('ручка упала: %s' % errors)
    return status, body, db


def _proxy_status_source():
    """Единственное имя ручки из database.py — чистая функция, берём её как есть."""
    lines = source_cache.read(DATABASE_PATH).splitlines(keepends=True)
    node = source_cache.function_node(DATABASE_PATH, "normalize_proxy_status_value")
    return "".join(lines[node.lineno - 1:node.end_lineno])


def _operator(**fields):
    payload = {'name': 'Новый Сотрудник', 'role': 'operator', 'hire_date': '2026-10-08', 'rate': 1.0}
    payload.update(fields)
    return payload


def _remote_operator(**fields):
    return _operator(**{'department_id': REMOTE, 'group_id': 90, 'direction_id': 900, **fields})


class AddUserDepartmentChoiceTests(unittest.TestCase):

    def test_head_of_two_departments_creates_in_the_chosen_one(self):
        for department_id in (REMOTE, str(REMOTE)):
            with self.subTest(department_id=department_id):
                status, body, db = _add_user(_remote_operator(department_id=department_id), TWO_HEAD)

                self.assertEqual((status, body.get('status')), (200, 'success'), body)
                self.assertEqual(db.created[0]['department_id'], REMOTE)
                self.assertEqual(db.memberships[0][:2], (90, 901))

    def test_without_a_choice_the_first_headed_department_stays(self):
        # Отдел не назван — сотрудник уходит в отдел по умолчанию, как до выбора.
        for missing in ({}, {'department_id': None}, {'department_id': ''}):
            with self.subTest(payload=missing):
                status, body, db = _add_user(_operator(group_id=10, direction_id=70, **missing), TWO_HEAD)

                self.assertEqual(status, 200, body)
                self.assertEqual(db.created[0]['department_id'], SZOV)

    def test_own_first_department_can_be_named_as_well(self):
        status, body, db = _add_user(_operator(department_id=SZOV, group_id=10, direction_id=70), TWO_HEAD)

        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['department_id'], SZOV)

    def test_a_department_she_does_not_head_is_not_a_choice(self):
        # Отдел продаж она не возглавляет: выбор игнорируется, и группа отдела
        # продаж тут же не проходит — «не принадлежит выбранному отделу».
        status, body, db = _add_user(_operator(department_id=SALES, group_id=30, direction_id=71), TWO_HEAD)

        self.assertEqual((status, body), (400, {"error": "Группа не принадлежит выбранному отделу"}))
        self.assertEqual(db.created, [])
        # А с группой своего отдела человек заводится в нём, не в названном чужом.
        status, body, db = _add_user(_operator(department_id=SALES, group_id=10, direction_id=70), TWO_HEAD)
        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['department_id'], SZOV)

    def test_garbage_instead_of_a_department_is_not_a_choice(self):
        for raw in (True, 1954.0, 'abc', [REMOTE]):
            with self.subTest(department_id=raw):
                status, body, db = _add_user(
                    _operator(department_id=raw, group_id=10, direction_id=70), TWO_HEAD)
                self.assertEqual(status, 200, body)
                self.assertEqual(db.created[0]['department_id'], SZOV)

    def test_group_and_direction_must_belong_to_the_chosen_department(self):
        status, body, db = _add_user(_remote_operator(group_id=10), TWO_HEAD)
        self.assertEqual((status, body), (400, {"error": "Группа не принадлежит выбранному отделу"}))
        status, body, db = _add_user(_remote_operator(direction_id=70), TWO_HEAD)
        self.assertEqual((status, body), (400, {"error": "Направление не принадлежит выбранному отделу"}))
        self.assertEqual(db.created, [])

    def test_head_of_one_department_gets_no_choice(self):
        # Глава одного отдела — как раньше: выбор клиента игнорируем. В том числе
        # глава, которая в своём отделе не числится: её отдел — возглавляемый.
        status, body, db = _add_user(_operator(department_id=REMOTE, group_id=10, direction_id=70), SZOV_HEAD)
        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['department_id'], SZOV)

        status, body, db = _add_user(_operator(department_id=SALES, group_id=90, direction_id=900), OUTSIDE_HEAD)
        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['department_id'], REMOTE)

    def test_supervisor_gets_no_choice(self):
        status, body, db = _add_user(_operator(department_id=REMOTE, group_id=10, direction_id=70), SZOV_SV)

        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['department_id'], SZOV)

    def test_supervisor_heading_another_department_is_not_its_default_supervisor(self):
        """Супервайзер числится в отделе продаж, а возглавляет удалённый КЦ.
        Себя сотруднику чужого для него отдела он не подставляет — иначе стажёра
        без группы там было бы не завести: «Супервайзер не из выбранного отдела»."""
        trainee = {'name': 'Новый Стажёр', 'role': 'trainee', 'hire_date': '2026-10-08'}
        status, body, db = _add_user(trainee, OUTSIDE_HEAD)

        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (REMOTE, None))

        # Обычный супервайзер и супервайзер-глава своего отдела — как раньше: сами.
        status, body, db = _add_user(trainee, SZOV_SV)
        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (SZOV, SZOV_SV['id']))

        own_head = dict(SZOV_SV, id=305, headed=SZOV, headed_ids=(SZOV,))
        status, body, db = _add_user(trainee, own_head)
        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (SZOV, 305))

        # Супервайзер без отдела — тоже как раньше.
        homeless = dict(SZOV_SV, id=306, department=None)
        status, body, db = _add_user(trainee, homeless)
        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (None, 306))

        # …и когда он при этом возглавляет отдел: своего отдела у него нет,
        # сравнивать не с чем — подставляет себя, как до правки.
        homeless_head = dict(OUTSIDE_HEAD, id=307, department=None)
        status, body, db = _add_user(trainee, homeless_head)
        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (REMOTE, 307))

    def test_supervisor_heading_two_departments_chooses_like_any_head(self):
        # Выбор отдела — по главенству, а не по должности: супервайзер во главе
        # двух отделов заводит стажёра во втором и себя ему не подставляет.
        sv_two_head = dict(TWO_HEAD, id=308, role='sv')
        trainee = {'name': 'Новый Стажёр', 'role': 'trainee', 'hire_date': '2026-10-08', 'department_id': REMOTE}
        status, body, db = _add_user(trainee, sv_two_head)

        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (REMOTE, None))
        # В своём первом отделе — сам, как любой супервайзер.
        status, body, db = _add_user(dict(trainee, department_id=None), sv_two_head)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['supervisor_id']), (SZOV, 308))

    def test_global_admin_still_chooses_any_department(self):
        for requester in (SUPER_ADMIN, PLAIN_ADMIN):
            with self.subTest(requester=requester['id']):
                status, body, db = _add_user(_remote_operator(), requester)
                self.assertEqual(status, 200, body)
                self.assertEqual(db.created[0]['department_id'], REMOTE)

    def test_choice_is_read_once_and_only_for_the_scoped_requester(self):
        endpoint = _function_source("add_user")
        self.assertEqual(1, endpoint.count("_headed_department_choice("))
        scoped_at = endpoint.index("department_id = requester_dept_id")
        self.assertLess(scoped_at, endpoint.index("_headed_department_choice("))
        # Выбор разбирается ДО правил отдела (направление, поля линии, роль).
        self.assertLess(endpoint.index("_headed_department_choice("),
                        endpoint.index("line_fields_hidden = _department_hides_operator_line_fields(department_id)"))


class RemoteCcSipTests(unittest.TestCase):
    """«Без sip номера, если это удалённый КЦ»: номер у отдела — линия Binotel,
    её выдают в разделе «Удаленный КЦ», а не вписывают в карточку."""

    def test_remote_cc_employee_is_created_without_a_sip_number(self):
        for requester in (TWO_HEAD, OUTSIDE_HEAD, SUPER_ADMIN, PLAIN_ADMIN):
            with self.subTest(requester=requester['id']):
                status, body, db = _add_user(_remote_operator(sip_number='904'), requester)
                self.assertEqual(status, 200, body)
                self.assertEqual(db.created[0]['department_id'], REMOTE)
                self.assertIsNone(db.created[0]['sip_number'])

    def test_department_taken_from_the_group_counts_too(self):
        # Админ отдел не выбрал — его определила группа; номер всё равно не пишем.
        status, body, db = _add_user(_operator(group_id=90, direction_id=900, sip_number='904'), SUPER_ADMIN)

        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['department_id'], REMOTE)
        self.assertIsNone(db.created[0]['sip_number'])

    def test_line_departments_keep_the_number(self):
        for requester, payload in (
            (TWO_HEAD, _operator(group_id=10, direction_id=70, sip_number=' 1234 ')),
            (SUPER_ADMIN, _operator(department_id=SALES, group_id=30, direction_id=71, sip_number='1234')),
            (SUPER_ADMIN, _operator(group_id=10, direction_id=70, sip_number='1234')),
        ):
            with self.subTest(requester=requester['id'], payload=payload):
                status, body, db = _add_user(payload, requester)
                self.assertEqual(status, 200, body)
                self.assertEqual(db.created[0]['sip_number'], '1234')

    def test_request_without_a_number_does_not_ask_the_database(self):
        _status, _body, with_number = _add_user(_remote_operator(sip_number='904'), SUPER_ADMIN)
        _status, _body, without = _add_user(_remote_operator(), SUPER_ADMIN)

        self.assertEqual(with_number.department_lookups, without.department_lookups + 1)

    def test_backend_set_mirrors_the_frontend(self):
        server = set(_module_literal('EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENT_CODES'))
        front = re.search(r"const EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENTS = new Set\(\[([^\]]*)\]\);",
                          source_cache.read(VIEWS_PATH))
        self.assertIsNotNone(front, 'набор пропал из departmentViews.js')
        self.assertEqual(server, set(re.findall(r"'([a-z_]+)'", front.group(1))))
        self.assertEqual(server, {'request_processing_department', 'remote_cc'})

    def test_helper_runtime(self):
        class _Departments:
            def get_department_by_id(self, department_id):
                if department_id == 13:
                    raise RuntimeError("база недоступна")
                return DEPARTMENTS.get(department_id)

        namespace = {'db': _Departments()}
        exec(_real('EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENT_CODES', '_department_hides_employee_sip_input'), namespace)
        hides = namespace['_department_hides_employee_sip_input']

        self.assertTrue(hides(REMOTE))
        self.assertTrue(hides(str(REMOTE)), 'id строкой из JSON')
        self.assertTrue(hides(OOZ))
        for department_id in (SZOV, SALES):
            self.assertFalse(hides(department_id), DEPARTMENTS[department_id]['code'])
        # Отдел неизвестен или база не ответила — номер не отбираем.
        for unknown in (None, 999999, 'не число', 13):
            self.assertFalse(hides(unknown), repr(unknown))

    def test_number_is_dropped_after_the_department_is_final(self):
        endpoint = _function_source("add_user")
        self.assertEqual(1, endpoint.count("_department_hides_employee_sip_input("))
        # Ниже этого места группа отдел уже не уточняет.
        self.assertLess(endpoint.index("department_id = int(group_dept)"),
                        endpoint.index("_department_hides_employee_sip_input("))
        self.assertLess(endpoint.index("_department_hides_employee_sip_input("),
                        endpoint.index("user_id = db.create_user("))


class GroupsOfTheChosenDepartmentTests(unittest.TestCase):
    """Карточке нужны группы того отдела, который выбрала глава."""

    def _groups(self, requester, department_id_arg=None, include_archived=False):
        asked = []

        class _Db:
            def list_groups(self, include_archived=False, department_id=None):
                asked.append((include_archived, department_id))
                return [dict(group) for group in GROUPS.values()
                        if department_id is None or group['department_id'] == department_id]

            def get_user_department_id(self, _user_id):
                return requester.get('department')

            def get_supervisor_group_ids(self, _user_id):
                return []

        namespace = {
            'db': _Db(),
            'jsonify': lambda body: body,
            '_headed_department_id': lambda _requester_id: requester.get('headed'),
            '_headed_department_ids': lambda _requester_id: frozenset(requester.get('headed_ids') or ()),
            '_is_employee_accounting_manager': lambda _requester_id: False,
            '_is_marketing_observer': lambda _requester_id, _role: False,
            '_is_supervisor_role': lambda role: str(role) == 'sv',
        }
        exec(_real(*_ROLE_HELPERS, '_headed_department_choice', '_scoped_groups_for_requester'), namespace)
        groups, error = namespace['_scoped_groups_for_requester'](
            requester['id'], requester['role'], requester.get('headed'),
            include_archived=include_archived, department_id_arg=department_id_arg)
        return groups, error, asked

    def test_head_of_two_departments_gets_groups_of_the_named_one(self):
        for raw in (REMOTE, str(REMOTE)):
            with self.subTest(department_id=raw):
                groups, error, asked = self._groups(TWO_HEAD, raw)
                self.assertIsNone(error)
                self.assertEqual(asked, [(False, REMOTE)])
                self.assertEqual([group['id'] for group in groups], [90])
        # Архивные — по тому же флагу, что и у отдела по умолчанию.
        _groups, _error, asked = self._groups(TWO_HEAD, REMOTE, include_archived=True)
        self.assertEqual(asked, [(True, REMOTE)])
        _groups, _error, asked = self._groups(TWO_HEAD, None, include_archived=True)
        self.assertEqual(asked, [(True, SZOV)])
        # Роль главы значения не имеет: супервайзер во главе двух отделов — так же.
        _groups, _error, asked = self._groups(dict(TWO_HEAD, role='sv'), REMOTE)
        self.assertEqual(asked, [(False, REMOTE)])

    def test_without_a_name_or_with_a_foreign_one_the_default_stays(self):
        for raw in (None, '', str(SALES), 'abc'):
            with self.subTest(department_id=raw):
                groups, _error, asked = self._groups(TWO_HEAD, raw)
                self.assertEqual(asked, [(False, SZOV)])
                self.assertEqual([group['id'] for group in groups], [10])

    def test_head_of_one_department_cannot_reach_other_groups(self):
        for requester in (SZOV_HEAD, OUTSIDE_HEAD):
            with self.subTest(requester=requester['id']):
                _groups, _error, asked = self._groups(requester, str(SALES))
                self.assertEqual(asked, [(False, requester['headed'])])

    def test_supervisor_and_admin_rules_are_untouched(self):
        _groups, _error, asked = self._groups(SZOV_SV, str(REMOTE))
        self.assertEqual(asked, [(False, SZOV)])
        _groups, _error, asked = self._groups(PLAIN_ADMIN, str(REMOTE))
        self.assertEqual(asked, [(False, REMOTE)])
        _groups, _error, asked = self._groups(PLAIN_ADMIN, None, include_archived=True)
        self.assertEqual(asked, [(True, None)])
        groups, error, _asked = self._groups(OPERATOR)
        self.assertEqual(groups, [])
        self.assertEqual(error[1], 403)

    def _scope(self, requester, group_id):
        class _Db:
            def headed_department_id_for_user(self, _user_id):
                return requester.get('headed')

            def get_group(self, group_id):
                return GROUPS.get(int(group_id))

        namespace = {
            'db': _Db(),
            'jsonify': lambda body: body,
            '_headed_department_id': lambda _requester_id: requester.get('headed'),
            '_headed_department_ids': lambda _requester_id: frozenset(requester.get('headed_ids') or ()),
            '_is_employee_accounting_manager': lambda _requester_id: False,
        }
        exec(_real(*_ROLE_HELPERS, '_ensure_group_in_requester_scope'), namespace)
        return namespace['_ensure_group_in_requester_scope'](group_id, requester['id'], requester['role'])

    def test_group_of_any_headed_department_is_hers_to_manage(self):
        # Карточка предлагает главе группы каждого её отдела — перевод обязан пройти.
        self.assertIsNone(self._scope(TWO_HEAD, 10))
        self.assertIsNone(self._scope(TWO_HEAD, 90))
        self.assertIsNone(self._scope(OUTSIDE_HEAD, 90))
        self.assertIsNone(self._scope(SUPER_ADMIN, 30))
        for requester, group_id in ((TWO_HEAD, 30), (TWO_HEAD, 424242), (SZOV_HEAD, 90),
                                    (OUTSIDE_HEAD, 30), (SZOV_SV, 10), (OPERATOR, 10)):
            with self.subTest(requester=requester['id'], group=group_id):
                refusal = self._scope(requester, group_id)
                self.assertIsNotNone(refusal)
                self.assertEqual(refusal[1], 403)


class BulkGroupMoveKeepsDepartmentsApartTests(unittest.TestCase):
    """Глава двух отделов видит в «Учете сотрудников» людей обоих, а группы в
    панели массового перевода — одного, первого. Сотрудника второго отдела такой
    перевод увёл бы в группу, к супервайзеру и направлению чужого отдела."""

    def _bulk(self, requester, user_ids, group_id, personnel_manager=False):
        users = {
            20: (20, None, 'Оператор СЗоВ', 'operator', None, None, None),
            21: (21, None, 'Оператор удалённого КЦ', 'operator', None, None, None),
            22: (22, None, 'Стажёр удалённого КЦ', 'trainee', None, None, None),
            23: (23, None, 'Оператор продаж', 'operator', None, None, None),
        }
        departments = {20: SZOV, 21: REMOTE, 22: REMOTE, 23: SALES, requester['id']: requester.get('department')}
        moves = []

        class _Db:
            def get_user(self, id):  # noqa: A002 — так зовёт ручка
                return users.get(int(id))

            def get_user_department_id(self, user_id):
                return departments.get(int(user_id))

            def get_group(self, group_id):
                return GROUPS.get(int(group_id))

            def update_user(self, *args, **kwargs):
                return True

            def add_operator_to_group(self, group_id, operator_id, assigned_by=None, sync_direction=True):
                moves.append((int(group_id), int(operator_id), assigned_by))

        namespace = {
            'db': _Db(),
            'request': SimpleNamespace(get_json=lambda *a, **k: {'user_ids': list(user_ids),
                                                                 'changes': {'group_id': group_id}}),
            'jsonify': lambda body: body,
            'logging': logging,
            '_get_authenticated_requester': lambda: (requester['id'], _requester_row(requester), None),
            '_headed_department_id': lambda _requester_id: requester.get('headed'),
            '_headed_department_ids': lambda _requester_id: frozenset(requester.get('headed_ids') or ()),
            '_is_employee_accounting_manager': lambda _requester_id: personnel_manager,
            '_is_supervisor_role': lambda role: str(role) == 'sv',
            '_department_scope_id_for_requester': lambda _requester_id: requester.get('headed')
            or requester.get('department'),
            '_is_supervisor_rate_change_day': lambda: True,
        }
        exec(_real(*_ROLE_HELPERS, '_target_user_supervisor_id', '_requester_can_access_target_user',
                   '_validate_scoped_user_relation_update'), namespace)
        exec(_endpoint("admin_bulk_update_users"), namespace)
        status, body = _answer(namespace["admin_bulk_update_users"]())
        return status, body, moves

    def test_employee_of_her_other_department_is_not_moved_into_the_group(self):
        status, body, moves = self._bulk(TWO_HEAD, [20, 21, 22], 10)

        self.assertEqual(status, 200, body)
        self.assertEqual((body['updated_count'], body['failed_user_ids']), (1, [21, 22]))
        self.assertEqual(moves, [(10, 20, TWO_HEAD['id'])])

    def test_group_of_her_other_department_is_still_refused_in_bulk(self):
        # Панель массового перевода держится первого отдела — так было и до правки;
        # в группу второго отдела человека переводят в его карточке.
        status, body, moves = self._bulk(TWO_HEAD, [21], 90)

        self.assertEqual((status, body), (403, {"error": "Группа не из вашего отдела"}))
        self.assertEqual(moves, [])

    def test_head_of_one_department_moves_her_people_as_before(self):
        status, body, moves = self._bulk(SZOV_HEAD, [20], 10)
        self.assertEqual((status, body['updated_count'], body['failed_user_ids']), (200, 1, []))
        self.assertEqual(moves, [(10, 20, SZOV_HEAD['id'])])

        status, body, moves = self._bulk(OUTSIDE_HEAD, [21, 22], 90)
        self.assertEqual((status, body['updated_count'], body['failed_user_ids']), (200, 2, []))
        self.assertEqual(moves, [(90, 21, OUTSIDE_HEAD['id']), (90, 22, OUTSIDE_HEAD['id'])])

    def test_global_admin_and_personnel_are_not_bound_by_departments(self):
        # Глобальный админ и кадровик переводят людей между отделами — как раньше.
        for requester, personnel_manager in ((SUPER_ADMIN, False), (PLAIN_ADMIN, False), (OPERATOR, True)):
            with self.subTest(requester=requester['id']):
                status, body, moves = self._bulk(requester, [20, 21, 23], 10, personnel_manager=personnel_manager)
                self.assertEqual((status, body['updated_count'], body['failed_user_ids']), (200, 3, []))
                self.assertEqual([move[:2] for move in moves], [(10, 20), (10, 21), (10, 23)])


class ProfileCarriesHeadedDepartmentsTests(unittest.TestCase):
    """Карточка берёт список возглавляемых отделов из профиля."""

    def _payload(self, headed, *, fail=False, first_headed=None):
        class _Db:
            def get_user_department(self, _user_id):
                return (SZOV, 'szov')

            def headed_department_id_for_user(self, _user_id):
                if first_headed is not None:
                    return first_headed
                return headed[0]['id'] if headed else None

            def department_wiki_enabled(self, _department_id):
                return True

            def get_headed_departments_for_user(self, _user_id):
                if fail:
                    raise RuntimeError('база недоступна')
                return [dict(item) for item in headed]

            def department_has_wiki_space(self, _department_ids, user_id=None):
                return True

            def get_user_direction_model(self, _user_id):
                return None

        namespace = {
            'db': _Db(),
            'logging': logging,
            '_build_avatar_signed_url': lambda _bucket, _path: None,
            '_dial_list_line_member': lambda _user_id: None,
            '_baiga_section_open_for': lambda _user_id, _profile=None: False,
        }
        exec(_endpoint("_get_user_payload"), namespace)
        return namespace["_get_user_payload"]({'id': 300, 'role': 'admin', 'name': 'Руководитель'})

    def test_list_keeps_the_order_and_the_first_is_the_default(self):
        payload = self._payload([
            {'id': SZOV, 'name': 'СЗоВ', 'code': 'SZOV'},
            {'id': REMOTE, 'name': 'Удаленный КЦ', 'code': 'remote_cc'},
        ])

        self.assertEqual(payload['headed_departments'], [
            {'id': SZOV, 'name': 'СЗоВ', 'code': 'szov'},
            {'id': REMOTE, 'name': 'Удаленный КЦ', 'code': 'remote_cc'},
        ])
        self.assertEqual(payload['headed_department_id'], payload['headed_departments'][0]['id'])
        self.assertEqual(payload['headed_department_ids'], [SZOV, REMOTE])
        self.assertEqual(payload['headed_department_codes'], ['szov', 'remote_cc'])
        json.dumps(payload['headed_departments'])

    def test_department_without_a_code_stays_in_the_list(self):
        # В headed_department_codes такой отдел не попадает (и массивы там
        # расходятся по длине) — здесь у каждого отдела своя строка.
        payload = self._payload([{'id': 7, 'name': 'Без кода', 'code': ''},
                                 {'id': REMOTE, 'name': 'Удаленный КЦ', 'code': 'remote_cc'}])

        self.assertEqual(payload['headed_departments'], [
            {'id': 7, 'name': 'Без кода', 'code': None},
            {'id': REMOTE, 'name': 'Удаленный КЦ', 'code': 'remote_cc'},
        ])

    def test_nobody_else_gets_a_list(self):
        self.assertEqual(self._payload([])['headed_departments'], [])
        # Отказ базы не роняет вход: списка просто нет.
        self.assertEqual(self._payload([{'id': SZOV, 'name': 'СЗоВ', 'code': 'szov'}], fail=True)
                         ['headed_departments'], [])

    def test_list_never_disagrees_with_the_ids(self):
        """Сбой посреди разбора (уже после того, как список собран) обнуляет
        его вместе с id отделов: карточка не должна предлагать выбор отдела
        тому, кого остальной портал главой в эту минуту не считает."""
        payload = self._payload([{'id': SZOV, 'name': 'СЗоВ', 'code': 'szov'},
                                 {'id': REMOTE, 'name': 'Удаленный КЦ', 'code': 'remote_cc'}],
                                first_headed='не число')

        self.assertEqual(payload['headed_department_ids'], [])
        self.assertEqual(payload['headed_department_codes'], [])
        self.assertEqual(payload['headed_departments'], [])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Рядовому сотруднику удалённого КЦ — только «Профиль», «Мои смены», «Вики»
# ─────────────────────────────────────────────────────────────────────────────

ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
         'marketing_manager', 'accounting_manager', 'hr_manager', ' Operator ', 'Trainee', '')
CODES = ('remote_cc', 'Remote_CC', ' remote_cc ', 'szov', 'op', 'tez', 'front_office', 'analytik',
         'request_processing_department', 'remote', 'remote_cc2', 'constructor', '__proto__', '', None)


def _front_department_only_views():
    """Карта из departmentViews.js: {код: {роль: (разделы…)}}."""
    block = re.search(r'const DEPARTMENT_ONLY_VIEWS = \{\n(.*?)\n\};', source_cache.read(VIEWS_PATH), re.S)
    assert block, 'DEPARTMENT_ONLY_VIEWS не найден в departmentViews.js'
    result = {}
    code = None
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        opened = re.fullmatch(r"([a-z_]+): \{", line)
        if opened:
            code = opened.group(1)
            result[code] = {}
            continue
        if line == '},':
            code = None
            continue
        row = re.fullmatch(r"([a-z_]+): \[((?:'[a-z_]+'(?:, )?)*)\],", line)
        assert row and code, 'строка карты не разобрана: %r' % line
        result[code][row.group(1)] = tuple(re.findall(r"'([a-z_]+)'", row.group(2)))
    return result


def _views_namespace(departments, heads=(), personal=None):
    """Настоящий _personal_views_for с подставленными отделом и главенством."""
    calls = []

    class _Db:
        def get_user_department(self, user_id):
            calls.append(user_id)
            value = departments.get(user_id)
            if isinstance(value, Exception):
                raise value
            return (1954 if value else None, value)

    namespace = {
        'db': _Db(),
        'PERSONAL_VIEW_ALLOWLIST': personal if personal is not None else _module_literal('PERSONAL_VIEW_ALLOWLIST'),
        'PERSONAL_VIEW_BASE_ROLES': _module_literal('PERSONAL_VIEW_BASE_ROLES'),
        'DEPARTMENT_ONLY_VIEWS': _module_literal('DEPARTMENT_ONLY_VIEWS'),
        'BACK_OFFICE_EMPLOYEE_ROLES': frozenset(
            _module_literal('BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE').values()),
        '_headed_department_id': lambda requester_id: 1954 if requester_id in heads else None,
    }
    exec(_real('_normalize_user_role', '_personal_views_for'), namespace)
    return namespace, calls


class RemoteCcViewsMirrorTests(unittest.TestCase):

    def test_the_set_is_the_same_on_both_sides(self):
        front = _front_department_only_views()
        server = _module_literal('DEPARTMENT_ONLY_VIEWS')
        self.assertEqual(front, {code: {role: tuple(views) for role, views in by_role.items()}
                                 for code, by_role in server.items()})
        # Ровно то, что назвал владелец: профиль, мои смены и вики. Стажёру — без
        # вики: её запирает QR, а стажёру QR не выдают.
        self.assertEqual(front, {'remote_cc': {
            'operator': ('profile', 'work_schedules', 'wiki'),
            'trainee': ('profile', 'work_schedules'),
        }})

    def test_profile_is_the_default_section(self):
        # firstAllowedView берёт allow[0]: сюда человек попадает после входа.
        for by_role in _front_department_only_views().values():
            for role, views in by_role.items():
                self.assertEqual(views[0], 'profile', role)

    def test_wiki_stays_only_where_the_qr_lock_holds(self):
        """Вики в наборе — только у должности, которую спрашивает замок QR: и
        портал, и сервер. Иначе раздел открывался бы без подтверждения."""
        from wiki import access as wiki_access
        app = source_cache.read(APP_PATH)
        front_gated = re.search(r"const SENSITIVE_QR_GATED_ROLES = new Set\(\[([^\]]*)\]\);", app)
        gated_in_portal = set(re.findall(r"'([a-z_]+)'", front_gated.group(1)))
        for by_role in _front_department_only_views().values():
            for role, views in by_role.items():
                if 'wiki' not in views:
                    continue
                self.assertIn(role, gated_in_portal, role)
                self.assertTrue(wiki_access.requires_sensitive_qr(role), role)
        self.assertFalse(wiki_access.requires_sensitive_qr('trainee'))
        self.assertNotIn('wiki', _front_department_only_views()['remote_cc']['trainee'])

    def test_server_reads_the_set_like_the_frontend(self):
        """personalViewsOf (фронт) и _personal_views_for (сервер) — одно правило
        на сетке отделов, ролей и главенства."""
        cases = [(user_id, code, role, head)
                 for user_id in (700, '700', 540)
                 for code in CODES for role in ROLES for head in (False, True)]
        users = [{'id': user_id, 'role': role, 'department_code': code,
                  'headed_department_id': 1954 if head else None} for user_id, code, role, head in cases]
        front = _run_node('\n'.join((
            "import { personalViewsOf } from '%s';" % VIEWS_PATH.as_uri(),
            'const users = %s;' % json.dumps(users),
            'process.stdout.write(JSON.stringify(users.map((u) => personalViewsOf(u))));',
        )))
        if front is None:
            self.skipTest('node недоступен')
        self.assertEqual(len(front), len(cases))
        restricted = 0
        for (user_id, code, role, head), answer in zip(cases, front):
            namespace, _calls = _views_namespace({int(user_id): code}, heads={user_id} if head else ())
            server = namespace['_personal_views_for'](user_id, role)
            self.assertEqual(answer, list(server) if server is not None else None, (user_id, code, role, head))
            restricted += answer is not None
        # Сетка не вырождена: набор и действует, и снимается.
        self.assertGreater(restricted, 20)
        self.assertLess(restricted, len(cases) // 4)

    def test_server_rule_by_hand(self):
        namespace, calls = _views_namespace({700: 'remote_cc', 701: 'szov', 702: None,
                                             703: RuntimeError('база недоступна'), 540: 'remote_cc'})
        views = namespace['_personal_views_for']

        self.assertEqual(views(700, 'operator'), ('profile', 'work_schedules', 'wiki'))
        self.assertEqual(views('700', ' Operator '), ('profile', 'work_schedules', 'wiki'))
        self.assertEqual(views(700, 'trainee'), ('profile', 'work_schedules'))
        # Роли вне набора отдела и чужие отделы — без набора.
        for role in ('hr_manager', 'accounting_manager', 'marketing_manager'):
            self.assertIsNone(views(700, role), role)
        self.assertIsNone(views(701, 'operator'))
        self.assertIsNone(views(702, 'operator'))
        # База не ответила — набора нет: лишнее уведомление дешевле пропавшего.
        self.assertIsNone(views(703, 'operator'))
        # Личный набор человека сильнее набора его отдела.
        self.assertEqual(views(540, 'operator'), ('baiga',))
        # Набор — только рядовому, не возглавляющему отдел.
        calls.clear()
        for role in ('sv', 'supervisor', 'trainer', 'admin', 'super_admin', '', None):
            self.assertIsNone(views(700, role), role)
        headed, headed_calls = _views_namespace({700: 'remote_cc'}, heads={700})
        for role in ('operator', 'trainee', 'admin'):
            self.assertIsNone(headed['_personal_views_for'](700, role), role)
        # …и отдел у них даже не спрашиваем: колокол собирается без лишнего запроса.
        self.assertEqual(calls, [])
        self.assertEqual(headed_calls, [])
        for garbage in (None, 'abc', '', [700]):
            self.assertIsNone(views(garbage, 'operator'), repr(garbage))

    def test_personal_set_does_not_ask_the_department(self):
        namespace, calls = _views_namespace({540: 'analytik'})
        self.assertEqual(namespace['_personal_views_for'](540, 'operator'), ('baiga',))
        self.assertEqual(calls, [])


class RemoteCcBellTests(unittest.TestCase):
    """Колокол не зовёт сотрудника удалённого КЦ в разделы, которых у него нет."""

    def _viewer(self, requester_id, role, departments, *, heads=(), can_see_tasks=False):
        namespace, _calls = _views_namespace(departments, heads=heads)
        namespace.update({
            '_events_viewer_scope': lambda requester_id, role: (False, REMOTE),
            '_four_you_access_for_requester': lambda requester_id, requester: (False, None),
            '_can_access_tasks': lambda role, requester_id: can_see_tasks,
            '_birthdays_viewer_scope': lambda requester_id, role, **kwargs: (False, REMOTE),
            '_checkpoint_scope_for_requester': lambda requester_id, requester: {},
            '_can_manage_checkpoints': lambda requester_id, requester: False,
            '_shift_change_scope_for_requester': lambda requester_id, requester: {},
        })
        exec(_real('_notifications_viewer_context'), namespace)
        return namespace['_notifications_viewer_context'](requester_id, (requester_id, None, None, role))

    def test_operator_hears_only_about_his_three_sections(self):
        hidden = set(self._viewer(700, 'operator', {700: 'remote_cc'})['hidden_sources'])

        self.assertEqual(hidden, {'tasks', 'checkpoints', 'lms', 'surveys', 'events', 'four_you'})
        # Вики и заявки на смену — его разделы: о них колокол не молчит.
        for name in ('wiki_ack', 'wiki_questions', 'shift_requests'):
            self.assertNotIn(name, hidden)
        self.assertEqual(hidden, {'tasks'} | set(
            sources.sources_outside_views(('profile', 'work_schedules', 'wiki'))))

    def test_trainee_has_no_wiki_in_the_bell_either(self):
        hidden = set(self._viewer(700, 'trainee', {700: 'remote_cc'})['hidden_sources'])

        self.assertIn('wiki_ack', hidden)
        self.assertIn('wiki_questions', hidden)
        self.assertNotIn('shift_requests', hidden)

    def test_head_supervisor_and_other_departments_are_untouched(self):
        self.assertEqual(self._viewer(700, 'operator', {700: 'szov'})['hidden_sources'], ('tasks',))
        self.assertEqual(self._viewer(700, 'sv', {700: 'remote_cc'}, can_see_tasks=True)['hidden_sources'], ())
        self.assertEqual(self._viewer(700, 'operator', {700: 'remote_cc'}, heads={700},
                                      can_see_tasks=True)['hidden_sources'], ())


class EmployeeCardWiringTests(unittest.TestCase):
    """Карточка сотрудника: выбор отдела — только главе нескольких отделов и
    только при создании."""

    @classmethod
    def setUpClass(cls):
        cls.modal = source_cache.read(MODAL_PATH)
        cls.app = source_cache.read(APP_PATH)

    def test_field_opens_only_when_creating_and_only_with_several_departments(self):
        self.assertIn(
            "const requesterHeadedDepartments = (isScopedDepartmentHeadRequester && !isUnscopedRequester)\n"
            "        ? headedDepartmentsOf(user)\n"
            "        : [];", self.modal)
        self.assertIn(
            "const canPickHeadedDepartment = isDeptScoped && !userToEdit?.id"
            " && requesterHeadedDepartments.length > 1;", self.modal)
        # Создание: поле открыто тому, кто может выбирать; правка — заперто, как было.
        self.assertEqual(1, self.modal.count("disabled={fieldsLocked || (isDeptScoped && !canPickHeadedDepartment)}"))
        self.assertEqual(1, self.modal.count("disabled={fieldsLocked || isDeptScoped}"))
        create_at = self.modal.index("disabled={fieldsLocked || (isDeptScoped && !canPickHeadedDepartment)}")
        edit_at = self.modal.index("disabled={fieldsLocked || isDeptScoped}")
        self.assertLess(create_at, self.modal.index("{!isCreateMode && ("))
        self.assertGreater(edit_at, self.modal.index("{!isCreateMode && ("))

    def test_options_are_her_own_departments_in_both_modes(self):
        self.assertEqual(2, self.modal.count("? scopedDepartmentOptions\n"))
        self.assertIn("const scopedDepartmentOptions = (requesterHeadedDepartments.length\n"
                      "        ? requesterHeadedDepartments\n", self.modal)

    def test_fields_follow_the_chosen_department(self):
        # Код выбранного отдела — из профиля главы: справочник ей срезан до одного.
        self.assertIn(
            "        ?? requesterHeadedDepartments.find((d) => d.id === Number(effectiveDeptId))?.code\n"
            "        ?? (isDeptScoped && Number(effectiveDeptId) === Number(requesterScopeDeptId)"
            " ? requesterScopeDeptCode : null);", self.modal)
        self.assertIn(
            "const showSipField = showOperatorLineFields"
            " && !departmentCodeHidesEmployeeSipInput(effectiveDeptCode);", self.modal)
        # Группы — выбранного отдела, а не всех её отделов разом.
        self.assertIn(
            "if (requesterHeadedDepartments.length > 1 && effectiveDeptId != null) {\n"
            "            return active.filter((g) => Number(g?.department_id ?? g?.departmentId)"
            " === Number(effectiveDeptId));", self.modal)

    def test_card_gets_groups_of_every_headed_department(self):
        fetch = self.app.split("const fetchUserModalGroups = async () => {")[1].split(
            "const saveDirections = async")[0]
        # Первый запрос — прежний, без параметра: его список по-прежнему читают
        # массовый перевод и перевод СВ в операторы.
        self.assertIn("const response = await requestGroups();", fetch)
        self.assertIn("setUserModalGroups(data.groups);", fetch)
        # Остальные возглавляемые отделы — отдельными запросами и в свой список.
        self.assertIn(
            "const otherDepartmentIds = (isScopedDepartmentHead && !isEmployeeAccountingManager)\n"
            "                        ? headedDepartmentsOf(user)\n"
            "                            .map((department) => department.id)\n"
            "                            .filter((departmentId) => departmentId !== Number(scopedDepartmentId))\n"
            "                        : [];", fetch)
        self.assertIn("const responses = await Promise.all(otherDepartmentIds.map(requestGroups));", fetch)
        self.assertIn("...(departmentId != null ? { params: { department_id: departmentId } } : {}),", fetch)
        # Не глава нескольких отделов — второго запроса нет, а чужой список чистится.
        self.assertIn("if (!otherDepartmentIds.length) {\n"
                      "                        // Следующему вошедшему в этом же окне чужие группы не достаются.\n"
                      "                        setUserModalOtherDepartmentGroups((previous) => (previous.length ? [] : previous));\n"
                      "                        return;", fetch)
        # Имя параметра — то же, что читает ручка.
        self.assertIn("department_id_arg=request.args.get('department_id'),",
                      _function_source("list_groups_endpoint"))

    def test_other_departments_groups_reach_only_the_card(self):
        """Массовый перевод в «Учете сотрудников» сервер держит в границе первого
        отдела главы — группы остальных её отделов в его список не попадают."""
        # Объявление и одно чтение — в пропсах карточки.
        self.assertEqual(self.app.count("userModalOtherDepartmentGroups"), 2)
        props = self.app.split("const userEditModalProps = {")[1].split("onSave: saveUserChanges,")[0]
        self.assertIn("                groups: userModalGroups,\n", props)
        self.assertIn("                otherDepartmentGroups: userModalOtherDepartmentGroups,\n", props)
        # Карточка складывает оба списка сама и режет по отделу сотрудника.
        self.assertIn("groups: primaryGroups = [], otherDepartmentGroups = [],", self.modal)
        self.assertIn("        const known = new Set(base.map((group) => group?.id));\n"
                      "        return [...base, ...extra.filter((group) => !known.has(group?.id))];\n"
                      "    }, [primaryGroups, otherDepartmentGroups]);", self.modal)
        # Панель массовой правки и перевод СВ в операторы — прежний список.
        self.assertIn("groups: (userModalGroups || []).filter((group) => group?.status !== 'archived')", self.app)
        self.assertIn("const availableBulkGroups = (userModalGroups || []).filter((g) => g?.status !== 'archived');",
                      self.app)

    def test_draft_role_follows_the_department_right_in_the_card(self):
        """«Группа» и «Направление» показываются по должности черновика. Глава,
        у которой первый отдел бэк-офисный, выбрав отдел с линией, осталась бы
        без этих полей, а сервер отказал бы: «Missing required field: direction_id»."""
        change = self.modal.split("const handleDepartmentChange = (deptValue) => {")[1].split(
            "const handleGroupChange")[0]
        self.assertIn(
            "            if (!userToEdit?.id) {\n"
            "                next.role = employeeRoleForDepartmentCode(\n"
            "                    prev?.role,\n"
            "                    (departments || []).find((d) => Number(d.id) === effDept)?.code\n"
            "                        ?? requesterHeadedDepartments.find((d) => d.id === effDept)?.code,\n"
            "                );\n"
            "            }", change)
        self.assertIn("import { employeeRoleForDepartmentCode } from '../../utils/departmentViews';", self.modal)
        # Поля линии — по должности черновика, поэтому пересчёт и нужен.
        self.assertIn("{isOperatorDraft(editedUser) && showOperatorLineFields && (", self.modal)

    def test_role_of_the_new_employee_follows_the_chosen_department(self):
        resolver = self.app.split("const resolveEmployeeRoleForDepartment = useCallback((draft) => {")[1].split(
            "// Подстрочник в карточке дня рождения")[0]
        self.assertIn("?? headedDepartmentsOf(user)\n"
                      "                        .find((d) => d.id === Number(draft?.department_id))?.code;", resolver)
        self.assertIn("}, [departments, user]);", resolver)
        # Выбранный отдел уходит на сервер тем же полем, что у админа.
        self.assertIn("department_id: editedUser.department_id ? Number(editedUser.department_id) : null,",
                      self.app)


if __name__ == "__main__":
    unittest.main()
