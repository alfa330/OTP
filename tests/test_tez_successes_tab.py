# -*- coding: utf-8 -*-
"""Успешки ОП Тез переехали из «Учёта часов» в раздел «Воронка ОП Tez» (03.11.2026).

Владелец: «для отдела tez с раздела учет часов перенести весь функционал по успешкам в раздел
авто обзвон TEZ и переименовать его в Воронка ОП Tez; загрузка базы по сути уже там есть».
Колонки таблицы часов («План успешек», вкладка «Успешки») остаются — выбор владельца в тот же
день: они считаются от часов той же таблицы.

Контракты разметки (исполняемая логика вкладки — tests/dial_list_tez_successes.test.mjs):
  * окно «База лидов и успешки» и его пункт меню из «Учёта часов» ушли, старых компонентов нет;
  * у Тез в разделе есть вкладка «Успешки», у удалённого КЦ — нет;
  * своей загрузки во вкладке нет: базу грузят в «Базе водителей», сервер зеркалит её сам;
  * детализация лидов показывает обе поездки, и причину статуса — не только в title;
  * поздний ответ прошлого месяца не ложится на новый;
  * раздел называется «Воронка ОП Tez» в меню (обе ветки) и в шапке;
  * «План успешек» в часах по-прежнему перечитывается вместе с часами.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
VIEW = (ROOT / "src" / "components" / "dial_list" / "DialListView.jsx").read_text(encoding="utf-8-sig")
PANEL = (ROOT / "src" / "components" / "dial_list" / "TezSuccessesPanel.jsx").read_text(encoding="utf-8-sig")
LOGIC = (ROOT / "src" / "components" / "dial_list" / "tezAutodial.js").read_text(encoding="utf-8-sig")
SETTINGS = (ROOT / "src" / "components" / "sip" / "DialListSettings.jsx").read_text(encoding="utf-8-sig")
FORMULA = (ROOT / "src" / "utils" / "salaryFormula.js").read_text(encoding="utf-8-sig")


def _block(source, start_marker, end_marker):
    start = source.index(start_marker)
    return source[start:source.index(end_marker, start)]


class HoursAccountingLostTheSheetTests(unittest.TestCase):
    def test_sheet_and_menu_item_are_gone(self):
        for token in ("TezLeadsPanel", "TezOpPlanPanel", "salesOverlay", "База лидов и успешки",
                      'title="База лидов ОП TEZ"', "tezSuccessReloadKey"):
            # assertFalse, а не assertNotIn: при падении не печатать весь App.jsx.
            self.assertFalse(token in APP, f"в App.jsx осталось: {token}")
        self.assertFalse((ROOT / "src" / "components" / "salary" / "TezLeadsPanel.jsx").exists())
        self.assertFalse((ROOT / "src" / "components" / "salary" / "TezOpPlanPanel.jsx").exists())

    def test_hours_columns_stay_and_reload_with_hours(self):
        # Колонки остаются в часах (выбор владельца 03.11.2026).
        self.assertIn("{ key: 'tez_successes', label: 'Успешки', unit: 'шт' }", APP)
        self.assertIn("<TezOpPlanCell", APP)
        self.assertIn("<TezDepartmentPlanSummary summary={tezPlanSummary} />", APP)
        # Ключ перечитывания жив: его двигает загрузка часов, а не удалённое окно.
        loader = _block(APP, "setOperators(hoursResp.data.operators);", "setLoadedHoursScope(requestScope);")
        self.assertIn("setTezPlanReloadKey((key) => key + 1);", loader)
        self.assertIn("}, [isTezOpContext, tezOpDeptId, month, user?.id, tezPlanReloadKey]);", APP)
        self.assertIn("}, [isTezOpContext, selectedGroupId, month, user?.id, tezPlanReloadKey]);", APP)

    def test_plan_hint_points_to_the_new_place(self):
        self.assertIn("в разделе «Воронка ОП Tez», вкладка «Успешки»", FORMULA)
        self.assertNotIn("в панели «План ОП TEZ»", FORMULA)


class SectionNameTests(unittest.TestCase):
    def test_menu_carries_the_new_name_in_both_branches(self):
        item = '<FaIcon className="fas fa-filter"></FaIcon> <span className="sidebar-text">Воронка ОП Tez</span>'
        self.assertEqual(APP.count(item), 2)  # ветка админа и ветка руководителей отдела
        self.assertNotIn('<span className="sidebar-text">Автообзвон Тез</span>', APP)
        # Ключ раздела прежний: ссылки ?view=tez_autodial и права не меняются.
        self.assertEqual(APP.count("handleSidebarViewNavigation(e, 'tez_autodial')"), 2)
        self.assertIn("export const TEZ_SECTION_TITLE = 'Воронка ОП Tez';", LOGIC)


class SuccessesTabTests(unittest.TestCase):
    def test_tab_is_tez_only_and_renders_the_panel(self):
        self.assertIn("{ value: 'successes', label: 'Успешки'", VIEW)
        self.assertIn(".filter((t) => tez || t.value !== 'successes')", VIEW)
        screen = _block(VIEW, "{tab === 'successes' && tez && (", "/>")
        self.assertIn("<TezSuccessesPanel", screen)
        self.assertIn("key={selected.department_id}", screen)
        self.assertIn("month={successMonth}", screen)

    def test_tab_has_no_upload_of_its_own(self):
        # База одна на оба места: её грузят во вкладке «База водителей», сервер зеркалит.
        self.assertNotIn('type="file"', PANEL)
        self.assertNotIn("/api/tez_leads/upload", PANEL)
        self.assertIn("«База водителей»", PANEL)
        self.assertIn("по ней во вкладке «Успешки» считаются успешки и зарплата ОП", SETTINGS)
        self.assertNotIn("«Базу лидов ОП TEZ»", SETTINGS)

    def test_moved_functions_are_all_there(self):
        for endpoint in ("/api/tez_leads/stats", "/api/tez_leads/detail", "/api/tez_leads/day",
                         "/api/tez_leads/recompute", "/api/tez_leads/export", "/api/department_plan",
                         "/api/tez_leads/batches/${batch.id}/${mode}", "/api/tez_leads/batches/${batchId}/history"):
            self.assertIn(endpoint, PANEL, endpoint)
        for label in ("'Операторы'", "'По дням'", "'Лиды'", "'Загрузки'", "Сверить сейчас", "Excel",
                      "План отдела", "Воронка базы месяца", "Удалить базу", "Восстановить"):
            self.assertIn(label, PANEL, label)

    def test_detail_shows_both_trips_and_the_status_reason(self):
        trips = _block(PANEL, "const LeadTripDates =", "\n};")
        self.assertIn("row?.prev_month_first_order_at", trips)
        self.assertIn("row?.month_first_order_at", trips)
        self.assertIn("Предыдущий месяц", trips)
        self.assertIn("Отчётный месяц", trips)
        self.assertIn("причина статуса", trips)
        self.assertIn("row?.status_rule === 'active_prev_month'", trips)
        table = _block(PANEL, "<tbody className=\"divide-y divide-slate-50\">\n                                {rows.map((row) => (",
                       "</tbody>")
        self.assertIn("<LeadTripDates row={row} />", table)

    def test_late_answers_cannot_land_on_another_month(self):
        stats = _block(PANEL, "const loadStats = useCallback(", "}, [request, scope, year, monthNum]);")
        self.assertIn("const my = ++statsSeq.current;", stats)
        self.assertGreaterEqual(stats.count("at !== scopeRef.current"), 2)
        plan = _block(PANEL, "const loadPlan = useCallback(", "}, [request, scope, departmentId, year, monthNum]);")
        self.assertGreaterEqual(plan.count("at !== scopeRef.current"), 2)
        leads = _block(PANEL, "const LeadsView = (", "const operatorOptions")
        self.assertIn("let alive = true;", leads)
        self.assertIn("return () => { alive = false; };", leads)
        # Строки прошлого месяца после смены месяца не показываются, даже пока идёт запрос.
        self.assertIn("const fresh = state.scope === scope;", PANEL)
        self.assertIsNotNone(re.search(r"const current = statsScope === scope \? stats : null;", PANEL))

    def test_mirror_texts_name_the_tab(self):
        self.assertIn("Во вкладку «Успешки» не попало:", LOGIC)
        self.assertNotIn("Базе лидов ОП TEZ»:", LOGIC)


if __name__ == "__main__":
    unittest.main()
