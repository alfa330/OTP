# -*- coding: utf-8 -*-
"""Факты дня: сжатый слепок оценённых разговоров дня отдела — для периода и месяца.

ЗАЧЕМ СЛЕПОК. Экран периода и месячная сводка спрашивают про тысячи разговоров
сразу: месяц ОП — две с лишним тысячи карточек по 15–60 КБ в ai_review_cache.
Читать их при каждом открытии периода — 5 с на стороне базы по замеру
17.10.2026 (и 20+ с с машины разработчика), а с ростом выборки — больше. Слепок
дня весит около 100 КБ, лежит в ai_qa_day_facts и читается за доли секунды; из
тридцати слепков период собирается в памяти за миллисекунды.

ЧТО В СЛЕПКЕ. Только то, что нужно цифрам и чату периода: кто и по какой шкале
говорил, балл ИИ и человека, какие критерии применимы, где «Неверно» и
«Недочёт» (с комментарием и цитатой оценщика — ими отвечают на «какие ошибки у
Ивановой за месяц»), что исправил человек, итог оценщика. Транскриптов нет: их
место — в сводке и чате дня.

КОГДА ОН УСТАРЕВАЕТ. У дня два отпечатка: набор оценок (тот же inputs_hash, что
у сводки дня) и проверки людей («сколько проверено : сколько поправлено»). Оба
считаются одним запросом по всему периоду (day_signatures, ~1 с на месяц) и
сверяются со строкой слепка; разошлись — день пересчитывается. Один-два дня
пересчитываются прямо в запросе экрана, больше — в фоне, и экран опрашивает
готовность: пересчёт месяца с нуля — минута-две.

ПРАВА. Слепок — по всему отделу; скоуп зрителя (СВ видит только свои
направления) накладывается при сборке периода, по направлению сотрудника.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from psycopg2.extras import Json

from .. import config
from . import data as digest_data

# Формат слепка. Сменился — все дни пересчитываются при первом обращении.
VERSION = 1
# Сколько устаревших дней пересчитывать прямо в запросе экрана. День — секунда-две
# на сервере; дальше — фон, иначе первое открытие месяца держало бы запрос минуту.
INLINE_LIMIT = 2
# Сколько дней назад держать слепки готовыми (ночной догон): период — до 31 дня,
# плюс такой же прошлый период для сравнения.
BACKFILL_DAYS = 70

_V = {"Incorrect": "I", "Error": "I", "Deficiency": "D", "Correct": "C", "N/A": "N", "Pending": "P"}
VERDICT_OF = {"I": "Incorrect", "D": "Deficiency", "C": "Correct", "N": "N/A", "P": "Pending"}


def _short(verdict) -> str | None:
    text = str(verdict or "").strip()
    return _V.get(text, text[:1] or None) if text else None


def _clean(text, limit) -> str:
    return " ".join(str(text or "").split())[:limit]


# ── слепок ────────────────────────────────────────────────────────────────────

def reviews_mark_of(talks) -> str:
    """«проверено:поправлено» — тем же счётом, что day_signatures считает в SQL."""
    reviewed = sum(1 for t in talks if t.get("outcome") or t.get("human_score") is not None)
    corrected = sum(1 for t in talks if t.get("outcome") == "adjudicated")
    return f"{reviewed}:{corrected}"


def compact(talks, day) -> dict:
    """Разговоры дня (data.collect_day с with_notes) → слепок.

    Критерии — словарём дня: имя критерия повторяется в каждом из сотни
    разговоров, а в слепке оно стоит один раз, у разговора — номера. Ключ
    словаря — (шкала, имя): у разных шкал бывают одноимённые критерии с разным
    смыслом, и месяц складывал бы их в одну строку."""
    index: dict[tuple, int] = {}
    criteria: list[dict] = []
    directions: dict[str, str] = {}
    out = []
    for talk in talks:
        scale = str(talk.get("scale_direction") or "none")
        directions.setdefault(scale, talk.get("direction") or "Без направления")
        human = talk.get("human") or {}
        changes = {c.get("position"): c for c in human.get("changes") or []}
        applicable, fails, deficiencies, doubtful, notes, fixes = [], [], [], [], [], []
        for position, criterion in enumerate(talk.get("criteria") or []):
            name = str(criterion.get("name") or f"Критерий {position + 1}")
            key = (scale, name)
            idx = index.get(key)
            if idx is None:
                idx = index[key] = len(criteria)
                criteria.append({"n": name, "c": bool(criterion.get("is_critical")), "s": scale})
            verdict = digest_data.effective_verdict(talk, criterion, position)
            ai = str(criterion.get("ai") or "")
            conf = digest_data._num(criterion.get("conf"))
            if (criterion.get("source") == "transcript" and conf is not None
                    and conf <= config.REVIEW_MODEL_CONF):
                doubtful.append(idx)
            change = changes.get(position)
            if change:
                fixes.append([idx, _short(change.get("ai")), _short(change.get("human")),
                              _clean(change.get("note"), 200)])
            if verdict in digest_data.NOT_COUNTED or verdict is None:
                continue
            applicable.append(idx)
            if verdict in digest_data.FAIL:
                fails.append(idx)
            elif verdict == digest_data.DEFICIENCY:
                deficiencies.append(idx)
            else:
                continue
            # Нарушение поставил человек — довод его, а обоснование ИИ доказывает
            # обратное; иначе — комментарий и цитата оценщика.
            if change and verdict != ai:
                notes.append([idx, "x" if verdict in digest_data.FAIL else "~",
                              _clean(change.get("note"), 240), ""])
            else:
                notes.append([idx, "x" if verdict in digest_data.FAIL else "~",
                              _clean(criterion.get("comment"), 240),
                              _clean(criterion.get("evidence"), 200)])
        reviewed = bool(talk.get("outcome")) or talk.get("human_score") is not None
        corrected = talk.get("outcome") == "adjudicated" or bool(human.get("changes"))
        entry = {
            "k": talk["kind"], "i": int(talk["id"]), "f": talk["family"],
            "op": talk.get("operator") or "—", "oid": talk.get("operator_id"),
            "t": talk.get("time") or "", "sd": talk.get("subject_direction"), "sc": scale,
            "ai": talk.get("ai_score"), "hs": talk.get("human_score"), "out": talk.get("outcome"),
            "rv": 1 if reviewed else 0, "co": 1 if corrected else 0,
            "cr": 1 if digest_data.is_critical_failure(talk) else 0,
            "ap": applicable, "fl": fails, "df": deficiencies, "nt": notes,
        }
        if doubtful:
            entry["dq"] = doubtful
        if fixes:
            entry["fx"] = fixes
        if talk.get("unchecked_weight"):
            entry["uw"] = int(talk["unchecked_weight"])
        if talk.get("asr_conf") is not None and talk["asr_conf"] < config.ASR_CONF_HARD:
            entry["asr"] = 1
        if talk.get("media_failed"):
            entry["mf"] = 1
        if talk.get("end_party"):
            entry["end"] = talk["end_party"]
        if talk.get("overall_comment"):
            entry["sum"] = _clean(talk["overall_comment"], 300)
        if human.get("comment"):
            entry["hc"] = _clean(human["comment"], 200)
        out.append(entry)
    return {"v": VERSION, "day": day.isoformat(), "criteria": criteria, "directions": directions,
            "talks": out}


def compute_day(cur, department, day) -> dict | None:
    """Слепок дня с отпечатками. None — оценённых разговоров нет."""
    talks = digest_data.collect_day(cur, day, department, None, with_transcripts=False,
                                    with_notes=True)
    if not talks:
        return None
    return {"facts": compact(talks, day), "inputs_hash": digest_data.inputs_hash(talks),
            "reviews_mark": reviews_mark_of(talks), "count": len(talks)}


# ── отпечатки дней ────────────────────────────────────────────────────────────

_KEY_SQL = ("rc.subject_kind || ':' || rc.call_id::text || ':' || "
            "COALESCE(NULLIF(rc.payload->>'_evaluation_run_id', ''), rc.model, '')")


def day_signatures(cur, department, first, last) -> dict[str, dict]:
    """{день: {hash, evaluated, reviewed, corrected}} — состояние оценок каждого дня
    периода одним запросом, без карточек.

    hash — тот же отпечаток, что data.inputs_hash: ключи «вид:id:прогон» по
    байтам (COLLATE "C" — как сортировка строк в Python), sha256, первые 32
    знака. Сверка с Python на живом дне 17.10.2026 — совпали."""
    from .. import api
    scope_sql, scope_params = api._direction_predicate(cur, None, department, api._SUBJECT_DIRECTION)
    if scope_sql is None:
        return {}
    cur.execute(
        f"""SELECT {api._SUBJECT_DAY} AS day, count(*),
                   left(encode(digest(string_agg({_KEY_SQL}, E'\\n'
                                                 ORDER BY ({_KEY_SQL}) COLLATE "C"), 'sha256'),
                               'hex'), 32),
                   count(*) FILTER (WHERE m.review_outcome IS NOT NULL
                                       OR {api._SUBJECT_HUMAN_SCORE} IS NOT NULL),
                   count(*) FILTER (WHERE m.review_outcome = 'adjudicated')
              FROM {api._LATEST_EVALUATIONS}""" + api._SUBJECT_JOIN + api._EVAL_META_JOIN
        + f" WHERE {api._SUBJECT_DAY} BETWEEN %s AND %s" + api._SUBJECT_EXISTS + scope_sql
        + " GROUP BY 1",
        (first, last, *scope_params))
    out = {}
    for day, evaluated, digest_hash, reviewed, corrected in cur.fetchall():
        if day is None:
            continue
        out[day.isoformat()] = {"hash": digest_hash, "evaluated": int(evaluated or 0),
                                "reviewed": int(reviewed or 0), "corrected": int(corrected or 0)}
    return out


def signature_mark(signature: dict) -> str:
    return f"{signature.get('reviewed', 0)}:{signature.get('corrected', 0)}"


# ── таблица ───────────────────────────────────────────────────────────────────

def _schema_missing(error) -> bool:
    import psycopg2
    return isinstance(error, (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn))


def read_days(cur, department, days) -> dict[str, dict]:
    days = sorted(set(days or []))
    if not days:
        return {}
    try:
        cur.execute(
            """SELECT facts_day, version, inputs_hash, reviews_mark, talks_count, facts, computed_at
                 FROM ai_qa_day_facts
                WHERE department_code = %s AND facts_day = ANY(%s::date[])""",
            (department, days))
    except Exception as error:
        if _schema_missing(error):
            return {}
        raise
    return {day.isoformat(): {"version": version, "inputs_hash": inputs_hash,
                              "reviews_mark": reviews_mark, "count": count, "facts": facts,
                              "computed_at": computed_at}
            for day, version, inputs_hash, reviews_mark, count, facts, computed_at in cur.fetchall()}


def save_day(department, day, computed) -> None:
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_qa_day_facts (department_code, facts_day, version, inputs_hash,
                                                reviews_mark, talks_count, facts, computed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (department_code, facts_day) DO UPDATE
                      SET version = EXCLUDED.version, inputs_hash = EXCLUDED.inputs_hash,
                          reviews_mark = EXCLUDED.reviews_mark, talks_count = EXCLUDED.talks_count,
                          facts = EXCLUDED.facts, computed_at = now()""",
                (department, day, VERSION, computed["inputs_hash"], computed["reviews_mark"],
                 int(computed["count"]), Json(computed["facts"])))
    finally:
        conn.close()


