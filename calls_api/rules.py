# -*- coding: utf-8 -*-
"""Звонки Binotel для CRM — чистые правила: чей звонок отдаём, как разбираем вебхук и строку
истории Binotel и в каком виде звонок уходит партнёру. Ни Flask, ни базы, ни сети.

Постановка владельца 05.11.2026: в CRM (backend.yataxi.kz) есть «воронка регионов» — менеджеры
«Фронт офисов» обзванивают базу водителей. CRM должна в реальном времени видеть попытки, исходы и
факты звонков, а кнопка «Позвонить» в CRM — открывать iCORE Phone. Решения владельца
(AskUserQuestion 05.11.2026):
* звонки — только менеджеров «Фронт офисов» (DEPARTMENT_CODES): звонки удалённого КЦ, рекрутера,
  робота-автоинформатора и остальных линий кабинета в CRM не уходят;
* звонок приходит после окончания (вебхук Binotel «API Call Completed»). Сигнала «начался
  набор» нет: для него Binotel ждал бы ответа нашего сервера перед каждым звонком кабинета.

Чей звонок. Менеджеры фронт-офиса звонят с линий кабинета Binotel удалённого КЦ — «сидят» на
линиях отдела (dial_list_line_members). Звонок наш, когда:
* он из этого кабинета: companyID совпал с кабинетом удалённого КЦ. Номера линий у кабинетов
  пересекаются (у Тез свои 924, 925, 929 — те же цифры, что у линий фронт-офиса, 05.11.2026),
  а вебхуки обоих кабинетов приходят на один адрес;
* на линии в момент звонка сидел сотрудник отдела из DEPARTMENT_CODES;
* это не звонок обзвона: вкладка «Обзвон» в режиме удалённого КЦ — работа удалённого КЦ.
"""

import re
from datetime import datetime, timedelta, timezone

# Чьи звонки видит область «Звонки Binotel» (коды departments.code, без учёта регистра).
DEPARTMENT_CODES = ('front_office',)
# Кабинет Binotel, на линиях которого они звонят (binotel.client.COMPANY_REMOTE_CC).
COMPANY = 'remote_cc'

ALMATY = timezone(timedelta(hours=5))

# Исход Binotel → результат. Те же множества, что у «Удаленного КЦ» и «Воронки найма».
ANSWERED = frozenset({'ANSWER', 'ANSWERED', 'SUCCESS', 'VM-SUCCESS'})
BUSY = frozenset({'BUSY'})
NO_ANSWER = frozenset({'NOANSWER', 'NO ANSWER', 'NO_ANSWER', 'CANCEL', 'CANCELLED', 'CANCELED'})
# Звонок ещё идёт (так Binotel отвечает на call-details) — ни хранить, ни слать его нельзя.
NON_FINAL = frozenset({'', 'ONLINE', 'CALLING', 'RINGING', 'DIALING', 'INPROGRESS', 'IN-PROGRESS'})
RESULTS = ('answered', 'busy', 'no_answer', 'failed')
DIRECTIONS = {'out': 'outgoing', 'in': 'incoming'}

# Запись выключена у линии — так Binotel пишет recordingStatus, когда файла не будет.
_NO_RECORDING = frozenset({'0', 'no', 'false', 'off', 'disabled'})
_DIGITS = re.compile(r'\D+')


def map_result(disposition):
    """disposition Binotel → answered | busy | no_answer | failed; '' — звонок ещё идёт."""
    value = str(disposition or '').strip().upper()
    if value in NON_FINAL:
        return ''
    if value in ANSWERED:
        return 'answered'
    if value in BUSY:
        return 'busy'
    if value in NO_ANSWER:
        return 'no_answer'
    return 'failed'


def normalize_phone(value):
    """+7XXXXXXXXXX для номера с кодом +7 (10 цифр, 11 с ведущей 7 или 8); иначе None.
    Десять цифр с нуля впереди — местный формат другой страны, а не номер +7."""
    digits = _DIGITS.sub('', str(value or ''))
    if len(digits) == 11 and digits[0] in '78':
        digits = digits[1:]
    if len(digits) != 10 or digits[0] == '0':
        return None
    return '+7' + digits


def _int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _moment(start_time):
    """startTime Binotel (unix) → момент с поясом UTC; нет — None."""
    seconds = _int(start_time, 0)
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def field(fields, name):
    """Поле звонка из разобранного вебхука (dial_list.service.webhook_fields): плоское имя или то
    же внутри callDetails[…], без учёта регистра. Вложенные глубже (historyData — плечи) не в счёт."""
    want = str(name).lower()
    for key, value in (fields or {}).items():
        lowered = str(key).lower()
        if (lowered == want or lowered == 'calldetails[%s]' % want) and value not in (None, ''):
            return value
    return None


