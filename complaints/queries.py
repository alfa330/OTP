# -*- coding: utf-8 -*-
"""SQL-слой раздела «Жалобы».

Функции принимают ГОТОВЫЙ курсор и не управляют ни пулом, ни транзакцией —
так же, как crm/queries.py. Одно действие здесь почти всегда меняет несколько
таблиц разом (жалоба, журнал работы, запись в «Тренингах», история), и один
курсор = одна транзакция: запись о тренинге не может лечь без записи в журнале
жалобы и наоборот.

Строки отдаются словарями по cursor.description, а не по номерам столбцов:
у жалобы полсотни полей, и разбор кортежа по индексам сломался бы на первом же
добавленном столбце.
"""

import json
from datetime import date, datetime, timedelta

from crm import queries as crm_queries

from . import access, catalog

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


def _iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _dicts(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(cursor):
    rows = _dicts(cursor)
    return rows[0] if rows else None


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа — тот же запрос, что у «Обращений»: профиль, отделы, где
# человек глава, и группы, где он супервайзер. Второй такой же запрос означал
# бы две формы одного и того же периметра.
# ─────────────────────────────────────────────────────────────────────────────

def load_access_context(cursor, user_id):
    from crm import queries as crm_queries

    return crm_queries.load_access_context(cursor, user_id)


_CURRENT_GROUP_MEMBER = """
    SELECT 1 FROM group_operator_memberships gom
     WHERE gom.operator_id = c.created_by
       AND gom.group_id = ANY(%(viewer_groups)s)
       AND gom.start_date <= CURRENT_DATE
       AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
"""


# Жалоба на проверке (Яндекс) у супервайзера отдела автора — форма
# access.reviews_for_department: проверяет её любой СВ отдела, и любой из них её
# видит и разбирает. Своя жалоба — не его: её решает коллега.
_DEPARTMENT_REVIEW = ('(c.review_state IS NOT NULL'
                      ' AND c.creator_department_id = %(viewer_department)s'
                      ' AND c.created_by IS DISTINCT FROM %(viewer_id)s)')


def visibility_sql(ctx):
    """Условие «жалоба видна пользователю» для WHERE. Возвращает (sql, params).

    Те же правила, что access.can_view, но по списку. Сверяются тестом.
    """
    params = {'viewer_id': int(ctx['user_id'])}
    if access.visibility_scope(ctx) == access.SCOPE_ALL:
        return 'TRUE', params

    clauses = ['c.created_by = %(viewer_id)s', 'c.responsible_id = %(viewer_id)s']
    headed = [int(x) for x in (ctx.get('headed_department_ids') or [])]
    if headed:
        params['headed'] = headed
        clauses.append('c.target_department_id = ANY(%(headed)s)')
        clauses.append('c.creator_department_id = ANY(%(headed)s)')
    if access.is_supervisor(ctx):
        if ctx.get('department_id') is not None:
            params['viewer_department'] = int(ctx['department_id'])
            clauses.append('c.target_department_id = %(viewer_department)s')
            clauses.append(_DEPARTMENT_REVIEW)
        groups = [int(x) for x in (ctx.get('group_ids') or [])]
        if groups:
            params['viewer_groups'] = groups
            clauses.append('EXISTS (%s)' % _CURRENT_GROUP_MEMBER)
    # Тот, на кого жалуются, свою жалобу не видит. IS DISTINCT FROM, а не <>:
    # у жалобы без сотрудника employee_id NULL, и «<>» отбросил бы её тоже.
    return ('(c.employee_id IS DISTINCT FROM %%(viewer_id)s AND (%s))'
            % ' OR '.join(clauses)), params


def handling_sql(ctx):
    """Условие «пользователь разбирает эту жалобу» — для очереди «К разбору».

    Форма access.can_handle для списка. Видимость подразумевается: в запросе
    оба условия стоят рядом.
    """
    params = {'viewer_id': int(ctx['user_id'])}
    if access.is_global_admin(ctx):
        return 'TRUE', params
    clauses = ['c.responsible_id = %(viewer_id)s']
    headed = [int(x) for x in (ctx.get('headed_department_ids') or [])]
    if headed:
        params['headed'] = headed
        clauses.append('c.target_department_id = ANY(%(headed)s)')
        clauses.append('(c.target_department_id IS NULL '
                       'AND c.creator_department_id = ANY(%(headed)s))')
    if access.is_supervisor(ctx):
        if ctx.get('department_id') is not None:
            params['viewer_department'] = int(ctx['department_id'])
            clauses.append('c.target_department_id = %(viewer_department)s')
            clauses.append('(c.target_department_id IS NULL AND %s)' % _DEPARTMENT_REVIEW)
        groups = [int(x) for x in (ctx.get('group_ids') or [])]
        if groups:
            params['viewer_groups'] = groups
            clauses.append('(c.target_department_id IS NULL AND EXISTS (%s))'
                           % _CURRENT_GROUP_MEMBER)
    return '(%s)' % ' OR '.join(clauses), params


# ─────────────────────────────────────────────────────────────────────────────
# Справочники: отделы, сотрудники, офисы, парки
# ─────────────────────────────────────────────────────────────────────────────

def departments(cursor):
    """Подразделения КЦ и фронт-офис — живым списком из departments."""
    cursor.execute(
        """
        SELECT id, code, name FROM departments
         WHERE is_active AND code = ANY(%(codes)s)
        """,
        {'codes': list(catalog.HANDLER_DEPARTMENT_CODES)},
    )
    rows = _dicts(cursor)
    order = {code: index for index, code in enumerate(catalog.HANDLER_DEPARTMENT_CODES)}
    rows.sort(key=lambda row: order.get(row['code'], 99))
    return [{
        'id': row['id'],
        'code': row['code'],
        'name': row['name'],
        'label': catalog.department_label(row['code'], row['name']),
        'call_center': row['code'] in catalog.CALL_CENTER_DEPARTMENT_CODES,
    } for row in rows]


def department_by_id(cursor, department_id):
    if department_id is None:
        return None
    cursor.execute('SELECT id, code, name FROM departments WHERE id = %s', (int(department_id),))
    return _one(cursor)


# Уволенных в выборе нет: жалоба приходит на того, кто работает сейчас.
# «Б/С» и отпуска остаются — жалоба могла прийти на смену до отпуска.
_NOT_FIRED = "COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')"
_EMPLOYEE_ROLES = ('operator', 'trainee', 'sv', 'trainer')


def employees(cursor, department_id):
    """Сотрудники отдела для выбора «на кого жалоба»: ФИО и группа."""
    cursor.execute(
        """
        SELECT u.id, u.name, u.role,
               (SELECT g.name FROM group_operator_memberships gom
                  JOIN groups g ON g.id = gom.group_id
                 WHERE gom.operator_id = u.id
                   AND gom.start_date <= CURRENT_DATE
                   AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
                 ORDER BY gom.start_date DESC LIMIT 1) AS group_name
          FROM users u
         WHERE u.department_id = %(department)s
           AND u.role = ANY(%(roles)s)
           AND {not_fired}
         ORDER BY u.name
        """.format(not_fired=_NOT_FIRED),
        {'department': int(department_id), 'roles': list(_EMPLOYEE_ROLES)},
    )
    return _dicts(cursor)


def employee(cursor, employee_id):
    """Сотрудник и тот, кто с ним работает: его супервайзер или глава отдела.

    Супервайзер берётся из users.supervisor_id — он производный от группы
    (синхронизируется при каждой смене членства). Нет супервайзера или он
    уволен — задача уходит главе отдела сотрудника: ТЗ требует довести работу
    до конца, а «ничей» сотрудник остался бы без неё навсегда.
    """
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, u.department_id, u.status,
               CASE WHEN sv.id IS NOT NULL AND sv.id <> u.id
                         AND COALESCE(sv.status, 'working') NOT IN ('fired', 'dismissal')
                    THEN sv.id END AS supervisor_id,
               -- Глава — только работающий и не сам сотрудник: жалоба бывает и
               -- на главу, и разбирать её себе он не может.
               CASE WHEN hu.id IS NOT NULL AND hu.id <> u.id
                         AND COALESCE(hu.status, 'working') NOT IN ('fired', 'dismissal')
                    THEN hu.id END AS head_id
          FROM users u
          LEFT JOIN users sv ON sv.id = u.supervisor_id
          LEFT JOIN departments d ON d.id = u.department_id AND d.is_active
          LEFT JOIN users hu ON hu.id = d.head_user_id
         WHERE u.id = %s
        """,
        (int(employee_id),),
    )
    row = _one(cursor)
    if row:
        # Может остаться пустым: у фронт-офисов на 29.09.2026 нет ни главы, ни
        # СВ. Тогда жалоба не теряется — её разбирают глобальные админы из
        # «К разбору», а карточка прямо пишет «ответственный не назначен».
        row['responsible_id'] = row['supervisor_id'] or row['head_id']
    return row


def offices(cursor, space_ids):
    """Наши офисы (парка, действующие) пространства отдела СЗоВ — для выбора
    «какой офис» у жалобы на фронт-офис. Тот же фильтр, что у «Статуса работы
    офиса» в «Обращениях» (crm.queries.city_offices)."""
    space_ids = [int(x) for x in (space_ids or [])]
    if not space_ids:
        return []
    cursor.execute(
        """
        SELECT o.id, o.name, o.city, o.address
          FROM wiki_offices o
         WHERE o.status = 'active' AND o.kind = 'park' AND NOT COALESCE(o.no_office, FALSE)
           AND o.space_id = ANY(%(spaces)s)
         ORDER BY o.city, o.position, o.name
        """,
        {'spaces': space_ids},
    )
    return _dicts(cursor)


def parks(cursor, space_ids):
    """Таксопарки — справочник вики, как в мастере обращений, но с id."""
    space_ids = [int(x) for x in (space_ids or [])]
    if not space_ids:
        return []
    cursor.execute(
        """
        SELECT p.id, p.name FROM wiki_taxi_parks p
         WHERE p.status = 'active' AND p.space_id = ANY(%(spaces)s)
         ORDER BY p.position, p.name
        """,
        {'spaces': space_ids},
    )
    return _dicts(cursor)


def office_by_id(cursor, office_id, space_ids):
    for item in offices(cursor, space_ids):
        if int(item['id']) == int(office_id):
            return item
    return None


def park_by_id(cursor, park_id, space_ids):
    for item in parks(cursor, space_ids):
        if int(item['id']) == int(park_id):
            return item
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Настройки: группа жалоб
# ─────────────────────────────────────────────────────────────────────────────

def settings(cursor):
    cursor.execute(
        """
        SELECT chat_id, chat_title, mention_usernames, updated_by_name, updated_at
          FROM complaint_settings WHERE id = 1
        """
    )
    row = _one(cursor) or {}
    from wiki import offices as wiki_offices

    return {
        'chat_id': row.get('chat_id'),
        'chat_title': row.get('chat_title'),
        'mention_usernames': wiki_offices.split_telegram_usernames(row.get('mention_usernames')),
        'updated_by_name': row.get('updated_by_name'),
        'updated_at': _iso(row.get('updated_at')),
    }


def save_settings(cursor, *, chat_id, chat_title, mention_usernames, actor_id, actor_name):
    cursor.execute(
        """
        INSERT INTO complaint_settings (id, chat_id, chat_title, mention_usernames,
                                        updated_by, updated_by_name, updated_at)
        VALUES (1, %s, %s, %s, %s, %s, {now})
        ON CONFLICT (id) DO UPDATE
           SET chat_id = EXCLUDED.chat_id, chat_title = EXCLUDED.chat_title,
               mention_usernames = EXCLUDED.mention_usernames,
               updated_by = EXCLUDED.updated_by, updated_by_name = EXCLUDED.updated_by_name,
               updated_at = {now}
        """.format(now=_NOW),
        (chat_id, chat_title, mention_usernames, actor_id, actor_name),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Жалобы
# ─────────────────────────────────────────────────────────────────────────────

_COLUMNS = """
    c.id, c.target, c.reason_code, c.target_department_id, td.name AS target_department_name,
    td.code AS target_department_code,
    c.unit_kind, c.unit_id, c.unit_name,
    c.employee_id, c.employee_name, c.employee_source, c.employee_set_by_name, c.employee_set_at,
    c.responsible_id, ru.name AS responsible_name,
    c.reported_employee_id, c.reported_employee_name,
    c.driver_name, c.driver_phone, c.driver_ref, c.city, c.description, c.event_at,
    c.event_time_known,
    c.requires_processing, c.status, c.closed_at,
    c.review_state, c.review_by_name, c.review_at,
    c.result_code, c.result_note, c.result_by_name, c.result_via, c.result_at,
    c.work_state, c.feedback_done, c.training_required, c.training_done,
    c.training_planned_at,
    c.work_closed_at, c.work_closed_by_name,
    c.created_by, c.created_by_name, c.creator_department_id, cd.name AS creator_department_name,
    c.tg_chat_id, c.tg_chat_title, c.tg_message_id, c.delivery_status, c.delivery_error,
    c.question_open_at, c.answer_at,
    c.author_unread_at, c.author_unread_kind, c.author_unread_count,
    c.last_activity_at, c.created_at, c.updated_at
"""

_JOINS = """
      FROM complaints c
      LEFT JOIN departments td ON td.id = c.target_department_id
      LEFT JOIN departments cd ON cd.id = c.creator_department_id
      LEFT JOIN users ru ON ru.id = c.responsible_id
"""


def _decorate(row, viewer_id=None):
    """Строка базы → то, что уходит в интерфейс: подписи рядом с кодами."""
    if not row:
        return row
    item = {key: _iso(value) for key, value in row.items()}
    item['target_title'] = catalog.target_title(item['target'])
    item['reason_title'] = catalog.reason_title(item['target'], item['reason_code'])
    item['result_title'] = catalog.result_title(item['result_code']) or None
    if item.get('target_department_code'):
        item['target_department_label'] = catalog.department_label(
            item['target_department_code'], item.get('target_department_name'))
    # Непрочитанное — свойство автора: разбирающий, открывший чужую жалобу, не
    # должен ни видеть чужую «точку», ни гасить её.
    is_author = (viewer_id is not None and item.get('created_by') is not None
                 and int(item['created_by']) == int(viewer_id))
    unread = bool(item.get('author_unread_at')) and is_author
    item['unread'] = unread
    item['unread_kind'] = item.get('author_unread_kind') if unread else None
    item['unread_count'] = (int(item.get('author_unread_count') or 0) or 1) if unread else 0
    item['question_open'] = bool(item.get('question_open_at'))
    return item


def create_complaint(cursor, fields):
    """Заводит жалобу. fields — уже проверенные catalog.clean_complaint поля
    плюс то, что сверил роут (отдел, подразделение, сотрудник, автор)."""
    cursor.execute(
        """
        INSERT INTO complaints (
            target, reason_code, target_department_id, unit_kind, unit_id, unit_name,
            employee_id, employee_name, employee_source, employee_set_by,
            employee_set_by_name, employee_set_at, responsible_id,
            reported_employee_id, reported_employee_name,
            driver_name, driver_phone, driver_ref, city, description, event_at,
            event_time_known, requires_processing, review_state, status, work_state,
            created_by, created_by_name, creator_department_id,
            delivery_status, last_activity_at
        ) VALUES (
            %(target)s, %(reason)s, %(target_department_id)s, %(unit_kind)s, %(unit_id)s,
            %(unit_name)s, %(employee_id)s, %(employee_name)s, %(employee_source)s,
            %(employee_set_by)s, %(employee_set_by_name)s,
            CASE WHEN %(employee_id)s::int IS NULL THEN NULL ELSE {now} END,
            %(responsible_id)s,
            %(employee_id)s, %(employee_name)s,
            %(driver_name)s, %(driver_phone)s, %(driver_ref)s, %(city)s, %(description)s,
            %(event_at)s, %(event_time_known)s, %(requires_processing)s, %(review_state)s,
            %(status)s, %(work_state)s,
            %(created_by)s, %(created_by_name)s, %(creator_department_id)s,
            %(delivery_status)s, {now}
        )
        RETURNING id
        """.format(now=_NOW),
        fields,
    )
    return cursor.fetchone()[0]


# Текущие группы автора — нужны праву супервайзера (access.can_handle) по
# КАЖДОЙ строке, а не только в карточке: без них лента и выгрузка не смогли бы
# отличить, чьи внутренние поля зритель вправе видеть.
_CREATOR_GROUPS = """,
       COALESCE((
           SELECT array_agg(gom.group_id) FROM group_operator_memberships gom
            WHERE gom.operator_id = c.created_by
              AND gom.start_date <= CURRENT_DATE
              AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
       ), '{}') AS creator_group_ids
"""

# «Зафиксирована»: в группу не уходила, на проверку не ставилась, итога нет,
# сотрудника нет — её никто не разбирал. Так до 30.09.2026 сохранялись жалобы
# на Яндекс. Закрыта она с момента создания, но «отработанной» её считать
# нельзя: аналитика «отработано» иначе раздувалась бы на все такие жалобы.
# Жалоба на проверке у супервайзера (review_state) сюда не попадает никогда:
# она в работе, а решённая им — отработана его итогом. Та же формула — у
# интерфейса (complaintRules.statusView) и выгрузки (report.is_recorded).
RECORDED_SQL = ("(NOT c.requires_processing AND c.review_state IS NULL "
                "AND c.employee_id IS NULL AND c.result_code IS NULL)")


def _with_groups(row, viewer_id=None):
    groups = list(row.pop('creator_group_ids', None) or [])
    item = _decorate(row, viewer_id)
    item['creator_group_ids'] = groups
    return item


def get_complaint(cursor, complaint_id, viewer_id=None):
    """Жалоба + текущие группы её автора (для прав супервайзера)."""
    cursor.execute(
        'SELECT ' + _COLUMNS + _CREATOR_GROUPS + _JOINS + ' WHERE c.id = %s',
        (int(complaint_id),),
    )
    row = _one(cursor)
    return _with_groups(row, viewer_id) if row else None


# Поля, которые видит только разбирающий: «внутренние детали обратной связи и
# обучения сотрудника не должны уходить оператору» (ТЗ). Вырезаются в ОДНОМ
# месте (public_item) для всех ответов — карточки, ленты, отправки сообщения,
# повторной отправки и выгрузки.
INTERNAL_FIELDS = ('result_note', 'work_closed_by_name', 'responsible_id', 'responsible_name',
                   'employee_set_by_name', 'employee_source', 'reported_employee_id',
                   'reported_employee_name', 'training_planned_at')


def public_item(ctx, item):
    """Жалоба в том виде, в каком её вправе видеть зритель."""
    if not item or access.can_handle(ctx, item):
        return item
    clean = dict(item)
    for key in INTERNAL_FIELDS:
        clean.pop(key, None)
    return clean


# Фильтры ленты. «К разбору» — открытые жалобы, которые разбирает сам зритель.
SEGMENT_MINE = 'mine'
SEGMENT_WORK = 'work'
SEGMENT_ALL = 'all'


def list_complaints(cursor, ctx, *, segment=SEGMENT_ALL, status=None, target=None,
                    search=None, limit=40, offset=0, unread_first=False):
    """Порция ленты + есть ли ещё. Возвращает (items, has_more).

    Без COUNT(*) — по той же причине, что у обращений: полный проход по
    периметру на каждый фильтр и букву в поиске. has_more — лишняя строка.

    Статусы — двух разделов. «Жалобы»: open, closed (отработанные), recorded
    (зафиксированные). «Обращения», где автор видит свои жалобы в общей ленте:
    answered (в работе и есть ответ для водителя) и done (закрытые — любые,
    и отработанные, и зафиксированные: автору разница не нужна).

    unread_first — порядок «непрочитанное наверху» ленты «Обращений»
    (crm/queries.list_tickets): две части одной ленты обязаны идти одним
    порядком, иначе при слиянии строки перемешаются. Имеет смысл только для
    своих жалоб — «непрочитано» есть у автора и ни у кого больше.
    """
    where, params = visibility_sql(ctx)
    clauses = [where]
    if segment == SEGMENT_MINE:
        clauses.append('c.created_by = %(viewer_id)s')
    elif segment == SEGMENT_WORK:
        handle, handle_params = handling_sql(ctx)
        params.update(handle_params)
        clauses.append(handle)
        clauses.append("c.status = 'open'")
    if status == 'open':
        clauses.append("c.status = 'open'")
    elif status == 'closed':
        # «Отработанные» — разобранные, а не просто закрытые: зафиксированные
        # (Яндекс) видны в «Все», но отработанными не притворяются.
        clauses.append("c.status = 'closed' AND NOT %s" % RECORDED_SQL)
    elif status == 'recorded':
        clauses.append(RECORDED_SQL)
    elif status == 'answered':
        clauses.append("c.status = 'open' AND c.answer_at IS NOT NULL")
    elif status == 'done':
        clauses.append("c.status = 'closed'")
    if target and catalog.target(target):
        params['target'] = target
        clauses.append('c.target = %(target)s')
    if search:
        needle = str(search).strip()
        digits = needle.replace(' ', '').replace('+', '').replace('-', '')
        if digits.isdigit():
            params['search'] = '%%%s%%' % digits
            params['search_id'] = int(digits) if len(digits) < 10 else None
            clauses.append("""(
                (%(search_id)s::int IS NOT NULL AND c.id = %(search_id)s::int)
                OR c.driver_phone ILIKE %(search)s
                OR c.driver_ref ILIKE %(search)s
            )""")
        else:
            params['search'] = '%%%s%%' % needle.replace('%', r'\%').replace('_', r'\_')
            clauses.append("""(
                c.driver_name ILIKE %(search)s
                OR c.employee_name ILIKE %(search)s
                OR c.unit_name ILIKE %(search)s
            )""")

    page = max(1, min(int(limit), 200))
    params['limit'] = page + 1
    params['offset'] = max(0, int(offset))
    # Выражение — дословно как в idx_complaints_author_attention, иначе индекс
    # запросу не подойдёт.
    order = ('(c.author_unread_at IS NULL), c.last_activity_at DESC, c.id DESC'
             if unread_first and segment == SEGMENT_MINE
             else 'c.last_activity_at DESC, c.id DESC')
    # «Ждёт МОЕЙ проверки» — тем же правилом, что счётчик «К разбору» и
    # колокол (reviewer_sql): бейдж строки горит ровно у того, чья это задача,
    # а не у каждого, кому жалоба видна (глава, админ). CASE — чтобы
    # подзапросы правила считались только у жалоб на проверке.
    review_mine = (",\n       CASE WHEN c.review_state = 'pending'"
                   " AND c.created_by IS DISTINCT FROM %(viewer_id)s THEN "
                   + reviewer_sql('viewer_id') + ' ELSE FALSE END AS review_mine')
    cursor.execute(
        'SELECT ' + _COLUMNS + _CREATOR_GROUPS + review_mine + _JOINS
        + ' WHERE ' + ' AND '.join(clauses)
        + ' ORDER BY ' + order
        + ' LIMIT %(limit)s OFFSET %(offset)s',
        params,
    )
    rows = _dicts(cursor)
    has_more = len(rows) > page
    # Внутренние поля вырезаются по праву зрителя НА КАЖДОЙ строке — лента
    # отдаёт то же, что и карточка, не больше.
    return [public_item(ctx, _with_groups(row, ctx['user_id'])) for row in rows[:page]], has_more


def reviewer_sql(param='viewer_id'):
    """Условие «зритель проверяет эту жалобу до группы» (catalog.REVIEW_*).

    Проверяет любой супервайзер отдела автора; других СВ в отделе нет — глава
    отдела, иначе жалобу не увидел бы никто (владелец, 09.10.2026; до этого —
    только супервайзер группы оператора). Правило одно с «Сотрудничеством с
    Яндексом» в «Обращениях» — crm/queries.department_reviewer_sql. Оно
    адресное: колокол и счётчик будят тех, чья это задача, а не всех, кому
    жалоба видна (глава при живых СВ, админ). Одна строка на счётчик раздела и
    колокол, чтобы бейдж и список не разошлись; триггер колокола (database.py)
    будит этот же круг.
    """
    return crm_queries.department_reviewer_sql(
        param, author='c.created_by', department='c.creator_department_id')


def counters(cursor, ctx):
    """Два числа для раздела: непрочитанное у автора и открытая работа у СВ.

    Работа — это и работа с сотрудником, и жалобы на проверке (Яндекс). Все
    три подсчёта — по частичным индексам (idx_complaints_unread,
    idx_complaints_work_pending, idx_complaints_review_pending): условие
    «мой автор» / «мой ответственный» / «на проверке» отсекает всё остальное.
    """
    cursor.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM complaints
              WHERE created_by = %(viewer_id)s AND author_unread_at IS NOT NULL
                AND employee_id IS DISTINCT FROM %(viewer_id)s),
            (SELECT COUNT(*) FROM complaints c
              WHERE c.work_state = 'pending'
                AND c.employee_id IS DISTINCT FROM %(viewer_id)s
                AND (c.responsible_id = %(viewer_id)s
                     OR (c.responsible_id IS NULL AND c.target_department_id IN (
                         SELECT d.id FROM departments d
                          WHERE d.head_user_id = %(viewer_id)s AND d.is_active))))
            + (SELECT COUNT(*) FROM complaints c
                WHERE c.review_state = 'pending'
                  AND c.created_by IS DISTINCT FROM %(viewer_id)s
                  AND """ + reviewer_sql('viewer_id') + """)
        """,
        {'viewer_id': int(ctx['user_id'])},
    )
    unread, work = cursor.fetchone()
    return {'unread': int(unread or 0), 'work': int(work or 0)}


