# -*- coding: utf-8 -*-
"""Живые звонки обзвона у Binotel (13.10.2026): один опрос call-details на компанию и общий кэш.

Прод 12.10.2026: call-details спрашивали по каждому поводу отдельно — карточка звонка каждые 3 с
на каждый звонок, добор исходов на каждом GET состояния, события телефона, вопрос автосброса, —
и Binotel отвечал «Requests are too frequent» почти на всё. Теперь один запрос на все живые
попытки компании не чаще трёх секунд, остальные читают кэш (dial_list.live_calls); ответ
водителя, пока плечо идёт, — разговор, а не финал (service.live_verdict); «нет данных» —
никогда не «класть плечо»; вебхук понимает настоящие поля Binotel (callDetails[…]).

Без базы и сети: курсор и клиент Binotel подменены.
"""
import contextlib
import os
import sys
import threading
import time
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binotel import client as binotel  # noqa: E402
from dial_list import live_calls  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


class _Clock:
    """Часы для кэша: monotonic и UTC идут вместе, двигаем руками."""

    def __init__(self):
        self.mono = 1000.0

    def __call__(self):
        return self.mono

    def wall(self):
        return T0 + timedelta(seconds=self.mono - 1000.0)

    def tick(self, seconds):
        self.mono += seconds


def _sync(fn):
    fn()


class _Fetch:
    """fetch для CompanyCalls.lookup: answers — что вернуть по очереди ({id: звонок} или исключение)."""

    def __init__(self, *answers, extra=(), after=None):
        self.answers, self.extra, self.after = list(answers), list(extra), after
        self.calls = []

    def __call__(self, wanted):
        asked = list(dict.fromkeys(list(wanted) + self.extra))
        self.calls.append(asked)
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, Exception):
            raise answer
        return asked, answer, self.after


def _call(gid, disposition, billsec=0):
    return {"general_call_id": gid, "disposition": disposition, "billsec": billsec, "waitsec": 5}


