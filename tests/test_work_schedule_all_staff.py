"""«Графики работы» для всех сотрудников — решение владельца 09.11.2026: «по всем
нужно сделать возможность вести графики в Графиках работы».

До решения строки сетки отдавались только операторам (и СВ отдела продаж, #352).
Сетку засеивал общий список сотрудников с бэк-офисом, поэтому строку кадровика
было видно, смену в неё можно было записать, но сервер её не читал: после
перезагрузки смена «пропадала». Теперь строки — у всех ролей, и вместе с этим:
  * общий проход синхронизации статусов видит не-операторов с периодами графика,
    но ведёт им только временные статусы — без увольнения побочным эффектом;
  * пересчёт часов из статусов телефонии не пишет не-операторам нулевые дни;
  * рядовой СВ без отдела по-прежнему видит только операторов.

Исполняются настоящие методы из db/*.py и bot_schedule2.py: `import database`
на Windows падает (time.tzset), поэтому методы компилируются из исходника.
"""
import ast
import copy
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import source_cache

ROOT = Path(__file__).resolve().parents[1]
SCHEDULES = ROOT / 'db' / 'schedules.py'
SHIFTS = ROOT / 'db' / 'shifts.py'
GROUP_LATE = ROOT / 'db' / 'group_late.py'
CORE = ROOT / 'db' / 'core.py'
BOT = ROOT / 'bot_schedule2.py'


def _tree(path):
    return ast.parse(path.read_text(encoding='utf-8-sig'))


def _class(path, class_name):
    return next(n for n in _tree(path).body if isinstance(n, ast.ClassDef) and n.name == class_name)


def _compile(node, path, namespace):
    module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), namespace)


def _method(path, class_name, name, namespace):
    node = next(n for n in _class(path, class_name).body if getattr(n, 'name', None) == name)
    _compile(node, path, namespace)
    return namespace[name]


def _class_constant(path, class_name, name):
    for node in _class(path, class_name).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f'{class_name}.{name} не найден')


def _module_constant(path, name):
    for node in _tree(path).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None)
        if getattr(target, 'id', None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f'{name} не найден в {path.name}')


# ───────────────────────────────────────────── синхронизация статусов по периодам

class SyncCursor:
    """users: {id: (status, role)}; active: {id: код периода на сегодня};
    with_periods: id людей, у которых есть хоть один период."""

    def __init__(self, users, active=None, dismissal_history=(), with_periods=()):
        self.users = users
        self.active = dict(active or {})
        self.dismissal_history = set(dismissal_history)
        self.with_periods = set(with_periods)
        self.sqls = []
        self.updates = {}
        self._rows = []

    def execute(self, sql, params=None):
        flat = ' '.join(sql.split())
        self.sqls.append(flat)
        if flat.startswith('SELECT id FROM users WHERE role ='):
            ids = {i for i, (_, role) in self.users.items() if role == 'operator'} | self.with_periods
            self._rows = [(i,) for i in sorted(ids)]
        elif flat.startswith('SELECT id, status, role FROM users'):
            self._rows = [(i, *self.users[i]) for i in params[0] if i in self.users]
        elif 'DISTINCT ON (operator_id)' in flat:
            self._rows = [(i, self.active[i]) for i in params[0] if i in self.active]
        elif "status_code = 'dismissal'" in flat:
            self._rows = [(i,) for i in params[0] if i in self.dismissal_history]
        elif flat.startswith('UPDATE users'):
            self.updates[params[1]] = params[0]
            self._rows = []
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class _SyncStub:
    def _normalize_schedule_date(self, value):
        return value if isinstance(value, date) else datetime.strptime(str(value), '%Y-%m-%d').date()


_SyncStub._sync = _method(SCHEDULES, 'SchedulesMixin', '_sync_user_statuses_from_schedule_periods_tx', {
    'datetime': datetime,
    'SCHEDULE_STATUS_TO_USER_STATUS': _module_constant(CORE, 'SCHEDULE_STATUS_TO_USER_STATUS'),
})


def _sync(cursor, operator_ids=None):
    return _SyncStub()._sync(cursor, operator_ids=operator_ids, as_of_date='2026-11-09')


