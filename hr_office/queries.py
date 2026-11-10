# -*- coding: utf-8 -*-
"""Запросы разделов «Прием», «Табель учета рабочего времени» и «АВР». Курсор приходит снаружи — транзакцией владеет ручка."""

import json
from collections import defaultdict

from . import rules

COMPANY_FIELDS = ('id', 'name', 'kind', 'bin', 'legal_address', 'contacts', 'bank_name', 'iik', 'bik', 'kbe',
                  'in_person', 'signer_name', 'signer_position', 'city', 'is_active')
COMPANY_WRITE = COMPANY_FIELDS[1:]

DATA_FIELDS = ('iin', 'doc_type', 'doc_number', 'doc_issuer', 'doc_issued_on', 'citizenship', 'birth_place',
               'registration_address', 'actual_address', 'project', 'timesheet_group', 'schedule_pattern',
               'day_hours')

CONTRACT_FIELDS = ('id', 'company_id', 'employment_type', 'number', 'signed_on', 'liability_number', 'amount',
                   'job_title', 'updated_at')


def _rows(cursor, fields):
    return [dict(zip(fields, row)) for row in cursor.fetchall()]


# ── Компании ────────────────────────────────────────────────────────────────

def list_companies(cursor, include_inactive=True):
    cursor.execute("SELECT %s FROM hr_companies %s ORDER BY is_active DESC, lower(name)"
                   % (', '.join(COMPANY_FIELDS), '' if include_inactive else 'WHERE is_active'))
    return _rows(cursor, COMPANY_FIELDS)


def get_company(cursor, company_id):
    cursor.execute("SELECT %s FROM hr_companies WHERE id = %%s" % ', '.join(COMPANY_FIELDS), (int(company_id),))
    rows = _rows(cursor, COMPANY_FIELDS)
    return rows[0] if rows else None


def save_company(cursor, company_id, fields, actor_id):
    values = [fields.get(column) for column in COMPANY_WRITE]
    if company_id:
        cursor.execute(
            "UPDATE hr_companies SET %s, updated_at = (now() AT TIME ZONE 'Asia/Almaty'), updated_by = %%s "
            "WHERE id = %%s RETURNING id" % ', '.join('%s = %%s' % column for column in COMPANY_WRITE),
            values + [actor_id, int(company_id)])
    else:
        cursor.execute(
            "INSERT INTO hr_companies (%s, created_by, updated_by) VALUES (%s, %%s, %%s) RETURNING id"
            % (', '.join(COMPANY_WRITE), ', '.join(['%s'] * len(COMPANY_WRITE))),
            values + [actor_id, actor_id])
    row = cursor.fetchone()
    return int(row[0]) if row else None


def company_name_taken(cursor, name, exclude_id=None):
    cursor.execute("SELECT 1 FROM hr_companies WHERE lower(name) = lower(%s) AND id <> %s",
                   (name, int(exclude_id or 0)))
    return cursor.fetchone() is not None


def company_index(companies):
    """{ключ названия: компания} — для старых записей «ТОО/ИП» текстом в карточке и таблиц HR."""
    index = {}
    for company in companies:
        key = rules.company_key(company.get('name'))
        if key and (key not in index or company.get('is_active')):
            index[key] = company
    return index


