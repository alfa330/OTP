# -*- coding: utf-8 -*-
"""Исполнители и участники доски (владелец 26.11.2026): «у каждой доски свои
исполнители, их можно выбирать, а остальные как участники».

Задачу доски поручают только её исполнителям (task_project_members.is_executor) —
всем одной задачей, без потолка в десять, или поштучно; участник доски видит её
задачи, но исполнителем не становится. Доска без людей — как раньше; доска с
людьми, но без исполнителей, задачу не принимает.

Правила гоняем исполнением, без базы: настоящие методы Database (AST'ом из склейки,
как в test_task_projects.py) на подставленном курсоре, настоящая сверка «кому вправе
поручить» и настоящая рассылка из tasks/routes.py. Что значит SQL — исполнители
доски и сверка правила — в tests/test_task_board_assignees_postgres.py.
"""
import ast
import concurrent.futures
import copy
import textwrap
import threading
import types
import unittest
from pathlib import Path
from typing import Optional

import task_projects
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_SOURCE = source_cache.database_flat_path().read_text(encoding="utf-8-sig")
ROUTES_PATH = ROOT / "tasks" / "routes.py"
ROUTES_SOURCE = ROUTES_PATH.read_text(encoding="utf-8")
TASKS_VIEW = (ROOT / "src" / "components" / "tasks" / "TasksView.jsx").read_text(encoding="utf-8")
PROJECTS_SHEET = (ROOT / "src" / "components" / "tasks" / "TaskProjectsSheet.jsx").read_text(encoding="utf-8")

NAMES = [
    "TASK_MAX_ASSIGNEES",
    "TASK_MAX_ASSIGNEES_PROJECT",
    "_normalize_task_assignees",
    "_task_project_assignable_ids_tx",
    "_require_task_project_assignees_tx",
    "_task_projects_admin",
    "_require_task_project_tx",
    "_task_assignee_ids_tx",
    "get_task_extra_assignable_ids",
]

# Люди выдуманы: 1 — постановщик, 2..19 — исполнители доски 10, 60..61 — её участники,
# 70..81 — исполнители доски 20, 62 — единственный человек доски 35 (участник),
# 50+ — посторонние. Доска 30 — без людей.
EXECUTORS = set(range(2, 20))
PARTICIPANTS_10 = {60, 61}
TEAM_20 = set(range(70, 82))
WATCHER_35 = 62
OUTSIDER, OTHER = 50, 51


def _class_members(names):
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Database"
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
            targets = ([node.name] if isinstance(node, ast.FunctionDef)
                       else [t.id for t in node.targets if isinstance(t, ast.Name)])
            for name in targets:
                if name in names:
                    found[name] = ast.get_source_segment(DATABASE_SOURCE, node)
    missing = set(names) - set(found)
    assert not missing, f"в склейке нет: {sorted(missing)}"
    return found


def _build_database():
    namespace = {"task_projects": task_projects, "_UNSET": object(), "Optional": Optional}
    # Роли — для _task_projects_admin (кто вправе ставить на любую доску).
    for source in _module_members(["normalize_role_value", "role_has_min", "ROLE_ALIASES", "ROLE_HIERARCHY"]).values():
        exec(source, namespace)
    body = _class_members(NAMES)
    source = "class FakeDatabase:\n" + "".join(textwrap.indent(body[name], "    ") + "\n" for name in NAMES)
    exec(source, namespace)
    return namespace["FakeDatabase"]


FakeDatabase = _build_database()


