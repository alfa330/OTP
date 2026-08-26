"""План смен из графика iCore вместо расписания Workpace.

Отдел, у которого график ведётся у нас, не должен зависеть от того, что заведено
в Workpace: там план оказывался плоским («всем 09:00»), а дубли карточек давали
неявку человеку, который отметился на другой своей карточке. Поэтому для таких
отделов ПЛАН берётся из `work_shifts`, а ФАКТ по-прежнему из отметок терминала
Workpace — своего источника прихода/ухода у нас нет и не будет.

Сборка выдаёт записи в том же виде, в каком их отдаёт `timetablespan`, поэтому
`find_violations` и Excel-отчёт работают дальше без изменений: `inMark`/`outMark`
намеренно пустые, и отметки к смене привязывает та же ветка, что и раньше
доставала не привязанный Workpace приход.
"""

import logging
from datetime import date, datetime, timedelta

from group_late import config
from group_late.departments import (
    build_employee_department_lookup,
    employee_roster,
    normalize_text,
    resolve_department_name,
)

logger = logging.getLogger(__name__)

SCHEDULE_LABEL = "График iCore"


def plan_pairs() -> dict[str, str]:
    """{название отдела Workpace: код нашего отдела} — у кого план берём у себя."""
    return {
        str(name).strip(): str(code).strip().lower()
        for name, code in (config.ICORE_PLAN_DEPARTMENTS or {}).items()
        if str(name or '').strip() and str(code or '').strip()
    }


def _empty_diagnostics(pairs=None):
    return {
        'departments': sorted((pairs or {}).keys()),
        'spans': 0,
        'people_total': 0,
        'people_with_shifts': 0,
        'people_without_card': [],
        'unlinked': [],
        'night_spans': 0,
    }


def _merge_day_spans(shifts, people):
    """Смены одного человека за день → один интервал (самое раннее начало —
    самый поздний конец).

    Сливать обязательно, а не желательно: ключ дедупликации события — «сотрудник +
    дата + тип», поэтому две смены в одном дне дали бы два кандидата с одним
    ключом, и второй молча пропал бы при вставке. К тому же отметку человек ставит
    один раз за приход и один раз за уход, а не по разу на смену."""
    spans = {}
    for shift in shifts:
        person = people.get(shift['user_id'])
        if person is None:
            continue
        shift_date = shift['date']
        if isinstance(shift_date, datetime):
            shift_date = shift_date.date()
        start_dt = datetime.combine(shift_date, shift['start'])
        end_dt = datetime.combine(shift_date, shift['end'])
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)       # смена через полночь
        key = (shift['user_id'], shift_date)
        current = spans.get(key)
        if current is None:
            spans[key] = {'start': start_dt, 'end': end_dt, 'shifts': 1}
        else:
            current['start'] = min(current['start'], start_dt)
            current['end'] = max(current['end'], end_dt)
            current['shifts'] += 1
    return spans


