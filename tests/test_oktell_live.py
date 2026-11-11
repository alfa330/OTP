# -*- coding: utf-8 -*-
"""Приём снимка табло от коннектора Oktell (oktell_live/): ручка и память процесса.

Что закреплено:
  * без подписи ключом шлюза ручка не принимает ничего (401), без ключей на портале — 503;
  * снимок, снятый с параметрами портала, принимается и становится источником табло;
    снятый с другими — нет (`accepted: false, reason: 'params'`), а в ответе всегда текущие
    параметры и такт;
  * от строк остаются только колонки запроса и простые значения;
  * табло берёт снимок, только пока он свеж; рубильник `SZOV_WALLBOARD_VM_SOURCE=0` его
    отключает; новее готового снимка табло — повод пересобрать его сразу.
"""
import json
import os
import sys
import unittest
from unittest import mock

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import agent_auth, oktell_wallboard  # noqa: E402
from cdr_bridge import signing  # noqa: E402
from oktell_live import routes as live_routes, store as live_store  # noqa: E402

PRIVATE_B64, PUBLIC_B64, KEY_ID = signing.generate()
OTHER_PRIVATE_B64, _OTHER_PUBLIC_B64, _OTHER_KEY_ID = signing.generate()
PATH = '/api/oktell_live/agent/push'
TEST_KEY = '7000000101'

TOTALS = {
    'oktell_now': '2026-11-10 22:00:00', 'queue_now': 2, 'queue_max_wait_seconds': 17,
    'talking_now': 4, 'arrived': 300, 'served': 280, 'lost': 20, 'greet_drop': 9,
    'served_sl': 250, 'wait_seconds': 4100.5, 'wait_served_seconds': 3000.25,
    'max_wait_seconds': 201.3, 'talk_seconds': 60000,
}


