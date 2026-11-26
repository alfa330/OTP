"""Чаты верификаторов ОП по Syntony: «Табло ОП» · «Чат», его выгрузка и отбивка (26.11.2026).

Верификаторы писали клиентам из аккаунта «op» Wazzup; с середины ноября эти номера подключены в
Syntony, и к 26.11.2026 каналы Wazzup почти все заблокированы или без денег — табло, читавшее
Wazzup, видело четыре чата за день. Сверено 26.11.2026 по /v3/channels Wazzup и /channels
Syntony: из 31 номера аккаунта в Syntony 26 (у номера бывает по два канала — прямой и через
Wazzup), оставшиеся пять в Wazzup заблокированы. С номерами СЗоВ (szov.SZOV_CHANNEL_IDS) эти
не пересекаются ни одним.

Решения владельца 26.11.2026:
  * правила табло верификаторов — прежние (op_wallboard/chat.py): диалог — до паузы 6 ч, первый
    ответ и ответ внутри чата с нормами 1 и 4 мин, люди — по своим ответам, ответы сотрудников
    других групп закрывают ожидание, но верификаторам не засчитываются;
  * ответ ИИ закрывает ожидание клиента и никому не засчитывается; ИИ передал чат человеку —
    первый ответ верификатора считается от передачи; диалоги, которые вёл только ИИ, не в счёт;
  * статусы — по ответам: онлайн — верификатор ответил клиенту в Syntony за последние
    15 минут (на любом номере сети), как у СЗоВ до статусов Syntony;
  * только Syntony, Wazzup параллельно не нужен. До дня перехода (SYNTONY_FROM) выгрузка за
    прошлые сутки по-прежнему читает Wazzup — там их переписка тех дней.
Откат на Wazzup — OP_CHAT_SOURCE=wazzup. Без ключа истории и без секрета вебхука Syntony
источник тоже Wazzup: брать переписку было бы неоткуда.

Здесь — только чтение базы и перевод строк Syntony в форму строк Wazzup (`chat.classify`);
переписку в базу приносят вебхук (routes.py) и сторож API истории (sync.py).
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import queries, rules, szov

SOURCE = 'syntony'
SOURCE_LABEL = 'Syntony'
LOCAL_TZ = ZoneInfo('Asia/Almaty')

ENV_SOURCE = 'OP_CHAT_SOURCE'
ENV_CHANNELS = 'OP_CHAT_SYNTONY_CHANNEL_IDS'
ENV_FROM = 'OP_CHAT_SYNTONY_FROM'

# Номера аккаунта «op» Wazzup в Syntony (совпадение по номеру канала, 26.11.2026). У номера часто
# два канала: прямой (WABA или WhatsApp) и прежний «через Wazzup» — второй выключен, но номер тот
# же, и включат его — переписка останется на табло. Полных номеров здесь нет намеренно —
# репозиторий публичный; номер канала виден в Syntony по его id.
VERIFIER_CHANNEL_IDS = frozenset({
    '5f3220d4-797c-49e4-a3dc-7fbe7b501d50',  # Salam Taxi, …1199 (WABA)
    'c51159be-aa62-42d4-9027-3383027720ad',  # Salam Taxi, …1199 (через Wazzup, выключен)
    '9e52be0c-7a76-41c8-98c5-8898796a89c1',  # iTaxi, …0880 (WhatsApp)
    '7925f652-f565-407a-819c-6704f455d6ef',  # iTaxi, …0880 (через Wazzup, выключен)
    'cd824063-38ca-481f-8c54-99b4455d8840',  # iTaxi, …3333 (WABA)
    '56b4c0ad-683f-4759-b114-5a44b20578af',  # iTaxi, …3333 (через Wazzup, выключен)
    'dbb2cddc-9dd7-4df1-9c66-9ebc57291602',  # ДепТакси, …1777 (WABA)
    '7e32c82c-67f7-4e9f-8ead-e8079c8536fd',  # ДепТакси, …1777 (через Wazzup, выключен)
    '3700d5a7-0f6b-453b-919d-577bbd63d46a',  # ДепТакси, …7692 (WhatsApp)
    '778068b6-a2f7-4d12-b1a4-13079066de0a',  # ДепТакси, …7692 (через Wazzup, выключен)
    'd1dad776-55b1-4c74-a917-6c4e5aec90ed',  # Жана такси, …1372 (WhatsApp)
    'f36b2b56-0dac-4204-a00a-55b87fc9ce8b',  # Жана такси, …1372 (через Wazzup, выключен)
    'f0f62a73-5eb3-4203-926c-a046940db968',  # Жана такси, …3322 (WABA)
    '7905d661-3ddd-42e0-a2d5-57d1a280037c',  # Жана такси, …3322 (через Wazzup, выключен)
    '1536ea58-756f-453b-9240-cc5a1e28d1d0',  # Ноль такси, …4242 (WABA)
    '7818c3ef-a639-4052-ad96-2f6a365f0fb4',  # Ноль такси, …4242 (через Wazzup, выключен)
    '0cd01146-0906-4988-8cfb-e9097d9fccd3',  # Ноль такси, …4569 (WhatsApp)
    '64d9cdd6-0fb9-4fc3-acaf-288170b2cddf',  # Ноль такси, …4569 (через Wazzup, выключен)
    'dfb82465-685e-4b25-816c-75af4635ec58',  # Таксопарк 2донгелек, …1500 (WABA)
    'fc83fb99-5ce5-4ed4-92c8-a72f8908440e',  # Таксопарк 2донгелек, …1500 (через Wazzup, выключен)
    '63d53779-8311-4028-a515-bad9eeedde3a',  # Таксопарк Amanat, …2629 (WhatsApp)
    '5c69d1c3-aeea-4118-bf2f-b3e4e5cbbd49',  # Таксопарк Amanat, …2629 (через Wazzup, выключен)
    '4bbf0c38-698b-42d7-835c-10cd8c1f5ee3',  # Таксопарк Amanat, …4507 (через Wazzup, выключен)
    '865cc3b3-b7fb-44b5-b380-7dbf3391beff',  # Таксопарк Global, …1230 (WhatsApp, выключен)
    '865c08b4-e955-42b9-b1e9-5d96ba59ba7d',  # Таксопарк Global, …1230 (через Wazzup, выключен)
    '024be18a-84dd-4491-8b07-05c5221cf8c8',  # Таксопарк Global, …8501 (WABA)
    'd7d1e2d4-d40c-45d6-9c52-ac8e17a0648e',  # Таксопарк Global, …8501 (через Wazzup, выключен)
    '3337d7da-0982-4f51-9e67-f4e7656399a8',  # Таксопарк Qazaq, …4506 (WABA)
    '9fe05b13-33c4-448e-b6c8-834c3006f75f',  # Таксопарк Qazaq, …4506 (через Wazzup, выключен)
    '01ae879c-4a05-44ba-9f56-6ee9ea98ca87',  # Таксопарк Qazaq, …9671 (WhatsApp)
    'da79eb51-67b2-4d2c-b477-e38dfd7274ae',  # Таксопарк Qazaq, …9671 (через Wazzup, выключен)
    'ac0efd50-5dd3-4e55-a07d-a5098f8eebc4',  # Таксопарк Tenge, …5050 (WABA)
    '9bb47586-f5f9-4f52-a126-dadc482a1efe',  # Таксопарк Tenge, …5050 (через Wazzup, выключен)
    '4033e092-42f3-4fe5-913b-b6e3bd9519d7',  # Таксопарк Tenge, …9716 (WhatsApp)
    'a8f54bd9-cb78-45c0-9265-5ed0844a45b8',  # Таксопарк Tenge, …9716 (через Wazzup, выключен)
    '380fc072-9ff3-4e52-b9a3-5c19d9adfdb3',  # Таксопарк Достойный, …6032 (WhatsApp)
    '912558d2-a544-4147-9965-e6de2b396bd3',  # Таксопарк Достойный, …6032 (через Wazzup)
    '98c48dc0-2f2b-4768-a63d-94d6a9c4496c',  # Таксопарк Честный, …0602 (WhatsApp)
    '17308c7d-a435-4283-8ec7-0f55b5093523',  # Таксопарк Честный, …0602 (через Wazzup, выключен)
    '1b867646-1f65-440e-b1ab-87cad1968144',  # Таксопарк Честный, …4503 (WABA)
    'de6ba002-a776-4788-88cb-0a68d31cd29b',  # Таксопарк Честный, …4503 (через Wazzup, выключен)
    '97f02f5d-8d15-4361-9db9-918402328d8a',  # Центр регистрации, …1223 (WhatsApp)
    '93bb29ed-7b9a-4b26-ad4e-d9ac648c6c9a',  # Центр регистрации, …1223 (через Wazzup, выключен)
    '39cf8402-3a07-4763-b4c6-f359cc5c7442',  # Центр регистрации, …1372 (WhatsApp, выключен)
    '66219160-33b6-467f-b9cf-a0916af0c1b3',  # Центр регистрации, …3322 (WABA)
    'e69506da-17be-47f3-bc0f-45de12f6a879',  # Центр регистрации, …7778 (WABA)
    '69d63062-864a-4b60-955f-48d2c0eb8b4f',  # Центр регистрации, …7778 (через Wazzup, выключен)
    '8f47e06d-63ea-47e2-8d74-b4a966302191',  # Центр регистрации, …9674 (WhatsApp, «2ГИС»)
})

# С какого дня табло верификаторов считает по Syntony; раньше — Wazzup (выгрузка за прошлые
# сутки). Замер 26.11.2026 по API Syntony: на этих номерах верификаторы отвечают в полную силу
# уже 15.11 (1342 сообщения шести верификаторов, 17–24.11 — 1750–2170 в сутки), а переписка Syntony
# лежит в нашей базе с 17.11 00:00 (сторож начал с этих суток) — с них и считаем.
SYNTONY_FROM_DEFAULT = date(2026, 11, 17)

# Онлайн — ответ клиенту за последние 15 минут (решение владельца 26.11.2026, как у СЗоВ до
# статусов Syntony). Окно «есть ли ответы вообще» — 16 часов, как у живого статуса «Линии»
# (OPERATOR_LIVE_STATUS_LOOKBACK_HOURS): позже ответов не было — «Нет ответов», и такой человек
# сворачивается в строку «не отвечавших», как «Нет событий» у статусов смены.
PRESENCE_WINDOW_SECONDS = 15 * 60
PRESENCE_LOOKBACK_HOURS = 16
# «Время входа» по ответам — начало смены, а смена кончается паузой в ответах от 6 часов (как пауза
# диалога). У кнопки смены порог 4 часа (snapshot.SESSION_GAP_HOURS), но ответы ночью редки: замер
# 24–26.11.2026 — внутри смены паузы до 3,2 ч, между сменами от 12 ч; в тихую ночь 4 ч перенесли бы
# вход ночника на утро (находка разбора 26.11.2026).
ENTRY_GAP_HOURS = 6

# Статусы по ответам — в каталоге табло (подпись, ключ оформления, вес сортировки): «Онлайн» —
# зелёный, как «Активный» у смены, и наверху списка; «Не в системе» — серый, ниже работающих.
ONLINE_KEY = 'syntony_online'
OFFLINE_KEY = 'syntony_offline'
_STATUS_CATALOG = {
    ONLINE_KEY: ('Онлайн', 'free', 20),
    OFFLINE_KEY: ('Не в системе', 'offline', 70),
}
_STATUS_UNKNOWN = ('Нет ответов', 'unknown', 90)


def status_entry(key):
    """Подпись, ключ оформления и вес — та же форма, что у каталога статусов табло."""
    return _STATUS_CATALOG.get(key, _STATUS_UNKNOWN)


def channel_ids(environ=None):
    """Каналы верификаторов: из OP_CHAT_SYNTONY_CHANNEL_IDS (через запятую или пробел), иначе
    список выше. Окружение — чтобы добавить номер, не дожидаясь выкладки."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_CHANNELS) or '').strip()
    if raw:
        ids = frozenset(part for part in re.split(r'[\s,;]+', raw) if part)
        if ids:
            return ids
    return VERIFIER_CHANNEL_IDS


