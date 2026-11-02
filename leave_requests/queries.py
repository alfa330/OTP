# -*- coding: utf-8 -*-
"""SQL заявок на отпуск и Б/С. Все функции принимают готовый курсор и не
коммитят: транзакцией управляет роут."""

from datetime import date, datetime

from . import rules

# Группа оператора — на «сегодня»: по ней очередь делится на группы СВ и
# сортируются пересечения («сначала своя группа»). Покрывающее членство
# выигрывает, иначе — последнее прошедшее (переведённого вчера покажем в старой).
_GROUP_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT g.id, g.name
          FROM group_operator_memberships gom
          JOIN groups g ON g.id = gom.group_id
         WHERE gom.operator_id = {operator}
           AND gom.start_date <= %(today)s
         ORDER BY (gom.end_date IS NULL OR gom.end_date >= %(today)s) DESC,
                  gom.start_date DESC, gom.id DESC
         LIMIT 1
    ) {alias} ON TRUE
"""

REQUEST_SELECT = """
    SELECT r.id, r.operator_id, op.name, dir.name,
           r.department_id, dep.name,
           r.supervisor_id, sv.name,
           r.kind, r.start_date, r.end_date, r.reason, r.status,
           r.proposed_start_date, r.proposed_end_date,
           r.exclude_from_tenure, r.status_period_id,
           r.decided_by, dcd.name, r.decided_at,
           r.operator_seen_at, r.created_at, r.updated_at,
           grp.id, grp.name
      FROM leave_requests r
      JOIN users op ON op.id = r.operator_id
      LEFT JOIN directions dir ON dir.id = op.direction_id
      LEFT JOIN departments dep ON dep.id = r.department_id
      LEFT JOIN users sv ON sv.id = r.supervisor_id
      LEFT JOIN users dcd ON dcd.id = r.decided_by
