# -*- coding: utf-8 -*-
"""Поиск фрода в звонках удалённого КЦ (задача SZOV-2): четыре шаблона из задачи,
звонки на прослушку сверх случайной выборки и раздел «Поиск фрода» в сводке.

Стережём:
* каждый шаблон — ровно по формулировке задачи и сказанным владельцу числам
  (границы 11/20, 30, 11–15, 11–12, 10 минут; «подряд» — недозвон не рвёт серию);
* итоги «подписал/подпишет» узнаются без регистра, но только эти три;
* необычная доля — не просто «вдвое больше», а ещё и неслучайно;
* звонки под шаблонами идут на прослушку СВЕРХ N случайных, по важности, до потолка;
* в сводке: разделы направлений и цифры — без взятых по триггеру, «Поиск фрода» —
  отдельным разделом сразу за направлением, без совпадений модель не зовётся;
* звонок базы удалённого КЦ принадлежит направлению «Обзвон — подписание АВР», а не
  отделу звонившего (scope_direction_id во всех выражениях отдела и скоупа).
"""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from call_qa import api, config, daily_sample as ds, fraud, remote_cc
from call_qa.digest import data as digest_data, prompts, service

DAY = date(2026, 10, 21)
T0 = datetime(2026, 10, 21, 10, 0, tzinfo=fraud.ALMATY)
AVR = config.REMOTE_CC_QA_DIRECTION_ID


def call(cid, op, sec, minute, *, lead=None, outcome="", by_operator=False, gid=None,
         signed_at=None, name=None):
    return {"id": cid, "operator_id": op, "operator": name or f"Оператор {op}",
            "lead_id": lead or f"lead-{cid}", "phone": "7770000000",
            "started_at": T0 + timedelta(minutes=minute), "billsec": sec,
            "operator_hung_up": by_operator, "end_party": "operator" if by_operator else "client",
            "outcome": outcome, "general_call_id": gid if gid is not None else f"g{cid}",
            "signed_at": signed_at}


class SeriesTests(unittest.TestCase):
    def test_two_in_a_row_between_11_and_20_seconds_make_a_series(self):
        calls = [call("a", 1, 15, 0), call("b", 1, 11, 1, by_operator=True), call("c", 1, 20, 2),
                 call("d", 1, 21, 3), call("e", 1, 12, 4)]
        found = fraud.series(calls)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["calls"], ["a", "b", "c"])
        self.assertEqual(found[0]["operator_ended"], 1)

    def test_borders_and_single_short_call(self):
        # 10 и 21 с — не 11–20; одиночный короткий разговор — не серия.
        calls = [call("a", 1, 10, 0), call("b", 1, 11, 1), call("c", 1, 21, 2),
                 call("d", 1, 20, 3)]
        self.assertEqual(fraud.series(calls), [])

    def test_other_operator_and_order_by_time(self):
        # Разговоры разных операторов серию не составляют; порядок — по времени, а не по вводу.
        calls = [call("b", 1, 15, 5), call("x", 2, 15, 1), call("a", 1, 15, 0)]
        found = fraud.series(calls)
        self.assertEqual([s["calls"] for s in found], [["a", "b"]])


class ShortPositiveTests(unittest.TestCase):
    def test_three_positive_outcomes_up_to_30_seconds(self):
        calls = [call("a", 1, 30, 0, outcome="Подписал в звонке"),
                 call("b", 1, 31, 1, outcome="Подписал в звонке"),
                 call("c", 2, 9, 2, outcome="  сообщил,  что подпишет "),
                 call("d", 2, 25, 3, outcome="Подпишет в офисе"),
                 call("e", 2, 20, 4, outcome="Перезвонить позже"),
                 call("f", 2, 20, 5, outcome="")]
        self.assertEqual([h["calls"][0] for h in fraud.short_positive(calls)], ["a", "c", "d"])

    def test_outcome_list_is_the_task_list(self):
        self.assertEqual(fraud.POSITIVE_OUTCOMES,
                         ("Подписал в звонке", "Сообщил, что подпишет", "Подпишет в офисе"))


