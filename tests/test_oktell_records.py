# -*- coding: utf-8 -*-
"""Записи разговоров Oktell через коннектор на VM шлюза (12.11.2026).

Мост Oktell лежит с 10.11.2026, а файлы записей портал брал только у него — «Случайный звонок»,
ежедневная выборка «ИИ-оценки» и ночное «Деление звонков» СЗоВ получали «Не удалось получить
аудиозапись из Oktell». Теперь файл приносит исполнитель коннектора: портал кладёт задание с
относительными путями, исполнитель берёт файл с веб-сервера Oktell и возвращает его следующим
опросом.

Что закреплено:
  * исполнитель скачивает только файл записи: путь обязан совпасть со строгим шаблоном имени,
    `..`, лишние папки и параметры не проходят, и до сети такое задание не доходит;
  * 404 — «файла нет» (не ошибка), файл больше потолка, не MP3/WAV, чужой сертификат, обрыв —
    причина с видом отказа, по которому портал решает, можно ли спросить мост;
  * для HTTPS отпечаток сертификата обязателен — цепочку Oktell не отдаёт;
  * портал проверяет принесённое сам: путь из запрошенных, тип audio/…, честный base64, потолок;
  * портал просит запись у шлюза, только если исполнитель сказал, что умеет; старый коннектор
    задание на запись отклоняет, и тогда (и только тогда) идёт запасной путь — мост;
  * скачивание, которое шлюз начал и не довёл, мосту не повторяется;
  * пути, которые строит монолит, проходят шаблон исполнителя (иначе шлюз отказывал бы всем).
"""
import ast
import base64
import io
import logging
import os
import sys
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from unittest import mock
from urllib.parse import quote, urlparse

import requests
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr_bridge import oktell_connector, oktell_jobs, oktell_records  # noqa: E402
from oktell_live import jobs as live_jobs  # noqa: E402
from tests import source_cache  # noqa: E402
from tests.test_oktell_jobs import Harness  # noqa: E402

BASE = 'https://192.0.2.24/data/0f0f0f0f-0000-4000-8000-000000000000/record/'
FINGERPRINT = 'AB:' * 31 + 'AB'
CONN_ID = '2b1f8608-1dc7-4059-945a-043b6d3aad9c'
MP3_PATH = '20261112/0923/mix_13003_17e006__2026_11_12__09_23_30_923.mp3'
WAV_PATH = MP3_PATH[:-4] + '.wav'
MP3 = bytes([0xFF, 0xE3]) + b'\x00' * 2000
WAV = b'RIFF' + bytes(4) + b'WAVE' + bytes(200)


class FakeResponse:
    def __init__(self, status=200, body=b'', headers=None, stream_error=None):
        self.status = status
        self.body = body
        self.headers = dict(headers or {})
        self.stream_error = stream_error
        self.released = self.closed = self.drained = False

    def stream(self, amount):
        for start in range(0, len(self.body), amount):
            yield self.body[start:start + amount]
        if self.stream_error is not None:
            raise self.stream_error

    def drain_conn(self):
        self.drained = True

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeHttp:
    """Пул urllib3: ответ по окончанию адреса, остальное — 404."""

    def __init__(self, by_suffix=None, error=None):
        self.by_suffix = by_suffix or {}
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        for suffix, response in self.by_suffix.items():
            if url.endswith(suffix):
                return response
        return FakeResponse(404)


def mp3_response(body=MP3, content_type='audio/mpeg'):
    return FakeResponse(200, body, {'Content-Type': content_type, 'Content-Length': str(len(body))})


def records(http, **kwargs):
    return oktell_records.Records(BASE, FINGERPRINT, http=http, **kwargs)


