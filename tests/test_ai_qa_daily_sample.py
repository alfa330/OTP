# -*- coding: utf-8 -*-
"""Ежедневная выборка «ИИ-оценки» (call_qa.daily_sample): 30 случайных на направление.

Стережём:
* день выборки — вчера по АЛМАТЫ, а не по часам сервера (UTC);
* повторный проход того же дня ДОБИРАЕТ направление до N, а не берёт ещё N;
  место освобождает только сорвавшийся субъект;
* звонки чужого отдела не берутся; уже лежащие в пуле не подтягиваются второй
  раз (до платного скачивания записи); лежащую АТС после трёх отказов подряд не
  долбим;
* оценка: отказ по существу — замена сразу, сбой модели — повтор следующим
  проходом, запись моста ждётся в пределах прохода, каждый разговор проход
  трогает один раз;
* проход под advisory-локом — второй инстанс ничего не выбирает и не оценивает;
* строки пула выборки невидимы журналу и «Делению звонков» (статус 'ai_sample');
* проводка в монолите: расписание, свой пул, ручка только супер-админу.
"""
import ast
import random
import re
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from call_qa import config, daily_sample as ds
from call_qa import subjects as subjects_mod
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 9, 29)


class FakeDB:
    """Ровно те запросы, что шлёт выборка, над списком строк в памяти."""

    def __init__(self, departments=None, imported=(), locked=False, canonical=(), chat_ids=()):
        self.departments = dict(departments or {})
        self.imported = set(imported)
        self.locked = locked
        self.canonical = list(canonical)
        self.chat_ids = list(chat_ids)
        self.samples = []
        self.chat_sql = []

    def connect(self):
        return FakeConn(self)

    def by_status(self, status):
        return [s for s in self.samples if s["status"] == status]


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self.db)

    def close(self):
        pass

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def execute(self, sql, params=()):
        db, s = self.db, " ".join(sql.split())
        if "pg_try_advisory_lock" in s:
            self.rows = [(not db.locked,)]
        elif s.startswith("SELECT d.id, lower(COALESCE(dep.code"):
            self.rows = [(d, db.departments[d]) for d in params[0] if d in db.departments]
        elif s.startswith("SELECT direction_id, family, count(*)"):
            day, failed = params
            counts = {}
            for x in db.samples:
                if x["sample_day"] == day and x["status"] != failed:
                    key = (x["direction_id"], x["family"])
                    counts[key] = counts.get(key, 0) + 1
            self.rows = [(d, f, n) for (d, f), n in counts.items()]
        elif s.startswith("SELECT external_id FROM imported_calls"):
            self.rows = [(k,) for k in params[0] if k in db.imported]
        elif s.startswith("SELECT DISTINCT COALESCE(canonical_id, id) FROM directions"):
            self.rows = [(d,) for d in db.canonical]
        elif "FROM wazzup_episodes t" in s:
            db.chat_sql.append((s, params))
            self.rows = [(i,) for i in db.chat_ids[:params[-1]]]
        elif s.startswith("INSERT INTO ai_qa_daily_samples"):
            day, department, direction_id, family, kind, subject_id = params
            if any(x["subject_kind"] == kind and x["subject_id"] == subject_id for x in db.samples):
                self.rows = []
                return
            sample_id = len(db.samples) + 1
            db.samples.append({"id": sample_id, "sample_day": day, "department": department,
                               "direction_id": direction_id, "family": family,
                               "subject_kind": kind, "subject_id": subject_id,
                               "status": ds.STATUS_PICKED, "attempts": 0, "last_error": None})
            self.rows = [(sample_id,)]
        elif s.startswith("UPDATE ai_qa_daily_samples") and "sample_day <" in s:
            failed, error, picked, cutoff = params
            stale = [x for x in db.samples if x["status"] == picked and x["sample_day"] < cutoff]
            for row in stale:
                row.update(status=failed, last_error=error)
            self.rowcount, self.rows = len(stale), []
        elif s.startswith("UPDATE ai_qa_daily_samples"):
            status, error, increment, _status_again, sample_id = params
            row = next(x for x in db.samples if x["id"] == sample_id)
            row.update(status=status, last_error=error, attempts=row["attempts"] + increment)
            self.rows = []
        elif s.startswith("SELECT id, department_code, direction_id, family"):
            first, last, picked = params
            order = re.search(r"ORDER BY (.+)$", s).group(1).strip()
            assert order in ("sample_day, id", "sample_day DESC, id"), order
            rows = sorted(db.samples, key=lambda x: (x["sample_day"], x["id"]))
            if order.startswith("sample_day DESC"):
                rows = sorted(rows, key=lambda x: x["sample_day"], reverse=True)
            self.rows = [(x["id"], x["department"], x["direction_id"], x["family"],
                          x["subject_kind"], x["subject_id"], x["attempts"])
                         for x in rows
                         if first <= x["sample_day"] <= last and x["status"] == picked]
        else:
            raise AssertionError(f"неожиданный SQL: {s[:90]}")


