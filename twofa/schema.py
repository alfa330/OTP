"""Схема раздела «Коды 2FA». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_twofa_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Ключ аккаунта — только шифротекст** (`secret_enc`, crypto.py). Удаление аккаунта не
  стирает строку, а стирает ключ: журнал ссылается на аккаунт по названию и id, и через
  год должно быть видно, к чему выдавали коды, но восстановить ключ из базы — нельзя.
  CHECK держит инвариант «живой аккаунт ⇔ ключ есть».

* **Один ждущий запрос на пару «аккаунт + сотрудник»** — частичный уникальный индекс.
  Двойное нажатие или вторая вкладка не пришлют главе два одинаковых запроса.

* **Решение — одна строка в одном UPDATE … WHERE status = 'pending'**. Глава нажал в
  Telegram и в портале почти одновременно — запишется первое, второе получит «уже решено».

* **Сообщения Telegram — своя таблица** с моментами «стереть цифры» и «убрать сообщение».
  Будильник (timer.py) берёт сроки отсюда, поэтому перезапуск сервера их не теряет.

* **Уведомления о запросах — по подписке** (`twofa_notify_optin`): подтверждать могут глава
  маркетинга и супер-админы, а колокол и Telegram получает только тот, кто включил тумблер.

* **Журнал не правится** (ТЗ п. 7): триггер запрещает UPDATE и DELETE. Внешних ключей на
  users у журнала и запросов нет намеренно: удаление учётки (склейка дублей) не должно ни
  падать на них, ни переписывать журнал через ON DELETE SET NULL — имя лежит снимком.
"""

