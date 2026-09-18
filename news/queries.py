# -*- coding: utf-8 -*-
"""SQL-слой раздела «Новости».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и ничего не знают
про пул и транзакции — их открывает вызывающий, как в wiki/queries.py и call_qa.
"""

import html
import json

from wiki import access as wiki_access
from wiki import structure as wiki_structure

from . import access as news_access
# Шаблон адресата берём СЕЛЕКТОРОМ, а не константой: у него две формы — с
# границей пространства и без неё, пока её таблицу не принёс пакет вики.
from .access import ROLE_CANON, SQL_ROLE_LEVELS, report_match, viewer_match
# schema.py ничего не импортирует из пакета — цикла нет.
from .schema import LOOSE_PHOTO_TTL_HOURS, MAX_PHOTOS_PER_POST

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

def _pass_columns_sql(with_pass):
    """Колонки прохождения (#342) для выборок с `p` = news_posts и `r` = news_reads.

    Без развёрнутых колонок — литералы того же вида: тест обязателен (так было
    до задачи), тренажёра нет, ничего не пройдено. Запрос не падает на
    отсутствующей колонке, и форма строки одна при любой готовности схемы.
    """
    if with_pass:
        return ("p.pass_required, p.trainer_key, "
                "r.quiz_passed_at, r.trainer_passed_at")
    return ("TRUE AS pass_required, NULL::varchar AS trainer_key, "
            "NULL::timestamp AS quiz_passed_at, NULL::timestamp AS trainer_passed_at")


def _space_filter(with_space):
    """Условие «новость этого пространства» для выборок с `p` = news_posts.

    Требует параметра %(space)s (NULL — про пространство не спрашивали).
    Пустая строка, пока колонки нет вовсе (schema.space_ready): раздел тогда
    работает как до задачи, а не отвечает пятисоткой на несуществующую колонку.

    НИЧЬЯ новость (space_id IS NULL) видна из ЛЮБОГО пространства намеренно.
    Так лежат объявления, выпущенные до этой границы и пришедшие мимо вкладки,
    и спрятать их значило бы оставить обязательное окно висеть у людей без
    единого редактора, который вправе его снять.
    """
    if not with_space:
        return ''
    return """
           AND (%(space)s::int IS NULL
                OR p.space_id IS NULL
                OR p.space_id = %(space)s::int)
        """


def _channel_filter(channel):
    """Условие «объявление этого канала» для выборок с `p` = news_posts.

    Пустая строка, когда канал не назвали: колонки ещё нет (schema.channel_ready)
    или спрашивают всё сразу. Требует параметра %(channel)s.
    """
    if not channel:
        return ''
    return 'AND p.channel = %(channel)s'


