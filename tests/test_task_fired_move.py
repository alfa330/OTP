# -*- coding: utf-8 -*-
"""Карточки уволенных (исполнитель, постановщик или поручитель): суперадмин переносит их в любую колонку доски.

Здесь — ручка POST /api/tasks/<id>/move и проводка фронта. Ручку гоняем НАСТОЯЩУЮ:
её тело достаётся из tasks/routes.py AST'ом и выполняется в настоящем запросе
Flask с подставленной базой и гардом (импортировать tasks/routes.py тест не может —
модуль на импорте поднимает базу). Что метод пишет в базу — в
tests/test_task_fired_move_postgres.py, правило доски — в tests/board_columns.test.mjs.
"""
import ast
import copy
import logging
import textwrap
import unittest
from pathlib import Path

from flask import Flask, g, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
ROUTES_PATH = ROOT / 'tasks' / 'routes.py'
ROUTES_SOURCE = ROUTES_PATH.read_text(encoding='utf-8')
TASKS_VIEW = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
WORKSPACE = (ROOT / 'src' / 'components' / 'tasks' / 'TaskBoardWorkspace.jsx').read_text(encoding='utf-8')


def _route_namespace():
    module = ast.parse(ROUTES_SOURCE)
    namespace = {'jsonify': jsonify, 'request': request, 'g': g, 'logging': logging}
    errors = next(node for node in module.body if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'TASK_FIRED_MOVE_ERRORS' for t in node.targets))
    exec(ast.get_source_segment(ROUTES_SOURCE, errors), namespace)
    builder = next(node for node in module.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'build_tasks_blueprint')
    route = copy.deepcopy(next(node for node in builder.body
                               if isinstance(node, ast.FunctionDef) and node.name == 'move_fired_employee_task'))
    decorators = [ast.unparse(item) for item in route.decorator_list]
    route.decorator_list = []
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(ROUTES_PATH), 'exec'), namespace)
    return namespace, decorators


class FakeDb:
    def __init__(self):
        self.calls = []
        self.error = None
        self.result = {'task_id': 5, 'column': 'done', 'status': 'accepted', 'is_backlog': False,
                       'backlog_rank': None, 'updated': True, 'history_id': 9}

    def move_fired_employee_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class MoveRouteTest(unittest.TestCase):
    def setUp(self):
        self.namespace, self.decorators = _route_namespace()
        self.db = FakeDb()
        self.guard_calls = 0
        self.guard_response = None
        self.namespace['db'] = self.db
        self.namespace['_task_route_guard'] = self._guard
        self.app = Flask(__name__)

    def _guard(self):
        self.guard_calls += 1
        if self.guard_response is not None:
            return None, None, jsonify({'error': 'нет доступа'}), 403
        g.effective_task_role = 'super_admin'
        return 77, (77, None, 'Суперадмин', 'admin'), None, None

    def _post(self, body=None, data=None):
        kwargs = {'method': 'POST'}
        if data is not None:
            kwargs.update(data=data, content_type='application/json')
        elif body is not None:
            kwargs['json'] = body
        with self.app.test_request_context('/api/tasks/5/move', **kwargs):
            response = self.namespace['move_fired_employee_task'](5)
            response, status = response if isinstance(response, tuple) else (response, 200)
            return status, response.get_json()

    def test_route_is_registered_behind_api_key(self):
        self.assertEqual(self.decorators[0], "bp.route('/tasks/<int:task_id>/move', methods=['POST', 'OPTIONS'])")
        self.assertIn('require_api_key', self.decorators)

    def test_moves_with_guard_role_and_column(self):
        status, payload = self._post({'column': 'done'})
        self.assertEqual(status, 200)
        self.assertEqual(payload['task'], self.db.result)
        self.assertEqual(payload['message'], 'Задача перенесена')
        # Роль — та, что положил гард (effective_task_role), а не из карточки пользователя.
        self.assertEqual(self.db.calls, [{'task_id': 5, 'requester_id': 77, 'requester_role': 'super_admin',
                                          'column': 'done'}])

    def test_same_column_is_not_an_error(self):
        self.db.result = dict(self.db.result, updated=False, history_id=None)
        status, payload = self._post({'column': 'done'})
        self.assertEqual((status, payload['message']), (200, 'Задача уже в этой колонке'))

    def test_guard_refusal_stops_before_database(self):
        self.guard_response = True
        status, payload = self._post({'column': 'done'})
        self.assertEqual((status, payload), (403, {'error': 'нет доступа'}))
        self.assertEqual(self.db.calls, [])

    def test_database_refusals_map_to_codes(self):
        cases = [
            (PermissionError('ONLY_SUPER_ADMIN_MOVES'), 403, 'Карточки уволенных переносит только суперадмин'),
            (PermissionError('NO_FIRED_EMPLOYEE'), 409, 'В задаче нет уволенных — её двигают по обычным правилам'),
            (ValueError('INVALID_COLUMN'), 400, 'Неизвестная колонка доски'),
            (ValueError('TASK_NOT_FOUND'), 404, 'Task not found'),
            # Незнакомый отказ не становится 500: код по виду ошибки.
            (PermissionError('SOMETHING_NEW'), 403, 'SOMETHING_NEW'),
            (ValueError('SOMETHING_ELSE'), 400, 'SOMETHING_ELSE'),
        ]
        for error, code, message in cases:
            with self.subTest(error=str(error)):
                self.db.error = error
                self.assertEqual(self._post({'column': 'done'}), (code, {'error': message}))

    def test_body_that_is_not_an_object_reaches_database_without_column(self):
        # Тело-список или пустое — колонки нет; метод базы отвечает INVALID_COLUMN.
        self.db.error = ValueError('INVALID_COLUMN')
        for kwargs in ({'data': '[1, 2]'}, {'data': 'не json'}, {}):
            with self.subTest(kwargs=kwargs):
                self.db.calls.clear()
                status, payload = self._post(**kwargs)
                self.assertEqual(status, 400)
                self.assertEqual(self.db.calls[0]['column'], None)

    def test_unexpected_failure_is_internal_error(self):
        self.db.error = RuntimeError('boom')
        self.assertEqual(self._post({'column': 'done'}), (500, {'error': 'Internal server error'}))


