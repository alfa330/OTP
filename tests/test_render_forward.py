"""Пересылка со старого адреса Render на новый сервер (deploy/render_forward.py).

Настоящий локальный HTTP-сервер вместо api.icorehub.kz: запрос должен дойти байт в байт
(метод, путь с кодированием, запрос, тело, заголовки подписи и токена), ответ — вернуться как
есть (код, редирект без перехода, сжатие), SSE — кусками по мере прихода."""
import importlib.util
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('render_forward', ROOT / 'deploy' / 'render_forward.py')
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _echo(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        payload = json.dumps({'method': self.command, 'path': self.path,
                              'headers': {k.lower(): v for k, v in self.headers.items()},
                              'body': body.decode('utf-8', 'replace')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Set-Cookie', 'a=1; Path=/')
        self.send_header('Set-Cookie', 'b=2; Path=/')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', 'https://icorehub.kz/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path.startswith('/sse'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            for index in range(3):
                data = ('data: %d\n\n' % index).encode()
                self.wfile.write(b'%x\r\n%s\r\n' % (len(data), data))
                self.wfile.flush()
                time.sleep(0.4)
            self.wfile.write(b'0\r\n\r\n')
            return
        if self.path.startswith('/error'):
            self.send_response(500)
            self.send_header('Content-Length', '4')
            self.end_headers()
            self.wfile.write(b'boom')
            return
        self._echo()

    do_POST = do_PUT = do_PATCH = do_DELETE = _echo


@pytest.fixture(scope='module')
def upstream():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield 'http://127.0.0.1:%d' % server.server_address[1]
    server.shutdown()


@pytest.fixture
def forward(upstream, monkeypatch):
    monkeypatch.setattr(rf, 'TARGET', upstream)
    return rf.app


def _call(app, method='GET', path='/', query='', body=b'', headers=None):
    environ = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
               'SERVER_NAME': 'otp-2-fos4.onrender.com', 'SERVER_PORT': '443', 'REMOTE_ADDR': '10.0.0.7',
               'wsgi.input': BytesIO(body), 'wsgi.url_scheme': 'https', 'HTTP_HOST': 'otp-2-fos4.onrender.com'}
    if body:
        environ['CONTENT_LENGTH'] = str(len(body))
    for name, value in (headers or {}).items():
        key = name.upper().replace('-', '_')
        environ[key if key in ('CONTENT_TYPE',) else 'HTTP_' + key] = value
    captured = {}

    def start_response(status, response_headers, exc_info=None):
        captured['status'] = status
        captured['headers'] = response_headers

    started = time.monotonic()
    chunks = []
    for chunk in app(environ, start_response):
        chunks.append((time.monotonic() - started, chunk))
    return captured['status'], captured['headers'], chunks


def test_request_goes_through_as_is(forward):
    status, headers, chunks = _call(
        forward, 'POST', '/api/oktell_guard/agent/poll', query='a=1&b=%D1%8F',
        body=json.dumps({'x': 'я'}).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer t', 'X-User-Id': '7',
                 'X-Agent-Signature': 'sig', 'Connection': 'keep-alive'})
    assert status.startswith('200')
    seen = json.loads(b''.join(c for _, c in chunks))
    assert seen['method'] == 'POST'
    assert seen['path'] == '/api/oktell_guard/agent/poll?a=1&b=%D1%8F'
    assert json.loads(seen['body']) == {'x': 'я'}
    assert seen['headers']['authorization'] == 'Bearer t'
    assert seen['headers']['x-user-id'] == '7'
    assert seen['headers']['x-agent-signature'] == 'sig'
    assert seen['headers']['x-forwarded-for'] == '10.0.0.7'
    assert seen['headers']['x-forwarded-host'] == 'otp-2-fos4.onrender.com'
    assert seen['headers']['host'].startswith('127.0.0.1:')
    # Оба Set-Cookie доходят отдельными заголовками.
    assert [v for k, v in headers if k.lower() == 'set-cookie'] == ['a=1; Path=/', 'b=2; Path=/']


def test_encoded_path_survives(forward):
    # Как отдаёт waitress (PEP 3333): байты пути UTF-8, прочитанные как latin-1.
    wsgi_path = '/api/x/a b/я'.encode('utf-8').decode('latin-1')
    _, _, chunks = _call(forward, 'GET', wsgi_path)
    assert json.loads(b''.join(c for _, c in chunks))['path'] == '/api/x/a%20b/%D1%8F'
    # И если сервер вдруг отдал уже раскодированный текст — тот же адрес, без падения.
    _, _, chunks = _call(forward, 'GET', '/api/x/a b/я')
    assert json.loads(b''.join(c for _, c in chunks))['path'] == '/api/x/a%20b/%D1%8F'


def test_redirect_is_returned_not_followed(forward):
    status, headers, _ = _call(forward, 'GET', '/redirect')
    assert status.startswith('302')
    assert dict(headers)['Location'] == 'https://icorehub.kz/'


def test_upstream_error_status_passes_through(forward):
    status, _, chunks = _call(forward, 'GET', '/error')
    assert status.startswith('500') and b''.join(c for _, c in chunks) == b'boom'


def test_sse_comes_in_pieces(forward):
    """Первое событие у клиента раньше, чем сервер отправил последнее: поток не копится."""
    status, headers, chunks = _call(forward, 'GET', '/sse')
    assert status.startswith('200')
    assert dict(headers)['Content-Type'] == 'text/event-stream'
    text = b''.join(c for _, c in chunks).decode()
    assert text == 'data: 0\n\ndata: 1\n\ndata: 2\n\n'
    assert chunks[0][0] < 0.35, chunks


def test_unreachable_upstream_is_502(monkeypatch):
    monkeypatch.setattr(rf, 'TARGET', 'http://127.0.0.1:9')
    status, _, _ = _call(rf.app, 'GET', '/api/health')
    assert status.startswith('502')


def test_health_does_not_touch_upstream(monkeypatch):
    monkeypatch.setattr(rf, 'TARGET', 'http://127.0.0.1:9')
    status, _, chunks = _call(rf.app, 'GET', rf.HEALTH_PATH)
    assert status.startswith('200') and b'127.0.0.1:9' in b''.join(c for _, c in chunks)


def test_refuses_to_start_without_target():
    """Без адреса назначения процесс падает, а не поднимает что-то ещё."""
    result = subprocess.run([sys.executable, str(ROOT / 'deploy' / 'render_forward.py')],
                            env={'PATH': '', 'SYSTEMROOT': __import__('os').environ.get('SYSTEMROOT', '')},
                            capture_output=True, timeout=60)
    assert result.returncode != 0
    assert 'OTP_FORWARD_TO' in result.stderr.decode('utf-8', 'replace')
