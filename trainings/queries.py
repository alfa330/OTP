# -*- coding: utf-8 -*-
"""SQL раздела «Тренинги»: справочник корпоративных тем, охват, аудитория.

Все запросы принимают курсор — транзакцией управляет вызывающий.

Про охват. Знаменатель — активные сотрудники отдела темы; решение владельца:
«все активные сотрудники отдела». Активный = users.status = 'working'
('bs' — без сохранения, человек не работает и провести ему тренинг нельзя;
'fired' — уволен). Из знаменателя исключены админы и супер-админы: раскатка
информационной темы адресована линейным сотрудникам, СВ и тренерам, а не
владельцам портала — иначе 100 % было бы недостижимо by design.

Числитель — COUNT(DISTINCT operator_id) по trainings.topic_id, то есть
«скольким РАЗНЫМ сотрудникам тему провели», а не сколько было сессий.
"""

# Кого считаем аудиторией темы. Держим одним выражением, чтобы знаменатель
# охвата и список «кому ещё не провели» никогда не разъехались: два разных
# условия здесь означали бы «осталось 16», но 18 строк в списке.
AUDIENCE_PREDICATE = """
    u.status = 'working'
    AND LOWER(COALESCE(u.role, '')) NOT IN ('admin', 'super_admin', 'superadmin', 'super-admin')
"""


def list_topics(cursor, department_ids=None, include_archived=False):
    """Справочник корпоративных тем с охватом.

    department_ids: None — без фильтра; множество — только эти отделы плюс
    общие темы (department_id IS NULL), их видят все.
    """
    where = []
    params = []

    if not include_archived:
        where.append("t.is_archived = FALSE")

    if department_ids is not None:
        if not department_ids:
            # Пользователь без отдела: только общие темы.
            where.append("t.department_id IS NULL")
        else:
            where.append("(t.department_id IS NULL OR t.department_id = ANY(%s))")
            params.append(list(int(item) for item in department_ids))

    query = """
        SELECT
            t.id,
            t.title,
            t.kind,
            t.department_id,
            d.name                                AS department_name,
            t.description,
            t.count_in_hours,
            t.is_archived,
            t.created_by,
            cb.name                               AS created_by_name,
            t.created_at,
            COALESCE(s.covered_count, 0)          AS covered_count,
            COALESCE(s.session_count, 0)          AS session_count,
            s.first_date,
            s.last_date,
            COALESCE(a.audience_count, 0)         AS audience_count
        FROM training_topics t
        LEFT JOIN departments d ON d.id = t.department_id
        LEFT JOIN users cb      ON cb.id = t.created_by
        -- Числитель охвата считается по ТОЙ ЖЕ аудитории, что и знаменатель, —
        -- коррелированным подзапросом, а не отдельной группировкой по теме.
        -- Иначе уволенный, которому тему когда-то провели, остаётся в числителе
        -- и выпадает из знаменателя: карточка показывала бы «68 из 68», пока в
        -- списке раскатки честно висят непройденные новички.
        LEFT JOIN LATERAL (
            SELECT COUNT(*)             AS session_count,
                   MIN(tr.training_date) AS first_date,
                   MAX(tr.training_date) AS last_date,
                   COUNT(DISTINCT tr.operator_id) FILTER (
                       WHERE EXISTS (
                           SELECT 1 FROM users u
                            WHERE u.id = tr.operator_id
                              AND (t.department_id IS NULL OR u.department_id = t.department_id)
                              AND %(audience)s
                       )
                   ) AS covered_count
              FROM trainings tr
             WHERE tr.topic_id = t.id
        ) s ON TRUE
        -- Размер аудитории. `t.department_id IS NULL` — это «общая тема», то
        -- есть весь портал, а НЕ «сотрудники без отдела»: равенство с NULL
        -- никогда не выполняется, и на JOIN по department_id общая тема
        -- получала бы охват 0 из 0 навсегда.
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS audience_count
              FROM users u
             WHERE (t.department_id IS NULL OR u.department_id = t.department_id)
               AND %(audience)s
        ) a ON TRUE
    """ % {'audience': AUDIENCE_PREDICATE}

    if where:
        query += " WHERE " + " AND ".join(where)
    # Живые сверху, внутри — свежие первыми.
    query += " ORDER BY t.is_archived, t.id DESC"

    cursor.execute(query, params)
    return [_topic_row(row) for row in cursor.fetchall()]


