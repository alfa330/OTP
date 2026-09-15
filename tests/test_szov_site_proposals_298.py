"""Задача #298 от СВ СЗоВ: «Предложения по доработке функционала сайта».

Пункт 1 — общее FTE в строке «Итого» учёта часов; пункт 2 — очередь «Запросы
по сменам» разделена по группам СВ; пункт 4 — среднее время разговора (ATT)
ещё и целыми секундами в «Биллинге Oktell», на экране и в выгрузке.

Интерфейсные решения, как и в соседних тестах разделов, сторожатся чтением
исходника; выгрузка проверяется настоящими функциями из bot_schedule2.py.
"""

import re
import unittest
from pathlib import Path

from tests.test_oktell_billing_report import _extract_namespace


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _between(source, start, end):
    start_index = source.index(start)
    return source[start_index:source.index(end, start_index + len(start))]


class HoursFooterFteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _read("src/App.jsx")

    def test_footer_totals_sum_rates(self):
        totals = _between(self.app, "const footerTotals = useMemo(", "// Red → amber → green gradient")
        self.assertIn("let sumRate = 0;", totals)
        self.assertIn("sumRate += rateValue;", totals)
        self.assertIn("sumRate,", totals)
        self.assertIn("hasRateRows,", totals)

    def test_footer_rate_cell_shows_total_fte(self):
        footer = _between(self.app, "{/* FOOTER: итоговые строки */}", "{daysArray.map(day => (")
        self.assertIn("footerTotals.sumRate.toLocaleString('ru-RU', { maximumFractionDigits: 2 })", footer)
        self.assertIn(" FTE`", footer)
        self.assertNotIn("<div className={hoursRateColClass}>—</div>", footer)


class ShiftChangeReviewGroupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = _read("database.py")
        cls.app = _read("src/App.jsx")
        cls.select_sql = _between(
            cls.database, "SHIFT_CHANGE_REQUEST_SELECT = \"\"\"", "def _serialize_shift_change_request_row"
        )
        cls.serializer = _between(
            cls.database, "def _serialize_shift_change_request_row", "\n    def "
        )

    def test_select_resolves_operator_group_on_shift_date(self):
        self.assertIn("grp.name AS group_name", self.select_sql)
        self.assertIn("grp_sv.name AS group_supervisor_name", self.select_sql)
        # Правило выбора группы общее с записью дня часов, а не своя копия.
        self.assertIn("MEMBERSHIP_MONTH_OVERLAP_SQL.format(m='gom', day='r.shift_date')", self.select_sql)
        self.assertIn("MEMBERSHIP_DAY_DISTANCE_SQL.format(m='gom', day='r.shift_date')", self.select_sql)
        self.assertIn("FROM group_supervisor_memberships gsm", self.select_sql)

    def test_select_columns_match_serializer_unpacking(self):
        columns = _between(self.select_sql, "SELECT", "FROM work_shift_change_requests r")
        column_count = len([part for part in columns[len("SELECT"):].split(",") if part.strip()])
        unpacking = _between(self.serializer, "(\n", ") = row")
        name_count = len([part for part in unpacking.split(",") if part.strip()])
        self.assertEqual(column_count, name_count)

    def test_payload_carries_group_and_its_supervisor(self):
        self.assertIn("'group': {", self.serializer)
        self.assertIn("'supervisorId': int(group_supervisor_id) if group_supervisor_id is not None else None,", self.serializer)
        self.assertIn("'supervisorName': group_supervisor_name,", self.serializer)

    def test_review_queue_renders_sections_per_group(self):
        self.assertIn("const reviewRequestGroups = useMemo(", self.app)
        self.assertIn("{reviewRequestGroups.map(group => (", self.app)
        self.assertIn("{group.items.map(item => {", self.app)
        # Одна группа — без заголовка: подпись ничего не различает.
        self.assertIn("{reviewRequestGroups.length > 1 && (", self.app)
        self.assertNotIn("{reviewVisibleRequests.map(item => {", self.app)

    def test_own_group_goes_first(self):
        memo = _between(self.app, "const reviewRequestGroups = useMemo(", "}, [reviewVisibleRequests, user?.id]);")
        self.assertIn("Number(item?.group?.supervisorId) === myId", memo)
        self.assertIn("(Number(b.isMine) - Number(a.isMine))", memo)
        self.assertIn("'Без группы'", memo)


class BillingAttSecondsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _read("src/components/resources/ResourceFteView.jsx")
        cls.ns = _extract_namespace()

    def test_screen_shows_att_in_seconds(self):
        self.assertIn(">Ср. разговор, сек</th>", self.view)
        self.assertIn(">ATT, сек</th>", self.view)
        self.assertEqual(self.view.count("{attSeconds === null ? '—' : formatInt(attSeconds)}"), 2)
        self.assertIn("`Ср. время разговора · ${formatInt(billingAttSeconds)} сек`", self.view)

    def test_export_columns_stay_consistent(self):
        columns = self.ns["_oktell_billing_export_columns"]
        for mode, seconds_header in (("park", "Ср. разговор, сек"), ("line", "Ср. разговор, сек"),
                                     ("operator", "АТТ, сек")):
            with self.subTest(mode=mode):
                headers, widths, formats = columns(mode)
                self.assertEqual(len(widths), len(headers))
                self.assertTrue(all(1 <= index <= len(headers) for index in formats))
                seconds_column = headers.index(seconds_header) + 1
                # Секунды — обычное целое число, не доля суток под форматом времени.
                self.assertNotIn(seconds_column, formats)
                self.assertIn(seconds_column - 1, formats)

    def test_export_values_put_seconds_next_to_duration(self):
        values = self.ns["_oktell_billing_export_values"]
        park = values("park", {
            "park": "iTaxi", "arrived": 10, "served": 3, "lost": 7, "served_sl": 2, "greet_drop": 0,
            "talk_seconds": 337, "wait_ok_seconds": 30, "wait_lost_seconds": 0, "total_seconds": 400,
        })
        # Длительность в выгрузке округляется до целой секунды, секунды рядом — те же.
        self.assertAlmostEqual(park[6], 112 / 86400.0)
        self.assertEqual(park[7], 112)

        operator = values("operator", {
            "operator": "Оператор", "served": 4, "talk_seconds": 450, "hold_seconds": 0,
            "postproc_seconds": 0, "talk_in_seconds": 450, "talk_out_seconds": 0,
            "dial_seconds": 0, "wait_seconds": 0, "pause_seconds": 0,
        })
        self.assertAlmostEqual(operator[2], 112 / 86400.0)
        self.assertEqual(operator[3], 112)

        nobody = values("operator", {"operator": "Оператор", "served": 0})
        self.assertEqual(nobody[3], "—")


class BillingDriverRatingTests(unittest.TestCase):
    """Пункт 4: «Средняя оценка по паркам» — балл водителя в IVR после разговора."""

    @classmethod
    def setUpClass(cls):
        cls.view = _read("src/components/resources/ResourceFteView.jsx")
        cls.ns = _extract_namespace()

    def test_sql_joins_ivr_rating_once_per_chain(self):
        for group_by in ("park", "line"):
            with self.subTest(group_by=group_by):
                sql = self.ns["_oktell_billing_sql"]("20260908", "20260915", 0, 1440, 20, group_by)
                self.assertIn("FROM oktell.dbo.quality_employes GROUP BY chainid) qe", sql)
                self.assertIn("ON qe.chainid = t.chainid", sql)
                # '0' — водитель ничего не нажал за отведённое время: в среднее не идёт.
                self.assertIn("answer IN (N'1', N'2', N'3', N'4', N'5')", sql)
                self.assertIn("AS rating_sum", sql)
                self.assertIn("AS rating_count", sql)

    def test_report_keeps_sum_and_count_not_average(self):
        report = self.ns["_oktell_billing_build_report"]([
            {"report_date": "2026-09-14", "taxi_park": "iTaxi", "arrived": 3, "served": 3,
             "rating_sum": 9, "rating_count": 2},
            {"report_date": "2026-09-15", "taxi_park": "iTaxi", "arrived": 5, "served": 4,
             "rating_sum": 5, "rating_count": 1},
            {"report_date": "2026-09-15", "taxi_park": "Jana", "arrived": 1, "served": 1,
             "rating_sum": 0, "rating_count": 0},
        ])
        itaxi = next(item for item in report["parks"] if item["park"] == "iTaxi")
        # Итог периода — 14/3, а не среднее дневных средних (4,5 и 5).
        self.assertEqual((itaxi["rating_sum"], itaxi["rating_count"]), (14, 3))
        self.assertEqual((report["totals"]["rating_sum"], report["totals"]["rating_count"]), (14, 3))

    def test_export_average_rating_column(self):
        values = self.ns["_oktell_billing_export_values"]
        for mode in ("park", "line"):
            with self.subTest(mode=mode):
                headers, widths, formats = self.ns["_oktell_billing_export_columns"](mode)
                self.assertEqual(headers[-1], "Ср. оценка")
                self.assertEqual(len(widths), len(headers))
                self.assertEqual(formats[len(headers)], "0.00")
                rated = values(mode, {"park": "iTaxi", "line": "", "arrived": 5, "served": 4,
                                      "rating_sum": 14, "rating_count": 3})
                self.assertEqual(len(rated), len(headers))
                self.assertAlmostEqual(rated[-1], 14 / 3)
                unrated = values(mode, {"park": "iTaxi", "line": "", "arrived": 5, "served": 4,
                                        "rating_sum": 0, "rating_count": 0})
                self.assertEqual(unrated[-1], "—")

    def test_screen_shows_average_rating_column(self):
        self.assertIn(">Ср. оценка</th>", self.view)
        self.assertIn("const ratingAvg = safeRatio(item.rating_sum, item.rating_count);", self.view)
        self.assertIn("{ratingAvg === null ? '—' : formatNumber(ratingAvg, 2)}", self.view)


