"""Профессия водителя из кабинета Fleet для карточки «Чатов ОП» (wazzup/driver_profession.py).

Кэш и справочник — на настоящем PostgreSQL, как у данных CRM (WAZZUP_PILOT_TEST_PORT,
своя схема на тест). Кабинет — заглушки: в сеть ничего не уходит, куки выдуманные."""
import json
from unittest.mock import Mock

import pytest
import requests
from flask import Flask

from driver_mailings.client import PATH_PROFESSIONS
from driver_mailings.schema import init_driver_mailings_schema
from fleet_edm import client as fleet
from fleet_edm.client import PATH_LIST, FleetAccessDenied, FleetError, FleetSessionExpired
from fleet_edm.schema import init_fleet_edm_schema
from tests.test_wazzup_driver_cards_persistence import (  # noqa: F401  (pg — фикстура)
    CARD_PATH, CHAT, DRIVER, OPTIONS, OTHER_DRIVER, PORT, Database, card_body, pg)
from tests.test_wazzup_driver_info import DICTIONARY, DRIVER_DATA, park_id
from tests.test_wazzup_pilot import CHANNEL
from wazzup import driver_info as info
from wazzup import driver_profession as profession
from wazzup import pilot
from wazzup.realtime import EventBroker

needs_pg = pytest.mark.skipif(not PORT, reason='needs isolated local PG (WAZZUP_PILOT_TEST_PORT)')

PARK = park_id(1)
OTHER_PARK = park_id(2)
MOTO = 'cargo/courier/on-motorcycle'
NAMES = {'taxi/driver': 'Водитель такси', MOTO: 'Мотокурьер', 'cargo/courier/on-foot': 'Пеший курьер'}
ABSENT = 'нет в этом сегменте списка'


class Seq(list):
    """Ответы кабинета по очереди: первый вызов — первый ответ."""


class Cabinet:
    """Заглушка кабинета с журналом вызовов. active и archived — что список исполнителей
    отвечает про водителя среди действующих и в архиве: код профессии, None (водитель
    есть, профессии нет), ABSENT, готовый ответ словарём, исключение или Seq из них;
    names — справочник парка, исключение или Seq."""

    def __init__(self, active=MOTO, archived=ABSENT, names=None):
        self.active = active
        self.archived = archived
        self.names = NAMES if names is None else names
        self.calls = []

    def _request(self, method, path, *, park_id, body=None, attempts=7, **_):
        query = (body or {}).get('filter') or {}
        archive = 'archive' in (query.get('groups') or [])
        driver = (query.get('contractor_ids') or [None])[0]
        self.calls.append((path, park_id, driver, archive, attempts))
        answer = (self.archived if archive else self.active) if path == PATH_LIST else self.names
        if isinstance(answer, Seq):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if path != PATH_LIST:
            return [{'id': code, 'name': name} for code, name in answer.items()]
        if isinstance(answer, dict):
            return answer
        if answer == ABSENT:
            return {'contractors': [], 'cursor': None}
        return {'contractors': [dict({'id': driver}, **({} if answer is None else {'profession_id': answer}))],
                'cursor': None}

    def lists(self):
        return [call for call in self.calls if call[0] == PATH_LIST]

    def dictionaries(self):
        return [call for call in self.calls if call[0] == PATH_PROFESSIONS]


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    """Клиенты и паузы живут в процессе — у каждого теста свои; повтор — без ожидания."""
    monkeypatch.setattr(profession, '_clients', {})
    monkeypatch.setattr(profession, '_blocks', {})
    monkeypatch.setattr(profession, 'RETRY_DELAY', 0)


def outage(code=500):
    return FleetError('Fleet не ответил после 1 попыток: HTTP %d' % code)


def ask(database, cabinets, driver=DRIVER, park=PARK, clock=None):
    clients = list(cabinets.items()) if isinstance(cabinets, dict) else [('ЭДО', cabinets)]
    return profession.lookup(database, driver, park, clients=lambda: clients, now=clock or Clock())


def age(connection, driver_id, seconds):
    with connection.cursor() as cursor:
        cursor.execute('UPDATE wazzup_driver_professions SET fetched_at = now() - make_interval(secs => %s) '
                       'WHERE driver_id = %s', (seconds, driver_id))
    connection.commit()


