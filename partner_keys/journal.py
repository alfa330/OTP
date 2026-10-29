# -*- coding: utf-8 -*-
"""Запись вызова партнёра в журнал — для ручек.

    journal.record(db, key, scope=scopes.SIGN_LINKS, endpoint='/api/sign_links/external/links',
                   outcome='ok', http_status=200, latency_ms=812, ip=…, user_agent=…,
                   summary='ИИН: 12 — ссылок 3', details={'iins': 12, 'links': 3})

record — своей короткой транзакцией ПОСЛЕ ответа: отказ записи не меняет ответ
партнёру (ответ уже посчитан, падение здесь — наша беда), а сама запись не
держит соединение на время работы ручки. insert — то же на чужом курсоре, когда
ручке нужно записать вызов в одной транзакции со своими строками (ручка знаний
кладёт рядом «Вопрос операторов»).
"""

import logging

from . import queries


def insert(cursor, key, *, scope, endpoint, outcome, http_status=None, latency_ms=None,
           summary=None, details=None, ip=None, user_agent=None, request_id=None):
    call_id = queries.insert_call(
        cursor, key_id=key['id'], scope=scope, endpoint=endpoint, outcome=outcome,
        http_status=http_status, latency_ms=latency_ms, summary=summary, details=details,
        ip_address=ip, user_agent=user_agent, request_id=request_id)
    queries.touch_key(cursor, key['id'])
    return call_id


def record(db, key, **fields):
    """id записи или None. Исключения не выпускает: это приставка к ответу."""
    if not key or key.get('id') is None:
        return None
    try:
        with db._get_cursor() as cursor:
            return insert(cursor, key, **fields)
    except Exception:                                    # noqa: BLE001
        logging.exception('partner_keys: вызов ключа %s не записан в журнал', key.get('mark'))
        return None