class ShareTests(unittest.TestCase):
    def _day(self, own_short, own_total, rest_short, rest_total):
        calls = []
        for i in range(own_total):
            calls.append(call(f"o{i}", 1, 13 if i < own_short else 60, i))
        for i in range(rest_total):
            calls.append(call(f"r{i}", 2 + i % 5, 14 if i < rest_short else 90, i))
        return calls

    def test_unusual_share_is_flagged(self):
        # 6 из 39 против 5 из 142 (живой день 20.10) — вчетверо больше и неслучайно.
        found = fraud.share(self._day(6, 39, 5, 142))
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["short"], found[0]["talks"]), (6, 39))
        self.assertLess(found[0]["p_value"], 0.05)
        self.assertEqual(len(found[0]["calls"]), 6)

    def test_double_share_by_chance_is_not_flagged(self):
        # 3 из 48 против 4 из 141 (живой день 14.10): вдвое больше, но так бывает
        # примерно в каждом шестом дне — не «необычно».
        self.assertEqual(fraud.share(self._day(3, 48, 4, 141)), [])

    def test_fewer_than_three_or_nobody_to_compare(self):
        self.assertEqual(fraud.share(self._day(2, 5, 0, 100)), [])
        only_one = [call(f"o{i}", 1, 13, i) for i in range(10)]
        self.assertEqual(fraud.share(only_one), [])

    def test_15_and_16_seconds_border(self):
        calls = [call(f"o{i}", 1, 15, i) for i in range(5)] + \
                [call(f"r{i}", 2, 16, i) for i in range(100)]
        self.assertEqual(len(fraud.share(calls)), 1)
        calls = [call(f"o{i}", 1, 16, i) for i in range(5)] + \
                [call(f"r{i}", 2, 90, i) for i in range(100)]
        self.assertEqual(fraud.share(calls), [])

    def test_binomial_tail(self):
        self.assertAlmostEqual(fraud.binomial_tail(0, 10, 0.3), 1.0)
        self.assertAlmostEqual(fraud.binomial_tail(1, 2, 0.5), 0.75)
        self.assertEqual(fraud.binomial_tail(3, 10, 0.0), 0.0)


class RedialTests(unittest.TestCase):
    def test_short_first_call_then_same_driver_within_ten_minutes(self):
        calls = [call("a", 1, 11, 0, lead="L"), call("b", 1, 180, 2, lead="L"),
                 call("c", 1, 12, 20, lead="M"), call("d", 1, 40, 31, lead="M"),
                 call("e", 1, 13, 40, lead="N"), call("f", 1, 50, 41, lead="N")]
        found = fraud.redial(calls)
        self.assertEqual([r["calls"] for r in found], [["a", "b"]])
        self.assertEqual(found[0]["gap_s"], 120)

    def test_other_operator_calling_the_same_driver_is_not_a_redial(self):
        calls = [call("a", 1, 11, 0, lead="L"), call("b", 2, 180, 2, lead="L")]
        self.assertEqual(fraud.redial(calls), [])


class FindTests(unittest.TestCase):
    def test_flagged_calls_are_unique_and_ordered_by_kind(self):
        calls = [call("a", 1, 15, 0, outcome="Подписал в звонке"), call("b", 1, 15, 1),
                 call("c", 2, 60, 2)]
        found = fraud.find(calls)
        self.assertEqual(found["talks"], 3)
        self.assertEqual(found["operators"], 2)
        flagged = fraud.flagged_calls(found)
        # «a» — и шаблон 2, и серия; первым идёт более важный шаблон.
        self.assertEqual(flagged, [("a", ["short_positive", "series"]), ("b", ["series"])])
        stats = fraud.public_stats(found)
        self.assertEqual(stats["short_positive"], {"hits": 1, "calls": 1, "operators": 1})
        self.assertEqual(stats["series"], {"hits": 1, "calls": 2, "operators": 1})
        self.assertEqual(stats["flagged"], 2)

    def test_day_bounds_are_almaty(self):
        start, end = fraud.day_bounds(DAY)
        self.assertEqual(start.utcoffset(), timedelta(hours=5))
        self.assertEqual(end - start, timedelta(days=1))


