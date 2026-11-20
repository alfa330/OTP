# -*- coding: utf-8 -*-
"""Вебхуки чатов Syntony без базы: подпись, разбор событий, строки для базы, ответы ручки.

Запись в настоящий Postgres — tests/test_syntony_chats_postgres.py. Люди, номера и id
выдуманы; форма событий — по документу Syntony «API истории чатов и вебхуки чатов»
(версия 1, 18.11.2026) и по живым ответам API (ключи и типы полей, без значений).
"""

import copy
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from syntony_chats import routes, rules, store  # noqa: E402

SECRET = 'synthetic-chats-webhook-secret'
NOW = 1_763_373_600          # 2025-11-17T10:00:00Z — часы ручки в тестах
URL = '/api/syntony/chats/webhook'

CONVERSATION = {
    'id': 'c3d9a1e7-0000-4000-8000-000000000001',
    'park': {'id': '3f2a0000-0000-4000-8000-0000000000aa', 'name': 'Global'},
    'channel': {'id': '66219160-0000-4000-8000-0000000000bb', 'type': 'whatsapp',
                'provider': 'waba_direct', 'name': '77000000000 — WABA',
                'phoneNumber': '77000000000'},
    'contact': {'id': '1b7e0000-0000-4000-8000-0000000000cc', 'phone': '77000000001',
                'name': 'Тестовый Водитель', 'externalId': '77000000001'},
    'status': 'active',
    'assignedTo': {'kind': 'user', 'user': {'id': '5e9b0000-0000-4000-8000-0000000000dd',
                                            'fullName': 'Тестовый Оператор'}},
    'messageCount': 14,
    'createdAt': '2025-11-15T08:12:33Z',
    'lastMessageAt': '2025-11-17T09:40:02Z',
    'lastInboundAt': '2025-11-17T09:39:51Z',
    'closedAt': None,
    'closedReason': None,
    'leadId': '7b1e0000-0000-4000-8000-0000000000ee',
    'handoff': {'requestedAt': '2025-11-17T09:20:00Z', 'resolvedAt': '2025-11-17T09:31:00Z',
                'reason': 'complaint', 'source': 'ai_initiated', 'summary': 'Вопрос про штраф'},
}

MESSAGE = {
    'id': '0f4b7c31-0000-4000-8000-000000000001',
    'conversationId': CONVERSATION['id'],
    'direction': 'outbound',
    'senderType': 'human',
    'origin': 'crm_operator',
    'author': {'id': '5e9b0000-0000-4000-8000-0000000000dd', 'fullName': 'Тестовый Оператор'},
    'contentType': 'image',
    'text': 'Вот образец техпаспорта',
    'media': {'mimeType': 'image/jpeg', 'fileName': 'sample.jpg',
              'url': '/api/chats-api/v1/files/0f4b7c31-0000-4000-8000-000000000001'},
    'externalMessageId': 'wamid.TEST',
    'replyToMessageId': None,
    'status': 'read',
    'statusAt': '2025-11-17T09:41:10Z',
    'createdAt': '2025-11-17T09:40:02Z',
    'editedAt': None,
    'deletedAt': None,
    'deleteScope': None,
    'source': 'inbox',
}


def created_event(message=None, conversation=None, occurred='2025-11-17T09:40:02Z',
                  event_id='evt_00000000000000000000000000000001'):
    return {
        'event': 'message.created',
        'eventId': event_id,
        'occurredAt': occurred,
        'webhookId': '0b7e0000-0000-4000-8000-0000000000ff',
        'data': {'message': copy.deepcopy(message or MESSAGE),
                 'conversation': copy.deepcopy(conversation or CONVERSATION)},
    }


def status_event(status='failed', message_id=MESSAGE['id'], status_at='2025-11-17T09:41:10Z',
                 error=None, event_id='evt_00000000000000000000000000000002'):
    data = {
        'messageId': message_id, 'conversationId': CONVERSATION['id'],
        'park': {'id': CONVERSATION['park']['id'], 'name': 'Global'},
        'channel': {'id': CONVERSATION['channel']['id'], 'phoneNumber': '77000000000'},
        'contact': {'phone': '77000000001'},
        'status': status, 'statusAt': status_at,
    }
    if error is not None:
        data['error'] = error
    return {'event': 'message.status', 'eventId': event_id, 'occurredAt': status_at,
            'webhookId': '0b7e0000-0000-4000-8000-0000000000ff', 'data': data}


