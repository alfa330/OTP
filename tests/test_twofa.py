# -*- coding: utf-8 -*-
"""Раздел «Коды 2FA» (#373): код, разбор QR, шифрование, права, тексты и проводка.

Что сторожится:
  * код считается ровно как в Google Authenticator — контрольные значения RFC 6238;
  * правило ТЗ п. 5: меньше 10 секунд до смены — выдаётся следующий код;
  * экспорт Google Authenticator разбирается (несколько аккаунтов, части экспорта, '+' без
    %2B), а то, что бот посчитал бы неверно (HOTP, 8 цифр, SHA256), — отсекается с причиной;
  * ключи шифруются AES-256-GCM ключом из окружения; подмену шифротекста видно;
  * права: подтверждает только глава маркетинга, супер-админ ведёт, но не подтверждает,
    уволенный не получает ничего, без ключа шифрования раздел виден только супер-админу;
  * раздел подключён: схема до триггеров колокола, Blueprint, кнопки бота ПЕРВЫМИ среди
    callback-хендлеров, флаг профиля, пункт меню, гарды и источник колокола.

Процесс на настоящей базе — tests/test_twofa_postgres.py.
"""

import ast
import base64
import hashlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from twofa import access, crypto, otpauth, rules, timer, totp  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
BOT_PY = ROOT / 'bot_schedule2.py'
DATABASE_PY = ROOT / 'database.py'
SOURCES_PY = ROOT / 'notifications' / 'sources.py'
FA_ICON = ROOT / 'src' / 'components' / 'common' / 'FaIcon.jsx'
VIEW_JSX = ROOT / 'src' / 'components' / 'twofa' / 'TwofaView.jsx'
HISTORY_JSX = ROOT / 'src' / 'components' / 'twofa' / 'TwofaHistory.jsx'

RFC_SECRET = b'12345678901234567890'


def _read(path):
    return path.read_text(encoding='utf-8-sig')


def _function_source(source, name):
    """Текст функции верхнего уровня из bot_schedule2 — по ast, а не по поиску строки."""
    from tests import source_cache

    for node in source_cache.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return '\n'.join(source.splitlines()[node.lineno - 1:node.end_lineno])
    raise AssertionError('нет функции %s' % name)


def _varint(n):
    out = b''
    while True:
        byte, n = n & 0x7F, n >> 7
        if n:
            out += bytes([byte | 0x80])
        else:
            return out + bytes([byte])


def _field(number, wire, value):
    key = _varint((number << 3) | wire)
    return key + (_varint(len(value)) + value if wire == 2 else _varint(value))


def _otp(secret, name, issuer, algorithm=1, digits=1, kind=2):
    return (_field(1, 2, secret) + _field(2, 2, name.encode()) + _field(3, 2, issuer.encode())
            + _field(4, 0, algorithm) + _field(5, 0, digits) + _field(6, 0, kind))


def migration_uri(*params, size=1, index=0, encode=True):
    payload = b''.join(_field(1, 2, p) for p in params) + _field(2, 0, 1) + _field(3, 0, size) + _field(4, 0, index)
    data = base64.b64encode(payload).decode()
    return 'otpauth-migration://offline?data=' + (quote(data, safe='') if encode else data)


