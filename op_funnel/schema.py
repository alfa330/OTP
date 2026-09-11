# -*- coding: utf-8 -*-
"""Схема раздела «Воронка ОП». Таблицы op_funnel_*.

Идемпотентно (CREATE TABLE / INDEX IF NOT EXISTS), разворачивается один раз при
старте из `Database._init_db` через `init_op_funnel_schema(cursor)`.

Четыре направления — четыре источника
-------------------------------------
Раздел собирает ежедневную воронку обзвона по направлениям отдела продаж (отдел
367). Направления в портале уже есть, и таб опирается на КОД МОДЕЛИ расчёта, а
не на `direction_id`: переименование направления обнуляет `direction_id` у
операторов, а код модели живёт и на группе.

    op_osnova       «Основа ОП»            amoCRM, воронка 5524684 «Отдел продаж»
    op_potok        «Поток»                POST /api/partners/stream-leads (потоки 1 и 2)
    op_yandex_reg   «Яндекс Регистрация»   POST /api/partners/paid-hire-leads
    op_verificator  «Верификатор»          чаты Wazzup + ручная выгрузка тикетов

Зачем хранить у себя то, что есть в СРМ
---------------------------------------
**СРМ переписывает прошлое.** Замерено 11.09.2026: снимок супервайзера за
01.09 против того, что партнёрская ручка отдаёт сейчас, — состав лидов совпал
ровно (1125 против 1125), а исходы уехали: Дозвон 499 → 534, Согласия 257 → 267.
У одного оператора все 108 лидов ушли в «свободные» — лиды просто открепили.
Операторы продолжают звонить по старым лидам, и статус лида меняется задним
числом, потому что в СРМ у лида ОДИН статус, а не история.

Отсюда два хранилища с разными обязанностями:

* `op_funnel_leads` — ПОСЛЕДНЕЕ известное состояние лида. Нужно, чтобы клик по
  причине отказа открывал список конкретных людей (требование приёмки ТЗ #301),
  и чтобы пересчитать сутки, когда правила изменились.
* `op_funnel_daily` — ЗАФИКСИРОВАННЫЙ суточный итог по оператору. Пишется один
  раз и сам не меняется. Иначе отчёт за вчера завтра будет другим, а супервайзер
  уже назвал эти цифры на планёрке.

Расхождения не прячутся, а записываются в `op_funnel_drift`: пересчёт суток
сравнивает новый итог с зафиксированным и складывает разницу туда. Тот же приём,
что у «Топа по регистрациям» (`reg_contest_operator_changes` + сторож усадки
снапшота): источник, который переписывает прошлое, обязан оставлять след.

Почему сопоставление операторов — таблица, а не алгоритм
--------------------------------------------------------
Ни один источник не отдаёт id сотрудника. СРМ отдаёт имя строкой, amoCRM —
числовой id пользователя amoCRM (справочник `/api/v4/users` закрыт: 403 «Admin
access only», проверено 11.09.2026 и на одном пользователе тоже). А имена в
источниках и в портале НЕ СОВПАДАЮТ, вот живые пары:

    СРМ «Кузембаева Аяулым»       портал «Кузембекова Аяулым»
    СРМ «Жұмаханбет Алдияр»       портал «Жуманхабет Алдияр»
    СРМ «Сарсенбаева Эльдана»     портал «Елдана Сарсенбаева»   (и порядок, и Э/Е)
    СРМ «Қуандыққызы Іңкәр»       портал «Куандыккызы Іңкәр»

Автоматическое сходство здесь даёт молчаливую ошибку — чужие цифры в чужой
строке, и снаружи это не видно. Поэтому соответствие подтверждает человек
(`op_funnel_operator_map`), а похожесть работает только подсказкой в интерфейсе.
Так уже сделано для Wazzup (`wazzup_operator_map`), и его мы не дублируем —
чаты берём через него.

Нормы и пороги — данные, а не код
---------------------------------
ТЗ прямо требует: пороги раскраски настраиваемые, справочник причин пополняется
без релиза. Поэтому нормы («20 дозвонов в час»), таргеты («ответ за 120 секунд
днём, 180 ночью»), веса показателей и границы цветов лежат в `op_funnel_targets`
с датой вступления в силу: пересчёт старых суток не должен поехать из-за того,
что сегодня подняли план.
"""

