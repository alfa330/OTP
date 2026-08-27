"""Расчет ресурсов для чат-направления.

Отличие от линии принципиальное: среднее время обработки НЕ используется. В Chat2Desk
`request_time` — это время жизни обращения от начала до закрытия (медиана ~1,6 часа),
а не работа оператора: чат висит открытым, пока клиент молчит. Умножать объём на такую
величину нельзя — модель попросит в разы больше людей, чем нужно.

Вместо этого считаем от ЦЕЛИ по сервису:

    Нужно чатников в час = Чаты в час / Чатов в час на одного чатника

где «чатов в час на одного чатника» — ёмкость при заданной цели по «ответу внутри чата».
Ёмкость меряется по факту: нагрузка (чаты за час ÷ отработанные чатнико-часы) против
реального времени ответа. Так же поступает Genesys Cloud WFM — снимает перекрытие
одновременных чатов и считает усилие, а не сырой AHT.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .common import (
    WEEKDAYS_RU, WORK_DAYS_PER_OPERATOR_WEEK,
    _round_fte_to_half, _to_float, _to_int,
)


DEFAULT_CHAT_SETTINGS = {
    "target_reply_seconds": 300,
    "capacity_per_hour": 17.0,
    "shrinkage_coeff": 0.90,
    "weekly_hours_per_operator": 40.0,
    "base_weeks": 2,
}

# Границы вводных: за ними расчёт теряет смысл, а не просто становится неточным.
CHAT_SETTINGS_LIMITS = {
    "target_reply_seconds": (30, 3600),
    "capacity_per_hour": (0.5, 60.0),
    "shrinkage_coeff": (0.1, 1.0),
    "weekly_hours_per_operator": (1.0, 168.0),
    "base_weeks": (1, 8),
}

CHAT_REQUEST_TYPE = "common"

# Направление «Чат менеджер». Штат для расчёта берём по нему, а не по всем операторам.
CHAT_DIRECTION_NAME_PATTERN = "%чат%"

# Смены чат-направления взяты из боевого графика владельца «График чат (6).xlsx»:
# это НЕ дефолтный набор линии — там свой состав (нет 7*16 и 9*18, зато есть 12*21).
# Разложены по длительности: 9–12 ч это полная ставка, 6,5 ч — 0,75.
CHAT_SHIFT_TEMPLATE_LABELS = {
    1.0: [
        "8*17",
        "10*19",
        "11*20",
        "12*21",
        "13*22",
        "15*00",
        "17*02",
        "20*08",
    ],
    0.75: [
        "9*15/30",
        "11*17/30",
        "12*18/30",
        "13*19/30",
    ],
}

# В чате только две ставки — 1,0 и 0,75 (решение владельца 27.08.2026).
# Половинной ставки здесь нет, поэтому и шаблонов на неё быть не должно.
CHAT_RATES = (1.0, 0.75)


def get_chat_shift_templates() -> Dict[str, Any]:
    """Шаблоны смен чата — из боевого графика, а не дефолты линии."""
    from .schedule_generation import _normalize_shift_template

    templates = []
    index = 0
    for rate, labels in CHAT_SHIFT_TEMPLATE_LABELS.items():
        for label in labels:
            templates.append(_normalize_shift_template(label, fallback_rate=rate, index=index))
            index += 1
    return {
        "templates": templates,
        "rates": [{"rate": 1.0, "label": "1"}, {"rate": 0.75, "label": "0.75"}],
        "source": "График чат (6).xlsx",
    }


def _clamp(value: float, key: str) -> float:
    low, high = CHAT_SETTINGS_LIMITS[key]
    return min(max(float(value), float(low)), float(high))


def _as_chat_settings(row: Any) -> Dict[str, Any]:
    if not row:
        return dict(DEFAULT_CHAT_SETTINGS)
    return {
        "target_reply_seconds": _to_int(row[0], DEFAULT_CHAT_SETTINGS["target_reply_seconds"]),
        "capacity_per_hour": _to_float(row[1], DEFAULT_CHAT_SETTINGS["capacity_per_hour"]),
        "shrinkage_coeff": _to_float(row[2], DEFAULT_CHAT_SETTINGS["shrinkage_coeff"]),
        "weekly_hours_per_operator": _to_float(
            row[3], DEFAULT_CHAT_SETTINGS["weekly_hours_per_operator"]),
        "base_weeks": _to_int(row[4], DEFAULT_CHAT_SETTINGS["base_weeks"]),
    }


def _get_chat_settings_tx(cursor) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT target_reply_seconds, capacity_per_hour, shrinkage_coeff,
               weekly_hours_per_operator, base_weeks
        FROM resource_chat_settings
        WHERE id = 1
        """
    )
    return _as_chat_settings(cursor.fetchone())