def pending_for_user(cursor, *, user_id, otp_role, subjects, with_photos=False,
                     with_quiz=False, with_pass=False, with_space=False,
                     channel=None):
    """Новости, которые этому человеку сейчас показывают. Свои — не показываем.

    Порядок: обязательные раньше необязательных, внутри — по публикации. Автор
    своей новости в выдачу не попадает: он её и написал, а окно, которое
    невозможно закрыть, у самого себя — это брак, а не контроль.

    channel — куда спрашивают: 'icore' спрашивает окно портала, 'oktell' —
    программа поверх клиента АТС (oktell_guard/routes.py). None — колонки канала
    ещё нет (schema.channel_ready), и очередь собирается как до неё: одна на
    оба места. Спрашивающий обязан назвать себя сам — выбери мы канал ЗДЕСЬ по
    умолчанию, объявление для АТС молча уехало бы в портал.

    with_photos=False — таблица кадров ещё не развёрнута (см. schema.photos_ready).
    Флагом, а не проверкой внутри: этот запрос дёргает каждый вошедший в портал,
    и спрашивать про существование таблицы на каждый вызов означало бы третье
    обращение к базе ради ответа, который не меняется.

    КАДРЫ ПОДШИТЫ ВСЕЙ ОЧЕРЕДИ, а не только первой новости. Соблазн есть — окно
    показывает по одной, — но фронт держит очередь целиком и рисует вторую
    новость НЕМЕДЛЕННО из уже приехавшего ответа, не дожидаясь сети
    (NewsOfDayModal: dropCurrent). Пришли кадры только у головы — вторая
    новость показалась бы без фотографий, и навсегда.
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params['channel'] = channel
    # Автора, его должность, отдел и время выпуска окно больше НЕ показывает
    # (решение владельца 01.09.2026), поэтому их здесь и не собираем: это два
    # LEFT JOIN на запросе, который дёргает каждый вошедший в портал и каждая
    # открытая вкладка на каждый тычок канала. Автор и время остались там, где
    # по ним работают, — в списке и журнале редактора.
    sql = ("""
        SELECT p.id, p.title, p.body, p.is_mandatory, p.confirm_delay_seconds,
               r.shown_at,
               GREATEST(0, p.confirm_delay_seconds
                        - EXTRACT(EPOCH FROM (@NOW@ - COALESCE(r.shown_at, @NOW@)))
               )::int AS remaining_seconds,
               -- Наружу не отдаётся (окно не показывает время публикации), но
               -- обязано быть в CTE: по нему сортирует внешний запрос с
               -- кадрами. Без него — «column q.published_at does not exist».
               p.published_at,
               """ + _pass_columns_sql(with_pass) + """
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.status = 'published'
           AND (p.expires_at IS NULL OR p.expires_at > @NOW@)
           AND (p.expires_at IS NOT NULL
                OR p.published_at IS NULL
                OR p.published_at > @NOW@ - INTERVAL '@HORIZON@ days')
           AND p.author_id IS DISTINCT FROM %(user_id)s
           AND r.confirmed_at IS NULL
           """ + _channel_filter(channel) + """
           AND
        """ + viewer_match(with_space) + """
         ORDER BY p.is_mandatory DESC, p.published_at, p.id
         LIMIT 20
        """)
    # Обёртка — ВСЕГДА, а не только при кадрах или тесте: строка ответа тогда
    # одного вида при любой готовности схемы, и разбор ниже не гадает, на каком
    # месте какая колонка. Цена — ноль: CTE с LIMIT планировщик считает один раз.
    if with_photos:
        params['max_photos'] = MAX_PHOTOS_PER_POST
    # Кадры СКАЛЯРНЫМ подзапросом, а не джойном, и СНАРУЖИ лимита.
    #
    # Джойн испортил бы две вещи сразу. Во-первых, размножил бы строку
    # новости на число кадров — вместе с телом объявления в каждой копии, а
    # это самый горячий запрос портала. Во-вторых, и это хуже, он отдал бы
    # LIMIT 20 КАРТИНКАМ: две новости по десять кадров съели бы всю очередь,
    # и третье объявление молча не доехало бы до окна.
    #
    # Снаружи CTE, а не в исходном SELECT-листе: подзапрос с LIMIT в
    # родителя не разворачивается, поэтому агрегат считается РОВНО для
    # отобранных двадцати, а не для каждого кандидата до сортировки.
    #
    # bucket и blob_path здесь есть — без них нечего подписывать, — но
    # наружу не уходят: роут гонит список через news_photos.sign_urls, а тот
    # собирает новый словарь по белому списку ключей.
    photos = ("""COALESCE((
               SELECT json_agg(json_build_object(
                          'id', f.id, 'bucket', f.bucket,
                          'blob_path', f.blob_path, 'content_type', f.content_type,
                          'width', f.width, 'height', f.height,
                          'file_size', f.file_size, 'sort_order', f.sort_order)
                      ORDER BY f.sort_order, f.id)
                 FROM (SELECT id, bucket, blob_path, content_type,
                              width, height, file_size, sort_order
                         FROM news_photos
                        WHERE news_id = q.id
                        ORDER BY sort_order, id
                        LIMIT %(max_photos)s) f
           ), '[]'::json)""" if with_photos else "'[]'::json")
    # Тест в окне — тем же приёмом и по той же причине: скалярно и снаружи
    # лимита. ВЕРНОГО ВАРИАНТА здесь нет и быть не должно: окно показывает
    # только формулировки, а сверку делает сервер (confirm_read). Отдай мы
    # индекс верного варианта, тест проходился бы из вкладки «Сеть».
    quiz = ("""COALESCE((
               SELECT json_agg(json_build_object(
                          'id', z.id, 'prompt', z.prompt, 'options', z.options)
                      ORDER BY z.position, z.id)
                 FROM news_quiz_questions z
                WHERE z.news_id = q.id
           ), '[]'::json)""" if with_quiz else "'[]'::json")
    sql = ("""
    WITH queue AS (""" + sql + """)
    SELECT q.id, q.title, q.body, q.is_mandatory, q.confirm_delay_seconds,
           q.shown_at, q.remaining_seconds,
           """ + photos + """ AS photos,
           """ + quiz + """ AS quiz,
           q.pass_required, q.trainer_key,
           q.quiz_passed_at IS NOT NULL, q.trainer_passed_at IS NOT NULL
      FROM queue q
     ORDER BY q.is_mandatory DESC, q.published_at, q.id
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
        'shown_at': row[5].isoformat() if row[5] else None,
        # Сколько секунд серверу ещё рано принимать подтверждение. Клиент
        # заводит свой таймер от этого числа: считать «сколько прошло» он не
        # может — время на машине сотрудника своё, а поля хранятся наивными в
        # Алматы (см. notifications/sources.py: _seconds_until).
        'remaining_seconds': int(row[6] or 0),
        # Сырые строки кадров: подписывает и чистит их роут. Здесь они ещё с
        # bucket/blob_path — наружу в таком виде не уходят.
        'photos': (row[7] or []) if with_photos else [],
        # Формулировки и варианты, без верных ответов (см. выше).
        'quiz': (row[8] or []) if with_quiz else [],
        # Задача #342: держит ли прохождение кнопку, какой тренажёр и что уже
        # пройдено. Пройденное нужно окну после перезагрузки страницы: иначе
        # человек, прошедший тренажёр, увидел бы его снова непройденным.
        'pass_required': bool(row[9]),
        'trainer_key': row[10],
        'quiz_passed': bool(row[11]),
        'trainer_passed': bool(row[12]),
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


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ В ОКНЕ («Вопросы операторов», задача #321)
#
# Стоит ПОСЛЕ mark_shown и отдельными функциями, а не внутри выдачи: /pending
# забирает формулировки тем же единственным запросом (pending_for_user), а
# верные ответы читаются только здесь — при подтверждении и в карточке
# редактора.
# ─────────────────────────────────────────────────────────────────────────────

def quiz_answer_key(cursor, news_id):
    """[(id вопроса, индекс верного варианта)] — только для сверки на сервере."""
    cursor.execute(
        """
        SELECT id, correct_index FROM news_quiz_questions
         WHERE news_id = %s ORDER BY position, id
        """,
        (news_id,),
    )
    return [(int(row[0]), int(row[1])) for row in cursor.fetchall()]


def post_quiz(cursor, post_id):
    """Тест новости С ВЕРНЫМИ ОТВЕТАМИ — для витрины редактора, не для окна."""
    cursor.execute(
        """
        SELECT id, prompt, options, correct_index FROM news_quiz_questions
         WHERE news_id = %s ORDER BY position, id
        """,
        (post_id,),
    )
    return [{'id': int(row[0]), 'prompt': row[1], 'options': list(row[2] or []),
             'correct': int(row[3])} for row in cursor.fetchall()]


def set_quiz(cursor, *, post_id, quiz):
    """Полная замена теста. quiz уже прошёл news_access.normalize_quiz."""
    cursor.execute('DELETE FROM news_quiz_questions WHERE news_id = %s', (post_id,))
    for position, item in enumerate(quiz or ()):
        cursor.execute(
            """
            INSERT INTO news_quiz_questions (news_id, position, prompt, options,
                                             correct_index)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            """,
            (post_id, position, item['prompt'],
             json.dumps(item['options'], ensure_ascii=False), item['correct']),
        )


def confirm_read(cursor, *, news_id, user_id, otp_role, subjects, answers=None,
                 with_quiz=False, with_pass=False, with_space=False):
    """Принять «Прочитал». (status, подробность).

    Подробность — оставшиеся секунды у 'too_early' и id вопросов с неверным
    ответом у 'quiz_wrong'. 'trainer_pending' — обязательный тренажёр ещё не
    пройден (задача #342).

    Задержку проверяет СЕРВЕР — по своей же отметке о показе. Клиентский
    таймер это удобство: без серверной проверки подтверждение уходило бы из
    консоли мгновенно, и весь смысл задержки («нельзя пролистать за секунду»)
    держался бы на честном слове браузера. Тот же принцип, что у гейта
    «дочитал до конца» в обязательном ознакомлении вики. Тест в окне проверяется
    здесь же и по той же причине: верных ответов у клиента нет вовсе.
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params['news_id'] = news_id
    cursor.execute(
        """
        SELECT p.is_mandatory, r.shown_at, r.confirmed_at,
               GREATEST(0, p.confirm_delay_seconds
                        - EXTRACT(EPOCH FROM ({now} - COALESCE(r.shown_at, {now})))
               )::int AS remaining,
               {passes}
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.id = %(news_id)s
           AND p.status = 'published'
           AND
        """.format(now=_NOW, passes=_pass_columns_sql(with_pass)) + viewer_match(with_space),
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
    (is_mandatory, shown_at, confirmed_at, remaining,
     pass_required, trainer_key, quiz_passed_at, trainer_passed_at) = row
    if confirmed_at is not None:
        return 'already', 0

    # Новость с ОБЯЗАТЕЛЬНЫМ прохождением обязательна ВСЕГДА, что бы ни стояло
    # в записи: крестик необязательной подтверждал бы прочтение без единого
    # ответа, и тест обходился бы одним нажатием. Необязательный тест
    # (задача #342) окно не держит — по нему и обязательность не навязывается.
    answer_key = quiz_answer_key(cursor, news_id) if with_quiz else []
    if news_access.must_pass(pass_required=pass_required, has_quiz=bool(answer_key),
                             has_trainer=bool(trainer_key)):
        is_mandatory = True

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

    left = news_access.outstanding_passes(
        pass_required=pass_required, has_quiz=bool(answer_key),
        has_trainer=bool(trainer_key), quiz_passed=quiz_passed_at is not None,
        trainer_passed=trainer_passed_at is not None)
    if 'trainer' in left:
        # Отметку тренажёра ставит окно, когда урок дошёл до конца
        # (mark_trainer_passed). Без неё подтверждение не принимается — иначе
        # обязательный тренажёр обходился бы кнопкой из консоли.
        return 'trainer_pending', 0
    quiz_passed_now = False
    if 'quiz' in left:
        wrong = news_access.quiz_mistakes(answer_key, answers)
        if wrong:
            # Ошибку называем по вопросу, а верный вариант — нет: окно подсветит
            # вопрос, и человек перечитает новость, а не подберёт ответ перебором.
            return 'quiz_wrong', wrong
        quiz_passed_now = True

    cursor.execute(
        """
        UPDATE news_reads
           SET confirmed_at = {now}{quiz_mark}
         WHERE news_id = %(news_id)s AND user_id = %(user_id)s
           AND confirmed_at IS NULL
        """.format(now=_NOW,
                   # Верные ответы при подтверждении — это и есть пройденный
                   # тест: журнал обязан показать его так же, как пройденный
                   # отдельной кнопкой «Проверить».
                   quiz_mark=(', quiz_passed_at = COALESCE(quiz_passed_at, %s)' % _NOW
                              if quiz_passed_now and with_pass else '')),
        {'news_id': news_id, 'user_id': user_id},
    )
    return 'ok', 0


# ─────────────────────────────────────────────────────────────────────────────
# ПРОХОЖДЕНИЕ И ЛЕНТА ЧИТАТЕЛЯ (задача #342)
#
# «После публикации новости оператор должен иметь возможность открыть и пройти
# прикреплённый тест». Проходят его в окне и во вкладке «Новости» вики — она
# открыта всем (решение владельца 17.09.2026): читателю показываются только
# адресованные ему новости, редактору — ещё и управление.
#
# Периметр у всех функций раздела один — опубликованная и адресованная этому
# человеку (AUDIENCE_MATCH_FOR_VIEWER). Роуты стоят на голой аутентификации,
# как /read, поэтому проверка здесь обязательна: без неё перебором id можно
# было бы прочитать чужой черновик или «пройти» ещё не выпущенный тест.
# ─────────────────────────────────────────────────────────────────────────────

# Первые знаки текста для строки ленты. Считает Postgres: тело объявления в
# питон ради двух строк превью не тянем.
FEED_PREVIEW_LENGTH = 220


def _viewer_post(cursor, *, news_id, user_id, otp_role, subjects, with_pass,
                 with_space=False):
    """Опубликованная новость, адресованная человеку, со своими отметками. None — нет.

    Своя новость сюда НЕ попадает: автор её не получает (как и окно).
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params['news_id'] = news_id
    cursor.execute(
        """
        SELECT p.id, {passes}, r.confirmed_at
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.id = %(news_id)s
           AND p.status = 'published'
           AND p.author_id IS DISTINCT FROM %(user_id)s
           AND
        """.format(passes=_pass_columns_sql(with_pass)) + viewer_match(with_space),
        params,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {'id': row[0], 'pass_required': bool(row[1]), 'trainer_key': row[2],
            'quiz_passed_at': row[3], 'trainer_passed_at': row[4], 'confirmed_at': row[5]}


def _mark_pass(cursor, *, news_id, user_id, column):
    """Отметка «прошёл» в строке журнала. Первая остаётся — повтор её не двигает.

    Строки может ещё не быть: необязательную новость проходят и во вкладке,
    куда окно не заглядывало (горизонт показа, срок новости). Тогда строка
    заводится здесь же — отметка «открыл» в ней честная: человек новость открыл.
    """
    assert column in ('quiz_passed_at', 'trainer_passed_at')
    cursor.execute(
        """
        INSERT INTO news_reads (news_id, user_id, shown_at, {col})
        VALUES (%(news_id)s, %(user_id)s, {now}, {now})
        ON CONFLICT (news_id, user_id)
        DO UPDATE SET {col} = COALESCE(news_reads.{col}, EXCLUDED.{col})
        """.format(col=column, now=_NOW),
        {'news_id': news_id, 'user_id': user_id},
    )


def pass_quiz(cursor, *, news_id, user_id, otp_role, subjects, answers,
              with_space=False):
    """«Проверить» у теста новости. (status, подробность).

    'not_found' — новости нет или она не этому человеку; 'no_quiz' — теста у
    неё нет; 'quiz_wrong' — id вопросов с ошибкой; 'ok' — тест пройден и
    отмечен в журнале. Подтверждение прочтения здесь НЕ ставится: это другой
    вопрос, и отвечает на него кнопка «Прочитал».
    """
    post = _viewer_post(cursor, news_id=news_id, user_id=user_id, otp_role=otp_role,
                        subjects=subjects, with_pass=True, with_space=with_space)
    if post is None:
        return 'not_found', 0
    answer_key = quiz_answer_key(cursor, news_id)
    if not answer_key:
        return 'no_quiz', 0
    wrong = news_access.quiz_mistakes(answer_key, answers)
    if wrong:
        return 'quiz_wrong', wrong
    _mark_pass(cursor, news_id=news_id, user_id=user_id, column='quiz_passed_at')
    return 'ok', 0


def mark_trainer_passed(cursor, *, news_id, user_id, otp_role, subjects,
                        with_space=False):
    """Тренажёр новости дошёл до конца. (status, 0).

    Итог урока присылает браузер: сценарий живёт в коде фронта, и сервер
    проверить прохождение шаг за шагом не может — ровно так же пишется и
    статистика тренажёров вики (wiki_trainer_runs). Граница здесь та, что есть:
    новость опубликована, адресована этому человеку и тренажёр у неё правда есть.
    """
    post = _viewer_post(cursor, news_id=news_id, user_id=user_id, otp_role=otp_role,
                        subjects=subjects, with_pass=True, with_space=with_space)
    if post is None:
        return 'not_found', 0
    if not post['trainer_key']:
        return 'no_trainer', 0
    _mark_pass(cursor, news_id=news_id, user_id=user_id, column='trainer_passed_at')
    return 'ok', 0


def feed_for_user(cursor, *, user_id, otp_role, subjects, limit=20, offset=0,
                  with_photos=False, with_quiz=False, with_pass=False,
                  with_space=False, space_id=None):
    """Лента «мои новости»: опубликованные и адресованные человеку. (всего, строки).

    Свежие сверху. Без тела и без кадров — строка списка отвечает на «что это
    за новость»; целиком карточку отдаёт feed_post, когда её открыли. Горизонт
    показа и срок новости ленту НЕ режут: они снимают окно, а не память о
    новости — открыть её и пройти тест человек вправе и через месяц.

    space_id — лента ОТКРЫТА В ЭТОМ ПРОСТРАНСТВЕ. Граница адресата уже не
    пустит сюда чужую компанию, но человека, работающего в обеих виках, лента
    иначе показывала бы одинаково в каждой: вкладка «Новости» в «Тез» — это
    новости Тез, а не всё, что человеку когда-либо адресовали.
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params.update({'limit': int(limit), 'offset': int(offset), 'space': space_id})
    where = """
         WHERE p.status = 'published'
           AND p.author_id IS DISTINCT FROM %(user_id)s
           AND
        """ + viewer_match(with_space) + _space_filter(with_space)
    cursor.execute(
        """
        SELECT p.id, p.title, p.is_mandatory, p.published_at,
               -- Блочные теги — пробелом (абзацы не слипаются), строчные —
               -- ничем: «<strong>Sapar</strong>.» не должно стать «Sapar .».
               left(regexp_replace(
                        regexp_replace(p.body, '</?(p|li|ul|ol|h[1-6]|br|div|blockquote)[^>]*>',
                                       ' ', 'gi'),
                        '<[^>]+>', '', 'g'), {preview}) AS preview,
               r.confirmed_at,
               {photo_count} AS photo_count,
               {quiz_count} AS quiz_count,
               {passes}
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
        """.format(
            preview=FEED_PREVIEW_LENGTH * 2,
            photo_count=("(SELECT COUNT(*) FROM news_photos f WHERE f.news_id = p.id)"
                         if with_photos else "0"),
            quiz_count=("(SELECT COUNT(*) FROM news_quiz_questions z WHERE z.news_id = p.id)"
                        if with_quiz else "0"),
            passes=_pass_columns_sql(with_pass),
        ) + where + """
         ORDER BY p.published_at DESC NULLS LAST, p.id DESC
         LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    rows = cursor.fetchall()
    # Счётчик отдельным запросом, а не окном COUNT(*) OVER (): страница за
    # хвостом вернула бы ноль вместо «на этой странице пусто».
    cursor.execute("SELECT COUNT(*) FROM news_posts p" + where, params)
    total = int(cursor.fetchone()[0])
    return total, [{
        'id': row[0],
        'title': row[1],
        'is_mandatory': bool(row[2]),
        'published_at': row[3].isoformat() if row[3] else None,
        'preview': _plain_preview(row[4]),
        'confirmed': row[5] is not None,
        'photo_count': int(row[6] or 0),
        'quiz_count': int(row[7] or 0),
        'pass_required': bool(row[8]),
        'trainer_key': row[9],
        'quiz_passed': row[10] is not None,
        'trainer_passed': row[11] is not None,
    } for row in rows]


def _plain_preview(text):
    """Превью без сущностей HTML и лишних пробелов, по границе слова."""
    plain = ' '.join(html.unescape(str(text or '')).split())
    if len(plain) <= FEED_PREVIEW_LENGTH:
        return plain
    cut = plain[:FEED_PREVIEW_LENGTH].rsplit(' ', 1)[0]
    return cut.rstrip(' ,.;:—-') + '…'


def feed_post(cursor, *, news_id, user_id, otp_role, subjects, with_photos=False,
              with_quiz=False, with_pass=False, with_space=False):
    """Карточка новости из ленты — тем же периметром, что и лента. None — не его.

    Тест — БЕЗ верных ответов, как в окне: сверяет сервер (pass_quiz).
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params['news_id'] = news_id
    cursor.execute(
        """
        SELECT p.id, p.title, p.body, p.is_mandatory, p.published_at, r.confirmed_at,
               {quiz} AS quiz,
               {passes}
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.id = %(news_id)s
           AND p.status = 'published'
           AND p.author_id IS DISTINCT FROM %(user_id)s
           AND
        """.format(
            quiz=("""COALESCE((
                       SELECT json_agg(json_build_object(
                                  'id', z.id, 'prompt', z.prompt, 'options', z.options)
                              ORDER BY z.position, z.id)
                         FROM news_quiz_questions z
                        WHERE z.news_id = p.id
                   ), '[]'::json)""" if with_quiz else "'[]'::json"),
            passes=_pass_columns_sql(with_pass),
        ) + viewer_match(with_space),
        params,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        'id': row[0],
        'title': row[1],
        'body': row[2],
        'is_mandatory': bool(row[3]),
        'published_at': row[4].isoformat() if row[4] else None,
        'confirmed': row[5] is not None,
        'quiz': row[6] or [],
        'pass_required': bool(row[7]),
        'trainer_key': row[8],
        'quiz_passed': row[9] is not None,
        'trainer_passed': row[10] is not None,
        # Кадры подшивает роут: подпись — его работа (news_photos.sign_urls).
        'photos': post_photos(cursor, row[0]) if with_photos else [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ВИТРИНА РЕДАКТОРА
# ─────────────────────────────────────────────────────────────────────────────

def list_posts(cursor, *, viewer_id, viewer_level, departments, status=None,
               limit=50, offset=0, with_photos=False, with_quiz=False, with_pass=False,
               with_space=False, space_id=None, with_channel=False):
    """Новости, которые этот редактор вправе видеть в разделе.

    departments=None — без границы (супер-админ, администратор вики): все.
    Иначе своё плюс чужое своего отдела, но только от авторов НЕ ВЫШЕ себя:
    черновик руководителя — не материал супервайзера, ровно по тому же правилу,
    по которому новость идёт вниз, а не вверх.

    space_id — ПРОСТРАНСТВО, из которого открыт список. Граница отдела на этот
    вопрос не отвечает: у супер-админа её нет вовсе, и без пространства он
    видел бы объявления Тез КЦ вперемешку с таксопарковыми в одном списке.
    """
    params = {'viewer': viewer_id, 'viewer_level': viewer_level,
              'depts': list(departments) if departments is not None else None,
              'space': space_id,
              'status': status, 'limit': limit, 'offset': offset}
    params.update(_role_params())
    author_level = news_access.role_level_sql(news_access.canon_role_sql('u.role'))
    cursor.execute(
        """
        SELECT p.id, p.title, p.status, p.is_mandatory, p.confirm_delay_seconds,
               p.published_at, p.expires_at, p.created_at, p.updated_at,
               u.name AS author_name, d.name AS author_department,
               p.author_id, u.role AS author_role,
               {photo_count} AS photo_count,
               {quiz_count} AS quiz_count,
               {trainer_key} AS trainer_key,
               {channel} AS channel
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
           {space}
         ORDER BY COALESCE(p.published_at, p.created_at) DESC, p.id DESC
         LIMIT %(limit)s OFFSET %(offset)s
        """.format(
            author_level=author_level,
            space=_space_filter(with_space),
            # Скалярным подзапросом в тот же запрос, а не четвёртым обращением
            # по образцу audience_stats: там отдельный запрос оправдан тяжёлым
            # CTE сотрудников, а здесь это COUNT по индексу idx_news_photos_post.
            # Нулём — когда таблицы кадров ещё нет: подзапрос по
            # несуществующей таблице уронил бы список редактора пятисоткой.
            photo_count=("(SELECT COUNT(*) FROM news_photos f WHERE f.news_id = p.id)"
                         if with_photos else "0"),
            # Тест — тем же приёмом и с той же оговоркой про таблицу.
            quiz_count=("(SELECT COUNT(*) FROM news_quiz_questions z WHERE z.news_id = p.id)"
                        if with_quiz else "0"),
            trainer_key=("p.trainer_key" if with_pass else "NULL::varchar"),
            # Канал — в строке списка: редактор должен видеть, куда ушло
            # объявление, не открывая карточку.
            channel=("p.channel" if with_channel
                     else "'%s'::varchar" % news_access.DEFAULT_CHANNEL),
        ),
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
           {space}
        """.format(author_level=author_level, space=_space_filter(with_space)),
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
        # Сколько кадров прикреплено. Числом, а не фразой: строка списка
        # отвечает на «что это за новость», а не рассказывает про её устройство.
        'photo_count': int(row[13] or 0),
        # Сколько вопросов в тесте окна. Форма по нему запирает обязательность.
        'quiz_count': int(row[14] or 0),
        # Тренажёр новости (#342): метка в строке и дверь к журналу прохождений.
        'trainer_key': row[15],
        # Куда ушло объявление: 'icore' — окно портала, 'oktell' — окно поверх
        # клиента АТС.
        'channel': row[16] or news_access.DEFAULT_CHANNEL,
        # Заполняется ниже одним запросом на всю страницу: считать его
        # подзапросом по news_reads значило бы считать НЕ ТО, что показывает
        # журнал (там знаменатель — нынешние адресаты), и «Прочитали: 14» на
        # карточке спорило бы с «Подтвердили 12 из 30» под ней.
        'confirmed_count': 0,
        'audience_count': 0,
    } for row in rows]

    stats = audience_stats(cursor, [item['id'] for item in items],
                           with_space=with_space)
    for item in items:
        addressed, confirmed = stats.get(item['id'], (0, 0))
        item['audience_count'] = addressed
        item['confirmed_count'] = confirmed
    return total, items


