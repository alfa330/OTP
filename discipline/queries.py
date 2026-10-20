# -*- coding: utf-8 -*-
"""SQL-слой раздела «Дисциплина».

Функции принимают ГОТОВЫЙ курсор (Database._get_cursor) и не управляют ни
пулом, ни транзакцией: одно действие раздела почти всегда пишет в несколько
таблиц сразу (запись, акт, контроль, уведомления, журнал), и всё это обязано
лечь одной транзакцией — иначе запись без строки журнала или акт без записей.

Даты «сегодня» приходят из Python по Алматы: CURRENT_DATE базы — UTC, и с
полуночи до пяти утра членство в группах считалось бы на вчера.
"""

import json

from . import access, catalog

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


def _rows(cursor):
    names = [column[0] for column in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _row(cursor):
    rows = _rows(cursor)
    return rows[0] if rows else None


def _int_list(values):
    result = []
    for value in values or ():
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(result))


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────

def section_departments(cursor):
    """{код: id} отделов раздела, только действующих."""
    cursor.execute(
        "SELECT code, id FROM departments WHERE code = ANY(%s) AND COALESCE(is_active, TRUE)",
        (list(catalog.SECTION_DEPARTMENT_CODES),))
    return {str(code): int(dept_id) for code, dept_id in cursor.fetchall()}


def load_access_context(cursor, user_id, today):
    """Профиль, возглавляемые отделы и СВ-группы на сегодня — одним запросом."""
    cursor.execute(
        """
        WITH me AS (
            SELECT id, name, role, department_id FROM users WHERE id = %(user_id)s
        ),
        headed AS (
            SELECT d.id, d.code FROM departments d
             WHERE d.head_user_id = %(user_id)s AND COALESCE(d.is_active, TRUE)
        ),
        my_groups AS (
            SELECT gsm.group_id
              FROM group_supervisor_memberships gsm
              JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
             WHERE gsm.supervisor_id = %(user_id)s
               AND gsm.start_date <= %(today)s
               AND (gsm.end_date IS NULL OR gsm.end_date >= %(today)s)
        )
        SELECT (SELECT name FROM me), (SELECT role FROM me), (SELECT department_id FROM me),
               (SELECT d.code FROM departments d WHERE d.id = (SELECT department_id FROM me)),
               COALESCE((SELECT array_agg(id) FROM headed), '{}'),
               COALESCE((SELECT array_agg(code) FROM headed), '{}'),
               COALESCE((SELECT array_agg(DISTINCT group_id) FROM my_groups), '{}')
        """,
        {'user_id': int(user_id), 'today': today})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, headed, headed_codes, groups = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
        'group_ids': list(groups or []),
        'section_departments': section_departments(cursor),
    }


def department_names(cursor, department_ids):
    ids = _int_list(department_ids)
    if not ids:
        return {}
    cursor.execute("SELECT id, name, code FROM departments WHERE id = ANY(%s)", (ids,))
    return {int(row[0]): {'id': int(row[0]), 'name': row[1], 'code': row[2]} for row in cursor.fetchall()}


def department_heads(cursor, department_ids):
    """{отдел: глава} — адресат уведомлений «руководителю»."""
    ids = _int_list(department_ids)
    if not ids:
        return {}
    cursor.execute(
        "SELECT id, head_user_id FROM departments WHERE id = ANY(%s) AND head_user_id IS NOT NULL", (ids,))
    return {int(row[0]): int(row[1]) for row in cursor.fetchall()}


def group_supervisors(cursor, group_ids, today):
    ids = _int_list(group_ids)
    if not ids:
        return []
    cursor.execute(
        """
        SELECT DISTINCT gsm.supervisor_id
          FROM group_supervisor_memberships gsm
          JOIN users u ON u.id = gsm.supervisor_id
         WHERE gsm.group_id = ANY(%s)
           AND gsm.start_date <= %s AND (gsm.end_date IS NULL OR gsm.end_date >= %s)
           AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
        """, (ids, today, today))
    return [int(row[0]) for row in cursor.fetchall()]


def user_names(cursor, user_ids):
    ids = _int_list(user_ids)
    if not ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (ids,))
    return {int(row[0]): row[1] for row in cursor.fetchall()}


def telegram_ids(cursor, user_ids):
    ids = _int_list(user_ids)
    if not ids:
        return {}
    cursor.execute(
        "SELECT id, telegram_id FROM users WHERE id = ANY(%s) AND telegram_id IS NOT NULL", (ids,))
    return {int(row[0]): int(row[1]) for row in cursor.fetchall()}


# ─────────────────────────────────────────────────────────────────────────────
# Сотрудники
# ─────────────────────────────────────────────────────────────────────────────

_OPERATOR_SQL = """
SELECT u.id, u.name, u.login, u.role, u.status, u.department_id, u.hire_date,
       u.avatar_bucket, u.avatar_blob_path,
       d.name AS department_name, d.code AS department_code,
       COALESCE((
           SELECT array_agg(gom.group_id ORDER BY gom.start_date DESC)
             FROM group_operator_memberships gom
            WHERE gom.operator_id = u.id
              AND gom.start_date <= %(today)s
              AND (gom.end_date IS NULL OR gom.end_date >= %(today)s)
       ), '{}') AS group_ids
  FROM users u
  LEFT JOIN departments d ON d.id = u.department_id
"""


def operator_profiles(cursor, operator_ids, today):
    """{id: профиль на сегодня}: отдел, действующие группы, дата приёма."""
    ids = _int_list(operator_ids)
    if not ids:
        return {}
    cursor.execute(_OPERATOR_SQL + " WHERE u.id = ANY(%(ids)s)", {'today': today, 'ids': ids})
    profiles = {}
    for row in _rows(cursor):
        row['group_ids'] = list(row.get('group_ids') or [])
        profiles[int(row['id'])] = row
    groups = group_labels(cursor, [g for p in profiles.values() for g in p['group_ids']])
    for profile in profiles.values():
        first = groups.get(profile['group_ids'][0]) if profile['group_ids'] else None
        profile['group_name'] = first['name'] if first else None
        profile['direction_name'] = first['direction_name'] if first else None
        profile['supervisor_name'] = first['supervisor_name'] if first else None
    return profiles