def update_complaint(cursor, complaint_id, changes):
    """Точечное обновление полей жалобы. Ключи — имена столбцов."""
    if not changes:
        return
    fields = sorted(changes)
    assignments = ', '.join('%s = %%(%s)s' % (name, name) for name in fields)
    params = dict(changes)
    params['id'] = int(complaint_id)
    cursor.execute(
        'UPDATE complaints SET %s, updated_at = %s WHERE id = %%(id)s' % (assignments, _NOW),
        params,
    )


def touch_activity(cursor, complaint_id):
    cursor.execute(
        'UPDATE complaints SET last_activity_at = {now}, updated_at = {now} WHERE id = %s'
        .format(now=_NOW),
        (int(complaint_id),),
    )


def mark_answer(cursor, complaint_id):
    """Пришёл ответ для водителя. Открытый вопрос он не закрывает: вопрос
    закрывает только ответ оператора — группа могла и спросить, и ответить."""
    cursor.execute(
        'UPDATE complaints SET answer_at = {now}, updated_at = {now} WHERE id = %s'
        .format(now=_NOW),
        (int(complaint_id),),
    )


def set_question_open(cursor, complaint_id, is_open):
    cursor.execute(
        """
        UPDATE complaints
           SET question_open_at = CASE WHEN %s THEN {now} ELSE NULL END,
               last_activity_at = {now}, updated_at = {now}
         WHERE id = %s
        """.format(now=_NOW),
        (bool(is_open), int(complaint_id)),
    )


