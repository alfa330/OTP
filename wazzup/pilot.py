"""Text sending and event-driven refresh, explicitly restricted to alfa330.

The outbox claims a client UUID before contacting Wazzup. Ambiguous outcomes
are never automatically retried: the vendor deduplicates for only 60 seconds.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Blueprint, Response, jsonify, request

from . import accounts, realtime

EXCLUDED_CHANNELS = frozenset({
    '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',  # Global Taxi
    'a4bccb5e-5d41-483d-b7c1-a1079685577d',  # old Global
})
MAX_TEXT = 4096
STREAM_LIMIT = 8


def eligible_user(user):
    # is_active (index 10) is the operator's on-shift flag, not account access.
    # Match the session principal guard; an admin need not be on an operator shift.
    return bool(user and len(user) > 11
                and str(user[7] or '').strip().lower() == 'alfa330'
                and str(user[11] or '').strip().lower() not in ('fired', 'dismissal'))


def message_item(row):
    return dict(zip(('messageId', 'dt', 'isEcho', 'type', 'text', 'contentUri',
                     'authorName', 'authorId', 'status', 'isEdited', 'isDeleted',
                     'wazzupDt', 'replyToMessageId', 'replyText', 'replyAuthorName'),
                    [v.isoformat() if isinstance(v, datetime) else v for v in row]))


MESSAGE_SELECT = """
    SELECT m.message_id,m.dt,m.is_echo,m.type,m.text,m.content_uri,
           COALESCE(o.author_name,m.author_name),m.author_id,m.status,
           m.is_edited,m.is_deleted,m.wazzup_dt,o.reply_to_message_id,
           CASE WHEN r.is_deleted THEN NULL ELSE r.text END,r.author_name
      FROM wazzup_messages m
      LEFT JOIN wazzup_pilot_outbox o ON o.message_id=m.message_id AND o.account=m.account
      LEFT JOIN wazzup_messages r ON r.message_id=o.reply_to_message_id AND r.account=m.account
          AND r.channel_id=m.channel_id AND r.chat_id=m.chat_id
