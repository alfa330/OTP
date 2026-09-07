"""Раздел «ИИ-оценка» на три отдела: ОП, СЗоВ и Тез КЦ.

Стережём то, что при расширении раздела ломается МОЛЧА:

* новый вид субъекта, забытый в одной из карт-регистров или в SQL-склейке
  очереди, проходит все проверки на Python и просто исчезает из очереди ревью и
  списка оценок — ровно так вёл себя `_SUBJECT_EXISTS`;
* промпт звонка отдела продаж входит в evaluation_fingerprint, и любая его
  правка помечает все сохранённые оценки устаревшими;
* граница отделов: подстановка ?department= не должна открывать чужой отдел.
"""
from pathlib import Path
import ast
import re
from types import SimpleNamespace
import unittest

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]


def _load_function(source, function_name, namespace):
    tree = source_cache.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == function_name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<ai-qa-multi-dept>", "exec"), namespace)
    return namespace[function_name]


class SubjectRegistryTests(unittest.TestCase):
    """Каждый вид субъекта заведён ВО ВСЕХ картах, а не в части из них."""

    def setUp(self):
        from call_qa import config
        self.config = config

    def test_five_subject_kinds_split_into_calls_and_chats(self):
        config = self.config
        self.assertEqual(set(config.SUBJECT_KINDS), {
            "call", "wz_episode", "imported_call", "c2d_snapshot", "ca_episode"})
        # Разбиение полное и без пересечений: по нему выбирается и промпт,
        # и вкладка раздела.
        self.assertEqual(set(config.AUDIO_SUBJECT_KINDS) | set(config.CHAT_SUBJECT_KINDS),
                         set(config.SUBJECT_KINDS))
        self.assertFalse(set(config.AUDIO_SUBJECT_KINDS) & set(config.CHAT_SUBJECT_KINDS))

    def test_every_department_has_exactly_one_chat_source(self):
        config = self.config
        self.assertEqual(set(config.DEPARTMENT_CODES), {"op", "szov", "tez"})
        for code in config.DEPARTMENT_CODES:
            with self.subTest(department=code):
                subject = config.chat_subject_kind(code)
                self.assertIn(subject, config.CHAT_SUBJECT_KINDS)
        # Источники не совпадают: иначе одна переписка оценивалась бы дважды,
        # под двумя отделами.
        subjects = [config.chat_subject_kind(c) for c in config.DEPARTMENT_CODES]
        self.assertEqual(len(subjects), len(set(subjects)))

    def test_every_subject_kind_has_a_loader(self):
        from call_qa import subjects
        self.assertEqual(set(subjects._LOADERS), set(self.config.SUBJECT_KINDS))

    def test_every_subject_kind_has_its_own_advisory_lock_namespace(self):
        """Общий лок заставлял бы разные субъекты с одинаковым id ждать друг друга."""
        from call_qa.evaluation import runtime_store
        classids = runtime_store._LOCK_CLASSID
        self.assertEqual(set(classids), set(self.config.SUBJECT_KINDS))
        self.assertEqual(len(set(classids.values())), len(classids))
        # Звонок исторически 71623 — менять нельзя, иначе на деплое два процесса
        # разойдутся в пространствах локов.
        self.assertEqual(classids[self.config.SUBJECT_CALL], 71623)
        self.assertEqual(classids[self.config.SUBJECT_WZ_EPISODE], 71626)

    def test_unknown_subject_does_not_silently_share_the_call_lock(self):
        from call_qa.evaluation import runtime_store
        with self.assertRaises(ValueError):
            with runtime_store.distributed_call_lock(1, subject_kind="nope"):
                pass

    def test_every_subject_kind_has_a_source_resolver(self):
        from call_qa import api
        self.assertEqual(set(api._SOURCE_RESOLVERS), set(self.config.SUBJECT_KINDS))
        # Звонок из АТС читается тем же путём, что звонок журнала.
        self.assertIs(api._SOURCE_RESOLVERS[self.config.SUBJECT_IMPORTED_CALL],
                      api._SOURCE_RESOLVERS[self.config.SUBJECT_CALL])

    def test_every_subject_kind_knows_where_its_human_review_lives(self):
        """id субъекта НЕ равен calls.id ни у кого, кроме звонка журнала.

        Чтение calls «по id субъекта» показало бы чужой звонок, поэтому у
        каждого вида свой запрос."""
        from call_qa import api
        self.assertEqual(set(api._HUMAN_REVIEW_SQL), set(self.config.SUBJECT_KINDS))
        self.assertIn("imported_call_id", api._HUMAN_REVIEW_SQL[self.config.SUBJECT_IMPORTED_CALL])
        self.assertIn("c2d_snapshot_id", api._HUMAN_REVIEW_SQL[self.config.SUBJECT_C2D_SNAPSHOT])
        self.assertIn("chatapp_episodes", api._HUMAN_REVIEW_SQL[self.config.SUBJECT_CA_EPISODE])

    def test_every_subject_kind_has_a_scope_query(self):
        from call_qa import api
        self.assertEqual(set(api._SCOPE_SQL), set(self.config.SUBJECT_KINDS))

    def test_unknown_subject_is_refused_by_scope_check_not_allowed_through(self):
        """Молчаливое «считаем звонком» здесь = утечка чужого отдела супервайзеру."""
        from call_qa import api
        with self.assertRaises(ValueError):
            api.call_in_scope(1, [1], subject_kind="nope")
        # None-скоуп (неограниченный зритель) проверку не выполняет вовсе.
        self.assertTrue(api.call_in_scope(1, None, subject_kind="nope"))

    def test_every_subject_kind_has_prompt_intro_and_transcript_label(self):
        from call_qa.evaluation import evaluator
        self.assertEqual(set(evaluator._INTRO), set(self.config.SUBJECT_KINDS))
        self.assertEqual(set(evaluator._TRANSCRIPT_LABEL), set(self.config.SUBJECT_KINDS))

    def test_every_subject_kind_has_a_batch_checkpoint_prefix(self):
        from call_qa import batch_eval
        self.assertEqual(set(batch_eval._SUBJECT_PREFIX), set(self.config.SUBJECT_KINDS))
        self.assertEqual(len(set(batch_eval._SUBJECT_PREFIX.values())),
                         len(batch_eval._SUBJECT_PREFIX))


