# call_qa — production-система ИИ-оценки звонков

`call_qa` распознаёт звонок, оценивает его по версии шкалы и, когда это разрешено rollout-конфигурацией, добавляет в промпт только релевантные проверенные правила из RAG. Транскрипты, оценки, версии правил и состав базы знаний не перезаписываются: результат можно воспроизвести и объяснить через fingerprint, snapshot и retrieval trace.

RAG здесь — не «поиск последних разборов», а версионируемая policy memory. Человеческая корректировка сначала становится доказательным кейсом и черновиком правила; в боевой retrieval правило попадает только после проверки, успешной индексации и явной активации.

## Архитектура

```text
GCS audio
   │  object generation / ETag / size / md5
   ▼
audio fingerprint ──► Soniox ASR ──► immutable ai_transcript_cache
                                             │
                                             ▼
                               evaluation fingerprint
                                             │
                 ┌───────────────────────────┴──────────────────────────┐
                 │                                                      │
       scale revision + knowledge snapshot                  exact cached run
                 │                                                      │
                 ▼                                                      ▼
   query chunks ──► embedding ──► hybrid retrieval ──► Claude ──► evaluation run
                                      │                         │
                                      ▼                         ▼
                              retrieval run/hits          review interface
                                                                  │
                                                                  ▼
                    adjudication case ──► draft rule/version ──► embedding
                                                                  │
                                                     explicit activation
                                                                  │
                                                                  ▼
                                                   new knowledge snapshot
```

Основные компоненты:

- `evaluation/fingerprint.py` — канонические content hashes и полный fingerprint оценки.
- `evaluation/runtime_store.py` — immutable-кэш транскриптов и оценок, блокировка параллельной обработки, запись LLM/retrieval-метрик.
- `rag/knowledge.py` — ревизии шкалы, snapshot базы знаний, доказательные кейсы и lifecycle правил.
- `rag/store.py` — chunking, query embeddings и set-based hybrid retrieval.
- `evaluation/evaluator.py` — один retrieval на оценку и повторное использование одного RAG-контекста во всех LLM-проходах.
- `evaluation/benchmark.py` — парные RAG-off/on метрики, retrieval-метрики, temporal split и quality gates.
- `rag/schema.sql` — идемпотентная production-схема PostgreSQL/pgvector.

## Инварианты воспроизводимости

### Транскрипт

`ai_transcript_cache` хранит неизменяемую ASR-версию. Её идентичность включает:

- fingerprint аудиообъекта;
- ASR-провайдера, модель и конфигурацию;
- hash текста транскрипта.

Повторный запрос использует транскрипт только при точном совпадении этих компонентов. Изменение аудио или ASR-настроек создаёт новую версию, а не перезаписывает старую.

### Оценка

Кэш оценки больше не определяется парой `(call_id, model)`. `evaluation_fingerprint` включает:

- fingerprint транскрипта;
- BULK/HARD-модели, effort и порог эскалации;
- hash системного промпта и output schema;
- hash шкалы и стабильную конфигурацию критериев;
- hash knowledge snapshot либо маркер `rag-disabled`;
- провайдера/модель/dimension эмбеддингов, chunking, top-K и пороги retrieval;
- версию retrieval pipeline и `AI_QA_CODE_VERSION`.

Любое значимое изменение даёт новый fingerprint. `force`, `shadow` и обычный прогон могут намеренно иметь одинаковый fingerprint, поэтому он индексируется, но не является уникальным ключом таблицы запусков.

### Шкала и база знаний

Критерий имеет стабильный `criterion_id`, не зависящий от позиции в массиве. Состав шкалы фиксируется в `qa_scale_revisions`. Для каждого направления `qa_knowledge_snapshots` закрепляет точный набор активных версий правил и ревизию шкалы; snapshot имеет content hash и монотонный `knowledge_revision`.

Оценка с RAG всегда ссылается на конкретный snapshot. Активация, деактивация или новая версия правила публикует новый snapshot, не изменяя историю уже выполненных оценок.

## Хранилище

| Область | Основные объекты |
|---|---|
| Совместимость rolling deploy | `criterion_config`, `ai_evaluation_meta`, `ai_review_cache`, `qa_adjudications` |
| Критерии и шкала | `qa_criterion_registry`, `qa_scale_revisions`, `qa_scale_revision_criteria` |
| Runtime | `ai_transcript_cache`, `ai_evaluation_runs` |
| Решения человека | `qa_adjudication_cases` |
| Правила | `qa_policy_rules`, `qa_policy_rule_versions`, `qa_policy_rule_events` |
| Индекс | `qa_embedding_models`, `qa_policy_rule_embeddings` |
| Версии знаний | `qa_knowledge_snapshots`, `qa_knowledge_snapshot_rules`, `qa_knowledge_state` |
| Retrieval trace | `qa_retrieval_runs`, `qa_retrieval_hits`, `qa_policy_rule_metrics` |
| Контроль качества | `qa_gold_sets`, `qa_gold_labels`, `qa_retrieval_relevance_labels`, `qa_rag_experiments`, `qa_rag_experiment_pairs` |
| Rollout | `qa_rag_rollout_config` |

