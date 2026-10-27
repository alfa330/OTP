# -*- coding: utf-8 -*-
"""Наличие авто и направления в офис аренды (#391). Чистая логика: ни базы, ни сети.

Постановка — ТЗ Зарины Абдраман v5 и её HTML-макет v3; решения владельца 27.10.2026:

* Карточка — вариант модели с условиями (класс, цена, страховка, путевой лист). Варианты одной
  модели с разными условиями страховки не объединяются (ТЗ 3.1): у HongQi E-QM5 и Geely Emgrand
  по две карточки — «страховка водителя» и «путевой лист».
* Наличие — смешанная схема ТЗ v5, п. 3.1 (владелец 27.10: «в Yume только занятые авто, перечитай
  ТЗ»): из Yume — только ЗАНЯТЫЕ машины (в аренде и в брони); число СВОБОДНЫХ сотрудники аренды
  вводят вручную, у каждой карточки своё, и синхронизация его не трогает. Поэтому и путевой лист —
  отдельная карточка со своим числом свободных. Карточка находит машины Yume по ключам модели
  (`yume_keys`): в Yume одна модель записана по-разному («HONGQI EQM5», «Hongqi E-QM5»).
* Свободных 0 — «Нет в наличии», направить нельзя (ТЗ 3.1); ни разу не указано — «Наличие не
  указано», направить тоже нельзя.
* Направление в офис не резервирует машину (ТЗ 3.2): число свободных при нём не меняется.
* Статусы направления — шесть из ТЗ 4; «Подумает» не нужен (владелец 27.10).
* Парк — обязательное поле, из общего справочника парков вики (владелец 27.10: «Общий»).
"""

import re
from datetime import date, datetime, timedelta

# ─── Модель машины ──────────────────────────────────────────────────────────────

def model_key(text):
    """«HongQi E-QM5» → «hongqieqm5»: регистр, пробелы, дефисы и точки не важны."""
    lowered = str(text or '').lower().replace('ё', 'е')
    return re.sub(r'[^a-z0-9а-я]', '', lowered)


def car_keys(car):
    """Ключи машины Yume: по марке и модели карточки и по названию инвентаря."""
    keys = set()
    brand_model = ' '.join(part for part in (car.get('brand'), car.get('model')) if part)
    for text in (brand_model, car.get('name')):
        key = model_key(text)
        if key:
            keys.add(key)
    return keys


def clean_keys(values):
    keys = []
    for value in values or ():
        key = model_key(value)
        if key and key not in keys:
            keys.append(key)
    return keys


# Статусы машины в Yume (инвентарь): 1 Забронировано, 2 В аренде — это и есть «занятые» ТЗ 3.1.
YUME_BOOKED, YUME_IN_RENT = 1, 2


def count_busy(cars, cards):
    """Сколько машин модели каждой карточки занято по Yume: {id карточки: {'in_rent', 'booked'}}.

    cars — машины Yume: {'keys', 'status', 'deleted'}. Удалённые из парка не считаются. Карточки
    одной модели (страховка водителя и путевой лист) видят одних и тех же занятых: в Yume не
    отмечено, под какие условия сдана машина, поэтому это число — по модели.
    """
    busy = {card['id']: {'in_rent': 0, 'booked': 0} for card in cards}
    keys_of = {card['id']: set(card.get('yume_keys') or ()) for card in cards}
    for car in cars:
        if car.get('deleted'):
            continue
        status = car.get('status')
        if status not in (YUME_IN_RENT, YUME_BOOKED):
            continue
        car_keys_set = set(car.get('keys') or ())
        for card_id, keys in keys_of.items():
            if keys & car_keys_set:
                busy[card_id]['in_rent' if status == YUME_IN_RENT else 'booked'] += 1
    return busy


FREE_MAX = 999


def clean_free_count(value):
    """(число свободных, ошибка). Целое от 0 до 999."""
    text = str(value if value is not None else '').strip()
    if not re.match(r'^\d{1,3}$', text):
        return None, 'Число свободных — целое от 0 до %d' % FREE_MAX
    return int(text), None


# ─── Карточка ──────────────────────────────────────────────────────────────────

