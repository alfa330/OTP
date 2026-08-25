"""Бэк-офис: отделы «Бухгалтерия» (accounting) и «HR» (hr).

Владелец оставил им ровно два раздела — «Учёт сотрудников» и «Вики». Разделы
выдаются РАЗНЫМИ механизмами, и оба должны быть на месте:

* «Учёт сотрудников» — строкой в DEPARTMENT_VIEW_ALLOWLIST (поведение карты
  проверяет настоящий Node в tests/back_office_department_views.test.mjs);
* «Вики» — тумблером departments.wiki_enabled вместе с пространством вики;
  allowlist о разделе не знает вовсе.

Здесь — обвязка вокруг карты: что раздел «Вики» действительно гейтится
тумблером, что оператору бэк-офиса его открывает подтверждение QR и что
подтвердить это подтверждение есть кому.
"""
import ast
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
APP_PATH = ROOT / "src" / "App.jsx"
NODE_TEST = ROOT / "tests" / "back_office_department_views.test.mjs"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _function_source(path, function_name):
    source = _read(path)
    module = source_cache.parse(source)
    function_node = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, function_node))


class BackOfficeViewAllowlistRuntimeTests(unittest.TestCase):
    """Карта разделов — литералы: опечатка в ключе роли не ломает синтаксис,
    она молча снимает ограничение. Поэтому карту гоняем настоящим Node."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_back_office_views_runtime(self):
        completed = subprocess.run(
            [shutil.which("node") or "node", "--test", str(NODE_TEST)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class BackOfficeAllowlistSourceTests(unittest.TestCase):
    """Записи отделов и наборы разделов — в исходнике, как у фронт-офисов."""

    def test_back_office_allowlist_entries(self):
        source = _read(DEPARTMENT_VIEWS_PATH)

        self.assertIn("const BACK_OFFICE_EMPLOYEE_VIEWS = ['profile'];", source)
        self.assertIn("const BACK_OFFICE_MANAGER_VIEWS = ['manage_operators'];", source)
        self.assertIn(
            "const BACK_OFFICE_HEAD_VIEWS = [...BACK_OFFICE_MANAGER_VIEWS, 'qr_access'];",
            source,
        )

        for code in ("accounting", "hr"):
            self.assertIn(f"    {code}: {{", source)
            entry = source.split(f"    {code}: {{", 1)[1].split("},", 1)[0]
            self.assertIn("operator: BACK_OFFICE_EMPLOYEE_VIEWS", entry)
            self.assertIn("trainee: BACK_OFFICE_EMPLOYEE_VIEWS", entry)
            self.assertIn("head: BACK_OFFICE_HEAD_VIEWS", entry)
            self.assertIn("sv: BACK_OFFICE_MANAGER_VIEWS", entry)

    def test_back_office_uses_simple_employee_accounting(self):
        # Ни супервайзеров, ни тренеров в этих отделах нет: выпадашка «Учёта
        # сотрудников» открывала бы два заведомо пустых списка.
        source = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS = "
            "new Set(['front_office', 'accounting', 'hr']);", source
        )

    def test_back_office_keeps_colleague_schedules_helpers_untouched(self):
        # «Графики работы» бэк-офису не выданы вовсе, поэтому в множествах про
        # смены и про поля карточки фронт-офиса его быть не должно: лишний код
        # отдела там — правило, которое однажды сработает не там, где задумано.
        source = _read(DEPARTMENT_VIEWS_PATH)
        for const_name in (
            "COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS",
            "EMPLOYEE_CITY_DEPARTMENTS",
            "FRONT_OFFICE_TRAINING_HIDDEN_DEPARTMENTS",
        ):
            line = next(
                line for line in source.splitlines()
                if line.startswith(f"const {const_name}")
            )
            self.assertNotIn("accounting", line, const_name)
            self.assertNotIn("'hr'", line, const_name)


class BackOfficeWikiSectionTests(unittest.TestCase):
    """«Вики» бэк-офису выдаёт тумблер отдела + пространство, а не allowlist."""

    def test_wiki_is_not_gated_by_the_department_allowlist(self):
        views = _read(DEPARTMENT_VIEWS_PATH)
        allowlist = views.split("export const DEPARTMENT_VIEW_ALLOWLIST", 1)[1].split("};", 1)[0]
        self.assertNotIn("'wiki'", allowlist)

        app = _read(APP_PATH)
        self.assertIn("const wikiEnabledFor = (user) => user?.wiki_enabled !== false;", app)
        self.assertIn("if (view === 'wiki' && wikiSectionEnabled) return;", app)
        self.assertIn("{wikiSectionEnabled && (", app)

    def test_profile_payload_requires_both_toggle_and_space(self):
        # Тумблер отвечает «раздел выдан?», пространство — «есть ли в нём для
        # отдела хоть что-нибудь». Новый отдел без пространства не должен
        # получить пункт меню, ведущий в пустой раздел.
        payload = _function_source(BOT_PATH, "_get_user_payload")
        self.assertIn("wiki_enabled = db.department_wiki_enabled(department_id)", payload)
        self.assertIn("wiki_enabled = db.department_has_wiki_space(", payload)
        self.assertIn('"wiki_enabled": wiki_enabled,', payload)


class BackOfficeQrAccessTests(unittest.TestCase):
    """У главы бэк-офиса есть «QR доступ» — иначе «Вики» сотрудникам не открыть."""

    def test_operator_needs_qr_confirmation_for_sensitive_sections(self):
        app = _read(APP_PATH)
        self.assertIn(
            "const sensitiveSectionQrRequiredFor = (userLike) => (\n"
            "    normalizeRole(userLike?.role) === 'operator' && !isDepartmentHead(userLike)\n"
            ");",
            app,
        )
        self.assertIn("{departmentAllowsView(user, 'qr_access') && (", app)

    def test_department_head_may_confirm_own_employees(self):
        # Подтверждает админ, супервайзер или глава отдела. Супервайзеров в
        # бэк-офисе нет — остаётся глава, и его право ограничено своим отделом.
        approval = _function_source(BOT_PATH, "_sensitive_access_approval_error")
        self.assertIn("if headed:", approval)
        self.assertIn("if operator_department_id is not None and int(operator_department_id) in headed:", approval)


class BackOfficeHeadSidebarTests(unittest.TestCase):
    """Глава бэк-офиса ходит по ветке менеджера: один пункт «Учёт сотрудников»."""

    def test_simple_employee_accounting_sidebar_item(self):
        app = _read(APP_PATH)
        self.assertIn("{isDepartmentHeadUser && departmentUsesSimpleEmployeeAccounting(user) && (", app)
        self.assertIn(
            "else if (departmentUsesSimpleEmployeeAccounting(user) && "
            "['sv_list', 'manage_trainers'].includes(view)) setView('manage_users');",
            app,
        )

    def test_supervisor_item_is_gated_by_the_allowlist(self):
        # У СВ бэк-офиса пункт появляется строкой 'manage_operators' в карте;
        # без гейта он приехал бы и в отделы, где раздел не выдан.
        app = _read(APP_PATH)
        self.assertIn(
            "{departmentAllowsView(user, 'manage_operators') && !isDepartmentHeadUser && (",
            app,
        )


if __name__ == "__main__":
    unittest.main()
