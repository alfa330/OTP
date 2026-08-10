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
