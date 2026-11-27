# -*- coding: utf-8 -*-
"""Заявка в IT из «Задач» (владелец 27.11.2026): серверная часть.

«В разделе тех причины есть возможность создавать тикеты для айти, его надо
полностью перенести в раздел задачи при отметке it helpdesk… чтобы тикеты
создавались там же и попадали в их же доску».

Что защищаем:
  • it_tickets/helpdesk.py — что из поля it_ticket попадает в журнал it_tickets
    и что только для задач доски ITH (поле приходит от клиента);
  • tasks/routes.py — журнал пишется после того, как задача поставлена, и только
    при поле it_ticket; сбой журнала задачу не роняет;
  • it_tickets/routes.py — люди доски ITH пользуются ИИ заявок, но настройки
    (каналы — только админ) им это не открывает;
  • «Тех причины» больше не несут «Тикет в IT-отдел».

`bot_schedule2`/`database` целиком не импортируются (tzset, пул к базе) —
функции берём из исходника и выполняем с заглушками.
"""
import ast
import copy
import json
import logging
import textwrap
import types
import unittest
from pathlib import Path

import task_projects
from it_tickets import helpdesk

ROOT = Path(__file__).resolve().parents[1]
TASK_ROUTES = ROOT / 'tasks' / 'routes.py'
IT_ROUTES = ROOT / 'it_tickets' / 'routes.py'


class TicketMetaTests(unittest.TestCase):
    def test_values_are_cleaned_and_whitelisted(self):
        meta = helpdesk.parse_ticket_meta(json.dumps({
            'profile': 'SZOV',
            'category': '  Телефония   и Oktell ',
            'subcategory': 'Не работают наушники/гарнитура',
            'priority': 'HIGH',
            'fields': {'workplace': ' РМ 27 (Тех поддержка) ', 'empty': '  ', '': 'без ключа', 'n': 5},
        }, ensure_ascii=False))
        self.assertEqual(meta['profile'], 'szov')
        self.assertEqual(meta['category'], 'Телефония и Oktell')
        self.assertEqual(meta['priority'], 'high')
        self.assertEqual(meta['fields'], {'workplace': 'РМ 27 (Тех поддержка)', 'n': '5'})

    def test_unknown_profile_and_priority_fall_back(self):
        meta = helpdesk.parse_ticket_meta('{"profile": "hr", "priority": "urgent!!"}')
        self.assertEqual((meta['profile'], meta['priority']), ('op', 'medium'))

    def test_limits(self):
        meta = helpdesk.parse_ticket_meta(json.dumps({
            'category': 'я' * 500,
            'fields': {f'k{i}': 'x' * 5000 for i in range(50)},
        }))
        self.assertEqual(len(meta['category']), helpdesk.MAX_TOPIC)
        self.assertEqual(len(meta['fields']), helpdesk.MAX_FIELDS)
        self.assertTrue(all(len(value) == helpdesk.MAX_FIELD_VALUE for value in meta['fields'].values()))

    def test_garbage_is_ignored(self):
        for raw in (None, '', 'не json', '[1, 2]', '42', b'\xff'):
            with self.subTest(raw=raw):
                self.assertIsNone(helpdesk.parse_ticket_meta(raw))


class _JournalDb:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def record_it_ticket(self, **kwargs):
        if self.fail:
            raise RuntimeError('column "task_id" does not exist')
        self.calls.append(kwargs)
        return {'id': 900, 'created_at': '2026-11-27 12:00:00'}


