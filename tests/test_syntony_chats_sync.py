# -*- coding: utf-8 -*-
"""Забор чатов Syntony через API истории чатов: клиент (без сети) и прогоны сторожа на
настоящем Postgres (SYNTONY_CHATS_TEST_PORT) с подставной Syntony.

Подставная Syntony отвечает по путям так, как настоящая (§3–§5 документа; форма ответов
сверена с живым API по ключам и типам). Номера, имена и id выдуманы.
"""

import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from syntony_chats import client, rules, store, sync  # noqa: E402
from test_syntony_chats import CONVERSATION, MESSAGE  # noqa: E402

KEY = 'sck_' + 'synthetic0history0key0for0tests'
ENV = {client.ENV_KEY: KEY}
PORT = os.environ.get('SYNTONY_CHATS_TEST_PORT')
needs_pg = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')


class _Response:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = b'x' * 10

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return copy.deepcopy(self._body)


class FakeSyntony:
    """Подставная Syntony: ответы по путям, журнал запросов."""

    def __init__(self):
        self.calls = []
        self.feed_pages = {}            # cursor (None — первая) → (сообщения, nextCursor)
        self.conversations = []
        self.threads = {}               # id диалога → сообщения
        self.errors = []                # очередь (путь, ответ), отдаётся раньше обычного

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        path = urlsplit(url).path.split('/v1', 1)[1]
        self.calls.append({'path': path, 'params': dict(params or {}), 'headers': headers,
                           'timeout': timeout, 'allow_redirects': allow_redirects})
        for index, (error_path, response) in enumerate(self.errors):
            if error_path == path:
                del self.errors[index]
                return response
        if path == '/messages':
            items, next_cursor = self.feed_pages.get((params or {}).get('cursor'), ([], None))
            return _Response(200, {'apiVersion': 1, 'data': items, 'nextCursor': next_cursor})
        if path == '/conversations':
            offset, limit = int(params['offset']), int(params['limit'])
            chunk = self.conversations[offset:offset + limit]
            more = offset + limit < len(self.conversations)
            return _Response(200, {'apiVersion': 1, 'total': len(self.conversations), 'offset': offset,
                                   'limit': limit, 'nextOffset': offset + limit if more else None,
                                   'data': chunk})
        if path.startswith('/conversations/') and path.endswith('/messages'):
            conversation_id = path.split('/')[2]
            return _Response(200, {'apiVersion': 1, 'conversationId': conversation_id,
                                   'data': self.threads.get(conversation_id, []), 'nextCursor': None})
        return _Response(404, {'error': 'not_found'})


def msg(number, created, **extra):
    item = dict(copy.deepcopy(MESSAGE), id='m-%04d' % number, createdAt=created,
                statusAt=created, status='sent', text='сообщение %d' % number)
    item.update(extra)
    return item


# ── Клиент ────────────────────────────────────────────────────────────────────

def test_config_from_environment():
    assert client.load_config({}) is None
    assert client.load_config({client.ENV_KEY: 'sck_ with space'}) is None
    config = client.load_config(ENV)
    assert config.url == client.DEFAULT_URL and config.key == KEY
    assert KEY not in repr(config)
    for bad in ('http://app.syntony.kz/api/chats-api/v1', 'https://syntony.kz.evil.example/v1',
                'https://user:pw@app.syntony.kz/v1'):
        assert client.load_config(dict(ENV, **{client.ENV_URL: bad})) is None
    other = client.load_config(dict(ENV, **{client.ENV_URL: 'https://staging.syntony.kz/api/chats-api/v1/'}))
    assert other.url == 'https://staging.syntony.kz/api/chats-api/v1'


def test_get_sends_key_in_header_and_never_follows_redirects():
    fake = FakeSyntony()
    fake.feed_pages[None] = ([msg(1, '2025-11-17T09:00:00Z')], None)
    since = datetime(2025, 11, 17, 9, 0, tzinfo=timezone.utc)
    page = client.messages_page(client.load_config(ENV), since, session=fake)
    assert page['data'][0]['id'] == 'm-0001'
    [call] = fake.calls
    assert call['headers']['X-Api-Key'] == KEY
    assert call['allow_redirects'] is False
    assert call['params'] == {'since': '2025-11-17T09:00:00Z', 'limit': 1000}
    assert KEY not in str(call['params'])


