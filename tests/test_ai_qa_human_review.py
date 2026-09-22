# -*- coding: utf-8 -*-
"""«Моя оценка» в карточке ИИ-оценки (call_qa.human_review + ручки раздела).

Что здесь стережём:

* формула балла и набор вердиктов повторяют журнал (main.jsx totalScore /
  CriterionCard): иначе один разговор получал бы разный балл в карточке и в
  «Журнале оценок»;
* оценка, которая уходит в журнал, полная, а калибровочная — может быть
  частичной, но комментарий к ошибке обязателен всегда;
* «как у ИИ» переводит вердикты ИИ в вердикты человека (Incorrect по
  критическому → Error, Pending → пусто);
* новая таблица знает все виды субъекта, списки видят балл человека из
  карточки, ручки проверяют отдел и скоуп.
"""
from pathlib import Path
import ast
import re
import unittest
from unittest import mock

from tests import source_cache

from call_qa import human_review as hr

ROOT = Path(__file__).resolve().parents[1]


def _crit(idx, weight=10, *, critical=False, deficiency=None, name=None):
    return {"idx": idx, "name": name or f"Критерий {idx}", "weight": weight,
            "is_critical": critical, "deficiency": deficiency}


SCALE = [
    _crit(0, 40),
    _crit(1, 30, deficiency={"weight": 15, "description": "частично"}),
    _crit(2, 30),
    _crit(3, None, critical=True),
]


class VerdictRulesTests(unittest.TestCase):
    def test_allowed_verdicts_mirror_journal_buttons(self):
        self.assertEqual(hr.allowed_verdicts(SCALE[0]), ("Correct", "Incorrect", "N/A"))
        self.assertEqual(hr.allowed_verdicts(SCALE[1]), ("Correct", "Incorrect", "Deficiency", "N/A"))
        # У критического критерия нет «Ошибки» и «Недочёта» — только критическая ошибка.
        self.assertEqual(hr.allowed_verdicts(SCALE[3]), ("Correct", "N/A", "Error"))

    def test_verdict_from_ai_maps_critical_incorrect_to_error(self):
        self.assertEqual(hr.verdict_from_ai(SCALE[3], "Incorrect"), "Error")
        self.assertEqual(hr.verdict_from_ai(SCALE[0], "Incorrect"), "Incorrect")
        self.assertEqual(hr.verdict_from_ai(SCALE[0], "Correct"), "Correct")
        self.assertEqual(hr.verdict_from_ai(SCALE[1], "Deficiency"), "Deficiency")
        # Недочёт по критерию без недочёта в шкале — это ошибка, а не молчаливый Correct.
        self.assertEqual(hr.verdict_from_ai(SCALE[0], "Deficiency"), "Incorrect")
        # Pending — не вердикт.
        self.assertIsNone(hr.verdict_from_ai(SCALE[0], "Pending"))
        self.assertIsNone(hr.verdict_from_ai(SCALE[0], None))

    def test_normalise_scores_accepts_journal_spelling_and_rejects_foreign(self):
        scores = hr.normalise_scores(SCALE, ["correct", "Deficiency", None, "Error"])
        self.assertEqual(scores, ["Correct", "Deficiency", None, "Error"])
        with self.assertRaisesRegex(ValueError, "недопустим"):
            hr.normalise_scores(SCALE, ["Correct", "Correct", "Correct", "Incorrect"])
        with self.assertRaisesRegex(ValueError, "неизвестный вердикт"):
            hr.normalise_scores(SCALE, ["Хорошо", None, None, None])
        # Короче шкалы — дополняется пустыми; длиннее — карточка устарела.
        self.assertEqual(hr.normalise_scores(SCALE, ["Correct"]), ["Correct", None, None, None])
        with self.assertRaisesRegex(ValueError, "больше, чем критериев"):
            hr.normalise_scores(SCALE, ["Correct"] * 5)


class ScoreTests(unittest.TestCase):
    def test_score_mirrors_journal_formula(self):
        self.assertEqual(hr.score_of(SCALE, ["Correct", "Correct", "Correct", "Correct"]), 100)
        self.assertEqual(hr.score_of(SCALE, ["Correct", "Correct", "N/A", "N/A"]), 100)
        self.assertEqual(hr.score_of(SCALE, ["Incorrect", "Correct", "Correct", "Correct"]), 60)
        self.assertEqual(hr.score_of(SCALE, ["Correct", "Deficiency", "Correct", "Correct"]), 85)
        self.assertEqual(hr.score_of(SCALE, ["Correct", "Incorrect", "Correct", "Correct"]), 70)

    def test_critical_error_zeroes_the_score(self):
        self.assertEqual(hr.score_of(SCALE, ["Correct", "Correct", "Correct", "Error"]), 0)

    def test_partial_review_has_no_score(self):
        # Частичная сумма читалась бы как низкая оценка.
        self.assertIsNone(hr.score_of(SCALE, ["Correct", None, "Correct", "Correct"]))
        self.assertIsNone(hr.score_of(SCALE, []))