class QueueSqlTests(unittest.TestCase):
    """SQL-склейка очереди перечисляет все виды субъектов.

    Вид, забытый в этих строках, проходит весь Python и потом исчезает из
    очереди ревью и списка оценок: его отсекает _SUBJECT_EXISTS.
    """

    def setUp(self):
        from call_qa import api, config
        self.api = api
        self.config = config

    def test_join_covers_every_subject_kind(self):
        for kind in self.config.SUBJECT_KINDS:
            with self.subTest(subject=kind):
                self.assertIn(f"rc.subject_kind = '{kind}'", self.api._SUBJECT_JOIN)

    def test_existence_filter_covers_every_joined_table(self):
        # По одному алиасу на вид субъекта: если у вида нет своего условия
        # существования, его строки отфильтруются как «субъекта нет».
        aliases = re.findall(r"(\w+)\.id IS NOT NULL", self.api._SUBJECT_EXISTS)
        self.assertEqual(len(aliases), len(self.config.SUBJECT_KINDS))
        self.assertEqual(len(set(aliases)), len(aliases))

    def test_direction_operator_and_datetime_fall_back_across_all_kinds(self):
        for expression in (self.api._SUBJECT_DIRECTION, self.api._SUBJECT_OPERATOR,
                           self.api._SUBJECT_DATETIME):
            with self.subTest(expression=expression[:30]):
                self.assertTrue(expression.strip().startswith("COALESCE"))
                # столько источников, сколько видов субъекта
                self.assertGreaterEqual(expression.count(","), len(self.config.SUBJECT_KINDS) - 1)

    def test_human_score_expression_reaches_pools_without_a_journal_row(self):
        """У звонка из АТС и заявки Chat2Desk балл человека лежит не в calls.score."""
        self.assertIn("imported_call_id", self.api._SUBJECT_HUMAN_SCORE)
        self.assertIn("c2d_snapshot_id", self.api._SUBJECT_HUMAN_SCORE)


class SchemaConstraintTests(unittest.TestCase):
    """Значение subject_kind ограничено на каждой таблице, где оно в ключе."""

    @classmethod
    def setUpClass(cls):
        cls.schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8")

    def test_every_check_lists_every_subject_kind(self):
        from call_qa import config
        checks = re.findall(r"CHECK \(subject_kind IN \(([^)]*)\)\)", self.schema)
        self.assertGreaterEqual(len(checks), 6, "CHECK на каждой таблице с ключом")
        for values in checks:
            with self.subTest(values=values):
                listed = {v.strip().strip("'") for v in values.split(",")}
                self.assertEqual(listed, set(config.SUBJECT_KINDS))

    def test_transcript_cache_key_includes_the_subject(self):
        """Без subject_kind в ключе звонок из АТС делил бы транскрипт со звонком
        журнала: у них ОДИН путь к записи и один провайдер ASR, а совпадение
        числовых id между calls и imported_calls реально (диапазоны перекрыты).
        Читатель фильтрует по subject_kind, поэтому запись «не находилась» бы и
        карточка гоняла бы платный ASR при каждом открытии."""
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_transcript_subject", self.schema)
        index = self.schema[self.schema.index("uq_ai_transcript_subject"):]
        index = index[:index.index(";")]
        self.assertIn("subject_kind", index)
        # Старый ключ снимается по составу колонок: сгенерированное Postgres имя
        # обрезано до 63 символов, угадывать его нельзя.
        self.assertIn("contype = 'u'", self.schema)

    def test_writer_conflict_target_matches_the_new_key(self):
        writer = (ROOT / "call_qa" / "evaluation" / "runtime_store.py").read_text(encoding="utf-8")
        self.assertIn("ON CONFLICT (subject_kind,call_id,audio_fingerprint,asr_provider",
                      writer)
        # Восстановление после гонки читает ту же строку, что и писало.
        self.assertIn("WHERE subject_kind=%s AND call_id=%s AND audio_fingerprint=%s", writer)


class PromptTests(unittest.TestCase):
    """Промпт зависит от субъекта И отдела, но у звонка ОП обязан не меняться."""

    # Тот же эталон, что в tests/test_ai_qa_chat_subject.py: prompt_hash входит
    # в evaluation_fingerprint, и правка пометила бы все оценки звонков продаж
    # устаревшими — то есть потребовала бы переоценки за деньги.
    CALL_PROMPT_HASH = "36eeb3fd743d6d67a2888e7344c1c232a25a24c5312a9e02e70c9f22e4921f46"

    def setUp(self):
        self.crits = [{"idx": 0, "name": "X", "description": "Y",
                       "is_critical": False, "deficiency": None}]

    def test_sales_call_prompt_is_unchanged_by_the_department_argument(self):
        from call_qa.evaluation import evaluator
        from call_qa.evaluation.fingerprint import content_hash
        for department in (None, "op", "OP", " op "):
            with self.subTest(department=department):
                self.assertEqual(
                    content_hash(evaluator.build_system(self.crits, "call", department)),
                    self.CALL_PROMPT_HASH)

    def test_other_departments_drop_the_sales_wording(self):
        from call_qa.evaluation import evaluator
        for department in ("szov", "tez"):
            for kind in ("call", "imported_call"):
                with self.subTest(department=department, subject=kind):
                    prompt = evaluator.build_system(self.crits, kind, department)
                    self.assertNotIn("отдела продаж", prompt)
                    # остальная часть вступления звонка сохраняется
                    self.assertIn("[S1]/[S2]", prompt)
                    self.assertIn("казахский + русский", prompt)

    def test_chat_prompts_of_new_sources_keep_the_chat_rules(self):
        from call_qa.evaluation import evaluator
        for kind in ("c2d_snapshot", "ca_episode"):
            with self.subTest(subject=kind):
                prompt = evaluator.build_system(self.crits, kind, "szov")
                self.assertNotIn("отдела продаж", prompt)
                self.assertNotIn("[S1]/[S2]", prompt)
                self.assertIn("Роли определять не нужно", prompt)
                # Орфография в набранном тексте — на операторе; поблажка ASR
                # остаётся только для расшифровки голосовых.
                self.assertIn("подлинный текст", prompt)

    def test_verifier_chat_prompt_still_names_the_sales_department(self):
        """Эпизоды Wazzup бывают только у ОП — их промпт менять незачем."""
        from call_qa.evaluation import evaluator
        prompt = evaluator.build_system(self.crits, "wz_episode", "op")
        self.assertIn("переписок отдела продаж", prompt)
        self.assertEqual(prompt, evaluator.build_system(self.crits, "wz_episode"))

    def test_fingerprint_uses_the_prompt_that_is_actually_sent(self):
        """prompt_hash считался от промпта ЗВОНКА для любого субъекта — прогон
        переписки был подписан чужим промптом."""
        api_source = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8")
        self.assertIn("prompt_hash=content_hash(evaluator.build_system(\n"
                      "            transcript_criteria, subject_kind, department))",
                      api_source)


