# -*- coding: utf-8 -*-
"""Схема раздела «Карточка клиента». Все таблицы с префиксом client_card.

Идемпотентно: CREATE … IF NOT EXISTS. Разворачивается при старте из Database._init_db
через init_client_cards_schema(cursor) — своим SAVEPOINT'ом, как у «Аренды авто»: не
развернулась — раздел недоступен, портал работает.

Время во всех таблицах — наивное время Алматы (rules.now_almaty), как у «Обращений».

Решения, которые видны в DDL:

* **Аккаунт — пара (вид, ID Тез).** ID водителя и ID клиента — разные счётчики Тез, и
  одно число у них значит разных людей (владелец, 12.11.2026: /admin/drivers/<id> —
  водитель, /admin/clients/<id> — клиент). Свой id — чтобы обращения и привязки
  ссылались на одну строку, а не на пару.
* **Привязка номера к аккаунту — многие ко многим.** Человек зарегистрирован на один
  номер, а звонит с другого (и не с одного); с одного номера звонят по разным аккаунтам
  (водитель и он же клиент). origin: tez — нашёл запрос по номеру, manual — оператор
  приложил ссылку.
* **Карточка хранит снимки** вида аккаунта, города и имени оператора: отчёт за прошлый
  месяц не должен меняться оттого, что водитель сменил город, а оператора перевели.
* **Причины карточки — отдельной таблицей**, а не массивом: по ним строятся отчёт и
  алерты, а причин у звонка бывает несколько.
* **Причину с обращениями нельзя удалить** (ON DELETE RESTRICT у причин) — её скрывают
  (is_active): иначе отчёт за прошлые месяцы потерял бы строки.
* **Файл запроса лежит у нас** (file_ref = gs://…), а не только в Telegram: бот отдаёт
  обратно файлы до 20 МБ, а видео оператора бывает больше. Файл из группы — наоборот,
  file_id Telegram: его прислали туда, а не нам.
* **Непрочитанное живёт в самом запросе** (author_unread_count/_at): адресат один —
  автор, как у «Обращений».
"""