class ValidationTests(unittest.TestCase):
    def test_journal_bound_review_must_be_complete(self):
        problems = hr.validate(SCALE, ["Correct", None, "Correct", None], [""] * 4,
                               complete_required=True)
        self.assertEqual(problems["missing"], [1, 3])
        self.assertIsNotNone(hr.validation_message(problems))

    def test_calibration_review_may_be_partial(self):
        problems = hr.validate(SCALE, ["Correct", None, None, None], [""] * 4,
                               complete_required=False)
        self.assertEqual(problems, {"missing": [], "comments_required": []})
        self.assertIsNone(hr.validation_message(problems))

    def test_comment_is_required_for_errors_even_in_calibration(self):
        problems = hr.validate(SCALE, ["Incorrect", None, None, "Error"], ["", "", "", "почему"],
                               complete_required=False)
        self.assertEqual(problems["comments_required"], [0])
        # Недочёт комментария не требует — как в форме журнала.
        problems = hr.validate(SCALE, [None, "Deficiency", None, None], [""] * 4,
                               complete_required=False)
        self.assertEqual(problems["comments_required"], [])

    def test_empty_review_is_detected(self):
        self.assertTrue(hr.is_empty([None] * 4, [""] * 4, ""))
        self.assertFalse(hr.is_empty([None] * 4, [""] * 4, "заметка"))
        self.assertFalse(hr.is_empty([None, "Correct", None, None], [""] * 4, ""))


class SerialiseTests(unittest.TestCase):
    def test_serialise_reports_completeness(self):
        row = {"id": 7, "scores": ["Correct", None], "criterion_comments": ["", ""], "score": None,
               "comment": None, "comment_visible_to_operator": True, "question_resolved": False,
               "resolved_first_contact": None, "counted_in_quality": False,
               "journal_call_id": None, "updated_at": None}
        out = hr.serialise(row)
        self.assertFalse(out["complete"])
        self.assertEqual(out["source"], "review")
        self.assertEqual(out["comment"], "")
        self.assertIsNone(hr.serialise(None))


class FrontendMirrorTests(unittest.TestCase):
    """Фронт считает балл тем же правилом — иначе человек видел бы один балл до
    сохранения и другой после."""

    def test_js_module_mirrors_the_rules(self):
        js = (ROOT / "src" / "components" / "call_qa" / "humanReview.js").read_text(encoding="utf-8-sig")
        self.assertIn("COMMENT_REQUIRED = new Set([INCORRECT, ERROR])", js)
        self.assertIn("if (criterion?.is_critical) return [CORRECT, NOT_APPLICABLE, ERROR];", js)
        self.assertIn("if (verdict === INCORRECT && criterion?.is_critical) verdict = ERROR;", js)
        self.assertIn("if (criteria.some((c, i) => c.is_critical && scores[i] === ERROR)) return 0;", js)


class SchemaTests(unittest.TestCase):
    def test_table_knows_every_subject_kind_and_one_row_per_reviewer(self):
        from call_qa import config
        schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8")
        block = schema[schema.index("CREATE TABLE IF NOT EXISTS ai_human_reviews"):]
        block = block[:block.index(";")]
        listed = re.search(r"CHECK \(subject_kind IN \(([^)]*)\)\)", block).group(1)
        self.assertEqual({v.strip().strip("'") for v in listed.split(",")}, set(config.SUBJECT_KINDS))
        self.assertIn("UNIQUE (subject_kind, call_id, reviewer_id)", block)
        self.assertIn("journal_call_id", block)


