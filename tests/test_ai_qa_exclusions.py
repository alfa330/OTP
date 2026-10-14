"""«Исключить оценку» в карточке ИИ-оценки (call_qa.exclusions).

Что сторожим:
* исключённый разговор пропадает из ВСЕХ выборок раздела — условие стоит в
  _SUBJECT_EXISTS, а метрики, собранные мимо него, проверяют его сами;
* причина обязательна (категория или свои слова; у «Другое» — свои слова);
* разбор причин моделью не уводит причину в чужую или выдуманную категорию и не
  плодит дубли категорий;
* ручки — за гардом раздела, со скоупом СВ и отделом субъекта; наблюдателю
  «Маркетинга» исключать нельзя.
Проверка на боевых данных (временные таблицы на сессии codex_readonly) — в
описании коммита; здесь — то, что должно держаться без базы.
"""
from __future__ import annotations

import ast
import inspect
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import source_cache
from call_qa import api, exclusions

ROOT = Path(__file__).resolve().parents[1]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class SubjectPredicateTests(unittest.TestCase):
    """Одно место вместо перечня выборок: так же устроены тестовые номера."""

    def test_subject_exists_skips_active_exclusions(self):
        self.assertTrue(api._SUBJECT_EXISTS.startswith(api._SUBJECT_PRESENT))
        self.assertIn(api._SUBJECT_NOT_EXCLUDED, api._SUBJECT_EXISTS)
        self.assertIn("qx.restored_at IS NULL", api._SUBJECT_NOT_EXCLUDED)
        self.assertIn("qx.subject_kind = rc.subject_kind AND qx.call_id = rc.call_id",
                      api._SUBJECT_NOT_EXCLUDED)
        # Список исключённых строится тем же отбором, но по исключённым — ему
        # нужно «субъект есть» без условия «не исключён».
        self.assertNotIn("ai_qa_exclusions", api._SUBJECT_PRESENT)

    def test_metrics_built_past_subject_exists_skip_exclusions_too(self):
        self.assertIn(api._not_excluded_sql("t.subject_kind", "t.call_id"), api._RAW_BENCHMARK_SQL)
        reviewed = inspect.getsource(api._reviewed_metrics)
        self.assertIn("_not_excluded_sql(\"COALESCE(m.subject_kind, 'call')\"", reviewed)
        self.assertIn('"m.call_id"', reviewed)
        # Очередь «Обзора» без скоупа считается мимо _SUBJECT_EXISTS.
        self.assertIn('_SUBJECT_NOT_TEST + " AND " + _SUBJECT_NOT_EXCLUDED',
                      inspect.getsource(api.stats))

    def test_random_pickers_never_offer_an_excluded_subject_again(self):
        self.assertIn("_not_excluded_sql(\"'call'\", \"c.id\")", inspect.getsource(api.random_call))
        self.assertIn("_not_excluded_sql(f\"'{subject_kind}'\", \"t.id\")",
                      inspect.getsource(api.random_chat_episode))

    def test_excluded_list_is_the_same_selection_inverted(self):
        where = inspect.getsource(api._excluded_where)
        self.assertIn("_SUBJECT_PRESENT", where)
        self.assertNotIn("_SUBJECT_EXISTS", where)
        for piece in ("_direction_predicate(", "_subject_kind_predicate(", "_list_filters_predicate("):
            with self.subTest(piece=piece):
                self.assertIn(piece, where)
        self.assertIn("ex.restored_at IS NULL", api._EXCLUSION_JOIN)
        # «· N исключено» — тем же отбором, что сам список дней.
        days = inspect.getsource(api.evaluations_days)
        self.assertIn("_excluded_total(cur, allowed_direction_ids, subject_kind, department, filters)", days)
        self.assertIn('"excluded": excluded', days)

    def test_search_marks_excluded_instead_of_hiding_them(self):
        """Исключённый ищут как раз затем, чтобы вернуть, — поиск его находит."""
        src = inspect.getsource(api.find_subjects)
        self.assertIn("excluded_keys(cur,", src)
        self.assertIn('item["excluded"]', src)

    def test_excluded_keys_ignores_kinds_outside_the_section(self):
        cursor = mock.Mock()
        cursor.fetchall.return_value = [("imported_call", 7)]
        found = api.excluded_keys(cursor, [("cdr_touch", "abc"), ("imported_call", 7), ("call", None)])
        self.assertEqual(found, {("imported_call", 7)})
        sql, params = cursor.execute.call_args.args
        self.assertEqual(params, (["imported_call"], [7]))
        self.assertIn("restored_at IS NULL", sql)
        self.assertEqual(api.excluded_keys(mock.Mock(), [("cdr_touch", 1)]), set())


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = _squash((ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8"))

    def test_tables_and_the_active_unique_index(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS ai_qa_exclusion_categories", self.schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS ai_qa_exclusions", self.schema)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_qa_exclusions_active "
                      "ON ai_qa_exclusions (subject_kind, call_id) WHERE restored_at IS NULL", self.schema)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_qa_exclusion_categories_title "
                      "ON ai_qa_exclusion_categories (department_code, lower(title))", self.schema)
        # Причина обязательна и на уровне базы.
        self.assertIn("CHECK (category_id IS NOT NULL OR reason IS NOT NULL)", self.schema)

    def test_upserts_target_exactly_those_indexes(self):
        """ON CONFLICT выводит индекс по колонкам и условию: разойдись они —
        вставка падала бы ошибкой, а не тихо пропускала дубль."""
        self.assertIn("ON CONFLICT (subject_kind, call_id) WHERE restored_at IS NULL DO NOTHING",
                      _squash(inspect.getsource(exclusions.exclude)))
        self.assertIn("ON CONFLICT (department_code, lower(title)) DO NOTHING",
                      _squash(inspect.getsource(exclusions._apply_plan)))


