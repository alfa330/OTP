# -*- coding: utf-8 -*-
"""Сотрудник не видит, сколько его звонков и чатов должны проверить.

Задача #272 (Сабыр Азана) начиналась с чат-менеджеров, но владелец 03.09.2026
распространил правило на ВСЕ рядовые аккаунты: план проверок — рабочая мерка
проверяющих, а не показатель сотрудника. Поэтому в личных разделах чисел про
проверку нет ни у кого, а не по признаку модели.

Стережём три границы:
  1) карточки «Прослушано / нужно» (с остатком, общим числом оценок и раскрытым
     расчётом нормы) нет ни в «Мои оценки», ни в плитках «Профиля»;
  2) вместе с ней ушли и вычисления плана — иначе остался бы мёртвый код,
     который следующая правка снова выведет на экран;
  3) блок «Низкие оценки клиентов» (он есть только у чат-менеджеров) не печатает
     ни сводку из четырёх чисел, ни количество оценок в шапке журнала — при этом
     список обращений, переписка и вердикты сохранены.

Что НЕ должно пострадать: средний балл, успешки и план ОП TEZ (это их рабочие
мерки, не проверки) и счётчики проверяющей стороны в «Учёте часов».
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "App.jsx"
MY_LOW_RATINGS_PATH = ROOT / "src" / "components" / "c2d_eval" / "MyLowRatings.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _region(src, start_marker, end_marker):
    """Кусок App.jsx между маркерами — файл на 53 тысячи строк, и проверять
    «строки нет вообще» нельзя: те же слова и переменные законно живут на
    проверяющей стороне (getEvaluationPlanMeta и экран СВ)."""
    _, found, tail = src.partition(start_marker)
    assert found, f"не найден маркер начала: {start_marker}"
    block, found_end, _ = tail.partition(end_marker)
    assert found_end, f"не найден маркер конца: {end_marker}"
    return block


class EvaluationPlanIsGoneTests(unittest.TestCase):
    """Личные разделы: ни одного числа про план проверок."""

    def setUp(self):
        self.src = _read(APP_PATH)
        # «Ваши оценки» (личный раздел) и плитки «Профиля».
        self.own_screens = (
            _region(self.src, "{view === 'evaluation' && (", "view === 'salary' && (")
            + _region(self.src, "const profileIsTezOp", "Быстрые действия")
        )

    def test_plan_labels_are_gone(self):
        # Этих подписей у сотрудника не должно быть НИГДЕ: на экране СВ свои.
        for text in (
            "Прослушано / нужно",
            "Осталось прослушать",
            "Норма прослушки выполнена",
            "Всего оценок (включая не оцененные)",
        ):
            self.assertNotIn(text, self.src, f"на экране осталось «{text}»")
        # А раскрытый расчёт нормы — только в личных разделах: у проверяющих он
        # остаётся (formatEvaluationPlanFormula).
        self.assertNotIn("ч полной ставки) x ", self.own_screens)

    def test_hours_norm_labels_are_untouched(self):
        # «Норма выполнена» в «Моих часах» — про ОТРАБОТАННЫЕ ЧАСЫ, а не про
        # проверки: под запрет выше она не попадает и остаётся на месте.
        self.assertIn("label: 'Норма выполнена'", self.src)
        self.assertIn(">Норма<", self.src)

    def test_plan_calculations_are_gone(self):
        # Мёртвые вычисления опаснее мёртвой разметки: их легко снова вывести.
        for name in (
            "const targetEvalCount",
            "const remainingEvalCount",
            "const evaluationCount",
            "const evalCount",
            "const hasCalculationDetails",
            "const requiredCallsRaw",
            "const baseCallTarget",
            "const targetNormHours",
        ):
            self.assertNotIn(name, self.own_screens, f"осталось вычисление «{name}»")

    def test_reviewer_plan_helper_survives(self):
        # Плитка «Прослушано / нужно» у СВ по каждому оператору — рабочая, её
        # источник не тронут.
        self.assertIn("const getEvaluationPlanMeta = (operatorRow) => {", self.src)
        self.assertIn("const formatEvaluationPlanFormula = (operatorRow) => {", self.src)

    def test_average_score_survives(self):
        self.assertIn('label="Средний балл"', self.src)
        self.assertIn("Ср. балл", self.src)
        self.assertIn("const averageScore =", self.src)
        self.assertIn("const avgScore = evalsFiltered.length > 0", self.src)

    def test_tez_op_plan_tiles_survive(self):
        # У ОП TEZ успешки и выполнение плана — их работа, а не проверка качества.
        self.assertIn("Успешки / план", self.src)
        self.assertIn("Выполнение плана", self.src)
        self.assertIn("profileIsTezOp ? 'sm:grid-cols-4' : 'sm:grid-cols-3'", self.src)

    def test_reviewer_side_counters_survive(self):
        # Проверяющим количества нужны: бейдж раздела и числа в фильтрах.
        self.assertIn("Низкие оценки{lowRatingAttentionCount ?", self.src)
        self.assertIn("const lowRatingAttentionCount = lowRatingFilterCount('attention');", self.src)


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
        # Задача требует убрать числа, а не доступ.
        self.assertIn("Смотреть проверки", self.src)
        self.assertIn("rows.map((row)", self.src)
        self.assertIn("<ChatThread", self.src)
        self.assertIn("Решение по оценке", self.src)

    def test_empty_month_still_says_so(self):
        self.assertIn("За этот месяц низких оценок нет.", self.src)


if __name__ == "__main__":
    unittest.main()
