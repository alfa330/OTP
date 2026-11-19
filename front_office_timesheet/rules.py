# -*- coding: utf-8 -*-
"""Правила табеля фронт-офисов: статус дня, минуты, итоги, норма и график.

Только расчёт — без базы и сети, поэтому всё здесь проверяется тестами на
готовых входах. Минуты опоздания и раннего ухода считаются ТЕМИ ЖЕ функциями,
что и в разделе «Отметки» (group_late.attendance): определение одно, второго
не заводим.
"""

import calendar
import re
from collections import Counter
from datetime import date, datetime, timedelta

from group_late.attendance import early_minutes, late_minutes
from group_late.config import TZ
from group_late.helpers import parse_dt

# Обозначения дня — как в образце ТЗ: «р», «в», «Б/С», «О». Больничного и
# неявки в образце нет, у нас они есть в графике и в «Отметках».
CODE_WORK = 'р'
CODE_DAY_OFF = 'в'
CODE_BS = 'Б/С'
CODE_VACATION = 'О'
CODE_SICK = 'Б'
CODE_ABSENT = 'н'

# Период графика → обозначение. Увольнение обозначения не даёт: после даты
# увольнения ячейки пустые, как у образца до приёма на работу.
PERIOD_CODES = {
    'bs': CODE_BS,
    'annual_leave': CODE_VACATION,
    'sick_leave': CODE_SICK,
}
DISMISSAL = 'dismissal'

# «Кол-во пропущенных дней»: в образце это ячейки «Б/С»; неявка по смене без
# единой отметки — тоже пропущенный день. Отпуск и больничный — нет.
MISSED_CODES = (CODE_BS, CODE_ABSENT)

DEFAULT_JOB_TITLE = 'менеджер'
# Порядок городов как в образце: три больших города, дальше по алфавиту.
FIRST_CITIES = ('Алматы', 'Астана', 'Шымкент')
NO_CITY_LABEL = 'Город не указан'
NO_CITY_VALUE = '__none__'

WEEKDAY_SHORT = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')

MONTH_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')
DAY_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
MONTHS_NOMINATIVE = ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль',
                     'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь')
MONTHS_GENITIVE = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
                   'августа', 'сентября', 'октября', 'ноября', 'декабря')


# Годы, за которые табель вообще строится. Шире — не данные, а опечатка или
# подбор адреса: у 9999-12 «месяц от начала» и окно графика выходят за пределы
# date, и вместо внятного 400 был бы 500 (разбор 19.11.2026).
MIN_YEAR, MAX_YEAR = 2000, 2099


