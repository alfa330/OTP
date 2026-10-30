"""Маршруты пересылки вебхуков Wazzup: Global → Syntony, +77070253322 «Help Taxi» →
CRM группы «Поток» (владелец, 20.10.2026: «пересылку вебхуков с канала +77070253322
как для глобал»).

Без приложения, боевой базы и внешнего HTTP. Проверки с SQL — только на одноразовом
локальном Postgres (WAZZUP_PILOT_TEST_PORT), как у остальных тестов пересылки.
"""
import hashlib
import json
from unittest.mock import Mock

import pytest

from tests.test_wazzup_pilot_persistence import PORT, pg  # noqa: F401
from tests.test_wazzup_syntony import GLOBAL, OTHER, message
from tests.test_wazzup_syntony_persistence import Database, response, rows
from wazzup import syntony, syntony_schema

HELP_TAXI = '6983db13-ae8e-4b27-b09d-6c10ae4f54cf'
DEPARTMENT = '33bc674a-727f-4e2d-b350-da1832ecfdd5'
CRM_URL = 'https://crm.example.test/icore/wazzup'
SYNTONY_URL = 'https://relay.example.test/wazzup'
ENV_NAMES = [f'{prefix}_{suffix}'
             for prefix in ('SYNTONY_WEBHOOK', 'SYNTONY_DEPARTMENT_WEBHOOK', 'POTOK_CRM_WEBHOOK')
             for suffix in ('ENABLED', 'API_URL', 'API_KEY', 'AUTH_HEADER', 'AUTH_PREFIX')]
needs_pg = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def enable_crm(monkeypatch):
    monkeypatch.setenv('POTOK_CRM_WEBHOOK_ENABLED', 'true')
    monkeypatch.setenv('POTOK_CRM_WEBHOOK_API_URL', CRM_URL)
    monkeypatch.setenv('POTOK_CRM_WEBHOOK_API_KEY', 'synthetic-crm-secret')


def enable_syntony(monkeypatch):
    monkeypatch.setenv('SYNTONY_WEBHOOK_ENABLED', 'true')
    monkeypatch.setenv('SYNTONY_WEBHOOK_API_URL', SYNTONY_URL)
    monkeypatch.setenv('SYNTONY_WEBHOOK_API_KEY', 'synthetic-syntony-secret')


def enable_department(monkeypatch):
    # Тот же приёмник Syntony, но ключ подключения «Департамента».
    monkeypatch.setenv('SYNTONY_DEPARTMENT_WEBHOOK_ENABLED', 'true')
    monkeypatch.setenv('SYNTONY_DEPARTMENT_WEBHOOK_API_URL', SYNTONY_URL)
    monkeypatch.setenv('SYNTONY_DEPARTMENT_WEBHOOK_API_KEY', 'synthetic-department-secret')


def help_taxi(message_id, **changes):
    return message(message_id, HELP_TAXI, **changes)


def test_help_taxi_belongs_to_the_crm_route_only():
    assert syntony.POTOK_CRM.channels == {HELP_TAXI}
    assert not syntony.POTOK_CRM.channels & syntony.GLOBAL_CHANNEL_IDS
    assert [route.name for route in syntony.ROUTES] == ['syntony', 'syntony_department', 'potok_crm']


def test_department_has_its_own_switch_and_key(clean_env):
    enable_syntony(clean_env)
    # Настройки Global подключение «Департамента» не открывают.
    assert syntony.load_config(syntony.SYNTONY_DEPARTMENT) is None
    enable_department(clean_env)
    department = syntony.load_config(syntony.SYNTONY_DEPARTMENT)
    assert (department.url, department.key, department.auth_prefix) == (
        SYNTONY_URL, 'synthetic-department-secret', 'Bearer')
    assert syntony.load_config().key == 'synthetic-syntony-secret'
    assert set(syntony.enabled_routes()) == {'syntony', 'syntony_department'}


def test_each_route_has_its_own_switch_and_credentials(clean_env):
    enable_syntony(clean_env)
    # Настройки Syntony не открывают маршрут CRM, и наоборот.
    assert syntony.load_config(syntony.POTOK_CRM) is None
    assert set(syntony.enabled_routes()) == {'syntony'}
    enable_crm(clean_env)
    crm = syntony.load_config(syntony.POTOK_CRM)
    assert (crm.url, crm.key) == (CRM_URL, 'synthetic-crm-secret')
    assert syntony.load_config().key == 'synthetic-syntony-secret'
    clean_env.setenv('SYNTONY_WEBHOOK_ENABLED', 'false')
    assert set(syntony.enabled_routes()) == {'potok_crm'}
    clean_env.setenv('POTOK_CRM_WEBHOOK_API_URL', 'http://crm.example.test/plain-http')
    assert syntony.enabled_routes() == {}


