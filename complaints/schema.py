# -*- coding: utf-8 -*-
"""Схема раздела «Жалобы». Все таблицы с префиксом complaint.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Вызывается один раз при
старте из Database._init_db через init_complaints_schema(cursor).

Почему не строка в crm_tickets. Жалоба живёт дольше и шире обращения: у неё
есть «на кого» (сотрудник, отдел, офис, парк), итог проверки, цепочка работы с
сотрудником и тренинг в журнале, а в группу она уходит не всегда — жалобы на
Яндекс только фиксируются. В обращении ничего этого нет, и разместить всё это
там значило бы получить в crm_tickets два десятка столбцов, пустых у всех
остальных тематик, и второй смысл у каждого статуса. Транспорт в Telegram при
этом общий — crm.transport, — а своё здесь только то, чем жалоба отличается.

Модель:

    complaints            сама жалоба: на кого, почему, водитель, итог, работа
    complaint_messages    переписка с группой: ответ водителю, вопрос оператору,
                          внутреннее обсуждение
    complaint_work_log    журнал работы с сотрудником: что сделано и когда
    complaint_events      история действий — кто создал, кто что поменял

Ключевые решения, которые видны в DDL:

* **Статус — два значения, open и closed**, и считает его не человек, а правило
  (catalog.is_closed): итог проверки есть И работа с сотрудником не висит.
  Отдельной кнопки «закрыть жалобу» нет намеренно — ТЗ требует, чтобы жалоба на
  сотрудника не считалась закрытой, пока работа с ним не проведена, и кнопка
  была бы способом это обойти.
* **Факты цепочки — флагами** (feedback_done, training_done, training_required),
  а состояние работы — отдельным столбцом work_state. Флаги отвечают фильтрам
  аналитики из ТЗ («проведённой обратной связи», «проведённым тренингам»),
  состояние — очереди «К разбору» у супервайзера. Считать их каждый раз из
  журнала значило бы джойн с журналом в каждом запросе списка.
* **Отдел жалобы — target_department_id**: отдел сотрудника, на которого
  жалуются (подразделение КЦ или фронт-офис). По нему супервайзеры и главы
  отдела видят жалобы своих людей. У жалоб на аренду, парк и Яндекс его нет.
* **Непрочитанное у автора — в самой жалобе**, как у обращений: адресат ответа
  один — оператор, заведший жалобу.
* **CHECK'ов на справочные коды нет.** Цели, причины, итоги и действия — список
  из ТЗ, он проверяется в catalog.py и тестом. Расширять CHECK на живой таблице
  ради новой причины — плохая сделка (так уже решили в crm_ticket_events).
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

STATUSES = ('open', 'closed')

# Вид сообщения в переписке с группой.
#   root            само сообщение бота с жалобой
#   prompt          сообщение бота «напишите ответ ответом на это сообщение»
#   answer          ответ для водителя — ЕДИНСТВЕННОЕ, что видит оператор из группы
#   question        уточняющий вопрос группы оператору
#   operator_reply  ответ оператора из iCORE в группу
#   internal        внутреннее обсуждение группы — оператору не показывается
#   notice          отбивка бота («итог проверки», «работа проведена»)
MESSAGE_KINDS = ('root', 'prompt', 'answer', 'question', 'operator_reply',
                 'internal', 'notice')

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS complaints (
        id                    SERIAL PRIMARY KEY,

        -- На кого или на что жалоба и почему (catalog.TARGETS).
        target                VARCHAR(24) NOT NULL,
        reason_code           VARCHAR(32) NOT NULL,

        -- Отдел сотрудника, на которого жалуются: подразделение КЦ или
        -- фронт-офис. По нему жалобу видят и разбирают СВ и глава отдела.
        target_department_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,

        -- «Подразделение, если удалось определить»: отдел КЦ, офис или парк.
        -- Имя — снимок: офис переименуют, а жалоба должна читаться как была.
        unit_kind             VARCHAR(16),
        unit_id               INTEGER,
        unit_name             VARCHAR(255),

        -- Сотрудник, на которого поступила жалоба. Может появиться позже:
        -- «супервайзер должен иметь возможность самостоятельно определить и
        -- проставить сотрудника».
        employee_id           INTEGER REFERENCES users(id) ON DELETE SET NULL,
        employee_name         VARCHAR(255),
        employee_source       VARCHAR(16),
        employee_set_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        employee_set_by_name  VARCHAR(255),
        employee_set_at       TIMESTAMP,
        -- Кому поставлена задача провести работу с сотрудником: его СВ, а
        -- если СВ нет — глава его отдела. Снимок на момент определения.
        responsible_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        -- Кого назвал оператор при приёме жалобы. Не меняется, когда СВ по
        -- итогам проверки ставит другого: ТЗ требует сохранять и «кто был
        -- определён», и «кто фактически являлся сотрудником».
        reported_employee_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        reported_employee_name VARCHAR(255),

        -- Данные водителя (ТЗ: «оператор указывает основные данные»).
        driver_name           VARCHAR(255) NOT NULL,
        driver_phone          VARCHAR(64) NOT NULL,
        driver_ref            VARCHAR(64),
        city                  VARCHAR(120) NOT NULL,
        description           TEXT NOT NULL,
        event_at              TIMESTAMP,
        -- Указано ли время события. Дата без времени хранится полуночью, и без
        -- этого флага «00:00» в карточке нельзя было бы отличить от настоящей
        -- полуночи.
        event_time_known      BOOLEAN NOT NULL DEFAULT FALSE,

        -- «Требует обработки» (уходит в группу) или «Жалоба зафиксирована».
        requires_processing   BOOLEAN NOT NULL,
        status                VARCHAR(16) NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'closed')),
        closed_at             TIMESTAMP,

        -- Итог проверки (catalog.RESULTS) и принятые меры.
        result_code           VARCHAR(24),
        result_note           TEXT,
        result_by             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        result_by_name        VARCHAR(255),
        result_via            VARCHAR(16),
        result_at             TIMESTAMP,

        -- Работа с сотрудником (catalog.WORK_*).
        work_state            VARCHAR(16),
        feedback_done         BOOLEAN NOT NULL DEFAULT FALSE,
        training_required     BOOLEAN NOT NULL DEFAULT FALSE,
        training_done         BOOLEAN NOT NULL DEFAULT FALSE,
        work_closed_at        TIMESTAMP,
        work_closed_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        work_closed_by_name   VARCHAR(255),

        created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name       VARCHAR(255),
        creator_department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,

        -- Сообщение в группе. Адрес — снимок при отправке, как у обращений:
        -- чат потом перепривяжут, а нить этой жалобы останется в прежнем.
        tg_chat_id            BIGINT,
        tg_chat_title         VARCHAR(255),
        tg_message_id         BIGINT,
        delivery_status       VARCHAR(16) NOT NULL DEFAULT 'none'
                              CHECK (delivery_status IN ('none', 'pending', 'sent', 'failed')),
        delivery_error        TEXT,

        -- Вопрос группы оператору, на который он ещё не ответил.
        question_open_at      TIMESTAMP,
        -- Последний ответ для водителя.
        answer_at             TIMESTAMP,

        author_unread_at      TIMESTAMP,
        author_unread_kind    VARCHAR(16),
        author_unread_count   INTEGER NOT NULL DEFAULT 0,

        -- По нему сортируется каждый список раздела; NOT NULL по той же
        -- причине, что у обращений: COALESCE в ORDER BY отменил бы индекс.
        last_activity_at      TIMESTAMP NOT NULL DEFAULT %(now)s,
        created_at            TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at            TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    # ── Индексы под фактические запросы раздела ──────────────────────────
    # Лента «Мои» у оператора.
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_author_recent
        ON complaints(created_by, last_activity_at DESC, id DESC)
    """,
    # Свои жалобы в ленте «Обращений»: непрочитанное наверху, как у обращений
    # (idx_crm_tickets_author_attention). Выражение — дословно как в ORDER BY.
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_author_attention
        ON complaints(created_by, (author_unread_at IS NULL), last_activity_at DESC, id DESC)
    """,
    # Лента «Все» у админа и периметр отдела у главы и СВ.
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_recent
        ON complaints(last_activity_at DESC, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_department_recent
        ON complaints(target_department_id, last_activity_at DESC, id DESC)
    """,
    # Очередь «К разбору» — открытая работа у конкретного СВ. Частичный: таких
    # строк единицы, а жалоб со временем тысячи.
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_work_pending
        ON complaints(responsible_id, last_activity_at DESC)
        WHERE work_state = 'pending'
    """,
    # Колокол автора — «что у меня непрочитано».
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_unread
        ON complaints(created_by, author_unread_at DESC)
        WHERE author_unread_at IS NOT NULL
    """,
    # Аналитика и выгрузка идут по периоду создания.
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_created
        ON complaints(created_at DESC, id DESC)
    """,
    # Повторные жалобы на одного сотрудника.
    """
    CREATE INDEX IF NOT EXISTS idx_complaints_employee
        ON complaints(employee_id, created_at DESC)
        WHERE employee_id IS NOT NULL
    """,
    # Поиск по водителю: ФИО и телефон по фрагменту — триграммы, как у
    # обращений. Расширения нет — поиск остаётся рабочим, просто медленнее.
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
            CREATE INDEX IF NOT EXISTS idx_complaints_driver_trgm
                ON complaints USING gin (driver_name gin_trgm_ops,
                                         driver_phone gin_trgm_ops);
        END IF;
    END $$
    """,

    """
    CREATE TABLE IF NOT EXISTS complaint_messages (
        id                     SERIAL PRIMARY KEY,
        complaint_id           INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
        kind                   VARCHAR(16) NOT NULL,
        body                   TEXT,
        -- У приглашения «напишите ответ» — чего именно ждём: answer | question.
        prompt_kind            VARCHAR(16),
        author_user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        author_name            VARCHAR(255),
        tg_chat_id             BIGINT,
        tg_message_id          BIGINT,
        tg_from_id             BIGINT,
        tg_from_name           VARCHAR(255),
        tg_username            VARCHAR(255),
        reply_to_tg_message_id BIGINT,
        attachment_kind        VARCHAR(16),
        attachment_file_id     TEXT,
        attachment_name        VARCHAR(255),
        attachment_mime        VARCHAR(128),
        attachment_size        BIGINT,
        created_at             TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},
    """
    CREATE INDEX IF NOT EXISTS idx_complaint_messages_thread
        ON complaint_messages(complaint_id, created_at, id)
    """,
    # Повтор апдейта Telegram не должен лечь в нить дважды; он же — поиск
    # жалобы по сообщению, на которое ответили в группе.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_complaint_messages_tg
        ON complaint_messages(tg_chat_id, tg_message_id)
        WHERE tg_message_id IS NOT NULL
    """,

    """
    CREATE TABLE IF NOT EXISTS complaint_work_log (
        id                SERIAL PRIMARY KEY,
        complaint_id      INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
        action            VARCHAR(24) NOT NULL,
        -- «Какая обратная связь была проведена» и «результат» — два поля ТЗ
        -- для записи в тренингах, поэтому и здесь два.
        comment           TEXT NOT NULL,
        outcome           TEXT,
        need_training     BOOLEAN NOT NULL DEFAULT FALSE,
        -- Запись в «Тренингах», созданная этой работой (ОС или тренинг).
        training_id       INTEGER REFERENCES trainings(id) ON DELETE SET NULL,
        employee_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name   VARCHAR(255),
        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},
    """
    CREATE INDEX IF NOT EXISTS idx_complaint_work_log_complaint
        ON complaint_work_log(complaint_id, created_at, id)
    """,

    """
    CREATE TABLE IF NOT EXISTS complaint_events (
        id             SERIAL PRIMARY KEY,
        complaint_id   INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
        kind           VARCHAR(24) NOT NULL,
        actor_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name     VARCHAR(255),
        payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},
    """
    CREATE INDEX IF NOT EXISTS idx_complaint_events_complaint
        ON complaint_events(complaint_id, created_at DESC, id DESC)
    """,

    # ──────────────────────────────────────────────────────────────────────
    # НАСТРОЙКИ — в какую Telegram-группу уходят жалобы
    #
    # Одна строка (id = 1): группа у раздела одна — «Жалобы КЦ, регионы,
    # таксопарк». Не очередь «Обращений»: там очередь — это тематика со своими
    # темами и маршрутами, и жалоба стояла бы в её настройке строкой «своих тем
    # нет», ничего не объясняя. Выбирают из того же реестра чатов бота
    # (it_ticket_channels), FK на него нет по той же причине, что у маршрутов
    # обращений: бота могут выгнать, и настройка должна это пережить.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS complaint_settings (
        id                 INTEGER PRIMARY KEY CHECK (id = 1),
        chat_id            BIGINT,
        chat_title         VARCHAR(255),
        mention_usernames  TEXT,
        updated_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by_name    VARCHAR(255),
        updated_at         TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},
]

# Столбцы, появившиеся после первого выката. Пока пусто — список заведён
# сразу, чтобы порядок «таблицы → ALTER → индексы» был виден с первого дня
# (crm/schema.py поплатился за обратный порядок падением прода 17.08.2026).
_MIGRATIONS = []


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_complaints_schema(cursor):
    """Разворачивает схему раздела. Порядок: таблицы → ALTER → индексы."""
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)
    # Строка настроек заводится сама и пустой: админу остаётся выбрать группу.
    cursor.execute('INSERT INTO complaint_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING')


def schema_is_ready(cursor):
    cursor.execute("SELECT to_regclass('public.complaints') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
