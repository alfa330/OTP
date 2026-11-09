"""Отдел кадров ведёт графики любого отдела — решение владельца 09.11.2026: «нужно дать
права HR, чтобы они могли вести графики любого отдела».

Вести — значит смены, выходные, статусы (отпуск, больничный, Б/С, увольнение) и
выгрузка. Признак кадровика — членство в отделе кадров (_is_employee_accounting_manager),
у рядового роль hr_manager, и до решения он в раздел не попадал вовсе: проверки
пускали только админа, СВ, главу и тренера.

Санкции и прочая работа руководителя линии кадровику НЕ выданы: штраф, снять ЧС,
тренинги, перерывы, агрегация, импорт, разбор заявок. Там остаётся прежняя проверка,
а цели кадровику не расширяются (allow_employee_accounting=False) — иначе глава отдела
кадров (роль admin) штрафовал бы любого оператора: по людям у кадровика границ нет.

Исполняются настоящие функции bot_schedule2.py со стабами вокруг; привязка ручек к
проверкам сверяется по AST.
"""
import ast
import copy
import unittest
from pathlib import Path

import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / 'bot_schedule2.py'

HR_DEPT = 30
OTHER_DEPT = 10


def _load(name, namespace):
    node = copy.deepcopy(source_cache.function_node(BOT, name))
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT), 'exec'), namespace)
    return namespace[name]


def _role(role):
    return str(role or '').strip().lower()


# Люди: id → (роль, отдел). 7 — рядовой кадровик, 8 — глава отдела кадров с ролью
# admin, 9 — оператор чужого отдела, 11 — тренер, 12 — оператор отдела кадров (старая роль).
PEOPLE = {
    7: ('hr_manager', HR_DEPT),
    8: ('admin', HR_DEPT),
    9: ('operator', OTHER_DEPT),
    11: ('trainer', OTHER_DEPT),
    12: ('operator', HR_DEPT),
    20: ('sv', OTHER_DEPT),
    21: ('admin', None),
}
HEADS = {8: {HR_DEPT}}


def _user(user_id):
    role, _dept = PEOPLE[user_id]
    return (user_id, None, f'user{user_id}', role, None, None, None)


def _base_namespace(requester_id):
    return {
        '_get_authenticated_requester': lambda: (requester_id, _user(requester_id), None),
        '_normalize_user_role': _role,
        '_is_admin_role': lambda role: _role(role) in ('admin', 'super_admin'),
        '_is_super_admin_role': lambda role: _role(role) == 'super_admin',
        '_is_supervisor_role': lambda role: _role(role) in ('sv', 'supervisor'),
        '_headed_department_id': lambda uid: next(iter(HEADS.get(uid, ())), None),
        '_headed_department_ids': lambda uid: set(HEADS.get(uid, ())),
        '_is_employee_accounting_manager': lambda uid: uid is not None and PEOPLE.get(uid, (None, None))[1] == HR_DEPT,
        '_is_global_admin_requester': lambda role, uid=None: _role(role) == 'super_admin' or (
            _role(role) == 'admin' and not HEADS.get(uid)),
    }


class EditorGateTests(unittest.TestCase):
    """Кто ведёт графики: управленцы и отдел кадров; тренер и оператор — нет."""

    def _editor(self, requester_id):
        return _load('_resolve_work_schedule_editor', _base_namespace(requester_id))()

    def test_hr_employee_and_head_pass(self):
        for requester_id in (7, 8, 12):
            _uid, _user_row, error = self._editor(requester_id)
            self.assertIsNone(error, requester_id)

    def test_line_operator_and_trainer_stay_out(self):
        for requester_id in (9, 11):
            _uid, _user_row, error = self._editor(requester_id)
            self.assertEqual(error, ('Forbidden', 403), requester_id)

    def test_managers_keep_access(self):
        for requester_id in (20, 21):
            _uid, _user_row, error = self._editor(requester_id)
            self.assertIsNone(error, requester_id)

    def test_viewer_lets_hr_in(self):
        viewer = _load('_resolve_work_schedule_viewer', _base_namespace(7))
        self.assertIsNone(viewer()[2])
        viewer = _load('_resolve_work_schedule_viewer', _base_namespace(9))
        self.assertEqual(viewer()[2], ('Forbidden', 403))