def first_day(environ=None):
    """С какого дня — Syntony: OP_CHAT_SYNTONY_FROM (ГГГГ-ММ-ДД), иначе SYNTONY_FROM_DEFAULT."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_FROM) or '').strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return SYNTONY_FROM_DEFAULT


def source_enabled(environ=None):
    """Считать ли чаты верификаторов по Syntony. Нет — прежний Wazzup."""
    environ = os.environ if environ is None else environ
    if str(environ.get(ENV_SOURCE) or '').strip().lower() == 'wazzup':
        return False
    return szov.data_enabled(environ)


def covers(day, environ=None):
    """Сутки считаются по Syntony: источник включён и это день перехода или позже."""
    return source_enabled(environ) and day >= first_day(environ)


# ── Чтение ──────────────────────────────────────────────────────────────────────────────────

def _aware(moment):
    """Наивное время Алматы (так считает табло) → с поясом, для TIMESTAMPTZ."""
    return moment.replace(tzinfo=LOCAL_TZ) if moment.tzinfo is None else moment


def _local(moment):
    """TIMESTAMPTZ → наивное время Алматы."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).replace(tzinfo=None)


@dataclass
class Window:
    """Переписка номеров верификаторов за окно — сырьём, до сопоставления авторов.

    messages — (id, канал, ключ чата, направление, отправитель, автор, удалено, момент Алматы, диалог);
    conversations — {id диалога: (канал, ключ чата, передача человеку, закрыт)} — моменты Алматы;
    newest — последнее сообщение любого вида на этих номерах (жива ли переписка)."""
    messages: list = field(default_factory=list)
    conversations: dict = field(default_factory=dict)
    newest: object = None


