from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

from .common import WEEKDAYS_RU, _round_value, _to_float, _to_int


INCIDENT_UPLIFT_LOOKBACK_DAYS = 6
INCIDENT_UPLIFT_MAX_RATIO = 2.0


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


def _empty_incident_uplift_profile() -> Dict[str, Any]:
    return {
        "lookback_days": INCIDENT_UPLIFT_LOOKBACK_DAYS,
        "max_ratio": INCIDENT_UPLIFT_MAX_RATIO,
        "source_dates": [],
        "source_start": None,
        "source_end": None,
        "source_day_count": 0,
        "average_growth_ratio": 0.0,
        "max_hourly_growth_ratio": 0.0,
        "total_positive_delta_calls": 0.0,
        "hourly": [
            {
                "hour": hour,
                "growth_ratio": 0.0,
                "weighted_delta_calls": 0.0,
                "source_count": 0,
                "positive_source_count": 0,
                "sources": [],
            }
            for hour in range(24)
        ],
    }


def _compute_recent_incident_uplift_profile_tx(
    cursor,
    period_start,
    settings: Dict[str, Any],
    lookback_days: int = INCIDENT_UPLIFT_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    lookback_days = max(1, int(lookback_days or INCIDENT_UPLIFT_LOOKBACK_DAYS))
    cursor.execute(
        """
        SELECT report_date
        FROM daily_resource_summary
        WHERE report_date < %s
        ORDER BY report_date DESC
        LIMIT %s
        """,
        (period_start, lookback_days),
    )
    source_dates = [row[0] for row in cursor.fetchall()]
    if not source_dates:
        return _empty_incident_uplift_profile()

    weights_by_date = {
        report_date: lookback_days - index
        for index, report_date in enumerate(source_dates)
    }
    cursor.execute(
        """
        SELECT report_date, hour, received_calls, forecast_calls
        FROM daily_resource_hours
        WHERE report_date = ANY(%s)
        ORDER BY report_date DESC, hour
        """,
        (source_dates,),
    )

    hourly = {
        hour: {
            "hour": hour,
            "ratio_weighted_sum": 0.0,
            "ratio_weight": 0.0,
            "delta_weighted_sum": 0.0,
            "delta_weight": 0.0,
            "source_count": 0,
            "positive_source_count": 0,
            "sources": [],
        }
        for hour in range(24)
    }
    total_positive_delta = 0.0
    total_forecast_for_ratio = 0.0
    total_weighted_ratio = 0.0
    total_ratio_weight = 0.0

    for report_date, hour_raw, received_raw, forecast_raw in cursor.fetchall():
        hour = int(hour_raw)
        if hour < 0 or hour > 23:
            continue
        weight = float(weights_by_date.get(report_date, 1))
        forecast_calls = max(0.0, _to_float(forecast_raw))
        received_calls = max(0.0, _to_float(received_raw))
        positive_delta = max(0.0, received_calls - forecast_calls)
        ratio = 0.0
        if forecast_calls > 0:
            ratio = min(positive_delta / forecast_calls, INCIDENT_UPLIFT_MAX_RATIO)
            hourly[hour]["ratio_weighted_sum"] += ratio * weight
            hourly[hour]["ratio_weight"] += weight
            total_weighted_ratio += ratio * weight
            total_ratio_weight += weight
            total_forecast_for_ratio += forecast_calls
        hourly[hour]["delta_weighted_sum"] += positive_delta * weight
        hourly[hour]["delta_weight"] += weight
        hourly[hour]["source_count"] += 1
        if positive_delta > 0:
            hourly[hour]["positive_source_count"] += 1
            total_positive_delta += positive_delta
        hourly[hour]["sources"].append({
            "date": report_date.isoformat(),
            "weight": weight,
            "forecast_calls": round(forecast_calls, 4),
            "actual_calls": round(received_calls, 4),
            "delta_calls": round(positive_delta, 4),
            "growth_ratio": round(ratio, 4),
        })

    hourly_rows = []
    for hour in range(24):
        item = hourly[hour]
        ratio_weight = _to_float(item.get("ratio_weight"))
        delta_weight = _to_float(item.get("delta_weight"))
        growth_ratio = item["ratio_weighted_sum"] / ratio_weight if ratio_weight > 0 else 0.0
        weighted_delta_calls = item["delta_weighted_sum"] / delta_weight if delta_weight > 0 else 0.0
        hourly_rows.append({
            "hour": hour,
            "growth_ratio": round(growth_ratio, 4),
            "weighted_delta_calls": round(weighted_delta_calls, 4),
            "source_count": int(item.get("source_count") or 0),
            "positive_source_count": int(item.get("positive_source_count") or 0),
            "sources": item.get("sources") or [],
        })

    source_dates_iso = [item.isoformat() for item in source_dates]
    return {
        "lookback_days": lookback_days,
        "max_ratio": INCIDENT_UPLIFT_MAX_RATIO,
        "source_dates": source_dates_iso,
        "source_start": min(source_dates).isoformat(),
        "source_end": max(source_dates).isoformat(),
        "source_day_count": len(source_dates),
        "average_growth_ratio": round(total_weighted_ratio / total_ratio_weight, 4) if total_ratio_weight > 0 else 0.0,
        "weighted_total_growth_ratio": round(total_positive_delta / total_forecast_for_ratio, 4) if total_forecast_for_ratio > 0 else 0.0,
        "max_hourly_growth_ratio": max((row["growth_ratio"] for row in hourly_rows), default=0.0),
        "total_positive_delta_calls": round(total_positive_delta, 4),
        "hourly": hourly_rows,
    }


def _build_forecast_payload(
    period_start,
    period_end,
    profiles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    current_operator_fte: float = 0.0,
    actual_resource_by_day: Optional[Dict[str, Dict[str, Any]]] = None,
    incident_uplift_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    period_aht_seconds = _weighted_aht_from_profiles(profiles)
    period_totals = _period_totals(profiles, settings, current_operator_fte)
    effective_minutes = 60 * settings["occ"] * settings["ur"]
    actual_resource_by_day = actual_resource_by_day or {}
    incident_uplift_profile = incident_uplift_profile or _empty_incident_uplift_profile()
    incident_uplift_by_hour = {
        int(item.get("hour")): item
        for item in incident_uplift_profile.get("hourly", [])
        if item.get("hour") is not None
    }
    first_history_period_start = period_start - timedelta(days=21)
    first_history_period_end = period_end - timedelta(days=21)
    second_history_period_start = period_start - timedelta(days=14)
    second_history_period_end = period_end - timedelta(days=14)
    days = []
    incident_period_calls = 0.0
    incident_period_workload_minutes = 0.0
    incident_period_fte_hours = 0.0
    for index, profile in enumerate(profiles):
        weekday = int(profile.get("weekday", 0))
        forecast_date_iso = profile.get("forecast_date") or (period_start + timedelta(days=index)).isoformat()
        actual_day = actual_resource_by_day.get(forecast_date_iso, {})
        hourly_forecast = []
        day_incident_calls = 0.0
        day_incident_workload_minutes = 0.0
        day_incident_fte = 0.0
        for row in profile.get("hourly_profile", []):
            hour = int(row.get("hour", 0))
            actual_hour = (actual_day.get("hourly") or {}).get(hour, {})
            forecast_calls = _to_float(row.get("avg_calls"))
            forecast_aht_seconds = _to_float(row.get("aht_seconds"))
            forecast_workload_minutes = _to_float(row.get("workload_minutes"))
            forecast_fte = _to_float(row.get("fte"))
            uplift_hour = incident_uplift_by_hour.get(hour, {})
            incident_ratio = _to_float(uplift_hour.get("growth_ratio"))
            incident_calls = forecast_calls * incident_ratio
            if forecast_calls <= 0 and incident_calls <= 0:
                incident_calls = _to_float(uplift_hour.get("weighted_delta_calls"))
            incident_calls = max(0.0, incident_calls)
            incident_workload_minutes = incident_calls * settings["answer_rate"] * forecast_aht_seconds / 60
            incident_fte = incident_workload_minutes / effective_minutes if effective_minutes > 0 else 0.0
            day_incident_calls += incident_calls
            day_incident_workload_minutes += incident_workload_minutes
            day_incident_fte += incident_fte
            hourly_forecast.append(
                {
                    **row,
                    "forecast_calls": forecast_calls,
                    "forecast_aht_seconds": forecast_aht_seconds,
                    "forecast_workload_minutes": forecast_workload_minutes,
                    "forecast_fte": forecast_fte,
                    "incident_uplift_ratio": incident_ratio,
                    "incident_uplift_calls": incident_calls,
                    "incident_uplift_workload_minutes": incident_workload_minutes,
                    "incident_uplift_fte": incident_fte,
                    "incident_adjusted_calls": forecast_calls + incident_calls,
                    "incident_adjusted_workload_minutes": forecast_workload_minutes + incident_workload_minutes,
                    "incident_adjusted_fte": forecast_fte + incident_fte,
                    "incident_uplift_sources": uplift_hour.get("sources") or [],
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
        forecast_calls_total = _to_float(profile.get("avg_daily_calls"))
        forecast_workload_minutes_total = sum(_to_float(row.get("workload_minutes")) for row in profile.get("hourly_profile", []))
        forecast_daily_fte = _to_float(profile.get("daily_fte"))
        incident_period_calls += day_incident_calls
        incident_period_workload_minutes += day_incident_workload_minutes
        incident_period_fte_hours += day_incident_fte
        days.append(
            {
                **profile,
                "forecast_date": forecast_date_iso,
                "forecast_calls": forecast_calls_total,
                "forecast_aht_seconds": _to_float(profile.get("forecast_aht_seconds", profile.get("aht_seconds"))),
                "forecast_workload_minutes": forecast_workload_minutes_total,
                "forecast_daily_fte": forecast_daily_fte,
                "incident_uplift_calls": day_incident_calls,
                "incident_uplift_workload_minutes": day_incident_workload_minutes,
                "incident_uplift_fte": day_incident_fte,
                "incident_uplift_ratio": (day_incident_calls / forecast_calls_total) if forecast_calls_total > 0 else 0.0,
                "incident_adjusted_calls": forecast_calls_total + day_incident_calls,
                "incident_adjusted_workload_minutes": forecast_workload_minutes_total + day_incident_workload_minutes,
                "incident_adjusted_daily_fte": forecast_daily_fte + day_incident_fte,
                "operators_equivalent": forecast_daily_fte / 8,
                "has_actual_report": bool(actual_day.get("has_actual_report")),
                "actual_received_calls": _to_int(actual_day.get("actual_received_calls")),
                "actual_accepted_calls": _to_int(actual_day.get("actual_accepted_calls")),
                "actual_lost_calls": _to_int(actual_day.get("actual_lost_calls")),
                "actual_aht_seconds": _to_float(actual_day.get("actual_aht_seconds")),
                "actual_workload_minutes": _to_float(actual_day.get("actual_workload_minutes")),
                "actual_report_fte": _to_float(actual_day.get("actual_report_fte")),
                "actual_forecast_fte_delta": _to_float(actual_day.get("actual_report_fte")) - forecast_daily_fte,
                "hourly_forecast": hourly_forecast,
            }
        )
    incident_adjusted_period_fte_hours = _to_float(period_totals["period_fte_hours"]) + incident_period_fte_hours
    period_hours_per_operator = _to_float(period_totals["period_hours_per_operator"])
    incident_adjusted_base_operators = (
        incident_adjusted_period_fte_hours / period_hours_per_operator
        if period_hours_per_operator > 0 else 0.0
    )
    incident_adjusted_operators_with_shrinkage = (
        incident_adjusted_base_operators / settings["shrinkage_coeff"]
        if settings["shrinkage_coeff"] > 0 else 0.0
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
        "incidentUplift": {
            **incident_uplift_profile,
            "projected_calls": round(incident_period_calls, 4),
            "projected_workload_minutes": round(incident_period_workload_minutes, 4),
            "projected_fte_hours": round(incident_period_fte_hours, 4),
        },
        "incidentUpliftCalls": round(incident_period_calls, 4),
        "incidentUpliftWorkloadMinutes": round(incident_period_workload_minutes, 4),
        "incidentUpliftFteHours": round(incident_period_fte_hours, 4),
        "incidentAdjustedPeriodFteHours": round(incident_adjusted_period_fte_hours, 4),
        "incidentAdjustedBaseOperators": round(incident_adjusted_base_operators, 4),
        "incidentAdjustedOperatorsWithShrinkage": round(incident_adjusted_operators_with_shrinkage, 4),
        "incidentAdjustedOperatorFteGap": round(current_operator_fte - incident_adjusted_operators_with_shrinkage, 4),
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


