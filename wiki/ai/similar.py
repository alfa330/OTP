# -*- coding: utf-8 -*-
"""Поиск статьи, которая уже описывает то же самое.

Задача не «найти похожие статьи», а ответить редактору на вопрос «такая статья
уже есть?» — до того, как он создаст вторую. Поэтому ответ должен быть коротким
и с уверенностью, а не выдачей поиска.

ДВЕ ВЕТКИ, И ОНИ ПОКРЫВАЮТ РАЗНОЕ — это главное, что здесь важно понимать.

  * НАЗВАНИЕ и ТЕКСТ лексикой (pg_trgm, расширение уже стоит на проде) — по ВСЕМ
    статьям, которые человек вправе видеть. Включая черновики, архив и статьи со
    строгим режимом: дубль, лежащий в архиве, это тоже дубль, и создавать рядом
    третью копию из-за того, что мы её не показали, глупо.
  * ВЕКТОР — только по статьям, попавшим в индекс помощника. Индекс живёт в
    границах периметра ИИ (wiki/perimeter.py): опубликованные, без строгого
    режима, без рубильника. То есть вектор в принципе не видит часть корпуса, и
    молчание вектора НЕ означает «дубля нет».

Отсюда правило: лексика идёт всегда, вектор — добавка. Обратный порядок дал бы
ложное «ничего похожего нет» ровно на тех статьях, которые скрыты от ИИ, а
редактор доверился бы этому ответу.

Пороги ИЗМЕРЕНЫ на боевом корпусе (26 проиндексированных статей, 11.08.2026,
scratchpad/similar_probe.py и dup_detail.py), а не выбраны на глаз:

  * статья против САМОЙ СЕБЯ (эталон дубля): 0,914-0,980, медиана 0,949;
  * настоящий дубль в корпусе — «Рабочие сайты» существует дважды (id 11 и 24,
    ОБЕ опубликованы): 0,915 и 0,928 между собой;
  * близкие, но РАЗНЫЕ статьи: «Номера телефонов» ~ «Консультация пассажиров»
    0,866, «Брендирование» ~ «Информация по Грузовой/Доставка» 0,875;
  * медиана лучшего чужого совпадения по корпусу — 0,817.

Отсюда пороги, и распределения именно в этих местах расходятся:
    >= 0.90  дубль   (выше всех наблюдённых «разных», ниже всех настоящих дублей)
    >= 0.85  похоже  (сюда попадают и близкие-разные — их и надо посмотреть)
    >= 0.82  рядом   (ниже показывать нечего: медиана чужого 0,817)
Название по триграммам: одинаковые названия дают 1,000 (в корпусе таких три
пары), а самое похожее НЕ совпадающее название — 0,333. Порог 0,45 поэтому
безопасен. Отдельно правило вхождения: «Отпуск» внутри «Отпуск, больничный и
отгулы» даёт всего 0,280, хотя это очевидный дубль — триграммы короткую строку
внутри длинной не ловят.
"""

SURE = 0.90
CLOSE = 0.85
NEARBY = 0.82
TITLE_HIT = 0.45

# Название: триграммное сходство + прямое вхождение. Вхождение нужно отдельно от
# similarity, потому что короткое название внутри длинного даёт низкий триграммный
# балл («Отпуск» в «Отпуск, больничный и отгулы» — 0,32), а это очевидный дубль.
_TITLE_SQL = """
SELECT a.id, a.title, a.slug, a.status, left(coalesce(a.summary, ''), 200),
       GREATEST(
           similarity(translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ'), translate(lower(%(title)s), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')),
           CASE WHEN translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') LIKE '%%' || translate(lower(%(title)s), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') || '%%'
                  OR translate(lower(%(title)s), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') LIKE '%%' || translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') || '%%'
                THEN 0.75 ELSE 0 END
       ) AS score
  FROM wiki_articles a
 WHERE a.id = ANY(%(article_ids)s)
   AND (%(exclude_id)s::int IS NULL OR a.id <> %(exclude_id)s::int)
   AND length(btrim(a.title)) > 0
   AND GREATEST(
           similarity(translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ'), translate(lower(%(title)s), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ')),
           CASE WHEN translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') LIKE '%%' || translate(lower(%(title)s), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') || '%%'
                  OR translate(lower(%(title)s), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') LIKE '%%' || translate(lower(a.title), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') || '%%'
                THEN 0.75 ELSE 0 END
       ) >= %(floor)s
 ORDER BY score DESC
 LIMIT %(limit)s
"""

