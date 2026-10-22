# -*- coding: utf-8 -*-
"""«Табло ОП» в реальном времени: живые звонки станции (коннектор АТС) поверх статусов телефонов.

Что закреплено:
  * разговор на станции — «В разговоре» с момента ответа, даже если телефон шлёт «готов» или
    «перерыв»; исходящий набор и заявка автообзвона с линией оператора — «Соединение»;
    у внутреннего звонка в разговоре оба; законченный и ждущий оператора автообзвон — ничего;
  * «В очереди» — только входящие, вставшие в очередь и не принятые; «ждут дольше всех» — от
    входа самого раннего; по группе — через хозяина очереди; коннектор не на связи — прочерки;
  * «сейчас» отдела и групп считается по живому статусу;
  * кадр потока (`live_patch`) совпадает со снимком, собранным в тот же момент, цифра в цифру;
  * смена статуса телефона встаёт в снимок без пересчёта дня;
  * поток /live: первым кадром «сейчас», изменение звонка — новый кадр, касания — `refresh` и
    сброс кэша, статус человека со стены — одним запросом на всех зрителей; чужому — 403.
"""
import json
import os
import sys
import time
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from unittest import mock

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_wallboard import routes as op_routes, snapshot as S  # noqa: E402
from pbx import realtime as pbx_realtime  # noqa: E402

STATUS_CATALOG = {
    'занят': ('В разговоре', 'talking', 10),
    'соединение': ('Соединение', 'connecting', 15),
    'готов': ('Активный', 'free', 20),
    'перезвон': ('Исход', 'outgoing', 30),
    'перерыв': ('Перерыв', 'break', 60),
    'выключен': ('Не в сети', 'offline', 70),
}


def status_entry(key):
    return STATUS_CATALOG.get(str(key or '').lower(), ('Нет событий', 'unknown', 90))


NOW = datetime(2026, 10, 22, 12, 1, 0)


def live_call(linkedid, **over):
    base = {'linkedid': linkedid, 'direction': 'in', 'phone': '7015550001', 'queue': '3034', 'ext': '',
            'peer_ext': '', 'ringing': [], 'state': 'queued', 'started_at': '2026-10-22T12:00:00',
            'queued_at': '2026-10-22T12:00:10', 'ringing_at': '', 'answered_at': '', 'ended_at': ''}
    base.update(over)
    return base


MEMBERSHIPS = {
    1: {'group_id': 36, 'group_name': 'группа Основа', 'model': 'op_osnova'},
    2: {'group_id': 15, 'group_name': 'группа ЯР', 'model': 'op_yandex_reg'},
    3: {'group_id': 36, 'group_name': 'группа Основа', 'model': 'op_osnova'},
}
PEOPLE = [{'id': 1, 'name': 'Основин', 'sip_number': '6650'},
          {'id': 2, 'name': 'Яров', 'sip_number': '6651'},
          {'id': 3, 'name': 'Третий', 'sip_number': '6652'}]
STATUSES = {1: {'status_key': 'готов', 'seconds': 50, 'event_at': '2026-10-22 12:00:10'},
            2: {'status_key': 'перерыв', 'seconds': 300, 'event_at': '2026-10-22 11:56:00'},
            3: {'status_key': 'готов', 'seconds': 20, 'event_at': '2026-10-22 12:00:40'}}


def snapshot(calls, online=True, statuses=None):
    return S.assemble(day=date(2026, 10, 22), touches=[], people=PEOPLE,
                      live_statuses=dict(statuses or STATUSES), status_entry=status_entry,
                      resolve_name=None, bridge_state=None, now=NOW, memberships=MEMBERSHIPS,
                      queue_owners={'3034': 36}, phone_events=[], live_calls=calls, live_online=online)


