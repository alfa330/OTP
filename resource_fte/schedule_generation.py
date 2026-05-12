import math
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .common import _resource_rate_key, _resource_rate_value, _round_fte_to_half, _to_float, _to_int

try:
    from ortools.sat.python import cp_model
except Exception:
    cp_model = None


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
FREEFORM_SHIFT_MIN_MINUTES = 4 * 60
FREEFORM_SHIFT_MAX_MINUTES = 9 * 60
FREEFORM_SHIFT_STEP_MINUTES = 30
FREEFORM_SHIFT_START_STEP_MINUTES = 30
FREEFORM_PREFERRED_SHIFT_DURATIONS = (4 * 60, 6 * 60 + 30, 9 * 60)
FREEFORM_RATE_DURATION_OPTIONS = {
    "1": (9 * 60,),
    "0.75": (6 * 60 + 30,),
    "0.5": (4 * 60,),
}
FREEFORM_RATE_TARGET_DURATIONS = {
    "1": 9 * 60,
    "0.75": 6 * 60 + 30,
    "0.5": 4 * 60,
}
FREEFORM_NIGHT_SHIFT_START_MINUTE = 20 * 60
FREEFORM_NIGHT_SHIFT_END_MINUTE = 32 * 60
FREEFORM_NIGHT_SHIFT_LABEL = "20*08"
FREEFORM_DEEP_NIGHT_START_MINUTE = 3 * 60
FREEFORM_DEEP_NIGHT_END_MINUTE = 7 * 60
FREEFORM_REGULAR_OVERNIGHT_MAX_END_MINUTE = 26 * 60
FREEFORM_REGULAR_OVERNIGHT_END_CLOCK_HOURS = {0, 1, 2}
SHIFT_PREVIEW_DEEP_NIGHT_NEED_WEIGHT = 3.5
SHIFT_PREVIEW_CP_SAT_TIME_LIMIT_SECONDS = 2.0
SHIFT_PREVIEW_CP_SAT_SEARCH_WORKERS = 8
SHIFT_PREVIEW_CP_SAT_DEFICIT_WEIGHT = 100
SHIFT_PREVIEW_CP_SAT_OVER_WEIGHT = 5
SHIFT_PREVIEW_CP_SAT_SHIFT_WEIGHT = 2
SHIFT_PREVIEW_CP_SAT_PREFERENCE_WEIGHT = 10
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


def _shift_preview_usage_by_rate(selected: List[Dict[str, Any]], day_count: int = 7) -> Dict[str, Dict[str, Any]]:
    day_count = max(1, int(day_count or 1))
    usage = {
        _resource_rate_key(rate): {
            "rate": float(rate),
            "weekly_shifts": 0,
            "daily_shifts": [0 for _ in range(day_count)],
        }
        for rate in RESOURCE_RATE_VALUES
    }
    for item in selected:
        rate_key = _resource_rate_key((item.get("template") or {}).get("rate"))
        day_index = int(item.get("dayIndex") or 0)
        if rate_key not in usage or day_index < 0 or day_index >= day_count:
            continue
        usage[rate_key]["weekly_shifts"] += 1
        usage[rate_key]["daily_shifts"][day_index] += 1
    return usage


def _shift_preview_usage_state(
    selected: Optional[List[Dict[str, Any]]],
    total_hours: int,
) -> Dict[str, Any]:
    total_hours = max(24, int(total_hours or SHIFT_PREVIEW_HOURS))
    weekly_usage = defaultdict(int)
    daily_usage = defaultdict(lambda: defaultdict(int))
    hourly_usage = {
        _resource_rate_key(rate): [0.0 for _ in range(total_hours)]
        for rate in RESOURCE_RATE_VALUES
    }
    for item in selected or []:
        template = item.get("template") or {}
        rate_key = _resource_rate_key(template.get("rate"))
        day_index = int(item.get("dayIndex") or 0)
        weekly_usage[rate_key] += 1
        daily_usage[day_index][rate_key] += 1
        presence_vector = item.get("presenceVector")
        if presence_vector is None:
            presence_vector = _shift_preview_presence_vector(day_index, template, total_hours)
        for index, amount in enumerate(presence_vector or []):
            if index >= total_hours:
                break
            hourly_usage[rate_key][index] = round(
                float(hourly_usage[rate_key][index] or 0) + float(amount or 0),
                4,
            )
    return {
        "weekly_usage": weekly_usage,
        "daily_usage": daily_usage,
        "hourly_usage": hourly_usage,
    }


