# -*- coding: utf-8 -*-
"""SQL раздела «Воронка найма». Время в таблицах — наивное время Алматы (rules.ALMATY).

Все функции принимают готовый курсор и не коммитят: транзакцией управляет вызывающий
(service.py — одна операция, одна транзакция). Строки отдаются словарями по cursor.description.
"""

import json
from datetime import date

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


def _days(values):
    return sorted({value for value in (values or ()) if isinstance(value, date)})


# ─── Профиль и права ──────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
    SELECT u.name, u.role, u.status, u.department_id, d.code,
           ARRAY(SELECT h.id FROM departments h
                  WHERE h.head_user_id = u.id AND COALESCE(h.is_active, TRUE)),
           ARRAY(SELECT lower(h.code) FROM departments h
                  WHERE h.head_user_id = u.id AND COALESCE(h.is_active, TRUE) AND h.code IS NOT NULL),
           (SELECT p.kind FROM hiring_people p WHERE p.user_id = u.id AND p.is_active),
           -- Группы тренера: действующие и завершённые, где ещё идёт обучение (приглашён / вышел на обучение) —
           -- тренер завершил группу, а отметить выход на линию и отсев ему всё ещё нужно (разбор 18.11.2026).
           ARRAY(SELECT g.id FROM hiring_groups g WHERE g.trainer_id = u.id
                    AND (g.status = 'active' OR EXISTS (
                        SELECT 1 FROM hiring_cards c WHERE c.group_id = g.id AND NOT c.is_closed
                           AND c.stage IN ('invited_training', 'on_training')))),
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


def alert_user_ids(cursor):
    """Кому тревоги раздела — номер WhatsApp найма, возврат карточки в пул по сроку: руководители рекрутинга (глава и
    сотрудники HR, выдачи «lead»). Супер-админы — только с выдачей: раздел им открыт, но тревоги не их (владелец
    20.11.2026: «у всех суперадминов какие-то уведомления касательно данного раздела, которые не исчезают»)."""
    cursor.execute("""
        SELECT u.id FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.status NOT IN ('fired', 'dismissal')
           AND (lower(COALESCE(d.code, '')) = 'hr'
                OR EXISTS (SELECT 1 FROM departments h WHERE h.head_user_id = u.id AND lower(h.code) = 'hr')
                OR EXISTS (SELECT 1 FROM hiring_people p WHERE p.user_id = u.id AND p.is_active AND p.kind = 'lead'))
    """)
    return [int(row[0]) for row in cursor.fetchall()]


def wa_alerts_read(cursor, now, *, prefixes=None, except_key=None, before=None, not_users=None, kind='whatsapp'):
    """Погасить тревоги номера WhatsApp найма в колоколе (строки без карточки — их не гасит открытие карточки): по виду
    ключа (prefixes — 'wa:park:' и т. п., hiring/whatsapp.alert_for), кроме тревоги с ключом except_key, созданные
    раньше before, у всех, кроме not_users (кому тревоги положены). kind — чьи тревоги: номера WhatsApp или моста hh.kz
    ('hh'). Возвращает, сколько погашено."""
    where, params = ["kind = %s", "read_at IS NULL"], [kind]
    if prefixes:
        where.append("(" + " OR ".join(["dedupe_key LIKE %s"] * len(prefixes)) + ")")
        params.extend(prefix + '%' for prefix in prefixes)
    if except_key:
        where.append("dedupe_key IS DISTINCT FROM %s")
        params.append(except_key)
    if before is not None:
        where.append("created_at < %s")
        params.append(before)
    if not_users is not None:
        where.append("NOT (user_id = ANY(%s))")
        params.append([int(user_id) for user_id in not_users])
    cursor.execute("UPDATE hiring_notifications SET read_at = %s WHERE " + " AND ".join(where), [now] + params)
    return cursor.rowcount


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
    """Источники в порядке справочника: сначала откликов, потом контактов. color — цвет источника (владелец
    20.11.2026: каналы слиты с источниками), None — серый."""
    cursor.execute("SELECT id, code, name, kind, platform, is_active, position, color FROM hiring_sources"
                   + (" WHERE is_active" if active_only else "") + " ORDER BY kind DESC, position, id")
    return _all(cursor)


def source_by_code(cursor, code):
    cursor.execute("SELECT id, code, name, kind, platform, is_active, color FROM hiring_sources WHERE code = %s", (code,))
    return _one(cursor)


def get_source(cursor, source_id):
    cursor.execute("SELECT id, code, name, kind, platform, is_active, color FROM hiring_sources WHERE id = %s", (int(source_id),))
    return _one(cursor)


def source_by_name(cursor, name, kind, exclude_id=None):
    """Источник того же вида с таким именем без учёта регистра: «Таргет» и «таргет» — один источник."""
    cursor.execute("""
        SELECT id, code, name, kind, is_active FROM hiring_sources
         WHERE kind = %s AND lower(name) = lower(%s) AND (%s::int IS NULL OR id <> %s) ORDER BY id LIMIT 1
    """, (kind, name, exclude_id, exclude_id))
    return _one(cursor)


def create_source(cursor, code, name, kind, now, color=None):
    cursor.execute("""
        INSERT INTO hiring_sources (code, name, kind, position, created_at, color)
        VALUES (%s, %s, %s, COALESCE((SELECT MAX(position) + 1 FROM hiring_sources), 0), %s, %s) RETURNING id
    """, (code, name, kind, now, color))
    return int(cursor.fetchone()[0])


def update_source(cursor, source_id, **fields):
    return _update('hiring_sources', source_id, fields, allowed=('name', 'is_active', 'position', 'color'), cursor=cursor)


def switch_card_source(cursor, card_id, from_source_id, to_source_id):
    """Сменить источник карточки, только если он всё ещё from_source_id (повторный отклик по рекламе у карточки,
    пришедшей в WhatsApp без рекламы: она становится «Таргетом», а выбор рекрутера не перетирается)."""
    cursor.execute("UPDATE hiring_cards SET source_id = %s WHERE id = %s AND source_id = %s",
                   (int(to_source_id), int(card_id), int(from_source_id)))
    return cursor.rowcount


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


def list_groups(cursor, active_only=False, trainer_id=None, soonest_first=False, today=None):
    """Группы на обучение с местами: seats_taken — кандидаты, занявшие место (rules.SEAT_STAGES),
    seats_free — сколько ещё войдёт (None, если вместимость не задана). soonest_first — так их видит
    рекрутер, выбирая группу: сначала предстоящие (ближний старт сверху), уже начавшиеся (today — дата
    Алматы) — ниже, свежие сверху; иначе первой строкой стояла бы группа, которая учится вторую неделю
    (разбор 18.11.2026). В списке тренера и руководителя — свежие сверху."""
    where = ["TRUE"]
    params = [list(rules.SEAT_STAGES)]
    if active_only:
        where.append("g.status = 'active'")
    if trainer_id:
        where.append("g.trainer_id = %s")
        params.append(int(trainer_id))
    if soonest_first:
        today = today or rules.now_almaty().date()
        order = "(g.start_date < %s), CASE WHEN g.start_date >= %s THEN g.start_date END ASC, g.start_date DESC, g.id ASC"
        params.extend([today, today])
    else:
        order = "g.start_date DESC, g.id DESC"
    cursor.execute("""
        SELECT g.id, g.name, g.start_date, g.trainer_id, t.name AS trainer_name, g.department_id,
               d.name AS department_name, g.vacancy_id, v.name AS vacancy_name, g.status, g.capacity,
               COALESCE(seats.taken, 0) AS seats_taken
          FROM hiring_groups g
          LEFT JOIN users t ON t.id = g.trainer_id
          LEFT JOIN departments d ON d.id = g.department_id
          LEFT JOIN hiring_vacancies v ON v.id = g.vacancy_id
          LEFT JOIN LATERAL (SELECT COUNT(*) AS taken FROM hiring_cards c
                              WHERE c.group_id = g.id AND c.stage = ANY(%s)) seats ON TRUE
         WHERE """ + " AND ".join(where) + " ORDER BY " + order, params)
    rows = _all(cursor)
    for row in rows:
        row['seats_taken'] = int(row['seats_taken'] or 0)
        row['seats_free'] = rules.seats_free(row['capacity'], row['seats_taken'])
    return rows


def get_group(cursor, group_id, lock=False):
    """lock — FOR UPDATE: приглашение в группу и смена вместимости идут по одной, последнее место не
    достаётся двоим."""
    cursor.execute("SELECT id, name, start_date, trainer_id, department_id, vacancy_id, status, capacity FROM hiring_groups"
                   " WHERE id = %s" + (" FOR UPDATE" if lock else ""), (int(group_id),))
    return _one(cursor)


def group_seats_taken(cursor, group_id, exclude_card_id=None):
    """Сколько мест в группе занято (rules.SEAT_STAGES), не считая карточку exclude_card_id."""
    cursor.execute("SELECT COUNT(*) FROM hiring_cards WHERE group_id = %s AND stage = ANY(%s) AND id <> %s",
                   (int(group_id), list(rules.SEAT_STAGES), int(exclude_card_id or 0)))
    return int(cursor.fetchone()[0])


