# -*- coding: utf-8 -*-
"""Проекты-доски раздела «Задачи» (задача #401).

Правила модуля task_projects.py проверяем целиком — он лист без базы и Flask.
Правила Database вытаскиваем AST'ом (конструктор лезет в базу) и исполняем, как
в test_task_multi_assignees. Там, где правило живёт в SQL и без базы его не
исполнить, — маркеры: иначе копии правила тихо разъедутся.

Настоящий SQL этих мест прогнан на стенде (свой Postgres, настоящий database.py):
номера под гонкой, откат без потери номера, видимость наблюдателя после снятия с
доски, прежний ключ после переноса, итерация регламента в проекте.
"""

import ast
import re
import textwrap
import unittest
from pathlib import Path
from typing import List, Optional

import task_projects
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
# Склейки монолита и базы (tests/source_cache): с 02.11.2026 класс Database разнесён
# на миксины db/*.py, а ручки «Задач» вынесены в tasks/routes.py.
DATABASE_PATH = source_cache.database_flat_path()
BOT_PATH = source_cache.monolith_flat_path()
TASKS_DIR = ROOT / "src" / "components" / "tasks"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


DATABASE_SOURCE = _read(DATABASE_PATH)
BOT_SOURCE = _read(BOT_PATH)


def _database_members(names):
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    found = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            first = min([node.lineno] + [item.lineno for item in node.decorator_list])
            found[node.name] = textwrap.dedent("".join(lines[first - 1:node.end_lineno]))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, node))
    missing = set(names) - set(found)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return found


def _module_members(names):
    module = source_cache.parse(DATABASE_SOURCE)
    found = {}
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.Assign)):
            targets = (
                [node.name] if isinstance(node, ast.FunctionDef)
                else [t.id for t in node.targets if isinstance(t, ast.Name)]
            )
            for name in targets:
                if name in names:
                    found[name] = ast.get_source_segment(DATABASE_SOURCE, node)
    missing = set(names) - set(found)
    assert not missing, f"в database.py нет: {sorted(missing)}"
    return found


def _method_source(name):
    return _database_members([name])[name]


NAMES = [
    "_task_viewer_is_observer",
    "_task_projects_admin",
    "_normalize_task_project_id",
    "_normalize_task_project_filter",
    "_normalize_task_watchers",
    "_require_task_watchers_tx",
    "_require_task_project_tx",
    "_task_observable_tx",
    "_normalize_task_section_id",
    "_normalize_task_section_filter",
    "_require_task_section_tx",
    "_TASK_COLLEAGUE_SV_SQL",
    "_TASK_PROJECT_MEMBER_SQL",
    "_TASK_WATCHER_SQL",
]


def _build_database():
    # Аннотации в вытащенных функциях ссылаются на typing — кладём его в пространство имён.
    namespace = {"task_projects": task_projects, "Optional": Optional, "List": List}
    for source in _module_members(
        ["normalize_role_value", "role_has_min", "ROLE_ALIASES", "ROLE_HIERARCHY", "_UNSET"]
    ).values():
        exec(source, namespace)
    body = _database_members(NAMES)
    class_source = "class FakeDatabase:\n" + "".join(
        textwrap.indent(body[name], "    ") + "\n" for name in NAMES
    )
    exec(class_source, namespace)
    return namespace


DB_NAMESPACE = _build_database()
FakeDatabase = DB_NAMESPACE["FakeDatabase"]
UNSET = DB_NAMESPACE["_UNSET"]

OWNER, ASSIGNEE, BOSS, VIEWER = 10, 20, 30, 40


