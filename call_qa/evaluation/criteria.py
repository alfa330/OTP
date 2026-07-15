"""Загрузка мониторинговой шкалы направления (directions.criteria) — read-only."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
import psycopg2

from .. import config


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


def load_direction(direction_id: int) -> dict:
    """Возвращает {id, name, criteria: [{idx, name, value/description, weight, is_critical, deficiency}]}."""
    conn = config.connect_ro()
    cur = conn.cursor(); cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute("SELECT id, name, criteria FROM directions WHERE id=%s", (direction_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        raise ValueError(f"направление {direction_id} не найдено")
    raw = row[2] or []
    crits = []
    name_counts = {}
    for i, c in enumerate(raw):
        c = c if isinstance(c, dict) else {}
        norm_name = _normalise_identity(c.get("name"))
        duplicate = name_counts.get(norm_name, 0)
        name_counts[norm_name] = duplicate + 1
        crits.append({
            "idx": i,
            "criterion_id": criterion_identity(row[0], c, duplicate=duplicate),
            "name": c.get("name") or f"Критерий {i + 1}",
            "description": c.get("value") or "",
            "weight": c.get("weight"),
            "is_critical": bool(c.get("isCritical")),
            "deficiency": c.get("deficiency"),
        })
    return {"id": row[0], "name": row[1], "criteria": crits,
            "scale_hash": scale_fingerprint(row[0], row[1], crits)}
