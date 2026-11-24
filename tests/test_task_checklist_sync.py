# -*- coding: utf-8 -*-
"""Правка задачи не снимает отметок чек-листа (HR-2, 24.11.2026).

Прежде правка задачи удаляла все пункты чек-листа и вставляла их заново: добавили
исполнителя, и все галочки пропали, а у пунктов сменились id. Галочка из вкладки,
открытой до правки, падала «пункт не найден», после обновления страницы отмеченного
не было. Плюс у двух исполнителей HR-2 в карточке неверный Telegram, и каждая
галочка заканчивалась красной плашкой «не удалось отправить уведомление», хотя
отметка сохранилась: человек жал ещё раз и снимал её.

Здесь — правило сопоставления пунктов (task_checklist.plan_sync), что id пункта
доходит до него из тела запроса и через нормализацию базы, и НАСТОЯЩАЯ ручка
отметки (тело из tasks/routes.py AST'ом, в запросе Flask, база и Telegram
подставлены). Что SQL делает со строками — tests/test_task_checklist_sync_postgres.py
(нужен локальный Postgres), что шлёт форма — tests/task_composer_payload.test.mjs.
"""
import ast
import copy
import json
import logging
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, g, jsonify, request

import task_checklist
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
ROUTES_PATH = ROOT / 'tasks' / 'routes.py'
ROUTES_SOURCE = ROUTES_PATH.read_text(encoding='utf-8')
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding='utf-8-sig')
APP_SOURCE = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')

# Чек-лист HR-2 на проде 24.11.2026: восемь пунктов, четыре отмечены.
HR2 = ['диспетчерская', 'бэкенд', 'корп почта', 'платрум', 'iCore', 'обходной', 'прокси', 'акт приема-передачи']


def _current(titles, first_id=117, positions=None):
    return [
        {'id': first_id + index, 'title': title,
         'position': positions[index] if positions else index, 'is_required': True}
        for index, title in enumerate(titles)
    ]


def _incoming(titles, ids=None):
    items = []
    for index, title in enumerate(titles):
        item = {'title': title, 'is_required': True}
        if ids and ids[index]:
            item['id'] = ids[index]
        items.append(item)
    return items


