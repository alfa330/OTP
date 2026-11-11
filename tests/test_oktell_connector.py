# -*- coding: utf-8 -*-
"""Коннектор Oktell на VM шлюза (cdr_bridge/oktell_connector.py, cdr_bridge/oktell_db.py).

Портал здесь настоящий: поддельная сессия коннектора отдаёт его запросы ручке
`/api/oktell_live/agent/push` во Flask — подпись проверяется, строки чистятся, снимок ложится
в память, ответ возвращается коннектору. База Oktell — двойник.

Что закреплено:
  * первый запрос — за параметрами: без них базу не трогаем, и снимок сразу снят с реестром
    тестовых номеров;
  * в базу уходит ровно запрос общего модуля; текст, присланный порталом, не исполняется;
  * база легла — портал узнаёт причину, повтор идёт с растущей паузой, при отказе учётки —
    раз в пять минут; портал лёг — пауза, и базу в это время не дёргаем каждый такт;
  * параметры сменились — снимок переснимается сразу;
  * пульс пишется только после принятого снимка;
  * соединение с базой: autocommit, шифрование, только чтение, без блокировок чтения, таймауты,
    вежливые настройки сессии; значения колонок уходят в JSON простыми.
"""
import decimal
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import date, datetime
from unittest import mock

import requests
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import oktell_wallboard  # noqa: E402
from cdr_bridge import oktell_connector, oktell_db, signing  # noqa: E402
from oktell_live import routes as live_routes, store as live_store  # noqa: E402

PRIVATE_B64, PUBLIC_B64, KEY_ID = signing.generate()
TEST_KEY = '7000000101'

TOTALS = {
    'oktell_now': '2026-11-10 22:00:00', 'queue_now': 1, 'queue_max_wait_seconds': 9,
    'talking_now': 3, 'arrived': 120, 'served': 110, 'lost': 10, 'greet_drop': 4,
    'served_sl': 100, 'wait_seconds': 900.5, 'wait_served_seconds': 700.25,
    'max_wait_seconds': 88.1, 'talk_seconds': 20000,
}


def db_rows():
    return [dict(TOTALS, operator_name='Оператор Один', state=1, icode=-1,
                 since='2026-11-10 21:50:00', in_state_seconds=600),
            dict(TOTALS, operator_name='Оператор Два', state=5, icode=-1,
                 since='2026-11-10 21:59:00', in_state_seconds=60)]


