# -*- coding: utf-8 -*-
"""Логика разделов «Прием», «Табель учета рабочего времени» и «АВР» поверх запросов: доступ, карточка оформления, АВР, статусы, импорт.

Кто что видит (ТЗ п. 1, 6, 13; разбор 10.11.2026):
  * «Прием» и «Табель учета рабочего времени» — отдел кадров (членство в отделе hr) и суперадмины. Глобальные
    админы не входят: здесь ИИН, удостоверения и суммы договоров;
  * «АВР» — то же плюс главы отделов (на людей своих отделов) и супервайзеры (на
    операторов, стажёров и тренеров своего отдела).
"""

import hashlib
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import documents, importer, journal, queries, reconcile, rules

logger = logging.getLogger(__name__)


class HrError(Exception):
    def __init__(self, message, status=400, code='HR_ERROR', fields=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.fields = fields or {}


# ── Доступ ──────────────────────────────────────────────────────────────────

def capabilities(ctx):
    hr = bool(ctx.get('is_hr') or ctx.get('is_super'))
    acts = bool(hr or ctx.get('headed_department_ids') or ctx.get('is_supervisor'))
    statuses = bool(hr or ctx.get('is_global_admin') or ctx.get('headed_department_ids') or ctx.get('is_supervisor'))
    return {'hiring': hr, 'timesheet': hr, 'acts': acts, 'acts_all': hr, 'companies': hr, 'statuses': statuses}


def require(ctx, capability):
    if not capabilities(ctx).get(capability):
        raise HrError('Этот раздел вам не открыт', status=403, code='HR_FORBIDDEN')


SUPERVISOR_TARGET_ROLES = ('operator', 'trainee', 'trainer')
# Статус СВ ставит только линии — как в «Графиках работы» (_resolve_scoped_operator_for_requester).
STATUS_SUPERVISOR_ROLES = ('operator', 'trainee')


def in_acts_scope(ctx, person):
    if capabilities(ctx)['acts_all']:
        return True
    if person.get('role') == 'super_admin':
        return False
    headed = {int(value) for value in ctx.get('headed_department_ids') or ()}
    if person.get('department_id') in headed:
        return True
    if ctx.get('is_supervisor') and ctx.get('department_id') and person.get('department_id') == ctx['department_id']:
        return person.get('role') in SUPERVISOR_TARGET_ROLES
    return False


def in_status_scope(ctx, person):
    """Чей статус можно видеть и менять: та же граница, что у статусов в «Графиках работы».
    Отдел кадров, суперадмин и глобальный админ — всех; глава — своих отделов, кроме админов;
    СВ — операторов и стажёров своего отдела. Больничные — сведения о здоровье: чужим не видны."""
    if ctx.get('is_hr') or ctx.get('is_super') or ctx.get('is_global_admin'):
        return True
    role = person.get('role')
    if role == 'super_admin':
        return False
    headed = {int(value) for value in ctx.get('headed_department_ids') or ()}
    if person.get('department_id') in headed and role != 'admin':
        return True
    if ctx.get('is_supervisor') and ctx.get('department_id') and person.get('department_id') == ctx['department_id']:
        return role in STATUS_SUPERVISOR_ROLES
    return False


# ── Справочник компаний ─────────────────────────────────────────────────────

def company_payload(company):
    payload = {key: company.get(key) for key in queries.COMPANY_FIELDS}
    payload['display_name'] = rules.company_display_name(company)
    payload['id_label'] = rules.company_id_label(company.get('kind'))
    missing = [label for key, label in (('bin', 'ИИН/БИН'), ('legal_address', 'адрес'), ('bank_name', 'банк'),
                                        ('iik', 'счёт'), ('bik', 'БИК'), ('in_person', '«в лице»'),
                                        ('signer_name', 'подписант'))
               if not str(company.get(key) or '').strip()]
    payload['missing'] = missing
    return payload


COMPANY_NAME_MAX = 200     # hr_companies.name VARCHAR(200)


def clean_company(payload):
    errors = {}

    def text(key, limit, required=False):
        value = ' '.join(str(payload.get(key) or '').split())
        if required and not value:
            errors[key] = 'Обязательное поле'
        if len(value) > limit:
            errors[key] = f'Не длиннее {limit} знаков'
        return value or None

    name = importer.clean_company_name(payload.get('name'))
    fields = {
        'name': name or None,
        'kind': payload.get('kind') if payload.get('kind') in ('ip', 'too', 'other') else 'too',
        'bin': None, 'legal_address': text('legal_address', 400), 'contacts': text('contacts', 200),
        'bank_name': text('bank_name', 200), 'iik': None, 'bik': None, 'kbe': None,
        'in_person': text('in_person', 400), 'signer_name': text('signer_name', 120),
        'signer_position': text('signer_position', 120), 'city': text('city', 80),
        'is_active': payload.get('is_active') is not False,
    }
    if not name:
        errors['name'] = 'Укажите название'
    elif len(name) > COMPANY_NAME_MAX:
        errors['name'] = f'Не длиннее {COMPANY_NAME_MAX} знаков'
    bin_value = ''.join(ch for ch in str(payload.get('bin') or '') if ch.isdigit())
    if bin_value and len(bin_value) != 12:
        errors['bin'] = 'ИИН/БИН — 12 цифр'
    fields['bin'] = bin_value or None
    iik = ''.join(str(payload.get('iik') or '').split()).upper()
    if iik and not (iik.startswith('KZ') and len(iik) == 20):
        errors['iik'] = 'Счёт (ИИК) — KZ и 18 знаков'
    fields['iik'] = iik or None
    bik = ''.join(str(payload.get('bik') or '').split()).upper()
    if bik and not (8 <= len(bik) <= 11):
        errors['bik'] = 'БИК — 8 знаков'
    fields['bik'] = bik or None
    kbe = ''.join(ch for ch in str(payload.get('kbe') or '') if ch.isdigit())
    if kbe and len(kbe) != 2:
        errors['kbe'] = 'КБе — 2 цифры'
    fields['kbe'] = kbe or None
    if errors:
        raise HrError('Проверьте поля компании', fields=errors, code='HR_COMPANY_INVALID')
    return fields


# ── Люди ────────────────────────────────────────────────────────────────────

def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _company_for(person, companies_by_id, index):
    company = companies_by_id.get(person.get('contract_company_id'))
    if company:
        return company, True
    text = person.get('company_name')
    if text:
        # Тем же ключом, что строки таблиц HR: «ТТ» в карточке — это Taxi Technologies.
        found = index.get(importer.canonical_company(text)[1])
        if found:
            return found, False
    return None, False


def completeness(person, company):
    """Чего не хватает для документов — короткими словами для строки списка."""
    missing = []
    if not person.get('iin'):
        missing.append('ИИН')
    if not person.get('doc_number'):
        missing.append('удостоверение')
    if not person.get('registration_address'):
        missing.append('адрес')
    if not person.get('contract_id'):
        missing.append('договор')
    elif person.get('contract_amount') is None:
        missing.append('сумма')
    if not company:
        missing.append('компания')
    return missing


def _default_for(person, index):
    """Умолчание ТЗ п. 3 для того, у кого ещё нет ни договора, ни компании: (вид, компания из
    справочника или None, название, для кого) — либо None."""
    default = rules.default_hiring(person.get('department_code'))
    if not default or person.get('contract_id') or person.get('company_name'):
        return None
    kind, key = default
    company = index.get(key)
    name = rules.company_display_name(company) if company else key
    return kind, company, name, rules.DEFAULT_HIRING_FOR.get(str(person.get('department_code') or '').lower(), '')


def person_summary(person, companies_by_id, index):
    company, linked = _company_for(person, companies_by_id, index)
    kind = rules.clean_employment_type(person.get('contract_employment_type') or person.get('employment_type'))
    default = _default_for(person, index)
    return {
        'user_id': person['user_id'],
        'name': person['name'],
        'role': person.get('role'),
        'department_id': person.get('department_id'),
        'department': person.get('department_name') or '',
        'department_code': person.get('department_code') or '',
        'project': person['project_value'],
        'hire_date': _iso(person.get('hire_date')),
        'status': person.get('status'),
        'job_title': person.get('contract_job_title') or person.get('job_title') or '',
        'employment_type': kind,
        'company_id': company['id'] if company else None,
        'company': rules.company_display_name(company) if company else (person.get('company_name') or ''),
        'company_linked': linked,
        'contract_number': person.get('contract_number') or '',
        'contract_date': _iso(person.get('contract_signed_on')),
        'amount': float(person['contract_amount']) if person.get('contract_amount') is not None else None,
        'missing': completeness(person, company),
        'has_contract': bool(person.get('contract_id')),
        'default_hiring': {'employment_type': default[0], 'company': default[2], 'for': default[3]} if default else None,
    }


def list_people(cursor, ctx, *, query='', project='', department_id=None, scope='hiring'):
    people = queries.list_people(cursor)
    companies = queries.list_companies(cursor)
    by_id = {company['id']: company for company in companies}
    index = queries.company_index(companies)
    needle = importer.name_tokens(query)
    out = []
    for person in people:
        if scope == 'acts' and not in_acts_scope(ctx, person):
            continue
        if project and person['project_value'] != project:
            continue
        if department_id and person.get('department_id') != int(department_id):
            continue
        if needle:
            tokens = set(importer.name_tokens(person['name']))
            if not all(any(token.startswith(part) for token in tokens) for part in needle):
                continue
        out.append(person_summary(person, by_id, index))
    return out


def active_period(cursor, user_id, today):
    """(период на сегодня, ближайший будущий) — с id: «Вернуть «Работает»» удаляет
    будущий период или закрывает текущий вчерашним днём через ручку «Графиков работы»."""
    cursor.execute(
        "SELECT id, lower(status_code), start_date, end_date FROM operator_schedule_status_periods "
        "WHERE operator_id = %s AND start_date <= %s AND COALESCE(end_date, DATE '9999-12-31') >= %s "
        "ORDER BY start_date DESC, id DESC LIMIT 1", (int(user_id), today, today))
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "SELECT id, lower(status_code), start_date, end_date FROM operator_schedule_status_periods "
            "WHERE operator_id = %s AND start_date > %s AND lower(status_code) <> 'dismissal' "
            "ORDER BY start_date LIMIT 1", (int(user_id), today))
        upcoming = cursor.fetchone()
        return None, ({'id': upcoming[0], 'code': upcoming[1], 'start': _iso(upcoming[2]),
                       'end': _iso(upcoming[3])} if upcoming else None)
    return {'id': row[0], 'code': row[1], 'start': _iso(row[2]), 'end': _iso(row[3])}, None