_STATEMENTS = [

    """
    CREATE TABLE IF NOT EXISTS twofa_accounts (
        id            SERIAL PRIMARY KEY,
        title         VARCHAR(120) NOT NULL,
        service       VARCHAR(80),
        secret_enc    TEXT,
        is_active     BOOLEAN NOT NULL DEFAULT TRUE,
        created_by    INTEGER,
        created_at    TIMESTAMP NOT NULL,
        updated_at    TIMESTAMP NOT NULL,
        removed_at    TIMESTAMP,
        removed_by    INTEGER,
        CONSTRAINT twofa_accounts_key_while_active CHECK (NOT is_active OR secret_enc IS NOT NULL)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS twofa_grants (
        account_id    INTEGER NOT NULL REFERENCES twofa_accounts(id) ON DELETE CASCADE,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        granted_by    INTEGER,
        granted_at    TIMESTAMP NOT NULL,
        PRIMARY KEY (account_id, user_id)
    )
    """,

    # status: pending / approved / rejected / expired / cancelled. Без CHECK — список растёт кодом.
    """
    CREATE TABLE IF NOT EXISTS twofa_requests (
        id              SERIAL PRIMARY KEY,
        account_id      INTEGER NOT NULL REFERENCES twofa_accounts(id),
        requester_id    INTEGER NOT NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_at      TIMESTAMP NOT NULL,
        expires_at      TIMESTAMP NOT NULL,
        decided_at      TIMESTAMP,
        decided_by      INTEGER,
        decided_via     VARCHAR(16),
        resend_until    TIMESTAMP,
        codes_sent      INTEGER NOT NULL DEFAULT 0,
        -- Номер 30-секундного шага последнего выданного кода — не сам код: по нему
        -- «Код не подошёл» понимает, что выдаёт те же цифры, и говорит об этом.
        last_counter    BIGINT,
        delivery_error  VARCHAR(300)
    )
    """,

    # kind: approval (главе, с кнопками) / code (сотруднику, с цифрами).
    # state: shown → hidden (цифры стёрты, кнопка осталась) → deleted; closed — у сообщения
    # главы после решения (кнопки сняты, сообщение остаётся как отметка).
    """
    CREATE TABLE IF NOT EXISTS twofa_messages (
        id            SERIAL PRIMARY KEY,
        request_id    INTEGER NOT NULL REFERENCES twofa_requests(id) ON DELETE CASCADE,
        kind          VARCHAR(16) NOT NULL,
        chat_id       BIGINT NOT NULL,
        message_id    BIGINT NOT NULL,
        state         VARCHAR(16) NOT NULL DEFAULT 'shown',
        hide_at       TIMESTAMP,
        delete_at     TIMESTAMP,
        created_at    TIMESTAMP NOT NULL
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS twofa_events (
        id             SERIAL PRIMARY KEY,
        kind           VARCHAR(24) NOT NULL,
        request_id     INTEGER,
        account_id     INTEGER,
        account_title  VARCHAR(120),
        actor_id       INTEGER,
        actor_name     VARCHAR(200),
        subject_id     INTEGER,
        subject_name   VARCHAR(200),
        details        JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at     TIMESTAMP NOT NULL
    )
    """,

    # Кто подтверждает и хочет знать о запросах (владелец, 27.10.2026): колокол iCore и
    # Telegram с кнопками — только тем, кто включил тумблер в разделе; по умолчанию выключен.
    # Есть строка — включено; выключение строку удаляет: «не подписан» — одно состояние, как у
    # shift_change_notify_optin.
    """
    CREATE TABLE IF NOT EXISTS twofa_notify_optin (
        user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        enabled_at    TIMESTAMP NOT NULL
    )
    """,

    """
    CREATE OR REPLACE FUNCTION twofa_events_append_only()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'Журнал «Кодов 2FA» не правится';
    END;
    $$
    """,

    # Триггер — только если его ещё нет: DROP + CREATE на каждом старте брали бы
    # AccessExclusiveLock на журнал при каждой выкладке.
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_trigger
                        WHERE tgname = 'trg_twofa_events_append_only'
                          AND tgrelid = 'twofa_events'::regclass) THEN
            CREATE TRIGGER trg_twofa_events_append_only
                BEFORE UPDATE OR DELETE ON twofa_events
                FOR EACH ROW EXECUTE FUNCTION twofa_events_append_only();
        END IF;
    END
    $$
    """,

    "CREATE INDEX IF NOT EXISTS idx_twofa_accounts_active ON twofa_accounts(is_active, title)",
    "CREATE INDEX IF NOT EXISTS idx_twofa_grants_user ON twofa_grants(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_twofa_requests_pending "
    "ON twofa_requests(account_id, requester_id) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_twofa_requests_pending_due "
    "ON twofa_requests(expires_at) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_twofa_requests_requester "
    "ON twofa_requests(requester_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_twofa_requests_created ON twofa_requests(created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_twofa_messages_request ON twofa_messages(request_id)",
    "CREATE INDEX IF NOT EXISTS idx_twofa_messages_hide "
    "ON twofa_messages(hide_at) WHERE state = 'shown' AND hide_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_twofa_messages_delete "
    "ON twofa_messages(delete_at) WHERE state IN ('shown', 'hidden') AND delete_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_twofa_events_created ON twofa_events(created_at DESC, id DESC)",
]

# Миграции по живой базе. Идут ПОСЛЕ таблиц и ПЕРЕД индексами — порядок держит
# init_twofa_schema (у «Обращений» обратный порядок уронил прод 17.08.2026).
#
# Колонку добавляем, только если её нет: голый ALTER … ADD COLUMN IF NOT EXISTS берёт
# AccessExclusiveLock на каждом старте, даже когда колонка давно есть.
def _add_column(table, column, ddl):
    return """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema() AND table_name = '%s'
                          AND column_name = '%s') THEN
            ALTER TABLE %s ADD COLUMN %s %s;
        END IF;
    END
    $$
    """ % (table, column, table, column, ddl)


_MIGRATIONS = [
    # 27.10.2026, разбор после выкладки: момент последней выдачи кода — под блокировкой
    # запроса отсекает второе нажатие «Код не подошёл» (два сообщения с цифрами).
    _add_column('twofa_requests', 'last_issued_at', 'TIMESTAMP'),
    # Сколько раз Telegram не принял «стереть цифры» / «убрать сообщение»: повторяем, но не
    # вечно (бота заблокировали — править его сообщения больше нельзя никогда).
    _add_column('twofa_messages', 'attempts', 'INTEGER NOT NULL DEFAULT 0'),
]


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_twofa_schema(cursor):
    """Разворачивает схему. Курсор из _init_db, транзакцией правит вызывающий."""
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)


def schema_is_ready(cursor):
    """Развёрнута ли схема: отличает «раздел ещё не поднялся» от «таблица пуста»."""
    cursor.execute("SELECT to_regclass('public.twofa_events') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