def _shift_preview_presence_vector(day_index: int, template: Dict[str, Any], total_hours: int = SHIFT_PREVIEW_HOURS) -> List[float]:
    total_hours = max(24, int(total_hours or SHIFT_PREVIEW_HOURS))
    vector = [0.0 for _ in range(total_hours)]
    day_start = int(day_index) * 24 * 60
    start_abs = day_start + int(template.get("startMinute") or 0)
    end_abs = day_start + int(template.get("endMinute") or 0)
    for hour_index in range(total_hours):
        hour_start = hour_index * 60
        hour_end = hour_start + 60
        overlap = max(0, min(end_abs, hour_end) - max(start_abs, hour_start))
        if overlap > 0:
            vector[hour_index] = round(overlap / 60, 4)
    return vector


def _shift_preview_vector(day_index: int, template: Dict[str, Any], total_hours: int = SHIFT_PREVIEW_HOURS) -> List[float]:
    total_hours = max(24, int(total_hours or SHIFT_PREVIEW_HOURS))
    vector = [0.0 for _ in range(total_hours)]
    day_start = int(day_index) * 24 * 60
    start_abs = day_start + int(template.get("startMinute") or 0)
    end_abs = day_start + int(template.get("endMinute") or 0)
    for hour_index in range(total_hours):
        hour_start = hour_index * 60
        hour_end = hour_start + 60
        overlap = max(0, min(end_abs, hour_end) - max(start_abs, hour_start))
        if overlap <= 0:
            continue
        vector[hour_index] = round(overlap / 60, 4)
    return vector


def _shift_preview_active_items(vector: List[float]) -> List[tuple]:
    return [
        (index, float(amount or 0))
        for index, amount in enumerate(vector or [])
        if float(amount or 0) > 0
    ]


def _shift_preview_need_weight(hour_index: int) -> float:
    hour = int(hour_index) % 24
    if FREEFORM_DEEP_NIGHT_START_MINUTE // 60 <= hour < FREEFORM_DEEP_NIGHT_END_MINUTE // 60:
        return SHIFT_PREVIEW_DEEP_NIGHT_NEED_WEIGHT
    return 1.0


def _shift_preview_score(
    target: List[float],
    coverage: List[float],
    vector: List[float],
    strategy: Optional[Dict[str, Any]] = None,
    active_items: Optional[List[tuple]] = None,
) -> Dict[str, float]:
    strategy = strategy or SHIFT_PREVIEW_GREEDY_STRATEGIES[0]
    covered_need = 0.0
    weighted_need = 0.0
    added_over = 0.0
    active = 0.0
    items = active_items if active_items is not None else _shift_preview_active_items(vector)
    for index, amount in items:
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
        weighted_need += closed * (1.0 + min(need, 10.0) * 0.04) * _shift_preview_need_weight(index)
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


def _freeform_shift_duration_preference(rate: Any, duration_minutes: int) -> float:
    duration = int(duration_minutes or 0)
    if duration <= 0:
        return 0.0
    nearest_preferred_distance = min(
        abs(duration - preferred)
        for preferred in FREEFORM_PREFERRED_SHIFT_DURATIONS
    )
    rate_key = _resource_rate_key(rate)
    target_duration = FREEFORM_RATE_TARGET_DURATIONS.get(rate_key, FREEFORM_SHIFT_MAX_MINUTES)
    rate_distance = abs(duration - target_duration)
    exact_preferred_bonus = 1.7 if nearest_preferred_distance == 0 else 0.0
    preferred_bonus = max(0.0, 1.2 - (nearest_preferred_distance / 60.0) * 0.35)
    rate_bonus = max(0.0, 1.4 - (rate_distance / 60.0) * 0.30)
    return round(exact_preferred_bonus + preferred_bonus + rate_bonus, 4)


def _freeform_shift_template(rate: float, start_minute: int, end_minute: int, label: Optional[str] = None) -> Dict[str, Any]:
    rate = _resource_rate_value(rate)
    start_minute = int(start_minute)
    end_minute = int(end_minute)
    duration_minutes = max(0, end_minute - start_minute)
    shift_label = label or f"{_format_minutes_hhmm(start_minute)}-{_format_minutes_hhmm(end_minute)}"
    template_id = _shift_template_id(rate, f"free-{shift_label}", start_minute, end_minute)
    return {
        "id": template_id,
        "rate": rate,
        "label": shift_label,
        "start": _format_minutes_hhmm(start_minute),
        "end": _format_minutes_hhmm(end_minute),
        "startMinute": start_minute,
        "endMinute": end_minute,
        "durationMinutes": duration_minutes,
        "overnight": end_minute > 24 * 60,
        "enabled": True,
        "sortOrder": 0,
        "source": "freeform",
        "preferenceScore": _freeform_shift_duration_preference(rate, duration_minutes),
    }


