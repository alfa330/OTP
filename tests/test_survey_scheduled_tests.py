"""Тесты по расписанию в разделе «Опросы».

Чистая логика (окно теста, баллы, зачёт ответа) выполняется по-настоящему:
нужные методы вынимаются из database.py через AST и исполняются в синтетическом
классе без подключения к БД. Остальное — контракт: идемпотентные миграции,
исключение оценки «Тестирование знаний» из счёта прослушанных звонков,
роуты и cron в bot_schedule2.py, поведение фронта.
"""
import ast
import re
import unittest
from datetime import datetime
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "bot_schedule2.py"
SURVEYS_VIEW_PATH = ROOT / "src" / "components" / "surveys" / "SurveysView.jsx"
APP_JSX_PATH = ROOT / "src" / "App.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _survey_logic():
    """Собирает класс-заглушку из методов расчёта теста в DatabaseManager."""
    wanted_methods = {
        "_parse_survey_schedule_value",
        "survey_test_status",
        "survey_question_points",
        "survey_answer_is_correct",
        "survey_answer_score_ratio",
        "score_survey_test_attempt",
        "knowledge_test_evaluation_label",
        "_survey_bool",
        "_survey_int_id_list",
    }
    wanted_attrs = {
        "SURVEY_TEST_STATUS_SCHEDULED",
        "SURVEY_TEST_STATUS_ACTIVE",
        "SURVEY_TEST_STATUS_FINISHED",
        "KNOWLEDGE_TEST_EVALUATION_PREFIX",
    }

    module = source_cache.parse(_read(DATABASE_PATH))
    body = []
    for class_node in [node for node in module.body if isinstance(node, ast.ClassDef)]:
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted_methods:
                body.append(node)
            elif isinstance(node, ast.Assign):
                names = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if names & wanted_attrs:
                    body.append(node)

    found_methods = {node.name for node in body if isinstance(node, ast.FunctionDef)}
    missing = wanted_methods - found_methods
    if missing:
        raise AssertionError(f"Не найдены методы расчёта теста: {sorted(missing)}")

    stub = ast.ClassDef(
        name="SurveyTestLogic",
        bases=[],
        keywords=[],
        body=body,
        decorator_list=[],
        type_params=[],
    )
    tree = ast.Module(body=[stub], type_ignores=[])
    ast.fix_missing_locations(tree)

    namespace = {"math": __import__("math"), "datetime": datetime, "logging": __import__("logging")}
    exec(compile(tree, "<survey_logic>", "exec"), namespace)
    return namespace["SurveyTestLogic"]


LOGIC = _survey_logic()


class SurveyTestWindowTests(unittest.TestCase):
    """Статус теста выводится из окна, а не хранится отдельным полем."""

    def test_scheduled_before_start(self):
        status = LOGIC.survey_test_status(
            datetime(2026, 8, 1, 10, 0),
            datetime(2026, 8, 1, 12, 0),
            now=datetime(2026, 8, 1, 9, 59),
        )
        self.assertEqual(status, LOGIC.SURVEY_TEST_STATUS_SCHEDULED)

    def test_active_inside_window(self):
        status = LOGIC.survey_test_status(
            datetime(2026, 8, 1, 10, 0),
            datetime(2026, 8, 1, 12, 0),
            now=datetime(2026, 8, 1, 10, 0),
        )
        self.assertEqual(status, LOGIC.SURVEY_TEST_STATUS_ACTIVE)

    def test_finished_at_end_boundary(self):
        # Ровно в момент завершения тест уже закрыт: окно полуоткрытое.
        status = LOGIC.survey_test_status(
            datetime(2026, 8, 1, 10, 0),
            datetime(2026, 8, 1, 12, 0),
            now=datetime(2026, 8, 1, 12, 0),
        )
        self.assertEqual(status, LOGIC.SURVEY_TEST_STATUS_FINISHED)

    def test_without_window_test_is_active(self):
        status = LOGIC.survey_test_status(None, None, now=datetime(2026, 8, 1, 12, 0))
        self.assertEqual(status, LOGIC.SURVEY_TEST_STATUS_ACTIVE)

    def test_schedule_value_parsing(self):
        parse = LOGIC._parse_survey_schedule_value
        self.assertIsNone(parse('', 'ERR'))
        self.assertIsNone(parse(None, 'ERR'))
        self.assertEqual(parse('2026-08-01 10:30', 'ERR'), datetime(2026, 8, 1, 10, 30))
        self.assertEqual(parse('2026-08-01T10:30', 'ERR'), datetime(2026, 8, 1, 10, 30))
        self.assertEqual(parse('2026-08-01T10:30:00Z', 'ERR'), datetime(2026, 8, 1, 10, 30))
        # Таймзону снимаем: в БД лежат naive-метки времени Asia/Almaty.
        self.assertEqual(parse('2026-08-01T10:30:00+05:00', 'ERR'), datetime(2026, 8, 1, 10, 30))
        with self.assertRaises(ValueError):
            parse('не дата', 'ERR')


