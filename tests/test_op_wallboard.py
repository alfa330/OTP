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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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
          talk=60, ext='6650', queue='3000', linkedid=''):
    return {'started_at': started, 'answered_at': answered if talk else '', 'ext': ext,
            'call_type': call_type, 'result': 'Разговор' if talk else 'Не ответил',
            'talk_seconds': talk, 'dial_seconds': 70, 'queue': queue, 'linkedid': linkedid}


def linkedid_at(text, seq='1074887'):
    """linkedid станции, у которого целая часть — секунда прихода (время Алматы)."""
    arrival = datetime.strptime(text, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=5)))
    return '%d.%s' % (int(arrival.timestamp()), seq)


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

    def test_sl_is_stretched_from_measured_calls_to_all_answered(self):
        # Момент ответа известен у одного из двух принятых (второй телефон молчит): SL —
        # доля «в срок» среди измеренных × доля принятых среди дошедших, не 1/3 и не 1/2.
        touches = [touch(),                                         # измерен, за 12 с
                   touch(answered=''),                              # принят, момента нет
                   touch(call_type='Входящий (не приняли)', talk=0, ext='', answered='')]
        totals = S.aggregate(touches, sl_seconds=20)['totals']
        self.assertEqual(totals['wait_measured'], 1)
        self.assertAlmostEqual(totals['sl'], 1.0 * (2 / 3))
        self.assertEqual(totals['avg_wait_seconds'], 12)
        # Разговор — по измеренным телефоном: у неизмеренного billsec включает ожидание.
        self.assertEqual(totals['avg_talk_seconds'], 60)

    def test_sl_and_wait_are_unknown_without_answer_moment(self):
        # Станция с 09.09.2026 не отдаёт плечо агента: answered_at пуст у всех входящих.
        # SL 0 % при этом читался бы как «никого не обслужили» — должен быть прочерк.
        touches = [touch(answered=''), touch(answered=''),
                   touch(call_type='Входящий (не приняли)', talk=0, ext='', answered='')]
        totals = S.aggregate(touches, sl_seconds=20)['totals']
        self.assertEqual((totals['arrived'], totals['answered'], totals['missed']), (3, 2, 1))
        self.assertIsNone(totals['sl'])
        self.assertIsNone(totals['avg_wait_seconds'])
        self.assertEqual(totals['wait_measured'], 0)
        # Ни одного принятого — SL честно нулевой: все дошедшие потеряны.
        lost_only = S.aggregate([touch(call_type='Входящий (не приняли)', talk=0, ext='')])['totals']
        self.assertEqual(lost_only['sl'], 0.0)
        # Момент ответа известен — считается как раньше, и это видно по wait_measured.
        measured = S.aggregate([touch()], sl_seconds=20)['totals']
        self.assertAlmostEqual(measured['sl'], 1.0)
        self.assertEqual(measured['wait_measured'], 1)

    def test_outgoing_is_counted_apart_from_incoming(self):
        touches = [touch(call_type='Исходящий', talk=40), touch(call_type='Исходящий', talk=0)]
        totals = S.aggregate(touches)['totals']
        self.assertEqual(totals['arrived'], 0)
        self.assertEqual((totals['outgoing'], totals['outgoing_answered']), (2, 1))
        self.assertIsNone(totals['ar'])

    def test_no_per_line_breakdown(self):
        # Разрез по линиям снят целиком (владелец, 16.09.2026): очередь касания на итоги
        # не влияет и отдельным разрезом в снимок не попадает.
        parts = S.aggregate([touch(queue='3002,3000'), touch(queue='')])
        self.assertNotIn('queues', parts)
        self.assertEqual(parts['totals']['arrived'], 2)

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


