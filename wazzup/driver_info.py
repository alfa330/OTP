"""Данные водителя из CRM yataxi для карточки водителя «Чатов ОП».

Просьба владельца 16.10.2026: при вставке ID таксопарк и город подставляются
сами, а всё, что о водителе можно узнать, показывается тут же, в карточке. В
amoCRM по-прежнему уходят только три поля карточки (wazzup/driver_cards_amo.py).

Источник один — CRM yataxi, тот же токен, что у «Посылок» и «Воды»:
  * карточка водителя по ID профиля во Fleet — POST /api/partners/driver-info
    (клиент parcels/drivers.py; ответ за ~0,6 с, к Яндексу CRM не ходит);
  * справочник регистрации — GET /api/partners/registration-dictionaries: у
    каждого парка Яндекса в нём ровно один город и бренд, у тарифов — подписи.
Цепочка: ID водителя → его парк (park.yandex_id) → город парка → значения
списков amoCRM «Таксопарк привлечения» и «Город привлечения».

Сопоставление сверено 16.10.2026 на 216 водителях из сделок, где верификаторы
ставили поля сами. Название парка в CRM — «<парк> <город>» («Jana Taxi Алматы»,
«Адал Шымкент», «Департамент Такси Кокшетау»); без города, слова «такси» и
знаков оно совпадает с названием в amoCRM («Jana Taxi», «Адал», «Департамент»).
Само не выводится только то, что записано по-разному: «Каспий» → «Каспи»,
«Amanat» → «Аманат», «EKI DONGELEK» → «2 Донгелек» (ALIASES). Город парка совпал
с выбранным верификатором в 197 сделках из 216; в остальных он выбирал город
вручную — поэтому подставленное остаётся правимым.
"""
import logging
import os
import re
import threading
import time
from datetime import date, datetime

import requests

from parcels import drivers as crm

log = logging.getLogger(__name__)

DICTIONARIES_URL = 'https://backend.yataxi.kz/api/partners/registration-dictionaries'
# Ждёт верификатор с открытой формой: не ответила CRM — выбирает парк и город сам.
LOOKUP_TIMEOUT = 8
# Заказы и баланс меняются, но не ежеминутно; «не найден» — ненадолго: только
# что зарегистрированный водитель появляется в CRM в течение минуты.
FOUND_TTL = 600
MISSING_TTL = 60
DICTIONARY_TTL = 6 * 3600          # справочник CRM разрешает кэшировать на сутки
DICTIONARY_FAILURE_PAUSE = 300
CACHE_DAYS = 30
FLEET_DRIVER_URL = 'https://fleet.yandex.kz/contractors/%s/details?park_id=%s'

_HEX32 = re.compile(r'^[0-9a-f]{32}$')
_TAXI_WORD = re.compile(r'\b(?:такси|taxi)\b')
_NOT_ALNUM = re.compile(r'[\W_]+')
_BRACKETS = re.compile(r'\([^)]*\)')
ALIASES = {'каспий': 'каспи', 'amanat': 'аманат', 'ekidongelek': '2донгелек'}


def name_key(text):
    """Ключ сравнения названий: без регистра, «ё», слова «такси» и знаков."""
    value = str(text or '').lower().replace('ё', 'е')
    value = _NOT_ALNUM.sub('', _TAXI_WORD.sub(' ', value))
    return ALIASES.get(value, value)


def _without_city(name, city):
    name = ' '.join(str(name or '').split())
    city = ' '.join(str(city or '').split())
    if city and name.lower().endswith(city.lower()):
        name = name[:len(name) - len(city)].strip()
    return name


# ── Справочник CRM ───────────────────────────────────────────────────────────

_dictionary_next_try = [0.0]
_dictionary_lock = threading.Lock()


