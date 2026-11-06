# -*- coding: utf-8 -*-
"""Первый балл переводит задачу из «Новой» в бэклог, снятый — обратно (владелец 06.11.2026).

«Новая» — то, что ещё не разобрали на совещании суперадминов, бэклог — очередь уже
оценённых. Снимают балл в карточке задачи — и задача снова ждёт разбора в «Новой». Проверяем поведением: настоящий Database.update_task_board_state (вытащен
AST'ом из склейки database.py и db/*.py, как в соседних тестах) на поддельном курсоре,
который отвечает строкой задачи и запоминает запросы. Базы не нужно — идёт и в CI.
"""
import ast
import contextlib
import json
import re
import textwrap
import unittest
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from tests import source_cache

DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')

SUPER_ADMIN, ADMIN, CREATOR, ASSIGNEE = 1, 2, 3, 4
NEXT_RANK = 8  # что ответит «SELECT COALESCE(MAX(backlog_rank), 0) + 1»

METHODS = ['update_task_board_state', '_normalize_task_priority_score']
CLASS_ATTRS = ['TASK_PRIORITY_SCORES']
MODULE_NAMES = {'normalize_role_value', 'role_has_min', 'ROLE_ALIASES', 'ROLE_HIERARCHY', '_UNSET'}

STUBS = '''
@contextlib.contextmanager
def _get_cursor(self):
    yield self.cursor

def _task_assignee_scope_tx(self, cursor, task_id):
    return [(ASSIGNEE, 'operator', None)]

def _task_visible_for_requester(self, role, requester_id, created_by, assignee_scope, requested_by):
    return True

def _normalize_task_estimate_minutes(self, value):
    return value

def _normalize_task_reminder_minutes(self, value):
    return value

def _parse_task_datetime(self, value, code):
    return value

def _task_now(self):
    return datetime(2026, 11, 6, 12, 0)

def _task_dt_to_iso(self, value):
    return value.isoformat() if value else None
'''


def _build_database_class():
    module = source_cache.parse(DATABASE_SOURCE)
    namespace = {'contextlib': contextlib, 'datetime': datetime, 'ASSIGNEE': ASSIGNEE,
                 'Optional': Optional, 'List': List}
    for node in module.body:
        names = ([node.name] if isinstance(node, ast.FunctionDef)
                 else [t.id for t in getattr(node, 'targets', []) if isinstance(t, ast.Name)])
        if set(names) & MODULE_NAMES:
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    database_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    parts = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in METHODS:
            parts[node.name] = textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CLASS_ATTRS:
                    parts[target.id] = textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node))
    missing = set(METHODS + CLASS_ATTRS) - set(parts)
    assert not missing, f'в Database нет: {sorted(missing)}'
    body = ''.join(textwrap.indent(parts[name], '    ') + '\n' for name in CLASS_ATTRS + METHODS)
    exec('class FakeDatabase:\n' + body + textwrap.indent(STUBS, '    '), namespace)
    return namespace['FakeDatabase']


FakeDatabase = _build_database_class()


def _select_columns(sql):
    """Колонки SELECT по порядку — строку задачи собираем по именам, а не по позициям."""
    head = sql[sql.index('SELECT') + len('SELECT'):sql.index('FROM tasks t')]
    return [part.strip() for part in head.replace('\n', ' ').split(',') if part.strip()]


class FakeCursor:
    def __init__(self, task):
        self.task = task
        self.queries = []
        self._next = None

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if 'FROM tasks t' in sql and 'WHERE t.id = %s' in sql:
            self._next = tuple(self.task[re.sub(r'^t\.', '', column)] for column in _select_columns(sql))
        elif 'MAX(backlog_rank)' in sql:
            self._next = (NEXT_RANK,)
        else:
            self._next = None

    def fetchone(self):
        return self._next

    def updates(self):
        return [sql for sql, _params in self.queries if sql.lstrip().startswith('UPDATE tasks')]


def _task(**overrides):
    task = {
        'id': 77, 'created_by': CREATOR, 'assigned_to': ASSIGNEE, 'status': 'assigned',
        'created_at': datetime(2026, 11, 1, 9, 0), 'is_backlog': False, 'backlog_rank': None,
        'estimate_minutes': None, 'planned_start_at': None, 'due_at': None,
        'deadline_duration_minutes': None, 'subject': 'Проверка', 'reminder_minutes_before': None,
        'requested_by_id': None, 'priority_score': None, 'is_regulation': False,
        'regulation_parent_id': None,
    }
    task.update(overrides)
    return task


