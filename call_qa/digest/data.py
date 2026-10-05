# -*- coding: utf-8 -*-
"""Разговоры дня и точные цифры для сводки.

ОТКУДА РАЗГОВОРЫ. Из проекции ai_review_cache — той же, что читают «Звонки» и
«Чаты»: последняя оценка каждого субъекта, день — день разговора по Алматы
(api._SUBJECT_DAY), отдел и скоуп зрителя — тем же предикатом, что у списков
(api._direction_predicate). Возьми сводка разговоры иначе, чем список под ней, —
и «критических 3» в сводке не сошлось бы с тремя красными строками экрана дня.

ПОЧЕМУ ДВА ЗАПРОСА, А НЕ ОДИН. Карточка разговора весит 15–60 КБ (транскрипт
звонка — это токены распознавания с таймингами), а день отдела — до 120
разговоров. Первый запрос выбирает сами разговоры дня без тяжёлых полей, второй
пачками дочитывает из карточки только нужное: критерии, балл, транскрипт.
Одним запросом с полной карточкой проба 02.10.2026 упёрлась в statement_timeout
(30 с) на 270 разговорах; пачками по 25 тот же объём читается за 6 с.

ПОЧЕМУ ЦИФРЫ СЧИТАЕТ КОД. Модель уверенно ошибается в счёте («у 7 из 30»
вместо 5), а такие числа руководитель переносит в отчёт. Поэтому счёт —
здесь, а модель получает готовые цифры и только объясняет их.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from .. import config

FAMILY_CALLS = "calls"
FAMILY_CHATS = "chats"

# Вердикты, которые для сводки значат «здесь проблема». Error — так GLM изредка
# пишет критическую ошибку вне схемы (см. evaluator._collect_verdicts): в старых
# сохранённых карточках он ещё встречается и значит то же, что Incorrect.
FAIL = ("Incorrect", "Error")
DEFICIENCY = "Deficiency"
# Неприменимо и «проверяется по данным в ПО» — не успех и не провал: из
# знаменателя доли такие критерии выпадают.
NOT_COUNTED = ("N/A", "Pending")

# Сколько разговоров дочитывать из карточки за раз (см. «два запроса» в шапке).
_DETAIL_CHUNK = 25
# С чем сравнивать день: средние того же направления за предыдущую неделю.
BASELINE_DAYS = 7
# Сколько ссылок на разговоры отдавать модели в одной строке цифр: дальше
# перечень перестаёт читаться и только раздувает контекст.
_REFS_IN_STAT = 12

_TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*$")
_ROLE_PREFIX_RE = re.compile(r"^\s*(Клиент|Оператор|Водитель|Сотрудник)(\s*\([^)]*\))?\s*:\s*")


def _num(value):
    try:
        if value is None or str(value).strip() in ("", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def family_of(kind: str) -> str:
    return FAMILY_CHATS if kind in config.CHAT_SUBJECT_KINDS else FAMILY_CALLS


def _verdict(value) -> str | None:
    text = str(value or "").strip()
    return text or None


# ── транскрипт в текст ────────────────────────────────────────────────────────

def _clock(ms) -> str:
    try:
        seconds = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def transcript_text(lines, kind: str) -> str:
    """Транскрипт карточки → компактный текст по репликам.

    У переписки подписи сторон точные — «О» сотрудник, «К» клиент: у сообщения
    есть направление. У звонка подпись в сохранённых репликах — лишь догадка до
    оценки (api._lines_from_tokens; окончательные стороны ставит карточка по
    цитатам оценки, speaker_roles). Прежняя догадка «кто больше говорит»
    переворачивала роли как раз там, где клиент говорит дольше сотрудника, — в
    жалобах, где и звучит брань (скан 1385 оценок: «мат оператора» в четырёх
    звонках на деле сказал звонящий). Поэтому голоса звонка подписаны нейтрально —
    «Г1», «Г2» в порядке, в каком зазвучали, как и у оценщика ([S1]/[S2]), — а кто
    из них сотрудник, модель решает по смыслу.
    Подряд идущие реплики одной стороны склеиваются — так транскрипт короче на
    треть, а смысл тот же."""
    chat = kind in config.CHAT_SUBJECT_KINDS
    voices: dict[str, str] = {}
    out: list[list[str]] = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        speaker = str(line.get("speaker") or "")
        if chat:
            label = "О" if speaker == "operator" else "К" if speaker == "client" else "?"
        else:
            label = voices.setdefault(speaker, f"Г{len(voices) + 1}")
        segs = line.get("seg") if isinstance(line.get("seg"), list) else []
        text = "".join(str(seg.get("t") or "") for seg in segs if isinstance(seg, dict))
        if chat:
            # У переписки в сегменте уже стоит подпись «Оператор (Имя): …» — своя
            # короче, а вложения (описание фото, расшифровка голосового) живут
            # именно в сегменте, поэтому берём его, а не голый body.
            text = _ROLE_PREFIX_RE.sub("", text) or str(line.get("body") or "")
        text = " ".join(text.split())
        if not text:
            continue
        stamp = ""
        if chat:
            match = _TIME_RE.search(str(line.get("ts") or ""))
            stamp = match.group(1) if match else ""
        else:
            stamp = _clock(line.get("start_ms"))
        if out and out[-1][0] == label and not chat:
            out[-1][2] += " " + text
            continue
        out.append([label, stamp, text])
    return "\n".join(f"[{stamp}] {label}: {text}" if stamp else f"{label}: {text}"
                     for label, stamp, text in out)


def clip(text: str, limit: int) -> str:
    """Середину длинного транскрипта — вон: начало (кто, зачем) и конец (чем
    закончилось) важнее для сводки, чем пятая минута объяснений."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n… [пропущено {len(text) - limit} знаков] …\n{text[-tail:]}"


