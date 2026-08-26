# -*- coding: utf-8 -*-
"""План контроля опозданий из графика iCore вместо расписания Workpace.

Задача #246: у отдела, который ведёт график у себя, план должен браться из
«Графиков работы», а не из Workpace — там он оказывался плоским («всем 09:00»),
а дубли карточек давали неявку человеку, который отметился на второй своей
карточке. Факт при этом остаётся за Workpace: своего источника отметок у нас нет.

Тесты держат то, что ломается молча:

* подмена происходит ТОЛЬКО у переключённых отделов и именно подмена, а не
  добавление — иначе человек получит два плана и два нарушения за день;
* смены одного дня сливаются в один интервал: ключ дедупликации события —
  «сотрудник + дата + тип», и вторая смена дала бы кандидата с тем же ключом;
* отметка ищется по ВСЕМ карточкам человека в Workpace, а не по одной;
* человек без карточки Workpace в план не попадает — иначе неявка каждый день;
* переключение объявлено там же, где пара отделов: без пары сотрудника Workpace
  не с кем сопоставить у себя.
"""

import ast
import copy
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

from tests import source_cache

from group_late import config, icore_plan
from group_late.helpers import employee_keys
from group_late.lateness import find_violations
from group_late.mutes import MuteSnapshot

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database.py"
BOT_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
REPORTS_SOURCE = (ROOT / "group_late" / "reports.py").read_text(encoding="utf-8-sig")
LATENESS_SOURCE = (ROOT / "group_late" / "lateness.py").read_text(encoding="utf-8-sig")
VIEW_SOURCE = (ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx").read_text(
    encoding="utf-8-sig")

DAY = date(2026, 8, 26)
WORKPACE_DEPARTMENT = "Регионы"
OUR_CODE = "front_office"
OUR_DEPARTMENT_ID = 909


# ─────────────────────────────────────────────────────── сборка плана из снимка

def person(user_id, name, ext_ids=(), city=None, code=OUR_CODE):
    return {
        "user_id": user_id, "name": name, "city": city, "department_code": code,
        "workpace_ext_ids": list(ext_ids), "cards": [], "link_source": None,
    }


def shift(user_id, start, end, day=DAY):
    return {"user_id": user_id, "date": day, "start": start, "end": end}


class StubPlanDb:
    def __init__(self, people, shifts, unlinked=()):
        self.snapshot = {
            "people": list(people), "shifts": list(shifts), "unlinked": list(unlinked),
            "departments": {OUR_CODE: WORKPACE_DEPARTMENT},
        }
        self.calls = []

    def glb_icore_plan_snapshot(self, pairs, date_from, date_to, roster):
        self.calls.append((dict(pairs), date_from, date_to, list(roster)))
        return self.snapshot


def build(people, shifts, unlinked=(), day=DAY):
    db = StubPlanDb(people, shifts, unlinked)
    records, diagnostics = icore_plan.build_records(db, day, [])
    return db, records, diagnostics


def by_employee(records):
    return {record["employeeName"]: record for record in records}


