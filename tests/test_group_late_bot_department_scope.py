# -*- coding: utf-8 -*-
"""Раздел «Бот опозданий» в границах одного отдела Workpace.

Раздел общекорпоративный: отделы в нём приходят из Workpace и с нашими
`departments` не связаны, поэтому целиком он открыт только глобальным админам.
Главе отдела из GROUP_LATE_BOT_DEPARTMENT_SCOPES он выдаётся суженным: и данные,
и действия ограничены ОДНИМ отделом Workpace («Фронт офисы» → «Регионы»).

Граница держится на бэкенде: guard отдаёт название отдела, а каждый эндпоинт
обязан протащить его в db-вызов. Тесты проверяют и сам механизм (SQL-предикаты,
состояние чата), и то, что ни один эндпоинт не забыл про scope.
"""

import ast
import copy
import re
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"
VIEW_PATH = ROOT / "src" / "components" / "group_late" / "GroupLateBotView.jsx"

DB_SOURCE = DB_PATH.read_text(encoding="utf-8-sig")
DB_TREE = ast.parse(DB_SOURCE)
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
BOT_TREE = ast.parse(BOT_SOURCE)
APP_SRC = APP_PATH.read_text(encoding="utf-8-sig")
VIEW_SRC = VIEW_PATH.read_text(encoding="utf-8-sig")

SCOPED_DEPARTMENT = "Регионы"


def _db_class(method_names):
    """Собирает заглушку Database с нужными методами, не импортируя модуль.

    `import database` на Windows падает (time.tzset) и поднимает пул к боевой базе,
    поэтому методы вытаскиваем через ast — как в остальных тестах по базе.
    """
    namespace = {"re": re, "datetime": datetime}
    cls_node = next(
        n for n in DB_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Database"
    )
    attrs = {}
    for name in method_names:
        fn = next(
            n for n in cls_node.body
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        module = ast.Module(body=[copy.deepcopy(fn)], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(DB_PATH), "exec"), namespace)
        attrs[name] = namespace[name]
    return type("StubDatabase", (), attrs)


SCOPE_HELPERS = (
    "_glb_department",
    "_glb_chat_scope_sql",
    "_glb_mute_scope_sql",
    "_glb_chat_id",
    "glb_chat_department_state",
)


