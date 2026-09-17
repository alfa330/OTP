# -*- coding: utf-8 -*-
"""«Табло ОП»: расчёт снимка по касаниям и статусам, периметр ручки.

Что закреплено:
  * определения те же, что у СЗоВ: входящие = дошедшие, AR = потеряно/входящих,
    SL = отвечено не позже порога / входящих, среднее время разговора усекается;
  * ожидание — от входа в очередь (приход + автоинформатор), какую бы строку ни отдала станция;
  * касание через две очереди считается в одной линии, а не в двух;
  * люди сортируются по разряду статуса, а не по времени — строки на стене не прыгают;
  * номер, звонивший сегодня, но не найденный в составе отдела, показывается честно;
  * «онлайн» = свободные + в разговоре; перерыв, тренинг, тех.причина — «на перерыве»;
  * ручка: 403 чужому, снимок админу, 503 словами, когда данных нет вовсе;
  * ТЗ #339: разрезы по группам в том же снимке и в сумме — отдел, время входа — начало смены
    (ночник — вчерашним временем), журнал статусов — за календарные сутки, только тем, кто на табло.
"""

import unittest
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
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
        parts = S.aggregate([touch(queue='3002,3000'), touch(queue='3010')])
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

    def test_hour_has_the_same_metrics_as_the_day(self):
        """Владелец 17.09.2026: отбивка шлёт показатели последнего часа — тем же расчётом."""
        touches = [
            touch(started='2026-09-15 09:10:00', answered='2026-09-15 09:10:05', talk=60),  # 5 с
            touch(started='2026-09-15 09:20:00', answered='2026-09-15 09:20:40', talk=30),  # 40 с
            touch(call_type='Входящий (не приняли)', talk=0, started='2026-09-15 09:30:00'),
            touch(started='2026-09-15 10:05:00', answered='2026-09-15 10:05:03', talk=90),  # 3 с
            touch(call_type='Исходящий', talk=20, started='2026-09-15 09:40:00'),
        ]
        parts = S.aggregate(touches, sl_seconds=20)
        hour = parts['hourly'][9]
        self.assertEqual((hour['hour'], hour['arrived'], hour['answered'], hour['missed']), (9, 3, 2, 1))
        self.assertAlmostEqual(hour['ar'], 1 / 3)
        self.assertAlmostEqual(hour['sl'], (1 / 2) * (2 / 3))
        self.assertEqual((hour['avg_wait_seconds'], hour['avg_talk_seconds'], hour['wait_measured']),
                         (22, 45, 2))
        self.assertEqual((hour['outgoing'], hour['outgoing_answered']), (1, 1))
        self.assertAlmostEqual(parts['hourly'][10]['sl'], 1.0)
        self.assertIsNone(parts['hourly'][3]['sl'])
        self.assertEqual(parts['totals']['arrived'], sum(h['arrived'] for h in parts['hourly']))

    def test_incoming_hour_is_the_queue_entry_not_the_station_row(self):
        # Пришёл 09:58:30, автоинформатор 26 с, ждал до 10:01:30. Станция отдала строку с началом
        # за две секунды до ответа — по ней звонок ушёл бы в десятый час вместе с ожиданием.
        call = touch(started='2026-09-15 10:01:28', answered='2026-09-15 10:01:30', talk=40,
                     queue='3041', linkedid=linkedid_at('2026-09-15 09:58:30'))
        call['dial_seconds'] = 42
        parts = S.aggregate(S.attach_queue_entry([call], {'3041': 26}), sl_seconds=20)
        self.assertEqual((parts['hourly'][9]['arrived'], parts['hourly'][10]['arrived']), (1, 0))
        self.assertEqual(parts['hourly'][9]['served_sl'], 0)

    def test_arrival_before_midnight_stays_in_the_touch_day(self):
        call = touch(started='2026-09-15 00:00:18', answered='2026-09-15 00:00:20', talk=40,
                     queue='3000', linkedid=linkedid_at('2026-09-14 23:59:50'))
        parts = S.aggregate([call])
        self.assertEqual((parts['hourly'][0]['arrived'], parts['hourly'][23]['arrived']), (1, 0))

    def test_per_ext_counters(self):
        parts = S.aggregate([touch(ext='6650'), touch(ext='6650', call_type='Исходящий', talk=5),
                             touch(ext='6651', call_type='Входящий (не приняли)', talk=0)])
        self.assertEqual(parts['by_ext']['6650']['answered'], 1)
        self.assertEqual(parts['by_ext']['6650']['outgoing'], 1)
        self.assertEqual(parts['by_ext']['6650']['talk_seconds'], 65)
        self.assertEqual(parts['by_ext']['6651']['missed'], 1)

    def test_garbage_started_at_is_skipped(self):
        self.assertEqual(S.aggregate([touch(started='вчера')])['totals']['arrived'], 0)

    def test_incoming_without_a_queue_is_not_a_queue_call(self):
        # DID → прямой внутренний номер 2030: клиент до очереди не дошёл. Станция таких в
        # строках очередей не имеет, и табло их не считает — ни дошедшими, ни потерянными.
        touches = [touch(call_type='Входящий (не приняли)', talk=0, queue=''),
                   touch(queue='')]
        totals = S.aggregate(touches)['totals']
        self.assertEqual((totals['arrived'], totals['answered'], totals['missed']), (0, 0, 0))


