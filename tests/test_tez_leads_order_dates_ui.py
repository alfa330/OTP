# -*- coding: utf-8 -*-
"""Контракт отображения двух оконных дат заказов TEZ."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "src" / "components" / "salary" / "TezLeadsPanel.jsx"
SOURCE = PANEL_PATH.read_text(encoding="utf-8-sig")


class TezLeadsOrderDatesUiTests(unittest.TestCase):
    @staticmethod
    def _detail_table_source():
        rows_at = SOURCE.index("{leads.map((row) => (")
        start = SOURCE.rfind("<table", 0, rows_at)
        end = SOURCE.index("</table>", rows_at)
        return SOURCE[start:end]

    @staticmethod
    def _trip_dates_source():
        start = SOURCE.index("const LeadTripDates =")
        end = SOURCE.index("\n};", start) + len("\n};")
        return SOURCE[start:end]

    def test_detail_shows_previous_reason_and_current_month_trip_together(self):
        trip_dates = self._trip_dates_source()

        self.assertIn("row?.prev_month_first_order_at", trip_dates)
        self.assertIn("row?.month_first_order_at", trip_dates)
        self.assertIn("Предыдущий месяц", trip_dates)
        self.assertIn("Отчётный месяц", trip_dates)
        self.assertIn("причина статуса", trip_dates)
        self.assertIn("row?.status_rule === 'active_prev_month'", trip_dates)

    def test_previous_trip_is_not_only_available_in_hover_title(self):
        table = self._detail_table_source()
        body_at = table.index("<tbody>")
        visible_body = table[body_at:]

        self.assertIn("<LeadTripDates row={row} />", visible_body)
        self.assertIn("fmtDateTime(previousTrip)", self._trip_dates_source())


if __name__ == "__main__":
    unittest.main()