Immutable-таблицы защищены триггерами от `UPDATE`/`DELETE`. Изменения правил записываются как новые версии и события.

## Hybrid retrieval

Production retrieval выполняется так:

1. Транскрипт разбивается на перекрывающиеся окна. Если звонок очень длинный, окна выбираются равномерно по всей записи, включая середину, а не только начало и конец.
2. Все окна эмбеддятся одним batch-вызовом с ролью `query`. Правила индексируются отдельно с ролью `document`; для E5 используются разные префиксы, для Vertex — разные `task_type`.
3. Один set-based SQL-запрос обрабатывает все критерии и все query-векторы. Для каждого chunk/criterion HNSW сначала выбирает ограниченный ANN top-M, поэтому стоимость не растёт как полный `rules × chunks` scan.
4. ANN-кандидаты уточняются по cosine similarity и сохранённому PostgreSQL `tsvector`; dense/lexical ранги объединяются через reciprocal-rank fusion.
5. В промпт проходят не более `RETRIEVAL_TOP_K` правил на критерий и только после relevance gate. Lexical match может поддержать близкий dense-кандидат, но не обходит semantic threshold для нерелевантного правила.
6. Запрос ограничен выбранным direction, стабильным `criterion_id`, конкретным knowledge snapshot и точным контрактом `(embedding provider, model, dimension)`.

Retrieval возвращает явный статус:

- `ok` — есть прошедшие порог правила;
- `no_match` — корректный штатный результат, подходящих правил нет;
- `degraded` — недоступны embeddings/БД либо нарушен индексный контракт;
- `disabled`/`skipped` — RAG выключен для этого варианта оценки.

При `degraded` система fail-closed: она не подставляет «последние» или случайные правила. Оценщик получает явное указание не использовать память и может завершить базовую оценку без RAG; сбой retrieval остаётся видимым в trace и метриках.

Схема создаёт expression HNSW-индексы для размерностей 768 и 384, сохраняя основную колонку `vector` пригодной для миграции провайдера. Для иной размерности retrieval остаётся корректным, но перед canary нужно добавить соответствующий expression HNSW-индекс и подтвердить план через `EXPLAIN (ANALYZE, BUFFERS)`. ANN не заменяет relevance gate, snapshot-фильтр и гибридное ранжирование.

Snapshot закрепляет не только версию правила, но и конкретный immutable `embedding_id`, provider/model/dimension/config hash. Готовый embedding нельзя изменить триггером; смена региона, task types, префиксов, модели или размерности создаёт новый индексный контракт, snapshot и evaluation fingerprint.

## Lifecycle правил и доказательства

```text
review correction
    └─► verified/no_evidence adjudication case
            └─► draft rule version
                    └─► embedding: pending ─► ready / error
                            └─► explicit activate
                                    └─► active rule + new snapshot
```

Статусы правила: `draft`, `active`, `deprecated`, `quarantined`. Разрешённые переходы намеренно ограничены; из `deprecated` или `quarantined` правило сначала возвращается в `draft`.

Условия активации:

- доказательство имеет статус `verified` либо явно выбран `no_evidence`;
- текущая версия имеет embedding со статусом `ready` для настроенных provider/model/dimension;
- активацию выполняет авторизованный пользователь через lifecycle API/UI.

`verified` evidence — это канонический срез авторитетного транскрипта с offsets и hash транскрипта. Цитата из исторического кейса в RAG-контексте не считается доказательством текущего звонка. Если цитаты действительно нет, reviewer выбирает `no_evidence`; пустое или неподтверждённое поле не маскируется под verified.

Редактирование создаёт новую immutable-версию и возвращает правило в `draft`. Для активного правила неудачная переиндексация семантической правки не ломает работающую версию: изменение отклоняется, старое активное правило и snapshot сохраняются. У draft ошибка индексации видима как `error`, доступен асинхронный retry/reindex. Удаление заменено мягкой депрекацией, история и метрики сохраняются.

