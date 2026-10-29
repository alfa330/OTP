# -*- coding: utf-8 -*-
"""Чаты OLX для партнёров (olx_amo/partner_routes.py, partner_events.py).

Что здесь сторожится:

* **Дверь** — ключ партнёра с областью «Чаты OLX»: без неё 403, без ключа 401,
  без схемы 503, сверх предела в минуту 429.
* **Ручки чтения** отдают кабинеты, чаты, переписку и журнал в форме документа
  партнёру; время — с поясом Алматы; неизвестный кабинет — 404.
* **Ответ кандидату** уходит через тот же `reply_from_portal`, что у раздела, от
  имени «Партнёр: <ключ>»; отказы OLX переводятся в коды документа.
* **Подписка**: только https, известные события, секрет выпускается один раз;
  события собираются из того, что видит опрос, и не роняют робота.

Сети и базы нет: сервисы раздела подменены объектом, курсор — двойник.
"""

import json
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from olx_amo import cabinets, partner_events, partner_routes  # noqa: E402
from olx_amo.service import ReplyRefused  # noqa: E402
from partner_keys import keys, webhooks  # noqa: E402

BOT = ROOT / 'bot_schedule2.py'
KEY = 'otpk_FAKE-olx-key-for-tests-0123456789-abcdefgh'
KEY_ROW = (7, 'CRM «Потока»', keys.mark(KEY), ['olx_chats'], 'operator', 1, 'СЗоВ', 'szov',
           3, 'Админ', None, None, 0, None, None, None)
OTHER_DOOR_ROW = KEY_ROW[:3] + (['knowledge'],) + KEY_ROW[4:]
CABINET = cabinets.get('global')


