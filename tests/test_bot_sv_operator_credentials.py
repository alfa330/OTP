# -*- coding: utf-8 -*-
"""«Управление операторами🔑» в боте: СВ меняет логин и пароль любому оператору своего отдела (#380).

Сайт так умеет с 28.10.2026 (tests/test_sv_operator_credentials.py), а бот держался старой границы:
и список, и смена — только тем операторам, у кого СВ записан руководителем. Что сторожится —
настоящими функциями и обработчиками bot_schedule2 (через ast: импортировать монолит тесты не
могут), подменены только база и Telegram:
  * кого бот показывает, тому и даёт сменить вход, и ровно тех же пускает настоящая ручка сайта
    /api/user/change_password — сверка на всей матрице СВ × операторы;
  * экраны: своя группа первой, остальные группы отдела — кнопкой, длинная группа — страницами;
  * «Назад» снова работает: раньше 'cred_back' перехватывал startswith('cred_') и падал на int();
  * пока бот ждёт значение — «Отмена ❌» вместо меню, длина и занятость логина — как у сайта,
    граница проверяется ещё раз перед записью;
  * каждую кнопку экранов первым забирает её обработчик — по всем callback-обработчикам бота.
Данные выдуманные: репозиторий публичный.
"""

import ast
import asyncio
import copy
import html
import logging
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import source_cache  # noqa: E402

BOT_PY = source_cache.monolith_flat_path()

HELPERS = (
    'ROLE_HIERARCHY', '_get_role_level', '_has_min_role', '_normalize_user_role', '_is_super_admin_role',
    '_is_admin_role', '_is_supervisor_role', '_is_global_admin_requester', 'EMPLOYEE_ACCOUNTING_DEPARTMENT_CODE',
    '_is_employee_accounting_manager', '_headed_department_id', '_headed_department_ids',
    '_department_scope_id_for_requester', '_target_user_supervisor_id', '_requester_can_access_target_user',
    '_SV_CREDENTIALS_PAGE_SIZE', '_sv_credentials_operators', '_sv_credentials_target_allowed',
    '_sv_credentials_screen', '_sv_credentials_busy',
)
HANDLERS = ('manage_operators_credentials', 'operator_credentials_list', 'operator_credentials_menu',
            'operator_credentials_prompt', 'process_credential_change')
SITE_ROUTE = 'change_password'

SZOV, OP, HR = 1, 2, 9
DEPARTMENT_CODES = {SZOV: 'szov', OP: 'op', HR: 'hr'}

SV_FIRST, SV_SECOND, SV_OTHER_DEPT, SV_NO_DEPT, SV_HEAD = 10, 11, 12, 13, 14
CANCEL = 'Отмена ❌'
WAITING = 'ChangeCredentials:waiting_for_value'


def _user(user_id, name, role, department_id, supervisor_id=None, login=None, status='working'):
    return {'id': user_id, 'telegram_id': 9000 + user_id, 'name': name, 'role': role,
            'department_id': department_id, 'supervisor_id': supervisor_id,
            'login': login or 'login.%d' % user_id, 'status': status}


def _people():
    return [
        _user(1, 'Админ Тестовый', 'admin', SZOV),
        _user(SV_FIRST, 'СВ Первая', 'sv', SZOV),
        _user(SV_SECOND, 'СВ Вторая', 'sv', SZOV),
        _user(SV_OTHER_DEPT, 'СВ Третья', 'sv', OP),
        _user(SV_NO_DEPT, 'СВ Без отдела', 'sv', None),
        # Глава ОП, хотя сама числится в СЗоВ: сайт ведёт её по возглавляемому отделу.
        _user(SV_HEAD, 'СВ Глава', 'sv', SZOV),
        _user(101, 'Оператор Один', 'operator', SZOV, SV_FIRST),
        _user(102, 'Оператор Два', 'operator', SZOV, SV_FIRST),
        _user(103, 'Оператор Три', 'operator', SZOV, SV_SECOND),
        _user(104, 'Оператор Уволенный', 'operator', SZOV, SV_SECOND, status='fired'),
        _user(105, 'Оператор Без СВ', 'operator', SZOV),
        _user(106, 'Стажёр Шесть', 'trainee', SZOV, SV_FIRST),
        _user(201, 'Оператор ОП', 'operator', OP, SV_OTHER_DEPT),
        _user(301, 'Оператор Старый', 'operator', None, SV_NO_DEPT),
        _user(401, 'Кадровик Тестовый', 'hr_manager', HR),
    ]