def create_group(cursor, name, start_date, trainer_id, department_id, vacancy_id, by, now, capacity=None):
    cursor.execute("""
        INSERT INTO hiring_groups (name, start_date, trainer_id, department_id, vacancy_id, capacity, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (name, start_date, trainer_id, department_id, vacancy_id, capacity, by, now, now))
    return int(cursor.fetchone()[0])


def update_group(cursor, group_id, now, **fields):
    fields['updated_at'] = now
    _update('hiring_groups', group_id, fields,
            allowed=('name', 'start_date', 'trainer_id', 'department_id', 'vacancy_id', 'status', 'capacity', 'updated_at'),
            cursor=cursor)


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
    c.group_id, c.training_started_at, c.line_started_at, c.shifts_done, c.hired_at, c.hired_by, c.hired10_at,
    c.close_initiator, c.close_reason_id, c.close_reason_text, c.close_stage, c.close_day, c.close_comment,
    c.is_closed, c.closed_at, c.repeat_of_card_id, c.reactivations, c.comment, c.created_by, c.created_at, c.updated_at,
    c.employee_user_id, e.name AS employee_name,
    v.name AS vacancy_name, COALESCE(v.department_id, g.department_id) AS department_id,
    s.name AS source_name, s.code AS source_code, r.name AS recruiter_name, g.name AS group_name,
    g.start_date AS group_start_date, g.trainer_id AS trainer_id, tr.name AS trainer_name, b.file_name AS batch_file,
    s.color AS source_color
"""
_CARD_FROM = """
    FROM hiring_cards c
    LEFT JOIN hiring_vacancies v ON v.id = c.vacancy_id
    LEFT JOIN hiring_groups g ON g.id = c.group_id
    LEFT JOIN hiring_sources s ON s.id = c.source_id
    LEFT JOIN users r ON r.id = c.recruiter_id
    LEFT JOIN hiring_batches b ON b.id = c.batch_id
    LEFT JOIN users e ON e.id = c.employee_user_id
    LEFT JOIN users tr ON tr.id = g.trainer_id
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
               'reactivations', 'comment', 'updated_at', 'candidate_no', 'employee_user_id', 'hired10_at')
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


def last_cycle_restart(cursor, card_id):
    """Когда карточка последний раз вернулась в пул (release, recruiter_off, auto_timeout) или была повторно
    активирована (reactivate): с этого момента переписка с кандидатом — новый круг. None — круг один."""
    cursor.execute("""
        SELECT MAX(assigned_at) FROM hiring_assignments
         WHERE card_id = %s AND reason IN ('release', 'recruiter_off', 'auto_timeout', 'reactivate')
    """, (int(card_id),))
    return cursor.fetchone()[0]


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


def get_touch(cursor, touch_id):
    cursor.execute("""
        SELECT id, card_id, kind, result, body, duration_sec, author_id, author_name, at, meta, call_id
          FROM hiring_touches WHERE id = %s
    """, (int(touch_id),))
    return _parse_json_fields(_one(cursor), 'meta')


def card_touches(cursor, card_id):
    cursor.execute("""
        SELECT id, kind, result, body, duration_sec, author_id, author_name, at, meta, call_id FROM hiring_touches
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


def daily_hours_table_exists(cursor):
    """Таблица «Учета часов» daily_hours (часы дня по статусам работы) есть? На стенде её может не быть."""
    cursor.execute("SELECT to_regclass('daily_hours') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def worked_days(cursor, user_id, since_day, until_day, min_hours):
    """Дни сотрудника, где по статусам работы отработано не меньше min_hours часов
    (daily_hours.work_time — часы, их считает «Учет часов» из статусов): {date: часы}."""
    cursor.execute("""
        SELECT day, work_time FROM daily_hours
         WHERE operator_id = %s AND day >= %s AND day <= %s AND work_time >= %s
         ORDER BY day
    """, (int(user_id), since_day, until_day, float(min_hours)))
    return {row[0]: float(row[1] or 0) for row in cursor.fetchall()}


def replace_auto_shifts(cursor, card_id, days, now):
    """Смены «из статусов» карточки привести к набору days {date: часы}: лишние убрать, новые
    добавить, часы обновить. Возвращает общее число смен."""
    cursor.execute("DELETE FROM hiring_shifts WHERE card_id = %s AND source = 'auto' AND NOT (shift_date = ANY(%s::date[]))",
                   (int(card_id), list(days.keys())))
    for day, hours in sorted(days.items()):
        cursor.execute("""
            INSERT INTO hiring_shifts (card_id, shift_date, marked_by, marked_by_name, marked_at, work_hours, source)
            VALUES (%s, %s, NULL, 'статусы работы', %s, %s, 'auto')
            ON CONFLICT (card_id, shift_date) DO UPDATE SET work_hours = EXCLUDED.work_hours, source = 'auto'
        """, (int(card_id), day, now, round(float(hours), 2)))
    return count_shifts(cursor, card_id)


def count_shifts(cursor, card_id):
    cursor.execute("SELECT COUNT(*) FROM hiring_shifts WHERE card_id = %s", (int(card_id),))
    return int(cursor.fetchone()[0])


def card_shifts(cursor, card_id):
    cursor.execute("""
        SELECT shift_date, marked_by, marked_by_name, marked_at, work_hours, source FROM hiring_shifts
         WHERE card_id = %s ORDER BY shift_date
    """, (int(card_id),))
    return _all(cursor)


def shift_cards(cursor):
    """Карточки, которым смены считаются из статусов работы: открытые «Вышел на линию» — до седьмой смены, и
    «Отработал 7 дней» без «Отработал 10 дней» — до десятой (SZOV-10). Уволенный сотрудник смен больше не наберёт —
    его «7 дней» не пересчитываются, иначе список рос бы без конца."""
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE (c.stage = 'on_line' AND NOT c.is_closed)
            OR (c.stage = 'hired' AND c.hired10_at IS NULL AND c.employee_user_id IS NOT NULL
                AND COALESCE(e.status, '') <> 'fired')
         ORDER BY c.id
    """)
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


# ─── Звонки из телефонии (Binotel) ────────────────────────────────────────────

def line_members_table_exists(cursor):
    cursor.execute("SELECT to_regclass('dial_list_line_members') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def recruiter_by_line(cursor, internal_number):
    """Действующий рекрутер на линии Binotel (членство в линии «Удаленного КЦ» + выдача):
    {'user_id', 'department_id'} или None."""
    if not line_members_table_exists(cursor):
        return None
    cursor.execute("""
        SELECT m.user_id, m.department_id FROM dial_list_line_members m
          JOIN hiring_people p ON p.user_id = m.user_id AND p.is_active
         WHERE m.released_at IS NULL AND m.internal_number = %s
         ORDER BY m.assigned_at DESC LIMIT 1
    """, (str(internal_number),))
    row = cursor.fetchone()
    return {'user_id': int(row[0]), 'department_id': int(row[1])} if row else None


def recruiter_lines(cursor):
    """Линии действующих рекрутеров: [{'user_id', 'department_id', 'internal_number'}] — для добора истории."""
    if not line_members_table_exists(cursor):
        return []
    cursor.execute("""
        SELECT m.user_id, m.department_id, m.internal_number FROM dial_list_line_members m
          JOIN hiring_people p ON p.user_id = m.user_id AND p.is_active
         WHERE m.released_at IS NULL ORDER BY m.user_id
    """)
    return [{'user_id': int(row[0]), 'department_id': int(row[1]), 'internal_number': str(row[2]).strip()}
            for row in cursor.fetchall()]


def card_for_call(cursor, phone, recruiter_id):
    """К какой карточке относится звонок по номеру: открытая этого рекрутера → любая открытая →
    последняя завершённая. None — такого кандидата у нас нет."""
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE c.phone = %s
         ORDER BY (NOT c.is_closed) DESC, (c.recruiter_id = %s) DESC, c.id DESC LIMIT 1
    """, (phone, int(recruiter_id or 0)))
    return _one(cursor)


def get_call(cursor, call_id):
    cursor.execute("""
        SELECT id, general_call_id, card_id, recruiter_id, department_id, internal_number, external_phone, direction,
               disposition, result, billsec, waitsec, started_at, recording, source, touch_id, created_at, updated_at
          FROM hiring_calls WHERE id = %s
    """, (int(call_id),))
    return _one(cursor)


def get_call_by_general_id(cursor, general_call_id):
    cursor.execute("SELECT id, card_id, touch_id, result, billsec, disposition FROM hiring_calls WHERE general_call_id = %s",
                   (str(general_call_id),))
    return _one(cursor)


def insert_call(cursor, call, card_id, recruiter_id, department_id, result, recording, source, now):
    cursor.execute("""
        INSERT INTO hiring_calls (general_call_id, card_id, recruiter_id, department_id, internal_number, external_phone,
                                  direction, disposition, result, billsec, waitsec, started_at, recording, source,
                                  created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (general_call_id) DO NOTHING RETURNING id
    """, (call['general_call_id'], int(card_id), recruiter_id, department_id, call['internal_number'], call['external_phone'],
          call['direction'], call['disposition'], result, int(call['billsec']), int(call['waitsec']),
          call['started_at'] or now, recording, source, now, now))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def update_call_outcome(cursor, call_id, disposition, result, billsec, waitsec, recording, now):
    cursor.execute("""
        UPDATE hiring_calls SET disposition = %s, result = %s, billsec = %s, waitsec = %s, recording = %s, updated_at = %s
         WHERE id = %s
    """, (disposition, result, int(billsec), int(waitsec), recording, now, int(call_id)))


def link_call_touch(cursor, call_id, touch_id):
    cursor.execute("UPDATE hiring_calls SET touch_id = %s WHERE id = %s", (int(touch_id), int(call_id)))
    cursor.execute("UPDATE hiring_touches SET call_id = %s WHERE id = %s", (int(call_id), int(touch_id)))


def card_calls(cursor, card_id):
    cursor.execute("""
        SELECT c.id, c.general_call_id, c.recruiter_id, u.name AS recruiter_name, c.department_id, c.internal_number,
               c.direction, c.disposition, c.result, c.billsec, c.waitsec, c.started_at, c.recording, c.source, c.touch_id
          FROM hiring_calls c LEFT JOIN users u ON u.id = c.recruiter_id
         WHERE c.card_id = %s ORDER BY c.started_at, c.id
    """, (int(card_id),))
    return _all(cursor)


def manual_call_touch(cursor, card_id, recruiter_id, around, window_minutes):
    """Отметка звонка рядом по времени, ещё не связанная со звонком АТС: отметка того же рекрутера
    (с исходом или без) либо чья угодно отметка «ждём исход» — «Позвонить» мог нажать руководитель
    из своей учётки, а звонок ушёл с линии рекрутера. Чужую отметку с исходом не трогаем."""
    cursor.execute("""
        SELECT id, result, body, at FROM hiring_touches
         WHERE card_id = %s AND kind = 'call' AND call_id IS NULL
           AND (author_id = %s OR result IS NULL)
           AND at BETWEEN %s - (%s * interval '1 minute') AND %s + (%s * interval '1 minute')
         ORDER BY (author_id = %s) DESC, abs(extract(epoch FROM (at - %s))) LIMIT 1
    """, (int(card_id), int(recruiter_id), around, int(window_minutes), around, int(window_minutes),
          int(recruiter_id), around))
    return _one(cursor)


def orphan_pending_calls(cursor, before, window_minutes):
    """Касания «ждём исход», которые телефония обошла стороной — завела свою запись на той же
    карточке в окне после них (до правки слияния 30.10.2026): [(id ожидания, тело, id записи
    телефонии, её исход, тело, длительность, meta, id звонка)], по одной записи на ожидание."""
    cursor.execute("""
        SELECT DISTINCT ON (p.id) p.id, p.body, t.id, t.result, t.body, t.duration_sec, t.meta, c.id
          FROM hiring_touches p
          JOIN hiring_touches t ON t.card_id = p.card_id AND t.kind = 'call' AND t.call_id IS NOT NULL
               AND t.at >= p.at AND t.at <= p.at + (%s * interval '1 minute')
          JOIN hiring_calls c ON c.id = t.call_id
         WHERE p.kind = 'call' AND p.result IS NULL AND p.call_id IS NULL AND p.at <= %s
         ORDER BY p.id, t.at
    """, (int(window_minutes), before))
    rows = cursor.fetchall()
    result = []
    for row in rows:
        meta = row[6]
        if isinstance(meta, str):
            meta = json.loads(meta or '{}')
        result.append({'pending_id': int(row[0]), 'pending_body': row[1], 'touch_id': int(row[2]), 'result': row[3],
                       'body': row[4], 'duration_sec': row[5], 'meta': meta or {}, 'call_id': int(row[7])})
    return result


def delete_touch(cursor, touch_id):
    cursor.execute("DELETE FROM hiring_touches WHERE id = %s", (int(touch_id),))
    return cursor.rowcount


def update_touch(cursor, touch_id, **fields):
    allowed = ('result', 'body', 'duration_sec', 'meta', 'at')
    pairs = [(key, value) for key, value in fields.items() if key in allowed]
    if not pairs:
        return 0
    sets = ", ".join(("%s = %%s::jsonb" if key == 'meta' else "%s = %%s") % key for key, _ in pairs)
    values = [json.dumps(value, ensure_ascii=False) if key == 'meta' else value for key, value in pairs]
    cursor.execute("UPDATE hiring_touches SET " + sets + " WHERE id = %s", values + [int(touch_id)])
    return cursor.rowcount


# ─── Интеграции ───────────────────────────────────────────────────────────────

def _parse_json_fields(row, *names):
    for name in names:
        if row and isinstance(row.get(name), str):
            row[name] = json.loads(row[name] or '{}')
    return row


def get_integration(cursor, code):
    cursor.execute("SELECT code, state, last_run_at, last_ok_at, last_error, cursor, settings, received_total, updated_at "
                   "FROM hiring_integrations WHERE code = %s", (code,))
    return _parse_json_fields(_one(cursor), 'cursor', 'settings')


def list_integrations(cursor):
    cursor.execute("SELECT code, state, last_run_at, last_ok_at, last_error, settings, received_total, updated_at "
                   "FROM hiring_integrations ORDER BY code")
    return [_parse_json_fields(row, 'settings') for row in _all(cursor)]


def set_integration_settings(cursor, code, settings, now):
    """Настройки интеграции руками руководителя (у OLX — кабинеты «для найма»)."""
    cursor.execute("""
        INSERT INTO hiring_integrations (code, updated_at) VALUES (%s, %s) ON CONFLICT (code) DO NOTHING
    """, (code, now))
    cursor.execute("UPDATE hiring_integrations SET settings = %s::jsonb, updated_at = %s WHERE code = %s",
                   (json.dumps(settings or {}, ensure_ascii=False), now, code))


def olx_cabinet_codes(cursor):
    """Коды кабинетов, которые знает робот OLX (таблица olx_accounts); нет таблицы — пусто."""
    cursor.execute("SELECT to_regclass('olx_accounts') IS NOT NULL")
    if not cursor.fetchone()[0]:
        return []
    cursor.execute("SELECT code FROM olx_accounts ORDER BY code")
    return [row[0] for row in cursor.fetchall()]


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


# ─── Мост hh.kz (hiring/hh.py) ────────────────────────────────────────────────

def hh_lock_integration(cursor, now):
    """Строка интеграции hh под замком: доклады моста, сторож и правка руководителя идут по очереди."""
    cursor.execute("""
        INSERT INTO hiring_integrations (code, updated_at) VALUES ('hh', %s) ON CONFLICT (code) DO NOTHING
    """, (now,))
    cursor.execute("SELECT code, state, last_run_at, last_ok_at, last_error, cursor, settings, received_total, updated_at "
                   "FROM hiring_integrations WHERE code = 'hh' FOR UPDATE")
    return _parse_json_fields(_one(cursor), 'cursor', 'settings')


def hh_claim_topic(cursor, item, now):
    """Занять тему hh за этим приёмом. False — её уже принимали (повтор после перезапуска моста или потерянного
    ответа портала): второй раз карточку она не заводит. Одновременный второй приём ждёт первый на ключе."""
    cursor.execute("""
        INSERT INTO hiring_hh_topics (topic_id, hh_vacancy_id, responded_at, outcome, created_at)
        VALUES (%s, %s, %s, 'pending', %s) ON CONFLICT (topic_id) DO NOTHING
    """, (int(item['topic_id']), int(item['vacancy_id']), item.get('responded_at'), now))
    return cursor.rowcount == 1


def hh_finish_topic(cursor, topic_id, outcome, card_id, item=None):
    """Исход темы. Резюме и письмо отклика — только когда тема стала карточкой или повторным откликом в ней:
    у отклика мимо воронки персональным данным здесь делать нечего."""
    item = item if card_id and item else {}
    cursor.execute("""
        UPDATE hiring_hh_topics
           SET outcome = %s, card_id = %s, vacancy_name = %s, resume_url = %s, letter = %s, resume = %s::jsonb
         WHERE topic_id = %s
    """, (outcome, card_id, item.get('vacancy_name'), item.get('resume_url'), item.get('letter'),
          json.dumps(item['resume'], ensure_ascii=False) if item.get('resume') else None, int(topic_id)))


def hh_card_responses(cursor, card_id):
    """Отклики hh карточки с резюме или письмом — новые сверху (вкладка «Резюме»)."""
    cursor.execute("""
        SELECT topic_id, hh_vacancy_id, vacancy_name, responded_at, resume_url, letter, resume
          FROM hiring_hh_topics
         WHERE card_id = %s AND (letter IS NOT NULL OR resume IS NOT NULL)
         ORDER BY responded_at DESC NULLS LAST, topic_id DESC
         LIMIT 20
    """, (int(card_id),))
    return [{'topic_id': str(row[0]), 'vacancy_id': str(row[1]), 'vacancy_name': row[2], 'responded_at': row[3],
             'resume_url': row[4], 'letter': row[5],
             'resume': json.loads(row[6]) if isinstance(row[6], str) else row[6]} for row in cursor.fetchall()]


def hh_save_report(cursor, now, *, state, error, report, received, ok):
    """Доклад моста: состояние входа, что он видит, сколько карточек завёл. last_run_at — последний обмен с мостом."""
    set_integration(cursor, 'hh', now, state=state, ok=ok, error=error, cursor_value=report, received=received)


def hh_mark_state(cursor, now, state, error):
    """Сторож: мост замолчал — состояние и текст ошибки, без «последнего обмена» (его не было)."""
    cursor.execute("UPDATE hiring_integrations SET state = %s, last_error = %s, updated_at = %s WHERE code = 'hh'",
                   (state, error, now))


def hh_save_settings(cursor, now, vacancies):
    """Правка руководителя: правила вакансий hh целиком; «с какого момента» и остальное в настройках остаются."""
    cursor.execute("""
        UPDATE hiring_integrations
           SET settings = settings || jsonb_build_object('vacancies', %s::jsonb), updated_at = %s
         WHERE code = 'hh'
    """, (json.dumps(vacancies, ensure_ascii=False), now))


def hh_vacancy_ids(cursor):
    """Вакансии iCORE, которые руководитель может поставить отклику hh."""
    cursor.execute("SELECT id FROM hiring_vacancies")
    return {int(row[0]) for row in cursor.fetchall()}


def olx_tables_exist(cursor):
    """Таблицы робота OLX есть? Имена без схемы — по search_path: на проде это public,
    на стенде и в тестах — своя схема."""
    cursor.execute("SELECT to_regclass('olx_journal') IS NOT NULL AND to_regclass('olx_threads') IS NOT NULL")
    return bool(cursor.fetchone()[0])


def olx_responses_since(cursor, last_journal_id, cabinets, limit=200):
    """Обращения робота OLX с номером из кабинетов «для найма»: журнал после закладки, с именем и
    объявлением чата. Кабинеты лидов (таксопарки) сюда не попадают — список задаёт руководитель.
    `received` — номер, принятый без сделки в amoCRM (сделки робота выключены с 19.11.2026)."""
    codes = [str(code) for code in (cabinets or ()) if code]
    if not codes:
        return []
    cursor.execute("""
        SELECT j.id, j.cabinet_code, j.thread_id, j.message_at, j.phone_normalized, j.created_at,
               t.interlocutor_name, t.advert_title
          FROM olx_journal j
          LEFT JOIN olx_threads t ON t.cabinet_code = j.cabinet_code AND t.thread_id = j.thread_id
         WHERE j.id > %s AND j.phone_normalized IS NOT NULL AND j.cabinet_code = ANY(%s)
           AND j.result IN ('lead_created', 'manual_review', 'duplicate', 'received')
         ORDER BY j.id LIMIT %s
    """, (int(last_journal_id or 0), codes, int(limit)))
    return _all(cursor)


def olx_journal_max_id(cursor):
    cursor.execute("SELECT COALESCE(MAX(id), 0) FROM olx_journal")
    return int(cursor.fetchone()[0])


# ─── Доска, списки, рабочий стол ──────────────────────────────────────────────

def scope_clause(caps, user_id):
    """Что человек видит (ТЗ 2): руководитель — всё; рекрутер — пул и свои карточки; тренер —
    карточки своих групп; руководитель отдела — кандидатов своего отдела. Союзы — через OR."""
    parts, params = [], []
    # Тренер по роли читает всю воронку (владелец 19.11.2026) — действовать в чужих карточках ему не даёт сервис.
    if caps.get('lead') or caps.get('read_all'):
        return "TRUE", []
    if caps.get('recruiter'):
        parts.append("(c.stage IN ('contacts', 'responses') AND NOT c.is_closed)")
        parts.append("c.recruiter_id = %s")
        params.append(int(user_id))
    if caps.get('trainer_group_ids'):
        # Тренеру — кандидаты группы с приглашения на обучение и дальше. Карточка, вернувшаяся в работу
        # (повторная активация) или в пул, помнит старую группу, но тренеру её не видно — там телефон и
        # чужая работа рекрутера (разбор 18.11.2026; то же правило — HiringService.visible).
        parts.append("(c.group_id = ANY(%s) AND c.stage <> ALL(%s))")
        params.append([int(x) for x in caps['trainer_group_ids']])
        params.append(list(rules.TRAINER_HIDDEN_STAGES))
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
            # Удалённому рекрутеру (phone_scope_user_id) поиск по цифрам телефона — только в своих карточках:
            # в пуле телефон ему не показывается, и «нашлось / не нашлось» выдавало бы скрытый номер.
            phone_clause = "c.phone LIKE %s"
            phone_params = ['%' + digits[-10:] + '%']
            if filters.get('phone_scope_user_id'):
                phone_clause = "(c.phone LIKE %s AND c.recruiter_id = %s)"
                phone_params.append(int(filters['phone_scope_user_id']))
            where.append("(c.full_name ILIKE %s OR " + phone_clause + " OR c.candidate_no::text = %s)")
            params.extend(['%' + text + '%'] + phone_params + [digits])
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


def _board_where(caps, user_id, filters, responses_period=None):
    """Условие доски и его параметры: круг человека (scope_clause), отборы и период «Откликов» — он режет только их."""
    scope_sql, scope_params = scope_clause(caps, user_id)
    where, params = _filters(filters)
    start, end = responses_period or (None, None)
    bounds, bound_params = [], []
    if start:
        bounds.append("c.received_at >= %s")
        bound_params.append(start)
    if end:
        bounds.append("c.received_at < %s")
        bound_params.append(end)
    if bounds:
        where.append("(c.stage <> 'responses' OR (" + " AND ".join(bounds) + "))")
        params.extend(bound_params)
    return " AND ".join([scope_sql] + where), scope_params + params


# День карточки на доске (rules.board_day): визит в офис — его дата, обучение — дата старта группы, другие этапы — NULL.
_BOARD_DAY = ("CASE c.stage WHEN 'invited_office' THEN c.office_visit_at::date "
              "WHEN 'invited_training' THEN g.start_date END")


def board(cursor, caps, user_id, filters, per_column=60, responses_period=None, expand_sources=(), expanded_limit=1000,
          responses_open=None, days_open=None, expand_days=None):
    """Колонки доски: порция карточек на колонку и полное число. Пул — свежие сверху, остальные — по последнему
    изменению.

    «Отклики» поделены по источникам (владелец 20.11.2026: «столбец Отклики разделить на каналы с названием и
    кол-вом»): порция и число — у каждого источника своё; expand_sources — источники, у которых «Показать ещё»
    (порция expanded_limit). Группы свёрнуты, пока их не раскрыли (решение владельца 20.11.2026): responses_open —
    (id источников, раскрыта ли группа без источника) — карточки отдаются только раскрытым группам, остальным — одно
    число; None — всем (как до свёртки). Период — только у «Откликов» («остальные статусы не должны по периоду
    фильтроваться»): responses_period = (с, по) по дате поступления, «по» не включительно, любая граница может быть
    None. Возвращает (колонки, число по этапам, число откликов по источникам).

    «Приглашен в офис» и «Приглашен на обучение» поделены по дням так же (владелец 24.11.2026, rules.board_day): порция —
    на день, в офисе карточки дня идут по времени визита. days_open — {этап: (дни, раскрыта ли группа без даты)}:
    карточки только раскрытым дням, этапа нет в словаре — ни одному; None — всем дням. expand_days — {этап: дни} с
    «Показать ещё». Число карточек на каждый день — board_days."""
    base_where, base_params = _board_where(caps, user_id, filters, responses_period)
    cursor.execute("SELECT c.stage, COUNT(*) " + _CARD_FROM + " WHERE " + base_where + " GROUP BY c.stage", base_params)
    counts = {stage: int(count) for stage, count in cursor.fetchall()}
    cursor.execute("SELECT c.source_id, COUNT(*) " + _CARD_FROM + " WHERE " + base_where
                   + " AND c.stage = 'responses' GROUP BY c.source_id", base_params)
    by_source = {source_id: int(count) for source_id, count in cursor.fetchall()}
    day_limits, day_limit_params, day_opened, day_opened_params = [], [], [], []
    for stage in rules.DATED_STAGES:
        day_limits.append("WHEN stage = %s AND board_day = ANY(%s::date[]) THEN %s")
        day_limit_params += [stage, _days((expand_days or {}).get(stage)), int(expanded_limit)]
        opened, opened_none = (days_open or {}).get(stage) or ((), False)
        day_opened.append("AND (stage <> %s OR %s OR board_day = ANY(%s::date[]) OR (board_day IS NULL AND %s))")
        day_opened_params += [stage, days_open is None, _days(opened), bool(opened_none)]
    cursor.execute("""
        SELECT * FROM (
            SELECT """ + _CARD_COLUMNS + """, """ + _BOARD_DAY + """ AS board_day,
                   ROW_NUMBER() OVER (PARTITION BY c.stage, CASE WHEN c.stage = 'responses' THEN c.source_id END, """
                   + _BOARD_DAY + """ ORDER BY CASE WHEN c.stage = 'invited_office' THEN c.office_visit_at END,
                       CASE WHEN c.stage IN ('contacts', 'responses') THEN c.received_at ELSE c.updated_at END DESC, c.id DESC) AS rn
            """ + _CARD_FROM + " WHERE " + base_where + """
        ) ranked
         WHERE rn <= CASE WHEN stage = 'responses' AND source_id = ANY(%s::int[]) THEN %s """ + " ".join(day_limits) + """
                          ELSE %s END
           AND (stage <> 'responses' OR %s OR source_id = ANY(%s::int[]) OR (source_id IS NULL AND %s))
           """ + " ".join(day_opened) + """
         ORDER BY stage, CASE WHEN stage = 'invited_office' THEN office_visit_at END,
                  CASE WHEN stage IN ('contacts', 'responses') THEN received_at ELSE updated_at END DESC, id DESC
    """, base_params + [_ids(expand_sources), int(expanded_limit)] + day_limit_params + [int(per_column),
                        responses_open is None, _ids((responses_open or ((), False))[0]),
                        bool(responses_open and responses_open[1])] + day_opened_params)
    rows = _all(cursor)
    columns = {stage: [] for stage in rules.STAGE_CODES}
    for row in rows:
        row.pop('rn', None)
        row.pop('board_day', None)
        columns.setdefault(row['stage'], []).append(row)
    # «Отработал 10 дней» (SZOV-10) — колонка категории, а не этапа: те же карточки «Отработал 7 дней», у которых засчитана
    # десятая смена, — кандидат виден в обеих колонках сразу. Свежие засчитанные сверху; запросы — только когда на доске
    # вообще есть «7 дней».
    columns[rules.HIRED_10] = []
    if counts.get(rules.HIRED):
        ten_where = base_where + " AND c.stage = 'hired' AND c.hired10_at IS NOT NULL"
        cursor.execute("SELECT COUNT(*) " + _CARD_FROM + " WHERE " + ten_where, base_params)
        counts[rules.HIRED_10] = int(cursor.fetchone()[0])
        if counts[rules.HIRED_10]:
            cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + " WHERE " + ten_where
                           + " ORDER BY c.hired10_at DESC, c.id DESC LIMIT %s", base_params + [int(per_column)])
            columns[rules.HIRED_10] = _all(cursor)
    return columns, counts, by_source


