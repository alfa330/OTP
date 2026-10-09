# -*- coding: utf-8 -*-
"""«Убрать из ЧС»: главы отделов, админы и супер-админы — не рядовой СВ.

ЧС-увольнение нельзя удалить, прервать сменой или перекрыть статусом. Снять ЧС
(увольнение остаётся обычным) может глава отдела в своём отделе, админ и
супер-админ; поставить ЧС, как и раньше, может любой, кто ведёт график.

Монолиты из тестов не импортируются (поднимают пул к базе), поэтому роут и
методы берутся из исходника через ast, как в остальном наборе.
"""

import ast
import copy
import logging
import textwrap
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8-sig")
DATABASE_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")


def _bot_functions(*names, namespace):
    module = source_cache.parse(BOT_SOURCE)
    by_name = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    missing = set(names) - set(by_name)
    if missing:
        raise AssertionError(f"Missing functions in bot_schedule2.py: {sorted(missing)}")
    selected = []
    for name in names:
        node = copy.deepcopy(by_name[name])
        node.decorator_list = []
        selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace


def _bot_constant(name):
    module = source_cache.parse(BOT_SOURCE)
    for node in module.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing constant {name} in bot_schedule2.py")


def _database_methods(*names, namespace):
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    by_name = {node.name: node for node in database_class.body if isinstance(node, ast.FunctionDef)}
    missing = set(names) - set(by_name)
    if missing:
        raise AssertionError(f"Missing Database methods: {sorted(missing)}")
    for name in names:
        exec(textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, by_name[name])), namespace)
    return namespace


# ── Роут ────────────────────────────────────────────────────────────────────


def _user(user_id, role):
    # Роут и проверки прав читают только id (0) и роль (3).
    return (user_id, None, f"User {user_id}", role)


class _RouteDB:
    def __init__(self, removed=None):
        self.removed = [{"id": 7, "operatorId": 50, "statusCode": "dismissal", "isBlacklist": False}] \
            if removed is None else removed
        self.remove_calls = []
        self.snapshot_calls = []

    def remove_schedule_dismissal_blacklist(self, operator_id, actor_id=None):
        self.remove_calls.append((operator_id, actor_id))
        return self.removed

    def get_operator_with_shifts(self, operator_id, range_start, range_end):
        self.snapshot_calls.append((operator_id, range_start, range_end))
        return {"id": operator_id}


class _Request:
    def __init__(self, body):
        self.body = body

    def get_json(self, silent=False):
        return self.body


