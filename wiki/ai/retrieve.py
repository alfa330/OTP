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

from ..text import query_variants, sql_fold

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
    SELECT DISTINCT translate(v, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') AS txt
      FROM unnest(%(variants)s::text[]) AS v
     WHERE btrim(v) <> ''
),
q AS (
    SELECT v.txt,
           websearch_to_tsquery('russian', translate(v.txt, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')) AS tsq,
           (SELECT string_agg(quote_literal(lex), ' | ')
              FROM unnest(tsvector_to_array(to_tsvector('russian', translate(v.txt, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')))) AS lex
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
           unnest(tsvector_to_array(to_tsvector('russian', translate(v.txt, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')))) AS lex
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
       r.text, r.requires_ack, r.score, r.strict_hit, a.historical
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
       c.text, c.requires_ack, a.historical,
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


# ─────────────────────────────────────────────────────────────────────────────
# ТРЕТЬЯ ВЕТКА: ОДНА ПЕРЕПУТАННАЯ БУКВА В ИМЕНИ СОБСТВЕННОМ
#
# Ни лексика, ни вектор не переживают опечатку в редком названии, а голосовой
# наставник получает вопрос ИЗ РАСПОЗНАВАНИЯ РЕЧИ, где такая опечатка —
# норма. Замер на проде 23.08.2026, акция записана как «Лимонопад»:
#
#   вопрос                                 лексика   вектор   целевой кусок
#   «расскажи про акцию Лимонопад»          17 кусков   —      ПЕРВЫМ, счёт 5,7
#   «расскажи про акцию Лимонапад»          17 кусков   0      НЕ НАЙДЕН
#
# Разница — одна гласная (её и отдало распознавание). В куске лежит лексема
# «лимонопад», в запросе «лимонапад»: это разные лексемы, лексика молчит, а
# вектор не дотягивает до порога 0,68, потому что имя занимает одну строку в
# табличном куске на 1430 знаков. Наставник ответил «в доступных мне статьях
# нет информации об акции Лимонапад» — то есть УВЕРЕННО отрицал то, что лежит
# в вике первым результатом обычного поиска.
#
# Лечится триграммами, ровно как опечатки в поиске статей (wiki/search.py), но
# по ТЕЛУ куска, а не по заголовку: названия акций живут строками таблицы.
#
# Отбор слов — два предиката, и оба обязательны:
#
#   1. Слова, КОТОРЫХ ВИКА НЕ ЗНАЕТ ЛЕКСИКОЙ. Известное слово добирать нечего:
#      его уже нашла лексическая ветка, а триграммы приделали бы к нему соседей
#      («водитель» похож на 136 кусков из 295).
#   2. ВСЯ выдача ветки лежит в ОДНОЙ статье. Это тот же признак имени
#      собственного, что уже стоит в answer.named_term, и он же — граница между
#      находкой и мусором. Замер по всей вике, порог 0,45:
#
#        лимонапад → 1 кусок, 1 статья     мотобайга  → 1 кусок, 1 статья
#        тирмопакет → 1 кусок, 1 статья    кубокпро   → 1 кусок, 1 статья
#        напомни   → 4 куска, 2 статьи     интересует → 6 кусков, 4 статьи
#        страхавание → 22 куска, 4 статьи  бренирование → 25 кусков, 8 статей
#
#      Верхние четыре — искажённые названия, нижние четыре — общие слова, к
#      которым добор не нужен. Одна статья отделяет их без исключений, а
#      счётчик кусков — нет («донгелек» лежит в 8 кусках ОДНОЙ статьи).
#
#      Правило именно на ВСЮ выдачу, а не на каждое слово отдельно, и это цена
#      ошибки: сначала оно стояло на слово, и казахский вопрос «жолаушы затын
#      салонда қалдырды» (в вике его находит вектор, лексика — ничего) собрал
#      три посторонних куска из двух статей, по одному на слово. Такому вопросу
#      триграммы не помощник: незнакомых слов полфразы, и похоже в них всё на
#      всё. Требование одной статьи гасит это без списка исключений.
#
# Чего ветка НЕ лечит (проверено там же): «Жетқызыны» вместо «Жеті қазына» —
# два слова, слипшихся с искажением, триграммная близость 0,40 против 0,45.
# Понижать порог нельзя: на 0,35 первым выходит уже посторонний кусок. Слитое
# КАЗАХСКОЕ числительное разводит text.split_glued_numeral, но «жет» вместо
# «жеті» ему не по силам.
#
# ЦЕНА. Полный проход по кускам: 94 мс на корпусе из 295 кусков (198 тыс.
# знаков), против 37 мс у лексической ветки — замер EXPLAIN ANALYZE на проде
# 23.08.2026. Платится она ТОЛЬКО когда в вопросе есть слово, которого вика не
# знает: иначе внешняя часть соединения пуста и запрос сходит за единицы
# миллисекунд. Индекса под word_similarity здесь нет намеренно — он потребовал
# бы оператора <% и правки pg_trgm.word_similarity_threshold через SET LOCAL,
# то есть скрытого состояния транзакции. Когда корпус вырастет до тысяч кусков,
# ставить надо именно его (GIN gin_trgm_ops по свёрнутому тексту).
FUZZY_THRESHOLD = 0.45
# Короткие слова не берём: у слова из пяти букв триграмм слишком мало, и
# близость 0,45 набирает половина корпуса.
FUZZY_MIN_WORD = 6
# Сколько кусков ветка приносит максимум. Найденным случаям хватает одного;
# потолок стоит, чтобы имя, размазанное по статье (8 кусков со словом
# «Донгелек»), не выело весь контекст.
FUZZY_LIMIT = 3

_FUZZY_CHUNKS_SQL = """
WITH words AS (
    SELECT DISTINCT {fold_word} AS w
      FROM unnest(%(words)s::text[]) AS raw
),
unknown AS (
    SELECT w.w
      FROM words w
     WHERE plainto_tsquery('russian', w.w)::text <> ''
       AND NOT EXISTS (SELECT 1
                         FROM wiki_ai_chunks c
                        WHERE c.article_id = ANY(%(article_ids)s)
                          AND c.chunk_tsv @@ plainto_tsquery('russian', w.w))
),
near AS (
    SELECT * FROM (
        SELECT u.w, c.id, c.article_id,
               word_similarity(u.w, {fold_chunk}) AS wsim
          FROM unknown u
          JOIN wiki_ai_chunks c ON c.article_id = ANY(%(article_ids)s)
    ) scored
     WHERE wsim >= %(threshold)s
),
scope AS (
    SELECT count(DISTINCT article_id) AS articles FROM near
)
SELECT c.id, c.article_id, a.title, a.slug, c.chunk_idx, c.heading_path,
       c.text, c.requires_ack, a.historical, max(n.wsim) AS wsim
  FROM near n
  JOIN wiki_ai_chunks c ON c.id = n.id
  JOIN wiki_articles a ON a.id = c.article_id
 WHERE (SELECT articles FROM scope) = 1
 GROUP BY c.id, c.article_id, a.title, a.slug, c.chunk_idx, c.heading_path,
          c.text, c.requires_ack, a.historical
 ORDER BY max(n.wsim) DESC, c.article_id, c.chunk_idx
 LIMIT %(limit)s
""".format(fold_word=sql_fold('lower(raw)'),
           fold_chunk=sql_fold("lower(coalesce(c.heading_path, '') || ' ' || c.text)"))

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
               'heading_path', 'text', 'requires_ack', 'historical', 'similarity')
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def search_fuzzy(cursor, *, article_ids, query, limit=FUZZY_LIMIT,
                 threshold=FUZZY_THRESHOLD, min_word=FUZZY_MIN_WORD):
    """Куски, похожие на редкое слово вопроса, которого вика не знает.

    Зачем и почему отбор именно такой — в шапке у FUZZY_THRESHOLD.

    Без pg_trgm ветка молчит, как и триграммная часть поиска статей. Проверка
    идёт ДО обращения к word_similarity и БЕЗ кеша: запрос к pg_extension стоит
    доли миллисекунды на уже открытом соединении, а неудачный вызов
    несуществующей функции отравил бы транзакцию роута целиком (autocommit тут
    нет, см. database._get_cursor).
    """
    ids = sorted({int(x) for x in (article_ids or ())})
    # Слова разбираем ТЕМ ЖЕ разбором, что и гейт уточнения: две реализации
    # «слов вопроса» рано или поздно расходятся, и расхождение будет молчаливым.
    from .answer import meaningful_words

    words = sorted({word for word in meaningful_words(query)
                    if len(word) >= int(min_word)})
    if not ids or not words:
        return []
    from ..schema import trigram_available

    if not trigram_available(cursor):
        return []

    cursor.execute(_FUZZY_CHUNKS_SQL, {
        'words': words,
        'article_ids': ids,
        'threshold': float(threshold),
        'limit': int(limit),
    })
    columns = ('chunk_id', 'article_id', 'title', 'slug', 'chunk_idx',
               'heading_path', 'text', 'requires_ack', 'historical', 'fuzzy')
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in rows:
        row['fuzzy'] = float(row['fuzzy'])
        # Человек назвал вещь своим именем, ошибившись буквой. Для гейта
        # уточнения это то же самое, что точное совпадение термина.
        row['fuzzy_hit'] = True
    return rows


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


def fuse(lexical, dense, fuzzy=(), *, limit=8, per_article=3):
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

    ТРИГРАММНАЯ ВЕТКА ИДЁТ ПЕРВОЙ, впереди вектора. Она срабатывает только
    когда человек НАЗВАЛ вещь по имени, ошибившись буквой (отбор — в шапке у
    FUZZY_THRESHOLD), и кусок с этим именем обязан дойти до контекста при любой
    выдаче соседних ветвей. Иначе правка была бы половинчатой: на проде
    23.08.2026 тот же вопрос про «Лимонапад» во второй раз пришёл при полной
    плотной выдаче (21 кусок), и добор без гарантированного места вытеснился бы
    ею целиком. Мусора отсюда не приходит: ветка либо молчит, либо приносит
    один-три куска ОДНОЙ статьи.
    """
    fuzzy_ids = {row['chunk_id'] for row in fuzzy}
    dense_ids = {row['chunk_id'] for row in dense}
    lexical_ids = {row['chunk_id'] for row in lexical}
    seen = set(fuzzy_ids)
    merged = []
    for branch_index, rows in ((2, fuzzy), (1, dense), (0, lexical)):
        for row in rows:
            if branch_index != 2 and row['chunk_id'] in seen:
                continue
            seen.add(row['chunk_id'])
            merged.append({**row, 'found_by': [branch_index]})
    # Пометим куски, найденные несколькими ветвями: полезно в витрине и в журнале.
    membership = ((0, lexical_ids), (1, dense_ids), (2, fuzzy_ids))
    # strict_hit приходит только из лексической ветки, а в слиянии верх занимает
    # плотная — без переноса признак терялся ровно на самых точных попаданиях:
    # кусок, найденный обеими ветками, выглядел бы как «только вектор».
    strict_ids = {row['chunk_id'] for row in lexical if row.get('strict_hit')}
    for row in merged:
        found_by = [index for index, ids in membership if row['chunk_id'] in ids]
        if len(found_by) > 1:
            row['found_by'] = found_by
        if row['chunk_id'] in strict_ids:
            row['strict_hit'] = True
    return _cap_and_limit(merged, limit, per_article)


def search_hybrid(cursor, *, article_ids, query, query_vector=None,
                  limit=8, per_article=3, candidates=24):
    """Гибрид: лексика + вектор + триграммы, слияние, ограничение на статью.

    query_vector=None — вектор посчитать не удалось (нет ключа, провайдер лежит,
    расширение не установлено). Тогда работает одна лексика: помощник хуже, но
    жив. Молча деградировать нельзя — вызывающий видит это по полю branches.

    Третья ветка (search_fuzzy) вступает только на редком слове, которого вика
    не знает: она вытаскивает имя собственное, названное с ошибкой в букве, —
    ровно то, что приносит распознавание речи. Её вклад тоже виден в branches.
    """
    lexical = search_chunks(cursor, article_ids=article_ids, query=query,
                            limit=candidates, per_article=per_article)
    dense = []
    if query_vector:
        dense = search_dense(cursor, article_ids=article_ids,
                             query_vector=query_vector, limit=candidates)
    fuzzy = search_fuzzy(cursor, article_ids=article_ids, query=query)

    rows = fuse(lexical, dense, fuzzy, limit=limit, per_article=per_article)
    # degraded — про НЕДОСТУПНОСТЬ плотной ветки, а не про её пустую выдачу.
    # Сначала здесь стояло `not dense`, и на проде это дало ложную тревогу:
    # честный отказ («сколько мне отпускных» — ни один кусок не прошёл порог)
    # помечался как «поиск без векторов», и админ решил бы, что эмбеддинги
    # сломаны. Пустая плотная выдача — нормальный результат, отсутствие вектора —
    # нет.
    return {'rows': rows,
            'branches': {'lexical': len(lexical), 'dense': len(dense),
                         'fuzzy': len(fuzzy)},
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
               'heading_path', 'text', 'requires_ack', 'score', 'strict_hit',
               'historical')
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
