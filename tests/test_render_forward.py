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
import urllib3

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


@pytest.fixture(autouse=True)
def _fresh_pause(monkeypatch):
    """Пауза пересылки — состояние модуля: каждый тест начинает с работающей пересылки."""
    monkeypatch.setattr(rf, '_direct_until', 0.0)
    monkeypatch.setattr(rf, '_direct_reason', '')
    monkeypatch.setattr(rf, 'MODE', 'auto')


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


def test_cookies_of_one_client_never_reach_another(forward):
    """25.11: общая сессия requests запомнила cookie входа из Set-Cookie и подставляла их всем
    запросам без своих cookie — вход остальных получал 403 «Invalid request origin», а запрос с
    просроченным токеном прошёл бы под чужой учёткой."""
    status, headers, _ = _call(forward, 'POST', '/api/login', body=b'{}',
                               headers={'Content-Type': 'application/json'})
    assert status.startswith('200')
    # Клиенту, который входил, Set-Cookie отдаются как есть.
    assert [v for k, v in headers if k.lower() == 'set-cookie'] == ['a=1; Path=/', 'b=2; Path=/']
    for method in ('GET', 'POST'):
        _, _, chunks = _call(forward, method, '/api/auth/me', body=b'{}' if method == 'POST' else b'',
                             headers={'Authorization': 'Bearer other'})
        seen = json.loads(b''.join(c for _, c in chunks))
        assert 'cookie' not in seen['headers'], seen['headers']


def test_own_cookie_passes_as_is(forward):
    _, _, chunks = _call(forward, 'GET', '/api/x', headers={'Cookie': 'otp_access_token=mine'})
    assert json.loads(b''.join(c for _, c in chunks))['headers']['cookie'] == 'otp_access_token=mine'


def test_compression_is_not_asked_for_the_client(forward):
    """Сжатия клиент не просил — пересылка его за него не просит: иначе старый клиент получил бы
    gzip, которого не умеет читать."""
    _, _, chunks = _call(forward, 'GET', '/api/x')
    seen = json.loads(b''.join(c for _, c in chunks))['headers']
    assert seen.get('accept-encoding') in (None, 'identity'), seen
    assert 'accept' not in seen, seen
    # А если просил — просьба доходит.
    _, _, chunks = _call(forward, 'GET', '/api/x', headers={'Accept-Encoding': 'gzip'})
    assert json.loads(b''.join(c for _, c in chunks))['headers']['accept-encoding'] == 'gzip'


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


