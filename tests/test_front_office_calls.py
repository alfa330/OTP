# -*- coding: utf-8 -*-
"""Обзвон фронт-офиса: разбор ответа CRM, сведение с реестром и текст отбивки.

front_office_calls.py — чистая логика без БД и Flask, поэтому импортируется
напрямую (в отличие от bot_schedule2.py, который на старте поднимает пул к
боевой БД). Сеть в тестах не трогаем: клиент проверяем на подменённом _post.
"""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import front_office_calls as foc


def _user(uid, name, email=None):
    return {"id": uid, "name": name, "email": email}


def _crm(manager_id, name, login, calls):
    return {"manager_id": manager_id, "manager_name": name,
            "manager_login": login, "total_calls": calls,
            "by_status": [{"status_id": 23, "status_code": "agreement",
                           "status_title": "Согласие", "count": calls}]}


DAY = date(2026, 8, 16)


class FetchManagersTests(unittest.TestCase):
    """Разбор строгий: незнакомый формат = ошибка, а не пустой список."""

    def _client(self, payload):
        client = foc.RegionCallStatsClient("http://crm.test", "token")
        client._post = lambda body: payload
        return client

    def test_returns_managers_as_is(self):
        rows = [_crm(1, "Иванов Иван", "ivanov@x.kz", 7)]
        client = self._client({"total": 7, "dialog_statuses": [], "managers": rows})
        self.assertEqual(client.fetch_managers(DAY, DAY), rows)

    def test_empty_day_is_not_an_error(self):
        # Будущая дата — законный пустой ответ живого API, а не поломка.
        client = self._client({"total": 0, "dialog_statuses": [], "managers": []})
        self.assertEqual(client.fetch_managers(DAY, DAY), [])

    def test_missing_managers_key_raises(self):
        # Так CRM уже ломала конкурс регистраций: ключ пропал, а синк
        # отрапортовал «ok» и записал пустой срез.
        client = self._client({"total": 12, "rows": []})
        with self.assertRaises(RuntimeError) as ctx:
            client.fetch_managers(DAY, DAY)
        self.assertIn("managers", str(ctx.exception))

    def test_manager_without_total_calls_raises(self):
        client = self._client({"managers": [{"manager_id": 1, "manager_name": "И"}]})
        with self.assertRaises(RuntimeError):
            client.fetch_managers(DAY, DAY)

    def test_html_answer_raises(self):
        # На кривой период CRM отдаёт HTML-страницу с кодом 200.
        client = self._client("<!doctype html><html></html>")
        with self.assertRaises(RuntimeError):
            client.fetch_managers(DAY, DAY)

    def test_period_is_sent_inclusive(self):
        sent = {}
        client = foc.RegionCallStatsClient("http://crm.test", "token")
        client._post = lambda body: sent.update(body) or {"managers": []}
        client.fetch_managers(date(2026, 8, 10), date(2026, 8, 12))
        self.assertEqual(sent, {"from": "2026-08-10", "to": "2026-08-12"})


