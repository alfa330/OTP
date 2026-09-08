"""Аукцион смен идёт и на неполной неделе.

08.09.2026 владелец сохранил в «Расчёте ресурсов · Чат» график на 5 дней
(09–13.09) и добавил в него 11 смен руками. В аукционе вместо этого плана
по-прежнему показывались 7 дней предыдущего плана и его смены.

Причина не в отображении: план короче семи дней не проходил ДВА фильтра —
`_get_shift_auction_available_periods_tx` брал только `date_to = date_from + 6`,
а `_validate_shift_auction_period_tx` отвечал `AUCTION_PERIOD_NOT_WEEK`.
В список он не попадал, экран молча оставался на прошлом периоде, и всё
выглядело как «сохранение не сработало».

Второй, менее заметный слой — норма. Квота выходных была `min(2, дни)`, то есть
на четырёхдневном прогоне съедала половину периода: оператор со ставкой 1,0 мог
взять 2 рабочих дня (16 ч) вместо четырёх. На боевых числах 08.09.2026 это
значило 336 ч суммарной нормы против 438,5 ч выставленных лотов — раздать план
было физически некому. Квота стала долей от семидневки, на 7 днях она прежняя.
"""
import ast
import re
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
VIEW_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
VIEW_SOURCE = VIEW_PATH.read_text(encoding="utf-8")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)


def _method_source(name):
    database_class = next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(DATABASE_SOURCE, method)


def _norm_helpers(source_quota=None):
    """Настоящие функции нормы из database.py, поднятые без импорта модуля.

    `self` из сигнатуры убирается, обращение к соседнему методу заменяется на
    прямой вызов — так проверяется тот же код, что уходит в прод, а не его копия.
    """
    namespace = {}
    quota = source_quota if source_quota is not None else _method_source("_shift_auction_day_off_quota")
    exec(quota.replace("self, ", ""), namespace)
    exec(
        _method_source("_shift_auction_norm_workday_count")
        .replace("self, ", "")
        .replace("self._shift_auction_day_off_quota", "_shift_auction_day_off_quota"),
        namespace,
    )
    return namespace


class PartialPeriodReachesTheAuctionTests(unittest.TestCase):
    def test_period_list_does_not_require_a_full_week(self):
        source = _method_source("_get_shift_auction_available_periods_tx")
        self.assertNotIn(
            "p.date_to = p.date_from + 6",
            source,
            "план короче недели снова выпадет из списка периодов аукциона",
        )
        # Фильтр направления снимать было нельзя — он про другое.
        self.assertIn("COALESCE(p.direction_mode, 'line') = %s", source)
        self.assertIn("HAVING COUNT(s.id) > 0", source)

    def test_period_validation_checks_sanity_not_length(self):
        source = _method_source("_validate_shift_auction_period_tx")
        self.assertNotIn("AUCTION_PERIOD_NOT_WEEK", source)
        self.assertNotIn("timedelta(days=6)", source)
        self.assertIn("date_to < date_from", source)
        self.assertIn('raise ValueError("AUCTION_PERIOD_INVALID")', source)

    def test_new_error_code_has_a_russian_message(self):
        self.assertIn('"AUCTION_PERIOD_INVALID": (', BOT_SOURCE)
        self.assertNotIn("AUCTION_PERIOD_NOT_WEEK", BOT_SOURCE)


