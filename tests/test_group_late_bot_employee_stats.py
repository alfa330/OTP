# -*- coding: utf-8 -*-
"""Вкладка «Сотрудники» раздела «Бот опозданий».

Сводка считается по той же истории отбивок (`glb_events`), что показывает
вкладка «Отбивки», а город подтягивается из кадровой карточки у нас: связи
между сотрудником Workpace и нашим `users` нет, ключ — ФИО. Тесты держат две
вещи, на которых эта связка ломается молча:

* написание ФИО. Workpace пишет как в удостоверении («ҚҰРМАНОВ ҚАЙРАТ»),
  у нас в карточке — «Курманов Кайрат», и отчество есть не везде;
* тёзки. Показать чужой город хуже, чем прочерк, поэтому спорные случаи
  остаются пустыми.

Плюс правило таблицы: сотрудник, у которого за период были только неявки и
отсутствие отметки об уходе, в неё не попадает — строка из одних нулей
читалась бы как «нарушений нет».
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
VIEW_PATH = ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx"

DB_SOURCE = DB_PATH.read_text(encoding="utf-8-sig")
DB_TREE = ast.parse(DB_SOURCE)
VIEW_SRC = VIEW_PATH.read_text(encoding="utf-8-sig")

METHODS = (
    "_glb_parse_date",
    "_glb_department",
    "_glb_name_keys",
    "_glb_city_lookup",
    "get_group_late_employee_stats",
)
CLASS_ATTRS = ("_GLB_NAME_FOLD",)


def _db_class():
    """Заглушка Database с методами сводки: `import database` на Windows падает."""
    namespace = {"re": re, "datetime": datetime}
    cls_node = next(
        n for n in DB_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Database"
    )
    attrs = {}
    for node in cls_node.body:
        wanted = (
            isinstance(node, ast.FunctionDef) and node.name in METHODS
        ) or (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", None) in CLASS_ATTRS
        )
        if not wanted:
            continue
        module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(DB_PATH), "exec"), namespace)
        name = node.name if isinstance(node, ast.FunctionDef) else node.targets[0].id
        attrs[name] = namespace[name]
    missing = set(METHODS + CLASS_ATTRS) - set(attrs)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return type("StubDatabase", (), attrs)


StubDatabase = _db_class()


def event_row(employee, department="Регионы", late=0, late_minutes=0,
              early_out=0, early_out_minutes=0, suspicious=0, missing=0,
              last=date(2026, 8, 10)):
    """Строка агрегата в том порядке колонок, в каком её отдаёт SQL."""
    total = late + early_out + suspicious + missing
    return (department, employee, late, late_minutes, early_out, early_out_minutes,
            suspicious, missing, total, last)


def user_row(name, city, fired=False):
    return (name, city, fired)


class FakeCursor:
    """Отдаёт агрегат по glb_events и кадровые карточки по users."""

    def __init__(self, event_rows, user_rows):
        self.event_rows = event_rows
        self.user_rows = user_rows
        self.calls = []
        self._next = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        self._next = self.user_rows if "FROM users" in flat else self.event_rows

    def fetchall(self):
        return self._next


def run_stats(event_rows, user_rows=(), **kwargs):
    db = StubDatabase()
    cursor = FakeCursor(list(event_rows), list(user_rows))

    @contextmanager
    def _get_cursor():
        yield cursor

    db._get_cursor = _get_cursor
    return db.get_group_late_employee_stats(**kwargs), cursor


class NameKeyTests(unittest.TestCase):
    """Ключ ФИО: без него город к сотруднику не подобрать."""

    def keys(self, value):
        return StubDatabase._glb_name_keys(value)

    def test_kazakh_spelling_folds_to_the_card_spelling(self):
        # В Workpace — как в удостоверении, у нас в карточке — обиходно.
        self.assertEqual(self.keys("ҚҰРМАНОВ ҚАЙРАТ ")[0], self.keys("Курманов Кайрат")[0])
        self.assertEqual(self.keys("Тестбаев Анел")[0], self.keys("Тестбаев Анель")[0])
        self.assertEqual(self.keys("Сынакбай Нұрғали")[0], self.keys("Сынакбай Нургали")[0])

    def test_patronymic_matches_through_the_short_key(self):
        full, short = self.keys("Досанбаев Асан Тестович")
        self.assertNotEqual(full, self.keys("Досанбаев Асан")[0])
        self.assertEqual(short, self.keys("Досанбаев Асан")[0])

    def test_single_word_has_no_short_key(self):
        # Иначе одинокая фамилия совпала бы с любым однофамильцем.
        self.assertEqual(self.keys("Иванов"), ("иванов", None))

    def test_empty_name_gives_no_keys(self):
        self.assertEqual(self.keys("   "), (None, None))
        self.assertEqual(self.keys(None), (None, None))


class CityTests(unittest.TestCase):
    """Город — из кадровой карточки; спорные случаи остаются пустыми."""

    def test_city_comes_from_the_hr_card(self):
        result, _ = run_stats(
            [event_row("Кужагалиев Куаныш ", late=3, late_minutes=6)],
            [user_row("Кужагалиев Куаныш", "Костанай")],
        )
        employee = result["departments"][0]["employees"][0]
        self.assertEqual(employee["city"], "Костанай")
        self.assertTrue(result["departments"][0]["has_city"])

    def test_employee_outside_our_base_has_no_city(self):
        result, _ = run_stats(
            [event_row("ЖҮНІС Самат", late=1, late_minutes=5)],
            [user_row("Кужагалиев Куаныш", "Костанай")],
        )
        department = result["departments"][0]
        self.assertIsNone(department["employees"][0]["city"])
        self.assertFalse(department["has_city"])

    def test_namesakes_from_different_cities_stay_empty(self):
        result, _ = run_stats(
            [event_row("Иванов Иван", late=1, late_minutes=5)],
            [user_row("Иванов Иван", "Алматы"), user_row("Иванов Иван", "Астана")],
        )
        self.assertIsNone(result["departments"][0]["employees"][0]["city"])

    def test_fired_namesake_does_not_override_the_working_one(self):
        # SQL отдаёт действующих первыми (ORDER BY fired) — уволенный не спорит.
        result, _ = run_stats(
            [event_row("Иванов Иван", late=1, late_minutes=5)],
            [user_row("Иванов Иван", "Алматы"),
             user_row("Иванов Иван", "Астана", fired=True)],
        )
        self.assertEqual(result["departments"][0]["employees"][0]["city"], "Алматы")

    def test_city_lookup_reads_only_filled_cards(self):
        _, cursor = run_stats([event_row("Иванов Иван", late=1)],
                              [user_row("Иванов Иван", "Алматы")])
        users_sql = next(sql for sql, _ in cursor.calls if "FROM users" in sql)
        self.assertIn("btrim(u.city) <> ''", users_sql)
        self.assertIn("ORDER BY fired, u.id", users_sql)


class TableContentTests(unittest.TestCase):
    """Что попадает в таблицу и в каком порядке."""

    def test_only_other_violations_are_counted_aside(self):
        result, _ = run_stats([
            event_row("Опоздавший Иван", late=1, late_minutes=10),
            event_row("Неявившийся Пётр", missing=2),
        ])
        department = result["departments"][0]
        self.assertEqual([e["employee_name"] for e in department["employees"]],
                         ["Опоздавший Иван"])
        self.assertEqual(department["other_only_employees"], 1)
        self.assertEqual(department["totals"]["employees"], 1)

    def test_department_without_tracked_violations_is_dropped(self):
        result, _ = run_stats([event_row("Неявившийся Пётр", missing=2)])
        self.assertEqual(result["departments"], [])
        self.assertEqual(result["totals"]["employees"], 0)

    def test_suspicious_only_employee_stays_in_the_table(self):
        # Подозрительная отметка — самостоятельное нарушение, а не довесок.
        result, _ = run_stats([event_row("Отметившийся Пётр", suspicious=6)])
        self.assertEqual(result["departments"][0]["employees"][0]["suspicious_count"], 6)

    def test_totals_add_up_per_department_and_overall(self):
        result, _ = run_stats([
            event_row("Первый", late=2, late_minutes=30, suspicious=1),
            event_row("Второй", early_out=1, early_out_minutes=45),
            event_row("Третий", department="КЦ 3", late=1, late_minutes=5),
        ])
        by_name = {d["department_name"]: d for d in result["departments"]}
        self.assertEqual(by_name["Регионы"]["totals"], {
            "employees": 2, "late_count": 2, "late_minutes": 30,
            "early_out_count": 1, "early_out_minutes": 45, "suspicious_count": 1,
        })
        self.assertEqual(result["totals"]["departments"], 2)
        self.assertEqual(result["totals"]["employees"], 3)
        self.assertEqual(result["totals"]["late_count"], 3)
        self.assertEqual(result["totals"]["late_minutes"], 35)

    def test_departments_are_ordered_by_violations(self):
        result, _ = run_stats([
            event_row("Первый", department="Тихий отдел", late=1),
            event_row("Второй", department="Шумный отдел", late=5),
        ])
        self.assertEqual([d["department_name"] for d in result["departments"]],
                         ["Шумный отдел", "Тихий отдел"])

    def test_employees_are_ordered_by_lateness_then_name(self):
        result, _ = run_stats([
            event_row("Бета", late=1, late_minutes=5),
            event_row("Альфа", late=1, late_minutes=5),
            event_row("Гамма", late=3, late_minutes=1),
        ])
        self.assertEqual([e["employee_name"] for e in result["departments"][0]["employees"]],
                         ["Гамма", "Альфа", "Бета"])


class FilterTests(unittest.TestCase):
    """Период, отдел и поиск."""

    def test_period_and_department_reach_the_query(self):
        _, cursor = run_stats([event_row("Иванов Иван", late=1)],
                              date_from="2026-08-01", date_to="2026-08-10",
                              department="  Регионы  ")
        sql, params = cursor.calls[0]
        self.assertIn("e.event_date >= %s", sql)
        self.assertIn("e.event_date <= %s", sql)
        self.assertIn("lower(e.department_name) = lower(%s)", sql)
        self.assertEqual(params, [date(2026, 8, 1), date(2026, 8, 10), "Регионы"])

    def test_search_matches_the_city_too(self):
        rows = [event_row("Кужагалиев Куаныш", late=1),
                event_row("Сынаков Назым", late=1)]
        users = [user_row("Кужагалиев Куаныш", "Костанай"),
                 user_row("Сынаков Назым", "Алматы")]
        result, _ = run_stats(rows, users, search="костанай")
        self.assertEqual([e["employee_name"] for e in result["departments"][0]["employees"]],
                         ["Кужагалиев Куаныш"])

    def test_search_matches_name_case_insensitively(self):
        result, _ = run_stats([event_row("Кужагалиев Куаныш", late=1),
                               event_row("Сынаков Назым", late=1)], search="ОСПАН")
        self.assertEqual([e["employee_name"] for e in result["departments"][0]["employees"]],
                         ["Сынаков Назым"])

    def test_empty_period_gives_an_empty_answer(self):
        result, _ = run_stats([], date_from="2026-01-01", date_to="2026-01-02")
        self.assertEqual(result["departments"], [])
        self.assertEqual(result["totals"]["employees"], 0)
        self.assertEqual(result["date_from"], "2026-01-01")

    def test_bad_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            run_stats([], date_from="вчера")
        with self.assertRaises(ValueError):
            run_stats([], date_from="2026-08-10", date_to="2026-08-01")


class ViewTests(unittest.TestCase):
    """Фронт: вкладка, колонки и то, что город рисуется не всем подряд."""

    def test_tab_is_registered(self):
        self.assertIn("{ key: 'employees', label: 'Сотрудники', icon: Users }", VIEW_SRC)
        self.assertIn("{tab === 'employees' && renderEmployees()}", VIEW_SRC)

    def test_columns_match_the_request(self):
        for label in ("'Сотрудник'", "'Город'", "'Опозданий'", "'Минут опоздания'",
                      "'Минут раннего ухода'", "'Подозрительных отметок'"):
            self.assertIn(label, VIEW_SRC)

    def test_city_column_only_where_it_is_filled(self):
        # Пустая колонка «Город» у отделов с одним офисом — шум.
        self.assertIn("EMPLOYEE_COLUMNS.filter((column) => !column.cityOnly || department.has_city)",
                      VIEW_SRC)

    def test_scoped_head_does_not_pick_a_department(self):
        block = VIEW_SRC.split("const renderEmployees = () => {", 1)[1]
        block = block.split("const renderReports", 1)[0]
        self.assertIn("{!scoped && (", block)


if __name__ == "__main__":
    unittest.main()
