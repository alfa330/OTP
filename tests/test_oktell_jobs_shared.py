# -*- coding: utf-8 -*-
"""Общая очередь запросов к Oktell (oktell_live/shared.py): портал в нескольких процессах.

25.11.2026 после переезда в облако «Биллинг Oktell» падал через раз: портал — две копии api за
балансировщиком и worker без HTTP, а очередь жила в памяти процесса. Исполнитель коннектора
приносит ответ следующим опросом, балансировщик отдаёт его другой копии — ответ выбрасывался, и
запрос через 60 с падал. Запросы worker до исполнителя не доходили вовсе.

Здесь копии портала настоящие (две Flask-ручки коннектора), исполнитель коннектора настоящий
(cdr_bridge/oktell_jobs.Worker), а «балансировщик» отдаёт его запросы копиям строго по очереди —
худший для старой очереди случай. Хранилище — MemoryStore, двойник PgStore с той же семантикой
(сигнал уходит после записи, ответ забирается один раз). Сам SQL PgStore проверяет
PgStoreTests — на настоящем PostgreSQL, если задан OKTELL_SHARED_TEST_DSN.
"""
import base64
import json
import os
import sys
import threading
import time
import unittest
import uuid
from unittest import mock

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tests.test_oktell_jobs as base  # noqa: E402
from cdr_bridge import oktell_jobs, signing  # noqa: E402
from oktell_live import jobs as live_jobs, routes as live_routes, shared, store as live_store  # noqa: E402

RECORD_PATH = '20261112/0923/mix_13003_17e006__2026_11_12__09_23_30_923.mp3'
AUDIO = b'ID3' + b'\x00' * 64


def _copy(value):
    """Как через JSONB: несериализуемое падает здесь же, а не в проде."""
    return json.loads(json.dumps(value, ensure_ascii=False))


