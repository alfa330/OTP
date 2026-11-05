"""Маршруты пересылки вебхуков Wazzup: Global → Syntony, +77070253322 «Help Taxi» →
CRM группы «Поток» (владелец, 20.10.2026: «пересылку вебхуков с канала +77070253322
как для глобал»); «Департамент», iTaxi и Salam Taxi → Syntony, каждый своим ключом.

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
ITAXI = '771c4257-4f8f-45fb-8c6c-08b61ea7e7f5'
SALAM = '34da6a88-2b9c-4c5a-be8d-072e89bfd440'
CRM_URL = 'https://crm.example.test/icore/wazzup'
SYNTONY_URL = 'https://relay.example.test/wazzup'
ENV_NAMES = [f'{route.env}_{suffix}' for route in syntony.ROUTES
             for suffix in ('ENABLED', 'API_URL', 'API_KEY', 'AUTH_HEADER', 'AUTH_PREFIX')]
# Подключения номеров в Syntony: тот же приёмник, свой ключ у каждого.
PARKS = [(syntony.SYNTONY_DEPARTMENT, 'synthetic-department-secret'),
         (syntony.SYNTONY_ITAXI, 'synthetic-itaxi-secret'),
         (syntony.SYNTONY_SALAM, 'synthetic-salam-secret')] + [
    (route, f'synthetic-{route.name}-secret') for route in syntony.SYNTONY_NUMBER_ROUTES]
# Второй номер того же парка (второй список Syntony, 04.11.2026).
GLOBAL_1230 = 'b88e8d3a-f3f4-4854-bbc8-076a0924d118'
ITAXI_0880 = 'ee3372c2-f15f-4ace-8176-dba1a3838bb3'
DEPARTMENT_7692 = 'e28a8bf4-49e4-46c5-a915-23123439a4fc'
JANA_1372 = '24ea28f0-ba8a-4be7-a9b9-6253ff3d39ef'
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


def enable_park(monkeypatch, route, key):
    # Тот же приёмник Syntony, но ключ подключения этого парка.
    monkeypatch.setenv(route.env + '_ENABLED', 'true')
    monkeypatch.setenv(route.env + '_API_URL', SYNTONY_URL)
    monkeypatch.setenv(route.env + '_API_KEY', key)


def enable_department(monkeypatch):
    enable_park(monkeypatch, *PARKS[0])


def help_taxi(message_id, **changes):
    return message(message_id, HELP_TAXI, **changes)


def test_help_taxi_belongs_to_the_crm_route_only():
    assert syntony.POTOK_CRM.channels == {HELP_TAXI}
    assert not syntony.POTOK_CRM.channels & syntony.GLOBAL_CHANNEL_IDS
    assert [route.name for route in syntony.ROUTES] == [
        'syntony', 'syntony_department', 'syntony_itaxi', 'syntony_salam',
        'syntony_global_1230', 'syntony_itaxi_0880', 'syntony_jana_1372', 'syntony_qazaq_9671',
        'syntony_amanat_2629', 'syntony_department_7692', 'syntony_dostoyny_6032', 'syntony_nol_4569',
        'syntony_tenge_9716', 'syntony_registration_1223', 'syntony_chestny_0602', 'syntony_jana_3322',
        'potok_crm']


@pytest.mark.parametrize('route, key', PARKS, ids=[route.name for route, _ in PARKS])
def test_park_has_its_own_switch_and_key(clean_env, route, key):
    enable_syntony(clean_env)
    for other, other_key in PARKS:
        if other is not route:
            enable_park(clean_env, other, other_key)
    # Ни настройки Global, ни подключения других парков этот маршрут не открывают.
    assert syntony.load_config(route) is None
    enable_park(clean_env, route, key)
    config = syntony.load_config(route)
    assert (config.url, config.key, config.auth_header, config.auth_prefix) == (
        SYNTONY_URL, key, 'Authorization', 'Bearer')
    assert syntony.load_config().key == 'synthetic-syntony-secret'
    assert set(syntony.enabled_routes()) == {'syntony'} | {other.name for other, _ in PARKS}
    # Выключатель у каждого свой: выключенный парк не трогает соседей.
    clean_env.setenv(route.env + '_ENABLED', 'false')
    assert set(syntony.enabled_routes()) == {'syntony'} | {other.name for other, _ in PARKS
                                                           if other is not route}


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
def test_parks_reach_syntony_each_with_its_own_key_and_never_another_parks_events(relay_db, clean_env):
    # Вебхук аккаунта несёт события разных номеров вперемешку: каждый парк получает
    # только свои — сообщения, статусы (канал по messageId из базы) и состояние канала.
    enable_syntony(clean_env)
    for route, key in PARKS:
        enable_park(clean_env, route, key)
    itaxi_in, salam_in = message('itaxi-in', ITAXI, text='Сәлем'), message('salam-in', SALAM)
    itaxi_echo = message('itaxi-echo', ITAXI, isEcho=True)
    global_in, department_in = message('global-in'), message('department-in', DEPARTMENT)
    with relay_db._get_cursor() as cursor:
        cursor.executemany("INSERT INTO wazzup_messages (message_id, channel_id, chat_id, dt, is_echo, account) "
                           "VALUES (%s, %s, '70000000000', now(), TRUE, 'op')",
                           [('itaxi-out', ITAXI), ('salam-out', SALAM), ('private-out', OTHER)])
    statuses = [{'messageId': 'salam-out', 'status': 'delivered'},
                {'messageId': 'itaxi-out', 'status': 'read'},
                {'messageId': 'private-out', 'status': 'read'}]
    payload = {'messages': [itaxi_in, global_in, salam_in, message('private', OTHER),
                            department_in, itaxi_echo],
               'statuses': statuses,
               'channelsUpdates': [{'channelId': SALAM, 'state': 'active'}]}
    assert syntony.enqueue(relay_db, payload) == 4
    assert sorted(routes_of(relay_db)) == [('syntony', 'ready'), ('syntony_department', 'ready'),
                                           ('syntony_itaxi', 'ready'), ('syntony_salam', 'ready')]
    sent = []
    deliver_all(relay_db, sent)
    assert {url for url, _ in sent} == {SYNTONY_URL}
    by_key = {kwargs['headers']['Authorization']: json.loads(kwargs['data']) for _, kwargs in sent}
    assert by_key == {
        'Bearer synthetic-syntony-secret': {'messages': [global_in]},
        'Bearer synthetic-department-secret': {'messages': [department_in]},
        'Bearer synthetic-itaxi-secret': {'messages': [itaxi_in, itaxi_echo],
                                          'statuses': [statuses[1]]},
        'Bearer synthetic-salam-secret': {'messages': [salam_in], 'statuses': [statuses[0]],
                                          'channelsUpdates': payload['channelsUpdates']},
    }
    # Одна доставка на подключение и стабильный ключ повтора у каждой своей.
    assert len(sent) == 4
    assert len({kwargs['headers']['Idempotency-Key'] for _, kwargs in sent}) == 4
    # Повтор того же вебхука от Wazzup — ни одной новой доставки.
    assert syntony.enqueue(relay_db, payload) == 0
    deliver_all(relay_db, sent)
    assert len(sent) == 4


@needs_pg
def test_second_number_of_a_park_goes_with_its_own_key_and_the_first_keeps_its_own(relay_db, clean_env):
    # Syntony, 04.11.2026: «У Global, iTaxi и Департамента новый ключ — только для нового
    # номера, прежний остаётся для прежнего номера». Событие с чужим ключом они отбросят.
    enable_syntony(clean_env)
    for route, key in PARKS:
        enable_park(clean_env, route, key)
    pairs = [('global', GLOBAL, GLOBAL_1230), ('itaxi', ITAXI, ITAXI_0880),
             ('department', DEPARTMENT, DEPARTMENT_7692)]
    messages = []
    for park, first, second in pairs:
        messages += [message(f'{park}-first', first), message(f'{park}-second', second)]
    messages.append(message('jana-in', JANA_1372))
    with relay_db._get_cursor() as cursor:
        cursor.executemany("INSERT INTO wazzup_messages (message_id, channel_id, chat_id, dt, is_echo, account) "
                           "VALUES (%s, %s, '70000000000', now(), TRUE, 'op')",
                           [('global-1230-out', GLOBAL_1230), ('jana-out', JANA_1372)])
    statuses = [{'messageId': 'jana-out', 'status': 'read'},
                {'messageId': 'global-1230-out', 'status': 'delivered'}]
    assert syntony.enqueue(relay_db, {'messages': messages, 'statuses': statuses}) == 7
    sent = []
    deliver_all(relay_db, sent)
    by_key = {kwargs['headers']['Authorization']: json.loads(kwargs['data']) for _, kwargs in sent}
    by_id = {message_['messageId']: message_ for message_ in messages}
    assert by_key == {
        'Bearer synthetic-syntony-secret': {'messages': [by_id['global-first']]},
        'Bearer synthetic-syntony_global_1230-secret': {'messages': [by_id['global-second']],
                                                        'statuses': [statuses[1]]},
        'Bearer synthetic-itaxi-secret': {'messages': [by_id['itaxi-first']]},
        'Bearer synthetic-syntony_itaxi_0880-secret': {'messages': [by_id['itaxi-second']]},
        'Bearer synthetic-department-secret': {'messages': [by_id['department-first']]},
        'Bearer synthetic-syntony_department_7692-secret': {'messages': [by_id['department-second']]},
        'Bearer synthetic-syntony_jana_1372-secret': {'messages': [by_id['jana-in']],
                                                      'statuses': [statuses[0]]},
    }


@needs_pg
def test_pure_park_webhook_keeps_original_bytes(relay_db, clean_env):
    route, key = PARKS[1]
    enable_park(clean_env, route, key)
    payload = {'messages': [message('itaxi-in', ITAXI, text='Тест')]}
    raw = ('  ' + json.dumps(payload, ensure_ascii=False, indent=2)).encode('utf-8')
    assert syntony.enqueue(relay_db, payload, raw_body=raw) == 1
    sent = []
    deliver_all(relay_db, sent)
    assert [kwargs['headers']['Authorization'] for _, kwargs in sent] == ['Bearer ' + key]
    assert sent[0][1]['data'] == raw


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
