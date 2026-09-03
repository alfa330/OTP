"""Строки раздела «Отметки»: кто когда пришёл и ушёл, по обоим источникам.

Задача #273. Раздел показывает кадровому учёту одну таблицу поверх двух систем —
Workpace (отделы компании) и Clockster (центральный офис). Модуль собирает строки
и считает три числа из постановки: все отметки за день, время в работе за вычетом
обеда и опоздание в минутах.

Определения метрик живут ЗДЕСЬ и больше нигде. В проекте опоздание уже считается
по-разному в учёте часов и в отбивках; третье расхождение — прямо между экраном и
выгрузкой одного и того же раздела — было бы худшим из возможных: кадровик сверяет
эти числа между собой в одном окне.
"""

import logging
from datetime import timedelta
from typing import Optional

from group_late import config, icore_plan
from group_late.clockster import (
    MARK_SYSTEM as CLOCKSTER_SYSTEM,
    build_user_lookup as build_clockster_user_lookup,
    clockster_client,
    to_records as clockster_to_records,
)
from group_late.departments import (
    build_employee_department_lookup,
    clean_department_filters,
    department_matches,
    resolve_department_name,
)
from group_late.helpers import (
    employee_id as _employee_id,
    employee_keys,
    employee_name as _employee_name,
    is_archived as _is_archived,
    lunch_seconds,
    mark_date as _mark_date,
    mark_type as _mark_type,
    net_work_seconds,
    parse_dt,
)
from group_late.workpace import workpace_client

logger = logging.getLogger(__name__)

WORKPACE_SYSTEM = "workpace"
SYSTEM_LABELS = {WORKPACE_SYSTEM: "Воркпейс", CLOCKSTER_SYSTEM: "Клокстер"}

# Статус дня. Порядок важен: он же задаёт сортировку «сначала проблемные».
STATUS_ABSENT = "absent"
STATUS_LATE = "late"
STATUS_EARLY_OUT = "early_out"
STATUS_NO_OUT = "no_out"
STATUS_OFF_SCHEDULE = "off_schedule"
STATUS_NO_TERMINAL = "no_terminal"
STATUS_OK = "ok"

STATUS_LABELS = {
    STATUS_ABSENT: "Не отметился",
    STATUS_LATE: "Опоздание",
    STATUS_EARLY_OUT: "Ранний уход",
    STATUS_NO_OUT: "Нет отметки об уходе",
    STATUS_OFF_SCHEDULE: "Вне графика",
    STATUS_NO_TERMINAL: "Не отмечается",
    STATUS_OK: "Вовремя",
}

STATUS_ORDER = {
    STATUS_ABSENT: 0, STATUS_LATE: 1, STATUS_EARLY_OUT: 2,
    STATUS_NO_OUT: 3, STATUS_OFF_SCHEDULE: 4, STATUS_NO_TERMINAL: 5,
    STATUS_OK: 6,
}

# За сколько дней назад смотрим, пользуется ли человек терминалом вообще.
# Замер 03.09.2026: у 39 человек из 102 в Clockster есть график, но за девять дней
# нет НИ ОДНОЙ отметки — они просто не отмечаются (среди них глава отдела и
# разработчики). Каждый их день попадал бы в неявки и топил настоящие: 346 «неявок»
# за девять дней против 98 «вовремя». Поэтому такие дни получают отдельный статус,
# а не молча исчезают: пропажа строки скрыла бы и настоящий прогул.
NO_TERMINAL_LOOKBACK_DAYS = 30


