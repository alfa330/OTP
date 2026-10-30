# -*- coding: utf-8 -*-
"""Выход из бота — у каждого, кто вошёл (30.10.2026).

У ролей без своих кнопок в боте (маркетинг, HR, бухгалтерия, тренер, стажёр) меню после входа
было пустым или из одной «Запросить код 🔐» (#373), а «Доступ🔑» с «Выход🚪» жили только в меню
админа, СВ и оператора: маркетолог с выданными кодами 2FA выйти из бота не мог ничем.

Что сторожится — настоящими функциями и обработчиками bot_schedule2 (через ast: импортировать
монолит тесты не могут) на настоящих кнопках aiogram, подменены только база и Telegram:
  * главное меню любой роли кончается одной строкой «Доступ🔑» — с кодами 2FA и без, и то же
    меню раздел «Коды 2FA» шлёт сам (_twofa_reply_menu) — без Postgres, то есть и в CI;
  * нажатие каждой кнопки пути первым забирает её обработчик — по ВСЕМ message_handler бота
    в порядке файла: перехватчик выше по файлу снова оставил бы человека без выхода;
  * путь маркетолога: вход → «Доступ🔑» → «Назад 🔙» → «Выход🚪» отвязывает Telegram;
  * после «Сменить логин» и «Сменить пароль» человек снова в меню с «Доступ🔑».
Данные — выдуманные: репозиторий публичный.
"""

import ast
import asyncio
import copy
import html
import logging
import re
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from twofa import rules  # noqa: E402

BOT_PY = ROOT / 'bot_schedule2.py'

ACCESS = 'Доступ🔑'
LOGOUT = 'Выход🚪'
BACK = 'Назад 🔙'
LOGIN = 'Вход👤'
CHANGE_LOGIN = 'Сменить логин'
CHANGE_PASSWORD = 'Сменить пароль'

NAMES = ('_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role', '_is_admin_role',
         'MAX_LOGIN_ATTEMPTS', 'get_admin_keyboard', 'get_sv_keyboard', 'get_operator_keyboard',
         'get_access_keyboard', 'get_cancel_keyboard', '_main_menu_keyboard', '_twofa_reply_menu')
HANDLERS = ('cancel_handler', 'start_command', 'start_auth', 'process_password', 'back_to_admin',
            'change_credentials_menu', 'change_login_start', 'process_new_login', 'change_password_start',
            'verify_current_password', 'process_new_password', 'logout_user')

# Обработчики с фильтром-функцией, которые тест умеет исполнить. Любой другой такой фильтр выше
# обработчика кнопки пути роняет тест: статически не понять, не забирает ли он нажатие.
KNOWN_FILTER_FUNCTIONS = ('handle_twofa_request_button',)

TG = 9020
MARKETER = 41001
MARKETER_NAME = 'Асель Тестбаева'


class FakeDb:
    """db бота — то, что трогают обработчики пути. Учётка — список как кортеж db.get_user:
    id, telegram_id, имя, роль; восьмым полем — логин, как у get_user_by_login."""

    UPDATES = {
        'UPDATE users SET telegram_id = NULL WHERE id = %s': lambda user, params: user.__setitem__(1, None),
        'UPDATE users SET login = %s WHERE id = %s': lambda user, params: user.__setitem__(7, params[0]),
        'UPDATE users SET password_hash = %s WHERE id = %s': lambda user, params: user.__setitem__(8, params[0]),
    }

    def __init__(self, *users):
        self.users = {user[0]: list(user) + [None] * (9 - len(user)) for user in users}
        self.executed = []

    def get_user(self, telegram_id=None, id=None):
        for user in self.users.values():
            if (id is not None and user[0] == id) or (telegram_id is not None and user[1] == telegram_id):
                return tuple(user[:4])
        return None

    def get_user_by_login(self, login):
        return next((tuple(user[:8]) for user in self.users.values() if user[7] == login), None)

    def verify_password(self, user_id, password):
        return password == 'верный пароль'

    def update_telegram_id(self, user_id, telegram_id):
        self.users[user_id][1] = telegram_id
        return telegram_id

    @contextmanager
    def _get_cursor(self):
        db = self

        class Cursor:
            def execute(self, sql, params):
                sql = ' '.join(sql.split())
                db.executed.append((sql, params))
                if sql not in FakeDb.UPDATES:
                    raise AssertionError('неожиданный запрос: %s' % sql)
                FakeDb.UPDATES[sql](db.users[params[-1]], params)

        yield Cursor()


