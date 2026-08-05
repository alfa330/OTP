# -*- coding: utf-8 -*-
"""Общий план отдела ОП TEZ: сумма ставок × план на 1 FTE × 0,8 и его закрытие.

Правило владельца (Сынакбаева Анель, 2026-08-05): в сумму идут ставки ВСЕХ не
уволенных операторов ОП, целиком — приём или уход внутри месяца ставку не дробит
(в отличие от индивидуального плана, где новичок/увольнение пересчитываются).
"""

import ast
import calendar
import copy
import sys
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "database.py"
DB_SOURCE = DB_PATH.read_text(encoding="utf-8-sig")
DB_TREE = ast.parse(DB_SOURCE)
APP_SRC = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
PANEL_SRC = (ROOT / "src" / "components" / "salary" / "TezOpPlanPanel.jsx").read_text(encoding="utf-8-sig")
BOT_SRC = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")


def _load_db_method(name):
    """Достаёт метод Database без импорта модуля.

    `import database` на Windows падает (time.tzset) и поднимает пул к боевой базе,
    поэтому метод вытаскиваем через ast — как в остальных тестах по TEZ.
    """
    namespace = {"date": date, "calendar": calendar}
    for node in DB_TREE.body:
        if isinstance(node, ast.Assign) and all(
            isinstance(t, ast.Name) and t.id.isupper() for t in node.targets
        ):
            try:
                module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
                ast.fix_missing_locations(module)
                exec(compile(module, str(DB_PATH), "exec"), namespace)
            except Exception:
                pass
    cls = next(n for n in DB_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[copy.deepcopy(fn)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(DB_PATH), "exec"), namespace)
    return namespace[name], namespace


class FakeCursor:
    """Отвечает на запросы метода по содержимому SQL."""

    def __init__(self, *, groups=1, fte=5.5, staff=6, successes=202, stored_plan=None):
        self.groups, self.fte, self.staff = groups, fte, staff
        self.successes, self.stored_plan = successes, stored_plan
        self.queries = []
        self._row = None

    def execute(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params))
        if "FROM groups" in sql:
            self._row = (self.groups,)
        elif "group_operator_memberships" in sql:
            self._row = (self.fte, self.staff)
        elif "tez_lead_successes" in sql:
            self._row = (self.successes,)
        elif "department_monthly_plans" in sql:
            self._row = self.stored_plan
        else:
            raise AssertionError(f"Неожиданный запрос: {sql[:80]}")

    def fetchone(self):
        return self._row


class FakeDb:
    def __init__(self, cursor):
        self._cursor = cursor

    @contextmanager
    def _get_cursor(self):
        yield self._cursor


SUMMARY, NS = _load_db_method("get_tez_op_department_plan_summary")
GET_PLAN, _ = _load_db_method("get_department_monthly_plan")


