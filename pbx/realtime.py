# -*- coding: utf-8 -*-
"""Реалтайм раздела «АТС»: живые звонки станции → зрители, команды портала → коннектор.

Как это устроено
----------------
    коннектор ──POST /agent/sync──► pbx_calls (rev) ──NOTIFY pbx_live──► слушатель процесса
                                                                            │  (одно соединение)
    коннектор ◄──SSE /agent/stream── команды ◄── NOTIFY {"k":"cmd"} ────────┤
    браузер   ◄──SSE /api/pbx/stream, /api/op_wallboard/live ◄── Hub ◄──────┘

Паттерн — тот же, что у чатов Wazzup (wazzup/realtime.py) и колокола: один слушатель
`LISTEN pbx_live` на процесс на СОБСТВЕННОМ соединении (пул приложения не трогаем), раздача
из памяти. По тычку «звонки изменились» слушатель ОДНИМ запросом по индексу читает строки
новее своей ревизии (`rev`) и раскладывает их всем потокам — ни одного запроса на зрителя.
NOTIFY приходит после коммита, поэтому прочитанное всегда то, что записано.

Состояние процесса (`Hub`):
  * `calls` — живые звонки (идущие и законченные не дольше ENDED_KEEP_SECONDS назад);
    на них табло ОП считает «сейчас» на каждое событие без похода в базу;
  * `connector` — когда коннектор последний раз был на связи и видит ли он станцию; сам
    коннектор присылает пачку или пульс не реже раза в 15 с, и каждый такой запрос шлёт
    `{"k":"conn"}` с отметкой — поэтому «коннектор замолчал» поток узнаёт по возрасту
    отметки, а не по событию, которого не будет;
  * кольцо событий с эпохой процесса и номером — как у Wazzup: переподключившийся поток
    продолжает с места, если разрыв короткий, иначе получает сброс и перечитывает целиком;
  * счётчик команд: тычок `{"k":"cmd"}` будит канал команд коннектора в том процессе, где
    он сейчас держится.

Тычки (payload — компактный JSON с ключом "k"):
    calls   — коннектор записал изменения звонков;
    conn    — коннектор на связи: {"seen": unix, "ami": bool, "err": str|null, "ver": str}
              или пульс канала команд {"stream": unix};
    cmd     — поставлена команда станции;
    board   — изменилось то, из чего табло ОП считает день: касания моста ("why": "touches")
              или статус телефона оператора ("why": "status", "op": id).

Ёмкость: каждый поток занимает нить waitress на всё время соединения, поэтому у каждого вида
потоков свой потолок (`Hub.acquire`): сверх него — 503, и клиент остаётся на обычном опросе.
"""

import collections
import json
import logging
import select
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

CHANNEL = 'pbx_live'
HEARTBEAT_SECONDS = 20
LISTENER_RETRY_SECONDS = 2
# Законченный звонок держим в памяти столько: экраны показывают «только что закончился» 90 с.
ENDED_KEEP_SECONDS = 120
# Коннектор молчит дольше — считаем отвалившимся (пачка или пульс — не реже раза в 15 с).
CONNECTOR_SILENT_SECONDS = 60
# Незаконченный звонок старше суток — это потерянный отбой, а не живой звонок.
LIVE_WINDOW = timedelta(days=1)
# Сколько изменений за раз читает слушатель; больше — значит, разрыв был долгим: полная перечитка.
CATCH_UP_LIMIT = 1000
# Запас назад при догоне (ревизий): закрывает коммиты не по порядку, см. handle_notifications.
CATCH_UP_MARGIN = 50
# Кольцо событий: поток, отставший дальше, получает сброс и перечитывает целиком.
BUFFER_CAPACITY = 2000
RESUME_MAX_EVENTS = 500

_ALMATY = timezone(timedelta(hours=5))


def now_local():
    """Наивное время Алматы — в нём живут времена звонков станции."""
    return datetime.now(_ALMATY).replace(tzinfo=None)


