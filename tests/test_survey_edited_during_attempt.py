# -*- coding: utf-8 -*-
"""Опрос правят, пока его проходят.

Дефект 26.08.2026 (тест «Тестирование по Базе знаний»): автор переписал
формулировку варианта через восемь минут после старта, у трёх операторов в
черновике остался прежний текст — и кнопка «Завершить тест» упиралась в
«Выбран недопустимый вариант ответа». Вопрос при этом выглядел отвеченным
(галочка в бейдже, прогресс 30 из 30), так что найти его было нечем.

Чистая логика (очистка черновика, разбор кода ошибки) выполняется по-настоящему:
нужное вынимается из монолитов через AST. Ветка отправки и фронт проверяются
контрактом — их не выполнить без БД и браузера.
"""
import ast
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "bot_schedule2.py"
SURVEYS_VIEW_PATH = ROOT / "src" / "components" / "surveys" / "SurveysView.jsx"


def _read(path):
    return source_cache.read(str(path))


def _draft_cleaner():
    """Настоящий `survey_draft_answers_for_questions` без подключения к БД."""
    node = source_cache.function_node(
        str(DATABASE_PATH), "survey_draft_answers_for_questions", class_name="Database"
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, "<draft_cleaner>", "exec"), namespace)
    cleaner = namespace["survey_draft_answers_for_questions"]
    return lambda draft, questions: cleaner(None, draft, questions)


def _survey_error_response():
    """Настоящий разбор кода ошибки; `jsonify` подменён на сам payload."""
    body = []
    wanted_names = {
        "_SURVEY_ERROR_MESSAGES",
        "_SURVEY_ERROR_PREFIX_MESSAGES",
        "_SURVEY_ERROR_NUMBERED_PREFIX_MESSAGES",
    }
    for node in source_cache.tree(str(APP_PATH)).body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_names:
                body.append(node)
    body.append(source_cache.function_node(str(APP_PATH), "_survey_error_response"))
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"jsonify": lambda payload: payload}
    exec(compile(module, "<survey_errors>", "exec"), namespace)
    return namespace["_survey_error_response"]


CLEAN_DRAFT = _draft_cleaner()
ERROR_RESPONSE = _survey_error_response()

QUESTIONS = [
    {"id": 450, "options": ["С карты 23,2 тг", "Без комиссии", "25 тг в обоих случаях"]},
    {"id": 451, "options": ["Да", "Нет"]},
]


class DraftSurvivesSurveyEditTests(unittest.TestCase):
    """Черновик отдаётся оператору без вариантов, которых в вопросе уже нет."""

    def test_stale_option_is_dropped(self):
        draft = [{"question_id": 450, "selected_options": ["С карты 25 тг"]}]
        self.assertEqual(CLEAN_DRAFT(draft, QUESTIONS), [])

    def test_untouched_answers_stay(self):
        draft = [
            {"question_id": 450, "selected_options": ["С карты 25 тг"]},
            {"question_id": 451, "selected_options": ["Да"]},
        ]
        self.assertEqual(
            CLEAN_DRAFT(draft, QUESTIONS),
            [{"question_id": 451, "selected_options": ["Да"]}],
        )

    def test_multiple_choice_keeps_surviving_options(self):
        draft = [{"question_id": 450, "selected_options": ["Без комиссии", "С карты 25 тг"]}]
        self.assertEqual(
            CLEAN_DRAFT(draft, QUESTIONS),
            [{"question_id": 450, "selected_options": ["Без комиссии"]}],
        )

    def test_answer_of_deleted_question_is_dropped(self):
        draft = [{"question_id": 999, "selected_options": ["Да"]}]
        self.assertEqual(CLEAN_DRAFT(draft, QUESTIONS), [])

    def test_option_is_matched_after_trim_and_deduplicated(self):
        draft = [{"question_id": 451, "selected_options": ["  Да  ", "Да", ""]}]
        self.assertEqual(
            CLEAN_DRAFT(draft, QUESTIONS),
            [{"question_id": 451, "selected_options": ["Да"]}],
        )

    def test_empty_and_broken_drafts_are_safe(self):
        self.assertEqual(CLEAN_DRAFT(None, QUESTIONS), [])
        self.assertEqual(CLEAN_DRAFT([], QUESTIONS), [])
        self.assertEqual(CLEAN_DRAFT(["мусор", {"question_id": "нет"}], QUESTIONS), [])
        self.assertEqual(CLEAN_DRAFT([{"question_id": 451}], QUESTIONS), [])

    def test_draft_is_cleaned_before_it_reaches_the_operator(self):
        src = _read(DATABASE_PATH)
        self.assertIn("'draft_answers': draft_answers", src)
        self.assertIn(
            "draft_answers = self.survey_draft_answers_for_questions(",
            src,
        )


