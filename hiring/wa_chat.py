# -*- coding: utf-8 -*-
"""Переписка с кандидатами из портала (владелец 19.11.2026: «сделать интерфейс обработки чата ватсапа прямо
как в разделе чаты ОП»): чистые правила без базы — что можно отправить, как сообщение идёт через очередь к
сервису на шлюзе и обратно, какие отметки доставки ему ставить, сколько сообщений ждут ответа.

Путь сообщения: рекрутер пишет в окне «Чаты» → строка в hiring_wa_outbox (queued) → сервис шлюза забирает её в
ответе на опрос (handed) и отправляет связанным устройством номера найма → событие sent с id WhatsApp (сообщение
ложится в переписку с автором) или send_failed (строка «Не отправлено» с «Повторить»). Отметки «доставлено /
прочитано» приходят от шлюза событием receipt — и на ответы из портала, и на ответы с телефона.

Защита номера: новый чат портал начинает только из карточки кандидата, по одному и руками рекрутера (кнопка
«WhatsApp», владелец 20.11.2026: «Написать первым с номера найма»), рассылок нет; пока WhatsApp запретил номеру
первые сообщения незнакомым, написать первым нельзя; шлюз отправляет по одному с паузой; сообщение, пролежавшее в
очереди дольше SEND_EXPIRE (шлюз был не на связи), не уходит — «Не отправлено», чтобы «Сегодня в 14:00?» не пришло
кандидату на следующий день.
"""

import re
from datetime import datetime, timedelta

from . import rules, whatsapp

MAX_TEXT = 4096                       # длина текста WhatsApp
MAX_FILE = 16 * 1024 * 1024           # предел файла, который отправляется из портала (фото и видео WhatsApp)
CLIENT_ID_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')

QUEUED, HANDED, SENT, FAILED, DISCARDED = 'queued', 'handed', 'sent', 'failed', 'discarded'
OUTBOX_STATES = (QUEUED, HANDED, SENT, FAILED, DISCARDED)
SEND_EXPIRE = timedelta(minutes=30)   # дольше в очереди — не отправляем (шлюз был не на связи)
# Взятое шлюзом: шлюз сам не отправляет позже срока (expires_in в ответе опроса), портал переводит такое
# в «Не отправлено» с запасом на последнюю попытку шлюза (таймаут отправки 90 с) — после этого «Убрать» и
# «Повторить» безопасны: шлюз его уже точно не отправит.
HANDED_EXPIRE = SEND_EXPIRE + timedelta(minutes=3)
HAND_RETRY = timedelta(minutes=2)     # шлюз взял и не ответил (потерян ответ опроса) — отдать снова
HAND_ATTEMPTS = 3                     # шлюз сам отбрасывает повтор по id: двойной отправки нет
HAND_BATCH = 5                        # за один опрос

# Отметки доставки наших сообщений: только вперёд («прочитано» не откатывается к «доставлено»).
STATUS_ORDER = {'sent': 1, 'delivered': 2, 'read': 3, 'played': 4}
RECEIPT_STATUSES = ('delivered', 'read', 'played')

PORTAL_TOUCH = 'wa_portal'            # meta.source касаний по ответам из портала: одно в день, как у телефона
TEMPLATE_TITLE_MAX = 80
TEMPLATE_BODY_MAX = MAX_TEXT

# Вид файла для WhatsApp: фото и видео — с превью в ленте кандидата, голос — аудио, остальное — документом.
_IMAGE_TYPES = ('image/jpeg', 'image/png')
_VIDEO_TYPES = ('video/mp4', 'video/3gpp')
_AUDIO_TYPES = ('audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/amr')

FAILURE_TEXTS = {
    'expired': 'Не отправлено: номер был не на связи дольше 30 минут — отправьте заново',
    'unconfirmed': 'Шлюз не подтвердил отправку — проверьте чат на телефоне, прежде чем повторять',
    'not_connected': 'Связь с WhatsApp прервалась во время отправки — проверьте чат на телефоне, прежде чем повторять',
    'bad_chat': 'Чат кандидата не найден в WhatsApp',
    'too_large': 'Файл больше 16 МБ',
    'no_file': 'Файл не нашёлся — прикрепите его заново',
    'upload': 'WhatsApp не принял файл — попробуйте ещё раз',
    'rejected': 'WhatsApp не принял сообщение',
    'rate': 'Слишком много сообщений подряд — шлюз приостановил отправку',
}


