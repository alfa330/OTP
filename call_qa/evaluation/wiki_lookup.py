"""Оценщик сам ищет в вики то, чего ему не хватает для вердикта.

Владелец, 23.10.2026: «Надо сделать так, чтобы оценщик мог делать поиск по вики,
когда ему не хватает информации, — это одна из самых важных черт». До этого у
оценщика доступа к вики не было вовсе: на переписке 61480 он писал «наличие
акции у парка QAZAQ установить невозможно», хотя статья «Все акции» это говорит.

Как устроено — в два хода перед оценкой:

1. ПЛАН. Модель получает критерии, разговор и список статей вики отдела и
   отвечает, какие ФАКТЫ компании ей нужны, чтобы оценить по существу, — в виде
   поисковых запросов по критериям (``plan``). Не нужны — пустой список.
2. ПОИСК. Каждый запрос идёт тем же гибридным поиском, что у ИИ-помощника вики
   (wiki.ai.retrieve.search_hybrid: смысл + слова + названия), по периметру
   отдела (``perimeter``): статьи пространств отдела, пригодные для ИИ —
   опубликованные, не «строгий режим», не закрытые от ИИ ни сами, ни разделом.
   Найденные выдержки уходят в сообщение оценщику блоком «ВЫДЕРЖКИ ИЗ ВИКИ»
   (evaluator.build_eval_body) с подписью статьи, раздела и даты; архивные и
   просроченные — с пометкой (wiki.ai.currency), как у помощника.

Почему не инструмент (tool calling) внутри вызова модели: оценка идёт через трёх
провайдеров с разной поддержкой инструментов и через замороженные тела пакета
(batch_eval), а каждый дополнительный ход модели — ещё 60–90 с «мышления» GLM.
Один дешёвый ход плана (reasoning low) решает ту же задачу: модель выбирает, что
искать, не зная заранее ответа.

Что записывается: план, запросы, найденные куски и причина, по которой поиск не
состоялся (``trace``), — в payload прогона (`_wiki_trace`), а карточка получает
короткий список прочитанных статей (`wiki_sources`). Сбой любого шага оценку не
останавливает: без справки оценка идёт как раньше, причина — в трассе и в логе.
"""
from __future__ import annotations

import contextlib
import logging
import time

from .. import config
from .. import llm

VERSION = "wiki-search-v1"

# Потолки. Запросов — чтобы план не превратился в опрос всей вики; кусков и
# знаков — чтобы справка не вытеснила из контекста сам разговор (кусок помощника
# ≈ 1 100 знаков, см. wiki/ai/chunker.py).
MAX_QUERIES = int(config.env("AI_QA_WIKI_MAX_QUERIES", "8"))
MAX_CHUNKS = int(config.env("AI_QA_WIKI_MAX_CHUNKS", "12"))
MAX_CHARS = int(config.env("AI_QA_WIKI_MAX_CHARS", "16000"))
PER_QUERY = int(config.env("AI_QA_WIKI_PER_QUERY", "6"))
MAX_TITLES = 200
# Уровень рассуждений плана у GLM: план — короткий список запросов, «high»
# тратил бы на него столько же, сколько на саму оценку.
PLANNER_EFFORT = config.env("AI_QA_WIKI_PLANNER_EFFORT", "low")
ENABLED = str(config.env("AI_QA_WIKI_SEARCH", "1")).strip().lower() in {"1", "true", "yes", "on"}

_PERIMETER_SQL = """
SELECT DISTINCT a.id, a.title
  FROM wiki_space_departments sd
  JOIN departments d ON d.id = sd.department_id AND lower(d.code) = %(code)s
  JOIN wiki_spaces sp ON sp.id = sd.space_id AND sp.status = 'active'
  JOIN wiki_sections sec ON sec.space_id = sp.id AND sec.status = 'active'
  JOIN wiki_article_sections s ON s.section_id = sec.id
  JOIN wiki_articles a ON a.id = s.article_id
 WHERE a.status = 'published' AND NOT a.strict_mode AND NOT a.ai_opt_out
   AND NOT EXISTS (SELECT 1
                     FROM wiki_article_sections s2
                     JOIN wiki_sections sec2 ON sec2.id = s2.section_id
                    WHERE s2.article_id = a.id AND sec2.ai_opt_out)
   AND EXISTS (SELECT 1 FROM wiki_ai_chunks c WHERE c.article_id = a.id)
 ORDER BY a.title
"""

