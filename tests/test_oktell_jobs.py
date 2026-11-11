# -*- coding: utf-8 -*-
"""Запросы портала к базе Oktell через коннектор на VM шлюза: очередь заданий портала
(oktell_live/jobs.py), ручка `/api/oktell_live/agent/jobs`, исполнитель коннектора
(cdr_bridge/oktell_jobs.py) и общий вход портала `_oktell_query`.

Портал здесь настоящий: поддельная сессия исполнителя отдаёт его запросы ручке во Flask —
подпись проверяется, задание уходит, результат возвращается ждущему потоку. База — двойник.

Что закреплено:
  * на шлюз уходят имя набора, параметры и отпечаток — НЕ текст запроса; исполнитель строит
    текст сам и исполняет только его, что бы ни прислал портал;
  * каталоги портала и шлюза разошлись, набора на шлюзе нет, учётке не дана таблица, базы нет —
    шлюз за запрос не берётся, и портал отдаёт его мосту Oktell;
  * запрос исполнялся и не удался (ошибка базы, таймаут) — мосту он НЕ повторяется;
  * исполнителя нет на связи — портал не ждёт и идёт к мосту сразу;
  * обычная строка SQL без набора каталога на шлюз не уходит никогда.
"""
import json
import logging
import os
import sys
import threading
import time
import unittest
from http.client import RemoteDisconnected
from unittest import mock

import requests
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import oktell_datasets  # noqa: E402
from cdr_bridge import oktell_db, oktell_jobs, signing  # noqa: E402
from oktell_live import jobs as live_jobs, routes as live_routes, store as live_store  # noqa: E402
from tests import source_cache  # noqa: E402
from tests.test_oktell_http_client import _FakeSession, _load_names  # noqa: E402

PRIVATE_B64, PUBLIC_B64, KEY_ID = signing.generate()
ROWS = [{'OperatorName': 'Оператор Один', 'CallDate': '2026-10-08', 'CallCount': 41},
        {'OperatorName': 'Оператор Два', 'CallDate': '2026-10-08', 'CallCount': 17}]


def query(name='call_count', **params):
    base = {'date_from': '20261008', 'date_to': '20261009'}
    base.update(params)
    return oktell_datasets.build(name, base)


class FakeDb:
    def __init__(self, rows=None):
        self.rows = ROWS if rows is None else rows
        self.queries = []
        self.errors = []          # очередь отказов: по одному на запрос
        self.denied = set()       # имена таблиц, на которые «нет права»
        self.closed = 0

    def query(self, sql):
        self.queries.append(sql)
        for name in self.denied:
            if name in sql:
                raise oktell_db.OktellDbError('The SELECT permission was denied on the object',
                                              kind='permission', number=229)
        if self.errors:
            raise self.errors.pop(0)
        return [dict(row) for row in self.rows]

    def close(self):
        self.closed += 1


class _Response:
    def __init__(self, flask_response):
        self.status_code = flask_response.status_code
        self.text = flask_response.get_data(as_text=True)

    def json(self):
        return json.loads(self.text)


class PortalSession:
    """Сессия исполнителя, за которой стоит настоящая ручка портала."""

    def __init__(self, client):
        self.client = client
        self.posts = []
        self.replies = []
        self.down = None

    def post(self, url, data=None, timeout=None, headers=None):
        self.posts.append(json.loads(data.decode('utf-8')))
        if self.down is not None:
            raise self.down
        path = url.split('://', 1)[1].split('/', 1)[1]
        response = _Response(self.client.post('/' + path, data=data, headers=headers))
        self.replies.append(response.text)
        return response


