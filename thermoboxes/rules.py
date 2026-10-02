"""Поля таблицы термокоробов, их проверка и подписи. Чистая логика: ни базы, ни сети.

Колонки — ровно колонки листа «Условия выдачи коробов» (задача #363):

    Бесплатные термокороба, Термопакет, Б/У короб   — остатки, целые ≥ 0
    Тариф                                           — «Выдаются авто-курьерам» / «Все тарифы»
    Заказы                                          — «15+ заказов», «20+ заказов»
    Срок                                            — «Неделя (7 дней)»
    Отдельное условие                               — «НЕ новички»
    Депозит                                         — «5 000 тенге» / «Нет депозита»

В листе «Заказы», «Срок» и «Депозит» — текст из выпадающего списка. Здесь они
числа: «только целые числа, без отрицательных значений» (требование постановки)
проверяется у числа, а «15+ заказов» и «5 000 тенге» — это подпись к нему,
одна на экран, файл и историю. Ноль у заказов — «без нормы», у депозита —
«Нет депозита», как в листе.

Подписи здесь — единственный источник: фронт держит те же строки в
thermoboxMeta.js, тест сверяет их буквально.
"""

COUNT_FIELDS = ('free_boxes', 'thermo_bags', 'used_boxes')
CONDITION_FIELDS = ('tariff', 'min_orders', 'period_days', 'deposit_tenge', 'special_condition')
EDITABLE_FIELDS = COUNT_FIELDS + CONDITION_FIELDS

FIELD_LABELS = {
    'free_boxes': 'Бесплатные термокороба',
    'thermo_bags': 'Термопакеты',
    'used_boxes': 'Б/У короба',
    'tariff': 'Тариф',
    'min_orders': 'Заказы',
    'period_days': 'Срок',
    'deposit_tenge': 'Депозит',
    'special_condition': 'Отдельное условие',
}

# Коды тарифа — в базе, подписи — здесь. Список закрытый: два значения листа.
# Новое значение добавляется строкой сюда и в thermoboxMeta.js, без миграции —
# CHECK-констрейнта у колонки нет намеренно.
TARIFF_LABELS = {
    'auto_couriers': 'Выдаются авто-курьерам',
    'all': 'Все тарифы',
}
TARIFF_CODES = tuple(TARIFF_LABELS)

# Ответственные за пункт памятки — колонка «Ответственные» листа.
MEMO_OWNER_LABELS = {
    'kc': 'КЦ',
    'regions': 'Регионы',
}

# Значок пункта памятки — как в листе (⛔ 💰 📊 ⚠️ ✅ ❓). Выбирает СВ в
# редакторе; пункт без значка экран показывает с угаданным по тексту.
MEMO_KIND_LABELS = {
    'forbidden': 'Запрет',
    'money': 'Деньги',
    'data': 'Данные',
    'warning': 'Важно',
    'check': 'Проверка',
    'question': 'Вопрос',
}

# Пределы. Офис держит десятки коробов, сто тысяч — заведомо опечатка в числе;
# депозит — тенге, десять миллионов — тот же «лишний ноль».
_LIMITS = {
    'free_boxes': (0, 100000),
    'thermo_bags': (0, 100000),
    'used_boxes': (0, 100000),
    'min_orders': (0, 10000),
    'period_days': (1, 365),
    'deposit_tenge': (0, 10000000),
}

SPECIAL_CONDITION_MAX = 200
MEMO_ITEMS_MAX = 20
MEMO_TEXT_MAX = 500
MEMO_TITLE_MAX = 200

# Условия, которыми заводится новый офис, — самые частые в листе.
NEW_ROW_DEFAULTS = {
    'free_boxes': 0, 'thermo_bags': 0, 'used_boxes': 0,
    'tariff': 'auto_couriers', 'min_orders': 15, 'period_days': 7,
    'deposit_tenge': 5000, 'special_condition': None,
}