_UPDATED_SQL = "SELECT id, updated_at FROM wiki_articles WHERE id = ANY(%s)"

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion_idx": {"type": "integer"},
                    "query": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["criterion_idx", "query", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["queries"],
    "additionalProperties": False,
}

_PLAN_SYSTEM = """Ты — помощник оценщика качества работы операторов. Сейчас ты НЕ оцениваешь разговор. \
Твоя задача — решить, какие ФАКТЫ из базы знаний компании (вики: инструкции операторам, справочники, \
действующие акции и их условия по таксопаркам, тарифы, комиссии, процессы регистрации и поддержки) нужны, \
чтобы оценить разговор по критериям ПО СУЩЕСТВУ: верно ли оператор сообщил условия; обязан ли он был что-то \
предложить или сделать (например, озвучить действующую акцию парка после регистрации); существует ли \
услуга, тариф, правило, о которых шла речь; правильно ли он направил человека.

Правила:
1. По каждому критерию подумай: без какого факта компании вердикт не поставить? Если критерий проверяется \
только по самому разговору (вежливость, грамотность, структура, приветствие) — запросов по нему нет.
2. Запрос — как его задал бы сотрудник в поиске по вики: конкретный, с названием парка, акции, услуги, \
тарифа, города, если они названы в разговоре; без общих слов и без цитат из разговора целиком. \
Пример: «действующие акции для новых водителей таксопарк Qazaq при регистрации», а не «проверить акции».
3. Не больше {max_queries} запросов на весь разговор, по одному-два на критерий. Лучше два точных, чем \
пять похожих.
4. Ниже — названия статей вики, которые доступны поиску. Опирайся на них: если нужная тема есть в \
названии, запрос должен совпадать с ней по смыслу.
5. Если фактов из вики не нужно — верни пустой список.

Верни строго JSON по схеме:
{{"queries": [{{"criterion_idx": <номер критерия из списка>, "query": "<поисковый запрос>", \
"reason": "<какой факт нужен и зачем, одной фразой>"}}]}}"""

_PLAN_USER = ("КРИТЕРИИ ОЦЕНКИ:\n{criteria}\n\n"
              "СТАТЬИ ВИКИ, ДОСТУПНЫЕ ПОИСКУ:\n{titles}\n\n"
              "{transcript_label}:\n{transcript}\n\n"
              "Какие факты из вики нужны, чтобы оценить этот разговор по критериям? "
              "Верни JSON с запросами.")


def empty_trace(status: str, **extra) -> dict:
    return {"version": VERSION, "status": status, "queries": [], "chunks": [],
            "perimeter": {"articles": 0}, "errors": [], "latency_ms": 0, **extra}


def perimeter(cur, department_code) -> dict:
    """Статьи вики пространств отдела, пригодные для ИИ: {article_ids, titles}."""
    code = str(department_code or "").strip().lower()
    if not code:
        return {"article_ids": [], "titles": []}
    cur.execute(_PERIMETER_SQL, {"code": code})
    rows = cur.fetchall()
    return {"article_ids": [int(row[0]) for row in rows],
            "titles": [(int(row[0]), str(row[1] or "")) for row in rows]}


def _criteria_block(criteria: list[dict]) -> str:
    from .evaluator import _criteria_block as block
    return block(criteria)


def plan(*, criteria: list[dict], transcript: str, titles: list, subject_kind: str,
         model: str) -> dict:
    """Ход 1: модель решает, что искать. Возвращает {queries: [{criterion_idx, query,
    reason}], meta}. Ошибки провайдера пробрасываются — их ловит prepare."""
    from .evaluator import _TRANSCRIPT_LABEL
    known = {int(c["idx"]) for c in criteria}
    titles_text = "\n".join(f"- {title}" for _id, title in titles[:MAX_TITLES]) or "(список пуст)"
    system = _PLAN_SYSTEM.format(max_queries=MAX_QUERIES)
    user = _PLAN_USER.format(
        criteria=_criteria_block(criteria), titles=titles_text,
        transcript_label=_TRANSCRIPT_LABEL.get(subject_kind, "ТРАНСКРИПТ ЗВОНКА"),
        transcript=transcript)
    body = llm.build_body(model=model, system=system, user=user, schema=_PLAN_SCHEMA,
                          max_tokens=1500, effort=PLANNER_EFFORT)
    answer = llm.post_body(body, timeout=120.0, include_meta=True)
    meta = answer.get("_llm_meta") or {}
    queries, seen = [], set()
    for item in answer.get("queries") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("criterion_idx"))
        except (TypeError, ValueError):
            continue
        query = " ".join(str(item.get("query") or "").split())[:200]
        if idx not in known or len(query) < 3:
            continue
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        queries.append({"criterion_idx": idx, "query": query,
                        "reason": " ".join(str(item.get("reason") or "").split())[:300]})
        if len(queries) >= MAX_QUERIES:
            break
    return {"queries": queries, "meta": {"model": meta.get("model") or model,
                                         "usage": meta.get("usage") or {},
                                         "latency_ms": meta.get("latency_ms")}}


