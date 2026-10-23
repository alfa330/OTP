# -*- coding: utf-8 -*-
"""Коннектор АТС (cdr_bridge/pbx_connector.py, cdr_bridge/ami.py) — без станции и без портала:
AMI подменён, HTTP подменён. События — как у Asterisk 16 на станции.

Что закреплено:
  * изменения уходят на портал по событию (без опроса), пачкой; итоги команд — один раз;
  * при живом канале команд пачка говорит порталу `stream: true` и пульс редкий; без канала —
    частый, и команды берутся из ответа на пачку (запасной путь);
  * канал команд разбирает SSE по кускам байтов, исполняет команды, `bye` — переподключение
    без паузы, выключенный на портале канал — редкие попытки, обрыв — пауза растёт;
  * команды исполняются ровно теми действиями AMI, что описаны в ami.py; чужой канал
    положить нельзя, кривой набор не уходит на станцию;
  * неудачный Originate (телефон не ответил) даёт итог failed с человеческой причиной;
  * портал упал — изменения не теряются; переподключение AMI начинает состояние с чистого
    листа и шлёт полный снимок;
  * клиент AMI: вход, ответ по ActionID, списковый ответ, обрыв отпускает ждущих, закрытие
    кончает сессию и на станции.
"""
import json
import os
import socket
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr_bridge import ami as ami_mod, pbx_connector, signing  # noqa: E402

PRIVATE_B64, PUBLIC_B64, KEY_ID = signing.generate()


def ev(name, **fields):
    out = {'Event': name}
    out.update(fields)
    return out


# ── ami.py ───────────────────────────────────────────────────────────────────

def test_parse_messages_handles_split_packets_and_empty_values():
    raw = b'Event: Newchannel\r\nChannel: PJSIP/6726-1\r\nAccountCode:\r\n\r\nEvent: Hangup\r\nCau'
    messages, rest = ami_mod.parse_messages(raw)
    assert [m['Event'] for m in messages] == ['Newchannel']
    assert messages[0]['AccountCode'] == ''
    assert rest == b'Event: Hangup\r\nCau'


def test_encode_action_strips_line_breaks_that_would_forge_fields():
    data = ami_mod.encode_action({'Action': 'Originate', 'CallerID': '"x\r\nAction: Command" <1>'},
                                 {'A': 'b\nc'})
    assert data.count(b'\r\n') == 4, data
    assert b'Action: Command' not in data.split(b'\r\n')
    assert b'Variable: A=b c' in data


def test_originate_action_rings_operator_first_and_keeps_ext_as_callerid_number():
    fields, variables = ami_mod.originate_action('6726', '3333*77015550001', 'otp-cmd-7',
                                                 caller_name='+7 701 555 00 01', variables={'OTP_COMMAND': '7'})
    assert fields['Channel'] == 'PJSIP/6726'
    assert fields['Exten'] == '3333*77015550001'
    assert fields['Context'] == 'from-internal'
    assert fields['CallerID'] == '"+7 701 555 00 01" <6726>', 'номер — оператор: так FreePBX узнаёт его маршрут'
    assert fields['Async'] == 'true'
    assert fields['ActionID'] == 'otp-cmd-7'
    assert variables == {'OTP_COMMAND': '7'}


@pytest.mark.parametrize('mode, options', [('listen', 'qES'), ('whisper', 'qESw'), ('barge', 'qESB')])
def test_spy_action_modes(mode, options):
    fields = ami_mod.spy_action('6600', 'PJSIP/6726-', mode, 'otp-cmd-9')
    assert fields['Application'] == 'ChanSpy'
    assert fields['Data'] == 'PJSIP/6726-,' + options
    assert fields['Channel'] == 'PJSIP/6600'


def test_spy_action_rejects_unknown_mode():
    with pytest.raises(ValueError):
        ami_mod.spy_action('6600', 'PJSIP/6726-', 'record', 'x')


