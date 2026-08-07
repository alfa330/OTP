"""HTTP-клиент прокси Oktell: keep-alive-сессия, раздельные таймауты, единственный ретрай.

Прокси — узкое место интеграции: он поднимает отдельное ODBC-подключение к SQL Server на
каждое HTTP-соединение и захлёбывается на приёме новых. Поэтому здесь проверяется не «умеет
ли requests», а наши обязательства перед ним: одно соединение на процесс, короткое ожидание
хендшейка, никаких ретраев по таймауту.

Функции вытаскиваем из bot_schedule2.py через ast: импортировать модуль нельзя — он на старте
поднимает пул к боевой БД.
"""
import ast
import logging
import unittest
from http.client import RemoteDisconnected
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]


def _load_names(source, names, namespace, label="<oktell-http>"):
    """Исполняет в namespace перечисленные функции и присваивания модульного уровня."""
    tree = ast.parse(source)
    wanted = set(names)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, label, "exec"), namespace)
    missing = sorted(name for name in wanted if name not in namespace)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return namespace


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload if payload is not None else {'rows': [{'ok': 1}]}
        self.text = text

    def json(self):
        return self._payload


class _FakeSession:
    """Сессия, считающая обращения и умеющая падать заданной цепочкой ошибок."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else _FakeResponse()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _namespace(outcomes=(), token='secret', url='http://proxy.test:8085/query'):
    source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
    session = _FakeSession(outcomes)
    ns = {
        'requests': requests,
        'logging': logging,
        'RemoteDisconnected': RemoteDisconnected,
        'OKTELL_API_URL': url,
        'OKTELL_API_TOKEN': token,
        'OKTELL_API_TIMEOUT_SECONDS': 60,
        'OKTELL_API_CONNECT_TIMEOUT_SECONDS': 5,
        '_oktell_api_ready': lambda: bool(url) and bool(token),
        '_oktell_session': session,
    }
    _load_names(source, {
        '_OKTELL_DROPPED_CONNECTION_ERRORS',
        '_oktell_dropped_keepalive',
        '_oktell_query',
    }, ns)
    ns['_fake_session'] = session
    return ns


def _dropped_keepalive_error():
    """Как это выглядит в жизни: requests -> urllib3 -> http.client."""
    inner = RemoteDisconnected("Remote end closed connection without response")
    return requests.exceptions.ConnectionError(
        requests.packages.urllib3.exceptions.ProtocolError("Connection aborted.", inner))


class OktellSessionTests(unittest.TestCase):
    """Одно TCP-соединение на процесс вместо нового на каждый SELECT."""

    def test_query_goes_through_the_shared_session(self):
        ns = _namespace()
        rows = ns['_oktell_query']("SELECT 1")
        self.assertEqual(rows, [{'ok': 1}])
        self.assertEqual(len(ns['_fake_session'].calls), 1)

    def test_real_session_reuses_one_connection_per_host(self):
        """Пул именно маленький: прокси низкоконкурентный, разгонять его нам нечем."""
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        ns = _load_names(source, {'_build_oktell_session', '_oktell_session'}, {'requests': requests})
        session = ns['_oktell_session']
        self.assertIsInstance(session, requests.Session)
        adapter = session.get_adapter('http://proxy.test:8085/query')
        self.assertEqual(adapter._pool_connections, 1)
        self.assertEqual(adapter._pool_maxsize, 4)

    def test_timeouts_are_split_into_connect_and_read(self):
        ns = _namespace()
        ns['_oktell_query']("SELECT 1")
        self.assertEqual(ns['_fake_session'].calls[0][1]['timeout'], (5, 60))

    def test_caller_timeout_overrides_only_the_read_half(self):
        """Табло сокращает ожидание ОТВЕТА; хендшейк и так ограничен коротко."""
        ns = _namespace()
        ns['_oktell_query']("SELECT 1", timeout=20)
        self.assertEqual(ns['_fake_session'].calls[0][1]['timeout'], (5, 20))

    def test_token_is_sent_on_every_request(self):
        ns = _namespace()
        ns['_oktell_query']("SELECT 1")
        self.assertEqual(ns['_fake_session'].calls[0][1]['headers'], {"X-API-Key": 'secret'})
        self.assertEqual(ns['_fake_session'].calls[0][1]['json'], {"sql": "SELECT 1"})

    def test_http_error_is_reported_with_the_body(self):
        ns = _namespace([_FakeResponse(status_code=500, text='{"detail":"Ошибка БД"}')])
        with self.assertRaises(RuntimeError) as ctx:
            ns['_oktell_query']("SELECT 1")
        self.assertIn('500', str(ctx.exception))
        self.assertIn('Ошибка БД', str(ctx.exception))


class OktellRetryTests(unittest.TestCase):
    """Ретрай ровно один и только на протухшем keep-alive.

    Повторять таймауты нельзя: прокси и без нас захлёбывается, а 83% его отказов — это как раз
    незавершённый хендшейк."""

    def test_dropped_keepalive_is_retried_once(self):
        ns = _namespace([_dropped_keepalive_error(), _FakeResponse()])
        rows = ns['_oktell_query']("SELECT 1")
        self.assertEqual(rows, [{'ok': 1}])
        self.assertEqual(len(ns['_fake_session'].calls), 2)

    def test_second_drop_in_a_row_is_not_retried_again(self):
        ns = _namespace([_dropped_keepalive_error(), _dropped_keepalive_error()])
        with self.assertRaises(requests.exceptions.ConnectionError):
            ns['_oktell_query']("SELECT 1")
        self.assertEqual(len(ns['_fake_session'].calls), 2)

    def test_connect_timeout_is_never_retried(self):
        ns = _namespace([requests.exceptions.ConnectTimeout(
            "Connection to 89.107.98.195 timed out. (connect timeout=5)")])
        with self.assertRaises(requests.exceptions.ConnectTimeout):
            ns['_oktell_query']("SELECT 1")
        self.assertEqual(len(ns['_fake_session'].calls), 1)

    def test_read_timeout_is_never_retried(self):
        ns = _namespace([requests.exceptions.ReadTimeout("Read timed out. (read timeout=20)")])
        with self.assertRaises(requests.exceptions.ReadTimeout):
            ns['_oktell_query']("SELECT 1")
        self.assertEqual(len(ns['_fake_session'].calls), 1)

    def test_refused_connection_is_never_retried(self):
        """Прокси лежит — повтор ничего не изменит, только добавит ему работы."""
        ns = _namespace([requests.exceptions.ConnectionError(
            requests.packages.urllib3.exceptions.NewConnectionError(
                None, "Failed to establish a new connection: [Errno 111] Connection refused"))])
        with self.assertRaises(requests.exceptions.ConnectionError):
            ns['_oktell_query']("SELECT 1")
        self.assertEqual(len(ns['_fake_session'].calls), 1)

    def test_http_500_is_not_a_retry_reason(self):
        ns = _namespace([_FakeResponse(status_code=500, text='Login timeout expired')])
        with self.assertRaises(RuntimeError):
            ns['_oktell_query']("SELECT 1")
        self.assertEqual(len(ns['_fake_session'].calls), 1)


class OktellDroppedConnectionDetectionTests(unittest.TestCase):
    """Причину ищем по всей цепочке исключений, а не по тексту сообщения."""

    def setUp(self):
        self.detect = _namespace()['_oktell_dropped_keepalive']

    def test_detects_remote_disconnected_wrapped_by_urllib3(self):
        self.assertTrue(self.detect(_dropped_keepalive_error()))

    def test_detects_reset_raised_as_cause(self):
        exc = requests.exceptions.ConnectionError("Connection aborted.")
        exc.__cause__ = ConnectionResetError(104, 'Connection reset by peer')
        self.assertTrue(self.detect(exc))

    def test_detects_broken_pipe_in_context(self):
        exc = requests.exceptions.ConnectionError("Connection aborted.")
        exc.__context__ = BrokenPipeError(32, 'Broken pipe')
        self.assertTrue(self.detect(exc))

    def test_plain_timeout_is_not_a_dropped_connection(self):
        self.assertFalse(self.detect(requests.exceptions.ConnectTimeout("connect timeout=5")))
        self.assertFalse(self.detect(requests.exceptions.ReadTimeout("read timeout=20")))

    def test_refused_connection_is_not_a_dropped_connection(self):
        self.assertFalse(self.detect(requests.exceptions.ConnectionError(
            ConnectionRefusedError(111, 'Connection refused'))))

    def test_cycles_do_not_hang_the_walk(self):
        first = requests.exceptions.ConnectionError("a")
        second = requests.exceptions.ConnectionError("b")
        first.__cause__ = second
        second.__cause__ = first
        self.assertFalse(self.detect(first))


if __name__ == '__main__':
    unittest.main()
