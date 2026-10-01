"""«Такой разбор уже был?» и «разбор сразу в оценку» (01.10.2026).

Проверяется механика без базы и сети: ранжирование похожих правил по измеренным
порогам, привязка дубля к правилу, которое ИИ в этой оценке получил (вместо
нового правила), включение правил в оценку одним снимком, публикация снимка во
ВСЕ ревизии шкалы (у 72 оценки идут по ревизии №2, а «последняя по номеру» —
№3) и объяснение, почему правило до ИИ не дошло.
"""
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from call_qa import api
from call_qa.rag import knowledge, similar

RUN = "84a25693-c571-42bc-87c8-e1cfe3d5f95d"
KEPT = "11111111-1111-4111-8111-111111111111"
PROMPT = "22222222-2222-4222-8222-222222222222"
DUP = "33333333-3333-4333-8333-333333333333"
STARTED = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _rule(rule_id, text, *, status="active", verdict="Correct", **extra):
    row = {"rule_id": rule_id, "rule_status": status, "criterion_id": "d72-greeting",
           "criterion_idx": 0, "criterion_name": "Приветствие", "rule_version_id": 1,
           "rule_text": text, "situation": None, "not_covered": None,
           "correct_verdict": verdict, "excerpt": None,
           "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "author": "СВ",
           "included_count": 0, "corrected_after_count": 0, "confirmed_count": 0}
    row.update(extra)
    return row


def _unit(*values):
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values]


RULE_TEXT = "Оператор не обязан уточнять удобно ли разговаривать"


def _found(rule_id, *, verdict="duplicate", status="active", correct="Correct", score=0.99,
           text=RULE_TEXT):
    return {"rule_id": rule_id, "verdict": verdict, "rule_status": status,
            "correct_verdict": correct, "score": score, "same_verdict": True,
            "rule_text": text}


class RankTests(unittest.TestCase):
    def test_labels_follow_measured_thresholds(self):
        query = _unit(1, 0, 0)
        rules = [_rule("dup", "правило один"), _rule("near", "правило два"),
                 _rule("far", "правило три")]
        vectors = {similar.text_hash("правило один"): _unit(1, 0.2, 0),     # 0.981
                   similar.text_hash("правило два"): _unit(1, 0.45, 0),     # 0.912
                   similar.text_hash("правило три"): _unit(1, 0.7, 0)}      # 0.819
        found = similar.rank(rules, text="Правило один.", vector=query,
                             vectors_by_hash=vectors)
        self.assertEqual([(f["rule_id"], f["verdict"]) for f in found],
                         [("dup", "duplicate"), ("near", "similar")])
        self.assertEqual(found[0]["found_by"], ["смысл"])

    def test_score_thresholds(self):
        # По смыслу 0,97 — граница; дальше «дубль» решает формулировка (ниже).
        self.assertEqual(similar.label(0.9721), "duplicate")
        self.assertEqual(similar.label(0.9696), "similar")
        self.assertIsNone(similar.label(0.899))

    def test_close_meaning_in_other_words_is_only_similar(self):
        # Сверка с решениями владельца 01.10.2026: векторы не различают силу
        # правила. «Не должен проговаривать» — запрет, «не обязан» — не требуется
        # (0,974–0,982 по смыслу); перестановка слов — настоящая копия (0,989).
        query = _unit(1, 0, 0)
        prohibition = "Во время разговора оператор не должен проговаривать имя водителя"
        cases = [
            ("В течение разговора оператор не обязан уточнять имя водителя или проговаривать",
             "similar"),
            ("Оператор не обязан во время разговора проговаривать имя водителя", "similar"),
            ("Оператор не должен во время разговора проговаривать имя водителя", "duplicate"),
        ]
        for rule_text, expected in cases:
            with self.subTest(rule_text=rule_text):
                vectors = {similar.text_hash(rule_text): _unit(1, 0.12, 0)}    # 0.993
                found = similar.rank([_rule("r", rule_text)], text=prohibition,
                                     vector=query, vectors_by_hash=vectors)
                self.assertEqual(found[0]["verdict"], expected)

    def test_different_numbers_are_never_the_same_wording(self):
        # «4%» и «5%» — одно число среди многих слов почти не трогает долю общих
        # слов, но это другое правило (разбор подтвердил: такие числа есть в 74 и 83).
        query = _unit(1, 0, 0)
        old = "Считай верным: оператор сказал, что ранее комиссия составляла 4% от заказа"
        new = "Считай верным: оператор сказал, что ранее комиссия составляла 5% от заказа"
        self.assertFalse(similar.same_wording(old, new))
        self.assertTrue(similar.same_wording(old, old.replace("4%", "4 %")))
        self.assertTrue(similar.same_wording("ответил за 1,5 минуты", "ответил за 1.5 минуты"))
        semantic = similar.rank([_rule("r", old)], text=new, vector=query,
                                vectors_by_hash={similar.text_hash(old): _unit(1, 0.05, 0)})
        lexical = similar.rank([_rule("r", old)], text=new, vector=None)
        self.assertEqual((semantic[0]["verdict"], lexical[0]["verdict"]), ("similar", "similar"))

    def test_verbatim_copy_is_duplicate_without_vectors(self):
        text = "Оператор не обязан уточнять удобно ли разговаривать водителю"
        found = similar.rank([_rule("copy", text)], text=text.lower() + ".", vector=None)
        self.assertEqual(found[0]["verdict"], "duplicate")
        self.assertEqual(found[0]["found_by"], ["слова"])

    def test_negation_keeps_lexical_match_below_duplicate(self):
        # «Уточнил» и «даже если НЕ уточнил» — 7 из 8 слов общие, но правило обратное.
        found = similar.rank([_rule("r", "Считай верным, если оператор уточнил имя водителя")],
                             text="Считай верным, даже если оператор не уточнил имя водителя",
                             vector=None)
        self.assertEqual(found[0]["verdict"], "similar")

    def test_unrelated_wording_is_not_lexical_duplicate(self):
        found = similar.rank([_rule("r", "Оператор должен представляться по имени")],
                             text="Не требуй уточнять удобно ли разговаривать", vector=None)
        self.assertEqual(found, [])

    def test_excluded_rules_and_verdict_flag(self):
        text = "Оператор не обязан уточнять удобно ли разговаривать"
        rules = [_rule("same", text), _rule("other", text, verdict="Incorrect"),
                 _rule("skip", text)]
        found = similar.rank(rules, text=text, vector=None, correct_verdict="Correct",
                             exclude_rule_ids=["skip"])
        flags = {f["rule_id"]: f["same_verdict"] for f in found}
        self.assertEqual(flags, {"same": True, "other": False})

    def test_active_duplicate_goes_before_draft_duplicates(self):
        text = "Оператор не обязан уточнять удобно ли разговаривать"
        rules = [_rule("d1", text, status="draft"), _rule("d2", text, status="draft"),
                 _rule("live", text)]
        found = similar.rank(rules, text=text, vector=None)
        self.assertEqual(found[0]["rule_id"], "live")

    def test_duplicate_target_requires_active_rule_with_same_verdict(self):
        base = _found("a")
        self.assertEqual(similar.duplicate_target({"items": [base]},
                                                  correct_verdict="Correct")["rule_id"], "a")
        for change in ({"rule_status": "draft"}, {"correct_verdict": "Incorrect"},
                       {"verdict": "similar"}):
            with self.subTest(change=change):
                self.assertIsNone(similar.duplicate_target(
                    {"items": [{**base, **change}]}, correct_verdict="Correct"))

    def test_duplicate_links_only_to_rule_the_model_received(self):
        found = {"items": [_found("a")]}
        self.assertIsNone(similar.duplicate_target(found, correct_verdict="Correct",
                                                   linkable_ids=[]))
        self.assertIsNone(similar.duplicate_target(found, correct_verdict="Correct",
                                                   linkable_ids=["b"]))
        self.assertEqual(similar.duplicate_target(found, correct_verdict="Correct",
                                                  linkable_ids=["a"])["rule_id"], "a")

    def test_link_target_is_always_shown_and_first(self):
        items = [_found(str(i), status="draft") for i in range(5)]
        shown = similar.shown_items({"items": items}, target=items[4])
        self.assertEqual([item["rule_id"] for item in shown], ["4", "0", "1"])
        summary = similar.summary({"items": items}, target=items[4])
        self.assertEqual(len(summary), similar.SHOW_LIMIT)
        self.assertEqual(summary[0]["rule_id"], "4")
        self.assertEqual(set(summary[0]), {"rule_id", "score", "verdict", "status",
                                           "same_verdict"})


