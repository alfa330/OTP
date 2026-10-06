# -*- coding: utf-8 -*-
"""У отдела аналитики направление при заведении сотрудника необязательно.

Решение владельца 06.10.2026 («именно у отдела аналитики»). У отдела нет ни одного
направления, а форма и ручка `add_user` требовали выбрать его у каждого оператора:
завести аналитика было нельзя вовсе. Поле в карточке при этом остаётся — это не
случай ООЗ, где его нет: выбранное направление сохраняется и проверяется как обычно,
снята только обязательность.

Ручка здесь исполняется по-настоящему: её текст и текст помощников берутся из
`bot_schedule2.py`, подменены только база, запрос и вход. Имена, которые подменены,
сверяются с модулем — иначе заглушка скрыла бы, что ручка зовёт уже не то.
"""

import ast
import builtins
import copy
import logging
import re
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
MODAL_PATH = ROOT / "src" / "components" / "modals" / "UserEditModal.jsx"

BOT_SOURCE = source_cache.read(BOT_PATH)
BOT_TREE = source_cache.tree(BOT_PATH)
_BOT_LINES = BOT_SOURCE.splitlines(keepends=True)

ANALYTICS, SZOV, SALES, OOZ, HR = 2134, 1, 367, 2008, 1499
DEPARTMENTS = {
    SZOV: {'id': SZOV, 'code': 'szov'},
    SALES: {'id': SALES, 'code': 'op'},
    HR: {'id': HR, 'code': 'hr'},
    OOZ: {'id': OOZ, 'code': 'request_processing_department'},
    ANALYTICS: {'id': ANALYTICS, 'code': 'analytik'},
}
# направление -> отдел; 500 — направление, которое аналитике заведут когда-нибудь
DIRECTIONS = {70: SZOV, 71: SALES, 500: ANALYTICS}
GROUPS = {
    41: {'id': 41, 'department_id': ANALYTICS, 'status': 'active'},
    10: {'id': 10, 'department_id': SZOV, 'status': 'active'},
    40: {'id': 40, 'department_id': OOZ, 'status': 'active'},
}

# Настоящие помощники и наборы ручки — в порядке объявления в модуле.
_REAL_NAMES = {
    '_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role',
    '_is_super_admin_role', '_is_admin_role', '_is_global_admin_requester',
    'KZ_PHONE_REGEX', '_is_valid_kz_phone', 'SENSITIVE_ACCESS_ROLE_LABELS',
    'OPERATOR_FIELDS_HIDDEN_DEPARTMENT_CODES', 'EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENT_CODES',
    'EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENT_CODES',
    'BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE', 'BACK_OFFICE_EMPLOYEE_ROLES',
    '_back_office_employee_role', '_department_hides_operator_line_fields',
    '_department_hides_employee_direction', '_department_has_optional_employee_direction',
}
# Подменяем только то, что ходит в базу, в запрос и во вход.
_STUBBED_FUNCTIONS = ('_get_authenticated_requester', '_is_employee_accounting_manager',
                      '_headed_department_id')


def _segment(node, lines=_BOT_LINES):
    """Исходник оператора по номерам строк: `ast.get_source_segment` на этом
    файле стоит 0,3 с за вызов."""
    chunk = list(lines[node.lineno - 1:node.end_lineno])
    chunk[0] = chunk[0][node.col_offset:]
    return "".join(chunk)


def _is_real(node):
    if isinstance(node, ast.FunctionDef):
        return node.name in _REAL_NAMES
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in _REAL_NAMES for target in node.targets)


_REAL_DEFINITIONS = "\n".join(_segment(node) for node in BOT_TREE.body if _is_real(node))
# Единственное имя ручки из database.py — чистая функция, берём её как есть.
_PROXY_STATUS_SOURCE = _segment(
    source_cache.function_node(DATABASE_PATH, "normalize_proxy_status_value"),
    source_cache.read(DATABASE_PATH).splitlines(keepends=True),
)


def _endpoint_code():
    node = source_cache.function_copy(BOT_PATH, "add_user")
    node.decorator_list = []
    return compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                   str(BOT_PATH), "exec")


_ENDPOINT_CODE = _endpoint_code()


