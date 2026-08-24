# -*- coding: utf-8 -*-
"""Перенос статей из внешней вики и очередь их модерации.

ЗАЧЕМ ЭТО ОТДЕЛЬНЫЙ МЕХАНИЗМ, А НЕ ОБЫЧНОЕ СОЗДАНИЕ СТАТЬИ

Перенос отличается от «человек написал статью» тремя вещами, и каждая из них
здесь и живёт:

  * НИЧЕГО НЕ ПОКАЗЫВАЕМ СРАЗУ. Приехавшая статья — черновик и остаётся им до
    решения человека. Черновик уже скрыт от читателя общим правилом
    (wiki/articles.py: `status = 'published' OR автор OR can_see_drafts`), так
    что отдельного «скрытого» статуса заводить не потребовалось;
  * СВЕРЯЕМ С ТЕМ, ЧТО УЖЕ ЕСТЬ. Тот же поиск дублей, что показывает редактору
    кнопка «Такая статья уже есть?» (wiki/ai/similar.py). Вердикт снимается
    один раз, в момент переноса, и хранится строкой — см. шапку
    _MIGRATION_STATEMENTS в wiki/schema.py про то, почему не пересчитывается;
  * ПОМНИМ, ОТКУДА ЭТО. Строка в wiki_article_imports живёт и после решения:
    вопрос «это приехало из старой вики или мы сами писали?» задают спустя
    месяцы, когда источника уже нет.

ОЧЕРЕДЬ ЗАКРЫВАЕТСЯ, А НЕ КОПИТСЯ

Промодерированная строка из очереди уходит: reviewed_at перестаёт быть NULL.
Поэтому «перенесено 46, ждут проверки 12» — это не два счётчика одного и того
же, а работа и её остаток. Когда остаток обнуляется, экран очереди исчезает из
интерфейса целиком: панель, показывающая «ничего нет», — это шум.

ДВА РЕШЕНИЯ, А НЕ ТРИ

Опубликовать или убрать — так поставлена задача, и третьего действия здесь нет
намеренно. «Посмотрю позже» — это просто не нажать ничего: строка остаётся в
очереди, и она же остаток работы. Различаются лишь ПОДПИСИ решения:
'published' — черновик выпустили, 'kept' — статья была опубликована ещё до
очереди и её подтвердили, 'discarded' — убрали в архив. Все три означают
«решение принято» и все три закрывают строку.
"""

import re

from . import perimeter as wiki_perimeter
from . import sanitize as wiki_sanitize
from .ai import embed as ai_embed
from .ai import similar as ai_similar

# Код источника переноса. Сейчас один: старая корпоративная вика на Wiki.js 2 во
# внутренней сети (http://192.168.88.186:3000, заголовок «Яндекс GO»). Появится
# второй источник — добавится код, таблица к этому готова.
SOURCE_OLD_WIKI = 'wikijs'

# Вердикт проверки на дубль: наш код ← подпись из wiki/ai/similar.py. Подписи
# там на русском и идут в интерфейс, а в базе нужен стабильный ключ — иначе
# переформулировка подписи сломала бы CHECK и все уже записанные строки.
VERDICT_OF_LABEL = {'дубль': 'duplicate', 'похоже': 'similar', 'рядом': 'nearby'}

# Порядок тревожности — им сортируется очередь: дубли первыми, потому что
# именно на них решение принимается быстрее всего.
VERDICT_ORDER = {'duplicate': 0, 'similar': 1, 'nearby': 2, 'unique': 3}

REVIEW_ACTIONS = ('published', 'kept', 'discarded')


def document_words(plain):
    """Слова документа для лексической ветки поиска дублей.

    dict.fromkeys сохраняет порядок первого появления: начало документа
    описывает тему точнее, чем его хвост, а брать все слова статьи на 17 тысяч
    знаков (максимум корпуса) — значит утопить редкие слова в частых.
    """
    words = re.findall(r'[^\W\d_]{4,}', str(plain or '').lower(), re.UNICODE)
    return list(dict.fromkeys(words))[:40]


