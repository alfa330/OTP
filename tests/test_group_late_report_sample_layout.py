# -*- coding: utf-8 -*-
"""Excel-отчёт «Отметок» по образцу владельца (11.11.2026).

Лист дня: «№» слева, ФИО, график, все отметки за день (от последней к первой),
отработано без обеда и отклонение; строки — по алфавиту ФИО через все отделы.
Опоздания и неявки из листа ушли, но счётчики карточки отчёта и сводный лист
периода на них стоят — они обязаны считаться, как раньше.

Отчёт строится настоящим `generate_report`, подменены только Workpace и
Clockster: проверяется то, что получит кадровик, а не исходник.
"""

import io
import unittest
from unittest import mock

from openpyxl import load_workbook

from group_late import reports

DAY = "2026-11-09"
NEXT_DAY = "2026-11-10"
SAMPLE_HEADERS = ["№", "ФИО сотрудника", "График", "Все отметки за день",
                  "Отработано (без обеда)", "Отклонение (HH:MM)"]

# Вымышленные люди двух отделов: по алфавиту они перемежаются, и разбивка по
# отделам дала бы другой порядок.
PEOPLE = [
    # Яковлев уходит в 18:30 — ранний уход на 30 минут.
    ("wp-1", "Яковлев Ян", "Регионы", "09:58", "18:30"),
    ("wp-2", "Абаев Арман", "Центральный офис", "09:45", "19:15"),
    ("wp-3", "Иванов Иван", "Регионы", "10:10", "19:11"),
    ("wp-4", "Ёлкин Ерлан", "Центральный офис", "10:00", "19:00"),
]


def _span(emp_id, name, dept, day=DAY):
    return {"employeeId": emp_id, "employeeName": name, "departmentName": dept,
            "date": day, "scheduleName": "Офис",
            "workTimeStart": f"{day}T10:00:00+05:00", "workTimeEnd": f"{day}T19:00:00+05:00",
            "inMark": None, "outMark": None, "lateIn": 0, "earlyOut": 0}


def _mark(emp_id, name, dept, hhmm, kind, day=DAY):
    return {"employeeId": emp_id, "employeeName": name, "departmentName": dept,
            "markDate": f"{day}T{hhmm}:00+05:00", "markType": kind, "status": 1}


def _day_records(day):
    spans, marks = [], []
    for emp_id, name, dept, came, left in PEOPLE:
        spans.append(_span(emp_id, name, dept, day))
        # Отметки — от первой к последней: порядок листа задаёт отчёт, а не
        # источник (Clockster отдаёт их именно так).
        marks.append(_mark(emp_id, name, dept, came, 0, day))
        marks.append(_mark(emp_id, name, dept, left, 1, day))
    # Человек по графику без единой отметки — неявка для карточки отчёта.
    spans.append(_span("wp-5", "Петров Пётр", "Регионы", day))
    return spans, marks


class _Workpace:
    def get_employees(self):
        return []

    def get_timetable_spans(self, start, end):
        return _day_records(start.strftime("%Y-%m-%d"))[0]

    def get_marks(self, start, end):
        return _day_records(start.strftime("%Y-%m-%d"))[1]


def _build(start, end=None):
    stats = {}
    with mock.patch.object(reports, "workpace_client", _Workpace()), \
            mock.patch.object(reports.config, "is_clockster_configured", return_value=False):
        data, name, text = reports.generate_report(start, end, stats_out=stats, db=None)
    return load_workbook(io.BytesIO(data)), name, text, stats


def _rows(ws):
    return [[cell.value for cell in row] for row in ws.iter_rows(min_row=4)]


