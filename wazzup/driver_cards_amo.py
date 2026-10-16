"""Карточка водителя → сделка amoCRM: какая сделка у чата и как в неё писать.

Что пишем. Те же поля, что верификаторы заполняли в сделке руками, пока работали
в amoCRM (до перехода на «Чаты ОП» 09–11.10.2026; журнал событий amoCRM за
04–10.10 — правки четырёх верификаторов):
  * «ID водителя» 1176191 — текст, 32 символа (ID профиля в Яндекс Fleet). В
    аккаунте есть второе поле с тем же именем, 1176209: им почти не пользуются
    (17 правок за месяц против сотни за неделю), в него не пишем;
  * «Таксопарк привлечения» 915311 — список;
  * «Город привлечения» 1073351 — список с множественным выбором; из карточки
    приходит один город, и он заменяет прежние значения поля.

Какая сделка у чата. Интеграция Wazzup сама заводит сделку на новый чат (теги
«WZ (<канал>)») и складывает туда переписку; у вернувшегося водителя сообщения
ложатся в его прежнюю сделку, даже закрытую. Значит «сделка этого чата» — не
последняя сделка контакта, а та, к которой amoCRM привязала сообщения ИМЕННО
этого чата:

  1. контакты по номеру — `contacts?query=<10 цифр>`; у номера бывают дубли
     контакта, и переписка часто живёт на дубле;
  2. события сообщений этих контактов (`events` с фильтром по КОНТАКТУ: по
     сделке события чатов не отдаются) в окне времени наших сообщений;
  3. у номера бывает несколько бесед — по одной на канал Wazzup, а у чата беседа
     меняется (беседу закрыли и водитель написал снова). Наши — те, чьи события
     совпадают по времени с нашими сообщениями: id сообщений amoCRM с id Wazzup
     не совпадают, сопоставлять приходится по времени. Замер 16.10.2026 на 60
     живых чатах: совпадение 0–1 с, сделка найдена у всех;
  4. сделка — entity последнего события беседы САМОГО СВЕЖЕГО совпавшего
     сообщения (pick_lead).

Модуль без Flask и без базы: клиент amoCRM приходит снаружи (amocrm.leads.AmoClient).
"""
import os
import threading
import time

import requests

FIELD_DRIVER_ID = int(os.getenv('AMO_FIELD_DRIVER_ID') or 1176191)
FIELD_PARK = int(os.getenv('AMO_FIELD_PARK') or 915311)
FIELD_CITY = int(os.getenv('AMO_FIELD_CITY') or 1073351)

CHAT_EVENT_TYPES = ('incoming_chat_message', 'outgoing_chat_message')
# Событие amoCRM и наше сообщение одной беседы расходятся на 0–1 с; запас — на
# задержку доставки Wazzup → amoCRM.
MATCH_TOLERANCE_SECONDS = 120
# Сколько последних сообщений чата сверять и насколько далеко в прошлое: старая
# переписка ничего не добавляет к выбору беседы, а окно событий раздувает.
ANCHOR_LIMIT = 10
ANCHOR_MAX_AGE_SECONDS = 7 * 24 * 3600
# События запрашиваются пачками контактов: больше десяти id amoCRM не принимает.
CONTACT_BATCH = 10
MAX_CONTACTS = 30
EVENT_PAGE = 100
EVENT_PAGES = 3
REQUEST_TIMEOUT = 20
# Не чаще запроса в REQUEST_INTERVAL: у amoCRM ~7 запросов/с на весь аккаунт, а под
# тем же логином ходят и другие роботы портала. Та же настройка, что у выгрузки
# лидов (amocrm/leads.py).
REQUEST_INTERVAL = float(os.getenv('AMO_REQUEST_PAUSE') or 0.2)
_pace_lock = threading.Lock()
_last_request = [0.0]


def _pace():
    with _pace_lock:
        wait = _last_request[0] + REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()


class AmoTransientError(Exception):
    """amoCRM недоступна или ответила временной ошибкой — повторим позже."""


class AmoAuthError(AmoTransientError):
    """Токен не принят: клиента выбросить и войти заново НОВЫМ клиентом.

    Повторный вход в той же сессии amoCRM получает рабочий стол вместо формы
    входа («csrf_token не найден»), поэтому перелогин — только новым клиентом."""


class AmoPermanentError(Exception):
    """amoCRM отказала по существу (поле, значение, сделка) — повтор не поможет."""


class NoDealError(Exception):
    """У чата не нашлось сделки. Сообщение — для человека."""


