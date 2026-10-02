"""Схема раздела «Термокороба». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_thermoboxes_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Строка таблицы — офис из справочника вики.** `office_id` UNIQUE: у офиса
  одна строка условий, как одна строка в листе. Город, имя и адрес лежат и
  снимком — на случай, если офис из справочника удалят: строка и её история
  тогда остаются читаемыми. На экран же идёт живой адрес справочника.

* **Числа — колонки с CHECK (>= 0).** «Только целые числа, без отрицательных
  значений» (постановка #363) проверяет код, а CHECK — последний рубеж: даже
  ошибка в коде не запишет в остаток минус.

* **`version` — защита от затирания.** Экран присылает версию строки, которую
  видел; разошлась — сохранение отклоняется целиком, а не молча перетирает
  правку коллеги, сделанную минуту назад.

* **История — своя таблица, строка на сохранение.** `changes` — список
  «поле: было → стало» одной правки; отдельные колонки «было/стало» на каждое
  из восьми полей были бы той же информацией в шестнадцати местах.

* **Памятка — одна строка (`id = 1`)**, пункты — JSONB-список. Своя таблица
  на пункты ничего не дала бы: памятку правят целиком и читают целиком.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [

    """
    CREATE TABLE IF NOT EXISTS thermobox_offices (
        id                 SERIAL PRIMARY KEY,
        office_id          INTEGER UNIQUE REFERENCES wiki_offices(id) ON DELETE SET NULL,
        city               VARCHAR(120) NOT NULL,
        name               VARCHAR(200) NOT NULL,
        address            TEXT,
        free_boxes         INTEGER NOT NULL DEFAULT 0 CHECK (free_boxes >= 0),
        thermo_bags        INTEGER NOT NULL DEFAULT 0 CHECK (thermo_bags >= 0),
        used_boxes         INTEGER NOT NULL DEFAULT 0 CHECK (used_boxes >= 0),
        -- Код тарифа (thermoboxes.rules.TARIFF_LABELS). Без CHECK намеренно:
        -- новое значение списка — строка в коде, а не миграция.
        tariff             VARCHAR(32) NOT NULL DEFAULT 'auto_couriers',
        -- «15+ заказов»: 15. Ноль — нормы нет.
        min_orders         INTEGER NOT NULL DEFAULT 0 CHECK (min_orders >= 0),
        -- «Неделя (7 дней)»: 7.
        period_days        INTEGER NOT NULL DEFAULT 7 CHECK (period_days > 0),
        -- «5 000 тенге»: 5000. Ноль — «Нет депозита».
        deposit_tenge      INTEGER NOT NULL DEFAULT 0 CHECK (deposit_tenge >= 0),
        special_condition  VARCHAR(200),
        is_active          BOOLEAN NOT NULL DEFAULT TRUE,
        version            INTEGER NOT NULL DEFAULT 1,
        updated_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by_name    VARCHAR(200),
        updated_at         TIMESTAMP NOT NULL DEFAULT %s,
        created_at         TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % (_NOW, _NOW),

    # kind: created / edited / hidden / shown. Без CHECK — список растёт кодом.
    """
    CREATE TABLE IF NOT EXISTS thermobox_events (
        id               SERIAL PRIMARY KEY,
        row_id           INTEGER NOT NULL REFERENCES thermobox_offices(id) ON DELETE CASCADE,
        kind             VARCHAR(16) NOT NULL,
        changes          JSONB NOT NULL DEFAULT '[]'::jsonb,
        actor_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name       VARCHAR(200),
        created_at       TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    """
    CREATE TABLE IF NOT EXISTS thermobox_memo (
        id               SMALLINT PRIMARY KEY CHECK (id = 1),
        title            VARCHAR(200),
        items            JSONB NOT NULL DEFAULT '[]'::jsonb,
        updated_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by_name  VARCHAR(200),
        updated_at       TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    "INSERT INTO thermobox_memo (id) VALUES (1) ON CONFLICT (id) DO NOTHING",

    "CREATE INDEX IF NOT EXISTS idx_thermobox_offices_active ON thermobox_offices(is_active, city, name)",
    "CREATE INDEX IF NOT EXISTS idx_thermobox_events_row "
    "ON thermobox_events(row_id, created_at DESC, id DESC)",
]

# Миграции по живой базе. Идут ПОСЛЕ таблиц и ПЕРЕД индексами — порядок держит
# init_thermoboxes_schema (у «Обращений» обратный порядок уронил прод 17.08.2026).
_MIGRATIONS = []


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_thermoboxes_schema(cursor):
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
    """Развёрнута ли схема: отличает «раздел ещё не поднялся» от «таблица пуста».

    Спрашивает последнюю по порядку таблицу: разворот идёт одним SAVEPOINT, так
    что есть она — есть и остальные.
    """
    cursor.execute("SELECT to_regclass('public.thermobox_memo') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