class BuildRecordsTests(unittest.TestCase):
    def test_shift_becomes_a_timetable_span(self):
        _, records, _ = build([person(1, "Оспан Назым", ["card-1"])],
                              [shift(1, time(9, 0), time(18, 0))])
        record = records[0]
        self.assertEqual(record["workTimeStart"], "2026-08-26T09:00:00")
        self.assertEqual(record["workTimeEnd"], "2026-08-26T18:00:00")
        self.assertEqual(record["date"], "2026-08-26")
        self.assertEqual(record["departmentName"], WORKPACE_DEPARTMENT)
        self.assertEqual(record["scheduleName"], icore_plan.SCHEDULE_LABEL)

    def test_fact_fields_stay_empty_so_marks_are_bound_by_the_engine(self):
        """`inMark`/`outMark` заполняет Workpace, у нас их нет. Пустые поля
        включают ту же ветку find_violations, которая и раньше доставала
        непривязанный приход из первичных отметок."""
        _, records, _ = build([person(1, "Оспан Назым", ["card-1"])],
                              [shift(1, time(9, 0), time(18, 0))])
        self.assertIsNone(records[0]["inMark"])
        self.assertIsNone(records[0]["outMark"])
        self.assertEqual(records[0]["lateIn"], 0)
        self.assertEqual(records[0]["earlyOut"], 0)

    def test_two_shifts_in_a_day_merge_into_one_span(self):
        """Иначе два кандидата получат один event_key и второй молча пропадёт."""
        _, records, _ = build(
            [person(1, "Еспамбетов Жанибек", ["card-1"])],
            [shift(1, time(9, 0), time(13, 0)), shift(1, time(15, 0), time(19, 0))],
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["workTimeStart"], "2026-08-26T09:00:00")
        self.assertEqual(records[0]["workTimeEnd"], "2026-08-26T19:00:00")

    def test_night_shift_keeps_arrival_check_and_drops_the_exit_one(self):
        """Отметки опрос тянет за текущие сутки: «последний уход за день» для
        ночной смены поймал бы уход с ПРОШЛОЙ ночи — вышел бы ранний уход на
        сутки. Приход проверяем, уход — нет, и это видно в диагностике."""
        _, records, diagnostics = build([person(1, "Адильжанов Алмаз", ["card-N"])],
                                        [shift(1, time(22, 0), time(6, 0))])
        self.assertEqual(records[0]["workTimeStart"], "2026-08-26T22:00:00")
        self.assertIsNone(records[0]["workTimeEnd"])
        self.assertEqual(diagnostics["night_spans"], 1)

    def test_person_without_a_workpace_card_is_not_judged(self):
        """Отметок по нему взять негде: любая смена выглядела бы как неявка."""
        _, records, diagnostics = build(
            [person(1, "Хамкова Дарья"), person(2, "Оспан Назым", ["card-2"])],
            [shift(1, time(9, 0), time(18, 0)), shift(2, time(9, 0), time(18, 0))],
        )
        self.assertEqual([r["employeeName"] for r in records], ["Оспан Назым"])
        self.assertEqual(diagnostics["people_without_card"],
                         [{"user_id": 1, "name": "Хамкова Дарья"}])

    def test_all_workpace_cards_of_a_person_travel_with_the_span(self):
        _, records, _ = build([person(1, "Абдусатарова Дильназ", ["card-A", "card-B"])],
                              [shift(1, time(9, 0), time(18, 0))])
        self.assertEqual(records[0]["workpaceKeys"], ["card-A", "card-B"])
        self.assertEqual(records[0]["employeeId"], "card-A")

    def test_snapshot_is_asked_for_the_polled_day_only(self):
        db, _, _ = build([person(1, "Оспан Назым", ["card-1"])],
                         [shift(1, time(9, 0), time(18, 0))])
        pairs, date_from, date_to, _ = db.calls[0]
        self.assertEqual(pairs, {WORKPACE_DEPARTMENT: OUR_CODE})
        self.assertEqual((date_from, date_to), (DAY, DAY))


class MarkBindingTests(unittest.TestCase):
    """Отметка человека может лежать на любой его карточке Workpace."""

    def test_employee_keys_include_every_card(self):
        keys = employee_keys({"employeeId": "card-A", "workpaceKeys": ["card-A", "card-B"]})
        self.assertEqual(keys, {"card-A", "card-B"})

    def test_employee_keys_unchanged_for_workpace_records(self):
        self.assertEqual(employee_keys({"employeeId": "card-A"}), {"card-A"})

    def test_mark_on_the_second_card_makes_it_a_late_not_a_no_show(self):
        """Это и есть дефект, из-за которого человек каждый день числился
        отсутствующим: план Workpace держал на одной карточке, отметку — на другой."""
        _, records, _ = build([person(1, "Абдусатарова Дильназ", ["card-A", "card-B"])],
                              [shift(1, time(9, 0), time(18, 0))])
        marks = [{"employeeId": "card-B", "markDate": "2026-08-26T09:13:00",
                  "markType": 0, "status": 1}]
        lookup = {"by_id": {"card-A": WORKPACE_DEPARTMENT, "card-B": WORKPACE_DEPARTMENT},
                  "by_external_id": {}, "by_name": {}}
        found = find_violations(records, marks, lookup, MuteSnapshot([]),
                                datetime(2026, 8, 26, 12, 0, tzinfo=config.TZ))
        kinds = {item["event_type"]: item for item in found}
        self.assertIn("late", kinds)
        self.assertNotIn("missing", kinds)
        self.assertEqual(kinds["late"]["minutes"], 13)

    def test_mark_alias_map_points_every_card_at_the_planned_one(self):
        _, records, _ = build([person(1, "Абдусатарова Дильназ", ["card-A", "card-B"])],
                              [shift(1, time(9, 0), time(18, 0))])
        self.assertEqual(icore_plan.mark_alias_map(records),
                         {"card-A": "card-A", "card-B": "card-A"})


