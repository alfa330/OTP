# -*- coding: utf-8 -*-
"""Записи разговоров ОП для журнала оценок: заказ → мост → облако.

Что закреплено:
  * запись признаётся записью этого звонка и этого оператора только при совпадении
    внутреннего номера и клиента (external-/out-) либо uniqueid файла с linkedid (q-):
    станция подставляет ссылку по номеру клиента и у трети входящих отдаёт файл чужого
    агента той же группы вызова;
  * мост ходит за файлом ТОЛЬКО через локальный прокси записей (хост из ссылки станции
    отбрасывается), 404 — финальный «missing», прочее — «error»; наружу не бросает;
  * ручки моста: заказы выдаются партиями, приём записи кладёт файл через store_audio,
    отказ фиксируется, без store_audio приём отказывает, а мусор в base64 — 400;
  * проводка в монолите: источник 'cdr' у звонковых направлений ОП, обе кнопки
    (журнал и «ИИ-оценка») ветвятся на отдел продаж, store_audio передан разделу.
"""

import base64
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from flask import Flask

from cdr import routes as cdr_routes, touches as T
from cdr_bridge import agent as bridge_mod

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'test-bridge-token-0123456789'
LINKEDID = '1789533127.1074887'
REC = 'http://192.168.88.251/recordings/2026/09/16/'


class RecordingOwnershipTests(unittest.TestCase):
    def test_agent_leg_matches_ext_and_client(self):
        url = REC + 'external-6728-+77773714269-20260916-093225-1789533145.1074889.wav'
        self.assertTrue(T.recording_belongs_to(url, '6728', '7773714269', LINKEDID))
        # чужой агент той же группы вызова — файл не этого оператора
        self.assertFalse(T.recording_belongs_to(url, '6665', '7773714269', LINKEDID))
        # другой клиент
        self.assertFalse(T.recording_belongs_to(url, '6728', '7001112233', LINKEDID))

    def test_outgoing_leg_matches_ext_and_client(self):
        url = REC + 'out-4503*+77474491762-6665-20260916-000617-1789499177.1073847.wav'
        self.assertTrue(T.recording_belongs_to(url, '6665', '7474491762', '1789499177.1073847'))
        self.assertFalse(T.recording_belongs_to(url, '6360', '7474491762', '1789499177.1073847'))

    def test_queue_leg_is_tied_to_the_call_by_uniqueid(self):
        url = REC + 'q-3041-+77056864862-20260916-011548-1789503348.1073940.wav'
        self.assertTrue(T.recording_belongs_to(url, '6665', '7056864862', '1789503348.1073940'))
        self.assertFalse(T.recording_belongs_to(url, '6665', '7056864862', '1789503412.1073950'))

    def test_garbage_is_not_trusted(self):
        self.assertFalse(T.recording_belongs_to('', '6665', '7056864862', LINKEDID))
        self.assertFalse(T.recording_belongs_to(REC + 'unknown-form.wav', '6665', '7056864862', LINKEDID))
        self.assertFalse(T.recording_belongs_to(REC + 'external-6665-+77056864862-20260916-011548-1.1.wav',
                                                '', '7056864862', LINKEDID))


class _Response:
    def __init__(self, status=200, content=b'', content_type='audio/x-wav'):
        self.status_code = status
        self.content = content
        self.headers = {'Content-Type': content_type} if content_type else {}


class _RecordsSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.response


