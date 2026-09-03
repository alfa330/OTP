"""Разбор ответов Workpace: даты, отметки, ФИО."""

from datetime import datetime, timezone
from typing import Optional

from group_late import config
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


def lunch_seconds(span_seconds: float, planned_break_seconds=None) -> int:
    """Сколько обеда вычесть из отрезка «приход → уход».

    `planned_break_seconds` — настоящий перерыв по расписанию человека, если
    источник его даёт (Clockster отдаёт `break_time`). Он главнее общего правила:
    у части людей обед не час, а у отпускного дня его нет вовсе, и плоский час
    врал бы обоим. Когда источник молчит (Workpace перерывов не ведёт), берём час
    из настроек — по правилам перерывов проекта столько даёт стандартная смена.

    Отрезок короче порога обеда не содержит — так же считают и правила перерывов.
    Вычитаем не больше самого отрезка: иначе время в работе уходит в минус и в
    отчёте появляется отрицательный час, которого не было."""
    if span_seconds <= 0:
        return 0
    if planned_break_seconds is None:
        planned = config.LUNCH_BREAK_MINUTES * 60
    else:
        try:
            planned = max(0, int(planned_break_seconds))
        except (TypeError, ValueError):
            planned = config.LUNCH_BREAK_MINUTES * 60
    if planned <= 0:
        return 0
    if span_seconds < config.LUNCH_BREAK_MIN_WORK_MINUTES * 60:
        return 0
    return int(min(planned, span_seconds))


def net_work_seconds(span_seconds: float, planned_break_seconds=None) -> int:
    """Время в работе за вычетом обеда — то, что просит ТЗ #273."""
    if span_seconds <= 0:
        return 0
    return max(0, int(span_seconds) - lunch_seconds(span_seconds, planned_break_seconds))


def employee_name(item: dict) -> str:
    return item.get("employeeName") or item.get("name") or item.get("fullName") or "—"


def employee_id(item: dict) -> Optional[str]:
    for field in ("employeeId", "id", "employeeExternalId", "externalId"):
        value = item.get(field)
        if value:
            return str(value)
    return None


def employee_keys(item: dict) -> set[str]:
    """Идентификаторы сотрудника, по которым к записи подбираются его отметки.

    `workpaceKeys` — список всех карточек человека в Workpace; его проставляет
    план из графика iCore. Одному сотруднику там нередко заведено несколько
    карточек, отметка ложится на любую из них, и по одному идентификатору его
    приход просто не находится."""
    keys = set()
    for field in ("employeeId", "employeeExternalId", "id", "externalId"):
        value = item.get(field)
        if value:
            keys.add(str(value))
    for value in (item.get("workpaceKeys") or []):
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
