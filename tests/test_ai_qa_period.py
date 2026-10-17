# -*- coding: utf-8 -*-
"""Период и месяц в «Сводке» ИИ-оценки (call_qa.digest.facts / period / monthly).

Стережём:
* слепок дня хранит всё, что нужно периоду, и собирается обратно без потерь —
  критерии через словарь дня, вердикты человека поверх ИИ, критическое;
* цифры периода считает код: по дням, по шкалам, по критериям (доля из
  применимых), по людям, критические случаи; скоуп зрителя режет по направлению
  сотрудника;
* свежесть слепка — по двум отпечаткам (оценки и проверки людей); один-два дня
  пересчитываются в запросе, остальное — в фоне;
* контекст чата: таблицы, критические случаи, замечания, сводки дней; хвост «К
  ВОПРОСУ» — по названным людям, критериям и дням; данные огорожены метками;
* график — блок с таблицей: годный становится data-qa-chart, негодный остаётся
  таблицей, вложенный поднимается наверх; подписи кнопок за период — с датой;
* месячная сводка: разделы и «Главное», «fresh» на тех же оценках, упавший раздел
  не роняет соседей, зрителю со скоупом — только его разделы и без «Главного»,
  ночная проверка пишет только когда пора;
* ручки и хуки на месте, схема таблиц применяется, витрина пропускает атрибут
  графика.
"""
import ast
import datetime as dt
import re
import unittest
from pathlib import Path
from unittest import mock

from call_qa import config
from call_qa.digest import data, facts, monthly, period, prompts, render, service, store
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 10, 15)
FIRST, LAST = dt.date(2026, 10, 1), dt.date(2026, 10, 31)


def _criterion(name, ai, *, critical=False, conf=0.9, comment="", evidence="", source="transcript"):
    return {"name": name, "ai": ai, "is_critical": critical, "conf": conf, "comment": comment,
            "evidence": evidence, "source": source}


def _talk(ref, *, kind="imported_call", subject_id=None, operator="Иванова Айгерим", operator_id=7,
          time="10:12", criteria=None, score=80, direction=73, subject_direction=73, human=None,
          outcome=None, human_score=None, asr_conf=0.9):
    return {"ref": ref, "kind": kind, "id": subject_id or 1000 + ref, "model": "glm",
            "family": data.family_of(kind), "operator": operator, "operator_id": operator_id,
            "time": time, "subject_direction": subject_direction, "scale_direction": direction,
            "direction": {73: "Основа ОП", 74: "Поток", 71: "Верификатор"}.get(direction, "Направление"),
            "human_score": human_score, "outcome": outcome, "run_id": f"run-{ref}",
            "criteria": criteria or [], "ai_score": score, "unchecked_weight": 0, "asr_conf": asr_conf,
            "media_failed": 0, "end_party": None, "transcript": "", "human": human,
            "overall_comment": ""}


def _day_facts(day, talks):
    return facts.compact(talks, day)


def _sample_days():
    """Два дня: критическое у Ивановой, недочёт у Петрова, исправление человека."""
    day1 = [
        _talk(1, criteria=[_criterion("Приветствие", "Correct"),
                           _criterion("Грубость", "Incorrect", critical=True, comment="обругал",
                                      evidence="да пошёл ты"),
                           _criterion("Имя", "Incorrect", comment="не назвал")],
              score=40, time="09:00"),
        _talk(2, operator="Петров Арман", operator_id=8, direction=74, subject_direction=74,
              criteria=[_criterion("Отработка возражений", "Deficiency", comment="слабо"),
                        _criterion("Имя", "Correct")], score=78, time="11:30",
              human={"score": 85, "comment": "", "changes": [
                  {"position": 0, "name": "Отработка возражений", "ai": "Deficiency", "human": "Correct",
                   "note": "возражение отработано"}], "source": "journal",
                     "verdicts": ["Correct", None]},
              human_score=85, outcome="adjudicated"),
    ]
    day2 = [
        _talk(3, criteria=[_criterion("Приветствие", "Incorrect", comment="без приветствия"),
                           _criterion("Грубость", "Correct", critical=True),
                           _criterion("Имя", "N/A")], score=70, time="12:00"),
        _talk(4, kind="wz_episode", operator="Серик Даурен", operator_id=9, direction=71,
              subject_direction=71,
              criteria=[_criterion("Грамотность", "Deficiency", comment="опечатки")], score=90, time=""),
    ]
    return {"2026-10-14": _day_facts(dt.date(2026, 10, 14), day1),
            "2026-10-15": _day_facts(dt.date(2026, 10, 15), day2)}