class FakeDb:
    """База бота — ровно то, что трогают правило доступа, список и обработчики. Учётка уходит
    кортежем db.get_user: [0] id, [1] telegram_id, [2] имя, [3] роль, [6] СВ, [7] логин, [11] статус."""

    def __init__(self, people, heads=None):
        self.users = {item['id']: dict(item) for item in people}
        self.heads = dict(heads or {})  # отдел -> глава
        self.logins, self.passwords = [], []

    @staticmethod
    def _row(item):
        return (item['id'], item['telegram_id'], item['name'], item['role'], None, None,
                item['supervisor_id'], item['login'], None, None, True, item['status'])

    def get_user(self, telegram_id=None, id=None):
        for item in self.users.values():
            if (id is not None and item['id'] == int(id)) or (
                    telegram_id is not None and item['telegram_id'] == telegram_id):
                return self._row(item)
        return None

    def get_user_department_id(self, user_id):
        item = self.users.get(int(user_id))
        return item['department_id'] if item else None

    def get_department_by_id(self, department_id):
        return {'id': department_id, 'code': DEPARTMENT_CODES.get(department_id)}

    def get_headed_departments_for_user(self, user_id):
        return [{'id': dept} for dept, head in sorted(self.heads.items()) if head == user_id]

    def headed_department_id_for_user(self, user_id):
        headed = self.get_headed_departments_for_user(user_id)
        return headed[0]['id'] if headed else None

    def get_active_operators_for_access(self, department_ids=None, supervisor_id=None):
        """Смысл SQL из database.py; сам SQL сверяется на Postgres в *_postgres.py."""
        departments = [int(item) for item in (department_ids or ())]
        if not departments and supervisor_id is None:
            return []
        found = [item for item in self.users.values()
                 if item['role'] == 'operator' and item['status'] not in ('fired', 'dismissal')
                 and (item['department_id'] in departments if departments
                      else item['supervisor_id'] == supervisor_id)]
        return [{'id': item['id'], 'name': item['name'], 'supervisor_id': item['supervisor_id'],
                 'supervisor_name': (self.users.get(item['supervisor_id']) or {}).get('name')}
                for item in sorted(found, key=lambda item: (item['name'], item['id']))]

    def get_user_by_login(self, login):
        item = next((item for item in self.users.values() if item['login'] == login), None)
        return self._row(item) if item else None

    def update_operator_login(self, operator_id, supervisor_id, new_login):
        self.logins.append((operator_id, supervisor_id, new_login))
        self.users[int(operator_id)]['login'] = new_login
        return True

    def update_user_password(self, user_id, new_password):
        self.passwords.append((user_id, new_password))
        return True


def _source_nodes(names):
    tree = source_cache.parse(BOT_PY.read_text(encoding='utf-8-sig'))
    nodes, decorators = [], {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in names for t in node.targets):
            nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node = copy.deepcopy(node)  # дерево общее на весь набор (tests/source_cache.py)
            decorators[node.name] = node.decorator_list
            node.decorator_list = []
            nodes.append(node)
    return tree, nodes, decorators


class BotSvOperatorCredentialsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
        from flask import g  # настоящий g: вне запроса Flask он бросает RuntimeError, как в боте

        cls.tree, nodes, cls.decorators = _source_nodes(set(HELPERS + HANDLERS + (SITE_ROUTE,)))
        cls.namespace = {
            'InlineKeyboardMarkup': InlineKeyboardMarkup, 'InlineKeyboardButton': InlineKeyboardButton,
            'ReplyKeyboardMarkup': ReplyKeyboardMarkup, 'KeyboardButton': KeyboardButton,
            'types': mock.MagicMock(), 'FSMContext': object, 'html': html, 're': re, 'logging': logging, 'g': g,
            'get_cancel_keyboard': lambda: CANCEL,
            '_main_menu_keyboard': lambda user_id, role: ('меню', user_id, role),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PY), 'exec'), cls.namespace)
        missing = [name for name in HELPERS + HANDLERS + (SITE_ROUTE,) if name not in cls.namespace]
        if missing:
            raise AssertionError('не найдено в bot_schedule2.py: %s' % missing)

    def setUp(self):
        self.db = FakeDb(_people(), heads={OP: SV_HEAD})
        self.bot = SimpleNamespace(send_message=mock.AsyncMock(), edit_message_text=mock.AsyncMock())
        self.dialog = SimpleNamespace(waiting_for_value=SimpleNamespace(set=mock.AsyncMock(), state=WAITING))
        self.namespace.update(db=self.db, bot=self.bot, ChangeCredentials=self.dialog)

    # ─── Помощники ───────────────────────────────────────────────────────────

    def _row(self, user_id):
        return self.db.get_user(id=user_id)

    def _run(self, handler, *args):
        asyncio.run(self.namespace[handler](*args))

    @staticmethod
    def _state(current=None, **data):
        return SimpleNamespace(get_state=mock.AsyncMock(return_value=current),
                               get_data=mock.AsyncMock(return_value=data),
                               update_data=mock.AsyncMock(), finish=mock.AsyncMock())

    def _callback(self, data, user_id=SV_FIRST):
        callback = SimpleNamespace(data=data, id='cb', from_user=SimpleNamespace(id=9000 + user_id),
                                   message=SimpleNamespace(message_id=77, delete=mock.AsyncMock()))
        callback.answer = mock.AsyncMock()
        return callback

    @staticmethod
    def _message(text, user_id=SV_FIRST):
        message = SimpleNamespace(text=text, from_user=SimpleNamespace(id=9000 + user_id), message_id=55)
        message.delete = mock.AsyncMock()
        return message

    @staticmethod
    def _buttons(markup):
        return [[(button['text'], button['callback_data']) for button in row]
                for row in markup.to_python()['inline_keyboard']]

    def _screen(self, requester_id=SV_FIRST, **kwargs):
        operators = self.namespace['_sv_credentials_operators'](self._row(requester_id))
        text, markup = self.namespace['_sv_credentials_screen'](requester_id, operators, **kwargs)
        return text, (self._buttons(markup) if markup is not None else None)

    def _edited(self):
        kwargs = self.bot.edit_message_text.await_args.kwargs
        return kwargs['text'], self._buttons(kwargs['reply_markup'])

    def _sent(self):
        kwargs = self.bot.send_message.await_args.kwargs
        return kwargs['text'], kwargs.get('reply_markup')

    # ─── Граница: бот = сайт ─────────────────────────────────────────────────

    def _site_status(self, requester_id, target_id):
        namespace = dict(self.namespace)
        requester = self._row(requester_id)
        namespace.update(
            request=SimpleNamespace(get_json=lambda: {'user_id': target_id, 'new_password': 'новый-пароль'}),
            jsonify=lambda body: body,
            requests=SimpleNamespace(post=lambda *_a, **_k: SimpleNamespace(status_code=200, json=dict)),
            API_TOKEN='x',
            _get_authenticated_requester=lambda: (requester[0], requester, None),
        )
        route = copy.deepcopy(next(node for node in self.tree.body
                                   if isinstance(node, ast.FunctionDef) and node.name == SITE_ROUTE))
        route.decorator_list = []
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(BOT_PY), 'exec'), namespace)
        result = namespace[SITE_ROUTE]()
        return result[1] if isinstance(result, tuple) else 200

    def test_bot_lets_in_exactly_whom_the_site_lets_in(self):
        allowed = self.namespace['_sv_credentials_target_allowed']
        operators = [item['id'] for item in self.db.users.values() if item['role'] == 'operator']
        for sv in (SV_FIRST, SV_SECOND, SV_OTHER_DEPT, SV_NO_DEPT, SV_HEAD):
            for operator in operators:
                site = self._site_status(sv, operator)
                self.assertIn(site, (200, 403), (sv, operator))
                self.assertEqual(allowed(self._row(sv), self._row(operator)), site == 200, (sv, operator))

    def test_department_is_the_boundary_not_the_supervisor(self):
        allowed = self.namespace['_sv_credentials_target_allowed']
        first = self._row(SV_FIRST)
        for operator in (101, 103, 105, 104):  # своя группа, чужая группа отдела, без СВ, уволенный
            self.assertTrue(allowed(first, self._row(operator)), operator)
        for target in (201, 301, 106, SV_SECOND, 1, 401):  # чужой отдел, без отдела, стажёр, СВ, админ, кадровик
            self.assertFalse(allowed(first, self._row(target)), target)
        # СВ без отдела — как и на сайте, только свои; глава — по возглавляемому отделу.
        self.assertEqual([op for op in (101, 201, 301) if allowed(self._row(SV_NO_DEPT), self._row(op))], [301])
        self.assertEqual([op for op in (101, 201, 301) if allowed(self._row(SV_HEAD), self._row(op))], [201])
        # Сайт пускает главу и к СВ своего отдела, но эта кнопка — только про операторов.
        self.assertFalse(allowed(self._row(SV_HEAD), self._row(SV_OTHER_DEPT)))
        # Не СВ этой кнопкой не пользуется вовсе — даже админ.
        self.assertFalse(allowed(self._row(1), self._row(101)))
        self.assertFalse(allowed(None, self._row(101)))
        self.assertFalse(allowed(first, None))

    def test_the_list_is_who_can_be_changed_minus_the_dismissed(self):
        listed = self.namespace['_sv_credentials_operators']
        allowed = self.namespace['_sv_credentials_target_allowed']
        working = [item['id'] for item in self.db.users.values()
                   if item['role'] == 'operator' and item['status'] == 'working']
        for sv in (SV_FIRST, SV_SECOND, SV_OTHER_DEPT, SV_NO_DEPT, SV_HEAD):
            shown = [item['id'] for item in listed(self._row(sv))]
            self.assertEqual(sorted(shown), sorted(op for op in working if allowed(self._row(sv), self._row(op))), sv)
        self.assertEqual(sorted(item['id'] for item in listed(self._row(SV_FIRST))), [101, 102, 103, 105])

    # ─── Экраны ──────────────────────────────────────────────────────────────

    def test_own_group_first_and_the_rest_of_the_department_behind_a_button(self):
        text, rows = self._screen(group_id=SV_FIRST)
        self.assertEqual(text, '<b>Выберите оператора для управления доступом:</b>')
        self.assertEqual(rows, [[('Оператор Два', 'cred_102')], [('Оператор Один', 'cred_101')],
                                [('👥 Другие группы отдела', 'credgl')]])

    def test_groups_list_own_first_then_by_name_without_supervisor_last(self):
        self.db.users[SV_SECOND]['name'] = 'Алина СВ'  # по алфавиту раньше «Ваша группа»
        text, rows = self._screen(group_id=None)
        self.assertEqual(text, '<b>Группы вашего отдела</b>\nВыберите группу:')
        self.assertEqual(rows, [[('Ваша группа · 2', 'credg_10_0')], [('Алина СВ · 1', 'credg_11_0')],
                                [('Без супервайзера · 1', 'credg_0_0')]])

    def test_other_group_has_its_name_and_the_way_back(self):
        self.db.users[SV_SECOND]['name'] = 'СВ <Вторая> & Ко'
        text, rows = self._screen(group_id=SV_SECOND)
        self.assertEqual(text, '<b>Группа: СВ &lt;Вторая&gt; &amp; Ко</b>\n'
                               'Выберите оператора для управления доступом:')
        self.assertEqual(rows, [[('Оператор Три', 'cred_103')], [('‹ Группы отдела', 'credgl')]])
        text, rows = self._screen(group_id=0)
        self.assertEqual(text, '<b>Операторы без супервайзера</b>\nВыберите оператора для управления доступом:')
        self.assertEqual(rows, [[('Оператор Без СВ', 'cred_105')], [('‹ Группы отдела', 'credgl')]])

    def test_without_own_operators_the_first_screen_is_the_groups_list(self):
        for item in self.db.users.values():
            if item['supervisor_id'] == SV_FIRST:
                item['supervisor_id'] = SV_SECOND
        text, rows = self._screen(group_id=SV_FIRST)
        self.assertEqual(text, '<b>Группы вашего отдела</b>\nВыберите группу:')
        self.assertEqual([row[0][1] for row in rows], ['credg_11_0', 'credg_0_0'])

    def test_lone_group_has_no_groups_button_and_nobody_means_no_buttons(self):
        text, rows = self._screen(requester_id=SV_NO_DEPT, group_id=SV_NO_DEPT)
        self.assertEqual(rows, [[('Оператор Старый', 'cred_301')]])
        self.db.users[301]['status'] = 'fired'
        self.assertEqual(self._screen(requester_id=SV_NO_DEPT, group_id=SV_NO_DEPT),
                         ('<b>У вас нет операторов</b>', None))

    def test_a_long_group_goes_by_pages(self):
        size = self.namespace['_SV_CREDENTIALS_PAGE_SIZE']
        for index in range(size + 5):
            self.db.users[1000 + index] = _user(1000 + index, 'Оператор %03d' % index, 'operator', SZOV, SV_SECOND)
        text, rows = self._screen(group_id=SV_SECOND)
        self.assertEqual(len(rows), size + 2)  # страница, «▶️», «‹ Группы отдела»
        self.assertTrue(text.endswith('\nСтраница 1 из 2'))
        self.assertEqual(rows[-2], [('▶️', 'credg_11_1')])
        text, rows = self._screen(group_id=SV_SECOND, page=1)
        self.assertTrue(text.endswith('\nСтраница 2 из 2'))
        self.assertEqual(len(rows), 6 + 2)  # 5 добавленных + «Оператор Три», «◀️», «‹ Группы отдела»
        self.assertEqual(rows[-2], [('◀️', 'credg_11_0')])
        # Страница за краем (список стал короче, пока экран висел в чате) — последняя.
        self.assertEqual(self._screen(group_id=SV_SECOND, page=9), (text, rows))

    # ─── Обработчики ─────────────────────────────────────────────────────────

    def test_menu_button_sends_the_first_screen(self):
        message = self._message('Управление операторами🔑')
        self._run('manage_operators_credentials', message)
        text, markup = self._sent()
        self.assertEqual(text, '<b>Выберите оператора для управления доступом:</b>')
        self.assertEqual(self._buttons(markup)[-1], [('👥 Другие группы отдела', 'credgl')])
        message.delete.assert_awaited_once()

    def test_back_from_old_screens_works_again(self):
        callback = self._callback('cred_back')
        self._run('operator_credentials_list', callback, self._state())
        text, rows = self._edited()
        self.assertEqual(text, '<b>Выберите оператора для управления доступом:</b>')
        self.assertEqual(rows[0], [('Оператор Два', 'cred_102')])
        callback.answer.assert_awaited_once_with()

    def test_navigation_between_groups(self):
        self._run('operator_credentials_list', self._callback('credgl'), self._state())
        self.assertEqual(self._edited()[1][1], [('СВ Вторая · 1', 'credg_11_0')])
        self._run('operator_credentials_list', self._callback('credg_11_0'), self._state())
        self.assertEqual(self._edited()[1][0], [('Оператор Три', 'cred_103')])

    def test_operator_screen_from_another_group_and_its_way_back(self):
        self.db.users[103]['login'] = 'op<3>&'
        callback = self._callback('cred_103')
        self._run('operator_credentials_menu', callback, self._state())
        text, rows = self._edited()
        self.assertEqual(text, '<b>Управление доступом оператора</b>\n\nОператор Три\n'
                               'Логин: <code>op&lt;3&gt;&amp;</code>')
        self.assertEqual(rows, [[('Изменить логин', 'chlogin_103'), ('Изменить пароль', 'chpass_103')],
                                [('‹ Назад', 'credg_11_0')]])

    def test_operator_outside_the_department_is_refused_even_with_a_crafted_button(self):
        for data, handler in (('cred_201', 'operator_credentials_menu'), ('chpass_201', 'operator_credentials_prompt'),
                              ('chlogin_106', 'operator_credentials_prompt')):
            callback = self._callback(data)
            state = self._state()
            self._run(handler, callback, state)
            callback.answer.assert_awaited_once_with('Оператор не найден в вашем отделе', show_alert=True)
            state.update_data.assert_not_awaited()
        self.bot.edit_message_text.assert_not_awaited()
        self.dialog.waiting_for_value.set.assert_not_awaited()

    def test_not_a_supervisor_gets_nothing(self):
        message = self._message('Управление операторами🔑', user_id=101)
        self._run('manage_operators_credentials', message)
        self.bot.send_message.assert_not_awaited()
        callback = self._callback('credgl', user_id=101)
        self._run('operator_credentials_list', callback, self._state())
        self.bot.edit_message_text.assert_not_awaited()

    def test_old_buttons_do_not_cut_into_the_input(self):
        for data, handler in (('credgl', 'operator_credentials_list'), ('cred_101', 'operator_credentials_menu'),
                              ('chlogin_101', 'operator_credentials_prompt')):
            callback = self._callback(data)
            state = self._state(current=WAITING)
            self._run(handler, callback, state)
            callback.answer.assert_awaited_once_with('Сначала введите значение или нажмите «Отмена ❌»',
                                                     show_alert=True)
            state.update_data.assert_not_awaited()
        self.bot.edit_message_text.assert_not_awaited()

    def test_someone_elses_dialog_does_not_lock_the_screens(self):
        """В чужом диалоге «Отмены ❌» на клавиатуре может не быть — экраны не держим."""
        self._run('operator_credentials_list', self._callback('credgl'), self._state(current='sv:view_evaluations'))
        self.bot.edit_message_text.assert_awaited_once()
        state = self._state(current='sv:view_evaluations')
        self._run('operator_credentials_prompt', self._callback('chlogin_101'), state)
        state.update_data.assert_awaited_once_with(operator_id=101, action='login')

    def test_prompt_replaces_the_menu_with_cancel(self):
        callback = self._callback('chpass_103')
        state = self._state()
        self._run('operator_credentials_prompt', callback, state)
        state.update_data.assert_awaited_once_with(operator_id=103, action='password')
        self.dialog.waiting_for_value.set.assert_awaited_once()
        callback.message.delete.assert_awaited_once()
        self.assertEqual(self._sent(), ('Введите новый пароль для оператора <b>Оператор Три</b>:', CANCEL))

    def test_password_of_an_operator_from_another_group(self):
        short = self._message('12345')
        state = self._state(operator_id=103, action='password')
        self._run('process_credential_change', short, state)
        self.assertEqual(self._sent(), ('Пароль должен быть не менее 6 символов. Попробуйте еще раз:', CANCEL))
        short.delete.assert_awaited_once()  # набранный пароль не остаётся в чате
        state.finish.assert_not_awaited()

        good = self._message('  пароль-123  ')
        self._run('process_credential_change', good, state)
        self.assertEqual(self.db.passwords, [(103, 'пароль-123')])
        state.finish.assert_awaited_once()
        self.assertEqual(self._sent(), ('✅ Пароль оператора успешно изменён\n\nОператор: Оператор Три',
                                        ('меню', SV_FIRST, 'sv')))
        good.delete.assert_awaited_once()

    def test_login_rules_are_the_sites(self):
        state = self._state(operator_id=103, action='login')
        self._run('process_credential_change', self._message('abc'), state)
        self.assertEqual(self._sent()[0], 'Логин должен быть не менее 4 символов. Попробуйте еще раз:')
        self._run('process_credential_change', self._message('login.101'), state)  # чужой логин
        self.assertEqual(self._sent(), ('Этот логин уже занят. Попробуйте другой:', CANCEL))
        self.assertEqual(self.db.logins, [])
        state.finish.assert_not_awaited()

        self._run('process_credential_change', self._message('new<login>'), state)
        self.assertEqual(self.db.logins, [(103, None, 'new<login>')])
        self.assertEqual(self._sent()[0], '✅ Логин оператора изменён на: <code>new&lt;login&gt;</code>\n\n'
                                          'Оператор: Оператор Три')
        state.finish.assert_awaited_once()

    def test_boundary_is_checked_again_before_writing(self):
        state = self._state(operator_id=103, action='password')
        self.db.users[103]['department_id'] = OP  # перевели, пока СВ набирал пароль
        self._run('process_credential_change', self._message('пароль-123'), state)
        self.assertEqual(self.db.passwords, [])
        state.finish.assert_awaited_once()
        self.assertEqual(self._sent(), ('❌ Оператор не найден в вашем отделе', ('меню', SV_FIRST, 'sv')))

    def test_write_that_found_nobody_is_not_a_success(self):
        self.db.update_user_password = mock.Mock(return_value=False)
        self.db.update_operator_login = mock.Mock(return_value=False)
        for action, value in (('password', 'пароль-123'), ('login', 'new.login')):
            self._run('process_credential_change', self._message(value), self._state(operator_id=103, action=action))
            self.assertEqual(self._sent()[0], '❌ Не удалось изменить данные оператора. Попробуйте позже.', action)

    def test_failed_write_is_reported_without_internals(self):
        self.db.update_user_password = mock.Mock(side_effect=RuntimeError('pool exhausted: секрет'))
        state = self._state(operator_id=103, action='password')
        self._run('process_credential_change', self._message('пароль-123'), state)
        self.assertEqual(self._sent()[0], '❌ Не удалось изменить данные оператора. Попробуйте позже.')
        state.finish.assert_awaited_once()

    # ─── Кнопка → её обработчик, по всем callback-обработчикам бота ──────────

    def test_each_screen_button_reaches_its_handler_first(self):
        found = []
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == 'callback_query_handler'):
                        found.append((decorator.lineno, node.name, decorator))
        found.sort(key=lambda item: item[0])
        self.assertGreater(len(found), 25)

        def first_catcher(data):
            for line, name, decorator in found:
                keywords = {k.arg: k.value for k in decorator.keywords}
                state = keywords.get('state')
                if state is not None and not (isinstance(state, ast.Constant) and state.value == '*'):
                    continue  # ловит только внутри своего диалога
                if not decorator.args:
                    self.fail('%s (строка %d): фильтр без функции и без состояния забирает всё' % (name, line))
                check = eval(compile(ast.Expression(decorator.args[0]), str(BOT_PY), 'eval'), {'re': re})
                if check(SimpleNamespace(data=data)):
                    return name
            return None

        expected = {'credgl': 'operator_credentials_list', 'cred_back': 'operator_credentials_list',
                    'credg_11_0': 'operator_credentials_list', 'credg_0_3': 'operator_credentials_list',
                    'cred_101': 'operator_credentials_menu', 'chlogin_101': 'operator_credentials_prompt',
                    'chpass_101': 'operator_credentials_prompt'}
        for data, handler in expected.items():
            self.assertEqual(first_catcher(data), handler, data)
        for data in ('cred_', 'cred_x', 'credg_1', 'chlogin_', 'chpass_1_2', 'credgl_1'):
            self.assertIsNone(first_catcher(data), data)


if __name__ == '__main__':
    unittest.main()
