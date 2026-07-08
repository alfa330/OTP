# -*- coding: utf-8 -*-
"""Пакетная фоновая ИИ-оценка уже оценённых людьми звонков через Anthropic Batch API (−50%).

Зачем: теневой режим — массовая сверка ИИ↔человек за прошлый период + прогрев кэша карточек.
Поток: выборка звонков месяца (fallback на следующий, если мало) → GCS → Soniox ASR
(параллельно) → ОДИН батч в /v1/messages/batches → поллинг → сборка карточек →
ai_review_cache (карточки открываются мгновенно, дашборд согласия получает данные).

Устойчивость: транскрипты и batch_id сохраняются в --workdir; повторный запуск продолжает
с места остановки (уже закэшированные под текущим тегом звонки пропускаются всегда).

Запуск:  python -m call_qa.batch_eval --month 2026-06 --fallback-month 2026-07 --min-calls 50
Нужны env: ключ Claude, SONIOX_API_KEY, GOOGLE_APPLICATION_CREDENTIALS_CONTENT,
           read-write БД (DATABASE_URL или POSTGRES_*)."""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from . import config
from . import llm
from .asr import soniox
from .evaluation import criteria as criteria_mod
from .evaluation import criterion_config as cc
from .evaluation import evaluator
from .api import _download, _lines_from_tokens, _ai_score, _cache_put

# Цены Batch API для claude-opus-4-8, $/1M токенов (−50% от обычных 5/25).
_PRICE = {"input": 2.5, "output": 12.5, "cache_write": 3.125, "cache_read": 0.25}


# Windows-консоль может быть в cp1251 — не падаем на юникоде в логах.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(msg, flush=True)


def _retry(fn, *, tries=6, delay=20, what=""):
    """Длинный фоновый прогон не должен умирать от моргнувшей сети/DNS."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == tries:
                raise
            log(f"сетевой сбой ({what or 'запрос'}): {e} — повтор {attempt}/{tries - 1} через {delay}с")
            time.sleep(delay)


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    nxt = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"
    return f"{month}-01", nxt


def select_calls(month: str, fallback_month: str | None, min_calls: int, limit: int | None) -> list[dict]:
    """Оценённые людьми звонки ОП за месяц, ещё не оценённые ИИ под текущим тегом модели.
    Если их меньше min_calls — добавляется fallback-месяц."""
    def q(mon):
        lo, hi = _month_bounds(mon)
        conn = config.connect_ro(); cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT c.id, c.direction_id, d.name, u.name,
                      TO_CHAR(c.created_at,'DD.MM.YYYY, HH24:MI'), c.score, c.audio_path
                 FROM calls c
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN users u ON c.operator_id = u.id
                WHERE c.direction_id = ANY(%s) AND c.score IS NOT NULL
                  AND c.audio_path IS NOT NULL AND c.audio_path <> ''
                  AND COALESCE(c.is_draft, FALSE) = FALSE
                  AND c.created_at >= %s AND c.created_at < %s
                  AND NOT EXISTS (SELECT 1 FROM ai_review_cache rc
                                   WHERE rc.call_id = c.id AND rc.model = %s)
                ORDER BY c.created_at""",
            (config.OP_DIRECTION_IDS, lo, hi, config.CLAUDE_MODEL))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [{"id": r[0], "direction_id": r[1], "direction": r[2], "operator": r[3] or "—",
                 "datetime": r[4], "human_score": r[5], "audio_path": r[6]} for r in rows]

    calls = q(month)
    log(f"выборка {month}: {len(calls)} звонков (оценены человеком, без ИИ-оценки текущим тегом)")
    if len(calls) < min_calls and fallback_month:
        extra = q(fallback_month)
        log(f"мало (<{min_calls}) → добавляю {fallback_month}: +{len(extra)}")
        calls += extra
    if limit:
        calls = calls[:limit]
        log(f"ограничение --limit: берём первые {len(calls)}")
    return calls


def asr_stage(calls: list[dict], workdir: str, workers: int) -> dict:
    """GCS → Soniox для всех звонков (параллельно). Готовые транскрипты копятся в
    transcripts.jsonl — при перезапуске не распознаются заново. Возвращает {call_id: rec}."""
    path = os.path.join(workdir, "transcripts.jsonl")
    done: dict[int, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                done[rec["call_id"]] = rec
        log(f"ASR: {len(done)} транскриптов уже готово (из {path})")

    todo = [c for c in calls if c["id"] not in done]
    if not todo:
        return done
    log(f"ASR: распознаю {len(todo)} звонков ({workers} параллельно)…")
    lock_write = __import__("threading").Lock()

    def one(call):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "audio.mp3")
            _download(call["audio_path"], dest)
            toks = soniox.transcribe_file(dest)
        asm = soniox.assemble(toks)
        slim = [{k: t.get(k) for k in ("text", "speaker", "confidence")} for t in toks]
        rec = {"call_id": call["id"], "toks": slim,
               "asm": {"text": asm["text"], "languages": asm["languages"],
                       "mean_conf": asm["mean_conf"], "low_conf_spans": asm["low_conf_spans"]}}
        with lock_write:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_retry, (lambda c=c: one(c)), tries=2, delay=15,
                          what=f"ASR call {c['id']}"): c["id"] for c in todo}
        for fu in as_completed(futs):
            cid = futs[fu]
            try:
                done[cid] = fu.result(); ok += 1
            except Exception as e:
                fail += 1
                log(f"ASR FAIL call={cid}: {e}")
            if (ok + fail) % 20 == 0:
                log(f"ASR: {ok + fail}/{len(todo)} (ошибок {fail})")
    log(f"ASR готово: +{ok}, ошибок {fail}, всего транскриптов {len(done)}")
    return done


