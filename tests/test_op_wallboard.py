# -*- coding: utf-8 -*-
"""«Табло ОП»: расчёт снимка по касаниям и статусам, периметр ручки.

Что закреплено:
  * определения те же, что у СЗоВ: входящие = дошедшие, AR = потеряно/входящих,
    SL = отвечено не позже порога / входящих, среднее время разговора усекается;
  * касание через две очереди считается в одной линии, а не в двух;
  * люди сортируются по разряду статуса, а не по времени — строки на стене не прыгают;
  * номер, звонивший сегодня, но не найденный в составе отдела, показывается честно;
  * «онлайн» = свободные + в разговоре; перерыв, тренинг, тех.причина — «на перерыве»;
  * ручка: 403 чужому, снимок админу, 503 словами, когда данных нет вовсе.
"""

import unittest
from contextlib import contextmanager
from datetime import date, datetime
from unittest import mock

from flask import Flask

from op_wallboard import routes as op_routes, snapshot as S

STATUS_CATALOG = {
    'занят': ('В разговоре', 'talking', 10),
    'готов': ('Активный', 'free', 20),
    'перезвон': ('Исход', 'outgoing', 30),
    'перерыв': ('Перерыв', 'break', 60),
    'выключен': ('Не в сети', 'offline', 70),
}


def status_entry(key):
    return STATUS_CATALOG.get(str(key or '').lower(), ('Нет событий', 'unknown', 90))


def touch(call_type='Входящий', started='2026-09-15 10:00:00', answered='2026-09-15 10:00:12',
          talk=60, ext='6650', queue='3000'):
    return {'started_at': started, 'answered_at': answered if talk else '', 'ext': ext,
            'call_type': call_type, 'result': 'Разговор' if talk else 'Не ответил',
            'talk_seconds': talk, 'dial_seconds': 70, 'queue': queue}


class AggregateTests(unittest.TestCase):
    def test_incoming_totals_ar_and_sl(self):
        touches = [
            touch(),                                                   # ответ за 12 с
            touch(answered='2026-09-15 10:05:40', started='2026-09-15 10:05:00', talk=30),  # за 40 с
            touch(call_type='Входящий (не приняли)', talk=0, ext=''),
            touch(call_type='Входящий (не приняли)', talk=0, ext=''),
        ]
        totals = S.aggregate(touches, sl_seconds=20)['totals']
        self.assertEqual((totals['arrived'], totals['answered'], totals['missed']), (4, 2, 2))
        self.assertEqual(totals['served_sl'], 1)
        self.assertAlmostEqual(totals['ar'], 0.5)
        self.assertAlmostEqual(totals['sl'], 0.25)
        self.assertEqual(totals['avg_talk_seconds'], 45)
        self.assertEqual(totals['avg_wait_seconds'], 26)

    def test_average_talk_is_truncated_not_rounded(self):
        totals = S.aggregate([touch(talk=10), touch(talk=11), touch(talk=11)])['totals']
        self.assertEqual(totals['avg_talk_seconds'], 10)   # 32 / 3 = 10.67 → 10

    def test_outgoing_is_counted_apart_from_incoming(self):
        touches = [touch(call_type='Исходящий', talk=40), touch(call_type='Исходящий', talk=0)]
        totals = S.aggregate(touches)['totals']
        self.assertEqual(totals['arrived'], 0)
        self.assertEqual((totals['outgoing'], totals['outgoing_answered']), (2, 1))
        self.assertIsNone(totals['ar'])

    def test_multi_queue_touch_lands_in_one_line(self):
        parts = S.aggregate([touch(queue='3002,3000')])
        self.assertEqual([q['queue'] for q in parts['queues']], ['3000'])
        self.assertEqual(parts['queues'][0]['arrived'], 1)

    def test_hourly_buckets(self):
        parts = S.aggregate([touch(started='2026-09-15 09:59:59', answered='2026-09-15 10:00:05'),
                             touch(call_type='Входящий (не приняли)', talk=0,
                                   started='2026-09-15 14:30:00')])
        hourly = parts['hourly']
        self.assertEqual(len(hourly), 24)
        self.assertEqual((hourly[9]['arrived'], hourly[9]['answered']), (1, 1))
        self.assertEqual((hourly[14]['arrived'], hourly[14]['missed']), (1, 1))

    def test_per_ext_counters(self):
        parts = S.aggregate([touch(ext='6650'), touch(ext='6650', call_type='Исходящий', talk=5),
                             touch(ext='6651', call_type='Входящий (не приняли)', talk=0)])
        self.assertEqual(parts['by_ext']['6650']['answered'], 1)
        self.assertEqual(parts['by_ext']['6650']['outgoing'], 1)
        self.assertEqual(parts['by_ext']['6650']['talk_seconds'], 65)
        self.assertEqual(parts['by_ext']['6651']['missed'], 1)

    def test_garbage_started_at_is_skipped(self):
        self.assertEqual(S.aggregate([touch(started='вчера')])['totals']['arrived'], 0)