def _free_names(name):
    """Имена, которые функция берёт снаружи: всё, что читается и нигде в ней не
    присваивается."""
    node = source_cache.function_node(BOT_PATH, name)
    stored, loaded = set(), set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            (stored if isinstance(item.ctx, (ast.Store, ast.Del)) else loaded).add(item.id)
        elif isinstance(item, ast.arg):
            stored.add(item.arg)
        elif isinstance(item, ast.ExceptHandler) and item.name:
            stored.add(item.name)
    return {n for n in loaded - stored if not hasattr(builtins, n)}


class _Db:
    """База ручки: отделы, группы и направления — как на проде 06.10.2026."""

    def __init__(self, requester):
        self.requester = requester
        self.created = []
        self.memberships = []
        self.department_lookups = 0

    def headed_department_id_for_user(self, _user_id):
        return self.requester.get('headed')

    def get_user_department_id(self, _user_id):
        return self.requester.get('department')

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
        return [{'id': 70, 'name': 'Основа'}, {'id': 71, 'name': 'Верификатор'}]

    def create_user(self, **fields):
        self.created.append(fields)
        return 900 + len(self.created)

    def add_operator_to_group(self, group_id, user_id, **options):
        self.memberships.append((group_id, user_id, options))

    def update_user(self, *args, **kwargs):
        return True


SUPER_ADMIN = {'id': 1, 'role': 'super_admin', 'department': None, 'headed': None}
ANALYTICS_HEAD = {'id': 229, 'role': 'admin', 'department': ANALYTICS, 'headed': ANALYTICS}
SZOV_HEAD = {'id': 2, 'role': 'admin', 'department': SZOV, 'headed': SZOV}
# СВ со своим направлением «Основа» (id 70, отдел СЗоВ) — ради фолбэка наследования.
ANALYTICS_SV = {'id': 7, 'role': 'sv', 'department': ANALYTICS, 'headed': None, 'direction': 'Основа'}


def _add_user(payload, requester=SUPER_ADMIN):
    """Настоящая ручка на подменённой базе: (код ответа, тело, база)."""
    db = _Db(requester)
    errors = []
    requester_row = (requester['id'], 'Заводящий', 'login', requester['role'],
                     requester.get('direction'))
    namespace = {
        're': re, 'datetime': datetime, 'uuid': uuid,
        'logging': SimpleNamespace(error=errors.append, warning=errors.append, info=lambda *_: None),
        'db': db,
        'request': SimpleNamespace(get_json=lambda *args, **kwargs: dict(payload)),
        'jsonify': lambda body: body,
        '_get_authenticated_requester': lambda: (requester['id'], requester_row, None),
        '_is_employee_accounting_manager': lambda _requester_id: False,
        '_headed_department_id': lambda _requester_id: requester.get('headed'),
    }
    exec(_PROXY_STATUS_SOURCE, namespace)
    exec(_REAL_DEFINITIONS, namespace)
    exec(_ENDPOINT_CODE, namespace)
    result = namespace["add_user"]()
    status, body = (result[1], result[0]) if isinstance(result, tuple) else (200, result)
    if status == 500:
        raise AssertionError(f"ручка упала: {errors}")
    return status, body, db


def _operator(**fields):
    payload = {'name': 'Аналитик Первый', 'role': 'operator', 'hire_date': '2026-10-06', 'rate': 1.0}
    payload.update(fields)
    return payload


class HarnessTests(unittest.TestCase):
    """Обвязка не врёт: ручка получает всё, что ей нужно, и ничего выдуманного."""

    def test_every_outside_name_of_the_endpoint_is_provided(self):
        _status, _body, db = _add_user(_operator(department_id=SZOV, direction_id=70, group_id=10))
        provided = (_REAL_NAMES | set(_STUBBED_FUNCTIONS)
                    | {'db', 'request', 'jsonify', 'datetime', 'uuid', 'logging',
                       'normalize_proxy_status_value', 'app', 'require_api_key'})
        self.assertEqual(_free_names("add_user") - provided, set())
        self.assertEqual(len(db.created), 1)

    def test_stubbed_functions_exist_in_the_module(self):
        for name in _STUBBED_FUNCTIONS:
            with self.subTest(name=name):
                self.assertRegex(BOT_SOURCE, r"(?m)^def %s\(" % re.escape(name))