class FakeStation(threading.Thread):
    """Крошечный AMI: баннер, Login, Ping, CoreShowChannels списком, Getvar."""

    def __init__(self, sock, secret='s3cret', channels=None):
        super().__init__(daemon=True)
        self.sock = sock
        self.secret = secret
        self.channels = channels or []
        self.received = []

    def run(self):
        self.sock.sendall(b'Asterisk Call Manager/5.0.4\r\n')
        buffer = b''
        while True:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            messages, buffer = ami_mod.parse_messages(buffer)
            for message in messages:
                self.received.append(message)
                self.reply(message)

    def reply(self, message):
        action = message.get('Action')
        action_id = message.get('ActionID', '')
        if action == 'Login':
            ok = message.get('Secret') == self.secret
            self.sock.sendall(ami_mod.encode_action({
                'Response': 'Success' if ok else 'Error', 'ActionID': action_id,
                'Message': 'Authentication accepted' if ok else 'Authentication failed'}))
            if ok:
                self.sock.sendall(ami_mod.encode_action({'Event': 'FullyBooted', 'Status': 'Fully Booted'}))
        elif action == 'Ping':
            self.sock.sendall(ami_mod.encode_action({'Response': 'Success', 'ActionID': action_id, 'Ping': 'Pong'}))
        elif action == 'CoreShowChannels':
            self.sock.sendall(ami_mod.encode_action({'Response': 'Success', 'ActionID': action_id,
                                                     'EventList': 'start', 'Message': 'Channels will follow'}))
            for channel in self.channels:
                item = dict(channel)
                item.update({'Event': 'CoreShowChannel', 'ActionID': action_id})
                self.sock.sendall(ami_mod.encode_action(item))
            self.sock.sendall(ami_mod.encode_action({'Event': 'CoreShowChannelsComplete', 'ActionID': action_id,
                                                     'EventList': 'Complete', 'ListItems': str(len(self.channels))}))
        elif action == 'Logoff':
            self.sock.close()


def connected_ami(monkeypatch, secret='s3cret', channels=None, events=None):
    client_sock, server_sock = socket.socketpair()
    station = FakeStation(server_sock, secret=secret, channels=channels)
    station.start()
    monkeypatch.setattr(ami_mod.socket, 'create_connection', lambda *a, **k: client_sock)
    received = events if events is not None else []
    ami = ami_mod.Ami('127.0.0.1', 5038, 'stats', 's3cret', received.append)
    return ami, station, received


def test_ami_logs_in_dispatches_events_and_answers_actions(monkeypatch):
    ami, station, events = connected_ami(monkeypatch, channels=[
        {'Channel': 'PJSIP/6726-1', 'Uniqueid': '1.1', 'Linkedid': '1.1', 'Duration': '00:00:10'}])
    ami.connect()
    assert ami.connected
    assert station.received[0]['Events'] == 'call,agent,dialing,originate'
    assert ami.ping()
    reply = ami.action({'Action': 'CoreShowChannels'})
    assert reply['Response'] == 'Success'
    assert [item['Channel'] for item in reply['_items']] == ['PJSIP/6726-1']
    for _ in range(50):
        if events:
            break
        threading.Event().wait(0.02)
    assert events and events[0]['Event'] == 'FullyBooted'
    ami.close()


def test_ami_rejects_bad_secret(monkeypatch):
    ami, _station, _events = connected_ami(monkeypatch, secret='other')
    with pytest.raises(ami_mod.AmiError):
        ami.connect()
    assert not ami.connected


def test_ami_drop_releases_waiters(monkeypatch):
    ami, station, _events = connected_ami(monkeypatch)
    ami.connect()
    dropped = []
    ami.on_disconnect = dropped.append
    # Станция рвёт соединение. Голый close() тут не годится: на Linux он не будит поток
    # станции, стоящий в recv(), и соединение остаётся живым (так тест краснел в CI).
    station.sock.shutdown(socket.SHUT_RDWR)
    station.sock.close()
    for _ in range(100):
        if dropped:
            break
        threading.Event().wait(0.02)
    assert not ami.connected
    assert dropped, 'обрыв должен быть доложен'
    with pytest.raises(ami_mod.AmiError):
        ami.action({'Action': 'Ping'})


