# -*- coding: utf-8 -*-
"""Схема раздела «Касания». Таблицы cdr_touches, cdr_sync_days, cdr_operators,
cdr_agent_state.

Идемпотентно (CREATE TABLE / INDEX IF NOT EXISTS), разворачивается один раз при
старте из Database._init_db через init_cdr_schema(cursor).

Откуда берутся данные
---------------------
Станция стоит в корпоративной сети и наружу не выведена: 25.08.2026 её вывели
через прокси с basic-auth, в тот же день сервис лёг, и доступ закрыли. Поэтому
портал к станции НЕ ХОДИТ ВООБЩЕ. Внутри сети работает наш агент (`cdr_bridge/`),
он читает CDR, склеивает касания на месте и отдаёт их сюда по токену — тем же
приёмом, что «Ограничитель Перезвона» и iCORE Phone.

Следствие для схемы: `cdr_sync_days` — это НЕ журнал наших походов на станцию, а
**очередь заданий**. Человек в разделе просит период, недостающие сутки ложатся
сюда как `pending`, агент их разбирает и присылает результат. Направление
перевёрнуто, а таблица та же.

Зачем хранить у себя то, что есть на станции
--------------------------------------------
Сутки отдела продаж — это ~23 тысячи строк CDR, месяц — 600 тысяч. Через
интернет столько гонять незачем: агент склеивает их на месте в 3,4 тысячи
касаний в сутки (замерено на 24.08.2026) и присылает уже результат — в десять
раз меньше. Плюс раздел работает, даже когда корп-сеть недоступна: он читает
свою базу, а не чужую станцию.

Почему единица — сутки
----------------------
Сутки — естественная единица выкачки, естественная единица «готово / не готово»
и естественная единица прогресса: «сутки 12 из 31». Перезапуск агента или
портала теряет одни сутки, а не весь период.

**Ловушка полуночи.** Звонок, начавшийся в 23:59 и закончившийся в 00:03,
попадает плечами в двое суток. Если читать сутки строго по их границам, такой
звонок распадётся на два обрубка. Поэтому агент читает сутки с ЧАСОВЫМ хвостом
следующих, а присылает только касания, НАЧАВШИЕСЯ в эти сутки.

Что НЕ хранится
---------------
ФИО оператора. На касании лежит только внутренний номер, а имя подставляется при
чтении из `cdr_operators` — с учётом периода владения номером. Иначе правка
справочника (человека переименовали, номер уволившегося разобрали) не доезжала бы
до уже сохранённых касаний, и один и тот же период выгружался бы по-разному в
зависимости от того, когда его первый раз открыли.
"""

# Сколько суток держим. Это кэш: понадобится — агент пришлёт заново. Но расти
# бесконечно он не должен, при ежедневном использовании это ~3,4 тыс. строк/сут.
RETENTION_DAYS = 730

