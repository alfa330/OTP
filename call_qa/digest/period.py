# -*- coding: utf-8 -*-
"""Период в «Сводке»: цифры за несколько дней (до месяца) и чат о них.

ИЗ ЧЕГО. Из слепков дней (facts.py), а не из карточек: период — тысячи
разговоров, и читать их карточки при каждом открытии нельзя (см. шапку facts).
Слепок сверяется с живыми оценками дня отпечатком и пересчитывается, если
устарел, так что цифры периода сходятся с экраном дня — обе стороны считают
одно и то же одним кодом (data.effective_verdict, data.is_critical_failure).

ЧТО СЧИТАЕТСЯ. Итоги периода и такие же итоги прошлого периода той же длины
(«обычно» для периода), ряд по дням для графиков, разрез по шкалам, по
критериям (доля ошибок из применимых, против прошлого периода) и по людям (с
тем, на чём человек ошибается чаще всего), критические случаи с доводом
оценщика. Всё это считает код: модель в счёте ошибается, а цифры периода
руководитель переносит в отчёт.

ЧАТ. Модели отдаётся не транскрипты (месяц ОП — миллионы знаков), а готовые
таблицы, критические случаи и замечания оценщика по разговорам с ошибками, плюс
сводки дней, которые ИИ уже написал по каждому дню (в них — «где не хватает
знаний» и «что беспокоит клиентов», чего в вердиктах нет). К вопросу, в котором
названы сотрудник, критерий или день, подкладывается их полный разрез — это
хвост сообщения человека, а не системной части: системная часть одинакова от
вопроса к вопросу, и Vertex снимает её в кеш.

ПРАВА. Скоуп зрителя — по направлению сотрудника (как у списков раздела); СВ
видит разделы только своих направлений, сводки дней — только видимые ему
разделы и без «Главного» (service._visible).
"""
from __future__ import annotations

import calendar
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

from .. import config
from . import data as digest_data
from . import facts as day_facts
from . import render
from . import store

PERIOD_MAX_DAYS = 31
QUESTION_LIMIT = 1000

# Потолки контекста чата (в знаках). Месяц ОП целиком — около 450 тыс. знаков,
# то есть 110–140 тыс. токенов: столько же, сколько день с транскриптами, и
# отвечает модель за те же 8–12 с; второй вопрос дешевле за счёт кеша.
CRITICAL_BUDGET = 90_000
NOTES_BUDGET = 80_000
DIGESTS_BUDGET = 200_000
DIGEST_TEXT_LIMIT = 2600
PEOPLE_LIMIT = 150
CRITERIA_PER_DIRECTION = 25
FOCUS_BUDGET = 45_000
CRITICAL_CASES_LIMIT = 250

MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                   "сентября", "октября", "ноября", "декабря")
MONTHS_TITLE = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
                "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


class PeriodError(ValueError):
    """Отказ по существу — текст для человека."""


# ── границы ───────────────────────────────────────────────────────────────────

def parse_day(value, name: str) -> date:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise PeriodError(f"{name}: ожидается дата ГГГГ-ММ-ДД")
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise PeriodError(f"{name}: такой даты нет") from error


def parse_period(from_value, to_value, today: date) -> tuple[date, date]:
    """Границы периода из запроса: не длиннее PERIOD_MAX_DAYS, не позже сегодня."""
    first, last = parse_day(from_value, "from"), parse_day(to_value, "to")
    if last < first:
        first, last = last, first
    if last > today:
        raise PeriodError("Период бывает только за прошедшие дни и сегодняшний")
    if (last - first).days + 1 > PERIOD_MAX_DAYS:
        raise PeriodError(f"Период — не больше {PERIOD_MAX_DAYS} дней: выберите месяц или короче")
    return first, last


def parse_month(value) -> date:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", raw):
        raise PeriodError("month: ожидается месяц ГГГГ-ММ")
    year, month = int(raw[:4]), int(raw[5:])
    if not 1 <= month <= 12:
        raise PeriodError("month: такого месяца нет")
    return date(year, month, 1)


def month_bounds(month_first: date) -> tuple[date, date]:
    first = month_first.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def is_whole_month(first: date, last: date) -> bool:
    return (first.day == 1 and (first.year, first.month) == (last.year, last.month)
            and last == month_bounds(first)[1])


def month_complete(month_first: date, today: date) -> bool:
    """Месяц прошёл: его последний день — вчера или раньше. Ночная выборка оценивает
    вчерашний день утром, поэтому сводка за месяц собирается с первого числа."""
    return month_bounds(month_first)[1] < today


def previous_period(first: date, last: date) -> tuple[date, date]:
    """Прошлый период той же длины, вплотную перед этим; у целого месяца —
    прошлый календарный месяц (у него своя длина)."""
    if is_whole_month(first, last):
        return month_bounds((first - timedelta(days=1)).replace(day=1))
    length = (last - first).days + 1
    return first - timedelta(days=length), first - timedelta(days=1)


def period_title(first: date, last: date) -> str:
    """«сентябрь 2026», «1–15 октября», «16 сентября — 16 октября»."""
    if is_whole_month(first, last):
        return f"{MONTHS_TITLE[first.month - 1].lower()} {first.year}"
    if (first.year, first.month) == (last.year, last.month):
        if first == last:
            return f"{first.day} {MONTHS_GENITIVE[first.month - 1]}"
        return f"{first.day}–{last.day} {MONTHS_GENITIVE[first.month - 1]}"
    return (f"{first.day} {MONTHS_GENITIVE[first.month - 1]} — "
            f"{last.day} {MONTHS_GENITIVE[last.month - 1]}")