def body_of(payload):
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


def signed(body, secret=SECRET, ts=NOW):
    ts = str(ts)
    signature = hmac.new(secret.encode('utf-8'), ts.encode('ascii') + b'.' + body,
                         hashlib.sha256).hexdigest()
    return {rules.SIGNATURE_HEADER: signature, rules.TIMESTAMP_HEADER: ts,
            'Content-Type': 'application/json; charset=utf-8'}


# ── Подпись ───────────────────────────────────────────────────────────────────

class TestSignature:
    body = body_of(created_event())

    def test_valid_signature_with_cyrillic_body(self):
        assert 'Тестовый'.encode('utf-8') in self.body
        assert rules.verify_signature(self.body, signed(self.body), [SECRET], NOW) is None

    def test_any_of_several_secrets(self):
        headers = signed(self.body, secret='second-synthetic-secret')
        assert rules.verify_signature(self.body, headers, [SECRET, 'second-synthetic-secret'], NOW) is None
        assert rules.verify_signature(self.body, headers, [SECRET], NOW) == 'bad_signature'

    def test_body_or_timestamp_changed_breaks_signature(self):
        headers = signed(self.body)
        assert rules.verify_signature(self.body + b' ', headers, [SECRET], NOW) == 'bad_signature'
        moved = dict(headers, **{rules.TIMESTAMP_HEADER: str(NOW + 1)})
        assert rules.verify_signature(self.body, moved, [SECRET], NOW) == 'bad_signature'

    def test_hex_case_and_sha256_prefix_are_tolerated(self):
        headers = signed(self.body)
        upper = dict(headers, **{rules.SIGNATURE_HEADER: headers[rules.SIGNATURE_HEADER].upper()})
        prefixed = dict(headers, **{rules.SIGNATURE_HEADER: 'sha256=' + headers[rules.SIGNATURE_HEADER]})
        assert rules.verify_signature(self.body, upper, [SECRET], NOW) is None
        assert rules.verify_signature(self.body, prefixed, [SECRET], NOW) is None

    @pytest.mark.parametrize('signature', ['', 'zz' * 32, 'ab' * 31, 'ab' * 33, 'подпись'])
    def test_malformed_signature(self, signature):
        headers = dict(signed(self.body), **{rules.SIGNATURE_HEADER: signature})
        expected = 'missing_signature' if not signature else 'bad_signature'
        assert rules.verify_signature(self.body, headers, [SECRET], NOW) == expected

    @pytest.mark.parametrize('ts, code', [
        (NOW - 301, 'stale_timestamp'), (NOW + 301, 'stale_timestamp'),
        ('17633736OO', 'bad_timestamp'), ('-5', 'bad_timestamp'), ('1.5', 'bad_timestamp'),
    ])
    def test_timestamp_rules(self, ts, code):
        assert rules.verify_signature(self.body, signed(self.body, ts=ts), [SECRET], NOW) == code

    def test_five_minutes_either_way_and_milliseconds(self):
        for ts in (NOW - 300, NOW + 300, NOW * 1000):
            assert rules.verify_signature(self.body, signed(self.body, ts=ts), [SECRET], NOW) is None

    def test_missing_headers(self):
        assert rules.verify_signature(self.body, {}, [SECRET], NOW) == 'missing_signature'

    def test_non_ascii_secret_does_not_crash(self):
        secret = 'секрет-synthetic'
        headers = signed(self.body, secret=secret)
        assert rules.verify_signature(self.body, headers, [secret], NOW) is None
        assert rules.verify_signature(self.body, headers, [SECRET], NOW) == 'bad_signature'

    def test_secrets_from_environment(self):
        assert rules.load_secrets({}) == []
        assert rules.load_secrets({rules.ENV_SECRET: '  '}) == []
        assert rules.load_secrets({rules.ENV_SECRET: ' one-secret ,two-secret\nthree '}) == [
            'one-secret', 'two-secret', 'three']


