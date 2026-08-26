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

from .text import fold_kazakh

from . import text as wiki_text

# Длиннее этого запрос не несёт смысла: восемь слов уже уходят в префиксный
# tsquery, остальное — только работа парсеру.
MAX_QUERY_CHARS = 200

# Свёртка ё на СТОРОНЕ СТАТЬИ. Полнотекстовая ветка сворачивает ё сама (колонка
# search_vector построена по translate'нутым текстам, см. schema.py), а вот
# триграммной ветке этого никто не делал — и «учет» не дотягивал до порога по
# «Учёт рабочего времени»: word_similarity 0.400 против 1.000 после свёртки.
# lower() уже привёл Ё к ё, поэтому translate достаточно строчной буквы.
_TITLE = "translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')"
_ALIASES = "translate(lower(coalesce(a.search_aliases, '')), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')"

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
# Свёртка (ё→е и казахские буквы к русским двойникам, см. wiki/text.py) с обеих
# сторон: колонка search_vector строится по
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
    SELECT websearch_to_tsquery('russian', translate(v.txt, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ'))          AS tsq,
           CASE WHEN v.pref = '' THEN NULL
                ELSE to_tsquery('russian', v.pref) END                            AS tsq_prefix,
           CASE WHEN v.loose = '' THEN NULL
                ELSE to_tsquery('russian', v.loose) END                           AS tsq_loose,
           CASE WHEN v.loose = ''
                THEN websearch_to_tsquery('russian', translate(v.txt, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ'))
                ELSE to_tsquery('russian', v.loose) END                           AS tsq_mark,
           lower(translate(v.txt, 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ'))                                    AS raw
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
       AND (%(article_types)s::text[] IS NULL
            OR a.article_type = ANY(%(article_types)s::text[]))
       AND (%(authors)s::int[] IS NULL OR a.author_id = ANY(%(authors)s::int[]))
       AND (
{match_predicate}
       )
     ORDER BY a.id, tier ASC, score DESC
),
top AS (
    SELECT * FROM hit ORDER BY tier ASC, score DESC, views DESC LIMIT %(limit)s
)
SELECT top.id, top.slug, top.title, top.summary, top.status, top.views,
       top.updated_at, top.rank_fts, top.rank_trgm,
       {snippet_expr} AS snippet,
       -- Запасной отрывок для случая «статья по-казахски, запрос по-русски».
       -- Статья находится (векторы и tsvector свёрнуты), а ts_headline
       -- подсвечивает по ОРИГИНАЛУ — и по запросу «Казына» в тексте с «Қазына»
       -- отрывка не получается вовсе. В выдаче остаётся голый заголовок, что
       -- читается как «не нашлось».
       --
       -- Текст берётся ИЗ ОРИГИНАЛА, а позиция ищется по свёрнутому: свёртка
       -- посимвольная, один к одному, поэтому смещения совпадают. Первая версия
       -- отдавала сам свёрнутый текст — и подменяла в превью казахскую букву
       -- русской, то есть показывала статью не такой, какая она есть. Здесь
       -- подменять нечего: наружу уходит ровно то, что в статье.
       {folded_expr} AS snippet_folded,
       a2.article_type,
       -- Автор — и для фильтра «по создателю», и для подписи в строке выдачи:
       -- выбрав фильтр, человек обязан видеть в результатах, что он сработал.
       -- Имя берётся LEFT JOIN'ом: у части статей автор снят (ON DELETE SET NULL
       -- при удалении учётки), и INNER JOIN потерял бы их из выдачи молча.
       a2.author_id, au.name AS author_name
  FROM top
  JOIN wiki_articles a2 ON a2.id = top.id
  LEFT JOIN users au ON au.id = a2.author_id
{headline_join}
 ORDER BY top.tier ASC, top.score DESC, top.views DESC
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

# ── Область поиска ───────────────────────────────────────────────────────────
#
# MATCH_ALL — как было: совпало где угодно (заголовок, алиасы, описание, тело).
# MATCH_TITLE — «искать только в названиях статей».
#
# Ограничение сделано ВЕСОМ уже посчитанного tsvector, а не отдельным
# to_tsvector(a.title) на каждую строку: колонка search_vector собрана
# setweight'ом (schema.py), заголовок в ней помечен весом A, и ts_rank_cd с
# массивом '{0,0,0,1}' отвечает «совпало ли именно в заголовке» без второй
# индексации текста.
#
# Условие @@ обязано стоять ПЕРВЫМ в каждой скобке и остаться отдельным: только
# оно берётся GIN-индексом idx_wiki_articles_fts. Один ts_rank_cd без него
# заставил бы Postgres прочитать все статьи периметра и посчитать ранг у каждой.
#
# Алиасы (вес B) в область названий НЕ входят, хотя и собраны из заголовка:
# search_aliases_for_article кладёт туда ещё описание, теги и синонимы первых
# двух килобайт ТЕЛА (wiki/text.py) — то есть через вес B в «только названия»
# просочилось бы ровно то, что человек этим фильтром и отсекает. Опечатки и
# транслит от этого не страдают: варианты написания запроса подставляются до
# базы (query_variants), и «хундай» приходит в SQL уже как 'hyundai'.
MATCH_ALL = 'all'
MATCH_TITLE = 'title'
MATCH_SCOPES = (MATCH_ALL, MATCH_TITLE)

_MATCH_PREDICATE = {
    MATCH_ALL: """            a.search_vector @@ q.tsq
            OR (q.tsq_prefix IS NOT NULL AND a.search_vector @@ q.tsq_prefix)
            OR (q.tsq_loose IS NOT NULL AND a.search_vector @@ q.tsq_loose)
            {trigram_predicate}""",
    MATCH_TITLE: """            (a.search_vector @@ q.tsq
                AND ts_rank_cd({w_title}, a.search_vector, q.tsq) > 0)
            OR (q.tsq_prefix IS NOT NULL AND a.search_vector @@ q.tsq_prefix
                AND ts_rank_cd({w_title}, a.search_vector, q.tsq_prefix) > 0)
            OR (q.tsq_loose IS NOT NULL AND a.search_vector @@ q.tsq_loose
                AND ts_rank_cd({w_title}, a.search_vector, q.tsq_loose) > 0)
            {trigram_predicate}""",
}

# Колонки триграммного слоя по областям. В «только названиях» их одна, и это же
# выражение идёт в rank_trgm — иначе сходство с алиасами тянуло бы вверх статью,
# у которой в заголовке искомого слова нет.
_TRIGRAM_COLUMNS = {
    MATCH_ALL: ('{title}', '{aliases}'),
    MATCH_TITLE: ('{title}',),
}

# ── Отрывок с подсветкой ─────────────────────────────────────────────────────
#
# В области названий отрывка НЕТ, и это два решения в одном.
#
# По смыслу: человек попросил искать только в названиях, а секция «Совпадения в
# тексте» показывала бы ему совпадение в тексте — то есть ровно то, что он
# только что отключил. Строка выдачи без отрывка не пустеет: фронт показывает
# описание статьи (см. split_snippet и WikiSearch.jsx).
#
# По цене: ts_headline и LATERAL с position() по content_plain — самая дорогая
# часть запроса, а тела статей в проде доходят до 900 КБ. В суженной области
# они считались бы ради результата, который нельзя показывать.
#
# NULL::text, а не отсутствие колонок: порядок SELECT жёстко связан с _KEYS
# (dict(zip(_KEYS, row))), и выкидывать колонки означало бы держать два разных
# порядка на две области.
_SNIPPET_EXPR = {
    MATCH_ALL: """ts_headline('russian',
                   coalesce(top.content_plain, ''),
                   top.tsq_mark,
                   'MaxFragments=3, MaxWords=26, MinWords=10, '
                   'StartSel=<mark>, StopSel=</mark>, FragmentDelimiter="@@F@@"')""",
    MATCH_TITLE: 'NULL::text',
}

_FOLDED_EXPR = {
    MATCH_ALL: """CASE WHEN m.pos IS NULL THEN NULL ELSE
            substring(a2.content_plain from GREATEST(m.pos - 70, 1)
                      for m.pos - GREATEST(m.pos - 70, 1))
            || '<mark>' || substring(a2.content_plain from m.pos for m.len) || '</mark>'
            || substring(a2.content_plain from m.pos + m.len for 110)
       END""",
    MATCH_TITLE: 'NULL::text',
}

_HEADLINE_JOIN = {
    MATCH_ALL: """  LEFT JOIN LATERAL (
      -- Самое раннее вхождение любого варианта запроса, по свёрнутому тексту.
      SELECT t.pos, t.len
        FROM (
            SELECT position(translate(lower(v), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') IN
                            translate(lower(coalesce(a2.content_plain, '')), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')) AS pos,
                   length(v) AS len
              FROM unnest(%(variants)s::text[]) AS v
             WHERE length(btrim(v)) >= 3
        ) t
       WHERE t.pos > 0
       ORDER BY t.pos
       LIMIT 1
  ) m ON TRUE""",
    MATCH_TITLE: '',
}

_KEYS = ('id', 'slug', 'title', 'summary', 'status', 'views', 'updated_at',
         'rank_fts', 'rank_trgm', 'snippet', 'snippet_folded', 'article_type',
         'author_id', 'author_name')

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
    # Свёртка та же, что в SQL: иначе питоновская ветка нормализации и
    # запрос к базе разошлись бы на казахских буквах.
    lowered = fold_kazakh(str(variant or '').lower())
    words = [w for w in _PREFIX_WORD.findall(lowered) if len(w) >= 2]
    return joiner.join(word + ':*' for word in words[:8])


def normalize_scope(value):
    """Область поиска из запроса: неизвестное значение — как «везде».

    Гасим молча, а не отказом: область — украшение выдачи, и опечатка в адресе
    не повод оставить человека без результатов. То же правило, что у фильтра
    типа в routes_articles._article_types.
    """
    value = str(value or '').strip().lower()
    return value if value in MATCH_SCOPES else MATCH_ALL


def build_sql(with_trigram=True, scope=MATCH_ALL):
    """Текст запроса. Без pg_trgm остаётся чистый полнотекстовый поиск.

    scope=MATCH_TITLE сужает поиск до НАЗВАНИЙ статей: и предикат отбора, и
    ступени, и триграммный слой считаются только по заголовку. Сужать один лишь
    предикат было бы мало — ступени и score продолжали бы считаться по алиасам
    и описанию, и внутри отобранной по заголовку выдачи порядок решала бы
    длина тела статьи.
    """
    scope = normalize_scope(scope)
    title_only = scope == MATCH_TITLE

    steps = list(_TIER_FTS)
    if with_trigram:
        steps += _TIER_TRIGRAM
    if title_only:
        # Ступени по алиасам, описанию и телу в области названий недостижимы:
        # такие строки предикат уже не пропустил. Оставь мы их — CASE
        # присваивал бы ступень 4 статье, попавшей сюда по заголовку, просто
        # потому что слово нашлось и в алиасах, и порядок выдачи перестал бы
        # отличать заголовок от совпадения по описанию.
        steps = [pair for pair in steps if '{title}' in pair[0] or '{w_title}' in pair[0]]
    steps.sort(key=lambda pair: pair[1])

    tier_expr = 'CASE\n' + '\n'.join(
        '             WHEN %s THEN %d' % (_fill(cond), tier) for cond, tier in steps
    ) + '\n             ELSE %d\n           END' % _TIER_FALLBACK

    if with_trigram:
        columns = tuple(_fill(column) for column in _TRIGRAM_COLUMNS[scope])
        similarity_expr = ('word_similarity(q.raw, %s)' % columns[0] if len(columns) == 1
                           else 'GREATEST(%s)' % ', '.join(
                               'word_similarity(q.raw, %s)' % column for column in columns))
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

    # Подстановка replace'ом, а не format'ом, и по той же причине, что в _fill:
    # после _fill в тексте стоят весовые массивы ts_rank_cd ('{0,0,0,1}'), и
    # format сломался бы об их фигурные скобки.
    match_predicate = _fill(_MATCH_PREDICATE[scope]).replace(
        '{trigram_predicate}', trigram_predicate)

    # Нормализация 32 (rank/(rank+1)) гасит длину документа: без неё длинная
    # статья с частым словом всегда впереди короткой профильной.
    #
    # В области названий тот же ранг считается по ВЕСУ A: без весового массива
    # ts_rank_cd продолжал бы складывать вхождения из тела, и среди статей,
    # отобранных по заголовку, вперёд выходила бы самая длинная.
    weights = (_W_TITLE + ', ') if title_only else ''
    score_expr = (
        'ts_rank_cd(' + weights + 'a.search_vector, q.tsq, 32) * 3'
        ' + COALESCE(ts_rank_cd(' + weights + 'a.search_vector, q.tsq_prefix, 32), 0) * 2'
        ' + COALESCE(ts_rank_cd(' + weights + 'a.search_vector, q.tsq_loose, 32), 0) * 0.5'
        ' + ' + similarity_expr + whole_title
    )

    return _SEARCH_SQL.format(
        tier_expr=tier_expr,
        score_expr=score_expr,
        similarity_expr=similarity_expr,
        match_predicate=match_predicate,
        snippet_expr=_SNIPPET_EXPR[scope],
        folded_expr=_FOLDED_EXPR[scope],
        headline_join=_HEADLINE_JOIN[scope],
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

    Подсветка считается по ТЕЛУ статьи, а не по «заголовок + описание + тело»:
    во втором случае первый фрагмент почти всегда начинался с заголовка и
    дублировал строку прямо над собой. Совпало только в заголовке или алиасах —
    фрагментов нет, и фронт показывает описание статьи.
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
        if not fragments:
            # Основной отрывок пуст — пробуем свёрнутый (см. запрос выше).
            fragments = split_snippet(item.get('snippet_folded'))
        item.pop('snippet_folded', None)
        item['highlights'] = fragments
        item['snippet'] = fragments[0] if fragments else ''
        items.append(item)
    return items


def _run(cursor, sql, ids, variants, section_id, limit,
         article_types=None, author_ids=None):
    cursor.execute(sql, {
        'ids': ids,
        'variants': variants,
        'prefixes': [prefix_tsquery(v) for v in variants],
        'looses': [prefix_tsquery(v, ' | ') for v in variants],
        'section': section_id,
        # Пустой список — это НЕ «ничего не показывать», а «фильтр не задан»:
        # приди сюда [] как есть, `= ANY('{}')` не совпал бы ни с одной статьей,
        # и снятая галочка выглядела бы как «ничего не найдено».
        'article_types': list(article_types or ()) or None,
        'authors': list(author_ids or ()) or None,
        'limit': limit,
    })
    return _rows_to_items(cursor)


def search(cursor, visible_ids, query, *, section_id=None, article_types=None,
           author_ids=None, scope=MATCH_ALL, limit=20, with_trigram=True):
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

    Три фильтра выдачи, все необязательные и все сужающие УЖЕ посчитанный
    периметр, а не заменяющие его:
      * article_types — типы документа (регламент, инструкция, ...);
      * author_ids    — создатели статьи (wiki_articles.author_id);
      * scope         — где искать: MATCH_ALL (везде) или MATCH_TITLE (только
                        в названиях статей).
    Пустой список и None значат одно и то же — «фильтр не задан».
    """
    if not visible_ids or not str(query or '').strip():
        return []

    trimmed = str(query).strip()[:MAX_QUERY_CHARS]
    variants = [v for v in wiki_text.query_variants(trimmed) if len(v) >= 2]
    if not variants:
        return []

    ids = list(visible_ids)
    return _run(cursor, build_sql(with_trigram, scope), ids, variants, section_id, limit,
                article_types=article_types, author_ids=author_ids)


def suggest(cursor, visible_ids, query, *, limit=5, with_trigram=True):
    """Подсказки по мере ввода — те же правила, только короче выдача."""
    return search(cursor, visible_ids, query, limit=limit, with_trigram=with_trigram)


# ── Журнал запросов ──────────────────────────────────────────────────────────
#
# Живёт здесь, а не в роуте, по тому же правилу, по которому просмотр статьи
# пишет articles.register_view: у записи и у самого поиска должна быть одна
# нормализация. Разъедься они — «Қазына» в логе перестанет склеиваться с
# «казына» в отчёте, и обнаружится это через месяц на пустом топе.
#
# Свёртка префиксов (см. шапку таблицы в schema.py) сделана UPDATE'ом вместо
# INSERT'а: строка того же человека за последние 30 секунд переписывается, если
# один запрос — начало другого. Проверка идёт через left(длинный, длина
# короткого) = короткий, а НЕ через LIKE norm || '%': psycopg2 в запросе с
# параметрами считает % плейсхолдером, и LIKE сломался бы ещё и о символы _ и %
# внутри самого запроса пользователя.

# Шесть цифр подряд и длиннее — телефон или ИИН. В вике есть статья про смену
# номера, и ищут по ней именно так. Для отчёта конкретный номер не нужен.
_DIGITS = re.compile(r'\d{6,}')

# Окно свёртки. Тридцати секунд хватает на дописывание фразы и не хватает,
# чтобы склеить два разных вопроса подряд.
_COLLAPSE_WINDOW = "interval '30 seconds'"

_COLLAPSE_SQL = """
UPDATE wiki_search_log
   SET query = %(query)s, query_norm = %(norm)s, results_count = %(found)s,
       perimeter_size = %(perimeter)s, steps = steps + 1, filtered = %(filtered)s,
       created_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
 WHERE id = (
     SELECT id FROM wiki_search_log
      WHERE user_id = %(user)s
        AND created_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - """ + _COLLAPSE_WINDOW + """
        AND (left(%(norm)s, length(query_norm)) = query_norm
             OR left(query_norm, length(%(norm)s)) = %(norm)s)
      ORDER BY created_at DESC
      LIMIT 1
 )
RETURNING id
"""

_INSERT_SQL = """
INSERT INTO wiki_search_log (user_id, department_id, space_id, query, query_norm,
                             results_count, perimeter_size, filtered)
VALUES (%(user)s, %(department)s, %(space)s, %(query)s, %(norm)s, %(found)s,
        %(perimeter)s, %(filtered)s)
"""


def log_query(cursor, *, user_id, query, results_count, perimeter_size,
              department_id=None, space_id=None, filtered=False):
    """Записать поисковый запрос. Возвращает True, если строка легла в журнал.

    Вызывающий обязан обернуть вызов савпоинтом: запись идёт в ТОЙ ЖЕ
    транзакции, что и сам поиск, и падение INSERT'а иначе превратило бы рабочую
    выдачу в 500. Журнал — приставка к поиску, а не его часть.

    filtered — «выдача была сужена фильтрами». Пишется рядом с числом находок,
    потому что без него отчёт «искали и не нашли» перестаёт отвечать на свой
    вопрос: ноль при отмеченном типе документа означает не «статьи нет», а
    «статья не того вида», и лечится это не написанием статьи.
    """
    trimmed = str(query or '').strip()[:MAX_QUERY_CHARS]
    if len(trimmed) < 2:
        return False
    masked = _DIGITS.sub('#', trimmed)
    params = {
        'user': user_id,
        'department': department_id,
        'space': space_id,
        'query': masked,
        'norm': wiki_text.fold_kazakh(masked.lower()),
        'found': min(int(results_count or 0), 32767),
        'perimeter': min(int(perimeter_size or 0), 32767),
        'filtered': bool(filtered),
    }
    # Свернуть можно только у известного человека: у анонимного запроса нет
    # владельца, и «предыдущая строка за 30 секунд» склеила бы разных людей.
    if user_id:
        cursor.execute(_COLLAPSE_SQL, params)
        if cursor.fetchone():
            return True
    cursor.execute(_INSERT_SQL, params)
    return True


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