class ScheduleKeepingScopeTests(unittest.TestCase):
    """Строки сетки, история, выгрузка — все; заявки и тренинги — прежний круг."""

    def _scope(self, requester_id):
        ns = _base_namespace(requester_id)
        ns['_operator_item_id'] = lambda item: item.get('id')
        ns['_work_schedule_granted_member_ids'] = lambda uid: set()
        ns['_trainer_work_schedule_member_ids'] = lambda: set()
        ns['_department_scope_id_for_requester'] = lambda uid: PEOPLE[uid][1]

        class Db:
            @staticmethod
            def get_department_member_ids(dept_id):
                return {uid for uid, (_r, dept) in PEOPLE.items() if dept == dept_id}

        ns['db'] = Db()
        _load('_filter_operators_for_requester_role', ns)
        return _load('_filter_operators_for_requester_scope', ns)

    ITEMS = [{'id': 9, 'role': 'operator'}, {'id': 20, 'role': 'sv'}, {'id': 7, 'role': 'hr_manager'}]

    def test_hr_employee_sees_every_row_for_schedule_keeping(self):
        scope = self._scope(7)
        visible = scope(_user(7), 7, self.ITEMS, schedule_keeping=True)
        self.assertEqual([item['id'] for item in visible], [9, 20, 7])

    def test_hr_employee_has_no_line_circle_without_flag(self):
        # Заявки на смены и отпуска, тренинги: круга руководителя у кадровика нет.
        scope = self._scope(7)
        self.assertEqual(scope(_user(7), 7, self.ITEMS), [])

    def test_hr_head_without_flag_stays_in_own_department(self):
        scope = self._scope(8)
        self.assertEqual([item['id'] for item in scope(_user(8), 8, self.ITEMS)], [7])
        self.assertEqual(len(scope(_user(8), 8, self.ITEMS, schedule_keeping=True)), 3)

    def test_flag_does_not_widen_anyone_else(self):
        scope = self._scope(20)
        self.assertEqual(
            [item['id'] for item in scope(_user(20), 20, self.ITEMS, schedule_keeping=True)],
            [item['id'] for item in scope(_user(20), 20, self.ITEMS)],
        )


class LineActionTargetTests(unittest.TestCase):
    """Глава отдела кадров (admin) проходит проверку руководителя; цель штрафа и
    прочих действий линии ему по-прежнему считается границей его отдела."""

    def _resolver(self, requester_id):
        ns = _base_namespace(requester_id)

        class Db:
            @staticmethod
            def get_user(id):  # noqa: A002 - сигнатура db.get_user
                return _user(id) if id in PEOPLE else None

            @staticmethod
            def get_user_department_id(uid):
                return PEOPLE[uid][1]

        ns['db'] = Db()
        ns['_department_scope_id_for_requester'] = lambda uid: PEOPLE[uid][1]
        ns['_target_user_supervisor_id'] = lambda target: target[6]
        ns['_work_schedule_granted_target'] = lambda uid, target_id, roles: None
        _load('_requester_can_access_target_user', ns)
        _load('_load_target_user_with_scope', ns)
        return _load('_resolve_scoped_operator_for_requester', ns)

    def test_schedule_edit_reaches_any_department(self):
        resolve = self._resolver(8)
        target, error = resolve(_user(8), 8, 9)
        self.assertIsNone(error)
        self.assertEqual(target[0], 9)
        target, error = self._resolver(7)(_user(7), 7, 9)
        self.assertIsNone(error)

    def test_line_action_is_bounded_by_role(self):
        resolve = self._resolver(8)
        _target, error = resolve(_user(8), 8, 9, allow_employee_accounting=False)
        self.assertEqual(error, ('Forbidden for this operator', 403))
        # Свой отдел глава кадров ведёт как глава — и для действий линии.
        target, error = resolve(_user(8), 8, 12, allow_employee_accounting=False)
        self.assertIsNone(error)
        self.assertEqual(target[0], 12)


def _route_functions():
    """'/api/...' + метод → узел функции ручки."""
    routes = {}
    for node in source_cache.tree(BOT).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and getattr(deco.func, 'attr', None) == 'route'):
                continue
            path = deco.args[0].value
            methods = ['GET']
            for kw in deco.keywords:
                if kw.arg == 'methods':
                    methods = [elt.value for elt in kw.value.elts]
            for method in methods:
                routes[(path, method)] = node
    return routes


def _calls(node, name):
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call) and getattr(call.func, 'id', None) == name
    ]