class QueueEntryTests(unittest.TestCase):
    """Ожидание — от входа в очередь: приход (linkedid) + автоинформатор, а не started_at."""

    def test_announcement_length_is_the_stable_positive_delta(self):
        rows = [('3034', 0, 175), ('3034', 16, 122), ('3034', 15, 6), ('3034', 17, 4), ('3034', 51, 1),
                ('3010', 0, 461), ('3010', 7, 290), ('3010', 8, 45),
                # Меню IVR: кто-то жмёт сразу, кто-то дослушивает — устойчивой длины нет.
                ('3000', 0, 39), ('3000', 11, 3), ('3000', 15, 5), ('3000', 19, 5), ('3000', 25, 3),
                # Прямая очередь без автоинформатора: положительных задержек почти нет.
                ('3042', 0, 25), ('3042', 1, 2), ('3042', 66, 1)]
        self.assertEqual(S.announcement_seconds_from_deltas(rows), {'3034': 16, '3010': 7})

    def test_arrival_row_gets_queue_entry_after_the_announcement(self):
        # Тестовый звонок владельца 16.09: пришёл 09:32:07, автоинформатор Jana 16 с,
        # оператор снял трубку в 09:32:29 → в очереди ждал 6 с, а не 22.
        call = touch(started='2026-09-16 09:32:07', answered='2026-09-16 09:32:29', talk=12,
                     queue='3034', linkedid=linkedid_at('2026-09-16 09:32:07'))
        call['dial_seconds'] = 33
        out = S.attach_queue_entry([call], {'3034': 16})
        self.assertEqual(out[0]['queued_at'], '2026-09-16 09:32:23')
        totals = S.aggregate(out, sl_seconds=20)['totals']
        self.assertEqual(totals['avg_wait_seconds'], 6)
        self.assertAlmostEqual(totals['sl'], 1.0)

    def test_row_that_already_starts_at_the_queue_is_kept(self):
        # Станция отдала строку входа в очередь: started_at уже на 16 с позже прихода.
        call = touch(started='2026-09-16 09:32:23', answered='2026-09-16 09:32:29', talk=12,
                     queue='3034', linkedid=linkedid_at('2026-09-16 09:32:07'))
        out = S.attach_queue_entry([call], {'3034': 16})
        self.assertEqual(out[0]['queued_at'], '2026-09-16 09:32:23')

    def test_without_length_or_linkedid_nothing_changes(self):
        calls = [touch(queue='3000', linkedid=linkedid_at('2026-09-15 10:00:00')),   # длины нет
                 touch(queue='3034', linkedid=''),                                    # linkedid нет
                 touch(call_type='Исходящий', queue='3034', linkedid=linkedid_at('2026-09-15 10:00:00'))]
        out = S.attach_queue_entry(calls, {'3034': 16})
        self.assertEqual(out, calls)
        # Автоинформатор длиннее звонка — linkedid чужой или длина устарела: не правим.
        short = touch(queue='3034', talk=5, linkedid=linkedid_at('2026-09-15 10:00:00'))
        short['dial_seconds'] = 5
        self.assertNotIn('queued_at', S.attach_queue_entry([short], {'3034': 16})[0])


def event(operator_id, at, key='занят'):
    return {'operator_id': operator_id, 'event_at': at, 'status_key': key}


def is_talking(key):
    return status_entry(key)[1] == 'talking'