def duplicate_probe(cursor, ctx, *, title, content, exclude_id=None,
                    allow_vector=True):
    """Есть ли уже такая статья. Пустой ответ — не доказательство отсутствия.

    allow_vector=False — смысловая ветка не считается ВООБЩЕ. Это не
    оптимизация: вектор считает внешний сервис, то есть текст статьи уходит
    наружу, а панель импорта обещает ровно обратное, пока флажок «Поддержка ИИ»
    выключен. Обещание, которое нарушается там, где этого не видно, хуже
    отсутствующего. По названию и словам текста проверка при этом работает —
    она целиком у нас в базе.

    Вектор считается по названию и НАЧАЛУ текста, а не по всей статье: у
    документа на 17 тысяч знаков вектор целого текста размывается до
    бессмысленного, а тема живёт в первых абзацах.

    Одна реализация на три двери: панель импорта документа, кнопка «Такая статья
    уже есть?» в редакторе и перенос из внешней вики. Разъехаться им нельзя —
    иначе перенос и редактор отвечали бы на один вопрос по-разному.
    """
    _subjects, _sections, visible = wiki_perimeter.read_perimeter(cursor, ctx)
    indexed = wiki_perimeter.eligible_article_ids(cursor, visible)
    plain = wiki_sanitize.to_plain_text(content)
    probe = ('%s. %s' % (title or '', plain[:1200])).strip()

    vector = None
    if allow_vector:
        try:
            vector = ai_embed.embed_query(probe)
        except Exception:
            vector = None      # лексика справится и одна, см. wiki/ai/similar.py

    found = ai_similar.find_duplicates(
        cursor, visible_ids=visible, indexed_ids=indexed,
        title=title, text_words=document_words(plain), vector=vector,
        exclude_id=exclude_id)
    # degraded — про сбой эмбеддингов, а не про выключенный флажок: это разные
    # причины неполноты, и смешивать их значит врать в обеих.
    found['degraded'] = allow_vector and vector is None
    found['ai_support'] = bool(allow_vector)
    return found


def verdict_of(found):
    """Свести ответ поиска дублей к тому, что ложится в строку переноса.

    Берём САМУЮ ТРЕВОЖНУЮ находку, а не все: очередь модерации отвечает на
    вопрос «на что посмотреть в первую очередь», и список из пяти похожих в
    строке таблицы на этот вопрос не отвечает. Полный список у человека всё
    равно под рукой — он открывает статью и нажимает «Такая статья уже есть?».
    """
    items = (found or {}).get('items') or []
    if not items:
        return {'verdict': 'unique', 'score': None, 'match_id': None,
                'note': None, 'degraded': bool((found or {}).get('degraded'))}
    top = items[0]
    note = top.get('title') or ''
    if top.get('section'):
        # Раздел в подписи обязателен: на проде есть три пары статей с
        # ОДИНАКОВЫМИ названиями, и «дубль: Рабочие сайты» без раздела не
        # говорит, какая из двух.
        note = '%s · %s' % (note, top['section'])
    return {
        'verdict': VERDICT_OF_LABEL.get(top.get('verdict'), 'nearby'),
        'score': top.get('score'),
        'match_id': top.get('article_id'),
        'note': note or None,
        'degraded': bool((found or {}).get('degraded')),
    }


def already_imported(cursor, *, source, source_id):
    """Ту же статью уже переносили? Возвращает {article_id, slug} или None.

    Слаг здесь не для удобства: по нему скрипт переноса строит карту внутренних
    ссылок, и без него повторный прогон оставил бы их указывать на источник.

    Сверка по (source, source_id), а НЕ по slug: slug в приёмнике мог оказаться
    занят, и статья легла под «-2». Повторный прогон по slug её не нашёл бы и
    завёл третью копию.
    """
    if source_id is None:
        return None
    cursor.execute(
        'SELECT i.article_id, a.slug FROM wiki_article_imports i '
        '  JOIN wiki_articles a ON a.id = i.article_id '
        ' WHERE i.source = %s AND i.source_id = %s',
        (source, int(source_id)),
    )
    row = cursor.fetchone()
    return {'article_id': row[0], 'slug': row[1]} if row else None


