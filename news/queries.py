# -*- coding: utf-8 -*-
"""SQL-слой раздела «Новости».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и ничего не знают
про пул и транзакции — их открывает вызывающий, как в wiki/queries.py и call_qa.
"""

import json

from wiki import access as wiki_access
from wiki import structure as wiki_structure

from . import access as news_access
from .access import (AUDIENCE_MATCH_FOR_REPORT, AUDIENCE_MATCH_FOR_VIEWER,
                     ROLE_CANON, SQL_ROLE_LEVELS)

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Сколько дней новость гоняется за неподтвердившим, если срок ей не задан.
#
# Без горизонта неподтверждённая новость живёт вечно, и это бьёт по тому, кого
# в момент выпуска в компании не было: человек выходит на работу, попадает под
# правило «отдел СЗоВ» — и получает подряд ВСЕ обязательные окна, накопленные
# за год. То же у вернувшегося из долгого отпуска и у переведённого в другой
# отдел. Это не гипотеза, а прямое следствие того, что адресат считается по
# ТЕКУЩЕМУ профилю, а не по составу отдела на день выпуска.
#
# Две недели, а не сутки: объявление обязано дождаться тех, кто в отпуске или
# на больничном неделю. Журнал при этом НЕ обрезается — «не подтвердил»
# остаётся в нём навсегда, горизонт снимает только окно.
SHOW_HORIZON_DAYS = 14

# Сотрудник «на месте». Фильтр по status, а не по is_active: в боевой базе
# is_active снят почти у всех, и по нему адресатов оказалось бы десять из
# трёхсот (см. wiki/structure.py: grantable_people).
_WORKING = "u.status = 'working'"

# Субъекты сотрудника, посчитанные в SQL, — для журнала «кому ушла новость».
# Ровно те же четыре оси, что collect_subjects считает в питоне для зрителя.
_VIEWER_SUBJECTS_CTE = """
viewers AS (
    SELECT
        u.id,
        u.name,
        u.role,
        d.name AS department_name,
        ARRAY_REMOVE(ARRAY[u.department_id], NULL)  AS department_ids,
        ARRAY_REMOVE(ARRAY[u.direction_id], NULL)   AS direction_ids,
        COALESCE((
            SELECT ARRAY_AGG(DISTINCT gm.group_id)
              FROM (
                    SELECT gom.group_id
                      FROM group_operator_memberships gom
                      JOIN groups g ON g.id = gom.group_id AND g.status = 'active'
                     WHERE gom.operator_id = u.id
                       AND gom.start_date <= CURRENT_DATE
                       AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
                     UNION
                    SELECT gsm.group_id
                      FROM group_supervisor_memberships gsm
                      JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
                     WHERE gsm.supervisor_id = u.id
                       AND gsm.start_date <= CURRENT_DATE
                       AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)
                   ) gm
        ), ARRAY[]::INTEGER[]) AS group_ids,
        ARRAY[{canon_role}] AS roles,
        {role_level} AS role_level
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
     WHERE {working}
)
""".format(
    canon_role=news_access.canon_role_sql('u.role'),
    role_level=news_access.role_level_sql(news_access.canon_role_sql('u.role')),
    working=_WORKING,
)


def _role_params():
    """Две таблицы, без которых выражения должности в SQL не соберутся."""
    return {'role_canon': json.dumps(ROLE_CANON),
            'role_levels': json.dumps(SQL_ROLE_LEVELS)}


# ─────────────────────────────────────────────────────────────────────────────
# КОНТЕКСТ ЗРИТЕЛЯ
# ─────────────────────────────────────────────────────────────────────────────

_VIEWER_CONTEXT_SQL = """
WITH me AS (
    SELECT u.id, u.role, u.department_id, u.direction_id
      FROM users u
     WHERE u.id = %(user_id)s
),
headed AS (
    SELECT d.id FROM departments d
     WHERE d.head_user_id = %(user_id)s AND d.is_active
),
my_groups AS (
    SELECT gom.group_id
      FROM group_operator_memberships gom
      JOIN groups g ON g.id = gom.group_id AND g.status = 'active'
     WHERE gom.operator_id = %(user_id)s
       AND gom.start_date <= CURRENT_DATE
       AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
    UNION
    SELECT gsm.group_id
      FROM group_supervisor_memberships gsm
      JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
     WHERE gsm.supervisor_id = %(user_id)s
       AND gsm.start_date <= CURRENT_DATE
       AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)
)
SELECT EXISTS (SELECT 1 FROM me) AS found,
       (SELECT role          FROM me),
       (SELECT department_id FROM me),
       (SELECT direction_id  FROM me),
       COALESCE((SELECT array_agg(id)       FROM headed),    '{}'),
       COALESCE((SELECT array_agg(group_id) FROM my_groups), '{}')
"""


