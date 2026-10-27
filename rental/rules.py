# -*- coding: utf-8 -*-
"""Правила ночного отчёта по арендным машинам (#398). Чистая логика: ни базы, ни сети.

ТЗ #398 (ООЗ, 23.10.2026) и ответы владельца 27.10.2026:

* Отчёт два раза за ночную смену. В 01:00 — все столбцы, включая «Статус аренды»; в 07:00 —
  все, кроме него (ТЗ п. 3).
* «Статус аренды» из Yume: «В аренде», если у машины там статус «В аренде»; для всех
  остальных — «Нет» (ТЗ п. 2.3).
* «Статус GPS»: «Онлайн» — машина отправляет трекинг; «Офлайн» — не отправляет больше
  4 часов (ТЗ п. 2.4). Машина парка Yume, которой нет ни в одной GPS-системе, — «Нет GPS»
  отдельной строкой (владелец 27.10: «по машинам без GPS дай список госномеров»).
* Машина в двух GPS-системах — одна строка по самому свежему сигналу (владелец 27.10).
* В отчёт идут все машины трёх GPS-систем и машины парка Yume без трекера: какие брать,
  владелец оставил на моё решение («делай как посчитаешь нужным», 27.10).
* «Состояние» (ТЗ п. 2.5 и как определяет ночная смена, владелец 27.10): Утеген Батыра 15А,
  15 и ул. Отарская — «На базе»; Утеген Батыра 15/1 и «Автомастерская, 17Б» — «На ремонте»;
  «На продаже» — только вручную. По адресу состояние подставляется, ночная смена его меняет.
  Незнакомый адрес уточняют в группе — такую машину парка, если она не в аренде, отчёт
  помечает «Уточнить».
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo('Asia/Almaty')

KIND_NIGHT = 'night'      # 01:00 — все столбцы
KIND_MORNING = 'morning'  # 07:00 — без «Статуса аренды»
KINDS = (KIND_NIGHT, KIND_MORNING)
KIND_HOUR = {KIND_NIGHT: 1, KIND_MORNING: 7}
KIND_LABELS = {KIND_NIGHT: '01:00', KIND_MORNING: '07:00'}

# Сколько после своего часа отчёт можно собрать кнопкой, если планировщик его пропустил
# (перезапуск сервера в 01:00). Дольше — это уже не отчёт за 01:00, а снимок посреди дня.
CATCH_UP_HOURS = 6

OFFLINE_AFTER_SECONDS = 4 * 3600

GPS_ONLINE = 'online'
GPS_OFFLINE = 'offline'
GPS_NONE = 'none'
GPS_LABELS = {GPS_ONLINE: 'Онлайн', GPS_OFFLINE: 'Офлайн', GPS_NONE: 'Нет GPS'}

RENT_IN = 'in_rent'
RENT_NO = 'no'
RENT_LABELS = {RENT_IN: 'В аренде', RENT_NO: 'Нет'}

STATE_BASE = 'base'
STATE_REPAIR = 'repair'
STATE_SALE = 'sale'
STATES = (STATE_BASE, STATE_REPAIR, STATE_SALE)
STATE_LABELS = {STATE_BASE: 'На базе', STATE_REPAIR: 'На ремонте', STATE_SALE: 'На продаже'}

STATE_BY_ADDRESS = 'address'
STATE_BY_HAND = 'manual'

# Статус аренды в Yume (ORDER_REQUEST_STATUS / статус инвентаря): 2 — «В аренде».
YUME_IN_RENT = 2


# ─── Время ──────────────────────────────────────────────────────────────────────

def almaty_from_ts(ts):
    """Unix-время → наивное время Алматы (так время хранится в таблицах раздела)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), ALMATY).replace(tzinfo=None)


def slot_at(kind, day):
    """Плановый момент отчёта: день + час вида (01:00 или 07:00), наивное время Алматы."""
    return datetime(day.year, day.month, day.day, KIND_HOUR[kind])


def current_slot(now_local):
    """Какой отчёт сейчас «текущий»: (вид, плановый момент).

    С 07:00 — утренний сегодняшний, с 01:00 — ночной сегодняшний, до 01:00 — утренний
    вчерашний (ночь ещё не началась).
    """
    today = now_local.date()
    if now_local.hour >= KIND_HOUR[KIND_MORNING]:
        return KIND_MORNING, slot_at(KIND_MORNING, today)
    if now_local.hour >= KIND_HOUR[KIND_NIGHT]:
        return KIND_NIGHT, slot_at(KIND_NIGHT, today)
    return KIND_MORNING, slot_at(KIND_MORNING, today - timedelta(days=1))


def next_slot(now_local):
    """Следующий плановый отчёт после этого момента: (вид, плановый момент)."""
    today = now_local.date()
    for kind in (KIND_NIGHT, KIND_MORNING):
        moment = slot_at(kind, today)
        if moment > now_local:
            return kind, moment
    return KIND_NIGHT, slot_at(KIND_NIGHT, today + timedelta(days=1))


def can_catch_up(slot, now_local):
    """Можно ли ещё собрать пропущенный отчёт этого слота кнопкой."""
    return slot <= now_local < slot + timedelta(hours=CATCH_UP_HOURS)


# ─── Статусы ────────────────────────────────────────────────────────────────────

def gps_status(signal_ts, now_ts):
    """«Онлайн» / «Офлайн» по ТЗ п. 2.4. Объект без единой точки — тоже «Офлайн»."""
    if signal_ts is None:
        return GPS_OFFLINE
    return GPS_ONLINE if now_ts - float(signal_ts) <= OFFLINE_AFTER_SECONDS else GPS_OFFLINE