class FieldError(ValueError):
    """Неверное значение поля. field — ключ поля, message — текст для человека."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def _whole(field, value):
    """Целое ≥ 0 в пределах поля. bool — не число, «5» строкой — не число, 1.5 —
    не целое: фронт шлёт числа, и всё прочее значит, что поле заполнено неверно."""
    low, high = _LIMITS[field]
    label = FIELD_LABELS[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FieldError(field, '%s: укажите целое число' % label)
    if isinstance(value, float):
        if not value.is_integer():
            raise FieldError(field, '%s: только целое число' % label)
        value = int(value)
    if value < 0:
        raise FieldError(field, '%s: не может быть меньше нуля' % label)
    if value < low:
        raise FieldError(field, '%s: не меньше %d' % (label, low))
    if value > high:
        raise FieldError(field, '%s: слишком большое число' % label)
    return int(value)


def clean_value(field, value):
    """Проверенное значение одного поля. Ошибка — FieldError."""
    if field in _LIMITS:
        return _whole(field, value)
    if field == 'tariff':
        code = str(value or '').strip()
        if code not in TARIFF_LABELS:
            raise FieldError(field, 'Тариф: выберите из списка')
        return code
    if field == 'special_condition':
        if value is None:
            return None
        if not isinstance(value, str):
            raise FieldError(field, 'Отдельное условие: укажите текстом')
        text = ' '.join(value.split())
        if len(text) > SPECIAL_CONDITION_MAX:
            raise FieldError(field, 'Отдельное условие: не длиннее %d знаков' % SPECIAL_CONDITION_MAX)
        return text or None
    raise FieldError(field, 'Неизвестное поле')


def clean_fields(data):
    """Проверяет присланные поля строки — только те, что пришли.

    Возвращает словарь проверенных значений. Первая же ошибка — FieldError:
    сохранение пачки атомарно, и сохранять «то, что получилось» нельзя.
    """
    fields = {}
    for field in EDITABLE_FIELDS:
        if field in data:
            fields[field] = clean_value(field, data[field])
    return fields


def changes(before, fields):
    """«Было → стало» по полям, которые правда поменялись.

    Пустое сохранение (те же числа) изменением не считается: иначе история
    строки заполнилась бы записями «11 → 11», а «Обновлено» сдвигалось бы
    без правки.
    """
    result = []
    for field in EDITABLE_FIELDS:
        if field not in fields:
            continue
        old, new = before.get(field), fields[field]
        if old != new:
            result.append({'field': field, 'from': old, 'to': new})
    return result


def plural(count, one, few, many):
    value = abs(int(count or 0)) % 100
    if 11 <= value <= 14:
        return many
    last = value % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _thousands(value):
    return '{:,}'.format(int(value)).replace(',', ' ')


def orders_label(value):
    """«15+ заказов». После «N+» в листе всегда «заказов» — «21+ заказ» по
    правилу числа читался бы как опечатка."""
    if not value:
        return 'Без нормы'
    return '%d+ заказов' % value


def period_label(value):
    if value == 7:
        return 'Неделя (7 дней)'
    return '%d %s' % (value, plural(value, 'день', 'дня', 'дней'))


def deposit_label(value):
    if not value:
        return 'Нет депозита'
    return '%s тенге' % _thousands(value)


def tariff_label(code):
    return TARIFF_LABELS.get(code, code or '—')


def display_value(field, value):
    """Значение поля так, как его пишут в листе: для истории и выгрузки."""
    if field == 'tariff':
        return tariff_label(value)
    if field == 'min_orders':
        return orders_label(value or 0)
    if field == 'period_days':
        return period_label(value) if value else '—'
    if field == 'deposit_tenge':
        return deposit_label(value or 0)
    if field == 'special_condition':
        return value or '—'
    return '—' if value is None else str(value)


def clean_memo(data):
    """Памятка целиком: заголовок и пункты. Ошибка — FieldError.

    Пункт — текст, ответственный (КЦ / Регионы / никто) и значок. Пустые
    пункты выбрасываются молча: это строка, которую добавили и не заполнили.
    """
    title = data.get('title')
    if title is not None and not isinstance(title, str):
        raise FieldError('title', 'Заголовок: укажите текстом')
    title = ' '.join((title or '').split())
    if len(title) > MEMO_TITLE_MAX:
        raise FieldError('title', 'Заголовок: не длиннее %d знаков' % MEMO_TITLE_MAX)

    raw_items = data.get('items')
    if not isinstance(raw_items, list):
        raise FieldError('items', 'Пункты памятки не получены')
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise FieldError('items', 'Пункт памятки не разобран')
        text = raw.get('text')
        if text is not None and not isinstance(text, str):
            raise FieldError('items', 'Пункт памятки: укажите текстом')
        # Переносы строк внутри пункта сохраняются: в листе пункт — две строки.
        lines = [' '.join(line.split()) for line in (text or '').strip().splitlines()]
        text = '\n'.join(line for line in lines if line)
        if not text:
            continue
        if len(text) > MEMO_TEXT_MAX:
            raise FieldError('items', 'Пункт памятки: не длиннее %d знаков' % MEMO_TEXT_MAX)
        owner = raw.get('owner') or None
        if owner is not None and owner not in MEMO_OWNER_LABELS:
            raise FieldError('items', 'Ответственный: выберите из списка')
        kind = raw.get('kind') or None
        if kind is not None and kind not in MEMO_KIND_LABELS:
            raise FieldError('items', 'Значок правила: выберите из списка')
        items.append({'text': text, 'owner': owner, 'kind': kind})
    if len(items) > MEMO_ITEMS_MAX:
        raise FieldError('items', 'В памятке не больше %d пунктов' % MEMO_ITEMS_MAX)
    return {'title': title or None, 'items': items}


def _fold(text):
    return ' '.join(str(text or '').lower().replace('ё', 'е').split())


def matches(row, city='', query=''):
    """Отбор экрана — он же у выгрузки: «что вижу, то и выгружаю».

    Город — точное совпадение без учёта регистра; поиск — каждое слово запроса
    где-нибудь в городе, названии, адресе или отдельном условии. Пара —
    matchesRow в thermoboxMeta.js.
    """
    if _fold(city) and _fold(row.get('city')) != _fold(city):
        return False
    words = _fold(query).split()
    if not words:
        return True
    haystack = _fold(' '.join(str(row.get(name) or '') for name in
                              ('city', 'name', 'address', 'special_condition')))
    return all(word in haystack for word in words)