def load_viewer_context(cursor, user_id):
    """Должность, отдел, направление и группы человека. None — нет такого.

    Свой запрос, а не wiki.queries.load_access_context, НАМЕРЕННО: тот читает
    wiki_roles, wiki_user_roles и wiki_guest_access, а окно новости обязано
    показаться и тому, у кого вики нет вовсе. Сорвись развёртывание схемы вики
    — вместе с ней молча пропали бы и новости.

    Роль вики здесь НЕ спрашивается: она нужна только потолку публикации, а его
    считают лишь пишущие роуты (news/routes.py: news_route, rights=True). На
    горячем /pending это два лишних обращения к базе из четырёх.
    """
    cursor.execute(_VIEWER_CONTEXT_SQL, {'user_id': user_id})
    row = cursor.fetchone()
    # Запрос собран из скалярных подзапросов и отдаёт строку ВСЕГДА, даже когда
    # такого пользователя нет, — отсюда отдельный признак found. Без него
    # удалённая учётка выглядела бы как сотрудник без отдела и должности.
    if not row or not row[0]:
        return None
    _found, otp_role, department_id, direction_id, headed, groups = row
    return {
        'user_id': int(user_id),
        'otp_role': otp_role,
        'department_id': department_id,
        'direction_id': direction_id,
        'headed_department_ids': [int(v) for v in (headed or [])],
        'subjects': {
            'department': sorted({int(v) for v in ([department_id] if department_id else [])}
                                 | {int(v) for v in (headed or [])}),
            'direction': [int(direction_id)] if direction_id else [],
            'group': sorted({int(v) for v in (groups or [])}),
        },
    }


def is_wiki_admin(cursor, user_id):
    """Носитель роли ВИКИ со способностью can_manage_access.

    Такому человеку вики поднимает потолок выдачи до максимума, и новости
    обязаны вести себя так же — иначе администратор вики, назначенный руками,
    раздаёт доступ ко всей базе знаний, но не может написать объявление.

    Под try/except с проверкой таблицы: раздела «Вики» в базе может не быть
    вовсе (первый запуск, откатившаяся миграция), и новости из-за этого падать
    не должны.
    """
    try:
        cursor.execute("SELECT to_regclass('public.wiki_user_roles') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return False
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM wiki_user_roles ur
                  JOIN wiki_roles r ON r.id = ur.wiki_role_id
                 WHERE ur.user_id = %s AND r.can_manage_access
            )
            """,
            (user_id,),
        )
        return bool(cursor.fetchone()[0])
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ВЫДАЧА ОКНА
# ─────────────────────────────────────────────────────────────────────────────

def pending_for_user(cursor, *, user_id, otp_role, subjects):
    """Новости, которые этому человеку сейчас показывают. Свои — не показываем.

    Порядок: обязательные раньше необязательных, внутри — по публикации. Автор
    своей новости в выдачу не попадает: он её и написал, а окно, которое
    невозможно закрыть, у самого себя — это брак, а не контроль.
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    sql = ("""
        SELECT p.id, p.title, p.body, p.is_mandatory, p.confirm_delay_seconds,
               p.published_at, u.name AS author_name, u.role AS author_role,
               d.name AS author_department, r.shown_at,
               GREATEST(0, p.confirm_delay_seconds
                        - EXTRACT(EPOCH FROM (@NOW@ - COALESCE(r.shown_at, @NOW@)))
               )::int AS remaining_seconds
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
          LEFT JOIN departments d ON d.id = p.author_department_id
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.status = 'published'
           AND (p.expires_at IS NULL OR p.expires_at > @NOW@)
           AND (p.expires_at IS NOT NULL
                OR p.published_at IS NULL
                OR p.published_at > @NOW@ - INTERVAL '@HORIZON@ days')
           AND p.author_id IS DISTINCT FROM %(user_id)s
           AND r.confirmed_at IS NULL
           AND
        """ + AUDIENCE_MATCH_FOR_VIEWER + """
         ORDER BY p.is_mandatory DESC, p.published_at, p.id
         LIMIT 20
        """)
    # @NOW@ подставляем str.replace, а не %-форматом: в тексте запроса живут
    # именованные параметры psycopg2 (%(user_id)s), и %-формат сломался бы на
    # них — тем же способом, каким он уже ронял DDL вики на комментарии с '%'.
    cursor.execute(sql.replace('@NOW@', _NOW)
                      .replace('@HORIZON@', str(SHOW_HORIZON_DAYS)), params)
    return [{
        'id': row[0],
        'title': row[1],
        'body': row[2],
        'is_mandatory': bool(row[3]),
        'confirm_delay_seconds': int(row[4] or 0),
        'published_at': row[5].isoformat() if row[5] else None,
        'author_name': row[6],
        'author_role': row[7],
        'author_department': row[8],
        'shown_at': row[9].isoformat() if row[9] else None,
        # Сколько секунд серверу ещё рано принимать подтверждение. Клиент
        # заводит свой таймер от этого числа: считать «сколько прошло» он не
        # может — время на машине сотрудника своё, а поля хранятся наивными в
        # Алматы (см. notifications/sources.py: _seconds_until).
        'remaining_seconds': int(row[10] or 0),
    } for row in cursor.fetchall()]