class _Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self._current = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        if sql.strip().startswith("SET"):
            self._current = []
            return
        self._current = self.results.pop(0) if self.results else []

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        return self._current[0] if self._current else None


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class FindSimilarTests(unittest.TestCase):
    TEXT = "Оператор не обязан уточнять удобно ли разговаривать"

    def setUp(self):
        similar._QUERY_CACHE.clear()

    def _candidate_row(self, rule_id, text, status="active"):
        return (rule_id, status, "d72-greeting", 0, "Приветствие", 1, text, None, None,
                "Correct", None, datetime(2026, 10, 1), "СВ", 3, 1, 0)

    def _find(self, cursor, **kwargs):
        with patch.object(similar.config, "connect_ro", return_value=_Conn(cursor)):
            return similar.find_similar(direction_id=72, criterion_id="d72-greeting", **kwargs)

    def test_missing_rule_vectors_are_embedded_once_and_cached(self):
        cursor = _Cursor([[self._candidate_row("a", self.TEXT)], [(7,)], []])
        stored = {}
        with patch.object(similar, "embed_texts",
                          return_value=[_unit(1, 0, 0), _unit(1, 0.1, 0)]) as embed, \
             patch.object(similar, "store_vectors",
                          side_effect=lambda model, vectors: stored.update(vectors)):
            found = self._find(cursor, text="Оператор не обязан уточнять, удобно ли разговаривать",
                               correct_verdict="Correct")
        # Одним вызовом: текст правила без вектора + текст проверяющего.
        self.assertEqual(len(embed.call_args.args[0]), 2)
        self.assertEqual(list(stored), [similar.text_hash(self.TEXT)])
        self.assertEqual(found["items"][0]["rule_id"], "a")
        self.assertEqual(found["items"][0]["verdict"], "duplicate")
        self.assertFalse(found["degraded"])

    def test_query_vector_of_same_text_is_not_requested_twice(self):
        stored_row = [(similar.text_hash(self.TEXT), "[1,0,0]")]
        cursor = _Cursor([[self._candidate_row("a", self.TEXT)], [(7,)], stored_row,
                          [self._candidate_row("a", self.TEXT)], [(7,)], stored_row])
        with patch.object(similar, "embed_texts", return_value=[_unit(1, 0, 0)]) as embed, \
             patch.object(similar, "store_vectors"):
            first = self._find(cursor, text=self.TEXT)
            second = self._find(cursor, text=self.TEXT)
        embed.assert_called_once()
        self.assertEqual(first["items"], second["items"])

    def test_provider_failure_degrades_to_words_and_does_not_raise(self):
        cursor = _Cursor([[self._candidate_row("a", self.TEXT)], [(7,)], []])
        with patch.object(similar, "embed_texts", side_effect=RuntimeError("429")), \
             patch.object(similar, "store_vectors") as store:
            found = self._find(cursor, text=self.TEXT)
        self.assertTrue(found["degraded"])
        self.assertTrue(found["provider_failed"])      # сохранение не пойдёт к нему снова
        self.assertEqual(found["items"][0]["found_by"], ["слова"])
        store.assert_not_called()

    def test_given_vector_survives_provider_failure(self):
        # Вектор текста уже посчитан (сохранение); упал только досчёт векторов
        # других правил — сравнение с сохранёнными идёт по смыслу, а не по словам.
        other = "Оператор должен назвать таксопарк"
        cursor = _Cursor([[self._candidate_row("a", self.TEXT), self._candidate_row("b", other)],
                          [(7,)], [(similar.text_hash(self.TEXT), "[1,0,0]")]])
        with patch.object(similar, "embed_texts", side_effect=RuntimeError("429")), \
             patch.object(similar, "store_vectors"):
            found = self._find(cursor, text=self.TEXT, vector=_unit(1, 0, 0))
        self.assertEqual(found["items"][0]["rule_id"], "a")
        self.assertEqual(found["items"][0]["found_by"], ["смысл"])
        self.assertTrue(found["degraded"])          # «b» сравнили только по словам

    def test_embed_false_never_calls_provider(self):
        cursor = _Cursor([[self._candidate_row("a", self.TEXT)], [(7,)], []])
        with patch.object(similar, "embed_texts", side_effect=AssertionError("provider")), \
             patch.object(similar, "store_vectors"):
            found = self._find(cursor, text=self.TEXT, embed=False)
        self.assertEqual(found["items"][0]["verdict"], "duplicate")
        self.assertTrue(found["degraded"])
        self.assertFalse(found["provider_failed"])

    def test_missing_vector_table_before_migration_still_answers(self):
        class UndefinedTable(Exception):
            pgcode = "42P01"

        cursor = _Cursor([[self._candidate_row("a", self.TEXT)], [(7,)]])
        original = cursor.execute

        def execute(sql, params=None):
            if "qa_rule_text_embeddings" in sql:
                raise UndefinedTable("relation does not exist")
            return original(sql, params)

        cursor.execute = execute
        with patch.object(similar, "embed_texts",
                          return_value=[_unit(1, 0, 0), _unit(1, 0, 0)]), \
             patch.object(similar, "store_vectors") as store:
            found = self._find(cursor, text=self.TEXT)
        self.assertEqual(found["items"][0]["verdict"], "duplicate")
        # Кэш без таблицы не пишем: model_id сброшен.
        self.assertIsNone(store.call_args.args[0])

    def test_short_text_does_not_touch_database(self):
        with patch.object(similar.config, "connect_ro",
                          side_effect=AssertionError("db used")):
            found = similar.find_similar(direction_id=72, criterion_id="x", text="коротко")
        self.assertEqual(found["items"], [])

    def test_candidates_are_live_rules_of_one_criterion(self):
        sql = " ".join(similar._CANDIDATES_SQL.split())
        self.assertIn("r.direction_id = %s AND r.criterion_id = %s", sql)
        self.assertIn("r.rule_status IN ('active', 'draft', 'quarantined')", sql)