def stored(connection):
    with connection.cursor() as cursor:
        cursor.execute('SELECT driver_id, park_id, found, profession_id, profession_name, source '
                       'FROM wazzup_driver_professions ORDER BY driver_id')
        rows = cursor.fetchall()
    connection.commit()
    return rows


@needs_pg
def test_the_cabinet_list_is_asked_once_per_driver_and_the_card_then_reads_the_base(pg):
    connection, _ = pg
    database = Database(connection)
    cabinet = Cabinet()
    assert ask(database, cabinet) == {'id': MOTO, 'name': 'Мотокурьер'}, 'подпись — из справочника кабинета'
    assert cabinet.calls == [(PATH_LIST, PARK, DRIVER, False, 1), (PATH_PROFESSIONS, PARK, None, False, 1)], \
        'один запрос списка по ID водителя и справочник, без повторов'
    assert ask(database, cabinet) == {'id': MOTO, 'name': 'Мотокурьер'} and len(cabinet.calls) == 2
    age(connection, DRIVER, profession.FOUND_TTL - 60)
    assert ask(database, cabinet)['name'] == 'Мотокурьер' and len(cabinet.calls) == 2, 'неделю — из базы'
    age(connection, DRIVER, profession.FOUND_TTL + 1)
    assert ask(database, cabinet)['name'] == 'Мотокурьер'
    assert len(cabinet.lists()) == 2 and len(cabinet.dictionaries()) == 1, 'справочник уже есть — подпись из него'
    assert stored(connection) == [(DRIVER, PARK, True, MOTO, 'Мотокурьер', 'list')]


@needs_pg
def test_rows_of_the_first_release_from_the_driver_card_are_asked_again_at_once(pg):
    connection, _ = pg
    database = Database(connection)
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO wazzup_driver_professions(driver_id, park_id, found, profession_id, "
                       "profession_name) VALUES (%s, %s, TRUE, NULL, NULL), (%s, %s, TRUE, 'taxi/driver', "
                       "'Водитель такси')", (DRIVER, PARK, OTHER_DRIVER, PARK))
    connection.commit()
    cabinet = Cabinet(active='taxi/driver')
    assert ask(database, cabinet) == {'id': 'taxi/driver', 'name': 'Водитель такси'}
    assert cabinet.lists() == [(PATH_LIST, PARK, DRIVER, False, 1)], 'свежая, но из карточки — спрашиваем'
    down = Cabinet(active=outage(502))
    assert ask(database, down, driver=OTHER_DRIVER) == {'id': 'taxi/driver', 'name': 'Водитель такси'}, \
        'кабинет молчит — прежняя профессия того же парка'
    assert (DRIVER, PARK, True, 'taxi/driver', 'Водитель такси', 'list') in stored(connection)


@needs_pg
def test_a_label_from_any_saved_dictionary_serves_every_park_and_the_fallback_covers_an_outage(pg):
    connection, _ = pg
    database = Database(connection)
    assert ask(database, Cabinet(), park=OTHER_PARK)['name'] == 'Мотокурьер'
    cabinet = Cabinet()
    assert ask(database, cabinet) == {'id': MOTO, 'name': 'Мотокурьер'}
    assert cabinet.dictionaries() == [], 'подпись уже есть в справочнике другого парка'
    down = Cabinet(active='cargo/courier/on-car', names=outage(502))
    assert ask(database, down, driver=OTHER_DRIVER, park=park_id(3)) == {'id': 'cargo/courier/on-car',
                                                                         'name': 'Курьер на автомобиле'}
    assert len(down.dictionaries()) == 2, 'такой профессии нет ни в одном справочнике — спросили дважды, не вышло'


@needs_pg
def test_an_unknown_profession_is_not_shown_as_a_code_and_is_asked_again_sooner(pg):
    connection, _ = pg
    database = Database(connection)
    cabinet = Cabinet(active='scouts/worker', names={})
    assert ask(database, cabinet) is None
    assert stored(connection) == [(DRIVER, PARK, True, 'scouts/worker', None, 'list')]
    age(connection, DRIVER, profession.MISSING_TTL + 1)
    assert ask(database, cabinet) is None and len(cabinet.lists()) == 2
    assert len(cabinet.dictionaries()) == 1, 'пустой справочник парка не спрашивается снова неделю'


