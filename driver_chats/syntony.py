"""«Чаты водителей» на Syntony (с 25.11.2026).

ЗАЧЕМ. 16–23.11.2026 номера СЗоВ переехали из Chat2Desk в Syntony, и раздел, читавший только
Chat2Desk, опустел: замер 25.11 — до 18.11 переписка находилась в половине поисков, 21.11 — в
0 из 132, 24.11 — в 6 из 253; из 196 номеров, которые искали после 20.11, Chat2Desk показал 12,
а в Syntony на номерах СЗоВ переписка была у 126. Последний живой номер Chat2Desk («Ноль такси»
10908) с 20.11 — копия того же номера в Syntony: сообщения приходят в обе системы, отвечают в
Syntony.

ОТКУДА ПЕРЕПИСКА. Из своей базы — syntony_chat_* (вебхук и сторож API истории, syntony_chats/):
ни поиск, ни «Обновить» в API Syntony не ходят, поиск стоит один запрос к базе. Номера — те же
17 номеров СЗоВ, что у табло, отбивки и биллинга (syntony_chats.szov.channel_ids), окно и правило
«чат = таксопарк (канал), вся история двух суток внутри» — прежние (chat2desk.group_chats).

ВЛОЖЕНИЯ. Ссылка Syntony на файл живёт 5 минут и хранить её нельзя (§3.5), а лента показывает
медиа прямыми адресами в <img>/<audio> — заголовок с токеном портала они не несут (сайт и API на
разных адресах, вход по токену в заголовке, а не по cookie). Поэтому в ленте — адрес НАШЕЙ ручки
/api/driver_chats/media/<id>, подписанный на 2–3 часа (HMAC от JWT_SECRET, привязан к сообщению и
человеку); ручка проверяет подпись и доступ, берёт у Syntony свежую ссылку и переадресует на неё.

«ПЕРЕДАН». В API истории внутренних заметок нет — он только читает, отправка в чат — партнёрский
API, отдельным документом. Поэтому отметка «Передан» живёт в журнале раздела (kind='handoff',
source='syntony') и показывается в ленте раздела внутренним комментарием своего чата, а в сам
Syntony НЕ уходит — экран так и говорит.

ОТКАТ. DRIVER_CHATS_SOURCE=chat2desk — прежний путь целиком (routes.py).
"""

import hashlib
import hmac
import os
import re
import time
from datetime import datetime, time as dtime, timedelta
from urllib.parse import quote, urlencode

from syntony_chats import billing as syntony_billing
from syntony_chats import szov, sync, thread

from . import chat2desk

SOURCE_SYNTONY = 'syntony'
SOURCE_CHAT2DESK = 'chat2desk'
ENV_SOURCE = 'DRIVER_CHATS_SOURCE'

# Сколько сообщений окна отдаём за раз. Двое суток одного водителя на номерах СЗоВ — десятки
# сообщений (замер 23–25.11: в среднем ~6 на диалог), потолок — от разговора-аномалии.
MESSAGES_CAP = 3000

# Подпись ссылки на вложение. Срок — до конца часа плюс два: ссылки одного часа одинаковые,
# и браузер берёт картинку из кеша при повторном открытии того же чата.
MEDIA_TTL_HOURS = 2
MEDIA_PATH = '/api/driver_chats/media/'
_MEDIA_KEY_SALT = b'driver_chats.media.v1'

# Данные Syntony «замерли», если сторож давно не отмечал удачный прогон и вебхук за то же время
# ничего не принёс — то же правило, что у табло (szov._check_fresh).
STALE_SECONDS = szov.STALE_SECONDS

# Форма сообщения ленты — общая с проверкой качества (syntony_chats/thread.py).
AI_LABEL = thread.AI_LABEL
SYSTEM_CALL_TEXT = thread.SYSTEM_CALL_TEXT
DELETED_BY_DRIVER_TEXT = thread.DELETED_BY_DRIVER_TEXT
DELETED_TEXT = thread.DELETED_TEXT

# id Syntony — GUID строкой; в адрес ручки и в журнал пускаем только безопасный вид.
_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')


