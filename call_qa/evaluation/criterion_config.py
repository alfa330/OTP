"""Классификация критериев по ИСТОЧНИКУ оценки.

  transcript  — оценивает ИИ по разговору (доступно сейчас);
  system_api  — действие в ПО/бэкенде, по разговору не видно → нужна проверка данных
                через внешний API (см. data_checks.py). Пока API нет → Pending;
  manual      — только ручная проверка.

Хранится в таблице criterion_config (правится из админки). Пока таблица пустая —
работает эвристика по названию (стартовая разметка, которую вы потом поправите)."""
from __future__ import annotations

from .. import config

TRANSCRIPT = "transcript"
SYSTEM_API = "system_api"
MANUAL = "manual"
SOURCES = (TRANSCRIPT, SYSTEM_API, MANUAL)

# Эвристика по умолчанию (подстрока в названии → источник). Применяется ТОЛЬКО если
# для критерия нет явной записи в criterion_config. Правится в таблице/админке.
DEFAULT_RULES = [
    ("внесение информаци", SYSTEM_API),     # «Внесение информации в ПО»
    ("оформления регистрац", SYSTEM_API),   # «Корректность оформления регистрации»
    ("эскалац", SYSTEM_API),                # «Эскалация (перевод звонка)»
    ("перевод звонка", SYSTEM_API),
    ("сделка состоял", SYSTEM_API),         # факт регистрации знает система
]


def _heuristic(name: str) -> str:
    n = (name or "").lower()
    for sub, src in DEFAULT_RULES:
        if sub in n:
            return src
    return TRANSCRIPT


def _pgcode(exc: Exception) -> str | None:
    return getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)


def _read_rows(direction_id: int, *, stable: bool) -> list[tuple]:
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            if stable:
                cur.execute(
                    """SELECT criterion_idx,criterion_id,eval_source,
                              default_verdict,notes,scale_revision_id
                         FROM criterion_config WHERE direction_id=%s""",
                    (int(direction_id),),
                )
            else:
                cur.execute(
                    """SELECT criterion_idx,eval_source,default_verdict,notes
                         FROM criterion_config WHERE direction_id=%s""",
                    (int(direction_id),),
                )
            return list(cur.fetchall())
    finally:
        conn.close()


def load_config(direction_id: int) -> dict:
    """Stable criterion_id/legacy idx -> evaluation source configuration.

    During a rolling deployment old databases may not have ``criterion_id`` yet,
    so the legacy query remains an explicit compatibility path.  Callers always
    prefer the stable identity when it is available.
    """
    try:
        rows = _read_rows(direction_id, stable=True)
    except Exception as exc:
        if _pgcode(exc) == "42703":  # undefined_column: rolling schema deployment
            rows = _read_rows(direction_id, stable=False)
            return {
                row[0]: {"eval_source": row[1], "default_verdict": row[2],
                         "notes": row[3], "scale_revision_id": None}
                for row in rows
            }
        if _pgcode(exc) == "42P01":  # table has not been created yet
            return {}
        raise

    out = {}
    for idx, criterion_id, source, default, notes, scale_revision_id in rows:
        value = {"eval_source": source, "default_verdict": default, "notes": notes,
                 "scale_revision_id": scale_revision_id}
        out[int(idx)] = value
        if criterion_id:
            out[str(criterion_id)] = value
    return out


def apply_to_direction(direction: dict) -> dict:
    """Проставляет каждому критерию eval_source и default_verdict (из таблицы или эвристики)."""
    cfg = load_config(direction["id"])
    for c in direction["criteria"]:
        row = cfg.get(c.get("criterion_id")) or cfg.get(c["idx"])
        if row and row.get("eval_source") in SOURCES:
            c["eval_source"] = row["eval_source"]
            c["default_verdict"] = row.get("default_verdict")
        else:
            c["eval_source"] = _heuristic(c["name"])
            c["default_verdict"] = None
    return direction


def _normalise_items(items: list[dict]) -> list[dict]:
    rows, seen_idx, seen_ids = [], set(), set()
    for raw in items or []:
        idx = int(raw["criterion_idx"])
        criterion_id = str(raw.get("criterion_id") or "").strip()
        source = raw.get("eval_source")
        if source not in SOURCES:
            raise ValueError(f"неизвестный источник: {source}")
        if not criterion_id:
            raise ValueError(f"criterion_id обязателен для критерия {idx}")
        if idx in seen_idx:
            raise ValueError(f"criterion_idx {idx} указан повторно")
        if criterion_id in seen_ids:
            raise ValueError(f"criterion_id {criterion_id!r} указан повторно")
        seen_idx.add(idx); seen_ids.add(criterion_id)
        rows.append({
            "criterion_idx": idx, "criterion_id": criterion_id, "eval_source": source,
            **({"default_verdict": raw.get("default_verdict")}
               if "default_verdict" in raw else {}),
            **({"notes": raw.get("notes")} if "notes" in raw else {}),
        })
    if not rows:
        raise ValueError("конфигурация критериев не может быть пустой")
    return rows