class TaskColleagueObserverTests(unittest.TestCase):
    """Пункт 3: СВ видит задачи коллег-СВ своего отдела — только на просмотр."""

    @classmethod
    def setUpClass(cls):
        cls.database = _read("database.py")
        cls.view = _read("src/components/tasks/TasksView.jsx")
        cls.board = _read("src/components/tasks/TaskBoardWorkspace.jsx")

    def test_list_scope_adds_department_colleagues_only_for_sv(self):
        scope = _between(self.database, "    def _task_scope_filters(", "    def get_tasks_for_requester(")
        self.assertIn("elif role in TASK_PERSONAL_SCOPE_ROLES:", scope)
        self.assertIn("if role == 'sv':", scope)
        self.assertIn('conditions.append(f"({participant_sql} OR {self._TASK_COLLEAGUE_SV_SQL})")', scope)
        colleague = _between(self.database, "    _TASK_COLLEAGUE_SV_SQL = (", "\n    )")
        self.assertIn("task_sv_creator.role = 'sv'", colleague)
        self.assertIn("task_sv_viewer.department_id IS NOT NULL", colleague)
        self.assertIn("task_sv_creator.department_id = task_sv_viewer.department_id", colleague)
        self.assertEqual(colleague.count("%s"), 1)

    def test_colleague_gets_reading_not_actions(self):
        # Чтение открыто в трёх местах: журнал отчётов, лента уточнений, файлы.
        self.assertEqual(self.database.count("self._task_observable_by_colleague_tx("), 3)
        self.assertEqual(self.database.count("allow_observer=True"), 2)
        self.assertIn("allow_observer=True", _between(self.database, "    def get_task_reports(", "\n    def "))
        self.assertIn("allow_observer=True", _between(self.database, "    def get_task_messages(", "\n    def "))
        # Мутации по-прежнему решает проверка участия, у неё нет лазейки для коллеги.
        visible = _between(self.database, "    def _task_visible_for_requester(", "    def _task_viewer_is_observer(")
        self.assertNotIn("_TASK_COLLEAGUE_SV_SQL", visible)

    def test_viewer_is_observer_rule(self):
        import ast
        import textwrap

        tree = ast.parse(self.database)
        database_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Database")
        method = next(
            node for node in database_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_task_viewer_is_observer"
        )
        namespace = {"normalize_role_value": lambda role: str(role or "").strip().lower()}
        exec(textwrap.dedent(ast.get_source_segment(self.database, method)), namespace)
        observer = namespace["_task_viewer_is_observer"]

        self.assertTrue(observer("sv", 477, 202, None, [{"id": 2}]))
        self.assertFalse(observer("sv", 477, 477, None, []))            # сама поставила
        self.assertFalse(observer("sv", 477, 202, 477, []))             # поручитель
        self.assertFalse(observer("sv", 477, 202, None, [{"id": 477}]))  # среди исполнителей
        self.assertFalse(observer("admin", 1, 202, None, []))           # админ действует как раньше
        self.assertFalse(observer("trainer", 5, 202, None, []))

    def test_list_payload_carries_the_flag(self):
        listing = _between(self.database, "    def get_tasks_for_requester(", "\n    def ")
        self.assertIn('"viewer_is_observer": self._task_viewer_is_observer(', listing)

    def test_frontend_hides_actions_for_observer(self):
        self.assertIn("if (task?.viewer_is_observer) return false;", self.view)
        self.assertIn("if (task?.viewer_is_observer) return [];", self.view)
        self.assertEqual(self.view.count("|| !!task?.viewer_is_observer}"), 2)
        self.assertIn("if (task?.viewer_is_observer) return { type: 'blocked'", self.board)
        self.assertIn("draggable={!task?.viewer_is_observer}", self.board)
        self.assertIn("onDragStart={entry.task?.viewer_is_observer ? undefined :", self.board)
        self.assertIn("{!task?.viewer_is_observer && (", self.board)
        # Пустой ряд действий объясняет, почему кнопок нет, а не намекает на статус.
        self.assertIn("'Задача коллеги — только просмотр.'", self.view)


if __name__ == "__main__":
    unittest.main()