def test_crm_route_takes_only_help_taxi_events_and_global_never_takes_them():
    help_in, help_out = help_taxi('help-in'), help_taxi('help-out', isEcho=True)
    payload = {'messages': [message('global-in'), help_in, message('private', OTHER), help_out],
               'statuses': [{'messageId': 'help-out', 'status': 'read'},
                            {'messageId': 'global-in', 'status': 'read'}]}
    ready, pending, complete = syntony.filter_payload(payload, channels=syntony.POTOK_CRM.channels)
    assert ready == {'messages': [help_in, help_out],
                     'statuses': [{'messageId': 'help-out', 'status': 'read'}]}
    assert not pending and not complete
    global_ready, _, _ = syntony.filter_payload(payload)
    assert 'help-' not in json.dumps(global_ready)


def test_whole_help_taxi_payload_keeps_its_envelope():
    payload = {'messages': [help_taxi('help-in')], 'statuses': []}
    ready, pending, complete = syntony.filter_payload(payload, channels=syntony.POTOK_CRM.channels)
    assert ready == payload and not pending and complete


def test_syntony_digest_is_unchanged_and_routes_never_share_one():
    payload = {'messages': [message()]}
    before_routes = hashlib.sha256(b'op:ready:' + syntony._dump(payload)).hexdigest()
    assert syntony._fingerprint('ready', payload) == before_routes
    assert syntony._fingerprint('ready', payload, syntony.POTOK_CRM) != before_routes
    assert (syntony._fingerprint('receipt', payload, syntony.POTOK_CRM)
            != syntony._fingerprint('receipt', payload))


def test_without_enabled_routes_nothing_is_queued(clean_env):
    db = Mock()
    assert syntony.enqueue(db, {'messages': [help_taxi('help-in')]}) == 0
    db._get_cursor.assert_not_called()


# ── Очередь на настоящем Postgres ───────────────────────────────────────────

@pytest.fixture
def relay_db(pg, clean_env):
    connection, cursor, _, _ = pg
    syntony_schema.init_schema(cursor)
    connection.commit()
    clean_env.setattr(syntony, 'start_worker', lambda *_args, **_kwargs: None)
    return Database(connection)


def deliver_all(db, sent):
    def send(url, **kwargs):
        assert not db.in_transaction, 'HTTP must not hold a database transaction'
        sent.append((url, kwargs))
        return response()
    for _ in range(10):
        if not syntony.process_once(db, transport=send):
            break
    return {url: (json.loads(kwargs['data']), kwargs['headers']) for url, kwargs in sent}


def routes_of(db):
    with db._get_cursor() as cursor:
        cursor.execute("SELECT route, phase FROM wazzup_syntony_outbox WHERE phase <> 'receipt' ORDER BY id")
        return cursor.fetchall()


@needs_pg
def test_schema_upgrade_keeps_old_rows_as_syntony(relay_db):
    with relay_db._get_cursor() as cursor:
        cursor.execute('ALTER TABLE wazzup_syntony_outbox DROP COLUMN route')
        cursor.execute("INSERT INTO wazzup_syntony_outbox (fingerprint, account, phase) "
                       "VALUES ('before-routes', 'op', 'done')")
        syntony_schema.init_schema(cursor)
        syntony_schema.init_schema(cursor)
        cursor.execute("SELECT route FROM wazzup_syntony_outbox WHERE fingerprint='before-routes'")
        assert cursor.fetchone() == ('syntony',)


@needs_pg
def test_mixed_webhook_goes_to_each_receiver_with_its_own_key(relay_db, clean_env):
    enable_syntony(clean_env)
    enable_crm(clean_env)
    help_in, global_in = help_taxi('help-in', text='Здравствуйте'), message('global-in')
    payload = {'messages': [global_in, help_in, message('private', OTHER)]}
    assert syntony.enqueue(relay_db, payload) == 2
    assert sorted(routes_of(relay_db)) == [('potok_crm', 'ready'), ('syntony', 'ready')]
    sent = []
    delivered = deliver_all(relay_db, sent)
    assert len(sent) == 2
    assert delivered[CRM_URL][0] == {'messages': [help_in]}
    assert delivered[CRM_URL][1]['Authorization'] == 'Bearer synthetic-crm-secret'
    assert delivered[SYNTONY_URL][0] == {'messages': [global_in]}
    assert delivered[SYNTONY_URL][1]['Authorization'] == 'Bearer synthetic-syntony-secret'
    assert 'synthetic-syntony-secret' not in json.dumps(delivered[CRM_URL][1])
    assert 'private' not in json.dumps([body for body, _ in delivered.values()])
    # Повтор того же вебхука от Wazzup — ни одной новой доставки.
    assert syntony.enqueue(relay_db, payload) == 0
    deliver_all(relay_db, sent)
    assert len(sent) == 2