def mark_shown(cursor, *, news_ids, user_id):
    """Отметка «окно показали». Точка отсчёта задержки кнопки «Прочитал».

    ON CONFLICT DO NOTHING, а не перезапись: иначе перезагрузка страницы
    отматывала бы задержку назад и подтвердить новость было бы нельзя вовсе.

    Вызывающий передаёт ОДНУ новость — ту, что человек сейчас видит. Отметить
    всю очередь разом значило бы написать в журнал «открыл» о новостях, до
    которых человек ещё не дошёл, и запустить у них отсчёт кнопки, пока он
    читает первую.
    """
    if not news_ids:
        return
    cursor.execute(
        """
        INSERT INTO news_reads (news_id, user_id, shown_at)
        SELECT id, %(user_id)s, {now} FROM unnest(%(ids)s::int[]) AS id
        ON CONFLICT (news_id, user_id) DO NOTHING
        """.format(now=_NOW),
        {'ids': list(news_ids), 'user_id': user_id},
    )


def confirm_read(cursor, *, news_id, user_id, otp_role, subjects):
    """Принять «Прочитал». (status, оставшиеся секунды).

    Задержку проверяет СЕРВЕР — по своей же отметке о показе. Клиентский
    таймер это удобство: без серверной проверки подтверждение уходило бы из
    консоли мгновенно, и весь смысл задержки («нельзя пролистать за секунду»)
    держался бы на честном слове браузера. Тот же принцип, что у гейта
    «дочитал до конца» в обязательном ознакомлении вики.
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params['news_id'] = news_id
    cursor.execute(
        """
        SELECT p.is_mandatory, r.shown_at, r.confirmed_at,
               GREATEST(0, p.confirm_delay_seconds
                        - EXTRACT(EPOCH FROM ({now} - COALESCE(r.shown_at, {now})))
               )::int AS remaining
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.id = %(news_id)s
           AND p.status = 'published'
           AND
        """.format(now=_NOW) + AUDIENCE_MATCH_FOR_VIEWER,
        params,
    )
    row = cursor.fetchone()
    # Ничего не нашли — либо новости нет, либо она не адресована этому человеку,
    # либо ещё не выпущена. Отвечаем одинаково: подтверждать нечего.
    #
    # Проверка тут ОБЯЗАТЕЛЬНА, а не «на всякий случай». Роут стоит на голой
    # аутентификации (так требует постановка — окно обязано доехать и до тех,
    # у кого нет вики), поэтому без неё любой сотрудник перебором id заранее
    # «прочитывал» ещё не опубликованное объявление — и когда его выпускали,
    # окно у этого человека не показывалось уже никогда, а в журнале он стоял
    # подтвердившим.
    if row is None:
        return 'not_found', 0
    is_mandatory, shown_at, confirmed_at, remaining = row
    if confirmed_at is not None:
        return 'already', 0

    # У НЕОБЯЗАТЕЛЬНОЙ новости кнопки «Прочитал» нет вовсе — её закрывают
    # крестиком, и это закрытие и есть отметка. Гейт задержки здесь означал бы,
    # что окно, снятое с обязательности после создания, не закрывается совсем:
    # сохранённые секунды никуда не делись, а ждать их пользователю нечем.
    if not is_mandatory:
        mark_shown(cursor, news_ids=[news_id], user_id=user_id)
        cursor.execute(
            """
            UPDATE news_reads SET confirmed_at = {now}
             WHERE news_id = %(news_id)s AND user_id = %(user_id)s
               AND confirmed_at IS NULL
            """.format(now=_NOW),
            {'news_id': news_id, 'user_id': user_id},
        )
        return 'ok', 0
    if shown_at is None:
        # Подтверждение раньше отметки о показе. Так бывает штатно: /pending
        # отмечает только ПЕРВУЮ новость очереди, а окно, подтвердив её, тут же
        # рисует следующую — и человек может успеть нажать до того, как за
        # отметкой сходит перезапрос.
        #
        # Ставим отметку сейчас и смотрим на задержку. Нулевая означает, что
        # ждать нечего: отказ с «осталось 0 секунд» клиент показал бы как
        # невнятную ошибку, а кнопка при этом активна.
        mark_shown(cursor, news_ids=[news_id], user_id=user_id)
        cursor.execute("SELECT confirm_delay_seconds FROM news_posts WHERE id = %s",
                       (news_id,))
        delay_row = cursor.fetchone()
        delay = int(delay_row[0] if delay_row else 0)
        if delay > 0:
            return 'too_early', delay
    if int(remaining or 0) > 0:
        return 'too_early', int(remaining)

    cursor.execute(
        """
        UPDATE news_reads
           SET confirmed_at = {now}
         WHERE news_id = %(news_id)s AND user_id = %(user_id)s
           AND confirmed_at IS NULL
        """.format(now=_NOW),
        {'news_id': news_id, 'user_id': user_id},
    )
    return 'ok', 0


# ─────────────────────────────────────────────────────────────────────────────
# ВИТРИНА РЕДАКТОРА
# ─────────────────────────────────────────────────────────────────────────────

def list_posts(cursor, *, viewer_id, viewer_level, departments, status=None,
               limit=50, offset=0):
    """Новости, которые этот редактор вправе видеть в разделе.

    departments=None — без границы (супер-админ, администратор вики): все.
    Иначе своё плюс чужое своего отдела, но только от авторов НЕ ВЫШЕ себя:
    черновик руководителя — не материал супервайзера, ровно по тому же правилу,
    по которому новость идёт вниз, а не вверх.
    """
    params = {'viewer': viewer_id, 'viewer_level': viewer_level,
              'depts': list(departments) if departments is not None else None,
              'status': status, 'limit': limit, 'offset': offset}
    params.update(_role_params())
    author_level = news_access.role_level_sql(news_access.canon_role_sql('u.role'))
    cursor.execute(
        """
        SELECT p.id, p.title, p.status, p.is_mandatory, p.confirm_delay_seconds,
               p.published_at, p.expires_at, p.created_at, p.updated_at,
               u.name AS author_name, d.name AS author_department,
               p.author_id, u.role AS author_role,
               0 AS confirmed
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
          LEFT JOIN departments d ON d.id = p.author_department_id
         WHERE (%(status)s::text IS NULL OR p.status = %(status)s)
           AND (
                %(depts)s::int[] IS NULL
             OR p.author_id = %(viewer)s
             OR (p.author_department_id = ANY(%(depts)s::int[])
                 AND {author_level} <= %(viewer_level)s)
           )
         ORDER BY COALESCE(p.published_at, p.created_at) DESC, p.id DESC
         LIMIT %(limit)s OFFSET %(offset)s
        """.format(author_level=author_level),
        params,
    )
    rows = cursor.fetchall()

    # Счётчик ОТДЕЛЬНЫМ запросом, а не окном COUNT(*) OVER (): окно считает по
    # строкам выборки, и страница за хвостом (offset больше, чем строк) вернула
    # бы ноль — «новостей нет» вместо «на этой странице пусто».
    cursor.execute(
        """
        SELECT COUNT(*)
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
         WHERE (%(status)s::text IS NULL OR p.status = %(status)s)
           AND (
                %(depts)s::int[] IS NULL
             OR p.author_id = %(viewer)s
             OR (p.author_department_id = ANY(%(depts)s::int[])
                 AND {author_level} <= %(viewer_level)s)
           )
        """.format(author_level=author_level),
        params,
    )
    total = int(cursor.fetchone()[0])
    items = [{
        'id': row[0],
        'title': row[1],
        'status': row[2],
        'is_mandatory': bool(row[3]),
        'confirm_delay_seconds': int(row[4] or 0),
        'published_at': row[5].isoformat() if row[5] else None,
        'expires_at': row[6].isoformat() if row[6] else None,
        'created_at': row[7].isoformat() if row[7] else None,
        'updated_at': row[8].isoformat() if row[8] else None,
        'author_name': row[9],
        'author_department': row[10],
        'author_id': row[11],
        'author_role': row[12],
        # Заполняется ниже одним запросом на всю страницу: считать его
        # подзапросом по news_reads значило бы считать НЕ ТО, что показывает
        # журнал (там знаменатель — нынешние адресаты), и «Прочитали: 14» на
        # карточке спорило бы с «Подтвердили 12 из 30» под ней.
        'confirmed_count': 0,
        'audience_count': 0,
    } for row in rows]

    stats = audience_stats(cursor, [item['id'] for item in items])
    for item in items:
        addressed, confirmed = stats.get(item['id'], (0, 0))
        item['audience_count'] = addressed
        item['confirmed_count'] = confirmed
    return total, items


def audience_stats(cursor, post_ids):
    """{news_id: (адресатов сейчас, подтвердили из них)} для списка новостей.

    Одним запросом на всю страницу, а не подзапросом на строку: считается это
    ТЕМИ ЖЕ правилами, что и журнал, то есть по CTE сотрудников — тридцать
    отдельных прогонов этого CTE стоили бы дороже одного с GROUP BY.
    """
    ids = [int(value) for value in (post_ids or [])]
    if not ids:
        return {}
    params = {'ids': ids}
    params.update(_role_params())
    cursor.execute(
        "WITH " + _VIEWER_SUBJECTS_CTE + """
        SELECT p.id,
               COUNT(*)                                        AS addressed,
               COUNT(r.confirmed_at)                            AS confirmed
          FROM news_posts p
          JOIN viewers v ON TRUE
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = v.id
         WHERE p.id = ANY(%(ids)s)
           AND v.id IS DISTINCT FROM p.author_id
           AND
        """ + AUDIENCE_MATCH_FOR_REPORT + """
         GROUP BY p.id
        """,
        params,
    )
    return {int(row[0]): (int(row[1]), int(row[2])) for row in cursor.fetchall()}


def get_post(cursor, post_id):
    """Карточка новости с адресатами. None — нет такой."""
    cursor.execute(
        """
        SELECT p.id, p.title, p.body, p.status, p.is_mandatory,
               p.confirm_delay_seconds, p.published_at, p.expires_at,
               p.author_id, p.author_department_id, p.audience_max_role_level,
               u.name, d.name, p.created_at, p.updated_at, u.role
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
          LEFT JOIN departments d ON d.id = p.author_department_id
         WHERE p.id = %s
        """,
        (post_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        'id': row[0],
        'title': row[1],
        'body': row[2],
        'status': row[3],
        'is_mandatory': bool(row[4]),
        'confirm_delay_seconds': int(row[5] or 0),
        'published_at': row[6].isoformat() if row[6] else None,
        'expires_at': row[7].isoformat() if row[7] else None,
        'author_id': row[8],
        'author_department_id': row[9],
        'audience_max_role_level': row[10],
        'author_name': row[11],
        'author_department': row[12],
        'created_at': row[13].isoformat() if row[13] else None,
        'updated_at': row[14].isoformat() if row[14] else None,
        # Должность автора — ею меряется периметр карточки и право снять с
        # показа (news/routes.py: _may_read_post, _may_take_down).
        'author_role': row[15],
        'audience': audience_rules(cursor, post_id),
    }


def audience_rules(cursor, post_id):
    """Адресаты новости с человеческими именами — для формы и для карточки."""
    cursor.execute(
        """
        SELECT r.id, r.subject_type, r.subject_id, r.subject_role, r.min_role_level,
               COALESCE(dep.name, dir.name, g.name, usr.name) AS subject_name
          FROM news_audience_rules r
          LEFT JOIN departments dep ON r.subject_type = 'department' AND dep.id = r.subject_id
          LEFT JOIN directions  dir ON r.subject_type = 'direction'  AND dir.id = r.subject_id
          LEFT JOIN groups        g ON r.subject_type = 'group'      AND g.id  = r.subject_id
          LEFT JOIN users       usr ON r.subject_type = 'user'       AND usr.id = r.subject_id
         WHERE r.news_id = %s
         ORDER BY r.id
        """,
        (post_id,),
    )
    return [{
        'id': row[0],
        'subject_type': row[1],
        'subject_id': row[2],
        'subject_role': row[3],
        'min_role_level': row[4],
        'subject_name': row[5],
    } for row in cursor.fetchall()]


def subject_departments(cursor, rules):
    """{(subject_type, subject_id): department_id} — для проверки границы отдела.

    Одним запросом на все виды адресата: отдельный SELECT на каждую строку
    формы превратил бы сохранение в десяток обращений к базе.
    """
    wanted = [(r.get('subject_type'), r.get('subject_id')) for r in rules
              if r.get('subject_type') in ('department', 'direction', 'group', 'user')
              and r.get('subject_id') is not None]
    if not wanted:
        return {}
    ids = [int(subject_id) for _kind, subject_id in wanted]
    cursor.execute(
        """
        SELECT 'department', id, id FROM departments WHERE id = ANY(%(ids)s)
        UNION ALL
        SELECT 'direction', id, department_id FROM directions WHERE id = ANY(%(ids)s)
        UNION ALL
        SELECT 'group', id, department_id FROM groups WHERE id = ANY(%(ids)s)
        UNION ALL
        SELECT 'user', id, department_id FROM users WHERE id = ANY(%(ids)s)
        """,
        {'ids': ids},
    )
    return {(row[0], int(row[1])): row[2] for row in cursor.fetchall()}


def roles_of_users(cursor, user_ids):
    """{user_id: role} — потолок должности у адресата-человека."""
    ids = [int(value) for value in user_ids if value is not None]
    if not ids:
        return {}
    cursor.execute("SELECT id, role FROM users WHERE id = ANY(%s)", (ids,))
    return {int(row[0]): row[1] for row in cursor.fetchall()}


# ─────────────────────────────────────────────────────────────────────────────
# ЗАПИСЬ
# ─────────────────────────────────────────────────────────────────────────────

def create_post(cursor, *, title, body, author_id, author_department_id,
                is_mandatory, confirm_delay_seconds, expires_at, created_by):
    cursor.execute(
        """
        INSERT INTO news_posts (title, body, author_id, author_department_id,
                                status, is_mandatory, confirm_delay_seconds,
                                expires_at, created_by)
        VALUES (%(title)s, %(body)s, %(author)s, %(dept)s, 'draft',
                %(mandatory)s, %(delay)s, %(expires)s, %(created_by)s)
        RETURNING id
        """,
        {'title': title, 'body': body, 'author': author_id,
         'dept': author_department_id, 'mandatory': is_mandatory,
         'delay': confirm_delay_seconds, 'expires': expires_at,
         'created_by': created_by},
    )
    return int(cursor.fetchone()[0])


def update_post(cursor, *, post_id, title, body, is_mandatory,
                confirm_delay_seconds, expires_at):
    cursor.execute(
        """
        UPDATE news_posts
           SET title = %(title)s, body = %(body)s, is_mandatory = %(mandatory)s,
               confirm_delay_seconds = %(delay)s, expires_at = %(expires)s,
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': post_id, 'title': title, 'body': body, 'mandatory': is_mandatory,
         'delay': confirm_delay_seconds, 'expires': expires_at},
    )


