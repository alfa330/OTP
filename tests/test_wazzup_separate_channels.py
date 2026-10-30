"""Каналы, которые обрабатываются отдельно от «Чатов ОП»: Global и +77008541777
«Департамент» (владелец, 30.10.2026: «отключить от чатов оп как и глобал и пересылать
все вебхуки на syntony прямо как глобал»).

Список живёт в нескольких местах — сервер, шаблоны, маршрут Syntony, триггер счётчика
«ждут ответа», фронт; тест держит их одинаковыми. Без приложения, базы и сети.
"""
from pathlib import Path

from tests.test_wazzup_syntony import message
from wazzup import pilot, syntony, templates
from wazzup.unread import init_unread_schema

ROOT = Path(__file__).resolve().parents[1]
DEPARTMENT = '33bc674a-727f-4e2d-b350-da1832ecfdd5'


class RecordingCursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)


def unread_sql():
    cursor = RecordingCursor()
    init_unread_schema(cursor)
    return '\n'.join(cursor.sql)


def test_department_is_excluded_from_processing_like_global():
    assert pilot.EXCLUDED_CHANNELS == syntony.GLOBAL_CHANNEL_IDS | {DEPARTMENT}
    assert templates.EXCLUDED_CHANNELS == pilot.EXCLUDED_CHANNELS


def test_syntony_answers_global_and_department_and_operators_do_not():
    # У «Департамента» в Syntony своё подключение со своим ключом — и свой маршрут.
    assert syntony.SYNTONY.channels == syntony.GLOBAL_CHANNEL_IDS
    assert syntony.SYNTONY_DEPARTMENT.channels == {DEPARTMENT}
    # Что отвечает Syntony, операторы из «Чатов ОП» не трогают.
    assert syntony.SYNTONY.channels | syntony.SYNTONY_DEPARTMENT.channels <= pilot.EXCLUDED_CHANNELS
    # Событие уходит одному получателю.
    for index, route in enumerate(syntony.ROUTES):
        for other in syntony.ROUTES[index + 1:]:
            assert not route.channels & other.channels, (route.name, other.name)


def test_department_events_go_to_its_own_route_whole():
    payload = {'messages': [message('department-in', DEPARTMENT),
                            dict(message('department-out', DEPARTMENT), isEcho=True)],
               'statuses': [{'messageId': 'department-out', 'status': 'read'}],
               'channelsUpdates': [{'channelId': DEPARTMENT, 'state': 'active'}]}
    ready, pending, complete = syntony.filter_payload(payload, channels=syntony.SYNTONY_DEPARTMENT.channels)
    assert ready == payload and not pending and complete
    for route in (syntony.SYNTONY, syntony.POTOK_CRM):
        other_ready, other_pending, _ = syntony.filter_payload(payload, channels=route.channels)
        assert not other_ready and not other_pending, route.name


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
