# -*- coding: utf-8 -*-
"""Вкладка «Сотрудники» раздела «Бот опозданий».

Таблица показывает ВЕСЬ состав отдела Workpace, а не только нарушителей:
состав приезжает в кэш `glb_employees` с опросом, нарушения считаются по
`glb_events`. Тесты держат три вещи, на которых это ломается молча:

* сшивка состава и нарушений. Один и тот же человек приходит в нарушения то
  с `employeeId`, то с `employeeExternalId`, а ФИО — с хвостовым пробелом;
* написание ФИО. Workpace пишет как в удостоверении («ҚҰРМАНОВ ҚАЙРАТ»),
  у нас в карточке — «Курманов Кайрат», и отчество есть не везде;
* граница матчинга. Справочники Workpace и наш не связаны, поэтому искать
  сотрудника у себя можно ТОЛЬКО в отделе с объявленной парой: иначе тёзка из
  соседнего отдела подставит чужое имя и чужой город.
"""

import ast
import copy
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
VIEW_PATH = ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx"

DB_SOURCE = DB_PATH.read_text(encoding="utf-8-sig")
DB_TREE = ast.parse(DB_SOURCE)
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
VIEW_SRC = VIEW_PATH.read_text(encoding="utf-8-sig")

MEMBERS = (
    "_glb_parse_date",
    "_glb_department",
    "_glb_name_keys",
    "_glb_index_put",
    "_glb_user_lookup",
    "_glb_stats_entry",
    "_glb_stats_sort_key",
    "get_group_late_employee_stats",
    "glb_sync_employees",
    "_GLB_NAME_FOLD",
    "GLB_STATS_COUNTERS",
)

OUR_DEPARTMENT_ID = 909          # front_office
WORKPACE_DEPARTMENT = "Регионы"
MATCH = {WORKPACE_DEPARTMENT: OUR_DEPARTMENT_ID}

CAPTURED_VALUES = []


def _fake_execute_values(cursor, sql, rows, template=None):
    CAPTURED_VALUES.append((" ".join(sql.split()), list(rows)))


def _db_class():
    """Заглушка Database: `import database` на Windows падает (time.tzset)."""
    namespace = {"re": re, "datetime": datetime, "execute_values": _fake_execute_values}
    cls_node = next(
        n for n in DB_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Database"
    )
    attrs = {}
    for node in cls_node.body:
        name = getattr(node, "name", None) or (
            getattr(node.targets[0], "id", None) if isinstance(node, ast.Assign) else None
        )
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


def event_row(employee, department=WORKPACE_DEPARTMENT, late=0, late_minutes=0,
              early_out=0, early_out_minutes=0, missing=0, suspicious=0,
              last=date(2026, 8, 10), ext_ids=()):
    """Строка агрегата ровно в том порядке колонок, в каком её отдаёт SQL.

    ФИО подрезаем: в запросе стоит btrim, и хвостовой пробел Workpace до
    Python не доезжает."""
    total = late + early_out + missing + suspicious
    return (department, employee.strip(), late, late_minutes, early_out, early_out_minutes,
            missing, suspicious, total, last, list(ext_ids))


def roster_row(employee, department=WORKPACE_DEPARTMENT, ext_id=None, external_id=None):
    return (department, employee.strip(), ext_id or f"id-{employee.strip()}", external_id)


def user_row(name, city=None, department_id=OUR_DEPARTMENT_ID, fired=False):
    return (department_id, name, city or "", fired)


