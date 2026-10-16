"""Карточка водителя: разбор ID, сделка чата в amoCRM и тело записи — без базы и сети.

Ответы amoCRM в фикстурах повторяют форму, снятую с боевого аккаунта 16.10.2026
(события сообщений по фильтру контакта, talk_id, поля контакта с field_code PHONE).
"""
import json
import unittest
from unittest.mock import Mock

import pytest
import requests

from wazzup import driver_cards_amo as amo
from wazzup.driver_cards import DRIVER_ID_HINT, normalize_driver_id
from wazzup.driver_cards_sync import CLIENT_MARGIN_SECONDS, LOGIN_PAUSES, ClientHolder

# Темп запросов к amoCRM проверяется отдельным тестом; здесь он только замедлил бы прогон.
amo.REQUEST_INTERVAL = 0

DRIVER = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
PARK = '0f1e2d3c4b5a69788796a5b4c3d2e1f0'


@pytest.mark.parametrize('raw, expected', [
    (DRIVER, DRIVER),
    ('  ' + DRIVER.upper() + '\n', DRIVER),
    ('https://fleet.yandex.kz/contractors/%s/details?park_id=%s&theme=night' % (DRIVER, PARK), DRIVER),
    ('https://fleet.yandex.kz/contractors/%s/car?park_id=%s' % (DRIVER.upper(), PARK), DRIVER),
    ('fleet.yandex.kz/drivers/%s' % DRIVER, DRIVER),
    ('ID: %s' % DRIVER, DRIVER),
    ('', None), ('   ', None), (None, None),
])
def test_driver_id_accepts_the_id_and_a_fleet_profile_link(raw, expected):
    assert normalize_driver_id(raw) == expected


@pytest.mark.parametrize('raw', [
    '77000000099',                                   # номер телефона — был такой случай в amoCRM
    'ractors/a1b2c3d4e5f60718293a4b5c',              # обрезанная ссылка — тоже был
    DRIVER[:-1], DRIVER + '0', 'g' * 32,
    '%s %s' % (DRIVER, PARK),                        # два разных кода без пути профиля
    'https://fleet.yandex.kz/contractors/%s/x/contractors/%s' % (DRIVER, PARK),
    # Ссылка с обрезанным ID профиля и ссылка на список водителей: одинокий код — park_id.
    'https://fleet.yandex.kz/contractors/%s/details?park_id=%s' % (DRIVER[:-1], PARK),
    'https://fleet.yandex.kz/drivers?park_id=%s' % PARK,
    'fleet.yandex.kz/contractors/%s' % DRIVER[:-2],
    'park_id=%s' % PARK,
    'x' * 2001, 12345, ['list'],
])
def test_driver_id_rejects_anything_that_is_not_one_profile_id(raw):
    with pytest.raises(ValueError) as error:
        normalize_driver_id(raw)
    assert str(error.value) == DRIVER_ID_HINT


def test_long_fleet_link_with_report_parameters_is_accepted():
    link = ('https://fleet.yandex.kz/contractors/%s/details?park_id=%s&rent_type=park'
            '&metrics_period_start=2026-09-10T00%%3A00%%3A00&metrics_period_end=2026-09-11T23%%3A59%%3A59'
            '&gps_date_from=2026-09-09T00%%3A00%%3A00&gps_date_to=2026-09-10T23%%3A59%%3A59&gps_view=list'
            % (DRIVER, PARK))
    assert len(link) > 300
    assert normalize_driver_id(link) == DRIVER


# ── amoCRM: транспорт ───────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status, body=None, raw=None):
        self.status_code = status
        self._body, self._raw = body, raw

    def json(self):
        if self._raw is not None:
            raise ValueError('not json')
        return self._body