class _ExecutorCursor:
    """Курсор поверх «состава доски»: исполнители, участники, доступ к ней, состав задачи.

    executors_by_project: {доска: {исполнитель, ...}};
    participants_by_project: {доска: {участник, ...}} — в составе, но не исполнители."""

    def __init__(self, executors_by_project=None, task_projects_by_id=None, access=None, crews=None,
                 archived=(), participants_by_project=None):
        self.executors_by_project = executors_by_project or {}
        self.participants_by_project = participants_by_project or {}
        self.task_projects_by_id = task_projects_by_id or {}
        self.access = access or {}            # (project_id, user_id) -> 'member' | 'board'
        self.crews = crews or {}              # task_id -> [user_id, ...]
        self.archived = set(archived)
        self.queries = []
        self._rows = []

    def execute(self, query, params=None):
        sql = " ".join(query.split())
        self.queries.append((sql, params))
        if sql.startswith("SELECT pm.user_id, pm.is_executor FROM task_project_members pm"):
            executors = self.executors_by_project.get(params[0], set())
            participants = self.participants_by_project.get(params[0], set())
            self._rows = sorted([(person_id, True) for person_id in executors]
                                + [(person_id, False) for person_id in participants])
        elif sql.startswith("SELECT project_id FROM tasks"):
            project_id = self.task_projects_by_id.get(params[0])
            self._rows = [(project_id,)] if params[0] in self.task_projects_by_id else []
        elif sql.startswith("SELECT p.is_archived, pm.access_level FROM task_projects p"):
            user_id, project_id = params
            known = (set(self.executors_by_project) | set(self.participants_by_project)
                     | {pid for pid in self.task_projects_by_id.values() if pid})
            self._rows = ([(project_id in self.archived, self.access.get((project_id, user_id)))]
                          if project_id in known else [])
        elif sql.startswith("SELECT user_id FROM task_assignees WHERE task_id"):
            self._rows = [(person_id,) for person_id in self.crews.get(params[0], [])]
        else:
            raise AssertionError(f"неожиданный запрос: {sql}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class AssigneeRuleTests(unittest.TestCase):
    """_require_task_project_assignees_tx — правило владельца исполнением."""

    def setUp(self):
        self.db = FakeDatabase()
        # Доска 10 — исполнители и двое участников, 20 — только исполнители,
        # 30 — без людей, 35 — люди есть, исполнителей нет.
        self.cursor = _ExecutorCursor(
            {10: set(EXECUTORS), 20: set(TEAM_20), 30: set()},
            participants_by_project={10: set(PARTICIPANTS_10), 35: {WATCHER_35}},
        )

    def _check(self, project_id, ids, current=(), recheck_all=False):
        self.db._require_task_project_assignees_tx(self.cursor, project_id, ids, current, recheck_all=recheck_all)

    def test_board_takes_only_its_executors(self):
        """«У каждой доски свои исполнители… а остальные как участники»."""
        self._check(20, [70, 71])
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(20, [70, OUTSIDER])
        # Участник доски — в её составе, но исполнителем его не сделать.
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, [2, 60])
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, [61])
        # «Все» исполнители — больше десяти одной задачей.
        self._check(20, sorted(TEAM_20))

    def test_board_without_people_and_no_board_assign_as_before(self):
        self._check(30, [OUTSIDER, OTHER])
        self._check(None, [OUTSIDER])

    def test_board_with_people_but_no_executors_takes_nobody(self):
        """Люди есть, исполнителей нет — «как раньше» не включается: участники остаются участниками."""
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(35, [WATCHER_35])
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(35, [OUTSIDER])

    def test_everyone_on_the_board_lifts_the_ceiling_of_ten(self):
        """«Все» — восемнадцать исполнителей одной задачей: потолок десяти на них не действует."""
        everyone = sorted(EXECUTORS)
        self.assertGreater(len(everyone), self.db.TASK_MAX_ASSIGNEES)
        self._check(10, everyone)

    def test_ceiling_stays_for_anyone_else(self):
        eleven = list(range(100, 111))
        with self.assertRaisesRegex(ValueError, "TOO_MANY_ASSIGNEES"):
            self._check(30, eleven)          # доска без людей
        with self.assertRaisesRegex(ValueError, "TOO_MANY_ASSIGNEES"):
            self._check(None, eleven)        # задача без доски
        self._check(None, eleven[:10])

    def test_old_crew_does_not_lock_the_task(self):
        """Поставили до привязки (или человека сделали участником) — правка темы проходит."""
        old_crew = [OUTSIDER, OTHER, 2, 60]
        self._check(10, old_crew, current=old_crew)
        # Убрать человека из такой задачи тоже можно — новых в составе нет.
        self._check(10, [OUTSIDER, 2], current=old_crew)
        # А добавить постороннего или участника — нельзя.
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, old_crew + [52], current=old_crew)
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, [2, 61], current=[2])

    def test_task_for_everyone_keeps_editing_after_executors_become_participants(self):
        everyone = sorted(EXECUTORS)
        self.cursor.executors_by_project[10] = {2, 3}
        self.cursor.participants_by_project[10] = set(EXECUTORS) - {2, 3}
        self._check(10, everyone, current=everyone)           # правка без смены состава
        self._check(10, everyone[:-1], current=everyone)      # убрали одного
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, everyone + [OUTSIDER], current=everyone)

    def test_moving_to_a_bound_board_checks_the_whole_crew(self):
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, [2, OUTSIDER], current=[2, OUTSIDER], recheck_all=True)
        # Участник новой доски при переносе тоже не проходит.
        with self.assertRaisesRegex(ValueError, "ASSIGNEE_NOT_PROJECT_EXECUTOR"):
            self._check(10, [2, 60], current=[2, 60], recheck_all=True)
        self._check(10, [2, 3], current=[2, 3], recheck_all=True)

    def test_moving_a_big_crew_to_a_free_board_is_not_a_new_crew(self):
        """Перенос людей в составе не прибавляет — потолок считает только новых."""
        everyone = sorted(EXECUTORS)
        self._check(30, everyone, current=everyone, recheck_all=True)

    def test_nothing_new_means_no_query(self):
        self._check(10, [2, OUTSIDER], current=[OUTSIDER, 2])
        self.assertEqual(self.cursor.queries, [])

    def test_executors_query_skips_fired_and_tells_no_people_from_no_executors(self):
        self.assertIsNone(self.db._task_project_assignable_ids_tx(self.cursor, None))
        self.assertEqual(self.cursor.queries, [])
        # Только исполнители; участники — нет.
        self.assertEqual(self.db._task_project_assignable_ids_tx(self.cursor, 10), EXECUTORS)
        self.assertEqual(self.db._task_project_assignable_ids_tx(self.cursor, 20), TEAM_20)
        # Без людей — None («как раньше»), люди без исполнителей — пустое множество.
        self.assertIsNone(self.db._task_project_assignable_ids_tx(self.cursor, 30))
        self.assertEqual(self.db._task_project_assignable_ids_tx(self.cursor, 35), set())
        sql, params = self.cursor.queries[0]
        self.assertIn("COALESCE(u.status, 'working') <> 'fired'", sql)
        self.assertIn("pm.is_executor", sql)
        self.assertEqual(params, (10,))

    def test_normalizer_ceiling_is_a_parameter(self):
        twelve = list(range(1, 13))
        with self.assertRaisesRegex(ValueError, "TOO_MANY_ASSIGNEES"):
            self.db._normalize_task_assignees(twelve)
        self.assertEqual(self.db._normalize_task_assignees(twelve, limit=self.db.TASK_MAX_ASSIGNEES_PROJECT), twelve)
        self.assertEqual(self.db.TASK_MAX_ASSIGNEES_PROJECT, task_projects.MAX_MEMBERS)
        with self.assertRaisesRegex(ValueError, "TOO_MANY_ASSIGNEES"):
            self.db._normalize_task_assignees(list(range(1, task_projects.MAX_MEMBERS + 2)),
                                              limit=self.db.TASK_MAX_ASSIGNEES_PROJECT)


