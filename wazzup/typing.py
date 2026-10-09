"""Short-lived operator presence over the existing PostgreSQL/SSE transport."""
import collections
import json
import threading
import time
import uuid

from flask import jsonify, request

TTL_MS = 8000
MIN_HEARTBEAT_SECONDS = 2
RETENTION_SECONDS = 60
MAX_SEQUENCE = 9007199254740991


class TypingLimiter:
    """Bounded process-local coalescing and abuse limits, without background jobs.

    The user's bucket also limits a caller rotating client UUIDs. A final stop
    for an accepted start remains allowed even after that bucket is exhausted.
    Sequence tombstones prevent a delayed request from reviving a stopped tab.
    """

    def __init__(self, max_sessions=2048, max_users=1024, clock=time.monotonic):
        self.sessions = collections.OrderedDict()
        self.users = collections.OrderedDict()
        self.max_sessions, self.max_users = max_sessions, max_users
        self.clock = clock
        self.lock = threading.Lock()

    @staticmethod
    def _prune(entries, now):
        while entries and next(iter(entries.values()))['seen'] < now - RETENTION_SECONDS:
            entries.popitem(last=False)

    def reserve(self, user_id, client_id, scope, active, sequence=None):
        now = self.clock()
        key = (user_id, client_id)
        with self.lock:
            self._prune(self.sessions, now)
            self._prune(self.users, now)
            previous = self.sessions.get(key)
            if previous:
                highest = previous['sequence']
                if highest is not None and (sequence is None or sequence <= highest):
                    return None
                # Remember even coalesced requests so an older stop cannot beat
                # a newer heartbeat that did not need another notification.
                previous['sequence'] = sequence
                previous['seen'] = now
                self.sessions.move_to_end(key)
                if (previous['scope'] == scope and previous['active'] == active
                        and (not active or now - previous['emitted'] < MIN_HEARTBEAT_SECONDS)):
                    return None
            bucket = self.users.get(user_id, {'tokens': 8.0, 'seen': now})
            tokens = min(8.0, bucket['tokens'] + max(0, now - bucket['seen']) * 2)
            final_stop = bool(previous and previous['active'] and not active
                              and previous['scope'] == scope)
            if tokens < 1 and not final_stop:
                return None
            self.users[user_id] = {'tokens': max(0, tokens - 1), 'seen': now}
            self.users.move_to_end(user_id)
            entry = dict(scope=scope, active=active, sequence=sequence, emitted=now, seen=now)
            self.sessions[key] = entry
            self.sessions.move_to_end(key)
            while len(self.sessions) > self.max_sessions:
                self.sessions.popitem(last=False)
            while len(self.users) > self.max_users:
                self.users.popitem(last=False)
            return entry

    def cancel(self, user_id, client_id, reservation):
        with self.lock:
            key = (user_id, client_id)
            if self.sessions.get(key) is reservation:
                self.sessions.pop(key, None)


def normalize_event(event, now_ms=None):
    """Whitelist transient fields; malformed notifications cannot break LISTEN."""
    if not isinstance(event, dict) or event.get('kind') != 'typing' or event.get('account') != 'op':
        return None
    if type(event.get('typing')) is not bool:
        return None
    for field, limit in (('chatId', 100), ('userId', 100), ('authorName', 200)):
        value = event.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
            return None
    for field in ('channelId', 'clientId'):
        value = event.get(field)
        if not isinstance(value, str) or len(value) != 36:
            return None
        try:
            if str(uuid.UUID(value)) != value:
                return None
        except ValueError:
            return None
    emitted, expires = event.get('emittedAt'), event.get('expiresAt')
    if type(emitted) is not int or type(expires) is not int:
        return None
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if emitted > now_ms + 1000 or expires != emitted + TTL_MS:
        return None
    if expires <= now_ms:
        return None
    sequence = event.get('sequence')
    if sequence is not None and (type(sequence) is not int or not 0 <= sequence <= MAX_SEQUENCE):
        return None
    fields = ('kind', 'account', 'channelId', 'chatId', 'userId', 'authorName',
              'clientId', 'typing', 'emittedAt', 'expiresAt')
    result = {field: event[field] for field in fields}
    if sequence is not None:
        result['sequence'] = sequence
    return result


def register_typing_routes(bp, actor, require_api_key, preflight, db, excluded_channels=()):
    limiter = TypingLimiter()

    @bp.route('/typing', methods=['POST', 'OPTIONS'])
    @require_api_key
    def typing():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = actor()
        if error:
            return error
        if request.content_length and request.content_length > 4096:
            return jsonify(error='Слишком большой запрос'), 413
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error='Некорректный запрос'), 400
        if body.get('account') != 'op':
            return jsonify(error='Индикатор набора доступен в чатах Верификаторов'), 403
        cid, chat, client_id = body.get('channelId'), body.get('chatId'), body.get('clientId')
        sequence = body.get('sequence')
        if (type(body.get('typing')) is not bool or not isinstance(chat, str)
                or not chat.strip() or len(chat) > 100 or '\x00' in chat
                or not isinstance(cid, str) or len(cid) != 36
                or not isinstance(client_id, str) or len(client_id) != 36
                or (sequence is not None and (type(sequence) is not int or not 0 <= sequence <= MAX_SEQUENCE))):
            return jsonify(error='Некорректный индикатор набора'), 400
        try:
            cid, client_id = str(uuid.UUID(cid)), str(uuid.UUID(client_id))
        except ValueError:
            return jsonify(error='Некорректный канал или клиент'), 400
        if cid in excluded_channels:
            return jsonify(error='Global обрабатывается отдельно'), 403
        user_id = str(user[0])
        reservation = limiter.reserve(user_id, client_id, (cid, chat), body['typing'], sequence)
        if reservation is None:
            return '', 204, {'Cache-Control': 'no-store'}
        emitted = int(time.time() * 1000)
        event = dict(kind='typing', account='op', channelId=cid, chatId=chat,
                     userId=user_id, authorName=str(user[2] or user[7] or 'Оператор')[:200],
                     clientId=client_id, typing=body['typing'], emittedAt=emitted,
                     expiresAt=emitted + TTL_MS)
        if sequence is not None:
            event['sequence'] = sequence
        # Internal presence has the same operator/account/chat access boundary
        # as the composer, but needs no vendor channel state or API credential:
        # clearing must still work after a channel disconnects. The scoped
        # existence check and NOTIFY share one cheap indexed query. No archive/
        # outbox rows, message text, or vendor HTTP are involved.
        from .realtime import CHANNEL
        try:
            with db._get_cursor() as cursor:
                cursor.execute("SELECT pg_notify(%s,%s) FROM wazzup_chats WHERE account='op' "
                               "AND channel_id=%s AND chat_id=%s AND chat_type='whatsapp' LIMIT 1",
                               (CHANNEL, json.dumps(event, ensure_ascii=False), cid, chat))
                exists = cursor.fetchone() is not None
            if not exists:
                limiter.cancel(user_id, client_id, reservation)
                return jsonify(error='Выберите существующий личный чат WhatsApp'), 404
        except Exception:
            limiter.cancel(user_id, client_id, reservation)
            raise
        return '', 204, {'Cache-Control': 'no-store'}
