"""Обязательный комментарий у отдельных технических причин (задача #268)."""

import ast
import textwrap
import unittest
from pathlib import Path
from typing import Dict, List, Optional

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
VIEW_PATH = ROOT / "src" / "components" / "technical" / "TechnicalIssuesView.jsx"
RULES_PATH = ROOT / "src" / "components" / "technical" / "commentRules.js"
APP_PATH = ROOT / "src" / "App.jsx"

REASON = "Не работал рабочий сайт"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name, class_name=None):
    source = _read(path)
    module = source_cache.parse(source)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    function_node = next(
        node for node in body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


# database.py целиком на Windows не импортируется (там time.tzset), поэтому берём
# из исходника только справочник и его помощники и выполняем их отдельно.
def _comment_rule_namespace():
    source = _read(DATABASE_PATH)
    module = source_cache.parse(source)
    wanted_names = {
        "TECHNICAL_ISSUE_REASONS",
        "TECHNICAL_ISSUE_REASONS_SET",
        "TECHNICAL_ISSUE_REASON_COMMENT_RULES",
    }
    wanted_functions = {
        "technical_issue_comment_rule",
        "technical_issue_comment_rules_payload",
        "technical_issue_comment_required_error",
    }
    chunks = []
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in wanted_names:
                chunks.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(name in wanted_names for name in names):
                chunks.append(ast.get_source_segment(source, node))
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            chunks.append(ast.get_source_segment(source, node))

    namespace = {"Dict": Dict, "List": List, "Optional": Optional}
    exec('\n\n'.join(chunks), namespace)
    for name in wanted_names | wanted_functions:
        assert name in namespace, f"не нашли {name} в database.py"
    return namespace


class TechnicalIssueCommentRuleTests(unittest.TestCase):
    def test_rule_lives_in_one_place_with_hint_and_example(self):
        db = _comment_rule_namespace()

        rule = db["technical_issue_comment_rule"](REASON)
        self.assertIsNotNone(rule, "у причины «Не работал рабочий сайт» должно быть правило")
        self.assertIn("сайта", rule["hint"])
        self.assertIn("ошибка", rule["hint"])
        self.assertTrue(rule["example"])

        # Причина из списка, иначе правило никогда не сработает.
        self.assertIn(REASON, db["TECHNICAL_ISSUE_REASONS_SET"])

        # Остальные причины комментарий не требуют.
        self.assertIsNone(db["technical_issue_comment_rule"]("Замена мыши"))
        self.assertIsNone(db["technical_issue_comment_rule"](""))
        self.assertIsNone(db["technical_issue_comment_rule"](None))

        # Пробелы вокруг причины не должны обходить правило.
        self.assertIsNotNone(db["technical_issue_comment_rule"](f"  {REASON} "))

    def test_error_text_names_the_reason_and_repeats_the_hint(self):
        db = _comment_rule_namespace()

        message = db["technical_issue_comment_required_error"](REASON)
        self.assertIn(REASON, message)
        self.assertIn(db["TECHNICAL_ISSUE_REASON_COMMENT_RULES"][REASON]["hint"], message)

    def test_api_hands_the_rules_to_the_form(self):
        db = _comment_rule_namespace()

        payload = db["technical_issue_comment_rules_payload"]()
        self.assertTrue(any(item["reason"] == REASON for item in payload))
        for item in payload:
            self.assertEqual({"reason", "hint", "example"}, set(item))

        source = _read(BOT_PATH)
        self.assertIn("technical_issue_comment_rules_payload", source)
        # Справочник отдают оба ответа: и /reasons, и список записей.
        self.assertEqual(
            2,
            source.count('"comment_required_reasons": technical_issue_comment_rules_payload()'),
        )

    def test_server_refuses_to_save_without_comment(self):
        for function_name in (
            "create_operator_technical_issues",
            "update_operator_technical_issue_batch",
        ):
            with self.subTest(function=function_name):
                source = _function_source(DATABASE_PATH, function_name, class_name="Database")
                self.assertIn("technical_issue_comment_rule(reason_text)", source)
                self.assertIn(
                    "raise ValueError(technical_issue_comment_required_error(reason_text))",
                    source,
                )
                # Проверка стоит до записи в базу.
                self.assertLess(
                    source.index("technical_issue_comment_required_error"),
                    source.index("with self._get_cursor()"),
                )

    def test_shared_module_keeps_the_wording_in_one_place(self):
        source = _read(RULES_PATH)
        self.assertIn("FALLBACK_COMMENT_REQUIRED_REASONS", source)
        self.assertIn(REASON, source)
        self.assertIn("export const findCommentRule", source)
        self.assertIn("export const commentRequiredMessage", source)

    def test_section_form_marks_the_comment_required(self):
        source = _read(VIEW_PATH)
        self.assertIn("from './commentRules'", source)
        self.assertIn("findCommentRule(commentRules, createReason)", source)
        self.assertIn("res?.data?.comment_required_reasons", source)
        self.assertIn("required={Boolean(commentRule)}", source)
        self.assertIn("commentRule?.hint", source)
        self.assertIn("notify(commentRequiredMessage(createCommentRule), 'error')", source)

    def test_planner_forms_check_the_comment_too(self):
        source = _read(APP_PATH)
        self.assertIn("from './components/technical/commentRules'", source)
        self.assertIn("payload?.comment_required_reasons", source)
        # Обе формы графика: окно офлайн-активности и подтверждение статуса.
        self.assertIn(
            "setPlannerOfflineActivityModalError(commentRequiredMessage(technicalCommentRule))",
            source,
        )
        self.assertIn(
            "setPlannerTechStatusModalError(commentRequiredMessage(techStatusCommentRule))",
            source,
        )

    def test_comment_stays_in_the_journal_and_in_the_export(self):
        # Требование задачи: комментарий виден в общем отчёте и в выгрузке.
        view = _read(VIEW_PATH)
        self.assertIn("'Комментарий', 'Направления'", view)
        self.assertIn("item?.comment", view)

        export = _function_source(BOT_PATH, "export_technical_issues_excel")
        self.assertIn("'Комментарий'", export)
        self.assertIn("value=item.get('comment')", export)


if __name__ == "__main__":
    unittest.main()