class Chat2DeskEligibilityTests(unittest.TestCase):
    """У заявки Chat2Desk нет доли ответов — честность решает передача чата.

    Проверено на проде 07.09.2026: из 1972 неоценённых заявок с ≥2 ответами
    оператора реально поделены между людьми 131.
    """

    def _subject(self, messages, **over):
        base = {"kind": "c2d_snapshot", "id": 1, "operator_user_id": 5,
                "direction_id": 69, "direction": "Чат менеджер",
                "eligible_direction_ids": [69], "messages": messages}
        base.update(over)
        return base

    @staticmethod
    def _op(text="ответ"):
        return {"type": "to_client", "text": text}

    @staticmethod
    def _client(text="вопрос"):
        return {"type": "from_client", "text": text}

    @staticmethod
    def _transfer(auto=False):
        text = "Чат передан от Алишер к Бокен."
        if auto:
            text += " Причина — автоназначение чата системой."
        return {"type": "system", "text": text}

    def test_single_operator_request_is_evaluable(self):
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject(
            [self._client(), self._op(), self._op()]))
        self.assertTrue(verdict["ok"], verdict.get("message"))

    def test_auto_assignment_at_the_start_is_not_a_transfer(self):
        """Автоназначение — первичная выдача заявки. Считать его передачей
        значило бы отсечь почти весь пул (1690 строк из 1877 на проде)."""
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject(
            [self._client(), self._transfer(auto=True), self._op(), self._op()]))
        self.assertTrue(verdict["ok"], verdict.get("message"))

    def test_transfer_after_operator_replies_is_rejected(self):
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject(
            [self._client(), self._op(), self._transfer(), self._op(), self._op()]))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_SHARE)
        self.assertIn("несколько сотрудников", verdict["message"])

    def test_transfer_before_any_operator_reply_is_allowed(self):
        """Заявку передали до первого ответа — работа целиком одного человека."""
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject(
            [self._client(), self._transfer(), self._op(), self._op()]))
        self.assertTrue(verdict["ok"], verdict.get("message"))

    def test_too_few_operator_replies_is_rejected(self):
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject([self._client(), self._op()]))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_FEW_MESSAGES)

    def test_unattributed_request_is_rejected(self):
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject(
            [self._client(), self._op(), self._op()], operator_user_id=None))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_NO_OPERATOR)

    def test_foreign_direction_is_rejected(self):
        from call_qa import subjects
        verdict = subjects.eligibility(self._subject(
            [self._client(), self._op(), self._op()], direction_id=70, direction="Основа"))
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_DIRECTION)

    def test_internal_notes_and_system_lines_are_not_operator_replies(self):
        """`comment` — внутренняя заметка оператора, клиент её не видит."""
        from call_qa import subjects
        messages = [self._client(), {"type": "comment", "text": "обед"},
                    {"type": "system", "text": "Чат закрыт."}, self._op()]
        verdict = subjects.eligibility(self._subject(messages))
        self.assertFalse(verdict["ok"], "один ответ оператора — оценивать нечего")
        self.assertEqual(verdict["detail"]["operator_messages"], 1)

    def test_calls_never_ask_about_attribution(self):
        from call_qa import subjects
        for kind in ("call", "imported_call"):
            with self.subTest(subject=kind):
                self.assertTrue(subjects.eligibility({"kind": kind})["ok"])


class ChatApiThresholdTests(unittest.TestCase):
    def test_chatapp_reuses_the_episode_gate_with_its_own_knobs(self):
        """Эпизоды ChatApp построены тем же билдером, что Wazzup, — порог тот же,
        но своей переменной, чтобы ТЭЗ можно было подкрутить, не задев ОП."""
        from call_qa import config, subjects
        self.assertEqual(config.CA_MIN_OPERATOR_SHARE, config.WZ_MIN_OPERATOR_SHARE)
        subject = {"kind": "ca_episode", "id": 1, "episode_kind": "dialog",
                   "operator_user_id": 7, "direction_id": 83, "direction": "ТП линия",
                   "eligible_direction_ids": [83, 84],
                   "operator_share": config.CA_MIN_OPERATOR_SHARE,
                   "human_outbound_count": config.CA_MIN_OPERATOR_MESSAGES,
                   "authors": []}
        self.assertTrue(subjects.eligibility(subject)["ok"])
        subject["operator_share"] = config.CA_MIN_OPERATOR_SHARE - 0.2
        verdict = subjects.eligibility(subject)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"], subjects.REASON_SHARE)
        # Формулировка отказа у ЧУЖОГО направления не должна поминать Верификаторов.
        subject["operator_share"] = config.CA_MIN_OPERATOR_SHARE
        subject["direction_id"] = 999
        self.assertNotIn("Верификатор", subjects.eligibility(subject)["message"])

    def test_tez_chat_criteria_come_from_the_chat_direction(self):
        """Техменеджеры ТЭЗ числятся на «ТП линия», а шкала чата — «ТП чат»."""
        from call_qa import config, subjects
        self.assertEqual(config.CHAT_CRITERIA_DIRECTION_MAP.get(83), 84)
        self.assertEqual(subjects.criteria_direction_id(83), 84)
        # У остальных направлений подмены нет.
        self.assertEqual(subjects.criteria_direction_id(69), 69)
        self.assertIsNone(subjects.criteria_direction_id(None))

    def test_evaluation_uses_the_criteria_direction_for_chats(self):
        api_source = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8")
        self.assertIn("if subject_kind in config.CHAT_SUBJECT_KINDS:\n"
                      "        # Шкала переписки может лежать на ДРУГОМ направлении",
                      api_source)
        self.assertIn("direction_id = subjects_mod.criteria_direction_id(direction_id)",
                      api_source)


