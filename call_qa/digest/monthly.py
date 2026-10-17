# -*- coding: utf-8 -*-
"""Сводка за месяц: ИИ пишет по прошедшему месяцу раздел на каждую шкалу и
«Главное» по отделу — как сводка дня, только из цифр и сводок дней за месяц.

КОГДА ПИШЕТСЯ. После ночной выборки в первые дни нового месяца: выборка оценивает
вчерашний день, и утром первого числа оценён последний день прошлого месяца. В
первые AUTO_DAYS дней сводка переписывается, если второй проход выборки добрал
оценки прошлого месяца; дальше — только кнопкой «Обновить» (новый счёт за
модель — осознанное решение человека, как и у дня).

ИЗ ЧЕГО. Из тех же слепков дней (facts.py), что и экран периода, — цифры месяца
сходятся с цифрами периода и дня. Модель получает таблицы месяца и прошлого
месяца, критические случаи, замечания оценщика по разговорам с ошибками и
сводки дней, которые писала сама каждое утро; транскриптов она не видит.

ПРАВА — как у сводки дня (service): зрителю со скоупом — разделы, все люди
которых в его направлениях, и без «Главного».
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from . import facts as day_facts
from . import period, prompts, render, service, store

# В первые дни месяца сводка за прошлый месяц переписывается сама, если выборка
# добрала его оценки; дальше — только по кнопке.
AUTO_DAYS = 3
STATS_CRITERIA = 15
STATS_PEOPLE = 25


def today_almaty() -> date:
    return datetime.now(ZoneInfo("Asia/Almaty")).date()


def previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_text(month: date) -> str:
    return period.period_title(*period.month_bounds(month))


def _stored(department: str, month: date) -> dict | None:
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        digest = store.month_get(cur, department, month)
        cur.close()
    finally:
        conn.close()
    return digest


def _header_stats(totals: dict) -> dict:
    """Четыре клетки над текстом — теми же ключами, что у дня (prompts.header_line,
    render.drop_header_stats)."""
    return {key: totals.get(key) for key in ("evaluated", "operators", "ai_avg", "critical",
                                             "below_60", "reviewed", "human_avg")}


_PUBLIC_KEYS = ("evaluated", "calls", "chats", "ai_avg", "ai_min", "below_60", "critical",
                "critical_share", "reviewed", "corrected", "human_avg", "operators", "days")


def _public(totals: dict | None, previous: dict | None = None) -> dict | None:
    if not totals:
        return None
    out = {key: totals.get(key) for key in _PUBLIC_KEYS}
    out["previous"] = {key: previous.get(key) for key in _PUBLIC_KEYS} if previous else None
    return out


def _usual(previous_totals: dict | None) -> dict | None:
    if not previous_totals or previous_totals.get("ai_avg") is None:
        return None
    return {"ai_avg": previous_totals["ai_avg"], "n": previous_totals.get("evaluated")}


def incomplete(digest) -> bool:
    """Сводка дописана не до конца — как у дня (service.incomplete)."""
    return service.incomplete(digest)


def generate(department: str, month: date, *, triggered_by: str = "manual", force: bool = False,
             generate_fn=None) -> dict:
    """Написать сводку за месяц отдела или дописать её.

    Возвращает {'status': 'ready' | 'fresh' | 'running' | 'empty' | 'failed', …}.
    Слепки месяца и прошлого месяца досчитываются здесь же (задача идёт в пуле,
    запрос экрана её не ждёт). Набор оценок тот же и сводка дописана — модель не
    зовётся; изменился — переписываются все разделы (месяц — редкая и большая
    работа, выгадывать на разделах тут нечего); упавший раздел дописывается
    следующим запуском, остальные берутся как есть."""
    department = config.normalise_department_code(department)
    month = month.replace(day=1)
    first, last = period.month_bounds(month)
    generate_fn = generate_fn or service._generate_article
    if not store.month_claim(department, month, triggered_by):
        return {"status": "running"}
    started = time.monotonic()
    title = month_text(month)
    try:
        built = period.build(department, first, last, None, inline_limit=None, with_previous=True)
        talks = built["talks"]
        if not talks:
            store.month_release(department, month, forget_pending=True)
            return {"status": "empty"}
        fingerprint = built["inputs_hash"]
        digest = _stored(department, month)
        if (not force and digest and digest.get("status") == "ready"
                and digest.get("inputs_hash") == fingerprint and not incomplete(digest)):
            store.month_release(department, month)
            return {"status": "fresh"}
        same_inputs = bool(digest) and digest.get("inputs_hash") == fingerprint and not force
        stored = {s.get("key"): s for s in (digest or {}).get("sections") or []
                  if isinstance(s, dict) and s.get("html") and not s.get("error")}

        conn = config.connect_ro()
        try:
            cur = conn.cursor()
            cur.execute("SET client_encoding TO 'UTF8'")
            department_name = service._department_name(cur, department)
            day_digests = period.day_digests_for(cur, department, first, last, None)
            cur.close()
        finally:
            conn.close()

        summary, previous = built["summary"], built["previous"]
        pfirst, plast = period.previous_period(first, last)
        refs = {t["ref"]: period.ref_entry(t) for t in talks}
        usage: dict = {}
        models: Counter = Counter()
        sections_out, errors, stats_by_key = [], [], {}
        rewritten = attempted = 0
        for section in summary["sections"]:
            key = section["key"]
            own = [t for t in talks if t["scale"] == key]
            own_summary = period.summarize(own, built["criteria"], built["directions"],
                                           first=first, last=last)
            prev_summary = None
            if built.get("previous_talks"):
                prev_own = [t for t in built["previous_talks"] if t["scale"] == key]
                if prev_own:
                    prev_summary = period.summarize(prev_own, built["previous_criteria"],
                                                    built["previous_directions"],
                                                    first=pfirst, last=plast)
            prev_totals = prev_summary["totals"] if prev_summary else None
            header = _header_stats(own_summary["totals"])
            usual = _usual(prev_totals)
            entry = {"key": key, "direction_id": int(key) if str(key).isdigit() else None,
                     "direction": section["direction"], "family": section["family"],
                     "subject_directions": section["subject_directions"],
                     "stats": _public(own_summary["totals"], prev_totals),
                     "usual": usual, "headline": "", "html": "", "refs": [], "error": None}
            stats_by_key[key] = {"evaluated": own_summary["totals"]["evaluated"],
                                 "ai_avg": own_summary["totals"]["ai_avg"],
                                 "critical": own_summary["totals"]["critical"],
                                 "previous": ({"ai_avg": prev_totals.get("ai_avg"),
                                               "critical": prev_totals.get("critical")}
                                              if prev_totals else None)}
            kept = stored.get(key)
            if kept and same_inputs:
                entry.update(headline=kept.get("headline") or "", html=kept["html"],
                             refs=kept.get("refs") or [])
                sections_out.append(entry)
                continue
            attempted += 1
            section_refs = {t["ref"]: refs[t["ref"]] for t in own}
            own_digests = [d for d in day_digests if d.get("title") == f"«{section['direction']}»"]
            context = period.context_text(
                department_name=department_name, first=first, last=last,
                scope_text=f"направление «{section['direction']}»", summary=own_summary,
                previous=prev_summary, talks=own, criteria=built["criteria"],
                day_digests=own_digests)
            try:
                text, meta = generate_fn(
                    prompts.month_section_system(department, section["direction"], title),
                    prompts.month_section_user(title, department_name, section["direction"],
                                               prompts.header_line(header, usual), context),
                    max_tokens=prompts.SECTION_MAX_TOKENS)
                service._usage_add(usage, {**(meta.get("usage") or {}), "calls": 1})
                models[f"{meta.get('provider')}:{meta.get('model')}"] += 1
                headline, body = render.parse_envelope(text)
                html, used = render.render(body, section_refs)
                html = render.drop_header_stats(html, header, usual)
                if not html:
                    raise service.DigestError("ИИ вернул пустой ответ")
                entry.update(headline=headline or render.headline_from(html), html=html,
                             refs=sorted(set(used)))
                rewritten += 1
            except Exception as error:   # noqa: BLE001 — раздел не роняет соседей
                logging.exception("ai-qa сводка месяца %s %s: раздел «%s» не написан",
                                  department, month, section["direction"])
                if kept:
                    entry.update(headline=kept.get("headline") or "", html=kept["html"],
                                 refs=kept.get("refs") or [], outdated=True)
                    errors.append(f"{section['direction']}: {service._cause(error)} (оставлен прежний текст)")
                else:
                    entry["error"] = service._section_error(error)
                    errors.append(f"{section['direction']}: {service._cause(error)}")
            sections_out.append(entry)

        written = [s for s in sections_out if s["html"]]
        if not written:
            store.month_release(department, month,
                                error=f"{service._cause_of(errors)} — попробуйте ещё раз через несколько минут.")
            return {"status": "failed", "errors": errors}

        whole_header = _header_stats(summary["totals"])
        whole_usual = _usual(previous["totals"] if previous else None)
        old_overview = (digest or {}).get("overview_html")
        old_outdated = bool(((digest or {}).get("stats") or {}).get("overview_outdated"))
        overview_html, headline, overview_outdated = None, written[0]["headline"], False
        if len(written) > 1:
            same_sections = {s["key"] for s in written} == set(stored)
            if same_inputs and not rewritten and same_sections and old_overview and not old_outdated:
                overview_html = old_overview
                headline = digest.get("headline") or headline
            else:
                numbers = {f"{t['kind']}:{t['id']}": t["ref"] for t in talks}
                brief = [{"key": s["key"], "direction": s["direction"],
                          "text": render.to_text(s["html"], numbers)} for s in written]
                try:
                    text, meta = generate_fn(
                        prompts.month_overview_system(department, department_name, title),
                        prompts.month_overview_user(title, department_name,
                                                    prompts.header_line(whole_header, whole_usual),
                                                    brief, stats_by_key),
                        max_tokens=prompts.OVERVIEW_MAX_TOKENS)
                    service._usage_add(usage, {**(meta.get("usage") or {}), "calls": 1})
                    models[f"{meta.get('provider')}:{meta.get('model')}"] += 1
                    overview_headline, body = render.parse_envelope(text)
                    overview_html, _used = render.render(body, refs)
                    overview_html = render.drop_header_stats(overview_html, whole_header, whole_usual) or None
                    if not overview_html:
                        raise service.DigestError("ИИ вернул пустой ответ")
                    headline = overview_headline or render.headline_from(overview_html) or headline
                except Exception as error:   # noqa: BLE001
                    logging.exception("ai-qa сводка месяца %s %s: «Главное» не написано", department, month)
                    overview_html = None
                    if old_overview:
                        overview_html, overview_outdated = old_overview, True
                        headline = digest.get("headline") or headline
                    errors.append(f"Главное: {service._cause(error)}")
        elif len(sections_out) > 1 and old_overview:
            overview_html, overview_outdated = old_overview, True
            headline = digest.get("headline") or headline

        public = period.public_summary(summary, people_limit=STATS_PEOPLE,
                                       criteria_limit=STATS_CRITERIA, cases_limit=0)
        save_stats = {
            "from": first.isoformat(), "to": last.isoformat(),
            "previous_from": pfirst.isoformat(), "previous_to": plast.isoformat(),
            "department": _public(summary["totals"], previous["totals"] if previous else None),
            "usual": whole_usual, "days": public["days"], "criteria": public["criteria"],
            "people": public["people"], "critical_cases": len(summary["critical_cases"]),
        }
        if overview_outdated:
            save_stats["overview_outdated"] = True
        total_usage: dict = {}
        service._usage_add(total_usage, (digest or {}).get("usage"))
        service._usage_add(total_usage, usage)
        elapsed = round(time.monotonic() - started, 1)
        try:
            store.month_save(department, month, headline=headline, overview_html=overview_html,
                             sections=sections_out, stats=save_stats, inputs_hash=fingerprint,
                             inputs_count=len(talks),
                             model=(models.most_common(1)[0][0] if models else (digest or {}).get("model")),
                             usage=total_usage, elapsed_s=elapsed,
                             error="; ".join(errors)[:1000] if errors else None)
        except Exception as error:
            logging.exception("ai-qa сводка месяца %s %s: не удалось сохранить", department, month)
            raise service.DigestError("Не удалось сохранить сводку") from error
        logging.info("ai-qa сводка месяца %s %s (%s): разделов %s (переписано %s из %s), разговоров %s,"
                     " %s с, токены %s, сбои %s", department, month, triggered_by, len(written),
                     rewritten, attempted, len(talks), elapsed, usage, errors or "нет")
        return {"status": "ready", "sections": len(written), "rewritten": rewritten,
                "attempted": attempted, "errors": errors, "elapsed_s": elapsed}
    except Exception as error:
        logging.exception("ai-qa сводка месяца %s %s: генерация упала", department, month)
        try:
            store.month_release(department, month,
                                error=f"{service._cause(error)} — попробуйте ещё раз через несколько минут.")
        except Exception:
            logging.exception("ai-qa сводка месяца %s %s: не удалось отпустить генерацию", department, month)
        return {"status": "failed", "errors": [service._cause(error)]}


# ── чтение ────────────────────────────────────────────────────────────────────

def read(department: str, month: date, allowed_direction_ids=None, *, inputs_hash=None,
         today: date | None = None) -> dict:
    """Сводка месяца для зрителя: видимые разделы и признаки «устарела» /
    «не дописана». Цифры экран берёт у периода (они живые), здесь — тексты.

    inputs_hash — отпечаток месяца, уже посчитанный чтением периода; без него
    он считается здесь одним запросом отпечатков дней."""
    department = config.normalise_department_code(department)
    month = month.replace(day=1)
    first, last = period.month_bounds(month)
    today = today or today_almaty()
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        digest = store.month_get(cur, department, month)
        if digest and inputs_hash is None:
            inputs_hash = day_facts.combined_hash(day_facts.day_signatures(cur, department, first, last))
        cur.close()
    finally:
        conn.close()
    restricted = allowed_direction_ids is not None
    stored = [s for s in (digest or {}).get("sections") or [] if isinstance(s, dict)]
    sections, broken = [], False
    for entry in stored:
        if not service._visible(entry, allowed_direction_ids):
            continue
        if entry.get("error") or not entry.get("html") or entry.get("outdated"):
            broken = True
        sections.append({
            "key": entry.get("key"), "direction": entry.get("direction"),
            "family": entry.get("family"), "headline": entry.get("headline") or "",
            "html": entry.get("html") or "", "error": entry.get("error"),
            "stats": entry.get("stats"), "usual": entry.get("usual"),
            "outdated": bool(entry.get("outdated")),
        })
    if not restricted:
        broken = bool(digest) and incomplete(digest)
    stats = (digest or {}).get("stats") or {}
    return {
        "month": period.month_key(month), "from": first.isoformat(), "to": last.isoformat(),
        "title": month_text(month), "complete": period.month_complete(month, today),
        "status": (digest or {}).get("status") or "none",
        "running": bool((digest or {}).get("running")),
        "headline": service._headline_for(digest, sections, restricted),
        "overview_html": None if restricted else (digest or {}).get("overview_html"),
        "sections": sections,
        # Снимок цифр на момент генерации — только главе: у зрителя со скоупом
        # итоги отдела — чужие, а свои живые цифры ему отдаёт период.
        "stats": None if restricted else stats.get("department"),
        "stale": bool(digest and digest.get("status") == "ready" and digest.get("inputs_hash")
                      and inputs_hash and digest["inputs_hash"] != inputs_hash),
        "incomplete": bool(digest) and (digest or {}).get("status") == "ready" and broken,
        "generated_at": (digest or {}).get("generated_at"),
        "model": (digest or {}).get("model"),
        "error": (digest or {}).get("last_error") if (digest or {}).get("status") != "ready" else None,
    }


def status(department: str, month: date) -> dict:
    """Состояние без чтения — для опроса, пока сводка пишется."""
    department = config.normalise_department_code(department)
    digest = _stored(department, month.replace(day=1))
    return {"month": period.month_key(month), "status": (digest or {}).get("status") or "none",
            "running": bool((digest or {}).get("running")),
            "generated_at": (digest or {}).get("generated_at")}


def known(department: str, month_keys, allowed_direction_ids=None, *, today: date | None = None) -> dict:
    """{ГГГГ-ММ: {status, headline, running, hidden, complete, generated_at}} — для
    списка дней (карточка месяца и ссылки у заголовков месяцев)."""
    department = config.normalise_department_code(department)
    today = today or today_almaty()
    months = []
    for key in month_keys or []:
        try:
            months.append(period.parse_month(key))
        except period.PeriodError:
            continue
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        found = store.months(cur, department, months)
        cur.close()
    finally:
        conn.close()
    restricted = allowed_direction_ids is not None
    allowed = {int(x) for x in (allowed_direction_ids or [])}
    out = {}
    for month in months:
        key = period.month_key(month)
        info = found.get(key) or {}
        headline = info.get("headline") or ""
        hidden = False
        if restricted and info:
            sections = info.get("sections") or []
            visible = [s for s in sections if service._visible(s, allowed_direction_ids)]
            headline = next((s.get("headline") for s in visible if s.get("headline")), "")
            hidden = not visible and any(
                allowed & {int(x) for x in s.get("subject_directions") or []} for s in sections)
        out[key] = {"status": info.get("status") or "none", "headline": headline,
                    "running": bool(info.get("running")), "hidden": hidden,
                    "complete": period.month_complete(month, today),
                    "generated_at": info.get("generated_at")}
    return out


# ── ночной хук ────────────────────────────────────────────────────────────────

def generate_due(department: str, *, today: date | None = None, triggered_by: str = "after-sample",
                 generate_fn=None) -> dict | None:
    """После ночной выборки: сводка за прошлый месяц, если её нет или она не
    дописана; в первые AUTO_DAYS дней месяца — и если оценки месяца изменились
    (generate сам ответит «fresh», когда менять нечего). None — делать нечего."""
    today = today or today_almaty()
    month = previous_month(today)
    digest = _stored(config.normalise_department_code(department), month)
    if digest and digest.get("running"):
        return None
    if (digest and digest.get("status") == "ready" and not incomplete(digest)
            and today.day > AUTO_DAYS):
        return None
    return generate(department, month, triggered_by=triggered_by, generate_fn=generate_fn)
