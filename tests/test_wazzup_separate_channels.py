"""Каналы, которые обрабатываются отдельно от «Чатов ОП»: Global, +77008541777
«Департамент» (владелец, 30.10.2026: «отключить от чатов оп как и глобал и пересылать
все вебхуки на syntony прямо как глобал»), +77470943333 iTaxi и +77001551199 Salam Taxi
(владелец, 04.11.2026, тем же порядком), ещё 11 номеров второго списка Syntony
(владелец, 04.11.2026: «все эти … тоже») и JANA TAXI WABA (владелец, 05.11.2026).

Список живёт в нескольких местах — сервер, шаблоны, маршруты Syntony, триггер счётчика
«ждут ответа», фронт; тест держит их одинаковыми. Без приложения, базы и сети.
"""
import re
from pathlib import Path

import pytest

from tests.test_wazzup_syntony import message
from wazzup import pilot, syntony, templates
from wazzup.unread import init_unread_schema

ROOT = Path(__file__).resolve().parents[1]
DEPARTMENT = '33bc674a-727f-4e2d-b350-da1832ecfdd5'
ITAXI = '771c4257-4f8f-45fb-8c6c-08b61ea7e7f5'
SALAM = '34da6a88-2b9c-4c5a-be8d-072e89bfd440'
# Второй список Syntony, 04.11.2026: маршрут → канал, как в их файле (парк и последние
# цифры номера в имени маршрута; полных номеров в репозитории нет — страж личных данных).
SECOND_LIST = {
    'syntony_global_1230': 'b88e8d3a-f3f4-4854-bbc8-076a0924d118',
    'syntony_itaxi_0880': 'ee3372c2-f15f-4ace-8176-dba1a3838bb3',
    'syntony_jana_1372': '24ea28f0-ba8a-4be7-a9b9-6253ff3d39ef',
    'syntony_qazaq_9671': '7fede3da-b56a-4af4-918f-092145066b61',
    'syntony_amanat_2629': '9f6271c6-5ef7-4687-a196-de82a4c2dc8f',
    'syntony_department_7692': 'e28a8bf4-49e4-46c5-a915-23123439a4fc',
    'syntony_dostoyny_6032': 'e24b4922-14f9-4fa3-8730-e5d804869f7e',
    'syntony_nol_4569': '56701573-92a4-4121-af25-9be630900778',
    'syntony_tenge_9716': 'c0055888-ab4a-4bd1-ab54-44fa8342202b',
    'syntony_registration_1223': 'c4e9f91a-5a3c-4110-8319-345ce0229650',
    'syntony_chestny_0602': 'ec4209b1-c0f6-4cdd-9037-9ee18c34206d',
    # Файл Syntony 05.11.2026: JANA TAXI, WABA — свой ключ, у Jana …1372 остаётся свой.
    'syntony_jana_3322': '34403077-8ba8-4bb0-8c1e-576c0ce0db8e',
}
# У каждого номера в Syntony своё подключение со своим ключом — и свой маршрут.
PARK_ROUTES = [(syntony.SYNTONY_DEPARTMENT, DEPARTMENT), (syntony.SYNTONY_ITAXI, ITAXI),
               (syntony.SYNTONY_SALAM, SALAM)] + [
    (route, next(iter(route.channels))) for route in syntony.SYNTONY_NUMBER_ROUTES]


class RecordingCursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)


def unread_sql():
    cursor = RecordingCursor()
    init_unread_schema(cursor)
    return '\n'.join(cursor.sql)


def test_second_list_has_a_route_per_number_with_its_own_settings():
    assert {route.name: set(route.channels) for route in syntony.SYNTONY_NUMBER_ROUTES} == {
        name: {channel} for name, channel in SECOND_LIST.items()}
    for route in syntony.SYNTONY_NUMBER_ROUTES:
        assert route.env == route.name.upper() + '_WEBHOOK', route.name
        assert route.prepare is None, route.name
    names = [route.name for route in syntony.ROUTES]
    envs = [route.env for route in syntony.ROUTES]
    assert len(set(names)) == len(names) and len(set(envs)) == len(envs)
    # Прежние маршруты двух номеров одного парка не меняются: Global, iTaxi, «Департамент».
    assert syntony.SYNTONY.env == 'SYNTONY_WEBHOOK'
    assert syntony.SYNTONY_ITAXI.env == 'SYNTONY_ITAXI_WEBHOOK'
    assert syntony.SYNTONY_DEPARTMENT.env == 'SYNTONY_DEPARTMENT_WEBHOOK'


def test_parks_are_excluded_from_processing_like_global():
    assert pilot.EXCLUDED_CHANNELS == (syntony.GLOBAL_CHANNEL_IDS | {DEPARTMENT, ITAXI, SALAM}
                                       | set(SECOND_LIST.values()))
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
    listed = set(re.findall(r"'([0-9a-f-]{36})'", block))
    assert listed == pilot.EXCLUDED_CHANNELS