class LoadDayTests(unittest.TestCase):
    def test_load_day_reads_only_conversations_of_the_remote_base(self):
        class Cur:
            def execute(self, sql, params):
                self.sql, self.params = " ".join(sql.split()), params

            def fetchall(self):
                return [("att-1", 5, " Иванова Анна ", "lead-1", "77011234567",
                         T0, 14, True, True, "Подписал в звонке", 123456, None),
                        ("att-2", 6, "", "lead-2", "77011234568", T0, 40, False, False, "",
                         None, None)]
        cur = Cur()
        calls = fraud.load_day(cur, DAY, 1954)
        self.assertIn("l.department_id = %s", cur.sql)
        self.assertIn("NOT t.cancelled AND t.billsec > 0", cur.sql)
        self.assertIn("t.auto_hangup <> 'announcement'", cur.sql)   # не сообщение сети
        self.assertIn("NOT EXISTS (SELECT 1 FROM", cur.sql)          # тестовые номера
        self.assertEqual(cur.params[0], 1954)
        self.assertEqual(calls[0]["operator"], "Иванова Анна")
        self.assertEqual(calls[0]["end_party"], "operator")
        self.assertEqual(calls[0]["general_call_id"], "123456")
        self.assertEqual(calls[1]["operator"], "—")
        self.assertEqual(calls[1]["end_party"], "unknown")
        self.assertIsNone(calls[1]["general_call_id"])


class RemoteCcSourceTests(unittest.TestCase):
    def setUp(self):
        self.calls = [call("a", 1, 15, 0, outcome="Подписал в звонке"),
                      call("b", 1, 15, 1, gid=""), call("c", 2, 4, 2), call("d", 2, 60, 3)]
        patcher = mock.patch.object(remote_cc, "_load", return_value=(1954, self.calls))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_candidates_need_a_recording_and_five_seconds(self):
        keys = [c["key"] for c in remote_cc.candidates(DAY)]
        self.assertEqual(keys, ["ga", "gd"])
        first = remote_cc.candidates(DAY)[0]
        self.assertEqual(first["direction_id"], AVR)
        self.assertEqual(first["dt_raw"], "21.10.2026 10:00:00")
        self.assertEqual(first["department_id"], 1954)

    def test_triggered_skips_calls_without_recording(self):
        triggered = remote_cc.triggered(DAY)
        self.assertEqual([c["key"] for c in triggered], ["ga"])
        self.assertEqual(triggered[0]["reasons"], ["short_positive", "series"])

    def test_switched_off_without_direction(self):
        with mock.patch.object(config, "REMOTE_CC_QA_DIRECTION_ID", 0):
            self.assertEqual(remote_cc.candidates(DAY), [])
            self.assertEqual(remote_cc.triggered(DAY), [])

    def test_import_marks_scale_and_owner_direction(self):
        candidate = remote_cc.candidates(DAY)[0]
        imported = mock.Mock(return_value=77)
        fetch = mock.Mock(return_value="bucket/remote-cc-1.mp3")
        self.assertEqual(remote_cc.import_candidate(candidate, fetch_record=fetch,
                                                    import_call=imported, status="ai_sample"), 77)
        fetch.assert_called_once_with(1954, "ga")
        kwargs = imported.call_args.kwargs
        self.assertEqual(kwargs["qa_direction_id"], AVR)
        self.assertEqual(kwargs["scope_direction_id"], AVR)
        self.assertEqual(kwargs["notes"], remote_cc.NOTES)
        self.assertEqual(kwargs["month"], "2026-10")
        self.assertEqual(kwargs["status"], "ai_sample")
        self.assertTrue(remote_cc.NOTES.endswith(":" + remote_cc.NOTES_TAIL))

    def test_no_recording_no_row(self):
        candidate = remote_cc.candidates(DAY)[0]
        imported = mock.Mock()
        self.assertIsNone(remote_cc.import_candidate(candidate, fetch_record=lambda *a: None,
                                                     import_call=imported, status="ai_sample"))
        imported.assert_not_called()