class FrontWiringTest(unittest.TestCase):
    """Проводка: доска получает право суперадмина и ручку, ручка зовёт /move."""

    def test_tasks_view_passes_super_admin_and_move_handler(self):
        self.assertIn("isSuperAdmin={normalizeRole(currentUserRole) === 'super_admin'}", TASKS_VIEW)
        self.assertIn('onMoveTask={moveBoardTask}', TASKS_VIEW)
        start = TASKS_VIEW.index('const moveBoardTask = useCallback(')
        block = TASKS_VIEW[start:TASKS_VIEW.index('}, [apiBaseUrl, buildHeaders, notify, refreshTasksData]);', start)]
        # Без заголовков авторизации ручка отвечает 401 — перенос не работал бы вовсе.
        self.assertIn('`${apiBaseUrl}/api/tasks/${taskId}/move`,\n        { column },\n        { headers: buildHeaders() }', block)
        # Записали — колонки и счётчики перечитываются, доска сверяет правку с ответом.
        self.assertIn('await refreshTasksData();\n      return res?.data?.task || false;', block)
        # Отказ сервера виден человеку, доска снимает свою правку и перечитывается:
        # иначе карточка, чьего исполнителя вернули на работу, так и звала бы /move.
        catch = block[block.index('} catch (e) {'):]
        self.assertIn("notify(e?.response?.data?.error || 'Не удалось перенести задачу', 'error');", catch)
        self.assertIn('refreshTasksData().catch(() => {});\n      return false;', catch)

    def test_board_runs_move_decision_through_handler(self):
        self.assertIn("if (decision.type === 'move') {\n      applyMove(task, decision.column);", WORKSPACE)
        # Правка и запрос — по колонке броска (sendBoardMove проверен в board_columns.test.mjs).
        self.assertIn('task, BOARD_COLUMN_STATE[column], () => sendBoardMove(onMoveTask, task, column)', WORKSPACE)
        self.assertIn("type === 'board' || type === 'status' || type === 'move'", WORKSPACE)
        # Подсветка колонок и бросок решаются одним правилом с правом суперадмина.
        self.assertIn('resolve: (toColumn) => resolveBoardDrop(task, toColumn, dropContext)', WORKSPACE)
        self.assertIn('const decision = resolveBoardDrop(task, toColumn, dropContext);', WORKSPACE)
        # Без ручки переноса правило не включается: иначе бросок вёл бы в никуда.
        self.assertIn("isSuperAdmin: isSuperAdmin && typeof onMoveTask === 'function'", WORKSPACE)

    def test_moved_card_keeps_its_row_while_columns_reload(self):
        # Строки доски собирает mergeBoardRows (tests/board_columns.test.mjs) — с
        # прежней строкой карточки, которую запомнила оптимистичная правка.
        self.assertIn('return mergeBoardRows(columnState, overrides, keptRowsRef.current);', WORKSPACE)
        start = WORKSPACE.index('const applyOptimistic = useCallback(')
        block = WORKSPACE[start:WORKSPACE.index('const applyBoardPatch = useCallback(', start)]
        self.assertIn('keptRowsRef.current.set(String(task.id), task);', block)
        # Смена выборки снимает правки: иначе их строки показали бы чужие карточки.
        start = WORKSPACE.index('const resetColumns = useCallback(')
        block = WORKSPACE[start:WORKSPACE.index('}, []);', start)]
        self.assertIn('setOverrides({});', block)


if __name__ == '__main__':
    unittest.main()
