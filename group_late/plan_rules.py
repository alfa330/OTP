"""Свой график смен раздела «Отметки» (ТЗ #307, п. 5).

Кадровик заводит план сам: на проект целиком или на конкретного человека, в том
числе на выходные дни. Модуль отвечает только на два вопроса — какое правило
действует на человека в этот день и во сколько по нему начинается и кончается
смена. Применение (пересчёт опоздания, статуса, синтез строк) живёт в
`attendance`: там же лежат определения метрик, и разводить их по двум модулям
нельзя — кадровик сверяет эти числа в одном окне.

Главное правило применения, которое стоит помнить, читая этот модуль: наш план
ДОПОЛНЯЕТ источники, а не спорит с ними. Он ставится только в те дни, где ни
Workpace, ни Clockster смены не дали. Иначе одно правило «пн–пт 10:00–19:00» на
отдел перебило бы настоящий сменный график колл-центра и сделало бы опоздавшими
разом весь отдел.
"""

from datetime import datetime, timedelta
from typing import Optional

from group_late.config import TZ
from group_late.departments import departments_allow, normalize_text

SCOPE_EMPLOYEE = "employee"
SCOPE_DEPARTMENT = "department"

MODE_SCHEDULE = "schedule"
MODE_HOURS = "hours"

# Подпись графика в таблице и в выгрузке, когда план поставлен нашим правилом.
PLAN_SOURCE_RULE = "rule"


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _window_allows(rule: dict, day) -> bool:
    date_from = rule.get("date_from")
    date_to = rule.get("date_to")
    if date_from and day < _as_date(date_from):
        return False
    if date_to and day > _as_date(date_to):
        return False
    weekdays = rule.get("weekdays") or []
    # Пустой список — «каждый день»: правило без выбранных дней недели иначе не
    # действовало бы никогда, а выглядело бы заведённым.
    return not weekdays or day.isoweekday() in weekdays


def _matches_target(rule: dict, employee_id: str, employee_name: str, department: str) -> bool:
    target = str(rule.get("target") or "").strip()
    if not target:
        return False
    if rule.get("scope") == SCOPE_EMPLOYEE:
        # Сотрудник опознаётся по идентификатору источника, а при его смене — по
        # ФИО: карточку в Workpace заводят заново, и правило иначе потерялось бы.
        return (normalize_text(target) == normalize_text(employee_id)
                or normalize_text(target) == normalize_text(employee_name))
    return departments_allow([target], department or "")


def _specificity(rule: dict) -> tuple:
    """Чем правило «уже», тем оно главнее.

    По убыванию: адресное правило на человека → правило с обеими границами дат →
    правило с выбранными днями недели → более позднее по номеру. Номер последним
    ключом нужен, чтобы два одинаково узких правила не менялись местами между
    запросами: порядок в таблице не гарантирован."""
    return (
        1 if rule.get("scope") == SCOPE_EMPLOYEE else 0,
        1 if (rule.get("date_from") and rule.get("date_to")) else 0,
        1 if (rule.get("weekdays") or []) else 0,
        int(rule.get("id") or 0),
    )


def select_rule(rules, employee_id, employee_name, department, day) -> Optional[dict]:
    """Правило, действующее на человека в этот день, либо None."""
    if not rules:
        return None
    target_day = _as_date(day)
    candidates = [
        rule for rule in rules
        if rule.get("enabled", True)
        and _window_allows(rule, target_day)
        and _matches_target(rule, str(employee_id or ""), str(employee_name or ""),
                            str(department or ""))
    ]
    if not candidates:
        return None
    return max(candidates, key=_specificity)


def plan_bounds(rule: dict, day):
    """(начало, конец) смены по правилу. Ночная смена кончается следующим днём."""
    if not rule or rule.get("mode") != MODE_SCHEDULE:
        return None, None
    time_start = str(rule.get("time_start") or "").strip()
    time_end = str(rule.get("time_end") or "").strip()
    if not time_start or not time_end:
        return None, None
    target_day = _as_date(day)
    try:
        start_h, start_m = (int(part) for part in time_start.split(":")[:2])
        end_h, end_m = (int(part) for part in time_end.split(":")[:2])
    except (TypeError, ValueError):
        return None, None
    start = datetime(target_day.year, target_day.month, target_day.day,
                     start_h, start_m, tzinfo=TZ)
    end = datetime(target_day.year, target_day.month, target_day.day, end_h, end_m, tzinfo=TZ)
    if end <= start:
        # Конец не позже начала — смена переходит через полночь. Тот же сдвиг, что
        # у Clockster: без него план кончался бы раньше, чем начался.
        end += timedelta(days=1)
    return start, end


def break_seconds(rule: dict):
    """Перерыв правила в секундах либо None — тогда работает общее правило обеда."""
    if not rule:
        return None
    minutes = rule.get("break_minutes")
    if minutes in (None, ""):
        return None
    try:
        return max(0, int(minutes)) * 60
    except (TypeError, ValueError):
        return None


def label(rule: dict) -> Optional[str]:
    """Как правило подписывается в колонке «График»."""
    if not rule:
        return None
    if rule.get("mode") == MODE_HOURS:
        norm = rule.get("hours_norm")
        return f"По часам · норма {_hours_label(norm)}" if norm else "По часам"
    time_start = str(rule.get("time_start") or "").strip()
    time_end = str(rule.get("time_end") or "").strip()
    if time_start and time_end:
        return f"{time_start}–{time_end}"
    return None


def _hours_label(value) -> str:
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return "—"
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    return f"{whole}:{minutes:02d}"


def covered_targets(rules, day):
    """Правила, действующие в этот день, — по ним достраиваются недостающие строки."""
    target_day = _as_date(day)
    return [rule for rule in (rules or [])
            if rule.get("enabled", True) and _window_allows(rule, target_day)]
