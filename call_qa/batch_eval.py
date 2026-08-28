# -*- coding: utf-8 -*-
"""Пакетная фоновая ИИ-оценка через Anthropic Batch API (−50% на все токены).

Зачем: теневой режим — массовая сверка ИИ↔человек за прошлый период + прогрев кэша карточек.

Два субъекта оценки (--subject):
  call       — звонки: выборка месяца → GCS → Soniox ASR (параллельно) → батч оценок;
  wz_episode — эпизоды переписки Wazzup у Верификаторов: выборка пригодных эпизодов
               (доля ответов одного оператора ≥ порога) → ОТДЕЛЬНЫЙ батч описаний
               вложений (картинки через vision-модель, голосовые через Soniox) →
               транскрипт с содержанием вложений → батч оценок.

Скидка Batch −50% распространяется на ВСЕ токены, включая токены изображений,
поэтому ночной прогон описывает вложения вдвое дешевле интерактивного открытия
карточки.

Устойчивость: транскрипты и batch_id сохраняются в --workdir; повторный запуск продолжает
с места остановки (уже закэшированные под текущим fingerprint субъекты пропускаются).

Запуск:  python -m call_qa.batch_eval --month 2026-06 --fallback-month 2026-07 --min-calls 50
         python -m call_qa.batch_eval --subject wz_episode --month 2026-07
Нужны env: ключ Claude, SONIOX_API_KEY, GOOGLE_APPLICATION_CREDENTIALS_CONTENT (для звонков),
           read-write БД (DATABASE_URL или POSTGRES_*)."""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import tempfile
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from . import config
from . import llm
from . import providers
from . import media as media_mod
from . import subjects as subjects_mod
from .asr import soniox
from .evaluation import criteria as criteria_mod
from .evaluation import criterion_config as cc
from .evaluation import evaluator
from .evaluation import runtime_store
from .evaluation.fingerprint import content_hash, transcript_fingerprint
from .rag import knowledge
from .api import (_download, _lines_from_tokens, _ai_score, _cache_put, _meta_upsert,
                  _audio_object_fingerprint, _evaluation_identity, _score_breakdown)

_SUBJECT_PREFIX = {config.SUBJECT_CALL: "call", config.SUBJECT_WZ_EPISODE: "wz"}


def _subject_key(subject: dict) -> str:
    """Ключ чекпоинта: id звонка и id эпизода — независимые последовательности."""
    return f"{subject.get('subject_kind') or config.SUBJECT_CALL}:{subject['id']}"

# Ставки для итоговой строки прогона. У Anthropic отдельный набор со скидкой батча;
# у Vertex и Z.ai наш прогон идёт обычными вызовами (_run_locally), скидки нет, и
# считать его по CLAUDE_BATCH_* значило бы завысить счёт в десятки раз.
_BATCH_PRICE_PREFIX = {
    providers.ANTHROPIC: "CLAUDE_BATCH_",
    providers.VERTEX: "GEMINI_",
    providers.ZAI: "ZAI_",
}
_BATCH_PRICE_KEYS = ("input", "output", "cache_write", "cache_read")


def _batch_cost(usage: dict, model: str | None = None) -> float | None:
    prefix = _BATCH_PRICE_PREFIX[providers.provider_for_tag(model or config.CLAUDE_MODEL)]
    prices = {}
    for key in _BATCH_PRICE_KEYS:
        raw = config.env(f"{prefix}{key.upper()}_USD_PER_MTOK")
        if raw is None:
            return None
        prices[key] = float(raw)
    return sum(int(usage.get(key) or 0) * prices[key] for key in prices) / 1_000_000


# Windows-консоль может быть в cp1251 — не падаем на юникоде в логах.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print(msg, flush=True)


def _manifest_path(workdir: str) -> str:
    return os.path.join(workdir, "batch_manifest.json")


