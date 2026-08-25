"""Данные водителя из CRM yataxi + разбор ссылки на его аккаунт.

`POST https://backend.yataxi.kz/api/partners/driver-info`, тело
`{"account_id": "<id>"}`, авторизация заголовком `X-Integration-Token` — тем же
токеном, что у конкурса регистраций и обзвона фронт-офиса, поэтому его
разрешение целиком отдано `reg_contest.get_config`.

Проверено на живом API 25.08.2026:

  * успех — 200 с `{"data": {...}}`: пять блоков (`driver`, `park`,
    `employment`, `balance`, `car`, `dispatcher`, `orders`);
  * неизвестный id — честный 404 `{"error": "not_found"}`;
  * без токена — 401 `{"error": "unauthorized"}`;
  * **пустой или отсутствующий `account_id` — HTML-страница с кодом 200.**
    Это тот же капкан, что у конкурса регистраций и обзвона: Laravel отдаёт
    вёрстку вместо ошибки. Отсюда правило «незнакомый формат = ошибка», а не
    «пустой ответ»: иначе карточка сохранилась бы с пустым ФИО и никто бы не
    заметил.

Из всего ответа разделу нужно немного (ФИО, телефон, парк, машина), но храним
мы его целиком: поле, которое сегодня не понадобилось, завтра спросят, а
второй раз сходить в CRM за прошлогодней посылкой уже нельзя — водитель мог
сменить и номер, и парк.
"""

import logging
import os
import re

import requests

import reg_contest

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://backend.yataxi.kz/api/partners/driver-info"

# Терпение короткое и без повторов намеренно: запрос идёт СИНХРОННО, пока
# менеджер ждёт с открытой формой. Не ответила CRM — он вписывает ФИО руками,
# а не смотрит минуту в крутилку.
HTTP_TIMEOUT = 15

# id аккаунта в CRM — 32 шестнадцатеричных символа (id профиля исполнителя в
# Яндекс.Флите). Формат проверяем до похода в сеть: опечатка должна отвечать
# сразу, а не через таймаут.
_ACCOUNT_ID_RE = re.compile(r'^[0-9a-f]{32}$', re.IGNORECASE)

# Имена параметров, в которых лежит ИМЕННО водитель. Порядок значим: в ссылке
# вида /contractors?park_id=…&contractor_id=…&candidate_id=… все три значения
# похожи на id, и «первое найденное 32-значное» взяло бы ПАРК.
_DRIVER_QUERY_KEYS = (
    'contractor_id', 'driver_id', 'driver_profile_id', 'account_id',
    'courier_id', 'profile_id',
)

# Сегменты пути, после которых идёт id водителя:
#   /contractors/<id>/details        (Флит, новый вид)
#   /drivers/<id>/card               (Флит, старый вид)
#   /admin/driver-accounts/<id>      (админка yataxi)
_DRIVER_PATH_KEYS = ('contractors', 'contractor', 'drivers', 'driver',
                     'driver-accounts', 'couriers', 'courier')