class SnapshotPublicationTests(unittest.TestCase):
    def test_snapshot_goes_to_every_revision_with_state_requested_first(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = [(6,), (5,)]      # по номеру: №3 (id 6), №2 (id 5)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        published = []
        with patch.object(knowledge, "create_knowledge_snapshot",
                          side_effect=lambda conn, **kw: published.append(
                              kw["scale_revision_id"]) or {"id": kw["scale_revision_id"]}):
            snapshot = knowledge.publish_direction_snapshots(
                conn, direction_id=72, reason="r", scale_revision_id=5)
        self.assertEqual(published, [5, 6])
        self.assertEqual(snapshot, {"id": 5})

    def test_without_state_falls_back_to_latest_revision_or_fails_closed(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with self.assertRaises(knowledge.KnowledgeValidationError):
            knowledge.publish_direction_snapshots(conn, direction_id=72, reason="r")

    def test_activate_rules_publishes_once_for_live_scale(self):
        transitions = []
        with patch.object(knowledge, "ensure_knowledge_context",
                          return_value={"scale_revision_id": 5}) as ctx, \
             patch.object(knowledge, "transition_policy_rule",
                          side_effect=lambda conn, **kw: transitions.append(kw)), \
             patch.object(knowledge, "release_auto_activation") as release, \
             patch.object(knowledge, "publish_direction_snapshots",
                          return_value={"id": 1}) as publish:
            result = knowledge.activate_rules(MagicMock(), direction={"id": 72},
                                              rule_ids=["a", "b", "a", None], actor_id=2,
                                              reason="r")
        ctx.assert_called_once()
        self.assertEqual([t["rule_id"] for t in transitions], ["a", "b"])
        self.assertTrue(all(t["to_status"] == "active" and t["expected_status"] == "draft"
                            and t["publish_snapshot"] is False and t["scale_revision_id"] == 5
                            for t in transitions))
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs["scale_revision_id"], 5)
        # Отметка самовключения снимается с включённых: выключит администратор —
        # само правило больше не включится.
        self.assertEqual(release.call_args.kwargs["rule_ids"], ["a", "b"])
        self.assertEqual(result, {"snapshot": {"id": 1}, "activated": ["a", "b"]})

    def test_activate_rules_without_ids_is_noop(self):
        with patch.object(knowledge, "ensure_knowledge_context",
                          side_effect=AssertionError("db used")):
            self.assertIsNone(knowledge.activate_rules(
                MagicMock(), direction={"id": 72}, rule_ids=[], reason="r"))

    def test_one_stuck_rule_does_not_block_the_batch(self):
        def transition(conn, **kw):
            if kw["rule_id"] == "b":
                raise knowledge.KnowledgeValidationError("no ready embedding")

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)          # отметка самовключения на месте
        with patch.object(knowledge, "ensure_knowledge_context",
                          return_value={"scale_revision_id": 5}), \
             patch.object(knowledge, "transition_policy_rule", side_effect=transition), \
             patch.object(knowledge, "release_auto_activation"), \
             patch.object(knowledge, "publish_direction_snapshots",
                          return_value={"id": 1}) as publish:
            result = knowledge.activate_rules(conn, direction={"id": 72},
                                              rule_ids=["a", "b", "c"], reason="r",
                                              auto=True)
        self.assertEqual(result["activated"], ["a", "c"])
        publish.assert_called_once()
        statements = [" ".join(call.args[0].split()) for call in cursor.execute.call_args_list]
        self.assertIn("ROLLBACK TO SAVEPOINT activate_rule", statements)
        self.assertEqual(statements.count("SAVEPOINT activate_rule"), 3)

    def test_rule_released_by_admin_meanwhile_is_not_activated(self):
        # Список «ждущих» прочитан раньше; админ успел взять правило в руки и
        # снять отметку — под блокировкой строки её уже нет, правило не трогаем.
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(False,), (True,)]
        with patch.object(knowledge, "ensure_knowledge_context",
                          return_value={"scale_revision_id": 5}), \
             patch.object(knowledge, "transition_policy_rule") as transition, \
             patch.object(knowledge, "release_auto_activation"), \
             patch.object(knowledge, "publish_direction_snapshots", return_value={"id": 1}):
            result = knowledge.activate_rules(conn, direction={"id": 72},
                                              rule_ids=["released", "fresh"], reason="r",
                                              auto=True)
        self.assertEqual(result["activated"], ["fresh"])
        self.assertEqual([c.kwargs["rule_id"] for c in transition.call_args_list], ["fresh"])
        flag_sql = " ".join(cursor.execute.call_args_list[1].args[0].split())
        self.assertIn("metadata ? 'auto_activate'", flag_sql)
        self.assertIn("FOR UPDATE", flag_sql)

    def test_strict_activation_still_fails_whole_batch(self):
        with patch.object(knowledge, "ensure_knowledge_context",
                          return_value={"scale_revision_id": 5}), \
             patch.object(knowledge, "transition_policy_rule",
                          side_effect=knowledge.KnowledgeConflict("changed")), \
             self.assertRaises(knowledge.KnowledgeConflict):
            knowledge.activate_rules(MagicMock(), direction={"id": 72}, rule_ids=["a"],
                                     reason="r")

    def test_nothing_activated_publishes_nothing(self):
        with patch.object(knowledge, "ensure_knowledge_context",
                          return_value={"scale_revision_id": 5}), \
             patch.object(knowledge, "transition_policy_rule",
                          side_effect=knowledge.KnowledgeConflict("changed")), \
             patch.object(knowledge, "publish_direction_snapshots") as publish:
            self.assertIsNone(knowledge.activate_rules(
                MagicMock(), direction={"id": 72}, rule_ids=["a"], reason="r",
                auto=True))
        publish.assert_not_called()

    def test_release_auto_activation_only_touches_marked_rules(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        knowledge.release_auto_activation(conn, rule_ids=[KEPT, None])
        sql = " ".join(cursor.execute.call_args.args[0].split())
        self.assertIn("SET metadata = metadata - 'auto_activate'", sql)
        self.assertIn("metadata ? 'auto_activate'", sql)
        self.assertEqual(cursor.execute.call_args.args[1], ([KEPT],))

    def test_no_code_path_publishes_to_latest_revision_by_number(self):
        # «ORDER BY scale_revision DESC» остался только в аварийной ветке
        # publish_direction_snapshots (ревизий с состоянием нет вовсе).
        import inspect
        sources = inspect.getsource(knowledge) + inspect.getsource(api.reindex_adjudication)
        self.assertEqual(sources.count("ORDER BY scale_revision DESC LIMIT 1"), 1)


class _TxConn:
    """Транзакция сохранения: FOR SHARE отвечает нынешним состоянием цели привязки."""

    def __init__(self, target_status="active", target_text=RULE_TEXT, target_verdict="Correct"):
        self.target = (target_status, target_text, target_verdict)
        self.events = []
        self.cursors = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        state = {"sql": ""}

        def execute(sql, params=None):
            state["sql"] = sql
            if "FOR SHARE" in sql:
                self.events.append("lock-target")

        cursor.execute.side_effect = execute
        cursor.fetchone.side_effect = lambda: (
            self.target if "FOR SHARE" in state["sql"] else None)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        pass


MANUAL = {"mode": "active", "approval": {"valid": True, "manual": True}}


class SaveDuplicateTests(unittest.TestCase):
    def _payload(self, included=True):
        return {"id": 10, "direction_id": 72, "_evaluation_run_id": "run",
                "_scale_revision_id": 5, "_evaluation_model": "m", "_transcript_hash": "h",
                "_authoritative_transcript_text": "текст", "transcript": [],
                "_retrieval_trace": {"status": "ok", "candidates": [
                    {"criterion_id": "d72-greeting", "rule_id": KEPT, "included": included},
                    {"criterion_id": "other", "rule_id": DUP, "included": True}]}}

    def _item(self, reason=RULE_TEXT, criterion_id="d72-greeting"):
        return {"criterion_id": criterion_id, "criterion_idx": 0,
                "criterion_name": "Приветствие", "ai_verdict": "Incorrect",
                "correct_verdict": "Correct", "reason": reason, "situation": None,
                "not_covered": None, "excerpt": "", "excerpt_verified": False,
                "evidence_status": "no_evidence", "excerpt_start": None, "excerpt_end": None}

    def _save(self, found, *, payload=None, conn=None, rollout=MANUAL, provider=None,
              activated=frozenset({"new-rule"}), items=None):
        create_case = MagicMock(return_value="case")
        create_rule = MagicMock(return_value={"rule_id": "new-rule", "rule_version_id": 3})
        activate = MagicMock(return_value=set(activated))
        items = items or [self._item()]
        if provider is None:
            provider = MagicMock(metadata={"provider": "vertex", "model": "m", "dim": 2})
            provider.embed_document.return_value = [[0.1, 0.2]] * len(items) + [[0.3, 0.4]] * len(items)
        self.conn = conn or _TxConn()
        self.queue = MagicMock()
        self.index_error = MagicMock()
        model_row = MagicMock(side_effect=lambda *a, **k: self.conn.events.append("model-row") or 4)
        find_kwargs = ({"side_effect": found} if isinstance(found, list)
                       else {"return_value": found})
        with patch.object(api, "_validated_adjudication_items",
                          return_value=(payload or self._payload(), items)), \
             patch.object(api.config, "connect_rw", return_value=self.conn), \
             patch("call_qa.embeddings.provider.get_provider", return_value=provider), \
             patch("call_qa.rag.similar.find_similar", **find_kwargs) as find, \
             patch("call_qa.rag.similar.save_vector") as save_vector, \
             patch.object(knowledge, "create_adjudication_case", create_case), \
             patch.object(knowledge, "create_draft_policy_rule", create_rule), \
             patch.object(knowledge, "record_rule_embedding"), \
             patch.object(knowledge, "mark_rule_index_error", self.index_error), \
             patch.object(knowledge, "ensure_embedding_model", model_row), \
             patch.object(api, "_activate_review_rules", activate), \
             patch.object(api, "_rag_rollout", return_value=rollout), \
             patch.object(api, "queue_reindex_adjudication", self.queue), \
             patch.object(api, "_claim_review_outcome"), \
             patch.object(api, "_record_rule_review_feedback"):
            result = api._save_adjudications_locked(
                10, 72, [{}], evaluation_run_id="run", scale_revision_id=5)
        return result, create_case, create_rule, activate, find, save_vector

    def test_duplicate_of_rule_in_this_runs_prompt_is_linked_not_created(self):
        found = {"items": [_found(KEPT, score=0.99)], "degraded": False}
        result, create_case, create_rule, activate, find, _ = self._save(found)
        create_rule.assert_not_called()
        metadata = create_case.call_args.kwargs["metadata"]
        self.assertEqual(metadata["duplicate_of_rule_id"], KEPT)
        self.assertEqual(metadata["similar"][0]["rule_id"], KEPT)
        self.assertEqual((result["saved"], result["linked"], result["activated"]), (1, 1, 0))
        self.assertEqual(result["rules"][0]["status"], "linked")
        self.assertEqual((result["rag_mode"], result["auto_activation"]), ("active", True))
        # Вектор текста правила посчитан при сохранении и передан поиску дублей.
        self.assertEqual(find.call_args.kwargs["vector"], [0.3, 0.4])
        self.assertTrue(find.call_args.kwargs["embed"])
        # Разбор с исправлением подбирает застрявшие черновики и без своих правил.
        self.assertEqual(activate.call_args.args[:2], (72, []))

    def test_link_target_is_locked_before_the_embedding_model_row(self):
        # Правка правила админом берёт строку правила, потом строку модели; сохранение
        # разбора — в том же порядке, иначе встречные ожидания (deadlock).
        found = [{"items": [_found(KEPT)], "degraded": False}, {"items": [], "degraded": False}]
        result, *_ = self._save(found, items=[
            self._item(), self._item(criterion_id="d72-other", reason="Другое правило")])
        self.assertEqual(self.conn.events[:2], ["lock-target", "model-row"])
        self.assertEqual(result["linked"], 1)

    def test_rule_rewritten_meanwhile_is_not_linked(self):
        # Сравнивали со старым текстом; админ успел переписать правило — привязка
        # к новому тексту потеряла бы исправление проверяющего.
        found = {"items": [_found(KEPT)], "degraded": False}
        for conn in (_TxConn(target_text="Совсем другое правило"),
                     _TxConn(target_verdict="Incorrect")):
            with self.subTest(target=conn.target):
                _, create_case, create_rule, _, _, _ = self._save(found, conn=conn)
                create_rule.assert_called_once()
                self.assertNotIn("duplicate_of_rule_id",
                                 create_case.call_args.kwargs["metadata"])

    def test_provider_failure_in_one_check_spares_the_rest(self):
        found = [{"items": [], "degraded": True, "provider_failed": True},
                 {"items": [], "degraded": True}]
        _, _, _, _, find, _ = self._save(
            found, items=[self._item(), self._item(criterion_id="d72-other")])
        self.assertEqual([c.kwargs["embed"] for c in find.call_args_list], [True, False])

    def test_duplicate_the_model_did_not_receive_becomes_new_rule(self):
        # Правило не дошло до промпта на этом разговоре — привязка оставила бы ИИ
        # без правила для таких разговоров; нужно новое, из этого разговора.
        found = {"items": [_found(KEPT)], "degraded": False}
        result, create_case, create_rule, _, _, _ = self._save(
            found, payload=self._payload(included=False))
        create_rule.assert_called_once()
        metadata = create_case.call_args.kwargs["metadata"]
        self.assertNotIn("duplicate_of_rule_id", metadata)
        self.assertEqual(metadata["similar"][0]["rule_id"], KEPT)   # находка видна
        self.assertEqual(result["linked"], 0)

    def test_rule_switched_off_meanwhile_is_not_linked(self):
        found = {"items": [_found(KEPT)], "degraded": False}
        _, create_case, create_rule, _, _, _ = self._save(
            found, conn=_TxConn(target_status="deprecated"))
        create_rule.assert_called_once()
        self.assertNotIn("duplicate_of_rule_id", create_case.call_args.kwargs["metadata"])

    def test_new_rule_is_activated_and_its_text_vector_cached(self):
        result, create_case, create_rule, activate, _, save_vector = self._save(
            {"items": [], "degraded": False})
        create_rule.assert_called_once()
        self.assertTrue(create_rule.call_args.kwargs["metadata"]["auto_activate"])
        self.assertNotIn("duplicate_of_rule_id", create_case.call_args.kwargs["metadata"])
        self.assertEqual(activate.call_args.args[:2], (72, ["new-rule"]))
        save_vector.assert_called_once()
        self.assertEqual((result["saved"], result["activated"], result["linked"]), (1, 1, 0))
        self.assertEqual(result["rules"][0]["status"], "active")

    def test_similar_draft_or_other_verdict_still_creates_rule(self):
        for change in ({"rule_status": "draft"}, {"correct_verdict": "Incorrect"}):
            with self.subTest(change=change):
                found = {"items": [{**_found(KEPT), **change}]}
                _, _, create_rule, _, _, _ = self._save(found)
                create_rule.assert_called_once()

    def test_embedding_outage_does_not_call_provider_again(self):
        provider = MagicMock(metadata={"provider": "vertex", "model": "m", "dim": 2})
        provider.embed_document.side_effect = RuntimeError("Vertex 503")
        result, _, create_rule, activate, find, _ = self._save(
            {"items": [], "degraded": True}, provider=provider, activated=frozenset())
        self.assertFalse(find.call_args.kwargs["embed"])
        self.assertIsNone(find.call_args.kwargs["vector"])
        create_rule.assert_called_once()
        self.index_error.assert_called_once()
        self.queue.assert_called_once_with("new-rule", actor_id=None)
        self.assertEqual(activate.call_args.args[1], [])
        self.assertEqual((result["activated"], result["pending_index"]), (0, 1))

    def test_experiment_approved_direction_keeps_rules_as_drafts(self):
        rollout = {"mode": "active", "approval": {"valid": True}}
        result, _, create_rule, activate, _, _ = self._save(
            {"items": [], "degraded": False}, rollout=rollout)
        create_rule.assert_called_once()
        activate.assert_not_called()
        self.assertEqual((result["activated"], result["rules"][0]["status"]), (0, "draft"))
        self.assertFalse(result["auto_activation"])

    def test_failed_activation_keeps_saved_review_as_draft(self):
        with patch.object(api.criteria_mod, "load_direction", side_effect=RuntimeError("db")):
            self.assertEqual(api._activate_review_rules(72, ["r"], 2), set())
            self.assertEqual(api._activate_review_rules(72, [], 2), set())


class ActivationTests(unittest.TestCase):
    def test_activation_voids_only_experiment_approval(self):
        cases = [
            ({"mode": "active", "approval": {"valid": True}}, True),
            ({"mode": "canary", "approval": {"valid": True}}, True),
            ({"mode": "active", "approval": {"valid": True, "manual": True}}, False),
            ({"mode": "shadow", "approval": {"valid": True}}, False),
            ({"mode": "active", "approval": {"valid": False}}, False),
        ]
        for rollout, expected in cases:
            with self.subTest(rollout=rollout):
                self.assertIs(api._activation_voids_approval(rollout), expected)

    def _activate(self, rule_ids, *, pending, active_now, activated=None):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.side_effect = [pending, active_now]
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value = cursor
        contract = {"provider": "vertex", "model": "m", "dim": 768, "config_hash": "c" * 64}
        with patch.object(api.criteria_mod, "load_direction", return_value={"id": 72}), \
             patch.object(api.cc, "apply_to_direction"), \
             patch("call_qa.embeddings.provider.configured_contract", return_value=contract), \
             patch.object(api.config, "connect_rw", return_value=conn), \
             patch.object(knowledge, "activate_rules", return_value=activated) as act:
            result = api._activate_review_rules(72, rule_ids, 2)
        return result, act, cursor

    def test_review_activation_picks_up_drafts_stuck_earlier(self):
        result, act, _ = self._activate(
            ["new"], pending=[("stuck",)], active_now=[("new",)],
            activated={"snapshot": {}, "activated": ["new", "stuck"]})
        self.assertEqual(result, {"new"})                 # о своих правилах
        self.assertEqual(act.call_args.kwargs["rule_ids"], ["new", "stuck"])
        self.assertTrue(act.call_args.kwargs["auto"])
        sql = " ".join(api._PENDING_AUTO_RULES_SQL.split())
        self.assertIn("r.rule_status = 'draft'", sql)
        self.assertIn("r.metadata->>'auto_activate' = 'true'", sql)
        self.assertIn("e.index_status = 'ready'", sql)

    def test_own_rule_switched_on_by_a_neighbour_is_reported_active(self):
        # Соседний разбор направления успел подобрать и включить наше правило:
        # в нашей пачке оно — конфликт, но в базе действует, и сказать надо это.
        result, _, cursor = self._activate(["new"], pending=[], active_now=[("new",)],
                                           activated=None)
        self.assertEqual(result, {"new"})
        sql = " ".join(cursor.execute.call_args.args[0].split())
        self.assertIn("rule_status = 'active'", sql)

    def test_sweep_runs_without_own_ready_rules(self):
        result, act, _ = self._activate([], pending=[("stuck",)], active_now=[],
                                        activated={"snapshot": {}, "activated": ["stuck"]})
        self.assertEqual(act.call_args.kwargs["rule_ids"], ["stuck"])
        self.assertEqual(result, set())

    def test_admin_lifecycle_change_releases_auto_activation(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (5,)
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value = cursor
        source = {"rule_version_id": 3, "content_hash": "h", "rule_status": "active",
                  "direction_id": 72}
        with patch.object(api, "_canonical_rule_source", return_value=source), \
             patch.object(api.config, "connect_rw", return_value=conn), \
             patch.object(knowledge, "release_auto_activation") as release, \
             patch.object(knowledge, "transition_policy_rule") as transition:
            self.assertTrue(api.update_adjudication(KEPT, {
                "rule_status": "draft", "expected_rule_version_id": 3,
                "expected_content_hash": "h"}, actor_id=2))
        self.assertEqual(release.call_args.kwargs["rule_ids"], [KEPT])
        self.assertEqual(transition.call_args.kwargs["to_status"], "draft")

    def test_card_gets_the_directions_mode_now(self):
        # Режим в прогоне застыл (у пакетной оценки — «batch»), обещание «ИИ учтёт»
        # карточка строит по сегодняшнему режиму направления.
        self.assertEqual(api._rag_now({"mode": "active", "approval": {"valid": True, "manual": True}}),
                         {"mode": "active", "auto_activation": True})
        self.assertEqual(api._rag_now({"mode": "active", "approval": {"valid": True}}),
                         {"mode": "active", "auto_activation": False})
        import inspect
        self.assertEqual(inspect.getsource(api._evaluate_and_cache).count("_rag_now(rollout)"), 2)


class RunStateTests(unittest.TestCase):
    state = dict(candidates={"in": {"included": True, "similarity": 0.81},
                             "low": {"included": False, "similarity": 0.67996,
                                     "reject_reason": "below_threshold"},
                             "top": {"included": False, "similarity": 0.75,
                                     "reject_reason": "top_k_exceeded"}},
                 in_snapshot={"in", "low", "top", "far"}, rag_enabled=True,
                 trace_status="ok",
                 status_at_run={"lost": "active", "late": "active", "draft": "draft"},
                 published_before={"late"})

    def test_codes_explain_why_rule_did_or_did_not_reach_model(self):
        codes = {rule: api._rule_run_state(rule, **self.state)["code"]
                 for rule in ("in", "low", "top", "far", "lost", "late", "draft", "later")}
        self.assertEqual(codes, {"in": "included", "low": "below_threshold", "top": "top_k",
                                 "far": "not_retrieved",
                                 # действовало, а ни в один снимок ревизии не попало — сбой
                                 "lost": "missing_from_snapshot",
                                 # опубликовано, но оценка взяла снимок раньше (пакетная)
                                 "late": "inactive_at_run",
                                 "draft": "inactive_at_run", "later": "inactive_at_run"})

    def test_similarity_is_rounded_down_and_threshold_is_the_runs(self):
        state = api._rule_run_state("low", **self.state, threshold=0.7)
        self.assertEqual((state["similarity"], state["threshold"]), (0.6799, 0.7))
        self.assertEqual(api._rule_run_state("low", **self.state)["threshold"],
                         float(api.config.RETRIEVAL_MIN_SIMILARITY))

    def test_disabled_or_failed_retrieval_overrides_candidates(self):
        self.assertEqual(api._rule_run_state("in", **{**self.state, "rag_enabled": False})["code"],
                         "rag_off")
        self.assertEqual(api._rule_run_state("in", **{**self.state,
                                                      "trace_status": "degraded"})["code"],
                         "retrieval_failed")


class AdjudicationSimilarTests(unittest.TestCase):
    trace = {"status": "ok", "candidates": [
        {"criterion_id": "d72-greeting", "rule_id": PROMPT, "included": True,
         "similarity": 0.8, "reason": "Правило, как его получил ИИ",
         "situation": "Ситуация", "correct_verdict": "Correct"},
        {"criterion_id": "d72-greeting", "rule_id": DUP, "included": False,
         "similarity": 0.6, "reject_reason": "below_threshold"},
        {"criterion_id": "d72-greeting", "rule_id": "legacy:7", "included": True},
        {"criterion_id": "other", "rule_id": KEPT, "included": True, "similarity": 0.9}]}

    def _cursor(self, *, trace=None, run_threshold="0.68", status=None, published=None):
        return _Cursor([
            [(72, 10, "call", 33, True, trace or self.trace, STARTED, 5, run_threshold)],
            [(PROMPT,), (DUP,)],
            status if status is not None else [(PROMPT, "active"), (DUP, "active")],
            published if published is not None else [(PROMPT,), (DUP,)],
            [(PROMPT, "active", 5, 2, "СВ", datetime(2026, 10, 1))],
        ])

    def _call(self, found, cursor=None, **kwargs):
        with patch.object(api.config, "connect_ro", return_value=_Conn(cursor or self._cursor())), \
             patch("call_qa.rag.similar.find_similar", return_value=found) as find:
            out = api.adjudication_similar(
                call_id=10, subject_kind="call", evaluation_run_id=RUN,
                criterion_id="d72-greeting", text="текст", correct_verdict="Correct", **kwargs)
        return out, find

    def test_duplicate_the_model_did_not_receive_is_shown_but_not_linked(self):
        out, _ = self._call({"items": [_found(DUP)], "degraded": False})
        self.assertEqual([i["rule_id"] for i in out["items"]], [DUP, PROMPT])
        self.assertEqual(out["items"][0]["run"]["code"], "below_threshold")
        prompt = out["items"][1]
        self.assertEqual((prompt["run"]["code"], prompt["verdict"]), ("included", "in_prompt"))
        # Текст — как его получил ИИ (трасса прогона), а не нынешняя версия.
        self.assertEqual(prompt["rule_text"], "Правило, как его получил ИИ")
        self.assertIsNone(out["duplicate_of"])
        self.assertEqual(out["run"], {"rag_enabled": True, "status": "ok", "included": 1})

    def test_rule_from_prompt_is_link_target_and_shown_first_once(self):
        drafts = [_found(f"{i:08d}-0000-4000-8000-000000000000", status="draft")
                  for i in range(3)]
        out, _ = self._call({"items": [*drafts, _found(PROMPT)], "degraded": False})
        self.assertEqual(out["duplicate_of"], PROMPT)
        ids = [i["rule_id"] for i in out["items"]]
        self.assertEqual(ids[0], PROMPT)
        self.assertEqual(ids.count(PROMPT), 1)
        self.assertEqual(out["items"][0]["run"]["code"], "included")

    def test_active_rule_missing_from_runs_snapshot_is_flagged(self):
        lost = "44444444-4444-4444-8444-444444444444"
        cursor = self._cursor(status=[(lost, "active")], published=[(PROMPT,)])
        out, _ = self._call({"items": [_found(lost, verdict="similar")]}, cursor=cursor)
        self.assertEqual(out["items"][0]["run"]["code"], "missing_from_snapshot")
        status_sql, status_params = cursor.executed[3]
        self.assertIn("FROM qa_policy_rule_events", status_sql)
        self.assertEqual(status_params, (72, "d72-greeting", STARTED))
        published_sql, published_params = cursor.executed[4]
        self.assertIn("k.scale_revision_id = %s AND k.created_at <= %s", published_sql)
        self.assertEqual(published_params, (72, 5, STARTED, "d72-greeting"))

    def test_rule_published_after_batch_fixed_its_snapshot_is_not_a_failure(self):
        # Пакетная оценка берёт снимок один раз на направление; правило включили и
        # опубликовали в ту же ревизию позже — это «включено после», а не сбой.
        late = "55555555-5555-4555-8555-555555555555"
        cursor = self._cursor(status=[(late, "active")], published=[(late,)])
        out, _ = self._call({"items": [_found(late, verdict="similar")]}, cursor=cursor)
        self.assertEqual(out["items"][0]["run"]["code"], "inactive_at_run")

    def test_threshold_is_the_one_this_run_used(self):
        trace = {**self.trace, "config": {"min_similarity": 0.7}}
        out, _ = self._call({"items": [_found(DUP, verdict="similar")]},
                            cursor=self._cursor(trace=trace))
        self.assertEqual(out["items"][0]["run"]["threshold"], 0.7)
        out, _ = self._call({"items": [_found(DUP, verdict="similar")]},
                            cursor=self._cursor(run_threshold="0.65"))
        self.assertEqual(out["items"][0]["run"]["threshold"], 0.65)

    def test_foreign_direction_is_refused_before_any_rule_work(self):
        seen = []
        with patch.object(api.config, "connect_ro", return_value=_Conn(self._cursor())), \
             patch("call_qa.rag.similar.find_similar",
                   side_effect=AssertionError("rules read")), \
             self.assertRaises(PermissionError):
            api.adjudication_similar(
                call_id=10, evaluation_run_id=RUN, criterion_id="d72-greeting",
                authorize=lambda direction_id: seen.append(direction_id) or False)
        self.assertEqual(seen, [72])

    def test_run_of_another_subject_is_rejected(self):
        cursor = _Cursor([[(72, 99, "call", None, True, {}, STARTED, 5, None)]])
        with patch.object(api.config, "connect_ro", return_value=_Conn(cursor)), \
             self.assertRaisesRegex(ValueError, "другому разговору"):
            api.adjudication_similar(call_id=10, evaluation_run_id=RUN,
                                     criterion_id="d72-greeting")

    def test_bad_run_id_is_rejected_before_database(self):
        with patch.object(api.config, "connect_ro", side_effect=AssertionError("db used")), \
             self.assertRaisesRegex(ValueError, "evaluation_run_id"):
            api.adjudication_similar(call_id=10, evaluation_run_id="nope",
                                     criterion_id="d72-greeting")


class SchemaTests(unittest.TestCase):
    def test_text_vectors_table_and_duplicate_index_exist(self):
        from pathlib import Path
        schema = (Path(api.__file__).resolve().parent / "rag" / "schema.sql").read_text(
            encoding="utf-8-sig")
        self.assertIn("CREATE TABLE IF NOT EXISTS qa_rule_text_embeddings", schema)
        self.assertIn("PRIMARY KEY (text_hash, embedding_model_id)", schema)
        self.assertIn("idx_adjudication_case_duplicate_of", schema)
        # Таблица создаётся после qa_embedding_models, на которую ссылается.
        self.assertLess(schema.index("CREATE TABLE IF NOT EXISTS qa_embedding_models"),
                        schema.index("CREATE TABLE IF NOT EXISTS qa_rule_text_embeddings"))


if __name__ == "__main__":
    unittest.main()
