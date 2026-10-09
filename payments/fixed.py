"""Регулярные платежи: периодичность, автосоздание заявок, импорт из Excel.

Дополнение Дмитриевой к задаче #179, п. 11: перечень регулярных расходов
(аренда, телеметрия…) загружается файлом или заводится руками; система сама
создаёт по нему заявки в начале периода и ставит их «на согласование» с
ответственным из настроек; связь шаблон → заявка сохраняется
(payment_requests.fixed_template_id), и такие заявки участвуют в контроле
просрочек.

ТЗ «Закуп и оплата» (#381), п. 4.4 и п. 13: тот же перечень — справочник
«Регулярные платежи». Заявку по нему заводит и человек («Оплата по
действующему/регулярному обязательству»): выбирает платёж, а поставщика,
договор, компанию, реквизиты, назначение, лимит и согласующего подставляет
справочник (`request_fields`).

Что здесь решено за постановку (она молчит):

* **Когда именно создавать.** «В начале соответствующего периода (например,
  месяца)» — для месячных, квартальных и годовых платежей заявка создаётся
  первого числа месяца, на который приходится срок оплаты. Для произвольного
  интервала — за `lead_days` до срока (по умолчанию неделя).
* **Автозаявка уходит на согласование без счёта.** Человек, заводя регулярную
  оплату сам, прикладывает счёт сразу (п. 4.4). У календаря счёта быть не
  может — он создаёт заявку первого числа; заявка всё равно ставится «на
  согласование», как просит дополнение, а счёт раздел запросит у ответственного
  перед оплатой (flow.advance: «Счёт не приложен»).
* **Двойное создание исключено** сдвигом `next_due_on` вперёд сразу после
  создания и пометкой `last_generated_on`.

Чистые функции (даты, разбор файла) отделены от тех, что ходят в базу
(`generate`, `resolve_import`, `commit_import`) — первые проверяются тестами
без Postgres.
"""

import calendar
import io
import logging
import re
from datetime import date, datetime, timedelta

from . import directory, flow, notices, notify, queries, workflow

PERIOD_LABELS = {
    'monthly': 'Ежемесячно',
    'quarterly': 'Ежеквартально',
    'yearly': 'Ежегодно',
    'custom': 'Свой интервал',
}


