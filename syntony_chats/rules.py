"""Чистые правила вебхуков чатов Syntony: подпись, разбор события, строки для базы.

Ни Flask, ни базы, ни сети. Поля событий и объектов — §4 и §9 документа Syntony
«API истории чатов и вебхуки чатов» (версия 1). Незнакомые поля не теряются: они ложатся
в колонку extra той же строки («необязательные поля и новые значения перечислений
добавляются в рамках версии 1»).
"""

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone

ENV_SECRET = 'SYNTONY_CHATS_WEBHOOK_SECRET'

SIGNATURE_HEADER = 'X-Syntony-Signature'
TIMESTAMP_HEADER = 'X-Syntony-Timestamp'

# «X-Syntony-Timestamp отличается от вашего времени не больше чем на 5 минут» (§9.4).
MAX_SKEW_SECONDS = 300
# Событие — одно сообщение со снимком диалога: на живых данных 0,7 КБ в среднем, 2,5 КБ
# самое большое. Мегабайт — с запасом на длинный текст и не даёт забить память мусором.
MAX_BODY_BYTES = 1024 * 1024

EVENT_MESSAGE_CREATED = 'message.created'
EVENT_MESSAGE_STATUS = 'message.status'
EVENT_TEST = 'webhook.test'

VIA_WEBHOOK = 'webhook'
VIA_STATUS = 'status'          # строка заведена событием статуса раньше самого сообщения

# Статус исходящего — только «вперёд»: sent → delivered → read, failed — конечный (§9.5).
# received (входящее), pending, unknown и незнакомые новые значения — нулевой ранг: в новую
# строку они пишутся как есть, но известный статус не перебивают.
STATUS_RANKS = {'sent': 1, 'delivered': 2, 'read': 3, 'failed': 4}
# Момент каждого статуса — своей колонкой: «прочитано» могло прийти раньше «доставлено»,
# и время доставки всё равно нужно.
MILESTONES = {'sent': 'sent_at', 'delivered': 'delivered_at', 'read': 'read_at',
              'failed': 'failed_at'}


class PayloadError(ValueError):
    """Тело события не разобрать. code уходит в ответ Syntony (она покажет его в карточке)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def load_secrets(environ=None):
    """Секреты подписи из окружения: один или несколько через запятую или пробел (у клиники
    до трёх вебхуков, у каждого свой секрет). Пусто — приёмник не настроен."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_SECRET) or '')
    return [part for part in re.split(r'[\s,;]+', raw) if part]


def _signed_at(text):
    """Время подписи в секундах. Цифры — секунды; число с 12+ знаками примем за
    миллисекунды (подпись от этого не зависит — она считается по строке заголовка)."""
    if not re.fullmatch(r'\d{1,13}', text):
        return None
    number = int(text)
    return number / 1000.0 if number >= 10 ** 11 else float(number)


def verify_signature(raw_body, headers, secrets, now):
    """Подпись Syntony (§9.4) → None, если сошлась, иначе код отказа.

    X-Syntony-Signature = hex(HMAC_SHA256(секрет, timestamp + "." + сырое тело)).
    Сравнение — БАЙТАМИ и постоянным по времени: compare_digest на строках с не-ASCII
    падает TypeError, а 500 на каждую доставку через 6 часов выключит вебхук.
    """
    ts_text = str(headers.get(TIMESTAMP_HEADER) or '').strip()
    sig_text = str(headers.get(SIGNATURE_HEADER) or '').strip()
    if not ts_text or not sig_text:
        return 'missing_signature'
    signed_at = _signed_at(ts_text)
    if signed_at is None:
        return 'bad_timestamp'
    if abs(now - signed_at) > MAX_SKEW_SECONDS:
        return 'stale_timestamp'
    if sig_text[:7].lower() == 'sha256=':
        sig_text = sig_text[7:]
    try:
        got = bytes.fromhex(sig_text)
    except ValueError:
        return 'bad_signature'
    if len(got) != hashlib.sha256().digest_size:
        return 'bad_signature'
    message = ts_text.encode('ascii') + b'.' + bytes(raw_body)
    for secret in secrets:
        expected = hmac.new(secret.encode('utf-8'), message, hashlib.sha256).digest()
        if hmac.compare_digest(got, expected):
            return None
    return 'bad_signature'


_UNSTORABLE = re.compile('[\x00\ud800-\udfff]')


