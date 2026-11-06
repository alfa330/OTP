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
        self.assertIn("const [view, setView] = useState('funnel');", ANALYTICS)

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


class UploadAndRouteTests(unittest.TestCase):
    def test_tez_upload_hint_mentions_the_city_column(self):
        self.assertIn("Колонка «город» необязательна: по ней «Воронка ОП» во вкладке «Аналитика»", SETTINGS)

    def test_funnel_route_is_declared_and_listed(self):
        self.assertIn("'/api/dial_list/departments/<int:department_id>/funnel'", ROUTES)
        self.assertIn("GET     /api/dial_list/departments/<id>/funnel", ROUTES)


if __name__ == "__main__":
    unittest.main()
