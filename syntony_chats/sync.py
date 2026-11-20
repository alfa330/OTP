"""Забор чатов Syntony через API истории чатов (ключ sck_) — рекомендованный режим Syntony
(§6) рядом с вебхуками. Вебхук приносит событие за 10–20 секунд, а этот сторож закрывает
всё, чего вебхук не принёс: пока секрета подписи нет, пока вебхук выключен, пока сервер
перезапускался, — и «прочитано», которое вебхук не шлёт позже 3 суток.

Сторож — свой поток (не общий пул бота), раз в 3 минуты:
1. Лента /messages?since= — момент последнего полученного сообщения минус 5 минут (§6.2):
   новые сообщения всех диалогов сети. Лимит ленты — 10 запросов в минуту на ключ:
   страницы идут с паузой, за прогон не больше FEED_PAGES_PER_RUN, остаток — следующим.
2. Снимки диалогов /conversations?updatedSince= — в ленте сообщений их нет.
3. Ночью, раз в сутки — перечитать целиком диалоги, обновлённые за прошедшие сутки (§6.4):
   свежие статусы и сообщения, записанные задним числом (шаблон рассылки, импорт истории).
4. Ночью, раз в сутки — убрать переписку старше KEEP_DAYS (30 дней, SYNTONY_CHATS_KEEP_DAYS):
   Syntony хранит историю сама, у нас рабочая копия для отчётов, а диск боевой базы тесный.

Первый прогон берёт сообщения с начала текущих суток по Алматы — не всю историю сети.
Повтор того же сообщения ничего не портит (store.py). Транзакция — на страницу, а запрос
к Syntony идёт при закрытом соединении: ожидание чужого сервера не держит пул.
Выключатель — SYNTONY_CHATS_SYNC_ENABLED=false; без ключа сторож не запускается.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import client, rules, store

logger = logging.getLogger(__name__)

ENV_ENABLED = 'SYNTONY_CHATS_SYNC_ENABLED'
ENV_KEEP_DAYS = 'SYNTONY_CHATS_KEEP_DAYS'

ALMATY = ZoneInfo('Asia/Almaty')
INTERVAL_SECONDS = 180
FIRST_RUN_DELAY_SECONDS = 90
OVERLAP = timedelta(minutes=5)              # §6.2: «минус пять минут»
FEED_MAX_AGE = timedelta(days=30)           # since старше 31 дня API не принимает
FEED_PAGE = 1000
FEED_PAGES_PER_RUN = 8
FEED_PAUSE_SECONDS = 6.5                    # /messages — 10 запросов в минуту на ключ
CONVERSATIONS_PAGE = 500
CONVERSATIONS_PAUSE_SECONDS = 1.1           # /conversations — 60 в минуту
THREAD_PAUSE_SECONDS = 0.25                 # /conversations/{id}/messages — 300 в минуту
REREAD_WINDOW = timedelta(hours=26)
NIGHT_HOURS = range(3, 6)                   # 03:00–05:59 Алматы: перечитка и чистка
# Месяц для отчётов. Замер 20.11.2026 на живой ленте: 766 Б на сообщение с ключом, ~16 тыс.
# сообщений в сутки → ~12 МБ/сутки, 30 дней ≈ 370 МБ; диск боевой базы занят на 84 %.
KEEP_DAYS = 30
MIN_KEEP_DAYS = 7
DELETE_BATCH = 5000
RATE_LIMIT_WAIT_CAP_SECONDS = 120
SUMMARY_EVERY_SECONDS = 3600

MARK_FEED = 'feed_since'
MARK_CONVERSATIONS = 'conversations_since'
MARK_REREAD = 'reread_at'
MARK_CLEANUP = 'cleanup_at'


def is_enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_ENABLED, 'true')).strip().lower() not in ('0', 'false', 'off', 'no')


def keep_days(environ=None):
    environ = os.environ if environ is None else environ
    try:
        days = int(str(environ.get(ENV_KEEP_DAYS) or KEEP_DAYS).strip())
    except ValueError:
        return KEEP_DAYS
    return max(MIN_KEEP_DAYS, days)


def _utcnow():
    return datetime.now(timezone.utc)


def _day_start(now):
    return now.astimezone(ALMATY).replace(hour=0, minute=0, second=0, microsecond=0)


class Stop(Exception):
    """Отказ подключения целиком (ключ, лимит, сервис чатов, сеть): прогон дальше не идёт."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _call(fetch, sleep):
    """Один вызов API; после 429 — одно ожидание Retry-After (не дольше 2 минут) и повтор."""
    try:
        return fetch()
    except client.SyntonyHistoryError as exc:
        if exc.status != 429:
            raise Stop(exc.code) from None
        sleep(min(exc.retry_after or 60, RATE_LIMIT_WAIT_CAP_SECONDS))
    try:
        return fetch()
    except client.SyntonyHistoryError as exc:
        raise Stop(exc.code) from None