def test_ami_close_ends_session_at_station_and_reader(monkeypatch):
    # Коннектор закрывает сессию, когда станция не ответила на Ping или не отдала список каналов,
    # и следом открывает новую. Старая обязана кончиться и на станции, иначе её события идут в
    # on_event рядом с событиями новой. Ловится на Linux (шлюз, CI): там голый close() не будит
    # поток чтения, стоящий в recv().
    ami, station, _events = connected_ami(monkeypatch)
    ami.connect()
    dropped = []
    ami.on_disconnect = dropped.append
    reader = ami._reader
    ami.close()
    reader.join(2)
    station.join(2)
    assert not reader.is_alive(), 'поток чтения закрытой сессии должен закончиться'
    assert not station.is_alive(), 'станция должна увидеть конец сессии'
    assert dropped == [], 'своё закрытие — не обрыв'


# ── SSE канала команд ─────────────────────────────────────────────────────────

def test_sse_parser_handles_frames_split_anywhere():
    parser = pbx_connector.SseParser()
    stream = (b': connected 1792000000\n\n'
              b'event: command\ndata: {"id": 5, "kind": "hangup", "channel": "PJSIP/6726-0001"}\n\n'
              b': ping 1\n\nevent: bye\ndata: {"reason":"lifetime"}\n\n')
    out = []
    for index in range(0, len(stream), 7):
        out.extend(parser.feed(stream[index:index + 7]))
    assert out[0] == ('', 'connected 1792000000')
    assert out[1][0] == 'command'
    assert json.loads(out[1][1])['id'] == 5
    assert out[2] == ('', 'ping 1')
    assert out[3] == ('bye', '{"reason":"lifetime"}')


# ── коннектор ────────────────────────────────────────────────────────────────

class FakeAmi:
    def __init__(self):
        self.connected = True
        self.actions = []
        self.reply = {'Response': 'Success', 'Message': 'Originate successfully queued'}
        self.items = []
        self.list_complete = True
        self.last_rx = 10 ** 12

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def action(self, fields, variables=None, timeout=10):
        self.actions.append((dict(fields), dict(variables or {})))
        if fields.get('Action') == 'CoreShowChannels':
            return {'Response': 'Success', '_items': [dict(item) for item in self.items],
                    '_complete': self.list_complete}
        if fields.get('Action') == 'Getvar':
            return {'Response': 'Success', 'Value': 'q-3034-77015550001-20261022-120000-1.1.wav'}
        return dict(self.reply)

    def ping(self):
        return True

    def idle_seconds(self, now=None):
        return 0


class FakeResponse:
    def __init__(self, status=200, body=None, chunks=None, text=None):
        self.status_code = status
        self._body = body if body is not None else {'status': 'ok', 'commands': []}
        self.text = text if text is not None else json.dumps(self._body)
        self.chunks = chunks or []
        self.closed = False

    def json(self):
        return self._body

    def iter_content(self, chunk_size=None):
        for chunk in self.chunks:
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self):
        self.posts = []
        self.replies = []
        self.fail = None

    def post(self, url, data=None, timeout=None, headers=None, stream=False):
        if self.fail:
            raise self.fail
        self.posts.append((url, json.loads(data), headers, stream))
        return self.replies.pop(0) if self.replies else FakeResponse()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_connector(lines=None):
    config = {'portal': 'https://portal.example', 'private_key': PRIVATE_B64, 'key_file': '', 'proxy': '',
              'ca_bundle': '', 'ami_host': '192.0.2.1', 'ami_port': 5038, 'ami_user': 'stats',
              'ami_secret': 'x', 'snapshot_seconds': 60.0, 'recording_lookup': True,
              'heartbeat_file': '', 'stream': True}
    clock = Clock()
    session, stream_session = FakeSession(), FakeSession()
    fake_ami = FakeAmi()
    connector = pbx_connector.Connector(
        config, session=session, stream_session=stream_session, ami_factory=lambda: fake_ami,
        clock=clock, lines_reader=lambda cfg: (lines or {}, ''))
    connector.ensure_ami()                  # вход и снимок CoreShowChannels
    connector.uplink_once(sleep=lambda s: None)   # первая пачка — полный снимок
    session.posts.clear()
    return connector, session, stream_session, fake_ami, clock