class FakeClient:
    """Клиент amoCRM: отвечает по пути запроса, запоминает каждый вызов."""

    def __init__(self, routes=None):
        self.base = 'https://amo.invalid'
        self.routes = routes or {}
        self.calls = []
        self.expires_at = float('inf')

    def _request(self, method, url, **kwargs):
        path = url[len(self.base):]
        self.calls.append((method, path, kwargs))
        handler = self.routes.get((method, path))
        if handler is None:
            raise AssertionError('unexpected amoCRM call %s %s' % (method, path))
        result = handler(kwargs) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize('response, error', [
    (FakeResponse(401, {}), amo.AmoAuthError),
    (FakeResponse(429, {}), amo.AmoTransientError),
    (FakeResponse(502, {}), amo.AmoTransientError),
    (FakeResponse(200, raw='<html>'), amo.AmoTransientError),
    (requests.ConnectionError('boom'), amo.AmoTransientError),
    (requests.Timeout('slow'), amo.AmoTransientError),
    (FakeResponse(400, {'title': 'Bad Request'}), amo.AmoPermanentError),
    (FakeResponse(404, {}), amo.AmoPermanentError),
    # 403 — временная блокировка amoCRM за частоту запросов: повторить позже.
    (FakeResponse(403, {}), amo.AmoTransientError),
])
def test_amo_status_codes_map_to_retry_or_give_up(response, error):
    client = FakeClient({('GET', '/api/v4/x'): response})
    with pytest.raises(error):
        amo._request(client, 'GET', '/api/v4/x')


def test_auth_error_is_a_transient_error_so_the_card_is_retried():
    assert issubclass(amo.AmoAuthError, amo.AmoTransientError)
    assert not issubclass(amo.AmoPermanentError, amo.AmoTransientError)


def test_validation_details_reach_the_card_history():
    body = {'title': 'Bad Request', 'validation-errors': [{'errors': [{'path': 'custom_fields_values.0'}]}]}
    client = FakeClient({('PATCH', '/api/v4/leads/5'): FakeResponse(400, body)})
    with pytest.raises(amo.AmoPermanentError) as failure:
        amo.write_lead(client, 5, {'driver_id': DRIVER})
    assert 'Bad Request' in str(failure.value) and 'custom_fields_values' in str(failure.value)


# ── Справочники ─────────────────────────────────────────────────────────────

def custom_fields(*, park_enums=None, city_enums=None, drop=()):
    fields = [
        {'id': amo.FIELD_DRIVER_ID, 'type': 'text', 'name': 'ID водителя'},
        {'id': amo.FIELD_PARK, 'type': 'select', 'name': 'Таксопарк привлечения', 'enums': park_enums if park_enums is not None else [
            {'id': 1291843, 'value': 'Адал', 'sort': 500},
            {'id': 1667515, 'value': 'Jana Taxi', 'sort': 25},
            {'id': 1291845, 'value': 'Бизнес Партнер', 'sort': 1},
            {'id': 9, 'value': '  ', 'sort': 2}]},
        {'id': amo.FIELD_CITY, 'type': 'multiselect', 'name': 'Город привлечения', 'enums': city_enums if city_enums is not None else [
            {'id': 1610837, 'value': 'Алматы', 'sort': 2}, {'id': 1610839, 'value': 'Астана', 'sort': 3}]},
    ]
    return {'_embedded': {'custom_fields': [field for field in fields if field['id'] not in drop]}}


def test_options_follow_amo_order_and_skip_blank_values():
    client = FakeClient({('GET', '/api/v4/leads/custom_fields'): FakeResponse(200, custom_fields())})
    options = amo.fetch_options(client)
    assert options == {
        'parks': [{'id': 1291845, 'name': 'Бизнес Партнер'}, {'id': 1667515, 'name': 'Jana Taxi'},
                  {'id': 1291843, 'name': 'Адал'}],
        'cities': [{'id': 1610837, 'name': 'Алматы'}, {'id': 1610839, 'name': 'Астана'}],
    }


@pytest.mark.parametrize('kwargs', [{'drop': (amo.FIELD_DRIVER_ID,)}, {'drop': (amo.FIELD_CITY,)},
                                    {'park_enums': []}, {'city_enums': [{'id': 1, 'value': ''}]}])