class SurveyTestScoringTests(unittest.TestCase):
    """Результат — сумма баллов за целиком верные вопросы, приведённая к 0..100."""

    QUESTIONS = [
        {'id': 1, 'type': 'single', 'correct_options': ['А'], 'points': 1},
        {'id': 2, 'type': 'multiple', 'correct_options': ['А', 'Б'], 'points': 3},
        {'id': 3, 'type': 'single', 'correct_options': ['В'], 'points': 1},
    ]

    def test_points_weight_the_result(self):
        result = LOGIC.score_survey_test_attempt(self.QUESTIONS, {
            1: {'selected_options': ['Б']},
            2: {'selected_options': ['Б', 'А']},
            3: {'selected_options': ['В']},
        })
        self.assertEqual(result['max_points'], 5.0)
        self.assertEqual(result['earned_points'], 4.0)
        self.assertEqual(result['score_percent'], 80.0)
        self.assertEqual(result['correct_answers'], 2)
        self.assertEqual(result['answered_questions'], 3)
        self.assertEqual(result['total_questions'], 3)

    def test_partial_multiple_answer_earns_nothing(self):
        result = LOGIC.score_survey_test_attempt(self.QUESTIONS, {
            2: {'selected_options': ['А']},
        })
        self.assertEqual(result['earned_points'], 0.0)
        self.assertEqual(result['score_percent'], 0.0)
        self.assertEqual(result['answered_questions'], 1)

    def test_empty_attempt_is_zero_not_error(self):
        result = LOGIC.score_survey_test_attempt(self.QUESTIONS, {})
        self.assertEqual(result['earned_points'], 0.0)
        self.assertEqual(result['score_percent'], 0.0)
        self.assertEqual(result['answered_questions'], 0)

    def test_rating_questions_are_ignored(self):
        result = LOGIC.score_survey_test_attempt(
            [{'id': 9, 'type': 'rating', 'correct_options': [], 'points': 5}],
            {9: {'rating_value': 5}},
        )
        self.assertEqual(result['total_questions'], 0)
        self.assertEqual(result['max_points'], 0.0)
        self.assertEqual(result['score_percent'], 0.0)

    def test_points_fallback_to_one(self):
        self.assertEqual(LOGIC.survey_question_points({}), 1.0)
        self.assertEqual(LOGIC.survey_question_points({'points': 0}), 1.0)
        self.assertEqual(LOGIC.survey_question_points({'points': -3}), 1.0)
        self.assertEqual(LOGIC.survey_question_points({'points': 'abc'}), 1.0)
        self.assertEqual(LOGIC.survey_question_points({'points': '2.5'}), 2.5)

    def test_partial_credit_gives_share_of_points(self):
        # Отмечено 1 из 2 верных вариантов и ничего лишнего → половина балла.
        questions = [{
            'id': 1, 'type': 'multiple', 'correct_options': ['А', 'Б'],
            'points': 4, 'partial_credit': True
        }]
        result = LOGIC.score_survey_test_attempt(questions, {1: {'selected_options': ['А']}})
        self.assertEqual(result['earned_points'], 2.0)
        self.assertEqual(result['score_percent'], 50.0)
        # Полный балл считается верным ответом, частичный — нет.
        self.assertEqual(result['correct_answers'], 0)
        self.assertTrue(result['per_question'][1]['is_partially_correct'])

    def test_partial_credit_zeroes_out_on_any_wrong_option(self):
        # Защита от «отметить всё»: один лишний вариант обнуляет вопрос.
        questions = [{
            'id': 1, 'type': 'multiple', 'correct_options': ['А', 'Б'],
            'points': 4, 'partial_credit': True
        }]
        result = LOGIC.score_survey_test_attempt(questions, {1: {'selected_options': ['А', 'Б', 'В']}})
        self.assertEqual(result['earned_points'], 0.0)
        self.assertEqual(result['score_percent'], 0.0)
        self.assertFalse(result['per_question'][1]['is_partially_correct'])

        all_options = LOGIC.score_survey_test_attempt(
            questions, {1: {'selected_options': ['А', 'Б', 'В', 'Г']}}
        )
        self.assertEqual(all_options['earned_points'], 0.0)

    def test_partial_credit_full_answer_is_still_fully_correct(self):
        questions = [{
            'id': 1, 'type': 'multiple', 'correct_options': ['А', 'Б'],
            'points': 4, 'partial_credit': True
        }]
        result = LOGIC.score_survey_test_attempt(questions, {1: {'selected_options': ['Б', 'А']}})
        self.assertEqual(result['earned_points'], 4.0)
        self.assertEqual(result['correct_answers'], 1)
        self.assertFalse(result['per_question'][1]['is_partially_correct'])

    def test_partial_credit_off_by_default(self):
        questions = [{'id': 1, 'type': 'multiple', 'correct_options': ['А', 'Б'], 'points': 4}]
        result = LOGIC.score_survey_test_attempt(questions, {1: {'selected_options': ['А']}})
        self.assertEqual(result['earned_points'], 0.0)

    def test_partial_credit_ignored_for_single_choice(self):
        # У вопроса с одним ответом делить балл не на что.
        ratio = LOGIC.survey_answer_score_ratio('single', ['А'], ['Б'], '', partial_credit=True)
        self.assertEqual(ratio, 0.0)

    def test_score_ratio_edge_cases(self):
        ratio = LOGIC.survey_answer_score_ratio
        self.assertEqual(ratio('multiple', ['А', 'Б'], [], '', partial_credit=True), 0.0)
        self.assertEqual(ratio('multiple', [], ['А'], '', partial_credit=True), 0.0)
        # Свой текст в тесте не зачитывается даже при частичном зачёте.
        self.assertEqual(ratio('multiple', ['А', 'Б'], ['А'], 'своё', partial_credit=True), 0.0)
        self.assertAlmostEqual(ratio('multiple', ['А', 'Б', 'В'], ['А', 'Б'], '', partial_credit=True), 2 / 3)

    def test_answer_correctness_rules(self):
        is_correct = LOGIC.survey_answer_is_correct
        self.assertTrue(is_correct('single', ['А'], ['А']))
        self.assertFalse(is_correct('single', ['А'], ['А', 'Б']))
        self.assertTrue(is_correct('multiple', ['А', 'Б'], ['Б', 'А']))
        self.assertFalse(is_correct('multiple', ['А', 'Б'], ['А']))
        self.assertFalse(is_correct('multiple', [], []))
        # Свой текст в тесте не зачитывается — проверять его автоматом нечем.
        self.assertFalse(is_correct('single', ['А'], ['А'], 'свой вариант'))

    def test_knowledge_test_label_is_readable_and_capped(self):
        label = LOGIC.knowledge_test_evaluation_label('Продукт: базовые знания')
        self.assertTrue(label.startswith('Тестирование знаний: '))
        self.assertIn('Продукт', label)
        self.assertLessEqual(len(LOGIC.knowledge_test_evaluation_label('я' * 500)), 255)
        self.assertIn('без названия', LOGIC.knowledge_test_evaluation_label('   '))


