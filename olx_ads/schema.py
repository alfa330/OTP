# -*- coding: utf-8 -*-
"""Схема раздела «Объявления OLX»: снимок объявлений, история, прогоны, брифы, черновики.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Разворачивается один раз при
старте из Database._init_db через init_olx_ads_schema(cursor).

Ключевые решения, которые видны в DDL
-------------------------------------

* **Снимок объявлений хранится целиком, в `payload`.** У Partner API нет PATCH:
  `PUT` — полная замена объекта, и чтобы поменять два поля, надо вернуть все
  остальные ровно теми же значениями. Держать разобранными двадцать полей
  объявления значит гарантированно потерять то, которое мы не предусмотрели, —
  поэтому рядом с разобранными колонками (по ним ищут и фильтруют) лежит весь
  объект как есть. Перед каждой записью снимок всё равно обновляется из OLX:
  правим то, что есть сейчас, а не то, что лежало в кеше со вчера.

* **История — полный текст «до» и «после», а не разница.** Требование владельца
  14.09.2026: «обязательно нужна история правок». Хранить разницу дешевле, но
  по ней нельзя ни показать прежний текст целиком, ни откатить правку, а откат
  здесь — единственный способ исправить неудачный прогон по сотням объявлений.
  Текст объявления это ~1 КБ; 281 объявление на 12 прогонов в год — 3,4 МБ.

* **История пишется и на НЕУДАЧУ.** Строка с `result='failed'` и текстом ошибки
  OLX — это не мусор, а единственное место, где видно, что правка не доехала:
  в самом OLX не осталось ничего. Без этого «применили 281, в кабинете 279»
  не расследуется.

* **Черновик ИИ — отдельная таблица, а не поле у объявления.** Сочинённый текст
  живёт до применения и может быть выброшен; смешав его со снимком, мы получили
  бы объявление, которое в портале выглядит не так, как в OLX. Частичный
  уникальный индекс держит не больше одного ЖИВОГО черновика на объявление:
  два конкурирующих варианта в очереди на применение — это вопрос «какой из них
  настоящий», на который некому ответить.

* **Бриф месяца — строка, а не файл, и у него СВОЙ список кабинетов.** ТЗ требует,
  чтобы у текста был источник: оффер, бонус, акция, розыгрыш, комиссия, доход.
  Поля именно такие, как в разделе 4 ТЗ. Без действующего брифа ИИ по кабинету
  не запускается — это и есть «без утверждённого плана робот не стартует» из
  раздела 6.

  Решение владельца 14.09.2026: «разный бриф на разные кабинеты», с мультивыбором
  кабинетов при написании. Отсюда таблица `olx_ads_brief_cabinets` и главное
  правило, которое держит база, а не код: **у кабинета одновременно действует не
  больше одного брифа** (частичный уникальный индекс по кабинету среди
  действующих строк). Два действующих брифа на один кабинет — это вопрос «из
  какого из них ИИ брал текст», на который по объявлению уже не ответить.

  Кто прав при пересечении — решает порядок: кабинет забирает бриф, который
  включили (или сохранили включённым) последним, а у прежнего брифа этот кабинет
  снимается. Бриф, у которого не осталось ни одного кабинета, выключается сам.

  `is_active` на строке связи — осознанный дубль флага брифа: без него
  уникальность «один действующий бриф на кабинет» не выразить индексом, а
  проверка в коде между двумя одновременными нажатиями не спасает. Держат флаги
  согласованными функции `queries.activate_brief` / `set_brief_cabinets` /
  `deactivate_brief`, и только они.

* **`olx_ads_runs` существует ради возобновляемости.** Прогон по 281 объявлению
  идёт минуты и может оборваться. Строка прогона со счётчиками отвечает на
  вопрос «где остановились», а история — на вопрос «что уже сделано», поэтому
  повтор не трогает применённое второй раз.
"""

from olx_amo import cabinets as _cabinets

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Чем закончилась правка объявления. Коды в базе, подписи — на фронте.
HISTORY_RESULTS = (
    'applied',      # OLX принял новый текст
    'failed',       # OLX отказал; в OLX ничего не изменилось
    'rolled_back',  # вернули прежний текст этой же механикой
)

# Откуда взялся текст. Нужно, чтобы отличать работу ИИ от работы человека в
# отчётности: «сколько текстов написал ИИ» — первый вопрос, который зададут.
HISTORY_ORIGINS = ('human', 'ai')

# Как применяли: поштучно из карточки или пачкой из списка.
HISTORY_SOURCES = ('single', 'bulk', 'rollback')

DRAFT_STATUSES = ('draft', 'applied', 'discarded')

