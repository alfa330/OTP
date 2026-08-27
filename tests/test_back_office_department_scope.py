"""Бэк-офис: отделы «Бухгалтерия» (accounting) и «HR» (hr).

Владелец оставил им «Учёт сотрудников», «Задачи» и «Вики». Разделы выдаются
РАЗНЫМИ механизмами, и все должны быть на месте:

* «Учёт сотрудников» и «Задачи» — строками в DEPARTMENT_VIEW_ALLOWLIST
  (поведение карты проверяет настоящий Node в
  tests/back_office_department_views.test.mjs). «Задачи» есть у ВСЕХ ролей
  отдела, но у рядового сотрудника охват личный — задачи, где он постановщик,
  поручитель или исполнитель, без приёмки за других. Карта разделов и гейт
  бэкенда обязаны совпадать; зеркало держит TasksSectionForBackOfficeTests;
* «Вики» — тумблером departments.wiki_enabled вместе с пространством вики;
  allowlist о разделе не знает вовсе.

Здесь — обвязка вокруг карты: что раздел «Вики» действительно гейтится
тумблером, что оператору бэк-офиса его открывает подтверждение QR и что
подтвердить это подтверждение есть кому; что «Настройки SIP» бэк-офису не
показываются ни на фронте, ни на бэкенде; и что в карточке сотрудника нет
полей человека на линии.
"""
import ast
import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
DEPARTMENT_VIEWS_PATH = ROOT / "src" / "utils" / "departmentViews.js"
ROLES_PATH = ROOT / "src" / "utils" / "roles.js"
WIKI_ACCESS_PATH = ROOT / "wiki" / "access.py"
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

        # 'profile' первым — это раздел по умолчанию (firstAllowedView берёт
        # allow[0]); переставив элементы, мы молча сменили бы стартовый экран.
        self.assertIn("const BACK_OFFICE_EMPLOYEE_VIEWS = ['profile', 'tasks'];", source)
        self.assertIn("const BACK_OFFICE_MANAGER_VIEWS = ['manage_operators', 'tasks'];", source)
        self.assertIn(
            "const BACK_OFFICE_HEAD_VIEWS = [...BACK_OFFICE_MANAGER_VIEWS, 'qr_access'];",
            source,
        )

        for code, own_role in (("accounting", "accounting_manager"), ("hr", "hr_manager")):
            self.assertIn(f"    {code}: {{", source)
            entry = source.split(f"    {code}: {{", 1)[1].split("},", 1)[0]
            self.assertIn("operator: BACK_OFFICE_EMPLOYEE_VIEWS", entry)
            self.assertIn("trainee: BACK_OFFICE_EMPLOYEE_VIEWS", entry)
            # Собственная роль отдела — тем же набором, что и остатки на
            # operator/trainee: набор один, разъехавшись, они дали бы одному
            # человеку раздел, а его соседу с прежней ролью — нет.
            self.assertIn(f"{own_role}: BACK_OFFICE_EMPLOYEE_VIEWS", entry)
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
        # Роли бэк-офиса здесь наравне с оператором: до появления собственной
        # должности эти люди БЫЛИ операторами, и подтверждение им требовалось.
        self.assertIn(
            "    (normalizeRole(userLike?.role) === 'operator' || isBackOfficeEmployeeRole(userLike?.role))\n"
            "    && !isDepartmentHead(userLike)\n"
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


class EmployeeJobTitleFieldTests(unittest.TestCase):
    """«Должность» для бэк-офиса — устроена как «Город» у фронт-офисов:
    обычная колонка users + дубль в user_hr_profiles, показ по коду отдела
    СОТРУДНИКА.

    Колонка называется job_title, а не position: position — ключевое слово
    Postgres, а SQL здесь местами собирается строкой (в update_user —
    f"SELECT role, {field} FROM users").
    """

    def test_schema_adds_the_column_to_both_tables(self):
        source = _read(DATABASE_PATH)
        self.assertIn("job_title VARCHAR(255),", source)          # CREATE TABLE users
        self.assertIn("job_title VARCHAR(255)\n", source)         # CREATE user_hr_profiles
        self.assertEqual(
            2, source.count("ADD COLUMN IF NOT EXISTS job_title VARCHAR(255);"),
            "ALTER нужен и для users, и для user_hr_profiles",
        )
        # position закрыт намеренно — проверяем, что его не завезли обратно.
        self.assertNotIn("ADD COLUMN IF NOT EXISTS position ", source)

    def test_field_is_writable_through_both_paths(self):
        source = _read(DATABASE_PATH)
        # Точечное обновление поля кладёт его в кадровую карточку, а не только
        # в users: иначе users и user_hr_profiles разъедутся.
        self.assertIn("'card_number', 'city', 'job_title'", source)
        # Массовое создание.
        self.assertIn("        job_title=None,", source)

        bot = _read(BOT_PATH)
        self.assertIn(
            "        job_title = str(data.get('job_title') or '').strip() or None",
            bot,
        )
        self.assertIn("            job_title=job_title,", bot)

    def test_lists_append_the_column_at_the_end(self):
        # Оба списка читаются по ИНДЕКСАМ, поэтому колонку дописывают в конец;
        # вставка в середину молча сдвинула бы все поля ниже.
        bot = _read(BOT_PATH)
        self.assertIn('"city": row[51] or "",\n                        "job_title": row[52] or ""', bot)
        self.assertIn('"city": sv[40] or "",\n                        "job_title": sv[41] or ""', bot)

    def test_frontend_helper_and_card(self):
        views = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const EMPLOYEE_JOB_TITLE_DEPARTMENTS = new Set(['accounting', 'hr']);",
            views,
        )
        self.assertIn("export const departmentCodeUsesEmployeeJobTitle = (code) => {", views)

        modal = _read(MODAL_PATH)
        self.assertIn(
            "const showEmployeeJobTitle = departmentCodeUsesEmployeeJobTitle(effectiveDeptCode);",
            modal,
        )
        # Поле в обоих режимах модалки.
        self.assertEqual(2, modal.count(">Должность</label>"))
        self.assertEqual(2, modal.count("job_title: e.target.value"))

    def test_frontend_sends_and_diffs_the_field(self):
        app = _read(APP_PATH)
        self.assertIn("job_title: normalizeTextForApi(editedUser.job_title),", app)
        self.assertIn("field: 'job_title',", app)
        self.assertIn("label: 'Должность',", app)

    def test_name_field_is_labelled_fio(self):
        # Поле одно (editedUser.name) и хранит ФИО целиком — подпись обязана
        # совпадать в обоих режимах, иначе карточка только что заведённого
        # сотрудника называет его иначе, чем форма создания.
        modal = _read(MODAL_PATH)
        self.assertNotIn(">Имя</label>", modal)
        self.assertIn('setModalError("ФИО обязательно.");', modal)