def board_days(cursor, caps, user_id, filters):
    """Сколько карточек «Приглашен в офис» и «Приглашен на обучение» на каждый день (rules.board_day) с отборами доски —
    числа у групп-дней, а не только у показанных карточек: {этап: {день или None: число}}."""
    base_where, base_params = _board_where(caps, user_id, filters)
    cursor.execute("SELECT c.stage, " + _BOARD_DAY + ", COUNT(*) " + _CARD_FROM + " WHERE " + base_where
                   + " AND c.stage = ANY(%s) GROUP BY 1, 2", base_params + [list(rules.DATED_STAGES)])
    days = {stage: {} for stage in rules.DATED_STAGES}
    for stage, day, count in cursor.fetchall():
        days[stage][day] = int(count)
    return days


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


def silent_cards(cursor, deadline, card_id=None):
    """«В работе», а кандидат молчит (владелец 20.11.2026: «не отвечает ни на звонок, ни на ватсап в течение 72 часов»):
    первая попытка связаться — звонок без ответа или наше сообщение в WhatsApp (касание) — после последнего ответа
    кандидата была не позже deadline. Ответ — дозвон, его сообщение в WhatsApp, повторный отклик. Всё считается в
    нынешнем круге работы: с последнего входа в «В работе» (взятие, возврат из «Не отвечает»). С кандидатом не
    связывались — он не «молчит», карточка остаётся (решение владельца «с первой попытки»). Отдаёт id карточек."""
    cursor.execute("""
        WITH work AS (
            SELECT c.id, c.phone, c.whatsapp,
                   GREATEST(c.taken_at, (SELECT MAX(s.entered_at) FROM hiring_card_stages s
                                          WHERE s.card_id = c.id AND s.stage = 'in_work')) AS round_at
              FROM hiring_cards c
             WHERE c.stage = 'in_work' AND NOT c.is_closed AND c.taken_at IS NOT NULL
               AND (%(card)s::int IS NULL OR c.id = %(card)s::int)
        ),
        answered AS (
            SELECT w.id, w.round_at, GREATEST(
                (SELECT MAX(t.at) FROM hiring_touches t
                  WHERE t.card_id = w.id AND t.at >= w.round_at
                    AND ((t.kind = 'call' AND t.result = 'dial') OR t.kind = 'repeat_response')),
                (SELECT MAX(m.sent_at) FROM hiring_wa_messages m JOIN hiring_wa_chats ch ON ch.key = m.chat_key
                  WHERE (ch.card_id = w.id OR ch.key = w.phone OR ch.key = w.whatsapp)
                    AND NOT m.from_me AND m.sent_at >= w.round_at)) AS last_answer
              FROM work w
        )
        SELECT a.id FROM answered a
         WHERE (SELECT MIN(t.at) FROM hiring_touches t
                 WHERE t.card_id = a.id AND t.at >= a.round_at
                   AND (a.last_answer IS NULL OR t.at > a.last_answer)
                   AND ((t.kind = 'call' AND t.result = 'no_answer') OR t.kind = 'whatsapp')) <= %(deadline)s
         ORDER BY a.id
    """, {'card': int(card_id) if card_id else None, 'deadline': deadline})
    return [int(row[0]) for row in cursor.fetchall()]


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
           AND c.stage IN ('in_work', 'invited_office', 'invited_training')
         GROUP BY c.recruiter_id
    """, (today_start,))
    return {int(recruiter_id): int(count) for recruiter_id, count in cursor.fetchall()}


def cards_of_recruiter_open(cursor, recruiter_id):
    """Карточки рекрутера до обучения — с ними он уходит, они — в пул; «Не отвечает звонок/чат» тоже: иначе ответ
    кандидата вернул бы её «В работу» человеку без выдачи."""
    cursor.execute("SELECT " + _CARD_COLUMNS + _CARD_FROM + """
         WHERE c.recruiter_id = %s AND NOT c.is_closed
           AND c.stage IN ('in_work', 'invited_office', 'invited_training', 'no_response')
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


