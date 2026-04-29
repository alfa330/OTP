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

HEADER_ALIASES = {
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

REQUIRED_FIELDS = set(HEADER_ALIASES.keys())


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


def parse_resource_csv(content: bytes) -> Dict[str, Any]:
    text = _decode_csv_bytes(content)
    dialect = _detect_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [str(item or "").strip() for item in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("EMPTY_CSV")
    header_map = _map_headers(headers)
    rows_by_hour = {}
    for row_number, row in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue
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
        rows_by_hour[hour] = {
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
            "raw_payload": row,
        }
    if not rows_by_hour:
        raise ValueError("EMPTY_CSV_ROWS")
    for hour in range(24):
        rows_by_hour.setdefault(
            hour,
            {
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
            },
        )
    return {
        "headers": headers,
        "mapped_headers": header_map,
        "rows": [rows_by_hour[hour] for hour in range(24)],
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
        SELECT COUNT(*), COALESCE(SUM(COALESCE(u.rate, 1.0)), 0)
        FROM users u
        WHERE u.role = 'operator'
          AND COALESCE(u.status, 'working') = 'working'
          {direction_filter}
        """,
        params,
    )
    row = cursor.fetchone() or (0, 0)
    return {
        "active_operator_count": _to_int(row[0]),
        "current_operator_fte": _to_float(row[1]),
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


def _build_week_forecast_payload(
    target_week_start,
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
) -> Dict[str, Any]:
    weekly_aht_seconds = _weekly_aht_from_profiles(profiles)
    weekly_totals = _weekly_totals(profiles, settings, current_operator_fte)
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    days = []
    for profile in profiles:
        weekday = int(profile.get("weekday", 0))
        hourly_forecast = []
        for row in profile.get("hourly_profile", []):
            hourly_forecast.append(
                {
                    **row,
                    "forecast_calls": _to_float(row.get("avg_calls")),
                    "forecast_aht_seconds": _to_float(row.get("aht_seconds")),
                    "forecast_workload_minutes": _to_float(row.get("workload_minutes")),
                    "forecast_fte": _to_float(row.get("fte")),
                }
            )
        days.append(
            {
                **profile,
                "forecast_date": (target_week_start + timedelta(days=weekday)).isoformat(),
                "forecast_calls": _to_float(profile.get("avg_daily_calls")),
                "forecast_aht_seconds": weekly_aht_seconds,
                "forecast_workload_minutes": sum(_to_float(row.get("workload_minutes")) for row in profile.get("hourly_profile", [])),
                "forecast_daily_fte": _to_float(profile.get("daily_fte")),
                "operators_equivalent": _to_float(profile.get("daily_fte")) / 8,
                "hourly_forecast": hourly_forecast,
            }
        )
    return {
        "week_start": target_week_start.isoformat(),
        "week_end": (target_week_start + timedelta(days=6)).isoformat(),
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


def _upsert_profiles_tx(cursor, as_of_date, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    profiles = []
    for weekday_meta in WEEKDAYS_RU:
        profile = _compute_profile_for_weekday_tx(cursor, weekday_meta["index"], as_of_date, settings)
        profiles.append({**weekday_meta, **profile})
        cursor.execute(
            """
            INSERT INTO weekday_resource_profiles (
                as_of_date, weekday, history_dates, history_count,
                insufficient_history, avg_daily_calls, daily_fte,
                hourly_profile, settings_snapshot, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (as_of_date, weekday)
            DO UPDATE SET
                history_dates = EXCLUDED.history_dates,
                history_count = EXCLUDED.history_count,
                insufficient_history = EXCLUDED.insufficient_history,
                avg_daily_calls = EXCLUDED.avg_daily_calls,
                daily_fte = EXCLUDED.daily_fte,
                hourly_profile = EXCLUDED.hourly_profile,
                settings_snapshot = EXCLUDED.settings_snapshot,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                as_of_date,
                profile["weekday"],
                Json(profile["history_dates"]),
                profile["history_count"],
                profile["insufficient_history"],
                profile["avg_daily_calls"],
                profile["daily_fte"],
                Json(profile["hourly_profile"]),
                Json(settings),
            ),
        )
    return profiles


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


def import_resource_csv(db, report_date_value: str, content: bytes, filename: str, user_id: Optional[int]) -> Dict[str, Any]:
    report_date = _parse_report_date(report_date_value)
    parsed = parse_resource_csv(content)
    content_hash = hashlib.sha256(content).hexdigest()

    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
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
            (report_date, filename, content_hash, Json(parsed["headers"]), len(parsed["rows"]), user_id),
        )
        upload_id = cursor.fetchone()[0]
        values = []
        for row in parsed["rows"]:
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
        cursor.execute("SELECT COALESCE(MAX(report_date), %s) FROM daily_resource_summary", (report_date,))
        as_of_date = cursor.fetchone()[0] or report_date
        profiles = _upsert_profiles_tx(cursor, as_of_date, settings)
    day_payload = get_resource_day(db, report_date.isoformat())
    return {
        "upload_id": upload_id,
        "report_date": report_date.isoformat(),
        "mapped_headers": parsed["mapped_headers"],
        "day": day_payload,
        "profiles": profiles,
    }


def update_resource_hour(db, report_date_value: str, hour: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    report_date = _parse_report_date(report_date_value)
    hour = int(hour)
    if hour < 0 or hour > 23:
        raise ValueError("INVALID_HOUR")
    planned = max(0.0, _to_float(payload.get("planned_fte"), 0.0)) if "planned_fte" in payload else None
    actual = max(0.0, _to_float(payload.get("actual_fte"), 0.0)) if "actual_fte" in payload else None
    comments = payload.get("comments") if "comments" in payload else None
    with db._get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE daily_resource_hours
            SET planned_fte = COALESCE(%s, planned_fte),
                actual_fte = COALESCE(%s, actual_fte),
                comments = COALESCE(%s, comments),
                fact_forecast_delta = COALESCE(%s, actual_fte) - forecast_fte,
                updated_at = CURRENT_TIMESTAMP
            WHERE report_date = %s AND hour = %s
            """,
            (planned, actual, comments, actual, report_date, hour),
        )
        _refresh_daily_summary_tx(cursor, report_date)
    return get_resource_day(db, report_date.isoformat())


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
        profiles = _compute_week_forecast_profiles_tx(cursor, _next_week_start_date(as_of_date), settings)
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


def get_resource_overview(db, date_from: Optional[str] = None, date_to: Optional[str] = None, as_of_date_value: Optional[str] = None) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        if as_of_date_value:
            as_of_date = _parse_report_date(as_of_date_value)
        else:
            cursor.execute("SELECT CURRENT_DATE")
            as_of_date = cursor.fetchone()[0]
        next_week_start = _next_week_start_date(as_of_date)
        profiles = _fetch_latest_profiles_tx(cursor, as_of_date, settings)
        operator_capacity = _current_operator_fte_tx(cursor, settings)
        current_operator_fte = operator_capacity["current_operator_fte"]
        next_week_forecast = _build_week_forecast_payload(next_week_start, profiles, settings, current_operator_fte)
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
        cursor.execute(
            f"""
            SELECT s.report_date, s.weekday, s.total_received, s.total_accepted,
                   s.total_lost, s.no_answer_rate, s.forecast_fte_total,
                   s.planned_fte_total, s.actual_fte_total,
                   s.fact_forecast_delta_total, u.filename, u.updated_at
            FROM daily_resource_summary s
            LEFT JOIN raw_resource_uploads u ON u.report_date = s.report_date
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
            }
            for row in cursor.fetchall()
        ]

    return {
        "settings": _json_safe(settings),
        "as_of_date": as_of_date.isoformat(),
        "weekdays": WEEKDAYS_RU,
        "profiles": _json_safe(profiles),
        "weekly_totals": _weekly_totals(profiles, settings, current_operator_fte),
        "operator_capacity": _json_safe(operator_capacity),
        "directions": directions,
        "next_week_forecast": _json_safe(next_week_forecast),
        "history": history,
    }


def get_resource_day(db, report_date_value: str) -> Dict[str, Any]:
    report_date = _parse_report_date(report_date_value)
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        _refresh_actual_fte_for_day_tx(cursor, report_date, settings)
        profile = _refresh_historical_forecast_for_day_tx(cursor, report_date, settings)
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
        "profile": _json_safe(profile),
    }