class PeopleTests(unittest.TestCase):
    def setUp(self):
        self.people = [
            {'id': 1, 'name': 'Иванов', 'sip_number': '6650', 'role': 'operator'},
            {'id': 2, 'name': 'Петров', 'sip_number': '6651', 'role': 'operator'},
            {'id': 3, 'name': 'Сидоров', 'sip_number': None, 'role': 'operator'},
        ]
        self.live = {1: {'status_key': 'занят', 'seconds': 75, 'event_at': '2026-09-15 10:00:00'},
                     2: {'status_key': 'перерыв', 'seconds': 300, 'event_at': '2026-09-15 09:55:00'}}

    def test_rows_are_sorted_by_status_weight_then_name(self):
        rows = S.build_people(self.people, self.live, status_entry, {}, None)
        self.assertEqual([r['name'] for r in rows], ['Иванов', 'Петров', 'Сидоров'])
        self.assertEqual(rows[0]['status_key'], 'talking')
        self.assertEqual(rows[2]['status_key'], 'unknown')

    def test_stats_are_attached_by_ext(self):
        by_ext = {'6650': {'answered': 5, 'missed': 1, 'outgoing': 2, 'outgoing_answered': 1,
                           'talk_seconds': 600, 'calls': 8, 'last_call_at': '2026-09-15 10:00:00'}}
        rows = S.build_people(self.people, self.live, status_entry, by_ext, None)
        ivanov = next(r for r in rows if r['name'] == 'Иванов')
        self.assertEqual((ivanov['answered'], ivanov['outgoing']), (5, 2))

    def test_unknown_ext_is_shown_not_hidden(self):
        by_ext = {'6999': {'answered': 3, 'missed': 0, 'outgoing': 0, 'outgoing_answered': 0,
                           'talk_seconds': 100, 'calls': 3, 'last_call_at': ''}}
        rows = S.build_people(self.people, {}, status_entry, by_ext, lambda ext: 'Неизвестный номер ' + ext)
        stranger = rows[-1]
        self.assertFalse(stranger['in_roster'])
        self.assertEqual(stranger['name'], 'Неизвестный номер 6999')
        self.assertEqual(stranger['answered'], 3)

    def test_now_counts_follow_szov_definitions(self):
        rows = S.build_people(self.people, self.live, status_entry, {}, None)
        now = S.count_now(rows)
        self.assertEqual(now['operators_total'], 3)
        self.assertEqual(now['operators_online'], 1)      # только «в разговоре»
        self.assertEqual(now['operators_on_break'], 1)
        self.assertEqual(now['operators_unknown'], 1)

    def test_assemble_reports_live_age(self):
        snap = S.assemble(day=date(2026, 9, 15), touches=[touch()], people=self.people,
                          live_statuses=self.live, status_entry=status_entry, resolve_name=None,
                          bridge_state={'connected': True, 'live_at': '2026-09-15T10:00:00',
                                        'last_seen_at': '2026-09-15T10:00:10'},
                          now=datetime(2026, 9, 15, 10, 0, 42))
        self.assertEqual(snap['bridge']['live_age_seconds'], 42)
        self.assertEqual(snap['totals']['answered'], 1)
        self.assertEqual(snap['day'], '2026-09-15')
        self.assertEqual(len(snap['operators']), 3)


