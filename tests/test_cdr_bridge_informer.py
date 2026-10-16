# -*- coding: utf-8 -*-
"""Диспетчер автоинформатора на шлюзе (cdr_bridge/informer.py) — без Asterisk и без портала:
AMI и HTTP подменены. События — в том виде, в каком их шлёт Asterisk 20 (timestampevents=yes)."""
import base64
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr_bridge import informer  # noqa: E402

T0 = 1792080000.0


def test_parse_messages_splits_blocks_and_keeps_tail():
    raw = (b'Event: Newstate\r\nUniqueid: inf-1\r\nChannelState: 5\r\n\r\n'
           b'Event: Hangup\r\nUniqueid: inf-1\r\nCause: 16\r\n\r\nEvent: DTM')
    messages, rest = informer.parse_messages(raw)
    assert [m['Event'] for m in messages] == ['Newstate', 'Hangup']
    assert messages[1]['Cause'] == '16'
    assert rest == b'Event: DTM'


@pytest.mark.parametrize('call, expected', [
    ({'answered_at': 5, 'played_to_end': True, 'cause': 16}, ('answered', 'robot')),
    ({'answered_at': 5, 'played_to_end': False, 'cause': 16}, ('answered', 'driver')),
    ({'answered_at': 5, 'played_to_end': False, 'cause': 34}, ('answered', 'network')),
    # 480 через доли секунды и без гудков — Binotel не дал линию (у линии два звонка).
    ({'dialed_at': 0, 'ended_at': 0.2, 'cause': 19}, ('line_busy', '')),
    ({'dialed_at': 0, 'ended_at': 30, 'ringing_at': 3, 'cause': 19}, ('no_answer', '')),
    ({'dialed_at': 0, 'ended_at': 9, 'cause': 19}, ('unavailable', '')),   # сообщение сети без гудков
    # Гудки ранним звуком (183) без 180: долгий вызов — недозвон, а не «недоступен».
    ({'dialed_at': 0, 'ended_at': 30, 'cause': 19}, ('no_answer', '')),
    ({'dialed_at': 0, 'ended_at': 4, 'cause': 17}, ('busy', '')),
    ({'dialed_at': 0, 'ended_at': 4, 'ringing_at': 1, 'cause': 21}, ('rejected', '')),
    ({'dialed_at': 0, 'ended_at': 1, 'cause': 1}, ('unavailable', '')),
    ({'dialed_at': 0, 'ended_at': 35, 'originate_reason': 3}, ('no_answer', '')),
    ({'dialed_at': 0, 'ended_at': 1, 'cause': None}, ('failed', '')),
    # 16.10.2026: вызов шёл весь срок, причина 0 и ответа на набор не пришло (Binotel: CANCEL) — недозвон.
    ({'dialed_at': 0, 'ended_at': 35, 'cause': 0, 'originate_reason': None}, ('no_answer', '')),
    ({'dialed_at': 0, 'ended_at': 6.3, 'cause': 34}, ('failed', '')),     # 503 сети через 6 с — сбой
    # 16.10.2026: на INVITE ни одного ответа — таймер B SIP (32 с), 408 от самого Asterisk: до Binotel не дошло.
    ({'dialed_at': 0, 'ended_at': 32.0, 'cause': 18, 'originate_reason': 0}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 32.0, 'ringing_at': 4, 'cause': 18}, ('no_answer', '')),   # гудки были
    ({'dialed_at': 0, 'ended_at': 20, 'cause': 18}, ('no_answer', '')),                       # не таймер B
    # 15:46 того же дня: Binotel 32 с не достучался до оператора связи и вернул «занято»/«сброшен».
    ({'dialed_at': 0, 'ended_at': 33.1, 'cause': 21, 'originate_reason': 0}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 33.0, 'cause': 17}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 33.0, 'ringing_at': 5, 'cause': 21}, ('rejected', '')),   # гудки были — сбросил
    # Его же «недоступен» (480 → 19) и ответ на 35–36-й секунде — тоже сеть, а не водитель; раньше 31 с — нет.
    ({'dialed_at': 0, 'ended_at': 35.2, 'cause': 19, 'originate_reason': 0}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 36.0, 'cause': 21, 'originate_reason': 0}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 29.0, 'cause': 19, 'originate_reason': 0}, ('no_answer', '')),
    # Свой отбой по сроку вызова (40 с, причина 0, Reason 3) — недозвон: гудки были ранним звуком.
    ({'dialed_at': 0, 'ended_at': 40.1, 'cause': 0, 'originate_reason': 3}, ('no_answer', '')),
    # 19:05 того же дня: «сброшен» через секунду без гудков — сеть (12 подряд, у Binotel CHANUNAVAIL wait 0–1);
    # после гудков или позже трёх секунд — водитель.
    ({'dialed_at': 0, 'ended_at': 1.1, 'cause': 21, 'originate_reason': 0}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 2.9, 'cause': 21}, ('no_link', '')),
    ({'dialed_at': 0, 'ended_at': 1.2, 'ringing_at': 0.8, 'cause': 21}, ('rejected', '')),
    ({'dialed_at': 0, 'ended_at': 5.0, 'cause': 21}, ('rejected', '')),
    ({'dialed_at': 0, 'ended_at': 1.1, 'cause': 17}, ('busy', '')),              # «занято» сразу — бывает и у людей
])
def test_classify(call, expected):
    assert informer.classify(call) == expected