def incoming(connector, lid='1.100'):
    trunk = 'PJSIP/+77475550000_jana-00000001'
    connector.on_event(ev('Newchannel', Channel=trunk, Uniqueid=lid, Linkedid=lid, Context='from-pstn',
                          Exten='7475550000', CallerIDNum='+77015550001'))
    connector.on_event(ev('QueueCallerJoin', Channel=trunk, Uniqueid=lid, Linkedid=lid, Queue='3034'))
    return trunk


def no_sleep(seconds):
    return None


def test_first_exchange_is_a_snapshot_after_coreshowchannels():
    connector, session, _s, fake_ami, clock = make_connector()
    assert fake_ami.actions[0][0]['Action'] == 'CoreShowChannels'
    assert connector.last_snapshot == clock.now
    assert connector.snapshot_due is False


def test_event_wakes_the_uplink_and_goes_out_without_waiting_for_a_poll():
    connector, session, _s, _ami, clock = make_connector()
    connector.uplink_wake.clear()
    incoming(connector)
    assert connector.uplink_wake.is_set(), 'событие станции будит отправку сразу'
    slept = []
    wait = connector.uplink_once(woke=True, sleep=slept.append)
    assert slept == [pbx_connector.COALESCE_SECONDS], 'склейка всплеска перед пачкой'
    url, payload, headers, _stream = session.posts[-1]
    assert url.endswith('/api/pbx/agent/sync')
    assert headers['X-Agent-Key'] == KEY_ID
    assert payload['snapshot'] is False
    assert payload['stream'] is False
    assert [c['linkedid'] for c in payload['events']] == ['1.100']
    assert payload['events'][0]['state'] == 'queued'
    assert payload['ami'] == {'connected': True, 'host': '192.0.2.1:5038', 'error': None, 'events_total': 2}
    assert wait == pytest.approx(pbx_connector.FALLBACK_POLL_SECONDS), 'без канала команд — частый пульс'


def test_quiet_uplink_sends_nothing_until_the_heartbeat():
    connector, session, _s, _ami, clock = make_connector()
    connector.stream_connected = True
    connector.uplink_once(sleep=no_sleep)
    assert session.posts[-1][1]['stream'] is True, 'открытый канал команд — повод сообщить сразу'
    session.posts.clear()
    wait = connector.uplink_once(sleep=no_sleep)
    assert session.posts == [], 'тишина без пульса — ни одного запроса'
    assert wait == pytest.approx(pbx_connector.HEARTBEAT_SECONDS)
    clock.now += pbx_connector.HEARTBEAT_SECONDS
    connector.uplink_once(sleep=no_sleep)
    payload = session.posts[-1][1]
    assert (payload['events'], payload['results'], payload['stream']) == ([], [], True)


def test_snapshot_every_minute_sends_everything_known():
    connector, session, _s, _ami, clock = make_connector()
    connector.stream_connected = True
    incoming(connector)
    connector.uplink_once(woke=True, sleep=no_sleep)
    clock.now += 61
    connector.uplink_once(sleep=no_sleep)
    payload = session.posts[-1][1]
    assert payload['snapshot'] is True
    assert [c['linkedid'] for c in payload['events']] == ['1.100']


