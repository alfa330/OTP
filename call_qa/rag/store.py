"""RAG-хранилище разборов на pgvector: сохранение + retrieval по косинусной близости.
Embedding «мягкий»: если провайдер недоступен — разбор всё равно сохранится (embedding NULL)."""
from __future__ import annotations

from .. import config
from ..embeddings.provider import get_provider


def _rw_conn():
    return config.connect_rw()


def criteria_with_adjudications(direction_id) -> set:
    """Индексы критериев направления, по которым уже есть разборы — их отдаём на HARD-модель,
    чтобы стандарт из разбора применяла сильная модель (даже если BULK уверенно поставил Correct)."""
    try:
        ro = config.connect_ro(); cur = ro.cursor()
        cur.execute("SELECT DISTINCT criterion_idx FROM qa_adjudications WHERE direction_id=%s", (direction_id,))
        idx = {r[0] for r in cur.fetchall()}
        cur.close(); ro.close()
        return idx
    except Exception:
        return set()


def _vec(v):
    """Список float → текстовый формат pgvector '[1,2,3]' (или None)."""
    return "[" + ",".join(str(float(x)) for x in v) + "]" if v else None


def _embed(text):
    try:
        return get_provider().embed([text])[0]
    except Exception:
        return None  # разбор сохраняем и без вектора


# Vertex text-multilingual-embedding-002 принимает ~2048 токенов НА ИНСТАНС и молча
# обрезает хвост: весь звонок одним вектором физически не влезает — эмбеддинг строился бы
# только по первым минутам. Поэтому транскрипт эмбеддится КУСКАМИ (одним HTTP-вызовом),
# а retrieval берёт максимум близости по кускам: правило находится, если его ситуация
# встречается в любой части звонка.
_EMBED_CHUNK_CHARS = 3500   # ~2000 токенов кириллицы — с запасом под лимит Vertex
_EMBED_MAX_CHUNKS = 8       # потолок стоимости/запросов: головные куски + хвост звонка


def _chunk_for_embedding(text: str) -> list[str]:
    """Транскрипт → куски по границам реплик, каждый в лимит Vertex. Сверх потолка
    оставляем первые куски и последний: начало и завершение звонка информативнее
    середины с повторами."""
    chunks, buf, size = [], [], 0
    for ln in text.split("\n"):
        if buf and size + len(ln) + 1 > _EMBED_CHUNK_CHARS:
            chunks.append("\n".join(buf)); buf, size = [], 0
        buf.append(ln); size += len(ln) + 1
    if buf:
        chunks.append("\n".join(buf))
    if len(chunks) > _EMBED_MAX_CHUNKS:
        chunks = chunks[:_EMBED_MAX_CHUNKS - 1] + [chunks[-1]]
    return chunks


def embed_query_chunks(text) -> list:
    """Эмбеддинги всего транскрипта кусками. [] → fallback на последние разборы."""
    if not text:
        return []
    try:
        return get_provider().embed(_chunk_for_embedding(text))
    except Exception:
        return []


def bump_use_count(ids):
    """+1 к use_count подтянутых в оценку разборов — в «Базе разборов» видно, какие
    правила реально работают. Best-effort: без RW (локальная разработка) тихо пропускаем."""
    if not ids:
        return
    try:
        with config.connect_rw() as c, c.cursor() as cur:
            cur.execute("UPDATE qa_adjudications SET use_count = use_count + 1 WHERE id = ANY(%s)",
                        (list(ids),))
        c.close()
    except Exception:
        pass


