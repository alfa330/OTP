"""Additive schema for the alfa330 chat pilot; no changes to archive retention."""


def init_schema(cursor):
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
        BEGIN
            IF NEW.account <> 'op' THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' AND ROW(OLD.status, OLD.text, OLD.content_uri,
                OLD.is_edited, OLD.is_deleted, OLD.author_name, OLD.author_id, OLD.dt)
                IS NOT DISTINCT FROM ROW(NEW.status, NEW.text, NEW.content_uri,
                NEW.is_edited, NEW.is_deleted, NEW.author_name, NEW.author_id, NEW.dt)
                THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' THEN
                affects_list := ROW(OLD.dt, OLD.text, OLD.content_uri, OLD.is_deleted)
                    IS DISTINCT FROM ROW(NEW.dt, NEW.text, NEW.content_uri, NEW.is_deleted);
            END IF;
            PERFORM pg_notify('wazzup_pilot_events', json_build_object(
                'account', NEW.account, 'channelId', NEW.channel_id,
                'chatId', NEW.chat_id, 'messageId', NEW.message_id,
                'status', NEW.status,
                'affectsList', affects_list,
                'emittedAt', EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::text);
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS wazzup_receipt_insert ON wazzup_messages;
        CREATE TRIGGER wazzup_receipt_insert BEFORE INSERT ON wazzup_messages
            FOR EACH ROW EXECUTE FUNCTION wazzup_message_receipt_before_insert();
        DROP TRIGGER IF EXISTS wazzup_pilot_notify ON wazzup_messages;
        CREATE TRIGGER wazzup_pilot_notify AFTER INSERT OR UPDATE ON wazzup_messages
            FOR EACH ROW EXECUTE FUNCTION wazzup_pilot_notify_change();
    """)
