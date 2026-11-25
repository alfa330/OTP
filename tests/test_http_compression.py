# -*- coding: utf-8 -*-
"""Сжатие ответов API gzip-ом (common/http_compression.py).

25.11.2026, после переезда с Render: шлюз Yandex Cloud ответы не сжимает, сетка «Графиков
работы» шла 19 МБ и грузилась до 100 с — всё это время у людей «Нет смены». Здесь: большой JSON
сжимается и разжимается в то же самое, потоки (SSE), файлы, бинарное, маленькое и уже сжатое — нет,
клиент без gzip получает прежний JSON, Vary: Origin от CORS не теряется, и в монолите сжатие
стоит первым after_request — то есть отрабатывает последним.
"""
import ast
import gzip
import io
import json
import os
import pathlib
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, Response, jsonify, send_file, stream_with_context  # noqa: E402

from common import http_compression  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BROWSER = {'Accept-Encoding': 'gzip, deflate, br, zstd'}
BIG = {'operators': [{'name': 'Иванов Иван', 'shifts': {'2026-11-25': [{'start': '08:00', 'end': '20:00'}]}, 'n': i}
                     for i in range(300)]}


def _app():
    app = Flask(__name__)
    http_compression.install(app)

    @app.after_request
    def cors_like(response):
        # Как bot_schedule2.after_request: зарегистрирован позже — отрабатывает раньше и Vary затирает.
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Origin'] = 'https://icorehub.kz'
        return response

    @app.get('/big')
    def big():
        return jsonify(BIG)

    @app.get('/small')
    def small():
        return jsonify({'ok': True})

    @app.get('/sse')
    def sse():
        def gen():
            for index in range(3):
                yield 'data: %d\n\n' % index
                time.sleep(0.01)
        return Response(stream_with_context(gen()), mimetype='text/event-stream')

    @app.get('/file')
    def file():
        return send_file(io.BytesIO(b'{"a": 1}' * 1000), mimetype='application/json', download_name='a.json')

    @app.get('/xlsx')
    def xlsx():
        return Response(b'PK' + b'\x00' * 5000,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.get('/pre-gzipped')
    def pre_gzipped():
        response = Response(gzip.compress(json.dumps(BIG).encode()), mimetype='application/json')
        response.headers['Content-Encoding'] = 'gzip'
        return response

    @app.get('/no-transform')
    def no_transform():
        response = jsonify(BIG)
        response.headers['Cache-Control'] = 'no-store, no-transform'
        return response

    @app.get('/etag')
    def etag():
        response = jsonify(BIG)
        response.set_etag('v1')
        return response

    @app.get('/error')
    def error():
        return jsonify({'error': 'x' * 3000}), 500

    @app.get('/html')
    def html():
        return '<p>' + 'строка ' * 500 + '</p>'

    return app


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.client = _app().test_client()

    def test_big_json_is_gzipped_for_a_browser(self):
        response = self.client.get('/big', headers=BROWSER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        body = response.get_data()
        self.assertEqual(int(response.headers['Content-Length']), len(body))
        self.assertEqual(json.loads(gzip.decompress(body)), BIG)
        self.assertLess(len(body), len(json.dumps(BIG).encode()) / 5)
        self.assertEqual(response.mimetype, 'application/json')

    def test_cors_vary_is_kept_and_accept_encoding_added(self):
        for headers in (BROWSER, {}):
            response = self.client.get('/big', headers=headers)
            vary = {item.strip() for item in response.headers['Vary'].split(',')}
            self.assertEqual(vary, {'Origin', 'Accept-Encoding'}, headers)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://icorehub.kz')

    def test_client_without_gzip_gets_plain_json(self):
        for accept in (None, 'identity', 'br', 'gzip;q=0', 'br, *;q=0'):
            headers = {'Accept-Encoding': accept} if accept is not None else {}
            response = self.client.get('/big', headers=headers)
            self.assertNotIn('Content-Encoding', response.headers, accept)
            self.assertEqual(response.get_json(), BIG, accept)

    def test_wildcard_and_quality_count_as_consent(self):
        for accept in ('*', 'GZIP', 'gzip;q=0.5', 'deflate, *;q=0.1'):
            response = self.client.get('/big', headers={'Accept-Encoding': accept})
            self.assertEqual(response.headers.get('Content-Encoding'), 'gzip', accept)

    def test_small_response_is_left_alone(self):
        response = self.client.get('/small', headers=BROWSER)
        self.assertNotIn('Content-Encoding', response.headers)
        self.assertEqual(response.headers['Vary'], 'Origin')
        self.assertEqual(response.get_json(), {'ok': True})

    def test_streams_are_not_buffered_or_compressed(self):
        response = self.client.get('/sse', headers=BROWSER, buffered=False)
        self.assertTrue(response.is_streamed)
        self.assertNotIn('Content-Encoding', response.headers)
        chunks = [chunk for chunk in response.response if chunk]
        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(b''.join(chunks), b'data: 0\n\ndata: 1\n\ndata: 2\n\n')
        response.close()

    def test_files_binary_and_already_encoded_are_left_alone(self):
        for path in ('/file', '/xlsx', '/no-transform'):
            response = self.client.get(path, headers=BROWSER)
            self.assertNotIn('Content-Encoding', response.headers, path)
            response.close()
        response = self.client.get('/pre-gzipped', headers=BROWSER)
        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertEqual(json.loads(gzip.decompress(response.get_data())), BIG)

    def test_head_is_not_compressed(self):
        response = self.client.head('/big', headers=BROWSER)
        self.assertNotIn('Content-Encoding', response.headers)

    def test_errors_and_html_are_compressed_too(self):
        response = self.client.get('/error', headers=BROWSER)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(gzip.decompress(response.get_data())), {'error': 'x' * 3000})
        response = self.client.get('/html', headers=BROWSER)
        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertIn('строка', gzip.decompress(response.get_data()).decode('utf-8'))

    def test_etag_differs_for_the_compressed_body(self):
        plain = self.client.get('/etag')
        packed = self.client.get('/etag', headers=BROWSER)
        self.assertEqual(plain.headers['ETag'], '"v1"')
        self.assertEqual(packed.headers['ETag'], '"v1-gzip"')


class MonolithWiringTests(unittest.TestCase):
    """В bot_schedule2.py сжатие подключено сразу после app = Flask(...), до любого after_request."""

    def test_install_is_the_first_after_request(self):
        tree = ast.parse((ROOT / 'bot_schedule2.py').read_text(encoding='utf-8'))
        app_line = install_line = None
        hook_lines = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'app' for t in node.targets)
                    and isinstance(node.value, ast.Call) and getattr(node.value.func, 'id', '') == 'Flask'):
                app_line = node.lineno
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'install'
                    and getattr(node.func.value, 'id', '') == 'http_compression'):
                install_line = node.lineno
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if (isinstance(deco, ast.Attribute) and deco.attr == 'after_request'
                            and getattr(deco.value, 'id', '') == 'app'):
                        hook_lines.append(node.lineno)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'after_request' and getattr(node.func.value, 'id', '') == 'app'):
                hook_lines.append(node.lineno)
        self.assertIsNotNone(app_line)
        self.assertIsNotNone(install_line, 'http_compression.install(app) пропал из bot_schedule2.py')
        self.assertGreater(install_line, app_line)
        self.assertTrue(hook_lines)
        self.assertLess(install_line, min(hook_lines),
                        'сжатие должно регистрироваться раньше остальных after_request — тогда оно отрабатывает последним')


if __name__ == '__main__':
    unittest.main()