def set_audience(cursor, *, post_id, rules, audience_max_role_level=None):
    """Полная замена адресатов. Частичной правки у набора нет намеренно:
    «кому ушла новость» — один ответ, и собирать его из добавленных и удалённых
    строк значило бы держать два состояния одного списка.

    Вместе с набором переписывается и ПОТОЛОК: он принадлежит тому, кто
    адресатов назначил. Иначе директор, поправивший адресатов у чужой
    опубликованной новости, получил бы супервайзерский потолок 10 — его правка
    молча не дошла бы ни до кого выше оператора.
    """
    if audience_max_role_level is not None:
        cursor.execute(
            "UPDATE news_posts SET audience_max_role_level = %s WHERE id = %s",
            (audience_max_role_level, post_id))
    cursor.execute("DELETE FROM news_audience_rules WHERE news_id = %s", (post_id,))
    for rule in rules:
        cursor.execute(
            """
            INSERT INTO news_audience_rules
                   (news_id, subject_type, subject_id, subject_role, min_role_level)
            VALUES (%(news)s, %(type)s, %(id)s, %(role)s, %(min_level)s)
            """,
            {'news': post_id,
             'type': rule.get('subject_type'),
             'id': rule.get('subject_id'),
             'role': rule.get('subject_role'),
             'min_level': rule.get('min_role_level')},
        )


