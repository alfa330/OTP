# -*- coding: utf-8 -*-
"""Подписки партнёров на события и их доставка (partner_keys/webhooks.py).

Что здесь сторожится:

* **Правила адреса и секрета**: только https без учётных данных, секрет — ASCII
  от 16 знаков, события — только известные.
* **Подпись**: HMAC-SHA256 секретом от «timestamp.body», тем же способом, что в
  документе партнёру.
* **Публикация**: строка очереди на каждую подходящую подписку; без получателей —
  ничего; `key_id` сужает до одного ключа.
* **Доставка**: успех закрывает строку и сбрасывает сбои подписки; не-2xx и
  обрыв сети — повтор с растущей задержкой, Retry-After уважается; выключенная
  подписка — отказ без HTTP.

Сети и базы нет: курсор — двойник, транспорт — функция.
"""

import json
import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from partner_keys import webhooks  # noqa: E402

SECRET = 'partner-secret-for-tests-0123456789'


class RulesTests(unittest.TestCase):
    def test_url_rules(self):
        self.assertEqual(webhooks.validate_url(' https://crm.example.kz/icore/olx ')[0],
                         'https://crm.example.kz/icore/olx')
        for bad in (None, '', 'http://crm.example.kz/x', 'https://user:pw@crm.example.kz/x',
                    'https:///path', 'https://crm.example.kz/с пробелом', 'ftp://x.kz/',
                    'https://x.kz/' + 'a' * webhooks.MAX_URL):
            url, error = webhooks.validate_url(bad)
            self.assertIsNone(url, bad)
            self.assertTrue(error, bad)

    def test_secret_rules(self):
        self.assertEqual(webhooks.normalize_secret(None), (None, None))
        self.assertEqual(webhooks.normalize_secret(''), (None, None))
        self.assertEqual(webhooks.normalize_secret(' ' + SECRET + ' '), (SECRET, None))
        for bad in ('short', 'секрет-кириллицей-длинный-очень', 'with space ' * 3, 'x' * 201):
            secret, error = webhooks.normalize_secret(bad)
            self.assertIsNone(secret, bad)
            self.assertTrue(error, bad)
        generated = webhooks.generate_secret()
        self.assertEqual(webhooks.normalize_secret(generated), (generated, None))

    def test_events_rules(self):
        allowed = ('message.received', 'message.sent')
        self.assertEqual(webhooks.normalize_events(None, allowed), ([], None))
        self.assertEqual(webhooks.normalize_events('message.sent', allowed), (['message.sent'], None))
        self.assertEqual(webhooks.normalize_events(['message.sent', 'message.sent', ''], allowed),
                         (['message.sent'], None))
        self.assertIsNone(webhooks.normalize_events(['everything'], allowed)[0])
        self.assertIsNone(webhooks.normalize_events({'a': 1}, allowed)[0])

    def test_signature_matches_the_documented_scheme(self):
        import hashlib
        import hmac
        body = webhooks.body_for('ping', uuid.UUID(int=7), datetime(2026, 10, 29, 9, 5, tzinfo=timezone.utc),
                                 {'a': 'я'})
        self.assertEqual(json.loads(body.decode('utf-8')),
                         {'event': 'ping', 'id': str(uuid.UUID(int=7)),
                          'created_at': '2026-10-29T09:05:00+00:00', 'data': {'a': 'я'}})
        self.assertIn('я'.encode('utf-8'), body)  # без \\u-экранирования
        expected = 'sha256=' + hmac.new(SECRET.encode(), b'1761735911.' + body, hashlib.sha256).hexdigest()
        self.assertEqual(webhooks.sign(SECRET, '1761735911', body), expected)
        self.assertNotEqual(webhooks.sign(SECRET, '1761735912', body), expected)

    def test_retry_delay_grows_and_honours_retry_after(self):
        self.assertEqual(webhooks._retry_delay(1), 5)
        self.assertEqual(webhooks._retry_delay(2), 10)
        self.assertEqual(webhooks._retry_delay(30), 3600)
        response = MagicMock(status_code=429, headers={'Retry-After': '120'})
        self.assertEqual(webhooks._retry_delay(1, response), 120)
        response = MagicMock(status_code=500, headers={'Retry-After': '120'})
        self.assertEqual(webhooks._retry_delay(1, response), 5)


