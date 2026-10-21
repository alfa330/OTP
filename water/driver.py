"""Данные водителя из CRM yataxi — для проверки права на воду.

Клиент CRM общий с «Посылками» (`parcels/drivers.py`): тот же эндпоинт
`driver-info`, тот же токен, тот же разбор ссылки на аккаунт. Здесь только
выборка полей, по которым решается право на воду.

Что CRM отдаёт по живым снимкам (проверено 30.09.2026 на карточках «Посылок»):

  * `orders` — `today`, `week`, `month`, `total`, `first_order_at`,
    `last_order_at`. `week` и `month` — СКОЛЬЗЯЩИЕ окна, а не календарные: у
    снимка, снятого в понедельник, `week` = 27 при `today` = 2, у снятого
    1 сентября `month` = 121 при `today` = 0. `total` по документации «за всё
    время», а на деле — окно около полугода: старые заказы из него выпадают
    каждый день (21.10.2026 у водителя Астаны за неделю 173 поездки, а
    `total` вырос на 6). Поездки с прошлой выдачи им поэтому не считаются;
  * `orders.last_7_days` (с 01.10.2026) — сами заказы того же окна, что
    `week` (`count` = `week`), у каждого `category` — тариф заказа в тех же
    кодах Флита, и `booked_at` — время заказа (местное, Алматы, без пояса:
    граница окна `to` совпадает с часами Алматы в момент запроса). Список
    обрезается на 500 заказах (`truncated`);
  * отдельный запрос `POST /api/partners/orders` — число завершённых заказов
    за календарные даты (обе включительно), без самих заказов. 503 — база
    заказов CRM недоступна: нулей CRM в этом случае намеренно не отдаёт;
  * `car.tariffs` — коды тарифов машины в наименованиях Флита (`econom`,
    `comfort`, `comfort_plus`, `business`, …);
  * `employment` — `created_date` (регистрация в парке), `work_status`,
    `is_blocked`, `fire_date`. `is_blocked` — не запрет работать в парке, а
    «карточка заблокирована сменой номера» (так поле описано в спецификации
    CRM): 12.10.2026 флаг стоял у водителя со статусом `working` и 99
    заказами за неделю, у которого в карточке сменился телефон;
  * `driver.iin` — ИИН: он один у человека во всех парках, а аккаунт в каждом
    парке свой;
  * `dispatcher.photo_control.status` — фотоконтроль (ФК), `passed` — пройден.

Числа CRM могут прийти строкой — приводим к int, а негодное считаем «нет
данных» (None), а не нулём: ноль заказов делает водителя новым, и мусор в
ответе не должен молча выдавать приветственный блок.
"""

import logging

import requests

from parcels import drivers as crm

DriverLookupError = crm.DriverLookupError

log = logging.getLogger(__name__)


def _int_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _text(value, limit):
    text = str(value or '').strip()
    return text[:limit] if text else None


def _tariffs(car):
    raw = car.get('tariffs') if isinstance(car, dict) else None
    if not isinstance(raw, list):
        return []
    seen = []
    for item in raw:
        code = str(item or '').strip().lower()
        if code and code not in seen:
            seen.append(code)
    return seen


def _week_by_tariff(orders):
    """Заказы за последние 7 дней по тарифам: ({код: заказов}, обрезан ли список).

    Нет списка (ответ CRM старого вида, база заказов недоступна) — (None,
    False): «нет данных», а не «ноль заказов на каждом тарифе». Заказ без
    тарифа не считается ни к одному.
    """
    block = orders.get('last_7_days') if isinstance(orders, dict) else None
    items = block.get('items') if isinstance(block, dict) else None
    if not isinstance(items, list):
        return None, False
    counts = {}
    for item in items:
        code = str((item.get('category') if isinstance(item, dict) else None) or '').strip().lower()
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts, block.get('truncated') is True


def _order_times(orders):
    """Время каждого заказа за 7 дней — по нему считаются поездки после
    прошлой выдачи (rules.py).

    `booked_at` — время заказа (им же CRM ставит `last_order_at`), без него —
    `ended_at`. Нет списка — None, «нет данных», а не пустой список: пустой
    значил бы «после выдачи ни одной поездки».
    """
    block = orders.get('last_7_days') if isinstance(orders, dict) else None
    items = block.get('items') if isinstance(block, dict) else None
    if not isinstance(items, list):
        return None
    times = []
    for item in items:
        moment = _text(item.get('booked_at') or item.get('ended_at'), 32) if isinstance(item, dict) else None
        if moment:
            times.append(moment)
    return times


def _window_from(orders):
    block = orders.get('last_7_days') if isinstance(orders, dict) else None
    return _text(block.get('from'), 32) if isinstance(block, dict) else None