# ── Люди ────────────────────────────────────────────────────────────────────
PEOPLE_SQL = """
    SELECT u.id, btrim(u.name), lower(COALESCE(u.role, '')), lower(COALESCE(u.status, '')),
           u.department_id, d.code, d.name, u.hire_date, u.birth_date, u.gender,
           u.phone, u.email, u.telegram_nick, u.city, u.job_title, u.employment_type, u.company_name,
           u.study_place, u.study_specialty, u.study_completion_year,
           u.close_contact_1_relation, u.close_contact_1_full_name, u.close_contact_1_phone,
           u.close_contact_2_relation, u.close_contact_2_full_name, u.close_contact_2_phone,
           u.has_driver_license,
           e.iin, e.doc_type, e.doc_number, e.doc_issuer, e.doc_issued_on, e.citizenship, e.birth_place,
           e.registration_address, e.actual_address, e.project, e.timesheet_group, e.schedule_pattern,
           e.day_hours, e.updated_at, e.updated_by,
           c.id, c.company_id, c.employment_type, c.number, c.signed_on, c.liability_number, c.amount,
           c.job_title, c.updated_at,
           -- Дата увольнения — у уволенных: последний период «Увольнение». У работающего
           -- период мог остаться открытым по ошибке или прерваться возвратом — это не увольнение.
           CASE WHEN lower(COALESCE(u.status, '')) IN ('fired', 'dismissal') THEN
               (SELECT MAX(p.start_date) FROM operator_schedule_status_periods p
                 WHERE p.operator_id = u.id AND lower(p.status_code) = 'dismissal') END AS dismissal_from
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
      LEFT JOIN hr_employee_data e ON e.user_id = u.id
      LEFT JOIN hr_contracts c ON c.user_id = u.id AND c.is_current
"""

_PEOPLE_KEYS = (
    'user_id', 'name', 'role', 'status', 'department_id', 'department_code', 'department_name', 'hire_date',
    'birth_date', 'gender', 'phone', 'email', 'telegram_nick', 'city', 'job_title', 'employment_type',
    'company_name', 'study_place', 'study_specialty', 'study_completion_year',
    'close_contact_1_relation', 'close_contact_1_full_name', 'close_contact_1_phone',
    'close_contact_2_relation', 'close_contact_2_full_name', 'close_contact_2_phone', 'has_driver_license',
    'iin', 'doc_type', 'doc_number', 'doc_issuer', 'doc_issued_on', 'citizenship', 'birth_place',
    'registration_address', 'actual_address', 'project', 'timesheet_group', 'schedule_pattern', 'day_hours',
    'data_updated_at', 'data_updated_by',
    'contract_id', 'contract_company_id', 'contract_employment_type', 'contract_number', 'contract_signed_on',
    'contract_liability_number', 'contract_amount', 'contract_job_title', 'contract_updated_at',
    'dismissal_from',
)

# Служебные учётки в кадровых списках не нужны: у них нет ни договора, ни табеля.
SERVICE_ROLES = ('super_admin',)


def list_people(cursor, *, user_ids=None, include_fired=False, include_service=False):
    where, params = ["btrim(COALESCE(u.name, '')) <> ''"], []
    if user_ids is not None:
        ids = [int(value) for value in user_ids]
        if not ids:
            return []
        where.append('u.id = ANY(%s)')
        params.append(ids)
    if not include_fired:
        where.append("lower(COALESCE(u.status, '')) NOT IN ('fired', 'dismissal')")
    if not include_service:
        where.append("lower(COALESCE(u.role, '')) <> ALL(%s)")
        params.append(list(SERVICE_ROLES))
    cursor.execute(PEOPLE_SQL + ' WHERE ' + ' AND '.join(where) + ' ORDER BY lower(btrim(u.name)), u.id', params)
    out = []
    for row in cursor.fetchall():
        person = dict(zip(_PEOPLE_KEYS, row))
        person['project_value'] = rules.project_of(person)
        out.append(person)
    return out


def get_person(cursor, user_id, include_fired=True):
    rows = list_people(cursor, user_ids=[int(user_id)], include_fired=include_fired, include_service=True)
    return rows[0] if rows else None


def upsert_employee_data(cursor, user_id, fields, actor_id):
    """Пишет только присланные поля; остальные остаются как были."""
    columns = [column for column in DATA_FIELDS if column in fields]
    if not columns:
        return
    cursor.execute(
        "INSERT INTO hr_employee_data (user_id, %s, updated_by) VALUES (%%s, %s, %%s) "
        "ON CONFLICT (user_id) DO UPDATE SET %s, updated_at = (now() AT TIME ZONE 'Asia/Almaty'), "
        "updated_by = EXCLUDED.updated_by"
        % (', '.join(columns), ', '.join(['%s'] * len(columns)),
           ', '.join('%s = EXCLUDED.%s' % (column, column) for column in columns)),
        [int(user_id)] + [fields[column] for column in columns] + [actor_id])


