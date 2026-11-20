"""Разговор с API истории чатов Syntony: только чтение, ключ sck_ из окружения.

    GET https://app.syntony.kz/api/chats-api/v1/…
    X-Api-Key: sck_…

Ключ выпускает владелец аккаунта сети; он открывает всю переписку сети с номерами и
файлами, поэтому живёт только в окружении сервера (SYNTONY_HISTORY_API_KEY) и не пишется
ни в журнал, ни в ответы. В журнал — путь метода, статус и код ошибки, без параметров:
в них бывает номер телефона.

Методы и лимиты на ключ (§3): /messages — 10 в минуту, /conversations — 60,
/conversations/{id}/messages — 300. Повтор по 429 и 503 делает вызывающий (sync.py):
ему решать, ждать ли или отложить прогон.
"""

import os
from dataclasses import dataclass, field
from datetime import timezone
from urllib.parse import quote, urlsplit

import requests

ENV_KEY = 'SYNTONY_HISTORY_API_KEY'
ENV_URL = 'SYNTONY_HISTORY_API_URL'
DEFAULT_URL = 'https://app.syntony.kz/api/chats-api/v1'
SYNTONY_DOMAIN = 'syntony.kz'

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30
# Страница ленты — до 1000 сообщений: на живых данных ~0,7 КБ на сообщение.
MAX_REPLY_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Config:
    url: str = field(repr=False)
    key: str = field(repr=False)


class SyntonyHistoryError(Exception):
    """Отказ API. code — код Syntony (§5) или наш (network, bad_reply); status — HTTP;
    retry_after — секунды из заголовка Retry-After, если он был."""

    def __init__(self, code, status=None, retry_after=None):
        super().__init__('%s (HTTP %s)' % (code, status if status is not None else '-'))
        self.code = code
        self.status = status
        self.retry_after = retry_after


def _on_syntony_domain(hostname):
    host = str(hostname or '').lower().rstrip('.')
    return host == SYNTONY_DOMAIN or host.endswith('.' + SYNTONY_DOMAIN)


def load_config(environ=None):
    """Ключ и адрес из окружения. None — ключа нет или адрес не https на домене Syntony."""
    environ = os.environ if environ is None else environ
    key = str(environ.get(ENV_KEY) or '').strip()
    if not key or any(ord(char) < 33 or ord(char) > 126 for char in key):
        return None
    url = (str(environ.get(ENV_URL) or '').strip() or DEFAULT_URL).rstrip('/')
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (parsed.scheme != 'https' or not _on_syntony_domain(parsed.hostname)
            or parsed.username is not None or parsed.password is not None
            or any(ord(char) < 33 for char in url)):
        return None
    return Config(url=url, key=key)


def is_configured(environ=None):
    return load_config(environ) is not None


def _retry_after(response):
    try:
        return max(0, int(str(response.headers.get('Retry-After') or '').strip()))
    except (TypeError, ValueError):
        return None


def get(config, path, params=None, *, session=None):
    """GET метода API → разобранный JSON-объект. Отказ — SyntonyHistoryError."""
    session = session or requests
    try:
        response = session.get(
            config.url + path,
            params=params or None,
            headers={'X-Api-Key': config.key, 'Accept': 'application/json'},
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            # requests несёт X-Api-Key и на чужой хост переадресации: ключ не должен уйти.
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        # Текст исключения requests может нести адрес с параметрами (номер телефона) и —
        # у InvalidHeader — сам ключ: наружу только класс.
        raise SyntonyHistoryError('network:' + type(exc).__name__) from None
    status = int(getattr(response, 'status_code', 0) or 0)
    content = getattr(response, 'content', b'') or b''
    if len(content) > MAX_REPLY_BYTES:
        raise SyntonyHistoryError('too_large', status)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if status == 200:
        if not isinstance(payload, dict):
            raise SyntonyHistoryError('bad_reply', status)
        return payload
    code = payload.get('error') if isinstance(payload, dict) else None
    if not isinstance(code, str) or not code:
        code = 'http_%s' % status
    raise SyntonyHistoryError(code, status, _retry_after(response))


def iso(moment):
    """Время для параметров API: ISO 8601 в UTC с Z."""
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def messages_page(config, since, cursor=None, limit=1000, **kwargs):
    """Лента новых сообщений сети с момента since (§3.3)."""
    params = {'since': iso(since), 'limit': int(limit)}
    if cursor:
        params['cursor'] = cursor
    return get(config, '/messages', params, **kwargs)


def conversations_page(config, updated_since=None, offset=0, limit=500, **kwargs):
    """Диалоги, обновлённые с момента updated_since (§3.1), страницы по offset."""
    params = {'offset': int(offset), 'limit': int(limit)}
    if updated_since is not None:
        params['updatedSince'] = iso(updated_since)
    return get(config, '/conversations', params, **kwargs)


def conversation_messages_page(config, conversation_id, cursor=None, limit=1000, **kwargs):
    """Все сообщения одного диалога (§3.2), страницы по курсору."""
    params = {'limit': int(limit)}
    if cursor:
        params['cursor'] = cursor
    return get(config, '/conversations/%s/messages' % quote(str(conversation_id), safe=''),
               params, **kwargs)