class DepartmentScopeTests(unittest.TestCase):
    """Граница отделов: ?department= не должен открывать чужой отдел."""

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

    def _namespace(self, *, role, user_id, headed_codes=(), department_code="",
                   marketing_observer=False):
        from call_qa import config as qa_config

        namespace = {
            "call_qa_config": qa_config,
            "AI_QA_EXTRA_ACCESS_USER_IDS": {183},
            "AI_QA_OP_DEPARTMENT_ID": 367,
            "AI_QA_SUBJECT_DEPARTMENT_CODES": frozenset({"op", "szov", "tez"}),
            "AI_QA_OBSERVER_DEPARTMENT_CODES": frozenset({"marketing"}),
            "AI_QA_OBSERVER_SCOPE_DEPARTMENTS": ("op",),
            "AI_QA_HEAD_DEPARTMENT_CODES": frozenset({"op", "szov", "tez", "marketing"}),
            "db": SimpleNamespace(get_user=lambda id=None: (id, None, "X", role)),
            "jsonify": lambda payload: payload,
            "logging": SimpleNamespace(exception=lambda *a, **k: None),
            "request": SimpleNamespace(args={}, method="GET"),
            "_normalize_user_role": lambda value: value,
            "_is_global_admin_requester": lambda r, uid=None: r in ("super_admin",)
                or (r == "admin" and not headed_codes),
            "_is_marketing_observer": lambda uid, r=None: marketing_observer,
            "_headed_department_codes": lambda uid: list(headed_codes),
            "_department_code_of_user": lambda uid: department_code,
        }
        _load_function(self.api_source, "_ai_qa_department_scope", namespace)
        _load_function(self.api_source, "_ai_qa_requested_department", namespace)
        return namespace

    def test_global_admin_and_super_admin_see_every_department(self):
        for role in ("super_admin", "admin"):
            with self.subTest(role=role):
                ns = self._namespace(role=role, user_id=1)
                self.assertIsNone(ns["_ai_qa_department_scope"](1))

    def test_department_head_sees_only_own_department(self):
        ns = self._namespace(role="admin", user_id=4, headed_codes=("szov",))
        self.assertEqual(ns["_ai_qa_department_scope"](4), ["szov"])

    def test_tez_head_is_admitted(self):
        ns = self._namespace(role="admin", user_id=5, headed_codes=("tez",))
        self.assertEqual(ns["_ai_qa_department_scope"](5), ["tez"])

    def test_supervisor_sees_only_own_department(self):
        for code in ("op", "szov", "tez"):
            with self.subTest(department=code):
                ns = self._namespace(role="sv", user_id=6, department_code=code)
                self.assertEqual(ns["_ai_qa_department_scope"](6), [code])

    def test_supervisor_of_a_department_outside_the_section_gets_nothing(self):
        ns = self._namespace(role="sv", user_id=6, department_code="front_office")
        self.assertEqual(ns["_ai_qa_department_scope"](6), [])

    def test_marketing_observer_keeps_exactly_the_sales_window(self):
        """Периметр наблюдателя не расширяем вместе с разделом — это отдельное
        решение владельца, а не следствие открытия СЗоВ и Тез КЦ."""
        ns = self._namespace(role="marketing_manager", user_id=8,
                             marketing_observer=True)
        self.assertEqual(ns["_ai_qa_department_scope"](8), ["op"])

    def test_foreign_department_in_the_query_is_refused(self):
        ns = self._namespace(role="admin", user_id=4, headed_codes=("szov",))
        department, err = ns["_ai_qa_requested_department"](4, "tez")
        self.assertIsNone(department)
        self.assertIsNotNone(err, "подстановка чужого отдела должна отказывать")
        self.assertEqual(err[1], 403)

    def test_own_department_in_the_query_is_accepted(self):
        ns = self._namespace(role="admin", user_id=4, headed_codes=("szov",))
        self.assertEqual(ns["_ai_qa_requested_department"](4, "SZoV"), ("szov", None))

    def test_unknown_department_is_a_bad_request_not_a_silent_default(self):
        ns = self._namespace(role="super_admin", user_id=1)
        department, err = ns["_ai_qa_requested_department"](1, "hr")
        self.assertIsNone(department)
        self.assertEqual(err[1], 400)

    def test_single_department_viewer_needs_no_explicit_parameter(self):
        ns = self._namespace(role="sv", user_id=6, department_code="tez")
        self.assertEqual(ns["_ai_qa_requested_department"](6, None), ("tez", None))

    def test_supervisor_scope_follows_his_own_department(self):
        """Раньше здесь стоял литерал отдела продаж, и СВ другого отдела получал
        пустой список — то есть пустой раздел вместо отказа."""
        self.assertIn("dept_id = db.get_user_department_id(requester_id)", self.api_source)
        self.assertIn("db.get_supervisor_direction_ids(requester_id, department_id=int(dept_id))",
                      self.api_source)
        self.assertNotIn("get_supervisor_direction_ids(requester_id, department_id=AI_QA_OP_DEPARTMENT_ID)",
                         self.api_source)


class RoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

    def test_dead_binotel_route_is_gone(self):
        """Ручка импортировала call_qa.api.random_binotel_call, которой в
        репозитории никогда не было, и на каждый вызов отдавала 500."""
        self.assertNotIn("from call_qa.api import random_binotel_call", self.api_source)
        self.assertNotIn("'/api/ai-qa/random-binotel-call'", self.api_source)

    def test_pull_call_route_exists_and_reuses_the_journal_machinery(self):
        """Подтяжка из АТС не должна быть второй копией логики Oktell/Binotel:
        у Oktell там ловушка с путём к записи, из-за которой исходящие молча
        терялись."""
        self.assertIn("@app.route('/api/ai-qa/pull-call'", self.api_source)
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef) and item.name == "api_ai_qa_pull_call")
        body = ast.get_source_segment(self.api_source, node)
        self.assertIn("_oktell_random_call(", body)
        self.assertIn("_binotel_random_call(", body)
        self.assertIn("_ai_qa_guard()", body)

    def test_departments_route_feeds_the_selector(self):
        self.assertIn("@app.route('/api/ai-qa/departments'", self.api_source)

    def test_every_scoped_route_resolves_the_department(self):
        """Ручка, забывшая отдел, показала бы главе СЗоВ данные всех отделов."""
        scoped = {
            "api_ai_qa_review_queue", "api_ai_qa_evaluations", "api_ai_qa_stats",
            "api_ai_qa_random_call", "api_ai_qa_random_chat", "api_ai_qa_chat_overview",
        }
        functions = {
            node.name: ast.get_source_segment(self.api_source, node)
            for node in source_cache.parse(self.api_source).body
            if isinstance(node, ast.FunctionDef) and node.name in scoped
        }
        self.assertEqual(set(functions), scoped, "ручки раздела переименованы?")
        for name, body in functions.items():
            with self.subTest(route=name):
                self.assertIn("_ai_qa_requested_department(requester_id)", body)
                self.assertIn("department=department", body)


class WritePathDepartmentTests(unittest.TestCase):
    """У главы отдела ось направлений НЕ ограничена (None = весь свой отдел),
    поэтому на записи отдел надо сверять отдельно — иначе глава СЗоВ правил бы
    шкалу и разборы Тез КЦ, передав чужой direction_id."""

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

    def _guard(self, allowed, direction_department):
        namespace = {
            "logging": SimpleNamespace(exception=lambda *a, **k: None),
            "call_qa_config": SimpleNamespace(connect_ro=lambda: None),
            "_ai_qa_department_scope": lambda uid: allowed,
            "_ai_qa_direction_department_code": lambda did: direction_department,
        }
        return _load_function(self.api_source, "_ai_qa_direction_department_allowed",
                              namespace)

    def test_unrestricted_viewer_passes(self):
        self.assertTrue(self._guard(None, "tez")(1, 84))

    def test_own_department_direction_passes(self):
        self.assertTrue(self._guard(["szov"], "szov")(4, 69))

    def test_foreign_department_direction_is_refused(self):
        self.assertFalse(self._guard(["szov"], "tez")(4, 84))

    def test_direction_without_a_department_is_refused(self):
        """Пустой код отдела — не «разрешить на всякий случай»."""
        self.assertFalse(self._guard(["szov"], "")(4, 999))
        self.assertFalse(self._guard(["szov"], "szov")(4, None))

    def test_viewer_without_departments_is_refused(self):
        self.assertFalse(self._guard([], "szov")(9, 69))

    def test_write_routes_apply_the_guard(self):
        routes = ("api_ai_qa_criteria_config", "api_ai_qa_adjudicate")
        functions = {
            node.name: ast.get_source_segment(self.api_source, node)
            for node in source_cache.parse(self.api_source).body
            if isinstance(node, ast.FunctionDef) and node.name in routes
        }
        self.assertEqual(set(functions), set(routes))
        for name, body in functions.items():
            with self.subTest(route=name):
                self.assertIn("_ai_qa_direction_department_allowed(requester_id", body)

    def test_direction_department_resolves_through_the_live_row(self):
        """Архивная версия шкалы отдел не хранит отдельно — сводим к живой строке."""
        self.assertIn("live.id = COALESCE(d.canonical_id, d.id)", self.api_source)


class SubjectByIdTests(unittest.TestCase):
    """Открытие карточки ПО ID — вторая ось доступа обязательна.

    У главы отдела скоуп по направлениям не ограничен (None), и call_in_scope при
    None пропускает всё: без проверки отдела глава СЗоВ открывал бы карточку
    Тез КЦ, подставив её id в адрес.
    """

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

    def test_card_route_checks_the_department(self):
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef) and item.name == "api_ai_qa_call")
        body = ast.get_source_segment(self.api_source, node)
        self.assertIn("call_in_scope(call_id, scope, subject)", body)
        self.assertIn("_ai_qa_direction_department_allowed(", body)
        self.assertIn("subject_direction_id(call_id, subject)", body)

    def test_subject_direction_is_resolved_per_kind(self):
        from call_qa import api, config
        # Функция обязана уметь все виды: иначе карточка нового вида падала бы
        # на проверке отдела, а не открывалась.
        for kind in config.SUBJECT_KINDS:
            with self.subTest(subject=kind):
                self.assertIn(kind, api._SCOPE_SQL)
        with self.assertRaises(ValueError):
            api.subject_direction_id(1, "nope")