def get_topic(cursor, topic_id):
    """Одна тема (без охвата) — для проверок прав перед правкой."""
    cursor.execute(
        """
        SELECT id, title, kind, department_id, description, count_in_hours,
               is_archived, created_by
          FROM training_topics
         WHERE id = %s
        """,
        (topic_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'id': row[0],
        'title': row[1],
        'kind': row[2],
        'department_id': row[3],
        'description': row[4],
        'count_in_hours': bool(row[5]),
        'is_archived': bool(row[6]),
        'created_by': row[7],
    }


def topic_audience(cursor, topic_id, department_id):
    """Аудитория темы: кто должен пройти и кто уже прошёл.

    Один запрос на весь список, а не «список сотрудников» + «список пройденных»
    и сшивка на клиенте: раскатка пачками — самый частый экран темы, и второй
    круг запросов на нём стоил бы дороже, чем этот LEFT JOIN.

    Группа берётся членством НА СЕГОДНЯ, а не на дату тренинга: список нужен,
    чтобы выбрать следующую пачку, и «в какой группе человек сейчас» — это
    ровно то, чем эту пачку набирают.
    """
    params = [topic_id]
    # NULL у темы означает «общая, на весь портал», а не «сотрудники без
    # отдела»: сравнение с NULL никогда не выполняется, и такая тема получала
    # бы пустую аудиторию навсегда.
    dept_clause = "TRUE" if department_id is None else "u.department_id = %s"
    if department_id is not None:
        params.append(int(department_id))

    cursor.execute(
        """
        SELECT
            u.id,
            u.name,
            LOWER(COALESCE(u.role, ''))              AS role,
            sv.name                                   AS supervisor_name,
            grp.name                                  AS group_name,
            done.session_count,
            done.last_date
          FROM users u
          LEFT JOIN users sv ON sv.id = u.supervisor_id
          -- LATERAL ... LIMIT 1, а не обычный JOIN: у части людей два членства
          -- накрывают сегодняшний день (в проде такие есть), и обычный JOIN
          -- вернул бы человека дважды — а по длине этого списка считаются и
          -- охват, и «осталось».
          LEFT JOIN LATERAL (
              SELECT g.name
                FROM group_operator_memberships gom
                JOIN groups g ON g.id = gom.group_id
               WHERE gom.operator_id = u.id
                 AND gom.start_date <= CURRENT_DATE
                 AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
               ORDER BY gom.start_date DESC, gom.id DESC
               LIMIT 1
          ) grp ON TRUE
          LEFT JOIN (
              SELECT operator_id, COUNT(*) AS session_count, MAX(training_date) AS last_date
                FROM trainings
               WHERE topic_id = %s
               GROUP BY operator_id
          ) done ON done.operator_id = u.id
         WHERE {dept_clause}
           AND {audience}
         ORDER BY (done.session_count IS NOT NULL), u.name
        """.format(dept_clause=dept_clause, audience=AUDIENCE_PREDICATE),
        params,
    )
    return [
        {
            'id': row[0],
            'name': row[1],
            'role': row[2],
            'supervisor_name': row[3],
            'group_name': row[4],
            'session_count': int(row[5] or 0),
            'last_date': row[6].strftime('%Y-%m-%d') if row[6] else None,
            'covered': bool(row[5]),
        }
        for row in cursor.fetchall()
    ]


def create_topic(cursor, title, kind, department_id, description, count_in_hours, created_by):
    cursor.execute(
        """
        INSERT INTO training_topics (title, kind, department_id, description, count_in_hours, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (title, kind, department_id, description, count_in_hours, created_by),
    )
    return cursor.fetchone()[0]


def update_topic(cursor, topic_id, fields):
    """Частичное обновление. fields — только те ключи, что пришли в запросе."""
    allowed = ('title', 'kind', 'description', 'count_in_hours', 'is_archived', 'department_id')
    updates = []
    params = []
    for key in allowed:
        if key in fields:
            updates.append("%s = %%s" % key)
            params.append(fields[key])
    if not updates:
        return None
    updates.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    params.append(topic_id)
    cursor.execute(
        "UPDATE training_topics SET %s WHERE id = %%s RETURNING id" % ', '.join(updates),
        params,
    )
    row = cursor.fetchone()
    return row[0] if row else None


def topic_title_taken(cursor, title, department_id, exclude_id=None):
    """Есть ли живая тема с таким названием в этом отделе.

    Проверяем до вставки, чтобы отдать понятное 409, а не 500 от уникального
    индекса. Сам индекс при этом остаётся — он ловит гонку двух вкладок.
    """
    params = [title]
    clause = "t.department_id IS NULL" if department_id is None else "t.department_id = %s"
    if department_id is not None:
        params.append(int(department_id))
    if exclude_id is not None:
        clause += " AND t.id <> %s"
        params.append(int(exclude_id))
    cursor.execute(
        """
        SELECT t.id FROM training_topics t
         WHERE LOWER(t.title) = LOWER(%s)
           AND t.is_archived = FALSE
           AND {clause}
         LIMIT 1
        """.format(clause=clause),
        params,
    )
    return cursor.fetchone() is not None


def topic_has_sessions(cursor, topic_id):
    """Проводили ли по теме хоть один тренинг — тему с историей не удаляем,
    а архивируем: удаление обнулило бы topic_id у проведённых записей и
    охват прошлых месяцев перестал бы сходиться."""
    cursor.execute("SELECT 1 FROM trainings WHERE topic_id = %s LIMIT 1", (topic_id,))
    return cursor.fetchone() is not None


def delete_topic(cursor, topic_id):
    cursor.execute("DELETE FROM training_topics WHERE id = %s RETURNING id", (topic_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def department_audience_counts(cursor):
    """Размер аудитории по каждому отделу — справочно для фронта.

    Ключ 'all' — вся аудитория портала: это знаменатель общей темы
    (department_id IS NULL). Отдельной строкой, потому что в GROUP BY по
    department_id общая тема попала бы в корзину «без отдела», а это не она.
    """
    cursor.execute(
        """
        SELECT u.department_id, COUNT(*)
          FROM users u
         WHERE %(audience)s
         GROUP BY u.department_id
        """ % {'audience': AUDIENCE_PREDICATE}
    )
    counts = {row[0]: int(row[1]) for row in cursor.fetchall()}
    counts['all'] = sum(counts.values())
    return counts


def _topic_row(row):
    return {
        'id': row[0],
        'title': row[1],
        'kind': row[2],
        'department_id': row[3],
        'department_name': row[4],
        'description': row[5],
        'count_in_hours': bool(row[6]),
        'is_archived': bool(row[7]),
        'created_by': row[8],
        'created_by_name': row[9] or 'System',
        'created_at': row[10].strftime('%Y-%m-%d %H:%M') if row[10] else None,
        'covered_count': int(row[11] or 0),
        'session_count': int(row[12] or 0),
        'first_date': row[13].strftime('%Y-%m-%d') if row[13] else None,
        'last_date': row[14].strftime('%Y-%m-%d') if row[14] else None,
        'audience_count': int(row[15] or 0),
    }
