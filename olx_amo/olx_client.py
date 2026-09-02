# -*- coding: utf-8 -*-
"""Клиент OLX Partner API v2: токены, треды, сообщения.

Что выяснено про этот API и почему клиент устроен именно так
------------------------------------------------------------

* **Чаты доступны только в пользовательском контексте.** `client_credentials`
  отдаёт исключительно справочники (регионы, категории, валюты); на `/threads`
  такой токен получает 401. Значит владелец КАЖДОГО из девяти кабинетов должен
  один раз подтвердить согласие в браузере (`grant_type=authorization_code`),
  после чего мы живём на его `refresh_token`. Отсюда `authorize_url` и
  `exchange_code` в этом модуле: без них робота не запустить в принципе.

* **Вебхуков нет.** Ни подписок, ни push — в спецификации Partner API v2 нет ни
  одного пути про callbacks. SLA «минута в минуту» из ТЗ достижим только
  опросом, поэтому клиент оптимизирован под частый дешёвый опрос.

* **Фильтра «что нового» тоже нет.** У `GET /threads` всего четыре параметра:
  advert_id, interlocutor_id, offset, limit. Ни `since`, ни `updated_after`.
  Новое ищется по `unread_count > 0` и сверкой с собственной закладкой.

* **Лимит — 4500 запросов с IP за 5 минут**, при превышении блокировка на
  полчаса. Девять кабинетов с опросом раз в 30 секунд — это 90 запросов за 5
  минут, два процента бюджета. Но если на каждом цикле дочитывать сообщения во
  всех тредах подряд, бюджет пробивается уже на полусотне чатов. Поэтому в
  `/messages` клиент ходит ТОЛЬКО за тредами с непрочитанным, а расход держит
  под общим на процесс счётчиком `_Budget` — он один на все кабинеты, потому
  что лимит считается по IP, а IP у них общий.

* **Ответ приходит конвертом.** Спецификация обещает голый массив, а боевой API
  отдаёт `{"data": [...], "metadata": ..., "links": ...}`. Клиент понимает оба
  вида — расхождение спецификации с поведением здесь норма, и ломаться на нём
  раз в полгода не хочется.

* **Телефон кандидата бывает отдельным полем.** У сообщения есть
  недокументированное поле `phone`, и документация к отправке сообщения прямо
  говорит: «Field 'phone' … used in Jobs categories right now». Мы его читаем,
  но не полагаемся на него: основной путь — разбор текста (см. `phones.py`).
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

API_BASE = (os.getenv('OLX_API_BASE') or 'https://www.olx.kz/api/partner').rstrip('/')
TOKEN_URL = os.getenv('OLX_TOKEN_URL') or 'https://www.olx.kz/api/open/oauth/token'
AUTHORIZE_URL = os.getenv('OLX_AUTHORIZE_URL') or 'https://www.olx.kz/oauth/authorize'

# Скоупов у Partner API ровно три: v2 (доступ к версии 2), read и write.
# Отдельного скоупа на переписку нет — чтение тредов закрывает read, отправка
# заготовленного ответа и отметка «прочитано» требуют write.
SCOPES = os.getenv('OLX_SCOPES') or 'v2 read write'

# Адрес, на который OLX возвращает браузер после согласия владельца кабинета.
# Он обязан совпадать с тем, что вписан в заявку на приложение, — но важен он на
# ЭКРАНЕ СОГЛАСИЯ, а не на обмене кода: проверено на живом API 31.08.2026, обмен
# отвечает одинаковым `invalid_grant` и без адреса, и с посторонним. Поэтому
# отсутствие адреса не повод отказывать в обмене (см. `exchange_code`).
# Держим в окружении: адрес один на все девять приложений, и вводить его руками
# девять раз подряд — верный способ ошибиться в одном из них.
REDIRECT_URI = (os.getenv('OLX_REDIRECT_URI') or '').strip()

REQUEST_TIMEOUT = float(os.getenv('OLX_REQUEST_TIMEOUT') or 20)

# Бюджет запросов: 4500 на IP за 5 минут. Берём с запасом вдвое — лимит общий на
# весь наш адрес, и делить его с чем-то ещё безопаснее, чем упереться в
# получасовую блокировку, из которой нет выхода, кроме ожидания.
_BUDGET_LIMIT = int(os.getenv('OLX_BUDGET_LIMIT') or 2200)
_BUDGET_WINDOW = 300.0


class OlxError(Exception):
    """Любая неудача разговора с OLX."""

    def __init__(self, message, status=None, payload=None):
        super(OlxError, self).__init__(message)
        self.status = status
        self.payload = payload


class OlxAuthError(OlxError):
    """Токен не принят или его нечем обновить — нужен новый вход владельца кабинета."""


class OlxRateLimited(OlxError):
    """Уперлись в лимит. `retry_after` — через сколько секунд можно повторить."""

    def __init__(self, message, retry_after=None, status=None, payload=None):
        super(OlxRateLimited, self).__init__(message, status=status, payload=payload)
        self.retry_after = retry_after


class _Budget(object):
    """Счётчик запросов в скользящем окне. Один на процесс — лимит считается по IP.

    Не «умный» лимитер с очередью: если бюджет исчерпан, вызывающий получает
    отказ сразу и пропускает цикл опроса. Ждать внутри клиента нельзя — за
    ожиданием стоит поток из небольшого пула, и заснувший в нём запрос
    останавливает опрос остальных кабинетов.
    """

    def __init__(self, limit=_BUDGET_LIMIT, window=_BUDGET_WINDOW):
        self._limit = limit
        self._window = window
        self._hits = []
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def take(self):
        now = time.time()
        with self._lock:
            if now < self._blocked_until:
                raise OlxRateLimited(
                    'OLX временно заблокировал запросы',
                    retry_after=int(self._blocked_until - now))
            edge = now - self._window
            self._hits = [t for t in self._hits if t > edge]
            if len(self._hits) >= self._limit:
                raise OlxRateLimited(
                    'исчерпан собственный бюджет запросов к OLX (%d за %d с)'
                    % (self._limit, int(self._window)),
                    retry_after=int(self._hits[0] + self._window - now) + 1)
            self._hits.append(now)

    def block_for(self, seconds):
        """OLX сказал «слишком часто» — молчим столько, сколько он просит."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.time() + max(1, seconds))

    def snapshot(self):
        now = time.time()
        with self._lock:
            edge = now - self._window
            used = len([t for t in self._hits if t > edge])
            return {'used': used, 'limit': self._limit,
                    'blocked_for': max(0, int(self._blocked_until - now))}