"""


def build_pilot_blueprint(*, db, require_api_key, guard, channels, preflight,
                          listen_connect=None, transport=None, event_broker=None):
    bp = Blueprint('wazzup_pilot', __name__, url_prefix='/api/wazzup/pilot')
    transport = transport or requests
    broker = event_broker or realtime.broker

    def actor():
        user_id, error = guard()
        if error:
            return None, error
        user = db.get_user(id=user_id)
        if not eligible_user(user):
            return None, (jsonify(error='Пилот доступен только alfa330'), 403)
        return user, None

    from .unread import register_unread_routes
    register_unread_routes(bp, actor, require_api_key, preflight, db, EXCLUDED_CHANNELS)
    from .templates import register_template_routes, validate_template_message, render_template_preview
    register_template_routes(bp, actor, require_api_key, preflight, db,
                             excluded_channels=EXCLUDED_CHANNELS)
    from .assist import register_assist_routes
    register_assist_routes(bp, actor, require_api_key, preflight)
    from .attachments import register_attachment_routes
    register_attachment_routes(bp, actor, require_api_key, preflight, db, EXCLUDED_CHANNELS)

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    def capabilities():
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = guard()
        if error:
            return error
        enabled = (request.args.get('account', 'op') == 'op'
                   and eligible_user(db.get_user(id=user_id)))
        return jsonify(enabled=enabled, canSend=enabled and bool(accounts.api_key('op')),
                       excludedChannelIds=sorted(EXCLUDED_CHANNELS), maxTextLength=MAX_TEXT)

    @bp.route('/stream', methods=['GET', 'OPTIONS'])
    @require_api_key
    def stream():
        if request.method == 'OPTIONS':
            return preflight()
        _, error = actor()
        if error:
            return error
        if request.args.get('account', 'op') != 'op':
            return jsonify(error='Пилот работает только в аккаунте Верификаторов'), 403
        if listen_connect is None:
            return jsonify(error='Живые обновления недоступны'), 503
        if not broker.acquire(STREAM_LIMIT):
            return jsonify(error='Достигнут лимит живых подключений'), 503, {'Retry-After': '5'}
        try:
            if event_broker is None:
                realtime.ensure_listener(listen_connect)
        except Exception:
            broker.release()
            raise

        def generate():
            cursor = broker.current_seq()
            started = time.monotonic()
            yield 'event: connected\ndata: ' + json.dumps({'ready': broker.ready}) + '\n\n'
            # Re-authenticate on reconnect; revoked access cannot hold a stream forever.
            while time.monotonic() - started < 120:
                events, cursor = broker.wait(cursor)
                if any(e.get('unavailable') for e in events):
                    yield 'event: unavailable\ndata: {}\n\n'
                elif any(e.get('reload') for e in events):
                    yield 'event: reload\ndata: ' + json.dumps({'ready': broker.ready}) + '\n\n'
                elif events:
                    yield 'event: change\ndata: ' + json.dumps({'changes': events}) + '\n\n'
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
        cap = 2000 if since else 50
        reset = len(rows) > cap
        return jsonify(items=[message_item(r) for r in reversed(rows[:cap])],
                       reset=reset, hasMore=reset, serverTime=datetime.now(timezone.utc).isoformat())

    def replay(row, body, user):
        # account, channel, chat, text, user, state, message_id, error_code, error_message
        if tuple(row[:5]) != ('op', body['channelId'], body['chatId'], body['text'], user[0]):
            return jsonify(error='Этот идентификатор уже использован для другого сообщения',
                           code='REQUEST_CONFLICT', retryable=False), 409
        if (row[9] if len(row) > 9 else None) != body.get('replyToMessageId'):
            return jsonify(error='У этой отправки уже выбран другой ответ', code='REQUEST_CONFLICT', retryable=False), 409
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
        user, error = actor()
        if error:
            return error
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error='Некорректный запрос'), 400
        if body.get('account') != 'op':
            return jsonify(error='Пилот работает только в аккаунте Верификаторов'), 403
        cid, chat, text = body.get('channelId'), body.get('chatId'), body.get('text')
        reply_to = body.get('replyToMessageId')
        if reply_to is not None and (not isinstance(reply_to, str) or len(reply_to) > 200 or not reply_to.strip()):
            return jsonify(error='Некорректное сообщение для ответа'), 400
        body['replyToMessageId'] = reply_to
        if (not isinstance(cid, str) or not isinstance(chat, str) or not isinstance(text, str)
                or not text.strip() or len(text) > MAX_TEXT or not chat.strip() or len(chat) > 100):
            return jsonify(error='Введите сообщение длиной до 4096 символов'), 400
        try:
            uuid.UUID(cid)
            request_id = str(uuid.UUID(str(body.get('clientMessageId', ''))))
        except (ValueError, TypeError, AttributeError):
            return jsonify(error='Некорректный идентификатор сообщения или канала'), 400
        body['clientMessageId'] = request_id
        if cid in EXCLUDED_CHANNELS:
            return jsonify(error='Global обрабатывается отдельно и исключён из отправки'), 403
        key = accounts.api_key('op')
        if not key:
            return jsonify(error='Ключ отправки не настроен'), 503
        # Replay before channel checks, even if it was subsequently disconnected.
        with db._get_cursor() as cur:
            cur.execute("SELECT account,channel_id,chat_id,text,user_id,state,message_id,error_code,error_message,reply_to_message_id "
                        "FROM wazzup_pilot_outbox WHERE request_id=%s", (request_id,))
            previous = cur.fetchone()
        if previous:
            return replay(previous, body, user)
        channel = next((c for c in channels('op') if c.get('channelId') == cid), None)
        if not channel or channel.get('state') != 'active' or channel.get('transport') not in ('whatsapp', 'wapi'):
            return jsonify(error='Отправка доступна только в активных WhatsApp-каналах'), 400
        template_error = validate_template_message('op', cid, text, excluded_channels=EXCLUDED_CHANNELS)
        if template_error:
            return jsonify(error=template_error), 400
        display_text = render_template_preview('op', cid, text)
        with db._get_cursor() as cur:
            cur.execute("SELECT chat_type FROM wazzup_chats WHERE account='op' AND channel_id=%s AND chat_id=%s",
                        (cid, chat))
            known = cur.fetchone()
            if not known or known[0] != 'whatsapp':
                return jsonify(error='Выберите существующий личный чат WhatsApp'), 400
            if reply_to:
                cur.execute("SELECT 1 FROM wazzup_messages WHERE account='op' AND channel_id=%s AND chat_id=%s "
                            "AND message_id=%s AND NOT is_deleted", (cid, chat, reply_to))
                if not cur.fetchone():
                    return jsonify(error='Сообщение для ответа не найдено в этом чате'), 400
            cur.execute("""INSERT INTO wazzup_pilot_outbox
                (request_id,account,channel_id,chat_id,chat_type,text,user_id,author_name,reply_to_message_id)
                VALUES (%s,'op',%s,%s,'whatsapp',%s,%s,%s,%s)
                ON CONFLICT(request_id) DO NOTHING RETURNING request_id""",
                (request_id, cid, chat, text, user[0], user[2], reply_to))
            claimed = cur.fetchone()
            if not claimed:
                cur.execute("SELECT account,channel_id,chat_id,text,user_id,state,message_id,error_code,error_message,reply_to_message_id "
                            "FROM wazzup_pilot_outbox WHERE request_id=%s", (request_id,))
                return replay(cur.fetchone(), body, user)
        # Transaction committed before network I/O. Never retry an ambiguous POST.
        started = time.monotonic()
        state, code, explanation, message_id = 'unknown', 'SEND_UNKNOWN', '', None
        try:
            response = transport.post('https://api.wazzup24.com/v3/message',
                headers={'Authorization': 'Bearer ' + key}, timeout=(5, 20),
                json={'channelId': cid, 'chatId': chat, 'chatType': 'whatsapp',
                      'text': text, 'crmMessageId': request_id, 'clearUnanswered': True,
                      **({'refMessageId': reply_to} if reply_to else {})})
            try:
                data = response.json()
            except ValueError:
                data = {}
            if 200 <= response.status_code < 300 and isinstance(data, dict) and data.get('messageId'):
                message_id, state, code = str(data['messageId']), 'sent', None
            elif 400 <= response.status_code < 500:
                code = str(data.get('error') or 'SEND_REJECTED') if isinstance(data, dict) else 'SEND_REJECTED'
                if 'REPEATED' not in code.upper() and response.status_code not in (408,):
                    state = 'failed'
                explanation = 'Wazzup отклонил отправку (' + code[:100] + ').'
                if '24' in code or 'WINDOW' in code.upper() or 'TEMPLATE' in code.upper():
                    explanation = 'Возможно, закрыто 24-часовое окно WABA. Выберите одобренный шаблон Wazzup через / или +.'
        except requests.RequestException:
            pass
        if state == 'unknown':
            explanation = 'Подтверждение не получено. Сообщение могло уйти: проверьте чат перед новой отправкой.'
        with db._get_cursor() as cur:
            cur.execute("""UPDATE wazzup_pilot_outbox SET state=%s,message_id=%s,
                error_code=%s,error_message=%s,updated_at=now() WHERE request_id=%s""",
                (state, message_id, code, explanation, request_id))
        logging.info('Wazzup pilot send state=%s duration_ms=%d', state,
                     round((time.monotonic() - started) * 1000))
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
                    'isEcho': True, 'type': 'text', 'text': display_text, 'status': 'pending'}], account='op')
        except Exception:
            # Acceptance is durable in the outbox; echo will repair the archive.
            logging.exception('Wazzup pilot accepted message awaits webhook persistence')
        return jsonify(status='success', state='sent', messageId=message_id,
                       clientMessageId=request_id), 201

    return bp
