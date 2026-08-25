"""Бэк-офис: отделы «Бухгалтерия» (accounting) и «HR» (hr).

Владелец оставил им «Учёт сотрудников», «Задачи» и «Вики». Разделы выдаются
РАЗНЫМИ механизмами, и все должны быть на месте:

* «Учёт сотрудников» и «Задачи» — строками в DEPARTMENT_VIEW_ALLOWLIST
  (поведение карты проверяет настоящий Node в
  tests/back_office_department_views.test.mjs);
* «Вики» — тумблером departments.wiki_enabled вместе с пространством вики;
  allowlist о разделе не знает вовсе.

Здесь — обвязка вокруг карты: что раздел «Вики» действительно гейтится
тумблером, что оператору бэк-офиса его открывает подтверждение QR и что
подтвердить это подтверждение есть кому; что «Настройки SIP» бэк-офису не
показываются ни на фронте, ни на бэкенде; и что в карточке сотрудника нет
полей человека на линии.
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
MODAL_PATH = ROOT / "src" / "components" / "modals" / "UserEditModal.jsx"
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
        self.assertIn("const BACK_OFFICE_MANAGER_VIEWS = ['manage_operators', 'tasks'];", source)
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

    def test_back_office_stays_out_of_front_office_only_sets(self):
        # «Графики работы» бэк-офису не выданы вовсе, а «Город» нужен только
        # фронт-офисам (они сидят по городам). Лишний код отдела в этих
        # множествах — правило, которое однажды сработает не там, где задумано.
        source = _read(DEPARTMENT_VIEWS_PATH)
        for const_name in (
            "COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS",
            "EMPLOYEE_CITY_DEPARTMENTS",
        ):
            line = next(
                line for line in source.splitlines()
                if line.startswith(f"const {const_name}")
            )
            self.assertNotIn("accounting", line, const_name)
            self.assertNotIn("'hr'", line, const_name)


class BackOfficeEmployeeCardTests(unittest.TestCase):
    """Карточка сотрудника бэк-офиса: без группы, направления и SIP-номера."""

    def test_operator_line_fields_helper(self):
        source = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const OPERATOR_FIELDS_HIDDEN_DEPARTMENTS = new Set(['accounting', 'hr']);",
            source,
        )
        self.assertIn("export const departmentCodeHidesOperatorFields = (code) => {", source)
        self.assertIn(
            "export const departmentHidesOperatorFields = (user) ="
            "> departmentCodeHidesOperatorFields(departmentCodeOf(user));",
            source,
        )

    def test_front_office_training_is_hidden_for_back_office_too(self):
        source = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const FRONT_OFFICE_TRAINING_HIDDEN_DEPARTMENTS = "
            "new Set(['front_office', 'accounting', 'hr']);", source
        )

    def test_modal_hides_group_direction_and_sip(self):
        modal = _read(MODAL_PATH)
        self.assertIn(
            "const showOperatorLineFields = !departmentCodeHidesOperatorFields(effectiveDeptCode);",
            modal,
        )
        # Оба режима модалки: создание и редактирование. Поле, забытое в одной
        # из веток, всплыло бы только у того, кто открыл карточку уже
        # заведённого сотрудника.
        self.assertEqual(modal.count("{showOperatorLineFields && ("), 2)   # SIP-номер
        self.assertEqual(modal.count("&& showOperatorLineFields && ("), 4)  # группа и направление

    def test_group_and_direction_are_not_required_for_back_office(self):
        # Без этого глава бэк-офиса не смог бы завести человека вовсе:
        # валидация требовала выбрать группу и направление, которых нет.
        modal = _read(MODAL_PATH)
        self.assertIn(
            "if (isCreateMode && isOperatorUser && showOperatorLineFields && !editedUser.group_id) {",
            modal,
        )
        self.assertIn(
            "if (isOperatorUser && showOperatorLineFields && !editedUser.direction_id) {",
            modal,
        )


class BackOfficeSipSettingsTests(unittest.TestCase):
    """«Настройки SIP» — телефония. Бэк-офис звонков не принимает."""

    def test_frontend_gate_is_scoped_to_telephony_departments(self):
        app = _read(APP_PATH)
        self.assertIn(
            "const SIP_SETTINGS_DEPARTMENT_CODES = new Set(['szov', 'op', 'tez']);",
            app,
        )
        self.assertIn(
            "const isSipSettingsDepartmentHead = (userLike) => (\n"
            "    isDepartmentHead(userLike)\n"
            "    && aiQaHeadDepartmentCodesOf(userLike).some((code) => "
            "SIP_SETTINGS_DEPARTMENT_CODES.has(code))\n"
            ");",
            app,
        )
        # Прежний гейт пускал ЛЮБОГО главу отдела — из-за него раздел и вылез
        # у главы HR.
        self.assertNotIn(
            "const canAccessSipSettings = isAdminLikeRole || isDepartmentHeadUser",
            app,
        )
        self.assertIn(
            "const canAccessSipSettings = isAdminLikeRole\n"
            "                || isSipSettingsDepartmentHead(user)\n"
            "                || isOpSalesSupervisorForAiQa(user);",
            app,
        )

    def test_backend_mirror_replaces_base_admin_role_for_heads(self):
        source = _read(BOT_PATH)
        self.assertIn(
            "SIP_SETTINGS_DEPARTMENT_CODES = frozenset({'szov', 'op', 'tez'})",
            source,
        )
        guard = _function_source(BOT_PATH, "_can_manage_sip_config")
        # Роль главы отдела ЗАМЕНЯЕТ базовую: глава с role='admin' проходил
        # первой же строкой как глобальный админ.
        self.assertNotIn("if _is_admin_role(role):", guard)
        self.assertIn("if _is_global_admin_requester(role, requester_id):", guard)
        self.assertIn("if _is_sip_settings_department_head(requester_id):", guard)
        self.assertNotIn("if _headed_department_id(requester_id) is not None:", guard)

        head_check = _function_source(BOT_PATH, "_is_sip_settings_department_head")
        self.assertIn("SIP_SETTINGS_DEPARTMENT_CODES", head_check)


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


class SipSettingsGateRuntimeTests(unittest.TestCase):
    """Гейт настроек SIP гоняем настоящим кодом, а не сверкой строк.

    Дыра, из-за которой раздел вылез у главы HR, была именно в ПОРЯДКЕ условий:
    `_is_admin_role(role)` стоял первым и пропускал главу с базовой role='admin'
    как глобального админа. Такое ловится только исполнением.
    """

    DEPARTMENTS = {
        1: {'code': 'szov'}, 367: {'code': 'op'}, 560: {'code': 'tez'},
        909: {'code': 'front_office'}, 1499: {'code': 'hr'}, 1500: {'code': 'accounting'},
    }

    def _gate(self, headed, own_department):
        """Собирает _can_manage_sip_config на двойниках зависимостей."""
        outer = self

        class _Db:
            def get_department_by_id(self, department_id):
                return outer.DEPARTMENTS.get(department_id)

            def get_user_department_id(self, user_id):
                return own_department.get(user_id)

        namespace = {
            'db': _Db(),
            '_headed_department_ids': lambda uid: frozenset(headed.get(uid, ())),
            '_headed_department_id': lambda uid: (sorted(headed[uid])[0] if headed.get(uid) else None),
            '_is_admin_role': lambda role: role in ('admin', 'super_admin'),
            '_is_super_admin_role': lambda role: role == 'super_admin',
            '_is_supervisor_role': lambda role: role == 'sv',
            'AI_QA_OP_DEPARTMENT_ID': 367,
        }
        exec(_function_source(BOT_PATH, '_is_global_admin_requester'), namespace)
        exec("SIP_SETTINGS_DEPARTMENT_CODES = frozenset({'szov', 'op', 'tez'})", namespace)
        exec(_function_source(BOT_PATH, '_is_sip_settings_department_head'), namespace)
        exec(_function_source(BOT_PATH, '_can_manage_sip_config'), namespace)
        return namespace['_can_manage_sip_config']

    def test_gate_lets_in_telephony_departments_only(self):
        headed = {10: {1499}, 11: {1500}, 12: {909}, 13: {1}, 14: {367}, 15: {560}}
        own = {20: 367, 21: 1499, 22: 1499}
        can = self._gate(headed, own)

        self.assertTrue(can(1, 'super_admin'))
        self.assertTrue(can(2, 'admin'))          # админ вне отделов

        # Ровно тот случай, с которого началась правка.
        self.assertFalse(can(10, 'admin'), 'глава HR')
        self.assertFalse(can(11, 'admin'), 'глава Бухгалтерии')
        # Фронт-офисы телефонии тоже не имеют — раздел ушёл и у них.
        self.assertFalse(can(12, 'admin'), 'глава фронт-офисов')

        self.assertTrue(can(13, 'admin'), 'глава СЗоВ')
        self.assertTrue(can(14, 'admin'), 'глава ОП')
        self.assertTrue(can(15, 'admin'), 'глава ТЭЗ')

        self.assertTrue(can(20, 'sv'), 'СВ отдела продаж')
        self.assertFalse(can(21, 'sv'), 'СВ бэк-офиса')
        self.assertFalse(can(22, 'operator'), 'рядовой сотрудник')

    def test_head_of_two_departments_passes_by_the_telephony_one(self):
        # Глава может возглавлять несколько отделов; хватает одного с телефонией.
        can = self._gate({30: {1499, 367}, 31: {1499, 1500}}, {})
        self.assertTrue(can(30, 'admin'))
        self.assertFalse(can(31, 'admin'))


if __name__ == "__main__":
    unittest.main()