def _storable(text):
    # Половинка эмодзи (\\ud83d без пары) приходит из JSON отдельным символом, и драйвер
    # не может записать её в UTF-8 — заменяем знаком «�», NUL убираем.
    return _UNSTORABLE.sub(lambda match: '' if match.group() == '\x00' else '�', text)


def clean(value):
    """Строки на любой глубине — в то, что Postgres запишет: без NUL (его не принимают ни
    text, ни jsonb) и без одиноких половинок суррогатной пары. Отказ записи на одном
    сообщении держал бы всю очередь вебхука до суток."""
    if isinstance(value, str):
        return _storable(value) if _UNSTORABLE.search(value) else value
    if isinstance(value, dict):
        return {clean(key): clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def parse_time(value):
    """ISO 8601 → время с поясом (UTC, если пояс не указан: «даты — ISO 8601 в UTC»).
    Не время — None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1:] in ('Z', 'z'):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _text(value):
    """Идентификатор, имя, значение перечисления → строка; пусто — None."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


_INT_MAX = 2 ** 31 - 1


def _int(value):
    """Счётчик → int в пределах INTEGER Postgres; иначе None (счётчик не стоит события)."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if -_INT_MAX <= number <= _INT_MAX else None


def _obj(value):
    return value if isinstance(value, dict) else {}


def leftover(obj, known):
    """Всё, чего нет в описании known, — для колонки extra. known: ключ → None (поле
    разобрано целиком) или вложенное описание. Ничего лишнего — None."""
    if not isinstance(obj, dict):
        return None
    out = {}
    for key, value in obj.items():
        if key not in known:
            out[key] = value
        elif isinstance(known[key], dict) and isinstance(value, dict):
            nested = leftover(value, known[key])
            if nested:
                out[key] = nested
    return out or None


CONVERSATION_FIELDS = {
    'id': None,
    'park': {'id': None, 'name': None},
    'channel': {'id': None, 'type': None, 'provider': None, 'name': None, 'phoneNumber': None},
    'contact': {'id': None, 'phone': None, 'name': None, 'externalId': None},
    'status': None,
    'assignedTo': {'kind': None, 'user': {'id': None, 'fullName': None}},
    'messageCount': None,
    'createdAt': None, 'lastMessageAt': None, 'lastInboundAt': None,
    'closedAt': None, 'closedReason': None,
    'leadId': None,
    'handoff': None,
}

MESSAGE_FIELDS = {
    'id': None, 'conversationId': None,
    'direction': None, 'senderType': None, 'origin': None, 'source': None,
    'author': {'id': None, 'fullName': None},
    'contentType': None, 'text': None, 'media': None,
    'externalMessageId': None, 'replyToMessageId': None,
    'status': None, 'statusAt': None, 'error': None,
    'createdAt': None, 'editedAt': None, 'deletedAt': None, 'deleteScope': None,
}


def conversation_row(conversation, snapshot_at):
    """Диалог Syntony (§4) → строка syntony_chat_conversations. Без id — None.

    snapshot_at — на какой момент снимок (occurredAt события): старый снимок, пришедший
    повтором позже нового, новый не перепишет.
    """
    if not isinstance(conversation, dict):
        return None
    conv = clean(conversation)
    conversation_id = _text(conv.get('id'))
    if not conversation_id:
        return None
    park, channel, contact = _obj(conv.get('park')), _obj(conv.get('channel')), _obj(conv.get('contact'))
    assigned = _obj(conv.get('assignedTo'))
    user = _obj(assigned.get('user'))
    handoff = conv.get('handoff')
    return {
        'id': conversation_id,
        'park_id': _text(park.get('id')),
        'park_name': _text(park.get('name')),
        'channel_id': _text(channel.get('id')),
        'channel_type': _text(channel.get('type')),
        'channel_provider': _text(channel.get('provider')),
        'channel_name': _text(channel.get('name')),
        'channel_phone': _text(channel.get('phoneNumber')),
        'contact_id': _text(contact.get('id')),
        'contact_phone': _text(contact.get('phone')),
        'contact_name': _text(contact.get('name')),
        'contact_external_id': _text(contact.get('externalId')),
        'status': _text(conv.get('status')),
        'assigned_kind': _text(assigned.get('kind')),
        'assigned_user_id': _text(user.get('id')),
        'assigned_user_name': _text(user.get('fullName')),
        'message_count': _int(conv.get('messageCount')),
        'created_at': parse_time(conv.get('createdAt')),
        'last_message_at': parse_time(conv.get('lastMessageAt')),
        'last_inbound_at': parse_time(conv.get('lastInboundAt')),
        'closed_at': parse_time(conv.get('closedAt')),
        'closed_reason': _text(conv.get('closedReason')),
        'lead_id': _text(conv.get('leadId')),
        'handoff': handoff if isinstance(handoff, dict) else None,
        'extra': leftover(conv, CONVERSATION_FIELDS),
        'snapshot_at': snapshot_at,
    }


def status_fields(status, status_at, error=None):
    """Колонки статуса: сам статус, его ранг и момент, ошибка провайдера (только у failed —
    код Meta или Wazzup «в том виде, в каком он пришёл») и момент этого статуса."""
    status = _text(status)
    error = _obj(error)
    fields = {
        'status': status,
        'status_rank': STATUS_RANKS.get(status, 0),
        'status_at': status_at,
        'error_code': _text(error.get('code')),
        'error_message': _text(error.get('message')),
        'sent_at': None, 'delivered_at': None, 'read_at': None, 'failed_at': None,
    }
    milestone = MILESTONES.get(status)
    if milestone and status_at is not None:
        fields[milestone] = status_at
    return fields


def message_row(message, content_at, via):
    """Сообщение Syntony (§4) → строка syntony_chat_messages. Без id — None.

    content_at — на какой момент содержимое: им старое не перепишет новое.
    """
    if not isinstance(message, dict):
        return None
    msg = clean(message)
    message_id = _text(msg.get('id'))
    if not message_id:
        return None
    author = _obj(msg.get('author'))
    media = msg.get('media')
    text = msg.get('text')
    row = {
        'id': message_id,
        'conversation_id': _text(msg.get('conversationId')),
        'direction': _text(msg.get('direction')),
        'sender_type': _text(msg.get('senderType')),
        'origin': _text(msg.get('origin')),
        'source': _text(msg.get('source')),
        'author_id': _text(author.get('id')),
        'author_name': _text(author.get('fullName')),
        'content_type': _text(msg.get('contentType')),
        # Текст — как есть: пустая подпись голосового остаётся пустой строкой.
        'text': text if isinstance(text, str) else None,
        'media': media if isinstance(media, dict) else None,
        'external_message_id': _text(msg.get('externalMessageId')),
        'reply_to_message_id': _text(msg.get('replyToMessageId')),
        'created_at': parse_time(msg.get('createdAt')),
        'edited_at': parse_time(msg.get('editedAt')),
        'deleted_at': parse_time(msg.get('deletedAt')),
        'delete_scope': _text(msg.get('deleteScope')),
        'extra': leftover(msg, MESSAGE_FIELDS),
        'content_at': content_at,
        'first_seen_via': via,
    }
    row.update(status_fields(msg.get('status'), parse_time(msg.get('statusAt')), msg.get('error')))
    return row


def status_event_row(data, occurred_at):
    """data события message.status (§9.3) → поля статуса для строки сообщения. Без
    messageId — None. Момент статуса — statusAt, нет его — время события."""
    if not isinstance(data, dict):
        return None
    data = clean(data)
    message_id = _text(data.get('messageId'))
    if not message_id:
        return None
    row = {'id': message_id, 'conversation_id': _text(data.get('conversationId')),
           'first_seen_via': VIA_STATUS}
    row.update(status_fields(data.get('status'),
                             parse_time(data.get('statusAt')) or occurred_at,
                             data.get('error')))
    return row


def parse_event(raw_body):
    """Тело запроса → {'event', 'event_id', 'occurred_at', 'webhook_id', 'data'}.

    Тело подписано, заголовки X-Syntony-Event-Id/-Type — нет, поэтому верим телу.
    """
    try:
        body = json.loads(bytes(raw_body).decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise PayloadError('bad_json') from None
    if not isinstance(body, dict):
        raise PayloadError('bad_json')
    event = _text(body.get('event'))
    event_id = _text(body.get('eventId'))
    if not event or not event_id:
        raise PayloadError('bad_event')
    data = body.get('data')
    if data is not None and not isinstance(data, dict):
        raise PayloadError('bad_event')
    return {
        'event': event,
        'event_id': event_id,
        'occurred_at': parse_time(body.get('occurredAt')),
        'webhook_id': _text(body.get('webhookId')),
        'data': data or {},
    }