def late_minutes(plan_dt, fact_dt) -> int:
    """Опоздание в минутах: факт минус план, отрицательное — это ноль.

    Округление ВНИЗ, как у отбивок: пришедший в 09:00:59 при плане 09:00 не
    опоздал. В учёте часов округление вверх — там своя задача (не переплатить),
    и сводить эти два определения в одно нельзя, но и путать их тоже."""
    if not plan_dt or not fact_dt:
        return 0
    return max(0, int((fact_dt - plan_dt).total_seconds() // 60))


def early_minutes(plan_end_dt, fact_out_dt) -> int:
    """Ранний уход в минутах, тем же правилом."""
    if not plan_end_dt or not fact_out_dt:
        return 0
    return max(0, int((plan_end_dt - fact_out_dt).total_seconds() // 60))


def _raw_marks_for(record, marks_by_key):
    seen, out = set(), []
    for key in employee_keys(record):
        for mark in marks_by_key.get(key, []):
            marker = id(mark)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(mark)
    out.sort(key=lambda item: _mark_date(item) or "")
    return out


def _fact_bounds(record, raw_marks):
    """Приход и уход: сначала то, что свёл источник, потом — сырые отметки.

    Workpace не привязывает к смене слишком ранний или неподтверждённый приход,
    поэтому его достаём из отметок терминала — иначе получаем ложную неявку. Берём
    ПЕРВЫЙ вход и ПОСЛЕДНИЙ выход за день: две смены в одном дне сольются в одну,
    и это осознанный размен — иначе на терминале с одной кнопкой пары не собрать."""
    fact_in = parse_dt(record.get("inMark"))
    if not fact_in:
        ins = [m for m in raw_marks if _mark_type(m) == 0]
        if ins:
            fact_in = parse_dt(_mark_date(ins[0]))
    fact_out = parse_dt(record.get("outMark"))
    if not fact_out:
        outs = [m for m in raw_marks if _mark_type(m) == 1]
        if outs:
            fact_out = parse_dt(_mark_date(outs[-1]))
    return fact_in, fact_out


def _status(plan_in, fact_in, fact_out, late, early, now_local) -> str:
    if not plan_in:
        return STATUS_OFF_SCHEDULE
    if not fact_in:
        # Пока смена не началась и порог не прошёл, человек ещё не «не отметился»:
        # иначе утренний экран красит неявкой всю вечернюю смену.
        if now_local and now_local < plan_in + timedelta(minutes=config.MISSING_IN_AFTER_MINUTES):
            return STATUS_OK
        return STATUS_ABSENT
    if late >= config.LATE_THRESHOLD_MINUTES:
        return STATUS_LATE
    if early >= config.LATE_THRESHOLD_MINUTES:
        return STATUS_EARLY_OUT
    if not fact_out:
        return STATUS_NO_OUT
    return STATUS_OK


def build_rows(records, marks, employee_lookup, date_iso, now_local=None) -> list[dict]:
    """Записи смен + отметки одного дня → строки таблицы. Чистая функция."""
    marks_by_key: dict[str, list] = {}
    for mark in marks or []:
        for key in employee_keys(mark):
            marks_by_key.setdefault(key, []).append(mark)

    rows: list[dict] = []
    seen_ids = set()

    for record in records or []:
        if _is_archived(record):
            continue
        emp_id = _employee_id(record)
        if not emp_id:
            continue
        seen_ids.add(str(emp_id))
        raw_marks = _raw_marks_for(record, marks_by_key)
        rows.append(_row(record, raw_marks, employee_lookup, date_iso, now_local))

    # Отметился человек, которого в плане нет вовсе: в отчёте он обязан быть виден,
    # иначе работа вне графика пропадает совсем.
    orphan: dict[str, list] = {}
    for mark in marks or []:
        emp_id = _employee_id(mark)
        if not emp_id or str(emp_id) in seen_ids:
            continue
        orphan.setdefault(str(emp_id), []).append(mark)
    for emp_id, emp_marks in orphan.items():
        emp_marks.sort(key=lambda item: _mark_date(item) or "")
        stub = {**emp_marks[0], "employeeId": emp_id, "date": date_iso}
        rows.append(_row(stub, emp_marks, employee_lookup, date_iso, now_local))

    return rows


def _row(record, raw_marks, employee_lookup, date_iso, now_local) -> dict:
    plan_in = parse_dt(record.get("workTimeStart"))
    plan_out = parse_dt(record.get("workTimeEnd"))
    fact_in, fact_out = _fact_bounds(record, raw_marks)

    late = late_minutes(plan_in, fact_in)
    early = early_minutes(plan_out, fact_out)

    planned_break = record.get("breakSeconds")
    work_sec = 0
    lunch_sec = 0
    if fact_in and fact_out:
        span = (fact_out - fact_in).total_seconds()
        if span > 0:
            lunch_sec = lunch_seconds(span, planned_break)
            work_sec = net_work_seconds(span, planned_break)

    system = record.get("markSystem") or WORKPACE_SYSTEM
    status = _status(plan_in, fact_in, fact_out, late, early, now_local)

    return {
        "date": date_iso,
        "employee_id": str(_employee_id(record) or ""),
        "employee": _employee_name(record),
        "department": resolve_department_name(record, employee_lookup),
        "position": record.get("positionName") or None,
        "location": record.get("locationName") or record.get("inLocationName") or None,
        "schedule": record.get("scheduleName") or None,
        "system": system,
        "system_label": SYSTEM_LABELS.get(system, system),
        "plan_in": plan_in.isoformat() if plan_in else None,
        "plan_out": plan_out.isoformat() if plan_out else None,
        "fact_in": fact_in.isoformat() if fact_in else None,
        "fact_out": fact_out.isoformat() if fact_out else None,
        "late_minutes": late,
        "early_out_minutes": early,
        "work_seconds": work_sec,
        "lunch_seconds": lunch_sec,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "marks": [
            {
                "at": _mark_date(mark),
                # Приход/уход в общем соглашении: 0 — вход, 1 — выход.
                "kind": "in" if _mark_type(mark) == 0 else "out",
                "system": mark.get("markSystem") or WORKPACE_SYSTEM,
                "suspicious": mark.get("status") == 0,
                "location": mark.get("location") or mark.get("deviceName") or None,
            }
            for mark in raw_marks
        ],
    }


def collect(db, date_start, date_end, department=None, now_local=None) -> dict:
    """Строки за период по обоим источникам. Сеть здесь, расчёты — в build_rows."""
    filters = clean_department_filters(department)
    days = (date_end - date_start).days
    if days < 0:
        date_start, date_end = date_end, date_start
        days = -days
    if days > config.MAX_REPORT_DAYS:
        raise ValueError(f"Период не должен превышать {config.MAX_REPORT_DAYS} дн.")

    workpace_employees = workpace_client.get_employees()
    employee_lookup = build_employee_department_lookup(workpace_employees)

    clockster_by_date: dict[str, tuple] = {}
    clockster_error = None
    # Кто вообще пользуется терминалом. Пустое множество означает «не знаем» —
    # тогда никого не понижаем, чтобы не спрятать настоящий прогул.
    terminal_users: set[str] = set()
    if config.is_clockster_configured():
        try:
            # Одним запросом берём и период показа, и хвост назад: /schedules
            # отдаёт даты словарём, поэтому более широкое окно стоит столько же.
            lookback_start = min(date_start, date_end - timedelta(days=NO_TERMINAL_LOOKBACK_DAYS))
            rows = clockster_client.get_schedules(lookback_start, date_end)
            lookup = build_clockster_user_lookup(clockster_client.get_users())
            cl_records, cl_marks = clockster_to_records(rows, lookup)
            for mark in cl_marks:
                emp = str(mark.get("employeeId") or "")
                if emp and _mark_type(mark) == 0:
                    terminal_users.add(emp)
            for rec in cl_records:
                day = str(rec.get("date"))[:10]
                if not (date_start.isoformat() <= day <= date_end.isoformat()):
                    continue
                clockster_by_date.setdefault(day, ([], []))[0].append(rec)
            for mark in cl_marks:
                day = str(_mark_date(mark) or "")[:10]
                if day and date_start.isoformat() <= day <= date_end.isoformat():
                    clockster_by_date.setdefault(day, ([], []))[1].append(mark)
        except Exception as exc:
            # Один источник не должен ронять весь раздел: без Workpace таблица
            # пуста и так, а без Clockster в ней просто нет центрального офиса.
            clockster_error = str(exc)[:300]
            logger.warning("Clockster недоступен: %s", exc)

    all_rows: list[dict] = []
    current = date_start
    while current <= date_end:
        date_iso = current.isoformat()
        start_local = _midnight(current)
        end_local = _end_of_day(current)
        records = workpace_client.get_timetable_spans(start_local, end_local)
        marks = workpace_client.get_marks(start_local, end_local)
        if db is not None:
            records, _ = icore_plan.apply_to_records(
                db, records, workpace_employees, current, employee_lookup=employee_lookup)
        cl_records, cl_marks = clockster_by_date.get(date_iso, ([], []))
        all_rows.extend(build_rows(records + cl_records, marks + cl_marks,
                                   employee_lookup, date_iso, now_local))
        current += timedelta(days=1)

    all_rows = mark_non_terminal_users(all_rows, terminal_users)

    if filters:
        all_rows = [r for r in all_rows if department_matches(r.get("department"), filters)]

    return {"rows": all_rows, "clockster_error": clockster_error}


def mark_non_terminal_users(rows, terminal_users):
    """Неявка человека, который вообще не отмечается, — не неявка.

    Отдельный статус, а не фильтр: строка обязана остаться, иначе вместе с шумом
    исчезнет и настоящий прогул такого сотрудника. Понижаем только тех, про кого
    ТОЧНО знаем, что за хвост назад у них нет ни одной отметки."""
    if not terminal_users:
        return rows
    for row in rows:
        if row.get("status") != STATUS_ABSENT:
            continue
        if row.get("system") != CLOCKSTER_SYSTEM:
            continue
        if row.get("employee_id") in terminal_users:
            continue
        row["status"] = STATUS_NO_TERMINAL
        row["status_label"] = STATUS_LABELS[STATUS_NO_TERMINAL]
    return rows


def _midnight(day):
    from datetime import datetime as _dt
    return _dt(day.year, day.month, day.day, 0, 0, 0)


def _end_of_day(day):
    from datetime import datetime as _dt
    return _dt(day.year, day.month, day.day, 23, 59, 59, 999999)


def search_rows(rows, query: Optional[str]):
    """Поиск по ФИО и должности — ровно то, что просит постановка."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return rows
    return [
        row for row in rows
        if needle in str(row.get("employee") or "").casefold()
        or needle in str(row.get("position") or "").casefold()
    ]


SORT_KEYS = {
    "employee": lambda r: str(r.get("employee") or "").casefold(),
    "department": lambda r: str(r.get("department") or "").casefold(),
    "location": lambda r: str(r.get("location") or "").casefold(),
    "position": lambda r: str(r.get("position") or "").casefold(),
    "system": lambda r: str(r.get("system_label") or "").casefold(),
    "schedule": lambda r: str(r.get("schedule") or "").casefold(),
    # «Приход/уход» из постановки: сортировка по времени факта. Пустое значение
    # уезжает в конец, а не наверх — иначе первыми идут те, у кого отметки нет.
    "fact_in": lambda r: (r.get("fact_in") is None, str(r.get("fact_in") or "")),
    "fact_out": lambda r: (r.get("fact_out") is None, str(r.get("fact_out") or "")),
    "late_minutes": lambda r: -int(r.get("late_minutes") or 0),
    "work_seconds": lambda r: -int(r.get("work_seconds") or 0),
    "status": lambda r: STATUS_ORDER.get(r.get("status"), 99),
    "date": lambda r: str(r.get("date") or ""),
}


def sort_rows(rows, sort: Optional[str]):
    key = SORT_KEYS.get(str(sort or "").strip() or "status")
    if key is None:
        key = SORT_KEYS["status"]
    # Вторичный ключ — дата и ФИО: иначе строки одного статуса прыгают между
    # обновлениями, и таблица выглядит «живой» без причины.
    return sorted(rows, key=lambda r: (key(r), str(r.get("date") or ""),
                                       str(r.get("employee") or "").casefold()))