class CreateUserSqlBalanceTests(unittest.TestCase):
    """В create_user запросы собраны руками: колонки, плейсхолдеры и кортеж
    параметров живут в трёх разных местах. Забытый параметр — не синтаксис, а
    сдвиг значений по колонкам, и находят его уже в данных."""

    def test_placeholders_match_parameters(self):
        source = _read(DATABASE_PATH)
        module = source_cache.parse(source)
        create_user = next(
            node for node in ast.walk(module)
            if isinstance(node, ast.FunctionDef) and node.name == "create_user"
        )

        checked = 0
        for call in ast.walk(create_user):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "execute"
                    and len(call.args) == 2):
                continue
            sql_node, params_node = call.args
            if not (isinstance(sql_node, ast.Constant) and isinstance(sql_node.value, str)):
                continue
            if not isinstance(params_node, ast.Tuple):
                continue
            sql = sql_node.value
            if "INSERT INTO" not in sql and "UPDATE " not in sql:
                continue
            checked += 1
            self.assertEqual(
                sql.count("%s"), len(params_node.elts),
                f"строка {call.lineno}: плейсхолдеров и параметров разное число",
            )

        # Страховка от «тест ничего не проверил»: запросов в create_user много.
        self.assertGreaterEqual(checked, 6)

    def test_users_insert_lists_job_title_next_to_city(self):
        # Порядок — единственное, чего счётчик выше не ловит.
        source = _read(DATABASE_PATH)
        self.assertIn("card_number, city, job_title, internship_in_company", source)
        self.assertIn(
            "                    card_number,\n"
            "                    city,\n"
            "                    job_title,\n",
            source,
        )
        self.assertEqual(
            2, source.count("card_number, city, job_title, internship_in_company_value"),
            "обе UPDATE-ветки create_user (по имени и по telegram_id)",
        )


