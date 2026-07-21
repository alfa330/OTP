# -*- coding: utf-8 -*-
"""Правила начисления успешек TEZ ОП и нормализация телефонов.

Логика вынесена в отдельный модуль без БД и сети, поэтому проверяется целиком.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tez_op_leads import (  # noqa: E402
    ALMATY_TZ,
    RULE_PREV_MONTH_FIRST_WEEK,
    RULE_SAME_MONTH,
    STATUS_ALREADY_WORKING,
    STATUS_IN_PROGRESS,
    STATUS_NEW,
    STATUS_NOT_COUNTED,
    STATUS_SUCCESS,
    compute_lead_outcome,
    normalize_kz_phone,
    parse_first_order_at,
    to_e164,
)


def dt(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ALMATY_TZ)


def call(started_at, operator_id=1, billsec=42, call_type=1, gid="g1"):
    return {
        "general_call_id": gid,
        "started_at": started_at,
        "operator_id": operator_id,
        "billsec": billsec,
        "call_type": call_type,
    }


class NormalizePhoneTests(unittest.TestCase):
    def test_canonical_forms(self):
        """Все ходовые формы записи схлопываются в 11 цифр '77...'."""
        for raw in [
            "77023227108",
            "+77023227108",
            "8 702 322 71 08",
            "+7 (702) 322-71-08",
            "7023227108",
            " 77023227108 ",
        ]:
            self.assertEqual(normalize_kz_phone(raw), "77023227108", raw)

    def test_sources_agree(self):
        """Три системы пишут номер по-разному — ключ должен совпасть."""
        from_leads = normalize_kz_phone("77769167987")      # выгрузка СВ
        from_binotel = normalize_kz_phone("77769167987")    # externalNumber
        from_tezapp = normalize_kz_phone("+77769167987")    # TEZ APP
        self.assertEqual(from_leads, from_binotel)
        self.assertEqual(from_binotel, from_tezapp)

    def test_invalid(self):
        for raw in [None, "", "   ", "abc", "123", "7701234", "9971234567890", "0443334023"]:
            self.assertIsNone(normalize_kz_phone(raw), raw)

    def test_to_e164(self):
        self.assertEqual(to_e164("77023227108"), "+77023227108")


class ParseFirstOrderTests(unittest.TestCase):
    def test_iso_with_offset(self):
        parsed = parse_first_order_at("2026-03-14T10:35:21+05:00")
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 3, 14))
        self.assertEqual(parsed.hour, 10)

    def test_null_and_garbage(self):
        self.assertIsNone(parse_first_order_at(None))
        self.assertIsNone(parse_first_order_at(""))
        self.assertIsNone(parse_first_order_at("не дата"))


class LeadOutcomeTests(unittest.TestCase):
    def test_new_lead(self):
        out = compute_lead_outcome(None, [])
        self.assertEqual(out["status"], STATUS_NEW)

    def test_in_progress(self):
        out = compute_lead_outcome(None, [call(dt(2026, 6, 3))])
        self.assertEqual(out["status"], STATUS_IN_PROGRESS)

    def test_already_working_without_calls(self):
        """Выехал сам — успешки нет, оператора нет."""
        out = compute_lead_outcome(dt(2026, 6, 10), [])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)
        self.assertIsNone(out["operator_id"])

    def test_call_after_trip_is_not_a_success(self):
        """Позвонили уже работающему водителю — это не привлечение."""
        out = compute_lead_outcome(dt(2026, 6, 10), [call(dt(2026, 6, 11))])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)

    def test_success_same_month(self):
        out = compute_lead_outcome(dt(2026, 6, 20), [call(dt(2026, 6, 3), operator_id=77)])
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_SAME_MONTH)
        self.assertEqual(out["operator_id"], 77)

    def test_success_date_is_trip_day(self):
        """Дата успешки = день поездки, а не день звонка и не день обнаружения."""
        out = compute_lead_outcome(dt(2026, 6, 20, 23, 50), [call(dt(2026, 6, 3))])
        self.assertEqual(out["success_date"].isoformat(), "2026-06-20")

    def test_prev_month_call_trip_within_first_week(self):
        out = compute_lead_outcome(dt(2026, 7, 7, 23, 59), [call(dt(2026, 6, 25), operator_id=5)])
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_PREV_MONTH_FIRST_WEEK)
        self.assertEqual(out["operator_id"], 5)

    def test_prev_month_call_trip_after_day7(self):
        """Оператор работал, но не уложился в окно — отдельный статус, не «уже работающий»."""
        out = compute_lead_outcome(dt(2026, 7, 8), [call(dt(2026, 6, 25))])
        self.assertEqual(out["status"], STATUS_NOT_COUNTED)
        self.assertIsNone(out["operator_id"])

    def test_last_touch_attribution(self):
        """Из нескольких дозвонившихся успешка достаётся последнему перед поездкой."""
        calls = [
            call(dt(2026, 6, 1), operator_id=1, gid="a"),
            call(dt(2026, 6, 15), operator_id=2, gid="b"),
            call(dt(2026, 6, 25), operator_id=3, gid="c"),   # уже после поездки
        ]
        out = compute_lead_outcome(dt(2026, 6, 20), calls)
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["operator_id"], 2)
        self.assertEqual(out["call"]["general_call_id"], "b")

    def test_billsec_threshold(self):
        """Порог 10 секунд: 9 не считается, 10 считается."""
        short = compute_lead_outcome(dt(2026, 6, 20), [call(dt(2026, 6, 3), billsec=9)])
        self.assertEqual(short["status"], STATUS_ALREADY_WORKING)
        exact = compute_lead_outcome(dt(2026, 6, 20), [call(dt(2026, 6, 3), billsec=10)])
        self.assertEqual(exact["status"], STATUS_SUCCESS)

    def test_threshold_is_configurable(self):
        """Порог — настройка: пересчёт под другое значение не требует похода в Binotel."""
        calls = [call(dt(2026, 6, 3), billsec=7)]
        self.assertEqual(compute_lead_outcome(dt(2026, 6, 20), calls)["status"],
                         STATUS_ALREADY_WORKING)
        self.assertEqual(compute_lead_outcome(dt(2026, 6, 20), calls, min_billsec=5)["status"],
                         STATUS_SUCCESS)

    def test_incoming_call_does_not_qualify(self):
        out = compute_lead_outcome(dt(2026, 6, 20), [call(dt(2026, 6, 3), call_type=0)])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)

    def test_call_from_non_op_employee_is_ignored(self):
        """Звонок ТП/линии (operator_id не разрезолвен в ОП) успешку не даёт."""
        out = compute_lead_outcome(dt(2026, 6, 20), [call(dt(2026, 6, 3), operator_id=None)])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)

    def test_non_op_call_does_not_steal_from_op(self):
        """Более поздний звонок не-ОП не должен перехватывать успешку у оператора ОП."""
        calls = [
            call(dt(2026, 6, 3), operator_id=42, gid="op"),
            call(dt(2026, 6, 18), operator_id=None, gid="tp"),
        ]
        out = compute_lead_outcome(dt(2026, 6, 20), calls)
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["operator_id"], 42)

    def test_one_lead_gives_at_most_one_success(self):
        """Первая поездка одна, поэтому повторный расчёт даёт тот же результат."""
        calls = [call(dt(2026, 6, 3), operator_id=9)]
        first = compute_lead_outcome(dt(2026, 6, 20), calls)
        second = compute_lead_outcome(dt(2026, 6, 20), calls)
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["success_date"], second["success_date"])
        self.assertEqual(first["operator_id"], second["operator_id"])

    def test_naive_datetimes_treated_as_almaty(self):
        """Наивное время не должно ломать сравнение «звонок до поездки»."""
        out = compute_lead_outcome(datetime(2026, 6, 20, 12, 0),
                                   [{"started_at": datetime(2026, 6, 20, 11, 0),
                                     "operator_id": 1, "billsec": 30, "call_type": 1}])
        self.assertEqual(out["status"], STATUS_SUCCESS)

    def test_real_case_from_production_sample(self):
        """Боевой кейс из сверки: звонок 30.01.2025, поездка 06.02.2025 -> успешка."""
        out = compute_lead_outcome(
            parse_first_order_at("2025-02-06T18:16:26.055919+05:00"),
            [call(datetime(2025, 1, 30, 20, 19, tzinfo=ALMATY_TZ), operator_id=101)],
        )
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_PREV_MONTH_FIRST_WEEK)
        self.assertEqual(out["success_date"].isoformat(), "2025-02-06")

    def test_real_case_rejected_by_seven_day_rule(self):
        """Боевой кейс: звонок в мае, поездка 17 июня -> не засчитано."""
        out = compute_lead_outcome(
            parse_first_order_at("2026-06-17T22:02:40.243247+05:00"),
            [call(datetime(2026, 5, 20, 10, 0, tzinfo=ALMATY_TZ), operator_id=101)],
        )
        self.assertEqual(out["status"], STATUS_NOT_COUNTED)


class SchemaTests(unittest.TestCase):
    """Схема объявлена в database.py — сторожим ключевые инварианты."""

    @classmethod
    def setUpClass(cls):
        cls.ddl = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_tables_declared(self):
        for table in [
            "tez_drivers",
            "tez_leads",
            "tez_lead_batches",
            "tez_lead_batch_rows",
            "tez_lead_calls",
            "tez_lead_successes",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.ddl, table)

    def test_lead_is_unique_per_month(self):
        """База помесячная: один номер может быть и в июньской, и в июльской."""
        self.assertIn("UNIQUE(year, month, phone_norm)", self.ddl)

    def test_one_success_per_lead(self):
        """UNIQUE на lead_id закрепляет «один лид = максимум одна успешка»."""
        self.assertRegex(self.ddl, r"lead_id UUID NOT NULL UNIQUE REFERENCES tez_leads\(id\)")

    def test_statuses_match_module(self):
        """CHECK в схеме и константы модуля не должны разъезжаться."""
        for status in [STATUS_NEW, STATUS_IN_PROGRESS, STATUS_ALREADY_WORKING,
                       STATUS_SUCCESS, STATUS_NOT_COUNTED]:
            self.assertIn(f"'{status}'", self.ddl, status)


class GroupFilterTests(unittest.TestCase):
    """Сужение успешек по группе оператора привязано к дате поездки."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_group_filter_uses_membership_interval_on_success_date(self):
        """Группа берётся из членства, активного в день успешки (не «текущая» группа)."""
        self.assertIn("_TEZ_GROUP_FILTER_SQL", self.src)
        self.assertIn("group_operator_memberships gom", self.src)
        self.assertRegex(
            self.src,
            r"s\.success_date >= gom\.start_date\s*\n\s*AND \(gom\.end_date IS NULL OR s\.success_date <= gom\.end_date\)",
        )

    def test_operator_and_day_views_accept_group(self):
        """И рейтинг операторов, и разбивка по дням принимают group_id."""
        self.assertIn("def get_tez_operator_successes(self, year, month, group_id=None)", self.src)
        self.assertIn("def get_tez_successes_by_day(self, year, month, group_id=None)", self.src)

    def test_operator_day_view_exists_and_group_aware(self):
        """Таб «Успешки»: агрегат оператор→день, месяц по дате поездки, с группой."""
        self.assertIn("def get_tez_successes_operator_day(self, year, month, group_id=None)", self.src)
        self.assertIn("EXTRACT(DAY FROM s.success_date)", self.src)


if __name__ == "__main__":
    unittest.main()
