"""Шкала, заданная самому звонку из АТС, и указания направления для ИИ.

Звонки обзвона по подписанию АВР оцениваются по своей мониторинговой шкале
(imported_calls.qa_direction_id), а операторы остаются в «Основе». Здесь сторожится,
что шкалу выбирает одна функция везде, видимость остаётся по оператору, журнал другой
шкалы не подмешивается, а указания меняют промпт ТОЛЬКО у направления, где заданы.
"""
import ast
import re
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        for needle, result in self.conn.replies:
            if needle in sql:
                if isinstance(result, Exception):
                    raise result
                self._rows = list(result)
                return
        self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, replies):
        self.replies = replies
        self.executed = []

    def cursor(self):
        return _Cursor(self)

    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _UndefinedTable(Exception):
    pgcode = "42P01"


def _source_function(path, name):
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} не найдена в {path}")


class GuidancePromptTests(unittest.TestCase):
    def setUp(self):
        from call_qa import config
        from call_qa.evaluation import evaluator
        self.config = config
        self.evaluator = evaluator
        self.criteria = [{"idx": 0, "name": "Предложение подписать прямо в звонке", "description": "x",
                          "weight": 20, "is_critical": False, "deficiency": None}]

    def test_without_guidance_the_prompt_is_byte_for_byte_the_same(self):
        for kind in self.config.SUBJECT_KINDS:
            for department in (None, "op", "szov", "tez"):
                with self.subTest(kind=kind, department=department):
                    base = self.evaluator.build_system(self.criteria, kind, department)
                    self.assertEqual(base, self.evaluator.build_system(
                        self.criteria, kind, department, guidance=None))
                    self.assertEqual(base, self.evaluator.build_system(
                        self.criteria, kind, department, guidance="  \r\n "))

    def test_guidance_is_appended_after_the_template_and_changes_the_hash(self):
        from call_qa.evaluation.fingerprint import content_hash
        base = self.evaluator.build_system(self.criteria, "imported_call", "szov")
        with_guidance = self.evaluator.build_system(
            self.criteria, "imported_call", "szov", guidance="Правило 1\r\nПравило 2  ")
        self.assertTrue(with_guidance.startswith(base))
        tail = with_guidance[len(base):]
        self.assertIn(self.evaluator._GUIDANCE_HEADER, tail)
        self.assertIn("Правило 1\nПравило 2", tail)
        self.assertNotIn("\r", tail)
        self.assertNotEqual(content_hash(base), content_hash(with_guidance))

    def test_eval_body_sends_the_guidance_of_the_direction(self):
        source = _source_function("call_qa/evaluation/evaluator.py", "build_eval_body")
        self.assertIn('guidance=(direction or {}).get("guidance")', source)


class LoadDirectionGuidanceTests(unittest.TestCase):
    def setUp(self):
        from call_qa.evaluation import criteria
        self.criteria = criteria

    def _load(self, guidance_reply):
        conn = _Conn([
            ("FROM qa_direction_guidance", guidance_reply),
            ("SELECT id, name, criteria, canonical_id FROM directions",
             [(90, "Обзвон — подписание АВР", [{"name": "A", "value": "v", "weight": 100,
                                                  "isCritical": False, "deficiency": None}], None)]),
        ])
        with mock.patch.object(self.criteria.config, "connect_ro", return_value=conn):
            return self.criteria.load_direction(90), conn

    def test_guidance_is_read_by_canonical_id(self):
        direction, conn = self._load([("Указания",)])
        self.assertEqual(direction["guidance"], "Указания")
        guidance_queries = [p for s, p in conn.executed if "qa_direction_guidance" in s]
        self.assertEqual(guidance_queries, [(90,)])

    def test_missing_table_means_no_guidance(self):
        direction, _ = self._load(_UndefinedTable("relation does not exist"))
        self.assertEqual(direction["guidance"], "")

    def test_scale_hash_does_not_depend_on_guidance(self):
        first, _ = self._load([("Указания",)])
        second, _ = self._load([])
        self.assertEqual(first["scale_hash"], second["scale_hash"])