class PathTests(unittest.TestCase):

    def test_real_names_pass(self):
        for path in (MP3_PATH, WAV_PATH, '20260804/1006/mix_13130_17e010__2026_08_04__10_06_17_243.mp3'):
            with self.subTest(path=path):
                self.assertEqual(oktell_records.clean_path(path), path)

    def test_anything_else_is_refused(self):
        for path in ('', '../../Server.exe.config', '/' + MP3_PATH, MP3_PATH.replace('/', '\\'),
                     '20261112/0923/../../../oktell.ini', MP3_PATH + '?x=1', MP3_PATH + '#a',
                     '20261112/0923/mix_13003_17e006__2026_11_12__09_23_30_923.exe',
                     '20261112/0923/x/mix_13003_17e006__2026_11_12__09_23_30_923.mp3',
                     '20261112/0923/mix_13%2F03_17e006__2026_11_12__09_23_30_923.mp3',
                     '20261112/0923/mix_13003_17e006__2026_11_12__09_23_30_923.mp3\n',
                     'C:/Program Files (x86)/Oktell/Server/WWW/data/x/record/' + MP3_PATH):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    oktell_records.clean_path(path)

    def test_portal_paths_pass_the_gateway_template(self):
        """Пути строит монолит (_oktell_record_relative_paths) — они обязаны проходить шаблон
        исполнителя, иначе шлюз отказывал бы во всех записях."""
        source = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
        module = source_cache.parse(source)
        node = next(item for item in module.body if isinstance(item, ast.FunctionDef)
                    and item.name == '_oktell_record_relative_paths')
        ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot_schedule2.py', 'exec'), ns)
        build = ns['_oktell_record_relative_paths']
        for line_a, line_b, stamp in (('17e014', '13001', '2026-08-20 14:39:11.130'),
                                      ('13003', '17e006', '2026-11-12 09:23:30.923'),
                                      ('13146', '17e008', '2026-11-09 10:37:12.97'),
                                      ('13185', '17e006', '2026-11-10 11:42:13')):
            for path in build(line_a, line_b, stamp):
                with self.subTest(path=path):
                    self.assertEqual(oktell_records.clean_path(path), path)


class SettingsTests(unittest.TestCase):

    def test_base_url_and_fingerprint(self):
        self.assertEqual(oktell_records.clean_base_url('https://h/data/x/record'), 'https://h/data/x/record/')
        for bad in ('', 'ftp://h/x/', 'https:///x/', 'https://user:pw@h/x/', 'https://h/x/?a=1', 'https://h/x/#f'):
            with self.subTest(url=bad):
                with self.assertRaises(ValueError):
                    oktell_records.clean_base_url(bad)
        self.assertEqual(oktell_records.clean_fingerprint(FINGERPRINT), 'ab' * 32)
        for bad in ('', 'ab' * 20, 'zz' * 32, 'ab' * 33):
            with self.subTest(fingerprint=bad):
                with self.assertRaises(ValueError):
                    oktell_records.clean_fingerprint(bad)

    def test_https_needs_a_pinned_fingerprint(self):
        with self.assertRaisesRegex(ValueError, 'отпечаток'):
            oktell_records.Records(BASE)
        plain = oktell_records.Records('http://192.0.2.24:4055/data/x/record/')
        self.assertIsNone(plain.fingerprint)

    def test_real_pool_checks_the_fingerprint_instead_of_the_chain(self):
        pinned = oktell_records.Records(BASE, FINGERPRINT)
        self.assertEqual(pinned.http.connection_pool_kw['assert_fingerprint'], 'ab' * 32)
        self.assertEqual(pinned.http.connection_pool_kw['cert_reqs'], 'CERT_NONE')
        self.assertNotIn('0f0f0f0f', pinned.describe(), 'путь каталога в журнал не идёт')

    def test_not_configured_means_none_and_bad_config_is_loud(self):
        self.assertIsNone(oktell_records.Records.from_config({'record_url': ''}))
        with self.assertLogs(oktell_connector.log, level='ERROR') as captured:
            self.assertIsNone(oktell_connector.build_records({'record_url': BASE, 'record_tls_sha256': ''}))
        self.assertIn('записи разговоров выключены', captured.output[0])


