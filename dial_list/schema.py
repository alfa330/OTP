# -*- coding: utf-8 -*-
"""Схема раздела «Обзвон из телефона». Все таблицы с префиксом dial_list_.

Идемпотентно (CREATE ... IF NOT EXISTS), вызывается один раз при старте из
Database._init_db через init_dial_list_schema(cursor) — как у op_funnel и
oktell_guard. Порядок внутри: CREATE TABLE → ALTER → CREATE INDEX.

Модель:

    dial_list_department_settings  на отдел: включён ли режим, размер порции,
                                   правила повтора, имя компании Binotel (по нему
                                   сервер находит ключ API в своём окружении),
                                   что показывать линии оператора как номер
                                   звонящего. Секретов в таблице нет.
    dial_list_user_settings        персональное включение (NULL = как у отдела):
                                   нужно для пилота на одном-двух операторах,
                                   потому что телефон раскатывается всем сразу.
    dial_list_lead_batches         загрузки списков водителей (файл ФИО+телефон)
                                   отделом-обзвонщиком.
    dial_list_leads                база водителей ОТДЕЛА: ФИО, номер, сколько раз
                                   звонили, дозвонились ли. Номер уникален в
                                   пределах отдела: у каждого отдела-обзвонщика
                                   свои списки (решение владельца 22.09.2026).
    dial_list_portions             выдача: кому, когда, сколько строк, когда
                                   закрыта (все строки обработаны).
    dial_list_assignments          строка выдачи: лид → оператор; состояние
                                   issued/done и итог подтверждённой попытки.
    dial_list_attempts             попытка звонка: generalCallID от Binotel,
                                   события с телефона, финальная disposition и
                                   откуда она пришла (webhook / poll / api_error /
                                   timeout).
    dial_list_webhook_log          сырые POST'ы Binotel «API Call Completed» —
                                   на время внедрения, чтобы видеть, что именно
                                   присылает АТС; чистится по возрасту.

Почему свои таблицы настроек, а не колонки в sip_department_config:
_SIP_OPERATOR_SELECT читает строки по индексам, и каждая новая колонка там —
правка в трёх местах с риском сдвинуть соседей. Здесь настройки читает только
этот модуль.
"""

DDL = [
    """
    CREATE TABLE IF NOT EXISTS dial_list_department_settings (
        department_id INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        portion_size SMALLINT NOT NULL DEFAULT 20,
        max_attempts SMALLINT NOT NULL DEFAULT 3,
        retry_after_hours SMALLINT NOT NULL DEFAULT 24,
        binotel_company VARCHAR(32) NOT NULL DEFAULT 'remote_cc',
        caller_id_for_employee VARCHAR(32) NOT NULL DEFAULT '',
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_user_settings (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        enabled BOOLEAN,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_lead_batches (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        file_name TEXT NOT NULL DEFAULT '',
        rows_total INTEGER NOT NULL DEFAULT 0,
        rows_new INTEGER NOT NULL DEFAULT 0,
        rows_duplicate INTEGER NOT NULL DEFAULT 0,
        rows_invalid INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_leads (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        phone_norm VARCHAR(16) NOT NULL,
        full_name TEXT NOT NULL DEFAULT '',
        first_batch_id UUID REFERENCES dial_list_lead_batches(id) ON DELETE SET NULL,
        last_batch_id UUID REFERENCES dial_list_lead_batches(id) ON DELETE SET NULL,
        upload_count INTEGER NOT NULL DEFAULT 1,
        status VARCHAR(16) NOT NULL DEFAULT 'new'
            CHECK (status IN ('new', 'in_progress', 'done', 'excluded')),
        attempts_total SMALLINT NOT NULL DEFAULT 0,
        last_attempt_at TIMESTAMP WITH TIME ZONE,
        answered_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (department_id, phone_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_portions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        size SMALLINT NOT NULL,
        issued_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP WITH TIME ZONE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_assignments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        portion_id UUID NOT NULL REFERENCES dial_list_portions(id) ON DELETE CASCADE,
        lead_id UUID NOT NULL REFERENCES dial_list_leads(id) ON DELETE CASCADE,
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        position SMALLINT NOT NULL,
        state VARCHAR(16) NOT NULL DEFAULT 'issued'
            CHECK (state IN ('issued', 'done')),
        result VARCHAR(16) NOT NULL DEFAULT ''
            CHECK (result IN ('', 'answered', 'busy', 'no_answer', 'other', 'failed')),
        attempts SMALLINT NOT NULL DEFAULT 0,
        done_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_attempts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        assignment_id UUID NOT NULL REFERENCES dial_list_assignments(id) ON DELETE CASCADE,
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        internal_number VARCHAR(64) NOT NULL DEFAULT '',
        general_call_id VARCHAR(32),
        requested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        api_error TEXT NOT NULL DEFAULT '',
        state VARCHAR(16) NOT NULL DEFAULT 'requested'
            CHECK (state IN ('requested', 'failed', 'leg_ringing', 'leg_answered', 'ended', 'finished')),
        disposition VARCHAR(32) NOT NULL DEFAULT '',
        billsec INTEGER NOT NULL DEFAULT 0,
        waitsec INTEGER NOT NULL DEFAULT 0,
        final_source VARCHAR(16) NOT NULL DEFAULT '',
        phone_event_at TIMESTAMP WITH TIME ZONE,
        phone_ended_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_webhook_log (
        id BIGSERIAL PRIMARY KEY,
        received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remote_addr VARCHAR(64) NOT NULL DEFAULT '',
        general_call_id VARCHAR(32) NOT NULL DEFAULT '',
        matched BOOLEAN NOT NULL DEFAULT FALSE,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    # Секреты в базе не хранятся (решение владельца 23.09.2026: ключи и токены —
    # только в окружении Render). Колонки первой версии удаляем вместе со всем,
    # что в них могли успеть записать; имя компании добавляем.
    "ALTER TABLE dial_list_department_settings DROP COLUMN IF EXISTS binotel_api_key",
    "ALTER TABLE dial_list_department_settings DROP COLUMN IF EXISTS binotel_api_secret",
    "ALTER TABLE dial_list_department_settings DROP COLUMN IF EXISTS webhook_token",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS binotel_company VARCHAR(32) NOT NULL DEFAULT 'remote_cc'",
    # Один лид — максимум в одной ОТКРЫТОЙ выдаче: два оператора не должны
    # звонить одному водителю одновременно.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_assignments_open_lead
        ON dial_list_assignments(lead_id) WHERE state = 'issued'
    """,
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_pool ON dial_list_leads(department_id, status, attempts_total, last_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_portion ON dial_list_assignments(portion_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_lead ON dial_list_assignments(lead_id, state)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_portions_operator_open ON dial_list_portions(operator_id, closed_at)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_attempts_general_call
        ON dial_list_attempts(general_call_id) WHERE general_call_id IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_assignment ON dial_list_attempts(assignment_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_operator_state ON dial_list_attempts(operator_id, state)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_requested ON dial_list_attempts(requested_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_webhook_log_received ON dial_list_webhook_log(received_at)",
]


def init_dial_list_schema(cursor):
    """Создать/дополнить схему. Безопасно вызывать на каждом старте."""
    for statement in DDL:
        cursor.execute(statement)