def _message_handler_decorators(tree):
    """Все @dp.message_handler(...) бота в порядке регистрации — порядке строк файла
    (register_message_handler бот не зовёт; вложенные в функции тоже считаются)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == 'message_handler'):
                    found.append((decorator.lineno, node.name, decorator))
    return sorted(found, key=lambda item: item[0])


class BotAccessMenuTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

        from tests import source_cache

        cls.tree = source_cache.parse(BOT_PY.read_text(encoding='utf-8-sig'))
        body, cls.filters = [], {}
        for node in cls.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in NAMES:
                body.append(node)
            elif isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in NAMES for t in node.targets):
                body.append(node)
            elif isinstance(node, ast.AsyncFunctionDef) and node.name in HANDLERS:
                # Дерево общее на весь набор (tests/source_cache.py) — декоратор снимаем с копии.
                node = copy.deepcopy(node)
                [decorator] = node.decorator_list
                cls.filters[node.name] = {k.arg: ast.unparse(k.value) for k in decorator.keywords}
                node.decorator_list = []
                body.append(node)
        cls.granted = set()
        cls.namespace = {
            'ReplyKeyboardMarkup': ReplyKeyboardMarkup, 'KeyboardButton': KeyboardButton,
            'ReplyKeyboardRemove': ReplyKeyboardRemove, 'TWOFA_REQUEST_BUTTON': rules.REQUEST_BUTTON,
            '_twofa_can_request_for': lambda user_id: user_id in cls.granted,
            'types': mock.MagicMock(), 'FSMContext': object, 'html': html, 'logging': logging,
            'pbkdf2_sha256': SimpleNamespace(hash=lambda password: 'hash:' + password),
        }
        exec(compile(ast.Module(body=body, type_ignores=[]), str(BOT_PY), 'exec'), cls.namespace)
        missing = [name for name in NAMES + HANDLERS if name not in cls.namespace]
        if missing:
            raise AssertionError('не найдено в bot_schedule2.py: %s' % missing)

    def setUp(self):
        self.granted.clear()
        self.db = FakeDb((1, 101, 'Админ', 'super_admin'), (30, 130, 'Оператор', 'operator'),
                         (MARKETER, None, MARKETER_NAME, 'marketing_manager', None, None, None, 'test.marketer'))
        self.bot = SimpleNamespace(send_message=mock.AsyncMock())
        self.dp = SimpleNamespace(storage=SimpleNamespace(
            get_data=mock.AsyncMock(return_value={}), set_data=mock.AsyncMock(), reset_data=mock.AsyncMock()))
        self.dialog = SimpleNamespace(**{name: SimpleNamespace(set=mock.AsyncMock()) for name in (
            'waiting_for_new_login', 'waiting_for_current_password', 'waiting_for_new_password')})
        self.namespace.update(db=self.db, bot=self.bot, dp=self.dp, ChangeCredentials=self.dialog)

    # ─── Меню ────────────────────────────────────────────────────────────────

    @staticmethod
    def _rows(kb):
        return [[button['text'] for button in row] for row in kb.to_python()['keyboard']]

    def _menu(self, user_id, role):
        return self._rows(self.namespace['_main_menu_keyboard'](user_id, role))

    def test_every_role_ends_its_menu_with_the_access_row(self):
        roles = list(self.namespace['ROLE_HIERARCHY']) + ['supervisor', 'кто-то новый', None]
        for with_codes in (False, True):
            if with_codes:
                self.granted.add(7)
            for role in roles:
                rows = self._menu(7, role)
                self.assertEqual(rows[-1], [ACCESS], (role, with_codes))
                self.assertEqual(sum(row.count(ACCESS) for row in rows), 1, (role, with_codes))
                self.assertEqual(len(rows) > 1 and rows[-2] == [rules.REQUEST_BUTTON], with_codes,
                                 (role, with_codes))

    def test_staff_roles_get_codes_and_access_and_nothing_else(self):
        for role in ('marketing_manager', 'hr_manager', 'accounting_manager', 'trainer', 'trainee'):
            self.assertEqual(self._menu(MARKETER, role), [[ACCESS]], role)
        self.granted.add(MARKETER)
        self.assertEqual(self._menu(MARKETER, 'marketing_manager'), [[rules.REQUEST_BUTTON], [ACCESS]])

    def test_role_menus_without_codes_stay_as_they_were(self):
        self.assertEqual(self._menu(30, 'operator'), [['Моя статистика📊'], ['Мои оценки📝'], [ACCESS]])
        self.assertEqual(self._menu(31, 'sv'),
                         [['Добавить таблицу часов📊'], ['Управление операторами🔑'], [ACCESS]])
        self.assertEqual(self._menu(1, 'admin'), [['Редактор СВ📝', 'Операторы👷'], ['Данные📈'], [ACCESS]])

    def test_access_menu_holds_the_logout_and_the_way_back(self):
        rows = self._rows(self.namespace['get_access_keyboard']())
        self.assertIn([LOGOUT], rows)
        self.assertEqual(rows[-1], [BACK])

    def test_menu_the_codes_section_sends_itself_has_the_access_row(self):
        """«Вам открыт запрос кодов» и «запрос больше не открыт» несут меню из _twofa_reply_menu:
        роль без своих кнопок получает в нём «Доступ🔑», а не одну кнопку или «убрать клавиатуру»."""
        reply_menu = self.namespace['_twofa_reply_menu']
        access_row = [{'text': ACCESS}]
        self.assertEqual(reply_menu(MARKETER), {'keyboard': [access_row], 'resize_keyboard': True})
        self.granted.add(MARKETER)
        self.assertEqual(reply_menu(MARKETER),
                         {'keyboard': [[{'text': rules.REQUEST_BUTTON}], access_row], 'resize_keyboard': True})
        self.assertIsNone(reply_menu(999))  # учётки нет — меню нет, раздел уберёт кнопку сам

    # ─── Кнопка → её обработчик, по всем обработчикам бота ───────────────────

    def _catches(self, name, decorator, text):
        """Заберёт ли обработчик нажатие кнопки вне диалога в личке: True / False, None — не понять."""
        keywords = {k.arg: k.value for k in decorator.keywords}
        state = keywords.pop('state', None)
        if state is not None and not (isinstance(state, ast.Constant) and state.value == '*'):
            return False  # ловит только внутри своего диалога
        if keywords.pop('commands', None) is not None:
            return False  # подпись кнопки — не команда
        content_types = keywords.pop('content_types', None)
        if content_types is not None and not re.search(r'TEXT|ANY|text|any', ast.unparse(content_types)):
            return False
        regexp = keywords.pop('regexp', None)
        if regexp is not None and not re.search(ast.literal_eval(regexp), text):
            return False
        if keywords:
            return None
        if decorator.args:
            if name not in KNOWN_FILTER_FUNCTIONS:
                return None
            check = eval(compile(ast.Expression(decorator.args[0]), str(BOT_PY), 'eval'),
                         {'TWOFA_REQUEST_BUTTON': rules.REQUEST_BUTTON})
            return bool(check(self._message(text)))
        return True

    def test_each_button_reaches_its_handler_first(self):
        expected = {ACCESS: 'change_credentials_menu', LOGOUT: 'logout_user', BACK: 'back_to_admin',
                    CHANGE_LOGIN: 'change_login_start', CHANGE_PASSWORD: 'change_password_start',
                    LOGIN: 'start_auth', rules.REQUEST_BUTTON: 'handle_twofa_request_button'}
        decorators = _message_handler_decorators(self.tree)
        self.assertGreater(len(decorators), 40)
        for text, handler in expected.items():
            for line, name, decorator in decorators:
                caught = self._catches(name, decorator, text)
                if caught is None:
                    self.fail('%s (строка %d) стоит раньше %s, и его фильтр тест не умеет проверить: '
                              'не забирает ли он «%s»?' % (name, line, handler, text))
                if caught:
                    self.assertEqual(name, handler, '«%s» забирает %s (строка %d)' % (text, name, line))
                    break
            else:
                self.fail('«%s» не ловит никто' % text)

    def test_an_interceptor_above_would_be_caught(self):
        """Сам проверяльщик: обработчик regexp='Доступ' выше по файлу — тест обязан его увидеть."""
        stolen = ast.parse("@dp.message_handler(regexp='Доступ')\nasync def stolen(message): pass").body[0]
        [decorator] = stolen.decorator_list
        self.assertTrue(self._catches('stolen', decorator, ACCESS))
        unknown = ast.parse("@dp.message_handler(lambda m: 'Назад' in m.text, state='*')\n"
                            "async def unknown(message): pass").body[0]
        self.assertIsNone(self._catches('unknown', unknown.decorator_list[0], BACK))

    # ─── Путь маркетолога ────────────────────────────────────────────────────

    @staticmethod
    def _message(text):
        message = SimpleNamespace(text=text, chat=SimpleNamespace(id=TG, type='private'),
                                  from_user=SimpleNamespace(id=TG), message_id=55)
        message.delete = mock.AsyncMock()
        message.answer = mock.AsyncMock()
        return message

    def _run(self, handler, *args):
        asyncio.run(self.namespace[handler](*args))

    def _sent(self):
        kwargs = self.bot.send_message.await_args.kwargs
        markup = kwargs.get('reply_markup')
        return kwargs['text'], (self._rows(markup) if markup is not None else None)

    @staticmethod
    def _state(**data):
        return SimpleNamespace(finish=mock.AsyncMock(), get_data=mock.AsyncMock(return_value=data),
                               update_data=mock.AsyncMock())

    def _log_in(self):
        state = self._state(user={'id': MARKETER, 'name': MARKETER_NAME, 'role': 'marketing_manager',
                                  'login': 'test.marketer'})
        message = self._message('верный пароль')
        self._run('process_password', message, state)
        state.finish.assert_awaited_once()
        return self._rows(message.answer.await_args.kwargs['reply_markup'])

    def test_marketer_logs_in_and_out(self):
        self.granted.add(MARKETER)
        self.assertEqual(self._log_in(), [[rules.REQUEST_BUTTON], [ACCESS]])
        self.assertEqual(self.db.users[MARKETER][1], TG)

        # «Вы уже авторизованы» и /start — то же меню, с «Доступ🔑».
        self._run('start_auth', self._message(LOGIN))
        self.assertEqual(self._sent()[1], [[rules.REQUEST_BUTTON], [ACCESS]])
        self._run('start_command', self._message('/start'))
        self.assertEqual(self._sent()[1], [[rules.REQUEST_BUTTON], [ACCESS]])

        self._run('change_credentials_menu', self._message(ACCESS))
        self.assertIn([LOGOUT], self._sent()[1])

        self._run('back_to_admin', self._message(BACK))
        self.assertEqual(self._sent(), ('<b>Главное меню</b>', [[rules.REQUEST_BUTTON], [ACCESS]]))

        self._run('logout_user', self._message(LOGOUT))
        self.assertIsNone(self.db.users[MARKETER][1])
        self.assertEqual(self.db.executed, [('UPDATE users SET telegram_id = NULL WHERE id = %s', (MARKETER,))])
        text, rows = self._sent()
        self.assertEqual(rows, [[LOGIN]])
        self.assertIn('системы.</b>\n\nДля входа', text)

    def test_login_answer_carries_the_menu_with_the_access_row(self):
        self.assertEqual(self._log_in(), [[ACCESS]])  # кодов не выдано — одна строка, но выйти можно

    def test_already_logged_in_answer_names_the_way_out_without_role_codes(self):
        self.db.users[MARKETER][1] = TG
        self.db.users[MARKETER][2] = 'Асель & <Тестбаева>'
        self._run('start_auth', self._message(LOGIN))
        text, rows = self._sent()
        self.assertIn('Асель &amp; &lt;Тестбаева&gt;', text)
        self.assertNotIn('marketing_manager', text)
        self.assertIn('«%s» → «%s»' % (ACCESS, LOGOUT), text)
        self.assertEqual(rows, [[ACCESS]])

    def test_after_changing_the_password_the_menu_is_back(self):
        self.granted.add(MARKETER)
        self.db.users[MARKETER][1] = TG
        self._run('change_password_start', self._message(CHANGE_PASSWORD))
        self.dialog.waiting_for_current_password.set.assert_awaited_once()
        state = self._state()
        self._run('verify_current_password', self._message('верный пароль'), state)
        state.update_data.assert_awaited_once_with(user_id=MARKETER)
        self._run('process_new_password', self._message('новый-пароль'), self._state(user_id=MARKETER))
        self.assertEqual(self.db.users[MARKETER][8], 'hash:новый-пароль')
        self.assertEqual(self._sent(), ('✅ Пароль успешно изменен!', [[rules.REQUEST_BUTTON], [ACCESS]]))

    def test_after_changing_the_login_the_menu_is_back_and_the_login_is_escaped(self):
        self.db.users[MARKETER][1] = TG
        self._run('change_login_start', self._message(CHANGE_LOGIN))
        self.dialog.waiting_for_new_login.set.assert_awaited_once()
        self._run('process_new_login', self._message('ab<c&d'), self._state())
        self.assertEqual(self.db.users[MARKETER][7], 'ab<c&d')
        text, rows = self._sent()
        self.assertIn('<code>ab&lt;c&amp;d</code>', text)
        self.assertEqual(rows, [[ACCESS]])

    def test_failed_change_still_returns_the_menu(self):
        self.db.users[MARKETER][1] = TG
        broken = mock.patch.object(FakeDb, '_get_cursor', side_effect=RuntimeError('база'))
        with broken, self.assertLogs(level='ERROR'):
            self._run('process_new_login', self._message('new.login'), self._state())
        self.assertEqual(self._sent(), ('❌ Произошла ошибка при изменении логина. Попробуйте позже.', [[ACCESS]]))
        with broken, self.assertLogs(level='ERROR'):
            self._run('process_new_password', self._message('новый-пароль'), self._state(user_id=MARKETER))
        self.assertEqual(self._sent(), ('❌ Произошла ошибка при изменении пароля. Попробуйте позже.', [[ACCESS]]))

    def test_cancel_in_a_dialog_returns_the_menu_with_the_access_row(self):
        self.db.users[MARKETER][1] = TG
        state = SimpleNamespace(get_state=mock.AsyncMock(return_value='ChangeCredentials:waiting_for_new_login'),
                                finish=mock.AsyncMock())
        self._run('cancel_handler', self._message('Отмена ❌'), state)
        self.assertEqual(self._sent()[1], [[ACCESS]])

    def test_back_without_login_says_nothing(self):
        message = self._message(BACK)
        self._run('back_to_admin', message)
        self.bot.send_message.assert_not_awaited()
        message.delete.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