class _Cursor:
    def __init__(self, *, ready=True, key_row=KEY_ROW):
        self.calls = []
        self.ready = ready
        self.key_row = key_row
        self._one = None

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._one = None
        if 'to_regclass' in text:
            self._one = (self.ready,)
        elif 'FROM partner_keys k' in text and 'WHERE k.key_hash' in text:
            self._one = self.key_row if params and params[0] == keys.digest(KEY) else None
        elif text.startswith('INSERT INTO partner_key_calls'):
            self._one = (1,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def journal(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO partner_key_calls')]


class FakeServices:
    def __init__(self):
        self.calls = []
        self.reply_error = None
        self.subscription_item = None
        self.thread_rows = [{
            'cabinet_code': 'global', 'thread_id': '4195536101', 'advert_id': '12', 'advert_title': 'Водитель',
            'interlocutor_id': '88', 'interlocutor_name': 'Айдар', 'last_message_id': '9911',
            'last_message_at': datetime(2026, 10, 29, 13, 48, 10), 'messages_seen': 3,
            'awaiting_human_since': None, 'canned_reply_sent_at': None, 'phone_normalized': '77011234567',
            'amo_lead_id': 5, 'first_seen_at': datetime(2026, 10, 28, 9, 10), 'updated_at': datetime(2026, 10, 29, 13, 48, 14),
        }]

    def cabinets(self):
        self.calls.append(('cabinets',))
        return [{'code': 'global', 'title': 'Global', 'state': 'ok'}]

    def threads(self, **filters):
        self.calls.append(('threads', filters))
        return list(self.thread_rows), 1

    def conversation(self, cabinet, thread_id):
        self.calls.append(('conversation', cabinet.code, thread_id))
        return {'items': [
            {'id': '9910', 'outgoing': False, 'text': 'Здравствуйте', 'at': datetime(2026, 10, 29, 13, 48, 10),
             'author': None, 'attachments': [], 'cvs': []},
            {'id': 'event-77', 'event': 'lead_created', 'text': 'Создана сделка', 'at': datetime(2026, 10, 29, 13, 48, 14),
             'amo_lead_id': 5, 'phone': '77011234567', 'error': None, 'latency_ms': 4100},
            {'id': '9911', 'outgoing': True, 'text': 'Добрый день', 'at': datetime(2026, 10, 29, 13, 48, 40),
             'author': 'Робот', 'attachments': [], 'cvs': []},
        ], 'state': {'advert_title': 'Водитель', 'interlocutor_name': 'Айдар', 'phone_normalized': '77011234567'}}

    def reply(self, cabinet, thread_id, text, actor):
        self.calls.append(('reply', cabinet.code, thread_id, text, actor))
        if self.reply_error:
            raise self.reply_error
        return {'id': 5120}

    def refused_type(self):
        return ReplyRefused

    def journal(self, **filters):
        self.calls.append(('journal', filters))
        return {'total': 1, 'items': [{'id': 1, 'cabinet_code': 'global', 'thread_id': '41', 'result': 'lead_created',
                                       'phone_normalized': '77011234567', 'created_at': datetime(2026, 10, 29, 13, 48, 14)}]}

    def subscription(self, key_id):
        self.calls.append(('subscription', key_id))
        return self.subscription_item, ({'ready': 0, 'done': 2, 'failed': 0} if self.subscription_item else None)

    def save_subscription(self, key_id, *, url, secret, events):
        self.calls.append(('save', key_id, url, secret, events))
        self.subscription_item = {'id': 1, 'url': url, 'secret': secret, 'events': events, 'enabled': True,
                                  'created_at': 'x', 'updated_at': 'x', 'last_delivered_at': None,
                                  'last_error': None, 'last_error_at': None, 'failures_in_row': 0}
        return self.subscription_item

    def delete_subscription(self, key_id):
        self.calls.append(('delete', key_id))
        had = self.subscription_item is not None
        self.subscription_item = None
        return had

    def deliveries(self, key_id, limit):
        return [] if self.subscription_item else None

    def ping(self, key_id):
        self.calls.append(('ping', key_id))
        return 1


@unittest.skipIf(Flask is None, 'flask не установлен')
class Harness(unittest.TestCase):
    def build(self, *, ready=True, key_row=KEY_ROW, per_minute=None):
        self.cursor = _Cursor(ready=ready, key_row=key_row)
        self.services = FakeServices()
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield self.cursor

        db._get_cursor = _get_cursor
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.register_blueprint(partner_routes.build_olx_partner_blueprint(
            db=db, client_ip=lambda: '203.0.113.7', services=self.services, environ={}, per_minute=per_minute))
        return app.test_client()

    def headers(self, key=KEY):
        return {keys.HEADER: key} if key is not None else {}


class DoorTests(Harness):
    def test_refusals(self):
        self.assertEqual(self.build(ready=False).get('/api/olx/cabinets', headers=self.headers()).status_code, 503)
        client = self.build()
        self.assertEqual(client.get('/api/olx/cabinets').status_code, 401)
        self.assertEqual(client.get('/api/olx/cabinets', headers=self.headers(KEY + 'x')).status_code, 401)
        self.assertEqual(self.build(key_row=OTHER_DOOR_ROW).get('/api/olx/cabinets', headers=self.headers()).status_code, 403)
        self.assertEqual(self.services.calls, [])

    def test_minute_limit(self):
        client = self.build(per_minute=1)
        self.assertEqual(client.get('/api/olx/ping', headers=self.headers()).status_code, 200)
        response = client.get('/api/olx/ping', headers=self.headers())
        self.assertEqual(response.status_code, 429)
        self.assertTrue(int(response.headers['Retry-After']) >= 1)
        self.assertEqual(self.cursor.journal()[0]['outcome'], 'limit')


class ReadTests(Harness):
    def test_ping_and_cabinets(self):
        client = self.build()
        payload = client.get('/api/olx/ping', headers=self.headers()).get_json()
        self.assertEqual(payload['key'], 'CRM «Потока»')
        self.assertEqual(payload['events'], ['message.received', 'message.sent'])
        self.assertIn({'code': 'global', 'title': CABINET.title}, payload['cabinets'])
        response = client.get('/api/olx/cabinets', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['cabinets'][0]['code'], 'global')
        self.assertEqual(self.cursor.journal()[-1]['scope'], 'olx_chats')

    def test_threads_filters_and_shape(self):
        client = self.build()
        response = client.get('/api/olx/threads?cabinet=global&awaiting=1&since=2026-10-29T10:00:00Z&limit=500',
                              headers=self.headers())
        self.assertEqual(response.status_code, 200)
        _, filters = self.services.calls[0]
        self.assertEqual(filters, {'cabinet_code': 'global', 'awaiting_only': True,
                                   'since': datetime(2026, 10, 29, 15, 0), 'limit': 200, 'offset': 0})
        item = response.get_json()['threads'][0]
        self.assertEqual((item['cabinet'], item['thread_id'], item['phone'], item['amo_lead_id']),
                         ('global', '4195536101', '77011234567', 5))
        self.assertEqual(item['last_message_at'], '2026-10-29T13:48:10+05:00')
        self.assertEqual(response.get_json()['pagination']['total'], 1)
        self.assertEqual(client.get('/api/olx/threads?cabinet=nope', headers=self.headers()).status_code, 404)
        self.assertEqual(client.get('/api/olx/threads?since=yesterday', headers=self.headers()).status_code, 400)

    def test_conversation_splits_messages_and_events(self):
        client = self.build()
        response = client.get('/api/olx/threads/global/4195536101', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([m['direction'] for m in payload['messages']], ['in', 'out'])
        self.assertEqual(payload['messages'][1]['author'], 'Робот')
        self.assertEqual(payload['events'][0]['event'], 'lead_created')
        self.assertEqual(payload['thread']['advert_title'], 'Водитель')
        self.assertEqual(payload['cabinet']['code'], 'global')
        self.assertEqual(client.get('/api/olx/threads/nope/1', headers=self.headers()).status_code, 404)

    def test_journal(self):
        client = self.build()
        response = client.get('/api/olx/journal?cabinet=global&from=2026-10-01&result=lead_created',
                              headers=self.headers())
        self.assertEqual(response.status_code, 200)
        _, filters = self.services.calls[0]
        self.assertEqual((filters['cabinet_code'], filters['result'], filters['date_from']),
                         ('global', 'lead_created', datetime(2026, 10, 1)))
        self.assertEqual(response.get_json()['items'][0]['phone'], '77011234567')
        self.assertEqual(client.get('/api/olx/journal?from=когда-то', headers=self.headers()).status_code, 400)


class ReplyTests(Harness):
    def test_reply_goes_as_the_partner_and_is_journaled(self):
        client = self.build()
        response = client.post('/api/olx/threads/global/4195536101/messages', json={'text': ' Добрый день! '},
                               headers=self.headers())
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual((payload['status'], payload['outbound_id'], payload['author']),
                         ('sent', 5120, 'Партнёр: CRM «Потока»'))
        _, code, thread_id, text, actor = self.services.calls[0]
        self.assertEqual((code, thread_id, text), ('global', '4195536101', ' Добрый день! '))
        self.assertEqual((actor['source'], actor['key_id'], actor['user_id']), ('partner', 7, None))
        entry = self.cursor.journal()[0]
        self.assertEqual(entry['outcome'], 'ok')
        self.assertEqual(json.loads(entry['details'])['text'], 'Добрый день!')
        self.assertEqual(client.post('/api/olx/threads/global/1/messages', json={'text': '  '},
                                     headers=self.headers()).status_code, 400)

    def test_refusals_map_to_documented_codes(self):
        cases = (('too_long', 400, 'OLX_API_BAD_REQUEST'), ('duplicate', 409, 'OLX_API_DUPLICATE'),
                 ('rate_limited', 429, 'OLX_API_RATE_LIMITED'), ('needs_auth', 503, 'OLX_API_CABINET_UNAVAILABLE'),
                 ('olx_error', 502, 'OLX_API_UPSTREAM'))
        for reason, status, code in cases:
            client = self.build()
            self.services.reply_error = ReplyRefused(reason, 'отказ', retry_after=30 if reason == 'rate_limited' else None)
            response = client.post('/api/olx/threads/global/1/messages', json={'text': 'x'}, headers=self.headers())
            self.assertEqual(response.status_code, status, reason)
            self.assertEqual(response.get_json()['code'], code)
            self.assertEqual(response.get_json()['reason'], reason)
            if reason == 'rate_limited':
                self.assertEqual(response.headers['Retry-After'], '30')


class WebhookTests(Harness):
    def test_put_validates_and_issues_a_secret_once(self):
        client = self.build()
        self.assertEqual(client.put('/api/olx/webhook', json={'url': 'http://crm.kz/x'}, headers=self.headers()).status_code, 400)
        self.assertEqual(client.put('/api/olx/webhook', json={'url': 'https://crm.kz/x', 'events': ['all']},
                                    headers=self.headers()).status_code, 400)
        response = client.put('/api/olx/webhook', json={'url': 'https://crm.kz/x'}, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['secret'])
        self.assertEqual(payload['subscription']['url'], 'https://crm.kz/x')
        self.assertEqual(payload['subscription']['events'], ['message.received', 'message.sent'])
        self.assertNotIn('secret', payload['subscription'])
        self.assertEqual(payload['signature']['header'], webhooks.SIGNATURE_HEADER)
        saved = [call for call in self.services.calls if call[0] == 'save'][0]
        self.assertEqual((saved[2], saved[3], saved[4]), ('https://crm.kz/x', payload['secret'], []))
        again = client.put('/api/olx/webhook', json={'url': 'https://crm.kz/x', 'secret': 'my-own-secret-0123456789',
                                                      'events': ['message.sent']}, headers=self.headers())
        self.assertNotIn('secret', again.get_json())
        self.assertEqual(again.get_json()['subscription']['events'], ['message.sent'])

    def test_get_delete_test_and_deliveries(self):
        client = self.build()
        self.assertIsNone(client.get('/api/olx/webhook', headers=self.headers()).get_json()['subscription'])
        self.assertEqual(client.delete('/api/olx/webhook', headers=self.headers()).status_code, 404)
        self.assertEqual(client.post('/api/olx/webhook/test', headers=self.headers()).status_code, 404)
        self.assertEqual(client.get('/api/olx/webhook/deliveries', headers=self.headers()).status_code, 404)
        client.put('/api/olx/webhook', json={'url': 'https://crm.kz/x'}, headers=self.headers())
        shown = client.get('/api/olx/webhook', headers=self.headers()).get_json()['subscription']
        self.assertEqual(shown['deliveries'], {'ready': 0, 'done': 2, 'failed': 0})
        self.assertNotIn('secret', shown)
        self.assertEqual(client.post('/api/olx/webhook/test', headers=self.headers()).get_json()['status'], 'queued')
        self.assertEqual(client.get('/api/olx/webhook/deliveries', headers=self.headers()).get_json(), {'deliveries': []})
        self.assertEqual(client.delete('/api/olx/webhook', headers=self.headers()).get_json(), {'status': 'deleted'})


class EventTests(unittest.TestCase):
    def test_received_event_shape(self):
        published = []

        def publish(db, scope, event, payload, **kwargs):
            published.append((scope, event, payload, kwargs))
            return 1

        original = webhooks.publish
        webhooks.publish = publish
        try:
            thread = {'id': 4195536101, 'advert_id': 12, 'interlocutor_id': 88}
            state = {'advert_title': 'Водитель', 'interlocutor_name': 'Айдар', 'amo_lead_id': None}
            message = {'id': 9910, 'text': 'Здравствуйте', 'attachments': []}
            count = partner_events.message_received(None, CABINET, thread, state, message, 'lead_created',
                                                    phone='77011234567', phone_raw='8 701 123 45 67',
                                                    message_at=datetime(2026, 10, 29, 13, 48, 10))
            self.assertEqual(count, 1)
            scope, event, payload, _kwargs = published[0]
            self.assertEqual((scope, event), ('olx_chats', 'message.received'))
            self.assertEqual(payload['cabinet']['code'], 'global')
            self.assertEqual(payload['thread'], {'id': '4195536101', 'advert_id': '12', 'advert_title': 'Водитель',
                                                 'interlocutor_id': '88', 'interlocutor_name': 'Айдар',
                                                 'awaiting_human_since': None, 'phone': None, 'amo_lead_id': None})
            self.assertEqual(payload['message']['at'], '2026-10-29T13:48:10+05:00')
            self.assertEqual(payload['message']['phone'], '77011234567')
            self.assertEqual(payload['robot'], {'outcome': 'lead_created'})
            partner_events.message_sent(None, CABINET, '41', 'Текст', kind='portal', author='Партнёр: CRM',
                                        source='partner', sent_at=datetime(2026, 10, 29, 13, 50))
            self.assertEqual(published[1][1], 'message.sent')
            self.assertEqual(published[1][2]['message'], {'text': 'Текст', 'at': '2026-10-29T13:50:00+05:00',
                                                          'kind': 'portal', 'source': 'partner',
                                                          'author': 'Партнёр: CRM'})
            partner_events.ping(None, 7)
            self.assertEqual(published[2][3], {'key_id': 7})
        finally:
            webhooks.publish = original

    def test_events_never_break_the_robot(self):
        original = webhooks.publish
        webhooks.publish = MagicMock(side_effect=RuntimeError('упало'))
        try:
            with self.assertLogs(level='ERROR'):
                self.assertEqual(partner_events.message_sent(None, CABINET, '1', 'x', kind='canned'), 0)
        finally:
            webhooks.publish = original


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT.read_text(encoding='utf-8-sig')
        cls.service = (ROOT / 'olx_amo' / 'service.py').read_text(encoding='utf-8')

    def test_the_poll_reports_every_incoming_message_with_the_robot_outcome(self):
        self.assertIn('outcome = _handle_message(db, writers, cabinet, thread, message, result)', self.service)
        self.assertIn('partner_events.message_received(db, cabinet, thread, state, message, outcome,', self.service)
        for code in ("return 'needs_human'", "return 'canned_reply'", "return 'duplicate'", "return 'error'",
                     "return 'manual_review' if needs_manual else 'lead_created'"):
            self.assertIn(code, self.service, code)
        self.assertEqual(self.service.count("partner_events.message_sent("), 2)

    def test_the_entry_is_registered_without_the_session_and_the_worker_starts(self):
        start = self.bot.index('build_olx_partner_blueprint(')
        call = self.bot[start:self.bot.index('))', start)]
        self.assertIn('db=db', call)
        self.assertIn('client_ip=_client_ip', call)
        self.assertNotIn('require_api_key', call)
        self.assertIn('partner_webhooks.start_worker(db)', self.bot)

    def test_the_document_and_the_readme_name_the_contract(self):
        doc = (ROOT / 'docs' / 'OLX_PARTNER_API.md').read_text(encoding='utf-8')
        for token in ('/api/olx/cabinets', '/api/olx/threads', '/api/olx/journal', '/api/olx/webhook',
                      'message.received', 'message.sent', webhooks.SIGNATURE_HEADER, webhooks.TIMESTAMP_HEADER,
                      'Idempotency-Key', 'OLX_API_UNKNOWN_CABINET', 'OLX_API_RATE_LIMITED', 'OLX_API_LIMIT',
                      'X-Api-Key'):
            self.assertIn(token, doc, token)
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        for token in ('Чаты OLX', 'docs/OLX_PARTNER_API.md', 'OLX_API_PER_MINUTE', 'PARTNER_WEBHOOKS_DISABLED'):
            self.assertIn(token, readme, token)

    def test_parse_moment(self):
        self.assertEqual(partner_routes.parse_moment('2026-10-29T10:00:00Z'), datetime(2026, 10, 29, 15, 0))
        self.assertEqual(partner_routes.parse_moment('2026-10-29T15:00:00+05:00'), datetime(2026, 10, 29, 15, 0))
        self.assertEqual(partner_routes.parse_moment('2026-10-29'), datetime(2026, 10, 29))
        self.assertIsNone(partner_routes.parse_moment('вчера'))


if __name__ == '__main__':
    unittest.main()