def group_labels(cursor, group_ids, today=None):
    """{группа: имя, линия (направление), действующий СВ}."""
    ids = _int_list(group_ids)
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT g.id, g.name, dir.name AS direction_name,
               (SELECT u.name FROM group_supervisor_memberships gsm
                  JOIN users u ON u.id = gsm.supervisor_id
                 WHERE gsm.group_id = g.id AND gsm.end_date IS NULL
                 ORDER BY gsm.start_date DESC LIMIT 1) AS supervisor_name
          FROM groups g
          LEFT JOIN directions dir ON dir.id = g.direction_id
         WHERE g.id = ANY(%s)
        """, (ids,))
    return {int(row['id']): row for row in _rows(cursor)}


def search_operators(cursor, ctx, today, query='', limit=30):
    """Кому этот пользователь может зафиксировать нарушение: операторы и стажёры
    отделов раздела в его периметре. Поиск по ФИО и логину (табельного номера
    в портале нет — он не заведён ни в одной карточке)."""
    clauses = ["u.role IN ('operator', 'trainee')",
               "COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')"]
    params = {'today': today, 'limit': int(limit)}
    managed = sorted(access.managed_department_ids(ctx))
    if managed:
        clauses.append('u.department_id = ANY(%(departments)s)')
        params['departments'] = managed
    elif access.is_supervisor(ctx):
        clauses.append(
            """EXISTS (SELECT 1 FROM group_operator_memberships gom
                        WHERE gom.operator_id = u.id AND gom.group_id = ANY(%(groups)s)
                          AND gom.start_date <= %(today)s
                          AND (gom.end_date IS NULL OR gom.end_date >= %(today)s))""")
        params['groups'] = _int_list(ctx.get('group_ids')) or [0]
        clauses.append('u.department_id = ANY(%(departments)s)')
        params['departments'] = sorted(access.section_department_ids(ctx)) or [0]
    else:
        return []
    text = str(query or '').strip()
    if text:
        clauses.append("(u.name ILIKE %(q)s OR COALESCE(u.login, '') ILIKE %(q)s)")
        params['q'] = '%' + text.replace('%', '').replace('_', '') + '%'
    cursor.execute(
        _OPERATOR_SQL + ' WHERE ' + ' AND '.join(clauses) + ' ORDER BY u.name LIMIT %(limit)s', params)
    rows = _rows(cursor)
    groups = group_labels(cursor, [g for row in rows for g in (row.get('group_ids') or [])])
    for row in rows:
        row['group_ids'] = list(row.get('group_ids') or [])
        first = groups.get(row['group_ids'][0]) if row['group_ids'] else None
        row['group_name'] = first['name'] if first else None
        row['direction_name'] = first['direction_name'] if first else None
    return rows


def search_people(cursor, query='', limit=20):
    """Свидетели акта об отказе — «из справочника сотрудников» (п. 8.2): любой
    работающий сотрудник, не только операторы своей группы."""
    text = str(query or '').strip()
    if len(text) < 2:
        return []
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, d.name AS department_name
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
           AND u.name ILIKE %s
         ORDER BY u.name
         LIMIT %s
        """, ('%' + text.replace('%', '').replace('_', '') + '%', int(limit)))
    return _rows(cursor)


def group_at(cursor, operator_id, day):
    """Группа и линия человека на день нарушения — только по покрывающему
    членству. Запасного «ближайшего членства месяца» нет: для снимка в записи
    чужая группа хуже пустой. При правке даты записи снимок берётся заново."""
    cursor.execute(
        """
        SELECT g.id, g.name, dir.name
          FROM group_operator_memberships gom
          JOIN groups g ON g.id = gom.group_id
          LEFT JOIN directions dir ON dir.id = g.direction_id
         WHERE gom.operator_id = %s AND gom.start_date <= %s
           AND (gom.end_date IS NULL OR gom.end_date >= %s)
         ORDER BY gom.start_date DESC LIMIT 1
        """, (int(operator_id), day, day))
    row = cursor.fetchone()
    if not row:
        return None
    return {'group_id': int(row[0]), 'group_name': row[1], 'direction_name': row[2]}