class ApplyToRecordsTests(unittest.TestCase):
    """Подмена, а не добавление: два плана на человека = два нарушения за день."""

    EMPLOYEES = [
        {"id": "card-1", "name": {"lastName": "Оспан", "firstName": "Назым"},
         "departmentName": WORKPACE_DEPARTMENT},
        {"id": "other-1", "name": {"lastName": "Тестов", "firstName": "Тест"},
         "departmentName": "Основной отдел"},
    ]

    def apply(self, workpace_records):
        db = StubPlanDb([person(1, "Оспан Назым", ["card-1"])],
                        [shift(1, time(9, 0), time(18, 0))])
        return icore_plan.apply_to_records(db, workpace_records, self.EMPLOYEES, DAY)

    def test_workpace_plan_of_a_switched_department_is_dropped(self):
        records, diagnostics = self.apply([
            {"employeeId": "card-1", "employeeName": "Оспан Назым",
             "departmentName": WORKPACE_DEPARTMENT, "workTimeStart": "2026-08-26T09:00:00"},
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheduleName"], icore_plan.SCHEDULE_LABEL)
        self.assertEqual(diagnostics["dropped_workpace_records"], 1)

    def test_other_departments_pass_through_untouched(self):
        foreign = {"employeeId": "other-1", "employeeName": "Тестов Тест",
                   "departmentName": "Основной отдел", "workTimeStart": "2026-08-26T09:00:00"}
        records, _ = self.apply([foreign])
        self.assertIn(foreign, records)
        self.assertEqual(len(records), 2)

    def test_broken_plan_leaves_workpace_data_in_place(self):
        """Контроль в отделе станет неточным, но не исчезнет совсем."""
        class Broken:
            def glb_icore_plan_snapshot(self, *args, **kwargs):
                raise RuntimeError("база недоступна")

        workpace = [{"employeeId": "card-1", "departmentName": WORKPACE_DEPARTMENT}]
        records, _ = icore_plan.apply_to_records(Broken(), workpace, self.EMPLOYEES, DAY)
        self.assertEqual(records, workpace)


# ─────────────────────────────────────────────────────────── снимок из базы

MEMBERS = (
    "_glb_parse_date", "_glb_name_keys", "_glb_index_put", "_glb_one_edit_apart",
    "_glb_resolve_entry", "_GLB_NAME_FOLD", "GLB_PLAN_BLOCKING_STATUSES",
    "_glb_plan_department_ids", "glb_icore_plan_snapshot",
)


def _db_class():
    """Заглушка Database: `import database` на Windows падает (time.tzset)."""
    tree = source_cache.parse(DB_PATH.read_text(encoding="utf-8-sig"))
    cls_node = next(n for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "Database")
    namespace = {"re": re, "datetime": datetime}
    attrs = {}
    for node in cls_node.body:
        name = getattr(node, "name", None) or (
            getattr(node.targets[0], "id", None) if isinstance(node, ast.Assign) else None)
        if name not in MEMBERS:
            continue
        module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(DB_PATH), "exec"), namespace)
        attrs[name] = namespace[name]
    missing = set(MEMBERS) - set(attrs)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return type("StubDatabase", (), attrs)


StubDatabase = _db_class()


class FakeCursor:
    def __init__(self, staff_rows, shift_rows):
        self.staff_rows = staff_rows
        self.shift_rows = shift_rows
        self.calls = []
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        if "FROM departments" in flat:
            self._rows = [(OUR_DEPARTMENT_ID, OUR_CODE)]
        elif "FROM work_shifts" in flat:
            self._rows = self.shift_rows
        elif "FROM users" in flat:
            self._rows = self.staff_rows
        else:
            self._rows = []

    def fetchall(self):
        return self._rows


def snapshot(staff, workpace, links=None, shifts=()):
    db = StubDatabase()
    cursor = FakeCursor(list(staff), list(shifts))

    @contextmanager
    def _get_cursor():
        yield cursor

    db._get_cursor = _get_cursor
    db.glb_employee_links = lambda: dict(links or {})
    result = db.glb_icore_plan_snapshot(
        {WORKPACE_DEPARTMENT: OUR_CODE}, DAY, DAY, workpace)
    return result, cursor


def staff_row(user_id, name, city="", department_id=OUR_DEPARTMENT_ID):
    return (user_id, name, city, department_id)


def card(ext_id, full_name, department=WORKPACE_DEPARTMENT):
    return {"ext_id": ext_id, "full_name": full_name, "department_name": department}


