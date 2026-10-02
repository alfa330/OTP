"""Отдел «Маркетинг»: должность marketing_manager и её разделы.

Решение владельца 04.09.2026. Сотрудника «Маркетинга» заводят собственной
должностью (как кадровика и бухгалтера — отдел без линии), в карточке у него
есть «Должность» вместо группы, направления и SIP-номера, а из разделов ему
открыты десять пунктов сайдбара. Выдаются они ЧЕТЫРЬМЯ разными механизмами, и
проверять надо каждый — пропуск любого даёт пункт меню без содержимого либо
403 за открытым пунктом:

  карта разделов        «Курсы», «Журнал оценок», «Деление звонков»,
  (DEPARTMENT_VIEW_      «Опросы», «Задачи» (+ «Профиль», без которого человек
   ALLOWLIST)             не увидит собственную «Должность»)
  UNIVERSAL_VIEWS       «Ивенты»
  тумблер отдела        «Вики» (departments.wiki_enabled + пространство)
  свой предикат         «ИИ-оценка», «Лиды OLX»
  ничего                «Уведомление» — это колокол, у него нет view-ключа

Периметр внутри разделов — НАБЛЮДАТЕЛЬ: те же данные, что у главы его отдела,
и ни одного действия. Границу держит одно место на бэкенде
(_is_marketing_observer вместе с _request_is_read_only) и одно на фронте
(isMarketingObserver); поведение сáмой карты разделов гоняет настоящий Node —
tests/back_office_department_views.test.mjs.
"""
import ast
import re
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
CALL_EVALUATION_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"
SURVEYS_PATH = ROOT / "src" / "components" / "surveys" / "SurveysView.jsx"
TASKS_VIEW_PATH = ROOT / "src" / "components" / "tasks" / "TasksView.jsx"
NEWS_SHARED_PATH = ROOT / "src" / "components" / "news" / "newsShared.js"

ROLE = "marketing_manager"
DEPARTMENT_CODE = "marketing"


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


class RoleIsKnownEverywhereTests(unittest.TestCase):
    """Иерархия должностей переписана в ЧЕТЫРЁХ местах, и разъезд молчаливый.

    Пропуск роли не падает: role_level_of вернёт 0, и человек не пройдёт ни одно
    ограничение по уровню — «Вики» откроется пустой, а правила на 'operator' его
    не увидят (wiki.access.expand_otp_roles раздаёт роли не выше своего уровня).
    """

    HIERARCHY_FILES = (BOT_PATH, DATABASE_PATH, ROLES_PATH, WIKI_ACCESS_PATH)

    def test_role_sits_at_operator_level_in_every_copy(self):
        for path in self.HIERARCHY_FILES:
            with self.subTest(path=path.name):
                self.assertRegex(_read(path), rf"'?{ROLE}'?:\s*10\b")

    def test_wiki_and_news_share_one_table(self):
        # news/access.py переиспользует ROLE_LEVELS из wiki.access — без роли
        # там «Новость дня» перестала бы на неё адресоваться.
        import importlib

        wiki_access = importlib.import_module("wiki.access")
        news_access = importlib.import_module("news.access")
        self.assertEqual(10, wiki_access.ROLE_LEVELS[ROLE])
        self.assertIs(news_access.ROLE_LEVELS, wiki_access.ROLE_LEVELS)


class RoleIsStorableTests(unittest.TestCase):
    """CHECK на users.role — два места, и на проде работает только второе."""

    def test_check_constraint_lists_the_role_in_both_places(self):
        source = _read(DATABASE_PATH)
        # CREATE TABLE — только чистая база (dev/CI/новый стенд).
        self.assertIn(
            "'operator', 'trainee', 'hr_manager', 'accounting_manager', 'marketing_manager')",
            source,
        )
        # Пересборка констрейнта — единственное, что работает на боевой базе:
        # DROP IF EXISTS + ADD на каждом старте, дополнить CHECK нельзя.
        self.assertIn(
            "ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;",
            source,
        )
        self.assertIn(
            "                    'operator', 'trainee', 'hr_manager', 'accounting_manager',\n"
            "                    'marketing_manager'\n",
            source,
        )

    def test_role_fits_the_column(self):
        # Колонка расширяется до VARCHAR(32) только при length < 32; 17 символов
        # укладываются, и трогать ширину не нужно.
        self.assertEqual(17, len(ROLE))
        self.assertIn("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(32);", _read(DATABASE_PATH))