def shifts_around(cursor, operator_id, day):
    """Смены дня нарушения и предыдущего (ночная смена уходит за полночь)."""
    cursor.execute(
        """
        SELECT shift_date, start_time, end_time
          FROM work_shifts
         WHERE operator_id = %s AND shift_date BETWEEN %s::date - 1 AND %s::date
         ORDER BY shift_date, start_time
        """, (int(operator_id), day, day))
    return [{'shift_date': row[0], 'start': row[1], 'end': row[2]} for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Настройки и справочники
# ─────────────────────────────────────────────────────────────────────────────

def get_settings(cursor, department_id):
    cursor.execute("SELECT settings FROM discipline_settings WHERE department_id = %s",
                   (int(department_id),))
    row = cursor.fetchone()
    stored = row[0] if row and isinstance(row[0], dict) else {}
    try:
        return catalog.normalize_settings({}, base=dict(catalog.DEFAULT_SETTINGS, **stored))
    except catalog.SettingsError:
        return catalog.normalize_settings({})


def settings_map(cursor, department_ids):
    return {int(dept): get_settings(cursor, dept) for dept in _int_list(department_ids)}


def save_settings(cursor, department_id, settings, user_id):
    cursor.execute(
        """
        INSERT INTO discipline_settings (department_id, settings, updated_by, updated_at)
        VALUES (%s, %s::jsonb, %s, """ + _NOW + """)
        ON CONFLICT (department_id) DO UPDATE
           SET settings = EXCLUDED.settings, updated_by = EXCLUDED.updated_by,
               updated_at = EXCLUDED.updated_at
        """, (int(department_id), json.dumps(settings, ensure_ascii=False), user_id))


def counting_versions(cursor, department_id):
    """История правил счёта отдела: [(с какого момента, правила)] по возрастанию."""
    cursor.execute(
        'SELECT valid_from, rules FROM discipline_counting_rules WHERE department_id = %s'
        ' ORDER BY valid_from, id', (int(department_id),))
    return [(row[0], row[1] if isinstance(row[1], dict) else {}) for row in cursor.fetchall()]


def add_counting_version(cursor, department_id, rules, valid_from, user_id):
    cursor.execute(
        'INSERT INTO discipline_counting_rules (department_id, rules, valid_from, created_by)'
        ' VALUES (%s, %s::jsonb, %s, %s)',
        (int(department_id), json.dumps(rules, ensure_ascii=False), valid_from, user_id))


def ensure_department_defaults(cursor, department_id):
    """Стартовые справочники ТЗ (п. 5.1, 5.2) — при первом обращении к отделу.
    Только если у отдела ещё нет НИ ОДНОЙ строки (и архивной тоже): руководитель,
    который удалил стартовый тип, не должен получить его обратно."""
    department_id = int(department_id)
    cursor.execute("SELECT EXISTS (SELECT 1 FROM discipline_types WHERE department_id = %s)",
                   (department_id,))
    if not cursor.fetchone()[0]:
        for position, item in enumerate(catalog.DEFAULT_TYPES):
            cursor.execute(
                """
                INSERT INTO discipline_types (department_id, title, severity, is_other, position)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """, (department_id, item['title'], item['severity'], bool(item.get('is_other')), position))
    cursor.execute("SELECT EXISTS (SELECT 1 FROM discipline_measures WHERE department_id = %s)",
                   (department_id,))
    if not cursor.fetchone()[0]:
        for position, item in enumerate(catalog.DEFAULT_MEASURES):
            cursor.execute(
                """
                INSERT INTO discipline_measures (department_id, title, needs_approval, finance,
                                                 value_kind, position)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """, (department_id, item['title'], item['needs_approval'], item['finance'],
                      item['value_kind'], position))


_TYPE_COLUMNS = ('id, department_id, title, severity, needs_explanation, needs_evidence, weight, '
                 'is_other, is_archived, position')
_MEASURE_COLUMNS = 'id, department_id, title, needs_approval, finance, value_kind, is_archived, position'


def list_types(cursor, department_ids, include_archived=False):
    ids = _int_list(department_ids)
    if not ids:
        return []
    cursor.execute(
        'SELECT ' + _TYPE_COLUMNS + ' FROM discipline_types WHERE department_id = ANY(%s)'
        + ('' if include_archived else ' AND NOT is_archived')
        + ' ORDER BY department_id, is_archived, position, id', (ids,))
    return _rows(cursor)


def get_type(cursor, type_id):
    cursor.execute('SELECT ' + _TYPE_COLUMNS + ' FROM discipline_types WHERE id = %s', (int(type_id),))
    return _row(cursor)


def save_type(cursor, department_id, data, user_id, type_id=None):
    fields = (data['title'], data['severity'], data['needs_explanation'], data['needs_evidence'],
              data['weight'], data['is_other'], data['is_archived'])
    if type_id:
        cursor.execute(
            """
            UPDATE discipline_types
               SET title = %s, severity = %s, needs_explanation = %s, needs_evidence = %s,
                   weight = %s, is_other = %s, is_archived = %s, updated_at = """ + _NOW + """
             WHERE id = %s AND department_id = %s
             RETURNING id
            """, fields + (int(type_id), int(department_id)))
    else:
        cursor.execute(
            """
            INSERT INTO discipline_types (department_id, title, severity, needs_explanation,
                                          needs_evidence, weight, is_other, is_archived, position, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    COALESCE((SELECT MAX(position) + 1 FROM discipline_types WHERE department_id = %s), 0), %s)
            RETURNING id
            """, (int(department_id),) + fields + (int(department_id), user_id))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def list_measures(cursor, department_ids, include_archived=False):
    ids = _int_list(department_ids)
    if not ids:
        return []
    cursor.execute(
        'SELECT ' + _MEASURE_COLUMNS + ' FROM discipline_measures WHERE department_id = ANY(%s)'
        + ('' if include_archived else ' AND NOT is_archived')
        + ' ORDER BY department_id, is_archived, position, id', (ids,))
    return _rows(cursor)


def get_measure(cursor, measure_id):
    cursor.execute('SELECT ' + _MEASURE_COLUMNS + ' FROM discipline_measures WHERE id = %s',
                   (int(measure_id),))
    return _row(cursor)


def save_measure(cursor, department_id, data, user_id, measure_id=None):
    fields = (data['title'], data['needs_approval'], data['finance'], data['value_kind'], data['is_archived'])
    if measure_id:
        cursor.execute(
            """
            UPDATE discipline_measures
               SET title = %s, needs_approval = %s, finance = %s, value_kind = %s, is_archived = %s,
                   updated_at = """ + _NOW + """
             WHERE id = %s AND department_id = %s
             RETURNING id
            """, fields + (int(measure_id), int(department_id)))
    else:
        cursor.execute(
            """
            INSERT INTO discipline_measures (department_id, title, needs_approval, finance, value_kind,
                                             is_archived, position, created_by)
            VALUES (%s, %s, %s, %s, %s, %s,
                    COALESCE((SELECT MAX(position) + 1 FROM discipline_measures WHERE department_id = %s), 0), %s)
            RETURNING id
            """, (int(department_id),) + fields + (int(department_id), user_id))
    row = cursor.fetchone()
    return int(row[0]) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Номера
# ─────────────────────────────────────────────────────────────────────────────

def lock_operator(cursor, operator_id):
    """Изменения записей ОДНОГО человека — по очереди, до конца транзакции.

    Номер нарушения и ступень лестницы считаются по всей истории человека. Два
    СВ, фиксирующие нарушения одному оператору одновременно, посчитали бы
    ступень, не видя записи друг друга: обе записи стали бы «третьими», акт
    составился бы дважды, а второй особый контроль упёрся бы в уникальный индекс
    и уронил запрос. Блокировка на человека (не на раздел) другим не мешает.
    """
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('discipline:operator'), %s)", (int(operator_id),))


def next_number(cursor, kind, year):
    """Следующий номер вида (Д — запись, А — акт) за год: без дыр и гонок."""
    cursor.execute(
        """
        INSERT INTO discipline_counters (kind, year, last_no) VALUES (%s, %s, 1)
        ON CONFLICT (kind, year) DO UPDATE SET last_no = discipline_counters.last_no + 1
        RETURNING last_no
        """, (kind, int(year)))
    return catalog.format_number(kind, year, cursor.fetchone()[0])


# ─────────────────────────────────────────────────────────────────────────────
# Записи
# ─────────────────────────────────────────────────────────────────────────────

