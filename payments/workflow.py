"""Правила бизнес-процесса «Закуп товара/услуги». Чистая логика: ни базы, ни Flask.

Здесь лежит всё, что можно проверить без браузера и без Postgres:

* двенадцать шагов постановки Зарины Алиевой (18.08.2026) — кто отвечает, что
  обязан приложить, куда возвращается заявка при доработке;
* правило договора из дополнения Колкомбаевой: счёт свыше 300 000 ₸ не идёт
  дальше инициатора без ДЕЙСТВУЮЩЕГО договора с этим контрагентом;
* правило Приказа: Директор по развитию согласует счёт вместо Учредителя
  только при выполнении ВСЕХ условий Приказа — период, проект, контрагент,
  лимит, — и система обязана показать основание либо причину, почему Приказ не
  применён;
* сборка маршрута заявки: кому достаётся каждый шаг.

Правила реализованы БУКВАЛЬНО по постановкам, без обобщений (требование
владельца). Где постановка молчит, выбор назван в докстроке и вынесен в отчёт.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

ROLE_INITIATOR = 'initiator'
ROLE_MANAGER = 'manager'
ROLE_FOUNDER = 'founder'
ROLE_ACCOUNTING = 'accounting'

ROLE_LABELS = {
    ROLE_INITIATOR: 'Инициатор',
    ROLE_MANAGER: 'Руководитель инициатора',
    ROLE_FOUNDER: 'Учредитель',
    ROLE_ACCOUNTING: 'Бухгалтерия',
    # Не шаг маршрута, а справочная роль: кого подставлять в Приказ как
    # «кому передаётся право согласования». Сам по себе счетов не согласует.
    'development_director': 'Директор по развитию',
}

# Порог, с которого счёт требует действующего договора: «для счетов на сумму
# более 300 000 тг» — строго больше.
CONTRACT_REQUIRED_OVER = Decimal('300000')

# Шаги — дословно по постановке. `files` — какие виды вложений уместны на шаге,
# `files_required` — без вложения отписаться нельзя (шаги 1, 7, 11; на шаге 10
# вложение обязательно, только если его запросили на шаге 7 — см.
# missing_requirements). `fields` — поля заявки, которые заполняются на шаге.
# `returns_to` — куда уходит заявка при возврате на доработку: замечания по
# закупу правит инициатор на шаге 1, замечания по счёту — на шаге 7.
STEPS = (
    {
        'no': 1, 'role': ROLE_INITIATOR, 'phase': 'purchase',
        'title': 'Согласование закупки',
        'brief': 'Реестр поставщиков со сравнением цен и ссылками либо КП, если альтернатив нет',
        'action': 'Отправить на согласование',
        'files': ('supplier_registry', 'offer'), 'files_required': True,
    },
    {
        'no': 2, 'role': ROLE_MANAGER, 'phase': 'purchase',
        'title': 'Подтверждение целесообразности закупа',
        'brief': 'Целесообразность закупа и корректный выбор поставщика',
        'action': 'Подтвердить', 'can_reject': True, 'returns_to': 1,
    },
    {
        'no': 3, 'role': ROLE_FOUNDER, 'phase': 'purchase',
        'title': 'Итоговое подтверждение закупа',
        'brief': 'Сумма и выбор поставщика',
        'action': 'Подтвердить', 'can_reject': True, 'returns_to': 1,
    },
    {
        'no': 4, 'role': ROLE_INITIATOR, 'phase': 'requisites',
        'title': 'Запрос реквизитов у бухгалтерии',
        'brief': 'ИП/ТОО, НДС или без НДС, сумма; по сумме уточнить, нужен ли договор',
        'action': 'Реквизиты запрошены',
    },
    {
        'no': 5, 'role': ROLE_ACCOUNTING, 'phase': 'requisites',
        'title': 'Реквизиты для счёта',
        'brief': 'Бухгалтерия предоставляет реквизиты, на которые выставят счёт',
        'action': 'Реквизиты предоставлены', 'fields': ('invoice_requisites',),
    },
    {
        'no': 6, 'role': ROLE_INITIATOR, 'phase': 'invoice',
        'title': 'Запрос счёта у поставщика',
        'brief': 'Счёт запрашивается на реквизиты из шага 5',
        'action': 'Счёт запрошен',
    },
    {
        'no': 7, 'role': ROLE_INITIATOR, 'phase': 'invoice',
        'title': 'Согласование счёта на оплату',
        'brief': 'Счёт вложением; одним текстом — что закупается, за какой период, '
                 'с какого на какое юр. лицо, сумма, отдел; нужна ли доверенность '
                 'или платёжное поручение',
        'action': 'Отправить счёт на согласование',
        'files': ('invoice',), 'files_required': True,
        'fields': ('invoice_description', 'legal_entity_id'),
    },
    {
        'no': 8, 'role': ROLE_ACCOUNTING, 'phase': 'invoice',
        'title': 'Проверка счёта',
        'brief': 'Платили ли раньше: когда и на какую сумму в последний раз',
        'action': 'Счёт проверен', 'fields': ('previous_payment_note',), 'returns_to': 7,
    },
    {
        'no': 9, 'role': ROLE_FOUNDER, 'phase': 'invoice',
        'title': 'Подтверждение счёта',
        'brief': 'Учредитель либо согласующий по Приказу',
        'action': 'Подтвердить оплату', 'can_reject': True, 'returns_to': 7,
    },
    {
        'no': 10, 'role': ROLE_ACCOUNTING, 'phase': 'payment',
        'title': 'Оплата счёта',
        'brief': 'Отписаться об оплате; приложить платёжное поручение или доверенность, если их запрашивали',
        'action': 'Оплачено',
        'files': ('payment_order', 'power_of_attorney'), 'fields': ('paid_on', 'paid_amount'),
    },
    {
        'no': 11, 'role': ROLE_INITIATOR, 'phase': 'closing',
        'title': 'Получение товара/услуги',
        'brief': 'Скан или фото АВР/накладной; оригинал передать в бухгалтерию',
        'action': 'Получено', 'files': ('act',), 'files_required': True,
    },
    {
        'no': 12, 'role': ROLE_ACCOUNTING, 'phase': 'closing',
        'title': 'Закрытие заявки',
        'brief': 'Оригиналы закрывающих документов получены',
        'action': 'Закрыть заявку',
    },
)

STEP_BY_NO = {item['no']: item for item in STEPS}
FIRST_STEP = STEPS[0]['no']
LAST_STEP = STEPS[-1]['no']

PHASES = (
    ('purchase', 'Согласование закупки'),
    ('requisites', 'Реквизиты'),
    ('invoice', 'Счёт'),
    ('payment', 'Оплата'),
    ('closing', 'Закрытие'),
)
PHASE_LABELS = dict(PHASES)

ATTACHMENT_LABELS = {
    'supplier_registry': 'Реестр поставщиков',
    'offer': 'Коммерческое предложение',
    'invoice': 'Счёт на оплату',
    'payment_order': 'Платёжное поручение',
    'power_of_attorney': 'Доверенность',
    'act': 'АВР / накладная',
    'contract': 'Договор',
    'other': 'Другое',
}

BLOCK_CONTRACT_REQUIRED = 'contract_required'


def step(no):
    return STEP_BY_NO.get(int(no or 0))


def step_title(no):
    item = step(no)
    return item['title'] if item else 'Шаг %s' % no


def to_decimal(value, default=Decimal('0')):
    """Деньги приходят числом, строкой с пробелами и запятой, Decimal — сводим к Decimal."""
    if value is None or value == '':
        return default
    if isinstance(value, Decimal):
        return value
    text = str(value)
    for gap in (' ', ' ', ' ', ' ', '₸', 'тг'):
        text = text.replace(gap, '')
    try:
        return Decimal(text.replace(',', '.'))
    except (InvalidOperation, ValueError):
        return default


def items_total(items):
    """Сумма заявки — из позиций: количество × цена за единицу, по каждой строке."""
    total = Decimal('0')
    for item in items or []:
        qty = to_decimal(item.get('quantity'), Decimal('1'))
        price = to_decimal(item.get('unit_price'))
        total += qty * price
    return total.quantize(Decimal('0.01'))


# ─── Договор ─────────────────────────────────────────────────────────────────

def contract_gate(amount, contract, *, counterparty_id=None, on_date=None):
    """(пропускать ли счёт дальше, причина отказа).

    Правило дополнения Колкомбаевой: сумма > 300 000 ₸ и при этом договора нет,
    он не в системе, просрочен или не в статусе «действующий» — счёт остаётся у
    инициатора. Причина формулируется так, чтобы инициатор увидел, что именно
    исправить. Договор с ДРУГИМ контрагентом — тоже отсутствие договора: право
    на оплату даёт договор именно с поставщиком по счёту.
    """
    total = to_decimal(amount)
    if total <= CONTRACT_REQUIRED_OVER:
        return True, None
    on_date = on_date or date.today()
    prefix = 'Сумма счёта %s превышает %s. ' % (fmt_money(total), fmt_money(CONTRACT_REQUIRED_OVER))
    if not contract:
        return False, (prefix + 'Действующий договор с контрагентом не найден. '
                       'Укажите действующий договор — до этого счёт не может быть '
                       'передан на согласование.')
    number = contract.get('number') or '—'
    status = str(contract.get('status') or '').strip().lower()
    if status != 'active':
        return False, (prefix + 'Договор №%s имеет статус «%s» и для проверки считается '
                       'отсутствующим. Укажите действующий договор.'
                       % (number, CONTRACT_STATUS_LABELS.get(status, status or '—')))
    if counterparty_id is not None and contract.get('counterparty_id') not in (None, counterparty_id):
        return False, (prefix + 'Договор №%s заключён с другим контрагентом. Укажите '
                       'договор с поставщиком по счёту.' % number)
    ends_on = _as_date(contract.get('ends_on'))
    if ends_on and ends_on < on_date:
        return False, (prefix + 'Договор №%s просрочен: срок действия истёк %s. '
                       'Счёт не может быть передан на согласование до указания '
                       'действующего договора.' % (number, fmt_date(ends_on)))
    starts_on = _as_date(contract.get('starts_on'))
    if starts_on and starts_on > on_date:
        return False, (prefix + 'Договор №%s вступает в силу только %s.'
                       % (number, fmt_date(starts_on)))
    return True, None


CONTRACT_STATUS_LABELS = {
    'active': 'Действующий',
    'terminated': 'Расторгнут',
    'cancelled': 'Отменён',
    'archived': 'Архивный',
    'inactive': 'Недействующий',
}


# ─── Приказы ─────────────────────────────────────────────────────────────────

def evaluate_order(order, *, amount, project_id, counterparty_id, on_date, standard_role=ROLE_FOUNDER):
    """Проверяет ОДИН Приказ по всем условиям ТЗ и объясняет каждое.

    Возвращает {'order_id', 'number', 'applies', 'checks': [...], 'reason'}:
    `checks` — по одному на условие (статус, срок, кого замещает, проект,
    контрагент, лимит), `reason` — первое невыполненное словами, как в п. 12 ТЗ
    («Приказ №15 не применен. Причина: …»).
    """
    total = to_decimal(amount)
    checks = []

    def add(key, label, ok, detail):
        checks.append({'key': key, 'label': label, 'ok': bool(ok), 'detail': detail})

    status = str(order.get('status') or '').lower()
    add('status', 'Приказ действует', status == 'active',
        'статус «действующий»' if status == 'active' else 'Приказ отменён или деактивирован')

    starts_on = _as_date(order.get('starts_on'))
    ends_on = _as_date(order.get('ends_on'))
    in_period = (starts_on is None or starts_on <= on_date) and (ends_on is None or ends_on >= on_date)
    add('period', 'Срок действия', in_period,
        'действует %s — %s' % (fmt_date(starts_on) if starts_on else '…',
                                fmt_date(ends_on) if ends_on else 'бессрочно')
        if in_period else 'срок действия Приказа не покрывает дату %s' % fmt_date(on_date))

    replaces = str(order.get('replaces_role') or ROLE_FOUNDER)
    add('replaces', 'Замещает стандартного согласующего', replaces == standard_role,
        'замещает: %s' % ROLE_LABELS.get(replaces, replaces)
        if replaces == standard_role else
        'Приказ замещает %s, а стандартный согласующий счёта — %s'
        % (ROLE_LABELS.get(replaces, replaces), ROLE_LABELS.get(standard_role, standard_role)))

    project_ids = {int(x) for x in (order.get('project_ids') or [])}
    project_ok = bool(order.get('all_projects')) or (project_id is not None and int(project_id) in project_ids)
    add('project', 'Проект соответствует', project_ok,
        'все проекты' if order.get('all_projects') else
        ('проект входит в Приказ' if project_ok else
         'проект счёта не входит в область действия Приказа'))

    cp_ids = {int(x) for x in (order.get('counterparty_ids') or [])}
    cp_ok = bool(order.get('all_counterparties')) or (counterparty_id is not None and int(counterparty_id) in cp_ids)
    add('counterparty', 'Контрагент соответствует', cp_ok,
        'все контрагенты' if order.get('all_counterparties') else
        ('контрагент входит в Приказ' if cp_ok else
         'контрагент «%s» отсутствует в перечне контрагентов Приказа'
         % (order.get('_counterparty_name') or 'по счёту')))

    limit = order.get('amount_limit')
    limit_ok = limit in (None, '') or total <= to_decimal(limit)
    add('limit', 'Лимит не превышен', limit_ok,
        'без лимита' if limit in (None, '') else
        ('лимит по Приказу %s' % fmt_money(to_decimal(limit)) if limit_ok else
         'сумма счёта %s превышает лимит %s' % (fmt_money(total), fmt_money(to_decimal(limit)))))

    applies = all(check['ok'] for check in checks)
    failed = next((check for check in checks if not check['ok']), None)
    number = order.get('number') or str(order.get('id') or '')
    return {
        'order_id': order.get('id'),
        'number': number,
        'issued_on': fmt_date(_as_date(order.get('issued_on'))) if order.get('issued_on') else None,
        'delegate_user_id': order.get('delegate_user_id'),
        'delegate_name': order.get('delegate_name'),
        'applies': applies,
        'checks': checks,
        'reason': None if applies else 'Приказ №%s не применён. Причина: %s.' % (number, failed['detail']),
    }


def _order_priority(order):
    """Ключ сортировки по п. 10 ТЗ: конкретный проект старше «всех проектов»,
    конкретные контрагенты старше «всех», новый Приказ старше старого."""
    issued = _as_date(order.get('issued_on')) or date.min
    return (
        0 if not order.get('all_projects') else 1,
        0 if not order.get('all_counterparties') else 1,
        -issued.toordinal(),
        -int(order.get('id') or 0),
    )


def resolve_invoice_approver(orders, *, amount, project_id, counterparty_id, on_date,
                             standard_role=ROLE_FOUNDER):
    """Кто согласует счёт на шаге 9 и на каком основании.

    Возвращает словарь для карточки (п. 11 ТЗ): стандартный согласующий,
    фактический, основание (Приказ) либо причины, по которым ни один Приказ не
    применён. `conflict` — два применимых Приказа отдают право разным людям;
    выбирается приоритетный, но карточка предупреждает.
    """
    evaluations = []
    for order in sorted(orders or [], key=_order_priority):
        evaluations.append((order, evaluate_order(
            order, amount=amount, project_id=project_id, counterparty_id=counterparty_id,
            on_date=on_date, standard_role=standard_role)))
    applicable = [(order, ev) for order, ev in evaluations if ev['applies']]
    chosen = applicable[0] if applicable else None
    delegates = {int(order.get('delegate_user_id') or 0) for order, _ in applicable}
    basis = {
        'standard_role': standard_role,
        'standard_label': ROLE_LABELS.get(standard_role, standard_role),
        'on_date': fmt_date(on_date),
        'amount': str(to_decimal(amount)),
        'approver_user_id': None,
        'approver_name': None,
        'order_id': None,
        'order_number': None,
        'conflict': len(delegates) > 1,
        'evaluations': [ev for _, ev in evaluations],
    }
    if chosen:
        order, ev = chosen
        basis.update({
            'approver_user_id': order.get('delegate_user_id'),
            'approver_name': order.get('delegate_name'),
            'order_id': order.get('id'),
            'order_number': ev['number'],
        })
    return basis


# ─── Маршрут ─────────────────────────────────────────────────────────────────

def build_route(*, initiator, manager=None, approver=None):
    """Двенадцать шагов с ответственными.

    initiator/manager/approver — {'id', 'name'} либо None. Шаг 2 без
    руководителя ПРОПУСКАЕТСЯ с пометкой: постановка молчит о заявке от
    человека, у которого руководителя нет (владелец, глава без начальника), а
    оставить шаг без ответственного — значит заморозить заявку навсегда.
    Учредитель на шаге 3 всё равно подтверждает закуп, так что контроль не
    теряется. approver — согласующий счёта по Приказу; без него шаг 9 у роли.
    """
    route = []
    for item in STEPS:
        entry = {'step_no': item['no'], 'role_code': item['role'],
                 'assignee_id': None, 'assignee_name': None, 'state': 'pending'}
        if item['role'] == ROLE_INITIATOR and initiator:
            entry['assignee_id'] = initiator.get('id')
            entry['assignee_name'] = initiator.get('name')
        elif item['role'] == ROLE_MANAGER:
            if manager and manager.get('id'):
                entry['assignee_id'] = manager.get('id')
                entry['assignee_name'] = manager.get('name')
            else:
                entry['state'] = 'skipped'
                entry['comment'] = 'Шаг пропущен: у инициатора не определён непосредственный руководитель'
        elif item['no'] == 9 and approver and approver.get('id'):
            entry['assignee_id'] = approver.get('id')
            entry['assignee_name'] = approver.get('name')
        route.append(entry)
    return route


def next_open_step(route, after):
    """Следующий шаг после `after`, который не пропущен; None — маршрут пройден."""
    for entry in route:
        if entry['step_no'] > after and entry.get('state') != 'skipped':
            return entry['step_no']
    return None


def missing_requirements(step_no, request, attachments):
    """Чего не хватает, чтобы отписаться на шаге. Пустой список — можно.

    `attachments` — вложения ЭТОГО шага (list of {'kind'}). Проверки повторяют
    постановку: шаг 1 — реестр или КП, шаг 5 — реквизиты, шаг 7 — счёт и текст
    описания, шаг 8 — отписка о прошлых оплатах, шаг 10 — платёжка/доверенность,
    если их просили на шаге 7, шаг 11 — АВР/накладная.
    """
    item = step(step_no)
    if not item:
        return ['Неизвестный шаг']
    kinds = {str(att.get('kind') or 'other') for att in (attachments or [])}
    missing = []
    if step_no == 1 and not attachments:
        missing.append('Приложите реестр поставщиков со сравнением цен либо КП')
    if step_no == 5 and not str(request.get('invoice_requisites') or '').strip():
        missing.append('Укажите реквизиты, на которые выставить счёт')
    if step_no == 7:
        if 'invoice' not in kinds:
            missing.append('Приложите счёт на оплату')
        if not str(request.get('invoice_description') or '').strip():
            missing.append('Опишите одним текстом: что закупается, за какой период, '
                           'с какого на какое юр. лицо, сумма, отдел')
        if not request.get('legal_entity_id'):
            missing.append('Укажите юр. лицо, с которого идёт оплата')
        if not request.get('counterparty_id'):
            missing.append('Укажите контрагента')
        if to_decimal(request.get('amount')) <= 0:
            missing.append('Сумма счёта должна быть больше нуля')
    if step_no == 8 and not str(request.get('previous_payment_note') or '').strip():
        missing.append('Отпишитесь, когда и на какую сумму оплачивали в последний раз '
                       '(или что оплат не было)')
    if step_no == 10:
        if not request.get('paid_on'):
            missing.append('Укажите дату оплаты')
        if to_decimal(request.get('paid_amount')) <= 0:
            missing.append('Укажите оплаченную сумму')
        if request.get('needs_payment_order') and 'payment_order' not in kinds:
            missing.append('Приложите платёжное поручение — его запросили на шаге 7')
        if request.get('needs_power_of_attorney') and 'power_of_attorney' not in kinds:
            missing.append('Приложите доверенность — её запросили на шаге 7')
    if step_no == 11 and 'act' not in kinds:
        missing.append('Приложите скан или фото АВР/накладной')
    return missing


def request_state(request, today=None):
    """Состояние заявки для строки реестра: done / rejected / cancelled /
    blocked / overdue / active. Порядок важен: закрытая заявка не бывает
    просроченной, заблокированная — важнее просрочки (сначала снимают блок)."""
    status = request.get('status') or 'active'
    if status in ('done', 'rejected', 'cancelled'):
        return status
    if request.get('block_code'):
        return 'blocked'
    today = today or date.today()
    due_on = _as_date(request.get('due_on'))
    paid = int(request.get('current_step') or 0) > 10
    if due_on and due_on < today and not paid:
        return 'overdue'
    return 'active'


# ─── Форматирование ──────────────────────────────────────────────────────────

def fmt_money(value):
    total = to_decimal(value)
    sign = '-' if total < 0 else ''
    total = abs(total)
    whole = int(total)
    cents = int((total - whole) * 100)
    text = '{:,}'.format(whole).replace(',', ' ')
    if cents:
        text += ',%02d' % cents
    return '%s%s ₸' % (sign, text)


def fmt_date(value):
    value = _as_date(value)
    return value.strftime('%d.%m.%Y') if value else ''


def _as_date(value):
    if value is None or value == '':
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