class _DbCase(unittest.TestCase):
    def use(self, db):
        self.db = db
        for name in ("connect_rw", "connect_ro"):
            patcher = mock.patch.object(config, name, db.connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        return db


def _call(key, direction_id=73):
    return {"key": key, "direction_id": direction_id, "operator_id": 7}


class SampleDayTests(unittest.TestCase):
    def test_yesterday_is_taken_by_almaty_clock(self):
        # 30.09 03:30 по Алматы — по UTC это ещё 29.09 22:30.
        now = datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(ds.sample_day(now), date(2026, 9, 29))
        self.assertEqual(ds.sample_day(datetime(2026, 9, 30, 12, 0, tzinfo=ds.ALMATY)),
                         date(2026, 9, 29))

    def test_day_parsing(self):
        self.assertEqual(ds.parse_day("2026-09-29"), DAY)
        self.assertEqual(ds.parse_day(DAY), DAY)
        with self.assertRaises(ValueError):
            ds.parse_day("29.09.2026")


class CallSamplingTests(_DbCase):
    def setUp(self):
        self.use(FakeDB(departments={73: "op", 74: "op", 70: "szov"}))
        self.imported = []

    def source(self, candidates, fail_keys=(), none_keys=()):
        counter = iter(range(1000, 9999))
        self.attempted = []

        def import_call(candidate):
            self.attempted.append(candidate["key"])
            if candidate["key"] in fail_keys:
                raise RuntimeError("АТС недоступна")
            self.imported.append(candidate["key"])
            return None if candidate["key"] in none_keys else next(counter)
        return ds.CallSource(department="op", candidates=lambda day: candidates,
                             import_call=import_call, audio_state=lambda i: ds.AUDIO_READY)

    def test_each_direction_is_filled_up_to_size_and_foreign_directions_are_dropped(self):
        calls = ([_call(f"a{i}", 73) for i in range(10)] + [_call(f"b{i}", 74) for i in range(2)]
                 + [_call(f"c{i}", 70) for i in range(5)] + [_call("d0", None)])
        added = ds._sample_calls(DAY, self.source(calls), 3, random.Random(1))
        self.assertEqual(added, {73: 3, 74: 2})
        self.assertFalse(any(k.startswith(("c", "d")) for k in self.imported))
        self.assertEqual({s["family"] for s in self.db.samples}, {ds.FAMILY_CALLS})
        self.assertEqual({s["subject_kind"] for s in self.db.samples}, {config.SUBJECT_IMPORTED_CALL})

    def test_second_pass_tops_up_instead_of_taking_more(self):
        calls = [_call(f"a{i}", 73) for i in range(10)]
        ds._sample_calls(DAY, self.source(calls), 3, random.Random(1))
        self.assertEqual(ds._sample_calls(DAY, self.source(calls), 3, random.Random(2)), {})
        self.db.samples[0]["status"] = ds.STATUS_FAILED
        self.assertEqual(ds._sample_calls(DAY, self.source(calls), 3, random.Random(3)), {73: 1})
        self.assertEqual(len(self.db.samples), 4)

    def test_calls_already_in_the_pool_are_not_downloaded_again(self):
        self.db.imported = {"a0", "a1"}
        ds._sample_calls(DAY, self.source([_call("a0"), _call("a1"), _call("a2")]), 5,
                         random.Random(1))
        self.assertEqual(self.imported, ["a2"])

    def test_unusable_call_is_skipped_for_the_next_one(self):
        added = ds._sample_calls(DAY, self.source([_call("a0"), _call("a1")], none_keys={"a0", "a1"}),
                                 1, random.Random(1))
        self.assertEqual(added, {})
        self.assertEqual(sorted(self.imported), ["a0", "a1"])

    def test_downloads_stop_at_the_pass_deadline(self):
        calls = [_call(f"a{i}", 73) for i in range(5)]
        added = ds._sample_calls(DAY, self.source(calls), 5, random.Random(1),
                                 deadline=10, monotonic=lambda: 10)
        self.assertEqual(added, {})
        self.assertEqual(self.attempted, [])

    def test_broken_pbx_is_left_alone_after_three_failures_in_a_row(self):
        calls = ([_call(f"a{i}", 73) for i in range(10)] + [_call(f"b{i}", 74) for i in range(10)])
        added = ds._sample_calls(DAY, self.source(calls, fail_keys={c["key"] for c in calls}), 5,
                                 random.Random(1))
        self.assertEqual(added, {})
        # три отказа подряд — и отдел брошен целиком, соседнее направление тоже
        self.assertEqual(len(self.attempted), 3)
        self.assertEqual(self.db.samples, [])

    def test_failures_between_successes_do_not_stop_the_department(self):
        calls = [_call(f"a{i}", 73) for i in range(8)]
        keep_order = SimpleNamespace(shuffle=lambda pool: None)
        added = ds._sample_calls(DAY, self.source(calls, fail_keys={"a1", "a3", "a5"}), 4,
                                 keep_order)
        # Три отказа, но не подряд: АТС жива, отдел добирается до конца.
        self.assertEqual(added, {73: 4})
        self.assertEqual(self.attempted, [f"a{i}" for i in range(7)])   # набрали — остановились


class ChatSamplingTests(_DbCase):
    def test_chat_pool_of_the_section_is_narrowed_to_the_direction_and_day(self):
        self.use(FakeDB(canonical=[71], chat_ids=[11, 12, 13, 14]))
        self.db.samples.append({"id": 1, "sample_day": DAY, "department": "op", "direction_id": 71,
                                "family": ds.FAMILY_CHATS, "subject_kind": "wz_episode",
                                "subject_id": 10, "status": ds.STATUS_PICKED, "attempts": 0,
                                "last_error": None})
        with mock.patch.object(subjects_mod, "chat_direction_family", return_value=[71]):
            added = ds._sample_chats(DAY, "op", 3)
        self.assertEqual(added, {71: 2})
        sql, params = self.db.chat_sql[0]
        # Тот же пул, что «Случайный чат»: гейт атрибуции, направление и день
        # через _pick_filters_predicate, без уже выбранных и уже оценённых этой моделью.
        self.assertIn("t.operator_share >= %s", sql)
        self.assertIn("COALESCE(d.canonical_id, d.id) = %s", sql)
        self.assertIn("(t.ended_at AT TIME ZONE 'Asia/Almaty')::date >= %s", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM ai_qa_daily_samples s", sql)
        self.assertIn("rc.model = %s", sql)
        self.assertEqual(params[-1], 2)             # добор, а не N
        self.assertIn(config.CLAUDE_MODEL, params)
        self.assertEqual(params.count(DAY), 2)

    def test_department_without_chat_source_samples_nothing(self):
        self.use(FakeDB())
        self.assertEqual(ds._sample_chats(DAY, "front", 30), {})


class EvaluationTests(_DbCase):
    def setUp(self):
        self.use(FakeDB())
        self.clock = [0.0]
        self.evaluated = []
        self.audio = {}
        self.errors = {}
        self.not_in_queue = set()

    def add(self, subject_id, family=ds.FAMILY_CHATS, kind="wz_episode", day=DAY, attempts=0):
        self.db.samples.append({"id": len(self.db.samples) + 1, "sample_day": day, "department": "op",
                                "direction_id": 73, "family": family, "subject_kind": kind,
                                "subject_id": subject_id, "status": ds.STATUS_PICKED,
                                "attempts": attempts, "last_error": None})

    def evaluate(self, subject_id, kind):
        self.evaluated.append(subject_id)
        if subject_id in self.errors:
            raise self.errors[subject_id]

    def run_pass(self, deadline=10 ** 6, audio_wait=180):
        source = ds.CallSource(department="op", candidates=lambda day: [], import_call=lambda c: None,
                               audio_state=lambda i: self.audio.get(i, ds.AUDIO_PENDING))

        def sleep(seconds):
            self.clock[0] += seconds
            # запись доезжает, пока проход ждёт
            for key, when in list(self.audio.items()):
                if isinstance(when, float) and self.clock[0] >= when:
                    self.audio[key] = ds.AUDIO_READY
        with mock.patch.object(config, "AI_QA_DAILY_SAMPLE_AUDIO_WAIT_S", audio_wait):
            ds._evaluate_open(DAY, {"op": source}, self.evaluate,
                              lambda sid, kind: sid not in self.not_in_queue, deadline,
                              sleep=sleep, monotonic=lambda: self.clock[0])

    def status_of(self, subject_id):
        row = next(s for s in self.db.samples if s["subject_id"] == subject_id)
        return row["status"], row["attempts"]

    def test_outcomes(self):
        for subject_id in (1, 2, 3, 4):
            self.add(subject_id)
        self.errors = {2: RuntimeError("Z.ai 500"),
                       3: subjects_mod.SubjectNotEvaluable("отвечали двое", reason="share", detail={})}
        self.not_in_queue = {4}
        self.run_pass()
        self.assertEqual(self.status_of(1), (ds.STATUS_EVALUATED, 0))
        self.assertEqual(self.status_of(2), (ds.STATUS_PICKED, 1))    # повтор следующим проходом
        self.assertEqual(self.status_of(3), (ds.STATUS_FAILED, 1))    # замена сразу
        self.assertEqual(self.status_of(4), (ds.STATUS_PICKED, 1))    # проекции нет — повтор
        self.assertEqual(sorted(self.evaluated), [1, 2, 3, 4])        # по разу за проход

    def test_attempts_run_out(self):
        self.add(5, attempts=config.AI_QA_DAILY_SAMPLE_MAX_ATTEMPTS - 1)
        self.errors = {5: RuntimeError("timeout")}
        self.run_pass()
        self.assertEqual(self.status_of(5)[0], ds.STATUS_FAILED)

    def test_bridge_audio_is_awaited_within_the_pass(self):
        self.add(21, family=ds.FAMILY_CALLS, kind="imported_call")
        self.add(22, family=ds.FAMILY_CALLS, kind="imported_call")
        self.add(23, family=ds.FAMILY_CALLS, kind="imported_call")
        self.audio = {21: 120.0, 22: ds.AUDIO_MISSING}     # 21 доедет на второй минуте
        self.run_pass(audio_wait=300)
        self.assertEqual(self.status_of(21), (ds.STATUS_EVALUATED, 0))
        self.assertEqual(self.status_of(22), (ds.STATUS_FAILED, 1))
        self.assertEqual(self.status_of(23), (ds.STATUS_PICKED, 1))   # не доехала — попытка
        self.assertEqual(self.evaluated, [21])

    def test_older_days_are_finished_first_but_not_too_old(self):
        self.add(30)
        self.add(31, day=date(2026, 9, 27))
        self.add(32, day=date(2026, 9, 26))
        self.run_pass()
        self.assertEqual(self.evaluated, [31, 30])     # ближе к выходу из окна — раньше

    def test_tails_older_than_the_window_are_closed(self):
        self.add(33, day=date(2026, 9, 26))
        self.add(34, day=date(2026, 9, 27))
        self.assertEqual(ds._expire_stale(DAY), 1)
        self.assertEqual(self.status_of(33), (ds.STATUS_FAILED, 0))
        self.assertEqual(self.status_of(34), (ds.STATUS_PICKED, 0))

    def test_bridge_call_joins_the_work_as_soon_as_its_audio_arrives(self):
        for subject_id in range(1, 9):
            self.add(subject_id)
        self.add(9, family=ds.FAMILY_CALLS, kind="imported_call")
        for subject_id in range(10, 21):
            self.add(subject_id)
        evaluate = self.evaluate

        def slow_evaluate(subject_id, kind):
            evaluate(subject_id, kind)
            self.clock[0] += 60
            if self.clock[0] >= 120:
                self.audio[9] = ds.AUDIO_READY      # мост привёз запись, пока шла оценка
        self.evaluate = slow_evaluate
        with mock.patch.object(config, "AI_QA_DAILY_SAMPLE_WORKERS", 1):
            self.run_pass(audio_wait=10 ** 5)
        # Порция — 4 разговора: звонок встаёт в работу в третьей порции, а не
        # после всех двадцати чатов.
        self.assertLess(self.evaluated.index(9), self.evaluated.index(13))
        self.assertEqual(sorted(self.evaluated), list(range(1, 21)))

    def test_deadline_leaves_work_for_the_next_pass(self):
        self.add(41)
        self.run_pass(deadline=0)
        self.assertEqual(self.evaluated, [])
        self.assertEqual(self.status_of(41), (ds.STATUS_PICKED, 0))


class PassTests(_DbCase):
    def test_second_instance_does_nothing(self):
        self.use(FakeDB(locked=True))
        with mock.patch.object(ds, "_sample_chats") as chats, \
                mock.patch.object(ds, "_evaluate_open") as evaluate:
            result = ds.run(DAY, call_sources=[])
        self.assertEqual(result["status"], "skipped")
        chats.assert_not_called()
        evaluate.assert_not_called()

    def test_one_broken_department_does_not_cost_the_others(self):
        self.use(FakeDB())
        seen = []

        def chats(day, department, size):
            seen.append(department)
            if department == "op":
                raise RuntimeError("пул недоступен")
            return {}
        broken = ds.CallSource(department="szov", candidates=mock.Mock(side_effect=RuntimeError("Oktell")),
                               import_call=None, audio_state=None)
        with mock.patch.object(config, "DEPARTMENT_CODES", ("op", "szov", "tez")), \
                mock.patch.object(ds, "_sample_chats", side_effect=chats), \
                mock.patch.object(ds, "_evaluate_open") as evaluate, \
                mock.patch.object(ds, "status", return_value={"totals": {}}):
            result = ds.run(DAY, call_sources=[broken])
        self.assertEqual(seen, ["op", "szov", "tez"])
        evaluate.assert_called_once()
        self.assertEqual(result["status"], "success")


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8")

    def test_table_is_unique_per_subject(self):
        body = self.schema[self.schema.index("CREATE TABLE IF NOT EXISTS ai_qa_daily_samples"):]
        body = body[:body.index(");")]
        self.assertIn("UNIQUE (subject_kind, subject_id)", body)
        self.assertIn("CHECK (family IN ('calls', 'chats'))", body)
        kinds = set(re.findall(r"'(\w+)'", body[body.index("subject_kind"):body.index("subject_id")]))
        # Новый вид субъекта, не заведённый здесь, упал бы на INSERT посреди ночного прохода.
        self.assertEqual(kinds, set(config.SUBJECT_KINDS))


class DatabaseWiringTests(unittest.TestCase):
    """Строки выборки не видны журналу и «Делению звонков»."""

    @classmethod
    def setUpClass(cls):
        cls.source = source_cache.read(ROOT / "database.py")

    def body(self, name):
        start = self.source.index(f"    def {name}(")
        end = self.source.index("\n    def ", start + 10)
        return self.source[start:end]

    def test_import_takes_the_sample_status(self):
        body = self.body("import_single_random_call")
        self.assertIn("status=None", body)
        self.assertIn("status or 'not_evaluated'", body)

    def test_journal_and_distribution_see_only_their_statuses(self):
        self.assertIn("WHERE ic.operator_id = %s AND ic.status = 'not_evaluated'", self.source)
        counts = self.body("get_imported_calls_status_counts_by_operator")
        self.assertIn("COUNT(*) FILTER (WHERE status <> 'ai_sample') AS total", counts)
        self.assertIn("COUNT(*) FILTER (WHERE status = 'not_evaluated') AS not_evaluated", counts)
        self.assertEqual(ds.IMPORT_STATUS, "ai_sample")


def _load_function(source, name, namespace):
    node = next(item for item in source_cache.parse(source).body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<ai-qa-daily-sample>", "exec"), namespace)
    return namespace[name]


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AdapterTests(unittest.TestCase):
    """Адаптеры АТС в монолите — на подменах, без базы и телефонии."""

    @classmethod
    def setUpClass(cls):
        cls.source = source_cache.read(ROOT / "bot_schedule2.py")

    def test_cdr_candidates_trust_only_own_recordings_of_a_single_owner(self):
        from cdr import queries as cdr_queries, touches as cdr_touches
        rec = "http://192.168.88.251/recordings/2026/09/29/"
        rows = [
            {"linkedid": "1.1", "ext": "6728", "phone": "7773714269", "started_at": None,
             "call_type": cdr_touches.TYPE_IN, "talk_seconds": 90,
             "recording_url": rec + "external-6728-+77773714269-20260929-093225-1.1.wav"},
            # чужой агент группы вызова
            {"linkedid": "1.2", "ext": "6728", "phone": "7773714269", "started_at": None,
             "call_type": cdr_touches.TYPE_IN, "talk_seconds": 90,
             "recording_url": rec + "external-6665-+77773714269-20260929-093225-1.2.wav"},
            # тот же звонок второй строкой
            {"linkedid": "1.1", "ext": "6728", "phone": "7773714269", "started_at": None,
             "call_type": cdr_touches.TYPE_IN, "talk_seconds": 90,
             "recording_url": rec + "external-6728-+77773714269-20260929-093225-1.1.wav"},
            # номер у двоих действующих — чей звонок, не сказать
            {"linkedid": "1.3", "ext": "6000", "phone": "7001112233", "started_at": None,
             "call_type": cdr_touches.TYPE_OUT, "talk_seconds": 90,
             "recording_url": rec + "out-4503*+77001112233-6000-20260929-000617-1.3.wav"},
        ]
        fake_db = SimpleNamespace(
            get_all_operators=lambda: [
                (9, "Уволенный", 73, None, None, None, None, "", "", "fired"),
                # принят ПОСЛЕ дня выборки — звонки того дня на его номере не его
                (5, "Новичок", 73, date(2026, 9, 30), None, None, None, "", "", "working"),
                (1, "Айгерим", 73, datetime(2026, 1, 5, 0, 0), None, None, None, "", "", "working")],
            _get_cursor=lambda: _Cursor())
        namespace = {
            "db": fake_db, "CDR_CALL_DISTRIBUTION_DEPARTMENT_CODE": "op", "datetime": datetime,
            "_ai_qa_sample_durations": lambda: (60, 300),
            "_call_distribution_department_id_by_code": lambda code: 367,
            "_cdr_distribution_operators": lambda dep: [(1, "Айгерим", "6728"), (2, "Бота", "6000"),
                                                        (3, "Вера", "6000"), (9, "Уволенный", "6665"),
                                                        (5, "Новичок", "6999")],
        }
        fn = _load_function(self.source, "_ai_qa_sample_cdr_candidates", namespace)
        with mock.patch.object(cdr_queries, "sample_day_calls", return_value=rows) as sample, \
                mock.patch.object(ds, "operator_directions", return_value={1: 73}):
            candidates = fn(DAY)
        exts = sample.call_args.args[1]
        # общий номер, уволенный и принятый после дня выборки — вне выборки
        self.assertEqual(sorted(exts), ["6728"])
        self.assertEqual([c["key"] for c in candidates], ["1.1"])
        self.assertEqual(candidates[0]["direction_id"], 73)

    def test_cdr_audio_state(self):
        from cdr import queries as cdr_queries
        records = {1: {"audio_path": "b/x.wav"}, 2: {"audio_path": None}, 3: {"audio_path": None},
                   4: {"audio_path": None}}
        jobs = {2: {"status": "running"}, 3: {"status": "missing"}, 4: {"status": "error"}}
        namespace = {"db": SimpleNamespace(get_imported_call_audio=records.get,
                                           _get_cursor=lambda: _Cursor())}
        fn = _load_function(self.source, "_ai_qa_sample_cdr_audio_state", namespace)
        with mock.patch.object(cdr_queries, "audio_job_status_for_imported",
                               side_effect=lambda cursor, i: jobs.get(i)):
            self.assertEqual([fn(i) for i in (1, 2, 3, 4, 5)],
                             [ds.AUDIO_READY, ds.AUDIO_PENDING, ds.AUDIO_MISSING,
                              ds.AUDIO_MISSING, ds.AUDIO_MISSING])

    def test_binotel_candidates_filter_like_the_random_call(self):
        from tez import binotel_calls as tez_binotel_calls

        def call(gid, name, billsec=90, rec="uploaded", call_type=tez_binotel_calls.CALL_TYPE_OUTGOING):
            return {"general_call_id": gid, "employee_name": name, "billsec": billsec,
                    "recording_status": rec, "disposition": "ANSWER", "call_type": call_type,
                    "start_time": 1759123456, "external_number": "77001112233"}
        calls = [call("1", "Айгерим"), call("2", "Айгерим", billsec=0), call("3", "Айгерим", rec="none"),
                 call("4", "Айгерим", billsec=5000), call("5", "Незнакомец"), call("6", "Из СЗоВ"),
                 call("7", "Айгерим", call_type=tez_binotel_calls.CALL_TYPE_INCOMING)]
        client = SimpleNamespace(list_calls_for_day=lambda day: calls, format_dt=lambda ts: "29.09.2026 10:00:00")
        people = {"Айгерим": [{"id": 1, "name": "Айгерим"}], "Из СЗоВ": [{"id": 50, "name": "Из СЗоВ"}]}
        namespace = {
            "db": SimpleNamespace(), "TEZ_CALL_DISTRIBUTION_DEPARTMENT_CODE": "tez",
            "_ai_qa_sample_durations": lambda: (60, 300),
            "_ai_qa_sample_department_members": lambda code: {1},
            "_status_import_build_operator_lookup": lambda **kw: people,
            "_status_import_resolve_operator_matches": lambda name, lookup: lookup.get(name, []),
        }
        fn = _load_function(self.source, "_ai_qa_sample_binotel_candidates", namespace)
        with mock.patch.object(tez_binotel_calls, "get_config", return_value={}), \
                mock.patch.object(tez_binotel_calls, "api_ready", return_value=True), \
                mock.patch.object(tez_binotel_calls.BinotelApiClient, "from_config", return_value=client), \
                mock.patch.object(ds, "operator_directions", return_value={1: 83}):
            candidates = fn(DAY)
        self.assertEqual([c["key"] for c in candidates], ["1", "7"])
        self.assertEqual({c["direction_id"] for c in candidates}, {83})

    def test_binotel_import_without_recording_takes_no_row(self):
        imported = []
        namespace = {
            "db": SimpleNamespace(import_single_random_call=lambda **kw: imported.append(kw) or 7),
            "AI_QA_PULL_CALL_SOURCE": "aiqa",
            "_ai_qa_sample_month": lambda raw: "2026-09",
            "_binotel_fetch_record_to_gcs": lambda gid: None,
        }
        fn = _load_function(self.source, "_ai_qa_sample_binotel_import", namespace)
        candidate = {"key": "555", "operator_id": 1, "operator_name": "А", "dt_raw": "29.09.2026 10:00:00",
                     "call": {"external_number": "77001112233", "billsec": 90, "call_end_party": "client"}}
        self.assertIsNone(fn(candidate))
        self.assertEqual(imported, [])
        namespace["_binotel_fetch_record_to_gcs"] = lambda gid: "bucket/tez-1.mp3"
        self.assertEqual(fn(candidate), 7)
        self.assertEqual((imported[0]["audio_path"], imported[0]["status"], imported[0]["notes"]),
                         ("bucket/tez-1.mp3", ds.IMPORT_STATUS, "aiqa:auto:binotel"))

    def test_every_import_marks_the_row_as_sample_and_auto(self):
        for name in ("_ai_qa_sample_cdr_import", "_ai_qa_sample_oktell_import",
                     "_ai_qa_sample_binotel_import"):
            start = self.source.index(f"def {name}(")
            body = self.source[start:self.source.index("\ndef ", start + 10)]
            self.assertIn("status=qa_sample.IMPORT_STATUS", body, name)
            self.assertIn('f"{AI_QA_PULL_CALL_SOURCE}:auto:', body, name)

    def test_oktell_import_without_recording_takes_no_row(self):
        imported = []
        namespace = {
            "db": SimpleNamespace(import_single_random_call=lambda **kw: imported.append(kw) or 5),
            "AI_QA_PULL_CALL_SOURCE": "aiqa",
            "_ai_qa_sample_month": lambda raw: "2026-09",
            "_oktell_record_paths_by_conn": lambda ids: {},
            "_oktell_normalize_conn_id": lambda conn_id: conn_id,
            "_oktell_fetch_record_to_gcs": lambda conn_id, rel: None,
            "_oktell_call_end_party": lambda *a: "client",
        }
        fn = _load_function(self.source, "_ai_qa_sample_oktell_import", namespace)
        candidate = {"key": "c1", "operator_id": 1, "operator_name": "А",
                     "row": {"dt_raw": "29.09.2026 10:00:00", "phone": "7700", "talk_sec": 90}}
        self.assertIsNone(fn(candidate))
        self.assertEqual(imported, [])
        namespace["_oktell_fetch_record_to_gcs"] = lambda conn_id, rel: "bucket/oktell-c1.mp3"
        self.assertEqual(fn(candidate), 5)
        self.assertEqual(imported[0]["status"], ds.IMPORT_STATUS)
        self.assertEqual(imported[0]["notes"], "aiqa:auto:oktell")


class RouteTests(unittest.TestCase):
    """Ручка /api/ai-qa/daily-sample на настоящем Flask, без монолита."""

    def setUp(self):
        import copy
        from flask import Flask, jsonify, request
        self.app = Flask(__name__)
        self.submitted = []
        self.guard_error = None
        node = copy.deepcopy(source_cache.function_node(ROOT / "bot_schedule2.py",
                                                        "api_ai_qa_daily_sample"))
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        import threading
        from zoneinfo import ZoneInfo
        self.state = {}
        namespace = {
            "request": request, "jsonify": jsonify, "logging": mock.Mock(),
            "datetime": datetime, "ZoneInfo": ZoneInfo,
            "AI_QA_SAMPLE_STATE_LOCK": threading.Lock(), "AI_QA_SAMPLE_STATE": self.state,
            "_build_cors_preflight_response": lambda: ("", 204),
            "_ai_qa_admin_guard": lambda: (2, self.guard_error),
            "ai_qa_sample_pool": SimpleNamespace(submit=lambda *args: self.submitted.append(args)),
            "ai_qa_daily_sample_job": "job",
        }
        exec(compile(module, "<daily-sample-route>", "exec"), namespace)
        self.app.add_url_rule("/r", "r", namespace["api_ai_qa_daily_sample"],
                              methods=["GET", "POST", "OPTIONS"])
        self.client = self.app.test_client()
        patcher = mock.patch.object(ds, "sample_day", return_value=DAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_manual_run_goes_to_the_sample_pool(self):
        response = self.client.post("/r", json={"day": "2026-09-28"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "queued")
        self.assertEqual(self.submitted, [("job", date(2026, 9, 28), "manual:2")])
        self.assertEqual(self.state["state"], "queued")
        self.state.clear()                                             # проход закончился
        self.assertEqual(self.client.post("/r").status_code, 202)     # по умолчанию — вчера
        self.assertEqual(self.submitted[-1][1], DAY)

    def test_second_click_while_a_pass_is_queued_or_running_is_refused(self):
        self.assertEqual(self.client.post("/r").status_code, 202)
        second = self.client.post("/r", json={"day": "2026-09-28"})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["pass"]["state"], "queued")
        self.assertEqual(len(self.submitted), 1)
        self.state.update(state="running", day="2026-09-29", triggered_by="scheduler")
        with mock.patch.object(ds, "status", return_value={"day": "2026-09-29", "cells": []}):
            body = self.client.get("/r").get_json()
        self.assertEqual(body["pass"]["triggered_by"], "scheduler")

    def test_only_finished_days_and_valid_dates(self):
        self.assertEqual(self.client.post("/r", json={"day": "2026-09-30"}).status_code, 400)
        self.assertEqual(self.client.get("/r?day=30.09.2026").status_code, 400)
        self.assertEqual(self.submitted, [])

    def test_summary_and_guard(self):
        with mock.patch.object(ds, "status", return_value={"day": "2026-09-29", "cells": []}):
            body = self.client.get("/r?day=2026-09-29").get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["cells"], [])
        from flask import jsonify
        with self.app.app_context():
            self.guard_error = (jsonify({"error": "forbidden"}), 403)
        self.assertEqual(self.client.post("/r").status_code, 403)
        self.assertEqual(self.submitted, [])


class MonolithWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = source_cache.read(ROOT / "bot_schedule2.py")

    def test_schedule_runs_twice_in_the_morning_in_its_own_pool(self):
        self.assertIn("ai_qa_sample_pool = ThreadPoolExecutor(max_workers=1, "
                      "thread_name_prefix='ai-qa-sample')", self.source)
        wrapper = self.source[self.source.index("async def run_ai_qa_daily_sample_async"):]
        self.assertIn("run_in_executor(ai_qa_sample_pool, ai_qa_daily_sample_job)", wrapper[:400])
        job = self.source.index("id='ai_qa_daily_sample'")
        block = self.source[job - 400:job + 200]
        self.assertIn("run_ai_qa_daily_sample_async", block)
        self.assertIn("CronTrigger(hour='5,8', minute=10, timezone=ZoneInfo('Asia/Almaty'))", block)
        # При одном экземпляре APScheduler выбросил бы проход 08:10, пока идёт 05:10.
        self.assertIn("max_instances=2", block)

    def test_binotel_recording_is_fetched_before_the_pool_row(self):
        start = self.source.index("def _ai_qa_sample_binotel_import(")
        body = self.source[start:self.source.index("\ndef ", start + 10)]
        self.assertLess(body.index("_binotel_fetch_record_to_gcs("),
                        body.index("import_single_random_call("))

    def test_manual_run_is_super_admin_only_and_off_the_request_thread(self):
        route = self.source[self.source.index("def api_ai_qa_daily_sample"):]
        route = route[:route.index("\ndef ")]
        self.assertIn("_ai_qa_admin_guard()", route)
        self.assertIn("ai_qa_sample_pool.submit(ai_qa_daily_sample_job", route)
        self.assertIn("day > qa_sample.sample_day()", route)

    def test_switch_silences_only_the_schedule(self):
        job = self.source[self.source.index("def ai_qa_daily_sample_job"):]
        job = job[:job.index("\ndef ")]
        self.assertIn("triggered_by == 'scheduler' and not qa_config.AI_QA_DAILY_SAMPLE_ENABLED", job)


if __name__ == "__main__":
    unittest.main()
