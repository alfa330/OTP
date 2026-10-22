# -*- coding: utf-8 -*-
"""Коннектор АТС (cdr_bridge/pbx_connector.py, cdr_bridge/ami.py) — без станции и без портала:
AMI подменён, HTTP подменён. События — как у Asterisk 16 на станции.

Что закреплено:
  * изменения звонков уезжают пачкой, итоги команд — в следующем запросе и один раз;
  * команды из ответа портала исполняются ровно теми действиями AMI, что описаны в ami.py;
  * чужой канал положить нельзя, кривой набор не уходит на станцию;
  * неудачный Originate (телефон не ответил) даёт итог failed с человеческой причиной;
  * портал упал — изменения не теряются, пауза растёт;
  * клиент AMI: вход, ответ по ActionID, списковый ответ, обрыв отпускает ждущих.
"""
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
        elif action == 'Getvar':
            self.sock.sendall(ami_mod.encode_action({'Response': 'Success', 'ActionID': action_id,
                                                     'Variable': message.get('Variable'),
                                                     'Value': 'out-77015550001-6726-20261022-120000-1.1.wav'}))
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
    deadline = threading.Event()
    for _ in range(50):
        if events:
            break
        deadline.wait(0.02)
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
    station.sock.close()
    for _ in range(100):
        if dropped:
            break
        threading.Event().wait(0.02)
    assert not ami.connected
    assert dropped, 'обрыв должен быть доложен'
    with pytest.raises(ami_mod.AmiError):
        ami.action({'Action': 'Ping'})


# ── коннектор ────────────────────────────────────────────────────────────────

class FakeAmi:
    def __init__(self):
        self.connected = True
        self.actions = []
        self.reply = {'Response': 'Success', 'Message': 'Originate successfully queued'}
        self.last_rx = 10 ** 12
        self.on_event = None

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def action(self, fields, variables=None, timeout=10):
        self.actions.append((dict(fields), dict(variables or {})))
        if fields.get('Action') == 'CoreShowChannels':
            return {'Response': 'Success', '_items': []}
        if fields.get('Action') == 'Getvar':
            return {'Response': 'Success', 'Value': 'q-3034-77015550001-20261022-120000-1.1.wav'}
        return dict(self.reply)

    def ping(self):
        return True

    def idle_seconds(self, now=None):
        return 0


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {'status': 'ok', 'commands': []}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self):
        self.posts = []
        self.replies = []
        self.fail = None

    def post(self, url, data=None, timeout=None, headers=None):
        if self.fail:
            raise self.fail
        import json
        self.posts.append((url, json.loads(data), headers))
        return self.replies.pop(0) if self.replies else FakeResponse()


def make_connector(clock=None, lines=None):
    config = {'portal': 'https://portal.example', 'private_key': PRIVATE_B64, 'key_file': '', 'proxy': '',
              'ca_bundle': '', 'ami_host': '192.0.2.1', 'ami_port': 5038, 'ami_user': 'stats',
              'ami_secret': 'x', 'poll_seconds': 2.0, 'snapshot_seconds': 60.0, 'recording_lookup': True,
              'heartbeat_file': ''}
    state = {'now': 1000.0}
    session = FakeSession()
    fake_ami = FakeAmi()
    connector = pbx_connector.Connector(
        config, session=session, ami_factory=lambda: fake_ami, clock=lambda: state['now'],
        sleep=lambda s: None, lines_reader=lambda cfg: (lines or {}, ''))
    connector.last_snapshot = state['now']
    return connector, session, fake_ami, state


def incoming(connector, lid='1.100'):
    trunk = 'PJSIP/+77475550000_jana-00000001'
    connector.on_event(ev('Newchannel', Channel=trunk, Uniqueid=lid, Linkedid=lid, Context='from-pstn',
                          Exten='7475550000', CallerIDNum='+77015550001'))
    connector.on_event(ev('QueueCallerJoin', Channel=trunk, Uniqueid=lid, Linkedid=lid, Queue='3034'))
    return trunk


def test_changes_go_in_one_batch_and_results_are_sent_once():
    connector, session, fake_ami, state = make_connector()
    incoming(connector)
    assert set(connector.pending) == {'1.100'}
    pause = connector.step()
    assert pause == 2.0
    url, payload, headers = session.posts[-1]
    assert url.endswith('/api/pbx/agent/sync')
    assert headers['X-Agent-Key'] == KEY_ID
    assert payload['snapshot'] is False
    assert [c['linkedid'] for c in payload['events']] == ['1.100']
    assert payload['events'][0]['state'] == 'queued'
    assert payload['ami'] == {'connected': True, 'host': '192.0.2.1:5038', 'error': None, 'events_total': 2}
    assert fake_ami.actions[0][0]['Action'] == 'CoreShowChannels', 'при входе — снимок каналов'
    # Тишина две секунды — пульс без событий.
    state['now'] += 2.0
    connector.step()
    assert session.posts[-1][1]['events'] == []
    assert session.posts[-1][1]['results'] == []