def test_options_fail_loudly_when_amo_fields_are_gone(kwargs):
    client = FakeClient({('GET', '/api/v4/leads/custom_fields'): FakeResponse(200, custom_fields(**kwargs))})
    with pytest.raises(amo.AmoPermanentError):
        amo.fetch_options(client)


# ── Сделка чата ─────────────────────────────────────────────────────────────

def contact(contact_id, phone):
    return {'id': contact_id, 'custom_fields_values': [
        {'field_code': None, 'values': [{'value': phone}]},
        {'field_code': 'PHONE', 'values': [{'value': phone}]}]}


def event(at, talk, lead, kind='incoming_chat_message', entity_type='lead', event_id=None):
    return {'id': event_id or 'e%s-%s' % (talk, at), 'type': kind, 'entity_type': entity_type,
            'entity_id': lead, 'created_at': at,
            'value_after': [{'message': {'id': 'x', 'origin': 'com.wazzup24.wz', 'talk_id': talk}}]}


ANCHORS = [1_792_131_359, 1_792_131_329, 1_792_131_305]


def test_lead_is_taken_from_the_talk_that_matches_our_messages_by_time():
    events = [
        # Та же секунда в беседе другого канала того же номера — одно совпадение против трёх.
        event(1_792_131_359, 900, 111),
        event(1_792_131_360, 9001, 10000001, 'outgoing_chat_message'),
        event(1_792_131_330, 9001, 10000001),
        event(1_792_131_306, 9001, 10000001, 'outgoing_chat_message'),
        event(1_700_000_000, 9001, 50000000),
    ]
    assert amo.pick_lead(events, ANCHORS) == 10000001


def test_one_event_counts_for_one_message_only():
    # Пачка из трёх наших сообщений за минуту: одно событие чужой беседы в ту же
    # секунду лежит в допуске от всех трёх, но засчитывается одному.
    lonely = [event(ANCHORS[0], 900, 111)]
    ours = [event(ANCHORS[0] + 1, 5, 1), event(ANCHORS[1], 5, 1), event(ANCHORS[2] + 2, 5, 1)]
    assert [talk for _, talk, _ in amo.assign({900: lonely}, ANCHORS)] == [900]
    assigned = amo.assign({900: lonely, 5: ours}, ANCHORS)
    assert [talk for _, talk, _ in assigned] == [900, 5, 5]
    assert amo.pick_lead(lonely + ours, ANCHORS) == 1


def series(anchors, talk, lead, shift=0):
    return [event(at + shift, talk, lead, event_id='%s-%s' % (talk, at)) for at in anchors]


TEN = [1_792_131_000 + 60 * index for index in range(10)]


def test_two_talks_of_one_deal_are_one_chat_not_two_channels():
    # Беседу закрыли, водитель написал снова: amoCRM открыла новую беседу в той же сделке.
    events = series(TEN[:5], 71, 555) + series(TEN[5:], 72, 555)
    assert amo.pick_lead(events, TEN) == 555


def test_the_chat_moved_to_a_new_deal_mid_window_the_newest_wins():
    # Старая сделка закрыта, свежие сообщения легли в новую беседу новой сделки: 6 старых против 4 свежих.
    events = series(TEN[:6], 81, 111) + series(TEN[6:], 82, 222)
    assert amo.pick_lead(events, TEN) == 222


def test_a_same_second_message_of_another_channel_does_not_steal_the_chat():
    # Самое свежее наше сообщение совпало до секунды с сообщением в беседе другого
    # канала того же номера; у той беседы одно совпадение, у нашей — девять.
    events = series(TEN, 91, 777, shift=1) + [event(TEN[-1], 99, 999)]
    assert amo.pick_lead(events, TEN) == 777


def test_a_single_message_chat_still_finds_its_deal():
    assert amo.pick_lead([event(ANCHORS[0], 5, 42)], ANCHORS[:1]) == 42