class TotpTest(unittest.TestCase):
    def test_rfc6238_reference_values(self):
        """RFC 6238, приложение B (SHA1): 8-значные значения; 6 цифр — их хвост."""
        for unix_time, code8 in ((59, '94287082'), (1111111109, '07081804'), (1111111111, '14050471'),
                                 (1234567890, '89005924'), (2000000000, '69279037'), (20000000000, '65353130')):
            self.assertEqual(totp.hotp(RFC_SECRET, unix_time // 30, digits=8), code8)
            self.assertEqual(totp.code_at(RFC_SECRET, unix_time), code8[-6:])

    def test_ten_seconds_or_more_left_gives_the_current_code(self):
        issued = totp.issue(RFC_SECRET, 600 + 20)  # до смены ровно 10 с
        self.assertFalse(issued['is_next'])
        self.assertEqual((issued['valid_from'], issued['valid_until']), (600, 630))
        self.assertEqual(issued['code'], totp.code_at(RFC_SECRET, 620))

    def test_less_than_ten_seconds_left_gives_the_next_code(self):
        issued = totp.issue(RFC_SECRET, 600 + 20.5)
        self.assertTrue(issued['is_next'])
        self.assertEqual((issued['valid_from'], issued['valid_until']), (630, 660))
        self.assertEqual(issued['code'], totp.code_at(RFC_SECRET, 630))

    def test_secret_is_read_the_way_services_show_it(self):
        b32 = base64.b32encode(RFC_SECRET).decode()
        grouped = ' '.join(b32[i:i + 4].lower() for i in range(0, len(b32), 4))
        self.assertEqual(totp.normalize_secret(grouped), RFC_SECRET)
        self.assertEqual(totp.normalize_secret(b32.rstrip('=')), RFC_SECRET)

    def test_not_a_key_is_refused(self):
        for text in ('', '   ', 'ключ', 'AB1!', 'AAAA', 'A' * 1000):
            with self.assertRaises(ValueError, msg=text):
                totp.normalize_secret(text)

    def test_long_key_counts_like_google_authenticator(self):
        """Ключ длиннее 64 байт (так у Tilda, 27.10.2026) не обрезается и не отсекается: HMAC
        сначала сжимает его SHA1 (RFC 2104) — так же, как приложение."""
        key = bytes(range(100))
        self.assertEqual(totp.normalize_secret(base64.b32encode(key).decode()), key)
        for counter in (0, 1, 37037036, 66666666):
            self.assertEqual(totp.hotp(key, counter), totp.hotp(hashlib.sha1(key).digest(), counter))
        longest = b'k' * totp.MAX_SECRET_BYTES
        self.assertEqual(totp.validate_secret(longest), longest)
        with self.assertRaises(ValueError):
            totp.validate_secret(longest + b'k')


class OtpauthTest(unittest.TestCase):
    b32 = base64.b32encode(RFC_SECRET).decode().rstrip('=')

    def test_service_qr_with_issuer_in_label(self):
        parsed = otpauth.parse('otpauth://totp/Google%3Aads%40company.kz?secret=' + self.b32)
        [entry] = parsed['entries']
        self.assertEqual((parsed['kind'], entry['issuer'], entry['name']), ('single', 'Google', 'ads@company.kz'))
        self.assertTrue(entry['supported'])
        self.assertEqual(entry['secret'], RFC_SECRET)

    def test_what_the_bot_would_count_wrong_is_marked_unsupported(self):
        for query, word in (('&digits=8', 'цифр'), ('&algorithm=SHA256', 'SHA256'), ('&period=60', '60')):
            [entry] = otpauth.parse('otpauth://totp/X?secret=' + self.b32 + query)['entries']
            self.assertFalse(entry['supported'])
            self.assertIn(word, entry['reason'])
        [entry] = otpauth.parse('otpauth://hotp/X?secret=' + self.b32 + '&counter=3')['entries']
        self.assertIn('HOTP', entry['reason'])

    def test_google_authenticator_export_with_several_accounts(self):
        uri = migration_uri(_otp(RFC_SECRET, 'ads@company.kz', 'Google'),
                            _otp(b'abcdefghij0123456789', 'shop', 'Meta', kind=1),
                            _otp(b'0123456789abcdefghij', 'tt', 'TikTok', digits=2), size=3, index=1)
        parsed = otpauth.parse(uri)
        self.assertEqual(parsed['kind'], 'migration')
        self.assertEqual(parsed['batch'], {'size': 3, 'index': 1})
        self.assertEqual([e['supported'] for e in parsed['entries']], [True, False, False])
        self.assertEqual(parsed['entries'][0]['secret'], RFC_SECRET)

    def test_plus_in_export_data_survives_without_percent_encoding(self):
        # Ключ подбирается так, чтобы в base64 экспорта оказался '+': часть сканеров отдаёт
        # его как есть, без %2B, а parse_qs превратил бы его в пробел.
        secret = next(bytes([b]) * 20 for b in range(256)
                      if '+' in migration_uri(_otp(bytes([b]) * 20, 'plus', 'P'), encode=False))
        uri = migration_uri(_otp(secret, 'plus', 'P'), encode=False)
        self.assertIn('+', uri)
        self.assertEqual(otpauth.parse(uri)['entries'][0]['secret'], secret)

    def test_key_longer_than_64_bytes_is_accepted(self):
        """Tilda в экспорте Google Authenticator (27.10.2026): прежний предел 64 байта отсекал
        такой ключ как «вставлен лишний текст», и аккаунт было не добавить."""
        key = bytes(range(7, 107))
        [entry] = otpauth.parse(migration_uri(_otp(key, 'marketing@company.kz', 'Tilda')))['entries']
        self.assertTrue(entry['supported'], entry['reason'])
        self.assertEqual(entry['secret'], key)
        b32 = base64.b32encode(key).decode().rstrip('=')
        [entry] = otpauth.parse('otpauth://totp/Tilda%3Amarketing%40company.kz?secret=' + b32)['entries']
        self.assertTrue(entry['supported'], entry['reason'])
        self.assertEqual(entry['secret'], key)

    def test_unknown_fields_of_newer_app_versions_are_skipped(self):
        params = _otp(RFC_SECRET, 'n', 'I') + _field(8, 2, b'unique-id') + _field(9, 0, 5)
        self.assertTrue(otpauth.parse(migration_uri(params))['entries'][0]['supported'])

    def test_raw_key(self):
        parsed = otpauth.parse('jbsw y3dp ehpk 3pxp')
        self.assertEqual(parsed['kind'], 'single')
        self.assertEqual(parsed['entries'][0]['secret'], base64.b32decode('JBSWY3DPEHPK3PXP'))

    def test_refusals(self):
        for text in ('', 'https://example.com/login', 'otpauth-migration://offline?data=',
                     'otpauth-migration://offline?data=' + quote(base64.b64encode(b'\x0a\x7f').decode(), safe=''),
                     'otpauth://totp/X?issuer=Y', 'x' * 9000):
            with self.assertRaises(otpauth.ParseError, msg=text[:40]):
                otpauth.parse(text)

    def test_public_entry_has_no_key(self):
        [entry] = otpauth.parse('otpauth://totp/X?secret=' + self.b32)['entries']
        self.assertNotIn('secret', otpauth.public(entry))


class CryptoTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {crypto.ENV_KEY: base64.b64encode(b'z' * 32).decode()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip_and_fresh_nonce(self):
        first, second = crypto.encrypt(RFC_SECRET, 7), crypto.encrypt(RFC_SECRET, 7)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith('v2:'))
        self.assertEqual(crypto.decrypt(first, 7), RFC_SECRET)
        self.assertNotIn(base64.b32encode(RFC_SECRET).decode()[:8], first)

    def test_tampering_another_key_and_another_row_are_detected(self):
        token = crypto.encrypt(RFC_SECRET, 7)
        blob = bytearray(base64.b64decode(token[3:]))
        blob[-1] ^= 1
        with self.assertRaises(ValueError):
            crypto.decrypt('v2:' + base64.b64encode(bytes(blob)).decode(), 7)
        with mock.patch.dict(os.environ, {crypto.ENV_KEY: base64.b64encode(b'y' * 32).decode()}):
            with self.assertRaises(ValueError):
                crypto.decrypt(token, 7)
        # Шифротекст, переписанный в строку другого аккаунта, не расшифровывается (разбор 27.10).
        with self.assertRaises(ValueError):
            crypto.decrypt(token, 8)

    def test_first_release_format_is_still_readable(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = b'n' * 12
        sealed = AESGCM(b'z' * 32).encrypt(nonce, RFC_SECRET, b'icore-twofa-account-secret:v1')
        legacy = 'v1:' + base64.b64encode(nonce + sealed).decode()
        self.assertEqual(crypto.decrypt(legacy, 123), RFC_SECRET)

    def test_key_status(self):
        self.assertEqual(crypto.key_status(), 'ok')
        with mock.patch.dict(os.environ, {crypto.ENV_KEY: base64.b64encode(b'short').decode()}):
            self.assertEqual(crypto.key_status(), 'invalid')
            with self.assertRaises(crypto.KeyMissing):
                crypto.encrypt(RFC_SECRET, 1)
        # 32 символа — это 24 байта, а не 32: так ключ вставили 27.10.2026, раздел его отверг.
        with mock.patch.dict(os.environ, {crypto.ENV_KEY: 'A' * 32}):
            self.assertEqual(crypto.key_status(), 'invalid')
        with mock.patch.dict(os.environ, {crypto.ENV_KEY: ''}):
            self.assertEqual(crypto.key_status(), 'missing')

    def test_aes_256_not_portal_fernet(self):
        source = (ROOT / 'twofa' / 'crypto.py').read_text(encoding='utf-8')
        self.assertIn('AESGCM', source)
        self.assertNotIn('import Fernet', source)


def ctx(user_id, role='marketing_manager', status='working', approver=10, granted=(), key='ok'):
    return {'user_id': user_id, 'name': 'X', 'role': role, 'status': status, 'telegram_id': 1,
            'approver_id': approver, 'granted_account_ids': list(granted), 'key_status': key}


class TimerTest(unittest.TestCase):
    def test_sleep_is_capped_so_deadlines_of_another_instance_are_not_missed(self):
        """При выкладке срок, заведённый старым экземпляром, будит только его нить: новый
        будильник обязан проснуться сам не позже чем через минуту (разбор 27.10.2026)."""
        self.assertEqual(timer.Alarm.sleep_for(None, 1000), timer.MAX_SLEEP_SECONDS)
        self.assertEqual(timer.Alarm.sleep_for(1000 + 3600, 1000), timer.MAX_SLEEP_SECONDS)
        self.assertAlmostEqual(timer.Alarm.sleep_for(1005, 1000), 5.2)
        self.assertAlmostEqual(timer.Alarm.sleep_for(990, 1000), 0.2)
        self.assertLessEqual(timer.MAX_SLEEP_SECONDS, 60)


class AccessTest(unittest.TestCase):
    def test_head_of_marketing_approves_and_manages(self):
        head = ctx(10, role='admin')
        self.assertTrue(access.can_approve(head) and access.can_manage(head) and access.can_open_section(head))
        self.assertFalse(access.can_request(head))

    def test_super_admin_manages_and_approves(self):
        """Владелец 27.10.2026: «сделай чтобы мог глава маркетинга и суперадмины»."""
        sup = ctx(1, role='super_admin')
        self.assertTrue(access.can_manage(sup))
        self.assertTrue(access.can_approve(sup))
        self.assertFalse(access.can_approve(ctx(1, role='super_admin', key='missing')))
        self.assertFalse(access.can_approve(ctx(1, role='super_admin', status='fired')))
        self.assertTrue(access.can_open_section(ctx(1, role='super_admin', key='missing')))

    def test_employee_requests_only_granted_accounts(self):
        anna = ctx(20, granted=[5])
        self.assertTrue(access.can_request(anna, 5))
        self.assertFalse(access.can_request(anna, 6))
        self.assertFalse(access.can_manage(anna) or access.can_approve(anna))
        self.assertFalse(access.can_open_section(ctx(21)))

    def test_fired_gets_nothing(self):
        for status in ('fired', 'dismissal'):
            self.assertFalse(access.can_open_section(ctx(20, status=status, granted=[5])))
            self.assertFalse(access.can_open_section(ctx(10, role='admin', status=status)))

    def test_without_the_key_only_super_admin_sees_the_section(self):
        self.assertFalse(access.can_open_section(ctx(10, role='admin', key='missing')))
        self.assertFalse(access.can_open_section(ctx(20, granted=[5], key='invalid')))

    def test_capabilities_promise_nothing_to_the_closed(self):
        self.assertEqual(access.capabilities(ctx(21)),
                         {'can_open': False, 'can_manage': False, 'can_approve': False, 'can_request': False})


class RulesTest(unittest.TestCase):
    def test_section_title_is_cleaned_like_an_account_title(self):
        self.assertEqual(rules.clean_group_title('  SMM   и  таргет '), 'SMM и таргет')
        for bad in ('', '   ', None, 'x' * (rules.TITLE_MAX + 1)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.clean_group_title(bad)

    def test_section_events_are_labelled(self):
        for kind in ('account_moved', 'group_created', 'group_renamed', 'group_deleted', 'groups_introduced'):
            self.assertIn(kind, rules.EVENT_LABELS)
        # Собрало аккаунты обновление, а не человек; остальное с разделами делает человек.
        self.assertIn('groups_introduced', rules.SYSTEM_EVENTS)
        self.assertFalse({'account_moved', 'group_created', 'group_renamed', 'group_deleted'} & rules.SYSTEM_EVENTS)

    def test_sections_are_gathered_once_when_the_column_appears(self):
        """На проде 16 аккаунтов уже есть: их раздел ставится в момент появления колонки и
        только тогда — без Postgres (в CI Postgres-тесты пропускаются)."""
        from twofa import schema

        sql = schema._GROUPS_INTRODUCED
        self.assertIn(sql, schema._MIGRATIONS)
        guard = sql.index("column_name = 'group_id'")
        self.assertLess(guard, sql.index('ADD COLUMN group_id'))
        self.assertLess(sql.index('ADD COLUMN group_id'), sql.index('INSERT INTO twofa_groups'))
        self.assertIn('ON DELETE SET NULL', sql)
        self.assertEqual(schema.DEFAULT_GROUP_TITLE, 'Общий маркетинг и рекламные аккаунты')
        self.assertIn("'groups_introduced'", sql)

    def test_terms_from_the_tz(self):
        self.assertEqual((rules.REQUEST_TTL_SECONDS, rules.RESEND_WINDOW_SECONDS, rules.CODE_VISIBLE_SECONDS),
                         (600, 180, 60))
        self.assertEqual(totp.NEXT_CODE_THRESHOLD_SECONDS, 10)

    def test_almaty_clock(self):
        self.assertEqual(rules.clock(1761553800), '13:30:00')
        self.assertAlmostEqual(rules.epoch(rules.almaty(1761553800.25)), 1761553800.25, places=5)
        self.assertEqual(rules.iso(rules.almaty(1761553800)), '2025-10-27T13:30:00+05:00')

    def test_names_are_escaped_in_telegram_html(self):
        text = rules.approval_text(requester_name='<b>Анна</b>', account_title='A&B', service='<i>',
                                   created_at=0, expires_at=600)
        self.assertNotIn('<b>Анна</b>', text)
        self.assertIn('&lt;b&gt;Анна&lt;/b&gt;', text)
        self.assertIn('A&amp;B', text)

    def test_code_message_says_until_when(self):
        issued = totp.issue(RFC_SECRET, 1761553805)
        text = rules.code_text(account_title='Google Ads', issued=issued, now=1761553805)
        self.assertIn('<code>%s</code>' % issued['code'], text)
        self.assertIn('Действует до <b>13:30:30</b>', text)
        self.assertNotIn('Начнёт', text)

    def test_code_copies_without_spaces(self):
        # Нажатие на код в Telegram копирует его как есть: с «123 456» вставка давала «неверный код».
        for unix_time in (1761553805, 1761553825):  # текущий код и следующий (до смены < 10 с)
            issued = totp.issue(RFC_SECRET, unix_time)
            text = rules.code_text(account_title='Google Ads', issued=issued, now=unix_time)
            self.assertEqual(re.findall(r'<code>(.*?)</code>', text), [issued['code']])
            self.assertRegex(issued['code'], r'^\d{6}$')

    def test_closed_mark_names_who_decided(self):
        text = rules.approval_closed_text(requester_name='Анна', account_title='Google', status='approved',
                                          decided_at=1761553800, decided_by='<Супер>')
        self.assertIn('✅ Подтверждено в 13:30 — &lt;Супер&gt;', text)
        expired = rules.approval_closed_text(requester_name='Анна', account_title='Google', status='expired',
                                             decided_at=1761553800, decided_by='X')
        self.assertNotIn(' — X', expired)

    def test_every_status_has_a_label_and_a_closed_mark(self):
        for status in ('pending', 'approved', 'rejected', 'expired', 'cancelled'):
            self.assertIn(status, rules.STATUS_LABELS)
        for status in ('approved', 'rejected', 'expired', 'cancelled'):
            text = rules.approval_closed_text(requester_name='A', account_title='B', status=status, decided_at=0)
            self.assertNotIn(status, text)

    def test_buttons_fit_telegram_callback_limit(self):
        markup = rules.approval_markup(2 ** 31 - 1)
        for button in markup['inline_keyboard'][0] + rules.resend_markup(2 ** 31 - 1)['inline_keyboard'][0]:
            self.assertLessEqual(len(button['callback_data'].encode()), 64)

    def test_account_choice_is_a_list_with_a_cancel(self):
        rows = rules.choose_markup([
            {'id': 2 ** 31 - 1, 'title': 'Google Ads', 'service': 'Google'},
            {'id': 7, 'title': 'ads@company.kz', 'service': 'Meta'},
        ])['inline_keyboard']
        self.assertEqual([len(row) for row in rows], [1, 1, 1])
        self.assertEqual(rows[0][0]['text'], 'Google Ads')  # сервис уже в названии
        self.assertEqual(rows[1][0], {'text': 'ads@company.kz · Meta', 'callback_data': 'twofa:req:7'})
        self.assertEqual(rows[-1][0], {'text': 'Отмена', 'callback_data': 'twofa:x:0'})
        for row in rows:
            self.assertLessEqual(len(row[0]['callback_data'].encode()), 64)

    def test_account_label_is_plain_text_and_short(self):
        # Подпись кнопки Telegram не разбирает как HTML: экранировать — значит показать «&amp;».
        self.assertEqual(rules.account_button_label({'title': 'A & <B>', 'service': None}), 'A & <B>')
        long = rules.account_button_label({'title': 'x' * 120, 'service': None})
        self.assertEqual(len(long), rules.ACCOUNT_BUTTON_MAX)
        self.assertTrue(long.endswith('…'))

    def test_bot_texts_name_the_button_and_escape_titles(self):
        self.assertIn('«%s»' % rules.REQUEST_BUTTON, rules.expired_text(account_title='G'))
        opened = rules.access_opened_text(account_titles=['A&B', 'Meta'])
        self.assertIn('«A&amp;B», «Meta»', opened)
        self.assertIn('«%s»' % rules.REQUEST_BUTTON, opened)
        sent = rules.request_sent_text(account_title='<G>', created_at=1761553800, expires_at=1761554400)
        self.assertIn('«&lt;G&gt;» отправлен в 13:30', sent)
        self.assertIn('сгорит в 13:40', sent)
        self.assertEqual(rules.request_button_menu()['keyboard'], [[{'text': rules.REQUEST_BUTTON}]])

    def test_titles(self):
        self.assertEqual(rules.clean_title('  Google   Ads '), 'Google Ads')
        with self.assertRaises(ValueError):
            rules.clean_title('  ')
        with self.assertRaises(ValueError):
            rules.clean_title('x' * 121)
        self.assertIsNone(rules.clean_service(' '))
        self.assertEqual(rules.suggested_title({'issuer': 'Google', 'name': 'ads@c.kz'}), 'Google · ads@c.kz')
        self.assertEqual(rules.suggested_title({'issuer': 'Meta', 'name': 'Meta'}), 'Meta')


class WiringTest(unittest.TestCase):
    """Раздел подключён — сверка текстом, как у соседних разделов."""

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_JSX)
        cls.bot = _read(BOT_PY)
        cls.database = _read(DATABASE_PY)

    def test_schema_goes_before_the_bell_triggers(self):
        init = self.database.index('self._init_twofa_schema_tx(cursor)')
        bell = self.database.index('self._init_bell_notify_schema_tx(cursor)')
        self.assertLess(init, bell)
        self.assertIn("ELSIF TG_TABLE_NAME = 'twofa_requests' THEN", self.database)
        self.assertIn("('trg_bell_twofa_requests_insert', 'twofa_requests', 'AFTER INSERT', '')", self.database)

    def test_bot_wiring(self):
        self.assertIn('app.register_blueprint(build_twofa_blueprint(', self.bot)
        self.assertIn('"twofa_access": twofa_access,', self.bot)
        handler = self.bot.index("c.data.startswith('twofa:'), state='*')")
        first_state_handler = self.bot.index('@dp.callback_query_handler(state=')
        self.assertLess(handler, first_state_handler, 'кнопки 2FA должны стоять раньше хендлеров со state=')
        first_callback = self.bot.index('@dp.callback_query_handler(')
        self.assertLess(handler - first_callback, 200)
        # Будильник — только в __main__: импорт монолита в тестах не поднимает нить.
        main = self.bot.index("if __name__ == '__main__':")
        self.assertGreater(self.bot.index('    _twofa_start_alarm()'), main)
        self.assertNotIn('.start()', self.bot[self.bot.index("_TWOFA = {'service': None"):
                                              self.bot.index('def _twofa_section_open_for')])

    def test_menu_and_guards(self):
        self.assertIn('const canAccessTwofaSectionForUser = (userLike) => userLike?.twofa_access === true;', self.app)
        self.assertEqual(self.app.count("{canAccessTwofaSection && ("), 1)
        self.assertEqual(self.app.count('<span className="sidebar-text">Коды 2FA</span>'), 1)
        self.assertIn("(requestedViewFromUrl !== 'twofa' || canAccessTwofaSection) &&", self.app)
        self.assertIn("if (view === 'twofa' && canAccessTwofaSection) return;", self.app)
        self.assertIn("if (view === 'twofa' && !canAccessTwofaSection) {", self.app)
        self.assertIn("{view === 'twofa' && canAccessTwofaSection && (", self.app)
        self.assertIn('|| canAccessTwofaSection\n', self.app)
        self.assertIn("'fa-shield-halved': 'ShieldCheck'", _read(FA_ICON))

    def test_bell_wakes_the_screen_on_delivery_result_and_the_requester_too(self):
        """Отметка «код не дошёл» пишется после смены статуса — триггер обязан будить и по ней,
        и будить сотрудника, а не только главу."""
        self.assertIn("""'AFTER UPDATE OF status, delivery_error, codes_sent',""", self.database)
        self.assertIn('OR OLD.delivery_error IS DISTINCT FROM NEW.delivery_error', self.database)
        branch = self.database.split("ELSIF TG_TABLE_NAME = 'twofa_requests' THEN", 1)[1].split('ELSIF', 1)[0]
        self.assertIn('targets := ARRAY[NEW.requester_id] || ARRAY(', branch)
        # Будим включивших уведомления (тумблер), а не всех подтверждающих.
        self.assertIn('SELECT o.user_id FROM twofa_notify_optin o', branch)
        self.assertIn("('trg_bell_twofa_notify_optin', 'twofa_notify_optin', 'AFTER INSERT OR DELETE', '')",
                      self.database)

    def test_trainer_with_a_grant_is_not_bounced(self):
        allowed = self.app.split('const TRAINER_ALLOWED_VIEWS = Object.freeze([', 1)[1].split(']);', 1)[0]
        self.assertRegex(allowed, r"^\s*'twofa',$|\n\s*'twofa',\n")

    def test_section_flag_and_menu_item_place(self):
        self.assertIn('            const canAccessTwofaSection = canAccessTwofaSectionForUser(user);\n', self.app)
        # Пункт — в общем блоке рядом с «Продакшн», до первой ролевой ветки после него:
        # внутри {isAdminLikeRole && (…)} сотрудники его потеряли бы.
        production = self.app.index('<span className="sidebar-text">Продакшн</span>')
        item = self.app.index('<span className="sidebar-text">Коды 2FA</span>')
        next_branch = self.app.index('{isAdminLikeRole && (', production)
        self.assertLess(production, item)
        self.assertLess(item, next_branch)

    def test_buttons_run_in_their_own_pool(self):
        handler = self.bot.split('async def handle_twofa_callback', 1)[1].split('\n\n\n', 1)[0]
        self.assertIn('_TWOFA_CALLBACK_POOL, service.handle_callback', handler)
        self.assertNotIn('executor_pool', handler)
        # Выбор аккаунта правится на месте — сервису нужно, где сообщение с кнопкой.
        self.assertIn('callback_query.from_user.id, callback_query.data, where)', handler)

    def test_request_button_is_the_first_message_handler_and_works_from_any_state(self):
        """Посреди диалога бота подпись кнопки должна остаться кнопкой, а не стать ответом."""
        handler = self.bot.index("@dp.message_handler(lambda m: bool(TWOFA_REQUEST_BUTTON) and m.chat.type == 'private'\n"
                                 "                    and m.text == TWOFA_REQUEST_BUTTON, state='*')")
        self.assertEqual(self.bot.index('@dp.message_handler('), handler)
        body = self.bot.split('async def handle_twofa_request_button', 1)[1].split('\n\n\n', 1)[0]
        self.assertIn('_TWOFA_CALLBACK_POOL, service.offer_accounts, message.from_user.id', body)
        self.assertNotIn('executor_pool', body)
        self.assertIn('from twofa.rules import REQUEST_BUTTON as TWOFA_REQUEST_BUTTON', self.bot)
        self.assertIn('menu=lambda user_id: _twofa_reply_menu(user_id),', self.bot)

    def test_main_menu_is_built_in_one_place(self):
        """Кнопка «Запросить код 🔐» живёт в главном меню, поэтому каждое возвращение в него идёт
        через _main_menu_keyboard: иначе кнопка пропадала бы после «Отмены» или отчёта."""
        for name in ('get_admin_keyboard()', 'get_sv_keyboard()', 'get_operator_keyboard()'):
            # def и единственный вызов — внутри _main_menu_keyboard.
            self.assertEqual(self.bot.count(name), 2, name)
            self.assertIn('kb = ' + name, _function_source(self.bot, '_main_menu_keyboard'))
        for handler in ('start_command', 'start_auth', 'process_password', 'back_to_admin', 'cancel_handler'):
            self.assertIn('_main_menu_keyboard(', _function_source(self.bot, handler), handler)

    def test_bell_source(self):
        from notifications import sources

        self.assertIn('twofa', sources.SOURCES)
        self.assertEqual(sources.SOURCE_VIEWS['twofa'], ('twofa',))
        self.assertIn('twofa', sources.OWN_CIRCLE_VIEWS)
        self.assertIn("'source': 'twofa'", _read(SOURCES_PY))

    def test_screen_never_shows_the_code(self):
        """Выбор владельца: код — только в Telegram. На экране нет ни поля с кодом, ни ручки,
        которая бы его отдавала."""
        view = _read(VIEW_JSX)
        self.assertNotIn('.code}', view)
        routes = (ROOT / 'twofa' / 'routes.py').read_text(encoding='utf-8')
        self.assertNotIn("'code':", routes)
        self.assertNotIn('secret_enc', routes)

    def test_sections_are_wired_into_the_screen(self):
        """Разделы аккаунтов: окно раздела подключено, разделы доходят до всех окон, где
        выбирают аккаунты, журнал называет систему «Системой», а не «Разделом»."""
        view = _read(VIEW_JSX)
        history = _read(HISTORY_JSX)
        self.assertIn("import { GroupSheet } from './TwofaGroupSheet';", view)
        self.assertIn('<GroupSheet', view)
        self.assertEqual(view.count('groups={data.groups || []}'), 4)  # добавление, аккаунт, раздел, доступы
        self.assertIn("item.system ? 'Система'", history)
        self.assertNotIn("? 'Раздел'", view + history)
        # Колонка minmax(0, 1fr): длинная почта в названии не выталкивает карточку за край телефона.
        self.assertIn('className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3"', view)
        self.assertNotIn('className="grid gap-3 md:grid-cols-2', view)

    def test_screen_is_paged_and_remembered_per_person(self):
        """Вид раздела (владелец 27.10.2026, «не забудь использовать пагинацию»): история и
        журнал — страницами с сервера, аккаунты и люди — страницами на экране; повторный вход
        рисуется из памяти по человеку — userId доходит из App.jsx."""
        view = _read(VIEW_JSX)
        history = _read(HISTORY_JSX)
        self.assertIn("useServerPages(api, '/history'", history)
        self.assertIn("useServerPages(api, '/journal'", history)
        self.assertIn('<PagedGrid', view)
        self.assertIn('pageOf(matchedPeople, peoplePage, PEOPLE_PAGE_SIZE)', view)
        self.assertIn('SCREEN_MEMORY.get(memoryKey)', view)
        self.assertNotIn('localStorage', view + history)  # в ответе имена — только память страницы
        mount = self.app[self.app.index('<TwofaView'):]
        self.assertIn('userId={user?.id}', mount[:mount.index('/>')])
        # Вкладки — переезжающей плашкой, ждущие запросы — числом на вкладке.
        self.assertIn('options={tabOptions} ariaLabel="Вкладка раздела" slide', view)
        self.assertNotIn('.code}', history)


if __name__ == '__main__':
    unittest.main()


# ─── Права без базы — идут и в CI (тесты на Postgres там пропускаются) ──────────

def _ctx(user_id, role='marketing_manager', approver=10, granted=(), status='working', telegram=1):
    return {'user_id': user_id, 'name': 'Человек %d' % user_id, 'role': role, 'status': status,
            'telegram_id': telegram, 'approver_id': approver, 'granted_account_ids': list(granted),
            'key_status': 'ok'}


class MainMenuTest(unittest.TestCase):
    """Главное меню бота — настоящие функции bot_schedule2 (через ast) на настоящих кнопках aiogram.
    Подменён только ответ «выданы ли человеку коды»."""

    NAMES = ('_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role', '_is_admin_role',
             'get_admin_keyboard', 'get_sv_keyboard', 'get_operator_keyboard', '_main_menu_keyboard')
    OPERATOR_ROWS = [['Моя статистика📊'], ['Мои оценки📝'], ['Доступ🔑']]

    @classmethod
    def setUpClass(cls):
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

        from tests import source_cache

        body = [node for node in source_cache.parse(_read(BOT_PY)).body
                if (isinstance(node, ast.FunctionDef) and node.name in cls.NAMES)
                or (isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in cls.NAMES for t in node.targets))]
        cls.granted = set()
        namespace = {'ReplyKeyboardMarkup': ReplyKeyboardMarkup, 'KeyboardButton': KeyboardButton,
                     'TWOFA_REQUEST_BUTTON': rules.REQUEST_BUTTON,
                     '_twofa_can_request_for': lambda user_id: user_id in cls.granted}
        exec(compile(ast.Module(body=body, type_ignores=[]), str(BOT_PY), 'exec'), namespace)
        missing = [name for name in cls.NAMES if name not in namespace]
        if missing:
            raise AssertionError('не найдено в bot_schedule2.py: %s' % missing)
        cls.menu = staticmethod(namespace['_main_menu_keyboard'])

    def setUp(self):
        self.granted.clear()

    @staticmethod
    def _rows(kb):
        return [[button['text'] for button in row] for row in kb.to_python()['keyboard']]

    def test_without_codes_the_menu_is_the_role_menu(self):
        self.assertEqual(self._rows(self.menu(30, 'operator')), self.OPERATOR_ROWS)
        # Роль без своих кнопок: одна строка «Доступ🔑» — там «Выход🚪».
        self.assertEqual(self._rows(self.menu(20, 'marketing_manager')), [['Доступ🔑']])

    def test_codes_add_one_button_above_the_access_row(self):
        self.granted.update({1, 20, 30})
        self.assertEqual(self._rows(self.menu(30, 'operator')),
                         self.OPERATOR_ROWS[:-1] + [[rules.REQUEST_BUTTON], ['Доступ🔑']])
        self.assertEqual(self._rows(self.menu(20, 'marketing_manager')), [[rules.REQUEST_BUTTON], ['Доступ🔑']])
        admin = self._rows(self.menu(1, 'super_admin'))
        self.assertEqual(admin[0], ['Редактор СВ📝', 'Операторы👷'])
        self.assertEqual(admin[-2:], [[rules.REQUEST_BUTTON], ['Доступ🔑']])

    def test_the_button_does_not_stick_to_someone_else_s_menu(self):
        self.granted.add(30)
        self.menu(30, 'operator')
        self.assertEqual(self._rows(self.menu(31, 'operator')), self.OPERATOR_ROWS)


class BotHandlersTest(unittest.TestCase):
    """Обработчики кнопок в bot_schedule2 — их настоящий код (через ast) с подставным сервисом:
    фильтр нажатия, пул раздела и что уходит сервису. Импортировать монолит тесты не могут."""

    NAMES = ('handle_twofa_request_button', 'handle_twofa_callback')

    @classmethod
    def setUpClass(cls):
        import asyncio
        import copy
        import logging
        from concurrent.futures import ThreadPoolExecutor

        from tests import source_cache

        # Дерево общее на весь набор (tests/source_cache.py) — декораторы снимаем с копии.
        nodes = {node.name: copy.deepcopy(node) for node in source_cache.parse(_read(BOT_PY)).body
                 if isinstance(node, ast.AsyncFunctionDef) and node.name in cls.NAMES}
        cls.filters = {}
        for name, node in nodes.items():
            [decorator] = node.decorator_list
            cls.filters[name] = (decorator.args[0], {k.arg: ast.literal_eval(k.value) for k in decorator.keywords})
            node.decorator_list = []
        cls.pool = ThreadPoolExecutor(max_workers=1)
        cls.namespace = {'asyncio': asyncio, 'logging': logging, 'types': mock.MagicMock(),
                         '_TWOFA_CALLBACK_POOL': cls.pool, '_TWOFA': {}}
        exec(compile(ast.Module(body=list(nodes.values()), type_ignores=[]), str(BOT_PY), 'exec'), cls.namespace)

    @classmethod
    def tearDownClass(cls):
        cls.pool.shutdown()

    def _filter(self, name, button=rules.REQUEST_BUTTON):
        expression, keywords = self.filters[name]
        self.assertEqual(keywords.get('state'), '*')
        return eval(compile(ast.Expression(expression), str(BOT_PY), 'eval'), {'TWOFA_REQUEST_BUTTON': button})

    @staticmethod
    def _message(text=rules.REQUEST_BUTTON, chat='private'):
        from types import SimpleNamespace

        message = SimpleNamespace(text=text, chat=SimpleNamespace(type=chat, id=9020),
                                  from_user=SimpleNamespace(id=9020), message_id=55)
        message.delete = mock.AsyncMock()
        message.answer = mock.AsyncMock()
        return message

    def _run(self, handler, *args):
        import asyncio

        asyncio.run(self.namespace[handler](*args))

    def test_button_filter_takes_only_the_button_in_a_private_chat(self):
        matches = self._filter('handle_twofa_request_button')
        self.assertTrue(matches(self._message()))
        self.assertFalse(matches(self._message(chat='group')))
        self.assertFalse(matches(self._message(text='Запросить код')))
        self.assertFalse(matches(self._message(text=None)))
        # Подпись не загрузилась (раздел не подключён) — фильтр молчит, а не падает на каждом сообщении.
        self.assertFalse(self._filter('handle_twofa_request_button', button=None)(self._message(text=None)))

    def test_button_asks_the_service_for_the_choice_and_tidies_the_chat(self):
        service = mock.MagicMock()
        message = self._message()
        with mock.patch.dict(self.namespace['_TWOFA'], {'service': service}):
            self._run('handle_twofa_request_button', message)
        service.offer_accounts.assert_called_once_with(9020)
        message.delete.assert_awaited_once()  # подпись кнопки из чата убираем, как соседние кнопки
        message.answer.assert_not_awaited()

    def test_button_answers_when_the_section_is_off_or_fails(self):
        message = self._message()
        with mock.patch.dict(self.namespace['_TWOFA'], {'service': None}):
            self._run('handle_twofa_request_button', message)
        message.answer.assert_awaited_once_with('Раздел «Коды 2FA» не подключён')
        broken = mock.MagicMock()
        broken.offer_accounts.side_effect = RuntimeError('база')
        message = self._message()
        with mock.patch.dict(self.namespace['_TWOFA'], {'service': broken}), self.assertLogs(level='ERROR'):
            self._run('handle_twofa_request_button', message)
        message.answer.assert_awaited_once_with('Не получилось — попробуйте ещё раз')

    def test_callback_hands_over_where_the_pressed_message_is(self):
        from types import SimpleNamespace

        service = mock.MagicMock()
        service.handle_callback.return_value = ('Запрос отправлен', False)
        callback = SimpleNamespace(data='twofa:req:5', from_user=SimpleNamespace(id=9020),
                                   message=self._message(), answer=mock.AsyncMock())
        with mock.patch.dict(self.namespace['_TWOFA'], {'service': service}):
            self._run('handle_twofa_callback', callback)
        service.handle_callback.assert_called_once_with(9020, 'twofa:req:5', (9020, 55))
        callback.answer.assert_awaited_once_with('Запрос отправлен', show_alert=False)


class _NoDb:
    """Ручки, которые не должны дойти до базы: любой курсор — провал теста."""

    def _get_cursor(self):
        raise AssertionError('ручка полезла в базу, хотя должна была отказать раньше')


class RouteGatesWithoutDbTest(unittest.TestCase):
    """Гейт каждой ручки: кому отказ ещё ДО работы. Без Postgres — в CI тоже."""

    def setUp(self):
        from flask import Flask

        from twofa.routes import build_twofa_blueprint

        self.users = {
            1: _ctx(1, role='super_admin'), 10: _ctx(10, role='admin'),
            20: _ctx(20, granted=[5]), 30: _ctx(30, role='operator'),
        }
        self.current = {'id': 20}
        service = mock.MagicMock()
        service.context.side_effect = lambda uid: self.users.get(uid)
        service.tg = mock.MagicMock()
        self.service = service
        app = Flask(__name__)
        app.register_blueprint(build_twofa_blueprint(
            db=_NoDb(), require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.current['id'], None, None), service=service))
        self.client = app.test_client()
        patcher = mock.patch.dict(os.environ, {crypto.ENV_KEY: base64.b64encode(b'z' * 32).decode()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, user_id, method, path, body=None):
        self.current['id'] = user_id
        response = getattr(self.client, method)('/api/twofa' + path, json=body)
        return response.status_code, (response.get_json() or {}).get('code')

    def test_only_approvers_decide(self):
        for user_id in (20, 30):
            for action in ('approve', 'reject'):
                status, code = self.call(user_id, 'post', '/requests/5/%s' % action)
                self.assertEqual(status, 403, (user_id, action))
        self.service.decide.assert_not_called()

    def test_employee_manages_nothing(self):
        for method, path in (('get', '/journal'), ('post', '/accounts/preview'), ('post', '/accounts'),
                             ('patch', '/accounts/5'), ('delete', '/accounts/5'), ('get', '/people'),
                             ('put', '/people/20/grants'), ('post', '/groups'), ('patch', '/groups/3'),
                             ('delete', '/groups/3')):
            self.assertEqual(self.call(20, method, path, {})[1], 'TWOFA_MANAGE_FORBIDDEN', path)
        for name in ('create_group', 'update_group', 'delete_group', 'update_account', 'add_accounts'):
            getattr(self.service, name).assert_not_called()
        # Чужая история запросов — только тем, кто ведёт раздел; свою (mine=1) видит каждый.
        self.assertEqual(self.call(20, 'get', '/history'), (403, 'TWOFA_MANAGE_FORBIDDEN'))

    def test_other_department_does_not_open_the_section(self):
        for method, path in (('get', ''), ('post', '/requests')):
            self.assertEqual(self.call(30, method, path, {'account_id': 5}),
                             (403, 'TWOFA_SECTION_CLOSED'))
        self.service.request_code.assert_not_called()


class ServiceRechecksAccessWithoutDbTest(unittest.TestCase):
    """decide и resend перепроверяют доступ сотрудника под блокировкой — без базы, в CI."""

    def setUp(self):
        from contextlib import contextmanager

        from twofa import queries, service

        self.queries = queries
        self.tg = mock.MagicMock()
        self.tg.send.return_value = (777, None)

        @contextmanager
        def cursor():
            yield mock.MagicMock(rowcount=1)

        self.svc = service.Twofa(cursor_factory=cursor, tg=self.tg, clock=lambda: 1761553805.0)
        self.request = {
            'id': 9, 'account_id': 5, 'account_title': 'Google Ads', 'service': None, 'requester_id': 20,
            'requester_name': 'Анна', 'requester_telegram_id': 9020, 'status': 'pending',
            'created_at': rules.almaty(1761553800), 'expires_at': rules.almaty(1761554400),
            'decided_at': None, 'decided_by': None, 'decided_by_name': None, 'decided_via': None,
            'resend_until': None, 'codes_sent': 0, 'delivery_error': None, 'last_counter': None,
            'last_issued_at': None,
        }
        patcher = mock.patch.dict(os.environ, {crypto.ENV_KEY: base64.b64encode(b'z' * 32).decode()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch(self, requester_ctx, account_active=True, request=None):
        req = dict(self.request, **(request or {}))
        account = {'id': 5, 'title': 'Google Ads', 'service': None, 'is_active': account_active,
                   'created_at': None, 'secret_enc': crypto.encrypt(RFC_SECRET, 5)}
        patches = [
            mock.patch.object(self.queries, 'get_request', return_value=req),
            mock.patch.object(self.queries, 'load_access_context', return_value=requester_ctx),
            mock.patch.object(self.queries, 'get_account', return_value=account),
            mock.patch.object(self.queries, 'decide', return_value=True),
            mock.patch.object(self.queries, 'log_event'),
            mock.patch.object(self.queries, 'mark_issued'),
            mock.patch.object(self.queries, 'mark_delivery'),
            mock.patch.object(self.queries, 'insert_message'),
            mock.patch.object(self.queries, 'claim_messages', return_value=[]),
            mock.patch.object(self.queries, 'claim_expired', return_value=[]),
            mock.patch.object(self.queries, 'code_message_ids', return_value=[]),
            mock.patch.object(self.queries, 'claim_message_ids', return_value=[]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _codes_sent(self):
        return [c for c in self.tg.send.call_args_list if '<code>' in str(c)]

    def test_code_is_not_sent_when_access_was_revoked_while_waiting(self):
        self._patch(_ctx(20, granted=[]))
        result = self.svc.decide(_ctx(10, role='admin'), 9, 'approve', 'portal')
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self._codes_sent(), [])

    def test_code_is_not_sent_to_a_fired_employee(self):
        self._patch(_ctx(20, granted=[5], status='fired'))
        self.assertEqual(self.svc.decide(_ctx(10, role='admin'), 9, 'approve', 'portal')['outcome'], 'cancelled')
        self.assertEqual(self._codes_sent(), [])

    def test_code_is_sent_when_everything_holds(self):
        self._patch(_ctx(20, granted=[5]), request={'resend_until': rules.almaty(1761553985)})
        with mock.patch.object(self.queries, 'get_request', side_effect=[
                dict(self.request), dict(self.request, status='approved', resend_until=rules.almaty(1761553985)),
                dict(self.request, status='approved'), dict(self.request, status='approved')]):
            self.svc.decide(_ctx(10, role='admin'), 9, 'approve', 'portal')
        self.assertEqual(len(self._codes_sent()), 1)

    def test_employee_cannot_decide(self):
        self._patch(_ctx(20, granted=[5]))
        self.assertEqual(self.svc.decide(_ctx(20, granted=[5]), 9, 'approve', 'portal')['outcome'],
                         'forbidden')
        self.tg.send.assert_not_called()

    def test_super_admin_can_decide(self):
        self._patch(_ctx(20, granted=[5]), request={'resend_until': rules.almaty(1761553985)})
        with mock.patch.object(self.queries, 'get_request', side_effect=[
                dict(self.request), dict(self.request, status='approved', resend_until=rules.almaty(1761553985)),
                dict(self.request, status='approved'), dict(self.request, status='approved')]):
            result = self.svc.decide(_ctx(1, role='super_admin'), 9, 'approve', 'portal')
        self.assertEqual(result['outcome'], 'approved')
        self.assertEqual(len(self._codes_sent()), 1)

    def test_resend_refused_after_access_was_revoked(self):
        self._patch(_ctx(20, granted=[]), request={'status': 'approved',
                                                   'resend_until': rules.almaty(1761553985)})
        self.assertEqual(self.svc.resend(20, 9)['outcome'], 'forbidden')
        self.tg.send.assert_not_called()

    def test_cancel_of_the_choice_only_deletes_it(self):
        from twofa import service

        untouched = mock.MagicMock(side_effect=AssertionError('«Отмена» не ходит в базу'))
        svc = service.Twofa(cursor_factory=untouched, tg=self.tg)
        self.assertEqual(svc.handle_callback(9020, 'twofa:x:0', (9020, 55)), ('', False))
        self.tg.delete.assert_called_once_with(9020, 55)
        self.tg.send.assert_not_called()

