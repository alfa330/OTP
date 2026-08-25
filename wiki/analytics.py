# -*- coding: utf-8 -*-
"""Аналитика раздела «Вики»: чтение, ознакомления и спрос без ответа.

Отдельный модуль с чистым SQL — по тем же причинам, по которым отдельно живут
wiki/trainers.py и wiki/ack.py: запросы длинные, их проверяют настоящими на
таблицах-заглушках (tests/test_wiki_analytics.py), а роут обязан оставаться
читаемым.

ЧТО ЗДЕСЬ ВАЖНО ЗНАТЬ, ПРЕЖДЕ ЧЕМ ПРАВИТЬ.

1. ПРОЧТЕНИЕ ≠ СТРОКА ЖУРНАЛА. wiki_article_views_log пишется на КАЖДЫЙ GET
   статьи: обновление страницы, возврат «назад», предпросмотр редактора — всё
   это отдельные строки. На боевой базе сырых открытий вдвое больше, чем
   осмысленных чтений. Поэтому «прочтение» здесь — различная тройка (человек,
   статья, минута), а сырое число показывается рядом оговоркой.

2. ЧИСЛО ПОД СТАТЬЁЙ И ЧИСЛО ЗДЕСЬ РАЗНЫЕ, И ЭТО НЕ ДЕФЕКТ. На витрине стоит
   пожизненный счётчик wiki_articles.views — те же открытия, но с начала времён
   и без окна. Здесь всё за период и по прочтениям.

3. ВРЕМЯ НАИВНОЕ И АЛМАТИНСКОЕ. Метки лежат без зоны, но записаны алматинским
   временем, а сервер БД живёт в UTC. Сравнение с now() или CURRENT_DATE
   сдвинуло бы границу суток на пять часов. Единственная допустимая форма
   «сейчас» — (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty').

4. ПЕРИМЕТР СЧИТАЕТ ВЫЗЫВАЮЩИЙ. Сюда приходит готовое множество видимых статей
   — то же, которым пользуются витрина и помощник (wiki/perimeter.py). Второй
   реализации доступа здесь нет: ровно на таком раздвоении сломалась исходная
   вика.

5. ОТМЕНЁННОЕ НАЗНАЧЕНИЕ — НЕ ПРОСРОЧКА. Из ознакомлений выбрасываются и
   'superseded', и 'cancelled'. Первый статус снимается сам (перевыпуск версии),
   второй — нет: у отменённого назначения остаются и срок, и отсутствие
   подписи, и без фильтра оно вечно висело бы в «просрочено».
"""

from .ai.answer import NO_ANSWER_TEXT

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Окно склейки повторных открытий одной статьи одним человеком. Минута — это
# замер, а не «примерно»: дубли в журнале это обновление страницы и возврат
# «назад», они укладываются в секунды, а повторное открытие через час — уже
# честное второе чтение.
_READ_GRAIN = "date_trunc('minute', v.viewed_at)"

# Кто считается штатом в знаменателе охвата. Уволенные не в счёт, а вот отпуск,
# больничный и Б/С — в счёт: человек в отпуске остаётся сотрудником, которому
# вика адресована. Список статусов — из CHECK'а users.status (database.py).
_HEADCOUNT = "u.status NOT IN ('fired', 'dismissal')"

# Живое назначение на ознакомление.
_ACK_LIVE = "k.status NOT IN ('superseded', 'cancelled')"

# Период. Приведение типа у ОБОИХ вхождений until, а не только у проверки на
# NULL: «NULL + INTERVAL» Postgres вывести не может и падает с «operator does
# not exist». На моках это не ловится — там любой SQL «проходит».
_PERIOD = """
   AND (%(since)s::timestamp IS NULL OR {column} >= %(since)s::timestamp)
   AND (%(until)s::timestamp IS NULL
        OR {column} < %(until)s::timestamp + INTERVAL '1 day')
"""


def _period(column):
    return _PERIOD.format(column=column)


