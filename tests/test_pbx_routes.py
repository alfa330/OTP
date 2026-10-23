# -*- coding: utf-8 -*-
"""Ручки раздела «АТС» (pbx/routes.py): коннектор и люди. База подменена двойником SQL-слоя,
Flask поднят своим приложением с фальшивыми зависимостями (приём tests/test_cdr_agent_routes.py).

Что закреплено:
  * коннектор без подписи — 401, с подписью — события чистятся и сохраняются, команды
    выдаются, итоги закрывают команды, снимок закрывает пропавшие звонки;
  * человек вне периметра «Касаний» — 403; без внутреннего номера звонить нельзя — 400;
  * набор строится как `<префикс>*7<10 цифр>`, линия берётся из звонка или последнего набора;
  * подключение к разговору — только руководству и только к идущему разговору;
  * отбой чужого звонка — только руководству.
"""
import json
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import directory as directory_mod  # noqa: E402
from cdr_bridge import signing  # noqa: E402
from pbx import queries, realtime as pbx_realtime, routes as pbx_routes  # noqa: E402

PRIVATE_B64, PUBLIC_B64, KEY_ID = signing.generate()

ADMIN = {'user_id': 1, 'name': 'Админ', 'role': 'super_admin', 'department_id': None,
         'department_code': None, 'headed_department_ids': [], 'headed_department_codes': []}
SV_OP = {'user_id': 2, 'name': 'СВ', 'role': 'sv', 'department_id': 5, 'department_code': 'op',
         'headed_department_ids': [], 'headed_department_codes': []}
MARKETING = {'user_id': 471, 'name': 'Маркетолог', 'role': 'marketing_manager', 'department_id': 9,
             'department_code': 'marketing', 'headed_department_ids': [], 'headed_department_codes': []}
OPERATOR = {'user_id': 7, 'name': 'Оператор', 'role': 'operator', 'department_id': 5, 'department_code': 'op',
            'headed_department_ids': [], 'headed_department_codes': []}


class _FakeCursor:
    def __init__(self):
        self.notified = []

    def execute(self, sql, params=None):
        if 'pg_notify' in str(sql):
            self.notified.append(json.loads(params[1]))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeDb:
    def __init__(self):
        self.cursor = _FakeCursor()

    def _get_cursor(self):
        @contextmanager
        def scope():
            yield self.cursor
        return scope()


def talking_call(**over):
    call = {
        'linkedid': '1.100', 'direction': 'in', 'phone': '7015550001', 'did': '7475550033', 'queue': '3034',
        'ext': '6726', 'ringing': [], 'state': 'talking', 'started_at': '2026-10-22T12:00:00',
        'queued_at': '2026-10-22T12:00:05', 'ringing_at': '2026-10-22T12:00:07',
        'answered_at': '2026-10-22T12:00:10', 'ended_at': '', 'wait_seconds': 5, 'hangup_cause': None,
        'hangup_side': '', 'dial_prefix': '', 'dial_status': '', 'channel': 'PJSIP/+77475550033_jana-1',
        'agent_channel': 'PJSIP/6726-00000002', 'recording_file': '', 'origin': '', 'command_id': None, 'seq': 4,
    }
    call.update(over)
    return call


