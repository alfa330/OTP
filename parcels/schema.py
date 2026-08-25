"""Схема раздела «Посылки». Таблицы parcels и parcel_events.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается один раз при
старте из Database._init_db через init_parcels_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Офис хранится ссылкой И снимком.** `office_id` смотрит в `wiki_offices`, но
  рядом лежат `office_name`/`office_address`. Причина не в скорости: справочник
  офисов живой — адрес правят, запись уводят в архив, город переименовывают.
  Карточка посылки — документ о том, где вещь лежала В ТОТ день, и переписывать
  её задним числом нельзя. Тот же приём, что у `crm_tickets.tg_chat_title`.

* **Данные водителя — тоже снимок.** ФИО, телефон, парк и машина приезжают из
  CRM yataxi в момент заполнения. Водитель завтра сменит номер, а посылку ищут
  по тому, что он оставил дежурному. Полный ответ CRM лежит в `driver_info`
  (JSONB) — из него потом можно достать поле, которое сегодня не понадобилось,
  не ходя в CRM заново.

* **Статус — три значения из ТЗ и ни одним больше.** CHECK, а не справочная
  таблица: набор задан постановкой целиком («В офисе / Передали получателю /
  Передали отправителю»), а расширять CHECK на живой базе больно, поэтому
  четвёртое состояние, если оно появится, приедет отдельной миграцией
  осознанно.

* **`status_changed_at` и `status_changed_by` — колонки карточки**, а не только
  строки в истории. ТЗ требует их прямо в реестре («Дата изменения статуса»,
  «Кто изменил статус»), и считать их подзапросом к истории на каждую строку
  списка — лишний джойн на самом горячем экране.

* **История отдельной таблицей.** «Так же необходимо отобразить историю
  изменений» — это лента, а не одна последняя правка: карточку ведут разные
  дежурные, и вопрос «кто передал посылку» задают через месяц.

* **Поиск — trigram.** ТЗ просит искать по восьми полям сразу, в том числе
  «содержит» по ФИО и адресу. `pg_trgm` в базе уже стоит (им пользуется вики),
  поэтому GIN-индексы по `gin_trgm_ops` дают ILIKE '%…%' без полного скана.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Статусы посылки — дословно из ТЗ. Порядок = порядок жизни записи.
PARCEL_STATUSES = ('in_office', 'given_to_recipient', 'given_to_sender')

# Тип посылки. 'other' закрывает «другое» из ТЗ; описание при этом обязательно,
# так что «другое» никогда не остаётся без расшифровки.
PARCEL_KINDS = ('parcel', 'document', 'other')

# Виды событий истории. `comment` — комментарий без смены статуса: «Статус
# изменён: В офисе → В офисе» было бы неправдой. CHECK на этот столбец не
# ставим намеренно: новый вид события не должен требовать миграции живой базы,
# а неизвестный вид лента показывает нейтральной строкой.
EVENT_KINDS = ('created', 'status', 'comment', 'edited', 'driver_synced')


_STATEMENTS = [

    # ──────────────────────────────────────────────────────────────────────
    # КАРТОЧКА ПОСЫЛКИ
    #
    # `received_on` — дата приёма (ТЗ: «Когда посылку оставили в офисе»), её
    # ставит человек календарём и она НЕ равна created_at: карточку заводят
    # задним числом, разгребая накопившееся.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS parcels (
        id                     SERIAL PRIMARY KEY,

        received_on            DATE NOT NULL,

        -- Где лежит. Город обязателен, офис — ссылка на справочник вики.
        -- ON DELETE SET NULL, а не CASCADE: удалённый из справочника офис не
        -- должен утаскивать за собой карточки посылок, для того рядом и снимок.
        city                   VARCHAR(120) NOT NULL,
        office_id              INTEGER REFERENCES wiki_offices(id) ON DELETE SET NULL,
        office_name            VARCHAR(200),
        office_address         TEXT,

        -- Водитель. account_id — то, что уходит в CRM yataxi; остальное снимок.
        driver_account_id      VARCHAR(64) NOT NULL,
        driver_name            VARCHAR(200),
        driver_phone           VARCHAR(32),
        driver_park            VARCHAR(160),
        driver_license         VARCHAR(64),
        driver_callsign        VARCHAR(120),
        driver_car             VARCHAR(200),
        driver_info            JSONB,
        driver_synced_at       TIMESTAMP,

        kind                   VARCHAR(16) NOT NULL
                               CHECK (kind IN ('parcel', 'document', 'other')),
        description            TEXT NOT NULL,

        sender                 VARCHAR(200),
        recipient              VARCHAR(200),
        order_number           VARCHAR(64),

        status                 VARCHAR(24) NOT NULL DEFAULT 'in_office'
                               CHECK (status IN ('in_office', 'given_to_recipient',
                                                 'given_to_sender')),
        status_changed_at      TIMESTAMP,
        status_changed_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        status_changed_by_name VARCHAR(200),

        comment                TEXT,

        created_by             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name        VARCHAR(200),
        created_at             TIMESTAMP NOT NULL DEFAULT %s,
        updated_at             TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % (_NOW, _NOW),

    # ──────────────────────────────────────────────────────────────────────
    # ИСТОРИЯ
    #
    # payload держит «что на что поменяли» — набор полей у разных видов
    # событий разный, и раскладывать его по колонкам значило бы заводить
    # колонку под каждое поле карточки.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS parcel_events (
        id            SERIAL PRIMARY KEY,
        parcel_id     INTEGER NOT NULL REFERENCES parcels(id) ON DELETE CASCADE,
        kind          VARCHAR(24) NOT NULL,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name    VARCHAR(200),
        payload       JSONB,
        created_at    TIMESTAMP NOT NULL DEFAULT %s
    )
    """ % _NOW,

    # ── Индексы списка и фильтров (Город → Офис → Дата → Менеджер) ────────
    "CREATE INDEX IF NOT EXISTS idx_parcels_status ON parcels(status)",
    "CREATE INDEX IF NOT EXISTS idx_parcels_city ON parcels(city)",
    "CREATE INDEX IF NOT EXISTS idx_parcels_office ON parcels(office_id)",
    "CREATE INDEX IF NOT EXISTS idx_parcels_received ON parcels(received_on DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_parcels_created_by ON parcels(created_by)",
    # Точечный поиск «эта посылка того же водителя» — по равенству, не по «содержит».
    "CREATE INDEX IF NOT EXISTS idx_parcels_driver_account ON parcels(driver_account_id)",

    # ── Поиск «содержит» по восьми полям ТЗ ───────────────────────────────
    # Отдельный GIN на каждое поле, а не один по склейке: склейка требовала бы
    # либо генерируемого столбца, либо индекса по выражению, и любой из них
    # пришлось бы пересобирать при добавлении девятого поля поиска.
    """
    CREATE INDEX IF NOT EXISTS idx_parcels_trgm_driver_name
        ON parcels USING gin (driver_name gin_trgm_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_parcels_trgm_driver_phone
        ON parcels USING gin (driver_phone gin_trgm_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_parcels_trgm_sender
        ON parcels USING gin (sender gin_trgm_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_parcels_trgm_recipient
        ON parcels USING gin (recipient gin_trgm_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_parcels_trgm_order_number
        ON parcels USING gin (order_number gin_trgm_ops)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_parcels_trgm_office_name
        ON parcels USING gin (office_name gin_trgm_ops)
    """,

    "CREATE INDEX IF NOT EXISTS idx_parcel_events_parcel ON parcel_events(parcel_id, id)",
]


