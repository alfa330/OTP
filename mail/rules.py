"""Правила раздела «Почта»: статусы, переходы, проверка данных, сроки в рабочих днях.

Чистая логика — ни базы, ни Flask: так её проверяют тесты напрямую и так же её
повторяет фронт (`src/components/mail/mailMeta.js`). Коды статусов, видов
документов и ролей — одни и те же в обоих местах; подписи сверяет тест
tests/test_mail_rules.py::FrontMirrorTests.

Откуда что взято:
    статусы и ветки        ТЗ п. 4
    виды документов        макет «Новое отправление» (АВР, АВР возвратный, Договор,
                           Доверенность, Иное) + ТЗ п. 3 про «Иное»
    обязательные поля      ТЗ п. 3; черновик сохраняется неполным (ТЗ п. 5, шаг 3),
                           «Готово к отправке» — только полным
    правила качества       ТЗ п. 14
    сроки                  ТЗ п. 7: рабочие дни от фактической передачи курьеру,
                           контроль по верхней границе зоны, праздники РК
"""

from datetime import date, datetime, timedelta

# ─── Статусы (ТЗ п. 4) ────────────────────────────────────────────────────────

STATUS_LABELS = {
    'draft': 'Черновик',
    'ready': 'Готово к отправке',
    'courier_requested': 'Заявка курьеру',
    'handed_over': 'Передано курьеру',
    'in_transit': 'В пути',
    'delivered': 'Доставлено курьером',
    'accepted': 'Принято и сверено',
    'cancelled': 'Отменено',
    'not_delivered': 'Не удалось вручить',
    'problem': 'Проблема при получении',
    'return_initiated': 'Возврат инициирован',
    'returned': 'Возвращено',
}
STATUSES = tuple(STATUS_LABELS)

# До передачи курьеру карточку правят свободно, отменить её можно только здесь
# («Отменено (до передачи)»).
BEFORE_HANDOVER = ('draft', 'ready', 'courier_requested')
# Посылка у курьера и едет: плитки «В пути» и «Просрочено» (вторая — опоздавшие из них).
ON_THE_WAY = ('handed_over', 'in_transit')
# Срок идёт, пока не вручено, — и у «Не удалось вручить» тоже: в строке он виден.
# Но такое отправление уже «Проблема», и плитка у него одна — «Проблемы», без задвоения.
SLA_RUNNING = ON_THE_WAY + ('not_delivered',)
# Входящая видна получателю «сразу после передачи курьеру» (ТЗ п. 6).
INCOMING_VISIBLE = ('handed_over', 'in_transit', 'delivered', 'accepted', 'not_delivered',
                    'problem', 'return_initiated', 'returned')
FINAL = ('accepted', 'cancelled', 'returned')

# Быстрые вкладки реестра (ТЗ п. 9) — они же плитки «Обзора» в макете.
QUICK_FILTERS = {
    'to_send': {'label': 'Требуют отправки', 'statuses': ('ready', 'courier_requested')},
    'in_transit': {'label': 'В пути', 'statuses': ('handed_over', 'in_transit')},
    'to_reconcile': {'label': 'Ожидают сверки', 'statuses': ('delivered',)},
    'overdue': {'label': 'Просрочено', 'statuses': ON_THE_WAY},
    'problems': {'label': 'Проблемы', 'statuses': ('problem', 'not_delivered', 'return_initiated')},
}

# ─── Справочники формы ────────────────────────────────────────────────────────