def source(environ=None):
    """Откуда раздел берёт переписку: 'syntony' или 'chat2desk'.

    DRIVER_CHATS_SOURCE задаёт явно; без него — Syntony, если её переписка вообще идёт в базу
    (сторож API или вебхук: szov.data_enabled), иначе прежний Chat2Desk."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_SOURCE) or '').strip().lower()
    if raw in (SOURCE_SYNTONY, SOURCE_CHAT2DESK):
        return raw
    return SOURCE_SYNTONY if szov.data_enabled(environ) else SOURCE_CHAT2DESK


def channel_ids(environ=None):
    """Номера СЗоВ в Syntony — тот же список, что у табло и биллинга."""
    return sorted(szov.channel_ids(environ))


def clean_id(value):
    """id Syntony из запроса браузера → строка или None (мусор в журнал не пишем)."""
    text = str(value or '').strip()
    return text if _ID_RE.match(text) else None


def _digits(value):
    return re.sub(r'\D', '', str(value or ''))


_local_iso = thread.local_iso


def window_range(window_from, window_to):
    """Даты окна раздела (Алматы, включительно) → [начало, конец) с поясом."""
    start = datetime.combine(window_from, dtime(0, 0), tzinfo=szov.LOCAL_TZ)
    end = datetime.combine(window_to + timedelta(days=1), dtime(0, 0), tzinfo=szov.LOCAL_TZ)
    return start, end


# ─────────────────────────────────────────────────────────────────────────────
# Водитель по номеру
# ─────────────────────────────────────────────────────────────────────────────

# Номер собеседника у Syntony — цифры целиком (77011234567). Ищем по девяти последним цифрам —
# выражение СЛОВО В СЛОВО как в индексе idx_syntony_chat_conversations_phone_tail
# (syntony_chats/schema.py): так находится и номер, продиктованный без кода страны.
_CONTACT_SQL = r"""
    SELECT c.contact_phone,
           (array_agg(c.contact_name ORDER BY c.last_message_at DESC NULLS LAST)
                FILTER (WHERE coalesce(c.contact_name, '') <> ''))[1]
      FROM syntony_chat_conversations c
     WHERE right(regexp_replace(c.contact_phone, '\D', '', 'g'), 9) = %(tail)s
       AND c.channel_id = ANY(%(channels)s)
     GROUP BY c.contact_phone
     ORDER BY max(c.last_message_at) DESC NULLS LAST
     LIMIT 10
"""


def find_contact(cursor, phone, tail, channels):
    """Водитель на номерах СЗоВ → (номер как у Syntony, имя) | ('ambiguous', None) | None.

    Номер целиком (phone) выбирает свою запись среди совпавших по хвосту; без него — хвост
    обязан указывать на одного человека: показать переписку соседа нельзя, а «не найдено»
    было бы неправдой (тот же ответ, что у Chat2Desk-пути, PHONE_AMBIGUOUS)."""
    tail = tail or (chat2desk.phone_tail(phone) if phone else None)
    if not tail:
        return None
    cursor.execute(_CONTACT_SQL, {'tail': str(tail), 'channels': list(channels)})
    by_digits = {}
    for row in cursor.fetchall():
        if not row or not row[0]:
            continue
        number = str(row[0]).strip()
        by_digits.setdefault(_digits(number), (number, row[1] or None))
    by_digits.pop('', None)
    if not by_digits:
        return None
    if phone:
        for variant in chat2desk.phone_variants(phone):
            hit = by_digits.get(_digits(variant))
            if hit:
                return hit
    if len(by_digits) == 1:
        return next(iter(by_digits.values()))
    return ('ambiguous', None)


# ─────────────────────────────────────────────────────────────────────────────
# Переписка окна
# ─────────────────────────────────────────────────────────────────────────────

_WINDOW_SQL = r"""
    SELECT m.id, m.conversation_id, m.direction, m.sender_type, m.author_name,
           m.content_type, m.text, m.media, m.created_at, m.delete_scope, m.status,
           c.channel_id, c.park_name, c.channel_name
      FROM syntony_chat_conversations c
      JOIN syntony_chat_messages m ON m.conversation_id = c.id
     WHERE right(regexp_replace(c.contact_phone, '\D', '', 'g'), 9) = %(tail)s
       AND c.contact_phone = %(phone)s
       AND c.channel_id = ANY(%(channels)s)
       AND m.created_at >= %(start)s AND m.created_at < %(end)s
     ORDER BY m.created_at DESC, m.id DESC
     LIMIT %(limit)s
