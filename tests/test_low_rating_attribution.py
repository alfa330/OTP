# -*- coding: utf-8 -*-
"""Кому засчитывается низкая оценка чата (задача #286).

Chat2Desk отдаёт низкую оценку тому, кто закрыл чат, а вести его могли
несколько менеджеров. Проверяющие сами решают, кому оценка засчитывается:
одному или сразу нескольким — тогда каждому целиком, а не долями.
Необоснованная не засчитывается никому. Статистика качества строится по этому
решению, а не по тому, кого назвал Chat2Desk.

Сторожим:
1. арифметику дня: снимаем свои оценки, отданные другим, и добавляем чужие,
   засчитанные оператору; день без строки/без сырья не теряет засчитанную;
2. правила сохранения: «обоснованно» без менеджера не сохранить, при
   «необоснованно» отметки не трогаются, решение «как у Chat2Desk» хранится NULL;
3. переписку: кто вёл чат (по учёткам Chat2Desk в сообщениях) и дозаполнение
   учёток у старых снапшотов — без лишних запросов к квоте;
4. что проверяющая и операторская стороны видят ровно свои поля.

Монолиты не импортируем (см. tests/source_cache.py) — методы достаём через ast.
"""

import ast
import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"


def _exec_functions(path, names, namespace, class_name=None):
    body = []
    for name in names:
        node = source_cache.function_copy(path, name, class_name=class_name)
        node.decorator_list = []
        body.append(node)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def _db_class_constant(name):
    module = source_cache.tree(DB_PATH)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    for node in database_class.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(name)


STATIC_HELPERS = (
    "_normalize_low_rating_review_status",
    "_normalize_low_rating_operator_ids",
    "_low_rating_effective_attribution",
    "_low_rating_attribution_to_store",
    "_low_rating_db_time_iso",
    "_low_rating_recalc_pairs",
    "_low_rating_extract_source_details",
    "_low_rating_operator_item",
)


def _conflict_class(namespace):
    node = copy.deepcopy(next(
        n for n in source_cache.tree(DB_PATH).body
        if isinstance(n, ast.ClassDef) and n.name == "LowRatingAttributionConflict"
    ))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(DB_PATH), "exec"), namespace)
    return namespace["LowRatingAttributionConflict"]


def _database_shim():
    namespace = {"datetime": datetime, "date": date, "dt_timezone": timezone, "ZoneInfo": ZoneInfo}
    _exec_functions(DB_PATH, STATIC_HELPERS, namespace, class_name="Database")
    _conflict_class(namespace)

    class Shim:
        LOW_RATING_ATTRIBUTION_REQUIRED = _db_class_constant("LOW_RATING_ATTRIBUTION_REQUIRED")
        LOW_RATING_ATTRIBUTION_CHANGED = _db_class_constant("LOW_RATING_ATTRIBUTION_CHANGED")

    for name in STATIC_HELPERS:
        setattr(Shim, name, staticmethod(namespace[name]))
    namespace["Database"] = Shim
    return Shim, namespace


class AttributionHelpersTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = _database_shim()

    def test_operator_ids_are_normalized(self):
        normalize = self.db._normalize_low_rating_operator_ids
        self.assertIsNone(normalize(None))
        self.assertEqual(normalize([]), [])
        self.assertEqual(normalize(["227", 190, 190]), [190, 227])
        for bad in ("190", [0], [-3], ["x"], [True], {"id": 1}):
            with self.assertRaises(ValueError):
                normalize(bad)

    def test_effective_attribution_defaults_to_chat2desk_operator(self):
        effective = self.db._low_rating_effective_attribution
        self.assertEqual(effective(190, None), [190])
        self.assertEqual(effective(190, []), [190])
        self.assertEqual(effective(190, [227, 190]), [190, 227])
        self.assertEqual(effective(None, None), [])

    def test_decision_equal_to_chat2desk_is_stored_as_null(self):
        """Решение «как у Chat2Desk» не застывает: если пересинк поправит, кому
        вендор отдал оценку, она пойдёт за новым оператором."""
        store = self.db._low_rating_attribution_to_store
        self.assertIsNone(store(190, [190]))
        self.assertIsNone(store(190, []))
        self.assertEqual(store(190, [227]), [227])
        self.assertEqual(store(190, [227, 190]), [190, 227])

    def test_recalc_pairs_cover_owner_and_every_attributed(self):
        day = date(2026, 9, 25)
        pairs = self.db._low_rating_recalc_pairs(190, day, [190], [190, 227], None)
        self.assertEqual(pairs, {(190, day), (227, day)})

    def test_service_timestamps_are_shown_in_almaty(self):
        """Голоса и история пишутся CURRENT_TIMESTAMP в сессии UTC — на экране
        они стоят рядом со временем оценки, а оно местное."""
        stamp = self.db._low_rating_db_time_iso
        self.assertEqual(stamp(datetime(2026, 9, 28, 1, 51, 27)), "2026-09-28T06:51:27")
        self.assertEqual(
            stamp(datetime(2026, 9, 28, 1, 51, 27, tzinfo=timezone.utc)), "2026-09-28T06:51:27")
        self.assertIsNone(stamp(None))
        self.assertIsNone(stamp("2026-09-28"))