def parse_month(value):
    """'2026-10' → (первый день, последний день) или None."""
    match = MONTH_RE.match(str(value or '').strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def parse_day(value):
    """'2026-11-05' → date или None (в том числе за пределами MIN_YEAR…MAX_YEAR)."""
    match = DAY_RE.match(str(value or '').strip())
    if not match:
        return None
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return day if MIN_YEAR <= day.year <= MAX_YEAR else None


def month_end(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


# ── Период «с — по» ─────────────────────────────────────────────────────────
# Просьба владельца 19.11.2026: табель «Регионов» не только за месяц, а за
# период — от одного дня до месяца («месяц это максимум»). Месяц считается от
# дня начала: с 1-го числа — ровно этот месяц, с 15 октября — по 14 ноября.
# Двойник правила во фронте — periodMaxEnd в frontOfficeTimesheet.js (по нему
# календарь гасит дни, которые сервер не примет).

class PeriodError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


def period_max_end(start):
    """Последний день периода не длиннее месяца, который начинается в `start`:
    день перед тем же числом следующего месяца. Такого числа в следующем месяце
    нет (с 31 октября: в ноябре 30 дней) — его последний день."""
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    last = calendar.monthrange(year, month)[1]
    if start.day <= last:
        return date(year, month, start.day) - timedelta(days=1)
    return date(year, month, last)


def whole_month_of(start, end):
    """'ГГГГ-ММ', если период — ровно календарный месяц, иначе None."""
    if start.day == 1 and end == month_end(start):
        return f'{start.year}-{start.month:02d}'
    return None


def ru_date(day):
    return day.strftime('%d.%m.%Y')


def resolve_period(month=None, date_from=None, date_to=None):
    """Период табеля из запроса → {'start', 'end', 'month'}.

    Даты «с — по» — от одного дня до месяца; без дат — месяц `month`, как до
    периода (фронт шлёт месяц всегда: сервер без этой правки строит табель за
    месяц, а не падает). `month` в ответе — 'ГГГГ-ММ', если период ровно
    календарный месяц: так файл за месяц называется по-прежнему."""
    if str(date_from or '').strip() or str(date_to or '').strip():
        start, end = parse_day(date_from), parse_day(date_to)
        if not start or not end:
            raise PeriodError('Период нужен датами в виде ГГГГ-ММ-ДД', 'TIMESHEET_BAD_PERIOD')
        if end < start:
            raise PeriodError('Конец периода раньше начала', 'TIMESHEET_BAD_PERIOD')
        limit = period_max_end(start)
        if end > limit:
            raise PeriodError(
                'Период — не больше месяца: с %s самое позднее по %s' % (ru_date(start), ru_date(limit)),
                'TIMESHEET_PERIOD_TOO_LONG')
        return {'start': start, 'end': end, 'month': whole_month_of(start, end)}
    bounds = parse_month(month)
    if not bounds:
        raise PeriodError('Месяц нужен в виде ГГГГ-ММ', 'TIMESHEET_BAD_MONTH')
    return {'start': bounds[0], 'end': bounds[1], 'month': whole_month_of(*bounds)}


def period_title(start, end):
    """Подпись периода: «октябрь 2026», «5 ноября 2026», «1–15 ноября 2026»,
    «25 октября – 5 ноября 2026», через год — с годом у обеих дат."""
    if whole_month_of(start, end):
        return f'{MONTHS_NOMINATIVE[start.month - 1]} {start.year}'
    if start == end:
        return f'{start.day} {MONTHS_GENITIVE[start.month - 1]} {start.year}'
    if (start.year, start.month) == (end.year, end.month):
        return f'{start.day}–{end.day} {MONTHS_GENITIVE[end.month - 1]} {end.year}'
    if start.year == end.year:
        return (f'{start.day} {MONTHS_GENITIVE[start.month - 1]} – '
                f'{end.day} {MONTHS_GENITIVE[end.month - 1]} {end.year}')
    return (f'{start.day} {MONTHS_GENITIVE[start.month - 1]} {start.year} – '
            f'{end.day} {MONTHS_GENITIVE[end.month - 1]} {end.year}')


def period_file_label(start, end):
    """Часть имени файла: месяц — «2026-10», как было; период — «01.11.2026 — 15.11.2026»,
    один день — «05.11.2026». Двойник — timesheetFileName в frontOfficeTimesheet.js:
    Content-Disposition браузер фронту не отдаёт, имя собирается с двух сторон."""
    month = whole_month_of(start, end)
    if month:
        return month
    if start == end:
        return ru_date(start)
    return f'{ru_date(start)} — {ru_date(end)}'


def month_days(start, end):
    days, cursor = [], start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def last_fact_day(period_end, today):
    """Последний день, за который табель показывает факт: вчера (или конец периода).

    Сегодняшний день ещё идёт — отметки ухода нет почти ни у кого, а смены на
    будущие дни уже стоят в графике: без этой границы табель текущего месяца
    вышел бы полным неявок."""
    return min(period_end, today - timedelta(days=1))


def shift_span(shift_date, start_time, end_time):
    """Смена графика → (начало, конец) в поясе «Отметок». Конец не позже начала —
    смена через полночь."""
    start = datetime.combine(shift_date, start_time).replace(tzinfo=TZ)
    end = datetime.combine(shift_date, end_time).replace(tzinfo=TZ)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def merge_spans(spans):
    """Смены одного дня → один интервал: самое раннее начало, самый поздний конец
    (так же сводит их план «Отметок», icore_plan._merge_day_spans)."""
    spans = [span for span in spans if span]
    if not spans:
        return None
    return min(span[0] for span in spans), max(span[1] for span in spans)


def period_on(periods, day):
    """Вид периода графика на день. Периоды в графике не пересекаются, но если
    данные всё же наложились — берём начавшийся позже: он и есть фактический."""
    found = None
    for code, start, end in periods or ():
        if start <= day and (end is None or end >= day):
            if found is None or start >= found[1]:
                found = (code, start)
    return found[0] if found else None


def merge_facts(rows):
    """Строки «Отметок» одного человека за день → один факт.

    Строк бывает несколько: у человека две карточки Workpace, и в кэше за день
    лежит строка под каждой. Берём самый ранний приход и самый поздний уход по
    всем, отметки — все вместе. Складывать строки нельзя: минуты и дни
    удвоились бы."""
    ins, outs, marks = [], [], []
    for row in rows or ():
        fact_in = parse_dt(row.get('fact_in'))
        fact_out = parse_dt(row.get('fact_out'))
        if fact_in:
            ins.append(fact_in)
        if fact_out:
            outs.append(fact_out)
        for mark in row.get('marks') or ():
            at = parse_dt(mark.get('at'))
            if at:
                marks.append((at, 'in' if mark.get('kind') == 'in' else 'out'))
    if not ins and not outs:
        return None
    last = max(marks) if marks else None
    return {
        'fact_in': min(ins) if ins else None,
        'fact_out': max(outs) if outs else None,
        'last_at': last[0] if last else None,
        'last_kind': last[1] if last else None,
    }


def exit_confirmed(fact):
    """Уход подтверждён: отметка ухода есть, и после неё человек не возвращался.

    «Отметки» берут уходом последнюю отметку выхода. Вышел на обед, вернулся и
    вечером не отметился — последней отметкой дня стоит вход, а «уход» был
    обедом: ранний уход на полсмены. ТЗ п. 4 прямо запрещает считать такое без
    подтверждённых данных, поэтому в табеле это «нет отметки ухода»."""
    if not fact or not fact.get('fact_out'):
        return False
    last_at, last_kind = fact.get('last_at'), fact.get('last_kind')
    if last_kind == 'in' and last_at and last_at > fact['fact_out']:
        return False
    return True


def day_cell(day, *, fact_until, fact=None, span=None, period=None, day_off=False, has_card=False):
    """Ячейка дня: обозначение, минуты опоздания и раннего ухода, неполная отметка.

    Порядок решения:
      1. была отметка в «Отметках» — «р», день отработан (так в образце: итог
         «отработано» — это число «р», и отработанный выходной тоже «р»). Даже
         под периодом Б/С: отработанную смену период из графика не снимает;
      2. период графика — «Б/С», «О», «Б»; после увольнения — пусто;
      3. выходной из графика — «в»;
      4. прошедшая смена без единой отметки — «н», но только у того, у кого есть
         карточка Workpace: без карточки отметок взять негде, и неявка каждый
         день была бы неправдой;
      5. иначе пусто: до приёма, день без смены и без выходного, сегодня и
         будущее (факт показывается только до `fact_until`)."""
    cell = {'code': '', 'late': 0, 'early': 0, 'incomplete': False}
    if day <= fact_until and fact and (fact.get('fact_in') or fact.get('fact_out')):
        confirmed = exit_confirmed(fact)
        cell['code'] = CODE_WORK
        cell['incomplete'] = not (fact.get('fact_in') and confirmed)
        if span:
            start, end = span
            if fact.get('fact_in'):
                cell['late'] = late_minutes(start, fact['fact_in'])
            # Смена через полночь: уход на следующие сутки «Отметки» не проверяют.
            if confirmed and end.date() == day:
                cell['early'] = early_minutes(end, fact['fact_out'])
        return cell
    if period in PERIOD_CODES:
        cell['code'] = PERIOD_CODES[period]
        return cell
    if period == DISMISSAL:
        return cell
    if day_off:
        cell['code'] = CODE_DAY_OFF
        return cell
    if day <= fact_until and span and has_card:
        cell['code'] = CODE_ABSENT
    return cell


def person_totals(cells):
    return {
        'worked': sum(1 for cell in cells if cell['code'] == CODE_WORK),
        'missed': sum(1 for cell in cells if cell['code'] in MISSED_CODES),
        'late': sum(int(cell['late'] or 0) for cell in cells),
        'early': sum(int(cell['early'] or 0) for cell in cells),
    }


# Неделя, где смен меньше, — не ритм, а дыра в данных: график в ней не вели
# (так было в «Регионах» 21–30.09.2026) или человек пришёл/ушёл посреди недели.
MIN_SHIFTS_IN_FULL_WEEK = 3


def schedule_label(shift_days, window_start, window_end, holidays=(), excluded_days=()):
    """График вида «5.2» — сколько смен в обычной неделе.

    Поля «График» в iCore нет, есть смены по дням. Берём полные недели
    (пн–вс) окна — прошлый, выбранный месяц и две недели следующего — без
    дней отпуска, Б/С, больничного и увольнения, без дней до прихода в отдел и
    после ухода (`excluded_days`) и без праздников с понедельника по субботу
    (праздник в воскресенье смен недели не меняет); считаем смены в каждой.
    Нижняя медиана недель от трёх смен и есть график: на проде (октябрь 2026)
    глава ставит то 4, то 6 смен в неделю, и «самое частое число» у таких людей
    не находилось, хотя обычная неделя у них — пять смен. Нижняя — потому что
    график задаёт норму: недели 6, 4, 5, 6 дали бы «6.1» и норму 26 при 21 смене,
    то есть пять несуществующих пропусков. Нет ни одной такой недели — графика не
    выдумываем, пусто. Запись «5.2» — как в образце."""
    shift_days = set(shift_days or ())
    holidays = {day for day in (holidays or ()) if day.weekday() < 6}
    excluded = set(excluded_days or ())
    counts = []
    monday = window_start + timedelta(days=(7 - window_start.weekday()) % 7)
    while monday + timedelta(days=6) <= window_end:
        week = [monday + timedelta(days=offset) for offset in range(7)]
        monday += timedelta(days=7)
        if any(day in holidays or day in excluded for day in week):
            continue
        worked = sum(1 for day in week if day in shift_days)
        if worked >= MIN_SHIFTS_IN_FULL_WEEK:
            counts.append(worked)
    if not counts:
        return ''
    counts.sort()
    worked = counts[(len(counts) - 1) // 2]
    return f'{worked}.{7 - worked}'


def norm_days(label, days, holidays=(), shift_days_in_month=()):
    """«Кол-во рабочих дней план» — норма графика за месяц, как в образце.

    В образце у сотрудника 5.2 план 22 и при Б/С на весь месяц, и у пришедшего в
    середине месяца: это норма графика, а не число его смен. 5.2 — будни, 6.1 —
    понедельник–суббота, без праздников РК. Для остальных графиков нормы по
    календарю нет — число смен в графике за месяц."""
    holidays = set(holidays or ())
    if label == '5.2':
        return sum(1 for day in days if day.weekday() < 5 and day not in holidays)
    if label == '6.1':
        return sum(1 for day in days if day.weekday() < 6 and day not in holidays)
    return len(set(shift_days_in_month or ()))


def job_title_of(person):
    return (str(person.get('job_title') or '').strip()) or DEFAULT_JOB_TITLE


def city_of(person):
    return str(person.get('city') or '').strip()


def city_order_key(city):
    name = str(city or '').strip()
    if not name:
        return (2, 0, '')
    for index, first in enumerate(FIRST_CITIES):
        if name.casefold() == first.casefold():
            return (0, index, '')
    return (1, 0, name.casefold())


def person_order_key(person):
    job = job_title_of(person).casefold()
    return (0 if job == DEFAULT_JOB_TITLE else 1, job, str(person.get('name') or '').casefold())


def clean_filters(cities=None, user_ids=None, job_titles=None):
    """Отборы из запроса → множества. Пустой отбор — без ограничения."""
    def _texts(values):
        return {str(value).strip().casefold() for value in values or () if str(value or '').strip()}
    ids = set()
    for value in user_ids or ():
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return {'cities': _texts(cities), 'user_ids': ids, 'job_titles': _texts(job_titles)}


def matches_filters(person, filters):
    if not filters:
        return True
    cities = filters.get('cities') or set()
    if cities:
        city = city_of(person)
        key = city.casefold() if city else NO_CITY_VALUE.casefold()
        if key not in cities:
            return False
    user_ids = filters.get('user_ids') or set()
    if user_ids and int(person['user_id']) not in user_ids:
        return False
    jobs = filters.get('job_titles') or set()
    if jobs and job_title_of(person).casefold() not in jobs:
        return False
    return True


def membership_allows(envelope, day):
    """День внутри срока человека в отделе (первое начало — последний конец членства
    в группах отдела). Нет данных о членстве — не ограничиваем."""
    if not envelope:
        return True
    first, last = envelope
    if first and day < first:
        return False
    if last and day > last:
        return False
    return True


def build_model(inputs, filters=None):
    """Входы периода (data.collect) → модель листа для workbook.build.

    Период — от одного дня до месяца; дни листа — ровно дни периода, норма
    «кол-во рабочих дней план» — по дням периода, а график («5.2») — по тому же
    окну, что у табеля за месяц: у человека в отчёте за день и за месяц он один."""
    start, end = inputs['start'], inputs['end']
    days = month_days(start, end)
    fact_until = inputs['fact_until']
    holidays = set(inputs.get('holidays') or ())
    window_start = inputs.get('window_start') or start
    people = [person for person in inputs['people'] if matches_filters(person, filters)]

    rows = []
    for person in people:
        user_id = person['user_id']
        envelope = inputs.get('membership', {}).get(user_id)
        periods = inputs.get('periods', {}).get(user_id, ())
        cells = []
        for day in days:
            if not membership_allows(envelope, day):
                cells.append({'code': '', 'late': 0, 'early': 0, 'incomplete': False})
                continue
            fact = inputs.get('facts', {}).get((user_id, day))
            cell = day_cell(
                day,
                fact_until=fact_until,
                fact=fact,
                span=inputs.get('spans', {}).get((user_id, day)),
                period=period_on(periods, day),
                day_off=(user_id, day) in inputs.get('days_off', ()),
                has_card=user_id in inputs.get('has_card', ()),
            )
            if cell['code'] == CODE_WORK and fact:
                # Для подсказки ячейки на экране; в файл время не идёт. Уход —
                # только подтверждённый: «уход» на обед уходом дня не был.
                cell['arrive'] = _hhmm(fact.get('fact_in'))
                cell['leave'] = _hhmm(fact.get('fact_out')) if exit_confirmed(fact) else None
            cells.append(cell)
        shift_days = inputs.get('shift_days', {}).get(user_id, set())
        label_end = inputs.get('label_end') or end
        hired = person.get('hire_date')
        excluded = {
            day for day in month_days(window_start, label_end)
            if period_on(periods, day) in (*PERIOD_CODES, DISMISSAL)
            or not membership_allows(envelope, day)
            or (hired and day < hired)
        }
        label = schedule_label(shift_days, window_start, label_end, holidays, excluded)
        rows.append({
            'user_id': user_id,
            'name': person['name'],
            'active': bool(person.get('active', True)),
            'city': city_of(person),
            'job_title': job_title_of(person),
            'schedule': label,
            'plan': norm_days(label, days, holidays, {day for day in shift_days if start <= day <= end}),
            'cells': cells,
            'totals': person_totals(cells),
        })

    rows.sort(key=lambda row: (city_order_key(row['city']), person_order_key(row)))
    cities = []
    for row in rows:
        label = row['city'] or NO_CITY_LABEL
        if not cities or cities[-1]['city'] != label:
            cities.append({'city': label, 'people': []})
        cities[-1]['people'].append(row)
    return {
        'month': inputs.get('month'),
        'start': start,
        'end': end,
        'days': days,
        'holidays': holidays,
        'fact_until': fact_until,
        'cities': cities,
    }


def _hhmm(value):
    return value.astimezone(TZ).strftime('%H:%M') if value else None


def model_payload(model):
    """Модель листа → JSON для экрана «Учета часов»: те же люди, дни и итоги, что
    в файле, плюс признаки дня для шапки (выходной, праздник, ещё не наступил)."""
    holidays = set(model.get('holidays') or ())
    fact_until = model['fact_until']
    return {
        'month': model.get('month'),
        'date_from': model['start'].isoformat(),
        'date_to': model['end'].isoformat(),
        'title': period_title(model['start'], model['end']),
        'fact_until': fact_until.isoformat(),
        'days': [{
            'date': day.isoformat(),
            'day': day.day,
            'weekday': WEEKDAY_SHORT[day.weekday()],
            'weekend': day.weekday() >= 5,
            'holiday': day in holidays,
            'future': day > fact_until,
        } for day in model['days']],
        'cities': [{
            'city': city['city'],
            'people': [{
                'user_id': person['user_id'],
                'name': person['name'],
                'active': person.get('active', True),
                'job_title': person['job_title'],
                'schedule': person['schedule'],
                'plan': person['plan'],
                'cells': person['cells'],
                'totals': person['totals'],
            } for person in city['people']],
        } for city in model['cities']],
    }
