# -*- coding: utf-8 -*-
"""Схема ручки знаний: ключи партнёров и журнал вызовов.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается при старте из
Database._init_db через init_knowledge_api_schema(cursor) под своим савпоинтом —
как у «Ссылки на подписание».

Решения, видные в DDL:

* **Ключ хранится хешем.** В таблице — sha256 ключа и его отпечаток (первые
  восемь знаков хеша) для журнала и логов. Сам ключ показывается один раз при
  выпуске; утечка базы ключей не даёт.

* **Ключ действует от имени должности в отделе** (otp_role + department_id), а
  не от имени человека. Так периметр считают те же функции, что у помощника
  вики, и он меняется вместе с правами отдела.

* **Отзыв, а не удаление.** Отозванный ключ остаётся строкой с revoked_at: его
  журнал — свидетельство, и вопрос «кто и когда спрашивал» задают постфактум.

* **Журнал — строка на вызов**, включая отказы (нет данных, плохой запрос,
  предел). Текст вопроса пишется целиком: по нему супервайзеры видят, чего не
  хватает в базе знаний. Текст отданных фрагментов НЕ пишется — только ссылки
  на них (статья, заголовок, оценка): иначе журнал рос бы на мегабайты в день.

* **Срок хранения журнала — KNOWLEDGE_API_CALLS_DAYS (90 дней)**, чистит
  ежедневная джоба (queries.cleanup_calls).
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Исходы вызова. Порядок — от «всё хорошо» к «ничего не вышло».
OUTCOMES = ('ok', 'no_data', 'park_unavailable', 'bad_request', 'limit', 'error')

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS knowledge_api_keys (
        id             SERIAL PRIMARY KEY,
        name           VARCHAR(120) NOT NULL,
        key_hash       CHAR(64) NOT NULL UNIQUE,
        key_mark       VARCHAR(16) NOT NULL,
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
    CREATE TABLE IF NOT EXISTS knowledge_api_calls (
        id               BIGSERIAL PRIMARY KEY,
        key_id           INTEGER NOT NULL REFERENCES knowledge_api_keys(id) ON DELETE CASCADE,
        request_id       UUID NOT NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT """ + _NOW + """,
        ip_address       VARCHAR(64),
        user_agent       VARCHAR(300),
        park_id          INTEGER,
        park_name        VARCHAR(255),
        question         TEXT NOT NULL DEFAULT '',
        outcome          VARCHAR(16) NOT NULL,
        http_status      SMALLINT,
        fragments_count  SMALLINT NOT NULL DEFAULT 0,
        fragments        JSONB,
        question_id      BIGINT,
        latency_ms       INTEGER,
        degraded         BOOLEAN NOT NULL DEFAULT FALSE,
        error_text       TEXT
    );
    """,
    # Журнал ключа — страницей от новых к старым.
    "CREATE INDEX IF NOT EXISTS idx_knowledge_api_calls_key "
    "ON knowledge_api_calls (key_id, created_at DESC, id DESC);",
    # Чистка по сроку.
    "CREATE INDEX IF NOT EXISTS idx_knowledge_api_calls_created "
    "ON knowledge_api_calls (created_at);",
]


def init_knowledge_api_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Обе таблицы на месте. Иначе ручка отвечает 503, а не падает на INSERT."""
    cursor.execute("SELECT to_regclass('public.knowledge_api_keys') IS NOT NULL "
                   "AND to_regclass('public.knowledge_api_calls') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