class FetchTests(unittest.TestCase):

    def test_found_file_comes_back_with_its_type(self):
        response = mp3_response()
        http = FakeHttp({MP3_PATH: response})
        self.assertEqual(records(http).fetch(MP3_PATH), (MP3, 'audio/mpeg'))
        method, url, kwargs = http.calls[0]
        self.assertEqual((method, url), ('GET', BASE + MP3_PATH))
        self.assertFalse(kwargs['preload_content'], 'файл читается потоком, с потолком')
        self.assertFalse(kwargs['redirect'])
        self.assertTrue(response.released)

    def test_missing_file_is_none_not_an_error(self):
        response = FakeResponse(404)
        self.assertIsNone(records(FakeHttp({MP3_PATH: response})).fetch(MP3_PATH))
        self.assertTrue(response.drained and response.released)

    def test_server_error_is_a_record_failure(self):
        for status in (401, 403, 500, 301):
            with self.subTest(status=status):
                with self.assertRaises(oktell_records.RecordError) as caught:
                    records(FakeHttp({MP3_PATH: FakeResponse(status)})).fetch(MP3_PATH)
                self.assertEqual(caught.exception.kind, 'record')
                self.assertIn(str(status), str(caught.exception))

    def test_size_cap_by_header_and_by_stream(self):
        by_header = FakeResponse(200, MP3, {'Content-Length': str(10 ** 9), 'Content-Type': 'audio/mpeg'})
        with self.assertRaises(oktell_records.RecordError) as caught:
            records(FakeHttp({MP3_PATH: by_header})).fetch(MP3_PATH)
        self.assertEqual(caught.exception.kind, 'big')
        self.assertTrue(by_header.closed)
        streamed = FakeResponse(200, MP3 * 3, {'Content-Type': 'audio/mpeg'})
        with self.assertRaises(oktell_records.RecordError) as caught:
            records(FakeHttp({MP3_PATH: streamed}), max_bytes=len(MP3)).fetch(MP3_PATH)
        self.assertEqual(caught.exception.kind, 'big')
        self.assertTrue(streamed.closed and streamed.released)

    def test_not_audio_is_refused(self):
        page = FakeResponse(200, b'<html>login</html>', {'Content-Type': 'text/html'})
        with self.assertRaises(oktell_records.RecordError) as caught:
            records(FakeHttp({MP3_PATH: page})).fetch(MP3_PATH)
        self.assertEqual(caught.exception.kind, 'record')

    def test_wav_and_missing_content_type(self):
        wav = FakeResponse(200, WAV, {})
        self.assertEqual(records(FakeHttp({WAV_PATH: wav})).fetch(WAV_PATH), (WAV, 'audio/wav'))
        tagged = FakeResponse(200, b'ID3' + bytes(40), {'Content-Type': 'application/octet-stream'})
        self.assertEqual(records(FakeHttp({MP3_PATH: tagged})).fetch(MP3_PATH)[1], 'audio/mpeg')

    def test_network_failures_are_named(self):
        cases = (
            (urllib3.exceptions.MaxRetryError(None, BASE, reason=urllib3.exceptions.SSLError(
                'Fingerprints did not match. Expected "ab", got "cd"')), 'tls', 'не совпал'),
            (urllib3.exceptions.MaxRetryError(None, BASE, reason=urllib3.exceptions.NewConnectionError(
                None, 'Failed to establish a new connection: [Errno 111] Connection refused')),
             'unavailable', 'не принял соединение'),
            (urllib3.exceptions.MaxRetryError(None, BASE, reason=urllib3.exceptions.ConnectTimeoutError(
                'timed out')), 'unavailable', 'не принял соединение'),
            (urllib3.exceptions.MaxRetryError(None, BASE, reason=urllib3.exceptions.ReadTimeoutError(
                None, BASE, 'Read timed out')), 'timeout', 'не отдал файл'),
            (urllib3.exceptions.ProtocolError('Connection aborted.'), 'record', 'обрыв'),
        )
        for error, kind, text in cases:
            with self.subTest(kind=kind, error=type(error).__name__):
                with self.assertRaises(oktell_records.RecordError) as caught:
                    records(FakeHttp(error=error)).fetch(MP3_PATH)
                self.assertEqual(caught.exception.kind, kind)
                self.assertIn(text, str(caught.exception))

    def test_break_in_the_middle_of_the_file(self):
        broken = FakeResponse(200, MP3, {'Content-Type': 'audio/mpeg'},
                              stream_error=urllib3.exceptions.ProtocolError('Connection broken'))
        with self.assertRaises(oktell_records.RecordError) as caught:
            records(FakeHttp({MP3_PATH: broken})).fetch(MP3_PATH)
        self.assertEqual(caught.exception.kind, 'record')
        self.assertTrue(broken.released)


