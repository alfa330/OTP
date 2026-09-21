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


def _plan_columns_sql(with_plan):
    """Колонки ТЗ #300 (тип, балл, планировщик) для выборок с `p` = news_posts.

    Без развёрнутых колонок — литералы прежнего поведения: тип выводится из
    обязательности уже в питоне, балл сто, режим «сразу», расписания нет.
    Форма строки одна при любой готовности схемы, и разбор ниже не гадает.
    """
    if with_plan:
        return ("p.kind, p.pass_score_percent, p.publish_mode, p.scheduled_at, "
                "p.scheduled_armed, p.spread_minutes, p.wave_interval_minutes")
    return ("NULL::varchar AS kind, %d::smallint AS pass_score_percent, "
            "'%s'::varchar AS publish_mode, NULL::timestamp AS scheduled_at, "
            "FALSE AS scheduled_armed, "
            "NULL::int AS spread_minutes, NULL::int AS wave_interval_minutes"
            % (news_access.DEFAULT_PASS_SCORE_PERCENT, news_access.DEFAULT_PUBLISH_MODE))


# Поля планировщика в порядке колонок. Один список на вставку, правку и разбор
# ответа: разъехавшись, они дали бы новость, записанную не тем, чем её собрали.
_PLAN_FIELDS = ('kind', 'pass_score_percent', 'publish_mode', 'scheduled_at',
                'spread_minutes', 'wave_interval_minutes')
_PLAN_INSERT_COLUMNS = ''.join(', ' + name for name in _PLAN_FIELDS)
_PLAN_INSERT_VALUES = ''.join(', %%(plan_%s)s' % name for name in _PLAN_FIELDS)
# Признака «взведён» среди полей формы НЕТ: его ставит только публикация
# (schedule_post). Зато правка обязана его СНИМАТЬ, когда автор переключил
# запуск на «Сразу»: иначе крон выпустил бы новость, которую уже отвязали от
# расписания.
_PLAN_UPDATE_SET = (''.join('%s = %%(plan_%s)s, ' % (name, name) for name in _PLAN_FIELDS)
                    + 'scheduled_armed = CASE WHEN %(plan_scheduled_at)s IS NULL '
                      'THEN FALSE ELSE scheduled_armed END, ')


def _plan_params(plan):
    """Параметры плана с префиксом `plan_`.

    Префикс не для красоты: в том же запросе живут `title`, `body` и `channel`
    из формы, и поле с именем `kind` однажды столкнулось бы с чужим.
    """
    data = plan or {}
    return {('plan_' + name): data.get(name) for name in _PLAN_FIELDS}


def _status_filter(status, with_plan):
    """Условие корзины списка редактора. Требует параметра %(status)s.

    «Запланированные» — отдельная корзина, а не подмножество черновиков:
    объявление, ждущее своего часа, и недописанный черновик — разные вещи, и
    лежать вперемешку им незачем. Своего статуса у запланированной нет
    (см. news/schema.py), поэтому обе корзины описываются здесь, в одном месте.
    """
    if not status:
        return ''
    if status == 'scheduled':
        # Нет колонок — нет и запланированных: пустая корзина честнее, чем
        # список черновиков под чужой подписью.
        return ("AND p.status = 'draft' AND p.scheduled_armed"
                if with_plan else "AND FALSE")
    if status == 'draft' and with_plan:
        return "AND p.status = 'draft' AND NOT p.scheduled_armed"
    return "AND p.status = %(status)s"


def _wave_gate(with_plan):
    """Условие «волна этого человека уже наступила» для выборок с `p` = news_posts.

    Требует параметра %(user_id)s. Пустая строка, пока таблицы волн нет
    (schema.plan_ready): растяжки тогда не бывает вовсе, и упоминание
    несуществующей таблицы уронило бы /pending всему порталу.

    НЕТ СТРОКИ — ПОКАЗЫВАЕМ. Расписание считается снимком в момент выпуска, и
    те, кто пришёл в круг адресатов после (вышел на работу, перевёлся, дописан
    правкой), в нём отсутствуют. Обратное правило означало бы объявление,
    которое молча не доехало, — а тишина в этом разделе уже трижды стоила
    ровно того, ради чего он сделан.

    Решает ТОЛЬКО planned_at, отметка крона (activated_at) на показ не влияет:
    крон может опоздать на минуту, не запуститься после рестарта или отстать на
    сотне новостей, а объявление обязано открыться вовремя.
    """
    if not with_plan:
        return ''
    return """
           AND NOT EXISTS (SELECT 1 FROM news_waves w
                            WHERE w.news_id = p.id
                              AND w.user_id = %(user_id)s
                              AND w.planned_at > @NOW@)
        """.replace('@NOW@', _NOW)


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
                     with_plan=False, channel=None):
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
           """ + _channel_filter(channel) + _wave_gate(with_plan) + """
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