class MemoryStore:
    """Двойник PgStore: то же поведение, хранение — словарь под замком, сигналы — подписчикам сразу."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}
        self.order = 0
        self.workers_seen = {}
        self.callbacks = []
        self.signals = True
        self.fail = None
        self.started = 0

    def subscribe(self, callback):
        self.callbacks.append(callback)

    def start(self):
        self.started += 1

    def _signal(self, payload):
        if self.signals:
            for callback in list(self.callbacks):
                callback(payload)

    def _check(self):
        if self.fail is not None:
            raise self.fail

    def submit(self, job_id, kind, view, timeout):
        self._check()
        with self.lock:
            self.order += 1
            self.rows[job_id] = {'kind': kind, 'job': _copy(view), 'order': self.order,
                                 'deadline': time.monotonic() + float(timeout), 'taken_at': None,
                                 'worker': None, 'done_at': None, 'result': None, 'audio': None}
        self._signal('n')

    def claim(self, worker):
        self._check()
        now = time.monotonic()
        with self.lock:
            waiting = sorted((row['order'], key) for key, row in self.rows.items()
                             if row['taken_at'] is None and row['deadline'] > now)
            if not waiting:
                return None
            row = self.rows[waiting[0][1]]
            row['taken_at'], row['worker'] = now, worker
            return _copy(row['job'])

    def taken(self, job_id):
        self._check()
        with self.lock:
            row = self.rows.get(job_id)
            if row is None or row['taken_at'] is None or row['done_at'] is not None:
                return None
            return row['kind'], _copy(row['job'])

    def complete(self, job_id, result, audio):
        self._check()
        with self.lock:
            row = self.rows.get(job_id)
            if row is None or row['done_at'] is not None:
                return False
            row['done_at'], row['result'], row['audio'] = time.monotonic(), _copy(result), audio
        self._signal('d:' + job_id)
        return True

    def collect(self, job_id):
        self._check()
        with self.lock:
            row = self.rows.get(job_id)
            if row is None or row['done_at'] is None:
                return None
            del self.rows[job_id]
        return row['result'], row['audio']

    def drop_if_idle(self, job_id):
        self._check()
        now = time.monotonic()
        with self.lock:
            row = self.rows.get(job_id)
            if row is None or row['taken_at'] is not None:
                return False
            if any(other['taken_at'] is not None and other['done_at'] is None and other['deadline'] > now
                   for other in self.rows.values()):
                return False
            del self.rows[job_id]
            return True

    def withdraw(self, job_id):
        self._check()
        with self.lock:
            row = self.rows.pop(job_id, None)
        if row is None:
            return False, None, None
        if row['done_at'] is None:
            return row['taken_at'] is not None, None, None
        return True, row['result'], row['audio']

    def snapshot(self):
        self._check()
        now = time.monotonic()
        with self.lock:
            live = [row for row in self.rows.values() if row['deadline'] > now]
        return {'pending': sum(1 for row in live if row['taken_at'] is None),
                'in_flight': sum(1 for row in live if row['taken_at'] is not None and row['done_at'] is None),
                'answered': sum(1 for row in live if row['done_at'] is not None)}

    def worker_seen(self, worker, info):
        self._check()
        with self.lock:
            self.workers_seen[worker] = (_copy(info), time.monotonic())

    def touch_worker(self, worker):
        self._check()
        with self.lock:
            if worker in self.workers_seen:
                self.workers_seen[worker] = (self.workers_seen[worker][0], time.monotonic())

    def workers(self, silent_seconds):
        self._check()
        now = time.monotonic()
        with self.lock:
            return [(worker, _copy(info), now - seen) for worker, (info, seen) in sorted(self.workers_seen.items())
                    if now - seen < silent_seconds]


class BalancedSession:
    """Сессия исполнителя за балансировщиком: каждый запрос — следующей копии портала, по кругу."""

    def __init__(self, clients):
        self.clients = clients
        self.lock = threading.Lock()
        self.turn = 0
        self.hits = [0] * len(clients)

    def post(self, url, data=None, timeout=None, headers=None):
        with self.lock:
            index = self.turn % len(self.clients)
            self.turn += 1
            self.hits[index] += 1
        path = url.split('://', 1)[1].split('/', 1)[1]
        return base._Response(self.clients[index].post('/' + path, data=data, headers=headers))


def _copy_of_portal(queue):
    """Одна копия портала: своя ручка коннектора, своя очередь (общая или в памяти)."""
    app = Flask(__name__)
    app.register_blueprint(live_routes.build_oktell_live_blueprint(
        build_cors_preflight_response=lambda: ('', 204),
        wallboard_params=lambda: {'sl_seconds': 20, 'test_keys': []},
        store=live_store.Store(), job_queue=queue))
    return app.test_client()


class Copies(unittest.TestCase):
    """Две копии api и процесс worker без HTTP; исполнитель коннектора ходит через балансировщик."""

    shared_queue = True

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {'CDR_AGENT_KEYS': base.PUBLIC_B64}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(live_jobs.ENABLED_ENV, None)
        for target, name, value in ((live_jobs, 'POLL_WAIT_SECONDS', 0.4), (live_jobs, 'PICKUP_SECONDS', 0.6),
                                    (oktell_jobs, 'POLL_WAIT_SECONDS', 0.4), (shared, 'RECHECK_SECONDS', 0.2),
                                    (shared, 'TAKE_RECHECK_SECONDS', 0.2), (shared, 'ALIVE_CACHE_SECONDS', 0.0)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.store = MemoryStore()
        if self.shared_queue:
            self.api_a, self.api_b, self.worker_process = (shared.SharedJobs(self.store) for _ in range(3))
        else:
            self.api_a, self.api_b, self.worker_process = (live_jobs.Jobs() for _ in range(3))
        self.balancer = BalancedSession([_copy_of_portal(self.api_a), _copy_of_portal(self.api_b)])
        self.db = base.FakeDb()
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)
        signer = signing.Signer(signing.load_private_key(base.PRIVATE_B64))
        self.executor = oktell_jobs.Worker(0, 'https://portal.example', self.balancer, signer, self.db,
                                           lambda: {'agent_id': 'gw-1', 'hostname': 'gw',
                                                    'version': 'oktell/1.2.0/3.12'},
                                           self.stop, sleep=lambda seconds: None)

    def executed(self):
        """Запросы коннектора к базе без проверок доступа к таблицам (SELECT TOP 0 при старте)."""
        return [sql for sql in self.db.queries if not sql.startswith('SELECT TOP 0')]

    def serve(self):
        thread = threading.Thread(target=self.executor.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.stop.set)
        deadline = time.monotonic() + 3
        while min(self.balancer.hits) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(min(self.balancer.hits), 2, 'исполнитель не опросил обе копии')


class InMemoryQueueTests(Copies):
    """Как было до 25.11.2026 — очередь в памяти каждой копии. Закрепляет, ЧТО чинили."""

    shared_queue = False

    def test_answer_that_lands_in_the_other_copy_is_lost(self):
        self.serve()
        with self.assertRaises(live_jobs.Failed):
            self.api_a.run(base.query(), 1.5)
        self.assertEqual(len(self.executed()), 1, 'коннектор запрос исполнил — потерялся ответ')

    def test_worker_process_never_reaches_the_connector(self):
        self.serve()
        self.assertFalse(self.worker_process.known())


class SharedQueueTests(Copies):

    def test_every_answer_reaches_the_copy_that_asked(self):
        self.serve()
        for index in range(6):
            asking = (self.api_a, self.api_b)[index % 2]
            rows = asking.run(base.query(date_from='202610%02d' % (index + 1)), 5)
            self.assertEqual([row['CallCount'] for row in rows], [41, 17])
        self.assertEqual(len(self.executed()), 6)
        self.assertEqual(self.store.rows, {}, 'забранный ответ не остаётся в таблице')

    def test_worker_process_without_http_is_served(self):
        self.serve()
        self.assertTrue(self.worker_process.known(), 'worker видит исполнителя, который опрашивает api')
        self.assertEqual(self.worker_process.why_not(base.query()), '')
        rows = self.worker_process.run(base.query(), 5)
        self.assertEqual(len(rows), 2)

    def test_callers_from_all_processes_at_once(self):
        self.serve()
        results = {}

        def ask(name, queue, day):
            results[name] = queue.run(base.query(date_from='202610%02d' % day), 10)

        threads = [threading.Thread(target=ask, args=(name, queue, day))
                   for day, (name, queue) in enumerate(
                       (('a', self.api_a), ('b', self.api_b), ('w', self.worker_process), ('a2', self.api_a)), 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(results), ['a', 'a2', 'b', 'w'])
        self.assertTrue(all(len(rows) == 2 for rows in results.values()))

    def test_lost_signals_are_covered_by_rechecks(self):
        self.store.signals = False
        self.serve()
        rows = self.api_b.run(base.query(), 5)
        self.assertEqual(len(rows), 2)

    def test_tables_denied_to_the_gateway_are_known_in_every_process(self):
        self.db.denied = {'quality_employes', 'A_Users'}
        self.serve()
        refusal = self.worker_process.why_not(
            base.query('guard_history', since='20261008 10:00:00', until='20261008 11:00:00'))
        self.assertEqual(refusal, 'учётке шлюза не выдано право читать таблицы базы Oktell: '
                                  'oktell_settings.dbo.A_Users (права выдаёт администратор SQL Server)')
        self.assertEqual(self.worker_process.why_not(base.query()), '')

    def test_permission_refusal_from_the_gateway_reaches_the_caller(self):
        self.serve()
        self.db.denied = {'A_Cube_CC_OperatorStates'}
        with self.assertRaises(live_jobs.Refused) as caught:
            self.api_a.run(base.query(), 5)
        self.assertIn('не выдано право читать таблицу базы Oktell', str(caught.exception))


class QueueTests(unittest.TestCase):
    """Очередь без исполнителя коннектора: что лежит в хранилище и кому достаётся ответ."""

    def setUp(self):
        for target, name, value in ((live_jobs, 'PICKUP_SECONDS', 0.3), (shared, 'RECHECK_SECONDS', 0.05),
                                    (shared, 'TAKE_RECHECK_SECONDS', 0.05), (shared, 'ALIVE_CACHE_SECONDS', 0.0)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop(live_jobs.ENABLED_ENV, None)
        self.store = MemoryStore()
        self.a = shared.SharedJobs(self.store)
        self.b = shared.SharedJobs(self.store)
        for queue in (self.a, self.b):
            queue.worker_seen('gw-1#0', version='oktell/1.2.0', catalog='x', datasets=None, records=True)

    def _in_thread(self, call):
        box = {}

        def target():
            try:
                box['value'] = call()
            except Exception as exc:  # noqa: BLE001
                box['error'] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        return thread, box

    def test_record_asked_in_one_copy_comes_back_through_the_other(self):
        thread, box = self._in_thread(lambda: self.a.fetch_record([RECORD_PATH], 3))
        taken = self.b.take('gw-1#0', 2)
        self.assertEqual(taken[0]['paths'], [RECORD_PATH])
        delivered = self.b.finish([{'id': taken[0]['id'], 'ok': True, 'found': True, 'path': RECORD_PATH,
                                    'content_type': 'audio/mpeg', 'audio': base64.b64encode(AUDIO).decode()}])
        self.assertEqual(delivered, 1)
        thread.join(3)
        self.assertEqual(box.get('value'), (AUDIO, 'audio/mpeg'))

    def test_bad_record_is_rejected_in_the_copy_that_received_it(self):
        thread, box = self._in_thread(lambda: self.a.fetch_record([RECORD_PATH], 3))
        taken = self.b.take('gw-1#0', 2)
        self.b.finish([{'id': taken[0]['id'], 'ok': True, 'found': True, 'path': 'чужой/путь.mp3',
                        'content_type': 'audio/mpeg', 'audio': base64.b64encode(AUDIO).decode()}])
        thread.join(3)
        self.assertIsInstance(box.get('error'), live_jobs.Failed)

    def test_nobody_takes_the_job(self):
        with self.assertRaises(live_jobs.Refused):
            self.a.run(base.query(), 5)
        self.assertEqual(self.store.rows, {}, 'снятое задание не остаётся в таблице')

    def test_taken_but_unanswered_is_a_failure_and_late_answer_is_dropped(self):
        thread, box = self._in_thread(lambda: self.a.run(base.query(), 0.6))
        taken = self.b.take('gw-1#0', 2)
        thread.join(3)
        self.assertIsInstance(box.get('error'), live_jobs.Failed)
        self.assertEqual(self.b.finish([{'id': taken[0]['id'], 'ok': True, 'rows': []}]), 0)
        self.assertEqual(self.store.rows, {})

    def test_database_down_means_refusal_not_waiting(self):
        self.store.fail = RuntimeError('connection refused')
        started = time.monotonic()
        with self.assertRaises(live_jobs.Refused):
            self.a.run(base.query(), 5)
        self.assertLess(time.monotonic() - started, 1)

    def test_switch_keeps_the_queue_in_memory(self):
        with mock.patch.dict(os.environ, {shared.SHARED_ENV: '0'}), \
                mock.patch.object(live_jobs, 'jobs', live_jobs.Jobs()):
            queue = shared.configure(cursor=None, listen_connect=None)
            self.assertNotIsInstance(queue, shared.SharedJobs)

    def test_configure_makes_module_functions_use_the_shared_queue(self):
        with mock.patch.object(live_jobs, 'jobs', live_jobs.Jobs()):
            queue = shared.configure(cursor=None, listen_connect=None)
            self.assertIsInstance(queue, shared.SharedJobs)
            self.assertIs(live_jobs.jobs, queue)
            with mock.patch.object(queue, 'why_not', return_value='из общей очереди'):
                self.assertEqual(live_jobs.why_not(base.query()), 'из общей очереди')


@unittest.skipUnless(os.environ.get('OKTELL_SHARED_TEST_DSN'), 'нужен PostgreSQL: OKTELL_SHARED_TEST_DSN')
class PgStoreTests(unittest.TestCase):
    """SQL PgStore на настоящем PostgreSQL: две очереди-«копии» поверх одной базы и сигналы LISTEN."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        from oktell_live.schema import init_oktell_live_schema
        cls.psycopg2 = psycopg2
        cls.dsn = os.environ['OKTELL_SHARED_TEST_DSN']
        with cls._cursor() as cur:
            init_oktell_live_schema(cur)

    @classmethod
    def _cursor(cls):
        dsn = cls.dsn
        psycopg2 = cls.psycopg2

        class _Ctx:
            def __enter__(self):
                self.conn = psycopg2.connect(dsn)
                self.cur = self.conn.cursor()
                return self.cur

            def __exit__(self, kind, value, trace):
                if kind is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
                self.conn.close()
                return False

        return _Ctx()

    def setUp(self):
        with self._cursor() as cur:
            cur.execute('DELETE FROM oktell_jobs')
            cur.execute('DELETE FROM oktell_workers')
        for target, name, value in ((live_jobs, 'PICKUP_SECONDS', 0.5), (shared, 'ALIVE_CACHE_SECONDS', 0.0)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop(live_jobs.ENABLED_ENV, None)
        self.stores = [shared.PgStore(self._cursor, lambda: self.psycopg2.connect(self.dsn)) for _ in range(2)]
        self.a, self.b = (shared.SharedJobs(store) for store in self.stores)
        self.a.worker_seen('gw-1#0', version='oktell/1.2.0', catalog='x', datasets=None, records=True)

    def test_round_trip_between_two_copies(self):
        self.assertTrue(self.b.known(), 'исполнитель, записанный копией A, виден копии B')
        box = {}
        thread = threading.Thread(target=lambda: box.update(rows=self.a.run(base.query(), 10)), daemon=True)
        thread.start()
        started = time.monotonic()
        taken = self.b.take('gw-1#0', 5)
        self.assertEqual(taken[0]['dataset'], 'call_count')
        self.assertEqual(self.b.finish([{'id': taken[0]['id'], 'ok': True, 'rows': base.ROWS}]), 1)
        thread.join(10)
        self.assertEqual(box.get('rows'), base.ROWS)
        self.assertLess(time.monotonic() - started, 3, 'ответ доходит по сигналу, а не по сроку')
        with self._cursor() as cur:
            cur.execute('SELECT count(*) FROM oktell_jobs')
            self.assertEqual(cur.fetchone()[0], 0)

    def test_record_bytes_survive_the_table(self):
        box = {}
        thread = threading.Thread(target=lambda: box.update(got=self.a.fetch_record([RECORD_PATH], 10)),
                                  daemon=True)
        thread.start()
        taken = self.b.take('gw-1#0', 5)
        self.b.finish([{'id': taken[0]['id'], 'ok': True, 'found': True, 'path': RECORD_PATH,
                        'content_type': 'audio/mpeg', 'audio': base64.b64encode(AUDIO).decode()}])
        thread.join(10)
        self.assertEqual(box.get('got'), (AUDIO, 'audio/mpeg'))

    def test_one_job_goes_to_one_executor(self):
        for _ in range(3):
            self.stores[0].submit(uuid.uuid4().hex, 'query', {'id': 'x'}, 30)
        claims = []
        threads = [threading.Thread(target=lambda: claims.append(self.stores[i % 2].claim('gw#%d' % i)))
                   for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sum(1 for claim in claims if claim is not None), 3)

    def test_unclaimed_job_is_withdrawn_when_nobody_works(self):
        with self.assertRaises(live_jobs.Refused):
            self.a.run(base.query(), 5)
        with self._cursor() as cur:
            cur.execute('SELECT count(*) FROM oktell_jobs')
            self.assertEqual(cur.fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