class DaySheetTests(unittest.TestCase):
    def setUp(self):
        self.wb, self.name, self.text, self.stats = _build(DAY)
        self.ws = self.wb[DAY]

    def test_header_matches_the_owner_sample(self):
        self.assertEqual(self.ws["A1"].value, f"Отчет по посещаемости за {DAY}")
        self.assertEqual([cell.value for cell in self.ws[3]], SAMPLE_HEADERS)
        self.assertEqual(self.ws.max_column, len(SAMPLE_HEADERS))

    def test_rows_go_by_name_across_departments_and_are_numbered(self):
        rows = _rows(self.ws)
        self.assertEqual([row[1] for row in rows], [
            "Абаев Арман", "Ёлкин Ерлан", "Иванов Иван", "Петров Пётр", "Яковлев Ян"])
        self.assertEqual([row[0] for row in rows], [1, 2, 3, 4, 5])

    def test_row_reads_like_the_sample(self):
        first = _rows(self.ws)[0]
        self.assertEqual(first, [1, "Абаев Арман", "10:00–19:00",
                                 "19:15(Выход), 09:45(Вход)", "08:30", "+00:30"])

    def test_person_without_marks_keeps_a_numbered_row(self):
        row = next(row for row in _rows(self.ws) if row[1] == "Петров Пётр")
        self.assertEqual(row[3:], ["нет отметок", "—", "—"])

    def test_number_column_is_narrow_and_centered(self):
        self.assertLessEqual(self.ws.column_dimensions["A"].width, 6)
        self.assertEqual(self.ws["A4"].alignment.horizontal, "center")
        self.assertEqual(self.ws["B4"].alignment.horizontal, "left")

    def test_rows_alternate_like_the_sample(self):
        # Первая строка данных серая, следующая белая — по всей ширине листа.
        for column in "ABCDEF":
            self.assertTrue(self.ws[f"{column}4"].fill.fgColor.rgb.endswith("F2F5F8"), column)
            self.assertTrue(self.ws[f"{column}5"].fill.fgColor.rgb.endswith("FFFFFF"), column)

    def test_report_card_counters_still_count(self):
        # Иванов пришёл в 10:10 — опоздание; Петров без отметок — неявка.
        self.assertEqual(self.stats["rows_count"], 5)
        self.assertEqual(self.stats["late_count"], 1)
        self.assertEqual(self.stats["absent_count"], 1)

    def test_caption_does_not_promise_a_department_split(self):
        self.assertNotIn("разбивкой по отделам", self.text)
        self.assertIn("Опоздали: <b>1</b>", self.text)
        self.assertIn("Ранний уход: <b>1</b>", self.text)


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.wb, _name, _text, _stats = _build(DAY, NEXT_DAY)

    def test_day_sheets_follow_the_sample(self):
        self.assertEqual(self.wb.sheetnames, ["Сводная по сотрудникам", DAY, NEXT_DAY])
        for day in (DAY, NEXT_DAY):
            ws = self.wb[day]
            self.assertEqual([cell.value for cell in ws[3]], SAMPLE_HEADERS)
            self.assertEqual([row[0] for row in _rows(ws)], [1, 2, 3, 4, 5])

    def test_summary_is_numbered_like_the_day_sheets(self):
        ws = self.wb["Сводная по сотрудникам"]
        self.assertEqual(ws["A3"].value, "№")
        rows = _rows(ws)
        self.assertEqual([row[0] for row in rows], [1, 2, 3, 4, 5])
        # Порядок тот же, что в листах дня, — у человека один номер во всей
        # книге; отдел остаётся колонкой.
        self.assertEqual([(row[1], row[2]) for row in rows], [
            ("Центральный офис", "Абаев Арман"), ("Центральный офис", "Ёлкин Ерлан"),
            ("Регионы", "Иванов Иван"), ("Регионы", "Петров Пётр"), ("Регионы", "Яковлев Ян")])
        self.assertEqual([row[2] for row in rows], [row[1] for row in _rows(self.wb[DAY])])

    def test_summary_counts_survive_the_number_column(self):
        # Опоздания, ранние уходы и неявки видны теперь только здесь: в листе дня
        # их колонок нет. Колонки: №, Отдел, ФИО, дней по графику, отработано,
        # вовремя, опозданий, мин, ранних уходов, мин, неявок, всего, среднее.
        rows = {row[2]: row for row in _rows(self.wb["Сводная по сотрудникам"])}
        self.assertEqual(rows["Абаев Арман"][3:], [2, 2, 2, 0, "—", 0, "—", 0, "17:00", "08:30"])
        self.assertEqual(rows["Иванов Иван"][3:], [2, 2, 0, 2, 20, 0, "—", 0, "16:02", "08:01"])
        self.assertEqual(rows["Яковлев Ян"][3:], [2, 2, 0, 0, "—", 2, 60, 0, "15:04", "07:32"])
        self.assertEqual(rows["Петров Пётр"][3:], [2, 0, 0, 0, "—", 0, "—", 2, "00:00", "—"])


class NameOrderTests(unittest.TestCase):
    def test_kazakh_letters_yo_and_latin_sort_like_the_browser(self):
        names = ["Smith John", "Қасымов Нұрлан", "Ізбасарова Іңкәр", "Яковлев Ян",
                 "Касымов Ержан", "Ёлкин Ерлан", "Иванов Иван", "Әлиев Әли", "Абаев Арман",
                 "Ульянов Ұлан", "Үмбетов Үсен", "Уваров Ура"]
        self.assertEqual(sorted(names, key=reports._name_sort_key), [
            "Абаев Арман", "Әлиев Әли", "Ёлкин Ерлан", "Иванов Иван", "Ізбасарова Іңкәр",
            "Касымов Ержан", "Қасымов Нұрлан", "Уваров Ура", "Ульянов Ұлан", "Үмбетов Үсен",
            "Яковлев Ян", "Smith John"])

    def test_space_comes_before_letters(self):
        # Как в браузере: «Иванов Иван» раньше «Иванова Анна».
        self.assertEqual(sorted(["Иванова Анна", "Иванов Иван"], key=reports._name_sort_key),
                         ["Иванов Иван", "Иванова Анна"])

    def test_case_and_extra_spaces_do_not_move_a_name(self):
        self.assertEqual(reports._name_sort_key("  иванов   Иван "),
                         reports._name_sort_key("Иванов Иван"))


class MarksOrderTests(unittest.TestCase):
    def test_latest_first_whatever_the_source_order(self):
        early = {"markDate": f"{DAY}T09:00:00+05:00"}
        late = {"markDate": f"{DAY}T04:30:00Z"}  # 09:30 по Алматы
        broken = {"markDate": "не дата"}
        self.assertEqual(reports._marks_latest_first([early, broken, late]), [late, early, broken])
        self.assertEqual(reports._marks_latest_first([late, early]), [late, early])


if __name__ == "__main__":
    unittest.main()