def record_attempt(cursor, *, news_id, user_id, result, answers):
    """Записать попытку теста (ТЗ #300, п.4.2). Возвращает её номер.

    Номер считает САМ запрос, а не питон: отдельный SELECT ради счётчика
    открыл бы окно между «посчитали» и «записали», и две попытки подряд легли
    бы под одним номером. Агрегат по пустому набору даёт NULL, COALESCE — 1,
    поэтому первая попытка не требует отдельной ветки.

    Пишется КАЖДАЯ попытка, включая удачную: «сдал с третьего раза» — это ровно
    то, что отличает понятную инструкцию от непонятной (п.12).
    """
    cursor.execute(
        """
        INSERT INTO news_quiz_attempts
               (news_id, user_id, attempt_no, correct, total, needed, passed,
                answers, wrong_ids)
        SELECT %(news)s, %(user)s, COALESCE(MAX(attempt_no), 0) + 1,
               %(correct)s, %(total)s, %(needed)s, %(passed)s,
               %(answers)s::jsonb, %(wrong)s::jsonb
          FROM news_quiz_attempts
         WHERE news_id = %(news)s AND user_id = %(user)s
        RETURNING attempt_no
        """,
        {'news': news_id, 'user': user_id,
         'correct': int(result.get('correct') or 0),
         'total': int(result.get('total') or 0),
         'needed': int(result.get('needed') or 0),
         'passed': bool(result.get('passed')),
         'answers': json.dumps(answers or {}, ensure_ascii=False),
         'wrong': json.dumps(result.get('wrong') or [])},
    )
    return int(cursor.fetchone()[0])


