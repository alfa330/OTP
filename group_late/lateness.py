"""Опрос Workpace и поиск нарушений графика.

Работа делится на две фазы, чтобы event loop бота не занимался ни сетью Workpace,
ни базой:

* `collect_events(db)` — синхронно, в пуле потоков: тянет смены и отметки,
  находит нарушения, отбрасывает уже отправленные и «занимает» новые в базе;
* отправку в Telegram делает вызывающий (бот), а результаты возвращает в
  `finalize_deliveries(db, plan, results)` — тоже синхронно, в пуле потоков.

Ключ события уникален в базе, поэтому дедупликация переживает рестарт сервиса:
прежний бот держал её в памяти и после каждого перезапуска рассылал повторы.
"""

import hashlib
import logging
from datetime import datetime
from typing import Optional

from group_late import config, messages
from group_late.departments import (
    build_employee_department_lookup,
    count_departments,
    departments_allow,
    resolve_department_name,
)
from group_late.helpers import (
    employee_keys,
    employee_name,
    mark_date,
    mark_type,
    parse_dt,
    to_int,
)
from group_late.mutes import MuteSnapshot
from group_late.workpace import WorkpaceError, workpace_client

logger = logging.getLogger(__name__)


def make_event_key(emp_id: str, date: str, event_type: str) -> str:
    raw = f"{emp_id}:{date}:{event_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _raw_marks_for(emp_raw_marks: dict[str, list[dict]], rec: dict) -> list[dict]:
    marks = []
    seen = set()
    for key in employee_keys(rec):
        for mark in emp_raw_marks.get(key, []):
            mark_key = mark.get("id") or mark_date(mark) or id(mark)
            if mark_key in seen:
                continue
            seen.add(mark_key)
            marks.append(mark)
    return marks


def _late_minutes(plan_dt: datetime, fact_dt: datetime) -> int:
    return max(0, int((fact_dt - plan_dt).total_seconds() / 60))


def _candidate(event_key, event_type, event_date, text, rec, emp_id, emp_name, dept_name,
               plan_at=None, fact_at=None, minutes=None, location=None) -> dict:
    """Нарушение в том виде, в каком оно ложится в glb_events: текст для Telegram
    плюс разобранные поля — по ним раздел на сайте фильтрует и считает."""
    return {
        "event_key": event_key,
        "event_type": event_type,
        "event_date": event_date,
        "message_text": text,
        "employee_name": emp_name,
        "employee_ext_id": str(emp_id),
        "department_name": dept_name,
        "schedule_name": rec.get("scheduleName"),
        "plan_at": plan_at,
        "fact_at": fact_at,
        "minutes": minutes,
        "location": location,
    }


