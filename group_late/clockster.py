"""Клиент Clockster — второй источник отметок: центральный офис.

ТОЛЬКО ЧТЕНИЕ. Интеграция статусов операторов через Clockster была убрана
01.08.2026 после того, как «висящие приходы» терминала дали 25 несуществующих
часов за неделю; здесь она НЕ возвращается. Из этого модуля ничего не попадает ни
в `operator_status_events`, ни в сегменты, ни в учёт часов — только в раздел
«Отметки» и его выгрузку (задача #273).

Почему `/schedules`, а не `/attendance`: одна ручка отдаёт сразу и план смены, и
сведённые приход/уход, и сырые отметки, и обед — то есть ровно то, что раздел
показывает. Плюс это дешёво: один запрос на 50 человек покрывает ВЕСЬ период
(даты приходят словарём внутри записи), тогда как Workpace тянется по одному дню.

Главная ловушка источника, из-за которой и появились «висящие приходы»: терминал
один на вход и выход, тип отметки Clockster угадывает и регулярно ошибается. Мы
намеренно берём его сведённые `in`/`out`, а не собираем пару из сырых отметок
сами: своя догадка была бы третьей и разошлась бы с тем, что видит кадровик в
кабинете Clockster.
"""

import logging
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

import requests

from group_late import config

logger = logging.getLogger(__name__)

# У ручек справочников предел 50, у /attendance — 1000. Разъезжаются молча,
# поэтому держим меньшее: /schedules отдаёт 50 и ссылку на следующую страницу.
PAGE_SIZE = 50
REQUEST_TIMEOUT = 90
# Окно запроса ограничено самим API: date_end больше date_start + 3 месяца даёт 422.
MAX_WINDOW_DAYS = 90
# Приход/уход в отметке: 1 — пришёл, 0 — ушёл. Проверено на данных, а не по доке:
# среди человеко-дней «приход без ухода» сырой статус равен 1 в 62 случаях из 65.
MARK_IN = 1
MARK_OUT = 0

MARK_SYSTEM = "clockster"


class ClocksterError(RuntimeError):
    """Clockster недоступен или ответил ошибкой."""


