# -*- coding: utf-8 -*-
"""Создание заявки в CRM Syntony через форму: POST /api/leads/public/{ключ формы}.

    POST https://app.syntony.kz/api/leads/public/sf_…
    {"phone": "+77015550001", "comment": "…", "utm": {"utm_source": "…"}}

Договор — «Syntony: API заявок, создание и получение», версия 1 от 19.11.2026:

* Ключ формы sf_… — часть адреса. Syntony называет его несекретным (такой же стоит в коде
  страниц сайтов), но с ним любой заводил бы заявки в нашу воронку, поэтому ни ключ, ни
  адрес в журналы и тексты ошибок не попадают — только HTTP-статус и код ошибки.
* Куда ляжет заявка — воронка, ответственный, теги, автоответ клиенту в WhatsApp — решают
  НАСТРОЙКИ ФОРМЫ в Syntony («Интеграции → Форма на сайте»), а не запрос. Название формы
  становится источником заявки в отчётах Syntony.
* 202 {"status":"accepted"} — заявка принята. Номера заявки в ответе нет; повторять после
  202 не нужно.
* Повтор по телефону: пока у номера есть открытая заявка, новая не заводится — данные
  ложатся комментарием в открытую, её теги и метки не меняются, автоответ второй раз не
  уходит. Поэтому повтор после обрыва безопасен: в худшем случае лишний комментарий.
* 400 phone_required / invalid_payload — запрос не годится, повтор не поможет.
  403 form_disabled — форму выключили; 404 unknown_form — ключ сменили; 429 — больше 30
  заявок в минуту на форму; 503 no_pipeline — у клиники нет рабочей воронки. Эти — повтор
  позже: форму включат, лимит пройдёт.
* С заголовком Accept: text/html ответ приходит HTML-страницей для посетителя сайта, поэтому
  просим JSON. Origin не шлём: запрос без него принимается всегда.
"""

import logging
import os
import re

import requests

ENV_FORM_KEY = 'SYNTONY_LEADS_FORM_KEY'
BASE_URL = 'https://app.syntony.kz/api/leads/public/'

REQUEST_TIMEOUT = (10, 30)

# Ключ формы — sf_ и латиница с цифрами. Проверяем заранее: значение с пробелом или переводом
# строки (кривая вставка в окружение) ушло бы в адрес, а адрес requests печатает в тексте
# своих исключений.
_KEY_RE = re.compile(r'^sf_[A-Za-z0-9_\-]{8,128}$')

# «Телефон клиента в любом написании, от 6 до 30 цифр» — меньше или больше Syntony отвергнет
# сама (400 phone_required), и запрос заведомо зря.
PHONE_MIN_DIGITS = 6
PHONE_MAX_DIGITS = 30

# «Свои поля { "название": "значение" }, до 40 штук. Видны менеджеру в карточке, в выгрузку
# не попадают». Больше 40 Syntony не обещает принять — лишние не шлём.
MAX_FIELDS = 40

# Код ошибки из тела ответа попадает в журнал, только если это действительно код.
_ERROR_CODE_RE = re.compile(r'^[a-z][a-z_]{1,40}$')

# Что значат коды ответа — для журнала робота, который читает человек.
_REASONS = {
    400: 'Syntony отклонила заявку',
    403: 'форма Syntony не принимает заявки',
    404: 'форма Syntony не найдена — ключ формы сменили?',
    429: 'больше 30 заявок в минуту на форму Syntony',
    503: 'у клиники в Syntony нет рабочей воронки',
}

logger = logging.getLogger(__name__)


class SyntonyFormError(RuntimeError):
    """Заявку не приняли. Текст безопасен для журнала: без ключа и адреса.

    final — повтор того же запроса не поможет (400: нет телефона, тело не разобралось)."""

    def __init__(self, message, final=False):
        super(SyntonyFormError, self).__init__(message)
        self.final = bool(final)


_reported_bad_keys = set()


def form_key(env_name=ENV_FORM_KEY):
    """Ключ формы из окружения или None, если его нет или он не похож на ключ.

    Кривой ключ пишется в журнал один раз на процесс: робот спрашивает «есть ли ключ»
    каждые пятнадцать секунд, и строка ошибки на каждый цикл утопила бы журнал."""
    key = (os.getenv(env_name) or '').strip()
    if not key:
        return None
    if not _KEY_RE.match(key):
        if env_name not in _reported_bad_keys:
            _reported_bad_keys.add(env_name)
            logger.error('Syntony: %s не похож на ключ формы (sf_…) — заявки не заводятся',
                         env_name)
        return None
    return key


def is_configured(env_name=ENV_FORM_KEY):
    return form_key(env_name) is not None


def _error_code(response):
    try:
        body = response.json()
    except ValueError:
        return ''
    code = body.get('error') if isinstance(body, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE_RE.match(code) else ''


def _accepted(response):
    """202 — принята, так говорит договор. 200/201 принимаем, только если тело это
    подтверждает: страница для посетителя сайта тоже приходит с 200."""
    if response.status_code == 202:
        return True
    if response.status_code in (200, 201):
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get('status') == 'accepted'
    return False


def create_lead(key, phone, comment=None, utm=None, fields=None, session=None):
    """Завести заявку (или дописать комментарий в открытую заявку этого номера).

    fields — свои поля карточки {название: значение}: пустые не уходят, больше MAX_FIELDS —
    тоже. Ничего не возвращает: номера заявки Syntony не отдаёт. Не принята — SyntonyFormError.
    """
    if not key or not _KEY_RE.match(key):
        raise SyntonyFormError('Syntony: нет ключа формы', final=True)
    digits = sum(1 for ch in str(phone or '') if ch.isdigit())
    if not PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS:
        raise SyntonyFormError('Syntony: у заявки нет телефона', final=True)
    payload = {'phone': str(phone)}
    if comment:
        payload['comment'] = str(comment)
    if utm:
        payload['utm'] = {name: str(value) for name, value in utm.items() if value}
    if fields:
        filled = [(str(name), str(value)) for name, value in fields.items()
                  if name and value not in (None, '')]
        if filled:
            payload['fields'] = dict(filled[:MAX_FIELDS])

    own = session is None
    session = session or requests.Session()
    try:
        # Переадресации не выполняются: ответ чужого хоста принялся бы за ответ Syntony.
        response = session.post(BASE_URL + key, json=payload,
                                headers={'Accept': 'application/json'},
                                timeout=REQUEST_TIMEOUT, allow_redirects=False)
    except requests.exceptions.Timeout:
        # Запрос мог и дойти. Повтор безопасен: открытая заявка того же номера получит
        # комментарий, второй заявки не будет.
        raise SyntonyFormError('Syntony не ответила вовремя') from None
    except Exception as exc:                            # noqa: BLE001 — в тексте requests адрес с ключом
        raise SyntonyFormError('Syntony: сбой запроса (%s)' % type(exc).__name__) from None
    finally:
        if own:
            session.close()

    if _accepted(response):
        return
    status = response.status_code
    if 300 <= status < 400:
        raise SyntonyFormError('Syntony: переадресация HTTP %s — не идём' % status)
    code = _error_code(response)
    reason = _REASONS.get(status) or 'Syntony не приняла заявку'
    detail = ('HTTP %s %s' % (status, code)).strip()
    raise SyntonyFormError('%s (%s)' % (reason, detail), final=(status == 400))