class DayOffQuotaScalesWithThePeriodTests(unittest.TestCase):
    def test_week_keeps_the_owners_numbers(self):
        """Семь дней не сдвинулись: 1,0 → 40 ч, 0,75 → 30 ч, 0,5 → 20 ч."""
        namespace = _norm_helpers()
        self.assertEqual(namespace["_shift_auction_day_off_quota"](7), 2)
        workdays = namespace["_shift_auction_norm_workday_count"](7, 0)
        self.assertEqual(workdays, 5)
        self.assertEqual(round(workdays * 8 * 1.0), 40)
        self.assertEqual(round(workdays * 8 * 0.75), 30)
        self.assertEqual(round(workdays * 8 * 0.5), 20)

    def test_shorter_periods_get_a_proportional_quota(self):
        namespace = _norm_helpers()
        quota = namespace["_shift_auction_day_off_quota"]
        self.assertEqual([quota(days) for days in range(0, 8)], [0, 0, 0, 0, 1, 1, 1, 2])

    def test_five_day_period_can_absorb_its_own_plan(self):
        """Боевой случай 08.09.2026: 438,5 ч лотов на 17 участников (сумма ставок 14).

        При прежней квоте норма давала 336 ч — сто часов смен остались бы
        невыбираемыми. Тест держит именно это соотношение, а не абстрактное число.
        """
        namespace = _norm_helpers()
        workdays = namespace["_shift_auction_norm_workday_count"](5, 0)
        self.assertEqual(workdays, 4)
        self.assertGreaterEqual(workdays * 8 * 14.0, 438.5)

    def test_the_old_quota_would_fail_this_test(self):
        """Сторож обязан краснеть: гоняем ту же норму на прежней квоте."""
        namespace = _norm_helpers(
            "def _shift_auction_day_off_quota(total_days):\n"
            "    return min(2, max(0, int(total_days or 0)))\n"
        )
        self.assertEqual(namespace["_shift_auction_norm_workday_count"](5, 0), 3)
        self.assertLess(3 * 8 * 14.0, 438.5)


class FrontendMirrorsTheQuotaTests(unittest.TestCase):
    """Экран считает норму сам — разойдясь с сервером, он обещает чужие часы."""

    def test_single_helper_feeds_both_the_norm_and_the_day_off_limit(self):
        self.assertIn("const getAuctionDayOffQuota = (periodDayCount) => {", VIEW_SOURCE)
        self.assertIn("Math.floor((totalDays * 2) / 7)", VIEW_SOURCE)
        self.assertIn("const dayOffQuota = getAuctionDayOffQuota(totalDays);", VIEW_SOURCE)
        self.assertIn(
            "const dayOffQuota = useMemo(() => getAuctionDayOffQuota(lotDates.length), [lotDates.length]);",
            VIEW_SOURCE,
        )
        self.assertNotIn("Math.min(2, totalDays)", VIEW_SOURCE)
        self.assertNotIn("Math.min(2, Math.max(0, lotDates.length))", VIEW_SOURCE)

    def test_js_and_python_quotas_agree_day_by_day(self):
        """Одна формула на двух языках — сверяем значениями, а не глазами."""
        match = re.search(
            r"const getAuctionDayOffQuota = \(periodDayCount\) => \{(.+?)\n\};",
            VIEW_SOURCE,
            re.S,
        )
        self.assertIsNotNone(match, "helper квоты переименовали — тест потерял адрес")
        self.assertIn("Math.floor((totalDays * 2) / 7)", match.group(1))
        python_quota = _norm_helpers()["_shift_auction_day_off_quota"]
        for days in range(0, 32):
            self.assertEqual(python_quota(days), (days * 2) // 7, f"дней: {days}")

    def test_day_off_caption_follows_the_quota(self):
        """Подпись «до 2 дней» была прибита числом — на пятидневке она врала."""
        self.assertNotIn("Можно выбрать до 2 дней периода", VIEW_SOURCE)
        self.assertIn("Можно выбрать до ${dayOffQuota} ${formatDayOffQuotaWord(dayOffQuota)}", VIEW_SOURCE)
        self.assertIn("Период короче четырёх дней — выходные в нём не выбираются.", VIEW_SOURCE)


class MissingPeriodIsNotSilentTests(unittest.TestCase):
    """Из планировщика в аукцион уходит период, а не номер плана.

    Не найдя его, раздел раньше просто оставался на прошлом — экран выглядел
    рабочим, и разобраться, что показанные смены чужие, было нельзя.
    """

    def test_unmatched_initial_period_notifies_the_user(self):
        start = VIEW_SOURCE.index("const initialPeriodKey =")
        end = VIEW_SOURCE.index("onInitialPeriodApplied, updateDraftSchedulePlanId]);", start)
        block = VIEW_SOURCE[start:end]
        self.assertIn("if (!matchedPeriod?.id) {", block)
        self.assertIn("notify?.(", block)
        self.assertIn("'error',", block)
        # notify — проп; без него в зависимостях эффект замкнётся на первом рендере.
        self.assertIn("notify,", VIEW_SOURCE[end - 200:end + 120])


if __name__ == "__main__":
    unittest.main()
