# -*- coding: utf-8 -*-
"""Схема модуля «Маркетинговый мониторинг» (ТЗ #317). Таблицы qa_marketing_*.

Идемпотентно, разворачивается при старте из `Database._init_db` под SAVEPOINT —
как соседние разделы. Отказ схемы не имеет права уронить «ИИ-оценку» целиком:
без этих таблиц раздел работает как раньше, просто без маркетингового отбора.

Зачем вообще таблицы, если все данные уже есть
----------------------------------------------
Есть, но в двух разных мирах. Слева — разговор («ИИ-оценка»: `ai_review_cache`
поверх пяти видов субъекта), справа — сделка (`op_funnel_leads` из amoCRM).
Общего ключа у них нет: сделка не знает про звонок, звонок не знает про сделку.
Единственный мост — телефон, и мост этот НЕ однозначный: один водитель оставляет
заявку не раз, у 20% сделок «Основы» номер повторяется. Значит связь надо
вычислить (окнами по времени, см. `linker.py`) и ЗАПОМНИТЬ — иначе каждый показ
списка пересчитывал бы её заново по всей истории.

Чего здесь СОЗНАТЕЛЬНО нет
--------------------------
Копии атрибутов сделки. Этап, причина отказа и ответственный берутся живым
JOIN'ом к `op_funnel_leads`, а не переписываются сюда при связывании. Причина
простая: сделка меняется (в этом весь смысл ФТ-08), и снимок, снятый в момент
связывания, назавтра врал бы — причём молча, без единого признака снаружи.
Хранится ровно то, что не меняется: КАКАЯ это сделка и КОГДА был разговор.

Словарь вместо колонок
----------------------
Парк и канал нормализуются не на записи, а на чтении — join'ом к
`qa_marketing_dict`. Так правка словаря («Ноль такси» — это тоже NolTaxi»)
действует немедленно и на всю историю, без перелинковки и без релиза. Ровно тот
же приём, что у `op_funnel_reason_dict` в «Воронке ОП», и то же требование ТЗ:
«значения подтягиваются из справочника, не хардкодятся».
"""