def test_newest_event_of_the_talk_names_the_lead_when_the_talk_moved():
    events = [event(1_792_131_300, 7, 100), event(1_792_131_359, 7, 200), event(1_792_131_329, 7, 100)]
    assert amo.pick_lead(events, ANCHORS) == 200


@pytest.mark.parametrize('events, reason', [
    ([], 'не нашли переписку'),
    ([event(1_792_100_000, 7, 100)], 'не нашли переписку'),
    ([event(1_792_131_359, 7, 20000001, entity_type='contact')], 'не привязана к сделке'),
    ([event(1_792_131_359, 7, 100), event(1_792_131_359, 8, 200)], 'несколько каналов'),
    ([event(1_792_131_359, 7, 100, kind='lead_status_changed')], 'не нашли переписку'),
])
def test_no_guessing_when_the_chat_is_not_found_or_ambiguous(events, reason):
    with pytest.raises(amo.NoDealError) as missing:
        amo.pick_lead(events, ANCHORS)
    assert reason in str(missing.value)


def test_anchors_keep_the_last_week_of_the_newest_messages():
    newest = 1_792_131_359
    anchors = amo.usable_anchors([newest, newest - 10, None, 0, newest - 8 * 24 * 3600, newest - 10])
    assert anchors == [newest, newest - 10]
    assert len(amo.usable_anchors(range(newest - 100, newest))) == amo.ANCHOR_LIMIT
    assert amo.usable_anchors([]) == []


def test_resolve_asks_contacts_by_phone_tail_and_events_in_the_message_window():
    contacts = {'_embedded': {'contacts': [
        contact(1, '+7 (700) 000-01-01'), contact(2, '87000000101'),
        # query нашёл по другому полю (не телефон) — такого не берём.
        {'id': 3, 'custom_fields_values': [{'field_code': None, 'values': [{'value': '7000000101'}]}]},
        contact(4, '+77000000102'),
    ]}}
    client = FakeClient({
        ('GET', '/api/v4/contacts'): FakeResponse(200, contacts),
        ('GET', '/api/v4/events'): FakeResponse(200, {'_embedded': {'events': [
            event(ANCHORS[0], 5, 42), event(ANCHORS[1], 5, 42)]}}),
    })
    assert amo.resolve_lead(client, '77000000101', ANCHORS) == 42
    (_, _, contact_call), (_, _, events_call) = client.calls
    assert contact_call['params'] == {'query': '7000000101', 'limit': 50}
    params = events_call['params']
    assert params['filter[entity]'] == 'contact' and params['filter[entity_id][]'] == [1, 2]
    assert params['filter[type]'] == 'incoming_chat_message,outgoing_chat_message'
    assert params['filter[created_at][from]'] == min(ANCHORS) - amo.MATCH_TOLERANCE_SECONDS
    assert params['filter[created_at][to]'] == max(ANCHORS) + amo.MATCH_TOLERANCE_SECONDS


def test_events_are_requested_by_ten_contacts_and_page_through():
    contacts = {'_embedded': {'contacts': [contact(i, '77000000101') for i in range(1, 13)]}}
    pages = []

    def events(kwargs):
        params = kwargs['params']
        pages.append((tuple(params['filter[entity_id][]']), params['page']))
        if params['page'] == 1 and params['filter[entity_id][]'][0] == 1:
            return FakeResponse(200, {'_embedded': {'events': [event(ANCHORS[0] - 500 + i, 9, 1)
                                                               for i in range(amo.EVENT_PAGE)]}})
        return FakeResponse(200, {'_embedded': {'events': [event(ANCHORS[0], 5, 77)]}})

    client = FakeClient({('GET', '/api/v4/contacts'): FakeResponse(200, contacts),
                         ('GET', '/api/v4/events'): events})
    assert amo.resolve_lead(client, '77000000101', ANCHORS) == 77
    assert pages == [(tuple(range(1, 11)), 1), (tuple(range(1, 11)), 2), ((11, 12), 1)]


