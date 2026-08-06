"""Тексты уведомлений в Telegram.

Формат сохранён от прежнего сервиса — люди в чатах привыкли к этим сообщениям.
Строки «Статус: ожидает отбивки» и кнопки «Отбито» под сообщением больше нет:
отметка «инцидент взяли в работу» ничего не меняла ни в Workpace, ни в отчётах и
не хранила причину, поэтому по решению владельца механика убрана.
"""

from datetime import datetime

from group_late.helpers import employee_name, format_time, mark_date, mark_type, parse_dt

EVENT_TITLES = {
    "late": "Фактическое опоздание",
    "missing": "Отсутствует на месте",
    "early_out": "Ранний уход",
    "missing_out": "Нет отметки об уходе",
    "late_out": "Поздний уход",
    "suspicious": "Подозрительная отметка",
}


def _head(rec: dict) -> tuple[str, str, str]:
    return (
        rec.get("employeeName") or "—",
        rec.get("departmentName") or "—",
        rec.get("scheduleName") or "—",
    )


def build_missing_message(rec: dict, plan_dt: datetime, now_dt: datetime) -> str:
    emp_name, dept, schedule = _head(rec)
    passed_mins = int((now_dt - plan_dt).total_seconds() / 60)
    return (
        "🚨 <b>Отсутствует на месте</b>\n\n"
        f"👤 Сотрудник: {emp_name}\n"
        f"🏢 Отдел: {dept}\n"
        f"📅 График: {schedule}\n"
        f"🕐 План: {format_time(plan_dt)}\n"
        f"🕑 Факт: Нет отметки\n"
        f"⏱ Прошло с начала смены: <b>{passed_mins} мин.</b>"
    )


def build_late_message(rec: dict, plan_dt: datetime, fact_dt: datetime, late_mins: int) -> str:
    emp_name, dept, schedule = _head(rec)
    location = rec.get("inLocationName") or rec.get("locationName") or "—"
    return (
        "⏰ <b>Фактическое опоздание</b>\n\n"
        f"👤 Сотрудник: {emp_name}\n"
        f"🏢 Отдел: {dept}\n"
        f"📅 График: {schedule}\n"
        f"🕐 План: {format_time(plan_dt)}\n"
        f"🕑 Факт: {format_time(fact_dt)}\n"
        f"⏱ Опоздание: <b>{late_mins} мин.</b>\n"
        f"📍 Локация: {location}"
    )


def build_early_out_message(rec: dict, plan_end_dt: datetime, fact_out_dt: datetime, early_mins: int) -> str:
    emp_name, dept, schedule = _head(rec)
    location = rec.get("outLocationName") or rec.get("locationName") or "—"
    return (
        "🏃 <b>Ранний уход</b>\n\n"
        f"👤 Сотрудник: {emp_name}\n"
        f"🏢 Отдел: {dept}\n"
        f"📅 График: {schedule}\n"
        f"🕐 Конец смены: {format_time(plan_end_dt)}\n"
        f"🕑 Ушел: {format_time(fact_out_dt)}\n"
        f"⏱ Ушел раньше на: <b>{early_mins} мин.</b>\n"
        f"📍 Локация: {location}"
    )


def build_missing_out_message(rec: dict, plan_end_dt: datetime, now_dt: datetime) -> str:
    emp_name, dept, schedule = _head(rec)
    passed_mins = int((now_dt - plan_end_dt).total_seconds() / 60)
    return (
        "🚨 <b>Нет отметки об уходе</b>\n\n"
        f"👤 Сотрудник: {emp_name}\n"
        f"🏢 Отдел: {dept}\n"
        f"📅 График: {schedule}\n"
        f"🕐 Конец смены: {format_time(plan_end_dt)}\n"
        f"🕑 Факт: Нет отметки\n"
        f"⏱ Прошло с конца смены: <b>{passed_mins} мин.</b>"
    )


def build_late_out_message(rec: dict, plan_end_dt: datetime, fact_out_dt: datetime, late_out_mins: int) -> str:
    emp_name, dept, schedule = _head(rec)
    location = rec.get("outLocationName") or rec.get("locationName") or "—"
    return (
        "⏰ <b>Поздний уход</b>\n\n"
        f"👤 Сотрудник: {emp_name}\n"
        f"🏢 Отдел: {dept}\n"
        f"📅 График: {schedule}\n"
        f"🕐 Конец смены: {format_time(plan_end_dt)}\n"
        f"🕑 Ушел: {format_time(fact_out_dt)}\n"
        f"⏱ Ушел позже на: <b>{late_out_mins} мин.</b>\n"
        f"📍 Локация: {location}"
    )


def build_suspicious_mark_message(mark: dict) -> str:
    emp_name = employee_name(mark)
    dept = mark.get("departmentName") or mark.get("department") or "—"
    mtype_val = mark_type(mark)
    mtype = "Вход" if mtype_val == 0 else "Выход" if mtype_val == 1 else "—"
    device = mark.get("deviceName") or mark.get("location") or "—"
    return (
        "⚠️ <b>Подозрительная отметка</b>\n\n"
        f"👤 Сотрудник: {emp_name}\n"
        f"🏢 Отдел: {dept}\n"
        f"🕒 Время: {format_time(parse_dt(mark_date(mark)))}\n"
        f"🔄 Тип: {mtype}\n"
        f"📱 Устройство: {device}"
    )


def build_welcome_message() -> str:
    return (
        "🎉 <b>Этот чат подключён к контролю опозданий</b>\n\n"
        "Сюда будут приходить уведомления о нарушениях графика: опоздания, ранние "
        "и поздние уходы, неявки и подозрительные отметки.\n\n"
        "<b>Команда для всех участников:</b>\n"
        "• <code>/report</code> — отчёт по посещаемости за сегодня\n"
        "• <code>/report ГГГГ-ММ-ДД</code> — за конкретную дату\n\n"
        "<i>Отделы чата и правила тишины настраиваются в разделе «Бот опозданий» на сайте.</i>"
    )


def build_test_message(now_text: str) -> str:
    return (
        "🔔 <b>Проверка связи</b>\n"
        f"Бот на месте и может писать в этот чат. Время: {now_text}."
    )