class AnalyticsCreationTests(unittest.TestCase):
    def test_analyst_is_created_without_a_direction(self):
        for missing in ({}, {'direction_id': None}, {'direction_id': ''}):
            with self.subTest(payload=missing):
                status, body, db = _add_user(_operator(department_id=ANALYTICS, group_id=41, **missing))

                self.assertEqual((status, body.get('status')), (200, 'success'), body)
                (created,) = db.created
                self.assertEqual(
                    (created['role'], created['department_id'], created['direction_id']),
                    ('operator', ANALYTICS, None),
                )
                # Направления из формы нет — оператор получит действующее направление
                # группы, если оно у неё когда-нибудь появится.
                self.assertEqual(db.memberships, [(41, 901, {
                    'start_date': '2026-10-06', 'assigned_by': 1, 'sync_direction': True,
                })])

    def test_chosen_direction_of_the_department_is_saved_as_usual(self):
        status, body, db = _add_user(_operator(department_id=ANALYTICS, group_id=41, direction_id='500'))

        self.assertEqual(status, 200, body)
        self.assertEqual(db.created[0]['direction_id'], 500)
        # выбранное руками направление перевод в группу не перетирает
        self.assertFalse(db.memberships[0][2]['sync_direction'])

    def test_direction_of_another_department_is_still_rejected(self):
        status, body, db = _add_user(_operator(department_id=ANALYTICS, group_id=41, direction_id=70))

        self.assertEqual((status, body), (400, {"error": "Направление не принадлежит выбранному отделу"}))
        self.assertEqual(db.created, [])

    def test_garbage_direction_is_still_rejected(self):
        status, body, db = _add_user(_operator(department_id=ANALYTICS, group_id=41, direction_id='abc'))

        self.assertEqual((status, body), (400, {"error": "Invalid direction_id"}))
        self.assertEqual(db.created, [])

    def test_head_of_analytics_creates_in_own_department_without_a_direction(self):
        # Глава отдела не выбирает отдел: сервер ставит его сам.
        status, body, db = _add_user(_operator(group_id=41), requester=ANALYTICS_HEAD)

        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['direction_id']), (ANALYTICS, None))

    def test_supervisor_does_not_pass_own_direction_to_the_analyst(self):
        # У СВ своё направление — «Основа» из СЗоВ. Унаследуй аналитик его, ручка
        # тут же отказала бы: «Направление не принадлежит выбранному отделу».
        status, body, db = _add_user(_operator(group_id=41), requester=ANALYTICS_SV)

        self.assertEqual(status, 200, body)
        self.assertEqual((db.created[0]['department_id'], db.created[0]['direction_id']), (ANALYTICS, None))


class OtherDepartmentsTests(unittest.TestCase):
    """«Именно у отдела аналитики»: остальным обязательность не снята."""

    def test_line_departments_still_require_a_direction(self):
        for department_id, group_id in ((SZOV, 10), (SALES, None)):
            with self.subTest(department=department_id):
                status, body, db = _add_user(_operator(department_id=department_id, group_id=group_id))

                self.assertEqual((status, body), (400, {"error": "Missing required field: direction_id"}))
                self.assertEqual(db.created, [])

    def test_unknown_department_does_not_lift_the_requirement(self):
        # Отдел не выбран: группа аналитики сама по себе обязательность не снимает.
        status, body, db = _add_user(_operator(group_id=41))

        self.assertEqual((status, body), (400, {"error": "Missing required field: direction_id"}))
        self.assertEqual(db.created, [])

    def test_client_cannot_borrow_the_rule_by_naming_the_department(self):
        # Глава СЗоВ присылает отдел аналитики: сервер его выбор игнорирует.
        status, body, db = _add_user(_operator(department_id=ANALYTICS, group_id=10), requester=SZOV_HEAD)

        self.assertEqual((status, body), (400, {"error": "Missing required field: direction_id"}))
        self.assertEqual(db.created, [])

    def test_departments_without_directions_keep_their_own_rule(self):
        # ООЗ: поля нет вовсе, присланное направление отбрасывается — как и было.
        status, body, db = _add_user(_operator(department_id=OOZ, group_id=40, direction_id=70))
        self.assertEqual(status, 200, body)
        self.assertIsNone(db.created[0]['direction_id'])

        status, body, db = _add_user(_operator(department_id=HR))
        self.assertEqual(status, 200, body)
        self.assertIsNone(db.created[0]['direction_id'])

    def test_usual_creation_does_not_ask_the_database_about_the_rule(self):
        # Отдел спрашиваем, только когда направления в запросе нет.
        _status, _body, with_direction = _add_user(_operator(department_id=SZOV, group_id=10, direction_id=70))
        _status, _body, analyst = _add_user(_operator(department_id=ANALYTICS, group_id=41, direction_id=500))
        _status, _body, without = _add_user(_operator(department_id=ANALYTICS, group_id=41))

        self.assertEqual(analyst.department_lookups, with_direction.department_lookups)
        self.assertEqual(without.department_lookups, with_direction.department_lookups + 1)


