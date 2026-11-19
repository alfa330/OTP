# -*- coding: utf-8 -*-
"""Сбор периода для табеля: график из базы, факт — из кэша раздела «Отметки».

Период — месяц или даты «с — по» от одного дня до месяца (rules.resolve_period).
Состав — свой, за период: план «Отметок» (glb_icore_plan_snapshot) берёт только
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
from group_late.departments import clean_department_filters, department_matches

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


def scope(db):
    """Отдел фронт-офисов для экрана «Учета часов»: по его группам страница
    понимает, что выбраны «Регионы» и пора показывать табель."""
    with db._get_cursor() as cursor:
        department = front_office_department(cursor)
    return {'department_id': department[0] if department else None}


def _roster_candidates(cursor, department_id, start, end):
    """Кто мог быть в отделе в этом периоде: числится в отделе сейчас или состоял в
    его группе хоть день периода. Перевёрнутые членства (конец раньше начала —
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
    """У кого в периоде что-то есть: смена, выходной, период графика (кроме
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
    """Попадает ли человек в табель периода (месяца или дат «с — по»).

    Уволенный или переведённый — только если в периоде у него что-то есть
    (смена, выходной, период графика, отметка): членство в группе при увольнении
    не закрывают, и без этой проверки в табель октября 2026 попали четверо
    ушедших ещё в июле–начале октября, все строки пустые (сверка на проде
    06.11.2026). Работающий: состоял в группе отдела хоть день периода — да; был в
    группах отдела, но не в этом периоде — только если в периоде что-то есть;
    членства нет вовсе (группу не завели) — если принят не позже конца периода."""
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


def _cached_rows(db, start, end, department):
    """Строки «Отметок» периода только из кэша — без сборки дней из Workpace.

    Нужны спискам отбора: сшивка карточек по ФИО из строк плана и состав (кто
    отмечался) должны быть теми же, что у табеля, иначе в отборе не найти
    человека, который в табеле есть, а строка «Нет карточки Workpace» в окне
    называла бы того, кого табель над ним не называет (разбор 19.11.2026).
    Подразделение — тем же нестрогим сравнением, что у «Отметок»."""
    filters = clean_department_filters(department)
    rows = [attendance_cache.row_from_cache(record) for record in db.glb_read_attendance_rows(start, end)]
    if filters:
        rows = [row for row in rows if department_matches(row.get('department'), filters)]
    return rows


def resolve(period):
    """Период из `rules.resolve_period` или строка месяца 'ГГГГ-ММ' (так звали до периода)."""
    if isinstance(period, dict):
        return period
    try:
        return rules.resolve_period(month=period)
    except rules.PeriodError as exc:
        raise TimesheetError(exc.message, status=400, code=exc.code) from None


def collect(db, period, today, *, with_facts=True):
    """Всё для табеля за период — месяц или даты «с — по» (rules.resolve_period).
    `with_facts=False` — для списков отбора: строки «Отметок» только из кэша, без
    сборки недостающих дней из Workpace (окно открывается сразу)."""
    period = resolve(period)
    start, end = period['start'], period['end']
    # Окно графика «5.2»: прошлый месяц, месяц периода и две недели следующего —
    # у пришедшего в конце месяца иначе не нашлось бы ни одной полной недели.
    # Окно берётся по МЕСЯЦАМ периода, а не по его дням: у человека в табеле за
    # день, за неделю и за месяц один и тот же график. Период через границу
    # месяцев берёт оба месяца сразу (от прошлого месяца начала до двух недель
    # после месяца конца) — график тогда может отличаться от табеля одного из них.
    window_start = (start.replace(day=1) - timedelta(days=1)).replace(day=1)
    label_end = rules.month_end(end) + timedelta(days=13)
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
    if workpace_name and fact_until >= start:
        if with_facts:
            payload = attendance_cache.rows_for(db, start, fact_until, department=workpace_name)
            rows = payload.get('rows') or []
            pending_days = list(payload.get('pending_days') or [])
        else:
            rows = _cached_rows(db, start, fact_until, workpace_name)

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
        'month': period.get('month'),
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


def without_card_names(inputs, model):
    """Работающие без карточки Workpace — из тех, кто попал в модель (отбор)."""
    has_card = inputs.get('has_card') or set()
    shown = [person for city in model['cities'] for person in city['people']]
    return sorted(
        (person['name'] for person in shown if person.get('active') and person['user_id'] not in has_card),
        key=str.casefold,
    )


def options_payload(inputs):
    """Отборы табеля: города, люди и должности периода."""
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
        'month': inputs.get('month'),
        'date_from': inputs['start'].isoformat(),
        'date_to': inputs['end'].isoformat(),
        'cities': cities,
        'people': [
            {'id': person['user_id'], 'name': person['name'], 'city': rules.city_of(person),
             'job_title': rules.job_title_of(person), 'active': bool(person['active']),
             'card': person['user_id'] in inputs.get('has_card', ())}
            for person in people
        ],
        'job_titles': jobs,
        'without_card': inputs['without_card'],
    }
