"""Контроль опозданий по данным Workpace (бывший отдельный сервис group_late_bot).

Раньше это был свой сервис на Render со своим Telegram-ботом и состоянием в
JSON-файлах. Теперь всё живёт здесь: опрос Workpace идёт джобой планировщика,
уведомления шлёт наш бот, а настройки, найденные нарушения и отчёты лежат в glb_*
и управляются разделом «Бот опозданий» на сайте.
"""

from group_late.config import (
    LATE_THRESHOLD_MINUTES,
    MISSING_IN_AFTER_MINUTES,
    MISSING_OUT_AFTER_MINUTES,
    TZ,
    is_configured,
)
from group_late.departments import (
    NO_DEPARTMENT,
    build_employee_department_lookup,
    count_departments,
    department_name_from_fields,
    departments_allow,
    normalize_text,
    resolve_department_name,
)
from group_late import messages
from group_late.lateness import collect_events, finalize_deliveries
from group_late.reports import generate_report
from group_late.workpace import WorkpaceError, workpace_client

__all__ = [
    "LATE_THRESHOLD_MINUTES",
    "MISSING_IN_AFTER_MINUTES",
    "MISSING_OUT_AFTER_MINUTES",
    "NO_DEPARTMENT",
    "TZ",
    "WorkpaceError",
    "build_employee_department_lookup",
    "collect_events",
    "count_departments",
    "department_name_from_fields",
    "departments_allow",
    "finalize_deliveries",
    "generate_report",
    "messages",
    "is_configured",
    "normalize_text",
    "resolve_department_name",
    "workpace_client",
]