class StatusSyncTests(unittest.TestCase):
    def test_global_pass_reaches_people_with_periods(self):
        cursor = SyncCursor({1: ('working', 'operator')})
        _sync(cursor)
        target = cursor.sqls[0]
        self.assertIn('UNION', target)
        self.assertIn('FROM operator_schedule_status_periods', target)

    def test_expired_leave_of_a_non_operator_returns_to_working(self):
        """Прод 09.11.2026: у СВ отдела продаж «Б/С» по дням 3–8 ноября кончилось,
        а статус так и висел «Б/С» — общий проход видел только операторов."""
        cursor = SyncCursor({498: ('bs', 'sv')}, with_periods={498})
        _sync(cursor)
        self.assertEqual(cursor.updates, {498: 'working'})

    def test_active_leave_of_a_non_operator_is_applied(self):
        cursor = SyncCursor({7: ('working', 'accounting_manager')}, active={7: 'annual_leave'}, with_periods={7})
        _sync(cursor)
        self.assertEqual(cursor.updates, {7: 'annual_leave'})

    def test_global_pass_never_fires_a_non_operator(self):
        """Прод 09.11.2026: у действующей учётки СВ висел открытый период «Увольнение»
        с марта. Общий проход молча закрыл бы ей вход в портал."""
        cursor = SyncCursor({3: ('working', 'sv')}, active={3: 'dismissal'},
                            dismissal_history={3}, with_periods={3})
        _sync(cursor)
        self.assertEqual(cursor.updates, {})

    def test_global_pass_never_resurrects_a_fired_non_operator(self):
        cursor = SyncCursor({237: ('fired', 'admin')}, dismissal_history={237}, with_periods={237})
        _sync(cursor)
        self.assertEqual(cursor.updates, {})

    def test_operators_keep_the_full_rule(self):
        cursor = SyncCursor({10: ('working', 'operator'), 11: ('fired', 'operator')},
                            active={10: 'dismissal'}, dismissal_history={10, 11})
        _sync(cursor)
        self.assertEqual(cursor.updates, {10: 'fired', 11: 'working'})

    def test_explicit_save_applies_dismissal_to_any_role(self):
        """Период сохранили человеку руками — это решение, а не побочный эффект."""
        cursor = SyncCursor({3: ('working', 'sv')}, active={3: 'dismissal'}, dismissal_history={3})
        _sync(cursor, operator_ids=[3])
        self.assertEqual(cursor.updates, {3: 'fired'})


# ───────────────────────────────────────────── строки сетки: все роли и группа

class RowsCursor:
    def __init__(self, user_rows):
        self.user_rows = user_rows
        self.sqls = []
        self._rows = []

    def execute(self, sql, params=None):
        flat = ' '.join(sql.split())
        self.sqls.append((flat, params))
        is_rows_query = 'FROM users u' in flat and 'LEFT JOIN directions d' in flat
        self._rows = list(self.user_rows) if is_rows_query else []

    def fetchall(self):
        return list(self._rows)


class _RowsStub:
    _SCHEDULE_ROW_GROUP_NAME_SQL = _class_constant(SHIFTS, 'ShiftsMixin', '_SCHEDULE_ROW_GROUP_NAME_SQL')

    def __init__(self, cursor):
        self.cursor = cursor

    @contextmanager
    def _get_cursor(self):
        yield self.cursor

    def _normalize_schedule_date(self, value):
        return datetime.strptime(str(value), '%Y-%m-%d').date()

    def _normalize_work_shift_type(self, value):
        return value

    def _status_range_covers_today(self, *args):
        return False

    def __getattr__(self, name):
        # _sync_user_statuses…, _attach_* — к строкам и их SQL отношения не имеют.
        if name.startswith('_attach_') or name.startswith('_sync_'):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


_RowsStub.rows = _method(SHIFTS, 'ShiftsMixin', 'get_operators_with_shifts', {
    '_merge_shifts_for_date': lambda shifts: shifts,
    'datetime': datetime, 'date': date, 'timedelta': timedelta,
})


def _row(user_id, role, group_name=None, direction=None, department_id=1):
    return (user_id, f'Сотрудник {user_id}', None, None, direction, 'working', 1.0,
            department_id, role, group_name)