def audience_stats(cursor, post_ids, with_space=False):
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
        """ + report_match(with_space) + """
         GROUP BY p.id
        """,
        params,
    )
    return {int(row[0]): (int(row[1]), int(row[2])) for row in cursor.fetchall()}


def get_post(cursor, post_id, with_pass=False, with_space=False, with_channel=False):
    """Карточка новости с адресатами. None — нет такой.

    with_pass — развёрнуты ли колонки тренажёра и обязательности прохождения
    (schema.pass_ready). Без них карточка читается как до задачи #342: тест
    обязателен, тренажёра нет.

    with_space — развёрнута ли колонка пространства (schema.space_ready). По
    нему проверяются адресаты при правке: граница берётся у САМОЙ новости, а не
    у вкладки, из которой пришёл запрос, — иначе объявление Тез правилось бы
    справочником Таксопарков.
    """
    cursor.execute(
        """
        SELECT p.id, p.title, p.body, p.status, p.is_mandatory,
               p.confirm_delay_seconds, p.published_at, p.expires_at,
               p.author_id, p.author_department_id, p.audience_max_role_level,
               u.name, d.name, p.created_at, p.updated_at, u.role,
               {pass_required}, {trainer_key}, {space_id}, {channel}
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
          LEFT JOIN departments d ON d.id = p.author_department_id
         WHERE p.id = %s
        """.format(pass_required='p.pass_required' if with_pass else 'TRUE',
                   trainer_key='p.trainer_key' if with_pass else 'NULL::varchar',
                   space_id='p.space_id' if with_space else 'NULL::int',
                   channel=('p.channel' if with_channel
                            else "'%s'::varchar" % news_access.DEFAULT_CHANNEL)),
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
        'pass_required': bool(row[16]),
        'trainer_key': row[17],
        # Пространство новости — «чьей компании это объявление». Наружу нужно
        # форме: по нему она сужает справочники адресата так же, как сервер.
        'space_id': row[18],
        # Куда отправлено: портал или окно поверх клиента АТС.
        'channel': row[19] or news_access.DEFAULT_CHANNEL,
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
                is_mandatory, confirm_delay_seconds, expires_at, created_by,
                space_id=None, with_space=False,
                channel=None, with_channel=False):
    """Черновик новости. space_id — вика, в которой её пишут.

    with_space=False — колонки пространства ещё нет (schema.space_ready):
    пишем без неё, и новость остаётся ничьей до бэкфилла. Перечислять колонку
    в INSERT «на всякий случай» нельзя: на базе без неё упал бы сам выпуск.

    with_channel — то же самое про канал (schema.channel_ready): без колонки
    новость уходит туда, куда уходила всегда, — в портал (умолчание колонки).

    Пространство у новости НЕ МЕНЯЕТСЯ правкой намеренно. Переезд объявления в
    соседнюю вику — это не опечатка в тексте, а другой круг адресатов: у
    опубликованной новости он уже показан людям и посчитан в журнале.
    """
    cursor.execute(
        """
        INSERT INTO news_posts (title, body, author_id, author_department_id,
                                status, is_mandatory, confirm_delay_seconds,
                                expires_at, created_by{space_column}{channel_column})
        VALUES (%(title)s, %(body)s, %(author)s, %(dept)s, 'draft',
                %(mandatory)s, %(delay)s, %(expires)s,
                %(created_by)s{space_value}{channel_value})
        RETURNING id
        """.format(space_column=', space_id' if with_space else '',
                   space_value=', %(space)s' if with_space else '',
                   channel_column=', channel' if with_channel else '',
                   channel_value=', %(channel)s' if with_channel else ''),
        {'title': title, 'body': body, 'author': author_id,
         'dept': author_department_id, 'mandatory': is_mandatory,
         'delay': confirm_delay_seconds, 'expires': expires_at,
         'created_by': created_by, 'space': space_id,
         'channel': channel or news_access.DEFAULT_CHANNEL},
    )
    return int(cursor.fetchone()[0])


def update_post(cursor, *, post_id, title, body, is_mandatory,
                confirm_delay_seconds, expires_at,
                channel=None, with_channel=False):
    """Правка черновика. channel меняется только у невыпущенной новости —
    правило держит роут (NEWS_CHANNEL_LOCKED), здесь оно просто исполняется."""
    cursor.execute(
        """
        UPDATE news_posts
           SET title = %(title)s, body = %(body)s, is_mandatory = %(mandatory)s,
               confirm_delay_seconds = %(delay)s, expires_at = %(expires)s,
               {channel_set}
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW,
                   channel_set=('channel = %(channel)s,'
                                if with_channel and channel else '')),
        {'id': post_id, 'title': title, 'body': body, 'mandatory': is_mandatory,
         'delay': confirm_delay_seconds, 'expires': expires_at,
         'channel': channel},
    )


