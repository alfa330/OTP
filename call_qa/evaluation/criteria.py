"""Загрузка мониторинговой шкалы направления (directions.criteria) — read-only."""
from __future__ import annotations
import psycopg2

from .. import config


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
    for i, c in enumerate(raw):
        c = c if isinstance(c, dict) else {}
        crits.append({
            "idx": i,
            "name": c.get("name") or f"Критерий {i + 1}",
            "description": c.get("value") or "",
            "weight": c.get("weight"),
            "is_critical": bool(c.get("isCritical")),
            "deficiency": c.get("deficiency"),
        })
    return {"id": row[0], "name": row[1], "criteria": crits}
