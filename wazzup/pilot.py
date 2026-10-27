"""Message sending and event-driven refresh for the chat processing mode.

Who may process chats is decided in wazzup/access.py: verifiers after the
QR + Telegram-code confirmation, super admins without it. The monolith passes
that decision in as `access`; without it only super admins are admitted.

The outbox claims a client UUID before contacting Wazzup. Ambiguous outcomes
are never automatically retried: the vendor deduplicates for only 60 seconds.
"""
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, Response, g, jsonify, request
from requests.adapters import HTTPAdapter

from . import access as access_rules
from . import accounts, new_chat, realtime, shift
from .window import last_inbound_at
from .workspace_store import WorkspaceStore

EXCLUDED_CHANNELS = frozenset({
    '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',  # Global Taxi
    'a4bccb5e-5d41-483d-b7c1-a1079685577d',  # old Global
})
MAX_TEXT = 4096
# Every stream holds a Waitress thread for its whole lifetime, and the bell and
# the shift auction draw on the same ~96 threads. Sized for one verifier shift
# (nine people at most in the schedule) plus the super admins.
STREAM_LIMIT = 24
# api.wazzup24.com closes an idle connection after 65–90 s (measured 09.10.2026).
WAZZUP_IDLE_REUSE_SECONDS = 45
WAZZUP_API_ORIGIN = 'https://api.wazzup24.com'


class _OneSlotPoolFilter(logging.Filter):
    """Concurrent sends open a second connection that the one-slot pool then
    drops on purpose (see wazzup_session); that is not worth a warning."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        return not (message.startswith('Connection pool is full') and 'api.wazzup24.com' in message)


logging.getLogger('urllib3.connectionpool').addFilter(_OneSlotPoolFilter())


def wazzup_session():
    """At most ONE idle connection is kept: its idle time is the pool's idle
    time, so the reuse window below covers every reused connection, not only
    the most recently used one (urllib3 hands pooled connections out LIFO)."""
    session = requests.Session()
    session.mount(WAZZUP_API_ORIGIN, HTTPAdapter(pool_connections=1, pool_maxsize=1))
    return session


class KeepAliveTransport:
    """Sends to api.wazzup24.com over a reused HTTPS connection pool.

    A fresh connection pays TCP + TLS round trips before every send. A pool idle
    longer than idle_seconds is replaced instead of reused: a request must never
    be written into a connection the vendor is already closing, which would turn
    an ordinary send into an ambiguous one. The replaced pool is not closed under
    a request still running on another thread; it goes when that request ends."""

    def __init__(self, idle_seconds=WAZZUP_IDLE_REUSE_SECONDS, factory=wazzup_session):
        self._lock = threading.Lock()
        self._idle = idle_seconds
        self._factory = factory
        self._session = None
        self._last_used = 0.0

    def post(self, url, **kwargs):
        with self._lock:
            now = time.monotonic()
            if self._session is None or now - self._last_used > self._idle:
                self._session = self._factory()
            session = self._session
            self._last_used = now
        return session.post(url, **kwargs)


wazzup_transport = KeepAliveTransport()


def _server_timing(name, started):
    # Read by the monolith's after_request into the Server-Timing header.
    try:
        timings = getattr(g, 'server_timings', None)
        if timings is None:
            timings = g.server_timings = []
        timings.append((name, (time.perf_counter() - started) * 1000))
    except RuntimeError:
        pass


def eligible_user(user):
    # The rule without an injected access policy: super admins only.
    # is_active (index 10) is the operator's on-shift flag, not account access;
    # an admin need not be on an operator shift.
    return bool(user and len(user) > 11
                and access_rules.processes_without_gate(user[3], user[11]))


MESSAGE_FIELDS = ('messageId', 'dt', 'isEcho', 'type', 'text', 'contentUri',
                  'authorName', 'authorId', 'status', 'isEdited', 'isDeleted',
                  'wazzupDt', 'replyToMessageId', 'replyText', 'replyAuthorName',
                  # The sending browser's id of an iCORE send: lets the sender's own
                  # optimistic bubble meet the archive row before the POST returns.
                  'clientMessageId')


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if isinstance(value, uuid.UUID) else value


def message_item(row):
    return dict(zip(MESSAGE_FIELDS, [json_value(v) for v in row]))


MESSAGE_SELECT = """
    SELECT m.message_id,m.dt,m.is_echo,m.type,m.text,m.content_uri,
           COALESCE(o.author_name,m.author_name),m.author_id,m.status,
           m.is_edited,m.is_deleted,m.wazzup_dt,o.reply_to_message_id,
           CASE WHEN r.is_deleted THEN NULL ELSE r.text END,r.author_name,o.request_id
      FROM wazzup_messages m
      LEFT JOIN wazzup_pilot_outbox o ON o.message_id=m.message_id AND o.account=m.account
      LEFT JOIN wazzup_messages r ON r.message_id=o.reply_to_message_id AND r.account=m.account
          AND r.channel_id=m.channel_id AND r.chat_id=m.chat_id