class RecordTaskTicketTests(unittest.TestCase):
    META = json.dumps({'profile': 'szov', 'category': 'Телефония и Oktell', 'priority': 'critical',
                       'fields': {'workplace': 'РМ 1, 2'}}, ensure_ascii=False)

    def _record(self, db, key, meta=None):
        return helpdesk.record_task_ticket(
            db, raw_meta=self.META if meta is None else meta,
            created={'id': 501, 'key': key},
            subject='Нет интернета по всему отделу',
            description='Тема: Сетевые\n\n🔧 Что: нет интернета',
            requester_id=22, requester_name='А Супервайзер',
        )

    def test_helpdesk_task_goes_to_journal_with_task_link(self):
        db = _JournalDb()
        self.assertEqual(self._record(db, 'ITH-12'), {'id': 900, 'created_at': '2026-11-27 12:00:00'})
        call = db.calls[0]
        self.assertEqual(call['task_id'], 501)
        self.assertEqual(call['status'], 'task')
        self.assertIsNone(call['channel_id'])
        self.assertIsNone(call['telegram_message_id'])
        self.assertEqual(call['priority'], 'critical')
        self.assertEqual(call['title'], 'Нет интернета по всему отделу')
        self.assertIn('нет интернета', call['body'])
        self.assertEqual(call['fields'], {'workplace': 'РМ 1, 2'})
        self.assertEqual((call['created_by'], call['created_by_name']), (22, 'А Супервайзер'))

    def test_other_boards_and_tasks_without_board_are_not_tickets(self):
        db = _JournalDb()
        for key in ('HR-5', 'BE-1', None, '', 'ITHX-1'):
            with self.subTest(key=key):
                self.assertIsNone(self._record(db, key))
        self.assertEqual(db.calls, [])

    def test_lowercase_key_is_still_the_helpdesk(self):
        db = _JournalDb()
        self.assertIsNotNone(self._record(db, 'ith-3'))

    def test_bad_meta_skips_journal(self):
        db = _JournalDb()
        self.assertIsNone(self._record(db, 'ITH-1', meta='{битый'))
        self.assertEqual(db.calls, [])

    def test_journal_failure_does_not_raise(self):
        with self.assertLogs(level=logging.WARNING):
            self.assertIsNone(self._record(_JournalDb(fail=True), 'ITH-1'))

    def test_helpdesk_code_is_one_constant(self):
        self.assertTrue(task_projects.is_helpdesk_code(' ith '))
        self.assertFalse(task_projects.is_helpdesk_code('IT'))
        self.assertIn(('ITH', 'IT Helpdesk', 'it'), task_projects.SEED_PROJECTS)
        self.assertEqual(task_projects.HELPDESK_PROJECT_CODE, 'ITH')


def _nested_function(path, builder_name, function_name):
    module = ast.parse(path.read_text(encoding='utf-8-sig'))
    builder = next(node for node in module.body
                   if isinstance(node, ast.FunctionDef) and node.name == builder_name)
    return builder, next(node for node in ast.walk(builder)
                         if isinstance(node, ast.FunctionDef) and node.name == function_name)


class TaskRouteWiringTests(unittest.TestCase):
    """Журнал — после create_task, при поле it_ticket, теми значениями, что ушли в задачу."""

    def setUp(self):
        _builder, self.handler = _nested_function(TASK_ROUTES, 'build_tasks_blueprint', 'handle_tasks')
        self.source = ast.get_source_segment(TASK_ROUTES.read_text(encoding='utf-8-sig'), self.handler)

    def _call(self, dotted):
        for node in ast.walk(self.handler):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == dotted:
                return node
        self.fail(f'нет вызова {dotted}')

    def test_journal_written_after_the_task_and_before_the_answer(self):
        create = self._call('db.create_task')
        record = self._call('it_ticket_helpdesk.record_task_ticket')
        self.assertGreater(record.lineno, create.end_lineno)
        response = next(node for node in ast.walk(self.handler)
                        if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'response_payload' for t in node.targets))
        self.assertLess(record.lineno, response.lineno)
        keywords = {kw.arg: ast.unparse(kw.value) for kw in record.keywords}
        self.assertEqual(keywords['created'], 'created')
        self.assertEqual(keywords['subject'], 'subject')
        self.assertEqual(keywords['description'], 'description')
        self.assertEqual(keywords['requester_id'], 'requester_id')

    def test_only_when_the_form_carries_it_ticket(self):
        record = self._call('it_ticket_helpdesk.record_task_ticket')
        guard = next(node for node in ast.walk(self.handler)
                     if isinstance(node, ast.If) and record in ast.walk(node))
        self.assertEqual(ast.unparse(guard.test), 'it_ticket_raw')
        self.assertIn("it_ticket_raw = request.form.get('it_ticket')", self.source)


