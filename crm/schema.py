"""Схема раздела «Обращения» (CRM). Все таблицы с префиксом crm_.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Вызывается один раз при
старте из Database._init_db через init_crm_schema(cursor).

Зачем отдельные таблицы, а не расширение it_tickets. Заявка в IT — выстрел в
одну сторону: улетела в закреплённый чат и на этом жизнь записи кончилась
(в it_tickets нет ни переписки, ни статуса кроме sent/failed, ни адресата
ответа). Здесь нужна двусторонняя нить: сообщение бота в группе — ответы
сотрудников — ответы оператора обратно в ту же нить, и всё это с историей и
статусом. Это другая сущность, а не другое значение поля.

Модель (по ТЗ задач #133 и #29):

    crm_queues          куда уходит обращение — очередь = одна Telegram-группа
    crm_topics          тематики внутри очереди (каталог, редактируется в UI)
    crm_tickets         само обращение: номер, статус, автор, срок, нить в TG
    crm_ticket_messages переписка: исходящие в группу, входящие из группы, заметки
    crm_ticket_events   история действий (кто создал, кто сменил статус, когда)

Ключевые решения, которые видны в DDL:

* **Номер тикета — это id.** Отдельная человекочитаемая нумерация (CRM-2026-0042)
  завела бы вторую последовательность и вторую точку правды; сотруднику в
  Telegram достаточно «Обращение №123», и по нему запись находится однозначно.
* **Непрочитанное живёт в самом тикете** (author_unread_at/author_unread_kind),
  а не в отдельной таблице отметок. У обращения ровно один адресат уведомления —
  автор: это он ждёт ответа и это ему «мгновенное уведомление о выполнении».
  Одна колонка + частичный индекс дешевле джойна с таблицей прочтений на каждый
  запрос колокола.
* **Входящее сообщение уникально по (чат, message_id).** Telegram при сбое сети
  повторяет апдейт, и без этого один ответ сотрудника лёг бы в нить дважды.
* **Статусы в CHECK, но сразу все пять.** Расширять CHECK на живой базе больно,
  поэтому набор берётся из ТЗ #29 целиком (новое / в работе / есть ответ /
  решено / отменено), а не «по мере надобности».
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Статусы обращения. Порядок = порядок жизненного цикла.
TICKET_STATUSES = ('open', 'in_progress', 'answered', 'resolved', 'cancelled')

# Приоритеты. Те же четыре, что у заявок в IT и у задач — чтобы человек не
# переучивался, переходя между разделами.
TICKET_PRIORITIES = ('low', 'normal', 'high', 'critical')

# Откуда пришло обращение. 'manual' — оператор завёл руками; остальные
# заготовлены под ТЗ #28 (из корпоративного чата) и #30 (из телефонии).
TICKET_SOURCES = ('manual', 'chat', 'call', 'api')

# Направление сообщения в нити.
MESSAGE_DIRECTIONS = ('out', 'in', 'note')

_STATEMENTS = [

    # ──────────────────────────────────────────────────────────────────────
    # ОЧЕРЕДИ — «куда уходит обращение»
    #
    # Одна очередь = одна Telegram-группа (iTaxi, Sapar, «Вопросы-ответы с
    # регионами», согласование термокоробов). chat_id допускает NULL
    # намеренно: очередь заводят до того, как бота добавили в группу, и до
    # привязки она просто не предлагается оператору (см. is_ready в queries).
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS crm_queues (
        id             SERIAL PRIMARY KEY,
        title          VARCHAR(160) NOT NULL,
        description    TEXT,
        chat_id        BIGINT,
        chat_title     VARCHAR(255),
        department_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        sla_minutes    INTEGER CHECK (sla_minutes IS NULL OR sla_minutes > 0),
        sort_order     INTEGER NOT NULL DEFAULT 100,
        is_active      BOOLEAN NOT NULL DEFAULT TRUE,
        created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at     TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    # Одна группа — одна очередь: иначе ответ в группе невозможно отнести к
    # очереди, а оператор увидел бы два одинаковых адреса в выпадающем списке.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_queues_chat
        ON crm_queues(chat_id) WHERE chat_id IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_crm_queues_active
        ON crm_queues(is_active, sort_order, id)
    """,

    # ──────────────────────────────────────────────────────────────────────
    # ТЕМАТИКИ — каталог внутри очереди
    #
    # Отдельной таблицей, а не JSONB-каталогом как у заявок в IT: на тематику
    # ссылается тикет, и по ней потом строится отчёт «разбивка по тематикам»
    # (ТЗ #29). Переименованная строка в JSONB осиротила бы всю историю.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS crm_topics (
        id          SERIAL PRIMARY KEY,
        queue_id    INTEGER NOT NULL REFERENCES crm_queues(id) ON DELETE CASCADE,
        title       VARCHAR(160) NOT NULL,
        sort_order  INTEGER NOT NULL DEFAULT 100,
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},
    """
    CREATE INDEX IF NOT EXISTS idx_crm_topics_queue
        ON crm_topics(queue_id, is_active, sort_order, id)
    """,

    # ──────────────────────────────────────────────────────────────────────
    # ОБРАЩЕНИЯ
    #
    # delivery_status отделён от status намеренно. Статус — это про суть
    # («ответили», «решено»), доставка — про транспорт. Если Telegram лежал,
    # обращение не должно пропасть: оно остаётся с delivery_status='failed'
    # и отправляется повторно кнопкой, а не создаётся заново.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS crm_tickets (
        id                 SERIAL PRIMARY KEY,
        queue_id           INTEGER NOT NULL REFERENCES crm_queues(id) ON DELETE RESTRICT,
        topic_id           INTEGER REFERENCES crm_topics(id) ON DELETE SET NULL,
        subject            VARCHAR(300) NOT NULL,
        body               TEXT NOT NULL,
        priority           VARCHAR(16) NOT NULL DEFAULT 'normal'
                           CHECK (priority IN ('low', 'normal', 'high', 'critical')),
        status             VARCHAR(16) NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open', 'in_progress', 'answered', 'resolved', 'cancelled')),
        source             VARCHAR(16) NOT NULL DEFAULT 'manual',

        client_name        VARCHAR(255),
        client_phone       VARCHAR(64),

        created_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_by_name    VARCHAR(255),
        department_id      INTEGER REFERENCES departments(id) ON DELETE SET NULL,

        tg_chat_id         BIGINT,
        tg_message_id      BIGINT,
        delivery_status    VARCHAR(16) NOT NULL DEFAULT 'pending'
                           CHECK (delivery_status IN ('pending', 'sent', 'failed')),
        delivery_error     TEXT,

        due_at             TIMESTAMP,
        first_reply_at     TIMESTAMP,
        -- NOT NULL не для порядка ради порядка: по этому столбцу идёт ORDER BY
        -- каждого списка, и NULL заставил бы обернуть его в COALESCE — а
        -- выражение в сортировке отменяет чтение по индексу. Значение
        -- ставится при вставке и дальше только двигается вперёд.
        last_message_at    TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
        last_inbound_at    TIMESTAMP,

        -- Непрочитанное автором: что именно произошло и когда. NULL = автор
        -- всё видел. Гасится открытием карточки, а не просмотром колокола:
        -- «мне ответили» нельзя закрыть, просто заглянув в список.
        author_unread_at   TIMESTAMP,
        author_unread_kind VARCHAR(16),

        resolved_at        TIMESTAMP,
        resolved_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        resolved_by_name   VARCHAR(255),

        created_at         TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at         TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    # ── Индексы под ФАКТИЧЕСКИЕ запросы раздела ───────────────────────────
    # Порядок столбцов везде: сначала то, по чему фильтруем (периметр), потом
    # то, по чему сортируем. Иначе индекс отдаёт строки, но база всё равно их
    # сортирует — а сортировка и есть то, что дорожает с ростом таблицы.

    # Список «мои обращения» — самый частый запрос раздела: фильтр по автору,
    # порядок по свежести переписки. Покрывает и оператора, и сегмент «Мои».
    """
    CREATE INDEX IF NOT EXISTS idx_crm_tickets_author_recent
        ON crm_tickets(created_by, last_message_at DESC, id DESC)
    """,
    # Список «все» у админа и глава/СВ после отбора периметра: тот же порядок,
    # но без ведущего автора.
    """
    CREATE INDEX IF NOT EXISTS idx_crm_tickets_recent
        ON crm_tickets(last_message_at DESC, id DESC)
    """,
    # Фильтр по очереди («Все группы» → конкретная группа).
    """
    CREATE INDEX IF NOT EXISTS idx_crm_tickets_queue_recent
        ON crm_tickets(queue_id, last_message_at DESC, id DESC)
    """,
    # Периметр главы отдела.
    """
    CREATE INDEX IF NOT EXISTS idx_crm_tickets_department_recent
        ON crm_tickets(department_id, last_message_at DESC, id DESC)
    """,
    # Колокол спрашивает ровно это: «что у меня непрочитано». Частичный индекс —
    # непрочитанных единицы, а обращений со временем десятки тысяч, и полный
    # индекс по столбцу, который почти весь NULL, был бы платой ни за что.
    """
    CREATE INDEX IF NOT EXISTS idx_crm_tickets_unread
        ON crm_tickets(created_by, author_unread_at DESC)
        WHERE author_unread_at IS NOT NULL
    """,
    # Поиск по тексту (ТЗ #29: «поиск по тексту»). ILIKE '%%слово%%' обычным
    # индексом не ускоряется вообще — нужен триграммный GIN. В боевой базе
    # расширение pg_trgm уже стоит (его использует вики), поэтому индекс
    # создаётся; если расширения нет, оператор молча пропускается, и поиск
    # остаётся рабочим, просто последовательным.
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
            -- Один индекс на три столбца сразу: база собирает по нему BitmapOr,
            -- когда условие поиска перечисляет их через ИЛИ. Отдельные индексы
            -- дали бы то же самое, но втрое больше места на запись.
            CREATE INDEX IF NOT EXISTS idx_crm_tickets_search_trgm
                ON crm_tickets USING gin (
                    subject gin_trgm_ops,
                    body gin_trgm_ops,
                    client_name gin_trgm_ops
                );
            -- Телефон ищут по фрагменту («последние четыре цифры»), поэтому
            -- ему тоже нужен триграммный, а не обычный индекс.
            CREATE INDEX IF NOT EXISTS idx_crm_tickets_phone_trgm
                ON crm_tickets USING gin (client_phone gin_trgm_ops);
        END IF;
    END $$
    """,

    # ──────────────────────────────────────────────────────────────────────
    # ПЕРЕПИСКА
    #
    # Корневое сообщение бота тоже лежит здесь строкой direction='out'. Так
    # ответ из Telegram ищется ОДНИМ запросом по (чат, message_id), на что бы
    # сотрудник ни ответил: на исходную заявку, на уточнение оператора или на
    # реплику коллеги, уже попавшую в нить.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS crm_ticket_messages (
        id                SERIAL PRIMARY KEY,
        ticket_id         INTEGER NOT NULL REFERENCES crm_tickets(id) ON DELETE CASCADE,
        direction         VARCHAR(8) NOT NULL CHECK (direction IN ('out', 'in', 'note')),
        body              TEXT,

        author_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        author_name       VARCHAR(255),

        tg_chat_id        BIGINT,
        tg_message_id     BIGINT,
        tg_from_id        BIGINT,
        tg_from_name      VARCHAR(255),
        tg_username       VARCHAR(255),

        attachment_kind   VARCHAR(16),
        attachment_file_id TEXT,
        attachment_name   VARCHAR(255),
        attachment_mime   VARCHAR(128),
        attachment_size   BIGINT,

        created_at        TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    """
    CREATE INDEX IF NOT EXISTS idx_crm_messages_ticket
        ON crm_ticket_messages(ticket_id, created_at, id)
    """,
    # Защита от двойной записи одного апдейта Telegram + рабочий индекс поиска
    # тикета по сообщению, на которое ответили.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_messages_tg
        ON crm_ticket_messages(tg_chat_id, tg_message_id)
        WHERE tg_message_id IS NOT NULL
    """,

    # ──────────────────────────────────────────────────────────────────────
    # ИСТОРИЯ ДЕЙСТВИЙ (ТЗ #29: «кто создал, кто изменил, изменения статусов»)
    #
    # kind без CHECK: список видов событий будет расти вместе с разделом, а
    # менять CHECK на живой таблице ради нового вида события — плохая сделка.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS crm_ticket_events (
        id             SERIAL PRIMARY KEY,
        ticket_id      INTEGER NOT NULL REFERENCES crm_tickets(id) ON DELETE CASCADE,
        kind           VARCHAR(24) NOT NULL,
        actor_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        actor_name     VARCHAR(255),
        payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},
    """
    CREATE INDEX IF NOT EXISTS idx_crm_events_ticket
        ON crm_ticket_events(ticket_id, created_at DESC, id DESC)
    """,
]


def init_crm_schema(cursor):
    """Разворачивает схему раздела. Курсор приходит из _init_db, транзакцией
    управляет вызывающий."""
    for statement in _STATEMENTS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «раздел ещё не поднялся» от «раздел пуст»."""
    cursor.execute("SELECT to_regclass('public.crm_tickets') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