def fetch_dictionary(timeout=LOOKUP_TIMEOUT):
    """Парки (город, бренд) и подписи тарифов из справочника регистрации CRM."""
    token = crm.get_config().get('token')
    if not token:
        raise crm.DriverLookupError('Связь с CRM не настроена', code='not_configured', status=503)
    response = requests.get(os.getenv('CRM_DICTIONARIES_URL') or DICTIONARIES_URL, timeout=timeout,
                            headers={'X-Integration-Token': token, 'Accept': 'application/json'})
    response.raise_for_status()
    data = (response.json() or {}).get('data') or {}
    parks = [{'yandexId': str(item.get('register_park_id') or '').lower(), 'name': item.get('name') or '',
              'brand': item.get('brand') or '', 'city': item.get('city') or ''}
             for item in data.get('parks') or [] if item.get('register_park_id')]
    if not parks:
        raise ValueError('CRM вернула пустой справочник парков')
    categories = [{'value': item.get('value'), 'label': item.get('label')}
                  for item in ((data.get('vehicle') or {}).get('categories') or [])
                  if item.get('value') and item.get('label')]
    return {'parks': parks, 'categories': categories}


def load_dictionary(cursor):
    cursor.execute("SELECT payload, EXTRACT(EPOCH FROM now() - fetched_at) FROM wazzup_crm_dictionaries "
                   "WHERE key = 'registration'")
    row = cursor.fetchone()
    if not row or not isinstance(row[0], dict):
        return None, None
    return row[0], float(row[1])


def store_dictionary(cursor, payload):
    from psycopg2.extras import Json
    cursor.execute("""
        INSERT INTO wazzup_crm_dictionaries(key, payload, fetched_at) VALUES ('registration', %s, now())
        ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload, fetched_at = EXCLUDED.fetched_at
    """, (Json(payload),))
    # Заодно — срок хранения снимков водителей: в них ФИО, телефон и ИИН.
    cursor.execute("DELETE FROM wazzup_driver_info WHERE fetched_at < now() - make_interval(days => %s)",
                   (CACHE_DAYS,))


def current_dictionary(db, fetch=None):
    """Снимок справочника из базы; устарел или его нет — освежить (после сбоя — не чаще раза в 5 минут)."""
    with db._get_cursor() as cursor:
        payload, age = load_dictionary(cursor)
    if payload is not None and age < DICTIONARY_TTL:
        return payload
    now = time.monotonic()
    with _dictionary_lock:
        if now < _dictionary_next_try[0]:
            return payload
        _dictionary_next_try[0] = now + DICTIONARY_FAILURE_PAUSE
    try:
        fresh = (fetch or fetch_dictionary)()
    except Exception as exc:  # noqa: BLE001 — без справочника карточка всё равно работает
        log.warning('Карточка водителя: справочник CRM не получен: %s', exc)
        return payload
    with db._get_cursor() as cursor:
        store_dictionary(cursor, fresh)
    with _dictionary_lock:
        _dictionary_next_try[0] = 0.0
    return fresh


def _dictionary_park(dictionary, yandex_id):
    yandex_id = str(yandex_id or '').lower()
    for park in (dictionary or {}).get('parks') or []:
        if yandex_id and park.get('yandexId') == yandex_id:
            return park
    return None


# ── Водитель ─────────────────────────────────────────────────────────────────

def lookup(db, driver_id, fetch=None):
    """(state, data, fetched_at, stale, error): state — found / not_found / unavailable.

    Ответ CRM живёт в базе (кэш общий для всех процессов): FOUND_TTL у найденного,
    MISSING_TTL у «не найден». CRM не ответила — отдаём прежний снимок с пометкой."""
    if not _HEX32.match(str(driver_id or '')):
        raise ValueError('ID водителя — 32 символа')
    with db._get_cursor() as cursor:
        cursor.execute("SELECT payload, found, fetched_at, EXTRACT(EPOCH FROM now() - fetched_at) "
                       "FROM wazzup_driver_info WHERE driver_id = %s", (driver_id,))
        row = cursor.fetchone()
    if row and float(row[3]) < (FOUND_TTL if row[1] else MISSING_TTL):
        return ('found' if row[1] else 'not_found'), row[0], row[2], False, None
    try:
        data = (fetch or (lambda value: crm.fetch_driver(value, timeout=LOOKUP_TIMEOUT)))(driver_id)
    except crm.DriverLookupError as exc:
        if exc.code == 'driver_not_found':
            return 'not_found', None, _store(db, driver_id, None), False, None
        if row and row[1]:
            return 'found', row[0], row[2], True, exc.message
        return 'unavailable', None, None, False, exc.message
    return 'found', data, _store(db, driver_id, data), False, None