def _is_freeform_regular_shift_allowed(start_minute: int, end_minute: int) -> bool:
    start_minute = int(start_minute)
    end_minute = int(end_minute)
    for night_start in (FREEFORM_DEEP_NIGHT_START_MINUTE, FREEFORM_DEEP_NIGHT_START_MINUTE + 24 * 60):
        night_end = night_start + (FREEFORM_DEEP_NIGHT_END_MINUTE - FREEFORM_DEEP_NIGHT_START_MINUTE)
        if max(start_minute, night_start) < min(end_minute, night_end):
            return False
    if end_minute <= 24 * 60:
        return True
    if end_minute > FREEFORM_REGULAR_OVERNIGHT_MAX_END_MINUTE:
        return False
    end_clock_minute = end_minute % (24 * 60)
    end_clock_hour = end_clock_minute // 60
    if end_clock_minute % 60 != 0:
        return False
    return end_clock_hour in FREEFORM_REGULAR_OVERNIGHT_END_CLOCK_HOURS


def _build_freeform_shift_templates(rate_capacity: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for rate in RESOURCE_RATE_VALUES:
        rate_key = _resource_rate_key(rate)
        if int((rate_capacity.get(rate_key) or {}).get("weekly_shift_capacity") or 0) <= 0:
            continue
        durations = FREEFORM_RATE_DURATION_OPTIONS.get(rate_key) or FREEFORM_PREFERRED_SHIFT_DURATIONS
        for start_minute in range(0, 24 * 60, FREEFORM_SHIFT_START_STEP_MINUTES):
            for duration_minutes in durations:
                end_minute = start_minute + duration_minutes
                if not _is_freeform_regular_shift_allowed(start_minute, end_minute):
                    continue
                result.append(_freeform_shift_template(rate, start_minute, end_minute))
        if rate_key == "1":
            night_template = _freeform_shift_template(
                rate,
                FREEFORM_NIGHT_SHIFT_START_MINUTE,
                FREEFORM_NIGHT_SHIFT_END_MINUTE,
                label=FREEFORM_NIGHT_SHIFT_LABEL,
            )
            night_template["preferenceScore"] += 2.0
            result.append(night_template)
    return result


def _build_shift_preview_candidates(
    days: List[Dict[str, Any]],
    templates: List[Dict[str, Any]],
    rate_capacity: Dict[str, Dict[str, Any]],
    source: str,
    total_hours: Optional[int] = None,
) -> List[Dict[str, Any]]:
    candidates = []
    preview_hours = max(24, int(total_hours or max(1, len(days)) * 24))
    for day_index in range(len(days)):
        for template in templates:
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
            vector = _shift_preview_vector(day_index, template_with_breaks, preview_hours)
            if sum(vector) <= 0:
                continue
            presence_vector = _shift_preview_presence_vector(day_index, template_with_breaks, preview_hours)
            candidates.append({
                "dayIndex": day_index,
                "template": template_with_breaks,
                "rateKey": rate_key,
                "source": source,
                "preferenceScore": _to_float(template.get("preferenceScore")),
                "vector": vector,
                "activeVector": _shift_preview_active_items(vector),
                "presenceVector": presence_vector,
                "activePresenceVector": _shift_preview_active_items(presence_vector),
            })
    return candidates


def _shift_preview_day_deficit(target: List[float], coverage: List[float], day_index: int) -> float:
    start = max(0, int(day_index) * 24)
    end = min(len(target), start + 24)
    return sum(max(0.0, float(target[index] or 0) - float(coverage[index] or 0)) for index in range(start, end))


def _shift_preview_totals(
    target: List[float],
    coverage: List[float],
    raw_target: Optional[List[float]] = None,
    base_raw_target: Optional[List[float]] = None,
) -> Dict[str, Any]:
    effective_raw_target = raw_target if raw_target is not None else target
    effective_base_raw_target = base_raw_target if base_raw_target is not None else effective_raw_target
    total_needed = sum(float(item or 0) for item in target)
    real_needed = sum(float(item or 0) for item in effective_raw_target)
    base_real_needed = sum(float(item or 0) for item in effective_base_raw_target)
    incident_uplift_needed = sum(
        max(
            0.0,
            float(effective_raw_target[index] or 0) - float(effective_base_raw_target[index] or 0),
        )
        for index in range(len(target))
    )
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
        "baseRealNeededFteHours": round(base_real_needed, 4),
        "incidentUpliftFteHours": round(incident_uplift_needed, 4),
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
                for index in range(len(target))
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
    initial_coverage: Optional[List[float]] = None,
    initial_selected: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    total_hours = len(target)
    coverage = [
        round(float((initial_coverage or [])[index] if initial_coverage and index < len(initial_coverage) else 0.0), 4)
        for index in range(total_hours)
    ]
    selected = []
    usage_state = _shift_preview_usage_state(initial_selected, total_hours)
    weekly_usage = usage_state["weekly_usage"]
    daily_usage = usage_state["daily_usage"]
    hourly_usage = usage_state["hourly_usage"]
    total_weekly_capacity = sum(int(item.get("weekly_shift_capacity") or 0) for item in rate_capacity.values())
    used_weekly_capacity = sum(int(value or 0) for value in weekly_usage.values())
    remaining_weekly_capacity = max(0, total_weekly_capacity - used_weekly_capacity)
    remaining_deficit = sum(max(0.0, float(target[index] or 0) - float(coverage[index] or 0)) for index in range(total_hours))
    target_based_limit = max(0, min(800, int(remaining_deficit / 3) + 80))
    max_shifts = min(target_based_limit, remaining_weekly_capacity)
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
                for index, amount in (candidate.get("activePresenceVector") or [])
            ):
                continue

            stats = _shift_preview_score(
                target,
                coverage,
                candidate["vector"],
                strategy,
                candidate.get("activeVector"),
            )
            if stats["covered_need"] <= min_covered_need:
                continue
            day_deficit = _shift_preview_day_deficit(target, coverage, day_index)
            preference_score = float(candidate.get("preferenceScore") or 0)
            stats = {
                **stats,
                "day_deficit": round(day_deficit, 4),
                "preference_score": round(preference_score, 4),
                "score": stats["score"] + min(day_deficit, 120.0) * day_deficit_weight + preference_score,
            }
            if stats["score"] <= min_score:
                continue
            rank = (
                round(float(stats["score"] or 0), 6),
                round(float(stats["covered_need"] or 0), 6),
                round(preference_score, 6),
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
            "presenceVector": list(best.get("presenceVector") or []),
            "source": best.get("source"),
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
        if sum(max(0.0, target[index] - coverage[index]) for index in range(total_hours)) <= 0.01:
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


def _shift_preview_status_name(status: int) -> str:
    if cp_model is None:
        return "unavailable"
    status_names = {
        cp_model.OPTIMAL: "optimal",
        cp_model.FEASIBLE: "feasible",
        cp_model.INFEASIBLE: "infeasible",
        cp_model.MODEL_INVALID: "model_invalid",
        cp_model.UNKNOWN: "unknown",
    }
    return status_names.get(status, str(status))


def _run_shift_preview_cp_sat_strategy(
    target: List[float],
    candidates: List[Dict[str, Any]],
    rate_capacity: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if cp_model is None or not candidates:
        return None

    total_hours = len(target)
    model = cp_model.CpModel()
    selected_vars = [
        model.NewBoolVar(f"shift_{index}")
        for index in range(len(candidates))
    ]
    objective_terms = []

    by_rate = defaultdict(list)
    by_day_rate = defaultdict(list)
    by_hour_rate = defaultdict(list)
    vector_minutes_by_candidate = []

    for index, candidate in enumerate(candidates):
        rate_key = candidate["rateKey"]
        day_index = int(candidate.get("dayIndex") or 0)
        by_rate[rate_key].append(index)
        by_day_rate[(day_index, rate_key)].append(index)
        vector_minutes = [
            int(round(float(amount or 0) * 60))
            for amount in (candidate.get("vector") or [])
        ]
        presence_minutes = [
            int(round(float(amount or 0) * 60))
            for amount in (candidate.get("presenceVector") or [])
        ]
        vector_minutes_by_candidate.append(vector_minutes)
        for hour_index, amount in enumerate(presence_minutes):
            if amount > 0:
                by_hour_rate[(hour_index, rate_key)].append((index, amount))
        preference_bonus = int(
            round(float(candidate.get("preferenceScore") or 0) * SHIFT_PREVIEW_CP_SAT_PREFERENCE_WEIGHT)
        )
        shift_weight = SHIFT_PREVIEW_CP_SAT_SHIFT_WEIGHT - preference_bonus
        objective_terms.append(selected_vars[index] * shift_weight)

    for rate_key, indexes in by_rate.items():
        capacity = int((rate_capacity.get(rate_key) or {}).get("weekly_shift_capacity") or 0)
        model.Add(sum(selected_vars[index] for index in indexes) <= capacity)

    for (_day_index, rate_key), indexes in by_day_rate.items():
        capacity = int((rate_capacity.get(rate_key) or {}).get("daily_shift_capacity") or 0)
        model.Add(sum(selected_vars[index] for index in indexes) <= capacity)

    for (_hour_index, rate_key), items in by_hour_rate.items():
        capacity = int((rate_capacity.get(rate_key) or {}).get("daily_shift_capacity") or 0)
        active_minutes = sum(selected_vars[index] * amount for index, amount in items)
        model.Add(active_minutes <= capacity * 60)

    for hour_index in range(total_hours):
        target_minutes = int(round(float(target[hour_index] or 0) * 60))
        coverage_terms = [
            selected_vars[index] * vector_minutes_by_candidate[index][hour_index]
            for index in range(len(candidates))
            if hour_index < len(vector_minutes_by_candidate[index])
            and vector_minutes_by_candidate[index][hour_index] > 0
        ]
        max_coverage_minutes = sum(
            vector_minutes_by_candidate[index][hour_index]
            for index in range(len(candidates))
            if hour_index < len(vector_minutes_by_candidate[index])
        )
        deficit = model.NewIntVar(0, max(target_minutes, 0), f"deficit_{hour_index}")
        over = model.NewIntVar(
            0,
            max(max_coverage_minutes, target_minutes, 0),
            f"over_{hour_index}",
        )
        model.Add(sum(coverage_terms) + deficit - over == target_minutes)

        deficit_weight = SHIFT_PREVIEW_CP_SAT_DEFICIT_WEIGHT
        if _shift_preview_need_weight(hour_index) > 1.0:
            deficit_weight = int(round(deficit_weight * _shift_preview_need_weight(hour_index)))
        objective_terms.append(deficit * deficit_weight)
        objective_terms.append(over * SHIFT_PREVIEW_CP_SAT_OVER_WEIGHT)

    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SHIFT_PREVIEW_CP_SAT_TIME_LIMIT_SECONDS
    solver.parameters.num_search_workers = SHIFT_PREVIEW_CP_SAT_SEARCH_WORKERS
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    coverage = [0.0 for _ in range(total_hours)]
    selected = []
    status_name = _shift_preview_status_name(status)
    for index, candidate in enumerate(candidates):
        if not solver.BooleanValue(selected_vars[index]):
            continue
        for hour_index, amount in enumerate(candidate["vector"]):
            coverage[hour_index] = round(float(coverage[hour_index] or 0) + float(amount or 0), 4)
        selected.append({
            "dayIndex": candidate["dayIndex"],
            "template": candidate["template"],
            "vector": list(candidate["vector"]),
            "presenceVector": list(candidate.get("presenceVector") or []),
            "source": candidate.get("source"),
            "score": {
                "method": "cp_sat",
                "status": status_name,
                "objective": round(float(solver.ObjectiveValue()), 4),
                "preference_score": round(float(candidate.get("preferenceScore") or 0), 4),
            },
        })

    pruned = _shift_preview_prune_selected(target, coverage, selected, prune_mode="objective")
    totals = pruned["totals"]
    selected = pruned["selected"]
    return {
        "method": f"cp_sat_{status_name}",
        "selected": selected,
        "coverage": pruned["coverage"],
        "totals": totals,
        "quality": _shift_preview_quality_score(totals, len(selected)),
    }


def _select_shift_preview_strategy(
    target: List[float],
    candidates: List[Dict[str, Any]],
    rate_capacity: Dict[str, Dict[str, Any]],
    initial_coverage: Optional[List[float]] = None,
    initial_selected: Optional[List[Dict[str, Any]]] = None,
    allow_cp_sat: bool = True,
) -> Dict[str, Any]:
    cp_sat_result = None
    if allow_cp_sat and not initial_coverage and not initial_selected:
        cp_sat_result = _run_shift_preview_cp_sat_strategy(target, candidates, rate_capacity)
    strategy_results = []
    if cp_sat_result is not None:
        strategy_results.append(cp_sat_result)
    strategy_results.extend([
        _run_shift_preview_greedy_strategy(
            target,
            candidates,
            rate_capacity,
            strategy,
            initial_coverage=initial_coverage,
            initial_selected=initial_selected,
        )
        for strategy in SHIFT_PREVIEW_GREEDY_STRATEGIES
    ])
    best_result = min(
        strategy_results,
        key=lambda item: (
            round(float(item.get("quality") or 0), 6),
            round(float((item.get("totals") or {}).get("deficitFteHours") or 0), 4),
            round(float((item.get("totals") or {}).get("overFteHours") or 0), 4),
            len(item.get("selected") or []),
        ),
    )
    return {
        "best": best_result,
        "strategy_results": strategy_results,
    }


def _shift_preview_capacity_summary(
    rate_capacity: Dict[str, Dict[str, Any]],
    selected: List[Dict[str, Any]],
    day_count: int = 7,
) -> List[Dict[str, Any]]:
    day_count = max(1, int(day_count or 1))
    final_usage = _shift_preview_usage_by_rate(selected, day_count)
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
            "dailyShiftsUsed": usage.get("daily_shifts") or [0 for _ in range(day_count)],
        })
    return capacity_summary