class RoleIsBoundToItsDepartmentTests(unittest.TestCase):
    """Должность действительна только в своём отделе — как у бэк-офиса."""

    def test_backend_map_and_frontend_mirror_agree(self):
        backend = _read(BOT_PATH)
        frontend = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn("    'marketing': 'marketing_manager',", backend)
        self.assertIn("    marketing: 'marketing_manager',", frontend)

        namespace = {}
        exec(
            "BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE = {"
            "'accounting': 'accounting_manager', 'hr': 'hr_manager',"
            " 'marketing': 'marketing_manager'}",
            namespace,
        )
        self.assertEqual(
            {"accounting_manager", "hr_manager", "marketing_manager"},
            set(namespace["BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT_CODE"].values()),
        )

    def test_add_user_accepts_the_role(self):
        endpoint = _function_source(BOT_PATH, "add_user")
        self.assertIn("'hr_manager', 'accounting_manager', 'marketing_manager'):", endpoint)
        # Роль вне своего отдела — дыра в правах, а не опечатка: в чужом отделе
        # её нет в конфиге DEPARTMENT_VIEW_ALLOWLIST, а роль вне конфига
        # ограничений не получает вовсе и увидела бы всё меню.
        self.assertIn(
            "if role in BACK_OFFICE_EMPLOYEE_ROLES and _back_office_employee_role(department_id) != role:",
            endpoint,
        )
        # Отдел обязан разбираться ВЫШЕ проверки, иначе она сверяется с None.
        self.assertLess(
            endpoint.index("department_id = "),
            endpoint.index("if role in BACK_OFFICE_EMPLOYEE_ROLES"),
        )

    def test_answer_names_the_position_instead_of_calling_everyone_an_operator(self):
        endpoint = _function_source(BOT_PATH, "add_user")
        self.assertIn("elif role in SENSITIVE_ACCESS_ROLE_LABELS:", endpoint)
        self.assertIn("'marketing_manager': 'Менеджер маркетинга'", _read(BOT_PATH))

    def test_role_has_a_human_label_everywhere_it_is_shown(self):
        self.assertIn("marketing_manager: 'Маркетинг',", _read(TASKS_VIEW_PATH))
        self.assertIn("marketing_manager: 'маркетинг',", _read(NEWS_SHARED_PATH))
        # «Вики» отделу открыта, значит роль попадёт и в выгрузку тренажёров.
        self.assertIn(
            "'marketing_manager': 'Менеджер маркетинга',",
            _read(ROOT / "wiki" / "trainer_report.py"),
        )


class QrGateTests(unittest.TestCase):
    """Подтверждение QR на «Вики» обязано остаться.

    Сегодня люди «Маркетинга» заведены обычными 'operator' и подтверждение
    проходят. Перевод их на собственную должность БЕЗ строки в QR_GATED_ROLES
    снял бы подтверждение молча — дословный повтор дефекта со стажёром
    в «Посылках».
    """

    def test_role_is_qr_gated(self):
        import importlib

        module = importlib.import_module("wiki.access")
        self.assertIn(ROLE, module.QR_GATED_ROLES)

    def test_there_is_someone_to_approve(self):
        # Подтверждает админ, глава отдела или СВ. Глава «Маркетинга» под
        # ограничение карты разделов не попадает (ключа 'head' у отдела нет),
        # значит пункт «QR доступ» у него остаётся и подтвердить есть кому.
        views = _read(DEPARTMENT_VIEWS_PATH)
        marketing_config = views.split("    marketing: {", 1)[1].split("    },", 1)[0]
        self.assertNotIn("head:", marketing_config)
        self.assertIn("_sensitive_access_approval_error", _read(BOT_PATH))


