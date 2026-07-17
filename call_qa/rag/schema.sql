-- call_qa: production schema for observable, versioned QA evaluation and hybrid RAG.
-- Every statement is idempotent.  Existing legacy tables and columns are retained
-- during the rollout; normalized tables are additive and can be backfilled online.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Legacy contracts (kept intact until every caller has moved to immutable runs)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS criterion_config (
    direction_id    integer NOT NULL,
    criterion_idx   integer NOT NULL,
    eval_source     text NOT NULL DEFAULT 'transcript',
    default_verdict text,
    notes           text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (direction_id, criterion_idx)
);

CREATE TABLE IF NOT EXISTS ai_evaluation_meta (
    id              bigserial PRIMARY KEY,
    call_id         bigint NOT NULL,
    direction_id    integer NOT NULL,
    model           text NOT NULL,
    overall_conf    real,
    per_criterion   jsonb NOT NULL,
    asr_mean_conf   real,
    asr_low_spans   jsonb,
    needs_review    boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_eval_call ON ai_evaluation_meta (call_id);
CREATE INDEX IF NOT EXISTS idx_ai_eval_review ON ai_evaluation_meta (needs_review) WHERE needs_review;

CREATE TABLE IF NOT EXISTS ai_review_cache (
    call_id     bigint NOT NULL,
    model       text NOT NULL,
    payload     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (call_id, model)
);

-- База разборов человека = legacy «память» RAG.  New writes are mirrored into
-- qa_adjudication_cases and qa_policy_rules by the application during rollout.
CREATE TABLE IF NOT EXISTS qa_adjudications (
    id               bigserial PRIMARY KEY,
    direction_id     integer NOT NULL,
    criterion_idx    integer NOT NULL,
    criterion_name   text,
    call_id          bigint,
    excerpt          text NOT NULL,
    ai_verdict       text,
    correct_verdict  text NOT NULL,
    reason           text NOT NULL,
    not_covered      text,
    situation        text,
    situation_tag    text,
    is_active        boolean NOT NULL DEFAULT true,
    embedding        vector(768),
    use_count        integer NOT NULL DEFAULT 0,
    created_by       integer,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_adj_dir_crit ON qa_adjudications (direction_id, criterion_idx);

ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS not_covered text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS situation text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS criterion_id text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS scale_revision_id bigint;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS rule_status text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS rule_version integer NOT NULL DEFAULT 1;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS content_hash character(64);
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS verified_excerpt boolean NOT NULL DEFAULT false;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS evidence_status text NOT NULL DEFAULT 'unverified';
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS evidence_start_offset integer;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS evidence_end_offset integer;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS transcript_hash character(64);
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS embedding_provider text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS embedding_model text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS embedding_dim integer;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS index_status text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS index_error text;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS indexed_at timestamptz;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS canonical_case_id uuid;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS canonical_rule_id uuid;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS updated_by integer;
ALTER TABLE qa_adjudications ADD COLUMN IF NOT EXISTS updated_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_adj_criterion_id ON qa_adjudications (direction_id, criterion_id)
    WHERE criterion_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_adj_migration_pending ON qa_adjudications (id)
    WHERE canonical_rule_id IS NULL;

ALTER TABLE criterion_config ADD COLUMN IF NOT EXISTS criterion_id text;
ALTER TABLE criterion_config ADD COLUMN IF NOT EXISTS scale_revision_id bigint;
CREATE UNIQUE INDEX IF NOT EXISTS uq_criterion_config_stable_id
    ON criterion_config (direction_id, criterion_id) WHERE criterion_id IS NOT NULL;

ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS review_reasons jsonb;
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS review_outcome text;
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS reviewed_by integer;
ALTER TABLE ai_evaluation_meta ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_eval_call_model ON ai_evaluation_meta (call_id, model);

-- ---------------------------------------------------------------------------
-- Stable criterion identities and content-addressed scale revisions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS qa_criterion_registry (
    criterion_id       text PRIMARY KEY,
    direction_id       integer NOT NULL,
    stable_key         text NOT NULL,
    canonical_name     text NOT NULL,
    first_seen_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at       timestamptz NOT NULL DEFAULT now(),
    deprecated_at      timestamptz,
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (direction_id, stable_key),
    CHECK (length(btrim(criterion_id)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_criterion_registry_direction
    ON qa_criterion_registry (direction_id, deprecated_at, canonical_name);

CREATE TABLE IF NOT EXISTS qa_scale_revisions (
    id                  bigserial PRIMARY KEY,
    direction_id        integer NOT NULL,
    scale_revision      bigint NOT NULL,
    content_hash        character(64) NOT NULL,
    criteria_manifest   jsonb NOT NULL,
    created_by          integer,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (direction_id, scale_revision),
    UNIQUE (direction_id, content_hash),
    CHECK (content_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS idx_scale_revision_latest
    ON qa_scale_revisions (direction_id, scale_revision DESC);

CREATE TABLE IF NOT EXISTS qa_scale_revision_criteria (
    scale_revision_id   bigint NOT NULL REFERENCES qa_scale_revisions(id),
    criterion_id        text NOT NULL REFERENCES qa_criterion_registry(criterion_id),
    criterion_idx       integer NOT NULL,
    criterion_name      text NOT NULL,
    description         text,
    weight              numeric,
    is_critical         boolean NOT NULL DEFAULT false,
    deficiency          jsonb,
    eval_source         text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (scale_revision_id, criterion_id),
    UNIQUE (scale_revision_id, criterion_idx),
    CHECK (criterion_idx >= 0)
);

-- ---------------------------------------------------------------------------
-- ASR artifacts and immutable evaluation runs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ai_transcript_cache (
    id                  bigserial PRIMARY KEY,
    call_id             bigint NOT NULL,
    audio_fingerprint   character(64) NOT NULL,
    asr_provider        text NOT NULL,
    asr_model           text NOT NULL,
    asr_config_hash     character(64) NOT NULL,
    transcript_hash     character(64) NOT NULL,
    transcript_text     text NOT NULL,
    segments            jsonb NOT NULL DEFAULT '[]'::jsonb,
    tokens              jsonb,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    languages           jsonb,
    asr_mean_conf       real,
    asr_low_spans       jsonb,
    duration_ms         integer,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (call_id, audio_fingerprint, asr_provider, asr_model, asr_config_hash),
    CHECK (audio_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (asr_config_hash ~ '^[0-9a-f]{64}$'),
    CHECK (transcript_hash ~ '^[0-9a-f]{64}$'),
    CHECK (asr_mean_conf IS NULL OR (asr_mean_conf >= 0 AND asr_mean_conf <= 1)),
    CHECK (duration_ms IS NULL OR duration_ms >= 0)
);
CREATE INDEX IF NOT EXISTS idx_transcript_cache_call
    ON ai_transcript_cache (call_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transcript_cache_hash
    ON ai_transcript_cache (transcript_hash);

-- A run is inserted exactly once, after success/failure.  Force and shadow runs
-- may intentionally share a fingerprint, so fingerprint is indexed, not unique.
CREATE TABLE IF NOT EXISTS ai_evaluation_runs (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id                  bigint NOT NULL,
    direction_id             integer NOT NULL,
    transcript_cache_id      bigint REFERENCES ai_transcript_cache(id),
    transcript_hash          character(64) NOT NULL,
    evaluation_fingerprint   character(64) NOT NULL,
    fingerprint_version      integer NOT NULL DEFAULT 1,
    fingerprint_components   jsonb NOT NULL DEFAULT '{}'::jsonb,
    run_kind                 text NOT NULL DEFAULT 'standard',
    pair_id                  uuid,
    primary_run_id           uuid,
    llm_provider             text NOT NULL DEFAULT 'anthropic',
    model                    text NOT NULL,
    model_config_hash        character(64) NOT NULL,
    prompt_hash              character(64) NOT NULL,
    output_schema_hash       character(64) NOT NULL,
    output_schema_version    text NOT NULL,
    criteria_hash            character(64) NOT NULL,
    criterion_config_hash    character(64) NOT NULL,
    scale_revision_id        bigint REFERENCES qa_scale_revisions(id),
    knowledge_snapshot_id    bigint,
    knowledge_revision       bigint,
    retrieval_config         jsonb NOT NULL DEFAULT '{}'::jsonb,
    retrieval_config_hash    character(64) NOT NULL,
    status                   text NOT NULL,
    overall_conf             real,
    per_criterion            jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code               text,
    error_message            text,
    latency_ms               integer,
    input_tokens             integer,
    output_tokens            integer,
    cache_read_tokens        integer,
    cache_write_tokens       integer,
    estimated_cost           numeric(18,8),
    started_at               timestamptz NOT NULL,
    completed_at             timestamptz NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('succeeded', 'failed', 'cancelled')),
    CHECK (run_kind IN ('standard', 'force', 'shadow', 'batch')),
    CHECK (evaluation_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (completed_at >= started_at),
    CHECK (overall_conf IS NULL OR (overall_conf >= 0 AND overall_conf <= 1)),
    CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CHECK (input_tokens IS NULL OR input_tokens >= 0),
    CHECK (output_tokens IS NULL OR output_tokens >= 0),
    CHECK (status <> 'succeeded' OR error_message IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_evaluation_run_cache_lookup
    ON ai_evaluation_runs (call_id, evaluation_fingerprint, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evaluation_run_direction
    ON ai_evaluation_runs (direction_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evaluation_run_snapshot
    ON ai_evaluation_runs (knowledge_snapshot_id, created_at DESC);
ALTER TABLE ai_evaluation_runs ADD COLUMN IF NOT EXISTS pair_id uuid;
ALTER TABLE ai_evaluation_runs ADD COLUMN IF NOT EXISTS primary_run_id uuid;
CREATE INDEX IF NOT EXISTS idx_evaluation_run_pair
    ON ai_evaluation_runs (pair_id, run_kind, created_at) WHERE pair_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Human cases are evidence records; policy rules are a separate lifecycle.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS qa_adjudication_cases (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_adjudication_id   bigint UNIQUE,
    direction_id             integer NOT NULL,
    criterion_id             text NOT NULL,
    criterion_idx            integer,
    criterion_name           text,
    scale_revision_id        bigint REFERENCES qa_scale_revisions(id),
    call_id                  bigint,
    evaluation_run_id        uuid,
    ai_verdict               text,
    correct_verdict          text NOT NULL,
    evidence_excerpt         text NOT NULL DEFAULT '',
    verified_excerpt         boolean NOT NULL DEFAULT false,
    evidence_status          text NOT NULL DEFAULT 'unverified',
    evidence_start_offset    integer,
    evidence_end_offset      integer,
    evidence_start_ms        integer,
    evidence_end_ms          integer,
    transcript_hash          character(64),
    situation                text,
    reason                   text NOT NULL,
    not_covered              text,
    case_status              text NOT NULL DEFAULT 'submitted',
    content_hash             character(64) NOT NULL,
    supersedes_case_id       uuid REFERENCES qa_adjudication_cases(id),
    created_by               integer,
    created_at               timestamptz NOT NULL DEFAULT now(),
    verified_by              integer,
    verified_at              timestamptz,
    metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (evidence_status IN ('unverified', 'verified', 'no_evidence', 'rejected', 'missing')),
    CHECK (case_status IN ('submitted', 'verified', 'rejected', 'quarantined')),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (transcript_hash IS NULL OR transcript_hash ~ '^[0-9a-f]{64}$'),
    CHECK ((evidence_start_offset IS NULL AND evidence_end_offset IS NULL) OR
           (evidence_start_offset >= 0 AND evidence_end_offset > evidence_start_offset)),
    CHECK ((evidence_start_ms IS NULL AND evidence_end_ms IS NULL) OR
           (evidence_start_ms >= 0 AND evidence_end_ms >= evidence_start_ms)),
    CHECK (verified_excerpt = (evidence_status = 'verified')),
    CHECK (evidence_status <> 'verified' OR
           (length(btrim(evidence_excerpt)) > 0 AND transcript_hash IS NOT NULL AND
            evidence_start_offset IS NOT NULL AND evidence_end_offset IS NOT NULL)),
    CHECK (evidence_status <> 'no_evidence' OR length(btrim(evidence_excerpt)) = 0)
);
CREATE INDEX IF NOT EXISTS idx_adjudication_case_criterion
    ON qa_adjudication_cases (direction_id, criterion_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_adjudication_case_call
    ON qa_adjudication_cases (call_id, created_at DESC) WHERE call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_adjudication_case_status
    ON qa_adjudication_cases (case_status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_adjudication_case_content
    ON qa_adjudication_cases (call_id, direction_id, criterion_id, content_hash)
    WHERE call_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS qa_policy_rules (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    direction_id         integer NOT NULL,
    criterion_id         text NOT NULL,
    criterion_idx        integer,
    criterion_name       text,
    rule_status          text NOT NULL DEFAULT 'draft',
    current_version_id   bigint,
    created_by           integer,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           integer,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    change_reason        text,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (rule_status IN ('draft', 'active', 'deprecated', 'quarantined'))
);
CREATE INDEX IF NOT EXISTS idx_policy_rule_catalog
    ON qa_policy_rules (direction_id, criterion_id, rule_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS qa_policy_rule_versions (
    id                       bigserial PRIMARY KEY,
    rule_id                  uuid NOT NULL REFERENCES qa_policy_rules(id),
    rule_version             integer NOT NULL,
    content_hash             character(64) NOT NULL,
    situation                text NOT NULL,
    rule_text                text NOT NULL,
    not_covered              text,
    correct_verdict          text NOT NULL,
    excerpt                  text,
    verified_excerpt         boolean NOT NULL DEFAULT false,
    evidence_status          text NOT NULL DEFAULT 'unverified',
    evidence_start_offset    integer,
    evidence_end_offset      integer,
    source_case_id           uuid REFERENCES qa_adjudication_cases(id),
    search_document          tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', coalesce(situation, '') || ' ' ||
                     coalesce(rule_text, '') || ' ' || coalesce(not_covered, '') || ' ' ||
                     coalesce(excerpt, ''))) STORED,
    created_by               integer,
    created_at               timestamptz NOT NULL DEFAULT now(),
    change_summary           text,
    metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (rule_id, rule_version),
    UNIQUE (rule_id, content_hash),
    CHECK (rule_version > 0),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (length(btrim(situation)) > 0),
    CHECK (length(btrim(rule_text)) > 0),
    CHECK (evidence_status IN ('unverified', 'verified', 'no_evidence', 'rejected', 'missing')),
    CHECK (verified_excerpt = (evidence_status = 'verified')),
    CHECK ((evidence_start_offset IS NULL AND evidence_end_offset IS NULL) OR
           (evidence_start_offset >= 0 AND evidence_end_offset > evidence_start_offset))
);
CREATE INDEX IF NOT EXISTS idx_policy_version_rule
    ON qa_policy_rule_versions (rule_id, rule_version DESC);
CREATE INDEX IF NOT EXISTS idx_policy_version_fts
    ON qa_policy_rule_versions USING gin (search_document);

CREATE TABLE IF NOT EXISTS qa_policy_rule_events (
    id               bigserial PRIMARY KEY,
    rule_id          uuid NOT NULL REFERENCES qa_policy_rules(id),
    rule_version_id  bigint REFERENCES qa_policy_rule_versions(id),
    event_type       text NOT NULL,
    from_status      text,
    to_status        text,
    actor_id         integer,
    reason           text,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (event_type IN ('created', 'version_created', 'version_selected',
                          'status_changed', 'imported', 'snapshot_published')),
    CHECK (from_status IS NULL OR from_status IN ('draft', 'active', 'deprecated', 'quarantined')),
    CHECK (to_status IS NULL OR to_status IN ('draft', 'active', 'deprecated', 'quarantined'))
);
CREATE INDEX IF NOT EXISTS idx_policy_rule_audit
    ON qa_policy_rule_events (rule_id, created_at, id);

-- Embeddings are version-scoped.  An unbounded vector permits a safe provider
-- migration (for example 768 -> 384 dimensions); model metadata prevents
-- cross-dimensional comparisons and allows per-model partial ANN indexes later.
CREATE TABLE IF NOT EXISTS qa_embedding_models (
    id                   bigserial PRIMARY KEY,
    embedding_provider   text NOT NULL,
    embedding_model      text NOT NULL,
    embedding_dim        integer NOT NULL,
    distance_metric      text NOT NULL DEFAULT 'cosine',
    config_hash          character(64) NOT NULL,
    config               jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_status         text NOT NULL DEFAULT 'active',
    created_at           timestamptz NOT NULL DEFAULT now(),
    deprecated_at        timestamptz,
    UNIQUE (embedding_provider, embedding_model, embedding_dim, config_hash),
    CHECK (embedding_dim > 0),
    CHECK (distance_metric IN ('cosine', 'inner_product', 'l2')),
    CHECK (model_status IN ('active', 'deprecated')),
    CHECK (config_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS qa_policy_rule_embeddings (
    id                   bigserial PRIMARY KEY,
    rule_version_id      bigint NOT NULL REFERENCES qa_policy_rule_versions(id),
    embedding_model_id   bigint NOT NULL REFERENCES qa_embedding_models(id),
    embedding            vector,
    embedding_dim        integer NOT NULL,
    index_status         text NOT NULL DEFAULT 'pending',
    index_error          text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    indexed_at           timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rule_version_id, embedding_model_id),
    CHECK (embedding_dim > 0),
    CHECK (index_status IN ('pending', 'ready', 'error')),
    CHECK (embedding IS NULL OR vector_dims(embedding) = embedding_dim),
    CHECK (index_status <> 'ready' OR (embedding IS NOT NULL AND indexed_at IS NOT NULL)),
    CHECK (index_status <> 'error' OR index_error IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_rule_embedding_ready
    ON qa_policy_rule_embeddings (embedding_model_id, rule_version_id)
    WHERE index_status = 'ready';
CREATE INDEX IF NOT EXISTS idx_rule_embedding_queue
    ON qa_policy_rule_embeddings (index_status, created_at)
    WHERE index_status <> 'ready';
-- Common fixed-dimension expression indexes keep the canonical storage
-- provider-agnostic while allowing pgvector ANN scans at production scale.
CREATE INDEX IF NOT EXISTS idx_rule_embedding_hnsw_768
    ON qa_policy_rule_embeddings USING hnsw
       ((embedding::vector(768)) vector_cosine_ops)
    WITH (m=16, ef_construction=128)
    WHERE embedding_dim=768 AND index_status='ready' AND embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rule_embedding_hnsw_384
    ON qa_policy_rule_embeddings USING hnsw
       ((embedding::vector(384)) vector_cosine_ops)
    WITH (m=16, ef_construction=128)
    WHERE embedding_dim=384 AND index_status='ready' AND embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS qa_reindex_jobs (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id              uuid NOT NULL REFERENCES qa_policy_rules(id),
    rule_version_id      bigint NOT NULL REFERENCES qa_policy_rule_versions(id),
    embedding_model_id   bigint NOT NULL REFERENCES qa_embedding_models(id),
    job_status           text NOT NULL DEFAULT 'queued',
    attempts             integer NOT NULL DEFAULT 0,
    available_at         timestamptz NOT NULL DEFAULT now(),
    locked_at            timestamptz,
    locked_by            text,
    last_error           text,
    requested_by         integer,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    completed_at         timestamptz,
    CHECK (job_status IN ('queued','running','succeeded','failed','cancelled')),
    CHECK (attempts >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reindex_job_active
    ON qa_reindex_jobs (rule_version_id, embedding_model_id)
    WHERE job_status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_reindex_jobs_claim
    ON qa_reindex_jobs (available_at, created_at)
    WHERE job_status='queued';

-- Operational counters are projections, not sources of truth.  They can be
-- rebuilt from retrieval/evaluation/review facts without mutating history.
CREATE TABLE IF NOT EXISTS qa_policy_rule_metrics (
    rule_id                        uuid PRIMARY KEY REFERENCES qa_policy_rules(id),
    retrieved_count                bigint NOT NULL DEFAULT 0,
    included_count                 bigint NOT NULL DEFAULT 0,
    successful_evaluation_count    bigint NOT NULL DEFAULT 0,
    review_confirmed_count         bigint NOT NULL DEFAULT 0,
    review_corrected_count         bigint NOT NULL DEFAULT 0,
    last_retrieved_at              timestamptz,
    last_included_at               timestamptz,
    updated_at                     timestamptz NOT NULL DEFAULT now(),
    CHECK (retrieved_count >= 0 AND included_count >= 0 AND
           successful_evaluation_count >= 0 AND review_confirmed_count >= 0 AND
           review_corrected_count >= 0)
);

-- ---------------------------------------------------------------------------
-- Versioned knowledge snapshots
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS qa_knowledge_snapshots (
    id                   bigserial PRIMARY KEY,
    direction_id         integer NOT NULL,
    scale_revision_id    bigint NOT NULL REFERENCES qa_scale_revisions(id),
    knowledge_revision   bigint NOT NULL,
    content_hash         character(64) NOT NULL,
    rule_manifest        jsonb NOT NULL,
    policy_pack          text NOT NULL DEFAULT '',
    rule_count           integer NOT NULL,
    reason               text,
    created_by           integer,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (direction_id, scale_revision_id, knowledge_revision),
    CHECK (knowledge_revision > 0),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (rule_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_snapshot_hash
    ON qa_knowledge_snapshots (direction_id, scale_revision_id, content_hash);

CREATE TABLE IF NOT EXISTS qa_knowledge_snapshot_rules (
    snapshot_id       bigint NOT NULL REFERENCES qa_knowledge_snapshots(id),
    rule_id           uuid NOT NULL REFERENCES qa_policy_rules(id),
    rule_version_id   bigint NOT NULL REFERENCES qa_policy_rule_versions(id),
    embedding_id      bigint NOT NULL,
    criterion_id      text NOT NULL,
    ordinal           integer NOT NULL,
    content_hash      character(64) NOT NULL,
    PRIMARY KEY (snapshot_id, rule_version_id),
    UNIQUE (snapshot_id, ordinal),
    CHECK (ordinal >= 0),
    CHECK (content_hash ~ '^[0-9a-f]{64}$')
);
ALTER TABLE qa_knowledge_snapshot_rules ADD COLUMN IF NOT EXISTS embedding_id bigint;

CREATE TABLE IF NOT EXISTS qa_knowledge_state (
    direction_id         integer NOT NULL,
    scale_revision_id    bigint NOT NULL REFERENCES qa_scale_revisions(id),
    current_revision     bigint NOT NULL DEFAULT 0,
    current_snapshot_id  bigint REFERENCES qa_knowledge_snapshots(id),
    updated_by           integer,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (direction_id, scale_revision_id),
    CHECK (current_revision >= 0)
);

-- ---------------------------------------------------------------------------
-- Retrieval observability (one final append-only run + ranked hit facts)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS qa_retrieval_runs (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_run_id        uuid,
    call_id                  bigint,
    direction_id             integer NOT NULL,
    criterion_id             text,
    knowledge_snapshot_id    bigint REFERENCES qa_knowledge_snapshots(id),
    retrieval_config         jsonb NOT NULL DEFAULT '{}'::jsonb,
    retrieval_config_hash    character(64) NOT NULL,
    query_hash               character(64) NOT NULL,
    query_text               text,
    query_manifest           jsonb NOT NULL DEFAULT '{}'::jsonb,
    status                   text NOT NULL,
    error_code               text,
    error_message            text,
    latency_ms               integer NOT NULL,
    candidate_count          integer NOT NULL DEFAULT 0,
    included_count           integer NOT NULL DEFAULT 0,
    started_at               timestamptz NOT NULL,
    completed_at             timestamptz NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('succeeded', 'failed', 'skipped')),
    CHECK (retrieval_config_hash ~ '^[0-9a-f]{64}$'),
    CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    CHECK (latency_ms >= 0),
    CHECK (candidate_count >= 0),
    CHECK (included_count >= 0 AND included_count <= candidate_count),
    CHECK (completed_at >= started_at)
);
CREATE INDEX IF NOT EXISTS idx_retrieval_run_evaluation
    ON qa_retrieval_runs (evaluation_run_id, created_at) WHERE evaluation_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_retrieval_run_direction
    ON qa_retrieval_runs (direction_id, criterion_id, created_at DESC);

CREATE TABLE IF NOT EXISTS qa_retrieval_hits (
    id                   bigserial PRIMARY KEY,
    retrieval_run_id     uuid NOT NULL REFERENCES qa_retrieval_runs(id),
    criterion_id         text NOT NULL,
    rule_ref             text NOT NULL,
    source_type          text NOT NULL DEFAULT 'canonical',
    rule_id              uuid,
    rule_version_id      bigint REFERENCES qa_policy_rule_versions(id),
    rank                 integer NOT NULL,
    dense_rank           integer,
    lexical_rank         integer,
    rerank_rank          integer,
    similarity           real,
    dense_score          real,
    lexical_score        real,
    fused_score          real,
    rerank_score         real,
    included             boolean NOT NULL DEFAULT false,
    candidate_status     text NOT NULL,
    reject_reason        text,
    latency_ms           integer,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (retrieval_run_id, criterion_id, rule_ref),
    CHECK (rank > 0),
    CHECK (dense_rank IS NULL OR dense_rank > 0),
    CHECK (lexical_rank IS NULL OR lexical_rank > 0),
    CHECK (rerank_rank IS NULL OR rerank_rank > 0),
    CHECK (similarity IS NULL OR (similarity >= -1 AND similarity <= 1)),
    CHECK (candidate_status IN ('selected', 'rejected', 'filtered', 'error')),
    CHECK (included = (candidate_status = 'selected')),
    CHECK (included OR reject_reason IS NOT NULL),
    CHECK (latency_ms IS NULL OR latency_ms >= 0)
);
CREATE INDEX IF NOT EXISTS idx_retrieval_hit_run_rank
    ON qa_retrieval_hits (retrieval_run_id, criterion_id, rank);
CREATE INDEX IF NOT EXISTS idx_retrieval_hit_rule
    ON qa_retrieval_hits (rule_version_id, created_at DESC)
    WHERE rule_version_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Gold sets, relevance judgements and paired RAG off/on experiments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS qa_gold_sets (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL,
    description          text,
    knowledge_cutoff_at  timestamptz NOT NULL,
    status               text NOT NULL DEFAULT 'draft',
    created_by           integer,
    created_at           timestamptz NOT NULL DEFAULT now(),
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status IN ('draft','ready','archived')),
    CHECK (length(btrim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS qa_gold_labels (
    id                   bigserial PRIMARY KEY,
    gold_set_id          uuid NOT NULL REFERENCES qa_gold_sets(id),
    call_id              bigint NOT NULL,
    direction_id         integer NOT NULL,
    criterion_id         text NOT NULL,
    gold_verdict         text NOT NULL,
    gold_score           numeric,
    call_created_at      timestamptz NOT NULL,
    labelled_by          integer,
    labelled_at          timestamptz NOT NULL DEFAULT now(),
    notes                text,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (gold_set_id, call_id, criterion_id),
    CHECK (gold_verdict IN ('Correct','Incorrect','N/A','Deficiency'))
);
CREATE INDEX IF NOT EXISTS idx_gold_label_eval
    ON qa_gold_labels (gold_set_id,direction_id,criterion_id,call_id);

-- Существующая прод-таблица создана со старым CHECK без 'Deficiency' (CREATE TABLE
-- IF NOT EXISTS его не обновит) — пересоздаём ограничение идемпотентной парой.
ALTER TABLE qa_gold_labels DROP CONSTRAINT IF EXISTS qa_gold_labels_gold_verdict_check;
ALTER TABLE qa_gold_labels ADD CONSTRAINT qa_gold_labels_gold_verdict_check
    CHECK (gold_verdict IN ('Correct','Incorrect','N/A','Deficiency'));

CREATE TABLE IF NOT EXISTS qa_retrieval_relevance_labels (
    gold_label_id        bigint NOT NULL REFERENCES qa_gold_labels(id),
    rule_version_id      bigint NOT NULL REFERENCES qa_policy_rule_versions(id),
    is_relevant          boolean NOT NULL,
    labelled_by          integer,
    labelled_at          timestamptz NOT NULL DEFAULT now(),
    notes                text,
    PRIMARY KEY (gold_label_id, rule_version_id)
);

CREATE TABLE IF NOT EXISTS qa_rag_experiments (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    gold_set_id              uuid NOT NULL REFERENCES qa_gold_sets(id),
    name                     text NOT NULL,
    model                    text NOT NULL,
    evaluation_config        jsonb NOT NULL,
    evaluation_config_hash   character(64) NOT NULL,
    knowledge_snapshot_id    bigint REFERENCES qa_knowledge_snapshots(id),
    status                   text NOT NULL DEFAULT 'draft',
    metrics                  jsonb,
    started_by               integer,
    started_at               timestamptz,
    completed_at             timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('draft','running','succeeded','failed','cancelled')),
    CHECK (evaluation_config_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS qa_rag_experiment_pairs (
    experiment_id        uuid NOT NULL REFERENCES qa_rag_experiments(id),
    gold_label_id        bigint NOT NULL REFERENCES qa_gold_labels(id),
    rag_off_run_id       uuid REFERENCES ai_evaluation_runs(id),
    rag_on_run_id        uuid REFERENCES ai_evaluation_runs(id),
    off_verdict          text,
    on_verdict           text,
    outcome              text,
    latency_delta_ms     integer,
    input_token_delta    integer,
    cost_delta           numeric(18,8),
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (experiment_id, gold_label_id),
    CHECK (outcome IS NULL OR outcome IN ('unchanged','improved','harmed','both_wrong'))
);

CREATE TABLE IF NOT EXISTS qa_rag_rollout_config (
    direction_id         integer PRIMARY KEY,
    rollout_mode         text NOT NULL DEFAULT 'shadow',
    canary_percent       integer NOT NULL DEFAULT 0,
    quality_gates        jsonb NOT NULL DEFAULT
       '{"alarm_precision_gain_pp":10,"max_recall_drop_pp":2,"max_false_hit_rate":0.05,"max_p95_retrieval_ms":500}'::jsonb,
    approved_experiment_id uuid REFERENCES qa_rag_experiments(id),
    updated_by           integer,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CHECK (rollout_mode IN ('off','shadow','canary','active')),
    CHECK (canary_percent BETWEEN 0 AND 100)
);

-- ---------------------------------------------------------------------------
-- Read models for retrieval and admin UX.  The catalog includes every lifecycle
-- status and unmigrated legacy rows; active view is the production subset.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS qa_active_policy_rules;
DROP VIEW IF EXISTS qa_snapshot_policy_rules;
DROP VIEW IF EXISTS qa_policy_rule_catalog;

CREATE OR REPLACE VIEW qa_policy_rule_catalog AS
SELECT
    r.id::text                              AS rule_id,
    v.id::text                              AS rule_version_id,
    'canonical'::text                       AS source_type,
    c.legacy_adjudication_id                AS legacy_adjudication_id,
    r.direction_id,
    d.name                                  AS direction_name,
    r.criterion_id,
    r.criterion_idx,
    r.criterion_name,
    v.situation,
    v.excerpt,
    v.correct_verdict,
    v.rule_text                             AS reason,
    v.not_covered,
    r.rule_status,
    v.rule_version,
    v.content_hash::text,
    v.verified_excerpt,
    v.evidence_status,
    v.evidence_start_offset,
    v.evidence_end_offset,
    r.created_by,
    r.created_at,
    r.updated_by,
    r.updated_at,
    emb.embedding,
    emb.embedding_provider,
    emb.embedding_model,
    emb.embedding_dim,
    emb.index_status,
    emb.index_error,
    emb.indexed_at,
    r.metadata,
    coalesce(metrics.retrieved_count, 0)     AS retrieved_count,
    coalesce(metrics.included_count, 0)      AS included_count,
    coalesce(metrics.successful_evaluation_count, 0) AS successful_evaluation_count,
    coalesce(metrics.review_confirmed_count, 0) AS review_confirmed_count,
    coalesce(metrics.review_corrected_count, 0) AS review_corrected_count,
    c.ai_verdict                            AS ai_verdict,
    emb.embedding_config_hash               AS embedding_config_hash,
    v.search_document                       AS search_document
FROM qa_policy_rules r
LEFT JOIN directions d ON d.id = r.direction_id
LEFT JOIN LATERAL (
    SELECT rv.*
      FROM qa_policy_rule_versions rv
     WHERE rv.rule_id = r.id
     ORDER BY (rv.id = r.current_version_id) DESC, rv.rule_version DESC
     LIMIT 1
) v ON true
LEFT JOIN qa_adjudication_cases c ON c.id = v.source_case_id
LEFT JOIN LATERAL (
    SELECT e.embedding, m.embedding_provider, m.embedding_model,
           e.embedding_dim, e.index_status, e.index_error, e.indexed_at,
           m.config_hash::text AS embedding_config_hash
      FROM qa_policy_rule_embeddings e
      JOIN qa_embedding_models m ON m.id = e.embedding_model_id
     WHERE e.rule_version_id = v.id
     ORDER BY CASE e.index_status WHEN 'ready' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
              e.indexed_at DESC NULLS LAST, e.id DESC
     LIMIT 1
) emb ON true
LEFT JOIN qa_policy_rule_metrics metrics ON metrics.rule_id = r.id
UNION ALL
SELECT
    ('legacy:' || a.id)::text                AS rule_id,
    ('legacy:' || a.id || ':v' || coalesce(a.rule_version, 1))::text AS rule_version_id,
    'legacy'::text                          AS source_type,
    a.id                                    AS legacy_adjudication_id,
    a.direction_id,
    d.name                                  AS direction_name,
    a.criterion_id,
    a.criterion_idx,
    a.criterion_name,
    coalesce(nullif(a.situation, ''), a.excerpt) AS situation,
    a.excerpt,
    a.correct_verdict,
    a.reason,
    a.not_covered,
    CASE WHEN NOT a.is_active THEN 'deprecated'
         WHEN a.evidence_status IN ('verified','no_evidence') THEN 'active'
         ELSE 'quarantined' END              AS rule_status,
    coalesce(a.rule_version, 1)              AS rule_version,
    a.content_hash::text,
    a.verified_excerpt,
    a.evidence_status,
    a.evidence_start_offset,
    a.evidence_end_offset,
    a.created_by,
    a.created_at,
    a.updated_by,
    coalesce(a.updated_at, a.created_at)     AS updated_at,
    a.embedding::vector                     AS embedding,
    a.embedding_provider,
    a.embedding_model,
    coalesce(a.embedding_dim,
             CASE WHEN a.embedding IS NULL THEN NULL ELSE vector_dims(a.embedding) END) AS embedding_dim,
    coalesce(a.index_status,
             CASE WHEN a.embedding IS NULL THEN 'pending' ELSE 'ready' END) AS index_status,
    a.index_error,
    a.indexed_at,
    jsonb_build_object('legacy_adjudication_id', a.id) AS metadata,
    coalesce(a.use_count, 0)::bigint         AS retrieved_count,
    coalesce(a.use_count, 0)::bigint         AS included_count,
    0::bigint                                AS successful_evaluation_count,
    0::bigint                                AS review_confirmed_count,
    0::bigint                                AS review_corrected_count,
    a.ai_verdict                             AS ai_verdict,
    NULL::text                               AS embedding_config_hash,
    to_tsvector('simple', concat_ws(' ', a.situation, a.excerpt, a.reason,
                                    a.not_covered)) AS search_document
FROM qa_adjudications a
LEFT JOIN directions d ON d.id = a.direction_id
WHERE NOT EXISTS (
    SELECT 1 FROM qa_adjudication_cases mapped
     WHERE mapped.legacy_adjudication_id = a.id
);

CREATE OR REPLACE VIEW qa_active_policy_rules AS
SELECT * FROM qa_policy_rule_catalog
 WHERE rule_status = 'active'
   AND evidence_status IN ('verified','no_evidence')
   AND index_status = 'ready';

-- Exact immutable rule versions for reproducible retrieval.  Unlike the live
-- catalog this view remains valid after a rule is revised or deprecated.
CREATE OR REPLACE VIEW qa_snapshot_policy_rules AS
SELECT sr.snapshot_id,
       r.id::text AS rule_id, v.id::text AS rule_version_id,
       'canonical'::text AS source_type, NULL::bigint AS legacy_adjudication_id,
       r.direction_id, d.name AS direction_name, r.criterion_id,
       r.criterion_idx, r.criterion_name, v.situation, v.excerpt,
       v.correct_verdict, v.rule_text AS reason, v.not_covered,
       'active'::text AS rule_status, v.rule_version, v.content_hash::text,
       v.verified_excerpt, v.evidence_status, v.evidence_start_offset,
       v.evidence_end_offset, r.created_by, r.created_at, r.updated_by,
       r.updated_at, e.embedding, m.embedding_provider, m.embedding_model,
       e.embedding_dim, e.index_status, e.index_error, e.indexed_at,
       r.metadata, m.config_hash::text AS embedding_config_hash,
       v.search_document
  FROM qa_knowledge_snapshot_rules sr
  JOIN qa_policy_rules r ON r.id=sr.rule_id
  JOIN qa_policy_rule_versions v ON v.id=sr.rule_version_id
  LEFT JOIN directions d ON d.id=r.direction_id
  JOIN qa_policy_rule_embeddings e ON e.id=sr.embedding_id
  JOIN qa_embedding_models m ON m.id=e.embedding_model_id;

CREATE OR REPLACE VIEW qa_current_knowledge_snapshots AS
SELECT s.direction_id, s.scale_revision_id, s.current_revision,
       s.current_snapshot_id, k.content_hash, k.rule_count,
       k.policy_pack, k.rule_manifest, s.updated_by, s.updated_at
  FROM qa_knowledge_state s
  LEFT JOIN qa_knowledge_snapshots k ON k.id = s.current_snapshot_id;

-- ---------------------------------------------------------------------------
-- Database-enforced append-only and audit semantics
-- ---------------------------------------------------------------------------

DO $qa$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_eval_run_snapshot') THEN
        ALTER TABLE ai_evaluation_runs ADD CONSTRAINT fk_eval_run_snapshot
            FOREIGN KEY (knowledge_snapshot_id) REFERENCES qa_knowledge_snapshots(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_eval_primary_run') THEN
        ALTER TABLE ai_evaluation_runs ADD CONSTRAINT fk_eval_primary_run
            FOREIGN KEY (primary_run_id) REFERENCES ai_evaluation_runs(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_case_evaluation_run') THEN
        ALTER TABLE qa_adjudication_cases ADD CONSTRAINT fk_case_evaluation_run
            FOREIGN KEY (evaluation_run_id) REFERENCES ai_evaluation_runs(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_retrieval_evaluation_run') THEN
        ALTER TABLE qa_retrieval_runs ADD CONSTRAINT fk_retrieval_evaluation_run
            FOREIGN KEY (evaluation_run_id) REFERENCES ai_evaluation_runs(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_policy_version_id_rule') THEN
        ALTER TABLE qa_policy_rule_versions ADD CONSTRAINT uq_policy_version_id_rule
            UNIQUE (id, rule_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_policy_current_version_owner') THEN
        ALTER TABLE qa_policy_rules ADD CONSTRAINT fk_policy_current_version_owner
            FOREIGN KEY (current_version_id, id)
            REFERENCES qa_policy_rule_versions(id, rule_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_snapshot_embedding') THEN
        ALTER TABLE qa_knowledge_snapshot_rules ADD CONSTRAINT fk_snapshot_embedding
            FOREIGN KEY (embedding_id) REFERENCES qa_policy_rule_embeddings(id);
    END IF;
END;
$qa$;

CREATE OR REPLACE FUNCTION qa_count_retrieval_hit()
RETURNS trigger
LANGUAGE plpgsql
AS $qa$
BEGIN
    IF NEW.rule_id IS NOT NULL THEN
        INSERT INTO qa_policy_rule_metrics
            (rule_id,retrieved_count,included_count,last_retrieved_at,last_included_at)
        VALUES (NEW.rule_id,1,CASE WHEN NEW.included THEN 1 ELSE 0 END,now(),
                CASE WHEN NEW.included THEN now() ELSE NULL END)
        ON CONFLICT (rule_id) DO UPDATE SET
            retrieved_count=qa_policy_rule_metrics.retrieved_count+1,
            included_count=qa_policy_rule_metrics.included_count+
                CASE WHEN NEW.included THEN 1 ELSE 0 END,
            last_retrieved_at=now(),
            last_included_at=CASE WHEN NEW.included THEN now()
                                  ELSE qa_policy_rule_metrics.last_included_at END,
            updated_at=now();
    END IF;
    RETURN NEW;
END;
$qa$;

DROP TRIGGER IF EXISTS qa_retrieval_hit_counter ON qa_retrieval_hits;
CREATE TRIGGER qa_retrieval_hit_counter AFTER INSERT ON qa_retrieval_hits
    FOR EACH ROW EXECUTE FUNCTION qa_count_retrieval_hit();

CREATE OR REPLACE FUNCTION qa_reject_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $qa$
BEGIN
    RAISE EXCEPTION 'table % is append-only; insert a superseding record instead', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$qa$;

DROP TRIGGER IF EXISTS qa_immutable_guard ON ai_transcript_cache;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON ai_transcript_cache
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON ai_evaluation_runs;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON ai_evaluation_runs
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_scale_revisions;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_scale_revisions
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_scale_revision_criteria;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_scale_revision_criteria
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_adjudication_cases;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_adjudication_cases
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_policy_rule_versions;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_policy_rule_versions
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_policy_rule_events;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_policy_rule_events
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_knowledge_snapshots;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_knowledge_snapshots
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_knowledge_snapshot_rules;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_knowledge_snapshot_rules
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_retrieval_runs;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_retrieval_runs
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();
DROP TRIGGER IF EXISTS qa_immutable_guard ON qa_retrieval_hits;
CREATE TRIGGER qa_immutable_guard BEFORE UPDATE OR DELETE ON qa_retrieval_hits
    FOR EACH ROW EXECUTE FUNCTION qa_reject_append_only_mutation();

CREATE OR REPLACE FUNCTION qa_guard_ready_embedding()
RETURNS trigger
LANGUAGE plpgsql
AS $qa$
BEGIN
    IF OLD.index_status = 'ready' THEN
        RAISE EXCEPTION 'ready embedding % is immutable; create a new model contract', OLD.id
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$qa$;
DROP TRIGGER IF EXISTS qa_ready_embedding_guard ON qa_policy_rule_embeddings;
CREATE TRIGGER qa_ready_embedding_guard
    BEFORE UPDATE OR DELETE ON qa_policy_rule_embeddings
    FOR EACH ROW EXECUTE FUNCTION qa_guard_ready_embedding();

CREATE OR REPLACE FUNCTION qa_guard_policy_rule_change()
RETURNS trigger
LANGUAGE plpgsql
AS $qa$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'policy rules cannot be deleted; deprecate or quarantine them'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR
       NEW.direction_id IS DISTINCT FROM OLD.direction_id OR
       NEW.criterion_id IS DISTINCT FROM OLD.criterion_id OR
       NEW.created_at IS DISTINCT FROM OLD.created_at OR
       NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION 'policy rule identity is immutable; create a new rule instead'
            USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$qa$;

DROP TRIGGER IF EXISTS qa_policy_rule_change_guard ON qa_policy_rules;
CREATE TRIGGER qa_policy_rule_change_guard BEFORE UPDATE OR DELETE ON qa_policy_rules
    FOR EACH ROW EXECUTE FUNCTION qa_guard_policy_rule_change();

CREATE OR REPLACE FUNCTION qa_audit_policy_rule_change()
RETURNS trigger
LANGUAGE plpgsql
AS $qa$
DECLARE
    event_name text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO qa_policy_rule_events
            (rule_id, rule_version_id, event_type, from_status, to_status, actor_id, reason)
        VALUES (NEW.id, NEW.current_version_id, 'created', NULL, NEW.rule_status,
                NEW.created_by, NEW.change_reason);
        RETURN NEW;
    END IF;
    IF NEW.rule_status IS DISTINCT FROM OLD.rule_status THEN
        event_name := 'status_changed';
    ELSIF NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
        event_name := 'version_selected';
    ELSE
        RETURN NEW;
    END IF;
    INSERT INTO qa_policy_rule_events
        (rule_id, rule_version_id, event_type, from_status, to_status, actor_id, reason)
    VALUES (NEW.id, NEW.current_version_id, event_name, OLD.rule_status, NEW.rule_status,
            NEW.updated_by, NEW.change_reason);
    RETURN NEW;
END;
$qa$;

DROP TRIGGER IF EXISTS qa_policy_rule_audit ON qa_policy_rules;
CREATE TRIGGER qa_policy_rule_audit AFTER INSERT OR UPDATE ON qa_policy_rules
    FOR EACH ROW EXECUTE FUNCTION qa_audit_policy_rule_change();

CREATE OR REPLACE FUNCTION qa_guard_rule_embedding_change()
RETURNS trigger
LANGUAGE plpgsql
AS $qa$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'rule embedding records cannot be deleted'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.rule_version_id IS DISTINCT FROM NEW.rule_version_id OR
       OLD.embedding_model_id IS DISTINCT FROM NEW.embedding_model_id OR
       OLD.embedding_dim IS DISTINCT FROM NEW.embedding_dim THEN
        RAISE EXCEPTION 'embedding identity is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.index_status = 'ready' THEN
        RAISE EXCEPTION 'a ready embedding is immutable; register a new model/config'
            USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$qa$;

DROP TRIGGER IF EXISTS qa_rule_embedding_change_guard ON qa_policy_rule_embeddings;
CREATE TRIGGER qa_rule_embedding_change_guard BEFORE UPDATE OR DELETE ON qa_policy_rule_embeddings
    FOR EACH ROW EXECUTE FUNCTION qa_guard_rule_embedding_change();