_RECORD_SELECT = """
SELECT r.*,
       op.name AS operator_name, op.role AS operator_role, op.department_id AS operator_department_id,
       cb.name AS created_by_name, mp.name AS measure_proposed_by_name, ma.name AS measure_approved_by_name,
       cl.name AS closed_by_name, cn.name AS cancelled_by_name, d.name AS department_name,
       (SELECT COUNT(*) FROM discipline_files f
         WHERE f.record_id = r.id AND f.invalid_at IS NULL AND f.superseded_at IS NULL) AS files_count,
       COALESCE((
           SELECT array_agg(gom.group_id)
             FROM group_operator_memberships gom
            WHERE gom.operator_id = r.operator_id
              AND gom.start_date <= %(today)s
              AND (gom.end_date IS NULL OR gom.end_date >= %(today)s)
       ), '{}') AS operator_group_ids
  FROM discipline_records r
  JOIN users op ON op.id = r.operator_id
  LEFT JOIN users cb ON cb.id = r.created_by
  LEFT JOIN users mp ON mp.id = r.measure_proposed_by
  LEFT JOIN users ma ON ma.id = r.measure_approved_by
  LEFT JOIN users cl ON cl.id = r.closed_by
  LEFT JOIN users cn ON cn.id = r.cancelled_by
  LEFT JOIN departments d ON d.id = r.department_id
"""


def _normalize_record(row):
    row['operator_group_ids'] = list(row.get('operator_group_ids') or [])
    row['refusal_witness_ids'] = list(row.get('refusal_witness_ids') or [])
    return row


def get_record(cursor, record_id, today, for_update=False):
    cursor.execute(_RECORD_SELECT + ' WHERE r.id = %(id)s' + (' FOR UPDATE OF r' if for_update else ''),
                   {'id': int(record_id), 'today': today})
    row = _row(cursor)
    return _normalize_record(row) if row else None


def record_by_client_token(cursor, created_by, token):
    cursor.execute(
        'SELECT id, number, operator_id, type_id, occurred_at, cancelled_at FROM discipline_records'
        ' WHERE created_by = %s AND client_token = %s', (int(created_by), token))
    return _row(cursor)


def attach_to_control(cursor, operator_id, control_id, after_moment, after_id, until_day):
    """Открытые записи человека позже записи-основания акта и в сроке контроля —
    под этот контроль (запись задним числом поставила акт раньше них)."""
    cursor.execute(
        """
        UPDATE discipline_records SET control_id = %s, updated_at = """ + _NOW + """
         WHERE operator_id = %s AND control_id IS NULL AND cancelled_at IS NULL AND closed_at IS NULL
           AND (occurred_at, id) > (%s, %s) AND occurred_at::date <= %s
        RETURNING id
        """, (int(control_id), int(operator_id), after_moment, int(after_id), until_day))
    return [int(row[0]) for row in cursor.fetchall()]


def visibility_sql(ctx, alias='r'):
    """То же правило, что access.can_view_record, — для WHERE списков.
    Возвращает (sql, params) c именованными параметрами."""
    scope = access.scope(ctx)
    if scope is None:
        return 'FALSE', {}
    if scope == access.SCOPE_OWN:
        return '%s.operator_id = %%(vis_me)s' % alias, {'vis_me': int(ctx['user_id'])}
    managed = sorted(access.managed_department_ids(ctx))
    if managed:
        return ('(%s.department_id = ANY(%%(vis_departments)s) OR op.department_id = ANY(%%(vis_departments)s))'
                % alias, {'vis_departments': managed})
    groups = _int_list(ctx.get('group_ids')) or [0]
    return (
        """EXISTS (
               SELECT 1 FROM group_operator_memberships gv
                WHERE gv.operator_id = %(a)s.operator_id AND gv.group_id = ANY(%%(vis_groups)s)
                  AND gv.start_date <= %%(today)s
                  AND (gv.end_date IS NULL OR gv.end_date >= %%(today)s))""" % {'a': alias},
        {'vis_groups': groups})


def _record_where(ctx, today, filters):
    """WHERE журнала: видимость пользователя и фильтры. (clauses, params)."""
    filters = filters or {}
    vis_sql, params = visibility_sql(ctx)
    params = dict(params, today=today)
    clauses = [vis_sql]
    if filters.get('date_from'):
        clauses.append('r.occurred_at >= %(date_from)s')
        params['date_from'] = filters['date_from']
    if filters.get('date_to'):
        clauses.append('r.occurred_at < %(date_to)s')
        params['date_to'] = filters['date_to']
    if filters.get('operator_id'):
        clauses.append('r.operator_id = %(operator_id)s')
        params['operator_id'] = int(filters['operator_id'])
    if filters.get('department_id'):
        clauses.append('r.department_id = %(department_id)s')
        params['department_id'] = int(filters['department_id'])
    if filters.get('group_id'):
        clauses.append('r.group_id = %(group_id)s')
        params['group_id'] = int(filters['group_id'])
    if filters.get('type_title'):
        clauses.append('r.type_title = %(type_title)s')
        params['type_title'] = str(filters['type_title'])
    if filters.get('created_by'):
        clauses.append('r.created_by = %(created_by)s')
        params['created_by'] = int(filters['created_by'])
    # Выгрузка на удержания — по дате утверждения меры, а не нарушения: мера,
    # утверждённая в октябре по сентябрьскому нарушению, идёт в октябрьский файл.
    if filters.get('approved_from'):
        clauses.append("r.measure_state = 'approved' AND r.measure_approved_at >= %(approved_from)s")
        params['approved_from'] = filters['approved_from']
    if filters.get('approved_to'):
        clauses.append('r.measure_approved_at < %(approved_to)s')
        params['approved_to'] = filters['approved_to']
    if filters.get('search'):
        clauses.append('(op.name ILIKE %(search)s OR r.number ILIKE %(search)s OR COALESCE(op.login, \'\') '
                       'ILIKE %(search)s)')
        params['search'] = '%' + str(filters['search']).replace('%', '').replace('_', '') + '%'
    if not filters.get('include_cancelled'):
        clauses.append('r.cancelled_at IS NULL')
    return clauses, params


def list_records(cursor, ctx, today, filters=None, limit=500):
    """Записи, видимые пользователю, по фильтрам журнала (без вычислимых полей)."""
    clauses, params = _record_where(ctx, today, filters)
    params['limit'] = int(limit)
    cursor.execute(
        _RECORD_SELECT + ' WHERE ' + ' AND '.join(clauses)
        + ' ORDER BY r.occurred_at DESC, r.id DESC LIMIT %(limit)s', params)
    return [_normalize_record(row) for row in _rows(cursor)]


# Фильтры, которые сами выбираются из списков журнала: варианты считаются без
# них, иначе после выбора одного типа в списке остался бы только он.
_CHOICE_FILTERS = ('type_title', 'group_id', 'created_by', 'search')