def test_commands_from_the_sync_reply_are_the_fallback_path():
    connector, session, _s, fake_ami, clock = make_connector()
    session.replies.append(FakeResponse(body={'commands': [
        {'id': 15, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001', 'caller_name': '+7 701 555-00-01'}]}))
    clock.now += pbx_connector.FALLBACK_POLL_SECONDS
    connector.uplink_once(sleep=no_sleep)
    originate = [a for a in fake_ami.actions if a[0].get('Action') == 'Originate']
    assert len(originate) == 1
    fields, variables = originate[0]
    assert fields['Channel'] == 'PJSIP/6726' and fields['Exten'] == '3333*77015550001'
    assert fields['ActionID'] == 'otp-cmd-15'
    assert variables == {'OTP_COMMAND': '15'}
    assert list(connector.results) == [{'command_id': 15, 'status': 'accepted',
                                        'message': 'телефон 6726 звонит, после ответа набор 3333*77015550001'}]
    assert connector.uplink_wake.is_set(), 'итог команды будит отправку'
    connector.uplink_once(woke=True, sleep=no_sleep)
    assert session.posts[-1][1]['results'][0]['status'] == 'accepted'
    assert not connector.results
    clock.now += pbx_connector.FALLBACK_POLL_SECONDS
    connector.uplink_once(sleep=no_sleep)
    assert session.posts[-1][1]['results'] == [], 'итог уходит один раз'
    connector.on_event(ev('OriginateResponse', ActionID='otp-cmd-15', Response='Failure', Reason='3',
                          Uniqueid='<null>'))
    assert list(connector.results) == [{'command_id': '15', 'status': 'failed', 'message': 'телефон не ответил'}]


def stream_bytes(*commands, bye=True):
    out = b': connected 1792000000\n\n'
    for command in commands:
        out += b'event: command\ndata: ' + json.dumps(command).encode() + b'\n\n'
    out += b': ping 1792000015\n\n'
    if bye:
        out += b'event: bye\ndata: {"reason":"lifetime"}\n\n'
    return out


def test_command_channel_executes_commands_as_they_arrive():
    connector, session, stream_session, fake_ami, _clock = make_connector()
    raw = stream_bytes({'id': 16, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001'})
    stream_session.replies.append(FakeResponse(chunks=[raw[i:i + 11] for i in range(0, len(raw), 11)]))
    seen_connected = []
    original = connector.execute

    def spy_execute(command):
        seen_connected.append(connector.stream_connected)
        original(command)
    connector.execute = spy_execute
    pause = connector.open_stream()
    assert pause == 0.0, 'bye — переподключение без паузы'
    url, body, headers, streamed = stream_session.posts[-1]
    assert url.endswith('/api/pbx/agent/stream') and streamed is True
    assert headers['X-Agent-Key'] == KEY_ID
    assert body['version'].startswith(pbx_connector.VERSION)
    assert seen_connected == [True], 'команда пришла по живому каналу'
    assert [a[0]['Action'] for a in fake_ami.actions].count('Originate') == 1
    assert connector.results[0]['status'] == 'accepted'
    assert connector.stream_connected is False, 'канал закрыт — флаг снят'


def test_command_channel_drop_backs_off_and_off_channel_retries_rarely():
    connector, _session, stream_session, _ami, _clock = make_connector()
    stream_session.fail = ConnectionError('reset')
    assert connector.open_stream() == 1.0
    assert connector.open_stream() == 3.0
    stream_session.fail = None
    stream_session.replies.append(FakeResponse(status=503, text='{"code":"PBX_STREAM_OFF"}'))
    assert connector.open_stream() == pbx_connector.STREAM_OFF_RETRY_SECONDS
    stream_session.replies.append(FakeResponse(status=404, text='not found'))
    assert connector.open_stream() == pbx_connector.STREAM_OFF_RETRY_SECONDS
    # Канал, закрывшийся без прощания, — обрыв, а не плановое переподключение.
    stream_session.replies.append(FakeResponse(chunks=[stream_bytes(bye=False)]))
    assert connector.open_stream() > 0


def test_with_a_live_channel_the_sync_says_so_and_heartbeats_rarely():
    connector, session, stream_session, _ami, clock = make_connector()
    connector.stream_connected = True
    incoming(connector)
    wait = connector.uplink_once(woke=True, sleep=no_sleep)
    assert session.posts[-1][1]['stream'] is True
    assert wait == pytest.approx(pbx_connector.HEARTBEAT_SECONDS)


def test_originate_success_marks_the_call_as_ours():
    connector, session, _s, fake_ami, _clock = make_connector()
    connector.execute({'id': 16, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001'})
    connector.on_event(ev('OriginateResponse', ActionID='otp-cmd-16', Response='Success', Uniqueid='2.1',
                          Channel='PJSIP/6726-00000002', Exten='3333*77015550001'))
    connector.on_event(ev('Newchannel', Channel='PJSIP/6726-00000002', Uniqueid='2.1', Linkedid='2.1',
                          Context='from-internal', Exten='', CallerIDNum='6726'))
    call = connector.pending['2.1']
    assert call['origin'] == 'portal' and call['command_id'] == 16
    assert [r['status'] for r in connector.results] == ['accepted', 'done']
    assert connector.results[1]['linkedid'] == '2.1'


def test_bad_dial_and_unknown_channel_do_not_reach_the_station():
    connector, _session, _s, fake_ami, _clock = make_connector()
    for command in ({'id': 1, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001; Action: Command'},
                    {'id': 2, 'kind': 'hangup', 'channel': 'PJSIP/6726-00000009'},
                    {'id': 3, 'kind': 'teleport'}):
        connector.execute(command)
    assert [a[0]['Action'] for a in fake_ami.actions] == ['CoreShowChannels']
    assert [(r['command_id'], r['status']) for r in connector.results] == [(1, 'failed'), (2, 'failed'), (3, 'failed')]
    assert 'уже нет' in connector.results[1]['message']


def test_hangup_and_spy_use_channels_the_connector_has_seen():
    connector, _session, _s, fake_ami, _clock = make_connector()
    trunk = incoming(connector)
    connector.on_event(ev('Newchannel', Channel='PJSIP/6726-00000003', Uniqueid='1.101', Linkedid='1.100',
                          Context='from-internal', Exten='s'))
    connector.execute({'id': 21, 'kind': 'spy', 'ext': '6600', 'target_channel': 'PJSIP/6726-00000003',
                       'mode': 'whisper'})
    connector.execute({'id': 22, 'kind': 'hangup', 'channel': trunk})
    actions = [a[0] for a in fake_ami.actions if a[0]['Action'] != 'CoreShowChannels']
    assert actions[0]['Application'] == 'ChanSpy' and actions[0]['Data'] == 'PJSIP/6726-,qESw'
    assert actions[0]['Channel'] == 'PJSIP/6600'
    assert actions[1] == {'Action': 'Hangup', 'ActionID': 'otp-cmd-22', 'Channel': trunk, 'Cause': 16}
    assert [r['status'] for r in connector.results] == ['accepted', 'accepted']


def test_station_refusal_becomes_failed_result():
    connector, _session, _s, fake_ami, _clock = make_connector()
    fake_ami.reply = {'Response': 'Error', 'Message': 'Permission denied'}
    connector.execute({'id': 5, 'kind': 'originate', 'ext': '6726', 'dial': '6715'})
    assert connector.results[0] == {'command_id': 5, 'status': 'failed', 'message': 'Permission denied'}


def test_answer_triggers_recording_lookup_once():
    connector, session, _s, fake_ami, clock = make_connector()
    trunk = incoming(connector)
    connector.on_event(ev('Newchannel', Channel='PJSIP/6726-00000003', Uniqueid='1.101', Linkedid='1.100',
                          Context='from-internal', Exten='s'))
    connector.on_event(ev('AgentConnect', Channel=trunk, Uniqueid='1.100', Linkedid='1.100', Queue='3034',
                          Interface='Local/6726@from-queue/n', MemberName='Operator_One', HoldTime='4'))
    connector.on_event(ev('BridgeEnter', Channel='PJSIP/6726-00000003', Uniqueid='1.101', Linkedid='1.100'))
    assert list(connector.recording_todo) == ['1.100']
    assert connector.main_wake.is_set(), 'имя записи спрашивает поток обслуживания — его будим'
    connector.maintain_once()
    getvars = [a[0] for a in fake_ami.actions if a[0]['Action'] == 'Getvar']
    assert len(getvars) == 1
    assert getvars[0]['Channel'] == 'PJSIP/6726-00000003' and getvars[0]['Variable'] == 'CDR(recordingfile)'
    connector.uplink_once(woke=True, sleep=no_sleep)
    sent = session.posts[-1][1]['events'][0]
    assert sent['recording_file'].startswith('q-3034-')
    assert sent['state'] == 'talking'


def test_portal_outage_keeps_changes_and_backs_off():
    connector, session, _s, _ami, clock = make_connector()
    incoming(connector)
    session.fail = ConnectionError('down')
    attempts = []
    session.post = (lambda original: (lambda *a, **k: (attempts.append(1), original(*a, **k))[1]))(session.post)
    assert connector.uplink_once(woke=True, sleep=no_sleep) == 2.0
    # Новые события станции во время паузы не будят запрос к упавшему порталу.
    incoming(connector, lid='1.101')
    assert connector.uplink_once(woke=True, sleep=no_sleep) == pytest.approx(2.0)
    assert len(attempts) == 1, 'пауза после отказа обязательна'
    clock.now += 2.0
    assert connector.uplink_once(woke=True, sleep=no_sleep) == 5.0
    assert set(connector.pending) == {'1.100', '1.101'}, 'непринятое остаётся в очереди'
    session.fail = None
    clock.now += 5.0
    connector.uplink_once(woke=True, sleep=no_sleep)
    assert sorted(c['linkedid'] for c in session.posts[-1][1]['events']) == ['1.100', '1.101']
    assert connector.portal_failures == 0


def test_lines_are_read_and_sent_once():
    connector, session, _s, _ami, clock = make_connector(lines={'3333': {'number': '7475550033', 'name': 'OP OS'}})
    connector.maintain_once()
    connector.uplink_once(woke=True, sleep=no_sleep)
    assert session.posts[-1][1]['lines'] == {'3333': {'number': '7475550033', 'name': 'OP OS'}}
    clock.now += pbx_connector.FALLBACK_POLL_SECONDS
    connector.uplink_once(sleep=no_sleep)
    assert 'lines' not in session.posts[-1][1]


def test_ami_reconnect_starts_from_a_clean_slate_and_sends_a_snapshot():
    connector, session, _s, fake_ami, clock = make_connector()
    incoming(connector, lid='1.100')                 # звонок, отбой которого мы потеряем
    connector.uplink_once(woke=True, sleep=no_sleep)
    fake_ami.connected = False                       # AMI оборвался
    trunk = 'PJSIP/+77475550000_jana-00000011'
    fake_ami.items = [
        {'Channel': 'PJSIP/6726-00000012', 'Uniqueid': '3.2', 'Linkedid': '3.1', 'Context': 'macro-dial-one',
         'Exten': 's', 'CallerIDNum': '6726', 'Duration': '00:00:40', 'BridgeId': 'b1'},
        {'Channel': trunk, 'Uniqueid': '3.1', 'Linkedid': '3.1', 'Context': 'ext-queues', 'Exten': '3034',
         'CallerIDNum': '+77015550001', 'Duration': '00:01:05', 'BridgeId': 'b1'},
    ]
    connector.maintain_once()
    assert connector.snapshot_due is True and connector.authoritative is True
    connector.uplink_once(woke=True, sleep=no_sleep)
    payload = session.posts[-1][1]
    assert payload['snapshot'] is True
    assert [c['linkedid'] for c in payload['events']] == ['3.1'], 'потерянный звонок портал закроет по снимку'
    call = payload['events'][0]
    assert call['direction'] == 'in' and call['queue'] == '3034' and call['did'] == ''
    assert call['ext'] == '6726' and call['state'] == 'talking'
    assert call['started_at'] < call['answered_at'], 'начало — по Duration транка, ответ — сейчас'
    assert call['partial'] is True, 'восстановленное из списка не затирает на портале известное'
    assert trunk in connector.known_channels


def test_ami_down_is_reported_and_commands_fail_fast():
    connector, session, _s, fake_ami, clock = make_connector()
    fake_ami.connected = False

    def failing_connect():
        raise ami_mod.AmiError('AMI не принял вход: Authentication failed')
    fake_ami.connect = failing_connect
    connector.maintain_once()
    assert connector.ami_retry_at == clock.now + 5
    connector.uplink_once(woke=True, sleep=no_sleep)
    payload = session.posts[-1][1]
    assert payload['ami']['connected'] is False
    assert 'Authentication failed' in payload['ami']['error']
    connector.execute({'id': 8, 'kind': 'originate', 'ext': '6726', 'dial': '6715'})
    assert connector.results[-1]['status'] == 'failed'
    calls_before = len(fake_ami.actions)
    connector.maintain_once()                         # пауза перед повтором ещё не прошла
    assert len(fake_ami.actions) == calls_before


def test_parse_outbound_lines():
    rows = [('77475550033_OP_OS', '3333*'), ('77475550033_OP_OS', '3333*8'), ('77475550070_OLX', '0770*'),
            ('Chestniy', '0602*'), ('77005550050_Tenge', '5050*'), ('', '7777*')]
    lines = pbx_connector.parse_outbound_lines(rows)
    assert lines == {
        '3333': {'number': '7475550033', 'name': 'OP OS'},
        '0770': {'number': '7475550070', 'name': 'OLX'},
        '5050': {'number': '7005550050', 'name': 'Tenge'},
    }


def test_incomplete_channel_list_builds_nothing_and_sends_no_closing_snapshot():
    """Список каналов оборвался посреди выдачи: по нему нельзя ни строить состояние, ни слать
    снимок — портал закрыл бы им все живые звонки, и они бы уже не ожили."""
    connector, session, _s, fake_ami, clock = make_connector()
    incoming(connector)
    connector.uplink_once(woke=True, sleep=no_sleep)
    fake_ami.connected = False
    connector.on_ami_disconnect('обрыв')
    assert connector.authoritative is False
    fake_ami.list_complete = False
    connector.maintain_once()
    assert fake_ami.connected is False, 'неполный список — соединение рвётся, вход повторится'
    assert connector.ami_retry_at == clock.now + 5
    clock.now += 61
    connector.uplink_once(sleep=no_sleep)
    payload = session.posts[-1][1]
    assert payload['snapshot'] is False, 'без полного состояния — без снимка'
    assert payload['ami']['connected'] is False
    fake_ami.list_complete = True
    clock.now += 5
    connector.maintain_once()
    assert connector.authoritative is True


def test_hangup_during_the_channel_list_does_not_resurrect_the_call():
    connector, session, _s, fake_ami, clock = make_connector()
    fake_ami.connected = False
    connector.on_ami_disconnect('обрыв')
    trunk = 'PJSIP/+77475550000_jana-00000021'
    fake_ami.items = [{'Channel': trunk, 'Uniqueid': '4.1', 'Linkedid': '4.1', 'Context': 'ext-queues',
                       'Exten': '3034', 'CallerIDNum': '+77015550004', 'Duration': '00:00:30'}]
    original = fake_ami.action

    def listing_with_a_hangup_inside(fields, variables=None, timeout=10):
        if fields.get('Action') == 'CoreShowChannels':
            # Пока станция выдаёт список, клиент положил трубку.
            connector.on_event(ev('Hangup', Channel=trunk, Uniqueid='4.1', Linkedid='4.1', Cause='16'))
        return original(fields, variables, timeout)
    fake_ami.action = listing_with_a_hangup_inside
    connector.maintain_once()
    assert connector.authoritative is True
    assert '4.1' not in connector.tracker.calls
    assert trunk not in connector.known_channels


def test_reconcile_closes_a_call_whose_hangup_was_lost():
    connector, session, _s, fake_ami, clock = make_connector()
    trunk = incoming(connector)
    connector.uplink_once(woke=True, sleep=no_sleep)
    fake_ami.items = []                                  # на станции этого звонка уже нет
    clock.now += pbx_connector.RECONCILE_SECONDS
    connector.maintain_once()
    assert connector.pending['1.100']['state'] == 'ended'
    assert trunk not in connector.known_channels
    connector.uplink_once(woke=True, sleep=no_sleep)
    assert session.posts[-1][1]['events'][0]['state'] == 'ended'


def test_snapshot_carries_live_calls_and_only_recently_ended_ones():
    connector, session, _s, _ami, clock = make_connector()
    incoming(connector, lid='5.1')
    connector.on_event(ev('Hangup', Channel='PJSIP/+77475550000_jana-00000001', Uniqueid='5.1', Linkedid='5.1',
                          Cause='16'))
    incoming(connector, lid='5.2')
    old = connector.tracker.calls['5.1']
    old['ended_at'] = '2000-01-01T00:00:00'            # закончился давно — снимку он не нужен
    clock.now += 61
    connector.uplink_once(sleep=no_sleep)
    payload = session.posts[-1][1]
    assert payload['snapshot'] is True
    assert [c['linkedid'] for c in payload['events']] == ['5.2']


def test_station_refusal_forgets_the_pending_originate():
    connector, _session, _s, fake_ami, _clock = make_connector()
    fake_ami.reply = {'Response': 'Error', 'Message': 'Extension does not exist.'}
    connector.execute({'id': 31, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001'})
    assert connector.results[-1]['status'] == 'failed'
    assert 'otp-cmd-31' not in connector.tracker._pending_originates