class OverlayTests(unittest.TestCase):
    def test_talking_on_the_station_beats_the_phone_status(self):
        overlay = S.live_overlay([
            live_call('1', state='talking', ext='6650', answered_at='2026-10-22T12:00:20'),
            live_call('2', direction='out', state='ringing', ext='6651', queue='', queued_at='',
                      ringing_at='2026-10-22T12:00:50'),
        ])
        self.assertEqual(overlay['6650'], {'status_key': 'talking', 'status_label': 'В разговоре',
                                           'since': '2026-10-22T12:00:20', 'direction': 'in'})
        self.assertEqual(overlay['6651']['status_key'], 'connecting')
        self.assertEqual(overlay['6651']['since'], '2026-10-22T12:00:50')

    def test_autodial_counts_only_once_it_holds_the_operator(self):
        waiting = live_call('1', direction='autodial', queue='3016', state='queued')
        reserved = live_call('2', direction='autodial', queue='3016', state='ringing', ext='6652',
                             ringing_at='2026-10-22T12:00:55')
        self.assertEqual(S.live_overlay([waiting]), {}, 'заявка ждёт оператора — у людей ничего не меняется')
        self.assertEqual(S.live_overlay([reserved])['6652']['status_key'], 'connecting')
        self.assertEqual(S.waiting_calls([waiting, reserved]), [], 'заявки автообзвона — не очередь клиентов')

    def test_internal_call_puts_both_on_the_line_and_talking_wins(self):
        overlay = S.live_overlay([
            live_call('1', direction='internal', state='talking', ext='6650', peer_ext='6651',
                      answered_at='2026-10-22T12:00:30'),
            live_call('2', direction='out', state='new', ext='6650', queue='', queued_at=''),
            live_call('3', state='ended', ext='6652', answered_at='2026-10-22T12:00:20'),
        ])
        self.assertEqual(set(overlay), {'6650', '6651'})
        self.assertEqual(overlay['6650']['status_key'], 'talking')

    def test_queue_counts_waiting_incoming_from_the_earliest_entry(self):
        calls = [live_call('1', queued_at='2026-10-22T12:00:40'),
                 live_call('2', state='ringing', ringing=['6650'], queued_at='2026-10-22T12:00:10'),
                 live_call('3', state='talking', ext='6651', answered_at='2026-10-22T12:00:30'),
                 live_call('4', state='new', queued_at='')]                # ещё в IVR
        waiting = S.waiting_calls(calls)
        self.assertEqual([c['linkedid'] for c in waiting], ['1', '2'])
        self.assertEqual(S.queue_fields(waiting, NOW, True),
                         {'queue': 2, 'queue_max_wait_seconds': 50, 'queue_since': '2026-10-22T12:00:10'})
        self.assertEqual(S.queue_fields([], NOW, True), {'queue': 0, 'queue_max_wait_seconds': 0, 'queue_since': None})
        self.assertEqual(S.queue_fields(waiting, NOW, False),
                         {'queue': None, 'queue_max_wait_seconds': None, 'queue_since': None},
                         'коннектор не на связи — «нет данных», а не «пусто»')


