# -*- coding: utf-8 -*-
"""Два источника статусов у одного человека (решение владельца 10.11.2026).

Оператор СЗоВ работает в Oktell (статусы приходят выгрузкой), а на обзвон удалённого КЦ
садится в iCORE Phone (события приходят сами, с client_event_id). Пока он подключён к
обзвону через телефон, время считается по телефону, а не по Oktell. До правки выгрузка
Oktell работала как «replace по дню» и стирала телефонные события при каждом прогоне.

Тесты исполняют НАСТОЯЩИЕ методы из db/operator_status.py. Люди и номера выдуманы.
"""
import ast
import textwrap
import unittest
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "db" / "operator_status.py"
BOT_PATH = ROOT / "bot_schedule2.py"

DAY = date.today() - timedelta(days=5)          # старше 48 ч: открытого хвоста до «сейчас» нет
OP = 7


def at(hour, minute=0, second=0):
    return datetime.combine(DAY, dt_time(hour, minute, second))


def _mixin(namespace_extra=None):
    """Класс с настоящими методами и атрибутами OperatorStatusMixin."""
    source = STATUS_PATH.read_text(encoding="utf-8-sig")
    module = ast.parse(source)
    node = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "OperatorStatusMixin")
    wanted = {
        "LIVE_STATUS_OFF_KEYS", "LIVE_STATUS_SILENCE_LIMIT_SECONDS", "LIVE_STATUS_IDLE_IMPORT_KEYS",
        "LIVE_STATUS_RESUME_MAX_AGE_SECONDS",
        "_overlay_live_status_sessions", "_status_transition_signature",
        "_canonicalize_status_transition_events", "_split_status_segment_datetimes_by_day",
        "_status_import_event_kind_from_key", "_rebuild_operator_status_segments_tx",
        "save_operator_status_import",
    }
    body = []
    for child in node.body:
        name = getattr(child, "name", None) or (
            child.targets[0].id if isinstance(child, ast.Assign) else None)
        if name in wanted:
            body.append(child)
    assert {getattr(c, "name", None) or c.targets[0].id for c in body} == wanted
    cls = ast.ClassDef(name="Mixin", bases=[], keywords=[], body=body, decorator_list=[])
    namespace = {
        "CHAT_MANAGER_ACTION_STATUS_KEYS": {"take chat", "transfer chat"},
        "STATUS_EVENTS_RETENTION_DAYS": 45,
        "STATUS_IMPORT_INSERT_PAGE_SIZE": 100,
        "LIVE_STATUS_SILENCE_LIMIT_MINUTES": 60,
        "date": date, "datetime": datetime, "dt_time": dt_time, "timedelta": timedelta,
    }
    namespace.update(namespace_extra or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(STATUS_PATH), "exec"), namespace)
    return namespace["Mixin"]


class _Db(_mixin()):
    STATUS_SEGMENT_OPERATOR_LOCK_NAMESPACE = 915904142

    @staticmethod
    def _normalize_schedule_date(value):
        return value

    @staticmethod
    def _lock_operator_status_segments_tx(_cursor, _operator_ids):
        return None

    @staticmethod
    def _normalize_import_status_key(value):
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _clip_part_after_authoritative(part, _end):
        return part


def ev(event_id, moment, key, live, note=None):
    return {"id": event_id, "event_at": moment, "status_key": key, "state_note": note, "live": live}


def timeline(events):
    """Ряд после сведения и канонизации: [(время, статус)]."""
    db = _Db()
    merged = db._canonicalize_status_transition_events(db._overlay_live_status_sessions(events))
    return [(item["event_at"], item["status_key"]) for item in merged]