class Recorder:
    """Двойник SQL-слоя: помнит, что раздел писал, и отдаёт то, что ему скажут."""

    def __init__(self):
        self.seen = []
        self.stored = []
        self.closed = None
        self.finished = []
        self.lines = None
        self.commands_to_claim = []
        self.enqueued = []
        self.state = {'last_seen_at': '2026-10-22T07:00:00+00:00', 'ami_connected': True, 'hostname': 'otp-gw',
                      'version': 'pbx/1.0.0', 'lines': {'3333': {'number': '7475550033', 'name': 'OP OS'},
                                                        '5050': {'number': '7005550050', 'name': 'Tenge'}}}
        self.live = []
        self.calls = {}
        self.ctx = ADMIN
        self.ext = '6600'
        self.last_prefix = ''
        self.directory = {'6726': {'periods': [{'name': 'Оператор Один', 'direction': 'Основа'}]}}

    # коннектор
    def connector_seen(self, cursor, **kwargs):
        self.seen.append(kwargs)
        return True

    def upsert_calls(self, cursor, calls):
        self.stored.extend(calls)
        return [c['linkedid'] for c in calls]

    def close_missing(self, cursor, known, now=None):
        self.closed = list(known)
        return 1

    def finish_command(self, cursor, command_id, status, message='', linkedid=None):
        self.finished.append((command_id, status, message, linkedid))
        return True

    def save_lines(self, cursor, lines):
        self.lines = lines

    def claim_commands(self, cursor, limit=10):
        out, self.commands_to_claim = self.commands_to_claim, []
        return out

    def cleanup_due(self, cursor, hours=12):
        return False

    def drop_expired(self, cursor):
        return 0

    # люди
    def load_access_context(self, cursor, user_id):
        return self.ctx

    def user_ext(self, cursor, user_id):
        return self.ext

    def connector_state(self, cursor):
        return dict(self.state)

    def live_calls(self, cursor, ended_within_seconds=90, now=None):
        return list(self.live)

    def call_by_linkedid(self, cursor, linkedid):
        return self.calls.get(linkedid)

    def last_prefix_for_ext(self, cursor, ext, days=30):
        return self.last_prefix

    def enqueue_command(self, cursor, kind, params, requested_by=None, linkedid=None):
        self.enqueued.append({'kind': kind, 'params': params, 'requested_by': requested_by, 'linkedid': linkedid})
        return len(self.enqueued)

    def command(self, cursor, command_id):
        return {'id': command_id, 'kind': 'originate', 'params': {}, 'requested_by': 1, 'status': 'accepted',
                'message': 'телефон звонит', 'linkedid': None}


