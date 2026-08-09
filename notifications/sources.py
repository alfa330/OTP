# -*- coding: utf-8 -*-
"""Источники уведомлений: счётчик и последние элементы для каждого раздела.

Все функции принимают готовый курсор — центр открывает соединение один раз и
опрашивает разделы через него. Ни одна из них не коммитит и не откатывает
транзакцию сама: этим управляет collect().

Формат элемента одинаков для всех источников, потому что колокол показывает их
одним списком:

    {'source', 'id', 'title', 'body', 'at' (ISO), 'view' (раздел фронта),
     'target' (чем раздел открывает элемент: у вики — slug статьи, у остальных
     id либо None), 'tone'}

`tone` — только 'default' или 'warning'; второй для просроченного (дедлайн
ознакомления прошёл). Цвет по важности, а не по разделу: пользователю нужно
видеть, что горит, а не откуда пришло.
"""

import logging
from datetime import datetime

# Порядок здесь = порядок групп в колоколе. Сначала то, что требует действия с
# дедлайном, потом то, что просто новое.
#
# «Задачи» — третья копия правил «задача ждёт вас»: первые две — SQL бейджа
# (database.py::get_task_action_needs_summary) и клиентские правила раздела
# (src/components/tasks/taskActionNeeds.js). Долго держались на двух копиях
# намеренно, но колоколу нужны сами задачи, а не только число. Дрейф копий не
# падает, а просто расходится числами на экране, поэтому все три сверяются
# тестами: tests/test_notifications.py::TasksSourceRulesTest и
# tests/test_task_backlog_board.py::ActionNeedsBadgeTests.
SOURCES = ('wiki_ack', 'tasks', 'lms', 'surveys', 'events', 'four_you')

# Сколько элементов тянем из одного источника. Колокол — не лента: он говорит
# «что тебя ждёт», а полный список пользователь смотрит в самом разделе.
ITEMS_PER_SOURCE = 5


def _iso(value):
    return value.isoformat() if value is not None else None


def _almaty_now():
    """«Сейчас» в том же виде, в каком раздел опросов хранит границы окна.

    Параметром, а не выражением в SQL: боевой процесс живёт в Asia/Almaty
    (os.environ['TZ'] + tzset() в начале bot_schedule2.py и database.py), и
    datetime.now() здесь совпадает с тем, с чем сравнивает
    Database.survey_test_status.
    """
    return datetime.now()


# ── Вики: статьи под обязательное ознакомление ───────────────────────────────
def wiki_ack(cursor, viewer):
    """Назначенные, но не подтверждённые ознакомления.

    Считаем по актуальной версии: назначение привязано к версии статьи, и если
    статью переиздали, старое подтверждение не закрывает новое требование —
    ровно так работает раздел, здесь мы это только отражаем.
    """
    cursor.execute(
        """
        SELECT a.id, a.title, aa.due_at,
               aa.due_at IS NOT NULL
               AND aa.due_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') AS overdue,
               a.slug
          FROM wiki_ack_assignments aa
          JOIN wiki_articles a ON a.id = aa.article_id
         WHERE aa.user_id = %(user_id)s
           AND aa.acknowledged_at IS NULL
           AND aa.status NOT IN ('superseded', 'cancelled')
           AND a.status = 'published'
         ORDER BY overdue DESC, aa.due_at NULLS LAST, a.title
        """,
        {'user_id': viewer['user_id']},
    )
    rows = cursor.fetchall()
    items = [{
        'source': 'wiki_ack',
        'id': row[0],
        'title': row[1],
        'body': 'Просрочено' if row[3] else ('Ознакомиться до %s' % row[2].strftime('%d.%m')
                                             if row[2] else 'Требуется ознакомление'),
        'at': _iso(row[2]),
        'view': 'wiki',
        # Раздел открывает статьи по slug, а не по id — отдаём то, чем он
        # умеет пользоваться, иначе переход упрётся в корень раздела.
        'target': row[4],
        'tone': 'warning' if row[3] else 'default',
    } for row in rows[:ITEMS_PER_SOURCE]]
    return len(rows), items