class ExtraAssignableTests(unittest.TestCase):
    """get_task_extra_assignable_ids — кого можно сверх списка получателей (разбор 24.11.2026)."""

    def _db(self, cursor):
        db = FakeDatabase()

        class _Ctx:
            def __enter__(self_inner):
                return cursor

            def __exit__(self_inner, *exc):
                return False

        db._get_cursor = lambda: _Ctx()
        return db

    def test_board_executors_only_for_who_may_post_there(self):
        # 4 — участник доски: в обход списка получателей его не пускают.
        cursor = _ExecutorCursor({10: {2, 3}}, access={(10, 40): "board"}, participants_by_project={10: {4}})
        db = self._db(cursor)
        self.assertEqual(db.get_task_extra_assignable_ids(40, "sv", project_id=10), {2, 3})
        # Доска без исполнителей ничего сверх получателей не даёт.
        cursor.executors_by_project[10] = set()
        self.assertEqual(db.get_task_extra_assignable_ids(40, "sv", project_id=10), set())
        cursor.executors_by_project[10] = {2, 3}
        # Не в составе доски — обхода нет; админ раздела ставит на любую доску.
        self.assertEqual(db.get_task_extra_assignable_ids(41, "sv", project_id=10), set())
        self.assertEqual(db.get_task_extra_assignable_ids(41, "admin", project_id=10), {2, 3})
        # Доска в архиве — новых через неё не ставят.
        cursor.archived.add(10)
        self.assertEqual(db.get_task_extra_assignable_ids(40, "sv", project_id=10), set())

    def test_board_without_people_adds_nothing_and_does_not_fail(self):
        """Доска без людей — «как раньше»: сверх получателей ничего, и выборка None не роняет ручку."""
        cursor = _ExecutorCursor({10: {2}, 30: set()}, task_projects_by_id={79: 30}, crews={79: [5]})
        db = self._db(cursor)
        self.assertEqual(db.get_task_extra_assignable_ids(41, "admin", project_id=30), set())
        # Правка задачи этой доски: нынешний состав остаётся, людей доски нет.
        self.assertEqual(db.get_task_extra_assignable_ids(41, "admin", task_id=79), {5})

    def test_edit_keeps_the_current_crew_and_takes_the_task_board(self):
        """Человек, которого сделали участником доски, правку задачи не запирает (находка разбора)."""
        cursor = _ExecutorCursor({10: {2}}, task_projects_by_id={77: 10, 78: None},
                                 access={(10, 40): "board"}, crews={77: [9, 2], 78: [5]},
                                 participants_by_project={10: {9}})
        db = self._db(cursor)
        self.assertEqual(db.get_task_extra_assignable_ids(40, "sv", task_id=77), {9, 2})
        self.assertEqual(db.get_task_extra_assignable_ids(40, "sv", task_id=78), {5})
        # Постановщика сняли с доски — остаётся только нынешний состав.
        self.assertEqual(db.get_task_extra_assignable_ids(41, "sv", task_id=77), {9, 2})