class BuildReportTests(unittest.TestCase):
    def test_manager_without_calls_stays_in_report(self):
        # Главное правило: CRM присылает только звонивших, а в отбивке нужен
        # именно молчун — иначе «план не выполнен» никогда не сработает.
        roster = [_user(1, "Иванов Иван", "ivanov@x.kz"),
                  _user(2, "Петров Пётр", "petrov@x.kz")]
        report = foc.build_report(roster, [_crm(10, "Иванов Иван", "ivanov@x.kz", 5)],
                                  DAY, plan_per_day=3)
        by_name = {r["name"]: r for r in report["rows"]}
        self.assertEqual(by_name["Петров Пётр"]["calls"], 0)
        self.assertIs(by_name["Петров Пётр"]["met"], False)
        self.assertIs(by_name["Иванов Иван"]["met"], True)
        self.assertEqual([r["name"] for r in report["missing"]], ["Петров Пётр"])
        self.assertEqual(report["roster_size"], 2)
        self.assertEqual(report["called_count"], 1)

    def test_matched_by_email_case_insensitive(self):
        roster = [_user(1, "Иванов Иван", "Ivanov@X.kz")]
        report = foc.build_report(roster, [_crm(10, "кто-то другой", "ivanov@x.kz", 4)], DAY)
        self.assertEqual(report["rows"][0]["calls"], 4)
        self.assertEqual(report["unmatched_calls"], 0)

    def test_matched_by_name_when_logins_differ(self):
        # Живой случай: у CRM kairzhanova_diana_fo@, у нас diana_kairzhanova_fo@.
        roster = [_user(426, "Каиржанова Диана", "diana_kairzhanova_fo@yandextaxi.kz")]
        crm = [_crm(468, "Каиржанова Диана", "kairzhanova_diana_fo@yandextaxi.kz", 5)]
        report = foc.build_report(roster, crm, DAY)
        self.assertEqual(report["rows"][0]["calls"], 5)
        self.assertEqual(report["unmatched_calls"], 0)

    def test_two_crm_accounts_of_one_person_are_summed(self):
        # У отдела есть люди с парой учёток CRM из разных наборов id.
        roster = [_user(1, "Иванов Иван", "ivanov@x.kz")]
        crm = [_crm(10, "Иванов Иван", "ivanov@x.kz", 4),
               _crm(99, "Иванов Иван", None, 3)]
        report = foc.build_report(roster, crm, DAY)
        self.assertEqual(report["rows"][0]["calls"], 7)
        self.assertEqual(report["total_calls"], 7)

    def test_unknown_manager_with_calls_is_shown_separately(self):
        # Строки без имени и логина живой API отдаёт: терять их звонки молча нельзя.
        roster = [_user(1, "Иванов Иван", "ivanov@x.kz")]
        crm = [_crm(10, "Иванов Иван", "ivanov@x.kz", 4), _crm(587, None, None, 3)]
        report = foc.build_report(roster, crm, DAY)
        self.assertEqual(report["total_calls"], 4)
        self.assertEqual(report["unmatched_calls"], 3)
        self.assertEqual(report["unmatched"][0]["crm_manager_id"], 587)

    def test_unknown_manager_without_calls_is_ignored(self):
        report = foc.build_report([_user(1, "Иванов Иван")], [_crm(587, None, None, 0)], DAY)
        self.assertEqual(report["unmatched"], [])

    def test_rows_sorted_by_calls_then_name(self):
        roster = [_user(1, "Борисов Борис"), _user(2, "Абдулов Абдул"), _user(3, "Ким Ким")]
        crm = [_crm(1, "Борисов Борис", None, 2), _crm(2, "Абдулов Абдул", None, 2),
               _crm(3, "Ким Ким", None, 9)]
        report = foc.build_report(roster, crm, DAY)
        self.assertEqual([r["name"] for r in report["rows"]],
                         ["Ким Ким", "Абдулов Абдул", "Борисов Борис"])

    def test_without_plan_nobody_is_missing(self):
        report = foc.build_report([_user(1, "Иванов Иван")], [], DAY)
        self.assertIsNone(report["plan_total"])
        self.assertEqual(report["missing"], [])
        self.assertIsNone(report["rows"][0]["met"])

    def test_plan_multiplies_over_period(self):
        # За три дня норма — тройная, иначе период всегда «выполнен».
        roster = [_user(1, "Иванов Иван"), _user(2, "Петров Пётр")]
        crm = [_crm(1, "Иванов Иван", None, 30), _crm(2, "Петров Пётр", None, 20)]
        report = foc.build_report(roster, crm, date(2026, 8, 10), date(2026, 8, 12),
                                  plan_per_day=10)
        self.assertEqual(report["days"], 3)
        self.assertEqual(report["plan_total"], 30)
        self.assertEqual([r["name"] for r in report["missing"]], ["Петров Пётр"])

    def test_zero_plan_treated_as_unset(self):
        report = foc.build_report([_user(1, "Иванов Иван")], [], DAY, plan_per_day=0)
        self.assertIsNone(report["plan_total"])

    def test_has_data_false_only_when_nothing_at_all(self):
        roster = [_user(1, "Иванов Иван")]
        self.assertFalse(foc.has_data(foc.build_report(roster, [], DAY)))
        self.assertTrue(foc.has_data(
            foc.build_report(roster, [_crm(1, "Иванов Иван", None, 1)], DAY)))
        # Даже если звонили только «чужие», день рабочий — молчать нельзя.
        self.assertTrue(foc.has_data(
            foc.build_report(roster, [_crm(587, None, None, 2)], DAY)))


