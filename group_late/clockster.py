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
один на вход и выход, тип отметки Clockster угадывает и иногда ошибается.
Сведённые `in`/`out` клетки Clockster раздел больше НЕ берёт (ТЗ iCore 3, п. 4):
клетка — календарный день и угаданный тип, поэтому ночная смена рвалась на два
дня без пары, а «приход, приход» давал ноль часов. Смены собираются из сырых
отметок всего периода подряд — правило и его замер в `clockster_shifts`.
"""

import logging
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

import requests

from group_late import config
from group_late.clockster_shifts import IN, OUT, build_shifts, main_shift
from group_late.helpers import parse_dt

logger = logging.getLogger(__name__)

# У ручек справочников предел 50, у /attendance — 1000. Разъезжаются молча,
# поэтому держим меньшее: /schedules отдаёт 50 и ссылку на следующую страницу.
PAGE_SIZE = 50
REQUEST_TIMEOUT = 90
# Окно запроса ограничено самим API: date_end дальше date_start + 31 день даёт 422
# («The date must be a date before or equal to …», замер 29.09.2026; прежние
# «3 месяца» в документации не подтвердились). Длинный период режем на окна.
MAX_WINDOW_DAYS = 31
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
        """План + факт по каждому человеку на каждую дату периода.

        Период длиннее окна API собирается из нескольких запросов, а даты одного
        человека сливаются в одну запись: сведение смен идёт по всей ленте его
        отметок подряд, и ночная смена на стыке окон иначе порвалась бы."""
        merged: dict = {}
        order: list = []
        for start, end in _windows(date_start, date_end):
            for row in self._get_all("/schedules", {
                "date_start": start.isoformat(),
                "date_end": end.isoformat(),
            }):
                user_id = (row.get("user") or {}).get("id")
                if user_id not in merged:
                    merged[user_id] = {**row, "dates": dict(row.get("dates") or {})}
                    order.append(user_id)
                else:
                    merged[user_id]["dates"].update(row.get("dates") or {})
        return [merged[user_id] for user_id in order]

    def get_users(self) -> list[dict]:
        """Справочник людей: должность, локация, отдел, телефон."""
        return self._get_all("/users", {})


def _windows(date_start, date_end):
    """Период → окна не длиннее окна API, подряд и без пропусков."""
    start = _as_date(date_start)
    end = _as_date(date_end)
    if end < start:
        start, end = end, start
    windows = []
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + timedelta(days=MAX_WINDOW_DAYS))
        windows.append((window_start, window_end))
        window_start = window_end + timedelta(days=1)
    return windows


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
    уход поменяются местами и «время в работе» станет отрицательным.

    Приход и уход дня берутся из смен, собранных по всей ленте отметок человека
    (`clockster_shifts`), а не из клетки Clockster (ТЗ iCore 3, п. 4). Поэтому:
      * `markType` — тип, который угадал терминал (его читают часы СВ #352 и
        синхронизация их отметок — их правило пар на нём построено);
      * `markRole` — тип по порядку внутри смены, его показывает раздел;
      * `shiftDate` — день смены, к которой отметка относится: утренний уход
        ночной смены лежит в следующем календарном дне, а считается в день прихода;
      * у записи `factFromShifts` — приход и уход уже сведены, перебирать сырые
        отметки поверх них нельзя: ночной уход накануне стал бы уходом этого дня."""
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

        days: dict[str, dict] = {}
        plans: dict[str, tuple] = {}
        feed: list[dict] = []
        seen = set()
        for date_str, cell in sorted((row.get("dates") or {}).items()):
            if not isinstance(cell, dict):
                continue
            day = str(date_str)[:10]
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
            plan_start, plan_end = _plan_bounds(day, schedule) if schedule else (None, None)
            days[day] = {
                "schedule": schedule, "location": location, "department": department,
                "position": position, "plan_start": plan_start, "plan_end": plan_end,
            }
            start_dt = parse_dt(plan_start)
            if start_dt:
                plans[day] = (start_dt, parse_dt(plan_end),
                              schedule.get("boundary_start"), schedule.get("boundary_end"))

            for mark in (cell.get("attendance") or []):
                when = mark.get("datetime")
                at = parse_dt(when)
                if not at:
                    continue
                terminal = IN if mark.get("status") == MARK_IN else OUT
                # Отметка ночной смены лежит в клетках обоих соседних дней.
                key = (at, terminal)
                if key in seen:
                    continue
                seen.add(key)
                feed.append({"at": at, "terminal": terminal, "when": when,
                             "source": mark.get("source"), "location": location,
                             "department": department})

        shifts = build_shifts(feed, plans)
        for shift in shifts:
            for item, role in shift.roles():
                marks.append({
                    "employeeId": emp_id,
                    "employeeName": name,
                    "departmentName": item["department"],
                    "markDate": item["when"],
                    # Инверсия кодов: приход Clockster (1) → вход Workpace (0).
                    "markType": item["terminal"],
                    "markRole": role,
                    "shiftDate": shift.day.isoformat(),
                    # У Workpace status 0 означает неподтверждённую отметку и даёт
                    # событие «подозрительная». У Clockster такого флага нет, и
                    # выдавать его отметки за подозрительные нельзя.
                    "status": 1,
                    "location": item["location"],
                    "deviceName": item["location"],
                    "markSystem": MARK_SYSTEM,
                    "markSource": item["source"],
                })

        shift_days = {shift.day.isoformat() for shift in shifts}
        for day, info in days.items():
            if not info["plan_start"] and day not in shift_days:
                # Ни плана, ни своей смены — этого дня у человека просто нет.
                continue
            main = main_shift(shifts, day)
            arrival = main.arrival if main else None
            departure = main.departure if main else None
            schedule = info["schedule"]
            records.append({
                "employeeId": emp_id,
                "employeeExternalId": emp_id,
                "employeeName": name,
                "departmentName": info["department"],
                "date": day,
                "workTimeStart": info["plan_start"],
                "workTimeEnd": info["plan_end"],
                "inMark": arrival.isoformat() if arrival else None,
                "outMark": departure.isoformat() if departure else None,
                "factFromShifts": True,
                # Опоздание и ранний уход считает общий код по плану и факту:
                # своих чисел Clockster не даёт, а грейс у него нулевой.
                "lateIn": 0,
                "earlyOut": 0,
                "scheduleName": str(schedule.get("title") or "").strip() or None,
                "employeeIsArchived": False,
                "locationName": info["location"],
                "inLocationName": info["location"],
                "outLocationName": info["location"],
                "positionName": info["position"],
                "markSystem": MARK_SYSTEM,
                # Настоящий обед этого человека по его расписанию. Лучше общего
                # правила: у части людей он не час, а у отпускных его нет вовсе.
                "breakSeconds": schedule.get("break_time"),
                "plannedSeconds": schedule.get("time_planned"),
            })

    logger.info("Clockster: %d смен и %d отметок", len(records), len(marks))
    return records, marks