class PlanSyncTest(unittest.TestCase):
    def test_same_checklist_from_old_form_without_ids_changes_nothing(self):
        # Ровно случай HR-2: добавили исполнителя, форма прислала тот же чек-лист
        # без id. Ни одна строка не должна ни удалиться, ни вставиться.
        plan = task_checklist.plan_sync(_current(HR2), _incoming(HR2))
        self.assertFalse(plan['changed'])
        self.assertEqual(plan['insert'], [])
        self.assertEqual(plan['delete'], [])
        self.assertEqual([item['id'] for item in plan['keep']], list(range(117, 125)))
        self.assertFalse(any(item['content_changed'] or item['moved'] for item in plan['keep']))

    def test_same_checklist_with_ids_changes_nothing(self):
        plan = task_checklist.plan_sync(_current(HR2), _incoming(HR2, ids=list(range(117, 125))))
        self.assertFalse(plan['changed'])

    def test_title_match_ignores_case_and_spaces(self):
        plan = task_checklist.plan_sync(_current(['iCore']), _incoming(['  icore ']))
        self.assertEqual([item['id'] for item in plan['keep']], [117])
        self.assertEqual(plan['insert'], [])
        # Название в другом регистре — правка названия, но тот же пункт с отметкой.
        self.assertTrue(plan['keep'][0]['content_changed'])
        self.assertEqual(plan['keep'][0]['title'], 'icore')

    def test_renamed_item_with_id_stays_the_same_item(self):
        plan = task_checklist.plan_sync(
            _current(['корп почта', 'прокси']), _incoming(['корпоративная почта', 'прокси'], ids=[117, 118]))
        self.assertTrue(plan['changed'])
        self.assertEqual(plan['insert'], [])
        self.assertEqual(plan['delete'], [])
        self.assertEqual(plan['keep'][0], {
            'id': 117, 'title': 'корпоративная почта', 'position': 0, 'is_required': True,
            'content_changed': True, 'moved': False,
        })
        self.assertFalse(plan['keep'][1]['content_changed'])

    def test_required_flag_change_is_a_content_change(self):
        plan = task_checklist.plan_sync(_current(['прокси']), [{'title': 'прокси', 'is_required': False}])
        self.assertTrue(plan['changed'])
        self.assertEqual((plan['keep'][0]['id'], plan['keep'][0]['is_required']), (117, False))

    def test_reorder_moves_items_without_reinserting(self):
        plan = task_checklist.plan_sync(_current(['а', 'б', 'в']), _incoming(['в', 'а', 'б']))
        self.assertTrue(plan['changed'])
        self.assertEqual([(item['id'], item['position']) for item in plan['keep']], [(119, 0), (117, 1), (118, 2)])
        self.assertTrue(all(item['moved'] for item in plan['keep']))
        self.assertEqual((plan['insert'], plan['delete']), ([], []))

    def test_gaps_in_stored_positions_with_same_order_are_not_a_change(self):
        # Порядок тот же, только номера мест в базе с пропуском: не правка чек-листа.
        plan = task_checklist.plan_sync(_current(['а', 'б', 'в'], positions=[0, 2, 5]), _incoming(['а', 'б', 'в']))
        self.assertFalse(plan['changed'])
        self.assertEqual([item['moved'] for item in plan['keep']], [False, True, True])

    def test_added_and_removed_items(self):
        plan = task_checklist.plan_sync(_current(['а', 'б', 'в']), _incoming(['а', 'в', 'г']))
        self.assertTrue(plan['changed'])
        self.assertEqual([item['id'] for item in plan['keep']], [117, 119])
        self.assertEqual(plan['insert'], [{'title': 'г', 'position': 2, 'is_required': True}])
        self.assertEqual(plan['delete'], [118])

    def test_empty_list_removes_whole_checklist(self):
        plan = task_checklist.plan_sync(_current(['а', 'б']), [])
        self.assertTrue(plan['changed'])
        self.assertEqual(plan['delete'], [117, 118])
        self.assertEqual((plan['keep'], plan['insert']), ([], []))

    def test_empty_both_is_no_change(self):
        self.assertFalse(task_checklist.plan_sync([], [])['changed'])

    def test_id_wins_over_title_of_another_item(self):
        # Пункт 117 «а» переименовали в «б», а рядом добавили новый «а»: 117 —
        # переименованный, «а» — новый пункт, а не 117 обратно.
        plan = task_checklist.plan_sync(_current(['а']), _incoming(['б', 'а'], ids=[117, None]))
        self.assertEqual([(item['id'], item['title']) for item in plan['keep']], [(117, 'б')])
        self.assertEqual(plan['insert'], [{'title': 'а', 'position': 1, 'is_required': True}])
        self.assertEqual(plan['delete'], [])

    def test_foreign_or_stale_id_falls_back_to_title(self):
        # id чужой задачи (или пункта, которого уже нет) не узнаётся, пункт ищется по названию.
        plan = task_checklist.plan_sync(_current(['а', 'б']), _incoming(['а', 'б'], ids=[9001, 9002]))
        self.assertFalse(plan['changed'])
        self.assertEqual([item['id'] for item in plan['keep']], [117, 118])

    def test_same_id_twice_is_claimed_once(self):
        plan = task_checklist.plan_sync(_current(['а', 'б']), _incoming(['а', 'б'], ids=[117, 117]))
        self.assertEqual([item['id'] for item in plan['keep']], [117, 118])
        self.assertFalse(plan['changed'])

    def test_bad_ids_are_ignored(self):
        for bad in ('x', -117, 0, None, '117.5'):
            with self.subTest(bad=bad):
                plan = task_checklist.plan_sync(_current(['а']), [{'title': 'а', 'is_required': True, 'id': bad}])
                self.assertEqual([item['id'] for item in plan['keep']], [117])
                self.assertFalse(plan['changed'])


