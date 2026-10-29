# -*- coding: utf-8 -*-
"""Ключи партнёров (partner_keys): ключ, области доступа, проверка, журнал, админка.

Что здесь сторожится и почему:

* **Область — часть ключа.** Ручка сверяет ключ со своей областью: верный ключ
  без неё — NO_SCOPE (ручка отвечает 403), неизвестный и отозванный — отказ,
  схема не развёрнута — NOT_READY (503, а не «пускаем всех»).
* **Ключ в базе только хешем**, в ответе на выпуск — один раз; области при
  выпуске обязательны, чужие коды не принимаются, снять все — нельзя (для этого
  отзыв).
* **Журнал — строка на вызов** с областью, ручкой, исходом и сводкой; отказ
  записи не роняет ручку.
* **Перенос с первой версии** (таблицы knowledge_api_*): переименование один раз,
  старым ключам — область «Ручка знаний».

Сети и базы нет: курсор — двойник. Проводка (bot_schedule2, database.py, README,
App.jsx, ручки) проверяется чтением исходника.
"""

import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from partner_keys import auth, journal, keys, queries, schema, scopes  # noqa: E402
from partner_keys.admin_routes import build_partner_keys_admin_blueprint  # noqa: E402

BOT = ROOT / 'bot_schedule2.py'
README = ROOT / 'README.md'

# Ключ выдуман; настоящий живёт только в журнале выпуска.
KEY = 'otpk_FAKE-key-for-tests-0123456789-abcdefghijk'
KEY_ROW = (7, 'Syntony', keys.mark(KEY), ['knowledge'], 'operator', 1,
           'СЗоВ — Служба заботы о водителях', 'szov', 3, 'Админ', None, None, 5, None, None, None)
REVOKED_ROW = KEY_ROW[:13] + ('2026-10-29T10:00:00', 3, 'Админ')


class _Cursor:
    """Помнит SQL и параметры; отвечает на то, что пакет спрашивает у базы."""

    def __init__(self, *, ready=True, key_row=KEY_ROW, fail_insert=False):
        self.calls = []
        self.ready = ready
        self.key_row = key_row
        self.fail_insert = fail_insert
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._one, self._all = None, []
        if 'to_regclass' in text:
            self._one = (self.ready,)
        elif 'FROM partner_keys k' in text and 'WHERE k.key_hash' in text:
            self._one = self.key_row if params and params[0] == keys.digest(KEY) else None
        elif 'FROM partner_keys k' in text and 'WHERE k.id' in text:
            self._one = self.key_row
        elif 'FROM partner_keys k' in text:
            self._all = [self.key_row] if self.key_row else []
        elif text.startswith('INSERT INTO partner_key_calls'):
            if self.fail_insert:
                raise RuntimeError('база недоступна')
            self._one = (101,)
        elif text.startswith('INSERT INTO partner_keys'):
            self._one = (8,)
        elif text.startswith('UPDATE partner_keys SET scopes'):
            self._one = (7,) if self.key_row and self.key_row[13] is None else None
        elif text.startswith('UPDATE partner_keys') and 'revoked_at' in text and 'RETURNING' in text:
            self._one = (7,)
        elif text.startswith('SELECT id, name FROM departments'):
            self._one = (1, 'СЗоВ')
        elif 'count(*) FROM partner_key_calls' in text:
            self._one = (0,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)

    @property
    def rowcount(self):
        return 1

    def journal(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO partner_key_calls')]


def fake_db(cursor):
    db = MagicMock()

    @contextmanager
    def _get_cursor():
        yield cursor

    db._get_cursor = _get_cursor
    return db


# ── Ключ ────────────────────────────────────────────────────────────────────────

class KeyTests(unittest.TestCase):
    def test_generated_key_has_prefix_and_is_long_random_ascii(self):
        first, second = keys.generate(), keys.generate()
        self.assertTrue(first.startswith(keys.PREFIX))
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), keys.MIN_LENGTH)
        self.assertTrue(first.isascii())
        self.assertIsNotNone(keys.presented(first))

    def test_mark_is_the_start_of_the_hash_and_never_the_key(self):
        self.assertEqual(keys.mark(KEY), keys.digest(KEY)[:keys.MARK_LENGTH])
        self.assertNotIn(keys.mark(KEY), KEY)

    def test_values_that_are_not_keys_do_not_reach_the_database(self):
        for value in ('', None, 'short', 'ключ-кириллицей-' * 4, 'with space ' * 5,
                      'x' * (keys.MAX_LENGTH + 1)):
            self.assertIsNone(keys.presented(value), repr(value))