# ── Чтение и охват ───────────────────────────────────────────────────────────
#
# Все разрезы блока считаются от ОДНОГО набора событий — свёрнутых прочтений.
# Вынесен в CTE, а не скопирован по запросам: разъедься определение прочтения
# между плитками и таблицами, и числа на одном экране перестанут сходиться
# друг с другом.
_READS_CTE = """
WITH reads AS (
    SELECT DISTINCT
           v.user_id,
           v.article_id,
           """ + _READ_GRAIN + """ AS at,
           COALESCE(v.snapshot_department_id, u.department_id) AS department_id
      FROM wiki_article_views_log v
      LEFT JOIN users u ON u.id = v.user_id
     WHERE v.article_id = ANY(%(visible)s)
""" + _period('v.viewed_at') + """
)
"""

_TOTALS_SQL = _READS_CTE + """
SELECT (SELECT count(*) FROM reads)                       AS reads,
       (SELECT count(DISTINCT user_id) FROM reads)        AS readers,
       (SELECT count(DISTINCT article_id) FROM reads)     AS articles_read,
       (SELECT count(*) FROM wiki_article_views_log v
         WHERE v.article_id = ANY(%(visible)s)
""" + _period('v.viewed_at') + """)                       AS opens,
       (SELECT count(*) FROM wiki_articles a
         WHERE a.id = ANY(%(visible)s) AND a.status = 'published') AS published,
       -- Сколько ОПУБЛИКОВАННОГО читали. Нужно ради разности: список
       -- нетронутого режется потолком строк, и без этого числа плитка
       -- показывала бы длину куска («не открывали 20») вместо правды.
       -- Считается разностью, а не вторым NOT EXISTS: одно определение
       -- «читали за период» на весь блок — второе разошлось бы с первым.
       (SELECT count(DISTINCT r.article_id)
          FROM reads r JOIN wiki_articles a ON a.id = r.article_id
         WHERE a.status = 'published')                    AS published_read
"""

_DAYS_SQL = _READS_CTE + """
SELECT at::date AS day, count(*) AS reads, count(DISTINCT user_id) AS readers
  FROM reads
 GROUP BY 1
 ORDER BY 1
"""

_TOP_SQL = _READS_CTE + """
SELECT a.id, a.slug, a.title, a.status,
       count(*)                       AS reads,
       count(DISTINCT r.user_id)      AS readers,
       a.updated_at
  FROM reads r
  JOIN wiki_articles a ON a.id = r.article_id
 GROUP BY a.id, a.slug, a.title, a.status, a.updated_at
 ORDER BY count(*) DESC, count(DISTINCT r.user_id) DESC, a.title
 LIMIT %(limit)s
"""

# Контент-долг: опубликованное, к чему за период никто не прикоснулся. Только
# опубликованное — черновик без просмотров это норма, а не находка. Дата
# последнего чтения берётся ЗА ВСЁ ВРЕМЯ, а не за период: «читали в марте» и
# «не читали никогда» — разные диагнозы, и второй дороже.
_UNREAD_SQL = """
SELECT a.id, a.slug, a.title, a.updated_at,
       (SELECT max(v.viewed_at) FROM wiki_article_views_log v
         WHERE v.article_id = a.id) AS last_at
  FROM wiki_articles a
 WHERE a.id = ANY(%(visible)s)
   AND a.status = 'published'
   AND NOT EXISTS (
       SELECT 1 FROM wiki_article_views_log v
        WHERE v.article_id = a.id
""" + _period('v.viewed_at') + """
   )
 ORDER BY last_at NULLS FIRST, a.updated_at
 LIMIT %(limit)s
"""

# По отделам. Прочтения — по снимку отдела, штат — живой: вопрос разреза не
# «сколько прочитали», а «до кого вика вообще дошла», и без знаменателя на него
# не ответить.
_BY_DEPARTMENT_SQL = _READS_CTE + """
SELECT r.department_id,
       COALESCE(d.name, 'Без отдела') AS name,
       count(*)                       AS reads,
       count(DISTINCT r.user_id)      AS readers,
       count(DISTINCT r.article_id)   AS articles_read,
       (SELECT count(*) FROM users u
         WHERE u.department_id = r.department_id AND """ + _HEADCOUNT + """) AS headcount
  FROM reads r
  LEFT JOIN departments d ON d.id = r.department_id
 GROUP BY r.department_id, d.name
 ORDER BY count(*) DESC
"""