# ── разговоры дня ─────────────────────────────────────────────────────────────

def _subjects_sql(api, scope_sql: str) -> str:
    """Разговоры одного дня: последняя оценка каждого субъекта (как у «Звонков» и
    «Чатов» — api.evaluations_list). Во внутреннем запросе — только столбцы, без
    выражений над payload: DISTINCT ON сортирует ВСЮ проекцию, и выражение над
    карточкой там распаковало бы каждую из тысяч карточек, а не только дневные."""
    return (f"""SELECT rc.subject_kind, rc.call_id, rc.model, {api._SUBJECT_OPERATOR},
                       {api._SUBJECT_OPERATOR_ID}, {api._SUBJECT_DATETIME},
                       COALESCE(d.canonical_id, d.id), {api._SUBJECT_HUMAN_SCORE},
                       m.review_outcome, rc.payload->>'_evaluation_run_id'
                  FROM (SELECT DISTINCT ON (rc.subject_kind, rc.call_id)
                               rc.subject_kind, rc.call_id, rc.model, rc.created_at, rc.payload
                          FROM ai_review_cache rc
                         ORDER BY rc.subject_kind, rc.call_id, rc.created_at DESC) rc"""
            + api._SUBJECT_JOIN + """
                  LEFT JOIN ai_evaluation_meta m
                         ON m.subject_kind = rc.subject_kind AND m.call_id = rc.call_id
                            AND m.model = rc.model
                 WHERE """ + f"{api._SUBJECT_DAY} = %s" + api._SUBJECT_EXISTS + scope_sql)


def day_subjects(cur, day, department, allowed_direction_ids=None) -> list[dict] | None:
    """Разговоры дня отдела в скоупе зрителя — без тяжёлых полей карточки.

    None — скоуп невыполним (зрителю нечего показывать); [] — разговоров нет."""
    from .. import api
    scope_sql, scope_params = api._direction_predicate(
        cur, allowed_direction_ids, department, api._SUBJECT_DIRECTION)
    if scope_sql is None:
        return None
    cur.execute(_subjects_sql(api, scope_sql), (day, *scope_params))
    talks = []
    for (kind, subject_id, model, operator, operator_id, label, subject_direction,
         human, outcome, run_id) in cur.fetchall():
        match = _TIME_RE.search(str(label or ""))
        talks.append({
            "kind": kind or config.SUBJECT_CALL, "id": int(subject_id), "model": model,
            "family": family_of(kind or config.SUBJECT_CALL),
            "operator": (operator or "—").strip() or "—",
            "operator_id": int(operator_id) if operator_id is not None else None,
            "time": match.group(1) if match else "",
            "subject_direction": int(subject_direction) if subject_direction is not None else None,
            "human_score": _num(human), "outcome": outcome, "run_id": run_id,
        })
    # Порядок — по времени разговора: так номера ссылок идут по ленте дня, и
    # «#3» в сводке раньше «#12» по часам.
    talks.sort(key=lambda t: (t["time"] or "99:99", t["kind"], t["id"]))
    return talks