def notify(cursor, kind, **fields):
    """Тычок слушателям всех процессов. Уходит при коммите транзакции курсора — то есть
    только вместе с тем, о чём он сообщает."""
    payload = dict(fields)
    payload['k'] = kind
    cursor.execute('SELECT pg_notify(%s, %s)', (CHANNEL, json.dumps(payload, separators=(',', ':'))))


def sse(event, data):
    """Кадр SSE: имя события и JSON одной строкой."""
    return 'event: %s\ndata: %s\n\n' % (event, json.dumps(data, ensure_ascii=False, separators=(',', ':')))


def _stamp_seconds(value):
    """«YYYY-MM-DDTHH:MM:SS» (время станции) → datetime или None."""
    text = str(value or '')[:19]
    if len(text) != 19:
        return None
    try:
        return datetime.strptime(text.replace(' ', 'T'), '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return None


def _iso_to_epoch(value):
    """ISO с поясом (TIMESTAMPTZ из базы) → unix-время или None."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


class Hub:
    """Живое состояние процесса и раздача его потокам. Потокобезопасен: всё под `condition`."""

    def __init__(self, clock=time.time, local_now=now_local, capacity=BUFFER_CAPACITY):
        self.condition = threading.Condition()
        self.clock = clock
        self.local_now = local_now
        self.capacity = max(1, capacity)
        self.events = collections.deque()
        self.seq = 0
        self.epoch = uuid.uuid4().hex
        self.ready = False
        self.calls = {}
        self.rev = 0
        self.connector = {}
        self.cmd_seq = 0
        self.agent_generation = 0
        self.streams = collections.Counter()

    # ── кольцо событий ──────────────────────────────────────────────────────

    def _publish_locked(self, event):
        self.seq += 1
        self.events.append((self.seq, event))
        while len(self.events) > self.capacity:
            self.events.popleft()
        self.condition.notify_all()

    def publish(self, event):
        with self.condition:
            self._publish_locked(event)

    def current_seq(self):
        with self.condition:
            return self.seq

    def resume_from(self, epoch, after):
        """(курсор, продолжили ли) для потока, который подключается сейчас."""
        with self.condition:
            try:
                after = int(after)
            except (TypeError, ValueError):
                after = None
            oldest = self.events[0][0] if self.events else self.seq + 1
            if (epoch == self.epoch and after is not None and oldest - 1 <= after <= self.seq
                    and self.seq - after <= RESUME_MAX_EVENTS):
                return after, True
            return self.seq, False

    def wait(self, after, timeout=HEARTBEAT_SECONDS):
        """(события после курсора, новый курсор). Отстал дальше кольца — [{'kind': 'reset'}]."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            while self.seq <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], self.seq
                self.condition.wait(remaining)
            if self.events and after < self.events[0][0] - 1:
                return [{'kind': 'reset'}], self.seq
            return [event for seq, event in self.events if seq > after], self.seq

    # ── состояние ───────────────────────────────────────────────────────────

    def load_full(self, calls, connector, rev):
        """Полная перечитка (старт слушателя, переподключение, долгий разрыв)."""
        with self.condition:
            self.calls = {call['linkedid']: call for call in calls}
            self.rev = max(int(rev or 0), max((int(c.get('rev') or 0) for c in calls), default=0))
            if connector:
                self.connector.update(connector)
            self.ready = True
            self._expire_locked()
            self._publish_locked({'kind': 'reset'})

    def apply_calls(self, calls):
        """Изменения из базы (по возрастанию rev) → память и всем потокам. Возвращает, сколько
        звонков действительно новее того, что уже было в памяти."""
        fresh = []
        with self.condition:
            for call in calls:
                rev = int(call.get('rev') or 0)
                known = self.calls.get(call['linkedid'])
                if known is not None and int(known.get('rev') or 0) >= rev:
                    continue
                self.calls[call['linkedid']] = call
                fresh.append(call)
                self.rev = max(self.rev, rev)
            self._expire_locked()
            if fresh:
                self._publish_locked({'kind': 'calls', 'calls': fresh})
        return len(fresh)

    def apply_connector(self, meta):
        """Отметка коннектора из тычка. Событие наружу — только когда сменилось то, что видно
        на экране (станция видна или нет, ошибка, версия); пульс просто сдвигает отметку."""
        with self.condition:
            before = self._connector_view_locked()
            for key in ('seen', 'ami', 'err', 'ver', 'host', 'stream'):
                if key in meta:
                    self.connector[key] = meta[key]
            after = self._connector_view_locked()
            if before != after:
                self._publish_locked({'kind': 'connector'})

    def set_unavailable(self):
        with self.condition:
            self.ready = False
            self._publish_locked({'kind': 'unavailable'})

    def expire(self):
        with self.condition:
            self._expire_locked()

    def _expire_locked(self):
        now = self.local_now()
        for linkedid, call in list(self.calls.items()):
            if call.get('state') == 'ended':
                ended = _stamp_seconds(call.get('ended_at')) or _stamp_seconds(call.get('started_at'))
                if ended is None or (now - ended).total_seconds() > ENDED_KEEP_SECONDS:
                    del self.calls[linkedid]
            else:
                started = _stamp_seconds(call.get('started_at'))
                if started is None or now - started > LIVE_WINDOW:
                    del self.calls[linkedid]

    def snapshot(self):
        """(курсор, звонки, коннектор) атомарно: поток начинает с этого и дальше берёт события."""
        with self.condition:
            self._expire_locked()
            return self.seq, [dict(call) for call in self.calls.values()], dict(self.connector)

    def active_calls(self):
        with self.condition:
            return [dict(call) for call in self.calls.values() if call.get('state') != 'ended']

    # ── коннектор: на связи ли ──────────────────────────────────────────────

    def _connector_view_locked(self, now=None):
        moment = self.clock() if now is None else now
        seen = self.connector.get('seen')
        connected = bool(seen) and moment - float(seen) <= CONNECTOR_SILENT_SECONDS
        stream = self.connector.get('stream')
        return {
            'connected': connected,
            'ami_connected': bool(self.connector.get('ami')),
            'online': connected and bool(self.connector.get('ami')),
            'stream': bool(stream) and moment - float(stream) <= CONNECTOR_SILENT_SECONDS,
            'ami_error': self.connector.get('err') or None,
            'version': self.connector.get('ver') or None,
        }

    def connector_view(self, now=None):
        with self.condition:
            return self._connector_view_locked(now)

    # ── команды: канал коннектора ───────────────────────────────────────────

    def poke_commands(self):
        with self.condition:
            self.cmd_seq += 1
            self.condition.notify_all()

    def current_cmd_seq(self):
        with self.condition:
            return self.cmd_seq

    def new_agent_stream(self):
        """Новый канал команд вытесняет прежний этого процесса: у коннектора он один, а старый
        мог остаться висеть на полумёртвом соединении."""
        with self.condition:
            self.agent_generation += 1
            self.condition.notify_all()
            return self.agent_generation

    def wait_commands(self, after, generation, timeout):
        """(есть ли новые команды, курсор, жив ли этот канал)."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            while self.cmd_seq <= after and self.agent_generation == generation:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.cmd_seq > after, self.cmd_seq, self.agent_generation == generation

    # ── места под потоки ────────────────────────────────────────────────────

    def acquire(self, kind, limit):
        with self.condition:
            if self.streams[kind] >= int(limit):
                return False
            self.streams[kind] += 1
            return True

    def release(self, kind):
        with self.condition:
            self.streams[kind] = max(0, self.streams[kind] - 1)

    def stream_count(self, kind):
        with self.condition:
            return self.streams[kind]


hub = Hub()


# ── слушатель ────────────────────────────────────────────────────────────────

def _parse(payload):
    try:
        data = json.loads(payload) if payload else {}
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('k') else None


def full_reload(cursor, target):
    """Живые звонки и состояние коннектора из базы → память процесса.

    Ревизия читается ДО звонков: соединение слушателя в autocommit, и каждый запрос видит свой
    момент. Наоборот изменение, записанное между двумя запросами, получило бы ревизию не больше
    прочитанной позже и навсегда потерялось бы для догона «новее моей ревизии»."""
    from . import queries
    rev = queries.max_rev(cursor)
    calls = queries.live_calls(cursor, ended_within_seconds=ENDED_KEEP_SECONDS)
    state = queries.connector_state(cursor)
    connector = {'seen': _iso_to_epoch(state.get('last_seen_at')), 'ami': bool(state.get('ami_connected')),
                 'err': state.get('ami_error'), 'ver': state.get('version'),
                 'stream': _iso_to_epoch(state.get('stream_at'))}
    target.load_full(calls, connector, rev)


def handle_notifications(cursor, notifications, target):
    """Пачка тычков → память и события. Звонки читаются одним запросом на пачку."""
    from . import queries
    calls_changed = False
    for raw in notifications:
        data = _parse(getattr(raw, 'payload', raw))
        if data is None:
            continue
        kind = data.get('k')
        if kind == 'calls':
            calls_changed = True
        elif kind == 'conn':
            target.apply_connector({key: data[key] for key in ('seen', 'ami', 'err', 'ver', 'stream')
                                    if key in data})
        elif kind == 'cmd':
            target.poke_commands()
        elif kind == 'board':
            event = {'kind': 'board', 'why': str(data.get('why') or '')[:16]}
            if data.get('op') is not None:
                try:
                    event['operator_id'] = int(data['op'])
                except (TypeError, ValueError):
                    pass
            if data.get('ts') is not None:
                # Метка события: каждый поток табло видит его, а в базу за статусом идёт один.
                event['ts'] = data['ts']
            target.publish(event)
    if calls_changed:
        # С запасом назад: ревизии выдаёт последовательность, а коммиты двух записей могут прийти
        # не по порядку (две пачки одновременно) — меньшая ревизия видна позже большей. Повторно
        # прочитанное отсекает apply_calls по ревизии звонка.
        rows = queries.calls_since(cursor, max(0, target.rev - CATCH_UP_MARGIN), limit=CATCH_UP_LIMIT)
        if len(rows) >= CATCH_UP_LIMIT:
            full_reload(cursor, target)
        else:
            target.apply_calls(rows)
    else:
        target.expire()


def _run_listener(connect, target):
    while True:
        conn = None
        try:
            conn = connect()
            conn.set_session(autocommit=True)
            with conn.cursor() as cursor:
                cursor.execute('LISTEN ' + CHANNEL)
                full_reload(cursor, target)
                log.info('АТС: слушатель %s подключён, звонков в памяти %d', CHANNEL, len(target.calls))
                while True:
                    readable, _, _ = select.select([conn], [], [], HEARTBEAT_SECONDS)
                    if not readable:
                        cursor.execute('SELECT 1')
                    conn.poll()
                    while conn.notifies:
                        batch = conn.notifies[:]
                        conn.notifies.clear()
                        handle_notifications(cursor, batch, target)
                        conn.poll()
                    if not readable:
                        target.expire()
        except Exception:
            log.exception('АТС: слушатель %s упал, переподключение', CHANNEL)
            target.set_unavailable()
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        time.sleep(LISTENER_RETRY_SECONDS)


_start_lock = threading.Lock()
_started = False


def ensure_listener(connect, target=None):
    """Поднять слушатель один раз на процесс (лениво — при первом потоке)."""
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        threading.Thread(target=_run_listener, args=(connect, target or hub),
                         daemon=True, name='pbx-live-listener').start()
        _started = True


def wait_ready(target=None, timeout=5.0):
    """Дождаться первой загрузки слушателя (поток подключился раньше, чем слушатель прочитал
    базу). False — не дождались: поток отдаёт то, что есть, и сверится по событию reset."""
    target = target or hub
    deadline = time.monotonic() + timeout
    with target.condition:
        while not target.ready:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            target.condition.wait(remaining)
        return True