@needs_pg
def test_a_driver_without_a_profession_costs_no_dictionary_and_is_asked_again_sooner(pg):
    connection, _ = pg
    database = Database(connection)
    cabinet = Cabinet(active=None)
    assert ask(database, cabinet) is None and cabinet.dictionaries() == []
    assert stored(connection) == [(DRIVER, PARK, True, None, None, 'list')]
    age(connection, DRIVER, profession.MISSING_TTL - 60)
    assert ask(database, cabinet) is None and len(cabinet.calls) == 1
    age(connection, DRIVER, profession.MISSING_TTL + 1)
    cabinet.active = 'taxi/driver'
    assert ask(database, cabinet) == {'id': 'taxi/driver', 'name': 'Водитель такси'} and len(cabinet.lists()) == 2


@needs_pg
def test_an_archived_driver_is_found_in_the_archive_and_nobody_is_remembered_briefly(pg):
    connection, _ = pg
    database = Database(connection)
    archived = Cabinet(active=ABSENT, archived='taxi/driver')
    assert ask(database, archived) == {'id': 'taxi/driver', 'name': 'Водитель такси'}
    assert archived.lists() == [(PATH_LIST, PARK, DRIVER, False, 1), (PATH_LIST, PARK, DRIVER, True, 1)]
    nobody = Cabinet(active=ABSENT)
    assert ask(database, nobody, driver=OTHER_DRIVER) is None and len(nobody.lists()) == 2
    assert stored(connection)[0] == (OTHER_DRIVER, PARK, False, None, None, 'list')
    assert ask(database, nobody, driver=OTHER_DRIVER) is None and len(nobody.lists()) == 2
    age(connection, OTHER_DRIVER, profession.MISSING_TTL + 1)
    nobody.active = MOTO
    assert ask(database, nobody, driver=OTHER_DRIVER)['name'] == 'Мотокурьер'
    moved = Cabinet(active='cargo/courier/on-foot')
    assert ask(database, moved, driver=OTHER_DRIVER, park=OTHER_PARK) == {'id': 'cargo/courier/on-foot',
                                                                          'name': 'Пеший курьер'}
    assert moved.lists() == [(PATH_LIST, OTHER_PARK, OTHER_DRIVER, False, 1)], 'водитель в другом парке — спросим там'


@needs_pg
def test_a_refused_session_hands_over_to_the_mailings_account_and_rests(pg):
    connection, _ = pg
    database = Database(connection)
    clock = Clock()
    edm = Cabinet(active=FleetSessionExpired('401'))
    mailings = Cabinet()
    accounts = {'ЭДО': edm, 'Рассылки': mailings}
    assert ask(database, accounts, clock=clock)['name'] == 'Мотокурьер'
    assert len(edm.calls) == 1 and len(mailings.lists()) == 1 and mailings.dictionaries(), \
        'справочник — той учёткой, что ответила'
    assert ask(database, accounts, driver=OTHER_DRIVER, clock=clock)['name'] == 'Мотокурьер'
    assert len(edm.calls) == 1 and len(mailings.lists()) == 2, 'непринятую сессию не дёргаем'
    clock.value += profession.SESSION_PAUSE + 1
    edm.active = MOTO
    assert ask(database, accounts, driver='e' * 32, clock=clock)['name'] == 'Мотокурьер'
    assert len(edm.lists()) == 2 and len(mailings.lists()) == 2


@needs_pg
def test_no_rights_on_a_park_skips_that_account_for_that_park_only(pg):
    connection, _ = pg
    database = Database(connection)
    clock = Clock()

    class Picky(Cabinet):
        def _request(self, method, path, *, park_id, **kwargs):
            if park_id == PARK:
                self.calls.append((path, park_id, None, False, kwargs.get('attempts')))
                raise FleetAccessDenied('Кабинет отказал (403)')
            return super()._request(method, path, park_id=park_id, **kwargs)

    edm, mailings = Picky(), Cabinet()
    accounts = {'ЭДО': edm, 'Рассылки': mailings}
    assert ask(database, accounts, clock=clock)['name'] == 'Мотокурьер' and len(mailings.lists()) == 1
    assert ask(database, accounts, driver=OTHER_DRIVER, clock=clock) and len(edm.calls) == 1
    assert ask(database, accounts, driver='e' * 32, park=OTHER_PARK, clock=clock)
    assert edm.lists()[-1] == (PATH_LIST, OTHER_PARK, 'e' * 32, False, 1) and len(mailings.lists()) == 2
    plain = Cabinet(active=FleetError('Fleet вернул 403 без JSON (тело 0 байт)'))
    assert ask(database, {'ЭДО': plain, 'Рассылки': Cabinet()}, driver='d' * 32, park=park_id(5), clock=clock)
    assert profession._blocks[('ЭДО', park_id(5))] == clock.value + profession.DENIED_PAUSE, \
        '403 и без JSON — «нет прав», а не разовый отказ'


@needs_pg
def test_a_cabinet_outage_shows_the_last_profession_and_pauses_every_account(pg):
    connection, _ = pg
    database = Database(connection)
    clock = Clock()
    assert ask(database, Cabinet(), clock=clock)['name'] == 'Мотокурьер'
    age(connection, DRIVER, profession.FOUND_TTL + 1)
    edm = Cabinet(active=FleetError('Fleet не ответил после 1 попыток: ReadTimeout'))
    mailings = Cabinet()
    accounts = {'ЭДО': edm, 'Рассылки': mailings}
    assert ask(database, accounts, clock=clock) == {'id': MOTO, 'name': 'Мотокурьер'}, 'прежняя профессия'
    assert mailings.calls == [], 'сеть легла у кабинета, а не у учётки'
    sessions = Mock(return_value=list(accounts.items()))
    assert profession.lookup(database, OTHER_DRIVER, PARK, clients=sessions, now=clock) is None
    assert len(edm.calls) == 1 and mailings.calls == [], 'пауза: следующая карточка не ждёт кабинет'
    assert sessions.call_count == 0, 'и сессии из базы во время паузы не читаются'
    clock.value += profession.FAILURE_PAUSE + 1
    edm.active = MOTO
    assert ask(database, accounts, driver=OTHER_DRIVER, clock=clock)['name'] == 'Мотокурьер'


@needs_pg
def test_a_one_off_cabinet_failure_is_asked_again_at_once_and_a_timeout_is_not(pg):
    connection, _ = pg
    database = Database(connection)
    for failure in (outage(502), FleetError('Fleet не ответил после 1 попыток: 429 Too Many Requests')):
        cabinet = Cabinet(active=Seq([failure, MOTO]))
        driver = '%032x' % (len(stored(connection)) + 100)
        assert ask(database, cabinet, driver=driver)['name'] == 'Мотокурьер'
        assert len(cabinet.lists()) == 2 and profession._blocks == {}, 'повтор удался — паузы нет'
    twice = Cabinet(active=Seq([outage(503), outage(503)]))
    assert ask(database, twice, driver=OTHER_DRIVER) is None and len(twice.lists()) == 2
    assert ('*', None) in profession._blocks, 'второй сбой подряд — пауза всем учёткам'
    profession._blocks.clear()
    slow = Cabinet(active=FleetError('Fleet не ответил после 1 попыток: HTTPSConnectionPool: Read timed out.'))
    assert ask(database, slow, driver=OTHER_DRIVER) is None and len(slow.lists()) == 1, 'таймаут не повторяем'


@needs_pg
def test_another_refusal_of_one_account_hands_the_park_over_to_the_other(pg):
    connection, _ = pg
    database = Database(connection)
    clock = Clock()
    edm = Cabinet(active=FleetError("Fleet вернул 400: {'code': 'bad_request'}"))
    mailings = Cabinet()
    accounts = {'ЭДО': edm, 'Рассылки': mailings}
    assert ask(database, accounts, clock=clock)['name'] == 'Мотокурьер' and len(edm.calls) == 1
    assert ask(database, accounts, driver=OTHER_DRIVER, clock=clock) and len(edm.calls) == 1, \
        'этой учётке этот парк не отвечает — пока не спрашиваем'
    edm.active = MOTO
    assert ask(database, accounts, driver='e' * 32, park=OTHER_PARK, clock=clock)
    assert edm.lists()[-1][1] == OTHER_PARK, 'другой парк той же учётки — спрашиваем'
    clock.value += profession.REFUSED_PAUSE + 1
    assert ask(database, accounts, driver='d' * 32, clock=clock)
    assert edm.lists()[-1] == (PATH_LIST, PARK, 'd' * 32, False, 1)