def inputs_hash(talks) -> str:
    """Отпечаток набора оценок дня: появились новые или переоценили старые —
    сводка устарела. Проверки людей в отпечаток не входят: сводку пишут утром,
    чтобы показать, куда смотреть, и каждая проверка не должна звать её
    переписывать; исправления людей чат видит и так — он читает день заново."""
    keys = sorted(f"{t['kind']}:{t['id']}:{t.get('run_id') or t.get('model') or ''}"
                  for t in talks or [])
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:32]


_SLIM_CRITERIA = """(CASE WHEN jsonb_typeof(payload->'criteria') = 'array'
        THEN (SELECT jsonb_agg(jsonb_build_object('idx', c->'idx', 'name', c->'name',
                                                  'ai', c->'ai', 'is_critical', c->'is_critical',
                                                  'source', c->'source', 'conf', c->'conf'))
                FROM jsonb_array_elements(payload->'criteria') c) END)"""


def attach_details(cur, talks, *, with_transcripts=True) -> None:
    """Дочитать из карточки критерии, балл и транскрипт — пачками (см. шапку)."""
    by_key = defaultdict(list)
    for talk in talks:
        by_key[(talk["kind"], talk["model"])].append(talk)
    # Без транскриптов день читают ради цифр (экран сводки), и обоснования с
    # цитатами — 10–15 КБ на разговор — там не нужны: только имя, вердикт,
    # критичность, источник и уверенность. Это впятеро меньше по объёму.
    # Итог оценщика — тоже только для модели: в нём он пишет то, чего нет в
    # вердиктах («звонил пассажир, перенаправлен в поддержку Яндекс Go»).
    transcript_col = (", payload->'transcript', payload->>'overall_comment'" if with_transcripts
                      else ", NULL::jsonb, NULL::text")
    criteria_col = "payload->'criteria'" if with_transcripts else _SLIM_CRITERIA
    for (kind, model), group in by_key.items():
        index = {t["id"]: t for t in group}
        ids = sorted(index)
        for start in range(0, len(ids), _DETAIL_CHUNK):
            chunk = ids[start:start + _DETAIL_CHUNK]
            cur.execute(
                f"""SELECT call_id, {criteria_col}, payload->>'ai_score',
                          payload->'score_breakdown', payload->>'asr_mean_conf', payload->'media',
                          payload->>'direction_id', payload->>'call_end_party'"""
                + transcript_col + """
                     FROM ai_review_cache
                    WHERE subject_kind = %s AND model = %s AND call_id = ANY(%s)""",
                (kind, model, chunk))
            for (subject_id, criteria, ai_score, breakdown, asr, media, direction_id,
                 end_party, transcript, overall) in cur.fetchall():
                talk = index.get(int(subject_id))
                if talk is None:
                    continue
                talk["criteria"] = [c for c in (criteria or []) if isinstance(c, dict)]
                talk["ai_score"] = _num(ai_score)
                breakdown = breakdown if isinstance(breakdown, dict) else {}
                talk["unchecked_weight"] = int(_num(breakdown.get("unchecked_weight")) or 0)
                talk["asr_conf"] = _num(asr) if talk["family"] == FAMILY_CALLS else None
                talk["media_failed"] = int(_num((media or {}).get("failed")) or 0) \
                    if isinstance(media, dict) else 0
                talk["scale_direction"] = int(_num(direction_id)) if _num(direction_id) is not None \
                    else talk["subject_direction"]
                talk["end_party"] = end_party if end_party not in (None, "", "unknown") else None
                talk["transcript"] = transcript_text(transcript, kind) if with_transcripts else ""
                talk["overall_comment"] = " ".join(str(overall or "").split())[:400]
    for talk in talks:
        talk.setdefault("criteria", [])
        talk.setdefault("ai_score", None)
        talk.setdefault("unchecked_weight", 0)
        talk.setdefault("asr_conf", None)
        talk.setdefault("media_failed", 0)
        talk.setdefault("scale_direction", talk["subject_direction"])
        talk.setdefault("end_party", None)
        talk.setdefault("transcript", "")
        talk.setdefault("overall_comment", "")


