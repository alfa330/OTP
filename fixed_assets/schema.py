"""Схема раздела «Основные средства». Идемпотентна: ensure() вызывается при подключении Blueprint.

Таблицы:
    fa_categories      категории ОС и кто проверяет ОС категории перед списанием
    fa_members         «Участники»: поимённые добавки к ролям отделов
    fa_assets          карточка ОС — только ТЕКУЩЕЕ состояние (МОЛ, отдел, место, статус)
    fa_requests        запрос: вид, статус, стороны, проверка, блок акта
    fa_request_items   ОС запроса со снимком «что было» на день отправки
    fa_files           фото ОС и сканы актов (в бакете)
    fa_events          история: кто, когда, что сделал, какие файлы приложил
    fa_settings        настройки раздела (реквизиты организации и комиссия для актов)

Решения, которые видны в DDL:

* **«Нет обхода».** МОЛ, отдел, место и статус в `fa_assets` пишет только закрытие запроса
  (queries.close_request); `open_request_id` — запрос, который сейчас держит ОС: второй
  запрос на то же ОС не отправить.

* **История не правится и не удаляется** — правило ТЗ держит триггер базы: UPDATE, DELETE
  и TRUNCATE по `fa_events` падают с ошибкой. Поэтому в ней нет внешних ключей: каскад от
  удалённого сотрудника был бы правкой строки. Имя — снимком рядом с id.

* **Снимки имён рядом с id** — и в запросе, и в карточке: акт — документ о том, кто кому
  передал В ТОТ день, а сотрудника потом могут переименовать или уволить.

* **Инвентарный номер уникален без учёта регистра** — двух одинаковых не бывает. Штрихкод тоже:
  по нему ОС находят сканом, и один код не может вести к двум карточкам.

* **Реквизиты организации — в базе, не в коде.** Название, БИН, руководитель и комиссия нужны
  на бумаге (форма ДА-3), но репозиторий открытый: их вписывает бухгалтерия в настройках.
"""

import logging

from . import rules

logger = logging.getLogger(__name__)

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


def _in(values):
    return ', '.join("'%s'" % value for value in values)