class QueueEntryTests(unittest.TestCase):
    """Ожидание — от входа в очередь: приход (linkedid) + автоинформатор, а не started_at."""

    def test_announcement_length_is_the_stable_positive_delta(self):
        rows = [('3034', 0, 175), ('3034', 16, 122), ('3034', 15, 6), ('3034', 17, 4), ('3034', 51, 1),
                ('3010', 0, 461), ('3010', 7, 290), ('3010', 8, 45),
                # Кнопочное меню: кто-то жмёт сразу, кто-то дослушивает — устойчивой длины нет.
                ('3099', 0, 30), ('3099', 10, 3), ('3099', 11, 2)]
        rows += [('3099', d, 1) for d in (4, 6, 14, 17, 21, 24, 28, 31, 35, 40, 44, 52)]
        # Прямая очередь без автоинформатора: положительных задержек почти нет.
        rows += [('3042', 0, 25), ('3042', 1, 2), ('3042', 66, 1)]
        self.assertEqual(S.announcement_seconds_from_deltas(rows), {'3034': 16, '3010': 7})

    def test_length_survives_dial_rows_and_a_second_entry(self):
        # 3006 за 10–17.09.2026: IVR 19 с у 13 строк из 27, остальное — строки «начало дозвона»,
        # разбросанные по ожиданию. При пороге в половину длина то была, то пропадала.
        rows = [('3006', 0, 29), ('3006', 19, 12), ('3006', 20, 1), ('3006', 38, 2)]
        rows += [('3006', d, 1) for d in (7, 8, 10, 26, 39, 43, 54, 70, 71, 81, 93, 116)]
        # 3000: в очередь ведут автоинформатор 15 с и IVR 19 с — берётся более частый вход.
        rows += [('3000', 0, 40), ('3000', 15, 11), ('3000', 19, 8)]
        rows += [('3000', d, 1) for d in (3, 11, 18, 21, 25, 38, 45, 46, 73, 122)]
        self.assertEqual(S.announcement_seconds_from_deltas(rows), {'3006': 19, '3000': 15})

    def test_row_starting_at_the_dial_still_waits_from_queue_entry(self):
        # 3041, 10.09.2026: пришёл 00:11:50, автоинформатор 26 с, оператор снял трубку в 00:12:53.
        # Станция отдала строку с началом в 00:12:51 — за две секунды до ответа. От неё ожидание
        # выходило 2 с и звонок попадал в SL, а клиент ждал в очереди 37 с.
        call = touch(started='2026-09-10 00:12:51', answered='2026-09-10 00:12:53', talk=152,
                     queue='3041', linkedid=linkedid_at('2026-09-10 00:11:50'))
        call['dial_seconds'] = 154
        out = S.attach_queue_entry([call], {'3041': 26})
        self.assertEqual(out[0]['queued_at'], '2026-09-10 00:12:16')
        totals = S.aggregate(out, sl_seconds=20)['totals']
        self.assertEqual(totals['avg_wait_seconds'], 37)
        self.assertEqual(totals['served_sl'], 0)
        self.assertAlmostEqual(totals['sl'], 0.0)

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