def _replace_stable(conn, direction_id: int, scale_revision_id: int | None,
                    rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s,%s)", (71625, direction_id))
        cur.execute(
            """SELECT criterion_idx,criterion_id,eval_source,default_verdict,notes
                 FROM criterion_config WHERE direction_id=%s FOR UPDATE""",
            (direction_id,),
        )
        existing = list(cur.fetchall())
        by_id = {str(row[1]): row for row in existing if row[1]}
        by_idx = {int(row[0]): row for row in existing}
        values = []
        for row in rows:
            previous = by_id.get(row["criterion_id"]) or by_idx.get(row["criterion_idx"])
            default = (row["default_verdict"] if "default_verdict" in row else
                       (previous[3] if previous else None))
            notes = row["notes"] if "notes" in row else (previous[4] if previous else None)
            values.append((
                direction_id, row["criterion_idx"], row["criterion_id"], scale_revision_id,
                row["eval_source"], default, notes,
            ))
        cur.execute("DELETE FROM criterion_config WHERE direction_id=%s", (direction_id,))
        cur.executemany(
            """INSERT INTO criterion_config
                   (direction_id,criterion_idx,criterion_id,scale_revision_id,eval_source,
                    default_verdict,notes,updated_at)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,now())""",
            values,
        )


def _replace_legacy(conn, direction_id: int, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s,%s)", (71625, direction_id))
        cur.execute(
            """SELECT criterion_idx,eval_source,default_verdict,notes
                 FROM criterion_config WHERE direction_id=%s FOR UPDATE""",
            (direction_id,),
        )
        existing = {int(row[0]): row for row in cur.fetchall()}
        values = []
        for row in rows:
            previous = existing.get(row["criterion_idx"])
            default = (row["default_verdict"] if "default_verdict" in row else
                       (previous[2] if previous else None))
            notes = row["notes"] if "notes" in row else (previous[3] if previous else None)
            values.append((direction_id, row["criterion_idx"], row["eval_source"],
                           default, notes))
        cur.execute("DELETE FROM criterion_config WHERE direction_id=%s", (direction_id,))
        cur.executemany(
            """INSERT INTO criterion_config
                   (direction_id,criterion_idx,eval_source,default_verdict,notes,updated_at)
                 VALUES (%s,%s,%s,%s,%s,now())""",
            values,
        )


def replace_config(direction_id: int, scale_revision_id: int | None, items: list[dict],
                   *, conn=None) -> int:
    """Atomically replace one direction's complete criterion configuration.

    Stable identities, rather than array positions, carry defaults/notes across a
    scale reorder.  A legacy write is attempted only when PostgreSQL explicitly
    reports that the new columns are absent; constraint and data errors propagate.
    """
    direction_id = int(direction_id)
    scale_revision_id = (int(scale_revision_id) if scale_revision_id is not None else None)
    rows = _normalise_items(items)
    own_connection = conn is None
    connection = conn or config.connect_rw()
    try:
        try:
            _replace_stable(connection, direction_id, scale_revision_id, rows)
        except Exception as exc:
            if _pgcode(exc) != "42703":
                raise
            connection.rollback()
            _replace_legacy(connection, direction_id, rows)
        if own_connection:
            connection.commit()
        return len(rows)
    except Exception:
        if own_connection:
            connection.rollback()
        raise
    finally:
        if own_connection:
            connection.close()


def set_config(direction_id, criterion_idx, eval_source, default_verdict=None, notes=None,
               criterion_id=None, scale_revision_id=None):
    """Compatibility single-row upsert; bulk admin saves use :func:`replace_config`.

    Moving a stable criterion onto an occupied index raises a constraint error,
    prompting the caller to use the atomic full replacement instead of silently
    attaching another criterion's configuration.
    """
    if eval_source not in SOURCES:
        raise ValueError(f"неизвестный источник: {eval_source}")
    conn = config.connect_rw()
    try:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO criterion_config
                           (direction_id,criterion_idx,criterion_id,scale_revision_id,eval_source,
                            default_verdict,notes,updated_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,now())
                         ON CONFLICT (direction_id,criterion_id)
                           WHERE criterion_id IS NOT NULL
                         DO UPDATE SET criterion_idx=EXCLUDED.criterion_idx,
                                       scale_revision_id=EXCLUDED.scale_revision_id,
                                       eval_source=EXCLUDED.eval_source,
                                       default_verdict=EXCLUDED.default_verdict,
                                       notes=EXCLUDED.notes,updated_at=now()""",
                    (int(direction_id), int(criterion_idx), criterion_id, scale_revision_id,
                     eval_source, default_verdict, notes),
                )
        except Exception as exc:
            if _pgcode(exc) != "42703":
                raise
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO criterion_config
                           (direction_id,criterion_idx,eval_source,default_verdict,notes,updated_at)
                         VALUES (%s,%s,%s,%s,%s,now())
                         ON CONFLICT (direction_id,criterion_idx)
                         DO UPDATE SET eval_source=EXCLUDED.eval_source,
                                       default_verdict=EXCLUDED.default_verdict,
                                       notes=EXCLUDED.notes,updated_at=now()""",
                    (int(direction_id), int(criterion_idx), eval_source,
                     default_verdict, notes),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