def _authorize(db, role, *, head=None):
    """Настоящий _it_ticket_authorize из build_it_tickets_blueprint с заглушками."""
    _builder, node = _nested_function(IT_ROUTES, 'build_it_tickets_blueprint', '_it_ticket_authorize')
    node = copy.deepcopy(node)
    namespace = {
        'g': types.SimpleNamespace(user_id=7),
        'request': types.SimpleNamespace(headers={}),
        'jsonify': lambda payload: payload,
        'db': db,
        'logging': logging,
        '_normalize_management_role': lambda value: str(value or '').lower(),
        '_is_admin_role': lambda value: value in ('admin', 'super_admin'),
        '_is_supervisor_role': lambda value: value == 'sv',
        '_headed_department_id': lambda user_id: head,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(IT_ROUTES), 'exec'), namespace)
    db.role = role
    return namespace['_it_ticket_authorize']


class _AuthDb:
    def __init__(self, member=False, fail=False):
        self.member = member
        self.fail = fail
        self.role = 'operator'
        self.member_checks = 0

    def get_user(self, id):
        return (id, 'login', 'Имя', self.role)

    def is_task_helpdesk_member(self, user_id):
        self.member_checks += 1
        if self.fail:
            raise RuntimeError('boom')
        return self.member


class AuthorizeTests(unittest.TestCase):
    def test_supervisor_passes_without_board_lookup(self):
        db = _AuthDb()
        requester_id, _requester, role, error = _authorize(db, 'sv')()
        self.assertIsNone(error)
        self.assertEqual((requester_id, role), (7, 'sv'))
        self.assertEqual(db.member_checks, 0, 'роль решает сама — лишнего запроса нет')

    def test_helpdesk_board_member_uses_ticket_ai(self):
        _id, _requester, _role, error = _authorize(_AuthDb(member=True), 'operator')()
        self.assertIsNone(error)

    def test_stranger_is_refused(self):
        error = _authorize(_AuthDb(member=False), 'operator')()[3]
        self.assertEqual(error[1], 403)

    def test_failed_lookup_refuses_instead_of_500(self):
        with self.assertLogs(level=logging.WARNING):
            error = _authorize(_AuthDb(fail=True), 'operator')()[3]
        self.assertEqual(error[1], 403)

    def test_board_membership_does_not_open_admin_settings(self):
        db = _AuthDb(member=True)
        error = _authorize(db, 'operator')(require_admin=True)[3]
        self.assertEqual(error[1], 403)
        self.assertEqual(db.member_checks, 0)


class FrontWiringTests(unittest.TestCase):
    """Перенос целиком: в «Тех причинах» окна нет, в «Задачах» заявка уходит задачей."""

    def test_technical_issues_has_no_it_ticket_window(self):
        view = (ROOT / 'src' / 'components' / 'technical' / 'TechnicalIssuesView.jsx').read_text(encoding='utf-8')
        self.assertNotIn('ITTicketModal', view)
        self.assertNotIn('Тикет в IT-отдел', view)
        self.assertFalse((ROOT / 'src' / 'components' / 'technical' / 'ITTicketModal.jsx').exists())

    def test_ticket_is_a_task_on_the_board_with_journal_meta(self):
        view = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
        submit = view[view.index('const submitTicket = async () => {'):view.index('/* ── Create ── */')]
        self.assertIn("axios.post(`${apiBaseUrl}/api/tasks`", submit)
        self.assertIn("body.append('it_ticket', JSON.stringify(ticketLogPayload(ticket)))", submit)
        self.assertIn('ticketTaskDescription(ticket)', submit)
        # Скрытые поля формы задачи заявка не несёт — форма для неё собирается с нуля.
        self.assertIn('appendTaskFormData(body, buildEmptyTaskForm({', submit)

    def test_only_the_new_task_window_turns_into_a_ticket(self):
        view = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
        self.assertEqual(view.count('helpdesk={helpdeskComposer}'), 1, 'правка задачи остаётся формой задачи')


if __name__ == '__main__':
    unittest.main()
