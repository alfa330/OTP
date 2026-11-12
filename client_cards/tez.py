# -*- coding: utf-8 -*-
"""Запросы в TEZ APP для карточки клиента: аккаунты по номеру и аккаунт по ID.

Два источника, и работает тот, что есть:

1. **Поиск аккаунтов** — POST {base}/accounts/lookup (docs/API_REQ_TEZ_ACCOUNTS_2026-11-12.md).
   По номеру отдаёт все аккаунты, водителей и клиентов; по виду и ID — один аккаунт. Этот
   метод TEZ APP ещё делает: включается переменной TEZ_ACCOUNTS_API_ENABLED=1, когда он
   выйдет на прод. До этого сервер его не зовёт вовсе — иначе каждая карточка стоила бы
   запроса с заведомым 404.

2. **Даты водителя** — уже работающий POST {base}/drivers/first-orders (tez/first_orders.py).
   По номеру говорит, есть ли на нём аккаунт ВОДИТЕЛЯ, когда он зарегистрирован и когда был
   последний заказ. ID, имени и города не отдаёт и про клиентов не знает — поэтому это только
   подсказка оператору («на этом номере есть водитель — найдите его в админке»), а сам
   аккаунт он прикладывает ссылкой.

Всё с короткими сроками: запрос идёт, пока оператор держит трубку. TEZ APP молчит —
карточка работает на том, что уже привязано к номеру у нас; ошибка сюда не роняет ничего.

ENV:
    TEZ_ACCOUNTS_API_ENABLED=1            включить поиск аккаунтов (метод 1)
    TEZ_ACCOUNTS_API_URL                  по умолчанию адрес TEZ_DRIVERS_API_URL / api.tezapp.org
    TEZ_ACCOUNTS_API_PATH                 по умолчанию /accounts/lookup
    TEZ_ACCOUNTS_API_TOKEN                по умолчанию TEZ_DRIVERS_API_TOKEN (тот же X-Integration-Token)
    TEZ_ADMIN_URL                         адрес админки для ссылок, по умолчанию https://admin.tezapp.org
"""

import json
import logging
import os
import threading
import time
from datetime import datetime

import requests

from . import rules

log = logging.getLogger(__name__)

DEFAULT_LOOKUP_PATH = '/accounts/lookup'
# Сроки под живой звонок: соединение и ответ. first-orders отвечает за 0,2–0,5 с.
CONNECT_TIMEOUT = 3
READ_TIMEOUT = 6
# Один и тот же номер за минуту спрашиваем один раз: телефон шлёт начало звонка,
# страница — выбор аккаунта, и оба хода ведут сюда.
MEMO_TTL_SEC = 60
MEMO_MAX = 500

SOURCE_ACCOUNTS = 'accounts'        # поиск аккаунтов (метод 1)
SOURCE_FIRST_ORDERS = 'first_orders'  # только даты водителя (метод 2)
SOURCE_OFF = 'off'                  # токена нет — TEZ APP не спрашиваем

_TYPE_KINDS = {'driver': rules.KIND_DRIVER, 'drivers': rules.KIND_DRIVER,
               'client': rules.KIND_CLIENT, 'clients': rules.KIND_CLIENT,
               'passenger': rules.KIND_CLIENT}
_KNOWN_FIELDS = ('type', 'kind', 'id', 'full_name', 'name', 'phone', 'city', 'status',
                 'registered_at', 'last_order_at')


def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def get_config():
    """Конфиг из окружения; токен и адрес — те же, что у first-orders, если своих нет."""
    from tez import first_orders

    base = first_orders.get_config()
    return {
        'enabled': _truthy(os.getenv('TEZ_ACCOUNTS_API_ENABLED')),
        'base_url': (os.getenv('TEZ_ACCOUNTS_API_URL') or base.get('base_url')
                     or first_orders.DEFAULT_API_URL).rstrip('/'),
        'path': '/' + (os.getenv('TEZ_ACCOUNTS_API_PATH') or DEFAULT_LOOKUP_PATH).strip('/'),
        'token': os.getenv('TEZ_ACCOUNTS_API_TOKEN') or base.get('token'),
        'admin_url': (os.getenv('TEZ_ADMIN_URL') or rules.DEFAULT_ADMIN_URL).rstrip('/'),
    }


def _naive_almaty(value):
    """ISO-строка TEZ APP → наивное время Алматы (так оно лежит в наших таблицах)."""
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(rules.ALMATY).replace(tzinfo=None)
    return parsed