""" + _GROUP_LATERAL.format(operator='r.operator_id', alias='grp')

_REQUEST_COLUMNS = (
    'id', 'operator_id', 'operator_name', 'direction_name',
    'department_id', 'department_name',
    'supervisor_id', 'supervisor_name',
    'kind', 'start_date', 'end_date', 'reason', 'status',
    'proposed_start_date', 'proposed_end_date',
    'exclude_from_tenure', 'status_period_id',
    'decided_by', 'decided_by_name', 'decided_at',
    'operator_seen_at', 'created_at', 'updated_at',
    'group_id', 'group_name',
)


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    return None


def _person(person_id, name):
    return {'id': int(person_id) if person_id is not None else None, 'name': name}


def row_to_request(row):
    return dict(zip(_REQUEST_COLUMNS, row))


def serialize_request(raw, events=None):
    """Заявка для клиента. Комментарии шагов берутся из журнала: предложение
    дат и решение — последние события своего вида."""
    events = list(events or [])
    days = rules.period_days(raw['start_date'], raw['end_date'])

    def last(*actions):
        for event in reversed(events):
            if event['action'] in actions:
                return event
        return None

    proposal = None
    if raw['status'] == 'proposal' and raw['proposed_start_date']:
        proposed = last('proposed') or {}
        proposal = {
            'startDate': _iso(raw['proposed_start_date']),
            'endDate': _iso(raw['proposed_end_date']),
            'days': rules.period_days(raw['proposed_start_date'], raw['proposed_end_date']),
            'comment': proposed.get('comment'),
            'by': proposed.get('actor') or _person(None, None),
            'at': proposed.get('at'),
        }
    decision = last('approved', 'rejected') if raw['status'] in ('approved', 'rejected') else None
    return {
        'id': int(raw['id']),
        'kind': raw['kind'],
        'kindLabel': rules.KIND_LABELS.get(raw['kind'], raw['kind']),
        'status': raw['status'],
        'statusLabel': rules.STATUS_LABELS.get(raw['status'], raw['status']),
        'startDate': _iso(raw['start_date']),
        'endDate': _iso(raw['end_date']),
        'days': days,
        'reason': raw['reason'],
        'proposal': proposal,
        'decisionComment': (decision or {}).get('comment'),
        'operator': {
            'id': int(raw['operator_id']),
            'name': raw['operator_name'],
            'direction': raw['direction_name'],
        },
        'department': _person(raw['department_id'], raw['department_name']),
        'group': _person(raw['group_id'], raw['group_name']),
        'supervisor': _person(raw['supervisor_id'], raw['supervisor_name']),
        'excludeFromTenure': bool(raw['exclude_from_tenure']),
        'decidedBy': _person(raw['decided_by'], raw['decided_by_name']),
        'decidedAt': _iso(raw['decided_at']),
        'operatorSeenAt': _iso(raw['operator_seen_at']),
        'createdAt': _iso(raw['created_at']),
        'updatedAt': _iso(raw['updated_at']),
        'events': events,
    }


# ── Люди и отделы ───────────────────────────────────────────────────────────

def load_user(cursor, user_id):
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, u.department_id, COALESCE(u.status, 'working'), u.telegram_id
          FROM users u
         WHERE u.id = %s
        """,
        (int(user_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'id': int(row[0]), 'name': row[1], 'role': str(row[2] or '').strip().lower(),
        'department_id': int(row[3]) if row[3] is not None else None,
        'status': row[4], 'telegram_id': row[5],
    }


def department_head(cursor, department_id):
    if department_id is None:
        return None
    cursor.execute("SELECT head_user_id FROM departments WHERE id = %s", (int(department_id),))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def telegram_chats(cursor, user_ids):
    ids = sorted({int(u) for u in user_ids or () if u})
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT id, telegram_id FROM users
         WHERE id = ANY(%s) AND telegram_id IS NOT NULL
           AND COALESCE(status, 'working') NOT IN ('fired', 'dismissal')
        """,
        (ids,),
    )
    return {int(row[0]): int(row[1]) for row in cursor.fetchall() if row[1]}


# ── Настройки ───────────────────────────────────────────────────────────────

_SETTINGS_COLUMNS = ('enabled', 'allow_annual_leave', 'allow_bs', 'supervisor_step', 'long_bs_days')


def load_settings(cursor, department_id):
    if department_id is None:
        return None
    cursor.execute(
        "SELECT " + ', '.join(_SETTINGS_COLUMNS) + " FROM leave_request_settings WHERE department_id = %s",
        (int(department_id),),
    )
    row = cursor.fetchone()
    return dict(zip(_SETTINGS_COLUMNS, row)) if row else None


def enabled_department_ids(cursor):
    cursor.execute("SELECT department_id FROM leave_request_settings WHERE enabled")
    return {int(row[0]) for row in cursor.fetchall()}


def list_department_settings(cursor):
    """Все отделы — и с настройкой, и без: выключенный отдел тоже надо видеть,
    чтобы включить."""
    cursor.execute(
        """
        SELECT d.id, d.name, d.code,
               COALESCE(s.enabled, FALSE),
               COALESCE(s.allow_annual_leave, TRUE),
               COALESCE(s.allow_bs, TRUE),
               COALESCE(s.supervisor_step, TRUE),
               s.long_bs_days,
               hd.name
          FROM departments d
          LEFT JOIN leave_request_settings s ON s.department_id = d.id
          LEFT JOIN users hd ON hd.id = d.head_user_id
         WHERE COALESCE(d.is_active, TRUE)
         ORDER BY COALESCE(s.enabled, FALSE) DESC, d.name
        """
    )
    return [{
        'id': int(row[0]), 'name': row[1], 'code': row[2],
        'enabled': bool(row[3]), 'allowAnnualLeave': bool(row[4]), 'allowBs': bool(row[5]),
        'supervisorStep': bool(row[6]), 'longBsDays': row[7], 'headName': row[8],
    } for row in cursor.fetchall()]


def save_settings(cursor, department_id, values, updated_by):
    merged = dict(load_settings(cursor, department_id) or {
        'enabled': False, 'allow_annual_leave': True, 'allow_bs': True,
        'supervisor_step': True, 'long_bs_days': None,
    })
    merged.update(values)
    rules.check_settings(merged)
    cursor.execute(
        """
        INSERT INTO leave_request_settings
            (department_id, enabled, allow_annual_leave, allow_bs, supervisor_step, long_bs_days,
             updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (department_id) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            allow_annual_leave = EXCLUDED.allow_annual_leave,
            allow_bs = EXCLUDED.allow_bs,
            supervisor_step = EXCLUDED.supervisor_step,
            long_bs_days = EXCLUDED.long_bs_days,
            updated_by = EXCLUDED.updated_by,
            updated_at = CURRENT_TIMESTAMP
        """,
        (int(department_id), bool(merged['enabled']), bool(merged['allow_annual_leave']),
         bool(merged['allow_bs']), bool(merged['supervisor_step']), merged['long_bs_days'],
         int(updated_by) if updated_by else None),
    )
    return merged


# ── Заявки ──────────────────────────────────────────────────────────────────

def fetch_requests(cursor, where_sql, params, order_sql='r.created_at DESC, r.id DESC', limit=200):
    query_params = dict(params or {})
    query_params.setdefault('today', date.today())
    query_params['limit'] = int(limit)
    cursor.execute(
        REQUEST_SELECT + ' WHERE ' + where_sql + ' ORDER BY ' + order_sql + ' LIMIT %(limit)s',
        query_params,
    )
    return [row_to_request(row) for row in cursor.fetchall()]


def fetch_request(cursor, request_id, today=None):
    found = fetch_requests(cursor, 'r.id = %(id)s', {'id': int(request_id), 'today': today or date.today()},
                           limit=1)
    return found[0] if found else None


def lock_request(cursor, request_id):
    """Строка заявки под FOR UPDATE: два решения по одной заявке не пройдут разом."""
    cursor.execute(
        """
        SELECT id, operator_id, department_id, supervisor_id, kind, start_date, end_date,
               status, proposed_start_date, proposed_end_date, reason
          FROM leave_requests
         WHERE id = %s
         FOR UPDATE
        """,
        (int(request_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    keys = ('id', 'operator_id', 'department_id', 'supervisor_id', 'kind', 'start_date', 'end_date',
            'status', 'proposed_start_date', 'proposed_end_date', 'reason')
    return dict(zip(keys, row))


def insert_request(cursor, *, operator_id, department_id, supervisor_id, kind, start_date, end_date,
                   reason, status):
    cursor.execute(
        """
        INSERT INTO leave_requests
            (operator_id, department_id, supervisor_id, kind, start_date, end_date, reason, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (int(operator_id), department_id, supervisor_id, kind, start_date, end_date, reason, status),
    )
    return int(cursor.fetchone()[0])


_UPDATABLE = ('kind', 'start_date', 'end_date', 'reason', 'status', 'supervisor_id',
              'proposed_start_date', 'proposed_end_date', 'exclude_from_tenure', 'status_period_id',
              'decided_by', 'decided_at', 'operator_seen_at')


def update_request(cursor, request_id, **values):
    unknown = set(values) - set(_UPDATABLE)
    if unknown:
        raise ValueError('Неизвестные поля заявки: %s' % ', '.join(sorted(unknown)))
    assignments = ', '.join('%s = %%(%s)s' % (key, key) for key in values)
    params = dict(values)
    params['id'] = int(request_id)
    cursor.execute(
        'UPDATE leave_requests SET ' + assignments + ', updated_at = CURRENT_TIMESTAMP WHERE id = %(id)s',
        params,
    )


def insert_event(cursor, request_id, *, actor_id, actor_name, action, status_after,
                 start_date=None, end_date=None, comment=None):
    cursor.execute(
        """
        INSERT INTO leave_request_events
            (request_id, actor_id, actor_name, action, status_after, start_date, end_date, comment)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (int(request_id), actor_id, actor_name, action, status_after, start_date, end_date, comment),
    )


def fetch_events(cursor, request_ids):
    ids = sorted({int(i) for i in request_ids or ()})
    result = {i: [] for i in ids}
    if not ids:
        return result
    cursor.execute(
        """
        SELECT e.request_id, e.action, e.status_after, e.actor_id,
               COALESCE(u.name, e.actor_name), e.start_date, e.end_date, e.comment, e.created_at
          FROM leave_request_events e
          LEFT JOIN users u ON u.id = e.actor_id
         WHERE e.request_id = ANY(%s)
         ORDER BY e.request_id, e.id
        """,
        (ids,),
    )
    for row in cursor.fetchall():
        start, end = row[5], row[6]
        result[int(row[0])].append({
            'action': row[1],
            'statusAfter': row[2],
            'actor': _person(row[3], row[4]),
            'startDate': _iso(start),
            'endDate': _iso(end),
            'days': rules.period_days(start, end) if start and end else None,
            'comment': row[7],
            'at': _iso(row[8]),
        })
    return result


def own_active_overlap(cursor, operator_id, start, end, exclude_id=None):
    """Своя действующая заявка на те же дни — вторая была бы повтором."""
    cursor.execute(
        """
        SELECT id, kind, start_date, end_date
          FROM leave_requests
         WHERE operator_id = %(operator_id)s
           AND status = ANY(%(statuses)s)
           AND start_date <= %(end)s AND end_date >= %(start)s
           AND (%(exclude)s::int IS NULL OR id <> %(exclude)s::int)
         ORDER BY start_date
         LIMIT 1
        """,
        {'operator_id': int(operator_id), 'statuses': list(rules.ACTIVE_STATUSES),
         'start': start, 'end': end, 'exclude': exclude_id},
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'kind': row[1], 'start_date': row[2], 'end_date': row[3]}


def schedule_periods(cursor, operator_id, start, end):
    """Статусы графика оператора, пересекающие период: отпуск, Б/С, больничный, увольнение."""
    cursor.execute(
        """
        SELECT status_code, start_date, end_date
          FROM operator_schedule_status_periods
         WHERE operator_id = %s
           AND start_date <= %s
           AND COALESCE(end_date, DATE '9999-12-31') >= %s
         ORDER BY start_date, id
        """,
        (int(operator_id), end, start),
    )
    return [{'status_code': row[0], 'start_date': row[1], 'end_date': row[2]} for row in cursor.fetchall()]


def colleague_overlaps(cursor, *, department_id, operator_id, start, end, today, limit=8):
    """Кто из отдела уже отдыхает в эти даты (ТЗ п. 4).

    Источник — периоды графика «Ежегодный отпуск» и «Б/С», то есть уже
    утверждённое: и заявками, и руками руководителя. Ожидающие заявки сюда не
    попадают (ТЗ: «учитывать только утверждённые»). Сначала — своя группа.
    """
    if department_id is None:
        return 0, []
    params = {'department_id': int(department_id), 'operator_id': int(operator_id),
              'start': start, 'end': end, 'today': today, 'limit': int(limit)}
    cursor.execute(
        """
        WITH own AS (
            SELECT gom.group_id
              FROM group_operator_memberships gom
             WHERE gom.operator_id = %(operator_id)s AND gom.start_date <= %(today)s
             ORDER BY (gom.end_date IS NULL OR gom.end_date >= %(today)s) DESC, gom.start_date DESC
             LIMIT 1
        )
        SELECT p.operator_id, u.name, p.status_code, p.start_date, p.end_date, grp.id, grp.name,
               (grp.id IS NOT NULL AND grp.id IS NOT DISTINCT FROM (SELECT group_id FROM own)) AS same_group,
               COUNT(*) OVER () AS total
          FROM operator_schedule_status_periods p
          JOIN users u ON u.id = p.operator_id
        """ + _GROUP_LATERAL.format(operator='p.operator_id', alias='grp') + """
         WHERE u.department_id = %(department_id)s
           AND p.operator_id <> %(operator_id)s
           AND p.status_code IN ('annual_leave', 'bs')
           AND p.start_date <= %(end)s
           AND COALESCE(p.end_date, DATE '9999-12-31') >= %(start)s
           AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
         ORDER BY same_group DESC, p.start_date, u.name
         LIMIT %(limit)s
        """,
        params,
    )
    rows = cursor.fetchall()
    total = int(rows[0][8]) if rows else 0
    return total, [{
        'operatorId': int(row[0]),
        'name': row[1],
        'kind': row[2],
        'kindLabel': rules.KIND_LABELS.get(row[2], row[2]),
        'startDate': _iso(row[3]),
        'endDate': _iso(row[4]),
        'group': _person(row[5], row[6]),
        'sameGroup': bool(row[7]),
    } for row in rows]