class DepartmentPlanSummaryTests(unittest.TestCase):
    def _summary(self, **kwargs):
        plan_per_fte = kwargs.pop("plan_per_fte", 100.0)
        cursor = FakeCursor(**kwargs)
        db = FakeDb(cursor)
        return SUMMARY(db, 560, 2026, 7, plan_per_fte=plan_per_fte), cursor

    def test_formula(self):
        """5,5 FTE × 100 × 0,8 = 440; закрытие = 202 / 440."""
        out, _ = self._summary()
        self.assertEqual(out["fte_total"], 5.5)
        self.assertEqual(out["operators_count"], 6)
        self.assertEqual(out["coefficient"], 0.8)
        self.assertEqual(out["plan_total"], 440.0)
        self.assertEqual(out["successes_total"], 202)
        self.assertEqual(out["closure_pct"], 45.9)

    def test_no_tez_op_groups_gives_no_summary(self):
        """Для отделов без групп ОП TEZ сводки нет — поле останется пустым."""
        out, _ = self._summary(groups=0)
        self.assertIsNone(out)

    def test_plan_not_set(self):
        """План на 1 FTE не задан — план 0, процент не считаем (а не 0%)."""
        out, _ = self._summary(plan_per_fte=0)
        self.assertEqual(out["plan_total"], 0.0)
        self.assertIsNone(out["closure_pct"])

    def test_staff_filter_covers_month_and_keeps_mid_month_dismissals(self):
        """Состав — членство, пересекающее месяц.

        Увольнение внутри месяца план НЕ уменьшает (владелец, 2026-08-05: «какой
        план был в начале месяца, такой и остаётся»): успешки ушедшего идут в
        факт, значит и его ставка обязана остаться в плане. Но «числится вечно»
        отсекаем: у уволенного должен быть след работы именно в этом месяце —
        смена в учёте часов либо успешка, иначе его ставка висела бы в плане
        всех будущих месяцев (членство после ухода часто остаётся открытым).
        """
        _, cursor = self._summary()
        sql, params = next(q for q in cursor.queries if "group_operator_memberships" in q[0])
        self.assertIn("DISTINCT ON (u.id)", sql)
        self.assertIn("gom.start_date <= %s", sql)
        self.assertIn("gom.end_date IS NULL OR gom.end_date >= %s", sql)
        self.assertIn("FROM daily_hours dh", sql)
        self.assertIn("dh.day BETWEEN %s AND %s", sql)
        self.assertIn("FROM tez_lead_successes s", sql)
        # Границы месяца: с 1-го по последнее число (июль — 31 день).
        self.assertEqual(params[2], date(2026, 7, 31))
        self.assertEqual(params[3], date(2026, 7, 1))
        self.assertEqual((params[4], params[5]), (date(2026, 7, 1), date(2026, 7, 31)))
        self.assertEqual((params[6], params[7]), (2026, 7))

    def test_plan_per_fte_from_caller_saves_a_query(self):
        """Если план уже прочитан эндпоинтом, второй раз в базу не ходим."""
        _, cursor = self._summary(plan_per_fte=150.0)
        self.assertFalse([q for q in cursor.queries if "department_monthly_plans" in q[0]])

    def test_plan_per_fte_is_read_when_not_passed(self):
        cursor = FakeCursor(stored_plan=(560, 2026, 7, 100.0, None, None))
        db = FakeDb(cursor)
        db.get_department_monthly_plan = GET_PLAN.__get__(db, FakeDb)
        out = SUMMARY(db, 560, 2026, 7)
        self.assertEqual(out["plan_per_fte"], 100.0)
        self.assertEqual(out["plan_total"], 440.0)

    def test_coefficient_is_a_named_constant(self):
        """0,8 не должен быть магическим числом внутри метода."""
        self.assertEqual(NS["TEZ_OP_DEPARTMENT_PLAN_COEFFICIENT"], 0.8)
        # Границу метода берём по следующему def, а не фиксированным срезом:
        # иначе тест ломается от любого роста комментариев внутри метода.
        start = DB_SOURCE.index("    def get_tez_op_department_plan_summary")
        body = DB_SOURCE[start:DB_SOURCE.index("\n    def ", start + 1)]
        self.assertIn("TEZ_OP_DEPARTMENT_PLAN_COEFFICIENT", body)
        self.assertNotIn("* 0.8", body)


def _load_endpoint():
    """Вытаскивает get_department_plan из bot_schedule2 без импорта приложения."""
    tree = ast.parse(BOT_SRC)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "get_department_plan"
    )
    fn = copy.deepcopy(fn)
    fn.decorator_list = []
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    return module


class FakeRequest:
    method = "GET"

    def __init__(self, args):
        self.args = args


class EndpointDb:
    def __init__(self, summary=None, raises=False):
        self._summary = summary
        self._raises = raises
        self.summary_calls = []

    def get_department_monthly_plan(self, dept, year, month):
        return {'department_id': dept, 'year': year, 'month': month, 'plan_per_fte': 100.0,
                'updated_by': None, 'updated_at': None}

    def get_tez_op_department_plan_summary(self, dept, year, month, plan_per_fte=None):
        self.summary_calls.append((dept, year, month, plan_per_fte))
        if self._raises:
            raise RuntimeError('boom')
        return self._summary


def _call_endpoint(db):
    ns = {
        'request': FakeRequest({'department_id': '560', 'year': '2026', 'month': '7'}),
        'jsonify': lambda payload: payload,
        'db': db,
        'logging': __import__('logging'),
        '_build_cors_preflight_response': lambda: ({}, 200),
        '_get_authenticated_requester': lambda: (7, (7, 'x', 'y', 'admin'), None),
        '_normalize_user_role': lambda role: role,
        '_is_global_admin_requester': lambda role, uid: True,
        '_department_scope_id_for_requester': lambda uid: 560,
    }
    exec(compile(_load_endpoint(), "bot_schedule2.py", "exec"), ns)
    return ns['get_department_plan']()


