"""RAG-хранилище разборов на pgvector: сохранение + retrieval по косинусной близости.
Embedding «мягкий»: если провайдер недоступен — разбор всё равно сохранится (embedding NULL)."""
from __future__ import annotations

from .. import config
from ..embeddings.provider import get_provider


def _rw_conn():
    return config.connect_rw()


def _vec(v):
    """Список float → текстовый формат pgvector '[1,2,3]' (или None)."""
    return "[" + ",".join(str(float(x)) for x in v) + "]" if v else None


def _embed(text):
    try:
        return get_provider().embed([text])[0]
    except Exception:
        return None  # разбор сохраняем и без вектора


def save_adjudication(*, direction_id, criterion_idx, criterion_name, call_id,
                      excerpt, ai_verdict, correct_verdict, reason,
                      situation_tag=None, created_by=None) -> int:
    """Авто-сохранение разбора человека (+ embedding). Вызывается из экшена ревью."""
    vec = _embed(f"{criterion_name or ''}. {excerpt}. {reason}")
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
    """Разборы по критерию. С query_text — ранжируем по косинусной близости (pgvector),
    иначе — последние. Ограниченный список → промпт не растёт с базой."""
    k = k or config.RETRIEVAL_TOP_K
    ro = config.connect_ro(); cur = ro.cursor()
    try:
        qvec = _embed(query_text) if query_text else None
        if qvec:
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
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        cur.close(); ro.close()
