# -*- coding: utf-8 -*-
"""Правила оформления по умолчанию (ТЗ «iCore 4», п. 3) — задаёт отдел кадров во вкладке «Настройки».

Правило: «отделы + кому в отделе → вид оформления, компания, должность». Любое из трёх может быть не задано.
Подставляется новому сотруднику, у которого ещё нет договора: в «Приеме» (строка и карточка), подсказкой
в окне создания учётки; должность — ещё в табель и АВР, если у человека она не указана.

Какое правило сильнее: правило для роли («Операторам», «Супервайзерам») — сильнее правила «Всем» того же
отдела; при равенстве — то, где меньше отделов, потом — более раннее. Два правила одного охвата не могут задавать
одно поле одному отделу (проверяется при сохранении) — иначе исход зависел бы от порядка записи.

До «Настроек» умолчания были зашиты в код (rules.DEFAULT_HIRING / DEFAULT_JOB_TITLES); при создании таблицы
они переносятся сюда правилами (SEED) — после выкладки всё работает как раньше, но правится без программиста.
"""

from . import rules

# Кому в отделе: роль сотрудника (users.role). Линия — как у СВ в «АВР» (service.SUPERVISOR_TARGET_ROLES);
# СВ — и старое написание роли 'supervisor' (database.py считает его СВ наравне с 'sv').
ROLE_SCOPES = {'all': None, 'line': ('operator', 'trainee', 'trainer'), 'sv': ('sv', 'supervisor')}
ROLE_SCOPE_TITLES = {'all': 'Всем', 'line': 'Операторам', 'sv': 'Супервайзерам'}
# «для операторов отдела «Тез КЦ»» — подпись умолчания в карточке («По умолчанию для …»).
_ROLE_FOR = {'all': '', 'line': 'операторов ', 'sv': 'супервайзеров '}
FIELDS = ('employment_type', 'company_id', 'job_title')
FIELD_TITLES = {'employment_type': 'вид оформления', 'company_id': 'компанию', 'job_title': 'должность'}
JOB_TITLE_MAX = 200

# Умолчания, которые были в коде до «Настроек» (ТЗ п. 3 и должности линейных отделов): отделы — кодами,
# компании — ключом названия. Должность — только линии: у СВ СЗоВ «Оператор…» в договоре и АВР была ошибкой
# (сверка 11.11.2026), а GaBa по ТЗ — «операторам TEZ», а не всему отделу.
SEED = (
    (('szov', 'op'), 'all', 'gph', 'eazy go rent', None),
    (('tez',), 'line', 'gph', 'gaba', 'Оператор колл-центра'),
    (('szov',), 'line', None, None, 'Оператор службы заботы о водителях'),
    (('op',), 'line', None, None, 'Менеджер по привлечению водителей'),
    (('remote_cc',), 'line', None, None, 'Оператор колл-центра'),
    (('front_office',), 'line', None, None, 'Менеджер фронт офиса'),
)


def matches(rule, department_id, role):
    if department_id is None or int(department_id) not in {int(value) for value in rule['department_ids']}:
        return False
    roles = ROLE_SCOPES.get(rule['role_scope'])
    return roles is None or str(role or '').strip().lower() in roles


def ordered(rules_list):
    """Сильнее — правило для роли, потом с меньшим числом отделов, потом более раннее."""
    return sorted(rules_list, key=lambda rule: (rule['role_scope'] == 'all', len(rule['department_ids']), rule['id']))


def short_department(name):
    """«СЗоВ — Служба заботы о водителях» → «СЗоВ»: в подписях хватает короткого имени."""
    return str(name or '').split(' — ')[0].strip()


def _join(names):
    names = [name for name in names if name]
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + ' и ' + names[-1]


def _names(ids, department_names):
    return [name for name in (short_department(department_names.get(int(value), '')) for value in ids) if name]


def _quoted(names):
    return _join([f'«{name}»' for name in names])


def departments_of(ids, department_names):
    """«отдела «Тез КЦ»», «отделов «СЗоВ» и «Отдел продаж»». Названия отделов не склоняются («для Отдел продаж» —
    ошибка), поэтому они в кавычках после «отдела / отделов»."""
    names = _names(ids, department_names)
    if not names:
        return ''
    return ('отдела ' if len(names) == 1 else 'отделов ') + _quoted(names)


def departments_in(ids, department_names):
    """«в отделе «СЗоВ»», «в отделах «СЗоВ» и «Отдел продаж»»."""
    names = _names(ids, department_names)
    if not names:
        return ''
    return ('в отделе ' if len(names) == 1 else 'в отделах ') + _quoted(names)


def label(rule, department_names):
    """Для кого правило — «операторов отдела «Тез КЦ»», «отделов «СЗоВ» и «Отдел продаж»»."""
    return (_ROLE_FOR.get(rule['role_scope'], '') + departments_of(rule['department_ids'], department_names)).strip()


