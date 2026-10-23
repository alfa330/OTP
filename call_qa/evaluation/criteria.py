"""Загрузка мониторинговой шкалы направления (directions.criteria) — read-only."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
import psycopg2

from .. import config
from . import wiki_refs


def _normalise_identity(value: str) -> str:
    """Stable, language-agnostic text normalisation for criterion identities.

    The legacy scale stores criteria as an ordered JSON array without IDs.  Array
    positions are not identities: inserting a criterion at the beginning must not
    silently attach old adjudications to a different requirement.  Prefer an
    explicit ID from the scale and otherwise derive one from the direction and
    criterion name (description is only a collision breaker, so ordinary wording
    edits are tracked by ``scale_hash`` instead of remapping every rule).
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)


def criterion_identity(direction_id: int, raw: dict, *, duplicate: int = 0) -> str:
    explicit = raw.get("criterion_id") or raw.get("id") or raw.get("key")
    if explicit:
        return str(explicit).strip()
    name = _normalise_identity(raw.get("name"))
    seed = f"{int(direction_id)}\x1f{name}"
    if duplicate:
        seed += f"\x1f{_normalise_identity(raw.get('value'))}\x1f{duplicate}"
    return f"d{int(direction_id)}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def scale_fingerprint(direction_id: int, name: str, criteria: list[dict]) -> str:
    """Content-addressed revision of the complete monitoring scale."""
    canonical = {
        "direction_id": int(direction_id),
        "direction_name": _normalise_identity(name),
        "criteria": [{
            "criterion_id": c["criterion_id"],
            "name": c["name"],
            "description": c["description"],
            "weight": c["weight"],
            "is_critical": c["is_critical"],
            "deficiency": c["deficiency"],
        } for c in criteria],
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_guidance(cur, canonical_id: int) -> str:
    """Указания направления для ИИ (qa_direction_guidance) или ''.

    Таблицу разворачивает старт приложения (call_qa.rag.migrate). Пока её нет —
    42P01, — указаний просто нет: необязательная таблица не должна ронять оценку,
    так же переживает её отсутствие criterion_config.load_config."""
    try:
        cur.execute("SELECT guidance FROM qa_direction_guidance WHERE direction_id=%s",
                    (int(canonical_id),))
        row = cur.fetchone()
    except Exception as exc:
        if (getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)) == "42P01":
            return ""
        raise
    return str(row[0] or "") if row else ""


def load_direction(direction_id: int) -> dict:
    """Возвращает {id, name, criteria: [{idx, name, value/description, weight, is_critical, deficiency}],
    guidance}.

    ``id`` — канонический (стабильный) id направления: для архивной версии шкалы
    это id живой строки (directions.canonical_id). Все производные идентичности
    (criterion_id, scale_hash) и связки (rollout, база знаний, scale revisions)
    считаются от канонического id.

    КРИТЕРИИ ВСЕГДА берутся из ЖИВОЙ (канонической) строки направления — т.е.
    ИИ оценивает по ТЕКУЩЕЙ мониторинговой шкале, даже если звонок привязан к
    архивной строке (старой версии критериев). Так батч и интерактив всегда
    подтягивают актуальные критерии/веса. ``row_id`` — строка, из которой
    реально загружены критерии (после редиректа на каноническую).

    ``guidance`` — указания для ИИ на уровне направления (правила шкалы, справка
    по процессу), '' — их нет. В scale_hash они не входят: шкала та же, меняется
    промпт, и это уже ловит prompt_hash отпечатка (evaluator.build_system).

    ``wiki`` — статьи вики, на которые ссылаются описания критериев и указания
    (wiki_refs.load), с их текущим текстом; [] — ссылок нет. Тоже вне scale_hash:
    в шкале лежит ссылка, а текст статьи меняется в вики и ловится prompt_hash."""
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute("SELECT id, name, criteria, canonical_id FROM directions WHERE id=%s", (direction_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise ValueError(f"направление {direction_id} не найдено")
    canonical_id = int(row[3] or row[0])
    row_id, name, raw = row[0], row[1], (row[2] or [])
    if canonical_id != row_id:
        # передан архивный id → грузим критерии живой (канонической) строки
        cur.execute("SELECT name, criteria FROM directions WHERE id=%s", (canonical_id,))
        crow = cur.fetchone()
        if crow is not None and crow[1] is not None:
            name, raw, row_id = crow[0], (crow[1] or []), canonical_id
    try:
        guidance = _read_guidance(cur, canonical_id)
        wiki = wiki_refs.load(
            cur, [(i, c.get("value") if isinstance(c, dict) else "") for i, c in enumerate(raw)],
            guidance)
    finally:
        cur.close(); conn.close()
    crits = []
    name_counts = {}
    for i, c in enumerate(raw):
        c = c if isinstance(c, dict) else {}
        norm_name = _normalise_identity(c.get("name"))
        duplicate = name_counts.get(norm_name, 0)
        name_counts[norm_name] = duplicate + 1
        crits.append({
            "idx": i,
            "criterion_id": criterion_identity(canonical_id, c, duplicate=duplicate),
            "name": c.get("name") or f"Критерий {i + 1}",
            "description": c.get("value") or "",
            "weight": c.get("weight"),
            "is_critical": bool(c.get("isCritical")),
            "deficiency": c.get("deficiency"),
        })
    return {"id": canonical_id, "row_id": row_id, "name": name, "criteria": crits,
            "scale_hash": scale_fingerprint(canonical_id, name, crits),
            "guidance": guidance, "wiki": wiki}