def set_passes(cursor, *, post_id, pass_required, trainer_key):
    """Тренажёр и обязательность прохождения (#342). Отдельно от update_post:
    её зовут и «Вопросы операторов», где этих колонок могло ещё не быть."""
    cursor.execute(
        """
        UPDATE news_posts
           SET pass_required = %(required)s, trainer_key = %(trainer)s
         WHERE id = %(id)s
        """,
        {'id': post_id, 'required': bool(pass_required), 'trainer': trainer_key},
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

def read_report(cursor, post_id, with_pass=False, with_space=False):
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
        """ + report_match(with_space) + """
        ),
        -- Отметки ИМЕННО ЭТОЙ новости, отобранные ДО соединения. Условие
        -- `r.news_id = ...` в ON у FULL JOIN не работает: несовпавшая сторона
        -- приносит с собой отметки по ЧУЖИМ новостям, и журнал распухает
        -- дублями и посторонними людьми. Поймано прогоном на живой базе.
        reads AS (
            SELECT user_id, shown_at, confirmed_at, """ + (
                "quiz_passed_at, trainer_passed_at" if with_pass else
                "NULL::timestamp AS quiz_passed_at, NULL::timestamp AS trainer_passed_at") + """
              FROM news_reads
             WHERE news_id = %(post_id)s
        )
        SELECT COALESCE(a.id, u.id)                AS user_id,
               COALESCE(a.name, u.name)            AS name,
               COALESCE(a.role, u.role)            AS role,
               COALESCE(a.department_name, d.name) AS department_name,
               r.shown_at, r.confirmed_at,
               (a.id IS NOT NULL)                  AS in_audience,
               r.quiz_passed_at, r.trainer_passed_at
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
        # Задача #342: кто прошёл тест и тренажёр. Отдельно от подтверждения —
        # необязательный тест проходят и после «Прочитал», и не проходят вовсе.
        'quiz_passed_at': row[7].isoformat() if row[7] else None,
        'trainer_passed_at': row[8].isoformat() if row[8] else None,
    } for row in cursor.fetchall()]