class FakeCursor:
    """Раздаёт ответы по тому, из какой таблицы читает запрос."""

    def __init__(self, event_rows, roster_rows, user_rows):
        self.event_rows = event_rows
        self.roster_rows = roster_rows
        self.user_rows = user_rows
        self.calls = []
        self._rows = []
        self._one = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        if "FROM glb_events" in flat:
            self._rows, self._one = self.event_rows, None
        elif "FROM glb_employees p" in flat:
            self._rows, self._one = self.roster_rows, None
        elif "FROM glb_employees" in flat:
            self._rows = []
            self._one = (len(self.roster_rows), datetime(2026, 8, 10, 11, 24))
        elif "FROM users" in flat:
            self._rows, self._one = self.user_rows, None
        else:
            self._rows, self._one = [], None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def run_stats(event_rows=(), roster_rows=(), user_rows=(), **kwargs):
    db = StubDatabase()
    cursor = FakeCursor(list(event_rows), list(roster_rows), list(user_rows))

    @contextmanager
    def _get_cursor():
        yield cursor

    db._get_cursor = _get_cursor
    return db.get_group_late_employee_stats(**kwargs), cursor


def only_department(result, name=WORKPACE_DEPARTMENT):
    return next(d for d in result["departments"] if d["department_name"] == name)


def by_name(department):
    return {e["employee_name"]: e for e in department["employees"]}


class NameKeyTests(unittest.TestCase):
    """Ключ ФИО: без него ни город, ни наше написание к сотруднику не подобрать."""

    def keys(self, value):
        return StubDatabase._glb_name_keys(value)

    def test_kazakh_spelling_folds_to_the_card_spelling(self):
        self.assertEqual(self.keys("ҚҰРМАНОВ ҚАЙРАТ ")[0], self.keys("Курманов Кайрат")[0])
        self.assertEqual(self.keys("Тестбаев Анел")[0], self.keys("Тестбаев Анель")[0])
        self.assertEqual(self.keys("Сынакбай Нұрғали")[0], self.keys("Сынакбай Нургали")[0])

    def test_patronymic_matches_through_the_short_key(self):
        full, short = self.keys("Досанбаев Асан Тестович")
        self.assertNotEqual(full, self.keys("Досанбаев Асан")[0])
        self.assertEqual(short, self.keys("Досанбаев Асан")[0])

    def test_single_word_has_no_short_key(self):
        self.assertEqual(self.keys("Иванов"), ("иванов", None))

    def test_empty_name_gives_no_keys(self):
        self.assertEqual(self.keys("   "), (None, None))
        self.assertEqual(self.keys(None), (None, None))


