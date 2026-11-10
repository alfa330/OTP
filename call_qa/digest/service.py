# -*- coding: utf-8 -*-
"""Сводка дня: генерация, чтение с учётом прав и чат.

ГРАНИЦЫ ПРАВ. Сводку пишут по отделу целиком, а читают её и глава, и СВ,
которому открыты только его направления. Поэтому текст разложен по разделам —
раздел на шкалу, — и зрителю со скоупом отдаются только те разделы, все
разговоры которых лежат в его направлениях. «Главное» отдела собрано из всех
разделов сразу и такому зрителю не показывается вовсе: пересказ чужих
направлений — та же утечка, что и их строки. Чат читает день заново и тем же
скоупом, что списки раздела, так что чужого он не видит в принципе.

ДЕНЬГИ. Генерация отдела — один вызов модели на раздел плюс один на «Главное»:
у ОП это пять вызовов по 30–40 тыс. токенов (≈ $0,02 каждый на
gemini-3-flash-preview). Поэтому повторная генерация с тем же набором оценок —
отказ «сводка и так свежая», а не новый счёт, а при новых оценках
переписываются только разделы, чьи оценки изменились (см. generate).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from datetime import date

from .. import config
from .. import fraud as fraud_rules
from .. import remote_cc
from . import data as digest_data
from . import prompts
from . import render
from . import store

# Пока идёт вопрос человека, второй его вопрос того же дня ждать нечего: ответы
# легли бы в историю вперемешку. Замок процесса — достаточно: инстанс один.
_ASKING: set[tuple] = set()
_ASKING_LOCK = threading.Lock()

QUESTION_LIMIT = 1000


class DigestError(RuntimeError):
    """Отказ по существу — текст для человека, а не трейсбек."""


def _generate_article(system, user, **kwargs):
    from wiki.ai import providers
    return providers.generate_article(system, user, **kwargs)


def _department_name(cur, department: str) -> str:
    cur.execute("SELECT name FROM departments WHERE lower(code) = %s LIMIT 1",
                (str(department or "").lower(),))
    row = cur.fetchone()
    return (row[0] if row and row[0] else str(department or "").upper())


def _sections_of(talks) -> list[dict]:
    """Разговоры дня → разделы по шкале. Порядок: звонки, потом переписка, внутри
    — по названию: одинаковый от дня ко дню, чтобы глаз находил своё место."""
    groups: dict[str, list] = {}
    for talk in talks:
        groups.setdefault(digest_data.section_key(talk), []).append(talk)
    sections = []
    for key, group in groups.items():
        families = Counter(t["family"] for t in group)
        family = families.most_common(1)[0][0]
        sections.append({
            "key": key, "direction_id": group[0].get("scale_direction"),
            "direction": group[0].get("direction") or "Без направления",
            "family": family, "talks": group,
            # Канонические направления СОТРУДНИКОВ раздела — по ним раздел
            # сверяется со скоупом зрителя (у «ТП чата» Тез КЦ шкала своя, а
            # люди — «ТП линии»).
            "subject_directions": sorted({t["subject_direction"] for t in group
                                          if t.get("subject_direction") is not None}),
        })
    sections.sort(key=lambda s: (s["family"] != digest_data.FAMILY_CALLS, s["direction"].lower()))
    return sections


def _usage_add(total: dict, usage: dict | None) -> None:
    for key in ("calls", "prompt_tokens", "completion_tokens", "thoughts_tokens", "cached_tokens"):
        value = (usage or {}).get(key)
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + int(value)


# «Обычно» — средние прошлой недели: за день они не меняются, а читаются тяжело
# (вся проекция оценок и разбор критериев сотен карточек). Экран сводки читает
# день на каждое открытие, поэтому неделя держится в памяти процесса.
BASELINE_CACHE_TTL_S = 1800
_BASELINE_CACHE: dict = {}
_BASELINE_CACHE_LOCK = threading.Lock()


def _baseline(cur, day, department) -> dict:
    """digest_data.baseline всего отдела — из памяти, если читали недавно."""
    key = (department, day.isoformat())
    now = time.monotonic()
    with _BASELINE_CACHE_LOCK:
        hit = _BASELINE_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = digest_data.baseline(cur, day, department, None)
    with _BASELINE_CACHE_LOCK:
        _BASELINE_CACHE[key] = (now + BASELINE_CACHE_TTL_S, value)
        for stale in [k for k, (until, _v) in _BASELINE_CACHE.items() if until <= now]:
            _BASELINE_CACHE.pop(stale, None)
    return value


def _read_day(day, department):
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        talks = digest_data.collect_day(cur, day, department, None)
        base = _baseline(cur, day, department) if talks else {}
        name = _department_name(cur, department)
        digest = store.get(cur, department, day)
        fraud_day = _fraud_day(cur, day, department) if talks else None
        cur.close()
    finally:
        conn.close()
    return talks, base, name, digest, fraud_day


# ── «Поиск фрода» (SZOV-2) ────────────────────────────────────────────────────

def _fraud_department(cur) -> str | None:
    """Отдел, в чьей сводке живёт «Поиск фрода», — отдел направления, через которое
    оцениваются звонки удалённого КЦ («Обзвон — подписание АВР» — СЗоВ)."""
    direction_id = config.REMOTE_CC_QA_DIRECTION_ID
    if not direction_id:
        return None
    cur.execute("""SELECT lower(COALESCE(dep.code, ''))
                     FROM directions d JOIN departments dep ON dep.id = d.department_id
                    WHERE d.id = %s""", (int(direction_id),))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _fraud_day(cur, day, department) -> dict | None:
    """Совпадения шаблонов фрода за день и какие из звонков ИИ слушал.

    None — у отдела «Поиска фрода» нет или удалённый КЦ в этот день не звонил.
    subjects — {попытка журнала удалённого КЦ: звонок пула ИИ-оценки}: по нему
    совпадение получает номер разговора [[#n]] (если звонок оценён)."""
    if _fraud_department(cur) != config.normalise_department_code(department):
        return None
    calls = fraud_rules.load_day(cur, day)
    if not calls:
        return None
    keys = sorted({c["general_call_id"] for c in calls if c["general_call_id"]})
    subjects = {}
    if keys:
        cur.execute("""SELECT external_id, id FROM imported_calls
                        WHERE notes = %s AND external_id = ANY(%s)""", (remote_cc.NOTES, keys))
        by_key = {str(key): int(subject_id) for key, subject_id in cur.fetchall()}
        subjects = {c["id"]: by_key[c["general_call_id"]] for c in calls
                    if c["general_call_id"] in by_key}
    return {"calls": {c["id"]: c for c in calls}, "found": fraud_rules.find(calls),
            "subjects": subjects}


def _fraud_hash(found: dict, heard) -> str:
    """Отпечаток раздела: совпадения (какие звонки под какими шаблонами) и оценки
    прослушанных звонков. Изменилось одно из двух — раздел переписывается."""
    import hashlib
    keys = [f"{kind}:{','.join(hit['calls'])}" for kind in fraud_rules.KINDS
            for hit in found.get(kind) or []]
    keys.append(digest_data.inputs_hash(heard))
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:32]


def _fraud_section(day, department, department_name, fraud_day, talks, refs, kept, *,
                   force, generate_fn, usage, models, errors) -> tuple[dict, bool]:
    """Раздел «Поиск фрода»: (запись раздела, звали ли модель успешно).

    Ни один шаблон не сработал — модель не зовём: одна строка без счёта за неё."""
    found = fraud_day["found"]
    direction_id = int(config.REMOTE_CC_QA_DIRECTION_ID)
    ref_of_subject = {t["id"]: t["ref"] for t in talks
                      if t["kind"] == config.SUBJECT_IMPORTED_CALL}
    ref_by_call = {call_id: ref_of_subject[subject_id]
                   for call_id, subject_id in fraud_day["subjects"].items()
                   if subject_id in ref_of_subject}
    talk_by_ref = {t["ref"]: t for t in talks}
    flagged = [call_id for call_id, _kinds in fraud_rules.flagged_calls(found)]
    heard = [talk_by_ref[ref] for ref in dict.fromkeys(
        ref_by_call[call_id] for call_id in flagged if call_id in ref_by_call)]
    stats = fraud_rules.public_stats(found)
    entry = {"key": prompts.FRAUD_KEY, "kind": prompts.FRAUD_KIND, "direction_id": direction_id,
             "direction": prompts.FRAUD_TITLE, "family": digest_data.FAMILY_CALLS,
             # Видят те, кому открыто направление звонков удалённого КЦ (_visible).
             "subject_directions": [direction_id], "stats": stats, "usual": None,
             "inputs_hash": _fraud_hash(found, heard), "inputs_count": len(heard),
             "headline": "", "html": "", "refs": [], "error": None}
    if kept and kept.get("html") and kept.get("inputs_hash") == entry["inputs_hash"] and not force:
        entry.update(headline=kept.get("headline") or "", html=kept["html"],
                     refs=kept.get("refs") or [])
        return entry, False
    if not stats["flagged"]:
        entry.update(headline="Шаблоны фрода за день не сработали",
                     html="<p>Ни один из четырёх шаблонов за день не сработал.</p>")
        return entry, False
    # Ссылки — только на звонки удалённого КЦ: номер чужого разговора открыл бы
    # СВ сотрудника другого направления (как у обычных разделов).
    own_refs = {t["ref"]: refs[t["ref"]] for t in talks
                if t.get("subject_direction") == direction_id and t["ref"] in refs}
    try:
        text, meta = generate_fn(
            prompts.fraud_system(department),
            prompts.fraud_user(day, department_name, found, fraud_day["calls"], ref_by_call,
                               heard, stats),
            max_tokens=prompts.FRAUD_MAX_TOKENS)
        _usage_add(usage, {**(meta.get("usage") or {}), "calls": 1})
        models[f"{meta.get('provider')}:{meta.get('model')}"] += 1
        headline, body = render.parse_envelope(text)
        html, used = render.render(body, own_refs)
        html = render.drop_fraud_header_stats(html, stats)
        if not html:
            raise DigestError("ИИ вернул пустой ответ")
        entry.update(headline=_fraud_headline(headline or render.headline_from(html), found),
                     html=html, refs=sorted(set(used)))
        return entry, True
    except Exception as error:   # noqa: BLE001 — раздел не роняет сводку
        logging.exception("ai-qa сводка %s %s: «Поиск фрода» не написан", department, day)
        if kept and kept.get("html"):
            entry.update(headline=kept.get("headline") or "", html=kept["html"],
                         refs=kept.get("refs") or [], inputs_hash=kept.get("inputs_hash"),
                         inputs_count=kept.get("inputs_count"), outdated=True)
            errors.append(f"{prompts.FRAUD_TITLE}: {_cause(error)} (оставлен прежний текст)")
        else:
            entry["error"] = _section_error(error)
            errors.append(f"{prompts.FRAUD_TITLE}: {_cause(error)}")
        return entry, False


def _fraud_headline(headline: str, found: dict) -> str:
    """«КРАТКО» раздела — без фамилий: промпт это запрещает, но модель называла
    оператора в заголовке («…у Тулегенулы Максата», живой прогон 21.10). Заголовок
    раздела встаёт подписью дня в списке, где фамилия обвиняемым тоном — лишнее.
    Названа фамилия кого-то из операторов с совпадениями — заголовок считает код."""
    operators = {hit["operator"] for kind in fraud_rules.KINDS for hit in found.get(kind) or []
                 if hit.get("operator") and hit["operator"] != "—"}
    lowered = str(headline or "").casefold()
    for name in operators:
        for word in str(name).split():
            stem = word.casefold()[:max(4, len(word) - 2)]
            if len(word) >= 4 and stem in lowered:
                count = len(operators)
                return (f"Шаблоны фрода сработали у {count} "
                        f"{'оператора' if count % 10 == 1 and count % 100 != 11 else 'операторов'}")
    return headline


def _place_fraud(sections_out, entry) -> None:
    """«Поиск фрода» — сразу за разделом направления звонков удалённого КЦ."""
    for position, section in enumerate(sections_out):
        if section.get("direction_id") == entry["direction_id"]:
            sections_out.insert(position + 1, entry)
            return
    sections_out.append(entry)


def _cause(error) -> str:
    """Причина сбоя словами для экрана. Сырой текст — ответ провайдера с JSON
    попыток («HTTP 429: RESOURCE_EXHAUSTED…») или исключение базы — уходит только
    в лог: плашку видят все зрители дня, и понять из него им нечего."""
    if isinstance(error, DigestError):
        return str(error)
    text = str(error or "").lower()
    if any(mark in text for mark in ("429", "resource_exhausted", "quota", "rate limit",
                                     "overloaded", "too many requests")):
        return "ИИ был перегружен"
    if type(error).__name__ == "ProviderError":
        return "ИИ не ответил"
    if type(error).__module__.startswith("psycopg2"):
        return "Сбой базы данных"
    return "Сбой при составлении"


def _section_error(error) -> str:
    return f"{_cause(error)} — раздел допишется при следующем обновлении сводки."


def incomplete(digest) -> bool:
    """Сводка дописана не до конца: раздел упал или пуст, у раздела остался
    прежний текст при новых оценках (outdated), «Главного» нет при двух и более
    написанных разделах или оно прежнее (stats.overview_outdated). Такая сводка
    свежей не считается: её допишут следующий проход выборки или «Обновить»."""
    digest = digest or {}
    sections = [s for s in digest.get("sections") or [] if isinstance(s, dict)]
    if any(s.get("error") or not s.get("html") or s.get("outdated") for s in sections):
        return True
    if (digest.get("stats") or {}).get("overview_outdated"):
        return True
    written = sum(1 for s in sections if s.get("html"))
    return written > 1 and not digest.get("overview_html")


def generate(department: str, day: date, *, triggered_by: str = "manual", force: bool = False,
             generate_fn=None) -> dict:
    """Написать сводку дня отдела или дописать её.

    Возвращает {'status': 'ready' | 'fresh' | 'running' | 'empty' | 'failed', …}.
    Модель зовётся без открытого соединения с базой (см. store).

    ЧТО ПЕРЕПИСЫВАЕТСЯ. Раздел — только если изменился набор его оценок, он упал
    в прошлый раз или его не было; остальные разделы берутся из сохранённой
    сводки как есть. «Главное» — если переписан хоть один раздел или его нет.
    Так второй проход выборки, добравший три звонка одного направления, стоит
    один вызов модели плюс «Главное», а не шесть, и прочитанные утром тексты
    других направлений без новых данных не меняются. force переписывает всё."""
    department = config.normalise_department_code(department)
    generate_fn = generate_fn or _generate_article
    if not store.claim(department, day, triggered_by):
        return {"status": "running"}
    started = time.monotonic()
    try:
        talks, base, department_name, digest, fraud_day = _read_day(day, department)
        if not talks:
            store.release(department, day, forget_pending=True)
            return {"status": "empty"}
        # Отпечаток дня — по ВСЕМ оценкам, и взятым по триггеру тоже: оценили новый
        # подозрительный звонок — «Поиск фрода» пора дописать. Разделы направлений и
        # цифры — только по случайной выборке (regular_talks).
        regular = digest_data.regular_talks(talks)
        fingerprint = digest_data.inputs_hash(talks)
        if (not force and digest and digest.get("status") == "ready"
                and digest.get("inputs_hash") == fingerprint and not incomplete(digest)):
            store.release(department, day)
            return {"status": "fresh"}
        stored = {s.get("key"): s for s in (digest or {}).get("sections") or []
                  if isinstance(s, dict) and s.get("html") and not s.get("error")}
        refs = {t["ref"]: render.ref_entry(t) for t in talks}
        usage: dict = {}
        models = Counter()
        sections_out, errors = [], []
        stats_by_key = {}
        # rewritten — сколько разделов РЕАЛЬНО переписано (по нему решается,
        # переписывать ли «Главное»), attempted — сколько раз звали модель.
        rewritten = attempted = 0
        for section in _sections_of(regular):
            stats = digest_data.section_stats(section["talks"])
            stats_by_key[section["key"]] = stats
            section_hash = digest_data.inputs_hash(section["talks"])
            entry = {"key": section["key"], "direction_id": section["direction_id"],
                     "direction": section["direction"], "family": section["family"],
                     "subject_directions": section["subject_directions"],
                     "stats": digest_data.public_stats(stats),
                     "usual": _usual(base.get(section["key"])),
                     # По ним раздел сверяется с живым днём: «устарела» у СВ —
                     # только про его разделы, и дописывается только изменённое.
                     "inputs_hash": section_hash, "inputs_count": len(section["talks"]),
                     "headline": "", "html": "", "refs": [], "error": None}
            kept = stored.get(section["key"])
            same_data = bool(kept) and kept.get("inputs_hash") == section_hash
            if same_data and not force:
                entry.update(headline=kept.get("headline") or "", html=kept["html"],
                             refs=kept.get("refs") or [])
                sections_out.append(entry)
                continue
            attempted += 1
            # Только свои разговоры: номер из чужого раздела — это сотрудник чужого
            # направления, и СВ, которому виден этот раздел, увидел бы его ФИО и
            # балл. Ошибся модель номером — метка просто исчезает.
            section_refs = {t["ref"]: refs[t["ref"]] for t in section["talks"]}
            try:
                text, meta = generate_fn(
                    prompts.section_system(department, section["direction"]),
                    prompts.section_user(day, department_name, section["direction"],
                                         section["talks"], stats, base.get(section["key"])),
                    max_tokens=prompts.SECTION_MAX_TOKENS)
                # Ответ оплачен, даже если ниже окажется непригодным.
                _usage_add(usage, {**(meta.get("usage") or {}), "calls": 1})
                models[f"{meta.get('provider')}:{meta.get('model')}"] += 1
                headline, body = render.parse_envelope(text)
                html, used = render.render(body, section_refs)
                html = render.drop_header_stats(html, stats, entry["usual"])
                if not html:
                    raise DigestError("ИИ вернул пустой ответ")
                entry.update(headline=headline or render.headline_from(html), html=html,
                             refs=sorted(set(used)))
                rewritten += 1
            except Exception as error:   # noqa: BLE001 — раздел не роняет соседей
                logging.exception("ai-qa сводка %s %s: раздел «%s» не написан",
                                  department, day, section["direction"])
                if kept:
                    # Прежний текст честнее, чем дыра на его месте. При тех же
                    # данных он и так верен; при новых — помечен устаревшим и
                    # хранит ПРЕЖНИЙ отпечаток: следующий проход перепишет раздел,
                    # а сводка до тех пор не считается дописанной.
                    entry.update(headline=kept.get("headline") or "", html=kept["html"],
                                 refs=kept.get("refs") or [])
                    if not same_data:
                        entry.update(inputs_hash=kept.get("inputs_hash"),
                                     inputs_count=kept.get("inputs_count"), outdated=True)
                    errors.append(f"{section['direction']}: {_cause(error)} (оставлен прежний текст)")
                else:
                    entry["error"] = _section_error(error)
                    errors.append(f"{section['direction']}: {_cause(error)}")
            sections_out.append(entry)

        if fraud_day:
            kept = next((s for s in (digest or {}).get("sections") or []
                         if isinstance(s, dict) and s.get("key") == prompts.FRAUD_KEY), None)
            fraud_entry, fraud_written = _fraud_section(
                day, department, department_name, fraud_day, talks, refs, kept, force=force,
                generate_fn=generate_fn, usage=usage, models=models, errors=errors)
            _place_fraud(sections_out, fraud_entry)
            if fraud_entry["html"] and fraud_entry["html"] != (kept or {}).get("html"):
                rewritten += 1
            attempted += 1 if fraud_written or fraud_entry.get("error") or \
                fraud_entry.get("outdated") else 0

        written = [s for s in sections_out if s["html"]]
        if not written:
            store.release(department, day,
                          error=f"{_cause_of(errors)} — попробуйте ещё раз через несколько минут.")
            return {"status": "failed", "errors": errors}

        whole = digest_data.section_stats(regular)
        old_overview = (digest or {}).get("overview_html")
        old_outdated = bool(((digest or {}).get("stats") or {}).get("overview_outdated"))
        overview_html, headline, overview_outdated = None, written[0]["headline"], False
        if len(written) > 1:
            same_sections = {s["key"] for s in written} == set(stored)
            if not force and not rewritten and same_sections and old_overview and not old_outdated:
                overview_html = old_overview
                headline = digest.get("headline") or headline
            else:
                numbers = {f"{t['kind']}:{t['id']}": t["ref"] for t in talks}
                brief = [{"key": s["key"], "direction": s["direction"], "kind": s.get("kind"),
                          "text": render.to_text(s["html"], numbers)} for s in written]
                try:
                    text, meta = generate_fn(
                        prompts.overview_system(department, department_name),
                        prompts.overview_user(day, department_name, brief, stats_by_key,
                                              whole=whole, usual=_usual_department(base, regular)),
                        max_tokens=prompts.OVERVIEW_MAX_TOKENS)
                    _usage_add(usage, {**(meta.get("usage") or {}), "calls": 1})
                    models[f"{meta.get('provider')}:{meta.get('model')}"] += 1
                    overview_headline, body = render.parse_envelope(text)
                    overview_html, _used = render.render(body, refs)
                    overview_html = render.drop_header_stats(
                        overview_html, whole, _usual_department(base, regular)) or None
                    if not overview_html:
                        raise DigestError("ИИ вернул пустой ответ")
                    headline = overview_headline or render.headline_from(overview_html) or headline
                except Exception as error:   # noqa: BLE001 — без «Главного» сводка всё равно есть
                    logging.exception("ai-qa сводка %s %s: «Главное» не написано", department, day)
                    overview_html = None
                    if old_overview:
                        # Прежнее «Главное» лучше пустого места — с пометкой, что
                        # оно прежнее: сводка не будет считаться дописанной.
                        overview_html, overview_outdated = old_overview, True
                        headline = digest.get("headline") or headline
                    errors.append(f"Главное: {_cause(error)}")
        elif len(sections_out) > 1 and old_overview:
            # Разделов несколько, а написан один — остальные упали. Прежнее
            # «Главное» с прежним заголовком отдела не стираем.
            overview_html, overview_outdated = old_overview, True
            headline = digest.get("headline") or headline

        save_stats = {"department": digest_data.public_stats(whole),
                      "usual": _usual_department(base, regular)}
        if overview_outdated:
            save_stats["overview_outdated"] = True
        # Расход — накопительно за день: дописывание зовёт модель на часть
        # разделов, и счёт только последнего прохода занижал бы стоимость сводки.
        total_usage: dict = {}
        _usage_add(total_usage, (digest or {}).get("usage"))
        _usage_add(total_usage, usage)
        elapsed = round(time.monotonic() - started, 1)
        try:
            store.save(department, day, headline=headline, overview_html=overview_html,
                       sections=sections_out, stats=save_stats, inputs_hash=fingerprint,
                       inputs_count=len(talks),
                       model=(models.most_common(1)[0][0] if models else (digest or {}).get("model")),
                       usage=total_usage, elapsed_s=elapsed,
                       error="; ".join(errors)[:1000] if errors else None)
        except Exception as error:
            logging.exception("ai-qa сводка %s %s: не удалось сохранить", department, day)
            raise DigestError("Не удалось сохранить сводку") from error
        logging.info("ai-qa сводка %s %s (%s): разделов %s (переписано %s из %s), разговоров %s, "
                     "%s с, токены %s, сбои %s", department, day, triggered_by, len(written),
                     rewritten, attempted, len(talks), elapsed, usage, errors or "нет")
        return {"status": "ready", "sections": len(written), "rewritten": rewritten,
                "attempted": attempted, "errors": errors, "elapsed_s": elapsed}
    except Exception as error:
        logging.exception("ai-qa сводка %s %s: генерация упала", department, day)
        try:
            store.release(department, day,
                          error=f"{_cause(error)} — попробуйте ещё раз через несколько минут.")
        except Exception:
            logging.exception("ai-qa сводка %s %s: не удалось отпустить генерацию", department, day)
        return {"status": "failed", "errors": [_cause(error)]}


def _cause_of(errors) -> str:
    """Общая причина, когда не написан ни один раздел: одна на всех — она и
    есть; разные — без перечня (названия направлений на экран сбоя не нужны)."""
    causes = {str(e).split(": ", 1)[-1] for e in errors or []}
    return causes.pop() if len(causes) == 1 else "ИИ не смог написать сводку"


def _usual(base: dict | None) -> dict | None:
    if not base:
        return None
    return {"ai_avg": base.get("ai_avg"), "critical_share": base.get("critical_share"),
            "n": base.get("n")}


def _usual_department(base: dict, talks) -> dict | None:
    """Обычный средний балл отдела — взвешенно по разделам прошлой недели."""
    keys = {digest_data.section_key(t) for t in talks}
    pairs = [(b["ai_avg"], b["n"]) for k, b in (base or {}).items()
             if k in keys and b.get("ai_avg") is not None]
    if not pairs:
        return None
    total = sum(n for _avg, n in pairs)
    return {"ai_avg": round(sum(avg * n for avg, n in pairs) / total, 1), "n": total}


# ── чтение ────────────────────────────────────────────────────────────────────

def _visible(section: dict, allowed_direction_ids) -> bool:
    """Раздел виден зрителю, если ВСЕ его сотрудники — в направлениях скоупа."""
    if allowed_direction_ids is None:
        return True
    allowed = {int(x) for x in allowed_direction_ids}
    own = {int(x) for x in section.get("subject_directions") or []}
    return bool(own) and own <= allowed


def read(department: str, day: date, allowed_direction_ids=None) -> dict:
    """Сводка дня для зрителя: видимые разделы, живые цифры и признак «устарела».

    Цифры над разделами — живые, а не со дня генерации: «проверено 4 из 30»
    меняется весь день, пока СВ работают с очередью, а «оценено» — когда второй
    ночной проход добирает выборку. Ради этого день читается заново — без
    транскриптов, это сотня строк критериев.

    «Устарела» у зрителя со скоупом — только про ЕГО разделы: новые оценки
    чужого направления ему не сигнал (и не повод узнавать, сколько их)."""
    department = config.normalise_department_code(department)
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        everything = digest_data.collect_day(cur, day, department, None, with_transcripts=False)
        digest = store.get(cur, department, day)
        base = _baseline(cur, day, department) if everything else {}
        cur.close()
    finally:
        conn.close()
    everything = everything or []
    restricted = allowed_direction_ids is not None
    allowed = {int(x) for x in (allowed_direction_ids or [])}
    mine_all = [t for t in everything
                if not restricted or (t.get("subject_direction") is not None
                                      and int(t["subject_direction"]) in allowed)]
    # Разделы направлений и цифры — по случайной выборке, как у генерации: звонки,
    # взятые по триггеру фрода, разбирает «Поиск фрода» (SZOV-2).
    mine = digest_data.regular_talks(mine_all)
    live_sections = {s["key"]: s for s in _sections_of(mine)}
    # Раздел целиком, со всеми его людьми, — тот, что увидит генерация. «Устарела»
    # сверяется с ним: после перевода сотрудника в чужое направление разговоры
    # раздела те же, и generate ответит «свежая», — вечной кнопки «обновить»,
    # которая ничего не делает, у СВ быть не должно.
    full_sections = ({s["key"]: s for s in _sections_of(digest_data.regular_talks(everything))}
                     if restricted else live_sections)
    stored = [s for s in (digest or {}).get("sections") or [] if isinstance(s, dict)]
    sections = []
    stale_mine, new_mine, broken, hidden_own = False, 0, False, 0
    for entry in stored:
        if entry.get("kind") == prompts.FRAUD_KIND:
            # «Поиск фрода»: своих оценок у раздела нет, цифры — совпадения шаблонов
            # по журналу удалённого КЦ за прошедший день, они уже не меняются. Свежесть
            # сводки с ним держит отпечаток дня (по всем оценкам, и по триггерным).
            if not _visible(entry, allowed_direction_ids):
                continue
            if entry.get("error") or not entry.get("html") or entry.get("outdated"):
                broken = True
            sections.append({"key": entry.get("key"), "kind": entry.get("kind"),
                             "direction": entry.get("direction"), "family": entry.get("family"),
                             "headline": entry.get("headline") or "",
                             "html": entry.get("html") or "", "error": entry.get("error"),
                             "stats": entry.get("stats"), "usual": None})
            continue
        # Живой раздел снимается и тогда, когда сохранённый скрыт: иначе его
        # «свои» разговоры всплыли бы ниже фантомом «этого направления ещё нет».
        live = live_sections.pop(entry.get("key"), None)
        if not _visible(entry, allowed_direction_ids):
            hidden_own += len(live["talks"]) if live else 0
            continue
        live_talks = live["talks"] if live else []
        full = full_sections.get(entry.get("key"))
        if (entry.get("inputs_hash")
                and entry["inputs_hash"] != digest_data.inputs_hash(full["talks"] if full else [])):
            stale_mine = True
        if entry.get("inputs_count") is not None:
            new_mine += max(0, len(live_talks) - int(entry["inputs_count"]))
        if entry.get("error") or not entry.get("html") or entry.get("outdated"):
            broken = True
        sections.append({
            "key": entry.get("key"), "direction": entry.get("direction"),
            "family": entry.get("family"), "headline": entry.get("headline") or "",
            "html": entry.get("html") or "", "error": entry.get("error"),
            "stats": digest_data.public_stats(digest_data.section_stats(live_talks))
            if live else entry.get("stats"),
            "usual": _usual(base.get(entry.get("key"))) or entry.get("usual"),
        })
    # Раздел, которого в сводке ещё нет (оценки направления появились после её
    # генерации), — цифрами, без текста: так видно, что сводку пора обновить.
    for key, live in live_sections.items():
        sections.append({"key": key, "direction": live["direction"], "family": live["family"],
                         "headline": "", "html": "", "error": None, "missing": True,
                         "stats": digest_data.public_stats(digest_data.section_stats(live["talks"])),
                         "usual": _usual(base.get(key))})
        stale_mine = True
        new_mine += len(live["talks"])
    if restricted:
        stale, new_count = bool(digest) and stale_mine, new_mine if digest else 0
    else:
        stale = bool(digest and digest.get("inputs_hash")
                     and digest["inputs_hash"] != digest_data.inputs_hash(everything))
        new_count = 0
        if digest and digest.get("inputs_count") is not None:
            new_count = max(0, len(everything) - int(digest.get("inputs_count") or 0))
        broken = bool(digest) and incomplete(digest)
    whole = digest_data.public_stats(digest_data.section_stats(mine)) if mine else None
    return {
        "day": day.isoformat(), "department": department,
        "status": (digest or {}).get("status") or ("none" if everything else "empty"),
        "running": bool((digest or {}).get("running")),
        "headline": _headline_for(digest, sections, restricted),
        # «Главное» собрано из ВСЕХ разделов отдела — зрителю со скоупом его не
        # отдаём (см. шапку модуля).
        "overview_html": None if restricted else (digest or {}).get("overview_html"),
        "sections": sections,
        "stats": whole,
        # «Обычно» — живое, как и цифры: по прошлой неделе тех разделов, что видит
        # зритель (у СВ — только его направлений).
        "usual": _usual_department(base, mine) if mine else None,
        # Сколько разговоров читает сводка — со взятыми по триггеру: их читает «Поиск
        # фрода». Цифры в stats — без них.
        "evaluated": len(mine_all),
        "stale": stale, "new_evaluations": new_count,
        # Раздел упал или «Главное» не написано — допишет «Обновить» (generate
        # переписывает только недостающее).
        "incomplete": bool(digest) and (digest or {}).get("status") == "ready" and broken,
        # Свои разговоры зрителя, попавшие только в общие с чужими направлениями
        # разделы (их ему не показывают): сводка есть, но не для него — экран
        # говорит об этом, а не «сводки ещё нет» с бесполезной кнопкой.
        "hidden_own": hidden_own if restricted else 0,
        "generated_at": (digest or {}).get("generated_at"),
        "model": (digest or {}).get("model"),
        "error": (digest or {}).get("last_error") if (digest or {}).get("status") != "ready" else None,
    }


def status(department: str, day: date) -> dict:
    """Состояние сводки без чтения дня — для опроса, пока она пишется: экран
    спрашивает раз в несколько секунд, и полное чтение дня с неделей «обычно»
    на каждый такой вопрос нагружало бы базу впустую."""
    department = config.normalise_department_code(department)
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        digest = store.get(cur, department, day)
        cur.close()
    finally:
        conn.close()
    return {"day": day.isoformat(), "department": department,
            "status": (digest or {}).get("status") or "none",
            "running": bool((digest or {}).get("running")),
            "generated_at": (digest or {}).get("generated_at")}


def _headline_for(digest, sections, restricted: bool) -> str:
    if not digest:
        return ""
    if not restricted:
        return digest.get("headline") or ""
    for section in sections:
        if section.get("headline"):
            return section["headline"]
    return ""


def headlines(department: str, days, allowed_direction_ids=None) -> dict:
    """{день: {headline, status, running}} — подписи строк списка дней."""
    department = config.normalise_department_code(department)
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        found = store.for_days(cur, department, days)
        cur.close()
    finally:
        conn.close()
    restricted = allowed_direction_ids is not None
    allowed = {int(x) for x in (allowed_direction_ids or [])}
    out = {}
    for day, info in found.items():
        headline = info.get("headline") or ""
        hidden = False
        if restricted:
            sections = info.get("sections") or []
            visible = [s for s in sections if _visible(s, allowed_direction_ids)]
            headline = next((s.get("headline") for s in visible if s.get("headline")), "")
            # Сводка есть, но все разделы с людьми зрителя — общие с чужими
            # направлениями: строка дня скажет это, а не «не составлена».
            hidden = not visible and any(
                allowed & {int(x) for x in s.get("subject_directions") or []} for s in sections)
        out[day] = {"headline": headline, "status": info.get("status"),
                    "running": info.get("running"), "hidden": hidden}
    return out


# ── чат ───────────────────────────────────────────────────────────────────────

def thread(department: str, day: date, user_id: int) -> list[dict]:
    department = config.normalise_department_code(department)
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        return store.thread(cur, department, day, user_id)
    finally:
        conn.close()


def clear(department: str, day: date, user_id: int) -> int:
    return store.clear_thread(config.normalise_department_code(department), day, user_id)


def _scope_text(department_name: str, sections) -> str:
    names = ", ".join(f"«{s['direction']}»" for s in sections) or "нет разделов"
    return f"отдел «{department_name}», направления {names}"


def ask(department: str, day: date, question: str, *, user_id: int,
        allowed_direction_ids=None, generate_fn=None) -> dict:
    """Вопрос о дне → ответ модели (готовый HTML) и обе реплики в истории."""
    department = config.normalise_department_code(department)
    question = str(question or "").strip()
    if not question:
        raise DigestError("Вопрос пустой")
    if len(question) > QUESTION_LIMIT:
        raise DigestError(f"Вопрос длиннее {QUESTION_LIMIT} знаков — сократите его")
    key = (department, day.isoformat(), int(user_id))
    with _ASKING_LOCK:
        if key in _ASKING:
            raise DigestError("Предыдущий вопрос ещё обрабатывается — дождитесь ответа")
        _ASKING.add(key)
    try:
        return _ask(department, day, question, user_id=user_id,
                    allowed_direction_ids=allowed_direction_ids,
                    generate_fn=generate_fn or _generate_article)
    finally:
        with _ASKING_LOCK:
            _ASKING.discard(key)


# День отдела для чата держится в памяти несколько минут. Чтение дня — это сотня
# карточек с транскриптами (на сервере секунды, с машины разработчика — 17 с), а
# вопросы о дне идут сериями: человек спрашивает, дочитывает, уточняет. Проверки
# людей за эти минуты могут добавиться — чат увидит их со следующего чтения;
# сводка и история при этом читаются каждый раз заново, они дешёвые.
DAY_CACHE_TTL_S = 300
_DAY_CACHE_MAX = 6
_DAY_CACHE: dict = {}
_DAY_CACHE_LOCK = threading.Lock()


def _chat_day(cur, day, department, allowed_direction_ids):
    scope = None if allowed_direction_ids is None else tuple(sorted(int(x) for x in allowed_direction_ids))
    key = (department, day.isoformat(), scope)
    now = time.monotonic()
    with _DAY_CACHE_LOCK:
        hit = _DAY_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    talks = digest_data.collect_day(cur, day, department, allowed_direction_ids)
    # «Обычно» — то же, что на экране и в тексте разделов: неделя шкалы по
    # отделу (_baseline). Своё, скоупное, у чата расходилось бы с шапкой над
    # сводкой на общих шкалах (у Тез КЦ «ТП чат» оценивает людей двух
    # направлений); новых данных зрителю это не открывает — те же числа уже на
    # его экране.
    value = (talks, _baseline(cur, day, department) if talks else {},
             _department_name(cur, department))
    with _DAY_CACHE_LOCK:
        _DAY_CACHE[key] = (now + DAY_CACHE_TTL_S, value)
        while len(_DAY_CACHE) > _DAY_CACHE_MAX:
            _DAY_CACHE.pop(min(_DAY_CACHE, key=lambda k: _DAY_CACHE[k][0]))
    return value


def _ask(department, day, question, *, user_id, allowed_direction_ids, generate_fn) -> dict:
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        talks, base, department_name = _chat_day(cur, day, department, allowed_direction_ids)
        if not talks:
            raise DigestError("За этот день нет оценённых разговоров — спрашивать не о чем")
        digest = store.get(cur, department, day)
        history = store.thread(cur, department, day, user_id, limit=store.HISTORY_FOR_MODEL)
        cur.close()
    finally:
        conn.close()

    numbers = {f"{t['kind']}:{t['id']}": t["ref"] for t in talks}
    refs = {t["ref"]: render.ref_entry(t) for t in talks}
    restricted = allowed_direction_ids is not None
    # Цифры — по случайной выборке, как на экране; сами звонки под триггерами чат
    # видит (с пометкой в шапке разговора) — о них и спрашивают.
    sections = _sections_of(digest_data.regular_talks(talks)) or _sections_of(talks)
    stats_by_key = {s["key"]: digest_data.section_stats(s["talks"]) for s in sections}
    stored = [s for s in ((digest or {}).get("sections") or [])
              if isinstance(s, dict) and _visible(s, allowed_direction_ids) and s.get("html")]
    summaries = [{"direction": s.get("direction"), "text": render.to_text(s["html"], numbers)}
                 for s in stored]
    overview = None if restricted else render.to_text((digest or {}).get("overview_html") or "",
                                                       numbers)
    context = prompts.chat_context(day, department_name, summaries, overview, talks,
                                   stats_by_key, base)
    system = prompts.chat_system(department, _scope_text(department_name, sections), context)
    turns = [{"role": m["role"],
              "text": m["body"] if m["role"] == "user" else render.to_text(m["body"], numbers)}
             for m in history]
    started = time.monotonic()
    text, meta = generate_fn(system, question, history=turns, max_tokens=prompts.CHAT_MAX_TOKENS)
    html, _used = render.render(text, refs)
    if not html:
        raise DigestError("ИИ не ответил — попробуйте спросить иначе")
    elapsed = round(time.monotonic() - started, 1)
    asked, answered = store.append_pair(
        department, day, user_id, question, html,
        model=f"{meta.get('provider')}:{meta.get('model')}", usage=meta.get("usage"),
        elapsed_s=elapsed)
    return {"question": asked, "answer": answered, "elapsed_s": elapsed}