def journal_choices(cursor, ctx, today, filters=None):
    """Что предложить в фильтрах журнала: виды нарушений, группы и кто
    фиксировал — среди видимых записей периода."""
    base = {key: value for key, value in (filters or {}).items() if key not in _CHOICE_FILTERS}
    clauses, params = _record_where(ctx, today, base)
    where = ' WHERE ' + ' AND '.join(clauses)
    joins = ' FROM discipline_records r JOIN users op ON op.id = r.operator_id LEFT JOIN users cb ON cb.id = r.created_by'
    cursor.execute('SELECT DISTINCT r.type_title' + joins + where + ' ORDER BY r.type_title', params)
    types = [row[0] for row in cursor.fetchall() if row[0]]
    cursor.execute('SELECT DISTINCT r.group_id, r.group_name' + joins + where
                   + ' AND r.group_id IS NOT NULL ORDER BY r.group_name', params)
    groups = [{'id': int(row[0]), 'name': row[1] or 'Группа'} for row in cursor.fetchall()]
    cursor.execute('SELECT DISTINCT r.created_by, cb.name' + joins + where
                   + ' AND r.created_by IS NOT NULL ORDER BY cb.name', params)
    creators = [{'id': int(row[0]), 'name': row[1] or '—'} for row in cursor.fetchall()]
    return {'types': types, 'groups': groups, 'creators': creators}


def decision_measures(cursor, department_ids, *, date_from=None, date_to=None, operator_id=None):
    """Меры, применённые решением по акту (п. 9) и по окончании особого контроля
    (п. 10.5): вкладка «Меры» и выгрузка на удержания. Период — по дате решения."""
    ids = _int_list(department_ids)
    if not ids:
        return []
    params = {'departments': ids, 'date_from': date_from, 'date_to': date_to,
              'operator_id': int(operator_id) if operator_id else None}
    window = """
           AND (%%(date_from)s::timestamp IS NULL OR %(col)s >= %%(date_from)s::timestamp)
           AND (%%(date_to)s::timestamp IS NULL OR %(col)s < %%(date_to)s::timestamp)
           AND (%%(operator_id)s::int IS NULL OR %(op)s = %%(operator_id)s::int)"""
    cursor.execute(
        """
        SELECT 'act' AS source, a.id, a.number, a.operator_id, op.name AS operator_name,
               r.group_name, r.occurred_at, a.trigger_record_id AS record_id,
               a.decision_title AS measure_title, a.decision_finance AS measure_finance,
               a.decision_amount AS measure_amount, a.decision_percent AS measure_percent,
               a.approved_at AS applied_at, ab.name AS applied_by_name
          FROM discipline_acts a
          JOIN users op ON op.id = a.operator_id
          JOIN discipline_records r ON r.id = a.trigger_record_id AND r.cancelled_at IS NULL
          LEFT JOIN users ab ON ab.id = a.approved_by
         WHERE a.approved_at IS NOT NULL AND a.decision_title IS NOT NULL
           AND a.department_id = ANY(%(departments)s)""" + window % {'col': 'a.approved_at', 'op': 'a.operator_id'}
        + """
        UNION ALL
        SELECT 'control', c.id, NULL, c.operator_id, op.name,
               NULL, NULL, NULL,
               c.decision_measure_title, c.decision_finance,
               c.decision_amount, c.decision_percent,
               c.decided_at, db.name
          FROM discipline_controls c
          JOIN users op ON op.id = c.operator_id
          LEFT JOIN users db ON db.id = c.decided_by
         WHERE c.decision = 'measure' AND c.decision_measure_title IS NOT NULL
           AND c.department_id = ANY(%(departments)s)""" + window % {'col': 'c.decided_at', 'op': 'c.operator_id'}
        + """
         ORDER BY applied_at, id
        """, params)
    return _rows(cursor)


def operator_histories(cursor, operator_ids, since=None):
    """Все записи людей (и отменённые) для расчёта лестницы: минимум полей."""
    ids = _int_list(operator_ids)
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT id, number, operator_id, occurred_at, created_at, weight, department_id, type_title, control_id,
               needs_explanation, explanation_received_at, explanation_requested_at, refused_at, training_id,
               closed_at, cancelled_at IS NOT NULL AS cancelled
          FROM discipline_records
         WHERE operator_id = ANY(%s) AND (%s::timestamp IS NULL OR occurred_at >= %s::timestamp)
         ORDER BY operator_id, occurred_at, id
        """, (ids, since, since))
    result = {operator_id: [] for operator_id in ids}
    for row in _rows(cursor):
        result.setdefault(int(row['operator_id']), []).append(row)
    return result


def insert_record(cursor, data):
    columns = sorted(data)
    cursor.execute(
        'INSERT INTO discipline_records (%s) VALUES (%s) RETURNING id'
        % (', '.join(columns), ', '.join('%%(%s)s' % c for c in columns)), data)
    return int(cursor.fetchone()[0])


_RECORD_EDITABLE = frozenset({
    'occurred_at', 'type_id', 'type_title', 'weight', 'needs_explanation', 'description', 'evidence_note',
    'shift_label', 'group_id', 'group_name', 'direction_name', 'explanation_received_at', 'refused_at',
    'refusal_witness_ids', 'training_id', 'measure_id', 'measure_title', 'measure_finance',
    'measure_value_kind', 'measure_amount', 'measure_percent', 'measure_state', 'measure_proposed_by',
    'measure_approved_by', 'measure_approved_at', 'manager_comment', 'closed_at', 'closed_by',
    'cancelled_at', 'cancelled_by', 'cancel_reason', 'explanation_requested_at',
})


def update_record(cursor, record_id, changes):
    fields = {key: value for key, value in (changes or {}).items() if key in _RECORD_EDITABLE}
    if not fields:
        return
    assignments = ', '.join('%s = %%(%s)s' % (key, key) for key in sorted(fields))
    params = dict(fields, record_id=int(record_id))
    cursor.execute(
        'UPDATE discipline_records SET %s, updated_at = %s WHERE id = %%(record_id)s' % (assignments, _NOW),
        params)


# ─────────────────────────────────────────────────────────────────────────────
# Акты
# ─────────────────────────────────────────────────────────────────────────────

_ACT_SELECT = """
SELECT a.*, cb.name AS composed_by_name, ab.name AS approved_by_name,
       COALESCE((SELECT array_agg(ar.record_id ORDER BY ar.record_id)
                   FROM discipline_act_records ar WHERE ar.act_id = a.id), '{}') AS record_ids,
       EXISTS (SELECT 1 FROM discipline_files f
                WHERE f.act_id = a.id AND f.kind = 'act' AND f.superseded_at IS NULL
                  AND f.invalid_at IS NULL) AS has_file
  FROM discipline_acts a
  LEFT JOIN users cb ON cb.id = a.composed_by
  LEFT JOIN users ab ON ab.id = a.approved_by
