# -*- coding: utf-8 -*-
"""Схема робота OLX → amoCRM. Таблицы olx_accounts, olx_threads, olx_journal, olx_poll_runs.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается один раз при
старте из Database._init_db через init_olx_amo_schema(cursor).

Ключевые решения, которые видны в DDL:

* **Токены кабинетов — в базе, а не в окружении.** OLX выдаёт access_token на
  час и refresh_token, который меняется при каждом обновлении. Переменные
  окружения правит человек и перезапуск, а этот токен меняется сам каждый час,
  и потерять его — значит заново проходить согласие владельца кабинета в
  браузере по всем девяти кабинетам. В окружении остаётся только то, что
  человек вводит однажды: логин, пароль, client_id и секрет приложения.

* **Дедупликация — уникальным индексом, а не проверкой в коде.** ТЗ требует не
  плодить сделки при повторных сообщениях одного кандидата за день и при его же
  отклике на другую вакансию в том же кабинете. Проверка «сначала посмотрю, нет
  ли» между двумя потоками опроса не спасает: девять кабинетов ходят
  параллельно, и два сообщения одного человека легко разъезжаются по потокам.
  Частичный уникальный индекс по (кабинет, нормализованный номер, дата) ловит
  это на уровне базы, и повтор превращается в честный `duplicate` в журнале.

* **Журнал — отдельная таблица, а не строки в логе.** Раздел 7 ТЗ перечисляет,
  что хранить по КАЖДОМУ обращению: кабинет, идентификатор чата, время отклика,
  время создания сделки, номер до и после нормализации, тег, результат и текст
  ошибки, с выгрузкой за произвольный период. Из текстового лога это не
  достать, а из журнала же считается ежедневная сводка и контроль SLA.

* **Время отклика и время сделки — две колонки, а не одна.** SLA в ТЗ задан
  как разница между ними («не более 1 минуты»), и считать его по времени записи
  строки нельзя: строка пишется после обеих операций. `latency_ms` держим
  посчитанным, чтобы сводка за месяц не пересчитывала разницу на каждой строке.

* **Состояние чата отдельно от журнала.** Журнал — лента событий, её строки не
  переписываются. А «этот чат уже обработан», «этому кандидату автоответ уже
  ушёл» — это состояние, оно меняется. Смешать их в одной таблице значило бы
  либо править историю, либо искать последнюю строку по чату на каждом опросе.

* **`olx_poll_runs` существует ради тишины.** Главный страх ТЗ — «тихий»
  простой: робот молча перестал видеть чаты, и это обнаруживают постфактум.
  Пустой опрос — тоже строка в этой таблице, поэтому «последний опрос был
  20 минут назад» отличимо от «опросы идут, обращений нет».
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Чем закончилась обработка обращения. Лежит в базе КОДАМИ, подписи — на фронте.
JOURNAL_RESULTS = (
    'lead_created',    # сделка заведена, всё по сценарию
    'duplicate',       # тот же номер, тот же кабинет, тот же день — сделки нет намеренно
    'manual_review',   # номер писали, но он не под маской: сделка есть, с пометкой
    'canned_reply',    # номера в обращении нет — ушёл заготовленный ответ
    'needs_human',     # написал снова после автоответа: отвечать должен человек
    'human_reply',     # человек ответил кандидату из раздела
    'skipped',         # обращение не от кандидата (наше же сообщение, системное)
    'error',           # не доехало; обращение остаётся в очереди на повтор
)

# Состояние учётки кабинета. `needs_auth` — токенов нет или refresh отвергнут:
# нужно заново пройти согласие владельца кабинета в браузере.
ACCOUNT_STATES = ('ok', 'needs_auth', 'not_configured', 'disabled', 'error')


_STATEMENTS = [

    # ──────────────────────────────────────────────────────────────────────
    # УЧЁТКА КАБИНЕТА OLX
    #
    # Строка на кабинет, заводится из справочника olx_amo/cabinets.py при
    # старте. Справочник остаётся источником правды по тегам и телефонам —
    # здесь живёт только то, что меняется само: токены и состояние.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_accounts (
        code                   VARCHAR(32) PRIMARY KEY,
        olx_id                 VARCHAR(32) NOT NULL,

        -- Выключить кабинет, не трогая код и не теряя его токены.
        is_enabled             BOOLEAN NOT NULL DEFAULT TRUE,

        -- Токены OLX. access живёт час, refresh приходит один раз и меняется
        -- при каждом обновлении — поэтому пишутся они всегда вместе.
        access_token           TEXT,
        access_token_expires_at TIMESTAMP,
        refresh_token          TEXT,
        token_scope            VARCHAR(200),
        authorized_at          TIMESTAMP,

        -- Наблюдаемость. `last_poll_at` ставится на КАЖДОМ опросе, даже
        -- пустом: по нему ловится тихий простой.
        state                  VARCHAR(24) NOT NULL DEFAULT 'not_configured',
        last_poll_at           TIMESTAMP,
        last_message_at        TIMESTAMP,
        last_lead_at           TIMESTAMP,
        last_error             TEXT,
        last_error_at          TIMESTAMP,
        consecutive_failures   INTEGER NOT NULL DEFAULT 0,

        created_at             TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at             TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    # ──────────────────────────────────────────────────────────────────────
    # СОСТОЯНИЕ ЧАТА
    #
    # ТЗ: «помечать обработанный чат, чтобы не обрабатывать его повторно».
    # Помечаем не сам чат в OLX, а его состояние у себя: отметка «прочитано» в
    # OLX ставится тоже, но полагаться на неё нельзя — её снимает человек,
    # открывший кабинет руками.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_threads (
        id                     SERIAL PRIMARY KEY,
        cabinet_code           VARCHAR(32) NOT NULL,
        thread_id              VARCHAR(64) NOT NULL,

        advert_id              VARCHAR(64),
        advert_title           TEXT,
        interlocutor_id        VARCHAR(64),
        interlocutor_name      VARCHAR(200),

        -- Докуда разобрано. Идём по возрастанию id сообщения, поэтому
        -- «последнее разобранное» — достаточная закладка.
        last_message_id        VARCHAR(64),
        last_message_at        TIMESTAMP,
        messages_seen          INTEGER NOT NULL DEFAULT 0,

        -- Сколько сообщений было в чате, когда мы его последний раз смотрели.
        -- Это НЕ статистика, а пропуск лишних запросов: не изменилось — значит
        -- нового сообщения нет, и лезть за списком незачем. Без этого чаты,
        -- отсечённые горизонтом, навсегда занимали бы весь лимит цикла.
        --
        -- Считаем именно ОБЩЕЕ число, а не непрочитанные. Непрочитанные гасит
        -- человек, открывший чат: счётчик уходит в ноль, следующее сообщение
        -- кандидата возвращает его к прежнему значению — и робот решал бы, что
        -- ничего не появилось, теряя живое обращение. Общее число только растёт.
        last_total_count       INTEGER,

        -- Осталось от первой версии признака «ничего не появилось». Больше не
        -- решает ничего (см. last_total_count), но колонку держим: по ней видно
        -- состояние чатов, заведённых до правки.
        last_unread_count      INTEGER,

        -- Заготовленный ответ на обращение без номера. ТЗ прямо запрещает
        -- слать его повторно в рамках одного обращения.
        canned_reply_sent_at   TIMESTAMP,

        -- Кандидат написал ЕЩЁ раз после автоответа. Робот на это молчит —
        -- решение владельца 02.09.2026: второе автоматическое сообщение
        -- раздражает и выглядит поломкой. Вместо ответа чат подсвечивается в
        -- разделе, чтобы маркетолог ответил сам и по-человечески.
        --
        -- Метка снимается САМА, когда в чате появляется наше исходящее позже
        -- неё: значит человек ответил. Иначе список «ждут ответа» пришлось бы
        -- разгребать руками, а такой список быстро перестают открывать.
        awaiting_human_since   TIMESTAMP,

        -- Чем кончилось: последняя сделка по этому чату, если была.
        amo_lead_id            BIGINT,
        phone_normalized       VARCHAR(16),

        first_seen_at          TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at             TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    "CREATE UNIQUE INDEX IF NOT EXISTS idx_olx_threads_key "
    "ON olx_threads (cabinet_code, thread_id)",

    # ──────────────────────────────────────────────────────────────────────
    # ЖУРНАЛ ОБРАЩЕНИЙ (раздел 7 ТЗ)
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_journal (
        id                     BIGSERIAL PRIMARY KEY,
        cabinet_code           VARCHAR(32) NOT NULL,
        thread_id              VARCHAR(64),
        message_id             VARCHAR(64),

        -- Когда кандидат написал, и когда сделка появилась в amoCRM.
        -- Разница между ними и есть SLA из пункта 6.2.
        message_at             TIMESTAMP,
        lead_created_at        TIMESTAMP,
        latency_ms             INTEGER,

        -- Номер до и после нормализации — ТЗ требует оба.
        phone_raw              VARCHAR(64),
        phone_normalized       VARCHAR(16),

        tag                    VARCHAR(64),
        result                 VARCHAR(24) NOT NULL,
        amo_lead_id            BIGINT,
        amo_contact_id         BIGINT,
        error_text             TEXT,

        -- Текст обращения храним обрезанным: он нужен, чтобы разобрать спорный
        -- случай «почему номер не распознался», а не как архив переписки.
        message_excerpt        TEXT,

        -- Сколько раз обращение уже пробовали отправить. ТЗ: при ошибке
        -- обращение возвращается в очередь, а не удаляется.
        attempts               INTEGER NOT NULL DEFAULT 1,

        created_at             TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    # Лента журнала: почти всегда спрашивают «последнее» и «за период».
    # Список «ждут ответа человека» открывают часто и ждут мгновенно: это
    # рабочая очередь маркетолога, а не отчёт. Индекс частичный — помеченных
    # чатов единицы против всей таблицы.
    "CREATE INDEX IF NOT EXISTS idx_olx_threads_awaiting "
    "ON olx_threads (awaiting_human_since) WHERE awaiting_human_since IS NOT NULL",

    "CREATE INDEX IF NOT EXISTS idx_olx_journal_created "
    "ON olx_journal (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_olx_journal_cabinet_created "
    "ON olx_journal (cabinet_code, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_olx_journal_result "
    "ON olx_journal (result, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_olx_journal_phone "
    "ON olx_journal (phone_normalized, created_at DESC)",

    # Сердце защиты от дублей (раздел 8 ТЗ). Условие `WHERE` оставляет в индексе
    # только реально заведённые сделки: отказы, автоответы и ошибки повторяться
    # обязаны, иначе повторная попытка после сбоя была бы запрещена.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_olx_journal_dedupe "
    "ON olx_journal (cabinet_code, phone_normalized, (message_at::date)) "
    "WHERE result IN ('lead_created', 'manual_review') "
    "AND phone_normalized IS NOT NULL",

    # ──────────────────────────────────────────────────────────────────────
    # ПРОГОНЫ ОПРОСА — чтобы тишина была отличима от простоя
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_poll_runs (
        id                     BIGSERIAL PRIMARY KEY,
        cabinet_code           VARCHAR(32) NOT NULL,
        started_at             TIMESTAMP NOT NULL DEFAULT %(now)s,
        finished_at            TIMESTAMP,
        threads_seen           INTEGER NOT NULL DEFAULT 0,
        messages_seen          INTEGER NOT NULL DEFAULT 0,
        leads_created          INTEGER NOT NULL DEFAULT 0,
        replies_sent           INTEGER NOT NULL DEFAULT 0,
        errors                 INTEGER NOT NULL DEFAULT 0,
        error_text             TEXT
    )
    """ % {'now': _NOW},

    # ──────────────────────────────────────────────────────────────────────
    # КУДА СЛАТЬ УВЕДОМЛЕНИЯ
    #
    # Свой реестр чатов раздел НЕ заводит: группы, в которых есть бот, уже
    # копятся в общей таблице `it_ticket_channels` — её наполняет обработчик
    # `my_chat_member`, и она же кормит выпадающие списки «Обращений» и
    # «Бота опозданий». Второй справочник тех же групп немедленно разошёлся бы
    # с первым.
    #
    # Здесь лежит только ВЫБОР: какие из этих чатов получают отбивку робота.
    # Название дублируется снимком намеренно — чтобы в разделе было что
    # показать, если группу переименовали или бота из неё убрали.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_alert_chats (
        chat_id                BIGINT PRIMARY KEY,
        title                  VARCHAR(255),
        chat_type              VARCHAR(32),
        added_by               INTEGER,
        created_at             TIMESTAMP NOT NULL DEFAULT %(now)s,
        last_sent_at           TIMESTAMP
    )
    """ % {'now': _NOW},

    # ──────────────────────────────────────────────────────────────────────
    # ЧТО МЫ ОТПРАВЛЯЛИ В ЧАТЫ
    #
    # OLX помечает сообщение только направлением: `sent` — наше, `received` —
    # кандидата. Ни автора, ни канала в его выдаче нет, поэтому робот, ответ из
    # портала и сообщение, написанное руками в кабинете OLX, там НЕРАЗЛИЧИМЫ.
    # Значит авторство обязано жить у нас, иначе на вопрос «кто ответил» ответить
    # нечем — а в одном чате будут писать несколько человек.
    #
    # Храним только ИСХОДЯЩИЕ. Переписку целиком дублировать незачем: она живёт
    # в OLX и читается оттуда, а вторая копия немедленно начала бы расходиться
    # с первой. Сообщение кандидата, которого нет у нас, — это норма; наше
    # исходящее, которого нет у нас, — это «написали прямо в кабинете», и так
    # оно в интерфейсе и подписывается.
    #
    # `status` нужен из-за порядка: строку пишем ДО отправки. У автоответа
    # робота порядок сознательно обратный (отметка раньше отправки, чтобы не
    # разослать копий), но здесь страшнее другое — человек уже написал текст, и
    # второй раз он его не напишет. Поэтому не доехавший ответ остаётся видимым
    # со статусом `failed`, а не пропадает.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_outbound (
        id                     BIGSERIAL PRIMARY KEY,
        cabinet_code           VARCHAR(32) NOT NULL,
        thread_id              VARCHAR(64) NOT NULL,

        -- 'canned' — заготовленный ответ робота, 'portal' — ответ человека
        -- из раздела. Третьего вида у нас нет: написанное прямо в кабинете OLX
        -- сюда не попадает по определению.
        kind                   VARCHAR(16) NOT NULL,
        body                   TEXT NOT NULL,

        -- Автор снимком рядом со ссылкой: удаление сотрудника не должно стирать
        -- историю переписки. Тот же приём, что у crm_ticket_messages.
        author_user_id         INTEGER,
        author_name            VARCHAR(200),

        status                 VARCHAR(16) NOT NULL DEFAULT 'pending',
        error_text             TEXT,
        sent_at                TIMESTAMP,
        created_at             TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    "CREATE INDEX IF NOT EXISTS idx_olx_outbound_thread "
    "ON olx_outbound (cabinet_code, thread_id, created_at)",

    # ──────────────────────────────────────────────────────────────────────
    # ПАМЯТЬ УВЕДОМЛЕНИЙ
    #
    # Раздел 7 ТЗ требует уведомлять ответственных о простое, потере
    # авторизации, ошибках передачи и тишине по кабинету. Слать это каждые пять
    # минут, пока держится проблема, нельзя: через час такие сообщения
    # перестают читать, и настоящая авария теряется среди повторов.
    #
    # Поэтому здесь лежит ПОСЛЕДНЕЕ отправленное состояние по каждому поводу, и
    # сообщение уходит только когда состояние сменилось — в том числе обратно
    # на «ok», чтобы человек узнал и о восстановлении, а не гадал.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_alerts (
        key                    VARCHAR(64) PRIMARY KEY,
        state                  VARCHAR(32) NOT NULL,
        detail                 TEXT,
        notified_at            TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    "CREATE INDEX IF NOT EXISTS idx_olx_poll_runs_cabinet "
    "ON olx_poll_runs (cabinet_code, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_olx_poll_runs_started "
    "ON olx_poll_runs (started_at DESC)",
]


_MIGRATIONS = [
    # Признак «в чате появилось новое» переехал с непрочитанных на общее число
    # сообщений: непрочитанные гасит человек, открывший чат, и робот слеп к
    # следующему обращению в нём (найдено на проде 02.09.2026).
    "ALTER TABLE olx_threads ADD COLUMN IF NOT EXISTS last_total_count INTEGER",

    # Повторное обращение после автоответа: робот молчит, а чат ждёт человека.
    "ALTER TABLE olx_threads ADD COLUMN IF NOT EXISTS awaiting_human_since TIMESTAMP",
]


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_olx_amo_schema(cursor):
    """Разворачивает схему робота. Курсор из _init_db, транзакцией правит вызывающий.

    Порядок тот же, что у соседних разделов: сначала таблицы, потом ALTER'ы, и
    только потом индексы — на уже развёрнутой базе индекс по новому столбцу
    иначе создаётся раньше самого столбца и падает, а падение откатывает ВЕСЬ
    разворот схемы.
    """
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «робот ещё не поднялся» от «журнал пуст»."""
    cursor.execute("SELECT to_regclass('public.olx_journal') IS NOT NULL")
    row = cursor.fetchone()
    if not row:
        return False
    return bool(row[0] if not isinstance(row, dict) else list(row.values())[0])