class BackOfficeUserCreationBackendTests(unittest.TestCase):
    """Сервер тоже не должен требовать направление у бэк-офиса.

    Скрыть поле на фронте мало: ручка создания сотрудника проверяет
    direction_id сама и отвечала «Missing required field: direction_id».
    """

    def test_department_is_resolved_before_the_direction_check(self):
        # Суть бага была в ПОРЯДКЕ: отдел разбирался ниже проверки направления,
        # поэтому проверка не могла знать, что направлений у отдела нет.
        source = _read(BOT_PATH)
        endpoint = _function_source(BOT_PATH, "add_user")

        resolved_at = endpoint.index("line_fields_hidden = _department_hides_operator_line_fields(department_id)")
        checked_at = endpoint.index('"error": "Missing required field: direction_id"')
        self.assertLess(
            resolved_at, checked_at,
            "отдел обязан разбираться ДО проверки направления",
        )
        # Отдел разбирается один раз: второй копии этого блока быть не должно.
        self.assertEqual(1, endpoint.count("department_id = requester_dept_id"))
        self.assertEqual(1, source.count("line_fields_hidden = _department_hides_operator_line_fields"))

    def test_direction_is_optional_only_for_back_office(self):
        endpoint = _function_source(BOT_PATH, "add_user")
        self.assertIn(
            "if role == 'operator' and not line_fields_hidden and not data.get('direction_id'):",
            endpoint,
        )
        self.assertIn("                if line_fields_hidden:\n                    direction_id = None", endpoint)
        # Фолбэк «СВ наследует своё направление» бэк-офису тоже не нужен.
        self.assertIn(
            "if role == 'operator' and not direction_id and not line_fields_hidden:",
            endpoint,
        )

    def test_helper_matches_the_frontend_set(self):
        source = _read(BOT_PATH)
        self.assertIn(
            "OPERATOR_FIELDS_HIDDEN_DEPARTMENT_CODES = frozenset({'accounting', 'hr'})",
            source,
        )
        views = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const OPERATOR_FIELDS_HIDDEN_DEPARTMENTS = new Set(['accounting', 'hr']);",
            views,
        )

    def test_helper_runtime(self):
        departments = {
            1: {'code': 'szov'}, 367: {'code': 'op'}, 560: {'code': 'tez'},
            909: {'code': 'front_office'}, 1499: {'code': 'hr'}, 1500: {'code': 'accounting'},
        }

        class _Db:
            def get_department_by_id(self, department_id):
                return departments.get(department_id)

        namespace = {'db': _Db()}
        exec("OPERATOR_FIELDS_HIDDEN_DEPARTMENT_CODES = frozenset({'accounting', 'hr'})", namespace)
        exec(_function_source(BOT_PATH, "_department_hides_operator_line_fields"), namespace)
        hides = namespace["_department_hides_operator_line_fields"]

        self.assertTrue(hides(1499), 'HR')
        self.assertTrue(hides(1500), 'Бухгалтерия')
        self.assertTrue(hides('1500'), 'id строкой из JSON')
        for department_id in (1, 367, 560, 909):
            self.assertFalse(hides(department_id), departments[department_id]['code'])
        # Отдел неизвестен — проверку не снимаем: это операторы на линии.
        self.assertFalse(hides(None))
        self.assertFalse(hides(999999))
        self.assertFalse(hides('не число'))


