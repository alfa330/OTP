# -*- coding: utf-8 -*-
"""Таблицы сводки дня и переписки с ИИ (схема — call_qa/rag/schema.sql).

Соединения — свои, из call_qa.config, а не из пула приложения: генерация идёт
минутами и в фоне, и держать на это время место общего пула нельзя. Модель
зовётся строго ВНЕ соединения: здесь только короткие чтения и записи.
"""
from __future__ import annotations

from psycopg2.extras import Json

from .. import config

# Сколько ждать генерацию, прежде чем считать её осиротевшей (инстанс умер).
# Отдел генерируется за минуту-две; двадцать минут — с запасом на медленный
# Vertex и резервное звено цепочки.
STALE_RUN_MINUTES = 20
# Сколько реплик переписки держать на экране и отдавать модели.
THREAD_LIMIT = 200
HISTORY_FOR_MODEL = 10


def _row_dict(cur, row):
    return {desc[0]: value for desc, value in zip(cur.description, row)} if row else None


def _schema_missing(error) -> bool:
    """Таблиц ещё нет (миграция не дошла) — читаем как «сводок нет», а не 500.

    Схему применяет старт приложения (rag.migrate.apply_on_startup); упади он на
    чужой правке схемы — раздел без этой страховки отдавал бы ошибку на каждой
    вкладке, где спрашивают заголовки сводок."""
    import psycopg2
    return isinstance(error, (psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedColumn))


def claim(department: str, day, triggered_by: str) -> bool:
    """Взять генерацию (отдел, день). False — её уже ведёт другой процесс.

    Условный upsert вместо advisory-лока: лок живёт на соединении, а держать
    соединение всю генерацию (минуты, вызовы модели) значит занимать его зря."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_qa_day_digests (department_code, digest_day, running_since,
                                                  triggered_by)
                   VALUES (%s, %s, now(), %s)
                   ON CONFLICT (department_code, digest_day) DO UPDATE
                      SET running_since = now(), triggered_by = EXCLUDED.triggered_by
                    WHERE ai_qa_day_digests.running_since IS NULL
                       OR ai_qa_day_digests.running_since
                          < now() - make_interval(mins => %s)
                RETURNING id""",
                (department, day, triggered_by, STALE_RUN_MINUTES))
            return cur.fetchone() is not None
    finally:
        conn.close()


def release(department: str, day, *, error: str | None = None,
            forget_pending: bool = False) -> None:
    """Отпустить генерацию без нового текста (сбой или «и так свежая»).

    Прежний текст остаётся: упавшая перегенерация не должна стирать вчерашнюю
    сводку. Статус 'failed' — только если показать нечего вовсе.

    forget_pending — день без оценённых разговоров: строку, которую завёл claim
    и в которой ничего не написано, удалить. Это не сбой — оценки дня придут
    вторым проходом выборки или вручную, и «составить не удалось» висело бы над
    ними до следующей генерации."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            if forget_pending:
                cur.execute("""DELETE FROM ai_qa_day_digests
                                WHERE department_code = %s AND digest_day = %s
                                  AND status = 'pending'""",
                            (department, day))
            cur.execute(
                """UPDATE ai_qa_day_digests
                      SET running_since = NULL,
                          last_error = %s,
                          status = CASE WHEN %s IS NOT NULL AND status = 'pending'
                                        THEN 'failed' ELSE status END
                    WHERE department_code = %s AND digest_day = %s""",
                (error[:1000] if error else None, error, department, day))
    finally:
        conn.close()


def save(department: str, day, *, headline, overview_html, sections, stats, inputs_hash,
         inputs_count, model, usage, elapsed_s, error=None) -> None:
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE ai_qa_day_digests
                      SET status = 'ready', headline = %s, overview_html = %s,
                          sections = %s, stats = %s, inputs_hash = %s, inputs_count = %s,
                          model = %s, usage = %s, elapsed_s = %s, last_error = %s,
                          running_since = NULL, generated_at = now()
                    WHERE department_code = %s AND digest_day = %s""",
                (headline, overview_html, Json(sections), Json(stats), inputs_hash,
                 int(inputs_count), model, Json(usage or {}), elapsed_s,
                 error[:1000] if error else None, department, day))
    finally:
        conn.close()