CARD_FIELDS = (
    # (ключ, подпись, обязательно)
    ('title', 'Модель', True),
    ('car_class', 'Класс', True),
    ('price', 'Стоимость в сутки, ₸', True),
    ('year', 'Год выпуска', False),
    ('fuel', 'Топливо', False),
    ('color', 'Цвет', False),
    ('age', 'Возраст водителя', False),
    ('insurance_class', 'Класс страховки', False),
    ('insurance', 'Страховка', False),
    ('first_day', 'Первый день', False),
    ('office', 'Офис', False),
)
CARD_LABELS = {key: label for key, label, _required in CARD_FIELDS}
CARD_LABELS['yume_keys'] = 'Как модель записана в Yume'
CARD_LABELS['free_count'] = 'Свободно'

INSURANCE_DRIVER = 'от водителя'
INSURANCE_WAYBILL = 'Путевой лист'


def clean_card(payload):
    """(поля карточки, ошибка). Цена — целое число тенге; ключи Yume — список строк."""
    card = {}
    for key, label, required in CARD_FIELDS:
        value = payload.get(key)
        if key == 'price':
            digits = re.sub(r'\D', '', str(value or ''))
            if not digits:
                return None, 'Укажите стоимость в сутки'
            card['price'] = int(digits)
            if not 1000 <= card['price'] <= 1000000:
                return None, 'Стоимость в сутки — от 1 000 до 1 000 000 ₸'
            continue
        text = re.sub(r'\s+', ' ', str(value or '')).strip()[:120]
        if required and not text:
            return None, 'Заполните поле «%s»' % label
        card[key] = text or None
    raw_keys = payload.get('yume_keys')
    if isinstance(raw_keys, str):
        raw_keys = re.split(r'[,\n;]+', raw_keys)
    card['yume_keys'] = clean_keys(raw_keys) or clean_keys([card['title']])
    return card, None


# ─── Направление ───────────────────────────────────────────────────────────────

STATUS_WAITING = 'waiting'
STATUS_CAME = 'came'
STATUS_ISSUED = 'issued'
STATUS_NO_SHOW = 'no_show'
STATUS_CLIENT_REFUSED = 'client_refused'
STATUS_COMPANY_REFUSED = 'company_refused'
STATUSES = (STATUS_WAITING, STATUS_CAME, STATUS_ISSUED, STATUS_NO_SHOW,
            STATUS_CLIENT_REFUSED, STATUS_COMPANY_REFUSED)
STATUS_LABELS = {
    STATUS_WAITING: 'Ожидаем',
    STATUS_CAME: 'Пришёл',
    STATUS_ISSUED: 'Авто выдано',
    STATUS_NO_SHOW: 'Не пришёл в офис',
    STATUS_CLIENT_REFUSED: 'Отказ клиента',
    STATUS_COMPANY_REFUSED: 'Отказ компании',
}

COMMENT_MAX = 500
# На сколько дней вперёд можно назначить визит. Дальше месяца — это уже не направление.
VISIT_MAX_DAYS = 31


def normalize_phone(raw):
    """Казахстанский номер → «+77071234567»; не похоже на номер — None.

    Принимает «8 707 123 45 67», «+7 (707) 123-45-67», «7071234567». Код после +7 у Казахстана
    начинается с 7 — и у мобильных, и у городских (727 — Алматы): по формату их не различить,
    а ТЗ просит проверить корректность номера, а не что он мобильный.
    """
    digits = re.sub(r'\D', '', str(raw or ''))
    if len(digits) == 11 and digits[0] in '78':
        digits = digits[1:]
    if len(digits) != 10 or digits[0] != '7':
        return None
    return '+7' + digits


def clean_direction(payload, *, today, parks):
    """(поля направления, ошибка) по ТЗ 3.2: телефон, дата, время и парк — обязательны."""
    phone = normalize_phone(payload.get('phone'))
    if not phone:
        return None, 'Телефон клиента — казахстанский номер: +7 7XX XXX XX XX'
    try:
        visit_date = date.fromisoformat(str(payload.get('visit_date') or ''))
    except ValueError:
        return None, 'Укажите дату визита'
    if visit_date < today:
        return None, 'Дата визита уже прошла'
    if visit_date > today + timedelta(days=VISIT_MAX_DAYS):
        return None, 'Визит — не дальше чем через месяц'
    visit_time = str(payload.get('visit_time') or '').strip()
    if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', visit_time):
        return None, 'Укажите время визита'
    park = re.sub(r'\s+', ' ', str(payload.get('park') or '')).strip()
    if not park:
        return None, 'Выберите парк, который предложили клиенту'
    if park not in parks:
        return None, 'Такого парка нет в справочнике — выберите из списка'
    comment = str(payload.get('comment') or '').strip()[:COMMENT_MAX] or None
    return {'phone': phone, 'visit_date': visit_date, 'visit_time': visit_time, 'park': park,
            'comment': comment}, None


