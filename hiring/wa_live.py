# -*- coding: utf-8 -*-
"""Окно «Чаты» «Воронки найма» в реальном времени (владелец 20.11.2026: «чаты работают не так быстро и не в реальном
времени. Я попробовал написать, и моё сообщение пришло только после обновления страницы»).

Паттерн колокола (notifications/realtime.py): переписка, чаты и очередь отправки шлют NOTIFY hiring_wa триггером
(hiring/schema.py, LIVE_NOTIFY) — тычок уходит при коммите, вместе с тем, о чём сообщает, из какого бы процесса ни
пришло изменение (сообщение от шлюза, ответ рекрутера, отметка доставки, расшифровка голосового). Один слушатель на
процесс на собственном соединении раскладывает тычки потокам /api/hiring/wa/stream; окно по тычку перечитывает
список и открытый чат обычными запросами — права проверяются там же, как и при опросе.

В тычке только номера карточек затронутых чатов (0 — чат без карточки): телефоны — ключи чатов — в поток не уходят,
а номер карточки окну нужен, чтобы не перечитывать чат, которого изменение не касается.

Ёмкость: каждый поток держит нить waitress на всё время соединения, поэтому мест — stream_limit (сверх — 503, окно
остаётся на опросе); скрытая вкладка соединение рвёт, поток живёт не дольше STREAM_MAX_SECONDS и уходит событием
bye — клиент переподключается сразу (sseClient.js).
"""

import collections
import json
import logging
import select
import threading
import time

log = logging.getLogger(__name__)

CHANNEL = 'hiring_wa'
HEARTBEAT_SECONDS = 20
LISTENER_RETRY_SECONDS = 2
STREAM_MAX_SECONDS = 30 * 60
TICK_BUFFER = 500
MAX_CARDS_PER_FRAME = 200


def parse_payload(payload):
    """'{"c":12}' → frozenset({12}); мусор — None (тычок пропускается)."""
    try:
        data = json.loads(payload) if payload else None
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or isinstance(data.get('c'), bool):
        return None
    try:
        card = int(data.get('c'))
    except (TypeError, ValueError):
        return None
    return frozenset((card,)) if 0 <= card < 2 ** 31 else None


def sse(event, data):
    return 'event: %s\ndata: %s\n\n' % (event, json.dumps(data, separators=(',', ':')))


class Hub:
    """Тычки процесса и раздача их потокам. Всё — под condition."""

    def __init__(self, capacity=TICK_BUFFER):
        self.condition = threading.Condition()
        self.ticks = collections.deque(maxlen=max(1, capacity))   # (seq, frozenset карточек | None = всем)
        self.seq = 0
        self.streams = 0

    def publish(self, cards):
        with self.condition:
            self.seq += 1
            self.ticks.append((self.seq, cards))
            self.condition.notify_all()

    def current_seq(self):
        with self.condition:
            return self.seq

    def wait(self, after, timeout):
        """(затронутые карточки — set, или None «перечитать всё», или пусто — тычков не было; новый курсор).
        Поток, отставший дальше кольца, перечитывает всё."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            while self.seq <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return set(), self.seq
                self.condition.wait(remaining)
            oldest = self.ticks[0][0] if self.ticks else self.seq + 1
            if after < oldest - 1:
                return None, self.seq
            cards = set()
            for seq, targets in self.ticks:
                if seq <= after:
                    continue
                if targets is None:
                    return None, self.seq
                cards |= targets
            return cards, self.seq

    def acquire(self, limit):
        with self.condition:
            if self.streams >= int(limit):
                return False
            self.streams += 1
            return True

    def release(self):
        with self.condition:
            self.streams = max(0, self.streams - 1)


hub = Hub()


def frame(cards):
    """Кадр потока: {"c": [...]} — затронутые карточки, {"all": 1} — перечитать всё."""
    if cards is None or len(cards) > MAX_CARDS_PER_FRAME:
        return sse('chats', {'all': 1})
    return sse('chats', {'c': sorted(cards)})


def _drain(conn, target):
    while conn.notifies:
        note = conn.notifies.pop(0)
        cards = parse_payload(note.payload)
        if cards is not None:
            target.publish(cards)


def _run_listener(connect, target):
    """Вечный цикл слушателя на собственном соединении — пул приложения не трогаем. После каждого подключения —
    тычок «перечитать всё»: пока LISTEN лежал, PostgreSQL тычки этому процессу не копил."""
    while True:
        conn = None
        try:
            conn = connect()
            conn.set_session(autocommit=True)
            cursor = conn.cursor()
            cursor.execute('LISTEN ' + CHANNEL)
            target.publish(None)
            log.info('Воронка найма: слушатель %s подключён', CHANNEL)
            while True:
                readable, _, _ = select.select([conn], [], [], HEARTBEAT_SECONDS)
                conn.poll()
                _drain(conn, target)
                if not readable:
                    cursor.execute('SELECT 1')
                    conn.poll()
                    _drain(conn, target)
        except Exception:  # noqa: BLE001
            log.exception('Воронка найма: слушатель %s упал, переподключение', CHANNEL)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        time.sleep(LISTENER_RETRY_SECONDS)


_start_lock = threading.Lock()
_started = False


def ensure_listener(connect, target=None):
    """Поднять слушатель один раз на процесс — лениво, при первом потоке."""
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        threading.Thread(target=_run_listener, args=(connect, target or hub), daemon=True,
                         name='hiring-wa-listener').start()
        _started = True