class AnswerMomentTests(unittest.TestCase):
    """Момент ответа — из пары «занят»/«готов» iCORE Phone, строго внутри звонка по CDR."""

    EXT = {1: '6650', 2: '6651'}

    def test_pair_inside_the_call_gives_answer_and_real_talk(self):
        # Звонок 10:00:00, длительность по CDR 70 с (touch(): dial_seconds=70) → конец 10:01:10.
        events = [event(1, '2026-09-15 10:00:15'), event(1, '2026-09-15 10:01:11', 'готов')]
        out = S.attach_answer_moments([touch(answered='', talk=70)], events, self.EXT, is_talking)
        self.assertEqual(out[0]['answered_at'], '2026-09-15 10:00:15')
        self.assertEqual(out[0]['talk_seconds'], 56)
        self.assertEqual(out[0]['answer_source'], 'phone')
        totals = S.aggregate(out, sl_seconds=20)['totals']
        self.assertEqual((totals['avg_wait_seconds'], totals['avg_talk_seconds']), (15, 56))
        self.assertAlmostEqual(totals['sl'], 1.0)

    def test_outgoing_call_while_waiting_is_not_taken_for_the_answer(self):
        # Пока входящий ждал в очереди, оператор успел сделать короткий исходящий:
        # «занят» 10:00:05 → «готов» 10:00:20 лежит внутри звонка, но не у его конца.
        events = [event(1, '2026-09-15 10:00:05'), event(1, '2026-09-15 10:00:20', 'готов'),
                  event(1, '2026-09-15 10:00:40'), event(1, '2026-09-15 10:01:09', 'готов')]
        out = S.attach_answer_moments([touch(answered='', talk=70)], events, self.EXT, is_talking)
        self.assertEqual(out[0]['answered_at'], '2026-09-15 10:00:40')
        self.assertEqual(out[0]['talk_seconds'], 29)

    def test_without_a_pair_the_touch_is_left_alone(self):
        touches = [touch(answered='', talk=70),                                   # телефон молчит
                   touch(answered='', talk=70, ext='6651'),                       # «готов» не у конца
                   touch(call_type='Входящий (не приняли)', talk=0, answered=''),  # потерянный
                   touch(call_type='Исходящий', talk=30, answered='')]            # исходящий
        events = [event(2, '2026-09-15 10:00:05'), event(2, '2026-09-15 10:00:30', 'готов')]
        out = S.attach_answer_moments(touches, events, self.EXT, is_talking)
        self.assertEqual(out, touches)
        for original, result in zip(touches, out):
            self.assertIsNot(original, result) if result.get('answer_source') else None

    def test_existing_answer_moment_is_kept(self):
        # Если склейка когда-нибудь снова получит плечо агента, телефон её не перебивает.
        events = [event(1, '2026-09-15 10:00:15'), event(1, '2026-09-15 10:01:11', 'готов')]
        out = S.attach_answer_moments([touch()], events, self.EXT, is_talking)
        self.assertEqual(out[0]['answered_at'], '2026-09-15 10:00:12')
        self.assertEqual(out[0]['talk_seconds'], 60)


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

    def test_bridge_time_from_timestamptz_is_shown_in_almaty(self):
        # cdr_agent_state.live_at — TIMESTAMPTZ, сессия Postgres в UTC: строка приезжает с
        # «+00:00». Табло показывало «данные на 20:18:53» и «последнее обновление 5:00:10
        # назад» при живом мосте — UTC сравнивался с алматинским «сейчас».
        snap = S.assemble(day=date(2026, 9, 15), touches=[touch()], people=self.people,
                          live_statuses=self.live, status_entry=status_entry, resolve_name=None,
                          bridge_state={'connected': True,
                                        'live_at': '2026-09-15T20:18:53+00:00',
                                        'last_seen_at': '2026-09-15T20:19:00.450000+00:00'},
                          now=datetime(2026, 9, 16, 1, 19, 3))
        self.assertEqual(snap['bridge']['live_age_seconds'], 10)
        # formatClock на фронте берёт часы из строки как есть — они обязаны быть местными.
        self.assertEqual(snap['bridge']['live_at'], '2026-09-16T01:18:53')
        self.assertEqual(snap['bridge']['last_seen_at'], '2026-09-16T01:19:00')


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

        self.touches = [touch()]
        self.phone_events = []
        for name, value in (('day_touches_compact', lambda cursor, day: list(self.touches)),
                            ('agent_state', lambda cursor: {'connected': True, 'live_at': None,
                                                            'last_seen_at': None}),
                            ('load_directory', lambda cursor: {})):
            patcher = mock.patch.object(op_routes.queries, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(op_routes, 'load_phone_events',
                                    lambda cursor, ids, day: list(self.phone_events))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.announcement_deltas = []
        patcher = mock.patch.object(op_routes, 'load_announcement_deltas',
                                    lambda cursor, day, days=7: list(self.announcement_deltas))
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

    def test_answer_moment_comes_from_the_phone(self):
        """Склейка без плеча агента + события iCORE Phone → SL, ожидание и разговор по телефону."""
        self.requester['role'] = 'admin'
        self.touches = [touch(answered='', talk=70)]
        self.phone_events = [event(1, datetime(2026, 9, 15, 10, 0, 15)),
                             event(1, datetime(2026, 9, 15, 10, 1, 11), 'готов')]
        totals = self.client.get('/api/op_wallboard/snapshot').get_json()['totals']
        self.assertEqual(totals['wait_measured'], 1)
        self.assertEqual(totals['avg_wait_seconds'], 15)
        self.assertEqual(totals['avg_talk_seconds'], 56)
        self.assertAlmostEqual(totals['sl'], 1.0)

    def test_wait_starts_after_the_announcement(self):
        """Строка прихода + длина автоинформатора очереди из недели касаний → ожидание в очереди."""
        self.requester['role'] = 'admin'
        self.touches = [touch(answered='', talk=70, queue='3034',
                              linkedid=linkedid_at('2026-09-15 10:00:00'))]
        self.announcement_deltas = [('3034', 0, 100), ('3034', 10, 80), ('3034', 11, 5)]
        self.phone_events = [event(1, datetime(2026, 9, 15, 10, 0, 15)),
                             event(1, datetime(2026, 9, 15, 10, 1, 11), 'готов')]
        body = self.client.get('/api/op_wallboard/snapshot').get_json()
        self.assertEqual(body['announcement_seconds'], {'3034': 10})
        self.assertEqual(body['totals']['avg_wait_seconds'], 5)   # 15 с от прихода − 10 с сообщения

    def test_no_data_at_all_is_a_503_in_words(self):
        self.requester['role'] = 'admin'
        self.fail_fetch = True
        response = self.client.get('/api/op_wallboard/snapshot')
        self.assertEqual(response.status_code, 503)
        self.assertIn('данных пока нет', response.get_json()['error'])


# ── фронт ─────────────────────────────────────────────────────────────────────
# По исходнику, как у табло СЗоВ и Тез: сборки React в тестах нет, а структура экрана и
# проводка виджета — решения владельца, которые должны переживать правки соседей.

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / 'src' / 'components' / 'monitoring'


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = (MONITORING / 'OpWallboardView.jsx').read_text(encoding='utf-8-sig')
        cls.shared = (MONITORING / 'opWallboardShared.js').read_text(encoding='utf-8-sig')
        cls.widget = (MONITORING / 'SzovWallboardWidget.jsx').read_text(encoding='utf-8-sig')
        cls.szov_view = (MONITORING / 'SzovWallboardView.jsx').read_text(encoding='utf-8-sig')
        cls.app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig')

    def test_lines_panel_is_gone_everywhere(self):
        """Решение владельца 16.09.2026: разрез «По линиям» снят целиком — с экрана,
        из снимка и из текста отбивки (тот сторожит test_op_broadcast)."""
        self.assertNotIn('По линиям', self.view)
        self.assertNotIn('QueuesTable', self.view)
        self.assertNotIn('queues', self.view)
        self.assertIn('title="По часам"', self.view)
        backend = (ROOT / 'op_wallboard' / 'snapshot.py').read_text(encoding='utf-8-sig')
        self.assertNotIn("'queues'", backend)
        self.assertNotIn('_first_queue', backend)

    def test_missing_answer_moment_is_explained_on_the_wall(self):
        """Прочерк в SL без причины читается как поломка: причина — в данных станции."""
        self.assertIn('export const opDataGapNotice', self.shared)
        self.assertIn('wait_measured', self.shared)
        self.assertIn('{dataGap ? (', self.view)
        self.assertIn('opDataGapNotice(snapshot)', self.view)

    def test_widget_button_is_the_same_as_szov(self):
        """Кнопка виджета — общая с СЗоВ (экспорт), а не третья копия."""
        self.assertIn('export const WidgetButton', self.szov_view)
        self.assertIn("import { BroadcastControls, WidgetButton } from './SzovWallboardView';", self.view)
        self.assertIn('<WidgetButton direction="op" widgetOpen={widgetOpen}', self.view)
        self.assertIn('onToggleWidget={onToggleWidget}', self.view)

    def test_widget_resolves_op_direction_from_its_own_registry(self):
        """Реестр ОП свой: словарь СЗоВ питает переключатель направлений его раздела."""
        self.assertIn("import { OP_WALLBOARD_DIRECTIONS, opWallboardDirection } from './opWallboardShared';",
                      self.widget)
        self.assertIn('OP_WALLBOARD_DIRECTIONS[key] ? opWallboardDirection(key)', self.widget)
        registry = self.shared[self.shared.index('export const OP_WALLBOARD_DIRECTIONS'):]
        for field in ("key: 'op'", 'useSnapshot: useOpWallboardSnapshot', 'metrics: OP_METRICS',
                      'metricMap: OP_METRIC_MAP', 'metricGroups: OP_METRIC_GROUPS',
                      'defaultMetrics: DEFAULT_OP_WIDGET_METRICS', 'readMetric: readOpMetric',
                      'freshnessNotice: opFreshnessNotice'):
            self.assertIn(field, registry, field)
        # Набор по умолчанию — только ключи каталога.
        self.assertTrue(all(("key: '%s'" % key) in self.shared for key in
                            ('op_online', 'op_talking', 'op_free', 'op_arrived', 'op_missed', 'op_ar')))

    def test_widget_reads_op_metrics_through_the_registry(self):
        """Показатели ОП читают снимок целиком; общий readWallboardMetric отдал бы им `now`
        и прочерки во всех плитках. Подсказка бывает функцией — в title её надо вызвать."""
        self.assertIn('const read = config.readMetric || readWallboardMetric;', self.widget)
        self.assertIn('read={read}', self.widget)
        self.assertIn("typeof metric.hint === 'function' ? metric.hint(snapshot)", self.widget)
        self.assertNotIn('title={metric.hint || metric.label}', self.widget)
        self.assertIn('config.freshnessNotice(snapshot)', self.widget)
        self.assertIn('config.clockLabel', self.widget)

    def test_app_wires_the_widget_with_the_sections_own_access(self):
        """Окно поверх других одно на приложение; право на него — у раздела «Табло ОП»."""
        self.assertIn('widgetOpen={szovWallboardWidget}\n'
                      '                                    onToggleWidget={setSzovWallboardWidget}\n'
                      '                                />\n'
                      '                            </Suspense>\n'
                      '                        )}\n'
                      '                        {view === "tez_wallboard"', self.app)
        self.assertIn(": szovWallboardWidget === 'op'\n"
                      "                            ? canAccessOpWallboardSection", self.app)


if __name__ == '__main__':
    unittest.main()
