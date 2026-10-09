"""Additive schema for the chat processing mode; no changes to archive retention."""


def init_schema(cursor):
    from .unread import init_unread_schema
    from .templates import init_template_schema
    from .syntony_schema import init_schema as init_syntony_schema
    from .notes_schema import init_schema as init_notes_schema
    from .uploads_schema import init_schema as init_uploads_schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_pilot_outbox (
            request_id UUID PRIMARY KEY, account TEXT NOT NULL,
            channel_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT NOT NULL,
            text TEXT NOT NULL, user_id BIGINT NOT NULL, author_name TEXT,
            state TEXT NOT NULL DEFAULT 'sending', message_id TEXT,
            error_code TEXT, error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wazzup_pilot_account_message
            ON wazzup_pilot_outbox(account, message_id) WHERE message_id IS NOT NULL;
        ALTER TABLE wazzup_pilot_outbox ADD COLUMN IF NOT EXISTS reply_to_message_id TEXT;
        -- Author key of a verifier's send (wazzup.access.icore_author_id); NULL
        -- for senders who are not credited in the reports.
        ALTER TABLE wazzup_pilot_outbox ADD COLUMN IF NOT EXISTS author_id TEXT;
        CREATE OR REPLACE FUNCTION wazzup_pilot_stamp_author()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE sender RECORD;
        BEGIN
            -- Wazzup echoes API sends as "Admin" without an author id. Keep the
            -- real sender on every write of such a row, whichever arrives first:
            -- our own archive copy, the echo, or a later delivery status.
            IF NEW.account = 'op' AND NEW.is_echo THEN
                SELECT o.author_id, o.author_name INTO sender FROM wazzup_pilot_outbox o
                    WHERE o.account = NEW.account AND o.message_id = NEW.message_id
                      AND o.author_id IS NOT NULL;
                IF FOUND THEN
                    NEW.author_id := sender.author_id;
                    NEW.author_name := COALESCE(sender.author_name, NEW.author_name);
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TABLE IF NOT EXISTS wazzup_status_receipts (
            account TEXT NOT NULL, message_id TEXT NOT NULL, status TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(account, message_id)
        );
        CREATE OR REPLACE FUNCTION wazzup_delivery_rank(value TEXT)
        RETURNS INTEGER LANGUAGE SQL IMMUTABLE AS $$
            SELECT CASE value WHEN 'read' THEN 4 WHEN 'delivered' THEN 3
                WHEN 'error' THEN 2 WHEN 'sent' THEN 1 ELSE 0 END
        $$;
        CREATE OR REPLACE FUNCTION wazzup_merge_delivery(old_status TEXT, new_status TEXT)
        RETURNS TEXT LANGUAGE SQL IMMUTABLE AS $$
            SELECT CASE WHEN new_status IS NULL OR new_status = 'edited' THEN NULLIF(old_status, 'edited')
                WHEN wazzup_delivery_rank(old_status) > wazzup_delivery_rank(new_status)
                THEN old_status ELSE new_status END
        $$;
        CREATE OR REPLACE FUNCTION wazzup_message_receipt_before_insert()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE receipt TEXT;
        BEGIN
            -- The same lock is taken by the status writer: an early receipt
            -- cannot slip between this lookup and the INSERT commit.
            PERFORM pg_advisory_xact_lock(hashtext('wazzup-status'), hashtext(NEW.message_id));
            SELECT status INTO receipt FROM wazzup_status_receipts
                WHERE account=NEW.account AND message_id=NEW.message_id;
            NEW.status := wazzup_merge_delivery(NEW.status, receipt);
            RETURN NEW;
        END $$;
        CREATE OR REPLACE FUNCTION wazzup_pilot_notify_change()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE affects_list BOOLEAN := TRUE;
                status_only BOOLEAN := FALSE;
        BEGIN
            IF NEW.account <> 'op' THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' THEN
                status_only := ROW(OLD.dt, OLD.is_echo, OLD.type, OLD.text, OLD.content_uri,
                    OLD.author_name, OLD.author_id, OLD.is_edited, OLD.is_deleted, OLD.wazzup_dt,
                    OLD.contact_name, OLD.contact_phone, OLD.chat_type, OLD.channel_id, OLD.chat_id)
                    IS NOT DISTINCT FROM ROW(NEW.dt, NEW.is_echo, NEW.type, NEW.text, NEW.content_uri,
                    NEW.author_name, NEW.author_id, NEW.is_edited, NEW.is_deleted, NEW.wazzup_dt,
                    NEW.contact_name, NEW.contact_phone, NEW.chat_type, NEW.channel_id, NEW.chat_id);
                IF status_only AND OLD.status IS NOT DISTINCT FROM NEW.status THEN RETURN NEW; END IF;
                affects_list := ROW(OLD.dt, OLD.text, OLD.content_uri, OLD.is_deleted,
                    OLD.contact_name, OLD.contact_phone, OLD.is_echo, OLD.chat_type)
                    IS DISTINCT FROM ROW(NEW.dt, NEW.text, NEW.content_uri, NEW.is_deleted,
                    NEW.contact_name, NEW.contact_phone, NEW.is_echo, NEW.chat_type);
            END IF;
            PERFORM pg_notify('wazzup_pilot_events', json_build_object(
                'account', NEW.account, 'channelId', NEW.channel_id,
                'chatId', NEW.chat_id, 'messageId', NEW.message_id,
                'status', NEW.status,
                'affectsList', affects_list,
                'statusOnly', status_only,
                'isEcho', NEW.is_echo,
                'createdAt', NEW.created_at,
                'emittedAt', EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::text);
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS wazzup_pilot_author ON wazzup_messages;
        CREATE TRIGGER wazzup_pilot_author BEFORE INSERT OR UPDATE ON wazzup_messages
            FOR EACH ROW EXECUTE FUNCTION wazzup_pilot_stamp_author();
        DROP TRIGGER IF EXISTS wazzup_receipt_insert ON wazzup_messages;
        CREATE TRIGGER wazzup_receipt_insert BEFORE INSERT ON wazzup_messages
            FOR EACH ROW EXECUTE FUNCTION wazzup_message_receipt_before_insert();
        DROP TRIGGER IF EXISTS wazzup_pilot_notify ON wazzup_messages;
        CREATE TRIGGER wazzup_pilot_notify AFTER INSERT OR UPDATE ON wazzup_messages
            FOR EACH ROW EXECUTE FUNCTION wazzup_pilot_notify_change();
    """)
    init_unread_schema(cursor)
    init_template_schema(cursor)
    init_syntony_schema(cursor)
    init_notes_schema(cursor)
    init_uploads_schema(cursor)