def _items(page):
    data = page.get('data') if isinstance(page, dict) else None
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def write_messages(db, items, fetched_at):
    """Сообщения из API → база одной транзакцией → (записано, самое позднее createdAt)."""
    written, latest = 0, None
    with db._get_cursor() as cursor:
        for item in items:
            row = rules.message_row(item, fetched_at, rules.VIA_API)
            if row is None:
                continue
            if store.upsert_message(cursor, row) != 'unchanged':
                written += 1
            if row['created_at'] is not None and (latest is None or row['created_at'] > latest):
                latest = row['created_at']
    return written, latest


def write_conversations(db, items, fetched_at):
    written = 0
    with db._get_cursor() as cursor:
        for item in items:
            row = rules.conversation_row(item, fetched_at)
            if row is not None and store.upsert_conversation(cursor, row):
                written += 1
    return written


def pull_feed(db, config, *, now, marks, sleep, session=None):
    """Шаг 1: лента новых сообщений → (записано, страниц)."""
    since = max(marks.get(MARK_FEED) or _day_start(now), now - FEED_MAX_AGE)
    token, written, pages, latest = None, 0, 0, None
    while pages < FEED_PAGES_PER_RUN:
        if pages:
            sleep(FEED_PAUSE_SECONDS)
        fetched_at = _utcnow()
        page = _call(lambda: client.messages_page(config, since, token, FEED_PAGE, session=session),
                     sleep)
        pages += 1
        count, page_latest = write_messages(db, _items(page), fetched_at)
        written += count
        if page_latest is not None and (latest is None or page_latest > latest):
            latest = page_latest
        token = page.get('nextCursor')
        if not token:
            break
    # Лента кончилась — следующий прогон с последнего полученного минус 5 минут (§6.2);
    # не кончилась за прогон — тоже с последнего полученного: дочитает остаток. Назад
    # отметка не идёт.
    anchor = latest if latest is not None else (now if not token else since)
    with db._get_cursor() as cursor:
        store.set_mark(cursor, MARK_FEED, max(anchor - OVERLAP, since))
    return written, pages


def pull_conversations(db, config, *, now, marks, sleep, session=None):
    """Шаг 2: снимки диалогов, обновлённых с прошлого прогона → записано."""
    since = marks.get(MARK_CONVERSATIONS) or _day_start(now)
    offset, written = 0, 0
    while True:
        fetched_at = _utcnow()
        page = _call(lambda: client.conversations_page(config, since, offset, CONVERSATIONS_PAGE,
                                                       session=session), sleep)
        items = _items(page)
        written += write_conversations(db, items, fetched_at)
        offset = page.get('nextOffset')
        if offset is None or not items:
            break
        sleep(CONVERSATIONS_PAUSE_SECONDS)
    with db._get_cursor() as cursor:
        store.set_mark(cursor, MARK_CONVERSATIONS, now - OVERLAP)
    return written


def reread_day(db, config, *, now, sleep, session=None):
    """Шаг 3: диалоги, обновлённые за сутки, — снимок и все сообщения → (диалогов, сообщений)."""
    since, offset, conversations, messages = now - REREAD_WINDOW, 0, [], 0
    while True:
        page = _call(lambda: client.conversations_page(config, since, offset, CONVERSATIONS_PAGE,
                                                       session=session), sleep)
        items = _items(page)
        conversations.extend(item for item in items if item.get('id'))
        offset = page.get('nextOffset')
        if offset is None or not items:
            break
        sleep(CONVERSATIONS_PAUSE_SECONDS)
    for conversation in conversations:
        token, fetched_at = None, _utcnow()
        write_conversations(db, [conversation], fetched_at)
        while True:
            sleep(THREAD_PAUSE_SECONDS)
            page = _call(lambda: client.conversation_messages_page(
                config, conversation['id'], token, FEED_PAGE, session=session), sleep)
            messages += write_messages(db, _items(page), fetched_at)[0]
            token = page.get('nextCursor')
            if not token:
                break
    with db._get_cursor() as cursor:
        store.set_mark(cursor, MARK_REREAD, now)
    return len(conversations), messages