class _Cursor:
    """Курсор с ответами по порядку запросов; запоминает SQL."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.sql = []
        self.rowcount = 1
        self._last = None

    def execute(self, sql, params=None):
        self.sql.append((_squash(sql), params))
        if sql.strip().upper().startswith("SET "):
            self._last = None
            return
        self._last = self.answers.pop(0) if self.answers else None

    def fetchone(self):
        value = self._last
        return value[0] if isinstance(value, list) and value else value

    def fetchall(self):
        value = self._last
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, cursor):
        self.cur = cursor
        self.autocommit = False

    def cursor(self):
        return self.cur

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReasonTests(unittest.TestCase):
    def _exclude(self, cursor=None, **kwargs):
        args = {"subject_kind": "imported_call", "call_id": 8930, "department": "op",
                "direction_id": 73, "user_id": 5, **kwargs}
        if cursor is None:
            with mock.patch.object(exclusions.config, "connect_rw",
                                   side_effect=AssertionError("до базы дойти не должно")):
                return exclusions.exclude(**args)
        with mock.patch.object(exclusions.config, "connect_rw", return_value=_Conn(cursor)):
            return exclusions.exclude(**args)

    def test_reason_is_required(self):
        for kwargs in ({}, {"reason": ""}, {"reason": "  ок  "}, {"reason": None, "category_id": ""}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(exclusions.ExclusionError) as ctx:
                    self._exclude(**kwargs)
                self.assertEqual(ctx.exception.status, 400)

    def test_department_and_category_must_be_sane(self):
        with self.assertRaises(exclusions.ExclusionError):
            self._exclude(department="", reason="ошибся номером")
        with self.assertRaises(exclusions.ExclusionError):
            self._exclude(category_id="abc")

    def test_reason_is_squeezed_and_capped(self):
        self.assertEqual(exclusions.normalise_reason("  ошибся\n\n  номером  "), "ошибся номером")
        self.assertEqual(len(exclusions.normalise_reason("x" * 900)), exclusions.REASON_MAX)

    def test_foreign_category_is_refused(self):
        cursor = _Cursor([None])   # категории в отделе нет
        with self.assertRaises(exclusions.ExclusionError) as ctx:
            self._exclude(cursor, category_id=12)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "category_missing"))
        self.assertIn("department_code = %s", cursor.sql[1][0])
        self.assertEqual(cursor.sql[1][1], (12, "op"))
        self.assertFalse(any("INSERT INTO ai_qa_exclusions" in sql for sql, _ in cursor.sql))

    def test_other_bucket_needs_own_words(self):
        with self.assertRaises(exclusions.ExclusionError) as ctx:
            self._exclude(_Cursor([("Другое",)]), category_id=3)
        self.assertIn("Другое", str(ctx.exception))
        state_row = (1, 3, "Другое", "picked", "звонил в другую службу", 5, "Имя", "14.10.2026 12:00", 0)
        state = self._exclude(_Cursor([("Другое",), (41,), state_row]), category_id=3,
                              reason="звонил в другую службу")
        self.assertEqual(state["category"], "Другое")

    def test_picked_category_is_enough(self):
        state_row = (2, 7, "Ошибся номером", "picked", None, 5, "Имя", "14.10.2026 12:00", 0)
        cursor = _Cursor([("Ошибся номером",), (42,), state_row])
        state = self._exclude(cursor, category_id=7)
        insert = next(params for sql, params in cursor.sql if "INSERT INTO ai_qa_exclusions" in sql)
        self.assertEqual(insert, ("imported_call", 8930, "op", 73, 7, "picked", None, 5))
        self.assertEqual((state["category_id"], state["category_pending"]), (7, False))

    def test_second_exclusion_is_refused_with_the_first_ones_reason(self):
        state_row = (3, None, None, None, "тест", 9, "Первый", "14.10.2026 11:00", 0)
        with self.assertRaises(exclusions.ExclusionError) as ctx:
            self._exclude(_Cursor([None, state_row]), reason="ошибся номером")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "already_excluded"))
        self.assertEqual(ctx.exception.state["excluded_by"], "Первый")
        self.assertTrue(ctx.exception.state["category_pending"])

    def test_restore_reports_whether_there_was_anything(self):
        cursor = _Cursor()
        cursor.rowcount = 0
        with mock.patch.object(exclusions.config, "connect_rw", return_value=_Conn(cursor)):
            self.assertFalse(exclusions.restore(subject_kind="call", call_id=1, user_id=5))
        self.assertIn("restored_at IS NULL", cursor.sql[0][0])


class CategorizeTests(unittest.TestCase):
    def test_titles_are_normalised(self):
        cases = {"  тестовый   звонок. ": "Тестовый звонок", "«ошибся номером»": "Ошибся номером",
                 "": "", "Спам!": "Спам"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(exclusions.normalise_title(raw), expected)
        self.assertLessEqual(len(exclusions.normalise_title("слово " * 40)), exclusions.TITLE_MAX)
        self.assertTrue(exclusions.is_other_title("другое."))
        self.assertFalse(exclusions.is_other_title("Ошибся номером"))

    def test_plan_ignores_foreign_ids_and_invented_categories(self):
        answer = {"items": [
            {"id": 5, "category_id": 3, "new_title": "", "new_description": ""},
            {"id": 6, "category_id": 0, "new_title": "спам.", "new_description": "рассылки"},
            {"id": 99, "category_id": 3, "new_title": "", "new_description": ""},     # чужая причина
            {"id": 7, "category_id": 42, "new_title": "", "new_description": ""},     # выдуманная категория
            {"id": 5, "category_id": 0, "new_title": "Другое", "new_description": ""},  # повтор id
            "мусор",
        ]}
        plan = exclusions.plan_assignments(answer, {5, 6, 7}, {3})
        self.assertEqual(plan, {5: ("existing", 3), 6: ("new", "Спам", "рассылки")})
        self.assertEqual(exclusions.plan_assignments({"items": None}, {1}, set()), {})
        self.assertEqual(exclusions.plan_assignments("not json", {1}, set()), {})

    def test_same_new_title_makes_one_category(self):
        cursor = _Cursor([(11,)])   # INSERT … RETURNING id первой новой категории
        result = exclusions._apply_plan(cursor, "op", {
            1: ("new", "Тестовый звонок", "проверка"),
            2: ("new", "тестовый звонок", "проверка связи"),
            3: ("existing", 4),
        })
        inserts = [sql for sql, _ in cursor.sql if "INSERT INTO ai_qa_exclusion_categories" in sql]
        self.assertEqual(len(inserts), 1)
        updates = [params for sql, params in cursor.sql if sql.startswith("UPDATE ai_qa_exclusions")]
        self.assertEqual([(p[0], p[1], p[2]) for p in updates], [(11, "ai", 1), (11, "ai", 2), (4, "ai", 3)])
        self.assertEqual(result["created"], 1)
        # Причину, которую успел разобрать другой проход, не переписываем.
        self.assertTrue(all("category_id IS NULL" in sql for sql, _ in cursor.sql
                            if sql.startswith("UPDATE ai_qa_exclusions")))

    def test_category_created_in_parallel_is_reused(self):
        cursor = _Cursor([None, (17,)])   # ON CONFLICT DO NOTHING → берём существующую
        exclusions._apply_plan(cursor, "op", {1: ("new", "Спам", "")})
        self.assertIn("lower(title) = lower(%s)", cursor.sql[1][0])
        self.assertEqual(cursor.sql[-1][1][0], 17)

    def test_prompt_lists_categories_with_examples_and_reasons(self):
        prompt = exclusions.build_prompt(
            "Отдел продаж",
            [{"id": 3, "title": "Ошибся номером", "description": "Позвонили не туда",
              "examples": ["хотел в диспетчерскую"]}],
            [{"id": 41, "reason": "автоответчик", "subject_kind": "imported_call"},
             {"id": 42, "reason": "спам", "subject_kind": "wz_episode"}])
        self.assertIn("ОТДЕЛ: Отдел продаж", prompt)
        self.assertIn("[3] Ошибся номером — Позвонили не туда. Примеры причин: «хотел в диспетчерскую»", prompt)
        self.assertIn("#41 (звонок): «автоответчик»", prompt)
        self.assertIn("#42 (переписка): «спам»", prompt)
        self.assertIn("(пока нет — заведи первые)", exclusions.build_prompt("ОП", [], []))
        # Формат ответа описан в системном промпте: у Z.ai схема в запрос не уходит.
        self.assertIn('"category_id"', exclusions._SYSTEM)
        self.assertIn("Другое", exclusions._SYSTEM)

    def test_attempt_is_counted_before_the_model_call(self):
        """Упади модель или процесс посреди разбора — строка не просит вызов вечно."""
        cursor = _Cursor([(True,), [(41, "автоответчик", "imported_call")], [], ("Отдел продаж",), None])
        calls = []

        def model(system, user):
            calls.append([sql for sql, _ in cursor.sql])
            raise RuntimeError("Z.ai 429")

        with mock.patch.object(exclusions.config, "connect_rw", return_value=_Conn(cursor)):
            with self.assertRaises(RuntimeError):
                exclusions.categorize_pending("op", model_call=model)
        before_model = calls[0]
        self.assertTrue(any("categorize_attempts = categorize_attempts + 1" in sql for sql in before_model))
        self.assertIn("pg_advisory_unlock", cursor.sql[-1][0])
        self.assertFalse(any(sql.startswith("UPDATE ai_qa_exclusions SET category_id") for sql, _ in cursor.sql))

    def test_department_busy_elsewhere_is_skipped(self):
        cursor = _Cursor([(False,)])
        model = mock.Mock()
        with mock.patch.object(exclusions.config, "connect_rw", return_value=_Conn(cursor)):
            self.assertEqual(exclusions.categorize_pending("op", model_call=model)["pending"], 0)
        model.assert_not_called()
        self.assertEqual(len([sql for sql, _ in cursor.sql if not sql.startswith("SET ")]), 1)

    def test_nothing_pending_means_no_model_call(self):
        cursor = _Cursor([(True,), [], None])
        model = mock.Mock()
        with mock.patch.object(exclusions.config, "connect_rw", return_value=_Conn(cursor)):
            exclusions.categorize_pending("op", model_call=model)
        model.assert_not_called()

    def test_scheduler_runs_one_pass_per_department_at_a_time(self):
        """Просьба до начала прохода сливается с ним (проход читает все причины в
        начале); просьба посреди прохода — ещё один проход после него."""
        started = []

        def during_first_pass(department):
            started.append(department)
            if len(started) == 1:
                self.assertFalse(exclusions.schedule_categorize("op"))
            return {}

        with mock.patch.object(exclusions, "_executor") as executor, \
                mock.patch.object(exclusions, "categorize_pending", side_effect=during_first_pass):
            exclusions._running.clear(); exclusions._again.clear()
            self.assertTrue(exclusions.schedule_categorize("OP"))
            self.assertFalse(exclusions.schedule_categorize("op"))   # ещё не начался — тот же проход
            executor.submit.call_args.args[0]()
            self.assertEqual(started, ["op", "op"])
            self.assertEqual(executor.submit.call_count, 1)
            self.assertNotIn("op", exclusions._running)
            self.assertNotIn("op", exclusions._again)
            self.assertFalse(exclusions.schedule_categorize(""))

    def test_scheduler_stops_on_model_failure_and_frees_the_department(self):
        with mock.patch.object(exclusions, "_executor") as executor, \
                mock.patch.object(exclusions, "categorize_pending", side_effect=RuntimeError("сеть")):
            exclusions._running.clear(); exclusions._again.clear()
            exclusions.schedule_categorize("szov")
            with self.assertLogs(level="ERROR"):
                executor.submit.call_args.args[0]()
            self.assertNotIn("szov", exclusions._running)
            self.assertTrue(exclusions.schedule_categorize("szov"))
            exclusions._running.clear()


class RouteTests(unittest.TestCase):
    NAMES = {"api_ai_qa_exclusion_categories", "api_ai_qa_exclusion", "api_ai_qa_exclusions",
             "_ai_qa_exclusion_subject", "api_ai_qa_call"}

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        # Только нужные функции: get_source_segment режет строки файла заново на
        # каждый вызов, и по всем трём тысячам функций это минуты.
        cls.functions = {
            node.name: ast.get_source_segment(cls.src, node)
            for node in source_cache.parse(cls.src).body
            if isinstance(node, ast.FunctionDef) and node.name in cls.NAMES
        }

    def test_routes_exist_behind_the_section_guard(self):
        routes = {"/api/ai-qa/exclusion-categories": "api_ai_qa_exclusion_categories",
                  "/api/ai-qa/exclusion": "api_ai_qa_exclusion",
                  "/api/ai-qa/exclusions": "api_ai_qa_exclusions"}
        for route, name in routes.items():
            with self.subTest(route=route):
                self.assertIn(f"@app.route('{route}'", self.src)
                self.assertIn("_ai_qa_guard()", self.functions[name])

    def test_write_checks_scope_and_the_subjects_own_department(self):
        helper = self.functions["_ai_qa_exclusion_subject"]
        self.assertIn("call_in_scope(call_id, _ai_qa_direction_scope(requester_id), subject_kind)", helper)
        self.assertIn("_ai_qa_direction_department_allowed(requester_id, direction_id)", helper)
        route = self.functions["api_ai_qa_exclusion"]
        self.assertIn("_ai_qa_exclusion_subject(requester_id, subject_kind, call_id)", route)
        # Свои слова без категории — разбор фоном, человек модель не ждёт.
        self.assertIn("schedule_categorize(department)", route)

    def test_lists_resolve_the_requested_department(self):
        for name in ("api_ai_qa_exclusions", "api_ai_qa_exclusion_categories"):
            with self.subTest(route=name):
                self.assertIn("_ai_qa_requested_department(requester_id)", self.functions[name])
        self.assertIn("department=department", self.functions["api_ai_qa_exclusions"])
        self.assertIn("allowed_direction_ids=_ai_qa_direction_scope(requester_id)",
                      self.functions["api_ai_qa_exclusions"])

    def test_marketing_observer_cannot_exclude(self):
        """Право действий наблюдателю выдано поимённым перечнем ручек — новая
        ручка записи туда сама не попадает."""
        block = self.src[self.src.index("AI_QA_OBSERVER_ACTION_ENDPOINTS = frozenset({"):]
        block = block[:block.index("})")]
        self.assertNotIn("api_ai_qa_exclusion", block)
        card = self.functions["api_ai_qa_call"]
        self.assertIn('payload["can_exclude"] = not _is_marketing_observer(requester_id)', card)
        self.assertIn('payload["exclusion"] = _qa_exclusions.state_for(subject, call_id)', card)
        self.assertIn('payload["department"]', card)

    def test_subject_helper_refuses_foreign_scope_and_unknown_department(self):
        def load(scope_ok, dept_ok, code):
            namespace = {
                "jsonify": lambda body: body,
                "_ai_qa_direction_scope": lambda uid: [73],
                "_ai_qa_direction_department_allowed": lambda uid, did: dept_ok,
                "_ai_qa_direction_department_code": lambda did: code,
            }
            node = next(item for item in source_cache.parse(self.src).body
                        if isinstance(item, ast.FunctionDef) and item.name == "_ai_qa_exclusion_subject")
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            fake_api = SimpleNamespace(call_in_scope=lambda *a: scope_ok, subject_direction_id=lambda *a: 73)
            with mock.patch.dict("sys.modules", {"call_qa.api": fake_api}):
                exec(compile(module, "<exclusion-subject>", "exec"), namespace)
                return namespace["_ai_qa_exclusion_subject"](5, "call", 1)

        self.assertEqual(load(True, True, "op"), ((73, "op"), None))
        self.assertEqual(load(False, True, "op")[1][1], 403)
        self.assertEqual(load(True, False, "op")[1][1], 403)
        self.assertEqual(load(True, True, "")[1][1], 400)


if __name__ == "__main__":
    unittest.main()