def write_history(cursor, user_id, actor_id, field, old_value, new_value):
    cursor.execute(
        "INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value) "
        "VALUES (%s, %s, %s, %s, %s)", (int(user_id), actor_id, field, old_value, new_value))


# ── Договоры ────────────────────────────────────────────────────────────────

def save_contract(cursor, user_id, fields, actor_id):
    """Текущий договор человека. Новая компания или вид — новый договор (старый уходит в
    историю), иначе — правка текущего."""
    cursor.execute("SELECT id, company_id, employment_type FROM hr_contracts "
                   "WHERE user_id = %s AND is_current FOR UPDATE", (int(user_id),))
    current = cursor.fetchone()
    values = {key: fields.get(key) for key in ('company_id', 'employment_type', 'number', 'signed_on',
                                               'liability_number', 'amount', 'job_title')}
    if current and (current[1] == values['company_id'] and current[2] == values['employment_type']):
        cursor.execute(
            "UPDATE hr_contracts SET number = %s, signed_on = %s, liability_number = %s, amount = %s, "
            "job_title = %s, updated_at = (now() AT TIME ZONE 'Asia/Almaty'), updated_by = %s WHERE id = %s",
            (values['number'], values['signed_on'], values['liability_number'], values['amount'],
             values['job_title'], actor_id, current[0]))
        return int(current[0])
    if current:
        cursor.execute("UPDATE hr_contracts SET is_current = FALSE, updated_at = (now() AT TIME ZONE 'Asia/Almaty'), "
                       "updated_by = %s WHERE id = %s", (actor_id, current[0]))
    cursor.execute(
        "INSERT INTO hr_contracts (user_id, company_id, employment_type, number, signed_on, liability_number, "
        "amount, job_title, created_by, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (int(user_id), values['company_id'], values['employment_type'], values['number'], values['signed_on'],
         values['liability_number'], values['amount'], values['job_title'], actor_id, actor_id))
    return int(cursor.fetchone()[0])


def next_number(cursor, company_id, year, kind):
    """Следующий номер в справочнике компании: одна строка-счётчик, без гонки."""
    cursor.execute(
        "INSERT INTO hr_doc_counters (company_id, year, kind, last_value) VALUES (%s, %s, %s, 1) "
        "ON CONFLICT (company_id, year, kind) DO UPDATE SET last_value = hr_doc_counters.last_value + 1 "
        "RETURNING last_value", (int(company_id), int(year), kind))
    return int(cursor.fetchone()[0])


def peek_number(cursor, company_id, year, kind):
    cursor.execute("SELECT last_value FROM hr_doc_counters WHERE company_id = %s AND year = %s AND kind = %s",
                   (int(company_id), int(year), kind))
    row = cursor.fetchone()
    return (int(row[0]) if row else 0) + 1


# ── АВР ─────────────────────────────────────────────────────────────────────

def insert_act(cursor, *, user_id, contract_id, company_id, number, act_date, period_from, period_to, work_name,
               amount, actor_id):
    cursor.execute(
        "INSERT INTO hr_acts (user_id, contract_id, company_id, number, act_date, period_from, period_to, "
        "work_name, amount, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (int(user_id), contract_id, company_id, number, act_date, period_from, period_to, work_name, amount,
         actor_id))
    return int(cursor.fetchone()[0])