# Ключ чата — собеседник на номере (как чат Wazzup — номер клиента на канале): у одного
# водителя на номере бывает несколько диалогов Syntony (закрыли — следующее сообщение открывает
# новый), а диалог табло режет паузой. Нет собеседника — диалог сам по себе.
_WINDOW_SQL = """
    SELECT m.id, c.channel_id, COALESCE(NULLIF(c.contact_id, ''), c.id), m.direction,
           m.sender_type, m.author_name, m.delete_scope, m.created_at,
           c.id, c.handoff ->> 'requestedAt', c.closed_at
      FROM syntony_chat_messages m
      JOIN syntony_chat_conversations c ON c.id = m.conversation_id
     WHERE m.created_at >= %(start)s AND m.created_at < %(end)s
       AND c.channel_id = ANY(%(channels)s)
       AND NOT {is_test}
"""


def load_window(cursor, *, since, until, channels, is_test_sql=None):
    """Сообщения номеров верификаторов за [since, until) — наивное время Алматы.

    Тестовые номера («Реестр тестовых номеров») не читаются вовсе — как у Wazzup. Индекс по
    created_at есть (schema.py); за сутки на этих номерах ~7 тыс. строк. Без ORDER BY: порядок
    наводит chat.split_episodes."""
    is_test = is_test_sql('c.contact_phone') if is_test_sql else 'FALSE'
    cursor.execute(_WINDOW_SQL.format(is_test=is_test),
                   {'start': _aware(since), 'end': _aware(until), 'channels': sorted(channels)})
    window = Window()
    for (message_id, channel_id, chat_key, direction, sender_type, author_name, delete_scope,
         created_at, conversation_id, handoff_at, closed_at) in cursor.fetchall():
        at = _local(created_at)
        if at is None:
            continue
        window.messages.append((message_id, channel_id, chat_key, direction, sender_type,
                                author_name, delete_scope, at, conversation_id))
        if window.newest is None or at > window.newest:
            window.newest = at
        if conversation_id not in window.conversations:
            window.conversations[conversation_id] = (
                channel_id, chat_key, _local(rules.parse_time(handoff_at)), _local(closed_at))
    return window