# ── Обучение ─────────────────────────────────────────────────────────────────
def lms(cursor, viewer):
    cursor.execute(
        """
        SELECT id, title, message, created_at,
               COUNT(*) OVER () AS total
          FROM lms_notifications
         WHERE user_id = %(user_id)s AND is_read = FALSE
         ORDER BY created_at DESC
         LIMIT %(limit)s
        """,
        {'user_id': viewer['user_id'], 'limit': ITEMS_PER_SOURCE},
    )
    rows = cursor.fetchall()
    total = int(rows[0][4]) if rows else 0
    return total, [{
        'source': 'lms',
        'id': row[0],
        'title': row[1],
        'body': row[2] or '',
        'at': _iso(row[3]),
        'view': 'lms',
        'target': None,
        'tone': 'default',
    } for row in rows]


# ── Опросы ───────────────────────────────────────────────────────────────────
def surveys(cursor, viewer):
    """Опросы и тесты, назначенные лично зрителю и ещё не пройденные.

    Раньше это число считал фронт, выгрузив ВЕСЬ список опросов со всеми
    назначениями и статистикой ради одной цифры. Критерий здесь тот же, что был
    у оператора на клиенте (can_submit && статус ≠ completed), только выражен
    в SQL: назначение не завершено, а для теста ещё и открыто его окно —
    Database.survey_test_status считает ровно это по starts_at/ends_at.

    Колокол — личный: он отвечает на вопрос «что ждёт меня». Управленческое
    число опросов «сколько не прошли мои люди» осталось бейджем самого раздела,
    у него другой смысл, и складывать их в одно было бы враньём.

    Про время. starts_at/ends_at хранятся НАИВНЫМИ во времени Алматы: их пишет
    _parse_survey_schedule_value без tzinfo, а сравнивает survey_test_status с
    datetime.now() — процесс живёт в Asia/Almaty. База же стоит в UTC, поэтому
    голый CURRENT_TIMESTAMP здесь давал сдвиг ровно на 5 часов: тест считался
    открытым ещё пять часов после закрытия и закрытым первые пять часов после
    открытия.
    """
    cursor.execute(
        """
        SELECT s.id, s.title, s.ends_at, s.is_test,
               COUNT(*) OVER () AS total
          FROM survey_assignments sa
          JOIN surveys s ON s.id = sa.survey_id
         WHERE sa.operator_id = %(user_id)s
           AND COALESCE(sa.status, '') <> 'completed'
           AND s.is_active
           AND (NOT s.is_test
                OR ((s.starts_at IS NULL OR s.starts_at <= %(now)s)
                    AND (s.ends_at IS NULL OR s.ends_at > %(now)s)))
         ORDER BY s.ends_at NULLS LAST, s.id DESC
         LIMIT %(limit)s
        """,
        {'user_id': viewer['user_id'], 'limit': ITEMS_PER_SOURCE,
         'now': _almaty_now()},
    )
    rows = cursor.fetchall()
    total = int(rows[0][4]) if rows else 0
    return total, [{
        'source': 'surveys',
        'id': row[0],
        'title': row[1],
        'body': ('Открыт до %s' % row[2].strftime('%d.%m')) if row[2]
                else ('Нужно пройти' if not row[3] else 'Тест доступен'),
        'at': _iso(row[2]),
        'view': 'surveys',
        'target': row[0],
        'tone': 'default',
    } for row in rows]


# ── Ивенты ───────────────────────────────────────────────────────────────────
def events(cursor, viewer):
    """Посты новее водяного знака зрителя, видимые ему, кроме своих.

    Условие видимости повторяет Database.count_unread_events: пустой набор
    отделов = пост для всех, иначе отдел зрителя должен быть среди получателей.
    """
    params = {'user_id': viewer['user_id'], 'dept': viewer.get('department_id'),
              'limit': ITEMS_PER_SOURCE}
    scope = '' if viewer.get('is_global') else """
           AND (
                (NOT EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id)
                 AND (e.department_id IS NULL OR e.department_id = %(dept)s))
                OR EXISTS (SELECT 1 FROM event_departments ed
                            WHERE ed.event_id = e.id AND ed.department_id = %(dept)s)
           )"""
    cursor.execute(
        """
        SELECT e.id, e.title, e.created_at, COUNT(*) OVER () AS total
          FROM events e
         WHERE e.author_id IS DISTINCT FROM %(user_id)s
           AND e.created_at > COALESCE(
                   (SELECT last_seen_at FROM event_reads WHERE user_id = %(user_id)s),
                   '-infinity'::timestamp)
        """ + scope + """
         ORDER BY e.id DESC
         LIMIT %(limit)s
        """,
        params,
    )
    rows = cursor.fetchall()
    total = int(rows[0][3]) if rows else 0
    return total, [{
        'source': 'events',
        'id': row[0],
        'title': row[1] or 'Новый пост',
        'body': '',
        'at': _iso(row[2]),
        'view': 'events',
        'target': row[0],
        'tone': 'default',
    } for row in rows]