CDR_SCHEMA_SQL = """
-- ── КАСАНИЯ ────────────────────────────────────────────────────────────────
-- Одна строка = один звонок (группа плеч CDR с общим linkedid).
--
-- Ключ составной: у одного linkedid бывает два клиентских номера (перевод,
-- конференция), и это честно два касания, а не одно.
CREATE TABLE IF NOT EXISTS cdr_touches (
    linkedid      VARCHAR(64)  NOT NULL,
    phone         VARCHAR(16)  NOT NULL,

    -- Сутки начала вызова. Отдельной колонкой, а не выражением по started_at:
    -- по ним идут и выборка периода, и перезапись при повторной присылке.
    call_day      DATE         NOT NULL,

    -- Время местное (Алматы, UTC+5) — как его отдаёт станция и как его читает
    -- человек. Часовые пояса CRM и телефонии совпадают, проверено на 2863
    -- лидах amoCRM: медиана расхождения −2 с.
    started_at    TIMESTAMP    NOT NULL,
    -- Момент, когда сняли трубку. NULL, если он совпадает с началом вызова: у
    -- входящего через очередь между этим медиана 16 секунд, а бывает и 11 минут
    -- ожидания, и путать их нельзя.
    answered_at   TIMESTAMP,

    ext           VARCHAR(8)   NOT NULL DEFAULT '',
    call_type     VARCHAR(32)  NOT NULL,
    result        VARCHAR(32)  NOT NULL,

    -- Разговор — billsec плеча САМОГО АГЕНТА, без ожидания в очереди.
    talk_seconds  INTEGER      NOT NULL DEFAULT 0,
    dial_seconds  INTEGER      NOT NULL DEFAULT 0,

    queue         VARCHAR(64)  NOT NULL DEFAULT '',
    recording_url TEXT,
    legs          SMALLINT     NOT NULL DEFAULT 1,

    -- ── Точные факты из журнала очередей станции (`queuelog`) ──────────────
    -- Заполняются мостом с 21.09.2026, когда у нас появилась учётка на чтение
    -- базы АТС. NULL значит «станция не сказала»: до этого ожидание выводилось
    -- из длины автоинформатора, и аудит 17.09.2026 намерил у такого расчёта
    -- 3–5 п.п. лишнего SL. Нулём заменять нельзя — ноль это «ответили сразу».
    queued_at             TIMESTAMP,   -- вход в очередь (ENTERQUEUE)
    wait_seconds          INTEGER,     -- ожидание до ответа ИЛИ до отказа клиента
    talk_measured_seconds INTEGER,     -- разговор без ожидания (COMPLETE*)
    hangup_side           VARCHAR(16) NOT NULL DEFAULT '',  -- client | operator | ''

    -- Наш номер, десять цифр: входящему его набрал клиент, исходящему с него
    -- позвонили. Парк по нему выводится при чтении (cdr/lines.py), а не хранится:
    -- у парка десятки номеров, и подпись поправится без пересборки касаний.
    -- Пусто — станция номера не назвала (или сутки собраны мостом до 1.3.0).
    line_number   VARCHAR(16)  NOT NULL DEFAULT '',

    PRIMARY KEY (linkedid, phone)
);

CREATE INDEX IF NOT EXISTS idx_cdr_touches_started ON cdr_touches(started_at);
CREATE INDEX IF NOT EXISTS idx_cdr_touches_day ON cdr_touches(call_day);
CREATE INDEX IF NOT EXISTS idx_cdr_touches_ext ON cdr_touches(ext);
CREATE INDEX IF NOT EXISTS idx_cdr_touches_phone ON cdr_touches(phone);

-- ── ОЧЕРЕДЬ СУТОК ──────────────────────────────────────────────────────────
-- Она же карточка прогресса: отдельной таблицы заданий у раздела нет.
--
-- pending  — сутки нужны, агент их ещё не забрал
-- running  — агент взял в работу (claimed_by/claimed_at)
-- done     — касания присланы; complete = сутки на тот момент уже закончились
-- error    — агент не смог; текст в error, человек видит его в разделе
--
-- Брошенное `running` (агент умер или VM перезагрузили) подбирается по возрасту
-- claimed_at: подхвата с контрольной точкой не нужно, сутки читаются секунды.
CREATE TABLE IF NOT EXISTS cdr_sync_days (
    day          DATE PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'done', 'error')),
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    touches      INTEGER NOT NULL DEFAULT 0,
    -- Сутки уже закончились на момент чтения. Сегодняшние и незакрытые сутки
    -- ставятся в очередь заново при каждом обращении: в них дописываются звонки.
    complete     BOOLEAN NOT NULL DEFAULT FALSE,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requested_by INTEGER,
    claimed_at   TIMESTAMPTZ,
    claimed_by   TEXT,
    finished_at  TIMESTAMPTZ,
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_cdr_sync_days_status ON cdr_sync_days(status);

-- ── СПРАВОЧНИК ВНУТРЕННИХ НОМЕРОВ ──────────────────────────────────────────
-- periods — список {since, name, direction}: у переиспользованного номера два
-- владельца, и звонок подписывается тем, кто владел номером в тот день.
--
-- Собирается из НАШЕЙ базы (users/operator_profiles) и из справочника агентов
-- станции, который присылает агент: только станция знает, кто владеет номером
-- сейчас — у нас номер уволившегося остаётся висеть на нём же.
CREATE TABLE IF NOT EXISTS cdr_operators (
    ext        VARCHAR(8) PRIMARY KEY,
    periods    JSONB NOT NULL,
    station    TEXT,
    source     TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── СОСТОЯНИЕ МОСТА ────────────────────────────────────────────────────────
-- Одна строка. Нужна ради честной плашки в разделе: без неё «нет данных за
-- вчера» и «мост умер неделю назад» выглядят одинаково.
--
-- Сырой справочник агентов станции лежит здесь же (station_agents): портал сам
-- к станции не ходит, а пересобирать справочник надо и в те моменты, когда
-- агент молчит.
CREATE TABLE IF NOT EXISTS cdr_agent_state (
    id             INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_seen_at   TIMESTAMPTZ,
    hostname       TEXT,
    version        TEXT,
    station_url    TEXT,
    last_error     TEXT,
    last_error_at  TIMESTAMPTZ,
    days_sent      INTEGER NOT NULL DEFAULT 0,
    rows_read      BIGINT  NOT NULL DEFAULT 0,
    station_agents JSONB,
    agents_at      TIMESTAMPTZ,
    -- Когда последний раз подчищали кэш старше срока хранения. Без отметки
    -- уборка либо не запускалась бы вовсе, либо шла на каждый опрос моста.
    cleaned_at     TIMESTAMPTZ,
    -- Идентификатор ключа, которым мост подписал последний запрос. Нужен при
    -- ротации: старый ключ убирают с портала только когда здесь уже виден новый.
    agent_key      TEXT
);

-- ── ЗАПИСИ РАЗГОВОРОВ ДЛЯ ЖУРНАЛА ОЦЕНОК ───────────────────────────────────
-- Файлы записей лежат на сервере внутри корпоративной сети, и портал до него не
-- дотягивается. Поэтому запись, нужная журналу («Случайный звонок» отдела продаж,
-- деление звонков), ЗАКАЗЫВАЕТСЯ здесь: мост забирает заказ вместе с сутками,
-- скачивает файл через свой прокси записей и присылает порталу, а портал кладёт
-- его в облако и проставляет imported_calls.audio_path. Та же очередь, что у суток:
-- pending → running (claimed_at/claimed_by) → done | error | missing.
-- missing — файла на сервере нет (404): повторами он не появится, финал сразу.
CREATE TABLE IF NOT EXISTS cdr_audio_jobs (
    id               BIGSERIAL PRIMARY KEY,
    linkedid         VARCHAR(64) NOT NULL,
    recording_url    TEXT NOT NULL,
    imported_call_id INTEGER,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'done', 'error', 'missing')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requested_by     INTEGER,
    claimed_at       TIMESTAMPTZ,
    claimed_by       TEXT,
    finished_at      TIMESTAMPTZ,
    error            TEXT,
    audio_path       TEXT,
    audio_bytes      INTEGER
);

-- Одна строка пула — один заказ: повторный клик по тому же звонку не плодит дублей.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cdr_audio_jobs_imported_call
    ON cdr_audio_jobs(imported_call_id) WHERE imported_call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cdr_audio_jobs_status ON cdr_audio_jobs(status);
"""