# ── разговоры периода из слепков ──────────────────────────────────────────────

def talks_of(facts_by_day: dict, allowed_direction_ids=None) -> tuple[list[dict], list[dict], dict]:
    """Слепки дней → разговоры периода в скоупе зрителя.

    Возвращает (разговоры, общий словарь критериев, {шкала: название}). У
    разговора номера критериев переведены в общий словарь периода (у каждого дня
    он свой): applicable/fails/deficiencies/doubtful — списки номеров, notes и
    fixes — с номером первым элементом."""
    restricted = allowed_direction_ids is not None
    allowed = {int(x) for x in (allowed_direction_ids or [])}
    index: dict[tuple, int] = {}
    criteria: list[dict] = []
    directions: dict[str, str] = {}
    talks: list[dict] = []
    for day_iso in sorted(facts_by_day):
        facts = facts_by_day[day_iso] or {}
        local = facts.get("criteria") or []
        mapping = []
        for item in local:
            key = (str(item.get("s") or "none"), str(item.get("n") or ""))
            number = index.get(key)
            if number is None:
                number = index[key] = len(criteria)
                criteria.append({"name": key[1], "is_critical": bool(item.get("c")), "scale": key[0]})
            mapping.append(number)
        for scale, name in (facts.get("directions") or {}).items():
            directions.setdefault(str(scale), name)

        def remap(numbers):
            return [mapping[n] for n in numbers or [] if isinstance(n, int) and 0 <= n < len(mapping)]

        for raw in facts.get("talks") or []:
            if restricted and (raw.get("sd") is None or int(raw["sd"]) not in allowed):
                continue
            talk = {
                "day": facts.get("day") or day_iso, "kind": raw.get("k"), "id": raw.get("i"),
                "family": raw.get("f") or digest_data.family_of(raw.get("k") or config.SUBJECT_CALL),
                "operator": raw.get("op") or "—", "operator_id": raw.get("oid"),
                "time": raw.get("t") or "", "subject_direction": raw.get("sd"),
                "scale": str(raw.get("sc") or "none"), "ai_score": raw.get("ai"),
                "human_score": raw.get("hs"), "outcome": raw.get("out"),
                "reviewed": bool(raw.get("rv")), "corrected": bool(raw.get("co")),
                "critical": bool(raw.get("cr")), "unchecked_weight": raw.get("uw") or 0,
                "asr_low": bool(raw.get("asr")), "media_failed": bool(raw.get("mf")),
                "end_party": raw.get("end"), "summary": raw.get("sum") or "",
                "human_comment": raw.get("hc") or "",
                "applicable": remap(raw.get("ap")), "fails": remap(raw.get("fl")),
                "deficiencies": remap(raw.get("df")), "doubtful": remap(raw.get("dq")),
                "notes": [[mapping[n[0]], *n[1:]] for n in raw.get("nt") or []
                          if isinstance(n, list) and n and isinstance(n[0], int) and 0 <= n[0] < len(mapping)],
                "fixes": [[mapping[n[0]], *n[1:]] for n in raw.get("fx") or []
                          if isinstance(n, list) and n and isinstance(n[0], int) and 0 <= n[0] < len(mapping)],
            }
            talk["direction"] = directions.get(talk["scale"], "Без направления")
            talks.append(talk)
    talks.sort(key=lambda t: (t["day"], t["time"] or "99:99", str(t["kind"]), int(t["id"] or 0)))
    for number, talk in enumerate(talks, start=1):
        talk["ref"] = number
    return talks, criteria, directions


# ── цифры ─────────────────────────────────────────────────────────────────────

