# -*- coding: utf-8 -*-
"""Клиенты внешних систем раздела «Аренда авто». Только чтение.

Все четыре системы — через их собственный API, не через куки браузера (проверено
27.10.2026, рецепты — в заметке «API Yume, Wialon, ГЛОНАСС, Efir»):

    Yume Cloud     POST /v1/auth/login/ → JWT; GET /v1/crm/inventories/ — весь парк со статусом
                   машины (2 — «В аренде») и госномером в car.number. Нужна учётка с правом
                   «просмотр инвентаря»: группа «ООЗ» его не имела, «Менеджер» — имеет.
    Wialon         токен на просмотр формой oauth/authorize.html (или готовый токен учётки) →
                   token/login → core/search_items: имя объекта и последняя точка. Адрес —
                   геокодер Wialon gis_geocode по id пользователя учётки.
    ГЛОНАССсофт    POST /api/v3/auth/login → AuthId; vehicles/find; vehicles/getlastdata с
                   ключом «vehiclesIds» — время сигнала (UTC), точка и готовый адрес.
    Efir           POST /api/auth/login → JWT в «data»; существующая сессия слежения →
                   /api/tracking/{id}/units — точка и время в миллисекундах. Свой геокодер
                   Efir на 27.10 отвечал 500, адрес даёт геокодер Wialon.

Каждый клиент отдаёт объекты GPS одной формы — {'site', 'name', 'plates', 't', 'lat', 'lon',
'address'} (t — unix-время последнего сигнала) — или бросает SourceError с понятной причиной.
Пароли берутся из окружения и никуда не пишутся.
"""

import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import requests

from . import plates

UA = 'iCORE rental report'
TIMEOUT = 30

SITE_WIALON = 'Wialon'
SITE_GLONASS = 'ГЛОНАСС'
SITE_EFIR = 'Efir'


class SourceError(Exception):
    """Источник не отдал данные. Текст — для ночной смены и журнала."""


def _base_api(url, default):
    """Корень API по адресу сайта из окружения: «https://host/login» → «https://host/api»."""
    parts = urlsplit(str(url or '').strip())
    if not parts.scheme or not parts.netloc:
        return default
    return '%s://%s/api' % (parts.scheme, parts.netloc)


def _json(response, what):
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError('%s: ответ не JSON (HTTP %s)' % (what, response.status_code)) from exc


