# -*- coding: utf-8 -*-
"""Интерфейс раздела «Отметки» по ТЗ #307 — то, что легко потерять правкой.

Раздел читается кадровиком каждый день, и каждое из требований здесь уже было
нарушено в прежней версии: таблица открывалась «сначала проблемные», одним
куском до 500 строк, с фильтром только по отделам Workpace и выбором людей в
отчёт без поиска и без центрального офиса. Проверки текстовые — компонент на
2800 строк без сборки не отрендерить, а стеречь нужно именно разметку.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = (ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx").read_text(encoding="utf-8-sig")
BOT = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")


class MainScreenTests(unittest.TestCase):
    def test_opens_with_recent_marks_first(self):
        # п. 1: «на главной отметки показаны от самых недавних, а не от проблемных».
        self.assertRegex(VIEW, r"departments: \[\], q: '', kind: '', sort: 'recent'")
        sorts = VIEW[VIEW.index("const ATTENDANCE_SORTS = ["):]
        first = re.search(r"value: '(\w+)'", sorts).group(1)
        self.assertEqual(first, "recent")

    def test_page_size_can_be_chosen(self):
        self.assertIn("const ATTENDANCE_PAGE_SIZES = [50, 100, 200, 500];", VIEW)
        self.assertIn("changeAttendancePageSize", VIEW)

    def test_pages_use_the_shared_pager(self):
        self.assertIn("<IosPager", VIEW)
        self.assertIn("offset: Math.max(0, (page - 1) * limit)", VIEW)

    def test_several_departments_can_be_picked(self):
        block = VIEW[VIEW.index('label="Подразделения"'):]
        block = block[:block.index("</FilterField>")]
        self.assertIn("multiple", block)
        self.assertIn("directoryDepartmentOptions", block)

    def test_period_is_no_longer_a_week(self):
        match = re.search(r"GROUP_LATE_ATTENDANCE_MAX_DAYS = (\d+)", BOT)
        self.assertGreaterEqual(int(match.group(1)), 365)

    def test_pending_days_are_shown_not_hidden(self):
        # Молча обрезанный период читается как «в эти дни никто не отмечался».
        self.assertIn("attendancePending.length > 0", VIEW)


class ReportPickerTests(unittest.TestCase):
    def test_people_come_from_both_sources(self):
        # Раньше список собирался из сводки нарушений Workpace — центрального
        # офиса в нём не было вовсе (жалоба в описании задачи #307).
        block = VIEW[VIEW.index("const reportEmployees = useMemo"):]
        block = block[:block.index("}, [directory, employees]);")]
        self.assertIn("directory?.employees", block)

    def test_picker_has_name_search(self):
        # п. 6: поиск по ФИО вместо прокрутки алфавитного списка.
        self.assertIn("employeeQuery", VIEW)
        self.assertIn("visibleReportEmployees.map", VIEW)


class PlanTabTests(unittest.TestCase):
    def test_tab_is_registered_and_rendered(self):
        self.assertIn("{ key: 'plan', label: 'Графики', icon: CalendarRange }", VIEW)
        self.assertIn("{tab === 'plan' && renderPlanRules()}", VIEW)

    def test_hours_mode_is_offered(self):
        self.assertIn("{ value: 'hours', label: 'По часам' }", VIEW)

    def test_rule_source_is_visible_in_the_table(self):
        # С ручным графиком опоздание считается от него — кадровик должен это видеть.
        self.assertIn("row.plan_source === 'rule'", VIEW)


class ApiTests(unittest.TestCase):
    def test_new_routes_exist(self):
        for route in ("/api/group_late_bot/directory", "/api/group_late_bot/plan_rules",
                      "/api/group_late_bot/plan_rules/<int:rule_id>"):
            self.assertIn(f"@app.route('{route}'", BOT)

    def test_nightly_job_is_registered(self):
        self.assertIn("id='group_late_nightly'", BOT)

    def test_directory_does_not_wait_for_the_night(self):
        """Первый деплой показал: до ночной джобы центрального офиса в справочнике
        нет вовсе — ровно та жалоба, с которой пришла задача. Справочник обязан
        подтянуть состав Clockster сам, если его нет или он устарел."""
        route = BOT[BOT.index("def api_group_late_bot_directory"):]
        route = route[:route.index("\n@app.route")]
        self.assertIn("_group_late_refresh_clockster_roster_if_stale()", route)
        helper = BOT[BOT.index("def _group_late_refresh_clockster_roster_if_stale"):]
        helper = helper[:helper.index("\n@app.route")]
        self.assertIn("acquire(blocking=False)", helper)
        self.assertIn("source='clockster'", helper)
        self.assertIn("GROUP_LATE_CLOCKSTER_ROSTER_MAX_AGE", helper)


if __name__ == "__main__":
    unittest.main()