def build_records(db, day, employees):
    """Записи плана за одну дату + диагностика. `employees` — состав из Workpace.

    Возвращает `([], диагностика)`, если отделов с планом из iCore нет или в
    графике за эту дату пусто: в первом случае ничего не подменяем, во втором
    честно нечего проверять."""
    pairs = plan_pairs()
    diagnostics = _empty_diagnostics(pairs)
    if not pairs:
        return [], diagnostics

    if isinstance(day, datetime):
        day = day.date()
    if not isinstance(day, date):
        raise ValueError("day must be a date")

    snapshot = db.glb_icore_plan_snapshot(pairs, day, day, employee_roster(employees or []))
    people = {person['user_id']: person for person in snapshot['people']}
    workpace_by_code = snapshot['departments']

    diagnostics['people_total'] = len(people)
    diagnostics['unlinked'] = snapshot['unlinked']
    diagnostics['people_without_card'] = [
        {'user_id': person['user_id'], 'name': person['name']}
        for person in people.values() if not person['workpace_ext_ids']
    ]

    spans = _merge_day_spans(snapshot['shifts'], people)
    records = []
    for (user_id, shift_date), span in sorted(spans.items(), key=lambda item: item[0]):
        person = people[user_id]
        ext_ids = person['workpace_ext_ids']
        if not ext_ids:
            # Отметок по человеку нам взять негде: карточки в Workpace нет.
            # Считать его отсутствующим нельзя — это была бы неявка каждый день.
            continue
        # Смена через полночь: конец не отдаём. Отметки опрос тянет за текущие
        # сутки, и «последний уход за день» для такой смены поймал бы уход с
        # ПРОШЛОЙ ночной смены — вышел бы ранний уход на сутки. Приход при этом
        # проверяется как обычно, а пропуск контроля ухода виден в диагностике.
        crosses_midnight = span['end'].date() != shift_date
        if crosses_midnight:
            diagnostics['night_spans'] += 1
        records.append({
            'employeeId': ext_ids[0],
            'employeeExternalId': ext_ids[0],
            # Все карточки человека сразу: у одного сотрудника их в Workpace бывает
            # несколько, и отметка может лежать на любой из них.
            'workpaceKeys': list(ext_ids),
            'employeeName': person['name'],
            'departmentName': workpace_by_code.get(person['department_code']),
            'scheduleName': SCHEDULE_LABEL,
            'date': shift_date.strftime('%Y-%m-%d'),
            'workTimeStart': span['start'].strftime('%Y-%m-%dT%H:%M:%S'),
            'workTimeEnd': (None if crosses_midnight
                            else span['end'].strftime('%Y-%m-%dT%H:%M:%S')),
            # Пустые намеренно: факт собирается из отметок терминала той же веткой
            # find_violations, которая и раньше доставала непривязанный приход.
            'inMark': None,
            'outMark': None,
            'lateIn': 0,
            'earlyOut': 0,
        })

    diagnostics['spans'] = len(records)
    diagnostics['people_with_shifts'] = len({record['employeeId'] for record in records})
    return records, diagnostics


def mark_alias_map(records) -> dict[str, str]:
    """{любая карточка Workpace: карточка, на которую записан план}.

    Нужна там, где отметки сшиваются со сменой по одному идентификатору (Excel-
    отчёт). У человека с двумя карточками план лежит на одной, а отметка — на
    другой, и без склейки он выглядит как не пришедший на работу."""
    alias = {}
    for record in records or []:
        primary = record.get('employeeId')
        if not primary:
            continue
        for ext_id in (record.get('workpaceKeys') or []):
            if ext_id:
                alias[str(ext_id)] = str(primary)
    return alias


def apply_to_records(db, records, employees, day, employee_lookup=None):
    """Заменить расписание Workpace планом из iCore у переключённых отделов.

    Именно заменить: если оставить обе версии, на одного человека придут два плана
    и он получит по два нарушения за день. Отделы, которых в паре нет, проходят
    насквозь — там Workpace остаётся единственным источником."""
    pairs = plan_pairs()
    if not pairs:
        return list(records or []), _empty_diagnostics(pairs)

    try:
        own_records, diagnostics = build_records(db, day, employees)
    except Exception:
        # План не собрался — оставляем расписание Workpace: контроль в этом отделе
        # будет неточным, но не исчезнет совсем.
        logger.exception("group_late: не удалось собрать план из iCore, остаёмся на Workpace")
        return list(records or []), _empty_diagnostics(pairs)

    lookup = employee_lookup or build_employee_department_lookup(employees or [])
    switched = {normalize_text(name) for name in pairs}
    kept = [
        record for record in (records or [])
        if normalize_text(resolve_department_name(record, lookup)) not in switched
    ]
    dropped = len(records or []) - len(kept)
    logger.info(
        "group_late: план из iCore — отделы %s, смен %d, снято записей Workpace %d, "
        "без карточки Workpace %d, карточек без нашего сотрудника %d, ночных смен %d",
        ', '.join(diagnostics['departments']), diagnostics['spans'], dropped,
        len(diagnostics['people_without_card']), len(diagnostics['unlinked']),
        diagnostics['night_spans'],
    )
    diagnostics['dropped_workpace_records'] = dropped
    return kept + own_records, diagnostics
