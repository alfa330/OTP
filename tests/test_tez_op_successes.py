# -*- coding: utf-8 -*-
"""Правила начисления успешек TEZ ОП и нормализация телефонов.

Логика вынесена в отдельный модуль без БД и сети, поэтому проверяется целиком.
"""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tez_op_leads import (  # noqa: E402
    ALMATY_TZ,
    call_window_for_period,
    REASON_ACTIVE_PREV_MONTH,
    REASON_CALL_BEFORE_WINDOW,
    RULE_PREV_MONTH_LAST_DAYS,
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
            "77000000107",
            "+77000000107",
            "8 700 000 01 07",
            "+7 (700) 000-01-07",
            "7000000107",
            " 77000000107 ",
        ]:
            self.assertEqual(normalize_kz_phone(raw), "77000000107", raw)

    def test_sources_agree(self):
        """Три системы пишут номер по-разному — ключ должен совпасть."""
        from_leads = normalize_kz_phone("77000000113")      # выгрузка СВ
        from_binotel = normalize_kz_phone("77000000113")    # externalNumber
        from_tezapp = normalize_kz_phone("+77000000113")    # TEZ APP
        self.assertEqual(from_leads, from_binotel)
        self.assertEqual(from_binotel, from_tezapp)

    def test_invalid(self):
        for raw in [None, "", "   ", "abc", "123", "7701234", "9971234567890", "0443334023"]:
            self.assertIsNone(normalize_kz_phone(raw), raw)

    def test_to_e164(self):
        self.assertEqual(to_e164("77000000107"), "+77000000107")


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
        out = compute_lead_outcome(None, None, [])
        self.assertEqual(out["status"], STATUS_NEW)

    def test_in_progress(self):
        out = compute_lead_outcome(None, None, [call(dt(2026, 6, 3))])
        self.assertEqual(out["status"], STATUS_IN_PROGRESS)

    def test_already_working_without_calls(self):
        """Выехал сам — успешки нет, оператора нет."""
        out = compute_lead_outcome(dt(2026, 6, 10), None, [])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)
        self.assertIsNone(out["operator_id"])

    def test_call_after_trip_is_not_a_success(self):
        """Позвонили уже работающему водителю — это не привлечение."""
        out = compute_lead_outcome(dt(2026, 6, 10), None, [call(dt(2026, 6, 11))])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)

    def test_success_same_month(self):
        out = compute_lead_outcome(dt(2026, 6, 20), None, [call(dt(2026, 6, 3), operator_id=77)])
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_SAME_MONTH)
        self.assertEqual(out["operator_id"], 77)

    def test_success_date_is_trip_day(self):
        """Дата успешки = день поездки, а не день звонка и не день обнаружения."""
        out = compute_lead_outcome(dt(2026, 6, 20, 23, 50), None, [call(dt(2026, 6, 3))])
        self.assertEqual(out["success_date"].isoformat(), "2026-06-20")

    def test_prev_month_call_in_last_seven_days(self):
        """Звонок 25 июня (последние 7 дней месяца) + поездка 7 июля -> успешка."""
        out = compute_lead_outcome(dt(2026, 7, 7, 23, 59), None, [call(dt(2026, 6, 25), operator_id=5)])
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_PREV_MONTH_LAST_DAYS)
        self.assertEqual(out["operator_id"], 5)

    def test_prev_month_call_counts_for_any_trip_day(self):
        """Главное в новом правиле (владелец, 2026-08-04): день поездки внутри
        отчётного месяца больше не ограничен — важен только звонок."""
        for trip in [dt(2026, 7, 8), dt(2026, 7, 17), dt(2026, 7, 31, 23, 59)]:
            out = compute_lead_outcome(trip, None, [call(dt(2026, 6, 25), operator_id=5)])
            self.assertEqual(out["status"], STATUS_SUCCESS, trip)
            self.assertEqual(out["rule"], RULE_PREV_MONTH_LAST_DAYS, trip)
            self.assertEqual(out["operator_id"], 5, trip)
            self.assertEqual(out["success_date"], trip.date(), trip)

    def test_prev_month_window_edges_follow_month_length(self):
        """Окно считается от конца месяца: в июне (30 дней) это 24–30 число."""
        inside = compute_lead_outcome(dt(2026, 7, 20), None, [call(dt(2026, 6, 24), operator_id=5)])
        self.assertEqual(inside["status"], STATUS_SUCCESS)
        self.assertEqual(inside["rule"], RULE_PREV_MONTH_LAST_DAYS)
        outside = compute_lead_outcome(dt(2026, 7, 20), None, [call(dt(2026, 6, 23), operator_id=5)])
        self.assertEqual(outside["status"], STATUS_NOT_COUNTED)
        # Февраль короче: окно начинается 22-го, а не 24-го.
        feb = compute_lead_outcome(dt(2026, 3, 20), None, [call(dt(2026, 2, 22), operator_id=5)])
        self.assertEqual(feb["status"], STATUS_SUCCESS)
        feb_early = compute_lead_outcome(dt(2026, 3, 20), None, [call(dt(2026, 2, 21), operator_id=5)])
        self.assertEqual(feb_early["status"], STATUS_NOT_COUNTED)

    def test_prev_month_window_across_year_boundary(self):
        """Декабрь -> январь: «прошлый месяц» не должен потеряться на смене года."""
        out = compute_lead_outcome(dt(2027, 1, 15), None, [call(dt(2026, 12, 28), operator_id=5)])
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_PREV_MONTH_LAST_DAYS)

    def test_prev_month_call_before_window(self):
        """Звонок в прошлом месяце, но раньше последних 7 дней — отдельный статус,
        не «уже работающий»: именно такие случаи операторы оспаривают."""
        out = compute_lead_outcome(dt(2026, 7, 3), None, [call(dt(2026, 6, 10))])
        self.assertEqual(out["status"], STATUS_NOT_COUNTED)
        self.assertEqual(out["rule"], REASON_CALL_BEFORE_WINDOW)
        self.assertIsNone(out["operator_id"])

    def test_call_two_months_before_trip_is_not_counted(self):
        """Окно только на прошлый месяц: конец мая при поездке в июле не считается."""
        out = compute_lead_outcome(dt(2026, 7, 3), None, [call(dt(2026, 5, 30))])
        self.assertEqual(out["status"], STATUS_NOT_COUNTED)
        self.assertEqual(out["rule"], REASON_CALL_BEFORE_WINDOW)

    def test_last_touch_attribution(self):
        """Из нескольких дозвонившихся успешка достаётся последнему перед поездкой."""
        calls = [
            call(dt(2026, 6, 1), operator_id=1, gid="a"),
            call(dt(2026, 6, 15), operator_id=2, gid="b"),
            call(dt(2026, 6, 25), operator_id=3, gid="c"),   # уже после поездки
        ]
        out = compute_lead_outcome(dt(2026, 6, 20), None, calls)
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["operator_id"], 2)
        self.assertEqual(out["call"]["general_call_id"], "b")

    def test_billsec_threshold(self):
        """Порог 10 секунд: 9 не считается, 10 считается."""
        short = compute_lead_outcome(dt(2026, 6, 20), None, [call(dt(2026, 6, 3), billsec=9)])
        self.assertEqual(short["status"], STATUS_ALREADY_WORKING)
        exact = compute_lead_outcome(dt(2026, 6, 20), None, [call(dt(2026, 6, 3), billsec=10)])
        self.assertEqual(exact["status"], STATUS_SUCCESS)

    def test_threshold_is_configurable(self):
        """Порог — настройка: пересчёт под другое значение не требует похода в Binotel."""
        calls = [call(dt(2026, 6, 3), billsec=7)]
        self.assertEqual(compute_lead_outcome(dt(2026, 6, 20), None, calls)["status"],
                         STATUS_ALREADY_WORKING)
        self.assertEqual(compute_lead_outcome(dt(2026, 6, 20), None, calls, min_billsec=5)["status"],
                         STATUS_SUCCESS)

    def test_incoming_call_does_not_qualify(self):
        out = compute_lead_outcome(dt(2026, 6, 20), None, [call(dt(2026, 6, 3), call_type=0)])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)

    def test_call_from_non_op_employee_is_ignored(self):
        """Звонок ТП/линии (operator_id не разрезолвен в ОП) успешку не даёт."""
        out = compute_lead_outcome(dt(2026, 6, 20), None, [call(dt(2026, 6, 3), operator_id=None)])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)

    def test_non_op_call_does_not_steal_from_op(self):
        """Более поздний звонок не-ОП не должен перехватывать успешку у оператора ОП."""
        calls = [
            call(dt(2026, 6, 3), operator_id=42, gid="op"),
            call(dt(2026, 6, 18), operator_id=None, gid="tp"),
        ]
        out = compute_lead_outcome(dt(2026, 6, 20), None, calls)
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["operator_id"], 42)

    def test_walks_back_through_non_op_calls_until_op_found(self):
        """Если последние звонки не от ОП — идём назад, пока не найдём звонок ОП,
        и успешку получает он (владелец, 2026-07-22)."""
        calls = [
            call(dt(2026, 6, 2), operator_id=7, gid="op-old"),    # ОП, ранний
            call(dt(2026, 6, 10), operator_id=11, gid="op-new"),  # ОП, поздний -> ему
            call(dt(2026, 6, 15), operator_id=None, gid="tp1"),   # не ОП
            call(dt(2026, 6, 18), operator_id=None, gid="tp2"),   # не ОП
        ]
        out = compute_lead_outcome(dt(2026, 6, 20), None, calls)
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["operator_id"], 11)
        self.assertEqual(out["call"]["general_call_id"], "op-new")

    def test_no_op_call_at_all_gives_no_success(self):
        """Если ОП-звонков нет вовсе, откатываться не к кому — успешки нет."""
        calls = [
            call(dt(2026, 6, 10), operator_id=None, gid="tp1"),
            call(dt(2026, 6, 18), operator_id=None, gid="tp2"),
        ]
        out = compute_lead_outcome(dt(2026, 6, 20), None, calls)
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)
        self.assertIsNone(out["operator_id"])