def cleanup(db, *, now, days):
    """Шаг 4: переписка старше days — пачками, транзакция на пачку → (сообщений, диалогов)."""
    cutoff = now - timedelta(days=days)
    removed = []
    for delete in (store.delete_old_messages, store.delete_old_conversations):
        total = 0
        while True:
            with db._get_cursor() as cursor:
                count = delete(cursor, cutoff, DELETE_BATCH)
            total += count
            if count < DELETE_BATCH:
                break
        removed.append(total)
    with db._get_cursor() as cursor:
        store.set_mark(cursor, MARK_CLEANUP, now)
    return tuple(removed)


def night_due(mark, now):
    """Ночной шаг: сейчас 03:00–05:59 Алматы и сегодня его ещё не было."""
    local = now.astimezone(ALMATY)
    if local.hour not in NIGHT_HOURS:
        return False
    return mark is None or mark.astimezone(ALMATY).date() < local.date()


def run_once(db, *, environ=None, now=None, sleep=time.sleep, session=None):
    """Один прогон сторожа → сводка или {'skipped': 'not_configured'}."""
    config = client.load_config(environ)
    if config is None:
        return {'skipped': 'not_configured'}
    now = now or _utcnow()
    with db._get_cursor() as cursor:
        marks = store.get_marks(cursor)
    summary = {}
    try:
        summary['feed'] = pull_feed(db, config, now=now, marks=marks, sleep=sleep, session=session)
        summary['conversations'] = pull_conversations(db, config, now=now, marks=marks,
                                                      sleep=sleep, session=session)
        if night_due(marks.get(MARK_REREAD), now):
            summary['reread'] = reread_day(db, config, now=now, sleep=sleep, session=session)
    except Stop as stop:
        summary['stopped'] = stop.code
    if night_due(marks.get(MARK_CLEANUP), now):
        summary['cleanup'] = cleanup(db, now=now, days=keep_days(environ))
    return summary


class _Journal:
    """Сводка в журнал раз в час (прогон раз в 3 минуты — 480 строк в сутки были бы шумом);
    отказ, ночная перечитка и чистка — сразу."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._since = clock()
        self._totals = {'runs': 0, 'messages': 0, 'pages': 0, 'conversations': 0}
        self._last_stop = None

    def note(self, summary):
        if summary.get('skipped'):
            return
        totals = self._totals
        totals['runs'] += 1
        written, pages = summary.get('feed', (0, 0))
        totals['messages'] += written
        totals['pages'] += pages
        totals['conversations'] += summary.get('conversations', 0)
        stop = summary.get('stopped')
        if stop and stop != self._last_stop:
            logger.warning('Чаты Syntony, забор из API: прогон остановлен — %s', stop)
        self._last_stop = stop
        if 'reread' in summary:
            logger.info('Чаты Syntony, забор из API: ночная перечитка — диалогов %s, '
                        'сообщений записано %s', *summary['reread'])
        if 'cleanup' in summary:
            logger.info('Чаты Syntony, забор из API: убрано старше срока — сообщений %s, '
                        'диалогов %s', *summary['cleanup'])
        if self._clock() - self._since >= SUMMARY_EVERY_SECONDS:
            logger.info('Чаты Syntony, забор из API за час: прогонов %(runs)s, сообщений '
                        'записано %(messages)s (страниц ленты %(pages)s), снимков диалогов '
                        '%(conversations)s', totals)
            self._since = self._clock()
            self._totals = dict.fromkeys(totals, 0)


def _loop(db, interval, first_delay):
    time.sleep(first_delay)
    journal = _Journal()
    while True:
        try:
            journal.note(run_once(db))
        except Exception:  # noqa: BLE001
            logger.exception('Чаты Syntony, забор из API: прогон упал')
        time.sleep(interval)


_worker = None
_worker_pid = None
_worker_lock = threading.Lock()


def start_worker(db, *, interval=INTERVAL_SECONDS, first_delay=FIRST_RUN_DELAY_SECONDS):
    """Один поток на процесс. Без ключа или с выключателем — не запускается (ключ и
    выключатель меняются только перезапуском сервиса)."""
    global _worker, _worker_pid
    if not is_enabled() or not client.is_configured():
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        _worker = threading.Thread(target=_loop, args=(db, interval, first_delay),
                                   name='syntony-chats-sync', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker
