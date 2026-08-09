"""Поиск по статьям: полнотекстовый + триграммы + алиасы.

Что воспроизводим из оригинала (по services/meilisearch.ts):
  * приоритет полей — заголовок выше описания, описание выше текста;
  * устойчивость к опечаткам;
  * поиск по мере ввода, с двух символов;
  * подсветка найденного в сниппете;
  * фильтр по разделу;
  * СЛИЯНИЕ выдачи по всем написаниям запроса и ступенчатое ранжирование.

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

ТРИ ИСПРАВЛЕНИЯ ПРОТИВ ПЕРВОЙ ВЕРСИИ ПОРТА (все воспроизведены на боевой базе).

1. Варианты написания СЛИВАЮТСЯ, а не перебираются до первого удачного.
   Раньше цикл возвращал выдачу первого варианта, давшего строки, — и «hyundai
   solaris» отдавал результат варианта «хендай», ни разу не попробовав
   «solaris». Заодно промах стоил до восьми последовательных запросов с
   ts_headline; теперь запрос всегда один (плюс редкий добор по телу, см. ниже).

2. Ранжирование СТУПЕНЧАТОЕ, как getRelevanceRank оригинала, а не линейная
   сумма. Сумма складывала величины разной природы: ts_rank_cd растёт с числом
   вхождений и перебивал вес A заголовка (0.4 у попадания в заголовок против
   12.0 у 120 вхождений в теле), а на опечатках вклад триграмм у всех строк
   слипался и порядок решали просмотры — по запросу «номера телефонав» статья
   «Номера телефонов» оказывалась ВТОРОЙ. Ступень решает первой, сумма — только
   внутри ступени.

3. Многословный запрос ДЕГРАДИРУЕТ, а не проваливается. websearch_to_tsquery и
   префиксный tsquery склеивают слова через AND, поэтому одно лишнее или
   опечатанное слово обнуляло выдачу: «срез занний» не находил «Положение о
   проведении ежемесячного среза знаний». У Meilisearch на этот случай
   matchingStrategy='last' — слова отбрасываются с конца. Наш эквивалент —
   третий, OR-овый tsquery: он даёт совпадение по подмножеству слов, но всегда
   на последней ступени, ниже любого честного попадания.
"""

import re

from . import text as wiki_text

# Длиннее этого запрос не несёт смысла: восемь слов уже уходят в префиксный
# tsquery, остальное — только работа парсеру.
MAX_QUERY_CHARS = 200

# Свёртка ё на СТОРОНЕ СТАТЬИ. Полнотекстовая ветка сворачивает ё сама (колонка
# search_vector построена по translate'нутым текстам, см. schema.py), а вот
# триграммной ветке этого никто не делал — и «учет» не дотягивал до порога по
# «Учёт рабочего времени»: word_similarity 0.400 против 1.000 после свёртки.
# lower() уже привёл Ё к ё, поэтому translate достаточно строчной буквы.
_TITLE = "translate(lower(a.title), 'ё', 'е')"
_ALIASES = "translate(lower(coalesce(a.search_aliases, '')), 'ё', 'е')"

# Веса ts_rank_cd задаются массивом {D, C, B, A}. Это даёт дешёвую проверку
# «совпало именно в заголовке / в алиасах / в описании» по УЖЕ посчитанному
# tsvector — без to_tsvector(a.title) на каждую строку и каждый вариант.
_W_TITLE = "'{0,0,0,1}'::float4[]"
_W_ALIAS = "'{0,0,1,0}'::float4[]"
_W_SUMMARY = "'{0,1,0,0}'::float4[]"

_TRIGRAM_THRESHOLD = '0.45'

# Триграммного слоя ПО ТЕЛУ статьи здесь нет, и это измеренное решение.
# Прогон 15 реальных опечаток по боевой базе: 13 из них ловятся уже первым
# проходом — потому что search_aliases несёт нормализованные заголовок,
# описание и теги, и триграммы идут по ним. Второй проход по телу сработал
# дважды, причём один раз мусором («комиссея» -> шесть посторонних статей).
# Цена — до 50 мс на каждом промахе и пересборка таблицы под колонку.
# Не окупается: слой убран, порог опечаток остался один.