class ScheduleRowsTests(unittest.TestCase):
    def _users_sql(self, cursor):
        return next(sql for sql, _ in cursor.sqls if 'FROM users u' in sql)

    def test_planner_rows_take_every_role(self):
        cursor = RowsCursor([])
        _RowsStub(cursor).rows('2026-11-01', '2026-11-30', include_all_staff=True)
        sql = self._users_sql(cursor)
        self.assertNotIn("u.role = 'operator'", sql)
        self.assertNotIn('WHERE', sql.split('LEFT JOIN directions d ON u.direction_id = d.id', 1)[1])

    def test_other_callers_still_get_only_operators(self):
        """Импорт и выгрузка Excel, симуляция, «смены коллег» ищут людей по ФИО."""
        cursor = RowsCursor([])
        _RowsStub(cursor).rows('2026-11-01', '2026-11-30')
        self.assertIn("u.role = 'operator'", self._users_sql(cursor))

    def test_department_filter_survives_without_role_filter(self):
        cursor = RowsCursor([])
        _RowsStub(cursor).rows('2026-11-01', '2026-11-30', department_id=7, include_all_staff=True)
        sql, params = next((sql, params) for sql, params in cursor.sqls if 'FROM users u' in sql)
        self.assertIn('WHERE u.department_id = %s', sql)
        self.assertEqual(params, [7])

    def test_row_carries_its_current_group(self):
        cursor = RowsCursor([_row(1, 'operator', group_name='Разработчики TT'),
                             _row(2, 'accounting_manager')])
        rows = _RowsStub(cursor).rows('2026-11-01', '2026-11-30', include_all_staff=True)
        self.assertEqual([(r['id'], r['role'], r['group_name']) for r in rows],
                         [(1, 'operator', 'Разработчики TT'), (2, 'accounting_manager', None)])
        sql = self._users_sql(cursor)
        self.assertIn('FROM group_operator_memberships gm', sql)
        self.assertIn('gm.end_date IS NULL OR gm.end_date >= CURRENT_DATE', sql)


# ───────────────────────────────────────────── кого видит рядовой СВ без отдела

class SupervisorWithoutDepartmentTests(unittest.TestCase):
    def test_sees_operators_and_only_himself_from_the_rest(self):
        namespace = {
            '_normalize_user_role': lambda role: str(role or '').strip().lower(),
            '_is_global_admin_requester': lambda role, requester_id=None: False,
            '_headed_department_ids': lambda requester_id: set(),
            '_is_supervisor_role': lambda role: role in ('sv', 'supervisor'),
            '_department_scope_id_for_requester': lambda requester_id: None,
            '_operator_item_id': lambda item: item.get('id'),
        }
        source = source_cache.read(BOT)
        node = source_cache.function_node(BOT, '_filter_operators_for_requester_role')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT), 'exec'), namespace)
        visible = namespace['_filter_operators_for_requester_role'](
            (5, None, 'СВ', 'sv'), 5,
            [{'id': 1, 'role': 'operator'}, {'id': 2, 'role': 'admin'}, {'id': 3, 'role': 'hr_manager'},
             {'id': 4, 'role': 'sv'}, {'id': 5, 'role': 'sv'}],
        )
        self.assertEqual([item['id'] for item in visible], [1, 5])
        self.assertIn('def _filter_operators_for_requester_role', source)


# ───────────────────────────────────────────── пересчёт часов из статусов

class AutoHoursExemptionTests(unittest.TestCase):
    """У не-оператора статусов телефонии нет: пересчёт при сохранении смены записал
    бы ему в «Учёт часов» нулевые дни. Роль — на дату, как у СВ отдела продаж:
    дни, покрытые операторским членством в группе, остаются за пересчётом."""

    def setUp(self):
        node = next(n for n in _class(GROUP_LATE, 'GroupLateMixin').body
                    if getattr(n, 'name', None) == '_recalculate_auto_daily_hours_tx')
        self.source = ast.get_source_segment(GROUP_LATE.read_text(encoding='utf-8-sig'), node)

    def test_non_line_roles_are_exempt(self):
        self.assertIn("AND lower(COALESCE(role, '')) NOT IN ('operator', 'trainee')", self.source)
        self.assertIn('exempt_ids = set(exempt_ids) | {int(row[0]) for row in cursor.fetchall()}', self.source)

    def test_exemption_is_applied_to_people_and_to_days(self):
        self.assertIn('if op_id not in exempt_ids or op_id in still_operators', self.source)
        self.assertIn('if key[0] not in exempt_ids or key in supervisor_operator_days', self.source)
        self.assertNotIn('clockster_supervisors', self.source)


if __name__ == '__main__':
    unittest.main()