class VerifierChatsPerimeterTests(unittest.TestCase):
    """«Чаты Верификаторов» — раздел ОТДЕЛА ПРОДАЖ, и его периметр не выводится
    из аудитории «ИИ-оценки».

    Вывод был верен, пока в «ИИ-оценке» жил один отдел продаж. С расширением на
    СЗоВ и Тез КЦ он молча отдал бы переписку Wazzup главе Тез КЦ и
    супервайзерам СЗоВ/Тез — у них своя переписка в других разделах.
    """

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.app = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")

    def test_backend_guard_lists_its_own_perimeter(self):
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "_verifier_chats_guard")
        body = ast.get_source_segment(self.api_source, node)
        self.assertNotIn("return _ai_qa_guard()", body)
        self.assertIn("VERIFIER_CHATS_HEAD_DEPARTMENT_CODES", body)
        self.assertIn("_department_code_of_user(requester_id) == 'op'", body)

    def test_wazzup_episode_routes_require_the_sales_department(self):
        """Эпизоды Верификаторов — данные ОП, и по отделу эти ручки не
        фильтруют вовсе. Гарда «ИИ-оценки» после расширения раздела на СЗоВ и
        Тез КЦ туда пропускает их главу и СВ."""
        routes = ("api_wazzup_episodes", "api_wazzup_episode",
                  "api_wazzup_episodes_rebuild")
        functions = {
            node.name: ast.get_source_segment(self.api_source, node)
            for node in source_cache.parse(self.api_source).body
            if isinstance(node, ast.FunctionDef) and node.name in routes
        }
        self.assertEqual(set(functions), set(routes))
        for name, body in functions.items():
            with self.subTest(route=name):
                self.assertIn("_op_episodes_guard()", body)
                self.assertNotIn("_ai_qa_guard()", body)
        guard = next(node for node in source_cache.parse(self.api_source).body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == "_op_episodes_guard")
        guard_body = ast.get_source_segment(self.api_source, guard)
        self.assertIn("_ai_qa_department_scope(requester_id)", guard_body)
        self.assertIn("OP_DEPARTMENT_CODE not in allowed", guard_body)

    def test_perimeter_excludes_tez_on_both_sides(self):
        """Тез КЦ в переписку Верификаторов не входит, маркетинг — входит.

        Глава маркетинга читал её и до правки: это разборы звонков ОП, а он их
        наблюдатель. Рядовой наблюдатель «Маркетинга» вычитается отдельно."""
        self.assertIn(
            "VERIFIER_CHATS_HEAD_DEPARTMENT_CODES = frozenset({'op', 'szov', 'marketing'})",
            self.api_source)
        self.assertIn(
            "const VERIFIER_CHATS_HEAD_DEPARTMENT_CODES = new Set(['op', 'szov', 'marketing']);",
            self.app)
        # Сверяем именно ПРИСВАИВАНИЕ, а не первое упоминание имени: выше него
        # стоит пояснение, в котором Тез КЦ назван законно.
        for source in (self.api_source, self.app):
            marker = "VERIFIER_CHATS_HEAD_DEPARTMENT_CODES = "
            listing = source[source.index(marker):]
            listing = listing[:listing.index(")")]
            self.assertNotIn("tez", listing)


class PullCallTests(unittest.TestCase):
    """Подтяжка звонка из АТС: та же машинерия, что у журнала, но своё окно."""

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.node = next(item for item in source_cache.parse(cls.api_source).body
                        if isinstance(item, ast.FunctionDef)
                        and item.name == "api_ai_qa_pull_call")
        cls.body = ast.get_source_segment(cls.api_source, cls.node)

    def test_period_has_a_default(self):
        """Обе АТС требуют период, а раздел его не спрашивает: без умолчания
        кнопка отвечала бы «Укажите период» ВСЕГДА."""
        self.assertIn("AI_QA_PULL_CALL_DAYS", self.body)
        self.assertIn("ZoneInfo('Asia/Almaty')", self.body)
        self.assertIn("AI_QA_PULL_CALL_DAYS = _env_int('AI_QA_PULL_CALL_DAYS', 7",
                      self.api_source)

    def test_pbx_is_chosen_by_the_operators_own_department(self):
        """Журнал выбирает АТС по оператору; пара «отдел из запроса + чужой
        оператор» ушла бы не в ту АТС."""
        self.assertIn("db.get_user_department(operator_id)", self.body)
        self.assertIn("Оператор не из выбранного отдела", self.body)

    def test_binotel_gets_a_duration_window(self):
        """У Oktell окно берётся из настроек «Деления звонков», у Binotel такого
        источника нет — без умолчания в пул уезжал бы односекундный звонок."""
        self.assertIn("db.get_call_distribution_settings()", self.body)
        self.assertIn("min_duration_sec", self.body)

    def test_operator_pick_skips_chat_only_directions(self):
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "_ai_qa_pick_department_operator")
        body = ast.get_source_segment(self.api_source, node)
        # По МОДЕЛИ, а не по «чатовой семье»: у Тез КЦ «ТП линия» входит в семью
        # (её чат оценивается по шкале «ТП чат»), но её операторы и звонят —
        # вычитание по семье убрало бы 11 из 18 действующих операторов ТЭЗ.
        self.assertIn("<> 'chat_manager'", body)
        # Именно ВЫЗОВА быть не должно — в пояснении имя упоминается законно.
        self.assertNotIn("chat_direction_family(cur", body)
        self.assertIn("NOT IN ('fired', 'dismissal')", body)

    def test_directory_failure_is_not_reported_as_no_operators(self):
        self.assertIn("Справочник сотрудников недоступен", self.body)
        self.assertIn("503", self.body)

    def test_section_pulls_are_distinguishable_in_the_pool(self):
        """Журнальная кнопка и раздел пишут в ОДИН пул imported_calls."""
        self.assertIn("AI_QA_PULL_CALL_SOURCE = 'aiqa'", self.api_source)
        self.assertIn("source=AI_QA_PULL_CALL_SOURCE", self.body)
        self.assertIn('notes=f"{source}:{requester_id}:oktell"', self.api_source)
        self.assertIn('notes=f"{source}:{requester_id}:binotel"', self.api_source)

    def test_journal_endpoint_still_uses_the_extracted_helper(self):
        """Ветку Oktell вынесли из журнальной ручки — она обязана её вызывать."""
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "fetch_random_evaluation_call")
        body = ast.get_source_segment(self.api_source, node)
        self.assertIn("_oktell_random_call(", body)
        self.assertIn("_binotel_random_call(", body)
        # Журнал не должен задавать маркер раздела.
        self.assertNotIn("AI_QA_PULL_CALL_SOURCE", body)

    def test_extracted_helper_has_no_free_variables_from_its_old_caller(self):
        """Вырезанное тело не должно опираться на локальные переменные ручки."""
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "_oktell_random_call")
        body = ast.get_source_segment(self.api_source, node)
        for leaked in ("data.get(", "requester["):
            with self.subTest(name=leaked):
                self.assertNotIn(leaked, body)


