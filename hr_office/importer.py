# -*- coding: utf-8 -*-
"""Загрузка таблиц HR («Список по компаниям», табель) в карточки людей.

Из строки берутся: проект или табельный отдел (колонка «Отдел»), должность, вид
оформления и компания («ГПХ ИП Тестова», «ТД Taxi Technologies»), график и часы
в день (у табеля). Человек находится по ФИО среди работающих; не нашёлся или
нашлось двое — строка в отчёте, ничего не пишется. Сначала предпросмотр, запись —
отдельным шагом по выбранным строкам.
"""

import re
import zipfile
from io import BytesIO

from openpyxl import load_workbook

from . import queries, rules

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000

_KAZ = str.maketrans({'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у', 'һ': 'х', 'і': 'и',
                      'ё': 'е', 'й': 'и'})
# Сокращения компаний в таблицах HR → ключ и название в справочнике.
_COMPANY_ALIASES = {'тт': 'taxi technologies'}
_ALIAS_NAMES = {'taxi technologies': 'Taxi Technologies'}
MAX_UNPACKED_BYTES = 40 * 1024 * 1024


class ImportError_(Exception):
    def __init__(self, message, status=400, code='HR_IMPORT_ERROR'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def name_tokens(name):
    text = str(name or '').casefold().translate(_KAZ)
    text = re.sub(r'[^a-zа-я\s-]', ' ', text)
    return [token for token in re.split(r'[\s-]+', text) if token]


def candidates_by_name(name, people):
    """Люди с этим ФИО: «Фамилия Имя» в любом порядке, а если в строке есть отчество —
    у найденного не должно быть ДРУГОГО отчества. Тёзка «Иванов Иван Сергеевич» на строку
    «Иванов Иван Петрович» не подставится; учётка без отчества («Иванов Иван») — подходит."""
    tokens = name_tokens(name)
    if len(tokens) < 2:
        return []
    key, wanted = set(tokens[:2]), set(tokens)
    found = []
    for person in people:
        own = name_tokens(person['name'])
        if not key <= set(own):
            continue
        extra = set(own) - key
        if len(tokens) > 2 and extra and not extra <= wanted:
            continue
        found.append(person)
    if len(found) > 1 and len(tokens) > 2:
        exact = [person for person in found if wanted <= set(name_tokens(person['name']))]
        found = exact or found
    return found


def canonical_company(text):
    """Первая компания строки → (название для справочника, ключ). «ТТ» → «Taxi Technologies»."""
    name = clean_company_name(str(text or '').split(',')[0])
    key = rules.company_key(name)
    key = _COMPANY_ALIASES.get(key, key)
    return _ALIAS_NAMES.get(key, name), key


def parse_contract(text):
    """«ГПХ ИП Тестова» → ('gph', 'ИП Тестова'); «ТД Taxi Technologies» → ('of', …);
    «Ресурс Плюс KZ» → (None, 'Ресурс Плюс KZ'); «ГПХ» → ('gph', '')."""
    raw = ' '.join(str(text or '').split())
    if not raw:
        return None, ''
    match = re.match(r'^(ГПХ|ТД|СМЗ|ОФ)\b\s*(.*)$', raw, re.IGNORECASE)
    if match:
        kind = rules.clean_employment_type(match.group(1))
        company = match.group(2).strip().strip('"«»').strip()
        return kind, company
    return None, raw


def _find_header(ws):
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 15)):
        for cell in row:
            if isinstance(cell.value, str) and 'ф.и.о' in cell.value.casefold():
                return cell.row, cell.column
    return None, None


