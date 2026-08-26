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


if __name__ == "__main__":
    unittest.main()