def publish_post(cursor, *, post_id, audience_max_role_level):
    """Выпуск новости. Потолок адресата фиксируется ЗДЕСЬ — снимком должности
    автора на момент выпуска: повышение автора завтра не должно расширять круг
    тех, кому новость уже ушла."""
    cursor.execute(
        """
        UPDATE news_posts
           SET status = 'published',
               audience_max_role_level = %(ceiling)s,
               -- Дату выпуска ставим заново, если новость сейчас НЕ на показе:
               -- снятую и выпущенную повторно человек видит впервые, и подпись
               -- «сегодня, 09:14» обязана говорить про этот раз, а не про
               -- прошлый месяц. Повторный publish уже опубликованной (правка
               -- через форму) дату не двигает.
               published_at = CASE WHEN status = 'published'
                                   THEN published_at ELSE {now} END,
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': post_id, 'ceiling': audience_max_role_level},
    )


def set_status(cursor, *, post_id, status):
    cursor.execute(
        """
        UPDATE news_posts SET status = %(status)s, updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': post_id, 'status': status},
    )


def delete_post(cursor, post_id):
    cursor.execute("DELETE FROM news_posts WHERE id = %s", (post_id,))


# ─────────────────────────────────────────────────────────────────────────────
# ЖУРНАЛ: кто прочитал, кто нет
# ─────────────────────────────────────────────────────────────────────────────

