"""Дневные телефонные метрики TEZ ОП из официального Binotel API 4.0.

Модуль намеренно не использует внутренние web-модули панели Binotel. Источник —
нормализованные звонки из ``tez_binotel_calls.BinotelApiClient``.

Контракт с БД:

* ``get_tez_op_binotel_internal_numbers(start, end)`` возвращает внутренние
  номера, относящиеся к TEZ ОП в периоде;
* ``replace_tez_op_call_metrics(start, end, rows)`` атомарно заменяет дневные
  метрики за период.

``resolve_operator(employee_name, call_date)`` должен вернуть id оператора TEZ
ОП на дату звонка либо ``None``. Все данные сначала полностью загружаются и
агрегируются в памяти. Replace вызывается ровно один раз и только после
успешного завершения всех запросов, поэтому частичная выгрузка не сохраняется.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import tez_binotel_calls

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11 в production имеет zoneinfo
    ZoneInfo = None


DEFAULT_TZ = "Asia/Almaty"
DEFAULT_DAYS = 2
_SYNC_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def get_config(env_file: str = ".env.codex.local") -> Dict[str, Any]:
    """Возвращает конфиг официального API, не читая настройки panel scraping."""
    return tez_binotel_calls.get_config(env_file)


def api_ready(config: Optional[Dict[str, Any]] = None) -> bool:
    """Настроены ли key/secret официального Binotel API."""
    return tez_binotel_calls.api_ready(config)


def _tzinfo(tz_name: str = DEFAULT_TZ):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name or DEFAULT_TZ)
        except Exception:
            pass
    return tez_binotel_calls._tzinfo(DEFAULT_TZ)


def default_date_range(
    tz_name: str = DEFAULT_TZ,
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """По умолчанию синхронизирует вчера и текущий день по Asia/Almaty.

    Текущий день может быть неполным; replace-контракт позволяет безопасно
    пересчитать его следующим запуском.
    """
    tz = _tzinfo(tz_name)
    current = now
    if current is None:
        current = datetime.now(tz)
    elif current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)
    end_day = current.date()
    start_day = end_day - timedelta(days=DEFAULT_DAYS - 1)
    return start_day.isoformat(), end_day.isoformat()


def _parse_period_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be YYYY-MM-DD")
    text = value.strip()
    if len(text) != 10:
        raise ValueError(f"{field_name} must be YYYY-MM-DD")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field_name} must be YYYY-MM-DD")
    return parsed


def normalize_period(
    start: Optional[str] = None,
    end: Optional[str] = None,
    *,
    tz_name: str = DEFAULT_TZ,
    now: Optional[datetime] = None,
) -> Tuple[str, str, date, date]:
    """Проверяет период и возвращает ISO-строки вместе с ``date``."""
    if start is None and end is None:
        start, end = default_date_range(tz_name=tz_name, now=now)
    elif start is None or end is None:
        raise ValueError("start and end must both be YYYY-MM-DD")

    start_day = _parse_period_date(start, "start")
    end_day = _parse_period_date(end, "end")
    if end_day < start_day:
        raise ValueError("end must be greater than or equal to start")
    return start_day.isoformat(), end_day.isoformat(), start_day, end_day


def _internal_number_value(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("internal_number", "internalNumber", "sip_number", "sip"):
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""
    if isinstance(item, (tuple, list)):
        if not item:
            return ""
        return str(item[0] or "").strip()
    return str(item or "").strip()


def _internal_number_sort_key(value: str):
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, str(value)


def distinct_internal_numbers(items: Optional[Iterable[Any]]) -> List[str]:
    """Нормализует и дедуплицирует SIP без логирования их значений."""
    if isinstance(items, (str, int, float, dict)):
        items = [items]
    unique = {
        value
        for value in (_internal_number_value(item) for item in (items or []))
        if value
    }
    return sorted(unique, key=_internal_number_sort_key)


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0


def _call_local_date(call: Dict[str, Any], tz_name: str) -> Optional[date]:
    value = call.get("start_time")
    if value in (None, ""):
        return None
    tz = _tzinfo(tz_name)
    try:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                local_dt = value.replace(tzinfo=tz)
            else:
                local_dt = value.astimezone(tz)
        else:
            local_dt = datetime.fromtimestamp(int(value), tz)
        return local_dt.date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _resolved_operator_id(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        value = value.get("operator_id", value.get("id"))
    if value is None or isinstance(value, bool):
        return None
    try:
        operator_id = int(value)
    except (TypeError, ValueError):
        return None
    return operator_id if operator_id > 0 else None


def _prefer_call(existing: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Для дубля оставляет вариант с наиболее полными полями атрибуции."""
    def score(item: Dict[str, Any]) -> int:
        return (
            int(bool(str(item.get("employee_name") or "").strip())) * 4
            + int(item.get("start_time") not in (None, "")) * 2
            + int(item.get("billsec") not in (None, ""))
            + int(item.get("waitsec") not in (None, ""))
        )

    return candidate if score(candidate) > score(existing) else existing


