"""Чаты СЗоВ из Syntony для проверки качества (SZOV-9, 26.11.2026).

ЗАЧЕМ. Номера СЗоВ переехали из Chat2Desk в Syntony 16–23.11.2026, а проверка качества —
«Случайный чат» «Журнала оценок» у чат-менеджеров СЗоВ и чаты СЗоВ в «ИИ-оценке» — брала только
Chat2Desk и опустела. Решение владельца 26.11.2026: чаты Syntony оцениваются с 20.11.2026
(SYNTONY_QA_FROM); раньше этого дня — Chat2Desk, как было.

ЧТО ТАКОЕ «ЧАТ». Диалог Syntony сам не закрывается — водитель пишет в него днями, — поэтому
единица проверки — эпизод: сообщения диалога подряд с паузами меньше 6 часов (как у Wazzup и
ChatApp); эпизод длиннее 48 часов или 200 сообщений режется. Берутся только эпизоды, где водитель
писал и ответил сотрудник: эпизоды, которые целиком вёл ИИ-агент, не берутся (п. 2 задачи).
Эпизод принадлежит сотруднику, давшему больше всего ответов, — по тому же справочнику чатников
СЗоВ, что у табло, биллинга и «Учёта часов» (szov.Context). Его доля ответов пишется в снимок:
«ИИ-оценка» берёт эпизод, только если доля не ниже 90 %, как у Wazzup; журнал показывает
любой — проверяющий видит под каждым ответом, кто его написал.

ГДЕ ХРАНИТСЯ. Переписка Syntony живёт в базе 30 дней (sync.KEEP_DAYS), поэтому закрытые эпизоды
раз в час замораживаются снимками в c2d_chat_snapshots (source='syntony') — в ту же таблицу, где
лежат чаты Chat2Desk, Wazzup и ChatApp. Снимок живёт 180 дней, оценённый ИИ — дольше
(cleanup_c2d_eval_data). Дальше всё общее: оценка журнала ссылается на снимок, цитаты
сверяются с ним, «Мои оценки» и карточка «ИИ-оценки» показывают его же.

Эпизод закрыт, когда после последнего сообщения прошло 6 часов (или он обрезан по длине).
Докуда диалог уже разобран — syntony_qa_marks: сообщение, пришедшее задним числом внутрь
разобранного куска, не учитывается (тот же компромисс, что у эпизодов Wazzup).

ВЛОЖЕНИЯ. Ссылка Syntony на файл живёт 5 минут, хранить её нельзя (§3.5). В снимке лежит
метка «syntony-file:<id сообщения>», а браузеру при показе отдаётся подписанный адрес нашей
ручки (как у «Чатов водителей»): она проверяет подпись и берёт у Syntony свежую ссылку.
«ИИ-оценка» скачивает файл по той же метке сама (call_qa.media).

ОТКАТ. SYNTONY_QA_SOURCE=chat2desk — журнал и выборка снова только на Chat2Desk, снимки не
пишутся. Без переписки Syntony в базе (szov.data_enabled) — то же самое.
"""

import hashlib
import hmac
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlencode

from psycopg2.extras import Json, execute_values

from . import billing, client, store, szov, thread

logger = logging.getLogger(__name__)

SOURCE = 'syntony'
SOURCE_LABEL = 'Syntony'
ENV_SOURCE = 'SYNTONY_QA_SOURCE'
ENV_FROM = 'SYNTONY_QA_FROM'
# С какого дня чаты СЗоВ оцениваются по Syntony (решение владельца 26.11.2026: «с 20-го»).
DEFAULT_FROM = date(2026, 11, 20)

# Эпизод: пауза — как у Wazzup и ChatApp; сверхдлинный режется головами.
GAP = timedelta(hours=6)
FORCE_SPAN = timedelta(hours=48)
FORCE_MESSAGES = 200
# Насколько назад от прошлого удачного прогона читать переписку. Не разобранное к прогону —
# только открытый эпизод, а он начался не раньше 48 + 6 часов до прогона; час — запас.
RESCAN = FORCE_SPAN + GAP + timedelta(hours=1)
# Отметку разбора диалога, в который давно не писали, можно забыть: его переписку уже удалила
# чистка (30 дней), и вернуться к ней нечем.
MARKS_KEEP = timedelta(days=40)
MARK_RUN = 'qa_freeze'
_FREEZE_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(hashtext('syntony_qa.freeze'))"