class CompanyCallsTests(unittest.TestCase):
    def _calls(self, submit=_sync):
        clock = _Clock()
        return live_calls.CompanyCalls('remote_cc', submit=submit, clock=clock, wall_clock=clock.wall), clock

    def test_constants(self):
        self.assertEqual((live_calls.REFRESH_SEC, live_calls.WAIT_SEC), (3.0, 1.5))
        self.assertEqual(dial_service.CALLS_REFRESH_SEC, live_calls.REFRESH_SEC)

    def test_one_request_answers_everybody_until_it_is_old(self):
        calls, clock = self._calls()
        fetch = _Fetch({'g1': _call('g1', 'CALLING'), 'g2': _call('g2', 'ONLINE')}, extra=['g2', 'g3'])
        seen = calls.lookup(['g1'], fetch)
        # Один запрос — на все живые звонки компании; кого Binotel не отдал — «звонка не видно».
        self.assertEqual(fetch.calls, [['g1', 'g2', 'g3']])
        self.assertEqual(seen['g1'].call['disposition'], 'CALLING')
        self.assertIsNone(calls.peek('g3').call)
        self.assertEqual(calls.requests, 1)
        # Другие спрашивающие в эти три секунды — из кэша, Binotel не спрашиваем.
        clock.tick(2.9)
        for gid in ('g1', 'g2', 'g3'):
            self.assertIn(gid, calls.lookup([gid], fetch))
        self.assertEqual(len(fetch.calls), 1)
        # Данные старше трёх секунд — новое обновление.
        clock.tick(0.2)
        calls.lookup(['g2'], fetch)
        self.assertEqual(len(fetch.calls), 2)

    def test_cadence_holds_even_when_data_is_wanted_fresher(self):
        calls, clock = self._calls()
        fetch = _Fetch({'g1': _call('g1', 'CALLING')}, {'g1': _call('g1', 'ONLINE')})
        calls.lookup(['g1'], fetch)
        clock.tick(1.0)
        # Нужны данные не старше 0.5 с, но от прошлого запроса прошла секунда — ответ из кэша.
        seen = calls.lookup(['g1'], fetch, max_age=0.5)
        self.assertEqual((len(fetch.calls), seen['g1'].call['disposition']), (1, 'CALLING'))
        # Своё min_interval (вопрос автосброса — 2 с) отпускает раньше обычных трёх секунд.
        clock.tick(1.1)
        seen = calls.lookup(['g1'], fetch, max_age=2.0, min_interval=2.0)
        self.assertEqual((len(fetch.calls), seen['g1'].call['disposition']), (2, 'ONLINE'))

    def test_rate_limit_pause_is_respected(self):
        calls, clock = self._calls()
        fetch = _Fetch({'g1': _call('g1', 'CALLING')},
                       binotel.RateLimited('too frequent', retry_after=5.0), {'g1': _call('g1', 'ONLINE')})
        calls.lookup(['g1'], fetch)
        clock.tick(3.5)
        with self.assertLogs('dial_list.live_calls', level='INFO') as logs:
            seen = calls.lookup(['g1'], fetch)
        self.assertTrue(any('просит паузу' in line for line in logs.output), logs.output)
        self.assertEqual(seen['g1'].call['disposition'], 'CALLING')          # прежние данные
        # Пауза Binotel (5 с) не кончилась — новых запросов нет, хотя темп уже позволил бы.
        clock.tick(3.5)
        calls.lookup(['g1'], fetch)
        self.assertEqual(len(fetch.calls), 2)
        clock.tick(2.0)
        self.assertEqual(calls.lookup(['g1'], fetch)['g1'].call['disposition'], 'ONLINE')
        self.assertEqual(len(fetch.calls), 3)

    def test_error_keeps_what_is_known(self):
        calls, clock = self._calls()
        fetch = _Fetch({'g1': _call('g1', 'CALLING')}, RuntimeError('Binotel HTTP 502'))
        calls.lookup(['g1'], fetch)
        clock.tick(3.1)
        with self.assertLogs('dial_list.live_calls', level='INFO'):
            seen = calls.lookup(['g1'], fetch)
        self.assertEqual(seen['g1'].call['disposition'], 'CALLING')
        self.assertEqual(calls.requests, 1)
        # Ничего не знали — ничего и нет.
        calls2, _ = self._calls()
        with self.assertLogs('dial_list.live_calls', level='INFO'):
            self.assertEqual(calls2.lookup(['g9'], _Fetch(RuntimeError('нет сети'))), {})

    def test_newer_than_asks_again_but_not_for_the_future(self):
        calls, clock = self._calls()
        fetch = _Fetch({'g1': _call('g1', 'ANSWER', 10)}, {'g1': _call('g1', 'ANSWER', 14)})
        calls.lookup(['g1'], fetch)
        sent = calls.peek('g1').wall
        clock.tick(3.0)
        # Нужны данные после момента, который ещё не наступил, — обновлять незачем (возраст 3 с — ещё в норме).
        calls.lookup(['g1'], fetch, max_age=10, newer_than=clock.wall() + timedelta(seconds=5))
        self.assertEqual(len(fetch.calls), 1)
        # Момент наступил, а данные старше него — обновление.
        seen = calls.lookup(['g1'], fetch, max_age=10, newer_than=sent + timedelta(seconds=1))
        self.assertEqual((len(fetch.calls), seen['g1'].call['billsec']), (2, 14))

    def test_cache_keeps_no_phone_numbers(self):
        # Номер водителя и данные сотрудника из ответа Binotel в кэш процесса не попадают.
        calls, clock = self._calls()
        answer = {'g1': dict(_call('g1', 'ANSWER', 20), external_number='77011234567', employee_name='Оператор',
                             internal_number='913')}
        calls.lookup(['g1'], _Fetch(answer))
        calls.put('g2', dict(_call('g2', 'BUSY'), external_number='77017654321'))
        for gid in ('g1', 'g2'):
            self.assertEqual(set(calls.peek(gid).call), set(live_calls.CALL_FIELDS), gid)
        self.assertNotIn('7701', repr(calls._entries))

    def test_put_is_fresh_data(self):
        calls, clock = self._calls()
        calls.put('g7', _call('g7', 'ANSWER', 33))
        fetch = _Fetch()
        self.assertEqual(calls.lookup(['g7'], fetch)['g7'].call['billsec'], 33)
        self.assertEqual(fetch.calls, [])
        calls.put('', _call('', 'ANSWER'))                                         # без id — мимо
        self.assertIsNone(calls.peek(''))

    def test_after_runs_once_the_answer_is_in_the_cache(self):
        calls, clock = self._calls()
        seen_in_after = []

        def after():
            seen_in_after.append(calls.peek('g1').call['disposition'])
            raise RuntimeError('сбой разбора')

        with self.assertLogs('dial_list.live_calls', level='ERROR'):
            calls.lookup(['g1'], _Fetch({'g1': _call('g1', 'BUSY')}, after=after))
        self.assertEqual(seen_in_after, ['BUSY'])
        self.assertEqual(calls.requests, 1)

    def test_single_flight_and_bounded_wait(self):
        # Настоящие потоки: медленный запрос один на всех, ждущие уходят с тем, что есть, не
        # дольше wait.
        calls = live_calls.CompanyCalls('remote_cc')
        release = threading.Event()
        fetch_calls = []

        def slow_fetch(wanted):
            fetch_calls.append(list(wanted))
            release.wait(5)
            return list(wanted), {g: _call(g, 'ONLINE') for g in wanted}, None

        results = []

        def ask():
            results.append(calls.lookup(['g1'], slow_fetch, wait=0.3))

        threads = [threading.Thread(target=ask) for _ in range(5)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        waited = time.monotonic() - started
        self.assertEqual(len(fetch_calls), 1)                                     # один запрос на всех
        self.assertEqual(results, [{}] * 5)                                        # не дождались — ничего не знаем
        self.assertLess(waited, 2.0)
        release.set()
        deadline = time.monotonic() + 3
        while calls.peek('g1') is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(calls.lookup(['g1'], slow_fetch)['g1'].call['disposition'], 'ONLINE')
        self.assertEqual(len(fetch_calls), 1)


    def test_stuck_refresh_is_abandoned_for_a_fresh_one(self):
        # Ревью 13.10.2026: зависший запрос (сеть, DNS) держал опрос компании — новое обновление не
        # начиналось, пока старое не кончится, и финалы всех операторов стояли. Дольше STUCK_SEC —
        # бросаем: новое идёт в свежем потоке, а не в очередь за зависшим; ответ зависшего, если он
        # всё же придёт, новые данные не затирает.
        self.assertEqual((live_calls.STUCK_SEC, live_calls.HTTP_TIMEOUT), (10.0, (3.05, 5.0)))
        clock = _Clock()
        calls = live_calls.CompanyCalls('remote_cc', clock=clock, wall_clock=clock.wall)   # свой поток
        release, started = threading.Event(), threading.Event()
        asked = []

        def fetch(wanted):
            asked.append(list(wanted))
            if len(asked) == 1:
                started.set()
                release.wait(5)
                return list(wanted), {'g1': _call('g1', 'CALLING')}, None
            return list(wanted), {'g1': _call('g1', 'ONLINE')}, None

        try:
            self.assertEqual(calls.lookup(['g1'], fetch, wait=0), {})
            self.assertTrue(started.wait(2))                                      # первый запрос «повис»
            clock.tick(5)
            self.assertEqual(calls.lookup(['g1'], fetch, wait=0.1), {})         # идёт — второго нет
            self.assertEqual(len(asked), 1)
            clock.tick(6)
            with self.assertLogs('dial_list.live_calls', level='WARNING') as logs:
                seen = calls.lookup(['g1'], fetch, wait=2)
            self.assertTrue(any('зависшим' in line for line in logs.output), logs.output)
            self.assertEqual(len(asked), 2)
            self.assertEqual(seen['g1'].call['disposition'], 'ONLINE')
        finally:
            release.set()
        deadline = time.monotonic() + 3
        while calls.requests < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(calls.requests, 2)
        self.assertEqual(calls.peek('g1').call['disposition'], 'ONLINE')       # старый ответ — мимо

    def test_late_answer_never_overwrites_newer_data(self):
        # Финал вебхука пришёл, пока шёл запрос опроса: ответ опроса (отправлен раньше) его не затирает.
        clock = _Clock()
        calls = live_calls.CompanyCalls('remote_cc', clock=clock, wall_clock=clock.wall)
        release, started = threading.Event(), threading.Event()

        def fetch(wanted):
            started.set()
            release.wait(5)
            return list(wanted), {'g1': _call('g1', 'ONLINE')}, None

        calls.lookup(['g1'], fetch, wait=0)
        self.assertTrue(started.wait(2))
        clock.tick(1)
        calls.put('g1', _call('g1', 'ANSWER', 61))
        release.set()
        deadline = time.monotonic() + 3
        while calls.requests < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual((calls.peek('g1').call['disposition'], calls.peek('g1').call['billsec']), ('ANSWER', 61))


class LiveVerdictTests(unittest.TestCase):
    def _seen(self, disposition, wall=T0, billsec=0):
        return live_calls.Seen(_call('g1', disposition, billsec), wall, 0.0)

    def test_no_data_is_unknown(self):
        verdict = dial_service.live_verdict
        self.assertEqual(verdict('leg_answered', None), ('unknown', None))
        self.assertEqual(verdict('leg_answered', live_calls.Seen(None, T0, 0.0)), ('unknown', None))

    def test_dialing_talking_and_non_answer_finals(self):
        verdict = dial_service.live_verdict
        for disposition in ('CALLING', 'RINGING', '', 'DIALING'):
            self.assertEqual(verdict('leg_answered', self._seen(disposition)), ('dialing', None), disposition)
        self.assertEqual(verdict('leg_answered', self._seen('online')), ('talking', None))
        # Не ответил, занято, прочее — финал сразу, в каком бы состоянии ни было плечо.
        for disposition in ('BUSY', 'NOANSWER', 'CANCEL', 'CONGESTION'):
            for state in ('requested', 'leg_answered', 'ended'):
                decision, call = verdict(state, self._seen(disposition))
                self.assertEqual((decision, call['disposition']), ('finish', disposition), (state, disposition))

    def test_answer_is_a_talk_until_the_leg_is_over_and_settled(self):
        verdict = dial_service.live_verdict
        # Плечо идёт — разговор, не финал.
        for state in ('requested', 'leg_ringing', 'leg_answered'):
            self.assertEqual(verdict(state, self._seen('ANSWER', billsec=12)), ('talking', None), state)
        ended_at = T0
        settle = timedelta(seconds=dial_service.ANSWER_SETTLE_SEC)
        # Плечо кончилось — финал только по данным не раньше чем через ANSWER_SETTLE_SEC после конца.
        self.assertEqual(verdict('ended', self._seen('ANSWER', wall=ended_at), leg_over_at=ended_at),
                         ('settling', None))
        self.assertEqual(verdict('ended', self._seen('ANSWER', wall=ended_at + settle - timedelta(milliseconds=1)),
                                 leg_over_at=ended_at), ('settling', None))
        decision, call = verdict('ended', self._seen('ANSWER', wall=ended_at + settle, billsec=40),
                                 leg_over_at=ended_at)
        self.assertEqual((decision, call['billsec']), ('finish', 40))
        # Конец плеча без момента (старые строки) — финал.
        self.assertEqual(verdict('ended', self._seen('ANSWER'))[0], 'finish')
        # Оператор начал следующий звонок — прежнее плечо точно кончилось (superseded_at).
        self.assertEqual(verdict('leg_answered', self._seen('ANSWER', wall=ended_at + settle),
                                 leg_over_at=ended_at)[0], 'finish')
        # Момент следующего звонка — время базы: к нему запас на расхождение часов (ревью 13.10.2026).
        over = dial_service.leg_over_moment
        margin = timedelta(seconds=dial_service.SUPERSEDED_SETTLE_MARGIN_SEC)
        self.assertEqual(dial_service.SUPERSEDED_SETTLE_MARGIN_SEC, 2.0)
        self.assertEqual(over('ended', ended_at, None), ended_at)
        self.assertEqual(over('ended', ended_at, ended_at - timedelta(seconds=9)), ended_at)
        self.assertIsNone(over('ended', None, None))
        self.assertEqual(over('leg_answered', None, ended_at), ended_at + margin)
        self.assertEqual(over('leg_answered', None, ended_at.replace(tzinfo=None)), ended_at + margin)
        self.assertIsNone(over('leg_answered', None, None))
        self.assertEqual(verdict('leg_answered', self._seen('ANSWER', wall=ended_at + settle + margin / 2),
                                 leg_over_at=over('leg_answered', None, ended_at), leg_over=True)[0], 'settling')
        self.assertEqual(verdict('leg_answered', self._seen('ANSWER', wall=ended_at + settle + margin),
                                 leg_over_at=over('leg_answered', None, ended_at), leg_over=True)[0], 'finish')
        # Без поясов (база отдала время без пояса) — тоже UTC.
        naive = (ended_at + settle).replace(tzinfo=None)
        self.assertEqual(verdict('ended', live_calls.Seen(_call('g1', 'ANSWER'), naive, 0.0),
                                 leg_over_at=ended_at.replace(tzinfo=None))[0], 'finish')


# ── сервис: один запрос на всех ─────────────────────────────────────────────

class _Cursor:
    """Курсор: ответ по первой подстроке SQL из rules; executed — что спрашивали."""

    def __init__(self, rules):
        self.rules, self.executed, self._answer = rules, [], {}

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._answer = next((a for sub, a in self.rules if sub in sql), {})

    def fetchone(self):
        value = self._answer.get('one')
        return value(self.executed[-1][1]) if callable(value) else value

    def fetchall(self):
        value = self._answer.get('all', [])
        return list(value(self.executed[-1][1]) if callable(value) else value)

    def sql_with(self, fragment):
        return [(sql, params) for sql, params in self.executed if fragment in sql]


class _Db:
    def __init__(self, cursor):
        self.cursor = cursor
        self.in_tx = False

    @contextlib.contextmanager
    def _get_cursor(self):
        self.in_tx = True
        try:
            yield self.cursor
        finally:
            self.in_tx = False


class _Binotel:
    def __init__(self, db, answer):
        self.db, self.answer, self.calls, self.in_tx, self.max_waits, self.timeouts = db, answer, [], [], [], []

    def call_details(self, ids, max_wait=None, timeout=None):
        self.calls.append(ids)
        self.in_tx.append(self.db.in_tx)
        self.max_waits.append(max_wait)
        self.timeouts.append(timeout)
        if isinstance(self.answer, Exception):
            raise self.answer
        wanted = ids if isinstance(ids, list) else [ids]
        return {g: dict(self.answer[g], general_call_id=g) for g in wanted if g in self.answer}


def _ctx(uid, company='remote_cc'):
    return {"user_id": uid, "department_id": 1954, "name": f"Оператор {uid}", "internal_number": str(900 + uid),
            "settings": {"binotel_company": company, "portion_size": 20, "_period": date(2026, 10, 1),
                         "retry_after_hours": 24, "max_attempts": 3}}


class ServiceLiveCallsTests(unittest.TestCase):
    """Карточки звонков десяти операторов, добор исходов и вопрос автосброса — одним опросом."""

    def _svc(self, live_rows, answer, attempts=None, submit=_sync):
        """live_rows — живые попытки компании (_LIVE_ATTEMPTS_SQL); attempts — строки attempt_live
        по id попытки."""
        attempts = attempts or {}
        cur = _Cursor([
            ('FROM dial_list_attempts t\n        JOIN dial_list_assignments a', {'all': live_rows}),
            ('phone_ended_at\n                FROM dial_list_attempts', {'one': lambda params: attempts.get(params[0])}),
        ])
        db = _Db(cur)
        svc = dial_service.DialListService(db=db, calls_submit=submit)
        client = _Binotel(db, answer)
        svc._client = lambda dep: client
        svc.operator_context = _ctx
        finished = []
        svc._finish_by_call = lambda aid, call, source: finished.append((aid, call['disposition'], source))
        return svc, cur, client, finished

    def test_ten_operators_one_request_per_window(self):
        gids = [f'g{i}' for i in range(10)]
        rows = [(f'a{i}', g, 'leg_answered', None, None) for i, g in enumerate(gids)]
        attempts = {f'a{i}': ('leg_answered', g, '', False, 0, '', None, None) for i, g in enumerate(gids)}
        answer = {g: {"disposition": "ONLINE" if i % 2 else "CALLING"} for i, g in enumerate(gids)}
        svc, cur, client, finished = self._svc(rows, answer, attempts)
        cards = [svc.attempt_live(i, f'a{i}') for i in range(10)]
        # Десять карточек — один запрос call-details сразу на все десять звонков компании.
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(sorted(client.calls[0]), sorted(gids))
        self.assertEqual(client.calls[0][0], 'g0')                                 # кому нужно — первым
        self.assertEqual(client.max_waits, [0])                                     # фоновый опрос не ждёт очереди
        self.assertEqual(client.in_tx, [False])                                     # сеть — вне транзакций
        self.assertEqual([c['talking'] for c in cards], [bool(i % 2) for i in range(10)])
        self.assertEqual(finished, [])
        # Живые попытки — одним запросом по компании отдела, без номеров.
        (sql, params), = cur.sql_with('JOIN dial_list_assignments a ON a.id = t.assignment_id\n        JOIN')
        self.assertIn("COALESCE(NULLIF(TRIM(s.binotel_company), ''), %s) = %s", sql)
        self.assertEqual(params, (dial_service.LIVE_STATES, dial_service.ATTEMPT_STALE_MINUTES,
                                  dial_service.DEFAULT_BINOTEL_COMPANY, 'remote_cc', live_calls.BATCH_MAX))
        self.assertNotIn('phone_norm', sql)

    def test_companies_poll_separately(self):
        rows = [('a1', 'g1', 'leg_answered', None, None)]
        attempts = {'a1': ('leg_answered', 'g1', '', False, 0, '', None, None),
                    'a2': ('leg_answered', 'g2', '', False, 0, '', None, None)}
        svc, cur, client, finished = self._svc(rows, {'g1': {"disposition": "CALLING"},
                                                      'g2': {"disposition": "CALLING"}}, attempts)
        svc.operator_context = lambda uid: _ctx(uid, company='remote_cc' if uid == 1 else 'tez')
        svc.attempt_live(1, 'a1')
        svc.attempt_live(2, 'a2')
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(set(svc._calls), {'remote_cc', 'tez'})

    def test_finals_of_every_live_attempt_close_in_the_background(self):
        # Опрос, который запросил один оператор, закрывает финалы всех: «не ответил» — сразу,
        # ответ водителя — только у кончившегося плеча и по данным после его конца; пока плечо
        # идёт — это разговор.
        now = datetime.now(timezone.utc)
        long_ago = now - timedelta(seconds=30)
        rows = [('a1', 'g1', 'leg_answered', None, None),            # набор
                ('a2', 'g2', 'ended', long_ago, None),               # не ответил
                ('a3', 'g3', 'leg_answered', None, None),            # говорит (ANSWER посреди разговора)
                ('a4', 'g4', 'ended', long_ago, None),               # разговор кончился 30 с назад
                ('a5', 'g5', 'ended', now + timedelta(seconds=5), None),   # кончился «только что»
                ('a6', 'g6', 'leg_answered', None, long_ago)]        # оператор уже звонит следующему
        answer = {'g1': {"disposition": "CALLING"}, 'g2': {"disposition": "NOANSWER"},
                  'g3': {"disposition": "ANSWER", "billsec": 7}, 'g4': {"disposition": "ANSWER", "billsec": 61},
                  'g5': {"disposition": "ANSWER", "billsec": 9}, 'g6': {"disposition": "ANSWER", "billsec": 15}}
        svc, cur, client, finished = self._svc(rows, answer,
                                               {'a1': ('leg_answered', 'g1', '', False, 0, '', None, None)})
        svc.attempt_live(1, 'a1')
        self.assertEqual(sorted(finished), [('a2', 'NOANSWER', 'poll'), ('a4', 'ANSWER', 'poll'),
                                            ('a6', 'ANSWER', 'poll')])

    def test_settle_and_reconcile_use_the_superseded_margin(self):
        # Оператор начал следующий звонок только что (время базы): ответ водителя по прежнему звонку
        # закрывается данными не раньше, чем через запас после этого момента (ревью 13.10.2026).
        now = datetime.now(timezone.utc)
        rows = [('a1', 'g1', 'leg_answered', None, now),                         # следующий — только что
                ('a2', 'g2', 'leg_answered', None, now - timedelta(seconds=30))]  # давно
        answer = {'g1': {"disposition": "ANSWER", "billsec": 15}, 'g2': {"disposition": "ANSWER", "billsec": 20}}
        svc, cur, client, finished = self._svc(rows, answer,
                                               {'a1': ('leg_answered', 'g1', '', False, 0, '', None, None)})
        svc.attempt_live(1, 'a1')
        self.assertEqual(finished, [('a2', 'ANSWER', 'poll')])
        # Свой короткий таймаут HTTP у запроса опроса.
        self.assertEqual(client.timeouts, [live_calls.HTTP_TIMEOUT])

    def test_answer_on_a_live_card_is_talking_not_final(self):
        rows = [('a1', 'g1', 'leg_answered', None, None)]
        svc, cur, client, finished = self._svc(rows, {'g1': {"disposition": "ANSWER", "billsec": 3}},
                                               {'a1': ('leg_answered', 'g1', '', False, 0, '', None, None)})
        card = svc.attempt_live(1, 'a1')
        self.assertEqual((card['talking'], card['final'], card['state'], card['disposition'], card['result']),
                         (True, False, 'leg_answered', 'ANSWER', ''))
        self.assertEqual(finished, [])
        # Финал «занято» на карточке закрывает попытку здесь же.
        svc, cur, client, finished = self._svc(rows, {'g1': {"disposition": "BUSY"}},
                                               {'a1': ('leg_answered', 'g1', '', False, 0, '', None, None)})
        card = svc.attempt_live(1, 'a1')
        self.assertEqual((card['final'], card['result'], card['state']), (True, 'busy', 'finished'))
        self.assertIn(('a1', 'BUSY', 'poll'), finished)

    def test_no_connection_means_cache_only(self):
        rows = [('a1', 'g1', 'leg_answered', None, None)]
        svc, cur, client, finished = self._svc(rows, {}, {'a1': ('leg_answered', 'g1', '', False, 0, '', None, None)})

        def not_configured(dep):
            raise dial_service.DialListError("Подключение к Binotel для отдела не настроено на сервере", 503)

        svc._client = not_configured
        with self.assertLogs('dial_list.service', level='INFO'):
            card = svc.attempt_live(1, 'a1')
        self.assertEqual((card['talking'], card['final'], card['disposition']), (False, False, ''))


class ReconcileTests(unittest.TestCase):
    """Добор исходов на GET состояния: из общего кэша; кончившееся плечо (ended) — без
    минимального возраста; ответ водителя — данными после конца плеча; старые без исхода —
    «неизвестно», а с известным ответом — им."""

    def _svc(self, pending, answer):
        cur = _Cursor([
            ('FROM dial_list_attempts t\n        JOIN dial_list_assignments a', {'all': []}),
            ('t.state = \'ended\' OR t.requested_at', {'all': pending}),
        ])
        db = _Db(cur)
        svc = dial_service.DialListService(db=db, calls_submit=_sync)
        client = _Binotel(db, answer)
        svc._client = lambda dep: client
        closed = []
        svc._finish_by_call = lambda aid, call, source: closed.append((aid, call['disposition']))
        svc._finish_attempt = lambda aid, disposition, billsec, waitsec, source: closed.append((aid, disposition, source))
        return svc, cur, client, closed

    def test_nothing_pending_is_one_select(self):
        svc, cur, client, closed = self._svc([], {})
        self.assertEqual(svc.reconcile(_ctx(7)), 0)
        self.assertEqual((len(cur.executed), client.calls), (1, []))
        sql, params = cur.executed[0]
        self.assertIn("AND (t.state = 'ended' OR t.requested_at < CURRENT_TIMESTAMP - make_interval(secs => %s))",
                      ' '.join(sql.split()))
        self.assertEqual(params, (7, dial_service.LIVE_STATES, dial_service.POLL_MIN_AGE_SEC,
                                  dial_service.POLL_MAX_PER_REQUEST))

    def test_ended_answer_waits_for_data_after_the_leg_end(self):
        now = datetime.now(timezone.utc)
        ended = now - timedelta(seconds=5)
        pending = [('a1', 'g1', now - timedelta(seconds=40), 'ended', ended, None)]
        svc, cur, client, closed = self._svc(pending, {'g1': {"disposition": "ANSWER", "billsec": 30}})
        calls = svc._company_calls('remote_cc')
        # В кэше — ANSWER, полученный ДО конца плеча (длительность неполная): нужен новый опрос.
        calls.put('g1', {"general_call_id": 'g1', "disposition": "ANSWER", "billsec": 12})
        calls._entries['g1'] = calls.peek('g1')._replace(wall=ended - timedelta(seconds=2))
        self.assertEqual(svc.reconcile(_ctx(7)), 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(closed, [('a1', 'ANSWER')])
        self.assertEqual(calls.peek('g1').call['billsec'], 30)

    def test_stale_attempts(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=dial_service.ATTEMPT_STALE_MINUTES + 5)
        pending = [('a1', 'g1', old, 'leg_answered', None, None),     # час без конца плеча, но водитель отвечал
                   ('a2', 'g2', old, 'leg_answered', None, None),     # час без исхода
                   ('a3', None, old, 'requested', None, None)]        # АТС так и не дала id
        svc, cur, client, closed = self._svc(pending, {'g1': {"disposition": "ANSWER", "billsec": 50}})
        self.assertEqual(svc.reconcile(_ctx(7)), 3)
        self.assertEqual(closed, [('a1', 'ANSWER'), ('a2', 'UNKNOWN', 'timeout'), ('a3', 'UNKNOWN', 'timeout')])


class WebhookTests(unittest.TestCase):
    """«API Call Completed» — как его шлёт Binotel: форма callDetails[…] или JSON."""

    FORM = {
        'requestType': 'apiCallCompleted',
        'callDetails[companyID]': '1234',
        'callDetails[generalCallID]': '7890123',
        'callDetails[callType]': '1',
        'callDetails[internalNumber]': '913',
        'callDetails[externalNumber]': '77011234567',
        'callDetails[waitsec]': '12',
        'callDetails[billsec]': '47',
        'callDetails[disposition]': 'ANSWER',
        'callDetails[employeeData][name]': 'Оператор',
        'callDetails[historyData][0][disposition]': 'BUSY',
        'callDetails[historyData][0][billsec]': '0',
    }

    def test_fields_from_form_json_and_flat(self):
        fields, value = dial_service.webhook_fields, dial_service.webhook_value
        form = fields(self.FORM)
        self.assertEqual((value(form, 'generalCallID'), value(form, 'disposition'), value(form, 'billsec'),
                          value(form, 'waitsec')), ('7890123', 'ANSWER', '47', '12'))
        nested = fields({"requestType": "apiCallCompleted",
                         "callDetails": {"generalCallID": 7890123, "disposition": "NOANSWER", "billsec": 0,
                                         "waitsec": 31, "historyData": [{"disposition": "ANSWER", "billsec": 9}]}})
        self.assertEqual(nested['callDetails[historyData][0][disposition]'], 'ANSWER')
        self.assertEqual((value(nested, 'generalCallID'), value(nested, 'disposition'), value(nested, 'billsec'),
                          value(nested, 'waitsec')), ('7890123', 'NOANSWER', '0', '31'))
        flat = fields({"generalCallId": "55", "disposition": "BUSY"})
        self.assertEqual((value(flat, 'generalCallID'), value(flat, 'disposition')), ('55', 'BUSY'))
        # Плечи из historyData за звонок не принимаем.
        self.assertIsNone(value(fields({'callDetails[historyData][0][disposition]': 'ANSWER'}), 'disposition'))
        self.assertEqual(fields(None), {})
        self.assertEqual(fields(['не словарь']), {})

    def _svc(self, row):
        cur = _Cursor([('WHERE t.general_call_id = %s', {'one': row})])
        svc = dial_service.DialListService(db=_Db(cur), calls_submit=_sync)
        finished = []
        svc._finish_attempt = lambda aid, d, b, w, source: finished.append((aid, d, b, w, source))
        return svc, cur, finished

    def test_form_from_binotel_finishes_the_attempt_and_feeds_the_cache(self):
        from werkzeug.datastructures import ImmutableMultiDict
        svc, cur, finished = self._svc(('att-1', 'ended', 'remote_cc'))
        with self.assertLogs('dial_list.service', level='INFO') as logs:
            res = svc.handle_webhook(ImmutableMultiDict(self.FORM), '194.88.218.116')
        self.assertEqual(res, {"matched": True, "general_call_id": '7890123', "disposition": 'ANSWER'})
        self.assertEqual(finished, [('att-1', 'ANSWER', 47, 12, 'webhook')])
        seen = svc._company_calls('remote_cc').peek('7890123')
        self.assertEqual((seen.call['disposition'], seen.call['billsec']), ('ANSWER', 47))
        # В журнал сервера — без номера водителя.
        self.assertFalse(any('77011234567' in line for line in logs.output), logs.output)
        self.assertTrue(any('7890123' in line and 'ANSWER' in line for line in logs.output), logs.output)
        # Сырой POST — в журнал вебхука (сервер), с компанией попытки в запросе.
        (sql, params), = cur.sql_with('WHERE t.general_call_id = %s')
        self.assertIn('binotel_company', sql)
        self.assertEqual(params, (dial_service.DEFAULT_BINOTEL_COMPANY, '7890123'))
        self.assertEqual(len(cur.sql_with('INSERT INTO dial_list_webhook_log')), 1)

    def test_json_from_binotel(self):
        svc, cur, finished = self._svc(('att-2', 'leg_answered', 'tez'))
        body = {"requestType": "apiCallCompleted",
                "callDetails": {"generalCallID": "42", "disposition": "BUSY", "billsec": "0", "waitsec": "18"}}
        self.assertTrue(svc.handle_webhook(body, '194.88.218.116')['matched'])
        self.assertEqual(finished, [('att-2', 'BUSY', 0, 18, 'webhook')])
        self.assertEqual(svc._company_calls('tez').peek('42').call['disposition'], 'BUSY')

    def test_unknown_call_or_no_final(self):
        svc, cur, finished = self._svc(None)
        self.assertEqual(svc.handle_webhook(self.FORM, '1.2.3.4')['matched'], False)
        svc, cur, finished = self._svc(('att-3', 'ended', 'remote_cc'))
        res = svc.handle_webhook({'callDetails[generalCallID]': '9', 'callDetails[disposition]': 'ONLINE'})
        self.assertEqual((res['matched'], finished), (True, []))

    def _client(self, svc):
        import flask
        bp = dial_routes.build_dial_list_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None), is_admin_role=lambda role: True,
            headed_department_ids=lambda rid: [], service=svc)
        app = flask.Flask('dial_list_webhook_test')
        app.register_blueprint(bp)
        return app.test_client()

    def test_route_logs_rejections_with_address_never_token(self):
        svc, cur, finished = self._svc(('att-1', 'ended', 'remote_cc'))
        client = self._client(svc)
        token = 'w' * 24
        url = '/api/dial_list/webhook/binotel'
        cases = (
            ({}, '194.88.218.116', '', 'не задан'),
            ({dial_service.WEBHOOK_TOKEN_ENV: token}, '194.88.218.116', '', 'нет token'),
            ({dial_service.WEBHOOK_TOKEN_ENV: token}, '194.88.218.116', 'x' * 24, 'token не совпал'),
            ({dial_service.WEBHOOK_TOKEN_ENV: token}, '203.0.113.7', token, 'адрес 203.0.113.7'),
        )
        for env, addr, presented, reason in cases:
            with unittest.mock.patch.dict(os.environ, dict(env, **{dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '1'})):
                if not env:
                    os.environ.pop(dial_service.WEBHOOK_TOKEN_ENV, None)
                with self.assertLogs('dial_list.routes', level='WARNING') as logs:
                    # Прокси Render дописывает адрес отправителя справа; левее — что прислал клиент.
                    resp = client.post(f'{url}?token={presented}', data=self.FORM,
                                       headers={'X-Forwarded-For': f'10.0.0.1, {addr}'})
            self.assertEqual(resp.status_code, 403, reason)
            line = '\n'.join(logs.output)
            self.assertIn(reason, line)
            self.assertIn(addr, line)                                              # точный адрес
            self.assertIn('10.0.0.1', line)                                        # вся цепочка прокси
            self.assertNotIn(token, line)                                          # токен — никогда
            if presented:
                self.assertNotIn(presented, line)
        self.assertEqual(finished, [])
        # Правильный токен с адреса Binotel — принят, форма разобрана.
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_TOKEN_ENV: token,
                                                   dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '1'}):
            resp = client.post(f'{url}?token={token}', data=self.FORM,
                               headers={'X-Forwarded-For': '194.88.218.116'})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()['status'], 'success')
            resp = client.post(f'{url}?token={token}', json={"callDetails": {"generalCallID": "7890123",
                                                                             "disposition": "ANSWER",
                                                                             "billsec": 50, "waitsec": 12}},
                               headers={'X-Forwarded-For': '194.88.218.116'})
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(finished, [('att-1', 'ANSWER', 47, 12, 'webhook'), ('att-1', 'ANSWER', 50, 12, 'webhook')])

    def test_allowlist_trusts_only_the_hop_the_proxy_appended(self):
        # Ревью 13.10.2026: левый край X-Forwarded-For пишет сам клиент. С токеном из утёкшей
        # ссылки и чужим адресом «X-Forwarded-For: <адрес Binotel>» прокси Render допишет справа
        # настоящий адрес — по нему и отказ; в журнал — вся цепочка.
        svc, cur, finished = self._svc(('att-1', 'ended', 'remote_cc'))
        client = self._client(svc)
        token = 'w' * 24
        url = '/api/dial_list/webhook/binotel'
        env = {dial_service.WEBHOOK_TOKEN_ENV: token, dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '1'}
        with unittest.mock.patch.dict(os.environ, env):
            os.environ.pop(dial_service.WEBHOOK_PROXY_HOPS_ENV, None)
            with self.assertLogs('dial_list.routes', level='WARNING') as logs:
                resp = client.post(f'{url}?token={token}', data=self.FORM,
                                   headers={'X-Forwarded-For': '194.88.218.116, 203.0.113.7'})
            self.assertEqual(resp.status_code, 403)
            line = '\n'.join(logs.output)
            self.assertIn('адрес 203.0.113.7 не из списка', line)
            self.assertIn('194.88.218.116, 203.0.113.7', line)                     # цепочка целиком
            # Без заголовка — адрес соединения (у тестового клиента 127.0.0.1).
            with self.assertLogs('dial_list.routes', level='WARNING'):
                self.assertEqual(client.post(f'{url}?token={token}', data=self.FORM).status_code, 403)
            # Два доверенных прокси (переменная) — второй справа.
            with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_PROXY_HOPS_ENV: '2'}):
                resp = client.post(f'{url}?token={token}', data=self.FORM,
                                   headers={'X-Forwarded-For': '203.0.113.7, 194.88.218.116, 10.1.2.3'})
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(finished, [('att-1', 'ANSWER', 47, 12, 'webhook')])

    def test_remote_addr_rule(self):
        addr = dial_service.webhook_remote_addr
        with unittest.mock.patch.dict(os.environ, {}):
            os.environ.pop(dial_service.WEBHOOK_PROXY_HOPS_ENV, None)
            self.assertEqual(dial_service.webhook_proxy_hops(), 1)
            self.assertEqual(addr('1.1.1.1, 2.2.2.2 , 3.3.3.3', '10.0.0.9'), '3.3.3.3')
            self.assertEqual(addr('3.3.3.3', '10.0.0.9'), '3.3.3.3')
            self.assertEqual(addr('', '10.0.0.9'), '10.0.0.9')
            self.assertEqual(addr(None, None), '')
            self.assertEqual(addr(' , ,', '10.0.0.9'), '10.0.0.9')
        for raw, hops in (('2', 2), ('0', 0), ('5', 5), ('6', 1), ('-1', 1), ('два', 1), ('', 1)):
            with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_PROXY_HOPS_ENV: raw}):
                self.assertEqual(dial_service.webhook_proxy_hops(), hops, raw)
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_PROXY_HOPS_ENV: '2'}):
            self.assertEqual(addr('1.1.1.1, 2.2.2.2, 3.3.3.3', '10.0.0.9'), '2.2.2.2')
            self.assertEqual(addr('3.3.3.3', '10.0.0.9'), '10.0.0.9')            # цепочка короче прокси
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_PROXY_HOPS_ENV: '0'}):
            self.assertEqual(addr('1.1.1.1, 2.2.2.2', '10.0.0.9'), '10.0.0.9')   # заголовку не верим

    def test_verdict_reasons(self):
        svc = dial_service.DialListService(db=None)
        token = 'v' * 20
        with unittest.mock.patch.dict(os.environ, {dial_service.WEBHOOK_TOKEN_ENV: token,
                                                   dial_service.WEBHOOK_REQUIRE_BINOTEL_IP_ENV: '0'}):
            self.assertEqual(svc.webhook_verdict(token, '8.8.8.8'), (True, ''))
            ok, reason = svc.webhook_verdict('чужой' * 5, '8.8.8.8')
            self.assertEqual((ok, reason), (False, 'token не совпал'))
            self.assertNotIn(token, reason)