class EmployeeCardTests(unittest.TestCase):
    """Карточка как у бэк-офиса: «Должность» вместо операторских полей."""

    def test_job_title_and_hidden_operator_fields(self):
        views = _read(DEPARTMENT_VIEWS_PATH)
        self.assertIn(
            "const EMPLOYEE_JOB_TITLE_DEPARTMENTS = "
            "new Set(['accounting', 'hr', 'marketing', 'request_processing_department']);",
            views,
        )
        self.assertIn(
            "const OPERATOR_FIELDS_HIDDEN_DEPARTMENTS = new Set(['accounting', 'hr', 'marketing']);",
            views,
        )

    def test_backend_mirrors_the_hidden_fields(self):
        # Разъезд здесь молчаливый: поле скрыто, а сервер всё равно требует
        # направление, и завести сотрудника становится нельзя.
        self.assertIn(
            "OPERATOR_FIELDS_HIDDEN_DEPARTMENT_CODES = frozenset({'accounting', 'hr', 'marketing'})",
            _read(BOT_PATH),
        )


class SectionAllowlistTests(unittest.TestCase):
    """Карта разделов: ровно шесть ключей и «Профиль» первым."""

    def setUp(self):
        views = _read(DEPARTMENT_VIEWS_PATH)
        self.constant = views.split("const MARKETING_EMPLOYEE_VIEWS = [", 1)[1].split("];", 1)[0]
        self.config = views.split("    marketing: {", 1)[1].split("    },", 1)[0]

    def test_exactly_six_views_with_profile_first(self):
        keys = re.findall(r"'([a-z_]+)'", self.constant)
        self.assertEqual(
            ["profile", "tasks", "surveys", "lms", "call_evaluation", "call_division"],
            keys,
        )
        # firstAllowedView берёт allow[0]: это раздел по умолчанию, и только в
        # «Профиле» сотрудник видит собственную «Должность».
        self.assertEqual("profile", keys[0])

    def test_only_the_own_role_is_restricted(self):
        # Решение владельца: людей, заведённых в отделе до появления должности,
        # ограничение не касается, глава отдела тоже остаётся без него.
        self.assertIn("marketing_manager: MARKETING_EMPLOYEE_VIEWS,", self.config)
        for role in ("operator:", "trainee:", "head:", "sv:"):
            self.assertNotIn(role, self.config)

    def test_sections_handed_out_by_other_means_are_not_duplicated_here(self):
        # Дубль здесь завёл бы вторую, молча расходящуюся проверку.
        for view_key in ("'wiki'", "'events'", "'olx_leads'", "'ai_qa'"):
            self.assertNotIn(view_key, self.constant)

    def test_marketing_uses_its_own_constant(self):
        # Расширив BACK_OFFICE_EMPLOYEE_VIEWS, мы бы молча выдали те же шесть
        # разделов Бухгалтерии и HR.
        self.assertIn(
            "const BACK_OFFICE_EMPLOYEE_VIEWS = ['profile', 'tasks'];",
            _read(DEPARTMENT_VIEWS_PATH),
        )


class SidebarTests(unittest.TestCase):
    """Пункт меню и экран обязаны быть в ОДНОЙ ветке.

    До «Маркетинга» «Журнал оценок» и «Деление звонков» жили только в ветках
    админа и руководителя. Пункт без экрана открывается в пустую область — ни
    ошибки, ни заглушки.
    """

    def setUp(self):
        self.app = _read(APP_PATH)

    def test_two_new_items_live_in_the_rank_and_file_branch(self):
        for view_key in ("call_evaluation", "call_division"):
            with self.subTest(view=view_key):
                self.assertIn(
                    f"{{departmentRestrictsViews(user) && departmentAllowsView(user, '{view_key}') && (",
                    self.app,
                )

    def test_the_double_condition_is_not_an_accident(self):
        # У отделов без карты разделов (СЗоВ) departmentAllowsView отвечает true
        # на любой ключ: без departmentRestrictsViews оба пункта появились бы у
        # каждого оператора линии.
        self.assertNotIn(
            "{departmentAllowsView(user, 'call_evaluation') && (\n"
            "                                            <li>\n"
            "                                                <button onClick={(e) => handleSidebarViewNavigation(e, 'call_evaluation')}",
            self.app,
        )

    def test_call_division_screen_is_rendered_for_rank_and_file(self):
        rank_and_file_block = self.app.split(
            "{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && (", 2
        )[2]
        self.assertIn('{( view === "call_division" && (<AdminCallsUploadView user={user}/>))}',
                      rank_and_file_block)

    def test_notification_bell_needs_nothing(self):
        # «Уведомление» из перечня владельца — это колокол: он отрисован
        # безусловно над списком пунктов, view-ключа у него нет.
        self.assertIn("<NotificationsBell", self.app)


