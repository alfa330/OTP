# -*- coding: utf-8 -*-
"""Лексический поиск по кускам статей в границах периметра помощника.

Одним запросом, и периметр в нём стоит ПЕРВЫМ предикатом. Это не оптимизация, а
требование к правильности: pgvector на этапе 4 фильтрует права уже ПОСЛЕ прохода
по индексу, и у оператора с 15 доступными статьями из 36 выдача оказалась бы
пустой при наличии разрешённой релевантной статьи. Поэтому периметр — вход, а не
постфильтр, и на этапе 4 плотная ветка встроится в этот же запрос.

Два дефекта поиска по статьям здесь намеренно не повторены:

  1. В wiki/search.py OR-деградированная форма (tsq_loose) стоит в WHERE
     (search.py:134), но НИ ОДНА ступень _TIER_FTS на неё не ссылается
     (search.py:155-171) — статья, найденная только ею, падает в
     _TIER_FALLBACK = 8 и ранжируется хуже всего. А на вопросе оператора из
     десяти слов выживает как раз только она. Здесь обе формы участвуют в
     СЧЁТЕ, а не в ступенях.
  2. Там же потолок words[:8] (search.py:209) съедают служебные слова. Здесь
     ограничения на число слов нет: лексемы берёт сам to_tsvector.

Вес заголовка отдельным слагаемым не нужен: в chunk_tsv путь заголовков лежит с
весом B, текст — с весом D, а стандартные веса ts_rank_cd ({0.1,0.2,0.4,1.0} для
D,C,B,A) уже дают заголовку четырёхкратное преимущество.
"""

from ..text import query_variants

# СЧЁТ ИДЁТ ПО IDF, а не по ts_rank_cd. Это не тюнинг, а исправление дефекта,
# измеренного на боевом корпусе: у ts_rank_cd нет обратной документной частоты,
# поэтому десять совпадений частого «заказ» перевешивают одно совпадение редкого
# «термопакет». Замер: запрос «термопакет» находит нужную статью со strict-совпа-
# дением и счётом 0,36, а запрос «как заказать термопакет» не находит её вовсе —
# наверху оказывается мусор со счётом 0,44-0,63 и БЕЗ strict-совпадения. Такая
# лексическая ветка в гибриде хуже отсутствующей: она уверенно поднимает мусор
# над верным ответом, и слияние это унаследует.
#
# Поэтому вклад куска — сумма веса ln(N/df + 1) по РАЗНЫМ лексемам запроса,
# которые в нём есть. Редкое слово весит много, «как» и «водитель» почти ничего.
# ts_rank_cd остаётся, но малым слагаемым: он приносит плотность и близость,
# которых у IDF нет. Полное совпадение всех слов запроса даёт множитель.
_SEARCH_CHUNKS_SQL = """
WITH variants AS (
    SELECT DISTINCT translate(v, 'ёЁ', 'еЕ') AS txt
      FROM unnest(%(variants)s::text[]) AS v
     WHERE btrim(v) <> ''
),
q AS (
    SELECT v.txt,
           websearch_to_tsquery('russian', v.txt) AS tsq,
           (SELECT string_agg(quote_literal(lex), ' | ')
              FROM unnest(tsvector_to_array(to_tsvector('russian', v.txt))) AS lex
           ) AS loose_txt
      FROM variants v
),
qq AS (
    SELECT txt, tsq,
           CASE WHEN loose_txt IS NULL OR loose_txt = '' THEN NULL
                ELSE to_tsquery('russian', loose_txt) END AS tsq_loose
      FROM q
),
lexemes AS (
    SELECT DISTINCT lex
      FROM variants v,
           unnest(tsvector_to_array(to_tsvector('russian', v.txt))) AS lex
),
scope AS (
    SELECT count(*)::float AS n
      FROM wiki_ai_chunks WHERE article_id = ANY(%(article_ids)s)
),
weight AS (
    -- Частота считается ПО ПЕРИМЕТРУ, а не по всему корпусу: слово, редкое для
    -- портала, но частое в доступных человеку статьях, различающим не является.
    SELECT l.lex,
           ln((SELECT n FROM scope) / GREATEST(count(c.id), 1)::float + 1) AS w
      FROM lexemes l
      LEFT JOIN wiki_ai_chunks c
             ON c.article_id = ANY(%(article_ids)s)
            AND c.chunk_tsv @@ to_tsquery('russian', quote_literal(l.lex))
     GROUP BY l.lex
),
matched AS (
    SELECT c.id, sum(w.w) AS idf_score, count(*) AS lexemes_hit
      FROM wiki_ai_chunks c
      JOIN weight w ON c.chunk_tsv @@ to_tsquery('russian', quote_literal(w.lex))
     WHERE c.article_id = ANY(%(article_ids)s)
     GROUP BY c.id
),
hit AS (
    SELECT c.id,
           c.article_id,
           c.chunk_idx,
           c.heading_path,
           c.text,
           c.requires_ack,
           m.idf_score
             * (1 + 0.5 * (CASE WHEN bool_or(qq.tsq IS NOT NULL
                                             AND c.chunk_tsv @@ qq.tsq)
                                THEN 1 ELSE 0 END))
             + 0.15 * max(COALESCE(ts_rank_cd(c.chunk_tsv, qq.tsq_loose, 32), 0))
             AS score,
           bool_or(qq.tsq IS NOT NULL AND c.chunk_tsv @@ qq.tsq) AS strict_hit
      FROM wiki_ai_chunks c
      JOIN matched m ON m.id = c.id
      JOIN qq ON (qq.tsq IS NOT NULL AND c.chunk_tsv @@ qq.tsq)
              OR (qq.tsq_loose IS NOT NULL AND c.chunk_tsv @@ qq.tsq_loose)
     WHERE c.article_id = ANY(%(article_ids)s)
     GROUP BY c.id, c.article_id, c.chunk_idx, c.heading_path, c.text,
              c.requires_ack, m.idf_score
),
ranked AS (
    SELECT hit.*,
           row_number() OVER (PARTITION BY article_id ORDER BY score DESC, chunk_idx)
               AS rank_in_article
      FROM hit
     WHERE score > 0
)
SELECT r.id, r.article_id, a.title, a.slug, r.chunk_idx, r.heading_path,
       r.text, r.requires_ack, r.score, r.strict_hit
  FROM ranked r
  JOIN wiki_articles a ON a.id = r.article_id
 WHERE r.rank_in_article <= %(per_article)s
 ORDER BY r.score DESC, r.article_id, r.chunk_idx
 LIMIT %(limit)s
"""