class BackOfficeEmployeeRoleTests(unittest.TestCase):
    """Рядовой сотрудник бэк-офиса заводится не оператором.

    'operator' в этой системе означает человека НА ЛИНИИ — с направлением,
    группой, часами и оценками. Бухгалтеру и кадровику эта роль давала бы
    разделы и поля, которых у них нет.
    """

    ROLES = ('hr_manager', 'accounting_manager')

    def test_role_is_known_everywhere_at_operator_level(self):
        # Шкала ролей продублирована в четырёх местах. Пропуск в любом из них
        # НЕ падает — он молча роняет уровень до нуля, а нулевой уровень в вике
        # не проходит ни одного min_role_level: раздел просто окажется пустым.
        for path in (BOT_PATH, DATABASE_PATH, ROLES_PATH, WIKI_ACCESS_PATH):
            source = _read(path)
            for role in self.ROLES:
                self.assertRegex(
                    source, rf"'?{role}'?:\s*10\b",
                    f"{path.name}: {role} должен быть на уровне оператора",
                )

    def test_db_check_and_column_width(self):
        source = _read(DATABASE_PATH)
        # CHECK пересобирается целиком — ALTER ... ADD CONSTRAINT не «дополняет».
        self.assertIn("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;", source)
        self.assertIn("'operator', 'trainee', 'hr_manager', 'accounting_manager'", source)
        # 'accounting_manager' — 18 символов; в прежний VARCHAR(20) влезало впритык.
        self.assertEqual(18, len('accounting_manager'))
        self.assertIn("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(32);", source)
        self.assertIn("role VARCHAR(32) NOT NULL CHECK(role IN (", source)

    def test_backend_lets_the_head_create_the_role(self):
        endpoint = _function_source(BOT_PATH, "add_user")
        self.assertIn("'hr_manager', 'accounting_manager'):", endpoint)
        # Прежняя формулировка пускала главу только к операторам и стажёрам —
        # то есть ни к кому из тех, кто у него есть.
        self.assertNotIn(
            "and role not in ('operator', 'trainee'):\n"
            '            return jsonify({"error": "Scoped managers can create only operators or trainees"}), 403',
            endpoint,
        )
        self.assertIn("role not in BACK_OFFICE_EMPLOYEE_ROLES", endpoint)

    def test_role_is_bound_to_its_department(self):
        # Роль вне своего отдела — дыра в правах, а не опечатка: в чужом отделе
        # её нет в DEPARTMENT_VIEW_ALLOWLIST, а роль вне конфига ограничений не
        # получает вовсе.
        endpoint = _function_source(BOT_PATH, "add_user")
        guard = "if role in BACK_OFFICE_EMPLOYEE_ROLES and _back_office_employee_role(department_id) != role:"
        self.assertIn(guard, endpoint)
        # Отдел обязан быть разобран ДО проверки.
        self.assertLess(endpoint.index("department_id = requester_dept_id"), endpoint.index(guard))

    def test_employee_lists_include_the_new_roles(self):
        # Без этого глава открывает «Учёт сотрудников» и видит пустой список:
        # люди в базе есть, а оба фильтра — серверный и клиентский — их режут.
        bot = _read(BOT_PATH)
        self.assertIn(
            "visible_roles = ['operator', 'trainee', 'trainer', *sorted(BACK_OFFICE_EMPLOYEE_ROLES)]",
            bot,
        )
        app = _read(APP_PATH)
        self.assertIn(
            "const RANK_AND_FILE_ROLES = Object.freeze(['operator', 'trainee', ...BACK_OFFICE_EMPLOYEE_ROLES]);",
            app,
        )
        self.assertIn("isRankAndFileRole(employee?.role)", app)
        # Ветки меню и рендера рядового сотрудника — обе.
        self.assertEqual(2, app.count("{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && ("))
        self.assertNotIn("(currentUserRole === 'operator' || currentUserRole === 'trainee') && !isScopedDepartmentHead", app)

    def test_qr_gate_survives_the_rename(self):
        # До появления своей роли эти люди были операторами и подтверждение QR
        # для «Вики» им требовалось. Переименование должности не повод снять его.
        wiki = _read(WIKI_ACCESS_PATH)
        self.assertIn(
            "QR_GATED_ROLES = frozenset({'operator', 'hr_manager', 'accounting_manager'})",
            wiki,
        )
        self.assertIn("return normalize_role(otp_role) in QR_GATED_ROLES", wiki)
        app = _read(APP_PATH)
        self.assertIn("|| isBackOfficeEmployeeRole(userLike?.role))", app)

    def test_helper_runtime(self):
        departments = {
            1: {'code': 'szov'}, 909: {'code': 'front_office'},
            1499: {'code': 'hr'}, 1500: {'code': 'accounting'},
        }

        class _Db:
            def get_department_by_id(self, department_id):
                return departments.get(department_id)

        namespace = {'db': _Db()}
        exec("BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE = "
             "{'accounting': 'accounting_manager', 'hr': 'hr_manager'}", namespace)
        exec(_function_source(BOT_PATH, "_back_office_employee_role"), namespace)
        role_of = namespace["_back_office_employee_role"]

        self.assertEqual('hr_manager', role_of(1499))
        self.assertEqual('accounting_manager', role_of(1500))
        self.assertEqual('accounting_manager', role_of('1500'))   # id строкой из JSON
        for department_id in (1, 909, None, 999999, 'не число'):
            self.assertIsNone(role_of(department_id), repr(department_id))


class ProfileSectionTests(unittest.TestCase):
    """«Профиль»: без плашки роли у всех, без операторского — у бэк-офиса."""

    def test_role_badge_is_gone_for_everyone(self):
        # Плашка печатала СЫРОЕ значение из базы («operator», «sv») латиницей,
        # с фолбэком «Оператор», который у любой другой должности просто неверен.
        app = _read(APP_PATH)
        self.assertNotIn("{profileData.role || 'Оператор'}", app)
        self.assertNotIn('fas fa-user-tag"></FaIcon> {profileData.role', app)

    def test_operator_blocks_are_gated(self):
        app = _read(APP_PATH)
        self.assertIn(
            "const profileHidesOperatorBlocks = departmentHidesOperatorFields(user);",
            app,
        )
        # Плитки оценок/часов, смена ставки, карточки СВ и ставки, быстрые
        # действия — всё, что ведёт в разделы, которых у бэк-офиса нет.
        self.assertIn("{!profileHidesOperatorBlocks && (() => {", app)
        self.assertIn("{!profileHidesOperatorBlocks && (\n                                    <RateSelfChangeCard", app)
        self.assertGreaterEqual(app.count("!profileHidesOperatorBlocks"), 5)
        # Взамен — должность и отдел.
        self.assertIn("{profileHidesOperatorBlocks && (", app)
        self.assertIn("{profileData.job_title || '-'}", app)
        self.assertIn("{profileData.department_name || '-'}", app)

    def test_backend_sends_job_title_and_department(self):
        endpoint = _function_source(BOT_PATH, "get_user_profile")
        self.assertIn("profile_data.update(db.get_user_hr_card(user[0]))", endpoint)
        card = _function_source(DATABASE_PATH, "get_user_hr_card") if False else _read(DATABASE_PATH)
        self.assertIn("def get_user_hr_card(self, user_id):", card)
        self.assertIn('return {"job_title": row[0], "department_name": row[1]}', card)

    def test_columns_use_the_staff_variant_for_back_office(self):
        # «Супервайзер», «Направление», «Ставка», «SIP» и «Вод. права» —
        # операторские колонки; вариант 'staff' их уже не рисует.
        app = _read(APP_PATH)
        self.assertIn(
            "const employeeSectionColumns = buildEmployeeSectionColumns(\n"
            "                departmentHidesOperatorFields(user) ? 'staff' : 'operator'\n"
            "            );",
            app,
        )


