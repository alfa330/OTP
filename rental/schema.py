# -*- coding: utf-8 -*-
"""Схема раздела «Аренда авто». Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_rental_schema(cursor) — своим
SAVEPOINT'ом, как у «Кодов 2FA»: не развернулась — раздел недоступен, портал работает.

Ночной отчёт (#398):

* **Отчёт — снимок на момент сбора**, а не живая выборка: ТЗ п. 4 требует историю, «данные
  за предыдущие даты и время заполнения». Один отчёт на пару (вид, плановый момент) —
  уникальный ключ; повторный сбор того же слота обновляет строки, а не плодит отчёты.

* **«Состояние» хранит, откуда оно**: `address` — подставлено по адресу GPS, `manual` —
  поставила ночная смена. Пересбор отчёта («Обновить») заново считает только подставленное,
  ручное остаётся.

* **Журнал ручных правок не ссылается на строку внешним ключом**: строку пересбор может
  убрать (машина пропала из GPS), а запись «кто и когда менял» должна остаться. Имя автора —
  снимком, как в журнале «Кодов 2FA».

Наличие и направления (#391):

* **Карточки — данные, а не код**: заводятся из макета Зарины один раз, дальше их ведут сотрудники
  аренды в разделе, каждая правка — строка в rental_card_log.
* **Свободные — ручное поле карточки** (ТЗ v5, 3.1): free_count и кто/когда его поставил. Снимок
  Yume (rental_fleet_sync) даёт только занятые и это поле не трогает. free_at пусто — наличие
  ещё ни разу не указывали, и карточка так и говорит, а не показывает «0».
* **Направление хранит снимок** карточки, офиса и оператора: реестр читается и после правок.
* **История только пополняется**: версии «Условий работы» и журналы правок защищены триггером
  (ТЗ 10.3 — «удаление пользователем истории недоступно»).
"""