class ScaleSelectionWiringTests(unittest.TestCase):
    """Шкалу субъекта выбирает subjects.scale_direction_id — во всех местах."""

    def setUp(self):
        from call_qa import api, config, subjects
        self.api = api
        self.config = config
        self.subjects = subjects

    def test_evaluation_loads_the_scale_marked_on_the_call(self):
        class _Stop(Exception):
            pass

        seen = {}

        def load_direction(direction_id):
            seen["direction_id"] = direction_id
            raise _Stop()

        subject = {"kind": self.config.SUBJECT_IMPORTED_CALL, "id": 5, "direction_id": 70,
                   "qa_direction_id": 90, "audio_path": "b/x.mp3"}
        resolver = mock.Mock(return_value={"duration_ms": 60000, "asm": {"text": "x"}, "lines": [],
                                           "transcript_cache_id": None, "transcript_hash": "h"})
        with mock.patch.object(self.api.subjects_mod, "load", return_value=subject), \
                mock.patch.object(self.api.subjects_mod, "require_evaluable"), \
                mock.patch.object(self.api.subjects_mod, "require_recording_length"), \
                mock.patch.dict(self.api._SOURCE_RESOLVERS,
                                {self.config.SUBJECT_IMPORTED_CALL: resolver}), \
                mock.patch.object(self.api.criteria_mod, "load_direction", side_effect=load_direction):
            with self.assertRaises(_Stop):
                self.api._evaluate_and_cache(5, "m", False, subject_kind=self.config.SUBJECT_IMPORTED_CALL)
        self.assertEqual(seen["direction_id"], 90)

    def test_stale_flag_uses_the_scale_from_the_queue_row(self):
        built = []

        def context(direction_id):
            built.append(direction_id)
            return None

        item = {"id": 5, "subject": self.config.SUBJECT_IMPORTED_CALL, "_direction_id": 90,
                "_run_fp": "fp", "_run_components": {"source_transcript_hash": "t"}}
        with mock.patch.object(self.api, "_direction_identity_context", side_effect=context):
            self.api._flag_stale_evaluations([item])
        self.assertEqual(built, [90])
        self.assertIsNone(item["stale"])

    def test_queue_row_direction_is_the_scale(self):
        source = _source_function("call_qa/api.py", "_queue_items")
        self.assertIn("{_SUBJECT_SCALE_DIRECTION}, {run_cols}", source)
        self.assertIn("SELECT rc.call_id, {_SUBJECT_DIRECTION_NAME}", source)
        # Видимость — по оператору.
        self.assertIn("_direction_predicate(\n        cur, allowed_direction_ids, department, _SUBJECT_DIRECTION)",
                      source)

    def test_filter_by_direction_uses_the_scale_and_scope_uses_the_operator(self):
        self.assertIn("ic.qa_direction_id", self.api._SUBJECT_SCALE_DIRECTION)
        self.assertNotIn("qa_direction_id", self.api._SUBJECT_DIRECTION)
        source = _source_function("call_qa/api.py", "_list_filters_predicate")
        self.assertIn("{_SUBJECT_SCALE_DIRECTION} = ANY(%s)", source)
        self.assertNotIn("qa_direction_id", self.api._SCOPE_SQL[self.config.SUBJECT_IMPORTED_CALL])

    def test_payload_carries_the_scale_name(self):
        source = _source_function("call_qa/api.py", "_evaluate_and_cache")
        self.assertIn('"direction": subject.get("qa_direction") or subject.get("direction")', source)


class JournalSameScaleTests(unittest.TestCase):
    def test_condition_passes_calls_without_a_mark(self):
        from call_qa.subjects import journal_same_scale_sql
        sql = journal_same_scale_sql("c", "ic")
        self.assertTrue(sql.startswith("(ic.qa_direction_id IS NULL OR EXISTS ("))
        self.assertIn("jd.id = c.direction_id", sql)
        self.assertIn("COALESCE(jd.canonical_id, jd.id) = COALESCE(qd.canonical_id, qd.id)", sql)

    def test_every_journal_link_of_an_imported_call_checks_the_scale(self):
        from call_qa import api, config
        from call_qa.digest import data
        self.assertIn("qa_direction_id", api._SUBJECT_HUMAN_SCORE)
        self.assertIn("qa_direction_id", api._HUMAN_REVIEW_SQL[config.SUBJECT_IMPORTED_CALL])
        self.assertIn("qa_direction_id", api._RAW_BENCHMARK_SQL)
        self.assertIn("qa_direction_id", data._JOURNAL_BATCH_SQL[config.SUBJECT_IMPORTED_CALL])
        loader = _source_function("call_qa/subjects.py", "_load_imported_call")
        self.assertIn('journal_same_scale_sql("c", "ic")', loader)


