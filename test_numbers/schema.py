"""Схема «Реестра тестовых номеров». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_test_numbers_schema(cursor).

Таблица реестра стоит в условиях исключения десятков запросов (табло, отчёты,
ИИ-оценка), поэтому DDL намеренно простой: ни внешних ключей на разделы, ни
триггеров — только `users`, которая к этому моменту есть всегда. Не развернись
таблица — упали бы не только раздел, но и все эти расчёты.

* **`phone_key` — последние десять цифр** (test_numbers/keys.py). UNIQUE: у
  номера один владелец, а повторный ввод того же номера в другом виде
  («8 701…» после «+7 701…») — это тот же номер. CHECK держит формат, на который
  опираются запросы к Oktell: ключ подставляется туда литералом.
* **`phone_display` — номер для экрана**, как его привёл раздел. По ключу его не
  восстановить: у чужого номера код страны в ключ не входит.
* **Удаление — настоящее**, без пометки «удалён»: иначе каждое условие
  исключения в каждом расчёте несло бы ещё и «AND removed_at IS NULL». След
  правок — в журнале `test_phone_number_events`: убрать номер = вернуть его
  звонки в цифры табло, и кто это сделал, должно быть видно.
"""

_STATEMENTS = [

    """
    CREATE TABLE IF NOT EXISTS test_phone_numbers (
        id              SERIAL PRIMARY KEY,
        phone_key       VARCHAR(10) NOT NULL UNIQUE CHECK (phone_key ~ '^[0-9]{10}$'),
        phone_display   VARCHAR(32) NOT NULL,
        owner_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,

    # kind: added / owner_changed / removed. Без CHECK — список растёт кодом.
    # Снимок номера и владельца: строки реестра после удаления нет.
    """
    CREATE TABLE IF NOT EXISTS test_phone_number_events (
        id              SERIAL PRIMARY KEY,
        kind            VARCHAR(16) NOT NULL,
        phone_key       VARCHAR(10) NOT NULL,
        phone_display   VARCHAR(32) NOT NULL,
        owner_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        owner_name      VARCHAR(200),
        actor_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name      VARCHAR(200),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_test_phone_number_events_created "
    "ON test_phone_number_events(created_at DESC, id DESC)",
]

_MIGRATIONS = []


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_test_numbers_schema(cursor):
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
    """Развёрнута ли схема: «раздел ещё не поднялся» против «реестр пуст»."""
    cursor.execute("SELECT to_regclass('public.test_phone_number_events') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