class RosterTests(unittest.TestCase):
    """В таблице весь состав отдела, а не только нарушители."""

    def test_employees_without_violations_are_listed(self):
        result, _ = run_stats(
            event_rows=[event_row("Опоздавший Иван", late=1, late_minutes=10)],
            roster_rows=[roster_row("Опоздавший Иван"), roster_row("Чистый Пётр")],
        )
        department = only_department(result)
        self.assertEqual(department["totals"]["employees"], 2)
        self.assertEqual(department["totals"]["employees_with_violations"], 1)
        clean = by_name(department)["Чистый Пётр"]
        self.assertEqual(clean["late_count"], 0)
        self.assertEqual(clean["events_total"], 0)
        self.assertTrue(clean["in_roster"])

    def test_department_without_any_violations_is_still_shown(self):
        result, _ = run_stats(roster_rows=[roster_row("Чистый Пётр")])
        self.assertEqual(len(result["departments"]), 1)
        self.assertEqual(result["totals"]["employees"], 1)
        self.assertEqual(result["totals"]["employees_with_violations"], 0)

    def test_dismissed_employee_with_violations_stays_without_roster(self):
        # Workpace отдаёт только действующих: уволенный из состава пропал,
        # но нарушения за период у него есть — строка обязана остаться.
        result, _ = run_stats(
            event_rows=[event_row("Уволенный Пётр", late=2, late_minutes=20)],
            roster_rows=[roster_row("Чистый Иван")],
        )
        entry = by_name(only_department(result))["Уволенный Пётр"]
        self.assertFalse(entry["in_roster"])
        self.assertEqual(entry["late_count"], 2)

    def test_violations_attach_by_ext_id_even_when_the_name_differs(self):
        result, _ = run_stats(
            event_rows=[event_row("ИВАНОВ И.", late=1, late_minutes=5, ext_ids=["wp-1"])],
            roster_rows=[roster_row("Иванов Иван", ext_id="wp-1")],
        )
        department = only_department(result)
        self.assertEqual(department["totals"]["employees"], 1)
        self.assertEqual(department["employees"][0]["employee_name"], "Иванов Иван")
        self.assertEqual(department["employees"][0]["late_count"], 1)

    def test_violations_attach_by_external_id(self):
        # В нарушениях лежит employeeExternalId, в составе — оба идентификатора.
        result, _ = run_stats(
            event_rows=[event_row("Кто-то", suspicious=3, ext_ids=["ext-9"])],
            roster_rows=[roster_row("Иванов Иван", ext_id="wp-1", external_id="ext-9")],
        )
        self.assertEqual(only_department(result)["totals"]["employees"], 1)
        self.assertEqual(only_department(result)["employees"][0]["suspicious_count"], 3)

    def test_violations_attach_by_name_when_ids_diverge(self):
        result, _ = run_stats(
            event_rows=[event_row("Иванов Иван ", late=1, late_minutes=5, ext_ids=["other"])],
            roster_rows=[roster_row("Иванов Иван", ext_id="wp-1")],
        )
        self.assertEqual(only_department(result)["totals"]["employees"], 1)

    def test_two_aggregate_rows_of_one_person_are_summed(self):
        # Один и тот же человек приходит и по смене, и по отметке терминала.
        result, _ = run_stats(
            event_rows=[
                event_row("Иванов Иван", late=1, late_minutes=5, ext_ids=["wp-1"]),
                event_row("Иванов Иван ", suspicious=2, ext_ids=["ext-9"]),
            ],
            roster_rows=[roster_row("Иванов Иван", ext_id="wp-1", external_id="ext-9")],
        )
        department = only_department(result)
        self.assertEqual(department["totals"]["employees"], 1)
        self.assertEqual(department["employees"][0]["late_count"], 1)
        self.assertEqual(department["employees"][0]["suspicious_count"], 2)

    def test_roster_meta_is_returned(self):
        result, _ = run_stats(roster_rows=[roster_row("Чистый Пётр")])
        self.assertEqual(result["roster_total"], 1)
        self.assertTrue(result["roster_synced_at"].startswith("2026-08-10"))