# Текст: ДОЛЯ веса слов документа, найденная в статье. Не нормировка «на лучшего»
# из выдачи — та давала бы находку всегда, даже когда похожего нет вовсе, и панель
# кричала бы на каждую статью. Здесь знаменатель фиксирован: суммарный IDF слов
# документа, которые вообще встречаются хоть где-то. То есть 0,9 значит «в этой
# статье почти все редкие слова документа», а не «эта статья лучшая из плохих».
#
# IDF, а не ts_rank_cd: у того нет обратной документной частоты, и я на этом уже
# обжигался в поиске помощника — частые слова перевешивали редкие, и наверх лезла
# статья, совпавшая одним словом «аренда».
_TEXT_SQL = """
WITH words AS (
    SELECT DISTINCT translate(lower(w), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') AS word
      FROM unnest(%(words)s::text[]) AS w
),
corpus AS (
    SELECT a.id, a.title, a.slug, a.status,
           left(coalesce(a.summary, ''), 200) AS excerpt,
           translate(lower(a.content_plain), 'әӘғҒқҚңҢөӨұҰүҮһҺіІёЁ', 'аАгГкКнНоОуУуУхХиИеЕ') AS body
      FROM wiki_articles a
     WHERE a.id = ANY(%(article_ids)s)
       AND (%(exclude_id)s::int IS NULL OR a.id <> %(exclude_id)s::int)
       AND length(a.content_plain) > 40
),
total AS (SELECT GREATEST(count(*), 1) AS n FROM corpus),
df AS (
    -- LEFT JOIN, а не JOIN: слово, которого нет НИ В ОДНОЙ статье, обязано
    -- остаться в знаменателе. Иначе покрытие врёт: тест поймал случай, где из
    -- шести слов документа в вике нашлись два, а балл вышел 1,0 — то есть
    -- «полный дубль» у документа, которого в вике нет.
    SELECT w.word, count(c.id) AS docs
      FROM words w
      LEFT JOIN corpus c ON c.body LIKE '%%' || w.word || '%%'
     GROUP BY w.word
),
weights AS (
    -- df=0 весит как самое редкое слово: ненайденное слово это довод ПРОТИВ
    -- дубля, а не отсутствие довода.
    SELECT df.word, ln(total.n::float / GREATEST(df.docs, 1) + 1) AS idf,
           df.docs
      FROM df CROSS JOIN total
),
denominator AS (SELECT GREATEST(sum(idf), 0.000001) AS total_idf FROM weights)
SELECT c.id, c.title, c.slug, c.status, c.excerpt,
       sum(weights.idf) / denominator.total_idf AS coverage,
       count(*) AS hits
  FROM corpus c
  JOIN weights ON weights.docs > 0
                AND c.body LIKE '%%' || weights.word || '%%'
  CROSS JOIN denominator
 GROUP BY c.id, c.title, c.slug, c.status, c.excerpt, denominator.total_idf
 ORDER BY coverage DESC
 LIMIT %(limit)s
"""


def _rank_label(score):
    if score >= SURE:
        return 'дубль'
    if score >= CLOSE:
        return 'похоже'
    return 'рядом'


def by_title(cursor, *, article_ids, title, exclude_id=None, limit=5,
             floor=TITLE_HIT):
    """Статьи с похожим названием. Работает по всем видимым статьям."""
    ids = sorted({int(x) for x in (article_ids or ())})
    clean = ' '.join(str(title or '').split())
    if not ids or len(clean) < 3:
        return []
    cursor.execute(_TITLE_SQL, {'article_ids': ids, 'title': clean,
                                'exclude_id': exclude_id, 'limit': int(limit),
                                'floor': float(floor)})
    return [{'article_id': row[0], 'title': row[1], 'slug': row[2],
             'status': row[3], 'excerpt': row[4], 'score': float(row[5] or 0),
             'found_by': 'название'}
            for row in cursor.fetchall()]


def by_text(cursor, *, article_ids, words, exclude_id=None, limit=5):
    """Статьи, пересекающиеся с документом по редким словам."""
    ids = sorted({int(x) for x in (article_ids or ())})
    terms = [w for w in dict.fromkeys(str(x).lower() for x in (words or ())) if len(w) >= 4]
    if not ids or not terms:
        return []
    cursor.execute(_TEXT_SQL, {'article_ids': ids, 'words': terms[:40],
                               'exclude_id': exclude_id, 'limit': int(limit)})
    return [{'article_id': row[0], 'title': row[1], 'slug': row[2],
             'status': row[3], 'excerpt': row[4],
             'score': round(min(1.0, float(row[5] or 0)), 3),
             'hits': row[6], 'found_by': 'текст'}
            for row in cursor.fetchall()]


