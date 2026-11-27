# -*- coding: utf-8 -*-
"""Откуда «Активация водителей» берёт поездки и провайдера ЭДО (activation_check.py). Сеть — только здесь.

CRM yataxi (тот же токен, что у «Посылок» и «Воды», parcels/drivers.get_config):
  • POST /api/partners/orders {account_ids ≤ 1000, from, to} — завершённые заказы аккаунтов за календарные
    даты (обе включительно). Замер 26.11.2026 на файле «Новички по поездкам с ЭДО»: 20 ID × окно с 01.10 —
    0,9 с, число совпало с колонкой «Завершено поездок» у 19 из 20 (у двадцатого +3: откатал после выгрузки).
    Заказов в базе CRM нет раньше 17.04.2026, окно «год × 1000 ID» CRM не тянет (таймаут) — поэтому окно
    начинается с месяца регистрации водителя. На чужой ID ручка отвечает как на настоящий ноль;
  • POST /api/partners/driver-info {account_id} — карточка: парк во Fleet (park.yandex_id), orders.total (окно
    ~полгода — у новичков это и есть «за всё время»), employment. Лимит токена 120/мин общий с порталом.

Кабинет Fleet (учётки раздела «Провайдер ЭДО», fleet_edm): провайдер ЭДО значением есть только в карточке
водителя (cards/driver/details, нужен парк), ~22 карточки в минуту на учётку. Фильтр списка по провайдеру с
28.10.2026 у кабинета сломан (fleet_edm/engine.py, SOURCE_PRIOR) — им не пользуемся.
"""
import logging
import threading
import time
from datetime import date

import requests

log = logging.getLogger(__name__)

CRM_TIMEOUT_SEC = 60
CRM_BATCH_MAX = 1000
# Клиент кабинета живёт столько, потом учётку выбираем заново (её могли разлогинить или вернуть).
FLEET_CLIENT_TTL_SEC = 15 * 60
# После «сессия кабинета не настроена или протухла» столько секунд не пробуем.
FLEET_BACKOFF_SEC = 10 * 60


class SourceError(Exception):
    """Источник не ответил по делу: связь, 5xx, незнакомый ответ. Текст — для журнала и руководителя."""