# Вложение в снимке: метка вместо ссылки (ссылка Syntony живёт 5 минут).
MEDIA_SCHEME = 'syntony-file:'
MEDIA_PATH = '/api/syntony_qa/media/'
MEDIA_TTL_HOURS = 2
_MEDIA_KEY_SALT = b'syntony_qa.media.v1'
_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
_MEDIA_FIELDS = ('photo', 'video', 'audio', 'pdf')

KIND_DIALOG = 'dialog'
KIND_AI_ONLY = 'ai_only'
KIND_UNANSWERED = 'unanswered'
KIND_OUTBOUND_ONLY = 'outbound_only'

# Чат, в котором отвечали несколько сотрудников, приписывается тому, у кого не меньше 90 %
# ответов, иначе в проверку не берётся — ни в журнал, ни в «ИИ-оценку» (план SZOV-9, как у
# Wazzup). Порог — тот же, что читает call_qa.config (SYN_MIN_OPERATOR_SHARE). Снимок такого
# чата всё равно пишется: «ИИ-оценка» показывает, сколько чатов отсеяно «вели несколько человек».
ENV_MIN_SHARE = 'SYN_MIN_OPERATOR_SHARE'
DEFAULT_MIN_SHARE = 0.9


# ─────────────────────────────────────────────────────────────────────────────
# Настройки
# ─────────────────────────────────────────────────────────────────────────────

def enabled(environ=None):
    """Проверка качества СЗоВ берёт чаты Syntony: переписка Syntony идёт в базу и откат не
    включён (SYNTONY_QA_SOURCE=chat2desk)."""
    environ = os.environ if environ is None else environ
    if str(environ.get(ENV_SOURCE) or '').strip().lower() == 'chat2desk':
        return False
    return szov.data_enabled(environ)