class ExecuteTests(unittest.TestCase):

    def test_first_found_path_wins_and_mp3_goes_first(self):
        http = FakeHttp({WAV_PATH: FakeResponse(200, WAV, {'Content-Type': 'audio/wav'})})
        result = records(http).execute({'id': 'j1', 'kind': 'record', 'paths': [MP3_PATH, WAV_PATH]})
        self.assertEqual((result['ok'], result['found'], result['path']), (True, True, WAV_PATH))
        self.assertEqual(base64.b64decode(result['audio']), WAV)
        self.assertEqual([call[1] for call in http.calls], [BASE + MP3_PATH, BASE + WAV_PATH])

    def test_no_file_is_found_false(self):
        result = records(FakeHttp()).execute({'id': 'j1', 'kind': 'record', 'paths': [MP3_PATH, WAV_PATH]})
        self.assertEqual(result, {'id': 'j1', 'ok': True, 'found': False})

    def test_foreign_paths_never_reach_the_network(self):
        http = FakeHttp()
        for paths in ([MP3_PATH, '../../Server.exe.config'], [], [MP3_PATH] * 5, 'abc', None,
                      [{'path': MP3_PATH}]):
            with self.subTest(paths=str(paths)[:40]):
                result = records(http).execute({'id': 'j1', 'kind': 'record', 'paths': paths})
                self.assertEqual((result['ok'], result['reason']), (False, 'params'))
        self.assertEqual(http.calls, [])

    def test_failure_is_reported_with_its_kind(self):
        http = FakeHttp({MP3_PATH: FakeResponse(500)})
        result = records(http).execute({'id': 'j1', 'kind': 'record', 'paths': [MP3_PATH, WAV_PATH]})
        self.assertEqual((result['ok'], result['reason']), (False, 'record'))
        self.assertEqual(len(http.calls), 1, 'после отказа сервера второй путь не пробуем')

    def test_stats_are_counted_for_the_summary(self):
        source = records(FakeHttp({MP3_PATH: mp3_response()}))
        source.execute({'id': '1', 'kind': 'record', 'paths': [MP3_PATH]})
        source.execute({'id': '2', 'kind': 'record', 'paths': [WAV_PATH]})
        stats = source.take_stats()
        self.assertEqual((stats['found'], stats['missing'], stats['bytes']), (1, 1, len(MP3)))
        self.assertEqual(source.take_stats()['found'], 0)


class PortalResultTests(unittest.TestCase):
    """Что принёс исполнитель, портал проверяет сам."""

    def item(self, **overrides):
        base = {'id': 'x', 'ok': True, 'found': True, 'path': MP3_PATH, 'content_type': 'audio/mpeg',
                'audio': base64.b64encode(MP3).decode('ascii')}
        base.update(overrides)
        return base

    def test_good_file(self):
        result = live_jobs.record_result(self.item(), (MP3_PATH, WAV_PATH))
        self.assertEqual((result['ok'], result['found'], result['audio']), (True, True, MP3))

    def test_bad_answers_are_failures(self):
        for overrides in ({'path': WAV_PATH.replace('0923', '0924')}, {'content_type': 'text/html'},
                          {'audio': 'не base64!'}, {'audio': ''}, {'audio': None}, {'path': None}):
            with self.subTest(overrides=str(overrides)[:40]):
                result = live_jobs.record_result(self.item(**overrides), (MP3_PATH, WAV_PATH))
                self.assertEqual((result['ok'], result['reason']), (False, 'record'))

    def test_too_big_is_refused_before_decoding(self):
        with mock.patch.object(live_jobs, '_MAX_RECORD_B64', 8):
            result = live_jobs.record_result(self.item(), (MP3_PATH,))
        self.assertEqual(result['reason'], 'big')

    def test_answer_without_found_is_not_a_quiet_missing(self):
        for item in ({'ok': True}, {'ok': True, 'rows': []}, {'ok': True, 'found': 'yes'}):
            with self.subTest(item=item):
                result = live_jobs.record_result(item, (MP3_PATH,))
                self.assertEqual((result['ok'], result['reason']), (False, 'record'))

    def test_missing_and_refusal_pass_through(self):
        self.assertEqual(live_jobs.record_result({'ok': True, 'found': False}, (MP3_PATH,)),
                         {'ok': True, 'found': False})
        refusal = live_jobs.record_result({'ok': False, 'reason': 'params', 'error': 'путь'}, (MP3_PATH,))
        self.assertEqual((refusal['ok'], refusal['reason']), (False, 'params'))