def existing_act_number(cursor, user_id, company_id, period_from, period_to):
    """Номер уже выданного АВР на того же человека, компанию и период — повтор не тратит номер."""
    cursor.execute(
        "SELECT number FROM hr_acts WHERE user_id = %s AND company_id IS NOT DISTINCT FROM %s "
        "AND period_from = %s AND period_to = %s ORDER BY id LIMIT 1",
        (int(user_id), company_id, period_from, period_to))
    row = cursor.fetchone()
    return row[0] if row else None


def last_acts(cursor, user_ids):
    """Последний АВР каждого человека — чтобы в списке было видно, за что акт уже есть."""
    if not user_ids:
        return {}
    cursor.execute(
        "SELECT DISTINCT ON (user_id) user_id, number, period_from, period_to, act_date, created_at "
        "FROM hr_acts WHERE user_id = ANY(%s) ORDER BY user_id, period_to DESC, id DESC",
        ([int(value) for value in user_ids],))
    return {int(row[0]): {'number': row[1], 'period_from': row[2], 'period_to': row[3], 'act_date': row[4],
                          'created_at': row[5]} for row in cursor.fetchall()}


# ── Табель ──────────────────────────────────────────────────────────────────

def status_periods(cursor, user_ids, start, end):
    if not user_ids:
        return {}
    cursor.execute(
        "SELECT operator_id, lower(status_code), start_date, end_date FROM operator_schedule_status_periods "
        "WHERE operator_id = ANY(%s) AND start_date <= %s AND COALESCE(end_date, DATE '9999-12-31') >= %s "
        "ORDER BY operator_id, start_date, id", (list(user_ids), end, start))
    out = defaultdict(list)
    for operator_id, code, period_start, period_end in cursor.fetchall():
        out[int(operator_id)].append((code, period_start, period_end))
    return dict(out)


def shifts(cursor, user_ids, start, end):
    if not user_ids:
        return []
    cursor.execute(
        "SELECT operator_id, shift_date, start_time, end_time FROM work_shifts "
        "WHERE operator_id = ANY(%s) AND shift_date BETWEEN %s AND %s ORDER BY operator_id, shift_date, start_time",
        (list(user_ids), start, end))
    return cursor.fetchall()


def days_off(cursor, user_ids, start, end):
    if not user_ids:
        return set()
    cursor.execute("SELECT operator_id, day_off_date FROM days_off WHERE operator_id = ANY(%s) "
                   "AND day_off_date BETWEEN %s AND %s", (list(user_ids), start, end))
    return {(int(row[0]), row[1]) for row in cursor.fetchall()}


def timesheet_edits(cursor, user_ids, start, end):
    if not user_ids:
        return {}
    cursor.execute(
        "SELECT user_id, day, value, minutes, edited_at, edited_by FROM hr_timesheet_edits "
        "WHERE user_id = ANY(%s) AND day BETWEEN %s AND %s", (list(user_ids), start, end))
    return {(int(row[0]), row[1]): {'value': row[2], 'minutes': row[3], 'edited_at': row[4], 'edited_by': row[5]}
            for row in cursor.fetchall()}


def timesheet_notes(cursor, user_ids, month_start):
    if not user_ids:
        return {}
    cursor.execute("SELECT user_id, note FROM hr_timesheet_notes WHERE user_id = ANY(%s) AND month = %s",
                   (list(user_ids), month_start))
    return {int(row[0]): row[1] for row in cursor.fetchall()}