class BridgeAudioTests(unittest.TestCase):
    def setUp(self):
        self.posts = []
        config = {'portal': 'http://portal', 'token': 'tok', 'station': 'http://127.0.0.1:8081',
                  'login': '', 'password': '', 'records': 'http://127.0.0.1:8082',
                  'live_interval': '0'}
        self.bridge = bridge_mod.Bridge(config, station=object())
        self.bridge._post = lambda path, payload: self.posts.append((path, payload)) or {'jobs': []}

    def job(self, url=REC + 'external-6728-+77773714269-20260916-093225-1789533145.1074889.wav'):
        return {'id': 7, 'linkedid': LINKEDID, 'recording_url': url}

    def test_file_goes_to_the_portal_as_base64_via_the_local_proxy(self):
        self.bridge.records_session = _RecordsSession(_Response(200, b'RIFF....WAVE'))
        self.assertTrue(self.bridge.do_audio(self.job()))
        # хост станции отброшен, путь ушёл в прокси записей на шлюзе
        self.assertEqual(self.bridge.records_session.urls, [
            'http://127.0.0.1:8082/rec/recordings/2026/09/16/'
            'external-6728-+77773714269-20260916-093225-1789533145.1074889.wav'])
        path, payload = self.posts[-1]
        self.assertEqual(path, 'audio')
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['job_id'], 7)
        self.assertEqual(payload['content_type'], 'audio/x-wav')
        self.assertEqual(base64.b64decode(payload['audio_b64']), b'RIFF....WAVE')
        self.assertEqual(payload['bytes'], 12)

    def test_missing_file_is_final_and_other_errors_are_not(self):
        self.bridge.records_session = _RecordsSession(_Response(404, b''))
        self.assertFalse(self.bridge.do_audio(self.job()))
        self.assertEqual(self.posts[-1][1]['status'], 'missing')
        self.bridge.records_session = _RecordsSession(_Response(502, b''))
        self.assertFalse(self.bridge.do_audio(self.job()))
        self.assertEqual(self.posts[-1][1]['status'], 'error')
        self.bridge.records_session = _RecordsSession(error=bridge_mod.requests.exceptions.ConnectionError('нет'))
        self.assertFalse(self.bridge.do_audio(self.job()))
        self.assertIn('сервер записей', self.posts[-1][1]['error'])

    def test_bad_link_never_reaches_the_records_proxy(self):
        session = _RecordsSession(_Response(200, b'x'))
        self.bridge.records_session = session
        for url in ('http://192.168.88.251/../etc/passwd.wav', 'http://192.168.88.251/recordings/a.exe', ''):
            self.assertFalse(self.bridge.do_audio(self.job(url)))
            self.assertEqual(self.posts[-1][1]['status'], 'error')
        self.assertEqual(session.urls, [])

    def test_oversized_file_is_refused(self):
        self.bridge.records_session = _RecordsSession(_Response(200, b'x' * (bridge_mod.MAX_RECORD_BYTES + 1)))
        self.assertFalse(self.bridge.do_audio(self.job()))
        self.assertIn('размер', self.posts[-1][1]['error'])

    def test_content_type_falls_back_to_the_extension(self):
        self.bridge.records_session = _RecordsSession(_Response(200, b'x', content_type='application/octet-stream'))
        self.bridge.do_audio(self.job())
        self.assertEqual(self.posts[-1][1]['content_type'], 'audio/wav')

    def test_jobs_are_polled_and_a_broken_job_does_not_stop_the_rest(self):
        answers = iter([{'jobs': [self.job(''), self.job()]}])
        self.bridge._post = lambda path, payload: (self.posts.append((path, payload))
                                                   or (next(answers) if path == 'audio_poll' else {}))
        self.bridge.records_session = _RecordsSession(_Response(200, b'RIFF'))
        self.assertTrue(self.bridge.do_audio_jobs())
        statuses = [p['status'] for path, p in self.posts if path == 'audio']
        self.assertEqual(statuses, ['error', 'ok'])

    def test_failures_alone_are_not_work(self):
        # Иначе мост шёл бы за следующим заказом без паузы и сжигал попытки за секунды.
        self.bridge._post = lambda path, payload: (self.posts.append((path, payload))
                                                   or ({'jobs': [self.job()]} if path == 'audio_poll' else {}))
        self.bridge.records_session = _RecordsSession(_Response(403, b''))
        self.assertFalse(self.bridge.do_audio_jobs())
        self.assertEqual(self.posts[-1][1]['status'], 'error')

    def test_portal_outage_on_poll_is_not_fatal(self):
        def failing(path, payload):
            raise RuntimeError('портал лёг')
        self.bridge._post = failing
        self.assertFalse(self.bridge.do_audio_jobs())


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
    """Двойник слоя SQL для ручек записей."""

    def __init__(self):
        self.jobs = {7: {'id': 7, 'linkedid': LINKEDID, 'recording_url': REC + 'x.wav',
                         'imported_call_id': 555, 'status': 'running', 'attempts': 1}}
        self.claimed = []
        self.done = []
        self.failed = []

    def claim_audio_jobs(self, cursor, agent_id, limit=3):
        self.claimed.append((agent_id, limit))
        return [dict(self.jobs[7], attempts=1)]

    def audio_job(self, cursor, job_id):
        return self.jobs.get(job_id)

    def mark_audio_done(self, cursor, job_id, audio_path, audio_bytes):
        self.done.append((job_id, audio_path, audio_bytes))

    def mark_audio_failed(self, cursor, job_id, error, missing=False):
        self.failed.append((job_id, error, missing))