def snapshot_rows(operators=(('Оператор Один', 1, -1), ('Оператор Два', 2, 4))):
    rows = [dict(TOTALS, operator_name=name, state=state, icode=icode,
                 since='2026-11-10 21:50:00', in_state_seconds=600)
            for name, state, icode in operators]
    return rows or [dict(TOTALS, operator_name=None, state=None, icode=None, since=None,
                         in_state_seconds=None)]


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class CleanRowsTests(unittest.TestCase):

    def test_keeps_only_snapshot_columns_and_plain_values(self):
        raw = snapshot_rows()
        raw[0].update(extra='лишнее', served={'x': 1}, lost=[1], greet_drop=True,
                      operator_name='Я' * 500, wait_seconds=float('nan'))
        rows = live_store.clean_rows(raw)
        self.assertEqual(set(rows[0]), set(oktell_wallboard.SNAPSHOT_COLUMNS))
        self.assertIsNone(rows[0]['served'], 'объект вместо числа — пусто, а не объект на табло')
        self.assertIsNone(rows[0]['lost'])
        self.assertIsNone(rows[0]['greet_drop'], 'булево — не число звонков')
        self.assertIsNone(rows[0]['wait_seconds'], 'NaN в JSON табло не нужен')
        self.assertEqual(len(rows[0]['operator_name']), live_store.MAX_TEXT)
        self.assertEqual(rows[1]['arrived'], 300)
        self.assertEqual(rows[1]['wait_served_seconds'], 3000.25)

    def test_rejects_what_is_not_a_snapshot(self):
        for bad in (None, 'rows', {}, [], [1, 2], [['a']]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    live_store.clean_rows(bad)
        with self.assertRaises(ValueError):
            live_store.clean_rows(snapshot_rows() * (live_store.MAX_ROWS // 2 + 1))

    def test_rejects_a_snapshot_without_totals_columns(self):
        """Коннектор со старым запросом молча обнулил бы показатель на табло."""
        raw = snapshot_rows()
        del raw[0]['wait_served_seconds']
        with self.assertRaises(ValueError) as caught:
            live_store.clean_rows(raw)
        self.assertIn('wait_served_seconds', str(caught.exception))

    def test_night_snapshot_with_no_operators_is_valid(self):
        rows = live_store.clean_rows(snapshot_rows(operators=()))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['operator_name'])
        self.assertEqual(rows[0]['served'], 280)


class StoreTests(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.store = live_store.Store(clock=self.clock)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (live_store.ENABLED_ENV, live_store.MAX_AGE_ENV, live_store.INTERVAL_ENV):
            os.environ.pop(name, None)

    def put(self, sl_seconds=20):
        self.store.put_wallboard(live_store.clean_rows(snapshot_rows()),
                                 {'sl_seconds': sl_seconds, 'test_keys': []}, taken_at=1.0, query_ms=180)

    def test_empty_store_gives_nothing(self):
        self.assertIsNone(self.store.wallboard_rows(20))
        self.assertFalse(self.store.wallboard_newer_than(0))
        self.assertFalse(self.store.agent_known())
        self.assertEqual(self.store.silence_note(), '')

    def test_fresh_snapshot_is_served(self):
        self.put()
        self.clock.tick(12)
        got = self.store.wallboard_rows(20)
        self.assertEqual(len(got['rows']), 2)
        self.assertEqual(got['rows'][0]['served'], 280)
        self.assertAlmostEqual(got['age_seconds'], 12)

    def test_snapshot_expires(self):
        self.put()
        self.clock.tick(live_store.DEFAULT_MAX_AGE_SECONDS + 1)
        self.assertIsNone(self.store.wallboard_rows(20))
        self.assertFalse(self.store.wallboard_newer_than(0))

    def test_max_age_is_configurable_within_bounds(self):
        self.put()
        self.clock.tick(100)
        os.environ[live_store.MAX_AGE_ENV] = '120'
        self.assertIsNotNone(self.store.wallboard_rows(20))
        os.environ[live_store.MAX_AGE_ENV] = '1'       # ниже нижней границы — берётся граница
        self.assertEqual(live_store.max_age_seconds(), live_store.MIN_MAX_AGE_SECONDS)
        os.environ[live_store.MAX_AGE_ENV] = 'мусор'
        self.assertEqual(live_store.max_age_seconds(), live_store.DEFAULT_MAX_AGE_SECONDS)

    def test_other_sl_threshold_is_not_served(self):
        """Порог SL на портале сменили: снимок со старым порогом считал бы SL иначе, чем экран."""
        self.put(sl_seconds=20)
        self.assertIsNone(self.store.wallboard_rows(30))
        self.assertIsNotNone(self.store.wallboard_rows('20'))
        # Порог портала вне границ коннектора: тот снял бы снимок со своим порогом (20), и
        # совпадением это считать нельзя — табло идёт к мосту с настоящим порогом.
        self.assertIsNone(self.store.wallboard_rows(900))
        self.assertIsNone(self.store.wallboard_rows(None))

    def test_kill_switch(self):
        self.put()
        for off in ('0', 'off', 'false', 'No'):
            with self.subTest(off=off):
                os.environ[live_store.ENABLED_ENV] = off
                self.assertIsNone(self.store.wallboard_rows(20))
                self.assertFalse(self.store.wallboard_newer_than(0))
                self.assertEqual(self.store.silence_note(), '')
        os.environ[live_store.ENABLED_ENV] = '1'
        self.assertIsNotNone(self.store.wallboard_rows(20))

    def test_newer_than_compares_with_the_moment_of_receipt(self):
        self.put()
        received = self.clock.now
        self.assertTrue(self.store.wallboard_newer_than(received - 1))
        self.assertFalse(self.store.wallboard_newer_than(received))
        self.assertFalse(self.store.wallboard_newer_than(received + 5))
        self.assertTrue(self.store.wallboard_newer_than(None))

    def test_silence_note_names_the_reason(self):
        self.store.agent_seen(hostname='otp-gw', version='oktell/1.0.0', key_id='k',
                              db={'host': '10.0.0.24:1433/oktell', 'connected': True})
        self.assertIn('ещё не прислал', self.store.silence_note())
        self.put()
        self.clock.tick(20)
        self.store.agent_seen(hostname='otp-gw', version='oktell/1.0.0', key_id='k',
                              db={'connected': False, 'error': 'Login timeout'})
        self.assertIn('не читает базу Oktell', self.store.silence_note())
        self.assertIn('Login timeout', self.store.silence_note())
        self.clock.tick(live_store.DEFAULT_MAX_AGE_SECONDS + 30)
        self.assertIn('молчит', self.store.silence_note())

    def test_source_switch_is_logged_once(self):
        with self.assertLogs(live_store.log, level='INFO') as captured:
            self.store.note_source(live_store.SOURCE_VM)
            self.store.note_source(live_store.SOURCE_VM)
            self.store.note_source(live_store.SOURCE_BRIDGE)
            self.store.note_source(live_store.SOURCE_BRIDGE)
        self.assertEqual(len(captured.records), 2)
        self.assertIn('коннектор Oktell на VM шлюза', captured.records[0].getMessage())
        self.assertEqual(captured.records[1].levelname, 'WARNING')
        self.assertIn('мост', captured.records[1].getMessage())

    def test_first_snapshot_from_the_bridge_is_not_news(self):
        """До коннектора табло всегда жило на мосту — писать об этом в журнал незачем."""
        with self.assertNoLogs(live_store.log, level='INFO'):
            self.store.note_source(live_store.SOURCE_BRIDGE)

    def test_db_state_change_is_logged_once(self):
        ok = {'host': 'h', 'connected': True}
        down = {'host': 'h', 'connected': False, 'error': 'timeout'}
        with self.assertLogs(live_store.log, level='INFO') as captured:
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=ok)
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=ok)
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=down)
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=down)
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=ok)
        messages = [record.getMessage() for record in captured.records]
        self.assertEqual(len(messages), 3, messages)
        self.assertIn('на связи', messages[0])
        self.assertIn('не отвечает', messages[1])
        self.assertIn('снова отвечает', messages[2])

    def test_unknown_db_state_raises_no_alarm_and_keeps_what_is_known(self):
        """Первый запрос коннектора уходит до первого чтения базы, проверка установки — после
        него: `connected: None` — не «база лежит»."""
        unknown = {'host': 'h', 'connected': None, 'error': None}
        with self.assertLogs(live_store.log, level='INFO') as captured:
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=unknown)
        self.assertEqual([record.levelname for record in captured.records], ['INFO'], 'только «на связи»')
        self.assertIsNone(self.store.status()['agent']['db_connected'])
        self.assertIn('ещё не прислал', self.store.silence_note())

        self.store.agent_seen(hostname='gw', version='v', key_id='k', db={'host': 'h', 'connected': True})
        with self.assertNoLogs(live_store.log, level='INFO'):
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=unknown)
        self.assertTrue(self.store.status()['agent']['db_connected'], 'прежнее знание не стёрто')

        with self.assertLogs(live_store.log, level='WARNING'):
            self.store.agent_seen(hostname='gw', version='v', key_id='k',
                                  db={'host': 'h', 'connected': False, 'error': 'обрыв'})
        with self.assertNoLogs(live_store.log, level='INFO'):
            self.store.agent_seen(hostname='gw', version='v', key_id='k', db=unknown)
        self.assertIs(self.store.status()['agent']['db_connected'], False)

    def test_status_reports_agent_and_snapshot(self):
        self.store.agent_seen(hostname='otp-gw', version='oktell/1.0.0', key_id=KEY_ID,
                              db={'host': '10.0.0.24:1433/oktell', 'connected': True})
        self.put()
        self.clock.tick(7)
        status = self.store.status()
        self.assertEqual(status['agent']['hostname'], 'otp-gw')
        self.assertEqual(status['agent']['age_seconds'], 7)
        self.assertEqual(status['wallboard']['rows'], 2)
        self.assertEqual(status['wallboard']['query_ms'], 180)
        json.dumps(status)   # уходит в JSON как есть