# ── Разбор событий и строки для базы ─────────────────────────────────────────

def test_parse_event():
    event = rules.parse_event(body_of(created_event()))
    assert event['event'] == 'message.created'
    assert event['event_id'] == 'evt_00000000000000000000000000000001'
    assert event['occurred_at'] == datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)
    assert event['data']['message']['id'] == MESSAGE['id']
    test = rules.parse_event(b'{"event":"webhook.test","eventId":"evt_1","occurredAt":null}')
    assert test['data'] == {} and test['occurred_at'] is None


@pytest.mark.parametrize('raw, code', [
    (b'not json', 'bad_json'), (b'\xff\xfe', 'bad_json'), (b'[1, 2]', 'bad_json'),
    (b'{"eventId": "evt_1"}', 'bad_event'), (b'{"event": "message.created"}', 'bad_event'),
    (b'{"event": "message.created", "eventId": "evt_1", "data": [1]}', 'bad_event'),
])
def test_parse_event_refusals(raw, code):
    with pytest.raises(rules.PayloadError) as caught:
        rules.parse_event(raw)
    assert caught.value.code == code


@pytest.mark.parametrize('text, expected', [
    ('2025-11-17T09:40:02Z', datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)),
    ('2025-11-17T09:40:02.123Z', datetime(2025, 11, 17, 9, 40, 2, 123000, tzinfo=timezone.utc)),
    ('2025-11-17T14:40:02+05:00', datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)),
    ('2025-11-17T09:40:02', datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)),
    ('вчера', None), ('', None), (None, None), (1763373600, None),
])
def test_parse_time(text, expected):
    assert rules.parse_time(text) == expected


def test_conversation_row_takes_every_field_and_keeps_unknown_ones():
    conversation = dict(CONVERSATION, slaDueAt='2025-11-17T10:00:00Z')
    conversation['channel'] = dict(CONVERSATION['channel'], isActive=True)
    snapshot = datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)
    row = rules.conversation_row(conversation, snapshot)
    assert row['id'] == CONVERSATION['id']
    assert (row['park_id'], row['park_name']) == (CONVERSATION['park']['id'], 'Global')
    assert (row['channel_type'], row['channel_provider'], row['channel_phone']) == (
        'whatsapp', 'waba_direct', '77000000000')
    assert (row['contact_phone'], row['contact_name'], row['contact_external_id']) == (
        '77000000001', 'Тестовый Водитель', '77000000001')
    assert (row['assigned_kind'], row['assigned_user_name']) == ('user', 'Тестовый Оператор')
    assert row['message_count'] == 14
    assert row['last_inbound_at'] == datetime(2025, 11, 17, 9, 39, 51, tzinfo=timezone.utc)
    assert row['closed_at'] is None and row['closed_reason'] is None
    assert row['handoff']['reason'] == 'complaint'
    assert row['snapshot_at'] == snapshot
    assert row['extra'] == {'slaDueAt': '2025-11-17T10:00:00Z', 'channel': {'isActive': True}}
    assert set(row) == set(store._CONVERSATION_COLUMNS)


def test_conversation_row_ai_owner_and_no_contact_phone():
    conversation = dict(CONVERSATION, assignedTo={'kind': 'ai'}, handoff=None,
                        contact={'id': 'x', 'phone': None, 'name': None, 'externalId': 'olx-42'})
    row = rules.conversation_row(conversation, None)
    assert (row['assigned_kind'], row['assigned_user_id'], row['assigned_user_name']) == ('ai', None, None)
    assert row['contact_phone'] is None and row['contact_external_id'] == 'olx-42'
    assert row['handoff'] is None and row['extra'] is None
    assert rules.conversation_row({'park': {}}, None) is None
    assert rules.conversation_row(None, None) is None