class OperatorSideTests(unittest.TestCase):
    """Оператор видит, что оценку засчитали другому, но не кому."""

    def setUp(self):
        self.db, _ = _database_shim()
        self.item = {
            "id": "r1", "operator_id": 190, "operator_name": "Асель", "score": 1.0,
            "final_status": "valid", "attributed_operator_ids": [227],
            "raw_payload": {}, "review_entries": [],
        }

    def test_reassigned_for_chat2desk_operator(self):
        view = self.db._low_rating_operator_item(self.item, viewer_id=190)
        self.assertTrue(view["reassigned"])
        self.assertNotIn("attributed_operator_ids", view)
        self.assertNotIn("attributed_operators", view)

    def test_counted_for_attributed_manager(self):
        view = self.db._low_rating_operator_item(self.item, viewer_id=227)
        self.assertFalse(view["reassigned"])

    def test_counted_for_everyone_in_the_list(self):
        self.item["attributed_operator_ids"] = [190, 227]
        self.assertFalse(self.db._low_rating_operator_item(self.item, viewer_id=190)["reassigned"])
        self.assertFalse(self.db._low_rating_operator_item(self.item, viewer_id=227)["reassigned"])

    def test_invalid_and_pending_are_never_reassigned(self):
        for status in ("invalid", None):
            self.item["final_status"] = status
            self.assertFalse(self.db._low_rating_operator_item(self.item, viewer_id=190)["reassigned"])


class _ScriptedCursor:
    """Курсор, который отвечает на запросы пересчёта из заданного состояния.

    Сами FILTER-выражения проверяются на настоящем Postgres отдельно; здесь —
    что метод правильно складывает ответы и какие записи делает."""

    def __init__(self, metrics, adjustments):
        self.metrics = metrics          # {(op, day): (raw_sum, raw_count, avg)}
        self.adjustments = adjustments  # {(op, day): (removed_sum, removed_count, added_sum, added_count)}
        self.updates = {}
        self.inserts = []
        self._result = None

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if text.startswith("SELECT raw_score_sum, raw_score_count, avg_score"):
            self._result = self.metrics.get((params[0], params[1]))
        elif "FILTER (WHERE operator_id = %s" in text:
            operator_id, day = params[0], params[8]
            self._result = self.adjustments.get((operator_id, day), (0, 0, 0, 0))
        elif text.startswith("INSERT INTO chat_manager_daily_metrics"):
            self.inserts.append((params[0], params[1]))
            self.metrics[(params[0], params[1])] = (0.0, 0, None)
            self._result = None
        elif text.startswith("UPDATE chat_manager_daily_metrics"):
            (score_sum, score_count, avg, base_sum, base_count, operator_id, day) = params
            self.updates[(operator_id, day)] = {
                "score_sum": score_sum, "score_count": score_count, "avg_score": avg,
                "raw_seed": (base_sum, base_count),
            }
            self._result = None
        else:
            raise AssertionError(f"неожиданный запрос: {text[:120]}")

    def fetchone(self):
        return self._result