def status_payload(cursor, person, today):
    kind = rules.clean_employment_type(person.get('contract_employment_type') or person.get('employment_type'))
    period, upcoming = active_period(cursor, person['user_id'], today)
    status = person.get('status') or 'working'
    if status == 'unpaid_leave':
        status = 'bs'
    current = period['code'] if period and period['code'] in rules.PERIOD_STATUSES else status
    if period:
        period['label'] = rules.status_label(period['code'], kind)
        # Увольнение правится в «Учете сотрудников» (причина, ЧС) — здесь только показываем.
        period['editable'] = period['code'] in rules.PERIOD_STATUSES
    if upcoming:
        upcoming['label'] = rules.status_label(upcoming['code'], kind)
        upcoming['editable'] = upcoming['code'] in rules.PERIOD_STATUSES
    return {
        'user_id': person['user_id'],
        'status': current,
        'label': rules.status_label(current, kind),
        'employment_type': kind,
        'period': period,
        'upcoming': upcoming,
        'options': rules.status_options(kind, current),
    }


def person_card(cursor, ctx, user_id, today):
    require(ctx, 'hiring')
    person = queries.get_person(cursor, user_id)
    if not person:
        raise HrError('Сотрудник не найден', status=404, code='HR_NOT_FOUND')
    companies = queries.list_companies(cursor)
    by_id = {company['id']: company for company in companies}
    index = queries.company_index(companies)
    company, linked = _company_for(person, by_id, index)
    summary = person_summary(person, by_id, index)
    default = _default_for(person, index)
    suggestion = {'employment_type': None, 'company_id': None}
    # Умолчание ТЗ п. 3 — только когда у человека ещё нет ни договора, ни компании.
    if default:
        suggestion['employment_type'] = default[0]
        suggestion['company_id'] = default[1]['id'] if default[1] and default[1].get('is_active') is not False else None
        suggestion['company_name'] = default[2]
        suggestion['for'] = default[3]
    intake = queries.last_intake(cursor, person['user_id'])
    updater = queries.user_names(cursor, [person['data_updated_by']]).get(person['data_updated_by']) \
        if person.get('data_updated_by') else None
    contract_company_id = (company or {}).get('id')
    year = (person.get('contract_signed_on') or today).year
    next_number = queries.peek_number(cursor, contract_company_id, year, 'contract') if contract_company_id else None
    return {
        **summary,
        'gender': person.get('gender'),
        'birth_date': _iso(person.get('birth_date')),
        'phone': person.get('phone') or '',
        'email': person.get('email') or '',
        'telegram_nick': person.get('telegram_nick') or '',
        'data': {
            'iin': person.get('iin') or '',
            'doc_type': person.get('doc_type') or 'id_card',
            'doc_number': person.get('doc_number') or '',
            'doc_issuer': person.get('doc_issuer') or '',
            'doc_issued_on': _iso(person.get('doc_issued_on')),
            'citizenship': person.get('citizenship') or '',
            'birth_place': person.get('birth_place') or '',
            'registration_address': person.get('registration_address') or '',
            'actual_address': person.get('actual_address') or '',
            'project': person.get('project') or '',
            'project_default': rules.default_project(person.get('department_code')),
            'timesheet_group': person.get('timesheet_group') or '',
            'schedule_pattern': person.get('schedule_pattern') or '',
            'day_hours': float(person['day_hours']) if person.get('day_hours') is not None else None,
        },
        'contract': {
            'id': person.get('contract_id'),
            'employment_type': rules.clean_employment_type(person.get('contract_employment_type')
                                                           or person.get('employment_type')),
            'company_id': contract_company_id,
            'number': person.get('contract_number') or '',
            'signed_on': _iso(person.get('contract_signed_on')),
            'liability_number': person.get('contract_liability_number') or '',
            'amount': float(person['contract_amount']) if person.get('contract_amount') is not None else None,
            'job_title': person.get('contract_job_title') or person.get('job_title')
                         or rules.default_job_title(person.get('department_code')),
            'next_number': rules.contract_number(next_number, year) if next_number else None,
        },
        'suggestion': suggestion,
        # ТЗ п. 2: данные для документов вносит тот, кто заводит учётку, — карточка говорит, кто и когда.
        'intake': {'at': _iso(intake['at']), 'by': intake['actor_name']} if intake else None,
        'data_updated': {'at': _iso(person.get('data_updated_at')), 'by': updater or ''}
        if person.get('data_updated_at') else None,
        'status': status_payload(cursor, person, today),
        'documents': [{'kind': kind, 'title': documents.DOC_TITLES[kind]}
                      for kind in documents.KINDS_BY_EMPLOYMENT.get(summary['employment_type'], ('questionnaire',))],
    }