class Clock:
    def __init__(self, now=2_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeDb:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else db_rows()
        self.error = None
        self.queries = []
        self.connected = False
        self.closed = 0
        self.permissions = []
        self.permissions_error = None
        self.permission_checks = 0

    def describe(self):
        return '10.0.0.24:1433/oktell'

    def query(self, sql):
        self.queries.append(sql)
        if self.error is not None:
            self.connected = False
            raise self.error
        self.connected = True
        return [dict(row) for row in self.rows]

    def write_permissions(self):
        self.permission_checks += 1
        if self.permissions_error is not None:
            raise self.permissions_error
        return list(self.permissions)

    def close(self):
        self.closed += 1
        self.connected = False


class _Response:
    def __init__(self, flask_response):
        self.status_code = flask_response.status_code
        self.text = flask_response.get_data(as_text=True)

    def json(self):
        return json.loads(self.text)


class PortalSession:
    """Сессия коннектора, за которой стоит настоящая ручка портала."""

    def __init__(self, client):
        self.client = client
        self.posts = []
        self.down = None
        self.drop_next = None
        self.reply_override = None

    def post(self, url, data=None, timeout=None, headers=None):
        self.posts.append({'url': url, 'body': json.loads(data.decode('utf-8')), 'headers': dict(headers or {})})
        if self.drop_next is not None:
            error, self.drop_next = self.drop_next, None
            raise error
        if self.down is not None:
            raise self.down
        path = url.split('://', 1)[1].split('/', 1)[1]
        response = _Response(self.client.post('/' + path, data=data, headers=headers))
        if self.reply_override is not None and response.status_code == 200:
            body = json.loads(response.text)
            body.update(self.reply_override)
            response.text = json.dumps(body)
        return response


class Harness(unittest.TestCase):

    def setUp(self):
        self.portal_clock = Clock()
        self.store = live_store.Store(clock=self.portal_clock)
        self.portal_params = {'sl_seconds': 20, 'test_keys': [TEST_KEY]}
        app = Flask(__name__)
        app.register_blueprint(live_routes.build_oktell_live_blueprint(
            build_cors_preflight_response=lambda: ('', 204),
            wallboard_params=lambda: self.portal_params,
            store=self.store))
        patcher = mock.patch.dict(os.environ, {'CDR_AGENT_KEYS': PUBLIC_B64}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (live_store.ENABLED_ENV, live_store.MAX_AGE_ENV, live_store.INTERVAL_ENV):
            os.environ.pop(name, None)
        self.session = PortalSession(app.test_client())
        self.db = FakeDb()
        self.tmp = tempfile.mkdtemp(prefix='otp-oktell-')
        self.heartbeat = os.path.join(self.tmp, 'heartbeat')
        self.connector = self.make()

    def make(self, **config):
        base = {'portal': 'https://portal.example', 'key_file': '', 'private_key': PRIVATE_B64,
                'proxy': '', 'ca_bundle': '', 'interval': 10.0, 'heartbeat_file': self.heartbeat}
        base.update(config)
        return oktell_connector.Connector(base, session=self.session, db=self.db, clock=Clock())


class CycleTests(Harness):

    def test_first_cycle_asks_params_then_delivers_a_snapshot(self):
        wait = self.connector.cycle()
        self.assertEqual(len(self.session.posts), 2)
        hello, push = self.session.posts
        self.assertIsNone(hello['body']['wallboard'], 'первый запрос — без снимка, за параметрами')
        self.assertEqual(self.db.queries, [oktell_wallboard.snapshot_sql(20, [TEST_KEY])])
        self.assertEqual(push['body']['wallboard']['params'], {'sl_seconds': 20, 'test_keys': [TEST_KEY]})
        self.assertEqual(len(push['body']['wallboard']['rows']), 2)
        got = self.store.wallboard_rows(20)
        self.assertEqual([row['operator_name'] for row in got['rows']], ['Оператор Один', 'Оператор Два'])
        self.assertTrue(9.0 < wait <= 10.0)
        self.assertTrue(os.path.exists(self.heartbeat))

    def test_start_raises_no_false_alarm_about_the_database(self):
        """Первый запрос уходит до первого чтения базы: «не читается» в нём было бы ложной
        тревогой на портале при каждом запуске коннектора."""
        with self.assertNoLogs(live_store.log, level='WARNING'):
            self.connector.cycle()
        hello, push = self.session.posts
        self.assertIsNone(hello['body']['db']['connected'], 'ещё не пробовали — не «лежит»')
        self.assertIs(push['body']['db']['connected'], True)

    def test_next_cycles_send_one_request_each(self):
        self.connector.cycle()
        self.connector.cycle()
        self.connector.cycle()
        self.assertEqual(len(self.session.posts), 4, 'параметры спрашиваются один раз')
        self.assertEqual(len(self.db.queries), 3)

    def test_requests_are_signed_for_this_route(self):
        self.connector.cycle()
        for post in self.session.posts:
            self.assertTrue(post['url'].endswith('/api/oktell_live/agent/push'))
            self.assertEqual(post['headers']['X-Agent-Key'], KEY_ID)
            self.assertIn('X-Agent-Signature', post['headers'])
        self.assertEqual(self.store.status()['agent']['key_id'], KEY_ID)

    def test_payload_carries_no_credentials(self):
        connector = self.make(sql_user='reader', sql_password='s3cret-пароль')
        connector.cycle()
        dumped = json.dumps([post['body'] for post in self.session.posts], ensure_ascii=False)
        self.assertNotIn('s3cret', dumped)
        self.assertNotIn('reader', dumped)
        self.assertEqual(self.session.posts[-1]['body']['db'],
                         {'host': '10.0.0.24:1433/oktell', 'connected': True, 'error': None})
        self.assertRegex(self.session.posts[-1]['body']['version'], r'^oktell/\d+\.\d+\.\d+/\d+\.\d+')

    def test_interval_is_set_by_the_portal_within_bounds(self):
        with mock.patch.dict(os.environ, {live_store.INTERVAL_ENV: '5'}):
            wait = self.connector.cycle()
        self.assertEqual(self.connector.interval, 5.0)
        self.assertTrue(4.0 < wait <= 5.0)
        self.session.reply_override = {'interval_seconds': 0.01}
        self.connector.cycle()
        self.assertEqual(self.connector.interval, oktell_connector.MIN_INTERVAL_SECONDS,
                         'чаще нижней границы базу не опрашиваем, что бы ни назвал портал')
        self.session.reply_override = {'interval_seconds': 10 ** 6}
        self.connector.cycle()
        self.assertEqual(self.connector.interval, oktell_connector.MAX_INTERVAL_SECONDS)
        self.session.reply_override = {'interval_seconds': 'часто'}
        self.connector.cycle()
        self.assertEqual(self.connector.interval, oktell_connector.MAX_INTERVAL_SECONDS,
                         'мусор вместо такта — такт прежний')


class TrustTests(Harness):
    """Портал — доверенная сторона, но не безусловно."""

    def test_sql_from_the_portal_is_never_executed(self):
        self.session.reply_override = {
            'sql': 'DROP TABLE oktell.dbo.Call_Systems_hst',
            'query': "SELECT * FROM oktell_settings.dbo.A_Users",
            'params': {'sl_seconds': "20; DROP TABLE x", 'sql': 'DELETE FROM x',
                       'test_keys': [TEST_KEY, "1' OR 1=1 --", "'; EXEC xp_cmdshell 'dir' --"]},
        }
        self.connector.cycle()
        self.connector.cycle()
        expected = oktell_wallboard.snapshot_sql(20, [TEST_KEY])
        self.assertEqual(set(self.db.queries), {expected})
        for sql in self.db.queries:
            self.assertNotIn('DROP', sql)
            self.assertNotIn('xp_cmdshell', sql)
            self.assertNotIn('A_Users', sql)
            self.assertNotIn('OR 1=1', sql)

    def test_params_are_cleaned_before_use(self):
        self.session.reply_override = {'params': {'sl_seconds': 10 ** 9, 'test_keys': 'не список'}}
        self.connector.cycle()
        self.assertEqual(self.connector.params, {'sl_seconds': 20, 'test_keys': []})

    def test_connector_source_has_no_way_to_run_foreign_sql(self):
        """Сторож по тексту: в базу уходит только то, что построил общий модуль."""
        source = open(oktell_connector.__file__, encoding='utf-8').read()
        self.assertEqual(source.count('self.db.query('), 1)
        self.assertIn('sql = oktell_wallboard.snapshot_sql(', source)
        self.assertIn('rows = self.db.query(sql)', source)


class ParamsChangeTests(Harness):

    def test_changed_params_retake_the_snapshot_at_once(self):
        self.connector.cycle()
        self.portal_params = {'sl_seconds': 20, 'test_keys': [TEST_KEY, '7012223344']}
        wait = self.connector.cycle()
        self.assertEqual(wait, 0.0, 'реестр тестовых номеров изменился — не ждём такт')
        self.assertEqual(self.connector.params['test_keys'], [TEST_KEY, '7012223344'])
        self.assertEqual(self.store.status()['wallboard']['params']['test_keys'], [TEST_KEY],
                         'снимок со старым реестром портал не принял — на табло прежний')
        wait = self.connector.cycle()
        self.assertGreater(wait, 9.0)
        self.assertEqual(self.db.queries[-1], oktell_wallboard.snapshot_sql(20, [TEST_KEY, '7012223344']))
        self.assertEqual(self.store.status()['wallboard']['params']['test_keys'], [TEST_KEY, '7012223344'])

    def test_refusal_without_new_params_does_not_spin(self):
        """Портал не принял снимок, а параметры те же — ждём такт, а не крутимся."""
        self.connector.cycle()
        self.session.reply_override = {'accepted': False, 'reason': 'что-то не так'}
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            wait = self.connector.cycle()
        self.assertGreater(wait, 9.0)


class DbFailureTests(Harness):

    def test_db_failure_is_reported_and_backs_off(self):
        self.connector.cycle()
        self.portal_clock.now += 5
        self.db.error = oktell_db.OktellDbError('запрос к базе Oktell не выполнен: Login timeout expired')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            waits = [self.connector.cycle() for _ in range(5)]
        self.assertEqual(waits, [5.0, 15.0, 30.0, 60.0, 60.0])
        last = self.session.posts[-1]['body']
        self.assertIsNone(last['wallboard'])
        self.assertFalse(last['db']['connected'])
        self.assertIn('Login timeout expired', last['db']['error'])
        self.assertIn('Login timeout expired', self.store.silence_note())

    def test_rejected_login_is_retried_slowly(self):
        """Каждая попытка входа — шаг к блокировке учётки политикой SQL Server."""
        self.connector.cycle()
        self.db.error = oktell_db.OktellDbError('вход в базу Oktell не удался: Login failed', auth=True)
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            self.assertEqual(self.connector.cycle(), float(oktell_connector.DB_AUTH_BACKOFF_SECONDS))
        self.assertGreaterEqual(oktell_connector.DB_AUTH_BACKOFF_SECONDS, 300)

    def test_recovery_resets_the_backoff_and_the_error(self):
        self.connector.cycle()
        self.db.error = oktell_db.OktellDbError('обрыв')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            self.connector.cycle()
            self.connector.cycle()
        self.db.error = None
        with self.assertLogs(oktell_connector.log, level='INFO'):
            wait = self.connector.cycle()
        self.assertGreater(wait, 9.0)
        self.assertEqual(self.connector.db_failures, 0)
        self.assertIsNone(self.session.posts[-1]['body']['db']['error'])
        self.assertTrue(self.session.posts[-1]['body']['db']['connected'])

    def test_empty_answer_is_a_failure_not_a_quiet_night(self):
        """Запрос всегда отдаёт строку итогов: пустой ответ — поломка, а не «никого на линии»."""
        self.connector.cycle()
        self.db.rows = []
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            wait = self.connector.cycle()
        self.assertEqual(wait, 5.0)
        self.assertIsNone(self.session.posts[-1]['body']['wallboard'])

    def test_heartbeat_only_after_an_accepted_snapshot(self):
        self.db.error = oktell_db.OktellDbError('обрыв')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            self.connector.cycle()
        self.assertFalse(os.path.exists(self.heartbeat), 'без снимка коннектор здоровым не считается')
        self.db.error = None
        self.connector.cycle()
        self.assertTrue(os.path.exists(self.heartbeat))


class PortalFailureTests(Harness):

    def test_portal_down_at_start_keeps_the_database_untouched(self):
        self.session.down = ConnectionError('portal is down')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            waits = [self.connector.cycle() for _ in range(6)]
        self.assertEqual(waits, [2.0, 5.0, 15.0, 30.0, 60.0, 60.0])
        self.assertEqual(self.db.queries, [], 'пока портал не назвал параметры, базу не трогаем')

    def test_portal_down_later_backs_off_and_recovers(self):
        self.connector.cycle()
        queries = len(self.db.queries)
        self.session.down = ConnectionError('502')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            self.assertEqual(self.connector.cycle(), 2.0)
            self.assertEqual(self.connector.cycle(), 5.0)
        self.assertEqual(len(self.db.queries), queries + 2, 'по одному запросу на паузу, не чаще')
        self.session.down = None
        with self.assertLogs(oktell_connector.log, level='INFO'):
            self.assertGreater(self.connector.cycle(), 9.0)
        self.assertEqual(self.connector.portal_failures, 0)

    def test_dropped_keepalive_is_retried_once_with_a_fresh_signature(self):
        """Портал закрыл простаивающее соединение — это не отказ: повтор сразу, и подпись новая
        (одноразовый номер второй раз портал не примет)."""
        self.connector.cycle()
        before = len(self.session.posts)
        self.session.drop_next = requests.exceptions.ConnectionError('Remote end closed connection')
        wait = self.connector.cycle()
        self.assertGreater(wait, 9.0, 'такт не потерян')
        self.assertEqual(self.connector.portal_failures, 0)
        first, second = self.session.posts[before], self.session.posts[before + 1]
        self.assertEqual(first['body'], second['body'], 'тот же снимок')
        self.assertNotEqual(first['headers']['X-Agent-Nonce'], second['headers']['X-Agent-Nonce'])
        self.assertEqual(len(self.session.posts), before + 2)

    def test_second_connection_failure_is_a_real_outage(self):
        self.connector.cycle()
        before = len(self.session.posts)
        self.session.down = requests.exceptions.ConnectionError('connection refused')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            self.assertEqual(self.connector.cycle(), 2.0)
        self.assertEqual(len(self.session.posts), before + 2, 'один повтор, не больше')

    def test_timeout_is_not_retried_at_once(self):
        """Портал принял запрос и думает — второй такой же ему не поможет."""
        self.connector.cycle()
        before = len(self.session.posts)
        self.session.down = requests.exceptions.ConnectTimeout('timed out')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            self.connector.cycle()
        self.assertEqual(len(self.session.posts), before + 1)
        self.session.down = requests.exceptions.ReadTimeout('read timed out')
        self.connector.cycle()
        self.assertEqual(len(self.session.posts), before + 2)

    def test_unauthorized_reply_names_the_key(self):
        with mock.patch.dict(os.environ, {'CDR_AGENT_KEYS': signing.generate()[1]}):
            with self.assertLogs(oktell_connector.log, level='WARNING') as captured:
                self.connector.cycle()
        self.assertIn(KEY_ID, captured.output[0])
        self.assertIn('CDR_AGENT_KEYS', captured.output[0])

    def test_connector_without_a_key_does_not_post(self):
        connector = self.make(private_key='')
        with self.assertLogs(oktell_connector.log, level='WARNING') as captured:
            connector.cycle()
        self.assertEqual(self.session.posts, [])
        self.assertIn('ключа подписи', captured.output[0])


class ReadOnlyAccountTests(Harness):
    """Учётке базы на шлюзе положено только читать: при захвате контейнера она утекает."""

    def test_privileges_are_checked_once_after_the_first_accepted_snapshot(self):
        with self.assertLogs(oktell_connector.log, level='INFO') as captured:
            self.connector.cycle()
            self.connector.cycle()
            self.connector.cycle()
        self.assertEqual(self.db.permission_checks, 1, 'раз за жизнь процесса, а не каждый такт')
        self.assertTrue(any('только чтение' in line for line in captured.output))

    def test_privileged_account_is_reported_loudly_but_work_goes_on(self):
        self.db.permissions = ['администратор сервера', 'INSERT']
        with self.assertLogs(oktell_connector.log, level='WARNING') as captured:
            wait = self.connector.cycle()
        self.assertGreater(wait, 9.0, 'табло важнее: работа не останавливается')
        self.assertIn('администратор сервера, INSERT', captured.output[0])
        self.assertIn('только на чтение', captured.output[0])
        self.assertIsNotNone(self.store.wallboard_rows(20))

    def test_failed_privilege_check_does_not_break_the_cycle(self):
        self.db.permissions_error = oktell_db.OktellDbError('обрыв')
        with self.assertLogs(oktell_connector.log, level='WARNING'):
            wait = self.connector.cycle()
        self.assertGreater(wait, 9.0)
        self.assertEqual(self.connector.cycle() > 9.0, True)
        self.assertEqual(self.db.permission_checks, 1)

    def test_check_names_what_the_account_can_do(self):
        self.db.permissions = ['администратор сервера']
        out = io.StringIO()
        with redirect_stdout(out):
            code = oktell_connector.check(self.connector)
        self.assertEqual(code, 0, 'без флага — только сказано')
        self.assertIn('умеет больше чтения: администратор сервера', out.getvalue())

    def test_deploy_check_refuses_a_privileged_account(self):
        """Выкладка на шлюз зовёт проверку с --require-readonly и останавливается на отказе."""
        self.db.permissions = ['UPDATE']
        out = io.StringIO()
        with redirect_stdout(out):
            code = oktell_connector.check(self.connector, require_readonly=True)
        self.assertEqual(code, 1)
        self.db.permissions = []
        out = io.StringIO()
        with redirect_stdout(out):
            code = oktell_connector.check(self.connector, require_readonly=True)
        self.assertEqual(code, 0)
        self.assertIn('только чтение', out.getvalue())


class RunLoopTests(Harness):

    def test_run_stops_on_request_and_closes_the_database(self):
        calls = []

        def cycle():
            calls.append(1)
            if len(calls) == 3:
                self.connector.stop()
            return 0.0

        self.connector.cycle = cycle
        with self.assertLogs(oktell_connector.log, level='INFO'):
            self.assertEqual(self.connector.run(), 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.db.closed, 1)

    def test_a_crashed_cycle_does_not_kill_the_loop(self):
        calls = []

        def cycle():
            calls.append(1)
            if len(calls) == 1:
                raise ZeroDivisionError('boom')
            self.connector.stop()
            return 0.0

        self.connector.cycle = cycle
        with mock.patch.object(oktell_connector, 'CRASH_PAUSE_SECONDS', 0.0):
            with self.assertLogs(oktell_connector.log, level='ERROR'):
                self.connector.run()
        self.assertEqual(len(calls), 2)


class CheckTests(Harness):

    def test_check_reads_the_database_and_greets_the_portal_without_a_snapshot(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = oktell_connector.check(self.connector)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn('отвечает', text)
        self.assertIn('операторов на линии 2', text)
        self.assertIn('принял', text)
        self.assertEqual(len(self.session.posts), 1)
        self.assertIsNone(self.session.posts[0]['body']['wallboard'], 'проверка снимок не отправляет')
        self.assertIsNone(self.session.posts[0]['body']['db']['connected'],
                          'проверка не меняет то, что портал знает о базе от работающего коннектора')
        self.assertIsNone(self.store.wallboard_rows(20))

    def test_check_fails_when_the_database_is_unreadable(self):
        self.db.error = oktell_db.OktellDbError('вход не удался', auth=True)
        out = io.StringIO()
        with redirect_stdout(out):
            code = oktell_connector.check(self.connector)
        self.assertEqual(code, 1)
        self.assertIn('НЕ читается', out.getvalue())


class ConfigTests(unittest.TestCase):

    def test_environment_wins_and_interval_is_clamped(self):
        env = {'CDR_BRIDGE_PORTAL': 'https://portal.example/', 'OKTELL_SQL_HOST': '10.0.0.24',
               'OKTELL_SQL_USER': 'reader', 'OKTELL_SQL_PASSWORD': 'p', 'OKTELL_SQL_PORT': '1433',
               'OKTELL_LIVE_INTERVAL_SECONDS': '1', 'OKTELL_HEARTBEAT_FILE': '/tmp/oktell-heartbeat',
               'CDR_PORTAL_PROXY': 'http://127.0.0.1:3128', 'OKTELL_SQL_DB': 'oktell'}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(oktell_connector, '_env_file_values', return_value={}):
                config = oktell_connector.load_config()
        self.assertEqual(config['portal'], 'https://portal.example')
        self.assertEqual(config['sql_host'], '10.0.0.24')
        self.assertEqual(config['sql_user'], 'reader')
        self.assertEqual(config['interval'], oktell_connector.MIN_INTERVAL_SECONDS)
        self.assertEqual(config['sql_encryption'], 'require')
        self.assertEqual(config['proxy'], 'http://127.0.0.1:3128')

    def test_main_refuses_to_start_without_database_settings(self):
        with mock.patch.object(oktell_connector, 'load_config',
                               return_value={'sql_host': '', 'sql_user': '', 'portal': 'x',
                                             'key_file': '', 'private_key': ''}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(oktell_connector.main([]), 2)
        self.assertIn('OKTELL_SQL_HOST', out.getvalue())


# ── база ─────────────────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def execute(self, sql):
        self.connection.executed.append(sql)
        if self.connection.fail is not None:
            raise self.connection.fail

    def fetchmany(self, size):
        self.connection.asked = size
        return self.connection.rows[:size]

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, rows=None, fail=None):
        self.rows = rows or []
        self.fail = fail
        self.executed = []
        self.closed = False
        self.asked = None

    def cursor(self):
        return _Cursor(self)

    def close(self):
        self.closed = True


class JsonValueTests(unittest.TestCase):

    def test_plain_values_pass_through(self):
        for value in (None, 0, 17, 'Оператор', True, 2.5):
            self.assertEqual(oktell_db.json_value(value), value)

    def test_decimal_becomes_a_number(self):
        self.assertEqual(oktell_db.json_value(decimal.Decimal('392')), 392)
        self.assertIsInstance(oktell_db.json_value(decimal.Decimal('392')), int)
        self.assertEqual(oktell_db.json_value(decimal.Decimal('5000.7')), 5000.7)
        self.assertIsNone(oktell_db.json_value(decimal.Decimal('NaN')))

    def test_dates_use_the_format_of_the_query(self):
        self.assertEqual(oktell_db.json_value(datetime(2026, 11, 10, 22, 5, 9, 123000)), '2026-11-10 22:05:09')
        self.assertEqual(oktell_db.json_value(date(2026, 11, 10)), '2026-11-10')

    def test_exotic_values_stay_serialisable(self):
        token = uuid.UUID('12345678-1234-5678-1234-567812345678')
        self.assertEqual(oktell_db.json_value(token), str(token))
        self.assertEqual(oktell_db.json_value(b'\x01\xff'), '01ff')
        self.assertIsNone(oktell_db.json_value(float('inf')))
        json.dumps([oktell_db.json_value(v) for v in (token, b'x', decimal.Decimal('1.5'), datetime.now())])


class OktellDbTests(unittest.TestCase):

    def db(self, connection=None, **kwargs):
        self.connection = connection or _Connection(rows=[
            {'oktell_now': '2026-11-10 22:00:00', 'served': decimal.Decimal('110'),
             'wait_seconds': decimal.Decimal('900.5'), 'since': datetime(2026, 11, 10, 21, 50), 0: 'лишний ключ'}])
        self.opened = []

        def connect():
            self.opened.append(1)
            return self.connection

        return oktell_db.OktellDb('10.0.0.24', 1433, 'reader', 'secret', connect=connect, **kwargs)

    def test_query_returns_json_safe_rows(self):
        db = self.db()
        rows = db.query('SELECT 1')
        self.assertEqual(rows, [{'oktell_now': '2026-11-10 22:00:00', 'served': 110,
                                 'wait_seconds': 900.5, 'since': '2026-11-10 21:50:00'}])
        json.dumps(rows)
        self.assertTrue(db.connected)

    def test_connection_is_reused(self):
        db = self.db()
        db.query('SELECT 1')
        db.query('SELECT 2')
        self.assertEqual(len(self.opened), 1, 'одно постоянное соединение, не вход на каждый запрос')
        self.assertEqual(self.connection.executed, ['SELECT 1', 'SELECT 2'])

    def test_failed_query_closes_the_connection_and_does_not_retry(self):
        db = self.db(_Connection(fail=RuntimeError('Adaptive Server connection timed out')))
        with self.assertRaises(oktell_db.OktellDbError) as caught:
            db.query('SELECT 1')
        self.assertIn('timed out', str(caught.exception))
        self.assertFalse(caught.exception.auth)
        self.assertTrue(self.connection.closed)
        self.assertFalse(db.connected)
        self.assertEqual(self.connection.executed, ['SELECT 1'], 'повтора нет: паузу держит коннектор')

    def test_row_cap_is_an_error_not_a_truncated_snapshot(self):
        rows = [{'n': index} for index in range(oktell_db.MAX_ROWS + 5)]
        db = self.db(_Connection(rows=rows))
        with self.assertRaises(oktell_db.OktellDbError) as caught:
            db.query('SELECT n')
        self.assertIn(str(oktell_db.MAX_ROWS), str(caught.exception))
        self.assertEqual(self.connection.asked, oktell_db.MAX_ROWS + 1)
        self.assertTrue(self.connection.closed)

    def test_describe_and_errors_never_show_credentials(self):
        db = self.db()
        self.assertEqual(db.describe(), '10.0.0.24:1433/oktell')
        self.assertNotIn('secret', db.describe())
        self.assertNotIn('reader', db.describe())

    def test_login_failure_is_marked_as_auth(self):
        def connect():
            raise RuntimeError((18456, b"Login failed for user 'reader'."))

        db = oktell_db.OktellDb('h', 1433, 'reader', 'secret', connect=connect)
        with self.assertRaises(oktell_db.OktellDbError) as caught:
            db.query('SELECT 1')
        self.assertTrue(caught.exception.auth)
        self.assertNotIn('secret', str(caught.exception))

    def test_network_failure_is_not_auth(self):
        def connect():
            raise RuntimeError((20009, b'Unable to connect: Adaptive Server is unavailable'))

        db = oktell_db.OktellDb('h', 1433, 'reader', 'secret', connect=connect)
        with self.assertRaises(oktell_db.OktellDbError) as caught:
            db.query('SELECT 1')
        self.assertFalse(caught.exception.auth)

    def test_unconfigured_database_is_disabled(self):
        db = oktell_db.OktellDb('', 1433, '', '')
        self.assertFalse(db.enabled)
        with self.assertRaises(oktell_db.OktellDbError):
            db.query('SELECT 1')

    def test_driver_connection_is_polite_and_read_only(self):
        """Чем соединение обязано быть на боевой базе телефонии — см. шапку oktell_db.py."""
        captured = {}

        class FakeDriver:
            @staticmethod
            def connect(**kwargs):
                captured.update(kwargs)
                return _Connection(rows=[{'n': 1}])

        with mock.patch.object(oktell_db, 'pymssql', FakeDriver):
            db = oktell_db.OktellDb('10.0.0.24', '1433', 'reader', 'secret')
            self.assertTrue(db.enabled)
            db.query('SELECT 1 AS n')
        self.assertTrue(captured['autocommit'], 'без autocommit соединение держит открытую транзакцию')
        self.assertEqual(captured['encryption'], 'require')
        self.assertTrue(captured['read_only'])
        self.assertTrue(captured['as_dict'])
        self.assertEqual(captured['server'], '10.0.0.24')
        self.assertEqual(captured['port'], '1433')
        self.assertEqual(captured['database'], 'oktell')
        self.assertLessEqual(captured['login_timeout'], 10)
        self.assertTrue(0 < captured['timeout'] <= 30, 'запрос без таймаута висел бы без читателя')
        setup = ' ; '.join(captured['conn_properties'])
        self.assertIn('SET LOCK_TIMEOUT 5000', setup)
        self.assertIn('SET DEADLOCK_PRIORITY LOW', setup)
        # Чтение без разделяемых блокировок: под READ COMMITTED снимок на третьей минуте работы
        # попал во взаимную блокировку с пишущим процессом станции (11.11.2026, ошибка 1205).
        self.assertIn('SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED', setup)
        self.assertNotIn('READ COMMITTED', setup)
        self.assertEqual(sum('ISOLATION LEVEL' in item for item in captured['conn_properties']), 1)

    def test_write_permissions_reads_only_own_rights(self):
        sql = oktell_db.WRITE_PERMISSIONS_SQL
        self.assertTrue(sql.startswith('SELECT HAS_PERMS_BY_NAME('))
        self.assertNotIn(' FROM ', sql, 'ни одной таблицы: только действующие права самой учётки')
        self.assertNotIn(';', sql)
        for name, _expr, _label in oktell_db.WRITE_PERMISSIONS:
            self.assertIn(' AS %s' % name, sql)

    def test_write_permissions_names_what_is_granted(self):
        row = {name: 0 for name, _expr, _label in oktell_db.WRITE_PERMISSIONS}
        db = self.db(_Connection(rows=[dict(row)]))
        self.assertEqual(db.write_permissions(), [])
        self.assertEqual(self.connection.executed, [oktell_db.WRITE_PERMISSIONS_SQL])
        db = self.db(_Connection(rows=[dict(row, control_server=1, can_update=decimal.Decimal(1),
                                            can_delete=None, can_alter='x')]))
        self.assertEqual(db.write_permissions(), ['администратор сервера', 'UPDATE'])

    def test_unknown_encryption_mode_falls_back_to_require(self):
        self.assertEqual(oktell_db.OktellDb('h', 1433, 'u', 'p', encryption='nope').encryption, 'require')
        self.assertEqual(oktell_db.OktellDb('h', 1433, 'u', 'p', encryption='off').encryption, 'off')

    def test_from_config(self):
        db = oktell_db.from_config({'sql_host': '10.0.0.24', 'sql_port': '1433', 'sql_user': 'reader',
                                    'sql_password': 'p', 'sql_database': '', 'sql_encryption': ''})
        self.assertEqual(db.describe(), '10.0.0.24:1433/oktell')
        self.assertEqual(db.encryption, 'require')


if __name__ == '__main__':
    unittest.main()
