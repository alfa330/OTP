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
    def test_section_is_one_card_not_two(self):
        """Две рамки (карточка раздела + рамка таблицы) были лишними, без обеих раздел
        терялся на сером полотне. Остаётся одна — карточка раздела, таблица в ней
        под волосяной линией до краёв."""
        markup = _desktop_markup()
        root = markup[markup.index("return ("):][:400]
        self.assertIn(
            '<div className="flex max-h-full min-h-0 min-w-0 flex-col rounded-2xl bg-white pt-4 '
            'shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70"',
            root,
        )
        self.assertNotIn("bg-white p-5 rounded-xl shadow-md", markup)
        table = markup[markup.index("{/* Table */}"):]
        table_box = table[table.index("<div className="):table.index("<div className=\"min-w-max")]
        self.assertIn("rounded-b-2xl border-t border-slate-200/70", table_box)
        self.assertNotIn("ring-1", table_box)

    def test_plan_strip_is_flat_inside_the_card(self):
        self.assertIn('<div className="mx-5 mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl bg-slate-50 px-4 py-3">', _desktop_markup())


class MacStyleLayoutTests(unittest.TestCase):
    """Второй заход по #344: «по внешке сделай более аккуратно, удобно, в стиле ios/macos»."""

    def test_toolbar_has_no_captions_over_button_groups(self):
        markup = _desktop_markup()
        for caption in (">Параметры<", ">Действия<", ">Операторы</span>"):
            self.assertNotIn(caption, markup)

    def test_month_is_picked_by_the_shared_month_picker(self):
        """Системное поле месяца рисует ОС — рядом с карточками оно деталь из другой программы."""
        markup = _desktop_markup()
        self.assertIn("<MonthPicker value={month} onChange={setMonth} allowFuture />", markup)
        self.assertNotIn('type="month"', markup[:markup.index("{/* Table */}")])

    def test_multiselect_hint_is_on_demand(self):
        markup = _desktop_markup()
        hint = markup.index("Мультивыбор ячеек:")
        self.assertIn("<IosHint", markup[hint - 200:hint])
        bar = markup.index("Выбрано ячеек:")
        self.assertIn("{selectedHourCells.length > 0 && (", markup[bar - 700:bar])

    def test_filters_are_segmented_controls_without_colour_fills(self):
        markup = _desktop_markup()
        self.assertIn('ariaLabel="Тип отчёта"', markup)
        self.assertIn("{ value: 'active', label: 'Активные', count: activeCount }", markup)
        self.assertNotIn("bg-green-600 text-white shadow", markup)
        self.assertNotIn("bg-red-600 text-white shadow", markup)

    def test_header_and_totals_stay_on_screen(self):
        markup = _desktop_markup()
        # Карточка не выше окна, таблица — сжимаемый прокручиваемый элемент.
        self.assertIn('<div className="flex max-h-full min-h-0 min-w-0 flex-col', markup)
        self.assertIn('hours-scroll hours-table-scroll isolate min-h-[240px] overflow-auto', markup)
        self.assertIn('className="sticky top-0 z-30 flex w-max', markup)
        self.assertIn('className="sticky bottom-0 z-30 flex w-max', markup)

    def test_day_header_is_number_and_weekday(self):
        markup = _desktop_markup()
        self.assertIn("HOURS_WEEKDAY_SHORT[new Date(monthParts.year, monthParts.monthNum - 1, day).getDay()]", markup)
        self.assertIn("const HOURS_WEEKDAY_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];", APP)

    def test_empty_days_are_quiet(self):
        """Прошедший день без часов был тёмно-серой плашкой — на каждом выходном."""
        start = APP.index("function renderCellByMetricWithStyleAndMarker(op, day, metricKey) {")
        renderer = APP[start:APP.index("function getTrainingsFor(opId, day) {", start)]
        self.assertNotIn("bg-gray-400 text-white", renderer)
        self.assertIn("bg-slate-100 text-slate-400", renderer)


class ScrollbarsAndHintTests(unittest.TestCase):
    """«Сделай кастомные скролы» и «i при наведении выходит некорректно»."""

    def test_hint_is_not_covered_by_the_sticky_day_header(self):
        """Шапка дней (z-30) и столбец имён (z-40) без isolate соревновались с
        подсказкой «i» (z-30) на уровне страницы и накрывали её низ на 1920 px."""
        self.assertIn("hours-table-scroll isolate", _desktop_markup())

    def test_custom_scrollbars_use_webkit_pseudo_elements_only(self):
        body = _css_rule(".hours-scroll::-webkit-scrollbar-thumb")
        self.assertIn("border-radius: 9999px;", body)
        self.assertIn("background-clip: padding-box;", body)
        # scrollbar-color в обычном правиле выключил бы ::-webkit-scrollbar в Chrome 121+.
        plain = re.search(r"\n    \.hours-scroll\s*\{", STYLES)
        self.assertIsNone(plain)
        self.assertIn("@supports not selector(::-webkit-scrollbar)", STYLES[STYLES.index(".hours-scroll::-webkit-scrollbar {"):])

    def test_scrollbars_cover_table_lists_and_page(self):
        markup = _desktop_markup()
        self.assertIn('<div className="hours-scroll max-h-64 overflow-y-auto" role="listbox">', APP)
        self.assertIn('<div className="hours-scroll flex max-h-[280px] min-w-[220px] flex-col overflow-y-auto">', markup)
        self.assertIn("overflow-y-auto${view === 'sv_hours' ? ' hours-scroll' : ''}", APP)


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
