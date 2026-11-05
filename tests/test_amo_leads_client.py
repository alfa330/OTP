# -*- coding: utf-8 -*-
"""Клиент amoCRM: повтор запроса на обрыве соединения и зависшей передаче.

Одна выгрузка лидов — около 60 запросов подряд, и amoCRM время от времени
закрывает соединение на середине (на проде 5 прогонов из 39 за 06–10.08, всегда
через 12–13 с после старта), а с 05.11 ещё и замирает посреди отдачи страницы.
Без повтора обрыв выбрасывал все уже вычитанные страницы, и отбивка показывала
«выгрузка не удалась» до следующего синка через три часа. Проверяем именно наши
обязательства: повторяем обрыв уже установленного соединения и зависшую после
ответа передачу, но не таймаут до ответа и не отказ в соединении.
"""

import os
import sys

import pytest
import requests
import urllib3
from http.client import RemoteDisconnected

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amocrm import leads as amo_leads


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"_embedded": {"leads": []}}
        self.text = text

    def json(self):
        return self._payload


class _FakeSession:
    """Сессия, отдающая заготовленные ответы и ошибки по порядку."""

    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else _FakeResponse()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(outcomes=()):
    """Клиент без сети: конструктор ходил бы в amoCRM за токеном."""
    client = amo_leads.AmoClient.__new__(amo_leads.AmoClient)
    client.base = "https://amo.test"
    client.session = _FakeSession(outcomes)
    client.expires_at = float("inf")
    return client


def _dropped_connection_error():
    """Как это приходит в жизни: requests -> urllib3 -> http.client."""
    inner = RemoteDisconnected("Remote end closed connection without response")
    return requests.exceptions.ConnectionError(
        requests.packages.urllib3.exceptions.ProtocolError("Connection aborted.", inner))


def _read_timeout_error():
    """urllib3 так сообщает о тишине в сокете дольше read-таймаута."""
    error = urllib3.exceptions.ReadTimeoutError(None, None, "Read timed out.")
    error.__cause__ = TimeoutError("The read operation timed out")
    return error


def _stalled_body_error():
    """Как упали выгрузки 05.11: ответ 200 пришёл, чтение тела встало.

    requests/models.py, generate(): `except ReadTimeoutError as e: raise ConnectionError(e)`.
    """
    inner = _read_timeout_error()
    error = requests.exceptions.ConnectionError(inner)
    error.__context__ = inner
    return error


def _timeout_before_answer_error():
    """Таймаут до ответа — requests/adapters.py: `raise ReadTimeout(e, request=request)`."""
    inner = _read_timeout_error()
    error = requests.exceptions.ReadTimeout(inner)
    error.__context__ = inner
    return error


@pytest.fixture(autouse=True)
def _no_backoff_wait(monkeypatch):
    """Пауза перед повтором в бою нужна, а тесту ждать её незачем."""
    monkeypatch.setattr(amo_leads, "RETRY_BACKOFF_SECONDS", 0)


def test_dropped_connection_is_retried():
    client = _client([_dropped_connection_error(),
                      _FakeResponse(payload={"_embedded": {"leads": [{"id": 1}]}})])
    data = client.get("/api/v4/leads")
    assert data == {"_embedded": {"leads": [{"id": 1}]}}
    assert len(client.session.calls) == 2


def test_stalled_body_is_retried():
    client = _client([_stalled_body_error(),
                      _FakeResponse(payload={"_embedded": {"leads": [{"id": 1}]}})])
    data = client.get("/api/v4/leads")
    assert data == {"_embedded": {"leads": [{"id": 1}]}}
    assert len(client.session.calls) == 2


@pytest.mark.parametrize("make_error", [_dropped_connection_error, _stalled_body_error])
def test_retries_are_not_endless(make_error):
    """Три попытки — и наверх, иначе синк будет молча висеть на мёртвом сокете."""
    client = _client([make_error() for _ in range(amo_leads.REQUEST_RETRIES)])
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("/api/v4/leads")
    assert len(client.session.calls) == amo_leads.REQUEST_RETRIES


def test_timeout_is_never_retried():
    """Таймаут до ответа — это «amoCRM не отвечает»; повтор только добавит ему работы."""
    for error in (requests.exceptions.ConnectTimeout("connect timeout=10"),
                  requests.exceptions.ReadTimeout("read timeout=30"),
                  _timeout_before_answer_error()):
        client = _client([error])
        with pytest.raises(requests.exceptions.Timeout):
            client.get("/api/v4/leads")
        assert len(client.session.calls) == 1


def test_refused_connection_is_never_retried():
    client = _client([requests.exceptions.ConnectionError(
        requests.packages.urllib3.exceptions.NewConnectionError(
            None, "Failed to establish a new connection: [Errno 111] Connection refused"))])
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("/api/v4/leads")
    assert len(client.session.calls) == 1


def test_http_error_is_reported_with_the_body_and_not_retried():
    client = _client([_FakeResponse(status_code=500, text='{"detail":"amoCRM упал"}')])
    with pytest.raises(RuntimeError) as err:
        client.get("/api/v4/leads")
    assert "500" in str(err.value)
    assert "amoCRM упал" in str(err.value)
    assert len(client.session.calls) == 1


def test_no_content_means_end_of_list():
    client = _client([_FakeResponse(status_code=204)])
    assert client.get("/api/v4/leads") is None