class RecalculateDayTests(unittest.TestCase):
    DAY = date(2026, 9, 25)

    def setUp(self):
        namespace = {"datetime": datetime, "date": date, "Json": lambda value: value}
        _exec_functions(DB_PATH, ["_recalculate_chat_manager_score_adjustments_tx"], namespace,
                        class_name="Database")
        self.recalc = namespace["_recalculate_chat_manager_score_adjustments_tx"]

        class Self:
            def _aggregate_month_from_daily_tx(self, cursor, operator_id, month):
                return {"month": month}

        self.self = Self()

    def run_recalc(self, metrics, adjustments, pairs):
        cursor = _ScriptedCursor(metrics, adjustments)
        result = self.recalc(self.self, cursor, pairs)
        return cursor, result

    def test_reassigned_rating_moves_from_owner_to_new_manager(self):
        """Оценку 1 Chat2Desk отдал Асель (190); её засчитали Данияру (227)."""
        cursor, result = self.run_recalc(
            metrics={(190, self.DAY): (14.0, 4, 3.5), (227, self.DAY): (10.0, 2, 5.0)},
            adjustments={(190, self.DAY): (1.0, 1, 0, 0), (227, self.DAY): (0, 0, 1.0, 1)},
            pairs=[(190, self.DAY), (227, self.DAY)],
        )
        self.assertEqual(cursor.updates[(190, self.DAY)]["score_count"], 3)
        self.assertEqual(cursor.updates[(190, self.DAY)]["score_sum"], 13.0)
        self.assertEqual(cursor.updates[(227, self.DAY)]["score_count"], 3)
        self.assertEqual(cursor.updates[(227, self.DAY)]["score_sum"], 11.0)
        self.assertEqual(result["adjusted_days"], 2)
        self.assertEqual(cursor.inserts, [])

    def test_shared_rating_counts_for_both_in_full(self):
        """Засчитана обоим: у Асель остаётся целиком, Данияру добавляется целиком."""
        cursor, _ = self.run_recalc(
            metrics={(190, self.DAY): (14.0, 4, 3.5), (227, self.DAY): (10.0, 2, 5.0)},
            adjustments={(190, self.DAY): (0, 0, 0, 0), (227, self.DAY): (0, 0, 2.0, 1)},
            pairs=[(190, self.DAY), (227, self.DAY)],
        )
        self.assertEqual(cursor.updates[(190, self.DAY)]["score_count"], 4)
        self.assertEqual(cursor.updates[(190, self.DAY)]["score_sum"], 14.0)
        self.assertEqual(cursor.updates[(227, self.DAY)]["score_count"], 3)
        self.assertEqual(cursor.updates[(227, self.DAY)]["score_sum"], 12.0)

    def test_manager_without_a_day_row_gets_one(self):
        """Чат шёл через полночь: засчитанная оценка не должна пропасть."""
        cursor, _ = self.run_recalc(
            metrics={},
            adjustments={(227, self.DAY): (0, 0, 2.0, 1)},
            pairs=[(227, self.DAY)],
        )
        self.assertEqual(cursor.inserts, [(227, self.DAY)])
        update = cursor.updates[(227, self.DAY)]
        self.assertEqual((update["score_sum"], update["score_count"], update["avg_score"]), (2.0, 1, 2.0))

    def test_missing_row_without_attributed_ratings_is_left_alone(self):
        cursor, result = self.run_recalc(metrics={}, adjustments={}, pairs=[(227, self.DAY)])
        self.assertEqual(cursor.inserts, [])
        self.assertEqual(cursor.updates, {})
        self.assertEqual(result["adjusted_days"], 0)

    def test_day_without_raw_scores_is_seeded_from_what_it_contributed(self):
        """Старый ручной ввод одной средней: месяц считал её с весом 1 — так и
        остаётся, плюс засчитанная оценка; сырьё фиксируется для отката."""
        cursor, _ = self.run_recalc(
            metrics={(227, self.DAY): (None, None, 4.5)},
            adjustments={(227, self.DAY): (0, 0, 1.0, 1)},
            pairs=[(227, self.DAY)],
        )
        update = cursor.updates[(227, self.DAY)]
        self.assertEqual((update["score_sum"], update["score_count"]), (5.5, 2))
        self.assertEqual(update["raw_seed"], (4.5, 1))

    def test_day_without_raw_scores_and_nothing_added_is_not_touched(self):
        cursor, _ = self.run_recalc(
            metrics={(227, self.DAY): (None, None, 4.5)},
            adjustments={(227, self.DAY): (1.0, 1, 0, 0)},
            pairs=[(227, self.DAY)],
        )
        self.assertEqual(cursor.updates, {})

    def test_zero_ratings_mean_zero_sum(self):
        cursor, _ = self.run_recalc(
            metrics={(190, self.DAY): (1.0, 1, 1.0)},
            adjustments={(190, self.DAY): (1.0, 1, 0, 0)},
            pairs=[(190, self.DAY)],
        )
        update = cursor.updates[(190, self.DAY)]
        self.assertEqual((update["score_sum"], update["score_count"], update["avg_score"]), (0.0, 0, None))


