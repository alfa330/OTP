# -*- coding: utf-8 -*-
"""«Термокороба» — живой канал открытого экрана (24.11.2026: «читать раз в минуту и обновлять»).

Сервер читает Google-таблицу раз в минуту; после каждого чтения открытый экран узнаёт об этом сразу, а не через
свой опрос раз в 30 с (thermoboxes/live.py, src/components/thermoboxes/sheetPulse.js).

Что сторожится:
  * каждый итог чтения (queries.save_sync_state — плановое, «Обновить из таблицы», ошибка) будит экраны: NOTIFY в
    той же транзакции, после записи — уйдёт при коммите, у отката не уйдёт;
  * хаб: тычок будит ждущий поток, пачка тычков — одно пробуждение, тишина — пульс; места под потоки ограничены;
    пачка NOTIFY с соединения — один тычок;
  * поток /api/thermoboxes/stream: только тем, кому открыт раздел; без канала, без подключённого слушателя и без мест
    — 503 с Retry-After (экран остаётся на опросе); кадры connected → source по тычку → ping в тишине → bye по сроку;
    место возвращается ровно один раз — закрыл ли сервер ответ, бросил ли его, не начав или на середине;
  * слушатель: признак «подключён» для ручки, пауза перед переподключением растёт, пока он падает сразу;
  * проводка: монолит отдаёт ручке соединение для LISTEN (с tcp_user_timeout) и потолок THERMOBOX_STREAM_LIMIT.

На настоящем PostgreSQL (THERMO_TEST_PORT — порт кластера, иначе пропуск; в CI базы нет): тычок приходит слушателю
только после коммита записи состояния, откат не будит никого, слушатель live.py поднимает тычок в хабе и признак
«подключён». Канал на время теста свой — чужие NOTIFY той же базы проверкам не мешают.

    THERMO_TEST_PORT=5432 python -m pytest tests/test_thermoboxes_live.py
"""

import gc
import os
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from thermoboxes import access, live, queries, routes, schema  # noqa: E402
from tests import source_cache  # noqa: E402
from tests.test_thermoboxes import Flask, _Base, person  # noqa: E402


class _Recorder:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))


class SaveSyncStateWakesScreensTests(unittest.TestCase):

    def test_every_state_write_ends_with_a_notify(self):
        for kwargs in ({'status': 'ok', 'read': True, 'changed': True},   # цифры поменялись
                       {'status': 'ok', 'read': True},                    # прочитан без изменений — «Сверено …»
                       {'status': 'error', 'error': 'Нет доступа'}):      # плашка ошибки — сразу
            cursor = _Recorder()
            queries.save_sync_state(cursor, **kwargs)
            self.assertEqual(len(cursor.calls), 2, kwargs)
            self.assertTrue(cursor.calls[0][0].startswith('INSERT INTO thermobox_sync'), kwargs)
            self.assertEqual(cursor.calls[1], ("SELECT pg_notify(%s, '')", (live.CHANNEL,)), kwargs)

    def test_channel_is_one_word(self):
        # LISTEN CHANNEL собирается строкой — имя без кавычек и пробелов.
        self.assertRegex(live.CHANNEL, r'^[a-z_]+$')


