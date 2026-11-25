# -*- coding: utf-8 -*-
"""Схема общей очереди запросов к Oktell (oktell_live/shared.py). Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_oktell_live_schema(cursor).

* `oktell_jobs` — задание живёт секунды: от записи вызывающим до чтения результата им же (строка
  удаляется в момент чтения). Брошенные (процесс умер, не дождавшись) подметает shared.PgStore.
  Время — TIMESTAMPTZ и now() базы: сроки сравнивают разные процессы, а часы у них свои.
* `oktell_workers` — что исполнитель коннектора знает и умеет; строка обновляется каждым его
  опросом, «на связи» = опрашивал недавно.
"""

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS oktell_jobs (
        id          VARCHAR(64) PRIMARY KEY,
        kind        VARCHAR(16) NOT NULL,
        job         JSONB NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        deadline    TIMESTAMPTZ NOT NULL,
        taken_at    TIMESTAMPTZ,
        worker      VARCHAR(120),
        done_at     TIMESTAMPTZ,
        result      JSONB,
        audio       BYTEA
    )
    """,
    # Исполнитель забирает старейшее незабранное — по этому индексу.
    """
    CREATE INDEX IF NOT EXISTS oktell_jobs_waiting_idx
        ON oktell_jobs (created_at) WHERE taken_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS oktell_jobs_deadline_idx ON oktell_jobs (deadline)
    """,
    """
    CREATE TABLE IF NOT EXISTS oktell_workers (
        worker   VARCHAR(120) PRIMARY KEY,
        info     JSONB NOT NULL,
        seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


def init_oktell_live_schema(cursor):
    """Разворачивает схему. Курсор из _init_db, транзакцией правит вызывающий."""
    for statement in _STATEMENTS:
        cursor.execute(statement)
