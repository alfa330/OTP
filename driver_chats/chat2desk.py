"""Разговор с API Chat2Desk для раздела «Чаты водителей».

Почему свой модуль, а не вызовы `_c2d_*` из bot_schedule2. Тамошний
`_c2d_fetch_request_messages` входит в переписку от ЗАЯВКИ (`/v1/requests/{id}`
-> dialog_id -> messages) — это правильный вход для оценки конкретного
обращения. Здесь вход другой: телефон водителя и окно в двое суток, а заявки
внутри окна как раз и надо найти. Обратный импорт из монолита был бы циклом, а
копия его обхода — второй реализацией одного и того же.

ЧТО ПРОВЕРЕНО ЖИВЬЁМ 03.09.2026 (не по документации):

    GET /v1/clients?phone=77XXXXXXXXX      -> HTTP 200, meta.total = 1, точное
                                              совпадение; параметры search/query
                                              молча игнорируются
    GET /v1/messages?client_id=&start_date=&finish_date=&order=desc&limit=200
                                           -> HTTP 200, отдаёт и сегодняшние
                                              сообщения; у каждого есть
                                              request_id и dialog_id
    POST /v1/messages {client_id, text, type: 'comment', open_dialog: false}
                                           -> HTTP 200, {message_id, dialog_id,
                                              request_id, operator_id}; клиенту
                                              сообщение НЕ уходит

ГРАБЛИ ВЕНДОРА, на которые тут уже не наступают:

* `offset`/`page` на /v1/messages ИГНОРИРУЮТСЯ — каждая «страница» отдаёт те же
  первые 200 сообщений. Листать можно только `start_id`, но его нельзя
  сочетать с датами («use date filter and start_id separately»). Поэтому окно
  берём датами, `order=desc` и limit=200 — то есть 200 САМЫХ СВЕЖИХ сообщений
  окна. Если их больше, честно говорим об этом в ответе (`truncated`), а не
  делаем вид, что показали всё.
* Формат даты — строго `dd-mm-yyyy`, иначе HTTP 400. Граница считается по
  времени Алматы.
* `client_id` на /v1/dialogs игнорируется (возвращает все 900 тыс. диалогов
  компании) — списка диалогов клиента одним вызовом не получить, и он не нужен:
  сообщения сами несут dialog_id.
* У `POST /v1/messages` `open_dialog` по умолчанию **true**, и без явного false
  заметка «раскроет» закрытый диалог и испортит чат-менеджеру метрики
  (количество чатов, время ответа). Передаём false всегда.
* Без `operator_id` комментарий приписывается оператору текущего диалога — то
  есть чат-менеджеру, который его вёл. Учёток Chat2Desk у линейных операторов
  нет, поэтому автора пишем В ТЕКСТ заметки (см. build_handoff_text).
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

# Смещение Алматы от UTC. Render живёт в UTC, и «сегодня» у него до 06:00 по
# Алматы ещё вчерашнее. Считаем сдвигом, а не ZoneInfo: у Казахстана с 01.03.2024
# одна зона без перевода часов, а tzdata на контейнере может и отсутствовать.
_ALMATY_OFFSET = timedelta(hours=5)

# Потолок вендора на одну выборку сообщений. Больше он не отдаёт.
MESSAGES_PAGE_LIMIT = 200

# Окно раздела из постановки: «история чатов за последние 2 дня».
WINDOW_DAYS = 2

# Предел длины внутреннего комментария. Удалить отправленную заметку через API
# НЕЛЬЗЯ (метода DELETE у messages нет), поэтому ограничение стоит на входе.
MAX_COMMENT_LENGTH = 500

_STORAGE_BASE_URL = (os.getenv('CHAT2DESK_STORAGE_BASE_URL')
                     or 'https://storage-02.chat2desk.kz').strip().rstrip('/')

# Справочник операторов вендора кешируется в процессе: он нужен, чтобы подписать
# исходящие сообщения именем чат-менеджера, меняется редко, а стоит вызова API.
_OPERATORS_CACHE = {'names': None, 'at': None}
_CHANNELS_CACHE = {'names': None, 'at': None}
_OPERATORS_TTL = timedelta(hours=6)


class Chat2DeskError(RuntimeError):
    """Ошибка вендора, которую можно показать человеку."""


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


# ─────────────────────────────────────────────────────────────────────────────
# Телефон
# ─────────────────────────────────────────────────────────────────────────────

def normalize_phone(raw):
    """Любая запись казахстанского номера -> 11 цифр вида 77XXXXXXXXX.

    В c2d_requests телефон лежит как пришёл от вендора: 11 знаков у 84 468 строк
    из 85 222, но встречаются 12, 14, 15 и даже 31. Оператор же вводит номер как
    привык — «+7 707 …», «8 (707) …», «707…». Без приведения поиск молча ничего
    не находит, и это выглядит как «раздел не работает», а не «формат не
    совпал».

    Возвращает None, если из ввода не собирается казахстанский номер: пусть
    лучше раздел скажет «непохоже на номер», чем уйдёт в вендора с мусором.
    """
    digits = re.sub(r'\D', '', str(raw or ''))
    if not digits:
        return None
    # 8 707 123 45 67 -> 7 707 123 45 67
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    # 707 123 45 67 -> 7 707 …
    if len(digits) == 10 and digits[0] == '7':
        digits = '7' + digits
    if len(digits) != 11 or not digits.startswith('7'):
        return None
    return digits


def phone_variants(phone):
    """Как этот номер может быть записан в c2d_requests.

    Вендор кладёт телефон без приведения, поэтому в базе рядом с 77071234567
    может лежать 87071234567 или +77071234567. Ищем по всем правдоподобным
    записям сразу, а не заводим колонку-дубль ради одного экрана.

    Нормализуем ВНУТРИ, а не полагаемся на вызывающего. Иначе функция, которой
    дали «8707…», честно вернёт варианты от «8707…» — и поиск по своей базе
    промахнётся мимо номера, который в ней лежит. Промах при этом выглядит как
    «водителя нет», а не как ошибка формата, и ищут его потом не там.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return []
    tail = normalized[1:]      # 7071234567
    return [normalized, '8' + tail, '+' + normalized, tail]


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _token():
    return (os.getenv('CHAT2DESK_API_TOKEN') or '').strip()