class SnapshotTests(unittest.TestCase):
    def test_cards_are_matched_by_name_across_spellings(self):
        result, _ = snapshot([staff_row(1, "Канатханов Куаныш", "Талдыкорган")],
                             [card("card-1", "ҚАНАТХАНОВ ҚУАНЫШ")])
        found = result["people"][0]
        self.assertEqual(found["workpace_ext_ids"], ["card-1"])
        self.assertEqual(found["link_source"], "name")
        self.assertEqual(result["unlinked"], [])

    def test_two_cards_land_on_one_person(self):
        result, _ = snapshot([staff_row(1, "Абдусатарова Дильназ")],
                             [card("card-A", "Абдусатарова Дильназ"),
                              card("card-B", "Абдусатарова Дильназ")])
        self.assertEqual(result["people"][0]["workpace_ext_ids"], ["card-A", "card-B"])

    def test_manual_link_wins_over_the_name(self):
        result, _ = snapshot(
            [staff_row(1, "Хамкова Дарья")],
            [card("card-1", "Фаустова Дарья")],
            links={"card-1": 1},
        )
        self.assertEqual(result["people"][0]["workpace_ext_ids"], ["card-1"])
        self.assertEqual(result["people"][0]["link_source"], "manual")

    def test_unmatched_card_is_reported_not_dropped(self):
        result, _ = snapshot([staff_row(1, "Оспан Назым")],
                             [card("card-X", "Уалхан Айдар")])
        self.assertEqual(result["unlinked"],
                         [{"ext_id": "card-X", "full_name": "Уалхан Айдар",
                           "department_name": WORKPACE_DEPARTMENT, "reason": "no_match"}])

    def test_excluded_card_stays_visible_so_the_exclusion_can_be_undone(self):
        result, _ = snapshot([staff_row(1, "Оспан Назым")],
                             [card("card-X", "Чужая Компания")], links={"card-X": None})
        self.assertEqual([item["reason"] for item in result["unlinked"]], ["excluded"])
        self.assertEqual(result["people"][0]["workpace_ext_ids"], [])

    def test_cards_of_other_departments_are_ignored(self):
        result, _ = snapshot([staff_row(1, "Оспан Назым")],
                             [card("card-Z", "Оспан Назым", department="Основной отдел")])
        self.assertEqual(result["unlinked"], [])
        self.assertEqual(result["people"][0]["workpace_ext_ids"], [])

    def test_vacation_and_sick_leave_are_excluded_by_the_query(self):
        """Смена на время отпуска планом не считается — иначе неявка на весь отпуск."""
        _, cursor = snapshot([staff_row(1, "Оспан Назым")], [])
        sql, params = next(call for call in cursor.calls if "FROM work_shifts" in call[0])
        self.assertIn("operator_schedule_status_periods", sql)
        self.assertIn(list(StubDatabase.GLB_PLAN_BLOCKING_STATUSES), params)
        self.assertEqual(set(StubDatabase.GLB_PLAN_BLOCKING_STATUSES),
                         {"bs", "sick_leave", "annual_leave", "dismissal"})

    def test_fired_operators_are_not_in_the_plan(self):
        _, cursor = snapshot([staff_row(1, "Оспан Назым")], [])
        sql, _ = next(call for call in cursor.calls
                      if "FROM users" in call[0] and "department_id" in call[0])
        self.assertIn("<> 'fired'", sql)
        self.assertIn("= 'operator'", sql)


# ──────────────────────────────────────────────────────────── связность правок

class WiringTests(unittest.TestCase):
    def test_switched_department_has_a_declared_pair(self):
        """Без пары отделов сотрудника Workpace не с кем сопоставить у себя, а
        матчить по всей базе нельзя: тёзка подставит чужого человека."""
        match = re.search(r"GROUP_LATE_BOT_DEPARTMENT_SCOPES = \{([^}]*)\}", BOT_SOURCE)
        self.assertIsNotNone(match, "не нашли GROUP_LATE_BOT_DEPARTMENT_SCOPES")
        paired = dict(re.findall(r"'([^']+)':\s*'([^']+)'", match.group(1)))
        for workpace_name, code in config.ICORE_PLAN_DEPARTMENTS.items():
            self.assertEqual(paired.get(code), workpace_name,
                             f"отдел {workpace_name} переключён на план iCore без пары")

    def test_both_plan_consumers_are_switched(self):
        """Опрос и Excel-отчёт обязаны брать план из одного источника, иначе
        выгрузка и уведомления разойдутся на одних и тех же людях."""
        for source, label in ((LATENESS_SOURCE, "lateness.py"), (REPORTS_SOURCE, "reports.py")):
            self.assertIn("icore_plan.apply_to_records", source, label)

    def test_report_receives_the_database(self):
        """Без db отчёт молча остался бы на расписании Workpace."""
        self.assertIn("stats_out=stats, db=db", BOT_SOURCE)

    def test_section_shows_the_bridge(self):
        self.assertIn("/plan_links", BOT_SOURCE)
        self.assertIn("plan_links", VIEW_SOURCE)
        self.assertIn("нет карточки Workpace", VIEW_SOURCE)


if __name__ == "__main__":
    unittest.main()