class DriverLookupError(Exception):
    """Понятная человеку причина, почему данные водителя не приехали.

    `code` уходит во фронт, чтобы он мог отличить «не нашли» (можно продолжать
    и вписать ФИО руками) от «CRM недоступна» (стоит попробовать ещё раз).
    """

    def __init__(self, message, code='driver_lookup_failed', status=502):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def extract_account_id(value):
    """Достаёт id водителя из того, что вставил сотрудник.

    Принимает и сам id, и ссылку на аккаунт — сотрудник копирует адресную
    строку, а не выковыривает из неё 32 символа. Оба живых вида ссылки:

        https://fleet.yandex.kz/contractors?park_id=<парк>&contractor_id=<водитель>&candidate_id=<…>
        https://fleet.yandex.kz/contractors/<водитель>/details?park_id=<парк>

    Порядок разбора — от однозначного к предположительному:

      1. именованный параметр про водителя (`contractor_id`, `driver_id`…);
      2. сегмент пути после `contractors`/`drivers`/`driver-accounts`;
      3. голое значение без схемы и слэшей — значит человек вставил сам id.

    Угадывать «единственный 32-значный кусок в строке» мы отказываемся: в
    первой же ссылке таких кусков ДВА, и вторым идёт парк. Молча подставить
    парк вместо водителя — это карточка с чужим ФИО, а такую ошибку в реестре
    никто не заметит. Поэтому непонятная ссылка честно отвечает отказом.

    Возвращает id в нижнем регистре или None.
    """
    raw = str(value or '').strip()
    if not raw:
        return None

    # Голый id — самый частый случай, когда сотрудник уже знает, что вставлять.
    if _ACCOUNT_ID_RE.match(raw):
        return raw.lower()

    # Всё остальное разбираем как адрес. Схему при необходимости достраиваем:
    # «fleet.yandex.kz/contractors/…» без http:// — обычная копипаста.
    try:
        from urllib.parse import parse_qs, urlparse

        candidate = raw if '://' in raw else 'https://' + raw.lstrip('/')
        parsed = urlparse(candidate)
    except Exception:  # noqa: BLE001 — мусор во входе не должен ронять запрос
        return None

    params = parse_qs(parsed.query or '')
    for key in _DRIVER_QUERY_KEYS:
        for found in params.get(key, []):
            token = str(found or '').strip()
            if _ACCOUNT_ID_RE.match(token):
                return token.lower()

    segments = [segment for segment in (parsed.path or '').split('/') if segment]
    for index, segment in enumerate(segments):
        if segment.lower() not in _DRIVER_PATH_KEYS:
            continue
        if index + 1 >= len(segments):
            continue
        token = segments[index + 1].strip()
        if _ACCOUNT_ID_RE.match(token):
            return token.lower()

    return None


def get_config(env_file=".env.codex.local"):
    """Адрес эндпоинта + токен CRM.

    Токен общий с конкурсом регистраций и обзвоном фронт-офиса — CRM одна и та
    же, и заводить третью переменную на Render незачем. Адрес не секретный,
    поэтому живёт значением по умолчанию.
    """
    token = (reg_contest.get_config(env_file) or {}).get("token")
    url = os.getenv("CRM_DRIVER_INFO_URL") or DEFAULT_API_URL
    return {"url": url, "token": token}


def is_configured(config=None):
    cfg = config or get_config()
    return bool(cfg.get("url") and cfg.get("token"))