class MatchTests(unittest.TestCase):
    """ФИО и город — из нашей карточки, но только в отделе с объявленной парой."""

    def test_our_spelling_replaces_the_workpace_one(self):
        result, _ = run_stats(
            roster_rows=[roster_row("ҚҰРМАНОВ ҚАЙРАТ ")],
            user_rows=[user_row("Курманов Кайрат", "Талдыкорган")],
            match_departments=MATCH,
        )
        entry = only_department(result)["employees"][0]
        self.assertEqual(entry["employee_name"], "Курманов Кайрат")
        self.assertEqual(entry["workpace_name"], "ҚҰРМАНОВ ҚАЙРАТ")
        self.assertEqual(entry["city"], "Талдыкорган")
        self.assertTrue(entry["matched"])
        self.assertTrue(only_department(result)["matched_department"])
        self.assertTrue(only_department(result)["has_city"])

    def test_patronymic_in_workpace_still_matches(self):
        result, _ = run_stats(
            roster_rows=[roster_row("Досанбаев Асан Тестович")],
            user_rows=[user_row("Досанбаев Асан", "Актобе")],
            match_departments=MATCH,
        )
        self.assertEqual(only_department(result)["employees"][0]["employee_name"],
                         "Досанбаев Асан")

    def test_employee_outside_our_base_keeps_the_workpace_name(self):
        result, _ = run_stats(
            roster_rows=[roster_row("ЖҮНІС Самат")],
            user_rows=[user_row("Курманов Кайрат", "Талдыкорган")],
            match_departments=MATCH,
        )
        entry = only_department(result)["employees"][0]
        self.assertEqual(entry["employee_name"], "ЖҮНІС Самат")
        self.assertFalse(entry["matched"])
        self.assertIsNone(entry["city"])

    def test_department_without_a_pair_is_not_matched_at_all(self):
        # «КЦ 3» с нашими отделами не связан: тёзка из СЗоВ дал бы чужое имя.
        result, _ = run_stats(
            roster_rows=[roster_row("Досанова Дана", department="КЦ 3")],
            user_rows=[user_row("Досанова Дана", "Алматы")],
            match_departments=MATCH,
        )
        department = only_department(result, "КЦ 3")
        self.assertFalse(department["matched_department"])
        self.assertFalse(department["employees"][0]["matched"])
        self.assertEqual(department["employees"][0]["employee_name"], "Досанова Дана")
        self.assertIsNone(department["employees"][0]["city"])

    def test_only_paired_departments_are_read_from_users(self):
        _, cursor = run_stats(roster_rows=[roster_row("Кто-то")], match_departments=MATCH)
        users_sql, params = next((c for c in cursor.calls if "FROM users" in c[0]), (None, None))
        self.assertIsNotNone(users_sql)
        self.assertIn("u.department_id = ANY(%s)", users_sql)
        self.assertEqual(params, ([OUR_DEPARTMENT_ID],))

    def test_users_are_not_read_without_a_pair(self):
        _, cursor = run_stats(roster_rows=[roster_row("Кто-то")])
        self.assertFalse([c for c in cursor.calls if "FROM users" in c[0]])

    def test_namesakes_in_our_base_are_not_guessed(self):
        result, _ = run_stats(
            roster_rows=[roster_row("Иванов Иван")],
            user_rows=[user_row("Иванов Иван", "Алматы"), user_row("Иванов Иван", "Астана")],
            match_departments=MATCH,
        )
        entry = only_department(result)["employees"][0]
        self.assertFalse(entry["matched"])
        self.assertIsNone(entry["city"])

    def test_fired_namesake_does_not_override_the_working_one(self):
        result, _ = run_stats(
            roster_rows=[roster_row("Иванов Иван")],
            user_rows=[user_row("Иванов Иван", "Алматы"),
                       user_row("Иванов Иван", "Астана", fired=True)],
            match_departments=MATCH,
        )
        self.assertEqual(only_department(result)["employees"][0]["city"], "Алматы")

    def test_matched_employee_without_a_city_leaves_the_column_off(self):
        result, _ = run_stats(
            roster_rows=[roster_row("Тестбек Даурен")],
            user_rows=[user_row("Тестбек Даурен", None)],
            match_departments=MATCH,
        )
        department = only_department(result)
        self.assertTrue(department["employees"][0]["matched"])
        self.assertFalse(department["has_city"])