def reading(cursor, visible_ids, *, since=None, until=None, limit=10):
    """Блок «Чтение и охват»."""
    if not visible_ids:
        return {'totals': _empty_reading_totals(), 'days': [], 'top': [],
                'unread': [], 'departments': []}
    params = {'visible': list(visible_ids), 'since': since, 'until': until,
              'limit': limit}

    cursor.execute(_TOTALS_SQL, params)
    reads, readers, articles_read, opens, published, published_read = cursor.fetchone()
    totals = {
        'reads': reads or 0,
        'opens': opens or 0,
        'readers': readers or 0,
        'articles_read': articles_read or 0,
        'published': published or 0,
        # Опубликованное, к чему за период никто не прикоснулся, — ЦЕЛИКОМ,
        # а не «сколько влезло в список ниже».
        'unread': max(0, (published or 0) - (published_read or 0)),
        # Охват — доля опубликованного, которую за период открыл хоть кто-то.
        # Знаменатель именно опубликованное: черновики в охват не входят,
        # иначе показатель падал бы от того, что кто-то начал писать статью.
        'coverage': round(100.0 * (articles_read or 0) / published, 1) if published else None,
    }

    cursor.execute(_DAYS_SQL, params)
    days = [{'day': _iso(row[0]), 'reads': row[1], 'readers': row[2]}
            for row in cursor.fetchall()]

    cursor.execute(_TOP_SQL, params)
    top = [_row(('id', 'slug', 'title', 'status', 'reads', 'readers', 'updated_at'), row)
           for row in cursor.fetchall()]

    cursor.execute(_UNREAD_SQL, params)
    unread = [_row(('id', 'slug', 'title', 'updated_at', 'last_at'), row)
              for row in cursor.fetchall()]

    cursor.execute(_BY_DEPARTMENT_SQL, params)
    departments = [_row(('department_id', 'name', 'reads', 'readers',
                         'articles_read', 'headcount'), row)
                   for row in cursor.fetchall()]

    return {'totals': totals, 'days': days, 'top': top, 'unread': unread,
            'departments': departments}


def _empty_reading_totals():
    return {'reads': 0, 'opens': 0, 'readers': 0, 'articles_read': 0,
            'published': 0, 'unread': 0, 'coverage': None}


# ── Ознакомления ─────────────────────────────────────────────────────────────
#
# ПЕРИОД НА ЭТОТ БЛОК НЕ ДЕЙСТВУЕТ, и это решение, а не упущение. Просрочка не
# бывает «за прошлый месяц»: она есть сейчас или её нет. Показатель «за период»
# означал бы «сколько назначили в те дни», то есть отвечал бы на вопрос про
# работу редактора, а не про выполнение — а открывают блок ради второго.
# Подпись об этом стоит на самом экране: молча игнорировать выбранный период
# нельзя, это читается как поломка фильтра.
_ACK_TOTALS_SQL = """
SELECT count(*),
       count(*) FILTER (WHERE k.status = 'acknowledged'),
       count(*) FILTER (WHERE k.status = 'not_open'),
       count(*) FILTER (WHERE k.due_at IS NOT NULL
                          AND k.acknowledged_at IS NULL
                          AND k.due_at < """ + _NOW + """),
       count(DISTINCT k.user_id),
       count(DISTINCT k.article_id)
  FROM wiki_ack_assignments k
 WHERE k.article_id = ANY(%(visible)s) AND """ + _ACK_LIVE + """
"""