def get_chat_settings(db) -> Dict[str, Any]:
    with db._get_cursor() as cursor:
        return _get_chat_settings_tx(cursor)


def update_chat_settings(db, payload: Dict[str, Any],
                         user_id: Optional[int] = None) -> Dict[str, Any]:
    current = get_chat_settings(db)
    nxt = dict(current)
    for key in ("capacity_per_hour", "shrinkage_coeff", "weekly_hours_per_operator"):
        if key in payload:
            nxt[key] = _clamp(_to_float(payload.get(key), current[key]), key)
    for key in ("target_reply_seconds", "base_weeks"):
        if key in payload:
            nxt[key] = int(_clamp(_to_int(payload.get(key), current[key]), key))
    # Долю можно прислать и процентами — 90 значит 0,9.
    if nxt["shrinkage_coeff"] > 1:
        nxt["shrinkage_coeff"] = _clamp(nxt["shrinkage_coeff"] / 100.0, "shrinkage_coeff")

    with db._get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE resource_chat_settings
            SET target_reply_seconds = %s,
                capacity_per_hour = %s,
                shrinkage_coeff = %s,
                weekly_hours_per_operator = %s,
                base_weeks = %s,
                updated_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (nxt["target_reply_seconds"], nxt["capacity_per_hour"], nxt["shrinkage_coeff"],
             nxt["weekly_hours_per_operator"], nxt["base_weeks"], user_id),
        )
    return nxt


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError("INVALID_CHAT_DATE")


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _latest_chat_day_tx(cursor) -> Optional[date]:
    cursor.execute("SELECT MAX(day) FROM c2d_requests WHERE request_type = %s",
                   (CHAT_REQUEST_TYPE,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def _hourly_volume_tx(cursor, day_from: date, day_to: date) -> Dict[str, Dict[int, int]]:
    """Чаты по часам за период. Час берётся из request_start — он уже местный (Алматы)."""
    cursor.execute(
        """
        SELECT day, EXTRACT(HOUR FROM request_start)::int AS hh, COUNT(*)
        FROM c2d_requests
        WHERE request_type = %s
          AND request_start IS NOT NULL
          AND day BETWEEN %s AND %s
        GROUP BY 1, 2
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    out: Dict[str, Dict[int, int]] = {}
    for day_value, hour, count in cursor.fetchall():
        key = day_value.isoformat()
        out.setdefault(key, {})[int(hour)] = int(count)
    return out


MAX_BASE_LOOKBACK_WEEKS = 12


def _base_week_starts(anchor_week_start: date, base_weeks: int) -> List[date]:
    """Недели-основания без учёта данных: `base_weeks` недель ПЕРЕД целевой."""
    return [anchor_week_start - timedelta(weeks=offset)
            for offset in range(1, max(1, int(base_weeks)) + 1)]


def _covered_base_week_starts(anchor_week_start: date, base_weeks: int,
                              covered_days: set) -> Dict[str, Any]:
    """Берём только ПОЛНЫЕ недели: все 7 дней должны быть в данных.

    Иначе выходит перекос: свежая неделя обрывается на середине (ретеншн
    c2d_requests или ещё не наступивший день), и тогда понедельник считается по двум
    неделям, а пятница — по одной. Неполные недели пропускаем и уходим глубже.
    """
    used: List[date] = []
    skipped: List[Dict[str, Any]] = []
    wanted = max(1, int(base_weeks))
    for offset in range(1, MAX_BASE_LOOKBACK_WEEKS + 1):
        if len(used) >= wanted:
            break
        candidate = anchor_week_start - timedelta(weeks=offset)
        days = [candidate + timedelta(days=i) for i in range(7)]
        missing = [d.isoformat() for d in days if d.isoformat() not in covered_days]
        if missing:
            skipped.append({"week_start": candidate.isoformat(), "missing_days": missing})
            continue
        used.append(candidate)
    if not used:
        # Полных недель нет вообще — честнее считать по тому, что есть, чем отдать пусто.
        used = _base_week_starts(anchor_week_start, wanted)
    return {"used": used, "skipped": skipped}


def _covered_days_tx(cursor, day_from: date, day_to: date) -> set:
    cursor.execute(
        """
        SELECT DISTINCT day FROM c2d_requests
        WHERE request_type = %s AND day BETWEEN %s AND %s
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    return {row[0].isoformat() for row in cursor.fetchall()}


MAX_FORECAST_PERIOD_DAYS = 31


def build_chat_forecast(db, week_start_value: Any = None,
                        settings: Optional[Dict[str, Any]] = None,
                        period_end_value: Any = None) -> Dict[str, Any]:
    """Почасовой прогноз: среднее того же дня недели в базовых неделях.

    Период по умолчанию — неделя от `week_start_value`. Планировщик графиков
    умеет работать и с произвольным отрезком, поэтому принимаем `period_end_value`.
    """
    with db._get_cursor() as cursor:
        settings = dict(settings or _get_chat_settings_tx(cursor))
        latest = _latest_chat_day_tx(cursor)
        requested = _parse_date(week_start_value)
        if requested is not None:
            period_start = requested
        elif latest is not None:
            period_start = _week_start(latest) + timedelta(weeks=1)
        else:
            period_start = _week_start(date.today()) + timedelta(weeks=1)

        period_end = _parse_date(period_end_value) or (period_start + timedelta(days=6))
        if period_end < period_start:
            period_start, period_end = period_end, period_start
        span = (period_end - period_start).days + 1
        if span > MAX_FORECAST_PERIOD_DAYS:
            period_end = period_start + timedelta(days=MAX_FORECAST_PERIOD_DAYS - 1)
            span = MAX_FORECAST_PERIOD_DAYS

        # Базовые недели отсчитываем от недели, в которую попадает начало периода.
        target_week = _week_start(period_start)
        lookback_from = target_week - timedelta(weeks=MAX_BASE_LOOKBACK_WEEKS)
        covered = _covered_days_tx(cursor, lookback_from, target_week - timedelta(days=1))
        chosen = _covered_base_week_starts(target_week, settings["base_weeks"], covered)
        base_weeks = chosen["used"]
        skipped_weeks = chosen["skipped"]
        oldest = min(base_weeks)
        newest_end = max(base_weeks) + timedelta(days=6)
        hourly = _hourly_volume_tx(cursor, oldest, newest_end)
        capacity_info = _chat_operator_capacity_tx(cursor)

    capacity = max(0.01, float(settings["capacity_per_hour"]))
    days: List[Dict[str, Any]] = []
    for offset in range(span):
        target_day = period_start + timedelta(days=offset)
        sources = []
        for base in base_weeks:
            # Сопоставляем по ДНЮ НЕДЕЛИ, а не по смещению в периоде: период может
            # начинаться не с понедельника, и тогда смещение уводит на чужой день.
            source_day = base + timedelta(days=target_day.weekday())
            per_hour = hourly.get(source_day.isoformat(), {})
            sources.append({
                "date": source_day.isoformat(),
                "chats": int(sum(per_hour.values())),
                "hourly": per_hour,
                "has_data": bool(per_hour),
            })
        used = [item for item in sources if item["has_data"]] or sources
        divisor = max(1, len(used))

        hourly_forecast = []
        for hour in range(24):
            chats = sum(item["hourly"].get(hour, 0) for item in used) / divisor
            required = chats / capacity
            hourly_forecast.append({
                "hour": hour,
                "forecast_chats": round(chats, 2),
                "forecast_fte": round(required, 4),
                "rounded_fte": float(_round_fte_to_half(required)),
                "incident_uplift_fte": 0.0,
            })

        weekday = WEEKDAYS_RU[target_day.weekday()]
        days.append({
            "forecast_date": target_day.isoformat(),
            "weekday": target_day.weekday(),
            "short": weekday["short"],
            "label": weekday["label"],
            "sources": sources,
            "used_source_count": divisor,
            "forecast_chats": round(sum(r["forecast_chats"] for r in hourly_forecast), 1),
            "forecast_fte_hours": round(sum(r["forecast_fte"] for r in hourly_forecast), 2),
            "peak_fte": round(max((r["forecast_fte"] for r in hourly_forecast), default=0.0), 2),
            "hourly_forecast": hourly_forecast,
        })

    total_chats = round(sum(d["forecast_chats"] for d in days), 1)
    total_fte_hours = round(sum(d["forecast_fte_hours"] for d in days), 2)
    weekly_hours = max(1.0, float(settings["weekly_hours_per_operator"]))
    shrink = min(max(float(settings["shrinkage_coeff"]), 0.01), 1.0)
    # Норма часов считается на длину периода, а не жёстко на неделю — как у линии.
    period_hours_per_operator = weekly_hours * span / 7
    operators = total_fte_hours / max(1.0, period_hours_per_operator)
    operators_with_shrinkage = operators / shrink
    current_fte = float(capacity_info.get("current_operator_fte") or 0.0)
    return {
        "week_start": period_start.isoformat(),
        "week_end": period_end.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "base_week_starts": [item.isoformat() for item in base_weeks],
        "skipped_base_weeks": skipped_weeks,
        "settings": settings,
        "days": days,
        "operator_capacity": capacity_info,
        "totals": {
            "forecast_chats": total_chats,
            "forecast_fte_hours": total_fte_hours,
            "period_days": span,
            "period_hours_per_operator": round(period_hours_per_operator, 2),
            "operators": round(operators, 2),
            "operators_with_shrinkage": round(operators_with_shrinkage, 2),
            "peak_fte": round(max((d["peak_fte"] for d in days), default=0.0), 2),
            "current_operator_fte": round(current_fte, 2),
            "operator_fte_gap": round(current_fte - operators_with_shrinkage, 2),
            "head_count": capacity_info.get("head_count", 0),
        },
    }


def _chat_operator_capacity_tx(cursor) -> Dict[str, Any]:
    """Текущий штат чат-направления по ставкам — аналог того, что линия берёт по своим."""
    cursor.execute(
        """
        SELECT COALESCE(u.rate, 1.0), COUNT(*)
        FROM users u
        JOIN directions d ON d.id = u.direction_id
        WHERE u.role = 'operator'
          AND COALESCE(u.status, 'working') = 'working'
          AND d.name ILIKE %s
        GROUP BY COALESCE(u.rate, 1.0)
        ORDER BY 1 DESC
        """,
        (CHAT_DIRECTION_NAME_PATTERN,),
    )
    rows = cursor.fetchall() or []
    rate_capacity = []
    head_count = 0
    current_fte = 0.0
    off_scale = []
    for rate_raw, count_raw in rows:
        rate = float(rate_raw or 1.0)
        count = int(count_raw or 0)
        if rate not in CHAT_RATES:
            # Ставку вне набора направления в расчёт не берём, но и не прячем:
            # это расхождение в карточках людей, и его должно быть видно.
            off_scale.append({"rate": rate, "count": count})
            continue
        head_count += count
        current_fte += count * rate
        rate_capacity.append({
            "rate": rate,
            "count": count,
            "daily_shift_capacity": count,
            "weekly_shift_capacity": count * WORK_DAYS_PER_OPERATOR_WEEK,
        })
    return {
        "head_count": head_count,
        "current_operator_fte": round(current_fte, 4),
        "rate_capacity": rate_capacity,
        "rates": list(CHAT_RATES),
        "off_scale_rates": off_scale,
    }


def _weekday_profile_tx(cursor, day_from: date, day_to: date) -> List[Dict[str, Any]]:
    """Средний профиль по дню недели и часу — то же, что «профиль» у линии."""
    cursor.execute(
        """
        SELECT EXTRACT(ISODOW FROM day)::int - 1 AS wd,
               EXTRACT(HOUR FROM request_start)::int AS hh,
               COUNT(*)::float / COUNT(DISTINCT day) AS avg_chats,
               COUNT(DISTINCT day) AS days
        FROM c2d_requests
        WHERE request_type = %s AND request_start IS NOT NULL
          AND day BETWEEN %s AND %s
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    by_weekday: Dict[int, Dict[str, Any]] = {}
    for weekday, hour, avg_chats, days in cursor.fetchall():
        entry = by_weekday.setdefault(int(weekday), {
            "weekday": int(weekday),
            "short": WEEKDAYS_RU[int(weekday)]["short"],
            "label": WEEKDAYS_RU[int(weekday)]["label"],
            "days_in_sample": int(days),
            "hourly": [],
        })
        entry["hourly"].append({"hour": int(hour), "avg_chats": round(float(avg_chats), 2)})
    for entry in by_weekday.values():
        entry["avg_chats"] = round(sum(item["avg_chats"] for item in entry["hourly"]), 1)
        entry["peak_hour"] = max(entry["hourly"], key=lambda x: x["avg_chats"])["hour"] \
            if entry["hourly"] else None
    return [by_weekday[key] for key in sorted(by_weekday)]


def _daily_history_tx(cursor, day_from: date, day_to: date) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT day, COUNT(*)
        FROM c2d_requests
        WHERE request_type = %s AND day BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 1
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    rows = []
    for day_value, count in cursor.fetchall():
        weekday = WEEKDAYS_RU[day_value.weekday()]
        rows.append({
            "date": day_value.isoformat(),
            "weekday": day_value.weekday(),
            "short": weekday["short"],
            "chats": int(count),
        })
    return rows


def _channel_split_tx(cursor, day_from: date, day_to: date) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT COALESCE(NULLIF(channel_name, ''), 'Без канала') AS channel, COUNT(*)
        FROM c2d_requests
        WHERE request_type = %s AND day BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 2 DESC
        """,
        (CHAT_REQUEST_TYPE, day_from, day_to),
    )
    rows = cursor.fetchall()
    total = sum(int(item[1]) for item in rows) or 1
    return [{"channel": item[0], "chats": int(item[1]),
             "share": round(int(item[1]) / total, 4)} for item in rows]


def get_chat_overview(db, week_start_value: Any = None,
                      history_days: Any = None) -> Dict[str, Any]:
    """Витрина раздела: история, прогноз на неделю, потребность и разбивка по каналам."""
    depth = _to_int(history_days, 45)
    depth = min(max(depth, 7), 120)
    forecast = build_chat_forecast(db, week_start_value)

    with db._get_cursor() as cursor:
        latest = _latest_chat_day_tx(cursor)
        if latest is None:
            history, channels, profile = [], [], []
            coverage = {"from": None, "to": None, "days": 0}
        else:
            day_from = latest - timedelta(days=depth - 1)
            history = _daily_history_tx(cursor, day_from, latest)
            base_from = min(_parse_date(x) for x in forecast["base_week_starts"])
            base_to = max(_parse_date(x) for x in forecast["base_week_starts"]) + timedelta(days=6)
            channels = _channel_split_tx(cursor, base_from, base_to)
            profile = _weekday_profile_tx(cursor, base_from, base_to)
            coverage = {"from": day_from.isoformat(), "to": latest.isoformat(),
                        "days": len(history)}

    return {
        "forecast": forecast,
        "history": history,
        "channels": channels,
        "weekday_profile": profile,
        "history_coverage": coverage,
        "latest_chat_day": latest.isoformat() if latest else None,
    }