class _Cursor:
    def __init__(self, targets=(), claimed=None):
        self.calls = []
        self.targets = list(targets)
        self.claimed = claimed
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._one, self._all = None, []
        if text.startswith('SELECT w.id FROM partner_webhooks w'):
            self._all = [(target,) for target in self.targets]
        elif text.startswith('WITH candidate AS'):
            self._one = self.claimed
            self.claimed = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)

    def inserted(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO partner_webhook_outbox')]

    def updates(self, table):
        return [(text, params) for text, params in self.calls if text.startswith('UPDATE ' + table)]


def fake_db(cursor):
    db = MagicMock()

    @contextmanager
    def _get_cursor():
        yield cursor

    db._get_cursor = _get_cursor
    return db


class PublishTests(unittest.TestCase):
    def test_a_row_per_matching_subscription(self):
        cursor = _Cursor(targets=(1, 2))
        count = webhooks.publish_with_cursor(cursor, 'olx_chats', 'message.received', {'thread': {'id': '5'}})
        self.assertEqual(count, 2)
        rows = cursor.inserted()
        self.assertEqual([row[0] for row in rows], [1, 2])
        self.assertEqual(rows[0][1], 'message.received')
        uuid.UUID(rows[0][2])
        self.assertEqual(json.loads(rows[0][3]), {'thread': {'id': '5'}})
        select = cursor.calls[0][0]
        for token in ('w.enabled', 'k.revoked_at IS NULL', '%(scope)s = ANY(k.scopes)',
                      'cardinality(w.events) = 0 OR %(event)s = ANY(w.events)'):
            self.assertIn(token, select)
        self.assertNotIn('w.key_id = %(key)s', select)

    def test_without_subscribers_nothing_is_queued_and_key_id_narrows(self):
        cursor = _Cursor(targets=())
        self.assertEqual(webhooks.publish_with_cursor(cursor, 'olx_chats', 'ping', {}, key_id=7), 0)
        self.assertEqual(cursor.inserted(), [])
        self.assertIn('w.key_id = %(key)s', cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1]['key'], 7)

    def test_publish_never_raises(self):
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            raise RuntimeError('нет соединения')
            yield  # noqa: unreachable — форма contextmanager

        db._get_cursor = _get_cursor
        with self.assertLogs(level='ERROR'):
            self.assertEqual(webhooks.publish(db, 'olx_chats', 'ping', {}), 0)


class _Response:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def close(self):
        pass


def claimed_row(enabled=True, attempts=0):
    return (5, 1, 'message.received', uuid.UUID(int=9), {'thread': {'id': '5'}}, attempts,
            datetime(2026, 10, 29, 9, 5, tzinfo=timezone.utc), uuid.UUID(int=3),
            'https://crm.example.kz/icore/olx', SECRET, enabled)


class DeliveryTests(unittest.TestCase):
    def test_success_is_signed_and_closes_the_row(self):
        cursor = _Cursor(claimed=claimed_row())
        seen = {}

        def transport(url, **kwargs):
            seen.update(kwargs, url=url)
            return _Response(204)

        self.assertTrue(webhooks.process_once(fake_db(cursor), transport))
        self.assertEqual(seen['url'], 'https://crm.example.kz/icore/olx')
        headers = seen['headers']
        self.assertEqual(headers[webhooks.EVENT_HEADER], 'message.received')
        self.assertEqual(headers['Idempotency-Key'], str(uuid.UUID(int=9)))
        self.assertEqual(headers[webhooks.DELIVERY_HEADER], str(uuid.UUID(int=9)))
        self.assertEqual(headers[webhooks.SIGNATURE_HEADER],
                         webhooks.sign(SECRET, headers[webhooks.TIMESTAMP_HEADER], seen['data']))
        self.assertEqual(json.loads(seen['data'])['data'], {'thread': {'id': '5'}})
        self.assertEqual(seen['timeout'], (3, 10))
        self.assertFalse(seen['allow_redirects'])
        outbox = cursor.updates('partner_webhook_outbox')
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0][1][0], 'done')
        self.assertTrue(outbox[0][1][1])  # delivered
        hooks = cursor.updates('partner_webhooks')
        self.assertIn('failures_in_row = 0', hooks[0][0])

    def test_a_refusal_is_retried_with_a_growing_delay(self):
        cursor = _Cursor(claimed=claimed_row(attempts=2))
        self.assertTrue(webhooks.process_once(fake_db(cursor),
                                              lambda url, **kwargs: _Response(503, {'Retry-After': '90'})))
        text, params = cursor.updates('partner_webhook_outbox')[0]
        self.assertIn('next_attempt_at = now()', text)
        attempts, state, delay = params[0], params[1], params[2]
        self.assertEqual((attempts, state), (3, 'ready'))
        self.assertEqual(delay, 90)
        self.assertIn('failures_in_row = failures_in_row + 1', cursor.updates('partner_webhooks')[0][0])

    def test_the_last_attempt_marks_the_row_failed(self):
        cursor = _Cursor(claimed=claimed_row(attempts=webhooks.MAX_ATTEMPTS - 1))
        webhooks.process_once(fake_db(cursor), lambda url, **kwargs: _Response(500))
        self.assertEqual(cursor.updates('partner_webhook_outbox')[0][1][1], 'failed')

    def test_a_network_error_is_a_retry_without_a_body_in_the_log(self):
        cursor = _Cursor(claimed=claimed_row())

        def transport(url, **kwargs):
            raise requests.ConnectionError('refused')

        webhooks.process_once(fake_db(cursor), transport)
        text, params = cursor.updates('partner_webhook_outbox')[0]
        self.assertEqual(params[1], 'ready')
        self.assertIn('transport_error', params[5])

    def test_a_disabled_subscription_is_not_posted(self):
        cursor = _Cursor(claimed=claimed_row(enabled=False))
        posted = []
        webhooks.process_once(fake_db(cursor), lambda url, **kwargs: posted.append(url) or _Response(200))
        self.assertEqual(posted, [])
        self.assertEqual(cursor.updates('partner_webhook_outbox')[0][1][0], 'failed')

    def test_an_empty_queue_yields(self):
        cursor = _Cursor(claimed=None)
        self.assertFalse(webhooks.process_once(fake_db(cursor), lambda url, **kwargs: _Response(200)))


if __name__ == '__main__':
    unittest.main()