_REPLIES_SQL = """
    SELECT m.author_name, m.created_at
      FROM syntony_chat_messages m
     WHERE m.created_at >= %(start)s AND m.created_at < %(end)s
       AND m.direction = 'outbound' AND m.sender_type = 'human'
       AND m.author_name IS NOT NULL
       AND m.delete_scope IS DISTINCT FROM 'unsent'
"""


def load_replies(cursor, *, since, until):
    """Ответы сотрудников по всей сети за [since, until): [(автор, момент Алматы)] — онлайн
    считается по ответу на ЛЮБОМ номере Syntony, как у СЗоВ."""
    cursor.execute(_REPLIES_SQL, {'start': _aware(since), 'end': _aware(until)})
    return [(author, _local(created_at)) for author, created_at in cursor.fetchall()
            if created_at is not None]


def load_state(cursor):
    """Отметки сторожа и самое раннее сообщение — та же проверка свежести, что у СЗоВ."""
    return queries.load_state(cursor)


def check_fresh(state, newest, now):
    """Падает, если данные Syntony замерли или сутки неполные (тексты — как у табло СЗоВ).

    newest и now — наивное время Алматы; newest — последнее сообщение в окне (любое)."""
    aware_now = _aware(now)
    szov.check_fresh(state, _aware(newest) if newest is not None else None, aware_now)
    day_start = datetime.combine(now.date(), dtime(0, 0), tzinfo=LOCAL_TZ)
    gap = szov.coverage_gap(state, day_start)
    if gap:
        raise RuntimeError('Syntony: сутки неполные — ' + gap)