_DIGEST_COLUMNS = """department_code, digest_day, status, headline, overview_html, sections,
                     stats, inputs_hash, inputs_count, model, usage, elapsed_s, last_error,
                     triggered_by, running_since, generated_at,
                     (running_since IS NOT NULL
                      AND running_since >= now() - make_interval(mins => %s)) AS running"""


def get(cur, department: str, day) -> dict | None:
    try:
        cur.execute(f"SELECT {_DIGEST_COLUMNS} FROM ai_qa_day_digests "
                    "WHERE department_code = %s AND digest_day = %s",
                    (STALE_RUN_MINUTES, department, day))
    except Exception as error:
        if _schema_missing(error):
            return None
        raise
    return _row_dict(cur, cur.fetchone())


def for_days(cur, department: str, days) -> dict:
    """{день: краткое о сводке} — для списка дней: заголовки и состояние."""
    days = sorted(set(days or []))
    if not days:
        return {}
    try:
        cur.execute(
            """SELECT digest_day, status, headline, sections, inputs_hash, generated_at,
                      (running_since IS NOT NULL
                       AND running_since >= now() - make_interval(mins => %s))
                 FROM ai_qa_day_digests
                WHERE department_code = %s AND digest_day = ANY(%s::date[])""",
            (STALE_RUN_MINUTES, department, days))
    except Exception as error:
        if _schema_missing(error):
            return {}
        raise
    out = {}
    for day, status, headline, sections, inputs_hash, generated_at, running in cur.fetchall():
        out[day.isoformat()] = {
            "status": status, "headline": headline, "inputs_hash": inputs_hash,
            "generated_at": generated_at, "running": bool(running),
            # Для зрителя со скоупом заголовок отдела не годится — ему отдают
            # заголовок первого своего раздела (service.list_days).
            "sections": [{"key": s.get("key"), "headline": s.get("headline"),
                          "subject_directions": s.get("subject_directions") or []}
                         for s in (sections or []) if isinstance(s, dict)],
        }
    return out


# ── переписка ─────────────────────────────────────────────────────────────────

def thread(cur, department: str, day, user_id: int, limit: int = THREAD_LIMIT) -> list[dict]:
    try:
        cur.execute(
            """SELECT id, role, body, model, created_at
                 FROM (SELECT id, role, body, model, created_at
                         FROM ai_qa_digest_messages
                        WHERE department_code = %s AND digest_day = %s AND user_id = %s
                        ORDER BY id DESC LIMIT %s) t
                ORDER BY id""",
            (department, day, int(user_id), int(limit)))
    except Exception as error:
        if _schema_missing(error):
            return []
        raise
    return [{"id": int(i), "role": role, "body": body, "model": model, "created_at": created}
            for i, role, body, model, created in cur.fetchall()]


def append_pair(department: str, day, user_id: int, question: str, answer_html: str, *,
                model=None, usage=None, elapsed_s=None) -> tuple[dict, dict]:
    """Вопрос и ответ — одной транзакцией: вопрос без ответа в истории выглядел
    бы как зависший, а ответ без вопроса — как реплика ни о чём."""
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_qa_digest_messages
                       (department_code, digest_day, user_id, role, body)
                   VALUES (%s, %s, %s, 'user', %s) RETURNING id, created_at""",
                (department, day, int(user_id), question))
            q_id, q_at = cur.fetchone()
            cur.execute(
                """INSERT INTO ai_qa_digest_messages
                       (department_code, digest_day, user_id, role, body, model, usage, elapsed_s)
                   VALUES (%s, %s, %s, 'assistant', %s, %s, %s, %s) RETURNING id, created_at""",
                (department, day, int(user_id), answer_html, model, Json(usage or {}), elapsed_s))
            a_id, a_at = cur.fetchone()
    finally:
        conn.close()
    return ({"id": int(q_id), "role": "user", "body": question, "model": None, "created_at": q_at},
            {"id": int(a_id), "role": "assistant", "body": answer_html, "model": model,
             "created_at": a_at})


def clear_thread(department: str, day, user_id: int) -> int:
    conn = config.connect_rw()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""DELETE FROM ai_qa_digest_messages
                            WHERE department_code = %s AND digest_day = %s AND user_id = %s""",
                        (department, day, int(user_id)))
            return int(cur.rowcount or 0)
    finally:
        conn.close()
