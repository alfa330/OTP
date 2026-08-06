"""Разбор ответов Workpace: даты, отметки, ФИО."""

from datetime import datetime, timezone
from typing import Optional

from group_late.config import TZ

DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    """Строка Workpace → время в Asia/Almaty. Без суффикса Z считаем локальным."""
    if not dt_str:
        return None
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(dt_str, fmt)
        except (ValueError, TypeError):
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(TZ)
        if fmt.endswith("Z"):
            return parsed.replace(tzinfo=timezone.utc).astimezone(TZ)
        return parsed.replace(tzinfo=TZ)
    return None


def format_time(value: Optional[datetime]) -> str:
    return value.strftime("%H:%M") if value else "—"


def to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def mark_date(mark: dict) -> Optional[str]:
    return mark.get("markDate") or mark.get("date")


def mark_type(mark: dict):
    return mark.get("markType") if mark.get("markType") is not None else mark.get("type")


def employee_name(item: dict) -> str:
    return item.get("employeeName") or item.get("name") or item.get("fullName") or "—"


def employee_id(item: dict) -> Optional[str]:
    for field in ("employeeId", "id", "employeeExternalId", "externalId"):
        value = item.get(field)
        if value:
            return str(value)
    return None


def employee_keys(item: dict) -> set[str]:
    keys = set()
    for field in ("employeeId", "employeeExternalId", "id", "externalId"):
        value = item.get(field)
        if value:
            keys.add(str(value))
    return keys


def is_archived(item: dict) -> bool:
    return (
        item.get("employeeIsArchived") is True
        or str(item.get("employeeIsArchived")).lower() == "true"
        or item.get("isArchived") is True
        or str(item.get("isArchived")).lower() == "true"
    )