class GroupTests(unittest.TestCase):
    """ТЗ #339: фильтр «Все / Основа / ЯР / Поток 1 / Поток 2» пересчитывает всё табло."""

    MEMBERSHIPS = {
        1: {'group_id': 36, 'group_name': 'Ешан Алмас группа Основа', 'model': 'op_osnova'},
        2: {'group_id': 15, 'group_name': 'Айтқалиев Айдын Беркінұлы группа ЯР', 'model': 'op_yandex_reg'},
        3: {'group_id': 38, 'group_name': 'Айткалиев Айдын - Поток 2', 'model': 'op_potok'},
        4: {'group_id': 14, 'group_name': 'Шыңғысбаева Ақерке Жасұланқызы группа', 'model': 'op_potok'},
        5: {'group_id': 13, 'group_name': 'Адилет группа Верификатор', 'model': 'op_verificator'},
    }
    PEOPLE = [{'id': 1, 'name': 'Основин', 'sip_number': '6650'},
              {'id': 2, 'name': 'Яров', 'sip_number': '6651'},
              {'id': 3, 'name': 'Второй', 'sip_number': '6652'},
              {'id': 4, 'name': 'Первый', 'sip_number': '6653'},
              {'id': 5, 'name': 'Верификатор', 'sip_number': None}]

    def _snapshot(self, touches, queue_owners=None):
        return S.assemble(day=date(2026, 9, 15), touches=touches,
                          people=S.on_board(self.PEOPLE, self.MEMBERSHIPS),
                          live_statuses={1: {'status_key': 'готов', 'seconds': 5},
                                         2: {'status_key': 'занят', 'seconds': 9}},
                          status_entry=status_entry, resolve_name=None, bridge_state=None,
                          now=datetime(2026, 9, 15, 12, 0), memberships=self.MEMBERSHIPS,
                          queue_owners=queue_owners or {}, phone_events=[])

    def test_label_follows_the_model_and_potoks_are_numbered_by_group_id(self):
        """Имя группы содержит ФИО супервайзера — подпись берётся из модели, как в «Воронке ОП»."""
        self.assertEqual([(g['id'], g['label']) for g in S.group_catalog(self.MEMBERSHIPS)],
                         [(36, 'Основа'), (15, 'ЯР'), (14, 'Поток 1'), (38, 'Поток 2')])
        single = {k: v for k, v in self.MEMBERSHIPS.items() if k != 3}
        self.assertIn({'id': 14, 'label': 'Поток'}, S.group_catalog(single))
        stranger = {9: {'group_id': 70, 'group_name': 'Стажёры', 'model': ''}}
        self.assertEqual(S.group_catalog(stranger), [{'id': 70, 'label': 'Стажёры'}])

    def test_verificators_are_not_on_the_phone_board(self):
        """На линии верификаторов нет: ни звонков, ни событий телефона за две недели."""
        self.assertEqual([p['id'] for p in S.on_board(self.PEOPLE, self.MEMBERSHIPS)], [1, 2, 3, 4])
        self.assertEqual(len(S.on_board([{'id': 99, 'sip_number': '7000'}], self.MEMBERSHIPS)), 1)

    def test_queue_belongs_to_the_group_that_answers_most_of_it(self):
        ext_group = {'6650': 36, '6651': 15}
        rows = [('3010', '6650', 30), ('3010', '6651', 2),     # Основа
                ('3001,3002', '6650', 5),                      # через две очереди — первая по номеру
                ('3020', '6650', 3), ('3020', '6651', 3),       # ничья — очередь ничья
                ('3030', '6999', 10)]                          # чужой номер не голосует
        self.assertEqual(S.queue_owner_groups(rows, ext_group), {'3010': 36, '3001': 36})

    def test_groups_sum_to_the_department_and_queue_drops_go_to_the_queue_owner(self):
        touches = [
            touch(ext='6650', queue='3010'),                                        # Основа приняла
            touch(call_type='Входящий (не приняли)', talk=0, ext='', queue='3010'),  # брошен в очереди
            touch(call_type='Исходящий', talk=30, ext='6651', queue=''),             # ЯР
            touch(call_type='Исходящий', talk=0, ext='6653', queue=''),              # Поток 1
        ]
        snap = self._snapshot(touches, queue_owners={'3010': 36})
        groups = {g['label']: g for g in snap['groups']}
        self.assertEqual(list(groups), ['Основа', 'ЯР', 'Поток 1', 'Поток 2'])
        self.assertEqual((groups['Основа']['totals']['arrived'], groups['Основа']['totals']['missed']), (2, 1))
        self.assertAlmostEqual(groups['Основа']['totals']['ar'], 0.5)
        self.assertEqual(groups['ЯР']['totals']['outgoing'], 1)
        self.assertEqual(groups['Поток 1']['totals']['outgoing'], 1)
        for key in ('arrived', 'answered', 'missed', 'outgoing'):
            self.assertEqual(sum(g['totals'][key] for g in groups.values()), snap['totals'][key], key)
        self.assertEqual(groups['Основа']['now']['operators_online'], 1)
        self.assertEqual(groups['ЯР']['now']['operators_talking'], 1)
        self.assertEqual(set(groups['Основа']['hourly'][10]), {'hour', 'arrived', 'answered', 'missed', 'outgoing'})
        self.assertEqual(groups['Основа']['hourly'][10]['arrived'], 2)
        labels = {row['name']: row['group_label'] for row in snap['operators']}
        self.assertEqual(labels['Первый'], 'Поток 1')
        self.assertNotIn('Верификатор', labels)

    def test_unowned_queue_and_foreign_number_stay_only_in_all(self):
        touches = [touch(call_type='Входящий (не приняли)', talk=0, ext='', queue='3099'),
                   touch(ext='6999', queue='3010')]
        snap = self._snapshot(touches, queue_owners={'3010': 36})
        self.assertEqual(snap['totals']['arrived'], 2)
        self.assertEqual(sum(g['totals']['arrived'] for g in snap['groups']), 0)

    def test_without_memberships_the_board_has_no_groups(self):
        snap = S.assemble(day=date(2026, 9, 15), touches=[touch()], people=self.PEOPLE[:1],
                          live_statuses={}, status_entry=status_entry, resolve_name=None,
                          bridge_state=None, now=datetime(2026, 9, 15, 12, 0))
        self.assertEqual(snap['groups'], [])
        self.assertEqual((snap['operators'][0]['group_label'], snap['operators'][0]['entry_at']), ('', None))