class RoundTripTests(Harness):
    """Портал → настоящая ручка заданий → исполнитель → файл обратно ждущему потоку."""

    def serve_with(self, http, index=0):
        worker = oktell_jobs.Worker(index, 'https://portal.example', self.session, self.signer, self.db,
                                    lambda: {'agent_id': 'gw-1', 'hostname': 'gw',
                                             'version': 'oktell/1.2.0/3.12'},
                                    self.stop, sleep=lambda seconds: None, records=records(http))
        return self.serve(worker)

    def test_file_comes_back_to_the_waiting_caller(self):
        self.serve_with(FakeHttp({MP3_PATH: mp3_response()}))
        self.assertEqual(self.jobs.records_why_not(), '')
        self.assertEqual(self.jobs.fetch_record([MP3_PATH, WAV_PATH], 5), (MP3, 'audio/mpeg'))
        self.assertEqual(self.jobs.status()['workers'][0]['records'], True)
        sent = [job for reply in self.session.replies
                for job in (__import__('json').loads(reply).get('jobs') or [])]
        self.assertEqual(sent, [{'id': sent[0]['id'], 'kind': 'record', 'paths': [MP3_PATH, WAV_PATH]}],
                         'на шлюз уходят только пути')

    def test_missing_file_is_none(self):
        self.serve_with(FakeHttp())
        self.assertIsNone(self.jobs.fetch_record([MP3_PATH, WAV_PATH], 5))

    def test_server_failure_is_failed_not_refused(self):
        self.serve_with(FakeHttp({MP3_PATH: FakeResponse(500)}))
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Failed) as caught:
                self.jobs.fetch_record([MP3_PATH], 5)
        self.assertIn('ответил 500', str(caught.exception))

    def test_unreachable_web_server_is_refused(self):
        error = urllib3.exceptions.MaxRetryError(None, BASE, reason=urllib3.exceptions.NewConnectionError(
            None, 'Connection refused'))
        self.serve_with(FakeHttp(error=error))
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Refused) as caught:
                self.jobs.fetch_record([MP3_PATH], 5)
        self.assertIn('не достучался до веб-сервера Oktell', str(caught.exception))

    def test_worker_without_records_is_not_asked(self):
        self.serve()                                  # исполнитель 1.2.0 без OKTELL_RECORD_URL
        self.assertIn('OKTELL_RECORD_URL', self.jobs.records_why_not())
        self.assertFalse(self.jobs.status()['workers'][0]['records'])

    def test_old_connector_is_not_asked_and_refuses_if_asked(self):
        self.jobs.worker_seen('gw-old#0', version='oktell/1.1.0', catalog='x', datasets=None)
        self.assertIn('не умеет приносить записи', self.jobs.records_why_not())
        # Если задание всё же дошло до исполнителя 1.1.0, он видит в нём «набор без имени».
        old = oktell_jobs.execute(self.db, {'id': '1', 'kind': 'record', 'paths': [MP3_PATH]})
        self.assertEqual((old['ok'], old['reason']), (False, 'unknown'))

    def test_record_job_does_not_touch_the_database(self):
        self.serve_with(FakeHttp({MP3_PATH: mp3_response()}))
        before = list(self.db.queries)
        self.jobs.fetch_record([MP3_PATH], 5)
        self.assertEqual(self.db.queries, before)

    def test_no_paths_is_refused_without_a_job(self):
        with self.assertRaises(live_jobs.Refused):
            self.jobs.fetch_record([], 5)
        self.assertEqual(self.jobs.status()['stats'].get('submitted', 0), 0)