def _text(payload, key, limit, errors):
    value = ' '.join(str(payload.get(key) or '').split())
    if len(value) > limit:
        errors[key] = f'Не длиннее {limit} знаков'
    return value or None


def clean_person(payload, person, today):
    """Тело «Сохранить» → (данные, договор, ошибки, предупреждения)."""
    errors, warnings = {}, []
    data_in = payload.get('data') or {}
    data = {}
    if 'iin' in data_in:
        iin, error, warning = rules.validate_iin(data_in.get('iin'), person.get('birth_date'), person.get('gender'))
        if error:
            errors['iin'] = error
        if warning:
            warnings.append(warning)
        data['iin'] = iin
    if 'doc_type' in data_in:
        data['doc_type'] = data_in.get('doc_type') if data_in.get('doc_type') in rules.DOC_TYPES else 'id_card'
    if 'doc_number' in data_in:
        number = ''.join(str(data_in.get('doc_number') or '').split())
        if number and not (5 <= len(number) <= 20):
            errors['doc_number'] = 'Номер документа — от 5 до 20 знаков'
        data['doc_number'] = number or None
    for key, limit in (('doc_issuer', 120), ('citizenship', 80), ('birth_place', 200),
                       ('registration_address', 500), ('actual_address', 500), ('timesheet_group', 120)):
        if key in data_in:
            data[key] = _text(data_in, key, limit, errors)
    if 'doc_issued_on' in data_in:
        raw = data_in.get('doc_issued_on')
        issued = rules.parse_day(raw) if raw else None
        if raw and not issued:
            errors['doc_issued_on'] = 'Дата в виде ГГГГ-ММ-ДД'
        elif issued and issued > today:
            errors['doc_issued_on'] = 'Дата выдачи не может быть в будущем'
        data['doc_issued_on'] = issued
    if 'project' in data_in:
        raw = str(data_in.get('project') or '').strip()
        project = rules.clean_project(raw)
        if raw and not project:
            errors['project'] = 'Проект из списка'
        data['project'] = project
    if 'schedule_pattern' in data_in:
        raw = str(data_in.get('schedule_pattern') or '').strip()
        pattern = rules.clean_schedule(raw)
        if raw and not pattern:
            errors['schedule_pattern'] = 'График — 5/2, 6/1 или 2/2'
        data['schedule_pattern'] = pattern
    if 'day_hours' in data_in:
        raw = data_in.get('day_hours')
        hours = rules.number_or_none(raw) if raw not in (None, '') else None
        if raw not in (None, '') and (hours is None or not 1 <= hours <= 24):
            errors['day_hours'] = 'Часов в дне — от 1 до 24'
        data['day_hours'] = hours

    contract = None
    contract_in = payload.get('contract')
    if contract_in is not None:
        contract = {}
        kind = rules.clean_employment_type(contract_in.get('employment_type'))
        if not kind:
            errors['employment_type'] = 'Выберите вид оформления'
        contract['employment_type'] = kind
        company_id = contract_in.get('company_id')
        try:
            contract['company_id'] = int(company_id) if company_id not in (None, '') else None
        except (TypeError, ValueError):
            contract['company_id'] = None
        if not contract['company_id']:
            errors['company_id'] = 'Выберите компанию'
        contract['number'] = _text(contract_in, 'number', 40, errors)
        contract['liability_number'] = _text(contract_in, 'liability_number', 40, errors)
        raw = contract_in.get('signed_on')
        signed = rules.parse_day(raw) if raw else None
        if raw and not signed:
            errors['signed_on'] = 'Дата в виде ГГГГ-ММ-ДД'
        contract['signed_on'] = signed
        amount, error = rules.clean_amount(contract_in.get('amount'))
        if error:
            errors['amount'] = error
        contract['amount'] = amount
        contract['job_title'] = _text(contract_in, 'job_title', 200, errors)
        if not contract['job_title']:
            errors['job_title'] = 'Укажите должность — она идёт в АВР'
        contract['auto_number'] = bool(contract_in.get('auto_number'))
    return data, contract, errors, warnings


def _contract_history(contract, company):
    if not contract:
        return None
    parts = [rules.EMPLOYMENT_SHORT.get(contract.get('employment_type'), ''),
             rules.company_display_name(company) if company else '',
             f"№ {contract['number']}" if contract.get('number') else '',
             f"от {rules.date_dots(contract['signed_on'])}" if contract.get('signed_on') else '',
             contract.get('job_title') or '']
    return ' · '.join(part for part in parts if part)


def _contract_fields(contract, company):
    """Договор для журнала — по полям, как их видит HR."""
    return {'employment_type': rules.EMPLOYMENT_SHORT.get(contract.get('employment_type'), ''),
            'company': rules.company_display_name(company) if company else '',
            'number': contract.get('number'), 'signed_on': contract.get('signed_on'),
            'job_title': contract.get('job_title')}


