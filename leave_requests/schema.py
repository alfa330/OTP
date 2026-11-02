# -*- coding: utf-8 -*-
"""Схема заявок на отпуск и Б/С. Таблицы с префиксом leave_.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Вызывается при старте из
Database._init_db (init_leave_requests_schema) под своим SAVEPOINT — сломанная
миграция раздела не роняет базу.

    leave_request_settings  отдел: включено ли, какие виды, нужен ли шаг СВ,
                            с какой длины Б/С по умолчанию не идёт в стаж
    leave_requests          заявка: вид, период, причина, шаг маршрута
    leave_request_events    журнал заявки: кто, что, с какими датами и словами

Решения, которые видны в DDL:

* **Комментарии живут в журнале, а не в заявке.** У заявки их может быть
  несколько (предложение дат, ответ оператора, отказ), и каждый привязан к
  шагу. Колонки «последний комментарий» затирали бы друг друга.
* **supervisor_id — снимок СВ группы на момент подачи.** Он нужен колоколу и
  Telegram: вычислять СВ через группу в триггере значило бы повторить правило
  членства на PL/pgSQL. На право согласовать он не влияет — там периметр отдела.
* **status_period_id без внешнего ключа.** Утверждённый период дальше живёт в
  графике: его правят и режут руками, и заявка не должна мешать удалению.
* **Отдел СЗоВ включается при первом старте** (ON CONFLICT DO NOTHING): дальше
  настройки меняет админ, и выключенный отдел старт не включит обратно.
"""

# Время — наивное по Алматы, как во всём портале: сессия базы на проде в UTC,
# и голый CURRENT_TIMESTAMP записал бы в журнал заявки час на пять раньше.
_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS leave_request_settings (
        department_id INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        allow_annual_leave BOOLEAN NOT NULL DEFAULT TRUE,
        allow_bs BOOLEAN NOT NULL DEFAULT TRUE,
        supervisor_step BOOLEAN NOT NULL DEFAULT TRUE,
        long_bs_days INTEGER NULL,
        updated_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT leave_request_settings_kinds_check
            CHECK (NOT enabled OR allow_annual_leave OR allow_bs),
        CONSTRAINT leave_request_settings_long_bs_check
            CHECK (long_bs_days IS NULL OR long_bs_days BETWEEN 1 AND 366)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS leave_requests (
        id SERIAL PRIMARY KEY,
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
        supervisor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        kind VARCHAR(16) NOT NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        reason TEXT NULL,
        status VARCHAR(16) NOT NULL,
        proposed_start_date DATE NULL,
        proposed_end_date DATE NULL,
        exclude_from_tenure BOOLEAN NOT NULL DEFAULT FALSE,
        status_period_id INTEGER NULL,
        decided_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        decided_at TIMESTAMP NULL,
        operator_seen_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT leave_requests_kind_check CHECK (kind IN ('annual_leave', 'bs')),
        CONSTRAINT leave_requests_status_check
            CHECK (status IN ('supervisor', 'head', 'proposal', 'approved', 'rejected', 'cancelled')),
        CONSTRAINT leave_requests_period_check CHECK (end_date >= start_date),
        CONSTRAINT leave_requests_proposal_check CHECK (
            (proposed_start_date IS NULL) = (proposed_end_date IS NULL)
            AND (proposed_end_date IS NULL OR proposed_end_date >= proposed_start_date)
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS leave_request_events (
        id SERIAL PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES leave_requests(id) ON DELETE CASCADE,
        actor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        actor_name TEXT NULL,
        action VARCHAR(24) NOT NULL,
        status_after VARCHAR(16) NOT NULL,
        start_date DATE NULL,
        end_date DATE NULL,
        comment TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    # Свои заявки оператора, свежие сверху.
    """
    CREATE INDEX IF NOT EXISTS idx_leave_requests_operator
        ON leave_requests(operator_id, created_at DESC)
    """,
    # Очередь главы и счётчик колокола по отделу.
    """
    CREATE INDEX IF NOT EXISTS idx_leave_requests_status_department
        ON leave_requests(status, department_id)
    """,
    # Колокол конкретного СВ.
    """
    CREATE INDEX IF NOT EXISTS idx_leave_requests_supervisor_status
        ON leave_requests(supervisor_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_leave_request_events_request
        ON leave_request_events(request_id, id)
    """,
)

# Первый отдел процесса — СЗоВ (решение владельца 02.11.2026: «на данный
# момент делай пока для СзоВ»). 30 дней — подсказка главе при утверждении Б/С,
# решение о стаже всё равно принимает он.
_SEED = """
    INSERT INTO leave_request_settings
        (department_id, enabled, allow_annual_leave, allow_bs, supervisor_step, long_bs_days)
    SELECT d.id, TRUE, TRUE, TRUE, TRUE, 30
      FROM departments d
     WHERE lower(COALESCE(d.code, '')) = 'szov'
    ON CONFLICT (department_id) DO NOTHING
"""


def init_leave_requests_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement % {'now': _NOW} if '%(now)s' in statement else statement)
    cursor.execute(_SEED)


def schema_is_ready(cursor):
    # Без «public.»: на проде это public, тесты разворачивают свою схему.
    cursor.execute("SELECT to_regclass('leave_requests') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