# ─── Номер WhatsApp найма (сервис на шлюзе, hiring/whatsapp.py) ───────────────

_WA_CHAT_COLUMNS = ("key, jid, phone, lid, push_name, card_id, first_at, last_at, last_inbound_at, messages, "
                    "COALESCE(waiting, 0) AS waiting, waiting_since, last_outbound_at, handled_at")


def wa_get_chat(cursor, key, lock=False):
    cursor.execute("SELECT " + _WA_CHAT_COLUMNS + " FROM hiring_wa_chats WHERE key = %s"
                   + (" FOR UPDATE" if lock else ""), (key,))
    return _one(cursor)


def wa_chat_by_lid(cursor, lid, lock=False):
    """Чат «только LID» (номер скрыт) — к нему доклеивается номер, когда тот станет известен."""
    cursor.execute("SELECT " + _WA_CHAT_COLUMNS + " FROM hiring_wa_chats WHERE key = %s"
                   + (" FOR UPDATE" if lock else ""), ('lid:' + str(lid),))
    return _one(cursor)


def wa_insert_message(cursor, key, message, now):
    """Сообщение один раз: повтор доставки (сервис шлёт заново после сбоя) — None."""
    cursor.execute("""
        INSERT INTO hiring_wa_messages (chat_key, wa_id, from_me, sent_at, kind, body, media, ad, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
        ON CONFLICT (chat_key, wa_id) DO NOTHING
        RETURNING id
    """, (key, message['id'], message['from_me'], message['at'] or now, message['kind'], message.get('text'),
          json.dumps(message['media'], ensure_ascii=False) if message.get('media') else None,
          json.dumps(message['ad'], ensure_ascii=False) if message.get('ad') else None, now))
    row = cursor.fetchone()
    return row[0] if row else None


