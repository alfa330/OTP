"""Разбор HTTP-запроса и ответы раздела «Оплата счетов». Общее для всех модулей с ручками.

Вынесено из routes.py, когда ручки разошлись по файлам (заявки, справочники,
имущество): всем им нужен один и тот же разбор значений и один вид ошибки —
{"error": "...", "code": "..."} с осмысленным кодом.
"""

import json
from datetime import date, datetime

from flask import jsonify as _flask_jsonify, request

from . import schema
from .sqlutil import plain

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

_MAX_LENGTHS = {
    'expense_name': 300, 'branch': 200, 'payment_period': 120, 'notes': 4000, 'invoice_number': 100,
    'invoice_description': 4000, 'comment': 4000, 'justification': 4000, 'payment_purpose': 2000,
    'supplier_choice_reason': 2000, 'no_alternatives_comment': 2000, 'card_holder_name': 200,
    'received_quantity': 200,
}


class ApiError(Exception):
    def __init__(self, message, code='PAYMENTS_BAD_REQUEST', status=400, extra=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra or {}


def jsonify(payload, status=200):
    """Все ответы раздела идут через plain(): Decimal → число, даты → isoformat без «GMT»."""
    return _flask_jsonify(plain(payload)), status


def as_int(value, field=None):
    if value in (None, '', 'null'):
        return None
    if isinstance(value, bool):
        raise ApiError('Поле «%s»: ожидается число' % (field or 'id'))
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError('Поле «%s»: ожидается число' % (field or 'id'))


def as_date(value, field=None):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    raise ApiError('Поле «%s»: дата не разобрана' % (field or 'дата'))


def as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'да', 'on')


def as_text(value, field, *, required=False, limit=None):
    text = str(value or '').strip()
    if required and not text:
        raise ApiError('Заполните поле «%s»' % field)
    limit = limit or _MAX_LENGTHS.get(field)
    if limit and len(text) > limit:
        raise ApiError('Поле «%s» длиннее %s символов' % (field, limit))
    return text or None


def as_choice(value, allowed, message):
    """Значение из закрытого списка либо None, если пусто. Чужое значение — ошибка."""
    text = str(value or '').strip().lower()
    if not text or text == 'null':
        return None
    if text not in allowed:
        raise ApiError(message, code='PAYMENT_REQUEST_INVALID')
    return text


def as_money(value, field, *, allow_empty=True):
    """Сумма числом (Decimal) либо None. Не число и отрицательное — ошибка."""
    from . import workflow

    if value in (None, '', 'null'):
        if allow_empty:
            return None
        raise ApiError('Заполните поле «%s»' % field)
    if isinstance(value, bool):
        raise ApiError('Поле «%s»: ожидается сумма' % field)
    number = workflow.to_decimal(value, None)
    if number is None:
        raise ApiError('Поле «%s»: ожидается сумма' % field)
    if number < 0:
        raise ApiError('Поле «%s»: сумма не может быть отрицательной' % field)
    if number > workflow.MAX_MONEY:
        raise ApiError('Поле «%s»: слишком большая сумма' % field)
    return number.quantize(workflow.MONEY_STEP)


def digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def party_kind(value):
    return as_choice(value, schema.PARTY_KINDS, 'Форма: ТОО, ИП или другое')


def payload():
    """JSON-тело либо поле `payload` формы multipart (когда рядом файлы)."""
    if request.content_type and 'multipart/form-data' in request.content_type.lower():
        raw = request.form.get('payload')
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                raise ApiError('Поле payload — не JSON')
            if not isinstance(data, dict):
                raise ApiError('Поле payload — не объект')
            return data
        return {key: request.form.get(key) for key in request.form.keys()}
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _json_list(name):
    values = request.form.getlist(name)
    if len(values) == 1 and values[0].startswith('['):
        try:
            parsed = json.loads(values[0])
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return values


def uploaded_files():
    """[(FileStorage, вид, номер поставщика | None)] из multipart.

    Файлы — полем `files`, виды — `kinds` (JSON-массив либо один `kind` на все).
    `offers` — JSON-массив той же длины: порядковый номер варианта поставщика,
    к которому относится файл (его коммерческое предложение), либо null.
    """
    if not request.files:
        return []
    storages = [item for item in request.files.getlist('files') if item and item.filename]
    kinds = _json_list('kinds')
    offers = _json_list('offers')
    default_kind = request.form.get('kind') or 'other'
    result = []
    for index, storage in enumerate(storages):
        kind = kinds[index] if index < len(kinds) else default_kind
        if kind not in schema.ATTACHMENT_KINDS:
            kind = 'other'
        offer = offers[index] if index < len(offers) else None
        try:
            offer = int(offer) if offer not in (None, '', 'null') else None
        except (TypeError, ValueError):
            offer = None
        result.append((storage, kind, offer))
    return result


def actor(ctx):
    return {'id': ctx['user_id'], 'name': ctx.get('name')}