class ChangedOptionErrorTests(unittest.TestCase):
    """Оператору называют номер вопроса, а не «недопустимый вариант»."""

    def test_message_names_the_question_number(self):
        payload, status = ERROR_RESPONSE("SURVEY_OPTION_CHANGED_10")
        self.assertEqual(status, 409)
        self.assertIn("№10", payload["error"])
        self.assertNotIn("недопустимый", payload["error"].lower())

    def test_status_409_makes_the_front_reload_the_survey(self):
        # Фронт перечитывает опрос именно по 409 — тогда исчезнувший вариант
        # уходит из черновика и вопрос становится пустым сам.
        _, status = ERROR_RESPONSE("SURVEY_OPTION_CHANGED_1")
        self.assertEqual(status, 409)
        view = _read(SURVEYS_VIEW_PATH)
        self.assertIn("if (error?.response?.status === 409) await reloadSurveys();", view)

    def test_code_without_number_still_explains_itself(self):
        payload, status = ERROR_RESPONSE("SURVEY_OPTION_CHANGED_")
        self.assertEqual(status, 409)
        self.assertIn("изменили", payload["error"])

    def test_other_survey_errors_are_untouched(self):
        payload, status = ERROR_RESPONSE("SURVEY_TOO_MANY_OPTIONS_7")
        self.assertEqual((payload["error"], status),
                         ("Выбрано больше вариантов, чем допускает вопрос", 400))
        payload, status = ERROR_RESPONSE("SURVEY_TEST_FINISHED")
        self.assertEqual((payload["error"], status), ("Время теста истекло", 409))


class SubmitAfterEditTests(unittest.TestCase):
    """Ветка отправки: живому оператору — отказ с номером, автоотправке — ноль."""

    def setUp(self):
        src = _read(DATABASE_PATH)
        start = src.index("def _submit_survey_response_tx(")
        self.block = src[start:src.index("invalid_selected = []", start) + 40]

    def test_manual_submit_reports_the_question_number(self):
        self.assertIn("if not auto_submitted:", self.block)
        self.assertIn('raise ValueError(f"SURVEY_OPTION_CHANGED_{question[\'number\']}")', self.block)

    def test_question_number_matches_the_form(self):
        # Номер в отказе — порядковый номер вопроса в форме (position, id).
        self.assertIn("'number': len(questions_by_id) + 1,", self.block)
        self.assertIn("ORDER BY position, id", self.block)

    def test_autosubmit_saves_the_attempt_instead_of_failing(self):
        # Иначе по истечении окна попытка не сохранилась бы вовсе, а cron
        # возвращался бы к тому же черновику каждую минуту.
        self.assertIn(
            "selected_unique = [item for item in selected_unique if item in allowed_options]",
            self.block,
        )


class FillFormIgnoresStaleDraftTests(unittest.TestCase):
    """Форма прохождения не считает отвеченным вопрос с исчезнувшим вариантом."""

    def setUp(self):
        self.src = _read(SURVEYS_VIEW_PATH)

    def test_draft_options_are_filtered_by_current_options(self):
        start = self.src.index("const draftByQuestion = new Map();")
        block = self.src[start:self.src.index("setAnswers(initial);", start)]
        self.assertIn("questionOptions.has(item)", block)
        self.assertIn("selected_options: draftOptions,", block)

    def test_progress_counts_the_same_answers(self):
        # Полоса и бейдж считают по selected_options — отфильтрованный черновик
        # сразу делает вопрос пустым и в прогрессе, и в номере на бейдже.
        self.assertIn("const isFillAnswerFilled = useCallback((question, answer) => {", self.src)
        self.assertIn("{isAnswered ? <FaIcon className=\"fas fa-check\" /> : index + 1}", self.src)


if __name__ == "__main__":
    unittest.main()
