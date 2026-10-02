# -*- coding: utf-8 -*-
"""Живой хвост сегодняшних суток: мост досылает только изменения, портал принимает
только сегодня/вчера.

Что закреплено:
  * первый проход — полный день и все касания уезжают; второй без изменений — ни
    одного запроса к порталу, кроме пульса раз в минуту; изменившееся касание уезжает одно;
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
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from flask import Flask

from cdr import agent_auth, queue_facts, routes as cdr_routes
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

    def test_no_change_within_a_minute_means_no_request(self):
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.tail.step(now=1000.0)
        self.tail.step(now=1030.0)
        self.assertEqual(len(self.posts), 1, 'без изменений портал дёргать нельзя')

    def test_silence_longer_than_a_minute_sends_a_heartbeat(self):
        # Ночью за час два звонка: без пульса live_at на портале замирал, и табло ОП
        # объявляло мост умершим, хотя он опрашивал станцию каждые двадцать секунд.
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.tail.step(now=1000.0)
        self.tail.step(now=1000.0 + live.HEARTBEAT_SECONDS)
        self.assertEqual(len(self.posts), 2)
        path, payload = self.posts[1]
        self.assertEqual(path, 'live')
        self.assertEqual((payload['day'], payload['touches'], payload['removed'], payload['heartbeat']),
                         ('2026-09-15', [], [], True))
        self.tail.step(now=1000.0 + live.HEARTBEAT_SECONDS + 10)
        self.assertEqual(len(self.posts), 2, 'пульс — не чаще раза в минуту')

    def test_empty_day_still_pulses(self):
        # Сразу после полуночи касаний нет, но портал должен знать, что хвост жив.
        self.tail.step(now=1000.0)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0][1]['touches'], [])
        self.assertTrue(self.posts[0][1]['heartbeat'])

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


class LiveTailFullPassFailureTests(unittest.TestCase):
    def test_a_failed_full_pass_does_not_repeat_on_every_step(self):
        """Полный проход упал (потолок строк, сбой базы) — следующий шаг идёт обычным окном
        последних часов, а не тем же полным запросом до полуночи."""
        posts = []
        station = _Station([cdr_row('1.1', '2026-09-15 09:00:00')])
        tail = live.LiveTail(lambda path, payload: posts.append((path, payload)),
                             station, 20, today=lambda: TODAY)
        tail.step(now=1000.0)                         # первый проход — полный, удачный
        tail.cycles = live.FULL_REFRESH_EVERY        # пришла очередь полного прохода
        station.fail = StationError('потолок строк', 'bad_payload')
        self.assertTrue(tail.maybe_step(now=2000.0))
        self.assertEqual(tail.failures, 1)
        station.fail = None
        tail.step(now=2100.0)
        self.assertNotEqual(station.calls[-1][0], '2026-09-15T00:00:00',
                            'после отказа полного прохода — обычное окно, а не снова весь день')


class _Journal:
    """База станции: журнал очередей (события, попавшие в окно запроса) и, с моста 1.5.0,
    сам CDR — его мост тоже берёт оттуда, а не у надстройки."""

    def __init__(self, rows=None, fail=None, cdr=None):
        self.rows = rows or []
        self.fail = fail
        self.windows = []
        self.enabled = True
        self.cdr = list(cdr or [])
        self.cdr_calls = []

    def facts(self, start, end):
        self.windows.append((start, end))
        if self.fail:
            raise self.fail
        return queue_facts.build_facts([r for r in self.rows if start <= r['time'] < end])

    def iter_cdr(self, from_dt, to_dt, on_page=None):
        self.cdr_calls.append((from_dt, to_dt))
        return iter(self.cdr)

    def describe(self):
        return 'mysql://pbx.invalid:3306/asteriskcdrdb'


def queue_event(callid, time_text, event, **data):
    return dict({'time': datetime.strptime(time_text, '%Y-%m-%d %H:%M:%S'), 'callid': callid,
                 'queuename': '3000', 'agent': 'NONE', 'event': event,
                 'data1': '', 'data2': '', 'data3': ''}, **data)


class LiveTailJournalTests(unittest.TestCase):
    """Точные факты очереди едут вместе с касаниями и не теряются между окнами."""

    def setUp(self):
        self.posts = []
        self.station = _Station()
        self.journal = _Journal()
        self.tail = live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                                  self.station, 20, today=lambda: TODAY, pbxdb=self.journal)
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00')]
        self.journal.rows = [
            queue_event('1.1', '2026-09-15 09:00:26', 'ENTERQUEUE', data2='+77015550001'),
            queue_event('1.1', '2026-09-15 09:00:30', 'CONNECT', agent='op', data1='4'),
        ]

    def test_touch_carries_the_exact_queue_fields(self):
        self.tail.step()
        touch = self.posts[0][1]['touches'][0]
        self.assertEqual(touch['queued_at'], '2026-09-15 09:00:26')
        self.assertEqual(touch['answered_at'], '2026-09-15 09:00:30')
        self.assertEqual(touch['wait_seconds'], 4)
        self.assertEqual(set(touch), set(live.TOUCH_FIELDS))

    def test_journal_is_asked_for_the_same_window_as_cdr(self):
        self.tail.step()
        self.assertEqual(self.journal.windows[0],
                         (datetime(2026, 9, 15), datetime(2026, 9, 16, 1)))

    def test_late_hangup_updates_the_touch(self):
        """Ответ и завершение приходят разными циклами: касание обязано уехать второй раз,
        иначе разговор и сторона отбоя остались бы на портале пустыми."""
        self.tail.step()
        self.journal.rows.append(queue_event('1.1', '2026-09-15 09:02:00', 'COMPLETECALLER',
                                             agent='op', data1='4', data2='86'))
        self.tail.step()
        touch = self.posts[1][1]['touches'][0]
        self.assertEqual((touch['talk_measured_seconds'], touch['hangup_side']), (86, 'client'))

    def test_narrow_window_does_not_strip_the_morning_call(self):
        """Приращение спрашивает журнал за последние два часа, а касания пересобирает за
        день: без накопления утренний звонок уехал бы на портал без входа в очередь."""
        self.tail.step()
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00'),
                             cdr_row('2.2', '2026-09-15 15:00:00', src='+77015550002')]
        self.tail.step()
        touches = {t['linkedid']: t for t in self.posts[1][1]['touches']}
        self.assertEqual(self.tail.facts['1.1']['queued_at'], datetime(2026, 9, 15, 9, 0, 26))
        self.assertNotIn('1.1', touches, 'у утреннего звонка ничего не изменилось')

    def test_unavailable_journal_keeps_the_tail_and_the_known_facts(self):
        self.tail.step()
        self.journal.fail = RuntimeError('база станции не ответила')
        self.station.rows = [cdr_row('1.1', '2026-09-15 09:00:00', billsec=45, duration=55)]
        self.tail.step()
        touch = self.posts[1][1]['touches'][0]
        self.assertEqual(touch['talk_seconds'], 45, 'касания едут и без журнала')
        self.assertEqual(touch['queued_at'], '2026-09-15 09:00:26',
                         'накопленное не стирается отказом журнала')

    def test_tail_works_without_a_journal_at_all(self):
        tail = live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                             self.station, 20, today=lambda: TODAY)
        tail.step()
        touch = self.posts[0][1]['touches'][0]
        self.assertEqual(touch['queued_at'], None)
        self.assertEqual(touch['wait_seconds'], None)
        self.assertIsNone(self.posts[0][1]['queue_calls'], 'без журнала — «не знаю», а не «никого»')


class _CoveringJournal(_Journal):
    """Журнал, который, как настоящий `PbxDb`, говорит, докуда прочитал без обрыва."""

    def __init__(self, rows=None, fail=None):
        super().__init__(rows, fail)
        self.covered_until = None

    def facts(self, start, end):
        self.covered_until = None
        facts = super().facts(start, end)
        self.covered_until = end
        return facts


def greeting_drop_row(linkedid, calldate):
    """Отбой на приветствии очереди 3010 за секунду до входа: строка как у брошенного в
    очереди — агента нет, записи нет, 6 с."""
    return cdr_row(linkedid, calldate, src='+77776084066', dst='3010', dcontext='ext-queues',
                   dstchannel='', recordingfile='', disposition='ANSWERED', billsec=6, duration=6,
                   did='7475777778', channel='PJSIP/+77475777778-0006a5ca')


class LiveTailBeforeQueueTests(unittest.TestCase):
    """Мост 1.4.1: непринятый без входа в очередь едет «не дошёл до очереди»."""

    def setUp(self):
        self.posts = []
        self.station = _Station([cdr_row('1.1', '2026-09-15 09:00:00'),
                                 greeting_drop_row('2.2', '2026-09-15 10:00:00')])
        self.journal = _CoveringJournal([
            queue_event('1.1', '2026-09-15 09:00:26', 'ENTERQUEUE', data2='+77015550001'),
            queue_event('1.1', '2026-09-15 09:00:30', 'CONNECT', agent='op', data1='4'),
        ])

    def _tail(self, journal):
        return live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                             self.station, 20, today=lambda: TODAY, pbxdb=journal)

    def test_call_without_an_entry_goes_as_before_the_queue(self):
        self._tail(self.journal).step()
        touches = {t['linkedid']: t for t in self.posts[0][1]['touches']}
        self.assertEqual(touches['2.2']['call_type'], 'Входящий (не дошёл до очереди)')
        self.assertEqual(touches['2.2']['queue'], '3010')
        self.assertEqual(touches['1.1']['call_type'], 'Входящий')

    def test_unavailable_journal_decides_nothing(self):
        self.journal.fail = RuntimeError('база станции не ответила')
        self._tail(self.journal).step()
        touch = [t for t in self.posts[0][1]['touches'] if t['linkedid'] == '2.2'][0]
        self.assertEqual(touch['call_type'], 'Входящий (не приняли)')

    def test_journal_without_a_coverage_mark_decides_nothing(self):
        """Старый источник без отметки: по-прежнему, как до 1.4.1."""
        self._tail(_Journal(self.journal.rows)).step()
        touch = [t for t in self.posts[0][1]['touches'] if t['linkedid'] == '2.2'][0]
        self.assertEqual(touch['call_type'], 'Входящий (не приняли)')

    def test_the_bridge_day_job_decides_the_same_way(self):
        """Суточная присылка (перечитка старых суток) идёт тем же правилом, что и хвост."""
        from cdr_bridge import agent as agent_mod
        # С 1.5.0 строки CDR — из базы станции, той же, где журнал очередей.
        self.journal.cdr = list(self.station.rows)
        bridge = agent_mod.Bridge({'portal': 'http://portal.invalid', 'token': 'x',
                                   'station': 'http://127.0.0.1:9', 'login': '', 'password': ''},
                                  station=self.station, pbxdb_source=self.journal)
        sent = []
        bridge._post = lambda path, payload: sent.append((path, payload)) or {'complete': True}
        self.assertTrue(bridge.do_day({'day': '2026-09-15', 'from_dt': '2026-09-15T00:00:00',
                                       'to_dt': '2026-09-16T01:00:00'}))
        self.assertEqual(self.station.calls, [], 'к надстройке мост с базой не ходит')
        touches = {t['linkedid']: t for t in sent[0][1]['touches']}
        self.assertEqual(touches['2.2']['call_type'], 'Входящий (не дошёл до очереди)')
        self.assertEqual(touches['2.2']['result'], 'Сброс до очереди')
        self.assertEqual(touches['1.1']['queued_at'], '2026-09-15 09:00:26')


def out_row(linkedid, calldate, billsec=139, client='+77000000101'):
    """Исходящий оператора 6687 через транк, с разговором и записью."""
    return {'calldate': calldate, 'clid': '', 'src': '77475777778', 'dst': '7778*' + client,
            'dcontext': 'from-internal', 'channel': 'PJSIP/6687-0006fb28',
            'dstchannel': 'PJSIP/+77475777778-0006fb29', 'duration': billsec + 13,
            'billsec': billsec, 'disposition': 'ANSWERED', 'uniqueid': linkedid, 'did': '',
            'linkedid': linkedid, 'recording_url': None,
            'recordingfile': 'out-7778*%s-6687-20260915-105759-%s.wav' % (client, linkedid)}


class _CelJournal(_Journal):
    """База станции, у которой есть и CEL: кто положил трубку (мост 1.6.0)."""

    def __init__(self, sides=None, cel_fail=None, **kwargs):
        super().__init__(**kwargs)
        self.sides = dict(sides or {})
        self.cel_fail = cel_fail
        self.cel_calls = []

    def hangup_sides(self, linkedids):
        self.cel_calls.append(list(linkedids))
        if self.cel_fail:
            raise self.cel_fail
        return {linkedid: self.sides[linkedid] for linkedid in linkedids if linkedid in self.sides}


class LiveTailHangupTests(unittest.TestCase):
    """Отбой случается один раз: хвост спрашивает о звонке однажды и помнит ответ."""

    def setUp(self):
        self.posts = []
        self.station = _Station([out_row('7.7', '2026-09-15 10:57:59'),
                                 cdr_row('1.1', '2026-09-15 09:00:00')])
        self.journal = _CelJournal(sides={'7.7': 'client'})
        self.tail = live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                                  self.station, 20, today=lambda: TODAY, pbxdb=self.journal)

    def _touch(self, post, linkedid):
        return next(t for t in self.posts[post][1]['touches'] if t['linkedid'] == linkedid)

    def test_outgoing_talk_rides_with_its_side(self):
        self.tail.step(now=1000.0)
        self.assertEqual(self._touch(0, '7.7')['hangup_side'], 'client')
        self.assertEqual(self.journal.cel_calls, [['7.7']], 'входящий у CEL не спрашивается')

    def test_only_this_days_calls_are_asked(self):
        """Час запаса до полуночи склеивается, но на портал не едет — и CEL о нём не спрашиваем."""
        self.station.rows.append(out_row('6.6', '2026-09-14 23:30:00', client='+77000000102'))
        self.journal.sides['6.6'] = 'operator'
        self.tail.step(now=1000.0)
        self.assertEqual(self.journal.cel_calls, [['7.7']])

    def test_known_answer_is_not_asked_again(self):
        self.tail.step(now=1000.0)
        self.tail.cycles = live.FULL_REFRESH_EVERY       # и полный проход не спрашивает заново
        self.tail.step(now=1020.0)
        self.assertEqual(self.journal.cel_calls, [['7.7']])

    def test_call_not_yet_written_whole_is_asked_again_and_then_ships_again(self):
        """CEL ещё не знает звонок целиком — касание едет без стороны, а когда она появится,
        уезжает второй раз: отпечаток касания включает сторону отбоя."""
        self.journal.sides = {}
        self.tail.step(now=1000.0)
        self.assertFalse(self._touch(0, '7.7')['hangup_side'])
        self.journal.sides = {'7.7': 'operator'}
        self.tail.step(now=1020.0)
        self.assertEqual(len(self.journal.cel_calls), 2)
        self.assertEqual(self.posts[1][1]['touches'], [dict(self._touch(0, '7.7'),
                                                            hangup_side='operator')])

    def test_asking_stops_after_the_attempt_ceiling(self):
        self.journal.sides = {}
        for cycle in range(live.HANGUP_ATTEMPTS + 3):
            self.tail.step(now=1000.0 + 20 * cycle)
        self.assertEqual(len(self.journal.cel_calls), live.HANGUP_ATTEMPTS)

    def test_cel_failure_keeps_the_tail_and_counts_as_an_attempt(self):
        self.journal.cel_fail = RuntimeError('база станции не ответила')
        self.tail.step(now=1000.0)
        self.assertFalse(self._touch(0, '7.7')['hangup_side'], 'касания едут и без CEL')
        self.assertEqual(self.tail.hangup_asked, {'7.7': 1})

    def test_new_day_forgets_the_answers(self):
        self.tail.step(now=1000.0)
        self.tail._today = lambda: TODAY + timedelta(days=1)
        self.station.rows = []
        self.tail.step(now=1020.0)
        self.assertEqual((self.tail.hangups, self.tail.hangup_asked), ({}, {}))

    def test_without_a_database_nothing_is_asked(self):
        tail = live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                             self.station, 20, today=lambda: TODAY)
        tail.step(now=1000.0)
        self.assertFalse(self._touch(0, '7.7')['hangup_side'])


def _epoch(text):
    """Местное время Алматы → секунды эпохи, как их передаёт мосту time.time()."""
    moment = datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    return moment.replace(tzinfo=timezone(timedelta(hours=5))).timestamp()


class LiveTailQueueCallsTests(unittest.TestCase):
    """Звонки, которых ещё нет в CDR (мост 1.4.0, задача #291)."""

    def setUp(self):
        self.posts = []
        self.station = _Station([cdr_row('1.1', '2026-09-15 09:00:00')])
        self.journal = _Journal([
            queue_event('1.1', '2026-09-15 09:00:26', 'DID', data1='7475550078'),
            queue_event('1.1', '2026-09-15 09:00:26', 'ENTERQUEUE', data2='+77015550001'),
            # Перезвон того же клиента: вошёл в очередь, строки CDR ещё нет.
            queue_event('3.3', '2026-09-15 09:02:10', 'DID', data1='7475550078'),
            queue_event('3.3', '2026-09-15 09:02:10', 'ENTERQUEUE', data2='+77015550001'),
        ])
        self.tail = live.LiveTail(lambda path, payload: self.posts.append((path, payload)),
                                  self.station, 20, today=lambda: TODAY, pbxdb=self.journal)
        self.now = _epoch('2026-09-15 09:02:30')

    def test_call_without_a_cdr_row_rides_with_the_increment(self):
        self.tail.step(self.now)
        calls = self.posts[0][1]['queue_calls']
        self.assertEqual([c['linkedid'] for c in calls], ['3.3'])
        self.assertEqual(calls[0]['phone'], '7015550001')
        self.assertEqual(calls[0]['answered_at'], '')

    def test_answer_in_the_queue_alone_is_pushed_at_once(self):
        """Касания не изменились, а оператор взял трубку — портал должен узнать сейчас, а
        не с пульсом через минуту: от этого зависит, заведётся ли сделка на перезвон."""
        self.tail.step(self.now)
        self.journal.rows.append(queue_event('3.3', '2026-09-15 09:02:35', 'CONNECT',
                                             agent='op', data1='25'))
        self.tail.step(self.now + 20)
        self.assertEqual(len(self.posts), 2)
        payload = self.posts[1][1]
        self.assertEqual(payload['touches'], [])
        self.assertNotIn('heartbeat', payload)
        self.assertEqual(payload['queue_calls'][0]['answered_at'], '2026-09-15 09:02:35')

    def test_unchanged_queue_calls_do_not_trigger_a_push(self):
        self.tail.step(self.now)
        self.tail.step(self.now + 20)
        self.assertEqual(len(self.posts), 1)

    def test_heartbeat_carries_the_list_too(self):
        self.tail.step(self.now)
        self.tail.step(self.now + live.HEARTBEAT_SECONDS + 1)
        self.assertTrue(self.posts[1][1]['heartbeat'])
        self.assertEqual(len(self.posts[1][1]['queue_calls']), 1)

    def test_failed_journal_read_sends_unknown_not_empty(self):
        self.tail.step(self.now)
        self.journal.fail = RuntimeError('база станции не ответила')
        self.tail.step(self.now + live.HEARTBEAT_SECONDS + 1)
        self.assertIsNone(self.posts[1][1]['queue_calls'])


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
        self.queue_calls = []

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

    def save_queue_calls(self, cursor, calls):
        self.queue_calls.append(calls)


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

    def test_heartbeat_without_touches_marks_the_bridge_alive(self):
        response = self._live({'day': '2026-09-15', 'touches': [], 'removed': [], 'heartbeat': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json(), {'status': 'ok', 'stored': 0, 'removed': 0})
        self.assertTrue(self.recorder.seen[0]['live'])

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

    def test_queue_calls_are_cleaned_and_stored(self):
        response = self._live({'day': '2026-09-15', 'touches': [], 'queue_calls': [
            {'linkedid': '3.3', 'phone': '+7 701 555 00 01', 'queue': '3010',
             'queued_at': '2026-09-15 09:02:10', 'answered_at': '', 'ended': False},
            {'linkedid': '', 'phone': '77015550002'},          # без ключа — мимо
            {'linkedid': '4.4', 'phone': '123'},                # не номер — мимо
            'мусор',
        ]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.recorder.queue_calls, [[{
            'linkedid': '3.3', 'phone': '7015550001', 'queue': '3010',
            'queued_at': '2026-09-15 09:02:10', 'answered_at': '', 'ended': False}]])

    def test_empty_list_is_stored_but_unknown_is_not(self):
        # Пустой список — «в очередях никого», его надо записать; нет поля или None —
        # «мост не знает» (старая версия, журнал не ответил), прежний снимок не трогаем.
        self._live({'day': '2026-09-15', 'touches': [], 'queue_calls': []})
        self._live({'day': '2026-09-15', 'touches': [], 'queue_calls': None})
        self._live({'day': '2026-09-15', 'touches': []})
        self.assertEqual(self.recorder.queue_calls, [[]])

    def test_oversized_queue_calls_are_refused(self):
        calls = [{'linkedid': '%d.1' % i, 'phone': '7015550001'}
                 for i in range(cdr_routes.MAX_QUEUE_CALLS + 1)]
        response = self._live({'day': '2026-09-15', 'touches': [], 'queue_calls': calls})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.recorder.queue_calls, [])


if __name__ == '__main__':
    unittest.main()
