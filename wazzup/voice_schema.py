"""Text of voice messages (wazzup/voice.py): one row per audio message.

A table of its own, not columns of wazzup_messages: that table takes every
webhook, and DDL on it at each start is a lock the whole section waits behind.
Rows die with their message (the retention sweep in wazzup/voice.py), so the
transcripts live exactly as long as the 45-day archive they describe.

status: pending (queued, leased while a worker transcribes it), ready,
no_speech, failed (gave up after retries; «Повторить» queues it again),
unavailable (Wazzup no longer serves the file), skipped (too long or too big).
Everything the paid call returned is kept: tokens by modality, model version,
latency, the raw usage block and the estimated cost.
"""


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_voice_transcripts (
            message_id TEXT PRIMARY KEY,
            account TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            text TEXT,
            language TEXT,
            source TEXT NOT NULL DEFAULT 'arrival',
            requested_by BIGINT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            lease_token UUID,
            lease_until TIMESTAMPTZ,
            error TEXT,
            model TEXT,
            model_version TEXT,
            prompt_version INTEGER,
            audio_bytes INTEGER,
            audio_duration_ms INTEGER,
            audio_tokens INTEGER,
            text_tokens INTEGER,
            output_tokens INTEGER,
            thinking_tokens INTEGER,
            estimated_cost NUMERIC(12, 8),
            latency_ms INTEGER,
            usage JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_voice_transcripts_queue
            ON wazzup_voice_transcripts (next_attempt_at) WHERE status = 'pending';
        -- Attempts started per Almaty day: the daily fuse of the worker. Counted when
        -- an attempt starts, so neither «Повторить» nor a row from yesterday hides one.
        CREATE TABLE IF NOT EXISTS wazzup_voice_daily (
            day DATE PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0
        );
    """)
