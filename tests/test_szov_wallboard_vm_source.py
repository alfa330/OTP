# -*- coding: utf-8 -*-
"""«Табло СЗоВ · Линия»: снимок от коннектора Oktell на VM шлюза вместо моста Oktell.

Стенд — тот же, что у tests/test_szov_wallboard.py: функции снимка вырезаны из монолита,
мост Oktell и база подменены. Коннектор «присылает» строки прямо в память портала
(oktell_live.store), как это делает ручка приёма.

Что закреплено:
  * свежий снимок VM — источник табло, и к мосту Oktell портал при этом не ходит вовсе;
  * снимок устарел, рубильник выключен или порог SL другой — табло идёт к мосту, как раньше;
  * новый снимок VM показывается сразу, не дожидаясь TTL готового снимка; между снимками
    зрители получают готовый, а не пересборку на каждый запрос;
  * VM вернулась — пауза после отказа моста этому не мешает;
  * коннектор молчит и мост не отвечает — причина про коннектор попадает в текст ошибки.
"""
import os
import time
import unittest
from unittest import mock

from oktell_live import store as live_store
from tests.test_szov_wallboard import _SnapshotHarness

VM_TOTALS = {
    'oktell_now': '2026-11-10 22:00:00', 'queue_now': 7, 'queue_max_wait_seconds': 95,
    'talking_now': 9, 'arrived': 1000, 'served': 950, 'lost': 50, 'greet_drop': 40,
    'served_sl': 800, 'wait_seconds': 20000.0, 'wait_served_seconds': 19000.0,
    'max_wait_seconds': 300.0, 'talk_seconds': 190000.0,
}


def vm_rows(totals=None, operators=2):
    base = dict(totals or VM_TOTALS)
    rows = [dict(base, operator_name='Оператор %d' % index, state=1, icode=-1,
                 since='2026-11-10 21:50:00', in_state_seconds=60 * index)
            for index in range(1, operators + 1)]
    return rows or [dict(base, operator_name=None, state=None, icode=None, since=None,
                         in_state_seconds=None)]