def test_message_row_status_milestone_and_content():
    content_at = datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)
    row = rules.message_row(MESSAGE, content_at, rules.VIA_WEBHOOK)
    assert row['id'] == MESSAGE['id'] and row['conversation_id'] == CONVERSATION['id']
    assert (row['direction'], row['sender_type'], row['origin'], row['source']) == (
        'outbound', 'human', 'crm_operator', 'inbox')
    assert (row['author_id'], row['author_name']) == (MESSAGE['author']['id'], 'Тестовый Оператор')
    assert row['media']['fileName'] == 'sample.jpg'
    assert (row['status'], row['status_rank']) == ('read', 3)
    assert row['read_at'] == datetime(2025, 11, 17, 9, 41, 10, tzinfo=timezone.utc)
    assert row['sent_at'] is None and row['delivered_at'] is None and row['failed_at'] is None
    assert row['content_at'] == content_at and row['first_seen_via'] == 'webhook'
    assert row['extra'] is None
    assert set(row) == set(store._MESSAGE_COLUMNS)


def test_message_row_inbound_voice_keeps_empty_text_and_unknown_fields():
    message = dict(MESSAGE, direction='inbound', senderType='contact', origin='contact',
                   author=None, contentType='audio', text='', media=None, status='received',
                   source=None, transcript={'lang': 'kk'})
    row = rules.message_row(message, None, rules.VIA_WEBHOOK)
    assert row['text'] == '' and row['author_id'] is None and row['media'] is None
    assert (row['status'], row['status_rank']) == ('received', 0)
    assert all(row[name] is None for name in ('sent_at', 'delivered_at', 'read_at', 'failed_at'))
    assert row['extra'] == {'transcript': {'lang': 'kk'}}
    assert rules.message_row({'text': 'без id'}, None, 'webhook') is None


def test_status_event_row_failed_with_provider_error():
    payload = status_event('failed', error={'code': 131047, 'message': 'Re-engagement message'})
    occurred = rules.parse_time(payload['occurredAt'])
    row = rules.status_event_row(payload['data'], occurred)
    assert (row['id'], row['conversation_id']) == (MESSAGE['id'], CONVERSATION['id'])
    assert (row['status'], row['status_rank']) == ('failed', 4)
    assert (row['error_code'], row['error_message']) == ('131047', 'Re-engagement message')
    assert row['failed_at'] == occurred and row['first_seen_via'] == 'status'
    no_moment = dict(payload['data'], statusAt=None, status='delivered')
    assert rules.status_event_row(no_moment, occurred)['delivered_at'] == occurred
    assert set(row) == set(store._STUB_COLUMNS)
    assert rules.status_event_row({'status': 'read'}, occurred) is None


def test_unknown_status_value_has_zero_rank():
    fields = rules.status_fields('played', None)
    assert (fields['status'], fields['status_rank']) == ('played', 0)
    assert rules.status_fields(None, None)['status_rank'] == 0


def test_nul_characters_are_removed_everywhere():
    message = dict(MESSAGE, text='при\x00вет', media={'fileName': 'a\x00.jpg'},
                   author={'id': 'a', 'fullName': 'Имя\x00'})
    row = rules.message_row(message, None, rules.VIA_WEBHOOK)
    assert row['text'] == 'привет' and row['media'] == {'fileName': 'a.jpg'}
    assert row['author_name'] == 'Имя'
    assert rules.clean({'k\x00': ['x\x00', 1, None]}) == {'k': ['x', 1, None]}


def test_lone_surrogates_become_replacement_char_and_pairs_survive():
    parsed = json.loads('{"t": "a\\ud83d b \\ud83d\\ude95 \\udc00"}')['t']
    assert rules.clean(parsed) == 'a� b 🚕 �'
    rules.clean(parsed).encode('utf-8')          # драйвер сможет записать
    assert rules.clean('обычный текст 🚕') == 'обычный текст 🚕'


@pytest.mark.parametrize('value, expected', [
    (14, 14), ('15', 15), (2 ** 31 - 1, 2 ** 31 - 1), (2 ** 31, None), (10 ** 30, None),
    (True, None), (None, None), ('много', None), (float('inf'), None),
])
def test_counters_fit_postgres_integer(value, expected):
    assert rules._int(value) == expected


