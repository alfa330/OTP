# -*- coding: utf-8 -*-
"""Схема ключей партнёров: ключи с областями доступа и журнал вызовов.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается при старте из
Database._init_db через init_partner_keys_schema(cursor) под своим савпоинтом.

Решения, видные в DDL:

* **Ключ хранится хешем.** В таблице — sha256 ключа и его отпечаток (первые
  восемь знаков хеша) для журнала и логов. Сам ключ показывается один раз при
  выпуске; утечка базы ключей не даёт.

* **Области доступа — массив кодов** (scopes.py), а не таблица связей: областей
  единицы, ключей единицы, и интерфейсу нужен весь список разом. Пустой массив
  у ключа невозможен по правилам ручек (хотя бы одна область), поэтому старые
  ключи ручки знаний при переносе получают ['knowledge'].

* **Ключ действует от имени должности в отделе** (otp_role + department_id) —
  это нужно ручке знаний: периметр считают те же функции, что у помощника вики.

* **Отзыв, а не удаление.** Отозванный ключ остаётся строкой с revoked_at: его
  журнал — свидетельство, и вопрос «кто и когда спрашивал» задают постфактум.

* **Журнал общий на все области**: область, ручка, исход, длительность, короткая
  сводка и подробности JSON (у ручки знаний — парк, вопрос, отданные фрагменты
  без текста). Срок хранения — PARTNER_KEYS_CALLS_DAYS (90 дней), чистит
  ежедневная джоба (queries.cleanup_calls).

* **Перенос с knowledge_api_*.** Первая версия (29.10.2026, утро) жила под именем
  ручки знаний. Таблицы переименовываются один раз, старые столбцы журнала
  сворачиваются в summary/details — ключ, уже выданный Syntony, продолжает
  работать с областью «Ручка знаний».
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Исходы вызова, общие для всех областей. Ручка может добавить свой (например
# park_unavailable у ручки знаний): CHECK на столбце намеренно нет — новый исход
# не должен требовать миграции живой базы.
OUTCOMES = ('ok', 'no_data', 'no_link', 'bad_request', 'limit', 'error')

_STATEMENTS = [
    # ── Перенос с первой версии (таблицы ручки знаний) — один раз ────────────
    """
    DO $$
    BEGIN
        IF to_regclass('public.knowledge_api_keys') IS NOT NULL
           AND to_regclass('public.partner_keys') IS NULL THEN
            ALTER TABLE knowledge_api_keys RENAME TO partner_keys;
            ALTER TABLE partner_keys ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{}';
            UPDATE partner_keys SET scopes = ARRAY['knowledge'] WHERE cardinality(scopes) = 0;
        END IF;
        IF to_regclass('public.knowledge_api_calls') IS NOT NULL
           AND to_regclass('public.partner_key_calls') IS NULL THEN
            ALTER TABLE knowledge_api_calls RENAME TO partner_key_calls;
            ALTER INDEX IF EXISTS idx_knowledge_api_calls_key RENAME TO idx_partner_key_calls_key;
            ALTER INDEX IF EXISTS idx_knowledge_api_calls_created RENAME TO idx_partner_key_calls_created;
            ALTER TABLE partner_key_calls
                ADD COLUMN IF NOT EXISTS scope VARCHAR(32) NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS endpoint VARCHAR(120) NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS summary TEXT,
                ADD COLUMN IF NOT EXISTS details JSONB;
            UPDATE partner_key_calls
               SET scope = 'knowledge',
                   endpoint = '/api/knowledge/search',
                   summary = NULLIF(question, ''),
                   details = jsonb_strip_nulls(jsonb_build_object(
                       'park_id', park_id, 'park_name', park_name, 'fragments', fragments,
                       'fragments_count', fragments_count, 'question_id', question_id,
                       'degraded', degraded, 'error_text', error_text))
             WHERE scope = '';
            ALTER TABLE partner_key_calls
                DROP COLUMN IF EXISTS park_id,
                DROP COLUMN IF EXISTS park_name,
                DROP COLUMN IF EXISTS question,
                DROP COLUMN IF EXISTS fragments,
                DROP COLUMN IF EXISTS fragments_count,
                DROP COLUMN IF EXISTS question_id,
                DROP COLUMN IF EXISTS degraded,
                DROP COLUMN IF EXISTS error_text;
        END IF;
    END $$;
    """,
    """
    CREATE TABLE IF NOT EXISTS partner_keys (
        id             SERIAL PRIMARY KEY,
        name           VARCHAR(120) NOT NULL,
        key_hash       CHAR(64) NOT NULL UNIQUE,
        key_mark       VARCHAR(16) NOT NULL,
        scopes         TEXT[] NOT NULL DEFAULT '{}',
        otp_role       VARCHAR(32) NOT NULL DEFAULT 'operator',
        department_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at     TIMESTAMP NOT NULL DEFAULT """ + _NOW + """,
        last_used_at   TIMESTAMP,
        calls_count    BIGINT NOT NULL DEFAULT 0,
        revoked_at     TIMESTAMP,
        revoked_by     INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS partner_key_calls (
        id               BIGSERIAL PRIMARY KEY,
        key_id           INTEGER NOT NULL REFERENCES partner_keys(id) ON DELETE CASCADE,
        request_id       UUID NOT NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT """ + _NOW + """,
        scope            VARCHAR(32) NOT NULL DEFAULT '',
        endpoint         VARCHAR(120) NOT NULL DEFAULT '',
        ip_address       VARCHAR(64),
        user_agent       VARCHAR(300),
        outcome          VARCHAR(16) NOT NULL,
        http_status      SMALLINT,
        latency_ms       INTEGER,
        summary          TEXT,
        details          JSONB
    );
    """,
    # Журнал ключа — страницей от новых к старым.
    "CREATE INDEX IF NOT EXISTS idx_partner_key_calls_key "
    "ON partner_key_calls (key_id, created_at DESC, id DESC);",
    # Чистка по сроку.
    "CREATE INDEX IF NOT EXISTS idx_partner_key_calls_created "
    "ON partner_key_calls (created_at);",
    # ── Подписки на события и их доставка (partner_keys/webhooks.py) ─────────
    # Подписка — одна на ключ и область: адрес партнёра, его секрет для подписи
    # HMAC и список событий (пусто — все события области). Секрет лежит открыто:
    # им подписывается каждая доставка, и хеш здесь не помог бы.
    """
    CREATE TABLE IF NOT EXISTS partner_webhooks (
        id                 SERIAL PRIMARY KEY,
        key_id             INTEGER NOT NULL REFERENCES partner_keys(id) ON DELETE CASCADE,
        scope              VARCHAR(32) NOT NULL,
        url                TEXT NOT NULL,
        secret             TEXT NOT NULL,
        events             TEXT[] NOT NULL DEFAULT '{}',
        enabled            BOOLEAN NOT NULL DEFAULT TRUE,
        created_at         TIMESTAMP NOT NULL DEFAULT """ + _NOW + """,
        updated_at         TIMESTAMP NOT NULL DEFAULT """ + _NOW + """,
        last_delivered_at  TIMESTAMP,
        last_error         TEXT,
        last_error_at      TIMESTAMP,
        failures_in_row    INTEGER NOT NULL DEFAULT 0,
        UNIQUE (key_id, scope)
    );
    """,
    # Очередь доставки: строка на событие и подписку, доставка «как минимум один
    # раз» с арендой строки (lease) — несколько экземпляров портала не дерутся за
    # одно событие. Тот же приём, что у пересылки Wazzup (wazzup/syntony_schema.py).
    """
    CREATE TABLE IF NOT EXISTS partner_webhook_outbox (
        id               BIGSERIAL PRIMARY KEY,
        webhook_id       INTEGER NOT NULL REFERENCES partner_webhooks(id) ON DELETE CASCADE,
        event            VARCHAR(64) NOT NULL,
        event_id         UUID NOT NULL,
        payload          JSONB NOT NULL,
        state            VARCHAR(12) NOT NULL DEFAULT 'ready'
                         CHECK (state IN ('ready', 'done', 'failed')),
        attempts         INTEGER NOT NULL DEFAULT 0,
        next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        lease_token      UUID,
        lease_until      TIMESTAMPTZ,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at     TIMESTAMPTZ,
        completed_at     TIMESTAMPTZ,
        last_status      SMALLINT,
        last_error       TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_partner_webhook_outbox_due "
    "ON partner_webhook_outbox (next_attempt_at, id) WHERE state = 'ready';",
    "CREATE INDEX IF NOT EXISTS idx_partner_webhook_outbox_webhook "
    "ON partner_webhook_outbox (webhook_id, created_at DESC, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_partner_webhook_outbox_completed "
    "ON partner_webhook_outbox (completed_at) WHERE completed_at IS NOT NULL;",
]


def init_partner_keys_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Обе таблицы на месте. Иначе ручки отвечают 503, а не падают на INSERT.

    Сравнение с True, а не истинность: курсор-двойник в тестах соседних
    разделов отдаёт на незнакомый запрос прошлую строку, и кортеж вроде
    (10, 'имя') не должен сойти за «схема есть»."""
    cursor.execute("SELECT to_regclass('public.partner_keys') IS NOT NULL "
                   "AND to_regclass('public.partner_key_calls') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row) and row[0] is True