# Таблица могла быть создана до появления колонки: CREATE TABLE IF NOT EXISTS
# существующую не тронет, поэтому колонку добавляем отдельным идемпотентным шагом.
CDR_SCHEMA_MIGRATIONS = (
    "ALTER TABLE cdr_agent_state ADD COLUMN IF NOT EXISTS agent_key TEXT",
    # Когда мост в последний раз прислал ЖИВОЕ приращение сегодняшних суток. Табло по нему
    # отличает «данные свежие» от «мост на связи, но сегодняшний хвост давно не обновлялся».
    "ALTER TABLE cdr_agent_state ADD COLUMN IF NOT EXISTS live_at TIMESTAMPTZ",
    # Точные факты очереди со станции (21.09.2026). На боевой базе таблица создана давно,
    # поэтому колонки добавляются отдельно; заполняет их мост, старые касания остаются
    # с NULL и считаются по-прежнему.
    "ALTER TABLE cdr_touches ADD COLUMN IF NOT EXISTS queued_at TIMESTAMP",
    "ALTER TABLE cdr_touches ADD COLUMN IF NOT EXISTS wait_seconds INTEGER",
    "ALTER TABLE cdr_touches ADD COLUMN IF NOT EXISTS talk_measured_seconds INTEGER",
    "ALTER TABLE cdr_touches ADD COLUMN IF NOT EXISTS hangup_side VARCHAR(16) NOT NULL DEFAULT ''",
    # Номер линии таксопарка (24.09.2026). Постоянный DEFAULT — колонка добавляется без
    # переписывания таблицы; старые касания получают его при перечитке суток мостом.
    "ALTER TABLE cdr_touches ADD COLUMN IF NOT EXISTS line_number VARCHAR(16) NOT NULL DEFAULT ''",
)


def init_cdr_schema(cursor):
    """Создаёт таблицы раздела. Курсор приходит снаружи: схема всех разделов
    разворачивается одной транзакцией."""
    cursor.execute(CDR_SCHEMA_SQL)
    for statement in CDR_SCHEMA_MIGRATIONS:
        cursor.execute(statement)


def schema_is_ready(cursor):
    """Есть ли таблицы раздела. Нужна роутам: если разворот схемы на старте упал
    под своим SAVEPOINT, раздел должен сказать это словами, а не падать
    пятисоткой из-под первого SELECT."""
    cursor.execute("SELECT to_regclass('public.cdr_touches')")
    row = cursor.fetchone()
    return bool(row and row[0])