def at(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')


class EntryAndJournalTests(unittest.TestCase):
    """Время входа — начало смены, журнал — календарные сутки. Случаи — с прода 17.09.2026."""

    DAY = date(2026, 9, 17)

    def entry(self, events):
        return S.entry_moment([(at(t), k) for t, k in events], self.DAY, status_entry)

    def test_entry_is_the_start_of_todays_block_not_a_later_relogin(self):
        events = [('2026-09-17 08:57:55', 'перезвон'), ('2026-09-17 08:59:56', 'готов'),
                  ('2026-09-17 13:00:00', 'выключен'), ('2026-09-17 14:00:00', 'готов')]
        self.assertEqual(self.entry(events), at('2026-09-17 08:57:55'))

    def test_night_shift_keeps_its_start_yesterday_through_a_reconnect(self):
        """Кенжебай: 19:53 → 08:00, в 07:01 телефон вышел и вошёл за пять секунд."""
        events = [('2026-09-16 19:53:44', 'перезвон'), ('2026-09-16 21:36:53', 'перерыв'),
                  ('2026-09-16 23:55:54', 'перерыв'),
                  ('2026-09-17 00:02:36', 'готов'), ('2026-09-17 03:48:37', 'готов'),
                  ('2026-09-17 06:08:57', 'занят'), ('2026-09-17 07:01:44', 'выключен'),
                  ('2026-09-17 07:01:49', 'перезвон'), ('2026-09-17 08:00:03', 'выключен')]
        self.assertEqual(self.entry(events), at('2026-09-16 19:53:44'))

    def test_evening_shift_past_midnight_does_not_replace_the_morning_entry(self):
        """Артаев: смена до 00:02, утром вход в 08:28 — входом сегодня считается утро."""
        evening = [('2026-09-16 17:44:43', 'перезвон'), ('2026-09-16 20:30:00', 'занят'),
                   ('2026-09-16 23:59:58', 'готов'),
                   ('2026-09-17 00:00:14', 'занят'), ('2026-09-17 00:02:15', 'выключен')]
        self.assertEqual(self.entry(evening + [('2026-09-17 08:28:08', 'занят')]), at('2026-09-17 08:28:08'))
        self.assertEqual(self.entry(evening), at('2026-09-16 17:44:43'))

    def test_phone_silent_since_yesterday_has_no_entry(self):
        self.assertIsNone(self.entry([('2026-09-16 20:48:05', 'готов')]))
        self.assertIsNone(self.entry([('2026-09-17 09:00:00', 'выключен')]))
        self.assertIsNone(self.entry([]))

    def test_entry_is_attached_to_rows(self):
        rows = [{'id': 1}, {'id': 2}, {'id': None}]
        S.attach_entry_times(rows, [event(1, '2026-09-17 08:00:00', 'готов')], self.DAY, status_entry)
        self.assertEqual([r['entry_at'] for r in rows], ['2026-09-17T08:00:00', None, None])

    def test_journal_starts_at_midnight_with_the_carried_status_and_merges_repeats(self):
        events = [(at(t), k) for t, k in (
            ('2026-09-16 19:53:44', 'перезвон'), ('2026-09-16 21:36:53', 'готов'),
            ('2026-09-16 23:55:54', 'перерыв'), ('2026-09-17 00:02:36', 'готов'), ('2026-09-17 00:05:00', 'готов'),
            ('2026-09-17 00:11:03', 'занят'))]
        entry, segments = S.status_journal(events, self.DAY, at('2026-09-17 00:12:03'), status_entry)
        self.assertEqual(entry, at('2026-09-16 19:53:44'))
        self.assertEqual([(s['status_label'], s['start_at'], s['end_at'], s['seconds']) for s in segments], [
            ('Перерыв', '2026-09-17T00:00:00', '2026-09-17T00:02:36', 156),
            ('Активный', '2026-09-17T00:02:36', '2026-09-17T00:11:03', 507),
            ('В разговоре', '2026-09-17T00:11:03', None, 60),
        ])

    def test_journal_after_logout_or_long_silence_starts_with_today(self):
        now = at('2026-09-17 10:00:00')
        logout = [(at('2026-09-16 23:00:00'), 'выключен'), (at('2026-09-17 09:00:00'), 'готов')]
        self.assertEqual(S.status_journal(logout, self.DAY, now, status_entry)[1][0]['start_at'],
                         '2026-09-17T09:00:00')
        stale = [(at('2026-09-16 20:48:05'), 'готов')]
        self.assertEqual(S.status_journal(stale, self.DAY, now, status_entry), (None, []))


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
        self.touch_days = []

        def day_touches(cursor, day):
            self.touch_days.append(day)
            return list(self.touches)

        for name, value in (('day_touches_compact', day_touches),
                            ('agent_state', lambda cursor: {'connected': True, 'live_at': None,
                                                            'last_seen_at': None}),
                            ('load_directory', lambda cursor: {})):
            patcher = mock.patch.object(op_routes.queries, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.memberships = {}
        self.queue_answers = []
        self.journal_events = []
        for name, value in (
                ('load_phone_events', lambda cursor, ids, day, lookback_hours=0: list(self.phone_events)),
                ('load_group_memberships', lambda cursor, ids, day: dict(self.memberships)),
                ('load_queue_answers', lambda cursor, day, days=7: list(self.queue_answers)),
                ('load_journal_events', lambda cursor, operator_id, day: list(self.journal_events))):
            patcher = mock.patch.object(op_routes, name, value)
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
        app.register_blueprint(self._build_blueprint(
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

    def _build_blueprint(self, **kwargs):
        self.bp = op_routes.build_op_wallboard_blueprint(**kwargs)
        return self.bp

    def test_day_parts_count_a_past_day_the_same_way(self):
        """Отбивке в полночь нужны итоги закончившихся суток — тем же расчётом, что снимок:
        вход в очередь по автоинформатору, ответ по телефону, разрез по часам."""
        self.touches = [touch(started='2026-09-16 23:10:00', answered='', talk=70, queue='3034',
                              linkedid=linkedid_at('2026-09-16 23:10:00'))]
        self.announcement_deltas = [('3034', 0, 100), ('3034', 10, 80)]
        self.phone_events = [event(1, datetime(2026, 9, 16, 23, 10, 15)),
                             event(1, datetime(2026, 9, 16, 23, 11, 11), 'готов')]
        parts = self.bp.day_parts(date(2026, 9, 16))
        self.assertEqual(self.touch_days, [date(2026, 9, 16)])
        self.assertEqual(parts['day'], '2026-09-16')
        self.assertEqual(parts['totals']['avg_wait_seconds'], 5)   # 15 с от прихода − 10 с сообщения
        self.assertEqual(parts['hourly'][23]['arrived'], 1)
        self.assertAlmostEqual(parts['hourly'][23]['sl'], 1.0)

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

    def test_snapshot_carries_groups_and_entry_time(self):
        """ТЗ #339: разрез группы и время входа приезжают в том же снимке, что и итоги отдела."""
        self.requester['role'] = 'admin'
        today = datetime.now(timezone(timedelta(hours=5))).date()
        self.memberships = {1: {'group_id': 36, 'group_name': 'Ешан Алмас группа Основа',
                                'model': 'op_osnova'}}
        self.phone_events = [event(1, datetime.combine(today, time(0, 0, 1)), 'готов')]
        body = self.client.get('/api/op_wallboard/snapshot').get_json()
        self.assertEqual([(g['id'], g['label']) for g in body['groups']], [(36, 'Основа')])
        self.assertEqual(body['groups'][0]['totals']['arrived'], body['totals']['arrived'])
        row = body['operators'][0]
        self.assertEqual((row['group_id'], row['group_label']), (36, 'Основа'))
        self.assertEqual(row['entry_at'], '%sT00:00:01' % today.isoformat())

    def test_journal_is_open_only_for_people_on_the_board(self):
        self.assertEqual(self.client.get('/api/op_wallboard/journal?operator_id=1').status_code, 403)
        self.requester['role'] = 'admin'
        self.assertEqual(self.client.get('/api/op_wallboard/journal?operator_id=x').status_code, 400)
        self.assertEqual(self.client.get('/api/op_wallboard/journal?operator_id=2').status_code, 404)

    def test_journal_returns_segments_of_the_day(self):
        self.requester['role'] = 'admin'
        today = datetime.now(timezone(timedelta(hours=5))).date()
        self.journal_events = [(datetime.combine(today, time(0, 0, 1)), 'готов'),
                               (datetime.combine(today, time(0, 0, 5)), 'занят')]
        response = self.client.get('/api/op_wallboard/journal?operator_id=1')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['name'], 'Иванов')
        self.assertEqual(body['entry_at'], '%sT00:00:01' % today.isoformat())
        self.assertEqual([(s['status_label'], s['seconds']) for s in body['segments']][:1],
                         [('Активный', 4)])
        self.assertIsNone(body['segments'][-1]['end_at'])

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

    def test_group_filter_recounts_the_whole_board_from_the_same_snapshot(self):
        """ТЗ #339: плитки, график и список читают снимок группы; своего запроса у фильтра нет."""
        self.assertIn('const view = useMemo(() => opGroupView(snapshot, group), [group, snapshot]);', self.view)
        self.assertIn('<IosSegmented', self.view)
        self.assertEqual(self.view.count('<OpWallboardBody snapshot={view}'), 2)   # страница и стена
        groups = (MONITORING / 'opWallboardGroups.js').read_text(encoding='utf-8-sig')
        self.assertIn("{ value: OP_GROUP_ALL, label: 'Все' }", groups)
        self.assertIn('operators: (snapshot.operators || []).filter((row) => row.group_id === group.id)', groups)

    def test_table_has_entry_time_and_group_column_only_in_all(self):
        self.assertIn('<th className={head}>Время входа</th>', self.view)
        self.assertIn('{showGroup ? <th className={head}>Группа</th> : null}', self.view)
        self.assertIn('const showGroupColumn = !group && groupOptions.length > 1;', self.view)

    def test_name_opens_the_journal_which_follows_status_changes_without_polling(self):
        journal = (MONITORING / 'OpStatusJournal.jsx').read_text(encoding='utf-8-sig')
        self.assertIn('onClick={canOpen ? () => onOpenJournal(row) : undefined}', self.view)
        self.assertIn("row.id != null && row.status_key !== 'unknown' && Boolean(onOpenJournal)", self.view)
        # Строка журнала — из полного снимка: смена группы не закрывает журнал.
        self.assertIn('(snapshot?.operators || []).find((row) => row.id != null && row.id === journalOperatorId)', self.view)
        self.assertIn('journalRow.status_key}|${journalRow.status_at}', self.view)
        self.assertIn('}, [apiBaseUrl, operatorId, refreshKey]);', journal)
        self.assertNotIn('setInterval(() => fetch', journal)
        self.assertIn("export const OP_JOURNAL_PATH = '/api/op_wallboard/journal';", self.shared)
        # Esc при открытом журнале на стене закрывает журнал, а не стену.
        self.assertIn('closeOnEscape={!journalRow}', self.view)

    def test_quiet_phones_fold_and_hour_axis_is_shared_by_groups(self):
        """Два десятка «Нет событий» сворачиваются в одну строку; ось часов — из полного снимка."""
        self.assertIn("row.in_roster !== false && row.status_key === 'unknown'", self.view)
        self.assertIn('без событий телефона', self.view)
        self.assertIn('const hourRange = useMemo(() => opHourRange(snapshot), [snapshot]);', self.view)
        self.assertEqual(self.view.count('hourRange={hourRange}'), 2)

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