def _request(client, method, path, *, params=None, json_body=None):
    """Запрос к /api/v4 через сессию клиента: заголовок авторизации держит он.

    `client._request` повторяет обрыв уже установленного соединения — для чтения
    и для PATCH полей сделки это безопасно (повтор пишет те же значения)."""
    url = client.base + path
    _pace()
    try:
        response = client._request(method, url, params=params, json=json_body,
                                   timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise AmoTransientError('amoCRM недоступна (%s)' % type(exc).__name__)
    status = int(response.status_code)
    if status == 204:
        return None
    if status == 401:
        raise AmoAuthError('amoCRM не приняла вход')
    # 403 — так amoCRM временно блокирует за частоту запросов: повторить позже,
    # в пределах суток повторов, а не сдаться сразу.
    if status in (403, 429) or status >= 500:
        raise AmoTransientError('amoCRM ответила %s' % status)
    if status >= 400:
        detail = ''
        try:
            body = response.json()
            detail = str(body.get('title') or body.get('detail') or '')[:200] if isinstance(body, dict) else ''
            problems = (body.get('validation-errors') or []) if isinstance(body, dict) else []
            if problems:
                detail = (detail + ': ' + str(problems)[:300]).strip(': ')
        except ValueError:
            pass
        raise AmoPermanentError('amoCRM отклонила запрос (%s)%s' % (status, ': ' + detail if detail else ''))
    try:
        return response.json()
    except ValueError:
        raise AmoTransientError('amoCRM ответила не JSON')


def _embedded(data, key):
    return ((data or {}).get('_embedded') or {}).get(key) or [] if isinstance(data, dict) else []


# ── Справочники ─────────────────────────────────────────────────────────────

def _enum_values(field):
    values = []
    for item in field.get('enums') or []:
        name = str(item.get('value') or '').strip()
        if item.get('id') and name:
            values.append((item.get('sort') or 0, name, int(item['id'])))
    # Порядок — как в карточке сделки amoCRM: к нему верификаторы привыкли.
    values.sort(key=lambda value: (value[0], value[1]))
    return [{'id': enum_id, 'name': name} for _, name, enum_id in values]


def fetch_options(client):
    """Списки «Таксопарк привлечения» и «Город привлечения» из полей сделки."""
    fields = []
    for page in range(1, 6):
        data = _request(client, 'GET', '/api/v4/leads/custom_fields',
                        params={'limit': 250, 'page': page})
        items = _embedded(data, 'custom_fields')
        fields.extend(items)
        if len(items) < 250:
            break
    by_id = {field.get('id'): field for field in fields}
    missing = [field_id for field_id in (FIELD_DRIVER_ID, FIELD_PARK, FIELD_CITY) if field_id not in by_id]
    if missing:
        raise AmoPermanentError('В amoCRM нет полей сделки %s' % ', '.join(map(str, missing)))
    parks, cities = _enum_values(by_id[FIELD_PARK]), _enum_values(by_id[FIELD_CITY])
    if not parks or not cities:
        raise AmoPermanentError('В amoCRM пустой список таксопарков или городов')
    return {'parks': parks, 'cities': cities}


# ── Сделка чата ─────────────────────────────────────────────────────────────

def phone_key(chat_id):
    """Хвост номера для поиска контакта: 10 цифр — так находит и +7, и 8."""
    digits = ''.join(ch for ch in str(chat_id or '') if ch.isdigit())
    return digits[-10:] if len(digits) >= 7 else ''


def _has_phone(contact, key):
    for field in contact.get('custom_fields_values') or []:
        if field.get('field_code') != 'PHONE':
            continue
        for value in field.get('values') or []:
            digits = ''.join(ch for ch in str(value.get('value') or '') if ch.isdigit())
            if digits.endswith(key):
                return True
    return False


def find_contacts(client, chat_id):
    """id контактов с этим номером. `query` ищет по всем полям — берём только
    тех, у кого совпал именно телефон."""
    key = phone_key(chat_id)
    if not key:
        raise NoDealError('Номер чата не похож на телефон — сделку в amoCRM не найти')
    data = _request(client, 'GET', '/api/v4/contacts', params={'query': key, 'limit': 50})
    ids = [int(contact['id']) for contact in _embedded(data, 'contacts')
           if contact.get('id') and _has_phone(contact, key)]
    return ids[:MAX_CONTACTS]


def usable_anchors(anchors):
    """Последние времена сообщений чата (секунды UNIX), без давних."""
    values = sorted({int(value) for value in anchors or () if value}, reverse=True)
    if not values:
        return []
    newest = values[0]
    return [value for value in values[:ANCHOR_LIMIT] if newest - value <= ANCHOR_MAX_AGE_SECONDS]


def chat_events(client, contact_ids, anchors):
    """События сообщений контактов в окне наших сообщений."""
    start = min(anchors) - MATCH_TOLERANCE_SECONDS
    end = max(anchors) + MATCH_TOLERANCE_SECONDS
    events = []
    for offset in range(0, len(contact_ids), CONTACT_BATCH):
        params = {'filter[entity]': 'contact',
                  'filter[entity_id][]': contact_ids[offset:offset + CONTACT_BATCH],
                  'filter[type]': ','.join(CHAT_EVENT_TYPES),
                  'filter[created_at][from]': int(start), 'filter[created_at][to]': int(end),
                  'limit': EVENT_PAGE}
        for page in range(1, EVENT_PAGES + 1):
            items = _embedded(_request(client, 'GET', '/api/v4/events', params=dict(params, page=page)),
                              'events')
            events.extend(items)
            if len(items) < EVENT_PAGE:
                break
    return events


def _talk_of(event):
    values = event.get('value_after') or []
    message = (values[0] or {}).get('message') if values and isinstance(values[0], dict) else None
    return (message or {}).get('talk_id')


def assign(talks, anchors, tolerance=MATCH_TOLERANCE_SECONDS):
    """Каждому сообщению чата, от свежих к старым, — ближайшее свободное событие
    любой беседы в допуске. Один к одному: иначе одно чужое событие в ту же
    секунду «совпало» бы со всей пачкой наших сообщений.

    [(сообщение, беседа, беседы с тем же наименьшим расхождением)] — свежие первыми."""
    pool = sorted(((event['created_at'], str(event.get('id') or ''), talk)
                   for talk, items in talks.items() for event in items), key=lambda entry: entry[:2])
    used = [False] * len(pool)
    assigned = []
    for anchor in sorted(anchors, reverse=True):
        best_gap, best = None, []
        for index, (at, _event_id, _talk) in enumerate(pool):
            gap = abs(at - anchor)
            if used[index] or gap > tolerance:
                continue
            if best_gap is None or gap < best_gap:
                best_gap, best = gap, [index]
            elif gap == best_gap:
                best.append(index)
        if best:
            used[best[0]] = True
            assigned.append((anchor, pool[best[0]][2], {pool[index][2] for index in best}))
    return assigned


def pick_lead(events, anchors, tolerance=MATCH_TOLERANCE_SECONDS):
    """Сделка беседы, к которой amoCRM привязала свежие сообщения чата.

    Наши беседы — совпавшие хотя бы с двумя сообщениями (или с одним, если
    других нет): так отсекается случайная секунда беседы другого канала того же
    номера. Беседа и сделка у чата меняются — беседу закрыли и водитель
    написал снова, сделку закрыли и завели новую, — поэтому решает беседа
    САМОГО СВЕЖЕГО совпавшего сообщения, а не та, где совпадений больше. Две
    наши беседы разных сделок в одну и ту же секунду — номер пишет в два канала
    разом: угадывать сделку тут нельзя."""
    talks = {}
    for event in events:
        at = event.get('created_at')
        talk = _talk_of(event)
        if event.get('type') in CHAT_EVENT_TYPES and talk is not None and isinstance(at, int):
            talks.setdefault(talk, []).append(event)
    assigned = assign(talks, anchors, tolerance)
    if not assigned:
        raise NoDealError('В amoCRM не нашли переписку этого чата')
    hits = {}
    for _anchor, talk, _tied in assigned:
        hits[talk] = hits.get(talk, 0) + 1
    ours = {talk for talk, count in hits.items() if count >= 2} or set(hits)

    def lead_of(talk):
        leads = [item for item in talks[talk] if item.get('entity_type') == 'lead' and item.get('entity_id')]
        newest = max(leads, key=lambda item: (item['created_at'], str(item.get('id') or '')), default=None)
        return int(newest['entity_id']) if newest else None

    _anchor, talk, tied = next(entry for entry in assigned if entry[1] in ours)
    lead = lead_of(talk)
    if any(lead_of(rival) != lead for rival in (tied & ours) - {talk}):
        raise NoDealError('Номер пишет в несколько каналов одновременно — сделку не выбрать')
    if lead is None:
        raise NoDealError('Переписка в amoCRM не привязана к сделке')
    return lead


def resolve_lead(client, chat_id, anchors):
    anchors = usable_anchors(anchors)
    if not anchors:
        raise NoDealError('В чате нет сообщений, по которым найти сделку')
    contacts = find_contacts(client, chat_id)
    if not contacts:
        raise NoDealError('В amoCRM нет контакта с этим номером')
    return pick_lead(chat_events(client, contacts, anchors), anchors)


# ── Запись ──────────────────────────────────────────────────────────────────

def lead_patch(card):
    """Тело PATCH сделки: только заполненные поля карточки.

    Пустое поле карточки — «ещё не знаем», а не «стереть»: очистить заполненное
    поле карточка не даёт (wazzup/driver_cards.py), а чужое значение в сделке,
    поставленное, например, менеджером «Основы», затирать нечем и незачем."""
    values = []
    if card.get('driver_id'):
        values.append({'field_id': FIELD_DRIVER_ID, 'values': [{'value': card['driver_id']}]})
    if card.get('park_enum_id'):
        values.append({'field_id': FIELD_PARK, 'values': [{'enum_id': int(card['park_enum_id'])}]})
    if card.get('city_enum_id'):
        values.append({'field_id': FIELD_CITY, 'values': [{'enum_id': int(card['city_enum_id'])}]})
    return {'custom_fields_values': values}


def write_lead(client, lead_id, card):
    data = _request(client, 'PATCH', '/api/v4/leads/%d' % int(lead_id), json_body=lead_patch(card))
    if not isinstance(data, dict) or str(data.get('id')) != str(int(lead_id)):
        raise AmoTransientError('amoCRM не подтвердила запись в сделку')
    return int(lead_id)


def lead_url(lead_id, domain=None):
    if not lead_id:
        return None
    domain = (domain or os.getenv('AMO_DOMAIN') or 'igroupkz.amocrm.ru').strip()
    return 'https://%s/leads/detail/%d' % (domain, int(lead_id))
