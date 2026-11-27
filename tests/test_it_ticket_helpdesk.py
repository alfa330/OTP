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

    def test_deep_nesting_does_not_escape(self):
        # «[[[[…» глубже предела разбора — RecursionError (наследник RuntimeError, не
        # ValueError): поле подделанное, но задача уже поставлена — ответ 500 значил бы дубль.
        self.assertIsNone(helpdesk.parse_ticket_meta('[' * 100000))
        self.assertIsNone(helpdesk.parse_ticket_meta('{"fields": ' + '[' * 5000 + ']' * 5000 + '}'))


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

    def test_deep_json_meta_does_not_raise(self):
        db = _JournalDb()
        self.assertIsNone(self._record(db, 'ITH-1', meta='[' * 100000))
        self.assertEqual(db.calls, [])

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
        self.assertEqual(keywords['raw_meta'], 'it_ticket_raw')
        self.assertEqual(keywords['created'], 'created')
        self.assertEqual(keywords['subject'], 'subject')
        self.assertEqual(keywords['description'], 'description')
        self.assertEqual(keywords['requester_id'], 'requester_id')
        self.assertEqual(keywords['requester_name'], 'requester[2] if requester else None')

    def test_only_when_the_form_carries_it_ticket(self):
        record = self._call('it_ticket_helpdesk.record_task_ticket')
        guard = next(node for node in ast.walk(self.handler)
                     if isinstance(node, ast.If) and record in ast.walk(node))
        self.assertEqual(ast.unparse(guard.test), 'it_ticket_raw')
        self.assertIn("it_ticket_raw = request.form.get('it_ticket')", self.source)

    def test_journal_cannot_change_the_answer(self):
        # Любой сбой журнала — после поставленной задачи — глушится тут же: общий
        # except ручки ответил бы 500, и человек отправил бы заявку второй раз.
        record = self._call('it_ticket_helpdesk.record_task_ticket')
        # Ближайший try вокруг вызова, а не общий try всей ручки (его except — это и есть 500).
        guard = max((node for node in ast.walk(self.handler)
                     if isinstance(node, ast.Try) and record in ast.walk(node)), key=lambda node: node.lineno)
        self.assertGreater(guard.lineno, self._call('db.create_task').lineno)
        self.assertTrue(any(
            handler.type is not None and ast.unparse(handler.type) == 'Exception'
            for handler in guard.handlers))
        self.assertIn(record, list(ast.walk(ast.Module(body=guard.body, type_ignores=[]))))


