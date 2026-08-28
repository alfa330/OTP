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
from datetime import datetime, time as day_time, timedelta

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
SOURCES = ('wiki_ack', 'tasks', 'checkpoints', 'shift_requests', 'crm', 'lms',
           'surveys', 'events', 'four_you', 'birthdays')

# Сколько элементов тянем из одного источника в первой порции. Дальше клиент
# добирает следующие, когда пользователь докручивает список до низа: счётчик
# считает ВСЁ, и без догрузки бейдж «6» висел бы над пятью карточками.
ITEMS_PER_SOURCE = 5
# Потолок одной порции. Колокол — не лента: полный список пользователь смотрит
# в самом разделе, а безлимитная выдача превратила бы сводку в тяжёлый запрос.
MAX_ITEMS_PER_SOURCE = 50


def _iso(value):
    return value.isoformat() if value is not None else None


def _seconds_until(moment):
    """Сколько секунд осталось до момента, либо None.

    Клиенту уходит именно интервал, а не абсолютное время: поля вроде due_at
    хранятся НАИВНЫМИ во времени Алматы, и такую строку браузер прочитал бы как
    своё локальное время — у сотрудника в другом поясе таймер уехал бы на часы.
    Интервал же не зависит ни от пояса, ни от кривых часов на машине.
    """
    if moment is None:
        return None
    return max(0, int((moment - _almaty_now()).total_seconds()))


def _almaty_now():
    """«Сейчас» в том же виде, в каком раздел опросов хранит границы окна.

    Параметром, а не выражением в SQL: боевой процесс живёт в Asia/Almaty
    (os.environ['TZ'] + tzset() в начале bot_schedule2.py и database.py), и
    datetime.now() здесь совпадает с тем, с чем сравнивает
    Database.survey_test_status.
    """
    return datetime.now()


# ── Вики: статьи под обязательное ознакомление ───────────────────────────────
def wiki_ack(cursor, viewer, limit):
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
    } for row in rows[:limit]]
    return len(rows), items


