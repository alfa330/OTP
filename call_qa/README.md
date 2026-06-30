# call_qa — ИИ-оценка звонков отдела продаж

Ежедневная автоматическая оценка звонков ОП по мониторинговой шкале: ASR → оценка на Claude → результат рядом с человеческими оценками. Спорное уходит в ревью, разборы человека сохраняются в RAG и подтягиваются в будущие оценки («обучение» без роста промпта).

## Поток данных (ежедневный batch)

```
GCS (записи)  →  Soniox ASR        →  Claude (Opus 4.8)        →  calls (evaluator='AI')
                 диаризация +          критерии направления +       + ai_evaluation_meta
                 confidence            RAG-разборы (top-K)          (confidence, цитаты)
                                            │                              │
                                            ▼                              ▼
                                       retrieve()                    review_queue
                                            ▲                              │
                                       qa_adjudications  ◄── авто-сохранение ◄── разбор человека
                                       (pgvector)
```

## Структура

```
call_qa/
  config.py              # env, модели, регион, пороги, id направлений ОП
  asr/
    soniox.py            # Soniox: upload→transcribe→poll→токены, диаризация + неуверенность
  embeddings/
    provider.py          # абстракция embed(texts); реализация Vertex (+ задел self-host)
  rag/
    schema.sql           # DDL (источник правды): criterion_config, ai_evaluation_meta, qa_adjudications
    migrate.py           # применяет schema.sql к РАБОЧЕЙ БД (идемпотентно) — сам .sql не применяется
    store.py             # save_adjudication() / retrieve() через pgvector
  evaluation/
    criteria.py          # загрузка directions.criteria по направлению
    evaluator.py         # сборка промпта + вызов Claude (structured outputs)  [нужен API-ключ]
    output_schema.py     # JSON-схема результата (1:1 к scores[])
  review/
    queue.py             # правила маршрутизации в ревью + хук авто-сохранения в RAG
  prompts/
    evaluator_system.md  # системный промпт оценщика (шаблон)
  pipeline.py            # оркестратор дневного прогона
```

## Решения (зафиксированы)

| Слой | Выбор |
|---|---|
| ASR | **Soniox** `stt-async-v5`, `language_hints=["kk","ru"]`, диаризация + confidence |
| LLM | **Claude Opus 4.8**, structured outputs + prompt caching + Batch API |
| Эмбеддинги | **Google Vertex `text-multilingual-embedding-002`** (`asia-southeast1`), за абстракцией провайдера |
| RAG | **pgvector** в существующем Postgres (без отдельной вектор-БД, без LangChain) |
| Хостинг | Render, без GPU |

## Статус

- Этап 0 ✓ — бенч ASR (выбран Soniox), эмбеддинги (Vertex проверен на kk/ru), доступы.
- Этап 1 — оценщик на Claude (нужен `ANTHROPIC_API_KEY`).
- Этап 2 — RAG (таблица + авто-сохранение на ревью + retrieval).
- Этап 3 — дневной batch-пайплайн + очередь ревью.
- Этап 4 — страница RAG в админке + раскатка по «надёжным» критериям.

## Важно
- Запись в БД (`qa_adjudications`, `ai_evaluation_meta`) требует **read-write** строки подключения; сейчас в окружении только `DATABASE_URL_READONLY` (для чтения критериев/звонков).
- Резидентность ПДн (Закон РК №94-V): Soniox/Vertex — иностранное облако (трансгран. передача). Провайдеры спрятаны за абстракцией → переключение на self-host в РК без переписывания.
