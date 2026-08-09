"""Поиск по статьям: полнотекстовый + триграммы + алиасы.

Что воспроизводим из оригинала (по services/meilisearch.ts):
  * приоритет полей — заголовок выше описания, описание выше текста;
  * устойчивость к опечаткам;
  * поиск по мере ввода, с двух символов;
  * подсветка найденного в сниппете;
  * фильтр по разделу.

Чего в оригинале НЕ было, вопреки ожиданиям: синонимов Meilisearch, стоп-слов,
кастомных правил ранжирования и фасетов — ничего из этого там не настроено.
Вся «умность» жила в utils/text.ts, и она перенесена целиком (wiki/text.py).

Устройство: tsvector-колонка даёт ранжирование по весам, pg_trgm — опечатки и
префикс. Если pg_trgm недоступен по правам, триграммная часть просто
выключается, а полнотекстовая продолжает работать.

«Поиск по мере ввода» держится на двух ногах, и обе обязательны:
  * префиксный tsquery («инструк:*») — websearch_to_tsquery его не умеет,
    а стемминг обрезает «инструкция» до «инструкц», поэтому частично набранное
    слово без ':*' не находит НИЧЕГО;
  * word_similarity вместо сходства с целым заголовком: у «инструк» против
    «Инструкция по возвратам водителей» полное сходство ~0.15 и порог 0.3
    не проходился — сравнивать надо с лучшим словом заголовка, а не со строкой.
"""

import re

from . import text as wiki_text

# ВАЖНО: выдача всегда пересекается с множеством видимых статей (%(ids)s).
# Подсказки поиска — такой же читающий путь, как список: без этого фильтра
# закрытая статья утекла бы заголовком.
#
# translate(ё -> е) с обеих сторон: колонка search_vector строится по
# текстам со свёрнутой ё (см. schema.py), конфигурация 'russian' сама
# ё и е НЕ склеивает — «отчет» без этого не находил «отчёт» в теле статьи.
#
# CASE вместо голого to_tsquery: пустая строка в to_tsquery — синтаксическая
# ошибка, а CASE гарантирует ленивое вычисление ветки.
_SEARCH_SQL = """
WITH q AS (
    SELECT websearch_to_tsquery('russian', translate(%(query)s, 'ёЁ', 'еЕ')) AS tsq,
           CASE WHEN %(prefix)s = ''
                THEN NULL
                ELSE to_tsquery('russian', %(prefix)s) END                   AS tsq_prefix,
           lower(translate(%(query)s, 'ёЁ', 'еЕ'))                          AS raw
)
SELECT a.id, a.slug, a.title, a.summary, a.status, a.views, a.updated_at,
       ts_rank_cd(a.search_vector, q.tsq) AS rank_fts,
       {similarity_expr}                  AS rank_trgm,
       ts_headline('russian', coalesce(a.content_plain, ''),
                   COALESCE(q.tsq || q.tsq_prefix, q.tsq),
                   'MaxFragments=1, MaxWords=28, MinWords=10, '
                   'StartSel=<mark>, StopSel=</mark>, FragmentDelimiter=" … "') AS snippet
  FROM wiki_articles a, q
 WHERE a.id = ANY(%(ids)s)
   AND (%(section)s::int IS NULL
        OR EXISTS (SELECT 1 FROM wiki_article_sections s
                    WHERE s.article_id = a.id AND s.section_id = %(section)s::int))
   AND (
        a.search_vector @@ q.tsq
        OR (q.tsq_prefix IS NOT NULL AND a.search_vector @@ q.tsq_prefix)
        {trigram_predicate}
   )
 ORDER BY (ts_rank_cd(a.search_vector, q.tsq) * 3
           + COALESCE(ts_rank_cd(a.search_vector, q.tsq_prefix), 0) * 2
           + {similarity_expr}) DESC,
          a.views DESC
 LIMIT %(limit)s
"""

# Функции вместо операторов %/<%: не нужен эскейпинг процентов в psycopg2,
# а порог виден в тексте запроса, а не спрятан в GUC pg_trgm.*_threshold.
_TRIGRAM_SIMILARITY = (
    "GREATEST(similarity(lower(a.title), q.raw), "
    "word_similarity(q.raw, lower(a.title)), "
    "word_similarity(q.raw, lower(coalesce(a.search_aliases, ''))))")
