"""Временный просмотр «Графиков работы», как у админа, но без правки (владелец, 21.11.2026:
«Сотрудникам … в сзов надо открыть админский доступ временный к разделам Удаленный КЦ и
Графики работы чисто на просмотр»).

Что защищаем:

  * человек из WORK_SCHEDULE_VIEW_ONLY_USER_IDS (оператор) читает графики всех отделов —
    проверка зрителя его пускает, граница сетки — все строки;
  * править он не может ничего: проверки руководителя и того, кто ведёт графики, его не
    пускают, а сам признак просмотра стоит только в ручках и правилах чтения;
  * своя граница сильнее просмотра: супервайзер из списка видит свой отдел, как раньше;
  * сайт держит тот же список людей, что и сервер.

Исполняются настоящие функции bot_schedule2.py со стабами вокруг; где признак стоит —
сверяется по AST. Люди выдуманы, id — из списка допуска.
"""
import ast
import copy
import re
import unittest
from pathlib import Path

import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / 'bot_schedule2.py'
APP = ROOT / 'src' / 'App.jsx'

SZOV = 1
OP = 367


def _constant(name):
    """Значение модульной константы монолита (frozenset({...}) — не литерал, исполняем)."""
    for node in source_cache.tree(BOT).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            namespace = {}
            exec(compile(ast.Module(body=[copy.deepcopy(node)], type_ignores=[]), str(BOT), 'exec'), namespace)
            return namespace[name]
    raise AssertionError(f'{name} не найден в bot_schedule2.py')


# Настоящий список сейчас пуст (допуск снят 23.11.2026): правило проверяем на выдуманных id,
# которые подставляются в пространство имён функций; зеркало сайта сверяется с настоящим.
REAL_VIEW_ONLY = _constant('WORK_SCHEDULE_VIEW_ONLY_USER_IDS')
VIEWER, OTHER_VIEWER = 9170, 9225
VIEW_ONLY = frozenset({VIEWER, OTHER_VIEWER})
OPERATOR = 700       # оператор СЗоВ не из списка
SUPERVISOR = 701     # СВ отдела продаж

PEOPLE = {
    VIEWER: ('operator', SZOV),
    OTHER_VIEWER: ('operator', SZOV),
    OPERATOR: ('operator', SZOV),
    SUPERVISOR: ('sv', OP),
    900: ('operator', OP),
    901: ('sv', OP),
}


def _role(role):
    return str(role or '').strip().lower()


def _user(user_id):
    role, _dept = PEOPLE[user_id]
    return (user_id, None, f'user{user_id}', role, None, None, None)


def _load(name, namespace):
    node = copy.deepcopy(source_cache.function_node(BOT, name))
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT), 'exec'), namespace)
    return namespace[name]


def _namespace(requester_id, view_only=VIEW_ONLY):
    ns = {
        'WORK_SCHEDULE_VIEW_ONLY_USER_IDS': view_only,
        '_get_authenticated_requester': lambda: (requester_id, _user(requester_id), None),
        '_normalize_user_role': _role,
        '_is_admin_role': lambda role: _role(role) in ('admin', 'super_admin'),
        '_is_supervisor_role': lambda role: _role(role) in ('sv', 'supervisor'),
        '_headed_department_id': lambda uid: None,
        '_headed_department_ids': lambda uid: set(),
        '_is_employee_accounting_manager': lambda uid: False,
        '_is_global_admin_requester': lambda role, uid=None: _role(role) == 'super_admin',
        '_operator_item_id': lambda item: item.get('id'),
        '_work_schedule_granted_member_ids': lambda uid: set(),
        '_trainer_work_schedule_member_ids': lambda: set(),
        '_department_scope_id_for_requester': lambda uid: PEOPLE[uid][1],
    }

    class Db:
        @staticmethod
        def get_department_member_ids(dept_id):
            return {uid for uid, (_r, dept) in PEOPLE.items() if dept == dept_id}

    ns['db'] = Db()
    _load('_is_work_schedule_view_only', ns)
    return ns


ITEMS = [{'id': VIEWER, 'role': 'operator'}, {'id': 900, 'role': 'operator'}, {'id': 901, 'role': 'sv'}]


class GateTests(unittest.TestCase):
    def test_viewer_is_let_in_to_read(self):
        for user_id in (VIEWER, OTHER_VIEWER):
            _uid, _row, error = _load('_resolve_work_schedule_viewer', _namespace(user_id))()
            self.assertIsNone(error, user_id)

    def test_operator_outside_the_list_stays_out(self):
        _uid, _row, error = _load('_resolve_work_schedule_viewer', _namespace(OPERATOR))()
        self.assertEqual(error, ('Forbidden', 403))

    def test_viewer_cannot_write(self):
        """Правка графика, штрафы, заявки — через эти две проверки; просмотр их не открывает."""
        for gate in ('_resolve_management_requester', '_resolve_work_schedule_editor'):
            _uid, _row, error = _load(gate, _namespace(VIEWER))()
            self.assertEqual(error, ('Forbidden', 403), gate)