def forget_day(department, day) -> None:
    """День остался без оценённых разговоров (все исключили) — слепок вон."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ai_qa_day_facts WHERE department_code = %s AND facts_day = %s",
                        (department, day))
    finally:
        conn.close()


def refresh_day(department, day) -> dict | None:
    """Пересчитать и сохранить слепок одного дня. Возвращает слепок или None."""
    department = config.normalise_department_code(department)
    day = day if isinstance(day, date) else date.fromisoformat(str(day))
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        computed = compute_day(cur, department, day)
        cur.close()
    finally:
        conn.close()
    if computed is None:
        forget_day(department, day)
        return None
    save_day(department, day, computed)
    return computed


def is_fresh(row: dict | None, signature: dict) -> bool:
    return bool(row) and int(row.get("version") or 0) == VERSION \
        and row.get("inputs_hash") == signature.get("hash") \
        and (row.get("reviews_mark") or "") == signature_mark(signature)


# ── фон ───────────────────────────────────────────────────────────────────────

# Свой пул на одно место: слепки — работа базы, модель они не зовут, и ставить их
# в очередь за ночной генерацией сводок незачем; одно место — чтобы не грузить
# базу параллельными чтениями карточек.
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-qa-facts")
_QUEUED: set[tuple] = set()
_QUEUED_LOCK = threading.Lock()


def _refresh_many(department, days):
    for day_iso in days:
        try:
            refresh_day(department, date.fromisoformat(day_iso))
        except Exception:
            logging.exception("ai-qa факты %s %s: пересчёт упал", department, day_iso)
        finally:
            with _QUEUED_LOCK:
                _QUEUED.discard((department, day_iso))


def queue(department, days) -> int:
    """Поставить дни на пересчёт в фоне. Возвращает, сколько поставлено (уже
    стоящие в очереди не дублируются)."""
    department = config.normalise_department_code(department)
    fresh = []
    with _QUEUED_LOCK:
        for day_iso in sorted(set(days or [])):
            key = (department, day_iso)
            if key in _QUEUED:
                continue
            _QUEUED.add(key)
            fresh.append(day_iso)
    if fresh:
        _POOL.submit(_refresh_many, department, fresh)
    return len(fresh)


def queued(department, first, last) -> list[str]:
    """Дни периода, которые ещё ждут пересчёта или считаются сейчас."""
    department = config.normalise_department_code(department)
    with _QUEUED_LOCK:
        return sorted(day_iso for dept, day_iso in _QUEUED
                      if dept == department and first.isoformat() <= day_iso <= last.isoformat())


# ── сборка периода ────────────────────────────────────────────────────────────

def ensure(department, first, last, *, inline_limit=INLINE_LIMIT, background=True) -> dict:
    """Слепки дней периода, свежие по возможности.

    Возвращает {'days': {день: слепок}, 'signatures': {день: отпечаток},
    'pending': [дни, которые ещё считаются], 'count': сколько разговоров}.
    Устаревший слепок отдаётся как есть (с пометкой в pending): частичные
    цифры честнее пустого экрана, а свежие придут со следующим чтением.
    inline_limit=None — пересчитать всё устаревшее здесь же (фоновая задача)."""
    department = config.normalise_department_code(department)
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        signatures = day_signatures(cur, department, first, last)
        stored = read_days(cur, department, list(signatures))
        cur.close()
    finally:
        conn.close()
    days: dict[str, dict] = {}
    stale: list[str] = []
    for day_iso, signature in sorted(signatures.items()):
        row = stored.get(day_iso)
        if is_fresh(row, signature):
            days[day_iso] = row["facts"]
            continue
        stale.append(day_iso)
        if row and row.get("facts"):
            days[day_iso] = row["facts"]
    pending: list[str] = []
    if stale and (inline_limit is None or len(stale) <= inline_limit):
        for day_iso in stale:
            try:
                computed = refresh_day(department, date.fromisoformat(day_iso))
            except Exception:
                logging.exception("ai-qa факты %s %s: пересчёт в запросе упал", department, day_iso)
                pending.append(day_iso)
                continue
            if computed:
                days[day_iso] = computed["facts"]
            else:
                days.pop(day_iso, None)
    elif stale:
        pending = list(stale)
        if background:
            queue(department, stale)
    count = sum(s.get("evaluated", 0) for s in signatures.values())
    return {"days": days, "signatures": signatures, "pending": pending, "count": count}


def combined_hash(signatures: dict) -> str:
    """Отпечаток периода — из отпечатков его дней (для «оценки изменились»)."""
    import hashlib
    keys = sorted(f"{day}:{s.get('hash')}:{signature_mark(s)}" for day, s in (signatures or {}).items())
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:32]


def backfill(days_back: int = BACKFILL_DAYS, today: date | None = None) -> dict:
    """Догнать слепки за последние дни по всем отделам — в фоне, без модели.

    Зовётся при старте и раз в сутки перед ночной выборкой: в обычный день
    устаревших дней нет и работа — один запрос отпечатков на отдел."""
    today = today or date.today()
    first = today - timedelta(days=int(days_back))
    out = {}
    for code in config.DEPARTMENT_CODES:
        try:
            result = ensure(code, first, today, inline_limit=0, background=True)
            out[code] = len(result["pending"])
        except Exception:
            logging.exception("ai-qa факты %s: догон упал", code)
            out[code] = None
    return out