class HubTests(unittest.TestCase):

    def test_publish_wakes_a_waiting_stream(self):
        hub = live.Hub()
        result = {}

        def waiter():
            result['value'] = hub.wait(0, 5)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)
        started = time.monotonic()
        hub.publish()
        thread.join(2)
        self.assertEqual(result['value'], (True, 1))
        self.assertLess(time.monotonic() - started, 1)

    def test_silence_is_a_heartbeat_and_a_burst_is_one_wake(self):
        hub = live.Hub()
        self.assertEqual(hub.wait(0, 0.05), (False, 0))
        for _ in range(3):
            hub.publish()
        self.assertEqual(hub.wait(0, 1), (True, 3))
        self.assertEqual(hub.wait(3, 0.01), (False, 3))
        # Тычок, пришедший раньше, чем поток снова встал ждать, не теряется.
        hub.publish()
        self.assertEqual(hub.wait(3, 0), (True, 4))

    def test_stream_slots_are_limited_and_returned(self):
        hub = live.Hub()
        self.assertTrue(hub.acquire(2))
        self.assertTrue(hub.acquire(2))
        self.assertFalse(hub.acquire(2))
        hub.release()
        self.assertTrue(hub.acquire(2))
        for _ in range(5):
            hub.release()
        self.assertEqual(hub.streams, 0)

    def test_listening_flag_is_awaited_by_the_first_stream(self):
        hub = live.Hub()
        self.assertFalse(hub.wait_listening(0.05))
        threading.Timer(0.05, hub.set_listening, args=(True,)).start()
        self.assertTrue(hub.wait_listening(2))
        hub.set_listening(False)
        self.assertFalse(hub.wait_listening(0))

    def test_retry_pause_grows_while_the_listener_falls_at_once(self):
        delays, delay = [], live.LISTENER_RETRY_SECONDS
        for _ in range(7):
            delay = live.next_retry_delay(delay, 0.1)
            delays.append(delay)
        self.assertEqual(delays, [4, 8, 16, 32, 60, 60, 60])
        self.assertEqual(live.next_retry_delay(60, live.LISTENER_HEALTHY_SECONDS), live.LISTENER_RETRY_SECONDS,
                         'долго прожившее соединение упало — сбой разовый, переподключиться быстро')

    def test_listener_that_cannot_connect_is_not_listening(self):
        hub = live.Hub()
        stop = threading.Event()
        attempts = []

        def connect():
            attempts.append(1)
            stop.set()
            raise RuntimeError('too many clients already')

        with mock.patch.object(live.log, 'exception'):
            live._run_listener(connect, hub, stop=stop)
        self.assertEqual((attempts, hub.listening, hub.seq), ([1], False, 0), 'без LISTEN — ни признака, ни тычка')

    def test_a_batch_of_notifies_is_one_tick(self):
        hub = live.Hub()

        class Conn:
            notifies = [object(), object(), object()]

        conn = Conn()
        live._drain(conn, hub)
        self.assertEqual((hub.seq, conn.notifies), (1, []))
        live._drain(conn, hub)
        self.assertEqual(hub.seq, 1)