def clean_text(value):
    """Текст сообщения: переводы строк к \\n, хвостовые пробелы строк прочь, пустые края — тоже."""
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    text = '\n'.join(line.rstrip() for line in text.split('\n')).strip('\n').strip()
    return text


def send_kind(mime):
    """Вид исходящего файла по типу: image / video / audio / document."""
    base = str(mime or '').split(';', 1)[0].strip().lower()
    if base in _IMAGE_TYPES:
        return 'image'
    if base in _VIDEO_TYPES:
        return 'video'
    if base in _AUDIO_TYPES:
        return 'audio'
    return 'document'


def valid_client_id(value):
    text = str(value or '').strip()
    return text if CLIENT_ID_RE.match(text) else None


def later_status(old, new):
    """Отметка после события: «прочитано» не откатывается к «доставлено»."""
    if STATUS_ORDER.get(new, 0) > STATUS_ORDER.get(old, 0):
        return new
    return old


def failure_text(reason, detail=None):
    text = FAILURE_TEXTS.get(reason)
    if text:
        return text
    detail = ' '.join(str(detail or '').split())[:160]
    return ('Не отправлено: %s' % detail) if detail else 'Не отправлено'


def expired(created_at, now):
    return created_at is not None and now - created_at > SEND_EXPIRE


# ─── События шлюза ───────────────────────────────────────────────────────────

def _positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_sent(raw):
    """{type: sent, outbox, id, ts} → {outbox, wa_id, at} или None."""
    if not isinstance(raw, dict) or raw.get('type') != 'sent':
        return None
    outbox = _positive(raw.get('outbox'))
    wa_id = whatsapp.clean_message_id(raw.get('id'))
    if not outbox or not wa_id:
        return None
    return {'outbox': outbox, 'wa_id': wa_id, 'at': whatsapp.moment_of(raw.get('ts'))}


def parse_send_failed(raw):
    """{type: send_failed, outbox, reason, error} → {outbox, reason, error} или None."""
    if not isinstance(raw, dict) or raw.get('type') != 'send_failed':
        return None
    outbox = _positive(raw.get('outbox'))
    if not outbox:
        return None
    reason = str(raw.get('reason') or '').strip()[:32] or 'rejected'
    error = ' '.join(str(raw.get('error') or '').split())[:300] or None
    return {'outbox': outbox, 'reason': reason, 'error': error}


def parse_receipt(raw):
    """{type: receipt, ids, status, ts} → {ids, status, at} или None. Доставка на телефон кандидата, прочитано,
    прослушано — на наши сообщения (из портала и с телефона)."""
    if not isinstance(raw, dict) or raw.get('type') != 'receipt':
        return None
    status = str(raw.get('status') or '').strip().lower()
    if status not in RECEIPT_STATUSES:
        return None
    ids = raw.get('ids') if isinstance(raw.get('ids'), list) else []
    clean = [wa_id for wa_id in (whatsapp.clean_message_id(item) for item in ids[:100]) if wa_id]
    if not clean:
        return None
    return {'ids': list(dict.fromkeys(clean)), 'status': status, 'at': whatsapp.moment_of(raw.get('ts'))}


# ─── Очередь → шлюз ──────────────────────────────────────────────────────────

def quote_preview(kind, body):
    """Текст цитаты в сообщении WhatsApp (кандидат видит его над ответом)."""
    text = ' '.join(str(body or '').split())
    if text:
        return text[:300]
    label = whatsapp.KIND_LABELS.get(kind)
    return label or 'Сообщение'


def bridge_item(row, now):
    """Строка очереди → то, что получает шлюз в ответе на опрос. expires_in — сколько секунд ещё можно отправлять
    (относительно: время в базе наивное, Алматы, а часы шлюза свои): позже шлюз не отправит, а сообщит expired."""
    left = (row['created_at'] + SEND_EXPIRE - now).total_seconds() if row.get('created_at') else SEND_EXPIRE.total_seconds()
    item = {'id': int(row['id']), 'chat': row['jid'], 'kind': row['kind'], 'text': row.get('body') or '',
            'expires_in': max(1, int(left))}
    if row.get('quote_wa_id'):
        item['quote'] = {'id': row['quote_wa_id'], 'from_me': bool(row.get('quote_from_me')),
                         'text': quote_preview(row.get('quote_kind'), row.get('quote_body'))}
    media = row.get('media') or None
    if media and row['kind'] != 'text':
        item['media'] = {'mime': media.get('mime') or 'application/octet-stream', 'size': int(media.get('size') or 0),
                         'name': media.get('file_name') or ''}
    return item


