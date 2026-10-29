# -*- coding: utf-8 -*-
"""Проверка ключа партнёра с областью доступа — для ручек.

    status, key = auth.lookup(db, auth.presented_from(request.headers), scope=scopes.KNOWLEDGE)

Исходы — слова, а не HTTP-коды: у каждой ручки свои коды ошибок и свой договор с
партнёром (у «Ссылок» — SIGN_LINKS_API_*, у окна CRM — короткие code), и решать,
чем ответить, должна она. Что здесь гарантируется:

* **До базы — только похожее на ключ** (keys.presented). Пустой заголовок и
  мусор в базу не ходят, но схема проверяется и для них: ручке без единого
  настроенного ключа положено отвечать 503, а не 401.
* **Неизвестный, отозванный и ключ без области — разные слова.** Чужому ручка
  отвечает одинаково (401), партнёру с верным ключом без этой двери — 403: у
  него ключ настоящий, и подсказать, чего не хватает, безопасно.
* **База не ответила — ERROR, не отказ по ключу.** Ручка решает сама; падать
  исключением из проверки ключа нельзя — это вход.
"""

import logging

from . import keys, queries, schema

OK = 'ok'
NOT_READY = 'not_ready'
UNKNOWN = 'unknown'
REVOKED = 'revoked'
NO_SCOPE = 'no_scope'
ERROR = 'error'


def presented_from(headers, *, bearer=False):
    """Значение ключа из заголовков: X-Api-Key, а для ручек, где партнёр уже ходит
    с Authorization: Bearer (окно CRM), — и он."""
    value = str(headers.get(keys.HEADER) or '').strip()
    if value or not bearer:
        return value or None
    authorization = str(headers.get('Authorization') or '').strip()
    if authorization[:7].lower() == 'bearer ':
        return authorization[7:].strip() or None
    return None


def lookup(db, presented, *, scope):
    """(исход, ключ). Ключ приходит и при NO_SCOPE — чтобы ручка назвала его в логе."""
    value = keys.presented(presented)
    try:
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return NOT_READY, None
            key = queries.find_key(cursor, keys.digest(value)) if value else None
    except Exception:                                    # noqa: BLE001
        logging.exception('partner_keys: ключ не проверен — база не ответила')
        return ERROR, None
    if key is None:
        return UNKNOWN, None
    if key.get('revoked_at'):
        return REVOKED, None
    if scope not in (key.get('scopes') or ()):
        return NO_SCOPE, key
    return OK, key
