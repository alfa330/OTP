from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

from .common import WEEKDAYS_RU, _round_value, _to_float, _to_int


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


def _weighted_aht_from_profiles(profiles: List[Dict[str, Any]]) -> float:
    total_calls = sum(_to_float(profile.get("avg_daily_calls")) for profile in profiles)
    if total_calls <= 0:
        return 0.0
    weighted_aht = sum(
        _to_float(profile.get("avg_daily_calls")) * _to_float(
            profile.get("forecast_aht_seconds", profile.get("aht_seconds"))
        )
        for profile in profiles
    )
    return weighted_aht / total_calls


def _weekly_aht_from_profiles(profiles: List[Dict[str, Any]]) -> float:
    return _weighted_aht_from_profiles(profiles)


def _apply_daily_aht_to_profile(profile: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    daily_aht_seconds = _to_float(profile.get("aht_seconds"))
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    hourly_profile = []
    daily_fte = 0.0
    for row in profile.get("hourly_profile", []):
        avg_calls = _to_float(row.get("avg_calls"))
        workload_minutes = avg_calls * settings["answer_rate"] * daily_aht_seconds / 60
        fte = workload_minutes / effective_minutes if effective_minutes > 0 else 0.0
        fte_rounded = _round_value(fte, settings["fte_rounding"])
        daily_fte += fte
        hourly_profile.append(
            {
                **row,
                "aht_seconds": daily_aht_seconds,
                "workload_minutes": workload_minutes,
                "effective_fte_minutes": effective_minutes,
                "fte": fte,
                "fte_rounded": round(fte_rounded, 4),
            }
        )
    return {
        **profile,
        "forecast_aht_seconds": daily_aht_seconds,
        "daily_fte": daily_fte,
        "hourly_profile": hourly_profile,
    }


def _compute_forecast_profile_for_date_tx(cursor, forecast_date, settings: Dict[str, Any]) -> Dict[str, Any]:
    weekday = forecast_date.weekday()
    expected_history_dates = [
        forecast_date - timedelta(days=21),
        forecast_date - timedelta(days=14),
    ]
    cursor.execute(
        """
        SELECT report_date
        FROM daily_resource_summary
        WHERE report_date = ANY(%s)
          AND weekday = %s
        ORDER BY report_date DESC
        """,
        (expected_history_dates, weekday),
    )
    history_dates = [row[0] for row in cursor.fetchall()]
    profile = _apply_daily_aht_to_profile(
        _build_profile_from_history_dates_tx(cursor, weekday, history_dates, settings),
        settings,
    )
    return {
        **WEEKDAYS_RU[weekday],
        **profile,
        "forecast_date": forecast_date.isoformat(),
    }


def _compute_period_forecast_profiles_tx(cursor, period_start, period_end, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    profiles = []
    current_date = period_start
    while current_date <= period_end:
        profiles.append(_compute_forecast_profile_for_date_tx(cursor, current_date, settings))
        current_date += timedelta(days=1)
    return profiles


def _compute_week_forecast_profiles_tx(cursor, target_week_start, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _compute_period_forecast_profiles_tx(
        cursor,
        target_week_start,
        target_week_start + timedelta(days=6),
        settings,
    )


def _actual_resource_load_for_period_tx(cursor, period_start, period_end, settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    cursor.execute(
        """
        SELECT report_date, hour, received_calls, accepted_calls, lost_calls,
               talk_time_seconds, avg_talk_seconds
        FROM daily_resource_hours
        WHERE report_date BETWEEN %s AND %s
        ORDER BY report_date, hour
        """,
        (period_start, period_end),
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


def _actual_resource_load_for_week_tx(cursor, target_week_start, settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return _actual_resource_load_for_period_tx(
        cursor,
        target_week_start,
        target_week_start + timedelta(days=6),
        settings,
    )


def _build_forecast_payload(
    period_start,
    period_end,
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
    actual_resource_by_day: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    period_aht_seconds = _weighted_aht_from_profiles(profiles)
    period_totals = _period_totals(profiles, settings, current_operator_fte)
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    actual_resource_by_day = actual_resource_by_day or {}
    first_history_period_start = period_start - timedelta(days=21)
    first_history_period_end = period_end - timedelta(days=21)
    second_history_period_start = period_start - timedelta(days=14)
    second_history_period_end = period_end - timedelta(days=14)
    days = []
    for index, profile in enumerate(profiles):
        weekday = int(profile.get("weekday", 0))
        forecast_date_iso = profile.get("forecast_date") or (period_start + timedelta(days=index)).isoformat()
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
                "forecast_aht_seconds": _to_float(profile.get("forecast_aht_seconds", profile.get("aht_seconds"))),
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
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "periodDays": len(profiles),
        "week_start": period_start.isoformat(),
        "week_end": period_end.isoformat(),
        "history_start": first_history_period_start.isoformat(),
        "history_end": second_history_period_end.isoformat(),
        "history_weeks": [
            {
                "start": first_history_period_start.isoformat(),
                "end": first_history_period_end.isoformat(),
            },
            {
                "start": second_history_period_start.isoformat(),
                "end": second_history_period_end.isoformat(),
            },
        ],
        "history_periods": [
            {
                "start": first_history_period_start.isoformat(),
                "end": first_history_period_end.isoformat(),
            },
            {
                "start": second_history_period_start.isoformat(),
                "end": second_history_period_end.isoformat(),
            },
        ],
        "historyComplete": all(not bool(profile.get("insufficient_history")) for profile in profiles),
        "days": days,
        "periodAhtSeconds": period_aht_seconds,
        "weeklyAhtSeconds": period_aht_seconds,
        "ahtMode": "daily",
        "answerRate": settings["answer_rate"],
        "occ": settings["occ"],
        "ur": settings["ur"],
        "shrinkage": settings["shrinkage_coeff"],
        "weeklyHours": settings["weekly_hours_per_operator"],
        "periodHoursPerOperator": period_totals["period_hours_per_operator"],
        "effectiveMinutes": effective_minutes,
        "periodFteHours": period_totals["period_fte_hours"],
        "weeklyFteHours": period_totals["period_fte_hours"],
        "baseOperators": period_totals["base_operators"],
        "operatorsWithShrinkage": period_totals["operators_with_shrinkage"],
        "operatorsRounded": period_totals["operators_rounded"],
        "currentOperatorFte": period_totals["current_operator_fte"],
        "operatorFteGap": period_totals["operator_fte_gap"],
    }


def _build_week_forecast_payload(
    target_week_start,
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
    actual_resource_by_day: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return _build_forecast_payload(
        target_week_start,
        target_week_start + timedelta(days=6),
        profiles,
        settings,
        current_operator_fte,
        actual_resource_by_day,
    )


def _compute_historical_forecast_profile_for_day_tx(cursor, report_date, settings: Dict[str, Any]) -> Dict[str, Any]:
    return _compute_forecast_profile_for_date_tx(cursor, report_date, settings)


def _period_totals(
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
) -> Dict[str, Any]:
    period_fte_hours = sum(_to_float(profile.get("daily_fte")) for profile in profiles)
    period_days = max(1, len(profiles))
    period_hours_per_operator = settings["weekly_hours_per_operator"] * period_days / 7
    base_operators = period_fte_hours / period_hours_per_operator if period_hours_per_operator > 0 else 0
    operators_with_shrinkage = base_operators / settings["shrinkage_coeff"] if settings["shrinkage_coeff"] > 0 else 0
    operator_fte_gap = current_operator_fte - operators_with_shrinkage
    return {
        "period_fte_hours": round(period_fte_hours, 4),
        "period_hours_per_operator": round(period_hours_per_operator, 4),
        "weekly_fte_hours": round(period_fte_hours, 4),
        "base_operators": round(base_operators, 4),
        "operators_with_shrinkage": round(operators_with_shrinkage, 4),
        "operators_rounded": round(_round_value(operators_with_shrinkage, settings["shift_rounding"]), 4),
        "current_operator_fte": round(current_operator_fte, 4),
        "operator_fte_gap": round(operator_fte_gap, 4),
    }


def _weekly_totals(
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
) -> Dict[str, Any]:
    return _period_totals(profiles, settings, current_operator_fte)