def test_originate_command_is_executed_and_reported():
    connector, session, fake_ami, state = make_connector()
    session.replies.append(FakeResponse(body={'commands': [
        {'id': 15, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001', 'caller_name': '+7 701 555-00-01'}]}))
    connector.step()
    originate = [a for a in fake_ami.actions if a[0].get('Action') == 'Originate']
    assert len(originate) == 1
    fields, variables = originate[0]
    assert fields['Channel'] == 'PJSIP/6726' and fields['Exten'] == '3333*77015550001'
    assert fields['ActionID'] == 'otp-cmd-15'
    assert variables == {'OTP_COMMAND': '15'}
    assert list(connector.results) == [{'command_id': 15, 'status': 'accepted',
                                        'message': 'телефон 6726 звонит, после ответа набор 3333*77015550001'}]
    # Итог уезжает следующим запросом и только один раз.
    connector.step()
    assert session.posts[-1][1]['results'][0]['status'] == 'accepted'
    assert connector.results == pbx_connector.deque()
    state['now'] += 2.0
    connector.step()
    assert session.posts[-1][1]['results'] == []
    # Станция ответила: телефон не взяли.
    connector.on_event(ev('OriginateResponse', ActionID='otp-cmd-15', Response='Failure', Reason='3',
                          Uniqueid='<null>'))
    assert list(connector.results) == [{'command_id': '15', 'status': 'failed', 'message': 'телефон не ответил'}]


def test_originate_success_marks_the_call_as_ours():
    connector, session, fake_ami, state = make_connector()
    session.replies.append(FakeResponse(body={'commands': [
        {'id': 16, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001'}]}))
    connector.step()
    connector.on_event(ev('OriginateResponse', ActionID='otp-cmd-16', Response='Success', Uniqueid='2.1',
                          Channel='PJSIP/6726-00000002', Exten='3333*77015550001'))
    connector.on_event(ev('Newchannel', Channel='PJSIP/6726-00000002', Uniqueid='2.1', Linkedid='2.1',
                          Context='from-internal', Exten='', CallerIDNum='6726'))
    call = connector.pending['2.1']
    assert call['origin'] == 'portal' and call['command_id'] == 16
    assert [r['status'] for r in connector.results] == ['accepted', 'done']
    assert connector.results[1]['linkedid'] == '2.1'


def test_bad_dial_and_unknown_channel_do_not_reach_the_station():
    connector, session, fake_ami, state = make_connector()
    session.replies.append(FakeResponse(body={'commands': [
        {'id': 1, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001; Action: Command'},
        {'id': 2, 'kind': 'hangup', 'channel': 'PJSIP/6726-00000009'},
        {'id': 3, 'kind': 'teleport'},
    ]}))
    connector.step()
    assert [a[0]['Action'] for a in fake_ami.actions] == ['CoreShowChannels']
    assert [(r['command_id'], r['status']) for r in connector.results] == [(1, 'failed'), (2, 'failed'), (3, 'failed')]
    assert 'уже нет' in connector.results[1]['message']


def test_hangup_and_spy_use_channels_the_connector_has_seen():
    connector, session, fake_ami, state = make_connector()
    trunk = incoming(connector)
    connector.on_event(ev('Newchannel', Channel='PJSIP/6726-00000003', Uniqueid='1.101', Linkedid='1.100',
                          Context='from-internal', Exten='s'))
    session.replies.append(FakeResponse(body={'commands': [
        {'id': 21, 'kind': 'spy', 'ext': '6600', 'target_channel': 'PJSIP/6726-00000003', 'mode': 'whisper'},
        {'id': 22, 'kind': 'hangup', 'channel': trunk},
    ]}))
    connector.step()
    actions = [a[0] for a in fake_ami.actions if a[0]['Action'] != 'CoreShowChannels']
    assert actions[0]['Application'] == 'ChanSpy' and actions[0]['Data'] == 'PJSIP/6726-,qESw'
    assert actions[0]['Channel'] == 'PJSIP/6600'
    assert actions[1] == {'Action': 'Hangup', 'ActionID': 'otp-cmd-22', 'Channel': trunk, 'Cause': 16}
    assert [r['status'] for r in connector.results] == ['accepted', 'accepted']


def test_station_refusal_becomes_failed_result():
    connector, session, fake_ami, state = make_connector()
    fake_ami.reply = {'Response': 'Error', 'Message': 'Permission denied'}
    session.replies.append(FakeResponse(body={'commands': [
        {'id': 5, 'kind': 'originate', 'ext': '6726', 'dial': '6715'}]}))
    connector.step()
    assert connector.results[0] == {'command_id': 5, 'status': 'failed', 'message': 'Permission denied'}


def test_answer_triggers_recording_lookup_once():
    connector, session, fake_ami, state = make_connector()
    trunk = incoming(connector)
    connector.on_event(ev('Newchannel', Channel='PJSIP/6726-00000003', Uniqueid='1.101', Linkedid='1.100',
                          Context='from-internal', Exten='s'))
    connector.on_event(ev('AgentConnect', Channel=trunk, Uniqueid='1.100', Linkedid='1.100', Queue='3034',
                          Interface='Local/6726@from-queue/n', MemberName='Operator_One', HoldTime='4'))
    connector.on_event(ev('BridgeEnter', Channel='PJSIP/6726-00000003', Uniqueid='1.101', Linkedid='1.100'))
    assert list(connector.recording_todo) == ['1.100']
    connector.step()
    getvars = [a[0] for a in fake_ami.actions if a[0]['Action'] == 'Getvar']
    assert len(getvars) == 1
    assert getvars[0]['Channel'] == 'PJSIP/6726-00000003' and getvars[0]['Variable'] == 'CDR(recordingfile)'
    sent = session.posts[-1][1]['events'][0]
    assert sent['recording_file'].startswith('q-3034-')
    assert sent['state'] == 'talking'


def test_snapshot_every_minute_sends_everything_known():
    connector, session, fake_ami, state = make_connector()
    incoming(connector)
    connector.step()
    state['now'] += 61
    connector.step()
    payload = session.posts[-1][1]
    assert payload['snapshot'] is True
    assert [c['linkedid'] for c in payload['events']] == ['1.100']


def test_portal_outage_keeps_changes_and_backs_off():
    connector, session, fake_ami, state = make_connector()
    incoming(connector)
    session.fail = ConnectionError('down')
    assert connector.step() == 5
    assert connector.step() == 15
    assert set(connector.pending) == {'1.100'}, 'непринятое остаётся в очереди'
    session.fail = None
    connector.step()
    assert [c['linkedid'] for c in session.posts[-1][1]['events']] == ['1.100']
    assert connector.portal_failures == 0


def test_lines_are_read_and_sent_once():
    connector, session, fake_ami, state = make_connector(lines={'3333': {'number': '7475550033', 'name': 'OP OS'}})
    connector.step()
    assert session.posts[-1][1]['lines'] == {'3333': {'number': '7475550033', 'name': 'OP OS'}}
    state['now'] += 2
    connector.step()
    assert 'lines' not in session.posts[-1][1]


def test_bootstrap_takes_running_calls_from_coreshowchannels():
    connector, session, fake_ami, state = make_connector()
    trunk = 'PJSIP/+77475550000_jana-00000011'
    items = [
        {'Channel': 'PJSIP/6726-00000012', 'Uniqueid': '3.2', 'Linkedid': '3.1', 'Context': 'macro-dial-one',
         'Exten': 's', 'CallerIDNum': '6726', 'Duration': '00:00:40', 'BridgeId': 'b1'},
        {'Channel': trunk, 'Uniqueid': '3.1', 'Linkedid': '3.1', 'Context': 'ext-queues', 'Exten': '3034',
         'CallerIDNum': '+77015550001', 'Duration': '00:01:05', 'BridgeId': 'b1'},
    ]
    fake_ami.action = lambda fields, variables=None, timeout=10: (
        {'Response': 'Success', '_items': items} if fields.get('Action') == 'CoreShowChannels'
        else {'Response': 'Success'})
    connector.step()
    call = session.posts[-1][1]['events'][0]
    assert call['linkedid'] == '3.1' and call['direction'] == 'in'
    assert call['phone'] == '7015550001'
    assert call['ext'] == '6726' and call['state'] == 'talking'
    assert call['started_at'] < call['answered_at'], 'начало — по Duration транка, ответ — сейчас'
    assert trunk in connector.known_channels


def test_parse_outbound_lines():
    rows = [('77475550033_OP_OS', '3333*'), ('77475550033_OP_OS', '3333*8'), ('77475550070_OLX', '0770*'),
            ('Chestniy', '0602*'), ('77005550050_Tenge', '5050*'), ('', '7777*')]
    lines = pbx_connector.parse_outbound_lines(rows)
    assert lines == {
        '3333': {'number': '7475550033', 'name': 'OP OS'},
        '0770': {'number': '7475550070', 'name': 'OLX'},
        '5050': {'number': '7005550050', 'name': 'Tenge'},
    }


def test_ami_down_is_reported_and_commands_fail_fast():
    connector, session, fake_ami, state = make_connector()
    fake_ami.connected = False

    def failing_connect():
        raise ami_mod.AmiError('AMI не принял вход: Authentication failed')
    fake_ami.connect = failing_connect
    session.replies.append(FakeResponse(body={'commands': [{'id': 8, 'kind': 'originate', 'ext': '6726', 'dial': '6715'}]}))
    pause = connector.step()
    assert pause == 5
    payload = session.posts[-1][1]
    assert payload['ami']['connected'] is False
    assert 'Authentication failed' in payload['ami']['error']
    assert connector.results[0]['status'] == 'failed'
