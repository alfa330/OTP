# -*- coding: utf-8 -*-
"""«Звонки Binotel» для CRM (calls_api): правила и ручки без базы.

Что здесь сторожится:

* **Разбор Binotel.** Вебхук «API Call Completed» приходит формой callDetails[…] (настоящие
  имена полей с прода, 05.11.2026), история — нормализованными звонками клиента; оба дают одну
  форму звонка, идущий звонок (ONLINE) и звонок без линии/момента — не звонок.
* **Исход.** ANSWER/BUSY/NOANSWER/CANCEL/прочее → answered/busy/no_answer/failed; разговор —
  ответ и хотя бы секунда; запись — только у разговора.
* **Документ партнёру.** Время ISO с +05:00, ответ = начало + ожидание, конец = + разговор,
  номер +7…, оператор с почтой (по ней CRM узнаёт менеджера).
* **Дверь** — ключ с областью «Звонки Binotel»: без ключа 401, чужая область 403, без схемы
  503, сверх предела 429; фильтры истории разбираются строго (400 на мусор).

SQL, сверка с историей и подписка — в tests/test_calls_api_postgres.py.
"""

import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from calls_api import routes, rules  # noqa: E402
from calls_api.service import CallsApiError  # noqa: E402
from partner_keys import keys  # noqa: E402

KEY = 'otpk_FAKE-calls-key-for-tests-0123456789-abcdef'
KEY_ROW = (9, 'CRM', keys.mark(KEY), ['binotel_calls'], 'operator', 1, 'СЗоВ', 'szov',
           3, 'Админ', None, None, 0, None, None, None)
OTHER_DOOR_ROW = KEY_ROW[:3] + (['olx_chats'],) + KEY_ROW[4:]

# Вебхук как его шлёт Binotel (имена полей сняты с прода 05.11.2026; значения выдуманы).
WEBHOOK = {
    'requestType': 'apiCallCompleted', 'attemptsCounter': '1', 'language': 'ru',
    'myBinotelDomain': 'my.binotel.ua',
    'callDetails[companyID]': '98190', 'callDetails[generalCallID]': '7890123456',
    'callDetails[callID]': '7890123457', 'callDetails[startTime]': '1793858400',
    'callDetails[callType]': '1', 'callDetails[internalNumber]': '915',
    'callDetails[internalAdditionalData]': '', 'callDetails[externalNumber]': '87011234567',
    'callDetails[waitsec]': '12', 'callDetails[billsec]': '47', 'callDetails[disposition]': 'ANSWER',
    'callDetails[isNewCall]': '1', 'callDetails[whoHungUp]': '', 'callDetails[recordingStatus]': 'uploading',
    'callDetails[pbxNumberData][number]': '77273334455',
    'callDetails[historyData][0][disposition]': 'BUSY', 'callDetails[historyData][0][billsec]': '0',
}


