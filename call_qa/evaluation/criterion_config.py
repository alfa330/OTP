"""Классификация критериев по ИСТОЧНИКУ оценки.

  transcript  — оценивает ИИ по разговору (доступно сейчас);
  system_api  — действие в ПО/бэкенде, по разговору не видно → нужна проверка данных
                через внешний API (см. data_checks.py). Пока API нет → Pending;
  manual      — только ручная проверка.

Хранится в таблице criterion_config (правится из админки). Пока таблица пустая —
работает эвристика по названию (стартовая разметка, которую вы потом поправите)."""
from __future__ import annotations
import psycopg2

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


def load_config(direction_id: int) -> dict[int, dict]:
    """idx -> {eval_source, default_verdict, notes}. Пусто, если таблицы/записей ещё нет."""
    try:
        c = config.connect_ro()
        cur = c.cursor()
        cur.execute("""SELECT criterion_idx, eval_source, default_verdict, notes
                         FROM criterion_config WHERE direction_id=%s""", (direction_id,))
        out = {r[0]: {"eval_source": r[1], "default_verdict": r[2], "notes": r[3]} for r in cur.fetchall()}
        cur.close(); c.close()
        return out
    except Exception:
        return {}   # таблицы criterion_config ещё нет — используем эвристику


def apply_to_direction(direction: dict) -> dict:
    """Проставляет каждому критерию eval_source и default_verdict (из таблицы или эвристики)."""
    cfg = load_config(direction["id"])
    for c in direction["criteria"]:
        row = cfg.get(c["idx"])
        if row and row.get("eval_source") in SOURCES:
            c["eval_source"] = row["eval_source"]
            c["default_verdict"] = row.get("default_verdict")
        else:
            c["eval_source"] = _heuristic(c["name"])
            c["default_verdict"] = None
    return direction


def set_config(direction_id, criterion_idx, eval_source, default_verdict=None, notes=None):
    """Upsert классификации (нужен DATABASE_URL read-write). Точка входа для админки/настройки."""
    assert eval_source in SOURCES, f"неизвестный источник: {eval_source}"
    with config.connect_rw() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO criterion_config
                         (direction_id, criterion_idx, eval_source, default_verdict, notes, updated_at)
                       VALUES (%s,%s,%s,%s,%s, now())
                       ON CONFLICT (direction_id, criterion_idx)
                       DO UPDATE SET eval_source=EXCLUDED.eval_source,
                                     default_verdict=EXCLUDED.default_verdict,
                                     notes=EXCLUDED.notes, updated_at=now()""",
                    (direction_id, criterion_idx, eval_source, default_verdict, notes))