# Сколько суток лидов держим в снимке. Год: дальше живут только суточные итоги,
# они на три порядка компактнее (13 операторов против 30 тысяч лидов в месяц).
LEADS_RETENTION_DAYS = 400

# Коды направлений = коды моделей расчёта отдела продаж (database.py,
# CALCULATION_MODEL_OP_SALES_CODES). Один источник правды на бэке и фронте.
DIRECTION_CODES = ('op_osnova', 'op_potok', 'op_yandex_reg', 'op_verificator')

# Источники лидов. Вынесены в константы, потому что по ним идёт и ключ строки, и
# выбор клиента в sync.py.
SOURCE_CRM_STREAM = 'crm_stream'
SOURCE_CRM_PAID_HIRE = 'crm_paid_hire'
SOURCE_AMO = 'amo'
SOURCE_WAZZUP = 'wazzup'
SOURCE_MANUAL = 'manual'
# Обращения СРМ. Отдельный источник сопоставления, а не общий с воронками: в
# обращениях человек назван так, как его завели в СРМ, и это третье написание
# имени после портала и Wazzup.
SOURCE_CRM_TICKETS = 'crm_tickets'

OP_FUNNEL_SCHEMA_SQL = """
-- ── СОПОСТАВЛЕНИЕ ОПЕРАТОРОВ ───────────────────────────────────────────────
-- «Как человек называется в источнике» → «кто это в портале».
--
-- Ключ составной с источником: один и тот же человек в СРМ зовётся «Габит
-- Аружан», а в amoCRM он число 12666570. Одна таблица на все источники, чтобы
-- экран сопоставления был один, а не четыре.
--
-- user_id NULL — это «видели, но ещё не сопоставили»: строка заводится самой
-- выгрузкой, чтобы в интерфейсе было видно, кого именно не хватает. Лиды такого
-- оператора не теряются, они просто складываются в «не сопоставлен».
CREATE TABLE IF NOT EXISTS op_funnel_operator_map (
    source        VARCHAR(24)  NOT NULL,
    external_key  VARCHAR(190) NOT NULL,
    external_name TEXT         NOT NULL DEFAULT '',
    user_id       INTEGER,
    -- Направление, в котором этого человека встретили впервые. Подсказка для
    -- экрана сопоставления («ищите среди операторов Потока»), не ограничение.
    hint_direction VARCHAR(24) NOT NULL DEFAULT '',
    -- Явный отказ сопоставлять: бот, тестовая учётка, ушедший подрядчик.
    -- Отличается от NULL тем, что строка перестаёт мигать в интерфейсе.
    is_ignored    BOOLEAN      NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_by    INTEGER,
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, external_key)
);

CREATE INDEX IF NOT EXISTS idx_op_funnel_map_user
    ON op_funnel_operator_map (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_op_funnel_map_pending
    ON op_funnel_operator_map (source, last_seen_at DESC)
    WHERE user_id IS NULL AND is_ignored = FALSE;

-- ── СНИМОК ЛИДОВ ───────────────────────────────────────────────────────────
-- Последнее известное состояние лида. Единица — лид источника, не событие:
-- истории статусов СРМ не отдаёт (см. шапку модуля).
CREATE TABLE IF NOT EXISTS op_funnel_leads (
    direction_code VARCHAR(24)  NOT NULL,
    source         VARCHAR(24)  NOT NULL,
    -- Номер потока для «Поток-1»/«Поток-2»; у остальных источников 0. В ключе,
    -- потому что один и тот же водитель живёт в обеих базах отдельными лидами.
    stream_type    SMALLINT     NOT NULL DEFAULT 0,
    lead_key       VARCHAR(64)  NOT NULL,

    -- Сутки, к которым отнесён лид. Правило то же, что на экране СРМ и в
    -- таблице супервайзера: взят в работу — по дате взятия, не взят — по дате
    -- загрузки базы. Отдельной колонкой, а не выражением: по ней идут и выборка
    -- периода, и перезапись суток.
    work_day       DATE         NOT NULL,

    -- Кто отработал. NULL = оператор ещё не сопоставлен или лид свободен.
    user_id        INTEGER,
    owner_raw      TEXT         NOT NULL DEFAULT '',

    -- Сырые поля источника. Храним как пришли: справочник причин в СРМ
    -- свободный (за август-сентябрь 40 разных значений, включая «не интеросно»
    -- и «своих не предаю дид»), и нормализовать их на записи значит потерять то,
    -- что человек на самом деле выбрал.
    stage_raw      TEXT         NOT NULL DEFAULT '',
    call_status    TEXT         NOT NULL DEFAULT '',
    dialog_status  TEXT         NOT NULL DEFAULT '',
    reason_raw     TEXT         NOT NULL DEFAULT '',
    reason_code    VARCHAR(64)  NOT NULL DEFAULT '',
    sub_reason_raw TEXT         NOT NULL DEFAULT '',

    -- Нормализованный исход. Считается на записи (metrics.classify_lead), чтобы
    -- выборки за период не пересчитывали правила по 30 тысячам строк.
    --   reach_outcome:  dozvon | nedozvon | new
    --   dialog_outcome: success | agree | reject | untargeted | callback | none
    --   reason_bucket:  nedozvon | otkaz | netsel | ''  (в какую разбивку идёт)
    reach_outcome  VARCHAR(16)  NOT NULL DEFAULT 'new',
    dialog_outcome VARCHAR(16)  NOT NULL DEFAULT 'none',
    reason_bucket  VARCHAR(16)  NOT NULL DEFAULT '',

    -- Для списка за причиной: человек, которому звонили.
    full_name      TEXT         NOT NULL DEFAULT '',
    phone          VARCHAR(24)  NOT NULL DEFAULT '',
    park_name      TEXT         NOT NULL DEFAULT '',
    city           TEXT         NOT NULL DEFAULT '',
    base_title     TEXT         NOT NULL DEFAULT '',
    comment        TEXT         NOT NULL DEFAULT '',

    -- Времена источника, местные (Алматы). Нужны, чтобы отличить «взял и не
    -- звонил» от «не брал».
    created_at     TIMESTAMP,
    taken_at       TIMESTAMP,
    updated_at     TIMESTAMP,

    captured_at    TIMESTAMP    NOT NULL DEFAULT NOW(),

    PRIMARY KEY (direction_code, source, stream_type, lead_key)
);

CREATE INDEX IF NOT EXISTS idx_op_funnel_leads_day
    ON op_funnel_leads (direction_code, work_day DESC, user_id);
CREATE INDEX IF NOT EXISTS idx_op_funnel_leads_reason
    ON op_funnel_leads (direction_code, work_day, reason_bucket, reason_code)
    WHERE reason_bucket <> '';
CREATE INDEX IF NOT EXISTS idx_op_funnel_leads_phone
    ON op_funnel_leads (phone) WHERE phone <> '';

-- ── ЗАФИКСИРОВАННЫЙ СУТОЧНЫЙ ИТОГ ──────────────────────────────────────────
-- Одна строка = один оператор за одни сутки одного направления.
--
-- Пишется один раз и сам не меняется: цифра, названную на планёрке, задним
-- числом менять нельзя. Пересчёт возможен только явным «перечитать сутки», и
-- тогда расхождение уходит в op_funnel_drift.
CREATE TABLE IF NOT EXISTS op_funnel_daily (
    direction_code VARCHAR(24)  NOT NULL,
    work_day       DATE         NOT NULL,
    -- 0 — строка «не сопоставленные»: без неё сумма по операторам не сходится с
    -- итогом команды, и это выглядит как ошибка расчёта, а не как пробел в
    -- сопоставлении.
    user_id        INTEGER      NOT NULL,

    -- Кадровое на дату: ставка и смена в конце месяца уже другие.
    rate           NUMERIC(4,2) NOT NULL DEFAULT 0,
    shift_kind     VARCHAR(8)   NOT NULL DEFAULT 'day',   -- day | night
    group_id       INTEGER,

    -- Часы. work_hours — то, что пойдёт в расчёты; hours_source говорит, откуда
    -- они взялись, и это ВИДНО в интерфейсе:
    --   phone    из статусов iCORE Phone (факт)
    --   schedule из графика смен — для направлений без телефона. У Верификаторов
    --            статусов нет вовсе: за 01–11.09 у группы 13 записи в daily_hours
    --            есть, а часы в них нулевые (проверено на проде 11.09.2026).
    --   manual   из ручной выгрузки
    work_hours     NUMERIC(6,2) NOT NULL DEFAULT 0,
    hours_source   VARCHAR(12)  NOT NULL DEFAULT 'phone',

    -- Воронка. Одни и те же колонки на все направления: «обработано → дозвон →
    -- согласие → успех» есть везде, различается только, что этим считается
    -- (metrics.py). Пустые для направления колонки остаются нулями, а не
    -- заводят вторую таблицу.
    handled        INTEGER      NOT NULL DEFAULT 0,  -- обработано лидов (Исход)
    reached        INTEGER      NOT NULL DEFAULT 0,  -- дозвон
    not_reached    INTEGER      NOT NULL DEFAULT 0,  -- недозвон
    agreed         INTEGER      NOT NULL DEFAULT 0,  -- согласия
    succeeded      INTEGER      NOT NULL DEFAULT 0,  -- успешно (вышел на линию / регистрация)
    rejected       INTEGER      NOT NULL DEFAULT 0,  -- отказы
    untargeted     INTEGER      NOT NULL DEFAULT 0,  -- нецелевые
    callbacks      INTEGER      NOT NULL DEFAULT 0,  -- перезвон назначен
    inbound        INTEGER      NOT NULL DEFAULT 0,  -- входящая линия (Основа)

    -- План на сутки: часы × норму из op_funnel_targets на эту дату. Храним
    -- посчитанным, иначе прошлое поедет при правке норм.
    plan_reached   NUMERIC(8,2) NOT NULL DEFAULT 0,
    plan_agreed    NUMERIC(8,2) NOT NULL DEFAULT 0,

    -- Чаты и тикеты Верификаторов. Здесь же, а не в своей таблице: это тот же
    -- «обработано» по смыслу, и в Excel супервайзера это одна строка.
    chats          INTEGER      NOT NULL DEFAULT 0,
    tickets        INTEGER      NOT NULL DEFAULT 0,
    chat_reply_seconds   NUMERIC(8,1),   -- среднее время ответа в чате
    ticket_handle_seconds NUMERIC(8,1),  -- среднее время обработки тикета
    quality_score  NUMERIC(5,2),         -- качество из «Оценок ИИ», если есть

    -- Место под метрику, которой ещё нет. Новая колонка на живой таблице в 3 млн
    -- строк — это блокировка; jsonb позволяет добавить показатель в тот же день.
    extra          JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- Когда зафиксировали и когда последний раз перечитывали.
    captured_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    recaptured_at  TIMESTAMP,

    PRIMARY KEY (direction_code, work_day, user_id)
);

CREATE INDEX IF NOT EXISTS idx_op_funnel_daily_user
    ON op_funnel_daily (user_id, work_day DESC);
CREATE INDEX IF NOT EXISTS idx_op_funnel_daily_group
    ON op_funnel_daily (group_id, work_day DESC) WHERE group_id IS NOT NULL;

-- ── РАЗБИВКА ПО ПРИЧИНАМ ───────────────────────────────────────────────────
-- Отдельной таблицей, а не колонками: причин в источниках десятки, они
-- пополняются без нашего участия (в СРМ поле свободное), и «добавить причину»
-- не должно быть миграцией.
CREATE TABLE IF NOT EXISTS op_funnel_reasons (
    direction_code VARCHAR(24)  NOT NULL,
    work_day       DATE         NOT NULL,
    user_id        INTEGER      NOT NULL,
    bucket         VARCHAR(16)  NOT NULL,   -- nedozvon | otkaz | netsel
    reason_code    VARCHAR(64)  NOT NULL,   -- нормализованный ключ
    reason_title   TEXT         NOT NULL DEFAULT '',
    leads          INTEGER      NOT NULL DEFAULT 0,
    PRIMARY KEY (direction_code, work_day, user_id, bucket, reason_code)
);

CREATE INDEX IF NOT EXISTS idx_op_funnel_reasons_period
    ON op_funnel_reasons (direction_code, work_day DESC, bucket);

-- ── СПРАВОЧНИК ПРИЧИН ──────────────────────────────────────────────────────
-- Пополняется САМ при выгрузке (новая причина заводится строкой), а человек
-- правит подпись, корзину и порядок. Требование ТЗ: «новая причина не должна
-- требовать релиза кода».
--
-- Зачем нужна корзина вручную: СРМ не говорит, отказ это или нецелевой. В
-- amoCRM это зашито в саму причину («Нет авто (не цел)»), а в партнёрских
-- ручках — нет, и «Сброс» это недозвон, а «Работа с ДТ» — отказ.
CREATE TABLE IF NOT EXISTS op_funnel_reason_dict (
    source       VARCHAR(24)  NOT NULL,
    reason_code  VARCHAR(64)  NOT NULL,
    title        TEXT         NOT NULL DEFAULT '',
    bucket       VARCHAR(16)  NOT NULL DEFAULT '',
    sort_order   INTEGER      NOT NULL DEFAULT 1000,
    -- Сырые написания, которые сводятся к этому коду: «не интересно»,
    -- «не интеросно», «неинтересно» — это одна причина и три опечатки.
    aliases      JSONB        NOT NULL DEFAULT '[]'::jsonb,
    is_hidden    BOOLEAN      NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_by   INTEGER,
    updated_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, reason_code)
);

-- ── НОРМЫ, ТАРГЕТЫ И ПОРОГИ ────────────────────────────────────────────────
-- Одна строка = одно число с датой вступления в силу. Пересчёт суток берёт
-- значение, действовавшее В ТЕ СУТКИ, — иначе поднятый сегодня план перепишет
-- выполнение за прошлый месяц.
--
-- Известные метрики (заводятся сидом, дальше правит СВ или глава отдела):
--   reached_per_hour   норма дозвонов в час            Поток: 20
--   agreed_per_hour    норма согласий в час            Поток: 5
--   plan_per_fte       месячный план на ставку 1,0     Поток: 160, Верификатор: 440
--   chats_per_hour     таргет обработки чатов в час    Верификатор: день 16, ночь 12
--   reply_seconds      таргет времени ответа           Верификатор: день 120, ночь 180
--   quality            таргет качества                 Верификатор: 90
--   weight_*           веса показателей                Верификатор: 0,5 / 0,05 / 0,45
--   green_from         с какого % плана красим зелёным
--   amber_from         с какого % плана красим жёлтым
CREATE TABLE IF NOT EXISTS op_funnel_targets (
    direction_code VARCHAR(24)  NOT NULL,
    shift_kind     VARCHAR(8)   NOT NULL DEFAULT 'any',  -- day | night | any
    metric         VARCHAR(32)  NOT NULL,
    effective_from DATE         NOT NULL DEFAULT DATE '2026-01-01',
    value          NUMERIC(10,3) NOT NULL,
    updated_by     INTEGER,
    updated_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (direction_code, shift_kind, metric, effective_from)
);

-- ── РАСХОЖДЕНИЯ ПОСЛЕ ПЕРЕСЧЁТА ────────────────────────────────────────────
-- Что именно изменилось в уже зафиксированных сутках. Пустая таблица — хороший
-- знак; непустая объясняет, почему цифра не та, что называли вчера.
CREATE TABLE IF NOT EXISTS op_funnel_drift (
    id             BIGSERIAL    PRIMARY KEY,
    direction_code VARCHAR(24)  NOT NULL,
    work_day       DATE         NOT NULL,
    user_id        INTEGER      NOT NULL,
    metric         VARCHAR(32)  NOT NULL,
    was            NUMERIC(12,2),
    became         NUMERIC(12,2),
    noticed_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    run_id         BIGINT
);

CREATE INDEX IF NOT EXISTS idx_op_funnel_drift_day
    ON op_funnel_drift (direction_code, work_day DESC, noticed_at DESC);

-- ── ЖУРНАЛ ВЫГРУЗОК ────────────────────────────────────────────────────────
-- Зачем: у источника четыре разных отказа (401 на отозванном токене, HTML с
-- кодом 200 у Laravel, обрыв посреди пагинации у amoCRM, 422 на кривом периоде),
-- и «почему цифры не обновились» должно отвечаться без чтения логов Render.
CREATE TABLE IF NOT EXISTS op_funnel_sync_runs (
    id             BIGSERIAL    PRIMARY KEY,
    direction_code VARCHAR(24)  NOT NULL,
    source         VARCHAR(24)  NOT NULL,
    period_from    DATE,
    period_to      DATE,
    started_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMP,
    status         VARCHAR(16)  NOT NULL DEFAULT 'running',  -- running|ok|error
    leads_seen     INTEGER      NOT NULL DEFAULT 0,
    leads_written  INTEGER      NOT NULL DEFAULT 0,
    days_frozen    INTEGER      NOT NULL DEFAULT 0,
    days_redone    INTEGER      NOT NULL DEFAULT 0,
    unmapped       INTEGER      NOT NULL DEFAULT 0,
    drift_rows     INTEGER      NOT NULL DEFAULT 0,
    error          TEXT,
    started_by     INTEGER,
    note           TEXT         NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_op_funnel_runs_fresh
    ON op_funnel_sync_runs (direction_code, started_at DESC);

-- ── РУЧНАЯ ВЫГРУЗКА ────────────────────────────────────────────────────────
-- Тикеты и время их обработки у Верификаторов взять из API нельзя: в СРМ
-- заказчика список обращений живёт под админской учёткой
-- (POST /api/admin/list-requests/list), и даже там нет времени обработки —
-- только created_at/updated_at. Проверено по свагеру 11.09.2026: партнёрской
-- ручки обращений нет вовсе, а /api/tickets — это лотерейные билеты за самокаты.
--
-- Поэтому супервайзер грузит тот же файл, который делает сегодня руками. Строка
-- за сутки перезаписывается целиком: повторная загрузка исправляет, а не
-- удваивает.
CREATE TABLE IF NOT EXISTS op_funnel_manual_rows (
    direction_code VARCHAR(24)  NOT NULL,
    work_day       DATE         NOT NULL,
    user_id        INTEGER      NOT NULL,
    work_hours     NUMERIC(6,2),
    chats          INTEGER,
    tickets        INTEGER,
    chat_reply_seconds    NUMERIC(8,1),
    ticket_handle_seconds NUMERIC(8,1),
    sales          INTEGER,
    import_id      BIGINT,
    updated_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (direction_code, work_day, user_id)
);

CREATE TABLE IF NOT EXISTS op_funnel_manual_imports (
    id             BIGSERIAL    PRIMARY KEY,
    direction_code VARCHAR(24)  NOT NULL,
    file_name      TEXT         NOT NULL DEFAULT '',
    period_from    DATE,
    period_to      DATE,
    rows_read      INTEGER      NOT NULL DEFAULT 0,
    rows_written   INTEGER      NOT NULL DEFAULT 0,
    rows_skipped   INTEGER      NOT NULL DEFAULT 0,
    unmapped       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    uploaded_by    INTEGER,
    uploaded_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    note           TEXT         NOT NULL DEFAULT ''
);
"""