class PbxRoutesTests(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder()
        patches = []
        for name in ('connector_seen', 'upsert_calls', 'close_missing', 'finish_command', 'save_lines',
                     'claim_commands', 'cleanup_due', 'drop_expired', 'load_access_context', 'user_ext',
                     'connector_state', 'live_calls', 'call_by_linkedid', 'last_prefix_for_ext',
                     'enqueue_command', 'command'):
            patches.append(mock.patch.object(queries, name, getattr(self.recorder, name)))
        patches.append(mock.patch.object(pbx_routes.schema, 'schema_is_ready', lambda cursor: True))
        patches.append(mock.patch.object(pbx_routes, '_schema_ready', {'ok': False}))
        # Слушатель реалтайма в тестах ручек не поднимается: хаб наполняет сам тест.
        patches.append(mock.patch.object(pbx_routes.realtime, 'ensure_listener', lambda *a, **k: None))
        patches.append(mock.patch.object(pbx_routes.cdr_config, 'agent_keys_raw', lambda: PUBLIC_B64))
        patches.append(mock.patch.object(pbx_routes.cdr_queries, 'load_directory',
                                         lambda cursor: self.recorder.directory))
        patches.append(mock.patch.object(pbx_routes.cdr_queries, 'line_queue_rows',
                                         lambda cursor, since: [('7475550033', '3034', 10)]))
        patches.append(mock.patch.object(pbx_routes.cdr_queries, 'now_almaty',
                                         lambda: __import__('datetime').datetime(2026, 10, 22, 12, 0, 30)))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        app = Flask(__name__)
        self.db = _FakeDb()
        # Часы хаба — те же 12:00:30: по настоящим звонок от 22.10 через сутки уже «повисший»
        # (LIVE_WINDOW), и хаб его не берёт.
        self.hub = pbx_realtime.Hub(local_now=lambda: datetime(2026, 10, 22, 12, 0, 30))
        self.hub.ready = True
        app.register_blueprint(pbx_routes.build_pbx_blueprint(
            db=self.db, require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.recorder.ctx['user_id'], None, None),
            listen_connect=lambda: None, hub=self.hub))
        app.config['TESTING'] = True
        self.client = app.test_client()
        self.signer = signing.Signer(signing.load_private_key(PRIVATE_B64))

    # ── коннектор ────────────────────────────────────────────────────────────

    def sync(self, payload, signed=True):
        body = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if signed:
            headers.update(self.signer.headers('POST', '/api/pbx/agent/sync', body))
        return self.client.post('/api/pbx/agent/sync', data=body, headers=headers)

    def test_unsigned_connector_is_refused_before_the_database(self):
        response = self.sync({'events': []}, signed=False)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.recorder.seen, [])

    def test_sync_cleans_events_and_hands_out_commands(self):
        self.recorder.commands_to_claim = [{'id': 3, 'kind': 'originate', 'ext': '6600', 'dial': '3333*77015550001'}]
        response = self.sync({
            'hostname': 'otp-gw', 'version': 'pbx/1.0.0/3.12',
            'ami': {'connected': True, 'host': '192.0.2.1:5038', 'error': None, 'events_total': 42},
            'events': [
                talking_call(phone='+7 701 555-00-01', hangup_side='nobody', state='talking'),
                {'linkedid': '', 'state': 'talking'},                       # без ключа — мусор
                talking_call(linkedid='2.1', state='hovering'),              # неизвестное состояние — мусор
                talking_call(linkedid='3.1', origin='portal', command_id=3),
            ],
            'results': [{'command_id': 9, 'status': 'failed', 'message': 'телефон не ответил'},
                        {'command_id': 'x', 'status': 'done'}],
            'lines': {'3333': {'number': '+77475550033', 'name': 'OP OS'}, 'bad': {'number': '7'}},
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['commands'], [{'id': 3, 'kind': 'originate', 'ext': '6600', 'dial': '3333*77015550001'}])
        self.assertEqual(body['stored'], 2)
        kinds = [n['k'] for n in self.db.cursor.notified]
        self.assertEqual(kinds, ['conn', 'calls'], 'зрителям — что коннектор жив и что звонки изменились')
        self.assertTrue(self.db.cursor.notified[0]['ami'])
        self.assertEqual([c['linkedid'] for c in self.recorder.stored], ['1.100', '3.1'])
        self.assertEqual(self.recorder.stored[0]['phone'], '7015550001')
        self.assertEqual(self.recorder.stored[0]['hangup_side'], '', 'чужое слово в стороне отбоя — пусто')
        self.assertEqual(self.recorder.stored[0]['started_at'].isoformat(), '2026-10-22T12:00:00')
        self.assertEqual(self.recorder.seen[0]['agent_key'], KEY_ID)
        self.assertEqual(self.recorder.seen[0]['ami']['events_total'], 42)
        self.assertEqual(self.recorder.lines, {'3333': {'number': '7475550033', 'name': 'OP OS'}})
        # Наш звонок привязал команду, итоги закрыли свою команду, мусорный итог пропущен.
        self.assertIn((3, 'accepted', '', '3.1'), self.recorder.finished)
        self.assertIn((9, 'failed', 'телефон не ответил', None), self.recorder.finished)
        self.assertEqual(len(self.recorder.finished), 2)
        self.assertIsNone(self.recorder.closed, 'без снимка пропавшие не закрываются')

    def test_snapshot_closes_calls_missing_from_it(self):
        self.sync({'snapshot': True, 'events': [talking_call()]})
        self.assertEqual(self.recorder.closed, ['1.100'])
        self.assertTrue(self.recorder.seen[0]['snapshot'])

    def test_too_many_events_is_a_bad_request(self):
        response = self.sync({'events': [talking_call()] * (pbx_routes.MAX_EVENTS + 1)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.recorder.stored, [])

    # ── люди ─────────────────────────────────────────────────────────────────

    def test_live_shows_calls_with_operator_names_and_my_rights(self):
        self.recorder.live = [talking_call()]
        response = self.client.get('/api/pbx/live')
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertTrue(body['connector']['connected'])
        self.assertTrue(body['connector']['online'])
        self.assertNotIn('lines', body['connector'])
        call = body['calls'][0]
        self.assertEqual(call['operator'], 'Оператор Один')
        self.assertEqual(call['park'], 'Jana такси')
        self.assertEqual(call['line_number'], '7475550033')
        self.assertTrue(call['has_agent_channel'])
        self.assertNotIn('agent_channel', call, 'имя канала — не для экрана')
        self.assertEqual(body['me'], {'ext': '6600', 'can_call': True, 'can_spy': True})
        self.assertEqual([line['prefix'] for line in body['lines']], ['3333', '5050'])
        self.assertEqual(body['lines'][0]['label'], 'Jana такси', 'линия подписана парком по очереди входящих')
        self.assertEqual(body['lines'][1]['label'], 'Tenge', 'незнакомая очередь — имя маршрута станции')

    def test_connector_silent_for_a_minute_is_offline(self):
        self.recorder.state['last_seen_at'] = '2026-10-22T06:58:00+00:00'   # 11:58 Алматы, сейчас 12:00:30
        body = self.client.get('/api/pbx/live').get_json()
        self.assertFalse(body['connector']['connected'])
        self.assertFalse(body['connector']['online'])

    def test_outsider_is_refused(self):
        self.recorder.ctx = OPERATOR
        self.assertEqual(self.client.get('/api/pbx/live').status_code, 403)
        self.assertEqual(self.client.post('/api/pbx/call', json={'phone': '7015550001'}).status_code, 403)

    def test_call_needs_my_extension(self):
        self.recorder.ext = ''
        response = self.client.post('/api/pbx/call', json={'phone': '7015550001', 'prefix': '3333'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('внутренний номер', response.get_json()['error'])
        self.assertEqual(self.recorder.enqueued, [])

    def test_call_builds_station_dial_from_prefix_and_phone(self):
        response = self.client.post('/api/pbx/call', json={'phone': '+7 (701) 555-00-01', 'prefix': '3333'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {'status': 'queued', 'command_id': 1, 'dial': '3333*77015550001'})
        command = self.recorder.enqueued[0]
        self.assertEqual(command['kind'], 'originate')
        self.assertEqual(command['params'], {'ext': '6600', 'dial': '3333*77015550001',
                                             'caller_name': '+7 701 555-00-01'})
        self.assertEqual(command['requested_by'], 1)

    def test_call_back_takes_the_line_of_the_incoming_call(self):
        self.recorder.calls['1.100'] = talking_call(did='+77475550033')
        response = self.client.post('/api/pbx/call', json={'phone': '7015550001', 'linkedid': '1.100'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['dial'], '3333*77015550001')

    def test_call_without_a_line_falls_back_to_my_last_route_or_asks(self):
        self.recorder.last_prefix = '5050'
        response = self.client.post('/api/pbx/call', json={'phone': '7015550001'})
        self.assertEqual(response.get_json()['dial'], '5050*77015550001')
        self.recorder.last_prefix = ''
        response = self.client.post('/api/pbx/call', json={'phone': '7015550001'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'PBX_LINE_NEEDED')

    def test_call_refuses_unknown_line_and_offline_connector(self):
        response = self.client.post('/api/pbx/call', json={'phone': '7015550001', 'prefix': '9999'})
        self.assertEqual(response.status_code, 400)
        self.recorder.state['last_seen_at'] = None
        response = self.client.post('/api/pbx/call', json={'phone': '7015550001', 'prefix': '3333'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'PBX_CONNECTOR_DOWN')
        self.assertEqual(self.recorder.enqueued, [])

    def test_spy_is_for_supervisors_and_only_into_a_talk(self):
        self.recorder.calls['1.100'] = talking_call()
        self.recorder.ctx = MARKETING
        self.assertEqual(self.client.post('/api/pbx/spy', json={'linkedid': '1.100'}).status_code, 403)
        self.recorder.ctx = SV_OP
        response = self.client.post('/api/pbx/spy', json={'linkedid': '1.100', 'mode': 'whisper'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.recorder.enqueued[-1]['kind'], 'spy')
        self.assertEqual(self.recorder.enqueued[-1]['params'],
                         {'ext': '6600', 'target_channel': 'PJSIP/6726-00000002', 'mode': 'whisper'})
        self.recorder.calls['1.100'] = talking_call(state='queued', agent_channel='')
        self.assertEqual(self.client.post('/api/pbx/spy', json={'linkedid': '1.100'}).status_code, 409)
        self.assertEqual(self.client.post('/api/pbx/spy', json={'linkedid': '1.100', 'mode': 'record'}).status_code, 400)

    def test_spy_into_my_own_talk_is_refused(self):
        self.recorder.ext = '6726'
        self.recorder.calls['1.100'] = talking_call()
        self.assertEqual(self.client.post('/api/pbx/spy', json={'linkedid': '1.100'}).status_code, 409)

    def test_hangup_own_call_or_as_supervisor(self):
        self.recorder.calls['1.100'] = talking_call()
        self.recorder.ctx = MARKETING
        self.recorder.ext = '6601'
        self.assertEqual(self.client.post('/api/pbx/hangup', json={'linkedid': '1.100'}).status_code, 403)
        self.recorder.ext = '6726'
        response = self.client.post('/api/pbx/hangup', json={'linkedid': '1.100'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.recorder.enqueued[-1]['params'], {'channel': 'PJSIP/6726-00000002'})
        self.recorder.ctx = SV_OP
        self.recorder.ext = '6600'
        self.assertEqual(self.client.post('/api/pbx/hangup', json={'linkedid': '1.100'}).status_code, 200)
        self.recorder.calls['1.100'] = talking_call(state='ended')
        self.assertEqual(self.client.post('/api/pbx/hangup', json={'linkedid': '1.100'}).status_code, 409)

    def test_command_status_is_readable_by_its_author(self):
        response = self.client.get('/api/pbx/commands/5')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'accepted')


class HubCatchUpTests(unittest.TestCase):
    """Догон с запасом (CATCH_UP_MARGIN) перечитывает уже разосланное. Давно законченный звонок,
    который память хаба отпустила, не должен возвращаться к зрителям на каждом тычке (прод 22.10:
    звонок с rev=1 приходил в поток снова через 145 и 153 с после отбоя)."""

    def setUp(self):
        self.now = [datetime(2026, 10, 22, 12, 0, 20)]
        self.hub = pbx_realtime.Hub(local_now=lambda: self.now[0])

    def published(self, after):
        events, _ = self.hub.wait(after, timeout=0)
        return [call for event in events for call in event.get('calls') or []]

    def test_expired_call_reread_by_catch_up_is_not_published_again(self):
        old = talking_call(rev=1, state='ended', ended_at='2026-10-22T12:00:15')
        self.assertEqual(self.hub.apply_calls([old]), 1)
        self.now[0] += timedelta(seconds=pbx_realtime.ENDED_KEEP_SECONDS + 5)
        self.hub.expire()
        after = self.hub.current_seq()
        fresh = talking_call(linkedid='2.2', rev=2, started_at='2026-10-22T12:02:20')
        self.assertEqual(self.hub.apply_calls([old, fresh]), 1, 'перечитанный давний звонок — не новость')
        self.assertEqual([c['linkedid'] for c in self.published(after)], ['2.2'])
        self.assertEqual(self.hub.rev, 2)
        after = self.hub.current_seq()
        self.assertEqual(self.hub.apply_calls([old]), 0)
        self.assertEqual(self.published(after), [], 'и на следующем тычке тоже')

    def test_known_call_closed_after_the_fact_is_still_published(self):
        self.hub.apply_calls([talking_call(rev=1)])
        self.now[0] += timedelta(minutes=10)
        after = self.hub.current_seq()
        # Сверка закрыла звонок задним числом: отбой давно за окном, но зрители держат его «в разговоре».
        closed = talking_call(rev=2, state='ended', ended_at='2026-10-22T12:01:00')
        self.assertEqual(self.hub.apply_calls([closed]), 1)
        self.assertEqual([c['state'] for c in self.published(after)], ['ended'])
        self.assertEqual(self.hub.active_calls(), [])


class CleanCallTests(unittest.TestCase):
    def test_clean_call_normalises_and_rejects(self):
        self.assertIsNone(queries.clean_call('мусор'))
        self.assertIsNone(queries.clean_call({'linkedid': '1.1', 'started_at': 'вчера'}))
        clean = queries.clean_call(talking_call(ringing=['6726', 6727, '6726'], command_id='12', direction='sideways'))
        self.assertEqual(clean['ringing'], ['6726', '6727'])
        self.assertEqual(clean['command_id'], 12)
        self.assertEqual(clean['direction'], '')
        self.assertEqual(clean['answered_at'].isoformat(), '2026-10-22T12:00:10')
        self.assertIsNone(clean['ended_at'])

    def test_directory_resolver_is_the_same_as_touches(self):
        resolve = directory_mod.resolver({'6726': {'periods': [{'name': 'Оператор Один', 'direction': 'Основа'}]}})
        self.assertEqual(resolve('6726', '2026-10-22T12:00:00')[0], 'Оператор Один')
        self.assertEqual(resolve('6999', '2026-10-22T12:00:00')[0], 'Неизвестный номер 6999')


if __name__ == '__main__':
    unittest.main()


class PbxStreamTests(PbxRoutesTests.__bases__[0]):
    """Живые каналы: канал команд коннектора и поток браузера."""

    setUp = PbxRoutesTests.setUp
    sync = PbxRoutesTests.sync

    def read_frames(self, response, count, limit=50, events_only=False):
        frames, buffer = [], ''
        iterator = iter(response.response)
        for _ in range(limit):
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            buffer += chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk
            while '\n\n' in buffer:
                frame, buffer = buffer.split('\n\n', 1)
                if events_only and frame.startswith(':'):
                    continue
                frames.append(frame)
            if len(frames) >= count:
                break
        return frames

    def test_sync_with_a_live_channel_gets_no_commands(self):
        self.recorder.commands_to_claim = [{'id': 3, 'kind': 'hangup', 'channel': 'PJSIP/6726-1'}]
        response = self.sync({'stream': True, 'events': []})
        self.assertEqual(response.get_json()['commands'], [])
        self.assertEqual(self.recorder.commands_to_claim, [{'id': 3, 'kind': 'hangup', 'channel': 'PJSIP/6726-1'}],
                         'команды остаются каналу — их не раздают дважды')

    def test_agent_stream_needs_a_signature_and_delivers_commands_at_once(self):
        self.assertEqual(self.client.post('/api/pbx/agent/stream', data=b'{}').status_code, 401)
        self.recorder.commands_to_claim = [{'id': 7, 'kind': 'originate', 'ext': '6726', 'dial': '3333*77015550001'}]
        body = json.dumps({'hostname': 'otp-gw', 'version': 'pbx/2.0.0'}).encode()
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', '/api/pbx/agent/stream', body))
        with mock.patch.object(pbx_routes, 'AGENT_HEARTBEAT_SECONDS', 0.2):
            response = self.client.post('/api/pbx/agent/stream', data=body, headers=headers, buffered=False)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'text/event-stream')
            frames = self.read_frames(response, 4)
            self.assertTrue(frames[0].startswith(': connected'))
            self.assertTrue(frames[1].startswith(': ping'))
            self.assertTrue(frames[2].startswith(': ping'), 'перед выдачей — байт в канал: обрыв виден до «sent»')
            self.assertEqual(frames[3].split('\n')[0], 'event: command')
            self.assertEqual(json.loads(frames[3].split('data: ', 1)[1])['id'], 7)
            self.assertEqual(self.hub.stream_count('agent'), 1)
            self.assertTrue(self.recorder.seen[0]['stream'], 'пульс канала отмечает коннектор живым')
            # Новый канал вытесняет прежний этого процесса.
            self.hub.new_agent_stream()
            rest = self.read_frames(response, 1, events_only=True)
            self.assertIn('event: bye', rest[-1])
            self.assertIn('superseded', rest[-1])
            response.close()
        self.assertEqual(self.hub.stream_count('agent'), 0, 'место канала возвращено')

    def test_agent_stream_wakes_up_on_a_new_command(self):
        body = b'{}'
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', '/api/pbx/agent/stream', body))
        with mock.patch.object(pbx_routes, 'AGENT_HEARTBEAT_SECONDS', 30):
            response = self.client.post('/api/pbx/agent/stream', data=body, headers=headers, buffered=False)
            self.read_frames(response, 2)                     # connected + ping
            self.recorder.commands_to_claim = [{'id': 9, 'kind': 'hangup', 'channel': 'PJSIP/6726-1'}]
            threading.Timer(0.1, self.hub.poke_commands).start()
            started = time.monotonic()
            frames = self.read_frames(response, 1, events_only=True)
            self.assertLess(time.monotonic() - started, 5, 'команда уходит по тычку, а не по пульсу')
            self.assertIn('"id":9', frames[0])
            response.close()

    def test_browser_stream_sends_state_then_changes(self):
        self.hub.load_full([dict(talking_call(), rev=5, started_at='2026-10-22T12:00:00')],
                           {'seen': time.time(), 'ami': True}, 5)
        with mock.patch.object(pbx_routes, 'STREAM_HEARTBEAT_SECONDS', 0.2):
            response = self.client.get('/api/pbx/stream', buffered=False)
            self.assertEqual(response.status_code, 200)
            frames = self.read_frames(response, 1)
            self.assertTrue(frames[0].startswith('event: state'))
            state = json.loads(frames[0].split('data: ', 1)[1])
            self.assertEqual([c['linkedid'] for c in state['calls']], ['1.100'])
            self.assertEqual(state['calls'][0]['operator'], 'Оператор Один')
            self.assertNotIn('agent_channel', state['calls'][0])
            self.assertTrue(state['connector']['online'])
            self.assertEqual(state['me']['ext'], '6600')
            self.hub.apply_calls([dict(talking_call(), rev=6, state='ended', ended_at='2026-10-22T12:00:40',
                                       hangup_side='client')])
            frames = self.read_frames(response, 1)
            self.assertTrue(frames[0].startswith('event: calls'))
            changed = json.loads(frames[0].split('data: ', 1)[1])['calls'][0]
            self.assertEqual((changed['state'], changed['hangup_side'], changed['rev']), ('ended', 'client', 6))
            response.close()
        self.assertEqual(self.hub.stream_count('browser'), 0)

    def test_browser_stream_is_refused_beyond_its_limit_and_outside_the_perimeter(self):
        self.recorder.ctx = OPERATOR
        self.assertEqual(self.client.get('/api/pbx/stream').status_code, 403)
        self.recorder.ctx = ADMIN
        for _ in range(10):
            self.assertTrue(self.hub.acquire('browser', 10))
        response = self.client.get('/api/pbx/stream')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get('Retry-After'), '120')

    def test_commands_poke_the_channel(self):
        self.client.post('/api/pbx/call', json={'phone': '7015550001', 'prefix': '3333'})
        self.assertIn({'k': 'cmd'}, self.db.cursor.notified)