# Плотная ветка. Периметр — в WHERE, индекса по вектору нет намеренно (см. шапку):
# при плоском переборе предикат прав применяется ДО отбора, и разрешённая
# релевантная статья не может быть вытеснена запрещёнными соседями.
_DENSE_CHUNKS_SQL = """
SELECT c.id, c.article_id, a.title, a.slug, c.chunk_idx, c.heading_path,
       c.text, c.requires_ack,
       1 - (e.embedding <=> %(qvec)s::vector) AS similarity
  FROM wiki_ai_chunks c
  JOIN wiki_ai_embeddings e
    ON e.text_hash = c.text_hash
   AND e.embed_provider = %(provider)s
   AND e.embed_model = %(model)s
   AND e.embed_dim = %(dim)s
  JOIN wiki_articles a ON a.id = c.article_id
 WHERE c.article_id = ANY(%(article_ids)s)
   AND 1 - (e.embedding <=> %(qvec)s::vector) >= %(min_similarity)s
 ORDER BY e.embedding <=> %(qvec)s::vector
 LIMIT %(limit)s
"""

# Порог близости. Замерен на живых вопросах: при 0,72 вопрос «сколько мне
# отпускных положено» даёт лучшую близость 0,661, куски в контекст не попадают
# вовсе, и модель отвечает «в доступных вам статьях этого нет» — то есть порог, а
# не уговоры в промпте, и есть главный анти-галлюцинационный слой. Для ВЫДАЧИ
# порог мягче (0,68), чтобы гибрид имел материал для слияния; строгий гейт на
# «есть ли ответ вообще» ставится слоем ответа.
DENSE_FLOOR = 0.68