Legacy-разборы остаются видимыми во время rolling deploy, но непроверенные записи отображаются как `quarantined` и не попадают в production retrieval. Супер-администратор может явно выбрать «Мигрировать в черновик (без цитаты)»: система создаст canonical case со статусом `no_evidence`, пересчитает embedding и оставит правило draft. После проверки его нужно активировать отдельным действием; автоматического массового доверия старому corpus нет.

## Безопасный rollout

| Режим | Поведение |
|---|---|
| `off` | Пользовательская оценка выполняется без RAG. |
| `shadow` | Пользователь получает RAG-off результат; RAG-on вариант считается в фоне с тем же звонком и связывается через `pair_id`. Режим по умолчанию. |
| `canary` | RAG включается для стабильного процента звонков; bucket детерминирован по call ID. |
| `active` | RAG включён для всех звонков направления. |

Конфигурация в `qa_rag_rollout_config` имеет приоритет над env. Env используется как безопасный fallback при отсутствии записи/схемы. Перевод в `canary` или `active` требует указать завершённый experiment с gold set и `metrics.quality_gates.passed=true`; иначе API отклоняет переключение и оставляет безопасный режим. `evaluation_config` experiment должен в точности совпадать с `current_rag_experiment_config()` (модели, effort, prompt/schema и retrieval contract), а snapshot и направление — с текущими production-данными. Любое последующее изменение этих идентичностей автоматически возвращает фактический режим в `shadow`.

Shadow-задача не меняет пользовательский verdict. Повторный запрос может достроить отсутствующую пару; advisory lock и повторная проверка immutable-кэша уменьшают дубли между worker-процессами.

## Массовая Batch-оценка

`python -m call_qa.batch_eval --month YYYY-MM --workdir <durable-dir>` использует те же fingerprints, snapshot и финализацию, что online-путь. До отправки внешнего Batch-запроса атомарно записывается frozen manifest: версия шкалы, точный knowledge/embedding snapshot, request body hash, RAG prompt, normalized retrieval trace и transcript/evaluation identities. При рестарте берётся этот manifest, а не текущая изменившаяся база знаний.

ASR сначала ищется и сохраняется в `ai_transcript_cache`; локальный `transcripts.jsonl` служит только checkpoint. Ответ Batch становится primary result общего evaluator: missing retry и HARD escalation используют тот же `rag_text` и не запускают retrieval повторно. Итог сначала атомарно пишется в `ai_evaluation_runs` + `qa_retrieval_runs/hits`, затем обновляется совместимая проекция `ai_review_cache`. `batch_id.txt` удаляется только после terminal immutable run для каждого ожидаемого `custom_id`.

Старый фильтр `(call_id, model)` удалён: пропуск определяется только полным evaluation fingerprint после получения точной ASR и snapshot identity. Поэтому изменение prompt, шкалы, модели, embedding config или знаний корректно создаёт новый прогон.

## Миграция схемы

Требования:

- PostgreSQL с расширениями `vector` и `pgcrypto`;
- read-write подключение через `DATABASE_URL` либо `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`;
- резервная копия и проверка сначала на staging для существующей production-БД.

Из корня репозитория:

```bash
python -m call_qa.rag.migrate
```

`schema.sql` сам по себе не применяется. Runner корректно разбирает строки, комментарии, функции и dollar-quoted блоки. В production (`RAG_TRACE_REQUIRED=true`) вся схема — таблицы, HNSW/GIN-индексы, views, FK и immutable triggers — применяется одной транзакцией и фиксируется hash-версией в `qa_schema_migrations`; любая ошибка откатывает всё и останавливает deploy. Диагностический `strict=False` допускает старый пооперационный отчёт, но не подходит для rollout.

Минимальная post-migration проверка:

```sql
SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pgcrypto');
SELECT to_regclass('public.ai_transcript_cache'),
       to_regclass('public.ai_evaluation_runs'),
       to_regclass('public.qa_policy_rules'),
       to_regclass('public.qa_retrieval_runs');
SELECT * FROM qa_current_knowledge_snapshots ORDER BY direction_id;
SELECT rule_status, index_status, count(*)
  FROM qa_policy_rule_catalog
 GROUP BY rule_status, index_status
 ORDER BY rule_status, index_status;
```

Если `CREATE EXTENSION vector` запрещён на managed PostgreSQL, расширение должен включить администратор/провайдер. Не переводите rollout выше `shadow`, пока все statements, views и triggers не применены, а UI не показывает здоровый индекс.

## Переменные окружения

Секреты берутся из окружения; для локальной разработки поддерживается `.env.codex.local`. Секреты не коммитятся.

