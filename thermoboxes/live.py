# -*- coding: utf-8 -*-
"""«Термокороба» на открытом экране — сразу после чтения Google-таблицы (владелец 24.11.2026: «давай сделаем
первое, то есть оно будет читать раз в минуту и обновлять»).

Паттерн «Воронки найма» (hiring/wa_live.py) и колокола. Каждое чтение листа — плановое раз в минуту, «Обновить из
таблицы», ошибка чтения — пишет итог в thermobox_sync, и там же (queries.save_sync_state) уходит NOTIFY CHANNEL.
PostgreSQL отдаёт его слушателям при коммите, вместе с тем, о чём он сообщает, из какого бы процесса ни пришло чтение
(планировщик в worker, кнопка в api), а у откаченной записи не отдаёт вовсе. Один слушатель на процесс на своём
соединении будит потоки /api/thermoboxes/stream. Данных в тычке нет: экран по нему спрашивает /api/thermoboxes/source
и перечитывает строки, только когда сдвинулся changed_at, — права проверяются там же, как и при опросе.

Ёмкость: поток держит нить waitress на всё время соединения — мест stream_limit (сверх — 503, экран остаётся на
опросе раз в 30 с); вкладка, скрытая дольше минуты, соединение рвёт; поток живёт не дольше STREAM_MAX_SECONDS и уходит
событием bye — клиент переподключается сразу (src/components/common/sseClient.js).

Слушатель не подключён (база не даёт соединения, сеть) — новый поток получает 503 и экран живёт на опросе: поток без
слушателя только пинговал бы, а экран считал бы себя живым. Уже открытые потоки на это время замолкают — экран сам
возвращается на опрос, не услышав тычка дольше 2,5 минут (sheetPulse.js, SILENCE_MS): тычок приходит каждую минуту.
"""

import logging
import selectors
import threading
import time

log = logging.getLogger(__name__)

CHANNEL = 'thermobox_live'
HEARTBEAT_SECONDS = 20
LISTENER_RETRY_SECONDS = 2
LISTENER_RETRY_MAX_SECONDS = 60
LISTENER_HEALTHY_SECONDS = 60
LISTEN_WAIT_SECONDS = 3
STREAM_MAX_SECONDS = 30 * 60


def notify(cursor):
    """Тычок «лист прочитан» — в той же транзакции, что и запись: уйдёт при коммите, у отката не уйдёт."""
    cursor.execute("SELECT pg_notify(%s, '')", (CHANNEL,))


def sse(event):
    return 'event: %s\ndata: {}\n\n' % event


class Hub:
    """Тычки процесса и раздача их потокам. Всё — под condition.

    Тычок не несёт данных, поэтому кольца, как у «Воронки найма», не нужно: потоку достаточно знать, что номер
    сдвинулся с его последнего пробуждения, — сколько бы тычков ни пришло за это время, экран спросит /source один раз.
    """

    def __init__(self):
        self.condition = threading.Condition()
        self.seq = 0
        self.streams = 0
        self.listening = False

    def set_listening(self, value):
        with self.condition:
            self.listening = bool(value)
            self.condition.notify_all()

    def wait_listening(self, timeout):
        """Подключён ли слушатель — ждать до timeout: первый поток процесса поднимает его сам и ждёт LISTEN."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            while not self.listening:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def publish(self):
        with self.condition:
            self.seq += 1
            self.condition.notify_all()

    def current_seq(self):
        with self.condition:
            return self.seq

    def wait(self, after, timeout):
        """(был ли тычок после after, новый курсор). Тычка нет до timeout — (False, прежний курсор)."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            while self.seq <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, self.seq
                self.condition.wait(remaining)
            return True, self.seq

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


def _drain(conn, target):
    """Снять пришедшие NOTIFY; пачку, пришедшую разом, — одним тычком."""
    if conn.notifies:
        del conn.notifies[:]
        target.publish()


def next_retry_delay(delay, lived_seconds):
    """Пауза перед переподключением слушателя. Соединение прожило долго — сбой разовый, переподключиться быстро;
    падает сразу (база не даёт соединений, выкладка) — пауза растёт до LISTENER_RETRY_MAX_SECONDS: каждое
    подключение будит экраны, и частые переподключения гоняли бы их за /source."""
    if lived_seconds >= LISTENER_HEALTHY_SECONDS:
        return LISTENER_RETRY_SECONDS
    return min(LISTENER_RETRY_MAX_SECONDS, max(LISTENER_RETRY_SECONDS, delay * 2))


def _run_listener(connect, target, stop=None):
    """Цикл слушателя на собственном соединении — пул приложения не трогаем. После каждого подключения — тычок:
    пока LISTEN лежал, PostgreSQL тычки этому процессу не копил, и экраны должны сверить /source сами.

    selectors, а не select.select: у процесса потоков SSE больше тысячи соединений, и номер дескриптора слушателя
    может оказаться выше 1023 — select.select на таком бросает ValueError. stop — только для тестов."""
    delay = LISTENER_RETRY_SECONDS
    while stop is None or not stop.is_set():
        conn = None
        selector = None
        started = time.monotonic()
        try:
            conn = connect()
            conn.set_session(autocommit=True)
            cursor = conn.cursor()
            cursor.execute('LISTEN ' + CHANNEL)
            target.set_listening(True)
            target.publish()
            log.info('Термокороба: слушатель %s подключён', CHANNEL)
            selector = selectors.DefaultSelector()
            selector.register(conn, selectors.EVENT_READ)
            while stop is None or not stop.is_set():
                ready = selector.select(HEARTBEAT_SECONDS)
                conn.poll()
                _drain(conn, target)
                if not ready:
                    # Тишина — проверить, что соединение живо: мёртвое бросит, и слушатель переподключится.
                    cursor.execute('SELECT 1')
                    conn.poll()
                    _drain(conn, target)
        except Exception:  # noqa: BLE001
            log.exception('Термокороба: слушатель %s упал, переподключение', CHANNEL)
        finally:
            target.set_listening(False)
            for resource in (selector, conn):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:  # noqa: BLE001
                        pass
        if stop is not None and stop.is_set():
            return
        delay = next_retry_delay(delay, time.monotonic() - started)
        time.sleep(delay)


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
                         name='thermobox-live-listener').start()
        _started = True
