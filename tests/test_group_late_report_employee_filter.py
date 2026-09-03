# -*- coding: utf-8 -*-
"""Выгрузка «по выбранному(-ым) сотруднику(-ам)» (задача #273).

Постановка требует отчёт не только по отделу, но и по конкретным людям. Здесь
сторожатся три вещи, каждая из которых уже ломалась в этом файле раньше или
ломается легко:

* отбор по ФИО — единственный доступный: стабильного общего идентификатора у
  Workpace и Clockster нет;
* нумерация колонок в чтении списка отчётов — добавление колонки в SELECT
  сдвигает ВСЕ индексы ниже, и ошибка тихо переставляет поля местами;
* выбор виден на карточке — иначе выгрузка по трём людям читается как выгрузка
  по всему отделу, а по ней делают выводы.
"""

import re
import unittest
from pathlib import Path

from group_late.reports import _employee_selected

ROOT = Path(__file__).resolve().parents[1]
DB_SRC = (ROOT / "database.py").read_text(encoding="utf-8-sig")
BOT_SRC = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
REPORTS_SRC = (ROOT / "group_late" / "reports.py").read_text(encoding="utf-8-sig")
VIEW_SRC = (ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx").read_text(encoding="utf-8-sig")


class SelectionTests(unittest.TestCase):
    def test_empty_selection_keeps_everyone(self):
        # Пустой список — это «весь отдел», как было до задачи, а не «никого».
        self.assertTrue(_employee_selected("Иванов Иван", set()))
        self.assertTrue(_employee_selected("Иванов Иван", None))

    def test_case_and_spaces_do_not_matter(self):
        selected = {"иванов иван"}
        self.assertTrue(_employee_selected("  Иванов Иван  ", selected))
        self.assertTrue(_employee_selected("ИВАНОВ ИВАН", selected))

    def test_other_people_are_dropped(self):
        self.assertFalse(_employee_selected("Петров Пётр", {"иванов иван"}))

    def test_empty_name_is_not_selected(self):
        self.assertFalse(_employee_selected(None, {"иванов иван"}))
        self.assertFalse(_employee_selected("", {"иванов иван"}))


class ReportPlumbingTests(unittest.TestCase):
    def test_filter_applied_before_the_row_is_built(self):
        # Отбрасывать надо до сборки строки, иначе лишние люди попадут в сводку
        # и в показатели карточки отчёта, даже не появившись в листе дня.
        self.assertEqual(REPORTS_SRC.count("_employee_selected(emp_name, selected_employees)"), 2)

    def test_selection_is_visible_in_the_summary(self):
        self.assertIn("Сотрудников выбрано", REPORTS_SRC)

    def test_api_passes_the_list_through(self):
        self.assertIn("employees=employees or None", BOT_SRC)
        self.assertIn("GROUP_LATE_REPORT_MAX_EMPLOYEES", BOT_SRC)

    def test_column_is_added_idempotently(self):
        # init_database гоняется на каждом старте — миграция обязана быть повторяемой.
        self.assertIn("ALTER TABLE glb_reports ADD COLUMN IF NOT EXISTS employee_filter TEXT;", DB_SRC)


class ReportRowMappingTests(unittest.TestCase):
    """Индексы кортежа обязаны совпадать с порядком колонок в SELECT."""

    def _block(self):
        start = DB_SRC.index("def get_group_late_reports")
        end = DB_SRC.index("def ", DB_SRC.index("items = [{", start))
        return DB_SRC[start:end]

    def _select_columns(self):
        block = self._block()
        # «FROM glb_reports r» встречается и в запросе COUNT выше — ищем от начала
        # нужного SELECT, иначе срез уезжает и колонок не остаётся вовсе.
        start = block.index("SELECT r.id")
        select = block[start:block.index("FROM glb_reports r", start)]
        select = select[len("SELECT"):]
        columns = []
        for part in select.replace("\n", " ").split(","):
            part = part.strip()
            if not part:
                continue
            alias = re.sub(r".*\bAS\s+", "", part, flags=re.IGNORECASE)
            columns.append(alias.split(".")[-1].strip())
        return columns

    def _mapping(self):
        block = self._block()
        body = block[block.index("items = [{"):]
        return {int(m.group(2)): m.group(1)
                for m in re.finditer(r"'([a-z_]+)': [^\n]*?r\[(\d+)\]", body)}

    def test_employee_filter_sits_where_it_is_read(self):
        columns = self._select_columns()
        self.assertEqual(columns[6], "department_filter")
        self.assertEqual(columns[7], "employee_filter")
        self.assertEqual(columns[8], "status")

    # Поля, которые наружу называются иначе, чем колонка в запросе.
    RENAMED = {"chat_title": "title"}

    def test_every_index_points_at_its_own_column(self):
        columns = self._select_columns()
        mapping = self._mapping()
        self.assertGreater(len(mapping), 10, "разбор не нашёл соответствий — проверь тест")
        for index, key in mapping.items():
            self.assertLess(index, len(columns), f"r[{index}] выходит за список колонок")
            self.assertEqual(columns[index], self.RENAMED.get(key, key),
                             f"r[{index}] читается как '{key}', а в SELECT там '{columns[index]}'")

    def test_filter_is_returned_as_a_list(self):
        # В базе список лежит строками; наружу отдаём массив, иначе фронт получит
        # одну длинную строку и покажет её целиком в ячейке.
        self.assertIn("'employee_filter': [x for x in (r[7] or '').splitlines() if x.strip()]", DB_SRC)


class FrontendTests(unittest.TestCase):
    def test_picker_is_hidden_behind_a_button(self):
        # Требование владельца: на виду обязательное, остальное — кнопками.
        self.assertIn("Выбрать сотрудников", VIEW_SRC)
        self.assertIn("reportModal.pickEmployees", VIEW_SRC)

    def test_selection_is_sent(self):
        self.assertIn("employees: reportModal.employees || []", VIEW_SRC)

    def test_card_says_the_report_is_partial(self):
        self.assertIn("report.employee_filter", VIEW_SRC)
        self.assertIn("только {fmtInt(report.employee_filter.length)} чел.", VIEW_SRC)

    def test_empty_selection_is_explained(self):
        self.assertIn("Никого не отметили — отчёт соберётся по всему отделу", VIEW_SRC)


if __name__ == "__main__":
    unittest.main()
