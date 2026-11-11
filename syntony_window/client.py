"""Разговор с Syntony: один POST за одноразовой ссылкой на окно.

    POST https://app.syntony.kz/v1/iframe
    Authorization: Bearer sk_live_…

Ключ — только из окружения сервера (SYNTONY_WINDOW_API_KEY на Render). В журналы
не пишутся ни ключ, ни адрес, ни тело запроса (в нём почта оператора), ни ссылка
из ответа (она одноразовая и открывает окно): только статус и код ошибки.

Ответ Syntony уходит браузеру как есть — { url, expiresAt } или
{ error: { code, message } }. Три оговорки, каждая — защита, а не правка ответа:

* ответ, в котором оказался сам ключ, браузеру не отдаётся (ключа там быть не
  должно; если он там есть — показывать это странице нельзя тем более);
* ссылка окна обязана быть https на домене Syntony: её ставят в src фрейма, и
  посторонний адрес или схема там недопустимы;
* не-JSON, пустое тело и обрыв связи превращаются в ошибку icore_unreachable того
  же вида, чтобы экран разбирал всё одним кодом.

Ссылка одноразовая и живёт 60 секунд: модуль ничего не хранит и не повторяет запрос
сам — новую ссылку просят новым запросом.
"""

import logging
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

from . import rules

ENV_KEY = 'SYNTONY_WINDOW_API_KEY'
ENV_URL = 'SYNTONY_WINDOW_API_URL'
DEFAULT_URL = 'https://app.syntony.kz/v1/iframe'
# Домен Syntony: и адрес запроса, и ссылка окна обязаны лежать на нём.
SYNTONY_DOMAIN = 'syntony.kz'

CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 10
# Больше ответу Syntony незачем быть: ссылка и срок либо ошибка.
MAX_REPLY_BYTES = 64 * 1024

logger = logging.getLogger(__name__)

UNREACHABLE_MESSAGE = 'Syntony не отвечает. Попробуйте через минуту.'
NOT_CONFIGURED_MESSAGE = 'Окно Syntony ещё не подключено: на сервере iCORE не задан ключ Syntony.'


@dataclass(frozen=True)
class Config:
    url: str = field(repr=False)
    key: str = field(repr=False)


@dataclass(frozen=True)
class Reply:
    """Что отдать браузеру: HTTP-статус и тело (всегда словарь)."""
    status: int
    body: dict

    @property
    def code(self):
        return rules.error_code(self.body)


def _on_syntony_domain(hostname):
    host = str(hostname or '').lower().rstrip('.')
    return host == SYNTONY_DOMAIN or host.endswith('.' + SYNTONY_DOMAIN)


def _is_https_syntony_url(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and _on_syntony_domain(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443))
    except ValueError:
        return False


def load_config(environ=None):
    """Ключ и адрес из окружения. None — окно не настроено (ключа нет или он битый)."""
    environ = os.environ if environ is None else environ
    key = str(environ.get(ENV_KEY) or '').strip()
    if not key or any(ord(char) < 33 or ord(char) > 126 for char in key):
        return None
    url = str(environ.get(ENV_URL) or '').strip() or DEFAULT_URL
    if any(ord(char) < 33 for char in url) or not _is_https_syntony_url(url):
        return None
    return Config(url=url, key=key)


def is_configured(environ=None):
    return load_config(environ) is not None


def not_configured_reply():
    return Reply(503, rules.error_payload(rules.CODE_NOT_CONFIGURED, NOT_CONFIGURED_MESSAGE))


def _unreachable(reason):
    # reason — слово для журнала: без адреса, тела и ключа.
    logger.warning('syntony_window: Syntony не ответила (%s)', reason)
    return Reply(502, rules.error_payload(rules.CODE_UNREACHABLE, UNREACHABLE_MESSAGE))


def _contains_key(value, key):
    """Есть ли ключ где-нибудь в ответе (в строках и ключах, на любой глубине)."""
    if isinstance(value, str):
        return key in value
    if isinstance(value, dict):
        return any(_contains_key(k, key) or _contains_key(v, key) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, key) for item in value)
    return False


def request_window(config, body, *, post=None):
    """Запросить ссылку на окно. Всегда возвращает Reply и не бросает исключений сети.

    post — подмена requests.post для тестов.
    """
    post = post or requests.post
    try:
        response = post(
            config.url,
            json=body,
            headers={'Authorization': 'Bearer ' + config.key, 'Content-Type': 'application/json'},
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            # Ключ не должен уйти по редиректу на другой адрес.
            allow_redirects=False,
        )
    except requests.Timeout:
        return _unreachable('таймаут')
    except requests.RequestException as exc:
        return _unreachable(type(exc).__name__)

    status = int(getattr(response, 'status_code', 0) or 0)
    content = getattr(response, 'content', b'') or b''
    if len(content) > MAX_REPLY_BYTES:
        return _unreachable('ответ длиннее допустимого, HTTP %s' % status)
    try:
        payload = response.json()
    except ValueError:
        return _unreachable('ответ не JSON, HTTP %s' % status)
    if not isinstance(payload, dict):
        return _unreachable('ответ не объект, HTTP %s' % status)
    if _contains_key(payload, config.key):
        return _unreachable('в ответе оказался ключ, HTTP %s' % status)

    if status == 200:
        url = payload.get('url')
        if not isinstance(url, str) or not _is_https_syntony_url(url):
            return _unreachable('в ответе нет ссылки окна на домене Syntony')
        return Reply(200, payload)

    if not rules.error_code(payload):
        return _unreachable('ошибка без кода, HTTP %s' % status)
    if not 400 <= status <= 599:
        # Ошибка с кодом, но со статусом «успех» или редиректом: браузеру её
        # показываем ошибкой, а не успехом без ссылки.
        status = 502
    return Reply(status, payload)