def save_person(cursor, ctx, user_id, payload, today):
    """Пишет личные данные и договор. Возвращает (поля users для db.update_user, предупреждения)."""
    require(ctx, 'hiring')
    person = queries.get_person(cursor, user_id)
    if not person:
        raise HrError('Сотрудник не найден', status=404, code='HR_NOT_FOUND')
    data, contract, errors, warnings = clean_person(payload, person, today)
    company = None
    if contract is not None and contract.get('company_id'):
        company = queries.get_company(cursor, contract['company_id'])
        if not company or not company.get('is_active'):
            errors['company_id'] = 'Компания не найдена в справочнике'
    if errors:
        raise HrError('Проверьте поля', fields=errors, code='HR_PERSON_INVALID')

    actor = ctx['user_id']
    # Пустой вид документа карточка показывает как «Удостоверение» и шлёт его обратно —
    # это не правка документа: ни в историю, ни в журнал.
    baseline = dict(person, doc_type=person.get('doc_type') or 'id_card')
    if data:
        queries.upsert_employee_data(cursor, user_id, data, actor)
        # ИИН в истории — только фактом правки, без цифр: «Историю» карточки читают и те, кому
        # «Прием» закрыт (глобальные админы), а последние 4 цифры вместе с датой рождения и полом
        # из той же карточки и контрольной цифрой почти всегда восстанавливают ИИН целиком.
        if 'iin' in data and (data['iin'] or None) != (person.get('iin') or None):
            queries.write_history(cursor, user_id, actor, 'hr_iin', None, 'изменён')
        if any(key in data and (data[key] or None) != (baseline.get(key) or None)
               for key in ('doc_type', 'doc_number', 'doc_issuer', 'doc_issued_on')):
            queries.write_history(cursor, user_id, actor, 'hr_document', None, 'изменено')
        if any(key in data and (data[key] or None) != (person.get(key) or None)
               for key in ('registration_address', 'actual_address')):
            queries.write_history(cursor, user_id, actor, 'hr_addresses', None, 'изменено')
        if 'project' in data and (data['project'] or None) != (person.get('project') or None):
            queries.write_history(cursor, user_id, actor, 'hr_project', person.get('project'), data['project'])

    user_fields = {}
    before = None
    before_fields = None
    amount_changed = False
    if contract is not None:
        if contract['auto_number'] and not contract.get('number'):
            year = (contract.get('signed_on') or today).year
            contract['number'] = rules.contract_number(
                queries.next_number(cursor, contract['company_id'], year, 'contract'), year)
            if not contract.get('signed_on'):
                contract['signed_on'] = today
        if not contract.get('liability_number') and contract.get('number'):
            contract['liability_number'] = f"{contract['number']}-МО"
        if person.get('contract_id'):
            before_company = queries.get_company(cursor, person['contract_company_id']) \
                if person.get('contract_company_id') else None
            previous = {'employment_type': person.get('contract_employment_type'),
                        'number': person.get('contract_number'),
                        'signed_on': person.get('contract_signed_on'),
                        'job_title': person.get('contract_job_title')}
            before = _contract_history(previous, before_company)
            before_fields = _contract_fields(previous, before_company)
        else:
            # Договора ещё нет — «было» то, что карточка и «Прием» уже показывали: вид, компания
            # и должность из «Учета сотрудников». Иначе журнал писал бы «— → ГПХ» про то, что не менялось.
            companies = queries.list_companies(cursor)
            shown, _linked = _company_for(person, {item['id']: item for item in companies},
                                          queries.company_index(companies))
            if not shown and person.get('company_name'):
                shown = {'name': person['company_name']}
            before_fields = _contract_fields({'employment_type': rules.clean_employment_type(person.get('employment_type')),
                                              'job_title': person.get('job_title')}, shown)
        queries.save_contract(cursor, user_id, contract, actor)
        after = _contract_history(contract, company)
        if after != before:
            queries.write_history(cursor, user_id, actor, 'hr_contract', before, after)
        amount_before = person.get('contract_amount')
        amount_changed = (contract.get('amount') is None) != (amount_before is None) or (
            amount_before is not None and contract.get('amount') is not None
            and float(amount_before) != float(contract['amount']))
        if amount_changed:
            queries.write_history(cursor, user_id, actor, 'hr_amount', None, 'изменена')
        # Карточка «Учета сотрудников» показывает вид, компанию и должность — держим их в лад.
        if contract['employment_type'] != (person.get('employment_type') or None):
            user_fields['employment_type'] = contract['employment_type']
        display = rules.company_display_name(company) if company else None
        if display and rules.company_key(display) != rules.company_key(person.get('company_name')):
            user_fields['company_name'] = display
        if contract['job_title'] and contract['job_title'] != (person.get('job_title') or ''):
            user_fields['job_title'] = contract['job_title']
    contract_text = _contract_history(contract, company) if contract is not None else None
    # В журнал — только то, что в договоре правда поменялось, по полям.
    contract_lines = journal.contract_changes(before_fields, _contract_fields(contract, company)) \
        if contract is not None else []
    data_changes = journal.changed_data_fields(baseline, data)
    summary = journal.person_summary(contract_text, bool(contract_lines), data_changes, amount_changed)
    if summary:
        queries.audit(cursor, section='intake', action='person', user_id=user_id, summary=summary, actor_id=actor,
                      details={'changes': journal.person_changes(data_changes, contract=contract_lines,
                                                                  amount_changed=amount_changed)})
    return user_fields, warnings


# ── Данные для документов при создании учётки (ТЗ п. 2) ─────────────────────
# «Пользователь уровня Супервайзер, Админ либо работник HR создаёт УЗ — вводит все необходимые
# данные; по ним система создаёт заполненные документы». Поэтому ИИН, документ и адреса
# принимает ручка создания учётки (bot_schedule2.add_user): проверка — ДО создания, запись —
# сразу после. Создающий эти данные потом не видит: их видит только отдел кадров в «Приеме»,
# в истории карточки — только факт, в журнале — какие поля, без значений.

INTAKE_KEYS = ('iin', 'doc_type', 'doc_number', 'doc_issuer', 'doc_issued_on', 'citizenship', 'birth_place',
               'registration_address', 'actual_address')
INTAKE_LABELS = {'iin': 'ИИН', 'doc_number': 'Номер документа', 'doc_issuer': 'Кем выдан',
                 'doc_issued_on': 'Дата выдачи', 'citizenship': 'Гражданство', 'birth_place': 'Место рождения',
                 'registration_address': 'Адрес регистрации', 'actual_address': 'Фактический адрес'}


def clean_intake(raw, *, birth_date=None, gender=None, today=None):
    """Данные для документов из формы создания учётки → (поля hr_employee_data, ошибки, предупреждения).
    Пустое не пишется: «не заполнили» — не «стереть». Один вид документа без номера — не данные."""
    raw = raw if isinstance(raw, dict) else {}
    given = {key: raw.get(key) for key in INTAKE_KEYS if str(raw.get(key) or '').strip()}
    if not set(given) - {'doc_type'}:
        return {}, {}, []
    if 'doc_type' not in given and any(key in given for key in ('doc_number', 'doc_issuer', 'doc_issued_on')):
        given['doc_type'] = 'id_card'
    # «Дата выдачи не в будущем» — по календарю Алматы, а не по часам сервера (UTC).
    data, _contract, errors, warnings = clean_person(
        {'data': given}, {'birth_date': birth_date, 'gender': gender},
        today or datetime.now(ZoneInfo('Asia/Almaty')).date())
    return {key: value for key, value in data.items() if value not in (None, '')}, errors, warnings