"""


def acts_by_record(cursor, record_ids):
    """{запись: акт} — по записи-основанию и по составу акта."""
    ids = _int_list(record_ids)
    if not ids:
        return {}
    cursor.execute(
        _ACT_SELECT + """
         WHERE a.trigger_record_id = ANY(%(ids)s)
            OR a.id IN (SELECT act_id FROM discipline_act_records WHERE record_id = ANY(%(ids)s))
        """, {'ids': ids})
    acts = _rows(cursor)
    result = {}
    for act in acts:
        act['record_ids'] = list(act.get('record_ids') or [])
        for record_id in set(act['record_ids']) | {act['trigger_record_id']}:
            if record_id in ids:
                # Своя запись-основание важнее акта, где запись лишь упомянута.
                current = result.get(record_id)
                if current is None or act['trigger_record_id'] == record_id:
                    result[record_id] = act
    return result


def get_act(cursor, act_id):
    cursor.execute(_ACT_SELECT + ' WHERE a.id = %s', (int(act_id),))
    act = _row(cursor)
    if act:
        act['record_ids'] = list(act.get('record_ids') or [])
    return act


def insert_act(cursor, *, number, operator_id, department_id, trigger_record_id, record_ids, created_by):
    cursor.execute(
        """
        INSERT INTO discipline_acts (number, operator_id, department_id, trigger_record_id, created_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (trigger_record_id) DO NOTHING
        RETURNING id
        """, (number, int(operator_id), department_id, int(trigger_record_id), created_by))
    row = cursor.fetchone()
    if not row:
        return None
    act_id = int(row[0])
    for record_id in _int_list(record_ids):
        cursor.execute(
            'INSERT INTO discipline_act_records (act_id, record_id) VALUES (%s, %s) ON CONFLICT DO NOTHING',
            (act_id, record_id))
    return act_id


def update_act(cursor, act_id, changes):
    allowed = {'composed_on', 'composed_by', 'approved_by', 'approved_at', 'decision_measure_id',
               'decision_title', 'decision_finance', 'decision_value_kind', 'decision_amount', 'decision_percent'}
    fields = {key: value for key, value in (changes or {}).items() if key in allowed}
    if not fields:
        return
    assignments = ', '.join('%s = %%(%s)s' % (key, key) for key in sorted(fields))
    cursor.execute('UPDATE discipline_acts SET %s, updated_at = %s WHERE id = %%(act_id)s' % (assignments, _NOW),
                   dict(fields, act_id=int(act_id)))


# ─────────────────────────────────────────────────────────────────────────────
# Особый контроль
# ─────────────────────────────────────────────────────────────────────────────

_CONTROL_SELECT = """
SELECT c.*, op.name AS operator_name, rs.name AS responsible_name, db.name AS decided_by_name,
       cb.name AS created_by_name, op.department_id AS operator_department_id
  FROM discipline_controls c
  JOIN users op ON op.id = c.operator_id
  LEFT JOIN users rs ON rs.id = c.responsible_id
  LEFT JOIN users db ON db.id = c.decided_by
  LEFT JOIN users cb ON cb.id = c.created_by
"""


def active_controls(cursor, operator_ids):
    ids = _int_list(operator_ids)
    if not ids:
        return {}
    cursor.execute(_CONTROL_SELECT + ' WHERE c.operator_id = ANY(%s) AND c.decided_at IS NULL', (ids,))
    return {int(row['operator_id']): row for row in _rows(cursor)}


def get_control(cursor, control_id, for_update=False):
    cursor.execute(_CONTROL_SELECT + ' WHERE c.id = %s' + (' FOR UPDATE OF c' if for_update else ''),
                   (int(control_id),))
    return _row(cursor)


def list_controls(cursor, department_ids, state='active', operator_id=None, limit=500, *, group_ids=None,
                  today=None, started_from=None, started_to=None):
    """Контроли отделов. group_ids — круг СВ: только люди его групп на today
    (фильтр в запросе, до LIMIT). started_from/started_to — начало в периоде
    (отчёт, без LIMIT). Действующие — по сроку окончания, закрытые — свежие первыми."""
    ids = _int_list(department_ids)
    clauses = ['(c.department_id = ANY(%(departments)s) OR op.department_id = ANY(%(departments)s))']
    params = {'departments': ids or [0]}
    if state == 'active':
        clauses.append('c.decided_at IS NULL')
    elif state == 'closed':
        clauses.append('c.decided_at IS NOT NULL')
    if operator_id:
        clauses.append('c.operator_id = %(operator_id)s')
        params['operator_id'] = int(operator_id)
    if group_ids is not None:
        clauses.append("""EXISTS (
            SELECT 1 FROM group_operator_memberships gm
             WHERE gm.operator_id = c.operator_id AND gm.group_id = ANY(%(groups)s)
               AND gm.start_date <= %(today)s AND (gm.end_date IS NULL OR gm.end_date >= %(today)s))""")
        params.update({'groups': _int_list(group_ids) or [0], 'today': today})
    if started_from is not None:
        clauses.append('c.start_date >= %(started_from)s')
        params['started_from'] = started_from
    if started_to is not None:
        clauses.append('c.start_date < %(started_to)s')
        params['started_to'] = started_to
    sql = (_CONTROL_SELECT + ' WHERE ' + ' AND '.join(clauses)
           + ' ORDER BY c.decided_at IS NOT NULL, CASE WHEN c.decided_at IS NULL THEN c.end_date END,'
             ' c.decided_at DESC, c.id DESC')
    if limit:
        sql += ' LIMIT %(limit)s'
        params['limit'] = int(limit)
    cursor.execute(sql, params)
    return _rows(cursor)


def operator_controls(cursor, operator_id):
    cursor.execute(_CONTROL_SELECT + ' WHERE c.operator_id = %s ORDER BY c.start_date DESC, c.id DESC',
                   (int(operator_id),))
    return _rows(cursor)


def insert_control(cursor, data):
    columns = sorted(data)
    cursor.execute(
        'INSERT INTO discipline_controls (%s) VALUES (%s) RETURNING id'
        % (', '.join(columns), ', '.join('%%(%s)s' % c for c in columns)), data)
    return int(cursor.fetchone()[0])


def update_control(cursor, control_id, changes):
    allowed = {'start_date', 'end_date', 'reason', 'source', 'act_id', 'responsible_id', 'decision', 'decision_comment',
               'decided_by', 'decided_at', 'warned_for', 'due_notified_for', 'decision_measure_id',
               'decision_measure_title', 'decision_finance', 'decision_value_kind', 'decision_amount',
               'decision_percent'}
    fields = {key: value for key, value in (changes or {}).items() if key in allowed}
    if not fields:
        return
    assignments = ', '.join('%s = %%(%s)s' % (key, key) for key in sorted(fields))
    cursor.execute('UPDATE discipline_controls SET %s, updated_at = %s WHERE id = %%(control_id)s'
                   % (assignments, _NOW), dict(fields, control_id=int(control_id)))


def controls_by_ids(cursor, control_ids):
    """{контроль: {'source', 'act_id', 'act_record_id'}} — у записи, заведённой
    под контролем. act_record_id — запись-основание акта контроля."""
    ids = _int_list(control_ids)
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT c.id, c.source, c.act_id, a.trigger_record_id
          FROM discipline_controls c
          LEFT JOIN discipline_acts a ON a.id = c.act_id
         WHERE c.id = ANY(%s)
        """, (ids,))
    return {int(row[0]): {'source': row[1], 'act_id': row[2], 'act_record_id': row[3]}
            for row in cursor.fetchall()}