@pytest.mark.parametrize('response, code, status, retry_after', [
    (_Response(401, {'error': 'invalid_api_key'}), 'invalid_api_key', 401, None),
    (_Response(429, ValueError('empty'), {'Retry-After': '45'}), 'http_429', 429, 45),
    (_Response(503, {'error': 'messaging_unavailable'}, {'Retry-After': 'soon'}), 'messaging_unavailable', 503, None),
    (_Response(200, ['not', 'an', 'object']), 'bad_reply', 200, None),
    (_Response(302, {}, {'Location': 'https://elsewhere.example/'}), 'http_302', 302, None),
])
def test_get_refusals(response, code, status, retry_after):
    class _Session:
        def get(self, *args, **kwargs):
            return response
    with pytest.raises(client.SyntonyHistoryError) as caught:
        client.get(client.load_config(ENV), '/channels', session=_Session())
    assert (caught.value.code, caught.value.status, caught.value.retry_after) == (code, status, retry_after)


def test_network_error_keeps_key_and_params_out_of_the_message():
    class _Session:
        def get(self, url, params=None, headers=None, **kwargs):
            raise requests.exceptions.InvalidHeader('bad header value %s for %s' % (headers['X-Api-Key'], params))
    with pytest.raises(client.SyntonyHistoryError) as caught:
        client.conversations_page(client.load_config(ENV), None, session=_Session())
    assert caught.value.code == 'network:InvalidHeader'
    assert KEY not in str(caught.value) and KEY not in repr(caught.value.args)


def test_conversation_id_is_quoted_into_the_path():
    fake = FakeSyntony()
    client.conversation_messages_page(client.load_config(ENV), 'a/b?c', session=fake)
    assert fake.calls[0]['path'] == '/conversations/a%2Fb%3Fc/messages'


# ── Правила сторожа без базы ─────────────────────────────────────────────────

def test_night_window_and_once_per_day():
    almaty = sync.ALMATY
    night = datetime(2025, 11, 18, 3, 30, tzinfo=almaty)
    assert sync.night_due(None, night)
    assert not sync.night_due(None, datetime(2025, 11, 18, 14, 0, tzinfo=almaty))
    assert not sync.night_due(datetime(2025, 11, 18, 3, 5, tzinfo=almaty), night)
    assert sync.night_due(datetime(2025, 11, 17, 3, 5, tzinfo=almaty), night)


def test_keep_days_from_environment():
    assert sync.keep_days({}) == 30
    assert sync.keep_days({sync.ENV_KEEP_DAYS: '60'}) == 60
    assert sync.keep_days({sync.ENV_KEEP_DAYS: '1'}) == sync.MIN_KEEP_DAYS
    assert sync.keep_days({sync.ENV_KEEP_DAYS: 'много'}) == 30
    assert sync.is_enabled({}) and not sync.is_enabled({sync.ENV_ENABLED: 'false'})


def test_worker_does_not_start_without_key(monkeypatch):
    monkeypatch.delenv(client.ENV_KEY, raising=False)
    assert sync.start_worker(object(), first_delay=3600) is None
    monkeypatch.setenv(client.ENV_KEY, KEY)
    monkeypatch.setenv(sync.ENV_ENABLED, 'false')
    assert sync.start_worker(object(), first_delay=3600) is None


def test_journal_logs_hourly_and_stops_at_once(caplog):
    ticks = iter([0, 10, 20, 3700, 3710])
    journal = sync._Journal(clock=lambda: next(ticks))
    with caplog.at_level('INFO'):
        journal.note({'feed': (5, 1), 'conversations': 2})
        journal.note({'feed': (0, 1), 'conversations': 0, 'stopped': 'invalid_api_key'})
        journal.note({'feed': (7, 2), 'conversations': 1})
    assert caplog.text.count('прогон остановлен — invalid_api_key') == 1
    assert caplog.text.count('за час') == 1
    assert 'сообщений записано 12' in caplog.text


# ── Прогоны на Postgres ───────────────────────────────────────────────────────