def start_day(environ=None):
    """С какого дня (Алматы) чаты СЗоВ оцениваются по Syntony: SYNTONY_QA_FROM (ГГГГ-ММ-ДД или
    ДД.ММ.ГГГГ), иначе 20.11.2026. Неразборчивое значение — умолчание, а не ошибка запуска."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_FROM) or '').strip()
    for pattern in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return DEFAULT_FROM


def start_moment(environ=None):
    """Начало дня перехода — полночь Алматы с поясом."""
    return szov.local_day_bounds(start_day(environ))[0]


def min_operator_share(environ=None):
    """Порог доли ответов сотрудника, с которого чат приписывается ему (0…1)."""
    environ = os.environ if environ is None else environ
    try:
        value = float(str(environ.get(ENV_MIN_SHARE) or DEFAULT_MIN_SHARE).strip())
    except ValueError:
        return DEFAULT_MIN_SHARE
    return min(max(value, 0.0), 1.0)


def as_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def split_period(date_from, date_to, first_day):
    """Период «Случайного чата» → (часть Chat2Desk, часть Syntony): (с, по) или None.

    До дня перехода чаты — Chat2Desk, с него — Syntony. Пустая граница — открытый край."""
    lo, hi = as_date(date_from), as_date(date_to)
    c2d = syn = None
    if lo is None or lo < first_day:
        c2d_hi = first_day - timedelta(days=1)
        if hi is not None and hi < c2d_hi:
            c2d_hi = hi
        if lo is None or lo <= c2d_hi:
            c2d = (lo, c2d_hi)
    if hi is None or hi >= first_day:
        syn_lo = first_day if lo is None or lo < first_day else lo
        if hi is None or syn_lo <= hi:
            syn = (syn_lo, hi)
    return c2d, syn


# ─────────────────────────────────────────────────────────────────────────────
# Эпизоды
# ─────────────────────────────────────────────────────────────────────────────

def _row_value(row, key):
    return row.get(key) if isinstance(row, dict) else getattr(row, key)


def is_driver(row):
    return (str(_row_value(row, 'direction') or '').lower() == 'inbound'
            and str(_row_value(row, 'sender_type') or '').lower() in ('', 'contact'))


def is_human_reply(row):
    return (str(_row_value(row, 'direction') or '').lower() == 'outbound'
            and str(_row_value(row, 'sender_type') or '').lower() == 'human'
            and str(_row_value(row, 'delete_scope') or '').lower() != 'unsent')


def is_ai_reply(row):
    return (str(_row_value(row, 'direction') or '').lower() == 'outbound'
            and str(_row_value(row, 'sender_type') or '').lower() == 'ai')


def split_episodes(messages, *, gap=GAP, force_span=FORCE_SPAN, force_messages=FORCE_MESSAGES):
    """Сообщения одного диалога по времени → [(сообщения эпизода, обрезан ли по длине)].

    Эпизод — подряд идущие сообщения с паузами меньше gap. Эпизод длиннее force_span или
    force_messages режется головами; хвост остаётся обычным эпизодом (закроется тишиной)."""
    episodes, current = [], []
    for message in messages:
        if current and message['created_at'] - current[-1]['created_at'] >= gap:
            episodes.append(current)
            current = []
        current.append(message)
    if current:
        episodes.append(current)
    out = []
    for episode in episodes:
        while True:
            head = []
            for message in episode:
                if head and (len(head) >= force_messages
                             or message['created_at'] - head[0]['created_at'] >= force_span):
                    break
                head.append(message)
            if len(head) == len(episode):
                out.append((episode, False))
                break
            out.append((head, True))
            episode = episode[len(head):]
    return out


def closed_episodes(messages, now, *, gap=GAP, force_span=FORCE_SPAN,
                    force_messages=FORCE_MESSAGES):
    """Закрытые эпизоды диалога: [(сообщения, обрезан ли)]. Последний эпизод закрыт, если после
    него прошло gap (тишина) или он — голова обрезанного по длине."""
    episodes = split_episodes(messages, gap=gap, force_span=force_span,
                              force_messages=force_messages)
    closed = []
    for index, (episode, forced) in enumerate(episodes):
        last = index == len(episodes) - 1
        if forced or not last or now - episode[-1]['created_at'] >= gap:
            closed.append((episode, forced))
    return closed


def summarize(messages, resolve, display_name=None):
    """Что за эпизод и чей он.

    → {'kind', 'inbound_count', 'human_outbound_count', 'ai_messages', 'operator_id',
    'operator_name', 'operator_messages', 'operator_share', 'authors'}.
    Сотрудник эпизода — давший больше всего ответов из сопоставленных со справочником чатников;
    ответ без автора (с телефона, waba_api) и незнакомого автора — в знаменателе доли, но эпизод
    им не приписывается (так же, как у Wazzup)."""
    inbound = sum(1 for m in messages if is_driver(m))
    human = [m for m in messages if is_human_reply(m)]
    ai = sum(1 for m in messages if is_ai_reply(m))
    per_user, names, authors = {}, {}, {}
    for message in human:
        author = _row_value(message, 'author_name') or None
        operator = resolve(author) if author else None
        operator_id = int(operator['id']) if operator and operator.get('id') is not None else None
        if operator_id is not None:
            per_user[operator_id] = per_user.get(operator_id, 0) + 1
            names[operator_id] = operator.get('name') or author
        label = (display_name(author) if display_name and author else author) or None
        key = (operator_id, label)
        entry = authors.setdefault(key, {'name': label, 'user_id': operator_id, 'messages': 0})
        entry['messages'] += 1
    operator_id = operator_share = None
    if per_user:
        operator_id = max(per_user, key=lambda user: (per_user[user], -user))
        operator_share = round(per_user[operator_id] / len(human), 3)
    if inbound and human:
        kind = KIND_DIALOG
    elif inbound and ai:
        kind = KIND_AI_ONLY
    elif inbound:
        kind = KIND_UNANSWERED
    else:
        kind = KIND_OUTBOUND_ONLY
    return {
        'kind': kind,
        'inbound_count': inbound,
        'human_outbound_count': len(human),
        'ai_messages': ai,
        'operator_id': operator_id,
        'operator_name': names.get(operator_id) if operator_id is not None else None,
        'operator_messages': per_user.get(operator_id, 0) if operator_id is not None else 0,
        'operator_share': operator_share,
        'authors': sorted(authors.values(), key=lambda a: (-a['messages'], a['name'] or '')),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Сообщения снимка
# ─────────────────────────────────────────────────────────────────────────────

def media_ref(message_id, file_name=None):
    """Метка вложения в снимке. Имя файла — ради расширения: «ИИ-оценка» читает PDF и
    картинки, присланные документом, по расширению (call_qa.media._kind_of)."""
    ref = MEDIA_SCHEME + str(message_id)
    if file_name:
        ref += '?filename=' + quote(str(file_name), safe='')
    return ref


def parse_media_ref(value):
    """Метка вложения → id сообщения или None (не метка)."""
    text = str(value or '')
    if not text.startswith(MEDIA_SCHEME):
        return None
    message_id = text[len(MEDIA_SCHEME):].split('?', 1)[0]
    return message_id if _ID_RE.match(message_id) else None


def _file_name(media):
    """Имя файла вложения для метки: своё или по типу (PDF без имени — «document.pdf»)."""
    if not isinstance(media, dict):
        return None
    name = str(media.get('fileName') or '').strip()
    if name:
        return name
    mime = str(media.get('mimeType') or '').lower()
    return 'document.pdf' if mime == 'application/pdf' else None


def snapshot_message(row, display_name=None):
    """Строка сообщения → сообщение снимка (форма ленты Chat2Desk) или None (не ушедшее).

    В отличие от «Чатов водителей»: автор ответа — ФИО сотрудника iCORE (по нему «ИИ-оценка»
    отличает оцениваемого от «другого сотрудника»), вместо ссылки на файл — метка, а служебные
    поля ленты раздела (статус доставки, диалог, канал — они у снимка свои) не хранятся."""
    media = _row_value(row, 'media')
    names = {_row_value(row, 'id'): _file_name(media)}
    item = thread.message_from_row(
        (_row_value(row, 'id'), _row_value(row, 'conversation_id'), _row_value(row, 'direction'),
         _row_value(row, 'sender_type'), _row_value(row, 'author_name'),
         _row_value(row, 'content_type'), _row_value(row, 'text'), media,
         _row_value(row, 'created_at'), _row_value(row, 'delete_scope'), None, None),
        media_url=lambda message_id: media_ref(message_id, names.get(message_id)))
    if item is None:
        return None
    author = item.get('author')
    if author and display_name:
        item['author'] = display_name(author)
    compact = {key: item[key] for key in ('id', 'type', 'text', 'created')}
    for key in _MEDIA_FIELDS + ('author', 'label'):
        if item.get(key):
            compact[key] = item[key]
    if item.get('attachments'):
        compact['attachments'] = item['attachments']
    return compact


# ─────────────────────────────────────────────────────────────────────────────
# Заморозка закрытых эпизодов
# ─────────────────────────────────────────────────────────────────────────────

_LOAD_SQL = """
    SELECT m.id, m.conversation_id, m.direction, m.sender_type, m.author_name,
           m.content_type, m.text, m.media, m.created_at, m.delete_scope, c.channel_id
      FROM syntony_chat_messages m
      JOIN syntony_chat_conversations c ON c.id = m.conversation_id
      LEFT JOIN syntony_qa_marks k ON k.conversation_id = m.conversation_id
     WHERE m.created_at >= %(floor)s
       AND c.channel_id = ANY(%(channels)s)
       AND (k.closed_until IS NULL OR m.created_at > k.closed_until)
       AND COALESCE(m.delete_scope, '') <> 'unsent'
       AND NOT {is_test}
     ORDER BY m.conversation_id, m.created_at, m.id