BUDGET = _Budget()


# ─────────────────────────────────────────────────────────────────────────────
# Согласие владельца кабинета и токены
# ─────────────────────────────────────────────────────────────────────────────

def authorize_url(client_id, redirect_uri, state):
    """Ссылка, по которой владелец кабинета подтверждает доступ.

    Открывается ОДИН раз на кабинет и обязательно в браузере, где выполнен вход
    именно в этот кабинет OLX: согласие выдаётся от лица того, кто вошёл, а не
    того, чей client_id в ссылке. Перепутать вход — самая частая ошибка при
    подключении: токен приедет, но чужой, и `/threads` вернёт чужие чаты.
    """
    from urllib.parse import urlencode
    return AUTHORIZE_URL + '?' + urlencode({
        'client_id': client_id,
        'response_type': 'code',
        'scope': SCOPES,
        'redirect_uri': redirect_uri,
        'state': state,
    })


def _post_token(payload):
    try:
        response = requests.post(TOKEN_URL, json=payload, timeout=REQUEST_TIMEOUT,
                                 headers={'Accept': 'application/json'})
    except requests.RequestException as exc:
        raise OlxError('не достучались до токен-ручки OLX: %s' % (exc,))

    try:
        body = response.json()
    except ValueError:
        body = {'raw': response.text[:500]}

    if response.status_code >= 400:
        detail = body.get('error_description') or body.get('error') or response.text[:200]
        message = 'OLX отказал в токене (%s): %s' % (response.status_code, detail)
        # «Client is not active» приходит и на несуществующее приложение, и на
        # не одобренное модерацией: OLX склеивает эти случаи в один ответ.
        # Различить их можно только в личном кабинете разработчика, поэтому в
        # сообщении честно называем оба варианта, а не гадаем.
        if body.get('error') == 'invalid_client':
            message += ('. Приложение OLX не найдено или не активировано — '
                        'проверьте client_id/client_secret и статус заявки '
                        'на developer.olx.kz')
        raise OlxAuthError(message, status=response.status_code, payload=body)

    token = body.get('access_token')
    if not token:
        raise OlxAuthError('OLX вернул ответ без access_token', payload=body)

    # Живёт час. Минуту снимаем на дорогу: обновиться заранее дешевле, чем
    # получить 401 посреди разбора обращения и потерять цикл опроса.
    expires_in = int(body.get('expires_in') or 3600)
    return {
        'access_token': token,
        'refresh_token': body.get('refresh_token'),
        'scope': body.get('scope') or SCOPES,
        'expires_at': datetime.utcnow() + timedelta(seconds=max(60, expires_in - 60)),
    }