def read_report(cursor, post_id):
    """Адресаты новости с отметками показа и подтверждения.

    Круг адресатов считается ТЕМИ ЖЕ правилами, что и выдача окна
    (AUDIENCE_MATCH_TEMPLATE) — иначе журнал показывал бы не тех, кто видел
    окно. Автор из списка исключён по той же причине, что и из выдачи.

    Плюс к нынешним адресатам — ВСЕ, у кого есть отметка по этой новости, даже
    если человек уже уволен или переведён в другой отдел. Журнал отвечает на
    вопрос «был ли сотрудник проинформирован», и его задают как раз при разборе,
    то есть задним числом. Фильтр по status='working' в одиночку стирал такого
    человека целиком: он исчезал и из «подтвердили», и из «из скольких», и
    выходило, что предупреждения он не получал. Признак `in_audience` говорит,
    адресована ли ему новость СЕЙЧАС.
    """
    params = {'post_id': post_id}
    params.update(_role_params())
    cursor.execute(
        "WITH " + _VIEWER_SUBJECTS_CTE + """,
        addressed AS (
            SELECT v.id, v.name, v.role, v.department_name
              FROM news_posts p
              JOIN viewers v ON TRUE
             WHERE p.id = %(post_id)s
               AND v.id IS DISTINCT FROM p.author_id
               AND
        """ + AUDIENCE_MATCH_FOR_REPORT + """
        ),
        -- Отметки ИМЕННО ЭТОЙ новости, отобранные ДО соединения. Условие
        -- `r.news_id = ...` в ON у FULL JOIN не работает: несовпавшая сторона
        -- приносит с собой отметки по ЧУЖИМ новостям, и журнал распухает
        -- дублями и посторонними людьми. Поймано прогоном на живой базе.
        reads AS (
            SELECT user_id, shown_at, confirmed_at
              FROM news_reads
             WHERE news_id = %(post_id)s
        )
        SELECT COALESCE(a.id, u.id)                AS user_id,
               COALESCE(a.name, u.name)            AS name,
               COALESCE(a.role, u.role)            AS role,
               COALESCE(a.department_name, d.name) AS department_name,
               r.shown_at, r.confirmed_at,
               (a.id IS NOT NULL)                  AS in_audience
          FROM addressed a
          FULL JOIN reads r ON r.user_id = a.id
          LEFT JOIN users u ON u.id = r.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE COALESCE(a.id, r.user_id) IS NOT NULL
         ORDER BY (r.confirmed_at IS NULL) DESC, COALESCE(a.name, u.name)
        """,
        params,
    )
    return [{
        'user_id': row[0],
        'name': row[1],
        'role': row[2],
        'department_name': row[3],
        'shown_at': row[4].isoformat() if row[4] else None,
        'confirmed_at': row[5].isoformat() if row[5] else None,
        # Адресована ли новость этому человеку СЕЙЧАС. False — он подтвердил её
        # когда-то, а потом уволился или сменил отдел; из знаменателя «из
        # скольких» такой не считается, но из журнала не пропадает.
        'in_audience': bool(row[6]),
    } for row in cursor.fetchall()]


