# -*- coding: utf-8 -*-
"""События чатов OLX для партнёров (partner_keys.webhooks, область «Чаты OLX»).

У OLX вебхуков нет: новое в чатах узнаёт наш опрос (service.poll_cabinet). Этот
модуль превращает то, что опрос увидел, в события партнёру:

    message.received  кандидат написал в чат кабинета; рядом — что робот с этим
                      сделал (сделка в amoCRM, автоответ, ждёт человека…)
    message.sent      из кабинета ушло наше сообщение: робот, сотрудник из
                      раздела или сам партнёр через API
    ping              проверка адреса приёмника (POST /api/olx/webhook/test)

Публикация — приставка к делу робота: ни одна функция здесь не выпускает
исключений, падение доставки не должно стоить сделки в CRM.
"""

import logging
from datetime import timedelta, timezone

from partner_keys import scopes as partner_scopes
from partner_keys import webhooks as partner_webhooks

from . import cabinets as olx_cabinets

log = logging.getLogger(__name__)

SCOPE = partner_scopes.OLX_CHATS
EVENT_RECEIVED = 'message.received'
EVENT_SENT = 'message.sent'
EVENT_PING = 'ping'
EVENTS = (EVENT_RECEIVED, EVENT_SENT)

_ALMATY = timezone(timedelta(hours=5))


def iso(value):
    """Время в ISO 8601 с поясом. Наивные метки в базе и у OLX-клиента — Алматы."""
    if value is None or not hasattr(value, 'isoformat'):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=_ALMATY)
    return value.isoformat()


def cabinet_info(cabinet):
    return {'code': cabinet.code, 'title': cabinet.title, 'olx_id': cabinet.olx_id,
            'line_phone': cabinet.line_phone}


def thread_info(thread_id, thread=None, state=None):
    thread = thread or {}
    state = state or {}
    return {
        'id': str(thread_id),
        'advert_id': _text(thread.get('advert_id') or state.get('advert_id')),
        'advert_title': state.get('advert_title'),
        'interlocutor_id': _text(thread.get('interlocutor_id') or state.get('interlocutor_id')),
        'interlocutor_name': state.get('interlocutor_name'),
        'awaiting_human_since': iso(state.get('awaiting_human_since')),
        'phone': state.get('phone_normalized'),
        'amo_lead_id': state.get('amo_lead_id'),
    }


def message_received(db, cabinet, thread, state, message, outcome, phone=None, phone_raw=None,
                     message_at=None):
    """Кандидат написал. outcome — результат робота по журналу (lead_created,
    manual_review, duplicate, canned_reply, needs_human, error)."""
    try:
        payload = {
            'cabinet': cabinet_info(cabinet),
            'thread': thread_info((thread or {}).get('id'), thread, state),
            'message': {
                'id': _text((message or {}).get('id')),
                'text': (message or {}).get('text') or '',
                'at': iso(message_at),
                'attachments': (message or {}).get('attachments') or [],
                'cvs': (message or {}).get('cvs') or [],
                'phone': phone,
                'phone_raw': phone_raw,
            },
            'robot': {'outcome': outcome},
        }
        return partner_webhooks.publish(db, SCOPE, EVENT_RECEIVED, payload)
    except Exception:                                    # noqa: BLE001
        log.exception('OLX %s: событие партнёру не собрано', getattr(cabinet, 'code', '?'))
        return 0


def message_sent(db, cabinet, thread_id, text, *, kind, author=None, source='portal', state=None,
                 sent_at=None):
    """Из кабинета ушло наше сообщение. kind — canned | portal; source — robot |
    portal | partner (чей это был ответ)."""
    try:
        payload = {
            'cabinet': cabinet_info(cabinet),
            'thread': thread_info(thread_id, None, state),
            'message': {'text': text or '', 'at': iso(sent_at), 'kind': kind,
                        'source': source, 'author': author},
        }
        return partner_webhooks.publish(db, SCOPE, EVENT_SENT, payload)
    except Exception:                                    # noqa: BLE001
        log.exception('OLX %s: событие об отправке партнёру не собрано', getattr(cabinet, 'code', '?'))
        return 0


def ping(db, key_id):
    """Проверка приёмника — одной подписке, по просьбе самого партнёра."""
    payload = {'cabinets': [cabinet_info(cab) for cab in olx_cabinets.CABINETS],
               'note': 'Проверка адреса приёмника вебхука iCORE'}
    return partner_webhooks.publish(db, SCOPE, EVENT_PING, payload, key_id=key_id)


def _text(value):
    return None if value is None else str(value)