class NextAttributionTests(unittest.TestCase):
    """Когда и как меняется атрибуция при сохранении вердикта."""

    def setUp(self):
        shim, namespace = _database_shim()
        self.conflict = namespace["LowRatingAttributionConflict"]
        _exec_functions(DB_PATH, ["_low_rating_next_attribution_tx"], namespace, class_name="Database")
        self.next_attribution = namespace["_low_rating_next_attribution_tx"]
        self.validated = []
        validated = self.validated

        class Self(shim):
            def _validate_low_rating_attribution_tx(self, cursor, ids, owner, scope=None, stored_ids=None):
                validated.append((tuple(ids), owner, scope, stored_ids))

        self.self = Self()

    def call(self, status, ids, stored=None, scope=None, base=None):
        return self.next_attribution(self.self, None, status, ids, 190, stored,
                                     department_scope_id=scope, base_operator_ids=base)

    def test_invalid_keeps_previous_decision(self):
        self.assertEqual(self.call("invalid", [227], stored=[227, 300]), [227, 300])
        self.assertEqual(self.validated, [])

    def test_old_client_without_field_keeps_decision(self):
        self.assertEqual(self.call("valid", None, stored=[227]), [227])

    def test_valid_requires_at_least_one_manager(self):
        with self.assertRaises(ValueError) as caught:
            self.call("valid", [])
        self.assertIn("хотя бы одного менеджера", str(caught.exception))

    def test_valid_saves_normalized_decision_after_validation(self):
        self.assertEqual(self.call("valid", [190, 227], scope=1, stored=[300]), [190, 227])
        self.assertEqual(self.validated, [((190, 227), 190, 1, [300])])
        self.assertIsNone(self.call("valid", [190]))

    def test_stale_view_is_rejected_instead_of_overwriting(self):
        """Второй проверяющий открыл карточку до того, как первый засчитал
        оценку обоим: его «обоснованно» по старым отметкам не должно молча
        снять оценку со второго менеджера."""
        with self.assertRaises(self.conflict) as caught:
            self.call("valid", None, stored=[190, 227], base=[190])
        self.assertIn("другой проверяющий", str(caught.exception))
        with self.assertRaises(self.conflict):
            self.call("valid", [300], stored=[190, 227], base=[190])
        # Необоснованно отметок не касается — сверять нечего.
        self.assertEqual(self.call("invalid", None, stored=[190, 227], base=[190]), [190, 227])

    def test_current_view_passes(self):
        self.assertEqual(self.call("valid", None, stored=[190, 227], base=[227, 190]), [190, 227])
        # NULL в базе = «как у Chat2Desk» = тот же [190], что видел проверяющий.
        self.assertIsNone(self.call("valid", None, stored=None, base=[190]))
        self.assertEqual(self.call("valid", [227], stored=None, base=[190]), [227])


class ValidateAttributionTests(unittest.TestCase):
    """Проверка отдела не мешает сохранить уже засчитанное."""

    def setUp(self):
        namespace = {}
        _exec_functions(DB_PATH, ["_validate_low_rating_attribution_tx"], namespace, class_name="Database")
        self.validate = namespace["_validate_low_rating_attribution_tx"]
        # 190 и 227 — СЗоВ (1), 300 — переведён в другой отдел (2).
        self.users = {190: 1, 227: 1, 300: 2}

    def cursor(self):
        users = self.users

        class Cursor:
            def execute(self, sql, params):
                self.rows = [(uid, users[uid]) for uid in params[0] if uid in users]

            def fetchall(self):
                return self.rows

        return Cursor()

    def test_new_manager_from_another_department_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.validate(None, self.cursor(), [190, 300], 190, department_scope_id=1)
        self.assertIn("своего отдела", str(caught.exception))

    def test_already_attributed_manager_moved_away_is_kept(self):
        self.validate(None, self.cursor(), [190, 300], 190, department_scope_id=1, stored_ids=[190, 300])

    def test_owner_and_global_reviewer_pass(self):
        self.users[190] = 2
        self.validate(None, self.cursor(), [190, 227], 190, department_scope_id=1)
        self.validate(None, self.cursor(), [300], 190, department_scope_id=None)

    def test_unknown_user_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(None, self.cursor(), [999], 190)