class CallErrorTextTests(unittest.TestCase):
    """«Позвонить»: текст ошибки Binotel (бывает, с номером водителя) телефону не уходит —
    только код; в api_error попытки — без длинных чисел; полностью — в журнале сервера."""

    PHONE = '77019876543'

    def _svc(self, error):
        cur = _Cursor([
            ('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 0, self.PHONE, 'Иванов', 'lead-1', False)}),
            ('FROM dial_list_outcomes o', {'all': [('o-1', 'Отказ', '#FF3B30', 1, False, True, 0, [])]}),
            ('INSERT INTO dial_list_attempts', {'one': ('att-1', None)}),
        ])
        db = _Db(cur)

        class _Checker:
            def configured(self):
                return False

        svc = dial_service.DialListService(db=db, sign_checker=_Checker())
        svc.operator_context = lambda uid: dict(_ctx(uid), settings=dict(_ctx(uid)['settings'],
                                                                         caller_id_for_employee=''))

        class _Client:
            def originate_internal_to_external(self, internal, external, **extra):
                raise error

        svc._client = lambda dep: _Client()
        return svc, cur

    def _call(self, error):
        svc, cur = self._svc(error)
        with self.assertLogs('dial_list.service', level='WARNING') as logs, \
                self.assertRaises(dial_service.DialListError) as ctx:
            svc.start_call(7, 'asg-1')
        (sql, params), = cur.sql_with("SET state = 'failed', api_error = %s")
        return str(ctx.exception), ctx.exception.status, params[0], '\n'.join(logs.output)

    def test_code_only_for_the_operator(self):
        error = RuntimeError(f"Binotel calls/internal-number-to-external-number status='error' code=104: "
                             f"Wrong externalNumber {self.PHONE} for this company")
        text, status, api_error, log_text = self._call(error)
        self.assertEqual((text, status), ("АТС не приняла звонок (код 104) — обратитесь к руководителю", 502))
        self.assertNotIn(self.PHONE, api_error)
        self.assertIn('code=104', api_error)
        self.assertIn(self.PHONE, log_text)                                       # полностью — только в журнале

    def test_no_code_and_too_frequent(self):
        text, status, api_error, _ = self._call(RuntimeError(f"Binotel HTTP 502: <html>{self.PHONE}</html>"))
        self.assertEqual((text, status), ("АТС не приняла звонок — обратитесь к руководителю", 502))
        self.assertNotIn(self.PHONE, api_error)
        text, status, _, _ = self._call(binotel.RateLimited(
            f"Binotel calls/internal-number-to-external-number status='error' code=106: Requests are too frequent "
            f"for {self.PHONE}", retry_after=3))
        self.assertEqual(status, 429)
        self.assertEqual(text, "АТС не приняла звонок (код 106): слишком частые запросы — повторите через "
                               "несколько секунд")
        self.assertNotIn(self.PHONE, text)

    def test_scrub_digits(self):
        self.assertEqual(dial_service.scrub_digits('номер 77011234567, ИИН 900101300007, код 104, линия 913'),
                         'номер …, ИИН …, код 104, линия 913')
        self.assertEqual(dial_service.binotel_error_code('code=150: Can\'t call'), 150)
        self.assertIsNone(dial_service.binotel_error_code('Binotel HTTP 502'))


if __name__ == '__main__':
    unittest.main()
