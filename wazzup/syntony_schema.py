"""Durable, independently leased delivery of the Global Wazzup webhooks."""


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_syntony_outbox (
            id BIGSERIAL PRIMARY KEY,
            fingerprint TEXT NOT NULL UNIQUE,
            account TEXT NOT NULL CHECK (account = 'op'),
            phase TEXT NOT NULL CHECK (
                phase IN ('ready', 'resolve', 'done', 'ignored', 'failed', 'receipt')),
            payload BYTEA,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            lease_token UUID,
            lease_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            last_error TEXT
        );
        ALTER TABLE wazzup_syntony_outbox
            ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS idx_wazzup_syntony_due
            ON wazzup_syntony_outbox(next_attempt_at, id)
            WHERE phase IN ('ready', 'resolve');
        CREATE INDEX IF NOT EXISTS idx_wazzup_syntony_completed
            ON wazzup_syntony_outbox(completed_at)
            WHERE completed_at IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_wazzup_syntony_created
            ON wazzup_syntony_outbox(created_at);
    """)