def _monolith_namespace(session):
    wanted = {'_oktell_normalize_conn_id', '_oktell_record_url', '_oktell_record_path_url',
              '_oktell_record_pace', '_oktell_record_get', '_oktell_detect_audio_format',
              '_oktell_download_record', '_oktell_bridge_download_record', '_oktell_bridge_ready'}
    source = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
    module = source_cache.parse(source)
    nodes = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    ns = {'uuid': uuid, 'quote': quote, 'urlparse': urlparse, 'logging': logging, 'threading': threading,
          'time': __import__('time'), 'requests': requests,
          'OKTELL_API_URL': 'http://oktell-proxy.test:8085/query', 'OKTELL_API_TOKEN': 'secret',
          'OKTELL_API_TIMEOUT_SECONDS': 5, 'OKTELL_API_CONNECT_TIMEOUT_SECONDS': 5,
          'OKTELL_RECORD_MIN_INTERVAL_MS': 0, '_oktell_session': session,
          '_oktell_record_pace_lock': threading.Lock(), '_oktell_record_pace_last': [0.0],
          '_oktell_dropped_keepalive': lambda _exc: False}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot_schedule2.py', 'exec'), ns)
    return ns


class BridgeSession:
    def __init__(self, error=None, response=None):
        self.error = error
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.response


class _BridgeResponse:
    def __init__(self, status_code=200, content=MP3):
        self.status_code = status_code
        self.content = content
        self.headers = {'Content-Type': 'audio/mpeg'}
        self.text = ''


class MonolithRoadTests(Harness):
    """_oktell_download_record: сначала шлюз, мост — только когда шлюз за файл не взялся."""

    def setUp(self):
        super().setUp()
        for name in ('records_why_not', 'fetch_record'):
            patch = mock.patch.object(live_jobs, name, getattr(self.jobs, name))
            patch.start()
            self.addCleanup(patch.stop)

    def serve_with(self, http):
        return RoundTripTests.serve_with(self, http)

    def test_gateway_brings_the_file_and_the_bridge_is_not_touched(self):
        bridge = BridgeSession(response=_BridgeResponse())
        ns = _monolith_namespace(bridge)
        self.serve_with(FakeHttp({MP3_PATH: mp3_response()}))
        self.assertEqual(ns['_oktell_download_record'](CONN_ID, [MP3_PATH, WAV_PATH]), (MP3, 'audio/mpeg'))
        self.assertEqual(bridge.calls, [])

    def test_missing_on_the_gateway_is_missing(self):
        bridge = BridgeSession(response=_BridgeResponse())
        ns = _monolith_namespace(bridge)
        self.serve_with(FakeHttp())
        self.assertIsNone(ns['_oktell_download_record'](CONN_ID, [MP3_PATH, WAV_PATH]))
        self.assertEqual(bridge.calls, [], 'нет файла — это ответ, у моста его не перепроверяем')

    def test_failed_on_the_gateway_is_not_repeated_on_the_bridge(self):
        bridge = BridgeSession(response=_BridgeResponse())
        ns = _monolith_namespace(bridge)
        self.serve_with(FakeHttp({MP3_PATH: FakeResponse(500)}))
        with self.assertLogs(live_jobs.log, level='WARNING'):
            with self.assertRaises(live_jobs.Failed):
                ns['_oktell_download_record'](CONN_ID, [MP3_PATH])
        self.assertEqual(bridge.calls, [])

    def test_no_gateway_means_the_bridge(self):
        bridge = BridgeSession(response=_BridgeResponse())
        ns = _monolith_namespace(bridge)
        self.assertEqual(ns['_oktell_download_record'](CONN_ID, [MP3_PATH]), (MP3, 'audio/mpeg'))
        self.assertIn('/record?path=' + MP3_PATH, bridge.calls[0])

    def test_no_paths_means_the_bridge_even_with_a_gateway(self):
        bridge = BridgeSession(response=_BridgeResponse())
        ns = _monolith_namespace(bridge)
        self.serve_with(FakeHttp({MP3_PATH: mp3_response()}))
        self.assertEqual(ns['_oktell_download_record'](CONN_ID, None), (MP3, 'audio/mpeg'))
        self.assertTrue(bridge.calls[0].endswith('/record/' + CONN_ID))

    def test_both_roads_closed_names_the_gateway_reason(self):
        """Ровно сегодняшняя поломка, но с коннектором старой версии: человеку — причина на
        стороне шлюза; отказ моста — следствие, он в __cause__."""
        bridge = BridgeSession(error=requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='89.107.98.195', port=8085): Connection refused"))
        ns = _monolith_namespace(bridge)
        self.jobs.worker_seen('gw-old#0', version='oktell/1.1.0', catalog='x', datasets=None)
        with self.assertRaises(RuntimeError) as caught:
            ns['_oktell_download_record'](CONN_ID, [MP3_PATH])
        self.assertIn('не умеет приносить записи', str(caught.exception))
        self.assertIn('Запасной путь (мост Oktell) тоже недоступен', str(caught.exception))
        self.assertNotIn('HTTPConnectionPool', str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, requests.exceptions.ConnectionError)

    def test_gateway_alone_without_a_bridge(self):
        ns = _monolith_namespace(BridgeSession())
        ns['OKTELL_API_URL'] = ''
        self.serve_with(FakeHttp({MP3_PATH: mp3_response()}))
        self.assertEqual(ns['_oktell_download_record'](CONN_ID, [MP3_PATH]), (MP3, 'audio/mpeg'))
        with self.assertRaises(RuntimeError) as caught:
            ns['_oktell_download_record'](CONN_ID, None)
        self.assertEqual(str(caught.exception), 'Запись Oktell: путь к записи не вычислен')