# ── Области доступа ─────────────────────────────────────────────────────────────

class ScopeTests(unittest.TestCase):
    def test_every_door_is_in_the_catalog(self):
        codes = [scope['code'] for scope in scopes.catalog()]
        self.assertEqual(codes, [scopes.KNOWLEDGE, scopes.SIGN_LINKS, scopes.CRM_EMBED, scopes.OLX_CHATS])
        for scope in scopes.catalog():
            for field in ('code', 'title', 'hint', 'endpoints', 'header'):
                self.assertTrue(scope[field], field)

    def test_normalize_orders_dedupes_and_refuses_strangers(self):
        self.assertEqual(scopes.normalize(['crm_embed', 'Knowledge', 'crm_embed', '']),
                         ['knowledge', 'crm_embed'])
        self.assertEqual(scopes.normalize(None), [])
        self.assertEqual(scopes.normalize('sign_links'), ['sign_links'])
        with self.assertRaises(ValueError):
            scopes.normalize(['everything'])
        self.assertEqual(scopes.titles(['sign_links', 'nope']), ['Ссылки на подписание'])


# ── Проверка ключа ──────────────────────────────────────────────────────────────

class AuthTests(unittest.TestCase):
    def test_header_choice(self):
        self.assertEqual(auth.presented_from({'X-Api-Key': ' k '}), 'k')
        self.assertIsNone(auth.presented_from({'Authorization': 'Bearer k'}))
        self.assertEqual(auth.presented_from({'Authorization': 'Bearer k'}, bearer=True), 'k')
        self.assertEqual(auth.presented_from({'X-Api-Key': 'a', 'Authorization': 'Bearer b'}, bearer=True), 'a')
        self.assertIsNone(auth.presented_from({'Authorization': 'Basic k'}, bearer=True))

    def test_outcomes(self):
        ready = _Cursor()
        self.assertEqual(auth.lookup(fake_db(_Cursor(ready=False)), KEY, scope='knowledge'),
                         (auth.NOT_READY, None))
        self.assertEqual(auth.lookup(fake_db(ready), None, scope='knowledge'), (auth.UNKNOWN, None))
        self.assertEqual(auth.lookup(fake_db(ready), KEY + 'x', scope='knowledge'), (auth.UNKNOWN, None))
        self.assertEqual(auth.lookup(fake_db(_Cursor(key_row=REVOKED_ROW)), KEY, scope='knowledge'),
                         (auth.REVOKED, None))
        status, key = auth.lookup(fake_db(ready), KEY, scope='sign_links')
        self.assertEqual((status, key['name']), (auth.NO_SCOPE, 'Syntony'))
        status, key = auth.lookup(fake_db(ready), KEY, scope='knowledge')
        self.assertEqual((status, key['id'], key['scopes']), (auth.OK, 7, ['knowledge']))

    def test_nothing_that_is_not_a_key_reaches_the_lookup(self):
        cursor = _Cursor()
        auth.lookup(fake_db(cursor), 'short', scope='knowledge')
        self.assertFalse(any('FROM partner_keys' in text for text, _ in cursor.calls))

    def test_a_broken_database_is_an_error_not_a_pass(self):
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            raise RuntimeError('нет соединения')
            yield  # noqa: unreachable — форма contextmanager

        db._get_cursor = _get_cursor
        with self.assertLogs(level='ERROR'):
            self.assertEqual(auth.lookup(db, KEY, scope='knowledge'), (auth.ERROR, None))


# ── Журнал ──────────────────────────────────────────────────────────────────────

class JournalTests(unittest.TestCase):
    def test_record_writes_scope_endpoint_summary_and_details_and_touches_the_key(self):
        cursor = _Cursor()
        key = {'id': 7, 'mark': 'abcd1234'}
        call_id = journal.record(fake_db(cursor), key, scope='sign_links',
                                 endpoint='/api/sign_links/external/links', outcome='ok',
                                 http_status=200, latency_ms=812, summary='ИИН: 2 — ссылок 1',
                                 details={'iins': 2, 'links': 1}, ip='203.0.113.7', user_agent='crm/1')
        self.assertEqual(call_id, 101)
        entry = cursor.journal()[0]
        self.assertEqual((entry['key'], entry['scope'], entry['endpoint'], entry['outcome'],
                          entry['status'], entry['latency'], entry['summary'], entry['ip']),
                         (7, 'sign_links', '/api/sign_links/external/links', 'ok', 200, 812,
                          'ИИН: 2 — ссылок 1', '203.0.113.7'))
        self.assertEqual(json.loads(entry['details']), {'iins': 2, 'links': 1})
        self.assertTrue(entry['request'])
        self.assertTrue(any(text.startswith('UPDATE partner_keys SET last_used_at')
                            for text, _ in cursor.calls))

    def test_record_swallows_failures_and_skips_keys_without_an_id(self):
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(journal.record(fake_db(_Cursor(fail_insert=True)), {'id': 7},
                                             scope='knowledge', endpoint='/x', outcome='ok'))
        cursor = _Cursor()
        self.assertIsNone(journal.record(fake_db(cursor), None, scope='knowledge', endpoint='/x', outcome='ok'))
        self.assertEqual(cursor.calls, [])


