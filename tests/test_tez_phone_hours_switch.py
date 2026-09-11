"""Переход ТЭЗ на часы по событиям телефона iCORE Phone.

Переключатель защищает от разрушительной, а не от косметической ошибки: импорт
статусов делает REPLACE по затронутым дням (database.save_operator_status_import),
поэтому ночная выгрузка Binotel, оставленная включённой, не «сверяла» бы часы, а
затирала бы события телефона за день целиком. Тест фиксирует три вещи: за дни
после перехода импорт запрещён, дни до перехода остаются доступны для бэкфилла,
а непонятное значение env падает в безопасную сторону (импорт выключен).
"""

import ast
import logging
import os
import textwrap
import unittest
from datetime import date as dt_date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"


def _load_switch_namespace():
    """Вытащить константу и обе функции переключателя, не поднимая приложение."""
    source = BOT_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)

    wanted_functions = {"_tez_phone_hours_since", "_tez_status_import_allowed"}
    chunks = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TEZ_PHONE_HOURS_SINCE_DEFAULT"
            for t in node.targets
        ):
            chunks.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            chunks.append(textwrap.dedent(ast.get_source_segment(source, node)))

    namespace = {"os": os, "datetime": datetime, "dt_date": dt_date, "logging": logging}
    exec("\n\n".join(chunks), namespace)
    return namespace


class TezPhoneHoursSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _load_switch_namespace()

    def setUp(self):
        self._saved_env = os.environ.pop("TEZ_PHONE_HOURS_SINCE", None)

    def tearDown(self):
        os.environ.pop("TEZ_PHONE_HOURS_SINCE", None)
        if self._saved_env is not None:
            os.environ["TEZ_PHONE_HOURS_SINCE"] = self._saved_env

    def test_switch_namespace_exposes_both_helpers(self):
        self.assertIn("_tez_phone_hours_since", self.ns)
        self.assertIn("_tez_status_import_allowed", self.ns)

    def test_default_cutoff_is_a_real_date(self):
        self.assertIsInstance(self.ns["_tez_phone_hours_since"](), dt_date)

    def test_import_blocked_from_cutoff_day_onwards(self):
        os.environ["TEZ_PHONE_HOURS_SINCE"] = "2026-09-11"
        allowed = self.ns["_tez_status_import_allowed"]
        # Сам день перехода уже закрыт событиями телефона — выгрузка его затрёт.
        self.assertFalse(allowed(dt_date(2026, 9, 11)))
        self.assertFalse(allowed(dt_date(2026, 9, 12)))

    def test_backfill_before_cutoff_still_allowed(self):
        os.environ["TEZ_PHONE_HOURS_SINCE"] = "2026-09-11"
        allowed = self.ns["_tez_status_import_allowed"]
        # До перехода выгрузка — единственный источник, перезалив должен работать.
        self.assertTrue(allowed(dt_date(2026, 9, 10)))
        self.assertTrue(allowed(dt_date(2026, 1, 1)))

    def test_rollback_via_env_reenables_import(self):
        allowed = self.ns["_tez_status_import_allowed"]
        # Откат без выката кода: «off» или дата в будущем.
        os.environ["TEZ_PHONE_HOURS_SINCE"] = "off"
        self.assertTrue(allowed(dt_date(2026, 9, 12)))
        os.environ["TEZ_PHONE_HOURS_SINCE"] = "2026-12-01"
        self.assertTrue(allowed(dt_date(2026, 9, 12)))

    def test_unparsable_env_fails_safe(self):
        # Опечатка не должна вернуть выгрузке роль источника часов: разрушительная
        # сторона здесь — импорт, поэтому он остаётся выключенным для любого дня.
        os.environ["TEZ_PHONE_HOURS_SINCE"] = "11.09.2026"
        allowed = self.ns["_tez_status_import_allowed"]
        with self.assertLogs(level="WARNING"):
            self.assertFalse(allowed(dt_date(2020, 1, 1)))


if __name__ == "__main__":
    unittest.main()
