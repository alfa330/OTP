# -*- coding: utf-8 -*-
"""Схема раздела «Аренда авто». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_rental_schema(cursor) — своим
SAVEPOINT'ом, как у «Кодов 2FA»: не развернулась — раздел недоступен, портал работает.

Ночной отчёт (#398):

* **Отчёт — снимок на момент сбора**, а не живая выборка: ТЗ п. 4 требует историю, «данные
  за предыдущие даты и время заполнения». Один отчёт на пару (вид, плановый момент) —
  уникальный ключ; повторный сбор того же слота обновляет строки, а не плодит отчёты.

* **«Состояние» хранит, откуда оно**: `address` — подставлено по адресу GPS, `manual` —
  поставила ночная смена. Пересбор отчёта («Обновить») заново считает только подставленное,
  ручное остаётся.

* **Журнал ручных правок не ссылается на строку внешним ключом**: строку пересбор может
  убрать (машина пропала из GPS), а запись «кто и когда менял» должна остаться. Имя автора —
  снимком, как в журнале «Кодов 2FA».
"""

_STATEMENTS = [

    # kind: night (01:00) / morning (07:00). status: collecting / ready / failed.
    # sources — по источнику {ok, count, error}: что ответило в этом сборе.
    """
    CREATE TABLE IF NOT EXISTS rental_night_reports (
        id             SERIAL PRIMARY KEY,
        kind           VARCHAR(16) NOT NULL,
        slot_at        TIMESTAMP NOT NULL,
        status         VARCHAR(16) NOT NULL DEFAULT 'collecting',
        collected_at   TIMESTAMP,
        sources        JSONB NOT NULL DEFAULT '{}'::jsonb,
        error          TEXT,
        created_by     INTEGER,
        started_at     TIMESTAMP NOT NULL,
        created_at     TIMESTAMP NOT NULL,
        updated_at     TIMESTAMP NOT NULL,
        CONSTRAINT rental_night_reports_slot UNIQUE (kind, slot_at)
    )
    """,

    # gps_status: online / offline / none. rent_status: in_rent / no; NULL — Yume не ответил.
    # state: base / repair / sale; state_source: address / manual.
    """
    CREATE TABLE IF NOT EXISTS rental_night_rows (
        id             SERIAL PRIMARY KEY,
        report_id      INTEGER NOT NULL REFERENCES rental_night_reports(id) ON DELETE CASCADE,
        plate          VARCHAR(16) NOT NULL,
        gps_site       VARCHAR(32),
        gps_sites      VARCHAR(80),
        gps_status     VARCHAR(16) NOT NULL,
        signal_at      TIMESTAMP,
        address        TEXT,
        lat            DOUBLE PRECISION,
        lon            DOUBLE PRECISION,
        rent_status    VARCHAR(16),
        in_fleet       BOOLEAN NOT NULL DEFAULT FALSE,
        model          VARCHAR(120),
        state          VARCHAR(16),
        state_source   VARCHAR(16),
        state_by       INTEGER,
        state_at       TIMESTAMP,
        CONSTRAINT rental_night_rows_plate UNIQUE (report_id, plate)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS rental_night_state_log (
        id               SERIAL PRIMARY KEY,
        report_id        INTEGER NOT NULL,
        plate            VARCHAR(16) NOT NULL,
        old_state        VARCHAR(16),
        new_state        VARCHAR(16),
        changed_by       INTEGER,
        changed_by_name  VARCHAR(200),
        changed_at       TIMESTAMP NOT NULL
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_rental_night_reports_slot ON rental_night_reports (slot_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rental_night_state_log_report ON rental_night_state_log (report_id, plate)",
]


def init_rental_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)