class OverlayRuleTests(unittest.TestCase):
    def test_single_source_is_returned_untouched(self):
        """Отделы на одном источнике считаются ровно как раньше — те же объекты, тот же порядок."""
        db = _Db()
        for live in (True, False):
            events = [ev(1, at(9), "готов", live), ev(2, at(10), "выключен", live),
                      ev(3, at(11), "готов", live)]
            result = db._overlay_live_status_sessions(events)
            self.assertEqual(len(result), 3)
            self.assertTrue(all(a is b for a, b in zip(result, events)))
        self.assertEqual([], db._overlay_live_status_sessions([]))
        self.assertEqual([], db._overlay_live_status_sessions(None))

    def test_phone_session_wins_over_oktell(self):
        """Утро в Oktell, с 12:50 обзвон в телефоне. Сессию Oktell закрыли при входе в
        телефон («Выключен» 12:50:05) — внутри сессии телефона она не видна, а после
        «выключен» телефона человек не в сети, пока не вернётся в Oktell."""
        events = [
            ev(1, at(9), "готов", False),
            ev(2, at(12, 50), "готов", True),
            ev(3, at(12, 50, 5), "выключен", False),
            ev(4, at(12, 55), "занят", True),
            ev(5, at(13), "готов", True),
            ev(6, at(15), "выключен", True),
            ev(7, at(15, 5), "готов", False),
        ]
        self.assertEqual(timeline(events), [
            (at(9), "готов"), (at(12, 55), "занят"), (at(13), "готов"),
            (at(15), "выключен"), (at(15, 5), "готов"),
        ])

    def test_oktell_events_inside_session_are_hidden(self):
        """Oktell не закрыли, и он пишет свои статусы посреди обзвона — в ряд они не идут."""
        events = [
            ev(1, at(9), "готов", False),
            ev(2, at(12, 50), "перезвон", True),
            ev(3, at(13), "перерыв", False, "Авто"),
            ev(4, at(13, 10), "занят", True),
            ev(5, at(13, 12), "нет на месте", False),
            ev(6, at(13, 20), "перезвон", True),
        ]
        self.assertEqual(timeline(events), [
            (at(9), "готов"), (at(12, 50), "перезвон"), (at(13, 10), "занят"),
            (at(13, 20), "перезвон"),
        ])

    def test_after_phone_exit_oktell_state_of_that_moment_resumes(self):
        """Человек вышел из телефона, а в Oktell он всё это время на перерыве (статус
        сменился ВНУТРИ сессии) — после выхода действует именно он."""
        events = [
            ev(1, at(9), "готов", False),
            ev(2, at(12), "готов", True),
            ev(3, at(12, 30), "перерыв", False),
            ev(4, at(14), "выключен", True),
            ev(5, at(16), "готов", False),
        ]
        self.assertEqual(timeline(events), [
            (at(9), "готов"), (at(14), "перерыв"), (at(16), "готов"),
        ])

    def test_stray_phone_exit_does_not_break_oktell_day(self):
        """«Выключен» телефона без открытой сессии (запоздавшее событие) не выключает
        человека, который работает в Oktell."""
        events = [
            ev(1, at(9), "готов", False),
            ev(2, at(9, 30), "выключен", True),
            ev(3, at(12), "перерыв", False),
        ]
        self.assertEqual(timeline(events), [(at(9), "готов"), (at(12), "перерыв")])

    def test_dead_phone_gives_way_to_oktell_after_silence_limit(self):
        """Телефон пропал в 13:00 без «выключен». События Oktell в первый час ещё не
        видны, первое же позже порога (14:10) возвращает учёт в Oktell — с 14:00 и тем
        состоянием, которое было в Oktell на 14:00."""
        events = [
            ev(1, at(12), "готов", True),
            ev(2, at(13), "занят", True),
            ev(3, at(13, 20), "перерыв", False),
            ev(4, at(14, 10), "готов", False),
            ev(5, at(14, 30), "занят", False),
        ]
        self.assertEqual(timeline(events), [
            (at(12), "готов"), (at(13), "занят"), (at(14), "перерыв"),
            (at(14, 10), "готов"), (at(14, 30), "занят"),
        ])

    def test_silence_limit_boundary(self):
        """Ровно через порог — ещё сессия; секундой позже — уже нет."""
        base = [ev(1, at(9), "готов", False), ev(2, at(12), "тренинг", True)]
        inside = base + [ev(3, at(13), "занят", False)]
        outside = base + [ev(3, at(13, 0, 1), "занят", False)]
        self.assertEqual(timeline(inside), [(at(9), "готов"), (at(12), "тренинг")])
        self.assertEqual(timeline(outside), [
            (at(9), "готов"), (at(12), "тренинг"), (at(13), "готов"), (at(13, 0, 1), "занят")])

    def test_long_phone_status_without_oktell_activity_is_not_cut(self):
        """Три часа «Тренинга» в телефоне: Oktell молчит — статус телефона стоит."""
        events = [
            ev(1, at(9), "выключен", False),
            ev(2, at(10), "тренинг", True),
            ev(3, at(13), "готов", True),
        ]
        self.assertEqual(timeline(events), [
            (at(9), "выключен"), (at(10), "тренинг"), (at(13), "готов")])

    def test_idle_oktell_window_does_not_cut_the_session(self):
        """Окно Oktell на экране входа пишет «Без телефона» → «Выключен» каждую минуту.
        Это не работа в Oktell: двухчасовой «Тренинг» в телефоне остаётся целым, а после
        выхода из телефона действует последнее из этих состояний."""
        events = [ev(1, at(9), "готов", False), ev(2, at(10), "тренинг", True)]
        for minute in range(0, 115, 2):
            moment = at(10) + timedelta(minutes=5 + minute)
            events.append(ev(100 + minute, moment, "без телефона", False))
            events.append(ev(101 + minute, moment + timedelta(seconds=50), "выключен", False, "Вышел"))
        events.append(ev(3, at(12), "выключен", True))
        self.assertEqual(timeline(events), [(at(9), "готов"), (at(10), "тренинг"), (at(12), "выключен")])

    def test_old_import_of_a_phone_only_department_changes_nothing(self):
        """У отделов, давно работающих только в телефоне, в базе лежат старые выгрузки
        (прод 10.11.2026: ОП — 34 человека, Тез — 13, последняя запись «inactive» от июля).
        После «выключен» телефона такой статус не возвращается: ряд тот же, что без него."""
        old = datetime.combine(DAY - timedelta(days=100), dt_time(18))
        live_day = [ev(2, at(9), "готов", True), ev(3, at(13), "перерыв", True),
                    ev(4, at(14), "готов", True), ev(5, at(18), "выключен", True),
                    ev(6, at(18) + timedelta(days=1, hours=-9), "готов", True)]
        db = _Db()
        for stale_key in ("inactive", "active", "готов", "выключен"):
            with_stale = [ev(1, old, stale_key, False)] + live_day
            # Сведение ничего не добавляет и не убирает: дальше ряд идёт тем же путём, что
            # и до правки (канонизация, сегменты).
            self.assertEqual(db._overlay_live_status_sessions(with_stale), with_stale, stale_key)
        self.assertEqual(timeline([ev(1, old, "inactive", False)] + live_day)[1:], timeline(live_day))

    def test_resume_age_boundary(self):
        """Состояние выгрузки возвращается, пока ему не больше 16 часов."""
        self.assertEqual(_Db.LIVE_STATUS_RESUME_MAX_AGE_SECONDS, 16 * 3600)
        start = at(1)
        fresh = [ev(1, start, "тренинг", False), ev(2, start + timedelta(minutes=1), "готов", True),
                 ev(3, start + timedelta(hours=16), "выключен", True)]
        stale = [ev(1, start, "тренинг", False), ev(2, start + timedelta(minutes=1), "готов", True),
                 ev(3, start + timedelta(hours=16, seconds=1), "выключен", True)]
        self.assertEqual(timeline(fresh)[-1], (start + timedelta(hours=16), "тренинг"))
        self.assertEqual(timeline(stale)[-1], (start + timedelta(hours=16, seconds=1), "выключен"))

    def test_session_reopens_after_cut(self):
        events = [
            ev(1, at(10), "готов", True),
            ev(2, at(12), "готов", False),
            ev(3, at(12, 30), "перерыв", False),
            ev(4, at(13), "занят", True),
            ev(5, at(13, 5), "готов", False),
        ]
        self.assertEqual(timeline(events), [
            (at(10), "готов"), (at(12, 30), "перерыв"), (at(13), "занят")])

    def test_silence_limit_is_an_hour_by_default(self):
        self.assertEqual(_Db.LIVE_STATUS_SILENCE_LIMIT_SECONDS, 3600)
        self.assertEqual(_Db.LIVE_STATUS_OFF_KEYS, frozenset({"выключен"}))
        core = (ROOT / "db" / "core.py").read_text(encoding="utf-8-sig")
        self.assertIn("_env_int('LIVE_STATUS_SILENCE_LIMIT_MINUTES', 60, minimum=5)", core)