@pytest.mark.parametrize("make_error", [_dropped_connection_error, _stalled_body_error])
def test_pagination_survives_a_drop_in_the_middle(monkeypatch, make_error):
    """Обрыв или зависание на середине не должны стоить уже вычитанных страниц."""
    monkeypatch.setattr(amo_leads, "REQUEST_PAUSE_SECONDS", 0)

    def page(number, has_next):
        leads = [{"id": number * 10 + i, "created_at": 1754400000} for i in range(2)]
        payload = {"_embedded": {"leads": leads}}
        if has_next:
            payload["_links"] = {"next": {"href": "https://amo.test/api/v4/leads?page=%d"
                                                 % (number + 1)}}
        return _FakeResponse(payload=payload)

    client = _client([
        page(1, True),
        make_error(),                  # рвётся вторая страница
        page(2, True),
        page(3, False),
    ])
    monkeypatch.setattr(amo_leads, "AmoClient", lambda: client)
    monkeypatch.setattr(amo_leads, "_load_stage_names", lambda _client: {})
    monkeypatch.setattr(amo_leads, "_load_user_names", lambda _client: {})

    rows = amo_leads.fetch_leads(16)
    assert len(rows) == 6
    assert len(client.session.calls) == 4


def test_token_refresh_still_happens_on_401(monkeypatch):
    """Повтор по обрыву не должен подменять повтор по протухшему токену."""
    monkeypatch.setattr(amo_leads, "AMO_ACCESS_TOKEN", "")
    logins = []
    client = _client([_FakeResponse(status_code=401, text="Unauthorized"), _FakeResponse()])
    monkeypatch.setattr(amo_leads.AmoClient, "_login",
                        lambda self: logins.append(True))
    client.get("/api/v4/leads")
    assert logins == [True]
    assert len(client.session.calls) == 2


class _StreamedPage:
    """Страница входа, отдаваемая кусками; кусок-исключение — зависшая передача."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.read = 0
        self.closed = False

    def iter_content(self, _size):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


def _login_client(monkeypatch, outcomes):
    monkeypatch.setattr(amo_leads, "AMO_CRM_LOGIN", "bot@example.test")
    monkeypatch.setattr(amo_leads, "AMO_CRM_PASSWORD", "secret")
    return _client(list(outcomes) + [
        _FakeResponse(payload={"access_token": "fresh", "expires_in": 2400})])


def test_login_page_is_read_only_up_to_the_csrf_token(monkeypatch):
    """Страница ~105 КБ, токен на ~23-м: остаток не читаем и соединение закрываем."""
    page = _StreamedPage([b"<html>" + b"x" * 8000,
                          b'<input name="csrf_tok', b'en" value="abc123">',
                          b"y" * 8000, b"z" * 8000])
    client = _login_client(monkeypatch, [page])
    client._login()
    method, url, kwargs = client.session.calls[1]
    assert (method, url) == ("POST", "https://amo.test/oauth2/authorize")
    assert kwargs["data"]["csrf_token"] == "abc123"
    assert client.session.calls[0][2]["stream"] is True
    assert page.read == 3 and page.closed
    assert client.session.headers["Authorization"] == "Bearer fresh"


def test_login_page_stalling_before_the_token_is_retried(monkeypatch):
    stalled = _StreamedPage([b"<html>" + b"x" * 8000, _stalled_body_error()])
    page = _StreamedPage([b'<input name="csrf_token" value="abc123">'])
    client = _login_client(monkeypatch, [stalled, page])
    client._login()
    assert stalled.closed
    assert client.session.calls[2][2]["data"]["csrf_token"] == "abc123"


def test_login_page_without_a_token_is_still_an_error(monkeypatch):
    page = _StreamedPage([b"<html>dashboard</html>"])
    client = _login_client(monkeypatch, [page])
    with pytest.raises(RuntimeError, match="csrf_token не найден"):
        client._login()
    assert page.closed


# Дальше — сам разбор причины: ищем её по цепочке исключений, а не по тексту
# сообщения (наружу у всех обрывов одинаковое «Connection aborted.»).


def test_detects_remote_disconnected_wrapped_by_urllib3():
    assert amo_leads._dropped_connection(_dropped_connection_error())


def test_detects_reset_raised_as_cause():
    exc = requests.exceptions.ConnectionError("Connection aborted.")
    exc.__cause__ = ConnectionResetError(104, "Connection reset by peer")
    assert amo_leads._dropped_connection(exc)


def test_detects_broken_pipe_in_context():
    exc = requests.exceptions.ConnectionError("Connection aborted.")
    exc.__context__ = BrokenPipeError(32, "Broken pipe")
    assert amo_leads._dropped_connection(exc)


def test_plain_timeout_is_not_a_dropped_connection():
    assert not amo_leads._dropped_connection(requests.exceptions.ConnectTimeout("timeout=60"))
    assert not amo_leads._dropped_connection(requests.exceptions.ReadTimeout("timeout=60"))


def test_stalled_body_is_told_apart_from_timeout_before_answer():
    """Внутри у обоих один и тот же ReadTimeoutError — различает только внешний тип."""
    assert amo_leads._stalled_body(_stalled_body_error())
    assert not amo_leads._stalled_body(_timeout_before_answer_error())
    assert not amo_leads._stalled_body(requests.exceptions.ConnectTimeout("timeout=10"))
    assert not amo_leads._stalled_body(_dropped_connection_error())


def test_cycles_do_not_hang_the_walk():
    first = requests.exceptions.ConnectionError("a")
    second = requests.exceptions.ConnectionError("b")
    first.__cause__ = second
    second.__cause__ = first
    assert not amo_leads._dropped_connection(first)