class EmployeeSectionWordingTests(unittest.TestCase):
    """В разделе сотрудников бэк-офиса не должно быть слова «операторы».

    Заголовок и кнопку «Добавить» переключал departmentUsesSimpleEmployeeAccounting
    и раньше, но подпись виджета дней рождения, пустое состояние, плейсхолдер
    поиска и текст QR-доступа остались операторскими — их и видел владелец.
    """

    def test_no_hardcoded_operator_wording_in_the_section(self):
        app = _read(APP_PATH)
        section = app.split("{(view === 'manage_users' || view === 'employees') && (", 1)[1]
        section = section.split("{view === 'manage_admins'", 1)[0]

        # Каждое упоминание операторов в разделе обязано быть под предикатом.
        for phrase in ("'Операторы'", "'Добавить оператора'", "'Операторы не найдены.'"):
            self.assertIn(phrase, section, phrase)
            for line in section.splitlines():
                if phrase in line:
                    self.assertIn(
                        "departmentUsesSimpleEmployeeAccounting(user) ?", line,
                        f"{phrase} без переключателя: {line.strip()[:100]}",
                    )

    def test_birthdays_widget_caption_and_sublabel(self):
        app = _read(APP_PATH)
        # Подпись виджета — та, что владелец увидел как «ОПЕРАТОРЫ».
        self.assertIn(
            "                                        departmentUsesSimpleEmployeeAccounting(user) "
            "? 'Сотрудники' : 'Операторы',",
            app,
        )
        # Подстрочник карточки: направления у бэк-офиса нет, есть должность.
        self.assertIn("const manageUsersBirthdayLabel = useCallback((employee) => (", app)
        self.assertIn("? (employee?.job_title || 'Должность не указана')", app)
        self.assertIn(": (employee?.direction || 'Без направления')", app)
        self.assertNotIn(
            "buildUpcomingBirthdays(operatorUsers, (employee) => employee?.direction || 'Без направления', 14)",
            app,
        )

    def test_search_covers_job_title(self):
        # У бэк-офиса нет ни направления, ни супервайзера — без должности поиск
        # работал бы только по имени, а плейсхолдер обещал бы несуществующее.
        app = _read(APP_PATH)
        self.assertIn("String(employee?.job_title || '').toLowerCase().includes(q) ||", app)
        self.assertIn('? "Поиск по имени или должности..."', app)

    def test_qr_screen_talks_about_employees(self):
        # Экран отрисован в двух ветках (админ и менеджер) — обе.
        app = _read(APP_PATH)
        self.assertNotIn("Отсканируйте QR оператора", app)
        self.assertEqual(2, app.count("Отсканируйте QR сотрудника или вставьте токен вручную."))

    def test_bulk_panel_drops_group_and_direction(self):
        # Оба списка у бэк-офиса пустые: групп и направлений в отделе нет.
        app = _read(APP_PATH)
        self.assertIn(
            "<div className={`grid grid-cols-1 gap-3 ${departmentHidesOperatorFields(user) "
            "? 'md:grid-cols-2' : 'md:grid-cols-4'}`}>",
            app,
        )
        bulk = app.split("Зажмите <span className=\"font-semibold\">Ctrl</span>", 1)[1]
        bulk = bulk.split("Применить массово", 1)[0]
        self.assertEqual(2, bulk.count("{!departmentHidesOperatorFields(user) && ("))
        # Ставка остаётся — она есть у всех.
        self.assertIn("Ставка: не менять", bulk)