def save_timesheet_edit(cursor, *, user_id, day, field, value, computed, actor_id):
    """field: 'value' (ячейка) или 'minutes' (строка под ней). Пусто — вернуть расчёт."""
    cursor.execute("SELECT value, minutes FROM hr_timesheet_edits WHERE user_id = %s AND day = %s FOR UPDATE",
                   (int(user_id), day))
    row = cursor.fetchone()
    old = (row[0] if field == 'value' else row[1]) if row else None
    result = old
    if row:
        cursor.execute("UPDATE hr_timesheet_edits SET %s = %%s, edited_by = %%s, "
                       "edited_at = (now() AT TIME ZONE 'Asia/Almaty') WHERE user_id = %%s AND day = %%s" % field,
                       (value, actor_id, int(user_id), day))
        cursor.execute("DELETE FROM hr_timesheet_edits WHERE user_id = %s AND day = %s "
                       "AND value IS NULL AND minutes IS NULL", (int(user_id), day))
    elif value is not None:
        cursor.execute("INSERT INTO hr_timesheet_edits (user_id, day, %s, edited_by) VALUES (%%s, %%s, %%s, %%s)"
                       % field, (int(user_id), day, value, actor_id))
    cursor.execute(
        "INSERT INTO hr_timesheet_edit_log (user_id, day, field, old_value, new_value, computed, edited_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (int(user_id), day, field, old, value, computed, actor_id))
    return result


def save_timesheet_note(cursor, *, user_id, month_start, note, actor_id):
    if note:
        cursor.execute(
            "INSERT INTO hr_timesheet_notes (user_id, month, note, edited_by) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (user_id, month) DO UPDATE SET note = EXCLUDED.note, edited_by = EXCLUDED.edited_by, "
            "edited_at = (now() AT TIME ZONE 'Asia/Almaty')", (int(user_id), month_start, note, actor_id))
    else:
        cursor.execute("DELETE FROM hr_timesheet_notes WHERE user_id = %s AND month = %s",
                       (int(user_id), month_start))


def get_closure(cursor, project, month_start):
    cursor.execute("SELECT snapshot, closed_by, closed_at FROM hr_timesheet_closures WHERE project = %s AND month = %s",
                   (project, month_start))
    row = cursor.fetchone()
    return {'snapshot': row[0], 'closed_by': row[1], 'closed_at': row[2]} if row else None


def save_closure(cursor, project, month_start, snapshot_json, actor_id):
    cursor.execute(
        "INSERT INTO hr_timesheet_closures (project, month, snapshot, closed_by) VALUES (%s, %s, %s::jsonb, %s) "
        "ON CONFLICT (project, month) DO UPDATE SET snapshot = EXCLUDED.snapshot, closed_by = EXCLUDED.closed_by, "
        "closed_at = (now() AT TIME ZONE 'Asia/Almaty')", (project, month_start, snapshot_json, actor_id))


def delete_closure(cursor, project, month_start):
    cursor.execute("DELETE FROM hr_timesheet_closures WHERE project = %s AND month = %s", (project, month_start))
    return cursor.rowcount


def user_names(cursor, user_ids):
    ids = [int(value) for value in user_ids or () if value]
    if not ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (ids,))
    return {int(row[0]): row[1] for row in cursor.fetchall()}


# ── «Отметки»: карточки Workpace/Clockster и ручные привязки ────────────────

def roster_cards(cursor):
    cursor.execute("SELECT ext_id, full_name, COALESCE(source, 'workpace'), department_name FROM glb_employees")
    return [{'ext_id': str(row[0]), 'full_name': row[1] or '', 'source': row[2], 'department': row[3]}
            for row in cursor.fetchall() if row[0]]


def manual_links(cursor):
    cursor.execute("SELECT workpace_ext_id, user_id FROM glb_employee_links")
    return {str(row[0]): (int(row[1]) if row[1] is not None else None) for row in cursor.fetchall()}


# ── Журнал: загрузки и правки в «Приеме», «Табеле учета рабочего времени» и «АВР» ──

def audit(cursor, *, section, action, summary, actor_id, user_id=None, details=None):
    cursor.execute(
        "INSERT INTO hr_audit_log (section, action, user_id, summary, details, actor_id) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
        (section, action, user_id, str(summary)[:600],
         json.dumps(details, ensure_ascii=False, default=str) if details is not None else None, actor_id))


