"""Shared needs-reply counter, retaining the existing unread API field names.

Only pending inbound IDs are kept. Delivery receipts and operator replies can
therefore clear a prefix without scanning the archive or clearing a later reply.
"""
from flask import jsonify, request


def init_unread_schema(cursor):
    cursor.execute("""
        -- Match the writer's lock order (messages -> state). init_schema()
        -- already owns this lock while replacing its other message triggers;
        -- taking it here also makes standalone upgrades safe from inversion.
        LOCK TABLE wazzup_messages IN SHARE ROW EXCLUSIVE MODE;
        CREATE TABLE IF NOT EXISTS wazzup_chat_read_state (
            account TEXT NOT NULL, channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            unread_count INTEGER NOT NULL DEFAULT 0 CHECK(unread_count >= 0),
            last_inbound_id TEXT NOT NULL, revision BIGINT NOT NULL DEFAULT 1,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(account, channel_id, chat_id)
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_unread_active
            ON wazzup_chat_read_state(account, (channel_id || ':' || chat_id)) WHERE unread_count > 0;
        ALTER TABLE wazzup_chat_read_state
            ADD COLUMN IF NOT EXISTS answered_through TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS unanswered_ready BOOLEAN NOT NULL DEFAULT FALSE;
        CREATE TABLE IF NOT EXISTS wazzup_unanswered_messages (
            account TEXT NOT NULL, channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL, sent_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(account, channel_id, chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_unanswered_time
            ON wazzup_unanswered_messages(account, channel_id, chat_id, sent_at);
        -- Anonymous integration messages include automatic greetings. The v3
        -- echo does not expose clearUnanswered=false, so only identified human
        -- replies/native Wazzup sends/our own outbox dismiss pending inbound.
        CREATE OR REPLACE FUNCTION wazzup_is_operator_reply(
            author TEXT, author_key TEXT, from_app TEXT, acc TEXT, mid TEXT)
        RETURNS BOOLEAN LANGUAGE SQL STABLE AS $$
            SELECT NULLIF(BTRIM(author),'') IS NOT NULL OR NULLIF(BTRIM(author_key),'') IS NOT NULL
                OR LOWER(COALESCE(from_app,'')) IN ('true','t','1')
                OR EXISTS(SELECT 1 FROM wazzup_pilot_outbox o WHERE o.account=acc AND o.message_id=mid)
        $$;

        -- Reconcile only the already tracked tail once. A manual dismissal made
        -- before this migration is represented by unread_count=0 and stays zero.
        -- Existing archive history never creates additional tracked chats.
        DO $$
        DECLARE state RECORD; answered TIMESTAMPTZ; remaining INTEGER;
        BEGIN
            FOR state IN SELECT * FROM wazzup_chat_read_state
                WHERE NOT unanswered_ready FOR UPDATE LOOP
                SELECT MAX(COALESCE(wazzup_dt, dt)) INTO answered FROM wazzup_messages
                    WHERE account=state.account AND channel_id=state.channel_id AND chat_id=state.chat_id
                      AND is_echo AND NOT is_deleted AND chat_type='whatsapp'
                      AND (status IS NULL OR status IN ('', 'sent', 'delivered', 'read'))
                      AND wazzup_is_operator_reply(author_name,author_id,sent_from_app::text,account,message_id);
                INSERT INTO wazzup_unanswered_messages(account,channel_id,chat_id,message_id,sent_at)
                    SELECT state.account,state.channel_id,state.chat_id,m.message_id,COALESCE(m.wazzup_dt,m.dt)
                    FROM (SELECT message_id,dt,wazzup_dt,is_deleted FROM wazzup_messages
                        WHERE account=state.account AND channel_id=state.channel_id AND chat_id=state.chat_id
                          AND NOT is_echo AND chat_type='whatsapp'
                        ORDER BY created_at DESC,dt DESC,message_id DESC LIMIT state.unread_count) m
                    WHERE NOT m.is_deleted AND (answered IS NULL OR COALESCE(m.wazzup_dt,m.dt)>answered)
                    ON CONFLICT DO NOTHING;
                SELECT COUNT(*) INTO remaining FROM wazzup_unanswered_messages
                    WHERE account=state.account AND channel_id=state.channel_id AND chat_id=state.chat_id;
                UPDATE wazzup_chat_read_state SET unread_count=remaining,answered_through=answered,
                    unanswered_ready=TRUE,revision=revision+(unread_count<>remaining)::int,updated_at=now()
                    WHERE account=state.account AND channel_id=state.channel_id AND chat_id=state.chat_id;
            END LOOP;
        END $$;
        ALTER TABLE wazzup_chat_read_state ALTER COLUMN unanswered_ready SET DEFAULT TRUE;

        CREATE OR REPLACE FUNCTION wazzup_track_unread() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE answered TIMESTAMPTZ; event_time TIMESTAMPTZ;
                removed INTEGER; inserted INTEGER;
        BEGIN
            IF NEW.account<>'op' OR NEW.chat_type IS DISTINCT FROM 'whatsapp'
               OR NEW.channel_id IN ('99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',
                                     'a4bccb5e-5d41-483d-b7c1-a1079685577d') THEN RETURN NEW; END IF;
            event_time := COALESCE(NEW.wazzup_dt,NEW.dt);
            IF NOT NEW.is_echo AND NOT NEW.is_deleted AND TG_OP='INSERT' THEN
                SELECT answered_through INTO answered FROM wazzup_chat_read_state
                    WHERE account=NEW.account AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id FOR UPDATE;
                IF NOT FOUND THEN
                    -- Only a genuinely new chat consults the archive. The hot
                    -- path above is one primary-key lookup, independent of its
                    -- historical message count or PostgreSQL aggregate plans.
                    INSERT INTO wazzup_chat_read_state(account,channel_id,chat_id,last_inbound_id,
                        revision,answered_through)
                    SELECT NEW.account,NEW.channel_id,NEW.chat_id,NEW.message_id,0,
                        MAX(COALESCE(m.wazzup_dt,m.dt)) FROM wazzup_messages m
                        WHERE m.account=NEW.account AND m.channel_id=NEW.channel_id AND m.chat_id=NEW.chat_id
                          AND m.is_echo AND NOT m.is_deleted AND m.chat_type='whatsapp'
                          AND (m.status IS NULL OR m.status IN ('', 'sent', 'delivered', 'read'))
                          AND wazzup_is_operator_reply(m.author_name,m.author_id,m.sent_from_app::text,m.account,m.message_id)
                    ON CONFLICT(account,channel_id,chat_id) DO NOTHING;
                    -- A concurrent first outgoing/incoming can win the insert;
                    -- use its committed watermark under the common row lock.
                    SELECT answered_through INTO answered FROM wazzup_chat_read_state
                        WHERE account=NEW.account AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id FOR UPDATE;
                END IF;
                IF answered IS NULL OR event_time>answered THEN
                    INSERT INTO wazzup_unanswered_messages(account,channel_id,chat_id,message_id,sent_at)
                        VALUES(NEW.account,NEW.channel_id,NEW.chat_id,NEW.message_id,event_time)
                        ON CONFLICT DO NOTHING;
                    GET DIAGNOSTICS inserted = ROW_COUNT;
                    IF inserted>0 THEN
                        UPDATE wazzup_chat_read_state SET unread_count=unread_count+1,
                            last_inbound_id=NEW.message_id,revision=revision+1,updated_at=now()
                            WHERE account=NEW.account AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id;
                    END IF;
                END IF;
            ELSIF NEW.is_echo AND NOT NEW.is_deleted
                AND (NEW.status IS NULL OR NEW.status IN ('', 'sent', 'delivered', 'read'))
                AND wazzup_is_operator_reply(NEW.author_name,NEW.author_id,NEW.sent_from_app::text,
                                            NEW.account,NEW.message_id) THEN
                IF TG_OP='UPDATE' AND NOT OLD.is_deleted
                    AND (OLD.status IS NULL OR OLD.status IN ('', 'sent', 'delivered', 'read'))
                    AND wazzup_is_operator_reply(OLD.author_name,OLD.author_id,OLD.sent_from_app::text,
                                                OLD.account,OLD.message_id) THEN
                    RETURN NEW;
                END IF;
                -- A zero state also serializes an outgoing message racing with
                -- the first incoming message in a chat. No unread is created.
                INSERT INTO wazzup_chat_read_state(account,channel_id,chat_id,last_inbound_id,revision)
                    VALUES(NEW.account,NEW.channel_id,NEW.chat_id,'',0)
                    ON CONFLICT(account,channel_id,chat_id) DO NOTHING;
                SELECT answered_through INTO answered FROM wazzup_chat_read_state
                    WHERE account=NEW.account AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id FOR UPDATE;
                IF NOT FOUND OR (answered IS NOT NULL AND event_time<=answered) THEN RETURN NEW; END IF;
                DELETE FROM wazzup_unanswered_messages WHERE account=NEW.account
                    AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id AND sent_at<=event_time;
                GET DIAGNOSTICS removed = ROW_COUNT;
                UPDATE wazzup_chat_read_state SET unread_count=GREATEST(0,unread_count-removed),
                    answered_through=event_time,revision=revision+(removed>0)::int,updated_at=now()
                    WHERE account=NEW.account AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id;
            ELSIF NOT NEW.is_echo AND NEW.is_deleted AND TG_OP='UPDATE' AND NOT OLD.is_deleted THEN
                PERFORM 1 FROM wazzup_chat_read_state WHERE account=NEW.account
                    AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id FOR UPDATE;
                DELETE FROM wazzup_unanswered_messages WHERE account=NEW.account
                    AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id AND message_id=NEW.message_id;
                GET DIAGNOSTICS removed = ROW_COUNT;
                IF removed>0 THEN
                    UPDATE wazzup_chat_read_state SET unread_count=GREATEST(0,unread_count-removed),
                        revision=revision+1,updated_at=now() WHERE account=NEW.account
                        AND channel_id=NEW.channel_id AND chat_id=NEW.chat_id;
                END IF;
            END IF;
            RETURN NEW;
        END $$;

        CREATE OR REPLACE FUNCTION wazzup_dismiss_unanswered(cid TEXT, chat TEXT, seen TEXT)
        RETURNS VOID LANGUAGE plpgsql AS $$
        DECLARE current_id TEXT; current_count INTEGER;
        BEGIN
            SELECT last_inbound_id,unread_count INTO current_id,current_count FROM wazzup_chat_read_state
                WHERE account='op' AND channel_id=cid AND chat_id=chat FOR UPDATE;
            IF current_id=seen AND current_count>0 THEN
                DELETE FROM wazzup_unanswered_messages WHERE account='op' AND channel_id=cid AND chat_id=chat;
                UPDATE wazzup_chat_read_state SET unread_count=0,revision=revision+1,updated_at=now()
                    WHERE account='op' AND channel_id=cid AND chat_id=chat;
            END IF;
        END $$;
        CREATE OR REPLACE FUNCTION wazzup_notify_unread() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' AND NEW.unread_count=0 THEN RETURN NEW; END IF;
            IF TG_OP='UPDATE' AND ROW(NEW.unread_count,NEW.last_inbound_id,NEW.revision)
                IS NOT DISTINCT FROM ROW(OLD.unread_count,OLD.last_inbound_id,OLD.revision) THEN RETURN NEW; END IF;
            PERFORM pg_notify('wazzup_pilot_events', json_build_object(
                'account',NEW.account,'channelId',NEW.channel_id,'chatId',NEW.chat_id,
                'messageId','unread:' || NEW.channel_id || ':' || NEW.chat_id,
                'kind','unread','statusOnly',TRUE,'affectsList',FALSE,
                'unreadCount',NEW.unread_count,'unreadVersion',NEW.revision,
                'lastInboundId',NEW.last_inbound_id)::text);
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS wazzup_unread_insert ON wazzup_messages;
        CREATE TRIGGER wazzup_unread_insert AFTER INSERT OR UPDATE ON wazzup_messages
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
            # This is an explicit "Ответ не нужен" action. Opening a chat does
            # not call it. The row lock protects the pending IDs and the count
            # together; a newer unseen inbound makes this dismissal a no-op.
            cur.execute('SELECT wazzup_dismiss_unanswered(%s,%s,%s)', (cid,chat,seen))
            cur.execute("""SELECT channel_id,chat_id,unread_count,last_inbound_id,revision
                FROM wazzup_chat_read_state WHERE account='op' AND channel_id=%s AND chat_id=%s""", (cid,chat))
            row = cur.fetchone()
        return jsonify(item=read_item(row) if row else None)