def _run(task, role='super_admin', requester=SUPER_ADMIN, **changes):
    db = FakeDatabase()
    db.cursor = FakeCursor(task)
    result = db.update_task_board_state(task['id'], requester, role, **changes)
    return result, db.cursor


class FirstScoreMovesToBacklogTests(unittest.TestCase):
    def test_first_score_moves_new_task_to_end_of_backlog(self):
        result, cursor = _run(_task(), priority_score=2)
        self.assertTrue(result['is_backlog'])
        self.assertFalse(result['was_backlog'])
        self.assertEqual(result['score_move'], 'to_backlog')
        self.assertEqual(result['priority_score'], 2)
        self.assertEqual(result['backlog_rank'], float(NEXT_RANK))
        self.assertEqual(set(result['changed_fields']), {'is_backlog', 'backlog_rank', 'priority_score'})
        self.assertEqual(len(cursor.updates()), 1)

    def test_stale_rank_from_earlier_backlog_is_replaced(self):
        # Ранг остался от прежнего захода в бэклог — оценённая встаёт в конец, а не на старое место.
        result, _cursor = _run(_task(backlog_rank=3.5), priority_score=1)
        self.assertTrue(result['is_backlog'])
        self.assertEqual(result['backlog_rank'], float(NEXT_RANK))

    def test_score_string_from_cli_counts_too(self):
        result, _cursor = _run(_task(), priority_score='4')
        self.assertTrue(result['is_backlog'])
        self.assertEqual(result['priority_score'], 4)

    def test_regulation_occurrence_moves_like_any_task(self):
        result, _cursor = _run(_task(is_regulation=True, regulation_parent_id=5), priority_score=3)
        self.assertTrue(result['is_backlog'])


class ScoreKeepsPlaceTests(unittest.TestCase):
    def test_regulation_template_stays_on_board(self):
        # В бэклоге шаблон стоит на паузе: materialize_due_regulation_tasks его не берёт.
        result, _cursor = _run(_task(is_regulation=True, regulation_parent_id=None), priority_score=1)
        self.assertFalse(result['is_backlog'])
        self.assertEqual(result['priority_score'], 1)
        self.assertEqual(result['changed_fields'], ['priority_score'])

    def test_rescoring_scored_new_task_does_not_move_it(self):
        # Оценённая и вынесенная из бэклога кнопкой «В работу» — назад её балл не тянет.
        result, _cursor = _run(_task(priority_score=3), priority_score=1)
        self.assertFalse(result['is_backlog'])
        self.assertIsNone(result['score_move'])
        self.assertEqual(result['changed_fields'], ['priority_score'])

    def test_clearing_score_does_not_move(self):
        result, cursor = _run(_task(), priority_score=None)
        self.assertFalse(result['is_backlog'])
        self.assertFalse(result['updated'])
        self.assertEqual(cursor.updates(), [])

    def test_backlog_task_keeps_its_rank(self):
        result, _cursor = _run(_task(is_backlog=True, backlog_rank=2.0, priority_score=3), priority_score=5)
        self.assertTrue(result['is_backlog'])
        self.assertEqual(result['backlog_rank'], 2.0)
        self.assertIsNone(result['score_move'])
        self.assertEqual(result['changed_fields'], ['priority_score'])

    def test_started_task_only_gets_score(self):
        for status in ('in_progress', 'returned', 'completed', 'accepted'):
            with self.subTest(status=status):
                result, _cursor = _run(_task(status=status), priority_score=2)
                self.assertFalse(result['is_backlog'])
                self.assertEqual(result['changed_fields'], ['priority_score'])

    def test_explicit_backlog_flag_wins(self):
        # Балл и «на доску» одним пакетом: явное решение важнее правила.
        result, _cursor = _run(_task(), priority_score=2, is_backlog=False)
        self.assertFalse(result['is_backlog'])
        self.assertEqual(result['priority_score'], 2)