def _authorization():
    """Схема авторизации ровно та же, что у синка метрик в монолите."""
    token = _token()
    if not token:
        return ''
    scheme = (os.getenv('CHAT2DESK_AUTH_SCHEME') or 'raw').strip().lower()
    if not scheme or scheme in {'raw', 'none', 'token'}:
        return token
    if scheme == 'bearer':
        return f"Bearer {token}"
    return f"{scheme} {token}".strip()


def _base_url():
    return (os.getenv('CHAT2DESK_API_BASE_URL')
            or 'https://api-02.chat2desk.kz').strip().rstrip('/')


def _request(method, path, *, params=None, json_body=None, timeout=30):
    authorization = _authorization()
    if not authorization:
        raise Chat2DeskError('CHAT2DESK_API_TOKEN не задан')
    response = requests.request(
        method, f"{_base_url()}{path}",
        headers={'Authorization': authorization, 'Accept': 'application/json'},
        params=params or {}, json=json_body, timeout=timeout)
    if response.status_code == 429:
        raise Chat2DeskError('Chat2Desk: превышен лимит запросов API — попробуйте позже')
    if response.status_code >= 400:
        try:
            payload = response.json() or {}
            detail = str(payload.get('message') or payload.get('errors')
                         or payload.get('error') or '')[:200]
        except Exception:  # noqa: BLE001
            detail = str(response.text or '')[:200]
        raise Chat2DeskError(f"Chat2Desk API HTTP {response.status_code}: {detail}")
    return response.json() or {}


def api_info():
    """Остаток месячной квоты. Ручка — /v1/companies/api_info (не /v1/api_info: тот 404)."""
    payload = _request('GET', '/v1/companies/api_info')
    return ((payload.get('data') or {}).get('api_calls') or {})


# ─────────────────────────────────────────────────────────────────────────────
# Клиент по номеру
# ─────────────────────────────────────────────────────────────────────────────