class _Refused:
    """Подмена пула: соединение не открывается, как с Render до Yandex Cloud 26.11 с 04:33."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def urlopen(self, method, url, **kwargs):
        self.calls.append((method, url))
        raise self.exc


def _connect_timeout():
    return urllib3.exceptions.ConnectTimeoutError(
        None, 'Connection to api.icorehub.kz timed out. (connect timeout=3)')


def test_unreachable_upstream_sends_client_directly(monkeypatch):
    """Настоящий отказ в соединении (порт 9 закрыт) — не 502, а 307 на тот же путь и запрос
    нового адреса: старые программы стоят в Казахстане, и новый адрес им открыт."""
    monkeypatch.setattr(rf, 'TARGET', 'http://127.0.0.1:9')
    wsgi_path = '/api/x/я'.encode('utf-8').decode('latin-1')
    status, headers, chunks = _call(rf.app, 'POST', wsgi_path, query='a=1', body=b'{"login":"u"}',
                                    headers={'Content-Type': 'application/json'})
    assert status == '307 Temporary Redirect'
    assert dict(headers)['Location'] == 'http://127.0.0.1:9/api/x/%D1%8F?a=1'
    assert dict(headers)['Cache-Control'] == 'no-store'
    assert b''.join(c for _, c in chunks) == b''


def test_after_failure_everyone_goes_direct_without_waiting(monkeypatch):
    """Пока пауза, пересылку даже не пробуем: каждая попытка — до CONNECT_TIMEOUT ожидания."""
    monkeypatch.setattr(rf, 'TARGET', 'https://api.icorehub.kz')
    refused = _Refused(_connect_timeout())
    monkeypatch.setattr(rf, '_pool', refused)
    status, headers, _ = _call(rf.app, 'POST', '/api/login', body=b'{}')
    assert status.startswith('307') and dict(headers)['Location'] == 'https://api.icorehub.kz/api/login'
    assert len(refused.calls) == 1
    for method, path in (('POST', '/api/oktell_guard/heartbeat'), ('GET', '/api/operator/sip_settings'),
                         ('PUT', '/api/x'), ('DELETE', '/api/x'), ('HEAD', '/')):
        status, headers, _ = _call(rf.app, method, path, body=b'{}' if method in ('POST', 'PUT') else b'')
        assert status.startswith('307'), (method, path, status)
        assert dict(headers)['Location'] == 'https://api.icorehub.kz' + path
    assert len(refused.calls) == 1, refused.calls


def test_forwarding_is_tried_again_after_the_pause(monkeypatch, upstream):
    monkeypatch.setattr(rf, 'TARGET', upstream)
    real_pool = rf._pool
    monkeypatch.setattr(rf, '_pool', _Refused(_connect_timeout()))
    assert _call(rf.app, 'GET', '/api/x')[0].startswith('307')
    assert rf._direct_left() > rf.DIRECT_FOR_S - 5
    # Связь вернулась, но пауза ещё идёт — по-прежнему 307.
    monkeypatch.setattr(rf, '_pool', real_pool)
    assert _call(rf.app, 'GET', '/api/x')[0].startswith('307')
    # Пауза кончилась — запрос снова пересылается, и пауза снята совсем.
    monkeypatch.setattr(rf, '_direct_until', time.monotonic() - 1)
    status, _, chunks = _call(rf.app, 'GET', '/api/x')
    assert status.startswith('200') and json.loads(b''.join(c for _, c in chunks))['path'] == '/api/x'
    assert rf._direct_reason == '' and rf._direct_left() == 0


def test_success_of_an_older_request_does_not_lift_the_pause(monkeypatch, upstream):
    """Запрос, ушедший до паузы по живому соединению пула, может закончиться удачно уже во
    время паузы — это не значит, что новые соединения снова открываются."""
    monkeypatch.setattr(rf, 'TARGET', upstream)
    rf._pause_forwarding('ConnectTimeoutError: test')
    rf._forwarding_back()
    assert rf._direct_left() > 0 and rf._direct_reason


def test_post_that_may_have_reached_the_server_is_not_repeated(monkeypatch):
    """Ответ не пришёл уже после отправки (таймаут чтения, обрыв) — сервер мог запись провести.
    POST получает 502, а не 307: повтор напрямую провёл бы её дважды. Пауза при этом включается,
    и повтор клиента уходит по 307. GET в том же случае сразу идёт напрямую — он ничего не пишет."""
    monkeypatch.setattr(rf, 'TARGET', 'https://api.icorehub.kz')
    read_timeout = urllib3.exceptions.ReadTimeoutError(None, '/api/x', 'Read timed out. (read timeout=300)')
    monkeypatch.setattr(rf, '_pool', _Refused(read_timeout))
    status, _, chunks = _call(rf.app, 'POST', '/api/oktell_guard/violations', body=b'{}')
    assert status.startswith('502') and b''.join(c for _, c in chunks) == b'upstream unavailable'
    status, headers, _ = _call(rf.app, 'POST', '/api/oktell_guard/violations', body=b'{}')
    assert status.startswith('307')
    monkeypatch.setattr(rf, '_direct_until', 0.0)
    status, headers, _ = _call(rf.app, 'GET', '/api/oktell_guard/news')
    assert status.startswith('307')
    assert dict(headers)['Location'] == 'https://api.icorehub.kz/api/oktell_guard/news'


@pytest.mark.parametrize('exc', [
    urllib3.exceptions.NewConnectionError(None, 'Failed to establish a new connection: [Errno 111] refused'),
    urllib3.exceptions.NameResolutionError('api.icorehub.kz', None, OSError('no such host')),
])
def test_connection_errors_of_any_kind_send_post_directly(monkeypatch, exc):
    """Отказ, неразрешённое имя — тоже «соединение не открылось»: POST до сервера не дошёл."""
    monkeypatch.setattr(rf, 'TARGET', 'https://api.icorehub.kz')
    monkeypatch.setattr(rf, '_pool', _Refused(exc))
    status, headers, _ = _call(rf.app, 'POST', '/api/login', body=b'{}')
    assert status.startswith('307') and dict(headers)['Location'] == 'https://api.icorehub.kz/api/login'


def test_redirect_mode_never_forwards(monkeypatch, upstream):
    monkeypatch.setattr(rf, 'TARGET', upstream)
    monkeypatch.setattr(rf, 'MODE', 'redirect')
    refused = _Refused(AssertionError('в режиме redirect пересылки быть не должно'))
    monkeypatch.setattr(rf, '_pool', refused)
    status, headers, _ = _call(rf.app, 'POST', '/api/login', query='x=1', body=b'{}')
    assert status.startswith('307') and dict(headers)['Location'] == upstream + '/api/login?x=1'
    assert refused.calls == []


def test_health_tells_forward_or_direct(monkeypatch):
    monkeypatch.setattr(rf, 'TARGET', 'https://api.icorehub.kz')
    status, _, chunks = _call(rf.app, 'GET', rf.HEALTH_PATH)
    assert status.startswith('200') and b'(forward)' in b''.join(c for _, c in chunks)
    monkeypatch.setattr(rf, '_pool', _Refused(_connect_timeout()))
    _call(rf.app, 'GET', '/api/x')
    status, _, chunks = _call(rf.app, 'GET', rf.HEALTH_PATH)
    text = b''.join(c for _, c in chunks).decode()
    assert status.startswith('200') and 'direct' in text and 'ConnectTimeoutError' in text


def test_redirect_comes_before_the_old_guard_gives_up():
    """Старый «Oktell» ждёт ответа пересылки 10 с (request_timeout_s): 307 должен успеть раньше."""
    assert rf.CONNECT_TIMEOUT <= 5


class _Echo(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _echo(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        payload = json.dumps({'method': self.command, 'path': self.path, 'body': body.decode(),
                              'headers': {k.lower(): v for k, v in self.headers.items()}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _echo


def test_old_guard_login_goes_through_when_forwarding_is_down(monkeypatch):
    """Настоящая пересылка под waitress и настоящий requests, как во «Oktell» ≤1.0.35: новый
    сервер с Render недоступен, а вход оператора всё равно проходит — клиент сам повторяет POST
    с телом по 307 (Authorization requests на чужой хост не несёт, X-Agent-Token — несёт)."""
    requests = pytest.importorskip('requests')
    waitress = pytest.importorskip('waitress.server')
    direct = ThreadingHTTPServer(('127.0.0.1', 0), _Echo)
    threading.Thread(target=direct.serve_forever, daemon=True).start()
    # Другое имя хоста, как otp-2-fos4.onrender.com → api.icorehub.kz.
    monkeypatch.setattr(rf, 'TARGET', 'http://localhost:%d' % direct.server_address[1])
    monkeypatch.setattr(rf, '_pool', _Refused(_connect_timeout()))
    server = waitress.create_server(rf.app, host='127.0.0.1', port=0, threads=4)
    threading.Thread(target=server.run, daemon=True).start()
    base = 'http://127.0.0.1:%d' % int(server.effective_port)
    try:
        response = requests.post(base + '/api/login', timeout=10,
                                 json={'login': 'op', 'password': 'p', 'auth_transport': 'bearer'})
        seen = response.json()
        assert response.status_code == 200 and response.history[0].status_code == 307
        assert seen['method'] == 'POST' and seen['path'] == '/api/login'
        assert json.loads(seen['body']) == {'login': 'op', 'password': 'p', 'auth_transport': 'bearer'}
        response = requests.get(base + '/api/oktell_guard/version', timeout=10,
                                headers={'X-Agent-Token': 'machine', 'Authorization': 'Bearer t'})
        seen = response.json()
        assert response.status_code == 200 and seen['headers']['x-agent-token'] == 'machine'
        assert 'authorization' not in seen['headers']
    finally:
        server.close()
        direct.shutdown()


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
