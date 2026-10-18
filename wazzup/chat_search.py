"""Поиск по тексту сообщений «Чатов ОП» (GET /api/wazzup/chat-search).

Просьба владельца 18.10.2026: поиск раздела ищет не только по имени и номеру,
но и по тексту сообщений — «максимально быстро и оптимизированно».

Как в WhatsApp и Telegram, найденное делится на два блока. Чаты по имени и
номеру отдаёт прежний список (wazzup/chat_list.py), здесь — блок «Сообщения»:
каждое найденное сообщение отдельной строкой, свежие сверху. Страницы — по
ключу (dt, message_id) последней строки: ни OFFSET, ни COUNT по всему архиву —
и то и другое заставило бы базу перебрать все совпадения ради каждой страницы.

Скорость. ILIKE '%…%' обычным индексом не ускоряется, нужен триграммный GIN
(pg_trgm в боевой базе стоит, кириллицу и казахские буквы он режет на
триграммы — проверено 18.10.2026). Но и с ним выбор плана выбору базы не
оставлен: частоту подстроки она оценивает грубо, и на «777» (ждала 1305
совпадений, было 156) пошла проходом по свежим сообщениям через весь архив —
в 50 раз дольше индекса. Поэтому два шага с известной ценой:
  1. совпадения среди RECENT_WINDOW последних сообщений — частое слово
     набирает страницу здесь, сколько бы его ни было в архиве;
  2. не набралось — слово редкое, и остальное берётся индексом триграмм
     (проход по времени запрещён), там совпадений по определению немного.
Замер 18.10.2026 на синтетическом архиве того же объёма, что прод (~230 тыс.
сообщений): единицы миллисекунд на страницу. Без индекса тот же поиск на
проде шёл полным просмотром ~1,1 с.

Триграмма — три буквы или цифры подряд. Запрос без такого слова индекс не
сузит, и база прошла бы весь архив, поэтому текст ищется от трёх символов в
слове (searchable_query); имя и номер список ищет с первого символа, как прежде.

Индексы строятся после старта отдельным соединением CONCURRENTLY
(ensure_search_indexes): обычный CREATE INDEX в транзакции схемы держал бы
запись вебхуков Wazzup всё время построения. Пока индекса нет, поиск тот же,
только медленнее.
"""
import logging
import re
import time
from datetime import datetime

from .chat_list import chat_list_item, chat_rows_query, escape_like

PAGE_SIZE = 30
MAX_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 200
# Потолок одного запроса поиска. Обычная страница — миллисекунды; потолок —
# на случай, которого замер не видел, чтобы поиск не держал соединение пула.
STATEMENT_TIMEOUT = '8s'
# Три буквы или цифры подряд — меньше pg_trgm триграммы не даст.
_TRIGRAM_WORD = re.compile(r'[^\W_]{3}')
# Найденное место в длинном сообщении должно попасть в узкую строку списка:
# перед ним — несколько слов, не больше.
SNIPPET_BEFORE = 24
SNIPPET_LENGTH = 240
CURSOR_MAX_LENGTH = 300
# Окно шага 1 — несколько часов переписки (~5 тыс. сообщений в сутки). Его
# цена платится всегда, поэтому оно небольшое: что встречается в нём реже
# трёх раз на сотню сообщений, индекс триграмм найдёт дешевле прохода.
RECENT_WINDOW = 1000

# name, table, definition ({ops} — класс операторов pg_trgm со схемой
# расширения). Один индекс на три столбца чатов: условие поиска списка
# перечисляет их через ИЛИ, и база собирает BitmapOr по одному индексу.
SEARCH_INDEXES = (
    ('idx_wazzup_messages_text_trgm', 'wazzup_messages', 'USING gin (text {ops})'),
    ('idx_wazzup_chats_search_trgm', 'wazzup_chats',
     'USING gin (contact_name {ops}, contact_phone {ops}, chat_id {ops})'),
)
# Строит один процесс; второй (перекрытие при деплое) видит замок и уходит.
INDEX_LOCK_KEY = 0x5A5A_C4A7
INDEX_RETRY_DELAYS = (600, 1800)

log = logging.getLogger(__name__)


def normalize_query(value):
    """Пробелы схлопнуты, длина ограничена."""
    return ' '.join(str(value or '').split())[:MAX_QUERY_LENGTH]


def searchable_query(value):
    """Запрос для поиска по тексту или '' — если в нём нет трёх букв/цифр подряд."""
    query = normalize_query(value)
    return query if _TRIGRAM_WORD.search(query) else ''


def encode_cursor(dt, message_id):
    return f'{dt.isoformat()}|{message_id}'


