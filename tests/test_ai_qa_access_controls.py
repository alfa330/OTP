from pathlib import Path
import ast
from types import SimpleNamespace
import unittest
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]


def _load_function(source, function_name, namespace):
    tree = source_cache.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function_name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<ai-qa-access>", "exec"), namespace)
    return namespace[function_name]


def _load_assignment(source, name, namespace):
    """Присваивание уровня модуля (например ROLE_HIERARCHY) — как оно в исходнике.

    Нужно ровно затем, чтобы не переписывать таблицу ролей в тест: подмена
    сделала бы проверку прав тавтологией, а изменение уровня 'admin' в
    bot_schedule2.py прошло бы мимо теста.
    """
    tree = source_cache.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in item.targets)
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<ai-qa-access>", "exec"), namespace)
    return namespace[name]


class _DepartmentDb:
    def __init__(self, departments, user_departments=None):
        self.departments = departments
        self.user_departments = user_departments or {}

    def get_department_by_id(self, department_id):
        return self.departments.get(int(department_id))

    def get_user_department(self, user_id):
        department_id = self.user_departments.get(user_id)
        department = self.departments.get(int(department_id)) if department_id else None
        return (department_id, (department or {}).get("code"))

    def get_user_department_id(self, user_id):
        return self.user_departments.get(user_id)


class AiQaAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.call_qa_view_source = (
            ROOT / "src" / "components" / "call_qa" / "CallQaView.jsx"
        ).read_text(encoding="utf-8-sig")
        cls.database_source = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_frontend_allows_super_admin_and_moldir_user_id(self):
        self.assertIn("const AI_QA_EXTRA_ACCESS_USER_IDS = new Set([183]);", self.app_source)
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin'", self.app_source)
        self.assertIn("AI_QA_EXTRA_ACCESS_USER_IDS.has(Number(userLike?.id))", self.app_source)
        self.assertIn("const canAccessAiQaSection = canAccessAiQaForUser(user);", self.app_source)
        self.assertIn('view === "ai_qa" && canAccessAiQaSection', self.app_source)

    def test_frontend_allows_szov_department_head_for_ai_qa_and_verifier_chats(self):
        # Перечень собирается из отделов раздела и отдела-наблюдателя, а не
        # выписан одним литералом: список оцениваемых отделов должен совпадать
        # с call_qa.config.DEPARTMENT_CODES, и на копии литералов здесь уже
        # ловили «Верификатор».
        self.assertIn(
            "const AI_QA_SUBJECT_DEPARTMENT_CODES = new Set(['op', 'szov', 'tez']);",
            self.app_source,
        )
        self.assertIn(
            "const AI_QA_OBSERVER_DEPARTMENT_CODES = new Set(['marketing']);",
            self.app_source,
        )
        self.assertIn(
            "const AI_QA_HEAD_DEPARTMENT_CODES = new Set([\n"
            "    ...AI_QA_SUBJECT_DEPARTMENT_CODES, ...AI_QA_OBSERVER_DEPARTMENT_CODES,\n"
            "]);",
            self.app_source,
        )
        self.assertIn("const isAiQaDepartmentHead = (userLike) => (", self.app_source)
        self.assertIn("userLike?.headed_department_codes ?? userLike?.headedDepartmentCodes", self.app_source)
        self.assertIn("isAiQaDepartmentHead(userLike) ||", self.app_source)
        # «Чаты Верификаторов» — переписка Wazzup отдела продаж, а не общий
        # раздел: у СВ СЗоВ и Тез КЦ своя переписка, и им этот пункт не нужен.
        self.assertIn('(isAiQaDepartmentHead(user) || isOpSalesSupervisorForAiQa(user)) && (', self.app_source)
        # А сама «ИИ-оценка» доступна СВ всех трёх отделов раздела.
        self.assertIn('(isAiQaDepartmentHead(user) || isAiQaSupervisor(user)) && (', self.app_source)
        self.assertIn("requestedViewFromUrl !== 'wazzup_chats' || canAccessVerifierChatsSection", self.app_source)
        self.assertIn('view === "wazzup_chats" && canAccessVerifierChatsSection', self.app_source)

    def test_frontend_opens_verifier_chats_to_global_admins(self):
        """«Чаты Верификаторов» — глобальным админам; периметр ЯВНЫЙ.

        Раньше предикат начинался с canAccessAiQaForUser, то есть наследовал
        аудиторию «ИИ-оценки». После её расширения на СЗоВ и Тез КЦ это отдало бы
        переписку Wazzup (раздел ОТДЕЛА ПРОДАЖ) главе Тез КЦ и супервайзерам
        СЗоВ/Тез, поэтому периметр перечислен по частям.
        """
        predicate = self.app_source.split(
            "const canAccessVerifierChatsForUser = (userLike) => {", 1
        )[1].split("\n};", 1)[0]
        self.assertIn("if (isMarketingObserver(userLike)) return false;", predicate)
        # Именно ВЫЗОВА быть не должно (в комментарии имя упоминается законно).
        self.assertNotIn("canAccessAiQaForUser(userLike)", predicate)
        # Глава отдела с базовой admin-ролью — не глобальный админ. Без этой
        # половины условия раздел открылся бы главам бухгалтерии, HR и ТЭЗ.
        self.assertIn(
            "normalizeRole(userLike?.role) === 'admin' && !isDepartmentHead(userLike)",
            predicate,
        )
        # Главы — ОП, СЗоВ и маркетинг (наблюдатель разборов ОП); Тез КЦ здесь
        # быть не должно: у него своя переписка в разделе «Чаты ChatApp».
        self.assertIn("VERIFIER_CHATS_HEAD_DEPARTMENT_CODES", predicate)
        self.assertIn(
            "const VERIFIER_CHATS_HEAD_DEPARTMENT_CODES = new Set(['op', 'szov', 'marketing']);",
            self.app_source)
        self.assertNotIn("'tez'", predicate)
        # СВ — только отдела продаж, не «любой СВ раздела ИИ-оценки».
        self.assertIn("isOpSalesSupervisorForAiQa(userLike)", predicate)
        self.assertNotIn("isAiQaSupervisor", predicate)
        self.assertIn(
            "const canAccessVerifierChatsSection = canAccessVerifierChatsForUser(user);",
            self.app_source,
        )
        self.assertNotIn("canAccessAiQaSection = canAccessVerifierChats", self.app_source)
        # «ИИ-оценка» пускает и ГЛОБАЛЬНОГО админа — ради него добавлен селектор
        # отдела (задача про СЗоВ и Тез КЦ). Глава отдела с базовой admin-ролью
        # глобальным админом не считается и проходит своей проверкой.
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin' ||\n"
                      "    // Глобальный админ", self.app_source)
        self.assertIn("(normalizeRole(userLike?.role) === 'admin' && !isDepartmentHead(userLike)) ||\n"
                      "    isAiQaDepartmentHead(userLike) ||", self.app_source)
        # Пункт меню — по новому флагу, и так в КАЖДОЙ ветке сайдбара: пункт
        # продублирован по ролям (админы, СВ/главы, общий хвост), и ветка,
        # забытая на старом флаге, оставила бы раздел достижимым только по URL.
        # Проверяем условие над каждым пунктом, а не отступы: разметку
        # переформатируют, и тест на пробелах ловил бы форматирование, а не права.
        blocks = []
        marker = "handleSidebarViewNavigation(e, 'wazzup_chats')"
        at = self.app_source.find(marker)
        while at >= 0:
            head = self.app_source.rfind("&& (", 0, at)
            blocks.append(self.app_source[self.app_source.rfind("{", 0, head):at])
            at = self.app_source.find(marker, at + 1)
        self.assertGreaterEqual(len(blocks), 3, "ветки сайдбара изменились — проверь тест")
        for block in blocks:
            with self.subTest(block=block.strip()[:80]):
                self.assertNotIn("canAccessAiQaSection", block)
                self.assertTrue(
                    "canAccessVerifierChatsSection" in block
                    or "isAiQaDepartmentHead(user)" in block,
                    block,
                )
        # Ключевая ловушка: общее условие на два раздела выкидывало бы админа
        # из чатов сразу после входа (и гасило бы переход по ссылке на чат).
        self.assertNotIn("(view === 'ai_qa' || view === 'wazzup_chats')", self.app_source)
        self.assertIn("if (view === 'wazzup_chats' && !canAccessVerifierChatsSection) {", self.app_source)
        self.assertIn("if (view === 'wazzup_chats' && canAccessVerifierChatsSection) return;", self.app_source)
        self.assertIn("if (view === 'ai_qa' && !canAccessAiQaSection) {", self.app_source)
        self.assertIn("if (view === 'ai_qa' && canAccessAiQaSection) return;", self.app_source)

    def test_backend_allows_moldir_user_id(self):
        self.assertIn("AI_QA_EXTRA_ACCESS_USER_IDS = {183}", self.api_source)
        self.assertIn("int(requester_id) in AI_QA_EXTRA_ACCESS_USER_IDS", self.api_source)
        self.assertIn("if _is_super_admin_role(role):", self.api_source)

    def test_backend_recognizes_heads_of_every_section_department(self):
        """Главы ОП, СЗоВ и Тез КЦ — свои отделы; глава маркетинга — наблюдатель.

        Тез КЦ раньше отсекался (его в разделе не было). Отдел, которого в
        разделе нет вовсе (например «Фронт офисы»), не проходит.
        """
        departments = {
            367: {"id": 367, "code": "op"},
            501: {"id": 501, "code": "SZoV"},
            560: {"id": 560, "code": "tez"},
            888: {"id": 888, "code": "marketing"},
            909: {"id": 909, "code": "front_office"},
        }
        headed_by_user = {10: 367, 20: 501, 30: 560, 40: 909, 50: 560, 60: 888}
        all_headed_by_user = {10: {367}, 20: {501}, 30: {560}, 40: {909},
                              50: {560, 501}, 60: {888}}
        namespace = {
            "db": _DepartmentDb(departments),
            "_headed_department_id": lambda user_id: headed_by_user.get(user_id),
            "_headed_department_ids": lambda user_id: frozenset(all_headed_by_user.get(user_id, set())),
            "AI_QA_OP_DEPARTMENT_ID": 367,
            "AI_QA_HEAD_DEPARTMENT_CODES": frozenset({"op", "szov", "tez", "marketing"}),
        }
        _load_function(self.api_source, "_headed_department_codes", namespace)
        fn = _load_function(self.api_source, "_is_ai_qa_department_head", namespace)

        self.assertTrue(fn(10), "Глава ОП")
        self.assertTrue(fn(20), "Глава СЗоВ")
        self.assertTrue(fn(30), "Глава Тез КЦ — отдел вошёл в раздел")
        self.assertFalse(fn(40), "Глава отдела вне раздела")
        self.assertFalse(fn(70), "Не глава")
        self.assertTrue(fn(50), "Access must consider every formally headed department")
        self.assertTrue(fn(60), "Глава маркетинга допущен наблюдателем")

    def test_backend_section_departments_come_from_call_qa_config(self):
        """Перечень оцениваемых отделов — ОДИН с пакетом оценки, не вторая копия.

        На копии литералов здесь уже ловили «Верификатор»: направление молча
        выпадало из allowlist, потому что список жил в двух местах.
        """
        from call_qa import config as qa_config

        self.assertIn(
            "AI_QA_SUBJECT_DEPARTMENT_CODES = frozenset(call_qa_config.DEPARTMENT_CODES)",
            self.api_source,
        )
        self.assertEqual(set(qa_config.DEPARTMENT_CODES), {"op", "szov", "tez"})
        # Наблюдатель остаётся в периметре раздела, но со своей областью данных.
        self.assertIn("AI_QA_OBSERVER_DEPARTMENT_CODES = frozenset({'marketing'})",
                      self.api_source)
        self.assertIn("AI_QA_OBSERVER_SCOPE_DEPARTMENTS = ('op',)", self.api_source)

    def test_user_payload_exposes_formal_head_department_codes(self):
        self.assertIn('"headed_department_code": headed_department_code', self.api_source)
        self.assertIn('"headed_department_codes": headed_department_codes', self.api_source)
        self.assertIn("SELECT id, name, code", self.database_source)
        self.assertIn('{"id": int(row[0]), "name": row[1] or "", "code": row[2] or ""}', self.database_source)

    def test_department_head_with_sv_base_role_keeps_full_ai_qa_tabs(self):
        self.assertIn("import { isDepartmentHead, normalizeRole }", self.call_qa_view_source)
        self.assertIn(
            "normalizeRole(user?.role) === 'sv' && !isDepartmentHead(user)",
            self.call_qa_view_source,
        )

    def test_verifier_chat_routes_use_verifier_chats_guard(self):
        """Ручки раздела «Чаты Верификаторов» — под своим, более широким гардом.

        Эпизоды в этот список не входят: эпизод — единица ИИ-оценки, и админу он
        не открывается. Гард у них остаётся _ai_qa_guard.
        """
        section_names = {
            "api_wazzup_channels",
            "api_wazzup_chats",
            "api_wazzup_chat_messages",
            "api_wazzup_authors",
            "api_wazzup_authors_map",
            "api_wazzup_analytics",
        }
        ai_qa_only_names = {
            "api_wazzup_episodes",
            "api_wazzup_episode",
            "api_wazzup_episodes_rebuild",
        }
        functions = {
            node.name: ast.get_source_segment(self.api_source, node)
            for node in source_cache.parse(self.api_source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in (section_names | ai_qa_only_names)
        }
        for function_name in section_names:
            with self.subTest(function_name=function_name):
                self.assertIn("_verifier_chats_guard()", functions[function_name])
        # Эпизоды — единица ИИ-оценки, поэтому у них СВОЙ гард: аудитория
        # «ИИ-оценки» И отдел продаж. Раньше хватало _ai_qa_guard, пока в
        # разделе жил один отдел; после расширения на СЗоВ и Тез КЦ он стал
        # пропускать к эпизодам Верификаторов их главу и СВ.
        for function_name in ai_qa_only_names:
            with self.subTest(function_name=function_name):
                self.assertIn("_op_episodes_guard()", functions[function_name])
                self.assertNotIn("_verifier_chats_guard()", functions[function_name])

    def test_backend_verifier_chats_guard_admits_global_admins_only(self):
        """Гард чатов: глобальный админ проходит, глава чужого отдела — нет.

        Проверяем не текст, а поведение, и на настоящих helper'ах роли: подмена
        _is_global_admin_requester сделала бы тест тавтологией, а именно там и
        живёт разница между «админом» и «главой отдела с ролью admin».
        """
        users = {
            1: (1, None, "Супер-админ", "super_admin"),
            2: (2, None, "Админ", "admin"),
            3: (3, None, "Глава ТЭЗ", "admin"),
            4: (4, None, "Глава СЗоВ", "admin"),
            5: (5, None, "Оператор", "operator"),
            6: (6, None, "СВ ОП", "sv"),
            7: (7, None, "Тренер", "trainer"),
            8: (8, None, "Маркетолог", "marketing_manager"),
            9: (9, None, "СВ СЗоВ", "sv"),
        }
        headed = {3: 560, 4: 501}          # id отдела, которым человек назначен главой
        ai_qa_heads = {3, 4}               # главы Тез КЦ и СЗоВ — в «ИИ-оценке»
        departments_of = {6: 367, 8: 888, 9: 1}  # отдел сотрудника (СВ и маркетолог)
        # Коды нужны целиком: периметр «Чатов Верификаторов» перечислен по кодам,
        # а не выведен из аудитории «ИИ-оценки».
        department_codes = {888: "marketing", 501: "szov", 560: "tez", 367: "op", 1: "szov"}

        class _Db:
            @staticmethod
            def get_user(id=None):
                return users.get(id)

            @staticmethod
            def get_user_department_id(user_id):
                return departments_of.get(user_id)

            @staticmethod
            def get_user_department(user_id):
                department_id = departments_of.get(user_id)
                return (department_id, department_codes.get(department_id))

            @staticmethod
            def get_department_by_id(department_id):
                code = department_codes.get(department_id)
                return {"id": department_id, "code": code} if code else None

        namespace = {
            "db": _Db(),
            "g": SimpleNamespace(user_id=None),
            "jsonify": lambda payload: payload,
            "logging": SimpleNamespace(exception=lambda *_a, **_kw: None),
            "AI_QA_EXTRA_ACCESS_USER_IDS": {183},
            "AI_QA_OP_DEPARTMENT_ID": 367,
            "AI_QA_SUBJECT_DEPARTMENT_CODES": frozenset({"op", "szov", "tez"}),
            # «Чаты Верификаторов» — раздел ОТДЕЛА ПРОДАЖ: главы ОП и СЗоВ, но
            # не Тез КЦ, и СВ только продаж. Периметр в гарде перечислен явно,
            # поэтому в область видимости нужны его собственные имена.
            "VERIFIER_CHATS_HEAD_DEPARTMENT_CODES": frozenset({"op", "szov"}),
            "_headed_department_id": lambda user_id: headed.get(user_id),
            "_headed_department_ids": lambda user_id: (
                frozenset({headed[user_id]}) if user_id in headed else frozenset()),
            "_is_ai_qa_department_head": lambda user_id: user_id in ai_qa_heads,
        }
        _load_assignment(self.api_source, "ROLE_HIERARCHY", namespace)
        namespace["request"] = SimpleNamespace(method="GET")
        _load_assignment(self.api_source, "MARKETING_OBSERVER_ROLE", namespace)
        _load_assignment(self.api_source, "MARKETING_OBSERVER_DEPARTMENT_CODE", namespace)
        for helper in ("_normalize_user_role", "_get_role_level", "_has_min_role",
                       "_is_super_admin_role", "_is_admin_role",
                       "_is_global_admin_requester", "_request_is_read_only",
                       "_is_marketing_observer", "_headed_department_codes",
                       "_department_code_of_user", "_ai_qa_guard"):
            _load_function(self.api_source, helper, namespace)
        guard = _load_function(self.api_source, "_verifier_chats_guard", namespace)

        def verdict(user_id):
            namespace["g"] = SimpleNamespace(user_id=user_id)
            return guard()

        self.assertEqual(verdict(1), (1, None), "Супер-админ")
        self.assertEqual(verdict(2), (2, None), "Глобальный админ — тот, ради кого раздел открыли")
        self.assertEqual(verdict(4), (4, None), "Глава СЗоВ проходил и до правки")
        # Регресс, который дала бы «аудитория ИИ-оценки»: раздел — переписка
        # Wazzup отдела продаж, у Тез КЦ своя в «Чатах ChatApp».
        self.assertEqual(verdict(3), (None, ({"error": "forbidden"}, 403)),
                         "Глава Тез КЦ в «Чаты Верификаторов» не допущен")
        self.assertEqual(verdict(9), (None, ({"error": "forbidden"}, 403)),
                         "СВ СЗоВ в «Чаты Верификаторов» не допущен")
        self.assertEqual(verdict(6), (6, None), "СВ отдела продаж проходил и до правки")
        self.assertEqual(verdict(183), (183, None), "whitelist ИИ-оценки")

        for user_id, who in ((3, "глава ТЭЗ с ролью admin"), (5, "оператор"),
                             (7, "тренер"), (8, "рядовой маркетолог"), (None, "без сессии")):
            with self.subTest(who=who):
                requester, err = verdict(user_id)
                self.assertIsNone(requester, who)
                self.assertEqual(err, ({"error": "forbidden"}, 403), who)

        # Маркетолога отсекает ИМЕННО этот гард, а не общий: в «ИИ-оценку» он
        # проходит. Без проверки различие было бы недоказанным — оба ответа
        # «forbidden» могли бы приходить из одного и того же места.
        namespace["g"] = SimpleNamespace(user_id=8)
        self.assertEqual(namespace["_ai_qa_guard"](), (8, None),
                         "разборы ИИ маркетологу открыты — закрыта только переписка")

    def test_backend_grants_full_scope_to_allowed_department_heads(self):
        guard_source = ast.get_source_segment(
            self.api_source,
            next(
                node for node in source_cache.parse(self.api_source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_ai_qa_guard"
            ),
        )
        scope_source = ast.get_source_segment(
            self.api_source,
            next(
                node for node in source_cache.parse(self.api_source).body
                if isinstance(node, ast.FunctionDef) and node.name == "_ai_qa_direction_scope"
            ),
        )
        self.assertIn("if _is_ai_qa_department_head(requester_id):", guard_source)
        self.assertIn("if _is_ai_qa_department_head(requester_id):\n        return None", scope_source)

        class _AccessDb:
            @staticmethod
            def get_user(id=None):
                return (id, None, "Глава СЗоВ", "operator")

            @staticmethod
            def get_user_department_id(_user_id):
                return None

            @staticmethod
            def get_supervisor_direction_ids(*_args, **_kwargs):
                raise AssertionError("A department head must not receive supervisor scope")

        namespace = {
            "AI_QA_EXTRA_ACCESS_USER_IDS": set(),
            "AI_QA_OP_DEPARTMENT_ID": 367,
            "AI_QA_SUBJECT_DEPARTMENT_CODES": frozenset({"op", "szov", "tez"}),
            "db": _AccessDb(),
            "g": SimpleNamespace(user_id=20),
            "jsonify": lambda payload: payload,
            "logging": SimpleNamespace(exception=lambda *_args, **_kwargs: None),
            "request": SimpleNamespace(method="GET"),
            "_normalize_user_role": lambda role: role,
            "_is_super_admin_role": lambda _role: False,
            "_is_global_admin_requester": lambda _role, _user_id=None: False,
            "_is_marketing_observer": lambda _user_id, _role=None: False,
            "_department_code_of_user": lambda _user_id: "",
            "_is_ai_qa_department_head": lambda user_id: user_id == 20,
        }
        guard = _load_function(self.api_source, "_ai_qa_guard", namespace)
        scope = _load_function(self.api_source, "_ai_qa_direction_scope", namespace)
        self.assertEqual(guard(), (20, None))
        self.assertIsNone(scope(20))


if __name__ == "__main__":
    unittest.main()
