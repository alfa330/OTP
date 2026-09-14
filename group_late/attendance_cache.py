"""Кэш строк раздела «Отметки» по дням (ТЗ #307, п. 1).

Зачем. До этого период на экране был ограничен неделей, и не от лени: строки
собираются из чужих API прямо в запросе — два обращения к Workpace на КАЖДЫЙ
день плюс общий запрос к Clockster. Замер на проде 14.09.2026: один день —
5,5 с, семь дней — 13,4 с; месяц дал бы около сорока пяти секунд ожидания и
столько же нагрузки на Workpace при каждом открытии раздела.

Как. Прошедший день меняться уже не может, поэтому он собирается ровно один раз
и ложится в `glb_attendance_rows`. Сегодняшний день всегда живой: он ещё идёт.
Ночная джоба добирает вчерашний, так что в обычной жизни экран читает только
базу, а в Workpace ходит за один сегодняшний день.

Чего кэш НЕ делает: он не фильтрует. Подразделение, тип отметки и состав режутся
тем же кодом, что и на живом пути (`attendance`), иначе два набора правил
разошлись бы на нестрогом сравнении названий отделов — а кадровик сверяет эти
числа в одном окне.
"""

import logging
from datetime import date as date_cls, datetime, timedelta

from group_late import attendance
from group_late.config import TZ
from group_late.departments import clean_department_filters, department_matches

logger = logging.getLogger(__name__)

# Сколько дней добираем в один заход. Ограничение про ожидание в HTTP-запросе, а
# не про объём: 31 день — это около сорока пяти секунд, и больше держать человека
# перед пустым экраном нельзя. Остаток честно возвращается как «ещё собирается»
# и дособирается следующим открытием или ночной джобой.
BUILD_MAX_DAYS_PER_REQUEST = 31