"""


def window_rows(cursor, phone, channels, start, end, cap=MESSAGES_CAP):
    """Сообщения водителя на номерах СЗоВ за [start, end) → (строки от старых к новым, обрезано ли).

    Больше потолка — берём самые свежие и честно говорим об этом (truncated), как Chat2Desk."""
    cursor.execute(_WINDOW_SQL, {
        'tail': _digits(phone)[-chat2desk.PHONE_TAIL_DIGITS:], 'phone': str(phone),
        'channels': list(channels), 'start': start, 'end': end, 'limit': int(cap) + 1})
    rows = cursor.fetchall()
    truncated = len(rows) > cap
    return list(reversed(rows[:cap])), truncated


def channel_name(channel_id, park_name=None, fallback=None):
    """Таксопарк чата — тем же именем, что у Chat2Desk в c2d_requests (биллинг держит один парк
    под одним именем, syntony_chats.billing.PARK_BY_CHANNEL); незнакомый номер — парком Syntony."""
    return (syntony_billing.PARK_BY_CHANNEL.get(str(channel_id or ''))
            or (str(park_name).strip() if park_name else None)
            or (str(fallback).strip() if fallback else None))


def channel_names(rows):
    """{канал: имя таксопарка} по строкам окна."""
    names = {}
    for row in rows:
        channel_id = row[11]
        if channel_id and channel_id not in names:
            names[channel_id] = channel_name(channel_id, row[12], row[13])
    return names


# Строка окна → сообщение ленты ChatThread: правило общее с проверкой качества (снимки чатов
# «Журнала оценок» и «ИИ-оценки»), поэтому живёт в syntony_chats/thread.py.
_attach_media = thread.attach_media
message_from_row = thread.message_from_row


# ─────────────────────────────────────────────────────────────────────────────
# «Передан» — отметки журнала в ленте
# ─────────────────────────────────────────────────────────────────────────────

_HANDOFFS_SQL = """
    SELECT id, comment_text, syntony_channel_id, syntony_conversation_id, created_at
      FROM dch_events
     WHERE kind = 'handoff' AND source = %(source)s AND phone = %(phone)s
       AND created_at >= %(start)s AND created_at < %(end)s
     ORDER BY created_at, id
"""


def handoff_message(*, event_id, text, created, channel_id, conversation_id=None):
    """Отметка «Передан» в форме сообщения ленты — внутренним комментарием по центру.

    id — 'dch-<строка журнала>': фронт держит свою копию только что поставленной отметки,
    пока поиск не вернёт её же из журнала, и склеивает их по id."""
    return {
        'id': 'dch-%s' % event_id,
        'type': 'comment',
        'text': text or '',
        'created': _local_iso(created) if not isinstance(created, str) else created,
        'photo': None, 'video': None, 'audio': None, 'pdf': None,
        'attachments': [],
        'status': None,
        'requestId': None,
        'dialogId': conversation_id,
        'channelId': channel_id,
        'author': None,
    }


def handoff_messages(cursor, phone, window_from, window_to):
    """Отметки «Передан» этого водителя за окно — из журнала. created_at журнала — стенные
    часы Алматы без пояса, поэтому и границы окна — такие же."""
    cursor.execute(_HANDOFFS_SQL, {
        'source': SOURCE_SYNTONY, 'phone': str(phone),
        'start': datetime.combine(window_from, dtime(0, 0)),
        'end': datetime.combine(window_to + timedelta(days=1), dtime(0, 0))})
    return [handoff_message(event_id=row[0], text=row[1], created=row[4].isoformat()
                            if hasattr(row[4], 'isoformat') else row[4],
                            channel_id=row[2], conversation_id=row[3])
            for row in cursor.fetchall() if row and row[2]]


_CONTACT_CHANNEL_SQL = r"""
    SELECT 1 FROM syntony_chat_conversations c
     WHERE right(regexp_replace(c.contact_phone, '\D', '', 'g'), 9) = %(tail)s
       AND c.contact_phone = %(phone)s AND c.channel_id = %(channel)s
     LIMIT 1
