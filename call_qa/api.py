"""Слой данных для раздела «ИИ-оценка» (форма контракта фронтенда src/components/call_qa).
Тяжёлые операции (review_payload) запускают реальный пайплайн: GCS → Soniox → Claude."""
from __future__ import annotations
import os
import logging
import tempfile
import threading

from . import config
from .asr import soniox
from .evaluation import criteria as criteria_mod
from .evaluation import criterion_config as cc
from .evaluation import evaluator
from .review import queue as review_queue


def review_queue_list(limit: int = 30) -> list[dict]:
    """Очередь ревью: ИИ-оценённые звонки (текущий тег модели), которые человек ещё не
    проверял. Причины считаются из сохранённой карточки; сортировка — сначала критичное,
    внутри — свежее. Если миграция меты ещё не прошла — fallback на «последние звонки»."""
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT rc.call_id, d.name, u.name, TO_CHAR(c.created_at,'DD.MM HH24:MI'), c.score,
                      rc.payload->'criteria', rc.payload->'asr_mean_conf', rc.created_at
                 FROM ai_review_cache rc
                 JOIN calls c ON c.id = rc.call_id
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN users u ON c.operator_id = u.id
                 LEFT JOIN ai_evaluation_meta m ON m.call_id = rc.call_id AND m.model = rc.model
                WHERE rc.model = %s AND m.review_outcome IS NULL
                ORDER BY rc.created_at DESC LIMIT %s""",
            (config.CLAUDE_MODEL, max(limit * 3, limit)),  # запас: часть окажется «чистой»
        )
        rows = cur.fetchall(); cur.close(); conn.close()
        prio = review_queue.REASON_PRIORITY
        items = []
        for r in rows:
            reasons = review_queue.review_reasons(r[5] or [], r[6])
            items.append({"id": r[0], "direction": r[1], "operator": r[2] or "—",
                          "datetime": r[3], "human_score": r[4], "reasons": reasons or ["ok"],
                          "_sev": min((prio.index(x) for x in reasons), default=len(prio)),
                          "_ts": r[7]})
        items.sort(key=lambda i: (i["_sev"], -(i["_ts"].timestamp() if i["_ts"] else 0)))
        for i in items:
            i.pop("_sev", None); i.pop("_ts", None)
        return items[:limit]
    except Exception:
        logging.exception("ai-qa: очередь по мете недоступна — fallback на последние звонки")
        return _recent_calls_fallback(limit)


def _recent_calls_fallback(limit: int) -> list[dict]:
    """Старое поведение очереди (до появления следа ревью): последние звонки ОП с записью."""
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute(
        """SELECT c.id, d.name, u.name, TO_CHAR(c.created_at,'DD.MM HH24:MI'), c.score
             FROM calls c
             LEFT JOIN directions d ON c.direction_id = d.id
             LEFT JOIN users u ON c.operator_id = u.id
            WHERE c.direction_id = ANY(%s) AND c.audio_path IS NOT NULL AND c.audio_path <> ''
              AND COALESCE(c.is_draft, FALSE) = FALSE
            ORDER BY c.created_at DESC LIMIT %s""",
        (config.OP_DIRECTION_IDS, limit),
    )
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id": r[0], "direction": r[1], "operator": r[2] or "—",
             "datetime": r[3], "human_score": r[4], "reasons": ["new"]} for r in rows]


def _download(audio_path: str, dest: str):
    from google.oauth2 import service_account
    from google.cloud import storage
    sa = config.google_sa_info()
    creds = service_account.Credentials.from_service_account_info(sa)
    bucket, blob = audio_path.split("/", 1)
    storage.Client(project=sa["project_id"], credentials=creds).bucket(bucket).blob(blob).download_to_filename(dest)


def _signed_url(audio_path, minutes=30):
    """Подписанная ссылка на запись в GCS (для прослушивания в браузере)."""
    if not audio_path:
        return None
    try:
        from datetime import timedelta
        from google.oauth2 import service_account
        from google.cloud import storage
        sa = config.google_sa_info()
        creds = service_account.Credentials.from_service_account_info(sa)
        bucket, blob = audio_path.split("/", 1)
        b = storage.Client(project=sa["project_id"], credentials=creds).bucket(bucket).blob(blob)
        return b.generate_signed_url(version="v4", expiration=timedelta(minutes=minutes),
                                     method="GET", response_type="audio/mpeg")
    except Exception:
        return None


def _norm_verdict(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"correct", "ok", "да", "верно", "true", "1"}:
        return "Correct"
    if s in {"incorrect", "error", "нет", "неверно", "false", "0"}:
        return "Incorrect"
    if s in {"n/a", "na", "неприменимо", "-", ""}:
        return "N/A"
    return str(v)


def _ai_score(direction: dict, result: dict):
    """Балл ИИ по той же формуле, что и человеческий (main.jsx): критический Incorrect → 0;
    иначе сумма весов НЕкритических критериев со статусом Correct/N/A. Критерии, которые ИИ
    не может проверить (system_api/manual → Pending), считаем зачётом (benefit of the doubt).
    Но Pending по TRANSCRIPT-критерию = модель не вернула вердикт даже после повтора —
    оценка неполная, балла нет (None): сбой не должен превращаться в незаслуженный зачёт."""
    rows = result.get("per_criterion", [])
    if any(r.get("source") == "transcript" and r.get("verdict") == "Pending" for r in rows):
        return None
    verdict = {r["idx"]: r["verdict"] for r in rows}
    crits = direction.get("criteria", [])
    for c in crits:
        if c.get("is_critical") and verdict.get(c["idx"]) == "Incorrect":
            return 0
    total = 0.0
    for c in crits:
        if c.get("is_critical"):
            continue
        if verdict.get(c["idx"]) in ("Correct", "N/A", "Pending"):
            total += (c.get("weight") or 0)
    return round(total)


def _lines_from_tokens(toks: list[dict]) -> list[dict]:
    """Токены Soniox → диаризованные строки с сегментами (низкая уверенность помечена 'c')."""
    cnt = {}
    for t in toks:
        sp = t.get("speaker")
        if sp is not None:
            cnt[sp] = cnt.get(sp, 0) + 1
    op = max(cnt, key=cnt.get) if cnt else None  # оператор = кто больше говорит
    lines, seg = [], []
    cur_sp = object()

    def flush():
        if seg:
            lines.append({"speaker": "operator" if cur_sp == op else "client", "seg": list(seg)})

    for t in toks:
        sp = t.get("speaker")
        if sp != cur_sp:
            flush(); seg.clear(); cur_sp = sp
        txt = t.get("text", ""); c = t.get("confidence")
        if c is not None and c < config.ASR_CONF_HARD:
            seg.append({"t": txt, "c": round(c, 2)})
        elif seg and "c" not in seg[-1]:
            seg[-1]["t"] += txt
        else:
            seg.append({"t": txt})
    flush()
    return lines


def _cache_get(call_id, model):
    try:
        conn = config.connect_ro(); cur = conn.cursor()
        cur.execute("SELECT payload FROM ai_review_cache WHERE call_id=%s AND model=%s", (call_id, model))
        row = cur.fetchone(); cur.close(); conn.close()
        return row[0] if row else None
    except Exception:
        return None  # таблицы ещё нет


def _cache_put(call_id, model, payload, strict=False):
    try:
        from psycopg2.extras import Json
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_review_cache (call_id, model, payload, created_at)
                   VALUES (%s,%s,%s, now())
                   ON CONFLICT (call_id, model) DO UPDATE SET payload=EXCLUDED.payload, created_at=now()""",
                (call_id, model, Json(payload)))
        conn.close()
    except Exception:
        if strict:
            raise  # пакетная оценка: потеря результата недопустима — наверху ретрай
        pass  # карточка ревью: best-effort (нет RW/таблицы) — просто не кэшируем