def _bot_function_source(name):
    node = next(
        n for n in ast.walk(BOT_TREE)
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return ast.get_source_segment(BOT_SOURCE, node)


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


class ScopeSqlTests(unittest.TestCase):
    """Предикаты границы: без отдела — TRUE (глобальный админ видит всё)."""

    def setUp(self):
        self.db = _db_class(SCOPE_HELPERS)()

    def test_no_department_means_no_restriction(self):
        self.assertEqual(self.db._glb_chat_scope_sql("c.chat_id", None), "TRUE")
        self.assertEqual(self.db._glb_mute_scope_sql("m", None), "TRUE")
        self.assertIsNone(self.db._glb_department("   "))

    def test_chat_belongs_to_department_only_when_filter_is_exactly_it(self):
        sql = self.db._glb_chat_scope_sql("c.chat_id", SCOPED_DEPARTMENT)
        # Чат «наш», если у него есть фильтр на наш отдел И нет фильтра ни на какой
        # другой: чат без фильтра получает всю компанию, а с несколькими — ещё и чужое.
        self.assertIn("EXISTS (SELECT 1 FROM glb_chat_departments cd_in", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM glb_chat_departments cd_out", sql)
        self.assertIn("lower(cd_in.department_name) = lower(%(glb_dept)s)", sql)
        self.assertIn("lower(cd_out.department_name) <> lower(%(glb_dept)s)", sql)

    def test_mute_scope_covers_own_chats_and_own_department(self):
        sql = self.db._glb_mute_scope_sql("m", SCOPED_DEPARTMENT)
        # Либо правило привязано к нашему чату, либо это глобальное правило на сам
        # наш отдел. Глобальные «выключить всё» и «сотрудник везде» сюда не попадают.
        self.assertIn("m.chat_id IS NOT NULL", sql)
        self.assertIn("m.chat_id IS NULL AND m.mute_kind = 'dept'", sql)
        self.assertIn("lower(m.mute_value) = lower(%(glb_dept)s)", sql)

    def test_department_name_is_trimmed_and_capped(self):
        self.assertEqual(self.db._glb_department("  Регионы  "), "Регионы")
        self.assertEqual(len(self.db._glb_department("я" * 500)), 200)


class ChatDepartmentStateTests(unittest.TestCase):
    """'absent' | 'own' | 'foreign' — до записи нужно знать, не чужой ли это чат."""

    def _state(self, row):
        db = _db_class(SCOPE_HELPERS)()
        cursor = FakeCursor(row)

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        state = db.glb_chat_department_state("-100500", SCOPED_DEPARTMENT)
        return state, cursor

    def test_absent_chat(self):
        state, _ = self._state(None)
        self.assertEqual(state, "absent")

    def test_own_chat(self):
        state, cursor = self._state((True,))
        self.assertEqual(state, "own")
        self.assertEqual(cursor.calls[0][1]["glb_dept"], SCOPED_DEPARTMENT)

    def test_foreign_chat(self):
        state, _ = self._state((False,))
        self.assertEqual(state, "foreign")

    def test_invalid_chat_id_is_rejected(self):
        db = _db_class(SCOPE_HELPERS)()
        with self.assertRaises(ValueError):
            db.glb_chat_department_state("не число", SCOPED_DEPARTMENT)


class DbReadScopeTests(unittest.TestCase):
    """Читающие методы принимают department и подставляют его в запрос."""

    def test_read_methods_accept_department(self):
        for name in (
            "get_group_late_overview",
            "get_group_late_chats",
            "get_group_late_departments",
            "get_group_late_employee_stats",
            "get_group_late_mutes",
            "get_group_late_reports",
            "get_group_late_report_file",
        ):
            fn = next(
                n for n in ast.walk(DB_TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name
            )
            names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
            self.assertIn("department", names, f"{name} без границы отдела")

    def test_write_methods_accept_department(self):
        # Правку и удаление режем тем же предикатом: чужой чат/правило просто
        # «не находится», и вызов отвечает «не найдено».
        for name in (
            "update_group_late_chat",
            "delete_group_late_chat",
            "set_group_late_chat_departments",
            "delete_group_late_mute",
        ):
            fn = next(
                n for n in ast.walk(DB_TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name
            )
            names = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
            self.assertIn("department", names, f"{name} без границы отдела")

    def test_reports_filter_by_department_of_the_report(self):
        source = ast.get_source_segment(
            DB_SOURCE,
            next(n for n in ast.walk(DB_TREE)
                 if isinstance(n, ast.FunctionDef) and n.name == "get_group_late_reports"),
        )
        self.assertIn('lower(r.department_filter) = lower(%s)', source)


class BackendGuardTests(unittest.TestCase):
    """Guard отдаёт границу, а не только «пустить/не пустить»."""

    def test_scope_map_names_front_office(self):
        self.assertIn(
            "GROUP_LATE_BOT_DEPARTMENT_SCOPES = {'front_office': 'Регионы'}", BOT_SOURCE
        )

    def test_guard_returns_scope_triple(self):
        source = _bot_function_source("_group_late_bot_guard")
        self.assertIn("return requester_id, None, None", source)      # глобальный админ
        self.assertIn("return requester_id, scope, None", source)     # глава отдела
        self.assertIn('return requester_id, None, (jsonify({"error": "forbidden"}), 403)', source)

    def test_scope_resolved_from_headed_department(self):
        source = _bot_function_source("_group_late_bot_department_scope")
        self.assertIn("_headed_department_id(requester_id)", source)
        self.assertIn("_group_late_bot_scope_by_department_id()", source)
        # Глава отдела — админ-подобная роль; СВ и оператору раздел не положен.
        self.assertIn("if not _is_admin_role(role)", source)

    def test_every_guard_call_site_unpacks_scope(self):
        call_sites = re.findall(r"^\s*(.+?)\s*=\s*_group_late_bot_guard\(\)$",
                                BOT_SOURCE, flags=re.M)
        self.assertTrue(call_sites)
        for target in call_sites:
            self.assertEqual(len(target.split(",")), 3, f"guard распакован как «{target}»")


class EndpointScopeTests(unittest.TestCase):
    """Ни один эндпоинт не забыл протащить границу в db-вызов."""

    SCOPED_ENDPOINTS = {
        "api_group_late_bot_overview": ["department=scope"],
        "api_group_late_bot_chats": ["department=scope", "departments = [scope]"],
        "api_group_late_bot_chat_item": ["department=scope", "[scope] if scope else"],
        "api_group_late_bot_departments": ["department=scope"],
        "api_group_late_bot_mutes": ["department=scope"],
        "api_group_late_bot_mute_item": ["department=scope"],
        "api_group_late_bot_events": ["department=scope or request.args.get('department')"],
        "api_group_late_bot_employees": ["department=scope or request.args.get('department')"],
        "api_group_late_bot_reports": ["department=scope", "scope or str(payload.get('department')"],
        "api_group_late_bot_report_file": ["department=scope"],
    }

    # Общие для компании и ничего об отделах не раскрывают.
    GLOBAL_READ_ENDPOINTS = (
        "api_group_late_bot_available_chats",
        "api_group_late_bot_departments_sync",
        "api_group_late_bot_poll_runs",
    )

    def test_scoped_endpoints_pass_department(self):
        for name, needles in self.SCOPED_ENDPOINTS.items():
            source = _bot_function_source(name)
            for needle in needles:
                self.assertIn(needle, source, f"{name}: нет «{needle}»")

    def test_global_read_endpoints_ignore_scope_explicitly(self):
        for name in self.GLOBAL_READ_ENDPOINTS:
            source = _bot_function_source(name)
            self.assertIn("_, _, err = _group_late_bot_guard()", source, name)

    def test_writing_to_a_chat_requires_owning_it(self):
        # Тест-сообщение и отправка отчёта пишут в чат от имени бота.
        for name in ("api_group_late_bot_chat_test", "api_group_late_bot_reports"):
            source = _bot_function_source(name)
            self.assertIn("glb_chat_department_state", source, name)

    def test_manual_poll_is_admin_only(self):
        source = _bot_function_source("api_group_late_bot_poll")
        # Опрос прогоняет всю компанию и рассылает найденное по всем чатам.
        self.assertIn("if scope:", source)
        self.assertIn("_group_late_bot_scope_forbidden", source)

    def test_global_mute_rules_stay_admin_only(self):
        source = _bot_function_source("api_group_late_bot_mutes")
        # Без чата разрешено только правило на свой же отдел, и глушится всегда свой.
        self.assertIn("if mute_kind == 'dept':", source)
        self.assertIn("mute_value = scope", source)
        self.assertIn("elif mute_kind != 'dept':", source)
        self.assertIn("_group_late_bot_scope_forbidden('Правило тишины во всех чатах')", source)


class FrontendAccessTests(unittest.TestCase):
    """Пункт меню и раздел видны главе отдела, а не только глобальным админам."""

    def test_head_department_codes(self):
        self.assertIn(
            "const GROUP_LATE_BOT_HEAD_DEPARTMENT_CODES = new Set(['front_office']);", APP_SRC
        )
        self.assertIn("const isGroupLateBotDepartmentHead = (userLike) => (", APP_SRC)
        self.assertIn("GROUP_LATE_BOT_HEAD_DEPARTMENT_CODES.has(code)", APP_SRC)

    def test_access_predicate_lets_the_head_in(self):
        block = APP_SRC.split("const canAccessGroupLateBotForUser = (userLike) => {", 1)[1]
        block = block.split("};", 1)[0]
        self.assertIn("if (isGroupLateBotDepartmentHead(userLike)) return true;", block)
        # Глава ЧУЖОГО отдела с базовой admin-ролью по-прежнему не проходит.
        self.assertIn("return role === 'admin' && !isDepartmentHead(userLike);", block)

    def test_menu_item_reaches_the_sidebar_branch_of_the_head(self):
        # Глава отдела — «суженный» админ: isAdminLikeRole у него false (см.
        # isScopedDepartmentHead), и сайдбар ему рисует ветка isDepartmentManager.
        # Пункт, объявленный только в админской ветке, до главы не доезжает: доступ
        # есть, раздел открывается прямым URL, а в меню его нет — так и было.
        head_branch = APP_SRC.split("{isDepartmentManager && !isAdminLikeRole && (", 1)[1]
        head_branch = head_branch.split("{(currentUserRole === 'operator'", 1)[0]
        self.assertIn("{canAccessGroupLateBotSection && (", head_branch)
        self.assertIn("handleSidebarViewNavigation(e, 'group_late_bot')", head_branch)
        # И в админской ветке пункт остался: у пункта ровно две точки рендера,
        # как у «Табло СЗоВ».
        self.assertEqual(APP_SRC.count("handleSidebarViewNavigation(e, 'group_late_bot')"), 2)

    def test_allowlist_guard_does_not_redirect_the_head_away(self):
        # У front_office список разделов ограничен; раздел гейтится своим предикатом,
        # как «Табло СЗоВ» и «Чаты ChatApp», а не вписан в allowlist отдела.
        self.assertIn(
            "if (view === 'group_late_bot' && canAccessGroupLateBotSection) return;", APP_SRC
        )


class ScopedViewTests(unittest.TestCase):
    """В границах отдела раздел не показывает того, чем нельзя пользоваться."""

    def test_scope_comes_from_overview(self):
        self.assertIn("const [departmentScope, setDepartmentScope] = useState(null);", VIEW_SRC)
        self.assertIn("setDepartmentScope(r.data?.department_scope || null);", VIEW_SRC)
        self.assertIn("const scoped = Boolean(departmentScope);", VIEW_SRC)
        self.assertIn("overview['department_scope'] = scope", BOT_SOURCE)

    def test_manual_poll_button_hidden(self):
        self.assertIn("{!scoped && (\n                            <button onClick={pollNow}", VIEW_SRC)

    def test_department_pickers_replaced_by_the_scope(self):
        # Фильтр отбивок по отделу, выбор отделов чата и отдел отчёта: в границах
        # отдела выбирать нечего — бэкенд всё равно подставит свой отдел.
        self.assertIn("{!scoped && (\n                        <FilterField label=\"Отдел\"", VIEW_SRC)
        self.assertIn("чат получает нарушения только этого отдела", VIEW_SRC)
        self.assertIn("отчёт собирается только по своему отделу", VIEW_SRC)
        self.assertIn("чужой отдел заглушить нельзя", VIEW_SRC)

    def test_global_mute_option_only_for_own_department(self):
        self.assertIn("...((!scoped || muteModal.kind === 'dept')", VIEW_SRC)


if __name__ == "__main__":
    unittest.main()
