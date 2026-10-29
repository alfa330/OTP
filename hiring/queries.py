# -*- coding: utf-8 -*-
"""SQL раздела «Воронка найма». Время в таблицах — наивное время Алматы (rules.ALMATY).

Все функции принимают готовый курсор и не коммитят: транзакцией управляет вызывающий
(service.py — одна операция, одна транзакция). Строки отдаются словарями по cursor.description.
"""

import json

from . import rules

now_almaty = rules.now_almaty


def _all(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _one(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))


def _ids(values):
    return sorted({int(x) for x in (values or ()) if x})


# ─── Профиль и права ──────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
    SELECT u.name, u.role, u.status, u.department_id, d.code,
           ARRAY(SELECT h.id FROM departments h
                  WHERE h.head_user_id = u.id AND COALESCE(h.is_active, TRUE)),
           ARRAY(SELECT lower(h.code) FROM departments h
                  WHERE h.head_user_id = u.id AND COALESCE(h.is_active, TRUE) AND h.code IS NOT NULL),
           (SELECT p.kind FROM hiring_people p WHERE p.user_id = u.id AND p.is_active),
           ARRAY(SELECT g.id FROM hiring_groups g WHERE g.trainer_id = u.id AND g.status = 'active'),
           ARRAY(SELECT DISTINCT v.department_id FROM hiring_vacancies v
                  WHERE v.is_active AND v.department_id IS NOT NULL)
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
     WHERE u.id = %(user_id)s
"""


def load_access_context(cursor, user_id):
    """Профиль для прав раздела одним запросом. None — нет учётки."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row:
        return None
    name, role, status, department_id, department_code, headed_ids, headed_codes, grant, groups, vac_depts = row
    return {
        'user_id': int(user_id),
        'name': name or '',
        'role': str(role or '').strip().lower(),
        'status': status,
        'department_id': department_id,
        'department_code': str(department_code or '').strip().lower() or None,
        'headed_ids': [int(x) for x in (headed_ids or ()) if x],
        'headed_codes': [code for code in (headed_codes or ()) if code],
        'grant': grant,
        'trainer_group_ids': [int(x) for x in (groups or ()) if x],
        'vacancy_department_ids': [int(x) for x in (vac_depts or ()) if x],
    }


def user_names(cursor, ids):
    ids = _ids(ids)
    if not ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (ids,))
    return {int(user_id): name or '' for user_id, name in cursor.fetchall()}


def user_name(cursor, user_id):
    return user_names(cursor, [user_id]).get(int(user_id), '') if user_id else ''


def telegram_chats(cursor, ids):
    """{user_id: telegram_id} у тех, кто подключил Telegram."""
    ids = _ids(ids)
    if not ids:
        return {}
    cursor.execute("SELECT id, telegram_id FROM users WHERE id = ANY(%s) AND telegram_id IS NOT NULL", (ids,))
    return {int(user_id): int(chat_id) for user_id, chat_id in cursor.fetchall()}


def search_users(cursor, query='', limit=1000):
    """Работающие сотрудники для выдач, групп и планов: список целиком (сотни строк),
    отбор по фамилии делает сам выпадающий список."""
    pattern = '%' + str(query or '').strip() + '%'
    cursor.execute("""
        SELECT u.id, u.name, u.role, d.name AS department_name
          FROM users u LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.status NOT IN ('fired', 'dismissal') AND u.name ILIKE %s
         ORDER BY u.name LIMIT %s
    """, (pattern, int(limit)))
    return _all(cursor)


def departments(cursor):
    cursor.execute("SELECT id, code, name FROM departments WHERE COALESCE(is_active, TRUE) ORDER BY name")
    return _all(cursor)


def heads_of_departments(cursor, department_ids):
    """{department_id: head_user_id} — кому писать о выходе на линию."""
    ids = _ids(department_ids)
    if not ids:
        return {}
    cursor.execute("SELECT id, head_user_id FROM departments WHERE id = ANY(%s) AND head_user_id IS NOT NULL", (ids,))
    return {int(dep): int(head) for dep, head in cursor.fetchall()}


def lead_user_ids(cursor):
    """Руководители рекрутинга: глава и сотрудники HR, выдачи «lead», супер-админы."""
    cursor.execute("""
        SELECT u.id FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.status NOT IN ('fired', 'dismissal')
           AND (lower(u.role) = 'super_admin' OR lower(COALESCE(d.code, '')) = 'hr'
                OR EXISTS (SELECT 1 FROM departments h WHERE h.head_user_id = u.id AND lower(h.code) = 'hr')
                OR EXISTS (SELECT 1 FROM hiring_people p WHERE p.user_id = u.id AND p.is_active AND p.kind = 'lead'))
    """)
    return [int(row[0]) for row in cursor.fetchall()]


def recruiter_user_ids(cursor):
    cursor.execute("""
        SELECT p.user_id FROM hiring_people p JOIN users u ON u.id = p.user_id
         WHERE p.is_active AND p.kind IN ('staff', 'remote') AND u.status NOT IN ('fired', 'dismissal')
    """)
    return [int(row[0]) for row in cursor.fetchall()]


# ─── Выдачи ───────────────────────────────────────────────────────────────────

def list_people(cursor):
    cursor.execute("""
        SELECT p.user_id, p.kind, p.is_active, p.granted_at, u.name, u.status, d.name AS department_name
          FROM hiring_people p JOIN users u ON u.id = p.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         ORDER BY p.is_active DESC, u.name
    """)
    return _all(cursor)


