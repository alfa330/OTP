# -*- coding: utf-8 -*-
"""Живой канал окна «Чаты» «Воронки найма» (hiring/wa_live.py) без базы: разбор тычков, кольцо, кадры, места."""

import json
import threading
import time

from hiring import wa_live


def test_payload_is_card_number_only():
    assert wa_live.parse_payload('{"c":12}') == frozenset({12})
    assert wa_live.parse_payload('{"c":0}') == frozenset({0})          # чат без карточки
    for junk in ('', 'x', '[]', '{"c":true}', '{"c":-1}', '{"c":"a"}', '{"c":4294967296}', '{}', None):
        assert wa_live.parse_payload(junk) is None, junk


def test_hub_collects_cards_since_cursor_and_resets_laggards():
    hub = wa_live.Hub(capacity=3)
    assert hub.wait(0, 0) == (set(), 0)
    hub.publish(frozenset({1}))
    hub.publish(frozenset({2, 0}))
    assert hub.wait(0, 0) == ({0, 1, 2}, 2)
    assert hub.wait(1, 0) == ({0, 2}, 2)
    hub.publish(None)                                   # слушатель переподключился — всем перечитать всё
    assert hub.wait(2, 0) == (None, 3)
    hub.publish(frozenset({5}))
    hub.publish(frozenset({6}))
    assert hub.wait(3, 0) == ({5, 6}, 5)
    # Тычки после курсора уже вытеснены из кольца (в нём 3 последних) — перечитать всё, а не отдать половину.
    lagging = wa_live.Hub(capacity=2)
    for card in (1, 2, 3):
        lagging.publish(frozenset({card}))
    assert lagging.wait(0, 0) == (None, 3)
    assert lagging.wait(1, 0) == ({2, 3}, 3)


def test_hub_wakes_waiting_stream_on_publish():
    hub = wa_live.Hub()
    got = []
    worker = threading.Thread(target=lambda: got.append(hub.wait(0, 5)))
    worker.start()
    time.sleep(0.05)
    started = time.monotonic()
    hub.publish(frozenset({7}))
    worker.join(2)
    assert got == [({7}, 1)] and time.monotonic() - started < 1


def test_frames_and_stream_places():
    assert wa_live.frame({3, 1}) == 'event: chats\ndata: {"c":[1,3]}\n\n'
    assert wa_live.frame(None) == 'event: chats\ndata: {"all":1}\n\n'
    assert json.loads(wa_live.frame(set(range(wa_live.MAX_CARDS_PER_FRAME + 1))).split('data: ')[1]) == {'all': 1}
    hub = wa_live.Hub()
    assert hub.acquire(1) and not hub.acquire(1)
    hub.release()
    assert hub.acquire(1)
    hub.release()
    hub.release()                                        # лишнее «вернуть» не уводит счёт в минус
    assert hub.streams == 0


def test_base_payload_is_vacancy_number_only():
    """«База hh» в реальном времени (владелец 26.11.2026): в тычке только номер вакансии hh — строкой (номера вакансий hh
    длиннее, чем помещается в JS без потерь, не бывают, но портал везде держит их строкой)."""
    assert wa_live.parse_base_payload('{"v":"139079696"}') == frozenset({'139079696'})
    assert wa_live.parse_base_payload('{"v":139079696}') == frozenset({'139079696'})
    for junk in ('', 'x', '[]', '{"v":true}', '{"v":"-1"}', '{"v":"abc"}', '{"v":"1' + '0' * 18 + '"}', '{"v":null}',
                 '{"c":12}', '{}', None):
        assert wa_live.parse_base_payload(junk) is None, junk
    assert wa_live.base_frame({'139079696', '138923446'}) == 'event: base\ndata: {"v":["138923446","139079696"]}\n\n'
    assert wa_live.base_frame(None) == 'event: base\ndata: {"all":1}\n\n'


class _Note:
    def __init__(self, channel, payload):
        self.channel, self.payload = channel, payload


class _Conn:
    def __init__(self, notes):
        self.notifies = list(notes)


def test_one_listener_sorts_pokes_by_channel():
    """Один слушатель на процесс (одно соединение с базой) раскладывает тычки «Чатов» и «Базы hh» по своим кольцам."""
    chats, base = wa_live.Hub(), wa_live.Hub()
    wa_live._drain(_Conn([_Note(wa_live.CHANNEL, '{"c":5}'), _Note(wa_live.BASE_CHANNEL, '{"v":"139079696"}'),
                          _Note(wa_live.BASE_CHANNEL, '{"c":5}'), _Note(wa_live.CHANNEL, '{"v":"1"}')]), chats, base)
    assert chats.wait(0, 0) == ({5}, 1)
    assert base.wait(0, 0) == ({'139079696'}, 1)