"""

_ROW_KEYS = ('id', 'conversation_id', 'direction', 'sender_type', 'author_name', 'content_type',
             'text', 'media', 'created_at', 'delete_scope', 'channel_id')

_SNAPSHOT_COLUMNS = ('source', 'wz_channel_id', 'wz_chat_id', 'episode_start', 'episode_end',
                     'day', 'operator_id', 'c2d_operator_name', 'channel_name', 'transport',
                     'client_name', 'client_phone', 'messages', 'messages_count',
                     'request_payload')


def _local_day(moment):
    return moment.astimezone(szov.LOCAL_TZ).date()


def episode_snapshot(episode, forced, summary, detail, display_name=None):
    """Закрытый эпизод → строка c2d_chat_snapshots (словарь по _SNAPSHOT_COLUMNS)."""
    messages = [item for item in (snapshot_message(row, display_name) for row in episode) if item]
    first, last = episode[0], episode[-1]
    channel_id = first.get('channel_id') or detail.get('channel_id')
    park = billing.park_of(channel_id, detail.get('park_name'))
    payload = {
        'conversation_id': first['conversation_id'],
        'channel_id': channel_id,
        'park': park,
        'kind': summary['kind'],
        'inbound_count': summary['inbound_count'],
        'human_outbound_count': summary['human_outbound_count'],
        'operator_messages': summary['operator_messages'],
        'operator_share': summary['operator_share'],
        'ai_messages': summary['ai_messages'],
        'authors': summary['authors'],
        'force_closed': bool(forced),
        'started_at': first['created_at'].isoformat(),
        'ended_at': last['created_at'].isoformat(),
    }
    return {
        'source': SOURCE,
        'wz_channel_id': str(channel_id or ''),
        'wz_chat_id': str(first['conversation_id']),
        'episode_start': first['created_at'],
        'episode_end': last['created_at'],
        'day': _local_day(first['created_at']),
        'operator_id': summary['operator_id'],
        'c2d_operator_name': summary['operator_name'],
        'channel_name': park or detail.get('channel_name'),
        'transport': billing.transport_of(detail.get('channel_type'), detail.get('channel_provider')),
        'client_name': detail.get('contact_name'),
        'client_phone': detail.get('contact_phone'),
        'messages': messages,
        'messages_count': len(messages),
        'request_payload': payload,
    }


def plan_conversation(rows, now, first_moment, resolve, display_name=None):
    """Разбор одного диалога: (снимки к записи, докуда разобран, счётчики) — без базы.

    Эпизоды до дня перехода разбираются (чтобы граница следующего встала правильно), но не
    пишутся; пишутся только диалоги, приписанные сотруднику."""
    stats = {'dialogs': 0, 'ai_only': 0, 'unanswered': 0, 'outbound_only': 0,
             'unattributed': 0, 'before_start': 0}
    snapshots, closed_until = [], None
    for episode, forced in closed_episodes(rows, now):
        closed_until = episode[-1]['created_at']
        if episode[0]['created_at'] < first_moment:
            stats['before_start'] += 1
            continue
        summary = summarize(episode, resolve, display_name)
        if summary['kind'] != KIND_DIALOG:
            stats[summary['kind']] += 1
            continue
        if summary['operator_id'] is None:
            stats['unattributed'] += 1
            continue
        stats['dialogs'] += 1
        snapshots.append((episode, forced, summary))
    return snapshots, closed_until, stats


def freeze(db, ctx, *, now=None, environ=None):
    """Заморозить закрытые эпизоды СЗоВ с дня перехода → счётчики прогона.

    Читает переписку от прошлого удачного прогона минус RESCAN (первый прогон — с дня перехода
    минус 48 часов: эпизод, начатый до полуночи 20.11, должен получить свою, а не обрезанную
    границу), только по номерам СЗоВ и без тестовых номеров. Два прогона сразу (выкладка, два
    процесса) не идут: второй видит лок и выходит."""
    now = now or datetime.now(timezone.utc)
    first_moment = start_moment(environ)
    totals = {'conversations': 0, 'written': 0, 'dialogs': 0, 'ai_only': 0, 'unanswered': 0,
              'outbound_only': 0, 'unattributed': 0, 'before_start': 0, 'locked': False}
    is_test = ctx.is_test_sql('c.contact_phone') if ctx.is_test_sql else 'FALSE'
    with db._get_cursor() as cursor:
        cursor.execute(_FREEZE_LOCK_SQL)
        if not cursor.fetchone()[0]:
            totals['locked'] = True
            return totals
        last_run = store.get_marks(cursor).get(MARK_RUN)
        floor = first_moment - FORCE_SPAN
        if last_run is not None and last_run - RESCAN > floor:
            floor = last_run - RESCAN
        cursor.execute(_LOAD_SQL.format(is_test=is_test),
                       {'floor': floor, 'channels': sorted(ctx.channels)})
        by_conversation = {}
        for row in cursor.fetchall():
            item = dict(zip(_ROW_KEYS, row))
            by_conversation.setdefault(item['conversation_id'], []).append(item)
        details = billing._details(cursor, by_conversation.keys())
        rows, marks = [], []
        for conversation_id, messages in by_conversation.items():
            planned, closed_until, stats = plan_conversation(
                messages, now, first_moment, ctx.resolve, ctx.display_name)
            for key, value in stats.items():
                totals[key] += value
            detail = details.get(conversation_id) or {}
            for episode, forced, summary in planned:
                rows.append(episode_snapshot(episode, forced, summary, detail, ctx.display_name))
            if closed_until is not None:
                marks.append((conversation_id, closed_until))
        totals['conversations'] = len(by_conversation)
        if rows:
            # RETURNING через все страницы (fetch=True): rowcount знает только последнюю.
            inserted = execute_values(cursor, """
                INSERT INTO c2d_chat_snapshots ({columns}) VALUES %s
                ON CONFLICT (wz_channel_id, wz_chat_id, episode_start) WHERE source = 'syntony'
                DO NOTHING
                RETURNING id
            """.format(columns=', '.join(_SNAPSHOT_COLUMNS)),
                [tuple(Json(row[name]) if name in ('messages', 'request_payload') else row[name]
                       for name in _SNAPSHOT_COLUMNS) for row in rows], page_size=200, fetch=True)
            totals['written'] = len(inserted or [])
        if marks:
            execute_values(cursor, """
                INSERT INTO syntony_qa_marks (conversation_id, closed_until) VALUES %s
                ON CONFLICT (conversation_id) DO UPDATE
                   SET closed_until = GREATEST(syntony_qa_marks.closed_until, EXCLUDED.closed_until),
                       updated_at = now()
            """, marks, page_size=500)
        cursor.execute('DELETE FROM syntony_qa_marks WHERE closed_until < %s', (now - MARKS_KEEP,))
        store.set_mark(cursor, MARK_RUN, now)
    return totals


# ─────────────────────────────────────────────────────────────────────────────
# «Случайный чат» журнала
# ─────────────────────────────────────────────────────────────────────────────

def pick(cursor, *, date_from=None, date_to=None, operator_id=None, min_messages=None,
         max_messages=None, department_id=None, exclude='any', evaluator_id=None,
         exclude_ids=None, is_test_sql=None, min_share=None):
    """Случайный снимок чата Syntony по фильтрам → (id снимка | None, сколько подходит).

    Только чаты, приписанные сотруднику честно: его доля ответов не ниже порога
    (min_operator_share). exclude — как у Chat2Desk: 'any' — без чатов, уже оценённых кем-либо
    в журнале, 'mine' — оценённых этим проверяющим, 'none' — без исключений."""
    share = min_operator_share() if min_share is None else float(min_share)
    where = ["s.source = %s",
             "COALESCE((s.request_payload->>'operator_share')::float, 0) >= %s"]
    params = [SOURCE, share - 1e-9]
    if date_from:
        where.append("s.day >= %s")
        params.append(date_from)
    if date_to:
        where.append("s.day <= %s")
        params.append(date_to)
    if operator_id is not None:
        where.append("s.operator_id = %s")
        params.append(int(operator_id))
    if department_id is not None:
        where.append("u.department_id = %s")
        params.append(int(department_id))
    if min_messages is not None:
        where.append("s.messages_count >= %s")
        params.append(int(min_messages))
    if max_messages is not None:
        where.append("s.messages_count <= %s")
        params.append(int(max_messages))
    if exclude_ids:
        where.append("s.id <> ALL(%s)")
        params.append([int(value) for value in exclude_ids])
    if is_test_sql:
        where.append("NOT " + is_test_sql('s.client_phone'))
    exclude = str(exclude or 'none').strip().lower()
    evaluated = "NOT EXISTS (SELECT 1 FROM calls c WHERE c.c2d_snapshot_id = s.id{extra})"
    if exclude == 'mine' and evaluator_id is not None:
        where.append(evaluated.format(extra=" AND c.evaluator_id = %s"))
        params.append(int(evaluator_id))
    elif exclude in ('any', 'mine'):
        where.append(evaluated.format(extra=""))
    cursor.execute("""
        SELECT s.id, COUNT(*) OVER ()
          FROM c2d_chat_snapshots s
          JOIN users u ON u.id = s.operator_id
         WHERE {where}
         ORDER BY random()
         LIMIT 1
    """.format(where=' AND '.join(where)), params)
    row = cursor.fetchone()
    if not row:
        return None, 0
    return int(row[0]), int(row[1] or 0)


def request_row(snapshot):
    """Шапка окна оценки и «дата обращения» журнала — в форме заявки Chat2Desk."""
    payload = snapshot.get('request') or {}

    def local(value):
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return thread.local_iso(moment)

    return {
        'episode_id': snapshot.get('id'),
        'request_start': local(snapshot.get('episode_start') or payload.get('started_at')),
        'request_end': local(snapshot.get('episode_end') or payload.get('ended_at')),
        'day': snapshot.get('day'),
        'operator_id': snapshot.get('operator_id'),
        'operator_name': snapshot.get('operator_name'),
        'channel_name': snapshot.get('channel_name'),
        'transport': snapshot.get('transport'),
        'messages_count': snapshot.get('messages_count'),
        'inbound_count': payload.get('inbound_count'),
        'operator_share': payload.get('operator_share'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ссылки на вложения
# ─────────────────────────────────────────────────────────────────────────────

def media_key(secret=None):
    """Ключ подписи ссылок — свой, производный от общего секрета сервера (JWT_SECRET)."""
    secret = os.environ.get('JWT_SECRET') if secret is None else secret
    secret = str(secret or '').strip()
    if not secret:
        return None
    return hmac.new(secret.encode('utf-8'), _MEDIA_KEY_SALT, hashlib.sha256).digest()


def _signature(key, snapshot_id, message_id, user_id, expires):
    payload = ('%s|%s|%s|%s' % (int(snapshot_id), message_id, int(user_id), int(expires)))
    return hmac.new(key, payload.encode('utf-8'), hashlib.sha256).hexdigest()[:40]


def media_link_builder(key, snapshot_id, user_id, now_ts=None):
    """id сообщения → подписанный адрес ручки вложения (без адреса API — его ставит фронт).

    Подпись привязана к снимку, сообщению и человеку и живёт до конца часа плюс два: ссылки
    одного часа одинаковые, и браузер берёт открытую картинку из кеша."""
    if not key or not user_id or snapshot_id is None:
        return None
    now_ts = int(time.time() if now_ts is None else now_ts)
    expires = (now_ts // 3600 + 1 + MEDIA_TTL_HOURS) * 3600

    def build(message_id):
        if not _ID_RE.match(str(message_id or '')):
            return None
        query = urlencode({'u': int(user_id), 'e': expires,
                           's': _signature(key, snapshot_id, message_id, user_id, expires)})
        return '%s%d/%s?%s' % (MEDIA_PATH, int(snapshot_id), quote(str(message_id), safe=''),
                               query)

    return build


def check_media_link(key, snapshot_id, message_id, args, now_ts=None):
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
    expected = _signature(key, snapshot_id, message_id, user_id, expires)
    if not hmac.compare_digest(expected, signature):
        return None
    return user_id


def _signed(value, build):
    message_id = parse_media_ref(value)
    if message_id is None:
        return value
    return build(message_id) if build else None


def sign_snapshot(snapshot, key, user_id, now_ts=None):
    """Снимок для показа человеку: метки вложений → подписанные адреса (копия, снимок не
    меняется). Чужие источники возвращаются как есть."""
    if not snapshot or snapshot.get('source') != SOURCE:
        return snapshot
    build = media_link_builder(key, snapshot.get('id'), user_id, now_ts)
    messages = []
    for message in snapshot.get('messages') or []:
        item = dict(message)
        for field in _MEDIA_FIELDS:
            if item.get(field):
                item[field] = _signed(item[field], build)
        if item.get('attachments'):
            item['attachments'] = [dict(att, link=_signed(att.get('link'), build))
                                   if isinstance(att, dict) else att
                                   for att in item['attachments']]
        messages.append(item)
    return dict(snapshot, messages=messages)


def sign_lines(lines, snapshot_id, key, user_id, now_ts=None):
    """Строки карточки «ИИ-оценки» (media.url — метка) → копия с подписанными адресами."""
    build = media_link_builder(key, snapshot_id, user_id, now_ts)
    out = []
    for line in lines or []:
        media = line.get('media') if isinstance(line, dict) else None
        if isinstance(media, dict) and parse_media_ref(media.get('url')):
            line = dict(line, media=dict(media, url=_signed(media.get('url'), build)))
        out.append(line)
    return out


def snapshot_has_media(snapshot, message_id):
    """В этом снимке есть сообщение с вложением-меткой этого id."""
    if not snapshot or snapshot.get('source') != SOURCE:
        return False
    for message in snapshot.get('messages') or []:
        if str(message.get('id')) != str(message_id):
            continue
        refs = [message.get(field) for field in _MEDIA_FIELDS]
        refs += [att.get('link') for att in (message.get('attachments') or [])
                 if isinstance(att, dict)]
        return any(parse_media_ref(ref) == str(message_id) for ref in refs)
    return False


def file_url(message_id, config=None):
    """Свежая ссылка Syntony на вложение (живёт 5 минут) — для скачивания сервером."""
    config = config or client.load_config()
    if config is None:
        raise client.SyntonyHistoryError('not_configured', 503)
    return client.file_link(config, unquote(str(message_id)))['url']
