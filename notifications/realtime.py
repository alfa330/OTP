# -*- coding: utf-8 -*-
"""Реалтайм-канал колокола: LISTEN bell_events → «тычки» подключённым SSE-потокам.

Схема повторяет проверенный паттерн аукциона смен (один PG-слушатель на процесс,
раздача из памяти), но проще: тычок не несёт данных — только «у тебя что-то
изменилось». Получив его, клиент перечитывает /api/notifications обычным load().
Поэтому здесь нет ни журнала событий, ни догрузки пропущенного из базы:
переподключившийся клиент просто перечитывает сводку и снова в актуальном
состоянии.

Периодической сверки нет и не должно появиться: она была бы обычным фоновым
опросом, ради отсутствия которого весь механизм и делался. Две дыры, которые
ею затыкали, закрыты событийно: окно, пока LISTEN лежал, — широковещательным
тычком сразу после подключения (см. _subscribe), а переходы чисто по времени
(открылось окно теста, наступил дедлайн) — полем next_change_at в самой сводке:
клиент просыпается ровно к названному моменту, см. sources.next_change_at.

Тычки шлют триггеры на таблицах источников — см.
database.py::_init_bell_notify_schema_tx. Payload: '{"u":[id,...]}' (адресный)
или '{"b":1}' (широковещательный — «Ивенты» и «4 You», их периметр зритель
выясняет сам при перечитке).

Ёмкость. Каждый SSE-поток занимает нить waitress на всё время соединения
(их всего WAITRESS_THREADS≈96), поэтому число потоков жёстко ограничено
слотами: сверх лимита клиент получает 503 и остаётся на обновлении по фокусу.
Замер 2026-08-09: активных за 5 минут — 13 человек, за 15 — 22, так что лимита
по умолчанию хватает всем живым вкладкам с запасом; скрытые вкладки слот не
держат — клиент рвёт соединение, уходя в фон.
"""

import collections
import json
import logging
import select
import threading
import time

# Имя канала продублировано в database.py (BELL_EVENTS_NOTIFY_CHANNEL и литерал
# в теле триггера): импортировать database отсюда нельзя — модуль тестируется
# без него. Совпадение сверяет tests/test_notifications.py.
BELL_NOTIFY_CHANNEL = 'bell_events'

# Пауза между тычками у молчащего потока: SSE-комментарий, чтобы прокси и
# браузер не считали соединение мёртвым.
HEARTBEAT_SECONDS = 25
LISTENER_RETRY_SECONDS = 2
# Кольцо последних тычков. Поток сканирует его от хвоста до своего курсора при
# каждом пробуждении, так что отстать на длину кольца практически невозможно —
# запас на всплеск (массовое назначение опроса даёт по тычку на назначение).
TICK_BUFFER_MAXLEN = 1000

_condition = threading.Condition()
_ticks = collections.deque(maxlen=TICK_BUFFER_MAXLEN)  # (seq, frozenset|None), None = всем
_seq = 0

_listener_started = False
_listener_lock = threading.Lock()

_active_streams = 0
_streams_lock = threading.Lock()


def _publish(targets):
    global _seq
    with _condition:
        _seq += 1
        _ticks.append((_seq, targets))
        _condition.notify_all()


def _parse_payload(payload):
    """None — широковещательный, frozenset — адресный, False — мусор (игнор)."""
    try:
        data = json.loads(payload) if payload else {}
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if data.get('b'):
        return None
    try:
        ids = frozenset(int(item) for item in (data.get('u') or []) if item)
    except Exception:
        return False
    return ids if ids else False


def _drain_notifies(conn):
    """Снять накопившиеся NOTIFY с соединения и опубликовать валидные."""
    got_any = False
    while conn.notifies:
        note = conn.notifies.pop(0)
        got_any = True
        parsed = _parse_payload(note.payload)
        if parsed is not False:
            _publish(parsed)
    return got_any


def _subscribe(conn):
    """Оформить LISTEN и заставить уже живые SSE-потоки сверить сводку.

    Между падением старого соединения и успешным новым LISTEN PostgreSQL не
    хранит NOTIFY для этого процесса. Широковещательная тычка после каждого
    подключения закрывает это окно: содержимого в ней нет, клиенты просто
    перечитают актуальное состояние обычным HTTP-запросом.
    """
    conn.set_session(autocommit=True)
    cursor = conn.cursor()
    cursor.execute('LISTEN %s' % BELL_NOTIFY_CHANNEL)
    _publish(None)
    logging.info('Колокол: слушатель %s подключён, отправлена сверка',
                 BELL_NOTIFY_CHANNEL)
    return cursor


def _run_listener(connect):
    """Вечный цикл слушателя на СОБСТВЕННОМ соединении — пул приложения не трогаем."""
    while True:
        conn = None
        try:
            conn = connect()
            cursor = _subscribe(conn)
            while True:
                readable, _, _ = select.select([conn], [], [], HEARTBEAT_SECONDS)
                conn.poll()
                got_notify = _drain_notifies(conn)
                if not readable and not got_notify:
                    # Тишина — держим соединение живым. Сам запрос может втянуть
                    # ожидающие NOTIFY в conn.notifies, поэтому после него ещё раз.
                    cursor.execute('SELECT 1')
                    conn.poll()
                    _drain_notifies(conn)
        except Exception:
            logging.exception('Колокол: слушатель %s упал, переподключение', BELL_NOTIFY_CHANNEL)
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        time.sleep(LISTENER_RETRY_SECONDS)


def ensure_listener(connect):
    """Поднять слушатель один раз на процесс (лениво, при первом SSE-клиенте)."""
    global _listener_started
    if _listener_started:
        return
    with _listener_lock:
        if _listener_started:
            return
        threading.Thread(
            target=_run_listener,
            args=(connect,),
            daemon=True,
            name='bell-events-listener',
        ).start()
        _listener_started = True


def current_seq():
    with _condition:
        return _seq


def wait_for_tick(after_seq, user_id, timeout_seconds):
    """(есть ли тычок для user_id после after_seq, новый курсор).

    Чужие адресные тычки молча продвигают курсор: они уже просканированы, и
    возвращаться к ним при следующем пробуждении незачем.
    """
    deadline = time.monotonic() + max(0.1, float(timeout_seconds or 0.1))
    with _condition:
        while True:
            # Если курсор старше самого раннего сохранённого элемента, часть
            # тычков уже вытеснена из deque. Нельзя заключать, что среди них не
            # было адресной для этого пользователя: принудительная перечитка
            # сводки дешевле и восстанавливает точное состояние.
            oldest_seq = _ticks[0][0] if _ticks else _seq + 1
            if after_seq < oldest_seq - 1:
                return True, _seq

            matched = False
            for seq, targets in reversed(_ticks):
                if seq <= after_seq:
                    break
                if targets is None or user_id in targets:
                    matched = True
                    break
            cursor_seq = _seq
            if matched:
                return True, cursor_seq
            after_seq = cursor_seq
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, cursor_seq
            _condition.wait(timeout=remaining)


def try_acquire_stream_slot(limit):
    global _active_streams
    with _streams_lock:
        if _active_streams >= int(limit):
            return False
        _active_streams += 1
        return True


def release_stream_slot():
    global _active_streams
    with _streams_lock:
        _active_streams = max(0, _active_streams - 1)


def active_stream_count():
    with _streams_lock:
        return _active_streams