@needs_pg
def test_a_silent_dictionary_does_not_hide_a_new_profession_for_hours(pg):
    connection, _ = pg
    database = Database(connection)
    clock = Clock()
    cabinet = Cabinet(active='scouts/worker', names=Seq([outage(500), outage(500), {'scouts/worker': 'Скаут'}]))
    assert ask(database, cabinet, clock=clock) is None
    assert stored(connection) == [], 'подписи нет, потому что справочник молчал, — не запоминаем'
    clock.value += profession.FAILURE_PAUSE + 1
    assert ask(database, cabinet, clock=clock) == {'id': 'scouts/worker', 'name': 'Скаут'}
    assert stored(connection) == [(DRIVER, PARK, True, 'scouts/worker', 'Скаут', 'list')]


@needs_pg
def test_every_refusal_and_answer_is_logged_with_the_request_the_account_and_the_answer(pg, caplog):
    connection, _ = pg
    database = Database(connection)
    caplog.set_level('INFO', logger='wazzup.driver_profession')
    edm = Cabinet(active=FleetError("Fleet вернул 400: {'driver_id': '%s', 'code': 'bad'}" % DRIVER))
    accounts = {'ЭДО': edm, 'Рассылки': Cabinet(names=outage(500))}
    assert ask(database, accounts)['name'] == 'Курьер на мотоцикле', 'справочник молчит — запасная подпись'
    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert ('кабинет Fleet не спрашиваем 600 с — список исполнителей, учётка «ЭДО»: FleetError: '
            "Fleet вернул 400: {'driver_id': '%s…', 'code': 'bad'}" % DRIVER[:8]) in text
    assert DRIVER not in text, 'ID водителя в логе — обрезанный'
    assert 'справочник профессий, учётка «Рассылки»: FleetError: Fleet не ответил после 1 попыток: HTTP 500' in text
    assert 'кабинет ответил (учётка «Рассылки») — %s, подпись: Курьер на мотоцикле' % MOTO in text
    caplog.clear()
    profession._blocks.clear()      # пауза после молчания справочника — к этой проверке не относится
    assert ask(database, Cabinet(active=None), driver=OTHER_DRIVER) is None
    assert 'кабинет ответил (учётка «ЭДО») — профессии в ответе нет, поля: id' in caplog.text
    assert ask(database, Cabinet(active=ABSENT), driver='e' * 32) is None
    assert 'кабинет ответил (учётка «ЭДО») — в этом парке водителя нет' in caplog.text


@needs_pg
def test_without_any_session_the_card_has_no_profession_and_nothing_breaks(pg):
    connection, _ = pg
    database = Database(connection)
    assert ask(database, {}) is None and stored(connection) == []

    class Broken:
        def _get_cursor(self):
            raise RuntimeError('база легла')

    assert profession.lookup(Broken(), DRIVER, PARK, clients=lambda: []) is None


def test_bad_ids_never_reach_the_base_or_the_cabinet():
    cabinet = Cabinet()
    database = Mock()
    for driver, park in ((DRIVER, None), (None, PARK), ('77000000000', PARK), (DRIVER, 'x' * 32)):
        assert profession.lookup(database, driver, park, clients=lambda: [('ЭДО', cabinet)]) is None
    assert cabinet.calls == [] and database._get_cursor.call_count == 0


def session_row(cursor, table, value):
    cursor.execute('INSERT INTO ' + table + '(id, cookies, user_agent) VALUES (1, %s, %s) '
                   'ON CONFLICT (id) DO UPDATE SET cookies = EXCLUDED.cookies',
                   (json.dumps([{'name': 'Session_id', 'value': value}]), 'test-agent'))


