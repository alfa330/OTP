"""Private iCORE comments: isolated from messages, vendor outboxes and unread."""


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_chat_notes (
            id UUID PRIMARY KEY,
            account TEXT NOT NULL CHECK (account='op'),
            channel_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            client_note_id UUID NOT NULL,
            author_id BIGINT NOT NULL,
            author_name TEXT NOT NULL,
            text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (author_id, client_note_id),
            CHECK (channel_id NOT IN ('99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',
                                     'a4bccb5e-5d41-483d-b7c1-a1079685577d'))
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_chat_notes_thread
            ON wazzup_chat_notes(account,channel_id,chat_id,created_at DESC,id DESC);
        CREATE OR REPLACE FUNCTION wazzup_notify_note() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            -- Only metadata enters NOTIFY. The shared authenticated SSE listener
            -- hydrates private notes separately, after this transaction commits.
            PERFORM pg_notify('wazzup_pilot_events', json_build_object(
                'account',NEW.account,'channelId',NEW.channel_id,'chatId',NEW.chat_id,
                'messageId','note:' || NEW.id::text,'noteId',NEW.id::text,
                'kind','note','statusOnly',FALSE,'affectsList',FALSE,
                'createdAt',NEW.created_at,
                'emittedAt',EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::text);
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS wazzup_note_notify ON wazzup_chat_notes;
        CREATE TRIGGER wazzup_note_notify AFTER INSERT ON wazzup_chat_notes
            FOR EACH ROW EXECUTE FUNCTION wazzup_notify_note();
    """)