# ── Схема ───────────────────────────────────────────────────────────────────────

class SchemaTests(unittest.TestCase):
    def test_first_version_tables_are_renamed_once_and_old_keys_get_the_knowledge_scope(self):
        migration = schema._STATEMENTS[0]
        for token in ("to_regclass('public.knowledge_api_keys') IS NOT NULL",
                      "to_regclass('public.partner_keys') IS NULL",
                      'ALTER TABLE knowledge_api_keys RENAME TO partner_keys',
                      "UPDATE partner_keys SET scopes = ARRAY['knowledge'] WHERE cardinality(scopes) = 0",
                      'ALTER TABLE knowledge_api_calls RENAME TO partner_key_calls',
                      "scope = 'knowledge'", "endpoint = '/api/knowledge/search'",
                      'DROP COLUMN IF EXISTS question'):
            self.assertIn(token, migration, token)

    def test_readiness_needs_a_real_true_not_any_row(self):
        for row, expected in (((True,), True), ((False,), False), (None, False), ((10, 'имя'), False)):
            cursor = MagicMock()
            cursor.fetchone.return_value = row
            self.assertEqual(schema.schema_is_ready(cursor), expected, row)


# ── Ключи для супер-админа ──────────────────────────────────────────────────────

@unittest.skipIf(Flask is None, 'flask не установлен')
class AdminTests(unittest.TestCase):
    def build(self, role='super_admin', key_row=KEY_ROW):
        self.cursor = _Cursor(key_row=key_row)

        def passthrough(handler):
            return handler

        app = Flask(__name__)
        app.config['TESTING'] = True
        app.register_blueprint(build_partner_keys_admin_blueprint(
            db=fake_db(self.cursor), require_api_key=passthrough,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (3, (3, 'admin', 'Админ', role), None),
            normalize_role=lambda value: str(value or '').strip().lower()))
        return app.test_client()

    def test_only_the_super_admin_issues_keys(self):
        client = self.build(role='admin')
        self.assertEqual(client.get('/api/admin/partner-keys').status_code, 403)
        self.assertEqual(client.post('/api/admin/partner-keys', json={'name': 'X'}).status_code, 403)
        self.assertEqual(self.cursor.calls, [])

    def test_the_list_names_the_doors_and_the_catalog(self):
        client = self.build()
        response = client.get('/api/admin/partner-keys')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        item = payload['keys'][0]
        self.assertEqual((item['name'], item['scopes'], item['scope_titles'], item['active']),
                         ('Syntony', ['knowledge'], ['Ручка знаний'], True))
        self.assertEqual(item['acts_as']['department_code'], 'szov')
        self.assertNotIn('key_hash', item)
        self.assertEqual([scope['code'] for scope in payload['scopes']],
                         ['knowledge', 'sign_links', 'crm_embed', 'olx_chats'])
        self.assertEqual(item['webhooks'], [])
        self.assertEqual(payload['header'], keys.HEADER)

    def test_issue_needs_a_name_and_doors_and_stores_only_the_hash(self):
        client = self.build()
        self.assertEqual(client.post('/api/admin/partner-keys', json={'name': '  ', 'scopes': ['knowledge']}).status_code, 400)
        self.assertEqual(client.post('/api/admin/partner-keys', json={'name': 'X', 'scopes': []}).status_code, 400)
        self.assertEqual(client.post('/api/admin/partner-keys', json={'name': 'X', 'scopes': ['all']}).status_code, 400)
        self.assertEqual(client.post('/api/admin/partner-keys', json={'name': 'X', 'scopes': ['knowledge'],
                                                                      'otp_role': 'admin'}).status_code, 400)
        response = client.post('/api/admin/partner-keys',
                               json={'name': ' CRM «Потока» ', 'scopes': ['crm_embed', 'sign_links']})
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        secret = payload['secret']
        self.assertTrue(secret.startswith(keys.PREFIX))
        self.assertEqual(payload['header'], keys.HEADER)
        inserted = [params for text, params in self.cursor.calls
                    if text.startswith('INSERT INTO partner_keys')][0]
        self.assertEqual(inserted['hash'], keys.digest(secret))
        self.assertEqual(inserted['mark'], keys.mark(secret))
        self.assertEqual((inserted['name'], inserted['scopes'], inserted['role'], inserted['department'],
                          inserted['by']),
                         ('CRM «Потока»', ['sign_links', 'crm_embed'], 'operator', 1, 3))
        self.assertNotIn(secret, json.dumps(inserted))

    def test_doors_can_be_changed_but_not_all_taken_away(self):
        client = self.build()
        response = client.patch('/api/admin/partner-keys/7', json={'scopes': ['knowledge', 'crm_embed']})
        self.assertEqual(response.status_code, 200)
        updated = [params for text, params in self.cursor.calls
                   if text.startswith('UPDATE partner_keys SET scopes')][0]
        self.assertEqual(updated['scopes'], ['knowledge', 'crm_embed'])
        self.assertEqual(client.patch('/api/admin/partner-keys/7', json={'scopes': []}).status_code, 400)
        revoked = self.build(key_row=REVOKED_ROW)
        self.assertEqual(revoked.patch('/api/admin/partner-keys/7', json={'scopes': ['knowledge']}).status_code, 409)

    def test_revoke_and_calls(self):
        client = self.build()
        self.assertEqual(client.post('/api/admin/partner-keys/7/revoke').status_code, 200)
        response = client.get('/api/admin/partner-keys/7/calls?limit=10')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['pagination'], {'limit': 10, 'offset': 0, 'total': 0,
                                                             'has_more': False})