# Миграции по живой базе. Пока пусто — раздел новый; список объявлен, чтобы
# порядок «таблицы → ALTER'ы → индексы» в init_parcels_schema не пришлось
# вводить задним числом (у «Обращений» именно это уронило прод 17.08.2026).
_MIGRATIONS = []


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_parcels_schema(cursor):
    """Разворачивает схему раздела. Курсор из _init_db, транзакцией правит вызывающий.

    Порядок — таблицы, потом ALTER'ы, и только потом индексы: на уже развёрнутой
    базе индекс по новому столбцу иначе создаётся раньше самого столбца и
    падает, а падение откатывает ВЕСЬ разворот схемы.

    pg_trgm ставим отдельным CREATE EXTENSION под своим SAVEPOINT: на проде оно
    уже есть (им пользуется поиск вики), но на чистой базе разработчика — нет, и
    без расширения весь разворот раздела упал бы на первом GIN-индексе. Прав на
    CREATE EXTENSION может не быть — тогда раздел поднимается без trigram-
    индексов, а ILIKE работает полным сканом: на реестре в тысячи строк это
    незаметно, и терять из-за этого весь раздел незачем.
    """
    cursor.execute("SAVEPOINT parcels_trgm")
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT parcels_trgm")
        trigram_ready = False
    else:
        cursor.execute("RELEASE SAVEPOINT parcels_trgm")
        trigram_ready = True

    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if _is_table(statement):
            continue
        if 'gin_trgm_ops' in statement and not trigram_ready:
            continue
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «раздел ещё не поднялся» от «реестр пуст»."""
    cursor.execute("SELECT to_regclass('public.parcels') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