RUN_STATUSES = ('running', 'done', 'failed')


def _all_cabinet_codes_sql():
    """Коды кабинетов литералом массива — для переноса старого брифа на все кабинеты.

    Коды берутся из справочника в коде (ascii-идентификаторы вида 'tenge'), а не
    из ввода человека, поэтому подстановка литералом безопасна.
    """
    return "ARRAY[%s]::varchar[]" % ', '.join(
        "'%s'" % cab.code for cab in _cabinets.CABINETS)


_STATEMENTS = [

    # ──────────────────────────────────────────────────────────────────────
    # СНИМОК ОБЪЯВЛЕНИЙ
    #
    # Что сейчас в OLX. Разобранные колонки — для фильтров и поиска в разделе,
    # `payload` — чтобы собрать тело PUT без потери полей.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_ads_adverts (
        cabinet_code   VARCHAR(32) NOT NULL,
        advert_id      VARCHAR(64) NOT NULL,

        status         VARCHAR(32),
        title          TEXT,
        description    TEXT,
        url            TEXT,

        category_id    INTEGER,
        category_name  VARCHAR(160),
        city_id        INTEGER,
        city_name      VARCHAR(160),
        company_name   VARCHAR(200),
        phone          VARCHAR(32),

        activated_at   TIMESTAMP,
        valid_to       TIMESTAMP,
        auto_extend    BOOLEAN,

        -- Весь объект как его отдал OLX. Основа read-modify-write.
        payload        JSONB,

        synced_at      TIMESTAMP NOT NULL DEFAULT %(now)s,

        PRIMARY KEY (cabinet_code, advert_id)
    )
    """ % {'now': _NOW},

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_adverts_cabinet "
    "ON olx_ads_adverts (cabinet_code, status)",

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_adverts_city "
    "ON olx_ads_adverts (city_id)",

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_adverts_category "
    "ON olx_ads_adverts (category_id)",

    # ──────────────────────────────────────────────────────────────────────
    # ПРОГОН
    #
    # Одно нажатие «Применить» — одна строка. Поштучная правка тоже прогон
    # (kind='single'), чтобы история была однородной и не имела двух видов
    # родителя.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_ads_runs (
        id             SERIAL PRIMARY KEY,
        kind           VARCHAR(16) NOT NULL DEFAULT 'bulk',
        status         VARCHAR(16) NOT NULL DEFAULT 'running',

        actor_id       INTEGER,
        actor_name     VARCHAR(200),

        total          INTEGER NOT NULL DEFAULT 0,
        applied        INTEGER NOT NULL DEFAULT 0,
        failed         INTEGER NOT NULL DEFAULT 0,

        started_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        finished_at    TIMESTAMP,
        error_text     TEXT
    )
    """ % {'now': _NOW},

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_runs_started "
    "ON olx_ads_runs (started_at DESC)",

    # ──────────────────────────────────────────────────────────────────────
    # ИСТОРИЯ ПРАВОК
    #
    # Требование владельца: история обязательна. Поэтому она не право и не
    # настройка — строка пишется на КАЖДУЮ попытку записи в OLX, удачную и нет.
    # Полный текст «до» и «после» держим намеренно: по нему и показывается
    # разница, и делается откат.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_ads_history (
        id              SERIAL PRIMARY KEY,
        run_id          INTEGER,

        cabinet_code    VARCHAR(32) NOT NULL,
        advert_id       VARCHAR(64) NOT NULL,
        advert_url      TEXT,

        actor_id        INTEGER,
        actor_name      VARCHAR(200),

        source          VARCHAR(16) NOT NULL DEFAULT 'single',
        origin          VARCHAR(16) NOT NULL DEFAULT 'human',

        old_title       TEXT,
        new_title       TEXT,
        old_description TEXT,
        new_description TEXT,

        result          VARCHAR(16) NOT NULL,
        error_text      TEXT,

        created_at      TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_history_advert "
    "ON olx_ads_history (cabinet_code, advert_id, created_at DESC)",

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_history_created "
    "ON olx_ads_history (created_at DESC)",

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_history_run "
    "ON olx_ads_history (run_id)",

    # ──────────────────────────────────────────────────────────────────────
    # БРИФ МЕСЯЦА
    #
    # Вводные для ИИ: что именно предлагаем в этом месяце. Поля — по разделу 4
    # ТЗ. `is_active` — «бриф включён»; ДЛЯ КАКИХ кабинетов — в таблице ниже.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_ads_briefs (
        id            SERIAL PRIMARY KEY,
        title         VARCHAR(200) NOT NULL,

        offer         TEXT,
        bonus         TEXT,
        promo         TEXT,
        raffle        TEXT,
        commission    TEXT,
        income        TEXT,
        extra         TEXT,

        is_active     BOOLEAN NOT NULL DEFAULT FALSE,

        created_by    INTEGER,
        created_by_name VARCHAR(200),
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    )
    """ % {'now': _NOW},

    # ──────────────────────────────────────────────────────────────────────
    # КАБИНЕТЫ БРИФА
    #
    # Строка = «этот бриф написан для этого кабинета». `is_active` повторяет флаг
    # брифа ради индекса ниже: у кабинета действует не больше одного брифа.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_ads_brief_cabinets (
        brief_id      INTEGER NOT NULL REFERENCES olx_ads_briefs (id) ON DELETE CASCADE,
        cabinet_code  VARCHAR(32) NOT NULL,
        is_active     BOOLEAN NOT NULL DEFAULT FALSE,

        PRIMARY KEY (brief_id, cabinet_code)
    )
    """,

    # Главное правило раздела брифов: у кабинета один действующий бриф. Держит
    # база — два одновременных «Включить» по одному кабинету не пройдут оба.
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_olx_ads_brief_cabinet_active "
    "ON olx_ads_brief_cabinets (cabinet_code) WHERE is_active",

    # ──────────────────────────────────────────────────────────────────────
    # ЧЕРНОВИК ТЕКСТА
    #
    # Что ИИ сочинил для конкретного объявления. Живёт до применения.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS olx_ads_drafts (
        id             SERIAL PRIMARY KEY,
        cabinet_code   VARCHAR(32) NOT NULL,
        advert_id      VARCHAR(64) NOT NULL,
        brief_id       INTEGER,

        title          TEXT NOT NULL,
        description    TEXT NOT NULL,

        model          VARCHAR(160),
        origin         VARCHAR(16) NOT NULL DEFAULT 'ai',
        status         VARCHAR(16) NOT NULL DEFAULT 'draft',

        created_by     INTEGER,
        created_by_name VARCHAR(200),
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        applied_at     TIMESTAMP
    )
    """ % {'now': _NOW},

    # Живой черновик на объявление ровно один.
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_olx_ads_draft_live "
    "ON olx_ads_drafts (cabinet_code, advert_id) WHERE status = 'draft'",

    "CREATE INDEX IF NOT EXISTS idx_olx_ads_drafts_status "
    "ON olx_ads_drafts (status, created_at DESC)",
]

_MIGRATIONS = [
    # 14.09.2026: «разный бриф на разные кабинеты». Первая версия держала ОДИН
    # действующий бриф на всё уникальным индексом по is_active — он запрещал бы
    # второй действующий бриф на другом кабинете. Индекс снимаем; правило
    # «один действующий бриф» переехало на уровень кабинета
    # (uniq_olx_ads_brief_cabinet_active).
    "DROP INDEX IF EXISTS uniq_olx_ads_brief_active",

    # Бриф, включённый до появления кабинетов, действовал на ВСЕ кабинеты — так и
    # переносим, чтобы смена схемы не выключила ИИ молча. Только для брифов, у
    # которых кабинетов ещё нет, поэтому повторный старт ничего не меняет.
    """
    INSERT INTO olx_ads_brief_cabinets (brief_id, cabinet_code, is_active)
    SELECT b.id, codes.code, TRUE
      FROM olx_ads_briefs b
     CROSS JOIN unnest(%s) AS codes(code)
     WHERE b.is_active
       AND NOT EXISTS (SELECT 1 FROM olx_ads_brief_cabinets c WHERE c.brief_id = b.id)
    ON CONFLICT (brief_id, cabinet_code) DO NOTHING
    """ % (_all_cabinet_codes_sql(),),
]


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_olx_ads_schema(cursor):
    """Разворачивает схему раздела. Курсор из _init_db, транзакцией правит вызывающий.

    Порядок тот же, что у соседних разделов: сначала таблицы, потом ALTER'ы, и
    только потом индексы — иначе индекс по новому столбцу создаётся раньше
    самого столбца и падает, а падение откатывает весь разворот схемы. Здесь это
    ещё и порядок миграции брифов: старый глобальный индекс снимается и старый
    бриф переносится на кабинеты ДО того, как встанет новый индекс по кабинетам.
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
    """Развёрнута ли схема. Отличает «раздел ещё не поднялся» от «объявлений нет»."""
    cursor.execute("SELECT to_regclass('public.olx_ads_adverts') IS NOT NULL")
    row = cursor.fetchone()
    if not row:
        return False
    return bool(row[0] if not isinstance(row, dict) else list(row.values())[0])