class RouteTests(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.store = live_store.Store(clock=self.clock)
        self.params = {'sl_seconds': 20, 'test_keys': [TEST_KEY]}
        app = Flask(__name__)
        app.register_blueprint(live_routes.build_oktell_live_blueprint(
            build_cors_preflight_response=lambda: ('', 204),
            wallboard_params=lambda: self.params,
            store=self.store))
        self.client = app.test_client()
        patcher = mock.patch.dict(os.environ, {'CDR_AGENT_KEYS': PUBLIC_B64}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(live_store.INTERVAL_ENV, None)
        self.signer = signing.Signer(signing.load_private_key(PRIVATE_B64))

    def post(self, payload, signer=None, sign=True, body=None):
        body = body if body is not None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if sign:
            headers.update((signer or self.signer).headers('POST', PATH, body))
        return self.client.post(PATH, data=body, headers=headers)

    def hello(self, **over):
        payload = {'agent_id': 'gw-1', 'hostname': 'otp-gw', 'version': 'oktell/1.0.0/3.12.0',
                   'db': {'host': '10.0.0.24:1433/oktell', 'connected': True, 'error': None},
                   'wallboard': None}
        payload.update(over)
        return payload

    def snapshot(self, params=None, rows=None):
        return {'rows': rows if rows is not None else snapshot_rows(),
                'params': params if params is not None else dict(self.params),
                'taken_at': 123.0, 'query_ms': 175}

    # ── вход ─────────────────────────────────────────────────────────────────

    def test_unsigned_request_is_refused(self):
        response = self.post(self.hello(wallboard=self.snapshot()), sign=False)
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.wallboard_rows(20))
        self.assertFalse(self.store.agent_known())

    def test_foreign_key_is_refused(self):
        stranger = signing.Signer(signing.load_private_key(OTHER_PRIVATE_B64))
        response = self.post(self.hello(wallboard=self.snapshot()), signer=stranger)
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.wallboard_rows(20))

    def test_tampered_body_is_refused(self):
        body = json.dumps(self.hello(wallboard=self.snapshot())).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', PATH, body))
        response = self.client.post(PATH, data=body.replace(b'"served": 280', b'"served": 999'),
                                    headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.wallboard_rows(20))

    def test_replayed_request_is_refused(self):
        body = json.dumps(self.hello()).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', PATH, body))
        self.assertEqual(self.client.post(PATH, data=body, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(PATH, data=body, headers=headers).status_code, 401)

    def test_portal_without_keys_answers_503(self):
        with mock.patch.dict(os.environ, {'CDR_AGENT_KEYS': ''}):
            with mock.patch('cdr.config.agent_keys_raw', return_value=''):
                response = self.post(self.hello())
        self.assertEqual(response.status_code, 503)

    def test_signature_is_bound_to_this_path(self):
        """Подпись запроса к другой ручке шлюза здесь не годится."""
        body = json.dumps(self.hello(wallboard=self.snapshot())).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', '/api/pbx/agent/sync', body))
        self.assertEqual(self.client.post(PATH, data=body, headers=headers).status_code, 401)

    # ── приём ────────────────────────────────────────────────────────────────

    def test_hello_returns_params_and_interval(self):
        response = self.post(self.hello())
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['params'], {'sl_seconds': 20, 'test_keys': [TEST_KEY]})
        self.assertEqual(data['interval_seconds'], live_store.DEFAULT_INTERVAL_SECONDS)
        self.assertFalse(data['accepted'])
        self.assertIsNone(data['reason'])
        self.assertTrue(self.store.agent_known())
        self.assertIsNone(self.store.wallboard_rows(20), 'без снимка табло брать нечего')

    def test_interval_comes_from_portal_settings(self):
        with mock.patch.dict(os.environ, {live_store.INTERVAL_ENV: '5'}):
            self.assertEqual(self.post(self.hello()).get_json()['interval_seconds'], 5)
        with mock.patch.dict(os.environ, {live_store.INTERVAL_ENV: '1'}):
            self.assertEqual(self.post(self.hello()).get_json()['interval_seconds'],
                             live_store.MIN_INTERVAL_SECONDS)

    def test_matching_snapshot_is_accepted(self):
        response = self.post(self.hello(wallboard=self.snapshot()))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['accepted'])
        got = self.store.wallboard_rows(20)
        self.assertEqual([row['operator_name'] for row in got['rows']], ['Оператор Один', 'Оператор Два'])
        self.assertEqual(got['rows'][0]['wait_served_seconds'], 3000.25)
        status = self.store.status()
        self.assertEqual(status['agent']['key_id'], KEY_ID)
        self.assertEqual(status['agent']['version'], 'oktell/1.0.0/3.12.0')
        self.assertEqual(status['wallboard']['query_ms'], 175)

    def test_snapshot_with_other_params_is_not_accepted(self):
        """После правки реестра тестовых номеров снимок со старым списком на табло не идёт."""
        stale = self.snapshot(params={'sl_seconds': 20, 'test_keys': []})
        response = self.post(self.hello(wallboard=stale))
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data['accepted'])
        self.assertEqual(data['reason'], 'params')
        self.assertEqual(data['params']['test_keys'], [TEST_KEY])
        self.assertIsNone(self.store.wallboard_rows(20))
        self.assertTrue(self.store.agent_known(), 'коннектор при этом на связи')

    def test_previous_snapshot_survives_a_refused_one(self):
        self.post(self.hello(wallboard=self.snapshot()))
        self.params = {'sl_seconds': 20, 'test_keys': []}     # реестр на портале изменился
        refused = self.post(self.hello(wallboard=self.snapshot(params={'sl_seconds': 20,
                                                                       'test_keys': [TEST_KEY]})))
        self.assertFalse(refused.get_json()['accepted'])
        self.assertIsNotNone(self.store.wallboard_rows(20), 'прежний снимок остаётся до нового')

    def test_malformed_snapshot_is_400_and_stores_nothing(self):
        for bad in ({'rows': 'x', 'params': self.params}, {'rows': [], 'params': self.params},
                    {'params': self.params}, 'снимок', [1]):
            with self.subTest(bad=bad):
                response = self.post(self.hello(wallboard=bad))
                self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.store.wallboard_rows(20))

    def test_body_must_be_an_object(self):
        self.assertEqual(self.post(None, body=b'[1, 2]').status_code, 400)
        self.assertEqual(self.post(None, body=b'not json').status_code, 400)

    def test_rows_are_cleaned_before_storing(self):
        rows = snapshot_rows()
        rows[0]['password'] = 'лишнее'
        rows[0]['served'] = {'$gt': 0}
        self.assertTrue(self.post(self.hello(wallboard=self.snapshot(rows=rows))).get_json()['accepted'])
        stored = self.store.wallboard_rows(20)['rows'][0]
        self.assertNotIn('password', stored)
        self.assertIsNone(stored['served'])

    def test_db_trouble_is_reported_without_a_snapshot(self):
        payload = self.hello(db={'host': 'h', 'connected': False, 'error': 'Login timeout expired'})
        with self.assertLogs(live_store.log, level='WARNING'):
            response = self.post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Login timeout expired', self.store.silence_note())

    def test_options_is_a_cors_preflight(self):
        self.assertEqual(self.client.options(PATH).status_code, 204)

    def test_route_uses_the_same_protocol_as_other_gateway_agents(self):
        self.assertEqual(agent_auth.SCHEME, 'OTP-AGENT-v1')
        self.assertEqual(PATH, '/api/oktell_live/agent/push')


if __name__ == '__main__':
    unittest.main()