def embed_queries(queries: list[dict]) -> dict:
    """Векторы запросов (Vertex, как у помощника вики) — одним обращением и ДО
    открытия соединения с базой: внешний вызов не должен держать соединение. Без
    векторов поиск идёт по словам и помечается degraded."""
    from wiki.ai import embed as wiki_embed
    texts = [query["query"] for query in queries]
    try:
        vectors = list(wiki_embed.embed_queries(texts))
        if len(vectors) != len(texts):
            raise RuntimeError(f"векторов {len(vectors)} на {len(texts)} запросов")
    except Exception as exc:  # noqa: BLE001 — поиск по словам остаётся
        return {"vectors": [None] * len(texts), "degraded": True,
                "errors": [f"embed: {type(exc).__name__}: {str(exc)[:200]}"]}
    return {"vectors": vectors, "errors": [], "degraded": False}


def search(cur, *, article_ids: list[int], queries: list[dict], vectors=None) -> dict:
    """Ход 2: гибридный поиск помощника вики по каждому запросу. Куски без повторов,
    самые ранние (по порядку запросов и выдачи) — в пределах потолков.
    vectors — из embed_queries, по одному на запрос (None — только по словам)."""
    from wiki.ai import currency as wiki_currency
    from wiki.ai import retrieve as wiki_retrieve
    found_rows = []
    for index, query in enumerate(queries):
        text = query["query"]
        vector = vectors[index] if vectors and index < len(vectors) else None
        found = wiki_retrieve.search_hybrid(
            cur, article_ids=article_ids, query=text, query_vector=vector,
            limit=PER_QUERY, per_article=3)
        rows = wiki_currency.mark_chunks(found["rows"])
        query["found"] = len(rows)
        query["chunk_ids"] = [int(row["chunk_id"]) for row in rows]
        found_rows.extend((query, row) for row in rows)
    # Под потолки сначала идут действующие выдержки, архивные и просроченные — после:
    # таблица «Актуальные акции 2026» не должна проигрывать место «ВСЕ ПАРКИ АРХИВ».
    chunks, seen, total = [], set(), 0
    for query, row in sorted(found_rows, key=lambda item: bool(item[1].get("stale"))):
        chunk_id = int(row["chunk_id"])
        if chunk_id in seen:
            continue
        body = str(row.get("text") or "")
        if len(chunks) >= MAX_CHUNKS or total + len(body) > MAX_CHARS:
            continue
        seen.add(chunk_id)
        total += len(body)
        chunks.append({
            "chunk_id": chunk_id, "article_id": int(row["article_id"]),
            "title": row.get("title") or "", "slug": row.get("slug") or "",
            "heading_path": row.get("heading_path") or "", "text": body,
            "criterion_idx": int(query["criterion_idx"]),
            "query": query["query"], "stale_note": row.get("stale_note") or "",
            "score": float(row["score"]) if row.get("score") is not None else None,
            "similarity": (float(row["similarity"])
                           if row.get("similarity") is not None else None),
            "found_by": row.get("found_by"),
        })
    if chunks:
        cur.execute(_UPDATED_SQL, ([chunk["article_id"] for chunk in chunks],))
        updated = {int(row[0]): row[1] for row in cur.fetchall()}
        for chunk in chunks:
            moment = updated.get(chunk["article_id"])
            chunk["updated_at"] = moment.isoformat() if hasattr(moment, "isoformat") else moment
    return {"chunks": chunks}


def _date(value) -> str:
    raw = str(value or "")[:10]
    return ".".join(reversed(raw.split("-"))) if len(raw) == 10 and raw[4] == "-" else ""