@pytest.mark.parametrize('chat_id, anchors, reason', [
    ('telegram-user', ANCHORS, 'не похож на телефон'),
    ('77000000101', [], 'нет сообщений'),
])
def test_resolve_explains_why_there_is_no_deal(chat_id, anchors, reason):
    with pytest.raises(amo.NoDealError) as missing:
        amo.resolve_lead(FakeClient(), chat_id, anchors)
    assert reason in str(missing.value)


def test_resolve_without_a_contact_reports_it():
    client = FakeClient({('GET', '/api/v4/contacts'): FakeResponse(204)})
    with pytest.raises(amo.NoDealError) as missing:
        amo.resolve_lead(client, '77000000101', ANCHORS)
    assert 'нет контакта' in str(missing.value)


# ── Запись ──────────────────────────────────────────────────────────────────

def test_patch_carries_only_filled_fields_and_enum_ids():
    assert amo.lead_patch({'driver_id': DRIVER, 'park_enum_id': 1667515, 'city_enum_id': None}) == {
        'custom_fields_values': [
            {'field_id': amo.FIELD_DRIVER_ID, 'values': [{'value': DRIVER}]},
            {'field_id': amo.FIELD_PARK, 'values': [{'enum_id': 1667515}]}]}
    assert amo.lead_patch({'city_enum_id': 1610837})['custom_fields_values'] == [
        {'field_id': amo.FIELD_CITY, 'values': [{'enum_id': 1610837}]}]


def test_write_goes_to_the_one_lead_and_checks_the_answer():
    client = FakeClient({('PATCH', '/api/v4/leads/10000001'): FakeResponse(200, {'id': 10000001})})
    assert amo.write_lead(client, 10000001, {'driver_id': DRIVER, 'park_enum_id': 1, 'city_enum_id': 2}) == 10000001
    method, path, kwargs = client.calls[0]
    assert (method, path) == ('PATCH', '/api/v4/leads/10000001')
    assert [value['field_id'] for value in kwargs['json']['custom_fields_values']] == [
        amo.FIELD_DRIVER_ID, amo.FIELD_PARK, amo.FIELD_CITY]
    wrong = FakeClient({('PATCH', '/api/v4/leads/1'): FakeResponse(200, {'id': 2})})
    with pytest.raises(amo.AmoTransientError):
        amo.write_lead(wrong, 1, {'driver_id': DRIVER})


def test_lead_link_uses_the_account_domain():
    assert amo.lead_url(10000001, 'igroupkz.amocrm.ru') == 'https://igroupkz.amocrm.ru/leads/detail/10000001'
    assert amo.lead_url(None) is None


# ── Живой поток ─────────────────────────────────────────────────────────────

def _frames(response, count):
    frames = iter(response.response)
    next(frames)  # connected
    result = []
    for _ in range(count):
        lines = next(frames).decode().strip().split('\n')
        fields = dict(line.split(': ', 1) for line in lines)
        result.append((fields.get('id'), fields['event'], json.loads(fields['data'])))
    return result


def test_cards_travel_in_their_own_frame_never_among_message_changes():
    """Вкладка, открытая до выкладки, на любое незнакомое событие внутри change
    перечитывает список и переписку. Незнакомый кадр она пропускает, а его id:
    всё равно двигает точку возобновления."""
    from tests.test_wazzup_pilot import fixture
    from wazzup.realtime import broadcast_changes
    app, _, broker, _ = fixture()
    client = app.test_client()
    response = client.get('/api/wazzup/pilot/stream', buffered=False)
    try:
        card = dict(account='op', channelId='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', chatId='77000000000',
                    messageId='card:1', cardId='1', kind='card', statusOnly=False, affectsList=False)
        status = dict(account='op', channelId=card['channelId'], chatId=card['chatId'], messageId='m1',
                      status='read', statusOnly=True, affectsList=False)
        cursor = Mock()
        cursor.fetchall.return_value = []   # строки нет — событие уходит без содержимого
        broadcast_changes(cursor, [card, status], broker)
        (change_id, change_name, change), (card_id, card_name, cards) = _frames(response, 2)
        assert change_name == 'change' and change == {'changes': [status]}
        assert card_name == 'card' and cards == {'cards': [card]}
        assert change_id == card_id == str(broker.current_seq())
    finally:
        response.close()