class RuleTests(unittest.TestCase):
    def test_results(self):
        self.assertEqual([rules.map_result(v) for v in ('ANSWER', 'busy', 'NOANSWER', 'CANCEL', 'CHANUNAVAIL',
                                                        'CONGESTION', 'VM-SUCCESS')],
                         ['answered', 'busy', 'no_answer', 'no_answer', 'failed', 'failed', 'answered'])
        for value in ('', None, 'ONLINE', 'ringing'):
            self.assertEqual(rules.map_result(value), '')

    def test_phone(self):
        self.assertEqual(rules.normalize_phone('87011234567'), '+77011234567')
        self.assertEqual(rules.normalize_phone('+7 (701) 123-45-67'), '+77011234567')
        self.assertEqual(rules.normalize_phone('7011234567'), '+77011234567')
        self.assertIsNone(rules.normalize_phone('0675639050'))
        self.assertIsNone(rules.normalize_phone('102'))

    def test_webhook_form(self):
        call = rules.from_webhook(WEBHOOK)
        self.assertEqual(call, {
            'general_call_id': '7890123456', 'company_id': '98190', 'internal_number': '915',
            'direction': 'out', 'phone': '+77011234567', 'external_raw': '87011234567',
            'disposition': 'ANSWER', 'result': 'answered', 'billsec': 47, 'waitsec': 12,
            'started_at': datetime.fromtimestamp(1793858400, tz=timezone.utc),
            'recording_status': 'uploading'})
        # Плечо из historyData не подменяет звонок: исход — callDetails[disposition].
        self.assertEqual(call['disposition'], 'ANSWER')

    def test_not_a_call(self):
        for broken in ({'callDetails[internalNumber]': ''}, {'callDetails[disposition]': 'ONLINE'},
                       {'callDetails[generalCallID]': ''}, {'callDetails[startTime]': '0'}):
            self.assertIsNone(rules.from_webhook(dict(WEBHOOK, **broken)), broken)
        self.assertIsNone(rules.from_webhook({}))

    def test_history_shape_matches_webhook(self):
        history = {'general_call_id': '7890123456', 'internal_number': '915', 'external_number': '87011234567',
                   'call_type': 1, 'disposition': 'ANSWER', 'billsec': 47, 'waitsec': 12,
                   'start_time': 1793858400, 'recording_status': 'uploading', 'company_id': '98190'}
        self.assertEqual(rules.from_history(history), rules.from_webhook(WEBHOOK))

    def test_talk_and_recording(self):
        call = rules.from_webhook(WEBHOOK)
        self.assertTrue(rules.talked(call))
        self.assertTrue(rules.has_recording(call))
        self.assertFalse(rules.talked(dict(call, billsec=0)))
        self.assertFalse(rules.has_recording(dict(call, result='no_answer', billsec=0)))
        self.assertFalse(rules.has_recording(dict(call, recording_status='disabled')))

    def test_changed(self):
        old = {'result': 'answered', 'billsec': 47, 'waitsec': 12}
        self.assertFalse(rules.changed(old, dict(old)))
        self.assertFalse(rules.changed(old, dict(old, billsec=46)))
        self.assertTrue(rules.changed(old, dict(old, billsec=48)))
        self.assertTrue(rules.changed(old, dict(old, result='no_answer')))

    def test_scope(self):
        self.assertTrue(rules.in_scope('FRONT_OFFICE'))
        self.assertFalse(rules.in_scope('remote_cc'))
        self.assertFalse(rules.in_scope(None))

    def test_document(self):
        call = rules.from_webhook(WEBHOOK)
        row = dict(call, user_id=42, user_name='Иванова Айгерим', user_email='a.ivanova@example.kz',
                   department_name='Фронт офисы', source='webhook',
                   received_at=datetime(2026, 11, 5, 6, 1, 0, tzinfo=timezone.utc),
                   updated_at=datetime(2026, 11, 5, 6, 1, 0, tzinfo=timezone.utc))
        doc = rules.document(row)
        self.assertEqual(doc['id'], '7890123456')
        self.assertEqual((doc['direction'], doc['result'], doc['talked'], doc['has_recording']),
                         ('outgoing', 'answered', True, True))
        self.assertEqual((doc['phone'], doc['phone_raw'], doc['line']), ('+77011234567', '87011234567', '915'))
        self.assertEqual(doc['operator'], {'id': 42, 'name': 'Иванова Айгерим', 'email': 'a.ivanova@example.kz',
                                           'department': 'Фронт офисы'})
        self.assertEqual(doc['started_at'], '2026-11-05T11:00:00+05:00')
        self.assertEqual(doc['answered_at'], '2026-11-05T11:00:12+05:00')
        self.assertEqual(doc['ended_at'], '2026-11-05T11:00:59+05:00')
        self.assertEqual((doc['wait_sec'], doc['talk_sec']), (12, 47))
        self.assertEqual(doc['received_at'], '2026-11-05T11:01:00+05:00')
        missed = rules.document(dict(row, result='no_answer', disposition='NOANSWER', billsec=0, waitsec=30))
        self.assertIsNone(missed['answered_at'])
        self.assertEqual(missed['ended_at'], '2026-11-05T11:00:30+05:00')
        self.assertFalse(missed['talked'] or missed['has_recording'])
        self.assertIsNone(rules.document(dict(row, user_id=None))['operator'])