def test_result_payload_seconds():
    payload = informer.result_payload({'call_id': 7, 'line': '939', 'dialed_at': T0, 'answered_at': T0 + 7.6,
                                       'ended_at': T0 + 33.1, 'played_to_end': True, 'cause': 16,
                                       'dtmf': ['9']})
    assert payload['result'] == 'answered' and payload['hangup_by'] == 'robot'
    assert payload['listened_ms'] == 25500 and payload['ring_ms'] == 7600 and payload['dtmf'] == '9'


class FakeAmi:
    def __init__(self):
        self.connected = True
        self.sent = []

    def connect(self):
        self.connected = True

    def send(self, fields, variables=None):
        self.sent.append((fields, variables or {}))


class Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakePortal:
    """Портал: очередь ответов на poll, хранилище записей, принятые итоги."""

    def __init__(self, polls, audio=None):
        self.polls = list(polls)
        self.audio = audio or {}
        self.results = []
        self.paths = []

    def post(self, url, data, timeout, headers):
        path = url.split('/api/informer/agent/')[1]
        body = json.loads(data)
        self.paths.append(path)
        if path == 'poll':
            self.last_poll = body
            return Resp(200, self.polls.pop(0) if self.polls else {'calls': [], 'next_poll_sec': 10})
        if path == 'audio':
            raw = self.audio[body['campaign_id']]
            return Resp(200, {'sha256': hashlib.sha256(raw).hexdigest(), 'wav_base64': base64.b64encode(raw).decode()})
        if path == 'result':
            self.results.append(body)
            return Resp(200, {'applied': True})
        return Resp(404, {})


class Signer:
    def headers(self, method, path, body):
        return {'X-Agent-Key': 'test'}


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def make(tmp_path, polls, audio=None, lines=('939',), max_per_minute=0, dial_gap_ms=0,
         max_active=informer.MAX_ACTIVE_CALLS):
    clock = Clock()
    portal = FakePortal(polls, audio)
    ami = FakeAmi()
    config = {'portal': 'https://portal.test', 'proxy': '', 'ca_bundle': '', 'key_file': '', 'private_key': '',
              'lines': list(lines), 'per_line': 2, 'sounds_dir': str(tmp_path), 'heartbeat_file': '',
              'ami_host': '127.0.0.1', 'ami_port': 5038, 'ami_user': 'dispatcher', 'ami_secret': 's',
              'max_per_minute': max_per_minute, 'dial_gap_ms': dial_gap_ms, 'max_active': max_active}
    d = informer.Dispatcher(config, session=portal, ami=ami, clock=clock)
    d.signer = Signer()
    return d, portal, ami, clock