# ── Обучение ─────────────────────────────────────────────────────────────────
def lms(cursor, viewer, limit):
    cursor.execute(
        """
        SELECT id, title, message, created_at,
               COUNT(*) OVER () AS total
          FROM lms_notifications
         WHERE user_id = %(user_id)s AND is_read = FALSE
         ORDER BY created_at DESC
         LIMIT %(limit)s
        """,
        {'user_id': viewer['user_id'], 'limit': limit},
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
def surveys(cursor, viewer, limit):
    """Опросы и тесты, назначенные лично зрителю и ещё не пройденные.

    Раньше это число считал фронт, выгрузив ВЕСЬ список опросов со всеми
    назначениями и статистикой ради одной цифры. Критерий здесь тот же, что был
    у оператора на клиенте (can_submit && статус ≠ completed), только выражен
    в SQL: назначение не завершено, а для теста ещё и открыто его окно —
    Database.survey_test_status считает ровно это по starts_at/ends_at.

    Архивные опросы (старше двух недель) сюда не попадают: архив и заведён,
    чтобы старое перестало числиться делом.

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
           AND s.archived_at IS NULL
           AND (NOT s.is_test
                OR ((s.starts_at IS NULL OR s.starts_at <= %(now)s)
                    AND (s.ends_at IS NULL OR s.ends_at > %(now)s)))
         ORDER BY s.ends_at NULLS LAST, s.id DESC
         LIMIT %(limit)s
        """,
        {'user_id': viewer['user_id'], 'limit': limit,
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
def events(cursor, viewer, limit):
    """Посты новее водяного знака зрителя, видимые ему, кроме своих.

    Условие видимости повторяет Database.count_unread_events: пустой набор
    отделов = пост для всех, иначе отдел зрителя должен быть среди получателей.
    """
    params = {'user_id': viewer['user_id'], 'dept': viewer.get('department_id'),
              'limit': limit}
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
def four_you(cursor, viewer, limit):
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
    'info': 'Исполнителю не хватает информации',
    'review': 'Ждёт вашей приёмки',
    'fresh': 'Поручена, работа не начата',
    'accepted': 'Работу приняли',
}


def tasks(cursor, viewer, limit):
    """Задачи, ждущие действия лично зрителя, — правила бейджа сайдбара.

    Категории взаимоисключающие, у задачи ровно одна причина, самая срочная:
      review   — я поручитель, исполнитель сдал работу и ждёт приёмки;
      info     — я отвечаю за постановку, исполнителю не хватает информации;
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
                       -- Строго раньше проверки дедлайна: у принятой задачи он
                       -- давно позади, и она попала бы в «просрочена».
                       WHEN t.status = 'accepted' THEN 'accepted'
                       -- Раньше дедлайна и статусов: строку привёл сюда открытый
                       -- запрос информации, а не работа зрителя — он тут не
                       -- исполнитель, и «просрочена»/«не начата» про него ложь.
                       -- Причина адресована стороне постановки, но отсекать
                       -- надо не «всех исполнителей», а именно СПРАШИВАВШЕГО:
                       -- при нескольких исполнителях постановщик может сам быть
                       -- одним из них, и тогда вопрос коллеги от него прятался.
                       WHEN t.info_request_id IS NOT NULL
                            AND COALESCE((SELECT m.author_id FROM task_messages m
                                           WHERE m.id = t.info_request_id), 0)
                                <> %(user_id)s
                            AND COALESCE(t.requested_by_id, t.created_by) = %(user_id)s
                            THEN 'info'
                       WHEN t.due_at IS NOT NULL AND t.due_at < %(now)s THEN 'overdue'
                       WHEN t.status = 'returned' THEN 'returned'
                       ELSE 'fresh'
                   END AS kind
              FROM tasks t
              LEFT JOIN task_action_reads r ON r.task_id = t.id AND r.user_id = %(user_id)s
             WHERE (t.status = 'completed'
                    AND COALESCE(t.requested_by_id, t.created_by) = %(user_id)s
                    AND (r.task_id IS NULL OR r.kind <> 'review' OR r.seen_at < t.updated_at))
                -- Единственное уведомление «к сведению», а не «сделай»: работу
                -- приняли. Два отличия от остальных причин, оба намеренные.
                -- 1) Отметка о просмотре ВЕЧНАЯ (нет сравнения с updated_at):
                --    «тронули — посмотри заново» верно для живой задачи, а
                --    принятую правка отчёта или тега воскрешала бы снова и снова.
                -- 2) Себе не сообщаем: если исполнитель и есть принимающий,
                --    он сам нажал «Принять» — уведомлять его о своём же клике
                --    незачем (так же поступают «Ивенты» и «4 You» с автором).
                OR (t.status = 'accepted'
                    AND EXISTS (SELECT 1 FROM task_assignees ta
                                WHERE ta.task_id = t.id AND ta.user_id = %(user_id)s)
                    AND COALESCE(t.requested_by_id, t.created_by) IS DISTINCT FROM %(user_id)s
                    AND (r.task_id IS NULL OR r.kind <> 'accepted'))
                -- Исполнителю не хватает информации. Бэклог считается, в
                -- отличие от остальных причин: вопрос задали живому человеку, и
                -- «задача ещё в очереди» ответа не отменяет.
                OR (t.info_request_id IS NOT NULL
                    AND t.status IN ('assigned', 'in_progress', 'returned')
                    AND COALESCE(t.requested_by_id, t.created_by) = %(user_id)s
                    AND COALESCE((SELECT m.author_id FROM task_messages m
                                   WHERE m.id = t.info_request_id), 0)
                        <> %(user_id)s
                    AND (r.task_id IS NULL OR r.kind <> 'info' OR r.seen_at < t.updated_at))
                OR (EXISTS (SELECT 1 FROM task_assignees ta
                            WHERE ta.task_id = t.id AND ta.user_id = %(user_id)s)
                    AND t.is_backlog = FALSE
                    AND t.status IN ('assigned', 'in_progress', 'returned')
                    AND ((t.due_at IS NOT NULL AND t.due_at < %(now)s
                          AND (r.task_id IS NULL OR r.kind <> 'overdue' OR r.seen_at < t.updated_at))
                         OR (t.status = 'returned' AND (t.due_at IS NULL OR t.due_at >= %(now)s)
                             AND (r.task_id IS NULL OR r.kind <> 'returned' OR r.seen_at < t.updated_at))
                         OR (t.status = 'assigned' AND (t.due_at IS NULL OR t.due_at >= %(now)s)
                             AND (r.task_id IS NULL OR r.kind <> 'fresh' OR r.seen_at < t.updated_at))))
          ) needs
         ORDER BY array_position(
                      ARRAY['overdue', 'returned', 'info', 'review', 'fresh', 'accepted']::text[], kind),
                  due_at NULLS LAST, id DESC
         LIMIT %(limit)s
        """,
        {'user_id': viewer['user_id'], 'limit': limit, 'now': _almaty_now()},
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



# ── Обращения ────────────────────────────────────────────────────────────────
def crm(cursor, viewer, limit):
    """Ответы и отметки «выполнено» по обращениям, которые зритель ещё не читал.

    Адресат один — автор обращения: это он ждёт ответа из Telegram-группы.
    Супервайзеру и главе отдела раздел виден целиком, но звонить им по чужой
    переписке было бы шумом, поэтому здесь только своё.

    Гасится не колоколом, а открытием карточки (как ознакомления вики):
    «вам ответили» нельзя закрыть, просто заглянув в список — ответ нужно
    прочитать. Поэтому у источника нет ветки в mark_seen.
    """
    total, rows = _crm_queries().unread_for_bell(cursor, viewer['user_id'], limit)
    return total, [{
        'source': 'crm',
        'id': row[0],
        'title': row[1],
        'body': _CRM_UNREAD_LABELS.get(row[2], 'Обновление по обращению'),
        'at': _iso(row[3]),
        'view': 'crm_tickets',
        'target': row[0],
        # Тон предупреждения тут не нужен: ответ на обращение — это хорошая
        # новость, а не просрочка. Цветом в колоколе помечается горящее.
        'tone': 'default',
    } for row in rows]


# Что именно ждёт автора. Подписи короткие: в колоколе строка — не место для
# пересказа переписки, детали человек увидит в карточке.
_CRM_UNREAD_LABELS = {
    'reply': 'Пришёл ответ',
    'done': 'Обращение выполнено',
    'progress': 'Взяли в работу',
}


def _crm_queries():
    """Импорт SQL-слоя раздела откладываем до вызова.

    На уровне модуля он тянул бы пакет crm в любой процесс, который трогает
    уведомления, — включая тесты, которым раздел не нужен.
    """
    from crm import queries as crm_queries
    return crm_queries


# ── Дни рождения ─────────────────────────────────────────────────────────────
def birthdays(cursor, viewer, limit):
    """Сегодняшние именинники — и только те, что в отделе зрителя.

    Периметр здесь у́же, чем у любого другого источника, и это правило
    владельца, а не осторожность: дата рождения — личные данные сотрудника, и
    человек из чужого отдела о ней знать не должен. Видят всех только
    глобальные админы, чей периметр и так весь портал; всем остальным, включая
    тренеров и СВ, показываем свой отдел. Тем самым источник НЕ повторяет
    границу «Ивентов»: там тренер глобален, здесь — нет.

    Свой день рождения виден всегда, даже если отдела у человека нет: иначе
    единственный, кого праздник касается лично, о нём бы и не узнал.

    Момента у события нет намеренно (`at` = None): «сегодня» — это и есть весь
    его срок, а дата, прогнанная через клиентский fmtWhen, после полудня
    превращалась бы во «вчера».

    Гасится кнопкой «отметить прочитанным», и водяной знак здесь ДАТА, а не
    момент (см. mark_seen): список именинников целиком меняется в полночь, и
    отметка «видел» обязана истечь вместе с ним.
    """
    today = _almaty_now().date()
    params = {'user_id': viewer['user_id'], 'dept': viewer.get('birthday_department_id'),
              'today': today, 'month': today.month, 'day': today.day, 'limit': limit}
    # Периметр отдела; своя строка проходит в любом случае — в том числе когда
    # отдела нет вовсе и сравнение с NULL не дало бы ни одной строки.
    scope = '' if viewer.get('birthday_is_global') else """
           AND (u.department_id = %(dept)s OR u.id = %(user_id)s)"""
    cursor.execute(
        """
        SELECT u.id, u.name, d.name, (u.id = %(user_id)s) AS is_self,
               COUNT(*) OVER () AS total
          FROM users u
          LEFT JOIN directions d ON d.id = u.direction_id
         WHERE u.birth_date IS NOT NULL
           AND EXTRACT(MONTH FROM u.birth_date) = %(month)s
           AND EXTRACT(DAY FROM u.birth_date) = %(day)s
           AND (u.status IS NULL OR u.status NOT IN ('fired', 'dismissal'))
           AND NOT EXISTS (SELECT 1 FROM birthday_reads br
                            WHERE br.user_id = %(user_id)s
                              AND br.last_seen_on = %(today)s)
        """ + scope + """
         ORDER BY is_self DESC, u.name
         LIMIT %(limit)s
        """,
        params,
    )
    rows = cursor.fetchall()
    total = int(rows[0][4]) if rows else 0
    return total, [{
        'source': 'birthdays',
        'id': row[0],
        # Заголовок — только имя: что это именно день рождения, уже сказано
        # подписью источника, и повторять её в строке значило бы шуметь.
        'title': ('%s (вы)' % row[1]) if row[3] else (row[1] or 'Сотрудник'),
        'body': row[2] or '',
        'at': None,
        # Раздела за этим нет: поздравлять человек идёт не в портал. Клик по
        # строке просто закрывает панель — onNavigate на пустой view выходит
        # сразу (см. App.jsx::stableNotificationsNavigate).
        'view': None,
        'target': None,
        # Праздник не горит: 'warning' в колоколе означает просрочку.
        'tone': 'default',
    } for row in rows]


# ── Контроль: контрольные точки по сотруднику ────────────────────────────────
def checkpoints(cursor, viewer, limit):
    """Назначенные повторные проверки — с двух сторон одного события.

    Клик по строке открывает «Журнал оценок» → вкладку «Контроль».

    РУКОВОДИТЕЛЮ (супервайзер, глава отдела, админ) точка показывается только
    когда СРОК НАСТУПИЛ: `due_date <= сегодня`. Пока проверка впереди, в
    колоколе тихо — иначе назначенная на месяц вперёд точка висела бы там все
    тридцать дней и приучила бы не смотреть на колокол вовсе. Просроченная
    горит (`tone` = warning): «не потерять срок контроля» — это ровно та
    задача, ради которой раздел и сделан.

    СОТРУДНИКУ та же точка показывается СРАЗУ, с момента постановки: ему нужно
    знать, что исправить к проверке, а не узнать об этом в день Х. И показывается
    ему СОВСЕМ ДРУГОЙ ТЕКСТ — без вида контроля (в том числе без слов
    «испытательный срок»), без причины постановки и без внутреннего комментария
    супервайзера. Это требование постановки задачи #86, и держится оно тем, что
    служебные колонки в этой ветке просто не выбираются из базы.

    Точку нельзя «погасить» просмотром (её нет в mark_seen) — по той же причине,
    что ознакомление и опрос: она снимается ДЕЙСТВИЕМ, когда проверку провели.
    Иначе счётчик контроля обнулялся бы фактом открытия колокола, то есть врал.
    """
    is_manager = bool((viewer.get('checkpoints') or {}).get('is_manager'))
    today = _almaty_now().date()
    params = {'user_id': viewer['user_id'], 'limit': limit, 'today': today}

    if not is_manager:
        cursor.execute(
            """
            SELECT c.id, c.due_date, c.focus, COUNT(*) OVER () AS total
              FROM operator_checkpoints c
             WHERE c.status = 'open'
               AND c.notify_operator
               AND c.operator_id = %(user_id)s
             ORDER BY c.due_date, c.id
             LIMIT %(limit)s
            """,
            params,
        )
        rows = cursor.fetchall()
        total = int(rows[0][3]) if rows else 0
        return total, [{
            'source': 'checkpoints',
            'id': row[0],
            # Заголовок нейтральный и одинаковый для всех видов контроля:
            # «испытательный срок» сотруднику не показывается.
            'title': 'Повторная проверка качества',
            'body': ('до %s · %s' % (row[1].strftime('%d.%m'), row[2] or '')).strip(' ·'),
            'at': None,
            # Ведём в его собственные оценки: там та же проверка показана
            # карточкой у оценки, из-за которой её назначили.
            'view': 'evaluation',
            'target': None,
            'tone': 'default',
        } for row in rows]

    # Локальный импорт: раздел разворачивается под своим SAVEPOINT и может не
    # примениться — падать импортом на старте всего колокола из-за этого нельзя.
    from trainings.checkpoints import scope_clause
    from trainings.schema import CHECKPOINT_KIND_LABELS
    scope_sql = scope_clause(viewer.get('checkpoints') or {}, params)
    cursor.execute(
        """
        SELECT c.id, op.name, c.kind, c.due_date, COUNT(*) OVER () AS total
          FROM operator_checkpoints c
          JOIN users op ON op.id = c.operator_id
         WHERE c.status = 'open'
           AND c.due_date <= %(today)s
        """ + scope_sql + """
         ORDER BY c.due_date, c.id
         LIMIT %(limit)s
        """,
        params,
    )
    rows = cursor.fetchall()
    total = int(rows[0][4]) if rows else 0
    items = []
    for row in rows:
        overdue_days = (today - row[3]).days
        if overdue_days > 0:
            body = 'просрочена на %d дн. · %s' % (overdue_days, CHECKPOINT_KIND_LABELS.get(row[2], ''))
        else:
            body = 'проверка сегодня · %s' % CHECKPOINT_KIND_LABELS.get(row[2], '')
        items.append({
            'source': 'checkpoints',
            'id': row[0],
            'title': row[1] or 'Сотрудник',
            'body': body.strip(' ·'),
            'at': None,
            # Вкладка «Контроль» живёт в «Журнале оценок», а не в «Тренингах»:
            # контроль ставят там же, где разбирают звонок.
            'view': 'call_evaluation',
            'target': row[0],
            'tone': 'warning' if overdue_days > 0 else 'default',
        })
    return total, items


# ── Заявки на изменение смены ────────────────────────────────────────────────

# Подписи вида заявки. Короткие: в колоколе строка — не место для пересказа
# интервалов, точные времена человек увидит в самой заявке.
_SHIFT_REQUEST_KIND_LABELS = {
    'shorten': 'сокращение смены',
    'extra': 'дополнительная часть смены',
}

_SHIFT_REQUEST_DECISION_LABELS = {
    'approved': 'Заявка по смене одобрена',
    'rejected': 'Заявка по смене отклонена',
}


def _shift_request_scope_clause(descriptor, params):
    """SQL-условие «чьи заявки видит руководитель» + параметры в params.

    Описание границы приходит из bot_schedule2._shift_change_scope_for_requester —
    там же, где оно посчитано для самого раздела. Здесь только перевод в SQL.
    """
    descriptor = descriptor or {}
    scope = str(descriptor.get('scope') or 'none')
    if scope == 'all':
        return ''
    if scope == 'departments':
        ids = [int(value) for value in (descriptor.get('department_ids') or [])]
        if not ids:
            return ' AND FALSE'
        params['scr_departments'] = ids
        return ' AND op.department_id = ANY(%(scr_departments)s)'
    return ' AND FALSE'


def shift_requests(cursor, viewer, limit):
    """Заявки на изменение смены — с двух сторон одного события (задача #17).

    РУКОВОДИТЕЛЮ (супервайзер, глава отдела, админ) показываются ОЖИДАЮЩИЕ
    заявки его периметра: это очередь, которую он разбирает кнопкой «Запросы»
    в «Графиках работы». Разобранная заявка из колокола уходит сама — гасить
    её просмотром нельзя, она снимается ДЕЙСТВИЕМ, как ознакомление и опрос.
    Иначе очередь согласования обнулялась бы взглядом на колокол, то есть врала.

    ОПЕРАТОРУ показывается РЕШЕНИЕ по его собственной заявке — и только пока он
    его не видел (operator_seen_at). Ожидающую свою заявку ему не показываем:
    он сам её только что отправил, напоминать об этом нечем.

    Отклонённая заявка идёт нейтральным тоном, а не warning: warning в колоколе
    зарезервирован под просрочку — под то, что горит. Отказ — это ответ, а не
    горящий срок.
    """
    scope = viewer.get('shift_requests') or {}
    scope_kind = str(scope.get('scope') or 'none')
    params = {'user_id': viewer['user_id'], 'limit': limit}

    if scope_kind in ('none', 'self'):
        cursor.execute(
            """
            SELECT r.id, r.status, r.request_kind, r.shift_date, r.reviewed_at,
                   COUNT(*) OVER () AS total
              FROM work_shift_change_requests r
             WHERE r.operator_id = %(user_id)s
               AND r.status IN ('approved', 'rejected')
               AND r.operator_seen_at IS NULL
             ORDER BY r.reviewed_at DESC NULLS LAST, r.id DESC
             LIMIT %(limit)s
            """,
            params,
        )
        rows = cursor.fetchall()
        total = int(rows[0][5]) if rows else 0
        return total, [{
            'source': 'shift_requests',
            'id': row[0],
            'title': _SHIFT_REQUEST_DECISION_LABELS.get(row[1], 'Решение по заявке'),
            'body': ('%s · %s' % (
                row[3].strftime('%d.%m'),
                _SHIFT_REQUEST_KIND_LABELS.get(row[2], ''),
            )).strip(' ·'),
            'at': _iso(row[4]),
            'view': 'work_schedules',
            'target': row[0],
            'tone': 'default',
        } for row in rows]

    scope_sql = _shift_request_scope_clause(scope, params)
    cursor.execute(
        """
        SELECT r.id, op.name, r.request_kind, r.shift_date, r.created_at,
               COUNT(*) OVER () AS total
          FROM work_shift_change_requests r
          JOIN users op ON op.id = r.operator_id
         WHERE r.status = 'pending'
           AND r.operator_id <> %(user_id)s
        """ + scope_sql + """
         ORDER BY r.shift_date, r.id
         LIMIT %(limit)s
        """,
        params,
    )
    rows = cursor.fetchall()
    total = int(rows[0][5]) if rows else 0
    return total, [{
        'source': 'shift_requests',
        'id': row[0],
        'title': row[1] or 'Сотрудник',
        'body': ('%s · %s' % (
            row[3].strftime('%d.%m'),
            _SHIFT_REQUEST_KIND_LABELS.get(row[2], ''),
        )).strip(' ·'),
        'at': _iso(row[4]),
        'view': 'work_schedules',
        'target': row[0],
        'tone': 'default',
    } for row in rows]


_HANDLERS = {
    'wiki_ack': wiki_ack,
    'tasks': tasks,
    'checkpoints': checkpoints,
    'shift_requests': shift_requests,
    'crm': crm,
    'lms': lms,
    'surveys': surveys,
    'events': events,
    'four_you': four_you,
    'birthdays': birthdays,
}


def collect(cursor, viewer, only=None, limit=ITEMS_PER_SOURCE):
    """(счётчики, элементы, есть ли ещё) по всем доступным зрителю источникам.

    Каждый источник считается под SAVEPOINT: раздел может быть ещё не
    развёрнут (нет таблицы) или сломан своей миграцией, и это не повод отдать
    500 на весь колокол — такой источник даёт ноль и строку в логе.

    `has_more` — сигнал клиенту «докрути список, там ещё есть»: счётчики всегда
    считают ВСЁ, а элементов отдаётся не больше limit на источник, и без этого
    флага бейдж «6» висел бы над пятью карточками без всякой возможности
    добраться до шестой.
    """
    # Любая бессмыслица (ноль, отрицательное, не число) — это «клиент не указал
    # порцию», а не «отдай одну строку»: берём дефолт, а не крайность.
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = ITEMS_PER_SOURCE
    if limit < 1:
        limit = ITEMS_PER_SOURCE
    limit = min(limit, MAX_ITEMS_PER_SOURCE)
    wanted = [s for s in SOURCES if s in _HANDLERS and (not only or s in only)]
    counts, items = {}, []
    has_more = False

    for name in wanted:
        if name in viewer.get('hidden_sources', ()):  # раздел закрыт для роли
            counts[name] = 0
            continue
        cursor.execute('SAVEPOINT notif_source')
        try:
            count, source_items = _HANDLERS[name](cursor, viewer, limit)
            cursor.execute('RELEASE SAVEPOINT notif_source')
        except Exception:
            cursor.execute('ROLLBACK TO SAVEPOINT notif_source')
            cursor.execute('RELEASE SAVEPOINT notif_source')
            logging.exception('Уведомления: источник %s не посчитан', name)
            count, source_items = 0, []
        counts[name] = int(count or 0)
        # Оба условия обязательны. Одного «счётчик больше показанного» мало:
        # «4 You» сворачивает любое число фото в ОДНУ строку «Новые фото: 12»,
        # и по нему клиент до бесконечности просил бы порцию за порцией, ничего
        # нового не получая. Упёрлись в limit — значит следующая порция есть.
        if int(count or 0) > len(source_items) and len(source_items) >= limit:
            has_more = True
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

    # Момент следующего перехода по часам — под тем же SAVEPOINT: он заменяет
    # фоновую сверку, но сам по себе не настолько важен, чтобы из-за него
    # разваливалась вся сводка.
    cursor.execute('SAVEPOINT notif_next_change')
    try:
        upcoming = next_change_at(cursor, viewer)
        cursor.execute('RELEASE SAVEPOINT notif_next_change')
    except Exception:
        cursor.execute('ROLLBACK TO SAVEPOINT notif_next_change')
        cursor.execute('RELEASE SAVEPOINT notif_next_change')
        logging.exception('Уведомления: не удалось вычислить следующий переход')
        upcoming = None

    meta = {'has_more': has_more, 'next_change_in': _seconds_until(upcoming)}
    return counts, items, meta


def next_change_at(cursor, viewer):
    """Ближайший момент, когда сводка изменится САМА, без чьего-либо действия.

    Почти всё в колоколе меняется от записи в БД, и об этом мгновенно сообщает
    триггер. Но три вещи наступают просто по часам, не оставляя следа в базе:
    открывается окно теста, закрывается окно теста, наступает дедлайн (задача
    становится просроченной, ознакомление — горящим).

    Раньше это закрывала сверка раз в минуту — то есть обычный фоновый опрос,
    ради которого весь механизм триггеров и затевался, чтобы его не было.
    Вместо него сервер отдаёт МОМЕНТ следующего перехода, и клиент просыпается
    ровно к нему: холостых запросов не остаётся вовсе.

    Возвращает naive datetime во времени Алматы (как хранятся сами поля) либо
    None, если впереди ничего не намечено.
    """
    hidden = viewer.get('hidden_sources', ()) or ()
    now = _almaty_now()
    params = {'user_id': viewer['user_id'], 'now': now}
    parts = []

    if 'surveys' not in hidden:
        # Окно теста: и открытие, и закрытие меняют состав сводки.
        parts.append("""
            SELECT MIN(moment) FROM (
                SELECT s.starts_at AS moment
                  FROM survey_assignments sa JOIN surveys s ON s.id = sa.survey_id
                 WHERE sa.operator_id = %(user_id)s AND COALESCE(sa.status, '') <> 'completed'
                   AND s.is_active AND s.is_test AND s.archived_at IS NULL
                   AND s.starts_at > %(now)s
                UNION ALL
                SELECT s.ends_at
                  FROM survey_assignments sa JOIN surveys s ON s.id = sa.survey_id
                 WHERE sa.operator_id = %(user_id)s AND COALESCE(sa.status, '') <> 'completed'
                   AND s.is_active AND s.is_test AND s.archived_at IS NULL
                   AND s.ends_at > %(now)s
            ) w""")

    if 'tasks' not in hidden:
        # Дедлайн задачи: наступив, он меняет причину на «просрочена» и может
        # вернуть в счётчик задачу, чьё прежнее уведомление уже просмотрели.
        parts.append("""
            SELECT MIN(t.due_at)
              FROM tasks t
             WHERE EXISTS (SELECT 1 FROM task_assignees ta
                            WHERE ta.task_id = t.id AND ta.user_id = %(user_id)s)
               AND t.is_backlog = FALSE
               AND t.status IN ('assigned', 'in_progress', 'returned')
               AND t.due_at > %(now)s""")

    if 'checkpoints' not in hidden and (viewer.get('checkpoints') or {}).get('is_manager'):
        # Контрольная точка появляется у руководителя не от записи в базу, а от
        # календаря: в полночь дня проверки. Триггеру тут взяться неоткуда —
        # ровно тот же случай, что у именинников ниже.
        from trainings.checkpoints import scope_clause
        parts.append("""
            SELECT MIN(c.due_date::timestamp)
              FROM operator_checkpoints c
              JOIN users op ON op.id = c.operator_id
             WHERE c.status = 'open'
               AND c.due_date::timestamp > %(now)s"""
            + scope_clause(viewer.get('checkpoints') or {}, params))

    if 'wiki_ack' not in hidden:
        # Срок ознакомления: счётчик не меняет, но документ становится горящим
        # и поднимается наверх списка — это видимое изменение.
        parts.append("""
            SELECT MIN(aa.due_at)
              FROM wiki_ack_assignments aa JOIN wiki_articles a ON a.id = aa.article_id
             WHERE aa.user_id = %(user_id)s AND aa.acknowledged_at IS NULL
               AND aa.status NOT IN ('superseded', 'cancelled') AND a.status = 'published'
               AND aa.due_at > %(now)s""")

    # Полночь. Единственный переход, за которым не стоит вообще никакой записи
    # в базе: с календарным днём меняется весь список именинников и истекает
    # отметка «видел». Триггеру тут взяться неоткуда, поэтому без этого момента
    # ночная смена, у которой портал открыт с вечера, узнала бы о сегодняшнем
    # празднике только по возврату фокуса.
    #
    # Границы честные: клиент просыпается по таймеру, только пока вкладка
    # видима (NotificationsBell::scheduleNextChange выходит на hidden), — у
    # свёрнутого окна переход всё равно ловится обновлением по фокусу.
    midnight = None
    if 'birthdays' not in hidden:
        midnight = datetime.combine(now.date() + timedelta(days=1), day_time.min)

    if not parts:
        return midnight

    cursor.execute('SELECT MIN(moment) FROM (%s) AS all_moments(moment)'
                   % ' UNION ALL '.join(parts), params)
    row = cursor.fetchone()
    moment = row[0] if row else None
    if moment is None:
        return midnight
    return moment if midnight is None else min(moment, midnight)


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
    if source == 'birthdays':
        # Знак ДАТЫ, а не момента: список именинников целиком меняется в
        # полночь, и отметка «видел» обязана истечь вместе с ним. С момента
        # (last_seen_at) вчерашнее «прочитано» пришлось бы каждый раз
        # сравнивать с началом суток — то же самое, только окольно.
        #
        # Дата берётся из процесса, а не из базы: та живёт в UTC, и с 00:00 до
        # 05:00 по Алматы CURRENT_DATE указывал бы на вчера — человек гасил бы
        # уже прошедший день, а сегодняшние именинники оставались бы в колоколе.
        cursor.execute(
            """
            INSERT INTO birthday_reads (user_id, last_seen_on)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET last_seen_on = EXCLUDED.last_seen_on
            """,
            (user_id, _almaty_now().date()),
        )
        return True
    return False
