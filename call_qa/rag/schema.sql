-- call_qa: схема для AI-оценок и базы разборов (RAG на pgvector).
-- Применять к РАБОЧЕЙ (read-write) БД. dim = 768 (Vertex text-multilingual-embedding-002).
-- pgvector 0.8.0 доступен и TRUSTED на этой БД (PG 16) — владелец БД включает его без суперюзера.

CREATE EXTENSION IF NOT EXISTS vector;

-- Классификация критериев: чем оценивается каждый критерий направления.
-- eval_source: 'transcript' (ИИ по разговору) | 'system_api' (проверка данных в ПО) | 'manual'.
-- Пока строки нет — код применяет эвристику по названию (criterion_config.py).
CREATE TABLE IF NOT EXISTS criterion_config (
    direction_id    integer NOT NULL,
    criterion_idx   integer NOT NULL,
    eval_source     text NOT NULL DEFAULT 'transcript',
    default_verdict text,                       -- что ставить, если источник недоступен (или NULL → Pending)
    notes           text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (direction_id, criterion_idx)
);

-- Метаданные AI-оценки (саму оценку кладём в существующую calls с evaluator='AI';
-- здесь — то, чего в calls нет: уверенность, цитаты, версия модели, неуверенность ASR).
CREATE TABLE IF NOT EXISTS ai_evaluation_meta (
    id              bigserial PRIMARY KEY,
    call_id         bigint NOT NULL,            -- FK -> calls.id
    direction_id    integer NOT NULL,
    model           text NOT NULL,              -- напр. claude-opus-4-8
    overall_conf    real,                       -- общая уверенность модели
    per_criterion   jsonb NOT NULL,             -- [{idx, verdict, confidence, evidence_quote, comment}]
    asr_mean_conf   real,                       -- средняя уверенность ASR
    asr_low_spans   jsonb,                      -- неуверенные фрагменты транскрипта
    needs_review    boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_eval_call ON ai_evaluation_meta (call_id);
CREATE INDEX IF NOT EXISTS idx_ai_eval_review ON ai_evaluation_meta (needs_review) WHERE needs_review;

-- Кэш готовой карточки ревью (чтобы повтор открытия был мгновенным и без повторной оплаты ASR/LLM).
CREATE TABLE IF NOT EXISTS ai_review_cache (
    call_id     bigint NOT NULL,
    model       text NOT NULL,
    payload     jsonb NOT NULL,             -- полный контракт карточки (транскрипт + критерии)
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (call_id, model)
);

-- База разборов человека = «память» RAG.
CREATE TABLE IF NOT EXISTS qa_adjudications (
    id               bigserial PRIMARY KEY,
    direction_id     integer NOT NULL,
    criterion_idx    integer NOT NULL,          -- индекс критерия в шкале направления
    criterion_name   text,
    call_id          bigint,                    -- откуда возник спор
    excerpt          text NOT NULL,             -- фрагмент транскрипта (ситуация)
    ai_verdict       text,                      -- что поставил ИИ
    correct_verdict  text NOT NULL,             -- что правильно (человек)
    reason           text NOT NULL,             -- почему — это и есть «правило»
    not_covered      text,                      -- границы: какие нарушения правило НЕ оправдывает
    situation        text,                      -- обобщённое «когда применять» (excerpt остаётся дословной цитатой)
    situation_tag    text,                      -- короткий тег ситуации (для фильтра)
    embedding        vector(768),               -- эмбеддинг для семантического поиска (может быть NULL)
    use_count        integer NOT NULL DEFAULT 0,-- сколько раз подтянулась в оценки
    created_by       integer,                   -- users.id ревьюера
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_adj_dir_crit ON qa_adjudications (direction_id, criterion_idx);

-- Для БД, где таблица создана до появления границ/ситуации правила.
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS not_covered text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS situation text;

-- След ревью: ai_evaluation_meta становится и журналом оценок, и состоянием очереди ревью.
-- review_outcome: NULL (не проверялся) | 'confirmed' (человек согласился) | 'adjudicated' (исправил).
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS review_reasons jsonb;
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS review_outcome text;
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS reviewed_by integer;
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
-- Для upsert по (call_id, model): одна актуальная мета на звонок и версию модели.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_eval_call_model ON ai_evaluation_meta (call_id, model);
