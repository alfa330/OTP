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
import hashlib
import os

from .. import config

_SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")


def split_sql_statements(sql: str) -> list[str]:
    """Split PostgreSQL SQL without breaking quoted strings or function bodies.

    The original runner used ``str.split(';')``.  That is unsafe as soon as a
    migration contains a trigger function, a JSON/string literal with a
    semicolon, or a dollar-quoted block.  This deliberately small lexer handles
    PostgreSQL single/double quoted strings, dollar quotes and nested block
    comments.  It does not try to parse SQL; it only recognises where a
    semicolon is a real statement boundary.
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    state = "normal"
    dollar_tag = ""
    block_depth = 0

    while i < len(sql):
        char = sql[i]
        pair = sql[i:i + 2]

        if state == "line_comment":
            current.append(char)
            i += 1
            if char == "\n":
                state = "normal"
            continue

        if state == "block_comment":
            if pair == "/*":
                block_depth += 1
                current.append(pair)
                i += 2
            elif pair == "*/":
                block_depth -= 1
                current.append(pair)
                i += 2
                if block_depth == 0:
                    state = "normal"
            else:
                current.append(char)
                i += 1
            continue

        if state == "single_quote":
            current.append(char)
            i += 1
            if char == "'":
                if i < len(sql) and sql[i] == "'":
                    current.append(sql[i])
                    i += 1
                else:
                    state = "normal"
            continue

        if state == "double_quote":
            current.append(char)
            i += 1
            if char == '"':
                if i < len(sql) and sql[i] == '"':
                    current.append(sql[i])
                    i += 1
                else:
                    state = "normal"
            continue

        if state == "dollar_quote":
            if sql.startswith(dollar_tag, i):
                current.append(dollar_tag)
                i += len(dollar_tag)
                state = "normal"
            else:
                current.append(char)
                i += 1
            continue

        # Normal SQL text.
        if pair == "--":
            current.append(pair)
            i += 2
            state = "line_comment"
        elif pair == "/*":
            current.append(pair)
            i += 2
            block_depth = 1
            state = "block_comment"
        elif char == "'":
            current.append(char)
            i += 1
            state = "single_quote"
        elif char == '"':
            current.append(char)
            i += 1
            state = "double_quote"
        elif char == "$":
            # PostgreSQL dollar quote: $$...$$ or $tag$...$tag$.
            end = sql.find("$", i + 1)
            candidate = sql[i:end + 1] if end >= 0 else ""
            tag_body = candidate[1:-1]
            if candidate and (not tag_body or
                              (tag_body[0].isalpha() or tag_body[0] == "_") and
                              all(c.isalnum() or c == "_" for c in tag_body)):
                dollar_tag = candidate
                current.append(candidate)
                i = end + 1
                state = "dollar_quote"
            else:
                current.append(char)
                i += 1
        elif char == ";":
            statement = "".join(current).strip()
            if statement and not _comments_only(statement):
                statements.append(statement)
            current = []
            i += 1
        else:
            current.append(char)
            i += 1

    tail = "".join(current).strip()
    if tail and not _comments_only(tail):
        statements.append(tail)
    return statements


def _comments_only(value: str) -> bool:
    """True when a trailing chunk contains comments/whitespace and no SQL."""
    import re
    without_blocks = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    without_lines = re.sub(r"--[^\r\n]*", "", without_blocks)
    return not without_lines.strip()


def _statements() -> list[str]:
    """Load and safely split the production schema."""
    with open(_SCHEMA, encoding="utf-8") as schema_file:
        return split_sql_statements(schema_file.read())


class SchemaMigrationError(RuntimeError):
    pass


def ensure_schema(conn, *, strict: bool = True) -> tuple[list[str], list[tuple[str, str]]]:
    """Apply the schema atomically by default.

    A production deployment must never expose half-created tables without the
    matching views, constraints and immutable triggers.  ``strict=False`` is a
    diagnostic rolling-deploy mode only; it retains the old per-statement report.
    """
    statements = _statements()
    if strict:
        applied = []
        schema_hash = hashlib.sha256("\n;\n".join(statements).encode("utf-8")).hexdigest()
        try:
            with conn:
                with conn.cursor() as cur:
                    # Several web workers may start together.  Serialize the
                    # migration and re-check the hash while holding the lock so
                    # expensive view/index DDL runs exactly once per schema.
                    cur.execute("SELECT pg_advisory_xact_lock(%s,%s)", (71624, 1))
                    cur.execute(
                        """CREATE TABLE IF NOT EXISTS qa_schema_migrations (
                               schema_hash character(64) PRIMARY KEY,
                               statement_count integer NOT NULL,
                               applied_at timestamptz NOT NULL DEFAULT now()
                           )""")
                    cur.execute(
                        """SELECT 1 FROM qa_schema_migrations
                            WHERE schema_hash=%s AND statement_count=%s""",
                        (schema_hash, len(statements)),
                    )
                    if cur.fetchone():
                        return [], []
                    for stmt in statements:
                        head = " ".join(stmt.split())[:70]
                        cur.execute(stmt)
                        applied.append(head)
                    cur.execute(
                        """INSERT INTO qa_schema_migrations(schema_hash,statement_count)
                             VALUES (%s,%s) ON CONFLICT (schema_hash) DO NOTHING""",
                        (schema_hash, len(statements)),
                    )
            return applied, []
        except Exception as exc:
            conn.rollback()
            raise SchemaMigrationError(
                f"atomic call_qa schema migration failed after {len(applied)} statements: {exc}"
            ) from exc

    applied, skipped = [], []
    for stmt in statements:
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
    """Apply schema at startup; production trace mode fails closed on partial DDL."""
    import logging
    try:
        conn = config.connect_rw()
    except Exception as e:
        logging.info("call_qa: миграция пропущена (нет read-write подключения): %s", e)
        return
    try:
        applied, skipped = ensure_schema(conn, strict=bool(config.RAG_TRACE_REQUIRED))
        logging.info("call_qa schema: применено %d, пропущено %d", len(applied), len(skipped))
        for head, err in skipped:
            logging.warning("call_qa schema пропущено: %s :: %s", head, err)
    except Exception:
        logging.exception("call_qa schema: ошибка применения")
        if config.RAG_TRACE_REQUIRED:
            raise
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