# ─── Список чатов ────────────────────────────────────────────────────────────

def preview(kind, body, deleted=False):
    """Последнее сообщение в строке списка: текст или вид файла."""
    if deleted:
        return 'Сообщение удалено'
    text = ' '.join(str(body or '').split())
    if kind and kind != 'text':
        label = whatsapp.KIND_LABELS.get(kind, 'Сообщение')
        return '%s · %s' % (label, text[:120]) if text else label
    return text[:160] or 'Сообщение'


def chat_title(row, hidden=False):
    """Имя в списке: ФИО карточки, иначе имя из WhatsApp, иначе номер. Тому, кому номер не виден (удалённый
    рекрутер в пуле, ТЗ 2), — номер кандидата, а не телефон."""
    for value in (row.get('full_name'), row.get('push_name')):
        text = ' '.join(str(value or '').split())
        if text and text not in ('.', '-'):
            return text[:120]
    if hidden:
        return 'Кандидат №%s' % row['candidate_no'] if row.get('candidate_no') else 'Кандидат без имени'
    return rules.pretty_phone(row.get('phone')) if row.get('phone') else 'Без имени'


def chat_ref(row, hidden):
    """Адрес чата для окна: ключ — это номер телефона, поэтому тому, кому номер не виден, — номер карточки."""
    if hidden and row.get('card_id'):
        return 'card:%d' % int(row['card_id'])
    return row['key']


def card_ref(ref):
    """'card:123' → 123, иначе None."""
    text = str(ref or '')
    if text.startswith('card:') and text[5:].isdigit():
        return int(text[5:])
    return None


def can_handle(block, *, lead, hidden, waiting):
    """«Ответ не нужен» — тому, кто вправе отвечать или взять отклик; чат без карточки — руководителю; пока шлюз
    старый, гасить можно (отвечают с телефона). Удалённому рекрутеру, переписку не видящему, — нет."""
    if hidden or not waiting:
        return False
    if block == 'no_card':
        return bool(lead)
    return block in (None, 'take', 'bridge')


def send_block(card, *, involved, owner_or_lead, can_take, connected):
    """Почему в этот чат сейчас нельзя написать (None — можно): нет карточки, отклик в пуле (сначала «Взять в
    работу» — карточку ведёт тот, кто взял), чужая карточка, только чтение. Номер не на связи — не запрет:
    сообщение уйдёт, когда подключится (не дольше SEND_EXPIRE), окно покажет предупреждение."""
    del connected
    if not card:
        return 'no_card'
    if card.get('stage') in rules.POOL_STAGES and not card.get('is_closed'):
        return 'take' if can_take else 'read_only'
    if not involved or not owner_or_lead:
        return 'read_only'
    return None


SEND_BLOCK_TEXTS = {
    'no_card': 'У чата нет карточки кандидата — ответить можно с телефона',
    'take': 'Возьмите отклик в работу, чтобы ответить — карточка закрепится за вами',
    'read_only': 'Чат открыт вам только для чтения — отвечает закреплённый рекрутер',
    'bridge': 'Ответ из портала заработает после обновления сервиса WhatsApp на шлюзе — пока отвечайте с телефона',
    # Текст со сроком — из состояния номера (whatsapp.reachout_lock); этот — если срока нет.
    'first_lock': 'WhatsApp ограничил номеру первые сообщения незнакомым — кандидат вам ещё не писал, написать '
                  'первым пока нельзя',
}


def touch_text(kind, body):
    """Текст касания по ответу из портала: что написал рекрутер (начало), файл — его видом."""
    text = ' '.join(str(body or '').split())
    if text:
        return 'Ответ в чате: «%s»' % (text if len(text) <= 300 else text[:299] + '…')
    label = whatsapp.KIND_LABELS.get(kind)
    return 'Ответ в чате: %s' % label.lower() if label else 'Ответ в чате'


def day_bounds(moment):
    start = datetime.combine(moment.date(), datetime.min.time())
    return start, start + timedelta(days=1)


def clean_template(payload):
    """Шаблон ответа: название и текст. ValueError — с текстом для человека."""
    payload = payload or {}
    title = ' '.join(str(payload.get('title') or '').split())[:TEMPLATE_TITLE_MAX]
    body = clean_text(payload.get('body'))[:TEMPLATE_BODY_MAX]
    if not title:
        raise ValueError('Назовите шаблон')
    if not body:
        raise ValueError('Шаблон пустой')
    return {'title': title, 'body': body}