def _module_function(source, name):
    module = ast.parse(source)
    node = next(item for item in module.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {'json': json}
    exec(ast.get_source_segment(source, node), namespace)
    return namespace[name]


def _database_method(name):
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
    node = next(item for item in database_class.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {}
    exec('class FakeDatabase:\n' + textwrap.indent(textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node)),
                                                   '    '), namespace)
    return getattr(namespace['FakeDatabase'](), name)


class ItemIdReachesPlanTest(unittest.TestCase):
    def test_route_parser_keeps_positive_ids(self):
        parse = _module_function(ROUTES_SOURCE, '_parse_task_checklist_items')
        raw = [
            {'id': 117, 'title': 'диспетчерская', 'is_required': True},
            {'id': '118', 'title': 'бэкенд'},
            {'id': -3, 'title': 'корп почта'},
            {'id': 'abc', 'title': 'платрум'},
            {'title': 'iCore', 'is_required': False},
            'обходной',
            {'id': 119, 'title': 'ДИСПЕТЧЕРСКАЯ'},  # повтор названия отбрасывается
        ]
        self.assertEqual(parse(raw), [
            {'title': 'диспетчерская', 'is_required': True, 'id': 117},
            {'title': 'бэкенд', 'is_required': True, 'id': 118},
            {'title': 'корп почта', 'is_required': True},
            {'title': 'платрум', 'is_required': True},
            {'title': 'iCore', 'is_required': False},
            {'title': 'обходной', 'is_required': True},
        ])
        # Мультипарт создания задачи шлёт чек-лист JSON-строкой — id в ней тоже доживает.
        self.assertEqual(parse(json.dumps([{'id': 5, 'title': 'а'}])), [{'title': 'а', 'is_required': True, 'id': 5}])

    def test_database_normalizer_keeps_ids_and_positions(self):
        normalize = _database_method('_normalize_task_checklist_items')
        self.assertEqual(normalize([{'id': 117, 'title': ' а '}, 'б', {'id': 0, 'title': 'в', 'is_required': False}]), [
            {'id': 117, 'title': 'а', 'position': 0, 'is_required': True},
            {'id': None, 'title': 'б', 'position': 1, 'is_required': True},
            {'id': None, 'title': 'в', 'position': 2, 'is_required': False},
        ])
        with self.assertRaises(ValueError):
            normalize('не список')

    def test_edit_syncs_instead_of_delete_and_insert(self):
        # Правка задачи больше не стирает строки чек-листа целиком.
        module = source_cache.parse(DATABASE_SOURCE)
        database_class = next(node for node in module.body
                              if isinstance(node, ast.ClassDef) and node.name == 'Database')
        edit = next(item for item in database_class.body if isinstance(item, ast.FunctionDef) and item.name == 'edit_task')
        body = ast.get_source_segment(DATABASE_SOURCE, edit)
        self.assertNotIn('DELETE FROM task_checklist_items WHERE task_id = %s"', body)
        self.assertNotIn('_insert_task_checklist_items', body)
        sync_call = 'if has_checklist and self._sync_task_checklist_items_tx(cursor, task_id, checklist_items):'
        self.assertEqual(body.count('_sync_task_checklist_items_tx('), 1)
        self.assertIn(sync_call + '\n                changed_fields.append("checklist")', body)
        # Сверка — до раннего выхода «ничего не поменялось» и до UPDATE tasks: правка одного
        # чек-листа — тоже правка, а тот же чек-лист без других изменений — не правка.
        self.assertLess(body.index(sync_call), body.index('if not changed_fields:'))
        self.assertLess(body.index(sync_call), body.index('UPDATE tasks\n'))