class ClocksterClient:
    def __init__(self):
        self._session = requests.Session()

    def _get(self, path: str, params: dict) -> dict:
        if not config.is_clockster_configured():
            raise ClocksterError("Не задан CLOCKSTER_API_TOKEN")
        url = f"{config.CLOCKSTER_BASE_URL}{path}"
        try:
            response = self._session.get(
                url, params=params,
                headers={"Authorization": f"Bearer {config.CLOCKSTER_API_TOKEN}",
                         "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise ClocksterError(f"Clockster {path}: {exc}") from exc
        except ValueError as exc:
            raise ClocksterError(f"Clockster {path}: некорректный JSON ({exc})") from exc

    def _get_all(self, path: str, params: dict) -> list[dict]:
        """Постраничный обход. `links.next` не переносит per_page и начинает отдавать
        по 15 записей, поэтому страницы запрашиваем номером, а не готовой ссылкой."""
        rows: list[dict] = []
        page = 1
        while True:
            payload = self._get(path, {**params, "per_page": PAGE_SIZE, "page": page})
            chunk = payload.get("data") or []
            rows.extend(chunk)
            if not chunk or not (payload.get("links") or {}).get("next"):
                break
            page += 1
            last_page = (payload.get("meta") or {}).get("last_page")
            if last_page and page > int(last_page):
                break
        logger.info("Clockster %s: получено %d записей", path, len(rows))
        return rows

    def get_schedules(self, date_start, date_end) -> list[dict]:
        """План + факт по каждому человеку на каждую дату периода."""
        start, end = _clamp_window(date_start, date_end)
        return self._get_all("/schedules", {
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
        })

    def get_users(self) -> list[dict]:
        """Справочник людей: должность, локация, отдел, телефон."""
        return self._get_all("/users", {})


def _clamp_window(date_start, date_end):
    """Период не длиннее окна API. Молча урезаем конец, а не падаем: раздел просит
    период сам, а 422 от Clockster выглядел бы как поломка выгрузки."""
    start = _as_date(date_start)
    end = _as_date(date_end)
    if end < start:
        start, end = end, start
    limit = start + timedelta(days=MAX_WINDOW_DAYS)
    if end > limit:
        logger.warning("Clockster: период %s..%s урезан до %s (окно API)", start, end, limit)
        end = limit
    return start, end


def _as_date(value) -> date_cls:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def full_name(user: dict) -> str:
    """«Фамилия Имя Отчество» — в том же порядке, что у Workpace и у нас."""
    parts = [
        str(user.get("last_name") or "").strip(),
        str(user.get("first_name") or "").strip(),
        str(user.get("middle_name") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def _titled(value) -> Optional[str]:
    if isinstance(value, dict):
        title = str(value.get("title") or "").strip()
        return title or None
    return None


def _plan_bounds(date_str: str, schedule: dict):
    """(начало, конец) смены строками ISO. Ночная смена кончается следующим днём."""
    time_start = str(schedule.get("time_start") or "").strip()
    time_end = str(schedule.get("time_end") or "").strip()
    if not time_start:
        return None, None
    offset = str(schedule.get("timezone") or "").strip()
    start = f"{date_str}T{time_start}{offset}"
    if not time_end:
        return start, None
    end_date = date_str
    if time_end <= time_start:
        # Конец не позже начала — смена переходит через полночь. Без этого сдвига
        # план кончался бы раньше, чем начался, и весь день читался как ранний уход.
        end_date = (_as_date(date_str) + timedelta(days=1)).isoformat()
    return start, f"{end_date}T{time_end}{offset}"


def to_records(schedule_rows, user_lookup=None):
    """Ответ /schedules → (записи смен, отметки) в форме, которую уже понимают
    find_violations и выгрузка.

    Формы намеренно совпадают с Workpace, чтобы правила (порог 15 минут, вычет
    обеда, статусы дня) считались ОДНИМ кодом для обоих источников: два расчёта
    разошлись бы на одних и тех же людях, а кадровик сверяет числа между собой.

    Коды типа отметки у источников ИНВЕРТИРОВАНЫ: у Workpace markType 0 — вход,
    у Clockster status 1 — приход. Приводим к соглашению Workpace, иначе приход и
    уход поменяются местами и «время в работе» станет отрицательным."""
    lookup = user_lookup or {}
    records: list[dict] = []
    marks: list[dict] = []

    for row in schedule_rows or []:
        user = row.get("user") or {}
        user_id = user.get("id")
        if user_id is None:
            continue
        emp_id = f"{MARK_SYSTEM}:{user_id}"
        name = full_name(user)
        extra = lookup.get(str(user_id), {})

        for date_str, cell in (row.get("dates") or {}).items():
            if not isinstance(cell, dict):
                continue
            schedule = cell.get("schedule") or {}
            # Отпуск, больничный, выходной: плана нет, и неявкой это не является.
            is_work = str(schedule.get("type") or "").strip().lower() == "work"
            if schedule and not is_work:
                schedule = {}

            location = (_titled(schedule.get("location"))
                        or extra.get("location"))
            department = (_titled(schedule.get("department"))
                          or extra.get("department")
                          or location)
            position = (_titled(schedule.get("position"))
                        or extra.get("position"))

            for mark in (cell.get("attendance") or []):
                when = mark.get("datetime")
                if not when:
                    continue
                status = mark.get("status")
                marks.append({
                    "employeeId": emp_id,
                    "employeeName": name,
                    "departmentName": department,
                    "markDate": when,
                    # Инверсия кодов: приход Clockster (1) → вход Workpace (0).
                    "markType": 0 if status == MARK_IN else 1,
                    # У Workpace status 0 означает неподтверждённую отметку и даёт
                    # событие «подозрительная». У Clockster такого флага нет, и
                    # выдавать его отметки за подозрительные нельзя.
                    "status": 1,
                    "location": location,
                    "deviceName": location,
                    "markSystem": MARK_SYSTEM,
                    "markSource": mark.get("source"),
                })

            plan_start, plan_end = _plan_bounds(date_str, schedule) if schedule else (None, None)
            if not plan_start and not cell.get("in") and not cell.get("out"):
                # Ни плана, ни факта — этого дня у человека просто нет.
                continue

            records.append({
                "employeeId": emp_id,
                "employeeExternalId": emp_id,
                "employeeName": name,
                "departmentName": department,
                "date": date_str,
                "workTimeStart": plan_start,
                "workTimeEnd": plan_end,
                "inMark": cell.get("in"),
                "outMark": cell.get("out"),
                # Опоздание и ранний уход считает общий код по плану и факту:
                # своих чисел Clockster не даёт, а грейс у него нулевой.
                "lateIn": 0,
                "earlyOut": 0,
                "scheduleName": str(schedule.get("title") or "").strip() or None,
                "employeeIsArchived": False,
                "locationName": location,
                "inLocationName": location,
                "outLocationName": location,
                "positionName": position,
                "markSystem": MARK_SYSTEM,
                # Настоящий обед этого человека по его расписанию. Лучше общего
                # правила: у части людей он не час, а у отпускных его нет вовсе.
                "breakSeconds": schedule.get("break_time"),
                "plannedSeconds": schedule.get("time_planned"),
            })

    logger.info("Clockster: %d смен и %d отметок", len(records), len(marks))
    return records, marks


def build_user_lookup(users) -> dict[str, dict]:
    """{id пользователя: должность/локация/отдел} — в клетке расписания они бывают
    пустыми, а в справочнике заполнены; иначе колонка «Должность» пустеет без причины."""
    lookup: dict[str, dict] = {}
    for user in users or []:
        user_id = user.get("id")
        if user_id is None:
            continue
        lookup[str(user_id)] = {
            "position": _titled(user.get("position")),
            "location": _titled(user.get("location")),
            "department": _titled(user.get("department")),
            "phone": str(user.get("phone") or "").strip() or None,
            "code": str(user.get("code") or "").strip() or None,
        }
    return lookup


clockster_client = ClocksterClient()