class PrevMonthWindowTests(unittest.TestCase):
    """Новая оконная логика: заказ в прошлом месяце снимает успешку."""

    def test_active_prev_month_blocks_success(self):
        """Были заказы в прошлом месяце -> водитель уже работал, успешки нет,
        даже если в этом месяце есть заказ и хороший звонок до него."""
        current_trip = dt(2026, 7, 3, 22, 47)
        previous_trip = dt(2026, 6, 30, 23, 27)
        out = compute_lead_outcome(
            current_trip,
            previous_trip,
            [call(dt(2026, 7, 2, 13, 58), operator_id=5)],
        )
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)
        self.assertEqual(out["rule"], REASON_ACTIVE_PREV_MONTH)
        self.assertIsNone(out["operator_id"])
        # active_prev_month объясняется прошлой поездкой, но не
        # должен стирать отдельную поездку отчётного месяца.
        self.assertEqual(out["first_order_at"], current_trip)

    def test_clean_prev_month_allows_success(self):
        """Тот же случай, но в прошлом месяце заказов не было -> успешка."""
        out = compute_lead_outcome(
            dt(2026, 7, 20), None, [call(dt(2026, 7, 3), operator_id=5)]
        )
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["operator_id"], 5)

    def test_prev_month_checked_before_trip_presence(self):
        """Заказ в прошлом месяце при отсутствии заказа в текущем — тоже «уже работающий»."""
        out = compute_lead_outcome(None, dt(2026, 6, 15), [call(dt(2026, 7, 3), operator_id=5)])
        self.assertEqual(out["status"], STATUS_ALREADY_WORKING)
        self.assertEqual(out["rule"], REASON_ACTIVE_PREV_MONTH)

    def test_one_lead_gives_at_most_one_success(self):
        """Первая поездка одна, поэтому повторный расчёт даёт тот же результат."""
        calls = [call(dt(2026, 6, 3), operator_id=9)]
        first = compute_lead_outcome(dt(2026, 6, 20), None, calls)
        second = compute_lead_outcome(dt(2026, 6, 20), None, calls)
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["success_date"], second["success_date"])
        self.assertEqual(first["operator_id"], second["operator_id"])

    def test_naive_datetimes_treated_as_almaty(self):
        """Наивное время не должно ломать сравнение «звонок до поездки»."""
        out = compute_lead_outcome(datetime(2026, 6, 20, 12, 0), None,
                                   [{"started_at": datetime(2026, 6, 20, 11, 0),
                                     "operator_id": 1, "billsec": 30, "call_type": 1}])
        self.assertEqual(out["status"], STATUS_SUCCESS)

    def test_real_case_from_production_sample(self):
        """Боевой кейс из сверки: звонок 30.01.2025, поездка 06.02.2025 -> успешка."""
        out = compute_lead_outcome(
            parse_first_order_at("2025-02-06T18:16:26.055919+05:00"), None,
            [call(datetime(2025, 1, 30, 20, 19, tzinfo=ALMATY_TZ), operator_id=101)],
        )
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_PREV_MONTH_LAST_DAYS)
        self.assertEqual(out["success_date"].isoformat(), "2025-02-06")

    def test_real_case_rejected_because_call_is_too_early(self):
        """Боевой кейс: звонок 20 мая (не последние 7 дней), поездка 17 июня ->
        не засчитано. Раньше этот кейс отклоняло правило «поездка после 7-го»."""
        out = compute_lead_outcome(
            parse_first_order_at("2026-06-17T22:02:40.243247+05:00"), None,
            [call(datetime(2026, 5, 20, 10, 0, tzinfo=ALMATY_TZ), operator_id=101)],
        )
        self.assertEqual(out["status"], STATUS_NOT_COUNTED)
        self.assertEqual(out["rule"], REASON_CALL_BEFORE_WINDOW)

    def test_last_call_decides_the_window(self):
        """Успешку по-прежнему получает последний звонок ОП перед поездкой, и
        именно он проверяется окном: ранний звонок коллеги окно не «спасает»."""
        calls = [
            call(dt(2026, 6, 26), operator_id=1, gid="early"),   # в окне, но не последний
            call(dt(2026, 7, 2), operator_id=2, gid="late"),     # месяц поездки -> ему
        ]
        out = compute_lead_outcome(dt(2026, 7, 20), None, calls)
        self.assertEqual(out["status"], STATUS_SUCCESS)
        self.assertEqual(out["rule"], RULE_SAME_MONTH)
        self.assertEqual(out["operator_id"], 2)


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