"""


def contact_has_channel(cursor, phone, channel_id):
    """Есть ли у этого водителя переписка с этим номером — отметку ставят только на свой чат."""
    cursor.execute(_CONTACT_CHANNEL_SQL, {
        'tail': _digits(phone)[-chat2desk.PHONE_TAIL_DIGITS:], 'phone': str(phone),
        'channel': str(channel_id)})
    return cursor.fetchone() is not None


# ─────────────────────────────────────────────────────────────────────────────
# Свежесть данных
# ─────────────────────────────────────────────────────────────────────────────

_FRESH_SQL = """
    SELECT (SELECT min(updated_at) FROM syntony_chat_sync_marks WHERE name = ANY(%(marks)s)),
           (SELECT count(*) FROM syntony_chat_sync_marks WHERE name = ANY(%(marks)s)),
           (SELECT max(created_at) FROM syntony_chat_messages
             WHERE created_at >= now() - interval '1 hour'),
           now()
"""


def stale_minutes(cursor, environ=None):
    """Сколько минут переписка Syntony в базе не пополняется — если дольше STALE_SECONDS.

    None — всё живо или судить не по чему (сторож API выключен, есть только вебхук, — как у
    табло). Замерло — это сторож давно не отмечал удачный прогон ленты и диалогов И вебхук за
    то же время не завёл ни одного сообщения сети."""
    if not szov._puller_on(environ):
        return None
    cursor.execute(_FRESH_SQL, {'marks': [sync.MARK_FEED, sync.MARK_CONVERSATIONS]})
    row = cursor.fetchone()
    if not row:
        return None
    marks_at, marks_count, newest, now = row
    if not marks_count or marks_at is None:
        return None
    if newest is not None and (now - newest).total_seconds() <= STALE_SECONDS:
        return None
    stalled = (now - marks_at).total_seconds()
    if stalled <= STALE_SECONDS:
        return None
    return int(stalled // 60)


# ─────────────────────────────────────────────────────────────────────────────
# Ссылки на вложения
# ─────────────────────────────────────────────────────────────────────────────

def media_key(secret):
    """Ключ подписи ссылок — свой, производный от общего секрета сервера (JWT_SECRET)."""
    if not secret:
        return None
    raw = secret.encode('utf-8') if isinstance(secret, str) else bytes(secret)
    return hmac.new(raw, _MEDIA_KEY_SALT, hashlib.sha256).digest()


def media_signature(key, message_id, user_id, expires):
    payload = ('%s|%s|%s' % (message_id, int(user_id), int(expires))).encode('utf-8')
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:40]


def media_url_builder(key, user_id, now_ts=None):
    """message_id → подписанный адрес ручки вложения (без адреса API: его ставит фронт)."""
    if not key or not user_id:
        return None
    now_ts = int(time.time() if now_ts is None else now_ts)
    expires = (now_ts // 3600 + 1 + MEDIA_TTL_HOURS) * 3600

    def build(message_id):
        if not _ID_RE.match(str(message_id or '')):
            return None
        query = urlencode({'u': int(user_id), 'e': expires,
                           's': media_signature(key, message_id, user_id, expires)})
        return '%s%s?%s' % (MEDIA_PATH, quote(str(message_id), safe=''), query)

    return build


def check_media_link(key, message_id, args, now_ts=None):
    """Подпись и срок ссылки → id человека или None. Сравнение — за постоянное время."""
    if not key or not _ID_RE.match(str(message_id or '')):
        return None
    raw_user, raw_expires = str(args.get('u') or ''), str(args.get('e') or '')
    signature = str(args.get('s') or '')
    if not raw_user.isdigit() or not raw_expires.isdigit() or not signature:
        return None
    user_id, expires = int(raw_user), int(raw_expires)
    now_ts = int(time.time() if now_ts is None else now_ts)
    if expires < now_ts or expires > now_ts + (MEDIA_TTL_HOURS + 2) * 3600:
        return None
    expected = media_signature(key, message_id, user_id, expires)
    if not hmac.compare_digest(expected, signature):
        return None
    return user_id


_MEDIA_SQL = """
    SELECT m.media, m.delete_scope, c.channel_id
      FROM syntony_chat_messages m
      JOIN syntony_chat_conversations c ON c.id = m.conversation_id
     WHERE m.id = %(id)s
"""


def media_allowed(cursor, message_id, channels):
    """Вложение этого сообщения можно отдать: оно на номере СЗоВ, не удалено и вложение есть."""
    cursor.execute(_MEDIA_SQL, {'id': str(message_id)})
    row = cursor.fetchone()
    if not row:
        return False
    media, delete_scope, channel_id = row
    if str(channel_id or '') not in set(channels):
        return False
    if str(delete_scope or '').lower() in ('contact', 'everyone', 'unsent'):
        return False
    return isinstance(media, dict) and bool(media)