# ── ручка ─────────────────────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self):
        self.departments = [{'id': 7, 'code': 'op'}, {'id': 8, 'code': 'szov'}]
        self.user_department = {}

    def get_departments(self):
        return self.departments

    def get_user_department_id(self, user_id):
        return self.user_department.get(user_id)

    def _get_cursor(self):
        @contextmanager
        def scope():
            yield object()
        return scope()


def _passthrough_cache(*, fetch, before=None, after=None, **_ignored):
    if before:
        before()
    payload = fetch()
    if after:
        after(payload, 0.0)
    return dict(payload, stale=False, age_seconds=0)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDb()
        self.requester = {'id': 10, 'role': 'operator', 'headed': None}
        self.fail_fetch = False

        def get_authenticated_requester():
            return self.requester['id'], (None, None, None, self.requester['role']), None

        for name, value in (('day_touches_compact', lambda cursor, day: [touch()]),
                            ('agent_state', lambda cursor: {'connected': True, 'live_at': None,
                                                            'last_seen_at': None}),
                            ('load_directory', lambda cursor: {})):
            patcher = mock.patch.object(op_routes.queries, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        def fetch_guarded_cache(**kwargs):
            if self.fail_fetch:
                raise RuntimeError('Табло ОП (мост «Касаний»): снимок обновляется, данных пока нет')
            return _passthrough_cache(**kwargs)

        department_id = op_routes.make_department_resolver(self.db)
        guard = op_routes.make_guard(
            db=self.db, department_id=department_id,
            get_authenticated_requester=get_authenticated_requester,
            normalize_user_role=lambda role: role,
            is_global_admin_requester=lambda role, uid: role in ('admin', 'super_admin')
            and self.requester['headed'] is None,
            headed_department_id=lambda uid: self.requester['headed'],
            is_supervisor_role=lambda role: role in ('sv', 'supervisor'))
        app = Flask(__name__)
        app.register_blueprint(op_routes.build_op_wallboard_blueprint(
            db=self.db, require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204), guard=guard,
            department_id=department_id, snapshot_with_cache=fetch_guarded_cache,
            restore_cache=lambda *a, **k: None, persist_cache=lambda *a, **k: None,
            status_entry=status_entry,
            load_people=lambda dept, day: [{'id': 1, 'name': 'Иванов', 'sip_number': '6650',
                                            'role': 'operator'}],
            live_statuses=lambda ids: {1: {'status_key': 'готов', 'seconds': 5}}))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_operator_is_refused(self):
        self.assertEqual(self.client.get('/api/op_wallboard/snapshot').status_code, 403)

    def test_admin_gets_a_snapshot(self):
        self.requester['role'] = 'admin'
        response = self.client.get('/api/op_wallboard/snapshot')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['totals']['arrived'], 1)
        self.assertEqual(body['now']['operators_free'], 1)
        self.assertEqual(body['operators'][0]['status_key'], 'free')

    def test_head_of_sales_passes_and_head_of_another_department_does_not(self):
        self.requester.update(role='admin', headed=7)
        self.assertEqual(self.client.get('/api/op_wallboard/snapshot').status_code, 200)
        self.requester.update(headed=8)
        self.assertEqual(self.client.get('/api/op_wallboard/snapshot').status_code, 403)

    def test_supervisor_of_sales_passes_by_department(self):
        self.requester['role'] = 'sv'
        self.db.user_department[10] = 8
        self.assertEqual(self.client.get('/api/op_wallboard/snapshot').status_code, 403)
        self.db.user_department[10] = 7
        self.assertEqual(self.client.get('/api/op_wallboard/snapshot').status_code, 200)

    def test_no_data_at_all_is_a_503_in_words(self):
        self.requester['role'] = 'admin'
        self.fail_fetch = True
        response = self.client.get('/api/op_wallboard/snapshot')
        self.assertEqual(response.status_code, 503)
        self.assertIn('данных пока нет', response.get_json()['error'])


if __name__ == '__main__':
    unittest.main()
