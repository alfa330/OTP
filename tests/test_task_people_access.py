"""Лента «Моих задач» — только люди, которым открыт раздел «Задачи» (17.11.2026).

Владелец: «выводить здесь людей, у которых есть доступ к разделу задачи» —
человек бывает постановщиком или исполнителем чужих задач, сам в раздел не
попадая. Кому раздел открыт, решает гард _can_access_tasks (bot_schedule2.py);
ленте нужен тот же ответ сразу для десятков людей, поэтому рядом с ним стоит
пакетный _task_access_for_people: отделы и главы — одним запросом на всех.
Два места одного правила держатся вместе здесь: обе функции гоняются по одной
таблице ролей, отделов и глав.
"""
import ast
import logging
import textwrap
import unittest
from itertools import product
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = source_cache.monolith_flat_path()
ROUTES = (ROOT / 'tasks' / 'routes.py').read_text(encoding='utf-8')
DEPARTMENTS = (ROOT / 'db' / 'departments.py').read_text(encoding='utf-8')
VIEW = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
BOT = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')


def _read(path):
    return Path(path).read_text(encoding='utf-8')


def _function_source(path, function_name):
    source = _read(path)
    module = source_cache.parse(source)
    node = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


def _assignment_source(path, name):
    source = _read(path)
    module = source_cache.parse(source)
    node = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    return ast.get_source_segment(source, node)


ROLES = ('super_admin', 'admin', 'superadmin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
         'hr_manager', 'accounting_manager', 'marketing_manager', 'unknown', '', None)
CODES = ('szov', 'hr', 'accounting', 'marketing', 'it', ' IT ', 'remote_cc', None)


class AccessRuleMirrorTests(unittest.TestCase):
    """Пакетное правило отвечает так же, как гард раздела, — на всей таблице."""

    def _functions(self, department_by_user, heads):
        class _Db:
            def get_user_department(self, user_id):
                return (1, department_by_user.get(int(user_id)))

            def get_task_access_facts(self, user_ids):
                return {int(uid): (department_by_user.get(int(uid)), int(uid) in heads) for uid in user_ids}

        namespace = {
            'db': _Db(),
            'logging': logging,
            '_headed_department_id': lambda requester_id: 7 if requester_id in heads else None,
        }
        for name in ('ROLE_HIERARCHY', 'BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE', 'BACK_OFFICE_EMPLOYEE_ROLES',
                     'BACK_OFFICE_TASK_EMPLOYEE_ROLES', 'TASK_EMPLOYEE_DEPARTMENT_CODES'):
            exec(_assignment_source(BOT_PATH, name), namespace)
        for name in ('_normalize_user_role', '_get_role_level', '_has_min_role', '_is_admin_role',
                     '_back_office_task_employee_role', '_can_access_tasks', '_task_access_for_people'):
            exec(_function_source(BOT_PATH, name), namespace)
        return namespace['_can_access_tasks'], namespace['_task_access_for_people']

    def test_batch_matches_the_guard_everywhere(self):
        cases = list(product(ROLES, CODES, (False, True)))
        department_by_user = {}
        heads = set()
        role_by_id = {}
        for index, (role, code, is_head) in enumerate(cases, start=1):
            role_by_id[index] = role
            department_by_user[index] = code
            if is_head:
                heads.add(index)
        can_access, batch = self._functions(department_by_user, heads)
        allowed = batch(role_by_id)
        self.assertIsNotNone(allowed)
        mismatches = [
            (role, code, is_head)
            for index, (role, code, is_head) in enumerate(cases, start=1)
            if (index in allowed) != bool(can_access(role, index))
        ]
        self.assertEqual(mismatches, [])
        # Таблица не вырождена: есть и пропущенные, и не пропущенные.
        self.assertTrue(0 < len(allowed) < len(cases))

    def test_known_cases(self):
        department_by_user = {1: 'szov', 2: 'it', 3: 'hr', 4: 'szov', 5: 'szov'}
        _, batch = self._functions(department_by_user, heads={5})
        allowed = batch({1: 'operator', 2: 'operator', 3: 'operator', 4: 'trainer', 5: 'operator'})
        # Оператор линии — нет, хоть и есть в чужих задачах; IT и HR по отделу — да;
        # тренер по роли — да; глава отдела — да при любой роли.
        self.assertEqual(allowed, {2, 3, 4, 5})

    def test_unknown_facts_hide_nobody(self):
        class _Broken:
            def get_task_access_facts(self, user_ids):
                raise RuntimeError('база недоступна')

        namespace = {'db': _Broken(), 'logging': logging}
        for name in ('ROLE_HIERARCHY', 'BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE', 'BACK_OFFICE_EMPLOYEE_ROLES',
                     'BACK_OFFICE_TASK_EMPLOYEE_ROLES', 'TASK_EMPLOYEE_DEPARTMENT_CODES'):
            exec(_assignment_source(BOT_PATH, name), namespace)
        for name in ('_normalize_user_role', '_get_role_level', '_has_min_role', '_is_admin_role',
                     '_task_access_for_people'):
            exec(_function_source(BOT_PATH, name), namespace)
        logging.disable(logging.CRITICAL)
        try:
            self.assertIsNone(namespace['_task_access_for_people']({1: 'operator'}))
        finally:
            logging.disable(logging.NOTSET)


class AccessWiringTests(unittest.TestCase):
    def test_facts_use_the_guard_predicates(self):
        """Главы и отдел — с теми же условиями, что у headed_department_id_for_user
        и get_user_department."""
        facts = DEPARTMENTS[DEPARTMENTS.index('    def get_task_access_facts('):DEPARTMENTS.index('    def get_user_department(self')]
        self.assertIn('WHERE headed.head_user_id = u.id', facts)
        self.assertIn('AND COALESCE(headed.is_active, TRUE) = TRUE', facts)
        self.assertIn('LEFT JOIN departments d ON d.id = u.department_id', facts)
        self.assertIn('WHERE u.id = ANY(%s)', facts)

    def test_route_marks_people_once_per_response(self):
        """Один пакет на ответ; признак — у постановщика, первого исполнителя и всего
        состава; не узнали (None) — признака нет."""
        listing = ROUTES[ROUTES.index('tasks = payload.get("tasks") or []'):ROUTES.index('total_filtered = int(payload.get("total_filtered"')]
        self.assertEqual(listing.count('_task_access_for_people('), 1)
        self.assertIn('if people_with_access is not None:', listing)
        self.assertIn('person["has_tasks_access"] = int(person["id"]) in people_with_access', listing)
        self.assertIn('[task.get("creator"), task.get("assignee"), *(task.get("assignees") or [])]', listing)
        self.assertIn('    _task_access_for_people,\n', ROUTES)
        self.assertIn('        _task_access_for_people=_task_access_for_people,\n', BOT)

    def test_strip_shows_only_people_with_access(self):
        grouping = VIEW[VIEW.index('const groupTasksByPerson = '):VIEW.index('/* ─── Icons ─── */')]
        self.assertIn('const visible = people.filter((person) => !person?.is_fired && person?.has_tasks_access !== false);', grouping)
        self.assertIn('if (people.length && !visible.length) return;', grouping)
        self.assertIn('(visible.length ? visible : [{}]).forEach(', grouping)


if __name__ == '__main__':
    unittest.main()
