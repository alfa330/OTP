# -*- coding: utf-8 -*-
"""Таблицы автоинформатора. Своей транзакцией и только при старте (ensure_schema): на путях
запросов DDL нет — флаг «готово» ставится после коммита (см. заметку про схемы модулей)."""
import logging
import threading
import time

log = logging.getLogger(__name__)

DDL = [
    """
    CREATE TABLE IF NOT EXISTS informer_campaigns (
        id SERIAL PRIMARY KEY,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        name VARCHAR(200) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'draft',
        -- телефонная версия (8 кГц) — её играет робот; audio_sha256 и audio_ms — о ней
        audio_wav BYTEA NOT NULL,
        -- оригинал (24 кГц) — его слушает руководитель; NULL у рассылок прежней страницы (только 8 кГц)
        audio_master BYTEA,
        audio_sha256 VARCHAR(64) NOT NULL,
        audio_ms INTEGER NOT NULL,
        -- [{lang, start_ms, end_ms}] — части записи по порядку (сначала казахская)
        parts JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- откуда запись: {source: tts|upload, voice, speed, texts: {kz, ru}} — что именно звучало
        script JSONB NOT NULL DEFAULT '{}'::jsonb,
        window_start TIME NOT NULL DEFAULT '10:00',
        window_end TIME NOT NULL DEFAULT '20:00',
        max_per_minute SMALLINT NOT NULL DEFAULT 10,
        max_attempts SMALLINT NOT NULL DEFAULT 2,
        retry_after_min INTEGER NOT NULL DEFAULT 60,
        targets_total INTEGER NOT NULL DEFAULT 0,
        file_name VARCHAR(255) NOT NULL DEFAULT '',
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    )
    """,
    "ALTER TABLE informer_campaigns ADD COLUMN IF NOT EXISTS audio_master BYTEA",
    "CREATE INDEX IF NOT EXISTS informer_campaigns_dept ON informer_campaigns (department_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS informer_targets (
        id BIGSERIAL PRIMARY KEY,
        campaign_id INTEGER NOT NULL REFERENCES informer_campaigns(id) ON DELETE CASCADE,
        phone VARCHAR(16) NOT NULL,
        full_name VARCHAR(200) NOT NULL DEFAULT '',
        row_number INTEGER,
        state VARCHAR(16) NOT NULL DEFAULT 'pending',
        attempts SMALLINT NOT NULL DEFAULT 0,
        next_call_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_result VARCHAR(24) NOT NULL DEFAULT '',
        last_call_at TIMESTAMPTZ,
        best_listened_ms INTEGER,
        best_played_to_end BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE (campaign_id, phone)
    )
    """,
    "CREATE INDEX IF NOT EXISTS informer_targets_due ON informer_targets (campaign_id, state, next_call_at)",
    """
    CREATE TABLE IF NOT EXISTS informer_calls (
        id BIGSERIAL PRIMARY KEY,
        campaign_id INTEGER NOT NULL REFERENCES informer_campaigns(id) ON DELETE CASCADE,
        target_id BIGINT NOT NULL REFERENCES informer_targets(id) ON DELETE CASCADE,
        attempt_no SMALLINT NOT NULL,
        line VARCHAR(16) NOT NULL DEFAULT '',
        agent_id VARCHAR(128) NOT NULL DEFAULT '',
        issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        dialed_at TIMESTAMPTZ,
        answered_at TIMESTAMPTZ,
        ended_at TIMESTAMPTZ,
        ring_ms INTEGER,
        listened_ms INTEGER,
        played_to_end BOOLEAN,
        result VARCHAR(24) NOT NULL DEFAULT '',
        hangup_by VARCHAR(16) NOT NULL DEFAULT '',
        hangup_cause INTEGER,
        dtmf VARCHAR(32) NOT NULL DEFAULT '',
        finished BOOLEAN NOT NULL DEFAULT FALSE,
        finished_at TIMESTAMPTZ,
        details JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS informer_calls_campaign ON informer_calls (campaign_id, issued_at)",
    "CREATE INDEX IF NOT EXISTS informer_calls_issued ON informer_calls (issued_at)",
    "CREATE INDEX IF NOT EXISTS informer_calls_open ON informer_calls (issued_at) WHERE NOT finished",
    """
    CREATE TABLE IF NOT EXISTS informer_agent_state (
        id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        agent_id VARCHAR(128) NOT NULL DEFAULT '',
        version VARCHAR(64) NOT NULL DEFAULT '',
        key_id VARCHAR(32) NOT NULL DEFAULT '',
        lines JSONB NOT NULL DEFAULT '{}'::jsonb,
        ami_ok BOOLEAN NOT NULL DEFAULT FALSE,
        error TEXT NOT NULL DEFAULT '',
        last_seen_at TIMESTAMPTZ
    )
    """,
]

_ready = False
_lock = threading.Lock()
_last_failure = 0.0
RETRY_AFTER_FAILURE_SEC = 600


def apply(cursor):
    """DDL в переданной транзакции — для тестов на чистой схеме."""
    for statement in DDL:
        cursor.execute(statement)


def ensure_schema(db):
    """Создать таблицы своей транзакцией. True — готово. Неудача — повтор не чаще раза в 10 мин."""
    global _ready, _last_failure
    if _ready:
        return True
    with _lock:
        if _ready:
            return True
        if _last_failure and time.monotonic() - _last_failure < RETRY_AFTER_FAILURE_SEC:
            return False
        try:
            with db._get_cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                apply(cursor)
        except Exception:
            _last_failure = time.monotonic()
            log.exception("Автоинформатор: схема не создана — раздел ответит 503")
            return False
        _ready = True
        return True


def is_ready():
    return _ready


def _reset_for_tests():
    global _ready, _last_failure
    _ready = False
    _last_failure = 0.0