class FrontendObserverPredicateTests(unittest.TestCase):
    """Признак доступа — членство в отделе ВМЕСТЕ с должностью, не одна роль."""

    def setUp(self):
        self.app = _read(APP_PATH)

    def test_predicate_checks_department_and_role_and_excludes_the_head(self):
        predicate = self.app.split("const isMarketingObserver = (userLike) => (", 1)[1].split(");", 1)[0]
        self.assertIn("!isDepartmentHead(userLike)", predicate)
        self.assertIn("MARKETING_OBSERVER_DEPARTMENT_CODE", predicate)
        self.assertIn("normalizeRole(userLike?.role) === 'marketing_manager'", predicate)
        self.assertIn("const MARKETING_OBSERVER_DEPARTMENT_CODE = 'marketing';", self.app)

    def test_every_section_predicate_is_wired(self):
        # «Курсы», «ИИ-оценка», «Лиды OLX», «Журнал оценок».
        self.assertIn("isMarketingObserver(userLike) ||\n        (role === 'super_admin' && userId === 2)", self.app)
        # СВ отделов раздела проходят своим предикатом: исходный (СВ ОП)
        # переиспользован в «Настройках SIP» и «Касаниях», и расширять его на
        # СЗоВ и Тез КЦ значило бы открыть им заодно те разделы.
        self.assertIn("isAiQaSupervisor(userLike) ||\n    isMarketingObserver(userLike) ||", self.app)
        self.assertIn("if (isMarketingObserver(userLike)) return true;", self.app)
        self.assertIn(
            "const canSeeCallEvaluation = isAdminLikeRole || isDepartmentManager || isMarketingObserverUser;",
            self.app,
        )
        # Обе константы журнала меняются вместе: одна решает, активен ли вид,
        # вторая — монтировать ли iframe.
        self.assertIn(
            "const isCallEvaluationView = view === 'call_evaluation' "
            "&& (isAdminLikeRole || isDepartmentManager || isMarketingObserverUser);",
            self.app,
        )

    def test_verifier_chats_stay_closed(self):
        """«Чаты Верификаторов» наблюдателю не положены.

        Раздел ездит на СВОЁМ предикате canAccessVerifierChatsForUser, и его
        периметр перечислен ЯВНО. Раньше он выводился из аудитории «ИИ-оценки»
        (canAccessAiQaForUser), и вычет наблюдателя обязан был стоять первой
        строкой. Вывод перестал быть верным, когда «ИИ-оценка» расширилась на
        СЗоВ и Тез КЦ: он молча отдал бы переписку Wazzup — раздел ОТДЕЛА
        ПРОДАЖ — главе Тез КЦ и супервайзерам СЗоВ/Тез. Вычет наблюдателя
        остался на месте и по-прежнему обязан быть первым: он единственный, кого
        надо отнять у аудитории, которая иначе его включает.
        """
        predicate = self.app.split(
            "const canAccessVerifierChatsForUser = (userLike) => {", 1
        )[1].split("};", 1)[0]
        self.assertIn("if (isMarketingObserver(userLike)) return false;", predicate)
        self.assertLess(
            predicate.index("isMarketingObserver(userLike)"),
            predicate.index("super_admin"),
            "вычет наблюдателя обязан стоять до любой допускающей проверки",
        )
        # Периметр не выводится из «ИИ-оценки»: иначе он снова поедет за ней.
        self.assertNotIn("canAccessAiQaForUser(userLike)", predicate)
        # Зеркало на бэкенде — тот же вычет в гарде раздела, и тоже свой периметр.
        guard = _function_source(BOT_PATH, "_verifier_chats_guard")
        self.assertIn("_is_marketing_observer(", guard)
        self.assertNotIn("return _ai_qa_guard()", guard)


