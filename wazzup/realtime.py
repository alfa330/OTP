"""Bounded PG -> SSE fan-out. One DB listener per process, zero per-client queries."""
import collections
import json
import logging
import select
import threading
import time
from datetime import datetime

CHANNEL = 'wazzup_pilot_events'
HEARTBEAT_SECONDS = 20
HYDRATE_BATCH_SIZE = 100
MAX_EVENT_BYTES = 256 * 1024
MAX_BUFFER_BYTES = 8 * 1024 * 1024
_DELIVERY_RANK = {'pending': 0, 'sent': 1, 'error': 2, 'delivered': 3, 'read': 4}
_MESSAGE_FIELDS = ('messageId', 'dt', 'isEcho', 'type', 'text', 'contentUri',
                   'authorName', 'authorId', 'status', 'isEdited', 'isDeleted', 'wazzupDt')
_CHAT_FIELDS = ('channelId', 'chatId', 'chatType', 'contactName', 'contactPhone',
                'lastMessageAt', 'lastMessageText', 'lastMessageIsEcho',
                'messagesCount', 'inboundCount', 'outboundCount')

_HYDRATE_SQL = """
    SELECT m.message_id,m.dt,m.is_echo,m.type,m.text,m.content_uri,
           COALESCE(o.author_name,m.author_name),m.author_id,m.status,
           m.is_edited,m.is_deleted,m.wazzup_dt,
           c.channel_id,c.chat_id,c.chat_type,c.contact_name,c.contact_phone,
           c.last_message_at,c.last_message_text,c.last_message_is_echo,
           c.messages_count,c.inbound_count,c.outbound_count
      FROM wazzup_messages m
      LEFT JOIN wazzup_pilot_outbox o
        ON o.message_id=m.message_id AND o.account=m.account
      LEFT JOIN wazzup_chats c
        ON c.account=m.account AND c.channel_id=m.channel_id AND c.chat_id=m.chat_id
     WHERE m.account='op' AND m.message_id=ANY(%s)
"""


def _change_key(event):
    return (event.get('account'), event.get('channelId'), event.get('chatId'), event['messageId'])


def merge_changes(previous, current):
    """Keep an INSERT/edit when a later status arrives before a subscriber reads it."""
    if previous is None:
        return current
    merged = dict(previous, **current)
    merged['affectsList'] = previous.get('affectsList', True) or current.get('affectsList', True)
    merged['statusOnly'] = previous.get('statusOnly') is True and current.get('statusOnly') is True
    if current.get('statusOnly') is not True:
        # A missing hydrated record is a refresh fallback, never a stale edit.
        for key in ('message', 'chat'):
            if key not in current:
                merged.pop(key, None)
    previous_status, current_status = previous.get('status'), current.get('status')
    if _DELIVERY_RANK.get(previous_status, -1) > _DELIVERY_RANK.get(current_status, -1):
        merged['status'] = previous_status
    if merged.get('message'):
        merged['message'] = dict(merged['message'], status=merged.get('status'))
    return merged


def _json_item(fields, row):
    return dict(zip(fields, (v.isoformat() if isinstance(v, datetime) else v for v in row)))


def broadcast_changes(cursor, changes, event_broker):
    """Hydrate once per process/batch, never once per connected browser.

    Trigger payloads contain no message content. PostgreSQL delivers them after
    COMMIT, so the shared listener can read the message and its final chat summary
    together. Pure delivery-status transitions do not touch the database.
    """
    pending = {}

    def flush():
        if not pending:
            return
        events = list(pending.values())
        pending.clear()
        with event_broker.condition:
            subscribed = event_broker.streams > 0
        full = [e for e in events if e.get('statusOnly') is not True]
        rows = {}
        if subscribed and full:
            cursor.execute(_HYDRATE_SQL, ([e['messageId'] for e in full],))
            rows = {row[0]: row for row in cursor.fetchall()}
        for event in events:
            row = rows.get(event['messageId'])
            if row is not None and event.get('statusOnly') is not True:
                event = dict(event, message=_json_item(_MESSAGE_FIELDS, row[:12]), status=row[8])
                if row[12] is not None:
                    event['chat'] = _json_item(_CHAT_FIELDS, row[12:])
            event_broker.publish(event)

    for event in changes:
        key = _change_key(event)
        pending[key] = merge_changes(pending.get(key), event)
        if len(pending) >= HYDRATE_BATCH_SIZE:
            flush()
    flush()


class EventBroker:
    def __init__(self, capacity=2048, max_bytes=MAX_BUFFER_BYTES):
        self.condition = threading.Condition()
        self.events = collections.deque()
        self._event_sizes = collections.deque()
        self.capacity = max(1, capacity)
        self.max_bytes = max_bytes
        self.buffer_bytes = 0
        self.seq = 0
        self.streams = 0
        self.ready = False

    def set_ready(self, ready):
        with self.condition:
            self.ready = ready
            self.publish({'reload': True} if ready else {'unavailable': True})

    def publish(self, event):
        size = len(json.dumps(event, ensure_ascii=False).encode('utf-8'))
        if size > min(MAX_EVENT_BYTES, self.max_bytes):
            # Fall back to the existing reconciliation endpoint for exceptional
            # messages. Do not truncate text, media URLs, or attachment data.
            event = {key: value for key, value in event.items() if key not in ('message', 'chat')}
            size = len(json.dumps(event, ensure_ascii=False).encode('utf-8'))
        with self.condition:
            self.seq += 1
            self.events.append((self.seq, event))
            self._event_sizes.append(size)
            self.buffer_bytes += size
            while len(self.events) > 1 and (len(self.events) > self.capacity
                                            or self.buffer_bytes > self.max_bytes):
                self.events.popleft()
                self.buffer_bytes -= self._event_sizes.popleft()
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
                    key = _change_key(event)
                    latest[key] = merge_changes(latest.get(key), event)
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
                        # execute() can receive further notifications while
                        # hydrating. Drain those before waiting for a new socket
                        # readiness edge, without repeatedly pop(0)-shifting.
                        notifications = conn.notifies[:]
                        conn.notifies.clear()

                        def changes():
                            for notification in notifications:
                                try:
                                    event = json.loads(notification.payload)
                                    if event.get('account') == 'op' and event.get('messageId'):
                                        yield event
                                except (ValueError, AttributeError):
                                    logging.warning('Invalid Wazzup pilot notification')

                        broadcast_changes(cursor, changes(), broker)
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