def read_rows(content):
    """Строки таблицы HR: колонки по заголовку «Ф.И.О должностного лица»."""
    if not content or len(content) > MAX_FILE_BYTES:
        raise ImportError_('Нужен файл Excel до 5 МБ')
    # Сжатый xlsx в 5 МБ распаковывается в сотни: openpyxl держит каждую ячейку в памяти,
    # и такой файл уронил бы весь процесс портала. Проверяем размер до разбора.
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise ImportError_('Таблица слишком большая — сохраните в ней только нужный лист')
    except zipfile.BadZipFile:
        raise ImportError_('Это не файл Excel (.xlsx)') from None
    try:
        workbook = load_workbook(BytesIO(content), data_only=True, read_only=False)
    except Exception:  # noqa: BLE001
        raise ImportError_('Excel не открывает этот файл') from None
    for ws in workbook.worksheets:
        if ws.sheet_state != 'visible':
            continue
        header_row, name_col = _find_header(ws)
        if not header_row:
            continue
        columns = {}
        for column in range(1, ws.max_column + 1):
            for row in (header_row - 1, header_row):
                value = ws.cell(row=row, column=column).value if row >= 1 else None
                text = str(value or '').casefold()
                if 'должност' in text and 'ф.и.о' not in text:
                    columns.setdefault('job', column)
                elif 'тд/гпх' in text or text.strip() in ('гпх', 'тд'):
                    columns.setdefault('contract', column)
                elif text.strip() == 'график':
                    columns.setdefault('schedule', column)
                elif 'рабочих дней план' in text:
                    columns.setdefault('plan_days', column)
                elif text.strip() == 'план часов':
                    columns.setdefault('plan_hours', column)
                elif text.strip() == 'отдел':
                    columns.setdefault('group', column)
        columns.setdefault('group', 1)
        rows, group = [], None
        for row_index in range(header_row + 1, min(ws.max_row, header_row + MAX_ROWS) + 1):
            group_value = ws.cell(row=row_index, column=columns['group']).value
            if isinstance(group_value, str) and group_value.strip():
                group = ' '.join(group_value.split())
            name = ws.cell(row=row_index, column=name_col).value
            if not isinstance(name, str) or len(name_tokens(name)) < 2:
                continue

            def value(key):
                column = columns.get(key)
                return ws.cell(row=row_index, column=column).value if column else None

            kind, company = parse_contract(value('contract'))
            plan_days, plan_hours = rules.number_or_none(value('plan_days')), rules.number_or_none(value('plan_hours'))
            day_hours = None
            if plan_days and plan_hours and plan_days > 0:
                ratio = plan_hours / plan_days
                if abs(ratio - round(ratio * 2) / 2) < 0.01 and 1 <= ratio <= 24:
                    day_hours = round(ratio * 2) / 2
            rows.append({
                'line': row_index,
                'name': ' '.join(str(name).split()),
                'group': group or '',
                'job_title': ' '.join(str(value('job') or '').split()),
                'employment_type': kind,
                'company': company,
                'schedule': rules.clean_schedule(value('schedule')),
                'day_hours': day_hours,
            })
        if rows:
            return ws.title, rows
    raise ImportError_('Не нашёл таблицу: нужна колонка «Ф.И.О должностного лица»')


def match_people(rows, people):
    """Ищет человека: «Фамилия Имя» из таблицы в ФИО работающего (порядок слов не важен), с
    отчеством — без подстановки тёзки с другим отчеством. Двое — выбор вручную."""
    for row in rows:
        found = candidates_by_name(row['name'], people)
        if len(found) == 1:
            person = found[0]
            row['match'] = {'user_id': person['user_id'], 'name': person['name'],
                            'department': person.get('department_name') or ''}
            row['state'] = 'found'
        elif found:
            row['match'] = None
            row['candidates'] = [{'user_id': person['user_id'], 'name': person['name'],
                                  'department': person.get('department_name') or ''} for person in found[:5]]
            row['state'] = 'ambiguous'
        else:
            row['match'] = None
            row['state'] = 'missing'
    return rows


def resolve_companies(rows, companies):
    index = queries.company_index(companies)
    new = {}
    for row in rows:
        name, key = canonical_company(row.get('company'))
        if not key:
            row['company_id'] = None
            continue
        found = index.get(key)
        if found:
            row['company_id'] = found['id']
            row['company_label'] = rules.company_display_name(found)
        else:
            row['company_id'] = None
            row['company_label'] = name
            new.setdefault(key, name)
    return sorted(new.values(), key=str.casefold)


def detect_mode(rows):
    """В колонке «Отдел» проекты («Список по компаниям») или табельные отделы (табель)."""
    groups = {row['group'] for row in rows if row['group']}
    if groups and all(rules.clean_project(group) for group in groups):
        return 'project'
    return 'group'


def company_kind_from_name(name):
    lower = str(name or '').strip().casefold()
    if lower.startswith('ип'):
        return 'ip'
    if lower.startswith('тоо') or lower.startswith('too'):
        return 'too'
    return 'too'


def clean_company_name(name):
    text = ' '.join(str(name or '').split())
    text = re.sub(r'^(ИП|ТОО|TOO)\s+', '', text, flags=re.IGNORECASE)
    return text.strip().strip('"«»').strip()