class RenderReportTests(unittest.TestCase):
    def _report(self, plan=10):
        roster = [_user(1, "Иванов Иван"), _user(2, "Петров Пётр"),
                  _user(3, "Сидоров Сидор")]
        crm = [_crm(1, "Иванов Иван", None, 12), _crm(2, "Петров Пётр", None, 3)]
        return foc.build_report(roster, crm, DAY, plan_per_day=plan)

    def test_alert_lists_only_those_below_plan(self):
        text = foc.render_report(self._report(), only_missing=True)
        self.assertIn("Петров Пётр — 3 из 10", text)
        self.assertIn("Сидоров Сидор — 0 из 10", text)
        # Выполнивший план в утреннюю отбивку поимённо не попадает.
        self.assertNotIn("Иванов Иван", text)
        self.assertIn("Не выполнили: 2 из 3.", text)

    def test_full_report_lists_everyone_with_one_divider(self):
        text = foc.render_report(self._report())
        for name in ("Иванов Иван", "Петров Пётр", "Сидоров Сидор"):
            self.assertIn(name, text)
        self.assertEqual(text.count("— ниже плана —"), 1)
        # Черта стоит ровно между выполнившим и первым отставшим.
        lines = text.splitlines()
        self.assertLess(lines.index("Иванов Иван — 12"), lines.index("— ниже плана —"))
        self.assertLess(lines.index("— ниже плана —"), lines.index("Петров Пётр — 3"))

    def test_no_divider_when_nobody_reached_the_plan(self):
        # Живой случай 16.08: план не вытянул никто — черте не место в начале.
        roster = [_user(1, "Иванов Иван"), _user(2, "Петров Пётр")]
        crm = [_crm(1, "Иванов Иван", None, 2), _crm(2, "Петров Пётр", None, 1)]
        text = foc.render_report(foc.build_report(roster, crm, DAY, plan_per_day=10))
        self.assertNotIn("— ниже плана —", text)

    def test_unmatched_ids_are_grouped_under_one_prefix(self):
        roster = [_user(1, "Иванов Иван")]
        crm = [_crm(583, None, None, 2), _crm(476, None, None, 1),
               _crm(608, "Новенький Новичок", None, 3)]
        text = foc.render_report(foc.build_report(roster, crm, DAY))
        self.assertIn("Новенький Новичок, id 583, 476", text)

    def test_full_report_without_plan_has_no_divider(self):
        text = foc.render_report(self._report(plan=None))
        self.assertNotIn("— ниже плана —", text)
        self.assertNotIn("Не выполнили", text)

    def test_period_header_shows_both_dates(self):
        report = foc.build_report([_user(1, "Иванов Иван")], [],
                                  date(2026, 8, 10), date(2026, 8, 12), plan_per_day=10)
        text = foc.render_report(report)
        self.assertIn("10.08.2026 — 12.08.2026", text)
        self.assertIn("План: 10 в день, за 3 дня — 30.", text)

    def test_single_day_header(self):
        text = foc.render_report(self._report())
        self.assertIn("Обзвон фронт-офиса за 16.08.2026", text)
        self.assertIn("План: 10 звонков в день.", text)

    def test_unmatched_calls_are_mentioned(self):
        roster = [_user(1, "Иванов Иван")]
        crm = [_crm(1, "Иванов Иван", None, 4), _crm(587, None, None, 3)]
        text = foc.render_report(foc.build_report(roster, crm, DAY))
        self.assertIn("Ещё 3 звонка", text)
        self.assertIn("id 587", text)

    def test_html_in_names_is_escaped(self):
        roster = [_user(1, "Иванов <b>Иван</b>")]
        text = foc.render_report(foc.build_report(roster, [], DAY))
        self.assertIn("Иванов &lt;b&gt;Иван&lt;/b&gt;", text)

    def test_plural_forms(self):
        self.assertIn("Всего 1 звонок", foc.render_report(
            foc.build_report([_user(1, "И")], [_crm(1, "И", None, 1)], DAY)))
        self.assertIn("Всего 2 звонка", foc.render_report(
            foc.build_report([_user(1, "И")], [_crm(1, "И", None, 2)], DAY)))
        self.assertIn("Всего 11 звонков", foc.render_report(
            foc.build_report([_user(1, "И")], [_crm(1, "И", None, 11)], DAY)))


class ConfigTests(unittest.TestCase):
    def test_url_defaults_without_env(self):
        with patch.dict("os.environ", {}, clear=False):
            with patch.object(foc.reg_contest, "get_config", return_value={"token": "t"}):
                cfg = foc.get_config()
        self.assertEqual(cfg["url"], foc.DEFAULT_API_URL)
        self.assertTrue(foc.is_configured(cfg))

    def test_not_configured_without_token(self):
        with patch.object(foc.reg_contest, "get_config", return_value={"token": None}):
            self.assertFalse(foc.is_configured(foc.get_config()))


class YesterdayTests(unittest.TestCase):
    def test_yesterday_crosses_month(self):
        self.assertEqual(foc.yesterday(date(2026, 9, 1)), date(2026, 8, 31))


class ParsePeriodTests(unittest.TestCase):
    TODAY = date(2026, 8, 17)

    def test_empty_argument_is_yesterday(self):
        self.assertEqual(foc.parse_period("", self.TODAY),
                         (date(2026, 8, 16), date(2026, 8, 16)))
        self.assertEqual(foc.parse_period(None, self.TODAY),
                         (date(2026, 8, 16), date(2026, 8, 16)))

    def test_single_day_formats(self):
        expected = (date(2026, 8, 12), date(2026, 8, 12))
        for text in ("12.08.2026", "12.08", "2026-08-12"):
            self.assertEqual(foc.parse_period(text, self.TODAY), expected, text)

    def test_range(self):
        self.assertEqual(foc.parse_period("10.08 12.08", self.TODAY),
                         (date(2026, 8, 10), date(2026, 8, 12)))
        self.assertEqual(foc.parse_period("10.08, 12.08", self.TODAY),
                         (date(2026, 8, 10), date(2026, 8, 12)))

    def test_reversed_range_is_straightened(self):
        # CRM на перевёрнутом периоде отдаёт HTML с кодом 200 — не доводим до неё.
        self.assertEqual(foc.parse_period("12.08 10.08", self.TODAY),
                         (date(2026, 8, 10), date(2026, 8, 12)))

    def test_garbage_returns_none(self):
        for text in ("вчера", "32.08", "10.08 11.08 12.08", "08/10"):
            self.assertIsNone(foc.parse_period(text, self.TODAY), text)


if __name__ == "__main__":
    unittest.main()