@needs_pg
def test_department_and_global_reach_syntony_each_with_its_own_key(relay_db, clean_env):
    enable_syntony(clean_env)
    enable_department(clean_env)
    department_in, global_in = message('department-in', DEPARTMENT), message('global-in')
    with relay_db._get_cursor() as cursor:
        cursor.execute("INSERT INTO wazzup_messages (message_id, channel_id, chat_id, dt, is_echo, account) "
                       "VALUES ('department-out', %s, '70000000000', now(), TRUE, 'op')", (DEPARTMENT,))
    payload = {'messages': [global_in, department_in],
               'statuses': [{'messageId': 'department-out', 'status': 'read'}]}
    assert syntony.enqueue(relay_db, payload) == 2
    assert sorted(routes_of(relay_db)) == [('syntony', 'ready'), ('syntony_department', 'ready')]
    sent = []
    deliver_all(relay_db, sent)
    assert {url for url, _ in sent} == {SYNTONY_URL}
    by_key = {kwargs['headers']['Authorization']: json.loads(kwargs['data']) for _, kwargs in sent}
    assert by_key == {
        'Bearer synthetic-syntony-secret': {'messages': [global_in]},
        'Bearer synthetic-department-secret': {'messages': [department_in],
                                               'statuses': payload['statuses']},
    }


@needs_pg
def test_crm_gets_original_bytes_of_a_pure_help_taxi_webhook(relay_db, clean_env):
    enable_crm(clean_env)
    payload = {'messages': [help_taxi('help-in', text='Тест')]}
    raw = ('  ' + json.dumps(payload, ensure_ascii=False, indent=2)).encode('utf-8')
    assert syntony.enqueue(relay_db, payload, raw_body=raw) == 1
    sent = []
    deliver_all(relay_db, sent)
    assert [url for url, _ in sent] == [CRM_URL]
    assert sent[0][1]['data'] == raw
    assert sent[0][1]['headers']['Idempotency-Key']


@needs_pg
def test_status_of_a_help_taxi_message_follows_its_channel(relay_db, clean_env):
    enable_syntony(clean_env)
    enable_crm(clean_env)
    with relay_db._get_cursor() as cursor:
        cursor.executemany("INSERT INTO wazzup_messages (message_id, channel_id, chat_id, dt, is_echo, account) "
                           "VALUES (%s, %s, '70000000000', now(), TRUE, 'op')",
                           [('help-out', HELP_TAXI), ('global-out', GLOBAL), ('private-out', OTHER)])
    payload = {'statuses': [{'messageId': 'help-out', 'status': 'read'},
                            {'messageId': 'global-out', 'status': 'delivered'},
                            {'messageId': 'private-out', 'status': 'read'}]}
    syntony.enqueue(relay_db, payload)
    delivered = deliver_all(relay_db, [])
    assert delivered[CRM_URL][0] == {'statuses': payload['statuses'][:1]}
    assert delivered[SYNTONY_URL][0] == {'statuses': payload['statuses'][1:2]}


@needs_pg
def test_disabled_crm_route_neither_queues_nor_blocks_syntony(relay_db, clean_env):
    enable_syntony(clean_env)
    payload = {'messages': [help_taxi('help-in'), message('global-in')]}
    assert syntony.enqueue(relay_db, payload) == 1
    assert routes_of(relay_db) == [('syntony', 'ready')]
    delivered = deliver_all(relay_db, [])
    assert list(delivered) == [SYNTONY_URL]


@needs_pg
def test_switched_off_crm_keeps_its_queue_until_switched_on(relay_db, clean_env):
    enable_crm(clean_env)
    syntony.enqueue(relay_db, {'messages': [help_taxi('help-in')]})
    clean_env.setenv('POTOK_CRM_WEBHOOK_ENABLED', 'false')
    enable_syntony(clean_env)
    send = Mock(side_effect=AssertionError('a switched-off route must not deliver'))
    assert not syntony.process_once(relay_db, transport=send)
    send.assert_not_called()
    assert [phase for phase, *_ in rows(relay_db)].count('ready') == 1
    clean_env.setenv('POTOK_CRM_WEBHOOK_ENABLED', 'true')
    assert list(deliver_all(relay_db, [])) == [CRM_URL]