"""


def resume_point(args, headers):
    """(epoch, after) where a reconnecting stream continues.

    The client sends it as `Last-Event-ID: <epoch>:<seq>` so the stream URL stays
    the same on every reconnect and the browser reuses its cached CORS preflight:
    with the point in the query string each 2-minute reconnect cost an OPTIONS
    too (27.10.2026). The query pair is still read for tabs opened before that."""
    value = (headers.get('Last-Event-ID') or '').strip()
    if value:
        epoch, _, after = value.rpartition(':')
        return epoch or None, after or None
    return args.get('epoch'), args.get('after')


def build_pilot_blueprint(*, db, require_api_key, guard, channels, preflight,
                          listen_connect=None, transport=None, event_broker=None, gcs=None,
                          access=None, stream_limit=STREAM_LIMIT, driver_card_sync=None,
                          shift_status=None):
    """access() -> {'mode', 'can_process', ...} for the requesting user (the
    monolith's _wazzup_chat_access). It is the single rule shared with the
    section guard and the shift routes; None keeps the standalone rule above.

    driver_card_sync — {'wake', 'options', 'driver_info'} for the driver card routes;
    tests pass stubs, the monolith leaves the defaults (wazzup/driver_cards_sync.py,
    wazzup/driver_info.py).

    shift_status(user_id) -> the verifier's latest status event (the same row the
    shift routes read); tests pass a stub, the monolith leaves the store."""
    bp = Blueprint('wazzup_pilot', __name__, url_prefix='/api/wazzup/pilot')
    driver_card_sync = driver_card_sync or {}
    transport = transport or wazzup_transport
    broker = event_broker or realtime.broker
    shift_store = WorkspaceStore(db)

    def may_process(user):
        if access is None:
            return eligible_user(user)
        return bool(user) and bool(access().get('can_process'))

    def channel_scope():
        """Channels open to the requesting person: the «Поток» group gets
        +77070253322 only (wazzup/access.py); None means every channel."""
        return access().get('channels') if access is not None else None

    class ScopedExclusions:
        """Global plus, for a channel-scoped person, every channel outside the
        scope. The route modules only ask `channel in excluded_channels`, so a
        channel the person may not open is refused exactly like Global."""

        def __contains__(self, channel_id):
            if channel_id in EXCLUDED_CHANNELS:
                return True
            scope = channel_scope()
            return scope is not None and channel_id not in scope

    exclusions = ScopedExclusions()

    def latest_shift_status(user_id):
        if shift_status is not None:
            return shift_status(user_id)
        # Status events are stored in naive Almaty time, like the shift routes read them.
        return shift_store.latest_status(user_id, datetime.now(ZoneInfo('Asia/Almaty')).replace(tzinfo=None))

    def write_refusal(user):
        """A verifier writes to clients only while «Активный» (owner, 19.10.2026).
        Nobody else has a shift, so the rule does not apply to them."""
        if access is None or access().get('mode') != access_rules.MODE_OPERATOR:
            return None
        return None if shift.writes_allowed(latest_shift_status(user[0])) else shift.WRITE_LOCKED_MESSAGE

    def sender_author_id(user):
        # Only an operator's reply (a verifier or the «Поток» group) is credited
        # in the reports; a super admin and anyone writing from the CRM window
        # without a role of their own here stay out of them, as before.
        if access is not None and access().get('mode') in access_rules.CREDITED_MODES:
            return access_rules.icore_author_id(user[0])
        return None

    def actor():
        user_id, error = guard()
        if error:
            return None, error
        user = db.get_user(id=user_id)
        if not may_process(user):
            return None, (jsonify(error='Обработка чатов вам не открыта'), 403)
        return user, None

    from .unread import register_unread_routes
    register_unread_routes(bp, actor, require_api_key, preflight, db, exclusions,
                           channel_scope=channel_scope)
    from .templates import register_template_routes, validate_template_message, render_template_preview
    register_template_routes(bp, actor, require_api_key, preflight, db,
                             excluded_channels=exclusions)
    from .assist import register_assist_routes
    register_assist_routes(bp, actor, require_api_key, preflight)
    from .attachments import register_attachment_routes
    register_attachment_routes(bp, actor, require_api_key, preflight, db, exclusions)
    from .notes import register_note_routes
    register_note_routes(bp, actor, require_api_key, preflight, db, exclusions)
    from .driver_cards import register_driver_card_routes
    from . import driver_cards_sync
    # The driver card writes the verifiers' amoCRM deal; the «Поток» group and the CRM
    # window work in their own CRM.
    register_driver_card_routes(
        bp, guard=guard, actor=actor, require_api_key=require_api_key, preflight=preflight, db=db,
        may_edit=lambda user_id: (may_process(db.get_user(id=user_id))
                                  and (access is None or access().get('mode') not in (
                                      access_rules.MODE_POTOK, access_rules.MODE_CRM))),
        excluded_channels=exclusions,
        on_saved=driver_card_sync.get('wake', driver_cards_sync.wake),
        options_provider=driver_card_sync.get('options', lambda: driver_cards_sync.current_options(db)),
        driver_info_provider=driver_card_sync.get('driver_info'))
    from .typing import register_typing_routes
    register_typing_routes(bp, actor, require_api_key, preflight, db, exclusions)
    # The CRM user behind the window's iCORE account: who wrote, for the CRM (crm_identity.py).
    # A free pair is taken only behind the QR: an operator, or anyone in a CRM window session
    # signed in by password (mode «crm»); others are paired by name when the CRM opens the
    # window (embed_auth.py).
    from .crm_identity import register_crm_identity_route
    register_crm_identity_route(bp, actor, require_api_key, preflight, db,
                                may_bind=lambda: access is None or access().get('mode') in access_rules.GATED_MODES)
    from .send_check import register_send_check_route
    register_send_check_route(bp, actor, require_api_key, preflight, db, exclusions)
    from .uploads import (register_upload_routes, resolve_upload, require_storage, issue_link,
                          link_origin, UploadError)
    register_upload_routes(bp, actor, require_api_key, preflight, db, exclusions,
                           channels=channels, gcs=gcs)

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    def capabilities():
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = guard()
        if error:
            return error
        enabled = (request.args.get('account', 'op') == 'op'
                   and may_process(db.get_user(id=user_id)))
        # Where the «+» may start a chat (wazzup/new_chat.py); the channel list is cached.
        new_chat_ids = (new_chat.new_chat_channel_ids(channels('op'), EXCLUDED_CHANNELS,
                                                      allowed=channel_scope())
                        if enabled else [])
        return jsonify(enabled=enabled, canSend=enabled and bool(accounts.api_key('op')),
                       excludedChannelIds=sorted(EXCLUDED_CHANNELS), maxTextLength=MAX_TEXT,
                       newChatChannelIds=new_chat_ids)

    @bp.route('/stream', methods=['GET', 'OPTIONS'])
    @require_api_key
    def stream():
        if request.method == 'OPTIONS':
            return preflight()
        _, error = actor()
        if error:
            return error
        if request.args.get('account', 'op') != 'op':
            return jsonify(error='Обработка чатов работает только в аккаунте Верификаторов'), 403
        if listen_connect is None:
            return jsonify(error='Живые обновления недоступны'), 503
        if not broker.acquire(stream_limit):
            return jsonify(error='Достигнут лимит живых подключений'), 503, {'Retry-After': '5'}
        try:
            if event_broker is None:
                realtime.ensure_listener(listen_connect)
        except Exception:
            broker.release()
            raise
        # A reconnect continues after the last frame its client saw (`id:`), so
        # nothing published during the gap is lost and no reconciliation is needed.
        epoch, after = resume_point(request.args, request.headers)
        # The portal sidebar badge (WazzupWaitingKeeper.jsx) needs the waiting
        # counter only: no messages, presence or cards. A skipped batch still
        # sends its `id:` (an empty `cursor` frame the client ignores), so a
        # reconnect resumes instead of reconciling.
        only_unread = request.args.get('only') == 'unread'
        # Read inside the request: the generator runs outside its context. Every
        # chat event carries its channel; other channels never reach a scoped person.
        scope = channel_scope()

        def generate():
            cursor, resumed = broker.resume_from(epoch, after)
            started = time.monotonic()
            yield 'event: connected\ndata: ' + json.dumps({
                'ready': broker.ready, 'epoch': broker.epoch, 'seq': cursor, 'resumed': resumed}) + '\n\n'
            # Re-authenticate on reconnect; revoked access cannot hold a stream forever.
            while time.monotonic() - started < 120:
                events, cursor = broker.wait(cursor)
                if scope is not None and events:
                    events = [e for e in events if e.get('unavailable') or e.get('reload')
                              or e.get('channelId') in scope]
                    if not events:
                        # Only other channels changed: still move the resume point.
                        yield f'id: {cursor}\nevent: cursor\ndata: {{}}\n\n'
                        continue
                if any(e.get('unavailable') for e in events):
                    yield f'id: {cursor}\nevent: unavailable\ndata: {{}}\n\n'
                elif any(e.get('reload') for e in events):
                    yield f'id: {cursor}\nevent: reload\ndata: ' + json.dumps({'ready': broker.ready}) + '\n\n'
                elif events and only_unread:
                    unread = [e for e in events if e.get('kind') == 'unread']
                    yield ((f'id: {cursor}\nevent: change\ndata: '
                            + json.dumps({'changes': unread}, ensure_ascii=False) + '\n\n')
                           if unread else f'id: {cursor}\nevent: cursor\ndata: {{}}\n\n')
                elif events:
                    # Presence goes in its own frame: tabs opened before it existed
                    # skip unknown events, but would re-read the list and the
                    # thread for every typing pulse found among `changes`. Driver
                    # cards do the same for the same reason.
                    changes = [e for e in events if e.get('kind') not in ('typing', 'card')]
                    typing = [e for e in events if e.get('kind') == 'typing']
                    cards = [e for e in events if e.get('kind') == 'card']
                    # Raw UTF-8: Cyrillic as \uXXXX made frames ~2.5 times larger.
                    if changes:
                        yield (f'id: {cursor}\nevent: change\ndata: '
                               + json.dumps({'changes': changes}, ensure_ascii=False) + '\n\n')
                    if typing:
                        yield (f'id: {cursor}\nevent: typing\ndata: '
                               + json.dumps({'typing': typing, 'serverTime': int(time.time() * 1000)},
                                            ensure_ascii=False) + '\n\n')
                    if cards:
                        yield (f'id: {cursor}\nevent: card\ndata: '
                               + json.dumps({'cards': cards}, ensure_ascii=False) + '\n\n')
                else:
                    yield ': heartbeat\n\n'

        response = Response(generate(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        response.call_on_close(broker.release)
        return response

    @bp.route('/refresh', methods=['POST', 'OPTIONS'])
    @require_api_key
    def refresh():
        if request.method == 'OPTIONS':
            return preflight()
        _, error = actor()
        if error:
            return error
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('account') != 'op':
            return jsonify(error='Некорректный аккаунт'), 400
        cid, chat = body.get('channelId'), body.get('chatId')
        ids = body.get('messageIds', [])
        if (not isinstance(cid, str) or not isinstance(chat, str) or not cid or not chat
                or len(cid) > 100 or len(chat) > 100 or not isinstance(ids, list) or len(ids) > 2000
                or any(not isinstance(v, str) or len(v) > 100 for v in ids)):
            return jsonify(error='Некорректный запрос обновления'), 400
        scope = channel_scope()
        if scope is not None and cid not in scope:
            return jsonify(error='Этот канал вам не открыт'), 403
        # Refresh the whole displayed range (incl. changes, deletions and gaps
        # accumulated offline). A bounded response resets an oversized window.
        with db._get_cursor() as cur:
            cur.execute("SELECT MIN(dt) FROM wazzup_messages WHERE account='op' "
                        "AND channel_id=%s AND chat_id=%s AND message_id=ANY(%s)", (cid, chat, ids))
            since = cur.fetchone()[0]
            cur.execute(MESSAGE_SELECT + " WHERE m.account='op' AND m.channel_id=%s AND m.chat_id=%s "
                        + ("AND m.dt >= %s " if since else '')
                        + "ORDER BY m.dt DESC,m.message_id DESC LIMIT %s",
                        [cid, chat] + ([since] if since else []) + [2001 if since else 51])
            rows = cur.fetchall()
            last_inbound = last_inbound_at(cur, 'op', cid, chat)
        cap = 2000 if since else 50
        reset = len(rows) > cap
        return jsonify(items=[message_item(r) for r in reversed(rows[:cap])],
                       reset=reset, hasMore=reset, lastInboundAt=last_inbound,
                       serverTime=datetime.now(timezone.utc).isoformat())

    def replay(row, body, user):
        # account, channel, chat, text, user, state, message_id, error_code, error_message
        if tuple(row[:5]) != ('op', body['channelId'], body['chatId'], body['text'], user[0]):
            return jsonify(error='Этот идентификатор уже использован для другого сообщения',
                           code='REQUEST_CONFLICT', retryable=False), 409
        if (row[9] if len(row) > 9 else None) != body.get('replyToMessageId'):
            return jsonify(error='У этой отправки уже выбран другой ответ', code='REQUEST_CONFLICT', retryable=False), 409
        attachment_id = str(row[10]) if len(row) > 10 and row[10] is not None else None
        if attachment_id != body.get('attachmentId'):
            return jsonify(error='Для этой отправки уже выбрано другое вложение',
                           code='REQUEST_CONFLICT', retryable=False), 409
        state = row[5]
        if state == 'sent':
            return jsonify(status='success', state=state, messageId=row[6],
                           clientMessageId=body['clientMessageId']), 200
        return jsonify(error=row[8] or 'Отправка уже начата. Проверьте появление сообщения в чате.',
                       code=row[7] or 'SEND_IN_PROGRESS', state=state, retryable=False,
                       clientMessageId=body['clientMessageId']), 409

    @bp.route('/send', methods=['POST', 'OPTIONS'])
    @require_api_key
    def send():
        if request.method == 'OPTIONS':
            return preflight()
        stage = time.perf_counter()
        user, error = actor()
        if error:
            return error
        _server_timing('wz-access', stage)
        stage = time.perf_counter()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error='Некорректный запрос'), 400
        if body.get('account') != 'op':
            return jsonify(error='Обработка чатов работает только в аккаунте Верификаторов'), 403
        cid, chat = body.get('channelId'), body.get('chatId')
        attachment_id = body.get('attachmentId')
        text = body.get('text', '' if attachment_id is not None else None)
        if attachment_id is not None:
            try:
                attachment_id = str(uuid.UUID(str(attachment_id)))
            except (ValueError, TypeError, AttributeError):
                return jsonify(error='Некорректный идентификатор вложения'), 400
            if text != '':
                return jsonify(error='Файл и текст отправляются отдельными сообщениями.',
                               code='ATTACHMENT_WITH_TEXT'), 400
        body['text'], body['attachmentId'] = text, attachment_id
        reply_to = body.get('replyToMessageId')
        if reply_to is not None and (not isinstance(reply_to, str) or len(reply_to) > 200 or not reply_to.strip()):
            return jsonify(error='Некорректное сообщение для ответа'), 400
        body['replyToMessageId'] = reply_to
        if (not isinstance(cid, str) or not isinstance(chat, str) or not isinstance(text, str)
                or (not attachment_id and not text.strip()) or len(text) > MAX_TEXT
                or not chat.strip() or len(chat) > 100):
            return jsonify(error='Введите сообщение длиной до 4096 символов'), 400
        try:
            uuid.UUID(cid)
            request_id = str(uuid.UUID(str(body.get('clientMessageId', ''))))
        except (ValueError, TypeError, AttributeError):
            return jsonify(error='Некорректный идентификатор сообщения или канала'), 400
        body['clientMessageId'] = request_id
        if cid in EXCLUDED_CHANNELS:
            return jsonify(error='Global обрабатывается отдельно и исключён из отправки'), 403
        scope = channel_scope()
        if scope is not None and cid not in scope:
            return jsonify(error='Этот канал вам не открыт', code='CHANNEL_NOT_ALLOWED',
                           state='failed', retryable=False), 403
        key = accounts.api_key('op')
        if not key:
            # Nothing was attempted: a definite failure, not a lost answer to re-check.
            return jsonify(error='Ключ отправки не настроен', code='SEND_KEY_MISSING', state='failed',
                           retryable=False), 503
        # Replay before channel checks, even if it was subsequently disconnected.
        with db._get_cursor() as cur:
            cur.execute("SELECT account,channel_id,chat_id,text,user_id,state,message_id,error_code,error_message,reply_to_message_id,attachment_id "
                        "FROM wazzup_pilot_outbox WHERE request_id=%s", (request_id,))
            previous = cur.fetchone()
        if previous:
            return replay(previous, body, user)
        # After the replay: a message accepted before the break must still read as sent.
        refusal = write_refusal(user)
        if refusal:
            return jsonify(error=refusal, code='SHIFT_NOT_ACTIVE', state='failed', retryable=False,
                           clientMessageId=request_id), 403
        channel = next((c for c in channels('op') if c.get('channelId') == cid), None)
        if not channel or channel.get('state') != 'active' or channel.get('transport') not in ('whatsapp', 'wapi'):
            return jsonify(error='Отправка доступна только в активных WhatsApp-каналах'), 400
        template_error = (validate_template_message('op', cid, text, excluded_channels=EXCLUDED_CHANNELS)
                          if not attachment_id else None)
        if template_error:
            return jsonify(error=template_error), 400
        display_text = render_template_preview('op', cid, text) if not attachment_id else ''
        starting = False
        with db._get_cursor() as cur:
            cur.execute("SELECT chat_type FROM wazzup_chats WHERE account='op' AND channel_id=%s AND chat_id=%s",
                        (cid, chat))
            known = cur.fetchone()
            if not known and body.get('newChat') is True:
                # The «+»: the first message is what creates the chat (wazzup/new_chat.py).
                if not new_chat.may_start_chat(channel, EXCLUDED_CHANNELS):
                    return jsonify(error='В этом канале нельзя начинать переписку первыми',
                                   code='NEW_CHAT_CHANNEL'), 403
                if new_chat.normalize_phone(chat) != chat:
                    return jsonify(error='Укажите номер в международном формате, например 77001234567',
                                   code='NEW_CHAT_PHONE'), 400
                first_error = new_chat.first_message_error(channel, text, attachment_id, reply_to)
                if first_error:
                    return jsonify(error=first_error, code='NEW_CHAT_FIRST_MESSAGE'), 400
                starting = True
            elif not known or known[0] != 'whatsapp':
                return jsonify(error='Выберите существующий личный чат WhatsApp'), 400
            if reply_to:
                cur.execute("SELECT 1 FROM wazzup_messages WHERE account='op' AND channel_id=%s AND chat_id=%s "
                            "AND message_id=%s AND NOT is_deleted", (cid, chat, reply_to))
                if not cur.fetchone():
                    return jsonify(error='Сообщение для ответа не найдено в этом чате'), 400
        attachment = content_uri = None
        if attachment_id:
            try:
                attachment = resolve_upload(db, user[0], cid, chat, attachment_id)
                require_storage(gcs)
            except UploadError as error:
                # No outbox attempt or vendor POST exists at this point.
                return jsonify(error=error.message, code=error.code, state='failed', retryable=False), error.status
        author_id = sender_author_id(user)
        with db._get_cursor() as cur:
            cur.execute("""INSERT INTO wazzup_pilot_outbox
                (request_id,account,channel_id,chat_id,chat_type,text,user_id,author_name,reply_to_message_id,attachment_id,author_id)
                VALUES (%s,'op',%s,%s,'whatsapp',%s,%s,%s,%s,%s,%s)
                ON CONFLICT(request_id) DO NOTHING RETURNING request_id""",
                (request_id, cid, chat, text, user[0], user[2], reply_to, attachment_id, author_id))
            claimed = cur.fetchone()
            if not claimed:
                cur.execute("SELECT account,channel_id,chat_id,text,user_id,state,message_id,error_code,error_message,reply_to_message_id,attachment_id "
                            "FROM wazzup_pilot_outbox WHERE request_id=%s", (request_id,))
                return replay(cur.fetchone(), body, user)
            if attachment:
                # Wazzup downloads the file from this link while answering the POST.
                content_uri = issue_link(cur, attachment, link_origin() + bp.url_prefix)
            if author_id:
                # The vendor echoes API sends as "Admin" with no author id, so the
                # reports credit nobody. Our own author key is bound to the sender
                # once; a supervisor's later manual choice in the mapping wins.
                cur.execute("""INSERT INTO wazzup_operator_map
                    (author_id,author_name,user_id,is_bot,updated_by,updated_at,account)
                    VALUES (%s,%s,%s,FALSE,%s,now(),'op') ON CONFLICT(author_id) DO NOTHING""",
                    (author_id, user[2], user[0], user[0]))
        # Transaction committed before network I/O. Never retry an ambiguous POST.
        _server_timing('wz-checks', stage)
        stage = time.perf_counter()
        started = time.monotonic()
        state, code, explanation, message_id, detail = 'unknown', 'SEND_UNKNOWN', '', None, ''
        try:
            response = transport.post(WAZZUP_API_ORIGIN + '/v3/message',
                headers={'Authorization': 'Bearer ' + key}, timeout=(5, 20),
                json={'channelId': cid, 'chatId': chat, 'chatType': 'whatsapp',
                      **({'contentUri': content_uri} if attachment else {'text': text}),
                      'crmMessageId': request_id, 'clearUnanswered': True,
                      **({'refMessageId': reply_to} if reply_to else {})})
            try:
                data = response.json()
            except ValueError:
                data = {}
            if 200 <= response.status_code < 300 and isinstance(data, dict) and data.get('messageId'):
                message_id, state, code = str(data['messageId']), 'sent', None
            elif 400 <= response.status_code < 500:
                code = str(data.get('error') or 'SEND_REJECTED') if isinstance(data, dict) else 'SEND_REJECTED'
                # The code alone hid the cause for two weeks («Bad response from content store»).
                # Links are masked: a description may repeat the request.
                detail = (re.sub(r'https?://\S+', '<link>', str(data.get('description') or ''))[:200]
                          if isinstance(data, dict) else '')
                if 'REPEATED' not in code.upper() and response.status_code not in (408,):
                    state = 'failed'
                explanation = 'Wazzup отклонил отправку (' + code[:100] + ').'
                if '24' in code or 'WINDOW' in code.upper() or 'TEMPLATE' in code.upper():
                    explanation = 'Возможно, закрыто 24-часовое окно WABA. Выберите одобренный шаблон Wazzup через / или +.'
        except requests.RequestException as error:
            code = 'SEND_TIMEOUT' if isinstance(error, requests.Timeout) else 'SEND_NETWORK_ERROR'
            # Exception strings may contain request URLs or credentials. Keep
            # only the type and our request UUID for incident correlation.
            logging.warning('Wazzup pilot transport request_id=%s error_type=%s',
                            request_id, type(error).__name__)
        if state == 'unknown':
            explanation = 'Подтверждение не получено. Сообщение могло уйти: проверьте чат перед новой отправкой.'
        _server_timing('wz-post', stage)
        stage = time.perf_counter()
        with db._get_cursor() as cur:
            cur.execute("""UPDATE wazzup_pilot_outbox SET state=%s,message_id=%s,
                error_code=%s,error_message=%s,updated_at=now() WHERE request_id=%s""",
                (state, message_id, code, explanation, request_id))
        logging.info('Wazzup pilot send state=%s duration_ms=%d request_id=%s code=%s detail=%s', state,
                     round((time.monotonic() - started) * 1000), request_id, code, detail or '-')
        if state != 'sent':
            return jsonify(error=explanation, code=code, state=state, retryable=False,
                           clientMessageId=request_id), 422 if state == 'failed' else 409
        # Echo may have arrived already. Never overwrite its confirmed status/date.
        try:
            with db._get_cursor() as cur:
                cur.execute("SELECT 1 FROM wazzup_messages WHERE message_id=%s", (message_id,))
                exists = cur.fetchone()
            if not exists:
                db.store_wazzup_messages([{'messageId': message_id, 'channelId': cid,
                    'chatId': chat, 'chatType': 'whatsapp', 'dateTime': datetime.now(timezone.utc).isoformat(),
                    'isEcho': True, 'type': attachment['message_type'] if attachment else 'text',
                    'text': attachment['original_name'] if attachment else display_text,
                    'status': 'pending',
                    # A chat started with the «+» takes the name typed in its form.
                    **({'contact': {'name': new_chat.clean_contact_name(body.get('contactName')),
                                    'phone': chat}} if starting else {}),
                    **({'authorId': author_id, 'authorName': user[2]} if author_id else {})}],
                    account='op')
            elif author_id:
                # The echo was archived before the outbox learned its message id:
                # the author trigger had nothing to match then, so stamp it now.
                with db._get_cursor() as cur:
                    cur.execute("UPDATE wazzup_messages SET author_id=%s,author_name=%s "
                                "WHERE account='op' AND message_id=%s", (author_id, user[2], message_id))
        except Exception:
            # Acceptance is durable in the outbox; echo will repair the archive.
            logging.exception('Wazzup pilot accepted message awaits webhook persistence')
        _server_timing('wz-store', stage)
        return jsonify(status='success', state='sent', messageId=message_id,
                       clientMessageId=request_id), 201

    return bp