class AudioRouteTests(unittest.TestCase):
    def setUp(self):
        self.recorder = _Recorder()
        patcher = mock.patch.object(cdr_routes, 'queries', self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        token_patch = mock.patch.object(cdr_routes.config, 'agent_token', return_value=TOKEN)
        token_patch.start()
        self.addCleanup(token_patch.stop)
        self.stored = []

        def store_audio(linkedid, audio_bytes, content_type, imported_call_id):
            self.stored.append((linkedid, audio_bytes, content_type, imported_call_id))
            return 'bucket/Uploads/freepbx-%s.wav' % linkedid
        self.store_audio = store_audio
        self.client = self._client(store_audio)

    def _client(self, store_audio):
        app = Flask(__name__)
        app.register_blueprint(cdr_routes.build_cdr_blueprint(
            db=_FakeDb(), require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None), store_audio=store_audio))
        app.config['TESTING'] = True
        return app.test_client()

    def post(self, path, payload, client=None):
        return (client or self.client).post(
            '/api/cdr/agent/' + path, data=json.dumps(payload),
            headers={'Content-Type': 'application/json', 'X-Agent-Token': TOKEN})

    def test_poll_hands_out_claimed_jobs(self):
        response = self.post('audio_poll', {'agent_id': 'gw-1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['jobs'],
                         [{'id': 7, 'linkedid': LINKEDID, 'recording_url': REC + 'x.wav'}])
        self.assertEqual(self.recorder.claimed, [('gw-1', cdr_routes.AUDIO_JOBS_PER_POLL)])

    def test_received_file_is_stored_and_the_job_closed(self):
        body = base64.b64encode(b'RIFF....WAVE').decode('ascii')
        response = self.post('audio', {'job_id': 7, 'status': 'ok', 'content_type': 'audio/x-wav',
                                       'audio_b64': body})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.stored, [(LINKEDID, b'RIFF....WAVE', 'audio/x-wav', 555)])
        self.assertEqual(self.recorder.done, [(7, 'bucket/Uploads/freepbx-%s.wav' % LINKEDID, 12)])

    def test_bridge_failure_is_recorded_missing_is_final(self):
        response = self.post('audio', {'job_id': 7, 'status': 'missing', 'error': 'нет файла'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.recorder.failed, [(7, 'нет файла', True)])
        self.post('audio', {'job_id': 7, 'status': 'error', 'error': 'сервер записей ответил 502'})
        self.assertEqual(self.recorder.failed[-1], (7, 'сервер записей ответил 502', False))
        self.assertEqual(self.stored, [])

    def test_garbage_and_unknown_job_are_400(self):
        self.assertEqual(self.post('audio', {'job_id': 7, 'audio_b64': '***'}).status_code, 400)
        self.assertEqual(self.post('audio', {'job_id': 99, 'audio_b64': 'QUJD'}).status_code, 400)
        self.assertEqual(self.post('audio', {'status': 'ok'}).status_code, 400)
        self.assertEqual(self.stored, [])

    def test_storage_failure_returns_the_job_to_the_queue(self):
        def broken(*args):
            raise RuntimeError('облако недоступно')
        client = self._client(broken)
        response = self.post('audio', {'job_id': 7, 'audio_b64': 'QUJD'}, client=client)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.recorder.failed[-1][0], 7)
        self.assertFalse(self.recorder.failed[-1][2])

    def test_without_storage_the_portal_refuses(self):
        client = self._client(None)
        response = self.post('audio', {'job_id': 7, 'audio_b64': 'QUJD'}, client=client)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.recorder.done, [])


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        cls.journal = (ROOT / 'src' / 'call_evaluation' / 'main.jsx').read_text(encoding='utf-8-sig')
        cls.schema = (ROOT / 'cdr' / 'schema.py').read_text(encoding='utf-8-sig')

    def test_sales_call_directions_get_the_cdr_source(self):
        self.assertIn("CDR_RANDOM_CALL_MODELS = {'op_osnova', 'op_potok', 'op_yandex_reg'}", self.source)
        self.assertIn("elif _code == CDR_CALL_DISTRIBUTION_DEPARTMENT_CODE and _model in CDR_RANDOM_CALL_MODELS:",
                      self.source)
        self.assertIn("_source = 'cdr'", self.source)

    def test_both_buttons_branch_on_the_sales_department(self):
        journal = self.source[self.source.index('def fetch_random_evaluation_call'):]
        journal = journal[:journal.index('\n\n\n')]
        self.assertIn('CDR_CALL_DISTRIBUTION_DEPARTMENT_CODE', journal)
        self.assertIn('_cdr_random_call(', journal)
        ai_qa = self.source[self.source.index("elif department == CDR_CALL_DISTRIBUTION_DEPARTMENT_CODE:"):]
        self.assertIn('response = _cdr_random_call(', ai_qa[:800])

    def test_pool_rows_are_marked_and_audio_is_ordered_from_the_bridge(self):
        body = self.source[self.source.index('def _cdr_random_call'):]
        body = body[:body.index('\n\n\n')]
        self.assertIn('notes=f"{source}:{requester_id}:cdr"', body)
        self.assertIn('cdr_touches.recording_belongs_to(', body)
        self.assertIn('cdr_queries.enqueue_audio_job(', body)
        self.assertIn('"audio_pending": True', body)

    def test_storage_is_handed_to_the_cdr_section(self):
        self.assertIn('store_audio=_cdr_store_audio,', self.source)
        self.assertIn("'freepbx-%s.%s' % (safe_id, extension)", self.source)
        self.assertIn('CREATE TABLE IF NOT EXISTS cdr_audio_jobs', self.schema)

    def test_journal_modal_knows_the_cdr_source(self):
        self.assertIn("const isCdr = source === 'cdr';", self.journal)
        self.assertIn('const ownDurations = isBinotel || isCdr;', self.journal)
        self.assertIn('const boundToMonth = isBinotel || isCdr;', self.journal)
        # семидневный потолок — только Binotel
        self.assertIn('const spanTooLong = isBinotel && rcSpanDays', self.journal)


if __name__ == '__main__':
    unittest.main()
