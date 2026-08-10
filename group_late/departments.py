"""Отделы Workpace: определение отдела сотрудника и маршрутизация по чатам.

Отделы здесь — из Workpace, с нашими `departments` они не связаны: названия
графиков и карточек сотрудников расходятся, поэтому отдел берём из карточки, а
сопоставляем нестрого — по вхождению строки в обе стороны.
"""

import re
from typing import Iterable, Optional

NO_DEPARTMENT = "Без отдела"


def _clean(value) -> str:
    return str(value or "").strip()


def normalize_text(value: str) -> str:
    return _clean(value).casefold()


def employee_full_name(employee: dict) -> str:
    name_value = employee.get("name")
    direct_name = _clean(
        employee.get("fullName")
        or employee.get("employeeName")
        or (name_value if not isinstance(name_value, dict) else "")
    )
    if direct_name:
        return direct_name

    name_obj = name_value if isinstance(name_value, dict) else {}
    parts = [
        _clean(employee.get("lastName") or name_obj.get("lastName")),
        _clean(employee.get("firstName") or name_obj.get("firstName")),
        _clean(employee.get("middleName") or name_obj.get("middleName")),
    ]
    return " ".join(part for part in parts if part)


def department_name_from_fields(item: dict) -> Optional[str]:
    for field in ("departmentName", "department"):
        value = _clean(item.get(field))
        if value:
            return value

    department_tree = _clean(item.get("departmentTree"))
    if department_tree:
        for separator in (" / ", "/", "\\", ">", "»"):
            if separator in department_tree:
                parts = [part.strip() for part in department_tree.split(separator) if part.strip()]
                if parts:
                    return parts[-1]
        return department_tree
    return None


def build_employee_department_lookup(employees: Iterable[dict]) -> dict[str, dict[str, str]]:
    lookup = {"by_id": {}, "by_external_id": {}, "by_name": {}}
    for employee in employees:
        department_name = department_name_from_fields(employee)
        if not department_name:
            continue

        employee_id = _clean(employee.get("id") or employee.get("employeeId"))
        if employee_id:
            lookup["by_id"][employee_id] = department_name

        external_id = _clean(employee.get("externalId") or employee.get("employeeExternalId"))
        if external_id:
            lookup["by_external_id"][external_id] = department_name

        full_name = employee_full_name(employee)
        if full_name:
            lookup["by_name"][normalize_text(full_name)] = department_name
    return lookup


def resolve_department_name(item: dict, employee_lookup: dict[str, dict[str, str]]) -> str:
    employee_id = _clean(item.get("employeeId") or item.get("id"))
    if employee_id and employee_id in employee_lookup.get("by_id", {}):
        return employee_lookup["by_id"][employee_id]

    external_id = _clean(item.get("employeeExternalId") or item.get("externalId"))
    if external_id and external_id in employee_lookup.get("by_external_id", {}):
        return employee_lookup["by_external_id"][external_id]

    name_value = item.get("name")
    employee_name = _clean(
        item.get("employeeName")
        or item.get("fullName")
        or (name_value if not isinstance(name_value, dict) else "")
    )
    if employee_name:
        by_name = employee_lookup.get("by_name", {})
        normalized_name = normalize_text(employee_name)
        if normalized_name in by_name:
            return by_name[normalized_name]

    return department_name_from_fields(item) or NO_DEPARTMENT


def count_departments(employees: Iterable[dict]) -> dict[str, int]:
    """Справочник отделов со числом сотрудников — кэш для раздела на сайте."""
    counts: dict[str, int] = {}
    for employee in employees:
        name = department_name_from_fields(employee)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def employee_roster(employees: Iterable[dict]) -> list[dict]:
    """Состав отделов для кэша `glb_employees`: id, ФИО, отдел.

    Оба идентификатора нужны потому, что в нарушения попадает то `employeeId`,
    то `employeeExternalId` — по одному из них сотрудник не всегда находится."""
    rows: list[dict] = []
    for employee in employees:
        ext_id = _clean(employee.get("id") or employee.get("employeeId"))
        full_name = employee_full_name(employee)
        if not ext_id or not full_name:
            continue
        rows.append({
            "ext_id": ext_id,
            "external_id": _clean(employee.get("externalId")
                                  or employee.get("employeeExternalId")) or None,
            "full_name": full_name,
            "department_name": department_name_from_fields(employee) or NO_DEPARTMENT,
        })
    return rows


def clean_department_filters(department_filters) -> list[str]:
    """Список отделов без пустых значений и регистрозависимых дублей.
    Строку режем по ';' и '|' — так их вводят в командах бота."""
    if department_filters is None:
        return []

    raw_values = department_filters
    if isinstance(raw_values, str):
        raw_values = re.split(r"\s*[;|]\s*", raw_values)
    elif not isinstance(raw_values, list):
        raw_values = [raw_values]

    clean_values: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        value = _clean(raw_value)
        if not value:
            continue
        normalized = normalize_text(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        clean_values.append(value)
    return clean_values


def departments_allow(department_filters: list[str], department_name: str) -> bool:
    """Пустой фильтр = чат получает все отделы."""
    if not department_filters:
        return True
    if not department_name:
        return False

    department_normalized = normalize_text(department_name)
    return any(
        filter_normalized in department_normalized
        or department_normalized in filter_normalized
        for filter_normalized in (normalize_text(value) for value in department_filters)
    )


def department_matches(department_name: str, department_filters: list[str]) -> bool:
    """То же сопоставление для фильтра отчёта."""
    return departments_allow(department_filters, department_name)