def _meta_upsert(call_id, model, payload):
    """Журнал оценки в ai_evaluation_meta: needs_review + причины из карточки.
    Новая оценка сбрасывает след ревью (переоценили → человек проверяет заново).
    Best-effort: без RW/миграции оценка важнее журнала."""
    try:
        from psycopg2.extras import Json
        reasons = review_queue.review_reasons(payload.get("criteria"), payload.get("asr_mean_conf"))
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_evaluation_meta
                     (call_id, direction_id, model, per_criterion, asr_mean_conf, needs_review, review_reasons)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (call_id, model) DO UPDATE SET
                     per_criterion=EXCLUDED.per_criterion, asr_mean_conf=EXCLUDED.asr_mean_conf,
                     needs_review=EXCLUDED.needs_review, review_reasons=EXCLUDED.review_reasons,
                     review_outcome=NULL, reviewed_by=NULL, reviewed_at=NULL, created_at=now()""",
                (call_id, payload.get("direction_id") or 0, model,
                 Json(payload.get("criteria") or []), payload.get("asr_mean_conf"),
                 bool(reasons), Json(reasons)))
        conn.close()
    except Exception:
        logging.exception("ai-qa: не удалось записать ai_evaluation_meta (call %s)", call_id)


def _record_review_outcome(call_id, outcome, reviewer_id=None):
    """Фиксирует итог ревью («confirmed» — человек согласился, «adjudicated» — исправил):
    звонок уходит из очереди. Если меты ещё нет (старый кэш) — создаём её из карточки."""
    try:
        from psycopg2.extras import Json
        model = config.CLAUDE_MODEL
        payload = _cache_get(call_id, model) or {}
        reasons = review_queue.review_reasons(payload.get("criteria"), payload.get("asr_mean_conf"))
        conn = config.connect_rw()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_evaluation_meta
                     (call_id, direction_id, model, per_criterion, asr_mean_conf,
                      needs_review, review_reasons, review_outcome, reviewed_by, reviewed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (call_id, model) DO UPDATE SET
                     review_outcome=EXCLUDED.review_outcome,
                     reviewed_by=EXCLUDED.reviewed_by, reviewed_at=now()""",
                (call_id, payload.get("direction_id") or 0, model,
                 Json(payload.get("criteria") or []), payload.get("asr_mean_conf"),
                 bool(reasons), Json(reasons), outcome, reviewer_id))
        conn.close()
    except Exception:
        logging.exception("ai-qa: не удалось записать итог ревью (call %s)", call_id)