def wa_touch_chat(cursor, key, jid, phone, lid, push_name, at, inbound, now):
    """Чат собеседника после нового сообщения: создать или освежить. Имя — только из входящих:
    в исходящем «имя отправителя» — наше собственное."""
    cursor.execute("""
        INSERT INTO hiring_wa_chats (key, jid, phone, lid, push_name, first_at, last_at, last_inbound_at,
                                     messages, created_at, updated_at, waiting)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, 0)
        ON CONFLICT (key) DO UPDATE SET
            jid = EXCLUDED.jid,
            phone = COALESCE(EXCLUDED.phone, hiring_wa_chats.phone),
            lid = COALESCE(EXCLUDED.lid, hiring_wa_chats.lid),
            push_name = COALESCE(EXCLUDED.push_name, hiring_wa_chats.push_name),
            first_at = LEAST(hiring_wa_chats.first_at, EXCLUDED.first_at),
            last_at = GREATEST(hiring_wa_chats.last_at, EXCLUDED.last_at),
            last_inbound_at = GREATEST(hiring_wa_chats.last_inbound_at, EXCLUDED.last_inbound_at),
            messages = hiring_wa_chats.messages + 1,
            updated_at = EXCLUDED.updated_at
        RETURNING """ + _WA_CHAT_COLUMNS,
                   (key, jid, phone, lid, push_name if inbound else None, at, at, at if inbound else None, now, now))
    return _one(cursor)


def wa_move_chat(cursor, old_key, new_key, now):
    """Чат «только LID» узнал номер: сообщения переезжают к ключу номера, строка LID уходит.
    Дубль одного сообщения под обоими ключами остаётся один. Чат номера к этому моменту есть."""
    cursor.execute("""
        UPDATE hiring_wa_messages m SET chat_key = %s
         WHERE m.chat_key = %s
           AND NOT EXISTS (SELECT 1 FROM hiring_wa_messages d WHERE d.chat_key = %s AND d.wa_id = m.wa_id)
    """, (new_key, old_key, new_key))
    cursor.execute("DELETE FROM hiring_wa_messages WHERE chat_key = %s", (old_key,))
    cursor.execute("""
        UPDATE hiring_wa_chats n SET
            first_at = LEAST(n.first_at, o.first_at),
            last_at = GREATEST(n.last_at, o.last_at),
            last_inbound_at = GREATEST(n.last_inbound_at, o.last_inbound_at),
            push_name = COALESCE(n.push_name, o.push_name),
            lid = COALESCE(n.lid, o.lid),
            messages = (SELECT COUNT(*) FROM hiring_wa_messages WHERE chat_key = n.key),
            last_outbound_at = GREATEST(n.last_outbound_at, o.last_outbound_at),
            handled_at = GREATEST(n.handled_at, o.handled_at),
            updated_at = %s
          FROM hiring_wa_chats o
         WHERE n.key = %s AND o.key = %s
    """, (now, new_key, old_key))
    cursor.execute("DELETE FROM hiring_wa_chats WHERE key = %s", (old_key,))
    wa_recount_waiting(cursor, new_key)


def wa_set_chat_card(cursor, key, card_id, now):
    cursor.execute("UPDATE hiring_wa_chats SET card_id = %s, updated_at = %s WHERE key = %s", (card_id, now, key))


def wa_create_chat(cursor, key, jid, phone, card_id, now):
    """Пустой чат карточки — написать кандидату первым (владелец 20.11.2026). Чат с этим ключом уже есть (второе
    нажатие в ту же секунду) — None: вызывающий перечитает его."""
    cursor.execute("""
        INSERT INTO hiring_wa_chats (key, jid, phone, card_id, first_at, last_at, messages, created_at, updated_at,
                                     waiting)
        VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, 0)
        ON CONFLICT (key) DO NOTHING
        RETURNING """ + _WA_CHAT_COLUMNS, (key, jid, phone, int(card_id), now, now, now, now))
    return _one(cursor)


def wa_edit_message(cursor, key, wa_id, body, now):
    cursor.execute("""UPDATE hiring_wa_messages SET body = %s, edited_at = %s
                       WHERE chat_key = %s AND wa_id = %s AND deleted_at IS NULL""", (body, now, key, wa_id))
    return cursor.rowcount


def wa_revoke_message(cursor, key, wa_id, now):
    """Удалено у всех — текст и файл гасятся и в портале: собеседник убрал их и с телефона рекрутера.
    Возвращает (сколько строк, пути файлов в бакете — удалить после транзакции)."""
    cursor.execute("""
        WITH old AS (
            SELECT id, media->>'object' AS object FROM hiring_wa_messages
             WHERE chat_key = %s AND wa_id = %s AND deleted_at IS NULL FOR UPDATE
        )
        UPDATE hiring_wa_messages m SET body = NULL, media = NULL, deleted_at = %s
          FROM old WHERE m.id = old.id
        RETURNING old.object
    """, (key, wa_id, now))
    rows = cursor.fetchall()
    return len(rows), [row[0] for row in rows if row[0]]


def wa_messages_by_id(cursor, wa_id):
    """Сообщения с этим id WhatsApp (на деле одно) — для файла от шлюза."""
    cursor.execute("""SELECT id, chat_key, kind, media, from_me, sent_at, deleted_at
                        FROM hiring_wa_messages WHERE wa_id = %s ORDER BY id""", (wa_id,))
    rows = _all(cursor)
    for row in rows:
        _parse_json_fields(row, 'media')
    return rows


def wa_media_stored(cursor, message_id, stored):
    """Файл лёг в бакет: путь и тип — в описание файла сообщения. Повтор (файл уже есть) — False."""
    cursor.execute("""
        UPDATE hiring_wa_messages SET media = COALESCE(media, '{}'::jsonb) || %s::jsonb
         WHERE id = %s AND deleted_at IS NULL AND COALESCE(media->>'object', '') = ''
        RETURNING id
    """, (json.dumps(stored, ensure_ascii=False), int(message_id)))
    return cursor.fetchone() is not None


def wa_media_failed(cursor, wa_id, reason, now):
    """Шлюз сообщил, что файл не сохранится (у WhatsApp его нет, больше предела, попытки кончились)."""
    cursor.execute("""
        UPDATE hiring_wa_messages SET media = COALESCE(media, '{}'::jsonb) || jsonb_build_object('failed', %s, 'failed_at', %s)
         WHERE wa_id = %s AND deleted_at IS NULL AND COALESCE(media->>'object', '') = ''
    """, (reason, now.isoformat(timespec='seconds'), wa_id))
    return cursor.rowcount


# Сообщение с файлом, которого в бакете нет и уже не будет от шлюза само (wa_media: файлы старых
# сообщений). Условие совпадает с частичным индексом idx_hiring_wa_messages_no_file (schema.py): опрос
# шлюза раз в 10 с читает только такие строки — обычно ни одной.
_NO_FILE = """m.kind IN ('image', 'video', 'audio', 'voice', 'document') AND m.deleted_at IS NULL
              AND m.media->>'object' IS NULL AND m.media->>'failed' IS NULL"""


def wa_media_resend_due(cursor, max_bytes, retry_before_iso, stale_before, attempts, limit):
    """Сообщения без файла, которые пора попросить у телефона заново: шлюз файл не качает (пришло до
    09.11.2026 или «загружается» дольше суток) и прошлая просьба осталась без ответа дольше паузы —
    пока попытки не кончились. Сначала свежие. С каждым — следующее сообщение того же чата (anchor_*):
    от него телефон отсчитывает историю (запасной путь). Строки захватываются до конца транзакции опроса."""
    cursor.execute("""
        SELECT m.id, m.wa_id, m.from_me, c.jid, c.phone, c.lid,
               COALESCE((m.media->'resend'->>'attempts')::int, 0) AS attempts,
               n.wa_id AS anchor_id, n.from_me AS anchor_from_me, n.sent_at AS anchor_at
          FROM hiring_wa_messages m JOIN hiring_wa_chats c ON c.key = m.chat_key
          LEFT JOIN LATERAL (
                SELECT x.wa_id, x.from_me, x.sent_at FROM hiring_wa_messages x
                 WHERE x.chat_key = m.chat_key AND (x.sent_at, x.id) > (m.sent_at, m.id)
                 ORDER BY x.sent_at, x.id LIMIT 1) n ON TRUE
         WHERE """ + _NO_FILE + """
           AND COALESCE((m.media->>'size')::bigint, 0) <= %s
           AND CASE WHEN m.media ? 'resend'
                    THEN m.media->'resend'->>'at' < %s AND COALESCE((m.media->'resend'->>'attempts')::int, 0) < %s
                    ELSE COALESCE(m.media->>'fetch', '') <> 'true' OR m.created_at < %s END
         ORDER BY m.sent_at DESC
         LIMIT %s
           FOR UPDATE OF m SKIP LOCKED
    """, (int(max_bytes), retry_before_iso, int(attempts), stale_before, int(limit)))
    return _all(cursor)


def wa_media_resend_mark(cursor, message_ids, now_iso):
    """Просьба ушла шлюзу: время и номер попытки — в описание файла (во вкладке — «загружается…»)."""
    if not message_ids:
        return
    cursor.execute("""
        UPDATE hiring_wa_messages
           SET media = COALESCE(media, '{}'::jsonb) || jsonb_build_object('resend', jsonb_build_object(
                   'at', %s::text, 'attempts', COALESCE((media->'resend'->>'attempts')::int, 0) + 1))
         WHERE id = ANY(%s)
    """, (now_iso, [int(message_id) for message_id in message_ids]))