def exchange_code(client_id, client_secret, code, redirect_uri=None):
    """Обменять код согласия на пару токенов. Код живёт считанные секунды.

    `redirect_uri` необязателен. Проверено на живом API 31.08.2026: на обмене
    OLX сверяет только сам код и отвечает `invalid_grant` одинаково — и без
    адреса, и с нашим, и с заведомо посторонним. Адрес по-настоящему важен
    раньше, на экране согласия: именно туда OLX вернёт браузер, и именно он
    должен совпадать с заявкой на приложение.

    Поэтому пустое значение сюда просто не кладём, а не отказываем: если админ
    подключал кабинет по адресу, отличному от настроенного, обмен всё равно
    должен пройти, а не упереться в нашу же проверку.
    """
    payload = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'scope': SCOPES,
    }
    if redirect_uri:
        payload['redirect_uri'] = redirect_uri
    return _post_token(payload)


def refresh_tokens(client_id, client_secret, refresh_token):
    """Обновить access_token по refresh_token.

    OLX РОТИРУЕТ refresh_token: в ответе приезжает новый, а старый перестаёт
    работать. Не записать новый — значит потерять кабинет и звать владельца
    заново проходить согласие. Поэтому вызывающий обязан сохранить весь ответ
    целиком, а не только access_token.
    """
    return _post_token({
        'grant_type': 'refresh_token',
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'scope': SCOPES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Разговор с API
# ─────────────────────────────────────────────────────────────────────────────

def _unwrap(body):
    """Достать полезную нагрузку из конверта {data, metadata, links} или из массива."""
    if isinstance(body, dict) and 'data' in body:
        return body.get('data'), body.get('metadata') or {}, body.get('links') or {}
    return body, {}, {}


class OlxClient(object):
    """Разговор с одним кабинетом OLX.

    Токен не хранится в клиенте: его даёт `token_provider()` при каждом запросе,
    а обновление лежит на вызывающем (`service.py`), потому что новый токен надо
    не только использовать, но и записать в базу — клиент про базу не знает и
    знать не должен.
    """

    def __init__(self, token_provider, session=None, budget=None):
        self._token = token_provider
        self._session = session or requests.Session()
        self._budget = budget or BUDGET

    # -- транспорт ---------------------------------------------------------

    def _request(self, method, path, params=None, json_body=None):
        self._budget.take()
        url = API_BASE + path
        headers = {
            'Authorization': 'Bearer %s' % (self._token(),),
            # Без этого заголовка API отвечает первой версией — с другими
            # именами полей. Он обязателен на КАЖДОМ запросе, не только на
            # получении токена.
            'Version': '2.0',
            'Accept': 'application/json',
        }
        try:
            response = self._session.request(
                method, url, params=params, json=json_body,
                headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise OlxError('%s %s: сеть не отдала ответ (%s)' % (method, path, exc))

        if response.status_code == 204:
            return None, {}, {}

        try:
            body = response.json()
        except ValueError:
            body = {'raw': response.text[:500]}

        if response.status_code == 401:
            raise OlxAuthError('OLX не принял токен на %s' % (path,),
                               status=401, payload=body)
        if response.status_code == 429 or self._is_throttle_ban(response, body):
            retry = self._retry_after(response)
            self._budget.block_for(retry)
            raise OlxRateLimited('OLX ограничил частоту на %s' % (path,),
                                 retry_after=retry, status=response.status_code,
                                 payload=body)
        if response.status_code >= 400:
            raise OlxError('%s %s вернул %s: %s'
                           % (method, path, response.status_code, str(body)[:300]),
                           status=response.status_code, payload=body)

        return _unwrap(body)

    @staticmethod
    def _is_throttle_ban(response, body):
        """403 у OLX бывает двух смыслов: «нет прав» и «слишком часто, бан на 30 минут».

        Отличаем по тексту: у блокировки в теле стоит про частоту запросов. Спутать
        нельзя — на «нет прав» повтор бесполезен, а на бан повтор продлевает бан.
        """
        if response.status_code != 403:
            return False
        text = str(body).lower()
        return 'too many' in text or 'rate' in text or 'requests' in text

    @staticmethod
    def _retry_after(response):
        raw = response.headers.get('Retry-After')
        if raw and str(raw).strip().isdigit():
            return max(1, int(str(raw).strip()))
        # Заголовка может не быть: у блокировки по IP он не документирован.
        # Полчаса — заявленная длительность бана.
        return 1800 if response.status_code == 403 else 60

    # -- чаты --------------------------------------------------------------

    def threads(self, offset=0, limit=50, advert_id=None):
        """Список чатов кабинета.

        `limit` в спецификации без потолка, поэтому по умолчанию берём скромные
        50: страница дешевле, чем угадывать максимум и получить 400 в проде.
        Сортировка выдачи НЕ документирована, и полагаться на «свежие сверху»
        нельзя — вызывающий обязан пройти страницы, пока встречается непрочитанное.
        """
        params = {'offset': int(offset), 'limit': int(limit)}
        if advert_id:
            params['advert_id'] = advert_id
        data, meta, links = self._request('GET', '/threads', params=params)
        return list(data or []), meta, links

    def thread(self, thread_id):
        data, _, _ = self._request('GET', '/threads/%s' % (thread_id,))
        return data

    def messages(self, thread_id, offset=0, limit=30):
        """Сообщения чата.

        Спецификация у этой операции параметров не объявляет вовсе, но боевой
        API offset/limit принимает. Передаём их и на всякий случай не считаем
        ошибкой, если они будут проигнорированы: разбор всё равно идёт по
        собственной закладке `last_message_id`.
        """
        data, meta, links = self._request(
            'GET', '/threads/%s/messages' % (thread_id,),
            params={'offset': int(offset), 'limit': int(limit)})
        return list(data or []), meta, links

    def send_message(self, thread_id, text):
        """Ответить кандидату. Это единственная операция робота на запись в OLX."""
        data, _, _ = self._request(
            'POST', '/threads/%s/messages' % (thread_id,),
            json_body={'text': text})
        return data

    def mark_read(self, thread_id):
        """Отметить чат прочитанным.

        Нужна не роботу — он ведёт свою закладку, — а человеку: непрочитанное в
        кабинете должно гаснуть, иначе маркетолог продолжит открывать чаты,
        которые робот уже обработал.
        """
        self._request('POST', '/threads/%s/commands' % (thread_id,),
                      json_body={'command': 'mark-as-read'})

    def me(self):
        """Кто мы для OLX. Единственный дешёвый способ проверить живость токена."""
        data, _, _ = self._request('GET', '/users/me')
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Разбор сообщения
# ─────────────────────────────────────────────────────────────────────────────

def message_is_incoming(message):
    """Сообщение от кандидата, а не наше собственное.

    Тип `sent` — то, что отправили мы (в том числе сам робот заготовленным
    ответом). Обрабатывать своё же сообщение как обращение — прямой путь
    завести сделку с собственным номером линии.
    """
    return (message or {}).get('type') == 'received'


def message_phone(message):
    """Телефон из недокументированного поля `phone`, если он там есть.

    В схеме Message этого поля нет, но документация к отправке сообщения
    упоминает его прямо: «Field 'phone' … used in Jobs categories right now», а
    сторонние клиенты его читают. Возвращаем как есть — нормализацией занимается
    `phones.normalize`, здесь только достаём.
    """
    if not isinstance(message, dict):
        return None
    for key in ('phone', 'phone_number', 'contact_phone'):
        value = message.get(key)
        if value:
            return str(value)
    return None


def message_time(message):
    """`created_at` сообщения как naive-datetime в Алматы.

    **OLX отдаёт время В UTC И БЕЗ ПОМЕТКИ О ЗОНЕ** — строкой вида
    `'2026-09-01 16:57:32'`, даже не по ISO (пробел вместо «T»). Документация об
    этом молчит, схема обещает `date-time`.

    Проверено 02.09.2026 на боевом кабинете, и доказательство не косвенное: в
    чате лежит НАШ СОБСТВЕННЫЙ автоответ, и OLX датирует его `19:02:33`, тогда
    как в своей базе мы записали момент отправки как `00:02:32` по Алматы. Ровно
    пять часов — то есть отдаётся UTC.

    Цена ошибки была высокой. Считая такое время местным, робот видел каждое
    сообщение на пять часов старше, чем оно есть, и горизонт в 15 минут отсекал
    ПЕРВОЕ сообщение любого нового чата как «старую историю». Закладка при этом
    вставала, и обращение не возвращалось уже никогда: 01.09.2026 так потерялся
    отклик в noltaxi_olx. Доезжали только вторые и последующие сообщения — они
    проходят по закладке, минуя горизонт.
    """
    raw = (message or {}).get('created_at')
    if not raw:
        return None
    text = str(raw).strip().replace('Z', '+00:00')
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None

    # Смещение Алматы фиксированное (+5 с 01.03.2024, перевода часов нет), и
    # берём его числом, а не ZoneInfo: tzdata на контейнере может отсутствовать.
    # Тот же приём в queries.py и в parcels/queries.py.
    if stamp.tzinfo is None:
        # Зоны нет — это UTC (см. выше), а не местное время.
        return stamp + timedelta(hours=5)
    return (stamp - stamp.utcoffset()).replace(tzinfo=None) + timedelta(hours=5)
