# -*- coding: utf-8 -*-
"""Живой хвост сегодняшних суток: мост досылает только изменения, портал принимает
только сегодня/вчера.

Что закреплено:
  * первый проход — полный день и все касания уезжают; второй без изменений — ни
    одного запроса к порталу; изменившееся касание уезжает одно;
  * исчезнувшее из склейки касание уходит в `removed`, а не остаётся на портале
    навсегда;
  * ошибка станции не роняет хвост и не роняет мост: три подряд — пауза длиннее;
  * окно приращения — последние два часа от самой поздней строки, полный проход
    раз в FULL_REFRESH_EVERY циклов;
  * портал: живое приращение за позавчера — 400, за сегодня — upsert без сноса,
    отметка live_at, чужие сутки в теле отбрасываются как у суточной присылки.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest import mock

from flask import Flask

from cdr import agent_auth, routes as cdr_routes
from cdr_bridge import live, signing
from cdr_bridge.station import StationError

TODAY = date(2026, 9, 15)


def cdr_row(linkedid, calldate, **over):
    """Входящий на очередь 3000, отвеченный оператором 6650, с записью — как отдаёт станция.

    Клиента склейка берёт из ИМЕНИ файла записи, поэтому оно собирается из src после
    наложения переопределений — иначе два разных клиента слиплись бы в одного."""
    base = {
        'calldate': calldate, 'src': '+77015550001', 'dst': '6650', 'clid': '',
        'did': '', 'duration': 40, 'billsec': 30, 'disposition': 'ANSWERED',
        'dcontext': 'from-queue', 'uniqueid': linkedid, 'linkedid': linkedid,
        'recording_url': None, 'channel': 'SIP/trunk-0001', 'dstchannel': 'SIP/6650-0002',
    }
    base.update(over)
    base.setdefault('recordingfile',
                    'q-3000-%s-6650-20260915-090000-%s.wav' % (base['src'], linkedid))
    return base


class _Station:
    def __init__(self, rows=None, fail=None):
        self.rows = rows if rows is not None else []
        self.fail = fail
        self.calls = []

    def iter_cdr(self, from_dt, to_dt, on_page=None):
        self.calls.append((from_dt, to_dt))
        if self.fail:
            raise self.fail
        for row in self.rows:
            yield row


class LiveTailTests(unittest.TestCase):
    def setUp(self):
        self.posts = []
        self.station = _Station()
        self.tail = live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                                  self.station, 20, today=lambda: TODAY)

    def test_first_pass_is_full_day_and_ships_everything(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00'),
                             cdr_row('1.2', '2026-09-15 09:05:00', src='+77015550002')]
        self.tail.step()
        self.assertEqual(self.station.calls, [('2026-09-15T00:00:00', '2026-09-16T01:00:00')])
        self.assertEqual(len(self.posts), 1)
        path, payload = self.posts[0]
        self.assertEqual(path, 'live')
        self.assertEqual(payload['day'], '2026-09-15')
        self.assertTrue(payload['full_refresh'])
        self.assertEqual(len(payload['touches']), 2)
        self.assertEqual(payload['removed'], [])
        self.assertEqual(set(payload['touches'][0]), set(live.TOUCH_FIELDS))

    def test_no_change_means_no_request(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.tail.step()
        self.tail.step()
        self.assertEqual(len(self.posts), 1, 'без изменений портал дёргать нельзя')

    def test_only_the_changed_touch_is_shipped(self):
        first = cdr_row('1.1', '2026-09-15 09:00:00')
        second = cdr_row('1.2', '2026-09-15 09:05:00', src='+77015550002')
        self.station.rows = [first, second]
        self.tail.step()
        # У второго звонка станция дописала плечо: разговор стал длиннее.
        self.station.rows = [first, cdr_row('1.2', '2026-09-15 09:05:00', src='+77015550002',
                                            billsec=120, duration=130)]
        self.tail.step()
        self.assertEqual(len(self.posts), 2)
        touches = self.posts[1][1]['touches']
        self.assertEqual([t['phone'] for t in touches], ['7015550002'])
        self.assertEqual(touches[0]['talk_seconds'], 120)

    def test_incremental_window_starts_two_hours_before_the_latest_row(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00'),
                             cdr_row('1.2', '2026-09-15 11:30:00', src='+77015550002')]
        self.tail.step()
        self.tail.step()
        self.assertEqual(self.station.calls[1][0], '2026-09-15T09:30:00')

    def test_full_refresh_recurs_and_drops_vanished_touches(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00'),
                             cdr_row('1.2', '2026-09-15 09:05:00', src='+77015550002')]
        self.tail.step()
        # Станция переписала плечи: звонок 1.2 сменил linkedid. Это видно только на полном
        # проходе — подгоняем счётчик циклов к нему.
        self.tail.cycles = live.FULL_REFRESH_EVERY
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00'),
                             cdr_row('1.3', '2026-09-15 09:05:00', src='+77015550002')]
        self.tail.step()
        payload = self.posts[-1][1]
        self.assertTrue(payload['full_refresh'])
        self.assertEqual(payload['removed'], [{'linkedid': '1.2', 'phone': '7015550002'}])
        self.assertEqual([t['linkedid'] for t in payload['touches']], ['1.3'])

    def test_tail_touches_of_tomorrow_are_not_shipped(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 23:50:00'),
                             cdr_row('2.1', '2026-09-16 00:20:00', src='+77015550002')]
        self.tail.step()
        self.assertEqual([t['linkedid'] for t in self.posts[0][1]['touches']], ['1.1'])

    def test_new_day_resets_the_tail(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.tail.step()
        self.tail._today = lambda: TODAY + timedelta(days=1)
        self.station.rows = []
        self.tail.step()
        self.assertEqual(self.tail.day, TODAY + timedelta(days=1))
        self.assertEqual(self.tail.sent, {})
        self.assertEqual(self.station.calls[-1][0], '2026-09-16T00:00:00')

    def test_station_failure_does_not_raise_and_backs_off_after_three(self):
        self.station.fail = StationError('станция не ответила', 'timeout')
        now = 1_000_000.0
        for i in range(3):
            self.assertTrue(self.tail.maybe_step(now=now + i * 100))
        self.assertEqual(self.tail.failures, 3)
        self.assertGreaterEqual(self.tail.next_due - (now + 200), live.BACKOFF_SECONDS)
        self.assertEqual(self.posts, [])

    def test_portal_failure_keeps_touches_for_the_next_attempt(self):
        def failing_post(path, payload):
            raise RuntimeError('портал ответил 502')
        tail = live.LiveTail(failing_post, self.station, 20, today=lambda: TODAY)
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.assertTrue(tail.maybe_step(now=1_000_000.0))
        self.assertEqual(tail.sent, {}, 'неотправленное не должно считаться отправленным')
        tail._post = lambda path, payload: self.posts.append((path, payload))
        tail.step()
        self.assertEqual(len(self.posts[0][1]['touches']), 1)

    def test_not_due_means_no_step(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.tail.maybe_step(now=1_000_000.0)
        self.assertFalse(self.tail.maybe_step(now=1_000_000.0 + 5))
        self.assertEqual(len(self.station.calls), 1)


# ── портал ────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeDb:
    def _get_cursor(self):
        @contextmanager
        def scope():
            yield _FakeCursor()
        return scope()


class _Recorder:
    def __init__(self):
        self.upserts = []
        self.deleted = []
        self.seen = []

    def today_almaty(self):
        return TODAY

    def upsert_touches(self, cursor, day, touches):
        self.upserts.append((day, touches))
        return len(touches)

    def delete_touches(self, cursor, day, keys):
        self.deleted.append((day, keys))
        return len(keys)

    def agent_seen(self, cursor, **kwargs):
        self.seen.append(kwargs)


class LiveRouteTests(unittest.TestCase):
    def setUp(self):
        private_b64, public_b64, self.kid = signing.generate()
        self.signer = signing.Signer(signing.load_private_key(private_b64))
        self.recorder = _Recorder()
        for target, attr, value in ((cdr_routes, 'queries', self.recorder),
                                    (agent_auth, '_default_nonces', agent_auth.NonceCache())):
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (('agent_keys_raw', public_b64), ('agent_token', '')):
            patcher = mock.patch.object(cdr_routes.config, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(cdr_routes.build_cdr_blueprint(
            db=_FakeDb(), require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None)))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def _live(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', '/api/cdr/agent/live', body))
        return self.client.post('/api/cdr/agent/live', data=body, headers=headers)

    def _touch(self, linkedid='1.1', started='2026-09-15 09:00:00', phone='77015550001'):
        return {'linkedid': linkedid, 'phone': phone, 'started_at': started,
                'answered_at': '2026-09-15 09:00:10', 'ext': '6650', 'call_type': 'Входящий',
                'result': 'принят', 'talk_seconds': 30, 'dial_seconds': 10, 'queue': '3000',
                'recording_url': None, 'legs': 2}

    def test_today_is_upserted_without_wiping_the_day(self):
        response = self._live({'day': '2026-09-15', 'touches': [self._touch()],
                               'removed': [{'linkedid': '9.9', 'phone': '7015550009'}]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json(), {'status': 'ok', 'stored': 1, 'removed': 1})
        day, touches = self.recorder.upserts[0]
        self.assertEqual(day, TODAY)
        self.assertEqual(touches[0]['phone'], '7015550001')
        self.assertTrue(self.recorder.seen[0]['live'])
        self.assertEqual(self.recorder.seen[0]['agent_key'], self.kid)

    def test_yesterday_is_still_accepted_around_midnight(self):
        response = self._live({'day': '2026-09-14', 'touches': []})
        self.assertEqual(response.status_code, 200)

    def test_older_days_are_refused(self):
        response = self._live({'day': '2026-09-13', 'touches': [self._touch(started='2026-09-13 09:00:00')]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.recorder.upserts, [])

    def test_touches_of_another_day_are_dropped(self):
        response = self._live({'day': '2026-09-15',
                               'touches': [self._touch(started='2026-09-16 00:20:00')]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['stored'], 0)

    def test_removed_must_be_a_list_of_keys(self):
        response = self._live({'day': '2026-09-15', 'touches': [], 'removed': 'все'})
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
