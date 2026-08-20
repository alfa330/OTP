# -*- coding: utf-8 -*-
"""/help: список команд собирается по НАСТОЯЩИМ правам, а не по роли на глазок.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном
namespace — импортировать модуль нельзя, на старте он поднимает пул к боевой БД
(тот же приём, что в test_szov_wallboard.py). Подменяем только базу: правила
доступа берутся живые, поэтому тест ловит расхождение между тем, что /help
обещает, и тем, что команда реально сделает.
"""
import ast
import re
import time
import unittest
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

NAMES = {
    # правила доступа — живые, как их спрашивают сами команды
    '_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role',
    '_is_admin_role', '_is_super_admin_role', '_is_supervisor_role',
    'SZOV_WALLBOARD_DEPARTMENT_CODE', '_SZOV_WALLBOARD_DEPARTMENT_CACHE',
    '_SZOV_WALLBOARD_DEPARTMENT_CACHE_TTL', '_szov_wallboard_department_id',
    '_szov_wallboard_access_allowed', '_amo_leads_access_allowed',
    '_chat_hourly_access_allowed', '_front_office_calls_access_allowed',
    '_front_office_calls_manage_allowed',
    # собственно /help
    'BOT_HELP_SECTIONS', '_bot_help_flags', '_bot_help_text',
}

# Роли в user-кортеже db.get_user: (id, login, name, role, ...)
ADMIN = (1, 'admin', 'Админ', 'admin')
SV = (2, 'sv', 'Супервайзер', 'sv')
OPERATOR = (3, 'op', 'Оператор', 'operator')

SZOV_ID = 7
FRONT_OFFICE_ID = 9


class _FakeDB:
    """Ровно те методы базы, которые спрашивают правила доступа."""

    def __init__(self, headed=None, department=None):
        self._headed = headed or {}
        self._department = department or {}

    def get_departments(self):
        return [{'id': SZOV_ID, 'code': 'szov'}, {'id': FRONT_OFFICE_ID, 'code': 'front_office'}]

    def get_front_office_department_id(self):
        return FRONT_OFFICE_ID

    def headed_department_id_for_user(self, user_id):
        return self._headed.get(user_id)

    def get_user_department_id(self, user_id):
        return self._department.get(user_id)