class BoundsTests(unittest.TestCase):
    def test_period_is_capped_and_ordered(self):
        today = dt.date(2026, 10, 17)
        self.assertEqual(period.parse_period("2026-10-10", "2026-10-01", today),
                         (dt.date(2026, 10, 1), dt.date(2026, 10, 10)))
        with self.assertRaises(period.PeriodError):
            period.parse_period("2026-09-01", "2026-10-05", today)
        with self.assertRaises(period.PeriodError):
            period.parse_period("2026-10-01", "2026-10-18", today)
        with self.assertRaises(period.PeriodError):
            period.parse_period("2026-10-1", "2026-10-02", today)

    def test_month_bounds_and_completeness(self):
        self.assertEqual(period.month_bounds(dt.date(2026, 2, 10)), (dt.date(2026, 2, 1), dt.date(2026, 2, 28)))
        self.assertTrue(period.is_whole_month(dt.date(2026, 9, 1), dt.date(2026, 9, 30)))
        self.assertFalse(period.is_whole_month(dt.date(2026, 9, 1), dt.date(2026, 9, 29)))
        self.assertTrue(period.month_complete(dt.date(2026, 9, 1), dt.date(2026, 10, 1)))
        self.assertFalse(period.month_complete(dt.date(2026, 10, 1), dt.date(2026, 10, 31)))
        self.assertEqual(period.parse_month("2026-09"), dt.date(2026, 9, 1))
        with self.assertRaises(period.PeriodError):
            period.parse_month("2026-13")

    def test_previous_period_keeps_calendar_months(self):
        self.assertEqual(period.previous_period(dt.date(2026, 10, 1), dt.date(2026, 10, 31)),
                         (dt.date(2026, 9, 1), dt.date(2026, 9, 30)))
        self.assertEqual(period.previous_period(dt.date(2026, 10, 10), dt.date(2026, 10, 16)),
                         (dt.date(2026, 10, 3), dt.date(2026, 10, 9)))

    def test_titles(self):
        self.assertEqual(period.period_title(dt.date(2026, 9, 1), dt.date(2026, 9, 30)), "сентябрь 2026")
        self.assertEqual(period.period_title(dt.date(2026, 10, 1), dt.date(2026, 10, 15)), "1–15 октября")
        self.assertEqual(period.period_title(dt.date(2026, 9, 16), dt.date(2026, 10, 16)),
                         "16 сентября — 16 октября")