class RemoveBlacklistRouteTests(unittest.TestCase):
    def _call(self, requester, *, heads=(), body=None, scope_error=None, db=None):
        db = db or _RouteDB()
        namespace = {
            "ROLE_HIERARCHY": _bot_constant("ROLE_HIERARCHY"),
            "jsonify": lambda payload: payload,
            "request": _Request({"operator_id": 50} if body is None else body),
            "logging": logging,
            "db": db,
            "_get_authenticated_requester": lambda: (requester[0], requester, None),
            "_headed_department_id": lambda requester_id: (sorted(heads)[0] if heads else None),
            "_headed_department_ids": lambda requester_id: frozenset(heads),
            "_resolve_scoped_operator_for_requester": (
                lambda user_data, requester_id, operator_id:
                (None, scope_error) if scope_error else (_user(int(operator_id), "operator"), None)
            ),
        }
        _bot_functions(
            "_normalize_user_role",
            "_get_role_level",
            "_has_min_role",
            "_is_admin_role",
            "_is_supervisor_role",
            "_resolve_management_requester",
            "_can_remove_dismissal_blacklist",
            "remove_work_schedule_dismissal_blacklist",
            namespace=namespace,
        )
        payload, status = namespace["remove_work_schedule_dismissal_blacklist"]()
        return payload, status, db

    def test_plain_supervisor_is_refused(self):
        payload, status, db = self._call(_user(10, "sv"))
        self.assertEqual(status, 403)
        self.assertIn("ЧС", payload["error"])
        self.assertEqual(db.remove_calls, [])

    def test_trainer_is_refused(self):
        _, status, db = self._call(_user(11, "trainer"))
        self.assertEqual(status, 403)
        self.assertEqual(db.remove_calls, [])

    def test_department_head_admin_and_super_admin_may_remove(self):
        cases = {
            "глава-СВ": (_user(12, "sv"), {3}),
            "глава-оператор": (_user(13, "operator"), {3}),
            "глава двух отделов": (_user(14, "trainer"), {3, 4}),
            "админ": (_user(15, "admin"), ()),
            "супер-админ": (_user(16, "super_admin"), ()),
        }
        for label, (requester, heads) in cases.items():
            with self.subTest(label):
                payload, status, db = self._call(requester, heads=heads)
                self.assertEqual(status, 200)
                self.assertEqual(db.remove_calls, [(50, requester[0])])
                self.assertEqual(payload["status_periods"][0]["id"], 7)
                self.assertIsNone(payload["operator"])

    def test_head_outside_own_department_gets_scope_refusal(self):
        payload, status, db = self._call(
            _user(12, "sv"), heads={3}, scope_error=("Forbidden for this operator", 403)
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "Forbidden for this operator")
        self.assertEqual(db.remove_calls, [])

    def test_not_blacklisted_is_404(self):
        payload, status, _ = self._call(_user(15, "admin"), db=_RouteDB(removed=[]))
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "Сотрудник не в ЧС")

    def test_missing_operator_is_400(self):
        _, status, db = self._call(_user(15, "admin"), body={})
        self.assertEqual(status, 400)
        self.assertEqual(db.remove_calls, [])

    def test_range_returns_operator_snapshot(self):
        payload, status, db = self._call(
            _user(15, "admin"),
            body={"operator_id": 50, "range_start": "2026-10-01", "range_end": "2026-10-31"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(db.snapshot_calls, [(50, "2026-10-01", "2026-10-31")])
        self.assertEqual(payload["operator"], {"id": 50})

    def test_route_is_registered_as_delete(self):
        self.assertIn(
            "@app.route('/api/work_schedules/status_period/blacklist', methods=['DELETE'])\n"
            "@require_api_key\n"
            "def remove_work_schedule_dismissal_blacklist():",
            BOT_SOURCE.replace("\r\n", "\n"),
        )


# ── Метод базы ──────────────────────────────────────────────────────────────


SCHEDULE_META = {"dismissal": {"label": "Увольнение", "kind": "dismissal"}}


class _Cursor:
    def __init__(self, update_rows):
        self.update_rows = update_rows
        self.executed = []
        self._last = None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._last = sql

    def fetchall(self):
        return list(self.update_rows) if "UPDATE operator_schedule_status_periods" in self._last else []


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *exc):
        return False


class _FakeDatabase:
    def __init__(self, update_rows):
        self.cursor = _Cursor(update_rows)

    def _get_cursor(self):
        return _CursorContext(self.cursor)


def _bind_database(*names):
    namespace = _database_methods(
        *names,
        namespace={
            "date": date,
            "datetime": datetime,
            "timedelta": timedelta,
            "SCHEDULE_SPECIAL_STATUS_META": SCHEDULE_META,
        },
    )
    for name in names:
        setattr(_FakeDatabase, name, namespace[name])
    return _FakeDatabase


class RemoveBlacklistDatabaseTests(unittest.TestCase):
    def setUp(self):
        _bind_database("remove_schedule_dismissal_blacklist", "_serialize_schedule_status_period")

    def test_unflags_dismissal_and_records_who_did_it(self):
        row = (7, 50, "dismissal", date(2026, 9, 1), None, "Прогулы", "без восстановления", False)
        fake = _FakeDatabase([row])
        result = fake.remove_schedule_dismissal_blacklist("50", actor_id="15")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 7)
        self.assertEqual(result[0]["startDate"], "2026-09-01")
        self.assertIsNone(result[0]["endDate"])
        self.assertEqual(result[0]["dismissalReason"], "Прогулы")
        self.assertFalse(result[0]["isBlacklist"])

        (update_sql, update_params), (history_sql, history_params) = fake.cursor.executed
        self.assertIn("SET is_blacklist = FALSE", update_sql)
        self.assertIn("status_code = 'dismissal'", update_sql)
        self.assertIn("COALESCE(is_blacklist, FALSE) = TRUE", update_sql)
        self.assertEqual(update_params, (50,))
        self.assertIn("INSERT INTO user_history", history_sql)
        self.assertEqual(history_params, (50, 15, "blacklist", "Да", "Нет"))

    def test_not_blacklisted_changes_nothing(self):
        fake = _FakeDatabase([])
        self.assertEqual(fake.remove_schedule_dismissal_blacklist(50, actor_id=15), [])
        self.assertEqual(len(fake.cursor.executed), 1)
        self.assertNotIn("user_history", fake.cursor.executed[0][0])