class RankAndFileGatesTests(unittest.TestCase):
    """Гейты, написанные литералом 'operator' там, где имелся в виду «рядовой
    сотрудник». Пока роль была одна, литерал и смысл совпадали — с появлением
    hr_manager разошлись, и сломались сразу три экрана.
    """

    def test_profile_data_is_fetched_for_every_rank_and_file_role(self):
        # Эффект гейтился списком ['operator','trainee'] — для hr_manager он
        # выходил сразу, profileData оставался null, и раздел показывал
        # «Нету информации о профиле».
        app = _read(APP_PATH)
        self.assertIn(
            "                if (!user || !user.id || !isRankAndFileRole(currentUserRole) "
            "|| isScopedDepartmentHead) return;\n"
            "\n"
            "                if (view === 'profile') {\n"
            "                    fetchProfileData();",
            app,
        )
        # Часы/оценки/тренинги — только тем, у кого эти плитки в профиле есть.
        self.assertIn("                    if (!profileHidesOperatorBlocks) {\n"
                      "                        fetchHoursData();", app)
        self.assertIn("profileHidesOperatorBlocks]);", app)

    def test_salary_calculator_is_not_offered_to_rank_and_file(self):
        # Пункт стоял под ЗАПРЕЩАЮЩИМ списком: любая роль, которую забыли
        # перечислить, проваливалась внутрь и получала калькулятор.
        app = _read(APP_PATH)
        self.assertNotIn(
            "currentUserRole !== 'sv' && currentUserRole !== 'operator' "
            "&& currentUserRole !== 'trainer' && currentUserRole !== 'trainee' && (",
            app,
        )
        self.assertIn(
            "{!isAdminLikeRole && !isScopedDepartmentHead && !isRankAndFileRole(currentUserRole) "
            "&& currentUserRole !== 'sv' && currentUserRole !== 'trainer' && (",
            app,
        )

    def test_qr_modal_closes_when_access_is_granted(self):
        # После подтверждения окно с QR оставалось висеть, а опрос статуса
        # продолжал ходить на сервер: закрытие стояло на литерале 'operator'.
        app = _read(APP_PATH)
        self.assertIn(
            "                        if (sensitiveSectionQrRequiredFor(user) && data.granted) {\n"
            "                            clearSensitiveQrPolling();\n"
            "                            setShowSensitiveQrModal(false);",
            app,
        )
        self.assertNotIn("if (user.role === 'operator' && data.granted) {", app)

    def test_whole_qr_flow_shares_one_predicate(self):
        # Замок, кнопка в нём и закрытие окна обязаны спрашивать ОДНО и то же:
        # разойдясь, они дают экран, который показан, но не работает.
        app = _read(APP_PATH)
        self.assertEqual(4, app.count("sensitiveSectionQrRequiredFor"))  # объявление + 3 места
        for site in (
            "const sensitiveSectionsLocked = sensitiveSectionQrRequiredFor(user) && !sensitiveAccess.granted;",
            "if (sensitiveSectionQrRequiredFor(user) && data.granted) {",
            "if (!user || !sensitiveSectionQrRequiredFor(user)) return;",
        ):
            self.assertIn(site, app)

    def test_qr_button_works_for_everyone_the_lock_is_shown_to(self):
        # Замок рисовался, а кнопка в нём выходила на литерале 'operator' —
        # то есть молча не делала ничего.
        app = _read(APP_PATH)
        self.assertIn(
            "            const requestSensitiveQrAccess = async () => {",
            app,
        )
        self.assertIn("if (!user || !sensitiveSectionQrRequiredFor(user)) return;", app)
        self.assertNotIn("if (!user || user.role !== 'operator') return;", app)


class SensitiveQrRolesSingleSourceTests(unittest.TestCase):
    """Список ролей под QR — ОДИН на сервер и раздел «Вики».

    Копия здесь была бы четвёртой и разошлась бы молча: bot_schedule2 ответил бы
    «подтверждение не требуется», а wiki/access.py всё равно отказал бы — раздел
    просто не открывался бы, ничего не объясняя.
    """

    def test_backend_imports_the_list_instead_of_copying(self):
        source = _read(BOT_PATH)
        self.assertIn(
            "from wiki.access import QR_GATED_ROLES as SENSITIVE_QR_GATED_ROLES",
            source,
        )
        # Собственного литерального множества тех же ролей быть не должно.
        self.assertNotIn("SENSITIVE_QR_GATED_ROLES = frozenset", source)

    def test_all_three_endpoints_use_it(self):
        for name in ("request_sensitive_access_qr", "get_sensitive_access_status",
                     "approve_sensitive_access"):
            endpoint = _function_source(BOT_PATH, name)
            self.assertIn("SENSITIVE_QR_GATED_ROLES", endpoint, name)
            self.assertNotIn("== 'operator'", endpoint, name)
            self.assertNotIn("!= 'operator'", endpoint, name)

    def test_list_is_importable_on_its_own(self):
        # wiki.access не должен тянуть за собой ни Flask, ни пул к базе:
        # bot_schedule2 импортирует его на старте.
        import importlib
        import sys as _sys
        module = importlib.import_module("wiki.access")
        self.assertEqual(
            {"operator", "hr_manager", "accounting_manager"},
            set(module.QR_GATED_ROLES),
        )
        self.assertNotIn("bot_schedule2", _sys.modules)