class ModuleTests(unittest.TestCase):
    def test_refusal_has_a_russian_text(self):
        text = task_projects.error_message("ASSIGNEE_NOT_PROJECT_EXECUTOR")
        self.assertIn("исполнителям", text)


def _outside_scope_function():
    """Настоящая сверка «кому вправе поручить» из build_tasks_blueprint."""
    module = ast.parse(ROUTES_SOURCE)
    builder = next(node for node in module.body
                   if isinstance(node, ast.FunctionDef) and node.name == "build_tasks_blueprint")
    node = copy.deepcopy(next(item for item in builder.body
                              if isinstance(item, ast.FunctionDef) and item.name == "_task_assignees_outside_scope"))
    return node


class _ScopeDb:
    def __init__(self, recipients, executors_by_project, task_projects_by_id=None, crews=None):
        self.recipients = recipients
        self.executors_by_project = executors_by_project
        self.task_projects_by_id = task_projects_by_id or {}
        self.crews = crews or {}
        self.executor_calls = []

    def get_task_recipients(self, requester_id, requester_role, scope_department_id=None):
        return [{"id": person_id} for person_id in self.recipients]

    @staticmethod
    def _normalize_task_project_id(value):
        raw = str(value or "").strip().lower()
        if raw in ("", "none", "null", "0"):
            return None
        if not raw.isdigit():
            raise ValueError("INVALID_PROJECT")
        return int(raw)

    def get_task_extra_assignable_ids(self, requester_id, requester_role, project_id=None, task_id=None):
        self.executor_calls.append((project_id, task_id))
        extra = set(self.crews.get(task_id, ()))
        if project_id is None and task_id is not None:
            project_id = self.task_projects_by_id.get(task_id)
        return extra | set(self.executors_by_project.get(project_id, ()))