# Строка журнала, которая считается оценкой человека у субъекта, — по тем же
# связям, что карточка (api._HUMAN_REVIEW_SQL), только пачкой на вид субъекта. У
# звонка журнала — хвост цепочки переоценок, у звонка из АТС — строка с его
# imported_call_id, у переписки — строка, привязанная к снапшоту.
_JOURNAL_BATCH_SQL = {
    config.SUBJECT_CALL: """
        WITH RECURSIVE chain AS (
            SELECT c0.id AS root, c0.id, 0 AS depth FROM calls c0 WHERE c0.id = ANY(%s)
            UNION ALL
            SELECT chain.root, n.id, chain.depth + 1
              FROM calls n JOIN chain ON n.previous_version_id = chain.id
             WHERE chain.depth < 32)
        SELECT DISTINCT ON (chain.root) chain.root, c.scores, c.score, c.comment, c.criterion_comments
          FROM chain JOIN calls c ON c.id = chain.id
         WHERE COALESCE(c.is_draft, FALSE) = FALSE
         ORDER BY chain.root, c.created_at DESC, c.id DESC""",
    config.SUBJECT_IMPORTED_CALL: """
        SELECT DISTINCT ON (c.imported_call_id) c.imported_call_id, c.scores, c.score, c.comment, c.criterion_comments
          FROM calls c
         WHERE c.imported_call_id = ANY(%s) AND COALESCE(c.is_draft, FALSE) = FALSE
         ORDER BY c.imported_call_id, c.created_at DESC""",
    config.SUBJECT_C2D_SNAPSHOT: """
        SELECT DISTINCT ON (c.c2d_snapshot_id) c.c2d_snapshot_id, c.scores, c.score, c.comment, c.criterion_comments
          FROM calls c
         WHERE c.c2d_snapshot_id = ANY(%s) AND COALESCE(c.is_draft, FALSE) = FALSE
         ORDER BY c.c2d_snapshot_id, c.created_at DESC""",
    config.SUBJECT_WZ_EPISODE: """
        SELECT DISTINCT ON (e.id) e.id, c.scores, c.score, c.comment, c.criterion_comments
          FROM wazzup_episodes e
          JOIN c2d_chat_snapshots s
            ON s.source = 'wazzup' AND s.wz_channel_id = e.channel_id
               AND s.wz_chat_id = e.chat_id AND s.episode_start = e.started_at
          JOIN calls c ON c.c2d_snapshot_id = s.id
         WHERE e.id = ANY(%s) AND COALESCE(c.is_draft, FALSE) = FALSE
         ORDER BY e.id, c.created_at DESC""",
    config.SUBJECT_CA_EPISODE: """
        SELECT DISTINCT ON (e.id) e.id, c.scores, c.score, c.comment, c.criterion_comments
          FROM chatapp_episodes e
          JOIN c2d_chat_snapshots s
            ON s.source = 'chatapp'
               AND s.wz_channel_id = e.license_id::text || ':' || e.messenger_type
               AND s.wz_chat_id = e.chat_id AND s.episode_start = e.started_at
          JOIN calls c ON c.c2d_snapshot_id = s.id
         WHERE e.id = ANY(%s) AND COALESCE(c.is_draft, FALSE) = FALSE
         ORDER BY e.id, c.created_at DESC""",
}


def _human_verdict(value) -> str | None:
    """Вердикт человека в словаре сводки: «Критич. ошибка» журнала — Error."""
    text = str(value or "").strip()
    if not text:
        return None
    from .. import api
    return api._human_display_verdict(text)


def _same_verdict(human, ai) -> bool:
    return human == ai or (human in FAIL and ai in FAIL)