def record(cursor, *, article_id, source, source_id=None, source_slug=None,
           source_title=None, source_status=None, dedup=None,
           imported_by=None, reviewed=None, reviewed_by=None):
    """Записать факт переноса.

    reviewed — подпись решения ('published' | 'kept' | 'discarded') для строк,
    которые заводятся уже закрытыми. Нужно ровно для одного случая: восстановить
    в очереди статьи, перенесённые ДО появления этой таблицы. Прогон переноса
    закрытых строк не создаёт — иначе смысл очереди терялся бы.

    ON CONFLICT DO UPDATE, а не DO NOTHING: повторный прогон обязан обновить
    вердикт (корпус приёмника с тех пор вырос, и «уникальна» могла стать
    «дублем»), но НЕ трогать уже принятое решение — его принимал человек.
    """
    verdict = dedup or {}
    if reviewed is not None and reviewed not in REVIEW_ACTIONS:
        raise ValueError('Неизвестное решение модерации: %r' % (reviewed,))
    cursor.execute(
        """
        INSERT INTO wiki_article_imports
               (article_id, source, source_id, source_slug, source_title,
                source_status, dedup_verdict, dedup_score, dedup_match_id,
                dedup_note, dedup_degraded, imported_by,
                reviewed_at, reviewed_by, review_action)
        VALUES (%(article_id)s, %(source)s, %(source_id)s, %(source_slug)s,
                %(source_title)s, %(source_status)s, %(verdict)s, %(score)s,
                %(match_id)s, %(note)s, %(degraded)s, %(imported_by)s,
                -- Приведения типов здесь ОБЯЗАТЕЛЬНЫ: без них Postgres не
                -- выводит тип параметра внутри CASE, где вторая ветвь — голый
                -- NULL, и запрос падает на «could not determine data type».
                CASE WHEN %(reviewed)s::varchar IS NULL THEN NULL::timestamp
                     ELSE (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') END,
                CASE WHEN %(reviewed)s::varchar IS NULL THEN NULL::integer
                     ELSE %(reviewed_by)s::integer END,
                %(reviewed)s)
        ON CONFLICT (article_id) DO UPDATE SET
                dedup_verdict  = EXCLUDED.dedup_verdict,
                dedup_score    = EXCLUDED.dedup_score,
                dedup_match_id = EXCLUDED.dedup_match_id,
                dedup_note     = EXCLUDED.dedup_note,
                dedup_degraded = EXCLUDED.dedup_degraded
        """,
        {
            'article_id': article_id, 'source': source,
            'source_id': int(source_id) if source_id is not None else None,
            'source_slug': (source_slug or None), 'source_title': (source_title or None),
            'source_status': (source_status or None),
            'verdict': verdict.get('verdict') or 'unique',
            'score': verdict.get('score'), 'match_id': verdict.get('match_id'),
            'note': verdict.get('note'), 'degraded': bool(verdict.get('degraded')),
            'imported_by': imported_by,
            'reviewed': reviewed, 'reviewed_by': reviewed_by,
        },
    )


# Очередь читает только СВОИ строки и только те статьи, которые человек вправе
# видеть: границу считает периметр, а не этот запрос. Иначе супервайзер СЗоВ
# увидел бы в очереди статьи чужого пространства — те самые, которые весь
# остальной раздел от него закрывает.
_QUEUE_SQL = """
SELECT i.article_id, i.source, i.source_id, i.source_slug, i.source_title,
       i.source_status, i.dedup_verdict, i.dedup_score, i.dedup_match_id,
       i.dedup_note, i.dedup_degraded, i.imported_at,
       i.reviewed_at, i.review_action,
       reviewer.name AS reviewed_by_name,
       a.title, a.slug, a.status, a.summary,
       length(a.content_plain) AS size,
       dup.title AS match_title, dup.slug AS match_slug,
       dup.status AS match_status,
       (SELECT string_agg(s.name, ', ' ORDER BY s.name)
          FROM wiki_article_sections xs
          JOIN wiki_sections s ON s.id = xs.section_id
         WHERE xs.article_id = i.article_id) AS sections
  FROM wiki_article_imports i
  JOIN wiki_articles a  ON a.id = i.article_id
  -- Алиас dup, а не match: MATCH — зарезервированное слово Postgres, и
  -- таблицей его не назвать даже через AS.
  LEFT JOIN wiki_articles dup ON dup.id = i.dedup_match_id
  LEFT JOIN users reviewer ON reviewer.id = i.reviewed_by
 WHERE i.article_id = ANY(%(ids)s)
   AND (%(pending_only)s IS NOT TRUE OR i.reviewed_at IS NULL)
 ORDER BY (i.reviewed_at IS NOT NULL),
          CASE i.dedup_verdict WHEN 'duplicate' THEN 0 WHEN 'similar' THEN 1
                               WHEN 'nearby' THEN 2 ELSE 3 END,
          i.source_id NULLS LAST, i.article_id
"""


def _visible(cursor, ctx, space_id=None):
    """Периметр очереди — ТОТ ЖЕ, что у витрины и каталога.

    `master_key=False` и сужение пространством повторяют `_browse` в
    routes_articles намеренно: счётчик, по которому появляется половина
    «Перенос», приходит из каталога, и считать он обязан то же, что покажет
    список за ним. Разойдись они — половина появлялась бы с пустой очередью
    внутри (тот же класс дефекта, что сторожит tests/test_wiki_catalog.py).
    """
    _subjects, _sections, visible = wiki_perimeter.read_perimeter(
        cursor, ctx, master_key=False, space_id=space_id)
    return visible