# ── 4 You ────────────────────────────────────────────────────────────────────
def four_you(cursor, viewer):
    if not viewer.get('can_see_four_you'):
        return 0, []
    cursor.execute(
        """
        SELECT COUNT(*), MAX(created_at)
          FROM four_you_images
         WHERE uploaded_by IS DISTINCT FROM %(user_id)s
           AND created_at > COALESCE(
                   (SELECT last_seen_at FROM four_you_reads WHERE user_id = %(user_id)s),
                   '-infinity'::timestamp)
        """,
        {'user_id': viewer['user_id']},
    )
    total, last_at = cursor.fetchone()
    total = int(total or 0)
    if not total:
        return 0, []
    # Фото не имеют заголовков — показываем одной строкой, а не пятью пустыми.
    return total, [{
        'source': 'four_you',
        'id': 0,
        'title': 'Новые фото: %d' % total,
        'body': '',
        'at': _iso(last_at),
        'view': 'four_you',
        'target': None,
        'tone': 'default',
    }]


# ── Задачи ───────────────────────────────────────────────────────────────────
# Подписи причин согласованы с ACTION_NEED_META в taskActionNeeds.js.
_TASK_KIND_BODY = {
    'overdue': 'Просрочена',
    'returned': 'Вернули на доработку',
    'review': 'Ждёт вашей приёмки',
    'fresh': 'Поручена, работа не начата',
}


def tasks(cursor, viewer):
    """Задачи, ждущие действия лично зрителя, — правила бейджа сайдбара.

    Категории взаимоисключающие, у задачи ровно одна причина, самая срочная:
      review   — я поручитель, исполнитель сдал работу и ждёт приёмки;
      overdue  — я исполнитель, дедлайн прошёл, задача не закрыта;
      returned — мне вернули на доработку;
      fresh    — мне поручили, к работе ещё не приступил.
    Бэклог не считается — это очередь планирования, а не работа. Просмотренные
    уведомления (task_action_reads) не считаются, пока задачу не тронут снова.

    Время сравниваем часами процесса (%(now)s), а не базы: due_at хранится
    наивным во времени Алматы, база стоит в UTC — голый CURRENT_TIMESTAMP дал
    бы сдвиг на 5 часов, ровно как это было с окнами тестов у опросов.
    """
    cursor.execute(
        """
        SELECT id, subject, due_at, updated_at, kind, COUNT(*) OVER () AS total
          FROM (
            SELECT t.id, t.subject, t.due_at, t.updated_at,
                   CASE
                       WHEN t.status = 'completed' THEN 'review'
                       WHEN t.due_at IS NOT NULL AND t.due_at < %(now)s THEN 'overdue'
                       WHEN t.status = 'returned' THEN 'returned'
                       ELSE 'fresh'
                   END AS kind
              FROM tasks t
              LEFT JOIN task_action_reads r ON r.task_id = t.id AND r.user_id = %(user_id)s
             WHERE (t.status = 'completed'
                    AND COALESCE(t.requested_by_id, t.created_by) = %(user_id)s
                    AND (r.task_id IS NULL OR r.kind <> 'review' OR r.seen_at < t.updated_at))
                OR (t.assigned_to = %(user_id)s
                    AND t.is_backlog = FALSE
                    AND t.status IN ('assigned', 'in_progress', 'returned')
                    AND ((t.due_at IS NOT NULL AND t.due_at < %(now)s
                          AND (r.task_id IS NULL OR r.kind <> 'overdue' OR r.seen_at < t.updated_at))
                         OR (t.status = 'returned' AND (t.due_at IS NULL OR t.due_at >= %(now)s)
                             AND (r.task_id IS NULL OR r.kind <> 'returned' OR r.seen_at < t.updated_at))
                         OR (t.status = 'assigned' AND (t.due_at IS NULL OR t.due_at >= %(now)s)
                             AND (r.task_id IS NULL OR r.kind <> 'fresh' OR r.seen_at < t.updated_at))))
          ) needs
         ORDER BY array_position(ARRAY['overdue', 'returned', 'review', 'fresh']::text[], kind),
                  due_at NULLS LAST, id DESC
         LIMIT %(limit)s
        """,
        {'user_id': viewer['user_id'], 'limit': ITEMS_PER_SOURCE, 'now': _almaty_now()},
    )
    rows = cursor.fetchall()
    total = int(rows[0][5]) if rows else 0
    return total, [{
        'source': 'tasks',
        'id': row[0],
        'title': row[1] or 'Задача',
        'body': _TASK_KIND_BODY.get(row[4], ''),
        # У просрочки «когда» — это СРОК; у остальных — момент, когда задачу
        # поручили, вернули или сдали (updated_at).
        'at': _iso(row[2] if row[4] == 'overdue' else row[3]),
        'view': 'tasks',
        'target': row[0],
        'tone': 'warning' if row[4] == 'overdue' else 'default',
    } for row in rows]