class DirectionGuidanceApiTests(unittest.TestCase):
    def setUp(self):
        from call_qa import api
        self.api = api

    def test_set_normalises_text_and_upserts_by_canonical_id(self):
        rw = _Conn([("SELECT COALESCE(canonical_id, id) FROM directions", [(90,)])])
        with mock.patch.object(self.api.config, "connect_rw", return_value=rw), \
                mock.patch.object(self.api, "direction_guidance_get",
                                  side_effect=lambda d: {"direction_id": d}):
            self.api.direction_guidance_set(91, "  Правило\r\n ", 7)
        inserts = [(s, p) for s, p in rw.executed if "INSERT INTO qa_direction_guidance" in s]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1], (90, "Правило", 7))

    def test_empty_text_deletes(self):
        rw = _Conn([("SELECT COALESCE(canonical_id, id) FROM directions", [(90,)])])
        with mock.patch.object(self.api.config, "connect_rw", return_value=rw), \
                mock.patch.object(self.api, "direction_guidance_get", return_value={}):
            self.api.direction_guidance_set(90, "   ", 7)
        self.assertTrue(any("DELETE FROM qa_direction_guidance" in s for s, _ in rw.executed))

    def test_too_long_text_is_refused_before_any_write(self):
        with mock.patch.object(self.api.config, "connect_rw") as connect:
            with self.assertRaises(ValueError):
                self.api.direction_guidance_set(90, "x" * (self.api.GUIDANCE_MAX_CHARS + 1), 7)
        connect.assert_not_called()


class RouteWiringTests(unittest.TestCase):
    """Ручки и скоуп в bot_schedule2 — по тексту функций (модуль тянет весь Flask)."""

    def test_human_review_takes_the_same_scale(self):
        body = _source_function("bot_schedule2.py", "api_ai_qa_human_review")
        self.assertIn("_qa_subjects.scale_direction_id(subject)", body)

    def test_supervisor_scope_gets_scale_only_directions_of_the_department(self):
        body = _source_function("bot_schedule2.py", "_ai_qa_direction_scope")
        self.assertIn("db.get_department_scale_direction_ids(int(dept_id))", body)
        # Пустой скоуп СВ не превращается в шкалы: видеть ему нечего.
        self.assertRegex(body, r"if not ids:\s+return ids")

    def test_department_scale_directions_query(self):
        body = _source_function("database.py", "get_department_scale_direction_ids")
        for needle in ("d.department_id = %s", "d.is_active = TRUE", "d.canonical_id IS NULL",
                       "ic.qa_direction_id = d.id"):
            self.assertIn(needle, body)

    def test_guidance_route_reads_for_scope_and_writes_only_for_heads(self):
        body = _source_function("bot_schedule2.py", "api_ai_qa_direction_guidance")
        self.assertIn("_ai_qa_guard()", body)
        self.assertIn("direction_in_scope(direction_id, scope)", body)
        self.assertIn("_ai_qa_direction_department_allowed(requester_id, direction_id)", body)
        self.assertIn("if scope is not None:", body)
        self.assertIn('"can_edit": scope is None', body)

    def test_pull_does_not_narrow_people_by_a_scale_only_direction(self):
        body = _source_function("bot_schedule2.py", "_ai_qa_department_operator_candidates")
        self.assertIn("OR NOT EXISTS (", body)

    def test_column_is_created_idempotently(self):
        source = (ROOT / "database.py").read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE imported_calls ADD COLUMN IF NOT EXISTS qa_direction_id INTEGER "
                      "REFERENCES directions(id) ON DELETE SET NULL", source)
        schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8")
        self.assertRegex(schema, re.compile(r"CREATE TABLE IF NOT EXISTS qa_direction_guidance \("))


if __name__ == "__main__":
    unittest.main()
