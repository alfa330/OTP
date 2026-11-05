# -*- coding: utf-8 -*-
"""Схема «Звонков Binotel» для партнёров: копия звонков фронт-офиса и состояние сверки.

Идемпотентно (CREATE … IF NOT EXISTS). Разворачивается при старте из Database._init_db через
init_calls_api_schema(cursor) под своим савпоинтом — как ключи партнёров.

* calls_api_calls — строка на звонок (generalCallID Binotel), только звонки области
  (rules.DEPARTMENT_CODES): по ней партнёр забирает историю и ссылку на запись, а событие
  уходит один раз — при первом появлении звонка (или его уточнении). Номер водителя и
  сотрудник здесь хранятся, поэтому срок — KEEP_DAYS, чистит ежедневная джоба.
* calls_api_state — ключ → значение: ID кабинета Binotel удалённого КЦ (узнаётся из ответов
  Binotel по ключу кабинета, rules.COMPANY) и момент последней сверки с историей.
"""

KEEP_DAYS = 90

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS calls_api_calls (
        general_call_id     VARCHAR(32) PRIMARY KEY,
        company_id          VARCHAR(32) NOT NULL DEFAULT '',
        internal_number     VARCHAR(64) NOT NULL,
        line_department_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        department_id       INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        direction           VARCHAR(8) NOT NULL,
        phone               VARCHAR(16),
        external_raw        VARCHAR(32) NOT NULL DEFAULT '',
        disposition         VARCHAR(32) NOT NULL DEFAULT '',
        result              VARCHAR(16) NOT NULL,
        billsec             INTEGER NOT NULL DEFAULT 0,
        waitsec             INTEGER NOT NULL DEFAULT 0,
        started_at          TIMESTAMPTZ NOT NULL,
        recording_status    VARCHAR(32) NOT NULL DEFAULT '',
        source              VARCHAR(16) NOT NULL DEFAULT 'webhook',
        received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_calls_api_calls_started ON calls_api_calls (started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_calls_api_calls_updated ON calls_api_calls (updated_at, general_call_id);",
    "CREATE INDEX IF NOT EXISTS idx_calls_api_calls_phone ON calls_api_calls (phone, started_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_calls_api_calls_user ON calls_api_calls (user_id, started_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS calls_api_state (
        key         VARCHAR(64) PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,
]


def init_calls_api_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Обе таблицы на месте. Сравнение с True — см. partner_keys.schema.schema_is_ready. Имена без
    схемы — по search_path: на проде это public, а тесты разворачивают таблицы в своей схеме."""
    cursor.execute("SELECT to_regclass('calls_api_calls') IS NOT NULL "
                   "AND to_regclass('calls_api_state') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row) and row[0] is True