def attach_human_reviews(cur, talks) -> None:
    """Где человек уже проверил разговор — его вердикты по критериям.

    Сводка обязана им верить больше, чем ИИ: иначе она пересказывала бы
    «критическую ошибку», которую супервайзер уже снял. Источник — тот же и в
    том же порядке, что у балла человека (api._SUBJECT_HUMAN_SCORE) и у карточки
    (api._attach_human_review): сперва строка журнала — она и есть качество
    сотрудника, её вердикты лежат по idx критерия; нет её — «Моя оценка» из
    карточки (ai_human_reviews), список 1:1 с критериями карточки. «Моей оценке»
    верим, только если она сделана по той же шкале и той же длины: после правки
    шкалы позиции съезжают, и вердикт человека лёг бы на чужой критерий."""
    by_kind = defaultdict(list)
    for talk in talks:
        by_kind[talk["kind"]].append(talk["id"])
    journal, mine = {}, {}
    for kind, ids in by_kind.items():
        ids = sorted(set(ids))
        sql = _JOURNAL_BATCH_SQL.get(kind)
        if sql:
            cur.execute(sql, (ids,))
            for subject_id, scores, score, comment, comments in cur.fetchall():
                journal[(kind, int(subject_id))] = (scores, score, comment, comments)
        cur.execute(
            """SELECT DISTINCT ON (call_id) call_id, scores, criterion_comments, score, comment,
                      direction_id
                 FROM ai_human_reviews
                WHERE subject_kind = %s AND call_id = ANY(%s)
                ORDER BY call_id, (score IS NULL), updated_at DESC""",
            (kind, ids))
        for subject_id, scores, comments, score, comment, direction_id in cur.fetchall():
            mine[(kind, int(subject_id))] = (scores, comments, score, comment, direction_id)
    for talk in talks:
        key = (talk["kind"], talk["id"])
        criteria = talk.get("criteria") or []
        verdicts: list = [None] * len(criteria)
        notes: list = [""] * len(criteria)
        score = comment = None
        source = None
        if key in journal:
            scores, score, comment, comments = journal[key]
            scores = scores if isinstance(scores, list) else []
            comments = comments if isinstance(comments, list) else []
            for position, criterion in enumerate(criteria):
                idx = criterion.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(scores):
                    verdicts[position] = _human_verdict(scores[idx])
                if isinstance(idx, int) and 0 <= idx < len(comments):
                    notes[position] = str(comments[idx] or "").strip()[:300]
            source = "journal"
        elif key in mine:
            scores, comments, score, comment, direction_id = mine[key]
            scores = scores if isinstance(scores, list) else []
            comments = comments if isinstance(comments, list) else []
            same_scale = (len(scores) == len(criteria) and
                          (direction_id is None or talk.get("scale_direction") is None
                           or int(direction_id) == int(talk["scale_direction"])))
            if same_scale:
                for position in range(len(criteria)):
                    verdicts[position] = _human_verdict(scores[position])
                    if position < len(comments):
                        notes[position] = str(comments[position] or "").strip()[:300]
            source = "review"
        if source is None:
            talk["human"] = None
            continue
        changes = []
        for position, criterion in enumerate(criteria):
            human, ai = verdicts[position], _verdict(criterion.get("ai"))
            if human is None or _same_verdict(human, ai):
                continue
            changes.append({"position": position,
                            "name": criterion.get("name") or f"критерий {position + 1}",
                            "ai": ai, "human": human, "note": notes[position]})
        # verdicts — вердикт человека по каждой позиции (None — не ставил):
        # по ним промпт отличает «человек подтвердил Верно» от «никто не смотрел».
        talk["human"] = {"score": _num(score), "comment": str(comment or "").strip()[:400],
                         "changes": changes, "source": source, "verdicts": verdicts}


def attach_direction_names(cur, talks) -> dict:
    """{направление шкалы: название} — подписи разделов сводки."""
    ids = sorted({t["scale_direction"] for t in talks if t.get("scale_direction") is not None})
    if not ids:
        return {}
    cur.execute("SELECT id, name FROM directions WHERE id = ANY(%s)", (ids,))
    names = {int(i): (n or f"Направление {i}") for i, n in cur.fetchall()}
    for talk in talks:
        talk["direction"] = names.get(talk.get("scale_direction"), "Без направления")
    return names


def collect_day(cur, day, department, allowed_direction_ids=None, *,
                with_transcripts=True) -> list[dict] | None:
    """Разговоры дня целиком: что выбрано, что сказал ИИ, что поправил человек."""
    talks = day_subjects(cur, day, department, allowed_direction_ids)
    if not talks:
        return talks
    attach_details(cur, talks, with_transcripts=with_transcripts)
    attach_human_reviews(cur, talks)
    attach_direction_names(cur, talks)
    for number, talk in enumerate(talks, start=1):
        talk["ref"] = number
    return talks


# ── что в разговоре не так ────────────────────────────────────────────────────

def effective_verdict(talk, criterion, position) -> str | None:
    """Вердикт, которому верит сводка: человека, если он проверил, иначе ИИ.
    Сверка по позиции критерия, а не по имени: имя в шкале может повториться."""
    human = talk.get("human") or {}
    for change in human.get("changes") or []:
        if change.get("position") == position:
            return change["human"]
    return _verdict(criterion.get("ai"))