# Константа RRF. 60 — общепринятое значение: оно делает вклад первых позиций
# сопоставимым, а не подавляющим, поэтому кусок, найденный ОБЕИМИ ветками
# невысоко, обгоняет кусок, найденный одной ветвью первым. Именно это нам и
# нужно: лексика ловит термины и номера, вектор — перефразировки.
RRF_K = 60

# Вклад ветвей в RRF: (лексика, вектор). Само RRF в боевом пути НЕ используется —
# см. fuse() и замер там же; оставлено как инструмент для равноправных ветвей.
BRANCH_WEIGHTS = (0.4, 1.0)


def search_dense(cursor, *, article_ids, query_vector, limit=20,
                 min_similarity=DENSE_FLOOR):
    """Куски, близкие к вектору вопроса, в границах периметра."""
    ids = sorted({int(x) for x in (article_ids or ())})
    if not ids or not query_vector:
        return []

    from .embed import _as_vector, provider_contract

    params = dict(provider_contract())
    params.update({'qvec': _as_vector(query_vector), 'article_ids': ids,
                   'limit': int(limit), 'min_similarity': float(min_similarity)})
    cursor.execute(_DENSE_CHUNKS_SQL, params)
    columns = ('chunk_id', 'article_id', 'title', 'slug', 'chunk_idx',
               'heading_path', 'text', 'requires_ack', 'similarity')
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def fuse_rrf(*branches, limit=8, per_article=3, weights=None):
    """Слияние выдач по reciprocal rank fusion.

    Складываются ОБРАТНЫЕ РАНГИ, а не счёта ветвей: счёт лексики — это сумма IDF
    в неограниченной шкале, счёт вектора — косинус в [0,1]. Складывать их напрямую
    значило бы отдать вес случайности масштабов.

    weights задаёт вклад ветвей и по умолчанию НЕ равен единицам. Замер на боевом
    корпусе (29 вопросов, 202 куска): чистый вектор даёт 22/25 первым результатом
    по-русски, равновесный гибрид — 19/25, то есть слияние с равными весами ХУЖЕ
    одной плотной ветки. Причина в том, что лексика ранжирует заметно слабее
    (14/25 первым), а RRF отдаёт её первому месту тот же вклад. Поэтому ветки
    неравноправны: порядок задаёт вектор, лексика добавляет полноту.
    """
    if weights is None:
        weights = BRANCH_WEIGHTS
    fused = {}
    for branch_index, rows in enumerate(branches):
        weight = weights[branch_index] if branch_index < len(weights) else 1.0
        for rank, row in enumerate(rows, start=1):
            key = row['chunk_id']
            entry = fused.setdefault(key, {**row, 'rrf': 0.0, 'found_by': []})
            entry['rrf'] += weight / (RRF_K + rank)
            entry['found_by'].append(branch_index)
            # Счёт и близость приходят из разных ветвей — сохраняем оба, если есть.
            for field in ('score', 'similarity'):
                if field in row and row[field] is not None:
                    entry[field] = row[field]

    ordered = sorted(fused.values(),
                     key=lambda item: (-item['rrf'], item['article_id'],
                                       item['chunk_idx']))
    out, per_article_count = [], {}
    for item in ordered:
        seen = per_article_count.get(item['article_id'], 0)
        if seen >= per_article:
            continue
        per_article_count[item['article_id']] = seen + 1
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _cap_and_limit(rows, limit, per_article):
    out, per_article_count = [], {}
    for row in rows:
        seen = per_article_count.get(row['article_id'], 0)
        if seen >= per_article:
            continue
        per_article_count[row['article_id']] = seen + 1
        out.append(row)
        if len(out) >= limit:
            break
    return out