def _store(db, driver_id, data):
    from psycopg2.extras import Json
    with db._get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO wazzup_driver_info(driver_id, payload, found, fetched_at) VALUES (%s, %s, %s, now())
            ON CONFLICT (driver_id) DO UPDATE
               SET payload = EXCLUDED.payload, found = EXCLUDED.found, fetched_at = EXCLUDED.fetched_at
            RETURNING fetched_at
        """, (driver_id, Json(data) if data is not None else None, data is not None))
        return cursor.fetchone()[0]


def suggest(data, dictionary, options):
    """Что подставить в «Таксопарк привлечения» и «Город привлечения» amoCRM.

    {park, city, crmPark, crmCity}: park/city — {id, name} из списков amoCRM
    или None, если такого значения там нет (тогда человек выбирает сам)."""
    park = (data or {}).get('park') or {}
    entry = _dictionary_park(dictionary, park.get('yandex_id'))
    crm_name = ' '.join(str(park.get('name') or '').split())
    options = options or {}
    cities = {name_key(item.get('name')): item for item in options.get('cities') or []}
    city_name = (entry or {}).get('city') or ''
    city = cities.get(name_key(city_name)) if city_name else None
    if city is None and not city_name:
        # Парка нет в справочнике (его закрыли или ещё не завели) — город из
        # хвоста его названия в CRM: «Jana Taxi Алматы».
        for item in sorted(options.get('cities') or [], key=lambda value: -len(str(value.get('name') or ''))):
            if item.get('name') and crm_name.lower().endswith(str(item['name']).lower()):
                city, city_name = item, item['name']
                break
    parks = {name_key(item.get('name')): item for item in options.get('parks') or []}
    found = None
    base = _without_city(crm_name, city_name)
    entry_name = (entry or {}).get('name') or ''
    for candidate in (base, _BRACKETS.sub(' ', base), entry_name, _BRACKETS.sub(' ', entry_name),
                      (entry or {}).get('brand') or ''):
        key = name_key(candidate)
        if key and key in parks:
            found = parks[key]
            break
    pick = lambda item: {'id': int(item['id']), 'name': str(item.get('name') or '')} if item else None  # noqa: E731
    return {'park': pick(found), 'city': pick(city), 'crmPark': crm_name or entry_name or None,
            'crmCity': city_name or None}


# ── Как показать ─────────────────────────────────────────────────────────────

WORK_STATUS = {'working': ('Работает', 'ok'), 'not_working': ('Не работает', 'muted'),
               'fired': ('Уволен', 'bad')}
EMPLOYMENT = {'park_employee': 'Сотрудник парка', 'selfemployed': 'Самозанятый',
              'self_employed': 'Самозанятый', 'individual_entrepreneur': 'ИП', 'partner': 'Партнёр'}
PAYMENT = {'cash': 'наличные', 'card': 'карта', 'corp': 'корпоративный', 'corporate': 'корпоративный'}


def _text(value):
    text = ' '.join(str(value).split()) if value is not None else ''
    return text or None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    whole = abs(number) == int(abs(number))
    text = '{:,.0f}'.format(abs(number)) if whole else '{:,.2f}'.format(abs(number)).rstrip('0').rstrip('.')
    text = text.replace(',', ' ').replace('.', ',')
    return ('−' if number < 0 else '') + text


def _money(value):
    text = _number(value)
    return text + ' ₸' if text is not None else None


def _when(value):
    """CRM отдаёт местное время строкой «Y-m-d H:i:s» или дату «Y-m-d»."""
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        if len(raw) <= 10:
            return date.fromisoformat(raw).strftime('%d.%m.%Y')
        return datetime.fromisoformat(raw.replace('T', ' ')[:19]).strftime('%d.%m.%Y %H:%M')
    except ValueError:
        return raw


def _full_name(driver):
    parts = [_text(driver.get(key)) for key in ('last_name', 'first_name', 'middle_name')]
    return ' '.join(part for part in parts if part) or _text(driver.get('full_name'))


def _row(key, label, value, **extra):
    """Строка блока. key — что это (экран не повторяет в блоке то, что уже стоит в
    шапке панели), mono — коды и номера, tone — цвет статуса, chips — метки."""
    return dict({'key': key, 'label': label, 'value': value}, **{name: item for name, item in extra.items() if item})


def _rows(*rows):
    return [row for row in rows if row['value'] not in (None, '', [])]


def present(data, dictionary=None):
    """Карточка водителя для экрана: сводка для шапки и все блоки CRM."""
    data = data if isinstance(data, dict) else {}
    driver = data.get('driver') or {}
    park = data.get('park') or {}
    job = data.get('employment') or {}
    balance = data.get('balance') or {}
    car = data.get('car') or {}
    dispatcher = data.get('dispatcher') or {}
    orders = data.get('orders')
    entry = _dictionary_park(dictionary, park.get('yandex_id'))
    labels = {item['value']: item['label'] for item in (dictionary or {}).get('categories') or []}

    status_code = _text(job.get('work_status'))
    label, tone = WORK_STATUS.get(status_code, (status_code, 'muted')) if status_code else (None, 'muted')
    if job.get('is_blocked'):
        label, tone = 'Заблокирован', 'bad'
    car_title = ', '.join(part for part in (
        _text(car.get('model')), _text(car.get('year')), (_text(car.get('color')) or '').lower() or None) if part)
    tariffs = [labels.get(value, value) for value in car.get('tariffs') or [] if value]
    rule = (job.get('work_rule') or {}) if isinstance(job.get('work_rule'), dict) else {}
    photo = dispatcher.get('photo_control') if isinstance(dispatcher.get('photo_control'), dict) else {}
    photo_text = ', '.join(part for part in (_text(photo.get('status')), _text(photo.get('reason')),
                                             _when(photo.get('checked_at'))) if part)

    sections = [
        {'title': 'Водитель', 'rows': _rows(
            _row('name', 'ФИО', _full_name(driver)), _row('phone', 'Телефон', _text(driver.get('phone'))),
            _row('iin', 'ИИН', _text(driver.get('iin')), mono=True),
            _row('license', 'Водительское удостоверение', _text(driver.get('driver_license')), mono=True),
            _row('email', 'Email', _text(driver.get('email'))))},
        {'title': 'Работа', 'rows': _rows(
            _row('park', 'Парк', _text(park.get('name')) or _text((entry or {}).get('name'))),
            _row('parkCity', 'Город парка', _text((entry or {}).get('city'))),
            _row('status', 'Статус', label, tone=tone),
            _row('employment', 'Занятость', EMPLOYMENT.get(job.get('employment_type'), _text(job.get('employment_type')))),
            _row('rule', 'Условия работы', _text(rule.get('name'))),
            _row('registered', 'Зарегистрирован', _when(job.get('created_date'))),
            _row('hired', 'Принят', _when(job.get('hire_date'))), _row('fired', 'Уволен', _when(job.get('fire_date'))),
            _row('archive', 'В архиве', 'да' if job.get('is_archive') else None))},
        {'title': 'Машина', 'rows': _rows(
            _row('car', 'Машина', car_title or None), _row('plate', 'Госномер', _text(car.get('license_plate')), mono=True),
            _row('callsign', 'Позывной', _text(car.get('callsign'))),
            _row('tariffs', 'Тарифы', ', '.join(tariffs) or None, chips=tariffs))},
    ]
    latest = []
    if isinstance(orders, dict):
        stats = [{'label': title, 'value': _number(orders.get(name))} for name, title in (
            ('today', 'Сегодня'), ('week', '7 дней'), ('month', '30 дней'), ('total', 'Всего'))]
        sections.append({'title': 'Заказы', 'stats': [item for item in stats if item['value'] is not None],
                         'rows': _rows(_row('firstOrder', 'Первый заказ', _when(orders.get('first_order_at'))),
                                       _row('lastOrder', 'Последний заказ', _when(orders.get('last_order_at'))))})
        # Последние три заказа — без адресов: их CRM не отдаёт.
        for order in orders.get('latest_orders') or []:
            if not isinstance(order, dict):
                continue
            mileage = order.get('mileage')
            km = _number(round(float(mileage) / 1000, 1)) if isinstance(mileage, (int, float)) else None
            latest.append(_row('order', _when(order.get('booked_at')) or 'Заказ', ' · '.join(part for part in (
                labels.get(order.get('category'), _text(order.get('category'))), _money(order.get('price')),
                km and km + ' км', PAYMENT.get(order.get('payment_method'), _text(order.get('payment_method'))))
                if part) or None))
    elif 'orders' in data:
        # null у CRM — «база заказов недоступна», а не «заказов нет».
        sections.append({'title': 'Заказы', 'rows': _rows(
            _row('orders', 'Заказы', 'база заказов CRM сейчас недоступна'))})
    if latest:
        sections.append({'title': 'Последние заказы', 'rows': _rows(*latest)})
    sections.append({'title': 'Баланс', 'rows': _rows(
        _row('balance', 'Баланс', _money(balance.get('current'))), _row('limit', 'Лимит', _money(balance.get('limit'))),
        _row('frozen', 'Заморожено', _money(balance.get('frozen')) if balance.get('frozen') else None),
        _row('blocked', 'Заблокировано', _money(balance.get('blocked')) if balance.get('blocked') else None),
        _row('lastOperation', 'Последняя операция', _when(balance.get('last_transaction_date'))))})
    sections.append({'title': 'Диспетчерская', 'rows': _rows(
        _row('dispatcherStatus', 'Статус', _text(dispatcher.get('current_status'))),
        _row('rating', 'Рейтинг', _number(dispatcher.get('rating')) or _text(dispatcher.get('rating'))),
        _row('diagnostics', 'Диагностика', _text(dispatcher.get('diagnostics_status'))),
        _row('priority', 'Приоритет', _text(dispatcher.get('priority'))),
        _row('thermobag', 'Термосумка', {True: 'есть', False: 'нет'}.get(dispatcher.get('has_thermobag'))),
        _row('photo', 'Фотоконтроль', photo_text or None),
        _row('statusChanged', 'Статус изменён', _when(dispatcher.get('status_changed_at'))),
        _row('synced', 'Обновлено', _when(dispatcher.get('synced_at') or dispatcher.get('web_synced_at'))))})

    yandex_park = str(park.get('yandex_id') or '').lower()
    account = str(data.get('account_id') or '').lower()
    return {
        'name': _full_name(driver), 'phone': _text(driver.get('phone')),
        'status': {'label': label, 'tone': tone} if label else None,
        'park': _text(park.get('name')), 'city': _text((entry or {}).get('city')),
        'car': car_title or None, 'ordersTotal': orders.get('total') if isinstance(orders, dict) else None,
        'fleetUrl': FLEET_DRIVER_URL % (account, yandex_park)
        if _HEX32.match(account) and _HEX32.match(yandex_park) else None,
        'sections': [section for section in sections if section['rows'] or section.get('stats')],
    }


def describe(db, driver_id, options, fetch=None, dictionary_fetch=None, profession=None):
    """Ответ ручек карточки: состояние поиска, данные для экрана и подсказка полей.

    profession(driver_id, park_id) — профессия из кабинета Fleet {'id', 'name'}
    или None (wazzup/driver_profession.py: lookup): в CRM её нет."""
    state, data, fetched_at, stale, error = lookup(db, driver_id, fetch=fetch)
    payload = {'driverId': driver_id, 'state': state, 'stale': stale, 'error': error,
               'fetchedAt': fetched_at.isoformat() if isinstance(fetched_at, datetime) else None}
    if state == 'found':
        dictionary = current_dictionary(db, fetch=dictionary_fetch)
        payload['info'] = present(data, dictionary)
        if profession is not None:
            park = (data.get('park') if isinstance(data, dict) else None) or {}
            payload['info']['profession'] = profession(driver_id, park.get('yandex_id'))
        payload['suggestion'] = suggest(data, dictionary, options)
    return payload