class Harness(unittest.TestCase):

    def setUp(self):
        self.jobs = live_jobs.Jobs()
        app = Flask(__name__)
        app.register_blueprint(live_routes.build_oktell_live_blueprint(
            build_cors_preflight_response=lambda: ('', 204),
            wallboard_params=lambda: {'sl_seconds': 20, 'test_keys': []},
            store=live_store.Store(), job_queue=self.jobs))
        patcher = mock.patch.dict(os.environ, {'CDR_AGENT_KEYS': PUBLIC_B64}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(live_jobs.ENABLED_ENV, None)
        self.addCleanup(os.environ.pop, live_jobs.ENABLED_ENV, None)
        self.session = PortalSession(app.test_client())
        self.db = FakeDb()
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)
        self.signer = signing.Signer(signing.load_private_key(PRIVATE_B64))
        self.worker = self.make_worker()
        # Короткие ожидания: тест не должен стоять двадцать секунд на пустом опросе.
        for target, name, value in ((live_jobs, 'POLL_WAIT_SECONDS', 0.4), (live_jobs, 'PICKUP_SECONDS', 0.5),
                                    (oktell_jobs, 'POLL_WAIT_SECONDS', 0.4)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def make_worker(self, index=0, db=None):
        return oktell_jobs.Worker(index, 'https://portal.example', self.session, self.signer,
                                  db or self.db, lambda: {'agent_id': 'gw-1', 'hostname': 'gw',
                                                          'version': 'oktell/1.1.0/3.12'},
                                  self.stop, sleep=lambda seconds: None)

    def serve(self, worker=None):
        """Исполнитель в своём потоке, как на шлюзе."""
        worker = worker or self.worker
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.stop.set)
        deadline = time.monotonic() + 3
        while not self.jobs.known() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.jobs.known(), 'исполнитель не вышел на связь')
        return thread