def add_months(value, months):
    """Дата через `months` месяцев с тем же числом; 31-е в коротком месяце — последний день."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


_PERIOD_MONTHS = {'monthly': 1, 'quarterly': 3, 'yearly': 12}


def due_day_of(next_due_on, periodicity):
    """Число месяца, на которое назначен платёж, — для периодов в месяцах."""
    return next_due_on.day if periodicity in _PERIOD_MONTHS and next_due_on else None


def next_due(due_on, periodicity, interval_days=None, due_day=None):
    """Следующий срок платежа. `due_day` — число месяца из справочника: срок
    считается от него, а не от прошлой даты, иначе платёж с 31-го после февраля
    навсегда съехал бы на 28-е."""
    months = _PERIOD_MONTHS.get(periodicity)
    if months:
        following = add_months(due_on, months)
        day = int(due_day or 0)
        if 1 <= day <= 31:
            following = following.replace(day=min(day, calendar.monthrange(following.year, following.month)[1]))
        return following
    days = int(interval_days or 0)
    if days <= 0:
        raise ValueError('Для своего интервала нужно число дней')
    return due_on + timedelta(days=days)


def generate_on(due_on, periodicity, lead_days=7):
    """Когда создавать заявку под этот срок."""
    if periodicity in ('monthly', 'quarterly', 'yearly'):
        return due_on.replace(day=1)
    return due_on - timedelta(days=max(0, int(lead_days or 0)))


def request_fields(template):
    """Что регулярный платёж подставляет в заявку сам (п. 4.4): поставщик, договор,
    компания, реквизиты, назначение платежа. Способ оплаты — счёт; тип объекта —
    из справочника (обычно услуга: аренда, связь, лицензии)."""
    object_type = template.get('object_type') or workflow.OBJECT_SERVICE
    return {
        'fixed_template_id': template['id'],
        'expense_name': template['name'],
        'counterparty_id': template.get('counterparty_id'),
        'contract_id': template.get('contract_id'),
        'legal_entity_id': template.get('legal_entity_id'),
        'counterparty_account_id': template.get('counterparty_account_id'),
        'payment_purpose': template.get('payment_purpose'),
        'category_id': template.get('category_id'),
        'subcategory_id': template.get('subcategory_id'),
        'project_id': template.get('project_id'),
        'branch': template.get('branch'),
        'payment_method': workflow.METHOD_INVOICE,
        'object_type': object_type,
        # П. 10: у товара категория учёта обязательна — её тоже даёт справочник.
        'accounting_category': template.get('accounting_category') if object_type == workflow.OBJECT_GOODS else None,
    }


def is_due(template, today):
    """Пора ли создавать заявку по шаблону сегодня."""
    if not template.get('is_active') or template.get('auto_create') is False:
        return False
    due_on = workflow._as_date(template.get('next_due_on'))
    if not due_on:
        return False
    start = generate_on(due_on, template.get('periodicity'), template.get('lead_days'))
    if start > today:
        return False
    last = workflow._as_date(template.get('last_generated_on'))
    # Уже создавали под ЭТОТ срок — next_due_on тогда сдвинут, и сюда не попадём;
    # проверка страхует ручной сдвиг срока назад в тот же день.
    return not (last and last >= start and template.get('open_request_id'))


def generate(cursor, *, today=None, actor=None, base_url=None):
    """Создаёт заявки по всем шаблонам, у которых наступило начало периода.

    Возвращает (созданные заявки, письма для Telegram). Письма отправляет
    вызывающий ПОСЛЕ коммита — иначе человек получит ссылку на заявку, которой
    ещё нет.
    """
    today = today or queries.today_almaty()
    actor = actor or {'id': None, 'name': 'Календарь платежей'}
    created, outbox = [], []
    members = queries.role_member_ids(cursor)
    if not members.get('approver') or not members.get('accounting'):
        # Без утверждающих и бухгалтерии заявке некуда идти — календарь молчит и
        # говорит об этом в лог, а не плодит зависшие заявки.
        logging.warning('Оплата счетов: календарь не создаёт заявки — не назначены участники ролей')
        return created, outbox
    for template in directory.list_templates(cursor, include_inactive=False):
        if not is_due(template, today):
            continue
        locked = directory.read_template(cursor, template['id'], lock=True)
        if not locked or not is_due(locked, today):
            continue
        # Под SAVEPOINT: платёж с незаполненным справочником (нет поставщика,
        # компании) не должен ронять остальные — он просто ждёт, пока его дополнят.
        cursor.execute('SAVEPOINT payments_template')
        try:
            request_id = create_from_template(cursor, locked, actor=actor, today=today, outbox=outbox,
                                              base_url=base_url)
            cursor.execute('RELEASE SAVEPOINT payments_template')
        except (ValueError, flow.FlowError) as exc:
            cursor.execute('ROLLBACK TO SAVEPOINT payments_template')
            logging.warning('Оплата счетов: заявка по платежу «%s» не создана — %s', locked.get('name'), exc)
            continue
        created.append(request_id)
    return created, outbox


def create_from_template(cursor, template, *, actor, today, outbox, base_url=None):
    """Заявка по регулярному платежу от имени его ответственного, сразу на согласование.

    ValueError — в справочнике не хватает данных, без которых заявка не уйдёт
    (ответственный, поставщик, компания): текст называет, чего именно.
    """
    responsible = queries.user_brief(cursor, template['responsible_user_id'])
    if not responsible or responsible['fired']:
        raise ValueError('У платежа «%s» нет действующего ответственного' % template['name'])
    department = directory.department_brief(cursor, responsible.get('department_id'))
    due_on = workflow._as_date(template['next_due_on'])
    fields = dict(request_fields(template))
    fields.update({
        'request_kind': workflow.KIND_REGULAR,
        'department_id': (department or {}).get('id'),
        'department_name': (department or {}).get('name'),
        'payment_period': _period_text(due_on, template.get('periodicity')),
        'notes': template.get('note'),
        'due_on': due_on,
    })
    items = [{'name': template['name'], 'quantity': 1, 'unit': None, 'unit_price': template['amount']}]
    comment = 'Создано по календарю регулярных платежей: «%s», срок %s' % (
        template['name'], workflow.fmt_date(due_on))
    request_id = queries.create_request(cursor, fields=fields, items=items, actor=actor,
                                        initiator=responsible, system_comment=comment)
    queries.log_event(cursor, request_id, 'generated', actor,
                      payload={'template_id': template['id'], 'due_on': due_on})
    try:
        flow.submit(cursor, request_id, actor, comment=comment, outbox=outbox, base_url=base_url,
                    by_calendar=True)
    except flow.FlowError as exc:
        raise ValueError('платёж «%s»: %s' % (template['name'], exc))
    directory.advance_template(
        cursor, template['id'],
        next_due_on=next_due(due_on, template['periodicity'], template.get('interval_days'),
                             template.get('due_day')),
        generated_on=today)
    request = queries.read_request(cursor, request_id)
    event = notify.generated(request, template['name'])
    notices.push(cursor, user_ids=flow.open_to([responsible['id']]), request_id=request_id,
                 kind=event['kind'], title=event['title'], body=event['body'])
    return request_id


def _period_text(due_on, periodicity):
    months = ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август',
              'сентябрь', 'октябрь', 'ноябрь', 'декабрь')
    if not due_on:
        return None
    if periodicity == 'monthly':
        return '%s %s' % (months[due_on.month - 1], due_on.year)
    if periodicity == 'quarterly':
        return '%s квартал %s' % ((due_on.month - 1) // 3 + 1, due_on.year)
    if periodicity == 'yearly':
        return '%s год' % due_on.year
    return 'до %s' % workflow.fmt_date(due_on)


# ─────────────────────────────────────────────────────────────────────────────
# Импорт календаря из Excel
# ─────────────────────────────────────────────────────────────────────────────

# Заголовки колонок по ТЗ п. 11.1 и их бытовые варианты. Сравнение — по
# нормализованной строке (нижний регистр, без пунктуации).
_HEADERS = {
    'name': ('наименование', 'название', 'платеж', 'платёж', 'расход', 'наименование расхода'),
    'amount': ('сумма', 'сумма расхода', 'стоимость'),
    'periodicity': ('периодичность', 'период', 'частота'),
    'due': ('дата оплаты', 'срок', 'крайний срок', 'дата', 'день оплаты', 'срок оплаты'),
    'project': ('проект', 'филиал', 'проект филиал', 'проект / филиал', 'таксопарк', 'город'),
    'responsible': ('ответственный', 'инициатор', 'фио', 'сотрудник'),
    'category': ('категория', 'категория расхода'),
    'subcategory': ('подкатегория', 'подкатегория расхода'),
    'counterparty': ('контрагент', 'поставщик'),
    'note': ('примечание', 'примечания', 'комментарий'),
}


def _norm(text):
    return re.sub(r'[^a-zа-яё0-9 ]+', ' ', str(text or '').lower().replace('ё', 'е')).strip()


def detect_columns(header_row):
    mapping = {}
    for index, cell in enumerate(header_row):
        key = _norm(cell)
        if not key:
            continue
        for field, variants in _HEADERS.items():
            if field in mapping:
                continue
            if key in {_norm(v) for v in variants}:
                mapping[field] = index
                break
    return mapping


def parse_periodicity(value):
    """Строка из файла → (periodicity, interval_days) либо (None, None)."""
    text = _norm(value)
    if not text:
        return None, None
    if any(word in text for word in ('месяц', 'ежемес', 'monthly')):
        return 'monthly', None
    if 'квартал' in text or 'quarter' in text:
        return 'quarterly', None
    if any(word in text for word in ('год', 'ежегод', 'year', 'annual')):
        return 'yearly', None
    match = re.search(r'(\d+)\s*(д|дн|день|дня|дней|day)', text)
    if match:
        return 'custom', int(match.group(1))
    if 'недел' in text or 'week' in text:
        return 'custom', 7
    return None, None


def parse_due(value, today):
    """Дата оплаты из ячейки: дата, «dd.mm.yyyy», либо просто число месяца («3»)."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        day = int(float(value))
        if 1 <= day <= 31:
            candidate = date(today.year, today.month, min(day, calendar.monthrange(today.year, today.month)[1]))
            if candidate < today:
                candidate = add_months(candidate, 1)
                candidate = candidate.replace(day=min(day, calendar.monthrange(candidate.year, candidate.month)[1]))
            return candidate
        return None
    text = str(value).strip()
    for pattern in ('%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    match = re.match(r'^(\d{1,2})[ -]?(?:го|е|ое)?(?:\s+числа)?$', text.lower())
    if match:
        return parse_due(int(match.group(1)), today)
    return None


def parse_workbook(data, today=None):
    """Байты xlsx → строки предпросмотра с ошибками по каждой."""
    from openpyxl import load_workbook

    today = today or queries.today_almaty()
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    workbook.close()
    header_index = None
    mapping = {}
    for index, row in enumerate(rows[:20]):
        mapping = detect_columns(row or ())
        if 'name' in mapping and 'amount' in mapping:
            header_index = index
            break
    if header_index is None:
        raise ValueError('Не нашёл строку заголовков: нужны хотя бы колонки «Наименование» и «Сумма»')

    def cell(row, field):
        index = mapping.get(field)
        if index is None or index >= len(row):
            return None
        return row[index]

    result = []
    for row in rows[header_index + 1:]:
        if not row or all(value in (None, '') for value in row):
            continue
        name = str(cell(row, 'name') or '').strip()
        errors = []
        if not name:
            errors.append('нет наименования')
        amount = workflow.to_decimal(cell(row, 'amount'))
        if amount <= 0:
            errors.append('сумма не разобрана')
        periodicity, interval_days = parse_periodicity(cell(row, 'periodicity'))
        if not periodicity:
            periodicity, interval_days = 'monthly', None
        due_on = parse_due(cell(row, 'due'), today)
        if not due_on:
            errors.append('дата оплаты не разобрана')
        result.append({
            'name': name,
            'amount': amount,
            'periodicity': periodicity,
            'interval_days': interval_days,
            'next_due_on': due_on,
            'project': str(cell(row, 'project') or '').strip() or None,
            'responsible': str(cell(row, 'responsible') or '').strip() or None,
            'category': str(cell(row, 'category') or '').strip() or None,
            'subcategory': str(cell(row, 'subcategory') or '').strip() or None,
            'counterparty': str(cell(row, 'counterparty') or '').strip() or None,
            'note': str(cell(row, 'note') or '').strip() or None,
            'errors': errors,
        })
    return result


def resolve_import(cursor, rows, fallback_responsible_id):
    """Сопоставляет строки файла со справочниками и людьми; ничего не пишет.

    Ответственный ищется по ФИО (без учёта регистра, по вхождению); не нашёлся —
    подставляется тот, кто загружает файл, с пометкой в строке.
    """
    users = queries.list_users(cursor)
    by_name = {_norm(u['name']): u for u in users}
    projects = {_norm(p['name']): p for p in directory.list_projects(cursor, include_inactive=True)}
    categories = directory.list_categories(cursor, include_inactive=True)
    roots = {_norm(c['name']): c for c in categories if not c['parent_id']}
    children = {}
    for item in categories:
        if item['parent_id']:
            children.setdefault(item['parent_id'], {})[_norm(item['name'])] = item
    counterparties = {_norm(c['name']): c for c in directory.list_counterparties(cursor, include_inactive=True)}
    fallback = next((u for u in users if u['id'] == int(fallback_responsible_id or 0)), None)
    existing = _template_keys(cursor)

    for row in rows:
        notes = []
        responsible = None
        if row.get('responsible'):
            key = _norm(row['responsible'])
            responsible = by_name.get(key) or next(
                (u for norm_name, u in by_name.items() if key and (key in norm_name or norm_name in key)), None)
            if not responsible:
                notes.append('ответственный «%s» не найден — подставлен загружающий' % row['responsible'])
        if not responsible:
            responsible = fallback
        row['responsible_user_id'] = (responsible or {}).get('id')
        row['responsible_name'] = (responsible or {}).get('name')
        if not row['responsible_user_id']:
            row['errors'].append('не определён ответственный')

        project = projects.get(_norm(row.get('project'))) if row.get('project') else None
        row['project_id'] = (project or {}).get('id')
        row['project_new'] = bool(row.get('project') and not project)

        category = roots.get(_norm(row.get('category'))) if row.get('category') else None
        row['category_id'] = (category or {}).get('id')
        row['category_new'] = bool(row.get('category') and not category)
        sub = None
        if row.get('subcategory') and category:
            sub = children.get(category['id'], {}).get(_norm(row['subcategory']))
        row['subcategory_id'] = (sub or {}).get('id')
        row['subcategory_new'] = bool(row.get('subcategory') and not sub)

        cp = counterparties.get(_norm(row.get('counterparty'))) if row.get('counterparty') else None
        row['counterparty_id'] = (cp or {}).get('id')
        row['counterparty_new'] = bool(row.get('counterparty') and not cp)
        row['exists'] = _template_key(row) in existing
        if row['exists']:
            notes.append('такой платёж уже есть в справочнике — строка будет пропущена')
        row['notes'] = notes
    return rows


def _template_key(row):
    """По чему платёж считается тем же самым: название, поставщик и периодичность."""
    return (' '.join(_norm(row.get('name')).split()), int(row.get('counterparty_id') or 0),
            row.get('periodicity') or 'monthly')


def _template_keys(cursor):
    return {_template_key(item) for item in directory.list_templates(cursor)}


def commit_import(cursor, rows, actor_id):
    """Заводит недостающие справочники и шаблоны. Возвращает id созданных платежей.

    Пропускаются строки с ошибками и те, чей платёж уже есть в справочнике:
    повторная загрузка того же файла не должна удвоить календарь. Номера строк
    справочников из запроса сверяются с базой — присланный id может устареть.
    """
    created = []
    existing = _template_keys(cursor)
    user_ids = {user['id'] for user in queries.list_users(cursor)}
    project_ids = {item['id'] for item in directory.list_projects(cursor, include_inactive=True)}
    categories = {item['id']: item for item in directory.list_categories(cursor, include_inactive=True)}
    counterparty_ids = {item['id'] for item in directory.list_counterparties(cursor, include_inactive=True)}

    def known(value, ids):
        try:
            return int(value) if int(value) in ids else None
        except (TypeError, ValueError):
            return None

    for row in rows:
        if row.get('errors'):
            continue
        responsible_id = known(row.get('responsible_user_id'), user_ids)
        if not responsible_id:
            row['errors'] = ['не определён ответственный']
            continue
        project_id = known(row.get('project_id'), project_ids)
        if not project_id and row.get('project'):
            project_id, _ = directory.find_or_create_project(cursor, row['project'], actor_id)
        category_id = known(row.get('category_id'), categories)
        if not category_id and row.get('category'):
            category_id, _ = directory.find_or_create_category(cursor, row['category'], None, actor_id)
        subcategory_id = known(row.get('subcategory_id'), categories)
        if subcategory_id and categories[subcategory_id].get('parent_id') != category_id:
            subcategory_id = None
        if not subcategory_id and row.get('subcategory') and category_id:
            subcategory_id, _ = directory.find_or_create_category(cursor, row['subcategory'], category_id, actor_id)
        counterparty_id = known(row.get('counterparty_id'), counterparty_ids)
        if not counterparty_id and row.get('counterparty'):
            counterparty_id, _ = directory.find_or_create_counterparty(cursor, row['counterparty'], actor_id)
        key = _template_key(dict(row, counterparty_id=counterparty_id))
        if key in existing:
            row['exists'] = True
            continue
        existing.add(key)
        next_due_on = workflow._as_date(row['next_due_on'])
        template_id = directory.upsert_template(cursor, fields={
            'name': row['name'],
            'amount': workflow.to_decimal(row['amount']),
            'periodicity': row['periodicity'],
            'interval_days': row.get('interval_days'),
            'next_due_on': next_due_on,
            'lead_days': 7,
            'project_id': project_id,
            'branch': None,
            'responsible_user_id': responsible_id,
            'category_id': category_id,
            'subcategory_id': subcategory_id,
            'counterparty_id': counterparty_id,
            'legal_entity_id': None,
            # Договор, реквизиты, назначение, лимит и согласующего файл не
            # несёт — их дополняют в карточке платежа.
            'contract_id': None, 'counterparty_account_id': None, 'payment_purpose': None,
            'amount_limit': None, 'approver_user_id': None, 'object_type': workflow.OBJECT_SERVICE,
            'accounting_category': None, 'due_day': due_day_of(next_due_on, row['periodicity']),
            'auto_create': True,
            'note': row.get('note'),
            'is_active': True,
        }, actor_id=actor_id)
        created.append(template_id)
    return created


def template_sample_rows():
    """Строки-образцы для скачивания шаблона файла."""
    return [
        ('Наименование', 'Сумма', 'Периодичность', 'Дата оплаты', 'Проект / филиал', 'Ответственный',
         'Категория', 'Подкатегория', 'Контрагент', 'Примечание'),
        ('Аренда офиса Алматы', 450000, 'ежемесячно', '5', 'Таксопарк Алматы', 'Ядигаров Руслан',
         'Аренда', 'Офис', 'ТОО «Пример»', 'по договору №12'),
    ]


def log_generation(created):
    if created:
        logging.info('Оплата счетов: по календарю регулярных платежей создано заявок — %s', len(created))