class RuleDefinitionTests(unittest.TestCase):
    def test_backend_set_mirrors_the_frontend(self):
        self.assertIn(
            "EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENT_CODES = frozenset({'analytik'})", BOT_SOURCE)
        self.assertIn(
            "const EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENTS = new Set(['analytik']);",
            source_cache.read(VIEWS_PATH),
        )

    def test_helper_runtime(self):
        class _Departments:
            def get_department_by_id(self, department_id):
                if department_id == 13:
                    raise RuntimeError("база недоступна")
                return DEPARTMENTS.get(department_id)

        namespace = {'db': _Departments(), 're': re}
        exec(_REAL_DEFINITIONS, namespace)
        optional = namespace["_department_has_optional_employee_direction"]

        self.assertTrue(optional(ANALYTICS))
        self.assertTrue(optional(str(ANALYTICS)), 'id строкой из JSON')
        for department_id in (SZOV, SALES, HR, OOZ):
            self.assertFalse(optional(department_id), DEPARTMENTS[department_id]['code'])
        # Отдел неизвестен или база не ответила — обязательность не снимаем.
        for unknown in (None, 999999, 'не число', 13):
            self.assertFalse(optional(unknown), repr(unknown))

    def test_endpoint_decides_once_and_before_the_check(self):
        endpoint = _segment(source_cache.function_node(BOT_PATH, "add_user"))
        decided_at = endpoint.index("direction_skipped = direction_hidden or (")
        checked_at = endpoint.index('"error": "Missing required field: direction_id"')

        self.assertLess(decided_at, checked_at)
        self.assertEqual(1, endpoint.count("_department_has_optional_employee_direction("))


class EmployeeCardTests(unittest.TestCase):
    """Карточка: поле на месте, пустым оно может остаться только там, где нечего терять."""

    def setUp(self):
        self.modal = source_cache.read(MODAL_PATH)

    def test_direction_may_stay_empty_only_without_a_stored_one(self):
        self.assertIn(
            "    const directionMayStayEmpty = departmentCodeHasOptionalEmployeeDirection(effectiveDeptCode)\n"
            "        && !userToEdit?.direction_id;\n",
            self.modal,
        )
        self.assertIn(
            "if (isOperatorUser && showDirectionField && !directionMayStayEmpty && !editedUser.direction_id) {\n"
            '        setModalError("Направление обязательно.");',
            self.modal,
        )

    def test_field_stays_in_the_card(self):
        # Необязательное — не скрытое: показ поля от нового признака не зависит.
        self.assertIn(
            "const showDirectionField = showOperatorLineFields && !departmentCodeHidesEmployeeDirection(effectiveDeptCode);",
            self.modal,
        )
        self.assertEqual(2, self.modal.count("showDirectionField && ("))

    def test_empty_option_says_what_will_be_saved(self):
        self.assertIn(
            "const emptyDirectionLabel = directionMayStayEmpty ? 'Без направления' : 'Выберите направление';",
            self.modal,
        )
        # создание
        self.assertIn(
            "                        placeholder={emptyDirectionLabel}\n"
            "                        options={[\n"
            '                            { value: "", label: emptyDirectionLabel },\n',
            self.modal,
        )
        # правка
        self.assertIn('<option value="">{emptyDirectionLabel}</option>', self.modal)
        # Зашитой подписи пустого пункта не осталось ни в одном из двух режимов.
        self.assertEqual(1, self.modal.count("Выберите направление"))


if __name__ == "__main__":
    unittest.main()