def coverage_error(state, day):
    """Нет данных за эти сутки (сторож начинал позже, старое почищено) → текст или None."""
    start = datetime.combine(day, dtime(0, 0), tzinfo=LOCAL_TZ)
    gap = szov.coverage_gap(state, start)
    return None if gap is None else 'Нет данных Syntony за %s: %s' % (day.strftime('%d.%m.%Y'), gap)


# ── Строки для op_wallboard/chat.classify ───────────────────────────────────────────────────

def build_rows(window, resolve, *, since=None, until=None):
    """Строки Wazzup-формы {channel_id, chat_id, at, is_echo, user_id, is_bot, author_id, type,
    message_id} + признаки Syntony is_ai / is_handoff.

    resolve(имя автора) → id верификатора или None. Не верификатор (сотрудник другой группы,
    имя, которое не сопоставилось) — автор без привязки: ответ закрывает ожидание, но никому из
    верификаторов не засчитан (правило chat.classify). Служебные строки Syntony (звонки,
    напоминания) и не ушедшие сообщения — не ответ и не начало разговора: их нет вовсе.
    Передача человеку — отдельной строкой в её момент, если он внутри окна [since, until). У каждой
    строки — её диалог Syntony (conversation_id): передача живёт в своём диалоге, а следующий диалог
    того же водителя ИИ ведёт заново (chat._feed_episode)."""
    names = {}

    def who(author):
        if author not in names:
            names[author] = resolve(author) if author else None
        return names[author]

    rows = []
    for (message_id, channel_id, chat_key, direction, sender_type, author_name, delete_scope,
         at, conversation_id) in window.messages:
        base = {'channel_id': channel_id, 'chat_id': chat_key, 'at': at,
                'message_id': str(message_id or ''), 'type': None, 'is_bot': False,
                'conversation_id': conversation_id}
        if direction == 'inbound':
            if sender_type not in (None, '', 'contact'):
                continue
            rows.append(dict(base, is_echo=False, user_id=None, author_id=None))
            continue
        if direction != 'outbound' or delete_scope == 'unsent':
            continue
        if sender_type == 'human':
            # Без имени (отправлено с телефона мимо Syntony) — всё равно человек: ответ есть.
            rows.append(dict(base, is_echo=True, user_id=who(author_name),
                             author_id=author_name or 'syntony'))
        elif sender_type == 'ai':
            rows.append(dict(base, is_echo=True, is_ai=True, user_id=None, author_id=None))
    for conversation_id, (channel_id, chat_key, handoff_at, _closed_at) in window.conversations.items():
        if handoff_at is None:
            continue
        if (since is not None and handoff_at < since) or (until is not None and handoff_at >= until):
            continue
        rows.append({'channel_id': channel_id, 'chat_id': chat_key, 'at': handoff_at,
                     'is_echo': False, 'is_handoff': True, 'is_bot': False, 'user_id': None,
                     'author_id': None, 'type': None, 'conversation_id': conversation_id,
                     'message_id': 'handoff-%s' % conversation_id})
    return rows


