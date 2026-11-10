# -*- coding: utf-8 -*-
"""Табель учёта рабочего времени для HR (ТЗ «iCore 4», п. 6–10).

Ячейка дня — по ТЗ п. 8: «Работает» → плановые часы дня, выходной/праздник → «В»,
периоды графика → «О» (отпуск), «А» (без содержания / приостановление), «Б»
(больничный). План считается здесь, а не берётся из кэша «Отметок» (разбор
10.11.2026: в кэше нет будущих дней и планового обеда):

  1. смены «Графиков работы» iCore — у кого они есть в месяце;
  2. график 2/2 — плановые дни из «Отметок» (Workpace/Clockster);
  3. иначе — календарь 5/2 (или 6/1) с праздниками РК.

Из «Отметок» берётся только факт прошедших дней: минуты опоздания (под ячейкой,
со знаком минус, как в образце HR) и переработка (в комментарий). Ручная правка
HR главнее расчёта; закрытый месяц отдаётся из снимка и не пересчитывается.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import queries, rules

logger = logging.getLogger(__name__)


class TimesheetError(Exception):
    def __init__(self, message, status=400, code='HR_TIMESHEET_ERROR'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def kz_holidays():
    try:
        from front_office_timesheet.data import kz_holidays as _holidays
        return _holidays()
    except Exception:  # noqa: BLE001
        logger.exception('Табель: справочник праздников недоступен')
        return set()


def _lunch_hours(span_hours):
    try:
        from group_late.helpers import lunch_seconds
        return lunch_seconds(span_hours * 3600) / 3600.0
    except Exception:  # noqa: BLE001
        return 1.0 if span_hours >= 5 else 0.0


def _round_hours(value):
    """До получаса: 7.98 → 8, 7.5 → 7.5."""
    return round(value * 2) / 2


def _hours_text(value):
    value = _round_hours(value)
    return str(int(value)) if float(value).is_integer() else str(value).replace('.', ',')


def _span_hours(start_time, end_time):
    start = datetime.combine(date.today(), start_time)
    end = datetime.combine(date.today(), end_time)
    if end <= start:
        end += timedelta(days=1)
    span = (end - start).total_seconds() / 3600.0
    return max(0.0, span - _lunch_hours(span))


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


# ── Состав ──────────────────────────────────────────────────────────────────

def roster(people, project, start, end, user_ids=None):
    """Люди табеля: проект (поле человека, иначе по отделу), принят не позже конца
    месяца, уволен не раньше начала. Уволенный без даты увольнения не попадает."""
    wanted = set(int(value) for value in user_ids or ()) or None
    out = []
    for person in people:
        if wanted is not None and person['user_id'] not in wanted:
            continue
        if wanted is None and person['project_value'] != project:
            continue
        hired = person.get('hire_date')
        if hired and hired > end:
            continue
        dismissed = person.get('dismissal_from')
        fired = person.get('status') in ('fired', 'dismissal')
        if fired and (not dismissed or dismissed < start):
            continue
        if dismissed and dismissed < start:
            continue
        out.append(person)
    return out


# ── Факт из «Отметок» ───────────────────────────────────────────────────────

def match_rows_to_people(db, roster_ids, everyone, rows, cards, links):
    """{(user_id, день): [строки «Отметок»]} — правило сшивки то же, что у «Отметок» и
    табеля фронт-офисов: ручная привязка, затем ФИО (полное, «Фамилия Имя», опечатка).

    Кандидаты — ВСЯ компания, а не только люди проекта (разбор 10.11.2026): иначе тёзке из
    другого отдела не с кем столкнуться, ключ ФИО не гасится, и чужие опоздания достаются
    нашему человеку. Владельцы карточек берутся только из состава табеля."""
    from front_office_timesheet.data import match_cards
    candidates = [{'user_id': person['user_id'], 'name': person['name'],
                   'active': person.get('status') not in ('fired', 'dismissal')} for person in everyone]
    owners = match_cards(candidates, cards, rows, links, name_keys=db._glb_name_keys,
                         resolve=db._glb_resolve_entry, index_put=db._glb_index_put)
    owners = {ext: user_id for ext, user_id in owners.items() if user_id in roster_ids}
    by_person = defaultdict(list)
    for row in rows:
        owner = owners.get(str(row.get('employee_id') or ''))
        if owner is None:
            continue
        day = rules.parse_day(row.get('date'))
        if day:
            by_person[(owner, day)].append(row)
    return by_person, set(owners.values())


def day_fact(rows):
    """Строки одного человека за день → опоздание, переработка, есть ли отметки, план."""
    if not rows:
        return None
    late = max(int(row.get('late_minutes') or 0) for row in rows)
    over = max(int(row.get('overwork_minutes') or 0) for row in rows)
    present = any(row.get('fact_in') or row.get('fact_out') for row in rows)
    planned = [row for row in rows if row.get('plan_in') and row.get('plan_out')]
    plan_hours = None
    if planned:
        row = planned[0]
        if row.get('plan_mode') == 'hours' and row.get('hours_norm'):
            plan_hours = float(row['hours_norm'])
        else:
            start, end = _parse_dt(row.get('plan_in')), _parse_dt(row.get('plan_out'))
            if start and end and end > start:
                span = (end - start).total_seconds() / 3600.0
                plan_hours = max(0.0, span - _lunch_hours(span))
    elif any(row.get('plan_mode') == 'hours' and row.get('hours_norm') for row in rows):
        plan_hours = float(next(row['hours_norm'] for row in rows if row.get('hours_norm')))
    return {'late': late, 'over': over, 'present': present, 'planned': plan_hours is not None,
            'plan_hours': plan_hours}


# ── План ────────────────────────────────────────────────────────────────────

def person_plan(person, days, holidays, shift_rows, day_offs, facts, fact_until, periods=()):
    """{день: часы} — плановые рабочие дни человека в месяце и источник плана.

    Сверка с образцом HR за октябрь 2026: у неполного дня (4–5 ч), у личных выходных
    посреди недели и у почасовика («Специалист СБ», по 2 ч в разные дни) календарь
    5/2 по 8 ч расходился с табелем HR. Поэтому прошедшие дни человека, у которого в
    «Отметках» есть плановые смены, берутся из них; календарь — для будущих дней."""
    pattern = rules.clean_schedule(person.get('schedule_pattern'))
    day_hours = rules.number_or_none(person.get('day_hours'))
    if shift_rows:
        plan = {}
        for shift_date, start_time, end_time in shift_rows:
            hours = day_hours if day_hours else _span_hours(start_time, end_time)
            plan[shift_date] = max(plan.get(shift_date, 0.0), hours)
        for day in day_offs:
            plan.pop(day, None)
        return plan, 'icore', pattern or ''
    tracked = any(fact['planned'] for fact in facts.values())
    hired = person.get('hire_date')
    dismissals = dismissal_starts(person, periods)

    def excused(day):
        # Отпуск и до приема/после увольнения смен в «Отметках» нет, а «план дней»
        # у HR — норма месяца (образец: принятая 12.10 — план 21). Такие дни — по календарю.
        return _period_on(periods, day) in rules.PERIOD_CODES or not_employed(day, hired, dismissals)

    plan = {}
    from_otmetki = set()
    if tracked:
        for day in days:
            if day > fact_until or excused(day):
                continue
            from_otmetki.add(day)
            fact = facts.get(day)
            if fact and fact['planned']:
                plan[day] = day_hours or fact['plan_hours'] or rules.default_day_hours(pattern)
    calendar_pattern = pattern or '5/2'
    hours = day_hours or rules.default_day_hours(calendar_pattern)
    for day in days:
        if day in from_otmetki:
            continue
        if rules.calendar_workday(day, calendar_pattern, holidays):
            plan[day] = hours
    return plan, ('otmetki' if tracked else 'calendar'), (pattern or ('' if tracked else '5/2'))


def dismissal_starts(person, periods):
    """Начала увольнений, которые табель учитывает. Закрытый период (человека вернули) —
    всегда; открытый — только у уволенного: у работающего он остался по ошибке (на проде у
    действующего СВ с весны висит открытое «Увольнение»), и пустой табель был бы неправдой."""
    fired = person.get('status') in ('fired', 'dismissal')
    return [(start, end) for code, start, end in periods or ()
            if code == 'dismissal' and (end is not None or fired)]


def not_employed(day, hired, dismissals):
    """День вне работы: до приема или ПОСЛЕ даты увольнения. Сама дата увольнения — последний
    рабочий день (так в проекте: смена за этот день законна)."""
    if hired and day < hired:
        return True
    return any(start < day and (end is None or day <= end) for start, end in dismissals)


def _period_on(periods, day):
    found = None
    for code, start, end in periods or ():
        if start <= day and (end is None or end >= day):
            if found is None or start >= found[1]:
                found = (code, start)
    return found[0] if found else None


def build_person_row(person, days, *, holidays, until, fact_until, plan, plan_source, pattern, periods, facts,
                     edits, note, editor_names=None):
    hired = person.get('hire_date')
    dismissals = dismissal_starts(person, periods)
    cells = []
    over_total = 0
    for day in days:
        cell = {'v': '', 'm': None, 'cv': '', 'cm': None, 'edited': False, 'no_marks': False}
        employed = not not_employed(day, hired, dismissals)
        if employed and day <= until:
            period = _period_on(periods, day)
            fact = facts.get(day)
            if period == 'dismissal':
                period = None   # день увольнения — последний рабочий
            if period in rules.PERIOD_CODES:
                cell['cv'] = rules.PERIOD_CODES[period]
            elif day in plan:
                cell['cv'] = _hours_text(plan[day])
                if fact and day <= fact_until:
                    if fact['late'] > 0:
                        cell['cm'] = -int(fact['late'])
                    if not fact['present']:
                        cell['no_marks'] = True
                elif day <= fact_until and person.get('has_card'):
                    cell['no_marks'] = True
            else:
                cell['cv'] = rules.CODE_DAY_OFF
            if fact and day <= fact_until and fact['over'] > 0 and period not in rules.PERIOD_CODES:
                over_total += int(fact['over'])
        edit = edits.get(day)
        cell['v'] = cell['cv']
        cell['m'] = cell['cm']
        if edit and (edit.get('value') is not None or edit.get('minutes') is not None):
            # Кто и когда правил — видно в окне ячейки, полный след — в журнале табеля.
            cell['edited_by'] = (editor_names or {}).get(edit.get('edited_by')) or ''
            at = edit.get('edited_at')
            cell['edited_at'] = at.isoformat() if hasattr(at, 'isoformat') else at
        if edit:
            if edit.get('value') is not None:
                cell['v'] = edit['value']
                cell['edited'] = True
            if edit.get('minutes') is not None:
                minutes = edit['minutes']
                cell['m'] = int(minutes) if str(minutes).lstrip('+-').isdigit() else minutes
                cell['edited'] = True
        cells.append(cell)

    numbers = [rules.number_or_none(cell['v']) for cell in cells]
    plan_days = len(plan)
    plan_hours = sum(plan.values())
    auto_parts = []
    if hired and days[0] <= hired <= days[-1]:
        auto_parts.append(f'Прием {rules.date_dots(hired)}')
    for start, _end in dismissals:
        if days[0] <= start <= days[-1]:
            auto_parts.append(f'Увольнение {rules.date_dots(start)}')
    if over_total:
        auto_parts.append(f'Переработка {over_total} мин')
    return {
        'user_id': person['user_id'],
        'name': person['name'],
        'job_title': person.get('job_title_value') or '',
        'contract_label': person.get('contract_label') or '',
        'group': person.get('group_value') or '',
        'schedule': pattern,
        'plan_source': plan_source,
        'plan_days': plan_days,
        'plan_hours': _round_hours(plan_hours),
        'cells': cells,
        'totals': {
            'shifts': sum(1 for value in numbers if value is not None and value > 0),
            'hours': _round_hours(sum(value for value in numbers if value is not None)),
            'absences': sum(1 for cell in cells if cell['v'] in rules.ABSENCE_CODES),
            'minutes': sum(cell['m'] for cell in cells if isinstance(cell['m'], int)),
        },
        'overwork': over_total,
        'note': note if note is not None else '',
        'note_auto': '; '.join(auto_parts),
        'active': person.get('status') not in ('fired', 'dismissal'),
    }


def _contract_label(person, companies_by_id, company_index):
    kind = rules.clean_employment_type(person.get('contract_employment_type') or person.get('employment_type'))
    company = companies_by_id.get(person.get('contract_company_id'))
    if not company and person.get('company_name'):
        company = company_index.get(rules.company_key(person.get('company_name')))
    company_text = rules.company_display_name(company) if company else ' '.join(
        str(person.get('company_name') or '').split())
    parts = [rules.EMPLOYMENT_SHORT.get(kind, ''), company_text]
    return ' '.join(part for part in parts if part)


def collect(db, *, month, project, until=None, user_ids=None, today=None, with_facts=True):
    bounds = rules.parse_month(month)
    if not bounds:
        raise TimesheetError('Месяц нужен в виде ГГГГ-ММ', code='HR_TIMESHEET_BAD_MONTH')
    start, end = bounds
    today = today or date.today()
    until = until or end
    if until < start or until > end:
        raise TimesheetError('Дата «по» должна быть внутри месяца', code='HR_TIMESHEET_BAD_UNTIL')
    if project not in rules.PROJECTS and not user_ids:
        raise TimesheetError('Выберите проект', code='HR_TIMESHEET_BAD_PROJECT')
    fact_until = min(until, today - timedelta(days=1))
    days = rules.month_days(start, end)
    holidays = kz_holidays()

    with db._get_cursor() as cursor:
        everyone = queries.list_people(cursor, include_fired=True)
        people = roster(everyone, project, start, end, user_ids)
        ids = [person['user_id'] for person in people]
        companies = queries.list_companies(cursor)
        periods = queries.status_periods(cursor, ids, start, end)
        shift_rows = queries.shifts(cursor, ids, start, end)
        day_offs = queries.days_off(cursor, ids, start, end)
        edits = queries.timesheet_edits(cursor, ids, start, end)
        notes = queries.timesheet_notes(cursor, ids, start)
        cards = queries.roster_cards(cursor)
        links = queries.manual_links(cursor)
        editor_names = queries.user_names(cursor, {edit.get('edited_by') for edit in edits.values()})
    # Сбор дней «Отметок» ходит в Workpace/Clockster и бывает долгим — без открытого курсора:
    # иначе слот пула занят на всё время сборки (так же устроен табель фронт-офисов).
    rows, pending, facts_ok = [], [], True
    if with_facts and fact_until >= start and ids:
        try:
            from group_late import attendance_cache
            payload = attendance_cache.rows_for(db, start, fact_until)
            rows = payload.get('rows') or []
            pending = list(payload.get('pending_days') or [])
        except Exception:  # noqa: BLE001 — табель собирается, но закрыть его так нельзя
            logger.exception('Табель: строки «Отметок» не прочитались')
            facts_ok = False
    roster_ids = set(ids)
    facts_by_key, with_card = (match_rows_to_people(db, roster_ids, everyone, rows, cards, links)
                               if rows else ({}, set()))

    companies_by_id = {company['id']: company for company in companies}
    company_index = queries.company_index(companies)
    shifts_by_person = defaultdict(list)
    for operator_id, shift_date, start_time, end_time in shift_rows:
        shifts_by_person[int(operator_id)].append((shift_date, start_time, end_time))
    offs_by_person = defaultdict(set)
    for operator_id, day in day_offs:
        offs_by_person[operator_id].add(day)

    groups = defaultdict(list)
    for person in people:
        user_id = person['user_id']
        person['has_card'] = user_id in with_card
        person['job_title_value'] = (person.get('contract_job_title') or person.get('job_title')
                                     or rules.default_job_title(person.get('department_code')))
        person['contract_label'] = _contract_label(person, companies_by_id, company_index)
        person['group_value'] = person.get('timesheet_group') or person.get('department_name') or 'Без отдела'
        facts = {}
        for day in days:
            fact = day_fact(facts_by_key.get((user_id, day)))
            if fact:
                facts[day] = fact
        plan, source, pattern = person_plan(person, days, holidays, shifts_by_person.get(user_id),
                                            offs_by_person.get(user_id, set()), facts, fact_until,
                                            periods.get(user_id, ()))
        person_edits = {day: edit for (uid, day), edit in edits.items() if uid == user_id}
        row = build_person_row(person, days, holidays=holidays, until=until, fact_until=fact_until, plan=plan,
                               plan_source=source, pattern=pattern, periods=periods.get(user_id, ()),
                               facts=facts, edits=person_edits, note=notes.get(user_id), editor_names=editor_names)
        groups[person['group_value']].append(row)

    ordered = []
    for name in sorted(groups, key=lambda value: (value == 'Без отдела', value.casefold())):
        people_rows = sorted(groups[name], key=lambda row: row['name'].casefold())
        ordered.append({'name': name, 'people': people_rows})
    return {
        'month': month,
        'title': rules.month_title(start),
        'project': project if not user_ids else (project or ''),
        'until': until.isoformat(),
        'fact_until': fact_until.isoformat() if fact_until >= start else None,
        'days': [{'date': day.isoformat(), 'day': day.day, 'weekday': rules.WEEKDAYS_UPPER[day.weekday()],
                  'weekend': day.weekday() >= 5, 'holiday': day in holidays} for day in days],
        'groups': ordered,
        'pending_days': pending,
        'facts_ok': facts_ok,
        'closed': None,
    }


def model_json(model):
    return json.dumps(model, ensure_ascii=False, default=str)


# ── Excel по образцу HR ─────────────────────────────────────────────────────
_THIN = Side(style='thin', color='FFBFBFBF')
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_FILLS = {
    rules.CODE_UNPAID: PatternFill('solid', fgColor='FFFFFF00'),
    rules.CODE_LEAVE: PatternFill('solid', fgColor='FF92D050'),
    rules.CODE_SICK: PatternFill('solid', fgColor='FFF4B6B6'),
}
_WEEKEND_FILL = PatternFill('solid', fgColor='FFF2F2F2')
_EDIT_FONT = Font(name='Calibri', size=9, color='FF1F4E9E', bold=True)


def workbook(model):
    """Лист как у HR: на человека две строки (ячейки и минуты под ними), итоги — формулами."""
    wb = Workbook()
    ws = wb.active
    ws.title = (model.get('title') or 'Табель')[:31]
    days = model['days']
    first_day_col = 9
    last_day_col = first_day_col + len(days) - 1
    total_col = last_day_col + 1
    font = Font(name='Calibri', size=9)
    bold = Font(name='Calibri', size=9, bold=True)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    title = f"Табель учёта рабочего времени · {model.get('project') or ''} · {model.get('title') or ''}".strip(' ·')
    ws.cell(row=1, column=1, value=title).font = Font(name='Calibri', size=12, bold=True)

    head_row = 2
    headers = {1: 'Отдел', 2: '№', 3: 'Должность', 4: 'Ф.И.О должностного лица', 5: 'ТД/ГПХ', 6: 'график',
               7: 'кол-во рабочих дней план', 8: 'план часов'}
    for column, text in headers.items():
        cell = ws.cell(row=head_row + 1, column=column, value=text)
        cell.font, cell.alignment, cell.border = bold, center, _BORDER
        ws.merge_cells(start_row=head_row, start_column=column, end_row=head_row + 1, end_column=column)
        ws.cell(row=head_row, column=column).value = text
        ws.cell(row=head_row, column=column).font = bold
        ws.cell(row=head_row, column=column).alignment = center
    for offset, day in enumerate(days):
        column = first_day_col + offset
        top = ws.cell(row=head_row, column=column, value=day['day'])
        bottom = ws.cell(row=head_row + 1, column=column, value=day['weekday'])
        for cell in (top, bottom):
            cell.font, cell.alignment, cell.border = bold, center, _BORDER
            if day['weekend'] or day['holiday']:
                cell.fill = _WEEKEND_FILL
    totals = ['кол-во отработанных смен', 'КОЛИЧЕСТВО ОТРАБОТАННЫХ ЧАСОВ', 'кол-во пропусков/отпуск/бс/б',
              'итого минут опозданий/переработок', 'Комментарии, переработки']
    for offset, text in enumerate(totals):
        column = total_col + offset
        ws.merge_cells(start_row=head_row, start_column=column, end_row=head_row + 1, end_column=column)
        cell = ws.cell(row=head_row, column=column, value=text)
        cell.font, cell.alignment, cell.border = bold, center, _BORDER

    row = head_row + 2
    number = 0
    first_letter = get_column_letter(first_day_col)
    last_letter = get_column_letter(last_day_col)
    for group in model['groups']:
        group_start = row
        for person in group['people']:
            number += 1
            values = {2: number, 3: person['job_title'], 4: person['name'], 5: person['contract_label'],
                      6: person['schedule'], 7: person['plan_days'], 8: person['plan_hours']}
            for column, value in values.items():
                ws.merge_cells(start_row=row, start_column=column, end_row=row + 1, end_column=column)
                cell = ws.cell(row=row, column=column, value=value)
                cell.font = font
                cell.alignment = Alignment(horizontal='left' if column in (3, 4, 5) else 'center',
                                           vertical='center', wrap_text=True)
            for offset, cell_data in enumerate(person['cells']):
                column = first_day_col + offset
                value = cell_data['v']
                numeric = rules.number_or_none(value)
                top = ws.cell(row=row, column=column, value=numeric if numeric is not None and value != '' else
                              (value or None))
                top.font = _EDIT_FONT if cell_data.get('edited') else font
                top.alignment = center
                if value in _FILLS:
                    top.fill = _FILLS[value]
                elif days[offset]['weekend'] or days[offset]['holiday']:
                    top.fill = _WEEKEND_FILL
                minutes = cell_data.get('m')
                bottom = ws.cell(row=row + 1, column=column, value=minutes if minutes not in (None, '') else None)
                bottom.font = Font(name='Calibri', size=8, color='FFC00000' if isinstance(minutes, int)
                                   and minutes < 0 else 'FF404040')
                bottom.alignment = center
            r1, r2 = row, row + 1
            formulas = [
                f'=COUNT({first_letter}{r1}:{last_letter}{r1})',
                f'=SUM({first_letter}{r1}:{last_letter}{r1})',
                f'=COUNTIF({first_letter}{r1}:{last_letter}{r1},"О")+COUNTIF({first_letter}{r1}:{last_letter}{r1},"А")'
                f'+COUNTIF({first_letter}{r1}:{last_letter}{r1},"Б")',
                f'=SUM({first_letter}{r2}:{last_letter}{r2})',
            ]
            for offset, formula in enumerate(formulas):
                column = total_col + offset
                ws.merge_cells(start_row=r1, start_column=column, end_row=r2, end_column=column)
                cell = ws.cell(row=r1, column=column, value=formula)
                cell.font, cell.alignment = font, center
            note = person.get('note') or person.get('note_auto') or ''
            column = total_col + len(formulas)
            ws.merge_cells(start_row=r1, start_column=column, end_row=r2, end_column=column)
            cell = ws.cell(row=r1, column=column, value=note or None)
            cell.font = font
            cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
            for r in (r1, r2):
                for column in range(1, total_col + len(totals)):
                    ws.cell(row=r, column=column).border = _BORDER
            row += 2
        if row > group_start:
            ws.merge_cells(start_row=group_start, start_column=1, end_row=row - 1, end_column=1)
            cell = ws.cell(row=group_start, column=1, value=group['name'])
            cell.font = bold
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True, text_rotation=90)

    legend_row = row + 1
    ws.cell(row=legend_row, column=3, value='Обозначения:').font = bold
    for index, (code, text) in enumerate(rules.LEGEND):
        ws.cell(row=legend_row + 1 + index, column=3, value=code).font = bold
        ws.cell(row=legend_row + 1 + index, column=4, value=text).font = font
    ws.cell(row=legend_row + 1 + len(rules.LEGEND), column=3, value='−мин').font = bold
    ws.cell(row=legend_row + 1 + len(rules.LEGEND), column=4,
            value='Под ячейкой — минуты опоздания по «Отметкам»').font = font

    widths = {1: 6, 2: 4, 3: 22, 4: 26, 5: 18, 6: 6, 7: 8, 8: 7}
    for column, width in widths.items():
        ws.column_dimensions[get_column_letter(column)].width = width
    for column in range(first_day_col, last_day_col + 1):
        ws.column_dimensions[get_column_letter(column)].width = 4.2
    for offset, width in enumerate((8, 10, 9, 10, 24)):
        ws.column_dimensions[get_column_letter(total_col + offset)].width = width
    ws.freeze_panes = ws.cell(row=head_row + 2, column=first_day_col)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f'{head_row}:{head_row + 1}'
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def file_name(model):
    project = (model.get('project') or 'выбранные').replace('/', ' ')
    return f"Табель {project} {model.get('title') or ''}.xlsx".replace('  ', ' ')
