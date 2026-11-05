# -*- coding: utf-8 -*-
"""SQL «Звонков Binotel» для партнёров. Ни Flask, ни сети.

Свои таблицы — calls_api_calls / calls_api_state (schema.py). Кто на какой линии — из раздела
«Удаленный КЦ» (dial_list_line_members, dial_list_department_settings, dial_list_user_settings)
и справочников users / departments, только чтением.
"""

from . import rules

# Линия → сидевший на ней в момент звонка. Кабинет линии — по настройкам её отдела (как в
# dial_list.service: пусто — remote_cc). Окно assigned_at..released_at: менеджера могли
# пересадить, и вчерашний звонок с линии принадлежит тому, кто сидел на ней вчера.
_SEAT_COLUMNS = """
    m.internal_number, m.user_id, u.name, u.email, u.department_id, lower(COALESCE(d.code, '')),
    d.name, m.department_id, m.assigned_at, m.released_at
"""
_SEAT_FROM = """
      FROM dial_list_line_members m
      JOIN users u ON u.id = m.user_id
      LEFT JOIN departments d ON d.id = u.department_id
      LEFT JOIN dial_list_department_settings s ON s.department_id = m.department_id
     WHERE COALESCE(NULLIF(TRIM(s.binotel_company), ''), 'remote_cc') = %(company)s
"""
_SEAT_FIELDS = ('internal_number', 'user_id', 'user_name', 'user_email', 'department_id',
                'department_code', 'department_name', 'line_department_id', 'assigned_at',
                'released_at')


def _seat(row):
    return dict(zip(_SEAT_FIELDS, row)) if row else None


def seat_at(cursor, company, internal_number, moment):
    """Кто сидел на линии кабинета в этот момент (любой отдел) или None."""
    cursor.execute(
        'SELECT ' + _SEAT_COLUMNS + _SEAT_FROM + """
           AND m.internal_number = %(line)s
           AND m.assigned_at <= %(moment)s
           AND (m.released_at IS NULL OR m.released_at > %(moment)s)
         ORDER BY m.assigned_at DESC, m.id DESC
         LIMIT 1
        """,
        {'company': company, 'line': str(internal_number), 'moment': moment})
    return _seat(cursor.fetchone())


def seats_since(cursor, company, since):
    """Все посадки на линии кабинета, действовавшие после момента since, — сверке с историей:
    один запрос на прогон вместо запроса на звонок."""
    cursor.execute(
        'SELECT ' + _SEAT_COLUMNS + _SEAT_FROM + """
           AND (m.released_at IS NULL OR m.released_at > %(since)s)
         ORDER BY m.internal_number, m.assigned_at
        """,
        {'company': company, 'since': since})
    return [_seat(row) for row in cursor.fetchall()]


def pick_seat(seats, internal_number, moment):
    """seat_at по уже прочитанным посадкам (seats_since)."""
    best = None
    for seat in seats:
        if seat['internal_number'] != internal_number or seat['assigned_at'] > moment:
            continue
        if seat['released_at'] is not None and seat['released_at'] <= moment:
            continue
        if best is None or seat['assigned_at'] >= best['assigned_at']:
            best = seat
    return best


def dial_list_call_ids(cursor, general_call_ids):
    """Какие из звонков — попытки обзвона (вкладка «Обзвон» удалённого КЦ)."""
    ids = [str(value) for value in general_call_ids if value]
    if not ids:
        return set()
    cursor.execute('SELECT general_call_id FROM dial_list_attempts WHERE general_call_id = ANY(%s)', (ids,))
    return {str(row[0]) for row in cursor.fetchall()}


_CALL_COLUMNS = """
    c.general_call_id, c.company_id, c.internal_number, c.line_department_id, c.user_id,
    c.department_id, c.direction, c.phone, c.external_raw, c.disposition, c.result, c.billsec,
    c.waitsec, c.started_at, c.recording_status, c.source, c.received_at, c.updated_at,
    u.name, u.email, d.name
"""
_CALL_FROM = """
      FROM calls_api_calls c
      LEFT JOIN users u ON u.id = c.user_id
      LEFT JOIN departments d ON d.id = c.department_id
"""
_CALL_FIELDS = ('general_call_id', 'company_id', 'internal_number', 'line_department_id', 'user_id',
                'department_id', 'direction', 'phone', 'external_raw', 'disposition', 'result',
                'billsec', 'waitsec', 'started_at', 'recording_status', 'source', 'received_at',
                'updated_at', 'user_name', 'user_email', 'department_name')


def _call(row):
    return dict(zip(_CALL_FIELDS, row)) if row else None


def get_call(cursor, general_call_id):
    cursor.execute('SELECT ' + _CALL_COLUMNS + _CALL_FROM + ' WHERE c.general_call_id = %s',
                   (str(general_call_id),))
    return _call(cursor.fetchone())


