import math
from datetime import datetime
from decimal import Decimal
from typing import Any


WEEKDAYS_RU = [
    {"index": 0, "key": "mon", "short": "ПН", "label": "Понедельник"},
    {"index": 1, "key": "tue", "short": "ВТ", "label": "Вторник"},
    {"index": 2, "key": "wed", "short": "СР", "label": "Среда"},
    {"index": 3, "key": "thu", "short": "ЧТ", "label": "Четверг"},
    {"index": 4, "key": "fri", "short": "ПТ", "label": "Пятница"},
    {"index": 5, "key": "sat", "short": "СБ", "label": "Суббота"},
    {"index": 6, "key": "sun", "short": "ВС", "label": "Воскресенье"},
]

FTE_ROUNDING_STEP = 0.5
RESOURCE_RATE_VALUES = (1.0, 0.75, 0.5)
WORK_DAYS_PER_OPERATOR_WEEK = 5


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace("\u00a0", "").replace(",", ".")
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _parse_report_date(value: Any) -> datetime.date:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("INVALID_REPORT_DATE")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _round_value(value: float, mode: str) -> float:
    number = float(value or 0)
    if mode == "ceil":
        return float(math.ceil(number))
    if mode == "floor":
        return float(math.floor(number))
    if mode == "round":
        return float(round(number))
    return number


def _round_fte_to_half(value: float) -> float:
    number = max(0.0, float(value or 0))
    return round(math.floor((number / FTE_ROUNDING_STEP) + 0.5) * FTE_ROUNDING_STEP, 4)


def _resource_rate_key(value: Any) -> str:
    rate = _to_float(value, 1.0)
    if abs(rate - 0.75) < 0.001:
        return "0.75"
    if abs(rate - 0.5) < 0.001:
        return "0.5"
    return "1"


def _resource_rate_value(value: Any) -> float:
    key = _resource_rate_key(value)
    if key == "0.75":
        return 0.75
    if key == "0.5":
        return 0.5
    return 1.0