_HANDLERS = {
    'wiki_ack': wiki_ack,
    'tasks': tasks,
    'lms': lms,
    'surveys': surveys,
    'events': events,
    'four_you': four_you,
}


def collect(cursor, viewer, only=None):
    """Счётчики и элементы всех доступных зрителю источников.

    Каждый источник считается под SAVEPOINT: раздел может быть ещё не
    развёрнут (нет таблицы) или сломан своей миграцией, и это не повод отдать
    500 на весь колокол — такой источник даёт ноль и строку в логе.
    """
    wanted = [s for s in SOURCES if s in _HANDLERS and (not only or s in only)]
    counts, items = {}, []

    for name in wanted:
        if name in viewer.get('hidden_sources', ()):  # раздел закрыт для роли
            counts[name] = 0
            continue
        cursor.execute('SAVEPOINT notif_source')
        try:
            count, source_items = _HANDLERS[name](cursor, viewer)
            cursor.execute('RELEASE SAVEPOINT notif_source')
        except Exception:
            cursor.execute('ROLLBACK TO SAVEPOINT notif_source')
            cursor.execute('RELEASE SAVEPOINT notif_source')
            logging.exception('Уведомления: источник %s не посчитан', name)
            count, source_items = 0, []
        counts[name] = int(count or 0)
        items.extend(source_items)

    # Общий порядок уже верен: источники идут в порядке SOURCES, а внутри
    # каждого — в порядке его собственного ORDER BY. Сортировать весь список
    # по дате было бы ошибкой: у ознакомлений и опросов `at` — это СРОК, и
    # «свежее сверху» подняло бы наверх самый дальний дедлайн вместо ближнего.
    #
    # Поэтому одна устойчивая сортировка: просроченное наверх, всё остальное
    # сохраняет свой порядок.
    items.sort(key=lambda item: item['tone'] != 'warning')
    counts['total'] = sum(counts.get(s, 0) for s in wanted)
    return counts, items


def mark_seen(cursor, user_id, source):
    """Погасить источник. Возвращает True, если источник это поддерживает.

    Ознакомления и опросы гасить нельзя: они снимаются действием (подтвердил /
    прошёл), а не фактом того, что пользователь их увидел. Иначе счётчик
    обязательных документов обнулялся бы просмотром колокола — то есть врал.
    """
    if source == 'events':
        cursor.execute(
            """
            INSERT INTO event_reads (user_id, last_seen_at)
            VALUES (%s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
            ON CONFLICT (user_id) DO UPDATE
                SET last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
            """,
            (user_id,),
        )
        return True
    if source == 'four_you':
        cursor.execute(
            """
            INSERT INTO four_you_reads (user_id, last_seen_at)
            VALUES (%s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
            """,
            (user_id,),
        )
        return True
    if source == 'lms':
        cursor.execute(
            """
            UPDATE lms_notifications
               SET is_read = TRUE,
                   read_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE user_id = %s AND is_read = FALSE
            """,
            (user_id,),
        )
        return True
    return False