def last_intake(cursor, user_id):
    """Кто и когда внёс данные для документов при создании учётки (ТЗ п. 2) — или None."""
    cursor.execute(
        "SELECT l.created_at, a.name FROM hr_audit_log l LEFT JOIN users a ON a.id = l.actor_id "
        "WHERE l.section = 'intake' AND l.action = 'intake' AND l.user_id = %s "
        "ORDER BY l.created_at DESC, l.id DESC LIMIT 1", (int(user_id),))
    row = cursor.fetchone()
    return {'at': row[0], 'actor_name': row[1] or ''} if row else None


def last_import(cursor, digest):
    """Когда, кто и с каким проектом (у табеля) последним загружал файл с этим отпечатком
    (sha256 содержимого) — или None."""
    if not digest:
        return None
    cursor.execute(
        "SELECT l.created_at, a.name, l.details->>'project' FROM hr_audit_log l "
        "LEFT JOIN users a ON a.id = l.actor_id "
        "WHERE l.section = 'intake' AND l.action = 'import' AND l.details->>'file_sha256' = %s "
        "ORDER BY l.created_at DESC, l.id DESC LIMIT 1", (str(digest),))
    row = cursor.fetchone()
    return {'at': row[0], 'actor_name': row[1] or '', 'project': row[2]} if row else None


AUDIT_FIELDS = ('id', 'action', 'user_id', 'user_name', 'summary', 'details', 'actor_id', 'actor_name', 'created_at')


def list_audit(cursor, *, section, actor_id=None, query='', limit=50, offset=0):
    """(записи, всего) журнала раздела, свежие первыми. actor_id — только записи этого человека
    (глава или СВ в «АВР» видит свои выгрузки)."""
    where, params = ['l.section = %s'], [section]
    if actor_id:
        where.append('l.actor_id = %s')
        params.append(int(actor_id))
    needle = ' '.join(str(query or '').split())
    if needle:
        where.append('(u.name ILIKE %s OR a.name ILIKE %s OR l.summary ILIKE %s)')
        params += ['%' + needle + '%'] * 3
    cursor.execute(
        "SELECT l.id, l.action, l.user_id, u.name, l.summary, l.details, l.actor_id, a.name, l.created_at, "
        "COUNT(*) OVER () FROM hr_audit_log l LEFT JOIN users u ON u.id = l.user_id "
        "LEFT JOIN users a ON a.id = l.actor_id WHERE " + ' AND '.join(where) +
        " ORDER BY l.created_at DESC, l.id DESC LIMIT %s OFFSET %s", params + [int(limit), int(offset)])
    rows = cursor.fetchall()
    total = int(rows[0][-1]) if rows else 0
    return [dict(zip(AUDIT_FIELDS, row[:-1])) for row in rows], total


# ── Шаблоны документов, загруженные HR ──────────────────────────────────────

def get_template(cursor, kind):
    cursor.execute("SELECT file_name, content, uploaded_by, uploaded_at FROM hr_templates WHERE kind = %s", (kind,))
    row = cursor.fetchone()
    if not row:
        return None
    return {'file_name': row[0], 'content': bytes(row[1]), 'uploaded_by': row[2], 'uploaded_at': row[3]}


def list_templates(cursor):
    cursor.execute("SELECT kind, file_name, uploaded_by, uploaded_at FROM hr_templates")
    return {row[0]: {'file_name': row[1], 'uploaded_by': row[2], 'uploaded_at': row[3]} for row in cursor.fetchall()}


def save_template(cursor, kind, file_name, content, actor_id):
    cursor.execute(
        "INSERT INTO hr_templates (kind, file_name, content, uploaded_by) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (kind) DO UPDATE SET file_name = EXCLUDED.file_name, content = EXCLUDED.content, "
        "uploaded_by = EXCLUDED.uploaded_by, uploaded_at = (now() AT TIME ZONE 'Asia/Almaty')",
        (kind, file_name, content, actor_id))


def delete_template(cursor, kind):
    cursor.execute("DELETE FROM hr_templates WHERE kind = %s", (kind,))
    return cursor.rowcount
