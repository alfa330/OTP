import csv
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from psycopg2.extras import Json, execute_values


WEEKDAYS_RU = [
    {"index": 0, "key": "mon", "short": "ПН", "label": "Понедельник"},
    {"index": 1, "key": "tue", "short": "ВТ", "label": "Вторник"},
    {"index": 2, "key": "wed", "short": "СР", "label": "Среда"},
    {"index": 3, "key": "thu", "short": "ЧТ", "label": "Четверг"},
    {"index": 4, "key": "fri", "short": "ПТ", "label": "Пятница"},
    {"index": 5, "key": "sat", "short": "СБ", "label": "Суббота"},
    {"index": 6, "key": "sun", "short": "ВС", "label": "Воскресенье"},
]

DEFAULT_RESOURCE_SETTINGS = {
    "answer_rate": 0.95,
    "occ": 0.70,
    "ur": 0.95,
    "shrinkage_coeff": 0.90,
    "weekly_hours_per_operator": 40.0,
    "fte_rounding": "none",
    "shift_rounding": "ceil",
    "selected_direction_ids": [],
}

ROUNDING_MODES = {"none", "ceil", "floor", "round"}

DEFAULT_RESOURCE_SHIFT_TEMPLATE_LABELS = {
    1.0: [
        "20*08",
        "7*16",
        "8*17",
        "9*18",
        "10*19",
        "11*20",
        "13*22",
        "15*00",
        "17*02",
    ],
    0.75: [
        "7*13/30",
        "8*14/30",
        "9*15/30",
        "10*16/30",
        "11*17/30",
        "12*18/30",
        "13*19/30",
        "14*20/30",
        "15*21/30",
        "15/30*22",
        "16/30*23",
        "17/30*00",
        "19/30*02",
    ],
    0.5: [
        "7*11",
        "8*12",
        "9*13",
        "10*14",
        "11*15",
        "12*16",
        "13*17",
        "14*18",
        "15*19",
        "16*20",
        "17*21",
        "18*22",
        "19*23",
        "20*00",
        "22*02",
    ],
}

SHIFT_PREVIEW_HOURS = 24 * 7
SHIFT_PREVIEW_MINUTES = SHIFT_PREVIEW_HOURS * 60
FTE_ROUNDING_STEP = 0.5
RESOURCE_RATE_VALUES = (1.0, 0.75, 0.5)
WORK_DAYS_PER_OPERATOR_WEEK = 5
SHIFT_PREVIEW_GREEDY_STRATEGIES = (
    {
        "name": "balanced",
        "need_weight": 10.0,
        "over_weight": 3.2,
        "active_weight": 0.015,
        "day_deficit_weight": 0.12,
        "min_score": 0.0,
        "min_covered_need": 0.05,
        "prune_mode": "strict",
    },
    {
        "name": "coverage_first",
        "need_weight": 12.0,
        "over_weight": 1.8,
        "active_weight": 0.01,
        "day_deficit_weight": 0.18,
        "min_score": -0.2,
        "min_covered_need": 0.05,
        "prune_mode": "strict",
    },
    {
        "name": "low_over",
        "need_weight": 9.5,
        "over_weight": 5.4,
        "active_weight": 0.02,
        "day_deficit_weight": 0.08,
        "min_score": 0.0,
        "min_covered_need": 0.05,
        "prune_mode": "strict",
    },
    {
        "name": "day_focus",
        "need_weight": 10.5,
        "over_weight": 2.6,
        "active_weight": 0.015,
        "day_deficit_weight": 0.35,
        "min_score": -0.1,
        "min_covered_need": 0.05,
        "prune_mode": "strict",
    },
    {
        "name": "compact",
        "need_weight": 10.0,
        "over_weight": 3.2,
        "active_weight": 0.04,
        "day_deficit_weight": 0.12,
        "min_score": 0.0,
        "min_covered_need": 0.05,
        "prune_mode": "objective",
    },
)

HEADER_ALIASES = {
    "report_date": ["дата", "date", "день"],
    "hour": ["час"],
    "accepted_calls": ["разговор"],
    "talk_time": ["время разговора"],
    "avg_talk": ["среднее время разговора"],
    "success_wait": ["время ожидания в очереди удачные звонки"],
    "avg_success_wait": ["среднее время ожидания в очереди удачные звонки"],
    "received_calls": ["общее кол-во поступивших", "общее количество поступивших"],
    "total_time": ["общее время"],
    "greeting_abandoned": ["бросили трубку на приветствии"],
    "greeting_time": ["время на приветствии", "время на приветстви"],
    "queue_abandoned": ["абонент прервал ожидание в очереди"],
    "queue_wait": ["время ожидания в очереди"],
    "avg_lost_wait": ["среднее время ожидания в очереди неудачные звонки"],
}

REQUIRED_FIELDS = set(HEADER_ALIASES.keys()) - {"report_date"}


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


def _normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-zа-я0-9% ]+", "", text)
    return text.strip()


def _decode_csv_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _detect_dialect(text: str) -> csv.Dialect:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t")
    except Exception:
        dialect = csv.excel()
        dialect.delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        return dialect


def _parse_report_date(value: Any) -> datetime.date:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("INVALID_REPORT_DATE")