def _dir_cache() -> dict:
    cachedirs = {}

    def get(direction_id):
        if direction_id not in cachedirs:
            d = criteria_mod.load_direction(direction_id)
            cc.apply_to_direction(d)
            cachedirs[direction_id] = {
                "direction": d,
                "t_crits": [c for c in d["criteria"] if c["eval_source"] == cc.TRANSCRIPT],
            }
        return cachedirs[direction_id]
    return get


def submit_batch(calls: list[dict], transcripts: dict, workdir: str, get_dir) -> str | None:
    """Собирает батч оценок и отправляет. Возвращает batch_id (или существующий из workdir)."""
    marker = os.path.join(workdir, "batch_id.txt")
    if os.path.exists(marker):
        bid = open(marker).read().strip()
        log(f"нашёл незавершённый батч {bid} — продолжаю его")
        return bid

    requests_ = []
    for call in calls:
        rec = transcripts.get(call["id"])
        if not rec:
            continue  # ASR не удался — попадёт в следующий запуск
        info = get_dir(call["direction_id"])
        body = evaluator.build_eval_body(
            rec["asm"]["text"], info["direction"], info["t_crits"],
            asr_low_spans=rec["asm"]["low_conf_spans"], use_rag=True,
            model=config.CLAUDE_MODEL_BULK)
        requests_.append({"custom_id": f"call-{call['id']}", "params": body})
    if not requests_:
        log("нечего отправлять в батч")
        return None
    log(f"отправляю батч: {len(requests_)} оценок, модель {config.CLAUDE_MODEL_BULK}")

    def _post():
        r = httpx.post(llm.BATCHES_URL, json={"requests": requests_},
                       headers=llm._headers(), timeout=300.0)
        r.raise_for_status()
        return r.json()
    bid = _retry(_post, what="создание батча")["id"]
    open(marker, "w").write(bid)
    log(f"батч создан: {bid}")
    return bid


def poll_batch(batch_id: str, interval: int = 30) -> dict:
    """Ждёт завершения батча (обычно < 1 часа)."""
    def _get():
        r = httpx.get(f"{llm.BATCHES_URL}/{batch_id}", headers=llm._headers(), timeout=60.0)
        r.raise_for_status()
        return r.json()

    while True:
        st = _retry(_get, what="статус батча")
        if st.get("processing_status") == "ended":
            log(f"батч завершён: {st.get('request_counts')}")
            return st
        log(f"батч в работе: {st.get('request_counts')}")
        time.sleep(interval)


