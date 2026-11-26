"""Схема чатов Syntony. Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_syntony_chats_schema(cursor) —
своим SAVEPOINT'ом: не развернулась — приёмник отвечает 503 и Syntony повторяет события,
остальное приложение работает штатно.

Решения, видные в DDL:

* **Одна строка на диалог и на сообщение — последний снимок.** Ключ — id Syntony (GUID
  строкой: формат чужой, тип uuid уронил бы запись на первом не-GUID). Журнала событий
  нет: повтор события безопасен сам по себе (store.py), а диск боевой базы тесный.

* **Время — TIMESTAMPTZ.** Syntony отдаёт UTC; в Алматы переводит тот, кто показывает.

* **Незнакомые поля — в extra (jsonb), разобранные — колонками.** Сырое тело целиком не
  хранится: оно повторило бы колонки и удвоило таблицу.

* **Индексов, кроме ключей, нет**, пока нет запросов, которым они нужны: каждый лишний
  индекс на ~15 тыс. сообщений в сутки — это диск и запись.

* **fillfactor**: диалог переписывается каждым своим сообщением, исходящее сообщение —
  каждым статусом; запас места на странице даёт этим обновлениям идти без новых записей
  в индекс (HOT).
"""

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_conversations (
        id                   TEXT PRIMARY KEY,
        park_id              TEXT,
        park_name            TEXT,
        channel_id           TEXT,
        channel_type         TEXT,
        channel_provider     TEXT,
        channel_name         TEXT,
        channel_phone        TEXT,
        contact_id           TEXT,
        contact_phone        TEXT,
        contact_name         TEXT,
        contact_external_id  TEXT,
        status               TEXT,
        assigned_kind        TEXT,
        assigned_user_id     TEXT,
        assigned_user_name   TEXT,
        message_count        INTEGER,
        created_at           TIMESTAMPTZ,
        last_message_at      TIMESTAMPTZ,
        last_inbound_at      TIMESTAMPTZ,
        closed_at            TIMESTAMPTZ,
        closed_reason        TEXT,
        lead_id              TEXT,
        handoff              JSONB,
        extra                JSONB,
        snapshot_at          TIMESTAMPTZ,
        first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    ) WITH (fillfactor = 70)
    """,

    # first_seen_via: webhook — пришло событием message.created; status — строку завело
    # событие статуса раньше самого сообщения (порядок событий не гарантирован, §9.5).
    # content_at — на какой момент содержимое (occurredAt события).
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_messages (
        id                   TEXT PRIMARY KEY,
        conversation_id      TEXT,
        direction            TEXT,
        sender_type          TEXT,
        origin               TEXT,
        source               TEXT,
        author_id            TEXT,
        author_name          TEXT,
        content_type         TEXT,
        text                 TEXT,
        media                JSONB,
        external_message_id  TEXT,
        reply_to_message_id  TEXT,
        created_at           TIMESTAMPTZ,
        edited_at            TIMESTAMPTZ,
        deleted_at           TIMESTAMPTZ,
        delete_scope         TEXT,
        extra                JSONB,
        content_at           TIMESTAMPTZ,
        status               TEXT,
        status_rank          SMALLINT NOT NULL DEFAULT 0,
        status_at            TIMESTAMPTZ,
        error_code           TEXT,
        error_message        TEXT,
        sent_at              TIMESTAMPTZ,
        delivered_at         TIMESTAMPTZ,
        read_at              TIMESTAMPTZ,
        failed_at            TIMESTAMPTZ,
        first_seen_via       TEXT,
        first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
    ) WITH (fillfactor = 90)
    """,

    # Отметки сторожа API (sync.py): с какого момента читать ленту и диалоги, когда были
    # ночная перечитка и чистка. Несколько строк, переписываются раз в 3 минуты.
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_sync_marks (
        name        TEXT PRIMARY KEY,
        mark        TIMESTAMPTZ NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,

    # Запросы, которым индексы нужны (табло СЗоВ «Чат» и почасовой отчёт по чатам, SZOV-12):
    # сообщения сети за сутки — по времени создания, диалоги за сутки — так же; самое раннее
    # сообщение (с какого момента данные есть) — тот же индекс. Остальное читается по ключам.
    "CREATE INDEX IF NOT EXISTS idx_syntony_chat_messages_created "
    "ON syntony_chat_messages (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_syntony_chat_conversations_created "
    "ON syntony_chat_conversations (created_at)",

    # «Чаты водителей» (driver_chats/syntony.py, 25.11.2026): оператор вводит номер водителя —
    # диалоги ищутся по девяти последним цифрам номера (выражение СЛОВО В СЛОВО как в запросе,
    # иначе планировщик индекс не возьмёт; номер собеседника в диалоге не меняется, так что
    # переписывание диалога его сообщениями остаётся HOT), а переписка двух суток — по диалогу и
    # времени. Здесь, а не в схеме раздела: та разворачивается раньше этой, и на пустой базе
    # индекс по ещё не созданной таблице уронил бы схему раздела целиком.
    r"""
    CREATE INDEX IF NOT EXISTS idx_syntony_chat_conversations_phone_tail
    ON syntony_chat_conversations (right(regexp_replace(contact_phone, '\D', '', 'g'), 9))
    """,
    "CREATE INDEX IF NOT EXISTS idx_syntony_chat_messages_conversation "
    "ON syntony_chat_messages (conversation_id, created_at)",

    # Статусы операторов сети (метод /operator-statuses, statuses.py): интервал «оператор был в
    # статусе с … по …», ended_at IS NULL — статус сейчас. Строка переписывается, пока интервал
    # открыт, потом закрывается один раз. partner_user_ids — id оператора в iCORE (то, что мы
    # передаём в окно чатов); по нему человек находится без сверки ФИО. Срок хранения — тот же,
    # что у переписки: часы на линии для биллинга сворачиваются в syntony_chat_billing_staff.
    """
    CREATE TABLE IF NOT EXISTS syntony_operator_statuses (
        id                TEXT PRIMARY KEY,
        operator_id       TEXT,
        operator_name     TEXT,
        partner_user_ids  TEXT[],
        park_id           TEXT,
        park_name         TEXT,
        status_key        TEXT,
        status_label      TEXT,
        started_at        TIMESTAMPTZ,
        ended_at          TIMESTAMPTZ,
        duration_seconds  INTEGER,
        extra             JSONB,
        fetched_at        TIMESTAMPTZ,
        first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    ) WITH (fillfactor = 80)
    """,
    "CREATE INDEX IF NOT EXISTS idx_syntony_operator_statuses_started "
    "ON syntony_operator_statuses (started_at)",
    # Открытые интервалы читает каждый прогон сторожа и каждый снимок табло — их единицы.
    "CREATE INDEX IF NOT EXISTS idx_syntony_operator_statuses_open "
    "ON syntony_operator_statuses (started_at) WHERE ended_at IS NULL",

    # Биллинг чата по Syntony (billing.py): переписка хранится 30 дней, а биллинг смотрят за
    # любой месяц — поэтому сутки сворачиваются в «чат дня», как обращение Chat2Desk в
    # c2d_requests. Строка — диалог СЗоВ, где водитель писал в эти сутки (правило табло,
    # решение владельца 24.11.2026). id — из своей последовательности с 5·10^12: снимок
    # «Детализации» сравнивает его с request_id Chat2Desk (до 10^8), строки одной выборки
    # не должны сталкиваться. Сутки пересобираются upsert'ом — id строки не меняется.
    "CREATE SEQUENCE IF NOT EXISTS syntony_chat_billing_id_seq START WITH 5000000000000",
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_billing_chats (
        day                  DATE NOT NULL,
        conversation_id      TEXT NOT NULL,
        id                   BIGINT NOT NULL DEFAULT nextval('syntony_chat_billing_id_seq'),
        park                 TEXT,
        channel_id           TEXT,
        channel_name         TEXT,
        transport            TEXT,
        client_phone         TEXT,
        client_name          TEXT,
        started_at           TIMESTAMP NOT NULL,
        is_new               BOOLEAN NOT NULL,
        answered             BOOLEAN NOT NULL,
        first_reply_seconds  REAL,
        inner_reply_seconds  REAL,
        operator_id          INTEGER,
        operator_name        TEXT,
        incoming_messages    INTEGER NOT NULL DEFAULT 0,
        outgoing_messages    INTEGER NOT NULL DEFAULT 0,
        built_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (day, conversation_id)
    )
    """,
    # Разрез «Чатники»: диалог дня × сотрудник, который в нём отвечал (правило табло: чат
    # засчитан каждому, кто отвечал; первый ответ — тому, кто ответил первым).
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_billing_operators (
        day                  DATE NOT NULL,
        conversation_id      TEXT NOT NULL,
        operator_key         TEXT NOT NULL,
        operator_id          INTEGER,
        operator_name        TEXT,
        park                 TEXT,
        started_at           TIMESTAMP NOT NULL,
        -- FALSE — в эти сутки не отвечал, но первый ответ на начатый сегодня чат дал он (после
        -- полуночи): в первый ответ идёт, в «чаты» сотрудника — нет (правило operator_sums).
        replied              BOOLEAN NOT NULL DEFAULT TRUE,
        first_reply_seconds  REAL,
        inner_reply_seconds  REAL,
        incoming_messages    INTEGER NOT NULL DEFAULT 0,
        outgoing_messages    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, conversation_id, operator_key)
    )
    """,
    # Факт сотрудников «Группировки»: секунды «Онлайн» чатника СЗоВ в часе. source — status
    # (по статусам Syntony) или replies (статусов за сутки нет — онлайн по ответам за 15 минут,
    # как табло считало до статусов).
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_billing_staff (
        day             DATE NOT NULL,
        hour            SMALLINT NOT NULL,
        operator_id     INTEGER NOT NULL,
        online_seconds  INTEGER NOT NULL,
        source          TEXT NOT NULL,
        PRIMARY KEY (day, hour, operator_id)
    )
    """,
    # Журнал сборки суток: когда собраны и полные ли (сутки закрылись и данные были целиком).
    """
    CREATE TABLE IF NOT EXISTS syntony_chat_billing_days (
        day           DATE PRIMARY KEY,
        built_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        complete      BOOLEAN NOT NULL DEFAULT FALSE,
        chats         INTEGER NOT NULL DEFAULT 0,
        staff_source  TEXT,
        error         TEXT
    )
    """,

    # Показатели чатников СЗоВ в «Учёте часов» (SZOV-11, employees.py). Что за сутки уже
    # записано сотруднику из Syntony: строка «Учёта часов» переписывается, только когда
    # поменялись сами данные, — иначе ручная правка СВ откатывалась бы каждым прогоном.
    """
    CREATE TABLE IF NOT EXISTS syntony_employee_metric_marks (
        day          DATE NOT NULL,
        operator_id  INTEGER NOT NULL,
        chats        INTEGER NOT NULL,
        first_sum    DOUBLE PRECISION NOT NULL,
        first_n      INTEGER NOT NULL,
        written_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (day, operator_id)
    )
    """,

    # Проверка качества СЗоВ (SZOV-9, qa.py): докуда диалог уже разобран на закрытые эпизоды —
    # следующий прогон берёт только сообщения позже. Строка на диалог; забывается через 40 дней.
    # Сами снимки чатов — в общей таблице снимков журнала (c2d_chat_snapshots), её индексы — в
    # схеме журнала (db/schema.py), рядом с индексами снимков Wazzup и ChatApp.
    """
    CREATE TABLE IF NOT EXISTS syntony_qa_marks (
        conversation_id  TEXT PRIMARY KEY,
        closed_until     TIMESTAMPTZ NOT NULL,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


def init_syntony_chats_schema(cursor):
    """Разворачивает схему. Курсор из _init_db, транзакцией правит вызывающий."""
    for statement in _STATEMENTS:
        cursor.execute(statement)