def find_violations(records: list[dict], marks: list[dict], employee_lookup: dict,
                    mute_snapshot: MuteSnapshot, now_local: datetime) -> list[dict]:
    """Чистая функция: смены + отметки → список нарушений. Без базы и Telegram."""
    threshold = config.LATE_THRESHOLD_MINUTES
    candidates: list[dict] = []

    emp_raw_marks: dict[str, list[dict]] = {}
    for mark in marks:
        for key in employee_keys(mark):
            emp_raw_marks.setdefault(key, []).append(mark)

    for rec in records:
        emp_id = rec.get("employeeExternalId") or rec.get("employeeId")
        if not emp_id:
            continue

        emp_name = employee_name(rec)
        dept_name = resolve_department_name(rec, employee_lookup)
        rec = {**rec, "departmentName": dept_name}
        if mute_snapshot.is_globally_muted(emp_name, dept_name):
            continue

        plan_dt = parse_dt(rec.get("workTimeStart"))
        if not plan_dt:
            continue
        date_str = str(rec.get("date") or plan_dt.strftime("%Y-%m-%d"))[:10]
        location = rec.get("inLocationName") or rec.get("locationName")

        in_mark_str = rec.get("inMark")
        late_in = to_int(rec.get("lateIn"))
        fact_in_dt = parse_dt(in_mark_str)
        # Workpace не всегда привязывает к смене слишком ранний или не
        # подтверждённый приход — достаём его из первичных отметок терминала,
        # иначе получаем ложную неявку.
        if not fact_in_dt:
            raw_ins = [m for m in _raw_marks_for(emp_raw_marks, rec) if mark_type(m) == 0]
            if raw_ins:
                raw_ins.sort(key=lambda item: mark_date(item) or "")
                in_mark_str = mark_date(raw_ins[0])
                fact_in_dt = parse_dt(in_mark_str)
        if fact_in_dt:
            late_in = max(late_in, _late_minutes(plan_dt, fact_in_dt))

        if not in_mark_str:
            passed_mins = (now_local - plan_dt).total_seconds() / 60
            if passed_mins >= config.MISSING_IN_AFTER_MINUTES:
                candidates.append(_candidate(
                    make_event_key(emp_id, date_str, "missing"), "missing", date_str,
                    messages.build_missing_message(rec, plan_dt, now_local),
                    rec, emp_id, emp_name, dept_name,
                    plan_at=plan_dt, minutes=int(passed_mins),
                ))
            continue

        if late_in >= threshold and fact_in_dt:
            candidates.append(_candidate(
                make_event_key(emp_id, date_str, "late"), "late", date_str,
                messages.build_late_message(rec, plan_dt, fact_in_dt, late_in),
                rec, emp_id, emp_name, dept_name,
                plan_at=plan_dt, fact_at=fact_in_dt, minutes=late_in, location=location,
            ))

        plan_end_dt = parse_dt(rec.get("workTimeEnd"))
        out_mark_str = rec.get("outMark")
        out_location = rec.get("outLocationName") or rec.get("locationName")
        early_out = to_int(rec.get("earlyOut"))

        if plan_end_dt and not out_mark_str:
            raw_outs = [m for m in _raw_marks_for(emp_raw_marks, rec) if mark_type(m) == 1]
            if raw_outs:
                raw_outs.sort(key=lambda item: mark_date(item) or "")
                out_mark_str = mark_date(raw_outs[-1])
                fact_out_dt = parse_dt(out_mark_str)
                if fact_out_dt:
                    early_out = max(0, int((plan_end_dt - fact_out_dt).total_seconds() / 60))

        if out_mark_str:
            fact_out_dt = parse_dt(out_mark_str)
            if not (plan_end_dt and fact_out_dt):
                continue
            if early_out >= threshold:
                candidates.append(_candidate(
                    make_event_key(emp_id, date_str, "early_out"), "early_out", date_str,
                    messages.build_early_out_message(rec, plan_end_dt, fact_out_dt, early_out),
                    rec, emp_id, emp_name, dept_name,
                    plan_at=plan_end_dt, fact_at=fact_out_dt, minutes=early_out, location=out_location,
                ))
            else:
                late_out_mins = int((fact_out_dt - plan_end_dt).total_seconds() / 60)
                if late_out_mins >= config.MISSING_OUT_AFTER_MINUTES:
                    candidates.append(_candidate(
                        make_event_key(emp_id, date_str, "late_out"), "late_out", date_str,
                        messages.build_late_out_message(rec, plan_end_dt, fact_out_dt, late_out_mins),
                        rec, emp_id, emp_name, dept_name,
                        plan_at=plan_end_dt, fact_at=fact_out_dt, minutes=late_out_mins,
                        location=out_location,
                    ))
        elif plan_end_dt:
            passed_end_mins = (now_local - plan_end_dt).total_seconds() / 60
            if passed_end_mins >= config.MISSING_OUT_AFTER_MINUTES:
                candidates.append(_candidate(
                    make_event_key(emp_id, date_str, "missing_out"), "missing_out", date_str,
                    messages.build_missing_out_message(rec, plan_end_dt, now_local),
                    rec, emp_id, emp_name, dept_name,
                    plan_at=plan_end_dt, minutes=int(passed_end_mins),
                ))

    for mark in marks:
        if mark.get("status") != 0:
            continue
        emp_id = mark.get("employeeId") or mark.get("id") or mark.get("externalId")
        mark_id = mark.get("markId") or mark.get("id") or f"{emp_id}:{mark_date(mark)}:{mark_type(mark)}"
        if not emp_id or not mark_id:
            continue

        emp_name = employee_name(mark)
        dept_name = resolve_department_name(mark, employee_lookup)
        mark = {**mark, "departmentName": dept_name}
        if mute_snapshot.is_globally_muted(emp_name, dept_name):
            continue
        mark_date_str = (mark_date(mark) or "")[:10]
        if not mark_date_str:
            continue
        candidates.append(_candidate(
            make_event_key(emp_id, mark_date_str, f"suspicious_{mark_id}"), "suspicious", mark_date_str,
            messages.build_suspicious_mark_message(mark),
            mark, emp_id, emp_name, dept_name,
            fact_at=parse_dt(mark_date(mark)),
            location=mark.get("deviceName") or mark.get("location"),
        ))

    return candidates