class ClearedScoreReturnsToNewTests(unittest.TestCase):
    """Снятый в карточке задачи балл возвращает её из бэклога в «Новую» — снова к разбору."""

    def test_cleared_score_returns_backlog_task_to_new(self):
        result, cursor = _run(_task(is_backlog=True, backlog_rank=4.0, priority_score=2), priority_score=None)
        self.assertFalse(result['is_backlog'])
        self.assertTrue(result['was_backlog'])
        self.assertEqual(result['score_move'], 'to_new')
        self.assertIsNone(result['priority_score'])
        # Ранг остаётся: при новом балле задача всё равно встанет в конец своего балла.
        self.assertEqual(result['backlog_rank'], 4.0)
        self.assertEqual(set(result['changed_fields']), {'is_backlog', 'priority_score'})
        self.assertEqual(len(cursor.updates()), 1)

    def test_empty_string_from_form_clears_too(self):
        result, _cursor = _run(_task(is_backlog=True, priority_score=1), priority_score='')
        self.assertFalse(result['is_backlog'])
        self.assertEqual(result['score_move'], 'to_new')

    def test_template_stays_paused_in_backlog(self):
        # Вне бэклога шаблон ожил бы и сразу догнал пропущенные повторы.
        result, _cursor = _run(_task(is_backlog=True, priority_score=2, is_regulation=True,
                                     regulation_parent_id=None), priority_score=None)
        self.assertTrue(result['is_backlog'])
        self.assertIsNone(result['score_move'])

    def test_cleared_score_outside_backlog_only_clears(self):
        for status in ('assigned', 'in_progress', 'returned', 'completed', 'accepted'):
            with self.subTest(status=status):
                result, _cursor = _run(_task(status=status, priority_score=4), priority_score=None)
                self.assertFalse(result['is_backlog'])
                self.assertIsNone(result['score_move'])
                self.assertEqual(result['changed_fields'], ['priority_score'])

    def test_explicit_backlog_flag_wins_over_clearing(self):
        result, _cursor = _run(_task(is_backlog=True, priority_score=3), priority_score=None, is_backlog=True)
        self.assertTrue(result['is_backlog'])
        self.assertIsNone(result['score_move'])

    def test_board_route_does_not_announce_return_to_new(self):
        # Вынос из бэклога шлёт исполнителю «вынесли в работу»; возврат в «Новую» без
        # балла — это снова к разбору, а не в работу.
        app = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        start = app.index("def handle_tasks_board():")
        block = app[start:app.index("\n@app.route(", start)]
        self.assertIn("and result.get('score_move') != 'to_new'):", block)
        self.assertLess(block.index("and result.get('score_move') != 'to_new'):"),
                        block.index("promoted.append(result)"))


SHARED_CASES = json.loads(
    (Path(__file__).resolve().parent / 'fixtures' / 'score_moves.json').read_text(encoding='utf-8')
)['cases']
SERVER_MOVE = {'backlog': 'to_backlog', 'new': 'to_new', None: None}


class SharedRuleTableTests(unittest.TestCase):
    """Та же таблица, что у фронта (tests/backlog_order.test.mjs): правило живёт в двух местах."""

    def test_server_moves_like_the_board(self):
        self.assertGreater(len(SHARED_CASES), 10)
        for case in SHARED_CASES:
            with self.subTest(case=case['name']):
                result, _cursor = _run(_task(**case['task']), priority_score=case['score'])
                self.assertEqual(result['score_move'], SERVER_MOVE[case['move']])
                expected_backlog = {'backlog': True, 'new': False}.get(
                    case['move'], bool(case['task'].get('is_backlog', False)))
                self.assertEqual(result['is_backlog'], expected_backlog)

    def test_first_score_inside_backlog_goes_to_the_end_of_its_score(self):
        # Положили в бэклог без балла руками (ранг 2) — оценили там: в конец своего балла.
        result, _cursor = _run(_task(is_backlog=True, backlog_rank=2.0), priority_score=3)
        self.assertTrue(result['is_backlog'])
        self.assertIsNone(result['score_move'])
        self.assertEqual(result['backlog_rank'], float(NEXT_RANK))

    def test_explicit_rank_wins_over_the_end_of_the_queue(self):
        result, _cursor = _run(_task(), priority_score=2, backlog_rank=0.5)
        self.assertTrue(result['is_backlog'])
        self.assertEqual(result['backlog_rank'], 0.5)

    def test_task_row_is_read_under_lock(self):
        # Иначе балл в ту секунду, когда задачу взяли в работу, вернул бы её в бэклог.
        db = FakeDatabase()
        db.cursor = FakeCursor(_task())
        db.update_task_board_state(77, SUPER_ADMIN, 'super_admin', priority_score=2)
        select = next(sql for sql, _params in db.cursor.queries if 'WHERE t.id = %s' in sql)
        self.assertIn('FOR UPDATE OF t', select)


class ScoreRightsTests(unittest.TestCase):
    def test_admin_below_super_admin_cannot_score(self):
        with self.assertRaisesRegex(PermissionError, 'ONLY_SUPER_ADMIN_SCORE'):
            _run(_task(), role='admin', requester=ADMIN, priority_score=1)

    def test_creator_can_still_drag_to_backlog_without_score(self):
        result, _cursor = _run(_task(), role='supervisor', requester=CREATOR, is_backlog=True)
        self.assertTrue(result['is_backlog'])
        self.assertIsNone(result['priority_score'])


if __name__ == '__main__':
    unittest.main()
