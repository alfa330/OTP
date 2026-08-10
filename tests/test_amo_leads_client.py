# -*- coding: utf-8 -*-
"""Клиент amoCRM: повтор запроса на обрыве соединения.

Одна выгрузка лидов — около 86 запросов подряд, и amoCRM время от времени
закрывает соединение на середине (на проде 5 прогонов из 39 за 06–10.08, всегда
через 12–13 с после старта). Без повтора обрыв выбрасывал все уже вычитанные
страницы, и отбивка показывала «выгрузка не удалась» до следующего синка через
три часа. Проверяем именно наши обязательства: повторяем только обрыв уже
установленного соединения и не повторяем таймауты и отказ в соединении.
"""

import os
import sys

import pytest
import requests
from http.client import RemoteDisconnected

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_leads


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


def test_retries_are_not_endless():
    """Три попытки — и наверх, иначе синк будет молча висеть на мёртвом сокете."""
    client = _client([_dropped_connection_error() for _ in range(amo_leads.REQUEST_RETRIES)])
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("/api/v4/leads")
    assert len(client.session.calls) == amo_leads.REQUEST_RETRIES


def test_timeout_is_never_retried():
    """Таймаут — это «amoCRM не отвечает»; повтор только добавит ему работы."""
    for error in (requests.exceptions.ConnectTimeout("connect timeout=60"),
                  requests.exceptions.ReadTimeout("read timeout=60")):
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


def test_pagination_survives_a_drop_in_the_middle(monkeypatch):
    """Обрыв на середине не должен стоить уже вычитанных страниц."""
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
        _dropped_connection_error(),   # рвётся вторая страница
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


def test_cycles_do_not_hang_the_walk():
    first = requests.exceptions.ConnectionError("a")
    second = requests.exceptions.ConnectionError("b")
    first.__cause__ = second
    second.__cause__ = first
    assert not amo_leads._dropped_connection(first)
