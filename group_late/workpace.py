"""Клиент Workpace API.

Синхронный на requests — как остальные интеграции проекта: опрос и отчёты
крутятся в пуле потоков, а event loop бота остаётся свободным для Telegram.
Токены живут 30 минут (refresh — 23 часа), поэтому держим их в памяти с запасом
и обновляем под локом: в пуле потоков клиент дёргают параллельно.
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

import requests

from group_late import config

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL_MINUTES = 28   # реально 30, минус запас
REFRESH_TOKEN_TTL_HOURS = 22    # реально 23, минус запас
PAGE_SIZE = 100
REQUEST_TIMEOUT = 60


class WorkpaceError(RuntimeError):
    """Workpace недоступен или ответил ошибкой."""


class WorkpaceClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._access_expires_at: Optional[datetime] = None
        self._refresh_expires_at: Optional[datetime] = None

    # ------------------------------------------------------------------ auth

    def _store_tokens(self, access_token: str, refresh_token: str) -> None:
        now = datetime.utcnow()
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._access_expires_at = now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
        self._refresh_expires_at = now + timedelta(hours=REFRESH_TOKEN_TTL_HOURS)

    def _login(self) -> None:
        if not config.is_configured():
            raise WorkpaceError("Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD")
        url = f"{config.WORKPACE_BASE_URL}/api/auth/"
        try:
            response = requests.post(
                url,
                json={"login": config.WORKPACE_LOGIN, "password": config.WORKPACE_PASSWORD},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            self._store_tokens(data["accessToken"], data["refreshToken"])
        except requests.RequestException as exc:
            raise WorkpaceError(f"Не удалось авторизоваться в Workpace: {exc}") from exc
        except (KeyError, ValueError) as exc:
            raise WorkpaceError(f"Workpace вернул неожиданный ответ на авторизацию: {exc}") from exc
        logger.info("Workpace login successful")

    def _try_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        url = f"{config.WORKPACE_BASE_URL}/api/auth/refresh"
        try:
            response = requests.post(
                url,
                json={"accessToken": self._access_token, "refreshToken": self._refresh_token},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning("Workpace token refresh failed: %s", response.status_code)
                return False
            data = response.json()
            self._store_tokens(data["accessToken"], data["refreshToken"])
            return True
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.warning("Workpace refresh exception: %s", exc)
            return False

    def _valid_token(self) -> str:
        with self._lock:
            now = datetime.utcnow()
            if self._access_token and self._access_expires_at and now < self._access_expires_at:
                return self._access_token
            if self._refresh_token and self._refresh_expires_at and now < self._refresh_expires_at:
                if self._try_refresh():
                    return self._access_token
            self._login()
            return self._access_token

    def reset(self) -> None:
        """Забыть токены — например, когда сменили доступы в окружении."""
        with self._lock:
            self._access_token = None
            self._refresh_token = None
            self._access_expires_at = None
            self._refresh_expires_at = None

    # ------------------------------------------------------------------ http

    def _get(self, path: str, params: dict) -> dict:
        token = self._valid_token()
        url = f"{config.WORKPACE_BASE_URL}{path}"
        try:
            response = requests.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise WorkpaceError(f"Workpace {path}: {exc}") from exc
        except ValueError as exc:
            raise WorkpaceError(f"Workpace {path}: некорректный JSON ({exc})") from exc

    def _get_all(self, path: str, params: dict) -> list[dict]:
        """Постраничный обход: Workpace отдаёт data + totalCount."""
        records: list[dict] = []
        skip = 0
        total_count: Optional[int] = None
        while True:
            page = self._get(path, {**params, "skip": skip, "take": PAGE_SIZE,
                                    "requireTotalCount": "true"})
            chunk = page.get("data") or []
            records.extend(chunk)
            if total_count is None:
                total_count = page.get("totalCount")
            skip += len(chunk)
            if not chunk:
                break
            if total_count is not None and skip >= total_count:
                break
        logger.info("Workpace %s: получено %d записей (totalCount=%s)", path, len(records), total_count)
        return records

    # ------------------------------------------------------------------ data

    def get_timetable_spans(self, start: datetime, end: datetime) -> list[dict]:
        """Смены за период: план, факт прихода/ухода, опоздание."""
        return self._get_all("/public/v1/timetablespan", {
            "Start": start.isoformat(),
            "End": end.isoformat(),
        })

    def get_employees(self, active_only: bool = True) -> list[dict]:
        params = {}
        if active_only:
            params["filter"] = json.dumps(["isArchived", "=", False])
        return self._get_all("/public/v1/employee", params)

    def get_marks(self, start: datetime, end: datetime) -> list[dict]:
        """Первичные отметки терминала (DevExtreme-фильтр по дате)."""
        return self._get_all("/domain-api/mark", {
            "filter": json.dumps([
                ["markDate", ">=", start.isoformat()],
                "and",
                ["markDate", "<=", end.isoformat()],
            ]),
        })


workpace_client = WorkpaceClient()
