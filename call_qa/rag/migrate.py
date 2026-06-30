"""Применение схемы call_qa к РАБОЧЕЙ БД (идемпотентно).

schema.sql сам не применяется — его выполняет этот раннер с read-write подключением.
Согласовано с вашим подходом (Database._init_db делает то же самое через CREATE TABLE IF NOT EXISTS).

Использование:
  • разово:        python -m call_qa.rag.migrate          (нужен DATABASE_URL read-write)
  • на старте:     from call_qa.rag.migrate import ensure_schema; ensure_schema(conn)
                   — вызвать из вашего Database._init_db(), тогда таблицы создаются на деплое.

ВНИМАНИЕ про pgvector: CREATE EXTENSION vector может требовать привилегий. На Render
для существующей БД его включают через support; новые — сразу. Если расширение недоступно,
criterion_config и ai_evaluation_meta всё равно создадутся, а qa_adjudications будет пропущена
с понятным сообщением (её подключим, когда включат pgvector — это нужно только для RAG/Этап 2)."""
from __future__ import annotations
import os
import psycopg2

from .. import config

_SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")


def _statements() -> list[str]:
    """Разбивает schema.sql на отдельные стейтменты, убирая строки-комментарии."""
    sql = open(_SCHEMA, encoding="utf-8").read()
    out = []
    for chunk in sql.split(";"):
        lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


def ensure_schema(conn) -> tuple[list[str], list[tuple[str, str]]]:
    """Идемпотентно применяет схему. Каждый стейтмент — в своей транзакции, чтобы один
    сбой (напр. нет прав на CREATE EXTENSION) не валил остальные. Возвращает (applied, skipped)."""
    applied, skipped = [], []
    for stmt in _statements():
        head = " ".join(stmt.split())[:70]
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
            conn.commit()
            applied.append(head)
        except Exception as e:
            conn.rollback()
            skipped.append((head, str(e)[:140]))
    return applied, skipped


def apply_on_startup():
    """Вызывается из старта приложения (database.py). Полностью защищён — НИКОГДА не валит запуск.
    Сам открывает рабочее подключение (POSTGRES_*); если его нет (локально только RO) — тихо выходит."""
    import logging
    try:
        conn = config.connect_rw()
    except Exception as e:
        logging.info("call_qa: миграция пропущена (нет read-write подключения): %s", e)
        return
    try:
        applied, skipped = ensure_schema(conn)
        logging.info("call_qa schema: применено %d, пропущено %d", len(applied), len(skipped))
        for head, err in skipped:
            logging.warning("call_qa schema пропущено: %s :: %s", head, err)
    except Exception:
        logging.exception("call_qa schema: ошибка применения")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    conn = config.connect_rw()
    try:
        applied, skipped = ensure_schema(conn)
    finally:
        conn.close()
    print("Применено:")
    for a in applied:
        print("  +", a)
    if skipped:
        print("\nПропущено (требует внимания):")
        for h, e in skipped:
            print("  -", h, "\n      ::", e)


if __name__ == "__main__":
    main()