DOC_TYPE_LABELS = {
    'avr': 'АВР',
    'avr_return': 'АВР возвратный',
    'contract': 'Договор',
    'power_of_attorney': 'Доверенность',
    'other': 'Иное',
}
COPY_KIND_LABELS = {'original': 'Оригинал', 'copy': 'Копия'}
DELIVERY_TYPE_LABELS = {'standard': 'Стандарт', 'economy': 'Эконом'}
URGENCY_LABELS = {'normal': 'Обычная', 'urgent': 'Срочная'}
# Построчная сверка (ТЗ п. 6): совпало или вид расхождения.
LINE_STATUS_LABELS = {
    'ok': 'Совпадает',
    'shortage': 'Недостача',
    'excess': 'Лишнее',
    'damaged': 'Повреждение',
    'wrong': 'Не тот документ',
}
DISCREPANCY_KINDS = ('shortage', 'excess', 'damaged', 'wrong')
EVENT_LABELS = {
    'created': 'Создано',
    'edited': 'Изменено',
    'status': 'Смена статуса',
    'pickup_failed': 'Забор не состоялся',
    'file': 'Добавлен файл',
}
FIELD_LABELS = {
    'sender_office_id': 'Офис отправки', 'recipient_office_id': 'Офис получения',
    'sender_name': 'Отправитель', 'sender_phone': 'Телефон отправителя',
    'recipient_name': 'Получатель', 'recipient_phone': 'Телефон получателя',
    'recipient_address': 'Адрес получателя', 'recipient_postcode': 'Индекс',
    'payer_entity_id': 'Юрлицо-плательщик', 'delivery_type': 'Тип доставки', 'urgency': 'Срочность',
    'places': 'Мест', 'weight_kg': 'Вес, кг', 'volume_weight_kg': 'Объёмный вес, кг',
    'declared_value': 'Объявленная ценность', 'comment': 'Комментарий',
    'track_number': 'Трек-номер', 'handed_at': 'Передано курьеру', 'due_on': 'Крайний срок',
}
FILE_KIND_LABELS = {
    'waybill': 'Накладная',
    'handover': 'Подтверждение передачи',
    'delivery': 'Подтверждение доставки',
    'problem': 'Фото или акт расхождения',
    'other': 'Другое',
}

# Роли участников (ТЗ п. 11). Вкладка «Участники» выдаёт только «Сотрудник офиса» — ровно с одним
# офисом (решение владельца 13.11.2026: «нам нужны именно сотрудники офисов, которые будут отправлять
# и принимать»); «Администратор» — постановщик ТЗ, заводится схемой. Остальные роли в правах заложены
# на будущее и выдаются только напрямую в базе:
#   coordinator  офисы списком (ТЗ: «своё юрлицо»; у офисов юрлица в портале нет)
#   regional     офисы списком, только чтение
#   central, accounting — все офисы; свой офис — по желанию
ROLE_LABELS = {
    'office': 'Сотрудник офиса',
    'coordinator': 'Координатор почты',
    'regional': 'Региональный руководитель',
    'central': 'Центральный координатор',
    'accounting': 'Бухгалтерия',
    'admin': 'Администратор',
}
ROLES = tuple(ROLE_LABELS)
ROLES_ALL_OFFICES = ('central', 'accounting', 'admin')

# ─── Пределы полей (те же, что в DDL) ────────────────────────────────────────

MAX_ITEMS = 50
MAX_QTY = 99999
MAX_PLACES = 999
MAX_WEIGHT_KG = 9999
MAX_DECLARED_VALUE = 999_999_999_999
OTHER_DESCRIPTION_MAX = 500
_TEXT_LIMITS = {
    'sender_name': 200, 'sender_phone': 32,
    'recipient_name': 200, 'recipient_phone': 32, 'recipient_address': 1000, 'recipient_postcode': 12,
    'comment': 2000, 'track_number': 64, 'doc_number': 64, 'line_note': 1000, 'note': 2000,
}