def _load_manifest(workdir: str) -> dict | None:
    path = _manifest_path(workdir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("version") != 2 or not isinstance(data.get("entries"), dict):
        raise RuntimeError(f"unsupported or damaged Batch manifest: {path}")
    return data


def _write_manifest(workdir: str, manifest: dict) -> None:
    """Publish a complete frozen manifest before the external Batch request."""
    target = _manifest_path(workdir)
    fd, tmp = tempfile.mkstemp(prefix="batch_manifest_", suffix=".json", dir=workdir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, separators=(",", ":"), default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


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
                ORDER BY c.created_at""",
            (config.op_direction_id_family(cur), lo, hi))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [{"id": r[0], "subject_kind": config.SUBJECT_CALL,
                 "direction_id": r[1], "direction": r[2], "operator": r[3] or "—",
                 "datetime": r[4], "human_score": r[5], "audio_path": r[6]} for r in rows]

    calls = q(month)
    log(f"выборка {month}: {len(calls)} звонков; точный immutable cache проверяется по fingerprint")
    if len(calls) < min_calls and fallback_month:
        extra = q(fallback_month)
        log(f"мало (<{min_calls}) → добавляю {fallback_month}: +{len(extra)}")
        calls += extra
    if limit:
        calls = calls[:limit]
        log(f"ограничение --limit: берём первые {len(calls)}")
    return calls


def select_episodes(month: str, fallback_month: str | None, min_calls: int,
                    limit: int | None) -> list[dict]:
    """Эпизоды чатов Верификаторов за месяц, пригодные для честной оценки.

    Отсекаются эпизоды, где отвечали несколько операторов: приписать такую
    переписку одному человеку нельзя (порог config.WZ_MIN_OPERATOR_SHARE)."""
    def q(mon):
        lo, hi = _month_bounds(mon)
        conn = config.connect_ro(); cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
        try:
            wz_family = subjects_mod.wz_direction_family(cur)
            if not wz_family:
                log("не найдено направление Верификаторов — нечего выбирать")
                return []
            cur.execute(
                """SELECT e.id, u.direction_id, d.name, u.name,
                          TO_CHAR(e.ended_at AT TIME ZONE 'Asia/Almaty','DD.MM.YYYY, HH24:MI'),
                          e.operator_share, e.human_outbound_count
                     FROM wazzup_episodes e
                     JOIN users u ON u.id = e.operator_user_id
                     LEFT JOIN directions d ON d.id = u.direction_id
                    WHERE e.kind = 'dialog' AND u.direction_id = ANY(%s)
                      AND e.operator_share >= %s AND e.human_outbound_count >= %s
                      AND (e.ended_at AT TIME ZONE 'Asia/Almaty') >= %s
                      AND (e.ended_at AT TIME ZONE 'Asia/Almaty') < %s
                    ORDER BY e.ended_at""",
                (wz_family, config.WZ_MIN_OPERATOR_SHARE,
                 config.WZ_MIN_OPERATOR_MESSAGES, lo, hi))
            rows = cur.fetchall()
        finally:
            cur.close(); conn.close()
        return [{"id": r[0], "subject_kind": config.SUBJECT_WZ_EPISODE,
                 "direction_id": r[1], "direction": r[2], "operator": r[3] or "—",
                 "datetime": r[4], "human_score": None, "audio_path": None,
                 "operator_share": r[5], "human_outbound_count": r[6]} for r in rows]

    items = q(month)
    log(f"выборка {month}: {len(items)} эпизодов чатов "
        f"(порог доли ответов оператора {round(config.WZ_MIN_OPERATOR_SHARE * 100)}%)")
    if len(items) < min_calls and fallback_month:
        extra = q(fallback_month)
        log(f"мало (<{min_calls}) → добавляю {fallback_month}: +{len(extra)}")
        items += extra
    if limit:
        items = items[:limit]
        log(f"ограничение --limit: берём первые {len(items)}")
    return items


def asr_stage(calls: list[dict], workdir: str, workers: int) -> dict:
    """GCS → Soniox для всех звонков (параллельно). Готовые транскрипты копятся в
    transcripts.jsonl — при перезапуске не распознаются заново. Возвращает {call_id: rec}."""
    path = os.path.join(workdir, "transcripts.jsonl")
    done: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                # чекпоинты прежних версий не знают о типе субъекта — это звонки
                rec.setdefault("subject_kind", config.SUBJECT_CALL)
                done[f"{rec['subject_kind']}:{rec['call_id']}"] = rec
        log(f"ASR: {len(done)} транскриптов уже готово (из {path})")
    checkpoints = done
    done = {}

    # Every local checkpoint is validated against the same immutable audio/ASR
    # identity as the online path.  Legacy JSONL rows without that identity are
    # intentionally transcribed once more instead of being trusted blindly.
    todo = list(calls)
    if not todo:
        return done
    log(f"ASR: проверяю immutable cache / распознаю {len(todo)} звонков ({workers} параллельно)…")
    lock_write = __import__("threading").Lock()

    def one(call):
        audio_fp = _audio_object_fingerprint(call["id"], call["audio_path"])
        asr_cfg = runtime_store.asr_config()
        asr_cfg_hash = content_hash(asr_cfg)
        cached = runtime_store.get_transcript(
            call_id=call["id"], audio_fingerprint_value=audio_fp,
            asr_provider="soniox", asr_model=config.SONIOX_MODEL,
            asr_config_hash=asr_cfg_hash, subject_kind=config.SUBJECT_CALL,
        )
        if cached:
            rec = {
                "call_id": call["id"], "subject_kind": config.SUBJECT_CALL,
                "toks": cached.get("tokens") or [],
                "segments": cached.get("segments") or [],
                "asm": {"text": cached["text"], "languages": cached.get("languages") or {},
                        "mean_conf": cached.get("mean_conf"),
                        "low_conf_spans": cached.get("low_conf_spans") or []},
                "transcript_cache_id": cached["id"],
                "transcript_hash": cached["transcript_hash"],
                "audio_fingerprint": audio_fp,
                "asr_config_hash": asr_cfg_hash,
            }
        else:
            checkpoint = checkpoints.get(f"{config.SUBJECT_CALL}:{call['id']}") or {}
            if (checkpoint.get("audio_fingerprint") == audio_fp and
                    checkpoint.get("asr_config_hash") == asr_cfg_hash and
                    (checkpoint.get("asm") or {}).get("text")):
                rec = dict(checkpoint)
                slim = rec.get("toks") or []
                asm = rec["asm"]
            else:
                with tempfile.TemporaryDirectory() as td:
                    dest = os.path.join(td, "audio.mp3")
                    _download(call["audio_path"], dest)
                    got = soniox.transcribe_file_full(dest)
                toks, asr_meta = got["tokens"], got["meta"]
                assembled = soniox.assemble(toks, asr_meta)
                slim = [{k: t.get(k) for k in (
                    "text", "speaker", "language", "confidence",
                    "start_time_ms", "end_time_ms", "is_audio_event") if t.get(k) is not None}
                        for t in toks]
                asm = {"text": assembled["text"], "languages": assembled["languages"],
                       "mean_conf": assembled["mean_conf"],
                       "low_conf_spans": assembled["low_conf_spans"],
                       "duration_ms": assembled.get("duration_ms"),
                       "audio_events": assembled.get("audio_events"),
                       "asr_meta": asr_meta}
                rec = {"call_id": call["id"], "subject_kind": config.SUBJECT_CALL,
                       "toks": slim, "asm": asm}
            segments = rec.get("segments") or _lines_from_tokens(slim)
            transcript_hash = content_hash(asm["text"])
            duration_ms = asm.get("duration_ms") or max(
                (int(token.get("end_time_ms") or 0) for token in slim), default=None)
            transcript_cache_id = runtime_store.put_transcript(
                call_id=call["id"], audio_fingerprint_value=audio_fp,
                asr_provider="soniox", asr_model=config.SONIOX_MODEL,
                asr_config_hash=asr_cfg_hash, transcript_hash=transcript_hash,
                text=asm["text"], segments=segments, tokens=slim,
                payload={"asr_config": asr_cfg, "source": "batch",
                         "asr_meta": asm.get("asr_meta"), "audio_events": asm.get("audio_events")},
                languages=asm.get("languages"), mean_conf=asm.get("mean_conf"),
                low_conf_spans=asm.get("low_conf_spans"), duration_ms=duration_ms,
                subject_kind=config.SUBJECT_CALL,
            )
            rec.update({"segments": segments, "transcript_cache_id": transcript_cache_id,
                        "transcript_hash": transcript_hash,
                        "audio_fingerprint": audio_fp, "asr_config_hash": asr_cfg_hash})
        rec.setdefault("source_model", config.SONIOX_MODEL)
        rec.setdefault("source_config", runtime_store.asr_config())
        with lock_write:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_retry, (lambda c=c: one(c)), tries=2, delay=15,
                          what=f"ASR call {c['id']}"): c for c in todo}
        for fu in as_completed(futs):
            call = futs[fu]
            cid = call["id"]
            try:
                done[_subject_key(call)] = fu.result(); ok += 1
            except Exception as e:
                fail += 1
                log(f"ASR FAIL call={cid}: {e}")
            if (ok + fail) % 20 == 0:
                log(f"ASR: {ok + fail}/{len(todo)} (ошибок {fail})")
    log(f"ASR готово: +{ok}, ошибок {fail}, всего транскриптов {len(done)}")
    return done


def media_batch_stage(episodes: list[dict], workdir: str) -> int:
    """Описывает картинки и PDF выбранных эпизодов пакетно (−50%).

    Отдельная стадия и отдельный маркер: смешивать описания вложений с оценками в
    одном манифесте нельзя — process_results требует терминального результата для
    каждого custom_id и проверяет неизменность моделей оценки, а описания идут
    другой (более дешёвой) моделью.

    Голосовые расшифровываются не здесь: Soniox — не Batch API, у него нет
    пакетной скидки, поэтому они распознаются в общем потоке транскриптов."""
    marker = os.path.join(workdir, "media_batch_done")
    if os.path.exists(marker):
        log("описания вложений уже выполнены в этом workdir — пропуск")
        return 0
    pending, seen = [], set()  # картинки и PDF: их читает Claude, значит есть Batch
    for episode in episodes:
        try:
            subject = subjects_mod.load(config.SUBJECT_WZ_EPISODE, episode["id"])
            messages = subjects_mod.fetch_episode_messages(subject)
        except Exception as exc:
            log(f"эпизод {episode['id']}: не удалось прочитать сообщения ({exc})")
            continue
        for item in media_mod.plan(messages):
            # Голосовые расшифровывает Soniox — у него нет пакетной скидки,
            # поэтому в батч идут только те вложения, что читает Claude.
            if item["media_kind"] not in ("image", "document"):
                continue
            key = (item["message_id"], item["source_hash"])
            if key in seen:
                continue
            seen.add(key)
            pending.append(item)
    if not pending:
        log("картинок и документов к описанию нет")
        open(marker, "w", encoding="utf-8").close()
        return 0
    cached = media_mod._lookup(pending)
    todo = [item for item in pending
            if (cached.get((item["message_id"], item["source_hash"])) or {}).get("status") != "ready"]
    images = sum(1 for i in pending if i["media_kind"] == "image")
    docs = len(pending) - images
    log(f"вложения: картинок {images}, документов {docs}; к описанию {len(todo)} "
        f"(остальные уже расшифрованы)")
    if todo:
        # id уже отправленных частей батча переживают обрыв: без этого повтор
        # отправил бы те же картинки вторым батчем и заплатил дважды.
        state_path = os.path.join(workdir, "media_batches.json")

        def _resume():
            if not os.path.exists(state_path):
                return {}
            try:
                with open(state_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                return {}

        def _remember(key, batch_id):
            state = _resume()
            state[key] = batch_id
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
                fh.flush()
                os.fsync(fh.fileno())

        results = media_mod.annotate_media_batch(
            todo, log=log, resume=_resume, remember=_remember)
        ready = sum(1 for r in (results or {}).values() if r.get("status") == "ready")
        log(f"вложения: описано {ready} из {len(todo)}")
        if os.path.exists(state_path):
            os.remove(state_path)
    open(marker, "w", encoding="utf-8").close()
    return len(todo)


def episode_transcript_stage(episodes: list[dict], workdir: str, workers: int) -> dict:
    """Транскрипты эпизодов с содержанием вложений → immutable-кэш.

    К этому моменту картинки уже описаны батчем; здесь добираются голосовые
    (Soniox, параллельно) и собирается текст, который увидит модель."""
    path = os.path.join(workdir, "transcripts.jsonl")
    done: dict[str, dict] = {}
    if not episodes:
        return done
    log(f"транскрипты эпизодов: {len(episodes)} ({workers} параллельно)…")
    lock_write = __import__("threading").Lock()

    def one(episode):
        subject = subjects_mod.load(config.SUBJECT_WZ_EPISODE, episode["id"])
        subjects_mod.require_evaluable(subject)
        prepared = subjects_mod.prepare_wz_transcript(subject)
        cfg_hash = prepared["source_config_hash"]
        cached = runtime_store.get_transcript(
            call_id=episode["id"], audio_fingerprint_value=prepared["source_identity"],
            asr_provider=prepared["provider"], asr_model=prepared["model"],
            asr_config_hash=cfg_hash, subject_kind=config.SUBJECT_WZ_EPISODE)
        if cached:
            transcript_cache_id = cached["id"]
            transcript_hash = cached["transcript_hash"]
            text, segments = cached["text"], cached.get("segments") or []
        else:
            text, segments = prepared["text"], prepared["lines"]
            transcript_hash = content_hash(text)
            transcript_cache_id = runtime_store.put_transcript(
                call_id=episode["id"], audio_fingerprint_value=prepared["source_identity"],
                asr_provider=prepared["provider"], asr_model=prepared["model"],
                asr_config_hash=cfg_hash, transcript_hash=transcript_hash, text=text,
                segments=segments, tokens=None,
                payload={"source_config": prepared["source_config"], "source": "batch",
                         "media_source": prepared["media_source"],
                         "media_stats": prepared["media_stats"],
                         "source_identity": prepared["source_identity"]},
                languages=None, mean_conf=None, low_conf_spans=None, duration_ms=None,
                subject_kind=config.SUBJECT_WZ_EPISODE)
        rec = {
            "call_id": episode["id"], "subject_kind": config.SUBJECT_WZ_EPISODE,
            "toks": [], "segments": segments,
            "asm": {"text": text, "languages": {}, "mean_conf": None, "low_conf_spans": []},
            "transcript_cache_id": transcript_cache_id,
            "transcript_hash": transcript_hash,
            "audio_fingerprint": prepared["source_identity"],
            "asr_config_hash": cfg_hash,
            "source_model": prepared["model"], "source_config": prepared["source_config"],
            "media": dict(prepared["media_stats"], source=prepared["media_source"]),
        }
        with lock_write:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_retry, (lambda e=e: one(e)), tries=2, delay=10,
                          what=f"транскрипт эпизода {e['id']}"): e for e in episodes}
        for fu in as_completed(futs):
            episode = futs[fu]
            try:
                done[_subject_key(episode)] = fu.result(); ok += 1
            except subjects_mod.SubjectNotEvaluable as exc:
                fail += 1
                log(f"эпизод {episode['id']} пропущен: {exc}")
            except Exception as exc:
                fail += 1
                log(f"ТРАНСКРИПТ FAIL эпизод={episode['id']}: {exc}")
            if (ok + fail) % 20 == 0:
                log(f"транскрипты: {ok + fail}/{len(episodes)} (ошибок {fail})")
    log(f"транскрипты готовы: +{ok}, ошибок/пропусков {fail}")
    return done


def _dir_cache() -> dict:
    cachedirs = {}

    def get(direction_id):
        if direction_id not in cachedirs:
            d = criteria_mod.load_direction(direction_id)
            cc.apply_to_direction(d)
            conn = config.connect_rw()
            try:
                with conn:
                    knowledge_ctx = knowledge.ensure_knowledge_context(conn, direction=d)
            finally:
                conn.close()
            cachedirs[direction_id] = {
                "direction": d,
                "t_crits": [c for c in d["criteria"] if c["eval_source"] == cc.TRANSCRIPT],
                "scale_revision_id": knowledge_ctx["scale_revision_id"],
                "snapshot": knowledge_ctx["snapshot"],
            }
        return cachedirs[direction_id]
    return get


_LOCAL_PREFIX = "local:"


def _run_cost():
    """Чем подписать себестоимость строки прогона в ai_evaluation_runs.

    У Anthropic пакетный путь идёт со скидкой 50 %, а автоподсчёт в runtime_store
    знает только полные ставки CLAUDE_* — поэтому там цена остаётся пустой, как и
    была, и считается один раз в сводке по batch-тарифам. У Vertex и Z.ai пакетного
    режима нет: прогон идёт обычными вызовами по обычному прайсу, автоподсчёт для них
    точен, и колонка наконец перестаёт быть пустой. Без неё экономию нового
    провайдера нечем подтвердить по журналу.
    """
    if providers.provider_for(config.CLAUDE_MODEL_BULK) == providers.ANTHROPIC:
        return None
    return runtime_store.AUTO_COST


def _local_limits() -> tuple[int, float]:
    """Потоки и таймаут локального прогона — свои у каждого провайдера.

    У Z.ai оценка на reasoning_effort=high идёт около 91 с при p90 116 с, и таймаут
    Vertex (180 с) обрезал бы хвост длинных звонков. Публичных лимитов RPM/TPM Z.ai
    не раскрывает, поэтому потоков столько же, сколько у Vertex.
    """
    if providers.provider_for(config.CLAUDE_MODEL_BULK) == providers.ZAI:
        return config.ZAI_LOCAL_BATCH_WORKERS, config.ZAI_TIMEOUT
    return config.VERTEX_LOCAL_BATCH_WORKERS, config.VERTEX_TIMEOUT


def _run_locally(requests_: list[dict], workdir: str, marker: str) -> str:
    """Пакетный прогон без Batch API — для провайдеров, у которых его формы у нас нет.

    У Vertex пакетный режим — задание с файлом на GCS (проверено: работает и на
    моделях 3.x, но только в регионе `global`), и это отдельное хранилище со своей
    уборкой; пока оно не написано, прогон на Gemini идёт обычными вызовами.
    У Z.ai пакетный режим есть, но НЕ для нашей модели: `/files` с purpose=batch
    отвечает 400 со списком поддерживаемых, где самая свежая — `glm-5.1`. Ждать его
    нечего, этот путь для GLM постоянный.

    Дороже ровно вдвое (нет скидки батча) и не мгновенно, но остальной конвейер —
    отпечатки, локи, карточки, сохранение прогонов — работает без единой правки:
    результат укладывается в ту же форму, что отдаёт Anthropic.

    Результат пишется файлом до возврата: прогон в 19 тыс. звонков нельзя терять
    из-за обрыва на предпоследнем.
    """
    path = os.path.join(workdir, "local_results.jsonl")
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line).get("custom_id"))
                except ValueError:
                    continue
        log(f"локальный прогон продолжается: уже готово {len(done)}")
    todo = [r for r in requests_ if r["custom_id"] not in done]
    workers, timeout = _local_limits()
    log(f"пакетного API у провайдера нет — считаю сам: {len(todo)} оценок, "
        f"модель {config.CLAUDE_MODEL_BULK}, потоков {workers}")

    def one(item):
        custom_id = item["custom_id"]
        try:
            parsed = llm.post_body(item["params"], timeout=timeout,
                                   include_meta=True)
            meta = parsed.pop("_llm_meta", None) or {}
            return {"custom_id": custom_id, "result": {"type": "succeeded", "message": {
                "id": meta.get("request_id"), "model": meta.get("model"),
                "usage": meta.get("usage") or {},
                "content": [{"type": "text", "text": json.dumps(parsed, ensure_ascii=False)}],
            }}}
        except Exception as exc:
            log(f"{custom_id}: оценка не удалась — {exc}")
            return {"custom_id": custom_id,
                    "result": {"type": "errored", "error": {"message": str(exc)[:500]}}}

    with open(path, "a", encoding="utf-8") as out:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for done_count, row in enumerate(pool.map(one, todo), 1):
                out.write(json.dumps(row, ensure_ascii=False) + chr(10))
                out.flush()
                if done_count % 25 == 0:
                    log(f"  посчитано {done_count} из {len(todo)}")
    bid = _LOCAL_PREFIX + path
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write(bid)
        fh.flush()
        os.fsync(fh.fileno())
    return bid


def submit_batch(calls: list[dict], transcripts: dict, workdir: str, get_dir) -> str | None:
    """Собирает батч оценок и отправляет. Возвращает batch_id (или существующий из workdir)."""
    marker = os.path.join(workdir, "batch_id.txt")
    if os.path.exists(marker):
        if _load_manifest(workdir) is None:
            raise RuntimeError("batch_id exists without the frozen Batch manifest")
        bid = open(marker).read().strip()
        log(f"нашёл незавершённый батч {bid} — продолжаю его")
        if bid.startswith(_LOCAL_PREFIX):
            # Локальный прогон мог оборваться на середине, а process_results требует
            # результат по КАЖДОЙ заявке манифеста. Досчитываем остаток — уже готовые
            # заявки _run_locally пропускает по файлу.
            manifest = _load_manifest(workdir)
            return _run_locally(
                [{"custom_id": cid, "params": entry["request_body"]}
                 for cid, entry in manifest["entries"].items()], workdir, marker)
        return bid

    manifest = _load_manifest(workdir)
    if manifest is None:
        entries = {}
        # Sorting by direction improves provider-side system-prompt cache reuse.
        for call in sorted(calls, key=lambda c: c["direction_id"] or 0):
            subject_kind = call.get("subject_kind") or config.SUBJECT_CALL
            rec = transcripts.get(_subject_key(call))
            if not rec:
                continue
            info = get_dir(call["direction_id"])
            snapshot = info["snapshot"]
            transcript_identity = transcript_fingerprint(
                audio_fingerprint=rec["audio_fingerprint"],
                asr_model=rec.get("source_model") or config.SONIOX_MODEL,
                asr_config=rec.get("source_config") or runtime_store.asr_config(),
                transcript=rec["asm"]["text"],
            )
            fingerprint, components, retrieval_cfg = _evaluation_identity(
                transcript_hash=transcript_identity, direction=info["direction"],
                knowledge_snapshot=snapshot, use_rag=True,
            )
            cached = runtime_store.get_cached_evaluation(
                call_id=call["id"], evaluation_fingerprint=fingerprint,
                run_kinds=("standard", "force", "batch"), subject_kind=subject_kind,
            )
            if cached:
                log(f"{subject_kind} {call['id']}: точный evaluation fingerprint уже готов — пропуск")
                continue
            prepared = evaluator.prepare_rag_context(
                info["direction"], info["t_crits"], rec["asm"]["text"],
                use_rag=True, knowledge_snapshot_id=snapshot["id"],
            )
            body = evaluator.build_eval_body(
                rec["asm"]["text"], info["direction"], info["t_crits"],
                asr_low_spans=rec["asm"]["low_conf_spans"], use_rag=True,
                model=config.CLAUDE_MODEL_BULK, rag_text=prepared["rag_text"],
                subject_kind=subject_kind, cache_ttl=config.CLAUDE_CACHE_TTL_BATCH,
            )
            custom_id = f"{_SUBJECT_PREFIX.get(subject_kind, subject_kind)}-{call['id']}"
            entries[custom_id] = {
                "call": call, "subject_kind": subject_kind, "direction": info["direction"],
                "t_crits": info["t_crits"],
                "scale_revision_id": info["scale_revision_id"],
                "snapshot": snapshot,
                "transcript_cache_id": rec["transcript_cache_id"],
                "transcript_hash": rec["transcript_hash"],
                "transcript_identity": transcript_identity,
                "evaluation_fingerprint": fingerprint,
                "fingerprint_components": components,
                "retrieval_config": retrieval_cfg,
                "prepared_rag": prepared,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "request_body_hash": content_hash(body),
                "request_body": body,
            }
        manifest = {"version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
                    "model": config.CLAUDE_MODEL_BULK, "entries": entries}
        _write_manifest(workdir, manifest)

    requests_ = [{"custom_id": custom_id, "params": entry["request_body"]}
                 for custom_id, entry in manifest["entries"].items()]
    for entry in manifest["entries"].values():
        frozen = (entry.get("fingerprint_components") or {}).get("model_config") or {}
        if (frozen.get("bulk"), frozen.get("hard")) != (
                config.CLAUDE_MODEL_BULK, config.CLAUDE_MODEL_HARD):
            raise RuntimeError("Claude model configuration changed after Batch manifest creation")
    if not requests_:
        os.remove(_manifest_path(workdir))
        log("нечего отправлять в батч")
        return None
    if providers.provider_for(config.CLAUDE_MODEL_BULK) != providers.ANTHROPIC:
        return _run_locally(requests_, workdir, marker)

    log(f"отправляю батч: {len(requests_)} оценок, модель {config.CLAUDE_MODEL_BULK}")

    def _post():
        r = httpx.post(llm.BATCHES_URL, json={"requests": requests_},
                       headers=llm._headers(), timeout=300.0)
        r.raise_for_status()
        return r.json()
    bid = _retry(_post, what="создание батча")["id"]
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write(bid)
        fh.flush()
        os.fsync(fh.fileno())
    log(f"батч создан: {bid}")
    return bid


def poll_batch(batch_id: str, interval: int = 30) -> dict:
    """Ждёт завершения батча (обычно < 1 часа)."""
    if batch_id.startswith(_LOCAL_PREFIX):
        path = batch_id[len(_LOCAL_PREFIX):]
        return {"processing_status": "ended", "results_path": path}
    def _get():
        r = httpx.get(f"{llm.BATCHES_URL}/{batch_id}", headers=llm._headers(), timeout=60.0)
        r.raise_for_status()
        return r.json()

    while True:
        # Ожидание не должно умирать от сети: батч на стороне Anthropic живёт своей жизнью,
        # мы можем ждать сколько угодно (длинные DNS-провалы на машине — реальность).
        try:
            st = _retry(_get, what="статус батча")
        except Exception as e:
            log(f"статус батча недоступен ({e}) — продолжаю ждать")
            time.sleep(interval)
            continue
        if st.get("processing_status") == "ended":
            log(f"батч завершён: {st.get('request_counts')}")
            return st
        log(f"батч в работе: {st.get('request_counts')}")
        time.sleep(interval)


def process_results(batch: dict, calls: list[dict], transcripts: dict, workdir: str, get_dir) -> dict:
    """Finalize the frozen Batch requests into immutable runs and compatibility cards."""
    manifest = _load_manifest(workdir)
    if manifest is None:
        raise RuntimeError("Batch results cannot be processed without the frozen manifest")
    expected = set(manifest["entries"])
    terminal = set()
    usage_tot = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    stats = {"ok": 0, "errored": 0, "no_score": 0, "pairs": []}

    def _results():
        rr = httpx.get(batch["results_url"], headers=llm._headers(), timeout=300.0)
        rr.raise_for_status()
        return rr.text
    if batch.get("results_path"):
        with open(batch["results_path"], encoding="utf-8") as fh:
            results_text = fh.read()
    else:
        results_text = _retry(_results, what="результаты батча")
    for line in results_text.splitlines():
        item = json.loads(line)
        custom_id = item.get("custom_id")
        entry = manifest["entries"].get(custom_id)
        if entry is None:
            raise RuntimeError(f"unexpected Batch result custom_id={custom_id!r}")
        cid = int(entry["call"]["id"])
        call = entry["call"]
        subject_kind = (entry.get("subject_kind")
                        or call.get("subject_kind") or config.SUBJECT_CALL)
        direction = entry["direction"]
        rec = transcripts.get(f"{subject_kind}:{cid}")
        if not rec:
            cached_transcript = runtime_store.get_transcript_by_id(entry["transcript_cache_id"])
            if not cached_transcript:
                raise RuntimeError(f"immutable transcript missing for {subject_kind} {cid}")
            rec = {
                "toks": cached_transcript.get("tokens") or [],
                "segments": cached_transcript.get("segments") or [],
                "asm": {"text": cached_transcript["text"],
                        "languages": cached_transcript.get("languages") or {},
                        "mean_conf": cached_transcript.get("mean_conf"),
                        "low_conf_spans": cached_transcript.get("low_conf_spans") or []},
            }
        frozen_models = (entry.get("fingerprint_components") or {}).get("model_config") or {}
        if (frozen_models.get("bulk") != config.CLAUDE_MODEL_BULK or
                frozen_models.get("hard") != config.CLAUDE_MODEL_HARD):
            raise RuntimeError("Claude model configuration changed while Batch was running")

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
            primary_meta = {
                "request_id": msg.get("id"), "model": msg.get("model"),
                "usage": u, "latency_ms": 0, "stage": "batch_bulk",
                "criterion_idxs": [c["idx"] for c in entry["t_crits"]],
            }
        else:
            log(f"{subject_kind} {cid}: батч-запрос "
                f"{item['result']['type']} → синхронная оценка")
            stats["errored"] += 1
            parsed = {"per_criterion": [], "overall_comment": ""}
            primary_meta = None

        components = entry["fingerprint_components"]
        snapshot = entry["snapshot"]
        cache_model = components["model"]
        started_at = datetime.fromisoformat(entry["started_at"])
        with runtime_store.distributed_call_lock(
                cid, subject_kind=subject_kind) as lock_acquired:
            if not lock_acquired:
                raise RuntimeError("advisory lock unavailable while publishing batch run "
                                   f"for {subject_kind} {cid}")
            already = runtime_store.get_cached_evaluation(
                call_id=cid, evaluation_fingerprint=entry["evaluation_fingerprint"],
                run_kinds=("standard", "force", "batch"), subject_kind=subject_kind,
            )
            if already:
                compatibility = dict(already.get("payload") or {})
                compatibility.update({
                    "transcript": rec.get("segments") or _lines_from_tokens(rec.get("toks") or []),
                    "_audio_path": call.get("audio_path"),
                    "subject_kind": subject_kind,
                    "_transcript_cache_id": entry["transcript_cache_id"],
                    "_transcript_hash": entry["transcript_hash"],
                })
                if rec.get("media"):
                    compatibility["media"] = rec["media"]
                _retry(lambda: _cache_put(cid, cache_model, compatibility, strict=True,
                                          subject_kind=subject_kind),
                       what=f"восстановление кэша {subject_kind} {cid}")
                _meta_upsert(cid, cache_model, compatibility, subject_kind=subject_kind)
                terminal.add(custom_id)
                continue
            run_id = runtime_store.new_run_id()
            try:
                result = _retry(
                    lambda: evaluator.evaluate(
                        rec["asm"]["text"], direction,
                        asr_low_spans=rec["asm"].get("low_conf_spans") or [],
                        use_rag=True, knowledge_snapshot_id=snapshot["id"],
                        prepared_rag=entry["prepared_rag"], primary_result=parsed,
                        primary_llm_meta=primary_meta, subject_kind=subject_kind,
                    ),
                    tries=3, delay=20, what=f"завершение оценки call {cid}",
                )
                completed_at = datetime.now(timezone.utc)
            except Exception as exc:
                completed_at = datetime.now(timezone.utc)
                # прогоны ключуются каноническим id направления (call["direction_id"]
                # может указывать на архивную версию шкалы — по ней грузятся критерии)
                failure_payload = {"id": cid, "subject_kind": subject_kind,
                                   "direction_id": int(direction["id"]),
                                   "_retrieval_trace": entry["prepared_rag"]["retrieval_trace"]}
                runtime_store.save_evaluation_run(
                    run_id=run_id, call_id=cid, direction_id=int(direction["id"]),
                    subject_kind=subject_kind,
                    transcript_cache_id=entry["transcript_cache_id"],
                    transcript_hash=entry["transcript_hash"],
                    evaluation_fingerprint=entry["evaluation_fingerprint"],
                    fingerprint_components=components, run_kind="batch", model=cache_model,
                    model_config_hash=content_hash(components["model_config"]),
                    prompt_hash=components["prompt_hash"],
                    output_schema_hash=components["output_schema_hash"],
                    output_schema_version="ai-qa-evaluation-v2",
                    criteria_hash=direction["scale_hash"],
                    criterion_config_hash=components["criterion_config_hash"],
                    scale_revision_id=entry["scale_revision_id"],
                    knowledge_snapshot_id=snapshot["id"],
                    knowledge_revision=snapshot["knowledge_revision"],
                    retrieval_config=entry["retrieval_config"], status="failed",
                    per_criterion=[], payload=failure_payload, started_at=started_at,
                    completed_at=completed_at, error_code=type(exc).__name__,
                    error_message=str(exc)[:2000], estimated_cost=None,
                )
                raise

            crit_meta = {c["idx"]: c for c in direction["criteria"]}
            criteria = [{
                "idx": v["idx"], "criterion_id": crit_meta.get(v["idx"], {}).get("criterion_id"),
                "name": v["name"], "is_critical": bool(crit_meta.get(v["idx"], {}).get("is_critical")),
                "deficiency": (crit_meta.get(v["idx"], {}).get("deficiency")
                               if isinstance(crit_meta.get(v["idx"], {}).get("deficiency"), dict) else None),
                "source": v["source"], "ai": v["verdict"], "conf": v["confidence"],
                "evidence": v["evidence_quote"], "comment": v["comment"],
                "model": v.get("model"),
            } for v in result["per_criterion"]]
            score = _ai_score(direction, result)
            payload = {
                "id": cid, "subject_kind": subject_kind,
                "direction_id": int(direction["id"]), "direction": call["direction"],
                "operator": call["operator"], "datetime": call["datetime"],
                "human_score": call.get("human_score"), "languages": rec["asm"]["languages"],
                "asr_mean_conf": rec["asm"]["mean_conf"] or 0,
                "transcript": rec.get("segments") or _lines_from_tokens(rec.get("toks") or []),
                "criteria": criteria, "ai_score": score,
                "score_breakdown": _score_breakdown(direction, result),
                "_audio_path": call.get("audio_path"),
                "_transcript_cache_id": entry["transcript_cache_id"],
                "_transcript_hash": entry["transcript_hash"],
                "_evaluation_run_id": run_id,
                "_scale_revision_id": entry["scale_revision_id"],
                "_knowledge_snapshot_id": snapshot["id"],
                "_evaluation_fingerprint": entry["evaluation_fingerprint"],
                "_retrieval_trace": result.get("retrieval_trace") or {},
                "_llm_meta": result.get("_llm_meta") or {},
            }
            payload["evaluation"] = {
                "run_id": run_id,
                "fingerprint_short": entry["evaluation_fingerprint"][:12],
                "knowledge_revision": snapshot["knowledge_revision"],
                "retrieval_status": payload["_retrieval_trace"].get("status") or "unknown",
                "retrieval_ms": int(payload["_retrieval_trace"].get("latency_ms") or 0),
                "stale": False, "rollout_mode": "batch", "rag_enabled": True,
            }
            if rec.get("media"):
                payload["media"] = rec["media"]
            persisted_payload = dict(payload)
            persisted_payload.pop("transcript", None)
            persisted_payload.pop("_audio_path", None)
            runtime_store.save_evaluation_run(
                run_id=run_id, call_id=cid, direction_id=int(direction["id"]),
                subject_kind=subject_kind,
                transcript_cache_id=entry["transcript_cache_id"],
                transcript_hash=entry["transcript_hash"],
                evaluation_fingerprint=entry["evaluation_fingerprint"],
                fingerprint_components=components, run_kind="batch", model=cache_model,
                model_config_hash=content_hash(components["model_config"]),
                prompt_hash=components["prompt_hash"],
                output_schema_hash=components["output_schema_hash"],
                output_schema_version="ai-qa-evaluation-v2",
                criteria_hash=direction["scale_hash"],
                criterion_config_hash=components["criterion_config_hash"],
                scale_revision_id=entry["scale_revision_id"],
                knowledge_snapshot_id=snapshot["id"],
                knowledge_revision=snapshot["knowledge_revision"],
                retrieval_config=entry["retrieval_config"], status="succeeded",
                per_criterion=result.get("per_criterion") or [], payload=persisted_payload,
                started_at=started_at, completed_at=completed_at,
                llm_meta=result.get("_llm_meta"), estimated_cost=_run_cost(),
            )
            _retry(lambda: _cache_put(cid, cache_model, payload, strict=True,
                                      subject_kind=subject_kind),
                   what=f"запись кэша {subject_kind} {cid}")
            _meta_upsert(cid, cache_model, payload, subject_kind=subject_kind)
            terminal.add(custom_id)
            stats["ok"] += 1
            if score is None:
                stats["no_score"] += 1
            elif call.get("human_score") is not None:
                stats["pairs"].append((float(call["human_score"]), float(score)))
            if stats["ok"] % 50 == 0:
                log(f"обработано {stats['ok']} результатов…")

    missing_results = expected - terminal
    if missing_results:
        raise RuntimeError(f"Batch results incomplete; missing terminal runs: {sorted(missing_results)}")
    os.remove(os.path.join(workdir, "batch_id.txt"))
    os.remove(_manifest_path(workdir))
    stats["usage"] = usage_tot
    return stats


def main():
    ap = argparse.ArgumentParser(description="Пакетная ИИ-оценка через Batch API")
    ap.add_argument("--subject", choices=list(config.SUBJECT_KINDS),
                    default=config.SUBJECT_CALL,
                    help="что оцениваем: звонки (call) или эпизоды чатов Верификаторов (wz_episode)")
    ap.add_argument("--month", required=True, help="месяц субъектов, напр. 2026-06")
    ap.add_argument("--fallback-month", help="добрать из этого месяца, если мало")
    ap.add_argument("--min-calls", type=int, default=50)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--asr-workers", type=int, default=6)
    ap.add_argument("--workdir", help="папка стадий (для перезапуска); по умолчанию во временной")
    ap.add_argument("--dry-run", action="store_true", help="только выборка, без ASR/LLM")
    args = ap.parse_args()

    subject_kind = args.subject
    suffix = "" if subject_kind == config.SUBJECT_CALL else f"_{subject_kind}"
    workdir = args.workdir or os.path.join(
        tempfile.gettempdir(), f"call_qa_batch_{args.month}{suffix}")
    os.makedirs(workdir, exist_ok=True)
    log(f"субъект: {subject_kind} | workdir: {workdir} | модель: {config.CLAUDE_MODEL_BULK} "
        f"| тег кэша: {config.CLAUDE_MODEL}")

    # fail-fast: без RW-БД результаты некуда класть
    conn = config.connect_rw(); conn.close()

    if subject_kind == config.SUBJECT_WZ_EPISODE:
        calls = select_episodes(args.month, args.fallback_month, args.min_calls, args.limit)
    else:
        calls = select_calls(args.month, args.fallback_month, args.min_calls, args.limit)
    if not calls:
        log("нечего оценивать — всё уже в кэше"); return
    if args.dry_run:
        log(f"dry-run: к оценке {len(calls)} субъектов ({subject_kind})"); return

    if subject_kind == config.SUBJECT_WZ_EPISODE:
        # Сначала ОДИН батч описаний картинок (−50%), потом транскрипты с их содержанием.
        media_batch_stage(calls, workdir)
        transcripts = episode_transcript_stage(calls, workdir, args.asr_workers)
    else:
        transcripts = asr_stage(calls, workdir, args.asr_workers)
    bid = submit_batch(calls, transcripts, workdir, get_dir := _dir_cache())
    if not bid:
        return
    batch = poll_batch(bid)
    stats = process_results(batch, calls, transcripts, workdir, get_dir)

    u = stats["usage"]
    log(f"\nИТОГ: оценено {stats['ok']} (ошибок батча {stats['errored']}, без балла {stats['no_score']})")
    if stats["pairs"]:
        diffs = [abs(h - a) for h, a in stats["pairs"]]
        exact = sum(1 for d in diffs if d <= 5)
        log(f"согласие ИИ↔человек: средн. |Δ| = {sum(diffs)/len(diffs):.1f} баллов; "
            f"в пределах 5 баллов: {exact}/{len(diffs)} ({100*exact//len(diffs)}%)")
    estimated_cost = _batch_cost(u, config.CLAUDE_MODEL)
    cost = (f" | стоимость LLM (batch): ${estimated_cost:.2f}" if estimated_cost is not None else
            " | batch-тарифы не заданы, стоимость не оценивается")
    log(f"токены: in={u['input']} out={u['output']} cache_w={u['cache_write']} cache_r={u['cache_read']}{cost}")


if __name__ == "__main__":
    main()