WAV = b'RIFF....WAVEfake'
SHA = hashlib.sha256(WAV).hexdigest()


def job(call_id, line='939', phone='77076129056'):
    return {'call_id': call_id, 'campaign_id': 3, 'phone': phone, 'line': line, 'audio_sha256': SHA, 'audio_ms': 25520}


def test_poll_reports_free_capacity_and_originates_with_channel_id(tmp_path):
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(11), job(12)], 'next_poll_sec': 2}], audio={3: WAV})
    assert d.tick() == 2
    assert portal.last_poll['lines'] == {'939': {'capacity': 2, 'active': 0}} and portal.last_poll['ami_ok'] is True
    fields, variables = ami.sent[0]
    assert fields['Action'] == 'Originate' and fields['ChannelId'] == 'inf-11'
    assert fields['Channel'] == 'PJSIP/77076129056@line939' and fields['Context'] == 'informer-play'
    assert variables['INFORMER_CALL'] == '11' and variables['INFORMER_FILE'] == os.path.join(str(tmp_path), SHA)
    assert (tmp_path / f'{SHA}.wav').read_bytes() == WAV           # запись скачана один раз
    assert portal.paths.count('audio') == 1
    assert d.lines_state() == {'939': {'capacity': 2, 'active': 2}}


def test_events_build_the_result(tmp_path):
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(21)], 'next_poll_sec': 2}], audio={3: WAV})
    d.tick()
    for event in (
        {'Event': 'Newstate', 'Uniqueid': 'inf-21', 'ChannelState': '5', 'Timestamp': str(T0 + 2)},
        {'Event': 'Newstate', 'Uniqueid': 'inf-21', 'ChannelState': '6', 'Timestamp': str(T0 + 7)},
        {'Event': 'DTMFEnd', 'Uniqueid': 'inf-21', 'Direction': 'Received', 'Digit': '9', 'Timestamp': str(T0 + 9)},
        {'Event': 'Hangup', 'Uniqueid': 'inf-21', 'Cause': '16', 'Cause-txt': 'Normal Clearing',
         'Timestamp': str(T0 + 17.5)},
    ):
        d.on_event(event)
    d.flush_results()
    result = portal.results[-1]
    assert result['call_id'] == 21 and result['result'] == 'answered' and result['hangup_by'] == 'driver'
    assert result['listened_ms'] == 10500 and result['ring_ms'] == 7000 and result['dtmf'] == '9'
    assert d.lines_state()['939']['active'] == 0                   # ёмкость линии освободилась


def test_played_to_end_and_originate_failure_without_channel(tmp_path):
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(31), job(32)], 'next_poll_sec': 2}], audio={3: WAV})
    d.tick()
    d.on_event({'Event': 'Newstate', 'Uniqueid': 'inf-31', 'ChannelState': '6', 'Timestamp': str(T0 + 5)})
    d.on_event({'Event': 'UserEvent', 'UserEvent': 'InformerPlayed', 'Call': '31', 'Timestamp': str(T0 + 30.5)})
    d.on_event({'Event': 'Hangup', 'Uniqueid': 'inf-31', 'Cause': '16', 'Timestamp': str(T0 + 30.6)})
    # Binotel отклонил набор, канала не было: OriginateResponse без Uniqueid.
    d.on_event({'Event': 'OriginateResponse', 'ActionID': 'inf-32', 'Response': 'Failure', 'Reason': '5',
                'Uniqueid': '<null>', 'Channel': '<null>', 'Timestamp': str(T0 + 0.3)})
    d.flush_results()
    by_id = {r['call_id']: r for r in portal.results}
    assert by_id[31]['result'] == 'answered' and by_id[31]['played_to_end'] and by_id[31]['hangup_by'] == 'robot'
    assert by_id[32]['result'] == 'busy'