def is_critical_failure(talk) -> bool:
    for position, criterion in enumerate(talk.get("criteria") or []):
        if criterion.get("is_critical") and effective_verdict(talk, criterion, position) in FAIL:
            return True
    return False


def low_confidence(talk) -> bool:
    return any(c.get("source") == "transcript" and _num(c.get("conf")) is not None
               and _num(c.get("conf")) <= config.REVIEW_MODEL_CONF
               for c in talk.get("criteria") or [])


# ── цифры ─────────────────────────────────────────────────────────────────────

def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def section_key(talk) -> str:
    """Раздел сводки — шкала, по которой оценён разговор. У Тез КЦ звонки и чаты
    «ТП линии» оцениваются разными шкалами («ТП линия» и «ТП чат»), и одна
    сводка на две шкалы смешала бы критерии с одинаковыми именами."""
    return str(talk.get("scale_direction") or "none")


def section_stats(talks) -> dict:
    """Точные цифры раздела: и для шапки на экране, и для модели."""
    scored = [t["ai_score"] for t in talks if t.get("ai_score") is not None]
    critical = [t for t in talks if is_critical_failure(t)]
    per_criterion: dict[str, dict] = {}
    order: list[str] = []
    for talk in talks:
        for position, criterion in enumerate(talk.get("criteria") or []):
            name = str(criterion.get("name") or f"Критерий {position + 1}")
            entry = per_criterion.get(name)
            if entry is None:
                entry = per_criterion[name] = {
                    "name": name, "is_critical": bool(criterion.get("is_critical")),
                    "applicable": 0, "fail": [], "deficiency": []}
                order.append(name)
            verdict = effective_verdict(talk, criterion, position)
            if verdict in NOT_COUNTED or verdict is None:
                continue
            entry["applicable"] += 1
            if verdict in FAIL:
                entry["fail"].append(talk["ref"])
            elif verdict == DEFICIENCY:
                entry["deficiency"].append(talk["ref"])
    operators: dict[str, dict] = {}
    for talk in talks:
        key = str(talk.get("operator_id") or talk.get("operator"))
        entry = operators.setdefault(key, {"name": talk.get("operator") or "—", "refs": [],
                                           "scores": [], "critical": 0, "issues": 0,
                                           "deficiencies": 0})
        entry["refs"].append(talk["ref"])
        if talk.get("ai_score") is not None:
            entry["scores"].append(talk["ai_score"])
        entry["critical"] += 1 if is_critical_failure(talk) else 0
        for position, c in enumerate(talk.get("criteria") or []):
            verdict = effective_verdict(talk, c, position)
            entry["issues"] += 1 if verdict in FAIL else 0
            entry["deficiencies"] += 1 if verdict == DEFICIENCY else 0
    reviewed = [t for t in talks if t.get("outcome") or t.get("human_score") is not None]
    corrected = [t for t in talks if t.get("outcome") == "adjudicated"
                 or (t.get("human") or {}).get("changes")]
    return {
        "evaluated": len(talks),
        "calls": sum(1 for t in talks if t["family"] == FAMILY_CALLS),
        "chats": sum(1 for t in talks if t["family"] == FAMILY_CHATS),
        "ai_avg": _avg(scored),
        "ai_min": round(min(scored)) if scored else None,
        "below_60": sum(1 for s in scored if s < 60),
        "critical": len(critical),
        "critical_refs": [t["ref"] for t in critical],
        "reviewed": len(reviewed),
        "corrected": len(corrected),
        "human_avg": _avg([t.get("human_score") for t in talks]),
        "operators": len(operators),
        "asr_low": [t["ref"] for t in talks
                    if t.get("asr_conf") is not None and t["asr_conf"] < config.ASR_CONF_HARD],
        "media_failed": [t["ref"] for t in talks if t.get("media_failed")],
        "low_confidence": [t["ref"] for t in talks if low_confidence(t)],
        "criteria": [per_criterion[name] for name in order],
        "people": sorted(
            ({"name": e["name"], "refs": e["refs"], "avg": _avg(e["scores"]),
              "critical": e["critical"], "issues": e["issues"], "deficiencies": e["deficiencies"]}
             for e in operators.values()),
            key=lambda e: (-e["critical"], -e["issues"], -e["deficiencies"],
                           e["avg"] if e["avg"] is not None else 101)),
    }