# Отдел — ИЗ СНИМКА назначения, а не из живого users. Человек, перешедший в
# другой отдел, не должен уносить туда чужую просрочку: назначали ему тогда,
# когда он был здесь, и спрашивать за неё будут с прежнего руководителя.
_ACK_BY_DEPARTMENT_SQL = """
SELECT k.snapshot_department_id,
       COALESCE(k.snapshot_department_name, 'Без отдела') AS name,
       count(*)                                             AS total,
       count(*) FILTER (WHERE k.status = 'acknowledged')    AS done,
       count(*) FILTER (WHERE k.due_at IS NOT NULL
                          AND k.acknowledged_at IS NULL
                          AND k.due_at < """ + _NOW + """)  AS overdue
  FROM wiki_ack_assignments k
 WHERE k.article_id = ANY(%(visible)s) AND """ + _ACK_LIVE + """
 GROUP BY k.snapshot_department_id, k.snapshot_department_name
 ORDER BY 5 DESC, 3 DESC
"""

# Поимённый список — то, ради чего блок и открывают: из него получается
# рассылка супервайзерам. Граница отдела применяется здесь (см. шапку роута).
_ACK_OVERDUE_SQL = """
SELECT k.user_id, u.name,
       COALESCE(k.snapshot_department_name, '—') AS department,
       COALESCE(k.snapshot_group_name, '—')      AS team,
       k.snapshot_supervisor_name                AS supervisor,
       a.id, a.slug, a.title, k.due_at, k.status,
       GREATEST(0, (""" + _NOW + """)::date - k.due_at::date) AS days
  FROM wiki_ack_assignments k
  JOIN wiki_articles a ON a.id = k.article_id
  LEFT JOIN users u ON u.id = k.user_id
 WHERE k.article_id = ANY(%(visible)s) AND """ + _ACK_LIVE + """
   AND k.due_at IS NOT NULL AND k.acknowledged_at IS NULL
   AND k.due_at < """ + _NOW + """
   AND (%(depts)s::int[] IS NULL OR k.snapshot_department_id = ANY(%(depts)s))
 ORDER BY k.due_at
 LIMIT %(limit)s
"""


def acknowledgements(cursor, visible_ids, *, depts=None, limit=10):
    """Блок «Ознакомления». Состояние на сейчас — период на него не действует."""
    if not visible_ids:
        return {'totals': _empty_ack_totals(), 'departments': [], 'overdue': []}
    params = {'visible': list(visible_ids),
              'depts': list(depts) if depts is not None else None,
              'limit': limit}

    cursor.execute(_ACK_TOTALS_SQL, params)
    total, done, not_open, overdue, people, articles = cursor.fetchone()
    totals = {'total': total or 0, 'done': done or 0, 'not_open': not_open or 0,
              'overdue': overdue or 0, 'people': people or 0,
              'articles': articles or 0}

    cursor.execute(_ACK_BY_DEPARTMENT_SQL, params)
    by_department = [_row(('department_id', 'name', 'total', 'done', 'overdue'), row)
                     for row in cursor.fetchall()]

    cursor.execute(_ACK_OVERDUE_SQL, params)
    overdue_people = [_row(('user_id', 'name', 'department', 'team', 'supervisor',
                            'article_id', 'slug', 'title', 'due_at', 'status',
                            'days'), row) for row in cursor.fetchall()]

    return {'totals': totals, 'departments': by_department, 'overdue': overdue_people}


def _empty_ack_totals():
    return {'total': 0, 'done': 0, 'not_open': 0, 'overdue': 0, 'people': 0,
            'articles': 0}