### Доступы

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` или `POSTGRES_*` | Read-write runtime, миграции, review и индексирование. |
| `DATABASE_URL_READONLY` | Отдельное read-only подключение; при отсутствии используется `POSTGRES_*` в read-only session. |
| `SONIOX_API_KEY` | ASR. |
| `ANTHROPIC_API_KEY` или `CLAUDE_API_KEY` | Claude. |
| `GCS_BUCKET` | Bucket аудиозаписей. |
| `GOOGLE_APPLICATION_CREDENTIALS_CONTENT` | JSON service account для Vertex embeddings. |

### Модели и индекс

| Переменная | Default | Назначение |
|---|---:|---|
| `CLAUDE_MODEL_BULK` | `claude-opus-4-8` | Основной проход. |
| `CLAUDE_MODEL_HARD` | `claude-opus-4-8` | Эскалация спорных критериев. |
| `CLAUDE_EFFORT` | `high` | Effort модели. |
| `CLAUDE_ESCALATE_CONF` | `0.6` | Порог эскалации. |
| `EMBEDDINGS_PROVIDER` | `vertex` | `vertex` или локальный `selfhost`. |
| `VERTEX_REGION` | `asia-southeast1` | Регион Vertex. |
| `VERTEX_EMBED_MODEL` | `text-multilingual-embedding-002` | Модель Vertex. |
| `SELFHOST_EMBED_MODEL` | `intfloat/multilingual-e5-small` | Sentence-Transformers модель для self-host. |
| `EMBED_DIM` | `768` | Жёсткая размерность индексного контракта. Несовпадение останавливает indexing/retrieval. |
| `EMBED_CHUNK_CHARS` | `3200` | Размер query-окна. |
| `EMBED_CHUNK_OVERLAP` | `480` | Перекрытие окон. |
| `EMBED_MAX_CHUNKS` | `16` | Максимум равномерно выбранных окон на звонок. |

### Retrieval и rollout

| Переменная | Default | Назначение |
|---|---:|---|
| `RETRIEVAL_TOP_K` | `3` | Максимум правил на критерий. |
| `RETRIEVAL_MIN_SIMILARITY` | `0.68` | Основной semantic relevance gate. |
| `RETRIEVAL_CANDIDATE_MULTIPLIER` | `4` | Глубина наблюдаемого candidate pool. |
| `RETRIEVAL_LEXICAL_MIN_SCORE` | `0.05` | Минимальный lexical score. |
| `RETRIEVAL_LEXICAL_DENSE_MARGIN` | `0.08` | Допустимая близость dense-кандидата к основному порогу для lexical rescue. |
| `RAG_MODE` | `shadow` | Env fallback: `off`, `shadow`, `canary`, `active`. |
| `RAG_CANARY_PERCENT` | `10` | Env fallback для canary, `0..100`. |
| `RAG_TRACE_REQUIRED` | `true` | Запрещает тихий откат к старому кэшу, если immutable runtime-схема ещё не применена. |
| `RAG_SHADOW_WORKERS` | `2` | Локальный пул фоновых paired runs. |
| `RAG_REINDEX_WORKERS` | `2` | Локальный пул асинхронной переиндексации. |
| `RAG_REINDEX_MAX_ATTEMPTS` | `5` | Максимум durable retry reindex job с экспоненциальной задержкой. |
| `AI_QA_CODE_VERSION` | `ai-qa-2026-07-v2` | Версия evaluator в fingerprint; меняйте при несовместимой логике. |

Для денежной observability можно задать тарифы в USD за миллион токенов: `CLAUDE_INPUT_USD_PER_MTOK`, `CLAUDE_OUTPUT_USD_PER_MTOK`, `CLAUDE_CACHE_READ_USD_PER_MTOK`, `CLAUDE_CACHE_WRITE_USD_PER_MTOK`. Для Batch используются отдельные `CLAUDE_BATCH_INPUT_USD_PER_MTOK`, `CLAUDE_BATCH_OUTPUT_USD_PER_MTOK`, `CLAUDE_BATCH_CACHE_READ_USD_PER_MTOK`, `CLAUDE_BATCH_CACHE_WRITE_USD_PER_MTOK`. Без них usage сохраняется, а cost остаётся `null`, без выдуманной цены.

Пороги нельзя тюнинговать «на глаз» в production: изменение любого retrieval-параметра меняет fingerprint и должно пройти повторный gold/shadow benchmark.

## Observability и UX

Каждый evaluation run хранит статус, fingerprint components, model/retrieval config, snapshot/revision, timestamps, token usage, latency, cost и связь с paired run. Retrieval trace нормализуется в отдельные run/hit записи и содержит:

- embedding provider/model/dimension;
- безопасный chunk manifest с offsets;
- число embedding/SQL запросов;
- dense, lexical и fused ranks/scores;
- выбранные и отклонённые кандидаты с причиной (`below_threshold`, `top_k_exceeded`);
- `ok`/`no_match`/`degraded` и диагностическую ошибку.

Raw query/transcript не дублируется в retrieval trace: там хранится hash и manifest, а авторитетный текст остаётся в `ai_transcript_cache`.

Админский dashboard показывает за 30 дней количество retrieval runs, degraded/no-match, кандидаты/включения, p50/p95 latency, устаревшие оценки относительно текущего snapshot, rollout и последний успешный experiment. Каталог правил поддерживает server-side pagination, поиск и фильтры, revision/index health, lifecycle, legacy migration, редактирование и retry reindex. Reindex хранится в `qa_reindex_jobs`, забирается worker-ами через `FOR UPDATE SKIP LOCKED`, имеет lease/retry и переживает рестарт процесса; локальный executor только ускоряет выполнение. Ошибка схемы или индексирования отображается как `degraded`/`stale`/`error`, а не как пустая «здоровая» страница.

Карточка review требует подтверждённую цитату либо явный `no_evidence`; пользователю показываются run ID, fingerprint, knowledge revision, retrieval status и stale-признак.

## Gold set и benchmark

Gold set должен быть заморожен и отделён по времени от базы знаний. `validate_temporal_split()` запрещает правила после knowledge cutoff и звонки до/на cutoff, чтобы исключить leakage.

`paired_rag_report()` сравнивает RAG-off и RAG-on на одних и тех же звонках и считает:

- alarm precision, recall, F1, accuracy, false alarms и misses;
- improved/harmed/changed пары и MAE score;
- delta latency, input tokens и cost;
- Recall@K, Precision@K, MRR, false-hit rate и p50/p95 retrieval latency.

Пример вычисления gate:

```python
from call_qa.evaluation.benchmark import paired_rag_report, evaluate_quality_gates

