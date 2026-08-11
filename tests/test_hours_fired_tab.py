"""Вкладки «Активные»/«Уволенные» в разделе «Учет часов».

Владелец (2026-08-11): уволенный оператор должен попадать во вкладку «Уволенные».
До этого действовало правило коммита 51731fdc («оператор отображается в активных
даже если он уволен если у него есть хоть какие то показатели»), из-за которого
уволенная в июне операторка оставалась «активной» в августе — импорт задним числом
записал ей два дня со звонками. Новое правило: уволенный остаётся в «Активных»
только если увольнение пришлось на просматриваемый месяц или позже.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "App.jsx"
DB_PATH = ROOT / "database.py"


class HoursFiredTabFrontendTests(unittest.TestCase):
    """Source-level contract для фронта раздела часов."""

    @classmethod
    def setUpClass(cls):
        cls.src = APP_PATH.read_text(encoding="utf-8-sig")
        start = cls.src.index("const { filteredOperators, activeCount, firedCount } = useMemo(")
        end = cls.src.index("// Group operators by direction", start)
        cls.memo = cls.src[start:end]

    def test_fired_tab_is_decided_by_dismissal_month_not_by_metrics(self):
        self.assertIn("const firedMonth = getDismissalMonth(op);", self.memo)
        self.assertIn("firedMonth >= month", self.memo)
        # Показатели остаются только фолбэком, когда даты увольнения нет.
        self.assertIn("firedMonth === null", self.memo)
        self.assertIn("? hasAnyHoursIndicators(op)", self.memo)

    def test_memo_recomputes_when_month_changes(self):
        deps = self.memo[self.memo.rindex("}, ["):]
        self.assertIn("month]", deps)

    def test_dismissal_month_helper_reads_backend_field(self):
        start = self.src.index("const getDismissalMonth = (op) =>")
        end = self.src.index("const formatDismissalDate", start)
        helper = self.src[start:end]
        self.assertIn("op?.dismissal_date", helper)
        self.assertIn("slice(0, 7)", helper)

    def test_fallback_helper_is_preserved_for_old_payloads(self):
        # Якорь, на который опираются другие тесты, и сам фолбэк должны остаться.
        self.assertIn("const hasAnyHoursIndicators = (op) =>", self.src)

    def test_row_shows_dismissed_badge(self):
        anchor = self.src.index('<span className="truncate">{op.name}</span>')
        block = self.src[anchor:anchor + 900]
        self.assertIn("isFiredLikeOperator(op)", block)
        self.assertIn("Уволен", block)
        self.assertIn("Уволен(а) с ${formatDismissalDate(op)}", block)


class HoursFiredTabBackendTests(unittest.TestCase):
    """Контракт бэкенда: payload часов несёт дату увольнения."""

    @classmethod
    def setUpClass(cls):
        cls.src = DB_PATH.read_text(encoding="utf-8-sig")

    def test_dismissal_date_prefers_period_over_status_history(self):
        start = self.src.index("def _load_dismissal_dates_tx")
        end = self.src.index("def _dismissal_date_iso", start)
        helper = self.src[start:end]
        # Фактическая дата увольнения — из периода; user_history только фолбэк.
        self.assertIn("COALESCE(dp.start_date, dh.changed_at::date)", helper)
        self.assertIn("p.status_code = 'dismissal'", helper)
        self.assertIn("uh.field_changed = 'status'", helper)
        self.assertIn("IN ('fired', 'dismissal')", helper)
        sql = helper[helper.index("cursor.execute("):]
        self.assertLess(sql.index("operator_schedule_status_periods"), sql.index("user_history"))

    def test_both_hours_payload_builders_expose_dismissal_date(self):
        for builder, terminator in (
            ("def get_daily_hours_by_supervisor_month", "def get_daily_hours_for_all_month"),
            ("def get_daily_hours_for_all_month", "def aggregate_month_from_daily"),
        ):
            start = self.src.index(builder)
            end = self.src.index(terminator, start + len(builder))
            block = self.src[start:end]
            with self.subTest(builder=builder):
                self.assertIn("self._load_dismissal_dates_tx(cursor, op_ids)", block)
                self.assertIn(
                    '"dismissal_date": self._dismissal_date_iso(dismissal_dates, op_id),',
                    block,
                )

    def test_dismissal_date_is_serialized_as_iso(self):
        start = self.src.index("def _dismissal_date_iso")
        end = self.src.index("def get_daily_hours_by_group_month", start)
        helper = self.src[start:end]
        self.assertIn("isoformat()", helper)


class DismissalTabRuleTests(unittest.TestCase):
    """Само правило, продублированное на Python: уволен раньше месяца — «Уволенные»."""

    @staticmethod
    def _keeps_active(dismissal_date, month, has_indicators):
        """Портированная логика useMemo (src/App.jsx) для проверки решений на данных прода."""
        raw = str(dismissal_date or "").strip()
        fired_month = raw[:7] if re.match(r"^\d{4}-\d{2}", raw) else None
        if fired_month is None:
            return bool(has_indicators)
        return fired_month >= month

    def test_operator_dismissed_earlier_leaves_active_tab(self):
        # Асанова Айсулу (id 25): период увольнения с 2026-06-19, но импорт
        # записал ей звонки 3 и 4 августа — раньше это держало её в «Активных».
        self.assertFalse(self._keeps_active("2026-06-19", "2026-08", has_indicators=True))
        self.assertFalse(self._keeps_active("2026-06-19", "2026-07", has_indicators=True))

    def test_operator_dismissed_within_month_stays_active(self):
        # Реальные августовские увольнения: 348 (10.08), 389 (08.08), 346 (03.08).
        for dismissal_date in ("2026-08-10", "2026-08-08", "2026-08-03"):
            with self.subTest(dismissal_date=dismissal_date):
                self.assertTrue(self._keeps_active(dismissal_date, "2026-08", has_indicators=True))

    def test_month_of_dismissal_is_kept_even_without_any_metrics(self):
        # Увольнение в этом месяце — строка нужна, даже если показателей нет.
        self.assertTrue(self._keeps_active("2026-08-03", "2026-08", has_indicators=False))

    def test_future_months_keep_operator_active(self):
        # Смотрим июль у уволенного в августе — тогда он ещё работал.
        self.assertTrue(self._keeps_active("2026-08-10", "2026-07", has_indicators=False))

    def test_missing_date_falls_back_to_previous_behaviour(self):
        self.assertTrue(self._keeps_active(None, "2026-08", has_indicators=True))
        self.assertFalse(self._keeps_active(None, "2026-08", has_indicators=False))
        self.assertTrue(self._keeps_active("", "2026-08", has_indicators=True))


if __name__ == "__main__":
    unittest.main()