class ScopeTests(unittest.TestCase):
    def _scope(self, requester_id, view_only=VIEW_ONLY):
        ns = _namespace(requester_id, view_only)
        _load('_filter_operators_for_requester_role', ns)
        return _load('_filter_operators_for_requester_scope', ns)

    def test_viewer_sees_every_department(self):
        scope = self._scope(VIEWER)
        for keeping in (True, False):
            visible = scope(_user(VIEWER), VIEWER, ITEMS, schedule_keeping=keeping)
            self.assertEqual([item['id'] for item in visible], [VIEWER, 900, 901], keeping)

    def test_operator_outside_the_list_sees_nobody(self):
        scope = self._scope(OPERATOR)
        self.assertEqual(scope(_user(OPERATOR), OPERATOR, ITEMS, schedule_keeping=True), [])

    def test_own_boundary_wins_over_view_only(self):
        """СВ из списка видит свой отдел, как раньше: просмотр — последняя ветка."""
        scope = self._scope(SUPERVISOR, view_only=frozenset({SUPERVISOR}))
        visible = scope(_user(SUPERVISOR), SUPERVISOR, ITEMS, schedule_keeping=True)
        self.assertEqual([item['id'] for item in visible], [900, 901])

    def test_rule_reads_ids_of_any_shape(self):
        is_view_only = _namespace(VIEWER)['_is_work_schedule_view_only']
        self.assertIs(is_view_only(VIEWER), True)
        self.assertIs(is_view_only(str(VIEWER)), True)
        for value in (OPERATOR, None, '', 'abc'):
            self.assertIs(is_view_only(value), False, value)


class WhereTheFlagLivesTests(unittest.TestCase):
    """Признак просмотра — только в правилах и ручках чтения: ни одна запись его не видит."""

    READERS = {
        '_is_work_schedule_view_only',
        '_filter_operators_for_requester_role',
        '_resolve_work_schedule_viewer',
        'get_work_schedule_status_track',
        'get_work_schedule_break_rules',
        'api_admin_departments',
    }

    def _users_of_flag(self):
        found = set()
        for node in ast.walk(source_cache.tree(BOT)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if names & {'_is_work_schedule_view_only', 'WORK_SCHEDULE_VIEW_ONLY_USER_IDS'}:
                found.add(node.name)
        return found

    def test_flag_is_read_only_where_reading_happens(self):
        self.assertEqual(self._users_of_flag(), self.READERS)

    def _route_methods(self, function_name):
        node = source_cache.function_node(BOT, function_name)
        methods = set()
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and getattr(decorator.func, 'attr', '') == 'route':
                for keyword in decorator.keywords:
                    if keyword.arg == 'methods':
                        methods |= {elt.value for elt in keyword.value.elts}
        return methods

    def test_routes_with_the_flag_are_reads(self):
        self.assertEqual(self._route_methods('get_work_schedule_status_track'), {'GET'})
        self.assertEqual(self._route_methods('get_work_schedule_break_rules'), {'GET'})

    def test_departments_dictionary_opens_only_for_reading(self):
        """Справочник отделов: просмотр — в ветке GET, создание отдела остаётся супер-админу."""
        source = ast.get_source_segment(source_cache.read(BOT), source_cache.function_node(BOT, 'api_admin_departments'))
        get_branch = source[source.index("if request.method == 'GET':"):source.index('# POST')]
        self.assertIn('_is_work_schedule_view_only(requester_id)', get_branch)
        self.assertEqual(source.count('_is_work_schedule_view_only'), 1)

    def test_status_track_lets_the_viewer_past_the_operator_branch(self):
        source = ast.get_source_segment(source_cache.read(BOT),
                                        source_cache.function_node(BOT, 'get_work_schedule_status_track'))
        self.assertIn("== 'operator'\n                and not _is_work_schedule_view_only(requester_id)", source)


class SiteMirrorTests(unittest.TestCase):
    def test_site_holds_the_same_people(self):
        app = APP.read_text(encoding='utf-8')
        match = re.search(r'^const WORK_SCHEDULE_VIEW_ONLY_USER_IDS = new Set\(\[([\d,\s]*)\]\);', app, re.M)
        self.assertIsNotNone(match, 'зеркало списка в App.jsx не найдено')
        site = {int(x) for x in match.group(1).replace(' ', '').split(',') if x}
        self.assertEqual(site, set(REAL_VIEW_ONLY))


if __name__ == '__main__':
    unittest.main()