def intake_error_text(errors):
    """Ошибки полей одной строкой для окна создания учётки: «…: ИИН — …; Дата выдачи — …»."""
    return 'Проверьте «Документы для приема» на вкладке «Данные»: ' + '; '.join(
        f"{INTAKE_LABELS.get(key, key)} — {message}" for key, message in errors.items())


def save_intake(cursor, user_id, data, actor_id):
    """Пишет данные для документов новой учётки. Возвращает подписи внесённого (для журнала)."""
    if not data:
        return []
    queries.upsert_employee_data(cursor, user_id, data, actor_id)
    if data.get('iin'):
        queries.write_history(cursor, user_id, actor_id, 'hr_iin', None, 'изменён')
    if any(data.get(key) for key in ('doc_number', 'doc_issuer', 'doc_issued_on')):
        queries.write_history(cursor, user_id, actor_id, 'hr_document', None, 'изменено')
    if any(data.get(key) for key in ('registration_address', 'actual_address')):
        queries.write_history(cursor, user_id, actor_id, 'hr_addresses', None, 'изменено')
    # Новая учётка: поля не «изменены», а «внесены» — так и пишем в журнал (без значений).
    changes = [dict(change, note='внесено') for change in journal.person_changes(journal.changed_data_fields({}, data))]
    labels = [change['label'] for change in changes]
    queries.audit(cursor, section='intake', action='intake', user_id=user_id, actor_id=actor_id,
                  summary='Учётка создана, внесены данные для документов: '
                          + ', '.join(label if label == 'ИИН' else label.lower() for label in labels),
                  details={'changes': changes})
    return labels


# ── АВР ─────────────────────────────────────────────────────────────────────

def acts_people(cursor, ctx, *, project='', department_id=None, query=''):
    require(ctx, 'acts')
    people = list_people(cursor, ctx, query=query, project=project, department_id=department_id, scope='acts')
    last = queries.last_acts(cursor, [person['user_id'] for person in people])
    for person in people:
        act = last.get(person['user_id'])
        person['last_act'] = {key: _iso(value) for key, value in act.items()} if act else None
        person['work_name'] = rules.act_work_name(person['job_title']) if person['job_title'] else ''
    return people


def build_acts(cursor, ctx, payload, today):
    require(ctx, 'acts')
    period_from = rules.parse_day(payload.get('period_from'))
    period_to = rules.parse_day(payload.get('period_to'))
    if not period_from or not period_to or period_from > period_to:
        raise HrError('Укажите период: дата начала не позже даты конца', code='HR_ACT_PERIOD')
    if (period_to - period_from).days > 92:
        raise HrError('Период АВР — не больше трёх месяцев', code='HR_ACT_PERIOD')
    # По договору датой АВР считается последнее число отчётного периода.
    act_date = rules.parse_day(payload.get('act_date')) or period_to
    items = payload.get('items') or []
    if not items:
        raise HrError('Выберите хотя бы одного сотрудника', code='HR_ACT_EMPTY')
    if len(items) > 300:
        raise HrError('За раз — не больше 300 АВР', code='HR_ACT_TOO_MANY')
    ids = []
    for item in items:
        if not isinstance(item, dict):
            raise HrError('Сотрудник указан неверно', code='HR_ACT_BAD_USER')
        try:
            ids.append(int(item.get('user_id')))
        except (TypeError, ValueError):
            raise HrError('Сотрудник указан неверно', code='HR_ACT_BAD_USER') from None
    people = {person['user_id']: person for person in queries.list_people(cursor, user_ids=ids, include_fired=True,
                                                                           include_service=True)}
    companies = queries.list_companies(cursor)
    by_id = {company['id']: company for company in companies}
    index = queries.company_index(companies)
    acts = []
    for item in items:
        person = people.get(int(item['user_id']))
        if not person or not in_acts_scope(ctx, person):
            raise HrError('Среди выбранных есть сотрудник не из вашего отдела', status=403, code='HR_ACT_SCOPE')
        company, _linked = _company_for(person, by_id, index)
        contract = {'number': person.get('contract_number'), 'signed_on': person.get('contract_signed_on'),
                    'job_title': person.get('contract_job_title') or person.get('job_title')
                    or rules.default_job_title(person.get('department_code'))}
        work_name = ' '.join(str(item.get('work_name') or '').split()) or rules.act_work_name(contract['job_title'])
        if len(work_name) > 300:
            raise HrError('Наименование работ — не длиннее 300 знаков', code='HR_ACT_NAME')
        amount = person.get('contract_amount')
        if item.get('amount') not in (None, ''):
            amount, error = rules.clean_amount(item.get('amount'))
            if error:
                raise HrError(f"{person['name']}: {error}", code='HR_ACT_AMOUNT')
        # Повторная выгрузка того же акта (человек, компания, период) — тот же номер: нумерация
        # компании не должна разъезжаться с уже подписанными бумагами.
        number = queries.existing_act_number(cursor, person['user_id'], company['id'] if company else None,
                                             period_from, period_to)
        if not number:
            number = (rules.act_number(queries.next_number(cursor, company['id'], act_date.year, 'act'), act_date.year)
                      if company else 'б/н')
        queries.insert_act(cursor, user_id=person['user_id'], contract_id=person.get('contract_id'),
                           company_id=company['id'] if company else None, number=number, act_date=act_date,
                           period_from=period_from, period_to=period_to, work_name=work_name, amount=amount,
                           actor_id=ctx['user_id'])
        queries.audit(cursor, section='acts', action='act', user_id=person['user_id'], actor_id=ctx['user_id'],
                      summary=f"{number} за {rules.act_period_text(period_from, period_to)}: {work_name}")
        acts.append({'person': person, 'company': company, 'contract': contract, 'number': number,
                     'act_date': act_date, 'period_from': period_from, 'period_to': period_to,
                     'work_name': work_name, 'amount': amount})
    return acts


# ── Статус для «Отметок» и карточки ─────────────────────────────────────────

def status_lookup(cursor, ctx, name, today, employee_id=''):
    """Учётка по строке «Отметок»: сначала ручная привязка карточки («Сопоставление»),
    потом ФИО — с отчеством, если оно есть в строке (тёзка с другим отчеством не подставится)."""
    require(ctx, 'statuses')
    people = [person for person in queries.list_people(cursor) if in_status_scope(ctx, person)]
    found = []
    if employee_id:
        linked = queries.manual_links(cursor).get(str(employee_id))
        found = [person for person in people if linked and person['user_id'] == linked]
    if not found:
        found = importer.candidates_by_name(name, people)
    out = []
    for person in found[:5]:
        payload = status_payload(cursor, person, today)
        payload['name'] = person['name']
        payload['department'] = person.get('department_name') or ''
        out.append(payload)
    return out