class TableTests(unittest.TestCase):
    """Итоги, порядок и фильтры."""

    def test_totals_add_up_per_department_and_overall(self):
        result, _ = run_stats(
            event_rows=[
                event_row("Первый", late=2, late_minutes=30, suspicious=1),
                event_row("Второй", early_out=1, early_out_minutes=45, missing=2),
                event_row("Третий", department="КЦ 3", late=1, late_minutes=5),
            ],
            roster_rows=[roster_row("Первый"), roster_row("Второй"), roster_row("Чистый"),
                         roster_row("Третий", department="КЦ 3")],
        )
        self.assertEqual(only_department(result)["totals"], {
            "employees": 3, "employees_with_violations": 2, "late_count": 2,
            "late_minutes": 30, "early_out_count": 1, "early_out_minutes": 45,
            "missing_count": 2, "suspicious_count": 1,
        })
        self.assertEqual(result["totals"]["departments"], 2)
        self.assertEqual(result["totals"]["employees"], 4)
        self.assertEqual(result["totals"]["employees_with_violations"], 3)
        self.assertEqual(result["totals"]["late_minutes"], 35)

    def test_departments_are_ordered_by_violations(self):
        result, _ = run_stats(
            event_rows=[event_row("Первый", department="Тихий отдел", late=1),
                        event_row("Второй", department="Шумный отдел", late=5)],
            roster_rows=[roster_row("Первый", department="Тихий отдел"),
                         roster_row("Второй", department="Шумный отдел")],
        )
        self.assertEqual([d["department_name"] for d in result["departments"]],
                         ["Шумный отдел", "Тихий отдел"])

    def test_clean_employees_go_to_the_bottom(self):
        result, _ = run_stats(
            event_rows=[event_row("Гамма", late=3, late_minutes=1),
                        event_row("Дельта", missing=2)],
            roster_rows=[roster_row("Альфа"), roster_row("Бета"),
                         roster_row("Гамма"), roster_row("Дельта")],
        )
        self.assertEqual([e["employee_name"] for e in only_department(result)["employees"]],
                         ["Гамма", "Дельта", "Альфа", "Бета"])

    def test_period_and_department_reach_the_query(self):
        _, cursor = run_stats(event_rows=[event_row("Иванов Иван", late=1)],
                              date_from="2026-08-01", date_to="2026-08-10",
                              department="  Регионы  ")
        events_sql, params = next(c for c in cursor.calls if "FROM glb_events" in c[0])
        self.assertIn("e.event_date >= %s", events_sql)
        self.assertIn("e.event_date <= %s", events_sql)
        self.assertIn("lower(e.department_name) = lower(%s)", events_sql)
        self.assertEqual(params, [date(2026, 8, 1), date(2026, 8, 10), "Регионы"])
        roster_sql, roster_params = next(c for c in cursor.calls if "FROM glb_employees p" in c[0])
        self.assertIn("lower(p.department_name) = lower(%s)", roster_sql)
        self.assertEqual(roster_params, ["Регионы"])

    def test_search_matches_our_name_workpace_name_and_city(self):
        rows = [roster_row("ҚҰРМАНОВ ҚАЙРАТ"), roster_row("Сынаков Назым")]
        users = [user_row("Курманов Кайрат", "Талдыкорган"), user_row("Сынаков Назым", "Алматы")]
        for query, expected in (
            ("курманов", ["Курманов Кайрат"]),          # наше написание
            ("ҚҰРМАНОВ", ["Курманов Кайрат"]),          # написание Workpace
            ("талдыкорган", ["Курманов Кайрат"]),         # город
            ("алматы", ["Сынаков Назым"]),
        ):
            result, _ = run_stats(roster_rows=rows, user_rows=users,
                                  match_departments=MATCH, search=query)
            self.assertEqual([e["employee_name"] for e in only_department(result)["employees"]],
                             expected, query)

    def test_empty_period_gives_an_empty_answer(self):
        result, _ = run_stats(date_from="2026-01-01", date_to="2026-01-02")
        self.assertEqual(result["departments"], [])
        self.assertEqual(result["totals"]["employees"], 0)
        self.assertEqual(result["date_from"], "2026-01-01")

    def test_bad_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            run_stats(date_from="вчера")
        with self.assertRaises(ValueError):
            run_stats(date_from="2026-08-10", date_to="2026-08-01")


class SyncEmployeesTests(unittest.TestCase):
    """Кэш состава: полная замена, иначе уволенные копятся вечно."""

    def setUp(self):
        CAPTURED_VALUES.clear()
        self.db = StubDatabase()
        self.cursor = FakeCursor([], [], [])

        @contextmanager
        def _get_cursor():
            yield self.cursor

        self.db._get_cursor = _get_cursor

    def test_rows_are_cleaned_and_deduped(self):
        count = self.db.glb_sync_employees([
            {"ext_id": " wp-1 ", "full_name": " Иванов Иван ", "department_name": " Регионы "},
            {"ext_id": "wp-1", "full_name": "Дубль"},          # тот же id — второй раз не берём
            {"ext_id": "", "full_name": "Без id"},
            {"ext_id": "wp-2", "full_name": "   "},
        ])
        self.assertEqual(count, 1)
        _, rows = CAPTURED_VALUES[0]
        self.assertEqual(rows, [("wp-1", None, "Иванов Иван", "Регионы")])

    def test_missing_employees_are_deleted(self):
        self.db.glb_sync_employees([{"ext_id": "wp-1", "full_name": "Иванов Иван"}])
        delete_sql, params = next(c for c in self.cursor.calls if "DELETE FROM glb_employees" in c[0])
        self.assertIn("ext_id <> ALL(%s)", delete_sql)
        self.assertEqual(params, (["wp-1"],))

    def test_empty_payload_does_not_wipe_the_cache(self):
        # Пустой ответ Workpace — это сбой, а не «уволили всех».
        self.assertEqual(self.db.glb_sync_employees([]), 0)
        self.assertEqual(self.cursor.calls, [])