class _Cursor:
    def __init__(self, event_rows, existing=()):
        self.event_rows = event_rows
        self.existing = list(existing)
        self.rows = []
        self.queries = []
        self.rowcount = 0

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.queries.append((normalized, params))
        if "WITH candidates AS" in normalized:
            self.rows = list(self.event_rows)
        elif normalized.startswith("SELECT id, operator_id, status_date"):
            self.rows = list(self.existing)
        else:
            self.rows = []

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return None


def _rebuild(event_rows):
    captured = []
    cls = _mixin({"execute_values": lambda _c, _q, values, **_k: captured.extend(values)})

    class Db(cls, _Db):
        pass

    cursor = _Cursor(event_rows)
    Db()._rebuild_operator_status_segments_tx(cursor, [OP], DAY - timedelta(days=1), DAY + timedelta(days=1))
    return sorted((row[2], row[3], row[5]) for row in captured), cursor


class RebuildTests(unittest.TestCase):
    def test_mixed_day_segments(self):
        rows = [
            (1, OP, at(9), "Готов", None, "status", False),
            (2, OP, at(12, 50), "готов", None, "status", True),
            (3, OP, at(12, 50, 5), "Выключен", None, "status", False),
            (4, OP, at(12, 55), "занят", None, "status", True),
            (5, OP, at(13), "готов", None, "status", True),
            (6, OP, at(15), "выключен", None, "status", True),
            (7, OP, at(15, 5), "Готов", None, "status", False),
            (8, OP, at(18), "Выключен", None, "status", False),
        ]
        segments, _cursor = _rebuild(rows)
        self.assertEqual(segments, [
            (at(9), at(12, 55), "готов"),
            (at(12, 55), at(13), "занят"),
            (at(13), at(15), "готов"),
            (at(15), at(15, 5), "выключен"),
            (at(15, 5), at(18), "готов"),
        ])

    def test_single_source_day_is_as_before(self):
        for live in (True, False):
            rows = [(1, OP, at(9), "готов", None, "status", live),
                    (2, OP, at(12), "перерыв", None, "status", live),
                    (3, OP, at(12, 30), "готов", None, "status", live),
                    (4, OP, at(18), "выключен", None, "status", live)]
            segments, _cursor = _rebuild(rows)
            self.assertEqual(segments, [(at(9), at(12), "готов"), (at(12), at(12, 30), "перерыв"),
                                        (at(12, 30), at(18), "готов")])

    def test_anchor_events_are_taken_per_source(self):
        _segments, cursor = _rebuild([])
        sql = next(q for q, _ in cursor.queries if "WITH candidates AS" in q)
        self.assertIn("(client_event_id IS NOT NULL) AS is_live", sql)
        self.assertEqual(sql.count("SELECT DISTINCT ON (operator_id, is_live)"), 2)
        self.assertIn("ORDER BY operator_id, is_live, event_at DESC, id DESC", sql)
        self.assertIn("ORDER BY operator_id, is_live, event_at ASC, id DESC", sql)