def _checklist_route_namespace():
    module = ast.parse(ROUTES_SOURCE)
    builder = next(node for node in module.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'build_tasks_blueprint')
    route = copy.deepcopy(next(node for node in builder.body
                               if isinstance(node, ast.FunctionDef) and node.name == 'update_task_checklist_item'))
    decorators = [ast.unparse(item) for item in route.decorator_list]
    route.decorator_list = []
    namespace = {'jsonify': jsonify, 'request': request, 'g': g, 'logging': logging}
    for name in ('_parse_task_bool', '_collect_task_notification_recipients'):
        node = next(item for item in module.body if isinstance(item, ast.FunctionDef) and item.name == name)
        exec(ast.get_source_segment(ROUTES_SOURCE, node), namespace)
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(ROUTES_PATH), 'exec'), namespace)
    return namespace, decorators


class FakeChecklistDb:
    def __init__(self):
        self.calls = []
        self.error = None

    def update_task_checklist_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {'id': kwargs['checklist_item_id'], 'task_id': kwargs['task_id'], 'title': 'корп почта',
                'is_done': kwargs['is_done'], 'completed_by': 40, 'completed_by_name': 'Постановщик'}


class ChecklistRouteTest(unittest.TestCase):
    """Отметка пункта: сохранено — значит успех, даже если чьё-то уведомление не дошло."""

    def setUp(self):
        self.namespace, self.decorators = _checklist_route_namespace()
        self.db = FakeChecklistDb()
        self.sent = []
        self.telegram_status = {}
        self.namespace.update({
            'db': self.db,
            '_task_route_guard': self._guard,
            # Постановщик без Telegram; у двух исполнителей неверный id чата, как на проде.
            '_fetch_task_notification_context': lambda task_id: {
                'key': 'HR-2', 'created_by': 40, 'creator_telegram_id': None, 'creator_name': 'Постановщик',
                'assignees': [
                    {'id': 41, 'telegram_id': 900000041, 'name': 'Исполнитель 1'},
                    {'id': 42, 'telegram_id': 7042, 'name': 'Исполнитель 2'},
                    {'id': 43, 'telegram_id': 7043, 'name': 'Исполнитель 3'},
                    {'id': 40, 'telegram_id': None, 'name': 'Постановщик'},
                ],
            },
            '_build_current_task_deep_link': lambda task_id, task_key=None: f'https://example.test/tasks/{task_key}',
            '_build_task_notification_reply_markup': lambda link: None,
            '_build_task_checklist_notification_html': lambda **kwargs: 'пункт отмечен',
            '_send_telegram_text_message': self._send,
            '_get_telegram_error_text': lambda response: 'Bad Request: chat not found',
            '_telegram_exception_text': str,
            '_truncate_for_telegram': lambda text, limit: str(text)[:limit],
        })
        self.app = Flask(__name__)

    def _guard(self):
        g.effective_task_role = 'hr_manager'
        return 40, (40, None, 'Постановщик', 'hr_manager'), None, None

    def _send(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.sent.append(chat_id)
        return SimpleNamespace(status_code=self.telegram_status.get(chat_id, 200))

    def _patch(self, body):
        with self.app.test_request_context('/api/tasks/458/checklist/119', method='PATCH', json=body):
            response = self.namespace['update_task_checklist_item'](458, 119)
            response, status = response if isinstance(response, tuple) else (response, 200)
            return status, response.get_json()

    def test_route_is_registered_behind_api_key(self):
        self.assertEqual(self.decorators[0],
                         "bp.route('/tasks/<int:task_id>/checklist/<int:item_id>', methods=['PATCH', 'OPTIONS'])")
        self.assertIn('require_api_key', self.decorators)

    def test_undelivered_notification_is_logged_not_returned(self):
        self.telegram_status = {7042: 400, 7043: 400}
        with self.assertLogs(level='WARNING') as logs:
            status, payload = self._patch({'is_done': True})
        self.assertEqual(status, 200)
        self.assertNotIn('warning', payload)
        self.assertEqual(payload['item']['is_done'], True)
        # Уведомления по-прежнему уходят всем, у кого есть Telegram, кроме самого отметившего.
        self.assertEqual(self.sent, [900000041, 7042, 7043])
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertIn('Task 458 checklist item 119', message)
        self.assertIn('Исполнитель 2', message)
        self.assertIn('Исполнитель 3', message)

    def test_notification_crash_does_not_fail_saved_mark(self):
        def crash(*args, **kwargs):
            raise RuntimeError('telegram down')
        self.namespace['_send_telegram_text_message'] = crash
        with self.assertLogs(level='WARNING'):
            status, payload = self._patch({'is_done': False})
        self.assertEqual(status, 200)
        self.assertNotIn('warning', payload)

    def test_missing_item_is_404_in_russian(self):
        self.db.error = ValueError('CHECKLIST_ITEM_NOT_FOUND')
        status, payload = self._patch({'is_done': True})
        self.assertEqual((status, payload), (404, {'error': 'Этого пункта в чек-листе уже нет: чек-лист задачи изменили'}))
        self.assertEqual(self.sent, [])

    def test_forbidden_is_403_in_russian(self):
        self.db.error = PermissionError('TASK_FORBIDDEN')
        self.assertEqual(self._patch({'is_done': True}), (403, {'error': 'Нет доступа к задаче'}))

    def test_unexpected_failure_is_500_in_russian(self):
        self.db.error = RuntimeError('boom')
        with self.assertLogs(level='ERROR'):
            status, payload = self._patch({'is_done': True})
        self.assertEqual((status, payload), (500, {'error': 'Не удалось отметить пункт чек-листа, попробуйте ещё раз'}))


class PinnedSnapshotTest(unittest.TestCase):
    def test_pinned_snapshot_keeps_checklist(self):
        # Быстрая правка закреплённой задачи собирает форму из слепка: без чек-листа
        # в нём добавленный пункт заменил бы все нынешние.
        start = APP_SOURCE.index('const compactPinnedTaskForStorage = (task) => {')
        block = APP_SOURCE[start:APP_SOURCE.index('\n};', start)]
        self.assertIn('checklist: Array.isArray(task.checklist)', block)
        for field in ('id: item.id,', "title: item.title || '',", 'is_required: item.is_required !== false,',
                      'is_done: Boolean(item.is_done),'):
            self.assertIn(field, block)

    def test_pinned_checklist_toggle_shows_no_red_warning(self):
        start = APP_SOURCE.index('const togglePinnedTaskChecklistItem = useCallback(')
        block = APP_SOURCE[start:APP_SOURCE.index('const appendPinnedTaskPayloadFormData', start)]
        self.assertNotIn('response?.data?.warning', block)
        # На 404 — один перечит: новый taskRefreshToken сам перечитывает закреплённую задачу.
        self.assertIn('if (error?.response?.status === 404) setTaskRefreshToken((prev) => prev + 1);', block)
        self.assertNotIn('refreshPinnedTaskPool', block)
        effect = APP_SOURCE[APP_SOURCE.index('const refreshPinnedTaskRecipients'):]
        self.assertIn('}, [user?.id, canUsePinnedTasks, taskRefreshToken, refreshPinnedTaskPool]);', effect)

    def test_quick_form_hides_unknown_checklist(self):
        # Слепок без чек-листа (до синхронизации): редактор чек-листа в быстрой правке не
        # показывается — присланный из него список сервер принял бы за весь чек-лист.
        view = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
        start = view.index('{quickForm.initialChecklist !== null && (')
        self.assertIn('onChange={updateQuickFormChecklist}', view[start:start + 600])
        self.assertEqual(view.count('onChange={updateQuickFormChecklist}'), 1)


if __name__ == '__main__':
    unittest.main()