def test_at_most_three_calls_at_once(tmp_path):
    """Владелец, 16.10.2026, после того как залп 46 в минуту уронил исходящие всему кабинету: «жёстких залпов
    делать не надо вообще, по 3 максимум». Портал видит три места, лишнее от него — «линия занята», в очередь."""
    lines = tuple(str(900 + i) for i in range(12))
    jobs = [job(100 + i, line=line) for i, line in enumerate(lines)]
    d, portal, ami, clock = make(tmp_path, [{'calls': jobs, 'next_poll_sec': 2}], audio={3: WAV}, lines=lines)
    state = d.lines_state()
    assert sum(info['capacity'] for info in state.values()) == 3           # всего три места…
    assert [line for line, info in state.items() if info['capacity']] == ['900', '901', '902']   # …по одному на линию
    d.tick()
    assert len(ami.sent) == 3
    assert len([r for r in portal.results if r['result'] == 'line_busy']) == 9
    assert d.rate_room() == float('inf') and d.active_room() == 0
    assert all(info['capacity'] == info['active'] for info in d.lines_state().values())   # мест нет
    d.finish({'call_id': 101, 'line': '901', 'dialed_at': clock.now - 30, 'answered_at': clock.now - 26,
              'ended_at': clock.now, 'played_to_end': True, 'cause': 16})
    state = d.lines_state()
    assert d.active_room() == 1 and sum(info['capacity'] - info['active'] for info in state.values()) == 1
    assert state['901'] == {'capacity': 1, 'active': 0}                    # место — на самой свободной линии
    # Та же линия занята двумя — два места на одной линии только когда других нет.
    d2, _, _, _ = make(tmp_path, [], lines=('939', '940'))
    assert d2.lines_state() == {'939': {'capacity': 2, 'active': 0}, '940': {'capacity': 1, 'active': 0}}
    d3, _, _, _ = make(tmp_path, [], lines=('939',))
    assert d3.lines_state() == {'939': {'capacity': 2, 'active': 0}}      # линия держит два — больше не дадим


def test_no_active_cap_when_disabled(tmp_path):
    """INFORMER_MAX_ACTIVE=0 — без предела: диспетчер набирает всё, что раздал портал."""
    jobs = [job(100 + i, line=str(900 + i)) for i in range(12)]
    d, portal, ami, clock = make(tmp_path, [{'calls': jobs, 'next_poll_sec': 2}], audio={3: WAV},
                                 lines=tuple(str(900 + i) for i in range(12)), max_active=0)
    assert sum(info['capacity'] for info in d.lines_state().values()) == 24
    d.tick()
    assert len(ami.sent) == 12
    assert not [r for r in portal.results if r['result'] == 'line_busy']
    assert d.rate_room() == float('inf') and d.active_room() == float('inf')


def test_minute_guard_when_configured(tmp_path):
    """INFORMER_MAX_PER_MINUTE — страховка, если её вернут: сверх неё «линия занята», водитель в очереди."""
    jobs = [job(100 + i, line=str(900 + i)) for i in range(12)]
    d, portal, ami, clock = make(tmp_path, [{'calls': jobs, 'next_poll_sec': 2}], audio={3: WAV},
                                 lines=tuple(str(900 + i) for i in range(12)), max_per_minute=10, max_active=0)
    d.tick()
    assert len(ami.sent) == 10
    refused = [r for r in portal.results if r['result'] == 'line_busy']
    assert len(refused) == 2                                        # остальные вернутся в очередь
    clock.now += 61
    assert d.rate_room() == 10


def _no_link(clock, call_id, line='902'):
    return {'call_id': call_id, 'line': line, 'dialed_at': clock.now - 32, 'ended_at': clock.now, 'cause': 18}