def aggregate_calls(
    calls_by_id: Dict[str, Dict[str, Any]],
    resolve_operator: Callable[[str, date], Optional[int]],
    start_day: date,
    end_day: date,
    *,
    tz_name: str = DEFAULT_TZ,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Агрегирует уникальные звонки в строки ``operator/day``."""
    buckets: Dict[Tuple[int, date], Dict[str, Any]] = {}
    counters = {
        "matched_calls": 0,
        "skipped_no_date": 0,
        "skipped_out_of_range": 0,
        "skipped_unknown_operator": 0,
    }

    for call in calls_by_id.values():
        call_day = _call_local_date(call, tz_name)
        if call_day is None:
            counters["skipped_no_date"] += 1
            continue
        if call_day < start_day or call_day > end_day:
            counters["skipped_out_of_range"] += 1
            continue

        employee_name = str(call.get("employee_name") or "").strip()
        operator_id = _resolved_operator_id(
            resolve_operator(employee_name, call_day) if employee_name else None
        )
        if operator_id is None:
            counters["skipped_unknown_operator"] += 1
            continue

        key = (operator_id, call_day)
        row = buckets.setdefault(key, {
            "operator_id": operator_id,
            "day": call_day.isoformat(),
            "calls": 0,
            "dial_seconds": 0,
            "talk_seconds": 0,
        })
        row["calls"] += 1
        row["dial_seconds"] += _non_negative_int(call.get("waitsec"))
        row["talk_seconds"] += _non_negative_int(call.get("billsec"))
        counters["matched_calls"] += 1

    rows = []
    for key in sorted(buckets, key=lambda item: (item[1], item[0])):
        row = dict(buckets[key])
        row["dial_time"] = round(row["dial_seconds"] / 3600.0, 6)
        row["talk_time"] = round(row["talk_seconds"] / 3600.0, 6)
        rows.append(row)
    return rows, counters


def _base_summary(start: str, end: str, started_at: float) -> Dict[str, Any]:
    return {
        "status": "pending",
        "date_from": start,
        "date_to": end,
        "saved": False,
        "internal_numbers": 0,
        "fetched_internal_numbers": 0,
        "fetched_calls": 0,
        "unique_calls": 0,
        "duplicate_calls": 0,
        "skipped_no_call_id": 0,
        "matched_calls": 0,
        "skipped_no_date": 0,
        "skipped_out_of_range": 0,
        "skipped_unknown_operator": 0,
        "rows": 0,
        "operators": 0,
        "days": 0,
        "_started_at": started_at,
    }


def _finish_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    started_at = float(summary.pop("_started_at", time.monotonic()))
    summary["elapsed_seconds"] = round(max(0.0, time.monotonic() - started_at), 3)
    return summary


def run_sync(
    db: Any,
    resolve_operator: Callable[[str, date], Optional[int]],
    start: Optional[str] = None,
    end: Optional[str] = None,
    *,
    binotel_client: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Полностью выгружает период и одним вызовом заменяет дневные метрики.

    При ошибке любого SIP, обработки или сохранения возвращается ``status=failed``.
    Частично загруженные строки никогда не передаются в БД.
    """
    sync_log = logger or log
    started_at = time.monotonic()
    cfg = config if config is not None else get_config()
    tz_name = str(cfg.get("tz") or DEFAULT_TZ)
    start_iso, end_iso, start_day, end_day = normalize_period(
        start, end, tz_name=tz_name, now=now
    )
    summary = _base_summary(start_iso, end_iso, started_at)

    if binotel_client is None and not api_ready(cfg):
        summary.update({"status": "skipped", "reason": "no_credentials"})
        return _finish_summary(summary)
    if not callable(resolve_operator):
        raise ValueError("resolve_operator callback is required")

    if not _SYNC_LOCK.acquire(blocking=False):
        summary.update({"status": "skipped", "reason": "locked"})
        return _finish_summary(summary)

    try:
        try:
            raw_internal_numbers = db.get_tez_op_binotel_internal_numbers(
                start_iso, end_iso
            )
            internal_numbers = distinct_internal_numbers(raw_internal_numbers)
            summary["internal_numbers"] = len(internal_numbers)
        except Exception as exc:
            sync_log.error(
                "TEZ OP Binotel productivity: internal-number lookup failed (%s)",
                type(exc).__name__,
            )
            summary.update({
                "status": "failed",
                "error": "internal_numbers_failed",
                "error_type": type(exc).__name__,
            })
            return _finish_summary(summary)

        client = binotel_client
        if client is None:
            client = tez_binotel_calls.BinotelApiClient.from_config(cfg)

        start_ts, stop_ts = tez_binotel_calls._day_bounds_unix(
            start_iso, end_iso, tz_name
        )
        calls_by_id: Dict[str, Dict[str, Any]] = {}

        for internal_number in internal_numbers:
            try:
                fetched = client.list_calls_by_internal_number(
                    internal_number, start_ts, stop_ts
                ) or []
            except Exception as exc:
                sync_log.error(
                    "TEZ OP Binotel productivity: fetch failed after %s/%s SIPs (%s)",
                    summary["fetched_internal_numbers"],
                    summary["internal_numbers"],
                    type(exc).__name__,
                )
                summary.update({
                    "status": "failed",
                    "error": "binotel_fetch_failed",
                    "error_type": type(exc).__name__,
                })
                return _finish_summary(summary)

            summary["fetched_internal_numbers"] += 1
            summary["fetched_calls"] += len(fetched)
            for call in fetched:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("general_call_id") or "").strip()
                if not call_id:
                    summary["skipped_no_call_id"] += 1
                    continue
                existing = calls_by_id.get(call_id)
                if existing is not None:
                    summary["duplicate_calls"] += 1
                    calls_by_id[call_id] = _prefer_call(existing, call)
                else:
                    calls_by_id[call_id] = call

        summary["unique_calls"] = len(calls_by_id)
        try:
            rows, counters = aggregate_calls(
                calls_by_id,
                resolve_operator,
                start_day,
                end_day,
                tz_name=tz_name,
            )
        except Exception as exc:
            sync_log.error(
                "TEZ OP Binotel productivity: aggregation failed (%s)",
                type(exc).__name__,
            )
            summary.update({
                "status": "failed",
                "error": "aggregation_failed",
                "error_type": type(exc).__name__,
            })
            return _finish_summary(summary)

        summary.update(counters)
        summary["rows"] = len(rows)
        summary["operators"] = len({row["operator_id"] for row in rows})
        summary["days"] = len({row["day"] for row in rows})

        try:
            db.replace_tez_op_call_metrics(start_iso, end_iso, rows)
        except Exception as exc:
            sync_log.error(
                "TEZ OP Binotel productivity: replace failed (%s)",
                type(exc).__name__,
            )
            summary.update({
                "status": "failed",
                "error": "replace_failed",
                "error_type": type(exc).__name__,
            })
            return _finish_summary(summary)

        summary.update({"status": "success", "saved": True})
        sync_log.info(
            "TEZ OP Binotel productivity %s..%s: SIPs=%s calls=%s rows=%s operators=%s",
            start_iso,
            end_iso,
            summary["internal_numbers"],
            summary["unique_calls"],
            summary["rows"],
            summary["operators"],
        )
        return _finish_summary(summary)
    finally:
        _SYNC_LOCK.release()


sync_tez_op_productivity = run_sync


__all__ = [
    "DEFAULT_DAYS",
    "DEFAULT_TZ",
    "aggregate_calls",
    "api_ready",
    "default_date_range",
    "distinct_internal_numbers",
    "get_config",
    "normalize_period",
    "run_sync",
    "sync_tez_op_productivity",
]