def decode_cursor(value):
    """(dt, message_id) последней строки прошлой страницы; None — первая страница.
    ValueError — испорченный указатель."""
    if not value:
        return None
    if len(value) > CURSOR_MAX_LENGTH:
        raise ValueError('cursor too long')
    moment, separator, message_id = value.partition('|')
    if not separator or not message_id:
        raise ValueError('bad cursor')
    parsed = datetime.fromisoformat(moment)
    if parsed.tzinfo is None:
        raise ValueError('naive cursor time')
    return parsed, message_id


def _older_than(before):
    """Условие «старше строки before» и его параметры. dt <= — то же условие
    без message_id: его база берёт в индекс по времени."""
    if not before:
        return '', []
    moment, message_id = before
    return ' AND m.dt <= %s AND (m.dt, m.message_id) < (%s, %s)', [moment, moment, message_id]


def recent_hits_query(account, query, limit=PAGE_SIZE, before=None, window=None):
    """Шаг 1: (SQL, параметры) совпадений среди window последних сообщений
    аккаунта (старше before), свежие сверху, не больше limit + 1 — лишняя
    говорит, что есть следующая страница. Последней строкой с флагом edge
    идёт самое старое сообщение окна — если окно заполнено целиком, то есть
    за ним архив продолжается."""
    window = window or RECENT_WINDOW
    older, params = _older_than(before)
    sql = f"""
        WITH recent AS MATERIALIZED (
            SELECT m.channel_id, m.chat_id, m.message_id, m.dt, m.is_echo, m.type, m.status,
                   m.text, m.is_deleted
              FROM wazzup_messages m
             WHERE m.account = %s{older}
             ORDER BY m.dt DESC, m.message_id DESC
             LIMIT %s)
        (SELECT r.channel_id, r.chat_id, r.message_id, r.dt, r.is_echo, r.type, r.status, r.text, FALSE
           FROM recent r
          WHERE NOT r.is_deleted AND r.text ILIKE %s ESCAPE '\\'
          ORDER BY r.dt DESC, r.message_id DESC
          LIMIT %s)
        UNION ALL
        (SELECT NULL, NULL, e.message_id, e.dt, NULL, NULL, NULL, NULL, TRUE
           FROM (SELECT message_id, dt, COUNT(*) OVER () AS scanned FROM recent
                  ORDER BY dt, message_id LIMIT 1) e
          WHERE e.scanned = %s)"""
    return sql, [account, *params, window, f'%{escape_like(query)}%', limit + 1, window]


def message_hits_query(account, query, limit=PAGE_SIZE, before=None):
    """Шаг 2: (SQL, параметры) совпадений старше before по всему архиву,
    свежие сверху, не больше limit + 1."""
    older, params = _older_than(before)
    return ('SELECT m.channel_id, m.chat_id, m.message_id, m.dt, m.is_echo, m.type, m.status, m.text'
            " FROM wazzup_messages m WHERE m.account = %s AND NOT m.is_deleted"
            " AND m.text ILIKE %s ESCAPE '\\'" + older
            + ' ORDER BY m.dt DESC, m.message_id DESC LIMIT %s',
            [account, f'%{escape_like(query)}%', *params, limit + 1])


def snippet(text, query):
    """Кусок текста вокруг первого совпадения, в одну строку."""
    flat = ' '.join(str(text or '').split())
    position = flat.lower().find(query.lower())
    start = 0
    if position > SNIPPET_BEFORE:
        start = position - SNIPPET_BEFORE
        # Не резать слово пополам, если пробел есть до совпадения.
        space = flat.find(' ', start, position)
        if space >= 0:
            start = space + 1
    end = start + SNIPPET_LENGTH
    return ('…' if start else '') + flat[start:end] + ('…' if end < len(flat) else '')


def search_messages(cursor, account, query, limit=PAGE_SIZE, before=None):
    """(найденные сообщения, указатель следующей страницы или None).

    Каждое сообщение несёт строку своего чата (как в списке): из поиска чат
    открывается тем же путём, что и из списка."""
    cursor.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
    cursor.execute(*recent_hits_query(account, query, limit, before))
    rows, edge = [], None
    for row in cursor.fetchall():
        if row[8]:
            edge = (row[3], row[2])
        else:
            rows.append(row[:8])
    if len(rows) <= limit and edge is not None:
        # Свежее окно страницы не набрало — слово редкое: дальше только индекс
        # триграмм. Проход по времени был бы ставкой на то же, чего не нашлось.
        cursor.execute('SET LOCAL enable_indexscan = off')
        cursor.execute(*message_hits_query(account, query, limit - len(rows), edge))
        rows.extend(cursor.fetchall())
        cursor.execute('SET LOCAL enable_indexscan = on')
    more = len(rows) > limit
    rows = rows[:limit]
    chats = {}
    pairs = sorted({(row[0], row[1]) for row in rows})
    if pairs:
        cursor.execute(*chat_rows_query(account, pairs))
        for row in cursor.fetchall():
            item = chat_list_item(row)
            chats[(item['channelId'], item['chatId'])] = item
    items = []
    for channel_id, chat_id, message_id, dt, is_echo, kind, status, text in rows:
        chat = chats.get((channel_id, chat_id))
        # Сводку чата ретеншн удаляет вместе с его сообщениями; строка без чата
        # — гонка с уборкой, открыть её всё равно нечем.
        if chat is None:
            continue
        items.append({'channelId': channel_id, 'chatId': chat_id, 'messageId': message_id,
                      'dt': dt.isoformat(), 'isEcho': bool(is_echo), 'type': kind, 'status': status,
                      'text': snippet(text, query), 'chat': chat})
    last = rows[-1] if rows else None
    return items, (encode_cursor(last[3], last[2]) if more and last else None)