class DepartmentPlanEndpointTests(unittest.TestCase):
    def test_response_carries_the_summary(self):
        summary = {'fte_total': 5.5, 'plan_total': 440.0, 'successes_total': 202, 'closure_pct': 45.9}
        payload, status = _call_endpoint(EndpointDb(summary=summary))
        self.assertEqual(status, 200)
        self.assertEqual(payload['summary'], summary)
        self.assertEqual(payload['plan']['plan_per_fte'], 100.0)

    def test_summary_failure_does_not_break_the_plan(self):
        """Сводка — довесок: если она упала, план на 1 FTE всё равно отдаётся."""
        payload, status = _call_endpoint(EndpointDb(raises=True))
        self.assertEqual(status, 200)
        self.assertIsNone(payload['summary'])
        self.assertEqual(payload['plan']['plan_per_fte'], 100.0)

    def test_plan_per_fte_is_forwarded(self):
        db = EndpointDb(summary=None)
        _call_endpoint(db)
        self.assertEqual(db.summary_calls, [(560, 2026, 7, 100.0)])

    def test_summary_rides_on_the_existing_plan_request(self):
        """Отдельного эндпоинта нет: оба экрана и так дёргают /api/department_plan."""
        body = BOT_SRC[BOT_SRC.index("def get_department_plan()"):]
        body = body[:body.index("def save_department_plan")]
        self.assertIn("get_tez_op_department_plan_summary", body)
        self.assertIn("plan_per_fte=plan.get('plan_per_fte')", body)
        self.assertIn('"summary": summary', body)
        # Сбой сводки не должен ронять сам план.
        self.assertIn("summary = None", body)


class DepartmentPlanFrontendTests(unittest.TestCase):
    def test_hours_view_shows_plan_number_and_closure(self):
        start = APP_SRC.index("Общий план отдела ОП: считается на бэке")
        block = APP_SRC[start:start + 2600]
        self.assertIn("selectedTab === 'tez_successes' && tezPlanSummary", block)
        self.assertIn("Общий план отдела", block)
        self.assertIn("tezPlanSummary.plan_total", block)
        self.assertIn("tezPlanSummary.successes_total", block)
        self.assertIn("tezPlanSummary.closure_pct", block)
        self.assertIn("План на 1 FTE за этот месяц не задан", block)

    def test_summary_comes_from_the_plan_request(self):
        self.assertIn("setTezPlanSummary(resp?.data?.summary || null)", APP_SRC)
        # Факт должен обновляться после пересчёта успешек.
        self.assertIn(
            "}, [isTezOpContext, tezOpDeptId, month, user?.id, tezPlanReloadKey, tezSuccessReloadKey]);",
            APP_SRC,
        )

    def test_operator_row_shows_count_next_to_percent(self):
        start = APP_SRC.index("{selectedTab === 'tez_successes' && (() => {")
        block = APP_SRC[start:start + 2200]
        self.assertIn("Успешки", block)
        self.assertIn("Выполнение", block)
        self.assertIn("{total}", block)
        self.assertIn("planClosureClass(pct)", block)

    def test_closure_color_is_one_helper(self):
        """Цвет процента не должен расходиться между строкой, итогом и карточкой."""
        self.assertIn("function planClosureClass(pct)", APP_SRC)
        self.assertEqual(APP_SRC.count("planClosureClass("), 4)

    def test_plan_panel_shows_the_same_three_numbers(self):
        self.assertIn("setSummary(resp?.data?.summary || null)", PANEL_SRC)
        self.assertIn("Общий план отдела", PANEL_SRC)
        self.assertIn("summary.plan_total", PANEL_SRC)
        self.assertIn("summary.successes_total", PANEL_SRC)
        self.assertIn("summary.closure_pct", PANEL_SRC)
        # После сохранения плана сводку надо перечитать.
        self.assertIn("setReloadKey((k) => k + 1)", PANEL_SRC)
        self.assertIn("validPeriod, reloadKey]", PANEL_SRC)


if __name__ == "__main__":
    unittest.main()