class CrmSource:
    """CRM yataxi. config — {'url', 'token'} (по умолчанию parcels.drivers.get_config()); session — подмена."""

    def __init__(self, config=None, session=None):
        self._config = config
        self._session = session

    def _cfg(self):
        if self._config is not None:
            return self._config
        from parcels import drivers as crm
        return crm.get_config()

    def configured(self):
        try:
            cfg = self._cfg()
        except Exception:
            log.exception("dial_list: активация — не прочитан доступ к CRM")
            return False
        return bool(cfg.get("token"))

    def _post(self, path, body, timeout):
        cfg = self._cfg()
        if not cfg.get("token"):
            raise SourceError("Связь с CRM не настроена")
        base = str(cfg.get("url") or "https://backend.yataxi.kz/api/partners/driver-info").rstrip("/")
        url = base.rsplit("/", 1)[0] + "/" + path
        http = self._session or requests
        try:
            response = http.post(url, json=body, timeout=timeout or CRM_TIMEOUT_SEC,
                                 headers={"X-Integration-Token": cfg["token"], "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise SourceError(f"CRM не ответила: {str(exc)[:160]}")
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise SourceError("CRM просит помедленнее (429)")
        if response.status_code == 503:
            raise SourceError("База заказов CRM недоступна (503)")
        if response.status_code >= 400:
            raise SourceError(f"CRM ответила ошибкой {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise SourceError("CRM вернула не JSON")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            # Ловушка CRM: на кривой запрос — HTML-страница с кодом 200. Незнакомый ответ — ошибка, не ноль.
            raise SourceError("CRM вернула незнакомый ответ")
        return data

    def orders(self, account_ids, date_from, date_to, timeout=None):
        """Завершённые заказы аккаунтов за даты (включительно) → {account_id: {'count': int, 'last_order_at':
        str|None}}. Аккаунта без числа в ответе нет в словаре (вызывающий считает его ошибкой, а не нулём)."""
        ids = [str(x).strip().lower() for x in (account_ids or []) if str(x or "").strip()]
        if not ids:
            return {}
        if len(ids) > CRM_BATCH_MAX:
            raise ValueError(f"не больше {CRM_BATCH_MAX} ID за запрос")
        data = self._post("orders", {"account_ids": ids, "from": _day(date_from), "to": _day(date_to)}, timeout)
        result = {}
        for key, entry in (data or {}).items():
            if not isinstance(entry, dict):
                continue
            count = _int(entry.get("orders_count"))
            if count is None:
                continue
            result[str(key).strip().lower()] = {"count": count, "last_order_at": entry.get("last_order_at")}
        return result

    def driver(self, account_id, timeout=None):
        """Карточка водителя → {'park_id', 'park_name', 'total', 'created_date', 'employment_type',
        'last_order_at'}; водителя нет — None."""
        data = self._post("driver-info", {"account_id": str(account_id or "").strip().lower()}, timeout)
        if data is None:
            return None
        park = data.get("park") if isinstance(data.get("park"), dict) else {}
        orders = data.get("orders") if isinstance(data.get("orders"), dict) else {}
        employment = data.get("employment") if isinstance(data.get("employment"), dict) else {}
        return {
            "park_id": str(park.get("yandex_id") or "").strip().lower(),
            "park_name": " ".join(str(park.get("name") or "").split()),
            "total": _int(orders.get("total")),
            "last_order_at": orders.get("last_order_at"),
            "created_date": employment.get("created_date"),
            "employment_type": str(employment.get("employment_type") or "").strip(),
        }


class FleetSource:
    """Провайдер ЭДО из карточки водителя кабинета Fleet — учётками раздела «Провайдер ЭДО» (своя, затем
    «Рассылок»: fleet_edm.accounts.choose). db — та же база; make_client — подмена клиента в тестах."""

    def __init__(self, db, make_client=None, clock=time.monotonic):
        self.db = db
        self._make_client = make_client
        self._clock = clock
        self._lock = threading.Lock()
        self._client = None
        self._client_at = 0.0
        self._backoff_until = 0.0
        self.last_error = ""

    def _sessions(self):
        from fleet_edm import accounts, queries
        with self.db._get_cursor() as cur:
            own = queries.load_session(cur)
            helpers = []
            try:
                helpers = queries.load_helper_sessions(cur)
            except Exception:
                log.exception("dial_list: активация — учётку «Рассылок» кабинета Fleet не прочитали")
        return [(accounts.OWN, own)] + [(accounts.candidate_name(row), row) for row in helpers]

    def configured(self):
        """Есть ли хоть одна сессия кабинета (живость проверит первый запрос)."""
        if self._make_client is not None:
            return True
        try:
            return any(row and row.get("cookies") for _name, row in self._sessions())
        except Exception:
            log.exception("dial_list: активация — сессии кабинета Fleet не прочитаны")
            return False

    def _get_client(self):
        now = self._clock()
        with self._lock:
            if self._client is not None and now - self._client_at < FLEET_CLIENT_TTL_SEC:
                return self._client
            if now < self._backoff_until:
                raise SourceError(self.last_error or "Кабинет Fleet недоступен")
        if self._make_client is not None:
            client = self._make_client()
        else:
            from fleet_edm import accounts
            from fleet_edm.client import FleetClient
            candidates = self._sessions()
            if not any(row and row.get("cookies") for _name, row in candidates):
                self._fail("Сессия кабинета Fleet не настроена (раздел «Провайдер ЭДО»)")
            # Один поток на клиента: карточки идут по одной, общий шаг кабинета держит сам клиент (Pacer).
            selection = accounts.choose(
                candidates, lambda row: FleetClient(row["cookies"], row.get("user_agent"), concurrency=1))
            if selection.main is None:
                error = selection.failures[0][1] if selection.failures else "нет живой учётки"
                self._fail(f"Кабинет Fleet не пустил ни одну учётку: {str(error)[:160]}")
            client = selection.main
        with self._lock:
            self._client, self._client_at, self.last_error = client, self._clock(), ""
        return client

    def _fail(self, message):
        with self._lock:
            self._client = None
            self._backoff_until = self._clock() + FLEET_BACKOFF_SEC
            self.last_error = message
        raise SourceError(message)

    def provider(self, park_id, driver_id):
        """Провайдер водителя по карточке: (имя, есть ли карточка). ('', True) — карточка есть, провайдера нет
        (сотрудник парка или ЭДО не подключено); ('', False) — в этом парке водителя нет (404)."""
        from fleet_edm.client import FleetClient, FleetError, FleetSessionExpired
        client = self._get_client()
        try:
            profile = client.driver_card(str(park_id), str(driver_id))
        except FleetSessionExpired as exc:
            self._fail(f"Сессия кабинета Fleet протухла: {str(exc)[:160]}")
        except FleetError as exc:
            raise SourceError(f"Кабинет Fleet не ответил: {str(exc)[:160]}")
        if profile is None:
            return "", False
        return FleetClient.card_provider(profile), True


def _day(value):
    return value.isoformat() if isinstance(value, date) else str(value)


def _int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None