def audience_size(cursor, post_id):
    """Сколько человек под адресатами новости. Для подписи «12 из 30»."""
    params = {'post_id': post_id}
    params.update(_role_params())
    cursor.execute(
        "WITH " + _VIEWER_SUBJECTS_CTE + """
        SELECT COUNT(*)
          FROM news_posts p
          JOIN viewers v ON TRUE
         WHERE p.id = %(post_id)s
           AND v.id IS DISTINCT FROM p.author_id
           AND
        """ + AUDIENCE_MATCH_FOR_REPORT,
        params,
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def subject_catalog(cursor, department_ids=None):
    """Справочники адресата: отделы, направления, группы.

    Свой запрос, а НЕ wiki_structure.subject_catalog, хотя тот отвечает почти
    на тот же вопрос. Причина не в форме, а в том, что он делает UNION с
    `wiki_roles`: на стенде без вики и на проде, где схема вики не применилась,
    он падает — то есть раздел «Новости» умирал бы от чужой миграции. Роль вики
    новостям и не адресат: у сотрудника без вики её нет вовсе.

    department_ids=None — без границы отдела (директор, администратор вики).
    С границей справочник сужается до своего отдела: предлагать в форме то,
    что сервер потом отвергнет, — значит обещать невыполнимое.
    """
    bounded = department_ids is not None
    cursor.execute(
        """
        SELECT 'department' AS kind, id, name FROM departments
         WHERE is_active AND (%(depts)s::int[] IS NULL OR id = ANY(%(depts)s::int[]))
        UNION ALL
        SELECT 'direction', id, name FROM directions
         WHERE is_active
           AND (%(depts)s::int[] IS NULL OR department_id = ANY(%(depts)s::int[]))
        UNION ALL
        SELECT 'group', id, name FROM groups
         WHERE status = 'active'
           AND (%(depts)s::int[] IS NULL OR department_id = ANY(%(depts)s::int[]))
         ORDER BY 1, 3
        """,
        {'depts': list(department_ids) if bounded else None},
    )
    catalog = {'department': [], 'direction': [], 'group': []}
    for kind, ident, name in cursor.fetchall():
        catalog[kind].append({'id': ident, 'name': name})
    return catalog


def targetable_people(cursor, *, max_role_level, department_ids=None):
    """Сотрудники, которым этот человек вправе адресовать новость поимённо."""
    return wiki_structure.grantable_people(
        cursor, max_role_level=max_role_level, department_ids=department_ids)


def targetable_roles(ceiling):
    """Должности, которые автор вправе выбрать адресатом. Пустой список у того,
    кому границей отдела роль недоступна вовсе (см. may_target_subject)."""
    return [{'code': code, 'level': level}
            for code, level in sorted(wiki_access.ROLE_LEVELS.items(), key=lambda kv: kv[1])
            if ceiling is not None and level <= ceiling]