def test_link_breaker_pauses_dialing_then_probes_with_one_call(tmp_path):
    """16.10.2026: офисная сеть отрезала VM от Binotel — робот 6 минут слал вызовы в пустоту. Теперь после
    трёх «нет связи» подряд он не берёт звонков, ждёт 2, 5, 15 минут и проверяет связь одним звонком."""
    d, portal, ami, clock = make(tmp_path, [], lines=('902', '903'))
    d.finish(_no_link(clock, 1))
    d.finish(_no_link(clock, 2))
    assert d.lines_state()['902']['capacity'] == 2 and d.link_error() == ''   # два подряд — ещё не пауза
    d.finish(_no_link(clock, 3))
    assert all(info['capacity'] == info['active'] for info in d.lines_state().values())   # мест нет
    assert 'на паузе' in d.link_error()
    d.finish(_no_link(clock, 4))                                  # хвост набранных до паузы — не продлевает
    clock.now += 121
    state = d.lines_state()
    assert (state['902']['capacity'], state['903']['capacity']) == (1, 0)   # одно место — пробный звонок
    assert 'одним звонком' in d.link_error()
    d.finish(_no_link(clock, 5))                                  # пробный не дошёл — пауза 5 минут
    clock.now += 121
    assert d.lines_state()['902']['capacity'] == 0
    clock.now += 180
    assert d.lines_state()['902']['capacity'] == 1
    d.finish({'call_id': 6, 'line': '902', 'dialed_at': clock.now - 12, 'answered_at': clock.now - 8,
              'ended_at': clock.now, 'played_to_end': True, 'cause': 16})
    assert d.lines_state()['902']['capacity'] == 2 and d.link_error() == ''   # дошёл — полный ход
    assert [r['result'] for r in portal.results] == []            # итоги ушли в очередь портала, не потеряны
    assert len(d.pending) == 6


def test_jobs_during_link_pause_go_back_to_queue(tmp_path):
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(81), job(82)], 'next_poll_sec': 2}], audio={3: WAV})
    for call_id in (1, 2, 3):
        d.finish(_no_link(clock, call_id, line='939'))
    d.tick()
    assert ami.sent == []                                         # в паузе не звоним
    back = {r['call_id']: r['result'] for r in portal.results if r['call_id'] in (81, 82)}
    assert back == {81: 'line_busy', 82: 'line_busy'}             # водители — обратно в очередь, без попытки
    assert d.link_level == 1                                      # свой отказ связь «починенной» не делает
    assert 'на паузе' in portal.last_poll['error']                # портал и карточка робота знают почему


def test_dials_one_by_one_with_gap(tmp_path):
    jobs = [job(70 + i, line=str(900 + i)) for i in range(3)]
    d, portal, ami, clock = make(tmp_path, [{'calls': jobs, 'next_poll_sec': 2}], audio={3: WAV},
                                 lines=tuple(str(900 + i) for i in range(3)), dial_gap_ms=1000)
    slept = []
    d.sleep = slept.append
    d.tick()
    assert len(ami.sent) == 3 and slept == [1.0, 1.0]             # не залпом: пауза между наборами


def test_portal_pace_spreads_dials_over_the_minute(tmp_path):
    """15 в минуту — раз в 4 с (шаг присылает портал); свой шаг меньше — берём портальный, бред — не дольше 30 с."""
    jobs = [job(90 + i, line=str(900 + i)) for i in range(3)]
    d, portal, ami, clock = make(tmp_path, [{'calls': jobs, 'next_poll_sec': 2, 'dial_gap_ms': 4000},
                                            {'calls': [job(95), job(96)], 'next_poll_sec': 2, 'dial_gap_ms': 900000}],
                                 audio={3: WAV}, lines=tuple(str(900 + i) for i in range(3)), dial_gap_ms=1000)
    slept = []
    d.sleep = slept.append
    d.tick()
    assert slept == [4.0, 4.0]
    for call in list(d.calls.values()):
        d.finish({**call, 'ended_at': clock.now, 'cause': 16, 'answered_at': clock.now - 1, 'played_to_end': True})
    d.tick()
    assert slept[-1] == 30.0