class BackendObserverTests(unittest.TestCase):
    """Одно определение наблюдателя на весь бэкенд и жёсткое «только чтение»."""

    def setUp(self):
        self.bot = _read(BOT_PATH)

    def test_helper_checks_department_and_excludes_the_head(self):
        helper = _function_source(BOT_PATH, "_is_marketing_observer")
        self.assertIn("_normalize_user_role(role) != MARKETING_OBSERVER_ROLE", helper)
        self.assertIn("if _headed_department_id(requester_id) is not None:", helper)
        self.assertIn("== MARKETING_OBSERVER_DEPARTMENT_CODE", helper)
        self.assertIn("MARKETING_OBSERVER_DEPARTMENT_CODE = 'marketing'", self.bot)
        self.assertIn("MARKETING_OBSERVER_ROLE = 'marketing_manager'", self.bot)

    def test_helper_runs_and_costs_the_database_nothing_for_other_roles(self):
        """Функция стоит на общих проверках доступа к звонку, а те зовутся в
        цикле по списку: для всех прочих ролей выход отсюда обязан быть без
        обращения к базе, а наблюдателю ответ — запоминаться на время запроса."""
        calls = {"department_id": 0, "department": 0}

        class _Db:
            def get_user(self, id=None):
                return (id, None, "Имя", ROLE)

            def get_user_department_id(self, requester_id):
                calls["department_id"] += 1
                return 888

            def get_department_by_id(self, department_id):
                calls["department"] += 1
                return {"id": department_id, "code": "Marketing"}

        class _G:
            pass

        namespace = {
            "MARKETING_OBSERVER_ROLE": ROLE,
            "MARKETING_OBSERVER_DEPARTMENT_CODE": DEPARTMENT_CODE,
            "db": _Db(),
            "g": _G(),
            "_headed_department_id": lambda requester_id: None,
            "ROLE_NORMALIZATION_MAP": {},
        }
        exec(_function_source(BOT_PATH, "_normalize_user_role"), namespace)
        exec(_function_source(BOT_PATH, "_is_marketing_observer"), namespace)
        is_observer = namespace["_is_marketing_observer"]

        self.assertTrue(is_observer(7, ROLE))
        for _ in range(5):
            self.assertTrue(is_observer(7, ROLE))
        self.assertEqual({"department_id": 1, "department": 1}, calls, "ответ кешируется на запрос")

        # Регистр кода отдела не важен — двойник отдаёт 'Marketing'.
        self.assertFalse(is_observer(8, "operator"))
        self.assertFalse(is_observer(9, "sv"))
        self.assertEqual({"department_id": 1, "department": 1}, calls, "чужая роль базу не трогает")

        # Глава отдела сюда не попадает: у него свои, более широкие двери.
        namespace["_headed_department_id"] = lambda requester_id: 888
        namespace["g"] = _G()
        self.assertFalse(is_observer(10, ROLE))

    def test_read_only_helper_denies_outside_a_request(self):
        namespace = {"request": None}
        exec(_function_source(BOT_PATH, "_request_is_read_only"), namespace)
        self.assertFalse(namespace["_request_is_read_only"]())

        class _Req:
            def __init__(self, method):
                self.method = method

        for method, expected in (("GET", True), ("HEAD", True), ("OPTIONS", True),
                                 ("POST", False), ("PUT", False), ("DELETE", False)):
            with self.subTest(method=method):
                namespace["request"] = _Req(method)
                self.assertEqual(expected, namespace["_request_is_read_only"]())

    def test_shared_call_guards_let_the_observer_only_read(self):
        # Через эти две проверки ходят и чтение, и запись оценки. Граница по
        # методу запроса — одно место вместо перечня ручек.
        for name in ("_authorize_operator_scope", "_ensure_call_access_for_requester"):
            with self.subTest(function=name):
                source = _function_source(BOT_PATH, name)
                self.assertIn("if _is_marketing_observer(requester_id, role):", source)
                self.assertIn("return _request_is_read_only()", source)

    def test_ai_qa_guard_refuses_writes(self):
        guard = _function_source(BOT_PATH, "_ai_qa_guard")
        self.assertIn("if _is_marketing_observer(requester_id, role):", guard)
        # Кроме чтения — только поимённые действия (AiQaObserverActionTests).
        self.assertIn("if _request_is_read_only() or _ai_qa_observer_may_act(requester_id):", guard)
        self.assertIn("Раздел открыт вам только на просмотр", guard)

    def test_surveys_guard_lists_the_role(self):
        # _has_any_role сверяет ТОЧНОЕ членство в кортеже: десятка в
        # ROLE_HIERARCHY сюда роль не проводит.
        guard = _function_source(BOT_PATH, "_surveys_route_guard")
        self.assertIn("MARKETING_OBSERVER_ROLE)", guard)

    def test_lms_opens_by_department_not_by_allowlisted_account(self):
        self.assertIn("LMS_LEARNER_ROLES = ('operator', 'trainee', 'marketing_manager')", self.bot)
        allowed = _function_source(BOT_PATH, "_lms_is_allowed_account")
        self.assertIn("_is_marketing_observer(user_id, role_norm)", allowed)

    def test_call_distribution_run_stays_closed(self):
        run_endpoint = _function_source(BOT_PATH, "call_distribution_run")
        self.assertIn(
            "if not (_is_admin_role(role) or _headed_department_id(requester_id) is not None):",
            run_endpoint,
        )
        self.assertNotIn("_is_marketing_observer", run_endpoint)