def person_status(cursor, ctx, user_id, today):
    require(ctx, 'statuses')
    person = queries.get_person(cursor, user_id)
    if not person or not in_status_scope(ctx, person):
        raise HrError('Сотрудник не найден', status=404, code='HR_NOT_FOUND')
    payload = status_payload(cursor, person, today)
    payload['name'] = person['name']
    payload['department'] = person.get('department_name') or ''
    return payload


def change_status(cursor, db, ctx, user_id, payload, today):
    """Статус одной транзакцией (разбор 10.11.2026): ручка графика при наложении режет старый
    период, а не заменяет его, — поэтому правка и «Работает» идут здесь, по id периода:
      * «Работает» — у начавшегося периода конец становится вчерашним днём (та же запись,
        с её комментарием и флагом стажа), будущий период удаляется;
      * правка внутри прежних дат того же статуса — новые даты той же записи;
      * иначе прежний период удаляется и новый ставится тем же _tx-кодом, что в графике
        (снимает смены за эти дни и синхронизирует статус).
    Увольнение здесь не трогается: у него причина и ЧС, его меняют в «Учете сотрудников»."""
    require(ctx, 'statuses')
    person = queries.get_person(cursor, user_id)
    if not person or not in_status_scope(ctx, person):
        raise HrError('Сотрудник не найден', status=404, code='HR_NOT_FOUND')
    status = str(payload.get('status') or '').strip().lower()
    period = None
    if payload.get('period_id') not in (None, ''):
        try:
            period_id = int(payload.get('period_id'))
        except (TypeError, ValueError):
            raise HrError('Период указан неверно', code='HR_STATUS_BAD_PERIOD') from None
        cursor.execute(
            "SELECT id, lower(status_code), start_date, end_date, comment, COALESCE(exclude_from_tenure, FALSE) "
            "FROM operator_schedule_status_periods WHERE id = %s AND operator_id = %s FOR UPDATE",
            (period_id, int(user_id)))
        row = cursor.fetchone()
        if not row:
            raise HrError('Период не найден — обновите карточку', status=404, code='HR_STATUS_NO_PERIOD')
        period = {'id': row[0], 'code': row[1], 'start': row[2], 'end': row[3], 'comment': row[4],
                  'exclude_from_tenure': bool(row[5])}
        if period['code'] not in rules.PERIOD_STATUSES:
            raise HrError('Увольнение меняется в «Учете сотрудников»', status=409, code='HR_STATUS_DISMISSAL')
    yesterday = today - timedelta(days=1)
    kind = rules.clean_employment_type(person.get('contract_employment_type') or person.get('employment_type'))

    def note(summary):
        queries.audit(cursor, section='intake', action='status', user_id=int(user_id), summary=summary,
                      actor_id=ctx['user_id'])

    if status == rules.STATUS_WORKING:
        if not period:
            raise HrError('Человек и так работает', code='HR_STATUS_NOTHING')
        if period['start'] <= yesterday:
            cursor.execute("UPDATE operator_schedule_status_periods SET end_date = %s, updated_at = CURRENT_TIMESTAMP "
                           "WHERE id = %s", (yesterday, period['id']))
            note(journal.status_summary(status, kind, closed_on=yesterday, previous=period['code']))
        else:
            cursor.execute("DELETE FROM operator_schedule_status_periods WHERE id = %s", (period['id'],))
            note(journal.status_summary(status, kind, removed=True, previous=period['code']))
        db._sync_user_statuses_from_schedule_periods_tx(cursor, operator_ids=[int(user_id)])
        return
    allowed = [item['value'] for item in rules.status_options(kind, period['code'] if period else None)]
    if status not in rules.PERIOD_STATUSES or status not in allowed:
        raise HrError('Такой статус этому виду оформления не положен', code='HR_STATUS_BAD')
    start = rules.parse_day(payload.get('start'))
    end = rules.parse_day(payload.get('end'))
    errors = {}
    if not start:
        errors['start'] = 'Укажите начало'
    if not end:
        errors['end'] = 'Укажите окончание'
    if start and end and end < start:
        errors['end'] = 'Окончание не раньше начала'
    if errors:
        raise HrError('Проверьте даты', fields=errors, code='HR_STATUS_DATES')
    if period and period['code'] == status and period['start'] <= start and (period['end'] is None or end <= period['end']):
        cursor.execute("UPDATE operator_schedule_status_periods SET start_date = %s, end_date = %s, "
                       "updated_at = CURRENT_TIMESTAMP WHERE id = %s", (start, end, period['id']))
        db._sync_user_statuses_from_schedule_periods_tx(cursor, operator_ids=[int(user_id)])
        note(journal.status_summary(status, kind, start, end))
        return
    same_kind = bool(period and period['code'] == status)
    if period:
        cursor.execute("DELETE FROM operator_schedule_status_periods WHERE id = %s", (period['id'],))
    try:
        db._save_schedule_status_period_tx(
            cursor, int(user_id), status, start, end_date=end,
            comment=period['comment'] if same_kind else None, created_by=ctx['user_id'],
            exclude_from_tenure=period['exclude_from_tenure'] if same_kind else False)
    except ValueError as exc:
        raise HrError(str(exc), code='HR_STATUS_REJECTED', status=409) from None
    note(journal.status_summary(status, kind, start, end))


# ── Импорт таблиц HR ────────────────────────────────────────────────────────
# Загрузка — в два шага, и записывается только то, что HR увидел и подтвердил (просьба
# владельца 10.11.2026: «если уже такое есть, зачем загружать во второй раз; сверяет,
# говорит, что где поменяется, и подтвердить»). Предпросмотр сверяет каждую строку с
# тем, что уже записано (reconcile.py); строки, где всё совпадает, не пишутся вовсе.
# При записи сверка повторяется: поле пишется, только если его «было» осталось тем,
# что HR видел, — иначе оно в «расхождениях», а не тихо перезаписано. Последствия,
# которые HR сам не выбирает (новый договор — без прежних номера, даты и суммы),
# показываются в сверке и пишутся в журнал отдельными строками.

_DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')


def _text_value(value, limit=200):
    return ' '.join(str(value or '').split())[:limit]


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _contract_plan(person, row, fields, company_in, company_now):
    """Каким станет договор после записи подтверждённых полей — ровно тот расчёт, которым
    пишет запись, чтобы сверка показывала все её последствия.

    Есть договор и его компания/вид не меняются — договор тот же (номер, дата, сумма
    остаются). Поменялись компания или вид — это новый договор, прежние номер, дата и
    сумма в него не переходят (номер из нумерации другой компании был бы чужим).
    Договора нет — он заводится из того, что карточка уже показывает, плюс подтверждённое;
    если при этом нет ни вида, ни компании, договор не заводится вовсе (одна должность
    уходит только в карточку «Учета сотрудников»)."""
    has_contract = bool(person.get('contract_id'))
    if 'company' in fields:
        company = company_in
    else:
        company = None if has_contract else company_now
    company_id = company.get('id') if company else (person.get('contract_company_id') if has_contract else None)
    kind_now = rules.clean_employment_type(
        person.get('contract_employment_type') if has_contract else person.get('employment_type'))
    employment_type = rules.clean_employment_type(row.get('employment_type')) if 'employment_type' in fields else kind_now
    job_now = _text_value(person.get('contract_job_title') if has_contract else person.get('job_title'))
    job = _text_value(row.get('job_title')) if 'job_title' in fields else job_now
    keep = bool(has_contract and company_id == person.get('contract_company_id')
                and employment_type == rules.clean_employment_type(person.get('contract_employment_type')))
    return {'company': company, 'company_id': company_id, 'employment_type': employment_type, 'job_title': job,
            'keep': keep, 'has_contract': has_contract, 'save': bool(has_contract or company_id or employment_type)}