class SnapshotTests(unittest.TestCase):
    CALLS = [live_call('1', state='talking', ext='6651', answered_at='2026-10-22T12:00:20'),
             live_call('2', queued_at='2026-10-22T12:00:30'),
             live_call('3', queue='3099', queued_at='2026-10-22T12:00:45')]

    def test_now_follows_the_live_status(self):
        snap = snapshot(self.CALLS)
        rows = {row['id']: row for row in snap['operators']}
        self.assertEqual(rows[2]['status_key'], 'break', 'статус телефона остаётся в строке')
        self.assertEqual(rows[2]['live']['status_key'], 'talking')
        self.assertIsNone(rows[1]['live'])
        now = snap['now']
        self.assertEqual((now['operators_talking'], now['operators_on_break'], now['operators_free']), (1, 0, 2))
        self.assertEqual(now['operators_online'], 3)
        self.assertEqual((now['queue'], now['queue_max_wait_seconds']), (2, 30))
        groups = {g['label']: g for g in snap['groups']}
        self.assertEqual(groups['Основа']['now']['queue'], 1, 'очередь 3034 — Основы, 3099 — ничья')
        self.assertEqual(groups['ЯР']['now']['queue'], 0)
        self.assertEqual(groups['ЯР']['now']['operators_talking'], 1)
        self.assertEqual(snap['queue_groups'], {'3034': 36})
        self.assertEqual(snap['live'], {'online': True, 'at': '2026-10-22T12:01:00'})

    def test_offline_connector_leaves_the_phone_statuses_alone(self):
        snap = snapshot(self.CALLS, online=False)
        self.assertTrue(all(row['live'] is None for row in snap['operators']))
        self.assertEqual(snap['now']['operators_on_break'], 1)
        self.assertIsNone(snap['now']['queue'])

    def test_live_patch_equals_the_snapshot_taken_at_the_same_moment(self):
        base = snapshot([])                             # снимок без звонков (как в кэше)
        later = snapshot(self.CALLS)                    # что собрал бы снимок сейчас
        patch = S.live_patch(base, self.CALLS, True, NOW)
        self.assertEqual(patch['now'], later['now'])
        self.assertEqual(patch['groups_now'], {str(g['id']): g['now'] for g in later['groups']})
        self.assertEqual({key: value['live'] for key, value in patch['operators'].items()},
                         {S.row_key(row): row['live'] for row in later['operators']})
        self.assertEqual(patch['operators']['2']['status_key'], 'break')
        self.assertEqual((patch['at'], patch['online']), ('2026-10-22T12:01:00', True))

    def test_status_change_lands_in_the_snapshot_without_a_recount(self):
        snap = snapshot(self.CALLS)
        found = S.set_status(snap, 3, status_entry('перерыв'),
                             {'status_key': 'перерыв', 'seconds': 0, 'event_at': '2026-10-22 12:01:00'})
        self.assertTrue(found)
        row = next(r for r in snap['operators'] if r['id'] == 3)
        self.assertEqual((row['status_key'], row['status_at']), ('break', '2026-10-22 12:01:00'))
        self.assertEqual(snap['now']['operators_on_break'], 1)
        self.assertEqual(snap['now']['queue'], 2, 'очередь при смене статуса не теряется')
        self.assertFalse(S.set_status(snap, 99, status_entry('готов'), {}))


class _FakeDb:
    def get_departments(self):
        return [{'id': 7, 'code': 'op'}]

    def get_user_department_id(self, user_id):
        return 7 if user_id == 20 else None

    def _get_cursor(self):
        @contextmanager
        def scope():
            yield object()
        return scope()


