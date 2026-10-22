# -*- coding: utf-8 -*-
"""Схема раздела «АТС»: живые звонки, команды станции, состояние коннектора.

Идемпотентно (CREATE … IF NOT EXISTS), разворачивается при старте из Database._init_db под
своим SAVEPOINT (`_init_pbx_schema_tx`) — упавшая схема раздела не роняет приложение, а
ручки отвечают 503 словами (schema_is_ready).

Почему своя таблица звонков, а не cdr_touches
---------------------------------------------
Касание — итог звонка по CDR: оно появляется после отбоя и переписывается сутками целиком.
Живой звонок — состояние «прямо сейчас», которое меняется десять раз за минуту и ничего не
значит назавтра. Смешать их — значит либо терять живое при перезаписи суток, либо
портить сверенные касания недописанными строками. Ключ общий — `linkedid` станции, по нему
живой звонок потом сходится с касанием (и с записью, которую приносит мост).

Что хранится и сколько
----------------------
`pbx_calls` — все звонки, которые видел коннектор, с их последним состоянием; законченные
старше PBX_CALLS_RETENTION_DAYS убираются (это кэш живого состояния, история — в касаниях).
`pbx_commands` — очередь команд станции: queued → sent (коннектор забрал) → accepted
(станция приняла: телефон звонит) → done | failed; queued дольше COMMAND_TTL_SECONDS без
коннектора становится expired, чтобы телефон не зазвонил через полчаса после нажатия.
Телефоны клиентов лежат десятью цифрами, как в касаниях; ФИО — нигде: имя оператора
подставляется при чтении из справочника «Касаний» (cdr_operators).
"""

PBX_CALLS_RETENTION_DAYS = 7
PBX_COMMANDS_RETENTION_DAYS = 30
# Команда, которую коннектор не забрал за это время, протухает: нажали «позвонить», коннектор
# лежал — через полчаса телефон звонить не должен.
COMMAND_TTL_SECONDS = 60

PBX_SCHEMA_SQL = """
-- ── СОСТОЯНИЕ КОННЕКТОРА ───────────────────────────────────────────────────
-- Одна строка. По ней раздел различает «тихо, звонков нет» и «коннектор молчит
-- десять минут» — и показывает, видит ли он станцию (ami_connected).
CREATE TABLE IF NOT EXISTS pbx_connector_state (
    id             INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_seen_at   TIMESTAMPTZ,
    hostname       TEXT,
    version        TEXT,
    agent_key      TEXT,
    ami_connected  BOOLEAN NOT NULL DEFAULT FALSE,
    ami_host       TEXT,
    ami_error      TEXT,
    events_total   BIGINT NOT NULL DEFAULT 0,
    snapshot_at    TIMESTAMPTZ,
    -- Линии станции: {префикс набора: {number, name}} — с какой линии можно позвонить.
    lines          JSONB,
    lines_at       TIMESTAMPTZ,
    cleaned_at     TIMESTAMPTZ
);

-- ── ЖИВЫЕ ЗВОНКИ ───────────────────────────────────────────────────────────
-- Одна строка = один звонок станции (linkedid), последнее известное состояние.
-- Время — местное время станции без пояса, как у касаний (started_at TIMESTAMP).
CREATE TABLE IF NOT EXISTS pbx_calls (
    linkedid       VARCHAR(64) PRIMARY KEY,
    direction      VARCHAR(12) NOT NULL DEFAULT '',   -- in | out | internal | autodial
    phone          VARCHAR(16) NOT NULL DEFAULT '',   -- клиент, десять цифр
    did            VARCHAR(24) NOT NULL DEFAULT '',   -- набранный номер (линия парка)
    queue          VARCHAR(8)  NOT NULL DEFAULT '',
    ext            VARCHAR(8)  NOT NULL DEFAULT '',   -- оператор в разговоре или инициатор
    ringing        JSONB       NOT NULL DEFAULT '[]', -- у кого звонит телефон сейчас
    state          VARCHAR(12) NOT NULL DEFAULT 'new', -- new | queued | ringing | talking | ended
    started_at     TIMESTAMP NOT NULL,
    queued_at      TIMESTAMP,
    ringing_at     TIMESTAMP,
    answered_at    TIMESTAMP,
    ended_at       TIMESTAMP,
    wait_seconds   INTEGER,
    hangup_cause   INTEGER,
    hangup_side    VARCHAR(16) NOT NULL DEFAULT '',  -- operator | client | ''
    dial_prefix    VARCHAR(8)  NOT NULL DEFAULT '',  -- исходящий маршрут (линия), 4 цифры
    dial_status    VARCHAR(16) NOT NULL DEFAULT '',  -- BUSY, NOANSWER, ABANDON…
    channel        TEXT NOT NULL DEFAULT '',          -- первый канал звонка
    agent_channel  TEXT NOT NULL DEFAULT '',          -- канал оператора (для подключения)
    recording_file TEXT NOT NULL DEFAULT '',
    origin         VARCHAR(16) NOT NULL DEFAULT '',  -- 'portal' — набрано отсюда
    command_id     BIGINT,
    seq            BIGINT NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pbx_calls_active  ON pbx_calls(updated_at) WHERE state <> 'ended';
CREATE INDEX IF NOT EXISTS idx_pbx_calls_started ON pbx_calls(started_at);
CREATE INDEX IF NOT EXISTS idx_pbx_calls_ext     ON pbx_calls(ext, started_at);

-- ── КОМАНДЫ СТАНЦИИ ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pbx_commands (
    id             BIGSERIAL PRIMARY KEY,
    kind           VARCHAR(16) NOT NULL,              -- originate | hangup | spy
    params         JSONB NOT NULL DEFAULT '{}',
    requested_by   INTEGER,
    status         VARCHAR(16) NOT NULL DEFAULT 'queued',
    message        TEXT NOT NULL DEFAULT '',
    linkedid       VARCHAR(64),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at        TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_pbx_commands_open ON pbx_commands(created_at)
    WHERE status IN ('queued', 'sent', 'accepted');
"""


def init_pbx_schema(cursor):
    """Создаёт таблицы раздела. Курсор приходит снаружи: схема всех разделов разворачивается
    одной транзакцией."""
    cursor.execute(PBX_SCHEMA_SQL)


def schema_is_ready(cursor):
    cursor.execute("SELECT to_regclass('public.pbx_calls')")
    row = cursor.fetchone()
    return bool(row and row[0])