class DeleteGuardTests(unittest.TestCase):
    """Удаление звонка из пула не должно уносить субъект оценки ИИ."""

    @classmethod
    def setUpClass(cls):
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

    def test_ai_evaluated_imported_call_cannot_be_deleted(self):
        node = next(item for item in source_cache.parse(self.api_source).body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "delete_draft_evaluation")
        body = ast.get_source_segment(self.api_source, node)
        self.assertIn("ai_review_cache", body)
        self.assertIn("ai_evaluation_runs", body)
        self.assertIn("'imported_call'", body)
        self.assertIn("Звонок оценён ИИ", body)


class ExpiredChatTests(unittest.TestCase):
    """Оценённая переписка обязана открываться и после ретеншна сообщений.

    У эпизода Wazzup есть заморожённый транскрипт, у ChatApp и Chat2Desk — нет.
    Без запаса УЖЕ ОЦЕНЁННАЯ карточка перестала бы открываться навсегда.
    """

    def test_frozen_transcript_is_consulted_before_refusing(self):
        api_source = (ROOT / "call_qa" / "api.py").read_text(encoding="utf-8")
        node = next(item for item in source_cache.parse(api_source).body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "_resolve_wz_episode_source")
        body = ast.get_source_segment(api_source, node)
        self.assertIn("except subjects_mod.SubjectNotEvaluable:", body)
        self.assertIn("runtime_store.get_latest_transcript(", body)
        # Ничего не нашли — отказ остаётся отказом, а не пустой оценкой.
        self.assertIn("raise", body)


class RollingDeployTests(unittest.TestCase):
    def test_conflict_target_mismatch_counts_as_schema_compat(self):
        """Окно раскатки: новый инстанс сменил ключ ai_transcript_cache, старый
        ещё делает ON CONFLICT по прежнему составу колонок -> 42P10. Без этого
        кода запись падала бы 500-й ПОСЛЕ уже оплаченного распознавания."""
        from call_qa.evaluation import runtime_store

        class _Exc(Exception):
            pgcode = "42P10"

        self.assertTrue(runtime_store.is_schema_compat_error(_Exc()))


class SnapshotNormaliserTests(unittest.TestCase):
    """Снапшот и сырые сообщения одного источника обязаны давать одно и то же.

    Иначе ретеншн сообщений сам по себе менял бы транскрипт и балл.
    """

    def _subject(self):
        return {"kind": "ca_episode", "id": 1, "license_id": 72861,
                "messenger_type": "whatsapp", "chat_id": "77001112233",
                "channel_id": "72861:whatsapp", "operator": "Иванов Иван",
                "operator_user_id": 42, "contact_name": "Клиент",
                "messages": [
                    {"id": "m1", "type": "from_client", "text": "вопрос",
                     "created": "2026-09-01T10:00:00"},
                    {"id": "m2", "type": "to_client", "text": "ответ",
                     "created": "2026-09-01T10:01:00", "author": "Иванов Иван"},
                    {"id": "m3", "type": "to_client", "text": "чужая реплика",
                     "created": "2026-09-01T10:02:00", "author": "Петров Пётр"},
                ]}

    def test_chatapp_snapshot_keeps_the_per_message_author(self):
        """Порог атрибуции допускает до 10% ответов ДРУГОГО сотрудника, и без
        автора его реплики подписались бы оцениваемым оператором."""
        from call_qa import subjects

        subject = self._subject()
        messages = subjects._snapshot_messages(
            subject, prefix="ca:72861:whatsapp:77001112233:",
            channel_id=subject["channel_id"], chat_id=subject["chat_id"], authored=True)
        own = [m for m in messages if m["user_id"] == 42]
        foreign = [m for m in messages if m["is_echo"] and m["user_id"] is None]
        self.assertEqual(len(own), 1, "свой ответ ровно один")
        self.assertEqual(len(foreign), 1, "реплика другого сотрудника не своя")
        self.assertEqual(foreign[0]["matched_name"], "Петров Пётр")

    def test_chatapp_snapshot_uses_the_same_attachment_namespace_as_raw(self):
        """Иначе одно вложение расшифровывалось бы дважды, под двумя ключами."""
        from call_qa import subjects

        subject = self._subject()
        messages = subjects._snapshot_messages(
            subject, prefix="ca:72861:whatsapp:77001112233:",
            channel_id=subject["channel_id"], chat_id=subject["chat_id"], authored=True)
        for message in messages:
            with self.subTest(message_id=message["message_id"]):
                self.assertTrue(message["message_id"].startswith("ca:72861:whatsapp:"))

    def test_chat2desk_has_no_author_and_attributes_every_reply_to_the_request_owner(self):
        from call_qa import subjects

        subject = {"kind": "c2d_snapshot", "id": 7, "request_id": 99,
                   "operator": "Сидоров", "operator_user_id": 5,
                   "channel_name": "WhatsApp", "contact_name": "Клиент",
                   "messages": [{"id": 1, "type": "to_client", "text": "ответ",
                                 "created": "2026-09-01T10:00:00"}]}
        messages = subjects._c2d_snapshot_messages(subject)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["user_id"], 5)
        self.assertTrue(messages[0]["message_id"].startswith("c2d:"))