class _Cursor:
    def __init__(self, *, ready=True, key_row=KEY_ROW):
        self.calls = []
        self.ready = ready
        self.key_row = key_row
        self._one = None

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._one = None
        if 'to_regclass' in text:
            self._one = (self.ready,)
        elif 'FROM partner_keys k' in text and 'WHERE k.key_hash' in text:
            self._one = self.key_row if params and params[0] == keys.digest(KEY) else None
        elif text.startswith('INSERT INTO partner_key_calls'):
            self._one = (1,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def journal(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO partner_key_calls')]


class FakeService:
    def __init__(self, ready=True):
        self.calls = []
        self._ready = ready
        self.recording_error = None

    def ready(self):
        return self._ready

    def operators(self):
        self.calls.append(('operators',))
        return [{'id': 42, 'name': 'Иванова Айгерим', 'email': 'a@example.kz', 'line': '915',
                 'phone_mode': 'phone', 'seated_at': '2026-11-01T09:00:00+05:00'}]

    def list_calls(self, **filters):
        self.calls.append(('list', filters))
        return [{'id': '1'}], 1

    def get_call(self, general_call_id):
        self.calls.append(('get', general_call_id))
        return {'id': general_call_id} if general_call_id == '7890123456' else None

    def recording_url(self, general_call_id):
        self.calls.append(('recording', general_call_id))
        if self.recording_error:
            raise self.recording_error
        return {'id': general_call_id, 'url': 'https://records.example/1.mp3', 'talk_sec': 47}

    def ping(self, key_id):
        return 1


@unittest.skipIf(Flask is None, 'flask не установлен')
class Harness(unittest.TestCase):
    def build(self, *, ready=True, key_row=KEY_ROW, per_minute=None, service_ready=True):
        self.cursor = _Cursor(ready=ready, key_row=key_row)
        self.service = FakeService(ready=service_ready)
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield self.cursor

        db._get_cursor = _get_cursor
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.register_blueprint(routes.build_calls_api_blueprint(
            db=db, service=self.service, client_ip=lambda: '203.0.113.7', environ={}, per_minute=per_minute))
        return app.test_client()

    def headers(self, key=KEY):
        return {keys.HEADER: key} if key is not None else {}


class DoorTests(Harness):
    def test_refusals(self):
        self.assertEqual(self.build(ready=False).get('/api/calls', headers=self.headers()).status_code, 503)
        self.assertEqual(self.build(service_ready=False).get('/api/calls', headers=self.headers()).status_code, 503)
        client = self.build()
        self.assertEqual(client.get('/api/calls').status_code, 401)
        self.assertEqual(client.get('/api/calls', headers=self.headers(KEY + 'x')).status_code, 401)
        response = self.build(key_row=OTHER_DOOR_ROW).get('/api/calls', headers=self.headers())
        self.assertEqual((response.status_code, response.get_json()['code']), (403, 'CALLS_API_FORBIDDEN'))
        self.assertEqual(self.service.calls, [])

    def test_minute_limit(self):
        client = self.build(per_minute=1)
        self.assertEqual(client.get('/api/calls/ping', headers=self.headers()).status_code, 200)
        response = client.get('/api/calls/ping', headers=self.headers())
        self.assertEqual(response.status_code, 429)
        self.assertTrue(int(response.headers['Retry-After']) >= 1)
        self.assertEqual(self.cursor.journal()[0]['outcome'], 'limit')

    def test_ping(self):
        payload = self.build().get('/api/calls/ping', headers=self.headers()).get_json()
        self.assertEqual((payload['key'], payload['events'], payload['results']),
                         ('CRM', ['call.completed'], ['answered', 'busy', 'no_answer', 'failed']))


class ReadTests(Harness):
    def test_static_paths_win_over_call_id(self):
        client = self.build()
        response = client.get('/api/calls/operators', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['operators'][0]['line'], '915')
        self.assertEqual(self.service.calls, [('operators',)])

    def test_list_filters(self):
        client = self.build()
        response = client.get('/api/calls?from=2026-11-05T00:00:00&to=2026-11-06T00:00:00%2B05:00'
                              '&phone=8%20701%20123%2045%2067&operator=a@example.kz&line=915'
                              '&result=no_answer&direction=outgoing&limit=900&offset=5', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        _, filters = self.service.calls[0]
        self.assertEqual(filters['date_from'].isoformat(), '2026-11-05T00:00:00+05:00')
        self.assertEqual(filters['date_to'].isoformat(), '2026-11-06T00:00:00+05:00')
        self.assertEqual((filters['phone'], filters['email'], filters['line'], filters['result'],
                          filters['direction'], filters['limit'], filters['offset']),
                         ('+77011234567', 'a@example.kz', '915', 'no_answer', 'out', 500, 5))
        self.assertEqual(response.get_json()['pagination'], {'limit': 500, 'offset': 5, 'total': 1,
                                                             'has_more': False})
        client.get('/api/calls?operator=42&updated_since=2026-11-05T10:00:00Z', headers=self.headers())
        _, filters = self.service.calls[1]
        self.assertEqual(filters['user_id'], 42)
        self.assertEqual(filters['updated_since'].isoformat(), '2026-11-05T10:00:00+00:00')

    def test_list_refuses_garbage(self):
        client = self.build()
        for query in ('from=yesterday', 'phone=123', 'operator=Иванова', 'result=ok', 'direction=out',
                      'updated_since=x'):
            response = client.get('/api/calls?' + query, headers=self.headers())
            self.assertEqual((response.status_code, response.get_json()['code']),
                             (400, 'CALLS_API_BAD_REQUEST'), query)
        self.assertEqual(self.service.calls, [])

    def test_one_call(self):
        client = self.build()
        self.assertEqual(client.get('/api/calls/7890123456', headers=self.headers()).get_json(),
                         {'call': {'id': '7890123456'}})
        self.assertEqual(client.get('/api/calls/1', headers=self.headers()).status_code, 404)
        self.assertEqual(client.get('/api/calls/abc', headers=self.headers()).status_code, 404)
        self.assertEqual(self.service.calls, [('get', '7890123456'), ('get', '1')])

    def test_recording(self):
        client = self.build()
        response = client.get('/api/calls/7890123456/recording', headers=self.headers())
        self.assertEqual(response.get_json()['url'], 'https://records.example/1.mp3')
        self.service.recording_error = CallsApiError('CALLS_API_RECORDING_NOT_READY', 'ещё не готова', 409)
        response = client.get('/api/calls/7890123456/recording', headers=self.headers())
        self.assertEqual((response.status_code, response.get_json()['code'], response.headers['Retry-After']),
                         (409, 'CALLS_API_RECORDING_NOT_READY', '60'))
        self.service.recording_error = CallsApiError('CALLS_API_BUSY', 'подождите', 503)
        self.assertEqual(client.get('/api/calls/7890123456/recording', headers=self.headers()).headers['Retry-After'],
                         '5')
        self.service.recording_error = RuntimeError('boom')
        response = client.get('/api/calls/7890123456/recording', headers=self.headers())
        self.assertEqual((response.status_code, response.get_json()['code']), (500, 'CALLS_API_FAILED'))


if __name__ == '__main__':
    unittest.main()