def test_requests_to_amo_are_paced(monkeypatch):
    slept = []
    clock = [100.0]
    monkeypatch.setattr(amo, 'REQUEST_INTERVAL', 0.2)
    monkeypatch.setattr(amo, '_last_request', [0.0])
    monkeypatch.setattr(amo.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(amo.time, 'sleep', lambda seconds: slept.append(round(seconds, 3)))
    client = FakeClient({('GET', '/api/v4/x'): FakeResponse(200, {})})
    amo._request(client, 'GET', '/api/v4/x')
    clock[0] += 0.05
    amo._request(client, 'GET', '/api/v4/x')
    assert slept == [0.15], 'второй запрос подряд ждёт остаток паузы'


def test_a_stale_hydrated_card_is_dropped_when_a_newer_event_comes_without_it():
    from wazzup.realtime import merge_changes
    hydrated = dict(kind='card', cardId='1', messageId='card:1', card={'amo': {'state': 'pending'}})
    later = dict(kind='card', cardId='1', messageId='card:1')
    assert 'card' not in merge_changes(hydrated, later), 'без содержимого — повод перечитать, а не старый итог'


# ── Клиент очереди ──────────────────────────────────────────────────────────

class ClientHolderTests(unittest.TestCase):
    def test_new_client_before_the_token_expires_and_after_a_refused_login(self):
        import time
        made = []

        def factory():
            client = Mock()
            client.expires_at = time.time() + 2400
            made.append(client)
            return client

        now = [0.0]
        holder = ClientHolder(factory, clock=lambda: now[0])
        first = holder.get()
        self.assertIs(holder.get(), first)
        first.expires_at = time.time() + CLIENT_MARGIN_SECONDS - 1
        second = holder.get()
        self.assertIsNot(second, first)
        holder.drop()
        now[0] += LOGIN_PAUSES[0] + 1
        self.assertIsNot(holder.get(), second)
        self.assertEqual(3, len(made))

    def test_a_failed_login_is_not_repeated_for_every_card(self):
        # Сменили пароль или пришла капча: вход под общей учёткой — не на каждую карточку.
        now = [1000.0]
        factory = Mock(side_effect=RuntimeError('amoCRM: csrf_token не найден'))
        holder = ClientHolder(factory, clock=lambda: now[0])
        for _ in range(20):
            with self.assertRaises(amo.AmoTransientError):
                holder.get()
        self.assertEqual(1, factory.call_count, 'двадцать карточек подряд — одна попытка входа')
        now[0] += LOGIN_PAUSES[0] + 1
        with self.assertRaises(amo.AmoTransientError):
            holder.get()
        self.assertEqual(2, factory.call_count)
        now[0] += LOGIN_PAUSES[0] + 1
        with self.assertRaises(amo.AmoTransientError):
            holder.get()
        self.assertEqual(2, factory.call_count, 'пауза растёт после второй неудачи')
        good = Mock()
        good.expires_at = float('inf')
        factory.side_effect = None
        factory.return_value = good
        now[0] += LOGIN_PAUSES[1] + 1
        self.assertIs(holder.get(), good)
        holder.drop()                       # 401 посреди работы — тоже неудача
        with self.assertRaises(amo.AmoTransientError):
            holder.get()
        now[0] += LOGIN_PAUSES[0] + 1
        self.assertIs(holder.get(), good)

    def test_failed_login_is_a_retryable_error_without_details(self):
        holder = ClientHolder(Mock(side_effect=RuntimeError('amoCRM: csrf_token не найден, пароль=secret')))
        with self.assertRaises(amo.AmoTransientError) as failure:
            holder.get()
        self.assertNotIn('secret', str(failure.exception))
