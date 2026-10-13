"""Read-only send checks against Wazzup confirmations already received.

Public API v3 has no lookup by crmMessageId and its webhook does not return
that ID. Text/time matches are therefore evidence for the operator, NEVER an
identity proof: two people can send the same greeting in the same chat.
"""
from datetime import datetime, timezone
import uuid

from flask import jsonify, request


def inspect_send(cur, request_id, user_id, channel_id, chat_id):
    cur.execute("""SELECT state,message_id,error_message,text,attachment_id,created_at
        FROM wazzup_pilot_outbox WHERE request_id=%s AND account='op'
        AND user_id=%s AND channel_id=%s AND chat_id=%s""",
        (request_id, user_id, channel_id, chat_id))
    row = cur.fetchone()
    if not row:
        # The original POST may never have reached us. A check must not create
        # an outbox row or send the saved draft as the old /send replay could.
        return dict(state='unknown', code='SEND_NOT_FOUND', candidates=[],
                    error='Запись об отправке не найдена. Доставка не подтверждена. '
                          'Проверьте чат в Wazzup перед новой отправкой.')
    state, message_id, error, text, attachment_id, created_at = row
    if message_id:
        cur.execute("""SELECT status FROM wazzup_messages
            WHERE account='op' AND channel_id=%s AND chat_id=%s
            AND message_id=%s AND is_echo""", (channel_id, chat_id, message_id))
        message = cur.fetchone()
        # Receipts can precede the echo. They are safe only with the exact ID
        # the provider returned for this outbox entry.
        cur.execute("""SELECT status FROM wazzup_status_receipts
            WHERE account='op' AND message_id=%s""", (message_id,))
        receipt = cur.fetchone()
        rank = {'pending': 0, 'sent': 1, 'error': 2, 'delivered': 3, 'read': 4}
        statuses = [r[0] for r in (message, receipt) if r and r[0] in rank]
        delivery = max(statuses, key=rank.get) if statuses else 'pending'
        # "sent" here means accepted by Wazzup; deliveryStatus describes the
        # actual delivery. Even a delivery error keeps its existing identity.
        return dict(state='sent', messageId=message_id, deliveryStatus=delivery,
                    candidates=[], error='')
    if state == 'failed':
        return dict(state='failed', candidates=[], error=error or 'Wazzup отклонил отправку.')
    candidates = []
    if text and not attachment_id and not text.startswith('@template:'):
        cur.execute("""SELECT message_id,dt,status FROM wazzup_messages
            WHERE account='op' AND channel_id=%s AND chat_id=%s AND is_echo
            AND NOT is_deleted AND text=%s
            AND dt BETWEEN %s - interval '5 seconds' AND %s + interval '2 minutes'
            ORDER BY dt,message_id LIMIT 4""", (channel_id, chat_id, text, created_at, created_at))
        candidates = [dict(messageId=mid, dt=dt.isoformat(), status=status)
                      for mid, dt, status in cur.fetchall()]
    if candidates:
        explanation = ('В переписке есть сообщение с таким текстом. Оно может относиться к другой '
                       'отправке — откройте его и проверьте. Эта отправка пока не подтверждена.')
    elif state == 'sending' and (datetime.now(timezone.utc) - created_at).total_seconds() < 45:
        explanation = 'Wazzup ещё обрабатывает отправку. Проверьте статус чуть позже.'
    else:
        explanation = ('Проверено: подтверждения этой отправки пока нет. Это не доказывает, '
                       'что сообщение не дошло. Проверьте чат в Wazzup перед новой отправкой.')
    return dict(state='unknown', code='SEND_UNCONFIRMED', candidates=candidates, error=explanation)


def register_send_check_route(bp, actor, require_api_key, preflight, db, excluded_channels):
    @bp.route('/send/check', methods=['POST', 'OPTIONS'])
    @require_api_key
    def check_send():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = actor()
        if error:
            return error
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('account') != 'op':
            return jsonify(error='Некорректный аккаунт'), 400
        cid, chat = body.get('channelId'), body.get('chatId')
        try:
            uuid.UUID(cid)
            request_id = str(uuid.UUID(str(body.get('clientMessageId', ''))))
        except (ValueError, TypeError, AttributeError):
            return jsonify(error='Некорректный идентификатор сообщения или канала'), 400
        if not isinstance(chat, str) or not chat.strip() or len(chat) > 100:
            return jsonify(error='Некорректный чат'), 400
        if cid in excluded_channels:
            return jsonify(error='Global обрабатывается отдельно'), 403
        with db._get_cursor() as cur:
            result = inspect_send(cur, request_id, user[0], cid, chat)
        return jsonify(**result, clientMessageId=request_id,
                       checkedAt=datetime.now(timezone.utc).isoformat())