def roster(users) -> list[dict]:
    """Состав Clockster для кэша `glb_employees` — в форме `employee_roster`.

    Нужен затем же, зачем состав Workpace: без него центрального офиса нет ни в
    фильтре подразделений, ни в выборе людей для отчёта (ТЗ #307). Уволенных
    (`deleted_at`) пропускаем — иначе в списке копится история, которой в
    Workpace-половине справочника нет.

    Идентификатор тот же, что у строк раздела: `clockster:<id>`. Совпадение
    обязательно — по нему адресное правило графика находит человека, а строки
    кэша сходятся со справочником."""
    rows: list[dict] = []
    for user in users or []:
        user_id = user.get("id")
        if user_id is None or user.get("deleted_at"):
            continue
        name = full_name(user)
        if not name:
            continue
        location = _titled(user.get("location"))
        rows.append({
            "ext_id": f"{MARK_SYSTEM}:{user_id}",
            "external_id": str(user.get("code") or "").strip() or None,
            "full_name": name,
            # В справочнике отдел заполнен не у всех, а локация — почти у всех;
            # тот же порядок, что в `to_records`, иначе один человек оказался бы
            # в разных подразделениях в таблице и в фильтре.
            "department_name": _titled(user.get("department")) or location,
            "position_name": _titled(user.get("position")),
        })
    return rows


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