class RoundTripTests(Harness):

    def test_query_runs_on_the_gateway_and_rows_come_back(self):
        self.serve()
        asked = query()
        rows = self.jobs.run(asked, 5)
        self.assertEqual(rows, ROWS)
        self.assertEqual(self.db.queries[-1], str(asked), 'в базу ушёл ровно текст каталога')
        self.assertEqual(self.jobs.status()['stats']['ok'], 1)

    def test_the_gateway_never_receives_sql_text(self):
        """На шлюз едут имя набора, параметры и отпечаток. Текста запроса в ответе портала нет."""
        self.serve()
        asked = query('billing_operator_states', minute_from=0, minute_to=1439)
        self.jobs.run(asked, 5)
        with_jobs = [json.loads(text) for text in self.session.replies if json.loads(text).get('jobs')]
        self.assertEqual(len(with_jobs), 1)
        job = with_jobs[0]['jobs'][0]
        self.assertEqual(set(job), {'id', 'dataset', 'params', 'digest'})
        self.assertEqual(job['dataset'], 'billing_operator_states')
        self.assertEqual(job['params'], asked.params)
        self.assertEqual(job['digest'], asked.digest)
        for reply in self.session.replies:
            self.assertNotIn('SELECT', reply)
            self.assertNotIn('oktell_cc_temp', reply)

    def test_several_callers_are_served_in_turn(self):
        self.serve()
        second = self.make_worker(index=1, db=FakeDb())
        self.serve(second)
        results = {}

        def call(index):
            results[index] = self.jobs.run(query(date_from='202610%02d' % (index + 1)), 5)

        threads = [threading.Thread(target=call, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(results), list(range(6)))
        self.assertTrue(all(rows == ROWS for rows in results.values()))

    def test_worker_reports_its_catalog_and_tables(self):
        self.db.denied = {'quality_employes', 'A_Users'}
        with self.assertLogs(oktell_jobs.log, level='WARNING') as captured:
            self.serve()
        self.assertIn('oktell.dbo.quality_employes', captured.output[0])
        status = self.jobs.status()
        self.assertEqual(status['workers'][0]['unreadable'], ['ratings', 'users'])
        self.assertEqual(status['workers'][0]['catalog'], oktell_datasets.fingerprint())
        self.assertEqual(status['workers'][0]['datasets'], len(oktell_datasets.NAMES))
        # Набор с неданной таблицей портал шлюзу даже не предлагает.
        self.assertIn('не дано право', self.jobs.why_not(query('guard_history', since='20261008 10:00:00',
                                                               until='20261008 11:00:00')))
        self.assertEqual(self.jobs.why_not(query()), '')
        # Таблицы считаются по параметрам: «Группировка» биллинга оценок водителей не читает.
        billing = {'minute_from': 0, 'minute_to': 1439, 'sl_seconds': 20}
        self.assertEqual(self.jobs.why_not(query('billing', group_by='hour', **billing)), '')
        self.assertIn('не дано право', self.jobs.why_not(query('billing', group_by='park', **billing)))

    def test_unsigned_worker_is_not_let_in(self):
        os.environ['CDR_AGENT_KEYS'] = signing.generate()[1]
        with self.assertLogs(level='WARNING'):
            with self.assertRaises(RuntimeError) as caught:
                self.worker.step([])
        self.assertIn('не принял подпись', str(caught.exception))
        self.assertFalse(self.jobs.known())

    def test_a_busy_gateway_makes_the_next_query_wait_not_give_up(self):
        """Единственный исполнитель занят долгим запросом дольше срока «никто не забрал»:
        следующий запрос ждёт своей очереди и исполняется, а не уходит мосту."""
        release = threading.Event()
        slow_db = FakeDb()
        plain_query = slow_db.query

        def slow(sql):
            if 'A_Cube_CC_OperatorStates os' in sql and '20261001' in sql:
                release.wait(5)
            return plain_query(sql)

        slow_db.query = slow
        self.serve(self.make_worker(db=slow_db))
        results = {}
        first = threading.Thread(target=lambda: results.update(first=self.jobs.run(query(date_from='20261001'), 10)))
        first.start()
        deadline = time.monotonic() + 3
        while not self.jobs.status()['in_flight'] and time.monotonic() < deadline:
            time.sleep(0.01)
        second = threading.Thread(target=lambda: results.update(second=self.jobs.run(query(), 10)))
        second.start()
        time.sleep(live_jobs.PICKUP_SECONDS * 2.5)
        self.assertEqual(results, {}, 'оба ещё ждут: первый исполняется, второй в очереди')
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(results, {'first': ROWS, 'second': ROWS})

    def test_a_query_the_queue_never_reached_goes_to_the_bridge(self):
        """Исполнитель занят дольше, чем вызывающий готов ждать: до запроса очередь не дошла,
        он не исполнялся — значит, его можно отдать мосту (Refused, а не Failed)."""
        self.jobs.worker_seen('gw-1#0', version='v', catalog='x', datasets=None)
        blocker = threading.Thread(target=lambda: self.assertRaises(live_jobs.Failed, self.jobs.run, query(), 2))
        blocker.start()
        self.jobs.take('gw-1#0', 0.4)          # исполнитель забрал первое задание и «думает»
        with self.assertRaises(live_jobs.Refused) as caught:
            self.jobs.run(query(date_from='20261002'), 1)
        self.assertIn('очередь запросов', str(caught.exception))
        blocker.join(4)


class RefusalTests(Harness):
    """Шлюз за запрос не взялся — его можно отдать мосту."""

    def test_no_worker_means_no_waiting(self):
        self.assertEqual(self.jobs.why_not(query()), 'исполнителя запросов на VM шлюза нет на связи')
        self.assertFalse(self.jobs.can_run(query()))

    def test_plain_sql_never_goes_to_the_gateway(self):
        self.serve()
        self.assertEqual(self.jobs.why_not('SELECT 1'), 'у запроса нет набора каталога')
        self.assertEqual(self.jobs.why_not(str(query())), 'у запроса нет набора каталога')

    def test_switch_turns_the_gateway_off(self):
        self.serve()
        os.environ[live_jobs.ENABLED_ENV] = '0'
        self.assertIn('выключены', self.jobs.why_not(query()))
        self.assertFalse(self.jobs.known())

    def test_nobody_took_the_job(self):
        """Исполнитель числится на связи, но задание не забирает (завис, перезапускается)."""
        self.jobs.worker_seen('gw-1#0', version='oktell/1.1.0', catalog='x', datasets=None)
        started = time.monotonic()
        with self.assertRaises(live_jobs.Refused) as caught:
            self.jobs.run(query(), 30)
        self.assertLess(time.monotonic() - started, 3, 'ждём забора недолго, а не весь таймаут')
        self.assertIn('не забрал', str(caught.exception))
        self.assertEqual(self.jobs.status()['pending'], 0)

    def test_catalog_mismatch_is_refused_not_executed(self):
        """Портал новее шлюза: текст набора у них разный — шлюз его не исполняет."""
        self.serve()

        class NewerPortalQuery(oktell_datasets.Query):
            """Текст набора у портала уже другой — значит, другой и отпечаток."""
            digest = 'f' * 64

        fresh = query()
        asked = NewerPortalQuery(str(fresh) + ' /* новая версия */', fresh.dataset, fresh.params)
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Refused) as caught:
                self.jobs.run(asked, 5)
        self.assertIn('коннектор Oktell нужно обновить', str(caught.exception))
        self.assertEqual(self.db.queries[len(oktell_datasets.TABLES):], [],
                         'кроме проверки таблиц, в базу ничего не ушло')

    def test_unknown_dataset_on_the_gateway(self):
        result = oktell_jobs.execute(self.db, {'id': '1', 'dataset': 'brand_new', 'params': {}, 'digest': 'x'})
        self.assertEqual((result['ok'], result['reason']), (False, 'unknown'))
        self.assertEqual(self.db.queries, [])

    def test_bad_params_on_the_gateway(self):
        result = oktell_jobs.execute(self.db, {'id': '1', 'dataset': 'call_count', 'digest': 'x',
                                               'params': {'date_from': "20261008' OR 1=1", 'date_to': '20261009'}})
        self.assertEqual((result['ok'], result['reason']), (False, 'params'))
        self.assertEqual(self.db.queries, [])

    def test_sql_sent_by_the_portal_is_never_executed(self):
        asked = query()
        evil = {'id': '1', 'dataset': 'call_count', 'params': dict(asked.params, sql='DROP TABLE x'),
                'digest': asked.digest, 'sql': 'DROP TABLE oktell.dbo.Call_Systems_hst', 'text': 'SELECT 1'}
        result = oktell_jobs.execute(self.db, evil)
        self.assertTrue(result['ok'])
        self.assertEqual(self.db.queries, [str(asked)])
        only_sql = oktell_jobs.execute(self.db, {'id': '2', 'sql': 'SELECT * FROM oktell_settings.dbo.A_Users'})
        self.assertEqual(only_sql['reason'], 'unknown')
        self.assertEqual(len(self.db.queries), 1)

    def test_permission_denied_goes_to_the_bridge(self):
        self.serve()
        self.db.denied = {'A_Cube_CC_OperatorStates'}
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Refused) as caught:
                self.jobs.run(query(), 5)
        self.assertIn('не дано право', str(caught.exception))

    def test_database_down_goes_to_the_bridge_and_is_not_hammered(self):
        self.serve()
        self.db.errors = [oktell_db.OktellDbError('вход в базу Oktell не удался', kind='unavailable')]
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Refused):
                self.jobs.run(query(), 5)
        asked = len(self.db.queries)
        with self.assertRaises(live_jobs.Refused):
            self.jobs.run(query(), 5)
        self.assertEqual(len(self.db.queries), asked, 'пока пауза — базу не трогаем')