# Разделитель фрагментов ts_headline. Многоточие для этого не годится — оно
# встречается в самих статьях, и фрагменты было бы не разделить обратно.
FRAGMENT_SEPARATOR = '@@F@@'

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
#
# tsq_mark — запрос ДЛЯ ПОДСВЕТКИ, отдельный от поисковых: он OR-овый и
# префиксный, поэтому подсвечивает любое из набранных слов и целиком
# («инструк» подсветит «Инструкция»), тогда как AND-форма на частичном
# совпадении не подсветила бы ничего.
_SEARCH_SQL = """
WITH q AS (
    SELECT websearch_to_tsquery('russian', translate(v.txt, 'ёЁ', 'еЕ'))          AS tsq,
           CASE WHEN v.pref = '' THEN NULL
                ELSE to_tsquery('russian', v.pref) END                            AS tsq_prefix,
           CASE WHEN v.loose = '' THEN NULL
                ELSE to_tsquery('russian', v.loose) END                           AS tsq_loose,
           CASE WHEN v.loose = ''
                THEN websearch_to_tsquery('russian', translate(v.txt, 'ёЁ', 'еЕ'))
                ELSE to_tsquery('russian', v.loose) END                           AS tsq_mark,
           lower(translate(v.txt, 'ёЁ', 'еЕ'))                                    AS raw
      FROM unnest(%(variants)s::text[], %(prefixes)s::text[], %(looses)s::text[])
           AS v(txt, pref, loose)
),
hit AS (
    SELECT DISTINCT ON (a.id)
           a.id, a.slug, a.title, a.summary, a.status, a.views, a.updated_at,
           a.content_plain, q.tsq_mark,
           {tier_expr}  AS tier,
           {score_expr} AS score,
           ts_rank_cd(a.search_vector, q.tsq) AS rank_fts,
           {similarity_expr}                  AS rank_trgm
      FROM wiki_articles a, q
     WHERE a.id = ANY(%(ids)s)
       AND (%(section)s::int IS NULL
            OR EXISTS (SELECT 1 FROM wiki_article_sections s
                        WHERE s.article_id = a.id AND s.section_id = %(section)s::int))
       AND (
            a.search_vector @@ q.tsq
            OR (q.tsq_prefix IS NOT NULL AND a.search_vector @@ q.tsq_prefix)
            OR (q.tsq_loose IS NOT NULL AND a.search_vector @@ q.tsq_loose)
            {trigram_predicate}
       )
     ORDER BY a.id, tier ASC, score DESC
),
top AS (
    SELECT * FROM hit ORDER BY tier ASC, score DESC, views DESC LIMIT %(limit)s
)
SELECT id, slug, title, summary, status, views, updated_at, rank_fts, rank_trgm,
       ts_headline('russian',
                   concat_ws(' … ', title, coalesce(summary, ''), coalesce(content_plain, '')),
                   tsq_mark,
                   'MaxFragments=3, MaxWords=26, MinWords=10, '
                   'StartSel=<mark>, StopSel=</mark>, FragmentDelimiter="@@F@@"') AS snippet
  FROM top
 ORDER BY tier ASC, score DESC, views DESC
"""