class OutsideScopeRouteTests(unittest.TestCase):
    """Исполнитель доски разрешён и вне списка получателей: поручить ему вправе любой,
    кто ставит задачи на эту доску (иначе СВ другого отдела не поставил бы на доску HR
    ни одной задачи)."""

    def _outside(self, db, ids, **kwargs):
        namespace = {"db": db, "g": types.SimpleNamespace()}
        exec(compile(ast.Module(body=[_outside_scope_function()], type_ignores=[]), str(ROUTES_PATH), "exec"),
             namespace)
        return namespace["_task_assignees_outside_scope"](ids, 1, "sv", **kwargs)

    def test_board_executor_passes_outside_recipients(self):
        db = _ScopeDb(recipients={1, 2}, executors_by_project={10: {30, 31}})
        self.assertEqual(self._outside(db, [2, 30], project_id="10"), [])
        self.assertEqual(self._outside(db, [2, 30, 40], project_id="10"), [40])

    def test_without_a_board_nothing_widens(self):
        db = _ScopeDb(recipients={1, 2}, executors_by_project={10: {30}})
        self.assertEqual(self._outside(db, [30]), [30])
        self.assertEqual(db.executor_calls, [])
        # Неверный проект отвергнет само создание — здесь он просто ничего не расширяет.
        self.assertEqual(self._outside(db, [30], project_id="x"), [30])

    def test_edit_without_move_looks_at_the_task_board(self):
        db = _ScopeDb(recipients={1}, executors_by_project={10: {30}}, task_projects_by_id={77: 10})
        self.assertEqual(self._outside(db, [30], project_id=None, task_id=77), [])
        self.assertEqual(db.executor_calls, [(None, 77)])

    def test_edit_does_not_recheck_the_current_crew(self):
        """Исполнителя из другого отдела сняли с исполнителей доски — тема задачи правится."""
        db = _ScopeDb(recipients={1}, executors_by_project={10: set()}, task_projects_by_id={77: 10},
                      crews={77: [30]})
        self.assertEqual(self._outside(db, [30], project_id=None, task_id=77), [])
        self.assertEqual(self._outside(db, [30, 31], project_id=None, task_id=77), [31])

    def test_board_is_asked_only_when_someone_is_outside(self):
        db = _ScopeDb(recipients={1, 2}, executors_by_project={10: {30}})
        self.assertEqual(self._outside(db, [2], project_id="10"), [])
        self.assertEqual(db.executor_calls, [])


def _fanout_function():
    module = ast.parse(ROUTES_SOURCE)
    nodes = [node for node in module.body
             if (isinstance(node, ast.FunctionDef) and node.name == "_send_same_task_message")
             or (isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "TASK_TELEGRAM_FANOUT_THREADS" for t in node.targets))]
    namespace = {"concurrent": concurrent}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTES_PATH), "exec"), namespace)
    return namespace


class FanoutTests(unittest.TestCase):
    """«Новая задача» всем исполнителям доски — в несколько потоков, ответы по порядку."""

    def test_answers_keep_the_order_and_one_failure_stops_nobody(self):
        namespace = _fanout_function()
        lock = threading.Lock()
        active = {"now": 0, "max": 0}

        def send(chat_id, message, parse_mode=None, reply_markup=None):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            try:
                if chat_id == 13:
                    raise RuntimeError("сеть")
                return ("ok", chat_id, message, parse_mode, reply_markup)
            finally:
                with lock:
                    active["now"] -= 1

        chats = list(range(1, 21))
        answers = namespace["_send_same_task_message"](send, chats, "<b>Новая</b>", {"k": 1})
        self.assertEqual(len(answers), 20)
        self.assertIsInstance(answers[12], RuntimeError)
        self.assertEqual([item[1] for i, item in enumerate(answers) if i != 12], [c for c in chats if c != 13])
        self.assertTrue(all(item[3] == "HTML" and item[4] == {"k": 1} for i, item in enumerate(answers) if i != 12))
        self.assertLessEqual(active["max"], namespace["TASK_TELEGRAM_FANOUT_THREADS"])

    def test_single_chat_needs_no_threads(self):
        namespace = _fanout_function()
        calls = []
        answers = namespace["_send_same_task_message"](
            lambda chat_id, *a, **k: calls.append(threading.current_thread().name) or "ok", [5], "m")
        self.assertEqual(answers, ["ok"])
        self.assertEqual(calls, [threading.current_thread().name])
        self.assertEqual(namespace["_send_same_task_message"](lambda *a, **k: "x", [], "m"), [])


