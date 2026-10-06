"""Bounded PG -> SSE fan-out. One DB listener per process, zero per-client queries."""
import collections
import json
import logging
import select
import threading
import time

CHANNEL = 'wazzup_pilot_events'
HEARTBEAT_SECONDS = 20


class EventBroker:
    def __init__(self, capacity=2048):
        self.condition = threading.Condition()
        self.events = collections.deque(maxlen=capacity)
        self.seq = 0
        self.streams = 0
        self.ready = False

    def set_ready(self, ready):
        with self.condition:
            self.ready = ready
            self.publish({'reload': True} if ready else {'unavailable': True})

    def publish(self, event):
        with self.condition:
            self.seq += 1
            self.events.append((self.seq, event))
            self.condition.notify_all()

    def current_seq(self):
        with self.condition:
            return self.seq

    def wait(self, after, timeout=HEARTBEAT_SECONDS):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.seq <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], self.seq
                self.condition.wait(remaining)
            if self.events and after < self.events[0][0] - 1:
                return [{'reload': True}], self.seq
            # A single batch can have many status transitions of the same message.
            latest = {}
            for seq, event in self.events:
                if seq > after:
                    if event.get('reload') or event.get('unavailable'):
                        return [{'reload': True} if self.ready else {'unavailable': True}], self.seq
                    previous = latest.get(event['messageId'])
                    if previous is not None:
                        event = dict(event, affectsList=(previous.get('affectsList', True)
                                                        or event.get('affectsList', True)))
                    latest[event['messageId']] = event
            return list(latest.values()), self.seq

    def acquire(self, limit=8):
        with self.condition:
            if self.streams >= limit:
                return False
            self.streams += 1
            return True

    def release(self):
        with self.condition:
            self.streams = max(0, self.streams - 1)


broker = EventBroker()
_start_lock = threading.Lock()
_started = False


def _listen(connect):
    while True:
        conn = None
        try:
            conn = connect()
            conn.set_session(autocommit=True)
            with conn.cursor() as cursor:
                cursor.execute('LISTEN ' + CHANNEL)
                broker.set_ready(True)
                while True:
                    ready, _, _ = select.select([conn], [], [], HEARTBEAT_SECONDS)
                    if not ready:
                        cursor.execute('SELECT 1')
                    conn.poll()
                    while conn.notifies:
                        payload = conn.notifies.pop(0).payload
                        try:
                            event = json.loads(payload)
                            if event.get('account') == 'op' and event.get('messageId'):
                                broker.publish(event)
                        except (ValueError, AttributeError):
                            logging.warning('Invalid Wazzup pilot notification')
        except Exception:
            logging.exception('Wazzup pilot listener reconnecting')
            broker.set_ready(False)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        time.sleep(2)


def ensure_listener(connect):
    global _started
    with _start_lock:
        if not _started:
            threading.Thread(target=_listen, args=(connect,),
                             name='wazzup-pilot-listener', daemon=True).start()
            _started = True
