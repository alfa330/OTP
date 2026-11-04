"""Каналы, которые обрабатываются отдельно от «Чатов ОП»: Global, +77008541777
«Департамент» (владелец, 30.10.2026: «отключить от чатов оп как и глобал и пересылать
все вебхуки на syntony прямо как глобал»), +77470943333 iTaxi и +77001551199 Salam Taxi
(владелец, 04.11.2026, тем же порядком).

Список живёт в нескольких местах — сервер, шаблоны, маршруты Syntony, триггер счётчика
«ждут ответа», фронт; тест держит их одинаковыми. Без приложения, базы и сети.
"""
from pathlib import Path

import pytest

from tests.test_wazzup_syntony import message
from wazzup import pilot, syntony, templates
from wazzup.unread import init_unread_schema

ROOT = Path(__file__).resolve().parents[1]
DEPARTMENT = '33bc674a-727f-4e2d-b350-da1832ecfdd5'
ITAXI = '771c4257-4f8f-45fb-8c6c-08b61ea7e7f5'
SALAM = '34da6a88-2b9c-4c5a-be8d-072e89bfd440'
# У каждого парка в Syntony своё подключение со своим ключом — и свой маршрут.
PARK_ROUTES = [(syntony.SYNTONY_DEPARTMENT, DEPARTMENT), (syntony.SYNTONY_ITAXI, ITAXI),
               (syntony.SYNTONY_SALAM, SALAM)]


class RecordingCursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)


def unread_sql():
    cursor = RecordingCursor()
    init_unread_schema(cursor)
    return '\n'.join(cursor.sql)


def test_parks_are_excluded_from_processing_like_global():
    assert pilot.EXCLUDED_CHANNELS == syntony.GLOBAL_CHANNEL_IDS | {DEPARTMENT, ITAXI, SALAM}
    assert templates.EXCLUDED_CHANNELS == pilot.EXCLUDED_CHANNELS


def test_syntony_answers_global_and_parks_and_operators_do_not():
    assert syntony.SYNTONY.channels == syntony.GLOBAL_CHANNEL_IDS
    for route, channel in PARK_ROUTES:
        assert route.channels == {channel}, route.name
    # Что отвечает Syntony, операторы из «Чатов ОП» не трогают — и наоборот.
    answered_by_syntony = set().union(*(route.channels for route in syntony.ROUTES
                                        if route.name.startswith('syntony')))
    assert answered_by_syntony == pilot.EXCLUDED_CHANNELS
    # Событие уходит одному получателю.
    for index, route in enumerate(syntony.ROUTES):
        for other in syntony.ROUTES[index + 1:]:
            assert not route.channels & other.channels, (route.name, other.name)


@pytest.mark.parametrize('route, channel', PARK_ROUTES, ids=[route.name for route, _ in PARK_ROUTES])
def test_park_events_go_to_its_own_route_whole(route, channel):
    payload = {'messages': [message('park-in', channel),
                            dict(message('park-out', channel), isEcho=True)],
               'statuses': [{'messageId': 'park-out', 'status': 'read'}],
               'channelsUpdates': [{'channelId': channel, 'state': 'active'}]}
    ready, pending, complete = syntony.filter_payload(payload, channels=route.channels)
    assert ready == payload and not pending and complete
    for other in syntony.ROUTES:
        if other is route:
            continue
        other_ready, other_pending, _ = syntony.filter_payload(payload, channels=other.channels)
        assert not other_ready and not other_pending, other.name


def test_mixed_webhook_of_two_parks_is_split_by_channel():
    itaxi_in, salam_in = message('itaxi-in', ITAXI), message('salam-in', SALAM)
    payload = {'messages': [itaxi_in, salam_in],
               'statuses': [{'messageId': 'itaxi-in', 'status': 'read'},
                            {'messageId': 'salam-in', 'status': 'read'}]}
    itaxi, _, itaxi_whole = syntony.filter_payload(payload, channels=syntony.SYNTONY_ITAXI.channels)
    salam, _, salam_whole = syntony.filter_payload(payload, channels=syntony.SYNTONY_SALAM.channels)
    assert itaxi == {'messages': [itaxi_in], 'statuses': payload['statuses'][:1]}
    assert salam == {'messages': [salam_in], 'statuses': payload['statuses'][1:]}
    assert not itaxi_whole and not salam_whole


def test_unread_counter_skips_and_clears_every_excluded_channel():
    sql = unread_sql()
    trigger = sql[sql.index('FUNCTION wazzup_track_unread'):sql.index('FUNCTION wazzup_dismiss_unanswered')]
    cleanup = sql[sql.index('DECLARE untracked'):]
    for channel in pilot.EXCLUDED_CHANNELS:
        assert channel in trigger
        assert channel in cleanup


def test_frontend_hides_the_driver_card_in_every_excluded_channel():
    source = (ROOT / 'src/components/wazzup/chatPilot.js').read_text(encoding='utf-8')
    block = source[source.index('GLOBAL_CHANNEL_IDS = new Set(['):]
    block = block[:block.index(']);')]
    assert {channel for channel in pilot.EXCLUDED_CHANNELS if f"'{channel}'" in block} == pilot.EXCLUDED_CHANNELS
