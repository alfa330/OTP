"""Calendar model of daily call volume: level x weekday x day-of-month x holiday.

Two jobs:
  * for СЗоВ the day-of-month cycle (documents are signed from the 5th, drivers who did not
    sign by the 15th get blocked) is taken out of the series before TimesFM sees it and put
    back afterwards — that was the most accurate variant on 61 test weeks;
  * when BigQuery is unreachable it is the fallback forecast itself (median level of the
    last 28 cleaned days times the factors).

Factors are fitted on the past only (log-linear least squares with a level per calendar
month, so a falling or growing month does not leak into the weekday/day-of-month shape).
"""
import math
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# Kazakhstan public holidays and official transfer days. Moving: Kurban Ait (lunar).
KZ_HOLIDAYS = frozenset({
    "2025-01-01", "2025-01-02", "2025-01-03", "2025-01-07", "2025-03-08", "2025-03-10",
    "2025-03-21", "2025-03-22", "2025-03-23", "2025-03-24", "2025-03-25", "2025-05-01",
    "2025-05-07", "2025-05-08", "2025-05-09", "2025-06-06", "2025-07-06", "2025-07-07",
    "2025-08-30", "2025-09-01", "2025-10-25", "2025-10-27", "2025-12-16",
    "2026-01-01", "2026-01-02", "2026-01-07", "2026-03-08", "2026-03-09", "2026-03-21",
    "2026-03-22", "2026-03-23", "2026-03-24", "2026-05-01", "2026-05-07", "2026-05-09",
    "2026-05-11", "2026-05-27", "2026-07-06", "2026-08-30", "2026-08-31", "2026-10-25",
    "2026-10-26", "2026-12-16",
    "2027-01-01", "2027-01-02", "2027-01-07", "2027-03-08", "2027-03-21", "2027-03-22",
    "2027-03-23", "2027-05-01", "2027-05-07", "2027-05-09", "2027-05-16", "2027-07-06",
    "2027-08-30", "2027-10-25", "2027-12-16",
})

# Day-of-month windows. The middle splits around the 15th, where the block deadline sits.
DOM_BINS: Tuple[Tuple[int, int], ...] = ((1, 4), (5, 9), (10, 12), (13, 14), (15, 16), (17, 20), (21, 25), (26, 31))


def dom_bin(day_of_month: int) -> int:
    for index, (low, high) in enumerate(DOM_BINS):
        if low <= day_of_month <= high:
            return index
    return len(DOM_BINS) - 1


def is_holiday(day: date) -> bool:
    return day.isoformat() in KZ_HOLIDAYS


class Factors:
    """Multiplicative factors; weekday/dom normalised to a mean of 1 (dom by day count)."""

    def __init__(self, weekday: Sequence[float], dom: Sequence[float], holiday: float):
        self.weekday = [float(x) for x in weekday]
        self.dom = [float(x) for x in dom]
        self.holiday = float(holiday)

    @classmethod
    def neutral(cls) -> "Factors":
        return cls([1.0] * 7, [1.0] * len(DOM_BINS), 1.0)

    def season(self, day: date, with_weekday: bool = True) -> float:
        value = self.dom[dom_bin(day.day)]
        if with_weekday:
            value *= self.weekday[day.weekday()]
        if is_holiday(day):
            value *= self.holiday
        return value

    def as_dict(self) -> Dict[str, object]:
        return {
            "weekday": [round(x, 4) for x in self.weekday],
            "day_of_month": [{"from": low, "to": high, "factor": round(self.dom[i], 4)}
                             for i, (low, high) in enumerate(DOM_BINS)],
            "holiday": round(self.holiday, 4),
        }


def fit_factors(series: Dict[date, float], min_days: int = 56) -> Factors:
    """Least squares on log(volume) = month level + weekday + day-of-month bin + holiday."""
    days = sorted(d for d, v in series.items() if v is not None and v > 0)
    if len(days) < min_days:
        return Factors.neutral()
    months = sorted({(d.year, d.month) for d in days})
    month_index = {m: i for i, m in enumerate(months)}
    n_cols = len(months) + 6 + (len(DOM_BINS) - 1)
    holidays = [is_holiday(d) for d in days]
    use_holiday = sum(holidays) >= 2
    if use_holiday:
        n_cols += 1
    x = np.zeros((len(days), n_cols))
    y = np.zeros(len(days))
    for row, d in enumerate(days):
        x[row, month_index[(d.year, d.month)]] = 1.0
        if d.weekday() > 0:
            x[row, len(months) + d.weekday() - 1] = 1.0
        b = dom_bin(d.day)
        if b > 0:
            x[row, len(months) + 6 + b - 1] = 1.0
        if use_holiday and holidays[row]:
            x[row, n_cols - 1] = 1.0
        y[row] = math.log(series[d])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    weekday = np.exp(np.concatenate([[0.0], coef[len(months):len(months) + 6]]))
    weekday = weekday / weekday.mean()
    dom = np.exp(np.concatenate([[0.0], coef[len(months) + 6:len(months) + 6 + len(DOM_BINS) - 1]]))
    widths = np.array([high - low + 1 for low, high in DOM_BINS], dtype=float)
    dom = dom / (dom * widths).sum() * widths.sum()
    holiday = float(np.exp(coef[n_cols - 1])) if use_holiday else 1.0
    return Factors(weekday.tolist(), dom.tolist(), holiday)


def calendar_forecast(series: Dict[date, float], days: Iterable[date], factors: Optional[Factors] = None,
                      level_window: int = 28) -> Dict[date, float]:
    """Fallback forecast: median of the last `level_window` fully deseasonalised days."""
    factors = factors or fit_factors(series)
    history = sorted(d for d, v in series.items() if v is not None and v > 0)
    if not history:
        return {d: 0.0 for d in days}
    tail = history[-level_window:]
    level = float(np.median([series[d] / factors.season(d) for d in tail]))
    return {d: level * factors.season(d) for d in days}


def fill_gaps(series: Dict[date, float], start: date, end: date, factors: Factors,
              exclude: Iterable[date] = ()) -> Tuple[List[date], List[float], List[date]]:
    """A contiguous daily series start..end. Missing and excluded days are filled with the
    calendar estimate around them (TimesFM needs one value per day). Returns
    (days, values, filled_days)."""
    excluded = set(exclude)
    days: List[date] = []
    values: List[float] = []
    filled: List[date] = []
    known = sorted(d for d, v in series.items() if v is not None and v > 0 and d not in excluded and start <= d <= end)
    current = start
    while current <= end:
        value = series.get(current)
        if current in excluded or value is None or value <= 0:
            neighbours = [d for d in known if abs((d - current).days) <= 28]
            if neighbours:
                level = float(np.median([series[d] / factors.season(d) for d in neighbours]))
                value = level * factors.season(current)
            else:
                value = values[-1] if values else 0.0
            filled.append(current)
        days.append(current)
        values.append(float(value))
        current += timedelta(days=1)
    return days, values, filled