class SubjectDirectionSqlTests(unittest.TestCase):
    """Звонок базы удалённого КЦ — разговор направления, а не звонившего."""

    def test_scope_direction_comes_before_the_operator_direction(self):
        for expr in (api._SUBJECT_DIRECTION, api._SUBJECT_JOIN):
            self.assertLess(expr.index("ic.scope_direction_id"), expr.index("ui.direction_id"), expr)
        self.assertIn("COALESCE(ic.scope_direction_id, u.direction_id)",
                      api._SCOPE_SQL[config.SUBJECT_IMPORTED_CALL])
        # Шкала — своя пометка звонка, она главнее всего остального.
        self.assertTrue(api._SUBJECT_SCALE_DIRECTION.startswith("COALESCE(ic.qa_direction_id"))

    def test_trigger_pick_predicate(self):
        self.assertIn("pick_reason = 'trigger'", api._SUBJECT_TRIGGER_PICK)
        self.assertEqual(api._SUBJECT_NOT_TRIGGER_PICK, "NOT " + api._SUBJECT_TRIGGER_PICK)


class TriggeredSamplingTests(unittest.TestCase):
    """Звонки под триггерами — сверх N, по важности, до потолка, без повторов."""

    def setUp(self):
        from tests.test_ai_qa_daily_sample import FakeDB
        self.db = FakeDB(departments={AVR: "szov", 70: "szov"})
        for name in ("connect_rw", "connect_ro"):
            patcher = mock.patch.object(config, name, self.db.connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.counter = iter(range(5000, 9000))

    def source(self, random_calls, triggered_calls):
        def import_call(candidate):
            self.db.imported.add(candidate["key"])
            return next(self.counter)
        return ds.CallSource(department="szov", candidates=lambda day: random_calls,
                             import_call=import_call, audio_state=lambda i: ds.AUDIO_READY,
                             triggered=lambda day: triggered_calls)

    @staticmethod
    def cand(key):
        return {"key": key, "direction_id": AVR, "operator_id": 1}

    def test_trigger_calls_go_beyond_the_random_sample(self):
        randoms = [self.cand(f"r{i}") for i in range(5)]
        triggered = [self.cand("r0"), self.cand("t1"), self.cand("t2")]
        source = self.source(randoms, triggered)
        added = ds._sample_calls(DAY, source, 3, __import__("random").Random(1))
        self.assertEqual(added, {AVR: 3})
        taken_random = {s["subject_id"] for s in self.db.samples}
        self.db.imported.update({"r0"})      # r0 мог попасть в случайные — он уже в пуле
        extra = ds._sample_triggered(DAY, source)
        reasons = [s["pick_reason"] for s in self.db.samples]
        self.assertEqual(reasons.count(ds.PICK_RANDOM), 3)
        self.assertEqual(reasons.count(ds.PICK_TRIGGER), sum(extra.values()))
        self.assertGreaterEqual(sum(extra.values()), 2)
        self.assertTrue(taken_random)
        # N случайных не сдвинулся: триггерные в счёт ячейки не идут.
        conn = self.db.connect()
        with conn.cursor() as cur:
            self.assertEqual(ds._cell_counts(cur, DAY), {(AVR, ds.FAMILY_CALLS): 3})

    def test_second_pass_does_not_take_them_again_and_cap_is_respected(self):
        triggered = [self.cand(f"t{i}") for i in range(10)]
        source = self.source([], triggered)
        with mock.patch.object(config, "AI_QA_FRAUD_TRIGGER_DAILY_CAP", 4), \
                self.assertLogs(level="WARNING") as logs:
            self.assertEqual(ds._sample_triggered(DAY, source), {AVR: 4})
        self.assertIn("потолок 4", "\n".join(logs.output))
        with mock.patch.object(config, "AI_QA_FRAUD_TRIGGER_DAILY_CAP", 4):
            self.assertEqual(ds._sample_triggered(DAY, source), {})
        # Взяты первые по важности — порядок источника.
        keys = [k for k in ("t0", "t1", "t2", "t3")]
        self.assertTrue(all(k in self.db.imported for k in keys))

    def test_foreign_direction_is_not_taken(self):
        source = self.source([], [{"key": "x", "direction_id": 999, "operator_id": 1}])
        self.assertEqual(ds._sample_triggered(DAY, source), {})

    def test_run_takes_both_sources_of_a_department(self):
        oktell = ds.CallSource(department="szov", candidates=lambda day: [
                                   {"key": "o1", "direction_id": 70, "operator_id": 2}],
                               import_call=lambda c: next(self.counter),
                               audio_state=lambda i: ds.AUDIO_READY)
        remote = self.source([self.cand("r1")], [self.cand("t1")])
        with mock.patch.object(config, "DEPARTMENT_CODES", ("szov",)), \
                mock.patch.object(ds, "_sample_chats", return_value={}), \
                mock.patch.object(ds, "_evaluate_open"), \
                mock.patch.object(ds, "status", return_value={}), \
                mock.patch.object(ds, "_expire_stale", return_value=0):
            result = ds.run(DAY, call_sources=[oktell, remote], evaluate=lambda *a: None,
                            in_queue=lambda *a: True)
        self.assertEqual(result["added"]["szov"][ds.FAMILY_CALLS], {70: 1, AVR: 1})
        self.assertEqual(result["added"]["szov"][ds.PICK_TRIGGER], {AVR: 1})


def talk(ref, *, trigger=False, direction=AVR, kind="imported_call", subject_id=None,
         operator="Тулегенулы Максат", score=40.0):
    return {"kind": kind, "id": subject_id or 100 + ref, "model": "m", "family": "calls",
            "operator": operator, "operator_id": 9, "time": f"10:{ref:02d}",
            "subject_direction": direction, "scale_direction": direction,
            "direction": "Обзвон — подписание АВР" if direction == AVR else "Основа",
            "human_score": None, "outcome": None, "run_id": f"run-{ref}", "trigger": trigger,
            "criteria": [], "ai_score": score, "unchecked_weight": 0, "asr_conf": 0.9,
            "media_failed": 0, "end_party": "operator", "transcript": "Г1: Алло\nГ2: Да",
            "overall_comment": "", "ref": ref, "human": {}}


class DigestFraudSectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = {"a": call("a", 9, 20, 0, outcome="Сообщил, что подпишет", by_operator=True,
                                name="Тулегенулы Максат"),
                      "b": call("b", 9, 14, 5, name="Тулегенулы Максат"),
                      "c": call("c", 9, 16, 6, name="Тулегенулы Максат")}
        found = fraud.find(list(self.calls.values()))
        self.fraud_day = {"calls": self.calls, "found": found, "subjects": {"a": 102}}
        # #1 — случайный звонок СЗоВ «Основы», #2 — звонок удалённого КЦ под триггером,
        # #3 — случайный звонок удалённого КЦ.
        self.talks = [talk(1, direction=70, operator="Иванова Анна", score=90.0),
                      talk(2, trigger=True, score=10.0), talk(3, score=80.0)]

    def _read(self, digest=None, fraud_day="default"):
        fraud_day = self.fraud_day if fraud_day == "default" else fraud_day
        return mock.patch.object(service, "_read_day", return_value=(
            [dict(t) for t in self.talks], {}, "СЗоВ", digest, fraud_day))

    def test_fraud_section_follows_its_direction_and_trigger_talks_stay_out_of_numbers(self):
        systems, users = [], []

        def fake(system, user, **kwargs):
            systems.append(system)
            users.append(user)
            if "раздел «Поиск фрода»" in system:
                return ("КРАТКО: У одного оператора короткие разговоры с «подпишет»\n"
                        "СВОДКА:\n<p>Тулегенулы Максат [[#2]], чужой [[#1]]</p>",
                        {"provider": "vertex", "model": "m"})
            if "Напиши раздел «Главное»" in system:
                return "КРАТКО: Главное\nСВОДКА:\n<p>Обзор</p>", {"provider": "vertex", "model": "m"}
            return "КРАТКО: Раздел\nСВОДКА:\n<p>Текст</p>", {"provider": "vertex", "model": "m"}

        saved = {}
        with self._read(), mock.patch.object(service.store, "claim", return_value=True), \
                mock.patch.object(service.store, "save", side_effect=lambda *a, **k: saved.update(k)), \
                mock.patch.object(service.store, "release"):
            result = service.generate("szov", DAY, generate_fn=fake)
        self.assertEqual(result["status"], "ready")
        keys = [s["key"] for s in saved["sections"]]
        self.assertEqual(keys, [str(AVR), prompts.FRAUD_KEY, "70"])
        avr = saved["sections"][0]
        # Звонок по триггеру (#2, балл 10) не тянет вниз цифры направления.
        self.assertEqual(avr["stats"]["evaluated"], 1)
        self.assertEqual(avr["stats"]["ai_avg"], 80.0)
        self.assertEqual(saved["stats"]["department"]["evaluated"], 2)
        fraud_entry = saved["sections"][1]
        self.assertEqual(fraud_entry["kind"], prompts.FRAUD_KIND)
        self.assertEqual(fraud_entry["subject_directions"], [AVR])
        self.assertEqual(fraud_entry["stats"]["short_positive"]["hits"], 1)
        # Ссылка на чужой разговор (#1 — «Основа») в «Поиске фрода» не живёт.
        self.assertEqual(fraud_entry["refs"], ["imported_call:102"])
        self.assertNotIn('data-qa-ref="1"', fraud_entry["html"])
        fraud_user = next(u for s, u in zip(systems, users) if "раздел «Поиск фрода»" in s)
        self.assertIn("ОПЕРАТОР: Тулегенулы Максат", fraud_user)
        self.assertIn("в Sapar пока не подписал", fraud_user)
        self.assertIn("#2", fraud_user)
        self.assertIn("не прослушан", fraud_user)          # «b», «c» ИИ не слушал
        self.assertIn("взят на проверку по триггеру фрода", fraud_user)
        # В разделе направления звонка по триггеру нет.
        avr_user = next(u for s, u in zip(systems, users)
                        if "«Обзвон — подписание АВР»" in s and "Поиск фрода" not in s)
        self.assertNotIn("#2 ", avr_user)

    def test_no_hits_no_model_call(self):
        quiet = {"calls": {"x": call("x", 9, 90, 0)}, "found": fraud.find([call("x", 9, 90, 0)]),
                 "subjects": {}}
        systems = []

        def fake(system, user, **kwargs):
            systems.append(system)
            return "КРАТКО: Раздел\nСВОДКА:\n<p>Текст</p>", {"provider": "vertex", "model": "m"}

        saved = {}
        with self._read(fraud_day=quiet), mock.patch.object(service.store, "claim", return_value=True), \
                mock.patch.object(service.store, "save", side_effect=lambda *a, **k: saved.update(k)), \
                mock.patch.object(service.store, "release"):
            service.generate("szov", DAY, generate_fn=fake)
        self.assertFalse(any("раздел «Поиск фрода»" in s for s in systems))
        entry = next(s for s in saved["sections"] if s["key"] == prompts.FRAUD_KEY)
        self.assertIn("не сработал", entry["html"])

    def test_same_hits_keep_the_text_and_failure_keeps_the_old_one(self):
        kept = {"key": prompts.FRAUD_KEY, "kind": prompts.FRAUD_KIND, "html": "<p>прежний</p>",
                "headline": "прежний", "refs": [2]}
        talks = [dict(t) for t in self.talks]
        refs = {t["ref"]: {"label": "x"} for t in talks}
        common = dict(force=False, usage={}, models=__import__("collections").Counter(), errors=[])
        fresh, _ = service._fraud_section(DAY, "szov", "СЗоВ", self.fraud_day, talks, refs, None,
                                          generate_fn=lambda *a, **k: (
                                              "КРАТКО: x\nСВОДКА:\n<p>новый [[#2]]</p>",
                                              {"provider": "p", "model": "m"}), **common)
        same, called = service._fraud_section(
            DAY, "szov", "СЗоВ", self.fraud_day, talks, refs,
            {**kept, "inputs_hash": fresh["inputs_hash"]},
            generate_fn=mock.Mock(side_effect=AssertionError("модель не нужна")), **common)
        self.assertFalse(called)
        self.assertEqual(same["html"], "<p>прежний</p>")
        errors = []
        broken, _ = service._fraud_section(
            DAY, "szov", "СЗоВ", self.fraud_day, talks, refs, {**kept, "inputs_hash": "old"},
            generate_fn=mock.Mock(side_effect=RuntimeError("429")), force=False, usage={},
            models=__import__("collections").Counter(), errors=errors)
        self.assertTrue(broken["outdated"])
        self.assertEqual(broken["html"], "<p>прежний</p>")
        self.assertTrue(errors)

    def test_read_shows_fraud_section_only_to_those_who_see_the_direction(self):
        stats = fraud.public_stats(self.fraud_day["found"])
        digest = {"status": "ready", "inputs_hash": digest_data.inputs_hash(self.talks),
                  "inputs_count": 3, "sections": [
                      {"key": str(AVR), "direction": "Обзвон — подписание АВР", "family": "calls",
                       "subject_directions": [AVR], "html": "<p>a</p>", "headline": "a",
                       "inputs_hash": digest_data.inputs_hash([self.talks[2]]), "inputs_count": 1},
                      {"key": "70", "direction": "Основа", "family": "calls",
                       "subject_directions": [70], "html": "<p>b</p>", "headline": "b",
                       "inputs_hash": digest_data.inputs_hash([self.talks[0]]), "inputs_count": 1},
                      {"key": prompts.FRAUD_KEY, "kind": prompts.FRAUD_KIND,
                       "direction": prompts.FRAUD_TITLE, "family": "calls",
                       "subject_directions": [AVR], "html": "<p>f</p>", "headline": "f",
                       "stats": stats, "inputs_hash": "h", "inputs_count": 1}],
                  "overview_html": "<p>o</p>", "headline": "o"}

        class Conn:
            def cursor(self):
                return mock.Mock()

            def close(self):
                pass

        with mock.patch.object(service.config, "connect_ro", return_value=Conn()), \
                mock.patch.object(service.digest_data, "collect_day",
                                  return_value=[dict(t) for t in self.talks]), \
                mock.patch.object(service.store, "get", return_value=digest), \
                mock.patch.object(service, "_baseline", return_value={}):
            head = service.read("szov", DAY, None)
            sv_other = service.read("szov", DAY, [70])
            sv_avr = service.read("szov", DAY, [70, AVR])
        self.assertEqual([s["key"] for s in head["sections"]], [str(AVR), "70", prompts.FRAUD_KEY])
        self.assertFalse(head["stale"])
        fraud_view = head["sections"][2]
        self.assertEqual(fraud_view["kind"], prompts.FRAUD_KIND)
        self.assertEqual(fraud_view["stats"], stats)
        # Цифры направления — без звонка по триггеру; «прочитано» — со всеми.
        self.assertEqual(head["sections"][0]["stats"]["evaluated"], 1)
        self.assertEqual(head["evaluated"], 3)
        self.assertNotIn(prompts.FRAUD_KEY, [s["key"] for s in sv_other["sections"]])
        self.assertIn(prompts.FRAUD_KEY, [s["key"] for s in sv_avr["sections"]])
        self.assertFalse(sv_avr["stale"])


class ModelOutputGuardTests(unittest.TestCase):
    """Страховки по живому прогону 21.10: модель ссылалась временем, называла фамилию
    в «КРАТКО» и повторяла число из ряда над разделом."""

    def test_time_in_ref_brackets_becomes_plain_time(self):
        from call_qa.digest import render
        self.assertEqual(render.split_ref_groups("серия [[#12:06]], [[ 12:07 ]] и [[#3]]"),
                         "серия 12:06, 12:07 и [[#3]]")

    def test_headline_with_operator_name_is_replaced(self):
        found = fraud.find([call("a", 1, 20, 0, outcome="Подписал в звонке",
                                 name="Тулегенулы Максат"),
                            call("b", 2, 25, 1, outcome="Подпишет в офисе", name="Капарова Асель")])
        self.assertEqual(service._fraud_headline("Признаки накрутки у Тулегенулы Максата", found),
                         "Шаблоны фрода сработали у 2 операторов")
        self.assertEqual(service._fraud_headline("Короткие «подпишет» без согласия водителя", found),
                         "Короткие «подпишет» без согласия водителя")

    def test_header_numbers_are_dropped_but_per_operator_shares_stay(self):
        from call_qa.digest import render
        stats = {"talks": 323, "operators": 13,
                 "short_positive": {"hits": 42}, "series": {"hits": 6}, "share": {"hits": 0},
                 "redial": {"hits": 0}}
        html = ('<div data-wiki-block="stats">'
                '<div data-wiki-block="stat"><h4>42</h4><p>подозрительных итога</p></div>'
                '<div data-wiki-block="stat"><h4>19 из 72</h4><p>у оператора</p></div></div>')
        out = render.drop_fraud_header_stats(html, stats)
        self.assertNotIn(">42<", out)
        self.assertIn("19 из 72", out)
        only = ('<div data-wiki-block="stats"><div data-wiki-block="stat"><h4>6</h4>'
                '<p>серий</p></div></div><p>текст</p>')
        self.assertEqual(render.drop_fraud_header_stats(only, stats), "<p>текст</p>")

    def test_totals_are_given_ready(self):
        calls = {"a": call("a", 1, 20, 0, outcome="Подписал в звонке", by_operator=True,
                           signed_at=T0),
                 "b": call("b", 1, 25, 1, outcome="Подпишет в офисе")}
        found = fraud.find(list(calls.values()))
        user = prompts.fraud_user(DAY, "СЗоВ", found, calls, {"a": 7}, [],
                                  fraud.public_stats(found))
        self.assertIn("в Sapar потом подписали 1, пока не подписали 1", user)
        self.assertIn("итого звонков 2, трубку положил оператор — в 1", user)
        self.assertIn("прослушано ИИ 1 из 2", user)


class PromptTests(unittest.TestCase):
    def test_fraud_prompt_names_the_four_task_templates(self):
        system = prompts.fraud_system("szov")
        for phrase in ("11–20 секунд", "до 30 секунд", "11–15 секунд", "11–12 секунд",
                       "«Подписал в звонке»", "дисциплинарных мер", "КРАТКО:"):
            self.assertIn(phrase, system)

    def test_call_line(self):
        signed = call("a", 1, 20, 0, outcome="Подписал в звонке", by_operator=True,
                      signed_at=T0 + timedelta(hours=1))
        self.assertEqual(prompts.fraud_call_line(signed, 4),
                         "10:00 · 20 с · итог «Подписал в звонке» · трубку положил оператор · "
                         "в Sapar подписал 21.10 11:00 · #4")
        plain = call("b", 1, 14, 0)
        self.assertEqual(prompts.fraud_call_line(plain, None),
                         "10:00 · 14 с · трубку положил водитель · не прослушан")


if __name__ == "__main__":
    unittest.main()
