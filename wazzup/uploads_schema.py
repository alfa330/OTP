"""Private, immutable staging files for the chat processing outbox."""


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_pilot_uploads (
            id UUID PRIMARY KEY,
            account TEXT NOT NULL DEFAULT 'op' CHECK(account='op'),
            channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            user_id BIGINT NOT NULL, client_upload_id UUID NOT NULL,
            original_name TEXT NOT NULL, content_type TEXT NOT NULL,
            message_type TEXT NOT NULL,
            file_size INTEGER NOT NULL CHECK(file_size > 0 AND file_size <= 10485760),
            sha256 TEXT NOT NULL,
            bucket TEXT NOT NULL, blob_path TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours',
            deleted_at TIMESTAMPTZ,
            UNIQUE(user_id, client_upload_id)
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_pilot_uploads_expiry
            ON wazzup_pilot_uploads(expires_at) WHERE deleted_at IS NULL;
        ALTER TABLE wazzup_pilot_outbox ADD COLUMN IF NOT EXISTS attachment_id UUID;
        -- The link Wazzup downloads the file from (wazzup/uploads.issue_link):
        -- sha256 of its random token, never the token itself.
        ALTER TABLE wazzup_pilot_uploads ADD COLUMN IF NOT EXISTS link_hash TEXT;
        ALTER TABLE wazzup_pilot_uploads ADD COLUMN IF NOT EXISTS link_expires_at TIMESTAMPTZ;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wazzup_pilot_uploads_link
            ON wazzup_pilot_uploads(link_hash) WHERE link_hash IS NOT NULL;
    """)