# Нормы и таргеты, снятые с рабочих файлов супервайзеров (задачи #301, #303, #305)
# и сверенные с калькуляторами зарплат (src/utils/salaryFormula.js). Сид ставится
# один раз: дальше это данные, и правит их человек в разделе.
#
# Кортеж: (направление, смена, метрика, значение).
DEFAULT_TARGETS = (
    # «Поток»: лист «Общий» файла задачи #303 — нормы в час и месячный план на FTE.
    ('op_potok', 'any', 'reached_per_hour', 20),
    ('op_potok', 'any', 'agreed_per_hour', 5),
    ('op_potok', 'any', 'plan_per_fte', 160),
    ('op_potok', 'any', 'green_from', 100),
    ('op_potok', 'any', 'amber_from', 80),

    # «Яндекс Регистрация»: норм в час у направления нет, план держится
    # конверсией (YANDEX_REG_TARGET_CONVERSION = 0,5 в salaryFormula.js).
    ('op_yandex_reg', 'any', 'target_conversion', 0.5),
    ('op_yandex_reg', 'any', 'green_from', 100),
    ('op_yandex_reg', 'any', 'amber_from', 80),

    # «Основа ОП»: норма часов на ставку 1,0 — из OSNOVA_NORM_HOURS_FTE.
    # ПЛАН на ставку намеренно не засеян: его нет и в калькуляторе зарплат
    # (`planPerFte = 0`, значение вносит руководитель). Выдумать сюда число
    # значило бы показать людям план, которого им никто не ставил, — пусть лучше
    # «% плана» честно стоит прочерком, пока глава отдела не введёт значение.
    ('op_osnova', 'any', 'norm_hours_fte', 176),
    ('op_osnova', 'any', 'green_from', 100),
    ('op_osnova', 'any', 'amber_from', 80),

    # «Верификатор»: таргеты и веса с листа «Воронка» файла задачи #305.
    ('op_verificator', 'day', 'chats_per_hour', 16),
    ('op_verificator', 'night', 'chats_per_hour', 12),
    ('op_verificator', 'day', 'reply_seconds', 120),
    ('op_verificator', 'night', 'reply_seconds', 180),
    ('op_verificator', 'any', 'quality', 90),
    ('op_verificator', 'any', 'weight_reply', 0.5),
    ('op_verificator', 'any', 'weight_chats', 0.05),
    ('op_verificator', 'any', 'weight_quality', 0.45),
    ('op_verificator', 'any', 'plan_per_fte', 440),
    ('op_verificator', 'night', 'plan_per_fte', 220),
    ('op_verificator', 'any', 'norm_hours_fte', 176),
    ('op_verificator', 'any', 'green_from', 100),
    ('op_verificator', 'any', 'amber_from', 80),
)

