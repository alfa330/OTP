"""Схема раздела «Списки Байги». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_baiga_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Неделя = загрузка.** `baiga_uploads` — журнал загрузок: кто, когда, какой
  файл, за какой период и сколько строк. Активная загрузка у недели одна
  (уникальный частичный индекс): повторная загрузка той же недели ЗАМЕНЯЕТ её
  (п. 8 постановки: «заменяет, а не дублирует»). Старая запись журнала остаётся
  со статусом `replaced`/`deleted` — история того, кто что грузил, нужна именно
  потому, что в файле персональные данные.

* **Строки заменённой или удалённой недели удаляются**, а не прячутся флагом:
  искать по ним нельзя, а хранить ФИО и номера ВУ дольше, чем они нужны, —
  незачем. По той же причине у такой недели удаляется и исходный файл.

* **Исходник — в базе (`baiga_upload_files`, BYTEA), а не в бакете.** Файл
  недели ~140 КБ, ~7 МБ в год. Зато загрузка атомарна целиком: строки, запись
  журнала и сам файл ложатся одной транзакцией (п. 4: «все строки или ни
  одной»), и у журнала не бывает записи без файла или файла без записи. Своя
  таблица — чтобы широкие байты не читались каждым запросом к журналу.

* **Период денормализован в строку** (`baiga_rows.period_start`): фильтр по
  неделе и сортировка по ней — без JOIN на каждой странице поиска.

* **`campaign`** — задел под другие акции (открытый вопрос п. 9 постановки):
  поле стоит, интерфейс пока только про Байгу.

* **Ключи поиска хранятся готовыми**: `driver_key` (ID в нижнем регистре),
  `license_key` (номер ВУ латиницей без пробелов) и `search_text` (ФИО со
  свёрнутыми казахскими буквами + ключи). Сворачивать на каждом запросе все
  строки всех недель — лишняя работа; свёртка одна, в baiga/parse.py.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [

    # status: active / replaced / deleted. Без CHECK — список держит код
    # (решение по разделам портала: новое значение — строка в коде, а не миграция).
    """
    CREATE TABLE IF NOT EXISTS baiga_uploads (
        id                  SERIAL PRIMARY KEY,
        campaign            VARCHAR(32) NOT NULL DEFAULT 'baiga',
        period_start        DATE NOT NULL,
        period_end          DATE NOT NULL,
        period_source       VARCHAR(8) NOT NULL DEFAULT 'name',
        week_number         SMALLINT,
        file_name           TEXT NOT NULL,
        file_size           INTEGER NOT NULL,
        file_sha256         CHAR(64) NOT NULL,
        rows_count          INTEGER NOT NULL,
        drivers_count       INTEGER NOT NULL,
        prize_rows          INTEGER NOT NULL DEFAULT 0,
        prize_total         BIGINT NOT NULL DEFAULT 0,
        sheets              JSONB NOT NULL DEFAULT '[]'::jsonb,
        warnings_count      INTEGER NOT NULL DEFAULT 0,
        status              VARCHAR(16) NOT NULL DEFAULT 'active',
        uploaded_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_by_name    VARCHAR(200),
        uploaded_at         TIMESTAMP NOT NULL DEFAULT %s,
        closed_by           INTEGER REFERENCES users(id) ON DELETE SET NULL,
        closed_by_name      VARCHAR(200),
        closed_at           TIMESTAMP,
        replaced_by         INTEGER REFERENCES baiga_uploads(id) ON DELETE SET NULL
    )
    """ % _NOW,

    """
    CREATE TABLE IF NOT EXISTS baiga_upload_files (
        upload_id   INTEGER PRIMARY KEY REFERENCES baiga_uploads(id) ON DELETE CASCADE,
        content     BYTEA NOT NULL
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS baiga_rows (
        id              BIGSERIAL PRIMARY KEY,
        upload_id       INTEGER NOT NULL REFERENCES baiga_uploads(id) ON DELETE CASCADE,
        period_start    DATE NOT NULL,
        sheet_order     SMALLINT NOT NULL,
        row_number      INTEGER NOT NULL,
        zachet          VARCHAR(64) NOT NULL,
        position        INTEGER NOT NULL,
        date_text       VARCHAR(16) NOT NULL,
        row_date        DATE,
        week_number     SMALLINT NOT NULL,
        driver_name     VARCHAR(255) NOT NULL,
        prize_name      VARCHAR(255) NOT NULL DEFAULT '',
        prize_amount    INTEGER,
        has_prize       BOOLEAN NOT NULL DEFAULT FALSE,
        amount          BIGINT NOT NULL,
        trips           INTEGER NOT NULL,
        city            VARCHAR(120) NOT NULL DEFAULT '',
        park            VARCHAR(255) NOT NULL DEFAULT '',
        license         VARCHAR(64) NOT NULL DEFAULT '',
        license_key     VARCHAR(64) NOT NULL DEFAULT '',
        driver_id       VARCHAR(64) NOT NULL,
        driver_key      VARCHAR(64) NOT NULL,
        search_text     TEXT NOT NULL
    )
    """,

    # kind: export / source. Журнал выгрузок: персональные данные выносятся из
    # системы только здесь, и каждое такое движение должно оставлять след (п. 2).
    """
    CREATE TABLE IF NOT EXISTS baiga_exports (
        id              SERIAL PRIMARY KEY,
        kind            VARCHAR(16) NOT NULL,
        mode            VARCHAR(16),
        filters         JSONB NOT NULL DEFAULT '{}'::jsonb,
        rows_count      INTEGER NOT NULL DEFAULT 0,
        upload_id       INTEGER REFERENCES baiga_uploads(id) ON DELETE SET NULL,
        actor_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name      VARCHAR(200),
        created_at      TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    # Одна активная загрузка на неделю акции — на этом держится «повторная
    # загрузка заменяет, а не дублирует», даже при двух одновременных загрузках.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_baiga_uploads_active_week "
    "ON baiga_uploads(campaign, period_start) WHERE status = 'active'",
    "CREATE INDEX IF NOT EXISTS idx_baiga_uploads_recent ON baiga_uploads(uploaded_at DESC, id DESC)",

    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_upload ON baiga_rows(upload_id)",
    # Порядок по умолчанию — как в файле: свежая неделя, лист, место.
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_default_order "
    "ON baiga_rows(period_start DESC, sheet_order, position, id)",
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_driver ON baiga_rows(driver_key)",
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_license ON baiga_rows(license_key)",
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_city ON baiga_rows(city)",
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_park ON baiga_rows(park)",
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_zachet ON baiga_rows(zachet)",

    "CREATE INDEX IF NOT EXISTS idx_baiga_exports_recent ON baiga_exports(created_at DESC, id DESC)",
]

# Миграции по живой базе. Идут ПОСЛЕ таблиц и ПЕРЕД индексами — порядок держит
# init_baiga_schema (у «Обращений» обратный порядок уронил прод 17.08.2026).
_MIGRATIONS = []

# Триграммный индекс под поиск «содержит» по ФИО, ВУ и ID. Под своим SAVEPOINT,
# как у «Оплаты счетов»: без расширения pg_trgm раздел поднимается без индекса
# (поиск тогда идёт полным сканом — на десятках тысяч строк это миллисекунды),
# а не падает целиком.
_TRGM_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS idx_baiga_rows_search_trgm "
    "ON baiga_rows USING gin (search_text gin_trgm_ops)",
]


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_baiga_schema(cursor):
    """Разворачивает схему. Курсор из _init_db, транзакцией правит вызывающий."""
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)
    cursor.execute("SAVEPOINT baiga_trgm")
    try:
        for statement in _TRGM_STATEMENTS:
            cursor.execute(statement)
    except Exception:  # noqa: BLE001 — без индекса поиск просто медленнее
        cursor.execute("ROLLBACK TO SAVEPOINT baiga_trgm")
    else:
        cursor.execute("RELEASE SAVEPOINT baiga_trgm")


def schema_is_ready(cursor):
    """Развёрнута ли схема: отличает «раздел ещё не поднялся» от «недель нет».

    Спрашивает последнюю по порядку таблицу: разворот идёт одним SAVEPOINT, так
    что есть она — есть и остальные.
    """
    cursor.execute("SELECT to_regclass('public.baiga_exports') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