QA_MARKETING_SCHEMA_SQL = """
-- ── СВЯЗЬ РАЗГОВОРА СО СДЕЛКОЙ ─────────────────────────────────────────────
-- Одна строка = один субъект оценки, у которого нашлась сделка. Субъекта без
-- сделки здесь НЕТ вовсе: строка-пустышка ничего не добавила бы к LEFT JOIN,
-- зато удваивала бы таблицу.
CREATE TABLE IF NOT EXISTS qa_subject_deals (
    subject_kind   VARCHAR(24)  NOT NULL,
    call_id        BIGINT       NOT NULL,

    -- Ключ сделки — ровно тот, что в op_funnel_leads (PRIMARY KEY из четырёх
    -- полей). Внешнего ключа нет намеренно: лиды живут 400 суток и подчищаются,
    -- а связь пережившая сделку честнее, чем молча удалённая строка разбора.
    direction_code VARCHAR(24)  NOT NULL,
    source         VARCHAR(24)  NOT NULL,
    stream_type    SMALLINT     NOT NULL DEFAULT 0,
    lead_key       VARCHAR(64)  NOT NULL,

    -- Момент разговора. Дублируется сюда из субъекта, потому что по нему идёт
    -- выборка этапа из журнала (`op_funnel_lead_stages`), а доставать его на
    -- каждый ряд из COALESCE по пяти таблицам — это тот же JOIN ещё раз.
    happened_at    TIMESTAMP,

    -- По какому из телефонов сделки сошлось. Нужно не для выборки, а для
    -- разбирательств «почему этот звонок приписан этой заявке».
    matched_phone  VARCHAR(16)  NOT NULL DEFAULT '',

    -- Сколько сделок делят этот номер и какая из них по счёту. Показывается в
    -- карточке: связь по телефону у повторной заявки всегда спорна, и человек
    -- должен видеть, что она не единственная, а не верить ей вслепую.
    dup_total      SMALLINT     NOT NULL DEFAULT 1,
    dup_index      SMALLINT     NOT NULL DEFAULT 1,

    linked_at      TIMESTAMP    NOT NULL DEFAULT NOW(),

    PRIMARY KEY (subject_kind, call_id)
);

CREATE INDEX IF NOT EXISTS idx_qa_subject_deals_lead
    ON qa_subject_deals (source, lead_key);

-- Какой редакцией правил посчитана связь (linker.LINK_RULES_VERSION). Связь
-- старой редакции связыватель пересчитывает сам при ближайшем прогоне: так
-- исправление правила доезжает до уже связанных разговоров без ручного
-- «пересчитать всё». Редакция 2 (24.09.2026) — момент звонка из АТС и
-- загруженного звонка больше не сдвигается на пять часов.
ALTER TABLE qa_subject_deals ADD COLUMN IF NOT EXISTS rules_version SMALLINT NOT NULL DEFAULT 1;

-- ── СЛОВАРЬ ПАРКОВ И КАНАЛОВ ───────────────────────────────────────────────
-- Пополняется САМ (новое написание заводится строкой при связывании), а человек
-- правит подпись и склейку. `aliases` — сырые написания, которые сводятся к
-- этому коду: в базе 29 значений «Таксопарка привлечения» на семь брендов ТЗ,
-- и «Ноль такси», «NolTaxi», «ноль такси » — это один парк и три написания.
CREATE TABLE IF NOT EXISTS qa_marketing_dict (
    kind          VARCHAR(16)  NOT NULL,   -- park | channel
    code          VARCHAR(64)  NOT NULL,
    title         TEXT         NOT NULL DEFAULT '',
    aliases       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    sort_order    INTEGER      NOT NULL DEFAULT 1000,
    is_hidden     BOOLEAN      NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_by    INTEGER,
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kind, code)
);

-- Поиск идёт по вхождению сырого написания в массив алиасов (jsonb ?), значит
-- нужен GIN: без него каждая строка списка разворачивала бы весь словарь.
-- Класс операторов — ОБЫЧНЫЙ jsonb_ops, а не jsonb_path_ops: последний умеет
-- только @>, и оператор ? пошёл бы мимо индекса, то есть индекс был бы мёртвым
-- грузом, который ещё и обновляется на каждой правке словаря.
CREATE INDEX IF NOT EXISTS idx_qa_marketing_dict_aliases
    ON qa_marketing_dict USING GIN (aliases);

-- ── ПРЕСЕТЫ ФИЛЬТРОВ ───────────────────────────────────────────────────────
-- Требование раздела 3 ТЗ: аналитик сохраняет отбор себе, руководитель — на
-- весь отдел. Поэтому владелец и признак «общий» — разные поля: общий пресет
-- остаётся у своего автора, и по уходу человека видно, чей он был.
CREATE TABLE IF NOT EXISTS qa_filter_presets (
    id          BIGSERIAL    PRIMARY KEY,
    owner_id    INTEGER      NOT NULL,
    department  VARCHAR(24)  NOT NULL DEFAULT '',
    name        TEXT         NOT NULL,
    payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    is_shared   BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- Одно имя на человека в пределах отдела: «Сохранить» с тем же именем должно
-- ПЕРЕЗАПИСАТЬ пресет, а не завести второй такой же в списке.
CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_filter_presets_name
    ON qa_filter_presets (owner_id, department, lower(name));

CREATE INDEX IF NOT EXISTS idx_qa_filter_presets_shared
    ON qa_filter_presets (department, is_shared) WHERE is_shared;
"""