def _now():
    """Текущее время компании. Отдельной функцией — чтобы тесты подменяли «сейчас»,
    не трогая сам тип `datetime`, по которому модуль различает даты."""
    return datetime.now(TZ)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _iso(value):
    """Дата-время из базы → ISO в поясе компании. Наивное считаем местным."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ).isoformat()
    return value.astimezone(TZ).isoformat()


def row_from_cache(record: dict) -> dict:
    """Строка кэша → та же форма, что отдаёт `attendance.build_rows`."""
    system = record.get("system") or attendance.WORKPACE_SYSTEM
    status = record.get("status") or attendance.STATUS_OK
    marks = record.get("marks") or []
    last_mark = None
    for mark in marks:
        when = mark.get("at")
        if when and (last_mark is None or str(when) > str(last_mark)):
            last_mark = when
    return {
        "date": _as_date(record.get("day")).isoformat(),
        "employee_id": record.get("employee_id") or "",
        "employee": record.get("employee_name") or "—",
        "department": record.get("department_name"),
        "position": record.get("position_name"),
        "location": record.get("location_name"),
        "schedule": record.get("schedule_name"),
        "system": system,
        "system_label": attendance.SYSTEM_LABELS.get(system, system),
        "plan_in": _iso(record.get("plan_in")),
        "plan_out": _iso(record.get("plan_out")),
        "fact_in": _iso(record.get("fact_in")),
        "fact_out": _iso(record.get("fact_out")),
        "last_mark_at": last_mark or _iso(record.get("fact_out")) or _iso(record.get("fact_in")),
        "late_minutes": int(record.get("late_minutes") or 0),
        "early_out_minutes": int(record.get("early_out_minutes") or 0),
        "work_seconds": int(record.get("work_seconds") or 0),
        "present_seconds": int(record.get("present_seconds") or 0),
        "lunch_seconds": int(record.get("lunch_seconds") or 0),
        "plan_mode": record.get("plan_mode") or "schedule",
        "plan_source": record.get("plan_source"),
        "hours_norm": float(record["hours_norm"]) if record.get("hours_norm") is not None else None,
        "status": status,
        "status_label": attendance.STATUS_LABELS.get(status, status),
        "marks": marks,
        "cached": True,
    }


def _plan_inputs(db):
    """Свой график и состав обеих систем — читаем один раз на весь период."""
    if db is None:
        return [], []
    try:
        rules = db.glb_plan_rules(enabled_only=True)
    except Exception:
        logger.exception("Отметки: не удалось прочитать правила графика")
        rules = []
    roster = []
    if rules:
        try:
            roster = [
                {"ext_id": person["id"], "full_name": person["name"],
                 "department_name": person["department"],
                 "position_name": person.get("position"), "source": person.get("source")}
                for person in db.glb_attendance_directory().get("employees", [])
            ]
        except Exception:
            logger.exception("Отметки: не удалось прочитать состав для правил графика")
            roster = []
    return rules, roster


def build_day(db, day, rules=None, roster=None, store=True, clockster_users_out=None):
    """Собирает ОДИН день из источников и (по умолчанию) кладёт его в кэш."""
    target = _as_date(day)
    if rules is None or roster is None:
        rules, roster = _plan_inputs(db)
    payload = attendance.collect(
        db, target, target, now_local=_now(),
        rules=rules, roster=roster, clockster_users_out=clockster_users_out)
    rows = payload.get("rows") or []
    if store and db is not None:
        sources = "workpace" if payload.get("clockster_error") else "workpace+clockster"
        db.glb_store_attendance_day(target, rows, sources=sources)
    return rows, payload.get("clockster_error")


def rows_for(db, date_start, date_end, department=None, refresh=False):
    """Строки периода: прошлое — из кэша, сегодняшний день — живьём.

    Возвращает `rows`, `clockster_error` и `pending_days` — дни, которые в этот
    заход добрать не успели. Молча обрезать период нельзя: кадровик решит, что в
    эти дни никто не отмечался."""
    start, end = _as_date(date_start), _as_date(date_end)
    if end < start:
        start, end = end, start
    today = _now().date()

    all_days = []
    cursor = start
    while cursor <= end:
        all_days.append(cursor)
        cursor += timedelta(days=1)

    live_days = [day for day in all_days if day >= today]
    past_days = [day for day in all_days if day < today]

    if refresh and db is not None and past_days:
        db.glb_forget_attendance_days(day_from=past_days[0], day_to=past_days[-1])

    built = db.glb_attendance_built_days(start, end) if db is not None else set()
    missing = [day for day in past_days if day not in built]
    # Добираем от свежих к старым: если упрёмся в потолок, человек увидит
    # ближайшие дни, а не хвост трёхмесячной давности.
    missing.sort(reverse=True)
    pending = missing[BUILD_MAX_DAYS_PER_REQUEST:]
    to_build = sorted(missing[:BUILD_MAX_DAYS_PER_REQUEST])

    rules, roster = _plan_inputs(db)
    clockster_error = None
    fresh_rows: list[dict] = []

    for day in to_build:
        try:
            rows, day_error = build_day(db, day, rules=rules, roster=roster)
        except Exception as exc:
            logger.exception("Отметки: не удалось собрать день %s", day)
            pending.append(day)
            clockster_error = clockster_error or str(exc)[:300]
            continue
        clockster_error = clockster_error or day_error
        fresh_rows.extend(rows)

    if live_days:
        try:
            payload = attendance.collect(
                db, live_days[0], live_days[-1], now_local=_now(),
                rules=rules, roster=roster)
            fresh_rows.extend(payload.get("rows") or [])
            clockster_error = clockster_error or payload.get("clockster_error")
        except Exception as exc:
            logger.exception("Отметки: не удалось собрать сегодняшний день")
            clockster_error = clockster_error or str(exc)[:300]

    cached_days = [day for day in past_days if day not in set(to_build) and day not in set(pending)]
    rows: list[dict] = []
    if cached_days and db is not None:
        rows.extend(row_from_cache(record)
                    for record in db.glb_read_attendance_rows(start, end, days=cached_days))
    rows.extend(fresh_rows)

    filters = clean_department_filters(department)
    if filters:
        rows = [row for row in rows if department_matches(row.get("department"), filters)]

    return {
        "rows": rows,
        "clockster_error": clockster_error,
        "pending_days": [day.isoformat() for day in sorted(pending)],
        "built_days": len(to_build),
        "cached_days": len(cached_days),
    }


def backfill(db, days=7, until=None):
    """Ночной досбор: вчерашний день и хвост пропусков за последние `days` суток.

    Неделя назад, а не один вчерашний день: сервис на Render засыпает и
    перезапускается, и пропущенная ночь иначе оставила бы в периоде дырку
    навсегда — кадровик увидел бы пустой день вместо отметок."""
    if db is None:
        return 0
    edge = _as_date(until) if until else (_now().date() - timedelta(days=1))
    start = edge - timedelta(days=max(0, int(days) - 1))
    built = db.glb_attendance_built_days(start, edge)
    rules, roster = _plan_inputs(db)
    done = 0
    cursor = edge
    while cursor >= start:
        if cursor not in built:
            try:
                build_day(db, cursor, rules=rules, roster=roster)
                done += 1
            except Exception:
                logger.exception("Отметки: ночной досбор дня %s не удался", cursor)
        cursor -= timedelta(days=1)
    return done