class SurveyTestSchemaTests(unittest.TestCase):
    """Миграции идемпотентные, статус теста в БД не дублируется."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_schedule_columns_added(self):
        self.assertIn("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS starts_at TIMESTAMP;", self.src)
        self.assertIn("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS ends_at TIMESTAMP;", self.src)
        self.assertIn("ADD COLUMN IF NOT EXISTS group_ids JSONB NOT NULL DEFAULT '[]'::jsonb;", self.src)
        self.assertIn("ADD COLUMN IF NOT EXISTS single_attempt BOOLEAN NOT NULL DEFAULT TRUE;", self.src)
        self.assertIn("ADD COLUMN IF NOT EXISTS affects_quality BOOLEAN NOT NULL DEFAULT FALSE;", self.src)
        self.assertIn("surveys_schedule_window_valid", self.src)

    def test_question_points_column_with_positive_check(self):
        self.assertIn("ADD COLUMN IF NOT EXISTS points NUMERIC(6, 2) NOT NULL DEFAULT 1;", self.src)
        self.assertIn("survey_questions_points_positive CHECK (points > 0)", self.src)

    def test_partial_credit_column_defaults_to_off(self):
        # Поведение существующих тестов не меняется: по умолчанию «всё или ничего».
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS partial_credit BOOLEAN NOT NULL DEFAULT FALSE;",
            self.src,
        )

    def test_attempt_draft_table_and_result_columns(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS survey_attempt_drafts (", self.src)
        self.assertIn("UNIQUE (survey_id, operator_id)", self.src)
        for column in ("earned_points", "max_points", "score_percent", "is_auto_submitted"):
            self.assertIn(f"ALTER TABLE survey_responses\n                ADD COLUMN IF NOT EXISTS {column}", self.src)

    def test_evaluation_link_column_and_unique_index(self):
        self.assertIn("ADD COLUMN IF NOT EXISTS survey_response_id INTEGER", self.src)
        self.assertIn("REFERENCES survey_responses(id) ON DELETE SET NULL", self.src)
        self.assertIn("idx_calls_survey_response", self.src)

    def test_status_is_not_stored_as_column(self):
        # Запланирован/Активен/Завершён считаются от окна; отдельного поля нет.
        self.assertNotIn("ADD COLUMN IF NOT EXISTS test_status", self.src)
        self.assertIn("def survey_test_status(", self.src)


class KnowledgeTestQualityIntegrationTests(unittest.TestCase):
    """Оценка теста идёт в средний балл, но не в счёт прослушанных звонков."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)
        self.app_src = _read(APP_PATH)

    def test_call_count_excludes_test_evaluations(self):
        self.assertIn(
            "COUNT(*) FILTER (WHERE score IS NOT NULL AND survey_response_id IS NULL) AS call_count",
            self.src,
        )
        self.assertIn(
            "COUNT(*) FILTER (WHERE c.survey_response_id IS NULL) AS cnt",
            self.src,
        )
        self.assertIn(
            "SELECT COUNT(*) FILTER (WHERE survey_response_id IS NULL), AVG(score)::float",
            self.src,
        )

    def test_avg_score_still_includes_test_evaluations(self):
        # Средняя считается по всем строкам журнала — фильтра по тесту в AVG быть не должно.
        self.assertNotIn("AVG(score) FILTER (WHERE survey_response_id IS NULL)", self.src)
        self.assertNotIn("AVG(c.score) FILTER (WHERE c.survey_response_id IS NULL)", self.src)

    def test_profile_monthly_call_count_excludes_tests(self):
        block_start = self.src.index("(SELECT COUNT(*) FROM calls")
        block = self.src[block_start:block_start + 500]
        self.assertIn("AND survey_response_id IS NULL", block)

    def test_evaluation_is_created_from_submit(self):
        self.assertIn("def _upsert_knowledge_test_evaluation_tx(", self.src)
        submit_start = self.src.index("def _submit_survey_response_tx(")
        submit = self.src[submit_start:self.src.index("def save_survey_attempt_draft(")]
        self.assertIn("if is_test and affects_quality:", submit)
        self.assertIn("_upsert_knowledge_test_evaluation_tx", submit)
        # Автоотправка идёт тем же путём, значит подсчёт совпадает с обычной сдачей.
        self.assertIn("auto_submitted=True", self.src)

    def test_deleting_test_removes_its_evaluations(self):
        # SET NULL оставил бы неотличимую от звонка запись журнала.
        delete_start = self.src.index("def delete_survey(")
        delete_block = self.src[delete_start:delete_start + 1600]
        self.assertIn("DELETE FROM calls", delete_block)
        self.assertIn("survey_response_id IN (", delete_block)

    def test_editing_a_test_rescores_existing_attempts(self):
        # Иначе статистика показывала бы старый процент рядом с новыми баллами.
        self.assertIn("def _rescore_survey_responses_tx(", self.src)
        update_start = self.src.index("def update_survey(")
        update_block = self.src[update_start:self.src.index("def _rescore_survey_responses_tx(")]
        self.assertIn("rescored = self._rescore_survey_responses_tx(cursor, survey_id)", update_block)
        # Выключили режим теста — прежние оценки «Тестирование знаний» уходят.
        self.assertIn("DELETE FROM calls", update_block)

    def test_rescore_keeps_original_evaluation_date(self):
        # Пересчёт по новым правилам не должен переносить оценку в другой месяц.
        self.assertIn("touch_timestamps=True", self.src)
        self.assertIn("touch_timestamps=False", self.src)

    def test_disabling_quality_flag_removes_evaluation(self):
        rescore_start = self.src.index("def _rescore_survey_responses_tx(")
        rescore_block = self.src[rescore_start:rescore_start + 4200]
        self.assertIn("DELETE FROM calls WHERE survey_response_id = %s", rescore_block)

    def test_stale_draft_is_cleaned_up(self):
        autoclose_start = self.src.index("def autoclose_due_survey_tests(")
        autoclose = self.src[autoclose_start:autoclose_start + 3000]
        self.assertIn("SURVEY_ALREADY_COMPLETED", autoclose)
        self.assertIn("DELETE FROM survey_attempt_drafts", autoclose)

    def test_journal_exposes_knowledge_test_payload(self):
        self.assertIn('"survey_response_id": row[53]', self.src)
        self.assertIn('"knowledge_test": {', self.src)
        self.assertIn("LEFT JOIN survey_responses tr ON tr.id = c.survey_response_id", self.src)

    def test_sv_excel_report_counts_only_listened_calls(self):
        self.assertIn('"is_knowledge_test": row[2] is not None', self.app_src)
        self.assertIn(
            "'score_count': sum(1 for row in score_rows if not row.get('is_knowledge_test'))",
            self.app_src,
        )


class SurveyTestApiTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(APP_PATH)

    def test_attempt_draft_route(self):
        self.assertIn("@app.route('/api/surveys/<int:survey_id>/attempt', methods=['PUT', 'OPTIONS'])", self.src)
        self.assertIn("db.save_survey_attempt_draft(", self.src)

    def test_test_config_is_forwarded_to_create_and_update(self):
        self.assertIn("def _survey_test_config_from_payload(", self.src)
        self.assertEqual(self.src.count("test_config=_survey_test_config_from_payload(data)"), 2)

    def test_groups_returned_with_survey_list(self):
        self.assertIn("db.get_survey_assignable_groups(", self.src)

    def test_window_errors_are_russian(self):
        self.assertIn("'SURVEY_TEST_NOT_STARTED': (\"Тест ещё не начался\", 409)", self.src)
        self.assertIn("'SURVEY_TEST_FINISHED': (\"Время теста истекло\", 409)", self.src)
        self.assertIn("def _survey_error_response(", self.src)

    def test_autoclose_job_registered(self):
        self.assertIn("id='survey_tests_autoclose'", self.src)
        self.assertIn("def autoclose_due_survey_tests_job(", self.src)
        self.assertIn("db.autoclose_due_survey_tests()", self.src)

    def test_export_shows_required_statistics_columns(self):
        header_start = self.src.index("ws_scores = wb.create_sheet('Баллы теста')")
        header = self.src[header_start:header_start + 700]
        for column in ("'Начало'", "'Завершение'", "'Баллы'", "'Итоговая оценка'",
                       "'Тип оценки'", "'Передано в качество'"):
            self.assertIn(column, header)
        self.assertIn("'Тестирование знаний'", self.src)


class SurveyTestFrontendTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(SURVEYS_VIEW_PATH)

    def test_schedule_fields_only_in_test_mode(self):
        self.assertIn("Расписание теста", self.src)
        self.assertIn("draft.isTest && (", self.src)
        self.assertIn("startsAt", self.src)
        self.assertIn("endsAt", self.src)
        self.assertIn("Одна попытка", self.src)
        self.assertIn("Учитывать результат в качестве оператора", self.src)

    def test_status_labels_match_specification(self):
        self.assertIn("label: 'Запланирован'", self.src)
        self.assertIn("label: 'Активен'", self.src)
        self.assertIn("label: 'Завершён'", self.src)

    def test_countdown_and_draft_autosave(self):
        self.assertIn("formatCountdown", self.src)
        self.assertIn("scheduleAttemptDraftSave", self.src)
        self.assertIn("/attempt`", self.src)
        # По истечении времени форма закрывается, а не отправляется молча.
        self.assertIn("Время теста истекло", self.src)
        self.assertIn("canFillSelectedSurvey", self.src)

    def test_partial_credit_toggle_only_for_multiple_choice(self):
        self.assertIn("Частичный зачёт", self.src)
        self.assertIn("Всё или ничего", self.src)
        self.assertIn("partialCredit", self.src)
        self.assertIn("partial_credit = payloadType === 'multiple'", self.src)
        toggle_start = self.src.index("{question.partialCredit ? 'Частичный зачёт' : 'Всё или ничего'}")
        toggle_block = self.src[toggle_start - 900:toggle_start]
        self.assertIn("question.type === 'multiple' && (", toggle_block)

    def test_partial_answer_has_its_own_status(self):
        self.assertIn("label: 'Частично'", self.src)
        self.assertIn("isTestAnswerPartiallyCorrect", self.src)

    def test_group_assignment_chips(self):
        self.assertIn("groupOptions", self.src)
        self.assertIn("'groupIds'", self.src)
        self.assertIn("group_ids:", self.src)

    def test_numbers_use_tabular_nums(self):
        # Числа в таймере и баллах не должны «прыгать» при обновлении.
        countdown_start = self.src.index("formatCountdown(testMsLeft)")
        countdown_block = self.src[countdown_start - 400:countdown_start]
        self.assertIn("tabular-nums", countdown_block)

    def test_respondent_cards_replace_the_wide_table(self):
        """Ответы сотрудников — карточки и лист ответов, а не таблица.

        Таблица с колонкой на каждый вопрос читалась только вбок; проверяем,
        что её не вернули «на всякий случай» рядом с карточками.
        """
        self.assertIn("const respondentCards = useMemo(", self.src)
        self.assertIn("activeTab === 'answers'", self.src)
        self.assertIn("setOpenedRespondentKey(card.key)", self.src)
        self.assertNotIn("statsViewMode", self.src)
        self.assertNotIn("<table", self.src)

    def test_answer_sheet_shows_correct_option_only_when_wrong(self):
        """Правильный ответ — только там, где ошиблись: иначе это шум."""
        sheet_start = self.src.index("Лист ответов сотрудника")
        sheet = self.src[sheet_start:]
        self.assertIn("isTestStatsSurvey && !isCorrect && expectedOptions.length > 0", sheet)
        self.assertIn("Правильный ответ:", sheet)

    def test_tabs_are_a_large_segmented_control(self):
        """Вкладки должны читаться как навигация, а не как мелкая подпись."""
        tabs_start = self.src.index("ariaLabel=\"Разделы опроса\"")
        tabs = self.src[tabs_start - 400:tabs_start + 1400]
        self.assertIn('size="lg"', tabs)
        for tab in ("'questions'", "'answers'", "'stats'"):
            self.assertIn(tab, tabs)

    def test_list_is_paginated_and_has_archive_scope(self):
        self.assertIn("const SCOPE_ARCHIVE = 'archive';", self.src)
        self.assertIn("page_size: LIST_PAGE_SIZE", self.src)
        self.assertIn("setListPage", self.src)
        # Список — лёгкие строки, карточку тянем отдельно.
        self.assertIn("/detail`", self.src)

    def test_long_texts_hide_behind_the_i_hint(self):
        self.assertIn("IosHint", self.src)
        self.assertIn("label=\"О разделе\"", self.src)


class KnowledgeTestJournalFrontendTests(unittest.TestCase):
    def setUp(self):
        self.src = _read(APP_JSX_PATH)

    def test_plan_progress_ignores_knowledge_tests(self):
        self.assertIn("const listenedEvalsForCalc = evalsForCalc.filter(ev => !ev?.knowledge_test);", self.src)
        self.assertIn("const evaluationCount = listenedEvalsForCalc.length;", self.src)
        self.assertIn("const listenedEvals = evalsFiltered.filter(ev => !ev?.knowledge_test);", self.src)

    def test_average_score_includes_knowledge_tests(self):
        # Средний балл считается по всем оценкам, включая тест.
        self.assertIn(
            "? evalsForCalc.reduce((sum, ev) => sum + Number(ev.score || 0), 0) / evalsForCalc.length",
            self.src,
        )

    def test_journal_marks_knowledge_test_evaluations(self):
        self.assertIn("Тестирование знаний", self.src)
        self.assertIn("fa-graduation-cap", self.src)
        self.assertIn("!ev.c2d_snapshot_id && !ev.knowledge_test", self.src)


if __name__ == '__main__':
    unittest.main()
