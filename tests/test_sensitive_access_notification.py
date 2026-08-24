# -*- coding: utf-8 -*-
"""Уведомление в Telegram о выдаче QR-доступа.

Раньше сюда сваливали всё, что знал сервер: внутренние id, Telegram-id обеих
сторон, срок жизни токена, origin, оба user-agent целиком — сорок с лишним
строк, в которых главное («кто кому открыл») тонуло, и эмодзи в заголовке.
Остальное давно видно в разделе «Сессии».

Тест сторожит и краткость, и отсутствие того, чему в мессенджере не место.
"""

import ast
import copy
import html
import logging
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / 'bot_schedule2.py'

HELPERS = (
    '_truncate_for_telegram',
    '_normalize_user_role',
    '_escape_telegram_html',
    '_parse_user_agent_details',
    '_coerce_sensitive_access_datetime',
    '_format_sensitive_access_notification_dt',
    '_build_sensitive_access_approved_message_html',
)

EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF←-⇿⌀-➿️⬀-⯿]'
)


def _builder():
    module = source_cache.parse(BOT_PATH.read_text(encoding='utf-8-sig'))
    namespace = {
        'datetime': datetime,
        'timedelta': timedelta,
        're': re,
        'html': html,
        'logging': logging,
        'TELEGRAM_MAX_MESSAGE_CHARS': 4096,
        'SENSITIVE_ACCESS_ROLE_LABELS': {
            'super_admin': 'Супер админ', 'admin': 'Админ',
            'sv': 'Супервайзер', 'operator': 'Оператор',
        },
    }
    try:
        from zoneinfo import ZoneInfo
        namespace['ZoneInfo'] = ZoneInfo
        namespace['SENSITIVE_ACCESS_NOTIFICATION_TZ'] = ZoneInfo('Asia/Almaty')
    except Exception:  # pragma: no cover — на машине без tzdata
        namespace['SENSITIVE_ACCESS_NOTIFICATION_TZ'] = None
    for name in HELPERS:
        node = copy.deepcopy(next(n for n in module.body
                                  if isinstance(n, ast.FunctionDef) and n.name == name))
        node.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     str(BOT_PATH), 'exec'), namespace)
    return namespace['_build_sensitive_access_approved_message_html']


# users: id, telegram_id, name, role, direction, ?, supervisor_id, login
APPROVER = (2, 555001, 'Ядигаров Руслан', 'super_admin', None, None, None, 'ruslan')
OPERATOR = (448, 555002, 'Дуанаева Айша', 'operator', 'СЗоВ', None, 77, 'aisha')
SESSION = {
    'session_id': '3dbab764-1234-4b3c-9aaa-0011223344ff',
    'ip_address': '212.154.168.10',
    'user_agent': ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
                   'AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'),
    'created_at': datetime(2026, 8, 1, 10, 0),
    'last_seen_at': datetime(2026, 8, 24, 15, 0),
    'expires_at': datetime(2026, 9, 23, 10, 0),
}


class SensitiveAccessNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = staticmethod(_builder())

    def message(self, **overrides):
        kwargs = {
            'approver': APPROVER,
            'operator': OPERATOR,
            'claims': {'session_id': SESSION['session_id'],
                       'expires_at': datetime(2026, 8, 24, 16, 0)},
            'operator_session': SESSION,
            'approval_context': {'request_ip': '10.0.0.1',
                                 'request_user_agent': 'Mozilla/5.0 (Macintosh) Chrome/126',
                                 'request_origin': 'https://otp.example'},
            'operator_supervisor_name': 'Элекова Арайлым',
        }
        kwargs.update(overrides)
        return self.build(**kwargs)

    def plain(self, **overrides):
        return re.sub(r'</?[a-z]+>', '', self.message(**overrides))

    def test_message_is_short(self):
        text = self.message()
        self.assertLessEqual(len(text.split('\n')), 10, 'сообщение снова разрослось')
        self.assertLess(len(text), 600)

    def test_no_emoji(self):
        self.assertIsNone(EMOJI_RE.search(self.message()), 'эмодзи в уведомлении не нужны')

    def test_keeps_what_decisions_are_made_on(self):
        text = self.plain()
        self.assertIn('Дуанаева Айша', text)
        self.assertIn('@aisha', text)
        self.assertIn('оператор', text)
        self.assertIn('Элекова Арайлым', text)
        self.assertIn('Ядигаров Руслан', text)
        self.assertIn('супер админ', text)
        self.assertIn('212.154.168.10', text)
        self.assertIn('3dbab764', text)

    def test_drops_what_belongs_to_the_sessions_section(self):
        text = self.plain()
        for noise in ('555001', '555002', 'Telegram ID', 'Origin', 'otp.example',
                      'Mozilla/5.0', 'User-Agent', 'QR токен', 'Истекает',
                      'Последняя активность'):
            self.assertNotIn(noise, text, f'в уведомлении осталось лишнее: {noise}')

    def test_id_of_the_session_is_short_enough_to_search_by(self):
        text = self.plain()
        self.assertIn('3dbab764', text)
        self.assertNotIn(SESSION['session_id'], text, 'полный UUID в мессенджере не нужен')

    def test_survives_empty_input(self):
        text = self.plain(approver=(2,), operator=(448,), claims={},
                          operator_session=None, approval_context=None,
                          operator_supervisor_name=None)
        self.assertIn('Открыт доступ к чувствительным данным', text)
        self.assertNotIn(', —', text, 'роль неизвестна — не дописываем пустую')

    def test_html_is_escaped(self):
        text = self.message(operator=(448, None, '<b>Взлом</b>', 'operator',
                                      None, None, None, 'hack'))
        self.assertIn('&lt;b&gt;Взлом&lt;/b&gt;', text)


if __name__ == '__main__':
    unittest.main()