report = paired_rag_report(examples, retrieval_records=retrieval_records, k=3)
quality = evaluate_quality_gates(report)
```

Default quality gates:

- не меньше `30` пар RAG-off/on в контрольной выборке;
- прирост alarm precision не меньше `10 pp`;
- падение recall не больше `2 pp`;
- false-hit rate не выше `5%`;
- retrieval p95 не выше `500 ms`.

Записывайте полный report и результат gates в `qa_rag_experiments.metrics`. Canary/active разрешаются только после успешного experiment; один общий aggregate не заменяет проверку по направлениям и критичным критериям.

## Семантика сбоев

| Ситуация | Безопасное поведение |
|---|---|
| Нет релевантных правил | `no_match`; оценка продолжается без RAG-контекста. |
| Ошибка query embedding/БД retrieval | `degraded`; никаких fallback-правил, ошибка попадает в trace. |
| Provider/model/dimension не совпали | Запрос/индексация отклоняются до обращения к несовместимому pgvector-индексу. |
| Не удалось проиндексировать draft | `index_status=error`, активация запрещена, доступен retry. |
| Не удалось проиндексировать правку active-правила | Правка не заменяет работающую active-версию. |
| Evidence не совпадает с транскриптом | Review отклоняется; пользователь выбирает корректный фрагмент либо `no_evidence`. |
| LLM/ASR не завершились | Успешный immutable cache entry не создаётся; ошибка не маскируется старой оценкой с другим fingerprint. |
| Новая schema недоступна | UI/API показывают degraded/legacy compatibility; unverified legacy не становится active автоматически. |
| Устарел knowledge snapshot | Старый run остаётся воспроизводимым и помечается stale; новый запрос получает новый fingerprint. |

## Проверки перед rollout

```bash
python -m unittest discover -s tests -p "test_ai_qa*.py" -v
npm run build
```

Перед `canary` дополнительно проверьте:

1. миграция завершилась без пропущенных обязательных statements;
2. dashboard не показывает degraded index;
3. все active-правила имеют `verified`/`no_evidence` и `ready` embedding;
4. temporal gold experiment завершён и quality gates пройдены;
5. shadow-пары не ухудшают критичные критерии, а latency/cost укладываются в бюджет;
6. rollback в `shadow` или `off` проверен через админский rollout control.

## ПДн и резидентность

Soniox и Vertex — внешние облачные провайдеры, поэтому перед production нужна юридическая проверка трансграничной передачи и сроков хранения. Embedding provider абстрагирован: `EMBEDDINGS_PROVIDER=selfhost` переключает retrieval на локальный Sentence-Transformers/E5, но смена provider/model/dimension означает новый индексный контракт, переиндексацию правил, новый fingerprint и повторный benchmark.