# ── Ручка ─────────────────────────────────────────────────────────────────────

class _Cursor:
    pass


class _Db:
    def __init__(self, fail=None):
        self.fail = fail
        self.cursors = 0

    def _get_cursor(self):
        db = self

        class _Ctx:
            def __enter__(self):
                db.cursors += 1
                return _Cursor()

            def __exit__(self, *exc):
                return False
        return _Ctx()


class _PgError(Exception):
    def __init__(self, pgcode):
        super().__init__('row (77000000001, Тестовый Водитель)')
        self.pgcode = pgcode


class _Calls(list):
    """Вызовы записи: (вид, data, occurred_at); raise_ — чем запись упадёт."""
    raise_ = None


@pytest.fixture()
def calls(monkeypatch):
    seen = _Calls()

    def fake(kind):
        def apply(cursor, data, occurred_at):
            seen.append((kind, data, occurred_at))
            if seen.raise_ is not None:
                raise seen.raise_
            return 'inserted'
        return apply
    monkeypatch.setattr(store, 'apply_message_created', fake('created'))
    monkeypatch.setattr(store, 'apply_message_status', fake('status'))
    return seen


def make_client(environ=None, db=None):
    app = Flask(__name__)
    app.register_blueprint(routes.build_syntony_chats_blueprint(
        db=db or _Db(), environ={rules.ENV_SECRET: SECRET} if environ is None else environ,
        clock=lambda: NOW))
    return app.test_client()


def post(client, payload, *, secret=SECRET, ts=NOW, raw=None):
    body = raw if raw is not None else body_of(payload)
    return client.post(URL, data=body, headers=signed(body, secret=secret, ts=ts))


def test_get_says_alive_without_secrets():
    response = make_client(environ={}).get(URL)
    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'service': 'syntony-chats-webhook', 'configured': False}
    assert make_client().get(URL).get_json()['configured'] is True
    assert SECRET not in make_client().get(URL).get_data(as_text=True)


def test_without_secret_answers_503_so_syntony_retries(calls):
    response = post(make_client(environ={}), created_event())
    assert response.status_code == 503
    assert response.get_json() == {'ok': False, 'error': 'not_configured'}
    assert calls == []


@pytest.mark.parametrize('kwargs, code', [
    ({'secret': 'other-synthetic-secret'}, 'bad_signature'),
    ({'ts': NOW - 600}, 'stale_timestamp'),
])
def test_foreign_or_stale_request_is_refused(calls, kwargs, code):
    response = post(make_client(), created_event(), **kwargs)
    assert response.status_code == 403
    assert response.get_json() == {'ok': False, 'error': code}
    assert calls == []


def test_unsigned_request_is_refused(calls):
    response = make_client().post(URL, data=body_of(created_event()),
                                  headers={'Content-Type': 'application/json'})
    assert response.status_code == 403
    assert response.get_json()['error'] == 'missing_signature'
    assert calls == []


def test_test_event_is_accepted_without_writing(calls):
    db = _Db()
    response = post(make_client(db=db), {'event': 'webhook.test', 'eventId': 'evt_test',
                                          'occurredAt': '2025-11-17T10:00:00Z', 'data': {}})
    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'event': 'webhook.test'}
    assert calls == [] and db.cursors == 0


def test_message_created_is_written(calls):
    response = post(make_client(), created_event())
    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'event': 'message.created'}
    [(kind, data, occurred_at)] = calls
    assert kind == 'created' and data['message']['id'] == MESSAGE['id']
    assert occurred_at == datetime(2025, 11, 17, 9, 40, 2, tzinfo=timezone.utc)


def test_message_status_is_written_and_missing_time_falls_back_to_now(calls):
    payload = status_event('delivered')
    payload['occurredAt'] = None
    response = post(make_client(), payload)
    assert response.status_code == 200
    [(kind, data, occurred_at)] = calls
    assert kind == 'status' and data['status'] == 'delivered'
    assert occurred_at == datetime.fromtimestamp(NOW, tz=timezone.utc)


