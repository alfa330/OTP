"""Схема раздела «Тренажёр». Все таблицы с префиксом trainer_.

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS. Вызывается один раз при
старте из Database._init_db через init_trainer_schema(cursor).

Раздел тестовый и живёт ради ЗАМЕРОВ: владелец потребовал, чтобы сохранялось
абсолютно всё. Поэтому таблицы устроены не «диалог и оценка», а «диалог, оценка
и полный тайминг каждого звена на каждой реплике» — иначе через неделю нельзя
будет ответить, что именно тормозило и сколько это стоило.

Модель:

    trainer_sessions   один разговор: режим, сценарий, стек моделей, итог, стоимость
    trainer_turns      одна реплика: текст + тайминги распознавания, модели и синтеза
    trainer_events     всё, что не реплика: отказы провайдеров, перебивания, ошибки

Два режима в одной паре таблиц, а не в двух:

* `driver`  — ИИ играет водителя, человек тренируется отвечать, в конце оценка;
* `mentor`  — наоборот: человек спрашивает, ИИ отвечает как опытный оператор,
  опираясь на базу знаний вики (те же права на статьи, что у чат-помощника).

Разговор в обоих режимах — это последовательность реплик с одинаковым набором
замеров, и различает их поле mode. Разводить это на два комплекта таблиц значило
бы дублировать двадцать колонок таймингов ради одного различия в источнике ответа.

Почему стоимость считается и хранится, а не выводится из тарифа при показе:
тарифы провайдеров меняются, а вопрос «сколько стоил вот тот прогон» задаётся
задним числом. Ставки, по которым посчитано, лежат рядом с суммой (rates jsonb).
"""
from __future__ import annotations

_STATEMENTS = [
    # ── сессия: один разговор целиком ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trainer_sessions (
        id                  SERIAL PRIMARY KEY,
        user_id             INTEGER NOT NULL,
        mode                TEXT NOT NULL DEFAULT 'driver',
        scenario_key        TEXT,
        scenario_title      TEXT,
        difficulty          SMALLINT,
        lang                TEXT,

        status              TEXT NOT NULL DEFAULT 'active',
        started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at         TIMESTAMPTZ,
        duration_ms         INTEGER,

        -- стек, на котором фактически шёл разговор: он подменяется на ходу при
        -- отказе провайдера, поэтому пишется по факту, а не берётся из конфига
        stt_model           TEXT,
        llm_provider        TEXT,
        llm_model           TEXT,
        tts_model           TEXT,
        tts_voice           TEXT,

        turns_count         INTEGER NOT NULL DEFAULT 0,
        barge_ins           INTEGER NOT NULL DEFAULT 0,
        audio_in_ms         INTEGER NOT NULL DEFAULT 0,
        audio_out_ms        INTEGER NOT NULL DEFAULT 0,

        -- сводные тайминги по сессии, чтобы журнал не считал их на лету
        voice_to_voice_p50  INTEGER,
        voice_to_voice_max  INTEGER,

        score               SMALLINT,
        review              JSONB,
        review_provider     TEXT,
        review_model        TEXT,
        review_ms           INTEGER,

        cost_usd            NUMERIC(10, 6),
        cost_breakdown      JSONB,
        rates               JSONB,

        client              JSONB,
        error               TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # ── реплика: текст + полный тайминг звеньев ──────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trainer_turns (
        id                  SERIAL PRIMARY KEY,
        session_id          INTEGER NOT NULL REFERENCES trainer_sessions(id) ON DELETE CASCADE,
        idx                 INTEGER NOT NULL,
        role                TEXT NOT NULL,
        text                TEXT NOT NULL DEFAULT '',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        -- распознавание речи человека
        stt_lang            TEXT,
        stt_langs           JSONB,
        stt_confidence      REAL,
        stt_tokens          INTEGER,
        stt_audio_ms        INTEGER,
        endpoint_delay_ms   INTEGER,

        -- модель диалога
        llm_provider        TEXT,
        llm_model           TEXT,
        llm_first_token_ms  INTEGER,
        llm_total_ms        INTEGER,
        llm_input_tokens    INTEGER,
        llm_output_tokens   INTEGER,
        llm_cached_tokens   INTEGER,

        -- синтез речи
        tts_model           TEXT,
        tts_ttfb_ms         INTEGER,
        tts_audio_ms        INTEGER,
        tts_bytes           INTEGER,

        -- главная цифра: от последнего звука голоса человека до первого звука ответа
        voice_to_voice_ms   INTEGER,
        barge_in            BOOLEAN NOT NULL DEFAULT FALSE,

        -- режим «наставник»: на какие статьи опирался ответ
        sources             JSONB,
        raw                 JSONB
    )
    """,

    # ── события: отказы, подмены провайдера, ошибки ──────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trainer_events (
        id          SERIAL PRIMARY KEY,
        session_id  INTEGER REFERENCES trainer_sessions(id) ON DELETE CASCADE,
        user_id     INTEGER,
        at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        level       TEXT NOT NULL DEFAULT 'info',
        code        TEXT NOT NULL,
        message     TEXT,
        payload     JSONB
    )
    """,

    # ── индексы ──────────────────────────────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_trainer_sessions_user ON trainer_sessions (user_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trainer_sessions_mode ON trainer_sessions (mode, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trainer_sessions_status ON trainer_sessions (status) WHERE status = 'active'",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trainer_turns_session_idx ON trainer_turns (session_id, idx)",
    "CREATE INDEX IF NOT EXISTS idx_trainer_turns_session ON trainer_turns (session_id, idx)",
    "CREATE INDEX IF NOT EXISTS idx_trainer_events_session ON trainer_events (session_id, at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trainer_events_level ON trainer_events (level, at DESC) WHERE level <> 'info'",
]

# Миграции для уже развёрнутой базы. Пусто, пока схема первой версии: каждая
# новая колонка добавляется СЮДА, а не правкой CREATE TABLE выше, иначе на
# существующем проде её никто не создаст.
_MIGRATIONS: list[str] = []


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_trainer_schema(cursor):
    """Разворачивает схему раздела. Курсор приходит из _init_db, транзакцией
    управляет вызывающий.

    Порядок — таблицы, потом ALTER'ы, и только потом индексы. Он не для
    красоты: на пустой базе всё равно, а на уже развёрнутой индекс по новому
    столбцу создаётся раньше, чем этот столбец появляется, — и падает. Ровно
    так 17.08.2026 в разделе «Обращения» индекс выполнился до ALTER TABLE ADD
    COLUMN, и прод отдавал 500. Разбор по типу оператора, а не двумя списками:
    список один, добавлять в него можно куда угодно, порядок останется верным.
    """
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _MIGRATIONS:
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)