_STATEMENTS = [

    # kind: night (01:00) / morning (07:00). status: collecting / ready / failed.
    # sources — по источнику {ok, count, error}: что ответило в этом сборе.
    """
    CREATE TABLE IF NOT EXISTS rental_night_reports (
        id             SERIAL PRIMARY KEY,
        kind           VARCHAR(16) NOT NULL,
        slot_at        TIMESTAMP NOT NULL,
        status         VARCHAR(16) NOT NULL DEFAULT 'collecting',
        collected_at   TIMESTAMP,
        sources        JSONB NOT NULL DEFAULT '{}'::jsonb,
        error          TEXT,
        created_by     INTEGER,
        started_at     TIMESTAMP NOT NULL,
        created_at     TIMESTAMP NOT NULL,
        updated_at     TIMESTAMP NOT NULL,
        CONSTRAINT rental_night_reports_slot UNIQUE (kind, slot_at)
    )
    """,

    # gps_status: online / offline / none. rent_status: in_rent / no; NULL — Yume не ответил.
    # state: base / repair / sale; state_source: address / manual.
    """
    CREATE TABLE IF NOT EXISTS rental_night_rows (
        id             SERIAL PRIMARY KEY,
        report_id      INTEGER NOT NULL REFERENCES rental_night_reports(id) ON DELETE CASCADE,
        plate          VARCHAR(16) NOT NULL,
        gps_site       VARCHAR(32),
        gps_sites      VARCHAR(80),
        gps_status     VARCHAR(16) NOT NULL,
        signal_at      TIMESTAMP,
        address        TEXT,
        lat            DOUBLE PRECISION,
        lon            DOUBLE PRECISION,
        rent_status    VARCHAR(16),
        in_fleet       BOOLEAN NOT NULL DEFAULT FALSE,
        model          VARCHAR(120),
        state          VARCHAR(16),
        state_source   VARCHAR(16),
        state_by       INTEGER,
        state_at       TIMESTAMP,
        CONSTRAINT rental_night_rows_plate UNIQUE (report_id, plate)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS rental_night_state_log (
        id               SERIAL PRIMARY KEY,
        report_id        INTEGER NOT NULL,
        plate            VARCHAR(16) NOT NULL,
        old_state        VARCHAR(16),
        new_state        VARCHAR(16),
        changed_by       INTEGER,
        changed_by_name  VARCHAR(200),
        changed_at       TIMESTAMP NOT NULL
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_rental_night_reports_slot ON rental_night_reports (slot_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rental_night_state_log_report ON rental_night_state_log (report_id, plate)",

    # ── Наличие и направления (#391) ──────────────────────────────────────────

    # Карточка — вариант модели с условиями. yume_keys — как модель записана в Yume (catalog.model_key).
    # Скрытая карточка (is_active = FALSE) остаётся: на неё ссылаются прошлые направления.
    """
    CREATE TABLE IF NOT EXISTS rental_cards (
        id               SERIAL PRIMARY KEY,
        title            VARCHAR(120) NOT NULL,
        car_class        VARCHAR(120),
        price            INTEGER NOT NULL,
        year             VARCHAR(120),
        fuel             VARCHAR(120),
        color            VARCHAR(120),
        age              VARCHAR(120),
        insurance_class  VARCHAR(120),
        insurance        VARCHAR(120),
        first_day        VARCHAR(120),
        office           VARCHAR(120),
        yume_keys        TEXT[] NOT NULL DEFAULT '{}',
        free_count       INTEGER NOT NULL DEFAULT 0,
        free_by          INTEGER,
        free_by_name     VARCHAR(200),
        free_at          TIMESTAMP,
        position         INTEGER NOT NULL DEFAULT 0,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_by       INTEGER,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS rental_card_log (
        id               SERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL,
        field            VARCHAR(40) NOT NULL,
        old_value        TEXT,
        new_value        TEXT,
        changed_by       INTEGER,
        changed_by_name  VARCHAR(200),
        changed_at       TIMESTAMP NOT NULL
    )
    """,

    # Последний снимок парка Yume — одна строка. Машины без клиентов и госномеров: ключи модели,
    # статус и подпись — ровно то, что нужно для счёта свободных по карточкам.
    """
    CREATE TABLE IF NOT EXISTS rental_fleet_sync (
        id            SMALLINT PRIMARY KEY DEFAULT 1,
        cars          JSONB NOT NULL DEFAULT '[]'::jsonb,
        synced_at     TIMESTAMP,
        attempted_at  TIMESTAMP,
        error         TEXT,
        CONSTRAINT rental_fleet_sync_single CHECK (id = 1)
    )
    """,

    # Направление: снимок карточки, офиса и оператора на момент записи — реестр остаётся читаемым,
    # даже если карточку потом поправят или скроют. status: catalog.STATUSES.
    """
    CREATE TABLE IF NOT EXISTS rental_directions (
        id               SERIAL PRIMARY KEY,
        card_id          INTEGER,
        card_title       VARCHAR(120) NOT NULL,
        card_class       VARCHAR(120),
        card_terms       VARCHAR(240),
        office           VARCHAR(120),
        phone            VARCHAR(16) NOT NULL,
        park             VARCHAR(255) NOT NULL,
        visit_at         TIMESTAMP NOT NULL,
        comment          TEXT,
        status           VARCHAR(20) NOT NULL DEFAULT 'waiting',
        created_by       INTEGER NOT NULL,
        created_by_name  VARCHAR(200),
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL,
        status_by        INTEGER,
        status_at        TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rental_directions_visit ON rental_directions (visit_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rental_directions_phone ON rental_directions (phone)",
    "CREATE INDEX IF NOT EXISTS idx_rental_directions_creator ON rental_directions (created_by, visit_at DESC)",

    """
    CREATE TABLE IF NOT EXISTS rental_direction_log (
        id               SERIAL PRIMARY KEY,
        direction_id     INTEGER NOT NULL,
        field            VARCHAR(20) NOT NULL,
        old_value        TEXT,
        new_value        TEXT,
        changed_by       INTEGER,
        changed_by_name  VARCHAR(200),
        changed_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rental_direction_log ON rental_direction_log (direction_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_rental_card_log ON rental_card_log (card_id, id)",

    # «Условия работы»: каждая правка — новая версия целиком; текущая — последняя (ТЗ 10.3).
    """
    CREATE TABLE IF NOT EXISTS rental_conditions (
        id               SERIAL PRIMARY KEY,
        blocks           JSONB NOT NULL,
        created_by       INTEGER,
        created_by_name  VARCHAR(200),
        created_at       TIMESTAMP NOT NULL
    )
    """,

    # Фотографии машины (владелец 28.10.2026): файл в бакете, строка здесь; первая по sort_order —
    # обложка. Пути в бакете наружу не отдаются — фронт получает подписанный адрес (rental/photos.py).
    """
    CREATE TABLE IF NOT EXISTS rental_card_photos (
        id               SERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL REFERENCES rental_cards(id) ON DELETE CASCADE,
        bucket           VARCHAR(255) NOT NULL,
        blob_path        TEXT NOT NULL,
        thumb_blob_path  TEXT,
        content_type     VARCHAR(100) NOT NULL DEFAULT 'image/webp',
        original_name    VARCHAR(255),
        file_size        BIGINT NOT NULL DEFAULT 0,
        width            INTEGER,
        height           INTEGER,
        thumb_width      INTEGER,
        thumb_height     INTEGER,
        sort_order       INTEGER NOT NULL DEFAULT 0,
        created_by       INTEGER,
        created_by_name  VARCHAR(200),
        created_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rental_card_photos_card ON rental_card_photos (card_id, sort_order, id)",

    # Имя модели в «Классификаторе авто», когда по названию карточки она не находится сама
    # (rental/tariffs.py). Колонка появилась после выкладки карточек — отсюда ALTER.
    "ALTER TABLE rental_cards ADD COLUMN IF NOT EXISTS classifier_key VARCHAR(120)",

    # История не правится и не удаляется (ТЗ 10.3: «Удаление пользователем истории недоступно»).
    """
    CREATE OR REPLACE FUNCTION rental_history_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'история раздела «Аренда авто» только пополняется';
    END
    $$ LANGUAGE plpgsql
    """,
]

_APPEND_ONLY_TABLES = ('rental_conditions', 'rental_direction_log', 'rental_card_log', 'rental_night_state_log')


def init_rental_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)
    for table in _APPEND_ONLY_TABLES:
        cursor.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_%(table)s_append_only'
                                                          AND tgrelid = '%(table)s'::regclass) THEN
                    CREATE TRIGGER trg_%(table)s_append_only BEFORE UPDATE OR DELETE ON %(table)s
                        FOR EACH ROW EXECUTE FUNCTION rental_history_append_only();
                END IF;
            END $$
        """ % {'table': table})
    _seed(cursor)


def _seed(cursor):
    """Карточки и «Условия работы» из макета — один раз, в пустые таблицы."""
    from . import catalog, queries

    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('rental_seed'))")
    now = queries.now_almaty()
    cursor.execute("SELECT EXISTS (SELECT 1 FROM rental_cards)")
    if not cursor.fetchone()[0]:
        for card in catalog.seed_cards():
            cursor.execute("""
                INSERT INTO rental_cards (title, car_class, price, year, fuel, color, age, insurance_class,
                                          insurance, first_day, office, yume_keys, position, created_at, updated_at)
                VALUES (%(title)s, %(car_class)s, %(price)s, %(year)s, %(fuel)s, %(color)s, %(age)s,
                        %(insurance_class)s, %(insurance)s, %(first_day)s, %(office)s, %(yume_keys)s,
                        %(position)s, %(now)s, %(now)s)
            """, dict(card, now=now))
    cursor.execute("SELECT EXISTS (SELECT 1 FROM rental_conditions)")
    if not cursor.fetchone()[0]:
        import json
        cursor.execute("""
            INSERT INTO rental_conditions (blocks, created_by, created_by_name, created_at)
            VALUES (%s::jsonb, NULL, 'Макет ТЗ #391', %s)
        """, (json.dumps(catalog.SEED_BLOCKS, ensure_ascii=False), now))