def _bot_namespace(db_stub, api_stub=None):
    namespace = {
        "db": db_stub,
        "logging": __import__("logging"),
        "dt_date": date,
        "timedelta": timedelta,
        "_c2d_api_get": api_stub or (lambda path, params=None: (_ for _ in ()).throw(AssertionError("API не ждали"))),
    }
    module = source_cache.tree(BOT_PATH)
    body = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "LOW_RATING_PARTICIPANT_MESSAGE_TYPES" for t in node.targets
        ):
            body.append(node)
    for name in ("_c2d_int_or_none", "_c2d_api_date", "_low_rating_snapshot_fill_operators",
                 "_low_rating_chat_view", "_low_rating_attributed_names"):
        body.append(source_cache.function_copy(BOT_PATH, name))
    exec(compile(ast.Module(body=body, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


class _DbStub:
    def __init__(self, webhook=None, directory=None):
        shim, _ = _database_shim()
        self._low_rating_effective_attribution = shim._low_rating_effective_attribution
        self.webhook = webhook or {}
        self.directory = directory or {}
        self.persisted = []
        self.webhook_calls = []

    def get_c2d_webhook_message_operators(self, request_id, day_from, day_to):
        self.webhook_calls.append((request_id, day_from, day_to))
        return dict(self.webhook)

    def update_c2d_snapshot_messages(self, snapshot_id, messages, patch=None):
        self.persisted.append((snapshot_id, [dict(m) for m in messages], patch))
        return 1

    def get_c2d_operator_directory(self, ids):
        return {key: value for key, value in self.directory.items() if key in set(ids)}


OLD_SNAPSHOT = {
    "id": 77, "request_id": 70000001, "dialog_id": 10000001, "source": "chat2desk",
    "request": {},
    "messages": [
        {"id": 1, "type": "from_client", "text": "", "created": "2026-09-25T16:09:36"},
        {"id": 2, "type": "system", "text": "Чат передан от Айгерим к Жанель", "created": "2026-09-25T16:09:39"},
        {"id": 3, "type": "to_client", "text": "+", "created": "2026-09-25T16:09:47"},
        {"id": 4, "type": "to_client", "text": "+", "created": "2026-09-25T16:28:21"},
    ],
}


class SnapshotOperatorsFillTests(unittest.TestCase):
    def test_new_snapshot_is_left_as_is(self):
        db = _DbStub()
        ns = _bot_namespace(db)
        snapshot = {**OLD_SNAPSHOT, "messages": [{**m, "operatorId": 1} for m in OLD_SNAPSHOT["messages"]]}
        self.assertIs(ns["_low_rating_snapshot_fill_operators"](snapshot), snapshot)
        self.assertEqual(db.webhook_calls, [])

    def test_webhook_raw_data_fills_without_spending_quota(self):
        db = _DbStub(webhook={1: 41001, 2: 41001, 3: 41002, 4: 41001})
        ns = _bot_namespace(db)  # API-заглушка падает, если её позвать
        result = ns["_low_rating_snapshot_fill_operators"](OLD_SNAPSHOT)
        self.assertEqual([m["operatorId"] for m in result["messages"]], [41001, 41001, 41002, 41001])
        self.assertEqual(db.persisted[0][2], None)  # отметки «API уже спрашивали» нет
        # Окно — сутки вокруг переписки, по индексу (day, request_id).
        self.assertEqual(db.webhook_calls, [(70000001, date(2026, 9, 24), date(2026, 9, 26))])
        self.assertNotIn("operatorId", OLD_SNAPSHOT["messages"][0])  # исходник не тронут

    def test_gap_only_in_service_messages_does_not_spend_quota(self):
        """Вебхуки покрыли реплики и сообщения клиента, но не системное —
        платный запрос ради него не нужен: разбору учётка системных не нужна."""
        db = _DbStub(webhook={1: 41001, 3: 41002, 4: 41001})
        ns = _bot_namespace(db)  # API-заглушка падает, если её позвать
        result = ns["_low_rating_snapshot_fill_operators"](OLD_SNAPSHOT)
        self.assertEqual([m["operatorId"] for m in result["messages"]], [41001, None, 41002, 41001])
        self.assertIsNone(db.persisted[0][2])

    def test_message_reported_by_webhook_without_operator_is_known(self):
        """Чат ещё в очереди: вендор сам прислал сообщение клиента без учётки."""
        db = _DbStub(webhook={1: None, 3: 41002, 4: 41001})
        ns = _bot_namespace(db)
        result = ns["_low_rating_snapshot_fill_operators"](OLD_SNAPSHOT)
        self.assertEqual([m["operatorId"] for m in result["messages"]], [None, None, 41002, 41001])

    def test_single_dated_api_call_for_old_snapshot(self):
        calls = []

        def api(path, params=None):
            calls.append((path, dict(params or {})))
            return {"data": [{"id": 1, "operator_id": 41001}, {"id": 3, "operator_id": 41002}],
                    "meta": {"total": 2}}

        db = _DbStub()
        ns = _bot_namespace(db, api)
        result = ns["_low_rating_snapshot_fill_operators"](OLD_SNAPSHOT)
        # Конец окна у вендора — «строго до», поэтому +1 сутки.
        self.assertEqual(calls, [("/v1/messages", {
            "dialog_id": 10000001, "start_date": "25-09-2026", "finish_date": "26-09-2026", "limit": 200})])
        # Ответ про эту переписку: чего вендор не отдал — «неизвестно», больше не спрашиваем.
        self.assertEqual([m["operatorId"] for m in result["messages"]], [41001, None, 41002, None])
        self.assertEqual(db.persisted[0][2], {"operator_ids_checked": True})

    def test_chat_across_midnight_asks_both_days(self):
        calls = []

        def api(path, params=None):
            calls.append(dict(params or {}))
            return {"data": [{"id": 1, "operator_id": 1}], "meta": {"total": 1}}

        snapshot = {**OLD_SNAPSHOT, "messages": [
            {"id": 1, "type": "to_client", "created": "2026-09-24T23:55:00"},
            {"id": 2, "type": "to_client", "created": "2026-09-25T00:05:00"},
        ]}
        ns = _bot_namespace(_DbStub(), api)
        ns["_low_rating_snapshot_fill_operators"](snapshot)
        self.assertEqual((calls[0]["start_date"], calls[0]["finish_date"]), ("24-09-2026", "26-09-2026"))

    def test_answer_about_another_chat_does_not_mark_unknown(self):
        """Ни одного нашего сообщения в ответе — отмечать «вендор не знает» нельзя;
        флаг ставим (повтор квоту не вернёт), а реплики остаются без учётки."""
        def api(path, params=None):
            return {"data": [{"id": 999, "operator_id": 5}], "meta": {"total": 1}}

        db = _DbStub()
        ns = _bot_namespace(db, api)
        result = ns["_low_rating_snapshot_fill_operators"](OLD_SNAPSHOT)
        by_id = {m["id"]: m for m in result["messages"]}
        self.assertNotIn("operatorId", by_id[3])
        self.assertIsNone(by_id[2]["operatorId"])  # системное разбору не нужно
        self.assertEqual(db.persisted[0][2], {"operator_ids_checked": True})

    def test_api_is_not_asked_twice(self):
        db = _DbStub()
        ns = _bot_namespace(db)
        snapshot = {**OLD_SNAPSHOT, "request": {"operator_ids_checked": True},
                    "messages": [m for m in OLD_SNAPSHOT["messages"] if m["type"] != "system"]}
        self.assertIs(ns["_low_rating_snapshot_fill_operators"](snapshot), snapshot)
        self.assertEqual(db.persisted, [])

    def test_api_failure_keeps_replies_unknown_and_allows_retry(self):
        def api(path, params=None):
            raise RuntimeError("Chat2Desk: превышен лимит запросов API — попробуйте позже")

        db = _DbStub()
        ns = _bot_namespace(db, api)
        result = ns["_low_rating_snapshot_fill_operators"](OLD_SNAPSHOT)
        by_id = {m["id"]: m for m in result["messages"]}
        for mid in (1, 3, 4):
            self.assertNotIn("operatorId", by_id[mid])
        # Флага нет — при следующем открытии запрос повторится.
        self.assertTrue(all(patch is None for _, _, patch in db.persisted))

    def test_wazzup_snapshot_is_not_touched(self):
        db = _DbStub()
        ns = _bot_namespace(db)
        snapshot = {**OLD_SNAPSHOT, "source": "wazzup"}
        self.assertIs(ns["_low_rating_snapshot_fill_operators"](snapshot), snapshot)


class ChatViewTests(unittest.TestCase):
    DIRECTORY = {
        41001: {"user_id": 190, "name": "Асель Тестбаева", "department_id": 1},
        41002: {"user_id": 227, "name": "Данияр Примеров", "department_id": 1},
        41005: {"user_id": 500, "name": "Супервайзер другого отдела", "department_id": 7},
        41900: {"user_id": None, "name": "Служебная учётка"},
    }

    def view(self, messages, review, scope=None):
        db = _DbStub(directory=self.DIRECTORY)
        ns = _bot_namespace(db)
        return ns["_low_rating_chat_view"]({"messages": messages}, review, department_scope_id=scope)

    def test_manager_from_another_department_is_listed_but_not_selectable(self):
        messages = [
            {"id": 1, "type": "to_client", "created": "2026-09-25T10:00:00", "operatorId": 41001},
            {"id": 2, "type": "to_client", "created": "2026-09-25T10:05:00", "operatorId": 41005},
            {"id": 3, "type": "to_client", "created": "2026-09-25T10:06:00", "operatorId": 41900},
        ]
        review = {"operator_id": 190, "operator_name": "Асель Тестбаева"}
        _, scoped = self.view(messages, review, scope=1)
        self.assertEqual([(p["name"], p["selectable"]) for p in scoped], [
            ("Асель Тестбаева", True), ("Супервайзер другого отдела", False), ("Служебная учётка", False)])
        self.assertEqual([p["c2d_id"] for p in scoped], [41001, 41005, 41900])
        _, global_view = self.view(messages, review, scope=None)
        self.assertEqual([p["selectable"] for p in global_view], [True, True, False])
        # Уже засчитанного выбрать можно всегда — даже если его перевели в другой отдел.
        _, kept = self.view(messages, {**review, "attributed_operator_ids": [190, 500]}, scope=1)
        self.assertTrue(next(p for p in kept if p["id"] == 500)["selectable"])

    def test_partially_filled_snapshot_never_borrows_a_name(self):
        """Часть сообщений без учётки: подпись пустая, а не имя оператора снапшота."""
        snapshot, _ = self.view([
            {"id": 1, "type": "to_client", "created": "2026-09-25T10:00:00", "operatorId": 41001},
            {"id": 2, "type": "to_client", "created": "2026-09-25T10:05:00"},
        ], {"operator_id": 190, "operator_name": "Асель"})
        self.assertEqual([m.get("author") for m in snapshot["messages"]], ["Асель Тестбаева", ""])

    def test_participants_in_order_with_segments_and_replies(self):
        snapshot, participants = self.view([
            {"id": 1, "type": "from_client", "created": "2026-09-25T16:09:36", "operatorId": 41001},
            {"id": 2, "type": "system", "created": "2026-09-25T16:09:39", "operatorId": 41004},
            {"id": 3, "type": "to_client", "created": "2026-09-25T16:09:47", "operatorId": 41002},
            {"id": 4, "type": "to_client", "created": "2026-09-25T16:28:21", "operatorId": 41001},
            {"id": 5, "type": "comment", "created": "2026-09-25T16:29:00", "operatorId": 41900},
            {"id": 6, "type": "autoreply", "created": "2026-09-25T16:30:00", "operatorId": 41004},
        ], {"operator_id": 190, "operator_name": "Асель Тестбаева",
            "attributed_operators": [{"id": 190, "name": "Асель Тестбаева"}]})
        self.assertEqual([p["name"] for p in participants],
                         ["Асель Тестбаева", "Данияр Примеров", "Служебная учётка"])
        asel, daniyar, admin = participants
        self.assertEqual((asel["first_at"], asel["last_at"], asel["replies"]),
                         ("2026-09-25T16:09:36", "2026-09-25T16:28:21", 1))
        self.assertTrue(asel["is_default"])
        self.assertEqual((daniyar["id"], daniyar["replies"], daniyar["is_default"]), (227, 1, False))
        self.assertIsNone(admin["id"])
        # Системные сообщения и автоответы в участников не превращаются.
        self.assertEqual(len(participants), 3)
        # Подписи авторов — у реплик и заметок, в ответе, а не в базе.
        authors = {m["id"]: m.get("author") for m in snapshot["messages"]}
        self.assertEqual(authors[3], "Данияр Примеров")
        self.assertEqual(authors[4], "Асель Тестбаева")
        self.assertEqual(authors[5], "Служебная учётка")
        self.assertIsNone(authors[1])

    def test_unknown_author_gets_no_name_instead_of_a_wrong_one(self):
        snapshot, _ = self.view([
            {"id": 1, "type": "to_client", "created": "2026-09-25T16:09:47", "operatorId": None},
        ], {"operator_id": 190, "operator_name": "Асель"})
        self.assertEqual(snapshot["messages"][0]["author"], "")

    def test_old_snapshot_keeps_fallback_and_lists_owner_and_attributed(self):
        """Учёток в снапшоте нет вовсе: подписи не трогаем (ChatThread подпишет
        по-старому), а в списке — Chat2Desk-оператор и уже засчитанные."""
        snapshot, participants = self.view([
            {"id": 1, "type": "to_client", "created": "2026-08-10T10:56:39"},
        ], {"operator_id": 190, "operator_name": "Асель", "attributed_operator_ids": [190, 5],
            "attributed_operators": [{"id": 190, "name": "Асель"}, {"id": 5, "name": "Ранее"}]})
        self.assertNotIn("author", snapshot["messages"][0])
        self.assertEqual([(p["id"], p["name"], p["is_default"]) for p in participants],
                         [(190, "Асель", True), (5, "Ранее", False)])


class ExportColumnTests(unittest.TestCase):
    def setUp(self):
        self.names = _bot_namespace(_DbStub())["_low_rating_attributed_names"]

    def test_only_final_valid_has_attribution(self):
        operators = [{"id": 190, "name": "Асель"}, {"id": 227, "name": "Данияр"}]
        self.assertEqual(self.names({"final_status": "valid", "attributed_operators": operators}), "Асель, Данияр")
        self.assertEqual(self.names({"final_status": "invalid", "attributed_operators": operators}), "")
        self.assertEqual(self.names({"final_status": None, "attributed_operators": operators}), "")


class RouteWiringTests(unittest.TestCase):
    """Проводка ручек: без неё UI шлёт отметки в пустоту."""

    def source(self, name):
        return ast.unparse(source_cache.function_node(BOT_PATH, name))

    def test_patch_passes_attribution_to_both_decisions(self):
        text = self.source("update_chat_manager_low_rating_review")
        self.assertEqual(text.count("operator_ids=operator_ids"), 2)
        self.assertEqual(text.count("department_scope_id=attribution_scope"), 2)
        self.assertEqual(text.count("base_operator_ids=base_operator_ids"), 2)
        self.assertIn("'operator_ids' in payload", text)

    def test_stale_attribution_answers_409_with_fresh_card(self):
        text = self.source("update_chat_manager_low_rating_review")
        self.assertIn("except LowRatingAttributionConflict as conflict", text)
        self.assertIn("'ATTRIBUTION_CHANGED'", text)
        self.assertIn("409", text)

    def test_conflict_class_is_imported_by_the_app(self):
        module = source_cache.tree(BOT_PATH)
        imported = {
            alias.name
            for node in module.body if isinstance(node, ast.ImportFrom) and node.module == "database"
            for alias in node.names
        }
        self.assertIn("LowRatingAttributionConflict", imported)

    def test_chat_participants_only_for_reviewers(self):
        text = self.source("chat_manager_low_rating_review_chat")
        self.assertIn("_low_rating_snapshot_fill_operators(snapshot)", text)
        guard = next(
            node for node in ast.walk(source_cache.function_node(BOT_PATH, "chat_manager_low_rating_review_chat"))
            if isinstance(node, ast.If) and ast.unparse(node.test) == "is_reviewer"
        )
        self.assertIn("participants", ast.unparse(guard))

    def test_attributed_operator_opens_only_final_valid_chat(self):
        text = self.source("chat_manager_low_rating_review_chat")
        self.assertIn("current.get('final_status') == 'valid'", text)
        self.assertIn("int(requester_id) in (current.get('attributed_operator_ids') or [])", text)

    def test_operator_list_includes_ratings_given_to_them_only_when_final(self):
        text = ast.unparse(source_cache.function_node(DB_PATH, "list_operator_low_rating_reviews", "Database"))
        self.assertIn("lr.final_status = 'valid' AND %s = ANY(lr.attributed_operator_ids)", text)
        self.assertIn("self._low_rating_operator_item(item, viewer_id=operator_id)", text)

    def test_every_decision_path_recalculates_attributed_days(self):
        """Пересчёт только дня Chat2Desk-оператора оставил бы засчитанному
        менеджеру старый балл — поэтому пары собираются со всей атрибуцией."""
        for name in ("save_chat_manager_low_rating_personal_review",
                     "finalize_chat_manager_low_rating_review",
                     "update_chat_manager_low_rating_review"):
            text = ast.unparse(source_cache.function_node(DB_PATH, name, "Database"))
            self.assertIn("self._low_rating_recalc_pairs(", text, name)

    def test_history_is_reviewer_only_and_department_scoped(self):
        text = self.source("chat_manager_low_rating_review_history")
        self.assertIn("_is_supervisor_role(requester_role)", text)
        self.assertIn("_department_scope_id_for_requester(requester_id)", text)
        self.assertIn("return (jsonify({'error': 'Forbidden'}), 403)", text)


if __name__ == "__main__":
    unittest.main()
