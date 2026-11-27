# -*- coding: utf-8 -*-
"""«Воронка ОП» Тез в «Аналитике» раздела «Воронка ОП Tez» (задача #407 Алчинбаевой Анель, 03.11.2026).

Владелец: «выполни в этом же разделе задачу от Анель Воронка ОП тез, эту воронку можно положить в
Аналитику». Счёт — tests/test_dial_list_tez_funnel_postgres.py (настоящий PostgreSQL), подписи и запрос —
tests/dial_list_tez_funnel.test.mjs; здесь — проводка экрана и ручки:
  * у «Аналитики» Тез два разреза, «Воронка ОП» — первый и открывается сразу;
  * дашборд автообзвона не грузится, пока открыт разрез воронки (лишний запрос);
  * панель воронки ходит в /funnel и сбрасывает файл и город при смене месяца базы;
  * регистрация от TEZ APP (с 04.11.2026) — в плитках и по городам, пробелы в данных — предупреждением;
  * воронка считается по базе успешек и выбирает месяц у себя (06.11.2026);
  * загрузка базы Тез принимает город, подсказка у загрузки говорит об этом;
  * ручка /funnel объявлена и описана в списке ручек раздела.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYTICS = (ROOT / "src" / "components" / "dial_list" / "TezAutodialAnalytics.jsx").read_text(encoding="utf-8-sig")
PANEL = (ROOT / "src" / "components" / "dial_list" / "TezFunnelPanel.jsx").read_text(encoding="utf-8-sig")
FUNNEL_LOGIC = (ROOT / "src" / "components" / "dial_list" / "tezFunnel.js").read_text(encoding="utf-8-sig")
SETTINGS = (ROOT / "src" / "components" / "sip" / "DialListSettings.jsx").read_text(encoding="utf-8-sig")
ROUTES = (ROOT / "dial_list" / "routes.py").read_text(encoding="utf-8-sig")


class AnalyticsViewsTests(unittest.TestCase):
    def test_funnel_is_the_first_and_default_view(self):
        views = ANALYTICS[ANALYTICS.index("const VIEWS = ["):]
        views = views[:views.index("];")]
        self.assertLess(views.index("'funnel'"), views.index("'dial'"))
        self.assertIn("{ value: 'funnel', label: 'Воронка ОП' }", views)
        # У Тез первая — воронка; у «Активации водителей» (26.11.2026) воронки ОП нет — сразу «Автообзвон».
        self.assertIn("const [view, setView] = useState(activationSection ? 'dial' : 'funnel');", ANALYTICS)
        self.assertIn("const dial = activationSection || view === 'dial';", ANALYTICS)

    def test_dial_dashboard_loads_only_on_its_view(self):
        self.assertIn("useEffect(() => { if (view === 'dial') load(); }, [load, view]);", ANALYTICS)
        self.assertNotIn("useEffect(() => { load(); }, [load]);", ANALYTICS)
        self.assertIsNotNone(re.search(r"\{!dial && \(\s*<TezFunnelPanel", ANALYTICS))
        self.assertIn("{dial && ready && (", ANALYTICS)

    def test_panel_asks_the_funnel_route_and_resets_month_bound_filters(self):
        self.assertIn("/api/dial_list/departments/${departmentId}/funnel${qs}", PANEL)
        # Файл и город помнят свой месяц: другой месяц — сразу «все», без запроса со старым выбором.
        self.assertIn("const scope = `${departmentId}|${month}`;", PANEL)
        self.assertIn("const batchId = picked.scope === scope ? picked.batchId : '';", PANEL)
        self.assertIn("const city = picked.scope === scope ? picked.city : '';", PANEL)
        self.assertIn("if (my === seq.current) setData(body);", PANEL)

    def test_registration_from_tez_app_is_shown_by_city_and_gaps_are_announced(self):
        # Поля TEZ APP с 04.11.2026: по городам — все пять шагов и обе конверсии.
        for cell in ("num(r.already_registered)", "num(r.registered_after)", "pctText(r.dialog_to_registration)"):
            self.assertIn(cell, PANEL)
        # По кому TEZ APP ещё не ответил — предупреждение над плитками, а не молчаливый ноль.
        self.assertIn("{tezNotice && <Notice>{tezNotice}</Notice>}", PANEL)
        self.assertNotIn("ждём данные TEZ APP", PANEL + FUNNEL_LOGIC)

    def test_funnel_picks_months_of_the_successes_base_itself(self):
        # Воронка — по базе успешек (06.11.2026): месяц выбирается в самой панели из месяцев этой базы.
        self.assertIn('ariaLabel="Месяц базы"', PANEL)
        self.assertIn("const months = useMemo(() => monthOptions(data), [data]);", PANEL)
        self.assertIn("funnelQuery({ period: month,", PANEL)
        # Выбор над разрезами — месяцы базы автообзвона: он только у «Автообзвона», в воронку не уходит.
        self.assertIsNotNone(re.search(r"\{dial && \(\s*<div className=\"flex items-center gap-2\">\s*<CustomSelect",
                                       ANALYTICS))
        panel_call = ANALYTICS[ANALYTICS.index("<TezFunnelPanel"):]
        panel_call = panel_call[:panel_call.index("/>")]
        self.assertNotIn("period=", panel_call)


REPORT_KIT = (ROOT / "src" / "components" / "wiki" / "reportKit.jsx").read_text(encoding="utf-8-sig")


class OutcomesAndLayoutTests(unittest.TestCase):
    """27.11.2026: Анель — «не хватает показателей по итогам диалога: согласен, подумает, отказ», «согласия и
    отказы как основные показатели отдельно», «структурируй отображение», «муравьиный шрифт не отправлять»,
    «хочу просто скриншотить и кидать руководству»; владелец — «всё, что в настройках итогов звонка добавлено,
    всё должно быть выведено»."""

    def test_every_outcome_of_the_settings_is_a_row_with_the_total(self):
        self.assertIn("const outcomes = useMemo(() => outcomeRows(data), [data]);", PANEL)
        self.assertIn('title="Итоги звонков"', PANEL)
        # Строки — все итоги ответа, без отбора по числу: «Подумает» с нулём тоже на экране.
        self.assertIn("{outcomes.map((r) => (", PANEL)
        self.assertNotIn("outcomes.filter(", PANEL)
        self.assertIn('<TotalRow label="Всего с итогом" count={outcomesWhole} bars={bars} />', PANEL)
        # Последний ИТОГ, а не последний звонок: недозвон итог не меняет — так и сказано под «i» (разбор 27.11).
        self.assertIn("Звонки без итога (недозвоны) и звонки Binotel мимо обзвона его не меняют.", PANEL)

    def test_agreements_and_refusals_are_main_tiles_next_to_conversions(self):
        self.assertIn("const results = useMemo(() => [...outcomeTiles(data), ...funnelConversions(data)], [data]);",
                      PANEL)
        self.assertIn('<div className="grid grid-cols-2 gap-2 lg:grid-cols-4 xl:gap-2.5">', PANEL)

    def test_refusal_reasons_fit_one_screenshot_and_add_up_to_the_refusals(self):
        # Все причины сразу — без листания (на снимке страница 2 из 2 не видна), с «Всего отказов»; на широком
        # экране — двумя колонками, по высоте вровень с итогами (владелец 27.11: «сделать всё симметрично»).
        reasons_block = PANEL[PANEL.index('title="Причины отказа"'):]
        reasons_block = reasons_block[:reasons_block.index("</ReportBlock>")]
        self.assertNotIn("PagedTable", reasons_block)
        self.assertIn("{column.map((r) => <ReasonRow key={r.subtype_id || 'none'} reason={r} whole={refusalsTotal} bars={bars} />)}",
                      reasons_block)
        self.assertIn('<TotalRow label="Всего отказов" count={refusalsTotal} bars={bars} />', reasons_block)
        self.assertIn("Сумма причин равна «Отказались» и строке «Отказ» в итогах звонков", reasons_block)
        self.assertIn("const reasonColumns = layout !== 'single' && reasons.length > 1 ? splitInTwo(reasons) : [reasons];",
                      PANEL)

    def test_layout_follows_the_width_of_the_block_and_rows_are_level(self):
        # «Адаптив под любой экран»: раскладка — от ширины самого блока (сайдбар отнимает 220 px), не окна.
        # Ширина — корня раздела: он есть с первого кадра, раскладка не перескакивает с приходом данных.
        self.assertIn("const [panelRef, panelWidth] = useBlockWidth();", PANEL)
        self.assertIn("const layout = reportLayout(panelWidth);", PANEL)
        self.assertIn('<div ref={panelRef} className="space-y-4">', PANEL)
        self.assertIn("const inline = filtersInline(panelWidth, bigScreen);", PANEL)
        self.assertIn("new ResizeObserver(measure)", PANEL)
        self.assertIn("<div className={layout === 'three' ? 'grid grid-cols-3 gap-4' : 'grid grid-cols-1 gap-4'}>", PANEL)
        # Карточки ряда — во всю высоту ячейки, «Всего» прижато к низу: строки «Всего» на одном уровне.
        self.assertIn("<div className={`${iosCard} flex flex-1 flex-col overflow-hidden`}>{children}</div>", PANEL)
        self.assertEqual(PANEL.count('<div className="mt-auto border-t border-slate-100 bg-slate-50/70">'), 1)
        self.assertIn("mt-auto grid border-t border-slate-100 bg-slate-50/70", PANEL)
        # Шаги воронки — ровными рядами на любой ширине.
        self.assertIn("className={`${stepTileClass(i, steps.length)} [&>*]:h-full`}", PANEL)
        # Предупреждения — над обоими отчётами: над одним они сдвинули бы карточки ряда.
        self.assertIn("{(agreementNotice || setupNotice) && (", PANEL)
        # Строки отчётов — flex, не grid-cols-[…]: оболочка телефона схлопывает такие сетки в одну колонку
        # (имя, число и доля вставали друг под другом — стенд 27.11); имя — grow basis-0, не flex-1 (его ловит :has).
        self.assertNotIn("grid-cols-[minmax", PANEL)
        self.assertIn("const NAME = 'min-w-0 grow basis-0 break-words';", PANEL)
        self.assertIn("const bars = reportShowsBars(panelWidth, bigScreen);", PANEL)

    def test_big_type_only_on_a_big_screen_and_other_reports_untouched(self):
        self.assertIn("const SIZE = 'lg';", PANEL)
        self.assertIn("const lgOnly = (size, classes) => (size === 'lg' ? ` ${classes}` : '');", REPORT_KIT)
        # Крупный вид добавляет классы только с префиксом xl: — телефон и узкое окно как были.
        for classes in re.findall(r"lgOnly\(size, '([^']+)'\)", REPORT_KIT):
            with self.subTest(classes=classes):
                self.assertTrue(all(c.startswith("xl:") for c in classes.split()), classes)
        # Без size разметка прежняя: прежние классы остались первыми, lgOnly — только добавка.
        for before in ('className={`rounded-xl bg-slate-50 px-3 py-2.5${lgOnly(',
                       '`text-[11px] font-medium uppercase tracking-wide text-slate-500${lgOnly(',
                       '`mt-1 text-[11.5px] leading-snug text-slate-500${lgOnly(',
                       '`px-3 py-2 text-[12.5px] ${right ? '):
            self.assertIn(before, REPORT_KIT)
        # Полоса доли общего набора не тронута: у итогов и причин своя ячейка доли (ShareCell).
        self.assertIn('<div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">', REPORT_KIT)
        self.assertIn("export const Bar = ({ done, total, tone = 'indigo', caption = null }) => {", REPORT_KIT)


class UploadAndRouteTests(unittest.TestCase):
    def test_tez_upload_hint_mentions_the_city_column(self):
        # С 06.11.2026 колонка названа прямо в окне загрузки, а подсказка «i» говорит, где виден город.
        self.assertIn("с колонками <b>fio</b>, <b>phone</b> и, если есть, <b>город</b>", SETTINGS)
        self.assertIn("Колонка «город» необязательна: город виден в карточке водителя у оператора и в журнале, "
                      "по нему «Воронка ОП» во вкладке «Аналитика» разбивается по городам", SETTINGS)

    def test_funnel_route_is_declared_and_listed(self):
        self.assertIn("'/api/dial_list/departments/<int:department_id>/funnel'", ROUTES)
        self.assertIn("GET     /api/dial_list/departments/<id>/funnel", ROUTES)


if __name__ == "__main__":
    unittest.main()