class ApiWiringTests(unittest.TestCase):
    """Карточка и списки видят оценку человека из карточки, ручки проверяют границы."""

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.tree = source_cache.parse(cls.api_source)

    def _function(self, name):
        node = next(item for item in self.tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.get_source_segment(self.api_source, node)

    def test_lists_fall_back_to_card_reviews_for_human_score(self):
        from call_qa import api
        self.assertIn("ai_human_reviews", api._SUBJECT_HUMAN_SCORE)
        # Строка журнала старше: она и есть качество сотрудника.
        self.assertLess(api._SUBJECT_HUMAN_SCORE.index("c.score"),
                        api._SUBJECT_HUMAN_SCORE.index("ai_human_reviews"))

    def test_journal_row_for_a_call_follows_corrections(self):
        from call_qa import api, config
        sql = api._HUMAN_REVIEW_SQL[config.SUBJECT_CALL]
        self.assertIn("WITH RECURSIVE", sql)
        self.assertIn("previous_version_id", sql)
        # Каждый вид субъекта отдаёт одинаковый набор колонок — их читает один
        # journal_review_for_subject.
        for kind in config.SUBJECT_KINDS:
            with self.subTest(kind=kind):
                self.assertIn("c.evaluator_id", api._HUMAN_REVIEW_SQL[kind])
                self.assertIn("c.appeal_date", api._HUMAN_REVIEW_SQL[kind])

    def test_card_route_passes_the_reviewer(self):
        body = self._function("api_ai_qa_call")
        self.assertIn("reviewer_id=requester_id", body)

    def test_human_review_route_checks_scope_department_and_scale(self):
        body = self._function("api_ai_qa_human_review")
        self.assertIn("_ai_qa_guard()", body)
        self.assertIn("call_in_scope(call_id, scope, subject_kind)", body)
        self.assertIn("_ai_qa_direction_department_allowed(", body)
        self.assertIn("criteria_direction_id(direction_id)", body)
        self.assertIn('"scale_changed"', body)
        # В журнал — только с правами журнала.
        self.assertIn("_ensure_call_access_for_requester(", body)
        self.assertIn("_ai_qa_can_correct_journal(requester_id, requester)", body)
        self.assertIn("db.add_call_evaluation(", self._function("_ai_qa_write_journal"))

    def test_find_route_is_department_scoped(self):
        body = self._function("api_ai_qa_find")
        self.assertIn("_ai_qa_requested_department(requester_id)", body)
        self.assertIn("department=department", body)
        self.assertIn("allowed_direction_ids=scope", body)

    def test_pull_call_accepts_phone_and_op_department(self):
        body = self._function("api_ai_qa_pull_call")
        self.assertIn("CDR_CALL_DISTRIBUTION_DEPARTMENT_CODE", body)
        self.assertIn("phone=phone or None", body)
        self.assertIn("_cdr_import_touch(linkedid, requester_id, requester)", body)
        self.assertNotIn("записи загружаются вручную", body)


class JournalTargetTests(unittest.TestCase):
    """Откуда берутся реквизиты строки журнала — по виду субъекта."""

    def _load(self, namespace):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        tree = source_cache.parse(source)
        node = next(item for item in tree.body
                    if isinstance(item, ast.FunctionDef) and item.name == "_ai_qa_journal_target")
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, "<journal-target>", "exec"), namespace)
        return namespace["_ai_qa_journal_target"]

    def test_existing_journal_row_becomes_a_correction(self):
        from call_qa import config
        target = self._load({"call_qa_config": config})(
            {"kind": "imported_call", "id": 5},
            {"id": 77, "operator_id": 3, "phone_number": "7700", "month": "2026-09",
             "appeal_date": "2026-09-01T10:00:00", "audio_path": "b/p.mp3"}, 1)
        self.assertTrue(target["is_correction"])
        self.assertEqual(target["previous_version_id"], 77)
        self.assertIsNone(target["imported_call_id"])

    def test_chat_episode_uses_snapshot_and_local_start(self):
        from datetime import datetime, timezone
        from call_qa import config
        namespace = {"call_qa_config": config,
                     "_ai_qa_chat_snapshot_id": lambda subject, rid: 42,
                     "_ai_qa_local_iso": lambda v: v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
        target = self._load(namespace)(
            {"kind": "wz_episode", "id": 9, "operator_user_id": 3, "contact_phone": "+7 777 1",
             "chat_id": "c1", "started_at": datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)},
            None, 1)
        self.assertEqual(target["c2d_snapshot_id"], 42)
        self.assertEqual(target["phone_number"], "+7 777 1")
        self.assertEqual(target["month"], "2026-09")
        self.assertFalse(target["is_correction"])

    def test_chat_without_operator_is_refused(self):
        from call_qa import config
        with self.assertRaisesRegex(ValueError, "не привязана к сотруднику"):
            self._load({"call_qa_config": config})({"kind": "wz_episode", "id": 9}, None, 1)


class FindSubjectsTests(unittest.TestCase):
    def test_phone_normalisation(self):
        from call_qa import api
        self.assertEqual(api.phone_digits("8 (777) 123-45-67"), "77771234567")
        self.assertEqual(api.phone_suffix("+7 777 123 45 67"), "7771234567")
        self.assertEqual(api.phone_suffix("4567"), "4567")

    def test_search_requires_phone_or_operator(self):
        from call_qa import api
        with mock.patch.object(api.config, "connect_ro", side_effect=AssertionError("БД не нужна")):
            with self.assertRaisesRegex(ValueError, "номер телефона или сотрудника"):
                api.find_subjects(family="calls", department="op")
            with self.assertRaisesRegex(ValueError, "четырёх цифр"):
                api.find_subjects(family="calls", department="op", phone="12")
            with self.assertRaisesRegex(ValueError, "calls или chats"):
                api.find_subjects(family="x", department="op", phone="12345")

    def test_cdr_touches_are_searched_only_for_sales(self):
        from call_qa import api
        source = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8-sig")
        body = source[source.index("def find_subjects("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("if code == config.OP_DEPARTMENT_CODE:", body)
        self.assertIn("_find_cdr_touches", body)


if __name__ == "__main__":
    unittest.main()
