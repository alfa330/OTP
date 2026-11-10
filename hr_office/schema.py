# -*- coding: utf-8 -*-
"""Таблицы разделов «Прием», «Табель учета рабочего времени» и «АВР». Создаются при подключении Blueprint, идемпотентно.

Личные данные (ИИН, удостоверение, адреса) — в своей таблице, а не в users:
так они не попадают ни в список и выгрузку «Учета сотрудников», ни в историю
карточки значениями (разбор 10.11.2026), и читает их только своя ручка HR.
"""

import logging

from . import rules

logger = logging.getLogger(__name__)

# ТЗ п. 3: «для ОП и СЗоВ по умолчанию — ГПХ на ИП Eazy Go Rent, для операторов TEZ — ИП GaBa».
# Без этих строк справочника умолчанию не на что указать. Заводятся только названием:
# реквизиты (БИН, банк, «в лице…») — это данные компании, их вносит HR в «Компаниях».
DEFAULT_COMPANIES = (('Eazy Go Rent', 'ip'), ('GaBa', 'ip'))

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS hr_companies (
        id              SERIAL PRIMARY KEY,
        name            VARCHAR(200) NOT NULL,
        kind            VARCHAR(8) NOT NULL DEFAULT 'too' CHECK (kind IN ('ip', 'too', 'other')),
        bin             VARCHAR(12),
        legal_address   TEXT,
        contacts        TEXT,
        bank_name       VARCHAR(200),
        iik             VARCHAR(34),
        bik             VARCHAR(16),
        kbe             VARCHAR(4),
        in_person       TEXT,
        signer_name     VARCHAR(120),
        signer_position VARCHAR(120),
        city            VARCHAR(80),
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        created_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        updated_by      INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_hr_companies_name ON hr_companies (lower(name))",
    """
    CREATE TABLE IF NOT EXISTS hr_employee_data (
        user_id              INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        iin                  VARCHAR(12),
        doc_type             VARCHAR(16) CHECK (doc_type IN ('id_card', 'passport') OR doc_type IS NULL),
        doc_number           VARCHAR(32),
        doc_issuer           VARCHAR(120),
        doc_issued_on        DATE,
        citizenship          VARCHAR(80),
        birth_place          VARCHAR(200),
        registration_address TEXT,
        actual_address       TEXT,
        project              VARCHAR(40),
        timesheet_group      VARCHAR(120),
        schedule_pattern     VARCHAR(8),
        day_hours            NUMERIC(4, 2),
        updated_at           TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        updated_by           INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_hr_employee_data_project ON hr_employee_data (project)",
    """
    CREATE TABLE IF NOT EXISTS hr_contracts (
        id               SERIAL PRIMARY KEY,
        user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        company_id       INTEGER REFERENCES hr_companies(id) ON DELETE SET NULL,
        employment_type  VARCHAR(8) CHECK (employment_type IN ('gph', 'of', 'smz') OR employment_type IS NULL),
        number           VARCHAR(40),
        signed_on        DATE,
        liability_number VARCHAR(40),
        amount           NUMERIC(12, 2),
        job_title        VARCHAR(200),
        is_current       BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at       TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        updated_by       INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_hr_contracts_current ON hr_contracts (user_id) WHERE is_current",
    """
    CREATE TABLE IF NOT EXISTS hr_doc_counters (
        company_id INTEGER NOT NULL REFERENCES hr_companies(id) ON DELETE CASCADE,
        year       INTEGER NOT NULL,
        kind       VARCHAR(16) NOT NULL,
        last_value INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (company_id, year, kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hr_acts (
        id           SERIAL PRIMARY KEY,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        contract_id  INTEGER REFERENCES hr_contracts(id) ON DELETE SET NULL,
        company_id   INTEGER REFERENCES hr_companies(id) ON DELETE SET NULL,
        number       VARCHAR(40) NOT NULL,
        act_date     DATE NOT NULL,
        period_from  DATE NOT NULL,
        period_to    DATE NOT NULL,
        work_name    TEXT NOT NULL,
        amount       NUMERIC(12, 2),
        created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_hr_acts_user ON hr_acts (user_id, period_to DESC)",
    """
    CREATE TABLE IF NOT EXISTS hr_timesheet_edits (
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day        DATE NOT NULL,
        value      VARCHAR(8),
        minutes    VARCHAR(40),
        edited_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        edited_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        PRIMARY KEY (user_id, day)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hr_timesheet_edit_log (
        id          SERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day         DATE NOT NULL,
        field       VARCHAR(8) NOT NULL,
        old_value   VARCHAR(40),
        new_value   VARCHAR(40),
        computed    VARCHAR(40),
        edited_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        edited_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty')
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_hr_timesheet_edit_log_day ON hr_timesheet_edit_log (user_id, day)",
    """
    CREATE TABLE IF NOT EXISTS hr_timesheet_notes (
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        month      DATE NOT NULL,
        note       TEXT,
        edited_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        edited_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        PRIMARY KEY (user_id, month)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hr_timesheet_closures (
        project    VARCHAR(40) NOT NULL,
        month      DATE NOT NULL,
        snapshot   JSONB NOT NULL,
        closed_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        closed_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty'),
        PRIMARY KEY (project, month)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hr_audit_log (
        id          SERIAL PRIMARY KEY,
        section     VARCHAR(16) NOT NULL CHECK (section IN ('intake', 'timesheet', 'acts')),
        action      VARCHAR(32) NOT NULL,
        user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        summary     TEXT NOT NULL,
        details     JSONB,
        actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty')
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_hr_audit_log_section ON hr_audit_log (section, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_hr_audit_log_user ON hr_audit_log (user_id) WHERE user_id IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS hr_templates (
        kind         VARCHAR(24) PRIMARY KEY,
        file_name    VARCHAR(200) NOT NULL,
        content      BYTEA NOT NULL,
        uploaded_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Almaty')
    )
    """,
)


def seed_default_companies(cursor):
    """Компании умолчаний ТЗ п. 3 — если их ещё нет (сверка по ключу названия: «ИП Eazy Go Rent»,
    «Eazy Go Rent» и «ИП "Eazy Go Rent"» — одна компания). Возвращает заведённые названия."""
    cursor.execute('SELECT name FROM hr_companies')
    present = {rules.company_key(row[0]) for row in cursor.fetchall()}
    added = []
    for name, kind in DEFAULT_COMPANIES:
        if rules.company_key(name) not in present:
            cursor.execute("INSERT INTO hr_companies (name, kind, is_active, city) VALUES (%s, %s, TRUE, 'Алматы')",
                           (name, kind))
            added.append(name)
    return added


def ensure(db):
    with db._get_cursor() as cursor:
        for statement in STATEMENTS:
            cursor.execute(statement)
        added = seed_default_companies(cursor)
    if added:
        logger.info('Прием: в справочник компаний добавлены умолчания ТЗ п. 3: %s', ', '.join(added))
    logger.info('Прием, табель, АВР: таблицы на месте')