# ── Спрос, на который вика не отвечает ───────────────────────────────────────
#
# Главный отчёт вкладки. Спрос приходит двумя КАНАЛАМИ — через поиск и через
# помощника, — но вопрос у них один: какой статьи не хватает. Поэтому наружу
# они отдаются одним списком, а не двумя: два списка тем для одних и тех же
# новых статей читатель всё равно сложил бы в уме, только вручную.
#
# ПОИСК. Пара (находок, размер периметра) отвечает на вопрос, который иначе
# неразрешим: ноль находок означает и «такой статьи нет», и «статья есть, но
# этому человеку не выдана». Первое лечится текстом, второе — доступом.
# Признак «у других находится» отличает одно от другого: если тот же запрос
# кому-то что-то дал, дело в правах. Это осознанный компромисс — признак
# раскрывает сам факт существования статьи вне периметра смотрящего.
#
# ПОМОЩНИК. Причину отказа в базе не хранят, но она восстанавливается:
#   provider IS NULL                      — ни один кусок не прошёл порог, то
#                                           есть модель даже не звали: по этой
#                                           теме в вике нет НИЧЕГО;
#   provider есть, текст = NO_ANSWER_TEXT — ответ придержали, потому что числа
#                                           не подтвердились фрагментами;
#   provider есть, текст другой           — модель отказала своими словами.
# Дырой в базе знаний является только первый случай. Текст константы
# импортируется, а не копируется: сравнение с копией молча развалилось бы при
# первой же правке формулировки.
_SEARCH_TOTALS_SQL = """
SELECT count(*), count(*) FILTER (WHERE l.results_count = 0),
       count(DISTINCT l.user_id), COALESCE(sum(l.steps), 0)
  FROM wiki_search_log l
 WHERE (%(space)s::int IS NULL OR l.space_id IS NULL OR l.space_id = %(space)s)
""" + _period('l.created_at')

_SEARCH_EMPTY_SQL = """
SELECT l.query_norm,
       max(l.query)                    AS text,
       count(*)                        AS times,
       count(DISTINCT l.user_id)       AS people,
       max(l.created_at)               AS last_at,
       max(l.perimeter_size)           AS perimeter,
       EXISTS (SELECT 1 FROM wiki_search_log o
                WHERE o.query_norm = l.query_norm AND o.results_count > 0
""" + _period('o.created_at') + """) AS found_by_others
  FROM wiki_search_log l
 WHERE l.results_count = 0
   AND (%(space)s::int IS NULL OR l.space_id IS NULL OR l.space_id = %(space)s)
""" + _period('l.created_at') + """
 GROUP BY l.query_norm
 ORDER BY count(*) DESC, max(l.created_at) DESC
 LIMIT %(limit)s
"""

# С какого момента журнал вообще пишется. Без этой даты первые недели вкладки
# читались бы как «поиском не пользуются», хотя причина в том, что писать
# начали позавчера.
_SEARCH_SINCE_SQL = "SELECT min(created_at) FROM wiki_search_log"

_ASSISTANT_TOTALS_SQL = """
SELECT count(*),
       count(*) FILTER (WHERE m.kind = 'answer'),
       count(*) FILTER (WHERE m.kind = 'no_answer'),
       count(*) FILTER (WHERE m.kind = 'clarify'),
       count(DISTINCT c.user_id)
  FROM wiki_ai_messages m
  JOIN wiki_ai_chats c ON c.id = m.chat_id
 WHERE m.role = 'assistant'
   AND (%(space)s::int IS NULL OR c.space_id IS NULL OR c.space_id = %(space)s)
""" + _period('m.created_at')