def _list(payload):
    """Списки приходят и голыми, и в {'data': […]} / {'results': […]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('data', 'results', 'items'):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


# ─── Yume Cloud ────────────────────────────────────────────────────────────────

YUME_API = 'https://api.yume.cloud'
_YUME_STATUS_RANK = {2: 0, 1: 1, 0: 2}


def yume_model(car, fallback_name):
    """«Hongqi E-QM5» из марки и модели карточки; без них — название инвентаря как есть."""
    brand = str((car or {}).get('brand') or '').strip()
    model = str((car or {}).get('model') or '').strip()
    name = ' '.join(part for part in (brand, model) if part)
    return name or str(fallback_name or '').strip() or None


def _yume_better(candidate, current):
    """Один госномер бывает у двух записей инвентаря (машину завели заново) — берём живую,
    а из живых ту, что в аренде, затем забронированную, затем свободную."""
    if candidate['deleted'] != current['deleted']:
        return not candidate['deleted']
    rank = lambda entry: _YUME_STATUS_RANK.get(entry['status'], 9)  # noqa: E731
    return rank(candidate) < rank(current)


class YumeSource:
    def __init__(self, login, password, *, session=None, api=YUME_API, max_pages=300):
        self.login = login
        self.password = password
        self.session = session
        self.api = api
        self.max_pages = max_pages

    def fetch(self):
        """Весь парк для ночного отчёта: {госномер: {'status', 'deleted', 'model'}}."""
        return {car['plate']: {'status': car['status'], 'deleted': car['deleted'], 'model': car['model']}
                for car in self.fetch_cars()}

    def fetch_cars(self):
        """Весь парк: по машине на госномер — {'plate', 'status', 'deleted', 'model', 'keys', 'label'}.

        keys — ключи модели (catalog.car_keys): по ним карточки «Наличия авто» находят свои машины.
        Клиент, который держит машину (current_client), не берётся вовсе.
        """
        from .catalog import car_keys

        if not self.login or not self.password:
            raise SourceError('не заданы YUME_CLOUD_LOGIN / YUME_CLOUD_PASSWORD')
        http = self.session or requests.Session()
        http.headers.update({'User-Agent': UA, 'Accept': 'application/json'})
        response = http.post(self.api + '/v1/auth/login/', timeout=TIMEOUT,
                             json={'username': self.login, 'password': self.password})
        if response.status_code != 200:
            raise SourceError('вход в Yume не удался (HTTP %s)' % response.status_code)
        token = (_json(response, 'Yume') or {}).get('access')
        if not token:
            raise SourceError('Yume не выдал токен')
        headers = {'Authorization': 'Bearer ' + token}
        cars = {}
        for page in range(1, self.max_pages + 1):
            response = http.get(self.api + '/v1/crm/inventories/', params={'page': page},
                                headers=headers, timeout=TIMEOUT)
            if response.status_code == 403:
                raise SourceError('учётке Yume закрыт инвентарь — нужно право «просмотр инвентаря»')
            if response.status_code != 200:
                raise SourceError('Yume: инвентарь не открылся (HTTP %s)' % response.status_code)
            payload = _json(response, 'Yume')
            for item in payload.get('results') or []:
                car = item.get('car') or {}
                plate = plates.normalize(car.get('number'))
                if not plates.is_plate(plate):
                    continue
                model = yume_model(car, item.get('name'))
                entry = {'plate': plate, 'status': item.get('status'), 'deleted': bool(item.get('deleted')),
                         'model': model, 'label': model or 'Без названия',
                         'keys': sorted(car_keys({'brand': car.get('brand'), 'model': car.get('model'),
                                                  'name': item.get('name')}))}
                current = cars.get(plate)
                if current is None or _yume_better(entry, current):
                    cars[plate] = entry
            if not payload.get('next'):
                break
        else:
            raise SourceError('Yume: страниц инвентаря больше %d' % self.max_pages)
        return list(cars.values())


# ─── Wialon ────────────────────────────────────────────────────────────────────

WIALON_SDK = 'https://hst-api.wialon.com'
WIALON_GEOCODE = 'https://geocode-maps.wialon.com/hst-api.wialon.com/gis_geocode'
WIALON_ACCESS_VIEW = 0x100 | 0x200    # онлайн-слежение и просмотр объектов
WIALON_UNIT_FLAGS = 0x1 | 0x400       # базовые свойства и последнее сообщение с точкой
WIALON_ADDRESS_FLAGS = 1255211008     # «улица, дом, город, район, страна» — как на сайте
GEOCODE_BATCH = 50


class WialonSource:
    """Несколько учёток Wialon Hosting. Сбой одной не отменяет остальные (errors)."""

    def __init__(self, accounts, *, session_factory=requests.Session, sdk=WIALON_SDK,
                 geocode_url=WIALON_GEOCODE, token_duration=600, retry_pause=3.0, sleep=time.sleep):
        # accounts — [{'login', 'password'}] или [{'token'}]; пустые пропускаются.
        self.accounts = [a for a in accounts if a.get('token') or (a.get('login') and a.get('password'))]
        self.session_factory = session_factory
        self.sdk = sdk
        self.geocode_url = geocode_url
        self.token_duration = token_duration
        self.retry_pause = retry_pause
        self.sleep = sleep

    def _call(self, http, svc, params, sid=None):
        data = {'svc': svc, 'params': json.dumps(params)}
        if sid:
            data['sid'] = sid
        response = http.post(self.sdk + '/wialon/ajax.html', data=data, timeout=TIMEOUT)
        payload = _json(response, 'Wialon')
        if isinstance(payload, dict) and payload.get('error'):
            raise SourceError('Wialon %s: ошибка %s' % (svc, payload.get('error')))
        return payload

    def _token(self, http, account):
        """Токен только на просмотр, живёт token_duration секунд и истекает сам.

        Вход с сервера (27.10.2026, первый сбор на проде) у двух учёток из четырёх не прошёл,
        хотя с компьютера те же учётки входили весь день: поэтому один повтор через паузу, а в
        причине — HTTP-статус и код Wialon (svc_error), чтобы отказ было видно в самом отчёте.
        """
        reason = None
        for attempt in range(2):
            if attempt:
                self.sleep(self.retry_pause)
            response = http.post(self.sdk + '/oauth/authorize.html', allow_redirects=False, timeout=TIMEOUT,
                                 headers={'Origin': 'https://hosting.wialon.com',
                                          'Referer': 'https://hosting.wialon.com/login.html'},
                                 data={'response_type': 'token', 'wialon_sdk_url': self.sdk, 'success_uri': '',
                                       'login_uri': '', 'client_id': 'iCORE', 'redirect_uri':
                                       'https://hosting.wialon.com/post_token.html',
                                       'access_type': str(WIALON_ACCESS_VIEW), 'activation_time': '0',
                                       'duration': str(self.token_duration), 'flags': '0x1', 'sign': '',
                                       'login': account['login'], 'passw': account['password']})
            location = urlsplit(response.headers.get('Location', ''))
            query = parse_qs(location.query)
            query.update(parse_qs(location.fragment))
            token = (query.get('access_token') or [None])[0]
            if token:
                return token
            code = (query.get('svc_error') or [None])[0]
            reason = 'HTTP %s%s' % (response.status_code,
                                    ', код Wialon %s' % code if code else (', без редиректа' if not location.netloc else ''))
        raise SourceError('вход в Wialon не удался (%s)' % reason)

    def geocode(self, http, uid, points):
        """Адреса точек [(lat, lon)] пачками; не ответил геокодер — None на месте адреса."""
        addresses = []
        for start in range(0, len(points), GEOCODE_BATCH):
            batch = points[start:start + GEOCODE_BATCH]
            coords = json.dumps([{'lon': lon, 'lat': lat} for lat, lon in batch])
            try:
                response = http.get(self.geocode_url, timeout=TIMEOUT,
                                    params={'coords': coords, 'flags': WIALON_ADDRESS_FLAGS, 'uid': uid})
                names = response.json() if response.status_code == 200 else None
            except (requests.RequestException, ValueError):
                names = None
            if not isinstance(names, list) or len(names) != len(batch):
                names = [None] * len(batch)
            addresses.extend(str(name).strip() or None if name else None for name in names)
        return addresses

    def fetch(self, extra_points=()):
        """(объекты GPS, адреса extra_points, ошибки учёток).

        extra_points — точки других систем без своего адреса (Efir): их адреса берутся
        геокодером первой учётки, которая открылась.
        """
        units, errors = [], []
        extra = list(extra_points)
        extra_addresses = None
        for index, account in enumerate(self.accounts, start=1):
            http = self.session_factory()
            http.headers.update({'User-Agent': UA})
            sid = None
            try:
                token = account.get('token') or self._token(http, account)
                login = self._call(http, 'token/login', {'token': token})
                sid = login.get('eid')
                uid = (login.get('user') or {}).get('id')
                if not sid:
                    raise SourceError('Wialon не открыл сессию')
                found = self._call(http, 'core/search_items', {
                    'spec': {'itemsType': 'avl_unit', 'propName': 'sys_name', 'propValueMask': '*',
                             'sortType': 'sys_name'},
                    'force': 1, 'flags': WIALON_UNIT_FLAGS, 'from': 0, 'to': 0}, sid)
                rows = []
                for item in found.get('items') or []:
                    position = item.get('pos') or {}
                    rows.append({'site': SITE_WIALON, 'account': index, 'name': item.get('nm'),
                                 'plates': plates.find_in_name(item.get('nm')),
                                 't': position.get('t'), 'lat': position.get('y'), 'lon': position.get('x'),
                                 'address': None})
                located = [row for row in rows if row['lat'] is not None and row['lon'] is not None]
                if uid and located:
                    for row, address in zip(located, self.geocode(http, uid, [(r['lat'], r['lon']) for r in located])):
                        row['address'] = address
                if uid and extra and extra_addresses is None:
                    extra_addresses = self.geocode(http, uid, extra)
                units.extend(rows)
            except (SourceError, requests.RequestException, ValueError, KeyError) as exc:
                errors.append('Wialon, учётка %d: %s' % (index, exc))
            finally:
                if sid:
                    try:
                        self._call(http, 'core/logout', {}, sid)
                    except Exception:  # noqa: BLE001 — выход не обязателен, токен истечёт сам
                        pass
        if not self.accounts:
            errors.append('не заданы учётки Wialon (WIALON_LOGIN_1 / WIALON_PASSWORD_1 …)')
        return units, (extra_addresses or [None] * len(extra)), errors


# ─── ГЛОНАССсофт ───────────────────────────────────────────────────────────────

GLONASS_API = 'https://regions.glonasssoft.ru/api'


def parse_utc(text):
    """«2026-10-27T06:04:20Z» → unix-время. ГЛОНАСС отдаёт время сигнала в UTC."""
    raw = str(text or '').strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


class GlonassSource:
    def __init__(self, login, password, *, session=None, api=GLONASS_API):
        self.login = login
        self.password = password
        self.session = session
        self.api = api

    def fetch(self):
        if not self.login or not self.password:
            raise SourceError('не заданы GLONASS_SOFT_LOGIN / GLONASS_SOFT_PASSWORD')
        http = self.session or requests.Session()
        http.headers.update({'User-Agent': UA, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        response = http.post(self.api + '/v3/auth/login', timeout=TIMEOUT,
                             json={'login': self.login, 'password': self.password})
        if response.status_code != 200:
            raise SourceError('вход в ГЛОНАСС не удался (HTTP %s)' % response.status_code)
        auth = _json(response, 'ГЛОНАСС') or {}
        token = auth.get('AuthId') or auth.get('authId')
        if not token:
            raise SourceError('ГЛОНАСС не выдал AuthId')
        headers = {'X-Auth': token}
        response = http.post(self.api + '/v3/vehicles/find', json={}, headers=headers, timeout=TIMEOUT)
        if response.status_code != 200:
            raise SourceError('ГЛОНАСС: список машин не открылся (HTTP %s)' % response.status_code)
        names = {v.get('vehicleId'): v.get('name') for v in _list(_json(response, 'ГЛОНАСС'))
                 if v.get('vehicleId') is not None}
        if not names:
            return []
        response = http.post(self.api + '/v3/vehicles/getlastdata', headers=headers, timeout=TIMEOUT,
                             json={'vehiclesIds': list(names)})
        if response.status_code != 200:
            raise SourceError('ГЛОНАСС: последние данные не открылись (HTTP %s)' % response.status_code)
        last = {item.get('vehicleId'): item for item in _list(_json(response, 'ГЛОНАСС'))}
        units = []
        for vehicle_id, name in names.items():
            item = last.get(vehicle_id) or {}
            units.append({'site': SITE_GLONASS, 'name': name, 'plates': plates.find_in_name(name),
                          't': parse_utc(item.get('recordTime')), 'lat': item.get('latitude'),
                          'lon': item.get('longitude'), 'address': (item.get('address') or '').strip() or None})
        return units


# ─── Efir ──────────────────────────────────────────────────────────────────────

class EfirSource:
    def __init__(self, site_url, login, password, *, session=None):
        self.api = _base_api(site_url, '')
        self.login = login
        self.password = password
        self.session = session

    def fetch(self):
        if not self.api or not self.login or not self.password:
            raise SourceError('не заданы EFIR_SOFT_URL / EFIR_SOFT_LOGIN / EFIR_SOFT_PASSWORD')
        http = self.session or requests.Session()
        http.headers.update({'User-Agent': UA, 'Accept': 'application/json'})
        response = http.post(self.api + '/auth/login', timeout=TIMEOUT,
                             json={'username': self.login, 'password': self.password})
        if response.status_code != 200:
            raise SourceError('вход в Efir не удался (HTTP %s)' % response.status_code)
        token = (_json(response, 'Efir') or {}).get('data')
        if not isinstance(token, str) or not token:
            raise SourceError('Efir не выдал токен')
        headers = {'Authorization': 'Bearer ' + token}
        # Берём существующую сессию слежения: /tracking/new завела бы новую на каждом отчёте.
        response = http.get(self.api + '/tracking/sessions', headers=headers, timeout=TIMEOUT)
        sessions = _list(_json(response, 'Efir')) if response.status_code == 200 else []
        session_id = next((s.get('_id') or s.get('id') for s in sessions if s.get('_id') or s.get('id')), None)
        if not session_id:
            raise SourceError('в Efir нет сессии слежения')
        response = http.get('%s/tracking/%s/units' % (self.api, session_id), headers=headers, timeout=TIMEOUT)
        if response.status_code != 200:
            raise SourceError('Efir: машины не открылись (HTTP %s)' % response.status_code)
        units = []
        for item in _list(_json(response, 'Efir')):
            position = item.get('position') or {}
            millis = position.get('t')
            units.append({'site': SITE_EFIR, 'name': item.get('name'), 'plates': plates.find_in_name(item.get('name')),
                          't': (float(millis) / 1000.0) if millis else None,
                          'lat': position.get('lt'), 'lon': position.get('ln'), 'address': None})
        return units