def question_mistakes(cursor, post_id):
    """Где чаще ошибаются (ТЗ #300, п.12). Список вопросов со счётом ошибок.

    «Вопрос №3 — ошиблись 38% сотрудников»: считаем ЛЮДЕЙ, а не попытки. Один
    человек, трижды не ответивший на третий вопрос, — это один человек, которому
    инструкция непонятна, а не три ошибки.

    Знаменатель — те, кто вообще отвечал: доля от всех адресатов мерила бы не
    понятность вопроса, а явку.
    """
    cursor.execute(
        """
        SELECT z.id, z.position, z.prompt,
               COUNT(DISTINCT a.user_id) AS wrong_people,
               (SELECT COUNT(DISTINCT user_id) FROM news_quiz_attempts
                 WHERE news_id = %(post_id)s) AS people
          FROM news_quiz_questions z
          LEFT JOIN news_quiz_attempts a
                 ON a.news_id = z.news_id
                AND a.wrong_ids @> to_jsonb(z.id)
         WHERE z.news_id = %(post_id)s
         GROUP BY z.id, z.position, z.prompt
         ORDER BY z.position, z.id
        """,
        {'post_id': post_id},
    )
    rows = cursor.fetchall()
    return [{
        'id': int(row[0]),
        # Номер, как его видит сотрудник в окне: позиция с единицы.
        'number': index,
        'prompt': row[2],
        'wrong_people': int(row[3] or 0),
        'people': int(row[4] or 0),
        'percent': round(int(row[3] or 0) * 100 / int(row[4])) if row[4] else 0,
    } for index, row in enumerate(rows, start=1)]


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
                 with_quiz=False, with_pass=False, with_space=False,
                 with_plan=False, with_attempts=False):
    """Принять «Прочитал». (status, подробность).

    Подробность — оставшиеся секунды у 'too_early' и итог попытки у
    'quiz_wrong' (access.quiz_result: сколько верных, сколько нужно).
    'trainer_pending' — обязательный тренажёр ещё не пройден (задача #342).

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
               {passes}, {score}
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.id = %(news_id)s
           AND p.status = 'published'
           {wave}
           AND
        """.format(now=_NOW, passes=_pass_columns_sql(with_pass),
                   # Проходной балл берём ТУТ ЖЕ, а не вторым запросом: сверка
                   # ответов и порог, по которому она решает, — один вопрос.
                   score=('p.pass_score_percent' if with_plan
                          else '%d::smallint' % news_access.DEFAULT_PASS_SCORE_PERCENT),
                   # Подтверждение до своей волны — та же дверь, что чтение:
                   # без неё перебором id человек «прочитал» бы объявление,
                   # которого ещё не видел, и окно не показалось бы ему никогда.
                   wave=_wave_gate(with_plan)) + viewer_match(with_space),
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
     pass_required, trainer_key, quiz_passed_at, trainer_passed_at,
     pass_score) = row
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
        # Порог — проходной балл новости (ТЗ #300, п.4). При ста процентах это
        # прежнее правило слово в слово: непройденной попытку делает любая
        # ошибка.
        result = news_access.quiz_result(answer_key, answers, pass_score)
        # Попытка пишется ДО решения и при любом исходе (ТЗ п.4.2): удачная
        # отвечает на «с какого раза сдал», неудачная — на «где спотыкаются».
        if with_attempts:
            record_attempt(cursor, news_id=news_id, user_id=user_id, result=result,
                           answers=news_access.clean_answers(answer_key, answers))
        if not result['passed']:
            # Список вопросов с ошибкой роут наружу НЕ отдаёт (решение владельца
            # 21.09.2026): попытка не засчитывается целиком, окно снимает весь
            # выбор и просит пройти тест заново. Здесь он остаётся как ответ на
            # вопрос «почему отказ» — для журнала и разбора, не для клиента.
            return 'quiz_wrong', result
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
                 with_space=False, with_plan=False):
    """Опубликованная новость, адресованная человеку, со своими отметками. None — нет.

    Своя новость сюда НЕ попадает: автор её не получает (как и окно).
    """
    params = news_access.audience_params(subjects, user_id, otp_role)
    params.update(_role_params())
    params['news_id'] = news_id
    cursor.execute(
        """
        SELECT p.id, {passes}, r.confirmed_at, {score}
          FROM news_posts p
          LEFT JOIN news_reads r ON r.news_id = p.id AND r.user_id = %(user_id)s
         WHERE p.id = %(news_id)s
           AND p.status = 'published'
           AND p.author_id IS DISTINCT FROM %(user_id)s
           {wave}
           AND
        """.format(passes=_pass_columns_sql(with_pass),
                   score=('p.pass_score_percent' if with_plan
                          else '%d::smallint' % news_access.DEFAULT_PASS_SCORE_PERCENT),
                   # Та же дверь, что у окна: до своей волны новости для
                   # человека ещё нет — ни прочитать, ни пройти её тест.
                   wave=_wave_gate(with_plan)) + viewer_match(with_space),
        params,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {'id': row[0], 'pass_required': bool(row[1]), 'trainer_key': row[2],
            'quiz_passed_at': row[3], 'trainer_passed_at': row[4], 'confirmed_at': row[5],
            'pass_score_percent': int(row[6] or news_access.DEFAULT_PASS_SCORE_PERCENT)}


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
              with_space=False, with_plan=False, with_attempts=False):
    """«Проверить» у теста новости. (status, подробность).

    'not_found' — новости нет или она не этому человеку; 'no_quiz' — теста у
    неё нет; 'quiz_wrong' — итог попытки (access.quiz_result); 'ok' — тест
    пройден и отмечен в журнале. Подтверждение прочтения здесь НЕ ставится: это
    другой вопрос, и отвечает на него кнопка «Прочитал».
    """
    post = _viewer_post(cursor, news_id=news_id, user_id=user_id, otp_role=otp_role,
                        subjects=subjects, with_pass=True, with_space=with_space,
                        with_plan=with_plan)
    if post is None:
        return 'not_found', 0
    answer_key = quiz_answer_key(cursor, news_id)
    if not answer_key:
        return 'no_quiz', 0
    # Порог берём у новости — тот же, по которому решает подтверждение окна:
    # «Проверить» и «Прочитал» обязаны засчитывать одну и ту же попытку.
    result = news_access.quiz_result(answer_key, answers,
                                     post['pass_score_percent'])
    if with_attempts:
        record_attempt(cursor, news_id=news_id, user_id=user_id, result=result,
                       answers=news_access.clean_answers(answer_key, answers))
    if not result['passed']:
        return 'quiz_wrong', result
    _mark_pass(cursor, news_id=news_id, user_id=user_id, column='quiz_passed_at')
    return 'ok', 0


def mark_trainer_passed(cursor, *, news_id, user_id, otp_role, subjects,
                        with_space=False, with_plan=False):
    """Тренажёр новости дошёл до конца. (status, 0).

    Итог урока присылает браузер: сценарий живёт в коде фронта, и сервер
    проверить прохождение шаг за шагом не может — ровно так же пишется и
    статистика тренажёров вики (wiki_trainer_runs). Граница здесь та, что есть:
    новость опубликована, адресована этому человеку и тренажёр у неё правда есть.
    """
    post = _viewer_post(cursor, news_id=news_id, user_id=user_id, otp_role=otp_role,
                        subjects=subjects, with_pass=True, with_space=with_space,
                        with_plan=with_plan)
    if post is None:
        return 'not_found', 0
    if not post['trainer_key']:
        return 'no_trainer', 0
    _mark_pass(cursor, news_id=news_id, user_id=user_id, column='trainer_passed_at')
    return 'ok', 0