class FactsTests(unittest.TestCase):
    def test_compact_keeps_what_the_period_needs(self):
        day = _sample_days()["2026-10-14"]
        self.assertEqual(day["v"], facts.VERSION)
        names = [(c["s"], c["n"]) for c in day["criteria"]]
        self.assertEqual(names, [("73", "Приветствие"), ("73", "Грубость"), ("73", "Имя"),
                                 ("74", "Отработка возражений"), ("74", "Имя")])
        first, second = day["talks"]
        self.assertEqual(first["cr"], 1)
        self.assertEqual(first["fl"], [1, 2])
        self.assertEqual(first["nt"][0][:3], [1, "x", "обругал"])
        self.assertEqual(first["nt"][0][3], "да пошёл ты")
        # Человек снял недочёт: вердикт — человека, замечаний нет, исправление записано.
        self.assertEqual(second["df"], [])
        self.assertEqual(second["nt"], [])
        self.assertEqual(second["fx"], [[3, "D", "C", "возражение отработано"]])
        self.assertEqual((second["rv"], second["co"]), (1, 1))
        self.assertEqual(day["directions"], {"73": "Основа ОП", "74": "Поток"})

    def test_reviews_mark_matches_signature(self):
        talks = [_talk(1, outcome="confirmed"), _talk(2, human_score=70), _talk(3, outcome="adjudicated"),
                 _talk(4)]
        self.assertEqual(facts.reviews_mark_of(talks), "3:1")
        self.assertEqual(facts.signature_mark({"reviewed": 3, "corrected": 1}), "3:1")

    def test_freshness_needs_version_hash_and_reviews(self):
        signature = {"hash": "abc", "reviewed": 2, "corrected": 1}
        row = {"version": facts.VERSION, "inputs_hash": "abc", "reviews_mark": "2:1"}
        self.assertTrue(facts.is_fresh(row, signature))
        self.assertFalse(facts.is_fresh(dict(row, version=facts.VERSION - 1), signature))
        self.assertFalse(facts.is_fresh(dict(row, inputs_hash="zzz"), signature))
        self.assertFalse(facts.is_fresh(dict(row, reviews_mark="3:1"), signature))
        self.assertFalse(facts.is_fresh(None, signature))

    def test_ensure_recomputes_few_days_inline_and_queues_the_rest(self):
        signatures = {f"2026-10-{d:02d}": {"hash": f"h{d}", "reviewed": 0, "corrected": 0, "evaluated": 5}
                      for d in range(1, 6)}
        stored = {"2026-10-01": {"version": facts.VERSION, "inputs_hash": "h1", "reviews_mark": "0:0",
                                 "facts": {"day": "2026-10-01"}},
                  "2026-10-02": {"version": facts.VERSION, "inputs_hash": "old", "reviews_mark": "0:0",
                                 "facts": {"day": "2026-10-02", "stale": True}}}

        class _Conn:
            def cursor(self):
                return mock.MagicMock()

            def close(self):
                pass

        refreshed = []

        def refresh(department, day):
            refreshed.append(day.isoformat())
            return {"facts": {"day": day.isoformat(), "fresh": True}}

        queued = []
        def in_range(cur, department, first, last):
            return {day: s for day, s in signatures.items()
                    if first.isoformat() <= day <= last.isoformat()}

        with mock.patch.object(config, "connect_ro", return_value=_Conn()), \
                mock.patch.object(facts, "day_signatures", side_effect=in_range), \
                mock.patch.object(facts, "read_days", return_value=stored), \
                mock.patch.object(facts, "refresh_day", side_effect=refresh), \
                mock.patch.object(facts, "queue", side_effect=lambda d, days: queued.extend(days)):
            many = facts.ensure("op", dt.date(2026, 10, 1), dt.date(2026, 10, 5))
            self.assertEqual(sorted(many["pending"]), ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"])
            self.assertEqual(sorted(queued), sorted(many["pending"]))
            # Устаревший слепок отдан как есть — частичные цифры честнее пустоты.
            self.assertTrue(many["days"]["2026-10-02"].get("stale"))
            self.assertEqual(refreshed, [])
            self.assertEqual(many["count"], 25)
            few = facts.ensure("op", dt.date(2026, 10, 1), dt.date(2026, 10, 2))
            self.assertEqual(few["pending"], [])
            self.assertEqual(refreshed, ["2026-10-02"])
            self.assertTrue(few["days"]["2026-10-02"]["fresh"])
            everything = facts.ensure("op", dt.date(2026, 10, 1), dt.date(2026, 10, 5), inline_limit=None)
            self.assertEqual(everything["pending"], [])
            self.assertEqual(len(everything["days"]), 5)

    def test_signature_sql_mirrors_the_python_hash(self):
        # Ключ «вид:id:прогон», сортировка по байтам, sha256, 32 знака — как data.inputs_hash.
        self.assertIn("rc.subject_kind || ':' || rc.call_id::text || ':'", facts._KEY_SQL)
        self.assertIn("NULLIF(rc.payload->>'_evaluation_run_id', '')", facts._KEY_SQL)
        src = (ROOT / "call_qa" / "digest" / "facts.py").read_text(encoding="utf-8")
        self.assertIn('COLLATE "C"', src)
        self.assertIn("'sha256'), 'hex'), 32)", src.replace("\n", "").replace(" ", "")
                      .replace("'sha256'),'hex'),32)", "'sha256'), 'hex'), 32)"))

    def test_combined_hash_depends_on_days_and_reviews(self):
        a = facts.combined_hash({"2026-10-01": {"hash": "x", "reviewed": 1, "corrected": 0}})
        b = facts.combined_hash({"2026-10-01": {"hash": "x", "reviewed": 2, "corrected": 0}})
        c = facts.combined_hash({"2026-10-01": {"hash": "y", "reviewed": 1, "corrected": 0}})
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.days = _sample_days()
        self.talks, self.criteria, self.directions = period.talks_of(self.days, None)
        self.summary = period.summarize(self.talks, self.criteria, self.directions,
                                        first=dt.date(2026, 10, 14), last=dt.date(2026, 10, 16))

    def test_talks_are_rebuilt_with_a_shared_criteria_dictionary(self):
        self.assertEqual([t["ref"] for t in self.talks], [1, 2, 3, 4])
        names = {(c["scale"], c["name"]) for c in self.criteria}
        self.assertIn(("73", "Приветствие"), names)
        self.assertIn(("71", "Грамотность"), names)
        # Одноимённый критерий разных шкал — две строки словаря.
        self.assertEqual(sum(1 for c in self.criteria if c["name"] == "Имя"), 2)
        third = self.talks[2]
        self.assertEqual([self.criteria[n]["name"] for n in third["fails"]], ["Приветствие"])
        self.assertEqual(third["direction"], "Основа ОП")

    def test_totals_days_sections(self):
        totals = self.summary["totals"]
        self.assertEqual((totals["evaluated"], totals["calls"], totals["chats"]), (4, 3, 1))
        self.assertEqual(totals["critical"], 1)
        self.assertEqual(totals["below_60"], 1)
        self.assertEqual(totals["reviewed"], 1)
        self.assertEqual(totals["operators"], 3)
        self.assertEqual(totals["ai_avg"], 69.5)
        days = self.summary["days"]
        self.assertEqual([d["day"] for d in days], ["2026-10-14", "2026-10-15", "2026-10-16"])
        self.assertEqual(days[0]["critical"], 1)
        self.assertEqual(days[2]["evaluated"], 0)
        sections = {s["direction"]: s for s in self.summary["sections"]}
        self.assertEqual(sections["Основа ОП"]["evaluated"], 2)
        self.assertEqual(sections["Верификатор"]["family"], "chats")
        self.assertEqual(sections["Поток"]["subject_directions"], [74])

    def test_criteria_people_and_critical_cases(self):
        criteria = {(c["direction"], c["name"]): c for c in self.summary["criteria"]}
        greeting = criteria[("Основа ОП", "Приветствие")]
        self.assertEqual((greeting["applicable"], greeting["fail"], greeting["bad_share"]), (2, 1, 0.5))
        # Снятый человеком недочёт в ошибки не идёт.
        self.assertEqual(criteria[("Поток", "Отработка возражений")]["bad"], 0)
        self.assertEqual(greeting["people"], [{"name": "Иванова Айгерим", "n": 1}])
        people = {p["name"]: p for p in self.summary["people"]}
        self.assertEqual(self.summary["people"][0]["name"], "Иванова Айгерим")
        self.assertEqual(people["Иванова Айгерим"]["critical"], 1)
        self.assertEqual(people["Иванова Айгерим"]["fails"], 3)
        self.assertEqual(people["Иванова Айгерим"]["worst"][0]["n"], 1)
        self.assertEqual(people["Петров Арман"]["human_avg"], 85)
        cases = self.summary["critical_cases"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["reasons"][0]["name"], "Грубость")
        self.assertEqual(cases[0]["reasons"][0]["quote"], "да пошёл ты")

    def test_scope_filters_by_employee_direction(self):
        talks, _c, _d = period.talks_of(self.days, [74])
        self.assertEqual([t["operator"] for t in talks], ["Петров Арман"])
        empty, _c, _d = period.talks_of(self.days, [])
        self.assertEqual(empty, [])

    def test_public_summary_hides_internal_refs(self):
        public = period.public_summary(self.summary)
        self.assertNotIn("examples", public["criteria"][0])
        self.assertNotIn("critical_refs", public["people"][0])
        self.assertNotIn("ref", public["critical_cases"][0])
        self.assertEqual(public["critical_cases"][0]["id"], 1001)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.days = _sample_days()
        self.talks, self.criteria, self.directions = period.talks_of(self.days, None)
        self.first, self.last = dt.date(2026, 10, 14), dt.date(2026, 10, 15)
        self.summary = period.summarize(self.talks, self.criteria, self.directions,
                                        first=self.first, last=self.last)

    def test_context_has_tables_cases_notes_and_digests(self):
        previous = dict(self.summary)
        previous["totals"] = dict(self.summary["totals"], ai_avg=80.0, critical_share=0.1)
        text = period.context_text(
            department_name="Отдел продаж", first=self.first, last=self.last,
            scope_text="все направления", summary=self.summary, previous=previous,
            talks=self.talks, criteria=self.criteria,
            day_digests=[{"day": "2026-10-14", "title": "«Главное» по отделу", "text": "Главное дня === x"}])
        self.assertIn("ИТОГО ЗА ПЕРИОД: оценено 4 (звонков 3, чатов 1) у 3 сотрудников", text)
        self.assertIn("средний балл ИИ 70 (прошлый период 80)", text)
        self.assertIn("ПО ДНЯМ", text)
        self.assertIn("14.10 · 2 · 59 · 1 · 1 · 1 · 85 · 2", text)
        self.assertIn("«Основа ОП» (звонки): оценено 2", text)
        self.assertIn("«Основа ОП» · Приветствие: Неверно 1, Недочёт 0 из 2 (50%)", text)
        self.assertIn("Иванова Айгерим («Основа ОП»): 2 · 2 · 55 · 40 · 1 · 3 · 0 · 0 · —", text)
        self.assertIn("КРИТИЧЕСКИЕ СЛУЧАИ (1;", text)
        self.assertIn("#1 · 14.10 09:00 · звонок · Иванова Айгерим («Основа ОП») · балл ИИ 40", text)
        self.assertIn("✗ Грубость [критический]: обругал | цитата: «да пошёл ты»", text)
        self.assertIn("ЗАМЕЧАНИЯ ОЦЕНЩИКА", text)
        self.assertIn("#3 · 15.10 12:00", text)
        # Критический разговор в замечаниях не повторяется.
        self.assertEqual(text.count("#1 · 14.10 09:00"), 1)
        self.assertIn("СВОДКИ ДНЕЙ", text)
        # Чужой «===» внутри сводки обезврежен.
        self.assertIn("Главное дня = = = x", text)

    def test_focus_finds_people_criteria_and_days(self):
        focus = period.focus_text("Какие ошибки у Ивановой по приветствию 15 октября?",
                                  self.summary, self.talks, self.criteria, self.first, self.last)
        self.assertIn("РАЗГОВОРЫ ИВАНОВА АЙГЕРИМ С ЗАМЕЧАНИЯМИ (2 из 2)", focus)
        self.assertIn("КРИТЕРИЙ «Приветствие» ПО ЛЮДЯМ: Иванова Айгерим 1", focus)
        self.assertIn("РАЗГОВОРЫ 15.10 С ЗАМЕЧАНИЯМИ (2)", focus)
        self.assertEqual(period.focus_text("Что в целом?", self.summary, self.talks, self.criteria,
                                           self.first, self.last), "")
        self.assertEqual(period.mentioned_days("разбор 14.10 и 20.11", self.first, self.last), ["2026-10-14"])

    def test_chat_prompt_fences_data_and_knows_charts(self):
        text = prompts.period_chat_system("op", "Отдел продаж", "1–15 октября", "КОНТЕКСТ", "все направления")
        self.assertIn(prompts.PERIOD_OPEN + "\nКОНТЕКСТ\n" + prompts.PERIOD_CLOSE, text)
        self.assertIn(prompts.CHART_MARKUP, text)
        self.assertIn(prompts.MARKUP, text)
        self.assertIn(prompts.REF_RULE, text)
        self.assertIn("ПАССАЖИРЫ", text)
        self.assertNotIn("ПАССАЖИРЫ", prompts.period_chat_system("tez", "Тез", "месяц", "x"))
        # Чат дня тоже умеет графики, а разделы сводки дня — нет (их вид не менялся).
        self.assertIn(prompts.CHART_MARKUP, prompts.chat_system("op", "отдел", "контекст"))
        self.assertNotIn(prompts.CHART_MARKUP, prompts.section_system("op", "Поток"))
        for kind in prompts.CHART_KINDS:
            self.assertIn(kind, prompts.CHART_MARKUP)
        self.assertEqual(set(prompts.CHART_KINDS), set(render.CHART_KINDS))

    def test_month_prompts_name_the_header_numbers_and_sections(self):
        section = prompts.month_section_system("op", "Поток", "сентябрь 2026")
        self.assertIn("сводка за сентябрь 2026 по направлению «Поток»", section)
        self.assertIn("«План на следующий месяц»", section)
        self.assertIn(prompts.ENVELOPE, section)
        self.assertIn(prompts.CHART_MARKUP, section)
        overview = prompts.month_overview_system("op", "Отдел продаж", "сентябрь 2026")
        self.assertIn("Напиши раздел «Главное»", overview)
        user = prompts.month_overview_user("сентябрь 2026", "Отдел продаж", "НАД ТЕКСТОМ: x",
                                           [{"key": "73", "direction": "Основа ОП", "text": "т"}],
                                           {"73": {"evaluated": 5, "ai_avg": 80.4, "critical": 1,
                                                   "previous": {"ai_avg": 75.6, "critical": 3}}})
        self.assertIn("средний балл ИИ 80 (прошлый месяц 76), критических 1 (прошлый месяц 3)", user)


class ChartRenderTests(unittest.TestCase):
    CHART = ('<div data-wiki-block="chart" data-kind="line"><h4>Балл по дням</h4>'
             '<table><thead><tr><th>День</th><th>Балл</th></tr></thead>'
             '<tbody><tr><td>01.10</td><td>84</td></tr><tr><td>02.10</td><td>81,5</td></tr></tbody></table></div>')

    def test_valid_chart_becomes_a_chart_block(self):
        html = render.clean_html("<p>Вот</p>" + self.CHART)
        self.assertIn('<div data-qa-chart="line"><h4>Балл по дням</h4><table>', html)
        self.assertNotIn("data-wiki-block=\"chart\"", html)
        self.assertNotIn("data-kind", html)

    def test_unknown_kind_falls_back_to_bars_and_bad_table_stays_a_table(self):
        html = render.clean_html(self.CHART.replace('data-kind="line"', 'data-kind="pie"'))
        self.assertIn('data-qa-chart="bar"', html)
        words = render.clean_html(
            '<div data-wiki-block="chart" data-kind="bar"><table><tr><th>a</th><th>b</th></tr>'
            '<tr><td>x</td><td>много</td></tr><tr><td>y</td><td>мало</td></tr></table></div>')
        self.assertNotIn("data-qa-chart", words)
        self.assertIn("<table>", words)
        one_row = render.clean_html(
            '<div data-wiki-block="chart" data-kind="bar"><table><tr><th>a</th><th>b</th></tr>'
            '<tr><td>x</td><td>1</td></tr></table></div>')
        self.assertNotIn("data-qa-chart", one_row)

    def test_nested_chart_is_lifted_out_of_the_block(self):
        html = render.clean_html(
            '<div data-wiki-block="note"><p>текст</p>' + self.CHART + '</div>')
        self.assertIn('data-qa-chart="line"', html)
        soup_order = html.index('data-wiki-block="note"') < html.index('data-qa-chart')
        self.assertTrue(soup_order)
        # График не внутри плашки.
        self.assertNotIn('<p>текст</p><div data-qa-chart', html)

    def test_model_cannot_forge_chart_attribute_itself(self):
        html = render.clean_html('<div data-qa-chart="line"><table><tr><th>a</th><th>b</th></tr>'
                                 '<tr><td>x</td><td>1</td></tr><tr><td>y</td><td>2</td></tr></table></div>')
        self.assertNotIn("data-qa-chart", html)

    def test_chart_is_plain_text_for_the_model_history(self):
        html = render.clean_html(self.CHART)
        text = render.to_text(html)
        self.assertIn("Балл по дням", text)
        self.assertIn("01.10", text)

    def test_period_reference_label_has_a_date(self):
        talk = {"kind": "imported_call", "id": 5, "family": "calls", "operator": "Иванова Айгерим",
                "time": "14:09", "day": "2026-10-03", "ai_score": 41, "direction": "Поток"}
        refs = {1: period.ref_entry(talk)}
        html, used = render.link_refs("<p>Грубость [[#1]] и ещё [[#1]]</p>", refs)
        self.assertIn(">Иванова А. · 03.10 14:09<", html)
        self.assertIn("Звонок #5 · Иванова Айгерим · 03.10 14:09 · балл ИИ 41", html)
        self.assertEqual(used, ["imported_call:5", "imported_call:5"])
        # Названный рядом сотрудник — кнопка только с датой и временем.
        named, _ = render.link_refs("<p>Иванова Айгерим [[#1]]</p>", refs)
        self.assertIn(">03.10 14:09<", named)


class _Conn:
    def cursor(self):
        return mock.MagicMock()

    def close(self):
        pass


def _built(talks_days, previous_days=None, first=dt.date(2026, 9, 1), last=dt.date(2026, 9, 30)):
    talks, criteria, directions = period.talks_of(talks_days, None)
    summary = period.summarize(talks, criteria, directions, first=first, last=last)
    previous = None
    ptalks, pcriteria, pdirections = [], [], {}
    if previous_days:
        pfirst, plast = period.previous_period(first, last)
        ptalks, pcriteria, pdirections = period.talks_of(previous_days, None)
        previous = period.summarize(ptalks, pcriteria, pdirections, first=pfirst, last=plast)
    return {"department": "op", "first": first, "last": last, "talks": talks, "criteria": criteria,
            "directions": directions, "summary": summary, "previous": previous,
            "previous_pending": False, "previous_talks": ptalks, "previous_criteria": pcriteria,
            "previous_directions": pdirections, "pending": [], "signatures": {},
            "inputs_hash": "fp-1", "evaluated_total": len(talks)}


class MonthlyGenerateTests(unittest.TestCase):
    MONTH = dt.date(2026, 9, 1)

    def setUp(self):
        self.saved = {}
        self.released = {}
        days = {"2026-09-10": _day_facts(dt.date(2026, 9, 10), [
            _talk(1, criteria=[_criterion("Имя", "Incorrect", comment="не назвал")]),
            _talk(2, operator="Петров Арман", operator_id=8, direction=74, subject_direction=74,
                  criteria=[_criterion("Имя", "Correct")])])}
        self.built = _built(days)
        patches = [
            mock.patch.object(store, "month_claim", return_value=True),
            mock.patch.object(store, "month_release",
                              side_effect=lambda *a, **k: self.released.update(k)),
            mock.patch.object(store, "month_save", side_effect=lambda *a, **k: self.saved.update(k)),
            mock.patch.object(config, "connect_ro", return_value=_Conn()),
            mock.patch.object(service, "_department_name", return_value="Отдел продаж"),
            mock.patch.object(period, "day_digests_for", return_value=[
                {"day": "2026-09-10", "title": "«Основа ОП»", "text": "сводка дня"}]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _with(self, built=None, digest=None):
        return (mock.patch.object(period, "build", return_value=built or self.built),
                mock.patch.object(monthly, "_stored", return_value=digest))

    def test_sections_and_overview_are_written_and_saved(self):
        calls = []

        def fake(system, user, **kwargs):
            calls.append((system, user))
            if "Напиши раздел «Главное»" in system:
                return "КРАТКО: Главное месяца\nСВОДКА:\n<p>Обзор [[#1]]</p>", {"provider": "vertex", "model": "m"}
            return ("КРАТКО: Раздел\nСВОДКА:\n<p>Ошибка [[#1]] и чужой [[#2]]</p>"
                    '<div data-wiki-block="chart" data-kind="line"><h4>Балл</h4><table><tr><th>д</th><th>б</th></tr>'
                    "<tr><td>01.09</td><td>80</td></tr><tr><td>02.09</td><td>82</td></tr></table></div>",
                    {"provider": "vertex", "model": "m", "usage": {"prompt_tokens": 10}})

        a, b = self._with()
        with a, b:
            result = monthly.generate("op", self.MONTH, generate_fn=fake)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.saved["headline"], "Главное месяца")
        self.assertEqual([s["key"] for s in self.saved["sections"]], ["73", "74"])
        first = self.saved["sections"][0]
        # Раздел ссылается только на свои разговоры: чужой номер исчез, график остался.
        self.assertIn('data-qa-ref="imported_call:1001"', first["html"])
        self.assertNotIn("imported_call:1002", first["html"])
        self.assertIn('data-qa-chart="line"', first["html"])
        self.assertEqual(first["stats"]["evaluated"], 1)
        self.assertIsNone(first["stats"]["previous"])
        self.assertEqual(self.saved["inputs_hash"], "fp-1")
        self.assertEqual(self.saved["stats"]["department"]["evaluated"], 2)
        self.assertEqual(self.saved["usage"]["calls"], 3)
        section_system, section_user = calls[0]
        self.assertIn("сентябрь 2026", section_system)
        self.assertIn("НАД ТЕКСТОМ", section_user)
        self.assertIn("СВОДКИ ДНЕЙ", section_user)
        self.assertIn(prompts.PERIOD_OPEN, section_user)

    def test_same_inputs_are_fresh_and_do_not_call_the_model(self):
        digest = {"status": "ready", "inputs_hash": "fp-1", "overview_html": "<p>x</p>",
                  "sections": [{"key": "73", "html": "<p>a</p>"}, {"key": "74", "html": "<p>b</p>"}]}
        fake = mock.Mock()
        a, b = self._with(digest=digest)
        with a, b:
            self.assertEqual(monthly.generate("op", self.MONTH, generate_fn=fake)["status"], "fresh")
        fake.assert_not_called()
        self.assertEqual(self.released, {})

    def test_failed_section_keeps_the_rest_and_old_text(self):
        digest = {"status": "ready", "inputs_hash": "old", "headline": "Было", "overview_html": "<p>старое</p>",
                  "sections": [{"key": "73", "html": "<p>прежний</p>", "headline": "Прежний", "refs": []}],
                  "stats": {}}

        def fake(system, user, **kwargs):
            if "«Основа ОП»" in system:
                raise RuntimeError("HTTP 429 RESOURCE_EXHAUSTED")
            if "Напиши раздел «Главное»" in system:
                return "КРАТКО: Новое главное\nСВОДКА:\n<p>Обзор</p>", {"provider": "vertex", "model": "m"}
            return "КРАТКО: Поток\nСВОДКА:\n<p>Поток ок</p>", {"provider": "vertex", "model": "m"}

        a, b = self._with(digest=digest)
        with a, b:
            result = monthly.generate("op", self.MONTH, generate_fn=fake)
        self.assertEqual(result["status"], "ready")
        sections = {s["key"]: s for s in self.saved["sections"]}
        self.assertEqual(sections["73"]["html"], "<p>прежний</p>")
        self.assertTrue(sections["73"]["outdated"])
        self.assertEqual(sections["74"]["html"], "<p>Поток ок</p>")
        self.assertIn("ИИ был перегружен", self.saved["error"])
        self.assertTrue(monthly.incomplete({"sections": self.saved["sections"], "overview_html": "x"}))

    def test_empty_month_releases_without_the_model(self):
        empty = _built({})
        fake = mock.Mock()
        a, b = self._with(built=empty)
        with a, b:
            self.assertEqual(monthly.generate("op", self.MONTH, generate_fn=fake)["status"], "empty")
        fake.assert_not_called()
        self.assertTrue(self.released.get("forget_pending"))

    def test_busy_claim_is_running(self):
        with mock.patch.object(store, "month_claim", return_value=False):
            self.assertEqual(monthly.generate("op", self.MONTH)["status"], "running")


class MonthlyReadTests(unittest.TestCase):
    def setUp(self):
        self.digest = {
            "status": "ready", "running": False, "headline": "Отдел", "overview_html": "<p>Все</p>",
            "inputs_hash": "fp-1", "generated_at": None, "model": "vertex:m", "last_error": None,
            "stats": {"department": {"evaluated": 10}},
            "sections": [
                {"key": "73", "direction": "Основа ОП", "family": "calls", "headline": "Основа",
                 "html": "<p>a</p>", "subject_directions": [73], "stats": {}},
                {"key": "74", "direction": "Поток", "family": "calls", "headline": "Поток",
                 "html": "<p>b</p>", "subject_directions": [74], "stats": {}},
            ]}

    def _read(self, allowed, inputs_hash="fp-1", digest=None):
        with mock.patch.object(config, "connect_ro", return_value=_Conn()), \
                mock.patch.object(store, "month_get", return_value=self.digest if digest is None else digest):
            return monthly.read("op", dt.date(2026, 9, 1), allowed, inputs_hash=inputs_hash,
                                today=dt.date(2026, 10, 17))

    def test_department_viewer_sees_everything(self):
        view = self._read(None)
        self.assertEqual(view["overview_html"], "<p>Все</p>")
        self.assertEqual([s["key"] for s in view["sections"]], ["73", "74"])
        self.assertEqual(view["headline"], "Отдел")
        self.assertEqual(view["stats"], {"evaluated": 10})
        self.assertFalse(view["stale"])
        self.assertTrue(view["complete"])
        self.assertEqual(view["title"], "сентябрь 2026")

    def test_scoped_viewer_sees_own_section_without_overview_or_department_stats(self):
        view = self._read([74])
        self.assertIsNone(view["overview_html"])
        self.assertIsNone(view["stats"])
        self.assertEqual([s["key"] for s in view["sections"]], ["74"])
        self.assertEqual(view["headline"], "Поток")

    def test_changed_inputs_make_the_digest_stale(self):
        self.assertTrue(self._read(None, inputs_hash="fp-2")["stale"])
        self.assertEqual(self._read(None, digest={})["status"], "none")

    def test_known_months_for_the_list(self):
        found = {"2026-09": {"status": "ready", "headline": "Отдел", "running": False, "generated_at": None,
                             "sections": [{"key": "73", "headline": "Основа", "subject_directions": [73]},
                                          {"key": "84", "headline": "ТП", "subject_directions": [83, 84]}]}}
        with mock.patch.object(config, "connect_ro", return_value=_Conn()), \
                mock.patch.object(store, "months", return_value=found):
            whole = monthly.known("op", ["2026-09", "2026-10", "bad"], None, today=dt.date(2026, 10, 17))
            scoped = monthly.known("op", ["2026-09"], [83], today=dt.date(2026, 10, 17))
        self.assertEqual(whole["2026-09"]["headline"], "Отдел")
        self.assertTrue(whole["2026-09"]["complete"])
        self.assertEqual(whole["2026-10"]["status"], "none")
        self.assertFalse(whole["2026-10"]["complete"])
        self.assertNotIn("bad", whole)
        # СВ направления 83: его люди только в общем разделе — «в общих разделах».
        self.assertEqual(scoped["2026-09"]["headline"], "")
        self.assertTrue(scoped["2026-09"]["hidden"])

    def test_due_generation_only_when_it_is_time(self):
        ready = {"status": "ready", "running": False, "sections": [{"html": "<p>a</p>"}], "overview_html": None}
        with mock.patch.object(monthly, "_stored", return_value=ready), \
                mock.patch.object(monthly, "generate", return_value={"status": "fresh"}) as generate:
            self.assertIsNone(monthly.generate_due("op", today=dt.date(2026, 10, 17)))
            generate.assert_not_called()
            self.assertEqual(monthly.generate_due("op", today=dt.date(2026, 10, 2)), {"status": "fresh"})
            generate.assert_called_once_with("op", dt.date(2026, 9, 1), triggered_by="after-sample",
                                             generate_fn=None)
        with mock.patch.object(monthly, "_stored", return_value=None), \
                mock.patch.object(monthly, "generate", return_value={"status": "ready"}) as generate:
            self.assertEqual(monthly.generate_due("op", today=dt.date(2026, 10, 17))["status"], "ready")
        with mock.patch.object(monthly, "_stored", return_value=dict(ready, running=True)), \
                mock.patch.object(monthly, "generate") as generate:
            self.assertIsNone(monthly.generate_due("op", today=dt.date(2026, 10, 2)))
            generate.assert_not_called()


class AskTests(unittest.TestCase):
    def test_question_limits_and_one_at_a_time(self):
        with self.assertRaises(period.PeriodError):
            period.ask("op", FIRST, LAST, "  ", user_id=1)
        with self.assertRaises(period.PeriodError):
            period.ask("op", FIRST, LAST, "а" * (period.QUESTION_LIMIT + 1), user_id=1)
        key = ("op", FIRST.isoformat(), LAST.isoformat(), 1)
        period._ASKING.add(key)
        try:
            with self.assertRaises(period.PeriodError):
                period.ask("op", FIRST, LAST, "вопрос", user_id=1)
        finally:
            period._ASKING.discard(key)

    def test_answer_uses_context_focus_and_history(self):
        built = _built(_sample_days(), first=dt.date(2026, 10, 14), last=dt.date(2026, 10, 15))
        seen = {}

        def fake(system, user, **kwargs):
            seen["system"], seen["user"], seen["history"] = system, user, kwargs.get("history")
            return ("<p>Смотрите [[#1]]</p><div data-wiki-block=\"chart\" data-kind=\"hbar\"><table>"
                    "<tr><th>Кто</th><th>Балл</th></tr><tr><td>Иванова</td><td>55</td></tr>"
                    "<tr><td>Петров</td><td>78</td></tr></table></div>",
                    {"provider": "vertex", "model": "m", "usage": {}})

        saved = {}
        history = [{"role": "user", "body": "Привет"},
                   {"role": "assistant", "body": '<p>Ответ <span data-qa-ref="imported_call:1001">x</span></p>'}]
        period._CONTEXT.clear()
        with mock.patch.object(period, "build", return_value=built), \
                mock.patch.object(config, "connect_ro", return_value=_Conn()), \
                mock.patch.object(period, "_department_name", return_value="Отдел продаж"), \
                mock.patch.object(period, "day_digests_for", return_value=[]), \
                mock.patch.object(store, "period_thread", return_value=history), \
                mock.patch.object(store, "period_append_pair",
                                  side_effect=lambda *a, **k: saved.update(args=a, kwargs=k) or (
                                      {"id": 1, "role": "user", "body": a[4]},
                                      {"id": 2, "role": "assistant", "body": a[5]})):
            result = period.ask("op", dt.date(2026, 10, 14), dt.date(2026, 10, 15),
                                "Что у Ивановой?", user_id=1, generate_fn=fake)
        period._CONTEXT.clear()
        self.assertIn('data-qa-ref="imported_call:1001"', result["answer"]["body"])
        self.assertIn('data-qa-chart="hbar"', result["answer"]["body"])
        self.assertIn("КРИТИЧЕСКИЕ СЛУЧАИ", seen["system"])
        self.assertIn(prompts.FOCUS_OPEN, seen["user"])
        self.assertIn("РАЗГОВОРЫ ИВАНОВА АЙГЕРИМ", seen["user"])
        self.assertTrue(seen["user"].startswith("Что у Ивановой?"))
        self.assertEqual(seen["history"][1]["text"], "Ответ [[#1]]")
        self.assertEqual(saved["args"][5], result["answer"]["body"])

    def test_pending_facts_refuse_politely(self):
        built = _built(_sample_days())
        built["pending"] = ["2026-10-14"]
        period._CONTEXT.clear()
        with mock.patch.object(period, "build", return_value=built):
            with self.assertRaises(period.PeriodError):
                period._period_context("op", dt.date(2026, 10, 14), dt.date(2026, 10, 15), None)


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.tree = source_cache.parse(cls.routes)

    def _function(self, name):
        node = next(item for item in self.tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(self.routes, node)

    def test_routes_exist_with_guards(self):
        for route, function, guard in (
                ("/api/ai-qa/period", "api_ai_qa_period", "_ai_qa_guard()"),
                ("/api/ai-qa/period/digest", "api_ai_qa_month_digest", "_ai_qa_guard()"),
                ("/api/ai-qa/period/chat", "api_ai_qa_period_chat", "_ai_qa_chat_guard()")):
            self.assertIn(f"@app.route('{route}'", self.routes)
            body = self._function(function)
            self.assertIn(guard, body, function)
            self.assertIn("_ai_qa_requested_department(", body, function)

    def test_period_reads_in_scope_and_month_force_is_super_admin_only(self):
        body = self._function("api_ai_qa_period")
        self.assertIn("qa_period.read(department, first, last, scope)", body)
        self.assertIn("month_complete(first, today)", body)
        self.assertIn("qa_period.status(department, first, last)", body)
        chat = self._function("api_ai_qa_period_chat")
        self.assertIn("allowed_direction_ids=_ai_qa_direction_scope(requester_id)", chat)
        month = self._function("api_ai_qa_month_digest")
        self.assertIn("force = bool(body.get('force')) and is_super", month)
        self.assertIn("month_complete(month, today)", month)
        view = self._function("_ai_qa_month_view")
        self.assertIn('month["can_generate"] = (', view)
        self.assertIn("_is_marketing_observer(requester_id, role)", view)
        actions = self.routes.split("AI_QA_OBSERVER_ACTION_ENDPOINTS = frozenset({")[1].split("})")[0]
        self.assertNotIn("api_ai_qa_month_digest", actions)
        self.assertNotIn("api_ai_qa_period", actions)

    def test_nightly_hooks_and_pools(self):
        job = self._function("ai_qa_digest_job")
        self.assertIn("qa_facts.refresh_day(code, day)", job)
        sample = self._function("ai_qa_daily_sample_job")
        self.assertIn("_ai_qa_queue_month_due(", sample)
        queue = self._function("_ai_qa_queue_month")
        self.assertIn("ai_qa_digest_pool.submit(ai_qa_month_digest_job", queue)
        self.assertIn("AI_QA_MONTH_QUEUED.update((code, month_key) for code in _ai_qa_digest_departments())",
                      queue)
        month_job = self._function("ai_qa_month_digest_job")
        self.assertIn("AI_QA_MONTH_QUEUED.discard((code, month_key))", month_job)
        self.assertIn("id='ai_qa_facts_backfill'", self.routes)
        self.assertIn("qa_facts._POOL.submit(qa_facts.backfill)", self._function("ai_qa_facts_backfill_job"))
        digests = self._function("api_ai_qa_digests")
        self.assertIn("qa_monthly.known(department", digests)
        self.assertIn('"months": months', digests)

    def test_schema_has_the_tables(self):
        schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8")
        for table in ("ai_qa_day_facts", "ai_qa_month_digests", "ai_qa_period_messages"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)
        self.assertIn("UNIQUE (department_code, facts_day)", schema)
        self.assertIn("UNIQUE (department_code, digest_month)", schema)
        self.assertIn("idx_ai_qa_period_messages_thread", schema)

    def test_frontend_passes_the_chart_attribute(self):
        markup = (ROOT / "src" / "components" / "call_qa" / "AiMarkup.jsx").read_text(encoding="utf-8")
        block = re.search(r"export const MARKUP_ATTRS = \[(.*?)\];", markup, re.S).group(1)
        self.assertIn("'data-qa-chart'", block)
        view = (ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx").read_text(encoding="utf-8")
        self.assertIn("usePeriod(", view)


if __name__ == "__main__":
    unittest.main()