def violations_in_controls(cursor, control_ids):
    """{контроль: число записей, заведённых под ним (неотменённых)}."""
    ids = _int_list(control_ids)
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT control_id, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE measure_state IS DISTINCT FROM 'approved') AS without_measure
          FROM discipline_records
         WHERE control_id = ANY(%s) AND cancelled_at IS NULL
         GROUP BY control_id
        """, (ids,))
    return {int(row[0]): {'total': int(row[1]), 'without_measure': int(row[2])} for row in cursor.fetchall()}


# ─────────────────────────────────────────────────────────────────────────────
# Файлы
# ─────────────────────────────────────────────────────────────────────────────

_FILE_COLUMNS = ('f.id, f.record_id, f.act_id, f.department_id, f.kind, f.file_name, f.content_type, '
                 'f.size_bytes, f.sha256, f.bucket, f.blob_path, f.uploaded_by, f.uploaded_at, '
                 'f.superseded_at, f.invalid_at, f.invalid_by, f.invalid_reason, '
                 'ub.name AS uploaded_by_name, ib.name AS invalid_by_name')


def insert_file(cursor, data):
    columns = sorted(data)
    cursor.execute(
        'INSERT INTO discipline_files (%s) VALUES (%s) RETURNING id'
        % (', '.join(columns), ', '.join('%%(%s)s' % c for c in columns)), data)
    return int(cursor.fetchone()[0])


def supersede_files(cursor, *, kind, record_id=None, act_id=None, department_id=None):
    """Новая версия того же вида закрывает прежнюю — прежняя остаётся в истории."""
    if record_id:
        where, value = 'record_id = %s', int(record_id)
    elif act_id:
        where, value = 'act_id = %s', int(act_id)
    else:
        where, value = 'department_id = %s AND record_id IS NULL AND act_id IS NULL', int(department_id)
    cursor.execute(
        'UPDATE discipline_files SET superseded_at = ' + _NOW
        + ' WHERE kind = %s AND superseded_at IS NULL AND ' + where, (kind, value))


def files_for(cursor, *, record_ids=(), act_ids=(), department_id=None, kind=None):
    clauses, params = [], []
    if record_ids:
        clauses.append('f.record_id = ANY(%s)')
        params.append(_int_list(record_ids))
    if act_ids:
        clauses.append('f.act_id = ANY(%s)')
        params.append(_int_list(act_ids))
    if department_id is not None:
        clauses.append('(f.department_id = %s AND f.record_id IS NULL AND f.act_id IS NULL)')
        params.append(int(department_id))
    if not clauses:
        return []
    sql = ('SELECT ' + _FILE_COLUMNS + ' FROM discipline_files f LEFT JOIN users ub ON ub.id = f.uploaded_by '
           'LEFT JOIN users ib ON ib.id = f.invalid_by WHERE (' + ' OR '.join(clauses) + ')')
    if kind:
        sql += ' AND f.kind = %s'
        params.append(kind)
    cursor.execute(sql + ' ORDER BY f.uploaded_at DESC, f.id DESC', params)
    return _rows(cursor)


def get_file(cursor, file_id):
    cursor.execute('SELECT ' + _FILE_COLUMNS + ' FROM discipline_files f LEFT JOIN users ub ON ub.id = '
                   'f.uploaded_by LEFT JOIN users ib ON ib.id = f.invalid_by WHERE f.id = %s', (int(file_id),))
    return _row(cursor)


def restore_previous_version(cursor, record_id, kind):
    """Последняя действующая версия признана недействительной — действующей снова
    становится предыдущая (если она сама не признана недействительной)."""
    cursor.execute(
        """
        UPDATE discipline_files SET superseded_at = NULL
         WHERE id = (SELECT id FROM discipline_files
                      WHERE record_id = %s AND kind = %s AND invalid_at IS NULL AND superseded_at IS NOT NULL
                      ORDER BY uploaded_at DESC, id DESC LIMIT 1)
        RETURNING id, file_name
        """, (int(record_id), kind))
    return _row(cursor)


def super_admins(cursor):
    cursor.execute("SELECT id FROM users WHERE role = 'super_admin' AND COALESCE(status, 'working') = 'working'")
    return [int(row[0]) for row in cursor.fetchall()]


def only_super_admins(cursor, user_ids):
    """Из получателей — только супер-админы (раздел пока открыт им одним)."""
    ids = _int_list(user_ids)
    if not ids:
        return []
    cursor.execute("SELECT id FROM users WHERE id = ANY(%s) AND role = 'super_admin'", (ids,))
    allowed = {int(row[0]) for row in cursor.fetchall()}
    return [user_id for user_id in ids if user_id in allowed]


def invalidate_file(cursor, file_id, user_id, reason):
    cursor.execute(
        'UPDATE discipline_files SET invalid_at = ' + _NOW + ', invalid_by = %s, invalid_reason = %s '
        'WHERE id = %s AND invalid_at IS NULL', (user_id, reason, int(file_id)))
    return cursor.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# Тренинги
# ─────────────────────────────────────────────────────────────────────────────

def trainings_on_day(cursor, operator_id, day):
    cursor.execute(
        'SELECT id, start_time, end_time, reason FROM trainings WHERE operator_id = %s AND training_date = %s',
        (int(operator_id), day))
    return [{'id': int(row[0]), 'start': row[1], 'end': row[2], 'reason': row[3]} for row in cursor.fetchall()]


def insert_training(cursor, *, operator_id, day, start, end, reason, comment, created_by):
    cursor.execute(
        """
        INSERT INTO trainings (operator_id, training_date, start_time, end_time, reason, comment,
                               created_by, count_in_hours)
        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
        """, (int(operator_id), day, start, end, reason, comment, created_by))
    return int(cursor.fetchone()[0])


def get_training(cursor, training_id):
    cursor.execute(
        'SELECT id, training_date, start_time, end_time, reason FROM trainings WHERE id = %s', (int(training_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'date': row[1], 'start': row[2], 'end': row[3], 'reason': row[4]}


# ─────────────────────────────────────────────────────────────────────────────
# Колокол
# ─────────────────────────────────────────────────────────────────────────────

def push_notifications(cursor, *, user_ids, kind, title, body=None, record_id=None, control_id=None,
                       tone='default', dedupe_key=None):
    created = []
    for user_id in _int_list(user_ids):
        cursor.execute(
            """
            INSERT INTO discipline_notifications (user_id, record_id, control_id, kind, title, body, tone,
                                                  dedupe_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            RETURNING id
            """, (user_id, record_id, control_id, kind, str(title or '')[:300], body,
                  'warning' if tone == 'warning' else 'default', dedupe_key))
        if cursor.fetchone():
            created.append(user_id)
    return created


def mark_seen(cursor, user_id, *, record_id=None, control_id=None):
    if record_id:
        cursor.execute(
            'UPDATE discipline_notifications SET read_at = ' + _NOW
            + ' WHERE read_at IS NULL AND user_id = %s AND record_id = %s', (int(user_id), int(record_id)))
    elif control_id:
        cursor.execute(
            'UPDATE discipline_notifications SET read_at = ' + _NOW
            + ' WHERE read_at IS NULL AND user_id = %s AND control_id = %s', (int(user_id), int(control_id)))
    return cursor.rowcount


def clear_kind(cursor, *, record_id=None, control_id=None, kinds=()):
    """Дело сделано — «ждём объяснительную» и подобные гаснут у всех."""
    if record_id:
        where, value = 'record_id = %s', int(record_id)
    else:
        where, value = 'control_id = %s', int(control_id)
    cursor.execute(
        'UPDATE discipline_notifications SET read_at = ' + _NOW
        + ' WHERE read_at IS NULL AND kind = ANY(%s) AND ' + where, (list(kinds), value))
    return cursor.rowcount


def bell(cursor, ctx, limit, today):
    """Непрочитанное — только о том, что пользователь сейчас вправе открыть.
    Бывший глава или СВ, от которого человека перевели, строк о нём больше не
    видит: колокол вёл бы в запись, которая ответит отказом."""
    vis_sql, params = visibility_sql(ctx)
    params = dict(params, bell_user=int(ctx['user_id']), today=today, bell_limit=int(limit),
                  bell_departments=sorted(access.managed_department_ids(ctx)) or [0])
    body = """
          FROM discipline_notifications n
          LEFT JOIN discipline_records r ON r.id = n.record_id
          LEFT JOIN users op ON op.id = r.operator_id
          LEFT JOIN discipline_controls c ON c.id = n.control_id
          LEFT JOIN users cop ON cop.id = c.operator_id
         WHERE n.user_id = %(bell_user)s AND n.read_at IS NULL
           AND (n.record_id IS NULL OR (r.id IS NOT NULL AND """ + vis_sql + """))
           AND (n.control_id IS NULL OR c.department_id = ANY(%(bell_departments)s)
                OR cop.department_id = ANY(%(bell_departments)s))"""
    cursor.execute('SELECT COUNT(*)' + body, params)
    total = int(cursor.fetchone()[0] or 0)
    if not total:
        return 0, []
    cursor.execute(
        'SELECT n.id, n.record_id, n.control_id, n.kind, n.title, n.body, n.tone, n.created_at' + body
        + ' ORDER BY n.id DESC LIMIT %(bell_limit)s', params)
    return total, _rows(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Журнал действий
# ─────────────────────────────────────────────────────────────────────────────

def audit(cursor, ctx, action, entity, *, entity_id=None, operator_id=None, department_id=None,
          record_id=None, changes=None):
    cursor.execute(
        """
        INSERT INTO discipline_audit (actor_id, actor_name, action, entity, entity_id, operator_id,
                                      department_id, record_id, changes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """, (ctx.get('user_id'), ctx.get('name'), action, entity, entity_id, operator_id, department_id,
              record_id, json.dumps(changes, ensure_ascii=False, default=str) if changes is not None else None))


def list_audit(cursor, department_ids, *, date_from=None, date_to=None, operator_id=None, record_id=None,
               limit=200, offset=0):
    """Журнал по отделам руководителя. Строка без отдела (настройки другого
    отдела не бывает без отдела) в выдачу не попадает."""
    clauses = ['a.department_id = ANY(%(departments)s)']
    params = {'departments': _int_list(department_ids) or [0], 'limit': int(limit), 'offset': int(offset)}
    if date_from:
        clauses.append('a.created_at >= %(date_from)s')
        params['date_from'] = date_from
    if date_to:
        clauses.append('a.created_at < %(date_to)s')
        params['date_to'] = date_to
    if operator_id:
        clauses.append('a.operator_id = %(operator_id)s')
        params['operator_id'] = int(operator_id)
    if record_id:
        clauses.append('a.record_id = %(record_id)s')
        params['record_id'] = int(record_id)
    where = ' AND '.join(clauses)
    cursor.execute('SELECT COUNT(*) FROM discipline_audit a WHERE ' + where, params)
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute(
        """
        SELECT a.*, op.name AS operator_name
          FROM discipline_audit a
          LEFT JOIN users op ON op.id = a.operator_id
         WHERE """ + where + """
         ORDER BY a.id DESC LIMIT %(limit)s OFFSET %(offset)s
        """, params)
    return total, _rows(cursor)


def record_history(cursor, record_id):
    cursor.execute(
        """
        SELECT id, actor_id, actor_name, action, entity, entity_id, changes, created_at
          FROM discipline_audit
         WHERE record_id = %s AND action NOT IN ('view_record', 'view_file')
         ORDER BY id
        """, (int(record_id),))
    return _rows(cursor)
