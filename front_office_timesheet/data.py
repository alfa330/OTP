# -*- coding: utf-8 -*-
"""Сбор месяца для табеля: график из базы, факт — из кэша раздела «Отметки».

Состав — свой, за месяц: план «Отметок» (glb_icore_plan_snapshot) берёт только
работающих по ТЕКУЩЕМУ отделу, а табелю за прошлый месяц нужны и уволенные в нём,
и переведённые. Карточки Workpace сшиваются с людьми тем же правилом по ФИО, что
у «Отметок» (_glb_name_keys / _glb_resolve_entry), плюс ручные привязки
«Сопоставления» (glb_employee_links).
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from group_late import attendance_cache
from group_late import config as glb_config

from . import rules

logger = logging.getLogger(__name__)

FRONT_OFFICE_CODE = 'front_office'
ROSTER_ROLES = ('operator', 'trainee')
ICORE_PLAN_SCHEDULE = 'График iCore'


class TimesheetError(Exception):
    def __init__(self, message, status=400, code='TIMESHEET_ERROR'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def workpace_department_name():
    """Отдел Workpace, чей план ведётся у нас графиком фронт-офисов («Регионы»)."""
    for name, code in (glb_config.ICORE_PLAN_DEPARTMENTS or {}).items():
        if str(code or '').strip().lower() == FRONT_OFFICE_CODE and str(name or '').strip():
            return str(name).strip()
    return None


def kz_holidays():
    """Праздники РК — справочник проекта (resource_fte), второй копии не заводим."""
    try:
        from resource_fte.calendar_model import KZ_HOLIDAYS
    except Exception:  # noqa: BLE001 — без справочника норма просто без праздников
        logger.exception('Табель: справочник праздников недоступен')
        return set()
    out = set()
    for value in KZ_HOLIDAYS:
        try:
            out.add(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return out


def front_office_department(cursor):
    cursor.execute("""
        SELECT id, name FROM departments
        WHERE lower(code) = %s AND COALESCE(is_active, TRUE) = TRUE
        ORDER BY id LIMIT 1
    """, (FRONT_OFFICE_CODE,))
    row = cursor.fetchone()
    return (int(row[0]), row[1]) if row else None


def _roster_candidates(cursor, department_id, start, end):
    """Кто мог быть в отделе в этом месяце: числится в отделе сейчас или состоял в
    его группе хоть день месяца. Перевёрнутые членства (конец раньше начала —
    след перевода) не в счёт: это призраки, а не люди группы."""
    cursor.execute("""
        WITH dept_members AS (
            SELECT m.operator_id, m.start_date, m.end_date
            FROM group_operator_memberships m
            JOIN groups g ON g.id = m.group_id
            WHERE g.department_id = %(dept)s
              AND COALESCE(m.end_date, DATE '9999-12-31') >= m.start_date
        )
        SELECT u.id, btrim(u.name), btrim(COALESCE(u.city, '')), btrim(COALESCE(u.job_title, '')),
               lower(COALESCE(u.status, '')), u.department_id, u.hire_date,
               EXISTS (SELECT 1 FROM dept_members dm
                       WHERE dm.operator_id = u.id AND dm.start_date <= %(end)s
                         AND COALESCE(dm.end_date, DATE '9999-12-31') >= %(start)s) AS member_in_month,
               EXISTS (SELECT 1 FROM dept_members dm WHERE dm.operator_id = u.id) AS member_ever
        FROM users u
        WHERE lower(COALESCE(u.role, '')) = ANY(%(roles)s)
          AND btrim(COALESCE(u.name, '')) <> ''
          AND (u.department_id = %(dept)s
               OR EXISTS (SELECT 1 FROM dept_members dm
                          WHERE dm.operator_id = u.id AND dm.start_date <= %(end)s
                            AND COALESCE(dm.end_date, DATE '9999-12-31') >= %(start)s))
        ORDER BY u.name, u.id
    """, {'dept': department_id, 'start': start, 'end': end, 'roles': list(ROSTER_ROLES)})
    out = []
    for row in cursor.fetchall():
        out.append({
            'user_id': int(row[0]),
            'name': row[1],
            'city': row[2] or '',
            'job_title': row[3] or '',
            'active': row[4] != 'fired' and row[5] == department_id,
            'in_department': row[5] == department_id,
            'fired': row[4] == 'fired',
            'hire_date': row[6],
            'member_in_month': bool(row[7]),
            'member_ever': bool(row[8]),
        })
    return out


def _memberships(cursor, department_id, user_ids):
    """{user_id: (первое начало, последний конец или None)} по группам отдела."""
    if not user_ids:
        return {}
    cursor.execute("""
        SELECT m.operator_id, MIN(m.start_date),
               CASE WHEN bool_or(m.end_date IS NULL) THEN NULL ELSE MAX(m.end_date) END
        FROM group_operator_memberships m
        JOIN groups g ON g.id = m.group_id
        WHERE g.department_id = %s AND m.operator_id = ANY(%s)
          AND COALESCE(m.end_date, DATE '9999-12-31') >= m.start_date
        GROUP BY m.operator_id
    """, (department_id, list(user_ids)))
    return {int(row[0]): (row[1], row[2]) for row in cursor.fetchall()}


def _shifts(cursor, user_ids, start, end):
    if not user_ids:
        return []
    cursor.execute("""
        SELECT operator_id, shift_date, start_time, end_time
        FROM work_shifts
        WHERE operator_id = ANY(%s) AND shift_date BETWEEN %s AND %s
        ORDER BY operator_id, shift_date, start_time
    """, (list(user_ids), start, end))
    return cursor.fetchall()


def _days_off(cursor, user_ids, start, end):
    if not user_ids:
        return set()
    cursor.execute("""
        SELECT operator_id, day_off_date FROM days_off
        WHERE operator_id = ANY(%s) AND day_off_date BETWEEN %s AND %s
    """, (list(user_ids), start, end))
    return {(int(row[0]), row[1]) for row in cursor.fetchall()}


def _periods(cursor, user_ids, start, end):
    if not user_ids:
        return {}
    cursor.execute("""
        SELECT operator_id, lower(status_code), start_date, end_date
        FROM operator_schedule_status_periods
        WHERE operator_id = ANY(%s) AND start_date <= %s
          AND COALESCE(end_date, DATE '9999-12-31') >= %s
        ORDER BY operator_id, start_date, id
    """, (list(user_ids), end, start))
    out = defaultdict(list)
    for operator_id, code, period_start, period_end in cursor.fetchall():
        out[int(operator_id)].append((code, period_start, period_end))
    return dict(out)


def _workpace_cards(cursor, department_name):
    if not department_name:
        return []
    cursor.execute("""
        SELECT ext_id, full_name FROM glb_employees
        WHERE lower(COALESCE(department_name, '')) = lower(%s) AND source = 'workpace'
    """, (department_name,))
    return [{'ext_id': str(row[0]), 'full_name': row[1] or ''} for row in cursor.fetchall() if row[0]]


def _manual_links(cursor):
    cursor.execute("SELECT workpace_ext_id, user_id FROM glb_employee_links")
    return {str(row[0]): (int(row[1]) if row[1] is not None else None) for row in cursor.fetchall()}


def match_cards(people, cards, rows, links, *, name_keys, resolve, index_put):
    """{карточка Workpace: user_id} — чья это карточка.

    Правило то же, что у «Отметок»: ручная привязка «Сопоставления» сильнее всего
    (привязка к None — «не наш сотрудник»); иначе ФИО — полное, «Фамилия Имя»,
    фамилия с опечаткой в одну букву. Уволенные — «слабые» записи: за ключ ФИО с
    действующим тёзкой они не спорят.

    Имён у карточки бывает несколько: в строке с планом «Отметки» пишут наше ФИО
    из iCore, в строке «вне графика» и в справочнике — ФИО из Workpace. Наше
    пробуем первым: оно совпадает точно."""
    bucket = {'by_key': {}, 'by_short': {}}
    by_id = {}
    for person in sorted(people, key=lambda item: (not item.get('active'), item['name'], item['user_id'])):
        weak = not person.get('active')
        full_key, short_key = name_keys(person['name'])
        index_put(bucket['by_key'], full_key, person, weak=weak)
        index_put(bucket['by_short'], short_key, person, weak=weak)
        by_id[person['user_id']] = person

    names = defaultdict(list)
    for row in rows or ():
        if row.get('schedule') == ICORE_PLAN_SCHEDULE and row.get('employee_id'):
            names[str(row['employee_id'])].append(row.get('employee') or '')
    for card in cards or ():
        names[str(card['ext_id'])].append(card.get('full_name') or '')
    for row in rows or ():
        if row.get('employee_id'):
            names[str(row['employee_id'])].append(row.get('employee') or '')

    owners = {}
    for ext_id, card_names in names.items():
        if ext_id in links:
            user_id = links[ext_id]
            if user_id is not None and user_id in by_id:
                owners[ext_id] = user_id
            continue
        for name in dict.fromkeys(name for name in card_names if name and name != '—'):
            full_key, short_key = name_keys(name)
            person = resolve(bucket, full_key, short_key)
            if person is not None:
                owners[ext_id] = person['user_id']
                break
    return owners


def _active_user_ids(start, end, spans, days_off, periods, facts):
    """У кого в месяце что-то есть: смена, выходной, период графика (кроме
    увольнения), отметка."""
    active = {uid for uid, _day in spans}
    active.update(uid for uid, _day in days_off)
    active.update(uid for uid, _day in facts)
    for user_id, items in periods.items():
        if any(code != rules.DISMISSAL and period_start <= end and (period_end is None or period_end >= start)
               for code, period_start, period_end in items):
            active.add(user_id)
    return active


def _include(person, start, end, activity):
    """Попадает ли человек в табель месяца.

    Уволенный или переведённый — только если в месяце у него что-то есть
    (смена, выходной, период графика, отметка): членство в группе при увольнении
    не закрывают, и без этой проверки в табель октября 2026 попали четверо
    ушедших ещё в июле–начале октября, все строки пустые (сверка на проде
    06.11.2026). Работающий: состоял в группе отдела хоть день месяца — да; был в
    группах отдела, но не в этом месяце — только если в месяце что-то есть;
    членства нет вовсе (группу не завели) — если принят не позже конца месяца."""
    if not person['active']:
        return activity
    if person['member_in_month']:
        return True
    if person['member_ever']:
        return activity
    if activity:
        return True
    if not person['active']:
        return False
    hire = person.get('hire_date')
    return hire is None or hire <= end


def collect(db, month, today, *, with_facts=True):
    """Всё для табеля за месяц. `with_facts=False` — без строк «Отметок»: так
    быстро собирается состав для отборов окна выгрузки."""
    bounds = rules.parse_month(month)
    if not bounds:
        raise TimesheetError('Месяц нужен в виде ГГГГ-ММ', status=400, code='TIMESHEET_BAD_MONTH')
    start, end = bounds
    # Окно графика «5.2»: прошлый месяц, выбранный и две недели следующего — у
    # пришедшего в конце месяца иначе не нашлось бы ни одной полной недели.
    window_start = (start - timedelta(days=1)).replace(day=1)
    label_end = end + timedelta(days=13)
    fact_until = rules.last_fact_day(end, today)
    workpace_name = workpace_department_name()

    with db._get_cursor() as cursor:
        department = front_office_department(cursor)
        if not department:
            raise TimesheetError('Отдел «Фронт офисы» не найден', status=404, code='TIMESHEET_NO_DEPARTMENT')
        department_id = department[0]
        candidates = _roster_candidates(cursor, department_id, start, end)
        ids = [person['user_id'] for person in candidates]
        shift_rows = _shifts(cursor, ids, window_start, label_end)
        days_off = _days_off(cursor, ids, start, end)
        periods = _periods(cursor, ids, window_start, label_end)
        memberships = _memberships(cursor, department_id, ids)
        cards = _workpace_cards(cursor, workpace_name)
        links = _manual_links(cursor)

    spans_by_day = defaultdict(list)
    shift_days = defaultdict(set)
    for operator_id, shift_date, start_time, end_time in shift_rows:
        shift_days[int(operator_id)].add(shift_date)
        if start <= shift_date <= end:
            spans_by_day[(int(operator_id), shift_date)].append(
                rules.shift_span(shift_date, start_time, end_time))
    spans = {key: rules.merge_spans(value) for key, value in spans_by_day.items()}

    rows, pending_days = [], []
    if with_facts and workpace_name and fact_until >= start:
        payload = attendance_cache.rows_for(db, start, fact_until, department=workpace_name)
        rows = payload.get('rows') or []
        pending_days = list(payload.get('pending_days') or [])

    owners = match_cards(
        candidates, cards, rows, links,
        name_keys=db._glb_name_keys, resolve=db._glb_resolve_entry, index_put=db._glb_index_put,
    )
    rows_by_owner = defaultdict(list)
    for row in rows:
        owner = owners.get(str(row.get('employee_id') or ''))
        if owner is None:
            continue
        try:
            day = date.fromisoformat(str(row.get('date'))[:10])
        except ValueError:
            continue
        if start <= day <= fact_until:
            rows_by_owner[(owner, day)].append(row)
    facts = {}
    for key, day_rows in rows_by_owner.items():
        merged = rules.merge_facts(day_rows)
        if merged:
            facts[key] = merged

    active_ids = _active_user_ids(start, end, spans, days_off, periods, facts)
    people = [person for person in candidates
              if _include(person, start, end, person['user_id'] in active_ids)]
    included = {person['user_id'] for person in people}
    has_card = {user_id for user_id in owners.values() if user_id in included}

    return {
        'month': month,
        'start': start,
        'end': end,
        'window_start': window_start,
        'label_end': label_end,
        'today': today,
        'fact_until': fact_until,
        'department_id': department_id,
        'people': people,
        'spans': spans,
        'shift_days': {uid: days for uid, days in shift_days.items() if uid in included},
        'days_off': days_off,
        'periods': periods,
        'membership': memberships,
        'facts': facts,
        'has_card': has_card,
        'holidays': kz_holidays(),
        'pending_days': pending_days,
        'without_card': sorted(
            (person['name'] for person in people if person['active'] and person['user_id'] not in has_card),
            key=str.casefold,
        ),
    }


def options_payload(inputs):
    """Отборы окна выгрузки: города, люди и должности месяца."""
    people = sorted(inputs['people'], key=lambda person: (
        rules.city_order_key(rules.city_of(person)), rules.person_order_key(person)))
    cities, seen_cities = [], set()
    for person in people:
        city = rules.city_of(person)
        key = city.casefold() if city else rules.NO_CITY_VALUE
        if key in seen_cities:
            continue
        seen_cities.add(key)
        cities.append({'value': city or rules.NO_CITY_VALUE, 'label': city or rules.NO_CITY_LABEL})
    jobs, seen_jobs = [], set()
    for person in sorted(people, key=rules.person_order_key):
        job = rules.job_title_of(person)
        if job.casefold() in seen_jobs:
            continue
        seen_jobs.add(job.casefold())
        jobs.append(job)
    return {
        'month': inputs['month'],
        'cities': cities,
        'people': [
            {'id': person['user_id'], 'name': person['name'], 'city': rules.city_of(person),
             'job_title': rules.job_title_of(person), 'active': bool(person['active'])}
            for person in people
        ],
        'job_titles': jobs,
        'without_card': inputs['without_card'],
    }
