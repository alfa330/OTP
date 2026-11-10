# -*- coding: utf-8 -*-
"""Сверка строки таблицы HR с тем, что уже записано в iCORE: что поменяется, а что уже совпадает.

Без базы — проверяется тестами. Сравниваются те же значения, что «Прием» показывает в списке
и карточке человека: проект (у кого он не задан — проект по отделу), отдел в табеле,
должность, вид оформления, компания, график и часы в день. Пустая ячейка таблицы ничего
не стирает: это «в файле не указано», а не «очистить».

Значение поля — пара (ключ сравнения, как показать). Ключ сглаживает то, что разницей не
считается: регистр и пробелы в должности, «ИП "Тестова"» и «ИП Тестова», «ТТ» и
«Taxi Technologies», 8 и 8.0 часов.
"""

from . import importer, rules

FIELDS = (
    ('project', 'Проект'),
    ('timesheet_group', 'Отдел в табеле'),
    ('job_title', 'Должность'),
    ('employment_type', 'Вид оформления'),
    ('company', 'Компания'),
    ('schedule_pattern', 'График'),
    ('day_hours', 'Часов в день'),
)
LABELS = dict(FIELDS)
DATA_FIELDS = ('project', 'timesheet_group', 'schedule_pattern', 'day_hours')
CONTRACT_FIELDS = ('job_title', 'employment_type', 'company')


def _text(value):
    return ' '.join(str(value or '').split())


def _fold(value):
    return _text(value).casefold().replace('ё', 'е')


def hours_value(value):
    number = rules.number_or_none(value)
    return float(number) if number and 1 <= number <= 24 else None


def hours_text(number):
    number = float(number)
    return str(int(number)) if number.is_integer() else str(number).replace('.', ',')


def _company_value(company, text=''):
    if company:
        return rules.company_key(company.get('name')), rules.company_display_name(company)
    text = _text(text)
    # Текст карточки — тем же ключом, что строка таблицы (с синонимами: «ТТ» — Taxi Technologies).
    return (importer.canonical_company(text)[1], text) if text else None


def incoming(row, *, mode, project_for_groups, company):
    """Что строка таблицы записала бы: {поле: (ключ, показ)}. company — строка справочника
    (найденная или та, что заведётся при записи: {'name', 'kind'}) либо None."""
    out = {}
    if mode == 'project':
        project = rules.clean_project(row.get('group'))
    else:
        group = _text(row.get('group'))[:120]
        if group:
            out['timesheet_group'] = (_fold(group), group)
        project = rules.clean_project(project_for_groups)
    if project:
        out['project'] = (project, project)
    job = _text(row.get('job_title'))[:200]
    if job:
        out['job_title'] = (_fold(job), job)
    kind = rules.clean_employment_type(row.get('employment_type'))
    if kind:
        out['employment_type'] = (kind, rules.EMPLOYMENT_SHORT[kind])
    value = _company_value(company)
    if value and value[0]:
        out['company'] = value
    schedule = rules.clean_schedule(row.get('schedule'))
    if schedule:
        out['schedule_pattern'] = (schedule, schedule)
    hours = hours_value(row.get('day_hours'))
    if hours:
        out['day_hours'] = (hours, hours_text(hours))
    return out


def current(person, company):
    """Что сейчас в iCORE — ровно так, как это показывает «Прием» (service.person_summary).
    company — компания договора или найденная по тексту карточки, иначе None."""
    out = {}
    project = person.get('project_value') or rules.project_of(person)
    if project:
        out['project'] = (project, project)
    group = _text(person.get('timesheet_group'))
    if group:
        out['timesheet_group'] = (_fold(group), group)
    job = _text(person.get('contract_job_title') or person.get('job_title'))
    if job:
        out['job_title'] = (_fold(job), job)
    kind = rules.clean_employment_type(person.get('contract_employment_type') or person.get('employment_type'))
    if kind:
        out['employment_type'] = (kind, rules.EMPLOYMENT_SHORT[kind])
    value = _company_value(company, person.get('company_name'))
    if value and value[0]:
        out['company'] = value
    schedule = rules.clean_schedule(person.get('schedule_pattern'))
    if schedule:
        out['schedule_pattern'] = (schedule, schedule)
    hours = hours_value(person.get('day_hours'))
    if hours:
        out['day_hours'] = (hours, hours_text(hours))
    return out


def diff(now, wanted):
    """[{field, label, old, new}] — что поменяется, в порядке полей карточки. old '' — поле
    сейчас пустое (заполнится); поле, которого в таблице нет, не трогается."""
    out = []
    for field, label in FIELDS:
        new = wanted.get(field)
        if not new:
            continue
        old = now.get(field)
        if old and old[0] == new[0]:
            continue
        out.append({'field': field, 'label': label, 'old': old[1] if old else '', 'new': new[1]})
    return out


def line(values):
    """Короткая строка того, что есть: «ЦО · Менеджер · ГПХ · ИП «Тестова» · 5/2 · 8 ч»."""
    parts = []
    for field, _label in FIELDS:
        value = values.get(field)
        if value:
            parts.append(f'{value[1]} ч' if field == 'day_hours' else value[1])
    return ' · '.join(parts)


def incoming_company(row, index, create):
    """Компания строки таблицы: из справочника по ключу; если её там нет и HR оставил «завести
    в справочнике» — будущая запись {'name', 'kind', 'new': True}; иначе None."""
    name, key = importer.canonical_company(row.get('company'))
    if not key:
        return None
    found = index.get(key)
    if found:
        return found
    if not create:
        return None
    return {'name': name, 'kind': importer.company_kind_from_name(row.get('company') or ''), 'new': True, 'key': key}


def confirmed_changes(changes, confirm):
    """Из того, что поменялось бы сейчас, — только подтверждённое HR и только если «было»
    осталось тем, что он видел при сверке. Остальное — расхождения: (к записи, расхождения)."""
    confirm = confirm if isinstance(confirm, dict) else {}
    take, conflicts = [], []
    for change in changes:
        seen = confirm.get(change['field'])
        if seen is not None and _text(seen) == _text(change['old']):
            take.append(change)
        else:
            conflicts.append(change)
    return take, conflicts