class MailError(Exception):
    """Ошибка, которую видит человек: текст по-русски, код для фронта, поля с ошибкой."""

    def __init__(self, message, code='MAIL_INVALID', status=400, fields=None, extra=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.fields = fields or {}
        self.extra = extra or {}


# ─── Разбор значений ─────────────────────────────────────────────────────────

def text(value, limit, *, field=None, errors=None):
    """Строка без краевых пробелов; пустая — None. Длиннее предела — ошибка поля."""
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    if len(result) > limit:
        if errors is not None and field:
            errors[field] = 'Не длиннее %d символов' % limit
            return result[:limit]
        raise MailError('Слишком длинное значение (не больше %d символов)' % limit)
    return result


def int_or_none(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('bool')
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise ValueError('fraction')
        return int(value)
    return int(str(value).strip())


def number_or_none(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('bool')
    result = float(str(value).strip().replace(',', '.').replace(' ', ''))
    if result != result or result in (float('inf'), float('-inf')):
        raise ValueError('nan')
    return result


def date_or_none(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def datetime_or_none(value):
    """Дата и время по Алматы без пояса — как всё время раздела.

    Принимает «2026-11-13T15:30», «2026-11-13 15:30» и с секундами; хвост с поясом
    отрезаем: время передачи курьеру человек вводит по своим часам в Казахстане.
    """
    if value is None or value == '':
        return None
    if not isinstance(value, datetime):
        raw = str(value).strip().replace(' ', 'T')
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        value = datetime.fromisoformat(raw)
    if value.tzinfo is not None:
        # С поясом — переводим в Алматы (UTC+5, без перевода часов) и снимаем пояс:
        # сравнивать время с поясом и без Python не умеет.
        value = (value - value.utcoffset()).replace(tzinfo=None) + timedelta(hours=5)
    return value.replace(microsecond=0)


def bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'да')


# ─── Отправление: данные карточки ────────────────────────────────────────────

SHIPMENT_FIELDS = (
    'sender_office_id', 'recipient_office_id', 'sender_name', 'sender_phone',
    'recipient_name', 'recipient_phone', 'recipient_address', 'recipient_postcode',
    'payer_entity_id', 'delivery_type', 'urgency', 'places', 'weight_kg',
    'volume_weight_kg', 'declared_value', 'comment',
)


def clean_item(raw, index, errors):
    """Одна строка содержимого. Ошибки пишутся в errors с ключом items.<n>.<поле>."""
    raw = raw if isinstance(raw, dict) else {}
    key = 'items.%d.' % index
    doc_type = str(raw.get('doc_type') or '').strip()
    if doc_type not in DOC_TYPE_LABELS:
        errors[key + 'doc_type'] = 'Выберите тип документа'
    other = None
    if doc_type == 'other':
        # ТЗ п. 3: без описания «Иное» заявку не сохранить — даже черновиком.
        other = text(raw.get('other_description'), OTHER_DESCRIPTION_MAX,
                     field=key + 'other_description', errors=errors)
        if not other:
            errors[key + 'other_description'] = 'Укажите, что именно отправляется'
    # Другой тип — поле скрыто и в проверке не участвует; старый текст не храним.
    try:
        period_from = date_or_none(raw.get('period_from'))
        period_to = date_or_none(raw.get('period_to'))
    except ValueError:
        errors[key + 'period_from'] = 'Неверная дата'
        period_from = period_to = None
    if period_from and period_to and period_from > period_to:
        errors[key + 'period_to'] = 'Конец периода раньше начала'
    try:
        qty = int_or_none(raw.get('qty'))
    except (TypeError, ValueError):
        qty = None
        errors[key + 'qty'] = 'Количество — целое число'
    else:
        if qty is None or qty < 1:
            errors[key + 'qty'] = 'Укажите количество'
        elif qty > MAX_QTY:
            errors[key + 'qty'] = 'Слишком большое количество'
    copy_kind = str(raw.get('copy_kind') or 'original').strip()
    if copy_kind not in COPY_KIND_LABELS:
        errors[key + 'copy_kind'] = 'Оригинал или копия'
        copy_kind = 'original'
    return {
        'doc_type': doc_type,
        'other_description': other,
        'period_from': period_from,
        'period_to': period_to,
        'qty': qty,
        'copy_kind': copy_kind,
        'doc_number': text(raw.get('doc_number'), _TEXT_LIMITS['doc_number'], field=key + 'doc_number', errors=errors),
        'is_confidential': bool_value(raw.get('is_confidential')),
    }


def clean_shipment(payload):
    """Данные карточки из запроса → (поля, строки). Проверка «черновика»:

    форма и типы, «Иное» с описанием, периоды, количества. Полноту (адресат,
    плательщик, вес, хотя бы одна строка) проверяет require_complete — она нужна
    только при «Готово к отправке» и дальше. Все ошибки собираются разом, чтобы
    форма подсветила каждое поле, а не по одному за отправку.
    """
    payload = payload if isinstance(payload, dict) else {}
    errors = {}
    fields = {}
    for name in ('sender_office_id', 'recipient_office_id', 'payer_entity_id'):
        try:
            value = int_or_none(payload.get(name))
        except (TypeError, ValueError):
            errors[name] = 'Неверное значение'
            value = None
        if value is not None and value <= 0:
            errors[name] = 'Неверное значение'
            value = None
        fields[name] = value
    for name in ('sender_name', 'sender_phone', 'recipient_name', 'recipient_phone',
                 'recipient_address', 'recipient_postcode', 'comment'):
        fields[name] = text(payload.get(name), _TEXT_LIMITS[name], field=name, errors=errors)
    postcode = fields.get('recipient_postcode')
    if postcode and not postcode.replace(' ', '').isdigit():
        errors['recipient_postcode'] = 'Индекс — только цифры'
    delivery_type = str(payload.get('delivery_type') or 'standard').strip()
    if delivery_type not in DELIVERY_TYPE_LABELS:
        errors['delivery_type'] = 'Выберите тип доставки'
        delivery_type = 'standard'
    fields['delivery_type'] = delivery_type
    urgency = str(payload.get('urgency') or 'normal').strip()
    if urgency not in URGENCY_LABELS:
        errors['urgency'] = 'Выберите срочность'
        urgency = 'normal'
    fields['urgency'] = urgency
    try:
        places = int_or_none(payload.get('places'))
    except (TypeError, ValueError):
        errors['places'] = 'Число мест — целое число'
        places = None
    if places is None:
        places = 1
    elif places < 1 or places > MAX_PLACES:
        errors['places'] = 'От 1 до %d' % MAX_PLACES
    fields['places'] = places
    for name, limit in (('weight_kg', MAX_WEIGHT_KG), ('volume_weight_kg', MAX_WEIGHT_KG),
                        ('declared_value', MAX_DECLARED_VALUE)):
        try:
            value = number_or_none(payload.get(name))
        except (TypeError, ValueError):
            errors[name] = 'Введите число'
            value = None
        if value is not None:
            # Сначала округляем, потом проверяем: 0,0001 кг после округления — ноль, и CHECK в базе
            # ответил бы «внутренней ошибкой» вместо подсказки у поля.
            if name == 'declared_value':
                value = round(value, 2)
                if value < 0 or value > limit:
                    errors[name] = 'Неверная сумма'
            else:
                value = round(value, 3)
                if value <= 0 or value > limit:
                    errors[name] = 'Больше нуля, не больше %d кг' % limit
        fields[name] = value
    if (fields['sender_office_id'] and fields['recipient_office_id']
            and fields['sender_office_id'] == fields['recipient_office_id']):
        errors['recipient_office_id'] = 'Офис получения совпадает с офисом отправки'

    raw_items = payload.get('items')
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        errors['items'] = 'Неверный список содержимого'
        raw_items = []
    if len(raw_items) > MAX_ITEMS:
        errors['items'] = 'Не больше %d строк содержимого' % MAX_ITEMS
        raw_items = raw_items[:MAX_ITEMS]
    items = [clean_item(raw, index, errors) for index, raw in enumerate(raw_items)]
    if errors:
        raise MailError('Проверьте поля, отмеченные красным', code='MAIL_FIELDS', fields=errors)
    return fields, items


def missing_for_ready(fields, items):
    """Чего не хватает до «Готово к отправке» (ТЗ п. 3) — {поле: текст}."""
    missing = {}
    if not fields.get('sender_office_id'):
        missing['sender_office_id'] = 'Выберите офис отправки'
    if not fields.get('recipient_office_id'):
        missing['recipient_office_id'] = 'Выберите офис получения'
    if not fields.get('recipient_name'):
        missing['recipient_name'] = 'Укажите ФИО получателя'
    if not fields.get('recipient_phone'):
        missing['recipient_phone'] = 'Укажите телефон получателя'
    if not fields.get('recipient_address'):
        missing['recipient_address'] = 'Укажите адрес получателя'
    if not fields.get('sender_name'):
        missing['sender_name'] = 'Укажите ФИО отправителя'
    if not fields.get('sender_phone'):
        missing['sender_phone'] = 'Укажите телефон отправителя'
    if not fields.get('payer_entity_id'):
        missing['payer_entity_id'] = 'Выберите юрлицо-плательщика'
    if not fields.get('weight_kg'):
        missing['weight_kg'] = 'Укажите вес'
    if not items:
        missing['items'] = 'Добавьте хотя бы одну строку содержимого'
    return missing


def require_complete(fields, items):
    missing = missing_for_ready(fields, items)
    if missing:
        raise MailError('Для «Готово к отправке» не хватает данных', code='MAIL_INCOMPLETE', fields=missing)


def changed_fields(before, after, names=SHIPMENT_FIELDS):
    """Что поменялось — для истории: {поле: [было, стало]}."""
    changes = {}
    for name in names:
        old, new = _comparable(before.get(name)), _comparable(after.get(name))
        if old != new:
            changes[name] = [old, new]
    return changes


def _comparable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 3)
    if hasattr(value, 'as_tuple'):  # Decimal из базы
        return round(float(value), 3)
    return value


def items_signature(items):
    """Строки содержимого в сравнимом виде: правка без изменений в историю не идёт."""
    keys = ('doc_type', 'other_description', 'period_from', 'period_to', 'qty', 'copy_kind',
            'doc_number', 'is_confidential')
    return [tuple(_comparable(item.get(key)) for key in keys) for item in items or []]


def item_line(item):
    """Строка содержимого одной фразой — для истории «было → стало» (ТЗ п. 11, критерий L)."""
    label = DOC_TYPE_LABELS.get(item.get('doc_type'), item.get('doc_type') or '—')
    if item.get('doc_type') == 'other' and item.get('other_description'):
        label = '%s «%s»' % (label, item['other_description'])
    parts = ['%s ×%s' % (label, item.get('qty'))]
    start, end = item.get('period_from'), item.get('period_to')
    if start or end:
        fmt = lambda day: date_or_none(day).strftime('%d.%m.%Y') if day else '…'  # noqa: E731
        parts.append('%s–%s' % (fmt(start), fmt(end)))
    parts.append(COPY_KIND_LABELS.get(item.get('copy_kind'), '').lower())
    if item.get('doc_number'):
        parts.append('№ %s' % item['doc_number'])
    if item.get('is_confidential'):
        parts.append('конфиденциально')
    return ' · '.join(part for part in parts if part)


def economy_ready(zones):
    """Задан ли срок эконом-доставки хоть у одной зоны: без него «Эконом» не отправить."""
    return any(zone.get('eco_max_days') is not None for zone in zones or [])


def items_summary(items, limit=3):
    """«АВР (5), Договор (2)» — кратко для реестра, как в макете."""
    parts = []
    for item in items or []:
        label = DOC_TYPE_LABELS.get(item.get('doc_type'), item.get('doc_type') or '—')
        if item.get('doc_type') == 'other' and item.get('other_description'):
            label = item['other_description']
        parts.append('%s (%s)' % (label, item.get('qty') or 0))
    if len(parts) > limit:
        return ', '.join(parts[:limit]) + ' и ещё %d' % (len(parts) - limit)
    return ', '.join(parts)


# ─── Номер отправления ───────────────────────────────────────────────────────

def format_number(year, sequence):
    """MAIL-YYYY-NNNNN (ТЗ п. 3). Счётчик у каждого года свой."""
    return 'MAIL-%04d-%05d' % (int(year), int(sequence))


# ─── Переходы (ТЗ п. 4–6, 14) ────────────────────────────────────────────────
#
# side — чья это кнопка:
#   sender       сторона отправки: участники офиса отправки, координатор, центр, админ
#   recipient    сторона получения: участники офиса получения, координатор, центр, админ —
#                кроме автора отправления (ТЗ п. 11: «самостоятельно подтверждать получение
#                собственного отправления нельзя»)
#   coordinator  координатор офиса отправки или получения, центральный координатор, админ
#   courier      отметки о курьере знают обе стороны: отправитель видит трек, получатель —
#                саму посылку
ACTIONS = {
    'ready': {'from': ('draft',), 'to': 'ready', 'side': 'sender', 'label': 'Готово к отправке'},
    'courier_request': {'from': ('ready',), 'to': 'courier_requested', 'side': 'sender',
                        'label': 'Заявка курьеру'},
    'pickup_failed': {'from': ('courier_requested',), 'to': 'courier_requested', 'side': 'sender',
                      'label': 'Забор не состоялся'},
    'hand_over': {'from': ('ready', 'courier_requested'), 'to': 'handed_over', 'side': 'sender',
                  'label': 'Передано курьеру'},
    'in_transit': {'from': ('handed_over', 'not_delivered'), 'to': 'in_transit', 'side': 'courier',
                   'label': 'В пути'},
    'deliver': {'from': ('handed_over', 'in_transit'), 'to': 'delivered', 'side': 'courier',
                'label': 'Доставлено курьером'},
    'not_delivered': {'from': ('handed_over', 'in_transit'), 'to': 'not_delivered', 'side': 'courier',
                      'label': 'Не удалось вручить'},
    # Итог сверки — «Принято и сверено» или «Проблема при получении»: решают строки.
    'reconcile': {'from': ('handed_over', 'in_transit', 'delivered'), 'to': None, 'side': 'recipient',
                  'label': 'Сверить'},
    'resolve': {'from': ('problem',), 'to': 'accepted', 'side': 'coordinator', 'label': 'Проблема решена'},
    'start_return': {'from': ('problem', 'not_delivered'), 'to': 'return_initiated', 'side': 'coordinator',
                     'label': 'Начать возврат'},
    'returned': {'from': ('return_initiated',), 'to': 'returned', 'side': 'sender', 'label': 'Возвращено'},
    'cancel': {'from': BEFORE_HANDOVER, 'to': 'cancelled', 'side': 'sender', 'label': 'Отменить'},
}

# Причина обязательна: без неё событие ничего не объясняет тому, кто откроет историю.
NOTE_REQUIRED = {
    'pickup_failed': 'Укажите причину',
    'not_delivered': 'Укажите причину',
    'resolve': 'Опишите, как решили',
    'start_return': 'Укажите причину возврата',
    'cancel': 'Укажите причину отмены',
}


def action_spec(action):
    spec = ACTIONS.get(action)
    if not spec:
        raise MailError('Неизвестное действие', code='MAIL_UNKNOWN_ACTION', status=404)
    return spec


def check_transition(action, status):
    spec = action_spec(action)
    if status not in spec['from']:
        raise MailError(
            '«%s» нельзя: отправление в статусе «%s»' % (spec['label'], STATUS_LABELS.get(status, status)),
            code='MAIL_BAD_STATUS', status=409)
    return spec


def clean_reconcile_lines(raw_lines, items):
    """Построчная сверка (ТЗ п. 6, 14). Каждая строка содержимого — ровно один ответ.

    Возвращает ([{'id', 'received_qty', 'line_status', 'line_note'}], итог) где итог —
    'accepted', если все строки совпали, иначе 'problem'. «Совпадает» означает, что
    пришло ровно заявленное количество; любое другое число — расхождение, и вид его
    человек выбирает сам (недостача, лишнее, повреждение, не тот документ).
    """
    by_id = {}
    for raw in raw_lines if isinstance(raw_lines, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            item_id = int_or_none(raw.get('id'))
        except (TypeError, ValueError):
            continue
        if item_id is not None:
            by_id[item_id] = raw
    errors = {}
    lines = []
    for index, item in enumerate(items):
        raw = by_id.get(item['id'])
        key = 'lines.%d.' % index
        if raw is None:
            errors[key + 'line_status'] = 'Сверьте строку'
            continue
        status = str(raw.get('line_status') or '').strip()
        if status not in LINE_STATUS_LABELS:
            errors[key + 'line_status'] = 'Отметьте: совпадает или расхождение'
            continue
        note = text(raw.get('line_note'), _TEXT_LIMITS['line_note'], field=key + 'line_note', errors=errors)
        if status == 'ok':
            received = item['qty']
        else:
            try:
                received = int_or_none(raw.get('received_qty'))
            except (TypeError, ValueError):
                received = None
            if received is None or received < 0 or received > MAX_QTY:
                errors[key + 'received_qty'] = 'Укажите, сколько пришло фактически'
                continue
            if status == 'shortage' and received >= item['qty']:
                errors[key + 'received_qty'] = 'При недостаче пришло меньше заявленного'
                continue
            if status == 'excess' and received <= item['qty']:
                errors[key + 'received_qty'] = 'При «лишнем» пришло больше заявленного'
                continue
        lines.append({'id': item['id'], 'received_qty': received, 'line_status': status, 'line_note': note})
    if errors:
        raise MailError('Сверьте каждую строку', code='MAIL_RECONCILE_FIELDS', fields=errors)
    outcome = 'accepted' if all(line['line_status'] == 'ok' for line in lines) else 'problem'
    return lines, outcome


# ─── Рабочие дни и сроки (ТЗ п. 7) ───────────────────────────────────────────

def kz_holidays():
    """Праздники и переносы РК — справочник проекта (resource_fte), второй копии нет."""
    try:
        from resource_fte.calendar_model import KZ_HOLIDAYS
    except Exception:  # noqa: BLE001 — без справочника срок считается без праздников
        return frozenset()
    out = set()
    for value in KZ_HOLIDAYS:
        try:
            out.add(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return frozenset(out)


def is_working_day(day, holidays):
    return day.weekday() < 5 and day not in holidays


def add_working_days(start, days, holidays):
    """N-й рабочий день ПОСЛЕ даты start. День передачи курьеру в срок не входит:
    передали в пятницу вечером, срок «1 рабочий день» — это понедельник."""
    current = start
    remaining = int(days)
    while remaining > 0:
        current += timedelta(days=1)
        if is_working_day(current, holidays):
            remaining -= 1
    return current


def working_days_left(today, due_on, holidays):
    """Сколько рабочих дней от сегодня до срока: 0 — срок сегодня, 1 — завтра (следующий
    рабочий день), отрицательное — столько рабочих дней уже просрочено."""
    if due_on == today:
        return 0
    step = 1 if due_on > today else -1
    count = 0
    current = today
    while current != due_on:
        current += timedelta(days=step)
        if is_working_day(current, holidays):
            count += step
    return count


def sla_window(zone, delivery_type):
    """(мин, макс) рабочих дней для зоны и типа доставки или None, если не задано."""
    if not zone:
        return None
    if delivery_type == 'economy':
        low, high = zone.get('eco_min_days'), zone.get('eco_max_days')
    else:
        low, high = zone.get('min_days'), zone.get('max_days')
    if high is None:
        return None
    low = high if low is None else low
    return int(low), int(high)


def deadline(handed_on, window, holidays):
    """(самая ранняя дата, крайний срок) от даты передачи курьеру."""
    low, high = window
    return add_working_days(handed_on, low, holidays), add_working_days(handed_on, high, holidays)


def normalize_city(city):
    return ' '.join(str(city or '').lower().replace('ё', 'е').split())


def zone_for_route(from_city, to_city, routes):
    """Код зоны по матрице «город отправления × город получения» (ТЗ п. 7).

    routes — {(город_откуда, город_куда): код} с нормализованными городами. Нет строки
    матрицы, но город один — «внутригородская» зона 2, прямо по ТЗ. Иначе None:
    специальный маршрут подтверждают вручную.
    """
    key = (normalize_city(from_city), normalize_city(to_city))
    if key in routes:
        return routes[key]
    if key[0] and key[0] == key[1]:
        return INTRACITY_ZONE
    return None


INTRACITY_ZONE = '2'

# Зоны договора по ТЗ п. 7 — стартовые значения справочника; правятся в «Справочниках».
DEFAULT_ZONES = (
    {'code': '2', 'label': 'Внутригородская', 'min_days': 1, 'max_days': 1, 'position': 1},
    {'code': '1', 'label': 'Зона 1', 'min_days': 2, 'max_days': 3, 'position': 2},
    {'code': '3', 'label': 'Зона 3', 'min_days': 7, 'max_days': 15, 'position': 3},
)
MAX_SLA_DAYS = 60


def deadline_state(status, due_on, today, holidays):
    """Что показать в колонке «Срок»: просрочено / сегодня / через рабочий день / в срок.

    «soon» — до срока один рабочий день: это то самое «предупреждение за 1 рабочий день
    до предельного SLA» из ТЗ п. 8. Не «завтра»: в субботу следующий рабочий день —
    понедельник.

    Срок есть только у того, что едет: до передачи курьеру его нет вовсе (ТЗ п. 7 —
    «нельзя считать SLA от создания черновика»), после вручения он закрыт.
    """
    if not due_on or status not in SLA_RUNNING:
        return None
    left = working_days_left(today, due_on, holidays)
    if today > due_on:
        return {'kind': 'overdue', 'days': left}
    if left == 0:
        return {'kind': 'today', 'days': 0}
    if left == 1:
        return {'kind': 'soon', 'days': 1}
    return {'kind': 'ok', 'days': left}


def is_overdue(status, due_on, today):
    return bool(due_on) and status in SLA_RUNNING and today > due_on


def clean_zones(raw):
    """Справочник зон из настроек: код, подпись, мин–макс стандарта и эконома."""
    if not isinstance(raw, list) or not raw:
        raise MailError('Нужна хотя бы одна зона', code='MAIL_ZONES_EMPTY')
    seen = set()
    zones = []
    errors = {}
    for index, item in enumerate(raw[:20]):
        item = item if isinstance(item, dict) else {}
        key = 'zones.%d.' % index
        code = str(item.get('code') or '').strip()
        if not code or len(code) > 8:
            errors[key + 'code'] = 'Код зоны — до 8 символов'
            continue
        if code in seen:
            errors[key + 'code'] = 'Код повторяется'
            continue
        seen.add(code)
        label = text(item.get('label'), 60, field=key + 'label', errors=errors) or ('Зона %s' % code)
        values = {}
        for name in ('min_days', 'max_days', 'eco_min_days', 'eco_max_days'):
            try:
                value = int_or_none(item.get(name))
            except (TypeError, ValueError):
                errors[key + name] = 'Целое число'
                value = None
            if value is not None and (value < 0 or value > MAX_SLA_DAYS):
                errors[key + name] = 'От 0 до %d' % MAX_SLA_DAYS
            values[name] = value
        if values['max_days'] is None:
            errors[key + 'max_days'] = 'Укажите срок'
        for low, high in (('min_days', 'max_days'), ('eco_min_days', 'eco_max_days')):
            if values[low] is not None and values[high] is not None and values[low] > values[high]:
                errors[key + low] = 'Больше верхней границы'
        if values['eco_min_days'] is not None and values['eco_max_days'] is None:
            errors[key + 'eco_max_days'] = 'Укажите верхнюю границу эконома'
        zones.append({'code': code, 'label': label, 'position': index + 1, **values})
    if errors:
        raise MailError('Проверьте зоны', code='MAIL_FIELDS', fields=errors)
    return zones


def clean_routes(raw, zone_codes):
    """Матрица маршрутов: пары городов → зона. «В обе стороны» разворачивается в две строки."""
    if not isinstance(raw, list):
        raise MailError('Неверный список маршрутов')
    routes = {}
    errors = {}
    for index, item in enumerate(raw[:2000]):
        item = item if isinstance(item, dict) else {}
        key = 'routes.%d.' % index
        from_city = text(item.get('from_city'), 120, field=key + 'from_city', errors=errors)
        to_city = text(item.get('to_city'), 120, field=key + 'to_city', errors=errors)
        zone = str(item.get('zone_code') or '').strip()
        if not from_city:
            errors[key + 'from_city'] = 'Город отправления'
        if not to_city:
            errors[key + 'to_city'] = 'Город получения'
        if zone not in zone_codes:
            errors[key + 'zone_code'] = 'Выберите зону'
        if not from_city or not to_city or zone not in zone_codes:
            continue
        pairs = [(from_city, to_city)]
        if bool_value(item.get('both_ways', True)) and normalize_city(from_city) != normalize_city(to_city):
            pairs.append((to_city, from_city))
        for origin, target in pairs:
            routes[(normalize_city(origin), normalize_city(target))] = {
                'from_city': origin, 'to_city': target, 'zone_code': zone}
    if errors:
        raise MailError('Проверьте маршруты', code='MAIL_FIELDS', fields=errors)
    return list(routes.values())
