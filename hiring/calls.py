# -*- coding: utf-8 -*-
"""Звонки откликам из телефонии Binotel (владелец 30.10.2026: «звонки должны быть фактически видны,
как в удалённом КЦ»). Чистая логика: разбор вебхука и строки истории → одна форма звонка,
исход по правилам раздела «Удаленный КЦ», подписи.

Откуда звонки:
* вебхук «API Call Completed» — Binotel шлёт его по ВСЕМ звонкам компании удалённого КЦ
  (dial_list.service.handle_webhook отдаёт наблюдателям разобранные поля
  callDetails[…]); это и исходящие с линии рекрутера из iCore Phone, и входящие от кандидата;
* история звонков линии (stats/list-of-calls-by-internal-number-for-period) — добор того, что
  вебхук не донёс (сервер перезапускался), раз в десять минут по линиям рекрутеров.

Звонок привязывается к карточке по номеру абонента: сначала открытая карточка этого рекрутера,
потом любая открытая с таким телефоном, потом последняя завершённая. Рекрутер — по линии
(internalNumber → членство в линии «Удаленного КЦ» → действующая выдача рекрутера).
"""

from datetime import datetime, timezone

from . import rules

# Те же множества, что в dial_list.service: исход Binotel → результат.
ANSWERED = frozenset({'ANSWER', 'ANSWERED', 'SUCCESS', 'VM-SUCCESS'})
BUSY = frozenset({'BUSY'})
NO_ANSWER = frozenset({'NOANSWER', 'NO ANSWER', 'NO_ANSWER', 'CANCEL', 'CANCELLED', 'CANCELED'})
NON_FINAL = frozenset({'', 'ONLINE', 'CALLING', 'RINGING', 'DIALING', 'INPROGRESS', 'IN-PROGRESS'})

RESULT_LABELS = {'answered': 'Дозвонились', 'busy': 'Занято', 'no_answer': 'Не поднял трубку', 'other': 'Не состоялся'}
DIRECTION_LABELS = {'out': 'Исходящий звонок', 'in': 'Входящий звонок'}

# Ручная отметка звонка (окно после «Позвонить») и звонок из телефонии — одно касание: если они
# рядом по времени, звонок дописывает отметку, а не ложится второй строкой.
MERGE_WINDOW_MINUTES = 20
# Сколько истории линии добирать за один опрос, и насколько назад перекрывать прошлый опрос.
HISTORY_FIRST_HOURS = 24
HISTORY_OVERLAP_MINUTES = 30


def map_result(disposition):
    """disposition Binotel → answered | busy | no_answer | other; '' — звонок ещё идёт."""
    value = str(disposition or '').strip().upper()
    if value in NON_FINAL:
        return ''
    if value in ANSWERED:
        return 'answered'
    if value in BUSY:
        return 'busy'
    if value in NO_ANSWER:
        return 'no_answer'
    return 'other'


def _value(fields, name):
    """Поле звонка из разобранного вебхука: плоское имя либо внутри callDetails[…], без учёта регистра."""
    want = str(name).lower()
    for key, value in (fields or {}).items():
        lowered = str(key).lower()
        if (lowered == want or lowered == 'calldetails[%s]' % want) and value not in (None, ''):
            return value
    return None


def _int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _moment(start_time):
    """startTime Binotel (unix UTC) → наивное время Алматы; нет — None."""
    seconds = _int(start_time, 0)
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(rules.ALMATY).replace(tzinfo=None)


def from_webhook(fields):
    """Разобранный вебхук (dial_list.service.webhook_fields) → звонок или None (нет id / линии)."""
    general_call_id = str(_value(fields, 'generalCallID') or '').strip()[:32]
    internal_number = str(_value(fields, 'internalNumber') or '').strip()[:64]
    if not general_call_id or not internal_number:
        return None
    return {
        'general_call_id': general_call_id,
        'internal_number': internal_number,
        'external_phone': rules.normalize_phone(_value(fields, 'externalNumber')),
        'direction': 'out' if _int(_value(fields, 'callType'), -1) == 1 else 'in',
        'disposition': str(_value(fields, 'disposition') or '').strip().upper(),
        'billsec': max(0, _int(_value(fields, 'billsec'))),
        'waitsec': max(0, _int(_value(fields, 'waitsec'))),
        'started_at': _moment(_value(fields, 'startTime')),
        'recording_status': str(_value(fields, 'recordingStatus') or '').strip().lower(),
        'company_id': str(_value(fields, 'companyID') or '').strip()[:32],
    }


def from_history(call):
    """Нормализованный звонок клиента Binotel (list_calls_by_internal_number) → та же форма."""
    if not call or not call.get('general_call_id') or not call.get('internal_number'):
        return None
    return {
        'general_call_id': str(call['general_call_id'])[:32],
        'internal_number': str(call['internal_number']).strip()[:64],
        'external_phone': rules.normalize_phone(call.get('external_number')),
        'direction': 'out' if _int(call.get('call_type'), -1) == 1 else 'in',
        'disposition': str(call.get('disposition') or '').strip().upper(),
        'billsec': max(0, _int(call.get('billsec'))),
        'waitsec': max(0, _int(call.get('waitsec'))),
        'started_at': _moment(call.get('start_time')),
        'recording_status': str(call.get('recording_status') or '').strip().lower(),
        'company_id': '',
    }


def talked(call):
    """Был ли разговор: ответ Binotel и хотя бы секунда."""
    return map_result(call.get('disposition')) == 'answered' and int(call.get('billsec') or 0) >= 1


def touch_result(call):
    """Результат касания в карточке: разговор — «дозвон», остальное — «недозвон»."""
    return 'dial' if talked(call) else 'no_answer'


def touch_body(call):
    result = map_result(call.get('disposition')) or 'other'
    label = RESULT_LABELS.get(result, result)
    if talked(call):
        label += ' · %s' % format_duration(call.get('billsec'))
    return '%s · %s' % (DIRECTION_LABELS[call.get('direction', 'out')], label)


def format_duration(seconds):
    total = max(0, int(seconds or 0))
    minutes, rest = divmod(total, 60)
    return '%d:%02d' % (minutes, rest)


def recording_state(call):
    """available — запись должна быть (разговор был); none — разговора не было; unknown — не знаем."""
    status = str(call.get('recording_status') or '').lower()
    if talked(call):
        return 'none' if status in ('0', 'no', 'false', 'off', 'disabled') else 'available'
    return 'none'