def _authorize(db, role, *, head=None):
    """Настоящий _it_ticket_authorize из build_it_tickets_blueprint с заглушками.

    Возвращает (функцию, g): по g видно, прошёл ли человек только по доске."""
    _builder, node = _nested_function(IT_ROUTES, 'build_it_tickets_blueprint', '_it_ticket_authorize')
    node = copy.deepcopy(node)
    g = types.SimpleNamespace(user_id=7)
    namespace = {
        'g': g,
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
    return namespace['_it_ticket_authorize'], g


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
    """Люди доски ITH без роли СВ/админа пускаются только туда, где это нужно форме
    заявки (темы на чтение и ИИ); настройки, каналы и журнал — по роли, как раньше."""

    def test_supervisor_passes_without_board_lookup(self):
        db = _AuthDb()
        authorize, g = _authorize(db, 'sv')
        requester_id, _requester, role, error = authorize(allow_board_members=True)
        self.assertIsNone(error)
        self.assertEqual((requester_id, role), (7, 'sv'))
        self.assertEqual(db.member_checks, 0, 'роль решает сама — лишнего запроса нет')
        self.assertFalse(g.it_ticket_board_only)

    def test_helpdesk_board_member_uses_the_form(self):
        authorize, g = _authorize(_AuthDb(member=True), 'operator')
        self.assertIsNone(authorize(allow_board_members=True)[3])
        self.assertTrue(g.it_ticket_board_only, 'прошёл только по доске — настройки ему не считаются')

    def test_board_member_refused_where_the_form_does_not_need_it(self):
        db = _AuthDb(member=True)
        authorize, g = _authorize(db, 'operator')
        self.assertEqual(authorize()[3][1], 403)
        self.assertEqual(db.member_checks, 0)
        self.assertFalse(g.it_ticket_board_only)

    def test_stranger_is_refused(self):
        authorize, _g = _authorize(_AuthDb(member=False), 'operator')
        self.assertEqual(authorize(allow_board_members=True)[3][1], 403)

    def test_failed_lookup_refuses_instead_of_500(self):
        authorize, g = _authorize(_AuthDb(fail=True), 'operator')
        with self.assertLogs(level=logging.WARNING):
            error = authorize(allow_board_members=True)[3]
        self.assertEqual(error[1], 403)
        self.assertFalse(g.it_ticket_board_only)

    def test_board_membership_does_not_open_admin_settings(self):
        db = _AuthDb(member=True)
        authorize, _g = _authorize(db, 'operator')
        self.assertEqual(authorize(require_admin=True)[3][1], 403)
        self.assertEqual(db.member_checks, 0)

    def test_board_only_pass_gets_no_settings_profiles(self):
        # У не-главы права на темы иначе брались бы от его собственного отдела
        # (_department_scope_id_for_requester): оператор ОП на доске правил бы темы ОП.
        _builder, node = _nested_function(IT_ROUTES, 'build_it_tickets_blueprint', '_it_ticket_settings_profiles')
        g = types.SimpleNamespace(it_ticket_board_only=True)
        namespace = {'g': g, '_it_ticket_pinnable_profiles': lambda requester_id, role: ['op']}
        exec(compile(ast.Module(body=[copy.deepcopy(node)], type_ignores=[]), str(IT_ROUTES), 'exec'), namespace)
        self.assertEqual(namespace['_it_ticket_settings_profiles'](7, 'operator'), [])
        g.it_ticket_board_only = False
        self.assertEqual(namespace['_it_ticket_settings_profiles'](7, 'sv'), ['op'])

    def test_only_form_routes_admit_board_members(self):
        builder, _node = _nested_function(IT_ROUTES, 'build_it_tickets_blueprint', '_it_ticket_authorize')
        admitting = set()
        # Ручки — функции прямо в теле строителя (под @bp.route).
        for function in builder.body:
            if not isinstance(function, ast.FunctionDef):
                continue
            for call in ast.walk(function):
                if (isinstance(call, ast.Call) and ast.unparse(call.func) == '_it_ticket_authorize'
                        and any(kw.arg == 'allow_board_members' and ast.unparse(kw.value) == 'True'
                                for kw in call.keywords)):
                    admitting.add(function.name)
        self.assertEqual(admitting, {'it_ticket_catalog', 'it_ticket_ai_assist'})
        catalog = next(node for node in ast.walk(builder)
                       if isinstance(node, ast.FunctionDef) and node.name == 'it_ticket_catalog')
        source = ast.unparse(catalog)
        self.assertIn("'pinnable_profiles': _it_ticket_settings_profiles(requester_id, role)", source)
        self.assertIn("'catalog_editable_profiles': _it_ticket_settings_profiles(requester_id, role)", source)


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
        # Тело собирает fillTicketTaskBody (её вывод сверяет helpdesk_ticket_render.test.mjs).
        self.assertIn('fillTicketTaskBody(body, form, selectedFiles);', submit)
        body = view[view.index('const fillTicketTaskBody = (body, form, files = []) => {'):]
        body = body[:body.index('\n};\n')]
        self.assertIn("body.append('it_ticket', JSON.stringify(ticketLogPayload(ticket)));", body)
        self.assertIn('description: ticketTaskDescription(ticket),', body)
        # Скрытые поля формы задачи заявка не несёт — форма для неё собирается с нуля.
        self.assertIn('appendTaskFormData(body, buildEmptyTaskForm({', body)

    def test_ticket_waits_for_the_ai_and_checks_files_first(self):
        view = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
        submit = view[view.index('const submitTicket = async () => {'):view.index('/* ── Create ── */')]
        # Отправка во время ответа ИИ увела бы заявку без него, а ответ — в следующую заявку.
        self.assertIn('if (helpdeskAiBusy) return;', submit)
        self.assertIn('disabled={isCreateLoading || isRecipientsLoading || (createIsHelpdesk && helpdeskAiBusy)}', view)
        self.assertIn('onBusyChange: setHelpdeskAiBusy,', view)
        self.assertIn('onBusyChange={helpdesk.onBusyChange}', view)
        # Пределы файлов — до загрузки и по-русски.
        self.assertIn('const refusal = taskUploadRefusal(filesFromList(selectedFiles));', submit)
        self.assertLess(submit.index('taskUploadRefusal'), submit.index('axios.post'))

    def test_issues_and_seed_are_wired(self):
        view = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
        # Пустые обязательные поля заявки краснеют и листаются стрелками подвала.
        self.assertIn('const createBaseIssues = useMemo(() => (createIsHelpdesk ? ticketIssues(form.ticket) : [', view)
        # Написанное до выбора доски становится описанием проблемы.
        self.assertIn('const seed = seedTicketDescription(values);', view)
        self.assertIn('if (seed) onTicketChange({ description: seed });', view)
        # Смена доски в окне «Новая задача» — через assigneeBaseForBoardChange («Все» не уезжает с ITH).
        self.assertIn('const boardBase = helpdesk ? assigneeBaseForBoardChange(values, selectedProject, nextProject) : values;', view)
        self.assertIn('assigneesAfterProjectChange(boardBase, nextProject, {', view)

    def test_late_ai_answer_does_not_reach_a_closed_form(self):
        form = (ROOT / 'src' / 'components' / 'tasks' / 'HelpdeskTicketForm.jsx').read_text(encoding='utf-8')
        run_ai = form[form.index('const runAi = useCallback(async (mode) => {'):form.index('/* ─── Капсулы')]
        self.assertIn('useEffect(() => () => { aliveRef.current = false; }, []);', form)
        self.assertLess(run_ai.index('if (!aliveRef.current) return;'), run_ai.index('onChange(patch);'))

    def test_only_the_new_task_window_turns_into_a_ticket(self):
        view = (ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx').read_text(encoding='utf-8')
        self.assertEqual(view.count('helpdesk={helpdeskComposer}'), 1, 'правка задачи остаётся формой задачи')


if __name__ == '__main__':
    unittest.main()