def visit_at(visit_date, visit_time):
    hours, minutes = (int(part) for part in visit_time.split(':'))
    return datetime(visit_date.year, visit_date.month, visit_date.day, hours, minutes)


# ─── Условия работы ────────────────────────────────────────────────────────────

BLOCK_LIST = 'list'
BLOCK_TEXT = 'text'
BLOCKS_MAX = 20
BLOCK_TEXT_MAX = 4000


def clean_blocks(blocks):
    """(блоки, ошибка). Блок — заголовок и текст; «list» — каждая строка отдельным пунктом."""
    if not isinstance(blocks, list) or not blocks:
        return None, 'Добавьте хотя бы один блок'
    if len(blocks) > BLOCKS_MAX:
        return None, 'Блоков не больше %d' % BLOCKS_MAX
    cleaned = []
    for block in blocks:
        if not isinstance(block, dict):
            return None, 'Блок задан неверно'
        title = re.sub(r'\s+', ' ', str(block.get('title') or '')).strip()[:120]
        text = str(block.get('text') or '').replace('\r\n', '\n').strip()[:BLOCK_TEXT_MAX]
        kind = BLOCK_LIST if block.get('kind') == BLOCK_LIST else BLOCK_TEXT
        if not title:
            return None, 'У каждого блока нужен заголовок'
        if not text:
            return None, 'Блок «%s» пустой' % title
        cleaned.append({'title': title, 'kind': kind, 'text': text})
    return cleaned, None


# ─── Начальные данные — макет Зарины v3 ────────────────────────────────────────
# Цены и условия — из её макета (вкладки «Наличие авто» и «Условия работы»). Дальше их ведут
# сотрудники аренды в самом разделе, с историей правок.

OFFICE = 'Алматы — Утеген Батыра 15а, 2 этаж, 203 кабинет'
OFFICE_PHONE = '+7 707 578 80 80'

_SEED_COMMON = {'age': '25+', 'first_day': 'бесплатный', 'office': OFFICE}

SEED_CARDS = [
    # (модель, класс, топливо, цена, год, цвет, класс страховки, страховка, ключи Yume)
    ('Toyota RAV4', 'Комфорт', 'Бензин', 12000, '2018', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['Toyota RAV4']),
    ('Toyota Camry 70', 'Комфорт +', 'Бензин', 15000, '2020', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['Toyota Camry 70', 'Toyota Camry', 'Toyota Camr']),
    ('Hyundai Elantra', 'Комфорт +', 'Газ/бензин', 15000, '2024', 'Черный / Белый', 'от 7 и выше',
     INSURANCE_DRIVER, ['Hyundai Elantra', 'Hyundai Elantra ГБО']),
    ('Toyota Camry 80', 'Бизнес', 'Бензин', 20000, '2024', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['Toyota Camry 80']),
    ('FAW HONGQI H5', 'Бизнес, Ultima', 'Бензин', 20000, '2024', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['Hongqi H5', 'FAW Hongqi H5', 'FAW HONGGQI H5']),
    ('BYD Han', 'Бизнес, Ultima', 'Электрика', 23000, '2025', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['BYD Han', 'BYD HAN EV']),
    ('HongQi E-QM5', 'Бизнес, Электро', 'Электрика', 15000, '2025', 'Черный / Белый', 'от 7 и выше',
     INSURANCE_DRIVER, ['Hongqi E-QM5']),
    ('HongQi E-QM5', 'Бизнес, Электро', 'Электрика', 15000, '2025', 'Черный / Белый', 'от 11 и выше',
     INSURANCE_WAYBILL, ['Hongqi E-QM5']),
    ('Geely Emgrand', 'Комфорт +', 'Бензин', 15000, '2025', 'Серый / Белый', 'от 7 и выше', INSURANCE_DRIVER,
     ['Geely Emgrand']),
    ('Geely Emgrand', 'Комфорт +', 'Бензин', 15000, '2025', 'Серый / Белый', 'от 11 и выше', INSURANCE_WAYBILL,
     ['Geely Emgrand']),
    ('BYD Destroyer 05', 'Комфорт +', 'Гибрид', 16000, '2025', 'Серый', 'от 7 и выше', INSURANCE_DRIVER,
     ['BYD Destroyer 05']),
    ('BYD Qin L', 'Комфорт +', 'Бензин/электрика', 17000, '2025', 'Белый', 'от 7 и выше', INSURANCE_DRIVER,
     ['BYD Qin L', 'BYD Qin L DM-i']),
    ('BYD Seal', 'Комфорт +', 'Бензин', 17000, '2025', 'Черный / Белый', 'от 7 и выше', INSURANCE_DRIVER,
     ['BYD Seal', 'BYD Seal 05']),
    ('Kaiyi E5', 'Комфорт +', 'Бензин', 15000, '2025', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['Kaiyi E5']),
    ('Kia Soluto', 'Комфорт', 'Бензин', 11000, '2025', 'Черный', 'от 7 и выше', INSURANCE_DRIVER,
     ['Kia Soluto']),
]