# ── Проводка ────────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT.read_text(encoding='utf-8-sig')

    def test_the_admin_entry_needs_the_session(self):
        start = self.bot.index('build_partner_keys_admin_blueprint(\n')
        call = self.bot[start:self.bot.index('))', start)]
        for token in ('require_api_key=require_api_key', 'resolve_requester=_resolve_requester',
                      'normalize_role=_normalize_user_role'):
            self.assertIn(token, call)
        self.assertNotIn('knowledge_admin', self.bot)

    def test_browsers_cannot_send_the_key_cross_origin(self):
        for line in self.bot.splitlines():
            if 'allow_headers' in line or 'Access-Control-Allow-Headers' in line:
                self.assertNotIn(keys.HEADER.lower(), line.lower())

    def test_the_journal_is_cleaned_daily(self):
        self.assertIn("id='partner_keys_calls_retention_daily'", self.bot)
        self.assertIn('partner_queries.cleanup_calls(cursor, days)', self.bot)

    def test_the_schema_is_deployed_under_its_own_savepoint(self):
        database = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
        self.assertIn('self._init_partner_keys_schema_tx(cursor)', database)
        self.assertIn('SAVEPOINT partner_keys_schema', database)
        self.assertNotIn('knowledge_api_schema', database)

    def test_every_door_checks_the_key_with_its_own_scope(self):
        for path, scope in (('knowledge_api/routes.py', 'partner_scopes.KNOWLEDGE'),
                            ('sign_links/external.py', 'partner_scopes.SIGN_LINKS'),
                            ('wazzup/embed_auth.py', 'partner_scopes.CRM_EMBED')):
            source = (ROOT / path).read_text(encoding='utf-8')
            self.assertIn('partner_auth.lookup(', source, path)
            self.assertIn(scope, source, path)
            self.assertIn('partner_auth.NO_SCOPE', source, path)

    def test_the_readme_and_the_app_name_the_tab(self):
        readme = README.read_text(encoding='utf-8')
        for token in ('/api/admin/partner-keys', 'PARTNER_KEYS_CALLS_DAYS', keys.HEADER,
                      'Ссылки на подписание', 'Окно «Чатов ОП» в CRM', 'Ручка знаний'):
            self.assertIn(token, readme, token)
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn("import('./components/partner_keys/PartnerKeysPanel')", app)
        self.assertNotIn('KnowledgeKeysPanel', app)
        panel = (ROOT / 'src' / 'components' / 'partner_keys' / 'PartnerKeysPanel.jsx').read_text(encoding='utf-8')
        self.assertIn('/api/admin/partner-keys', panel)
        self.assertIn('ScopePicker', panel)


if __name__ == '__main__':
    unittest.main()