class ScheduleStatusDaysCarryBlacklistTests(unittest.TestCase):
    """Окно дня в «Графиках» берёт статус из scheduleStatusDays: без признака ЧС
    там не было ни плашки «ЧС», ни кнопки «Убрать из ЧС»."""

    def test_day_entry_has_blacklist_flag(self):
        _bind_database("_load_schedule_status_periods_for_operators", "_serialize_schedule_status_period")
        rows = [
            (7, 50, "dismissal", date(2026, 10, 2), None, "Прогулы", "", True),
            (8, 51, "dismissal", date(2026, 10, 2), None, "По собственному", "", False),
        ]
        cursor = _Cursor([])
        cursor.fetchall = lambda: rows
        result = _FakeDatabase([])._load_schedule_status_periods_for_operators(
            cursor, [50, 51], date(2026, 10, 1), date(2026, 10, 3)
        )
        self.assertTrue(result[50]["scheduleStatusDays"]["2026-10-02"]["isBlacklist"])
        self.assertFalse(result[51]["scheduleStatusDays"]["2026-10-03"]["isBlacklist"])
        self.assertNotIn("2026-10-01", result[50]["scheduleStatusDays"])


# ── Интерфейс ───────────────────────────────────────────────────────────────


class FrontendWiringTests(unittest.TestCase):
    def test_history_shows_blacklist_field_in_words(self):
        labels = (ROOT / "src/components/modals/historyFieldLabels.js").read_text(encoding="utf-8")
        self.assertIn("blacklist: 'ЧС',", labels)

    def test_both_places_call_the_route_for_heads_and_admins_only(self):
        app = (ROOT / "src/App.jsx").read_text(encoding="utf-8")
        self.assertEqual(app.count("/api/work_schedules/status_period/blacklist"), 2)
        self.assertIn(
            "const plannerViewerCanRemoveBlacklist = isAdminLikeRoleFn(user?.role) || isDepartmentHead(user);",
            app,
        )
        self.assertIn(
            "const canRemoveEmployeeBlacklist = isAdminLikeRoleFn(currentUserRole) || isDepartmentHeadUser;",
            app,
        )
        # Флаги стоят в самих условиях показа, а не только объявлены:
        # окно дня на компьютере, на телефоне (там ещё и не в режиме просмотра)...
        self.assertIn("modalActiveScheduleStatus?.isBlacklist && plannerViewerCanRemoveBlacklist ? (", app)
        self.assertIn(
            "modalActiveScheduleStatus.isBlacklist && plannerViewerCanRemoveBlacklist && !plannerReadOnly ? (",
            app,
        )
        # ...и действие «Учета сотрудников» — во всех трёх его списках.
        self.assertIn("const employeeUnblacklistAction = (employee) => canRemoveEmployeeBlacklist", app)
        self.assertIn("&& isEmployeeBlacklistDismissal(employee) && {", app)
        self.assertEqual(app.count("employeeUnblacklistAction(employee),"), 2)
        self.assertEqual(app.count("employeeUnblacklistAction(op),"), 1)

    def test_answer_touches_only_the_modal_it_was_sent_from(self):
        app = (ROOT / "src/App.jsx").read_text(encoding="utf-8")
        start = app.index("const removeScheduleDismissalBlacklist = async () => {")
        handler = app[start:app.index("const isPlannerRowLocked = (opId) => (", start)]
        self.assertIn("const isSameModal = (m) => m.opId === targetOpId && m.date === targetDate;", handler)
        self.assertIn("operator_id: targetOpId,", handler)
        self.assertNotIn("modalState.opId,", handler)
        self.assertNotIn("op.id !== modalState.opId", handler)
        # Каждый setModalState после запроса — только для того же окна.
        self.assertEqual(handler.count("setModalState(m => (isSameModal(m) ?"), 2)
        self.assertEqual(handler.count("setModalState("), 3)


if __name__ == "__main__":
    unittest.main()