class OlxLeadsAccessTests(unittest.TestCase):
    """Модуль прав «Лидов OLX» импортируется без базы — гоняем его напрямую."""

    def setUp(self):
        import importlib

        self.access = importlib.import_module("olx_amo.access")

    def _member(self, **over):
        ctx = {
            "role": ROLE,
            "department_code": DEPARTMENT_CODE,
            "headed_department_ids": [],
            "headed_department_codes": [],
        }
        ctx.update(over)
        return ctx

    def test_member_sees_the_section_but_does_not_write_to_candidates(self):
        ctx = self._member()
        self.assertTrue(self.access.can_view(ctx))
        # Ответ кандидату — действие НАРУЖУ, от лица компании.
        self.assertFalse(self.access.can_reply(ctx))
        self.assertFalse(self.access.can_manage_cabinets(ctx))

    def test_other_departments_and_roles_stay_out(self):
        self.assertFalse(self.access.can_view(self._member(department_code="op")))
        self.assertFalse(self.access.can_view(self._member(role="operator")))
        self.assertFalse(self.access.can_view(self._member(role="hr_manager",
                                                           department_code="hr")))

    def test_heads_keep_what_they_had(self):
        head = {
            "role": "admin",
            "department_code": DEPARTMENT_CODE,
            "headed_department_ids": [888],
            "headed_department_codes": [DEPARTMENT_CODE],
        }
        self.assertTrue(self.access.can_view(head))
        self.assertTrue(self.access.can_reply(head))
        self.assertFalse(self.access.can_manage_cabinets(head))

    def test_member_list_is_separate_from_the_heads_list(self):
        # Дописав 'marketing' вторым в SECTION_HEAD_DEPARTMENT_CODES, мы бы
        # открыли раздел рядовым отдела продаж.
        self.assertEqual(("op", "marketing"), self.access.SECTION_HEAD_DEPARTMENT_CODES)
        self.assertEqual(DEPARTMENT_CODE, self.access.SECTION_MEMBER_DEPARTMENT_CODE)
        self.assertEqual(ROLE, self.access.SECTION_MEMBER_ROLE)


class EvaluationJournalObserverTests(unittest.TestCase):
    """Журнал — отдельная сборка: «смотреть» и «менять» разведены на два флага."""

    def setUp(self):
        self.source = _read(CALL_EVALUATION_PATH)

    def test_viewer_and_admin_are_separate_flags(self):
        self.assertIn(
            "const isMarketingObserver = canonicalRole === 'marketing_manager' && !isDepartmentHead;",
            self.source,
        )
        self.assertIn("const isAdminRole = isBaseAdminRole || isDepartmentHead;", self.source)
        self.assertIn("const isEvaluationViewer = isAdminRole || isMarketingObserver;", self.source)

    def test_action_tabs_stay_closed_for_the_observer(self):
        # Наблюдатель не админ, не СВ и не глава — три вкладки действий
        # выключаются сами; проверяем, что их условия остались на isAdminRole.
        self.assertIn("const canUseRequests = isAdminRole || isSupervisorRole || isDepartmentHead;", self.source)
        self.assertIn("const canUseCalibration = isGlobalAdminRole || isSupervisorRole;", self.source)
        self.assertIn("const canUseCheckpoints = isAdminRole || isSupervisorRole || isDepartmentHead;", self.source)
        self.assertIn("const canUseAnalytics = isEvaluationViewer || isSupervisorRole;", self.source)

    def test_batch_feedback_stays_on_the_action_flag(self):
        self.assertIn("(isAdminRole || isSupervisorRole) && !!call && !call.is_imported", self.source)


