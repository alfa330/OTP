# -*- coding: utf-8 -*-
"""Схема раздела «Воронка найма» (#404). Идемпотентно: CREATE … IF NOT EXISTS.

Разворачивается при старте из Database._init_db через init_hiring_schema(cursor) — своим
SAVEPOINT'ом, как у «Аренды авто»: не развернулась — раздел недоступен, портал работает.

Решения, которые видны в DDL:

* **Карточка одна на весь путь** (hiring_cards): контакт или отклик в пуле, после «Взять в
  работу» — кандидат с номером (candidate_no). Этап и подстатус — колонки карточки, а вход в
  каждый этап — строкой hiring_card_stages: дашборд считает «достиг этапа, если хоть раз был»
  (ТЗ 8.1), и из одной колонки этого не узнать.
* **Телефон — ключ дубля** (ТЗ 5.3): частичный уникальный индекс по открытым карточкам, а не
  проверка в коде — загрузка файла и опрос OLX идут параллельно, и только база ловит гонку.
* **Закрепление — колонка recruiter_id плюс история** (hiring_assignments): «Взять в работу» —
  один UPDATE с условием recruiter_id IS NULL, второй одновременный получает «Уже в работе»
  (ТЗ 7.1), а переназначения и возвраты в пул остаются в истории.
* **Причины — справочник, а в карточке снимок текста**: руководитель правит справочник,
  закрытые карточки не меняются (ТЗ 6.3).
* **Мотивация — своя строка на карточку** (hiring_motivation): статусы Ожидает → Засчитано →
  Выплачено, отмена с причиной; второй раз за кандидата не засчитывается — UNIQUE(card_id).
* **Уведомления адресные** (hiring_notifications), как у «Оплаты счетов»: строку пишет процесс
  одному человеку, колокол читает её, триггер bell будит адресата.
"""

from . import rules