class BatchChatStageTests(unittest.TestCase):
    """Пакетный прогон переписки не должен быть «только Wazzup»."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "call_qa" / "batch_eval.py").read_text(encoding="utf-8")

    def test_chat_stages_take_the_subject_kind(self):
        import inspect

        from call_qa import batch_eval

        for name in ("media_batch_stage", "episode_transcript_stage"):
            with self.subTest(function=name):
                signature = inspect.signature(getattr(batch_eval, name))
                self.assertIn("subject_kind", signature.parameters)
        # Диспетчер отправляет ВСЕ виды переписки в эпизодную стадию: у заявок
        # Chat2Desk и эпизодов ChatApp audio_path=None, и аудио-стадия свалилась
        # бы на скачивании записи.
        self.assertIn("if subject_kind in config.CHAT_SUBJECT_KINDS:", self.source)

    def test_batch_fingerprint_carries_subject_and_department(self):
        """Иначе прогон СЗоВ подписан промптом ЗВОНКА ОТДЕЛА ПРОДАЖ, и открытие
        карточки не нашло бы его — оценило бы заново за деньги."""
        self.assertIn("subject_kind=subject_kind,\n"
                      "                department=_direction_department_code(",
                      self.source)

    def test_imported_call_is_refused_rather_than_silently_wrong(self):
        self.assertIn("пакетная оценка звонков из АТС (imported_call) не поддержана",
                      self.source)


class RetentionTests(unittest.TestCase):
    """Ретеншн не должен уносить субъект уже сделанной оценки ИИ."""

    @classmethod
    def setUpClass(cls):
        cls.database_source = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_ai_evaluated_chat2desk_snapshot_survives_retention(self):
        """Для переписки СЗоВ снапшот — САМ субъект оценки. Его удаление не
        обнулило бы ссылку, как у человеческой оценки, а увело бы оценку из
        очереди и списка целиком: выборки соединяются с таблицей субъекта."""
        node = next(item for item in source_cache.parse(self.database_source).body
                    if isinstance(item, ast.ClassDef))
        cleanup = next(item for item in node.body
                       if isinstance(item, ast.FunctionDef)
                       and item.name == "cleanup_c2d_eval_data")
        body = ast.get_source_segment(self.database_source, cleanup)
        self.assertIn("DELETE FROM c2d_chat_snapshots s", body)
        for table in ("ai_evaluation_runs", "ai_evaluation_meta", "ai_review_cache"):
            with self.subTest(table=table):
                self.assertIn(table, body)
                self.assertIn("'c2d_snapshot'", body)


class BatchSelectionTests(unittest.TestCase):
    """Пакетный прогон — платный, поэтому новый отдел включается ЯВНО."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "call_qa" / "batch_eval.py").read_text(encoding="utf-8")

    def test_department_defaults_to_sales(self):
        from call_qa import batch_eval, config
        import inspect

        for name in ("select_calls", "select_episodes"):
            with self.subTest(function=name):
                signature = inspect.signature(getattr(batch_eval, name))
                self.assertEqual(signature.parameters["department"].default,
                                 config.OP_DEPARTMENT_CODE)
        self.assertIn('default=config.OP_DEPARTMENT_CODE', self.source)

    def test_chat_selection_is_per_source_not_always_wazzup(self):
        """Раньше выборка всегда читала wazzup_episodes, и для СЗоВ и Тез КЦ
        отдавала бы 0 строк — молча, как «нечего оценивать»."""
        self.assertIn("_select_c2d_snapshots", self.source)
        self.assertIn("chatapp_episodes", self.source)
        self.assertIn("config.chat_subject_kind(department)", self.source)

    def test_subject_and_department_must_agree(self):
        self.assertIn("ap.error(f\"у отдела «{department}» источник переписки —", self.source)


class FrontendTests(unittest.TestCase):
    """Фронт узнаёт чат по общему модулю, а не по литералу 'wz_episode'."""

    @classmethod
    def setUpClass(cls):
        cls.dir = ROOT / "src" / "components" / "call_qa"
        cls.app = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")

    def test_shared_module_lists_every_chat_source(self):
        source = (self.dir / "subjects.js").read_text(encoding="utf-8")
        for kind in ("wz_episode", "c2d_snapshot", "ca_episode"):
            self.assertIn(kind, source)
        for department in ("op", "szov", "tez"):
            self.assertIn(f"{department}:", source)

    def test_no_component_compares_against_a_bare_chat_literal(self):
        """Сравнение с одной строкой открывало бы заявку СЗоВ как звонок."""
        for path in sorted(self.dir.glob("*.jsx")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(component=path.name):
                self.assertNotIn("= 'wz_episode'", source)
                self.assertNotIn('subject="wz_episode"', source)

    def test_selector_is_rendered_only_when_more_than_one_department_is_open(self):
        source = (self.dir / "CallQaView.jsx").read_text(encoding="utf-8")
        self.assertIn("/api/ai-qa/departments", source)
        self.assertIn("canSwitchDepartment", source)
        self.assertIn("departmentOptions.length > 1", source)
        # Открытая карточка принадлежит прежнему отделу — её надо закрыть.
        self.assertIn("const changeDepartment", source)

    def test_every_section_request_carries_the_department(self):
        for name in ("CallQaView.jsx", "ChatQueue.jsx", "EvaluationsList.jsx",
                     "QaDashboard.jsx"):
            source = (self.dir / name).read_text(encoding="utf-8")
            with self.subTest(component=name):
                self.assertIn("department ? { department } : {}", source)

    def test_frontend_perimeter_mirrors_the_backend(self):
        self.assertIn("const AI_QA_SUBJECT_DEPARTMENT_CODES = new Set(['op', 'szov', 'tez'])",
                      self.app)
        # СВ трёх отделов — отдельный предикат: исходный переиспользован
        # в «Настройках SIP» и «Касаниях», расширять его нельзя.
        self.assertIn("const isAiQaSupervisor", self.app)
        self.assertIn("isAiQaSupervisor(userLike) ||", self.app)
        # Глобальный админ — ради него и добавлен селектор.
        self.assertIn("(normalizeRole(userLike?.role) === 'admin' && !isDepartmentHead(userLike)) ||",
                      self.app)

    def test_sidebar_item_reaches_supervisors_of_the_new_departments(self):
        """Пункт продублирован в трёх ветвях сайдбара; в «менеджерской» стоит
        сырое условие, а не общий предикат, поэтому СВ СЗоВ и Тез КЦ не увидел
        бы пункта вовсе — только по URL."""
        self.assertGreaterEqual(self.app.count("handleSidebarViewNavigation(e, 'ai_qa')"), 3)
        self.assertIn("(isAiQaDepartmentHead(user) || isAiQaSupervisor(user)) && (", self.app)

    def test_verifier_chats_stay_a_sales_section(self):
        """«Чаты Верификаторов» — переписка Wazzup отдела продаж; СВ СЗоВ и
        Тез КЦ она не нужна, у них свои разделы."""
        self.assertIn("(isAiQaDepartmentHead(user) || isOpSalesSupervisorForAiQa(user)) && (",
                      self.app)


if __name__ == "__main__":
    unittest.main()