def public_stats(stats: dict) -> dict:
    """Цифры для экрана: без перечней ссылок и разреза по людям — их показывает
    сама сводка, а шапке раздела нужны только итоги."""
    keys = ("evaluated", "calls", "chats", "ai_avg", "ai_min", "below_60", "critical",
            "reviewed", "corrected", "human_avg", "operators")
    out = {key: stats.get(key) for key in keys}
    out["critical_refs"] = list(stats.get("critical_refs") or [])
    return out


def baseline(cur, day, department, allowed_direction_ids=None) -> dict:
    """С чем сравнивать день: по каждой шкале за BASELINE_DAYS предыдущих дней —
    средний балл, доля критических и доля провалов по каждому критерию.

    Без этого сводка не отличает «у этого направления всегда так» от «сегодня
    просело», а для руководителя важно именно второе. Читается облегчённо: из
    критериев — только имя и вердикт, транскриптов нет вовсе."""
    from .. import api
    scope_sql, scope_params = api._direction_predicate(
        cur, allowed_direction_ids, department, api._SUBJECT_DIRECTION)
    if scope_sql is None:
        return {}
    first = day - timedelta(days=BASELINE_DAYS)
    last = day - timedelta(days=1)
    cur.execute(
        f"""SELECT rc.payload->>'direction_id', {api._SUBJECT_AI_SCORE},
                   (CASE WHEN jsonb_typeof(rc.payload->'criteria') = 'array'
                         THEN (SELECT jsonb_agg(jsonb_build_array(c->>'name', c->>'ai',
                                                                  c->>'is_critical'))
                                 FROM jsonb_array_elements(rc.payload->'criteria') c) END)
              FROM (SELECT DISTINCT ON (rc.subject_kind, rc.call_id)
                           rc.subject_kind, rc.call_id, rc.model, rc.created_at, rc.payload
                      FROM ai_review_cache rc
                     ORDER BY rc.subject_kind, rc.call_id, rc.created_at DESC) rc"""
        + api._SUBJECT_JOIN
        + f" WHERE {api._SUBJECT_DAY} BETWEEN %s AND %s" + api._SUBJECT_EXISTS + scope_sql,
        (first, last, *scope_params))
    acc: dict[str, dict] = {}
    for direction_id, score, criteria in cur.fetchall():
        key = str(int(_num(direction_id))) if _num(direction_id) is not None else "none"
        entry = acc.setdefault(key, {"n": 0, "scores": [], "critical": 0, "criteria": {}})
        entry["n"] += 1
        if _num(score) is not None:
            entry["scores"].append(_num(score))
        critical = False
        for item in criteria or []:
            if not isinstance(item, list) or len(item) < 3:
                continue
            name, verdict, is_critical = item[0], _verdict(item[1]), str(item[2]).lower() == "true"
            if verdict in NOT_COUNTED or verdict is None:
                continue
            crit = entry["criteria"].setdefault(str(name), [0, 0])
            crit[0] += 1
            if verdict in FAIL or verdict == DEFICIENCY:
                crit[1] += 1
            if is_critical and verdict in FAIL:
                critical = True
        entry["critical"] += 1 if critical else 0
    out = {}
    for key, entry in acc.items():
        if entry["n"] < 5:
            # На паре разговоров «обычно» не бывает: сравнение с ними только
            # пугало бы ложными «просел вдвое».
            continue
        out[key] = {
            "n": entry["n"],
            "ai_avg": _avg(entry["scores"]),
            "critical_share": round(entry["critical"] / entry["n"], 3),
            "criteria": {name: round(bad / total, 3)
                         for name, (total, bad) in entry["criteria"].items() if total >= 5},
        }
    return out


def refs_text(refs) -> str:
    refs = list(refs or [])
    shown = " ".join(f"#{r}" for r in refs[:_REFS_IN_STAT])
    return shown + (f" и ещё {len(refs) - _REFS_IN_STAT}" if len(refs) > _REFS_IN_STAT else "")


def json_default(value):
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=json_default)


__all__ = [
    "FAMILY_CALLS", "FAMILY_CHATS", "FAIL", "DEFICIENCY", "BASELINE_DAYS",
    "transcript_text", "clip", "day_subjects", "inputs_hash", "collect_day",
    "attach_details", "attach_human_reviews", "attach_direction_names",
    "effective_verdict", "is_critical_failure", "low_confidence", "section_key",
    "section_stats", "public_stats", "baseline", "refs_text", "dumps",
]