def set_result(cursor, complaint_id, *, code, note, actor_id, actor_name, via):
    cursor.execute(
        """
        UPDATE complaints
           SET result_code = %s, result_note = %s, result_by = %s, result_by_name = %s,
               result_via = %s, result_at = {now},
               last_activity_at = {now}, updated_at = {now}
         WHERE id = %s
        """.format(now=_NOW),
        (code, note, actor_id, actor_name, via, int(complaint_id)),
    )


def set_employee(cursor, complaint_id, *, employee_id, employee_name, source, actor_id,
                 actor_name, responsible_id, target_department_id, unit_kind, unit_id,
                 unit_name, work_state):
    """Новый (или снятый) сотрудник жалобы. Факты прежней работы обнуляются:
    обратная связь была проведена с другим человеком, и засчитывать её новому —
    значит показать работу, которой не было. Сам журнал остаётся — у каждой
    записи свой сотрудник."""
    cursor.execute(
        """
        UPDATE complaints
           SET employee_id = %(employee_id)s, employee_name = %(employee_name)s,
               employee_source = %(source)s, employee_set_by = %(actor_id)s,
               employee_set_by_name = %(actor_name)s,
               employee_set_at = CASE WHEN %(employee_id)s::int IS NULL THEN NULL ELSE {now} END,
               responsible_id = %(responsible_id)s,
               target_department_id = %(target_department_id)s,
               unit_kind = %(unit_kind)s, unit_id = %(unit_id)s, unit_name = %(unit_name)s,
               work_state = %(work_state)s,
               feedback_done = FALSE, training_required = FALSE, training_done = FALSE,
               training_planned_at = NULL,
               work_closed_at = NULL, work_closed_by = NULL, work_closed_by_name = NULL,
               author_unread_at = CASE WHEN %(employee_id)s::int = created_by
                                       THEN NULL ELSE author_unread_at END,
               author_unread_count = CASE WHEN %(employee_id)s::int = created_by
                                          THEN 0 ELSE author_unread_count END,
               last_activity_at = {now}, updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': int(complaint_id), 'employee_id': employee_id, 'employee_name': employee_name,
         'source': source, 'actor_id': actor_id, 'actor_name': actor_name,
         'responsible_id': responsible_id, 'target_department_id': target_department_id,
         'unit_kind': unit_kind, 'unit_id': unit_id, 'unit_name': unit_name,
         'work_state': work_state},
    )


def set_work_flags(cursor, complaint_id, *, flags, actor_id, actor_name):
    closed = bool(flags.get('closed'))
    cursor.execute(
        """
        UPDATE complaints
           SET feedback_done = %(feedback_done)s, training_required = %(training_required)s,
               training_done = %(training_done)s,
               work_state = CASE WHEN %(closed)s THEN 'done' ELSE 'pending' END,
               work_closed_at = CASE WHEN %(closed)s THEN {now} ELSE NULL END,
               work_closed_by = CASE WHEN %(closed)s THEN %(actor_id)s ELSE NULL END,
               work_closed_by_name = CASE WHEN %(closed)s THEN %(actor_name)s ELSE NULL END,
               last_activity_at = {now}, updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': int(complaint_id), 'feedback_done': bool(flags.get('feedback_done')),
         'training_required': bool(flags.get('training_required')),
         'training_done': bool(flags.get('training_done')), 'closed': closed,
         'actor_id': actor_id, 'actor_name': actor_name},
    )


