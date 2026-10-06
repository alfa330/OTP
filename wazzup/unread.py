"""Shared iCORE read position. Historical archive is deliberately not backfilled."""
from flask import jsonify, request


def init_unread_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_chat_read_state (
            account TEXT NOT NULL, channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            unread_count INTEGER NOT NULL DEFAULT 0 CHECK(unread_count >= 0),
            last_inbound_id TEXT NOT NULL, revision BIGINT NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(account, channel_id, chat_id)
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_unread_active
            ON wazzup_chat_read_state(account, (channel_id || ':' || chat_id)) WHERE unread_count > 0;
        CREATE OR REPLACE FUNCTION wazzup_track_unread() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.account = 'op' AND NOT NEW.is_echo AND NOT NEW.is_deleted
               AND NEW.chat_type = 'whatsapp' AND NEW.channel_id NOT IN (
                   '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2', 'a4bccb5e-5d41-483d-b7c1-a1079685577d') THEN
                INSERT INTO wazzup_chat_read_state(account,channel_id,chat_id,unread_count,last_inbound_id)
                VALUES(NEW.account,NEW.channel_id,NEW.chat_id,1,NEW.message_id)
                ON CONFLICT(account,channel_id,chat_id) DO UPDATE SET
                    unread_count=wazzup_chat_read_state.unread_count+1,
                    last_inbound_id=EXCLUDED.last_inbound_id,
                    revision=wazzup_chat_read_state.revision+1,updated_at=now();
            END IF;
            RETURN NEW;
        END $$;
        CREATE OR REPLACE FUNCTION wazzup_notify_unread() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('wazzup_pilot_events', json_build_object(
                'account',NEW.account,'channelId',NEW.channel_id,'chatId',NEW.chat_id,
                'messageId','unread:' || NEW.channel_id || ':' || NEW.chat_id,
                'kind','unread','statusOnly',TRUE,'affectsList',FALSE,
                'unreadCount',NEW.unread_count,'unreadVersion',NEW.revision,
                'lastInboundId',NEW.last_inbound_id)::text);
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS wazzup_unread_insert ON wazzup_messages;
        CREATE TRIGGER wazzup_unread_insert AFTER INSERT ON wazzup_messages
            FOR EACH ROW EXECUTE FUNCTION wazzup_track_unread();
        DROP TRIGGER IF EXISTS wazzup_unread_notify ON wazzup_chat_read_state;
        CREATE TRIGGER wazzup_unread_notify AFTER INSERT OR UPDATE ON wazzup_chat_read_state
            FOR EACH ROW EXECUTE FUNCTION wazzup_notify_unread();
    """)


def read_item(row):
    return dict(zip(('channelId', 'chatId', 'unreadCount', 'lastInboundId', 'unreadVersion'), row))


def register_unread_routes(bp, actor, require_api_key, preflight, db, excluded_channels=()):
    @bp.route('/unread', methods=['GET', 'OPTIONS'])
    @require_api_key
    def unread_snapshot():
        if request.method == 'OPTIONS':
            return preflight()
        _, error = actor()
        if error:
            return error
        if request.args.get('account', 'op') != 'op':
            return jsonify(error='Недоступный аккаунт'), 403
        # Keyset pagination keeps reconnect snapshots bounded without losing chats.
        after = request.args.get('after', '')
        if len(after) > 512:
            return jsonify(error='Некорректный указатель страницы'), 400
        with db._get_cursor() as cur:
            cur.execute("""SELECT s.channel_id,s.chat_id,s.unread_count,s.last_inbound_id,s.revision,
                    c.chat_type,c.contact_name,c.contact_phone,c.last_message_at,
                    c.last_message_text,c.last_message_is_echo
                FROM wazzup_chat_read_state s
                LEFT JOIN wazzup_chats c ON c.account=s.account AND c.channel_id=s.channel_id AND c.chat_id=s.chat_id
                WHERE s.account='op' AND s.unread_count>0 AND s.channel_id || ':' || s.chat_id > %s
                ORDER BY s.channel_id || ':' || s.chat_id LIMIT 1001""", (after,))
            rows = cur.fetchall()
        items = []
        for row in rows[:1000]:
            item = read_item(row[:5])
            item['chat'] = dict(zip(('chatType','contactName','contactPhone','lastMessageAt',
                                    'lastMessageText','lastMessageIsEcho'), row[5:]))
            if item['chat']['lastMessageAt']:
                item['chat']['lastMessageAt'] = item['chat']['lastMessageAt'].isoformat()
            item['chat'].update(channelId=row[0], chatId=row[1])
            items.append(item)
        return jsonify(items=items, next=(rows[999][0]+':'+rows[999][1]) if len(rows)>1000 else None)

    @bp.route('/read', methods=['POST', 'OPTIONS'])
    @require_api_key
    def acknowledge_read():
        if request.method == 'OPTIONS':
            return preflight()
        _, error = actor()
        if error:
            return error
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('account') != 'op':
            return jsonify(error='Недоступный аккаунт'), 403
        cid, chat, seen = (body.get(key) for key in ('channelId','chatId','seenMessageId'))
        if not all(isinstance(v,str) and 0 < len(v) <= 200 for v in (cid,chat,seen)):
            return jsonify(error='Некорректный чат или сообщение'), 400
        if cid in excluded_channels:
            return jsonify(error='Global обрабатывается отдельно'), 403
        with db._get_cursor() as cur:
            # Conditional ACK cannot clear a message that arrived after the view rendered.
            cur.execute("""UPDATE wazzup_chat_read_state SET unread_count=0,revision=revision+1,updated_at=now()
                WHERE account='op' AND channel_id=%s AND chat_id=%s AND last_inbound_id=%s AND unread_count>0""",
                (cid,chat,seen))
            cur.execute("""SELECT channel_id,chat_id,unread_count,last_inbound_id,revision
                FROM wazzup_chat_read_state WHERE account='op' AND channel_id=%s AND chat_id=%s""", (cid,chat))
            row = cur.fetchone()
        return jsonify(item=read_item(row) if row else None)
