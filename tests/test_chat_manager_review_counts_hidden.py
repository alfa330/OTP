# -*- coding: utf-8 -*-
"""Задача #272: чат-менеджер не видит КОЛИЧЕСТВО чатов для проверки.

Требование заказчика (Сабыр Азана, СВ чат-направления): в личных разделах
чат-менеджера сами обращения и решения по ним остаются, а числа — сколько чатов
проверено, сколько ещё проверят, сколько низких оценок — не показываются.

Стережём три границы:
  1) бэкенд отдаёт признак модели (`is_chat_manager`) в ответе с оценками —
     без него фронт не отличит чат-менеджера от звонкового оператора;
  2) план проверок («Прослушано / нужно», «Осталось прослушать», раскрытый
     расчёт) скрыт по этому признаку и в «Моих оценках», и в «Профиле»,
     но у остальных моделей остаётся на месте;
  3) блок «Низкие оценки клиентов» больше не печатает ни сводку из четырёх
     чисел, ни количество оценок в шапке журнала — при этом список обращений,
     переписка и вердикты сохранены.
"""

import ast
import unittest
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"
MY_LOW_RATINGS_PATH = ROOT / "src" / "components" / "c2d_eval" / "MyLowRatings.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _bot_function_source(name):
    module = source_cache.parse(_read(BOT_PATH))
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.unparse(function)


class CallEvaluationsModelFlagTests(unittest.TestCase):
    """Ручка оценок сообщает, чат-менеджер это или звонковый оператор."""

    def setUp(self):
        self.source = _bot_function_source("get_call_evaluations")

    def test_response_carries_the_flag(self):
        self.assertIn("'is_chat_manager': is_chat_manager", self.source)

    def test_model_is_resolved_on_the_month_end(self):
        # Модель берём на конец выбранного месяца, а не «на сегодня»: перешедший
        # посреди месяца человек должен видеть раздел по своей модели за месяц.
        self.assertIn("get_operator_calculation_models_as_of", self.source)
        self.assertIn("calendar.monthrange", self.source)
        self.assertIn("== 'chat_manager'", self.source)

    def test_failure_keeps_the_old_behaviour(self):
        # Не смогли определить модель — ведём себя как раньше, а не прячем план
        # у всех подряд.
        self.assertIn("is_chat_manager = False", self.source)


class EvaluationPlanHiddenTests(unittest.TestCase):
    """«Мои оценки» и «Профиль»: план проверок скрыт только у чат-модели."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_flags_come_from_the_backend_field(self):
        self.assertIn(
            "const hidesEvalPlan = operatorData?.is_chat_manager === true;",
            self.src,
        )
        self.assertIn(
            "const profileHidesEvalPlan = operatorData?.is_chat_manager === true && !profileIsTezOp;",
            self.src,
        )

    def test_kpi_card_and_profile_tile_are_gated(self):
        self.assertIn("{!hidesEvalPlan && (", self.src)
        self.assertIn("{!profileHidesEvalPlan && (", self.src)

    def test_average_score_survives_without_the_plan(self):
        # Средний балл — не количество, его чат-менеджер видит по-прежнему.
        self.assertIn("label=\"Средний балл\"", self.src)
        self.assertIn(
            "hidesEvalPlan ? 'flex justify-center items-center' : 'col-span-1 flex justify-center items-center'",
            self.src,
        )

    def test_profile_grid_shrinks_to_three_tiles(self):
        # Иначе на месте убранной плитки осталась бы дырка в сетке из четырёх.
        self.assertIn("profileHidesEvalPlan ? 'sm:grid-cols-3' : 'sm:grid-cols-4'", self.src)

    def test_call_operators_keep_their_plan(self):
        self.assertIn("Прослушано / нужно", self.src)
        self.assertIn("Осталось прослушать ${remainingEvalCount}", self.src)


class LowRatingCountsHiddenTests(unittest.TestCase):
    """Блок «Низкие оценки клиентов»: обращения без счётчиков."""

    def setUp(self):
        self.src = _read(MY_LOW_RATINGS_PATH)

    def test_summary_tiles_are_gone(self):
        self.assertNotIn("<Stat ", self.src)
        self.assertNotIn("const Stat = ", self.src)
        for label in ('label="Всего"', 'label="Снято"', 'label="Обоснованно"'):
            self.assertNotIn(label, self.src)

    def test_journal_header_has_no_count(self):
        self.assertNotIn("оценок`", self.src)
        self.assertIn("${formatDay(data.start)} — ${formatDay(data.end)}`", self.src)

    def test_appeals_themselves_stay_available(self):
        # Задача требует убрать числа, а не доступ: список, переписка и вердикты
        # остаются, включая кнопку открытия полноэкранного просмотра.
        self.assertIn("Смотреть проверки", self.src)
        self.assertIn("rows.map((row)", self.src)
        self.assertIn("<ChatThread", self.src)
        self.assertIn("Решение по оценке", self.src)

    def test_empty_month_still_says_so(self):
        self.assertIn("За этот месяц низких оценок нет.", self.src)


if __name__ == "__main__":
    unittest.main()
