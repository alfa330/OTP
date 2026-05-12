import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from psycopg2.extras import Json, execute_values


from resource_fte.calculations import (
    _actual_resource_load_for_period_tx,
    _build_forecast_payload,
    _compute_historical_forecast_profile_for_day_tx,
    _compute_period_forecast_profiles_tx,
    _compute_recent_incident_uplift_profile_tx,
    _compute_week_forecast_profiles_tx,
    _next_week_start_date,
    _week_start_date,
)
from resource_fte.common import (
    RESOURCE_RATE_VALUES,
    WORK_DAYS_PER_OPERATOR_WEEK,
    WEEKDAYS_RU,
    _json_safe,
    _parse_report_date,
    _resource_rate_key,
    _resource_rate_value,
    _to_float,
    _to_int,
)
from resource_fte.schedule_generation import (
    _generate_schedule_preview_from_forecast,
    _normalize_shift_templates,
    get_resource_shift_templates,
)


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


def _period_operator_availability_tx(
    cursor,
    period_start,
    period_end,
    settings: Optional[Dict[str, Any]] = None,
    include_details: bool = True,
) -> Dict[str, Any]:
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    period_days = max(1, (period_end - period_start).days + 1)
    threshold_days = period_days / 2.0
    selected_direction_ids = _coerce_int_list((settings or {}).get("selected_direction_ids"))
    direction_filter = "AND u.direction_id = ANY(%s)" if selected_direction_ids else ""
    params = [period_start, period_end]
    if selected_direction_ids:
        params.append(selected_direction_ids)

    if not include_details:
        aggregate_params = list(params) + [threshold_days, threshold_days, threshold_days]
        cursor.execute(
            f"""
            WITH period_days AS (
                SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS day
            ),
            status_history AS (
                SELECT
                    operator_id,
                    BOOL_OR(status_code = 'dismissal') AS has_dismissal_history,
                    BOOL_OR(status_code = 'bs') AS has_bs_history,
                    BOOL_OR(status_code = 'sick_leave') AS has_sick_leave_history,
                    BOOL_OR(status_code = 'annual_leave') AS has_annual_leave_history
                FROM operator_schedule_status_periods
                WHERE status_code IN ('dismissal', 'bs', 'sick_leave', 'annual_leave')
                GROUP BY operator_id
            ),
            operators AS (
                SELECT
                    u.id,
                    COALESCE(u.rate, 1.0) AS rate,
                    LOWER(TRIM(COALESCE(u.status, 'working'))) AS current_status,
                    COALESCE(h.has_dismissal_history, FALSE) AS has_dismissal_history,
                    COALESCE(h.has_bs_history, FALSE) AS has_bs_history,
                    COALESCE(h.has_sick_leave_history, FALSE) AS has_sick_leave_history,
                    COALESCE(h.has_annual_leave_history, FALSE) AS has_annual_leave_history
                FROM users u
                LEFT JOIN status_history h ON h.operator_id = u.id
                WHERE u.role = 'operator'
                  {direction_filter}
            ),
            operator_days AS (
                SELECT
                    o.id,
                    o.rate,
                    CASE
                        WHEN scheduled.status_code IS NOT NULL THEN
                            CASE
                                WHEN scheduled.status_code IN ('bs', 'sick_leave', 'annual_leave', 'dismissal')
                                    THEN scheduled.status_code
                                ELSE 'working'
                            END
                        WHEN o.current_status IN ('bs', 'unpaid_leave') AND o.has_bs_history THEN 'working'
                        WHEN o.current_status = 'sick_leave' AND o.has_sick_leave_history THEN 'working'
                        WHEN o.current_status = 'annual_leave' AND o.has_annual_leave_history THEN 'working'
                        WHEN o.current_status IN ('fired', 'dismissal') AND o.has_dismissal_history THEN 'working'
                        WHEN o.current_status <> 'working' THEN o.current_status
                        ELSE 'working'
                    END AS effective_status
                FROM operators o
                CROSS JOIN period_days d
                LEFT JOIN LATERAL (
                    SELECT p.status_code
                    FROM operator_schedule_status_periods p
                    WHERE p.operator_id = o.id
                      AND p.start_date <= d.day
                      AND COALESCE(p.end_date, DATE '9999-12-31') >= d.day
                    ORDER BY p.start_date DESC, p.id DESC
                    LIMIT 1
                ) scheduled ON TRUE
            ),
            operator_summary AS (
                SELECT
                    id,
                    rate,
                    COUNT(*) FILTER (WHERE effective_status = 'working')::INT AS working_days,
                    COUNT(*) FILTER (WHERE effective_status = 'bs')::INT AS bs_days,
                    COUNT(*) FILTER (WHERE effective_status = 'unpaid_leave')::INT AS unpaid_leave_days,
                    COUNT(*) FILTER (WHERE effective_status = 'sick_leave')::INT AS sick_leave_days,
                    COUNT(*) FILTER (WHERE effective_status = 'annual_leave')::INT AS annual_leave_days,
                    COUNT(*) FILTER (WHERE effective_status = 'dismissal')::INT AS dismissal_days,
                    COUNT(*) FILTER (WHERE effective_status = 'fired')::INT AS fired_days
                FROM operator_days
                GROUP BY id, rate
            )
            SELECT
                rate,
                COUNT(*)::INT AS total_count,
                COUNT(*) FILTER (WHERE working_days > %s)::INT AS available_count,
                COUNT(*) FILTER (WHERE working_days > 0 AND working_days <= %s)::INT AS partial_count,
                COUNT(*) FILTER (WHERE working_days = 0)::INT AS unavailable_count,
                COALESCE(SUM(rate), 0) AS total_fte,
                COALESCE(SUM(CASE WHEN working_days > %s THEN rate ELSE 0 END), 0) AS available_fte,
                COALESCE(SUM(working_days), 0)::INT AS working_days,
                COALESCE(SUM(bs_days), 0)::INT AS bs_days,
                COALESCE(SUM(unpaid_leave_days), 0)::INT AS unpaid_leave_days,
                COALESCE(SUM(sick_leave_days), 0)::INT AS sick_leave_days,
                COALESCE(SUM(annual_leave_days), 0)::INT AS annual_leave_days,
                COALESCE(SUM(dismissal_days), 0)::INT AS dismissal_days,
                COALESCE(SUM(fired_days), 0)::INT AS fired_days
            FROM operator_summary
            GROUP BY rate
            """,
            aggregate_params,
        )

        by_rate = {
            _resource_rate_key(rate): {
                "rate": float(rate),
                "count": 0,
                "fte": 0.0,
                "total_count": 0,
                "total_fte": 0.0,
            }
            for rate in RESOURCE_RATE_VALUES
        }
        status_summary = {
            "working": 0,
            "bs": 0,
            "unpaid_leave": 0,
            "sick_leave": 0,
            "annual_leave": 0,
            "dismissal": 0,
            "fired": 0,
        }
        total_operator_count = 0
        available_operator_count = 0
        partially_available_operator_count = 0
        unavailable_operator_count = 0
        available_operator_fte = 0.0

        for row in cursor.fetchall():
            (
                rate_raw,
                total_count_raw,
                available_count_raw,
                partial_count_raw,
                unavailable_count_raw,
                total_fte_raw,
                available_fte_raw,
                working_days_raw,
                bs_days_raw,
                unpaid_leave_days_raw,
                sick_leave_days_raw,
                annual_leave_days_raw,
                dismissal_days_raw,
                fired_days_raw,
            ) = row
            rate = _resource_rate_value(rate_raw)
            rate_key = _resource_rate_key(rate)
            total_count = _to_int(total_count_raw)
            available_count = _to_int(available_count_raw)
            partial_count = _to_int(partial_count_raw)
            unavailable_count = _to_int(unavailable_count_raw)
            available_fte = _to_float(available_fte_raw)
            total_operator_count += total_count
            available_operator_count += available_count
            partially_available_operator_count += partial_count
            unavailable_operator_count += unavailable_count
            available_operator_fte += available_fte
            by_rate[rate_key]["count"] += available_count
            by_rate[rate_key]["fte"] += available_fte
            by_rate[rate_key]["total_count"] += total_count
            by_rate[rate_key]["total_fte"] += _to_float(total_fte_raw)
            status_summary["working"] += _to_int(working_days_raw)
            status_summary["bs"] += _to_int(bs_days_raw)
            status_summary["unpaid_leave"] += _to_int(unpaid_leave_days_raw)
            status_summary["sick_leave"] += _to_int(sick_leave_days_raw)
            status_summary["annual_leave"] += _to_int(annual_leave_days_raw)
            status_summary["dismissal"] += _to_int(dismissal_days_raw)
            status_summary["fired"] += _to_int(fired_days_raw)

        rate_capacity = [
            {
                **by_rate[_resource_rate_key(rate)],
                "fte": round(_to_float(by_rate[_resource_rate_key(rate)].get("fte")), 4),
                "total_fte": round(_to_float(by_rate[_resource_rate_key(rate)].get("total_fte")), 4),
            }
            for rate in RESOURCE_RATE_VALUES
        ]
        return {
            "period_operator_count": total_operator_count,
            "period_available_operator_count": available_operator_count,
            "period_available_operator_fte": round(available_operator_fte, 4),
            "period_partial_operator_count": partially_available_operator_count,
            "period_unavailable_operator_count": unavailable_operator_count,
            "period_working_days_threshold": round(threshold_days, 4),
            "period_day_count": period_days,
            "period_rate_capacity": rate_capacity,
            "period_rate_counts": {item["rate"]: item["count"] for item in rate_capacity},
            "period_status_summary": status_summary,
            "period_operator_details": [],
            "selected_direction_ids": selected_direction_ids,
        }

    cursor.execute(
        f"""
        WITH period_days AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS day
        ),
        status_history AS (
            SELECT
                operator_id,
                BOOL_OR(status_code = 'dismissal') AS has_dismissal_history,
                BOOL_OR(status_code = 'bs') AS has_bs_history,
                BOOL_OR(status_code = 'sick_leave') AS has_sick_leave_history,
                BOOL_OR(status_code = 'annual_leave') AS has_annual_leave_history
            FROM operator_schedule_status_periods
            WHERE status_code IN ('dismissal', 'bs', 'sick_leave', 'annual_leave')
            GROUP BY operator_id
        ),
        operators AS (
            SELECT
                u.id,
                u.name,
                direction.name AS direction_name,
                supervisor.name AS supervisor_name,
                COALESCE(u.rate, 1.0) AS rate,
                LOWER(TRIM(COALESCE(u.status, 'working'))) AS current_status,
                COALESCE(h.has_dismissal_history, FALSE) AS has_dismissal_history,
                COALESCE(h.has_bs_history, FALSE) AS has_bs_history,
                COALESCE(h.has_sick_leave_history, FALSE) AS has_sick_leave_history,
                COALESCE(h.has_annual_leave_history, FALSE) AS has_annual_leave_history
            FROM users u
            LEFT JOIN directions direction ON direction.id = u.direction_id
            LEFT JOIN users supervisor ON supervisor.id = u.supervisor_id
            LEFT JOIN status_history h ON h.operator_id = u.id
            WHERE u.role = 'operator'
              {direction_filter}
        ),
        operator_days AS (
            SELECT
                o.id,
                o.name,
                o.direction_name,
                o.supervisor_name,
                o.rate,
                o.current_status,
                d.day,
                CASE
                    WHEN scheduled.status_code IS NOT NULL THEN
                        CASE
                            WHEN scheduled.status_code IN ('bs', 'sick_leave', 'annual_leave', 'dismissal')
                                THEN scheduled.status_code
                            ELSE 'working'
                        END
                    WHEN o.current_status IN ('bs', 'unpaid_leave') AND o.has_bs_history THEN 'working'
                    WHEN o.current_status = 'sick_leave' AND o.has_sick_leave_history THEN 'working'
                    WHEN o.current_status = 'annual_leave' AND o.has_annual_leave_history THEN 'working'
                    WHEN o.current_status IN ('fired', 'dismissal') AND o.has_dismissal_history THEN 'working'
                    WHEN o.current_status <> 'working' THEN o.current_status
                    ELSE 'working'
                END AS effective_status
            FROM operators o
            CROSS JOIN period_days d
            LEFT JOIN LATERAL (
                SELECT p.status_code
                FROM operator_schedule_status_periods p
                WHERE p.operator_id = o.id
                  AND p.start_date <= d.day
                  AND COALESCE(p.end_date, DATE '9999-12-31') >= d.day
                ORDER BY p.start_date DESC, p.id DESC
                LIMIT 1
            ) scheduled ON TRUE
        )
        SELECT
            id,
            name,
            direction_name,
            supervisor_name,
            rate,
            current_status,
            COUNT(*)::INT AS total_days,
            COUNT(*) FILTER (WHERE effective_status = 'working')::INT AS working_days,
            COUNT(*) FILTER (WHERE effective_status = 'bs')::INT AS bs_days,
            COUNT(*) FILTER (WHERE effective_status = 'unpaid_leave')::INT AS unpaid_leave_days,
            COUNT(*) FILTER (WHERE effective_status = 'sick_leave')::INT AS sick_leave_days,
            COUNT(*) FILTER (WHERE effective_status = 'annual_leave')::INT AS annual_leave_days,
            COUNT(*) FILTER (WHERE effective_status = 'dismissal')::INT AS dismissal_days,
            COUNT(*) FILTER (WHERE effective_status = 'fired')::INT AS fired_days
        FROM operator_days
        GROUP BY id, name, direction_name, supervisor_name, rate, current_status
        ORDER BY name, id
        """,
        params,
    )

    by_rate = {
        _resource_rate_key(rate): {
            "rate": float(rate),
            "count": 0,
            "fte": 0.0,
            "total_count": 0,
            "total_fte": 0.0,
        }
        for rate in RESOURCE_RATE_VALUES
    }
    status_summary = {
        "working": 0,
        "bs": 0,
        "unpaid_leave": 0,
        "sick_leave": 0,
        "annual_leave": 0,
        "dismissal": 0,
        "fired": 0,
    }
    operator_details = []
    total_operator_count = 0
    available_operator_count = 0
    partially_available_operator_count = 0
    unavailable_operator_count = 0
    available_operator_fte = 0.0

    for row in cursor.fetchall():
        (
            operator_id,
            operator_name,
            direction_name,
            supervisor_name,
            rate_raw,
            current_status,
            total_days_raw,
            working_days_raw,
            bs_days_raw,
            unpaid_leave_days_raw,
            sick_leave_days_raw,
            annual_leave_days_raw,
            dismissal_days_raw,
            fired_days_raw,
        ) = row
        total_operator_count += 1
        rate = _resource_rate_value(rate_raw)
        rate_key = _resource_rate_key(rate)
        by_rate[rate_key]["total_count"] += 1
        by_rate[rate_key]["total_fte"] += rate
        total_days = max(0, _to_int(total_days_raw))
        working_days = max(0, _to_int(working_days_raw))
        status_counts = {
            "working": working_days,
            "bs": max(0, _to_int(bs_days_raw)),
            "unpaid_leave": max(0, _to_int(unpaid_leave_days_raw)),
            "sick_leave": max(0, _to_int(sick_leave_days_raw)),
            "annual_leave": max(0, _to_int(annual_leave_days_raw)),
            "dismissal": max(0, _to_int(dismissal_days_raw)),
            "fired": max(0, _to_int(fired_days_raw)),
        }
        for status_key, day_count in status_counts.items():
            status_summary[status_key] = status_summary.get(status_key, 0) + int(day_count or 0)
        is_available = working_days > threshold_days
        if is_available:
            available_operator_count += 1
            available_operator_fte += rate
            by_rate[rate_key]["count"] += 1
            by_rate[rate_key]["fte"] += rate
        elif working_days > 0:
            partially_available_operator_count += 1
        else:
            unavailable_operator_count += 1

        operator_details.append({
            "operatorId": int(operator_id),
            "name": operator_name,
            "directionName": direction_name,
            "supervisorName": supervisor_name,
            "rate": rate,
            "currentStatus": current_status or "working",
            "totalDays": total_days,
            "workingDays": working_days,
            "nonWorkingDays": max(0, total_days - working_days),
            "included": bool(is_available),
            "fteContribution": round(rate if is_available else 0.0, 4),
            "statusDays": status_counts,
        })

    rate_capacity = [
        {
            **by_rate[_resource_rate_key(rate)],
            "fte": round(_to_float(by_rate[_resource_rate_key(rate)].get("fte")), 4),
            "total_fte": round(_to_float(by_rate[_resource_rate_key(rate)].get("total_fte")), 4),
        }
        for rate in RESOURCE_RATE_VALUES
    ]
    operator_details.sort(key=lambda item: (
        not bool(item.get("included")),
        -int(item.get("workingDays") or 0),
        str(item.get("name") or "").lower(),
    ))

    return {
        "period_operator_count": total_operator_count,
        "period_available_operator_count": available_operator_count,
        "period_available_operator_fte": round(available_operator_fte, 4),
        "period_partial_operator_count": partially_available_operator_count,
        "period_unavailable_operator_count": unavailable_operator_count,
        "period_working_days_threshold": round(threshold_days, 4),
        "period_day_count": period_days,
        "period_rate_capacity": rate_capacity,
        "period_rate_counts": {item["rate"]: item["count"] for item in rate_capacity},
        "period_status_summary": status_summary,
        "period_operator_details": operator_details,
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


def build_resource_schedule_preview(db, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    period_start_value = (
        payload.get("date_from")
        or payload.get("dateFrom")
        or payload.get("period_start")
        or payload.get("periodStart")
    )
    period_end_value = (
        payload.get("date_to")
        or payload.get("dateTo")
        or payload.get("period_end")
        or payload.get("periodEnd")
    )
    week_start_value = payload.get("week_start") or payload.get("weekStart")
    if period_start_value:
        period_start = _parse_report_date(period_start_value)
        period_end = _parse_report_date(period_end_value) if period_end_value else period_start
    elif week_start_value:
        period_start = _week_start_date(_parse_report_date(week_start_value))
        period_end = period_start + timedelta(days=6)
    else:
        period_start = _next_week_start_date(datetime.now().date())
        period_end = period_start + timedelta(days=6)
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    templates = _normalize_shift_templates(payload.get("templates") if "templates" in payload else None)

    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        cursor.execute("SELECT CURRENT_DATE")
        incident_anchor_date = cursor.fetchone()[0]
        profiles = _compute_period_forecast_profiles_tx(cursor, period_start, period_end, settings)
        operator_capacity = _current_operator_fte_tx(cursor, settings)
        current_fte = operator_capacity.get("current_operator_fte", 0.0)
        incident_uplift_profile = _compute_recent_incident_uplift_profile_tx(cursor, incident_anchor_date, settings)
        forecast_payload = _build_forecast_payload(
            period_start,
            period_end,
            profiles,
            settings,
            current_operator_fte=current_fte,
            incident_uplift_profile=incident_uplift_profile,
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


def _normalize_forecast_period(
    as_of_date,
    forecast_week_start_value: Optional[str] = None,
    forecast_date_from_value: Optional[str] = None,
    forecast_date_to_value: Optional[str] = None,
):
    if forecast_date_from_value:
        period_start = _parse_report_date(forecast_date_from_value)
        period_end = _parse_report_date(forecast_date_to_value) if forecast_date_to_value else period_start
    elif forecast_week_start_value:
        period_start = _week_start_date(_parse_report_date(forecast_week_start_value))
        period_end = period_start + timedelta(days=6)
    else:
        period_start = _next_week_start_date(as_of_date)
        period_end = period_start + timedelta(days=6)
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    return period_start, period_end


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
    forecast_date_from_value: Optional[str] = None,
    forecast_date_to_value: Optional[str] = None,
) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        if as_of_date_value:
            as_of_date = _parse_report_date(as_of_date_value)
        else:
            cursor.execute("SELECT CURRENT_DATE")
            as_of_date = cursor.fetchone()[0]
        forecast_period_start, forecast_period_end = _normalize_forecast_period(
            as_of_date,
            forecast_week_start_value=forecast_week_start_value,
            forecast_date_from_value=forecast_date_from_value,
            forecast_date_to_value=forecast_date_to_value,
        )
        profiles = _compute_period_forecast_profiles_tx(cursor, forecast_period_start, forecast_period_end, settings)
        operator_capacity = _current_operator_fte_tx(cursor, settings)
        current_operator_fte = operator_capacity["current_operator_fte"]
        period_operator_availability = _period_operator_availability_tx(
            cursor,
            forecast_period_start,
            forecast_period_end,
            settings,
            include_details=False,
        )
        actual_resource_by_day = _actual_resource_load_for_period_tx(cursor, forecast_period_start, forecast_period_end, settings)
        incident_uplift_profile = _compute_recent_incident_uplift_profile_tx(cursor, as_of_date, settings)
        next_week_forecast = _build_forecast_payload(
            forecast_period_start,
            forecast_period_end,
            profiles,
            settings,
            current_operator_fte,
            actual_resource_by_day,
            incident_uplift_profile,
        )
        period_available_operator_fte = _to_float(period_operator_availability.get("period_available_operator_fte"))
        next_week_forecast.update({
            "periodAvailableOperatorFte": period_available_operator_fte,
            "periodAvailableOperatorCount": _to_int(period_operator_availability.get("period_available_operator_count")),
            "periodOperatorCount": _to_int(period_operator_availability.get("period_operator_count")),
            "periodPartialOperatorCount": _to_int(period_operator_availability.get("period_partial_operator_count")),
            "periodUnavailableOperatorCount": _to_int(period_operator_availability.get("period_unavailable_operator_count")),
            "periodWorkingDaysThreshold": _to_float(period_operator_availability.get("period_working_days_threshold")),
            "periodAvailableOperatorFteGap": round(
                period_available_operator_fte - _to_float(next_week_forecast.get("operatorsWithShrinkage")),
                4,
            ),
            "periodAvailableOperatorRateCounts": period_operator_availability.get("period_rate_counts") or {},
            "periodAvailableOperatorRates": period_operator_availability.get("period_rate_capacity") or [],
            "periodOperatorStatusSummary": period_operator_availability.get("period_status_summary") or {},
            "periodOperatorAvailabilityDetails": [],
        })
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


def get_resource_operator_availability_details(
    db,
    as_of_date_value: Optional[str] = None,
    forecast_week_start_value: Optional[str] = None,
    forecast_date_from_value: Optional[str] = None,
    forecast_date_to_value: Optional[str] = None,
) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        settings = _get_settings_tx(cursor)
        if as_of_date_value:
            as_of_date = _parse_report_date(as_of_date_value)
        else:
            cursor.execute("SELECT CURRENT_DATE")
            as_of_date = cursor.fetchone()[0]
        period_start, period_end = _normalize_forecast_period(
            as_of_date,
            forecast_week_start_value=forecast_week_start_value,
            forecast_date_from_value=forecast_date_from_value,
            forecast_date_to_value=forecast_date_to_value,
        )
        availability = _period_operator_availability_tx(
            cursor,
            period_start,
            period_end,
            settings,
            include_details=True,
        )
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "periodDays": _to_int(availability.get("period_day_count")),
            "periodAvailableOperatorFte": _to_float(availability.get("period_available_operator_fte")),
            "periodAvailableOperatorCount": _to_int(availability.get("period_available_operator_count")),
            "periodOperatorCount": _to_int(availability.get("period_operator_count")),
            "periodPartialOperatorCount": _to_int(availability.get("period_partial_operator_count")),
            "periodUnavailableOperatorCount": _to_int(availability.get("period_unavailable_operator_count")),
            "periodWorkingDaysThreshold": _to_float(availability.get("period_working_days_threshold")),
            "periodAvailableOperatorRateCounts": availability.get("period_rate_counts") or {},
            "periodAvailableOperatorRates": availability.get("period_rate_capacity") or [],
            "periodOperatorStatusSummary": availability.get("period_status_summary") or {},
            "periodOperatorAvailabilityDetails": availability.get("period_operator_details") or [],
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
