"""Схема раздела «Оплата счетов» (задача #179, поручила Зарина Алиева).

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS, колонки — ADD COLUMN IF NOT
EXISTS. Разворачивается один раз при старте из Database._init_db через
init_payments_schema(cursor), под своим SAVEPOINT.

Что здесь смоделировано и почему именно так.

* **Заявка (payment_requests) — документ о закупе, а не строка реестра.**
  Постановка Зарины — 12 шагов с ответственным на каждом, следующий шаг
  открывается только после отписки предыдущего. Дополнение Дмитриевой —
  «Реестр заявок на оплату» с полями проекта, категории, контрагента, источника
  и типа оплаты. Оба ТЗ описывают ОДНУ сущность с двух сторон: маршрут и
  карточку, — поэтому таблица одна, а не «процесс» плюс «реестр».

* **Шаги материализованы (payment_request_steps).** Двенадцать строк на заявку
  заводятся при создании: у каждой свой ответственный (человек или роль),
  состояние и отписка. Считать маршрут «на лету» из констант нельзя: шаг 9 может
  достаться Директору по развитию по Приказу, шаг 2 — быть пропущен у заявки без
  руководителя, а через месяц надо ответить, КТО и КОГДА отписался.

* **Ответственный — либо человек, либо роль.** `assignee_id` заполнен у шагов
  инициатора, руководителя и делегата по Приказу; у «Учредителя» и «Бухгалтерии»
  он пуст, а `role_code` говорит, участники какой роли вправе отписаться
  (payment_role_members). Так «любой из бухгалтеров» не требует переназначения,
  когда один в отпуске.

* **Позиции расхода — отдельной таблицей.** Требование п. 10 дополнения:
  «Бумага А4 — 1 ед. × 2 500 тг», без обобщённых «хоз. товары». Сумма заявки
  считается из позиций, а не вводится рядом второй раз.

* **Договоры и Приказы — справочники со сроком и статусом.** Правило «счёт свыше
  300 000 ₸ только при действующем договоре» и «Директор по развитию согласует
  вместо Учредителя только в рамках Приказа» читают именно их. Недействующий
  договор считается отсутствующим — это правило живёт в workflow.py, а здесь
  только набор статусов.

* **Снимки имён рядом со ссылками.** `initiator_name`, `manager_name`,
  `assignee_name`, `actor_name` — копии на момент записи. Человека переименуют
  или уволят, а заявка — документ о том, кто и когда согласовал.

* **Время — настенные часы Алматы**, как во всём портале (`_NOW`). Наружу даты
  уходят строкой isoformat без зоны — см. flask jsonify и «GMT» в памяти проекта.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Роли процесса, у которых есть СПИСОК участников. Инициатор и его руководитель
# — конкретные люди заявки, а не роли со списком, поэтому их здесь нет.
# development_director — справочная: кто у нас Директор по развитию; право
# согласовать счёт он получает только через Приказ (форма Приказа подставляет его).
MEMBER_ROLES = ('founder', 'accounting', 'development_director')

REQUEST_STATUSES = ('active', 'done', 'rejected', 'cancelled')
STEP_STATES = ('pending', 'current', 'done', 'skipped')
PAYMENT_SOURCES = ('too', 'wallet', 'cash')
PAYMENT_TYPES = ('fixed', 'monthly', 'one_time')
CONTRACT_STATUSES = ('active', 'terminated', 'cancelled', 'archived', 'inactive')
ORDER_STATUSES = ('active', 'cancelled')
PERIODICITIES = ('monthly', 'quarterly', 'yearly', 'custom')
PARTY_KINDS = ('too', 'ip', 'other')

# Виды вложений — по шагам ТЗ: реестр поставщиков и КП (шаг 1), счёт (шаг 7),
# платёжное поручение и доверенность (шаг 10), АВР/накладная (шаг 11).
ATTACHMENT_KINDS = ('supplier_registry', 'offer', 'invoice', 'payment_order',
                    'power_of_attorney', 'act', 'contract', 'other')

# Виды событий истории. CHECK не ставим намеренно: новый вид события не должен
# требовать миграции живой базы, неизвестный вид лента показывает нейтрально.
EVENT_KINDS = ('created', 'step_done', 'step_skipped', 'returned', 'rejected',
               'cancelled', 'edited', 'attachment_added', 'attachment_removed',
               'blocked', 'unblocked', 'route', 'reassigned', 'refund', 'comment',
               'generated')

_STATEMENTS = [

    # ── Участники ролей ────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS payment_role_members (
        id          SERIAL PRIMARY KEY,
        role_code   VARCHAR(32) NOT NULL,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        added_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        added_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        UNIQUE (role_code, user_id)
    )
    """,

    # ── Справочники ────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS payment_projects (
        id          SERIAL PRIMARY KEY,
        name        VARCHAR(200) NOT NULL,
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_projects_name ON payment_projects (lower(name))",

    """
    CREATE TABLE IF NOT EXISTS payment_categories (
        id          SERIAL PRIMARY KEY,
        parent_id   INTEGER REFERENCES payment_categories(id) ON DELETE CASCADE,
        name        VARCHAR(200) NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_categories_name "
    "ON payment_categories (COALESCE(parent_id, 0), lower(name))",
    "CREATE INDEX IF NOT EXISTS idx_payment_categories_parent ON payment_categories (parent_id)",

    """
    CREATE TABLE IF NOT EXISTS payment_legal_entities (
        id          SERIAL PRIMARY KEY,
        name        VARCHAR(200) NOT NULL,
        bin         VARCHAR(12),
        kind        VARCHAR(8) CHECK (kind IN ('too', 'ip', 'other') OR kind IS NULL),
        vat_payer   BOOLEAN NOT NULL DEFAULT FALSE,
        note        TEXT,
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_legal_entities_name "
    "ON payment_legal_entities (lower(name))",

    """
    CREATE TABLE IF NOT EXISTS payment_counterparties (
        id          SERIAL PRIMARY KEY,
        name        VARCHAR(200) NOT NULL,
        bin         VARCHAR(12),
        kind        VARCHAR(8) CHECK (kind IN ('too', 'ip', 'other') OR kind IS NULL),
        vat_payer   BOOLEAN NOT NULL DEFAULT FALSE,
        requisites  TEXT,
        contact     TEXT,
        note        TEXT,
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_counterparties_name "
    "ON payment_counterparties (lower(name))",
    "CREATE INDEX IF NOT EXISTS idx_payment_counterparties_bin ON payment_counterparties (bin)",

    # ── Договоры ───────────────────────────────────────────────────────────
    #
    # `ends_on` NULL — бессрочный договор. Статус, отличный от active, для
    # проверки «есть ли действующий договор» считается отсутствием договора
    # (дополнение Колкомбаевой, п. 4).
    """
    CREATE TABLE IF NOT EXISTS payment_contracts (
        id               SERIAL PRIMARY KEY,
        counterparty_id  INTEGER NOT NULL REFERENCES payment_counterparties(id) ON DELETE CASCADE,
        legal_entity_id  INTEGER REFERENCES payment_legal_entities(id) ON DELETE SET NULL,
        number           VARCHAR(100) NOT NULL,
        signed_on        DATE,
        starts_on        DATE,
        ends_on          DATE,
        status           VARCHAR(16) NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'terminated', 'cancelled', 'archived', 'inactive')),
        subject          TEXT,
        note             TEXT,
        created_at       TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at       TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_contracts_counterparty "
    "ON payment_contracts (counterparty_id, status)",

    # ── Приказы на согласование ────────────────────────────────────────────
    #
    # Приказ передаёт право согласования от стандартного согласующего
    # (`replaces_role`, сейчас всегда 'founder' — Учредитель) конкретному
    # сотруднику (`delegate_user_id`, в ТЗ — Директор по развитию) в границах:
    # период, проекты, контрагенты, лимит суммы. `amount_limit` NULL — без лимита.
    """
    CREATE TABLE IF NOT EXISTS payment_approval_orders (
        id                  SERIAL PRIMARY KEY,
        number              VARCHAR(100) NOT NULL,
        issued_on           DATE NOT NULL,
        starts_on           DATE NOT NULL,
        ends_on             DATE,
        status              VARCHAR(16) NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'cancelled')),
        replaces_role       VARCHAR(32) NOT NULL DEFAULT 'founder',
        delegate_user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount_limit        NUMERIC(14, 2),
        all_projects        BOOLEAN NOT NULL DEFAULT FALSE,
        all_counterparties  BOOLEAN NOT NULL DEFAULT FALSE,
        note                TEXT,
        created_at          TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at          TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payment_order_projects (
        order_id    INTEGER NOT NULL REFERENCES payment_approval_orders(id) ON DELETE CASCADE,
        project_id  INTEGER NOT NULL REFERENCES payment_projects(id) ON DELETE CASCADE,
        PRIMARY KEY (order_id, project_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payment_order_counterparties (
        order_id         INTEGER NOT NULL REFERENCES payment_approval_orders(id) ON DELETE CASCADE,
        counterparty_id  INTEGER NOT NULL REFERENCES payment_counterparties(id) ON DELETE CASCADE,
        PRIMARY KEY (order_id, counterparty_id)
    )
    """,

    # ── Календарь фиксированных платежей ───────────────────────────────────
    #
    # `next_due_on` — ближайшая дата оплаты; заявка создаётся в НАЧАЛЕ периода
    # (первого числа месяца срока) или за `lead_days` до срока у произвольного
    # интервала. После создания срок сдвигается на период вперёд.
    """
    CREATE TABLE IF NOT EXISTS payment_fixed_templates (
        id                   SERIAL PRIMARY KEY,
        name                 VARCHAR(300) NOT NULL,
        amount               NUMERIC(14, 2) NOT NULL,
        periodicity          VARCHAR(16) NOT NULL
                             CHECK (periodicity IN ('monthly', 'quarterly', 'yearly', 'custom')),
        interval_days        INTEGER,
        next_due_on          DATE NOT NULL,
        lead_days            INTEGER NOT NULL DEFAULT 7,
        project_id           INTEGER REFERENCES payment_projects(id) ON DELETE SET NULL,
        branch               VARCHAR(200),
        responsible_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category_id          INTEGER REFERENCES payment_categories(id) ON DELETE SET NULL,
        subcategory_id       INTEGER REFERENCES payment_categories(id) ON DELETE SET NULL,
        counterparty_id      INTEGER REFERENCES payment_counterparties(id) ON DELETE SET NULL,
        legal_entity_id      INTEGER REFERENCES payment_legal_entities(id) ON DELETE SET NULL,
        payment_source       VARCHAR(16)
                             CHECK (payment_source IN ('too', 'wallet', 'cash') OR payment_source IS NULL),
        note                 TEXT,
        is_active            BOOLEAN NOT NULL DEFAULT TRUE,
        last_generated_on    DATE,
        created_at           TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at           TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_fixed_templates_due "
    "ON payment_fixed_templates (is_active, next_due_on)",

    # ── Заявка ─────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS payment_requests (
        id                       SERIAL PRIMARY KEY,
        created_at               TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at               TIMESTAMP NOT NULL DEFAULT %(now)s,

        initiator_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        initiator_name           VARCHAR(200),
        manager_id               INTEGER REFERENCES users(id) ON DELETE SET NULL,
        manager_name             VARCHAR(200),
        department_id            INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        department_name          VARCHAR(200),

        project_id               INTEGER REFERENCES payment_projects(id) ON DELETE SET NULL,
        branch                   VARCHAR(200),
        expense_name             VARCHAR(300) NOT NULL,
        category_id              INTEGER REFERENCES payment_categories(id) ON DELETE SET NULL,
        subcategory_id           INTEGER REFERENCES payment_categories(id) ON DELETE SET NULL,
        counterparty_id          INTEGER REFERENCES payment_counterparties(id) ON DELETE SET NULL,
        legal_entity_id          INTEGER REFERENCES payment_legal_entities(id) ON DELETE SET NULL,
        contract_id              INTEGER REFERENCES payment_contracts(id) ON DELETE SET NULL,

        amount                   NUMERIC(14, 2) NOT NULL DEFAULT 0,
        currency                 VARCHAR(3) NOT NULL DEFAULT 'KZT',
        payment_period           VARCHAR(120),
        payment_source           VARCHAR(16)
                                 CHECK (payment_source IN ('too', 'wallet', 'cash') OR payment_source IS NULL),
        payment_type             VARCHAR(16) NOT NULL DEFAULT 'one_time'
                                 CHECK (payment_type IN ('fixed', 'monthly', 'one_time')),
        card_number              VARCHAR(32),
        notes                    TEXT,
        due_on                   DATE,

        invoice_requisites       TEXT,
        invoice_number           VARCHAR(100),
        invoice_date             DATE,
        invoice_description      TEXT,
        needs_power_of_attorney  BOOLEAN NOT NULL DEFAULT FALSE,
        needs_payment_order      BOOLEAN NOT NULL DEFAULT FALSE,
        previous_payment_note    TEXT,
        paid_on                  DATE,
        paid_amount              NUMERIC(14, 2),
        refund_on                DATE,
        refund_amount            NUMERIC(14, 2),

        fixed_template_id        INTEGER REFERENCES payment_fixed_templates(id) ON DELETE SET NULL,

        current_step             INTEGER NOT NULL DEFAULT 1,
        status                   VARCHAR(16) NOT NULL DEFAULT 'active'
                                 CHECK (status IN ('active', 'done', 'rejected', 'cancelled')),
        block_code               VARCHAR(32),
        block_reason             TEXT,
        current_role_code             VARCHAR(32),
        current_assignee_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        current_assignee_name    VARCHAR(200),
        approver_user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        approval_order_id        INTEGER REFERENCES payment_approval_orders(id) ON DELETE SET NULL,
        route_basis              JSONB,
        step_changed_at          TIMESTAMP,
        closed_at                TIMESTAMP,
        rejected_reason          TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests (status, current_step)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_assignee ON payment_requests (current_assignee_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_initiator ON payment_requests (initiator_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_counterparty ON payment_requests (counterparty_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_due ON payment_requests (due_on)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_created ON payment_requests (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_template ON payment_requests (fixed_template_id)",

    """
    CREATE TABLE IF NOT EXISTS payment_request_items (
        id          SERIAL PRIMARY KEY,
        request_id  INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        position    INTEGER NOT NULL DEFAULT 0,
        name        VARCHAR(300) NOT NULL,
        quantity    NUMERIC(12, 3) NOT NULL DEFAULT 1,
        unit        VARCHAR(32),
        unit_price  NUMERIC(14, 2) NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_request_items_request "
    "ON payment_request_items (request_id, position)",

    """
    CREATE TABLE IF NOT EXISTS payment_request_steps (
        id             SERIAL PRIMARY KEY,
        request_id     INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        step_no        INTEGER NOT NULL,
        role_code      VARCHAR(32) NOT NULL,
        assignee_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        assignee_name  VARCHAR(200),
        state          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (state IN ('pending', 'current', 'done', 'skipped')),
        done_at        TIMESTAMP,
        done_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        done_by_name   VARCHAR(200),
        comment        TEXT,
        UNIQUE (request_id, step_no)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS payment_events (
        id          SERIAL PRIMARY KEY,
        request_id  INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        kind        VARCHAR(32) NOT NULL,
        step_no     INTEGER,
        actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name  VARCHAR(200),
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        comment     TEXT,
        payload     JSONB
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_events_request ON payment_events (request_id, id)",

    """
    CREATE TABLE IF NOT EXISTS payment_attachments (
        id                SERIAL PRIMARY KEY,
        request_id        INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        step_no           INTEGER,
        kind              VARCHAR(32) NOT NULL DEFAULT 'other',
        file_name         VARCHAR(255) NOT NULL,
        content_type      VARCHAR(128),
        file_size         INTEGER NOT NULL DEFAULT 0,
        bucket            VARCHAR(255) NOT NULL,
        blob_path         TEXT NOT NULL,
        uploaded_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_by_name  VARCHAR(200),
        uploaded_at       TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_attachments_request "
    "ON payment_attachments (request_id, step_no, id)",
]

# Trigram-индексы под поиск «содержит» по названию расхода, примечаниям и
# контрагенту. Под своим SAVEPOINT: без расширения pg_trgm раздел поднимается
# без них (поиск тогда идёт по ILIKE полным сканом), а не падает целиком.
_TRGM_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_expense_trgm "
    "ON payment_requests USING gin (expense_name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_notes_trgm "
    "ON payment_requests USING gin (COALESCE(notes, '') gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_payment_counterparties_name_trgm "
    "ON payment_counterparties USING gin (name gin_trgm_ops)",
]

TABLES = (
    'payment_role_members', 'payment_projects', 'payment_categories',
    'payment_legal_entities', 'payment_counterparties', 'payment_contracts',
    'payment_approval_orders', 'payment_order_projects', 'payment_order_counterparties',
    'payment_fixed_templates', 'payment_requests', 'payment_request_items',
    'payment_request_steps', 'payment_events', 'payment_attachments',
)


def statements():
    """DDL по порядку, с подставленным выражением времени."""
    return [sql % {'now': _NOW} if '%(now)s' in sql else sql for sql in _STATEMENTS]


def init_payments_schema(cursor):
    for sql in statements():
        cursor.execute(sql)
    cursor.execute("SAVEPOINT payments_trgm")
    try:
        for sql in _TRGM_STATEMENTS:
            cursor.execute(sql)
        cursor.execute("RELEASE SAVEPOINT payments_trgm")
    except Exception:  # noqa: BLE001 — без trigram раздел живёт, просто медленнее ищет
        cursor.execute("ROLLBACK TO SAVEPOINT payments_trgm")


def schema_ready(cursor):
    """Есть ли главная таблица раздела — по ней /ping честно говорит о состоянии."""
    cursor.execute("SELECT to_regclass('public.payment_requests') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