_STATEMENTS = [

    # Настройки отдела. Строки нет — раздел у отдела выключен.
    # group_ids — кому телефон открывает карточку; пусто — всем операторам отдела.
    """
    CREATE TABLE IF NOT EXISTS client_card_settings (
        department_id     INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        enabled           BOOLEAN NOT NULL DEFAULT FALSE,
        group_ids         INTEGER[] NOT NULL DEFAULT '{}',
        wrapup_sec        INTEGER NOT NULL DEFAULT 60,
        alerts            JSONB NOT NULL DEFAULT '{}'::jsonb,
        alert_head        BOOLEAN NOT NULL DEFAULT TRUE,
        alert_chat_id     BIGINT,
        alert_chat_title  VARCHAR(255),
        updated_by        INTEGER,
        updated_by_name   VARCHAR(255),
        updated_at        TIMESTAMP
    )
    """,

    # Причины обращения — дерево любой глубины: parent_id IS NULL — основная причина,
    # иначе подтема причины parent_id (потолок глубины — rules.CATEGORY_MAX_DEPTH).
    """
    CREATE TABLE IF NOT EXISTS client_card_categories (
        id             SERIAL PRIMARY KEY,
        department_id  INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        parent_id      INTEGER REFERENCES client_card_categories(id) ON DELETE CASCADE,
        title          VARCHAR(160) NOT NULL,
        position       INTEGER NOT NULL DEFAULT 0,
        is_active      BOOLEAN NOT NULL DEFAULT TRUE,
        created_by     INTEGER,
        created_at     TIMESTAMP NOT NULL,
        updated_at     TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_categories_tree
        ON client_card_categories (department_id, parent_id, position, id)
    """,

    # fetched_at — когда Тез последний раз отдал профиль; NULL — ещё ни разу (аккаунт
    # известен только по ссылке оператора).
    """
    CREATE TABLE IF NOT EXISTS client_card_accounts (
        id             SERIAL PRIMARY KEY,
        kind           VARCHAR(16) NOT NULL,
        tez_id         BIGINT NOT NULL,
        full_name      VARCHAR(255),
        phone          VARCHAR(20),
        city           VARCHAR(120),
        status         VARCHAR(64),
        registered_at  TIMESTAMP,
        last_order_at  TIMESTAMP,
        details        JSONB NOT NULL DEFAULT '{}'::jsonb,
        fetched_at     TIMESTAMP,
        created_by     INTEGER,
        created_at     TIMESTAMP NOT NULL,
        CONSTRAINT client_card_accounts_tez UNIQUE (kind, tez_id)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS client_card_phone_links (
        id               SERIAL PRIMARY KEY,
        phone            VARCHAR(20) NOT NULL,
        account_id       INTEGER NOT NULL REFERENCES client_card_accounts(id) ON DELETE CASCADE,
        origin           VARCHAR(16) NOT NULL,
        created_by       INTEGER,
        created_by_name  VARCHAR(255),
        created_at       TIMESTAMP NOT NULL,
        CONSTRAINT client_card_phone_links_pair UNIQUE (phone, account_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_client_card_phone_links_account ON client_card_phone_links (account_id)",

    # call_key — идентификатор звонка от телефона (SIP Call-ID): повтор запроса того же
    # звонка возвращает ту же карточку, а не заводит вторую.
    # parent_id — «продолжение обращения»: человек перезвонил по тому же вопросу.
    """
    CREATE TABLE IF NOT EXISTS client_cards (
        id             SERIAL PRIMARY KEY,
        department_id  INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        operator_id    INTEGER NOT NULL,
        operator_name  VARCHAR(255),
        phone          VARCHAR(20) NOT NULL,
        direction      VARCHAR(8) NOT NULL,
        call_key       VARCHAR(160),
        status         VARCHAR(16) NOT NULL DEFAULT 'open',
        account_id     INTEGER REFERENCES client_card_accounts(id) ON DELETE SET NULL,
        account_kind   VARCHAR(16),
        city           VARCHAR(120),
        comment        TEXT,
        parent_id      INTEGER REFERENCES client_cards(id) ON DELETE SET NULL,
        wrapup_sec     INTEGER NOT NULL DEFAULT 0,
        created_at     TIMESTAMP NOT NULL,
        answered_at    TIMESTAMP,
        ended_at       TIMESTAMP,
        completed_at   TIMESTAMP,
        updated_at     TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_client_cards_call
        ON client_cards (operator_id, call_key) WHERE call_key IS NOT NULL
    """,
    # Журнал и отчёт отдела за период; история водителя; история номера; «мои незаполненные».
    "CREATE INDEX IF NOT EXISTS idx_client_cards_department ON client_cards (department_id, created_at DESC, id DESC)",
    """
    CREATE INDEX IF NOT EXISTS idx_client_cards_account
        ON client_cards (account_id, created_at DESC) WHERE account_id IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_client_cards_phone ON client_cards (phone, created_at DESC)",
    """
    CREATE INDEX IF NOT EXISTS idx_client_cards_operator_open
        ON client_cards (operator_id, created_at DESC) WHERE status = 'open'
    """,

    """
    CREATE TABLE IF NOT EXISTS client_card_topics (
        id              SERIAL PRIMARY KEY,
        card_id         INTEGER NOT NULL REFERENCES client_cards(id) ON DELETE CASCADE,
        category_id     INTEGER NOT NULL REFERENCES client_card_categories(id) ON DELETE RESTRICT,
        subcategory_id  INTEGER REFERENCES client_card_categories(id) ON DELETE RESTRICT,
        position        SMALLINT NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_client_card_topics
        ON client_card_topics (card_id, category_id, COALESCE(subcategory_id, 0))
    """,
    "CREATE INDEX IF NOT EXISTS idx_client_card_topics_category ON client_card_topics (category_id)",
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_topics_subcategory
        ON client_card_topics (subcategory_id) WHERE subcategory_id IS NOT NULL
    """,

    # Адресаты запросов — «отделы»: название и Telegram-группа, куда бот относит запрос.
    # chat_id допускает NULL: адресата заводят до того, как бота добавили в группу, и до
    # привязки он оператору не предлагается.
    """
    CREATE TABLE IF NOT EXISTS client_card_destinations (
        id                 SERIAL PRIMARY KEY,
        department_id      INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        title              VARCHAR(160) NOT NULL,
        chat_id            BIGINT,
        chat_title         VARCHAR(255),
        mention_usernames  TEXT,
        position           INTEGER NOT NULL DEFAULT 0,
        is_active          BOOLEAN NOT NULL DEFAULT TRUE,
        created_by         INTEGER,
        created_at         TIMESTAMP NOT NULL,
        updated_at         TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_destinations
        ON client_card_destinations (department_id, position, id)
    """,

    # delivery_status отделён от status, как у «Обращений»: статус — про суть, доставка —
    # про Telegram. Не ушло — запрос остаётся и отправляется повторно кнопкой.
    """
    CREATE TABLE IF NOT EXISTS client_card_requests (
        id                   SERIAL PRIMARY KEY,
        department_id        INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        card_id              INTEGER REFERENCES client_cards(id) ON DELETE SET NULL,
        destination_id       INTEGER NOT NULL REFERENCES client_card_destinations(id) ON DELETE RESTRICT,
        destination_title    VARCHAR(160),
        body                 TEXT NOT NULL,
        status               VARCHAR(16) NOT NULL DEFAULT 'open',
        account_id           INTEGER REFERENCES client_card_accounts(id) ON DELETE SET NULL,
        phone                VARCHAR(20),
        topic_title          VARCHAR(400),
        created_by           INTEGER,
        created_by_name      VARCHAR(255),
        tg_chat_id           BIGINT,
        tg_chat_title        VARCHAR(255),
        tg_message_id        BIGINT,
        delivery_status      VARCHAR(16) NOT NULL DEFAULT 'pending',
        delivery_error       TEXT,
        first_reply_at       TIMESTAMP,
        last_message_at      TIMESTAMP NOT NULL,
        last_inbound_at      TIMESTAMP,
        author_unread_count  INTEGER NOT NULL DEFAULT 0,
        author_unread_at     TIMESTAMP,
        resolved_at          TIMESTAMP,
        resolved_by          INTEGER,
        resolved_by_name     VARCHAR(255),
        created_at           TIMESTAMP NOT NULL,
        updated_at           TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_requests_author
        ON client_card_requests (created_by, last_message_at DESC, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_requests_department
        ON client_card_requests (department_id, last_message_at DESC, id DESC)
    """,
    "CREATE INDEX IF NOT EXISTS idx_client_card_requests_card ON client_card_requests (card_id)",
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_requests_unread
        ON client_card_requests (created_by, author_unread_at DESC) WHERE author_unread_count > 0
    """,

    # direction: out — из портала в группу, in — из группы. Корневое сообщение бота лежит
    # здесь же строкой out: ответ в группе находит свой запрос одним поиском по (чат, номер).
    """
    CREATE TABLE IF NOT EXISTS client_card_request_messages (
        id                      SERIAL PRIMARY KEY,
        request_id              INTEGER NOT NULL REFERENCES client_card_requests(id) ON DELETE CASCADE,
        direction               VARCHAR(8) NOT NULL,
        body                    TEXT,
        author_user_id          INTEGER,
        author_name             VARCHAR(255),
        tg_chat_id              BIGINT,
        tg_message_id           BIGINT,
        tg_from_id              BIGINT,
        tg_username             VARCHAR(255),
        reply_to_tg_message_id  BIGINT,
        file_ref                TEXT,
        file_name               VARCHAR(255),
        file_mime               VARCHAR(128),
        file_size               BIGINT,
        file_kind               VARCHAR(16),
        created_at              TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_request_messages
        ON client_card_request_messages (request_id, created_at, id)
    """,
    # Защита от повторного апдейта Telegram и рабочий индекс поиска запроса по сообщению.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_client_card_request_messages_tg
        ON client_card_request_messages (tg_chat_id, tg_message_id) WHERE tg_message_id IS NOT NULL
    """,

    # Журнал алертов. По нему же гасятся повторы: «когда и с каким числом писали в
    # прошлый раз» (rules.alert_decision). category_id — причина любого уровня дерева.
    """
    CREATE TABLE IF NOT EXISTS client_card_alerts (
        id             SERIAL PRIMARY KEY,
        department_id  INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        category_id    INTEGER NOT NULL REFERENCES client_card_categories(id) ON DELETE CASCADE,
        kind           VARCHAR(16) NOT NULL,
        count          INTEGER NOT NULL,
        baseline       NUMERIC(8, 2) NOT NULL DEFAULT 0,
        threshold      INTEGER NOT NULL,
        window_min     INTEGER NOT NULL,
        details        JSONB NOT NULL DEFAULT '{}'::jsonb,
        delivered      BOOLEAN NOT NULL DEFAULT FALSE,
        sent_at        TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_client_card_alerts
        ON client_card_alerts (department_id, category_id, sent_at DESC)
    """,
]


# Отделы, которым раздел выдан с выкладки: Тез КЦ — заказчик задачи #335 («карточка
# клиента для ТП»). Строка заводится ВЫКЛЮЧЕННОЙ: руководитель сначала заводит причины и
# адресатов и сам включает карточку операторам. Остальным отделам раздел выдаёт
# администратор компании из самого раздела.
_SEED_DEPARTMENT_CODES = ('tez',)


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_client_cards_schema(cursor):
    """Разворачивает схему. Курсор приходит из _init_db, транзакцией управляет вызывающий.

    Сначала таблицы, потом индексы — порядок, на котором «Обращения» однажды уронили
    прод (crm/schema.py): на развёрнутой базе индекс по новому столбцу не должен
    опережать сам столбец.
    """
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)
    cursor.execute("""
        INSERT INTO client_card_settings (department_id)
        SELECT d.id FROM departments d WHERE lower(d.code) = ANY(%s)
        ON CONFLICT (department_id) DO NOTHING
    """, (list(_SEED_DEPARTMENT_CODES),))


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «раздел ещё не поднялся» от «раздел пуст»."""
    cursor.execute("SELECT to_regclass('client_card_requests') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