_STATEMENTS = [

    # Кому раздел выдан руками: руководитель рекрутинга, штатный или удалённый рекрутер.
    """
    CREATE TABLE IF NOT EXISTS hiring_people (
        user_id          INTEGER PRIMARY KEY,
        kind             VARCHAR(16) NOT NULL,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        granted_by       INTEGER,
        granted_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,

    # Источники контактов и откликов (ТЗ 5): kind — contacts / responses; platform — откуда
    # приходит само (hh, olx, webhook) или NULL. Код устойчив, имя правит руководитель.
    """
    CREATE TABLE IF NOT EXISTS hiring_sources (
        id               SERIAL PRIMARY KEY,
        code             VARCHAR(40) NOT NULL UNIQUE,
        name             VARCHAR(120) NOT NULL,
        kind             VARCHAR(16) NOT NULL,
        platform         VARCHAR(16),
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        position         INTEGER NOT NULL DEFAULT 0,
        created_at       TIMESTAMP NOT NULL
    )
    """,

    # Вакансия / должность: куда идёт найм (department_id — отдел, чей руководитель
    # подтверждает «Отработал 7 дней») и есть ли собеседование в офисе (ТЗ 4.2).
    """
    CREATE TABLE IF NOT EXISTS hiring_vacancies (
        id               SERIAL PRIMARY KEY,
        name             VARCHAR(160) NOT NULL,
        department_id    INTEGER,
        office_interview BOOLEAN NOT NULL DEFAULT TRUE,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        position         INTEGER NOT NULL DEFAULT 0,
        created_by       INTEGER,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,

    # Справочник причин отказа и отсева (ТЗ 6.2, 6.3). requires_comment — «Другое».
    """
    CREATE TABLE IF NOT EXISTS hiring_reasons (
        id               SERIAL PRIMARY KEY,
        kind             VARCHAR(24) NOT NULL,
        name             VARCHAR(160) NOT NULL,
        requires_comment BOOLEAN NOT NULL DEFAULT FALSE,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        position         INTEGER NOT NULL DEFAULT 0,
        created_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_reasons_kind_name ON hiring_reasons (kind, name)",

    # Группа на обучение: тренер видит её кандидатов, дата старта — первый день обучения.
    """
    CREATE TABLE IF NOT EXISTS hiring_groups (
        id               SERIAL PRIMARY KEY,
        name             VARCHAR(120) NOT NULL,
        start_date       DATE NOT NULL,
        trainer_id       INTEGER,
        department_id    INTEGER,
        vacancy_id       INTEGER,
        status           VARCHAR(16) NOT NULL DEFAULT 'active',
        created_by       INTEGER,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_groups_trainer ON hiring_groups (trainer_id, status)",

    # Месячная задача на найм (ТЗ 1.2, 3): план в FTE, факт — закрытые наймы месяца.
    """
    CREATE TABLE IF NOT EXISTS hiring_plans (
        id               SERIAL PRIMARY KEY,
        month            DATE NOT NULL,
        vacancy_id       INTEGER,
        department_id    INTEGER,
        recruiter_id     INTEGER,
        plan_fte         NUMERIC(8,2) NOT NULL DEFAULT 0,
        planned_date     DATE,
        comment          TEXT,
        created_by       INTEGER,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_plans_month ON hiring_plans (month, recruiter_id)",

    # Партия загрузки базы контактов (ТЗ 5.1): кто, когда, из какого источника, сколько.
    """
    CREATE TABLE IF NOT EXISTS hiring_batches (
        id               SERIAL PRIMARY KEY,
        source_id        INTEGER NOT NULL,
        file_name        VARCHAR(255),
        loaded_by        INTEGER,
        loaded_by_name   VARCHAR(200),
        loaded_at        TIMESTAMP NOT NULL,
        total_rows       INTEGER NOT NULL DEFAULT 0,
        loaded           INTEGER NOT NULL DEFAULT 0,
        duplicates       INTEGER NOT NULL DEFAULT 0,
        errors           INTEGER NOT NULL DEFAULT 0,
        error_rows       JSONB NOT NULL DEFAULT '[]'::jsonb
    )
    """,

    # Карточка: контакт / отклик / кандидат. stage, substage — rules.STAGES, rules.SUBSTAGES.
    # vacancy_text — вакансия словами, когда площадка прислала название, а в справочнике его нет.
    """
    CREATE TABLE IF NOT EXISTS hiring_cards (
        id                    SERIAL PRIMARY KEY,
        candidate_no          INTEGER,
        kind                  VARCHAR(16) NOT NULL,
        full_name             VARCHAR(200) NOT NULL DEFAULT '',
        phone                 VARCHAR(16) NOT NULL,
        whatsapp              VARCHAR(16),
        city                  VARCHAR(120),
        vacancy_id            INTEGER,
        vacancy_text          VARCHAR(200),
        plan_id               INTEGER,
        source_id             INTEGER,
        batch_id              INTEGER,
        platform_ref          TEXT,
        campaign              VARCHAR(200),
        referred_by           VARCHAR(200),
        received_at           TIMESTAMP NOT NULL,
        stage                 VARCHAR(24) NOT NULL,
        substage              VARCHAR(24),
        recruiter_id          INTEGER,
        taken_at              TIMESTAMP,
        next_contact_at       TIMESTAMP,
        next_contact_notified TIMESTAMP,
        last_touch_at         TIMESTAMP,
        no_answer_streak      INTEGER NOT NULL DEFAULT 0,
        office_visit_at       TIMESTAMP,
        group_id              INTEGER,
        training_started_at   DATE,
        line_started_at       DATE,
        shifts_done           INTEGER NOT NULL DEFAULT 0,
        hired_at              TIMESTAMP,
        hired_by              INTEGER,
        close_initiator       VARCHAR(16),
        close_reason_id       INTEGER,
        close_reason_text     VARCHAR(160),
        close_stage           VARCHAR(16),
        close_day             INTEGER,
        close_comment         TEXT,
        is_closed             BOOLEAN NOT NULL DEFAULT FALSE,
        closed_at             TIMESTAMP,
        repeat_of_card_id     INTEGER,
        reactivations         INTEGER NOT NULL DEFAULT 0,
        comment               TEXT,
        created_by            INTEGER,
        created_at            TIMESTAMP NOT NULL,
        updated_at            TIMESTAMP NOT NULL
    )
    """,
    "CREATE SEQUENCE IF NOT EXISTS hiring_candidate_no_seq",
    # Ключ дубля — телефон открытой карточки (ТЗ 5.3).
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_cards_open_phone ON hiring_cards (phone) WHERE NOT is_closed",
    "CREATE INDEX IF NOT EXISTS idx_hiring_cards_stage ON hiring_cards (stage, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_cards_recruiter ON hiring_cards (recruiter_id, stage)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_cards_taken ON hiring_cards (taken_at)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_cards_next_contact ON hiring_cards (next_contact_at) WHERE next_contact_at IS NOT NULL AND NOT is_closed",
    "CREATE INDEX IF NOT EXISTS idx_hiring_cards_group ON hiring_cards (group_id)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_cards_phone ON hiring_cards (phone)",

    # Вход в этап: по строке на каждый вход (повторная активация даёт второй вход в «В работе»).
    """
    CREATE TABLE IF NOT EXISTS hiring_card_stages (
        id               BIGSERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL,
        stage            VARCHAR(24) NOT NULL,
        substage         VARCHAR(24),
        entered_at       TIMESTAMP NOT NULL,
        by_user_id       INTEGER,
        by_user_name     VARCHAR(200)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_card_stages_card ON hiring_card_stages (card_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_card_stages_stage ON hiring_card_stages (stage, entered_at)",

    # Касания (ТЗ 5.4, 6.1): звонок (result: dial / no_answer), WhatsApp, комментарий,
    # повторный отклик, системная запись. meta — что принесла площадка или телефония.
    """
    CREATE TABLE IF NOT EXISTS hiring_touches (
        id               BIGSERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL,
        kind             VARCHAR(24) NOT NULL,
        result           VARCHAR(24),
        body             TEXT,
        duration_sec     INTEGER,
        author_id        INTEGER,
        author_name      VARCHAR(200),
        at               TIMESTAMP NOT NULL,
        meta             JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_touches_card ON hiring_touches (card_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_touches_author_at ON hiring_touches (author_id, at)",

    # История закреплений (ТЗ 7.1): reason — take / reassign / release / auto_timeout /
    # reactivate / recruiter_off.
    """
    CREATE TABLE IF NOT EXISTS hiring_assignments (
        id               SERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL,
        recruiter_id     INTEGER,
        assigned_at      TIMESTAMP NOT NULL,
        released_at      TIMESTAMP,
        reason           VARCHAR(24) NOT NULL,
        comment          TEXT,
        by_user_id       INTEGER,
        by_user_name     VARCHAR(200)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_assignments_card ON hiring_assignments (card_id, id)",

    # Отметки смен на линии (ТЗ 7.2): руководитель отдела отмечает вручную, день за днём.
    """
    CREATE TABLE IF NOT EXISTS hiring_shifts (
        id               SERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL,
        shift_date       DATE NOT NULL,
        marked_by        INTEGER,
        marked_by_name   VARCHAR(200),
        marked_at        TIMESTAMP NOT NULL,
        CONSTRAINT hiring_shifts_once UNIQUE (card_id, shift_date)
    )
    """,

    # Мотивация (ТЗ 7.2): одна строка на кандидата.
    """
    CREATE TABLE IF NOT EXISTS hiring_motivation (
        id               SERIAL PRIMARY KEY,
        card_id          INTEGER NOT NULL UNIQUE,
        recruiter_id     INTEGER,
        status           VARCHAR(16) NOT NULL,
        confirmed_at     TIMESTAMP,
        confirmed_by     INTEGER,
        paid_at          TIMESTAMP,
        paid_by          INTEGER,
        cancel_reason    TEXT,
        cancelled_at     TIMESTAMP,
        cancelled_by     INTEGER,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_motivation_recruiter ON hiring_motivation (recruiter_id, status, confirmed_at)",

    # Уведомления (ТЗ 8.3): адресные строки для колокола; read_at — погашено.
    """
    CREATE TABLE IF NOT EXISTS hiring_notifications (
        id               SERIAL PRIMARY KEY,
        user_id          INTEGER NOT NULL,
        card_id          INTEGER,
        kind             VARCHAR(32) NOT NULL,
        title            VARCHAR(200) NOT NULL,
        body             TEXT,
        tone             VARCHAR(16) NOT NULL DEFAULT 'default',
        dedupe_key       VARCHAR(120),
        read_at          TIMESTAMP,
        created_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_notifications_unread ON hiring_notifications (user_id, id DESC) WHERE read_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_notifications_dedupe ON hiring_notifications (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL",

    # Состояние интеграций (hh, olx, webhook): когда ходили, что сломалось, закладка.
    """
    CREATE TABLE IF NOT EXISTS hiring_integrations (
        code             VARCHAR(16) PRIMARY KEY,
        state            VARCHAR(24) NOT NULL DEFAULT 'not_configured',
        last_run_at      TIMESTAMP,
        last_ok_at       TIMESTAMP,
        last_error       TEXT,
        cursor           JSONB NOT NULL DEFAULT '{}'::jsonb,
        received_total   INTEGER NOT NULL DEFAULT 0,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
]


def init_hiring_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)
    _seed(cursor)


def _seed(cursor):
    """Источники и причины из ТЗ — один раз, в пустые справочники; интеграции — строкой каждая."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('hiring_seed'))")
    now = rules.now_almaty()
    cursor.execute("SELECT EXISTS (SELECT 1 FROM hiring_sources)")
    if not cursor.fetchone()[0]:
        for position, (code, name, kind, platform) in enumerate(rules.SEED_SOURCES):
            cursor.execute("""
                INSERT INTO hiring_sources (code, name, kind, platform, position, created_at)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (code) DO NOTHING
            """, (code, name, kind, platform, position, now))
    cursor.execute("SELECT EXISTS (SELECT 1 FROM hiring_reasons)")
    if not cursor.fetchone()[0]:
        for kind, names in rules.SEED_REASONS.items():
            for position, name in enumerate(names):
                cursor.execute("""
                    INSERT INTO hiring_reasons (kind, name, requires_comment, position, created_at)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT (kind, name) DO NOTHING
                """, (kind, name, name == rules.OTHER, position, now))
    for code in ('hh', 'olx', 'webhook'):
        cursor.execute("""
            INSERT INTO hiring_integrations (code, updated_at) VALUES (%s, %s)
            ON CONFLICT (code) DO NOTHING
        """, (code, now))
