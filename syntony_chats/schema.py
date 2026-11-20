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
]


def init_syntony_chats_schema(cursor):
    """Разворачивает схему. Курсор из _init_db, транзакцией правит вызывающий."""
    for statement in _STATEMENTS:
        cursor.execute(statement)