@unittest.skipIf(Flask is None, 'Flask не установлен')
class StreamRouteTests(_Base):
    """Поток экрана — на подменённом слое запросов, с отдельным хабом и короткими сроками."""

    mirror = True

    def setUp(self):
        super().setUp()
        self.hub = live.Hub()
        self.hub.set_listening(True)
        for target, name, value in ((live, 'hub', self.hub), (live, 'ensure_listener', lambda connect: None),
                                    (live, 'HEARTBEAT_SECONDS', 0.05), (live, 'STREAM_MAX_SECONDS', 0.4),
                                    (live, 'LISTEN_WAIT_SECONDS', 0.05)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask('live')
        app.register_blueprint(routes.build_thermoboxes_blueprint(
            db=self.db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.viewer['user_id'], None, None),
            listen_connect=lambda: None,
            stream_limit=2,
        ))
        app.config['TESTING'] = True
        self.live_app = app
        self.live_client = app.test_client()

    def test_only_for_those_who_see_the_section(self):
        self.viewer = person(role='operator', department_code='hr')
        response = self.live_client.get('/api/thermoboxes/stream')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.hub.streams, 0)

    def test_without_a_channel_the_screen_stays_on_polling(self):
        # _Base собирает ручки без listen_connect — как юнит-тесты и стенд без базы.
        response = self.client.get('/api/thermoboxes/stream')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'THERMO_LIVE_OFF')
        with mock.patch.object(access, 'SHEET_MIRROR', False):
            response = self.live_client.get('/api/thermoboxes/stream')
        self.assertEqual((response.status_code, response.get_json()['code']), (503, 'THERMO_LIVE_OFF'))
        self.assertEqual(self.hub.streams, 0)

    def test_listener_down_is_503_and_the_screen_stays_on_polling(self):
        # Поток без слушателя только пинговал бы, а экран считал бы себя живым и не опрашивал сервер.
        self.hub.set_listening(False)
        response = self.live_client.get('/api/thermoboxes/stream')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'THERMO_LIVE_OFF')
        self.assertEqual(response.headers['Retry-After'], '60')
        self.assertEqual(self.hub.streams, 0)

    def test_no_free_slot_is_503_with_retry_after(self):
        self.assertTrue(self.hub.acquire(2) and self.hub.acquire(2))
        response = self.live_client.get('/api/thermoboxes/stream')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'THERMO_LIVE_BUSY')
        self.assertEqual(response.headers['Retry-After'], '120')
        self.assertEqual(self.hub.streams, 2, 'отказ места не берёт')

    def test_frames_connected_source_ping_bye_and_the_slot_comes_back(self):
        # Чужое место — другой открытый экран: поток должен вернуть ровно своё, а не два.
        self.assertTrue(self.hub.acquire(2))
        response = self.live_client.get('/api/thermoboxes/stream', buffered=False)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.mimetype.startswith('text/event-stream'))
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')
        self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
        self.assertEqual(self.hub.streams, 2)
        frames = iter(response.response)
        text = lambda chunk: chunk.decode() if isinstance(chunk, bytes) else chunk  # noqa: E731
        self.assertTrue(text(next(frames)).startswith(': connected '))
        self.hub.publish()
        self.assertEqual(text(next(frames)), 'event: source\ndata: {}\n\n')
        self.assertTrue(text(next(frames)).startswith(': ping '), 'тишина — пульс, а не событие')
        rest = [text(chunk) for chunk in frames]
        self.assertEqual(rest[-1], 'event: bye\ndata: {}\n\n', 'поток живёт не дольше STREAM_MAX_SECONDS')
        self.assertTrue(all(chunk.startswith(': ping ') for chunk in rest[:-1]))
        self.assertEqual(self.hub.streams, 1, 'поток дошёл до bye — своё место вернул')
        response.close()
        self.assertEqual(self.hub.streams, 1, 'закрытие ответа второй раз место не возвращает')

    def _wsgi_stream(self):
        # Прямо на уровне WSGI: тестовый клиент werkzeug при buffered=False сам вытягивает первый кадр, а здесь
        # важно, как поведёт себя сервер, который ответ не начинал читать или бросил его.
        from werkzeug.test import EnvironBuilder

        environ = EnvironBuilder(path='/api/thermoboxes/stream').get_environ()
        return self.live_app.wsgi_app(environ, lambda status, headers, exc_info=None: None)

    def test_closed_before_the_first_frame_still_returns_the_slot(self):
        # Сервер закрыл ответ, ни разу его не прочитав (клиент ушёл сразу): finally генератора не выполнится —
        # место возвращает call_on_close.
        app_iter = self._wsgi_stream()
        self.assertEqual(self.hub.streams, 1)
        app_iter.close()
        self.assertEqual(self.hub.streams, 0)

    def test_server_dropping_the_stream_without_close_still_returns_the_slot(self):
        # Dev-сервер werkzeug при сброшенном клиентом соединении падает в своём finally раньше, чем закрывает ответ:
        # close() не зовётся, call_on_close не срабатывает. Место возвращает finally самого генератора.
        self.assertTrue(self.hub.acquire(2))   # другой экран
        app_iter = self._wsgi_stream()
        frames = iter(app_iter)
        first = next(frames)
        self.assertTrue((first.decode() if isinstance(first, bytes) else first).startswith(': connected '))
        self.assertEqual(self.hub.streams, 2)
        del frames, app_iter
        gc.collect()
        self.assertEqual(self.hub.streams, 1)


class WiringTests(unittest.TestCase):

    def test_monolith_gives_the_stream_a_listen_connection_and_a_ceiling(self):
        source = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        block = source.split('app.register_blueprint(build_thermoboxes_blueprint(')[1].split('\n    ))\n')[0]
        self.assertIn("listen_connect=lambda: psycopg2.connect(**{**_build_postgres_connection_params(), "
                      "'tcp_user_timeout': 30000})", block)
        self.assertIn("stream_limit=_env_int('THERMOBOX_STREAM_LIMIT', 10, minimum=2, maximum=WAITRESS_THREADS)",
                      block)

    def test_stream_route_is_in_the_realtime_container(self):
        caddy = (ROOT / 'deploy' / 'yandex' / 'Caddyfile').read_text(encoding='utf-8')
        compose = (ROOT / 'deploy' / 'yandex' / 'docker-compose.yml').read_text(encoding='utf-8')
        self.assertIn('/api/thermoboxes/stream', caddy)
        realtime = compose[compose.index('  realtime:'):compose.index('  worker:')]
        self.assertIn('THERMOBOX_STREAM_LIMIT: "20"', realtime)


PORT = os.environ.get('THERMO_TEST_PORT')


