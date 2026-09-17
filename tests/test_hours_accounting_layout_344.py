# -*- coding: utf-8 -*-
"""Учёт часов, задача #344 от Ядигарова Руслана.

«Убрать внешний контейнер с раздела, чтобы не было ненужного горизонтального
скролла + во всех разделах, где можно вывести итог по дню, выводить итог по дню».

Лишний скролл вбок на проде давала не только белая карточка вокруг раздела:
закрытое меню группы показателей «Деньги» — прозрачная, но расставленная
панель шириной 220 px — стояло у правого края и на экране 1920 px выходило за
окно на 35 px. Поэтому закрытое меню теперь не занимает места, а открытое у
правого края раскрывается влево.

Формулы итога дня проверяет tests/hours_day_totals.test.mjs на настоящем коде
расчёта; здесь — разметка и стили, которые формулой не проверить.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8")
STYLES = (ROOT / "src" / "styles.css").read_text(encoding="utf-8")


def _desktop_markup():
    start = APP.index("/* Раздел лежит прямо на полотне страницы")
    end = APP.index("{/* Upload modal (file selector -> preview) */}", start)
    return APP[start:end]


def _footer():
    start = APP.index("{/* FOOTER: итоговые строки */}")
    return APP[start:APP.index("{selectedTab === 'work_time' && (", start)]


def _css_rule(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", STYLES)
    if not match:
        raise AssertionError(f"нет правила {selector}")
    return match.group(1)


class OuterContainerTests(unittest.TestCase):
    def test_section_has_no_outer_card(self):
        markup = _desktop_markup()
        root = markup[markup.index("return ("):][:200]
        self.assertIn('<div className="min-w-0">', root)
        self.assertNotIn("bg-white p-5 rounded-xl shadow-md", markup)

    def test_table_keeps_its_own_surface_and_scroll(self):
        """Без карточки строки таблицы легли бы на серое полотно — подложка у самой таблицы."""
        self.assertIn(
            '<div className="overflow-auto rounded-2xl bg-white shadow-sm ring-1 ring-slate-200/70">',
            _desktop_markup(),
        )


class ClosedMenuTakesNoSpaceTests(unittest.TestCase):
    def test_closed_menu_is_out_of_layout(self):
        closed = _css_rule(".tab-dropdown")
        self.assertIn("display: none;", closed)
        self.assertIn("display 0.2s allow-discrete", closed)
        self.assertIn("display: block;", _css_rule(".tab-dropdown.open"))

    def test_menu_opens_to_the_side_with_room(self):
        self.assertIn("right: 0;", _css_rule(".tab-dropdown.tab-dropdown--end"))
        self.assertIn("const [openMenuAlignEnd, setOpenMenuAlignEnd] = useState(false);", APP)
        self.assertIn("setOpenMenuAlignEnd(!fitsToRight && fitsToLeft);", APP)
        # И меню направлений, и меню групп показателей.
        self.assertEqual(APP.count("open${openMenuAlignEnd ? ' tab-dropdown--end' : ''}"), 2)

    def test_side_is_measured_before_paint(self):
        """В обычном useEffect меню на кадр мелькало бы не с той стороны."""
        start = APP.index("const [openMenuAlignEnd, setOpenMenuAlignEnd]")
        self.assertIn("useLayoutEffect(() => {", APP[start:start + 300])

    def test_phone_branch_still_returns_after_all_hooks(self):
        self.assertLess(APP.index("const [openMenuAlignEnd"), APP.index("if (isHoursPhone) {"))
        self.assertLess(APP.index("const hoursDayTotals = useMemo("), APP.index("if (isHoursPhone) {"))


class DayTotalsInFooterTests(unittest.TestCase):
    def test_every_day_cell_of_the_footer_shows_its_total(self):
        footer = _footer()
        self.assertIn("const dayTotal = hoursDayTotals[String(day)];", footer)
        self.assertNotIn("className={hoursDayColClass}>&nbsp;</div>", footer)

    def test_totals_are_calm_and_tabular(self):
        """Итог — не новый цвет на экране: нейтральный текст, цифры не прыгают."""
        footer = _footer()
        cell = footer[footer.index("const dayTotal"):]
        self.assertIn("tabular-nums text-slate-800", cell)
        for colour in ("emerald", "rose", "red-", "amber", "bg-"):
            self.assertNotIn(colour, cell)

    def test_totals_follow_the_same_rows_as_the_month_total(self):
        start = APP.index("const hoursDayTotals = useMemo(")
        memo = APP[start:APP.index("// render cell with trainings marker", start)]
        self.assertIn("for (const op of filteredOperators) {", memo)
        self.assertIn("if (isForeignDay(op, day)) continue;", memo)
        self.assertIn("calculateWeightedChatAverage(chatDays)", memo)


if __name__ == "__main__":
    unittest.main()