def _avg(values):
    values = [float(v) for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _share(part, whole):
    return round(part / whole, 3) if whole else None


def _totals(talks) -> dict:
    scored = [t["ai_score"] for t in talks if t.get("ai_score") is not None]
    critical = sum(1 for t in talks if t["critical"])
    return {
        "evaluated": len(talks),
        "calls": sum(1 for t in talks if t["family"] == digest_data.FAMILY_CALLS),
        "chats": sum(1 for t in talks if t["family"] == digest_data.FAMILY_CHATS),
        "ai_avg": _avg(scored),
        "ai_min": round(min(scored)) if scored else None,
        "below_60": sum(1 for s in scored if s < 60),
        "critical": critical,
        "critical_share": _share(critical, len(talks)),
        "reviewed": sum(1 for t in talks if t["reviewed"]),
        "corrected": sum(1 for t in talks if t["corrected"]),
        "human_avg": _avg([t.get("human_score") for t in talks]),
        "operators": len({str(t.get("operator_id") or t.get("operator")) for t in talks}),
        "days": len({t["day"] for t in talks}),
    }


def summarize(talks, criteria, directions, *, first: date, last: date) -> dict:
    """Все цифры периода: итоги, по дням, по шкалам, по критериям, по людям,
    критические случаи."""
    by_day: dict[str, list] = defaultdict(list)
    for talk in talks:
        by_day[talk["day"]].append(talk)
    days = []
    cursor = first
    while cursor <= last:
        iso = cursor.isoformat()
        group = by_day.get(iso) or []
        if group:
            totals = _totals(group)
            days.append({"day": iso, "evaluated": totals["evaluated"], "ai_avg": totals["ai_avg"],
                         "critical": totals["critical"], "below_60": totals["below_60"],
                         "reviewed": totals["reviewed"], "human_avg": totals["human_avg"],
                         "operators": totals["operators"]})
        else:
            days.append({"day": iso, "evaluated": 0, "ai_avg": None, "critical": 0, "below_60": 0,
                         "reviewed": 0, "human_avg": None, "operators": 0})
        cursor += timedelta(days=1)

    by_scale: dict[str, list] = defaultdict(list)
    for talk in talks:
        by_scale[talk["scale"]].append(talk)
    sections = []
    for scale, group in by_scale.items():
        families = Counter(t["family"] for t in group)
        totals = _totals(group)
        totals.update({
            "key": scale, "direction": directions.get(scale, "Без направления"),
            "family": families.most_common(1)[0][0],
            "subject_directions": sorted({int(t["subject_direction"]) for t in group
                                          if t.get("subject_direction") is not None}),
        })
        sections.append(totals)
    sections.sort(key=lambda s: (s["family"] != digest_data.FAMILY_CALLS, s["direction"].lower()))

    per_criterion: dict[int, dict] = {}
    for talk in talks:
        for number in talk["applicable"]:
            entry = per_criterion.get(number)
            if entry is None:
                meta = criteria[number]
                entry = per_criterion[number] = {
                    "number": number, "name": meta["name"], "is_critical": meta["is_critical"],
                    "scale": meta["scale"], "direction": directions.get(meta["scale"], "Без направления"),
                    "applicable": 0, "fail": 0, "deficiency": 0, "examples": [], "people": Counter()}
            entry["applicable"] += 1
        for number in talk["fails"]:
            entry = per_criterion.get(number)
            if entry is not None:
                entry["fail"] += 1
                entry["examples"].append(talk["ref"])
                entry["people"][talk["operator"]] += 1
        for number in talk["deficiencies"]:
            entry = per_criterion.get(number)
            if entry is not None:
                entry["deficiency"] += 1
                entry["examples"].append(talk["ref"])
                entry["people"][talk["operator"]] += 1
    criteria_rows = []
    for entry in per_criterion.values():
        bad = entry["fail"] + entry["deficiency"]
        criteria_rows.append({
            "name": entry["name"], "is_critical": entry["is_critical"], "scale": entry["scale"],
            "direction": entry["direction"], "applicable": entry["applicable"],
            "fail": entry["fail"], "deficiency": entry["deficiency"], "bad": bad,
            "bad_share": _share(bad, entry["applicable"]),
            "examples": entry["examples"][:3],
            "people": [{"name": name, "n": n} for name, n in entry["people"].most_common(3)],
        })
    criteria_rows.sort(key=lambda c: (-c["bad"], -(c["bad_share"] or 0), c["name"]))

    per_person: dict[str, dict] = {}
    for talk in talks:
        key = str(talk.get("operator_id") or talk.get("operator"))
        entry = per_person.get(key)
        if entry is None:
            entry = per_person[key] = {
                "key": key, "name": talk["operator"], "operator_id": talk.get("operator_id"),
                "directions": Counter(), "scores": [], "human": [], "talks": 0, "critical": 0,
                "fails": 0, "deficiencies": 0, "reviewed": 0, "worst": Counter(), "days": set(),
                "refs": [], "critical_refs": []}
        entry["talks"] += 1
        entry["directions"][talk["direction"]] += 1
        entry["days"].add(talk["day"])
        entry["refs"].append(talk["ref"])
        if talk.get("ai_score") is not None:
            entry["scores"].append(float(talk["ai_score"]))
        if talk.get("human_score") is not None:
            entry["human"].append(float(talk["human_score"]))
        entry["reviewed"] += 1 if talk["reviewed"] else 0
        if talk["critical"]:
            entry["critical"] += 1
            entry["critical_refs"].append(talk["ref"])
        entry["fails"] += len(talk["fails"])
        entry["deficiencies"] += len(talk["deficiencies"])
        for number in talk["fails"] + talk["deficiencies"]:
            entry["worst"][criteria[number]["name"]] += 1
    people = []
    for entry in per_person.values():
        people.append({
            "name": entry["name"], "operator_id": entry["operator_id"],
            "direction": entry["directions"].most_common(1)[0][0],
            "talks": entry["talks"], "days": len(entry["days"]),
            "ai_avg": _avg(entry["scores"]),
            "ai_min": round(min(entry["scores"])) if entry["scores"] else None,
            "critical": entry["critical"], "fails": entry["fails"],
            "deficiencies": entry["deficiencies"],
            "issues_per_talk": round((entry["fails"] + entry["deficiencies"]) / entry["talks"], 2),
            "reviewed": entry["reviewed"], "human_avg": _avg(entry["human"]),
            "worst": [{"name": name, "n": n} for name, n in entry["worst"].most_common(4)],
            "critical_refs": entry["critical_refs"][:6],
        })
    people.sort(key=lambda p: (-p["critical"], -p["issues_per_talk"],
                               p["ai_avg"] if p["ai_avg"] is not None else 101, p["name"]))

    critical_cases = []
    for talk in talks:
        if not talk["critical"]:
            continue
        reasons = []
        for note in talk["notes"]:
            number, mark = note[0], note[1] if len(note) > 1 else "x"
            if mark == "x" and criteria[number]["is_critical"]:
                reasons.append({"name": criteria[number]["name"],
                                "comment": note[2] if len(note) > 2 else "",
                                "quote": note[3] if len(note) > 3 else ""})
        critical_cases.append({
            "ref": talk["ref"], "day": talk["day"], "time": talk["time"], "kind": talk["kind"],
            "id": talk["id"], "family": talk["family"], "operator": talk["operator"],
            "direction": talk["direction"], "ai_score": talk["ai_score"],
            "human_score": talk["human_score"], "reviewed": talk["reviewed"],
            "reasons": reasons,
        })

    return {"totals": _totals(talks), "days": days, "sections": sections,
            "criteria": criteria_rows, "people": people, "critical_cases": critical_cases}


def public_summary(summary: dict, *, people_limit=PEOPLE_LIMIT, criteria_limit=60,
                   cases_limit=120) -> dict:
    """Цифры для экрана: без внутренних номеров ссылок, с потолками списков."""
    def person(p):
        out = dict(p)
        out.pop("critical_refs", None)
        return out

    def criterion(c):
        out = dict(c)
        out.pop("examples", None)
        return out

    return {
        "totals": summary["totals"], "days": summary["days"], "sections": summary["sections"],
        "criteria": [criterion(c) for c in summary["criteria"] if c["bad"]][:criteria_limit],
        "people": [person(p) for p in summary["people"][:people_limit]],
        "critical_cases": [{k: v for k, v in case.items() if k != "ref"}
                           for case in summary["critical_cases"][:cases_limit]],
    }


# ── контекст для модели ───────────────────────────────────────────────────────

def _half_up(value) -> int:
    import math
    return int(math.floor(float(value) + 0.5))


def _score(value) -> str:
    return "—" if value is None else str(_half_up(value))


def _pct(share) -> str:
    return "—" if share is None else f"{_half_up(100 * float(share))}%"


def _ru_date(iso: str) -> str:
    parts = str(iso or "").split("-")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else str(iso or "")


def _safe(text) -> str:
    return str(text or "").replace("===", "= = =")


def totals_line(totals: dict, previous: dict | None = None) -> str:
    prev = previous or {}

    def with_prev(value, key, fmt=_score):
        text = fmt(value)
        if prev.get(key) is not None:
            text += f" (прошлый период {fmt(prev[key])})"
        return text

    parts = [
        f"оценено {totals['evaluated']} (звонков {totals['calls']}, чатов {totals['chats']})"
        f" у {totals['operators']} сотрудников за {totals['days']} дней с оценками",
        f"средний балл ИИ {with_prev(totals.get('ai_avg'), 'ai_avg')}",
        f"самый низкий {_score(totals.get('ai_min'))}, ниже 60 баллов — {totals['below_60']}",
        f"критических {totals['critical']} ({_pct(totals.get('critical_share'))} разговоров"
        + (f"; прошлый период {_pct(prev['critical_share'])}" if prev.get("critical_share") is not None else "")
        + ")",
        f"проверено людьми {totals['reviewed']} из {totals['evaluated']}, ИИ поправили в {totals['corrected']}",
    ]
    if totals.get("human_avg") is not None:
        parts.append(f"средний балл человека {_score(totals['human_avg'])}")
    return "; ".join(parts) + "."


def _days_table(days) -> str:
    rows = ["ПО ДНЯМ (день · оценено · средний балл ИИ · критических · ниже 60 · проверено людьми"
            " · балл человека · сотрудников):"]
    for day in days:
        if not day["evaluated"]:
            rows.append(f"  {_ru_date(day['day'])} · оценок нет")
            continue
        rows.append(f"  {_ru_date(day['day'])} · {day['evaluated']} · {_score(day['ai_avg'])} · "
                    f"{day['critical']} · {day['below_60']} · {day['reviewed']} · "
                    f"{_score(day['human_avg'])} · {day['operators']}")
    return "\n".join(rows)


def _sections_table(sections, previous_sections=None) -> str:
    prev = {s["key"]: s for s in previous_sections or []}
    rows = ["ПО НАПРАВЛЕНИЯМ (шкала оценки):"]
    for section in sections:
        p = prev.get(section["key"]) or {}
        row = (f"  «{section['direction']}» ({'переписка' if section['family'] == digest_data.FAMILY_CHATS else 'звонки'}):"
               f" оценено {section['evaluated']}, сотрудников {section['operators']},"
               f" средний балл ИИ {_score(section['ai_avg'])}")
        if p.get("ai_avg") is not None:
            row += f" (прошлый период {_score(p['ai_avg'])})"
        row += (f", критических {section['critical']} ({_pct(section.get('critical_share'))})"
                f", ниже 60 — {section['below_60']}, проверено людьми {section['reviewed']}")
        if section.get("human_avg") is not None:
            row += f", балл человека {_score(section['human_avg'])}"
        rows.append(row + ".")
    return "\n".join(rows)


def _criteria_table(criteria, previous_criteria=None, *, per_direction=CRITERIA_PER_DIRECTION) -> str:
    prev = {(c["scale"], c["name"]): c for c in previous_criteria or []}
    rows = ["КРИТЕРИИ С ОШИБКАМИ (Неверно + Недочёт из применимых разговоров, доля; прошлый период"
            " — доля тогда; примеры — ссылки):"]
    seen = Counter()
    for entry in criteria:
        if not entry["bad"] or seen[entry["scale"]] >= per_direction:
            continue
        seen[entry["scale"]] += 1
        row = (f"  «{entry['direction']}» · {entry['name']}"
               f"{' (критический)' if entry['is_critical'] else ''}: Неверно {entry['fail']},"
               f" Недочёт {entry['deficiency']} из {entry['applicable']} ({_pct(entry['bad_share'])})")
        p = prev.get((entry["scale"], entry["name"]))
        if p and p.get("bad_share") is not None:
            row += f", прошлый период {_pct(p['bad_share'])}"
        if entry.get("people"):
            row += " — чаще всего: " + ", ".join(f"{x['name']} {x['n']}" for x in entry["people"])
        if entry.get("examples"):
            row += " — примеры " + ", ".join(f"#{r}" for r in entry["examples"])
        rows.append(row)
    return "\n".join(rows)


def _people_table(people, *, limit=PEOPLE_LIMIT) -> str:
    rows = ["СОТРУДНИКИ (разговоров · дней · средний балл ИИ · самый низкий · критических · Неверно"
            " · Недочёт · проверено людьми · балл человека · на чём ошибается чаще всего):"]
    for person in people[:limit]:
        worst = ", ".join(f"{w['name']} {w['n']}" for w in person.get("worst") or []) or "ошибок нет"
        row = (f"  {person['name']} («{person['direction']}»): {person['talks']} · {person['days']} ·"
               f" {_score(person['ai_avg'])} · {_score(person['ai_min'])} · {person['critical']} ·"
               f" {person['fails']} · {person['deficiencies']} · {person['reviewed']} ·"
               f" {_score(person['human_avg'])} · {worst}")
        if person.get("critical_refs"):
            row += " — критические: " + ", ".join(f"#{r}" for r in person["critical_refs"])
        rows.append(row)
    if len(people) > limit:
        rows.append(f"  … и ещё {len(people) - limit} сотрудников с меньшим числом ошибок")
    return "\n".join(rows)


def _talk_head(talk) -> str:
    kind = "чат" if talk["family"] == digest_data.FAMILY_CHATS else "звонок"
    head = (f"#{talk['ref']} · {_ru_date(talk['day'])} {talk['time']} · {kind} · {_safe(talk['operator'])}"
            f" («{_safe(talk['direction'])}») · балл ИИ {_score(talk.get('ai_score'))}")
    if talk.get("human_score") is not None:
        head += f", балл человека {_score(talk['human_score'])}"
    elif talk.get("reviewed"):
        head += ", проверено человеком"
    if talk.get("asr_low"):
        head += " · звук распознан плохо"
    return head


def _note_lines(talk, criteria, *, only_critical=False, limit=None) -> list[str]:
    lines = []
    for note in talk["notes"]:
        number, mark = note[0], note[1] if len(note) > 1 else "x"
        meta = criteria[number]
        if only_critical and not (mark == "x" and meta["is_critical"]):
            continue
        text = f"    {'✗' if mark == 'x' else '~'} {_safe(meta['name'])}"
        if meta["is_critical"]:
            text += " [критический]"
        comment = _safe(note[2] if len(note) > 2 else "")
        quote = _safe(note[3] if len(note) > 3 else "")
        if comment:
            text += f": {comment}"
        if quote:
            text += f" | цитата: «{quote}»"
        lines.append(text)
        if limit and len(lines) >= limit:
            break
    return lines


def _critical_block(talks, criteria, *, budget=CRITICAL_BUDGET, limit=CRITICAL_CASES_LIMIT) -> str:
    cases = [t for t in talks if t["critical"]]
    if not cases:
        return "КРИТИЧЕСКИЕ СЛУЧАИ: не было."
    rows = [f"КРИТИЧЕСКИЕ СЛУЧАИ ({len(cases)}; каждый — отдельный разговор, ссылка обязательна):"]
    used = 0
    for talk in cases[:limit]:
        block = "\n".join([f"  {_talk_head(talk)}", *_note_lines(talk, criteria, only_critical=True)])
        if used + len(block) > budget:
            rows.append(f"  … остальные {len(cases) - (len(rows) - 1)} не поместились — см. таблицу сотрудников")
            break
        rows.append(block)
        used += len(block)
    return "\n".join(rows)


def _notes_block(talks, criteria, *, budget=NOTES_BUDGET, exclude=()) -> str:
    """Замечания оценщика по разговорам с ошибками — самые проблемные первыми,
    пока хватает бюджета. Критические (они выше) не повторяются."""
    skip = set(exclude)
    pool = [t for t in talks if t["notes"] and not t["critical"] and t["ref"] not in skip]
    pool.sort(key=lambda t: (-(len(t["fails"]) * 2 + len(t["deficiencies"])),
                             t["ai_score"] if t.get("ai_score") is not None else 101))
    if not pool:
        return ""
    rows = ["ЗАМЕЧАНИЯ ОЦЕНЩИКА (разговоры с ошибками, самые проблемные первыми; у разговора"
            " перечислены только критерии «Неверно» (✗) и «Недочёт» (~)):"]
    used = 0
    shown = 0
    for talk in pool:
        block = "\n".join([f"  {_talk_head(talk)}", *_note_lines(talk, criteria, limit=6)])
        if used + len(block) > budget:
            break
        rows.append(block)
        used += len(block)
        shown += 1
    if shown < len(pool):
        rows.append(f"  … ещё {len(pool) - shown} разговоров с ошибками не поместились: их ошибки"
                    " учтены в таблицах выше")
    return "\n".join(rows)


def _digests_block(day_digests: list[dict], *, budget=DIGESTS_BUDGET,
                   text_limit=DIGEST_TEXT_LIMIT) -> str:
    """Сводки дней, которые ИИ уже написал: {day, title, text} в порядке дней."""
    if not day_digests:
        return ""
    rows = ["СВОДКИ ДНЕЙ (написаны ИИ по разговорам каждого дня; цитаты в них — слова людей; ссылок"
            " на разговоры в них нет):"]
    used = 0
    for item in day_digests:
        text = digest_data.clip(_safe(item.get("text") or ""), text_limit)
        block = f"  {_ru_date(item['day'])} — {item.get('title') or 'сводка'}:\n    " + text.replace("\n", "\n    ")
        if used + len(block) > budget:
            rows.append("  … более ранние сводки дней не поместились")
            break
        rows.append(block)
        used += len(block)
    return "\n".join(rows)


def context_text(*, department_name: str, first: date, last: date, scope_text: str,
                 summary: dict, previous: dict | None, talks, criteria,
                 day_digests: list[dict]) -> str:
    """Весь период одним текстом — системная часть чата (одинакова от вопроса к
    вопросу, Vertex снимает её в кеш)."""
    length = (last - first).days + 1
    parts = [f"ПЕРИОД: {first.strftime('%d.%m.%Y')} — {last.strftime('%d.%m.%Y')} ({length} дн.)"
             f" · ОТДЕЛ: {department_name} · {scope_text}",
             "ИТОГО ЗА ПЕРИОД: " + totals_line(summary["totals"], (previous or {}).get("totals")),
             _days_table(summary["days"]),
             _sections_table(summary["sections"], (previous or {}).get("sections")),
             _criteria_table(summary["criteria"], (previous or {}).get("criteria")),
             _people_table(summary["people"]),
             _critical_block(talks, criteria)]
    notes = _notes_block(talks, criteria)
    if notes:
        parts.append(notes)
    digests = _digests_block(day_digests)
    if digests:
        parts.append(digests)
    return "\n\n".join(parts)


# ── хвост к вопросу: названные люди, критерии, дни ────────────────────────────

_MENTION_CACHE: dict = {}


def _mention_re(word: str):
    """Слово имени в вопросе в любом падеже и регистре: основа (render._surname_stem)
    плюс падежное окончание до конца слова. В отличие от кнопок сводки регистр не
    важен — в вопросе фамилию пишут и со строчной."""
    stem = render._surname_stem(str(word or "").lower().replace("ё", "е"))
    if len(stem) < 3:
        return None
    if stem not in _MENTION_CACHE:
        _MENTION_CACHE[stem] = re.compile(
            rf"(?<!\w){re.escape(stem)}(?:{render._CASE_ENDINGS})?(?!\w)", re.I)
    return _MENTION_CACHE[stem]


def mentioned_people(question: str, people) -> list[dict]:
    """Сотрудники, названные в вопросе (фамилия или имя в любом падеже)."""
    text = question.replace("ё", "е")
    found = []
    for person in people:
        parts = [p for p in str(person.get("name") or "").split() if len(p) >= 4]
        for part in parts:
            regex = _mention_re(part)
            if regex is not None and regex.search(text):
                found.append(person)
                break
    return found


_WORD_RE = re.compile(r"[^\W\d_]+")


def _criterion_mentioned(question: str, name: str) -> bool:
    """Критерий назван в вопросе: длинные слова названия — по основе («приветствие»
    ↔ «по приветствию»), хотя бы половина из них; короткие («Имя») — слово целиком."""
    words = _WORD_RE.findall(str(name or "").lower().replace("ё", "е"))
    long_words = [w for w in words if len(w) >= 5]
    if long_words:
        hits = sum(1 for w in long_words if (w[:-2] if len(w) >= 6 else w[:-1]) in question)
        return hits >= max(1, (len(long_words) + 1) // 2)
    return bool(words) and all(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", question) for w in words)


def mentioned_criteria(question: str, criteria_rows) -> list[dict]:
    lowered = question.lower().replace("ё", "е")
    found, seen = [], set()
    for entry in criteria_rows:
        name = str(entry.get("name") or "")
        if name in seen or not _criterion_mentioned(lowered, name):
            continue
        seen.add(name)
        found.append(entry)
    return found


_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})[.\s]+(\d{1,2}|января|февраля|марта|апреля|мая|июня|июля|августа"
                     r"|сентября|октября|ноября|декабря)(?!\d)", re.I)


def mentioned_days(question: str, first: date, last: date) -> list[str]:
    out = []
    for match in _DAY_RE.finditer(question):
        day = int(match.group(1))
        month_raw = match.group(2).lower()
        month = int(month_raw) if month_raw.isdigit() else MONTHS_GENITIVE.index(month_raw) + 1
        for year in {first.year, last.year}:
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if first <= candidate <= last:
                out.append(candidate.isoformat())
    return sorted(set(out))


def focus_text(question: str, summary: dict, talks, criteria, first: date, last: date,
               *, budget=FOCUS_BUDGET) -> str:
    """Полный разрез по тому, что названо в вопросе, — хвостом к вопросу."""
    parts = []
    used = 0
    people = mentioned_people(question, summary["people"])
    for person in people[:3]:
        own = [t for t in talks if t["operator"] == person["name"] and (t["notes"] or t["critical"])]
        rows = [f"РАЗГОВОРЫ {person['name'].upper()} С ЗАМЕЧАНИЯМИ ({len(own)} из {person['talks']}):"]
        for talk in own:
            block = "\n".join([f"  {_talk_head(talk)}", *_note_lines(talk, criteria, limit=6)])
            if used + len(block) > budget:
                rows.append("  … остальные не поместились")
                break
            rows.append(block)
            used += len(block)
        parts.append("\n".join(rows))
    for entry in mentioned_criteria(question, summary["criteria"])[:2]:
        by_person = Counter()
        examples = []
        for talk in talks:
            hits = [n for n in talk["notes"] if criteria[n[0]]["name"] == entry["name"]]
            if hits:
                by_person[talk["operator"]] += 1
                examples.append((talk, hits))
        rows = [f"КРИТЕРИЙ «{entry['name']}» ПО ЛЮДЯМ: "
                + ", ".join(f"{name} {n}" for name, n in by_person.most_common(20))]
        for talk, hits in examples[:25]:
            note = hits[0]
            line = f"  {_talk_head(talk)}: {_safe(note[2] if len(note) > 2 else '')}"
            if len(note) > 3 and note[3]:
                line += f" | цитата: «{_safe(note[3])}»"
            if used + len(line) > budget:
                break
            rows.append(line)
            used += len(line)
        parts.append("\n".join(rows))
    for day_iso in mentioned_days(question, first, last)[:2]:
        own = [t for t in talks if t["day"] == day_iso and (t["notes"] or t["critical"])]
        if not own:
            continue
        rows = [f"РАЗГОВОРЫ {_ru_date(day_iso)} С ЗАМЕЧАНИЯМИ ({len(own)}):"]
        for talk in own:
            block = "\n".join([f"  {_talk_head(talk)}", *_note_lines(talk, criteria, limit=5)])
            if used + len(block) > budget:
                rows.append("  … остальные не поместились")
                break
            rows.append(block)
            used += len(block)
        parts.append("\n".join(rows))
    return "\n\n".join(parts)


# ── ссылки ────────────────────────────────────────────────────────────────────

def ref_entry(talk) -> dict:
    """Кнопка разговора в ответе за период — с датой: «Иванова А. · 03.10 14:09»."""
    return {"kind": talk["kind"], "id": talk["id"], "family": talk["family"],
            "operator": talk.get("operator") or "—", "time": talk.get("time") or "",
            "date": _ru_date(talk["day"]), "ai_score": talk.get("ai_score"),
            "direction": talk.get("direction") or ""}


# ── сводки дней периода ───────────────────────────────────────────────────────

def day_digests_for(cur, department, first, last, allowed_direction_ids=None) -> list[dict]:
    """Тексты сводок дней периода, видимые зрителю: «Главное» (не для СВ) и
    разделы его направлений — как текст, без кнопок разговоров."""
    from . import service
    try:
        cur.execute(
            """SELECT digest_day, overview_html, sections
                 FROM ai_qa_day_digests
                WHERE department_code = %s AND digest_day BETWEEN %s AND %s AND status = 'ready'
                ORDER BY digest_day""",
            (department, first, last))
    except Exception as error:
        if store._schema_missing(error):
            return []
        raise
    restricted = allowed_direction_ids is not None
    out = []
    for day, overview_html, sections in cur.fetchall():
        iso = day.isoformat()
        if overview_html and not restricted:
            out.append({"day": iso, "title": "«Главное» по отделу", "text": render.to_text(overview_html)})
        for section in sections or []:
            if not isinstance(section, dict) or not section.get("html"):
                continue
            if not service._visible(section, allowed_direction_ids):
                continue
            out.append({"day": iso, "title": f"«{section.get('direction') or 'направление'}»",
                        "text": render.to_text(section["html"])})
    return out


# ── чтение периода ────────────────────────────────────────────────────────────

def _department_name(cur, department: str) -> str:
    from . import service
    return service._department_name(cur, department)


def scope_text(sections, restricted: bool) -> str:
    names = ", ".join(f"«{s['direction']}»" for s in sections) or "направлений с оценками нет"
    return ("направления зрителя: " if restricted else "направления: ") + names


def build(department: str, first: date, last: date, allowed_direction_ids=None, *,
          inline_limit=day_facts.INLINE_LIMIT, with_previous=True) -> dict:
    """Период целиком: слепки → разговоры в скоупе → цифры; прошлый период — для
    сравнения (его слепки только читаются или ставятся в фон, запрос не ждут)."""
    department = config.normalise_department_code(department)
    current = day_facts.ensure(department, first, last, inline_limit=inline_limit)
    talks, criteria, directions = talks_of(current["days"], allowed_direction_ids)
    summary = summarize(talks, criteria, directions, first=first, last=last)
    previous = None
    previous_pending = False
    ptalks, pcriteria, pdirections = [], [], {}
    if with_previous:
        pfirst, plast = previous_period(first, last)
        past = day_facts.ensure(department, pfirst, plast,
                                inline_limit=None if inline_limit is None else 0)
        previous_pending = bool(past["pending"])
        if past["days"]:
            ptalks, pcriteria, pdirections = talks_of(past["days"], allowed_direction_ids)
            previous = summarize(ptalks, pcriteria, pdirections, first=pfirst, last=plast)
            previous["from"], previous["to"] = pfirst.isoformat(), plast.isoformat()
    return {
        "department": department, "first": first, "last": last,
        "talks": talks, "criteria": criteria, "directions": directions,
        "summary": summary, "previous": previous, "previous_pending": previous_pending,
        # Разговоры прошлого периода — месячной сводке: сравнение по разделам.
        "previous_talks": ptalks, "previous_criteria": pcriteria, "previous_directions": pdirections,
        "pending": current["pending"], "signatures": current["signatures"],
        "inputs_hash": day_facts.combined_hash(current["signatures"]),
        "evaluated_total": current["count"],
    }


def read(department: str, first: date, last: date, allowed_direction_ids=None) -> dict:
    """Период для экрана: цифры, ряд по дням, разрезы, прошлый период."""
    built = build(department, first, last, allowed_direction_ids)
    restricted = allowed_direction_ids is not None
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        digests = day_digests_for(cur, built["department"], first, last, allowed_direction_ids)
        cur.close()
    finally:
        conn.close()
    previous = built["previous"]
    return {
        "from": first.isoformat(), "to": last.isoformat(), "title": period_title(first, last),
        "department": built["department"], "whole_month": is_whole_month(first, last),
        "days_total": (last - first).days + 1,
        "pending": built["pending"], "preparing": bool(built["pending"]),
        "queued": day_facts.queued(built["department"], first, last),
        "evaluated": built["summary"]["totals"]["evaluated"],
        "summary": public_summary(built["summary"]),
        "previous": ({"from": previous["from"], "to": previous["to"], "totals": previous["totals"],
                      "sections": [{"key": s["key"], "ai_avg": s["ai_avg"],
                                    "critical_share": s["critical_share"]} for s in previous["sections"]]}
                     if previous else None),
        "previous_pending": built["previous_pending"],
        "digest_days": len({d["day"] for d in digests}),
        "scope": scope_text(built["summary"]["sections"], restricted),
        "inputs_hash": built["inputs_hash"],
    }


def status(department: str, first: date, last: date) -> dict:
    """Лёгкое состояние для опроса: сколько дней ещё считается, без чтения слепков."""
    department = config.normalise_department_code(department)
    return {"from": first.isoformat(), "to": last.isoformat(),
            "queued": day_facts.queued(department, first, last)}


# ── чат ───────────────────────────────────────────────────────────────────────

_ASKING: set[tuple] = set()
_ASKING_LOCK = threading.Lock()

# Контекст периода в памяти: отпечаток периода в ключе, так что новые оценки и
# проверки людей меняют ключ сами. Вопросы идут сериями, и собирать месяц
# заново на каждый незачем.
CONTEXT_TTL_S = 600
_CONTEXT_MAX = 8
_CONTEXT: dict = {}
_CONTEXT_LOCK = threading.Lock()


def _period_context(department, first, last, allowed_direction_ids):
    scope = None if allowed_direction_ids is None else tuple(sorted(int(x) for x in allowed_direction_ids))
    built = build(department, first, last, allowed_direction_ids, inline_limit=0)
    if built["pending"]:
        raise PeriodError("Данные за период ещё готовятся — подождите несколько секунд и спросите снова")
    if not built["talks"]:
        raise PeriodError("За этот период нет оценённых разговоров — спрашивать не о чем")
    key = (built["department"], first.isoformat(), last.isoformat(), scope, built["inputs_hash"])
    now = time.monotonic()
    with _CONTEXT_LOCK:
        hit = _CONTEXT.get(key)
        if hit and hit[0] > now:
            return hit[1]
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        department_name = _department_name(cur, built["department"])
        digests = day_digests_for(cur, built["department"], first, last, allowed_direction_ids)
        cur.close()
    finally:
        conn.close()
    restricted = allowed_direction_ids is not None
    text = context_text(department_name=department_name, first=first, last=last,
                        scope_text=scope_text(built["summary"]["sections"], restricted),
                        summary=built["summary"], previous=built["previous"],
                        talks=built["talks"], criteria=built["criteria"], day_digests=digests)
    value = {"text": text, "built": built, "department_name": department_name,
             "refs": {t["ref"]: ref_entry(t) for t in built["talks"]},
             "numbers": {f"{t['kind']}:{t['id']}": t["ref"] for t in built["talks"]}}
    with _CONTEXT_LOCK:
        _CONTEXT[key] = (now + CONTEXT_TTL_S, value)
        while len(_CONTEXT) > _CONTEXT_MAX:
            _CONTEXT.pop(min(_CONTEXT, key=lambda k: _CONTEXT[k][0]))
    return value


def ask(department: str, first: date, last: date, question: str, *, user_id: int,
        allowed_direction_ids=None, generate_fn=None) -> dict:
    """Вопрос о периоде → ответ модели (готовый HTML) и обе реплики в истории."""
    from . import prompts, service
    department = config.normalise_department_code(department)
    question = str(question or "").strip()
    if not question:
        raise PeriodError("Вопрос пустой")
    if len(question) > QUESTION_LIMIT:
        raise PeriodError(f"Вопрос длиннее {QUESTION_LIMIT} знаков — сократите его")
    key = (department, first.isoformat(), last.isoformat(), int(user_id))
    with _ASKING_LOCK:
        if key in _ASKING:
            raise PeriodError("Предыдущий вопрос ещё обрабатывается — дождитесь ответа")
        _ASKING.add(key)
    try:
        context = _period_context(department, first, last, allowed_direction_ids)
        built = context["built"]
        history = store.period_thread(department, first, last, user_id, limit=store.HISTORY_FOR_MODEL)
        system = prompts.period_chat_system(department, context["department_name"],
                                            period_title(first, last), context["text"])
        focus = focus_text(question, built["summary"], built["talks"], built["criteria"], first, last)
        user = question if not focus else f"{question}\n\n{prompts.FOCUS_OPEN}\n{focus}\n{prompts.FOCUS_CLOSE}"
        turns = [{"role": m["role"],
                  "text": m["body"] if m["role"] == "user" else render.to_text(m["body"], context["numbers"])}
                 for m in history]
        started = time.monotonic()
        text, meta = (generate_fn or service._generate_article)(
            system, user, history=turns, max_tokens=prompts.CHAT_MAX_TOKENS)
        html, _used = render.render(text, context["refs"])
        if not html:
            raise PeriodError("ИИ не ответил — попробуйте спросить иначе")
        elapsed = round(time.monotonic() - started, 1)
        asked, answered = store.period_append_pair(
            department, first, last, user_id, question, html,
            model=f"{meta.get('provider')}:{meta.get('model')}", usage=meta.get("usage"),
            elapsed_s=elapsed)
        return {"question": asked, "answer": answered, "elapsed_s": elapsed}
    finally:
        with _ASKING_LOCK:
            _ASKING.discard(key)


def thread(department: str, first: date, last: date, user_id: int) -> list[dict]:
    return store.period_thread(config.normalise_department_code(department), first, last, user_id)


def clear(department: str, first: date, last: date, user_id: int) -> int:
    return store.period_clear_thread(config.normalise_department_code(department), first, last, user_id)
