# -*- coding: utf-8 -*-
"""Журнал «Приема», «Табеля учета рабочего времени» и «АВР»: тексты записей. Без базы — проверяется тестами.

Значения личных данных (ИИН, документ, адреса) и суммы в журнал не пишутся — только что поменялось:
журнал читают те же, кто видит раздел, но выгрузка журнала не должна становиться выгрузкой паспортов.
Остальное — точно: «было → стало» (просьба владельца 10.11.2026 «в журнале сделай всё аккуратнее»).
"""

from decimal import Decimal

from . import rules

DATA_LABELS = {
    'iin': 'ИИН', 'doc_type': 'вид документа', 'doc_number': 'номер документа', 'doc_issuer': 'кем выдан',
    'doc_issued_on': 'дата выдачи', 'citizenship': 'гражданство', 'birth_place': 'место рождения',
    'registration_address': 'адрес регистрации', 'actual_address': 'фактический адрес', 'project': 'проект',
    'timesheet_group': 'отдел в табеле', 'schedule_pattern': 'график', 'day_hours': 'часов в день',
}
# Что показывается значением, а не только названием поля.
OPEN_FIELDS = ('project', 'timesheet_group', 'schedule_pattern', 'day_hours')
# Личные данные в подробностях записи — строкой «изменено», без значений. Документ — одной строкой.
HIDDEN_GROUPS = (
    ('ИИН', ('iin',)),
    ('Удостоверение личности', ('doc_type', 'doc_number', 'doc_issuer', 'doc_issued_on')),
    ('Гражданство', ('citizenship',)),
    ('Место рождения', ('birth_place',)),
    ('Адрес регистрации', ('registration_address',)),
    ('Фактический адрес', ('actual_address',)),
)


def _value(value):
    if value is None or value == '':
        return '—'
    if hasattr(value, 'strftime'):
        return rules.date_dots(value)
    if isinstance(value, Decimal):        # numeric из базы: 8.00 → «8», 7.50 → «7,5»
        value = float(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f'{value:g}'.replace('.', ',')
    return str(value)


def _plain(value):
    """Значение для «было → стало» в подробностях: пусто — пустая строка (экран пишет «—»)."""
    return '' if value is None or value == '' else _value(value)


def changed_data_fields(before, after):
    """[(поле, было, стало)] по присланным полям личных данных и табеля."""
    out = []
    for key, new in after.items():
        old = before.get(key)
        if (old or None) != (new or None) and str(old or '') != str(new or ''):
            out.append((key, old, new))
    return out


def person_summary(contract_text, contract_changed, data_changes, amount_changed=False):
    """«Оформление: ГПХ · ИП «…» · № 1/2026; изменены: ИИН, адрес регистрации, проект ЦО»."""
    parts = []
    if contract_changed and contract_text:
        parts.append(f'Оформление: {contract_text}')
    items = []
    for key, _old, new in data_changes:
        label = DATA_LABELS.get(key, key)
        items.append(f'{label} {_value(new)}' if key in OPEN_FIELDS else label)
    if amount_changed:
        items.append('сумма договора')
    if items:
        parts.append('изменены: ' + ', '.join(items))
    return '; '.join(parts)


CONTRACT_LABELS = (('employment_type', 'Вид оформления'), ('company', 'Компания'), ('number', 'Номер договора'),
                   ('signed_on', 'Дата договора'), ('job_title', 'Должность'))


def contract_changes(before, after):
    """Договор по полям: [{label, old, new}] только для того, что поменялось. before/after —
    {employment_type (подписью), company, number, signed_on, job_title}; договора не было — None."""
    out = []
    for key, label in CONTRACT_LABELS:
        old, new = _plain((before or {}).get(key)), _plain((after or {}).get(key))
        if old != new:
            out.append({'label': label, 'old': old, 'new': new})
    return out


def person_changes(data_changes, contract=(), amount_changed=False):
    """Подробности записи об оформлении: [{label, old, new}] либо {label, hidden: True} — для личных
    данных и суммы, чьи значения в журнал не пишутся. contract — contract_changes(...)."""
    out = list(contract or ())
    if amount_changed:
        out.append({'label': 'Сумма договора', 'hidden': True})
    changed = {key: (old, new) for key, old, new in data_changes}
    for label, keys in HIDDEN_GROUPS:
        if any(key in changed for key in keys):
            out.append({'label': label, 'hidden': True})
    for key in OPEN_FIELDS:
        if key in changed:
            old, new = changed[key]
            out.append({'label': DATA_LABELS[key].capitalize(), 'old': _plain(old), 'new': _plain(new)})
    return out


def status_summary(status, kind, start=None, end=None, closed_on=None, removed=False, previous=None):
    label = rules.status_label(previous if status == rules.STATUS_WORKING else status, kind)
    if status == rules.STATUS_WORKING:
        if removed:
            return f'Статус «{label}» снят'
        return f'Статус «{label}» закончен {rules.date_dots(closed_on)} — работает'
    if start and end:
        period = rules.date_dots(start) if start == end else f'{rules.date_dots(start)} – {rules.date_dots(end)}'
        return f'Статус «{label}» {period}'
    return f'Статус «{label}»'


def cell_summary(day, field, old, new, computed=None, reset=False):
    where = rules.date_dots(day) + (', под ячейкой' if field == 'minutes' else '')
    before = old if old not in (None, '') else (computed if computed not in (None, '') else '·')
    if reset:
        return f'{where}: «{before}» → расчёт'
    after = new if new not in (None, '') else 'пусто'
    return f'{where}: «{before}» → «{after}»'


def import_summary(file_name, sheet, changed, fields, companies_created, same=0, missing=0):
    """«Загружена таблица «Список.xlsx», лист « 2026»: изменено у 9 сотрудников (14 полей),
    без изменений — 40, нет в iCORE — 5, новых компаний — 2»."""
    text = f'Загружена таблица «{file_name or "без имени"}»'
    if sheet:
        text += f', лист «{sheet}»'
    parts = [f'изменено у {changed} {rules.plural_form(changed, ("сотрудника", "сотрудников", "сотрудников"))} '
             f'({fields} {rules.plural_form(fields, ("поле", "поля", "полей"))})']
    if same:
        parts.append(f'без изменений — {same}')
    if missing:
        parts.append(f'нет в iCORE — {missing}')
    if companies_created:
        parts.append(f'новых компаний — {len(companies_created)}')
    return text + ': ' + ', '.join(parts)
