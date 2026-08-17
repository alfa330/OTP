# -*- coding: utf-8 -*-
"""Раздел «Зарплата» по отделам.

Админ переключает отделы и видит все готовые расчёты, СВ и глава отдела —
только модели своего отдела, оператор — расчёт своего направления.
Вкладки и список «готовых» отделов строятся из одного каталога: пока это были
разные литералы, добавленная модель попадала в один список и молча выпадала
из другого.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "App.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


class SalaryCatalogTests(unittest.TestCase):
    """Каталог «отдел → модели» — единственный источник правды."""

    def setUp(self):
        self.src = _read(APP_PATH)
        start = self.src.index("const SALARY_CALCULATOR_CATALOG = [")
        self.catalog = self.src[start:self.src.index("\n];", start)]

    def test_catalog_covers_every_department_with_a_calculator(self):
        self.assertEqual(
            re.findall(r"code: '([a-z_]+)',", self.catalog),
            ["szov", "tez", "op"],
        )
        self.assertEqual(
            re.findall(r"key: '([a-z_]+)'", self.catalog),
            ["call", "chat", "converter", "tez_line", "tez_op", "op_osnova", "op_potok"],
        )

    def test_ready_departments_and_types_are_derived(self):
        # Иначе новая модель попадает в каталог, но не переживает нормализацию
        # сохранённой вкладки (normalizeSalaryCalculatorType сбросит её в 'call').
        self.assertIn(
            "const SALARY_CALCULATOR_TYPES = new Set(\n"
            "    SALARY_CALCULATOR_CATALOG.flatMap((entry) => entry.models.map((model) => model.key))\n"
            ");",
            self.src,
        )
        self.assertIn(
            "const SALARY_CALCULATOR_READY_DEPARTMENT_CODES = new Set(SALARY_CALCULATOR_CATALOG.map((entry) => entry.code));",
            self.src,
        )


class SalaryDepartmentScopeTests(unittest.TestCase):
    """Кому какой набор вкладок достаётся."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_only_global_admin_picks_department(self):
        # isAdminLikeRole в App.jsx уже исключает главу отдела (scoped head).
        self.assertIn("const canPickSalaryDepartment = isAdminLikeRole;", self.src)
        self.assertIn(
            "const salaryDeptOptions = canPickSalaryDepartment\n"
            "                ? SALARY_CALCULATOR_CATALOG\n"
            "                : (activeSalaryCatalogEntry ? [activeSalaryCatalogEntry] : []);",
            self.src,
        )
        # Строка отделов рендерится, только когда вариантов больше одного, —
        # у СВ и главы отдела она исчезает сама.
        self.assertIn("{salaryDeptOptions.length > 1 && (", self.src)

    def test_supervisor_and_head_get_their_own_department(self):
        self.assertIn(
            "const activeSalaryDeptCode = canPickSalaryDepartment",
            self.src,
        )
        self.assertIn(": ownSalaryDeptCode;", self.src)
        self.assertIn("const salaryModelOptions = activeSalaryCatalogEntry?.models || [];", self.src)

    def test_admin_never_sees_the_stub(self):
        self.assertIn(
            "const salaryStubShown = !canPickSalaryDepartment\n"
            "                && (!hasSalaryCalculatorForDepartment || (isOpSalaryDept && !showOpCalculator));",
            self.src,
        )

    def test_switching_department_moves_the_model_tab(self):
        # Вкладка могла остаться от прошлого отдела: без перевода админ увидел бы
        # чужой калькулятор (или Конвертер по остаточной ветке).
        start = self.src.index("const pickSalaryDepartment = (code) => {")
        block = self.src[start:self.src.index("\n            };", start)]
        self.assertIn("if (!entry.models.some((model) => model.key === calculatorType)) {", block)
        self.assertIn("setCalculatorType(entry.models[0].key);", block)
        self.assertIn("localStorage.setItem('salaryDeptCode', entry.code);", block)

    def test_active_model_falls_back_to_first_of_department(self):
        start = self.src.index("const activeSalaryModel = activeSalaryDeptCode === 'op'")
        block = self.src[start:self.src.index(";", self.src.index("salaryModelOptions[0]?.key", start))]
        self.assertIn("? opSalaryModel", block)
        self.assertIn("activeSalaryDeptCode === 'tez'", block)
        self.assertIn("? tezSalaryModel", block)
        self.assertIn("salaryModelOptions.some((model) => model.key === calculatorType)", block)
        self.assertIn("salaryModelOptions[0]?.key", block)


class SalarySectionRenderTests(unittest.TestCase):
    """Вкладки и тело раздела идут от активного отдела, а не от отдела юзера."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_model_tabs_are_rendered_from_the_catalog(self):
        self.assertIn("{showSalaryModelTabs && (", self.src)
        self.assertIn("{salaryModelOptions.map((model) => (", self.src)
        self.assertIn("${activeSalaryModel === model.key ? 'bg-blue-500 text-white'", self.src)

    def test_body_switches_by_active_department(self):
        self.assertIn("{activeSalaryDeptCode === 'op' ? (", self.src)
        self.assertIn(") : activeSalaryDeptCode === 'tez' ? (", self.src)
        # Ветки СЗоВ обязаны смотреть на активную модель: со вкладкой другого
        # отдела (например 'tez_op') старое условие проваливалось в «Конвертер».
        self.assertIn(") : activeSalaryModel === 'call' ? (", self.src)
        self.assertIn(") : activeSalaryModel === 'chat' ?(", self.src)
        self.assertNotIn("calculatorType === 'call' ? (", self.src)


if __name__ == "__main__":
    unittest.main()