def fuse(lexical, dense, *, limit=8, per_article=3):
    """Слияние ветвей: порядок задаёт вектор, лексика ДОБИРАЕТ пропущенное.

    Не RRF — и это вывод замера, а не вкусовщина. На боевом корпусе (29 вопросов,
    202 куска) по-русски первым результатом: одна плотная ветка 22/25, равновесный
    RRF 19/25, RRF с любым весом лексики от 0,2 до 0,5 — 20/25. Полнота (в топ-6)
    у всех вариантов одинаковая, 25/25.

    Механика провала RRF здесь такая. При RRF_K = 60 разница вкладов первого и
    второго места одной ветки — 1/61 - 1/62 = 0,00003, а вклад лексического
    первого места даже с весом 0,2 — 0,0033, то есть в сто раз больше. Лексика
    перестаёт быть дополнением и становится решающим тайбрейкером, переставляя
    верх плотной выдачи. Уменьшать RRF_K значит превращать слияние в «кто первый»,
    что тоже не слияние.

    Поэтому ветки неравноправны по построению: вектор отвечает за порядок, лексика
    — только за полноту. Она нужна не на этом наборе вопросов, а на том, чего
    набор не проверяет: точные термины, номера тарифов и пунктов. Диагностика это
    показала — запрос «термопакет» находится лексикой как strict-совпадение.
    """
    dense_ids = {row['chunk_id'] for row in dense}
    extra = [row for row in lexical if row['chunk_id'] not in dense_ids]
    merged = []
    for branch_index, rows in ((1, dense), (0, extra)):
        for row in rows:
            merged.append({**row, 'found_by': [branch_index]})
    # Пометим куски, найденные обеими ветвями: полезно в витрине и в журнале.
    lexical_ids = {row['chunk_id'] for row in lexical}
    # strict_hit приходит только из лексической ветки, а в слиянии верх занимает
    # плотная — без переноса признак терялся ровно на самых точных попаданиях:
    # кусок, найденный обеими ветками, выглядел бы как «только вектор».
    strict_ids = {row['chunk_id'] for row in lexical if row.get('strict_hit')}
    for row in merged:
        if row['chunk_id'] in lexical_ids and row['chunk_id'] in dense_ids:
            row['found_by'] = [0, 1]
        if row['chunk_id'] in strict_ids:
            row['strict_hit'] = True
    return _cap_and_limit(merged, limit, per_article)


def search_hybrid(cursor, *, article_ids, query, query_vector=None,
                  limit=8, per_article=3, candidates=24):
    """Гибрид: лексика + вектор, слияние RRF, ограничение на статью.

    query_vector=None — вектор посчитать не удалось (нет ключа, провайдер лежит,
    расширение не установлено). Тогда работает одна лексика: помощник хуже, но
    жив. Молча деградировать нельзя — вызывающий видит это по полю branches.
    """
    lexical = search_chunks(cursor, article_ids=article_ids, query=query,
                            limit=candidates, per_article=per_article)
    dense = []
    if query_vector:
        dense = search_dense(cursor, article_ids=article_ids,
                             query_vector=query_vector, limit=candidates)

    rows = fuse(lexical, dense, limit=limit, per_article=per_article)
    # degraded — про НЕДОСТУПНОСТЬ плотной ветки, а не про её пустую выдачу.
    # Сначала здесь стояло `not dense`, и на проде это дало ложную тревогу:
    # честный отказ («сколько мне отпускных» — ни один кусок не прошёл порог)
    # помечался как «поиск без векторов», и админ решил бы, что эмбеддинги
    # сломаны. Пустая плотная выдача — нормальный результат, отсутствие вектора —
    # нет.
    return {'rows': rows,
            'branches': {'lexical': len(lexical), 'dense': len(dense)},
            'degraded': query_vector is None}


def search_chunks(cursor, *, article_ids, query, limit=8, per_article=3):
    """Куски из периметра, отсортированные по релевантности запросу.

    article_ids — уже посчитанный периметр помощника (wiki.perimeter). Пустой
    периметр до базы не доходит: у человека без доступа искать нечего, и лишний
    запрос тут только маскировал бы ошибку конфигурации.

    per_article ограничивает вклад одной статьи. Без него «Часто Задаваемые
    Вопросы» (28 кусков) вытеснила бы из выдачи все остальные статьи, и ответ
    строился бы по одному источнику вместо нескольких.
    """
    ids = sorted({int(x) for x in (article_ids or ())})
    if not ids:
        return []

    variants = query_variants(query)
    if not variants:
        return []

    cursor.execute(_SEARCH_CHUNKS_SQL, {
        'variants': variants,
        'article_ids': ids,
        'limit': int(limit),
        'per_article': int(per_article),
    })
    columns = ('chunk_id', 'article_id', 'title', 'slug', 'chunk_idx',
               'heading_path', 'text', 'requires_ack', 'score', 'strict_hit')
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