def wa_media_resend_exhausted(cursor, retry_before_iso, attempts, now):
    """Телефон не прислал сообщение ни на одну просьбу — «не сохранился (телефон не прислал файл)»."""
    cursor.execute("""
        UPDATE hiring_wa_messages m
           SET media = m.media || jsonb_build_object('failed', 'unanswered', 'failed_at', %s::text)
         WHERE """ + _NO_FILE + """
           AND m.media ? 'resend'
           AND COALESCE((m.media->'resend'->>'attempts')::int, 0) >= %s
           AND m.media->'resend'->>'at' < %s
    """, (now.isoformat(timespec='seconds'), int(attempts), retry_before_iso))
    return cursor.rowcount


def wa_claim_transcript(cursor, message_id, now_iso, stale_iso):
    """Взять голосовое в расшифровку: ждёт своей очереди или прежний захват устарел (процесс умер на
    выкладке). Два процесса выкладки одно и то же не берут — за распознавание платим один раз."""
    cursor.execute("""
        UPDATE hiring_wa_messages
           SET media = jsonb_set(media, '{transcript}', COALESCE(media->'transcript', '{}'::jsonb)
                                 || jsonb_build_object('status', 'working', 'claimed_at', %s::text))
         WHERE id = %s AND deleted_at IS NULL AND COALESCE(media->>'object', '') <> ''
           AND ((media->'transcript'->>'status' = 'pending' AND COALESCE(media->'transcript'->>'next_at', '') <= %s)
                OR (media->'transcript'->>'status' = 'working' AND COALESCE(media->'transcript'->>'claimed_at', '') < %s))
        RETURNING id, kind, media
    """, (now_iso, int(message_id), now_iso, stale_iso))
    return _parse_json_fields(_one(cursor), 'media')


def wa_save_transcript(cursor, message_id, transcript):
    cursor.execute("""UPDATE hiring_wa_messages SET media = jsonb_set(media, '{transcript}', %s::jsonb)
                       WHERE id = %s AND media IS NOT NULL""",
                   (json.dumps(transcript, ensure_ascii=False), int(message_id)))


def wa_transcripts_due(cursor, now_iso, stale_iso, limit=3):
    """Голосовые, чья расшифровка ждёт повтора или зависла (сторож раз в минуту)."""
    cursor.execute("""
        SELECT id FROM hiring_wa_messages
         WHERE kind IN ('voice', 'audio') AND deleted_at IS NULL AND media ? 'transcript'
           AND ((media->'transcript'->>'status' = 'pending' AND COALESCE(media->'transcript'->>'next_at', '') <= %s)
                OR (media->'transcript'->>'status' = 'working' AND COALESCE(media->'transcript'->>'claimed_at', '') < %s))
         ORDER BY id LIMIT %s
    """, (now_iso, stale_iso, int(limit)))
    return [int(row[0]) for row in cursor.fetchall()]


def wa_chats_for_phones(cursor, phones):
    phones = [p for p in dict.fromkeys(phones or ()) if p]
    if not phones:
        return []
    cursor.execute("SELECT " + _WA_CHAT_COLUMNS + " FROM hiring_wa_chats WHERE key = ANY(%s) ORDER BY last_at DESC",
                   (phones,))
    return _all(cursor)


def wa_summary(cursor, phones):
    """Сколько сообщений с номером найма у телефонов карточки и когда последнее — для вкладки."""
    phones = [p for p in dict.fromkeys(phones or ()) if p]
    if not phones:
        return {'messages': 0, 'last_at': None}
    cursor.execute("SELECT COALESCE(SUM(messages), 0), MAX(last_at) FROM hiring_wa_chats WHERE key = ANY(%s)", (phones,))
    count, last_at = cursor.fetchone()
    return {'messages': int(count or 0), 'last_at': last_at}


def wa_messages(cursor, keys, limit=400):
    """Последние сообщения чатов — по времени, старые сверху (как в ленте телефона)."""
    if not keys:
        return []
    cursor.execute("""
        SELECT id, chat_key, wa_id, from_me, sent_at, kind, body, media, ad, edited_at, deleted_at, author_name, status,
               quote_wa_id FROM (
            SELECT * FROM hiring_wa_messages WHERE chat_key = ANY(%s) ORDER BY sent_at DESC, id DESC LIMIT %s
        ) recent ORDER BY sent_at, id
    """, (list(keys), int(limit)))
    rows = _all(cursor)
    for row in rows:
        _parse_json_fields(row, 'media', 'ad')
    return rows


def wa_unmatched(cursor):
    """Чаты с входящими, которые карточкой не стали: номер не казахстанский или скрыт (LID)."""
    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE key LIKE 'wa:%%'), COUNT(*) FILTER (WHERE key LIKE 'lid:%%')
          FROM hiring_wa_chats WHERE last_inbound_at IS NOT NULL
    """)
    foreign, hidden = cursor.fetchone()
    return {'foreign': int(foreign or 0), 'hidden': int(hidden or 0)}


def wa_add_received(cursor, count, now):
    if count:
        cursor.execute("UPDATE hiring_integrations SET received_total = received_total + %s, updated_at = %s "
                       "WHERE code = 'whatsapp'", (int(count), now))


def wa_lock_integration(cursor, now):
    """Строка интеграции под замком: опрос сервиса, сторож и кнопки людей меняют её по очереди."""
    cursor.execute("""
        INSERT INTO hiring_integrations (code, updated_at) VALUES ('whatsapp', %s) ON CONFLICT (code) DO NOTHING
    """, (now,))
    cursor.execute("SELECT code, state, last_run_at, last_ok_at, last_error, cursor, settings, received_total, updated_at "
                   "FROM hiring_integrations WHERE code = 'whatsapp' FOR UPDATE")
    return _parse_json_fields(_one(cursor), 'cursor', 'settings')


def wa_set_command(cursor, command, now):
    """Команда сервису: одна в очереди («Подключить», «Возобновить», «Отвязать»); None — снять."""
    if command is None:
        cursor.execute("UPDATE hiring_integrations SET settings = settings - 'command', updated_at = %s "
                       "WHERE code = 'whatsapp'", (now,))
    else:
        cursor.execute("""UPDATE hiring_integrations SET settings = settings || jsonb_build_object('command', %s::jsonb),
                                 updated_at = %s WHERE code = 'whatsapp'""",
                       (json.dumps(command, ensure_ascii=False), now))


def wa_last_inbound(cursor, key, at):
    """Последнее сообщение кандидата не позже ответа — по нему автоответ отличается от рекрутера."""
    cursor.execute("SELECT MAX(sent_at) FROM hiring_wa_messages WHERE chat_key = %s AND NOT from_me AND sent_at <= %s",
                   (key, at))
    return cursor.fetchone()[0]


def wa_our_message(cursor, phones, since, auto_gap):
    """Есть ли наше сообщение человеку с номера WhatsApp найма не раньше since (владелец 09.11.2026:
    переписку мы видим сами — сообщение с нашей стороны засчитывается за связь с кандидатом, нажимать
    «Написать в WhatsApp» не обязательно). Чат — по номерам карточки, как во вкладке «WhatsApp»: так
    видно и переписку, которую рекрутер начал сам. Автоответ WhatsApp Business (быстрее auto_gap после
    сообщения кандидата) и удалённое у всех — не в счёт."""
    phones = [p for p in dict.fromkeys(phones or ()) if p]
    if not phones:
        return False
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM hiring_wa_messages m
             WHERE m.chat_key = ANY(%s) AND m.from_me AND m.deleted_at IS NULL
               AND (%s::timestamp IS NULL OR m.sent_at >= %s)
               AND NOT EXISTS (SELECT 1 FROM hiring_wa_messages i
                                WHERE i.chat_key = m.chat_key AND NOT i.from_me
                                  AND i.sent_at <= m.sent_at AND i.sent_at > m.sent_at - %s)
        )
    """, (phones, since, since, auto_gap))
    return bool(cursor.fetchone()[0])


def wa_phone_touched(cursor, card_id, day_start, day_end):
    """Касание «ответ с телефона» за эти сутки уже есть: переписка — одно касание в день."""
    cursor.execute("""
        SELECT 1 FROM hiring_touches
         WHERE card_id = %s AND kind = 'whatsapp' AND meta->>'source' = 'wa_phone' AND at >= %s AND at < %s
         LIMIT 1
    """, (int(card_id), day_start, day_end))
    return cursor.fetchone() is not None


def wa_save_settings(cursor, patch, now):
    """Настройки номера руками руководителя (кто отвечает с телефона) — поверх команды сервису."""
    cursor.execute("UPDATE hiring_integrations SET settings = settings || %s::jsonb, updated_at = %s "
                   "WHERE code = 'whatsapp'", (json.dumps(patch, ensure_ascii=False), now))


def wa_recruiter_options(cursor):
    """Кого можно выбрать «Отвечает с телефона»: рекрутеры и руководители по выдаче раздела, отдел HR."""
    cursor.execute("""
        SELECT u.id, u.name FROM users u
         WHERE u.status NOT IN ('fired', 'dismissal')
           AND (EXISTS (SELECT 1 FROM hiring_people p WHERE p.user_id = u.id AND p.is_active
                         AND p.kind IN ('staff', 'remote', 'lead'))
                OR u.department_id IN (SELECT id FROM departments WHERE lower(code) = 'hr'))
         ORDER BY u.name
    """)
    return _all(cursor)


def wa_phone_replies(cursor):
    """Ответы с телефона в чатах открытых карточек по времени — для прогона задним числом, когда
    «Отвечает с телефона» выбрали после того, как переписка уже шла."""
    cursor.execute("""
        SELECT m.chat_key, m.wa_id, m.sent_at, m.kind, m.body, w.card_id
          FROM hiring_wa_messages m
          JOIN hiring_wa_chats w ON w.key = m.chat_key AND w.card_id IS NOT NULL
          JOIN hiring_cards c ON c.id = w.card_id AND NOT c.is_closed
         WHERE m.from_me AND m.deleted_at IS NULL AND m.author_id IS NULL
         ORDER BY m.sent_at, m.id
    """)
    return _all(cursor)


def wa_save_state(cursor, state, detail, error, now, *, heartbeat=True, ok=False):
    """Состояние от сервиса (или от сторожа — heartbeat=False: время опроса не трогаем)."""
    cursor.execute("""
        UPDATE hiring_integrations
           SET state = %s, cursor = %s::jsonb, last_error = %s,
               last_run_at = CASE WHEN %s THEN %s ELSE last_run_at END,
               last_ok_at = CASE WHEN %s THEN %s ELSE last_ok_at END,
               updated_at = %s
         WHERE code = 'whatsapp'
    """, (state, json.dumps(detail or {}, ensure_ascii=False, default=str), error,
          bool(heartbeat), now, bool(ok), now, now))


# ─── Переписка из портала: окно «Чаты» (hiring/wa_chat.py) ──────────────────────