def _call(general_call_id, internal_number, external, call_type, disposition, billsec, waitsec,
          start_time, recording_status, company_id):
    general_call_id = str(general_call_id or '').strip()[:32]
    internal_number = str(internal_number or '').strip()[:64]
    result = map_result(disposition)
    started_at = _moment(start_time)
    # Без id, линии, исхода или момента звонок ни к кому не привязать и не показать.
    if not general_call_id or not internal_number or not result or started_at is None:
        return None
    external_raw = _DIGITS.sub('', str(external or ''))[:32]
    return {
        'general_call_id': general_call_id,
        'company_id': str(company_id or '').strip()[:32],
        'internal_number': internal_number,
        'direction': 'out' if _int(call_type, -1) == 1 else 'in',
        'phone': normalize_phone(external_raw),
        'external_raw': external_raw,
        'disposition': str(disposition or '').strip().upper()[:32],
        'result': result,
        'billsec': max(0, _int(billsec)),
        'waitsec': max(0, _int(waitsec)),
        'started_at': started_at,
        'recording_status': str(recording_status or '').strip().lower()[:32],
    }


def from_webhook(fields):
    """Вебхук «API Call Completed» (поля webhook_fields) → звонок или None."""
    return _call(field(fields, 'generalCallID'), field(fields, 'internalNumber'),
                 field(fields, 'externalNumber'), field(fields, 'callType'),
                 field(fields, 'disposition'), field(fields, 'billsec'), field(fields, 'waitsec'),
                 field(fields, 'startTime'), field(fields, 'recordingStatus'),
                 field(fields, 'companyID'))


def from_history(call):
    """Звонок клиента Binotel (BinotelApiClient._normalize_call) → та же форма или None."""
    call = call or {}
    return _call(call.get('general_call_id'), call.get('internal_number'), call.get('external_number'),
                 call.get('call_type'), call.get('disposition'), call.get('billsec'),
                 call.get('waitsec'), call.get('start_time'), call.get('recording_status'),
                 call.get('company_id'))


def in_scope(department_code):
    return str(department_code or '').strip().lower() in DEPARTMENT_CODES


def talked(call):
    """Состоялся ли разговор: ответ Binotel и хотя бы секунда разговора."""
    return call.get('result') == 'answered' and int(call.get('billsec') or 0) >= 1


def has_recording(call):
    """Будет ли запись: разговор был и запись у линии не выключена."""
    return talked(call) and str(call.get('recording_status') or '').lower() not in _NO_RECORDING


def changed(old, new):
    """Уточнил ли новый разбор уже известный звонок так, что партнёру стоит прислать его снова."""
    return (old.get('result') != new.get('result')
            or int(new.get('billsec') or 0) > int(old.get('billsec') or 0)
            or int(new.get('waitsec') or 0) > int(old.get('waitsec') or 0))


def iso(value):
    """Время партнёру — ISO 8601 по Алматы (+05:00). Наивное — уже Алматы."""
    if value is None or not hasattr(value, 'isoformat'):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=ALMATY)
    return value.astimezone(ALMATY).isoformat()


def document(row):
    """Строка calls_api_calls (с оператором) → звонок, как его видит партнёр."""
    started = row['started_at']
    waitsec = int(row.get('waitsec') or 0)
    billsec = int(row.get('billsec') or 0)
    answered = row.get('result') == 'answered'
    operator = None
    if row.get('user_id'):
        operator = {'id': int(row['user_id']), 'name': row.get('user_name') or '',
                    'email': row.get('user_email') or None,
                    'department': row.get('department_name') or None}
    return {
        'id': str(row['general_call_id']),
        'direction': DIRECTIONS.get(row.get('direction'), 'outgoing'),
        'result': row.get('result'),
        'talked': talked(row),
        'disposition': row.get('disposition') or '',
        'phone': row.get('phone'),
        'phone_raw': row.get('external_raw') or '',
        'line': row.get('internal_number'),
        'operator': operator,
        'started_at': iso(started),
        'answered_at': iso(started + timedelta(seconds=waitsec)) if answered else None,
        'ended_at': iso(started + timedelta(seconds=waitsec + billsec)),
        'wait_sec': waitsec,
        'talk_sec': billsec,
        'has_recording': has_recording(row),
        'source': row.get('source') or 'webhook',
        'received_at': iso(row.get('received_at')),
        'updated_at': iso(row.get('updated_at')),
    }