def process_results(batch: dict, calls: list[dict], transcripts: dict, workdir: str, get_dir) -> dict:
    """Результаты батча → карточки → ai_review_cache. Невозвращённые критерии добираются
    синхронным повтором; упавшие запросы — синхронной оценкой (evaluate)."""
    by_call = {c["id"]: c for c in calls}
    usage_tot = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    stats = {"ok": 0, "errored": 0, "no_score": 0, "pairs": []}

    def _results():
        rr = httpx.get(batch["results_url"], headers=llm._headers(), timeout=300.0)
        rr.raise_for_status()
        return rr.text
    results_text = _retry(_results, what="результаты батча")
    for line in results_text.splitlines():
        item = json.loads(line)
        cid = int(item["custom_id"].split("-")[1])
        call = by_call.get(cid)
        rec = transcripts.get(cid)
        if not call or not rec:
            continue
        info = get_dir(call["direction_id"])
        model = config.CLAUDE_MODEL_BULK

        if item["result"]["type"] == "succeeded":
            msg = item["result"]["message"]
            u = msg.get("usage", {})
            usage_tot["input"] += u.get("input_tokens", 0)
            usage_tot["output"] += u.get("output_tokens", 0)
            usage_tot["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
            usage_tot["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
            try:
                parsed = llm.parse_message(msg)
            except Exception:
                parsed = {"per_criterion": [], "overall_comment": ""}
        else:
            log(f"call {cid}: батч-запрос {item['result']['type']} → синхронная оценка")
            stats["errored"] += 1
            parsed = {"per_criterion": [], "overall_comment": ""}

        by_idx = evaluator._collect_verdicts(parsed.get("per_criterion"))
        missing = [c for c in info["t_crits"] if c["idx"] not in by_idx]
        if missing:  # обрыв/дубли/ошибка батча — добор одним синхронным вызовом
            try:
                retry = _retry(lambda: evaluator._claude_eval(
                    rec["asm"]["text"], info["direction"], missing,
                    asr_low_spans=rec["asm"]["low_conf_spans"], use_rag=True, model=model),
                    tries=3, what=f"добор call {cid}")
                for idx, v in evaluator._collect_verdicts(retry.get("per_criterion")).items():
                    by_idx.setdefault(idx, v)
            except Exception as e:
                log(f"call {cid}: повтор не удался: {e}")
        model_by_idx = {idx: model for idx in by_idx}
        result = evaluator.assemble_results(info["direction"], by_idx, model_by_idx,
                                            overall_comment=parsed.get("overall_comment", ""))

        crit_meta = {c["idx"]: c for c in info["direction"]["criteria"]}
        criteria = [{
            "idx": v["idx"], "name": v["name"], "is_critical": bool(crit_meta.get(v["idx"], {}).get("is_critical")),
            "source": v["source"], "ai": v["verdict"], "conf": v["confidence"],
            "evidence": v["evidence_quote"], "comment": v["comment"], "model": v.get("model"),
        } for v in result["per_criterion"]]
        score = _ai_score(info["direction"], result)
        payload = {
            "id": cid, "direction_id": call["direction_id"], "direction": call["direction"],
            "operator": call["operator"], "datetime": call["datetime"],
            "human_score": call["human_score"], "languages": rec["asm"]["languages"],
            "asr_mean_conf": rec["asm"]["mean_conf"] or 0,
            "transcript": _lines_from_tokens(rec["toks"]), "criteria": criteria,
            "ai_score": score, "_audio_path": call["audio_path"],
        }
        _retry(lambda: _cache_put(cid, config.CLAUDE_MODEL, payload, strict=True),
               what=f"запись кэша call {cid}")
        stats["ok"] += 1
        if score is None:
            stats["no_score"] += 1
        elif call["human_score"] is not None:
            stats["pairs"].append((float(call["human_score"]), float(score)))
        if stats["ok"] % 50 == 0:
            log(f"обработано {stats['ok']} результатов…")

    os.remove(os.path.join(workdir, "batch_id.txt"))
    stats["usage"] = usage_tot
    return stats


def main():
    ap = argparse.ArgumentParser(description="Пакетная ИИ-оценка оценённых людьми звонков (Batch API)")
    ap.add_argument("--month", required=True, help="месяц звонков, напр. 2026-06")
    ap.add_argument("--fallback-month", help="добрать из этого месяца, если мало")
    ap.add_argument("--min-calls", type=int, default=50)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--asr-workers", type=int, default=6)
    ap.add_argument("--workdir", help="папка стадий (для перезапуска); по умолчанию во временной")
    ap.add_argument("--dry-run", action="store_true", help="только выборка, без ASR/LLM")
    args = ap.parse_args()

    workdir = args.workdir or os.path.join(tempfile.gettempdir(), f"call_qa_batch_{args.month}")
    os.makedirs(workdir, exist_ok=True)
    log(f"workdir: {workdir} | модель: {config.CLAUDE_MODEL_BULK} | тег кэша: {config.CLAUDE_MODEL}")

    # fail-fast: без RW-БД результаты некуда класть
    conn = config.connect_rw(); conn.close()

    calls = select_calls(args.month, args.fallback_month, args.min_calls, args.limit)
    if not calls:
        log("нечего оценивать — всё уже в кэше"); return
    if args.dry_run:
        log(f"dry-run: к оценке {len(calls)} звонков"); return

    transcripts = asr_stage(calls, workdir, args.asr_workers)
    bid = submit_batch(calls, transcripts, workdir, get_dir := _dir_cache())
    if not bid:
        return
    batch = poll_batch(bid)
    stats = process_results(batch, calls, transcripts, workdir, get_dir)

    u = stats["usage"]
    cost = sum(u[k] * _PRICE[k] for k in _PRICE) / 1e6
    log(f"\nИТОГ: оценено {stats['ok']} (ошибок батча {stats['errored']}, без балла {stats['no_score']})")
    if stats["pairs"]:
        diffs = [abs(h - a) for h, a in stats["pairs"]]
        exact = sum(1 for d in diffs if d <= 5)
        log(f"согласие ИИ↔человек: средн. |Δ| = {sum(diffs)/len(diffs):.1f} баллов; "
            f"в пределах 5 баллов: {exact}/{len(diffs)} ({100*exact//len(diffs)}%)")
    log(f"токены: in={u['input']} out={u['output']} cache_w={u['cache_write']} cache_r={u['cache_read']}"
        f" | стоимость LLM (batch): ${cost:.2f}")


if __name__ == "__main__":
    main()