SEED_TARGETS_SQL = """
INSERT INTO op_funnel_targets (direction_code, shift_kind, metric, effective_from, value)
VALUES (%s, %s, %s, DATE '2026-01-01', %s)
ON CONFLICT (direction_code, shift_kind, metric, effective_from) DO NOTHING
"""

# Проверка «схема развернулась». Роут возвращает 503 с внятным текстом, если
# таблиц нет: молчаливый 500 на пустой базе стоил бы вечера разбирательств.
SCHEMA_READY_SQL = """
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN (
    'op_funnel_operator_map', 'op_funnel_leads', 'op_funnel_daily',
    'op_funnel_reasons', 'op_funnel_reason_dict', 'op_funnel_targets',
    'op_funnel_drift', 'op_funnel_sync_runs', 'op_funnel_manual_rows',
    'op_funnel_manual_imports'
)
"""

SCHEMA_TABLES_COUNT = 10


def init_op_funnel_schema(cursor):
    """Развернуть схему и засеять нормы. Идемпотентно, зовётся при каждом старте."""
    cursor.execute(OP_FUNNEL_SCHEMA_SQL)
    for direction_code, shift_kind, metric, value in DEFAULT_TARGETS:
        cursor.execute(SEED_TARGETS_SQL, (direction_code, shift_kind, metric, value))


def schema_is_ready(cursor):
    """Все ли таблицы на месте. Дешевле, чем ловить UndefinedTable на каждом роуте."""
    cursor.execute(SCHEMA_READY_SQL)
    row = cursor.fetchone()
    count = (row[0] if not isinstance(row, dict) else list(row.values())[0]) if row else 0
    return int(count or 0) >= SCHEMA_TABLES_COUNT