def _kw(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return ast.literal_eval(kw.value)
    return None


class RouteWiringTests(unittest.TestCase):
    ROUTES = _route_functions()

    EDITOR_ROUTES = [
        ('/api/work_schedules/shift', 'POST'),
        ('/api/work_schedules/shift', 'DELETE'),
        ('/api/work_schedules/day_off', 'POST'),
        # Тот же вызов делает карточка в «Учете сотрудников», когда кадровик ставит
        # статус с периодом (отпуск, Б/С, больничный, увольнение): до решения сервер
        # отвечал ему отказом.
        ('/api/work_schedules/status_period', 'POST'),
        ('/api/work_schedules/status_period', 'DELETE'),
        ('/api/work_schedules/shifts_bulk', 'POST'),
        ('/api/work_schedules/bulk_actions', 'POST'),
        ('/api/work_schedules/export_excel', 'GET'),
    ]

    LINE_ROUTES = [
        ('/api/work_schedules/fines', 'POST'),
        ('/api/training_rejections', 'POST'),
        ('/api/work_schedules/auto_flags/resolve', 'POST'),
        ('/api/work_schedules/recalculate_breaks', 'POST'),
        ('/api/work_schedules/aggregate', 'POST'),
    ]

    def test_schedule_keeping_routes_use_editor_gate(self):
        for key in self.EDITOR_ROUTES:
            node = self.ROUTES[key]
            self.assertEqual(len(_calls(node, '_resolve_work_schedule_editor')), 1, key)
            self.assertEqual(_calls(node, '_resolve_management_requester'), [], key)

    def test_line_routes_keep_manager_gate_and_bounded_targets(self):
        for key in self.LINE_ROUTES:
            node = self.ROUTES[key]
            self.assertEqual(len(_calls(node, '_resolve_management_requester')), 1, key)
            self.assertEqual(_calls(node, '_resolve_work_schedule_editor'), [], key)
            resolver_calls = _calls(node, '_resolve_scoped_operator_for_requester')
            self.assertTrue(resolver_calls, key)
            for call in resolver_calls:
                self.assertIs(_kw(call, 'allow_employee_accounting'), False, key)

    def test_blacklist_removal_keeps_hr_reach(self):
        # «Убрать из ЧС» глава отдела кадров (роль admin) делает и из «Учета
        # сотрудников» по людям всей компании — право с 22.09.2026. Сужать его
        # здесь значило бы сломать ему тот раздел.
        node = self.ROUTES[('/api/work_schedules/status_period/blacklist', 'DELETE')]
        calls = _calls(node, '_resolve_scoped_operator_for_requester')
        self.assertEqual(len(calls), 1)
        self.assertIsNone(_kw(calls[0], 'allow_employee_accounting'))
        self.assertIs(_kw(calls[0], 'allow_grant'), False)

    def test_other_manager_routes_untouched(self):
        for key in [
            ('/api/work_schedules/break_rules', 'POST'),
            ('/api/work_schedules/shift_change/respond', 'POST'),
        ]:
            self.assertEqual(_calls(self.ROUTES[key], '_resolve_work_schedule_editor'), [], key)

    def test_schedule_keeping_flag_only_where_the_question_is_the_schedule(self):
        keeping = {
            ('/api/work_schedules/operators', 'GET'),
            ('/api/work_schedules/history', 'GET'),
            ('/api/work_schedules/status_track', 'GET'),
            ('/api/work_schedules/training_news', 'GET'),
            ('/api/work_schedules/export_excel', 'GET'),
        }
        for key, node in self.ROUTES.items():
            for call in _calls(node, '_filter_operators_for_requester_scope'):
                self.assertEqual(_kw(call, 'schedule_keeping') is True, key in keeping, key)
        for key in keeping:
            self.assertTrue(_calls(self.ROUTES[key], '_filter_operators_for_requester_scope'), key)
        # Тренинги («Графики работы» → чьи тренинги мне отдали) — круг руководителя.
        node = source_cache.function_node(BOT, '_work_schedule_operator_ids')
        for call in _calls(node, '_filter_operators_for_requester_scope'):
            self.assertIsNone(_kw(call, 'schedule_keeping'))

    def test_break_rules_are_readable_by_hr(self):
        # Перерывы смены сетка раскладывает по правилам направления: без них смена
        # оператора, поставленная кадровиком, легла бы без перерывов.
        node = self.ROUTES[('/api/work_schedules/break_rules', 'GET')]
        self.assertTrue(_calls(node, '_is_employee_accounting_manager'))


class BreakRulesReadTests(unittest.TestCase):
    def _call(self, requester_id):
        ns = _base_namespace(requester_id)
        seen = {}

        class Db:
            @staticmethod
            def get_work_schedule_break_rules(department_id=None):
                seen['department_id'] = department_id
                return [{'direction': 'X', 'rules': []}]

            @staticmethod
            def get_user_department_id(uid):
                return PEOPLE[uid][1]

        ns.update({
            'db': Db(),
            'jsonify': lambda payload: payload,
            '_with_granted_break_rules': lambda uid, rules: rules,
            'logging': __import__('logging'),
        })
        result = _load('get_work_schedule_break_rules', ns)()
        return result, seen

    def test_hr_employee_reads_rules_of_every_direction(self):
        (payload, status), seen = self._call(7)
        self.assertEqual(status, 200)
        self.assertIn('direction_rules', payload)
        self.assertIsNone(seen['department_id'])

    def test_line_operator_still_forbidden(self):
        (payload, status), _seen = self._call(9)
        self.assertEqual(status, 403)


if __name__ == '__main__':
    unittest.main()
