"""RAG-хранилище на pgvector: сохранение разборов и retrieval под промпт.
v1 — по тегам (direction+criterion); v2 — ранжирование по близости embedding."""
from __future__ import annotations
import json
import psycopg2

from .. import config
from ..embeddings.provider import get_provider


def _rw_conn():
    return config.connect_rw()


def _vec(v) -> str:
    """Список float → текстовый формат pgvector '[1,2,3]' (без зависимости pgvector-python)."""
    return "[" + ",".join(str(float(x)) for x in v) + "]"


def save_adjudication(*, direction_id, criterion_idx, criterion_name, call_id,
                      excerpt, ai_verdict, correct_verdict, reason,
                      situation_tag=None, created_by=None) -> int:
    """Авто-сохранение разбора человека + embedding. Вызывается из экшена ревью."""
    vec = get_provider().embed([f"{criterion_name or ''}. {excerpt}. {reason}"])[0]
    with _rw_conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_adjudications
               (direction_id, criterion_idx, criterion_name, call_id, excerpt,
                ai_verdict, correct_verdict, reason, situation_tag, embedding, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s) RETURNING id""",
            (direction_id, criterion_idx, criterion_name, call_id, excerpt,
             ai_verdict, correct_verdict, reason, situation_tag, _vec(vec), created_by),
        )
        return cur.fetchone()[0]


def retrieve(*, direction_id, criterion_idx, query_text=None, k=None) -> list[dict]:
    """Достаёт релевантные разборы для критерия. Если есть query_text — ранжирует по близости,
    иначе берёт последние по тегу. Возвращает ограниченный список → промпт не растёт с базой."""
    k = k or config.RETRIEVAL_TOP_K
    ro = config.connect_ro()
    cur = ro.cursor()
    if query_text:
        qvec = get_provider().embed([query_text])[0]
        cur.execute(
            """SELECT id, criterion_name, excerpt, ai_verdict, correct_verdict, reason,
                      1 - (embedding <=> %s::vector) AS sim
                 FROM qa_adjudications
                WHERE direction_id=%s AND criterion_idx=%s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector LIMIT %s""",
            (_vec(qvec), direction_id, criterion_idx, _vec(qvec), k))
    else:
        cur.execute(
            """SELECT id, criterion_name, excerpt, ai_verdict, correct_verdict, reason, NULL
                 FROM qa_adjudications
                WHERE direction_id=%s AND criterion_idx=%s
                ORDER BY created_at DESC LIMIT %s""",
            (direction_id, criterion_idx, k))
    cols = ["id", "criterion_name", "excerpt", "ai_verdict", "correct_verdict", "reason", "sim"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close(); ro.close()
    return rows
