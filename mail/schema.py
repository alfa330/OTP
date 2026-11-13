"""Схема раздела «Почта». Идемпотентна: ensure() вызывается при подключении Blueprint.

Таблицы:
    mail_participants          «Участники»: сотрудник → роль (ТЗ п. 11)
    mail_participant_offices   его офисы (справочник офисов вики, как у «Посылок»)
    mail_shipments             отправление: маршрут, адресат, плательщик, курьер, срок
    mail_items                 строки содержимого и их сверка
    mail_events                история: кто, когда, что, чем подтверждено — только пополняется
    mail_files                 накладные, подтверждения, фото расхождений (в бакете)
    mail_counters              счётчик номеров MAIL-YYYY-NNNNN по годам
    mail_zones, mail_routes    сроки договора: зона → рабочие дни, пара городов → зона
    mail_settings              прочие настройки раздела

Отправления и события физически не удаляются (ТЗ п. 14): удаляющих ручек у раздела нет,
у отправления есть только «Отменено».

Офис и адресат снимаются в карточку копией (`*_office_name`, `*_city`, адрес) рядом с id:
справочник живой, а карточка — документ о том, откуда и куда ушло В ТОТ день.
"""

import logging

from . import rules

logger = logging.getLogger(__name__)

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


def _in(values):
    return ', '.join("'%s'" % value for value in values)


STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS mail_participants (
        user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        role        VARCHAR(16) NOT NULL CHECK (role IN (%(roles)s)),
        created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mail_participant_offices (
        user_id    INTEGER NOT NULL REFERENCES mail_participants(user_id) ON DELETE CASCADE,
        office_id  INTEGER NOT NULL REFERENCES wiki_offices(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, office_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mail_participant_offices_office ON mail_participant_offices(office_id)",
    """
    CREATE TABLE IF NOT EXISTS mail_counters (
        year        SMALLINT PRIMARY KEY,
        last_value  INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mail_shipments (
        id                    SERIAL PRIMARY KEY,
        number                VARCHAR(20) NOT NULL UNIQUE,
        status                VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN (%(statuses)s)),
        status_changed_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        status_changed_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name       VARCHAR(200),
        created_at            TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at            TIMESTAMP NOT NULL DEFAULT %(now)s,
        sender_office_id      INTEGER REFERENCES wiki_offices(id) ON DELETE SET NULL,
        sender_city           VARCHAR(120),
        sender_office_name    VARCHAR(255),
        sender_office_address TEXT,
        sender_name           VARCHAR(200),
        sender_phone          VARCHAR(32),
        recipient_office_id   INTEGER REFERENCES wiki_offices(id) ON DELETE SET NULL,
        recipient_city        VARCHAR(120),
        recipient_office_name VARCHAR(255),
        recipient_name        VARCHAR(200),
        recipient_phone       VARCHAR(32),
        recipient_address     TEXT,
        recipient_postcode    VARCHAR(12),
        payer_entity_id       INTEGER REFERENCES payment_legal_entities(id) ON DELETE SET NULL,
        payer_name            VARCHAR(200),
        delivery_type         VARCHAR(12) NOT NULL DEFAULT 'standard' CHECK (delivery_type IN (%(delivery)s)),
        urgency               VARCHAR(12) NOT NULL DEFAULT 'normal' CHECK (urgency IN (%(urgency)s)),
        places                SMALLINT NOT NULL DEFAULT 1 CHECK (places BETWEEN 1 AND 999),
        weight_kg             NUMERIC(9, 3) CHECK (weight_kg > 0),
        volume_weight_kg      NUMERIC(9, 3) CHECK (volume_weight_kg > 0),
        declared_value        NUMERIC(14, 2) CHECK (declared_value >= 0),
        comment               TEXT,
        ready_at              TIMESTAMP,
        courier_requested_at  TIMESTAMP,
        planned_pickup_on     DATE,
        track_number          VARCHAR(64),
        handed_at             TIMESTAMP,
        zone_code             VARCHAR(8),
        zone_manual           BOOLEAN NOT NULL DEFAULT FALSE,
        sla_min_days          SMALLINT,
        sla_max_days          SMALLINT,
        earliest_on           DATE,
        due_on                DATE,
        delivered_on          DATE,
        reconciled_at         TIMESTAMP,
        reconciled_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reconciled_by_name    VARCHAR(200),
        closed_at             TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mail_shipments_status ON mail_shipments(status)",
    "CREATE INDEX IF NOT EXISTS idx_mail_shipments_sender ON mail_shipments(sender_office_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_mail_shipments_recipient ON mail_shipments(recipient_office_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_mail_shipments_created ON mail_shipments(created_at DESC, id DESC)",
    # Дубль трека у одного курьера — предупреждение (ТЗ п. 14), поиск по треку — ТЗ п. 9.
    "CREATE INDEX IF NOT EXISTS idx_mail_shipments_track ON mail_shipments(lower(track_number)) "
    "WHERE track_number IS NOT NULL",
    # Просрочка считается только у того, что едет.
    "CREATE INDEX IF NOT EXISTS idx_mail_shipments_due ON mail_shipments(due_on) "
    "WHERE status IN (%(on_the_way)s)",
    """
    CREATE TABLE IF NOT EXISTS mail_items (
        id                 SERIAL PRIMARY KEY,
        shipment_id        INTEGER NOT NULL REFERENCES mail_shipments(id) ON DELETE CASCADE,
        position           SMALLINT NOT NULL,
        doc_type           VARCHAR(24) NOT NULL CHECK (doc_type IN (%(doc_types)s)),
        other_description  VARCHAR(500),
        period_from        DATE,
        period_to          DATE,
        qty                INTEGER NOT NULL CHECK (qty BETWEEN 1 AND 99999),
        copy_kind          VARCHAR(10) NOT NULL DEFAULT 'original' CHECK (copy_kind IN (%(copy_kinds)s)),
        doc_number         VARCHAR(64),
        is_confidential    BOOLEAN NOT NULL DEFAULT FALSE,
        received_qty       INTEGER CHECK (received_qty BETWEEN 0 AND 99999),
        line_status        VARCHAR(12) CHECK (line_status IN (%(line_statuses)s)),
        line_note          TEXT,
        CONSTRAINT mail_items_other_described
            CHECK (doc_type <> 'other' OR length(btrim(coalesce(other_description, ''))) > 0),
        CONSTRAINT mail_items_period_order
            CHECK (period_from IS NULL OR period_to IS NULL OR period_from <= period_to)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mail_items_shipment ON mail_items(shipment_id, position)",
    """
    CREATE TABLE IF NOT EXISTS mail_events (
        id           BIGSERIAL PRIMARY KEY,
        shipment_id  INTEGER NOT NULL REFERENCES mail_shipments(id) ON DELETE CASCADE,
        kind         VARCHAR(20) NOT NULL,
        from_status  VARCHAR(20),
        to_status    VARCHAR(20),
        actor_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name   VARCHAR(200),
        source       VARCHAR(16) NOT NULL DEFAULT 'manual',
        note         TEXT,
        payload      JSONB,
        at           TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mail_events_shipment ON mail_events(shipment_id, at, id)",
    """
    CREATE TABLE IF NOT EXISTS mail_files (
        id            SERIAL PRIMARY KEY,
        shipment_id   INTEGER NOT NULL REFERENCES mail_shipments(id) ON DELETE CASCADE,
        event_id      BIGINT REFERENCES mail_events(id) ON DELETE SET NULL,
        kind          VARCHAR(16) NOT NULL CHECK (kind IN (%(file_kinds)s)),
        filename      VARCHAR(255) NOT NULL,
        content_type  VARCHAR(160),
        size_bytes    INTEGER NOT NULL,
        bucket        VARCHAR(255) NOT NULL,
        blob_path     TEXT NOT NULL,
        uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_by_name VARCHAR(200),
        uploaded_at   TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mail_files_shipment ON mail_files(shipment_id, id)",
    """
    CREATE TABLE IF NOT EXISTS mail_zones (
        code          VARCHAR(8) PRIMARY KEY,
        label         VARCHAR(60) NOT NULL,
        min_days      SMALLINT CHECK (min_days BETWEEN 0 AND 60),
        max_days      SMALLINT NOT NULL CHECK (max_days BETWEEN 0 AND 60),
        eco_min_days  SMALLINT CHECK (eco_min_days BETWEEN 0 AND 60),
        eco_max_days  SMALLINT CHECK (eco_max_days BETWEEN 0 AND 60),
        position      SMALLINT NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mail_routes (
        from_city   VARCHAR(120) NOT NULL,
        to_city     VARCHAR(120) NOT NULL,
        zone_code   VARCHAR(8) NOT NULL REFERENCES mail_zones(code) ON UPDATE CASCADE,
        updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_mail_routes_pair "
    "ON mail_routes (lower(btrim(from_city)), lower(btrim(to_city)))",
    """
    CREATE TABLE IF NOT EXISTS mail_settings (
        key         VARCHAR(40) PRIMARY KEY,
        value       JSONB NOT NULL,
        updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
)

# Администратор раздела по решению владельца 13.11.2026 («суперадмины и Дина»):
# постановщик ТЗ #417. Заводится ОДИН раз — метка в mail_settings, — чтобы удалённая
# суперадмином строка не возвращалась при каждом перезапуске.
SEED_ADMIN_USER_IDS = (532,)
_SEED_MARK = 'seeded_admins_v1'


def _params():
    return {
        'now': _NOW,
        'roles': _in(rules.ROLES),
        'statuses': _in(rules.STATUSES),
        'delivery': _in(rules.DELIVERY_TYPE_LABELS),
        'urgency': _in(rules.URGENCY_LABELS),
        'doc_types': _in(rules.DOC_TYPE_LABELS),
        'copy_kinds': _in(rules.COPY_KIND_LABELS),
        'line_statuses': _in(rules.LINE_STATUS_LABELS),
        'file_kinds': _in(rules.FILE_KIND_LABELS),
        'on_the_way': _in(rules.SLA_RUNNING),
    }


def statements():
    params = _params()
    return [statement % params for statement in STATEMENTS]


def seed_zones(cursor):
    """Зоны договора из ТЗ п. 7 — только если справочник пуст: правки администратора
    не перетираются."""
    cursor.execute("SELECT COUNT(*) FROM mail_zones")
    if int(cursor.fetchone()[0] or 0):
        return 0
    for zone in rules.DEFAULT_ZONES:
        cursor.execute(
            "INSERT INTO mail_zones (code, label, min_days, max_days, position) VALUES (%s, %s, %s, %s, %s)",
            (zone['code'], zone['label'], zone['min_days'], zone['max_days'], zone['position']))
    return len(rules.DEFAULT_ZONES)


def seed_admins(cursor):
    cursor.execute("SELECT 1 FROM mail_settings WHERE key = %s", (_SEED_MARK,))
    if cursor.fetchone():
        return []
    added = []
    for user_id in SEED_ADMIN_USER_IDS:
        cursor.execute(
            """
            INSERT INTO mail_participants (user_id, role)
            SELECT u.id, 'admin' FROM users u
             WHERE u.id = %s AND COALESCE(u.status, '') <> 'fired'
            ON CONFLICT (user_id) DO NOTHING
            RETURNING user_id
            """, (user_id,))
        if cursor.fetchone():
            added.append(user_id)
    cursor.execute("INSERT INTO mail_settings (key, value) VALUES (%s, 'true'::jsonb) ON CONFLICT DO NOTHING",
                   (_SEED_MARK,))
    return added


def ensure_in_cursor(cursor):
    for statement in statements():
        cursor.execute(statement)
    zones = seed_zones(cursor)
    admins = seed_admins(cursor)
    return zones, admins


def ensure(db):
    with db._get_cursor() as cursor:
        zones, admins = ensure_in_cursor(cursor)
    if zones:
        logger.info('Почта: справочник зон заполнен значениями ТЗ п. 7')
    if admins:
        logger.info('Почта: администратор раздела заведён: %s', admins)
    logger.info('Почта: таблицы на месте')
