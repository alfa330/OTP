# -*- coding: utf-8 -*-
"""«Учет сотрудников» по страницам — то, что проверяется без базы.

Строка страницы обязана совпадать со строкой полного списка (/api/admin/users,
/api/admin/sv_list): таблицы, карточка и окна правки читают её поля по именам.
Здесь сторожится, что запрос и сборка строки в employees/rows.py — те же, что в
полных ручках монолита: колонка, добавленная туда, должна появиться и здесь.
Кого видно, сверяется с get_admin_users тем же приёмом, что у
tests/test_hr_employee_accounting_scope.py (код ручки из монолита через ast).
Сами наборы людей, порядок и числа на настоящем SQL —
tests/test_employees_page_postgres.py.
"""
import ast
import re
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from employees import page, rows
from employees.routes import supervisors_scope, users_scope
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = source_cache.monolith_flat_path()
BACK_OFFICE = frozenset({'hr_manager', 'accounting_manager', 'marketing_manager'})


def _function(name):
    return next(node for node in source_cache.tree(BOT_PATH).body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _sql_strings(function):
    return [node.value for node in ast.walk(function)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 'SELECT' in node.value]


def _columns(sql, where):
    """Список выборки и FROM/JOIN до WHERE — без комментариев и пробелов."""
    text = re.sub(r'--[^\n]*', '', sql)
    text = text[:text.index(where)]
    return re.sub(r'\s+', ' ', text).strip()


def _dict_keys(function, list_name):
    """Ключи самого полного словаря, который ручка кладёт в свой список
    (list_name.append({...})): у get_admin_users есть и урезанная проекция оператора."""
    found = [[key.value for key in node.args[0].keys] for node in ast.walk(function)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'append'
             and isinstance(node.func.value, ast.Name) and node.func.value.id == list_name
             and node.args and isinstance(node.args[0], ast.Dict)]
    assert found, f'нет {list_name}.append({{...}}) в {function.name}'
    return max(found, key=len)


class ProjectionMatchesFullListsTests(unittest.TestCase):
    def test_users_select_is_the_one_of_get_admin_users(self):
        legacy = next(sql for sql in _sql_strings(_function('get_admin_users')) if 'status_period_status_code' in sql)
        self.assertEqual(_columns(rows.USERS_SQL, 'WHERE u.id = ANY'), _columns(legacy, 'WHERE u.role = ANY'))

    def test_users_row_has_every_field_of_get_admin_users(self):
        built = rows.user_row([None] * 8 + [1.0] + [None] * 45, avatar_url=lambda *_: None, proxy_labels={})
        self.assertEqual(list(built), _dict_keys(_function('get_admin_users'), 'users'))

    def test_supervisors_select_is_the_full_profile_of_get_sv_list(self):
        legacy = next(sql for sql in _sql_strings(_function('get_sv_list')) if 'close_contact_1_relation' in sql)
        self.assertEqual(_columns(rows.SUPERVISORS_SQL, 'WHERE id = ANY'),
                         _columns(legacy, "WHERE LOWER(COALESCE(role, '')) IN ('sv', 'supervisor')"))

    def test_supervisors_row_has_every_field_of_the_full_profile(self):
        built = rows.supervisor_row([None] * 43, avatar_url=lambda *_: None, proxy_labels={},
                                    normalize_role=lambda value: value)
        self.assertEqual(list(built), _dict_keys(_function('get_sv_list'), 'sv_data'))


class ParseQueryTests(unittest.TestCase):
    def test_defaults_are_the_first_page_of_active_people_by_name(self):
        query = page.parse_query({})
        self.assertEqual((query.list, query.status, query.sort, query.direction, query.limit, query.offset),
                         ('users', 'active', 'name', 'asc', 50, 0))
        self.assertIsNone(query.roles)
        self.assertFalse(query.with_ids)

    def test_roles_search_and_limits(self):
        query = page.parse_query({'roles': ' Operator, trainee ,,', 'q': '  Иван  ', 'limit': '1000',
                                  'offset': '100', 'department': '7', 'ids': '1', 'group': 'operators'})
        self.assertEqual(query.roles, ('operator', 'trainee'))
        self.assertEqual(query.search, 'Иван')
        self.assertEqual(query.limit, page.MAX_PAGE_SIZE)
        self.assertEqual((query.offset, query.department_id, query.with_ids, query.grouping), (100, 7, True, 'operators'))

    def test_empty_roles_mean_nobody_rather_than_everybody(self):
        self.assertEqual(page.parse_query({'roles': ''}).roles, ())

    def test_bad_values_are_refused(self):
        for args in ({'status': 'vacation'}, {'sort': 'salary'}, {'dir': 'up'}, {'limit': '0'}, {'offset': '-5'},
                     {'department': 'abc'}, {'list': 'all'}, {'list': 'supervisors', 'group': 'operators'}):
            with self.subTest(args=args), self.assertRaises(page.PageError):
                page.parse_query(args)


class PagingArithmeticTests(unittest.TestCase):
    def test_offset_past_the_end_is_the_last_page(self):
        self.assertEqual(page.clamp_offset(500, 50, 312), 300)
        self.assertEqual(page.clamp_offset(300, 50, 300), 250)
        self.assertEqual(page.clamp_offset(75, 50, 312), 50)
        self.assertEqual(page.clamp_offset(50, 50, 0), 0)

    def test_status_counts_active_is_everyone_but_the_fired(self):
        counts, total = page.status_counts([('working', 5), ('bs', 2), ('fired', 3), ('other', 1)])
        self.assertEqual(total, 11)
        self.assertEqual(counts, {'active': 8, 'working': 5, 'bs': 2, 'sick_leave': 0, 'annual_leave': 0, 'fired': 3})

    def test_every_order_ends_with_id_so_pages_never_overlap(self):
        for sort in page.SORT_FIELDS:
            for direction in ('asc', 'desc'):
                for grouping in page.GROUPINGS:
                    query = page.PageQuery(sort=sort, direction=direction, grouping=grouping)
                    self.assertTrue(page.order_by(query, 'users').endswith('id ASC'))


class VisibilityMirrorsGetAdminUsersTests(unittest.TestCase):
    """Набор ролей и граница отдела — как у get_admin_users для того же смотрящего."""

    VIEWERS = (
        # роль, возглавляемые отделы, кадровик
        ('super_admin', (), False),
        ('super_admin', (2,), False),
        ('admin', (), False),
        ('admin', (367,), False),
        ('sv', (), False),
        ('sv', (12,), False),
        ('trainer', (), False),
        ('hr_manager', (), True),
        ('admin', (1499,), True),
    )

    def _legacy_roles(self, role, headed, hr):
        captured = {}

        class _Cursor:
            def execute(self, _sql, params=None):
                captured['roles'] = list(params[0]) if params else []

            def fetchall(self):
                return []

        class _DB:
            @contextmanager
            def _get_cursor(self):
                yield _Cursor()

        node = source_cache.function_copy(BOT_PATH, 'get_admin_users')
        node.decorator_list = []
        namespace = {
            'db': _DB(),
            'jsonify': lambda payload: payload,
            'logging': SimpleNamespace(error=lambda *_a, **_k: None),
            'BACK_OFFICE_EMPLOYEE_ROLES': BACK_OFFICE,
            'PROXY_STATUS_LABELS': {},
            '_build_avatar_signed_url': lambda *_a: None,
            '_get_authenticated_requester': lambda: (10, (10, None, 'User 10', role), None),
            '_normalize_user_role': lambda value: str(value or '').strip().lower(),
            '_is_admin_role': lambda value: str(value or '').lower() in ('admin', 'super_admin'),
            '_is_super_admin_role': lambda value: str(value or '').lower() == 'super_admin',
            '_headed_department_ids': lambda _id: frozenset(headed),
            '_department_scope_id_for_requester': lambda _id: 5,
            '_is_employee_accounting_manager': lambda _id: hr,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), 'exec'), namespace)
        result = namespace['get_admin_users']()
        return captured.get('roles'), result[1] if isinstance(result, tuple) else 200

    def _scope(self, role, headed, hr, roles=None):
        return users_scope(
            role=role, requester_id=10, headed=frozenset(headed), hr=hr, back_office_roles=BACK_OFFICE, roles=roles,
            is_admin_role=lambda value: value in ('admin', 'super_admin'),
            is_super_admin_role=lambda value: value == 'super_admin',
            department_scope_id_for_requester=lambda _id: 5,
        )

    def test_visible_roles_are_the_same(self):
        for role, headed, hr in self.VIEWERS:
            with self.subTest(role=role, headed=headed, hr=hr):
                legacy_roles, status = self._legacy_roles(role, headed, hr)
                self.assertEqual(status, 200)
                self.assertEqual(list(self._scope(role, headed, hr).roles), legacy_roles)

    def test_department_boundary(self):
        self.assertEqual(self._scope('sv', (), False).sv_department, 5)
        self.assertIsNone(self._scope('sv', (), False).headed)
        self.assertEqual(self._scope('sv', (12,), False).headed, (12,))
        self.assertEqual(self._scope('admin', (367,), False).headed, (367,))
        # Супер-админ во главе отдела видит всех — как в get_admin_users.
        for role, headed, hr in (('super_admin', (), False), ('super_admin', (2,), False), ('admin', (), False),
                                 ('trainer', (), False),
                                 ('hr_manager', (), True), ('admin', (1499,), True)):
            scope = self._scope(role, headed, hr)
            self.assertIsNone(scope.headed, role)
            self.assertIsNone(scope.sv_department, role)

    def test_requested_roles_only_narrow_the_list(self):
        scope = self._scope('sv', (), False, roles=('operator', 'admin', 'super_admin'))
        self.assertEqual(scope.roles, ('operator',))

    def test_people_without_the_section_get_nothing(self):
        for role in ('operator', 'trainee', 'accounting_manager', 'marketing_manager'):
            self.assertIsNone(self._scope(role, (), False), role)
        self.assertEqual(self._legacy_roles('accounting_manager', (), False)[1], 403)

    def test_supervisors_list_is_for_admins_heads_and_hr(self):
        def scope(role, headed=(), hr=False):
            return supervisors_scope(
                role=role, requester_id=10, headed=frozenset(headed), hr=hr,
                is_global_admin_requester=lambda value, _id: value in ('admin', 'super_admin') and not headed)
        self.assertIsNone(scope('sv'))
        self.assertIsNone(scope('trainer'))
        self.assertIsNone(scope('super_admin').headed)
        self.assertEqual(scope('admin', (367,)).headed, (367,))
        self.assertEqual(scope('sv', (12,)).headed, (12,))
        self.assertIsNone(scope('hr_manager', hr=True).headed)
        self.assertIsNone(scope('admin', (1499,), hr=True).headed)


class WiringTests(unittest.TestCase):
    def test_monolith_registers_the_blueprint_with_its_dependencies(self):
        source = source_cache.read(ROOT / 'bot_schedule2.py')
        block = source[source.index('from employees.routes import build_employees_blueprint'):]
        block = block[:block.index('logging.info("Учет сотрудников: Blueprint подключён')]
        for name in ('require_api_key=require_api_key', 'get_authenticated_requester=_get_authenticated_requester',
                     'is_global_admin_requester=_is_global_admin_requester',
                     'department_scope_id_for_requester=_department_scope_id_for_requester',
                     'is_employee_accounting_manager=_is_employee_accounting_manager',
                     'back_office_roles=BACK_OFFICE_EMPLOYEE_ROLES', 'proxy_status_labels=PROXY_STATUS_LABELS'):
            self.assertIn(name, block)

    def test_package_does_not_import_the_monolith(self):
        for path in (ROOT / 'employees').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            self.assertNotRegex(text, r'^\s*(from|import)\s+(bot_schedule2|database)\b', path.name)


if __name__ == '__main__':
    unittest.main()