@needs_pg
def test_sessions_come_from_the_base_edm_first_and_a_client_lives_until_its_cookies_change(pg):
    connection, _ = pg
    database = Database(connection)
    with connection.cursor() as cursor:
        init_fleet_edm_schema(cursor)
        init_driver_mailings_schema(cursor)
        session_row(cursor, 'driver_mailing_session', 'mailings-test')
    connection.commit()
    only = profession.cabinet_clients(database)
    assert [name for name, _ in only] == ['Рассылки']
    with connection.cursor() as cursor:
        session_row(cursor, 'fleet_edm_session', 'edm-test')
    connection.commit()
    first = profession.cabinet_clients(database)
    assert [name for name, _ in first] == ['ЭДО', 'Рассылки']
    assert first[1][1] is only[0][1], 'куки те же — клиент тот же'
    assert isinstance(first[0][1], profession.CabinetClient)
    profession._blocks[('ЭДО', None)] = float('inf')
    profession._blocks[('Рассылки', PARK)] = float('inf')
    assert profession.cabinet_clients(database)[0][1] is first[0][1]
    with connection.cursor() as cursor:
        session_row(cursor, 'fleet_edm_session', 'edm-new')
    connection.commit()
    renewed = profession.cabinet_clients(database)
    assert renewed[0][1] is not first[0][1] and renewed[1][1] is first[1][1]
    assert ('ЭДО', None) not in profession._blocks, 'новые куки — прежний отказ не в счёт'
    assert ('Рассылки', PARK) in profession._blocks
    with connection.cursor() as cursor:
        cursor.execute("UPDATE driver_mailing_session SET cookies = '[]'")
    connection.commit()
    assert [name for name, _ in profession.cabinet_clients(database)] == ['ЭДО'], 'строка без кук — сессии нет'


