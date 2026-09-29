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
        period DATE NOT NULL DEFAULT (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))::date,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_dial_list_leads_period_phone UNIQUE (department_id, period, phone_norm)
    )
    """,
    # Итоги звонка (запрос владельца 23.09.2026): справочник отдела, оператор
    # обязан выбрать один после каждого разговора; цвет и порядок задаёт
    # руководитель. Не удаляются, а выключаются — история на них ссылается.
    """
    CREATE TABLE IF NOT EXISTS dial_list_outcomes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        name VARCHAR(64) NOT NULL,
        color VARCHAR(7) NOT NULL DEFAULT '#8E8E93',
        position SMALLINT NOT NULL DEFAULT 0,
        requeue BOOLEAN NOT NULL DEFAULT FALSE,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Скрипт разговора отдела (владелец, 25.09.2026): основной текст с лёгкой
    # разметкой и быстрые вопросы с ответами — оператор читает их на телефоне
    # прямо во время звонка. Версия растёт при каждом сохранении: по ней телефон
    # понимает, что пора перечитать. Вопросы не удаляются, а выключаются.
    """
    CREATE TABLE IF NOT EXISTS dial_list_scripts (
        department_id INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        body TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_script_questions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        position SMALLINT NOT NULL DEFAULT 0,
        question VARCHAR(200) NOT NULL,
        answer TEXT NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    # Журнал водителей (запрос владельца 23.09.2026): руководитель видит по каждому
    # человеку ответственного, статус и историю звонков и может вручную вернуть
    # его в список или исключить. Ручные действия — отдельной таблицей, чтобы в
    # истории было видно, кто и когда вмешался; заметка руководителя — на лиде.
    """
    CREATE TABLE IF NOT EXISTS dial_list_lead_events (
        id BIGSERIAL PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES dial_list_leads(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        kind VARCHAR(16) NOT NULL
            CHECK (kind IN ('requeue', 'exclude', 'restore', 'note')),
        note TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT ''",
    # Базы по месяцам (запрос владельца 23.09.2026): один и тот же номер может
    # повторяться в базе другого месяца, внутри месяца — нет. Уникальность
    # переезжает с (отдел, номер) на (отдел, месяц, номер); старое ограничение
    # снимаем по автоимени Postgres.
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS period DATE NOT NULL"
    " DEFAULT (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))::date",
    "ALTER TABLE dial_list_leads DROP CONSTRAINT IF EXISTS dial_list_leads_department_id_phone_norm_key",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_dial_list_leads_period_phone') THEN
            ALTER TABLE dial_list_leads
                ADD CONSTRAINT uq_dial_list_leads_period_phone UNIQUE (department_id, period, phone_norm);
        END IF;
    END $$
    """,
    "ALTER TABLE dial_list_lead_batches ADD COLUMN IF NOT EXISTS period DATE",
    # Какой месяц сейчас обзванивается; NULL — текущий календарный.
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS active_period DATE",
    # Итог и комментарий оператора по попытке; когда телефон принял плечо —
    # по этому признаку сервер понимает, что разговор был и итог обязателен.
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS outcome_id UUID REFERENCES dial_list_outcomes(id) ON DELETE SET NULL",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS operator_comment TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS outcome_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS leg_answered_at TIMESTAMP WITH TIME ZONE",
    # Отбой оператором (решение владельца 24.09.2026): когда оператор сам нажал
    # «Завершить» (operator_hangup_at) и Binotel сообщил, что водитель не ответил,
    # попытка отменяется (cancelled) — не считается строке и лиду, итог по ней не
    # ставится. leg_sec — сколько секунд плечо было принято телефоном (для журнала).
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS operator_hangup_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS cancelled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS leg_sec INTEGER NOT NULL DEFAULT 0",
    # Типы итога (запрос владельца 29.09.2026): у итога может быть свой список
    # вложенных типов («Отказ» → «Дорого», «Уже работает в другом парке»), чтобы
    # типизировать сами итоги. Отдельная таблица, а не parent_id в
    # dial_list_outcomes: иначе каждый запрос к итогам (сохранение списком,
    # выдача телефону, фишки журнала, лимит в 30) начал бы видеть типы как
    # итоги. Цвета и «перезвонить» у типа нет — они берутся у итога. Типы не
    # удаляются, а выключаются: история попыток на них ссылается.
    """
    CREATE TABLE IF NOT EXISTS dial_list_outcome_subtypes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        outcome_id UUID NOT NULL REFERENCES dial_list_outcomes(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        name VARCHAR(64) NOT NULL,
        position SMALLINT NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Тип пишется в той же попытке, что и итог; NULL — без типа (старые
    # телефоны и итоги без типов).
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS outcome_subtype_id UUID"
    " REFERENCES dial_list_outcome_subtypes(id) ON DELETE SET NULL",
    # ИИН и подписание документов (запрос владельца 29.09.2026, см. signing.py и
    # sign_check.py). ИИН обязателен в файле базы, по нему Sapar говорит, подписал
    # ли водитель документы за прошлый месяц. '' — строки, загруженные до ИИН.
    #   sign_status     общий статус пакета документов (signing.SIGN_RANK), '' — не
    #                   проверяли; CHECK нет намеренно: статусы задаёт Sapar
    #   sign_docs       номер/статус/время подписи каждого документа — для карточки
    #   sign_checked_at когда Sapar ответил последний раз (по нему «пора ли снова»)
    #   signed_at       когда водитель подписал (последняя подпись пакета); не NULL —
    #                   подписал: из пула ушёл навсегда, больше не проверяется
    #   success_*       кому засчитана успешка; success_resolved_at — решение принято
    #                   (NULL при signed_at — ждём конца звонка, начатого до подписи).
    #                   success_attempt_id без FK: лид → попытки → выдачи → лид был бы
    #                   кольцом внешних ключей с каскадами навстречу друг другу.
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS iin VARCHAR(12) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_status VARCHAR(16) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_docs JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_checked_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_error TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS signed_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS signed_detected_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_attempt_id UUID",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_operator_id INTEGER"
    " REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_resolved_at TIMESTAMP WITH TIME ZONE",
    # Строки файла без ИИН или с ошибкой в нём — не загружаются, считаются отдельно.
    "ALTER TABLE dial_list_lead_batches ADD COLUMN IF NOT EXISTS rows_bad_iin INTEGER NOT NULL DEFAULT 0",
    # Один лид — максимум в одной ОТКРЫТОЙ выдаче: два оператора не должны
    # звонить одному водителю одновременно.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_assignments_open_lead
        ON dial_list_assignments(lead_id) WHERE state = 'issued'
    """,
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_pool ON dial_list_leads(department_id, status, attempts_total, last_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_portion ON dial_list_assignments(portion_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_lead ON dial_list_assignments(lead_id, state)",
    # Выдачи оператора за месяц: «Мой прогресс» и вкладки итогов на телефоне.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_operator_created ON dial_list_assignments(operator_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_portions_operator_open ON dial_list_portions(operator_id, closed_at)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_attempts_general_call
        ON dial_list_attempts(general_call_id) WHERE general_call_id IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_assignment ON dial_list_attempts(assignment_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_operator_state ON dial_list_attempts(operator_id, state)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_requested ON dial_list_attempts(requested_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_webhook_log_received ON dial_list_webhook_log(received_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_lead_events_lead ON dial_list_lead_events(lead_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_journal ON dial_list_leads(department_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_name ON dial_list_leads(department_id, lower(full_name))",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_period ON dial_list_leads(department_id, period)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_outcomes_department ON dial_list_outcomes(department_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_outcome ON dial_list_attempts(outcome_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_script_questions_department ON dial_list_script_questions(department_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_pending_outcome ON dial_list_attempts(operator_id) WHERE outcome_id IS NULL AND leg_answered_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_outcome_subtypes_outcome ON dial_list_outcome_subtypes(outcome_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_outcome_subtype ON dial_list_attempts(outcome_subtype_id)",
    # ИИН уникален в базе месяца отдела, как и номер: один водитель — одна строка,
    # одна успешка. В DO-блоке: дубль, если он всё же окажется в данных, не должен
    # выключить весь раздел (схема идёт под одним SAVEPOINT) — без индекса дубли
    # ловит проверка при загрузке.
    """
    DO $$
    BEGIN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_leads_period_iin
            ON dial_list_leads(department_id, period, iin) WHERE iin <> '';
    EXCEPTION WHEN unique_violation THEN
        RAISE WARNING 'dial_list: в базах есть повторы ИИН — индекс uq_dial_list_leads_period_iin не создан';
    END $$
    """,
    # Кому пора проверять подписание: только неподписавшие с ИИН.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_sign_due ON dial_list_leads(period, sign_checked_at)"
    " WHERE iin <> '' AND signed_at IS NULL",
    # Успешки оператора по дням (сводка «Операторы», аналитика).
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_success_operator"
    " ON dial_list_leads(success_operator_id, signed_at) WHERE success_attempt_id IS NOT NULL",
    # Подписавшие, по кому решение об успешке ещё не принято.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_success_pending ON dial_list_leads(signed_at)"
    " WHERE signed_at IS NOT NULL AND success_resolved_at IS NULL",
]


def init_dial_list_schema(cursor):
    """Создать/дополнить схему. Безопасно вызывать на каждом старте."""
    for statement in DDL:
        cursor.execute(statement)