# Параллельное открытие одной карточки (двойной клик, два админа) не должно оплачивать
# ASR+LLM дважды: тяжёлый путь сериализуется на звонок, второй запрос дожидается кэша.
# Защита в пределах процесса; при нескольких воркерах дубль остаётся возможен, но редок.
_inflight_guard = threading.Lock()
_inflight: dict[int, threading.Lock] = {}


def _call_lock(call_id: int) -> threading.Lock:
    with _inflight_guard:
        return _inflight.setdefault(call_id, threading.Lock())


def review_payload(call_id: int, refresh: bool = False) -> dict:
    """Реальная оценка одного звонка в форме контракта карточки ревью. С кэшем в ai_review_cache.
    refresh=True переоценивает ИИ, но переиспользует сохранённый транскрипт (ASR не переплачиваем:
    запись не изменилась)."""
    model = config.CLAUDE_MODEL
    if not refresh:
        cached = _cache_get(call_id, model)
        if cached:
            cached["_cached"] = True
            cached["audio_url"] = _signed_url(cached.get("_audio_path"))
            return cached
    with _call_lock(call_id):
        if not refresh:
            cached = _cache_get(call_id, model)  # пока ждали лок, параллельный запрос уже оценил
            if cached:
                cached["_cached"] = True
                cached["audio_url"] = _signed_url(cached.get("_audio_path"))
                return cached
        return _evaluate_and_cache(call_id, model, refresh)