class Transport:
    """Подменённый транспорт requests: ответы по очереди."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def response(status, payload=None):
    reply = Mock(status_code=status, content=b'{}', headers={'content-type': 'application/json'})
    reply.json.return_value = payload if payload is not None else {}
    return reply


def test_the_cabinet_client_answers_at_once_without_sleeps_retries_or_a_wider_pool(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fleet.time, 'sleep', sleeps.append)
    monkeypatch.setattr(profession, 'RETRY_DELAY', 0.3)
    client = profession.CabinetClient([{'name': 'Session_id', 'value': 'test'}], 'test-agent')
    client._shared_session = Transport(
        requests.ConnectionError('нет сети'), response(429), response(503),
        response(200, {'contractors': [{'id': DRIVER, 'profession_id': MOTO}], 'cursor': None}))
    for _ in range(2):
        with pytest.raises(FleetError):
            profession._fetch_profession(client, PARK, DRIVER)
    assert profession._fetch_profession(client, PARK, DRIVER) == (True, MOTO, ('id', 'profession_id'))
    assert [pause for pause in sleeps if pause > 0] == [0.3], 'пауза — только перед повтором 429, у транспорта своих нет'
    calls = client._shared_session.calls
    assert len(calls) == 4 and calls[-1][2]['timeout'] == profession.REQUEST_TIMEOUT
    assert calls[-1][0] == 'POST' and calls[-1][1].endswith(PATH_LIST)
    assert json.loads(calls[-1][2]['data']) == {'filter': {'contractor_ids': [DRIVER]},
                                               'projection': ['id', 'profession_id'], 'limit': 1}
    assert calls[-1][2]['headers']['x-park-id'] == PARK
    for _ in range(200):
        client._note_success()
    client._widen_after_quiet()
    assert client.concurrency == profession.CONCURRENCY, 'ширина не растёт до потолка «Рассылок»'


def test_the_profession_is_read_from_the_list_like_the_cabinet_column():
    assert profession._fetch_profession(Cabinet(active=' taxi/driver '), PARK, DRIVER) == (
        True, 'taxi/driver', ('id', 'profession_id'))
    for payload, fields in (({'contractors': [{'id': DRIVER}]}, ('id',)),
                            ({'contractors': [{'id': DRIVER.upper(), 'profession_id': ''}]}, ('id', 'profession_id'))):
        assert profession._fetch_profession(Cabinet(active=payload), PARK, DRIVER) == (True, None, fields)
    for nobody in ({'contractors': []}, {}, {'contractors': None}, {'contractors': ['x', None]},
                   {'contractors': [{'id': OTHER_DRIVER, 'profession_id': MOTO}]}):
        cabinet = Cabinet(active=nobody, archived=nobody)
        assert profession._fetch_profession(cabinet, PARK, DRIVER) == (False, None, ()), nobody
        assert [call[3] for call in cabinet.lists()] == [False, True], 'и в архиве'
    with pytest.raises(FleetError):
        profession._fetch_profession(Cabinet(active=FleetError("Fleet вернул 400: {'id': 404}")), PARK, DRIVER)
    wrapped = Cabinet(names={})
    wrapped._request = lambda *args, **kwargs: {'professions': [{'id': MOTO, 'name': ' Курьер на\nмотоцикле '},
                                                                {'id': '', 'name': 'x'}, {'id': 'y'}, 'z']}
    assert profession._fetch_names(wrapped, PARK) == {MOTO: 'Курьер на мотоцикле'}


# ── Карточка: профессия рядом с данными CRM ─────────────────────────────────

class Crm:
    def __init__(self, answers):
        self.answers = answers

    def __call__(self, driver_id):
        return self.answers[driver_id]


@needs_pg
def test_describe_adds_the_profession_of_the_crm_park_and_only_for_a_found_driver(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    monkeypatch.setattr(info, '_dictionary_next_try', [0.0])
    asked = []

    def lookup(driver_id, park):
        asked.append((driver_id, park))
        return {'id': MOTO, 'name': 'Курьер на мотоцикле'}

    payload = info.describe(database, DRIVER, OPTIONS, fetch=Crm({DRIVER: dict(DRIVER_DATA, account_id=DRIVER)}),
                            dictionary_fetch=lambda: DICTIONARY, profession=lookup)
    assert payload['info']['profession'] == {'id': MOTO, 'name': 'Курьер на мотоцикле'}
    assert asked == [(DRIVER, PARK)]
    plain = info.describe(database, DRIVER, OPTIONS, fetch=Crm({}), dictionary_fetch=lambda: DICTIONARY)
    assert 'profession' not in plain['info']
    from parcels import drivers as crm

    class Missing:
        def __call__(self, driver_id):
            raise crm.DriverLookupError('нет', code='driver_not_found', status=404)

    assert 'info' not in info.describe(database, OTHER_DRIVER, OPTIONS, fetch=Missing(), profession=lookup)
    assert asked == [(DRIVER, PARK)]


@needs_pg
def test_the_card_routes_ask_the_cabinet_with_the_portal_base_by_default(pg, monkeypatch):
    connection, _ = pg
    database = Database(connection)
    seen = []
    monkeypatch.setattr(profession, 'lookup', lambda db, driver, park: seen.append((db, driver, park)) or {
        'id': MOTO, 'name': 'Курьер на мотоцикле'})
    monkeypatch.setattr(info, 'describe', lambda db, driver_id, options, profession=None: {
        'driverId': driver_id, 'state': 'found', 'stale': False, 'error': None, 'fetchedAt': None,
        'info': {'profession': profession(driver_id, PARK)}})
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(pilot.build_pilot_blueprint(
        db=database, require_api_key=lambda fn: fn, guard=lambda: (42, None),
        channels=lambda account: [], preflight=lambda: ('', 204), listen_connect=lambda: None,
        transport=Mock(), event_broker=EventBroker(), access=lambda: {'mode': 'full', 'can_process': True},
        driver_card_sync={'wake': Mock(), 'options': lambda: OPTIONS}))
    client = app.test_client()
    assert client.post(CARD_PATH, json=card_body()).status_code == 201
    response = client.get(CARD_PATH + '/driver', query_string={'account': 'op', 'channelId': CHANNEL, 'chatId': CHAT})
    assert response.status_code == 200, response.json
    assert response.json['info']['profession']['name'] == 'Курьер на мотоцикле'
    assert seen == [(database, DRIVER, PARK)]