def seed_cards():
    cards = []
    for position, (title, car_class, fuel, price, year, color, ins_class, insurance, keys) in enumerate(SEED_CARDS):
        card = dict(_SEED_COMMON, title=title, car_class=car_class, fuel=fuel, price=price, year=year,
                    color=color, insurance_class=ins_class, insurance=insurance, yume_keys=clean_keys(keys),
                    position=position)
        cards.append(card)
    return cards


SEED_BLOCKS = [
    {'title': 'Условия аренды', 'kind': BLOCK_LIST, 'text': '\n'.join([
        'Первый день — бесплатно, авто выдаётся водителям, живущим в черте города.',
        'Машина в распоряжении 24/7.',
        'График 6/1 — первое воскресенье платное. Оплата аренды производится до 16:00; если оплаты нет, '
        'авто отключается и после изымается.',
        'Минимальный срок аренды — 14 дней. Если авто сдаётся в течение 14 дней, первый бесплатный день сгорает.',
        'ТО — за счёт компании.',
        'Разрешённые геозоны по уведомлению: Талгар, Узынагаш, Иссык, Конаев.',
        'Страховка — за счёт водителя, годовая; стоимость зависит от класса страховки.',
        'Оплата через Kaspi Pay.',
        'Бонусная сетка действует, если водитель выполняет поездки на нашем авто.',
        'Водитель должен таксовать в нашем парке Jana Taxi, иначе аренда становится на 1 500 ₸ дороже.',
        'Авто нужно сдать до 10:00; если позже — день сдачи будет оплачиваемым.',
        'Залоговый документ — водительские права.',
    ])},
    {'title': 'Акция «Приведи друга»', 'kind': BLOCK_LIST, 'text': '\n'.join([
        'Необходимо привести друга.',
        'Друг должен отработать с нами 1 месяц.',
        'После первых 15 дней работы друга — бонус 10 000 ₸.',
        'После следующих 15 дней — бонус 20 000 ₸.',
        'Общая сумма бонуса — 30 000 ₸.',
        'Важно: у друга должна быть полная оплата аренды и отсутствие штрафов.',
        'Водитель, который работает в наших парках на своём авто, тоже может привести друга в аренду и участвовать в акции.',
    ])},
    {'title': 'Страховка — стоимость по классам', 'kind': BLOCK_LIST, 'text': '\n'.join([
        'Класс 7 — 32 000 ₸', 'Класс 8 — 30 000 ₸', 'Класс 9 — 28 000 ₸', 'Класс 10 — 26 000 ₸',
        'Класс 11 — 24 000 ₸', 'Класс 12 — 22 000 ₸', 'Класс 13 — 20 000 ₸',
    ])},
    {'title': 'Что такое автострахование', 'kind': BLOCK_TEXT, 'text': '\n\n'.join([
        'Автострахование — это страхование автомобиля и ответственности водителя на случай ДТП, угона или '
        'других повреждений.',
        'Проще говоря: водитель платит страховой компании, а если случится авария или ущерб — страховая '
        'покрывает расходы полностью или частично.',
        'Страховка покрывает ремонт авто, если наш водитель не виноват в ДТП. Если виноват наш водитель — '
        'ремонт автомобиля производится за счёт водителя.',
    ])},
    {'title': 'Что такое путевой лист', 'kind': BLOCK_TEXT, 'text': '\n\n'.join([
        'Путевой лист — это документ, который оформляется на автомобиль перед выездом. Он подтверждает, что '
        'машина допущена к использованию.',
        'При поездках с путевым листом страховая компания не покрывает ремонт. В случае ДТП ремонт '
        'автомобиля водитель покрывает самостоятельно.',
    ])},
]