def collect_events(db) -> dict:
    """Фаза 1 (в пуле потоков): найти нарушения и занять их в базе.

    Возвращает план отправки: у каждого события уже есть id в glb_events и список
    чатов, куда оно должно уйти."""
    now_local = datetime.now(config.TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    day_end = now_local.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=None)

    run_id = db.glb_start_poll_run()

    if not config.is_configured():
        error = "Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD"
        db.glb_finish_poll_run(run_id, ok=False, error=error)
        return {"run_id": run_id, "ok": False, "error": error, "events": []}

    try:
        records = workpace_client.get_timetable_spans(day_start, day_end)
        marks = workpace_client.get_marks(day_start, day_end)
        employees = workpace_client.get_employees()
    except WorkpaceError as exc:
        logger.error("Workpace API error: %s", exc)
        db.glb_finish_poll_run(run_id, ok=False, error=str(exc))
        return {"run_id": run_id, "ok": False, "error": str(exc), "events": []}

    # Справочник отделов освежаем здесь же: сотрудники уже загружены, разделу на
    # сайте это достаётся бесплатно.
    try:
        db.glb_sync_departments(count_departments(employees))
    except Exception:
        logger.exception("group_late: не удалось обновить справочник отделов")

    employee_lookup = build_employee_department_lookup(employees)
    routing = db.glb_get_routing()
    mute_snapshot = MuteSnapshot(db.glb_get_mute_rows())

    if not routing:
        logger.warning("group_late: нет ни одного чата в рассылке")
        db.glb_finish_poll_run(run_id, ok=True, fetched=len(records))
        return {"run_id": run_id, "ok": True, "fetched": len(records), "events": []}

    candidates = find_violations(records, marks, employee_lookup, mute_snapshot, now_local)

    # Всё, что уже находили сегодня, отсеиваем до похода в базу: опрос идёт раз в
    # 2 минуты, и иначе он каждый круг пытался бы вставить те же нарушения.
    known_keys = db.glb_known_event_keys(now_local.strftime("%Y-%m-%d"))
    candidates = [item for item in candidates if item["event_key"] not in known_keys]

    plan: list[dict] = []
    for candidate in candidates:
        dept_name = candidate["department_name"]
        emp_name = candidate["employee_name"]
        targets = [
            chat["chat_id"] for chat in routing
            if departments_allow(chat["departments"], dept_name)
            and not mute_snapshot.is_event_muted_for_chat(chat["chat_id"], emp_name, dept_name)
        ]
        event_id = db.glb_claim_event(candidate)
        if not event_id:
            continue  # кто-то занял тот же ключ параллельно
        plan.append({
            "event_id": event_id,
            "message_text": candidate["message_text"],
            "targets": targets,
        })

    return {
        "run_id": run_id,
        "ok": True,
        "fetched": len(records),
        "events_found": len(plan),
        "events": plan,
    }


def finalize_deliveries(db, plan: dict, results: list[dict]) -> dict:
    """Фаза 2 (в пуле потоков): записать доставки и закрыть прогон.

    `results` — [{event_id, chat_id, message_id|None, error|None}]. Событие,
    которое не ушло ни в один чат, ХОТЯ чаты были, удаляем: следующий опрос
    попробует снова. Если отправлять было некому (фильтр отдела или тишина),
    событие остаётся в истории без доставок — в разделе это видно как
    «никуда не отправлено»."""
    delivered_by_event: dict[int, bool] = {}
    for result in results:
        db.glb_record_delivery(
            result["event_id"], result["chat_id"],
            result.get("message_id"), result.get("error"),
        )
        delivered_by_event[result["event_id"]] = (
            delivered_by_event.get(result["event_id"], False) or bool(result.get("message_id"))
        )

    sent = 0
    for event in plan.get("events") or []:
        if not event["targets"]:
            continue
        if delivered_by_event.get(event["event_id"]):
            sent += 1
        else:
            db.glb_drop_event(event["event_id"])

    db.glb_finish_poll_run(
        plan.get("run_id"), ok=True,
        fetched=plan.get("fetched") or 0,
        events_found=plan.get("events_found") or 0,
        sent=sent,
    )
    db.glb_cleanup_history(
        events_days=config.RETENTION_EVENTS_DAYS,
        report_files_days=config.RETENTION_REPORT_FILES_DAYS,
        poll_runs_days=config.RETENTION_POLL_RUNS_DAYS,
    )
    return {
        "ok": True,
        "fetched": plan.get("fetched") or 0,
        "events_found": plan.get("events_found") or 0,
        "sent": sent,
    }