def save_adjudication(*, direction_id, criterion_idx, criterion_name, call_id,
                      excerpt, ai_verdict, correct_verdict, reason,
                      not_covered=None, situation=None, situation_tag=None, created_by=None) -> int:
    """Авто-сохранение разбора человека (+ embedding). Вызывается из экшена ревью.
    В embedding входят и границы (not_covered): прецедент должен находиться и по звонкам
    с нарушением, которое правило НЕ оправдывает — чтобы модель увидела запрет."""
    vec = _embed(". ".join(p for p in (criterion_name, situation, excerpt, reason, not_covered) if p))
    with _rw_conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_adjudications
               (direction_id, criterion_idx, criterion_name, call_id, excerpt,
                ai_verdict, correct_verdict, reason, not_covered, situation, situation_tag, embedding, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s) RETURNING id""",
            (direction_id, criterion_idx, criterion_name, call_id, excerpt,
             ai_verdict, correct_verdict, reason, not_covered or None, situation or None,
             situation_tag, _vec(vec), created_by),
        )
        return cur.fetchone()[0]


_RETRIEVE_COLS = ["id", "criterion_name", "excerpt", "ai_verdict", "correct_verdict",
                  "reason", "not_covered", "situation", "sim"]


def _retrieve_one(cur, direction_id, criterion_idx, qvec, k) -> list[dict]:
    if qvec:
        cur.execute(
            """SELECT id, criterion_name, excerpt, ai_verdict, correct_verdict, reason, not_covered, situation,
                      1 - (embedding <=> %s::vector) AS sim
                 FROM qa_adjudications
                WHERE direction_id=%s AND criterion_idx=%s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector LIMIT %s""",
            (_vec(qvec), direction_id, criterion_idx, _vec(qvec), k))
    else:
        cur.execute(
            """SELECT id, criterion_name, excerpt, ai_verdict, correct_verdict, reason, not_covered, situation, NULL
                 FROM qa_adjudications
                WHERE direction_id=%s AND criterion_idx=%s
                ORDER BY created_at DESC LIMIT %s""",
            (direction_id, criterion_idx, k))
    return [dict(zip(_RETRIEVE_COLS, r)) for r in cur.fetchall()]


def retrieve(*, direction_id, criterion_idx, query_text=None, query_vector=None, k=None) -> list[dict]:
    """Разборы по критерию. С query_text/query_vector — ранжируем по косинусной близости (pgvector),
    иначе — последние. Ограниченный список → промпт не растёт с базой."""
    k = k or config.RETRIEVAL_TOP_K
    ro = config.connect_ro(); cur = ro.cursor()
    try:
        qvec = query_vector if query_vector is not None else (_embed(query_text) if query_text else None)
        return _retrieve_one(cur, direction_id, criterion_idx, qvec, k)
    finally:
        cur.close(); ro.close()


def _retrieve_multi(cur, direction_id, criterion_idx, query_vectors, k) -> list[dict]:
    """Top-K по максимуму близости среди кусков транскрипта (см. _chunk_for_embedding)."""
    best = {}
    for qv in query_vectors:
        for h in _retrieve_one(cur, direction_id, criterion_idx, qv, k):
            prev = best.get(h["id"])
            if prev is None or (h["sim"] or 0) > (prev["sim"] or 0):
                best[h["id"]] = h
    return sorted(best.values(), key=lambda h: -(h["sim"] or 0))[:k]


def retrieve_for_criteria(*, direction_id, criterion_idxs, query_vectors=None, k=None) -> dict:
    """Разборы сразу по нескольким критериям ОДНИМ подключением: оценка звонка делала
    по TLS-хендшейку к Postgres на каждый критерий (~15 на звонок) — теперь один.
    query_vectors — эмбеддинги кусков транскрипта; пусто → последние разборы.
    Возвращает {criterion_idx: [hits]}."""
    k = k or config.RETRIEVAL_TOP_K
    out = {idx: [] for idx in criterion_idxs}
    if not criterion_idxs:
        return out
    ro = config.connect_ro(); cur = ro.cursor()
    try:
        for idx in criterion_idxs:
            if query_vectors:
                out[idx] = _retrieve_multi(cur, direction_id, idx, query_vectors, k)
            else:
                out[idx] = _retrieve_one(cur, direction_id, idx, None, k)
    finally:
        cur.close(); ro.close()
    return out