# Справочник, с которого раздел стартует на чистой базе. Коды — латиницей и
# стабильные: они уезжают в адресную строку, в пресеты и в выгрузку, и
# переименование подписи не должно ломать сохранённый отбор.
#
# Алиасы собраны из ДВУХ источников: перечень брендов из ТЗ (раздел 6.1, ФТ-05)
# и фактические написания в проде на 22.09.2026 (29 значений поля «Таксопарк
# привлечения» плюс то, что реально лежит в op_funnel_leads.park_name).
PARK_SEED = (
    ('itaxi',    'iTaxi',      10, ('itaxi', 'itaxi 2', 'itaxi 3', 'itaxi  (доставка)',
                                    'itaxi (доставка)', 'айтакси', 'i taxi')),
    ('jana',     'Jana',       20, ('jana', 'jana taxi', 'жана', 'жана такси')),
    ('tenge',    'Tenge',      30, ('tenge', 'tenge taxi', 'tengetaxi', 'tengegruz',
                                    'тенге', 'тенге такси')),
    ('amanat',   'Amanat',     40, ('amanat', 'аманат')),
    ('noltaxi',  'NolTaxi',    50, ('noltaxi', 'nol taxi', 'ноль такси', 'нольтакси',
                                    '0 такси')),
    ('adal',     'Adal',       60, ('adal', 'адал')),
    ('qazaq',    'Qazaq',      70, ('qazaq', 'казах', 'қазақ')),
)

# Каналы — перечень ФТ-06 плюс то, чем их называет наша же нормализация
# (`op_funnel.sources.amo_source_norm`): она сводит «fb»/«ig» к facebook, а
# «seo»/«youtube»/«chatgpt.com» — к google. YouTube ТЗ требует отдельным
# каналом, а нормализатор воронки трогать нельзя (по нему сходятся отчёты
# «Воронки ОП» с выгрузкой маркетинга), поэтому модуль разводит его сам — по
# СЫРОМУ utm_source сделки из amo_leads и по тегу (filters.CHANNEL_RAW).
CHANNEL_SEED = (
    ('google',   'Google',     10, ('google', 'googleban', 'google444', 'seo', 'sait')),
    ('youtube',  'YouTube',    15, ('youtube', 'yt')),
    ('facebook', 'Facebook',   20, ('facebook', 'fb', 'ig', 'instagram')),
    ('tiktok',   'TikTok',     30, ('tiktok', 'tik tok')),
    ('yandex',   'Yandex',     40, ('yandex', 'ya', 'yataxi', 'yaitaxi')),
    ('olx',      'OLX',        50, ('olx', 'olxx')),
    ('2gis',     '2ГИС',       60, ('2gis', '2гис')),
    ('whatsapp', 'WhatsApp',   70, ('wz', 'whatsapp', 'wa')),
    ('calls',    'Звонки',     80, ('звонки', 'call', 'сall')),
    ('telegram', 'Telegram',   90, ('telegram', 'tg')),
    ('organic',  'Органика',  100, ('организм', 'органика', 'organic', 'direct')),
    ('referral', 'Реферал',   110, ('реферал', 'referral', 'ref')),
)

SEED_DICT_SQL = """
INSERT INTO qa_marketing_dict (kind, code, title, sort_order, aliases)
VALUES (%s, %s, %s, %s, %s::jsonb)
ON CONFLICT (kind, code) DO NOTHING
"""

SCHEMA_READY_SQL = """
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('qa_subject_deals', 'qa_marketing_dict', 'qa_filter_presets')
"""

SCHEMA_TABLES_COUNT = 3


def init_qa_marketing_schema(cursor):
    """Развернуть схему и засеять словарь. Идемпотентно, зовётся при каждом старте."""
    import json

    cursor.execute(QA_MARKETING_SCHEMA_SQL)
    for kind, seed in (('park', PARK_SEED), ('channel', CHANNEL_SEED)):
        for code, title, order, aliases in seed:
            # Код сам себе алиас: значение в базе часто уже совпадает с кодом
            # («olx», «tiktok»), и без этого оно не нашлось бы в собственной строке.
            full = sorted({code, *(alias.lower().strip() for alias in aliases)})
            cursor.execute(SEED_DICT_SQL, (kind, code, title, order, json.dumps(full)))


def schema_is_ready(cursor):
    """Все ли таблицы на месте. Дешевле, чем ловить UndefinedTable на каждом роуте."""
    cursor.execute(SCHEMA_READY_SQL)
    row = cursor.fetchone()
    count = (row[0] if not isinstance(row, dict) else list(row.values())[0]) if row else 0
    return int(count or 0) >= SCHEMA_TABLES_COUNT