class AiQaObserverActionTests(unittest.TestCase):
    """Поимённое право действий наблюдателя в «ИИ-оценке» (02.10.2026, id 471 и 472).

    Отдел у них прежний — только ОП, — а запись открыта на перечень ручек:
    «Из АТС», «Моя оценка», разборы ИИ. Гоняем настоящие функции гарда на
    двойниках, а список людей и ручек — НАСТОЯЩИЙ, из исходника: опасны оба
    промаха — лишняя открытая ручка (шкала критериев, ручная выборка) и чужой
    отдел у оператора.
    """

    GRANTED = (471, 472)
    # Такой же наблюдатель «Маркетинга», но без поимённого права.
    OTHER_OBSERVER = 474

    def setUp(self):
        class _Request:
            method = "GET"
            endpoint = None

        class _G:
            user_id = None

        self.request = _Request()
        self.g = _G()
        self.operator_departments = {1: "op", 2: "szov", 3: "tez"}
        self.namespace = {
            "AI_QA_EXTRA_ACCESS_USER_IDS": {183, 169},
            "AI_QA_OBSERVER_SCOPE_DEPARTMENTS": ("op",),
            "AI_QA_SUBJECT_DEPARTMENT_CODES": frozenset({"op", "szov", "tez"}),
            "request": self.request,
            "g": self.g,
            "jsonify": lambda payload: payload,
            "db": type("_Db", (), {"get_user": staticmethod(
                lambda id=None: (id, None, "name", ROLE))})(),
            "_normalize_user_role": lambda role: role,
            "_is_global_admin_requester": lambda role, requester_id: False,
            "_is_ai_qa_department_head": lambda requester_id: False,
            "_is_marketing_observer": lambda requester_id, role=None: True,
            "_request_is_read_only": lambda: self.request.method in ("GET", "HEAD", "OPTIONS"),
            "_department_code_of_user": lambda user_id: self.operator_departments.get(user_id),
            "_ensure_call_access_for_requester": lambda *args: "delegated",
        }
        bot_source = _read(BOT_PATH)
        for pattern in (r"^AI_QA_OBSERVER_ACTION_USER_IDS = \{[\d, ]*\}$",
                        r"^AI_QA_OBSERVER_ACTION_ENDPOINTS = frozenset\(\{.*?\}\)"):
            exec(re.search(pattern, bot_source, re.S | re.M).group(0), self.namespace)
        for name in ("_ai_qa_guard", "_ai_qa_observer_may_act", "_ai_qa_operator_access"):
            exec(_function_source(BOT_PATH, name), self.namespace)

    def _guard(self, user_id, method, endpoint):
        self.g.user_id = user_id
        self.request.method = method
        self.request.endpoint = endpoint
        return self.namespace["_ai_qa_guard"]()

    def test_grant_lists_exactly_the_people_named_by_the_owner(self):
        # Строка в этом списке — запись в журнал оценок операторов ОП. Новый
        # человек появляется здесь только вместе с решением владельца.
        self.assertEqual(set(self.GRANTED), self.namespace["AI_QA_OBSERVER_ACTION_USER_IDS"])
        self.assertNotIn(self.OTHER_OBSERVER, self.namespace["AI_QA_OBSERVER_ACTION_USER_IDS"])

    def test_granted_observer_acts_only_through_listed_endpoints(self):
        for user_id in self.GRANTED:
            for endpoint in ("api_ai_qa_pull_call", "api_ai_qa_human_review",
                             "api_ai_qa_adjudicate", "api_ai_qa_adjudicate_refine",
                             "api_ai_qa_adjudicate_similar"):
                with self.subTest(user_id=user_id, endpoint=endpoint):
                    self.assertEqual((user_id, None), self._guard(user_id, "POST", endpoint))
            for endpoint in ("api_ai_qa_criteria_config", "api_ai_qa_daily_sample",
                             "api_ai_qa_rag_rollout", None):
                with self.subTest(user_id=user_id, endpoint=endpoint):
                    requester_id, err = self._guard(user_id, "POST", endpoint)
                    self.assertIsNone(requester_id)
                    self.assertEqual(403, err[1])

    def test_other_observers_stay_read_only(self):
        self.assertEqual((self.OTHER_OBSERVER, None),
                         self._guard(self.OTHER_OBSERVER, "GET", "api_ai_qa_evaluations"))
        requester_id, err = self._guard(self.OTHER_OBSERVER, "POST", "api_ai_qa_pull_call")
        self.assertIsNone(requester_id)
        self.assertEqual(403, err[1])

    def test_grant_needs_marketing_membership(self):
        # Переведён из «Маркетинга» — наблюдателем больше не считается, и
        # поимённая строка ничего сверх общих правил не даёт.
        self.namespace["_is_marketing_observer"] = lambda requester_id, role=None: False
        for user_id in self.GRANTED:
            with self.subTest(user_id=user_id):
                requester_id, err = self._guard(user_id, "POST", "api_ai_qa_pull_call")
                self.assertIsNone(requester_id)
                self.assertEqual(403, err[1])

    def test_operator_access_is_sales_only_and_section_only(self):
        access = self.namespace["_ai_qa_operator_access"]
        self.request.method = "POST"
        for user_id in self.GRANTED:
            with self.subTest(user_id=user_id):
                self.g.user_id = user_id
                self.request.endpoint = "api_ai_qa_pull_call"
                self.assertTrue(access(1, None, user_id))
                self.assertFalse(access(2, None, user_id))
                self.assertFalse(access(3, None, user_id))
                # Вне ручек раздела (тот же человек в «Журнале оценок») — общая
                # проверка, которая наблюдателю оставляет только чтение.
                self.request.endpoint = "save_call_evaluation"
                self.assertEqual("delegated", access(1, None, user_id))
        self.request.endpoint = "api_ai_qa_pull_call"
        self.assertEqual("delegated", access(1, None, self.OTHER_OBSERVER))

    def test_listed_endpoints_exist_and_section_sites_use_the_wrapper(self):
        source = _read(BOT_PATH)
        for endpoint in self.namespace["AI_QA_OBSERVER_ACTION_ENDPOINTS"]:
            with self.subTest(endpoint=endpoint):
                self.assertRegex(source, rf"(?m)^def {endpoint}\(\):")
        for name, needle in (
                ("api_ai_qa_pull_call", "if not _ai_qa_operator_access(operator_id, requester, requester_id):"),
                ("_cdr_import_touch", "if not _ai_qa_operator_access(operator_id, requester, requester_id):"),
                ("api_ai_qa_human_review",
                 "if not _ai_qa_operator_access(int(target['operator_id']), requester, requester_id):")):
            with self.subTest(function=name):
                body = _function_source(BOT_PATH, name)
                self.assertIn(needle, body)
                self.assertNotIn("_ensure_call_access_for_requester(", body)


class SurveysRespondentTests(unittest.TestCase):
    """Литерал role === 'operator' в смысле «рядовой» — знакомый класс дефекта.

    Роль, которая не оператор и не руководитель, проваливалась в МЕНЕДЖЕРСКУЮ
    вкладку вопросов, где у теста видны правильные ответы.
    """

    def test_respondent_role_covers_every_rank_and_file_position(self):
        source = _read(SURVEYS_PATH)
        self.assertIn("const isOperator = isRespondentRole(user?.role);", source)
        self.assertIn(
            "roleIsAny(role, ['operator', 'trainee']) || isBackOfficeEmployeeRole(role)",
            source,
        )
        # Менеджерская вкладка вопросов — отрицательная ветка того же флага.
        self.assertIn("{!isOperator && (!canManage || activeTab === 'questions') && (", source)


if __name__ == "__main__":
    unittest.main()