def test_unknown_event_is_accepted_and_ignored(calls):
    response = post(make_client(), {'event': 'conversation.closed', 'eventId': 'evt_x', 'data': {}})
    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'event': 'conversation.closed', 'ignored': True}
    assert calls == []


def test_signed_garbage_is_a_400(calls):
    response = post(make_client(), None, raw=b'{"event": ')
    assert response.status_code == 400
    assert response.get_json()['error'] == 'bad_json'


def test_too_large_body_is_refused_before_reading(calls):
    client = make_client()
    body = b'{"x": "' + b'a' * (rules.MAX_BODY_BYTES + 1) + b'"}'
    response = client.post(URL, data=body, headers=signed(body))
    assert response.status_code == 413
    assert calls == []


@pytest.mark.parametrize('error, status, code', [
    (rules.PayloadError('no_message'), 400, 'no_message'),
    (_PgError('22P05'), 422, 'unprocessable'),      # недопустимый символ — повтор не поможет
    (_PgError('23505'), 422, 'unprocessable'),
    (_PgError('40P01'), 503, 'storage_unavailable'),  # взаимная блокировка — повторить
    (_PgError('42P01'), 503, 'storage_unavailable'),  # таблицы нет — схема не развернулась
    (_PgError(None), 503, 'storage_unavailable'),     # обрыв соединения: pgcode пустой
    (ConnectionError('db down'), 503, 'storage_unavailable'),
    # psycopg2 бросает до базы: «A string literal cannot contain NUL», не та кодировка.
    (ValueError('A string literal cannot contain NUL (0x00) characters.'), 422, 'unprocessable'),
    (UnicodeEncodeError('utf-8', '\ud83d', 0, 1, 'surrogates not allowed'), 422, 'unprocessable'),
])
def test_write_failures_choose_retry_or_not(calls, caplog, error, status, code):
    calls.raise_ = error
    with caplog.at_level('INFO'):
        response = post(make_client(), created_event())
    assert response.status_code == status
    assert response.get_json() == {'ok': False, 'error': code}
    # Текст ошибки драйвера несёт значения строки — в журнал не попадает.
    assert '77000000001' not in caplog.text and 'Тестовый' not in caplog.text


def test_journal_keeps_no_personal_data(calls, caplog):
    with caplog.at_level('INFO'):
        post(make_client(), created_event())
        post(make_client(), status_event('failed', error={'code': 131047, 'message': 'x'}))
        post(make_client(), created_event(), secret='other-synthetic-secret')
    for value in ('77000000001', 'Тестовый', 'техпаспорта', SECRET):
        assert value not in caplog.text


def test_repeated_refusals_are_logged_once(calls, caplog):
    client = make_client(environ={})
    with caplog.at_level('WARNING'):
        for _ in range(5):
            assert post(client, created_event()).status_code == 503
    assert caplog.text.count('секрета подписи нет') == 1


# ── Проводка ──────────────────────────────────────────────────────────────────

def test_wiring_in_the_monolith_and_the_schema():
    monolith = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
    schema_src = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
    assert 'app.register_blueprint(build_syntony_chats_blueprint(db=db))' in monolith
    assert 'self._init_syntony_chats_schema_tx(cursor)' in schema_src
    method = schema_src[schema_src.index('def _init_syntony_chats_schema_tx(self, cursor):'):]
    method = method[:method.index('\n    def ')]
    assert 'SAVEPOINT syntony_chats_schema' in method
    assert 'from syntony_chats.schema import init_syntony_chats_schema' in method
    assert 'ROLLBACK TO SAVEPOINT syntony_chats_schema' in method


def test_blueprint_paths():
    app = Flask(__name__)
    app.register_blueprint(routes.build_syntony_chats_blueprint(db=_Db(), environ={}))
    methods = set()
    for rule in app.url_map.iter_rules():
        if rule.rule == URL:
            methods |= rule.methods
    assert {'GET', 'POST'} <= methods
