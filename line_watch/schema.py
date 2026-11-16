# -*- coding: utf-8 -*-
"""Журнал сторожа линий Binotel.

Идемпотентно (CREATE … IF NOT EXISTS). Разворачивается при старте из Database._init_db через
init_line_watch_schema(cursor) под своим савпоинтом — как «Звонки Binotel» и ключи партнёров.

binotel_line_watch — строка на случай:
  * расхождение состояния (cabinet_closed / cabinet_open / cabinet_stale / line_offline) —
    длится: started_at … ended_at, пока не кончилось — ended_at пуст;
  * событие звонка (queue_held / no_pickup / group_drop) — момент: started_at и ended_at
    заданы сразу, general_call_id — звонок Binotel.
Номеров клиентов и текста разговоров здесь нет: только линия, сотрудник, статусы и секунды.
"""

KEEP_DAYS = 120

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS binotel_line_watch (
        id              BIGSERIAL PRIMARY KEY,
        kind            VARCHAR(32) NOT NULL,
        line            VARCHAR(16) NOT NULL,
        operator_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        direction       VARCHAR(8),
        started_at      TIMESTAMPTZ NOT NULL,
        last_seen_at    TIMESTAMPTZ NOT NULL,
        ended_at        TIMESTAMPTZ,
        phone_status    VARCHAR(32),
        presence        VARCHAR(32),
        sip_status      VARCHAR(16),
        general_call_id VARCHAR(32),
        details         JSONB NOT NULL DEFAULT '{}'::jsonb,
        heal_action     VARCHAR(32),
        heal_at         TIMESTAMPTZ,
        heal_result     VARCHAR(255)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_binotel_line_watch_started ON binotel_line_watch (started_at DESC);",
    # Открытый случай у линии и вида один: повторный старт процесса продолжает его, а не плодит.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_binotel_line_watch_open
        ON binotel_line_watch (line, kind) WHERE ended_at IS NULL;
    """,
    # Вебхук Binotel повторяется, если мы не ответили: событие звонка — одно на звонок, вид и линию.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_binotel_line_watch_call
        ON binotel_line_watch (general_call_id, kind, line) WHERE general_call_id IS NOT NULL;
    """,
]


def init_line_watch_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Таблица журнала на месте. Имя без схемы — по search_path (тесты разворачивают в своей)."""
    cursor.execute("SELECT to_regclass('binotel_line_watch') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row) and row[0] is True