class FailureTests(Harness):
    """Запрос исполнялся и не удался — мосту он не повторяется."""

    def test_database_error_is_a_failure(self):
        self.serve()
        self.db.errors = [oktell_db.OktellDbError('запрос к базе Oktell не выполнен: Conversion failed', kind='db')]
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Failed) as caught:
                self.jobs.run(query(), 5)
        self.assertIn('Conversion failed', str(caught.exception))

    def test_query_timeout_is_a_failure(self):
        self.serve()
        self.db.errors = [oktell_db.OktellDbError('timed out', kind='timeout', number=20003)]
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Failed) as caught:
                self.jobs.run(query(), 5)
        self.assertIn('не ответила', str(caught.exception))

    def test_no_answer_in_time_is_a_failure(self):
        """Задание забрали, а ответа нет: вызывающий не ждёт дольше своего таймаута."""
        self.jobs.worker_seen('gw-1#0', version='v', catalog='x', datasets=None)
        taker = threading.Thread(target=lambda: self.jobs.take('gw-1#0', 0.4))
        taker.start()
        with self.assertRaises(live_jobs.Failed) as caught:
            self.jobs.run(query(), 1)
        taker.join(2)
        self.assertIn('не ответил', str(caught.exception))
        self.assertEqual(self.jobs.finish([{'id': 'нет такого', 'ok': True, 'rows': []}]), 0)

    def test_deadlock_victim_is_retried_once(self):
        self.db.errors = [oktell_db.OktellDbError('deadlocked', kind='deadlock', number=1205)]
        with mock.patch.object(oktell_jobs, 'DEADLOCK_RETRY_PAUSE', 0):
            result = oktell_jobs.execute(self.db, {'id': '1', 'dataset': 'call_count', 'params': query().params,
                                                   'digest': query().digest})
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.db.queries), 2)

    def test_stale_connection_is_reopened_once(self):
        """Соединение исполнителя живёт сутками и за ночь простоя может умереть: первый запрос
        по нему падает обрывом. Это не «базы нет» — один повтор по новому соединению."""
        job = {'id': '1', 'dataset': 'call_count', 'params': query().params, 'digest': query().digest}
        self.db.connected = True
        self.db.errors = [oktell_db.OktellDbError('Write to the server failed', kind='unavailable', number=20006)]
        result = oktell_jobs.execute(self.db, job)
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.db.queries), 2)
        # Соединения не было вовсе, и открыть его не вышло — базы действительно нет: без повтора.
        fresh = FakeDb()
        fresh.connected = False
        fresh.errors = [oktell_db.OktellDbError('вход в базу Oktell не удался', kind='unavailable')]
        result = oktell_jobs.execute(fresh, job)
        self.assertEqual((result['ok'], result['reason']), (False, 'unavailable'))
        self.assertEqual(len(fresh.queries), 1)

    def test_rows_must_look_like_rows(self):
        self.assertEqual(live_jobs.clean_rows([{'a': 1, 'b': 'x', 'c': None, 'd': 1.5, 'e': True}]),
                         [{'a': 1, 'b': 'x', 'c': None, 'd': 1.5, 'e': True}])
        for bad in ({'rows': 1}, [[1, 2]], [{'a': {'nested': 1}}], [{'a': [1]}], [{}] * (live_jobs.MAX_ROWS + 1)):
            with self.subTest(bad=str(bad)[:30]):
                with self.assertRaises(ValueError):
                    live_jobs.clean_rows(bad)
        self.assertIsNone(live_jobs.clean_rows([{'a': float('nan')}])[0]['a'])