@pytest.fixture()
def db():
    if not PORT:
        pytest.skip('needs local PostgreSQL (SYNTONY_CHATS_TEST_PORT)')
    import test_syntony_chats_postgres as pg
    yield from _schema_db(pg)


def _schema_db(pg):
    import uuid
    import psycopg2
    from syntony_chats import schema
    name = 'syntony_sync_' + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                             connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    database = pg._Db(name)
    try:
        with database._get_cursor() as cur:
            schema.init_syntony_chats_schema(cur)
        yield database
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.close()


NOW = datetime(2025, 11, 17, 10, 0, tzinfo=timezone.utc)        # 15:00 Алматы


def count(db, table):
    return db.one('SELECT count(*) FROM ' + table)[0]


@needs_pg
def test_first_run_reads_feed_since_midnight_and_conversation_snapshots(db):
    fake = FakeSyntony()
    fake.feed_pages[None] = ([msg(1, '2025-11-17T04:00:00Z'), msg(2, '2025-11-17T08:00:00Z')], 'c2')
    fake.feed_pages['c2'] = ([msg(3, '2025-11-17T09:58:00Z')], None)
    fake.conversations = [CONVERSATION]
    sleeps = []
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=sleeps.append, session=fake)
    assert summary == {'feed': (3, 2), 'conversations': 1}
    feed_calls = [c for c in fake.calls if c['path'] == '/messages']
    # Полночь по Алматы = 19:00 UTC накануне.
    assert feed_calls[0]['params']['since'] == '2025-11-16T19:00:00Z'
    assert feed_calls[1]['params']['cursor'] == 'c2' and feed_calls[1]['params']['since'] == '2025-11-16T19:00:00Z'
    assert sync.FEED_PAUSE_SECONDS in sleeps                       # лимит ленты 10 в минуту
    assert count(db, 'syntony_chat_messages') == 3
    row = db.message('m-0003')
    assert (row['first_seen_via'], row['text'], row['status']) == ('api', 'сообщение 3', 'sent')
    assert db.conversation()['park_name'] == 'Global'
    with db._get_cursor() as cur:
        marks = store.get_marks(cur)
    assert marks[sync.MARK_FEED] == datetime(2025, 11, 17, 9, 53, tzinfo=timezone.utc)
    assert marks[sync.MARK_CONVERSATIONS] == NOW - sync.OVERLAP
    assert sync.MARK_REREAD not in marks and sync.MARK_CLEANUP not in marks   # день, не ночь


@needs_pg
def test_next_run_starts_five_minutes_before_last_message_and_skips_repeats(db):
    fake = FakeSyntony()
    fake.feed_pages[None] = ([msg(1, '2025-11-17T09:58:00Z')], None)
    sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    fake.calls.clear()
    fake.feed_pages[None] = ([msg(1, '2025-11-17T09:58:00Z'), msg(2, '2025-11-17T10:01:00Z')], None)
    summary = sync.run_once(db, environ=ENV, now=NOW + timedelta(minutes=3), sleep=lambda s: None,
                            session=fake)
    assert summary['feed'] == (1, 1)                                # повтор m-0001 не записан
    assert fake.calls[0]['params']['since'] == '2025-11-17T09:53:00Z'
    assert fake.calls[1]['params']['updatedSince'] == '2025-11-17T09:55:00Z'


@needs_pg
def test_quiet_feed_moves_the_mark_forward(db):
    fake = FakeSyntony()
    sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    with db._get_cursor() as cur:
        assert store.get_marks(cur)[sync.MARK_FEED] == NOW - sync.OVERLAP


@needs_pg
def test_long_feed_is_read_in_parts(db, monkeypatch):
    monkeypatch.setattr(sync, 'FEED_PAGES_PER_RUN', 2)
    fake = FakeSyntony()
    fake.feed_pages[None] = ([msg(1, '2025-11-17T05:00:00Z')], 'c2')
    fake.feed_pages['c2'] = ([msg(2, '2025-11-17T06:00:00Z')], 'c3')
    fake.feed_pages['c3'] = ([msg(3, '2025-11-17T07:00:00Z')], None)
    assert sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)['feed'] == (2, 2)
    with db._get_cursor() as cur:
        assert store.get_marks(cur)[sync.MARK_FEED] == datetime(2025, 11, 17, 5, 55, tzinfo=timezone.utc)