def upsert_person(cursor, user_id, kind, by, now):
    cursor.execute("""
        INSERT INTO hiring_people (user_id, kind, is_active, granted_by, granted_at, updated_at)
        VALUES (%s, %s, TRUE, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET kind = EXCLUDED.kind, is_active = TRUE,
            granted_by = EXCLUDED.granted_by, updated_at = EXCLUDED.updated_at
    """, (int(user_id), kind, by, now, now))


def deactivate_person(cursor, user_id, now):
    cursor.execute("UPDATE hiring_people SET is_active = FALSE, updated_at = %s WHERE user_id = %s", (now, int(user_id)))
    return cursor.rowcount


# ─── Справочники ──────────────────────────────────────────────────────────────

def list_sources(cursor, active_only=False):
    cursor.execute("SELECT id, code, name, kind, platform, is_active, position FROM hiring_sources"
                   + (" WHERE is_active" if active_only else "") + " ORDER BY kind DESC, position, id")
    return _all(cursor)


def source_by_code(cursor, code):
    cursor.execute("SELECT id, code, name, kind, platform, is_active FROM hiring_sources WHERE code = %s", (code,))
    return _one(cursor)


def get_source(cursor, source_id):
    cursor.execute("SELECT id, code, name, kind, platform, is_active FROM hiring_sources WHERE id = %s", (int(source_id),))
    return _one(cursor)


def create_source(cursor, code, name, kind, now):
    cursor.execute("""
        INSERT INTO hiring_sources (code, name, kind, position, created_at)
        VALUES (%s, %s, %s, COALESCE((SELECT MAX(position) + 1 FROM hiring_sources), 0), %s) RETURNING id
    """, (code, name, kind, now))
    return int(cursor.fetchone()[0])


def update_source(cursor, source_id, **fields):
    _update('hiring_sources', source_id, fields, allowed=('name', 'is_active', 'position'), cursor=cursor)


def list_vacancies(cursor, active_only=False):
    cursor.execute("""
        SELECT v.id, v.name, v.department_id, d.name AS department_name, v.office_interview, v.is_active, v.position
          FROM hiring_vacancies v LEFT JOIN departments d ON d.id = v.department_id
    """ + (" WHERE v.is_active" if active_only else "") + " ORDER BY v.position, v.id")
    return _all(cursor)


def get_vacancy(cursor, vacancy_id):
    cursor.execute("SELECT id, name, department_id, office_interview, is_active FROM hiring_vacancies WHERE id = %s", (int(vacancy_id),))
    return _one(cursor)


def create_vacancy(cursor, name, department_id, office_interview, by, now):
    cursor.execute("""
        INSERT INTO hiring_vacancies (name, department_id, office_interview, position, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, COALESCE((SELECT MAX(position) + 1 FROM hiring_vacancies), 0), %s, %s, %s) RETURNING id
    """, (name, department_id, bool(office_interview), by, now, now))
    return int(cursor.fetchone()[0])


def update_vacancy(cursor, vacancy_id, now, **fields):
    fields['updated_at'] = now
    _update('hiring_vacancies', vacancy_id, fields,
            allowed=('name', 'department_id', 'office_interview', 'is_active', 'position', 'updated_at'), cursor=cursor)


def list_reasons(cursor, active_only=False):
    cursor.execute("SELECT id, kind, name, requires_comment, is_active, position FROM hiring_reasons"
                   + (" WHERE is_active" if active_only else "") + " ORDER BY kind, position, id")
    return _all(cursor)


def get_reason(cursor, reason_id):
    cursor.execute("SELECT id, kind, name, requires_comment, is_active FROM hiring_reasons WHERE id = %s", (int(reason_id),))
    return _one(cursor)


def reason_by_name(cursor, kind, name):
    cursor.execute("SELECT id, kind, name, requires_comment, is_active FROM hiring_reasons WHERE kind = %s AND name = %s", (kind, name))
    return _one(cursor)


def create_reason(cursor, kind, name, requires_comment, now):
    cursor.execute("""
        INSERT INTO hiring_reasons (kind, name, requires_comment, position, created_at)
        VALUES (%s, %s, %s, COALESCE((SELECT MAX(position) + 1 FROM hiring_reasons WHERE kind = %s), 0), %s)
        ON CONFLICT (kind, name) DO UPDATE SET is_active = TRUE RETURNING id
    """, (kind, name, bool(requires_comment), kind, now))
    return int(cursor.fetchone()[0])


def update_reason(cursor, reason_id, **fields):
    _update('hiring_reasons', reason_id, fields, allowed=('name', 'requires_comment', 'is_active', 'position'), cursor=cursor)


def list_groups(cursor, active_only=False, trainer_id=None):
    where = ["TRUE"]
    params = []
    if active_only:
        where.append("g.status = 'active'")
    if trainer_id:
        where.append("g.trainer_id = %s")
        params.append(int(trainer_id))
    cursor.execute("""
        SELECT g.id, g.name, g.start_date, g.trainer_id, t.name AS trainer_name, g.department_id,
               d.name AS department_name, g.vacancy_id, v.name AS vacancy_name, g.status,
               (SELECT COUNT(*) FROM hiring_cards c WHERE c.group_id = g.id) AS cards_total
          FROM hiring_groups g
          LEFT JOIN users t ON t.id = g.trainer_id
          LEFT JOIN departments d ON d.id = g.department_id
          LEFT JOIN hiring_vacancies v ON v.id = g.vacancy_id
         WHERE """ + " AND ".join(where) + " ORDER BY g.start_date DESC, g.id DESC", params)
    return _all(cursor)


