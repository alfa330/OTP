# -*- coding: utf-8 -*-
"""Схема раздела «Дисциплина». Все таблицы с префиксом discipline_.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Вызывается один раз при
старте из Database._init_db через init_discipline_schema(cursor) — под своим
SAVEPOINT, как у «Жалоб»: сломанная миграция раздела не роняет базу.

Модель:

    discipline_settings       настройки отдела: окно, лестница, сроки, каналы
    discipline_counting_rules история окна и лестницы: запись — по правилам своего дня
    discipline_types          типы нарушений отдела (ТЗ п. 5.1)
    discipline_measures       меры воздействия отдела (ТЗ п. 5.2)
    discipline_records        запись о нарушении
    discipline_acts           акт и его записи (discipline_act_records)
    discipline_controls       периоды особого контроля
    discipline_files          все файлы раздела, без удаления, с версиями
    discipline_notifications  колокол: одно событие — одному человеку
    discipline_audit          журнал действий, только дописывается
    discipline_counters       сквозные номера Д-ГГГГ-NNNN и А-ГГГГ-NNNN

Решения, которые видны в DDL:

* **Номер нарушения в периоде и шаг лестницы не хранятся** — их считает
  catalog.compute_steps (ТЗ п. 7.2: отмена пересчитывает номера соседей).
  Хранятся только факты: когда, что, кто, объяснительная получена, тренинг
  проведён, мера утверждена, запись закрыта или отменена.
* **Снимки в записи** (название типа, вес, группа, линия, смена): справочник
  правят, группу переименовывают, а запись должна читаться так, как её завели.
* **Время — TIMESTAMP по Алматы**, как во всём портале. ТЗ п. 16 просит
  «время с указанием зоны»: зона у портала одна (UTC+5), и TIMESTAMPTZ здесь
  принёс бы только известную ловушку со срезом строки в UTC.
* **Журнал действий без внешних ключей и под триггером-запретом**: «записи
  журнала не редактируются и не удаляются» (п. 14). Ссылка на users с ON DELETE
  SET NULL была бы UPDATE, и триггер не дал бы удалить пользователя, поэтому
  в журнале — id и имя снимком.
* **Файлы не удаляются** (п. 13): ошибочный помечается недействительным с
  причиной, повторная загрузка того же вида оставляет прежнюю версию.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [
    # ── Настройки отдела ───────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_settings (
        department_id     INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        settings          JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,

    # История правил счёта (окно и лестница): с какого момента какие действуют.
    # Запись считается по правилам дня, когда её зафиксировали, — смена лестницы
    # не переписывает прошлые ступени. Первая строка (с 2000-01-01) — правила,
    # что действовали до первой правки.
    """
    CREATE TABLE IF NOT EXISTS discipline_counting_rules (
        id                SERIAL PRIMARY KEY,
        department_id     INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        rules             JSONB NOT NULL,
        valid_from        TIMESTAMP NOT NULL,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_counting_rules_department
        ON discipline_counting_rules (department_id, valid_from)
    """,

    # ── Справочник типов нарушений ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_types (
        id                SERIAL PRIMARY KEY,
        department_id     INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        title             VARCHAR(200) NOT NULL,
        severity          VARCHAR(16) NOT NULL DEFAULT 'medium',
        needs_explanation BOOLEAN NOT NULL DEFAULT TRUE,
        needs_evidence    BOOLEAN NOT NULL DEFAULT FALSE,
        weight            SMALLINT NOT NULL DEFAULT 1,
        is_other          BOOLEAN NOT NULL DEFAULT FALSE,
        is_archived       BOOLEAN NOT NULL DEFAULT FALSE,
        position          INTEGER NOT NULL DEFAULT 0,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT discipline_types_weight CHECK (weight BETWEEN 1 AND 5)
    )
    """,
    # Одно название на отдел среди живых: архивное не мешает завести заново.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_discipline_types_title
        ON discipline_types (department_id, lower(title)) WHERE NOT is_archived
    """,

    # ── Справочник мер ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_measures (
        id                SERIAL PRIMARY KEY,
        department_id     INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        title             VARCHAR(200) NOT NULL,
        needs_approval    BOOLEAN NOT NULL DEFAULT TRUE,
        finance           VARCHAR(16) NOT NULL DEFAULT 'none',
        value_kind        VARCHAR(16) NOT NULL DEFAULT 'none',
        is_archived       BOOLEAN NOT NULL DEFAULT FALSE,
        position          INTEGER NOT NULL DEFAULT 0,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_discipline_measures_title
        ON discipline_measures (department_id, lower(title)) WHERE NOT is_archived
    """,

    # ── Сквозные номера ────────────────────────────────────────────────────
    # Строка на (вид, год), а не SEQUENCE на год: последовательность пришлось
    # бы заводить каждое 1 января, а UPDATE ... RETURNING выдаёт номер без
    # дыр и без гонок — строку держит блокировка до конца транзакции.
    """
    CREATE TABLE IF NOT EXISTS discipline_counters (
        kind              VARCHAR(8) NOT NULL,
        year              INTEGER NOT NULL,
        last_no           INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (kind, year)
    )
    """,

    # ── Особый контроль ────────────────────────────────────────────────────
    # Раньше записей: запись ссылается на контроль, под которым её завели.
    """
    CREATE TABLE IF NOT EXISTS discipline_controls (
        id                SERIAL PRIMARY KEY,
        operator_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id     INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        source            VARCHAR(16) NOT NULL,
        reason            TEXT NOT NULL,
        act_id            INTEGER,
        start_date        DATE NOT NULL,
        end_date          DATE NOT NULL,
        responsible_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        decision          VARCHAR(16),
        decision_comment  TEXT,
        decision_measure_id    INTEGER REFERENCES discipline_measures(id) ON DELETE SET NULL,
        decision_measure_title VARCHAR(200),
        decision_finance       VARCHAR(16),
        decision_value_kind    VARCHAR(16),
        decision_amount        NUMERIC(12, 2),
        decision_percent       NUMERIC(5, 2),
        decided_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        decided_at        TIMESTAMP,
        warned_for        DATE,
        due_notified_for  DATE,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at        TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT discipline_controls_dates CHECK (end_date >= start_date)
    )
    """,
    # Один ДЕЙСТВУЮЩИЙ контроль на человека: второй источник (испытательный
    # срок поверх контроля после акта) не плодит вторую строку в красном блоке,
    # а продлевает или переписывает основание существующей.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_discipline_controls_active
        ON discipline_controls (operator_id) WHERE decided_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_controls_department
        ON discipline_controls (department_id, decided_at, end_date)
    """,

    # ── Запись о нарушении ─────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_records (
        id                      SERIAL PRIMARY KEY,
        number                  VARCHAR(20) NOT NULL UNIQUE,
        operator_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id           INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        group_id                INTEGER REFERENCES groups(id) ON DELETE SET NULL,
        group_name              VARCHAR(255),
        direction_name          VARCHAR(255),
        shift_label             VARCHAR(64),
        occurred_at             TIMESTAMP NOT NULL,
        type_id                 INTEGER REFERENCES discipline_types(id) ON DELETE SET NULL,
        type_title              VARCHAR(200) NOT NULL,
        weight                  SMALLINT NOT NULL DEFAULT 1,
        needs_explanation       BOOLEAN NOT NULL DEFAULT TRUE,
        description             TEXT NOT NULL,
        evidence_note           TEXT,
        explanation_received_at TIMESTAMP,
        explanation_requested_at TIMESTAMP,
        refused_at              TIMESTAMP,
        refusal_witness_ids     INTEGER[],
        training_id             INTEGER REFERENCES trainings(id) ON DELETE SET NULL,
        measure_id              INTEGER REFERENCES discipline_measures(id) ON DELETE SET NULL,
        measure_title           VARCHAR(200),
        measure_finance         VARCHAR(16),
        measure_value_kind      VARCHAR(16),
        measure_amount          NUMERIC(12, 2),
        measure_percent         NUMERIC(5, 2),
        measure_state           VARCHAR(16),
        measure_proposed_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        measure_approved_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        measure_approved_at     TIMESTAMP,
        manager_comment         TEXT,
        control_id              INTEGER REFERENCES discipline_controls(id) ON DELETE SET NULL,
        closed_at               TIMESTAMP,
        closed_by               INTEGER REFERENCES users(id) ON DELETE SET NULL,
        cancelled_at            TIMESTAMP,
        cancelled_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
        cancel_reason           TEXT,
        client_token            VARCHAR(64),
        created_by              INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at              TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at              TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT discipline_records_weight CHECK (weight BETWEEN 1 AND 5),
        CONSTRAINT discipline_records_measure_state
            CHECK (measure_state IS NULL OR measure_state IN ('proposed', 'approved'))
    )
    """,
    # Счётчик человека: его неотменённые записи по времени — главный запрос
    # формы фиксации и карточки.
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_records_operator
        ON discipline_records (operator_id, occurred_at) WHERE cancelled_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_records_department
        ON discipline_records (department_id, occurred_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_records_created_by
        ON discipline_records (created_by, occurred_at DESC)
    """,
    # Метка отправки формы: повтор той же отправки (ответ потерялся в сети,
    # человек нажал ещё раз) находит уже сохранённую запись, а не фиксирует
    # нарушение второй раз — иначе лестница шагнула бы дважды.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_discipline_records_client_token
        ON discipline_records (created_by, client_token) WHERE client_token IS NOT NULL
    """,

    # ── Акт ────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_acts (
        id                  SERIAL PRIMARY KEY,
        number              VARCHAR(20) NOT NULL UNIQUE,
        operator_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id       INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        trigger_record_id   INTEGER NOT NULL REFERENCES discipline_records(id) ON DELETE CASCADE,
        composed_on         DATE,
        composed_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        approved_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        approved_at         TIMESTAMP,
        decision_measure_id INTEGER REFERENCES discipline_measures(id) ON DELETE SET NULL,
        decision_title      VARCHAR(200),
        decision_finance    VARCHAR(16),
        decision_value_kind VARCHAR(16),
        decision_amount     NUMERIC(12, 2),
        decision_percent    NUMERIC(5, 2),
        created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at          TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at          TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    # Акт на запись один: повторная сверка лестницы не плодит черновики.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_discipline_acts_trigger
        ON discipline_acts (trigger_record_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS discipline_act_records (
        act_id              INTEGER NOT NULL REFERENCES discipline_acts(id) ON DELETE CASCADE,
        record_id           INTEGER NOT NULL REFERENCES discipline_records(id) ON DELETE CASCADE,
        PRIMARY KEY (act_id, record_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_act_records_record
        ON discipline_act_records (record_id)
    """,

    # ── Файлы ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_files (
        id                  SERIAL PRIMARY KEY,
        record_id           INTEGER REFERENCES discipline_records(id) ON DELETE CASCADE,
        act_id              INTEGER REFERENCES discipline_acts(id) ON DELETE CASCADE,
        department_id       INTEGER REFERENCES departments(id) ON DELETE CASCADE,
        kind                VARCHAR(20) NOT NULL,
        file_name           VARCHAR(255) NOT NULL,
        content_type        VARCHAR(128),
        size_bytes          INTEGER NOT NULL DEFAULT 0,
        sha256              CHAR(64),
        bucket              VARCHAR(255),
        blob_path           TEXT NOT NULL,
        uploaded_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        uploaded_at         TIMESTAMP NOT NULL DEFAULT %(now)s,
        superseded_at       TIMESTAMP,
        invalid_at          TIMESTAMP,
        invalid_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
        invalid_reason      TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_files_record
        ON discipline_files (record_id, kind, uploaded_at DESC) WHERE record_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_files_act
        ON discipline_files (act_id, uploaded_at DESC) WHERE act_id IS NOT NULL
    """,

    # ── Колокол ────────────────────────────────────────────────────────────
    # Как payment_notifications: строку пишет сам процесс в момент события,
    # источник колокола только читает. dedupe_key не даёт напоминанию прийти
    # дважды (срок объяснительной, конец контроля).
    """
    CREATE TABLE IF NOT EXISTS discipline_notifications (
        id                  BIGSERIAL PRIMARY KEY,
        user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        record_id           INTEGER REFERENCES discipline_records(id) ON DELETE CASCADE,
        control_id          INTEGER REFERENCES discipline_controls(id) ON DELETE CASCADE,
        kind                VARCHAR(32) NOT NULL,
        title               VARCHAR(300) NOT NULL,
        body                TEXT,
        tone                VARCHAR(16) NOT NULL DEFAULT 'default',
        dedupe_key          VARCHAR(120),
        created_at          TIMESTAMP NOT NULL DEFAULT %(now)s,
        read_at             TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_notifications_unread
        ON discipline_notifications (user_id, id DESC) WHERE read_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_discipline_notifications_dedupe
        ON discipline_notifications (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL
    """,

    # ── Журнал действий ────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS discipline_audit (
        id                  BIGSERIAL PRIMARY KEY,
        actor_id            INTEGER,
        actor_name          VARCHAR(255),
        action              VARCHAR(40) NOT NULL,
        entity              VARCHAR(20) NOT NULL,
        entity_id           INTEGER,
        operator_id         INTEGER,
        department_id       INTEGER,
        record_id           INTEGER,
        changes             JSONB,
        created_at          TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_audit_created
        ON discipline_audit (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_audit_operator
        ON discipline_audit (operator_id, created_at DESC) WHERE operator_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_discipline_audit_record
        ON discipline_audit (record_id, created_at DESC) WHERE record_id IS NOT NULL
    """,
]

_MIGRATIONS = [
    # Колонки, добавленные после первой версии таблиц: база, где раздел уже
    # поднимался, получает их здесь — до индексов, которые на них опираются.
    "ALTER TABLE discipline_records ADD COLUMN IF NOT EXISTS client_token VARCHAR(64)",
    "ALTER TABLE discipline_records ADD COLUMN IF NOT EXISTS explanation_requested_at TIMESTAMP",
    "ALTER TABLE discipline_acts ADD COLUMN IF NOT EXISTS decision_finance VARCHAR(16)",
    "ALTER TABLE discipline_acts ADD COLUMN IF NOT EXISTS decision_value_kind VARCHAR(16)",
    "ALTER TABLE discipline_acts ADD COLUMN IF NOT EXISTS decision_amount NUMERIC(12, 2)",
    "ALTER TABLE discipline_acts ADD COLUMN IF NOT EXISTS decision_percent NUMERIC(5, 2)",
    """
    ALTER TABLE discipline_controls ADD COLUMN IF NOT EXISTS decision_measure_id INTEGER
        REFERENCES discipline_measures(id) ON DELETE SET NULL
    """,
    "ALTER TABLE discipline_controls ADD COLUMN IF NOT EXISTS decision_measure_title VARCHAR(200)",
    "ALTER TABLE discipline_controls ADD COLUMN IF NOT EXISTS decision_finance VARCHAR(16)",
    "ALTER TABLE discipline_controls ADD COLUMN IF NOT EXISTS decision_value_kind VARCHAR(16)",
    "ALTER TABLE discipline_controls ADD COLUMN IF NOT EXISTS decision_amount NUMERIC(12, 2)",
    "ALTER TABLE discipline_controls ADD COLUMN IF NOT EXISTS decision_percent NUMERIC(5, 2)",
    # Ссылка контроля на акт — после того, как таблица актов существует.
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'discipline_controls_act_fk'
               AND conrelid = 'discipline_controls'::regclass
        ) THEN
            ALTER TABLE discipline_controls
                ADD CONSTRAINT discipline_controls_act_fk
                FOREIGN KEY (act_id) REFERENCES discipline_acts(id) ON DELETE SET NULL;
        END IF;
    END $$;
    """,
    # Журнал только дописывается (ТЗ п. 14): правка и удаление строки — ошибка.
    """
    CREATE OR REPLACE FUNCTION discipline_audit_append_only()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Журнал действий раздела «Дисциплина» не редактируется';
    END $$;
    """,
    "DROP TRIGGER IF EXISTS trg_discipline_audit_append_only ON discipline_audit",
    """
    CREATE TRIGGER trg_discipline_audit_append_only
        BEFORE UPDATE OR DELETE ON discipline_audit
        FOR EACH ROW EXECUTE FUNCTION discipline_audit_append_only()
    """,
]

TABLES = (
    'discipline_settings', 'discipline_counting_rules', 'discipline_types', 'discipline_measures',
    'discipline_counters', 'discipline_controls', 'discipline_records', 'discipline_acts', 'discipline_act_records',
    'discipline_files', 'discipline_notifications', 'discipline_audit',
)


def _is_table(statement):
    return 'CREATE TABLE' in statement


def init_discipline_schema(cursor):
    """Разворачивает схему раздела. Порядок: таблицы → миграции → индексы."""
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement % {'now': _NOW})
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)


def schema_is_ready(cursor):
    # Без «public.»: таблица ищется по search_path — на проде это public, а
    # тесты на настоящем Postgres разворачивают раздел в своей схеме.
    cursor.execute("SELECT to_regclass('discipline_records') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