def _parse_seconds(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if 0 < number < 1:
            return int(round(number * 24 * 60 * 60))
        return int(round(number))
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return 0
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return int(round(float(raw)))
    parts = raw.split(":")
    try:
        nums = [int(float(part)) for part in parts]
    except Exception:
        return 0
    if len(nums) == 3:
        hours, minutes, seconds = nums
    elif len(nums) == 2:
        hours, minutes, seconds = 0, nums[0], nums[1]
    else:
        return 0
    return max(0, hours * 3600 + minutes * 60 + seconds)


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


def _parse_shift_template_time(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("INVALID_SHIFT_TEMPLATE_TIME")
    if "/" in raw:
        hour_raw, minute_raw = raw.split("/", 1)
    elif ":" in raw:
        hour_raw, minute_raw = raw.split(":", 1)
    else:
        hour_raw, minute_raw = raw, "0"
    try:
        hour = int(str(hour_raw).strip())
        minute = int(str(minute_raw).strip() or 0)
    except Exception as exc:
        raise ValueError("INVALID_SHIFT_TEMPLATE_TIME") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("INVALID_SHIFT_TEMPLATE_TIME")
    return hour * 60 + minute


def _format_minutes_hhmm(minutes: int) -> str:
    normalized = int(minutes) % (24 * 60)
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def _shift_template_id(rate: float, label: str, start_minute: int, end_minute: int) -> str:
    safe_label = re.sub(r"[^a-zA-Z0-9]+", "-", str(label or "").strip()).strip("-").lower()
    rate_part = str(int(round(float(rate or 0) * 100))).rjust(3, "0")
    return f"tpl-{rate_part}-{start_minute}-{end_minute}-{safe_label or 'shift'}"


def _parse_shift_template_label(label: Any) -> Dict[str, Any]:
    raw = str(label or "").strip()
    if "*" not in raw:
        raise ValueError("INVALID_SHIFT_TEMPLATE")
    start_raw, end_raw = raw.split("*", 1)
    start_minute = _parse_shift_template_time(start_raw)
    end_clock_minute = _parse_shift_template_time(end_raw)
    end_minute = end_clock_minute
    if end_minute <= start_minute:
        end_minute += 24 * 60
    duration_minutes = end_minute - start_minute
    if duration_minutes <= 0 or duration_minutes > 18 * 60:
        raise ValueError("INVALID_SHIFT_TEMPLATE_DURATION")
    return {
        "label": raw,
        "startMinute": start_minute,
        "endMinute": end_minute,
        "start": _format_minutes_hhmm(start_minute),
        "end": _format_minutes_hhmm(end_minute),
        "durationMinutes": duration_minutes,
        "overnight": end_minute > 24 * 60,
    }


def _default_break_durations_for_shift(duration_minutes: int) -> List[int]:
    duration = int(duration_minutes or 0)
    if duration >= 5 * 60 and duration < 6 * 60:
        return [15]
    if duration >= 6 * 60 and duration < 8 * 60:
        return [15, 15]
    if duration >= 8 * 60 and duration < 11 * 60:
        return [15, 30, 15]
    if duration >= 11 * 60:
        return [15, 30, 15, 15]
    return []


def _compute_default_shift_breaks(start_minute: int, end_minute: int) -> List[Dict[str, int]]:
    start_minute = int(start_minute)
    end_minute = int(end_minute)
    duration = end_minute - start_minute
    if duration <= 0:
        return []

    def snap5(value):
        return int(round(float(value) / 5.0) * 5)

    breaks = []
    durations = _default_break_durations_for_shift(duration)
    count = len(durations)
    for index, size in enumerate(durations):
        center = start_minute + (duration * ((index + 1) / (count + 1)))
        center_snapped = snap5(center)
        start = snap5(center_snapped - (int(size) / 2))
        end = start + int(size)
        start = max(start_minute, min(end_minute, start))
        end = max(start_minute, min(end_minute, end))
        if end > start:
            breaks.append({"start": int(start), "end": int(end)})
    return breaks


def _normalize_shift_template(raw: Any, fallback_rate: Optional[float] = None, index: int = 0) -> Dict[str, Any]:
    if isinstance(raw, str):
        rate = fallback_rate if fallback_rate is not None else 1.0
        parsed = _parse_shift_template_label(raw)
        enabled = True
    elif isinstance(raw, dict):
        rate = _to_float(raw.get("rate"), fallback_rate if fallback_rate is not None else 1.0)
        enabled = bool(raw.get("enabled", True))
        label = raw.get("label")
        if label:
            parsed = _parse_shift_template_label(label)
        else:
            start_raw = raw.get("start") or raw.get("startTime")
            end_raw = raw.get("end") or raw.get("endTime")
            start_minute = _parse_shift_template_time(start_raw)
            end_minute = _parse_shift_template_time(end_raw)
            if end_minute <= start_minute:
                end_minute += 24 * 60
            parsed = {
                "label": f"{_format_minutes_hhmm(start_minute)}-{_format_minutes_hhmm(end_minute)}",
                "startMinute": start_minute,
                "endMinute": end_minute,
                "start": _format_minutes_hhmm(start_minute),
                "end": _format_minutes_hhmm(end_minute),
                "durationMinutes": end_minute - start_minute,
                "overnight": end_minute > 24 * 60,
            }
    else:
        raise ValueError("INVALID_SHIFT_TEMPLATE")

    rate = _resource_rate_value(rate)
    template_id = str((raw or {}).get("id") or "").strip() if isinstance(raw, dict) else ""
    if not template_id:
        template_id = _shift_template_id(rate, parsed["label"], parsed["startMinute"], parsed["endMinute"])
    return {
        "id": template_id,
        "rate": rate,
        "label": parsed["label"],
        "start": parsed["start"],
        "end": parsed["end"],
        "startMinute": parsed["startMinute"],
        "endMinute": parsed["endMinute"],
        "durationMinutes": parsed["durationMinutes"],
        "overnight": parsed["overnight"],
        "enabled": enabled,
        "sortOrder": int((raw or {}).get("sortOrder", index)) if isinstance(raw, dict) else index,
    }


def get_resource_shift_templates() -> Dict[str, Any]:
    templates = []
    index = 0
    for rate, labels in DEFAULT_RESOURCE_SHIFT_TEMPLATE_LABELS.items():
        for label in labels:
            templates.append(_normalize_shift_template(label, fallback_rate=rate, index=index))
            index += 1
    return {
        "templates": templates,
        "rates": [
            {"rate": 1.0, "label": "1"},
            {"rate": 0.75, "label": "0.75"},
            {"rate": 0.5, "label": "0.5"},
        ],
    }


def _normalize_shift_templates(value: Any = None) -> List[Dict[str, Any]]:
    if value is None:
        return get_resource_shift_templates()["templates"]
    if not isinstance(value, list):
        raise ValueError("INVALID_SHIFT_TEMPLATES")
    result = []
    seen = set()
    for index, item in enumerate(value):
        template = _normalize_shift_template(item, index=index)
        key = template["id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(template)
    return result


def _as_settings(row: Optional[Iterable[Any]]) -> Dict[str, Any]:
    if not row:
        return dict(DEFAULT_RESOURCE_SETTINGS)
    keys = [
        "answer_rate",
        "occ",
        "ur",
        "shrinkage_coeff",
        "weekly_hours_per_operator",
        "fte_rounding",
        "shift_rounding",
        "selected_direction_ids",
    ]
    settings = dict(DEFAULT_RESOURCE_SETTINGS)
    for key, value in zip(keys, row):
        if key == "selected_direction_ids":
            settings[key] = _coerce_int_list(value)
        else:
            settings[key] = value if key.endswith("_rounding") else _to_float(value, settings[key])
    settings["fte_rounding"] = settings.get("fte_rounding") if settings.get("fte_rounding") in ROUNDING_MODES else "none"
    settings["shift_rounding"] = settings.get("shift_rounding") if settings.get("shift_rounding") in ROUNDING_MODES else "ceil"
    return settings


def _coerce_int_list(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    result = []
    seen = set()
    for item in value:
        try:
            parsed = int(item)
        except Exception:
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def _get_settings_tx(cursor) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT answer_rate, occ, ur, shrinkage_coeff,
               weekly_hours_per_operator, fte_rounding, shift_rounding,
               selected_direction_ids
        FROM resource_settings
        WHERE id = 1
        """
    )
    return _as_settings(cursor.fetchone())


def get_resource_settings(db) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        return _get_settings_tx(cursor)


def update_resource_settings(db, payload: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    current = get_resource_settings(db)
    next_settings = dict(current)
    numeric_keys = ["answer_rate", "occ", "ur", "shrinkage_coeff", "weekly_hours_per_operator"]
    for key in numeric_keys:
        if key in payload:
            next_settings[key] = max(0.0, _to_float(payload.get(key), current[key]))
    for key in ("fte_rounding", "shift_rounding"):
        if key in payload and payload.get(key) in ROUNDING_MODES:
            next_settings[key] = payload.get(key)
    if "selected_direction_ids" in payload:
        next_settings["selected_direction_ids"] = _coerce_int_list(payload.get("selected_direction_ids"))

    if next_settings["answer_rate"] > 1:
        next_settings["answer_rate"] = next_settings["answer_rate"] / 100
    for ratio_key in ("answer_rate", "occ", "ur", "shrinkage_coeff"):
        next_settings[ratio_key] = min(max(float(next_settings[ratio_key]), 0.0001), 1.0)
    next_settings["weekly_hours_per_operator"] = max(1.0, float(next_settings["weekly_hours_per_operator"]))

    with db._get_cursor() as cursor:
        if next_settings["selected_direction_ids"]:
            cursor.execute(
                """
                SELECT id
                FROM directions
                WHERE is_active = TRUE
                  AND id = ANY(%s)
                """,
                (next_settings["selected_direction_ids"],),
            )
            valid_direction_ids = {int(row[0]) for row in cursor.fetchall()}
            next_settings["selected_direction_ids"] = [
                direction_id for direction_id in next_settings["selected_direction_ids"]
                if direction_id in valid_direction_ids
            ]
        cursor.execute(
            """
            UPDATE resource_settings
            SET answer_rate = %s,
                occ = %s,
                ur = %s,
                shrinkage_coeff = %s,
                weekly_hours_per_operator = %s,
                fte_rounding = %s,
                shift_rounding = %s,
                selected_direction_ids = %s,
                updated_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (
                next_settings["answer_rate"],
                next_settings["occ"],
                next_settings["ur"],
                next_settings["shrinkage_coeff"],
                next_settings["weekly_hours_per_operator"],
                next_settings["fte_rounding"],
                next_settings["shift_rounding"],
                Json(next_settings["selected_direction_ids"]),
                user_id,
            ),
        )
    return get_resource_settings(db)


def _map_headers(headers: List[str]) -> Dict[str, str]:
    normalized_to_original = {_normalize_header(header): header for header in headers}
    mapped = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            original = normalized_to_original.get(_normalize_header(alias))
            if original is not None:
                mapped[canonical] = original
                break
    missing = sorted(REQUIRED_FIELDS - set(mapped))
    if missing:
        raise ValueError("INVALID_CSV_HEADERS:" + ",".join(missing))
    return mapped


def _empty_resource_hour(hour: int) -> Dict[str, Any]:
    return {
        "hour": hour,
        "received_calls": 0,
        "accepted_calls": 0,
        "lost_calls": 0,
        "no_answer_rate": 0,
        "talk_time_seconds": 0,
        "avg_talk_seconds": 0,
        "success_wait_seconds": 0,
        "avg_success_wait_seconds": 0,
        "total_time_seconds": 0,
        "greeting_abandoned": 0,
        "greeting_time_seconds": 0,
        "queue_abandoned": 0,
        "queue_wait_seconds": 0,
        "avg_lost_wait_seconds": 0,
        "avg_wait_seconds": 0,
        "raw_payload": {},
    }


def _parse_resource_csv_row(row: Dict[str, Any], header_map: Dict[str, str], row_number: int) -> Dict[str, Any]:
    hour = _to_int(row.get(header_map["hour"]), -1)
    if hour < 0 or hour > 23:
        raise ValueError(f"INVALID_HOUR:{row_number}")
    accepted = max(0, _to_int(row.get(header_map["accepted_calls"])))
    received = max(0, _to_int(row.get(header_map["received_calls"])))
    lost = max(0, received - accepted)
    success_wait_seconds = _parse_seconds(row.get(header_map.get("success_wait")))
    queue_wait_seconds = _parse_seconds(row.get(header_map.get("queue_wait")))
    queue_abandoned = max(0, _to_int(row.get(header_map.get("queue_abandoned"))))
    avg_wait_denominator = accepted + queue_abandoned
    avg_wait_seconds = (
        (success_wait_seconds + queue_wait_seconds) / avg_wait_denominator
        if avg_wait_denominator > 0
        else 0
    )
    return {
        "hour": hour,
        "received_calls": received,
        "accepted_calls": accepted,
        "lost_calls": lost,
        "no_answer_rate": (lost / received) if received > 0 else 0,
        "talk_time_seconds": _parse_seconds(row.get(header_map.get("talk_time"))),
        "avg_talk_seconds": _parse_seconds(row.get(header_map["avg_talk"])),
        "success_wait_seconds": success_wait_seconds,
        "avg_success_wait_seconds": _parse_seconds(row.get(header_map.get("avg_success_wait"))),
        "total_time_seconds": _parse_seconds(row.get(header_map.get("total_time"))),
        "greeting_abandoned": max(0, _to_int(row.get(header_map.get("greeting_abandoned")))),
        "greeting_time_seconds": _parse_seconds(row.get(header_map.get("greeting_time"))),
        "queue_abandoned": queue_abandoned,
        "queue_wait_seconds": queue_wait_seconds,
        "avg_lost_wait_seconds": _parse_seconds(row.get(header_map.get("avg_lost_wait"))),
        "avg_wait_seconds": avg_wait_seconds,
        "raw_payload": dict(row),
    }


def parse_resource_csv(content: bytes) -> Dict[str, Any]:
    text = _decode_csv_bytes(content)
    dialect = _detect_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [str(item or "").strip() for item in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("EMPTY_CSV")
    header_map = _map_headers(headers)
    date_header = header_map.get("report_date")
    if not date_header:
        raise ValueError("INVALID_CSV_HEADERS:report_date")
    rows_by_date_hour = defaultdict(dict)
    row_counts_by_date = defaultdict(int)
    for row_number, row in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue
        report_date = _parse_report_date(row.get(date_header))
        row_counts_by_date[report_date.isoformat()] += 1
        parsed_row = _parse_resource_csv_row(row, header_map, row_number)
        rows_by_date_hour[report_date.isoformat()][parsed_row["hour"]] = parsed_row
    if not rows_by_date_hour:
        raise ValueError("EMPTY_CSV_ROWS")
    days = []
    for report_date in sorted(rows_by_date_hour.keys()):
        rows_by_hour = rows_by_date_hour[report_date]
        source_row_count = int(row_counts_by_date.get(report_date) or len(rows_by_hour))
        for hour in range(24):
            rows_by_hour.setdefault(hour, _empty_resource_hour(hour))
        days.append(
            {
                "report_date": report_date,
                "rows": [rows_by_hour[hour] for hour in range(24)],
                "source_row_count": source_row_count,
            }
        )
    return {
        "headers": headers,
        "mapped_headers": header_map,
        "rows": days[0]["rows"],
        "days": days,
    }


def _shift_hourly_fte_tx(cursor, report_date, settings: Optional[Dict[str, Any]] = None) -> Dict[int, float]:
    report_date = _parse_report_date(report_date) if isinstance(report_date, str) else report_date
    prev_date = report_date - timedelta(days=1)
    selected_direction_ids = _coerce_int_list((settings or {}).get("selected_direction_ids"))
    direction_filter = "AND u.direction_id = ANY(%s)" if selected_direction_ids else ""
    params = [report_date, prev_date]
    if selected_direction_ids:
        params.append(selected_direction_ids)
    cursor.execute(
        f"""
        SELECT ws.id, ws.shift_date, ws.start_time, ws.end_time
        FROM work_shifts ws
        JOIN users u ON u.id = ws.operator_id
        WHERE ws.shift_date IN (%s, %s)
          AND u.role = 'operator'
          AND COALESCE(u.status, 'working') = 'working'
          {direction_filter}
        """,
        params,
    )
    shifts = cursor.fetchall()
    if not shifts:
        return {hour: 0.0 for hour in range(24)}
    shift_ids = [row[0] for row in shifts]
    breaks_by_shift = defaultdict(list)
    cursor.execute(
        """
        SELECT shift_id, start_minutes, end_minutes
        FROM shift_breaks
        WHERE shift_id = ANY(%s)
        """,
        (shift_ids,),
    )
    for shift_id, start_minutes, end_minutes in cursor.fetchall():
        breaks_by_shift[shift_id].append((int(start_minutes or 0), int(end_minutes or 0)))

    hourly = {hour: 0.0 for hour in range(24)}
    for shift_id, shift_date, start_time, end_time in shifts:
        start = start_time.hour * 60 + start_time.minute
        end = end_time.hour * 60 + end_time.minute
        if end <= start:
            end += 24 * 60
        if shift_date == prev_date:
            start -= 24 * 60
            end -= 24 * 60
        for hour in range(24):
            hour_start = hour * 60
            hour_end = hour_start + 60
            overlap = max(0, min(end, hour_end) - max(start, hour_start))
            if overlap <= 0:
                continue
            break_overlap = 0
            for break_start, break_end in breaks_by_shift.get(shift_id, []):
                if break_end <= break_start:
                    continue
                if shift_date == prev_date:
                    break_start -= 24 * 60
                    break_end -= 24 * 60
                break_overlap += max(0, min(break_end, hour_end) - max(break_start, hour_start))
            payable_minutes = max(0, overlap - break_overlap)
            hourly[hour] += payable_minutes / 60
    return hourly


def _refresh_actual_fte_for_day_tx(cursor, report_date, settings: Optional[Dict[str, Any]] = None) -> None:
    schedule_fte = _shift_hourly_fte_tx(cursor, report_date, settings)
    for hour, actual_fte in schedule_fte.items():
        cursor.execute(
            """
            UPDATE daily_resource_hours
            SET actual_fte = %s,
                fact_forecast_delta = %s - forecast_fte,
                updated_at = CURRENT_TIMESTAMP
            WHERE report_date = %s AND hour = %s
            """,
            (actual_fte, actual_fte, report_date, hour),
        )
    _refresh_daily_summary_tx(cursor, report_date)


def _refresh_all_actual_fte_tx(cursor, settings: Optional[Dict[str, Any]] = None) -> None:
    cursor.execute("SELECT report_date FROM daily_resource_summary ORDER BY report_date ASC")
    for (report_date,) in cursor.fetchall():
        _refresh_actual_fte_for_day_tx(cursor, report_date, settings)


def _current_operator_fte_tx(cursor, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    selected_direction_ids = _coerce_int_list((settings or {}).get("selected_direction_ids"))
    direction_filter = "AND u.direction_id = ANY(%s)" if selected_direction_ids else ""
    params = [selected_direction_ids] if selected_direction_ids else []
    cursor.execute(
        f"""
        SELECT COALESCE(u.rate, 1.0), COUNT(*)
        FROM users u
        WHERE u.role = 'operator'
          AND COALESCE(u.status, 'working') = 'working'
          {direction_filter}
        GROUP BY COALESCE(u.rate, 1.0)
        """,
        params,
    )
    by_rate = {
        _resource_rate_key(rate): {
            "rate": float(rate),
            "count": 0,
            "daily_shift_capacity": 0,
            "weekly_shift_capacity": 0,
        }
        for rate in RESOURCE_RATE_VALUES
    }
    active_operator_count = 0
    current_operator_fte = 0.0
    for rate_raw, count_raw in cursor.fetchall():
        rate = _resource_rate_value(rate_raw)
        rate_key = _resource_rate_key(rate)
        count = _to_int(count_raw)
        by_rate[rate_key]["count"] += count
        by_rate[rate_key]["daily_shift_capacity"] += count
        by_rate[rate_key]["weekly_shift_capacity"] += count * WORK_DAYS_PER_OPERATOR_WEEK
        active_operator_count += count
        current_operator_fte += count * rate
    rate_capacity = [by_rate[_resource_rate_key(rate)] for rate in RESOURCE_RATE_VALUES]
    return {
        "active_operator_count": active_operator_count,
        "current_operator_fte": round(current_operator_fte, 4),
        "rate_capacity": rate_capacity,
        "rate_counts": {item["rate"]: item["count"] for item in rate_capacity},
        "selected_direction_ids": selected_direction_ids,
    }


def _refresh_daily_summary_tx(cursor, report_date) -> None:
    cursor.execute(
        """
        SELECT
            COALESCE(SUM(received_calls), 0),
            COALESCE(SUM(accepted_calls), 0),
            COALESCE(SUM(lost_calls), 0),
            COALESCE(SUM(talk_time_seconds), 0),
            COALESCE(SUM(success_wait_seconds + queue_wait_seconds), 0),
            COALESCE(SUM(accepted_calls + queue_abandoned), 0),
            COALESCE(SUM(forecast_fte), 0),
            COALESCE(SUM(planned_fte), 0),
            COALESCE(SUM(actual_fte), 0),
            COALESCE(SUM(fact_forecast_delta), 0)
        FROM daily_resource_hours
        WHERE report_date = %s
        """,
        (report_date,),
    )
    row = cursor.fetchone()
    total_received = _to_int(row[0])
    total_accepted = _to_int(row[1])
    total_lost = _to_int(row[2])
    talk_time = _to_float(row[3])
    wait_time = _to_float(row[4])
    wait_count = _to_float(row[5])
    avg_talk = talk_time / total_accepted if total_accepted > 0 else 0
    avg_wait = wait_time / wait_count if wait_count > 0 else 0
    cursor.execute(
        """
        INSERT INTO daily_resource_summary (
            report_date, weekday, total_received, total_accepted, total_lost,
            no_answer_rate, avg_talk_seconds, avg_wait_seconds,
            forecast_fte_total, planned_fte_total, actual_fte_total,
            fact_forecast_delta_total, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (report_date)
        DO UPDATE SET
            weekday = EXCLUDED.weekday,
            total_received = EXCLUDED.total_received,
            total_accepted = EXCLUDED.total_accepted,
            total_lost = EXCLUDED.total_lost,
            no_answer_rate = EXCLUDED.no_answer_rate,
            avg_talk_seconds = EXCLUDED.avg_talk_seconds,
            avg_wait_seconds = EXCLUDED.avg_wait_seconds,
            forecast_fte_total = EXCLUDED.forecast_fte_total,
            planned_fte_total = EXCLUDED.planned_fte_total,
            actual_fte_total = EXCLUDED.actual_fte_total,
            fact_forecast_delta_total = EXCLUDED.fact_forecast_delta_total,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            report_date,
            report_date.weekday(),
            total_received,
            total_accepted,
            total_lost,
            (total_lost / total_received) if total_received > 0 else 0,
            avg_talk,
            avg_wait,
            _to_float(row[6]),
            _to_float(row[7]),
            _to_float(row[8]),
            _to_float(row[9]),
        ),
    )


def _build_profile_from_history_dates_tx(
    cursor,
    weekday: int,
    history_dates: List[Any],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    if not history_dates:
        hourly_profile = [
            {
                "hour": hour,
                "avg_calls": 0.0,
                "aht_seconds": 0.0,
                "distribution": 0.0,
                "workload_minutes": 0.0,
                "effective_fte_minutes": 60 * settings["occ"] * settings["ur"],
                "fte": 0.0,
                "fte_rounded": 0.0,
            }
            for hour in range(24)
        ]
        return {
            "weekday": weekday,
            "history_dates": [],
            "history_count": 0,
            "insufficient_history": True,
            "avg_daily_calls": 0.0,
            "aht_seconds": 0.0,
            "daily_fte": 0.0,
            "hourly_profile": hourly_profile,
        }

    history_dates = sorted(history_dates, reverse=True)[:2]
    cursor.execute(
        """
        SELECT
            hour,
            AVG(received_calls)::float,
            COALESCE(SUM(talk_time_seconds), 0)::float,
            COALESCE(SUM(accepted_calls), 0)::float
        FROM daily_resource_hours
        WHERE report_date = ANY(%s)
        GROUP BY hour
        ORDER BY hour
        """,
        (history_dates,),
    )
    hourly_source = {
        int(row[0]): {
            "avg_calls": _to_float(row[1]),
            "talk_time_seconds": _to_float(row[2]),
            "accepted_calls": _to_float(row[3]),
        }
        for row in cursor.fetchall()
    }
    cursor.execute(
        """
        SELECT report_date, hour, received_calls
        FROM daily_resource_hours
        WHERE report_date = ANY(%s)
        ORDER BY report_date DESC, hour
        """,
        (history_dates,),
    )
    source_calls_by_hour = defaultdict(list)
    for report_date, hour, received_calls in cursor.fetchall():
        source_calls_by_hour[int(hour)].append(
            {
                "date": report_date.isoformat(),
                "calls": _to_int(received_calls),
            }
        )
    avg_daily_calls = sum(_to_float(hourly_source.get(hour, {}).get("avg_calls")) for hour in range(24))
    total_talk_seconds = sum(item["talk_time_seconds"] for item in hourly_source.values())
    total_accepted_calls = sum(item["accepted_calls"] for item in hourly_source.values())
    profile_aht_seconds = total_talk_seconds / total_accepted_calls if total_accepted_calls > 0 else 0.0
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    hourly_profile = []
    daily_fte = 0.0
    for hour in range(24):
        source = hourly_source.get(hour, {})
        avg_calls = _to_float(source.get("avg_calls"))
        accepted_calls = _to_float(source.get("accepted_calls"))
        aht_seconds = (
            _to_float(source.get("talk_time_seconds")) / accepted_calls
            if accepted_calls > 0
            else profile_aht_seconds
        )
        distribution = avg_calls / avg_daily_calls if avg_daily_calls > 0 else 0.0
        workload_minutes = avg_calls * settings["answer_rate"] * aht_seconds / 60
        fte = workload_minutes / effective_minutes if effective_minutes > 0 else 0.0
        fte_rounded = _round_value(fte, settings["fte_rounding"])
        daily_fte += fte
        hourly_profile.append(
            {
                "hour": hour,
                "avg_calls": avg_calls,
                "aht_seconds": aht_seconds,
                "distribution": distribution,
                "workload_minutes": workload_minutes,
                "effective_fte_minutes": effective_minutes,
                "fte": fte,
                "fte_rounded": round(fte_rounded, 4),
                "source_calls": source_calls_by_hour.get(hour, []),
            }
        )
    return {
        "weekday": weekday,
        "history_dates": [item.isoformat() for item in history_dates],
        "history_count": len(history_dates),
        "insufficient_history": len(history_dates) < 2,
        "avg_daily_calls": avg_daily_calls,
        "aht_seconds": profile_aht_seconds,
        "daily_fte": daily_fte,
        "hourly_profile": hourly_profile,
    }


def _compute_profile_for_weekday_tx(cursor, weekday: int, as_of_date, settings: Dict[str, Any]) -> Dict[str, Any]:
    start_date = as_of_date - timedelta(days=13)
    cursor.execute(
        """
        SELECT report_date
        FROM daily_resource_summary
        WHERE report_date BETWEEN %s AND %s
          AND weekday = %s
        ORDER BY report_date DESC
        LIMIT 2
        """,
        (start_date, as_of_date, weekday),
    )
    history_dates = [row[0] for row in cursor.fetchall()]
    return _build_profile_from_history_dates_tx(cursor, weekday, history_dates, settings)


def _week_start_date(value):
    return value - timedelta(days=value.weekday())


def _next_week_start_date(value):
    return _week_start_date(value) + timedelta(days=7)


def _weekly_aht_from_profiles(profiles: List[Dict[str, Any]]) -> float:
    total_calls = sum(_to_float(profile.get("avg_daily_calls")) for profile in profiles)
    if total_calls <= 0:
        return 0.0
    weighted_aht = sum(
        _to_float(profile.get("avg_daily_calls")) * _to_float(profile.get("aht_seconds"))
        for profile in profiles
    )
    return weighted_aht / total_calls


def _apply_weekly_aht_to_profile(profile: Dict[str, Any], weekly_aht_seconds: float, settings: Dict[str, Any]) -> Dict[str, Any]:
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    hourly_profile = []
    daily_fte = 0.0
    for row in profile.get("hourly_profile", []):
        avg_calls = _to_float(row.get("avg_calls"))
        workload_minutes = avg_calls * settings["answer_rate"] * weekly_aht_seconds / 60
        fte = workload_minutes / effective_minutes if effective_minutes > 0 else 0.0
        fte_rounded = _round_value(fte, settings["fte_rounding"])
        daily_fte += fte
        hourly_profile.append(
            {
                **row,
                "aht_seconds": weekly_aht_seconds,
                "workload_minutes": workload_minutes,
                "effective_fte_minutes": effective_minutes,
                "fte": fte,
                "fte_rounded": round(fte_rounded, 4),
            }
        )
    return {
        **profile,
        "forecast_aht_seconds": weekly_aht_seconds,
        "daily_fte": daily_fte,
        "hourly_profile": hourly_profile,
    }


def _compute_week_forecast_profiles_tx(cursor, target_week_start, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    as_of_date = target_week_start - timedelta(days=8)
    base_profiles = [
        _compute_profile_for_weekday_tx(cursor, weekday_meta["index"], as_of_date, settings)
        for weekday_meta in WEEKDAYS_RU
    ]
    weekly_aht_seconds = _weekly_aht_from_profiles(base_profiles)
    return [
        {**WEEKDAYS_RU[index], **_apply_weekly_aht_to_profile(profile, weekly_aht_seconds, settings)}
        for index, profile in enumerate(base_profiles)
    ]


def _actual_resource_load_for_week_tx(cursor, target_week_start, settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    week_end = target_week_start + timedelta(days=6)
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    cursor.execute(
        """
        SELECT report_date, hour, received_calls, accepted_calls, lost_calls,
               talk_time_seconds, avg_talk_seconds
        FROM daily_resource_hours
        WHERE report_date BETWEEN %s AND %s
        ORDER BY report_date, hour
        """,
        (target_week_start, week_end),
    )
    by_day: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        report_date = row[0].isoformat()
        hour = int(row[1])
        received_calls = _to_int(row[2])
        accepted_calls = _to_int(row[3])
        lost_calls = _to_int(row[4])
        talk_time_seconds = _to_float(row[5])
        actual_aht_seconds = talk_time_seconds / accepted_calls if accepted_calls > 0 else 0.0
        workload_minutes = talk_time_seconds / 60
        report_fte = workload_minutes / effective_minutes if effective_minutes > 0 else 0.0
        day = by_day.setdefault(
            report_date,
            {
                "has_actual_report": True,
                "actual_received_calls": 0,
                "actual_accepted_calls": 0,
                "actual_lost_calls": 0,
                "actual_talk_time_seconds": 0.0,
                "actual_workload_minutes": 0.0,
                "actual_report_fte": 0.0,
                "actual_aht_seconds": 0.0,
                "hourly": {},
            },
        )
        day["actual_received_calls"] += received_calls
        day["actual_accepted_calls"] += accepted_calls
        day["actual_lost_calls"] += lost_calls
        day["actual_talk_time_seconds"] += talk_time_seconds
        day["actual_workload_minutes"] += workload_minutes
        day["actual_report_fte"] += report_fte
        day["hourly"][hour] = {
            "has_actual_report": True,
            "actual_received_calls": received_calls,
            "actual_accepted_calls": accepted_calls,
            "actual_lost_calls": lost_calls,
            "actual_talk_time_seconds": talk_time_seconds,
            "actual_aht_seconds": actual_aht_seconds,
            "actual_workload_minutes": workload_minutes,
            "actual_report_fte": report_fte,
        }
    for day in by_day.values():
        accepted_total = day["actual_accepted_calls"]
        day["actual_aht_seconds"] = day["actual_talk_time_seconds"] / accepted_total if accepted_total > 0 else 0.0
    return by_day


def _build_week_forecast_payload(
    target_week_start,
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
    actual_resource_by_day: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    weekly_aht_seconds = _weekly_aht_from_profiles(profiles)
    weekly_totals = _weekly_totals(profiles, settings, current_operator_fte)
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    actual_resource_by_day = actual_resource_by_day or {}
    first_history_week_start = target_week_start - timedelta(days=21)
    first_history_week_end = target_week_start - timedelta(days=15)
    second_history_week_start = target_week_start - timedelta(days=14)
    second_history_week_end = target_week_start - timedelta(days=8)
    days = []
    for profile in profiles:
        weekday = int(profile.get("weekday", 0))
        forecast_date = target_week_start + timedelta(days=weekday)
        forecast_date_iso = forecast_date.isoformat()
        actual_day = actual_resource_by_day.get(forecast_date_iso, {})
        hourly_forecast = []
        for row in profile.get("hourly_profile", []):
            actual_hour = (actual_day.get("hourly") or {}).get(int(row.get("hour", 0)), {})
            hourly_forecast.append(
                {
                    **row,
                    "forecast_calls": _to_float(row.get("avg_calls")),
                    "forecast_aht_seconds": _to_float(row.get("aht_seconds")),
                    "forecast_workload_minutes": _to_float(row.get("workload_minutes")),
                    "forecast_fte": _to_float(row.get("fte")),
                    "has_actual_report": bool(actual_hour.get("has_actual_report")),
                    "actual_received_calls": _to_int(actual_hour.get("actual_received_calls")),
                    "actual_accepted_calls": _to_int(actual_hour.get("actual_accepted_calls")),
                    "actual_lost_calls": _to_int(actual_hour.get("actual_lost_calls")),
                    "actual_talk_time_seconds": _to_float(actual_hour.get("actual_talk_time_seconds")),
                    "actual_aht_seconds": _to_float(actual_hour.get("actual_aht_seconds")),
                    "actual_workload_minutes": _to_float(actual_hour.get("actual_workload_minutes")),
                    "actual_report_fte": _to_float(actual_hour.get("actual_report_fte")),
                }
            )
        days.append(
            {
                **profile,
                "forecast_date": forecast_date_iso,
                "forecast_calls": _to_float(profile.get("avg_daily_calls")),
                "forecast_aht_seconds": weekly_aht_seconds,
                "forecast_workload_minutes": sum(_to_float(row.get("workload_minutes")) for row in profile.get("hourly_profile", [])),
                "forecast_daily_fte": _to_float(profile.get("daily_fte")),
                "operators_equivalent": _to_float(profile.get("daily_fte")) / 8,
                "has_actual_report": bool(actual_day.get("has_actual_report")),
                "actual_received_calls": _to_int(actual_day.get("actual_received_calls")),
                "actual_accepted_calls": _to_int(actual_day.get("actual_accepted_calls")),
                "actual_lost_calls": _to_int(actual_day.get("actual_lost_calls")),
                "actual_aht_seconds": _to_float(actual_day.get("actual_aht_seconds")),
                "actual_workload_minutes": _to_float(actual_day.get("actual_workload_minutes")),
                "actual_report_fte": _to_float(actual_day.get("actual_report_fte")),
                "actual_forecast_fte_delta": _to_float(actual_day.get("actual_report_fte")) - _to_float(profile.get("daily_fte")),
                "hourly_forecast": hourly_forecast,
            }
        )
    return {
        "week_start": target_week_start.isoformat(),
        "week_end": (target_week_start + timedelta(days=6)).isoformat(),
        "history_start": first_history_week_start.isoformat(),
        "history_end": second_history_week_end.isoformat(),
        "history_weeks": [
            {
                "start": first_history_week_start.isoformat(),
                "end": first_history_week_end.isoformat(),
            },
            {
                "start": second_history_week_start.isoformat(),
                "end": second_history_week_end.isoformat(),
            },
        ],
        "historyComplete": all(not bool(profile.get("insufficient_history")) for profile in profiles),
        "days": days,
        "weeklyAhtSeconds": weekly_aht_seconds,
        "answerRate": settings["answer_rate"],
        "occ": settings["occ"],
        "ur": settings["ur"],
        "shrinkage": settings["shrinkage_coeff"],
        "weeklyHours": settings["weekly_hours_per_operator"],
        "effectiveMinutes": effective_minutes,
        "weeklyFteHours": weekly_totals["weekly_fte_hours"],
        "baseOperators": weekly_totals["base_operators"],
        "operatorsWithShrinkage": weekly_totals["operators_with_shrinkage"],
        "operatorsRounded": weekly_totals["operators_rounded"],
        "currentOperatorFte": weekly_totals["current_operator_fte"],
        "operatorFteGap": weekly_totals["operator_fte_gap"],
    }


def _compute_historical_forecast_profile_for_day_tx(cursor, report_date, settings: Dict[str, Any]) -> Dict[str, Any]:
    profiles = _compute_week_forecast_profiles_tx(cursor, _week_start_date(report_date), settings)
    return profiles[report_date.weekday()]


def _apply_profile_forecast_to_day_tx(cursor, report_date, profile: Dict[str, Any]) -> None:
    hourly = {int(item["hour"]): item for item in (profile.get("hourly_profile") or [])}
    for hour in range(24):
        profile_row = hourly.get(hour, {})
        forecast_calls = _to_float(profile_row.get("avg_calls"))
        forecast_fte = _to_float(profile_row.get("fte"))
        cursor.execute(
            """
            UPDATE daily_resource_hours
            SET forecast_calls = %s,
                forecast_fte = %s,
                fact_forecast_delta = actual_fte - %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE report_date = %s AND hour = %s
            """,
            (forecast_calls, forecast_fte, forecast_fte, report_date, hour),
        )
    _refresh_daily_summary_tx(cursor, report_date)


def _refresh_historical_forecast_for_day_tx(cursor, report_date, settings: Dict[str, Any]) -> Dict[str, Any]:
    profile = _compute_historical_forecast_profile_for_day_tx(cursor, report_date, settings)
    _apply_profile_forecast_to_day_tx(cursor, report_date, profile)
    return profile


def _refresh_all_historical_forecasts_tx(cursor, settings: Dict[str, Any]) -> None:
    cursor.execute("SELECT report_date FROM daily_resource_summary ORDER BY report_date ASC")
    report_dates = [row[0] for row in cursor.fetchall()]
    for report_date in report_dates:
        _refresh_historical_forecast_for_day_tx(cursor, report_date, settings)


def _weekly_totals(
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
) -> Dict[str, Any]:
    weekly_fte_hours = sum(_to_float(profile.get("daily_fte")) for profile in profiles)
    base_operators = weekly_fte_hours / settings["weekly_hours_per_operator"] if settings["weekly_hours_per_operator"] > 0 else 0
    operators_with_shrinkage = base_operators / settings["shrinkage_coeff"] if settings["shrinkage_coeff"] > 0 else 0
    operator_fte_gap = current_operator_fte - operators_with_shrinkage
    return {
        "weekly_fte_hours": round(weekly_fte_hours, 4),
        "base_operators": round(base_operators, 4),
        "operators_with_shrinkage": round(operators_with_shrinkage, 4),
        "operators_rounded": round(_round_value(operators_with_shrinkage, settings["shift_rounding"]), 4),
        "current_operator_fte": round(current_operator_fte, 4),
        "operator_fte_gap": round(operator_fte_gap, 4),
    }


def _normalize_schedule_rate_capacity(operator_capacity: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_rate = {
        _resource_rate_key(rate): {
            "rate": float(rate),
            "count": 0,
            "daily_shift_capacity": 0,
            "weekly_shift_capacity": 0,
        }
        for rate in RESOURCE_RATE_VALUES
    }
    for item in (operator_capacity or {}).get("rate_capacity") or []:
        rate_key = _resource_rate_key(item.get("rate"))
        count = max(0, _to_int(item.get("count")))
        weekly_capacity = max(0, _to_int(item.get("weekly_shift_capacity"), count * WORK_DAYS_PER_OPERATOR_WEEK))
        daily_capacity = max(0, _to_int(item.get("daily_shift_capacity"), count))
        by_rate[rate_key] = {
            "rate": _resource_rate_value(item.get("rate")),
            "count": count,
            "daily_shift_capacity": daily_capacity,
            "weekly_shift_capacity": weekly_capacity,
        }
    return by_rate


def _shift_preview_usage_by_rate(selected: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    usage = {
        _resource_rate_key(rate): {
            "rate": float(rate),
            "weekly_shifts": 0,
            "daily_shifts": [0 for _ in range(7)],
        }
        for rate in RESOURCE_RATE_VALUES
    }
    for item in selected:
        rate_key = _resource_rate_key((item.get("template") or {}).get("rate"))
        day_index = int(item.get("dayIndex") or 0)
        if rate_key not in usage or day_index < 0 or day_index >= 7:
            continue
        usage[rate_key]["weekly_shifts"] += 1
        usage[rate_key]["daily_shifts"][day_index] += 1
    return usage


def _shift_preview_presence_vector(day_index: int, template: Dict[str, Any]) -> List[float]:
    vector = [0.0 for _ in range(SHIFT_PREVIEW_HOURS)]
    day_start = int(day_index) * 24 * 60
    start_abs = day_start + int(template.get("startMinute") or 0)
    end_abs = day_start + int(template.get("endMinute") or 0)
    for hour_index in range(SHIFT_PREVIEW_HOURS):
        hour_start = hour_index * 60
        hour_end = hour_start + 60
        overlap = max(0, min(end_abs, hour_end) - max(start_abs, hour_start))
        if overlap > 0:
            vector[hour_index] = round(overlap / 60, 4)
    return vector


def _shift_preview_vector(day_index: int, template: Dict[str, Any]) -> List[float]:
    vector = [0.0 for _ in range(SHIFT_PREVIEW_HOURS)]
    day_start = int(day_index) * 24 * 60
    start_abs = day_start + int(template.get("startMinute") or 0)
    end_abs = day_start + int(template.get("endMinute") or 0)
    for hour_index in range(SHIFT_PREVIEW_HOURS):
        hour_start = hour_index * 60
        hour_end = hour_start + 60
        overlap = max(0, min(end_abs, hour_end) - max(start_abs, hour_start))
        if overlap <= 0:
            continue
        vector[hour_index] = round(overlap / 60, 4)
    return vector


def _shift_preview_score(
    target: List[float],
    coverage: List[float],
    vector: List[float],
    strategy: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    strategy = strategy or SHIFT_PREVIEW_GREEDY_STRATEGIES[0]
    covered_need = 0.0
    weighted_need = 0.0
    added_over = 0.0
    active = 0.0
    for index, amount in enumerate(vector):
        amount = float(amount or 0)
        if amount <= 0:
            continue
        need = float(target[index] or 0)
        current = float(coverage[index] or 0)
        before_deficit = max(0.0, need - current)
        after_deficit = max(0.0, need - current - amount)
        closed = before_deficit - after_deficit
        current_over = max(0.0, current - need)
        next_over = max(0.0, current + amount - need)
        added_over += max(0.0, next_over - current_over)
        covered_need += closed
        weighted_need += closed * (1.0 + min(need, 10.0) * 0.04)
        active += amount
    score = (
        weighted_need * float(strategy.get("need_weight", 10.0))
        - added_over * float(strategy.get("over_weight", 3.2))
        - active * float(strategy.get("active_weight", 0.015))
    )
    return {
        "score": score,
        "covered_need": covered_need,
        "added_over": added_over,
        "active": active,
    }


def _shift_preview_day_deficit(target: List[float], coverage: List[float], day_index: int) -> float:
    start = max(0, int(day_index) * 24)
    end = min(len(target), start + 24)
    return sum(max(0.0, float(target[index] or 0) - float(coverage[index] or 0)) for index in range(start, end))


def _shift_preview_totals(
    target: List[float],
    coverage: List[float],
    raw_target: Optional[List[float]] = None,
) -> Dict[str, Any]:
    effective_raw_target = raw_target if raw_target is not None else target
    total_needed = sum(float(item or 0) for item in target)
    real_needed = sum(float(item or 0) for item in effective_raw_target)
    real_coverage = sum(float(item or 0) for item in coverage)
    rounded_coverage = [_round_fte_to_half(float(item or 0)) for item in coverage]
    rounded_coverage_total = sum(float(item or 0) for item in rounded_coverage)
    covered_need = sum(min(float(rounded_coverage[index] or 0), float(target[index] or 0)) for index in range(len(target)))
    real_covered_need = sum(
        min(
            float(coverage[index] or 0),
            float(effective_raw_target[index] or 0),
        )
        for index in range(len(target))
    )
    deficit = sum(max(0.0, float(target[index] or 0) - float(rounded_coverage[index] or 0)) for index in range(len(target)))
    over = sum(max(0.0, float(rounded_coverage[index] or 0) - float(target[index] or 0)) for index in range(len(target)))
    return {
        "neededFteHours": round(total_needed, 4),
        "roundedNeededFteHours": round(total_needed, 4),
        "realNeededFteHours": round(real_needed, 4),
        "coveredFteHours": round(covered_need, 4),
        "roundedCoveredFteHours": round(rounded_coverage_total, 4),
        "realCoveredFteHours": round(real_coverage, 4),
        "realCoveredNeedFteHours": round(real_covered_need, 4),
        "deficitFteHours": round(deficit, 4),
        "overFteHours": round(over, 4),
        "coveragePercent": round((covered_need / total_needed * 100) if total_needed > 0 else 0.0, 2),
        "realCoveragePercent": round((real_covered_need / real_needed * 100) if real_needed > 0 else 0.0, 2),
    }


def _shift_preview_quality_score(totals: Dict[str, Any], selected_count: int = 0) -> float:
    deficit = float(totals.get("deficitFteHours") or 0)
    over = float(totals.get("overFteHours") or 0)
    real_coverage = float(totals.get("realCoveragePercent") or 0)
    return deficit * 100.0 + over * 3.0 + max(0, int(selected_count or 0)) * 0.025 - real_coverage * 0.02


def _shift_preview_prune_selected(
    target: List[float],
    coverage: List[float],
    selected: List[Dict[str, Any]],
    prune_mode: str = "strict",
) -> Dict[str, Any]:
    selected = list(selected)
    coverage = list(coverage)
    while True:
        current_totals = _shift_preview_totals(target, coverage)
        current_quality = _shift_preview_quality_score(current_totals, len(selected))
        removed = False
        for item_index, selected_item in enumerate(list(selected)):
            vector = selected_item["vector"]
            coverage_without = [
                round(float(coverage[index] or 0) - float(vector[index] or 0), 4)
                for index in range(SHIFT_PREVIEW_HOURS)
            ]
            next_totals = _shift_preview_totals(target, coverage_without)
            if prune_mode == "objective":
                next_quality = _shift_preview_quality_score(next_totals, len(selected) - 1)
                should_remove = next_quality + 0.001 < current_quality
            else:
                should_remove = (
                    next_totals["deficitFteHours"] <= current_totals["deficitFteHours"] + 0.001
                    and next_totals["overFteHours"] + 0.001 < current_totals["overFteHours"]
                )
            if not should_remove:
                continue
            selected.pop(item_index)
            coverage = coverage_without
            removed = True
            break
        if not removed:
            break
    return {
        "selected": selected,
        "coverage": coverage,
        "totals": _shift_preview_totals(target, coverage),
    }


def _run_shift_preview_greedy_strategy(
    target: List[float],
    candidates: List[Dict[str, Any]],
    rate_capacity: Dict[str, Dict[str, Any]],
    strategy: Dict[str, Any],
) -> Dict[str, Any]:
    coverage = [0.0 for _ in range(SHIFT_PREVIEW_HOURS)]
    selected = []
    weekly_usage = defaultdict(int)
    daily_usage = defaultdict(lambda: defaultdict(int))
    hourly_usage = {
        _resource_rate_key(rate): [0.0 for _ in range(SHIFT_PREVIEW_HOURS)]
        for rate in RESOURCE_RATE_VALUES
    }
    total_weekly_capacity = sum(int(item.get("weekly_shift_capacity") or 0) for item in rate_capacity.values())
    target_based_limit = max(0, min(800, int(sum(target) / 3) + 80))
    max_shifts = min(target_based_limit, total_weekly_capacity)
    min_covered_need = float(strategy.get("min_covered_need", 0.05))
    min_score = float(strategy.get("min_score", 0.0))
    day_deficit_weight = float(strategy.get("day_deficit_weight", 0.12))

    for _ in range(max_shifts):
        best = None
        best_score = None
        best_rank = None
        for candidate in candidates:
            rate_key = candidate["rateKey"]
            day_index = int(candidate["dayIndex"])
            capacity = rate_capacity.get(rate_key) or {}
            if weekly_usage[rate_key] >= int(capacity.get("weekly_shift_capacity") or 0):
                continue
            if daily_usage[day_index][rate_key] >= int(capacity.get("daily_shift_capacity") or 0):
                continue
            active_capacity = int(capacity.get("daily_shift_capacity") or 0)
            if active_capacity <= 0:
                continue
            if any(
                float(hourly_usage[rate_key][index] or 0) + float(amount or 0) > active_capacity + 0.001
                for index, amount in enumerate(candidate.get("presenceVector") or [])
            ):
                continue

            stats = _shift_preview_score(target, coverage, candidate["vector"], strategy)
            if stats["covered_need"] <= min_covered_need:
                continue
            day_deficit = _shift_preview_day_deficit(target, coverage, day_index)
            stats = {
                **stats,
                "day_deficit": round(day_deficit, 4),
                "score": stats["score"] + min(day_deficit, 120.0) * day_deficit_weight,
            }
            if stats["score"] <= min_score:
                continue
            rank = (
                round(float(stats["score"] or 0), 6),
                round(float(stats["covered_need"] or 0), 6),
                -round(float(stats["added_over"] or 0), 6),
                -round(float(stats["active"] or 0), 6),
            )
            if best_rank is None or rank > best_rank:
                best = candidate
                best_score = stats
                best_rank = rank
        if best is None:
            break
        for index, amount in enumerate(best["vector"]):
            coverage[index] = round(float(coverage[index] or 0) + float(amount or 0), 4)
        selected.append({
            "dayIndex": best["dayIndex"],
            "template": best["template"],
            "vector": list(best["vector"]),
            "score": best_score,
        })
        best_rate_key = best["rateKey"]
        weekly_usage[best_rate_key] += 1
        daily_usage[int(best["dayIndex"])][best_rate_key] += 1
        for index, amount in enumerate(best.get("presenceVector") or []):
            hourly_usage[best_rate_key][index] = round(
                float(hourly_usage[best_rate_key][index] or 0) + float(amount or 0),
                4,
            )
        if sum(max(0.0, target[index] - coverage[index]) for index in range(SHIFT_PREVIEW_HOURS)) <= 0.01:
            break

    pruned = _shift_preview_prune_selected(
        target,
        coverage,
        selected,
        prune_mode=str(strategy.get("prune_mode") or "strict"),
    )
    totals = pruned["totals"]
    selected = pruned["selected"]
    return {
        "method": str(strategy.get("name") or "greedy"),
        "selected": selected,
        "coverage": pruned["coverage"],
        "totals": totals,
        "quality": _shift_preview_quality_score(totals, len(selected)),
    }


def _generate_schedule_preview_from_forecast(
    forecast_payload: Dict[str, Any],
    templates: List[Dict[str, Any]],
    operator_capacity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    days = forecast_payload.get("days") or []
    target = [0.0 for _ in range(SHIFT_PREVIEW_HOURS)]
    raw_target = [0.0 for _ in range(SHIFT_PREVIEW_HOURS)]
    for day_index, day in enumerate(days[:7]):
        for row in day.get("hourly_forecast") or []:
            hour = _to_int(row.get("hour"), -1)
            if hour < 0 or hour > 23:
                continue
            absolute_hour = day_index * 24 + hour
            raw_value = max(0.0, _to_float(row.get("forecast_fte")))
            raw_target[absolute_hour] = round(raw_value, 4)
            target[absolute_hour] = float(_round_fte_to_half(raw_value))

    enabled_templates = [item for item in templates if item.get("enabled", True)]
    rate_capacity = _normalize_schedule_rate_capacity(operator_capacity)

    candidates = []
    for day_index in range(min(7, len(days))):
        for template in enabled_templates:
            rate_key = _resource_rate_key(template.get("rate"))
            if rate_capacity.get(rate_key, {}).get("weekly_shift_capacity", 0) <= 0:
                continue
            template_with_breaks = {
                **template,
                "breaks": _compute_default_shift_breaks(
                    int(template.get("startMinute") or 0),
                    int(template.get("endMinute") or 0),
                ),
            }
            vector = _shift_preview_vector(day_index, template_with_breaks)
            if sum(vector) <= 0:
                continue
            candidates.append({
                "dayIndex": day_index,
                "template": template_with_breaks,
                "rateKey": rate_key,
                "vector": vector,
                "presenceVector": _shift_preview_presence_vector(day_index, template_with_breaks),
            })

    strategy_results = [
        _run_shift_preview_greedy_strategy(target, candidates, rate_capacity, strategy)
        for strategy in SHIFT_PREVIEW_GREEDY_STRATEGIES
    ]
    best_result = min(
        strategy_results,
        key=lambda item: (
            round(float(item.get("quality") or 0), 6),
            round(float((item.get("totals") or {}).get("deficitFteHours") or 0), 4),
            round(float((item.get("totals") or {}).get("overFteHours") or 0), 4),
            len(item.get("selected") or []),
        ),
    )
    coverage = best_result["coverage"]
    selected = best_result["selected"]

    final_usage = _shift_preview_usage_by_rate(selected)
    capacity_summary = []
    for rate in RESOURCE_RATE_VALUES:
        rate_key = _resource_rate_key(rate)
        capacity = rate_capacity[rate_key]
        usage = final_usage[rate_key]
        weekly_capacity = int(capacity.get("weekly_shift_capacity") or 0)
        daily_capacity = int(capacity.get("daily_shift_capacity") or 0)
        capacity_summary.append({
            "rate": float(rate),
            "count": int(capacity.get("count") or 0),
            "dailyShiftCapacity": daily_capacity,
            "weeklyShiftCapacity": weekly_capacity,
            "weeklyShiftsUsed": int(usage.get("weekly_shifts") or 0),
            "weeklyShiftsRemaining": max(0, weekly_capacity - int(usage.get("weekly_shifts") or 0)),
            "dailyShiftsUsed": usage.get("daily_shifts") or [0 for _ in range(7)],
        })

    shifts_by_day = defaultdict(list)
    for index, selected_item in enumerate(selected, start=1):
        template = selected_item["template"]
        day_index = int(selected_item["dayIndex"])
        shift = {
            "id": f"gen-{day_index}-{index}",
            "templateId": template["id"],
            "rate": template["rate"],
            "label": template["label"],
            "start": template["start"],
            "end": template["end"],
            "startMinute": int(template["startMinute"]),
            "endMinute": int(template["endMinute"]),
            "durationMinutes": int(template["durationMinutes"]),
            "overnight": bool(template.get("overnight")),
            "breaks": template.get("breaks") or [],
        }
        shifts_by_day[day_index].append(shift)

    preview_days = []
    for day_index, day in enumerate(days[:7]):
        coverage_rows = []
        for hour in range(24):
            absolute_hour = day_index * 24 + hour
            needed = float(target[absolute_hour] or 0)
            covered = float(coverage[absolute_hour] or 0)
            covered_rounded = _round_fte_to_half(covered)
            coverage_rows.append({
                "hour": hour,
                "needed": round(needed, 4),
                "rawNeeded": round(float(raw_target[absolute_hour] or 0), 4),
                "realNeeded": round(float(raw_target[absolute_hour] or 0), 4),
                "covered": round(covered, 4),
                "coveredRounded": round(covered_rounded, 4),
                "deficit": round(max(0.0, needed - covered_rounded), 4),
                "over": round(max(0.0, covered_rounded - needed), 4),
            })
        day_target = target[day_index * 24:(day_index + 1) * 24]
        day_raw_target = raw_target[day_index * 24:(day_index + 1) * 24]
        day_coverage = coverage[day_index * 24:(day_index + 1) * 24]
        preview_days.append({
            "date": day.get("forecast_date"),
            "weekday": day.get("weekday"),
            "short": day.get("short"),
            "label": day.get("label"),
            "coverage": coverage_rows,
            "shifts": shifts_by_day.get(day_index, []),
            "stats": _shift_preview_totals(day_target, day_coverage, day_raw_target),
        })

    return {
        "week_start": forecast_payload.get("week_start"),
        "week_end": forecast_payload.get("week_end"),
        "rounding": "math_half",
        "rounding_step": FTE_ROUNDING_STEP,
        "templates": templates,
        "capacity": {
            "workDaysPerOperatorWeek": WORK_DAYS_PER_OPERATOR_WEEK,
            "rates": capacity_summary,
            "activeOperatorCount": _to_int((operator_capacity or {}).get("active_operator_count")),
            "currentOperatorFte": _to_float((operator_capacity or {}).get("current_operator_fte")),
            "selectedDirectionIds": (operator_capacity or {}).get("selected_direction_ids") or [],
        },
        "days": preview_days,
        "summary": _shift_preview_totals(target, coverage, raw_target),
        "generation": {
            "method": best_result.get("method"),
            "strategiesTried": len(strategy_results),
            "qualityScore": round(float(best_result.get("quality") or 0), 4),
            "strategySummaries": [
                {
                    "method": item.get("method"),
                    "shifts": len(item.get("selected") or []),
                    "deficitFteHours": (item.get("totals") or {}).get("deficitFteHours"),
                    "overFteHours": (item.get("totals") or {}).get("overFteHours"),
                    "qualityScore": round(float(item.get("quality") or 0), 4),
                }
                for item in strategy_results
            ],
        },
    }


def build_resource_schedule_preview(db, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    week_start_value = payload.get("week_start") or payload.get("weekStart")
    if week_start_value:
        target_week_start = _week_start_date(_parse_report_date(week_start_value))
    else:
        target_week_start = _next_week_start_date(datetime.now().date())
    templates = _normalize_shift_templates(payload.get("templates") if "templates" in payload else None)

    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        profiles = _compute_week_forecast_profiles_tx(cursor, target_week_start, settings)
        operator_capacity = _current_operator_fte_tx(cursor, settings)
        current_fte = operator_capacity.get("current_operator_fte", 0.0)
        forecast_payload = _build_week_forecast_payload(
            target_week_start,
            profiles,
            settings,
            current_operator_fte=current_fte,
        )
    return _generate_schedule_preview_from_forecast(forecast_payload, templates, operator_capacity)


def import_resource_csv(db, content: bytes, filename: str, user_id: Optional[int]) -> Dict[str, Any]:
    parsed = parse_resource_csv(content)
    content_hash = hashlib.sha256(content).hexdigest()
    uploaded_dates = []
    upload_ids = []

    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        for day in parsed["days"]:
            report_date = _parse_report_date(day["report_date"])
            uploaded_dates.append(report_date)
            schedule_fte = _shift_hourly_fte_tx(cursor, report_date, settings)
            cursor.execute(
                """
                INSERT INTO raw_resource_uploads (
                    report_date, filename, content_sha256, headers, row_count,
                    uploaded_by, uploaded_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (report_date)
                DO UPDATE SET
                    filename = EXCLUDED.filename,
                    content_sha256 = EXCLUDED.content_sha256,
                    headers = EXCLUDED.headers,
                    row_count = EXCLUDED.row_count,
                    uploaded_by = EXCLUDED.uploaded_by,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (
                    report_date,
                    filename,
                    content_hash,
                    Json(parsed["headers"]),
                    int(day.get("source_row_count") or len(day["rows"])),
                    user_id,
                ),
            )
            upload_ids.append(cursor.fetchone()[0])
            values = []
            for row in day["rows"]:
                hour = int(row["hour"])
                values.append(
                    (
                        report_date,
                        hour,
                        row["received_calls"],
                        row["accepted_calls"],
                        row["lost_calls"],
                        row["no_answer_rate"],
                        row["talk_time_seconds"],
                        row["avg_talk_seconds"],
                        row["success_wait_seconds"],
                        row["avg_success_wait_seconds"],
                        row["total_time_seconds"],
                        row["greeting_abandoned"],
                        row["greeting_time_seconds"],
                        row["queue_abandoned"],
                        row["queue_wait_seconds"],
                        row["avg_lost_wait_seconds"],
                        row["avg_wait_seconds"],
                        float(schedule_fte.get(hour, 0.0)),
                        Json(row["raw_payload"]),
                    )
                )
            execute_values(
                cursor,
                """
                INSERT INTO daily_resource_hours (
                    report_date, hour, received_calls, accepted_calls, lost_calls,
                    no_answer_rate, talk_time_seconds, avg_talk_seconds,
                    success_wait_seconds, avg_success_wait_seconds, total_time_seconds,
                    greeting_abandoned, greeting_time_seconds, queue_abandoned,
                    queue_wait_seconds, avg_lost_wait_seconds, avg_wait_seconds,
                    actual_fte, raw_payload
                )
                VALUES %s
                ON CONFLICT (report_date, hour)
                DO UPDATE SET
                    received_calls = EXCLUDED.received_calls,
                    accepted_calls = EXCLUDED.accepted_calls,
                    lost_calls = EXCLUDED.lost_calls,
                    no_answer_rate = EXCLUDED.no_answer_rate,
                    talk_time_seconds = EXCLUDED.talk_time_seconds,
                    avg_talk_seconds = EXCLUDED.avg_talk_seconds,
                    success_wait_seconds = EXCLUDED.success_wait_seconds,
                    avg_success_wait_seconds = EXCLUDED.avg_success_wait_seconds,
                    total_time_seconds = EXCLUDED.total_time_seconds,
                    greeting_abandoned = EXCLUDED.greeting_abandoned,
                    greeting_time_seconds = EXCLUDED.greeting_time_seconds,
                    queue_abandoned = EXCLUDED.queue_abandoned,
                    queue_wait_seconds = EXCLUDED.queue_wait_seconds,
                    avg_lost_wait_seconds = EXCLUDED.avg_lost_wait_seconds,
                    avg_wait_seconds = EXCLUDED.avg_wait_seconds,
                    actual_fte = EXCLUDED.actual_fte,
                    raw_payload = EXCLUDED.raw_payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                values,
            )
            _refresh_daily_summary_tx(cursor, report_date)
        _refresh_all_historical_forecasts_tx(cursor, settings)
    primary_report_date = max(uploaded_dates)
    day_payload = get_resource_day(db, primary_report_date.isoformat())
    return {
        "upload_id": upload_ids[-1] if upload_ids else None,
        "upload_ids": upload_ids,
        "report_date": primary_report_date.isoformat(),
        "uploaded_dates": [report_date.isoformat() for report_date in uploaded_dates],
        "uploaded_days_count": len(uploaded_dates),
        "mapped_headers": parsed["mapped_headers"],
        "day": day_payload,
    }


def recalculate_resource_forecast(db, as_of_date_value: Optional[str] = None) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        if as_of_date_value:
            as_of_date = _parse_report_date(as_of_date_value)
        else:
            cursor.execute("SELECT CURRENT_DATE")
            as_of_date = cursor.fetchone()[0]
        _refresh_all_actual_fte_tx(cursor, settings)
        _refresh_all_historical_forecasts_tx(cursor, settings)
    return get_resource_overview(db, as_of_date_value=as_of_date.isoformat())


def _fetch_latest_profiles_tx(cursor, as_of_date, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _compute_week_forecast_profiles_tx(cursor, _next_week_start_date(as_of_date), settings)


def _resource_directions_tx(cursor) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, name
        FROM directions
        WHERE is_active = TRUE
        ORDER BY name
        """
    )
    return [{"id": int(row[0]), "name": row[1]} for row in cursor.fetchall()]


def get_resource_overview(
    db,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    as_of_date_value: Optional[str] = None,
    forecast_week_start_value: Optional[str] = None,
) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        if as_of_date_value:
            as_of_date = _parse_report_date(as_of_date_value)
        else:
            cursor.execute("SELECT CURRENT_DATE")
            as_of_date = cursor.fetchone()[0]
        if forecast_week_start_value:
            next_week_start = _week_start_date(_parse_report_date(forecast_week_start_value))
            profiles = _compute_week_forecast_profiles_tx(cursor, next_week_start, settings)
        else:
            next_week_start = _next_week_start_date(as_of_date)
            profiles = _fetch_latest_profiles_tx(cursor, as_of_date, settings)
        operator_capacity = _current_operator_fte_tx(cursor, settings)
        current_operator_fte = operator_capacity["current_operator_fte"]
        actual_resource_by_day = _actual_resource_load_for_week_tx(cursor, next_week_start, settings)
        next_week_forecast = _build_week_forecast_payload(
            next_week_start,
            profiles,
            settings,
            current_operator_fte,
            actual_resource_by_day,
        )
        directions = _resource_directions_tx(cursor)

        where = []
        params = []
        if date_from:
            where.append("s.report_date >= %s")
            params.append(_parse_report_date(date_from))
        if date_to:
            where.append("s.report_date <= %s")
            params.append(_parse_report_date(date_to))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        effective_minutes = 60 * settings["occ"] * settings["ur"]
        cursor.execute(
            f"""
            SELECT s.report_date, s.weekday, s.total_received, s.total_accepted,
                   s.total_lost, s.no_answer_rate, s.forecast_fte_total,
                   s.planned_fte_total, s.actual_fte_total,
                   s.fact_forecast_delta_total, u.filename, u.updated_at,
                   COALESCE(h.actual_talk_time_seconds, 0),
                   COALESCE(h.forecast_calls_total, 0)
            FROM daily_resource_summary s
            LEFT JOIN raw_resource_uploads u ON u.report_date = s.report_date
            LEFT JOIN (
                SELECT report_date,
                       SUM(talk_time_seconds) AS actual_talk_time_seconds,
                       SUM(forecast_calls) AS forecast_calls_total
                FROM daily_resource_hours
                GROUP BY report_date
            ) h ON h.report_date = s.report_date
            {where_sql}
            ORDER BY s.report_date DESC
            LIMIT 120
            """,
            params,
        )
        history = [
            {
                "report_date": row[0].isoformat(),
                "weekday": int(row[1]),
                "weekday_short": WEEKDAYS_RU[int(row[1])]["short"],
                "total_received": _to_int(row[2]),
                "total_accepted": _to_int(row[3]),
                "total_lost": _to_int(row[4]),
                "no_answer_rate": _to_float(row[5]),
                "forecast_fte_total": _to_float(row[6]),
                "planned_fte_total": _to_float(row[7]),
                "actual_fte_total": _to_float(row[8]),
                "fact_forecast_delta_total": _to_float(row[9]),
                "filename": row[10],
                "updated_at": row[11].isoformat() if row[11] else None,
                "actual_report_fte_total": (_to_float(row[12]) / 60 / effective_minutes) if effective_minutes > 0 else 0.0,
                "actual_report_workload_minutes": _to_float(row[12]) / 60,
                "forecast_calls_total": _to_float(row[13]),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute("SELECT report_date FROM daily_resource_summary ORDER BY report_date ASC")
        loaded_report_dates = [row[0].isoformat() for row in cursor.fetchall()]

    return {
        "settings": _json_safe(settings),
        "as_of_date": as_of_date.isoformat(),
        "directions": directions,
        "next_week_forecast": _json_safe(next_week_forecast),
        "loaded_report_dates": loaded_report_dates,
        "history": history,
    }


def get_resource_day(db, report_date_value: str) -> Dict[str, Any]:
    report_date = _parse_report_date(report_date_value)
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        _refresh_actual_fte_for_day_tx(cursor, report_date, settings)
        _refresh_historical_forecast_for_day_tx(cursor, report_date, settings)
        cursor.execute(
            """
            SELECT report_date, weekday, total_received, total_accepted, total_lost,
                   no_answer_rate, avg_talk_seconds, avg_wait_seconds,
                   forecast_fte_total, planned_fte_total, actual_fte_total,
                   fact_forecast_delta_total
            FROM daily_resource_summary
            WHERE report_date = %s
            """,
            (report_date,),
        )
        summary_row = cursor.fetchone()
        if not summary_row:
            raise ValueError("DAY_NOT_FOUND")
        cursor.execute(
            """
            SELECT hour, received_calls, accepted_calls, lost_calls, no_answer_rate,
                   avg_talk_seconds, avg_wait_seconds, forecast_calls, forecast_fte,
                   planned_fte, actual_fte, fact_forecast_delta, comments,
                   greeting_abandoned, queue_abandoned
            FROM daily_resource_hours
            WHERE report_date = %s
            ORDER BY hour
            """,
            (report_date,),
        )
        hours = [
            {
                "hour": int(row[0]),
                "hour_label": f"{int(row[0]):02d}:00",
                "received_calls": _to_int(row[1]),
                "accepted_calls": _to_int(row[2]),
                "lost_calls": _to_int(row[3]),
                "no_answer_rate": _to_float(row[4]),
                "avg_talk_seconds": _to_float(row[5]),
                "avg_wait_seconds": _to_float(row[6]),
                "forecast_calls": _to_float(row[7]),
                "forecast_fte": _to_float(row[8]),
                "planned_fte": _to_float(row[9]),
                "actual_fte": _to_float(row[10]),
                "fact_forecast_delta": _to_float(row[11]),
                "comments": row[12] or "",
                "greeting_abandoned": _to_int(row[13]),
                "queue_abandoned": _to_int(row[14]),
            }
            for row in cursor.fetchall()
        ]
    weekday = int(summary_row[1])
    return {
        "summary": {
            "report_date": summary_row[0].isoformat(),
            "weekday": weekday,
            "weekday_short": WEEKDAYS_RU[weekday]["short"],
            "weekday_label": WEEKDAYS_RU[weekday]["label"],
            "total_received": _to_int(summary_row[2]),
            "total_accepted": _to_int(summary_row[3]),
            "total_lost": _to_int(summary_row[4]),
            "no_answer_rate": _to_float(summary_row[5]),
            "avg_talk_seconds": _to_float(summary_row[6]),
            "avg_wait_seconds": _to_float(summary_row[7]),
            "forecast_fte_total": _to_float(summary_row[8]),
            "planned_fte_total": _to_float(summary_row[9]),
            "actual_fte_total": _to_float(summary_row[10]),
            "fact_forecast_delta_total": _to_float(summary_row[11]),
        },
        "hours": hours,
    }
