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

import logging

from . import rules

log = logging.getLogger(__name__)

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
    # Настройки интеграции руками руководителя (владелец 30.10.2026: кабинеты, подключённые к лидам
    # OLX, к рекрутерам не попадают — для найма будут отдельные аккаунты): у OLX здесь список
    # кабинетов «для найма», пустой — ничего не берём. Колонка появилась после выкладки — ALTER.
    "ALTER TABLE hiring_integrations ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}'::jsonb",

    # Звонки откликам из телефонии Binotel (владелец 30.10.2026: «фактически видны, как в удалённом
    # КЦ»): вебхук по звонкам компании и история линии рекрутера. Один звонок — одна строка по
    # generalCallID; касание в ленте карточки — touch_id. result — calls.map_result; recording —
    # calls.recording_state (ссылку на запись спрашивают у Binotel по нажатию, она живёт час).
    """
    CREATE TABLE IF NOT EXISTS hiring_calls (
        id               SERIAL PRIMARY KEY,
        general_call_id  VARCHAR(32) NOT NULL UNIQUE,
        card_id          INTEGER NOT NULL,
        recruiter_id     INTEGER,
        department_id    INTEGER,
        internal_number  VARCHAR(64) NOT NULL,
        external_phone   VARCHAR(16) NOT NULL,
        direction        VARCHAR(8) NOT NULL,
        disposition      VARCHAR(32) NOT NULL DEFAULT '',
        result           VARCHAR(16) NOT NULL DEFAULT '',
        billsec          INTEGER NOT NULL DEFAULT 0,
        waitsec          INTEGER NOT NULL DEFAULT 0,
        started_at       TIMESTAMP NOT NULL,
        recording        VARCHAR(16) NOT NULL DEFAULT 'unknown',
        source           VARCHAR(16) NOT NULL,
        touch_id         BIGINT,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_calls_card ON hiring_calls (card_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_calls_recruiter ON hiring_calls (recruiter_id, started_at)",
    "ALTER TABLE hiring_touches ADD COLUMN IF NOT EXISTS call_id INTEGER",

    # Смены на линии — из статусов работы сотрудника (владелец 30.10.2026: руками не ведутся):
    # учётка сотрудника, в которого превратился кандидат (по телефону), часы дня по статусам и
    # источник смены (auto — статусы, manual — старые ручные отметки).
    "ALTER TABLE hiring_cards ADD COLUMN IF NOT EXISTS employee_user_id INTEGER",
    "ALTER TABLE hiring_shifts ADD COLUMN IF NOT EXISTS work_hours NUMERIC(6,2)",
    "ALTER TABLE hiring_shifts ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'manual'",

    # Номер WhatsApp найма (владелец 06.11.2026): сервис на шлюзе присылает каждое сообщение
    # личных чатов номера (hiring/whatsapp.py). Чат — собеседник: ключ — казахстанский номер, как
    # у карточек, иначе цифры чужого номера или LID (скрытый идентификатор WhatsApp, номер доклеится,
    # когда станет известен). card_id — карточка, в которую лёг чат: повторные сообщения открытой
    # карточки новых откликов не плодят.
    """
    CREATE TABLE IF NOT EXISTS hiring_wa_chats (
        key              VARCHAR(48) PRIMARY KEY,
        jid              VARCHAR(96) NOT NULL,
        phone            VARCHAR(16),
        lid              VARCHAR(32),
        push_name        VARCHAR(200),
        card_id          INTEGER,
        first_at         TIMESTAMP NOT NULL,
        last_at          TIMESTAMP NOT NULL,
        last_inbound_at  TIMESTAMP,
        messages         INTEGER NOT NULL DEFAULT 0,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_chats_phone ON hiring_wa_chats (phone) WHERE phone IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_chats_lid ON hiring_wa_chats (lid) WHERE lid IS NOT NULL",
    # Сообщения: id WhatsApp уникален в чате; правка меняет текст, удаление у всех гасит его.
    # Медиа — описание (тип, размер, длительность), с 09.11.2026 и путь файла в бакете (wa_media.py).
    """
    CREATE TABLE IF NOT EXISTS hiring_wa_messages (
        id               BIGSERIAL PRIMARY KEY,
        chat_key         VARCHAR(48) NOT NULL,
        wa_id            VARCHAR(128) NOT NULL,
        from_me          BOOLEAN NOT NULL,
        sent_at          TIMESTAMP NOT NULL,
        kind             VARCHAR(16) NOT NULL,
        body             TEXT,
        media            JSONB,
        ad               JSONB,
        edited_at        TIMESTAMP,
        deleted_at       TIMESTAMP,
        created_at       TIMESTAMP NOT NULL,
        UNIQUE (chat_key, wa_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_messages_chat ON hiring_wa_messages (chat_key, sent_at)",
    # Файл сообщения от шлюза приходит по id сообщения (владелец 09.11.2026: файлы и голосовые в карточке):
    # путь в бакете, тип и расшифровка — в media; сам файл — в бакете, как фото задач.
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_messages_wa_id ON hiring_wa_messages (wa_id)",
    # Файлы, которых нет и не будет от шлюза само (владелец 10.11.2026: старые фото — «непонятно, как
    # посмотреть»): опрос шлюза раз в 10 с ищет, что попросить у телефона заново, — только среди них.
    # Условие — то же, что queries._NO_FILE.
    """
    CREATE INDEX IF NOT EXISTS idx_hiring_wa_messages_no_file ON hiring_wa_messages (sent_at)
     WHERE kind IN ('image', 'video', 'audio', 'voice', 'document') AND deleted_at IS NULL
       AND media->>'object' IS NULL AND media->>'failed' IS NULL
    """,

    # Каналы — метки откликов с цветом (владелец 09.11.2026): откуда кандидат узнал о вакансии (таргет,
    # hh, Kaspi). С 20.11.2026 слиты с источниками (_merge_channels_into_sources) и в работе не участвуют:
    # таблица и hiring_cards.channel_id остаются следом слияния — старый экземпляр во время выкладки ещё
    # читает их, а hiring_channels.source_id помнит, в какой источник ушёл каждый канал.
    """
    CREATE TABLE IF NOT EXISTS hiring_channels (
        id               SERIAL PRIMARY KEY,
        name             VARCHAR(40) NOT NULL,
        color            VARCHAR(16) NOT NULL,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        position         INTEGER NOT NULL DEFAULT 0,
        created_by       INTEGER,
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_channels_name ON hiring_channels (lower(name))",
    "ALTER TABLE hiring_cards ADD COLUMN IF NOT EXISTS channel_id INTEGER",
    # Код стартового канала (target, hh, kaspi): по нему система ставит метку сама — «Таргет» откликам с
    # рекламы (владелец 09.11.2026). Имя и цвет руководитель правит, код остаётся; свои каналы — без кода.
    "ALTER TABLE hiring_channels ADD COLUMN IF NOT EXISTS code VARCHAR(24)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_channels_code ON hiring_channels (code) WHERE code IS NOT NULL",

    # Вместимость группы на обучение (владелец 18.11.2026): сколько кандидатов она вмещает. Задаёт тренер
    # или руководитель; рекрутер видит свободные места при приглашении, в полную группу не пригласить.
    # NULL — у групп, заведённых до 18.11.2026: ограничения нет, пока вместимость не укажут.
    "ALTER TABLE hiring_groups ADD COLUMN IF NOT EXISTS capacity INTEGER",

    # Переписка из портала (владелец 19.11.2026: окно «Чаты» как в «Чатах ОП», hiring/wa_chat.py). У сообщения —
    # автор (кто ответил из портала; у ответов с телефона его нет), отметка доставки наших сообщений и цитата.
    "ALTER TABLE hiring_wa_messages ADD COLUMN IF NOT EXISTS author_id INTEGER",
    "ALTER TABLE hiring_wa_messages ADD COLUMN IF NOT EXISTS author_name VARCHAR(200)",
    "ALTER TABLE hiring_wa_messages ADD COLUMN IF NOT EXISTS status VARCHAR(16)",
    "ALTER TABLE hiring_wa_messages ADD COLUMN IF NOT EXISTS quote_wa_id VARCHAR(128)",
    # «Ждут ответа» у чата — счётчик, а не подзапрос на строку списка: сколько сообщений кандидата пришло после
    # нашего последнего ответа (автоответ WhatsApp Business не в счёт) и с какого момента ждёт. «Ответ не нужен»
    # гасит счётчик (handled_*). NULL — чат до этой правки: досчитывается один раз ниже.
    "ALTER TABLE hiring_wa_chats ADD COLUMN IF NOT EXISTS waiting INTEGER",
    "ALTER TABLE hiring_wa_chats ADD COLUMN IF NOT EXISTS waiting_since TIMESTAMP",
    "ALTER TABLE hiring_wa_chats ADD COLUMN IF NOT EXISTS last_outbound_at TIMESTAMP",
    "ALTER TABLE hiring_wa_chats ADD COLUMN IF NOT EXISTS handled_at TIMESTAMP",
    "ALTER TABLE hiring_wa_chats ADD COLUMN IF NOT EXISTS handled_by INTEGER",
    """
    WITH outs AS (
        SELECT m.chat_key, MAX(m.sent_at) AS last_out FROM hiring_wa_messages m
         WHERE m.from_me AND m.deleted_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM hiring_wa_messages i
                            WHERE i.chat_key = m.chat_key AND NOT i.from_me
                              AND i.sent_at <= m.sent_at AND i.sent_at > m.sent_at - interval '15 seconds')
         GROUP BY m.chat_key
    ), ins AS (
        SELECT m.chat_key, COUNT(*) AS n, MIN(m.sent_at) AS since FROM hiring_wa_messages m
          LEFT JOIN outs o ON o.chat_key = m.chat_key
         WHERE NOT m.from_me AND m.deleted_at IS NULL AND (o.last_out IS NULL OR m.sent_at > o.last_out)
         GROUP BY m.chat_key
    )
    UPDATE hiring_wa_chats w
       SET last_outbound_at = o.last_out, waiting = COALESCE(i.n, 0), waiting_since = i.since
      FROM hiring_wa_chats w2
      LEFT JOIN outs o ON o.chat_key = w2.key
      LEFT JOIN ins i ON i.chat_key = w2.key
     WHERE w.key = w2.key AND w.waiting IS NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_chats_card ON hiring_wa_chats (card_id) WHERE card_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_chats_last ON hiring_wa_chats (last_at DESC)",
    # Очередь отправки: сообщение из окна «Чаты» ждёт, пока сервис шлюза заберёт его в ответе на опрос. client_id —
    # повтор нажатия «Отправить» (обрыв сети) не даёт второго сообщения. media — файл в бакете (object, mime, имя).
    """
    CREATE TABLE IF NOT EXISTS hiring_wa_outbox (
        id               BIGSERIAL PRIMARY KEY,
        client_id        VARCHAR(64),
        chat_key         VARCHAR(48) NOT NULL,
        card_id          INTEGER,
        author_id        INTEGER NOT NULL,
        author_name      VARCHAR(200),
        kind             VARCHAR(16) NOT NULL DEFAULT 'text',
        body             TEXT,
        quote_wa_id      VARCHAR(128),
        media            JSONB,
        status           VARCHAR(16) NOT NULL,
        attempts         INTEGER NOT NULL DEFAULT 0,
        handed_at        TIMESTAMP,
        wa_id            VARCHAR(128),
        error            TEXT,
        created_at       TIMESTAMP NOT NULL,
        sent_at          TIMESTAMP,
        updated_at       TIMESTAMP NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_hiring_wa_outbox_client ON hiring_wa_outbox (author_id, client_id) WHERE client_id IS NOT NULL",
    # Причина отказа, о котором сообщил шлюз (он этот номер закрыл: «Повторить» — новой строкой); отказы, которые
    # ставит сам портал (просрочка, «не подтверждено»), — без неё.
    "ALTER TABLE hiring_wa_outbox ADD COLUMN IF NOT EXISTS fail_reason VARCHAR(32)",
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_outbox_due ON hiring_wa_outbox (id) WHERE status IN ('queued', 'handed')",
    "CREATE INDEX IF NOT EXISTS idx_hiring_wa_outbox_chat ON hiring_wa_outbox (chat_key, id)",
    # Шаблоны ответов команды найма — общие (рекрутеры пишут одни и те же приглашения и описания вакансий).
    """
    CREATE TABLE IF NOT EXISTS hiring_wa_templates (
        id               SERIAL PRIMARY KEY,
        title            VARCHAR(80) NOT NULL,
        body             TEXT NOT NULL,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_by       INTEGER,
        created_by_name  VARCHAR(200),
        created_at       TIMESTAMP NOT NULL,
        updated_at       TIMESTAMP NOT NULL
    )
    """,

    # Каналы слиты с источниками (владелец 20.11.2026: «понятие Канал надо убрать и всё запихать в источник… выбрать
    # цвет прямо в источниках»). Цвет — у источника (rules.SOURCE_COLORS; NULL — серый), а канал помнит, в какой
    # источник ушёл: по этой связи следующий старт доносит метки, которые успел поставить старый экземпляр.
    "ALTER TABLE hiring_sources ADD COLUMN IF NOT EXISTS color VARCHAR(16)",
    "ALTER TABLE hiring_channels ADD COLUMN IF NOT EXISTS source_id INTEGER",

    # «Отработал 10 дней» (SZOV-10): когда кандидату засчитана десятая смена — категория тренера группы. Карточка при
    # этом остаётся в «Отработал 7 дней»; смены после седьмой считаются дальше, пока отметки нет.
    "ALTER TABLE hiring_cards ADD COLUMN IF NOT EXISTS hired10_at TIMESTAMP",
]


# Окно «Чаты» в реальном времени (hiring/wa_live.py, владелец 20.11.2026): изменение переписки, чата или очереди
# отправки — тычок NOTIFY hiring_wa с номером карточки чата (0 — без карточки), уходит при коммите. Пустое обновление
# (сторож пересчитал «ждут ответа» в то же число) тычка не шлёт. Повтор одного тычка в транзакции PostgreSQL сливает.
LIVE_NOTIFY = """
    CREATE OR REPLACE FUNCTION hiring_wa_live_card() RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_notify('hiring_wa', json_build_object('c', COALESCE(NEW.card_id, 0))::text);
        RETURN NULL;
    END $$;
    CREATE OR REPLACE FUNCTION hiring_wa_live_message() RETURNS TRIGGER LANGUAGE plpgsql AS $$
    DECLARE card INTEGER;
    BEGIN
        SELECT card_id INTO card FROM hiring_wa_chats WHERE key = NEW.chat_key;
        PERFORM pg_notify('hiring_wa', json_build_object('c', COALESCE(card, 0))::text);
        RETURN NULL;
    END $$;
"""
LIVE_TRIGGERS = (
    ('hiring_wa_chats', 'hiring_wa_live_card'),
    ('hiring_wa_outbox', 'hiring_wa_live_card'),
    ('hiring_wa_messages', 'hiring_wa_live_message'),
)


def init_hiring_schema(cursor):
    for statement in _STATEMENTS:
        cursor.execute(statement)
    _seed(cursor)
    _install_live_notify(cursor)


def _install_live_notify(cursor):
    """Триггеры тычков окна «Чаты» — своим SAVEPOINT'ом: не встали — окно живёт на опросе, раздел работает. Триггер
    заводится, только если его нет: каждый старт не берёт на таблицы переписки исключительную блокировку."""
    cursor.execute("SAVEPOINT hiring_wa_live")
    try:
        cursor.execute(LIVE_NOTIFY)
        for table, function in LIVE_TRIGGERS:
            for name, event, condition in (('hiring_wa_live_ins', 'INSERT', ''),
                                           ('hiring_wa_live_upd', 'UPDATE', 'WHEN (OLD.* IS DISTINCT FROM NEW.*) ')):
                cursor.execute("SELECT 1 FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass", (name, table))
                if cursor.fetchone() is None:
                    cursor.execute('CREATE TRIGGER %s AFTER %s ON %s FOR EACH ROW %sEXECUTE FUNCTION %s()'
                                   % (name, event, table, condition, function))
        cursor.execute("RELEASE SAVEPOINT hiring_wa_live")
    except Exception as exc:  # noqa: BLE001
        cursor.execute("ROLLBACK TO SAVEPOINT hiring_wa_live")
        log.warning('hiring: тычки окна «Чаты» не подключены — окно на опросе: %s', exc)


def _seed(cursor):
    """Источники и причины из ТЗ — один раз, в пустые справочники; интеграции — строкой каждая."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('hiring_seed'))")
    now = rules.now_almaty()
    cursor.execute("SELECT EXISTS (SELECT 1 FROM hiring_sources)")
    if not cursor.fetchone()[0]:
        for position, (code, name, kind, platform) in enumerate(rules.SEED_SOURCES):
            cursor.execute("""
                INSERT INTO hiring_sources (code, name, kind, platform, position, created_at, color)
                VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (code) DO NOTHING
            """, (code, name, kind, platform, position, now, rules.SEED_SOURCE_COLORS.get(code)))
    cursor.execute("SELECT EXISTS (SELECT 1 FROM hiring_reasons)")
    if not cursor.fetchone()[0]:
        for kind, names in rules.SEED_REASONS.items():
            for position, name in enumerate(names):
                cursor.execute("""
                    INSERT INTO hiring_reasons (kind, name, requires_comment, position, created_at)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT (kind, name) DO NOTHING
                """, (kind, name, name == rules.OTHER, position, now))
    # Источники, появившиеся после выкладки раздела: в уже заполненный справочник — по коду, один раз
    # (переименование руководителем не трогаем).
    for code, name, kind, platform in rules.LATE_SOURCES:
        cursor.execute("""
            INSERT INTO hiring_sources (code, name, kind, platform, position, created_at)
            VALUES (%s, %s, %s, %s, (SELECT COALESCE(MAX(position), 0) + 1 FROM hiring_sources), %s)
            ON CONFLICT (code) DO NOTHING
        """, (code, name, kind, platform, now))
    merged = _merge_channels_into_sources(cursor, now)
    if merged:
        log.info('hiring: каналы слиты с источниками — перенесено карточек с меткой: %d', merged)
    for code in ('hh', 'olx', 'webhook', 'whatsapp'):
        cursor.execute("""
            INSERT INTO hiring_integrations (code, updated_at) VALUES (%s, %s)
            ON CONFLICT (code) DO NOTHING
        """, (code, now))


def _merge_channels_into_sources(cursor, now):
    """Каналы — в источники (владелец 20.11.2026: «понятие Канал надо убрать и всё запихать в источник. То есть каналы
    надо убрать/объединить с тем, что есть в источниках, и добавить возможность выбрать цвет прямо в источниках»).

    Канал уходит в источник с тем же кодом (стартовые target, hh, kaspi — те же коды, что у источников), иначе — в
    источник откликов с тем же именем без учёта регистра, иначе становится новым источником откликов (выключенный —
    выключенным). Цвет канала становится цветом источника, если у источника своего ещё нет. Карточка с меткой получает
    источником канал: WhatsApp по рекламе с меткой «Таргет» — теперь «Таргет»; метка снимается. Путь, которым отклик
    пришёл (wa:… у WhatsApp, ссылка у площадок), остаётся в platform_ref.

    Новых меток код больше не ставит, поэтому каждый следующий старт находит только то, что во время выкладки успел
    пометить старый экземпляр, и доносит это по той же связи канал → источник. updated_at карточек не трогается:
    доска не перемешивается. Возвращает, сколько карточек с меткой перенесено в источник."""
    cursor.execute("SELECT id, code, name, color, is_active FROM hiring_channels WHERE source_id IS NULL ORDER BY position, id")
    for channel_id, code, name, color, is_active in cursor.fetchall():
        row = None
        if code:
            cursor.execute("SELECT id FROM hiring_sources WHERE code = %s", (code,))
            row = cursor.fetchone()
        if row is None:
            cursor.execute("SELECT id FROM hiring_sources WHERE kind = %s AND lower(name) = lower(%s) ORDER BY id LIMIT 1",
                           (rules.SOURCE_RESPONSES, name))
            row = cursor.fetchone()
        if row is None:
            own_code = 'channel_%d' % int(channel_id)
            cursor.execute("""
                INSERT INTO hiring_sources (code, name, kind, platform, is_active, position, created_at)
                VALUES (%s, %s, %s, NULL, %s, (SELECT COALESCE(MAX(position), 0) + 1 FROM hiring_sources), %s)
                ON CONFLICT (code) DO NOTHING
            """, (own_code, name, rules.SOURCE_RESPONSES, bool(is_active), now))
            cursor.execute("SELECT id FROM hiring_sources WHERE code = %s", (own_code,))
            row = cursor.fetchone()
        source_id = int(row[0])
        if color in rules.SOURCE_COLORS:
            cursor.execute("UPDATE hiring_sources SET color = %s WHERE id = %s AND color IS NULL", (color, source_id))
        cursor.execute("UPDATE hiring_channels SET source_id = %s WHERE id = %s", (source_id, int(channel_id)))
    cursor.execute("""
        UPDATE hiring_cards c SET source_id = ch.source_id, channel_id = NULL
          FROM hiring_channels ch
         WHERE c.channel_id = ch.id AND ch.source_id IS NOT NULL
    """)
    return cursor.rowcount