def feed_for_user(cursor, *, user_id, otp_role, subjects, limit=20, offset=0,
                  with_photos=False, with_quiz=False, with_pass=False,
                  with_space=False, with_plan=False, space_id=None):
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
    # Волна режет и ленту: до своей волны новости для человека ещё нет, и
    # увидеть её списком раньше, чем окном, он не должен.
    where = """
         WHERE p.status = 'published'
           AND p.author_id IS DISTINCT FROM %(user_id)s
        """ + _wave_gate(with_plan) + """
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
              with_quiz=False, with_pass=False, with_space=False, with_plan=False):
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
           {wave}
           AND
        """.format(
            wave=_wave_gate(with_plan),
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
               with_space=False, space_id=None, with_channel=False, with_plan=False):
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
               {channel} AS channel,
               {plan}
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
          LEFT JOIN departments d ON d.id = p.author_department_id
         WHERE TRUE
           {status}
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
            plan=_plan_columns_sql(with_plan),
            status=_status_filter(status, with_plan),
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
         WHERE TRUE
           {status}
           AND (
                %(depts)s::int[] IS NULL
             OR p.author_id = %(viewer)s
             OR (p.author_department_id = ANY(%(depts)s::int[])
                 AND {author_level} <= %(viewer_level)s)
           )
           {space}
        """.format(author_level=author_level, space=_space_filter(with_space),
                   status=_status_filter(status, with_plan)),
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
        # ТЗ #300. Тип строки списка выводится из поведения, когда колонки ещё
        # нет: строка обязана читаться одинаково до и после деплоя. Число
        # вопросов здесь под рукой — значит и «критичная» выводится точно.
        # pass_required=True — умолчание колонки (#342): до неё тест новости был
        # обязателен всегда, а вывод нужен ровно для тех строк, что старше.
        'kind': row[17] or news_access.kind_of(
            is_mandatory=bool(row[3]), pass_required=True, has_quiz=bool(row[14])),
        'pass_score_percent': int(row[18] or news_access.DEFAULT_PASS_SCORE_PERCENT),
        'publish_mode': row[19] or news_access.DEFAULT_PUBLISH_MODE,
        'scheduled_at': row[20].isoformat() if row[20] else None,
        'scheduled_armed': bool(row[21]),
        'spread_minutes': row[22],
        'wave_interval_minutes': row[23],
        # «Запланирована» для колонок и подписи строки.
        'state': news_access.post_state(row[2], row[20], row[21]),
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


def get_post(cursor, post_id, with_pass=False, with_space=False, with_channel=False,
             with_plan=False):
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
               {pass_required}, {trainer_key}, {space_id}, {channel},
               {plan}
          FROM news_posts p
          LEFT JOIN users u ON u.id = p.author_id
          LEFT JOIN departments d ON d.id = p.author_department_id
         WHERE p.id = %s
        """.format(pass_required='p.pass_required' if with_pass else 'TRUE',
                   trainer_key='p.trainer_key' if with_pass else 'NULL::varchar',
                   space_id='p.space_id' if with_space else 'NULL::int',
                   channel=('p.channel' if with_channel
                            else "'%s'::varchar" % news_access.DEFAULT_CHANNEL),
                   plan=_plan_columns_sql(with_plan)),
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
        # ТЗ #300: тип, проходной балл теста и режим запуска. Тип у старой
        # строки выводится из поведения — тем же правилом, что и в бэкфилле
        # схемы, чтобы карточка и список говорили одно и то же.
        # has_quiz=False здесь не небрежность: без колонки (один деплой) тип
        # никуда не пишется, и «важная» вместо «критичной» — это подпись, а не
        # поведение. Ведут новость по-прежнему сами флаги.
        'kind': row[20] or news_access.kind_of(
            is_mandatory=bool(row[4]), pass_required=bool(row[16]), has_quiz=False),
        'pass_score_percent': int(row[21] or news_access.DEFAULT_PASS_SCORE_PERCENT),
        'publish_mode': row[22] or news_access.DEFAULT_PUBLISH_MODE,
        'scheduled_at': row[23].isoformat() if row[23] else None,
        'scheduled_armed': bool(row[24]),
        'spread_minutes': row[25],
        'wave_interval_minutes': row[26],
        # «Запланирована» — выведенное состояние, своего статуса у неё нет
        # (см. news/schema.py). Считает его access.post_state — одна функция на
        # карточку, список и крон.
        'state': news_access.post_state(row[3], row[23], row[24]),
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
                channel=None, with_channel=False,
                plan=None, with_plan=False):
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
    params = {'title': title, 'body': body, 'author': author_id,
              'dept': author_department_id, 'mandatory': is_mandatory,
              'delay': confirm_delay_seconds, 'expires': expires_at,
              'created_by': created_by, 'space': space_id,
              'channel': channel or news_access.DEFAULT_CHANNEL}
    params.update(_plan_params(plan))
    cursor.execute(
        """
        INSERT INTO news_posts (title, body, author_id, author_department_id,
                                status, is_mandatory, confirm_delay_seconds,
                                expires_at, created_by{space_column}{channel_column}{plan_columns})
        VALUES (%(title)s, %(body)s, %(author)s, %(dept)s, 'draft',
                %(mandatory)s, %(delay)s, %(expires)s,
                %(created_by)s{space_value}{channel_value}{plan_values})
        RETURNING id
        """.format(space_column=', space_id' if with_space else '',
                   space_value=', %(space)s' if with_space else '',
                   channel_column=', channel' if with_channel else '',
                   channel_value=', %(channel)s' if with_channel else '',
                   plan_columns=_PLAN_INSERT_COLUMNS if with_plan else '',
                   plan_values=_PLAN_INSERT_VALUES if with_plan else ''),
        params,
    )
    return int(cursor.fetchone()[0])


