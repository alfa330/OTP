"""Данные водителя из CRM на настоящем PostgreSQL: кэш ответа, «не найден», сбой
CRM, справочник парков и права ручек карточки (wazzup/driver_info.py).

Как у карточек: только явно выбранный локальный кластер (WAZZUP_PILOT_TEST_PORT),
своя схема на тест. CRM — заглушки, в сеть ничего не уходит."""
from unittest.mock import Mock

import pytest
from flask import Flask

from parcels import drivers as crm
from tests.test_wazzup_driver_cards_persistence import (  # noqa: F401  (pg — фикстура)
    CARD_PATH, CHAT, DRIVER, GLOBAL, OPTIONS, OTHER_CHAT, OTHER_DRIVER, PORT, Database, card_body, pg)
from tests.test_wazzup_driver_info import DICTIONARY, DRIVER_DATA, park_id
from tests.test_wazzup_pilot import CHANNEL
from wazzup import driver_info as info
from wazzup import pilot
from wazzup.realtime import EventBroker

pytestmark = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')

DRIVER_PATH = CARD_PATH + '/driver'
LOOKUP_PATH = CARD_PATH + '/lookup'


class Crm:
    """Заглушка CRM: ответы по ID, счётчик походов."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls = []

    def __call__(self, driver_id):
        self.calls.append(driver_id)
        answer = self.answers.get(driver_id, crm.DriverLookupError('Водитель с таким ID не найден',
                                                                   code='driver_not_found', status=404))
        if isinstance(answer, Exception):
            raise answer
        return answer


def found(driver_id=DRIVER, **updates):
    return dict(DRIVER_DATA, account_id=driver_id, **updates)


def age(connection, driver_id, seconds):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_driver_info SET fetched_at = now() - make_interval(secs => %s) "
                       "WHERE driver_id = %s", (seconds, driver_id))
    connection.commit()


def test_a_found_driver_is_cached_then_asked_again_once_stale(pg):
    connection, _ = pg
    database = Database(connection)
    fake = Crm({DRIVER: found()})
    first = info.lookup(database, DRIVER, fetch=fake)
    again = info.lookup(database, DRIVER, fetch=fake)
    assert first[0] == again[0] == 'found' and again[1]['park']['name'] == 'Jana' and fake.calls == [DRIVER]
    age(connection, DRIVER, info.FOUND_TTL + 1)
    assert info.lookup(database, DRIVER, fetch=fake)[0] == 'found' and fake.calls == [DRIVER, DRIVER]


def test_not_found_is_remembered_only_briefly_and_a_new_registration_shows_up(pg):
    connection, _ = pg
    database = Database(connection)
    fake = Crm()
    assert info.lookup(database, DRIVER, fetch=fake)[0] == 'not_found'
    assert info.lookup(database, DRIVER, fetch=fake)[0] == 'not_found' and len(fake.calls) == 1
    fake.answers[DRIVER] = found()
    age(connection, DRIVER, info.MISSING_TTL + 1)
    assert info.lookup(database, DRIVER, fetch=fake)[0] == 'found' and len(fake.calls) == 2


def test_a_crm_outage_shows_the_last_snapshot_marked_stale_or_says_unavailable(pg):
    connection, _ = pg
    database = Database(connection)
    down = crm.DriverLookupError('CRM не ответила — попробуйте ещё раз', code='crm_unavailable')
    assert info.lookup(database, DRIVER, fetch=Crm({DRIVER: down})) == (
        'unavailable', None, None, False, 'CRM не ответила — попробуйте ещё раз')
    info.lookup(database, DRIVER, fetch=Crm({DRIVER: found()}))
    age(connection, DRIVER, info.FOUND_TTL + 1)
    state, data, fetched_at, stale, error = info.lookup(database, DRIVER, fetch=Crm({DRIVER: down}))
    assert (state, stale, error) == ('found', True, 'CRM не ответила — попробуйте ещё раз')
    assert data['account_id'] == DRIVER and fetched_at is not None


def test_the_dictionary_is_kept_in_the_base_and_its_outage_pauses_new_attempts(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    monkeypatch.setattr(info, '_dictionary_next_try', [0.0])
    fetch = Mock(return_value=DICTIONARY)
    assert info.current_dictionary(database, fetch=fetch) == DICTIONARY
    assert info.current_dictionary(database, fetch=fetch) == DICTIONARY and fetch.call_count == 1
    with connection.cursor() as cursor:
        cursor.execute("UPDATE wazzup_crm_dictionaries SET fetched_at = now() - interval '7 hours'")
    connection.commit()
    broken = Mock(side_effect=RuntimeError('CRM down'))
    assert info.current_dictionary(database, fetch=broken) == DICTIONARY   # старый снимок, а не ничего
    assert info.current_dictionary(database, fetch=broken) == DICTIONARY and broken.call_count == 1


def test_snapshots_of_drivers_are_kept_thirty_days(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    monkeypatch.setattr(info, '_dictionary_next_try', [0.0])
    info.lookup(database, DRIVER, fetch=Crm({DRIVER: found()}))
    info.lookup(database, OTHER_DRIVER, fetch=Crm({OTHER_DRIVER: found(OTHER_DRIVER)}))
    age(connection, OTHER_DRIVER, 31 * 86400)
    info.current_dictionary(database, fetch=Mock(return_value=DICTIONARY))
    with connection.cursor() as cursor:
        cursor.execute('SELECT driver_id FROM wazzup_driver_info')
        assert cursor.fetchall() == [(DRIVER,)]
    connection.commit()


def test_describe_gives_the_screen_and_what_to_fill(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    monkeypatch.setattr(info, '_dictionary_next_try', [0.0])
    payload = info.describe(database, DRIVER, OPTIONS, fetch=Crm({DRIVER: found()}),
                            dictionary_fetch=Mock(return_value=DICTIONARY))
    assert payload['state'] == 'found' and payload['stale'] is False and payload['fetchedAt']
    assert payload['suggestion'] == {'park': {'id': 1667515, 'name': 'Jana Taxi'},
                                     'city': {'id': 1610837, 'name': 'Алматы'}, 'crmPark': 'Jana',
                                     'crmCity': 'Алматы'}
    assert payload['info']['name'] == 'Тестов Тест Тестович'
    missing = info.describe(database, OTHER_DRIVER, OPTIONS, fetch=Crm())
    assert missing['state'] == 'not_found' and 'info' not in missing and 'suggestion' not in missing


def app_for(database, *, can_process=True, crm_answers=None):
    fake = Crm(crm_answers if crm_answers is not None else {DRIVER: found(), OTHER_DRIVER: found(OTHER_DRIVER)})
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(pilot.build_pilot_blueprint(
        db=database, require_api_key=lambda fn: fn, guard=lambda: (42, None),
        channels=lambda account: [], preflight=lambda: ('', 204), listen_connect=lambda: None,
        transport=Mock(), event_broker=EventBroker(),
        access=lambda: {'mode': 'full', 'can_process': can_process},
        driver_card_sync={'wake': Mock(), 'options': lambda: OPTIONS,
                          'driver_info': lambda driver_id, options: info.describe(
                              database, driver_id, options, fetch=fake, dictionary_fetch=lambda: DICTIONARY)}))
    return app.test_client(), fake


def test_viewers_get_the_driver_of_the_chat_card_but_cannot_look_up_any_id(pg, monkeypatch):
    connection, _ = pg
    monkeypatch.setattr(info, '_dictionary_next_try', [0.0])
    writer, _ = app_for(Database(connection))
    assert writer.post(CARD_PATH, json=card_body()).status_code == 201
    viewer, fake = app_for(Database(connection), can_process=False)
    query = {'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT}
    response = viewer.get(DRIVER_PATH, query_string=query)
    assert response.status_code == 200, response.json
    assert response.json['state'] == 'found' and response.json['info']['park'] == 'Jana'
    assert response.headers['Cache-Control'] == 'private, no-store'
    # ID берётся из карточки чата: подставить чужой в запрос нельзя.
    assert viewer.get(DRIVER_PATH, query_string=dict(query, driverId=OTHER_DRIVER)).json['driverId'] == DRIVER
    assert fake.calls == [DRIVER]
    refused = viewer.post(LOOKUP_PATH, json={'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT,
                                             'driverId': OTHER_DRIVER})
    assert refused.status_code == 403 and fake.calls == [DRIVER]


def test_a_chat_without_a_card_id_has_no_driver_and_global_has_no_card(pg):
    connection, _ = pg
    client, fake = app_for(Database(connection))
    query = {'account': 'op', 'channelId': CHANNEL, 'chatId': OTHER_CHAT}
    assert client.get(DRIVER_PATH, query_string=query).json['code'] == 'NO_DRIVER_ID'
    assert client.post(CARD_PATH, json=card_body(chatId=OTHER_CHAT, driverId='')).status_code == 201
    response = client.get(DRIVER_PATH, query_string=query)
    assert response.status_code == 404 and response.json['code'] == 'NO_DRIVER_ID'
    assert client.get(DRIVER_PATH, query_string=dict(query, channelId=GLOBAL)).status_code == 403
    assert fake.calls == []


def test_processors_look_up_a_pasted_link_and_get_the_park_and_city_to_fill(pg, monkeypatch):
    connection, _ = pg
    monkeypatch.setattr(info, '_dictionary_next_try', [0.0])
    client, fake = app_for(Database(connection))
    body = {'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT,
            'driverId': 'https://fleet.yandex.kz/contractors/%s/details?park_id=%s' % (DRIVER.upper(), park_id(1))}
    response = client.post(LOOKUP_PATH, json=body)
    assert response.status_code == 200, response.json
    assert response.json['driverId'] == DRIVER and fake.calls == [DRIVER]
    assert response.json['suggestion']['park'] == {'id': 1667515, 'name': 'Jana Taxi'}
    assert response.json['suggestion']['city'] == {'id': 1610837, 'name': 'Алматы'}
    unknown = client.post(LOOKUP_PATH, json=dict(body, driverId='f' * 32))
    assert unknown.status_code == 200 and unknown.json['state'] == 'not_found'
    for bad, needle in (('77000000000', 'ID водителя — 32 символа'), ('', 'Укажите ID водителя')):
        refused = client.post(LOOKUP_PATH, json=dict(body, driverId=bad))
        assert refused.status_code == 400 and needle in refused.json['error'] and refused.json['field'] == 'driverId'
    assert client.post(LOOKUP_PATH, json=dict(body, channelId=GLOBAL)).status_code == 403


def test_a_broken_crm_answer_never_breaks_the_card(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    app = Flask(__name__)
    app.testing = True

    def broken(driver_id, options):
        raise RuntimeError('unexpected')

    app.register_blueprint(pilot.build_pilot_blueprint(
        db=database, require_api_key=lambda fn: fn, guard=lambda: (42, None),
        channels=lambda account: [], preflight=lambda: ('', 204), listen_connect=lambda: None,
        transport=Mock(), event_broker=EventBroker(), access=lambda: {'mode': 'full', 'can_process': True},
        driver_card_sync={'wake': Mock(), 'options': lambda: OPTIONS, 'driver_info': broken}))
    client = app.test_client()
    response = client.post(LOOKUP_PATH, json={'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT,
                                              'driverId': DRIVER})
    assert response.status_code == 200
    assert response.json == {'driverId': DRIVER, 'state': 'unavailable', 'stale': False, 'fetchedAt': None,
                             'error': 'Данные водителя из CRM сейчас недоступны'}