@unittest.skipIf(not PORT, 'нужен локальный PostgreSQL (THERMO_TEST_PORT)')
class PostgresNotifyTests(unittest.TestCase):
    """Своя схема на время теста; канал общий на базу — слушатель ждёт только своих тычков."""

    def connect(self):
        import psycopg2

        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('THERMO_TEST_USER', 'postgres'),
                                password=os.environ.get('THERMO_TEST_PASSWORD', ''),
                                dbname=os.environ.get('THERMO_TEST_DB', 'postgres'))
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema_name)
        conn.commit()
        return conn

    def setUp(self):
        self.schema_name = 'thermo_live_' + uuid.uuid4().hex[:10]
        # Свой канал: канал общий на базу, а тычок без данных — чужой NOTIFY thermobox_live (стенд, параллельный
        # прогон) уронил бы проверки «до коммита пусто» и «откат пуст».
        channel = mock.patch.object(live, 'CHANNEL', 'thermobox_live_' + ''.join(
            chr(ord('a') + int(digit, 16) % 26) for digit in uuid.uuid4().hex[:12]))
        channel.start()
        self.addCleanup(channel.stop)
        import psycopg2

        admin = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('THERMO_TEST_USER', 'postgres'),
                                 password=os.environ.get('THERMO_TEST_PASSWORD', ''),
                                 dbname=os.environ.get('THERMO_TEST_DB', 'postgres'))
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + self.schema_name)
            cur.execute('SET search_path TO ' + self.schema_name)
            ddl = next(statement for statement in schema._STATEMENTS if 'CREATE TABLE IF NOT EXISTS thermobox_sync' in statement)
            cur.execute(ddl)
        self.addCleanup(self._drop, admin)
        self.listener = self.connect()
        self.listener.autocommit = True
        with self.listener.cursor() as cur:
            cur.execute('LISTEN ' + live.CHANNEL)
        self.addCleanup(self.listener.close)

    def _drop(self, admin):
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS %s CASCADE' % self.schema_name)
        admin.close()

    def pokes(self):
        time.sleep(0.3)
        self.listener.poll()
        got = [note for note in self.listener.notifies if note.channel == live.CHANNEL]
        del self.listener.notifies[:]
        return got

    def test_notify_arrives_only_after_commit(self):
        writer = self.connect()
        self.addCleanup(writer.close)
        with writer.cursor() as cur:
            queries.save_sync_state(cur, status='ok', read=True, changed=True)
            self.assertEqual(self.pokes(), [], 'до коммита экранам будить не о чем — записи ещё не видно')
        writer.commit()
        self.assertEqual(len(self.pokes()), 1)
        with writer.cursor() as cur:
            cur.execute('SELECT status, read_at IS NOT NULL, changed_at IS NOT NULL FROM thermobox_sync')
            self.assertEqual(cur.fetchone(), ('ok', True, True))

    def test_rolled_back_write_wakes_nobody(self):
        writer = self.connect()
        self.addCleanup(writer.close)
        with writer.cursor() as cur:
            queries.save_sync_state(cur, status='error', error='Нет доступа')
        writer.rollback()
        self.assertEqual(self.pokes(), [])

    def test_listener_thread_turns_a_commit_into_a_hub_tick(self):
        hub = live.Hub()
        stop = threading.Event()
        heartbeat = mock.patch.object(live, 'HEARTBEAT_SECONDS', 0.2)
        heartbeat.start()
        self.addCleanup(heartbeat.stop)
        thread = threading.Thread(target=live._run_listener, args=(self.connect, hub, stop), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(stop.set)
        self.assertTrue(hub.wait_listening(5), 'после LISTEN ручка потока пускает экраны')
        # Подключение слушателя — сам по себе тычок «сверьтесь»: пока LISTEN лежал, тычки не копились.
        woke, seq = hub.wait(0, 5)
        self.assertTrue(woke)
        writer = self.connect()
        self.addCleanup(writer.close)
        with writer.cursor() as cur:
            queries.save_sync_state(cur, status='ok', read=True)
        writer.commit()
        woke, after = hub.wait(seq, 5)
        self.assertTrue(woke)
        self.assertGreater(after, seq)
        stop.set()
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'слушатель останавливается и закрывает соединение')
        self.assertFalse(hub.listening)


if __name__ == '__main__':
    unittest.main()