def get_group(cursor, group_id):
    cursor.execute("SELECT id, name, start_date, trainer_id, department_id, vacancy_id, status FROM hiring_groups WHERE id = %s",
                   (int(group_id),))
    return _one(cursor)


def create_group(cursor, name, start_date, trainer_id, department_id, vacancy_id, by, now):
    cursor.execute("""
        INSERT INTO hiring_groups (name, start_date, trainer_id, department_id, vacancy_id, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (name, start_date, trainer_id, department_id, vacancy_id, by, now, now))
    return int(cursor.fetchone()[0])


def update_group(cursor, group_id, now, **fields):
    fields['updated_at'] = now
    _update('hiring_groups', group_id, fields,
            allowed=('name', 'start_date', 'trainer_id', 'department_id', 'vacancy_id', 'status', 'updated_at'), cursor=cursor)


def list_plans(cursor, month_start, recruiter_id=None):
    where = ["p.month = %s"]
    params = [month_start]
    if recruiter_id:
        where.append("(p.recruiter_id = %s OR p.recruiter_id IS NULL)")
        params.append(int(recruiter_id))
    cursor.execute("""
        SELECT p.id, p.month, p.vacancy_id, v.name AS vacancy_name, p.department_id, d.name AS department_name,
               p.recruiter_id, r.name AS recruiter_name, p.plan_fte, p.planned_date, p.comment
          FROM hiring_plans p
          LEFT JOIN hiring_vacancies v ON v.id = p.vacancy_id
          LEFT JOIN departments d ON d.id = p.department_id
          LEFT JOIN users r ON r.id = p.recruiter_id
         WHERE """ + " AND ".join(where) + " ORDER BY p.id", params)
    return _all(cursor)


def create_plan(cursor, month_start, vacancy_id, department_id, recruiter_id, plan_fte, planned_date, comment, by, now):
    cursor.execute("""
        INSERT INTO hiring_plans (month, vacancy_id, department_id, recruiter_id, plan_fte, planned_date, comment,
                                  created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (month_start, vacancy_id, department_id, recruiter_id, plan_fte, planned_date, comment, by, now, now))
    return int(cursor.fetchone()[0])


def update_plan(cursor, plan_id, now, **fields):
    fields['updated_at'] = now
    _update('hiring_plans', plan_id, fields,
            allowed=('vacancy_id', 'department_id', 'recruiter_id', 'plan_fte', 'planned_date', 'comment', 'updated_at'),
            cursor=cursor)


def delete_plan(cursor, plan_id):
    cursor.execute("DELETE FROM hiring_plans WHERE id = %s", (int(plan_id),))
    return cursor.rowcount


def plan_facts(cursor, month_start, month_end):
    """Факт по месячным задачам: закрытые наймы месяца по вакансии, отделу и рекрутеру.
    Возвращает список {vacancy_id, department_id, recruiter_id, hired} по кандидатам."""
    cursor.execute("""
        SELECT c.vacancy_id, COALESCE(v.department_id, g.department_id) AS department_id, c.recruiter_id
          FROM hiring_cards c
          LEFT JOIN hiring_vacancies v ON v.id = c.vacancy_id
          LEFT JOIN hiring_groups g ON g.id = c.group_id
         WHERE c.stage = 'hired' AND c.hired_at >= %s AND c.hired_at < %s
    """, (month_start, month_end))
    return _all(cursor)


def _update(table, row_id, fields, allowed, cursor):
    pairs = [(key, value) for key, value in fields.items() if key in allowed]
    if not pairs:
        return 0
    cursor.execute("UPDATE %s SET %s WHERE id = %%s" % (table, ", ".join("%s = %%s" % key for key, _ in pairs)),
                   [value for _, value in pairs] + [int(row_id)])
    return cursor.rowcount


# ─── Партии ───────────────────────────────────────────────────────────────────

def create_batch(cursor, source_id, file_name, by, by_name, now):
    cursor.execute("""
        INSERT INTO hiring_batches (source_id, file_name, loaded_by, loaded_by_name, loaded_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (int(source_id), file_name, by, by_name, now))
    return int(cursor.fetchone()[0])


def finish_batch(cursor, batch_id, total_rows, loaded, duplicates, errors, error_rows):
    cursor.execute("""
        UPDATE hiring_batches SET total_rows = %s, loaded = %s, duplicates = %s, errors = %s, error_rows = %s::jsonb
         WHERE id = %s
    """, (total_rows, loaded, duplicates, errors, json.dumps(error_rows[:500], ensure_ascii=False), int(batch_id)))


def list_batches(cursor, limit=50):
    cursor.execute("""
        SELECT b.id, b.source_id, s.name AS source_name, b.file_name, b.loaded_by, b.loaded_by_name, b.loaded_at,
               b.total_rows, b.loaded, b.duplicates, b.errors
          FROM hiring_batches b LEFT JOIN hiring_sources s ON s.id = b.source_id
         ORDER BY b.id DESC LIMIT %s
    """, (int(limit),))
    return _all(cursor)


def get_batch(cursor, batch_id):
    cursor.execute("""
        SELECT b.id, b.source_id, s.name AS source_name, b.file_name, b.loaded_by, b.loaded_by_name, b.loaded_at,
               b.total_rows, b.loaded, b.duplicates, b.errors, b.error_rows
          FROM hiring_batches b LEFT JOIN hiring_sources s ON s.id = b.source_id WHERE b.id = %s
    """, (int(batch_id),))
    row = _one(cursor)
    if row and isinstance(row.get('error_rows'), str):
        row['error_rows'] = json.loads(row['error_rows'] or '[]')
    return row


# ─── Карточки ─────────────────────────────────────────────────────────────────

_CARD_COLUMNS = """
    c.id, c.candidate_no, c.kind, c.full_name, c.phone, c.whatsapp, c.city, c.vacancy_id, c.vacancy_text, c.plan_id,
    c.source_id, c.batch_id, c.platform_ref, c.campaign, c.referred_by, c.received_at, c.stage, c.substage,
    c.recruiter_id, c.taken_at, c.next_contact_at, c.last_touch_at, c.no_answer_streak, c.office_visit_at,
    c.group_id, c.training_started_at, c.line_started_at, c.shifts_done, c.hired_at, c.hired_by,
    c.close_initiator, c.close_reason_id, c.close_reason_text, c.close_stage, c.close_day, c.close_comment,
    c.is_closed, c.closed_at, c.repeat_of_card_id, c.reactivations, c.comment, c.created_by, c.created_at, c.updated_at,
    v.name AS vacancy_name, COALESCE(v.department_id, g.department_id) AS department_id,
    s.name AS source_name, s.code AS source_code, r.name AS recruiter_name, g.name AS group_name,
    g.start_date AS group_start_date, g.trainer_id AS trainer_id, b.file_name AS batch_file
"""
_CARD_FROM = """
    FROM hiring_cards c
    LEFT JOIN hiring_vacancies v ON v.id = c.vacancy_id
    LEFT JOIN hiring_groups g ON g.id = c.group_id
    LEFT JOIN hiring_sources s ON s.id = c.source_id
    LEFT JOIN users r ON r.id = c.recruiter_id
    LEFT JOIN hiring_batches b ON b.id = c.batch_id
"""


def get_card(cursor, card_id, lock=False):
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + " WHERE c.id = %s" + (" FOR UPDATE OF c" if lock else ""),
                   (int(card_id),))
    return _one(cursor)


def open_card_by_phone(cursor, phone, lock=False):
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + " WHERE c.phone = %s AND NOT c.is_closed"
                   + (" FOR UPDATE OF c" if lock else ""), (phone,))
    return _one(cursor)


def last_closed_card_by_phone(cursor, phone):
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + " WHERE c.phone = %s AND c.is_closed ORDER BY c.id DESC LIMIT 1", (phone,))
    return _one(cursor)


def employee_by_phone_key(cursor, key):
    """Сотрудник с таким телефоном (по последним десяти цифрам): {'id', 'status'} или None.
    Действующий — карточка не создаётся (ТЗ 5.3); бывший — создаётся с пометкой."""
    if not key:
        return None
    cursor.execute("""
        SELECT id, status FROM users
         WHERE phone IS NOT NULL AND right(regexp_replace(phone, '\\D', '', 'g'), 10) = %s
         ORDER BY (status NOT IN ('fired', 'dismissal')) DESC, id LIMIT 1
    """, (key,))
    row = cursor.fetchone()
    return {'id': int(row[0]), 'status': row[1]} if row else None


def insert_card(cursor, fields):
    """Вставить карточку в пул. Возвращает id. Повтор телефона у открытой карточки —
    psycopg2.errors.UniqueViolation, вызывающий ловит её под SAVEPOINT."""
    columns = ('kind', 'full_name', 'phone', 'whatsapp', 'city', 'vacancy_id', 'vacancy_text', 'source_id', 'batch_id',
               'platform_ref', 'campaign', 'referred_by', 'received_at', 'stage', 'repeat_of_card_id', 'comment',
               'created_by', 'created_at', 'updated_at')
    cursor.execute(
        "INSERT INTO hiring_cards (%s) VALUES (%s) RETURNING id" % (", ".join(columns), ", ".join(['%s'] * len(columns))),
        [fields.get(column) for column in columns])
    return int(cursor.fetchone()[0])


def update_card(cursor, card_id, **fields):
    allowed = ('full_name', 'whatsapp', 'city', 'vacancy_id', 'vacancy_text', 'plan_id', 'source_id', 'campaign',
               'referred_by', 'stage', 'substage', 'recruiter_id', 'taken_at', 'next_contact_at', 'next_contact_notified',
               'last_touch_at', 'no_answer_streak', 'office_visit_at', 'group_id', 'training_started_at',
               'line_started_at', 'shifts_done', 'hired_at', 'hired_by', 'close_initiator', 'close_reason_id',
               'close_reason_text', 'close_stage', 'close_day', 'close_comment', 'is_closed', 'closed_at',
               'reactivations', 'comment', 'updated_at', 'candidate_no')
    return _update('hiring_cards', card_id, fields, allowed=allowed, cursor=cursor)


def claim_card(cursor, card_id, recruiter_id, now):
    """«Взять в работу»: один UPDATE с условием «ещё не закреплена» — двое одновременно не
    возьмут (ТЗ 7.1). Возвращает карточку или None (уже в работе / не в пуле)."""
    cursor.execute("""
        UPDATE hiring_cards
           SET stage = 'in_work', substage = NULL, recruiter_id = %s, taken_at = %s,
               candidate_no = COALESCE(candidate_no, nextval('hiring_candidate_no_seq')),
               updated_at = %s
         WHERE id = %s AND recruiter_id IS NULL AND stage IN ('contacts', 'responses') AND NOT is_closed
     RETURNING id
    """, (int(recruiter_id), now, now, int(card_id)))
    row = cursor.fetchone()
    return get_card(cursor, row[0]) if row else None


def add_stage(cursor, card_id, stage, substage, by, by_name, now):
    cursor.execute("""
        INSERT INTO hiring_card_stages (card_id, stage, substage, entered_at, by_user_id, by_user_name)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (int(card_id), stage, substage, now, by, by_name))


def card_stages(cursor, card_id):
    cursor.execute("""
        SELECT id, stage, substage, entered_at, by_user_id, by_user_name FROM hiring_card_stages
         WHERE card_id = %s ORDER BY id
    """, (int(card_id),))
    return _all(cursor)


def add_assignment(cursor, card_id, recruiter_id, reason, by, by_name, now, comment=None):
    cursor.execute("""
        INSERT INTO hiring_assignments (card_id, recruiter_id, assigned_at, reason, comment, by_user_id, by_user_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (int(card_id), recruiter_id, now, reason, comment, by, by_name))
    return int(cursor.fetchone()[0])


def release_assignments(cursor, card_id, now):
    cursor.execute("UPDATE hiring_assignments SET released_at = %s WHERE card_id = %s AND released_at IS NULL", (now, int(card_id)))


def card_assignments(cursor, card_id):
    cursor.execute("""
        SELECT a.id, a.recruiter_id, u.name AS recruiter_name, a.assigned_at, a.released_at, a.reason, a.comment,
               a.by_user_id, a.by_user_name
          FROM hiring_assignments a LEFT JOIN users u ON u.id = a.recruiter_id
         WHERE a.card_id = %s ORDER BY a.id
    """, (int(card_id),))
    return _all(cursor)


def add_touch(cursor, card_id, kind, result, body, by, by_name, now, duration_sec=None, meta=None):
    cursor.execute("""
        INSERT INTO hiring_touches (card_id, kind, result, body, duration_sec, author_id, author_name, at, meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id
    """, (int(card_id), kind, result, body, duration_sec, by, by_name, now, json.dumps(meta or {}, ensure_ascii=False)))
    return int(cursor.fetchone()[0])


def card_touches(cursor, card_id):
    cursor.execute("""
        SELECT id, kind, result, body, duration_sec, author_id, author_name, at, meta FROM hiring_touches
         WHERE card_id = %s ORDER BY id
    """, (int(card_id),))
    rows = _all(cursor)
    for row in rows:
        if isinstance(row.get('meta'), str):
            row['meta'] = json.loads(row['meta'] or '{}')
    return rows


def has_human_touch(cursor, card_id, since=None):
    cursor.execute("""
        SELECT EXISTS (SELECT 1 FROM hiring_touches WHERE card_id = %s AND kind IN ('call', 'whatsapp', 'comment')
                        AND (%s::timestamp IS NULL OR at >= %s))
    """, (int(card_id), since, since))
    return bool(cursor.fetchone()[0])


def add_shift(cursor, card_id, shift_date, by, by_name, now):
    cursor.execute("""
        INSERT INTO hiring_shifts (card_id, shift_date, marked_by, marked_by_name, marked_at)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (card_id, shift_date) DO NOTHING
    """, (int(card_id), shift_date, by, by_name, now))
    return cursor.rowcount


def remove_shift(cursor, card_id, shift_date):
    cursor.execute("DELETE FROM hiring_shifts WHERE card_id = %s AND shift_date = %s", (int(card_id), shift_date))
    return cursor.rowcount


def count_shifts(cursor, card_id):
    cursor.execute("SELECT COUNT(*) FROM hiring_shifts WHERE card_id = %s", (int(card_id),))
    return int(cursor.fetchone()[0])


def card_shifts(cursor, card_id):
    cursor.execute("SELECT shift_date, marked_by, marked_by_name, marked_at FROM hiring_shifts WHERE card_id = %s ORDER BY shift_date",
                   (int(card_id),))
    return _all(cursor)


# ─── Мотивация ────────────────────────────────────────────────────────────────

def upsert_motivation(cursor, card_id, recruiter_id, status, now):
    cursor.execute("""
        INSERT INTO hiring_motivation (card_id, recruiter_id, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (card_id) DO UPDATE SET recruiter_id = EXCLUDED.recruiter_id, status = EXCLUDED.status,
            updated_at = EXCLUDED.updated_at
    """, (int(card_id), recruiter_id, status, now, now))


def get_motivation(cursor, card_id):
    cursor.execute("""
        SELECT m.id, m.card_id, m.recruiter_id, u.name AS recruiter_name, m.status, m.confirmed_at, m.confirmed_by,
               m.paid_at, m.cancel_reason, m.cancelled_at
          FROM hiring_motivation m LEFT JOIN users u ON u.id = m.recruiter_id WHERE m.card_id = %s
    """, (int(card_id),))
    return _one(cursor)


def set_motivation(cursor, card_id, now, **fields):
    fields['updated_at'] = now
    cursor.execute("UPDATE hiring_motivation SET %s WHERE card_id = %%s" % ", ".join("%s = %%s" % key for key in fields),
                   list(fields.values()) + [int(card_id)])
    return cursor.rowcount


def motivation_list(cursor, month_start, month_end, recruiter_id=None):
    """Начисления месяца (по дате подтверждения) и ожидающие подтверждения — для раздела 7.2 и отчёта."""
    where = ["((m.status IN ('counted', 'paid') AND m.confirmed_at >= %s AND m.confirmed_at < %s) OR m.status = 'pending')"]
    params = [month_start, month_end]
    if recruiter_id:
        where.append("m.recruiter_id = %s")
        params.append(int(recruiter_id))
    cursor.execute("""
        SELECT m.id, m.card_id, m.recruiter_id, u.name AS recruiter_name, m.status, m.confirmed_at, m.paid_at,
               m.cancel_reason, c.candidate_no, c.full_name, c.vacancy_id, v.name AS vacancy_name, c.line_started_at,
               c.shifts_done
          FROM hiring_motivation m
          JOIN hiring_cards c ON c.id = m.card_id
          LEFT JOIN hiring_vacancies v ON v.id = c.vacancy_id
          LEFT JOIN users u ON u.id = m.recruiter_id
         WHERE """ + " AND ".join(where) + " ORDER BY m.status, m.confirmed_at DESC NULLS LAST, m.id DESC", params)
    return _all(cursor)


# ─── Уведомления ──────────────────────────────────────────────────────────────

def push_notification(cursor, user_ids, kind, title, body, card_id, now, tone='default', dedupe_key=None):
    created = 0
    for user_id in _ids(user_ids):
        cursor.execute("""
            INSERT INTO hiring_notifications (user_id, card_id, kind, title, body, tone, dedupe_key, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
        """, (user_id, card_id, kind, str(title or '')[:200], body, tone if tone in ('default', 'warning') else 'default',
              dedupe_key, now))
        created += cursor.rowcount
    return created


def bell(cursor, user_id, limit):
    cursor.execute("SELECT COUNT(*) FROM hiring_notifications WHERE user_id = %s AND read_at IS NULL", (int(user_id),))
    total = int(cursor.fetchone()[0] or 0)
    if not total:
        return 0, []
    cursor.execute("""
        SELECT id, card_id, kind, title, body, tone, created_at FROM hiring_notifications
         WHERE user_id = %s AND read_at IS NULL ORDER BY id DESC LIMIT %s
    """, (int(user_id), int(limit)))
    return total, _all(cursor)


def mark_card_seen(cursor, user_id, card_id, now):
    """Человек открыл карточку — его уведомления по ней прочитаны."""
    cursor.execute("UPDATE hiring_notifications SET read_at = %s WHERE user_id = %s AND card_id = %s AND read_at IS NULL",
                   (now, int(user_id), int(card_id)))
    return cursor.rowcount


def mark_all_seen(cursor, user_id, now):
    cursor.execute("UPDATE hiring_notifications SET read_at = %s WHERE user_id = %s AND read_at IS NULL", (now, int(user_id)))
    return cursor.rowcount


# ─── Интеграции ───────────────────────────────────────────────────────────────

def get_integration(cursor, code):
    cursor.execute("SELECT code, state, last_run_at, last_ok_at, last_error, cursor, received_total, updated_at "
                   "FROM hiring_integrations WHERE code = %s", (code,))
    row = _one(cursor)
    if row and isinstance(row.get('cursor'), str):
        row['cursor'] = json.loads(row['cursor'] or '{}')
    return row


def list_integrations(cursor):
    cursor.execute("SELECT code, state, last_run_at, last_ok_at, last_error, received_total, updated_at "
                   "FROM hiring_integrations ORDER BY code")
    return _all(cursor)


def set_integration(cursor, code, now, *, state=None, ok=False, error=None, cursor_value=None, received=0):
    cursor.execute("""
        INSERT INTO hiring_integrations (code, updated_at) VALUES (%s, %s) ON CONFLICT (code) DO NOTHING
    """, (code, now))
    cursor.execute("""
        UPDATE hiring_integrations
           SET state = COALESCE(%s, state), last_run_at = %s,
               last_ok_at = CASE WHEN %s THEN %s ELSE last_ok_at END,
               last_error = %s,
               cursor = COALESCE(%s::jsonb, cursor),
               received_total = received_total + %s,
               updated_at = %s
         WHERE code = %s
    """, (state, now, bool(ok), now, error, json.dumps(cursor_value) if cursor_value is not None else None,
          int(received or 0), now, code))


def olx_tables_exist(cursor):
    """Таблицы робота OLX есть? Имена без схемы — по search_path: на проде это public,
    на стенде и в тестах — своя схема."""
    cursor.execute("SELECT to_regclass('olx_journal') IS NOT NULL AND to_regclass('olx_threads') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def olx_responses_since(cursor, last_journal_id, limit=200):
    """Обращения робота OLX с номером: журнал после закладки, с именем и объявлением чата."""
    cursor.execute("""
        SELECT j.id, j.cabinet_code, j.thread_id, j.message_at, j.phone_normalized, j.created_at,
               t.interlocutor_name, t.advert_title
          FROM olx_journal j
          LEFT JOIN olx_threads t ON t.cabinet_code = j.cabinet_code AND t.thread_id = j.thread_id
         WHERE j.id > %s AND j.phone_normalized IS NOT NULL
           AND j.result IN ('lead_created', 'manual_review', 'duplicate')
         ORDER BY j.id LIMIT %s
    """, (int(last_journal_id or 0), int(limit)))
    return _all(cursor)


# ─── Доска, списки, рабочий стол ──────────────────────────────────────────────

def scope_clause(caps, user_id):
    """Что человек видит (ТЗ 2): руководитель — всё; рекрутер — пул и свои карточки; тренер —
    карточки своих групп; руководитель отдела — кандидатов своего отдела. Союзы — через OR."""
    parts, params = [], []
    if caps.get('lead'):
        return "TRUE", []
    if caps.get('recruiter'):
        parts.append("(c.stage IN ('contacts', 'responses') AND NOT c.is_closed)")
        parts.append("c.recruiter_id = %s")
        params.append(int(user_id))
    if caps.get('trainer_group_ids'):
        parts.append("c.group_id = ANY(%s)")
        params.append([int(x) for x in caps['trainer_group_ids']])
    if caps.get('headed_ids'):
        parts.append("COALESCE(v.department_id, g.department_id) = ANY(%s)")
        params.append([int(x) for x in caps['headed_ids']])
    if not parts:
        return "FALSE", []
    return "(" + " OR ".join(parts) + ")", params


def _filters(filters):
    where, params = [], []
    if filters.get('vacancy_id'):
        where.append("c.vacancy_id = %s")
        params.append(int(filters['vacancy_id']))
    if filters.get('city'):
        where.append("c.city ILIKE %s")
        params.append(str(filters['city']).strip())
    if filters.get('source_id'):
        where.append("c.source_id = %s")
        params.append(int(filters['source_id']))
    if filters.get('recruiter_id'):
        where.append("c.recruiter_id = %s")
        params.append(int(filters['recruiter_id']))
    if filters.get('stage'):
        where.append("c.stage = %s")
        params.append(str(filters['stage']))
    if filters.get('mine_user_id'):
        where.append("(c.recruiter_id = %s OR (c.stage IN ('contacts', 'responses') AND NOT c.is_closed))")
        params.append(int(filters['mine_user_id']))
    if filters.get('query'):
        text = str(filters['query']).strip()
        digits = ''.join(ch for ch in text if ch.isdigit())
        if len(digits) >= 4:
            where.append("(c.full_name ILIKE %s OR c.phone LIKE %s OR c.candidate_no::text = %s)")
            params.extend(['%' + text + '%', '%' + digits[-10:] + '%', digits])
        else:
            where.append("(c.full_name ILIKE %s OR c.city ILIKE %s OR COALESCE(c.vacancy_text, '') ILIKE %s)")
            params.extend(['%' + text + '%', '%' + text + '%', '%' + text + '%'])
    if filters.get('from'):
        where.append("c.received_at >= %s")
        params.append(filters['from'])
    if filters.get('to'):
        where.append("c.received_at < %s")
        params.append(filters['to'])
    return where, params


def board(cursor, caps, user_id, filters, per_column=60):
    """Колонки доски: порция карточек на колонку и полное число. Пул — свежие сверху,
    остальные — по последнему изменению."""
    scope_sql, scope_params = scope_clause(caps, user_id)
    where, params = _filters(filters)
    base_where = " AND ".join([scope_sql] + where)
    cursor.execute("SELECT c.stage, COUNT(*) " + _CARD_FROM + " WHERE " + base_where + " GROUP BY c.stage",
                   scope_params + params)
    counts = {stage: int(count) for stage, count in cursor.fetchall()}
    cursor.execute("""
        SELECT * FROM (
            SELECT """ + _CARD_COLUMNS + """,
                   ROW_NUMBER() OVER (PARTITION BY c.stage ORDER BY
                       CASE WHEN c.stage IN ('contacts', 'responses') THEN c.received_at ELSE c.updated_at END DESC, c.id DESC) AS rn
            """ + _CARD_FROM + " WHERE " + base_where + """
        ) ranked WHERE rn <= %s ORDER BY stage, rn
    """, scope_params + params + [int(per_column)])
    rows = _all(cursor)
    columns = {stage: [] for stage in rules.STAGE_CODES}
    for row in rows:
        row.pop('rn', None)
        columns.setdefault(row['stage'], []).append(row)
    return columns, counts


def list_cards(cursor, caps, user_id, filters, limit=200, offset=0, order='c.updated_at DESC, c.id DESC'):
    scope_sql, scope_params = scope_clause(caps, user_id)
    where, params = _filters(filters)
    base_where = " AND ".join([scope_sql] + where)
    if filters.get('ids'):
        base_where += " AND c.id = ANY(%s)"
        params.append([int(x) for x in filters['ids']])
    cursor.execute("SELECT COUNT(*) " + _CARD_FROM + " WHERE " + base_where, scope_params + params)
    total = int(cursor.fetchone()[0])
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + " WHERE " + base_where + " ORDER BY " + order
                   + " LIMIT %s OFFSET %s", scope_params + params + [int(limit), int(offset)])
    return total, _all(cursor)


def desk_tasks(cursor, user_id, today_start, today_end, filters=None):
    """Задачи рекрутера на сегодня (ТЗ 3): перезвонить, повторный дозвон, визит в офис, просроченные.
    filters — отборы рабочего стола (вакансия, город, источник, этап)."""
    where, params = _filters(filters or {})
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE c.recruiter_id = %s AND NOT c.is_closed AND c.stage IN ('in_work', 'invited_office', 'invited_training')
           AND ((c.next_contact_at >= %s AND c.next_contact_at < %s)
                OR c.next_contact_at < %s
                OR (c.stage = 'invited_office' AND c.office_visit_at >= %s AND c.office_visit_at < %s)
                OR (c.stage = 'in_work' AND c.substage = 'no_answer' AND c.next_contact_at IS NULL))
    """ + "".join(" AND " + clause for clause in where) + """
         ORDER BY COALESCE(c.next_contact_at, c.office_visit_at, c.updated_at), c.id
    """, [int(user_id), today_start, today_end, today_start, today_start, today_end] + params)
    return _all(cursor)


def stage_counts_for_recruiter(cursor, user_id, period_start, period_end, filters=None):
    """«Моя воронка» (ТЗ 3): сколько карточек, взятых в работу за период, достигло каждого
    этапа (хоть раз), и сколько из них с хотя бы одним дозвоном. user_id None — по всем
    рекрутерам (руководитель без фильтра); filters — отборы рабочего стола."""
    recruiter_id = int(user_id) if user_id else None
    where, params = _filters(filters or {})
    cohort_sql = """
        SELECT c.id FROM hiring_cards c
          LEFT JOIN hiring_vacancies v ON v.id = c.vacancy_id
         WHERE (%s::int IS NULL OR c.recruiter_id = %s) AND c.taken_at >= %s AND c.taken_at < %s
    """ + "".join(" AND " + clause for clause in where)
    cohort_params = [recruiter_id, recruiter_id, period_start, period_end] + params
    cursor.execute("""
        WITH cohort AS (""" + cohort_sql + """)
        SELECT s.stage, COUNT(DISTINCT s.card_id)
          FROM hiring_card_stages s JOIN cohort ON cohort.id = s.card_id
         GROUP BY s.stage
    """, cohort_params)
    reached = {stage: int(count) for stage, count in cursor.fetchall()}
    cursor.execute("""
        WITH cohort AS (""" + cohort_sql + """)
        SELECT COUNT(DISTINCT t.card_id) FROM hiring_touches t
          JOIN cohort ON cohort.id = t.card_id
         WHERE t.kind = 'call' AND t.result = 'dial'
    """, cohort_params)
    reached['dialed'] = int(cursor.fetchone()[0])
    return reached


def idle_cards(cursor, deadline):
    """Закреплённые карточки без касаний дольше срока (ТЗ 7.1)."""
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE c.recruiter_id IS NOT NULL AND NOT c.is_closed AND c.stage IN ('in_work', 'invited_office')
           AND COALESCE(c.last_touch_at, c.taken_at) < %s
         ORDER BY c.id
    """, (deadline,))
    return _all(cursor)


def due_next_contacts(cursor, now):
    """Наступила дата следующего контакта, рекрутер ещё не предупреждён (ТЗ 8.3)."""
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE c.recruiter_id IS NOT NULL AND NOT c.is_closed AND c.next_contact_at IS NOT NULL
           AND c.next_contact_at <= %s
           AND (c.next_contact_notified IS NULL OR c.next_contact_notified < c.next_contact_at)
         ORDER BY c.next_contact_at LIMIT 500
    """, (now,))
    return _all(cursor)


def overdue_cards_by_recruiter(cursor, today_start):
    """Просроченные задачи (дата следующего контакта раньше сегодняшнего дня) — по рекрутерам,
    для утренней сводки в Telegram (ТЗ 8.3: «только нарушения сроков»)."""
    cursor.execute("""
        SELECT c.recruiter_id, COUNT(*) FROM hiring_cards c
         WHERE c.recruiter_id IS NOT NULL AND NOT c.is_closed AND c.next_contact_at < %s
         GROUP BY c.recruiter_id
    """, (today_start,))
    return {int(recruiter_id): int(count) for recruiter_id, count in cursor.fetchall()}


def cards_of_recruiter_open(cursor, recruiter_id):
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE c.recruiter_id = %s AND NOT c.is_closed AND c.stage IN ('in_work', 'invited_office', 'invited_training')
         ORDER BY c.id
    """, (int(recruiter_id),))
    return _all(cursor)


def distinct_cities(cursor):
    cursor.execute("SELECT DISTINCT city FROM hiring_cards WHERE city IS NOT NULL AND city <> '' ORDER BY city LIMIT 300")
    return [row[0] for row in cursor.fetchall()]


def recruiters_with_cards(cursor):
    """Рекрутеры для фильтров: выданные и все, у кого есть карточки."""
    cursor.execute("""
        SELECT u.id, u.name FROM users u
         WHERE u.id IN (SELECT user_id FROM hiring_people WHERE is_active AND kind IN ('staff', 'remote', 'lead'))
            OR u.id IN (SELECT DISTINCT recruiter_id FROM hiring_cards WHERE recruiter_id IS NOT NULL)
         ORDER BY u.name
    """)
    return _all(cursor)