class VmSourceTests(_SnapshotHarness, unittest.TestCase):

    def setUp(self):
        live_store.store.reset()
        self.addCleanup(live_store.store.reset)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (live_store.ENABLED_ENV, live_store.MAX_AGE_ENV):
            os.environ.pop(name, None)

    def push(self, totals=None, operators=2, sl_seconds=20, age=0.0):
        """Коннектор прислал снимок `age` секунд назад."""
        live_store.store.put_wallboard(live_store.clean_rows(vm_rows(totals, operators)),
                                       {'sl_seconds': sl_seconds, 'test_keys': []},
                                       taken_at=time.time() - age, query_ms=180)
        if age:
            live_store.store._wallboard['received_at'] -= age

    def counting(self, ns):
        """Сколько раз снимок табло собирался заново."""
        builds = {'n': 0}
        original = ns['_szov_wallboard_fetch_snapshot']

        def counted():
            builds['n'] += 1
            return original()

        ns['_szov_wallboard_fetch_snapshot'] = counted
        return builds

    # ── источник ─────────────────────────────────────────────────────────────

    def test_fresh_vm_snapshot_feeds_the_wallboard_without_the_bridge(self):
        ns = self._namespace()
        self.push()
        snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'vm')
        self.assertFalse(snap['stale'])
        self.assertEqual(ns['_query_state']['calls'], 0, 'к мосту Oktell портал не ходит')
        self.assertEqual(snap['today']['served'], 950)
        self.assertEqual(snap['today']['total'], 1000 + 40)
        self.assertAlmostEqual(snap['today']['ar_ratio'], 50 / 1000)
        self.assertEqual(snap['today']['asa_seconds'], 20)
        self.assertEqual(snap['now']['queue'], 7)
        self.assertEqual(snap['now']['queue_max_wait_seconds'], 95)
        self.assertEqual(snap['oktell_now'], '2026-11-10 22:00:00')

    def test_bridge_is_the_source_when_the_connector_never_came(self):
        ns = self._namespace()
        snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'bridge')
        self.assertEqual(ns['_query_state']['calls'], 1)
        self.assertEqual(snap['today']['served'], 392)

    def test_stale_vm_snapshot_falls_back_to_the_bridge(self):
        ns = self._namespace()
        self.push(age=live_store.DEFAULT_MAX_AGE_SECONDS + 5)
        snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'bridge')
        self.assertEqual(snap['today']['served'], 392, 'цифры моста, а не устаревшего снимка VM')
        self.assertEqual(ns['_query_state']['calls'], 1)

    def test_kill_switch_falls_back_to_the_bridge(self):
        ns = self._namespace()
        self.push()
        with mock.patch.dict(os.environ, {live_store.ENABLED_ENV: '0'}):
            snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'bridge')
        self.assertEqual(ns['_query_state']['calls'], 1)

    def test_snapshot_taken_with_another_sl_threshold_is_not_used(self):
        ns = self._namespace()
        self.push(sl_seconds=30)
        snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'bridge')
        self.assertEqual(snap['sl_threshold_seconds'], 20)

    def test_night_snapshot_without_operators(self):
        ns = self._namespace()
        self.push(operators=0)
        snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'vm')
        self.assertEqual(snap['now']['operators_online'], 0)
        self.assertEqual(snap['today']['served'], 950)

    # ── свежесть ─────────────────────────────────────────────────────────────

    def test_new_vm_snapshot_is_shown_at_once_not_after_the_ttl(self):
        ns = self._namespace()
        self.push()
        first = ns['_szov_wallboard_snapshot']()
        self.assertEqual(first['now']['queue'], 7)
        time.sleep(0.02)
        self.push(totals=dict(VM_TOTALS, queue_now=11, served=951))
        second = ns['_szov_wallboard_snapshot']()
        self.assertEqual(second['now']['queue'], 11, 'TTL готового снимка не задерживает новый')
        self.assertEqual(second['today']['served'], 951)
        self.assertEqual(ns['_query_state']['calls'], 0)

    def test_viewers_between_two_vm_snapshots_share_one_build(self):
        ns = self._namespace()
        builds = self.counting(ns)
        self.push()
        for _ in range(6):
            self.assertEqual(ns['_szov_wallboard_snapshot']()['source'], 'vm')
        self.assertEqual(builds['n'], 1, 'снимок VM тот же — пересобирать табло незачем')
        time.sleep(0.02)
        self.push()
        for _ in range(6):
            ns['_szov_wallboard_snapshot']()
        self.assertEqual(builds['n'], 2)

    def test_vm_snapshot_older_than_the_ready_one_does_not_rebuild(self):
        """«Новее готового» — по моменту приёма: уже использованный снимок поводом не служит."""
        ns = self._namespace()
        builds = self.counting(ns)
        self.push()
        ns['_szov_wallboard_snapshot']()
        self.assertFalse(live_store.wallboard_newer_than(ns['_szov_wallboard_cache']['ts']))
        ns['_szov_wallboard_snapshot']()
        self.assertEqual(builds['n'], 1)

    def test_connector_silence_ends_in_the_bridge_after_the_ttl(self):
        ns = self._namespace()
        self.push()
        self.assertEqual(ns['_szov_wallboard_snapshot']()['source'], 'vm')
        # Прошла минута: снимок VM устарел, готовый снимок табло просрочен.
        live_store.store._wallboard['received_at'] -= 60
        ns['_szov_wallboard_cache']['ts'] = time.time() - 60
        snap = ns['_szov_wallboard_snapshot']()
        self.assertEqual(snap['source'], 'bridge')
        self.assertFalse(snap['stale'])

    # ── отказы ───────────────────────────────────────────────────────────────

    def test_vm_return_is_not_delayed_by_the_bridge_failure_backoff(self):
        """Мост лежит, табло отдаёт старый снимок и держит паузу. Коннектор прислал снимок —
        пауза берегла мост от запросов, а здесь запроса к нему нет."""
        ns = self._namespace()
        ns['_szov_wallboard_snapshot']()                       # снимок моста в кэше

        def broken(sql, timeout=None):
            raise RuntimeError('Oktell proxy connect timeout')

        ns['_oktell_query'] = broken
        ns['_szov_wallboard_cache']['ts'] = time.time() - 60
        stale = ns['_szov_wallboard_snapshot']()
        self.assertTrue(stale['stale'])
        self.assertTrue(ns['_szov_wallboard_snapshot']()['stale'], 'идёт пауза после отказа')

        self.push()
        recovered = ns['_szov_wallboard_snapshot']()
        self.assertFalse(recovered['stale'])
        self.assertEqual(recovered['source'], 'vm')
        self.assertEqual(recovered['today']['served'], 950)
        self.assertEqual(ns['_szov_wallboard_cache']['failed_at'], 0.0)

    def test_bridge_error_names_the_silent_connector(self):
        ns = self._namespace(fail=True)
        live_store.store.agent_seen(hostname='otp-gw', version='oktell/1.0.0', key_id='k',
                                    db={'host': 'h', 'connected': False, 'error': 'Login timeout expired'})
        with self.assertRaises(RuntimeError) as caught:
            ns['_szov_wallboard_snapshot']()
        message = str(caught.exception)
        self.assertIn('коннектор на VM шлюза не читает базу Oktell', message)
        self.assertIn('Login timeout expired', message)
        self.assertIn('мост Oktell', message)
        self.assertIn('500', message, 'причина отказа моста тоже остаётся в тексте')

    def test_bridge_error_is_untouched_when_there_is_no_connector(self):
        ns = self._namespace(fail=True)
        with self.assertRaises(RuntimeError) as caught:
            ns['_szov_wallboard_snapshot']()
        self.assertEqual(str(caught.exception), 'Oktell proxy HTTP 500')

    def test_stale_notice_carries_the_connector_reason(self):
        ns = self._namespace()
        self.push()
        ns['_szov_wallboard_snapshot']()
        live_store.store.agent_seen(hostname='otp-gw', version='oktell/1.0.0', key_id='k',
                                    db={'host': 'h', 'connected': True})

        def broken(sql, timeout=None):
            raise RuntimeError('Oktell proxy HTTP 500')

        ns['_oktell_query'] = broken
        live_store.store._wallboard['received_at'] -= 100
        live_store.store._agent['seen_at'] -= 100
        ns['_szov_wallboard_cache']['ts'] = time.time() - 100
        stale = ns['_szov_wallboard_snapshot']()
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['today']['served'], 950, 'на экране — последний снимок VM')
        self.assertIn('молчит', stale['error'])


class RouteGuardTests(unittest.TestCase):
    """Ручка снимка не отказывает из-за ненастроенного моста, если снимок присылает VM."""

    def test_route_checks_the_connector_before_refusing(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'szov_wallboard' / 'routes.py').read_text(
            encoding='utf-8-sig')
        start = source.index('def api_szov_wallboard_snapshot():')
        body = source[start:source.index('@bp.route', start)]
        self.assertIn('if not _oktell_api_ready() and not oktell_live_store.agent_known():', body)
        self.assertIn('from oktell_live import store as oktell_live_store', body)


if __name__ == '__main__':
    unittest.main()