def rent_status(yume_car):
    """«В аренде» — только статус Yume «В аренде»; всё остальное и машина не из Yume — «Нет»."""
    if yume_car and yume_car.get('status') == YUME_IN_RENT:
        return RENT_IN
    return RENT_NO


# ─── Адрес → состояние ─────────────────────────────────────────────────────────

# Улица базы. Геокодеры пишут её по-разному: ГЛОНАСС «15/1 улица Утеген Батыра, Алматы»,
# Wialon «Утеген Батыра ул., 15/1, Алматы, Әуезов ауданы», ночная смена «ул. Утеген Батыра, 15/1».
_UTEGEN = r'[уө]теген\w*\s+батыр\w*'
_STREET_WORD = r'(?:улица|ул\.?|көшесі|к[-\s]?сі)'
# Дом: «15», «15А», «15 а», «15/1». Буква — только отдельная: «15 Алматы» не дом «15а».
_HOUSE = r'(\d+(?:\s?[а-яa-z](?![а-яa-z]))?(?:\s?/\s?\d+)?)'
_HOUSE_BEFORE = re.compile(_HOUSE + r'\s*,?\s*(?:' + _STREET_WORD + r'\s+)?' + _UTEGEN)
_HOUSE_AFTER = re.compile(_UTEGEN + r'(?:\s+' + _STREET_WORD + r')?\s*,?\s*(?:' + _STREET_WORD + r'\s*)?' + _HOUSE)
# ул. Отарская: ГЛОНАСС «улица Отарская», Wialon «Отар Көшесі».
_OTAR = re.compile(r'\bотар(?:ская|ской|\s+к[өо]шес|\b)')
_WORKSHOP = re.compile(r'автомастерск')

_LATIN_TO_CYRILLIC = str.maketrans('abcehkmoptx', 'авсенкмортх')

BASE_HOUSES = ('15а', '15')
REPAIR_HOUSES = ('15/1',)


def _house(text):
    return re.sub(r'\s+', '', text).translate(_LATIN_TO_CYRILLIC)


def state_by_address(address):
    """«На базе» / «На ремонте» по адресу GPS; None — адрес не базы и не ремонта."""
    text = re.sub(r'\s+', ' ', str(address or '').lower().replace('ё', 'е')).strip()
    if not text:
        return None
    if _WORKSHOP.search(text):
        return STATE_REPAIR
    # Дом ищется и перед улицей, и после: перед ней в строке бывает и индекс (050000).
    houses = {_house(match.group(1)) for pattern in (_HOUSE_BEFORE, _HOUSE_AFTER)
              for match in pattern.finditer(text)}
    if houses & set(REPAIR_HOUSES):
        return STATE_REPAIR
    if houses & set(BASE_HOUSES):
        return STATE_BASE
    if _OTAR.search(text):
        return STATE_BASE
    return None


# ─── Строки отчёта ──────────────────────────────────────────────────────────────

def freshest(units):
    """Объект с самым свежим сигналом; объекты без точки — последними."""
    return max(units, key=lambda unit: unit.get('t') if unit.get('t') is not None else float('-inf'))


def needs_check(row):
    """«Уточнить в группе»: машина парка Yume не в аренде, а адрес не базы и не ремонта.

    Только для машин парка: про чужие машины GPS (GoPartners, Taxi Technologies) мы не знаем,
    где им положено стоять, и пометка на каждой работающей на линии была бы шумом.
    """
    return bool(row.get('in_fleet')) and row.get('rent_status') != RENT_IN and not row.get('state')


def build_rows(units, yume_cars, now_ts, *, yume_ok=True):
    """Строки отчёта из объектов GPS и парка Yume.

    units — объекты GPS: {'site', 'plates', 't', 'lat', 'lon', 'address'}.
    yume_cars — {госномер: {'status', 'deleted', 'model'}}; yume_ok=False — Yume не
    ответил: статус аренды неизвестен (None), машины без GPS из Yume не добавить.
    """
    by_plate = {}
    for unit in units:
        for plate in unit.get('plates') or ():
            by_plate.setdefault(plate, []).append(unit)

    rows = []
    for plate, items in by_plate.items():
        best = freshest(items)
        car = yume_cars.get(plate) if yume_ok else None
        in_fleet = bool(car and not car.get('deleted'))
        state = state_by_address(best.get('address'))
        rows.append({
            'plate': plate,
            'gps_site': best.get('site'),
            'gps_sites': sorted({item.get('site') for item in items if item.get('site')}),
            'gps_status': gps_status(best.get('t'), now_ts),
            'signal_ts': best.get('t'),
            'address': (best.get('address') or '').strip() or None,
            'lat': best.get('lat'),
            'lon': best.get('lon'),
            'rent_status': rent_status(car) if yume_ok else None,
            'in_fleet': in_fleet,
            'model': (car or {}).get('model') or None,
            'state': state,
            'state_source': STATE_BY_ADDRESS if state else None,
        })

    if yume_ok:
        for plate, car in yume_cars.items():
            if plate in by_plate or car.get('deleted'):
                continue
            rows.append({
                'plate': plate, 'gps_site': None, 'gps_sites': [], 'gps_status': GPS_NONE,
                'signal_ts': None, 'address': None, 'lat': None, 'lon': None,
                'rent_status': rent_status(car), 'in_fleet': True, 'model': car.get('model') or None,
                'state': None, 'state_source': None,
            })

    rows.sort(key=sort_key)
    return rows


_GPS_ORDER = {GPS_OFFLINE: 0, GPS_NONE: 1, GPS_ONLINE: 2}


def sort_key(row):
    """Сначала то, чем ночной смене заниматься: офлайн, без GPS, «уточнить»; дальше по номеру."""
    return (_GPS_ORDER.get(row.get('gps_status'), 3), 0 if needs_check(row) else 1, row.get('plate') or '')