def clean_client_name(value):
    """Имя водителя или None.

    Вендор отдаёт «unknown» буквальной строкой, когда имени у клиента нет, — и
    в шапке чата это выглядело как имя водителя. Пустое имя честнее: раздел
    подставит вместо него телефон.
    """
    name = str(value or '').strip()
    if not name or name.lower() in {'unknown', 'no name', 'без имени'}:
        return None
    return name


def find_client(phone):
    """{'id', 'name', 'phone'} или None. Один вызов API.

    Фильтр `phone` — единственный работающий: `search` и `query` вендор молча
    игнорирует и отдаёт всех 663 тыс. клиентов, что однажды уже приняли за
    «поиск не нашёл».
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    payload = _request('GET', '/v1/clients', params={'phone': normalized, 'limit': 2})
    rows = payload.get('data') or []
    if not rows:
        return None
    row = rows[0] or {}
    return {
        'id': row.get('id'),
        'name': clean_client_name(row.get('name')),
        'phone': normalized,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Сообщения за окно
# ─────────────────────────────────────────────────────────────────────────────

def window_bounds(days=WINDOW_DAYS, today=None):
    """(дата_с, дата_по) окна раздела по времени Алматы.

    days=2 -> вчера и сегодня. Именно так постановщик читает «последние 2 дня»:
    водитель звонит про то, что писал вчера или сегодня утром.
    """
    end = today or today_almaty()
    start = end - timedelta(days=max(1, int(days)) - 1)
    return start, end


def _api_date(value):
    """Вендор принимает ТОЛЬКО dd-mm-yyyy, иначе HTTP 400."""
    return value.strftime('%d-%m-%Y')


def fetch_window_messages(client_id, date_from, date_to):
    """Сообщения клиента за окно. Один вызов API.

    finish_date берём на сутки позже конца окна: граница у вендора считается по
    Алматы и ведёт себя как «строго до», из-за чего сегодняшние сообщения в
    выборку не попадали.

    Возвращает (сообщения, всего_по_версии_вендора). Если всего больше, чем
    отдал вендор, вызывающий обязан сказать об этом человеку: листать окно
    нельзя — `start_id` с датами не сочетается.
    """
    payload = _request('GET', '/v1/messages', params={
        'client_id': int(client_id),
        'start_date': _api_date(date_from),
        'finish_date': _api_date(date_to + timedelta(days=1)),
        'order': 'desc',
        'limit': MESSAGES_PAGE_LIMIT,
    })
    rows = payload.get('data') or []
    total = int(((payload.get('meta') or {}).get('total')) or len(rows))
    return rows, total


def channel_names():
    """id канала -> название таксопарка. Кешируется в процессе на 6 часов.

    ЗАПАСНОЙ путь: основной справочник берётся бесплатно из своей базы
    (`c2d_requests`, 14 парков за 45 дней — покрывает весь живой трафик). Сюда
    доходим только за парком, которого в нашей базе ещё нет, — то есть за
    новым, подключённым сегодня.

    `offset` вендор игнорирует и здесь: отдаёт 15 каналов из 23 и на второй
    странице ноль. Долистать нечем, поэтому недостающие имена и остаются за
    локальным справочником, а не наоборот.
    """
    cached_at = _CHANNELS_CACHE.get('at')
    if _CHANNELS_CACHE.get('names') is not None and cached_at:
        if datetime.utcnow() - cached_at < _OPERATORS_TTL:
            return _CHANNELS_CACHE['names']
    names = {}
    try:
        payload = _request('GET', '/v1/channels', params={'limit': MESSAGES_PAGE_LIMIT})
        for row in (payload.get('data') or []):
            if not isinstance(row, dict) or row.get('id') is None:
                continue
            name = str(row.get('name') or '').strip()
            if name:
                names[int(row['id'])] = name
    except Chat2DeskError:
        logging.warning('driver_chats: справочник каналов Chat2Desk недоступен',
                        exc_info=True)
        return _CHANNELS_CACHE.get('names') or {}
    _CHANNELS_CACHE['names'] = names
    _CHANNELS_CACHE['at'] = datetime.utcnow()
    return names


def operator_names():
    """id оператора Chat2Desk -> имя. Кешируется в процессе на 6 часов.

    Нужен, чтобы подписать исходящие сообщения именем чат-менеджера. Без него
    ChatThread подписывает ВСЕ ответы одним именем из шапки снапшота, а за двое
    суток водителю могли отвечать трое разных людей — и оператор унёс бы на
    скриншоте чужое имя.
    """
    cached_at = _OPERATORS_CACHE.get('at')
    if _OPERATORS_CACHE.get('names') is not None and cached_at:
        if datetime.utcnow() - cached_at < _OPERATORS_TTL:
            return _OPERATORS_CACHE['names']
    names = {}
    try:
        payload = _request('GET', '/v1/operators', params={'limit': MESSAGES_PAGE_LIMIT})
        for row in (payload.get('data') or []):
            if not isinstance(row, dict) or row.get('id') is None:
                continue
            name = ' '.join(str(part).strip() for part in
                            (row.get('first_name'), row.get('last_name')) if part).strip()
            if name:
                names[int(row['id'])] = name
    except Chat2DeskError:
        # Справочник — украшение, а не условие работы: без него просто не будет
        # подписи автора. Ронять из-за этого просмотр чата нельзя.
        logging.warning('driver_chats: справочник операторов Chat2Desk недоступен',
                        exc_info=True)
        return _OPERATORS_CACHE.get('names') or {}
    _OPERATORS_CACHE['names'] = names
    _OPERATORS_CACHE['at'] = datetime.utcnow()
    return names


# ─────────────────────────────────────────────────────────────────────────────
# Нормализация под ленту ChatThread
# ─────────────────────────────────────────────────────────────────────────────

def _media_url(value):
    """photo приходит относительным путём, attachments — полным URL."""
    text = str(value or '').strip()
    if not text:
        return None
    if text.startswith('http://') or text.startswith('https://'):
        return text
    return f"{_STORAGE_BASE_URL}/{text.lstrip('/')}"


def _parse_created(value):
    """'2026-09-03T10:45:31 UTC' -> naive Asia/Almaty.

    Вендор отдаёт время в UTC и помечает его словом, а не оффсетом. Сообщение
    без пометки тоже считаем UTC — так же, как это делает синк метрик: другого
    времени вендор не отдавал ни разу, а угадывать зону по строке хуже, чем
    держать одно правило.
    """
    text = str(value or '').strip()
    if not text:
        return None
    text = text.replace(' UTC', '+00:00').replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed + _ALMATY_OFFSET


def normalize_message(msg, names=None):
    """Сообщение вендора -> форма, которую ждёт src/components/c2d_eval/ChatThread.

    Отличие от `_c2d_normalize_message` в монолите одно: здесь есть `author` —
    имя автора у КАЖДОГО исходящего сообщения. Тот нормализатор менять нельзя,
    его форму сторожит tests/test_c2d_eval.py, и она осознанно одна на весь
    «Журнал оценок».
    """
    names = names or {}
    created = _parse_created(msg.get('created'))
    attachments = [
        {'name': att.get('name'), 'link': _media_url(att.get('link'))}
        for att in (msg.get('attachments') or [])
        if isinstance(att, dict) and att.get('link')
    ]
    # Тип сообщения. Вендор регулярно присылает его ПУСТЫМ (`type: null`) —
    # так приходит автоопрос «Оператордың жұмысын қалай бағалайсыз?», который
    # парк шлёт водителю после закрытия чата (проверено живьём 03.09.2026).
    # Пустой тип нельзя оставлять как есть: в ленте у него нет своей ветки, и
    # ChatThread рисует его белым пузырём СЛЕВА — то есть выдаёт вопрос парка за
    # слова водителя. Пока чаты резались по обращению, опрос жил отдельной
    # служебной карточкой и на глаза не попадался; после склейки по парку он
    # оказывается внутри живой ленты, и оператор унёс бы его на скриншоте.
    # Считаем неизвестный тип автоответом: это ближе всего к правде (сообщение
    # шлёт робот парка) и ставит его на нужную сторону ленты.
    kind = str(msg.get('type') or '').strip() or 'autoreply'
    item = {
        'id': msg.get('id'),
        'type': kind,
        'text': msg.get('text') or '',
        'created': created.isoformat() if created else None,
        'photo': _media_url(msg.get('photo')),
        'video': _media_url(msg.get('video')),
        'audio': _media_url(msg.get('audio')),
        'pdf': _media_url(msg.get('pdf')),
        'attachments': attachments,
        'status': msg.get('status'),
        'requestId': msg.get('request_id'),
        'dialogId': msg.get('dialog_id'),
        # Канал = таксопарк, на чей номер написал водитель. Берём из САМОГО
        # сообщения: в ночном срезе заявок канал есть только за вчера, а
        # оператору чаще нужен сегодняшний чат — и парк у него не показывался.
        'channelId': msg.get('channel_id'),
    }
    # Подпись автора — только там, где она осмысленна: у ответа чат-менеджера и
    # у внутренней заметки. У реплики клиента автор — сам водитель.
    if kind in ('to_client', 'comment'):
        operator_id = msg.get('operator_id')
        if operator_id is not None:
            item['author'] = names.get(int(operator_id)) or None
        else:
            item['author'] = None
    return item


def chat_key(channel_id=None, dialog_id=None):
    """Ключ чата. Двойник во фронте — chatKey в DriverChatsView.jsx.

    Порядок обязан совпадать с фронтовым: на этом ключе держатся выбранный чат,
    отметка «передан» и защита от повторной записи в журнал.
    """
    if channel_id:
        return 'c%s' % int(channel_id)
    if dialog_id:
        return 'd%s' % int(dialog_id)
    return 'x0'


def group_chats(messages):
    """Сообщения окна -> список чатов, свежий сверху.

    ЧАТ = ТАКСОПАРК (`channel_id`), а вся история окна лежит внутри него.
    Формулировка владельца 04.09.2026: «один чат один таксопарк, но с историей в
    два последних дня».

    Раньше чат резался по `request_id` (обращению), и один разговор с одним
    парком распадался на несколько карточек. Это не редкость, а норма: за двое
    суток 88,8 % пар (клиент, парк) имеют больше одного обращения, в среднем
    2,7, максимум 25 — на живых данных один водитель давал десять «чатов» при
    одном парке и одном диалоге.

    Почему ключ — канал, а не диалог. Связь у вендора один-к-одному: из 1585 пар
    (клиент, канал) ни одна не держит двух диалогов, и ни один диалог не
    охватывает двух каналов; диалог живёт до 39 суток, то есть окно в двое суток
    он и так не разорвал бы. Диалог точности не добавляет, зато добавляет способ
    разрезать чат, если вендор однажды заведёт второй. Канал же — это ровно то,
    что владелец называет таксопарком, и он есть у каждого сообщения (0 пустых
    на 85 735 строк). Фолбэк на диалог оставлен на случай, которого пока не
    видели.

    `request_id` у склеенного чата больше не адрес, а справка: обращений внутри
    несколько, и все они лежат в `request_ids`.
    """
    buckets = {}
    for msg in messages:
        key = chat_key(msg.get('channelId'), msg.get('dialogId'))
        bucket = buckets.setdefault(key, {
            'key': key,
            'channel_id': msg.get('channelId'),
            'dialog_id': msg.get('dialogId'),
            'messages': [],
        })
        if bucket['dialog_id'] is None:
            bucket['dialog_id'] = msg.get('dialogId')
        if bucket['channel_id'] is None:
            bucket['channel_id'] = msg.get('channelId')
        bucket['messages'].append(msg)

    chats = []
    for bucket in buckets.values():
        items = sorted(bucket['messages'], key=lambda m: (m.get('created') or '', m.get('id') or 0))
        incoming = [m for m in items if m.get('type') == 'from_client']
        outgoing = [m for m in items if m.get('type') == 'to_client']
        # Превью — последняя ЖИВАЯ реплика, а не последняя строка с текстом.
        # Иначе в списке стоит «Chat closed. Reason — chat inactivity timeout»
        # у каждого закрытого чата, и оператор не видит, о чём был разговор.
        # Служебные строки берём только если живых реплик нет вовсе.
        def _last_text(kinds):
            return next((m.get('text') for m in reversed(items)
                         if m.get('type') in kinds and (m.get('text') or '').strip()), '')

        preview = _last_text(('from_client', 'to_client')) or _last_text(
            ('comment', 'autoreply', 'system'))
        # Все обращения окна — по ним обогащаемся метаданными. Свежие сверху,
        # как и сами сообщения.
        request_ids = []
        for m in reversed(items):
            rid = m.get('requestId')
            if rid is not None and rid not in request_ids:
                request_ids.append(rid)
        # «Справочное» обращение — последнее ЖИВОЕ, а не первое попавшееся:
        # самое свежее сообщение окна часто оказывается автоопросом после
        # закрытия чата, и наследовать его номер значило бы подписать живой
        # разговор служебной заявкой.
        live_request = next((m.get('requestId') for m in reversed(items)
                             if m.get('type') in ('from_client', 'to_client')
                             and m.get('requestId') is not None), None)
        chats.append({
            'key': bucket['key'],
            'channel_id': bucket['channel_id'],
            'dialog_id': bucket['dialog_id'],
            'request_id': live_request if live_request is not None else (
                request_ids[0] if request_ids else None),
            'request_ids': request_ids,
            'messages': items,
            'messages_count': len(items),
            'incoming_count': len(incoming),
            'outgoing_count': len(outgoing),
            # Служебный чат — тот, где за двое суток не было ни одной живой
            # реплики: только автоопрос «оцените работу оператора» и системные
            # строки. После склейки таких почти не остаётся (живая реплика в
            # парке за двое суток есть почти всегда), и это правильно: прятать
            # теперь надо служебные СООБЩЕНИЯ внутри ленты, а не карточки.
            #
            # Считаем строго по содержимому. Признак «тип заявки = rating» для
            # этого больше не годится: у склеенного чата обращений несколько, и
            # одно служебное среди них спрятало бы живой разговор целиком.
            'is_service': not incoming and not outgoing,
            'started_at': items[0].get('created') if items else None,
            'last_at': items[-1].get('created') if items else None,
            'preview': (preview or '')[:160],
            'has_media': any(m.get('photo') or m.get('video') or m.get('audio')
                             or m.get('pdf') or m.get('attachments') for m in items),
            'authors': sorted({m['author'] for m in items
                               if m.get('type') == 'to_client' and m.get('author')}),
        })
    chats.sort(key=lambda c: (c.get('last_at') or '', c.get('channel_id') or 0), reverse=True)
    return chats


# ─────────────────────────────────────────────────────────────────────────────
# Внутренний комментарий («Передан»)
# ─────────────────────────────────────────────────────────────────────────────

def build_handoff_text(operator_name, note=''):
    """Текст заметки, которая уйдёт в чат.

    Форма короткая — «Комментарий от оператора {Имя}: {сообщение}» (требование
    владельца 03.09.2026). Длинная преамбула про скриншот занимала в ленте
    Chat2Desk две строки и повторяла то, что и так очевидно из места, где
    заметка появилась.

    Имя оператора идёт В ТЕКСТ, а не в operator_id: учёток Chat2Desk у линейных
    операторов нет, и без operator_id вендор припишет заметку оператору текущего
    диалога — то есть самому чат-менеджеру. Он увидел бы «свой» комментарий,
    которого не писал (проверено живьём 03.09.2026: operator_id проставился
    автоматически).
    """
    who = str(operator_name or '').strip() or 'оператор OTP'
    note = str(note or '').strip() or 'скриншот чата передан'
    return f"Комментарий от оператора {who}: {note}"[:MAX_COMMENT_LENGTH]


def send_internal_comment(client_id, text):
    """Внутренняя заметка в чат клиента. Клиенту она НЕ уходит.

    `open_dialog=False` обязателен: по умолчанию вендор ставит true, и заметка
    «раскрыла» бы закрытый диалог, испортив чат-менеджеру количество чатов и
    время ответа.

    Возвращает {'message_id', 'dialog_id', 'request_id', 'operator_id'}.
    """
    body = {
        'client_id': int(client_id),
        'text': str(text or '')[:MAX_COMMENT_LENGTH],
        'type': 'comment',
        'open_dialog': False,
    }
    payload = _request('POST', '/v1/messages', json_body=body)
    data = payload.get('data') or {}
    return {
        'message_id': data.get('message_id'),
        'dialog_id': data.get('dialog_id'),
        'request_id': data.get('request_id'),
        'operator_id': data.get('operator_id'),
    }