def resolve(rules_list, department_id, role, inactive_companies=()):
    """Умолчание для сотрудника: каждое поле — из самого сильного подходящего правила, где оно задано.
    {'employment_type', 'company_id', 'job_title', 'hiring_rule', 'sources'} — hiring_rule: правило, давшее вид или
    компанию; sources: поле → id правила (для счётчика «сейчас подставляется» в «Настройках»).
    Компания, помеченная недействующей (inactive_companies), не подставляется — поле берётся из следующего правила:
    иначе правило роли с недействующей компанией гасило бы действующую компанию правила «Всем»."""
    out = {'employment_type': None, 'company_id': None, 'job_title': None, 'hiring_rule': None, 'sources': {}}
    for rule in ordered([rule for rule in rules_list if matches(rule, department_id, role)]):
        for field in FIELDS:
            value = rule.get(field)
            if out[field] is not None or not value:
                continue
            if field == 'company_id' and value in inactive_companies:
                continue
            out[field] = value
            out['sources'][field] = rule['id']
            if field != 'job_title' and out['hiring_rule'] is None:
                out['hiring_rule'] = rule
    return out


def job_title_for(rules_list, person):
    return resolve(rules_list, person.get('department_id'), person.get('role'))['job_title'] or ''


def clean(payload, *, department_ids, company_ids):
    """Поля правила из формы + ошибки у полей (как у компаний: fields для красного у поля)."""
    payload = payload or {}
    errors = {}
    picked = []
    raw = payload.get('department_ids')
    # Только список: строка «12» иначе разобралась бы по символам — в отделы 1 и 2.
    for value in raw if isinstance(raw, (list, tuple)) else []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number in department_ids and number not in picked:
            picked.append(number)
    if not picked:
        errors['department_ids'] = 'Выберите хотя бы один отдел'
    scope = str(payload.get('role_scope') or 'all').strip().lower()
    if scope not in ROLE_SCOPES:
        errors['role_scope'] = 'Нет такого варианта'
    kind = payload.get('employment_type') or None
    if kind is not None:
        kind = rules.clean_employment_type(kind)
        if not kind:
            errors['employment_type'] = 'Нет такого вида оформления'
    company_id = payload.get('company_id') or None
    if company_id is not None:
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            company_id = -1
        if company_id not in company_ids:
            errors['company_id'] = 'Компании нет в справочнике'
    job_title = ' '.join(str(payload.get('job_title') or '').split())
    if len(job_title) > JOB_TITLE_MAX:
        errors['job_title'] = f'Не длиннее {JOB_TITLE_MAX} знаков'
    if not errors and not (kind or company_id or job_title):
        errors['employment_type'] = 'Задайте вид оформления, компанию или должность'
    fields = {'department_ids': sorted(picked), 'role_scope': scope, 'employment_type': kind,
              'company_id': company_id, 'job_title': job_title or None}
    return fields, errors


def conflicts(fields, others, department_names):
    """Другое правило того же охвата на тот же отдел уже задаёт то же поле — какое правило сработает, было бы
    неясно. Возвращает текст ошибки или ''."""
    clashes = []
    mine = set(fields['department_ids'])
    for rule in others:
        if rule['role_scope'] != fields['role_scope']:
            continue
        shared = mine & {int(value) for value in rule['department_ids']}
        same = [FIELD_TITLES[field] for field in FIELDS if fields.get(field) and rule.get(field)]
        if shared and same:
            clashes.append(f'{_join(same)} {departments_in(sorted(shared), department_names)} уже задаёт правило '
                           f'«{who(rule, department_names)}»')
    if not clashes:
        return ''
    text = '; '.join(clashes)
    fix = 'то правило' if len(clashes) == 1 else 'те правила'
    return f'{text[:1].upper()}{text[1:]}. Поменяйте {fix} или выберите другое «Кому в отделе».'


def who(rule, department_names):
    """Правило так, как его видно в списке: «СЗоВ и Отдел продаж (всем)» — название правила, не фраза."""
    return f'{_join(_names(rule["department_ids"], department_names))} ({ROLE_SCOPE_TITLES[rule["role_scope"]].lower()})'


def normalized(rule):
    """Правило для сравнения «изменилось ли»: отделы по порядку, пустые поля — None."""
    return (tuple(sorted(int(value) for value in rule['department_ids'])), rule['role_scope'],
            rule.get('employment_type') or None, rule.get('company_id') or None, rule.get('job_title') or None)


def describe(rule, department_names, companies_by_id):
    """Строка для журнала и списка: «СЗоВ и Отдел продаж (всем) → ГПХ, ИП «Eazy Go Rent»»."""
    parts = []
    if rule.get('employment_type'):
        parts.append(rules.EMPLOYMENT_SHORT.get(rule['employment_type'], rule['employment_type']))
    company = companies_by_id.get(rule.get('company_id')) if rule.get('company_id') else None
    if company:
        parts.append(rules.company_display_name(company))
    if rule.get('job_title'):
        parts.append(f'должность «{rule["job_title"]}»')
    return f'{who(rule, department_names)} → {", ".join(parts) or "—"}'


def seed_rows(departments_by_code, company_index):
    """Строки SEED с id отделов и компаний этой базы; отделов, которых нет, правило не получает."""
    out = []
    for codes, scope, kind, company_key, job_title in SEED:
        # По порядку, как их сохраняет форма (clean): иначе сохранение без правок выглядело бы правкой.
        ids = sorted(departments_by_code[code] for code in codes if code in departments_by_code)
        if not ids:
            continue
        company = company_index.get(company_key) if company_key else None
        if company_key and not company and not kind and not job_title:
            continue
        out.append({'department_ids': ids, 'role_scope': scope, 'employment_type': kind,
                    'company_id': company['id'] if company else None, 'job_title': job_title})
    return out