def other_authors(window, resolve, day):
    """Кто писал клиентам на этих номерах в эти сутки, но не верификатор (или имя не
    сопоставилось) — диагностика: по списку видно, что имя в Syntony разошлось с карточкой."""
    authors = {author_name for (_id, _channel, _chat, direction, sender_type, author_name, _deleted, at,
                                _conversation) in window.messages
               if direction == 'outbound' and sender_type == 'human' and author_name and at.date() == day}
    return sorted(author for author in authors if resolve(author) is None)


def waiting_since(window, rows, candidates):
    """{(канал, чат): с какого момента ждёт или None} — закрытие диалога в Syntony вместо кнопки
    «Ответ не нужен» «Чатов ОП» (та же форма, что у chat.now_block): верификатор закрыл диалог —
    клиент со стены уходит; написал после закрытия — ждёт с первой такой реплики.

    candidates — чаты, где клиент писал недавно (chat.waiting_candidates)."""
    closed = {}
    for channel_id, chat_key, _handoff_at, closed_at in window.conversations.values():
        key = (channel_id, chat_key)
        if closed_at is not None and key in candidates:
            closed[key] = max(closed_at, closed.get(key) or closed_at)
    if not closed:
        return {}
    after = {}
    for row in rows:
        key = (row['channel_id'], row['chat_id'])
        if key in closed and not row.get('is_echo') and not row.get('is_handoff'):
            if row['at'] > closed[key] and (after.get(key) is None or row['at'] < after[key]):
                after[key] = row['at']
    return {key: after.get(key) for key in closed}


# ── Онлайн по ответам ───────────────────────────────────────────────────────────────────────

def _stretch_start(moments, window_seconds):
    """Начало непрерывного «онлайн», в котором лежит последний ответ (moments по возрастанию)."""
    begin = moments[-1]
    for moment in reversed(moments[:-1]):
        if (begin - moment).total_seconds() > window_seconds:
            break
        begin = moment
    return begin


def _stamp(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S')


def presence(replies, resolve, roster_ids, now, *, window_seconds=PRESENCE_WINDOW_SECONDS,
             lookback_hours=PRESENCE_LOOKBACK_HOURS):
    """Статусы верификаторов по ответам → (живые статусы, события для времени входа).

    Живые — {id: {status_key, event_at, seconds}}, форма get_operator_live_statuses: «Онлайн» —
    последний ответ моложе окна, время — с начала непрерывного онлайна; «Не в системе» — ответ
    был за lookback_hours, время — с ухода (последний ответ плюс окно, как «Не в системе с» у
    СЗоВ); ответов за lookback_hours нет — человека в словаре нет («Нет ответов»).
    События — каждый ответ событием «онлайн»: время входа считается тем же правилом, что у
    смены (snapshot.entry_moment: блоки до паузы в 4 ч, ночная смена — с вечера)."""
    roster = {int(value) for value in roster_ids or ()}
    window = timedelta(seconds=window_seconds)
    floor = now - timedelta(hours=lookback_hours)
    by_person = defaultdict(list)
    cache = {}
    for author, moment in replies or ():
        if moment is None or moment > now:
            continue
        if author not in cache:
            cache[author] = resolve(author) if author else None
        user_id = cache[author]
        if user_id is None or int(user_id) not in roster:
            continue
        by_person[int(user_id)].append(moment)
    live, events = {}, []
    for user_id, moments in by_person.items():
        moments.sort()
        events.extend({'operator_id': user_id, 'event_at': moment, 'status_key': ONLINE_KEY}
                      for moment in moments)
        last = moments[-1]
        if last < floor:
            continue
        if now - last < window:
            begin = _stretch_start(moments, window_seconds)
            live[user_id] = {'status_key': ONLINE_KEY, 'event_at': _stamp(begin),
                             'seconds': int(max(0.0, (now - begin).total_seconds()))}
        else:
            gone = last + window
            live[user_id] = {'status_key': OFFLINE_KEY, 'event_at': _stamp(gone),
                             'seconds': int(max(0.0, (now - gone).total_seconds()))}
    return live, events