# Ступени повторяют getRelevanceRank оригинала: точный заголовок > заголовок >
# префикс/опечатка в заголовке > алиасы > описание > тело > деградация по OR.
# Внутри ступени порядок решает score, и только потом — просмотры.
_TIER_FTS = [
    ("{title} = q.raw", 1),
    ("ts_rank_cd({w_title}, a.search_vector, q.tsq) > 0", 2),
    ("q.tsq_prefix IS NOT NULL"
     " AND ts_rank_cd({w_title}, a.search_vector, q.tsq_prefix) > 0", 3),
    ("ts_rank_cd({w_alias}, a.search_vector, q.tsq) > 0", 4),
    ("q.tsq_prefix IS NOT NULL"
     " AND ts_rank_cd({w_alias}, a.search_vector, q.tsq_prefix) > 0", 4),
    ("ts_rank_cd({w_summary}, a.search_vector, q.tsq) > 0", 5),
    ("a.search_vector @@ q.tsq", 6),
    ("q.tsq_prefix IS NOT NULL AND a.search_vector @@ q.tsq_prefix", 6),
]
_TIER_TRIGRAM = [
    ("word_similarity(q.raw, {title}) >= {thr}", 3),
    ("word_similarity(q.raw, {aliases}) >= {thr}", 4),
]
_TIER_FALLBACK = 8

_KEYS = ('id', 'slug', 'title', 'summary', 'status', 'views', 'updated_at',
         'rank_fts', 'rank_trgm', 'snippet')

# Буквы и цифры, из которых собирается префиксный tsquery. Всё прочее
# (операторы tsquery, кавычки, дефисы) отбрасывается — слово из букв и цифр
# не способно сломать to_tsquery синтаксически.
_PREFIX_WORD = re.compile(r'[a-zа-яеәғқңөұүһі0-9]+')


def _fill(template):
    """Подстановка имён колонок в кусочки условий.

    Отдельной функцией, а не через str.format у самого SQL-шаблона: в весовых
    массивах ts_rank_cd есть фигурные скобки ('{0,0,0,1}'), и format их бы
    съел. Подставляем до сборки шаблона — в аргументы format Python уже
    не заглядывает.
    """
    return (template
            .replace('{title}', _TITLE)
            .replace('{aliases}', _ALIASES)
            .replace('{w_title}', _W_TITLE)
            .replace('{w_alias}', _W_ALIAS)
            .replace('{w_summary}', _W_SUMMARY)
            .replace('{thr}', _TRIGRAM_THRESHOLD))


def prefix_tsquery(variant, joiner=' & '):
    """«инструк по возв» -> «инструк:* & возв:*» (стоп-слова выкинет Postgres).

    С joiner=' | ' получается OR-форма — та самая деградация многословного
    запроса: находит по подмножеству слов, когда AND не сложился.

    Однобуквенные слова не берём: префикс «а:*» совпадает со всем подряд.
    """
    lowered = str(variant or '').lower().replace('ё', 'е')
    words = [w for w in _PREFIX_WORD.findall(lowered) if len(w) >= 2]
    return joiner.join(word + ':*' for word in words[:8])


def build_sql(with_trigram=True):
    """Текст запроса. Без pg_trgm остаётся чистый полнотекстовый поиск."""
    steps = list(_TIER_FTS)
    if with_trigram:
        steps += _TIER_TRIGRAM
    steps.sort(key=lambda pair: pair[1])

    tier_expr = 'CASE\n' + '\n'.join(
        '             WHEN %s THEN %d' % (_fill(cond), tier) for cond, tier in steps
    ) + '\n             ELSE %d\n           END' % _TIER_FALLBACK

    if with_trigram:
        columns = (_TITLE, _ALIASES)
        similarity_expr = 'GREATEST(%s)' % ', '.join(
            'word_similarity(q.raw, %s)' % column for column in columns)
        trigram_predicate = '\n            '.join(
            'OR word_similarity(q.raw, %s) >= %s' % (column, _TRIGRAM_THRESHOLD)
            for column in columns)
        # Сходство с ЦЕЛЫМ заголовком — отдельным слагаемым, а не внутри
        # GREATEST: именно оно отличает «Номера телефонов» (0.70) от
        # «Инструкция по смене номера телефона» (0.33) при запросе с опечаткой,
        # у которых пословное сходство почти одинаково (0.82 против 0.88).
        whole_title = ' + similarity(%s, q.raw) * 0.5' % _TITLE
    else:
        similarity_expr = '0::real'
        trigram_predicate = ''
        whole_title = ''

    # Нормализация 32 (rank/(rank+1)) гасит длину документа: без неё длинная
    # статья с частым словом всегда впереди короткой профильной.
    score_expr = (
        'ts_rank_cd(a.search_vector, q.tsq, 32) * 3'
        ' + COALESCE(ts_rank_cd(a.search_vector, q.tsq_prefix, 32), 0) * 2'
        ' + COALESCE(ts_rank_cd(a.search_vector, q.tsq_loose, 32), 0) * 0.5'
        ' + ' + similarity_expr + whole_title
    )

    return _SEARCH_SQL.format(
        tier_expr=tier_expr,
        score_expr=score_expr,
        similarity_expr=similarity_expr,
        trigram_predicate=trigram_predicate,
    )


