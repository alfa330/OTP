"""Данные водителя из CRM yataxi — для проверки права на воду.

Клиент CRM общий с «Посылками» (`parcels/drivers.py`): тот же эндпоинт
`driver-info`, тот же токен, тот же разбор ссылки на аккаунт. Здесь только
выборка полей, по которым решается право на воду.

Что CRM отдаёт по живым снимкам (проверено 30.09.2026 на карточках «Посылок»):

  * `orders` — `today`, `week`, `month`, `total`, `first_order_at`,
    `last_order_at`. `week` и `month` — СКОЛЬЗЯЩИЕ окна, а не календарные: у
    снимка, снятого в понедельник, `week` = 27 при `today` = 2, у снятого
    1 сентября `month` = 121 при `today` = 0. Поездок «с даты» CRM не отдаёт;
  * `car.tariffs` — коды тарифов машины в наименованиях Флита (`econom`,
    `comfort`, `comfort_plus`, `business`, …);
  * `employment` — `created_date` (регистрация в парке), `work_status`,
    `is_blocked`, `fire_date`;
  * `driver.iin` — ИИН: он один у человека во всех парках, а аккаунт в каждом
    парке свой;
  * `dispatcher.photo_control.status` — фотоконтроль (ФК), `passed` — пройден.

Числа CRM могут прийти строкой — приводим к int, а негодное считаем «нет
данных» (None), а не нулём: ноль заказов делает водителя новым, и мусор в
ответе не должен молча выдавать приветственный блок.
"""

from parcels import drivers as crm

DriverLookupError = crm.DriverLookupError


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
        },
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
    """Карточка для ответа фронту: без полного снимка CRM и без ИИН.

    ИИН нужен только серверу — для «одному человеку один приветственный блок»;
    на экран он не выводится, значит и в ответ не едет.
    """
    return {key: value for key, value in (driver or {}).items()
            if key not in ('info', 'iin')}