class ImportKeepsPhoneEventsTests(unittest.TestCase):
    def _save(self, **kwargs):
        import json
        import uuid
        cls = _mixin({"execute_values": lambda *_a, **_k: None, "json": json, "uuid": uuid})

        class Db(cls, _Db):
            def _get_cursor(self):
                import contextlib

                @contextlib.contextmanager
                def manager():
                    yield self.cursor
                return manager()

            def _rebuild_operator_status_segments_tx(self, **_kwargs):
                return {"segments_saved": 0, "deleted_segments": 0}

            def _recalculate_auto_daily_hours_tx(self, **_kwargs):
                return {}

        db = Db()
        db.cursor = _Cursor([])
        db.save_operator_status_import(
            events=[{"operator_id": OP, "event_at": at(9), "status_key": "готов"}],
            segments=[], **kwargs)
        return [q for q, _ in db.cursor.queries if q.startswith("DELETE FROM operator_status_events")]

    def test_oktell_import_keeps_phone_events(self):
        deletes = self._save(keep_live_events=True)
        self.assertEqual(len(deletes), 1)
        self.assertTrue(deletes[0].endswith("AND client_event_id IS NULL"))

    def test_default_import_replaces_the_day(self):
        deletes = self._save()
        self.assertEqual(len(deletes), 1)
        self.assertNotIn("client_event_id", deletes[0])

    def test_oktell_sync_and_file_import_ask_to_keep_phone_events(self):
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        sync = source[source.index("def sync_oktell_operator_statuses("):
                      source.index("# === Oktell: количество звонков по операторам")]
        self.assertIn("keep_live_events=True", sync)
        route = source[source.index("def import_work_schedules_statuses_csv():"):]
        route = route[:route.index("\n@app.route(")]
        self.assertIn("keep_live_events=not is_tez_format", route)
        # Chat2Desk и выгрузка TEZ с точными интервалами работают как раньше.
        for name in ("def sync_chat2desk_operator_statuses(", "def _tez_status_sync_importer("):
            body = source[source.index(name):]
            body = body[:body.index("\ndef ", 10)]
            self.assertNotIn("keep_live_events", body)


if __name__ == "__main__":
    unittest.main()