_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS fa_categories (
        id          SERIAL PRIMARY KEY,
        name        VARCHAR(120) NOT NULL,
        check_by    VARCHAR(16) NOT NULL CHECK (check_by IN ({check_groups})),
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        position    INTEGER NOT NULL DEFAULT 0,
        created_at  TIMESTAMP NOT NULL DEFAULT {now}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_members (
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role        VARCHAR(16) NOT NULL CHECK (role IN ({member_roles})),
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at  TIMESTAMP NOT NULL DEFAULT {now},
        PRIMARY KEY (user_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_assets (
        id                 SERIAL PRIMARY KEY,
        inventory_number   VARCHAR(64) NOT NULL,
        name               VARCHAR(300) NOT NULL,
        category_id        INTEGER NOT NULL REFERENCES fa_categories(id),
        serial_number      VARCHAR(120),
        -- Штрихкод с наклейки (EAN-13 бухгалтерии, Code 128, QR): по нему ОС находят сканом.
        barcode            VARCHAR(64),
        cost               NUMERIC(14, 2) CHECK (cost IS NULL OR cost >= 0),
        acquired_on        DATE NOT NULL,
        note               TEXT,
        status             VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ({asset_statuses})),
        mol_user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        mol_name           VARCHAR(255),
        department_id      INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        department_name    VARCHAR(255),
        location           VARCHAR(300),
        on_stock           BOOLEAN NOT NULL DEFAULT FALSE,
        -- Запрос, который сейчас держит ОС (перемещение, списание, передача при увольнении).
        open_request_id    INTEGER,
        -- Запрос приёма в учёт: пока он открыт, бухгалтерия может исправить описание.
        intake_request_id  INTEGER,
        written_off_at     TIMESTAMP,
        created_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name    VARCHAR(255),
        created_at         TIMESTAMP NOT NULL DEFAULT {now},
        updated_at         TIMESTAMP NOT NULL DEFAULT {now}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_requests (
        id                         SERIAL PRIMARY KEY,
        kind                       VARCHAR(16) NOT NULL CHECK (kind IN ({kinds})),
        status                     VARCHAR(16) NOT NULL CHECK (status IN ({statuses})),
        review_by                  VARCHAR(16) CHECK (review_by IS NULL OR review_by IN ({reviewers})),
        review_department_id       INTEGER,
        created_by                 INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name            VARCHAR(255),
        from_user_id               INTEGER REFERENCES users(id) ON DELETE SET NULL,
        from_name                  VARCHAR(255),
        from_department_id         INTEGER,
        from_department_name       VARCHAR(255),
        to_user_id                 INTEGER REFERENCES users(id) ON DELETE SET NULL,
        to_name                    VARCHAR(255),
        to_department_id           INTEGER,
        to_department_name         VARCHAR(255),
        to_location                VARCHAR(300),
        to_stock                   BOOLEAN NOT NULL DEFAULT FALSE,
        comment                    TEXT,
        need_title                 VARCHAR(300),
        need_category_id           INTEGER,
        need_for_user_id           INTEGER REFERENCES users(id) ON DELETE SET NULL,
        need_for_name              VARCHAR(255),
        -- «Новое ОС», по которому сделан этот приём в учёт.
        source_request_id          INTEGER,
        checked_by                 INTEGER,
        checked_by_name            VARCHAR(255),
        checked_at                 TIMESTAMP,
        check_comment              TEXT,
        -- Блок акта (ТЗ, раздел 5): скан, дата подписания, кто и когда передал оригинал,
        -- «Оригинал получен», бухгалтер, принявший оригинал.
        act_date                   DATE,
        signed_on                  DATE,
        scan_file_id               INTEGER,
        scan_by                    INTEGER,
        scan_by_name               VARCHAR(255),
        scan_at                    TIMESTAMP,
        original_handed_by         INTEGER,
        original_handed_by_name    VARCHAR(255),
        original_handed_on         DATE,
        original_received_by       INTEGER,
        original_received_by_name  VARCHAR(255),
        original_received_at       TIMESTAMP,
        rejected_by                INTEGER,
        rejected_by_name           VARCHAR(255),
        rejected_at                TIMESTAMP,
        reject_reason              TEXT,
        rejected_from              VARCHAR(16),
        submitted_at               TIMESTAMP,
        closed_at                  TIMESTAMP,
        created_at                 TIMESTAMP NOT NULL DEFAULT {now},
        updated_at                 TIMESTAMP NOT NULL DEFAULT {now}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_request_items (
        id                      SERIAL PRIMARY KEY,
        request_id              INTEGER NOT NULL REFERENCES fa_requests(id) ON DELETE CASCADE,
        asset_id                INTEGER NOT NULL REFERENCES fa_assets(id),
        position                INTEGER NOT NULL DEFAULT 0,
        name                    VARCHAR(300),
        inventory_number        VARCHAR(64),
        serial_number           VARCHAR(120),
        category_name           VARCHAR(120),
        before_mol_user_id      INTEGER,
        before_mol_name         VARCHAR(255),
        before_department_id    INTEGER,
        before_department_name  VARCHAR(255),
        before_location         VARCHAR(300),
        before_on_stock         BOOLEAN,
        UNIQUE (request_id, asset_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_files (
        id                SERIAL PRIMARY KEY,
        request_id        INTEGER NOT NULL REFERENCES fa_requests(id) ON DELETE CASCADE,
        asset_id          INTEGER REFERENCES fa_assets(id),
        kind              VARCHAR(16) NOT NULL CHECK (kind IN ({file_kinds})),
        bucket            VARCHAR(255) NOT NULL,
        blob_path         TEXT NOT NULL,
        thumb_blob_path   TEXT,
        file_name         VARCHAR(255),
        content_type      VARCHAR(128),
        file_size         INTEGER,
        width             INTEGER,
        height            INTEGER,
        thumb_width       INTEGER,
        thumb_height      INTEGER,
        uploaded_by       INTEGER,
        uploaded_by_name  VARCHAR(255),
        created_at        TIMESTAMP NOT NULL DEFAULT {now}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_events (
        id             BIGSERIAL PRIMARY KEY,
        asset_id       INTEGER,
        request_id     INTEGER,
        kind           VARCHAR(32) NOT NULL,
        actor_user_id  INTEGER,
        actor_name     VARCHAR(255),
        payload        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at     TIMESTAMP NOT NULL DEFAULT {now}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fa_settings (
        key              VARCHAR(40) PRIMARY KEY,
        value            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        updated_by       INTEGER,
        updated_by_name  VARCHAR(255),
        updated_at       TIMESTAMP NOT NULL DEFAULT {now}
    )
    """,
)

# Колонки, появившиеся после первых установок: CREATE TABLE IF NOT EXISTS их в старую таблицу не добавит.
_COLUMNS = (
    "ALTER TABLE fa_assets ADD COLUMN IF NOT EXISTS barcode VARCHAR(64)",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS fa_categories_name_uq ON fa_categories (lower(name))",
    "CREATE UNIQUE INDEX IF NOT EXISTS fa_assets_inventory_uq ON fa_assets (lower(inventory_number))",
    # Один штрихкод — одна карточка: иначе скан открыл бы не то ОС.
    "CREATE UNIQUE INDEX IF NOT EXISTS fa_assets_barcode_uq ON fa_assets (lower(barcode)) WHERE barcode IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS fa_assets_mol_idx ON fa_assets (mol_user_id) WHERE status = 'active'",
    "CREATE INDEX IF NOT EXISTS fa_assets_department_idx ON fa_assets (department_id)",
    "CREATE INDEX IF NOT EXISTS fa_assets_status_idx ON fa_assets (status, id DESC)",
    "CREATE INDEX IF NOT EXISTS fa_requests_status_idx ON fa_requests (status, id DESC)",
    "CREATE INDEX IF NOT EXISTS fa_requests_author_idx ON fa_requests (created_by)",
    "CREATE INDEX IF NOT EXISTS fa_requests_from_idx ON fa_requests (from_user_id)",
    "CREATE INDEX IF NOT EXISTS fa_requests_to_idx ON fa_requests (to_user_id)",
    "CREATE INDEX IF NOT EXISTS fa_request_items_asset_idx ON fa_request_items (asset_id)",
    "CREATE INDEX IF NOT EXISTS fa_files_request_idx ON fa_files (request_id)",
    "CREATE INDEX IF NOT EXISTS fa_files_asset_idx ON fa_files (asset_id)",
    "CREATE INDEX IF NOT EXISTS fa_events_asset_idx ON fa_events (asset_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS fa_events_request_idx ON fa_events (request_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS fa_events_time_idx ON fa_events (created_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS fa_events_actor_idx ON fa_events (actor_user_id, id DESC)",
)

# История только пополняется (ТЗ: «Записи нельзя редактировать или удалять»). Триггер —
# последний рубеж: даже ошибка в коде или запрос в консоли базы строку не изменит.
_IMMUTABLE_EVENTS = (
    """
    CREATE OR REPLACE FUNCTION fa_events_immutable() RETURNS trigger AS $fa$
    BEGIN
        RAISE EXCEPTION 'История основных средств не редактируется и не удаляется';
    END;
    $fa$ LANGUAGE plpgsql
    """,
    """
    DO $fa$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'fa_events_no_change') THEN
            CREATE TRIGGER fa_events_no_change BEFORE UPDATE OR DELETE ON fa_events
                FOR EACH ROW EXECUTE FUNCTION fa_events_immutable();
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'fa_events_no_truncate') THEN
            CREATE TRIGGER fa_events_no_truncate BEFORE TRUNCATE ON fa_events
                FOR EACH STATEMENT EXECUTE FUNCTION fa_events_immutable();
        END IF;
    END
    $fa$
    """,
)


def statements():
    params = {
        'now': _NOW,
        'check_groups': _in(rules.CHECK_GROUPS),
        'member_roles': _in(rules.MEMBER_ROLES),
        'asset_statuses': _in(rules.ASSET_STATUSES),
        'kinds': _in(rules.KINDS),
        'statuses': _in(rules.STATUSES),
        'reviewers': _in(rules.REVIEWERS),
        'file_kinds': _in(rules.FILE_KINDS),
    }
    return ([statement.format(**params) for statement in _TABLES] + list(_COLUMNS) + list(_INDEXES)
            + list(_IMMUTABLE_EVENTS))


def seed_categories(cursor):
    """Категории из ТЗ — только если справочник пуст: правки бухгалтерии не перетираются."""
    cursor.execute("SELECT COUNT(*) FROM fa_categories")
    if int(cursor.fetchone()[0] or 0):
        return 0
    for position, (name, check_by) in enumerate(rules.DEFAULT_CATEGORIES, start=1):
        cursor.execute("INSERT INTO fa_categories (name, check_by, position) VALUES (%s, %s, %s)",
                       (name, check_by, position))
    return len(rules.DEFAULT_CATEGORIES)


def ensure_in_cursor(cursor):
    # Два экземпляра сервера, стартующие разом, иначе столкнулись бы на CREATE TABLE и задвоили бы
    # справочник категорий: схема и засев — по очереди, замок снимается с концом транзакции.
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('fixed_assets_schema'))")
    for statement in statements():
        cursor.execute(statement)
    return seed_categories(cursor)


def ensure(db):
    with db._get_cursor() as cursor:
        seeded = ensure_in_cursor(cursor)
    if seeded:
        logger.info('Основные средства: справочник категорий заполнен значениями ТЗ')
    logger.info('Основные средства: таблицы на месте')
