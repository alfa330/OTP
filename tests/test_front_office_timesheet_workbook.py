# -*- coding: utf-8 -*-
"""Табель фронт-офисов: файл Excel по образцу из ТЗ (front_office_timesheet/workbook.py).

Образец — лист «табель»: Times New Roman, шапка в две строки, город одной
объединённой ячейкой, «р» красным, «в» на бирюзовом, «Б/С» на жёлтом, «О» на
зелёном, итоги на зелёном под фиолетовой шапкой. Люди выдуманы.
"""

import io
import re
import unittest
import zipfile
from datetime import date, time

import openpyxl

from front_office_timesheet import rules, workbook


def build_sample():
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    days = rules.month_days(start, end)

    def cells(codes):
        out = []
        for day in days:
            code = codes.get(day.day, '')
            out.append({'code': code, 'late': 7 if day.day == 2 else 0,
                        'early': 30 if day.day == 5 else 0, 'incomplete': day.day == 6})
        return out

    almaty = [
        {'user_id': 1, 'name': 'Альфа Анна', 'city': 'Алматы', 'job_title': 'менеджер', 'schedule': '5.2',
         'plan': 21, 'cells': cells({1: 'р', 2: 'р', 3: 'в', 5: 'р', 6: 'р', 7: 'Б/С', 8: 'н', 9: 'О', 12: 'Б'})},
        {'user_id': 2, 'name': 'Бета Болат', 'city': 'Алматы', 'job_title': 'техперсонал', 'schedule': '',
         'plan': 13, 'cells': cells({1: 'р'})},
    ]
    astana = [
        {'user_id': 3, 'name': 'Гамма Гуля', 'city': 'Астана', 'job_title': 'менеджер', 'schedule': '5.2',
         'plan': 21, 'cells': cells({2: 'р'})},
    ]
    for person in almaty + astana:
        person['totals'] = rules.person_totals(person['cells'])
    model = {'month': '2026-10', 'days': days, 'holidays': {date(2026, 10, 26)}, 'fact_until': end,
             'cities': [{'city': 'Алматы', 'people': almaty}, {'city': 'Астана', 'people': astana}]}
    return workbook.build(model)


class WorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = build_sample()
        cls.book = openpyxl.load_workbook(io.BytesIO(cls.raw))
        cls.sheet = cls.book.active

    def test_one_sheet_named_like_the_sample(self):
        self.assertEqual(self.book.sheetnames, ['табель'])

    def test_header_matches_the_sample_and_the_tz_columns(self):
        sheet = self.sheet
        left = [sheet.cell(1, col).value for col in range(1, 6)]
        self.assertEqual(left, ['Город', 'Должность', 'Ф.И.О должностного лица', 'график', 'кол-во рабочих дней план'])
        self.assertEqual([sheet.cell(1, col).value for col in range(6, 9)], ['чт', 'пт', 'сб'])
        self.assertEqual([sheet.cell(2, col).value for col in range(6, 9)], [1, 2, 3])
        totals = [sheet.cell(1, col).value for col in range(37, 41)]
        self.assertEqual(totals, ['кол-во отработанных дней', 'кол-во пропущенных дней',
                                  'итого минут опозданий', 'итого минут раннего ухода'])
        # Правее итогов ничего нет: колонки с номерами карт из образца в ТЗ нет.
        self.assertEqual(sheet.max_column, 40)

    def test_fonts_and_header_fills_are_from_the_sample(self):
        sheet = self.sheet
        self.assertEqual(sheet.cell(1, 1).font.name, 'Times New Roman')
        self.assertEqual(sheet.cell(1, 1).fill.fgColor.rgb, 'FFC6E0B4')
        self.assertEqual(sheet.cell(1, 37).fill.fgColor.rgb, 'FFB3A2C7')
        self.assertEqual(sheet.cell(1, 8).fill.fgColor.rgb, 'FFFFF2CC')     # 3 октября — суббота
        self.assertEqual(sheet.cell(1, 31).fill.fgColor.rgb, 'FFFFF2CC')    # 26 октября — праздник
        self.assertNotEqual(sheet.cell(1, 6).fill.fgColor.rgb, 'FFFFF2CC')  # 1 октября — четверг

    def test_city_is_one_merged_cell_and_each_person_has_three_rows(self):
        merged = {str(rng) for rng in self.sheet.merged_cells.ranges}
        self.assertIn('A3:A8', merged)          # Алматы: два человека × три строки
        self.assertIn('A9:A11', merged)         # Астана
        for col in 'BCDE':
            self.assertIn(f'{col}3:{col}5', merged)
            self.assertIn(f'{col}6:{col}8', merged)
        self.assertEqual(self.sheet['C3'].value, 'Альфа Анна')
        self.assertEqual(self.sheet['D3'].value, '5.2')
        self.assertIsInstance(self.sheet['D3'].value, str)    # строкой: Excel не превратит в число
        self.assertEqual(self.sheet['E3'].value, 21)

    def test_day_codes_and_their_colours(self):
        sheet = self.sheet
        row = 3
        self.assertEqual(sheet.cell(row, 6).value, 'р')
        self.assertEqual(sheet.cell(row, 6).font.color.rgb, 'FFFF0000')
        self.assertEqual(sheet.cell(row, 8).fill.fgColor.rgb, 'FFB7E1CD')     # «в»
        self.assertEqual(sheet.cell(row, 12).value, 'Б/С')
        self.assertEqual(sheet.cell(row, 12).fill.fgColor.rgb, 'FFFFFF00')
        self.assertEqual(sheet.cell(row, 14).value, 'О')
        self.assertEqual(sheet.cell(row, 14).fill.fgColor.rgb, 'FF92D050')
        self.assertEqual(sheet.cell(row, 13).value, 'н')
        self.assertEqual(sheet.cell(row, 11).fill.fgColor.rgb, 'FFFFEB9C')    # 6-е: неполная отметка
        self.assertEqual(sheet.cell(row + 1, 7).value, 7)                      # опоздание 2-го
        self.assertEqual(sheet.cell(row + 2, 10).value, 30)                    # ранний уход 5-го

    def test_totals_are_formulas_with_values_for_previews(self):
        sheet = self.sheet
        self.assertEqual(sheet['AK3'].value, '=COUNTIF(F3:AJ3,"р")')
        self.assertEqual(sheet['AL3'].value, '=COUNTIF(F3:AJ3,"Б/С")+COUNTIF(F3:AJ3,"н")')
        self.assertEqual(sheet['AM3'].value, '=SUM(F4:AJ4)')
        self.assertEqual(sheet['AN3'].value, '=SUM(F5:AJ5)')
        xml = zipfile.ZipFile(io.BytesIO(self.raw)).read('xl/worksheets/sheet1.xml').decode('utf-8')
        cached = dict(re.findall(r'<c r="(A[K-N]3)"[^>]*><f>[^<]*</f><v>([^<]*)</v>', xml))
        # р — 1, 2, 5, 6 октября; пропущено — Б/С и н; опоздание 7; ранний уход 30.
        self.assertEqual(cached, {'AK3': '4', 'AL3': '2', 'AM3': '7', 'AN3': '30'})

    def test_header_is_frozen_and_legend_is_below(self):
        self.assertEqual(self.sheet.freeze_panes, 'F3')
        legend = self.sheet.cell(self.sheet.max_row, 1).value
        self.assertTrue(legend.startswith('Обозначения:'))
        for code in ('р', 'в', 'Б/С', 'О', 'Б', 'н'):
            self.assertIn(f'{code} —', legend)


if __name__ == '__main__':
    unittest.main()