def update_post(cursor, *, post_id, title, body, is_mandatory,
                confirm_delay_seconds, expires_at,
                channel=None, with_channel=False,
                plan=None, with_plan=False):
    """Правка черновика. channel меняется только у невыпущенной новости —
    правило держит роут (NEWS_CHANNEL_LOCKED), здесь оно просто исполняется.

    plan — тип, проходной балл и режим запуска (ТЗ #300). Пишется целиком, как
    и остальные поля формы: роут уже подставил в него прежние значения для
    того, чего в запросе не было."""
    params = {'id': post_id, 'title': title, 'body': body, 'mandatory': is_mandatory,
              'delay': confirm_delay_seconds, 'expires': expires_at,
              'channel': channel}
    params.update(_plan_params(plan))
    cursor.execute(
        """
        UPDATE news_posts
           SET title = %(title)s, body = %(body)s, is_mandatory = %(mandatory)s,
               confirm_delay_seconds = %(delay)s, expires_at = %(expires)s,
               {channel_set}{plan_set}
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW,
                   channel_set=('channel = %(channel)s,'
                                if with_channel and channel else ''),
                   plan_set=_PLAN_UPDATE_SET if with_plan else ''),
        params,
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


def delete_post(cursor, post_id, with_photos=False):
    """Сносит новость целиком. Возвращает ссылки на блобы её кадров.

    Журнал прочтений, тест и кадры уходят каскадом (news/schema.py), и это
    решение владельца 21.09.2026: «удалить новость навсегда, журнал его тоже
    удалится кто прочитал».

    А вот БАЙТЫ кадров каскад не трогает — он живёт в базе, картинка в бакете.
    Поэтому строки кадров снимаются отдельно и с RETURNING: сборщика сирот в
    проекте нет, и без этого от каждой удалённой новости в хранилище оставалось
    бы до десяти файлов навсегда. Сносит их ВЫЗЫВАЮЩИЙ и только после фиксации
    транзакции (news/photos.py: drop_blobs).
    """
    refs = []
    if with_photos:
        cursor.execute(
            "DELETE FROM news_photos WHERE news_id = %s RETURNING bucket, blob_path",
            (post_id,))
        refs = [(row[0], row[1]) for row in cursor.fetchall()]
    cursor.execute("DELETE FROM news_posts WHERE id = %s", (post_id,))
    return refs
# ─────────────────────────────────────────────────────────────────────────────
# ПЛАНИРОВЩИК ПУБЛИКАЦИИ (ТЗ #300, п.8)
#
# Три вопроса, на которые отвечает этот кусок: кому уйдёт объявление (чтобы
# разложить людей по волнам), кого пора выпускать (крон) и чья волна уже
# наступила (отметка факта для отчётности).
#
# Арифметику волн сюда НЕ переписываем — она в news/access.py, чистой функцией,
# и её же зовёт предварительный расчёт в форме. Обещание «12 волн примерно по
# 10 человек» и то, что ляжет в базу, обязаны быть одним счётом.
# ─────────────────────────────────────────────────────────────────────────────

def audience_user_ids(cursor, post_id, with_space=False):
    """Кому адресована СОХРАНЁННАЯ новость. Список id в порядке имени.

    Правила те же, что у журнала (report_match): по ним же считается «12 из
    30». Считать «кому уйдёт» вторым способом означало бы волны по одним людям
    и показ другим.

    Порядок по имени, а не по id: волна должна быть воспроизводимой и
    объяснимой («первыми ушли А–В»), а не зависеть от того, в каком порядке
    Postgres сегодня вернул строки.
    """
    params = {'post_id': post_id}
    params.update(_role_params())
    cursor.execute(
        "WITH " + _VIEWER_SUBJECTS_CTE + """
        SELECT v.id
          FROM news_posts p
          JOIN viewers v ON TRUE
         WHERE p.id = %(post_id)s
           AND v.id IS DISTINCT FROM p.author_id
           AND
        """ + report_match(with_space) + """
         ORDER BY v.name, v.id
        """,
        params,
    )
    return [int(row[0]) for row in cursor.fetchall()]


def audience_count_for_rules(cursor, *, rules, author_id, audience_max_role_level,
                             space_id=None, with_space=False):
    """Сколько человек под НЕСОХРАНЁННЫМИ правилами формы. Для расчёта п.8.4.

    Приём тот же, что у audience_sip_check: имя CTE перекрывает таблицу, и
    шаблон адресата читает набор из формы, ничего об этом не зная. Своей
    формулы у расчёта нет намеренно — иначе обещанное число получателей
    разошлось бы с тем, что потом уйдёт.
    """
    if not rules:
        return 0
    payload = json.dumps([{
        'subject_type': rule.get('subject_type'),
        'subject_id': rule.get('subject_id'),
        'subject_role': rule.get('subject_role'),
        'min_role_level': rule.get('min_role_level'),
    } for rule in rules], ensure_ascii=False)
    params = {'rules': payload, 'author': author_id,
              'ceiling': audience_max_role_level, 'space': space_id}
    params.update(_role_params())
    cursor.execute(
        """
        WITH news_posts AS (
            SELECT 0::int AS id, %(author)s::int AS author_id,
                   %(ceiling)s::int AS audience_max_role_level,
                   %(space)s::int AS space_id
        ),
        news_audience_rules AS (
            SELECT 0::int AS news_id, r.subject_type, r.subject_id,
                   r.subject_role, r.min_role_level
              FROM jsonb_to_recordset(%(rules)s::jsonb)
                AS r(subject_type text, subject_id int, subject_role text,
                     min_role_level int)
        ),
        """ + _VIEWER_SUBJECTS_CTE + """
        SELECT COUNT(*)
          FROM news_posts p
          JOIN viewers v ON TRUE
         WHERE v.id IS DISTINCT FROM p.author_id
           AND
        """ + report_match(with_space),
        params,
    )
    return int(cursor.fetchone()[0])


def set_waves(cursor, *, post_id, plan):
    """Полная замена расписания волн. plan — из access.plan_waves.

    Полная, а не добавление: расписание отвечает на один вопрос — «когда кому»,
    и собирать его из двух источников значило бы держать два ответа. Пустой
    план просто чистит таблицу: так снимается растяжка у перепланированной
    новости.
    """
    cursor.execute("DELETE FROM news_waves WHERE news_id = %s", (post_id,))
    for user_id, wave_no, planned_at in (plan or ()):
        cursor.execute(
            """
            INSERT INTO news_waves (news_id, user_id, wave_no, planned_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (news_id, user_id) DO NOTHING
            """,
            (post_id, int(user_id), int(wave_no), planned_at),
        )


def schedule_post(cursor, *, post_id, scheduled_at, audience_max_role_level):
    """Взвести отложенный запуск. Новость остаётся черновиком.

    Потолок должности фиксируется ЗДЕСЬ, а не в момент выпуска: выпускает крон,
    и должности автора у него под рукой нет. Смысл тот же, что у publish_post, —
    снимок прав на момент, когда автор нажал «Опубликовать».
    """
    cursor.execute(
        """
        UPDATE news_posts
           SET scheduled_at = %(when)s,
               scheduled_armed = TRUE,
               audience_max_role_level = COALESCE(%(ceiling)s, audience_max_role_level),
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': post_id, 'when': scheduled_at, 'ceiling': audience_max_role_level},
    )


def due_scheduled_posts(cursor, limit=20):
    """Кого пора выпускать. Для крона.

    Черновик со взведённым временем — это и есть «Запланирована»
    (access.post_state). Своего статуса у неё нет, и спрашивать его не о чем.
    """
    cursor.execute(
        """
        SELECT id, publish_mode, spread_minutes, wave_interval_minutes
          FROM news_posts
         WHERE status = 'draft'
           AND scheduled_armed
           AND scheduled_at <= {now}
         ORDER BY scheduled_at
         LIMIT %s
        """.format(now=_NOW),
        (int(limit),),
    )
    return [{'id': int(row[0]), 'publish_mode': row[1],
             'spread_minutes': row[2], 'wave_interval_minutes': row[3]}
            for row in cursor.fetchall()]


def publish_scheduled(cursor, post_id):
    """Выпустить запланированную новость. True — выпустили именно мы.

    Условие повторяет отбор: двум кронам (рестарт, второй процесс) одна новость
    достанется один раз, и дата выпуска не переедет. Потолок адресата уже
    зафиксирован взведением (schedule_post) — трогать его тут нечем и незачем.
    """
    cursor.execute(
        """
        UPDATE news_posts
           SET status = 'published',
               published_at = {now},
               -- Снимаем взвод: вышедшая новость расписанием больше не
               -- управляется, а снятая с показа и выпущенная заново пошла бы
               -- по нему второй раз.
               scheduled_armed = FALSE,
               updated_at = {now}
         WHERE id = %s
           AND status = 'draft'
           AND scheduled_armed
           AND scheduled_at <= {now}
        """.format(now=_NOW),
        (post_id,),
    )
    return bool(cursor.rowcount)


def activate_due_waves(cursor):
    """Отметить наступившие волны фактическим временем. Сколько отметили.

    На ПОКАЗ это не влияет ничем: выдача смотрит на planned_at (см. _wave_gate),
    потому что крон может опоздать или не подняться после рестарта, а
    объявление обязано открыться вовремя. Отметка нужна отчётности (ТЗ п.8.5:
    «плановое и фактическое время активации новости для сотрудника») и разбору
    «почему человек увидел позже плана».
    """
    cursor.execute(
        """
        UPDATE news_waves
           SET activated_at = {now}
         WHERE activated_at IS NULL
           AND planned_at <= {now}
        """.format(now=_NOW))
    return cursor.rowcount or 0


def poke_bell(cursor):
    """Разбудить открытые вкладки: пусть перезапросят своё /pending.

    Тем же каналом и тем же сообщением, что и триггеры колокола
    (database.py: bell_notify_change) — прямым NOTIFY, а не правкой строки ради
    срабатывания триггера. Новая волна не меняет в news_posts ничего, и
    выдумывать ей ложное изменение, чтобы вызвать побочное действие, значило бы
    оставить в коде ловушку для того, кто однажды тот триггер поправит.
    """
    cursor.execute("SELECT pg_notify('bell_events', '{\"b\":1}')")



# ─────────────────────────────────────────────────────────────────────────────
# ЖУРНАЛ: кто прочитал, кто нет
# ─────────────────────────────────────────────────────────────────────────────

def read_report(cursor, post_id, with_pass=False, with_space=False, with_plan=False,
                with_attempts=False):
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
        ),
        -- Попытки теста (ТЗ п.4.2) и ПОСЛЕДНИЙ результат: «2 из 3» человек
        -- читает сразу, а «сдал/не сдал» уже есть в reads.
        attempts AS (""" + ("""
            SELECT user_id,
                   COUNT(*)                                          AS tries,
                   (ARRAY_AGG(correct ORDER BY attempt_no DESC))[1]   AS last_correct,
                   (ARRAY_AGG(total   ORDER BY attempt_no DESC))[1]   AS last_total,
                   MAX(created_at)                                    AS last_at
              FROM news_quiz_attempts
             WHERE news_id = %(post_id)s
             GROUP BY user_id""" if with_attempts else """
            SELECT NULL::int AS user_id, 0::bigint AS tries,
                   NULL::smallint AS last_correct, NULL::smallint AS last_total,
                   NULL::timestamp AS last_at
             WHERE FALSE""") + """
        ),
        -- Часы человека вокруг публикации. Нужны единственному статусу — «не
        -- выходил на смену после публикации» (ТЗ п.11.2), и он требует ДВУХ
        -- ответов, а не одного.
        --
        -- ПОЧЕМУ ДВУХ. Учёт часов ведётся не по всей компании: у фронт-офисов,
        -- маркетинга, бухгалтерии и HR в daily_hours нет ни строки, да и внутри
        -- отдела часы есть не у каждого. По одному «нет часов после публикации»
        -- мы приписали бы прогул человеку, чьи смены просто нигде не считают, —
        -- а это обвинение, и оно попадёт в выгрузку.
        --
        -- Поэтому отдельно спрашиваем, ЗНАЕТ ли источник этого человека вообще
        -- (есть ли у него часы до публикации). Не знает — статус остаётся «не
        -- открывал объявление». Окно в 60 дней ограничивает просмотр: таблица
        -- большая, а «ведут ли ему часы» за два месяца видно наверняка.
        hours AS (
            SELECT h.operator_id                                  AS user_id,
                   BOOL_OR(h.day >= p.published_at::date)         AS worked_after,
                   BOOL_OR(h.day <  p.published_at::date)         AS worked_before
              FROM daily_hours h
              JOIN news_posts p ON p.id = %(post_id)s
             WHERE p.published_at IS NOT NULL
               AND h.work_time > 0
               AND h.day >= p.published_at::date - 60
             GROUP BY h.operator_id
        )
        SELECT COALESCE(a.id, u.id)                AS user_id,
               COALESCE(a.name, u.name)            AS name,
               COALESCE(a.role, u.role)            AS role,
               COALESCE(a.department_name, d.name) AS department_name,
               r.shown_at, r.confirmed_at,
               (a.id IS NOT NULL)                  AS in_audience,
               r.quiz_passed_at, r.trainer_passed_at,
               {wave_no}, {wave_planned}, {wave_activated},
               COALESCE(t.tries, 0), t.last_correct, t.last_total, t.last_at,
               COALESCE(hh.worked_after, FALSE),
               (hh.user_id IS NOT NULL)
          FROM addressed a
          FULL JOIN reads r ON r.user_id = a.id
          LEFT JOIN users u ON u.id = r.user_id
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN attempts t  ON t.user_id = COALESCE(a.id, r.user_id)
          -- Псевдоним hh, а не h: буква h занята таблицей часов внутри CTE, а
          -- w — расписанием волн («table name specified more than once»).
          LEFT JOIN hours    hh ON hh.user_id = COALESCE(a.id, r.user_id)
          {wave_join}
         WHERE COALESCE(a.id, r.user_id) IS NOT NULL
         ORDER BY (r.confirmed_at IS NULL) DESC, COALESCE(a.name, u.name)
        """.format(
            # Волна сотрудника — ТЗ п.8.5: «номер волны для каждого сотрудника,
            # плановое и фактическое время активации». Джойн, а не подзапрос:
            # строка волны у человека ровно одна (первичный ключ), размножить
            # выборку ей нечем.
            wave_no='w.wave_no' if with_plan else 'NULL::smallint AS wave_no',
            wave_planned=('w.planned_at' if with_plan
                          else 'NULL::timestamp AS planned_at'),
            wave_activated=('w.activated_at' if with_plan
                            else 'NULL::timestamp AS activated_at'),
            wave_join=('LEFT JOIN news_waves w ON w.news_id = %(post_id)s '
                       'AND w.user_id = COALESCE(a.id, r.user_id)'
                       if with_plan else ''),
        ),
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
        # Волна человека (ТЗ #300, п.8.5). Пусто — растяжки у новости не было
        # или человек пришёл в круг адресатов уже после выпуска: такому
        # объявление видно сразу (см. _wave_gate).
        'wave_no': (int(row[9]) + 1) if row[9] is not None else None,
        'wave_planned_at': row[10].isoformat() if row[10] else None,
        'wave_activated_at': row[11].isoformat() if row[11] else None,
        # ТЗ #300, п.4.2 и п.11.2: сколько раз отвечал и с каким результатом.
        # Результат — ПОСЛЕДНИЙ: он и есть ответ на «чем кончилось».
        'attempts': int(row[12] or 0),
        'last_correct': int(row[13]) if row[13] is not None else None,
        'last_total': int(row[14]) if row[14] is not None else None,
        'last_attempt_at': row[15].isoformat() if row[15] else None,
        # Был ли на смене после публикации — только для статуса «не выходил».
        'worked_after': bool(row[16]),
        # Ведут ли этому человеку часы вообще. Без этого «не выходил на смену»
        # превращается в обвинение по отсутствию данных.
        'attendance_tracked': bool(row[17]),
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


def audience_sip_check(cursor, *, rules, author_id, audience_max_role_level,
                       space_id=None, with_space=False):
    """Кто из отмеченных адресатов остался без SIP-номера. (сколько всего, список).

    Нужно каналу Oktell: объявление показывает программа поверх клиента АТС, а
    в АТС человек без номера не работает — значит и окна он не увидит. Форма
    предупреждает об этом ДО публикации, поимённо.

    ПРАВИЛА АДРЕСАТА ЗДЕСЬ НЕ ПЕРЕПИСАНЫ. Считать «кому уйдёт» вторым способом
    означало бы предупреждение про одних людей и показ другим; расходятся такие
    копии молча. Поэтому берётся тот же report_match, что у журнала и у счётчика
    «12 из 30», а НЕСОХРАНЁННЫЕ правила формы подставляются вместо таблиц: имя
    CTE в Postgres перекрывает таблицу внутри запроса, и шаблон, написанный про
    news_posts и news_audience_rules, читает временный набор, ничего не зная об
    этом.
    """
    if not rules:
        return 0, []
    payload = json.dumps([{
        'subject_type': rule.get('subject_type'),
        'subject_id': rule.get('subject_id'),
        'subject_role': rule.get('subject_role'),
        'min_role_level': rule.get('min_role_level'),
    } for rule in rules], ensure_ascii=False)
    params = {'rules': payload, 'author': author_id,
              'ceiling': audience_max_role_level, 'space': space_id}
    params.update(_role_params())
    cursor.execute(
        """
        WITH news_posts AS (
            SELECT 0::int AS id, %(author)s::int AS author_id,
                   %(ceiling)s::int AS audience_max_role_level,
                   %(space)s::int AS space_id
        ),
        news_audience_rules AS (
            SELECT 0::int AS news_id, r.subject_type, r.subject_id,
                   r.subject_role, r.min_role_level
              FROM jsonb_to_recordset(%(rules)s::jsonb)
                AS r(subject_type text, subject_id int, subject_role text,
                     min_role_level int)
        ),
        """ + _VIEWER_SUBJECTS_CTE + """
        SELECT v.id, v.name, v.role, v.department_name,
               NULLIF(btrim(COALESCE(u.sip_number, '')), '') IS NOT NULL AS has_sip
          FROM news_posts p
          JOIN viewers v ON TRUE
          JOIN users u ON u.id = v.id
         WHERE v.id IS DISTINCT FROM p.author_id
           AND
        """ + report_match(with_space) + """
         ORDER BY v.name
        """,
        params,
    )
    rows = cursor.fetchall()
    missing = [{
        'user_id': row[0],
        'name': row[1],
        'role': row[2],
        'department_name': row[3],
    } for row in rows if not row[4]]
    return len(rows), missing


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