def normalize(data):
    """Ответ CRM → поля, которые нужны разделу. Полный ответ — в `info`."""
    data = data if isinstance(data, dict) else {}
    summary = crm.summarize(data)
    driver = data.get('driver') if isinstance(data.get('driver'), dict) else {}
    car = data.get('car') if isinstance(data.get('car'), dict) else {}
    employment = data.get('employment') if isinstance(data.get('employment'), dict) else {}
    orders = data.get('orders') if isinstance(data.get('orders'), dict) else {}

    dispatcher = data.get('dispatcher') if isinstance(data.get('dispatcher'), dict) else {}
    photo = dispatcher.get('photo_control') if isinstance(dispatcher.get('photo_control'), dict) else {}

    iin = ''.join(ch for ch in str(driver.get('iin') or '') if ch.isdigit())
    week_by_tariff, week_truncated = _week_by_tariff(orders)
    return {
        'account_id': summary.get('account_id'),
        'iin': iin[:20] or None,
        'name': summary.get('name'),
        'phone': summary.get('phone'),
        'park': summary.get('park'),
        'park_id': summary.get('park_id'),
        'car': summary.get('car'),
        'tariffs': _tariffs(car),
        'work_status': _text(employment.get('work_status'), 32),
        'is_blocked': bool(employment.get('is_blocked')),
        'fired': bool(employment.get('fire_date')),
        'registered_at': _text(employment.get('created_date') or employment.get('hire_date'), 32),
        # Фотоконтроль из Диспетчерской. По документации CRM поле приходит,
        # только когда оператор обновил карточку из веб-кабинета, — на 30.09.2026
        # пусто у всех 19 карточек «Посылок». None — «данных нет», не «не прошёл».
        'photo_control': {
            'status': _text(photo.get('status'), 32),
            'checked_at': _text(photo.get('checked_at'), 32),
        },
        'orders': {
            'today': _int_or_none(orders.get('today')),
            'week': _int_or_none(orders.get('week')),
            'month': _int_or_none(orders.get('month')),
            'total': _int_or_none(orders.get('total')),
            'last_order_at': _text(orders.get('last_order_at'), 32),
            # Разбивка недели по тарифам — для экрана: сотрудник видит, сколько
            # заказов на тарифах программы. Само право по-прежнему считается
            # по `week`/`total`, то есть по всем тарифам.
            'week_by_tariff': week_by_tariff,
            'week_truncated': week_truncated,
            # Начало окна «7 дней»: прошлая выдача позже него — поездки после
            # неё все есть в списке `order_times`.
            'window_from': _window_from(orders),
        },
        # Время заказов за 7 дней — серверу для счёта поездок, на экран не едет.
        'order_times': _order_times(orders),
        'info': data,
    }


def lookup(raw, **kwargs):
    """Что вставил сотрудник (ссылка или ID) → нормализованная карточка.

    Бросает DriverLookupError с понятной причиной: не разобрали ссылку, не
    нашли водителя, CRM не ответила.
    """
    account_id = crm.extract_account_id(raw)
    if not account_id:
        raise crm.DriverLookupError(
            'Не удалось разобрать ссылку — вставьте адрес карточки водителя '
            'или сам ID из 32 символов',
            code='bad_account_id', status=400,
        )
    driver = normalize(crm.fetch_driver(account_id, **kwargs))
    # CRM отдаёт account_id в ответе; если вдруг нет — берём то, что спросили.
    driver['account_id'] = driver.get('account_id') or account_id
    return driver


def public(driver):
    """Карточка для ответа фронту: без полного снимка CRM, без ИИН и без
    времени заказов.

    ИИН нужен только серверу — для «одному человеку один приветственный блок»;
    на экран он не выводится, значит и в ответ не едет. Время заказов — тоже
    только для счёта поездок на сервере.
    """
    return {key: value for key, value in (driver or {}).items()
            if key not in ('info', 'iin', 'order_times')}


def orders_url(config=None):
    """Адрес «поездок за период» — сосед driver-info в той же CRM.

    Строится от адреса driver-info, а не отдельной переменной: стенд подменяет
    CRM одной `CRM_DRIVER_INFO_URL`, и запрос за период должен уйти туда же.
    """
    base = str((config or {}).get('url') or crm.DEFAULT_API_URL).rstrip('/')
    return base.rsplit('/', 1)[0] + '/orders'


def orders_count(account_id, date_from, date_to, config=None, timeout=crm.HTTP_TIMEOUT, session=None):
    """Завершённые заказы аккаунта за календарные даты, обе включительно.

    Бросает DriverLookupError: связи нет, база заказов CRM недоступна (503),
    ответ незнакомой формы. Ноль от ошибки отличаем строго — ноль поездок
    водителю отказывает, а ошибка просит повторить.
    """
    cfg = config or crm.get_config()
    if not cfg.get('token'):
        raise DriverLookupError('Связь с CRM не настроена — сообщите администратору',
                                code='not_configured', status=503)
    account_id = str(account_id or '').strip().lower()
    http = session or requests
    try:
        response = http.post(
            orders_url(cfg),
            json={'account_ids': [account_id], 'from': str(date_from), 'to': str(date_to)},
            headers={'X-Integration-Token': cfg['token'], 'Content-Type': 'application/json'},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.warning('water: CRM недоступна при запросе поездок водителя %s: %s', account_id, exc)
        raise DriverLookupError('CRM не ответила — попробуйте ещё раз через минуту',
                                code='crm_unavailable', status=502)
    if response.status_code == 503:
        raise DriverLookupError('База заказов CRM недоступна — попробуйте ещё раз через минуту',
                                code='crm_unavailable', status=502)
    if response.status_code >= 400:
        raise DriverLookupError('CRM ответила ошибкой %s' % response.status_code,
                                code='crm_error', status=502)
    # Та же ловушка, что у driver-info: на кривой запрос CRM может ответить
    # HTML-страницей с кодом 200. Незнакомый ответ — ошибка, а не ноль.
    try:
        payload = response.json()
    except ValueError:
        payload = None
    data = payload.get('data') if isinstance(payload, dict) else None
    entry = None
    if isinstance(data, dict):
        entry = next((value for key, value in data.items()
                      if str(key).strip().lower() == account_id), None)
    count = _int_or_none(entry.get('orders_count')) if isinstance(entry, dict) else None
    if count is None:
        log.warning('water: CRM вернула незнакомый ответ о поездках водителя %s', account_id)
        raise DriverLookupError('CRM вернула неожиданный ответ — попробуйте ещё раз через минуту',
                                code='crm_bad_payload', status=502)
    return count