def parse_account(row):
    """Строка ответа TEZ APP → аккаунт в нашем виде. None — строка не про аккаунт.

    Незнакомые поля не теряем: кладём в details — их покажет карточка, когда TEZ APP
    добавит что-то сверх договорённого (тариф, машина, баланс).
    """
    if not isinstance(row, dict):
        return None
    kind = _TYPE_KINDS.get(str(row.get('type') or row.get('kind') or '').strip().lower())
    try:
        tez_id = int(row.get('id'))
    except (TypeError, ValueError):
        return None
    if not kind or tez_id <= 0:
        return None
    details = {key: value for key, value in row.items()
               if key not in _KNOWN_FIELDS and value not in (None, '', [], {})}
    return {
        'kind': kind,
        'tez_id': tez_id,
        'full_name': rules.clean_text(row.get('full_name') or row.get('name'), 255) or None,
        'phone': rules.phone_key(row.get('phone')),
        'city': rules.clean_text(row.get('city'), 120) or None,
        'status': rules.clean_text(row.get('status'), 64) or None,
        'registered_at': _naive_almaty(row.get('registered_at')),
        'last_order_at': _naive_almaty(row.get('last_order_at')),
        'details': details,
    }


class TezAccounts:
    """Клиент TEZ APP для карточки. Ни одна функция не бросает исключений наружу."""

    def __init__(self, config=None, *, session=None, clock=time.monotonic, first_orders_fetch=None):
        self._config = config
        self._session = session or requests.Session()
        self._clock = clock
        self._first_orders_fetch = first_orders_fetch
        self._memo = {}
        self._lock = threading.Lock()

    def config(self):
        return self._config if self._config is not None else get_config()

    def source(self):
        cfg = self.config()
        if not cfg.get('token'):
            return SOURCE_OFF
        return SOURCE_ACCOUNTS if cfg.get('enabled') else SOURCE_FIRST_ORDERS

    def admin_url(self):
        return self.config().get('admin_url') or rules.DEFAULT_ADMIN_URL

    # ─── Память на минуту ────────────────────────────────────────────────────

    def _remember(self, key, value):
        with self._lock:
            if len(self._memo) >= MEMO_MAX:
                self._memo.clear()
            self._memo[key] = (self._clock(), value)
        return value

    def _recall(self, key):
        with self._lock:
            hit = self._memo.get(key)
        if hit and self._clock() - hit[0] < MEMO_TTL_SEC:
            return hit[1]
        return None

    def forget(self, phone=None):
        """Сбросить память: номер привязали или отвязали — следующий запрос идёт заново."""
        with self._lock:
            if phone is None:
                self._memo.clear()
            else:
                self._memo.pop(('phone', phone), None)

    # ─── Запрос ──────────────────────────────────────────────────────────────

    def _lookup(self, body):
        """POST /accounts/lookup → (список аккаунтов | None, ошибка)."""
        from tez.first_orders import BROWSER_HEADERS, _looks_like_cloudflare_block

        cfg = self.config()
        headers = dict(BROWSER_HEADERS)
        headers['X-Integration-Token'] = cfg['token']
        headers['Content-Type'] = 'application/json'
        try:
            response = self._session.post(
                cfg['base_url'] + cfg['path'],
                data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        except requests.RequestException as error:
            log.warning('client_cards: TEZ APP не ответил на поиск аккаунтов: %s', type(error).__name__)
            return None, 'TEZ APP не отвечает'
        if response.status_code == 404:
            # Нет такого аккаунта — либо метода ещё нет на этом адресе. Различает тело:
            # наш метод отвечает JSON, а несуществующий путь — нет.
            try:
                response.json()
            except ValueError:
                log.warning('client_cards: поиска аккаунтов на %s%s нет (404)', cfg['base_url'], cfg['path'])
                return None, 'В TEZ APP нет метода поиска аккаунтов'
            return [], None
        if response.status_code in (401, 403):
            blocked = _looks_like_cloudflare_block(response.text)
            log.warning('client_cards: TEZ APP отказал в поиске аккаунтов: HTTP %s%s',
                        response.status_code, ' (Cloudflare)' if blocked else '')
            return None, 'TEZ APP не принял ключ портала'
        if response.status_code != 200:
            log.warning('client_cards: TEZ APP, поиск аккаунтов: HTTP %s', response.status_code)
            return None, 'TEZ APP ответил ошибкой %s' % response.status_code
        try:
            payload = response.json()
        except ValueError:
            return None, 'TEZ APP ответил не по договору'
        rows = payload.get('accounts') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None, 'TEZ APP ответил не по договору'
        accounts = [account for account in (parse_account(row) for row in rows) if account]
        return accounts, None

    def _driver_hint(self, phone):
        """Есть ли на номере аккаунт водителя — по first-orders. (подсказка | None, ошибка)."""
        fetch = self._first_orders_fetch or _fetch_first_orders
        try:
            dates = fetch(phone)
        except Exception as error:  # noqa: BLE001 — чужой клиент, ронять карточку нечем
            log.warning('client_cards: TEZ APP не ответил на даты водителя: %s', type(error).__name__)
            return None, 'TEZ APP не отвечает'
        if not dates or not dates.get('account_known'):
            # Поля registered_at в ответе не было: сказать «аккаунта нет» нечем.
            return None, None
        registered = _naive_almaty(dates.get('registered'))
        if not registered:
            return {'exists': False}, None
        return {'exists': True, 'registered_at': registered,
                'last_order_at': _naive_almaty(dates.get('last_order'))}, None

    # ─── Наружу ──────────────────────────────────────────────────────────────

    def by_phone(self, phone):
        """Что TEZ APP знает о номере.

        {'source', 'ok', 'error', 'accounts': [...], 'driver_hint': {...} | None}
        ok=False — TEZ APP не ответил: это «не знаем», а не «аккаунтов нет».
        """
        source = self.source()
        if source == SOURCE_OFF or not phone:
            return {'source': SOURCE_OFF, 'ok': False, 'error': None, 'accounts': [], 'driver_hint': None}
        cached = self._recall(('phone', phone))
        if cached is not None:
            return cached
        if source == SOURCE_ACCOUNTS:
            accounts, error = self._lookup({'phone': '+' + phone})
            result = {'source': source, 'ok': accounts is not None, 'error': error,
                      'accounts': accounts or [], 'driver_hint': None}
        else:
            hint, error = self._driver_hint(phone)
            result = {'source': source, 'ok': error is None, 'error': error,
                      'accounts': [], 'driver_hint': hint}
        # Отказ не запоминаем: следующий звонок должен спросить заново.
        return self._remember(('phone', phone), result) if result['ok'] else result

    def by_id(self, kind, tez_id):
        """Профиль аккаунта по виду и ID.

        {'source', 'ok', 'error', 'account': {...} | None}
        ok=True и account=None — TEZ APP ответил, что такого аккаунта нет.
        При выключенном поиске аккаунтов ok=False: проверить ссылку нечем, аккаунт
        закрепляется как есть — видом и ID.
        """
        source = self.source()
        if source != SOURCE_ACCOUNTS:
            return {'source': source, 'ok': False, 'error': None, 'account': None}
        accounts, error = self._lookup({'type': kind, 'id': int(tez_id)})
        if accounts is None:
            return {'source': source, 'ok': False, 'error': error, 'account': None}
        match = next((item for item in accounts
                      if item['kind'] == kind and item['tez_id'] == int(tez_id)), None)
        return {'source': source, 'ok': True, 'error': None, 'account': match}


def _fetch_first_orders(phone):
    """Даты водителя по одному номеру: {'account_known', 'registered', 'last_order'} или None.

    Тот же метод и те же заголовки, что у подсчёта успешек Тез (tez/first_orders.py), но
    одним запросом без повторов: тот клиент рассчитан на ночную сверку и при сбое сети
    ждёт до полуминуты — оператору с трубкой в руке столько ждать нечего.
    """
    from tez import first_orders

    cfg = first_orders.get_config()
    if not first_orders.api_ready(cfg):
        return None
    headers = dict(first_orders.BROWSER_HEADERS)
    headers['X-Integration-Token'] = cfg['token']
    headers['Content-Type'] = 'application/json'
    body = {'month': rules.now_almaty().strftime('%Y-%m'),
            'drivers': [{'full_name': '-', 'phone': '+' + phone}]}
    response = requests.post(cfg['base_url'] + '/drivers/first-orders',
                             data=json.dumps(body).encode('utf-8'), headers=headers,
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    if response.status_code != 200:
        raise RuntimeError('HTTP %s' % response.status_code)
    rows = (response.json() or {}).get('drivers') or []
    row = next((item for item in rows if rules.phone_key(item.get('phone')) == phone), None)
    if row is None:
        return None
    return {'account_known': 'registered_at' in row,
            'registered': row.get('registered_at'), 'last_order': row.get('last_order_at')}
