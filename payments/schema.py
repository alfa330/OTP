"""Схема раздела «Оплата счетов» (задача #179, поручила Зарина Алиева).

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS, колонки — ADD COLUMN IF NOT
EXISTS. Разворачивается один раз при старте из Database._init_db через
init_payments_schema(cursor), под своим SAVEPOINT.

С задачи #381 (ТЗ «Закуп и оплата», та же постановщица, 06.10.2026) раздел
ведёт ОДНУ заявку «Закуп товара/услуги» через этапы: инициация → согласование →
оплата → получение → учёт имущества → закрывающие документы → закрытие. Этапы
лежат подзадачами (`payment_subtasks`) вместо двенадцати шагов первой версии;
таблица шагов `payment_request_steps` остаётся только как архив старых заявок —
их при старте переводит payments/legacy.py. Всё новое дописано в конец списка
под заголовком «ТЗ „Закуп и оплата“».

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

# Роли процесса, у которых есть СПИСОК участников (ТЗ «Закуп и оплата», п. 3).
# Инициатор и его руководитель — конкретные люди заявки, а не роли со списком,
# поэтому их здесь нет. Исполнитель этапа оплаты — подразделение, а не человек
# (п. 2, п. 9): «Бухгалтерия» и «Финансовый отдел» — это списки, и задачу видит
# каждый из списка.
MEMBER_ROLES = ('approver', 'accounting', 'finance', 'asset_keeper')

# До ТЗ «Закуп и оплата» согласующих было двое с разными правами: Учредитель и
# Директор по развитию (по Приказу). Теперь это одна роль «Утвердитель», а кому
# из утверждающих достаётся заявка, решает матрица согласования (п. 6).
LEGACY_ROLE_MAP = {'founder': 'approver', 'development_director': 'approver'}

REQUEST_STATUSES = ('active', 'done', 'rejected', 'cancelled')
STEP_STATES = ('pending', 'current', 'done', 'skipped')
# Источник и тип оплаты первой версии раздела. Колонки остаются в базе (старые
# заявки), но новые заявки их не заполняют: в ТЗ способов оплаты ровно два (п. 5).
PAYMENT_SOURCES = ('too', 'wallet', 'cash')
PAYMENT_TYPES = ('fixed', 'monthly', 'one_time')
CONTRACT_STATUSES = ('active', 'terminated', 'cancelled', 'archived', 'inactive')
ORDER_STATUSES = ('active', 'cancelled')
PERIODICITIES = ('monthly', 'quarterly', 'yearly', 'custom')
PARTY_KINDS = ('too', 'ip', 'other')

# ── ТЗ «Закуп и оплата» ──────────────────────────────────────────────────────
REQUEST_KINDS = ('purchase', 'regular')          # п. 4: новый закуп / регулярное обязательство
PAYMENT_METHODS = ('invoice', 'card')            # п. 5: оплата счёта / пополнение карты
CARD_RECIPIENTS = ('employee', 'supplier')       # п. 5.2: чья карта
OBJECT_TYPES = ('goods', 'service')              # п. 4.1: товар или услуга
ACCOUNTING_CATEGORIES = ('consumable', 'asset')  # п. 10: расходный материал / имущество
CLOSING_DOC_STATUSES = ('none', 'scan', 'original', 'closed')   # п. 14
ASSET_STATUSES = ('in_use', 'in_stock', 'repair', 'written_off')
SUBTASK_STATES = ('pending', 'open', 'waiting', 'done', 'skipped')
MANAGER_STEP_MODES = ('auto', 'required', 'skip')
CARD_OWNER_KINDS = ('employee', 'supplier')

# Виды вложений. Первые восемь — из первой версии раздела; дальше то, что
# появилось с ТЗ «Закуп и оплата»: подтверждение перевода на карту (п. 5.3),
# чек (п. 5.3), накладная (п. 14), акт приёма-передачи имущества (п. 11).
ATTACHMENT_KINDS = ('supplier_registry', 'offer', 'invoice', 'payment_order',
                    'power_of_attorney', 'act', 'contract', 'other',
                    'transfer_proof', 'receipt', 'waybill', 'handover_act')

# Виды событий истории. CHECK не ставим намеренно: новый вид события не должен
# требовать миграции живой базы, неизвестный вид лента показывает нейтрально.
EVENT_KINDS = ('created', 'step_done', 'step_skipped', 'returned', 'rejected',
               'cancelled', 'edited', 'attachment_added', 'attachment_removed',
               'blocked', 'unblocked', 'route', 'reassigned', 'refund', 'comment',
               'generated',
               # ТЗ «Закуп и оплата»: этапы и подзадачи одной заявки
               'submitted', 'subtask_moved', 'approved', 'clarification', 'clarified', 'paid',
               'topped_up', 'receipt_provided', 'received', 'asset_registered',
               'docs_status', 'closed', 'migrated', 'card_revealed')

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

    # ── Варианты выбором, а не текстом (06.10.2026) ────────────────────────
    #
    # Шаг 4 постановки: «предоставить информацию по ИП/ТОО, НДС/не НДС». Раньше
    # это писали словами в комментарии; теперь форма поставщика и НДС выбираются
    # и лежат в заявке. CHECK не ставим: набор форм уже сторожит код
    # (PARTY_KINDS), а новый вариант не должен требовать миграции живой базы.
    # `supplier_vat` NULL — «ещё не выбрано», это не то же, что «без НДС».
    "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS supplier_kind VARCHAR(8)",
    "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS supplier_vat BOOLEAN",
    # Реквизиты нашего юр. лица: на шаге 5 бухгалтерия выбирает юр. лицо, и
    # реквизиты для счёта подставляются отсюда, а не набираются каждый раз.
    "ALTER TABLE payment_legal_entities ADD COLUMN IF NOT EXISTS requisites TEXT",

    # ═════════════════════════════════════════════════════════════════════════
    # ТЗ «Закуп и оплата» (задача #381): одна заявка, этапы и подзадачи внутри
    # ═════════════════════════════════════════════════════════════════════════

    # ── Роли: Учредитель и Директор по развитию → «Утвердитель» ────────────
    """
    INSERT INTO payment_role_members (role_code, user_id, added_by, added_at)
    SELECT 'approver', user_id, added_by, added_at
      FROM payment_role_members
     WHERE role_code IN ('founder', 'development_director')
    ON CONFLICT (role_code, user_id) DO NOTHING
    """,
    "DELETE FROM payment_role_members WHERE role_code IN ('founder', 'development_director')",

    # ── Карточка поставщика (п. 13.1) ──────────────────────────────────────
    #
    # `approver_user_id` и `approval_limit` — «для каждого согласующего должны
    # настраиваться поставщики и лимиты» (п. 6): заявка на этого поставщика в
    # пределах лимита сама уходит указанному согласующему.
    """
    ALTER TABLE payment_counterparties
        ADD COLUMN IF NOT EXISTS legal_name VARCHAR(300),
        ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES payment_categories(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS responsible_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS approver_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS approval_limit NUMERIC(14, 2)
    """,

    # ── Банковские реквизиты поставщиков (п. 13) ───────────────────────────
    """
    CREATE TABLE IF NOT EXISTS payment_counterparty_accounts (
        id               SERIAL PRIMARY KEY,
        counterparty_id  INTEGER NOT NULL REFERENCES payment_counterparties(id) ON DELETE CASCADE,
        bank_name        VARCHAR(200),
        iik              VARCHAR(34) NOT NULL,
        bik              VARCHAR(16),
        kbe              VARCHAR(4),
        note             TEXT,
        is_default       BOOLEAN NOT NULL DEFAULT FALSE,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_counterparty_accounts_cp "
    "ON payment_counterparty_accounts (counterparty_id, is_active)",

    # ── Реквизиты наших юр. лиц (п. 13.3) ──────────────────────────────────
    # Раздельными полями, а не одним текстом: при выборе компании-плательщика
    # реквизиты подтягиваются сами и копируются одной кнопкой.
    """
    ALTER TABLE payment_legal_entities
        ADD COLUMN IF NOT EXISTS legal_address TEXT,
        ADD COLUMN IF NOT EXISTS bank_name VARCHAR(200),
        ADD COLUMN IF NOT EXISTS iik VARCHAR(34),
        ADD COLUMN IF NOT EXISTS bik VARCHAR(16),
        ADD COLUMN IF NOT EXISTS kbe VARCHAR(4)
    """,

    # ── Реестр договоров (п. 13.2) ─────────────────────────────────────────
    """
    ALTER TABLE payment_contracts
        ADD COLUMN IF NOT EXISTS amount NUMERIC(14, 2),
        ADD COLUMN IF NOT EXISTS amount_limit NUMERIC(14, 2),
        ADD COLUMN IF NOT EXISTS periodicity VARCHAR(16),
        ADD COLUMN IF NOT EXISTS responsible_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS file_name VARCHAR(255),
        ADD COLUMN IF NOT EXISTS file_type VARCHAR(128),
        ADD COLUMN IF NOT EXISTS file_size INTEGER,
        ADD COLUMN IF NOT EXISTS file_bucket VARCHAR(255),
        ADD COLUMN IF NOT EXISTS file_blob TEXT
    """,

    # ── Банковские карты сотрудников и поставщиков (п. 13) ─────────────────
    #
    # Полный номер лежит ЗАШИФРОВАННЫМ (payments/cards.py), рядом — последние
    # четыре цифры для подписи «•••• 1234». Открытого номера в базе нет: его
    # не должно быть ни в выгрузке таблицы, ни в резервной копии.
    """
    CREATE TABLE IF NOT EXISTS payment_cards (
        id               SERIAL PRIMARY KEY,
        owner_kind       VARCHAR(16) NOT NULL,
        user_id          INTEGER REFERENCES users(id) ON DELETE CASCADE,
        counterparty_id  INTEGER REFERENCES payment_counterparties(id) ON DELETE CASCADE,
        holder_name      VARCHAR(200) NOT NULL,
        card_number_enc  TEXT NOT NULL,
        card_last4       VARCHAR(4) NOT NULL,
        note             TEXT,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_cards_user ON payment_cards (user_id) WHERE user_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_payment_cards_counterparty ON payment_cards (counterparty_id) "
    "WHERE counterparty_id IS NOT NULL",

    # ── Регулярные платежи (п. 4.4, п. 13) ─────────────────────────────────
    #
    # Тот же календарь фиксированных платежей, дополненный тем, что по ТЗ
    # подставляется в заявку само: договор, реквизиты, назначение платежа,
    # лимит и согласующий. `auto_create` — заводить ли заявку календарём;
    # выключено — платёж только выбирают руками в «Создать заявку».
    """
    ALTER TABLE payment_fixed_templates
        ADD COLUMN IF NOT EXISTS contract_id INTEGER REFERENCES payment_contracts(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS counterparty_account_id INTEGER
            REFERENCES payment_counterparty_accounts(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS payment_purpose TEXT,
        ADD COLUMN IF NOT EXISTS amount_limit NUMERIC(14, 2),
        ADD COLUMN IF NOT EXISTS approver_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS object_type VARCHAR(16) NOT NULL DEFAULT 'service',
        ADD COLUMN IF NOT EXISTS auto_create BOOLEAN NOT NULL DEFAULT TRUE
    """,
    # `accounting_category` — категория учёта для платежа с типом объекта «Товар»
    # (п. 10): заявка получает её из справочника, как и остальное (п. 4.4).
    # `due_day` — число месяца, на которое платёж назначен: срок после короткого
    # месяца считается от него, а не от усечённой даты (31-е не съезжает на 28-е).
    """
    ALTER TABLE payment_fixed_templates
        ADD COLUMN IF NOT EXISTS accounting_category VARCHAR(16),
        ADD COLUMN IF NOT EXISTS due_day SMALLINT
    """,

    # ── Лимиты согласования (п. 6, п. 13) ──────────────────────────────────
    #
    # Это прежние «Приказы»: согласующий, лимит суммы, поставщики, проекты,
    # срок действия. ТЗ добавляет измерения матрицы — компанию, подразделение,
    # тип расхода, тип платежа и регулярность (NULL = любое). Номер и дата
    # Приказа остаются как необязательное основание лимита.
    """
    ALTER TABLE payment_approval_orders
        ADD COLUMN IF NOT EXISTS legal_entity_id INTEGER REFERENCES payment_legal_entities(id) ON DELETE CASCADE,
        ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
        ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES payment_categories(id) ON DELETE CASCADE,
        ADD COLUMN IF NOT EXISTS payment_method VARCHAR(16),
        ADD COLUMN IF NOT EXISTS request_kind VARCHAR(16)
    """,
    "ALTER TABLE payment_approval_orders ALTER COLUMN number DROP NOT NULL",
    "ALTER TABLE payment_approval_orders ALTER COLUMN issued_on DROP NOT NULL",
    "ALTER TABLE payment_approval_orders ALTER COLUMN starts_on DROP NOT NULL",

    # ── Маршруты согласования (п. 6, п. 13) ────────────────────────────────
    #
    # Строка маршрута отвечает на два вопроса для заявок своего класса: нужен
    # ли этап руководителя (п. 3: «если данный этап предусмотрен») и кто
    # утверждает, когда ни один лимит не подошёл. Пустое условие = «любое».
    """
    CREATE TABLE IF NOT EXISTS payment_approval_routes (
        id                SERIAL PRIMARY KEY,
        name              VARCHAR(200) NOT NULL,
        position          INTEGER NOT NULL DEFAULT 0,
        legal_entity_id   INTEGER REFERENCES payment_legal_entities(id) ON DELETE CASCADE,
        department_id     INTEGER REFERENCES departments(id) ON DELETE CASCADE,
        category_id       INTEGER REFERENCES payment_categories(id) ON DELETE CASCADE,
        payment_method    VARCHAR(16),
        request_kind      VARCHAR(16),
        amount_from       NUMERIC(14, 2),
        amount_to         NUMERIC(14, 2),
        manager_step      VARCHAR(8) NOT NULL DEFAULT 'auto',
        approver_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        is_active         BOOLEAN NOT NULL DEFAULT TRUE,
        note              TEXT,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,

    # ── Настройки процесса ─────────────────────────────────────────────────
    # Минимальное число поставщиков в новом закупе задаёт администратор (п. 4.2).
    """
    CREATE TABLE IF NOT EXISTS payment_settings (
        key         VARCHAR(64) PRIMARY KEY,
        value       JSONB NOT NULL,
        updated_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,

    # ── Заявка: поля ТЗ ────────────────────────────────────────────────────
    #
    # `stage` и `current_subtask_id` — денормализованное «где заявка сейчас»:
    # по ним строятся реестр, доски и фильтры без подзапроса на каждую строку.
    # `submitted_snapshot` — существенные условия на момент отправки (сумма,
    # поставщик, способ оплаты, карта, компания): изменились после возврата —
    # заявка согласуется заново, а не проходит с прежним «согласовано».
    """
    ALTER TABLE payment_requests
        ADD COLUMN IF NOT EXISTS request_kind VARCHAR(16),
        ADD COLUMN IF NOT EXISTS payment_method VARCHAR(16),
        ADD COLUMN IF NOT EXISTS card_recipient VARCHAR(16),
        ADD COLUMN IF NOT EXISTS card_holder_name VARCHAR(200),
        ADD COLUMN IF NOT EXISTS card_holder_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES payment_cards(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS card_number_enc TEXT,
        ADD COLUMN IF NOT EXISTS card_last4 VARCHAR(4),
        ADD COLUMN IF NOT EXISTS payment_purpose TEXT,
        ADD COLUMN IF NOT EXISTS object_type VARCHAR(16),
        ADD COLUMN IF NOT EXISTS accounting_category VARCHAR(16),
        ADD COLUMN IF NOT EXISTS justification TEXT,
        ADD COLUMN IF NOT EXISTS no_alternatives BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS no_alternatives_reason VARCHAR(32),
        ADD COLUMN IF NOT EXISTS no_alternatives_comment TEXT,
        ADD COLUMN IF NOT EXISTS supplier_choice_reason TEXT,
        ADD COLUMN IF NOT EXISTS counterparty_account_id INTEGER
            REFERENCES payment_counterparty_accounts(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS stage VARCHAR(24),
        ADD COLUMN IF NOT EXISTS current_subtask_id INTEGER,
        ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMP,
        ADD COLUMN IF NOT EXISTS submitted_snapshot JSONB,
        ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP,
        ADD COLUMN IF NOT EXISTS approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS approved_by_name VARCHAR(200),
        ADD COLUMN IF NOT EXISTS amount_approved NUMERIC(14, 2),
        ADD COLUMN IF NOT EXISTS paid_comment TEXT,
        ADD COLUMN IF NOT EXISTS received_on DATE,
        ADD COLUMN IF NOT EXISTS received_quantity VARCHAR(200),
        ADD COLUMN IF NOT EXISTS closing_docs_status VARCHAR(16) NOT NULL DEFAULT 'none',
        ADD COLUMN IF NOT EXISTS closing_docs_at TIMESTAMP
    """,
    # `legacy_step` — шаг первой версии процесса, на котором заявку застал перенос
    # (payments/legacy.py); у заявок, заведённых уже по ТЗ, пусто. Заявку, принятую
    # по прежним правилам, новые требования к полноте не останавливают.
    "ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS legacy_step SMALLINT",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_stage ON payment_requests (status, stage)",
    # Поиск дубля счёта: поставщик + номер счёта (п. 8) — остальное сверяется в запросе.
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_invoice "
    "ON payment_requests (counterparty_id, lower(invoice_number)) WHERE invoice_number IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_entity ON payment_requests (legal_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_department ON payment_requests (department_id)",

    # ── Поставщики заявки (п. 4.2) ─────────────────────────────────────────
    #
    # Варианты, из которых инициатор выбирал: у каждого своя стоимость, условия,
    # ссылка и вложение. Рекомендуемый (`is_recommended`) становится поставщиком
    # заявки. Альтернатива может быть вписана названием — заводить в справочник
    # каждого, у кого только спросили цену, незачем.
    """
    CREATE TABLE IF NOT EXISTS payment_request_offers (
        id               SERIAL PRIMARY KEY,
        request_id       INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        position         INTEGER NOT NULL DEFAULT 0,
        counterparty_id  INTEGER REFERENCES payment_counterparties(id) ON DELETE SET NULL,
        supplier_name    VARCHAR(200) NOT NULL,
        amount           NUMERIC(14, 2),
        terms            TEXT,
        link             TEXT,
        comment          TEXT,
        is_recommended   BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_request_offers_request "
    "ON payment_request_offers (request_id, position)",

    # ── Подзадачи (п. 2, п. 20) ────────────────────────────────────────────
    #
    # Этапы заявки материализованы строками, как раньше шаги: у подзадачи свой
    # исполнитель (человек либо роль-подразделение), состояние и статус —
    # колонка на доске исполнителя. Вид подзадачи встречается в заявке один раз:
    # «Оплата счёта» не плодится при возврате, а возвращается в работу.
    #
    # `state`: pending — ещё не дошли; open — у исполнителя; waiting — исполнитель
    # запросил уточнение и ждёт инициатора; done; skipped. `status` — статус
    # внутри своей доски (new / checking / ready …), `status_before` — куда
    # вернуть после уточнения.
    """
    CREATE TABLE IF NOT EXISTS payment_subtasks (
        id               SERIAL PRIMARY KEY,
        request_id       INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        kind             VARCHAR(32) NOT NULL,
        position         INTEGER NOT NULL DEFAULT 0,
        stage            VARCHAR(24) NOT NULL,
        role_code        VARCHAR(32) NOT NULL,
        assignee_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        assignee_name    VARCHAR(200),
        state            VARCHAR(16) NOT NULL DEFAULT 'pending',
        status           VARCHAR(24),
        status_before    VARCHAR(24),
        clarify_reason   VARCHAR(32),
        clarify_comment  TEXT,
        clarify_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        clarify_by_name  VARCHAR(200),
        clarify_at       TIMESTAMP,
        opened_at        TIMESTAMP,
        done_at          TIMESTAMP,
        done_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
        done_by_name     VARCHAR(200),
        outcome          VARCHAR(24),
        comment          TEXT,
        UNIQUE (request_id, kind)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_subtasks_request ON payment_subtasks (request_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_payment_subtasks_role "
    "ON payment_subtasks (role_code, kind) WHERE state IN ('open', 'waiting')",
    "CREATE INDEX IF NOT EXISTS idx_payment_subtasks_assignee "
    "ON payment_subtasks (assignee_id) WHERE state IN ('open', 'waiting')",

    # Вложение привязано к подзадаче (вместо номера шага) и, если это КП
    # конкретного поставщика, — к варианту поставщика.
    """
    ALTER TABLE payment_attachments
        ADD COLUMN IF NOT EXISTS subtask_kind VARCHAR(32),
        ADD COLUMN IF NOT EXISTS offer_id INTEGER REFERENCES payment_request_offers(id) ON DELETE SET NULL
    """,

    # ── Учёт имущества (пп. 10–12) ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS payment_asset_categories (
        id          SERIAL PRIMARY KEY,
        name        VARCHAR(200) NOT NULL,
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_asset_categories_name "
    "ON payment_asset_categories (lower(name))",

    # Город, подразделение, ответственный, место эксплуатации и инвентарный
    # номер — отдельными колонками (п. 12: «в структурированном виде, а не в
    # свободном текстовом поле»). Имя ответственного и отдела — снимком рядом
    # со ссылкой: имущество числится за человеком и после его увольнения.
    """
    CREATE TABLE IF NOT EXISTS payment_assets (
        id                   SERIAL PRIMARY KEY,
        request_id           INTEGER REFERENCES payment_requests(id) ON DELETE SET NULL,
        name                 VARCHAR(300) NOT NULL,
        category_id          INTEGER REFERENCES payment_asset_categories(id) ON DELETE SET NULL,
        serial_number        VARCHAR(120),
        inventory_number     VARCHAR(64) NOT NULL,
        received_on          DATE,
        cost                 NUMERIC(14, 2),
        legal_entity_id      INTEGER REFERENCES payment_legal_entities(id) ON DELETE SET NULL,
        city                 VARCHAR(120) NOT NULL,
        department_id        INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        department_name      VARCHAR(200),
        responsible_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        responsible_name     VARCHAR(200),
        location             VARCHAR(300) NOT NULL,
        status               VARCHAR(16) NOT NULL DEFAULT 'in_use',
        note                 TEXT,
        created_at           TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at           TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_assets_inventory ON payment_assets (lower(inventory_number))",
    "CREATE INDEX IF NOT EXISTS idx_payment_assets_responsible ON payment_assets (responsible_user_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_assets_request ON payment_assets (request_id)",

    # История владельцев и мест (п. 12: «должна сохраняться, а не
    # перезаписываться»): каждое перемещение — своя строка с «было» и «стало».
    """
    CREATE TABLE IF NOT EXISTS payment_asset_moves (
        id                        SERIAL PRIMARY KEY,
        asset_id                  INTEGER NOT NULL REFERENCES payment_assets(id) ON DELETE CASCADE,
        kind                      VARCHAR(16) NOT NULL DEFAULT 'moved',
        moved_at                  TIMESTAMP NOT NULL DEFAULT %(now)s,
        moved_by                  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        moved_by_name             VARCHAR(200),
        from_responsible_user_id  INTEGER,
        from_responsible_name     VARCHAR(200),
        from_department_name      VARCHAR(200),
        from_city                 VARCHAR(120),
        from_location             VARCHAR(300),
        from_status               VARCHAR(16),
        to_responsible_user_id    INTEGER,
        to_responsible_name       VARCHAR(200),
        to_department_name        VARCHAR(200),
        to_city                   VARCHAR(120),
        to_location               VARCHAR(300),
        to_status                 VARCHAR(16),
        comment                   TEXT,
        attachment_id             INTEGER REFERENCES payment_attachments(id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_asset_moves_asset ON payment_asset_moves (asset_id, id)",

    # ── Уведомления в портале (п. 17) ──────────────────────────────────────
    #
    # Строка — одному человеку об одном событии заявки. `actionable` — «от вас
    # ждут действия»: такая строка гаснет, когда подзадача выполнена кем угодно
    # из исполнителей, а не от того, что её увидели. Остальные («согласовано»,
    # «оплачено») гаснут, когда человек открыл заявку. `dedupe_key` не даёт
    # напоминанию о сроке прийти дважды за один день.
    """
    CREATE TABLE IF NOT EXISTS payment_notifications (
        id          BIGSERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        request_id  INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
        subtask_id  INTEGER,
        kind        VARCHAR(32) NOT NULL,
        actionable  BOOLEAN NOT NULL DEFAULT FALSE,
        title       VARCHAR(300) NOT NULL,
        body        TEXT,
        tone        VARCHAR(16) NOT NULL DEFAULT 'default',
        dedupe_key  VARCHAR(120),
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        read_at     TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_payment_notifications_unread "
    "ON payment_notifications (user_id, id DESC) WHERE read_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_payment_notifications_subtask "
    "ON payment_notifications (subtask_id) WHERE read_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_notifications_dedupe "
    "ON payment_notifications (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL",

    # ── Неизменяемая история (п. 18) ───────────────────────────────────────
    #
    # Запись истории нельзя ни поправить, ни стереть — даже запросом в обход
    # приложения. Исключение одно: действия самих внешних ключей (удаление
    # заявки администратором уносит её историю каскадом, удаление учётки
    # обнуляет ссылку на автора). Они идут изнутри системного триггера, поэтому
    # глубина вложенности у них больше единицы.
    """
    CREATE OR REPLACE FUNCTION payment_events_immutable()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF pg_trigger_depth() > 1 THEN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'payment_events: история заявки неизменяема';
    END;
    $$
    """,
    "DROP TRIGGER IF EXISTS trg_payment_events_immutable ON payment_events",
    """
    CREATE TRIGGER trg_payment_events_immutable
    BEFORE UPDATE OR DELETE ON payment_events
    FOR EACH ROW EXECUTE FUNCTION payment_events_immutable()
    """,
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
    # Поиск по назначению платежа (ТЗ «Закуп и оплата», п. 19).
    "CREATE INDEX IF NOT EXISTS idx_payment_requests_purpose_trgm "
    "ON payment_requests USING gin (COALESCE(payment_purpose, '') gin_trgm_ops)",
]

TABLES = (
    'payment_role_members', 'payment_projects', 'payment_categories',
    'payment_legal_entities', 'payment_counterparties', 'payment_contracts',
    'payment_approval_orders', 'payment_order_projects', 'payment_order_counterparties',
    'payment_fixed_templates', 'payment_requests', 'payment_request_items',
    'payment_request_steps', 'payment_events', 'payment_attachments',
    # ТЗ «Закуп и оплата»
    'payment_counterparty_accounts', 'payment_cards', 'payment_approval_routes',
    'payment_settings', 'payment_request_offers', 'payment_subtasks',
    'payment_asset_categories', 'payment_assets', 'payment_asset_moves',
    'payment_notifications',
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
    # Заявки первой версии (12 шагов) переводятся на этапы и подзадачи. Под
    # своим SAVEPOINT: сбой переноса не должен оставить раздел без схемы —
    # непереведённую заявку перенос подберёт при следующем старте.
    cursor.execute("SAVEPOINT payments_legacy")
    try:
        from . import legacy
        legacy.migrate(cursor)
        cursor.execute("RELEASE SAVEPOINT payments_legacy")
    except Exception:  # noqa: BLE001
        cursor.execute("ROLLBACK TO SAVEPOINT payments_legacy")
        import logging
        logging.exception('Оплата счетов: перенос заявок первой версии не выполнен')


def schema_ready(cursor):
    """Есть ли главная таблица раздела — по ней /ping честно говорит о состоянии."""
    cursor.execute("SELECT to_regclass('public.payment_requests') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
