"""Bounded PG -> SSE fan-out. One DB listener per process, zero per-client queries."""
import collections
import json
import logging
import select
import threading
import time
import uuid
from datetime import datetime

from .chat_list import last_message_columns
from .notes import note_item
from .typing import normalize_event as normalize_typing_event

CHANNEL = 'wazzup_pilot_events'
HEARTBEAT_SECONDS = 20
HYDRATE_BATCH_SIZE = 100
# A longer gap is cheaper to reconcile over HTTP than to replay as one huge frame.
RESUME_MAX_EVENTS = 500
MAX_EVENT_BYTES = 256 * 1024
MAX_BUFFER_BYTES = 8 * 1024 * 1024
_DELIVERY_RANK = {'pending': 0, 'sent': 1, 'error': 2, 'delivered': 3, 'read': 4}
# The same fields, in the same order, as the history endpoints (pilot.MESSAGE_FIELDS).
_MESSAGE_FIELDS = ('messageId', 'dt', 'isEcho', 'type', 'text', 'contentUri',
                   'authorName', 'authorId', 'status', 'isEdited', 'isDeleted', 'wazzupDt',
                   'replyToMessageId', 'replyText', 'replyAuthorName', 'clientMessageId')
_CHAT_OFFSET = len(_MESSAGE_FIELDS)
_CHAT_FIELDS = ('channelId', 'chatId', 'chatType', 'contactName', 'contactPhone',
                'lastMessageAt', 'lastMessageText', 'lastMessageIsEcho',
                'messagesCount', 'inboundCount', 'outboundCount',
                'lastMessageId', 'lastMessageStatus')

_HYDRATE_SQL = """
    SELECT m.message_id,m.dt,m.is_echo,m.type,m.text,m.content_uri,
           COALESCE(o.author_name,m.author_name),m.author_id,m.status,
           m.is_edited,m.is_deleted,m.wazzup_dt,o.reply_to_message_id,
           CASE WHEN r.is_deleted THEN NULL ELSE r.text END,r.author_name,o.request_id,
           c.channel_id,c.chat_id,c.chat_type,c.contact_name,c.contact_phone,
           c.last_message_at,c.last_message_text,c.last_message_is_echo,
           c.messages_count,c.inbound_count,c.outbound_count,
           {last_message}
      FROM wazzup_messages m
      LEFT JOIN wazzup_pilot_outbox o
        ON o.message_id=m.message_id AND o.account=m.account
      LEFT JOIN wazzup_chats c
        ON c.account=m.account AND c.channel_id=m.channel_id AND c.chat_id=m.chat_id
      LEFT JOIN wazzup_messages r ON r.message_id=o.reply_to_message_id AND r.account=m.account
        AND r.channel_id=m.channel_id AND r.chat_id=m.chat_id
     WHERE m.account='op' AND m.message_id=ANY(%s)
""".replace('{last_message}', last_message_columns('m'))

_HYDRATE_NOTES_SQL = """
    SELECT id,created_at,text,author_name,author_id,channel_id,chat_id
      FROM wazzup_chat_notes WHERE account='op' AND id=ANY(%s::uuid[])
"""


def _change_key(event):
    if event.get('kind') == 'typing':
        return ('typing', event.get('account'), event.get('channelId'), event.get('chatId'),
                event.get('userId'), event.get('clientId'))
    return (event.get('account'), event.get('channelId'), event.get('chatId'), event['messageId'])


def merge_changes(previous, current):
    """Keep an INSERT/edit when a later status arrives before a subscriber reads it."""
    if previous is None:
        return current
    if current.get('kind') == 'typing':
        # Across worker processes NOTIFY transactions can commit out of order.
        field = 'sequence' if 'sequence' in previous and 'sequence' in current else 'emittedAt'
        if current.get(field, 0) == previous.get(field, 0):
            return current if current.get('typing') is False else previous
        return current if current.get(field, 0) > previous.get(field, 0) else previous
    merged = dict(previous, **current)
    merged['affectsList'] = previous.get('affectsList', True) or current.get('affectsList', True)
    merged['statusOnly'] = previous.get('statusOnly') is True and current.get('statusOnly') is True
    if current.get('statusOnly') is not True:
        # A missing hydrated record is a refresh fallback, never a stale edit.
        for key in ('message', 'chat', 'note'):
            if key not in current:
                merged.pop(key, None)
    previous_status, current_status = previous.get('status'), current.get('status')
    if _DELIVERY_RANK.get(previous_status, -1) > _DELIVERY_RANK.get(current_status, -1):
        merged['status'] = previous_status
    if merged.get('message'):
        merged['message'] = dict(merged['message'], status=merged.get('status'))
    return merged


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if isinstance(value, uuid.UUID) else value


def _json_item(fields, row):
    return dict(zip(fields, (_json_value(v) for v in row)))


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
        full = [e for e in events if e.get('statusOnly') is not True
                and e.get('kind') not in ('note', 'typing')]
        notes = [e for e in events if e.get('kind') == 'note' and e.get('noteId')]
        rows = {}
        note_rows = {}
        if subscribed and full:
            cursor.execute(_HYDRATE_SQL, ([e['messageId'] for e in full],))
            rows = {row[0]: row for row in cursor.fetchall()}
        if subscribed and notes:
            cursor.execute(_HYDRATE_NOTES_SQL, ([e['noteId'] for e in notes],))
            note_rows = {(str(row[0]), row[5], row[6]): row for row in cursor.fetchall()}
        for event in events:
            if event.get('kind') == 'typing':
                event_broker.publish(event)
                continue
            if event.get('kind') == 'note':
                row = note_rows.get((event.get('noteId'), event.get('channelId'), event.get('chatId')))
                if row is not None:
                    event = dict(event, note=note_item(row[:5]))
                event_broker.publish(event)
                continue
            row = rows.get(event['messageId'])
            if row is not None and event.get('statusOnly') is not True:
                event = dict(event, message=_json_item(_MESSAGE_FIELDS, row[:_CHAT_OFFSET]), status=row[8])
                if row[_CHAT_OFFSET] is not None:
                    event['chat'] = _json_item(_CHAT_FIELDS, row[_CHAT_OFFSET:])
            event_broker.publish(event)

    for event in changes:
        if event.get('kind') == 'typing':
            event = normalize_typing_event(event)
            if event is None:
                continue
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
        # Sequence numbers are only meaningful within one process: a client
        # resuming after a restart or a deploy must reconcile, not continue.
        self.epoch = uuid.uuid4().hex

    def resume_from(self, epoch, after):
        """(cursor, resumed) for a stream that starts now.

        A reconnecting client continues after the last sequence it saw when that
        point is still in this broker's buffer and the gap is short: everything
        it missed is replayed and it needs no reconciliation. Otherwise it starts
        at the current sequence and must reconcile."""
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

    def set_ready(self, ready):
        with self.condition:
            self.ready = ready
            self.publish({'reload': True} if ready else {'unavailable': True})

    def publish(self, event):
        size = len(json.dumps(event, ensure_ascii=False).encode('utf-8'))
        if size > min(MAX_EVENT_BYTES, self.max_bytes):
            # Fall back to the existing reconciliation endpoint for exceptional
            # messages. Do not truncate text, media URLs, or attachment data.
            event = {key: value for key, value in event.items() if key not in ('message', 'chat', 'note')}
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
            now_ms = int(time.time() * 1000)
            return [event for event in latest.values() if event.get('kind') != 'typing'
                    or event.get('expiresAt', 0) > now_ms], self.seq

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
                                    if (event.get('account') == 'op'
                                            and (event.get('messageId') or event.get('kind') == 'typing')):
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