class LiveStreamTests(unittest.TestCase):
    def setUp(self):
        self.requester = {'id': 20, 'role': 'sv'}
        self.calls = []
        self.status_reads = []
        self.statuses = {1: {'status_key': 'готов', 'seconds': 5, 'event_at': '2026-10-22 12:00:55'},
                         2: {'status_key': 'готов', 'seconds': 5, 'event_at': '2026-10-22 12:00:55'}}
        for name, value in (('day_touches_compact', lambda cursor, day: []),
                            ('agent_state', lambda cursor: {'connected': True, 'live_at': None, 'last_seen_at': None}),
                            ('load_directory', lambda cursor: {})):
            patcher = mock.patch.object(op_routes.queries, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (('load_phone_events', lambda cursor, ids, day, lookback_hours=0: []),
                            ('load_group_memberships', lambda cursor, ids, day: {}),
                            ('load_queue_answers', lambda cursor, day, days=7: []),
                            ('load_announcement_deltas', lambda cursor, day, days=7: []),
                            ('load_lead_deals', lambda cursor, day: []),
                            ('load_lead_touches', lambda cursor, day: []),
                            ('load_lead_sync_age', lambda cursor, day: 0)):
            patcher = mock.patch.object(op_routes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(op_routes.pbx_realtime if hasattr(op_routes, 'pbx_realtime') else pbx_realtime,
                                    'ensure_listener', lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        def storing_cache(*, cache, fetch, **_ignored):
            if cache.get('payload') is None or time.time() - cache['ts'] >= 10:
                cache.update(payload=fetch(), ts=time.time())
                self.fetches += 1
            return dict(cache['payload'], stale=False, age_seconds=0)

        def live_statuses(ids):
            self.status_reads.append(list(ids))
            return {i: self.statuses[i] for i in ids if i in self.statuses}

        self.fetches = 0
        self.hub = pbx_realtime.Hub()
        self.hub.ready = True
        self.hub.apply_connector({'seen': time.time(), 'ami': True})
        db = _FakeDb()
        department_id = op_routes.make_department_resolver(db)
        guard = op_routes.make_guard(
            db=db, department_id=department_id,
            get_authenticated_requester=lambda: (self.requester['id'], (None, None, None, self.requester['role']), None),
            normalize_user_role=lambda role: role, is_global_admin_requester=lambda role, uid: False,
            headed_department_id=lambda uid: None, is_supervisor_role=lambda role: role == 'sv')
        self.bp = op_routes.build_op_wallboard_blueprint(
            db=db, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            guard=guard, department_id=department_id, snapshot_with_cache=storing_cache,
            restore_cache=lambda *a, **k: None, persist_cache=lambda *a, **k: None, status_entry=status_entry,
            load_people=lambda dept, day: [{'id': 1, 'name': 'Иванов', 'sip_number': '6650', 'role': 'operator'},
                                           {'id': 2, 'name': 'Петров', 'sip_number': '6651', 'role': 'operator'}],
            live_statuses=live_statuses, listen_connect=lambda: None, live_hub=self.hub,
            live_loader=lambda: (list(self.calls), True))
        app = Flask(__name__)
        app.register_blueprint(self.bp)
        app.config['TESTING'] = True
        self.client = app.test_client()

    def frames(self, response, count):
        out, buffer = [], ''
        iterator = iter(response.response)
        while len(out) < count:
            chunk = next(iterator)
            buffer += chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk
            while '\n\n' in buffer:
                frame, buffer = buffer.split('\n\n', 1)
                if not frame.startswith(':'):
                    event = frame.split('\n')[0].split(': ', 1)[1]
                    data = json.loads(frame.split('data: ', 1)[1])
                    out.append((event, data))
        return out

    def test_outsider_is_refused(self):
        self.requester.update(id=30, role='operator')
        self.assertEqual(self.client.get('/api/op_wallboard/live').status_code, 403)

    def test_live_frames_follow_the_station_and_the_phones(self):
        response = self.client.get('/api/op_wallboard/live', buffered=False)
        self.assertEqual(response.status_code, 200)
        (event, patch), = self.frames(response, 1)
        self.assertEqual(event, 'live')
        self.assertEqual(patch['now']['queue'], 0)
        self.assertIsNone(patch['operators']['1']['live'])

        # Звонок станции: Иванов ответил клиенту — кадр сразу.
        self.hub.apply_calls([dict(live_call('a.1', state='talking', ext='6650',
                                             answered_at='2026-10-22T12:00:58'), rev=1)])
        (event, patch), = self.frames(response, 1)
        self.assertEqual(event, 'live')
        self.assertEqual(patch['operators']['1']['live']['status_key'], 'talking')
        self.assertEqual(patch['now']['operators_talking'], 1)
        self.assertEqual(patch['now']['operators_free'], 1)

        # Петров ушёл на перерыв в телефоне: одно чтение статуса, строка и «сейчас» — новым кадром.
        self.statuses[2] = {'status_key': 'перерыв', 'seconds': 0, 'event_at': '2026-10-22 12:01:02'}
        self.hub.publish({'kind': 'board', 'why': 'status', 'operator_id': 2, 'ts': 1.5})
        (event, patch), = self.frames(response, 1)
        self.assertEqual(patch['operators']['2']['status_key'], 'break')
        self.assertEqual(patch['now']['operators_on_break'], 1)
        self.assertEqual(self.status_reads[-1], [2])
        reads = len(self.status_reads)
        self.hub.publish({'kind': 'board', 'why': 'status', 'operator_id': 2, 'ts': 1.5})
        self.hub.publish({'kind': 'board', 'why': 'status', 'operator_id': 99, 'ts': 2.0})
        self.hub.apply_calls([dict(live_call('a.2', queued_at='2026-10-22T12:00:59'), rev=2)])
        (event, patch), = self.frames(response, 1)
        self.assertEqual(len(self.status_reads), reads, 'то же событие и человек не со стены — без запросов')
        self.assertEqual(patch['now']['queue'], 1)

        # Мост прислал касания: кэш сброшен, экран перечитывает снимок.
        self.hub.publish({'kind': 'board', 'why': 'touches'})
        (event, data), = self.frames(response, 1)
        self.assertEqual(event, 'refresh')
        cache_ts = self.bp_cache_ts()
        self.assertTrue(0 < cache_ts <= time.time() - 10,
                        'снимок просрочен, но не обнулён: прежний остаётся запасным')
        response.close()
        self.assertEqual(self.hub.stream_count('wallboard'), 0)

    def bp_cache_ts(self):
        # Кэш — замыкание фабрики: виден через ячейку функции снимка.
        snapshot_fn = self.bp.snapshot
        for cell in snapshot_fn.__closure__ or ():
            value = cell.cell_contents
            if isinstance(value, dict) and 'ts' in value and 'payload' in value:
                return value['ts']
        raise AssertionError('кэш снимка не найден')

    def test_snapshot_response_carries_the_server_clock(self):
        self.calls = [live_call('a.1', queued_at='2026-10-22T12:00:30')]
        body = self.client.get('/api/op_wallboard/snapshot').get_json()
        self.assertIn('served_at', body)
        self.assertEqual(body['now']['queue'], 1)
        self.assertTrue(body['live']['online'])


class LiveStreamHeartbeatTests(LiveStreamTests):
    """Пульс по времени и доприменение смены статуса к пересчитанному снимку."""

    def bp_cache(self):
        for cell in self.bp.snapshot.__closure__ or ():
            value = cell.cell_contents
            if isinstance(value, dict) and 'ts' in value and 'payload' in value:
                return value
        raise AssertionError('кэш снимка не найден')

    def test_heartbeat_survives_a_storm_of_unrelated_events(self):
        import threading
        with mock.patch.object(op_routes, 'LIVE_HEARTBEAT_SECONDS', 0.3):
            response = self.client.get('/api/op_wallboard/live', buffered=False)
            iterator = iter(response.response)
            next(iterator)                                    # первый кадр live
            stop = threading.Event()

            def storm():
                # Статусы людей не со стены — каждые 50 мс: кадр не меняется.
                while not stop.is_set():
                    self.hub.publish({'kind': 'board', 'why': 'status', 'operator_id': 99, 'ts': time.time()})
                    stop.wait(0.05)
            thread = threading.Thread(target=storm, daemon=True)
            thread.start()
            started = time.monotonic()
            chunk = next(iterator)
            stop.set()
            thread.join()
            text = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk
            self.assertTrue(text.startswith(': ping'), text)
            self.assertLess(time.monotonic() - started, 2, 'пульс по времени, а не по тишине событий')
            response.close()

    def test_status_change_survives_a_snapshot_recalculated_meanwhile(self):
        response = self.client.get('/api/op_wallboard/live', buffered=False)
        self.frames(response, 1)
        cache = self.bp_cache()
        old_payload = cache['payload']
        # Петров ушёл на перерыв; поток применил это к снимку в кэше.
        self.statuses[2] = {'status_key': 'перерыв', 'seconds': 0, 'event_at': '2026-10-22 12:01:02'}
        self.hub.publish({'kind': 'board', 'why': 'status', 'operator_id': 2, 'ts': 7.0})
        (event, patch), = self.frames(response, 1)
        self.assertEqual(patch['operators']['2']['status_key'], 'break')
        # Пересчёт снимка начался ДО смены и кладёт статус «готов» (прочитан раньше).
        stale = json.loads(json.dumps(old_payload))
        for row in stale['operators']:
            if row['id'] == 2:
                row.update(status_key='free', status_label='Активный', status_at='2026-10-22 12:00:55')
        stale['now']['operators_on_break'] = 0
        cache['payload'] = stale
        self.hub.apply_calls([dict(live_call('b.1', queued_at='2026-10-22T12:00:59'), rev=10)])
        (event, patch), = self.frames(response, 1)
        self.assertEqual(patch['operators']['2']['status_key'], 'break', 'смена статуса не откатилась')
        self.assertEqual(patch['now']['operators_on_break'], 1)
        response.close()


if __name__ == '__main__':
    unittest.main()
