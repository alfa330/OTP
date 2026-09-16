# -*- coding: utf-8 -*-
"""«Успешки в час» в учёте часов ОП TEZ и у самого оператора (задача #329).

Постановка Алчинбаевой Анель: в таблице «Успешки» нужен показатель
«успешки / отработанные часы», и он же должен быть виден оператору.
Заодно снят визуальный шум: подписи «Успешки» и «Выполнение» повторялись
в каждой строке — теперь они стоят в шапке, а в строках только числа.

Знаменатель — те же отработанные часы, из которых считается индивидуальный
план (`displayedTotal` в таблице, `regular` в «Моих часах»). Разъедутся они —
две соседние колонки начнут мерить работу разными часами.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
MOBILE_SRC = (ROOT / "src" / "components" / "hours" / "MyHoursMobile.jsx").read_text(encoding="utf-8-sig")


def _hours_table_header():
    """Шапка сводных колонок таблицы учёта часов для вкладки «Успешки»."""
    start = APP_SRC.index("{/* Успешки: три отдельные колонки.")
    return APP_SRC[start:start + 1800]


def _hours_table_row():
    start = APP_SRC.index("{selectedTab === 'tez_successes' && (() => {")
    return APP_SRC[start:start + 3200]


def _hours_table_footer():
    marker = "{selectedTab === 'tez_successes' && (() => {"
    first = APP_SRC.index(marker)
    start = APP_SRC.index(marker, first + 1)
    return APP_SRC[start:start + 3200]


class SuccessesTabColumnsTests(unittest.TestCase):
    """Три колонки вместо одной сдвоенной ячейки."""

    def test_header_carries_all_three_labels(self):
        header = _hours_table_header()
        self.assertIn(">\n                            Успешки\n", header)
        self.assertIn(">\n                            Выполнение\n", header)
        self.assertIn(">\n                            Успешки в час\n", header)
        # Сдвоенной ячейки на 288 px больше нет — её место заняли три колонки.
        self.assertNotIn("w-72", header)

    def test_double_cell_is_gone_from_the_whole_tab(self):
        """Ни в строке, ни в итоге не должно остаться сетки «две ячейки в одной»."""
        for block in (_hours_table_row(), _hours_table_footer()):
            self.assertNotIn("grid-cols-2 divide-x divide-gray-200", block)
            self.assertNotIn("w-72", block)

    def test_footer_labels_are_not_repeated(self):
        footer = _hours_table_footer()
        rendered = footer[footer.index("return ("):]
        self.assertNotIn("Успешки<", rendered)
        self.assertNotIn("Выполнение<", rendered)


class SuccessesPerHourTests(unittest.TestCase):
    """Сам показатель: успешки ÷ отработанные часы."""

    def test_row_divides_by_the_same_hours_as_the_plan(self):
        row = _hours_table_row()
        self.assertIn("factHours: displayedTotal,", row)
        self.assertIn("const perHour = displayedTotal > 0 ? total / displayedTotal : null;", row)
        self.assertIn("formatNumber(perHour, 2)", row)
        # Нет часов — прочерк, а не ноль: делить не на что.
        self.assertIn("{perHour == null ? '—' : formatNumber(perHour, 2)}", row)

    def test_footer_totals_use_sums_not_average_of_rows(self):
        """Итог отдела — сумма успешек на сумму часов, иначе вес строк одинаков."""
        footer = _hours_table_footer()
        self.assertIn("const hours = footerTotals.sumDisplayedTotal;", footer)
        self.assertIn("const perHour = hours > 0 ? total / hours : null;", footer)
        self.assertIn("{perHour == null ? '—' : formatNumber(perHour, 2)}", footer)

    def test_numbers_stay_tabular(self):
        """Требование владельца: числа не должны прыгать при обновлении."""
        for block in (_hours_table_row(), _hours_table_footer()):
            rendered = block[block.index("return ("):]
            self.assertEqual(rendered.count("tabular-nums"), 3)


class OperatorSeesSuccessesPerHourTests(unittest.TestCase):
    """«Данные показатели так же должны отображаться у операторов»."""

    def test_my_hours_computes_per_hour_from_worked_hours(self):
        self.assertIn(
            "const tezSuccessesPerHour = safeNum(regular) > 0",
            APP_SRC,
        )
        self.assertIn("? (tezSuccessesTotal / safeNum(regular))", APP_SRC)

    def test_my_hours_desktop_shows_the_row(self):
        start = APP_SRC.index("'Успешки и корректировки'")
        block = APP_SRC[start:start + 6000]
        self.assertIn("Успешки за месяц", block)
        self.assertIn("Успешки в час", block)
        self.assertIn("{tezSuccessesPerHour == null ? '—' : tezSuccessesPerHour.toFixed(2)}", block)

    def test_phone_summary_receives_and_shows_it(self):
        self.assertIn("perHour: tezSuccessesPerHour,", APP_SRC)
        self.assertIn('<ValueRow title="Успешки в час"', MOBILE_SRC)
        # На телефоне число в русской записи — как у «Звонки в час» рядом.
        self.assertIn("tez.perHour == null ? '—' : formatHoursNumber(tez.perHour)", MOBILE_SRC)
        self.assertIn("value={formatHoursNumber(intensity.perHour)}", MOBILE_SRC)

    def test_wording_matches_the_rest_of_the_app(self):
        """«Звонки в час» / «Чаты в час» — тот же падеж, что у нового показателя."""
        self.assertIn("isChatModel ? 'Чаты в час' : 'Звонки в час'", APP_SRC)
        self.assertNotIn("Успешек в час", APP_SRC)
        self.assertNotIn("Успешок в час", APP_SRC)


if __name__ == "__main__":
    unittest.main()