class ErrorKindTests(unittest.TestCase):

    def test_error_numbers_map_to_kinds(self):
        for number, kind in ((229, 'permission'), (230, 'permission'), (916, 'permission'),
                             (20003, 'timeout'), (1222, 'timeout'), (1205, 'deadlock'),
                             (18456, 'auth'), (20009, 'unavailable'), (20047, 'unavailable'),
                             (245, 'db'), (None, 'db')):
            with self.subTest(number=number):
                self.assertEqual(oktell_db.error_kind(number), kind)
        self.assertEqual(oktell_db.error_kind(None, connecting=True), 'unavailable')
        self.assertEqual(oktell_db.error_kind(18456, connecting=True), 'auth')

    def test_job_connection_caps_rows_on_the_server(self):
        db = oktell_db.OktellDb('10.0.0.24', 1433, 'reader', 'secret', query_timeout=60,
                                row_limit=oktell_datasets.MAX_ROWS)
        self.assertEqual(db.session_setup()[-1], 'SET ROWCOUNT 1000')
        self.assertEqual(db.query_timeout, 60)
        self.assertNotIn('SET ROWCOUNT', ' '.join(oktell_db.OktellDb('h', 1433, 'u', 'p').session_setup()))

    def test_probe_tells_denied_from_unknown(self):
        db = FakeDb()
        db.denied = {'A_Users'}
        self.assertEqual(oktell_jobs.probe_tables(db), ['users'])
        self.assertEqual(len(db.queries), len(oktell_datasets.TABLES))
        self.assertTrue(all(sql.startswith('SELECT TOP 0 ') for sql in db.queries))
        down = FakeDb()
        down.errors = [oktell_db.OktellDbError('обрыв', kind='unavailable')]
        with self.assertRaises(oktell_db.OktellDbError):
            oktell_jobs.probe_tables(down)

    def test_workers_count_is_clamped(self):
        self.assertEqual(oktell_jobs.clamp_workers('2'), 2)
        self.assertEqual(oktell_jobs.clamp_workers('0'), 0)
        self.assertEqual(oktell_jobs.clamp_workers('99'), oktell_jobs.MAX_WORKERS)
        self.assertEqual(oktell_jobs.clamp_workers('мусор'), oktell_jobs.DEFAULT_WORKERS)