class ConnectorTests(unittest.TestCase):

    def test_settings_come_from_the_environment(self):
        env = {'OKTELL_RECORD_URL': BASE, 'OKTELL_RECORD_TLS_SHA256': FINGERPRINT}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(oktell_connector, '_env_file_values', return_value={}):
                config = oktell_connector.load_config()
        self.assertEqual((config['record_url'], config['record_tls_sha256']), (BASE, FINGERPRINT))

    def test_worker_tells_the_portal_whether_it_brings_records(self):
        stop = threading.Event()
        with_records = oktell_jobs.Worker(0, 'https://p', None, None, None, dict, stop,
                                          records=records(FakeHttp()))
        without = oktell_jobs.Worker(1, 'https://p', None, None, None, dict, stop)
        self.assertTrue(with_records._payload([])['records'])
        self.assertFalse(without._payload([])['records'])
        refusal = without.execute({'id': '7', 'kind': 'record', 'paths': [MP3_PATH]})
        self.assertEqual((refusal['ok'], refusal['reason']), (False, 'unknown'))

    def test_check_probes_the_web_server_and_fetches_one_record(self):
        connector = mock.Mock(records=records(FakeHttp({MP3_PATH: mp3_response()})), config={})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertTrue(oktell_connector.check_records(connector, MP3_PATH))
        text = out.getvalue()
        self.assertIn('совпал с отпечатком', text)
        self.assertIn('%d байт, audio/mpeg, mp3' % len(MP3), text)
        refused = mock.Mock(records=records(FakeHttp({oktell_records.PROBE_PATH: FakeResponse(401)})), config={})
        with redirect_stdout(io.StringIO()):
            self.assertFalse(oktell_connector.check_records(refused))

    def test_check_without_records(self):
        with redirect_stdout(io.StringIO()):
            self.assertTrue(oktell_connector.check_records(mock.Mock(records=None, config={})))
            self.assertFalse(oktell_connector.check_records(mock.Mock(records=None, config={'record_url': BASE})))


if __name__ == '__main__':
    unittest.main()