def wa_recount_waiting(cursor, key):
    """«Ждут ответа» у чата заново по переписке: сообщения кандидата после нашего последнего ответа и после
    «Ответ не нужен». Пересчёт, а не +1: события шлюза приходят не по порядку (досылка после простоя)."""
    cursor.execute("""
        UPDATE hiring_wa_chats w
           SET waiting = s.n, waiting_since = s.since
          FROM (
              SELECT COUNT(*) AS n, MIN(m.sent_at) AS since
                FROM hiring_wa_messages m, hiring_wa_chats c
               WHERE c.key = %(key)s AND m.chat_key = c.key AND NOT m.from_me AND m.deleted_at IS NULL
                 AND m.sent_at > GREATEST(COALESCE(c.last_outbound_at, '-infinity'::timestamp),
                                          COALESCE(c.handled_at, '-infinity'::timestamp))
          ) s
         WHERE w.key = %(key)s
    """, {'key': key})


def wa_mark_outbound(cursor, key, at):
    """Наш ответ в чате (с телефона или из портала): сообщения кандидата до него ответа больше не ждут."""
    cursor.execute("""
        UPDATE hiring_wa_chats SET last_outbound_at = GREATEST(COALESCE(last_outbound_at, %(at)s), %(at)s)
         WHERE key = %(key)s
    """, {'key': key, 'at': at})
    wa_recount_waiting(cursor, key)


def wa_mark_handled(cursor, key, user_id, now):
    """«Ответ не нужен»: всё, что кандидат написал до сих пор (по времени сообщений), ответа не ждёт; новое —
    и дошедшее позже, но написанное после, — снова ждёт."""
    cursor.execute("""
        UPDATE hiring_wa_chats
           SET handled_at = GREATEST(last_inbound_at, handled_at), handled_by = %(user)s, updated_at = %(now)s
         WHERE key = %(key)s
    """, {'key': key, 'user': int(user_id), 'now': now})
    wa_recount_waiting(cursor, key)


def wa_recount_outbound(cursor, key, auto_gap):
    """Наш последний ответ заново по переписке — после удаления «у всех» нашего сообщения чат снова ждёт ответа."""
    cursor.execute("""
        UPDATE hiring_wa_chats w SET last_outbound_at = (
            SELECT MAX(m.sent_at) FROM hiring_wa_messages m
             WHERE m.chat_key = w.key AND m.from_me AND m.deleted_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM hiring_wa_messages i
                                WHERE i.chat_key = m.chat_key AND NOT i.from_me
                                  AND i.sent_at <= m.sent_at AND i.sent_at > m.sent_at - %s))
         WHERE w.key = %s
    """, (auto_gap, key))
    wa_recount_waiting(cursor, key)


def wa_recount_recent(cursor, since):
    """Сторож: «ждут ответа» заново у чатов, менявшихся с since — лечит сообщения, которые принял прежний процесс в
    окне выкладки (он счётчик не вёл)."""
    cursor.execute("SELECT key FROM hiring_wa_chats WHERE updated_at >= %s ORDER BY updated_at DESC LIMIT 500", (since,))
    keys = [row[0] for row in cursor.fetchall()]
    for key in keys:
        wa_recount_waiting(cursor, key)
    return len(keys)


def wa_chat_by_card(cursor, card_id, lock=False):
    """Чат карточки (адрес 'card:<id>' — для того, кому номер не виден): последний по переписке."""
    cursor.execute("SELECT " + _WA_CHAT_COLUMNS + " FROM hiring_wa_chats WHERE card_id = %s ORDER BY last_at DESC LIMIT 1"
                   + (" FOR UPDATE" if lock else ""), (int(card_id),))
    return _one(cursor)


_CHAT_LIST_FROM = """
      FROM hiring_wa_chats w
      LEFT JOIN hiring_cards c ON c.id = w.card_id
      LEFT JOIN hiring_vacancies v ON v.id = c.vacancy_id
      LEFT JOIN hiring_groups g ON g.id = c.group_id
      LEFT JOIN users r ON r.id = c.recruiter_id
"""


def _like(text):
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def _chat_list_where(scope_sql, scope_params, *, lead, user_id, mode, waiting_only, search, phone_own_only=False, stages=()):
    """Чаты, куда кандидат писал сам, и чаты карточек, где уже есть сообщения (кандидату написали первыми из карточки,
    владелец 20.11.2026; пустой чат, открытый кнопкой и брошенный, в списке не висит), — в пределах видимости карточек
    (queries.scope_clause); чаты без карточки (иностранный номер, скрытый) — только руководителю. stages — отбор по
    статусам (этапам) карточки кандидата (23.11.2026: «фильтровать кандидатов по статусам»): чат без карточки в него
    не входит."""
    where = ["(w.last_inbound_at IS NOT NULL OR (w.card_id IS NOT NULL AND w.messages > 0))",
             "((c.id IS NOT NULL AND %s)%s)" % (scope_sql, " OR c.id IS NULL" if lead else "")]
    params = list(scope_params)
    if mode == 'mine':
        where.append("c.recruiter_id = %s")
        params.append(int(user_id))
    elif mode == 'pool':
        where.append("c.stage IN ('contacts', 'responses') AND NOT c.is_closed")
    if waiting_only:
        where.append("COALESCE(w.waiting, 0) > 0")
    if stages:
        where.append("c.stage = ANY(%s)")
        params.append(list(stages))
    text = ' '.join(str(search or '').split())[:80]
    if text:
        digits = ''.join(ch for ch in text if ch.isdigit())
        parts = ["c.full_name ILIKE %s", "w.push_name ILIKE %s"]
        params.extend([_like(text), _like(text)])
        if len(digits) >= 4:
            if phone_own_only:
                parts.append("(regexp_replace(COALESCE(w.phone, ''), '[^0-9]', '', 'g') LIKE %s AND c.recruiter_id = %s)")
                params.extend(['%' + digits + '%', int(user_id)])
            else:
                parts.append("regexp_replace(COALESCE(w.phone, ''), '[^0-9]', '', 'g') LIKE %s")
                params.append('%' + digits + '%')
        if digits and digits == text and len(digits) <= 9:
            parts.append("c.candidate_no = %s")
            params.append(int(digits))
        where.append("(" + " OR ".join(parts) + ")")
    return " AND ".join(where), params


def wa_chat_list(cursor, scope_sql, scope_params, *, lead, user_id, mode='all', waiting_only=False, search='',
                 limit=50, offset=0, phone_own_only=False, stages=()):
    """Страница списка чатов: ждущие ответа сверху, и внутри них, и ниже — по последнему сообщению, свежее выше
    (владелец 20.11.2026: «даже у не прочитанных сообщений последнее написанное сообщение отображалось наверху»;
    раньше среди ждущих первым стоял дольше всех ждущий). Последнее сообщение — только у строк страницы (LATERAL
    по индексу чата)."""
    where, params = _chat_list_where(scope_sql, scope_params, lead=lead, user_id=user_id, mode=mode,
                                     waiting_only=waiting_only, search=search, phone_own_only=phone_own_only, stages=stages)
    cursor.execute("""
        WITH page AS (
            SELECT w.key, w.jid, w.phone, w.push_name, w.card_id, w.last_at, w.last_inbound_at,
                   COALESCE(w.waiting, 0) AS waiting, w.waiting_since, c.full_name, c.stage, c.substage,
                   c.recruiter_id, r.name AS recruiter_name, c.is_closed, c.candidate_no
            """ + _CHAT_LIST_FROM + """
             WHERE """ + where + """
             ORDER BY (COALESCE(w.waiting, 0) > 0) DESC, w.last_at DESC, w.key
             LIMIT %s OFFSET %s
        )
        SELECT p.*, lm.from_me AS last_from_me, lm.kind AS last_kind, lm.body AS last_body, lm.status AS last_status,
               lm.deleted_at IS NOT NULL AS last_deleted, lm.author_name AS last_author
          FROM page p
          LEFT JOIN LATERAL (
              SELECT from_me, kind, body, status, deleted_at, author_name FROM hiring_wa_messages m
               WHERE m.chat_key = p.key ORDER BY m.sent_at DESC, m.id DESC LIMIT 1
          ) lm ON TRUE
         ORDER BY (p.waiting > 0) DESC, p.last_at DESC, p.key
    """, params + [int(limit), int(offset)])
    return _all(cursor)


def wa_chat_totals(cursor, scope_sql, scope_params, *, lead, user_id, mode='all', search='', phone_own_only=False,
                   stages=()):
    """Сколько чатов в выборке и сколько из них ждут ответа (счётчик «Ждут ответа» и вкладки)."""
    where, params = _chat_list_where(scope_sql, scope_params, lead=lead, user_id=user_id, mode=mode,
                                     waiting_only=False, search=search, phone_own_only=phone_own_only, stages=stages)
    cursor.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE COALESCE(w.waiting, 0) > 0), "
                   "COALESCE(SUM(COALESCE(w.waiting, 0)), 0) " + _CHAT_LIST_FROM + " WHERE " + where, params)
    total, waiting_chats, waiting_messages = cursor.fetchone()
    return {'total': int(total or 0), 'waiting_chats': int(waiting_chats or 0),
            'waiting_messages': int(waiting_messages or 0)}


_THREAD_COLUMNS = ("id, chat_key, wa_id, from_me, sent_at, kind, body, media, ad, edited_at, deleted_at, author_id, "
                   "author_name, status, quote_wa_id")


def wa_thread(cursor, key, *, before_id=None, after_id=None, limit=60):
    """Лента чата, старые сверху: последние limit сообщений (на одно больше — признак «есть раньше»); before_id —
    страница раньше; after_id — новое после него (опрос открытого чата)."""
    if after_id:
        cursor.execute("SELECT " + _THREAD_COLUMNS + " FROM hiring_wa_messages WHERE chat_key = %s AND id > %s "
                       "ORDER BY sent_at, id LIMIT %s", (key, int(after_id), int(limit) * 4))
    elif before_id:
        cursor.execute("SELECT " + _THREAD_COLUMNS + " FROM (SELECT * FROM hiring_wa_messages WHERE chat_key = %s "
                       "AND (sent_at, id) < (SELECT sent_at, id FROM hiring_wa_messages WHERE id = %s) "
                       "ORDER BY sent_at DESC, id DESC LIMIT %s) recent ORDER BY sent_at, id",
                       (key, int(before_id), int(limit) + 1))
    else:
        cursor.execute("SELECT " + _THREAD_COLUMNS + " FROM (SELECT * FROM hiring_wa_messages WHERE chat_key = %s "
                       "ORDER BY sent_at DESC, id DESC LIMIT %s) recent ORDER BY sent_at, id", (key, int(limit) + 1))
    rows = _all(cursor)
    for row in rows:
        _parse_json_fields(row, 'media', 'ad')
    return rows