class TasksSectionForBackOfficeTests(unittest.TestCase):
    """«Задачи» у ВСЕХ ролей бэк-офиса, а не только у главы и СВ.

    Раздел гейтится в шести независимых местах, и пять из них отказывают МОЛЧА
    (пустой список, пустой экран, ноль в колоколе). Поэтому проверяем не сами
    правки, а инвариант шире их: кому раздел выдала КАРТА отделов, того обязан
    пускать бэкенд, — иначе пункт меню есть, а за ним пустота.
    """

    BACK_OFFICE = {"hr": "hr_manager", "accounting": "accounting_manager"}

    @classmethod
    def _js_views(cls, source, name):
        """Значение набора разделов из departmentViews.js, со спредами."""
        line = next(row for row in source.splitlines() if row.startswith(f"const {name} = ["))
        views = []
        for spread in re.findall(r"\.\.\.([A-Z_]+)", line):
            views.extend(cls._js_views(source, spread))
        views.extend(re.findall(r"'([a-z_]+)'", line))
        return views

    def _roles_granted_tasks(self):
        """Роли, которым карта отделов выдала «Задачи», — прямо из исходника."""
        source = _read(DEPARTMENT_VIEWS_PATH)
        granted = {}
        for code in self.BACK_OFFICE:
            entry = source.split(f"    {code}: {{", 1)[1].split("},", 1)[0]
            for role, const in re.findall(r"(\w+): ([A-Z_]+)", entry):
                if "tasks" in self._js_views(source, const):
                    granted.setdefault(code, []).append(role)
        return granted

    def test_map_grants_tasks_to_every_role_of_the_department(self):
        granted = self._roles_granted_tasks()
        for code, own_role in self.BACK_OFFICE.items():
            self.assertEqual(
                {"operator", "trainee", own_role, "head", "sv"},
                set(granted.get(code, [])),
                code,
            )

    def _can_access_tasks(self, departments, user_departments, heads):
        """Настоящий _can_access_tasks с подставленной базой."""

        class _Db:
            def get_department_by_id(self, department_id):
                return departments.get(int(department_id))

            def get_user_department_id(self, user_id):
                return user_departments.get(int(user_id))

        namespace = {
            "db": _Db(),
            "ROLE_HIERARCHY": {
                "operator": 10, "trainee": 10, "hr_manager": 10, "accounting_manager": 10,
                "trainer": 20, "sv": 30, "admin": 40, "super_admin": 50,
            },
            "_headed_department_id": lambda requester_id: dict(heads).get(requester_id),
        }
        exec("BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE = "
             "{'accounting': 'accounting_manager', 'hr': 'hr_manager'}", namespace)
        exec("BACK_OFFICE_EMPLOYEE_ROLES = frozenset("
             "BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE.values())", namespace)
        exec("BACK_OFFICE_TASK_EMPLOYEE_ROLES = "
             "BACK_OFFICE_EMPLOYEE_ROLES | {'operator', 'trainee'}", namespace)
        for name in ("_normalize_user_role", "_get_role_level", "_has_min_role",
                     "_is_admin_role", "_back_office_employee_role",
                     "_back_office_task_employee_role", "_can_access_tasks"):
            exec(_function_source(BOT_PATH, name), namespace)
        return namespace["_can_access_tasks"]

    def test_backend_lets_in_everyone_the_map_did(self):
        # 1499 — HR, 1500 — Бухгалтерия, 1 — СЗоВ (отдел с линией).
        departments = {1: {"code": "szov"}, 1499: {"code": "hr"}, 1500: {"code": "accounting"}}
        can = self._can_access_tasks(departments, {10: 1499, 20: 1500, 30: 1}, heads={})
        granted = self._roles_granted_tasks()

        for user_id, code in ((10, "hr"), (20, "accounting")):
            for role in granted[code]:
                if role == "head":
                    continue  # глава — не роль, его пускает _headed_department_id
                self.assertTrue(can(role, user_id), f"{code}/{role}")

    def test_line_operator_does_not_get_tasks_along_the_way(self):
        # Роль 'operator' у кадровика и у оператора линии — одна и та же строка,
        # различает их только отдел. Проверка по роли открыла бы раздел всей линии.
        departments = {1: {"code": "szov"}, 1499: {"code": "hr"}}
        can = self._can_access_tasks(departments, {30: 1, 40: None}, heads={})
        self.assertFalse(can("operator", 30))
        self.assertFalse(can("trainee", 30))
        self.assertFalse(can("operator", 40))
        # И обратное: роль бэк-офиса в чужом отделе раздела не получает —
        # ровно как в add_user, где роль сверяется с отделом.
        self.assertFalse(can("hr_manager", 30))

    def test_managers_and_department_heads_still_get_in(self):
        can = self._can_access_tasks({1: {"code": "szov"}}, {50: 1, 60: 1, 70: 1}, heads={50: 1})
        self.assertTrue(can("admin", 50))   # глава отдела
        self.assertTrue(can("sv", 60))
        self.assertTrue(can("trainer", 70))

    def test_bell_asks_the_same_predicate_as_the_route_guard(self):
        # Разъехавшись, они дают худший из отказов: уведомление в колоколе есть,
        # а раздел за ним не открывается — и наоборот.
        guard = _function_source(BOT_PATH, "_task_route_guard")
        viewer = _function_source(BOT_PATH, "_notifications_viewer_context")
        self.assertIn("_can_access_tasks(requester_role, requester_id)", guard)
        self.assertIn("_can_access_tasks(role, requester_id)", viewer)
        # Своей копии списка ролей у колокола больше нет.
        self.assertNotIn("role in ('sv', 'trainer')", viewer)

    def test_data_layer_shares_one_list_of_personally_scoped_roles(self):
        source = _read(DATABASE_PATH)
        namespace = {}
        for name in ("BACK_OFFICE_EMPLOYEE_ROLES", "TASK_PERSONAL_SCOPE_ROLES",
                     "TASK_RECIPIENT_ROLES"):
            line = next(row for row in source.splitlines() if row.startswith(f"{name} = "))
            exec(line, namespace)

        self.assertEqual({"hr_manager", "accounting_manager"},
                         set(namespace["BACK_OFFICE_EMPLOYEE_ROLES"]))
        # Охват личный: постановщик / поручитель / исполнитель — тот же, что у СВ.
        self.assertEqual({"sv", "trainer", "hr_manager", "accounting_manager"},
                         set(namespace["TASK_PERSONAL_SCOPE_ROLES"]))
        # Рядовой бэк-офиса обязан быть и в ПОЛУЧАТЕЛЯХ: иначе задачу ему не
        # поручит ни глава отдела, ни админ, и раздел покажет только своё.
        self.assertEqual(("super_admin", "admin", "sv", "accounting_manager", "hr_manager"),
                         tuple(namespace["TASK_RECIPIENT_ROLES"]))

        # Три места, где список ролей раньше был переписан заново.
        self.assertIn("elif role in TASK_PERSONAL_SCOPE_ROLES:", source)
        self.assertIn("if role_has_min(role, 'admin') or role in TASK_PERSONAL_SCOPE_ROLES:", source)
        self.assertIn("(u.role IN ({_TASK_RECIPIENT_ROLES_SQL}) {scope_filter})", source)

    def test_personal_scope_has_no_default_department(self):
        # Отдел задачи — это отдел ПОСТАНОВЩИКА. Подставив рядовому его
        # собственный отдел, переключатель молча спрятал бы всё, что поручили
        # извне, — например задачу от админа из другого отдела.
        guard = _function_source(BOT_PATH, "_task_route_guard")
        endpoint = _function_source(BOT_PATH, "get_task_departments")
        self.assertIn("g.task_scope_is_personal = back_office_role is not None", guard)
        self.assertIn("None if getattr(g, 'task_scope_is_personal', False)", endpoint)

    def test_menu_item_and_screen_live_in_the_same_branch(self):
        # Пункт меню и экран объявлены в РАЗНЫХ ветках дерева, и забыть вторую
        # значит выдать раздел, который открывается в пустую область.
        app = _read(APP_PATH)
        marker = "{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && ("
        self.assertEqual(2, app.count(marker))
        menu_branch = app.split(marker, 1)[1]
        screen_branch = app.split(marker, 2)[2]
        self.assertIn("{departmentAllowsView(user, 'tasks') && (", menu_branch)
        self.assertIn("{renderTasksSidebarButtonInner()}", menu_branch)
        self.assertIn('{( view === "tasks" && (', screen_branch)
        self.assertIn("<TasksView", screen_branch)

    def test_section_gate_asks_the_map_not_a_list_of_roles(self):
        view = _read(ROOT / "src" / "components" / "tasks" / "TasksView.jsx")
        self.assertIn(
            "|| (departmentRestrictsViews(user) && departmentAllowsView(user, 'tasks'));",
            view,
        )
        # departmentRestrictsViews обязателен: у отдела без ограничений
        # departmentAllowsView возвращает true всем подряд.
        self.assertIn(
            "import { departmentAllowsView, departmentRestrictsViews } from '../../utils/departmentViews';",
            view,
        )
        # Сырой код роли в выпадашке исполнителей — тот самый шум, который
        # появляется, как только бэк-офис попадает в получатели.
        self.assertIn("hr_manager: 'HR',", view)
        self.assertIn("accounting_manager: 'Бухгалтерия',", view)


if __name__ == "__main__":
    unittest.main()