def fetch_driver(account_id, config=None, timeout=HTTP_TIMEOUT, session=None):
    """Карточка водителя из CRM. Бросает DriverLookupError с внятной причиной.

    Возвращает словарь `data` из ответа как есть — раскладку по полям делает
    `summarize`, а сырой словарь уходит в снимок карточки.
    """
    account_id = str(account_id or '').strip()
    if not _ACCOUNT_ID_RE.match(account_id):
        raise DriverLookupError(
            'ID водителя должен быть из 32 символов — проверьте ссылку',
            code='bad_account_id', status=400,
        )

    cfg = config or get_config()
    if not cfg.get('token'):
        raise DriverLookupError(
            'Связь с CRM не настроена — заполните данные водителя вручную',
            code='not_configured', status=503,
        )

    http = session or requests
    try:
        response = http.post(
            cfg['url'],
            json={'account_id': account_id},
            headers={
                'X-Integration-Token': cfg['token'],
                'Content-Type': 'application/json',
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.warning('parcels: CRM недоступна при запросе водителя %s: %s', account_id, exc)
        raise DriverLookupError(
            'CRM не ответила — попробуйте ещё раз или заполните вручную',
            code='crm_unavailable', status=502,
        )

    if response.status_code == 404:
        raise DriverLookupError(
            'Водитель с таким ID не найден',
            code='driver_not_found', status=404,
        )
    if response.status_code == 401:
        raise DriverLookupError(
            'CRM не приняла ключ доступа — сообщите администратору',
            code='crm_unauthorized', status=502,
        )
    if response.status_code >= 400:
        raise DriverLookupError(
            'CRM ответила ошибкой %s' % response.status_code,
            code='crm_error', status=502,
        )

    # Главная ловушка этой CRM: на пустой account_id она отдаёт HTML-страницу с
    # кодом 200. Разбор поэтому строгий — незнакомый формат считаем ошибкой, а
    # не «водитель без полей».
    try:
        payload = response.json()
    except ValueError:
        log.warning('parcels: CRM ответила не-JSON на водителя %s', account_id)
        raise DriverLookupError(
            'CRM вернула неожиданный ответ — заполните данные вручную',
            code='crm_bad_payload', status=502,
        )

    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data:
        raise DriverLookupError(
            'CRM вернула пустую карточку водителя',
            code='crm_bad_payload', status=502,
        )
    return data


def _text(value, limit):
    """Строка для колонки фиксированной длины: обрезаем сами, а не на INSERT.

    Иначе длинное значение из CRM роняет сохранение карточки целиком, и
    менеджер видит «внутреннюю ошибку» вместо посылки.
    """
    text = str(value or '').strip()
    return text[:limit] if text else None


def _full_name(driver):
    """ФИО одной строкой.

    CRM отдаёт и готовое `full_name`, и части по отдельности, причём готовое
    короче: у живого водителя `full_name` = «Abdikarim Nurkanat», а отчество
    лежит только в `middle_name`. Собираем из частей, когда они есть, — по
    отчеству посылку тоже ищут.
    """
    parts = [
        str(driver.get('last_name') or '').strip(),
        str(driver.get('first_name') or '').strip(),
        str(driver.get('middle_name') or '').strip(),
    ]
    assembled = ' '.join(part for part in parts if part)
    return assembled or str(driver.get('full_name') or '').strip() or None


def _car_title(car):
    """Машина одной строкой: «LADA (ВАЗ) Priora · 252АЕN13»."""
    model = str(car.get('model') or '').strip()
    plate = str(car.get('license_plate') or '').strip()
    if model and plate:
        return '%s · %s' % (model, plate)
    return model or plate or None


def summarize(data):
    """Раскладывает ответ CRM по колонкам карточки.

    Всё, что не разложилось, остаётся в `info` и уезжает в `parcels.driver_info`
    целиком — так снимок переживает и добавление полей в CRM, и наши будущие
    вопросы к нему.
    """
    data = data if isinstance(data, dict) else {}
    driver = data.get('driver') if isinstance(data.get('driver'), dict) else {}
    park = data.get('park') if isinstance(data.get('park'), dict) else {}
    car = data.get('car') if isinstance(data.get('car'), dict) else {}

    return {
        'account_id': _text(data.get('account_id'), 64),
        'name': _text(_full_name(driver), 200),
        'phone': _text(driver.get('phone'), 32),
        'license': _text(driver.get('driver_license'), 64),
        'park': _text(park.get('name'), 160),
        # id парка во Флите — из него и из account_id собирается ссылка на
        # аккаунт водителя. Держим отдельным полем, а не только внутри `info`:
        # ссылка нужна и в строке реестра, а туда снимок не отдаётся.
        'park_id': _text(park.get('yandex_id'), 64),
        'callsign': _text(car.get('callsign'), 120),
        'car': _text(_car_title(car), 200),
        'info': data,
    }


def lookup(value, config=None, timeout=HTTP_TIMEOUT, session=None):
    """Полный путь «что вставил сотрудник» → разложенная карточка водителя."""
    account_id = extract_account_id(value)
    if not account_id:
        raise DriverLookupError(
            'Не удалось разобрать ссылку — вставьте адрес карточки водителя '
            'или сам ID из 32 символов',
            code='bad_account_id', status=400,
        )
    return summarize(fetch_driver(account_id, config=config, timeout=timeout, session=session))