def _namespace(db):
    tree = source_cache.parse(SOURCE)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            body.append(node)
        elif isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & NAMES:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {'time': time, 'db': db, '_headed_department_id': lambda _user_id: None}
    exec(compile(module, "<bot-help>", "exec"), ns)
    missing = sorted(name for name in NAMES if name not in ns)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    # Кэш отдела общий на процесс — сбрасываем, иначе фикстуры текут между тестами.
    ns['_SZOV_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
    return ns


def _commands(text):
    """Команды, названные в ответе. Закрывающие теги (</b>) за команды не считаем."""
    return {match.group(0) for match in re.finditer(r'(?<![<\w])/[a-z_]+', text)}


class HelpFlagsTests(unittest.TestCase):
    """Кому какие разделы достаются."""

    def test_operator_sees_only_basics(self):
        ns = _namespace(_FakeDB(department={OPERATOR[0]: SZOV_ID}))
        flags = ns['_bot_help_flags'](OPERATOR, is_private=True, group_late_chat=None)
        self.assertTrue(flags['basics'])
        self.assertFalse(any(flags[key] for key in flags if key != 'basics'))
        text = ns['_bot_help_text'](flags)
        self.assertEqual(_commands(text), {'/start', '/help'})

    def test_admin_sees_every_section_except_group_only(self):
        ns = _namespace(_FakeDB())
        flags = ns['_bot_help_flags'](ADMIN, is_private=True, group_late_chat=None)
        for key in ('basics', 'tablo', 'leads', 'obzvon', 'obzvon_manage', 'chats'):
            self.assertTrue(flags[key], key)
        # /report отвечает только в чате контроля опозданий — в личке его нет.
        self.assertFalse(flags['report'])
        self.assertNotIn('/report', _commands(ns['_bot_help_text'](flags)))

    def test_szov_supervisor_gets_tablo_and_chats_but_not_leads(self):
        ns = _namespace(_FakeDB(department={SV[0]: SZOV_ID}))
        flags = ns['_bot_help_flags'](SV, is_private=True, group_late_chat=None)
        self.assertTrue(flags['tablo'])
        self.assertTrue(flags['chats'])
        self.assertFalse(flags['leads'])
        self.assertFalse(flags['obzvon'])

    def test_supervisor_of_another_department_gets_nothing_extra(self):
        ns = _namespace(_FakeDB(department={SV[0]: 42}))
        flags = ns['_bot_help_flags'](SV, is_private=True, group_late_chat=None)
        self.assertFalse(flags['tablo'])
        self.assertFalse(flags['chats'])

    def test_front_office_supervisor_sees_report_but_not_its_settings(self):
        ns = _namespace(_FakeDB(department={SV[0]: FRONT_OFFICE_ID}))
        flags = ns['_bot_help_flags'](SV, is_private=True, group_late_chat=None)
        self.assertTrue(flags['obzvon'])
        self.assertFalse(flags['obzvon_manage'])
        commands = _commands(ns['_bot_help_text'](flags))
        self.assertIn('/obzvon', commands)
        self.assertNotIn('/obzvon_subscribe', commands)

    def test_front_office_head_manages_the_plan(self):
        ns = _namespace(_FakeDB(headed={SV[0]: FRONT_OFFICE_ID}))
        flags = ns['_bot_help_flags'](SV, is_private=True, group_late_chat=None)
        self.assertTrue(flags['obzvon'])
        self.assertTrue(flags['obzvon_manage'])
        self.assertIn('/obzvon_subscribe', _commands(ns['_bot_help_text'](flags)))

    def test_report_shows_up_in_late_control_chat(self):
        ns = _namespace(_FakeDB())
        flags = ns['_bot_help_flags'](
            OPERATOR, is_private=False, group_late_chat={'chat_id': '-100', 'departments': []})
        self.assertTrue(flags['report'])
        # В группе /start не предлагаем: он уводит в личку и удаляет сообщение.
        self.assertFalse(flags['basics'])
        commands = _commands(ns['_bot_help_text'](flags))
        self.assertIn('/report', commands)
        self.assertNotIn('/start', commands)


class HelpTextTests(unittest.TestCase):
    """Сам текст: разметка, полнота, крайние случаи."""

    def test_stranger_in_a_random_group_gets_a_short_refusal(self):
        ns = _namespace(_FakeDB())
        flags = ns['_bot_help_flags'](None, is_private=False, group_late_chat=None)
        text = ns['_bot_help_text'](flags, has_user=False)
        self.assertIn('в личку', text)
        self.assertNotIn('<b>', text)

    def test_guest_in_private_is_pointed_at_the_login(self):
        ns = _namespace(_FakeDB())
        flags = ns['_bot_help_flags'](None, is_private=True, group_late_chat=None)
        text = ns['_bot_help_text'](flags, has_user=False)
        self.assertEqual(_commands(text), {'/start', '/help'})
        self.assertIn('не вошли', text)

    def test_html_tags_are_balanced(self):
        ns = _namespace(_FakeDB())
        flags = ns['_bot_help_flags'](ADMIN, is_private=True, group_late_chat=None)
        text = ns['_bot_help_text'](flags)
        for tag in ('b', 'i'):
            self.assertEqual(text.count('<%s>' % tag), text.count('</%s>' % tag), tag)
        # Символов, которые Telegram в HTML-режиме понял бы как разметку, в тексте нет.
        self.assertEqual(re.findall(r'<(?!/?[bi]>)', text), [])

    def test_help_is_the_only_command_in_the_blue_menu(self):
        """Меню — единственный способ найти /help. Уберут его — раздел снова спрячется."""
        self.assertIn("types.BotCommand(command='help'", SOURCE)
        self.assertIn("await bot.set_my_commands(commands, scope=scope)", SOURCE)
        # Остальные команды в меню не выкладываем: оно одно на всех, а права разные.
        self.assertEqual(len(re.findall(r"types\.BotCommand\(", SOURCE)), 1)
        self.assertIn("await _sync_bot_commands()", SOURCE)

    def test_handler_answers_from_any_state(self):
        """Посреди «Входа» /help должен остаться командой, а не уйти в пароль."""
        self.assertIn("@dp.message_handler(commands=['help'], state='*')", SOURCE)

    def test_every_command_of_the_bot_is_described(self):
        """Главное: добавили команду в бота — она обязана попасть в /help.

        Иначе смысл раздела теряется: человек снова не может её найти."""
        handlers = set()
        for match in re.finditer(r"message_handler\(commands=\[([^\]]+)\]", SOURCE):
            handlers |= {name.strip().strip("'\"") for name in match.group(1).split(',')}
        described = set()
        for _key, _title, rows in _namespace(_FakeDB())['BOT_HELP_SECTIONS']:
            described |= {command.split()[0].lstrip('/') for command, _about in rows}
        self.assertEqual(handlers - described, set())


if __name__ == '__main__':
    unittest.main()