def render(chunks: list[dict]) -> str:
    """Выдержки — текстом для оценщика, пронумерованные, с подписью статьи, раздела,
    даты обновления и пометкой свежести (как у помощника вики)."""
    parts = []
    for number, chunk in enumerate(chunks, start=1):
        label = f"статья «{chunk.get('title') or '—'}»"
        if chunk.get("heading_path"):
            label += f", раздел «{chunk['heading_path']}»"
        date = _date(chunk.get("updated_at"))
        if date:
            label += f"; обновлена {date}"
        if chunk.get("stale_note"):
            label = f"⚠ {chunk['stale_note']}. {label}"
        parts.append(f"[{number}; к критерию {chunk.get('criterion_idx')}; {label}]\n{chunk.get('text') or ''}")
    return "\n\n".join(parts)


def sources(chunks: list[dict]) -> list[dict]:
    """Короткий список прочитанных статей для карточки: без текста, по статье одна
    строка, разделы — списком."""
    out, by_article = [], {}
    for chunk in chunks:
        item = by_article.get(chunk["article_id"])
        if item is None:
            item = {"article_id": chunk["article_id"], "title": chunk.get("title") or "",
                    "slug": chunk.get("slug") or "", "updated_at": chunk.get("updated_at"),
                    "headings": [], "criterion_idxs": [], "stale": False}
            by_article[chunk["article_id"]] = item
            out.append(item)
        heading = chunk.get("heading_path") or ""
        if heading and heading not in item["headings"]:
            item["headings"].append(heading)
        if chunk.get("criterion_idx") not in item["criterion_idxs"]:
            item["criterion_idxs"].append(chunk.get("criterion_idx"))
        item["stale"] = item["stale"] or bool(chunk.get("stale_note"))
    return out


def prepare(*, criteria: list[dict], transcript: str, subject_kind: str, department,
            model: str, connect=None) -> dict:
    """План → поиск → текст для промпта. Возвращает {wiki_text, trace, sources}.

    Любой сбой — пустая справка и причина в трассе: оценка без вики лучше, чем
    оценка, упавшая из-за вики. connect — фабрика соединения только для чтения
    (по умолчанию config.connect_ro)."""
    started = time.perf_counter()
    if not ENABLED:
        return {"wiki_text": "", "trace": empty_trace("disabled"), "sources": []}
    if not criteria or not str(transcript or "").strip():
        return {"wiki_text": "", "trace": empty_trace("skipped"), "sources": []}
    # Без отдела нет и периметра: вики делится между отделами по пространствам
    # (wiki_space_departments), и до базы в этом случае не ходим.
    if not str(department or "").strip():
        return {"wiki_text": "", "trace": empty_trace("no_department"), "sources": []}
    trace = empty_trace("ok", department=str(department or ""))
    try:
        # Соединение с базой — только на время запросов к ней: ни ход плана, ни
        # векторы запросов (внешние вызовы на десятки секунд) его не держат.
        with _reading(connect) as cur:
            scope = perimeter(cur, department)
        trace["perimeter"] = {"articles": len(scope["article_ids"])}
        if not scope["article_ids"]:
            trace["status"] = "no_perimeter"
            return {"wiki_text": "", "trace": trace, "sources": []}
        planned = plan(criteria=criteria, transcript=transcript, titles=scope["titles"],
                       subject_kind=subject_kind, model=model)
        trace["planner"] = planned["meta"]
        trace["queries"] = planned["queries"]
        if not planned["queries"]:
            trace["status"] = "no_queries"
            return {"wiki_text": "", "trace": trace, "sources": []}
        embedded = embed_queries(planned["queries"])
        trace["errors"] = embedded["errors"]
        with _reading(connect) as cur:
            found = search(cur, article_ids=scope["article_ids"], queries=planned["queries"],
                           vectors=embedded["vectors"])
        trace["chunks"] = found["chunks"]
        if embedded["degraded"]:
            trace["status"] = "degraded"
        elif not found["chunks"]:
            trace["status"] = "no_match"
        return {"wiki_text": render(found["chunks"]), "trace": trace,
                "sources": sources(found["chunks"])}
    except Exception as exc:  # noqa: BLE001 — справка не должна ронять оценку
        logging.exception("ai-qa: поиск по вики для оценки не удался")
        trace["status"] = "failed"
        trace["errors"] = [*trace.get("errors", []), f"{type(exc).__name__}: {str(exc)[:300]}"]
        return {"wiki_text": "", "trace": trace, "sources": []}
    finally:
        trace["latency_ms"] = round((time.perf_counter() - started) * 1000)


@contextlib.contextmanager
def _reading(connect=None):
    """Курсор только для чтения; соединение закрывается на выходе."""
    conn = (connect or config.connect_ro)()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        yield cur
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