# Вопрос — реплика человека с номером на единицу меньше: seq уникален в чате и
# выдаётся как max+1, поэтому связь точная и без догадок.
#
# УДАЛЁННЫЕ ЧАТЫ СЧИТАЮТСЯ, НО БЕЗ ИМЁН. Чат удаляется мягко и только свой, и
# больше половины неотвеченных вопросов на боевой базе лежит именно в удалённых
# чатах — выбросив их, блок потерял бы половину тем. Жест «убрать это» уважается
# иначе: список отдаёт ТОЛЬКО текст темы и счётчики, без фамилии и отдела,
# и склеивает одинаковые вопросы разных людей в одну строку. Кто спрашивал —
# на этой вкладке не показывается вовсе, ни для удалённых чатов, ни для живых.
_ASSISTANT_UNANSWERED_SQL = """
SELECT lower(q.text)  AS key,
       max(q.text)    AS text,
       count(*)       AS times,
       count(DISTINCT c.user_id) AS people,
       max(m.created_at) AS last_at,
       -- Причина худшая из встреченных: «нет статей по теме» важнее прочих,
       -- потому что только она означает дыру в содержимом.
       bool_or(m.provider IS NULL) AS no_articles,
       bool_or(m.provider IS NOT NULL AND m.text = %(no_answer_text)s) AS unverified
  FROM wiki_ai_messages m
  JOIN wiki_ai_chats c ON c.id = m.chat_id
  JOIN wiki_ai_messages q ON q.chat_id = m.chat_id AND q.seq = m.seq - 1
                         AND q.role = 'user'
 WHERE m.role = 'assistant' AND m.kind = 'no_answer'
   AND (%(space)s::int IS NULL OR c.space_id IS NULL OR c.space_id = %(space)s)
""" + _period('m.created_at') + """
 GROUP BY lower(q.text)
 ORDER BY count(*) DESC, max(m.created_at) DESC
 LIMIT %(limit)s
"""


def demand(cursor, *, since=None, until=None, space_id=None, limit=10):
    """Блок «Спрос без ответа»: что искали и о чём спрашивали впустую."""
    params = {'since': since, 'until': until, 'space': space_id, 'limit': limit,
              'no_answer_text': NO_ANSWER_TEXT}

    cursor.execute(_SEARCH_TOTALS_SQL, params)
    total, empty, people, steps = cursor.fetchone()
    cursor.execute(_SEARCH_SINCE_SQL)
    row = cursor.fetchone()
    logging_since = row[0] if row else None

    cursor.execute(_SEARCH_EMPTY_SQL, params)
    items = [{
        'channel': 'search',
        'key': r[0], 'text': r[1], 'times': r[2], 'people': r[3],
        'last_at': _iso(r[4]),
        # Периметр нужен, чтобы отличить дыру в контенте от дыры в правах.
        'reason': ('rights' if r[6] else 'empty_perimeter' if not r[5] else 'missing'),
    } for r in cursor.fetchall()]

    cursor.execute(_ASSISTANT_TOTALS_SQL, params)
    a_total, answered, no_answer, clarify, a_people = cursor.fetchone()

    cursor.execute(_ASSISTANT_UNANSWERED_SQL, params)
    items += [{
        'channel': 'assistant',
        'key': r[0], 'text': r[1], 'times': r[2], 'people': r[3],
        'last_at': _iso(r[4]),
        'reason': ('missing' if r[5] else 'unverified' if r[6] else 'refused'),
    } for r in cursor.fetchall()]

    # Один список на два канала — сортируется по частоте, а не по каналу:
    # человек ищет темы для новых статей, а не отчёт по источникам. Сортировок
    # две, и порядок обязателен: сортировка в питоне устойчива, поэтому сначала
    # раскладываем по свежести, а потом по частоте — и внутри одинаковой
    # частоты свежесть сохраняется.
    items.sort(key=lambda x: (x['last_at'] or ''), reverse=True)
    items.sort(key=lambda x: x['times'], reverse=True)

    return {
        'search': {
            'total': total or 0,
            'empty': empty or 0,
            'people': people or 0,
            # Насколько «намерения» отличаются от сырых обращений: запросы,
            # набранные подряд одним человеком, склеены в один.
            'steps': steps or 0,
            'empty_share': round(100.0 * (empty or 0) / total, 1) if total else None,
            'logging_since': _iso(logging_since),
        },
        'assistant': {
            'total': a_total or 0,
            'answered': answered or 0,
            'no_answer': no_answer or 0,
            'clarify': clarify or 0,
            'people': a_people or 0,
        },
        'items': items[:limit],
    }


# ── Общее ────────────────────────────────────────────────────────────────────

def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _row(keys, row):
    """Строка курсора → словарь с датами в ISO.

    Приведение дат здесь, а не в роуте: jsonify не умеет datetime, и забыть его
    в одном из десятка разрезов слишком легко.
    """
    return {key: _iso(value) for key, value in zip(keys, row)}