@needs_pg
def test_rate_limit_waits_retry_after_once_then_goes_on(db):
    fake = FakeSyntony()
    fake.errors.append(('/messages', _Response(429, ValueError('empty'), {'Retry-After': '30'})))
    fake.feed_pages[None] = ([msg(1, '2025-11-17T09:00:00Z')], None)
    sleeps = []
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=sleeps.append, session=fake)
    assert summary['feed'] == (1, 1) and 'stopped' not in summary
    assert 30 in sleeps


@needs_pg
def test_rejected_key_stops_the_run_and_keeps_the_mark(db):
    fake = FakeSyntony()
    fake.errors.append(('/messages', _Response(401, {'error': 'invalid_api_key'})))
    summary = sync.run_once(db, environ=ENV, now=NOW, sleep=lambda s: None, session=fake)
    assert summary == {'stopped': 'invalid_api_key'}
    assert [c['path'] for c in fake.calls] == ['/messages']
    with db._get_cursor() as cur:
        assert store.get_marks(cur) == {}


@needs_pg
def test_night_rereads_the_day_and_cleans_old_chats(db):
    night = datetime(2025, 11, 17, 22, 30, tzinfo=timezone.utc)      # 03:30 Алматы 18.11
    old_conversation = dict(CONVERSATION, id='c-old', lastMessageAt='2025-09-01T10:00:00Z')
    with db._get_cursor() as cur:
        store.upsert_conversation(cur, rules.conversation_row(old_conversation, night - timedelta(days=70)))
        store.upsert_message(cur, rules.message_row(msg(90, '2025-09-01T10:00:00Z', conversationId='c-old'),
                                                    night - timedelta(days=70), rules.VIA_API))
        # Сообщение пришло событием «отправлено», а «прочитано» вебхук не принёс.
        store.upsert_message(cur, rules.message_row(msg(1, '2025-11-17T15:00:00Z'),
                                                    night - timedelta(hours=7), rules.VIA_WEBHOOK))
    fake = FakeSyntony()
    fake.conversations = [CONVERSATION]
    fake.threads[CONVERSATION['id']] = [msg(1, '2025-11-17T15:00:00Z', status='read',
                                            statusAt='2025-11-17T15:30:00Z'),
                                        msg(2, '2025-11-17T15:01:00Z')]
    summary = sync.run_once(db, environ=ENV, now=night, sleep=lambda s: None, session=fake)
    assert summary['reread'] == (1, 2)
    assert summary['cleanup'] == (1, 1)
    first = db.message('m-0001')
    assert (first['status'], first['read_at'], first['first_seen_via']) == (
        'read', datetime(2025, 11, 17, 15, 30, tzinfo=timezone.utc), 'webhook')
    assert db.message('m-0002')['first_seen_via'] == 'api'
    assert db.message('m-0090') is None and db.conversation('c-old') is None
    # Той же ночью второй раз не перечитывает и не чистит.
    again = sync.run_once(db, environ=ENV, now=night + timedelta(minutes=3), sleep=lambda s: None,
                          session=fake)
    assert 'reread' not in again and 'cleanup' not in again


@needs_pg
def test_cleanup_works_in_batches(db, monkeypatch):
    monkeypatch.setattr(sync, 'DELETE_BATCH', 2)
    old = datetime(2025, 9, 1, tzinfo=timezone.utc)
    with db._get_cursor() as cur:
        for number in range(5):
            store.upsert_message(cur, rules.message_row(msg(number, '2025-09-01T10:00:00Z'), old, rules.VIA_API))
        store.upsert_message(cur, rules.message_row(msg(9, '2025-11-16T10:00:00Z'), old, rules.VIA_API))
    assert sync.cleanup(db, now=NOW, days=45) == (5, 0)
    assert count(db, 'syntony_chat_messages') == 1


@needs_pg
def test_without_key_nothing_happens(db):
    fake = FakeSyntony()
    assert sync.run_once(db, environ={}, now=NOW, session=fake) == {'skipped': 'not_configured'}
    assert fake.calls == []
