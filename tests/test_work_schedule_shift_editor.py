"""Поимённая правка смен и выходных (владелец, 21.11.2026: «Надо ещё выдать доступ на изменение
смен»; выбрал «Только СЗоВ» и «Смены и выходные»).

Что защищаем:

  * человек из WORK_SCHEDULE_SHIFT_EDITOR_GRANTS (оператор) проходит проверку правки смен и
    правит смены и выходные операторов отдела допуска — и только их: не СВ, не чужой отдел,
    не свои собственные;
  * остальные операторы, как раньше, — 403; кто вёл графики раньше, проходит по-прежнему;
  * проверка правки смен стоит ровно в пяти ручках смен и выходных; статусы на период,
    штрафы, ЧС и прочее — прежние проверки, допуск их не открывает;
  * сайт держит тот же список, что и сервер.

Исполняются настоящие функции bot_schedule2.py со стабами вокруг; где что стоит — по AST.
Люди выдуманы, id правщиков — из списка допуска.
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

SZOV, OP = 1, 367


def _constant(name):
    for node in source_cache.tree(BOT).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            namespace = {}
            exec(compile(ast.Module(body=[copy.deepcopy(node)], type_ignores=[]), str(BOT), 'exec'), namespace)
            return namespace[name]
    raise AssertionError(f'{name} не найден в bot_schedule2.py')


# Настоящий список сейчас пуст (допуск снят 23.11.2026): правило проверяем на выдуманных id,
# которые подставляются в пространство имён функций; зеркало сайта сверяется с настоящим.
REAL_GRANTS = _constant('WORK_SCHEDULE_SHIFT_EDITOR_GRANTS')
EDITOR, OTHER_EDITOR = 9170, 9225
GRANTS = {EDITOR: ('szov',), OTHER_EDITOR: ('szov',)}

# id → (роль, отдел)
PEOPLE = {
    EDITOR: ('operator', SZOV),
    OTHER_EDITOR: ('operator', SZOV),
    700: ('operator', SZOV),       # оператор СЗоВ — его смены правщику открыты
    701: ('sv', SZOV),             # СВ СЗоВ — его строка правщику закрыта
    702: ('operator', OP),         # оператор другого отдела — закрыт
    703: ('trainee', SZOV),        # стажёр СЗоВ — как у СВ отдела, закрыт
    800: ('operator', SZOV),       # оператор не из списка — правщиком не является
    900: ('sv', SZOV),             # СВ — ведёт графики по своей роли
}
CODES = {SZOV: 'szov', OP: 'op'}


def _role(value):
    return str(value or '').strip().lower()


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


class _Db:
    def __init__(self):
        self.calls = []

    @staticmethod
    def get_user(id):  # noqa: A002 — имя аргумента как у Database.get_user
        return _user(id) if id in PEOPLE else None

    def toggle_day_off(self, operator_id, day, actor_id=None):
        self.calls.append(('day_off', operator_id, day, actor_id))
        return True

    def delete_shift(self, operator_id, day, start, end, actor_id=None):
        self.calls.append(('delete', operator_id, day, actor_id))
        return True


def _namespace(requester_id, db=None):
    def scoped(requester, rid, operator_id, **_kw):
        # Граница роли: оператор не правит никого, СВ — своих операторов.
        target = PEOPLE.get(int(operator_id))
        if target is None:
            return None, ('Operator not found', 404)
        if _role(requester[3]) == 'sv' and target[0] == 'operator' and target[1] == PEOPLE[rid][1]:
            return _user(int(operator_id)), None
        return None, ('Forbidden for this operator', 403)

    ns = {
        'WORK_SCHEDULE_SHIFT_EDITOR_GRANTS': GRANTS,
        'db': db or _Db(),
        '_get_authenticated_requester': lambda: (requester_id, _user(requester_id), None),
        '_normalize_user_role': _role,
        '_is_admin_role': lambda role: _role(role) in ('admin', 'super_admin'),
        '_is_supervisor_role': lambda role: _role(role) in ('sv', 'supervisor'),
        '_headed_department_id': lambda uid: None,
        '_is_employee_accounting_manager': lambda uid: False,
        '_resolve_scoped_operator_for_requester': scoped,
        '_department_code_of_user': lambda uid: CODES.get(PEOPLE[uid][1], ''),
    }
    for name in ('_work_schedule_shift_editor_codes', '_resolve_work_schedule_editor',
                 '_resolve_work_schedule_shift_editor', '_resolve_shift_target_for_requester'):
        _load(name, ns)
    return ns


class GateTests(unittest.TestCase):
    def test_editors_pass_the_shift_gate_but_not_the_general_one(self):
        for user_id in (EDITOR, OTHER_EDITOR):
            ns = _namespace(user_id)
            self.assertIsNone(ns['_resolve_work_schedule_shift_editor']()[2], user_id)
            # Статусы, выгрузка — прежняя проверка «ведёт графики»: она правщика не пускает.
            self.assertEqual(ns['_resolve_work_schedule_editor']()[2], ('Forbidden', 403), user_id)

    def test_other_operator_stays_out(self):
        self.assertEqual(_namespace(800)['_resolve_work_schedule_shift_editor']()[2], ('Forbidden', 403))

    def test_those_who_kept_schedules_still_pass(self):
        self.assertIsNone(_namespace(900)['_resolve_work_schedule_shift_editor']()[2])


class TargetTests(unittest.TestCase):
    def _target(self, requester_id, operator_id):
        ns = _namespace(requester_id)
        return ns['_resolve_shift_target_for_requester'](_user(requester_id), requester_id, operator_id)

    def test_operator_of_the_granted_department_is_open(self):
        target, error = self._target(EDITOR, 700)
        self.assertIsNone(error)
        self.assertEqual(target[0], 700)
        # Строкой id — так приходит из тела запроса.
        self.assertIsNone(self._target(EDITOR, '700')[1])

    def test_everyone_else_stays_closed(self):
        for operator_id in (701, 702, 703, EDITOR):
            with self.subTest(operator_id=operator_id):
                target, error = self._target(EDITOR, operator_id)
                self.assertIsNone(target)
                self.assertEqual(error, ('Forbidden for this operator', 403))
        # Свои смены правщик не правит, а смены коллеги-правщика (тоже оператора СЗоВ) — да.
        self.assertIsNone(self._target(OTHER_EDITOR, EDITOR)[1])
        self.assertEqual(self._target(OTHER_EDITOR, OTHER_EDITOR)[1], ('Forbidden for this operator', 403))

    def test_unknown_operator_answers_as_before(self):
        self.assertEqual(self._target(EDITOR, 999999)[1], ('Operator not found', 404))

    def test_without_the_grant_nothing_changes(self):
        target, error = self._target(800, 700)
        self.assertIsNone(target)
        self.assertEqual(error, ('Forbidden for this operator', 403))
        # СВ — по своей границе, как раньше.
        self.assertIsNone(self._target(900, 700)[1])


class RouteTests(unittest.TestCase):
    """Настоящие ручки выходного и удаления смены с настоящими проверками."""

    def _call(self, handler, requester_id, body):
        from types import SimpleNamespace
        db = _Db()
        ns = _namespace(requester_id, db)
        ns.update({
            'request': SimpleNamespace(get_json=lambda silent=False: dict(body)),
            'jsonify': lambda payload: payload,
            'logging': SimpleNamespace(error=lambda *_a, **_k: None),
        })
        result = _load(handler, ns)()
        status = result[1] if isinstance(result, tuple) else 200
        return status, db.calls

    def test_editor_toggles_a_day_off_and_deletes_a_shift_of_an_szov_operator(self):
        status, calls = self._call('toggle_work_day_off', EDITOR, {'operator_id': 700, 'day_off_date': '2026-11-25'})
        self.assertEqual((status, calls), (200, [('day_off', 700, '2026-11-25', EDITOR)]))
        status, calls = self._call('delete_work_shift', EDITOR, {
            'operator_id': 700, 'shift_date': '2026-11-25', 'start_time': '09:00', 'end_time': '18:00'})
        self.assertEqual((status, calls), (200, [('delete', 700, '2026-11-25', EDITOR)]))

    def test_editor_cannot_touch_other_rows(self):
        for operator_id in (701, 702, EDITOR):
            with self.subTest(operator_id=operator_id):
                status, calls = self._call('toggle_work_day_off', EDITOR,
                                           {'operator_id': operator_id, 'day_off_date': '2026-11-25'})
                self.assertEqual((status, calls), (403, []))

    def test_other_operator_is_refused(self):
        status, calls = self._call('toggle_work_day_off', 800, {'operator_id': 700, 'day_off_date': '2026-11-25'})
        self.assertEqual((status, calls), (403, []))


class WiringTests(unittest.TestCase):
    SHIFT_ROUTES = {'save_work_shift', 'delete_work_shift', 'toggle_work_day_off', 'save_shifts_bulk',
                    'apply_work_schedule_bulk_actions'}

    @staticmethod
    def _callers(name):
        found = set()
        for node in ast.walk(source_cache.tree(BOT)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(isinstance(call, ast.Call) and getattr(call.func, 'id', None) == name
                       for call in ast.walk(node)):
                    found.add(node.name)
        return found

    def test_shift_gate_and_target_stand_only_in_the_five_shift_routes(self):
        self.assertEqual(self._callers('_resolve_work_schedule_shift_editor'), self.SHIFT_ROUTES)
        self.assertEqual(self._callers('_resolve_shift_target_for_requester'), self.SHIFT_ROUTES)
        self.assertEqual(self._callers('_work_schedule_shift_editor_codes'),
                         {'_resolve_work_schedule_shift_editor', '_resolve_shift_target_for_requester'})

    def test_statuses_and_line_work_keep_their_gates(self):
        editor = self._callers('_resolve_work_schedule_editor')
        self.assertIn('save_work_schedule_status_period', editor)
        self.assertIn('delete_work_schedule_status_period', editor)
        manager = self._callers('_resolve_management_requester')
        for name in ('add_work_schedule_fine', 'remove_work_schedule_dismissal_blacklist',
                     'recalculate_work_schedule_breaks', 'save_work_schedule_break_rules'):
            self.assertIn(name, manager)

    def test_bulk_actions_know_only_shifts_and_days_off(self):
        source = (ROOT / 'db' / 'schedules.py').read_text(encoding='utf-8')
        start = source.index('def apply_work_schedule_bulk_actions')
        body = source[start:source.index('\n    def ', start + 10)]
        self.assertEqual(sorted(set(re.findall(r"action_type == '(\w+)'", body))),
                         ['delete_shifts', 'set_day_off', 'set_shift'])
        self.assertIn('Unsupported action type', body)


class SiteMirrorTests(unittest.TestCase):
    def test_site_holds_the_same_grants(self):
        app = APP.read_text(encoding='utf-8')
        match = re.search(r'^const WORK_SCHEDULE_SHIFT_EDITOR_GRANTS = Object\.freeze\(\{(.*)\}\);$', app, re.M)
        self.assertIsNotNone(match, 'зеркало списка в App.jsx не найдено')
        site = {int(uid): tuple(re.findall(r"'(\w+)'", codes))
                for uid, codes in re.findall(r"(\d+): Object\.freeze\(\[([^\]]*)\]\)", match.group(1))}
        self.assertEqual(site, {uid: tuple(codes) for uid, codes in REAL_GRANTS.items()})


if __name__ == '__main__':
    unittest.main()