def split_snippet(raw):
    """Фрагменты ts_headline -> только те, где реально есть подсветка.

    Без этой чистки ts_headline на промахе возвращает НАЧАЛО документа: по
    запросу «смз» статья «Адреса офисов» показывала «Офисы Яндекса для
    водителей Город Адрес Время работы Алматы БЦ» — текст без отношения к
    запросу. Фронт рисовал его как найденный фрагмент, а слово для подсветки
    в открытой статье бралось уже из сырого запроса и не совпадало с показанным.
    Оригинал этой болезни не имел: extractHighlights собирал сниппеты только из
    блоков, где <mark> действительно есть.
    """
    parts = [part.strip() for part in str(raw or '').split(FRAGMENT_SEPARATOR)]
    return [part for part in parts if '<mark>' in part]


def _rows_to_items(cursor):
    """Строки курсора -> элементы выдачи с массивом highlights.

    highlights — то же поле, что отдавал оригинал (до трёх фрагментов): фронт
    разворачивает его в секцию «Совпадения в тексте». snippet остаётся
    одиночной строкой ради тех мест, что читают только его.
    """
    items = []
    for row in cursor.fetchall():
        item = dict(zip(_KEYS, row))
        fragments = split_snippet(item['snippet'])
        item['highlights'] = fragments
        item['snippet'] = fragments[0] if fragments else ''
        items.append(item)
    return items


def _run(cursor, sql, ids, variants, section_id, limit):
    cursor.execute(sql, {
        'ids': ids,
        'variants': variants,
        'prefixes': [prefix_tsquery(v) for v in variants],
        'looses': [prefix_tsquery(v, ' | ') for v in variants],
        'section': section_id,
        'limit': limit,
    })
    return _rows_to_items(cursor)


def search(cursor, visible_ids, query, *, section_id=None, limit=20, with_trigram=True):
    """Поиск в границах периметра пользователя.

    Запрос прогоняется по всем вариантам написания сразу: исходный,
    транслитерация, исправленная раскладка, фраза с подставленными алиасами и
    сами алиасы. Варианты разворачиваются в CTE через unnest, выдача
    дедуплицируется по статье (DISTINCT ON) с сохранением лучшей ступени — то
    же слияние, что делал оригинал четырьмя параллельными обращениями к движку,
    только одним запросом вместо четырёх.

    Опечатки ловит триграммный слой по заголовку и алиасам. Алиасы несут
    нормализованные заголовок, описание и теги статьи, поэтому слой накрывает
    почти всё, по чему ищут: на боевой базе 13 опечаток из 15 находятся именно
    так (см. комментарий о том, почему отдельного слоя по телу нет).
    """
    if not visible_ids or not str(query or '').strip():
        return []

    trimmed = str(query).strip()[:MAX_QUERY_CHARS]
    variants = [v for v in wiki_text.query_variants(trimmed) if len(v) >= 2]
    if not variants:
        return []

    ids = list(visible_ids)
    return _run(cursor, build_sql(with_trigram), ids, variants, section_id, limit)


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