class PortalEntryTests(Harness):
    """`_oktell_query` монолита: сначала шлюз, мост — запасной путь."""

    def namespace(self, outcomes=(), bridge=True):
        source = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        session = _FakeSession(outcomes)
        ns = {
            'requests': requests, 'logging': logging, 'RemoteDisconnected': RemoteDisconnected,
            'OKTELL_API_URL': 'http://proxy.test:8085/query' if bridge else '',
            'OKTELL_API_TOKEN': 'secret' if bridge else '',
            'OKTELL_API_TIMEOUT_SECONDS': 5, 'OKTELL_API_CONNECT_TIMEOUT_SECONDS': 5,
            '_oktell_session': session,
        }
        _load_names(source, {'_OKTELL_DROPPED_CONNECTION_ERRORS', '_oktell_dropped_keepalive',
                             '_oktell_bridge_ready', '_oktell_api_ready', '_oktell_bridge_query',
                             '_oktell_query'}, ns)
        ns['_fake_session'] = session
        patch = mock.patch.object(live_jobs, 'jobs', self.jobs)
        patch.start()
        self.addCleanup(patch.stop)
        for name in ('can_run', 'why_not', 'run', 'known'):
            patch = mock.patch.object(live_jobs, name, getattr(self.jobs, name))
            patch.start()
            self.addCleanup(patch.stop)
        return ns

    def test_catalog_query_goes_to_the_gateway_and_the_bridge_is_not_touched(self):
        ns = self.namespace()
        self.serve()
        self.assertEqual(ns['_oktell_query'](query()), ROWS)
        self.assertEqual(ns['_fake_session'].calls, [])

    def test_plain_sql_goes_to_the_bridge_only(self):
        ns = self.namespace()
        self.serve()
        self.assertEqual(ns['_oktell_query']('SELECT 1'), [{'ok': 1}])
        self.assertEqual(len(ns['_fake_session'].calls), 1)
        self.assertEqual(self.jobs.status()['stats'].get('submitted', 0), 0)

    def test_no_gateway_means_the_bridge_at_once(self):
        ns = self.namespace()
        started = time.monotonic()
        self.assertEqual(ns['_oktell_query'](query()), [{'ok': 1}])
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(ns['_fake_session'].calls[0][1]['json'], {'sql': str(query())})

    def test_refused_by_the_gateway_falls_back_to_the_bridge(self):
        ns = self.namespace()
        self.serve()
        self.db.denied = {'A_Cube_CC_OperatorStates'}
        with self.assertLogs(live_jobs.log, level='WARNING'):
            self.assertEqual(ns['_oktell_query'](query()), [{'ok': 1}])
        self.assertEqual(len(ns['_fake_session'].calls), 1)

    def test_failed_on_the_gateway_is_not_repeated_on_the_bridge(self):
        ns = self.namespace()
        self.serve()
        self.db.errors = [oktell_db.OktellDbError('timed out', kind='timeout', number=20003)]
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Failed):
                ns['_oktell_query'](query())
        self.assertEqual(ns['_fake_session'].calls, [], 'второй тяжёлый запрос к базе не уходит')

    def test_both_roads_closed_names_both_reasons(self):
        ns = self.namespace([requests.exceptions.ConnectTimeout('Connection to 89.107.98.195 timed out')])
        with self.assertRaises(RuntimeError) as caught:
            ns['_oktell_query'](query())
        text = str(caught.exception)
        self.assertIn('исполнителя запросов на VM шлюза нет на связи', text)
        self.assertIn('мост Oktell: Connection to 89.107.98.195 timed out', text)

    def test_gateway_alone_is_enough_when_the_bridge_is_gone(self):
        ns = self.namespace(bridge=False)
        self.assertFalse(ns['_oktell_api_ready']())
        self.serve()
        self.assertTrue(ns['_oktell_api_ready'](), 'мост не настроен, но шлюз на связи')
        self.assertEqual(ns['_oktell_query'](query()), ROWS)
        with self.assertRaises(RuntimeError) as caught:
            ns['_oktell_query']('SELECT 1')
        self.assertIn('OKTELL_IP/OKTELL_API_TOKEN is not set', str(caught.exception))

    def test_caller_timeout_limits_the_wait(self):
        ns = self.namespace()
        self.jobs.worker_seen('gw-1#0', version='v', catalog='x', datasets=None)
        taker = threading.Thread(target=lambda: self.jobs.take('gw-1#0', 0.4))
        taker.start()
        started = time.monotonic()
        with self.assertRaises(live_jobs.Failed):
            ns['_oktell_query'](query(), timeout=1)
        taker.join(2)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == '__main__':
    unittest.main()