def audience_size(cursor, post_id, with_space=False):
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
        """ + report_match(with_space),
        params,
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def subject_catalog(cursor, department_ids=None, space_department_ids=None):
    """Справочники адресата: отделы, направления, группы.

    Свой запрос, а НЕ wiki_structure.subject_catalog, хотя тот отвечает почти
    на тот же вопрос. Причина не в форме, а в том, что он делает UNION с
    `wiki_roles`: на стенде без вики и на проде, где схема вики не применилась,
    он падает — то есть раздел «Новости» умирал бы от чужой миграции. Роль вики
    новостям и не адресат: у сотрудника без вики её нет вовсе.

    department_ids=None — без границы отдела (директор, администратор вики).
    С границей справочник сужается до своего отдела: предлагать в форме то,
    что сервер потом отвергнет, — значит обещать невыполнимое.

    space_department_ids — граница ПРОСТРАНСТВА (None — про него не
    спрашивали). Вопрос другой: не «чей это человек», а «чьей компании эта
    вика». Складываются обе (wiki/structure.py: narrow_to_space) — иначе в
    «Тез» предлагался бы отдел СЗоВ, а сервер такую новость всё равно
    отвергнет (access.audience_refusal).
    """
    department_ids = wiki_structure.narrow_to_space(department_ids, space_department_ids)
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


def targetable_people(cursor, *, max_role_level, department_ids=None,
                      space_department_ids=None):
    """Сотрудники, которым этот человек вправе адресовать новость поимённо.

    Обе границы — как у справочника субъектов: своя (чей это человек) и
    пространства (чьей компании вика). Список людей чужой компании — не только
    бесполезное правило, но и чужая оргструктура с именами и должностями.
    """
    return wiki_structure.grantable_people(
        cursor, max_role_level=max_role_level, department_ids=department_ids,
        space_department_ids=space_department_ids)


def space_departments(cursor, space_id):
    """Отделы пространства. Обёртка над справочником вики — ради одного места.

    Пространство знает про свои отделы только вика, и спрашивать её надо тем же
    запросом, которым спрашивает она сама: второй, написанный здесь, однажды
    разойдётся с первым (ровно так уже расходились лестницы прав).
    """
    return wiki_structure.space_department_ids(cursor, space_id)


def department_codes(cursor, department_ids):
    """Коды отделов по их id. Нужны, чтобы понять, какие каналы предлагать.

    Именно КОДЫ, а не имена: «СЗоВ» в базе переименуют однажды, а `szov` — это
    то, чем отдел зовут и раздел «Ограничитель Перезвона», и оргструктура.
    """
    ids = [int(value) for value in (department_ids or []) if value is not None]
    if not ids:
        return []
    cursor.execute("SELECT code FROM departments WHERE id = ANY(%s)", (ids,))
    return [row[0] for row in cursor.fetchall() if row[0]]


def space_of_department(cursor, department_id):
    """Пространство отдела: «чья это вика». None — отделу не выдано ни одного.

    Нужно там, где пространство некому назвать: новость из вкладки «Вопросы
    операторов» (#321) адресована отделу человека, задавшего вопрос, и та же
    связь отвечает, в какой вике объявление живёт. Тем же правилом разбираются
    ничьи новости на старте (schema.backfill_space_ids).

    Отдел, выданный двум пространствам, — случай, которого в жизни нет, но в
    базе он возможен: берём первое по порядку показа, чтобы ответ был
    одинаковым при каждом вызове.
    """
    if not department_id:
        return None
    cursor.execute(
        """
        SELECT sd.space_id
          FROM wiki_space_departments sd
          JOIN wiki_spaces sp ON sp.id = sd.space_id AND sp.status = 'active'
         WHERE sd.department_id = %s
         ORDER BY sp.position, sp.id
         LIMIT 1
        """,
        (department_id,),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def targetable_roles(ceiling):
    """Должности, которые автор вправе выбрать адресатом. Пустой список у того,
    кому границей отдела роль недоступна вовсе (см. may_target_subject)."""
    return [{'code': code, 'level': level}
            for code, level in sorted(wiki_access.ROLE_LEVELS.items(), key=lambda kv: kv[1])
            if ceiling is not None and level <= ceiling]


# ─────────────────────────────────────────────────────────────────────────────
# Фотографии объявления
#
# Байты и бакет живут в news/photos.py, здесь только строки. Колонки bucket и
# blob_path выбираются НЕ для ответа: их читает только news_photos.sign_urls,
# который собирает наружу новый словарь по белому списку.
# ─────────────────────────────────────────────────────────────────────────────

_PHOTO_COLUMNS = ('id, news_id, bucket, blob_path, content_type, '
                  'file_size, width, height, sort_order, uploaded_by')


def _photo_row(row):
    return {
        'id': row[0], 'news_id': row[1], 'bucket': row[2], 'blob_path': row[3],
        'content_type': row[4], 'file_size': row[5], 'width': row[6],
        'height': row[7], 'sort_order': row[8], 'uploaded_by': row[9],
    }


def insert_loose_photo(cursor, *, prepared, bucket, blob_path, uploaded_by):
    """Кадр, ещё ни к какой новости не привязанный (news_id IS NULL).

    «Ничей» — законное состояние, а не полуфабрикат: черновика, в который можно
    было бы грузить, не существует, пока автор не выбрал адресатов
    (news_post_create отвергает пустой набор). Подробности — в news/schema.py.
    """
    cursor.execute(
        """
        INSERT INTO news_photos (bucket, blob_path, content_type, file_size,
                                 width, height, original_name, uploaded_by)
        VALUES (%(bucket)s, %(blob)s, %(kind)s, %(size)s, %(w)s, %(h)s,
                %(name)s, %(user)s)
        RETURNING """ + _PHOTO_COLUMNS,
        {'bucket': bucket, 'blob': blob_path,
         'kind': prepared.get('content_type') or 'image/webp',
         'size': int(prepared.get('file_size') or 0),
         'w': prepared.get('width'), 'h': prepared.get('height'),
         'name': prepared.get('original_name'), 'user': uploaded_by},
    )
    return _photo_row(cursor.fetchone())


def count_loose_photos(cursor, user_id):
    """Сколько «ничьих» кадров держит этот человек. Потолок — против цикла в
    консоли, который иначе набил бы бакет молча."""
    cursor.execute(
        "SELECT COUNT(*) FROM news_photos WHERE news_id IS NULL AND uploaded_by = %s",
        (user_id,))
    return int(cursor.fetchone()[0])


def photo_by_id(cursor, photo_id):
    cursor.execute("SELECT " + _PHOTO_COLUMNS + " FROM news_photos WHERE id = %s",
                   (photo_id,))
    row = cursor.fetchone()
    return _photo_row(row) if row else None


def drop_photo(cursor, photo_id):
    """Убирает строку. Возвращает ссылки на блобы — сносить их можно только
    ПОСЛЕ фиксации транзакции (news/photos.py: drop_blobs)."""
    cursor.execute(
        "DELETE FROM news_photos WHERE id = %s RETURNING bucket, blob_path",
        (photo_id,))
    return [(row[0], row[1]) for row in cursor.fetchall()]


def post_photos(cursor, post_id):
    """Кадры новости по порядку показа. Сырые строки — подписывает их роут."""
    cursor.execute(
        "SELECT " + _PHOTO_COLUMNS + """
           FROM news_photos
          WHERE news_id = %(post)s
          ORDER BY sort_order, id
          LIMIT %(max)s
        """, {'post': post_id, 'max': MAX_PHOTOS_PER_POST})
    return [_photo_row(row) for row in cursor.fetchall()]


def sweep_loose_photos(cursor):
    """Брошенные «ничьи» кадры: строки удаляются, ссылки на блобы возвращаются
    вызывающему. Сутки, а не час: форму закрывают и возвращаются к ней завтра."""
    cursor.execute(
        """
        DELETE FROM news_photos
         WHERE news_id IS NULL
           AND created_at < %(now)s - INTERVAL '%(ttl)s hours'
        RETURNING bucket, blob_path
        """.replace('%(now)s', _NOW).replace('%(ttl)s', str(int(LOOSE_PHOTO_TTL_HOURS))))
    return [(row[0], row[1]) for row in cursor.fetchall()]


def set_photos(cursor, *, post_id, photo_ids, user_id):
    """Привязка + порядок + отцепление одним движением. Строка отказа или None.

    Одно поле формы на три действия: кадры, которых нет в списке, возвращаются
    в «ничьи» (блобы уберёт ближайшая уборка), остальные привязываются к новости
    и расставляются по местам массива.
    """
    ids = [str(value) for value in (photo_ids or [])][:MAX_PHOTOS_PER_POST + 1]
    if len(ids) > MAX_PHOTOS_PER_POST:
        return 'Больше %d фотографий к одной новости не прикрепить' % MAX_PHOTOS_PER_POST

    # Отцепляем лишних — обратно в «ничьи», а не удаляем: автор мог убрать кадр
    # по ошибке и вернуть его, пока форма открыта.
    cursor.execute(
        """
        UPDATE news_photos SET news_id = NULL, sort_order = 0
         WHERE news_id = %(post)s AND NOT (id = ANY(%(ids)s::uuid[]))
        """, {'post': post_id, 'ids': ids})
    if not ids:
        return None

    # Условие по news_id/uploaded_by ОБЯЗАТЕЛЬНО. Без него идентификатор кадра
    # сам стал бы ключом доступа: правкой своей новости можно было бы
    # «усыновить» чужую фотографию, подставив её id. Чужой id просто не
    # совпадёт — ни ошибки, ни утечки.
    cursor.execute(
        """
        UPDATE news_photos AS f
           SET news_id = %(post)s, sort_order = data.ord - 1
          FROM (SELECT * FROM unnest(%(ids)s::uuid[]) WITH ORDINALITY AS t(id, ord)) data
         WHERE f.id = data.id
           AND (f.news_id = %(post)s
                OR (f.news_id IS NULL AND f.uploaded_by = %(me)s))
        """, {'post': post_id, 'ids': ids, 'me': user_id})
    return None