# Потолок строк очереди. Перенос старой вики привёз 247 статей за один
# прогон, и на 200 список молча терял 47 — а очередь это список ДЕЛ, и
# незаметно выпавшее из неё дело не будет сделано никогда. Интерфейс, если
# упрётся в потолок, честно говорит, сколько показал (см. WikiMigration).
QUEUE_LIMIT = 1000


def queue(cursor, ctx, *, pending_only=True, limit=QUEUE_LIMIT, space_id=None):
    """Очередь модерации переноса в границах видимости человека."""
    visible = _visible(cursor, ctx, space_id)
    if not visible:
        return []
    cursor.execute(_QUEUE_SQL, {'ids': sorted(visible),
                                'pending_only': bool(pending_only)})
    columns = [c[0] for c in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()[:limit]]
    for row in rows:
        # Балл — NUMERIC, а json его не сериализует. Приводим здесь, а не в
        # роуте: роут отдаёт то, что дал этот модуль, и вторая точка приведения
        # однажды разошлась бы с первой.
        if row.get('dedup_score') is not None:
            row['dedup_score'] = float(row['dedup_score'])
        for key in ('imported_at', 'reviewed_at'):
            if row.get(key) is not None:
                row[key] = row[key].isoformat(sep=' ', timespec='seconds')
    return rows


def totals_for(cursor, visible_ids):
    """То же, но по уже посчитанному периметру.

    Нужно ровно для одного места — /catalog: он периметр уже посчитал, и второй
    его расчёт ради счётчика был бы платой ни за что. Именно из этого счётчика
    интерфейс решает, показывать ли половину «Перенос», поэтому периметр обязан
    быть ТОТ ЖЕ, что у списка за ней: иначе половина появлялась бы с пустой
    очередью внутри.
    """
    visible = sorted(visible_ids or ())
    if not visible:
        return {'imported': 0, 'pending': 0, 'duplicates': 0, 'reviewed': 0}
    cursor.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE i.reviewed_at IS NULL),
               count(*) FILTER (WHERE i.reviewed_at IS NULL
                                  AND i.dedup_verdict = 'duplicate')
          FROM wiki_article_imports i
          JOIN wiki_articles a ON a.id = i.article_id
         WHERE i.article_id = ANY(%s)
        """,
        (visible,),
    )
    # Агрегат всегда отдаёт строку — кроме подменённого курсора в тестах.
    # Запасное значение здесь не «на всякий случай»: без него счётчик каталога
    # ронял бы весь каталог, а он про статьи, а не про перенос.
    imported, pending, duplicates = cursor.fetchone() or (0, 0, 0)
    return {'imported': int(imported), 'pending': int(pending),
            'duplicates': int(duplicates),
            'reviewed': int(imported) - int(pending)}


def totals(cursor, ctx, space_id=None):
    """Сколько перенесено и сколько ещё ждёт решения — в границах видимости."""
    return totals_for(cursor, _visible(cursor, ctx, space_id))


def pending_row(cursor, article_id):
    """Строка переноса для решения. None — статью не переносили.

    Решение по уже закрытой строке возвращает её же: повторное нажатие не должно
    ни падать, ни переписывать первое решение задним числом — этим управляет
    вызывающий, а не запрос.
    """
    cursor.execute(
        """
        SELECT article_id, source, source_id, source_title, review_action,
               reviewed_at IS NOT NULL AS reviewed
          FROM wiki_article_imports WHERE article_id = %s
        """,
        (article_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('article_id', 'source', 'source_id', 'source_title',
                     'review_action', 'reviewed'), row))


def mark_reviewed(cursor, article_id, *, action, reviewer_id, note=None):
    """Закрыть строку очереди принятым решением.

    Условие `reviewed_at IS NULL` в WHERE — не оптимизация, а защита: два
    человека могли открыть очередь одновременно, и второй не должен переписывать
    решение первого. rowcount = 0 означает «уже решено», и вызывающий отвечает
    на это честно, а не рапортует об успехе.
    """
    if action not in REVIEW_ACTIONS:
        raise ValueError('Неизвестное решение модерации: %r' % (action,))
    cursor.execute(
        """
        UPDATE wiki_article_imports
           SET reviewed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               reviewed_by = %s, review_action = %s, review_note = %s
         WHERE article_id = %s AND reviewed_at IS NULL
        """,
        (reviewer_id, action, (note or None), article_id),
    )
    return cursor.rowcount > 0