def _contract_reset_lines(person, plan):
    """Строки «станет пустым» для сверки и журнала: прежний договор сменяется новым."""
    if not plan['has_contract'] or plan['keep']:
        return []
    out = []
    if person.get('contract_number'):
        out.append({'field': 'contract_number', 'label': 'Номер договора', 'old': str(person['contract_number']),
                    'new': '', 'derived': True})
    if person.get('contract_signed_on'):
        out.append({'field': 'contract_signed_on', 'label': 'Дата договора',
                    'old': rules.date_dots(person['contract_signed_on']), 'new': '', 'derived': True})
    if person.get('contract_amount') is not None:
        # Сумма — личные данные договора: и здесь, и в журнале только фактом.
        out.append({'field': 'contract_amount', 'label': 'Сумма договора', 'hidden': True,
                    'note': 'станет пустой', 'derived': True})
    return out


def _row_reconcile(row, person, *, mode, project, by_id, index, create):
    """Сверка строки с человеком: (что поменяется, компания из таблицы, компания сейчас, заметки).
    Компанию, которую записать нельзя (в справочнике она не действует, название длиннее
    столбца), сверка не предлагает — у строки остаётся заметка почему."""
    company_now = _company_for(person, by_id, index)[0]
    company_in = reconcile.incoming_company(row, index, create)
    notes = []
    if company_in and company_in.get('is_active') is False:
        notes.append(f'Компания {rules.company_display_name(company_in)} в справочнике не действует — не записывается')
        company_in = None
    elif company_in and company_in.get('new') and len(company_in.get('name') or '') > COMPANY_NAME_MAX:
        notes.append(f'Название компании длиннее {COMPANY_NAME_MAX} знаков — заведите её в справочнике вручную')
        company_in = None
    now = reconcile.current(person, company_now)
    wanted = reconcile.incoming(row, mode=mode, project_for_groups=project, company=company_in)
    changes = reconcile.diff(now, wanted)
    for change in changes:
        if change['field'] == 'company' and company_in and company_in.get('new'):
            change['new_company'] = True
    return now, changes, company_in, company_now, notes


def _usual_project(rows, by_user):
    """Проект, в котором сейчас большинство найденных людей таблицы: им табель и заполняется
    по умолчанию — повторная загрузка табеля КЦ не предлагает перевести весь КЦ в «ЦО»."""
    seen = Counter()
    for row in rows:
        person = by_user.get((row.get('match') or {}).get('user_id')) if row.get('state') == 'found' else None
        if person and person.get('project_value'):
            seen[person['project_value']] += 1
    return seen.most_common(1)[0][0] if seen else None


def import_preview(cursor, ctx, content, project=None):
    """Таблица → сверка: у кого что поменяется («было → станет»), у кого всё совпадает,
    кого нет в iCORE. Заодно — загружали ли уже этот же файл."""
    require(ctx, 'hiring')
    try:
        sheet, rows = importer.read_rows(content)
    except importer.ImportError_ as error:
        raise HrError(error.message, code=error.code) from None
    people = queries.list_people(cursor)
    by_user = {person['user_id']: person for person in people}
    importer.match_people(rows, people)
    companies = queries.list_companies(cursor)
    by_id = {company['id']: company for company in companies}
    index = queries.company_index(companies)
    new_companies = importer.resolve_companies(rows, companies)
    mode = importer.detect_mode(rows)
    digest = hashlib.sha256(content).hexdigest()
    previous = queries.last_import(cursor, digest)
    group_project = None
    if mode == 'group':
        # Проект для отделов табеля: выбранный HR; иначе тот, с которым этот же файл загружали;
        # иначе тот, где сейчас большинство его людей; иначе «ЦО».
        group_project = (rules.clean_project(project) or rules.clean_project((previous or {}).get('project'))
                         or _usual_project(rows, by_user) or 'ЦО')
    counts = {'changed': 0, 'same': 0, 'ambiguous': 0, 'missing': 0, 'fields': 0}

    def reconcile_for(person):
        now, changes, company_in, company_now, notes = _row_reconcile(
            row, person, mode=mode, project=group_project, by_id=by_id, index=index, create=True)
        plan = _contract_plan(person, row, {change['field'] for change in changes}, company_in, company_now)
        return {'changes': changes + _contract_reset_lines(person, plan), 'notes': notes, 'now': reconcile.line(now),
                'status': 'changed' if changes else 'same'}

    for row in rows:
        if mode == 'project':
            row['project'] = rules.clean_project(row['group'])
        person = by_user.get(row['match']['user_id']) if row['state'] == 'found' else None
        if person:
            row.update(reconcile_for(person))
            counts[row['status']] += 1
            counts['fields'] += sum(1 for change in row['changes'] if not change.get('derived'))
        elif row['state'] == 'ambiguous':
            for candidate in row['candidates']:
                candidate.update(reconcile_for(by_user[candidate['user_id']]))
            counts['ambiguous'] += 1
        else:
            counts['missing'] += 1
    return {'sheet': sheet, 'mode': mode, 'project': group_project, 'rows': rows, 'new_companies': new_companies,
            'counts': counts, 'file_sha256': digest,
            'previous': {'at': _iso(previous['at']), 'actor_name': previous['actor_name']} if previous else None}


def _ensure_company(cursor, ctx, company, index, created_names):
    """Компания из таблицы, которой нет в справочнике: заводится один раз на загрузку."""
    key = company.get('key') or rules.company_key(company['name'])
    found = index.get(key)
    if found and found.get('id'):
        return found if found.get('is_active') is not False else None
    if queries.company_name_taken(cursor, company['name']):
        return None
    company_id = queries.save_company(cursor, None, {
        'name': company['name'], 'kind': company.get('kind') or 'too', 'is_active': True, 'city': 'Алматы'},
        ctx['user_id'])
    saved = {'id': company_id, 'name': company['name'], 'kind': company.get('kind') or 'too', 'is_active': True}
    index[key] = saved
    created_names.append(company['name'])
    return saved