def save_call(cursor, call, seat, source):
    """Звонок → строка. ('new' | 'changed' | 'same', строка с оператором).

    Вебхук и сверка с историей могут принести один звонок одновременно: вставка без
    конфликта решает, кто первый, второй сравнивает со строкой под блокировкой."""
    params = dict(call, user_id=seat['user_id'], department_id=seat['department_id'],
                  line_department_id=seat['line_department_id'], source=source)
    cursor.execute(
        """
        INSERT INTO calls_api_calls
               (general_call_id, company_id, internal_number, line_department_id, user_id,
                department_id, direction, phone, external_raw, disposition, result, billsec,
                waitsec, started_at, recording_status, source)
        VALUES (%(general_call_id)s, %(company_id)s, %(internal_number)s, %(line_department_id)s,
                %(user_id)s, %(department_id)s, %(direction)s, %(phone)s, %(external_raw)s,
                %(disposition)s, %(result)s, %(billsec)s, %(waitsec)s, %(started_at)s,
                %(recording_status)s, %(source)s)
        ON CONFLICT (general_call_id) DO NOTHING
        RETURNING general_call_id
        """, params)
    if cursor.fetchone():
        return 'new', get_call(cursor, call['general_call_id'])
    cursor.execute('SELECT result, billsec, waitsec, recording_status FROM calls_api_calls '
                   'WHERE general_call_id = %s FOR UPDATE', (call['general_call_id'],))
    old = dict(zip(('result', 'billsec', 'waitsec', 'recording_status'), cursor.fetchone()))
    if rules.changed(old, call):
        cursor.execute(
            """
            UPDATE calls_api_calls
               SET disposition = %(disposition)s, result = %(result)s,
                   billsec = GREATEST(billsec, %(billsec)s), waitsec = GREATEST(waitsec, %(waitsec)s),
                   recording_status = COALESCE(NULLIF(%(recording_status)s, ''), recording_status),
                   updated_at = now()
             WHERE general_call_id = %(general_call_id)s
            """, params)
        return 'changed', get_call(cursor, call['general_call_id'])
    if call['recording_status'] and call['recording_status'] != old['recording_status']:
        # Состояние записи у Binotel меняется после звонка (загружается) — партнёру не повод.
        cursor.execute('UPDATE calls_api_calls SET recording_status = %s WHERE general_call_id = %s',
                       (call['recording_status'], call['general_call_id']))
    return 'same', None


def list_calls(cursor, *, date_from=None, date_to=None, updated_since=None, phone=None,
               user_id=None, email=None, line=None, result=None, direction=None, limit=100, offset=0):
    """(строки, всего) — звонки от новых к старым."""
    where, params = [], {}
    if date_from is not None:
        where.append('c.started_at >= %(from)s')
        params['from'] = date_from
    if date_to is not None:
        where.append('c.started_at < %(to)s')
        params['to'] = date_to
    if updated_since is not None:
        where.append('c.updated_at > %(updated)s')
        params['updated'] = updated_since
    if phone:
        where.append('c.phone = %(phone)s')
        params['phone'] = phone
    if user_id is not None:
        where.append('c.user_id = %(user)s')
        params['user'] = int(user_id)
    if email:
        where.append('lower(u.email) = %(email)s')
        params['email'] = str(email).strip().lower()
    if line:
        where.append('c.internal_number = %(line)s')
        params['line'] = str(line)
    if result:
        where.append('c.result = %(result)s')
        params['result'] = result
    if direction:
        where.append('c.direction = %(direction)s')
        params['direction'] = direction
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    cursor.execute('SELECT count(*)' + _CALL_FROM + clause, params)
    total = int(cursor.fetchone()[0])
    cursor.execute('SELECT ' + _CALL_COLUMNS + _CALL_FROM + clause
                   + ' ORDER BY c.started_at DESC, c.general_call_id DESC LIMIT %(limit)s OFFSET %(offset)s',
                   dict(params, limit=int(limit), offset=int(offset)))
    return [_call(row) for row in cursor.fetchall()], total


def operators(cursor, company):
    """Кто из отделов области сидит на линиях кабинета сейчас и в каком режиме его телефон:
    обзвон удалённого КЦ (персональное «включено» → отдела линии) или обычный телефон."""
    cursor.execute(
        """
        SELECT m.user_id, u.name, u.email, d.name, m.internal_number, m.assigned_at,
               us.enabled, COALESCE(s.enabled, FALSE)
          FROM dial_list_line_members m
          JOIN users u ON u.id = m.user_id
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN dial_list_department_settings s ON s.department_id = m.department_id
          LEFT JOIN dial_list_user_settings us ON us.user_id = m.user_id
         WHERE m.released_at IS NULL
           AND COALESCE(NULLIF(TRIM(s.binotel_company), ''), 'remote_cc') = %(company)s
           AND lower(COALESCE(d.code, '')) = ANY(%(codes)s)
         ORDER BY u.name, m.internal_number
        """,
        {'company': company, 'codes': list(rules.DEPARTMENT_CODES)})
    items = []
    for user_id, name, email, department, line, seated_at, personal, department_on in cursor.fetchall():
        dial_mode = bool(personal) if personal is not None else bool(department_on)
        items.append({'id': int(user_id), 'name': name or '', 'email': email or None,
                      'department': department or None, 'line': line,
                      'phone_mode': 'remote_cc' if dial_mode else 'phone',
                      'seated_at': rules.iso(seated_at)})
    return items


def get_state(cursor, key):
    cursor.execute('SELECT value FROM calls_api_state WHERE key = %s', (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_state(cursor, key, value):
    cursor.execute(
        """
        INSERT INTO calls_api_state (key, value, updated_at) VALUES (%s, %s, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """, (key, str(value)))


def cleanup(cursor, days):
    cursor.execute("DELETE FROM calls_api_calls WHERE started_at < now() - (%s * interval '1 day')",
                   (int(days),))
    return cursor.rowcount or 0