class CallWindowTests(unittest.TestCase):
    """Окно звонков должно совпадать с правилом успешки, иначе воронка врёт."""

    def test_window_starts_at_prev_month_tail(self):
        """Июль 2026: с 24 июня (последние 7 дней июня) по 31 июля."""
        self.assertEqual(call_window_for_period(2026, 7), (date(2026, 6, 24), date(2026, 7, 31)))

    def test_window_counts_from_month_end_not_fixed_day(self):
        """Хвост считается от конца месяца: у февраля это 22–28, а не «после 24-го»."""
        start, end = call_window_for_period(2026, 3)
        self.assertEqual(start, date(2026, 2, 22))
        self.assertEqual(end, date(2026, 3, 31))

    def test_window_crosses_year_boundary(self):
        start, end = call_window_for_period(2026, 1)
        self.assertEqual(start, date(2025, 12, 25))
        self.assertEqual(end, date(2026, 1, 31))

    def test_window_start_matches_success_rule(self):
        """Первый день окна обязан давать успешку, а предыдущий — уже нет."""
        trip = dt(2026, 7, 20)
        start, _ = call_window_for_period(2026, 7)
        inside = compute_lead_outcome(trip, None, [call(dt(start.year, start.month, start.day))])
        outside = compute_lead_outcome(trip, None, [call(dt(start.year, start.month, start.day - 1))])
        self.assertEqual(inside["status"], STATUS_SUCCESS)
        self.assertEqual(outside["status"], STATUS_NOT_COUNTED)


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

    def test_calls_fetch_is_not_gated_by_status(self):
        """Докачка звонков гейтится по calls_synced_at, а НЕ по статусу лида.

        Иначе лиды со старым статусом (already_working от прежней логики) никогда
        не получили бы звонки и не смогли бы стать успешками при пересчёте.
        """
        src = self.src
        start = src.index("def get_tez_phones_needing_calls")
        body = src[start:start + 1600]
        self.assertIn("l.calls_synced_at IS NULL", body)
        self.assertNotIn("l.status IN ('new', 'in_progress')", body)
        self.assertIn("def mark_tez_leads_calls_synced", src)
        self.assertIn("calls_synced_at TIMESTAMP WITH TIME ZONE", src)

    def test_funnel_counts_attempts_in_call_window_only(self):
        """«Обзвонено» = попытки оператора ОП в окне месяца, а не все звонки за всю историю.

        До правки карточка считала лид обзвоненным только если по нему вообще
        нашлись звонки, а качались они лишь по выехавшим — на июльской базе это
        давало 464 из 7 195. Теперь фильтр обязан быть в самом SQL: исходящий,
        с разрезолвленным оператором ОП и внутри окна успешки.
        """
        start = self.src.index("def get_tez_lead_funnel")
        body = self.src[start:start + 3000]
        self.assertIn("call_window_for_period", body)
        self.assertIn("c.call_type = 1", body)
        self.assertIn("c.operator_id IS NOT NULL", body)
        self.assertIn("COUNT(c.general_call_id) AS attempts", body)

    def test_call_upsert_refreshes_call_outcome(self):
        """Повторная выкачка дня обязана обновлять итог звонка, а не только оператора:
        звонок мог попасть в первую выдачу незавершённым (billsec = 0)."""
        start = self.src.index("def save_tez_lead_calls")
        body = self.src[start:start + 2600]
        for field in ("billsec = EXCLUDED.billsec", "disposition = EXCLUDED.disposition",
                      "call_type = EXCLUDED.call_type"):
            self.assertIn(field, body)

    def test_funnel_window_bounds_are_built_in_python(self):
        """Границы окна — готовые aware-даты, а не `date AT TIME ZONE` в SQL.

        У Postgres date неявно приводится и к timestamp, и к timestamptz, и он
        выбирает вторую ветку: `'2026-06-24'::date AT TIME ZONE 'Asia/Almaty'`
        даёт наивные 24.06 05:00 вместо полуночи по Алматы. Окно молча
        сдвинулось бы на 10 часов (проверено на проде).
        """
        start = self.src.index("def get_tez_lead_funnel")
        body = self.src[start:start + 3000]
        self.assertIn("datetime.combine(window_start, dt_time.min, tzinfo=ALMATY_TZ)", body)
        self.assertNotIn("::date AT TIME ZONE", body)

    def test_operator_day_view_exists_and_group_aware(self):
        """Таб «Успешки»: агрегат оператор→день, месяц по дате поездки, с группой.

        Сужение по operator_id добавлено для «Моих часов»: оператор получает свои
        успешки, не открывая статистику всей группы.
        """
        self.assertIn(
            "def get_tez_successes_operator_day(self, year, month, group_id=None, operator_id=None)",
            self.src,
        )
        self.assertIn("EXTRACT(DAY FROM s.success_date)", self.src)
        self.assertIn('operator_sql = " AND s.operator_id = %s"', self.src)


if __name__ == "__main__":
    unittest.main()