def recompute_status(cursor, complaint_id):
    """Статус по правилу catalog.is_closed. Возвращает (было, стало).

    Считается после каждой правки, которая может его изменить: итог, работа,
    сотрудник. Руками статус не ставит никто — иначе жалобу на сотрудника можно
    было бы закрыть в обход обязательной работы с ним.
    """
    cursor.execute(
        'SELECT status, requires_processing, result_code, work_state, review_state '
        'FROM complaints WHERE id = %s FOR UPDATE',
        (int(complaint_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None, None
    before = row[0]
    closed = catalog.is_closed(requires_processing=row[1], result_code=row[2],
                               work_state=row[3], review_state=row[4])
    after = 'closed' if closed else 'open'
    if after != before:
        cursor.execute(
            """
            UPDATE complaints
               SET status = %s,
                   closed_at = CASE WHEN %s = 'closed' THEN {now} ELSE NULL END,
                   updated_at = {now}
             WHERE id = %s
            """.format(now=_NOW),
            (after, after, int(complaint_id)),
        )
    return before, after


def set_review(cursor, complaint_id, *, state, actor_id, actor_name):
    """Решение супервайзера по жалобе на проверке. «В группу» заодно ставит
    requires_processing: дальше жалоба идёт обычным путём и ждёт итога, а
    отправку (и её повтор) сервис делает как у любой другой жалобы.

    Меняется только жалоба, которая ЕЩЁ ждёт проверки: два супервайзера,
    нажавшие разное одновременно, не должны перезаписать решение друг друга.
    Возвращает True, если решение легло.
    """
    to_group = state == catalog.REVIEW_SENT
    cursor.execute(
        """
        UPDATE complaints
           SET review_state = %(state)s, review_by = %(actor_id)s,
               review_by_name = %(actor_name)s, review_at = {now},
               requires_processing = requires_processing OR %(to_group)s,
               delivery_status = CASE WHEN %(to_group)s THEN 'pending' ELSE delivery_status END,
               last_activity_at = {now}, updated_at = {now}
         WHERE id = %(id)s AND review_state = 'pending'
        """.format(now=_NOW),
        {'id': int(complaint_id), 'state': state, 'actor_id': actor_id,
         'actor_name': actor_name, 'to_group': to_group},
    )
    return cursor.rowcount > 0


def set_training_plan(cursor, complaint_id, planned_at):
    """На когда назначен тренинг. Переназначение перезаписывает: действует
    последнее назначение, а прежнее остаётся в журнале работы."""
    cursor.execute(
        'UPDATE complaints SET training_planned_at = %s, updated_at = {now} WHERE id = %s'
        .format(now=_NOW),
        (planned_at, int(complaint_id)),
    )


def set_delivery(cursor, complaint_id, *, status, chat_id=None, chat_title=None,
                 message_id=None, error=None):
    cursor.execute(
        """
        UPDATE complaints
           SET delivery_status = %s,
               tg_chat_id = COALESCE(%s, tg_chat_id),
               tg_chat_title = COALESCE(%s, tg_chat_title),
               tg_message_id = COALESCE(%s, tg_message_id),
               delivery_error = %s,
               updated_at = {now}
         WHERE id = %s
        """.format(now=_NOW),
        (status, chat_id, chat_title, message_id, error, int(complaint_id)),
    )


# Что ждёт автора. Уходит и в колокол, и в карточку.
UNREAD_ANSWER = 'answer'
UNREAD_QUESTION = 'question'
UNREAD_RESULT = 'result'


def notify_author(cursor, complaint_id, kind):
    cursor.execute(
        """
        UPDATE complaints
           SET author_unread_at = {now}, author_unread_kind = %s,
               author_unread_count = author_unread_count + 1,
               last_activity_at = {now}, updated_at = {now}
         WHERE id = %s
           -- Автора определили сотрудником жалобы — он её больше не видит, и
           -- «непрочитанное», которое нельзя погасить, ему ни к чему.
           AND employee_id IS DISTINCT FROM created_by
        """.format(now=_NOW),
        (kind, int(complaint_id)),
    )


def mark_seen_by_author(cursor, complaint_id, user_id):
    cursor.execute(
        """
        UPDATE complaints
           SET author_unread_at = NULL, author_unread_kind = NULL, author_unread_count = 0
         WHERE id = %s AND created_by = %s
           AND (author_unread_at IS NOT NULL OR author_unread_count > 0)
        """,
        (int(complaint_id), int(user_id)),
    )
    return cursor.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────────
# Переписка с группой
# ─────────────────────────────────────────────────────────────────────────────

def add_message(cursor, *, complaint_id, kind, body=None, prompt_kind=None,
                author_user_id=None, author_name=None, tg_chat_id=None, tg_message_id=None,
                tg_from_id=None, tg_from_name=None, tg_username=None,
                reply_to_tg_message_id=None, attachment=None):
    """Строка переписки. None — это повтор апдейта Telegram (уже записан)."""
    attachment = attachment or {}
    cursor.execute(
        """
        INSERT INTO complaint_messages
            (complaint_id, kind, body, prompt_kind, author_user_id, author_name,
             tg_chat_id, tg_message_id, tg_from_id, tg_from_name, tg_username,
             reply_to_tg_message_id, attachment_kind, attachment_file_id,
             attachment_name, attachment_mime, attachment_size)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (int(complaint_id), kind, body, prompt_kind, author_user_id, author_name,
         tg_chat_id, tg_message_id, tg_from_id, tg_from_name, tg_username,
         reply_to_tg_message_id, attachment.get('kind'), attachment.get('file_id'),
         attachment.get('name'), attachment.get('mime'), attachment.get('size')),
    )
    row = cursor.fetchone()
    return row[0] if row else None


# Виды, которые видит автор-оператор. Внутреннее обсуждение, приглашения бота
# и корневое сообщение ему не показываются: первое по ТЗ, остальные — служебные
# строки, которые в карточке повторяли бы то, что уже стоит в ней полями.
OPERATOR_VISIBLE_KINDS = ('answer', 'question', 'operator_reply')
HANDLER_VISIBLE_KINDS = OPERATOR_VISIBLE_KINDS + ('internal', 'notice')

MESSAGES_LIMIT = 300


def list_messages(cursor, complaint_id, *, include_internal=False):
    kinds = HANDLER_VISIBLE_KINDS if include_internal else OPERATOR_VISIBLE_KINDS
    cursor.execute(
        """
        SELECT * FROM (
            SELECT id, kind, body, author_user_id, author_name, tg_from_name, tg_username,
                   tg_from_id, tg_message_id, reply_to_tg_message_id,
                   attachment_kind, attachment_name, attachment_mime, attachment_size,
                   created_at
              FROM complaint_messages
             WHERE complaint_id = %s AND kind = ANY(%s)
             ORDER BY created_at DESC, id DESC
             LIMIT %s
        ) recent ORDER BY created_at, id
        """,
        (int(complaint_id), list(kinds), MESSAGES_LIMIT),
    )
    return [{
        'id': row['id'],
        'kind': row['kind'],
        'body': row['body'],
        'author_user_id': row['author_user_id'],
        'author_name': row['author_name'] or row['tg_from_name']
                       or ('@' + row['tg_username'] if row['tg_username'] else None),
        'telegram_user_id': row['tg_from_id'],
        'tg_message_id': row['tg_message_id'],
        'reply_to_tg_message_id': row['reply_to_tg_message_id'],
        'attachment': ({'kind': row['attachment_kind'], 'name': row['attachment_name'],
                        'mime': row['attachment_mime'], 'size': row['attachment_size']}
                       if row['attachment_kind'] else None),
        'created_at': _iso(row['created_at']),
    } for row in _dicts(cursor)]


def find_by_tg_message(cursor, chat_id, message_id):
    """Сообщение нашей нити, на которое ответили в группе, и его жалоба."""
    cursor.execute(
        """
        SELECT m.id AS message_id, m.kind, m.prompt_kind, m.complaint_id,
               c.status, c.created_by
          FROM complaint_messages m
          JOIN complaints c ON c.id = m.complaint_id
         WHERE m.tg_chat_id = %s AND m.tg_message_id = %s
         LIMIT 1
        """,
        (int(chat_id), int(message_id)),
    )
    return _one(cursor)


def open_question_message(cursor, complaint_id):
    """Последний вопрос группы оператору — на него уходит ответ из iCORE."""
    cursor.execute(
        """
        SELECT id, tg_message_id FROM complaint_messages
         WHERE complaint_id = %s AND kind = 'question' AND tg_message_id IS NOT NULL
         ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (int(complaint_id),),
    )
    return _one(cursor)


def message_attachment(cursor, complaint_id, message_id, kinds):
    cursor.execute(
        """
        SELECT attachment_file_id, attachment_name, attachment_mime, attachment_kind
          FROM complaint_messages
         WHERE id = %s AND complaint_id = %s AND kind = ANY(%s)
        """,
        (int(message_id), int(complaint_id), list(kinds)),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return {'file_id': row[0], 'name': row[1], 'mime': row[2], 'kind': row[3]}


# ─────────────────────────────────────────────────────────────────────────────
# Работа с сотрудником
# ─────────────────────────────────────────────────────────────────────────────

def add_work_record(cursor, *, complaint_id, action, comment, outcome, need_training,
                    training_id, employee_id, actor_id, actor_name, planned_at=None):
    cursor.execute(
        """
        INSERT INTO complaint_work_log (complaint_id, action, comment, outcome, need_training,
                                        training_id, employee_id, created_by, created_by_name,
                                        planned_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (int(complaint_id), action, comment, outcome, bool(need_training), training_id,
         employee_id, actor_id, actor_name, planned_at),
    )
    return cursor.fetchone()[0]


def work_log(cursor, complaint_id):
    cursor.execute(
        """
        SELECT w.id, w.action, w.comment, w.outcome, w.need_training, w.training_id,
               w.planned_at,
               t.training_date, t.start_time, t.end_time, t.reason AS training_reason,
               w.employee_id, eu.name AS employee_name,
               w.created_by_name, w.created_at
          FROM complaint_work_log w
          LEFT JOIN trainings t ON t.id = w.training_id
          LEFT JOIN users eu ON eu.id = w.employee_id
         WHERE w.complaint_id = %s
         ORDER BY w.created_at, w.id
        """,
        (int(complaint_id),),
    )
    items = []
    for row in _dicts(cursor):
        item = {key: _iso(value) for key, value in row.items()}
        for key in ('start_time', 'end_time'):
            value = row.get(key)
            item[key] = value.strftime('%H:%M') if value is not None else None
        item['action_title'] = catalog.work_action_title(row['action'])
        items.append(item)
    return items


def training_at_slot(cursor, operator_id, day, start, end):
    """Занятие сотрудника ровно в этот слот: (id, текст) или None. Слот в
    «Тренингах» уникален (operator_id, дата, начало, конец), значит это то
    самое занятие, которое провели."""
    cursor.execute(
        """
        SELECT id, comment FROM trainings
         WHERE operator_id = %s AND training_date = %s AND start_time = %s AND end_time = %s
         LIMIT 1
        """,
        (int(operator_id), day, start, end),
    )
    row = cursor.fetchone()
    return (row[0], row[1]) if row else None


def append_training_comment(cursor, training_id, line):
    """Дописать в текст занятия ещё одну строку (вторая жалоба, разобранная
    на том же занятии)."""
    cursor.execute(
        "UPDATE trainings SET comment = CONCAT_WS(E'\\n', NULLIF(comment, ''), %s) WHERE id = %s",
        (line, int(training_id)),
    )


def create_training(cursor, *, operator_id, day, start, end, reason, comment, created_by):
    """Запись в журнале «Тренинги» — та же, что создаёт ОС по оценке звонка:
    идёт в часы сотрудника (count_in_hours) и видна в «Моих часах»."""
    cursor.execute(
        """
        INSERT INTO trainings (operator_id, training_date, start_time, end_time, reason,
                               comment, created_by, count_in_hours)
        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        (int(operator_id), day, start, end, reason, comment, created_by),
    )
    return cursor.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# История
# ─────────────────────────────────────────────────────────────────────────────

def add_event(cursor, *, complaint_id, kind, actor_user_id=None, actor_name=None, payload=None):
    cursor.execute(
        """
        INSERT INTO complaint_events (complaint_id, kind, actor_user_id, actor_name, payload)
        VALUES (%s, %s, %s, %s, %s::jsonb)
        """,
        (int(complaint_id), kind, actor_user_id, actor_name,
         json.dumps(payload or {}, ensure_ascii=False)),
    )


def list_events(cursor, complaint_id, limit=100):
    cursor.execute(
        """
        SELECT id, kind, actor_name, payload, created_at FROM complaint_events
         WHERE complaint_id = %s ORDER BY created_at DESC, id DESC LIMIT %s
        """,
        (int(complaint_id), int(limit)),
    )
    return [{key: _iso(value) for key, value in row.items()} for row in _dicts(cursor)]


# ─────────────────────────────────────────────────────────────────────────────
# Колокол
# ─────────────────────────────────────────────────────────────────────────────

def author_bell_items(cursor, user_id, limit):
    """Ответ для водителя или вопрос группы по жалобе, которую завёл зритель.

    Возвращает (всего, строки). Колокол показывает их в «Обращениях»: там
    автор и работает со своими жалобами (решение владельца 29.09.2026).
    """
    cursor.execute(
        """
        SELECT c.id, 'author' AS role, c.author_unread_kind AS kind,
               c.author_unread_at AS at, c.target, c.reason_code, c.driver_name,
               c.employee_name, COUNT(*) OVER () AS total
          FROM complaints c
         WHERE c.created_by = %(user_id)s AND c.author_unread_at IS NOT NULL
           -- Автор, которого СВ определил сотрудником жалобы, её больше не
           -- видит — и в колоколе она ему тоже не показывается.
           AND c.employee_id IS DISTINCT FROM %(user_id)s
         ORDER BY c.author_unread_at DESC, c.id DESC
         LIMIT %(limit)s
        """,
        {'user_id': int(user_id), 'limit': int(limit)},
    )
    rows = _dicts(cursor)
    total = int(rows[0]['total']) if rows else 0
    return total, rows


def work_bell_items(cursor, user_id, limit):
    """Задачи зрителя в разделе «Жалобы» — две ветки одного списка:

      work    «на сотрудника поступила жалоба» (ТЗ): работа с сотрудником
              ждёт ответственного — назначить или провести тренинг, записать
              принятые меры;
      review  жалоба на Яндекс ждёт проверки: в группу или «Решено»
              (владелец, 30.09.2026).

    Возвращает (всего, строки). Условия — дословно как у counters()['work'].
    """
    cursor.execute(
        """
        SELECT c.id, 'work' AS role,
               CASE WHEN c.review_state = 'pending' THEN 'review' ELSE 'work' END AS kind,
               COALESCE(c.employee_set_at, c.created_at) AS at,
               c.target, c.reason_code, c.driver_name, c.employee_name,
               c.training_required, c.training_planned_at,
               COUNT(*) OVER () AS total
          FROM complaints c
         WHERE (c.work_state = 'pending'
                AND c.employee_id IS DISTINCT FROM %(user_id)s
                AND (c.responsible_id = %(user_id)s
                     -- Ответственного не нашлось (нет ни СВ, ни главы) — задача
                     -- глав отдела сотрудника, а не ничья.
                     OR (c.responsible_id IS NULL AND c.target_department_id IN (
                         SELECT d.id FROM departments d
                          WHERE d.head_user_id = %(user_id)s AND d.is_active))))
            OR (c.review_state = 'pending'
                AND c.created_by IS DISTINCT FROM %(user_id)s
                AND """ + reviewer_sql('user_id') + """)
         ORDER BY at DESC, c.id DESC
         LIMIT %(limit)s
        """,
        {'user_id': int(user_id), 'limit': int(limit)},
    )
    rows = _dicts(cursor)
    total = int(rows[0]['total']) if rows else 0
    return total, rows


def training_day_bell_items(cursor, user_id, day, limit):
    """Тренинг, назначенный на этот день, — «в этот день супервайзерам этого
    отдела приходит уведомление о наличии запланированного тренинга, оператору
    тоже» (владелец, 30.09.2026). Две стороны одного назначения:

      team  зритель — супервайзер отдела сотрудника: видит, у кого из его
            отдела сегодня тренинг и когда;
      self  зритель — сам сотрудник: ему сегодня назначен тренинг.

    Сотруднику приходит только время: сама жалоба ему не видна никогда
    (access.can_view), и строка про неё выдала бы то, что от него скрыто.
    Строки нет, как только требование тренинга снято — провели или приняли
    другие меры. Возвращает (всего, строки).
    """
    start = datetime.combine(day, datetime.min.time())
    cursor.execute(
        """
        SELECT * , COUNT(*) OVER () AS total FROM (
            SELECT c.id, 'team' AS side, c.employee_name, c.training_planned_at
              FROM complaints c
             WHERE c.training_required
               AND c.training_planned_at >= %(start)s AND c.training_planned_at < %(end)s
               AND c.employee_id IS NOT NULL AND c.employee_id <> %(user_id)s
               AND EXISTS (SELECT 1 FROM users v
                            WHERE v.id = %(user_id)s
                              AND lower(COALESCE(v.role, '')) IN ('sv', 'supervisor')
                              AND v.department_id = c.target_department_id)
            UNION ALL
            -- Одна строка на ВРЕМЯ, а не на жалобу: две жалобы на одного
            -- сотрудника СВ разбирает одним занятием, и сотруднику нужен один
            -- тренинг в 14:00, а не два одинаковых — и не намёк числом «2»,
            -- что причин две. Номер жалобы (MIN) — только для порядка.
            SELECT MIN(c.id), 'self', NULL, c.training_planned_at
              FROM complaints c
             WHERE c.training_required
               AND c.training_planned_at >= %(start)s AND c.training_planned_at < %(end)s
               AND c.employee_id = %(user_id)s
             GROUP BY c.training_planned_at
        ) today
         ORDER BY training_planned_at, id
         LIMIT %(limit)s
        """,
        {'user_id': int(user_id), 'start': start, 'end': start + timedelta(days=1),
         'limit': int(limit)},
    )
    rows = _dicts(cursor)
    total = int(rows[0]['total']) if rows else 0
    return total, rows


def training_day_changes_sql(param='user_id', now_param='now'):
    """Моменты, когда список «тренинг сегодня» у зрителя меняется САМ, по
    часам: наступает день назначенного тренинга (строка появляется) и
    кончается (строка уходит). Для notifications.sources.next_change_at —
    без этого строка появилась бы только по возврату фокуса во вкладку."""
    mine = """
        (c.employee_id = %({p})s
         OR (c.employee_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM users v
                 WHERE v.id = %({p})s
                   AND lower(COALESCE(v.role, '')) IN ('sv', 'supervisor')
                   AND v.department_id = c.target_department_id)))""".format(p=param)
    return """
        SELECT MIN(moment) FROM (
            SELECT date_trunc('day', c.training_planned_at) AS moment
              FROM complaints c
             WHERE c.training_required AND c.training_planned_at IS NOT NULL AND {mine}
            UNION ALL
            SELECT date_trunc('day', c.training_planned_at) + INTERVAL '1 day'
              FROM complaints c
             WHERE c.training_required AND c.training_planned_at IS NOT NULL AND {mine}
        ) plan WHERE moment > %({now})s""".format(mine=mine, now=now_param)


def upcoming_shifts(cursor, employee_id, now, limit=2):
    """Ближайшие смены сотрудника — «было бы хорошо, если бы на сайте сразу
    отображались ближайшие 2 смены этого опера» (владелец, 30.09.2026): тренинг
    назначают на время, когда человек на работе.

    Идущая сейчас смена — тоже ближайшая. Ночная смена кончается на
    следующий день (end_time <= start_time), поэтому вчерашняя ночная ещё
    может идти — её берём с запасом в день и отсекаем по концу в Python.
    """
    cursor.execute(
        """
        SELECT shift_date, start_time, end_time, shift_type
          FROM work_shifts
         WHERE operator_id = %s AND shift_date >= %s
         ORDER BY shift_date, start_time
         LIMIT %s
        """,
        (int(employee_id), (now - timedelta(days=1)).date(), int(limit) + 4),
    )
    items = []
    for day, start, end, kind in cursor.fetchall():
        begins = datetime.combine(day, start)
        ends = datetime.combine(day, end)
        if ends <= begins:
            ends += timedelta(days=1)
        if ends <= now:
            continue
        items.append({'date': day.isoformat(), 'start': start.strftime('%H:%M'),
                      'end': end.strftime('%H:%M'), 'type': kind,
                      'ongoing': begins <= now})
        if len(items) >= limit:
            break
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Аналитика и выгрузка
# ─────────────────────────────────────────────────────────────────────────────

def _filter_sql(ctx, filters):
    """Периметр зрителя + фильтры аналитики из ТЗ. Возвращает (where, params).

    «Администратор должен иметь возможность выгружать данные и фильтровать их
    минимум по: периоду; городу; направлению; тематике и подтематике;
    подразделению; конкретному сотруднику; статусу обращения; результату
    проверки; подтверждённым / неподтверждённым; проведённой обратной связи;
    проведённым тренингам».
    """
    where, params = visibility_sql(ctx)
    clauses = [where]
    filters = filters or {}
    if filters.get('date_from'):
        params['date_from'] = filters['date_from']
        clauses.append('c.created_at >= %(date_from)s::date')
    if filters.get('date_to'):
        params['date_to'] = filters['date_to']
        clauses.append("c.created_at < (%(date_to)s::date + INTERVAL '1 day')")
    if filters.get('city'):
        params['city'] = filters['city']
        clauses.append('LOWER(c.city) = LOWER(%(city)s)')
    if filters.get('target'):
        params['target'] = filters['target']
        clauses.append('c.target = %(target)s')
    if filters.get('reason'):
        params['reason'] = filters['reason']
        clauses.append('c.reason_code = %(reason)s')
    if filters.get('unit'):
        params['unit'] = filters['unit']
        clauses.append('c.unit_name = %(unit)s')
    if filters.get('employee_id'):
        params['employee_id'] = int(filters['employee_id'])
        clauses.append('c.employee_id = %(employee_id)s')
    # Статус — три значения, как в ленте и карточке: в работе, отработанные,
    # зафиксированные. «Отработанные» — это разобранные, а не просто закрытые.
    if filters.get('status') == 'open':
        clauses.append("c.status = 'open'")
    elif filters.get('status') == 'closed':
        clauses.append("c.status = 'closed' AND NOT %s" % RECORDED_SQL)
    elif filters.get('status') == 'recorded':
        clauses.append(RECORDED_SQL)
    if filters.get('result'):
        params['result'] = filters['result']
        clauses.append('c.result_code = %(result)s')
    if filters.get('confirmed') == 'yes':
        params['confirmed_codes'] = list(catalog.CONFIRMED_RESULTS)
        clauses.append('c.result_code = ANY(%(confirmed_codes)s)')
    elif filters.get('confirmed') == 'no':
        params['not_confirmed_codes'] = list(catalog.NOT_CONFIRMED_RESULTS)
        clauses.append('c.result_code = ANY(%(not_confirmed_codes)s)')
    for key, column in (('feedback', 'c.feedback_done'), ('training', 'c.training_done')):
        if filters.get(key) == 'yes':
            clauses.append(column)
        elif filters.get(key) == 'no':
            clauses.append('c.employee_id IS NOT NULL AND NOT %s' % column)
    return ' AND '.join(clauses), params


def _bucket(filters):
    """Шаг динамики по длине периода: дни до полутора месяцев, недели до
    полугода, дальше месяцы. Иначе за год на графике было бы 365 столбиков."""
    try:
        start = date.fromisoformat(str(filters.get('date_from')))
        end = date.fromisoformat(str(filters.get('date_to')))
        span = (end - start).days
    except (TypeError, ValueError):
        span = 400
    if span <= 45:
        return 'day'
    if span <= 190:
        return 'week'
    return 'month'


def analytics(cursor, ctx, filters):
    where, params = _filter_sql(ctx, filters)
    params['confirmed_codes'] = list(catalog.CONFIRMED_RESULTS)
    params['not_confirmed_codes'] = list(catalog.NOT_CONFIRMED_RESULTS)

    base = 'FROM complaints c WHERE ' + where
    cursor.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE c.status = 'open') AS open,
               COUNT(*) FILTER (WHERE c.status = 'closed' AND NOT {recorded}) AS closed,
               COUNT(*) FILTER (WHERE {recorded}) AS recorded_only,
               COUNT(*) FILTER (WHERE c.result_code = 'confirmed') AS confirmed,
               COUNT(*) FILTER (WHERE c.result_code = 'partial') AS partial,
               COUNT(*) FILTER (WHERE c.result_code = ANY(%(not_confirmed_codes)s)) AS not_confirmed,
               COUNT(*) FILTER (WHERE c.employee_id IS NOT NULL) AS with_employee,
               COUNT(*) FILTER (WHERE c.work_state = 'unassigned') AS employee_unknown,
               COUNT(*) FILTER (WHERE c.work_state = 'pending') AS work_pending,
               COUNT(*) FILTER (WHERE c.feedback_done) AS feedback_done,
               COUNT(*) FILTER (WHERE c.training_done) AS training_done,
               COUNT(*) FILTER (WHERE c.feedback_done OR c.training_done
                                OR c.training_required) AS needed_work
        """.format(recorded=RECORDED_SQL) + base,
        params,
    )
    totals = {key: int(value or 0) for key, value in (_one(cursor) or {}).items()}

    cursor.execute(
        """
        SELECT c.target, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE c.result_code = ANY(%(confirmed_codes)s)) AS confirmed,
               COUNT(*) FILTER (WHERE c.result_code = ANY(%(not_confirmed_codes)s)) AS not_confirmed
        """ + base + ' GROUP BY c.target ORDER BY total DESC',
        params,
    )
    by_target = [dict(row, title=catalog.target_title(row['target'])) for row in _dicts(cursor)]

    cursor.execute(
        'SELECT c.target, c.reason_code, COUNT(*) AS total ' + base
        # Без лимита: пар «цель + причина» всего 32 (catalog.TARGETS), и лимит
        # ничего не экономил, а срезать мог как раз причины недовольства парком.
        + ' GROUP BY c.target, c.reason_code ORDER BY total DESC, c.target',
        params,
    )
    by_reason = [dict(row, target_title=catalog.target_title(row['target']),
                      title=catalog.reason_title(row['target'], row['reason_code']))
                 for row in _dicts(cursor)]

    cursor.execute(
        'SELECT c.target, c.unit_name, COUNT(*) AS total ' + base
        + ' AND c.unit_name IS NOT NULL GROUP BY c.target, c.unit_name'
          ' ORDER BY total DESC LIMIT 30',
        params,
    )
    by_unit = [dict(row, target_title=catalog.target_title(row['target']))
               for row in _dicts(cursor)]

    cursor.execute(
        'SELECT c.city, COUNT(*) AS total ' + base
        + ' GROUP BY c.city ORDER BY total DESC LIMIT 30',
        params,
    )
    by_city = _dicts(cursor)

    bucket = _bucket(filters or {})
    params['bucket'] = bucket
    cursor.execute(
        """
        SELECT date_trunc(%(bucket)s, c.created_at)::date AS bucket,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE c.result_code = ANY(%(confirmed_codes)s)) AS confirmed
        """ + base + ' GROUP BY 1 ORDER BY 1',
        params,
    )
    dynamics = [{'bucket': _iso(row['bucket']), 'total': int(row['total']),
                 'confirmed': int(row['confirmed'])} for row in _dicts(cursor)]

    # «Количество жалоб на каждого сотрудника; повторные жалобы на одного
    # сотрудника» — список сотрудников с числом жалоб. Повторные — у кого их
    # больше одной; интерфейс их выделяет, отдельного запроса не нужно.
    cursor.execute(
        """
        SELECT c.employee_id, MAX(c.employee_name) AS employee_name,
               MAX(td.name) AS department_name,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE c.result_code = ANY(%(confirmed_codes)s)) AS confirmed,
               COUNT(*) FILTER (WHERE c.feedback_done) AS feedback,
               COUNT(*) FILTER (WHERE c.training_done) AS training,
               COUNT(*) FILTER (WHERE c.work_state = 'pending') AS pending,
               MAX(c.created_at) AS last_at
          FROM complaints c
          LEFT JOIN departments td ON td.id = c.target_department_id
         WHERE """ + where + """ AND c.employee_id IS NOT NULL
         GROUP BY c.employee_id
         ORDER BY total DESC, last_at DESC
         LIMIT 100
        """,
        params,
    )
    by_employee = [{key: _iso(value) for key, value in row.items()} for row in _dicts(cursor)]

    return {
        'totals': totals,
        'by_target': by_target,
        'by_reason': by_reason,
        'by_unit': by_unit,
        'by_city': by_city,
        'dynamics': dynamics,
        'bucket': bucket,
        'by_employee': by_employee,
        'options': filter_options(cursor, ctx, filters),
    }


def filter_options(cursor, ctx, filters):
    """Варианты для фильтров «Город», «Подразделение» и «Сотрудник».

    Считаются по периметру и ПЕРИОДУ, без остальных фильтров: иначе, выбрав
    сотрудника А, в следующий раз в списке нашёлся бы только А (варианты
    строились бы из уже отфильтрованного ответа), а сотрудники за пределами
    первой сотни не попали бы в фильтр вовсе. Один запрос на все три списка.
    """
    where, params = _filter_sql(ctx, {'date_from': (filters or {}).get('date_from'),
                                      'date_to': (filters or {}).get('date_to')})
    base = ' FROM complaints c WHERE ' + where
    cursor.execute(
        "SELECT 'unit' AS kind, c.unit_name AS label, NULL::int AS id" + base
        + ' AND c.unit_name IS NOT NULL GROUP BY c.unit_name'
        + " UNION ALL SELECT 'employee', MAX(c.employee_name), c.employee_id" + base
        + ' AND c.employee_id IS NOT NULL GROUP BY c.employee_id'
        + " UNION ALL SELECT 'city', c.city, NULL::int" + base
        + " AND COALESCE(c.city, '') <> '' GROUP BY c.city",
        params,
    )
    options = {'units': [], 'employees': [], 'cities': []}
    for row in _dicts(cursor):
        if row['kind'] == 'unit':
            options['units'].append(row['label'])
        elif row['kind'] == 'city':
            options['cities'].append(row['label'])
        else:
            options['employees'].append({'id': row['id'], 'name': row['label']})
    options['units'].sort()
    options['cities'].sort()
    options['employees'].sort(key=lambda item: str(item['name'] or ''))
    return options


EXPORT_LIMIT = 20000


def export_rows(cursor, ctx, filters):
    where, params = _filter_sql(ctx, filters)
    params['limit'] = EXPORT_LIMIT
    cursor.execute(
        'SELECT ' + _COLUMNS + _CREATOR_GROUPS + _JOINS + ' WHERE ' + where
        + ' ORDER BY c.created_at DESC, c.id DESC LIMIT %(limit)s',
        params,
    )
    # Выгрузка отдаёт строку в том виде, в каком её видит зритель: СВ приёма
    # видит жалобу на сотрудника ОП, но «принятых мер» по ней не видит и в
    # файле тоже.
    return [public_item(ctx, _with_groups(row)) for row in _dicts(cursor)]


def default_period(today=None):
    """Период аналитики по умолчанию — последние 30 дней включая сегодня."""
    today = today or datetime.now().date()
    return (today - timedelta(days=29)).isoformat(), today.isoformat()