def _write_import_row(cursor, ctx, person, row, fields, plan, *, mode, project):
    """Пишет подтверждённые поля строки по плану договора. Возвращает поля users для db.update_user."""
    actor = ctx['user_id']
    user_id = person['user_id']
    data = {}
    if 'project' in fields:
        data['project'] = rules.clean_project(row.get('group')) if mode == 'project' else project
    if 'timesheet_group' in fields:
        data['timesheet_group'] = _text_value(row.get('group'), 120)
    if 'schedule_pattern' in fields:
        data['schedule_pattern'] = rules.clean_schedule(row.get('schedule'))
    if 'day_hours' in fields:
        data['day_hours'] = reconcile.hours_value(row.get('day_hours'))
    if data:
        queries.upsert_employee_data(cursor, user_id, data, actor)
        if 'project' in data and (data['project'] or None) != (person.get('project') or None):
            queries.write_history(cursor, user_id, actor, 'hr_project', person.get('project'), data['project'])

    user_fields = {}
    if not fields & set(reconcile.CONTRACT_FIELDS):
        return user_fields
    if plan['save']:
        keep = plan['keep']
        queries.save_contract(cursor, user_id, {
            'company_id': plan['company_id'],
            'employment_type': plan['employment_type'],
            'number': person.get('contract_number') if keep else None,
            'signed_on': person.get('contract_signed_on') if keep else None,
            'liability_number': person.get('contract_liability_number') if keep else None,
            'amount': person.get('contract_amount') if keep else None,
            'job_title': plan['job_title'] or None,
        }, actor)
    # Карточка «Учета сотрудников» — только подтверждённые поля: молча её не правим.
    if 'employment_type' in fields and plan['employment_type'] != (person.get('employment_type') or None):
        user_fields['employment_type'] = plan['employment_type']
    if 'company' in fields and plan['company']:
        display = rules.company_display_name(plan['company'])
        if rules.company_key(display) != rules.company_key(person.get('company_name')):
            user_fields['company_name'] = display
    if 'job_title' in fields and plan['job_title'] and plan['job_title'] != (person.get('job_title') or ''):
        user_fields['job_title'] = plan['job_title']
    return user_fields


def import_apply(cursor, ctx, payload):
    """Записывает подтверждённое. Строки приходят все (и совпавшие, и ненайденные) — по ним
    собирается полная запись журнала. Возвращает ({user_id: поля users}, итог).

    Два прохода: сначала вся таблица сверяется по состоянию ДО записи, потом пишется. Иначе
    компания, заведённая строкой выше, меняла бы «было» у строк ниже, и они уходили бы в
    ложные расхождения."""
    require(ctx, 'hiring')
    mode = payload.get('mode') if payload.get('mode') in ('project', 'group') else 'group'
    group_project = rules.clean_project(payload.get('project')) if mode == 'group' else None
    rows = [row for row in payload.get('rows') or [] if isinstance(row, dict)]
    if not rows:
        raise HrError('Нечего записывать', code='HR_IMPORT_EMPTY')
    if len(rows) > importer.MAX_ROWS:
        raise HrError('Слишком много строк', code='HR_IMPORT_TOO_MANY')
    create = bool(payload.get('create_companies'))
    companies = queries.list_companies(cursor)
    by_id = {company['id']: company for company in companies}
    index = queries.company_index(companies)
    active_ids = [_int_or_none(row.get('user_id')) for row in rows if not row.get('skip')]
    people = {person['user_id']: person for person in queries.list_people(cursor, user_ids=[i for i in active_ids if i])}
    # Две строки таблицы на одного человека не пишутся: вторая считалась бы по снимку до записи
    # первой и откатила бы её. HR правит таблицу или карточку.
    usage = Counter(i for i in active_ids if i)

    changed, same, skipped, missing, unresolved, conflicts, duplicates = [], [], [], [], [], [], []
    plan_rows = []
    for row in rows:
        user_id = _int_or_none(row.get('user_id'))
        if row.get('skip') and user_id:
            skipped.append({'user_id': user_id, 'name': _text_value(row.get('name'))})
            continue
        person = people.get(user_id) if user_id else None
        if not person:
            # Тёзки, между которыми HR не выбрал, — не «нет в iCORE»: в журнале это разные вещи.
            (unresolved if not user_id and row.get('state') == 'ambiguous' else missing).append(
                _text_value(row.get('name')))
            continue
        if usage[user_id] > 1:
            duplicates.append({'user_id': user_id, 'name': person['name'], 'line': row.get('line')})
            continue
        _now, changes, company_in, company_now, _notes = _row_reconcile(
            row, person, mode=mode, project=group_project, by_id=by_id, index=index, create=create)
        if not changes:
            same.append({'user_id': user_id, 'name': person['name']})
            continue
        take, late = reconcile.confirmed_changes(changes, row.get('confirm'))
        plan_rows.append((row, person, take, late, company_in, company_now))

    updates, created_names, names = {}, [], {}
    fields_total = 0
    for row, person, take, late, company_in, company_now in plan_rows:
        user_id = person['user_id']
        if 'company' in {change['field'] for change in take} and company_in and company_in.get('new'):
            company_in = _ensure_company(cursor, ctx, company_in, index, created_names)
            if not company_in:  # такое название уже занято другой записью справочника
                late = late + [change for change in take if change['field'] == 'company']
                take = [change for change in take if change['field'] != 'company']
        if late:
            conflicts.append({'user_id': user_id, 'name': person['name'], 'fields': [change['label'] for change in late]})
        if not take:
            continue
        fields = {change['field'] for change in take}
        plan = _contract_plan(person, row, fields, company_in, company_now)
        user_fields = _write_import_row(cursor, ctx, person, row, fields, plan, mode=mode, project=group_project)
        if user_fields:
            updates[user_id] = user_fields
            names[user_id] = person['name']
        for change in take:
            change.pop('new_company', None)
        changed.append({'user_id': user_id, 'name': person['name'],
                        'changes': take + (_contract_reset_lines(person, plan) if fields & {'company', 'employment_type'} else [])})
        fields_total += len(take)

    counts = {'changed': len(changed), 'fields': fields_total, 'same': len(same), 'skipped': len(skipped),
              'missing': len(missing), 'unresolved': len(unresolved), 'duplicates': len(duplicates),
              'conflicts': len(conflicts), 'companies_created': len(created_names)}
    if changed or created_names:
        digest = str(payload.get('file_sha256') or '').lower()
        file_name = _text_value(payload.get('file_name'), 200)
        sheet = _text_value(payload.get('sheet'), 120)
        queries.audit(cursor, section='intake', action='import', actor_id=ctx['user_id'],
                      summary=journal.import_summary(file_name, sheet, len(changed), fields_total, created_names,
                                                     same=len(same), missing=len(missing)),
                      details={'file': file_name, 'sheet': sheet, 'mode': mode, 'project': group_project,
                               'file_sha256': digest if _DIGEST_RE.match(digest) else None, 'counts': counts,
                               'companies_created': created_names, 'people': changed, 'same': same,
                               'skipped': skipped, 'missing': missing, 'unresolved': unresolved,
                               'duplicates': duplicates, 'conflicts': conflicts})
    return updates, dict(counts, conflicts=conflicts, duplicates=duplicates, names=names)