class RosterHelperTests(unittest.TestCase):
    """group_late.employee_roster — что кладём в кэш."""

    def setUp(self):
        from group_late.departments import employee_roster
        self.employee_roster = employee_roster

    def test_both_identifiers_and_department_are_kept(self):
        rows = self.employee_roster([{
            "id": "wp-1", "externalId": "ext-9",
            "lastName": "Иванов", "firstName": "Иван",
            "departmentName": "Регионы",
        }])
        self.assertEqual(rows, [{
            "ext_id": "wp-1", "external_id": "ext-9",
            "full_name": "Иванов Иван", "department_name": "Регионы",
        }])

    def test_employee_without_id_or_name_is_skipped(self):
        self.assertEqual(self.employee_roster([{"id": "wp-1"}, {"fullName": "Без id"}]), [])

    def test_department_falls_back_to_no_department(self):
        rows = self.employee_roster([{"id": "wp-1", "fullName": "Иванов Иван"}])
        self.assertEqual(rows[0]["department_name"], "Без отдела")


class WiringTests(unittest.TestCase):
    """Состав наполняется опросом, а пара отделов приходит из одного справочника."""

    def test_poll_syncs_the_roster(self):
        source = (ROOT / "group_late" / "lateness.py").read_text(encoding="utf-8-sig")
        self.assertIn("db.glb_sync_employees(employee_roster(employees))", source)

    def test_manual_sync_button_refreshes_the_roster(self):
        self.assertIn("db.glb_sync_employees(group_late.employee_roster(employees))", BOT_SOURCE)

    def test_endpoint_passes_the_department_pairs(self):
        node = next(n for n in ast.walk(ast.parse(BOT_SOURCE))
                    if isinstance(n, ast.FunctionDef) and n.name == "api_group_late_bot_employees")
        source = ast.get_source_segment(BOT_SOURCE, node)
        self.assertIn("match_departments=", source)
        self.assertIn("_group_late_bot_scope_by_department_id()", source)


class ViewTests(unittest.TestCase):
    """Фронт: вкладка, колонки и то, что город рисуется не всем подряд."""

    def test_tab_is_registered(self):
        self.assertIn("{ key: 'employees', label: 'Сотрудники', icon: Users }", VIEW_SRC)
        self.assertIn("{tab === 'employees' && renderEmployees()}", VIEW_SRC)

    def test_columns_match_the_request(self):
        for label in ("'Сотрудник'", "'Город'", "'Опозданий'", "'Минут опоздания'",
                      "'Минут раннего ухода'", "'Неявок'", "'Подозрительных отметок'"):
            self.assertIn(label, VIEW_SRC)

    def test_city_column_only_where_it_is_filled(self):
        self.assertIn("EMPLOYEE_COLUMNS.filter((column) => !column.cityOnly || department.has_city)",
                      VIEW_SRC)

    def test_unmatched_employee_is_marked_only_in_paired_departments(self):
        self.assertIn("department.matched_department && !row.matched", VIEW_SRC)

    def test_workpace_spelling_stays_reachable(self):
        # Подменили ФИО — исходное написание должно остаться в подсказке.
        self.assertIn("row.workpace_name", VIEW_SRC)

    def test_scoped_head_does_not_pick_a_department(self):
        block = VIEW_SRC.split("const renderEmployees = () => {", 1)[1]
        block = block.split("const renderReports", 1)[0]
        self.assertIn("{!scoped && (", block)


if __name__ == "__main__":
    unittest.main()