class ProjectModuleTests(unittest.TestCase):
    """task_projects.py — ключ, номер, состав и тексты ошибок."""

    def test_code_is_latin_upper_and_two_to_ten_chars(self):
        self.assertEqual(task_projects.normalize_code(" hr "), "HR")
        self.assertEqual(task_projects.normalize_code("szov"), "SZOV")
        self.assertEqual(task_projects.normalize_code("A1"), "A1")
        for bad in ("", "H", "1HR", "ХР", "HR-1", "ABCDEFGHIJK", "H R"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                task_projects.normalize_code(bad)

    def test_task_key_round_trip(self):
        self.assertEqual(task_projects.parse_task_key("hr-125"), ("HR", 125))
        self.assertEqual(task_projects.parse_task_key(" #SZOV-7 "), ("SZOV", 7))
        self.assertEqual(task_projects.format_task_key("hr", "12"), "HR-12")
        for bad in ("HR-0", "HR", "125", "ХР-1", "HR-1-2", None, ""):
            with self.subTest(bad=bad):
                self.assertIsNone(task_projects.parse_task_key(bad))
        self.assertIsNone(task_projects.format_task_key(None, 5))
        self.assertIsNone(task_projects.format_task_key("HR", None))

    def test_user_ids_keep_order_and_fold_duplicates(self):
        normalize = task_projects.normalize_user_ids
        self.assertEqual(normalize("12, 7,12", 20, "BAD", "MANY"), [12, 7])
        self.assertEqual(normalize([3, "3", 4], 20, "BAD", "MANY"), [3, 4])
        self.assertEqual(normalize(None, 20, "BAD", "MANY"), [])
        self.assertEqual(normalize("", 20, "BAD", "MANY"), [])
        with self.assertRaisesRegex(ValueError, "BAD"):
            normalize("x", 20, "BAD", "MANY")
        with self.assertRaisesRegex(ValueError, "BAD"):
            normalize([-1], 20, "BAD", "MANY")
        with self.assertRaisesRegex(ValueError, "MANY"):
            normalize(list(range(1, 23)), 20, "BAD", "MANY")

    def test_members_last_level_wins_and_level_is_closed(self):
        self.assertEqual(
            task_projects.normalize_members([
                {"user_id": 5, "access_level": "member"},
                {"user_id": 6, "access_level": "board"},
                {"user_id": 5, "access_level": "board"},
            ]),
            [(5, "board"), (6, "board")],
        )
        self.assertEqual(task_projects.normalize_members([7]), [(7, "member")])
        with self.assertRaises(ValueError):
            task_projects.normalize_members([{"user_id": 5, "access_level": "admin"}])
        with self.assertRaises(ValueError):
            task_projects.normalize_members("5,6")

    def test_every_project_error_code_has_a_russian_text(self):
        """Код ошибки без текста уехал бы человеку английской строкой кода."""
        raised = set(re.findall(r'raise (?:ValueError|PermissionError)\("([A-Z_]+)"\)', DATABASE_SOURCE))
        project_codes = {
            code for code in raised
            if code.startswith(("PROJECT_", "WATCHER", "INVALID_PROJECT", "TOO_MANY_PROJECT", "TOO_MANY_WATCHERS"))
            or code in {"INVALID_WATCHERS", "WATCHERS_NEED_PROJECT", "INVALID_DEPARTMENT",
                        "INVALID_SECTION", "SECTION_NOT_IN_PROJECT"}
        }
        raised_in_module = {"INVALID_PROJECT_CODE", "PROJECT_NAME_REQUIRED", "INVALID_PROJECT_MEMBERS",
                            "TOO_MANY_PROJECT_MEMBERS", "INVALID_PROJECT_SECTIONS", "TOO_MANY_PROJECT_SECTIONS",
                            "PROJECT_SECTION_NAME_REQUIRED", "PROJECT_SECTION_DUPLICATE"}
        self.assertTrue(project_codes, "не нашли ни одного кода проектов в database.py")
        for code in project_codes | raised_in_module:
            with self.subTest(code=code):
                text = task_projects.error_message(code)
                self.assertTrue(text and re.search("[а-яА-Я]", text), code)

    def test_sections_keep_order_and_refuse_repeats(self):
        """Разделы доски («регулируемый набор разделов»): порядок — как прислали."""
        self.assertEqual(
            task_projects.normalize_sections([{"name": "  Подбор "}, {"id": 7, "name": "Адаптация"}]),
            [(None, "Подбор"), (7, "Адаптация")],
        )
        self.assertEqual(task_projects.normalize_sections(None), [])
        for bad, code in (
            ([{"name": "Подбор"}, {"name": "подбор"}], "PROJECT_SECTION_DUPLICATE"),
            ([{"name": "   "}], "PROJECT_SECTION_NAME_REQUIRED"),
            ([{"id": "x", "name": "А"}], "INVALID_PROJECT_SECTIONS"),
            ([{"id": 3, "name": "А"}, {"id": 3, "name": "Б"}], "INVALID_PROJECT_SECTIONS"),
            ("Подбор", "INVALID_PROJECT_SECTIONS"),
            ([{"name": f"Р{i}"} for i in range(task_projects.MAX_SECTIONS + 1)], "TOO_MANY_PROJECT_SECTIONS"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                task_projects.normalize_sections(bad)

    def test_seed_is_the_list_from_the_task(self):
        codes = [code for code, _name, _dept in task_projects.SEED_PROJECTS]
        self.assertEqual(codes, ["HR", "FIN", "ACC", "ITH", "BE", "IC", "SZOV", "SALES", "MKT"])
        for code in codes:
            self.assertEqual(task_projects.normalize_code(code), code)


class ProjectRulesTests(unittest.TestCase):
    """Правила Database: кто только смотрит, кто настраивает, что приходит фильтром."""

    def setUp(self):
        self.db = FakeDatabase()

    def _observer(self, role, viewer, assignees=({"id": ASSIGNEE},), creator=OWNER, requested_by=None):
        return self.db._task_viewer_is_observer(role, viewer, creator, requested_by, list(assignees))

    def test_project_member_and_watcher_only_watch(self):
        """Чужая задача проекта у рядового, тренера и СВ — только на просмотр."""
        for role in ("hr_manager", "trainer", "sv", "accounting_manager"):
            with self.subTest(role=role):
                self.assertTrue(self._observer(role, VIEWER))

    def test_participants_are_never_observers(self):
        self.assertFalse(self._observer("hr_manager", OWNER))
        self.assertFalse(self._observer("hr_manager", ASSIGNEE))
        self.assertFalse(self._observer("hr_manager", BOSS, requested_by=BOSS))

    def test_admin_of_the_section_acts_by_role(self):
        self.assertFalse(self._observer("admin", VIEWER))
        self.assertFalse(self._observer("super_admin", VIEWER))

    def test_only_full_admins_configure_projects(self):
        """Глава-админ идёт в раздел под 'sv' — настраивать доски ему нельзя."""
        self.assertTrue(self.db._task_projects_admin("admin"))
        self.assertTrue(self.db._task_projects_admin("super_admin"))
        for role in ("sv", "trainer", "hr_manager", "operator", None):
            with self.subTest(role=role):
                self.assertFalse(self.db._task_projects_admin(role))

    def test_project_id_and_filter_values(self):
        self.assertIsNone(self.db._normalize_task_project_id(None))
        self.assertIsNone(self.db._normalize_task_project_id(""))
        self.assertIsNone(self.db._normalize_task_project_id("none"))
        self.assertIsNone(self.db._normalize_task_project_id(UNSET))
        self.assertEqual(self.db._normalize_task_project_id("7"), 7)
        with self.assertRaisesRegex(ValueError, "INVALID_PROJECT"):
            self.db._normalize_task_project_id("x")
        with self.assertRaisesRegex(ValueError, "INVALID_PROJECT"):
            self.db._normalize_task_project_id(-3)
        self.assertIsNone(self.db._normalize_task_project_filter("all"))
        self.assertEqual(self.db._normalize_task_project_filter("None"), "none")
        self.assertEqual(self.db._normalize_task_project_filter("12"), 12)
        with self.assertRaisesRegex(ValueError, "INVALID_TASK_PROJECT_FILTER"):
            self.db._normalize_task_project_filter("0")

    def test_watchers_unset_means_keep(self):
        self.assertIsNone(self.db._normalize_task_watchers(UNSET))
        self.assertEqual(self.db._normalize_task_watchers("5,5,6"), [5, 6])

    def test_watcher_without_board_access_is_refused(self):
        class _Db(FakeDatabase):
            def _task_project_board_user_ids_tx(self, cursor, project_id, user_ids):
                return {5}

        db = _Db()
        db._require_task_watchers_tx(None, 1, [])
        db._require_task_watchers_tx(None, 1, [5])
        with self.assertRaisesRegex(ValueError, "WATCHER_NO_PROJECT_ACCESS"):
            db._require_task_watchers_tx(None, 1, [5, 6])
        with self.assertRaisesRegex(ValueError, "WATCHERS_NEED_PROJECT"):
            db._require_task_watchers_tx(None, None, [5])


class _RowCursor:
    """Курсор, отвечающий заранее заданной строкой; запросы записывает."""

    def __init__(self, row=None):
        self.row = row
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.row


class ProjectAccessExecutionTests(unittest.TestCase):
    """Права на постановку в проект и правило «только смотрит» — исполнением, не строками."""

    def setUp(self):
        self.db = FakeDatabase()

    def _require(self, row, role="hr_manager"):
        cursor = _RowCursor(row)
        self.db._require_task_project_tx(cursor, 10, VIEWER, role)
        return cursor

    def test_missing_archived_and_foreign_projects_are_refused(self):
        with self.assertRaisesRegex(ValueError, "PROJECT_NOT_FOUND"):
            self._require(None)
        with self.assertRaisesRegex(ValueError, "PROJECT_ARCHIVED"):
            self._require((True, "member"))
        with self.assertRaisesRegex(ValueError, "PROJECT_ARCHIVED"):
            self._require((True, None), role="admin")
        with self.assertRaisesRegex(ValueError, "PROJECT_FORBIDDEN"):
            self._require((False, None))
        # Глава-админ идёт в раздел под 'sv' — без места в составе в проект не поставит.
        with self.assertRaisesRegex(ValueError, "PROJECT_FORBIDDEN"):
            self._require((False, None), role="sv")

    def test_board_access_or_section_admin_may_create(self):
        self._require((False, "member"))
        self._require((False, "board"))
        self._require((False, None), role="admin")
        cursor = self._require((False, "board"))
        # Сверяется доступ именно смотрящего к именно этому проекту.
        self.assertEqual(cursor.queries[0][1], (VIEWER, 10))

    def test_section_belongs_to_the_task_board(self):
        """Раздел — только со своей доски: разделы у каждой доски свои."""
        self.db._require_task_section_tx(_RowCursor(None), None, 10)   # без раздела — можно
        with self.assertRaisesRegex(ValueError, "SECTION_NOT_IN_PROJECT"):
            self.db._require_task_section_tx(_RowCursor((1,)), 5, None)   # раздел без доски
        with self.assertRaisesRegex(ValueError, "SECTION_NOT_IN_PROJECT"):
            self.db._require_task_section_tx(_RowCursor(None), 5, 10)     # раздел чужой доски
        cursor = _RowCursor((1,))
        self.db._require_task_section_tx(cursor, 5, 10)
        self.assertEqual(cursor.queries[0][1], (5, 10))

    def test_section_values(self):
        self.assertIsNone(self.db._normalize_task_section_id(""))
        self.assertIsNone(self.db._normalize_task_section_id(None))
        self.assertEqual(self.db._normalize_task_section_id("4"), 4)
        with self.assertRaisesRegex(ValueError, "INVALID_SECTION"):
            self.db._normalize_task_section_id("x")
        self.assertIsNone(self.db._normalize_task_section_filter("all"))
        self.assertEqual(self.db._normalize_task_section_filter("none"), "none")
        with self.assertRaisesRegex(ValueError, "INVALID_TASK_SECTION_FILTER"):
            self.db._normalize_task_section_filter("-1")

    def test_observer_query_gates_the_colleague_path_by_project(self):
        cursor = _RowCursor((True,))
        self.assertTrue(self.db._task_observable_tx(cursor, "sv", VIEWER, 77, OWNER))
        sql, params = cursor.queries[0]
        self.assertIn("(t.project_id IS NULL AND EXISTS (SELECT 1 FROM users task_sv_creator", sql)
        self.assertEqual(params, (77, VIEWER, VIEWER, VIEWER))
        cursor = _RowCursor((False,))
        self.assertFalse(self.db._task_observable_tx(cursor, "hr_manager", VIEWER, 77, OWNER))
        sql, params = cursor.queries[0]
        # Не СВ — пути коллеги нет вовсе, остаются участник проекта и наблюдатель.
        self.assertNotIn("task_sv_creator", sql)
        self.assertEqual(params, (77, VIEWER, VIEWER))


class ProjectSqlContractTests(unittest.TestCase):
    """Правила, живущие в SQL: проверяем, что они на своих местах."""

    def test_schema_is_idempotent_and_numbers_are_unique(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS task_projects (", DATABASE_SOURCE)
        self.assertIn("CHECK (code ~ '^[A-Z][A-Z0-9]{1,9}$')", DATABASE_SOURCE)
        self.assertEqual(task_projects.CODE_PATTERN, "^[A-Z][A-Z0-9]{1,9}$")
        self.assertIn("CHECK (access_level IN ('member', 'board'))", DATABASE_SOURCE)
        self.assertIn("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES task_projects(id) ON DELETE SET NULL;", DATABASE_SOURCE)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_project_number", DATABASE_SOURCE)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_task_project_members_user", DATABASE_SOURCE)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_task_watchers_user", DATABASE_SOURCE)
        # Стартовые проекты — только на пустой таблице: удалённое не воскресает.
        self.assertIn("WHERE NOT EXISTS (SELECT 1 FROM task_projects)", DATABASE_SOURCE)

    def test_number_is_taken_under_the_project_row_lock(self):
        source = _method_source("_take_task_project_number_tx")
        self.assertIn("SET next_number = next_number + 1", source)
        self.assertIn("RETURNING next_number - 1, code", source)

    def test_personal_scope_sees_project_tasks_through_membership(self):
        start = DATABASE_SOURCE.index("    def _task_scope_filters(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def get_tasks_for_requester(", start)]
        self.assertIn('project_sql = f"{self._TASK_PROJECT_MEMBER_SQL} OR {self._TASK_WATCHER_SQL}"', block)
        self.assertIn("(t.project_id IS NULL AND {self._TASK_COLLEAGUE_SV_SQL}) OR {project_sql}", block)
        self.assertIn('conditions.append(f"({participant_sql} OR {project_sql})")', block)

    def test_member_sees_all_board_level_sees_only_watched(self):
        member_sql = _database_members(["_TASK_PROJECT_MEMBER_SQL"])["_TASK_PROJECT_MEMBER_SQL"]
        self.assertIn("task_pm_me.access_level = 'member'", member_sql)
        watcher_sql = _database_members(["_TASK_WATCHER_SQL"])["_TASK_WATCHER_SQL"]
        # Наблюдатель видит задачу, только пока он в составе доски её проекта.
        self.assertIn("JOIN task_project_members task_pm_w", watcher_sql)
        self.assertIn("task_pm_w.project_id = t.project_id", watcher_sql)

    def test_every_read_path_shares_the_observer_rule(self):
        """Карточка открылась — её отчёты, уточнения, файлы и фото тоже открываются."""
        for method, marker in (
            ("_task_report_access_tx", "self._task_observable_tx(cursor, role, requester_id, task_id, created_by)"),
            ("_task_message_access_tx", "self._task_observable_tx(cursor, role, requester_id, row[0], row[1])"),
            ("get_task_attachment_for_requester", "self._task_observable_tx(cursor, role, requester_id, row[9], created_by)"),
            ("list_task_photo_attachments_for_requester", "self._task_observable_tx(cursor, role, requester_id, task_id, created_by)"),
        ):
            with self.subTest(method=method):
                self.assertIn(marker, _method_source(method))
        observable = _method_source("_task_observable_tx")
        # Коллега-СВ — только у задач вне проектов; дальше участник и наблюдатель.
        self.assertIn('f"(t.project_id IS NULL AND {self._TASK_COLLEAGUE_SV_SQL})"', observable)
        self.assertIn("self._TASK_PROJECT_MEMBER_SQL", observable)
        self.assertIn("self._TASK_WATCHER_SQL", observable)

    def test_actions_are_not_widened_by_projects(self):
        """Видимость через проект — только чтение: правило действий не трогали."""
        visible = _method_source("_task_visible_for_requester")
        self.assertNotIn("project", visible)
        self.assertNotIn("watcher", visible)

    def test_move_keeps_the_old_key_working(self):
        source = _method_source("edit_task")
        self.assertIn("INSERT INTO task_key_aliases (code, number, task_id, project_id)", source)
        self.assertIn("self._require_task_project_tx(cursor, project_id_new, requester_id, role)", source)
        key_sql = _database_members(["_TASK_KEY_MATCH_SQL"])["_TASK_KEY_MATCH_SQL"]
        self.assertIn("FROM task_key_aliases task_key_a", key_sql)

    def test_colleague_path_never_opens_project_tasks(self):
        start = DATABASE_SOURCE.index("    def _task_scope_filters(")
        block = DATABASE_SOURCE[start:DATABASE_SOURCE.index("    def get_tasks_for_requester(", start)]
        self.assertIn("(t.project_id IS NULL AND {self._TASK_COLLEAGUE_SV_SQL})", block)

    def test_create_checks_the_board_before_taking_a_number(self):
        source = _method_source("create_task")
        check = source.index("self._require_task_project_tx(cursor, project_id_norm, created_by_id or 0, requester_role)")
        take = source.index("self._take_task_project_number_tx(cursor, project_id_norm)")
        self.assertLess(check, take)
        self.assertIn("self._require_task_watchers_tx(cursor, project_id_norm, watcher_ids_norm)", source)

    def test_edit_cannot_pull_a_task_off_its_board(self):
        """Пустой проект в правке — «не менять»: форма без проекта не сжигает номер."""
        source = _method_source("edit_task")
        self.assertIn(
            "project_id_new = project_id_requested if project_id_requested is not None else current_project_id",
            source,
        )
        self.assertIn("project_changed = project_id_new != current_project_id", source)

    def test_old_watcher_does_not_lock_the_task(self):
        source = _method_source("edit_task")
        self.assertIn("[pid for pid in watcher_ids_new if pid not in set(current_watcher_ids)]", source)
        # Сняли с доски — наблюдения снимаются, карточка их не показывает.
        members = _method_source("_replace_task_project_members_tx")
        self.assertIn("DELETE FROM task_watchers tw", members)
        self.assertIn("AND NOT (tw.user_id = ANY(%s))", members)
        listing = _method_source("get_tasks_for_requester")
        self.assertIn("JOIN task_project_members tw_board", listing)

    def test_switcher_lists_only_boards_with_access(self):
        """ТЗ: «в списке доступны только проекты, к которым у сотрудника есть доступ»."""
        source = _method_source("get_task_projects")
        self.assertRegex(source, r"access = 'admin' if is_admin else row\[6\]\s+if access is None:\s+continue")

    def test_moving_a_task_drops_its_section_and_foreign_ids_are_refused(self):
        edit = _method_source("edit_task")
        self.assertRegex(edit, r"elif project_changed:\s+section_id_new = None")
        self.assertIn("self._require_task_section_tx(cursor, section_id_new, project_id_new)", edit)
        create = _method_source("create_task")
        self.assertIn("self._require_task_section_tx(cursor, section_id_norm, project_id_norm)", create)
        sections = _method_source("_replace_task_project_sections_tx")
        self.assertIn('raise ValueError("INVALID_PROJECT_SECTIONS")', sections)
        self.assertIn("REFERENCES task_project_sections(id) ON DELETE SET NULL", DATABASE_SOURCE)

    def test_regulation_iteration_lands_on_the_template_board(self):
        source = _method_source("materialize_due_regulation_tasks")
        self.assertIn("t.production_brief, t.project_id", source)
        self.assertIn("self._take_task_project_number_tx(", source)
        self.assertIn("self._sync_task_watchers_tx(", source)


class ProjectRouteContractTests(unittest.TestCase):
    def test_project_routes_go_through_the_task_guard(self):
        for route in ("@app.route('/api/tasks/projects', methods=['GET', 'POST', 'OPTIONS'])",
                      "@app.route('/api/tasks/projects/<int:project_id>', methods=['PATCH', 'OPTIONS'])"):
            self.assertIn(route, BOT_SOURCE)
        for handler in ("def handle_task_projects():", "def handle_task_project(project_id):"):
            start = BOT_SOURCE.index(handler)
            block = BOT_SOURCE[start:start + 1800]
            with self.subTest(handler=handler):
                self.assertIn("_task_route_guard()", block)
                # Роль — та, под которой человек в разделе: глава-админ здесь 'sv'.
                self.assertIn("getattr(g, 'effective_task_role', requester[3])", block)

    def test_task_routes_pass_project_and_watchers(self):
        start = BOT_SOURCE.index("def handle_tasks():")
        block = BOT_SOURCE[start:BOT_SOURCE.index("def handle_single_task(", start)]
        # Одним фрагментом: «requester_role=requester_role» есть и в вызове списка,
        # а создание обязано идти под ролью раздела (глава-админ здесь 'sv').
        self.assertIn(
            "project_id=project_id_raw,\n                watcher_ids=watcher_ids_raw,\n"
            "                requester_role=requester_role,\n                section_id=section_id_raw\n",
            block,
        )
        self.assertIn("task_key=task_key_filter", block)
        self.assertIn('"key": created.get("key"),', block)
        patch_start = BOT_SOURCE.index("def handle_single_task(")
        patch_block = BOT_SOURCE[patch_start:patch_start + 9000]
        self.assertIn('edit_kwargs["project_id"] = data.get(\'project_id\')', patch_block)
        self.assertIn('edit_kwargs["watcher_ids"] = data.get(\'watcher_ids\') or []', patch_block)

    def test_watchers_only_edit_does_not_ping_assignees(self):
        start = BOT_SOURCE.index("def handle_single_task(")
        block = BOT_SOURCE[start:start + 12000]
        self.assertIn("notify_fields = [field for field in (result.get('changed_fields') or []) if field != 'watchers']", block)
        self.assertIn("if result.get('updated') and notify_fields:", block)
        self.assertIn("changed_fields=notify_fields,", block)

    def test_project_patch_is_strict_about_members_and_archive(self):
        start = BOT_SOURCE.index("def handle_task_project(project_id):")
        block = BOT_SOURCE[start:start + 2600]
        self.assertIn("if not isinstance(data.get('members'), list):", block)
        self.assertIn("if not isinstance(data.get('is_archived'), bool):", block)

    def test_project_key_can_be_changed(self):
        # Владелец 06.11.2026: ключ доски меняют в её настройках; поведение —
        # tests/test_task_project_code_postgres.py (старые ключи, чужой и свой прежний).
        start = BOT_SOURCE.index("def handle_task_project(project_id):")
        block = BOT_SOURCE[start:start + 2600]
        self.assertIn("for key in ('name', 'department_id', 'code'):", block)
        update = _method_source("update_task_project")
        self.assertIn("sections=_UNSET, code=_UNSET):", update)
        self.assertIn("self._rename_task_project_code_tx(cursor, project_id, current_code, code_new)", update)
        rename = _method_source("_rename_task_project_code_tx")
        self.assertIn('raise ValueError("PROJECT_CODE_TAKEN")', rename)
        self.assertIn('raise ValueError("PROJECT_CODE_USED_BEFORE")', rename)
        self.assertIn("AND a.project_id IS DISTINCT FROM %s", rename)
        create = _method_source("create_task_project")
        self.assertLess(create.index("FROM task_key_aliases WHERE code = %s"), create.index("INSERT INTO task_projects"))

    def test_links_carry_the_key(self):
        start = BOT_SOURCE.index("def _build_task_deep_link(")
        block = BOT_SOURCE[start:BOT_SOURCE.index("def _build_task_notification_reply_markup(", start)]
        self.assertIn('f"task={task_projects.format_task_key(*parsed_key)}"', block)
        self.assertIn("task_key = db.get_task_key(task_id)", block)

    def test_key_pattern_is_the_same_on_every_side(self):
        python_pattern = task_projects._TASK_KEY_RE.pattern
        for path in (TASKS_DIR / "taskProjects.js", TASKS_DIR / "taskFocus.js", ROOT / "src" / "App.jsx"):
            with self.subTest(path=path.name):
                self.assertIn("#?([A-Za-z][A-Za-z0-9]{1,9})-(\\d{1,9})", _read(path))
        self.assertIn("#?([A-Za-z][A-Za-z0-9]{1,9})-(\\d{1,9})", python_pattern)


def _bot_functions(names, namespace):
    """Функции bot_schedule2.py по именам — исполняем их, как в остальных тестах раздела."""
    module = source_cache.parse(BOT_SOURCE)
    found = {}
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = ast.get_source_segment(BOT_SOURCE, node)
    missing = set(names) - set(found)
    assert not missing, f"в bot_schedule2.py нет: {sorted(missing)}"
    for name in names:
        exec(found[name], namespace)
    return namespace


class ProjectFrontendContractTests(unittest.TestCase):
    """Форма не вынимает задачу с доски и не «прилипает» к прошлой доске."""

    @classmethod
    def setUpClass(cls):
        cls.view = _read(TASKS_DIR / "TasksView.jsx")
        cls.app = _read(ROOT / "src" / "App.jsx")

    def test_project_and_watchers_leave_only_when_changed(self):
        self.assertIn("initialProjectId: task?.project?.id ? String(task.project.id) : '',", self.view)
        self.assertIn(
            "...(String(values.projectId || '') !== String(values.initialProjectId || '') && Number(values.projectId) > 0",
            self.view,
        )
        self.assertIn("!sameIdSet(values.watcherIds, values.initialWatcherIds)", self.view)

    def test_auto_project_is_replaced_but_a_choice_is_kept(self):
        self.assertIn("if (prev.projectId && !prev.projectAuto) return prev;", self.view)
        self.assertIn("projectAuto: false,", self.view)

    def test_pinned_quick_create_lands_on_the_pinned_board(self):
        self.assertIn("projectId: task?.project?.id ? String(task.project.id) : '',", self.view)
        self.assertIn("if (Number(payload.project_id) > 0) body.append('project_id', String(payload.project_id));", self.app)

    def test_settings_edit_the_key_and_reload_cards(self):
        sheet = _read(TASKS_DIR / "TaskProjectsSheet.jsx")
        # Поле ключа — и у новой доски, и у существующей; уходит, только если сменили.
        self.assertNotIn("Ключ не меняется", sheet)
        self.assertIn("...(codeChanged ? { code: draft.code } : {})", sheet)
        self.assertIn("{draft.originalCode}-12 станет {draft.code}-12.", sheet)
        # Проверка ключа — общей функцией (её поведение — tests/task_projects.test.mjs).
        self.assertIn("projectCodeIssue(", sheet)
        self.assertNotIn("const PROJECT_CODE_RE", sheet)
        # Отказ сервера по ключу — красным у поля, а не всплывашкой.
        self.assertIn("['PROJECT_CODE_TAKEN', 'PROJECT_CODE_USED_BEFORE', 'INVALID_PROJECT_CODE'].includes(refusal)", sheet)
        self.assertIn("await onSaved?.({ codeChanged });", sheet)
        # Карточки со старыми ключами перечитываются.
        self.assertIn("if (codeChanged) await refreshTasksData();", self.view)

    def test_task_key_does_not_leak_into_other_links(self):
        chat = _read(ROOT / "src" / "components" / "wazzup" / "chatLink.js")
        self.assertIn("const FOREIGN_QUERY_PARAMS = ['task_id', 'task', 'ticket_id', 'article'];", chat)
        self.assertEqual(self.app.count("url.searchParams.delete(TASK_KEY_QUERY_PARAM);"), 2)


class CliKeyTests(unittest.TestCase):
    """CLI доски принимает и номер, и ключ задачи проекта во всех командах."""

    @classmethod
    def setUpClass(cls):
        import sys
        if str(ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(ROOT / "scripts"))
        import task_board
        cls.cli = task_board

    def test_task_ref_parses_ids_and_keys(self):
        import argparse
        self.assertEqual(self.cli.task_ref("401"), 401)
        self.assertEqual(self.cli.task_ref("hr-12"), "HR-12")
        self.assertEqual(self.cli.task_ref("#SZOV-007"), "SZOV-7")
        for bad in ("HR-0", "ХР-1", "x", ""):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                self.cli.task_ref(bad)

    def test_every_task_command_takes_a_key(self):
        source = _read(ROOT / "scripts" / "task_board.py")
        self.assertNotIn("add_argument('task_id', type=int)", source)
        # 16 — вместе с `score` (балл приоритета суперадмина, 03.11.2026).
        self.assertEqual(source.count("add_argument('task_id', type=task_ref"), 16)


class TelegramKeyTests(unittest.TestCase):
    """Ключ задачи в Telegram: перед темой и в ссылке «Открыть задачу»."""

    def setUp(self):
        import html
        from urllib.parse import urlparse

        self.ns = _bot_functions(
            ["_truncate_for_telegram", "_escape_telegram_html", "_build_task_subject_notification_html",
             "_build_task_deep_link"],
            {"html": html, "urlparse": urlparse, "task_projects": task_projects,
             "TASK_WEB_APP_BASE_URL": "https://icore.example/OTP", "_is_allowed_origin": lambda origin: False},
        )

    def test_subject_carries_the_key_outside_the_link(self):
        build = self.ns["_build_task_subject_notification_html"]
        self.assertEqual(
            build({"id": 5, "subject": "Шаблон <приказа>", "key": "HR-12"}, "https://x/?a=1&b=2"),
            'HR-12 · <a href="https://x/?a=1&amp;b=2">Шаблон &lt;приказа&gt;</a>',
        )
        # Без проекта — как было, никакого префикса.
        self.assertEqual(build({"id": 5, "subject": "Тема"}), "Тема")

    def test_deep_link_uses_the_key_and_falls_back_to_the_id(self):
        link = self.ns["_build_task_deep_link"]
        self.assertEqual(link(401, task_key="hr-12"), "https://icore.example/OTP?view=tasks&task=HR-12")
        self.assertEqual(link(401), "https://icore.example/OTP?view=tasks&task_id=401")
        self.assertEqual(link(401, task_key="мусор"), "https://icore.example/OTP?view=tasks&task_id=401")
        self.assertEqual(link(0, task_key="HR-1"), "")


if __name__ == "__main__":
    unittest.main()