def wa_messages_by_wa_ids(cursor, key, wa_ids):
    """Сообщения чата по id WhatsApp — цитаты ответов, которых нет на загруженной странице."""
    ids = [wa_id for wa_id in dict.fromkeys(wa_ids or ()) if wa_id]
    if not ids:
        return []
    cursor.execute("SELECT wa_id, from_me, kind, body, author_name, deleted_at IS NOT NULL AS deleted "
                   "FROM hiring_wa_messages WHERE chat_key = %s AND wa_id = ANY(%s)", (key, ids))
    return _all(cursor)


_OUTBOX_COLUMNS = ("id, client_id, chat_key, card_id, author_id, author_name, kind, body, quote_wa_id, media, status, "
                   "attempts, handed_at, wa_id, error, created_at, sent_at, updated_at, fail_reason")


def wa_outbox_insert(cursor, fields):
    """Сообщение в очередь отправки. Повтор того же client_id того же автора — та же строка, второй нет."""
    params = dict(fields)
    params['media'] = json.dumps(fields['media'], ensure_ascii=False) if fields.get('media') else None
    cursor.execute("""
        INSERT INTO hiring_wa_outbox (client_id, chat_key, card_id, author_id, author_name, kind, body, quote_wa_id,
                                      media, status, created_at, updated_at)
        VALUES (%(client_id)s, %(chat_key)s, %(card_id)s, %(author_id)s, %(author_name)s, %(kind)s, %(body)s,
                %(quote_wa_id)s, %(media)s::jsonb, 'queued', %(now)s, %(now)s)
        ON CONFLICT (author_id, client_id) WHERE client_id IS NOT NULL DO NOTHING
        RETURNING """ + _OUTBOX_COLUMNS, params)
    row = _one(cursor)
    fresh = row is not None
    if row is None and fields.get('client_id'):
        cursor.execute("SELECT " + _OUTBOX_COLUMNS + " FROM hiring_wa_outbox WHERE author_id = %s AND client_id = %s",
                       (int(fields['author_id']), fields['client_id']))
        row = _one(cursor)
    return _parse_json_fields(row, 'media'), fresh


def wa_outbox_get(cursor, outbox_id, lock=False):
    cursor.execute("SELECT " + _OUTBOX_COLUMNS + " FROM hiring_wa_outbox WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                   (int(outbox_id),))
    return _parse_json_fields(_one(cursor), 'media')


def wa_outbox_pending(cursor, key):
    """Ещё не легшие в переписку сообщения чата из портала: в очереди, у шлюза, не отправленные."""
    cursor.execute("SELECT " + _OUTBOX_COLUMNS + " FROM hiring_wa_outbox WHERE chat_key = %s "
                   "AND status NOT IN ('sent', 'discarded') ORDER BY id", (key,))
    rows = _all(cursor)
    for row in rows:
        _parse_json_fields(row, 'media')
    return rows


def wa_outbox_update(cursor, outbox_id, **fields):
    allowed = ('status', 'attempts', 'handed_at', 'wa_id', 'error', 'sent_at', 'updated_at', 'card_id', 'created_at',
               'fail_reason', 'client_id')
    return _update('hiring_wa_outbox', outbox_id, fields, allowed=allowed, cursor=cursor)


def wa_outbox_clone(cursor, row, now):
    """«Повторить» после отказа, о котором сообщил шлюз: он этот номер уже закрыл и повтор с ним отбросил бы —
    новая строка с тем же содержимым и файлом, номер повтора переезжает к ней."""
    cursor.execute("""
        INSERT INTO hiring_wa_outbox (client_id, chat_key, card_id, author_id, author_name, kind, body, quote_wa_id, media,
                                      status, created_at, updated_at)
        SELECT client_id, chat_key, card_id, author_id, author_name, kind, body, quote_wa_id, media, 'queued', %s, %s
          FROM hiring_wa_outbox WHERE id = %s
        RETURNING id
    """, (now, now, int(row['id'])))
    return int(cursor.fetchone()[0])


def wa_outbox_expire(cursor, created_before, handed_before, now, error):
    """Очередь, которую шлюз так и не забрал вовремя, и взятое шлюзом, срок которого прошёл (шлюз позже срока не
    отправляет): «Не отправлено» с «Повторить»."""
    cursor.execute("""
        UPDATE hiring_wa_outbox SET status = 'failed', error = %s, updated_at = %s
         WHERE (status = 'queued' AND created_at < %s) OR (status = 'handed' AND created_at < %s)
    """, (error, now, created_before, handed_before))
    return cursor.rowcount


def wa_outbox_unconfirmed(cursor, handed_before, attempts, now, error):
    """Шлюз брал сообщение attempts раз и ни разу не ответил: «не подтверждено» (могло и уйти — повтор руками)."""
    cursor.execute("""
        UPDATE hiring_wa_outbox SET status = 'failed', error = %s, updated_at = %s
         WHERE status = 'handed' AND handed_at < %s AND attempts >= %s
    """, (error, now, handed_before, int(attempts)))
    return cursor.rowcount


def wa_outbox_due(cursor, handed_before, attempts, limit):
    """Что отдать шлюзу в этом опросе: новое из очереди и взятое шлюзом без ответа (повтор он отбросит сам по id).
    Строки — под блокировкой со SKIP LOCKED: два опроса подряд не выдают одно сообщение дважды за раз."""
    cursor.execute("""
        SELECT o.id, o.kind, o.body, o.quote_wa_id, o.media, o.created_at, w.jid, q.from_me AS quote_from_me,
               q.kind AS quote_kind, q.body AS quote_body
          FROM hiring_wa_outbox o
          JOIN hiring_wa_chats w ON w.key = o.chat_key
          LEFT JOIN hiring_wa_messages q ON q.chat_key = o.chat_key AND q.wa_id = o.quote_wa_id
         WHERE o.status = 'queued' OR (o.status = 'handed' AND o.handed_at < %s AND o.attempts < %s)
         ORDER BY o.id LIMIT %s
           FOR UPDATE OF o SKIP LOCKED
    """, (handed_before, int(attempts), int(limit)))
    rows = _all(cursor)
    for row in rows:
        _parse_json_fields(row, 'media')
    return rows


def wa_outbox_handed(cursor, ids, now):
    if not ids:
        return
    cursor.execute("UPDATE hiring_wa_outbox SET status = 'handed', attempts = attempts + 1, handed_at = %s, "
                   "updated_at = %s WHERE id = ANY(%s)", (now, now, [int(x) for x in ids]))


def wa_outbox_busy(cursor):
    """Есть что отправлять — шлюз опрашивает чаще."""
    cursor.execute("SELECT EXISTS (SELECT 1 FROM hiring_wa_outbox WHERE status IN ('queued', 'handed'))")
    return bool(cursor.fetchone()[0])


def wa_insert_outbound(cursor, key, wa_id, at, outbox, now):
    """Сообщение, отправленное из портала, — в переписку: с автором, отметкой «отправлено» и цитатой. Если оно
    уже пришло другим путём (эхо от телефона), автор и отметка дописываются к нему. → (id строки, вставлено ли)."""
    media = None
    if outbox.get('media'):
        media = {k: outbox['media'][k] for k in ('object', 'mime', 'size', 'file_name', 'stored_type', 'stored_size')
                 if outbox['media'].get(k) is not None}
    cursor.execute("""
        INSERT INTO hiring_wa_messages (chat_key, wa_id, from_me, sent_at, kind, body, media, author_id, author_name,
                                        status, quote_wa_id, created_at)
        VALUES (%s, %s, TRUE, %s, %s, %s, %s::jsonb, %s, %s, 'sent', %s, %s)
        ON CONFLICT (chat_key, wa_id) DO UPDATE SET
            author_id = EXCLUDED.author_id, author_name = EXCLUDED.author_name,
            status = COALESCE(hiring_wa_messages.status, EXCLUDED.status),
            quote_wa_id = COALESCE(hiring_wa_messages.quote_wa_id, EXCLUDED.quote_wa_id),
            media = COALESCE(hiring_wa_messages.media, EXCLUDED.media)
        RETURNING id, (xmax = 0) AS inserted
    """, (key, wa_id, at, outbox['kind'], outbox.get('body'), json.dumps(media, ensure_ascii=False) if media else None,
          outbox['author_id'], outbox.get('author_name'), outbox.get('quote_wa_id'), now))
    row = cursor.fetchone()
    return int(row[0]), bool(row[1])


def wa_apply_receipt(cursor, wa_ids, status):
    """Отметка доставки на наши сообщения — только вперёд (sent < delivered < read < played)."""
    if not wa_ids:
        return 0
    cursor.execute("""
        UPDATE hiring_wa_messages SET status = %(status)s
         WHERE wa_id = ANY(%(ids)s) AND from_me AND deleted_at IS NULL
           AND (CASE COALESCE(status, '') WHEN 'played' THEN 4 WHEN 'read' THEN 3 WHEN 'delivered' THEN 2
                                          WHEN 'sent' THEN 1 ELSE 0 END)
               < (CASE %(status)s WHEN 'played' THEN 4 WHEN 'read' THEN 3 WHEN 'delivered' THEN 2 ELSE 1 END)
    """, {'status': status, 'ids': list(wa_ids)})
    return cursor.rowcount


def wa_whatsapp_touched(cursor, card_id, day_start, day_end):
    """Касание WhatsApp по переписке (с телефона или из портала) за эти сутки уже есть: одно в день."""
    cursor.execute("""
        SELECT 1 FROM hiring_touches
         WHERE card_id = %s AND kind = 'whatsapp' AND meta->>'source' IN ('wa_phone', 'wa_portal')
           AND at >= %s AND at < %s
         LIMIT 1
    """, (int(card_id), day_start, day_end))
    return cursor.fetchone() is not None


def wa_templates(cursor):
    cursor.execute("SELECT id, title, body, created_by, created_by_name, created_at, updated_at FROM hiring_wa_templates "
                   "WHERE is_active ORDER BY lower(title), id")
    return _all(cursor)


def wa_template_insert(cursor, title, body, by, by_name, now):
    cursor.execute("""
        INSERT INTO hiring_wa_templates (title, body, created_by, created_by_name, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (title, body, by, by_name, now, now))
    return int(cursor.fetchone()[0])


def wa_template_update(cursor, template_id, title, body, now):
    cursor.execute("UPDATE hiring_wa_templates SET title = %s, body = %s, updated_at = %s WHERE id = %s AND is_active",
                   (title, body, now, int(template_id)))
    return cursor.rowcount


def wa_template_delete(cursor, template_id, now):
    cursor.execute("UPDATE hiring_wa_templates SET is_active = FALSE, updated_at = %s WHERE id = %s AND is_active",
                   (now, int(template_id)))
    return cursor.rowcount