def _shift_preview_days(
    days: List[Dict[str, Any]],
    target: List[float],
    raw_target: List[float],
    coverage: List[float],
    selected: List[Dict[str, Any]],
    base_target: Optional[List[float]] = None,
    base_raw_target: Optional[List[float]] = None,
    uplift_raw_target: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    shifts_by_day = defaultdict(list)
    for index, selected_item in enumerate(selected, start=1):
        template = selected_item["template"]
        day_index = int(selected_item["dayIndex"])
        source = str(selected_item.get("source") or template.get("source") or "template")
        is_incident_uplift = source == "incident_uplift"
        shift = {
            "id": f"gen-{source}-{day_index}-{index}",
            "templateId": template["id"],
            "rate": template["rate"],
            "label": template["label"],
            "start": template["start"],
            "end": template["end"],
            "startMinute": int(template["startMinute"]),
            "endMinute": int(template["endMinute"]),
            "durationMinutes": int(template["durationMinutes"]),
            "overnight": bool(template.get("overnight")),
            "source": source,
            "baseSource": template.get("source") or source,
            "isIncidentUplift": is_incident_uplift,
            "tone": "emerald" if is_incident_uplift else "blue",
            "breaks": template.get("breaks") or [],
        }
        shifts_by_day[day_index].append(shift)

    preview_days = []
    for day_index, day in enumerate(days):
        coverage_rows = []
        for hour in range(24):
            absolute_hour = day_index * 24 + hour
            needed = float(target[absolute_hour] or 0)
            covered = float(coverage[absolute_hour] or 0)
            covered_rounded = _round_fte_to_half(covered)
            base_needed = float((base_target or target)[absolute_hour] or 0)
            base_raw_needed = float((base_raw_target or raw_target)[absolute_hour] or 0)
            uplift_needed = float((uplift_raw_target or [0.0 for _ in range(len(target))])[absolute_hour] or 0)
            coverage_rows.append({
                "hour": hour,
                "needed": round(needed, 4),
                "rawNeeded": round(float(raw_target[absolute_hour] or 0), 4),
                "realNeeded": round(float(raw_target[absolute_hour] or 0), 4),
                "baseNeeded": round(base_needed, 4),
                "baseRawNeeded": round(base_raw_needed, 4),
                "incidentUpliftNeeded": round(uplift_needed, 4),
                "incidentAdjustedNeeded": round(float(raw_target[absolute_hour] or 0), 4),
                "covered": round(covered, 4),
                "coveredRounded": round(covered_rounded, 4),
                "deficit": round(max(0.0, needed - covered_rounded), 4),
                "over": round(max(0.0, covered_rounded - needed), 4),
            })
        day_target = target[day_index * 24:(day_index + 1) * 24]
        day_raw_target = raw_target[day_index * 24:(day_index + 1) * 24]
        day_base_raw_target = (base_raw_target or raw_target)[day_index * 24:(day_index + 1) * 24]
        day_coverage = coverage[day_index * 24:(day_index + 1) * 24]
        preview_days.append({
            "date": day.get("forecast_date"),
            "weekday": day.get("weekday"),
            "short": day.get("short"),
            "label": day.get("label"),
            "coverage": coverage_rows,
            "shifts": shifts_by_day.get(day_index, []),
            "stats": _shift_preview_totals(day_target, day_coverage, day_raw_target, day_base_raw_target),
        })
    return preview_days


def _build_schedule_preview_variant(
    key: str,
    label: str,
    days: List[Dict[str, Any]],
    target: List[float],
    raw_target: List[float],
    candidates: List[Dict[str, Any]],
    rate_capacity: Dict[str, Dict[str, Any]],
    adjusted_target: Optional[List[float]] = None,
    adjusted_raw_target: Optional[List[float]] = None,
    uplift_raw_target: Optional[List[float]] = None,
) -> Dict[str, Any]:
    selected_result = _select_shift_preview_strategy(target, candidates, rate_capacity)
    best_result = selected_result["best"]
    base_selected = best_result["selected"]
    base_coverage = best_result["coverage"]
    effective_target = adjusted_target if adjusted_target is not None else target
    effective_raw_target = adjusted_raw_target if adjusted_raw_target is not None else raw_target
    has_incident_uplift = any(float(item or 0) > 0.001 for item in (uplift_raw_target or []))
    incident_result = None
    if has_incident_uplift:
        incident_result = _select_shift_preview_strategy(
            effective_target,
            candidates,
            rate_capacity,
            initial_coverage=base_coverage,
            initial_selected=base_selected,
            allow_cp_sat=False,
        )
        incident_selected = [
            {
                **item,
                "source": "incident_uplift",
            }
            for item in ((incident_result.get("best") or {}).get("selected") or [])
        ]
        selected = [*base_selected, *incident_selected]
        coverage = (incident_result.get("best") or {}).get("coverage") or base_coverage
    else:
        incident_selected = []
        selected = base_selected
        coverage = base_coverage
    return {
        "key": key,
        "label": label,
        "days": _shift_preview_days(
            days,
            effective_target,
            effective_raw_target,
            coverage,
            selected,
            base_target=target,
            base_raw_target=raw_target,
            uplift_raw_target=uplift_raw_target,
        ),
        "summary": _shift_preview_totals(effective_target, coverage, effective_raw_target, raw_target),
        "capacityRates": _shift_preview_capacity_summary(rate_capacity, selected, len(days)),
        "generation": {
            "variant": key,
            "method": best_result.get("method"),
            "baseMethod": best_result.get("method"),
            "incidentUpliftMethod": ((incident_result or {}).get("best") or {}).get("method"),
            "baseShifts": len(base_selected),
            "incidentUpliftShifts": len(incident_selected),
            "strategiesTried": len(selected_result["strategy_results"]),
            "incidentStrategiesTried": len((incident_result or {}).get("strategy_results") or []),
            "candidateCount": len(candidates),
            "qualityScore": round(float(_shift_preview_quality_score(_shift_preview_totals(effective_target, coverage, effective_raw_target, raw_target), len(selected)) or 0), 4),
            "strategySummaries": [
                {
                    "method": item.get("method"),
                    "shifts": len(item.get("selected") or []),
                    "deficitFteHours": (item.get("totals") or {}).get("deficitFteHours"),
                    "overFteHours": (item.get("totals") or {}).get("overFteHours"),
                    "qualityScore": round(float(item.get("quality") or 0), 4),
                }
                for item in selected_result["strategy_results"]
            ],
            "incidentStrategySummaries": [
                {
                    "method": item.get("method"),
                    "shifts": len(item.get("selected") or []),
                    "deficitFteHours": (item.get("totals") or {}).get("deficitFteHours"),
                    "overFteHours": (item.get("totals") or {}).get("overFteHours"),
                    "qualityScore": round(float(item.get("quality") or 0), 4),
                }
                for item in ((incident_result or {}).get("strategy_results") or [])
            ],
        },
    }


def _schedule_preview_variant_rank(variant: Dict[str, Any]) -> tuple:
    summary = variant.get("summary") or {}
    generation = variant.get("generation") or {}
    quality = _to_float(
        generation.get("qualityScore"),
        _shift_preview_quality_score(summary),
    )
    variant_bias = 0 if str(variant.get("key") or "") == "templates" else 1
    return (
        round(quality, 6),
        round(_to_float(summary.get("deficitFteHours")), 4),
        round(_to_float(summary.get("overFteHours")), 4),
        variant_bias,
    )


def _work_days_per_operator_for_period(day_count: int) -> int:
    day_count = max(1, int(day_count or 1))
    return min(day_count, max(1, int(math.ceil(WORK_DAYS_PER_OPERATOR_WEEK * day_count / 7))))


def _operator_capacity_for_period(
    operator_capacity: Optional[Dict[str, Any]],
    day_count: int,
) -> Optional[Dict[str, Any]]:
    if not operator_capacity:
        return operator_capacity
    work_days = _work_days_per_operator_for_period(day_count)
    rate_capacity = []
    for item in operator_capacity.get("rate_capacity") or []:
        count = max(0, _to_int(item.get("count")))
        rate_capacity.append({
            **item,
            "daily_shift_capacity": count,
            "weekly_shift_capacity": count * work_days,
        })
    return {
        **operator_capacity,
        "period_day_count": max(1, int(day_count or 1)),
        "period_work_days_per_operator": work_days,
        "rate_capacity": rate_capacity,
    }


def _generate_schedule_preview_from_forecast(
    forecast_payload: Dict[str, Any],
    templates: List[Dict[str, Any]],
    operator_capacity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    days = forecast_payload.get("days") or []
    day_count = max(1, len(days))
    total_hours = day_count * 24
    target = [0.0 for _ in range(total_hours)]
    raw_target = [0.0 for _ in range(total_hours)]
    adjusted_target = [0.0 for _ in range(total_hours)]
    adjusted_raw_target = [0.0 for _ in range(total_hours)]
    uplift_raw_target = [0.0 for _ in range(total_hours)]
    for day_index, day in enumerate(days):
        for row in day.get("hourly_forecast") or []:
            hour = _to_int(row.get("hour"), -1)
            if hour < 0 or hour > 23:
                continue
            absolute_hour = day_index * 24 + hour
            raw_value = max(0.0, _to_float(row.get("forecast_fte")))
            uplift_value = max(0.0, _to_float(row.get("incident_uplift_fte")))
            adjusted_value = raw_value + uplift_value
            raw_target[absolute_hour] = round(raw_value, 4)
            target[absolute_hour] = float(_round_fte_to_half(raw_value))
            uplift_raw_target[absolute_hour] = round(uplift_value, 4)
            adjusted_raw_target[absolute_hour] = round(adjusted_value, 4)
            adjusted_target[absolute_hour] = float(_round_fte_to_half(adjusted_value))

    enabled_templates = [item for item in templates if item.get("enabled", True)]
    operator_capacity = _operator_capacity_for_period(operator_capacity, day_count)
    rate_capacity = _normalize_schedule_rate_capacity(operator_capacity)

    template_candidates = _build_shift_preview_candidates(days, enabled_templates, rate_capacity, "template", total_hours)
    freeform_templates = _build_freeform_shift_templates(rate_capacity)
    freeform_candidates = _build_shift_preview_candidates(days, freeform_templates, rate_capacity, "freeform", total_hours)
    template_variant = _build_schedule_preview_variant(
        "templates",
        "По шаблонам",
        days,
        target,
        raw_target,
        template_candidates,
        rate_capacity,
        adjusted_target=adjusted_target,
        adjusted_raw_target=adjusted_raw_target,
        uplift_raw_target=uplift_raw_target,
    )
    freeform_variant = _build_schedule_preview_variant(
        "freeform",
        "Без шаблонов",
        days,
        target,
        raw_target,
        freeform_candidates,
        rate_capacity,
        adjusted_target=adjusted_target,
        adjusted_raw_target=adjusted_raw_target,
        uplift_raw_target=uplift_raw_target,
    )
    variants = [template_variant, freeform_variant]
    default_variant = min(variants, key=_schedule_preview_variant_rank)

    return {
        "week_start": forecast_payload.get("week_start"),
        "week_end": forecast_payload.get("week_end"),
        "period_start": forecast_payload.get("period_start") or forecast_payload.get("week_start"),
        "period_end": forecast_payload.get("period_end") or forecast_payload.get("week_end"),
        "rounding": "math_half",
        "rounding_step": FTE_ROUNDING_STEP,
        "templates": templates,
        "capacity": {
            "workDaysPerOperatorWeek": WORK_DAYS_PER_OPERATOR_WEEK,
            "workDaysPerOperatorPeriod": _to_int((operator_capacity or {}).get("period_work_days_per_operator"), _work_days_per_operator_for_period(day_count)),
            "periodDayCount": day_count,
            "rates": default_variant["capacityRates"],
            "activeOperatorCount": _to_int((operator_capacity or {}).get("active_operator_count")),
            "currentOperatorFte": _to_float((operator_capacity or {}).get("current_operator_fte")),
            "selectedDirectionIds": (operator_capacity or {}).get("selected_direction_ids") or [],
        },
        "days": default_variant["days"],
        "summary": default_variant["summary"],
        "generation": default_variant["generation"],
        "incidentUplift": forecast_payload.get("incidentUplift") or {},
        "selectedVariant": default_variant["key"],
        "variants": variants,
    }