def test_config_reads_lines_and_minute_guard(monkeypatch):
    monkeypatch.setenv('INFORMER_LINES', '902, 907,939')
    monkeypatch.setenv('INFORMER_MAX_PER_MINUTE', '')
    config = informer.load_config()
    assert config['lines'] == ['902', '907', '939'] and config['max_per_minute'] == 0
    monkeypatch.setenv('INFORMER_MAX_PER_MINUTE', '25')
    assert informer.load_config()['max_per_minute'] == 25
    assert config['max_active'] == informer.MAX_ACTIVE_CALLS == 3        # по умолчанию — не больше трёх разом
    monkeypatch.setenv('INFORMER_MAX_ACTIVE', '0')
    assert informer.load_config()['max_active'] == 0
    assert informer.load_config()['dial_gap_ms'] == informer.DIAL_GAP_MS == 1000
    monkeypatch.setenv('INFORMER_DIAL_GAP_MS', '0')
    assert informer.load_config()['dial_gap_ms'] == 0


def test_bad_audio_and_unknown_line_do_not_call(tmp_path):
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(41), job(42, line='999')], 'next_poll_sec': 2}],
                                 audio={3: b'other bytes'})
    d.tick()
    assert ami.sent == []                                           # запись не та — не звоним
    assert {r['call_id']: r['result'] for r in portal.results} == {41: 'failed', 42: 'failed'}


def test_stuck_call_deadline_follows_audio_length(tmp_path):
    long_job = {**job(52), 'audio_ms': 280000}              # почти пять минут записи
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(51), long_job], 'next_poll_sec': 2}], audio={3: WAV})
    d.tick()
    clock.now += informer.RING_TIMEOUT_MS / 1000 + 25.52 + informer.STUCK_MARGIN_S + 1   # вызов + запись + запас
    d.close_stuck()
    d.flush_results()
    assert [r['call_id'] for r in portal.results] == [51]   # длинную не трогаем: она ещё играет
    assert d.lines_state()['939']['active'] == 1
    clock.now += 280
    d.close_stuck()
    d.flush_results()
    assert [r['call_id'] for r in portal.results] == [51, 52]


def test_asterisk_refused_originate_closes_call_at_once(tmp_path):
    d, portal, ami, clock = make(tmp_path, [{'calls': [job(61)], 'next_poll_sec': 2}], audio={3: WAV})
    d.tick()
    d.on_event({'Response': 'Error', 'ActionID': 'inf-61', 'Message': 'Permission denied'})
    d.flush_results()
    assert portal.results[-1]['call_id'] == 61 and portal.results[-1]['result'] == 'failed'
    assert d.lines_state()['939']['active'] == 0


def _ami_server(reply):
    """AMI на 127.0.0.1: баннер, затем ответ на вход (reply) и одно событие."""
    import socket
    import threading
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        conn.sendall(b'Asterisk Call Manager/9.0.0\r\n')
        data = b''
        while b'\r\n\r\n' not in data:
            data += conn.recv(4096)
        conn.sendall(reply + b'Event: FullyBooted\r\nStatus: Fully Booted\r\n\r\n')
        threading.Event().wait(1)
        conn.close()
        server.close()
    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_ami_login_is_checked():
    events = []
    port = _ami_server(b'Response: Error\r\nActionID: login\r\nMessage: Authentication failed\r\n\r\n')
    ami = informer.Ami('127.0.0.1', port, 'dispatcher', 'wrong', events.append)
    with pytest.raises(ConnectionError):
        ami.connect()
    assert ami.connected is False                            # неверный секрет — «не на связи»

    port = _ami_server(b'Response: Success\r\nActionID: login\r\nMessage: Authentication accepted\r\n\r\n')
    ami = informer.Ami('127.0.0.1', port, 'dispatcher', 'right', events.append)
    ami.connect()
    assert ami.connected is True
    ami.close()


def test_events_of_other_channels_are_ignored(tmp_path):
    d, portal, ami, clock = make(tmp_path, [])
    d.on_event({'Event': 'Hangup', 'Uniqueid': '1792.55', 'Cause': '16'})
    d.on_event({'Event': 'Hangup', 'Uniqueid': 'inf-999', 'Cause': '16'})
    assert list(d.pending) == []