class WiringTests(unittest.TestCase):
    """Проводка, которую исполнением здесь не проверить: маркеры на своих местах."""

    def test_create_and_edit_run_the_rule(self):
        create = DATABASE_SOURCE[DATABASE_SOURCE.index("    def create_task(\n"):]
        create = create[:create.index("\n    def ", 10)]
        self.assertIn("limit=self.TASK_MAX_ASSIGNEES_PROJECT", create)
        rule = create.index("self._require_task_project_assignees_tx(cursor, project_id_norm, assignee_ids_norm)")
        # Отказ — до номера в проекте: строку проекта он не держит.
        self.assertLess(rule, create.index("self._take_task_project_number_tx(cursor, project_id_norm)"))
        edit = DATABASE_SOURCE[DATABASE_SOURCE.index("    def edit_task(\n"):]
        edit = edit[:edit.index("\n    def ", 10)]
        self.assertIn("limit=self.TASK_MAX_ASSIGNEES_PROJECT", edit)
        self.assertIn("recheck_all=project_changed", edit)

    def test_routes_pass_the_board_to_the_scope_check(self):
        self.assertIn("db._normalize_task_assignees(raw_value, limit=db.TASK_MAX_ASSIGNEES_PROJECT)", ROUTES_SOURCE)
        self.assertIn("assignee_ids, requester_id, requester_role, project_id=project_id_raw", ROUTES_SOURCE)
        self.assertIn("project_id=data.get('project_id') if has_project else None,", ROUTES_SOURCE)
        self.assertIn("_send_same_task_message(\n                        _send_telegram_text_message,", ROUTES_SOURCE)

    def test_board_list_carries_the_department_code_and_the_rule_is_explained(self):
        # Код отдела людей доски — маркетолога для «Продакшна» форма узнаёт и вне получателей.
        self.assertIn('"department_code": row[8],', DATABASE_SOURCE)
        # Роль в составе уходит в ответ, исполнители — первыми.
        self.assertIn('"is_executor": bool(row[9]),', DATABASE_SOURCE)
        self.assertIn("CASE WHEN pm.is_executor THEN 0 ELSE 1 END", DATABASE_SOURCE)
        # Под «i» у состава — по строке на каждую роль сегмента.
        for line in ("'Исполнитель — ему ставят задачи доски.", "'Участник — видит задачи доски"):
            self.assertIn(line, PROJECTS_SHEET)
        self.assertIn("{ value: ROLE_EXECUTOR, label: 'Исполнитель' }", PROJECTS_SHEET)
        self.assertIn("{ value: ROLE_PARTICIPANT, label: 'Участник' }", PROJECTS_SHEET)

    def test_projects_sheet_saves_the_role_and_new_people_are_participants(self):
        # Роль уходит в запрос сохранения у каждого человека (не только в черновике окна).
        self.assertIn(
            "      members: draft.members.map((person) => ({\n"
            "        user_id: person.id,\n"
            "        access_level: person.access_level,\n"
            "        is_executor: Boolean(person.is_executor),\n"
            "      })),",
            PROJECTS_SHEET,
        )
        new_member = PROJECTS_SHEET[PROJECTS_SHEET.index("const newMember = (person) => ({"):]
        new_member = new_member[:new_member.index("});")]
        self.assertIn("is_executor: false,", new_member)
        self.assertIn("addDepartmentStaff = () => patch({\n    members: [...draft.members, ...departmentStaff.map(newMember)],",
                      PROJECTS_SHEET)
        # Доска с людьми, но без исполнителей, не сохраняется — и окно само доводит до ошибки внизу.
        self.assertIn("const membersProblem = boardMembersIssue(draft.members, draft.isArchived);", PROJECTS_SHEET)
        self.assertIn("if (!name || codeProblem || membersProblem || sectionsIssue(draft.sections)) {", PROJECTS_SHEET)
        self.assertIn("document.querySelector('[data-project-issue=\"members\"]')", PROJECTS_SHEET)
        self.assertIn("?.scrollIntoView({ block: 'center', behavior: 'smooth' });", PROJECTS_SHEET)
        self.assertIn("data-project-issue={issues.members ? 'members' : undefined}", PROJECTS_SHEET)
        # Порядок строк и подпись доски — общими функциями (их поведение — task_board_assignees.test.mjs).
        self.assertIn("const draftMembersOf = (project) => orderBoardMembers(", PROJECTS_SHEET)
        self.assertIn("const { facts, missingExecutors } = projectListFacts(project);", PROJECTS_SHEET)

    def test_pinned_quick_form_offers_board_executors(self):
        """Закреплённая задача (находка разбора 26.11): быстрая форма — тоже только исполнители доски."""
        start = TASKS_VIEW.index("export const PinnedTaskWidget = React.memo(({")
        widget = TASKS_VIEW[start:]
        self.assertIn("axios.get(`${apiBaseUrl}/api/tasks/projects`, { headers: buildNoteHeaders() })", widget)
        self.assertIn("const bound = project && hasBoardPeople(project) ? project : null;", widget)
        # Связанная задача — из прежнего состава только исполнители доски.
        self.assertIn("if (bound && quickFormMode === 'create') {", widget)
        self.assertIn("assigneeIds: keepAssigneesForProject(prev.assigneeIds, bound)", widget)
        self.assertIn("const byId = new Map(boardAssignees(quickBoard).map((person) => [String(person.id), person]));", widget)
        self.assertIn("isLoading={isTaskRecipientsLoading || quickBoardLoading}", widget)
        self.assertIn("const quickNoExecutors = boardWithoutExecutors(quickBoard);", widget)

    def test_existing_people_become_executors_once(self):
        """Разовый перенос: с 24.11 исполнителями был весь состав — выкладка этого не меняет."""
        schema = (ROOT / "db" / "schema.py").read_text(encoding="utf-8")
        start = schema.index("('task_board_executors_2611',)")
        block = schema[start:start + 400]
        self.assertIn("if cursor.fetchone():", block)
        self.assertIn("UPDATE task_project_members SET is_executor = TRUE WHERE NOT is_executor", block)
        # Перенос — после колонки, иначе на свежей базе ему нечего обновлять.
        self.assertLess(schema.index("ADD COLUMN IF NOT EXISTS is_executor BOOLEAN NOT NULL DEFAULT FALSE"), start)
        self.assertLess(schema.index("CREATE TABLE IF NOT EXISTS app_one_time_jobs"), start)

    def test_composer_offers_everyone_or_specific_people(self):
        start = TASKS_VIEW.index("const TaskComposerForm = ({")
        block = TASKS_VIEW[start:TASKS_VIEW.index("const TaskRow = React.memo", start)]
        self.assertIn("const boardPeople = useMemo(() => boardAssignees(selectedProject), [selectedProject]);", block)
        # Привязка — по людям доски, а не по исполнителям: доска без исполнителей
        # не возвращается к «как раньше», а говорит, что поручать некому.
        self.assertIn("const hasBinding = hasBoardPeople(selectedProject);", block)
        self.assertIn("const noExecutors = boardWithoutExecutors(selectedProject);", block)
        self.assertIn("'У доски нет исполнителей'", block)
        # Пустое поле не открывается: выбирать в нём некого.
        self.assertIn("disabled={disabled || (noExecutors && !assigneeIds.length)}", block)
        self.assertIn("onClick={() => pickScope('all')}", block)
        self.assertIn("Конкретные люди", block)
        self.assertIn("<AllAssigneesSummary people={allPeople} />", block)
        # Без привязки строка прежняя: сегменты и «i» к ним — только у доски с исполнителями.
        self.assertIn("{offersAll && (", block)
        self.assertIn("<InfoHint side=\"left\" text={assigneeHint} />", block)

    def test_edit_opens_on_everyone_and_auto_board_filters_people(self):
        self.assertIn("assignAll: isAllBoardAssigneesSelection(nextForm.assigneeIds, taskProject,", TASKS_VIEW)
        self.assertIn("...(prev.projectId === composerDefaultProject ? {} : assigneesAfterProjectChange(prev, nextProject, {",
                      TASKS_VIEW)


if __name__ == "__main__":
    unittest.main()
