"""Правила бизнес-процесса «Закуп товара/услуги». Чистая логика: ни базы, ни Flask.

ТЗ «Закуп и оплата» (задача #381, Зарина Алиева, 06.10.2026) заменило
двенадцать шагов первой версии одной заявкой с этапами:

    Инициация → Согласование → Оплата → Получение → Учёт имущества (если нужно)
    → Закрывающие документы → Закрытие

Этап исполняется ПОДЗАДАЧЕЙ. Подзадачу никто не заводит руками: она появляется
сама, когда пройден предыдущий этап (п. 2), и у неё один исполнитель — человек
(инициатор, руководитель, утверждающий) либо подразделение («Бухгалтерия»,
«Финансовый отдел» — п. 9: исполнитель этапа оплаты — отдел, а не сотрудник).

Здесь лежит всё, что проверяется без браузера и без Postgres:

* какие подзадачи нужны заявке и в каком порядке (`build_route`): зависит от
  типа заявки (п. 4), способа оплаты (п. 5) и категории учёта (п. 10);
* матрица согласования (`resolve_approval`, п. 6): кому из утверждающих уходит
  заявка и нужен ли этап руководителя;
* чего не хватает, чтобы отправить заявку (`missing_for_submit`, п. 4) и
  выполнить действие подзадачи (`missing_for_action`, пп. 5, 8, 9, 10, 14);
* в какой колонке доски стоит заявка (`board_column`, пп. 7–9);
* условия закрытия (`closing_blockers`, п. 15);
* правила первой версии, которые ТЗ не отменяет: счёт свыше 300 000 ₸ не идёт
  в оплату без ДЕЙСТВУЮЩЕГО договора (`contract_gate`), позиции «количество ×
  цена» с суммой до тиына.

Правила реализованы БУКВАЛЬНО по постановкам, без обобщений (требование
владельца). Где постановка молчит, выбор назван в докстроке.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ─── Роли ────────────────────────────────────────────────────────────────────

ROLE_INITIATOR = 'initiator'
ROLE_MANAGER = 'manager'
ROLE_APPROVER = 'approver'
ROLE_ACCOUNTING = 'accounting'
ROLE_FINANCE = 'finance'
ROLE_ASSET_KEEPER = 'asset_keeper'

# Подписи — дословно из п. 3 ТЗ.
ROLE_LABELS = {
    ROLE_INITIATOR: 'Инициатор',
    ROLE_MANAGER: 'Непосредственный руководитель',
    ROLE_APPROVER: 'Утвердитель',
    ROLE_ACCOUNTING: 'Бухгалтерия',
    ROLE_FINANCE: 'Финансовый отдел',
    ROLE_ASSET_KEEPER: 'Ответственный за учёт имущества',
}

# ─── Этапы (п. 2, п. 20) ─────────────────────────────────────────────────────

STAGE_INITIATION = 'initiation'
STAGE_APPROVAL = 'approval'
STAGE_PAYMENT = 'payment'
STAGE_RECEIVING = 'receiving'
STAGE_ASSETS = 'assets'
STAGE_CLOSING = 'closing'
STAGE_CLOSED = 'closed'

STAGES = (
    (STAGE_INITIATION, 'Инициация закупа'),
    (STAGE_APPROVAL, 'Согласование'),
    (STAGE_PAYMENT, 'Оплата'),
    (STAGE_RECEIVING, 'Получение'),
    (STAGE_ASSETS, 'Учёт имущества'),
    (STAGE_CLOSING, 'Закрывающие документы'),
    (STAGE_CLOSED, 'Закрытие'),
)
STAGE_LABELS = dict(STAGES)
STAGE_ORDER = {code: index for index, (code, _label) in enumerate(STAGES)}

# ─── Варианты выбора в заявке ────────────────────────────────────────────────

KIND_PURCHASE = 'purchase'
KIND_REGULAR = 'regular'
REQUEST_KIND_LABELS = {
    KIND_PURCHASE: 'Новый закуп',
    KIND_REGULAR: 'Оплата по действующему/регулярному обязательству',
}

METHOD_INVOICE = 'invoice'
METHOD_CARD = 'card'
PAYMENT_METHOD_LABELS = {
    METHOD_INVOICE: 'Оплата счёта',
    METHOD_CARD: 'Пополнение банковской карты',
}

CARD_EMPLOYEE = 'employee'
CARD_SUPPLIER = 'supplier'
CARD_RECIPIENT_LABELS = {
    CARD_EMPLOYEE: 'Карта сотрудника',
    CARD_SUPPLIER: 'Карта поставщика',
}

OBJECT_GOODS = 'goods'
OBJECT_SERVICE = 'service'
OBJECT_TYPE_LABELS = {OBJECT_GOODS: 'Товар', OBJECT_SERVICE: 'Услуга'}

ACCOUNTING_CONSUMABLE = 'consumable'
ACCOUNTING_ASSET = 'asset'
ACCOUNTING_CATEGORY_LABELS = {
    ACCOUNTING_CONSUMABLE: 'Расходный материал',
    ACCOUNTING_ASSET: 'Имущество, подлежащее учёту',
}

# П. 4.3: причина, по которой у закупа нет альтернативных предложений.
NO_ALTERNATIVES_REASONS = (
    ('single_supplier', 'Единственный поставщик'),
    ('license_renewal', 'Продление существующей лицензии'),
    ('compatibility', 'Техническая совместимость'),
    ('specific_contractor', 'Конкретный подрядчик'),
    ('existing_contract', 'Закуп по действующему договору'),
    ('other', 'Другое'),
)
NO_ALTERNATIVES_LABELS = dict(NO_ALTERNATIVES_REASONS)

# П. 14: статусы закрывающих документов — дословно.
DOCS_NONE = 'none'
DOCS_SCAN = 'scan'
DOCS_ORIGINAL = 'original'
DOCS_CLOSED = 'closed'
CLOSING_DOC_STATUSES = (
    (DOCS_NONE, 'Документы не получены'),
    (DOCS_SCAN, 'Скан получен'),
    (DOCS_ORIGINAL, 'Оригинал получен'),
    (DOCS_CLOSED, 'Документы закрыты'),
)
CLOSING_DOC_LABELS = dict(CLOSING_DOC_STATUSES)
CLOSING_DOC_ORDER = {code: index for index, (code, _label) in enumerate(CLOSING_DOC_STATUSES)}

# Статус имущества. ТЗ (п. 10.2) называет поле, но не значения — набор выбран
# по обычному ходу жизни имущества в учёте.
ASSET_STATUSES = (
    ('in_use', 'В эксплуатации'),
    ('in_stock', 'На складе'),
    ('repair', 'В ремонте'),
    ('written_off', 'Списано'),
)
ASSET_STATUS_LABELS = dict(ASSET_STATUSES)

# Настройки процесса и их значения по умолчанию. Минимум поставщиков задаёт
# администратор (п. 4.2); за сколько дней напоминать о сроке — тоже настройка,
# в ТЗ числа нет («приближении срока», п. 17).
SETTING_MIN_SUPPLIERS = 'min_suppliers'
SETTING_DUE_SOON_DAYS = 'due_soon_days'
SETTINGS_DEFAULTS = {SETTING_MIN_SUPPLIERS: 3, SETTING_DUE_SOON_DAYS: 2}
SETTINGS_LIMITS = {SETTING_MIN_SUPPLIERS: (1, 10), SETTING_DUE_SOON_DAYS: (0, 30)}

# Порог, с которого счёт требует действующего договора: «для счетов на сумму
# более 300 000 тг» — строго больше (дополнение Колкомбаевой к задаче #179).
CONTRACT_REQUIRED_OVER = Decimal('300000')

# ─── Подзадачи ───────────────────────────────────────────────────────────────

KIND_INITIATION = 'initiation'
KIND_MANAGER = 'manager_approval'
KIND_APPROVAL = 'approval'
KIND_INVOICE = 'invoice_payment'
KIND_CARD = 'card_topup'
KIND_RECEIPT = 'receipt_confirm'
KIND_RECEIVING = 'receiving'
KIND_ASSETS = 'asset_registration'
KIND_CLOSING = 'closing_docs'

BOARD_APPROVAL = 'approval'
BOARD_ACCOUNTING = 'accounting'
BOARD_FINANCE = 'finance'

# `statuses` — рабочие статусы открытой подзадачи (между ними карточку двигают
# свободно); `position` — место в маршруте; `brief` — что сделать исполнителю,
# простыми словами: этот текст человек видит в карточке и в уведомлении.
SUBTASKS = (
    {
        'kind': KIND_INITIATION, 'position': 0, 'stage': STAGE_INITIATION, 'role': ROLE_INITIATOR,
        'title': 'Заявка у инициатора', 'board': None, 'statuses': ('draft', 'rework', 'clarification'),
        'brief': 'Дополните заявку и отправьте её дальше.',
    },
    {
        'kind': KIND_MANAGER, 'position': 10, 'stage': STAGE_APPROVAL, 'role': ROLE_MANAGER,
        'title': 'Согласование руководителем', 'board': BOARD_APPROVAL, 'statuses': ('new', 'review'),
        'brief': 'Проверьте, нужен ли этот закуп и верно ли выбран поставщик.',
    },
    {
        'kind': KIND_APPROVAL, 'position': 20, 'stage': STAGE_APPROVAL, 'role': ROLE_APPROVER,
        'title': 'Утверждение', 'board': BOARD_APPROVAL, 'statuses': ('new', 'review'),
        'brief': 'Примите итоговое решение по закупу и способу оплаты.',
    },
    {
        'kind': KIND_INVOICE, 'position': 30, 'stage': STAGE_PAYMENT, 'role': ROLE_ACCOUNTING,
        'title': 'Оплата счёта', 'board': BOARD_ACCOUNTING,
        'statuses': ('new', 'checking', 'ready', 'paying'),
        'brief': 'Проверьте счёт и реквизиты, оплатите и приложите платёжное поручение.',
    },
    {
        'kind': KIND_CARD, 'position': 30, 'stage': STAGE_PAYMENT, 'role': ROLE_FINANCE,
        'title': 'Пополнение карты', 'board': BOARD_FINANCE,
        'statuses': ('new', 'in_progress', 'ready'),
        'brief': 'Переведите сумму на карту и приложите подтверждение перевода.',
    },
    {
        'kind': KIND_RECEIPT, 'position': 40, 'stage': STAGE_PAYMENT, 'role': ROLE_INITIATOR,
        'title': 'Предоставить чек/подтверждение расхода', 'board': None, 'statuses': ('new',),
        'brief': 'Приложите чек или другое подтверждение того, на что потрачены деньги с карты.',
    },
    {
        'kind': KIND_RECEIVING, 'position': 50, 'stage': STAGE_RECEIVING, 'role': ROLE_INITIATOR,
        'title': 'Получение товара/услуги', 'board': None, 'statuses': ('new',),
        'brief': 'Подтвердите получение: дата и количество. Приложите накладную, акт или чек, если они уже есть.',
    },
    {
        'kind': KIND_ASSETS, 'position': 60, 'stage': STAGE_ASSETS, 'role': ROLE_ASSET_KEEPER,
        'title': 'Постановка имущества на учёт', 'board': None, 'statuses': ('new', 'in_progress'),
        'brief': 'Присвойте инвентарный номер, укажите город, подразделение, ответственного и место '
                 'эксплуатации, приложите акт приёма-передачи.',
    },
    {
        'kind': KIND_CLOSING, 'position': 70, 'stage': STAGE_CLOSING, 'role': ROLE_ACCOUNTING,
        'title': 'Закрывающие документы', 'board': BOARD_ACCOUNTING, 'statuses': ('awaiting',),
        'brief': 'Проверьте закрывающие документы, отметьте получение оригиналов и закройте документы.',
    },
)
SUBTASK_BY_KIND = {item['kind']: item for item in SUBTASKS}
APPROVAL_KINDS = (KIND_MANAGER, KIND_APPROVAL)
PAYMENT_KINDS = (KIND_INVOICE, KIND_CARD)
STATUS_CLARIFICATION = 'clarification'


def subtask(kind):
    return SUBTASK_BY_KIND.get(kind)


def subtask_title(kind):
    item = subtask(kind)
    return item['title'] if item else str(kind or '')


def initial_status(kind):
    item = subtask(kind)
    return item['statuses'][0] if item and item.get('statuses') else None


def can_move(kind, to_status):
    """Можно ли поставить открытой подзадаче этот рабочий статус.

    Между рабочими статусами карточку двигают в любую сторону: «Проверка» →
    «Готово к оплате» и обратно. Итоговые колонки («Оплачено», «Согласовано»)
    и «Требуется уточнение» сюда не входят — туда ведут действия со своими
    обязательными данными, а не перестановка.
    """
    item = subtask(kind)
    return bool(item and to_status in item.get('statuses', ()) and kind != KIND_INITIATION)


# ─── Доски (пп. 7–9) ─────────────────────────────────────────────────────────
#
# Доска — не процесс, а вид на подзадачи исполнителя (п. 2). Колонки — дословно
# «рекомендуемые» из ТЗ. `work` — колонки-статусы открытой подзадачи.

BOARDS = (
    {
        'code': BOARD_APPROVAL, 'title': 'Согласование закупа', 'short': 'Согласование',
        'kinds': APPROVAL_KINDS, 'roles': (ROLE_MANAGER, ROLE_APPROVER),
        'columns': (
            ('new', 'Новые'),
            ('review', 'На рассмотрении'),
            ('clarification', 'Требуется уточнение'),
            ('approved', 'Согласовано'),
            ('rejected', 'Отклонено'),
        ),
        'work': ('new', 'review'),
    },
    {
        'code': BOARD_ACCOUNTING, 'title': 'Бухгалтерия / Оплата счетов', 'short': 'Бухгалтерия',
        'kinds': (KIND_INVOICE, KIND_CLOSING), 'roles': (ROLE_ACCOUNTING,),
        'columns': (
            ('new', 'Новые счета'),
            ('checking', 'Проверка'),
            ('clarification', 'Требуется уточнение'),
            ('ready', 'Готово к оплате'),
            ('paying', 'На оплате'),
            ('paid', 'Оплачено'),
            ('awaiting_docs', 'Ожидаются закрывающие документы'),
            ('closed', 'Закрыто'),
        ),
        'work': ('new', 'checking', 'ready', 'paying'),
    },
    {
        'code': BOARD_FINANCE, 'title': 'Финансовый отдел / Пополнение карт', 'short': 'Финансовый отдел',
        'kinds': (KIND_CARD,), 'roles': (ROLE_FINANCE,),
        'columns': (
            ('new', 'Новые'),
            ('in_progress', 'В работе'),
            ('clarification', 'Требуется уточнение'),
            ('ready', 'Готово к пополнению'),
            ('topped_up', 'Пополнено'),
            ('awaiting_receipt', 'Ожидается чек'),
            ('closed', 'Закрыто'),
        ),
        'work': ('new', 'in_progress', 'ready'),
    },
)
BOARD_BY_CODE = {item['code']: item for item in BOARDS}

# Подпись пустой колонки — что в неё попадает, как у колонок раздела «Задачи»
# («Назначены», «Идут сейчас»). Видна, только пока колонка пуста: у заполненной
# это и так понятно по карточкам. «За последние 30 дней» — срок, который
# закрытая заявка стоит на доске (queries.BOARD_CLOSED_DAYS).
COLUMN_CAPTIONS = {
    BOARD_APPROVAL: {
        'new': 'Ждут решения',
        'review': 'Взяты на рассмотрение',
        'clarification': 'Ждут ответа инициатора',
        'approved': 'Ушли на оплату',
        'rejected': 'За последние 30 дней',
    },
    BOARD_ACCOUNTING: {
        'new': 'Согласованные счета',
        'checking': 'Бухгалтерия сверяет счёт',
        'clarification': 'Ждут ответа инициатора',
        'ready': 'Проверены, ждут оплаты',
        'paying': 'Платёж в работе',
        'paid': 'Ждут получения товара',
        'awaiting_docs': 'Ждут накладную или акт',
        'closed': 'За последние 30 дней',
    },
    BOARD_FINANCE: {
        'new': 'Согласованные пополнения',
        'in_progress': 'Взяты в работу',
        'clarification': 'Ждут ответа инициатора',
        'ready': 'Готовы к переводу',
        'topped_up': 'Перевод сделан',
        'awaiting_receipt': 'Ждут чек от сотрудника',
        'closed': 'За последние 30 дней',
    },
}


def board(code):
    return BOARD_BY_CODE.get(code)


def column_caption(board_code, column):
    return COLUMN_CAPTIONS.get(board_code, {}).get(column, '')


def _approval_column(item):
    if not item:
        return None
    state = item.get('state')
    if state == 'done':
        return 'rejected' if item.get('outcome') == 'rejected' else 'approved'
    if state == 'waiting':
        return 'clarification'
    if state == 'open':
        return item.get('status') if item.get('status') in ('new', 'review') else 'new'
    return None


def board_column(board_code, request, subtasks, focus_kind=None):
    """Колонка заявки на доске либо None — заявки на этой доске нет.

    `subtasks` — подзадачи заявки (list of dict). На доске согласования у заявки
    может быть две подзадачи (руководитель и утверждающий); `focus_kind`
    называет ту, глазами чьего исполнителя смотрим. Отменённая заявка с досок
    уходит: по ней ни от кого ничего не ждут.
    """
    by_kind = {item['kind']: item for item in subtasks or []}
    status = (request or {}).get('status') or 'active'
    if status == 'cancelled':
        return None

    if board_code == BOARD_APPROVAL:
        if focus_kind:
            column = _approval_column(by_kind.get(focus_kind))
            # Отклонённая заявка не бывает «новой»: по ней никто ничего не ждёт.
            return 'rejected' if status == 'rejected' and column in ('new', 'review', 'clarification') else column
        # Без фокуса — «где согласование сейчас»: открытая подзадача, иначе последняя.
        for kind in APPROVAL_KINDS:
            item = by_kind.get(kind)
            if item and item.get('state') in ('open', 'waiting'):
                return _approval_column(item)
        for kind in reversed(APPROVAL_KINDS):
            column = _approval_column(by_kind.get(kind))
            if column:
                return column
        return None

    if status == 'rejected':
        return None

    if board_code == BOARD_ACCOUNTING:
        closing = by_kind.get(KIND_CLOSING) or {}
        if closing.get('state') == 'done':
            return 'closed'
        if closing.get('state') in ('open', 'waiting'):
            return 'awaiting_docs'
        invoice = by_kind.get(KIND_INVOICE) or {}
        if invoice.get('state') == 'done':
            return 'paid'
        if invoice.get('state') == 'waiting':
            return 'clarification'
        if invoice.get('state') == 'open':
            return invoice.get('status') if invoice.get('status') in ('new', 'checking', 'ready', 'paying') else 'new'
        return None

    if board_code == BOARD_FINANCE:
        card = by_kind.get(KIND_CARD) or {}
        if card.get('state') == 'waiting':
            return 'clarification'
        if card.get('state') == 'open':
            return card.get('status') if card.get('status') in ('new', 'in_progress', 'ready') else 'new'
        if card.get('state') != 'done':
            return None
        # Карта пополнена. Карта сотрудника ждёт чек (п. 5.3) и закрывается с
        # ним; по карте поставщика отдел свою подзадачу закрыл (п. 5.4) —
        # заявка стоит в «Пополнено», пока не закрыта целиком.
        receipt = by_kind.get(KIND_RECEIPT) or {}
        if receipt.get('state') in ('pending', 'open', 'waiting'):
            return 'awaiting_receipt'
        if receipt.get('state') == 'done':
            return 'closed'
        return 'closed' if status == 'done' else 'topped_up'

    return None


# ─── Причины «Запросить информацию» (п. 8) ───────────────────────────────────
#
# Бухгалтер и финансовый отдел выбирают причину из списка, а не пишут с нуля:
# так инициатор сразу видит, чего от него хотят. Системные причины (`system`)
# человек не выбирает — их ставит сам раздел, когда заявка пришла на оплату без
# счёта или без договора.

CLARIFY_REASONS = (
    {'code': 'invoice_missing', 'label': 'Счёт не приложен', 'kinds': (KIND_INVOICE,), 'system': True,
     'ask': 'Приложите счёт поставщика и укажите его номер и дату.'},
    {'code': 'contract_required', 'label': 'Нужен действующий договор', 'kinds': (KIND_INVOICE,), 'system': True,
     'ask': 'Укажите действующий договор с поставщиком.'},
    {'code': 'invoice_unreadable', 'label': 'Счёт не читается или это не счёт', 'kinds': (KIND_INVOICE,),
     'ask': 'Приложите читаемый счёт на оплату.'},
    {'code': 'amount_mismatch', 'label': 'Сумма не совпадает с согласованной', 'kinds': (KIND_INVOICE, KIND_CARD),
     'ask': 'Проверьте сумму: в заявке и в документе она должна совпадать.'},
    {'code': 'requisites', 'label': 'Неверные или неполные реквизиты', 'kinds': (KIND_INVOICE,),
     'ask': 'Уточните у поставщика реквизиты и поправьте счёт.'},
    {'code': 'payer', 'label': 'Счёт выставлен не на ту компанию', 'kinds': (KIND_INVOICE,),
     'ask': 'Счёт должен быть выставлен на компанию-плательщика из заявки.'},
    {'code': 'vat', 'label': 'Нужно уточнить НДС', 'kinds': (KIND_INVOICE,),
     'ask': 'Уточните у поставщика, с НДС счёт или без.'},
    {'code': 'duplicate', 'label': 'Похоже на уже оплаченный счёт', 'kinds': (KIND_INVOICE,),
     'ask': 'Проверьте, не оплачен ли этот счёт раньше.'},
    {'code': 'card', 'label': 'Неверные данные карты', 'kinds': (KIND_CARD,),
     'ask': 'Проверьте номер карты и имя получателя.'},
    {'code': 'purpose', 'label': 'Непонятно назначение перевода', 'kinds': (KIND_CARD,),
     'ask': 'Опишите, на что пойдут деньги.'},
    {'code': 'other', 'label': 'Другое', 'kinds': (KIND_INVOICE, KIND_CARD),
     'ask': ''},
)
CLARIFY_BY_CODE = {item['code']: item for item in CLARIFY_REASONS}
# Возврат на доработку согласующим (п. 7): причина — его комментарий.
CLARIFY_REWORK = 'rework'


def clarify_reasons_for(kind):
    """Причины, которые исполнитель подзадачи выбирает сам."""
    return [item for item in CLARIFY_REASONS if kind in item['kinds'] and not item.get('system')]


def clarify_label(code):
    if code == CLARIFY_REWORK:
        return 'Возврат на доработку'
    return (CLARIFY_BY_CODE.get(code) or {}).get('label') or 'Требуется уточнение'


# ─── Вложения ────────────────────────────────────────────────────────────────

ATTACHMENT_LABELS = {
    'supplier_registry': 'Реестр поставщиков',
    'offer': 'Коммерческое предложение',
    'invoice': 'Счёт на оплату',
    'payment_order': 'Платёжное поручение',
    'transfer_proof': 'Подтверждение перевода',
    'receipt': 'Чек',
    'act': 'Акт (АВР)',
    'waybill': 'Накладная',
    'handover_act': 'Акт приёма-передачи',
    'power_of_attorney': 'Доверенность',
    'contract': 'Договор',
    'other': 'Другое',
}
# Закрывающие документы (п. 14): накладная, акт, чек.
CLOSING_DOC_KINDS = ('act', 'waybill', 'receipt')

# Форма поставщика — те же коды, что у контрагентов в справочнике.
SUPPLIER_KIND_LABELS = {'too': 'ТОО', 'ip': 'ИП', 'other': 'Другое'}


def to_decimal(value, default=Decimal('0')):
    """Деньги приходят числом, строкой с пробелами и запятой, Decimal — сводим к Decimal.
    Не число (пусто, «abc», «NaN», «Infinity») — `default`."""
    if value is None or value == '':
        return default
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value)
        for gap in (' ', ' ', ' ', ' ', '₸', 'тг'):
            text = text.replace(gap, '')
        try:
            number = Decimal(text.replace(',', '.'))
        except (InvalidOperation, ValueError):
            return default
    # «NaN» и «Infinity» Decimal разбирает как числа, а сравнение и округление
    # на них падают — для денег это не значение.
    return number if number.is_finite() else default


# ─── Позиции: количество × цена ──────────────────────────────────────────────
#
# Одна формула на всё: строка = количество × цена за единицу, округлённая до
# тиына; сумма заявки = сумма строк. Так же считает форма (itemTotal в
# paymentsMeta.js) — итог на экране и в сохранённой заявке совпадает до тиына.
# Раньше строка округлялась отдельно, а итог — один раз по неокруглённым
# произведениям: при дробном количестве «1,5 × 33,33» дважды строки давали
# 50 + 50, а итог заявки — 99,99.

QUANTITY_STEP = Decimal('0.001')   # столько знаков хранит база
MONEY_STEP = Decimal('0.01')
MAX_QUANTITY = Decimal('999999999.999')
MAX_MONEY = Decimal('999999999999.99')


def _rounded(number, step, limit):
    if abs(number) > limit:
        # Больше, чем вмещает база: округлять такое нельзя (quantize падает на
        # слишком длинном числе). Отдаём «чуть больше предела» — проверка полей
        # заявки назовёт позицию по имени, а не уронит запрос.
        return (limit + step).copy_sign(number)
    return number.quantize(step, rounding=ROUND_HALF_UP)


def item_quantity(item):
    """Количество позиции — до тысячных; не указано — одна единица."""
    return _rounded(to_decimal((item or {}).get('quantity'), Decimal('1')), QUANTITY_STEP, MAX_QUANTITY)


def item_price(item):
    """Цена за единицу — до тиына."""
    return _rounded(to_decimal((item or {}).get('unit_price')), MONEY_STEP, MAX_MONEY)


def item_total(item):
    """Сумма строки: количество × цена за единицу, до тиына, половина — вверх."""
    return (item_quantity(item) * item_price(item)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def items_total(items):
    """Сумма заявки — сумма её строк, каждая округлена сама (как в счёте поставщика)."""
    return sum((item_total(item) for item in items or []), Decimal('0')).quantize(MONEY_STEP)


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
                       'передан в оплату.')
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
                       'Счёт не может быть передан в оплату до указания '
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


# ─── Матрица согласования (п. 6) ─────────────────────────────────────────────
#
# «Счета и закупы могут подтверждать Зарина или Сауле. Для каждого согласующего
# должны настраиваться поставщики и лимиты, в пределах которых заявка
# автоматически направляется нужному лицу». Матрица учитывает компанию,
# подразделение, поставщика, тип расхода, сумму, лимит согласования, тип
# платежа и регулярность платежа.
#
# Откуда берётся согласующий — по порядку, первый подошедший:
#   1. регулярный платёж: его согласующий в пределах его лимита (п. 4.4);
#   2. карточка поставщика: согласующий и лимит согласования (п. 13.1);
#   3. справочник «Лимиты согласования» (п. 13) — самая точная подошедшая
#      строка, при равной точности — с наименьшим достаточным лимитом;
#   4. справочник «Маршруты согласования» (п. 13) — утверждающий по умолчанию
#      для заявок этого класса;
#   5. роль «Утвердитель» целиком: заявку берёт любой из утверждающих.
# Порядок ТЗ не задаёт; он идёт от частного к общему, и карточка заявки всегда
# показывает, по какому основанию выбран согласующий и почему не подошли
# остальные.

def limit_label(limit):
    """Как лимит называется в карточке: «Приказ №15 от 01.09.2026» либо
    «лимит согласования (Иванов И.)», если основания-Приказа у него нет."""
    number = str(limit.get('number') or '').strip()
    if number:
        issued = _as_date(limit.get('issued_on'))
        return 'Приказ №%s%s' % (number, ' от %s' % fmt_date(issued) if issued else '')
    name = limit.get('delegate_name') or 'согласующий не указан'
    return 'Лимит согласования (%s)' % name


def evaluate_limit(limit, *, request, on_date):
    """Проверяет ОДНУ строку «Лимитов согласования» по всем условиям и объясняет каждое.

    Возвращает {'limit_id', 'label', 'applies', 'checks': [...], 'reason'}:
    `checks` — по одному на условие, `reason` — первое невыполненное словами
    («Приказ №15 не применён. Причина: …» — требование п. 12 ТЗ о Приказах).
    Пустое условие строки означает «любое» и в проверки не попадает.
    """
    total = to_decimal(request.get('amount'))
    checks = []

    def add(key, label, ok, detail):
        checks.append({'key': key, 'label': label, 'ok': bool(ok), 'detail': detail})

    status = str(limit.get('status') or '').lower()
    add('status', 'Лимит действует', status == 'active',
        'статус «действующий»' if status == 'active' else 'лимит отменён или деактивирован')

    starts_on = _as_date(limit.get('starts_on'))
    ends_on = _as_date(limit.get('ends_on'))
    in_period = (starts_on is None or starts_on <= on_date) and (ends_on is None or ends_on >= on_date)
    add('period', 'Срок действия', in_period,
        'действует %s — %s' % (fmt_date(starts_on) if starts_on else '…',
                                fmt_date(ends_on) if ends_on else 'бессрочно')
        if in_period else 'срок действия не покрывает дату %s' % fmt_date(on_date))

    for key, label, miss in (
        ('legal_entity_id', 'Компания', 'заявка другой компании'),
        ('department_id', 'Подразделение', 'заявка другого подразделения'),
        ('category_id', 'Тип расхода', 'у заявки другая категория закупа'),
    ):
        wanted = limit.get(key)
        if wanted in (None, ''):
            continue
        ok = request.get(key) is not None and int(request.get(key)) == int(wanted)
        add(key, label, ok, 'совпадает' if ok else miss)

    wanted_method = limit.get('payment_method') or None
    if wanted_method:
        ok = request.get('payment_method') == wanted_method
        add('payment_method', 'Тип платежа', ok,
            PAYMENT_METHOD_LABELS.get(wanted_method, wanted_method).lower() if ok
            else 'лимит только для способа «%s»' % PAYMENT_METHOD_LABELS.get(wanted_method, wanted_method))
    wanted_kind = limit.get('request_kind') or None
    if wanted_kind:
        ok = request.get('request_kind') == wanted_kind
        add('request_kind', 'Регулярность платежа', ok,
            REQUEST_KIND_LABELS.get(wanted_kind, wanted_kind).lower() if ok
            else 'лимит только для заявок «%s»' % REQUEST_KIND_LABELS.get(wanted_kind, wanted_kind))

    project_ids = {int(x) for x in (limit.get('project_ids') or [])}
    project_id = request.get('project_id')
    project_ok = bool(limit.get('all_projects')) or (project_id is not None and int(project_id) in project_ids)
    add('project', 'Проект соответствует', project_ok,
        'все проекты' if limit.get('all_projects') else
        ('проект входит в лимит' if project_ok else
         'проект заявки не входит в область действия лимита'))

    cp_ids = {int(x) for x in (limit.get('counterparty_ids') or [])}
    counterparty_id = request.get('counterparty_id')
    cp_ok = bool(limit.get('all_counterparties')) or (counterparty_id is not None and int(counterparty_id) in cp_ids)
    add('counterparty', 'Поставщик соответствует', cp_ok,
        'все поставщики' if limit.get('all_counterparties') else
        ('поставщик входит в лимит' if cp_ok else
         'поставщик «%s» отсутствует в перечне поставщиков лимита'
         % (request.get('counterparty_name') or 'по заявке')))

    amount_limit = limit.get('amount_limit')
    limit_ok = amount_limit in (None, '') or total <= to_decimal(amount_limit)
    add('limit', 'Лимит не превышен', limit_ok,
        'без лимита суммы' if amount_limit in (None, '') else
        ('лимит %s' % fmt_money(to_decimal(amount_limit)) if limit_ok else
         'сумма заявки %s превышает лимит %s' % (fmt_money(total), fmt_money(to_decimal(amount_limit)))))

    applies = all(check['ok'] for check in checks)
    failed = next((check for check in checks if not check['ok']), None)
    label = limit_label(limit)
    return {
        'limit_id': limit.get('id'),
        'label': label,
        'number': limit.get('number') or None,
        'issued_on': fmt_date(_as_date(limit.get('issued_on'))) if limit.get('issued_on') else None,
        'approver_user_id': limit.get('delegate_user_id'),
        'approver_name': limit.get('delegate_name'),
        'applies': applies,
        'checks': checks,
        'reason': None if applies else '%s не применён. Причина: %s.' % (label, failed['detail']),
    }


def _limit_priority(limit):
    """Ключ сортировки лимитов: точнее — раньше; при равной точности — меньший
    лимит суммы (классическая лестница «до 500 тыс. — один, свыше — другой»),
    затем более новый."""
    specificity = sum(1 for key in ('legal_entity_id', 'department_id', 'category_id',
                                    'payment_method', 'request_kind') if limit.get(key) not in (None, ''))
    specificity += 0 if limit.get('all_projects') else 1
    specificity += 0 if limit.get('all_counterparties') else 1
    amount_limit = limit.get('amount_limit')
    issued = _as_date(limit.get('issued_on')) or _as_date(limit.get('starts_on')) or date.min
    return (
        -specificity,
        to_decimal(amount_limit) if amount_limit not in (None, '') else Decimal('Infinity'),
        -issued.toordinal(),
        -int(limit.get('id') or 0),
    )


def _route_matches(route, request):
    """Подходит ли строка «Маршрутов согласования» заявке. Пустое условие — любое."""
    for key in ('legal_entity_id', 'department_id', 'category_id'):
        wanted = route.get(key)
        if wanted in (None, ''):
            continue
        if request.get(key) is None or int(request.get(key)) != int(wanted):
            return False
    for key in ('payment_method', 'request_kind'):
        wanted = route.get(key) or None
        if wanted and request.get(key) != wanted:
            return False
    total = to_decimal(request.get('amount'))
    amount_from, amount_to = route.get('amount_from'), route.get('amount_to')
    if amount_from not in (None, '') and total < to_decimal(amount_from):
        return False
    if amount_to not in (None, '') and total > to_decimal(amount_to):
        return False
    return True


def _route_priority(route):
    specificity = sum(1 for key in ('legal_entity_id', 'department_id', 'category_id', 'payment_method',
                                    'request_kind', 'amount_from', 'amount_to')
                      if route.get(key) not in (None, ''))
    return (-specificity, int(route.get('position') or 0), int(route.get('id') or 0))


def pick_route(routes, request):
    """Самая точная действующая строка маршрутов, подходящая заявке, либо None."""
    matching = [route for route in routes or []
                if route.get('is_active', True) and _route_matches(route, request)]
    return sorted(matching, key=_route_priority)[0] if matching else None


def manager_step_needed(route, request):
    """(нужен ли этап руководителя, основание словами).

    П. 3: руководитель проверяет целесообразность закупа и выбор поставщика,
    «если данный этап предусмотрен». Предусмотрен он строкой маршрута; когда
    маршрут молчит — у нового закупа этап есть, а у регулярного платежа нет:
    поставщик и условия там уже утверждены (п. 4), проверять нечего.
    """
    mode = str((route or {}).get('manager_step') or 'auto')
    if mode == 'required':
        return True, 'по маршруту «%s»' % route.get('name')
    if mode == 'skip':
        return False, 'маршрут «%s» не предусматривает этап руководителя' % route.get('name')
    if request.get('request_kind') == KIND_REGULAR:
        return False, 'регулярный платёж: поставщик и условия уже утверждены'
    return True, 'новый закуп'


def resolve_approval(*, request, counterparty=None, template=None, limits=(), routes=(), on_date=None):
    """Кому заявка уходит на утверждение и на каком основании.

    `request` — поля заявки (amount, counterparty_id, counterparty_name,
    legal_entity_id, department_id, category_id, project_id, payment_method,
    request_kind). Возвращает основание для карточки:

        approver_user_id / approver_name — конкретный утверждающий либо None
                                           (тогда заявку берёт любой из роли);
        source — template / supplier / limit / route / role;
        basis — основание одной строкой;
        manager_step, manager_reason — нужен ли этап руководителя;
        trace — что проверено по порядку и почему не подошло (под «i»).
    """
    on_date = on_date or date.today()
    total = to_decimal(request.get('amount'))
    trace = []
    route = pick_route(routes, request)
    manager_step, manager_reason = manager_step_needed(route, request)
    basis = {
        'approver_user_id': None, 'approver_name': None, 'source': 'role',
        'basis': 'Любой из утверждающих', 'limit_id': None, 'limit_number': None,
        'route_id': (route or {}).get('id'), 'route_name': (route or {}).get('name'),
        'manager_step': manager_step, 'manager_reason': manager_reason,
        'on_date': fmt_date(on_date), 'amount': str(total), 'conflict': False,
        'evaluations': [], 'trace': trace,
    }

    def chosen(user_id, name, source, text, **extra):
        basis.update({'approver_user_id': user_id, 'approver_name': name, 'source': source, 'basis': text})
        basis.update(extra)
        return basis

    def within(limit):
        return limit in (None, '') or total <= to_decimal(limit)

    # 1. Регулярный платёж.
    if request.get('request_kind') == KIND_REGULAR and template and template.get('approver_user_id'):
        limit = template.get('amount_limit')
        if within(limit):
            trace.append({'ok': True, 'text': 'Регулярный платёж «%s»: согласующий задан в справочнике%s'
                          % (template.get('name') or '', '' if limit in (None, '') else
                             ', сумма в пределах лимита %s' % fmt_money(limit))})
            return chosen(template['approver_user_id'], template.get('approver_name'), 'template',
                          'По регулярному платежу «%s»' % (template.get('name') or ''))
        trace.append({'ok': False, 'text': 'Регулярный платёж «%s»: сумма %s превышает лимит %s'
                      % (template.get('name') or '', fmt_money(total), fmt_money(limit))})

    # 2. Карточка поставщика.
    if counterparty and counterparty.get('approver_user_id'):
        limit = counterparty.get('approval_limit')
        if within(limit):
            trace.append({'ok': True, 'text': 'Поставщик «%s» закреплён за согласующим%s'
                          % (counterparty.get('name') or '', '' if limit in (None, '') else
                             ', сумма в пределах лимита %s' % fmt_money(limit))})
            return chosen(counterparty['approver_user_id'], counterparty.get('approver_name'), 'supplier',
                          'Поставщик «%s» закреплён за согласующим' % (counterparty.get('name') or ''))
        trace.append({'ok': False, 'text': 'Поставщик «%s»: сумма %s превышает лимит согласования %s'
                      % (counterparty.get('name') or '', fmt_money(total), fmt_money(limit))})

    # 3. Лимиты согласования.
    evaluations = [(limit, evaluate_limit(limit, request=request, on_date=on_date))
                   for limit in sorted(limits or [], key=_limit_priority)]
    basis['evaluations'] = [ev for _limit, ev in evaluations]
    applicable = [(limit, ev) for limit, ev in evaluations if ev['applies']]
    if applicable:
        top_key = _limit_priority(applicable[0][0])[:2]
        rivals = {int(limit.get('delegate_user_id') or 0) for limit, _ev in applicable
                  if _limit_priority(limit)[:2] == top_key}
        limit, ev = applicable[0]
        trace.append({'ok': True, 'text': '%s — применён' % ev['label']})
        return chosen(limit.get('delegate_user_id'), limit.get('delegate_name'), 'limit',
                      'По основанию: %s' % ev['label'], limit_id=limit.get('id'),
                      limit_number=ev.get('number'), conflict=len(rivals) > 1)
    for _limit, ev in evaluations:
        trace.append({'ok': False, 'text': ev['reason']})

    # 4. Маршрут по умолчанию.
    if route and route.get('approver_user_id'):
        trace.append({'ok': True, 'text': 'Маршрут «%s»: утверждающий по умолчанию' % route.get('name')})
        return chosen(route['approver_user_id'], route.get('approver_name'), 'route',
                      'По маршруту «%s»' % route.get('name'))

    # 5. Роль.
    trace.append({'ok': True, 'text': 'Ни лимит, ни маршрут не назвали согласующего — заявку берёт любой из утверждающих'})
    return basis


# ─── Существенные условия ────────────────────────────────────────────────────
#
# То, что утверждали согласующие. Если инициатор, получив заявку на доработку
# или уточнение, изменил что-то из этого — заявка согласуется заново: иначе
# можно пройти согласование на 250 000 ₸ и исправить сумму на 900 000 ₸. Номер
# карты в снимок не попадает (он зашифрован) — его смену отмечает флаг `stale`.

ESSENTIAL_FIELDS = (
    'request_kind', 'fixed_template_id', 'amount', 'counterparty_id', 'legal_entity_id',
    'department_id', 'category_id', 'project_id', 'payment_method', 'card_recipient',
    'card_holder_name', 'card_last4', 'object_type', 'accounting_category',
)


def essentials(request):
    snapshot = {}
    for key in ESSENTIAL_FIELDS:
        value = (request or {}).get(key)
        if key == 'amount':
            value = str(to_decimal(value).quantize(MONEY_STEP))
        elif isinstance(value, Decimal):
            value = str(value)
        snapshot[key] = value if value not in ('',) else None
    return snapshot


def accepted_before(request):
    """Заявка принята в работу ещё первой версией процесса (перенос, legacy.py):
    к моменту переноса она уже была отправлена на согласование."""
    return int((request or {}).get('legacy_step') or 0) >= 2


def _essentials_delta(snapshot, request):
    """Существенные условия, которые разошлись со снимком.

    У заявки первой версии части полей не было вовсе (подразделение, категория,
    у шага 5 — компания): заполнить пустое — не «сменить условия», иначе уже
    утверждённая заявка ушла бы на согласование заново из-за новой формы.
    """
    current = essentials(request)
    filled_only = accepted_before(request)
    return [key for key in ESSENTIAL_FIELDS
            if current.get(key) != snapshot.get(key) and not (filled_only and snapshot.get(key) is None)]


def essentials_changed(snapshot, request):
    """Изменились ли существенные условия с прошлой отправки. Нет снимка — да."""
    if not snapshot or snapshot.get('stale'):
        return True
    return bool(_essentials_delta(snapshot, request))


ESSENTIAL_LABELS = {
    'request_kind': 'тип заявки', 'fixed_template_id': 'регулярный платёж', 'amount': 'сумма',
    'counterparty_id': 'поставщик', 'legal_entity_id': 'компания', 'department_id': 'подразделение',
    'category_id': 'категория закупа', 'project_id': 'проект', 'payment_method': 'способ оплаты',
    'card_recipient': 'получатель по карте', 'card_holder_name': 'владелец карты',
    'card_last4': 'номер карты', 'object_type': 'тип объекта', 'accounting_category': 'категория учёта',
}


def essentials_diff(snapshot, request):
    """Какие существенные условия изменились — словами, для записи в историю."""
    if not snapshot:
        return []
    changed = [ESSENTIAL_LABELS[key] for key in _essentials_delta(snapshot, request)]
    if snapshot.get('stale') and 'номер карты' not in changed:
        changed.append('номер карты')
    return changed


# ─── Маршрут ─────────────────────────────────────────────────────────────────

def needs_asset_registration(request):
    return (request.get('object_type') == OBJECT_GOODS
            and request.get('accounting_category') == ACCOUNTING_ASSET)


def needs_receipt(request):
    """Чек нужен, когда пополнили карту сотрудника (п. 5.3)."""
    return (request.get('payment_method') == METHOD_CARD
            and request.get('card_recipient') == CARD_EMPLOYEE)


def build_route(request, *, initiator, manager=None, manager_step=True, approver=None):
    """Подзадачи заявки по порядку — этапы п. 2 для ЭТОЙ заявки.

    initiator / manager / approver — {'id', 'name'} либо None. Что влияет:
      * этап руководителя — если предусмотрен (`manager_step`); предусмотрен,
        но руководитель у инициатора не определён (владелец, глава без
        начальника) — подзадача ПРОПУСКАЕТСЯ с пометкой, иначе заявка зависла
        бы навсегда; утверждающий всё равно решает;
      * оплата — счёт (бухгалтерия) либо карта (финансовый отдел);
      * чек — только после пополнения карты сотрудника;
      * постановка на учёт — только у товара-имущества.
    Вида подзадачи, которого в маршруте нет, в списке нет вовсе.
    """
    route = []

    def add(kind, *, assignee=None, state='pending', comment=None):
        item = SUBTASK_BY_KIND[kind]
        route.append({
            'kind': kind, 'position': item['position'], 'stage': item['stage'], 'role_code': item['role'],
            'assignee_id': (assignee or {}).get('id'), 'assignee_name': (assignee or {}).get('name'),
            'state': state, 'comment': comment,
        })

    add(KIND_INITIATION, assignee=initiator)
    if manager_step:
        if manager and manager.get('id'):
            add(KIND_MANAGER, assignee=manager)
        else:
            add(KIND_MANAGER, state='skipped',
                comment='Этап пропущен: у инициатора не определён непосредственный руководитель')
    add(KIND_APPROVAL, assignee=approver if approver and approver.get('id') else None)
    if request.get('payment_method') == METHOD_CARD:
        add(KIND_CARD)
        if needs_receipt(request):
            add(KIND_RECEIPT, assignee=initiator)
    else:
        add(KIND_INVOICE)
    add(KIND_RECEIVING, assignee=initiator)
    if needs_asset_registration(request):
        add(KIND_ASSETS)
    add(KIND_CLOSING)
    return route


def lifecycle(request, subtasks):
    """Этапы жизненного цикла для полосы в карточке: [{code, label, state}].

    state: done — пройден, current — заявка здесь, pending — впереди, skipped —
    у этой заявки этапа нет (учёт имущества у услуги и расходников).
    """
    by_stage = {}
    for item in subtasks or []:
        by_stage.setdefault(item['stage'], []).append(item)
    status = (request or {}).get('status') or 'active'
    active = status == 'active'
    current_stage = (request or {}).get('stage') or STAGE_INITIATION
    # Заявку вернули инициатору: «текущим» остаётся этап, на котором попросили
    # уточнение (его подзадача ждёт), а не «Инициация» — она давно пройдена.
    returned = current_stage == STAGE_INITIATION and bool((request or {}).get('submitted_at'))
    result = []
    for code, label in STAGES:
        if code == STAGE_CLOSED:
            state = 'done' if status == 'done' else 'pending'
        elif code == STAGE_INITIATION:
            state = 'current' if active and current_stage == code and not returned else 'done'
        else:
            items = [item for item in by_stage.get(code, []) if item.get('state') != 'skipped']
            if not items:
                state = 'skipped'
            elif all(item.get('state') == 'done' for item in items):
                state = 'done'
            elif active and current_stage == code:
                state = 'current'
            elif active and returned and any(item.get('state') == 'waiting' for item in items):
                state = 'current'
            else:
                state = 'pending'
        result.append({'code': code, 'label': label, 'state': state})
    return result


# ─── Что нужно, чтобы отправить заявку (п. 4, п. 5) ──────────────────────────

def setting(settings, key):
    """Значение настройки с подстраховкой: мусор и выход за границы — значение по умолчанию."""
    default = SETTINGS_DEFAULTS[key]
    low, high = SETTINGS_LIMITS[key]
    try:
        value = int((settings or {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def missing_for_submit(request, *, items=(), offers=(), attachments=(), settings=None, card_number_ok=True,
                       invoice_required=True):
    """Чего не хватает заявке, чтобы уйти на согласование. Пустой список — можно.

    Цель ТЗ — «исключить подачу заявок с неполными данными», поэтому поля
    заявки из п. 4 обязательны. У варианта поставщика (п. 4.2) обязательны
    название и стоимость; условия, ссылка, комментарий и вложение ТЗ
    обязательными не называет — их заполняют, когда они есть.

    `attachments` — вложения заявки (list of {'kind'}); `card_number_ok` — номер
    карты введён и годится (сам номер сюда не передаётся). `invoice_required` —
    требовать ли счёт у регулярного платежа: человек прикладывает его сразу
    (п. 4.4), а календарь создаёт заявку без счёта — его запросят перед оплатой.
    """
    missing = []
    kind = request.get('request_kind')
    method = request.get('payment_method')
    kinds = {str(att.get('kind') or 'other') for att in (attachments or [])}

    if kind not in REQUEST_KIND_LABELS:
        return ['Выберите тип заявки: новый закуп или оплата по регулярному обязательству']
    if accepted_before(request):
        # Принята и согласована по правилам первой версии: трёх поставщиков,
        # обоснования и срока от неё не требовали. Нужно только то, без чего её
        # не оплатить: компания-плательщик, сумма и счёт.
        if not request.get('legal_entity_id'):
            missing.append('Выберите компанию-плательщика')
        if to_decimal(request.get('amount')) <= 0:
            missing.append('Сумма заявки должна быть больше нуля')
        if method != METHOD_CARD and invoice_required:
            missing += _invoice_missing(request, kinds)
        return missing
    if not request.get('legal_entity_id'):
        missing.append('Выберите компанию-плательщика')
    if not request.get('department_id'):
        missing.append('Укажите подразделение')
    if not str(request.get('expense_name') or '').strip():
        missing.append('Укажите наименование закупа')
    if to_decimal(request.get('amount')) <= 0:
        missing.append('Сумма заявки должна быть больше нуля')
    if not items:
        missing.append('Добавьте хотя бы одну позицию: что, сколько и по какой цене')

    if kind == KIND_REGULAR:
        # П. 4.4: поставщика, договор, реквизиты и назначение подставляет
        # справочник; инициатор указывает период, сумму и счёт.
        if not request.get('fixed_template_id'):
            missing.append('Выберите регулярный платёж из справочника')
        if not request.get('counterparty_id'):
            missing.append('У регулярного платежа не указан поставщик — дополните справочник')
        if not str(request.get('payment_period') or '').strip():
            missing.append('Укажите период оплаты')
        if (request.get('object_type') == OBJECT_GOODS
                and request.get('accounting_category') not in ACCOUNTING_CATEGORY_LABELS):
            # П. 10 действует и здесь: категорию учёта товара задаёт справочник.
            missing.append('У регулярного платежа не указана категория учёта товара — дополните справочник')
        if invoice_required:
            missing += _invoice_missing(request, kinds)
        return missing

    # ── Новый закуп (п. 4.1) ──
    if not request.get('category_id'):
        missing.append('Выберите категорию закупа')
    if not str(request.get('justification') or '').strip():
        missing.append('Опишите закуп и обоснуйте, зачем он нужен')
    if not request.get('due_on'):
        missing.append('Укажите желаемый срок')
    if request.get('object_type') not in OBJECT_TYPE_LABELS:
        missing.append('Выберите тип объекта: товар или услуга')
    elif request.get('object_type') == OBJECT_GOODS and request.get('accounting_category') not in ACCOUNTING_CATEGORY_LABELS:
        # П. 10: у товара «Категория учёта приобретения» обязательна.
        missing.append('Выберите категорию учёта: расходный материал или имущество, подлежащее учёту')
    if method not in PAYMENT_METHOD_LABELS:
        missing.append('Выберите способ оплаты: оплата счёта или пополнение карты')

    # ── Поставщики (пп. 4.2–4.3) ──
    offers = list(offers or [])
    incomplete = [index + 1 for index, offer in enumerate(offers)
                  if not str(offer.get('supplier_name') or '').strip() or to_decimal(offer.get('amount')) <= 0]
    recommended = [offer for offer in offers if offer.get('is_recommended')]
    if request.get('no_alternatives'):
        reason = request.get('no_alternatives_reason')
        if reason not in NO_ALTERNATIVES_LABELS:
            missing.append('Выберите причину отсутствия альтернатив')
        elif reason == 'other' and not str(request.get('no_alternatives_comment') or '').strip():
            missing.append('Опишите причину отсутствия альтернатив')
        if not offers:
            missing.append('Укажите поставщика и стоимость')
    else:
        need = setting(settings, SETTING_MIN_SUPPLIERS)
        if len(offers) < need:
            missing.append('Нужно не меньше %s — сейчас %s. Если сравнивать не с кем, отметьте '
                           '«Альтернативные предложения отсутствуют»'
                           % (_plural(need, ('варианта поставщика', 'вариантов поставщиков', 'вариантов поставщиков')),
                              len(offers)))
    if incomplete:
        missing.append('У поставщика №%s укажите название и стоимость' % ', №'.join(str(n) for n in incomplete))
    if offers:
        if len(recommended) != 1:
            missing.append('Отметьте одного рекомендуемого поставщика')
        elif not request.get('counterparty_id'):
            missing.append('Рекомендуемого поставщика нет в справочнике — выберите его из списка или добавьте')
        if not request.get('no_alternatives') and not str(request.get('supplier_choice_reason') or '').strip():
            missing.append('Обоснуйте выбор рекомендуемого поставщика')

    # ── Оплата (п. 5) ──
    if method == METHOD_CARD:
        if request.get('card_recipient') not in CARD_RECIPIENT_LABELS:
            missing.append('Выберите, чью карту пополнить: сотрудника или поставщика')
        if not str(request.get('card_holder_name') or '').strip():
            missing.append('Укажите ФИО сотрудника' if request.get('card_recipient') == CARD_EMPLOYEE
                           else 'Укажите ФИО владельца карты')
        if not card_number_ok:
            missing.append('Укажите полный номер карты')
        if not str(request.get('payment_purpose') or '').strip():
            missing.append('Укажите назначение перевода')
    elif method == METHOD_INVOICE and ('invoice' in kinds or request.get('invoice_number') or request.get('invoice_date')):
        # Счёт у нового закупа обычно появляется после согласования; но если его
        # уже приложили — номер и дата нужны сразу: по ним ищется дубль (п. 8).
        missing += _invoice_missing(request, kinds)
    return missing


def _invoice_missing(request, kinds):
    missing = []
    if 'invoice' not in kinds:
        missing.append('Приложите счёт на оплату')
    if not str(request.get('invoice_number') or '').strip():
        missing.append('Укажите номер счёта')
    if not request.get('invoice_date'):
        missing.append('Укажите дату счёта')
    return missing


def invoice_ready(request, attachments):
    """Есть ли у заявки счёт целиком: файл, номер и дата. Без него оплату не начать."""
    kinds = {str(att.get('kind') or 'other') for att in (attachments or [])}
    return not _invoice_missing(request, kinds)


def _plural(count, forms):
    mod10, mod100 = count % 10, count % 100
    if mod10 == 1 and mod100 != 11:
        return '%s %s' % (count, forms[0])
    if 2 <= mod10 <= 4 and not 10 <= mod100 < 20:
        return '%s %s' % (count, forms[1])
    return '%s %s' % (count, forms[2])


# ─── Действия подзадач ───────────────────────────────────────────────────────
#
# Действие — то, что исполнитель делает с подзадачей. `ends` — действие её
# завершает; остальные оставляют открытой либо возвращают заявку инициатору.

ACTIONS = {
    KIND_INITIATION: ('submit',),
    KIND_MANAGER: ('approve', 'return', 'reject'),
    KIND_APPROVAL: ('approve', 'return', 'reject'),
    KIND_INVOICE: ('pay', 'request_info'),
    KIND_CARD: ('top_up', 'request_info'),
    KIND_RECEIPT: ('provide',),
    KIND_RECEIVING: ('confirm',),
    KIND_ASSETS: ('register',),
    KIND_CLOSING: ('docs_original', 'docs_close'),
}
ACTION_LABELS = {
    'submit': 'Отправить', 'approve': 'Согласовать', 'return': 'Вернуть на доработку',
    'reject': 'Отклонить', 'pay': 'Оплачено', 'top_up': 'Пополнено', 'request_info': 'Запросить информацию',
    'provide': 'Отправить чек', 'confirm': 'Подтвердить получение', 'register': 'Поставить на учёт',
    'docs_original': 'Оригинал получен', 'docs_close': 'Закрыть документы',
}

ASSET_REQUIRED_FIELDS = (
    ('name', 'наименование имущества'),
    ('category_id', 'категория'),
    ('serial_number', 'серийный номер'),
    ('inventory_number', 'инвентарный номер'),
    ('received_on', 'дата получения'),
    ('cost', 'стоимость'),
    ('legal_entity_id', 'компания-владелец'),
    ('city', 'город'),
    ('department_id', 'подразделение'),
    ('responsible_user_id', 'ответственный сотрудник'),
    ('location', 'место эксплуатации'),
    ('status', 'статус имущества'),
)


def asset_missing(asset):
    """Каких обязательных полей не хватает карточке имущества (п. 10.2 — обязательны все)."""
    missing = []
    for key, label in ASSET_REQUIRED_FIELDS:
        value = (asset or {}).get(key)
        if key == 'cost':
            if value in (None, '') or to_decimal(value, None) is None or to_decimal(value) < 0:
                missing.append(label)
        elif key == 'status':
            if value not in ASSET_STATUS_LABELS:
                missing.append(label)
        elif value in (None, '') or (isinstance(value, str) and not value.strip()):
            missing.append(label)
    return missing


def missing_for_action(kind, action, *, request, fields=None, file_kinds=(), assets=()):
    """Чего не хватает, чтобы выполнить действие подзадачи. Пустой список — можно.

    `fields` — то, что прислал исполнитель вместе с действием; `file_kinds` —
    виды файлов, уже лежащих в заявке и приложенных сейчас; `assets` — карточки
    имущества заявки (для постановки на учёт).
    """
    fields = fields or {}
    kinds = set(file_kinds or ())
    missing = []
    comment = str(fields.get('comment') or '').strip()

    if action == 'return' and not comment:
        missing.append('Напишите, что нужно доработать')                # п. 7: комментарий обязателен
    if action == 'reject' and not comment:
        missing.append('Укажите причину отклонения')                    # п. 7: причина обязательна
    if action == 'request_info':
        reason = fields.get('reason')
        allowed = {item['code'] for item in clarify_reasons_for(kind)}
        if reason not in allowed:
            missing.append('Выберите причину запроса')                  # п. 8: «выбирает причину»
        elif reason == 'other' and not comment:
            missing.append('Опишите, какая информация нужна')
    if action in ('pay', 'top_up'):
        # П. 8 и п. 5.3: после оплаты фиксируются дата, фактическая сумма и
        # платёжное поручение (подтверждение перевода); комментарий — по желанию.
        if not fields.get('paid_on'):
            missing.append('Укажите дату оплаты' if action == 'pay' else 'Укажите дату перевода')
        if to_decimal(fields.get('paid_amount')) <= 0:
            missing.append('Укажите фактическую сумму')
        proof = 'payment_order' if action == 'pay' else 'transfer_proof'
        if proof not in kinds:
            missing.append('Приложите платёжное поручение' if action == 'pay'
                           else 'Приложите подтверждение перевода')
    if action == 'provide' and 'receipt' not in kinds:
        missing.append('Приложите чек или другое подтверждение расхода')
    if action == 'confirm':
        # П. 10.1: инициатор подтверждает факт получения, количество и дату.
        if not fields.get('received_on'):
            missing.append('Укажите дату получения')
        if request.get('object_type') == OBJECT_GOODS and not str(fields.get('received_quantity') or '').strip():
            missing.append('Укажите полученное количество')
    if action == 'register':
        assets = list(assets or [])
        if not assets:
            missing.append('Заведите хотя бы одну карточку имущества')
        for index, asset in enumerate(assets, start=1):
            gaps = asset_missing(asset)
            if gaps:
                missing.append('Имущество №%s: не заполнено — %s' % (index, ', '.join(gaps)))
        if 'handover_act' not in kinds:
            missing.append('Приложите акт приёма-передачи')              # п. 11, п. 15
    if action == 'docs_close' and CLOSING_DOC_ORDER.get(request.get('closing_docs_status'), 0) < CLOSING_DOC_ORDER[DOCS_SCAN]:
        missing.append('Закрывающие документы ещё не получены — нужен скан или оригинал')
    return missing


# ─── Условия закрытия (п. 15) ────────────────────────────────────────────────

def closing_conditions(request, subtasks, *, assets=(), has_handover_act=False):
    """Условия закрытия заявки по типу объекта: [{'label', 'ok'}] — дословно п. 15.

    Список показывается в карточке, и по нему же раздел решает, можно ли
    закрыть заявку: маршрут и так ведёт этапы по порядку, это вторая проверка
    на случай ручных правок маршрута.
    """
    by_kind = {item['kind']: item for item in subtasks or []}

    def done(kind):
        return (by_kind.get(kind) or {}).get('state') == 'done'

    goods = request.get('object_type') == OBJECT_GOODS
    approved = done(KIND_APPROVAL) and (by_kind.get(KIND_APPROVAL) or {}).get('outcome') != 'rejected'
    conditions = [
        {'label': 'Закуп согласован', 'ok': approved},
        {'label': 'Оплата проведена', 'ok': done(KIND_INVOICE) or done(KIND_CARD)},
        {'label': 'Товар получен' if goods else 'Услуга получена', 'ok': done(KIND_RECEIVING)},
    ]
    if needs_asset_registration(request):
        assets = list(assets or [])
        conditions += [
            {'label': 'Инвентарный номер присвоен',
             'ok': bool(assets) and all(str(a.get('inventory_number') or '').strip() for a in assets)},
            {'label': 'Акт приёма-передачи приложен', 'ok': bool(has_handover_act)},
            {'label': 'Указаны город, подразделение и ответственный сотрудник',
             'ok': bool(assets) and all(str(a.get('city') or '').strip() and a.get('department_id')
                                        and a.get('responsible_user_id') for a in assets)},
        ]
    conditions.append({'label': 'Закрывающие документы получены',
                       'ok': request.get('closing_docs_status') == DOCS_CLOSED})
    return conditions


def closing_blockers(request, subtasks, *, assets=(), has_handover_act=False):
    return [item['label'] for item in closing_conditions(request, subtasks, assets=assets,
                                                         has_handover_act=has_handover_act) if not item['ok']]


# ─── Состояние заявки ────────────────────────────────────────────────────────

def is_paid(request):
    return bool(request.get('paid_on'))


def request_state(request, today=None):
    """Состояние заявки для строки реестра: done / rejected / cancelled /
    clarification / overdue / active.

    Порядок важен: закрытая заявка не бывает просроченной; заявка, которую
    вернули инициатору, сначала ждёт его ответа — это важнее просрочки. Срок
    считается до оплаты: оплаченная заявка не «просрочена», даже если документы
    ещё собирают.
    """
    status = request.get('status') or 'active'
    if status in ('done', 'rejected', 'cancelled'):
        return status
    if request.get('stage') == STAGE_INITIATION and request.get('submitted_at'):
        return 'clarification'
    today = today or date.today()
    due_on = _as_date(request.get('due_on'))
    if due_on and due_on < today and not is_paid(request):
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