# Найденное сообщение открывается в своей ленте: от него до последнего
# сообщения чата (лента обязана доходить до конца — туда приходят новые и
# там поле ответа) плюс немного ранних для контекста. Потолок — как у сверки
# ленты (/pilot/refresh); самый длинный чат за 45 дней на 18.10.2026 — 641.
FOCUS_CONTEXT = 20
FOCUS_MAX_NEWER = 2000


def focus_window(cursor, message_select, account, channel_id, chat_id, message_id):
    """(строки ленты новыми вперёд, есть ли более ранние) от сообщения message_id
    до конца чата. None — такого сообщения в чате нет или после него больше
    FOCUS_MAX_NEWER: тогда лента открывается обычной последней страницей.

    message_select — SELECT ленты (wazzup.pilot.MESSAGE_SELECT, псевдоним m)."""
    cursor.execute('SELECT dt FROM wazzup_messages WHERE message_id = %s AND account = %s'
                   ' AND channel_id = %s AND chat_id = %s', (message_id, account, channel_id, chat_id))
    row = cursor.fetchone()
    if row is None:
        return None
    moment = row[0]
    chat = ' WHERE m.account = %s AND m.channel_id = %s AND m.chat_id = %s'
    order = ' ORDER BY m.dt DESC, m.message_id DESC LIMIT %s'
    cursor.execute(message_select + chat + ' AND m.dt >= %s' + order,
                   (account, channel_id, chat_id, moment, FOCUS_MAX_NEWER + 1))
    newer = cursor.fetchall()
    if len(newer) > FOCUS_MAX_NEWER:
        return None
    cursor.execute(message_select + chat + ' AND m.dt < %s' + order,
                   (account, channel_id, chat_id, moment, FOCUS_CONTEXT + 1))
    older = cursor.fetchall()
    return newer + older[:FOCUS_CONTEXT], len(older) > FOCUS_CONTEXT


def _index_state(cursor, name):
    """None — индекса нет, True — готов, False — брошен недостроенным."""
    cursor.execute('SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(%s)', (name,))
    row = cursor.fetchone()
    return None if row is None else bool(row[0])


def build_search_indexes(connect):
    """Построить недостающие индексы поиска. True — все на месте.

    CONCURRENTLY не блокирует запись, но идёт только вне транзакции — отсюда
    своё соединение в autocommit. Прерванное построение оставляет индекс
    невалидным (indisvalid = false): такой сносится и строится заново."""
    connection = connect()
    try:
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute('SELECT pg_try_advisory_lock(%s)', (INDEX_LOCK_KEY,))
        if not cursor.fetchone()[0]:
            log.info('Поиск чатов: индексы строит другой процесс')
            return True
        # Класс операторов — со схемой расширения: search_path соединения может
        # её и не содержать.
        cursor.execute("SELECT quote_ident(n.nspname) FROM pg_extension e"
                       " JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'")
        row = cursor.fetchone()
        if row is None:
            log.warning('Поиск чатов: нет расширения pg_trgm, поиск по тексту без индекса')
            return True
        ops = f'{row[0]}.gin_trgm_ops'
        # Построение на большой таблице дольше обычного потолка запроса.
        cursor.execute('SET statement_timeout = 0')
        for name, table, definition in SEARCH_INDEXES:
            state = _index_state(cursor, name)
            if state:
                continue
            if state is False:
                cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {name}')
            started = time.monotonic()
            cursor.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} '
                           + definition.format(ops=ops))
            log.info('Поиск чатов: индекс %s построен за %.1f с', name, time.monotonic() - started)
        return True
    finally:
        connection.close()


def ensure_search_indexes(connect, sleep=time.sleep):
    """build_search_indexes с повторами: сбой (обрыв, взаимная блокировка со
    схемой нового экземпляра при деплое) не должен оставить поиск без индекса
    до следующего перезапуска."""
    for delay in (0,) + INDEX_RETRY_DELAYS:
        if delay:
            sleep(delay)
        try:
            if build_search_indexes(connect):
                return True
        except Exception:
            log.exception('Поиск чатов: индексы не построены, повтор позже')
    return False