_TRIGRAM_PREDICATE = (
    "OR word_similarity(q.raw, lower(a.title)) >= 0.45 "
    "OR word_similarity(q.raw, lower(coalesce(a.search_aliases, ''))) >= 0.45")

_KEYS = ('id', 'slug', 'title', 'summary', 'status', 'views', 'updated_at',
         'rank_fts', 'rank_trgm', 'snippet')

# Буквы и цифры, из которых собирается префиксный tsquery. Всё прочее
# (операторы tsquery, кавычки, дефисы) отбрасывается — слово из букв и цифр
# не способно сломать to_tsquery синтаксически.
_PREFIX_WORD = re.compile(r'[a-zа-яеәғқңөұүһі0-9]+')


def prefix_tsquery(variant):
    """«инструк по возв» -> «инструк:* & возв:*» (стоп-слова выкинет Postgres).

    Однобуквенные слова не берём: префикс «а:*» совпадает со всем подряд.
    """
    lowered = str(variant or '').lower().replace('ё', 'е')
    words = [w for w in _PREFIX_WORD.findall(lowered) if len(w) >= 2]
    return ' & '.join(word + ':*' for word in words[:8])


def build_sql(with_trigram):
    return _SEARCH_SQL.format(
        similarity_expr=_TRIGRAM_SIMILARITY if with_trigram else '0::real',
        trigram_predicate=_TRIGRAM_PREDICATE if with_trigram else '',
    )


def search(cursor, visible_ids, query, *, section_id=None, limit=20, with_trigram=True):
    """Поиск в границах периметра пользователя.

    Запрос прогоняется по вариантам написания: исходный, транслитерация,
    исправленная раскладка, алиасы. Первый вариант, давший результат, и
    выигрывает — так «хундай» находит статью про Hyundai, а «ntrcn» про текст.

    В оригинале это были параллельные обращения к движку с последующим слиянием;
    здесь варианты перебираются по очереди и почти всегда срабатывает первый,
    потому что алиасы уже лежат в самой статье (search_aliases).
    """
    if not visible_ids or not str(query or '').strip():
        return []

    ids = list(visible_ids)
    sql = build_sql(with_trigram)

    # Срез 8, а не 6: базовых форм (оригинал, нормализация, транслиты,
    # раскладка) бывает до 7, и срез в 6 отрезал бы все алиасные варианты.
    for variant in wiki_text.query_variants(query)[:8]:
        if len(variant) < 2:
            continue
        cursor.execute(sql, {
            'ids': ids, 'query': variant, 'prefix': prefix_tsquery(variant),
            'section': section_id, 'limit': limit,
        })
        rows = cursor.fetchall()
        if rows:
            return [dict(zip(_KEYS, row)) for row in rows]
    return []


def suggest(cursor, visible_ids, query, *, limit=5, with_trigram=True):
    """Подсказки по мере ввода — те же правила, только короче выдача."""
    return search(cursor, visible_ids, query, limit=limit, with_trigram=with_trigram)


def refresh_aliases(cursor, article_id):
    """Пересчитать search_aliases статьи.

    Вызывается при каждом сохранении: в оригинале варианты написания
    вычислялись на КАЖДЫЙ поисковый запрос и превращались в четыре обращения
    к движку. Дешевле посчитать один раз при записи.
    """
    cursor.execute(
        """
        SELECT a.title, a.summary, a.content_plain,
               COALESCE((SELECT array_agg(t.tag_name) FROM wiki_article_tags t
                          WHERE t.article_id = a.id), '{}')
          FROM wiki_articles a WHERE a.id = %s
        """,
        (article_id,),
    )
    row = cursor.fetchone()
    if not row:
        return False

    title, summary, plain, tags = row
    aliases = wiki_text.search_aliases_for_article(
        title, summary or '', list(tags or []), plain or '')
    cursor.execute('UPDATE wiki_articles SET search_aliases = %s WHERE id = %s',
                   (aliases, article_id))
    return True