def by_vector(cursor, *, article_ids, vector, exclude_id=None, limit=5,
              floor=NEARBY):
    """Ближайшие статьи по смыслу. Только по тому, что попало в индекс ИИ."""
    from .retrieve import search_dense

    if not vector:
        return []
    rows = search_dense(cursor, article_ids=article_ids, query_vector=vector,
                        limit=int(limit) * 4, min_similarity=float(floor))
    best = {}
    for row in rows:
        if exclude_id and row['article_id'] == exclude_id:
            continue
        current = best.get(row['article_id'])
        if current is None or row['similarity'] > current['score']:
            best[row['article_id']] = {
                'article_id': row['article_id'], 'title': row['title'],
                'slug': row['slug'], 'score': round(float(row['similarity']), 3),
                'heading_path': row.get('heading_path'), 'found_by': 'смысл',
                # Отрывок совпавшего куска — доказательство прямо в панели.
                # Иначе редактору пришлось бы открывать статью, а открытие пишет
                # просмотр и, у строгих статей, запись в журнал чтения.
                'excerpt': ' '.join(str(row.get('text') or '').split())[:200],
            }
    return sorted(best.values(), key=lambda x: -x['score'])[:limit]


_SECTIONS_SQL = """
SELECT s.article_id, string_agg(sec.name, ', ' ORDER BY sec.name)
  FROM wiki_article_sections s
  JOIN wiki_sections sec ON sec.id = s.section_id
 WHERE s.article_id = ANY(%(ids)s)
 GROUP BY s.article_id
"""


def attach_sections(cursor, items):
    """Дописать название раздела к находкам.

    Нужно там, где без него не разобраться: на проде есть три пары статей с
    ОДИНАКОВЫМИ названиями («Рабочие сайты», «Аренда транспорта», «Информация по
    СМЗ»), и две строки «Рабочие сайты · дубль · 100%» подряд ничего редактору не
    говорят. Раздел отвечает на вопрос «какая из них».

    Отдельным запросом после сведения, а не join'ом в каждой ветке: веток три, и
    у векторной статьи приходят из search_dense, где разделов нет вовсе.
    """
    ids = sorted({int(item['article_id']) for item in items})
    if not ids:
        return items
    cursor.execute(_SECTIONS_SQL, {'ids': ids})
    names = dict(cursor.fetchall())
    for item in items:
        item['section'] = names.get(item['article_id']) or ''
    return items


def find_duplicates(cursor, *, visible_ids, indexed_ids, title, text_words,
                    vector=None, exclude_id=None, limit=5):
    """Свести три ветки в один ответ редактору.

    visible_ids — что человек вправе видеть (лексика ищет здесь),
    indexed_ids — что попало в индекс ИИ (вектор ищет здесь).

    Возвращает {'items': [...], 'verdict': 'дубль'|'похоже'|'рядом'|None,
    'vector_covered': bool}. verdict — самая тревожная из находок: редактору
    нужен один вывод, а не три списка.
    """
    found = {}

    def absorb(rows, weight=1.0):
        for row in rows:
            key = row['article_id']
            row = dict(row)
            row['score'] = round(min(1.0, row['score'] * weight), 3)
            existing = found.get(key)
            if existing is None:
                found[key] = row
                continue
            # Одна статья могла найтись всеми ветками — оставляем самую сильную
            # причину, но перечисляем все, иначе редактор не поймёт, почему это
            # вообще предложено.
            reasons = set(str(existing.get('found_by') or '').split(', '))
            reasons.add(row['found_by'])
            if row['score'] > existing['score']:
                found[key] = row
            found[key]['found_by'] = ', '.join(sorted(r for r in reasons if r))

    # Вектор первым: его балл — настоящая близость, а не нормировка.
    absorb(by_vector(cursor, article_ids=indexed_ids, vector=vector,
                     exclude_id=exclude_id, limit=limit))
    absorb(by_title(cursor, article_ids=visible_ids, title=title,
                    exclude_id=exclude_id, limit=limit))
    absorb(by_text(cursor, article_ids=visible_ids, words=text_words,
                   exclude_id=exclude_id, limit=limit))

    # Ниже порога «рядом» не показываем НИЧЕГО. Панель, показывающая пять
    # случайных статей на каждую проверку, обесценивает и настоящую находку.
    items = sorted((row for row in found.values() if row['score'] >= NEARBY),
                   key=lambda x: -x['score'])[:limit]
    for item in items:
        item['verdict'] = _rank_label(item['score'])
    attach_sections(cursor, items)

    verdict = items[0]['verdict'] if items else None
    return {
        'items': items,
        'verdict': verdict,
        # Прямо говорим, покрыл ли смысловой поиск корпус: если статья не в
        # индексе ИИ, «похожего не найдено» означает меньше, чем кажется.
        'vector_covered': bool(vector) and bool(indexed_ids),
    }