def _evaluate_and_cache(call_id: int, model: str, refresh: bool) -> dict:
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute(
        """SELECT c.id, c.direction_id, d.name, u.name,
                  TO_CHAR(c.created_at,'DD.MM.YYYY, HH24:MI'), c.score, c.audio_path
             FROM calls c
             LEFT JOIN directions d ON c.direction_id = d.id
             LEFT JOIN users u ON c.operator_id = u.id
            WHERE c.id = %s""", (call_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        raise ValueError("звонок не найден")
    direction_id, audio_path = row[1], row[6]
    if not audio_path:
        raise ValueError("у звонка нет записи")

    asm = lines = None
    if refresh:
        cached = _cache_get(call_id, model)
        if cached and cached.get("_asm"):
            asm, lines = cached["_asm"], cached.get("transcript") or []
    if asm is None:
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "audio.mp3")
            _download(audio_path, dest)
            toks = soniox.transcribe_file(dest)
        full = soniox.assemble(toks)
        asm = {"text": full["text"], "languages": full["languages"],
               "mean_conf": full["mean_conf"], "low_conf_spans": full["low_conf_spans"]}
        lines = _lines_from_tokens(toks)

    direction = criteria_mod.load_direction(direction_id)
    cc.apply_to_direction(direction)
    crit_meta = {c["idx"]: c for c in direction["criteria"]}
    result = evaluator.evaluate(asm["text"], direction, asr_low_spans=asm["low_conf_spans"], use_rag=True)

    criteria = []
    for v in result["per_criterion"]:
        cm = crit_meta.get(v["idx"], {})
        criteria.append({
            "idx": v["idx"], "name": v["name"], "is_critical": bool(cm.get("is_critical")),
            "source": v["source"], "ai": v["verdict"], "conf": v["confidence"],
            "evidence": v["evidence_quote"], "comment": v["comment"], "model": v.get("model"),
        })

    payload = {
        "id": row[0], "direction_id": direction_id, "direction": row[2],
        "operator": row[3] or "—", "datetime": row[4],
        "human_score": row[5], "languages": asm["languages"], "asr_mean_conf": asm["mean_conf"] or 0,
        "transcript": lines, "criteria": criteria,
        "ai_score": _ai_score(direction, result),
        "_audio_path": audio_path, "_asm": asm,
    }
    _cache_put(call_id, model, payload)
    _meta_upsert(call_id, model, payload)
    payload["_cached"] = False
    payload["audio_url"] = _signed_url(audio_path)
    return payload


def criteria_config_get(direction_id: int) -> dict:
    """Критерии направления с текущим источником оценки (таблица + эвристика)."""
    d = criteria_mod.load_direction(direction_id)
    cc.apply_to_direction(d)
    return {
        "direction_id": d["id"], "name": d["name"],
        "criteria": [{"idx": c["idx"], "name": c["name"],
                      "is_critical": bool(c["is_critical"]), "source": c["eval_source"]}
                     for c in d["criteria"]],
    }


def criteria_config_set(direction_id: int, items: list[dict]) -> int:
    """Сохраняет классификацию: items=[{criterion_idx, eval_source}]. Нужен read-write."""
    for it in (items or []):
        cc.set_config(direction_id, it["criterion_idx"], it["eval_source"])
    return len(items or [])


def adjudications_list(direction=None, q=None, limit=200) -> list[dict]:
    """База разборов (RAG). Если таблицы ещё нет — пустой список."""
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        sql = ("""SELECT a.id, d.name, a.criterion_name, a.ai_verdict, a.correct_verdict,
                         a.excerpt, a.reason, a.use_count, u.name, TO_CHAR(a.created_at,'DD.MM.YYYY'),
                         a.not_covered, a.situation
                    FROM qa_adjudications a
                    LEFT JOIN directions d ON a.direction_id = d.id
                    LEFT JOIN users u ON a.created_by = u.id
                   WHERE a.is_active""")
        params = []
        if direction and direction != "all":
            sql += " AND d.name = %s"; params.append(direction)
        if q:
            sql += " AND (a.criterion_name ILIKE %s OR a.excerpt ILIKE %s OR a.reason ILIKE %s)"
            params += [f"%{q}%"] * 3
        sql += " ORDER BY a.created_at DESC LIMIT %s"; params.append(limit)
        cur.execute(sql, params); rows = cur.fetchall(); cur.close(); conn.close()
        return [{"id": r[0], "direction": r[1], "criterion": r[2], "ai": r[3], "correct": r[4],
                 "excerpt": r[5], "reason": r[6], "use_count": r[7] or 0, "by": r[8] or "—", "date": r[9],
                 "not_covered": r[10], "situation": r[11]}
                for r in rows]
    except Exception:
        return []  # qa_adjudications ещё не создана


def _adjudication_exists(call_id, direction_id, criterion_idx, correct_verdict) -> bool:
    """Повторное сохранение того же разбора (звонок открыли дважды) не должно плодить
    дубли в RAG: одинаковые прецеденты вытесняют из top-K разнообразные."""
    try:
        conn = config.connect_ro(); cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM qa_adjudications
                WHERE call_id=%s AND direction_id=%s AND criterion_idx=%s AND correct_verdict=%s
                  AND is_active
                LIMIT 1""",
            (call_id, direction_id, criterion_idx, correct_verdict))
        row = cur.fetchone(); cur.close(); conn.close()
        return row is not None
    except Exception:
        return False


def save_adjudications(call_id, direction_id, items, reviewer_id=None) -> int:
    """Сохраняет исправления человека в RAG (qa_adjudications) и фиксирует итог ревью.
    Пустой items — тоже результат: человек СОГЛАСИЛСЯ с ИИ (confirmed), звонок уходит
    из очереди, а подтверждение остаётся сигналом качества модели."""
    saved = 0
    for it in (items or []):
        if _adjudication_exists(call_id, direction_id, it["criterion_idx"], it["correct_verdict"]):
            continue
        review_queue.on_adjudication(
            direction_id=direction_id, criterion_idx=it["criterion_idx"],
            criterion_name=it.get("criterion_name"), call_id=call_id,
            excerpt=it.get("excerpt", ""), ai_verdict=it.get("ai_verdict"),
            correct_verdict=it["correct_verdict"], reason=it.get("reason", ""),
            not_covered=it.get("not_covered"), situation=it.get("situation"),
            reviewer_id=reviewer_id,
        )
        saved += 1
    if call_id is not None:
        _record_review_outcome(call_id, "adjudicated" if (items or []) else "confirmed", reviewer_id)
    return saved


def _clean_adjudication_patch(body: dict) -> dict:
    """Патч правки разбора: только разрешённые поля, с валидацией.
    correct_verdict — из фиксированного словаря; reason (правило) не может стать пустым;
    необязательные текстовые поля пустая строка очищает в NULL."""
    from .rag.store import EDITABLE_ADJ_FIELDS
    if not isinstance(body, dict):
        raise ValueError("тело запроса должно быть JSON-объектом")
    patch = {}
    for key in EDITABLE_ADJ_FIELDS:
        if key not in body:
            continue
        val = body[key]
        if val is not None and not isinstance(val, str):
            raise ValueError(f"поле {key} должно быть строкой")
        val = val.strip() if isinstance(val, str) else val
        if key == "correct_verdict":
            if val not in ("Correct", "Incorrect", "N/A"):
                raise ValueError("недопустимый вердикт")
            patch[key] = val
        elif key == "reason":
            if not val:
                raise ValueError("правило (reason) не может быть пустым")
            patch[key] = val
        else:
            patch[key] = val or None
    if not patch:
        raise ValueError("нет полей для изменения")
    return patch


def update_adjudication(adj_id: int, body: dict) -> bool:
    """Правка разбора супер-админом (embedding пересчитывается). False — разбора нет."""
    from .rag import store
    return store.update_adjudication(int(adj_id), _clean_adjudication_patch(body))


def delete_adjudication(adj_id: int) -> bool:
    """Удаление разбора супер-админом. False — разбора нет."""
    from .rag import store
    return store.delete_adjudication(int(adj_id))


def refine_adjudication(body: dict) -> dict:
    """ИИ-подсказка формулировки разбора (человек редактирует и сохраняет сам)."""
    from .rag import refine as rag_refine
    return rag_refine.refine_adjudication(
        direction_id=body["direction_id"], criterion_idx=body["criterion_idx"],
        criterion_name=body.get("criterion_name"),
        ai_verdict=body.get("ai_verdict"), ai_comment=body.get("ai_comment"),
        correct_verdict=body["correct_verdict"], reason=body.get("reason", ""),
        excerpt=body.get("excerpt"))


def random_call() -> dict:
    """Случайный оценённый человеком звонок ОП с записью — для теста ИИ-оценки.
    Сначала из ЕЩЁ НЕ оценённых ИИ (каждый вызов = новый сигнал за те же деньги);
    если все уже оценены — любой."""
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    base = """SELECT c.id, d.name, u.name, TO_CHAR(c.created_at,'DD.MM HH24:MI'), c.score
                FROM calls c
                LEFT JOIN directions d ON c.direction_id = d.id
                LEFT JOIN users u ON c.operator_id = u.id
               WHERE c.direction_id = ANY(%s) AND c.audio_path IS NOT NULL AND c.audio_path <> ''
                 AND COALESCE(c.is_draft, FALSE) = FALSE AND c.score IS NOT NULL"""
    cur.execute(base + """ AND NOT EXISTS (SELECT 1 FROM ai_review_cache rc
                                            WHERE rc.call_id = c.id AND rc.model = %s)
                           ORDER BY random() LIMIT 1""",
                (config.OP_DIRECTION_IDS, config.CLAUDE_MODEL))
    row = cur.fetchone()
    if not row:
        cur.execute(base + " ORDER BY random() LIMIT 1", (config.OP_DIRECTION_IDS,))
        row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise ValueError("нет оценённых звонков ОП с записью")
    return {"id": row[0], "direction": row[1], "operator": row[2] or "—",
            "datetime": row[3], "human_score": row[4]}


def evaluations_list(limit=100) -> list[dict]:
    """Уже оценённые ИИ звонки (из кэша) — реальные данные, пусто пока ничего не оценено.
    Один звонок = одна строка (последняя оценка), иначе звонки, оценённые несколькими
    версиями модели, дублировались в списке."""
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT t.call_id, d.name, u.name, TO_CHAR(t.created_at,'DD.MM HH24:MI'), c.score, t.ai
                 FROM (SELECT DISTINCT ON (rc.call_id) rc.call_id, rc.created_at,
                              rc.payload->>'ai_score' AS ai
                         FROM ai_review_cache rc
                        ORDER BY rc.call_id, rc.created_at DESC) t
                 JOIN calls c ON c.id = t.call_id
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN users u ON c.operator_id = u.id
                ORDER BY t.created_at DESC LIMIT %s""", (limit,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [{"id": r[0], "direction": r[1], "operator": r[2] or "—",
                 "datetime": r[3], "human": r[4],
                 "ai": round(float(r[5])) if r[5] is not None else None} for r in rows]
    except Exception:
        return []  # таблицы кэша ещё нет


_VERDICTS = ("Correct", "Incorrect", "N/A")


def _rate(hits, total):
    """{pct, hits, total} или None, если событий не было (не рисуем выдуманный 0%)."""
    return {"pct": round(100 * hits / total), "hits": hits, "total": total} if total else None


def _verdict_metrics(rows) -> dict:
    """Метрики доверия по «сырому» эталону — человеческим оценкам calls.scores.

    rows: (criteria, scores, direction), criteria — в форме карточки ревью.
    Считает матрицу «человек × ИИ» и три главных вопроса:
      alarm_precision — когда ИИ ставит Incorrect, как часто человек согласен;
      recall          — какую долю человеческих нарушений ИИ поймал;
      correct_reliability — когда ИИ ставит Correct, как часто человек согласен.
    «Deficiency» (частичный зачёт) НЕ считается расхождением — у ИИ такого
    вердикта пока нет; учитывается отдельным счётчиком."""
    matrix = {h: {a: 0 for a in _VERDICTS} for h in _VERDICTS}
    per = {}
    deficiency = 0
    for criteria, scores, direction in rows:
        for crit in criteria or []:
            if crit.get("source") != "transcript":
                continue
            idx, ai = crit.get("idx"), crit.get("ai")
            if ai not in _VERDICTS:
                continue  # Pending/сбой — не вердикт
            raw = scores[idx] if isinstance(scores, list) and idx is not None and idx < len(scores) else None
            hv = _norm_verdict(raw)
            if hv == "Deficiency":
                deficiency += 1
                continue
            if hv not in _VERDICTS:
                continue
            matrix[hv][ai] += 1
            d = per.setdefault((direction or "—", crit.get("name") or f"#{idx}"),
                               {"n": 0, "match": 0, "alarms": 0, "alarm_hits": 0, "misses": 0})
            d["n"] += 1
            if hv == ai:
                d["match"] += 1
            if ai == "Incorrect":
                d["alarms"] += 1
                if hv == "Incorrect":
                    d["alarm_hits"] += 1
            elif hv == "Incorrect":
                d["misses"] += 1  # человек видит нарушение, ИИ — нет

    tot = sum(matrix[h][a] for h in _VERDICTS for a in _VERDICTS)
    by_criterion = [
        {"direction": dr, "name": nm, "n": d["n"], "v": round(100 * d["match"] / d["n"]),
         "alarms": d["alarms"], "alarm_hits": d["alarm_hits"],
         "false_alarms": d["alarms"] - d["alarm_hits"], "misses": d["misses"]}
        for (dr, nm), d in per.items()]
    return {
        "total": tot,
        "agreement": round(100 * sum(matrix[v][v] for v in _VERDICTS) / tot) if tot else None,
        "alarm_precision": _rate(matrix["Incorrect"]["Incorrect"],
                                 sum(matrix[h]["Incorrect"] for h in _VERDICTS)),
        "recall": _rate(matrix["Incorrect"]["Incorrect"],
                        sum(matrix["Incorrect"][a] for a in _VERDICTS)),
        "correct_reliability": _rate(matrix["Correct"]["Correct"],
                                     sum(matrix[h]["Correct"] for h in _VERDICTS)),
        "matrix": matrix,
        "deficiency": deficiency,
        "by_criterion": by_criterion,
    }


def _reviewed_metrics(cur):
    """«Чистый» эталон: только звонки, где человек нажал «Подтвердить»/«Сохранить разбор».
    confirmed — все вердикты ИИ одобрены; adjudicated — исправленные критерии берём из
    qa_adjudications, неисправленные считаются одобренными. None — миграции меты ещё нет."""
    try:
        cur.execute("""SELECT call_id, review_outcome, per_criterion
                         FROM ai_evaluation_meta
                        WHERE review_outcome IS NOT NULL AND model = %s""",
                    (config.CLAUDE_MODEL,))
        rows = cur.fetchall()
    except Exception:
        return None  # колонок ещё нет — появятся после деплоя (миграция на старте)
    out = {"confirmed": 0, "adjudicated": 0, "endorsed": 0, "corrected": 0, "alarm_precision": None}
    if not rows:
        return out
    corr = {}
    try:
        # Исправления могут быть и от ревью до появления следа — ключ call+criterion этого не различает,
        # для точности тревог это безопасно (исправление = человек не согласен).
        # Деактивированные RAG-правила остаются следом ревью. Если по тому же критерию
        # разбор создавали повторно, человеческим эталоном считаем самый свежий.
        cur.execute("""SELECT DISTINCT ON (call_id, criterion_idx)
                              call_id, criterion_idx, correct_verdict
                         FROM qa_adjudications
                        WHERE call_id = ANY(%s)
                        ORDER BY call_id, criterion_idx, created_at DESC, id DESC""",
                    ([r[0] for r in rows],))
        corr = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    except Exception:
        pass
    alarms = alarm_hits = 0
    for call_id, outcome, crits in rows:
        out["confirmed" if outcome == "confirmed" else "adjudicated"] += 1
        for c in crits or []:
            if c.get("source") != "transcript" or c.get("ai") not in _VERDICTS:
                continue
            # confirmed означает, что в ТЕКУЩЕМ ревью человек одобрил все verdict'ы ИИ;
            # старые разборы того же звонка не должны превращать это ревью в исправленное.
            hv = (c.get("ai") if outcome == "confirmed"
                  else corr.get((call_id, c.get("idx")), c.get("ai")))
            out["endorsed" if hv == c.get("ai") else "corrected"] += 1
            if c.get("ai") == "Incorrect":
                alarms += 1
                if hv == "Incorrect":
                    alarm_hits += 1
    out["alarm_precision"] = _rate(alarm_hits, alarms)
    return out


def stats() -> dict:
    """Метрики доверия для дашборда. Два эталона: «сырой» (человеческие оценки из
    calls.scores — много данных, но Correct в форме — дефолт) и «чистый» (итоги ревью —
    мало, но человек реально смотрел). Пустые места — честно null/[], без выдуманных цифр."""
    out = {"queue": 0, "evaluated": 0, "agreement": None, "by_criterion": [], "focus": [],
           "alarm_precision": None, "recall": None, "correct_reliability": None,
           "matrix": None, "deficiency": 0, "reviewed": None}
    try:
        conn = config.connect_ro()
        cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute("SELECT COUNT(*) FROM ai_review_cache")
        out["evaluated"] = cur.fetchone()[0]
        try:  # реальный размер очереди ревью; до миграции — свежие звонки, как раньше
            cur.execute("""SELECT COUNT(*) FROM ai_review_cache rc
                             LEFT JOIN ai_evaluation_meta m ON m.call_id = rc.call_id AND m.model = rc.model
                            WHERE rc.model = %s AND m.review_outcome IS NULL""", (config.CLAUDE_MODEL,))
            out["queue"] = cur.fetchone()[0]
        except Exception:
            cur.execute(
                """SELECT COUNT(*) FROM calls
                    WHERE direction_id = ANY(%s) AND audio_path IS NOT NULL AND audio_path <> ''
                      AND COALESCE(is_draft, FALSE) = FALSE AND created_at > NOW() - INTERVAL '7 days'""",
                (config.OP_DIRECTION_IDS,))
            out["queue"] = cur.fetchone()[0]

        # Сырой эталон: последняя оценка каждого звонка (без дублей по тегам моделей).
        cur.execute(
            """SELECT t.criteria, c.scores, t.direction
                 FROM (SELECT DISTINCT ON (rc.call_id) rc.call_id,
                              rc.payload->'criteria' AS criteria,
                              rc.payload->>'direction' AS direction
                         FROM ai_review_cache rc
                        ORDER BY rc.call_id, rc.created_at DESC) t
                 JOIN calls c ON c.id = t.call_id
                WHERE c.scores IS NOT NULL""")
        m = _verdict_metrics(cur.fetchall())
        for k in ("agreement", "alarm_precision", "recall", "correct_reliability", "matrix", "deficiency"):
            out[k] = m[k]
        out["by_criterion"] = sorted([r for r in m["by_criterion"] if r["n"] >= 3], key=lambda x: x["v"])
        # «Где отрабатывать»: критерии, генерирующие ложные тревоги и пропуски.
        out["focus"] = sorted([r for r in m["by_criterion"] if r["false_alarms"] or r["misses"]],
                              key=lambda x: -(x["false_alarms"] + x["misses"]))[:10]
        try:  # сколько правил (разборов) уже накоплено по каждому проблемному критерию
            cur.execute("""SELECT COALESCE(d.name, '—'), a.criterion_name, COUNT(*)
                             FROM qa_adjudications a LEFT JOIN directions d ON d.id = a.direction_id
                            WHERE a.is_active
                            GROUP BY 1, 2""")
            rules = {(r[0], r[1]): r[2] for r in cur.fetchall()}
            for r in out["focus"]:
                r["rules"] = rules.get((r["direction"], r["name"]), 0)
        except Exception:
            pass
        out["reviewed"] = _reviewed_metrics(cur)
        cur.close(); conn.close()
    except Exception:
        logging.exception("ai-qa stats failed")
    return out
