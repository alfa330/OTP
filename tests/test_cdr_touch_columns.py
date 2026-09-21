# -*- coding: utf-8 -*-
"""Колонки «Касаний»: приветствие и ожидание в очереди вместо «Вызов всего».

Прежняя колонка складывала в одно число приветствие, ожидание и разговор, и по ней
нельзя было сказать, где человек провёл это время. Разбор боевого звонка 21.09.2026
19:28:46: приход 19:28:46, приветствие 26 с, вход в очередь 19:29:12, ответ через 1 с,
разговор 9 с — а в колонках «Разговор» и «Вызов» стояло 9 и 9.

Что закреплено:
  * приветствие = вход в очередь − приход звонка (приход берётся из linkedid);
  * ожидание — то, что сказала станция; нет его — разность ответа и входа в очередь;
  * НЕИЗВЕСТНО и НОЛЬ различаются: ноль секунд ожидания — настоящее значение;
  * список значений строки выгрузки совпадает по длине со списком колонок.
"""

import unittest
from datetime import datetime

from openpyxl import Workbook

from cdr import queries, report

# Звонок из разбора: целая часть linkedid — секунда прихода (19:28:46 по Алматы).
LINKEDID = '1790000926.1131743'
QUEUED_AT = datetime(2026, 9, 21, 19, 29, 12)
ANSWERED_AT = datetime(2026, 9, 21, 19, 29, 13)


def row(queued_at=QUEUED_AT, answered_at=ANSWERED_AT, wait=1, talk_measured=9,
        hangup='client', linkedid=LINKEDID):
    """Строка ровно в порядке queries._COLUMNS."""
    return (datetime(2026, 9, 21, 19, 29, 12), answered_at, '7773714269', '6656',
            'Входящий', 'Разговор', 9, 9, '3041', 'http://rec/q-3041.wav',
            linkedid, 1, queued_at, wait, talk_measured, hangup)


class RowToTouchTests(unittest.TestCase):
    def test_columns_and_values_line_up(self):
        touch = queries._row_to_touch(row())
        self.assertEqual(len(queries._COLUMNS.split(',')), len(row()))
        self.assertEqual(touch['linkedid'], LINKEDID)

    def test_ivr_is_the_greeting_before_the_queue(self):
        self.assertEqual(queries._row_to_touch(row())['ivr_seconds'], 26)

    def test_wait_comes_from_the_station(self):
        self.assertEqual(queries._row_to_touch(row(wait=7))['wait_seconds'], 7)

    def test_zero_wait_is_a_value_not_a_gap(self):
        self.assertEqual(queries._row_to_touch(row(wait=0))['wait_seconds'], 0)

    def test_without_the_station_wait_is_counted_from_the_queue_entry(self):
        touch = queries._row_to_touch(row(wait=None))
        self.assertEqual(touch['wait_seconds'], 1, 'ответ 19:29:13 минус вход 19:29:12')

    def test_no_queue_entry_means_unknown_not_zero(self):
        touch = queries._row_to_touch(row(queued_at=None, wait=None))
        self.assertIsNone(touch['wait_seconds'])
        self.assertIsNone(touch['ivr_seconds'])

    def test_broken_linkedid_does_not_invent_a_greeting(self):
        self.assertIsNone(queries._row_to_touch(row(linkedid='мусор'))['ivr_seconds'])

    def test_hangup_side_travels_to_the_section(self):
        self.assertEqual(queries._row_to_touch(row())['hangup_side'], 'client')


class ExportColumnsTests(unittest.TestCase):
    def titles(self):
        return [title for _key, title, _width in report.COLUMNS]

    def test_dial_column_is_gone(self):
        self.assertNotIn('Вызов всего, с', self.titles())
        self.assertNotIn('dial_seconds', [key for key, _t, _w in report.COLUMNS])

    def test_greeting_and_wait_are_there(self):
        self.assertIn('Приветствие/IVR, с', self.titles())
        self.assertIn('Ожидание в очереди, с', self.titles())

    def test_row_has_a_value_for_every_column(self):
        touch = queries._row_to_touch(row())
        touch['operator'] = 'Зинеден Аружан'
        values = report._touch_row(_sheet(), touch)
        self.assertEqual(len(values), len(report.COLUMNS))
        by_title = dict(zip(self.titles(), values))
        self.assertEqual(by_title['Приветствие/IVR, с'], 26)
        self.assertEqual(by_title['Ожидание в очереди, с'], 1)
        self.assertEqual(by_title['Разговор, с'], 9)

    def test_unknown_stays_empty_instead_of_zero(self):
        """Ноль в клетке испортил бы среднее у того, кто считает по файлу."""
        touch = queries._row_to_touch(row(queued_at=None, wait=None))
        values = dict(zip(self.titles(), report._touch_row(_sheet(), touch)))
        self.assertIsNone(values['Приветствие/IVR, с'])
        self.assertIsNone(values['Ожидание в очереди, с'])


def _sheet():
    """Настоящий лист write-only книги: `_touch_row` собирает ячейки через
    WriteOnlyCell, а тому нужен живой родитель ради формата даты."""
    return Workbook(write_only=True).create_sheet('касания')


if __name__ == '__main__':
    unittest.main()
