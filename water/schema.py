"""Схема раздела «Учёт воды». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_water_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Остаток — колонка офиса, а не сумма журнала.** Выдачу проверяют по
  остатку на каждом шаге («в офисе нет воды» — одна из причин отказа), и
  складывать ради этого весь журнал каждый раз незачем. Согласованность держит
  транзакция: остаток и строка журнала меняются одним курсором, строка офиса
  при этом заперта FOR UPDATE. `CHECK (stock >= 0)` — последний рубеж: даже
  ошибка в коде не уведёт остаток в минус.

* **Офис в учёте — своя строка со ссылкой на справочник вики и снимком.**
  Справочник живой (адрес правят, офис уводят в архив), а журнал выдач —
  документ о том, где выдали воду В ТОТ день. `office_id` поэтому UNIQUE и
  ON DELETE SET NULL, а выдачи ссылаются на свой `water_offices.id`.

* **Выдача хранит всё, по чему принималось решение.** Парк, тарифы машины и
  число заказов — на момент выдачи (ТЗ: «парк водителя на момент выдачи»), плюс
  `orders_total` — показание счётчика CRM. По разнице этого счётчика считается,
  сколько поездок водитель сделал с прошлой выдачи: у CRM есть только «за
  сегодня / неделю / месяц / всё время», поездок «с даты» она не отдаёт.

* **Приветственный блок один раз — на уровне базы.** Частичные уникальные
  индексы по аккаунту и по ИИН: две вкладки или два офиса одновременно не
  выдадут его дважды, даже если проверка в коде когда-нибудь ошибётся. ИИН —
  потому что у одного человека в разных парках разные аккаунты.

* **Настройки — одна строка (`id = 1`).** Условия программы и пороги по
  умолчанию меняет региональный руководитель; свой порог офиса лежит в строке
  офиса и перекрывает общий (NULL — «как у всех»).
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Виды выдачи — дословно из ТЗ: «Приветственная / за активность».
ISSUE_KINDS = ('welcome', 'activity')

# Движения остатка, кроме выдач: поступление и пересчёт (инвентаризация).
# Пересчётом же заводится стартовый остаток, когда офис добавляют в учёт.
MOVEMENT_KINDS = ('intake', 'recount')

# Условия программы по умолчанию — из ТЗ и ответов постановщика.
#
# Тарифы — коды Флита, в которых CRM отдаёт тарифы машины: `business` — это
# Business (у Toyota Camry 2025 в снимках «Посылок» стоит именно он, а Комфорт
# там отдельным `comfort`), `ultimate` — Premier, единственный тариф линейки
# Ultima в Алматы («Ultima: тариф Premier» на pro.yandex.com/kz-ru).
DEFAULT_TARIFFS = ('business', 'ultimate')

_STATEMENTS = [

    """
    CREATE TABLE IF NOT EXISTS water_settings (
        id                SMALLINT PRIMARY KEY CHECK (id = 1),
        min_trips         INTEGER NOT NULL DEFAULT 20 CHECK (min_trips >= 0),
        cooldown_days     INTEGER NOT NULL DEFAULT 7 CHECK (cooldown_days >= 0),
        welcome_blocks    INTEGER NOT NULL DEFAULT 1 CHECK (welcome_blocks >= 1),
        activity_blocks   INTEGER NOT NULL DEFAULT 1 CHECK (activity_blocks >= 1),
        tariffs           JSONB NOT NULL DEFAULT '["business", "ultimate"]'::jsonb,
        low_threshold     INTEGER NOT NULL DEFAULT 20 CHECK (low_threshold >= 0),
        buy_threshold     INTEGER NOT NULL DEFAULT 10 CHECK (buy_threshold >= 0),
        -- Кому писать в Telegram «Требуется закупка». Список, а не роль:
        -- регионального специалиста ещё не наняли, и добавит его руководитель.
        notify_user_ids   JSONB NOT NULL DEFAULT '[]'::jsonb,
        updated_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by_name   VARCHAR(200),
        updated_at        TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    """
    CREATE TABLE IF NOT EXISTS water_offices (
        id              SERIAL PRIMARY KEY,
        office_id       INTEGER UNIQUE REFERENCES wiki_offices(id) ON DELETE SET NULL,
        city            VARCHAR(120) NOT NULL,
        name            VARCHAR(200) NOT NULL,
        address         TEXT,
        stock           INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
        -- Свои пороги офиса. NULL — действует общий из water_settings.
        low_threshold   INTEGER CHECK (low_threshold >= 0),
        buy_threshold   INTEGER CHECK (buy_threshold >= 0),
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name VARCHAR(200),
        created_at      TIMESTAMP NOT NULL DEFAULT %s,
        updated_at      TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % (_NOW, _NOW),

    # Поступления и пересчёты. Выдачи сюда не пишутся: у них свой журнал с
    # водителем. delta со знаком: поступление всегда > 0, пересчёт — сколько
    # оказалось на полке против того, что считала система.
    """
    CREATE TABLE IF NOT EXISTS water_movements (
        id               SERIAL PRIMARY KEY,
        water_office_id  INTEGER NOT NULL REFERENCES water_offices(id),
        kind             VARCHAR(16) NOT NULL CHECK (kind IN ('intake', 'recount')),
        delta            INTEGER NOT NULL,
        stock_after      INTEGER NOT NULL CHECK (stock_after >= 0),
        comment          TEXT,
        actor_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name       VARCHAR(200),
        created_at       TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    """
    CREATE TABLE IF NOT EXISTS water_issues (
        id                 SERIAL PRIMARY KEY,
        water_office_id    INTEGER NOT NULL REFERENCES water_offices(id),
        city               VARCHAR(120) NOT NULL,
        office_name        VARCHAR(200) NOT NULL,
        kind               VARCHAR(16) NOT NULL CHECK (kind IN ('welcome', 'activity')),
        blocks             INTEGER NOT NULL CHECK (blocks > 0),
        stock_after        INTEGER NOT NULL CHECK (stock_after >= 0),

        driver_account_id  VARCHAR(64) NOT NULL,
        driver_iin         VARCHAR(20),
        driver_name        VARCHAR(200),
        driver_phone       VARCHAR(32),
        driver_park        VARCHAR(160),
        driver_park_id     VARCHAR(64),
        driver_tariffs     JSONB,
        -- Показание счётчика заказов CRM в момент выдачи — база для следующей.
        orders_total       INTEGER,
        -- Сколько заказов засчитано этой выдаче и откуда число: с прошлой
        -- выдачи, за последние 7 дней или «новый водитель» (ноль).
        orders_counted     INTEGER,
        orders_basis       VARCHAR(16),
        -- ФК приветственной выдачи: passed — по CRM, confirmed — подтвердил
        -- сотрудник (CRM статуса не знала). У выдачи за активность — NULL.
        fk_source          VARCHAR(16),
        driver_info        JSONB,

        issued_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
        issued_by_name     VARCHAR(200),
        created_at         TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    "INSERT INTO water_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING",

    "CREATE INDEX IF NOT EXISTS idx_water_offices_active ON water_offices(is_active, city, name)",
    "CREATE INDEX IF NOT EXISTS idx_water_movements_office "
    "ON water_movements(water_office_id, created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_water_issues_created ON water_issues(created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_water_issues_office "
    "ON water_issues(water_office_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_water_issues_account "
    "ON water_issues(driver_account_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_water_issues_iin "
    "ON water_issues(driver_iin, created_at DESC) WHERE driver_iin IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_water_issues_issued_by ON water_issues(issued_by)",
    # Приветственный блок — один на аккаунт и один на человека (ИИН).
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_water_welcome_account "
    "ON water_issues(driver_account_id) WHERE kind = 'welcome'",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_water_welcome_iin "
    "ON water_issues(driver_iin) WHERE kind = 'welcome' AND driver_iin IS NOT NULL",
]

# Миграции по живой базе. Идут ПОСЛЕ таблиц и ПЕРЕД индексами (порядок держит
# init_water_schema, см. parcels/schema.py — у «Обращений» обратный порядок
# уронил прод 17.08.2026).
_MIGRATIONS = [
    # ФК нового водителя (30.09.2026, до первой выкладки — для уже развёрнутых стендов).
    "ALTER TABLE water_issues ADD COLUMN IF NOT EXISTS fk_source VARCHAR(16)",
]


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_water_schema(cursor):
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
    """Развёрнута ли схема: отличает «раздел ещё не поднялся» от «учёт пуст».

    Спрашивает последнюю по порядку таблицу: разворот идёт одним SAVEPOINT, так
    что есть она — есть и остальные.
    """
    cursor.execute("SELECT to_regclass('public.water_issues') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
