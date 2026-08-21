"""Архив опросов, страницы списка и лёгкий счётчик раздела «Опросы».

Что здесь проверяется:

* опрос старше двух недель уходит в архив, тест — никогда;
* владельцу сообщают об архивации, и отметка ставится только после успешной
  отправки (иначе сообщение потерялось бы молча);
* архивные опросы не считаются ни в колоколе, ни в бейдже раздела и не
  принимают ответы;
* список приходит страницами, а карточка одного опроса — отдельным запросом:
  раньше раздел выкачивал все опросы со всеми ответами всех сотрудников разом.

Чистая логика (нормализация страницы, экранирование поиска, сборка лёгкой
строки, предикаты видимости) выполняется по-настоящему: методы вынимаются из
database.py через AST и исполняются в синтетическом классе без подключения к БД.
Остальное — контракт исходников, как и в соседнем файле про тесты по расписанию.
"""
import ast
import unittest
from datetime import datetime
from pathlib import Path
from typing import Optional

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
APP_PATH = ROOT / "bot_schedule2.py"
NOTIFICATIONS_SOURCES_PATH = ROOT / "notifications" / "sources.py"
SURVEYS_VIEW_PATH = ROOT / "src" / "components" / "surveys" / "SurveysView.jsx"
APP_JSX_PATH = ROOT / "src" / "App.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _survey_page_logic():
    """Собирает класс-заглушку из методов страницы и архива."""
    wanted_methods = {
        "_normalize_survey_page",
        "_survey_search_pattern",
        "_survey_page_envelope",
        "_serialize_survey_list_row",
        "_survey_visible_sql",
        "_survey_assignment_visible_sql",
        "_survey_dt_to_iso",
        "_parse_survey_schedule_value",
        "survey_test_status",
    }
    wanted_attrs = {
        "SURVEY_TEST_STATUS_SCHEDULED",
        "SURVEY_TEST_STATUS_ACTIVE",
        "SURVEY_TEST_STATUS_FINISHED",
        "SURVEY_ARCHIVE_AFTER_DAYS",
        "SURVEY_PAGE_SIZE_DEFAULT",
        "SURVEY_PAGE_SIZE_MAX",
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

    found = {node.name for node in body if isinstance(node, ast.FunctionDef)}
    missing = wanted_methods - found
    if missing:
        raise AssertionError(f"Не найдены методы страницы опросов: {sorted(missing)}")

    # role_has_min нужен предикатам видимости. Берём его из того же дерева, а не
    # `from database import`: импорт исполняет монолит целиком, а он последней
    # строкой поднимает пул к Postgres. У разработчика рядом обычно есть локальная
    # база, и импорт молча проходит; в CI базы нет — там сбор ЭТОГО файла ронял
    # весь прогон (Interrupted: 1 error during collection). Правило набора прежнее:
    # монолиты не импортируем, см. tests/source_cache.py.
    wanted_role_funcs = {"normalize_role_value", "role_has_min"}
    wanted_role_attrs = {"ROLE_ALIASES", "ROLE_HIERARCHY"}
    prelude = []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted_role_funcs:
            prelude.append(node)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_role_attrs:
                prelude.append(node)

    lifted = {node.name for node in prelude if isinstance(node, ast.FunctionDef)}
    lifted |= {
        target.id
        for node in prelude if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    missing_roles = (wanted_role_funcs | wanted_role_attrs) - lifted
    if missing_roles:
        raise AssertionError(f"Не найдена иерархия ролей: {sorted(missing_roles)}")

    stub = ast.ClassDef(
        name="SurveyPageLogic",
        bases=[],
        keywords=[],
        body=body,
        decorator_list=[],
        type_params=[],
    )
    tree = ast.Module(body=prelude + [stub], type_ignores=[])
    ast.fix_missing_locations(tree)

    namespace = {
        "datetime": datetime,
        "logging": __import__("logging"),
        # Аннотации поднятых функций вычисляются при exec — Optional нужен здесь.
        "Optional": Optional,
    }
    exec(compile(tree, "<survey_page_logic>", "exec"), namespace)
    return namespace["SurveyPageLogic"]


LOGIC = _survey_page_logic()


class SurveyPagingLogicTests(unittest.TestCase):
    """Нормализация страницы, поиск и сборка лёгкой строки списка."""

    def test_page_and_size_are_clamped(self):
        normalize = LOGIC._normalize_survey_page
        self.assertEqual(normalize(1, 20), (1, 20))
        # Отрицательная и нулевая страница — это первая, а не пустой ответ.
        self.assertEqual(normalize(0, 20), (1, 20))
        self.assertEqual(normalize(-5, 20), (1, 20))
        # Размер страницы ограничен сверху: запросом нельзя выкачать всё разом.
        self.assertEqual(normalize(1, 10 ** 6)[1], LOGIC.SURVEY_PAGE_SIZE_MAX)
        self.assertEqual(normalize(1, 0)[1], 1)
        # Мусор в параметрах не роняет запрос.
        self.assertEqual(normalize('abc', 'abc'), (1, LOGIC.SURVEY_PAGE_SIZE_DEFAULT))

    def test_search_pattern_escapes_like_wildcards(self):
        pattern = LOGIC._survey_search_pattern
        self.assertIsNone(pattern(''))
        self.assertIsNone(pattern('   '))
        self.assertIsNone(pattern(None))
        self.assertEqual(pattern('тест'), '%тест%')
        # «%» и «_» — это символы, а не «что угодно»: иначе поиск по «100%»
        # выдавал бы весь список.
        self.assertEqual(pattern('100%'), r'%100\%%')
        self.assertEqual(pattern('a_b'), r'%a\_b%')

    def test_envelope_reports_page_count(self):
        envelope = LOGIC._survey_page_envelope(LOGIC, [], 0, 1, 20)
        self.assertEqual(envelope['pages'], 0)
        self.assertEqual(envelope['total'], 0)
        self.assertEqual(LOGIC._survey_page_envelope(LOGIC, [], 41, 1, 20)['pages'], 3)
        self.assertEqual(LOGIC._survey_page_envelope(LOGIC, [], 40, 1, 20)['pages'], 2)

    def _row(self, overrides=None):
        # Порядок колонок ровно тот, что читает _serialize_survey_list_row.
        base = {
            0: 7, 1: 'Опрос', 2: False, 3: datetime(2026, 8, 1, 10, 0), 4: None,
            5: None, 6: None, 7: True, 8: False, 9: 7, 10: 1, 11: 10, 12: 4, 13: 1,
        }
        base.update(overrides or {})
        return [base[index] for index in range(14)]

    def test_list_row_counts_pending_and_rate(self):
        item = LOGIC._serialize_survey_list_row(LOGIC, self._row())
        self.assertEqual(item['statistics']['assigned_count'], 10)
        self.assertEqual(item['statistics']['completed_count'], 4)
        self.assertEqual(item['statistics']['pending_count'], 6)
        self.assertEqual(item['statistics']['completion_rate'], 40.0)
        self.assertFalse(item['is_archived'])
        self.assertIsNone(item['test'])

    def test_list_row_without_assignments_is_not_a_division_by_zero(self):
        item = LOGIC._serialize_survey_list_row(LOGIC, self._row({11: 0, 12: 0}))
        self.assertEqual(item['statistics']['completion_rate'], 0.0)
        self.assertEqual(item['statistics']['pending_count'], 0)

    def test_archived_row_carries_the_flag_and_date(self):
        archived_at = datetime(2026, 8, 20, 10, 0)
        item = LOGIC._serialize_survey_list_row(LOGIC, self._row({4: archived_at}))
        self.assertTrue(item['is_archived'])
        self.assertEqual(item['archived_at'], archived_at.isoformat())

    def test_test_row_gets_its_window_status(self):
        item = LOGIC._serialize_survey_list_row(
            LOGIC,
            self._row({2: True, 5: datetime(2026, 8, 1, 10, 0), 6: datetime(2026, 8, 1, 12, 0)}),
            now=datetime(2026, 8, 1, 11, 0),
        )
        self.assertEqual(item['test']['status'], LOGIC.SURVEY_TEST_STATUS_ACTIVE)

    def test_operator_row_columns_match_the_shared_serializer(self):
        """У оператора статус лежит ПОСЛЕ общих колонок, а не вместо счётчиков.

        Иначе `int(row[11])` получал бы строку 'completed' и падал на каждом
        открытии раздела оператором.
        """
        src = _read(DATABASE_PATH)
        page_start = src.index("def get_operator_surveys_page(")
        block = src[page_start:page_start + 4200]
        self.assertIn("1 AS assigned_count", block)
        self.assertIn("CASE WHEN sa.status = 'completed' THEN 1 ELSE 0 END AS completed_count", block)
        self.assertIn("COUNT(*) OVER () AS total_rows,\n                sa.status,\n                sa.completed_at", block)
        self.assertIn("status = str(row[14] or 'assigned')", block)
        self.assertIn("self._survey_dt_to_iso(row[15])", block)

    def test_repeat_iteration_never_drops_below_one(self):
        item = LOGIC._serialize_survey_list_row(LOGIC, self._row({9: None, 10: 0}))
        self.assertEqual(item['repeat']['iteration'], 1)
        self.assertEqual(item['repeat']['root_id'], 7)
        self.assertFalse(item['repeat']['is_repeat'])


class SurveyVisibilitySqlTests(unittest.TestCase):
    """Права в странице списка — те же, что были в полной выгрузке."""

    def test_admin_and_trainer_see_everything(self):
        for role in ('admin', 'super_admin', 'trainer'):
            visible, params = LOGIC._survey_visible_sql(role, 5, None)
            self.assertEqual(visible, 'TRUE')
            self.assertEqual(params, [])
            assignment, assignment_params = LOGIC._survey_assignment_visible_sql(role, None)
            self.assertEqual(assignment, 'TRUE')
            self.assertEqual(assignment_params, [])

    def test_supervisor_with_department_scope(self):
        visible, params = LOGIC._survey_visible_sql('sv', 5, 3)
        self.assertIn('s.created_by = %s', visible)
        self.assertIn('op2.department_id = %s', visible)
        self.assertEqual(params, [5, 3])

        assignment, assignment_params = LOGIC._survey_assignment_visible_sql('sv', 3)
        self.assertIn('vu.department_id = %s', assignment)
        # Уволенные не должны попадать в «пройдено N из M».
        self.assertIn("NOT IN ('fired', 'dismissal')", assignment)
        self.assertEqual(assignment_params, [3])

    def test_supervisor_without_scope_falls_back_to_own_operators(self):
        visible, params = LOGIC._survey_visible_sql('sv', 5, None)
        self.assertIn('op2.supervisor_id = %s', visible)
        self.assertEqual(params, [5, 5])
        _, assignment_params = LOGIC._survey_assignment_visible_sql('sv', None)
        self.assertEqual(assignment_params, [])

    def test_operator_gets_nothing_from_the_management_path(self):
        visible, params = LOGIC._survey_visible_sql('operator', 5, None)
        self.assertEqual(visible, 'FALSE')
        self.assertEqual(params, [])


class SurveyArchiveSchemaTests(unittest.TestCase):
    """Миграции архива идемпотентные, отметка об уведомлении — отдельная."""

    def setUp(self):
        self.src = _read(DATABASE_PATH)

    def test_archive_columns_added(self):
        self.assertIn("ALTER TABLE surveys ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP;", self.src)
        self.assertIn(
            "ALTER TABLE surveys ADD COLUMN IF NOT EXISTS archive_notified_at TIMESTAMP;",
            self.src,
        )

    def test_archive_indexes_exist(self):
        self.assertIn("idx_surveys_archive_page", self.src)
        self.assertIn("idx_surveys_archive_pending_notify", self.src)

    def test_running_test_is_never_archived(self):
        """Опрос — по возрасту, тест — только с закрытым (или пустым) окном.

        Идущий тест не должен исчезать из-под людей, даже если он создан
        месяц назад. А тест БЕЗ окна закрыть нечему — такие на проде висели
        «активными» по 144 дня и держали назначения в бейдже.
        """
        archive_start = self.src.index("def archive_stale_surveys(")
        block = self.src[archive_start:archive_start + 2000]
        self.assertIn("archived_at IS NULL", block)
        self.assertIn("INTERVAL '1 day'", block)
        self.assertIn("(NOT COALESCE(is_test, FALSE) OR ends_at IS NULL OR ends_at <= %s)", block)

    def test_notification_mark_is_set_separately(self):
        # Отметку ставим только после успешной отправки — иначе уведомление
        # потерялось бы молча при недоступном Telegram.
        self.assertIn("def collect_survey_archive_notifications(", self.src)
        self.assertIn("def mark_survey_archive_notified(", self.src)
        collect_start = self.src.index("def collect_survey_archive_notifications(")
        block = self.src[collect_start:collect_start + 1400]
        self.assertIn("s.archive_notified_at IS NULL", block)
        self.assertIn("u.telegram_id IS NOT NULL", block)

    def test_archived_survey_stops_accepting_answers(self):
        submit_start = self.src.index("def _submit_survey_response_tx(")
        block = self.src[submit_start:submit_start + 2000]
        self.assertIn('raise ValueError("SURVEY_ARCHIVED")', block)

    def test_bell_trigger_wakes_assignees_on_archiving(self):
        self.assertIn("'trg_bell_surveys_archived'", self.src)
        self.assertIn("'AFTER UPDATE OF archived_at'", self.src)
        self.assertIn("OLD.archived_at IS DISTINCT FROM NEW.archived_at", self.src)

    def test_detail_and_page_queries_exist(self):
        for name in ("get_surveys_page", "get_operator_surveys_page",
                     "get_survey_detail_for_requester", "count_pending_surveys"):
            self.assertIn(f"def {name}(", self.src)

    def test_full_list_can_be_narrowed_to_one_survey(self):
        # Карточка переиспользует ту же сериализацию, а не заводит вторую.
        self.assertIn("def get_surveys_for_management(self, requester_id, requester_role, scope_department_id=None,", self.src)
        self.assertIn("survey_ids=None", self.src)
        self.assertIn("def get_surveys_for_operator(self, operator_id, survey_ids=None, include_archived=False):", self.src)


class SurveyArchiveApiTests(unittest.TestCase):
    """Роуты, cron и текст уведомления."""

    def setUp(self):
        self.src = _read(APP_PATH)

    def test_list_route_is_paginated(self):
        self.assertIn("db.get_surveys_page(", self.src)
        self.assertIn("db.get_operator_surveys_page(", self.src)
        self.assertIn("def _survey_archived_scope_requested(", self.src)
        self.assertIn("def _survey_page_payload(", self.src)

    def test_detail_and_count_routes_registered(self):
        self.assertIn("@app.route('/api/surveys/<int:survey_id>/detail', methods=['GET', 'OPTIONS'])", self.src)
        self.assertIn("@app.route('/api/surveys/pending_count', methods=['GET', 'OPTIONS'])", self.src)
        self.assertIn("db.get_survey_detail_for_requester(", self.src)
        self.assertIn("db.count_pending_surveys(", self.src)

    def test_archive_job_registered_in_working_hours(self):
        self.assertIn("def archive_stale_surveys_job(", self.src)
        self.assertIn("id='surveys_archive_daily'", self.src)
        self.assertIn("db.archive_stale_surveys()", self.src)
        job_start = self.src.index("id='surveys_archive_daily'")
        block = self.src[job_start - 400:job_start]
        # Ночная рассылка в Telegram — это шум, поэтому час задан явно.
        self.assertIn("CronTrigger(hour=10", block)

    def test_owner_notice_is_marked_only_after_success(self):
        job_start = self.src.index("def archive_stale_surveys_job(")
        block = self.src[job_start:job_start + 2600]
        self.assertIn("if response.status_code != 200:", block)
        self.assertIn("db.mark_survey_archive_notified(", block)
        self.assertLess(block.index("if response.status_code != 200:"),
                        block.index("db.mark_survey_archive_notified("))

    def test_owner_gets_one_message_per_batch(self):
        """В один день архивируется несколько опросов — письмо должно быть одно.

        На боевой базе первый же запуск уводит в архив весь накопившийся
        список, и письмо на каждый опрос было бы спамом.
        """
        job_start = self.src.index("def archive_stale_surveys_job(")
        block = self.src[job_start:job_start + 2600]
        self.assertIn("by_owner.setdefault(chat_id, []).append(item)", block)
        self.assertIn("for chat_id, items in by_owner.items():", block)
        self.assertIn("SURVEY_ARCHIVE_NOTICE_MAX_TITLES", self.src)

    def test_archived_error_is_russian(self):
        self.assertIn("'SURVEY_ARCHIVED': (\"Опрос в архиве и больше не принимает ответы\", 409)", self.src)


class SurveyArchiveNotificationScopeTests(unittest.TestCase):
    """Архив выключает опрос и в колоколе, и в бейдже раздела."""

    def test_bell_skips_archived_surveys(self):
        src = _read(NOTIFICATIONS_SOURCES_PATH)
        surveys_start = src.index("def surveys(cursor, viewer, limit):")
        block = src[surveys_start:surveys_start + 2400]
        self.assertIn("s.archived_at IS NULL", block)

    def test_next_transition_ignores_archived_tests(self):
        src = _read(NOTIFICATIONS_SOURCES_PATH)
        self.assertEqual(src.count("AND s.is_active AND s.is_test AND s.archived_at IS NULL"), 2)

    def test_sidebar_badge_asks_the_server_for_a_number(self):
        src = _read(APP_JSX_PATH)
        self.assertIn("/api/surveys/pending_count", src)
        # Прежний способ — выгрузить весь список ради одной цифры — не должен
        # вернуться незаметно.
        self.assertNotIn("Fetch surveys badge count error", src.split("/api/surveys/pending_count")[0][-400:])
        self.assertNotIn("survey?.statistics?.pending_count", src)


class SurveyArchiveFrontendTests(unittest.TestCase):
    """Архив — отдельная вкладка списка, а не фильтр внутри рабочей очереди."""

    def setUp(self):
        self.src = _read(SURVEYS_VIEW_PATH)

    def test_scope_tabs_and_pagination(self):
        self.assertIn("const SCOPE_ACTIVE = 'active';", self.src)
        self.assertIn("const SCOPE_ARCHIVE = 'archive';", self.src)
        self.assertIn("scope: listScope", self.src)
        self.assertIn("listPages", self.src)
        self.assertIn("В архиве пока пусто", self.src)

    def test_search_is_debounced(self):
        self.assertIn("LIST_SEARCH_DEBOUNCE_MS", self.src)
        self.assertIn("setListQuery(listQueryInput.trim())", self.src)

    def test_archive_badge_on_the_card(self):
        self.assertIn("В архиве", self.src)
        self.assertIn("selectedSurvey?.is_archived", self.src)


if __name__ == '__main__':
    unittest.main()
