# -*- coding: utf-8 -*-
"""Понижение супервайзера до оператора — обратная операция к повышению.

Опасность здесь не в смене роли, а в том, что СВ ДЕРЖИТ ГРУППЫ: `users.supervisor_id`
операторов выводится из группы, поэтому «просто поменять role» оставляет 7-33 человек
с супервайзером, который уже оператор, и починить это вручную нельзя — прямая правка
`supervisor_id` закрыта на уровне ручки.

Два инварианта, которые стерегут эти тесты:

1. ПОРЯДОК ШАГОВ. Членства СВ закрываются ДО того, как человека заводят оператором.
   Наоборот — и `_group_active_supervisor_id_tx` вернёт его же самого, то есть человек
   станет собственным супервайзером.
2. ГРУППА ОБЯЗАТЕЛЬНА. Оператор без группы остаётся без супервайзера и без учёта
   часов (`daily_hours.group_id` = NULL), поэтому ручка не принимает запрос без неё.

Боевые модули в тестах не импортируются (на импорте поднимается пул к БД), поэтому
и метод `Database`, и ручка достаются через `ast` — как в соседних наборах.
"""

import ast
import copy
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)
DATABASE_CLASS = next(
    node
    for node in DATABASE_MODULE.body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)


def _method_source(name):
    method = next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(DATABASE_SOURCE, method))


def _demote_error_codes():
    """Коды ошибок читаем из самого класса — тест переживёт переименование."""
    codes = {}
    for node in DATABASE_CLASS.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.startswith("DEMOTE_"):
                codes[target.id] = ast.literal_eval(node.value)
    return codes


DEMOTE_CODES = _demote_error_codes()


def _schedule_status_meta():
    """SCHEDULE_SPECIAL_STATUS_META из database.py — подписи статусов графика."""
    for node in DATABASE_MODULE.body:
        targets = getattr(node, "targets", None) or (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "SCHEDULE_SPECIAL_STATUS_META"
               for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError("SCHEDULE_SPECIAL_STATUS_META not found")


def _load_demote():
    namespace = {
        "logging": __import__("logging"),
        "SCHEDULE_SPECIAL_STATUS_META": _schedule_status_meta(),
    }
    exec(_method_source("demote_supervisor_to_operator"), namespace)
    return namespace["demote_supervisor_to_operator"]


class _UniqueNameRoleViolation(Exception):
    """Дубль psycopg2.errors.UniqueViolation по constraint unique_name_role."""

    def __str__(self):
        return 'duplicate key value violates unique constraint "unique_name_role"'


class _ScriptedCursor:
    """Курсор, отвечающий по фрагменту SQL. Пишет журнал вызовов — на нём и
    проверяется порядок шагов."""

    def __init__(self, *, user_row, headed=(), group_row, led_group_ids=(),
                 group_supervisors=None, raise_on_role_update=False,
                 active_status_period=None):
        self.calls = []
        self.executemany_calls = []
        self._user_row = user_row
        self._headed = [(name,) for name in headed]
        self._group_row = group_row
        self._led_group_ids = [(gid,) for gid in led_group_ids]
        # {group_id: supervisor_id or None} — кто станет СВ группы после ухода
        self._group_supervisors = group_supervisors or {}
        self._raise_on_role_update = raise_on_role_update
        self._active_status_period = active_status_period
        self._next_one = None
        self._next_all = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))

        if "FOR UPDATE" in flat and "FROM users" in flat:
            self._next_one = self._user_row
        elif "FROM departments" in flat and "head_user_id" in flat:
            self._next_all = self._headed
        elif "FROM operator_schedule_status_periods" in flat:
            self._next_one = (
                (self._active_status_period,) if self._active_status_period else None
            )
        elif "FROM groups" in flat:
            self._next_one = self._group_row
        elif "SELECT group_id FROM group_supervisor_memberships" in flat:
            self._next_all = self._led_group_ids
        elif flat.startswith("UPDATE users SET role = 'operator'"):
            if self._raise_on_role_update:
                raise _UniqueNameRoleViolation()
            self._next_one = (self._user_row[0], self._user_row[1])
        elif "SELECT gsm.supervisor_id" in flat:
            group_id = int(params[0])
            sv = self._group_supervisors.get(group_id)
            self._next_one = (sv,) if sv is not None else None
        elif "SELECT operator_id FROM group_operator_memberships" in flat:
            self._next_all = []
        elif "SELECT 1 FROM group_operator_memberships" in flat:
            self._next_one = None
        elif flat.startswith("SELECT supervisor_id FROM users"):
            target_group = int(self.target_group_id)
            self._next_one = (self._group_supervisors.get(target_group),)
        else:
            self._next_one = None
            self._next_all = []

    def executemany(self, sql, rows):
        self.executemany_calls.append((" ".join(sql.split()), list(rows)))

    def fetchone(self):
        row, self._next_one = self._next_one, None
        return row

    def fetchall(self):
        rows, self._next_all = self._next_all, []
        return rows

    def sql_log(self):
        return [flat for flat, _ in self.calls]

    def index_of(self, fragment):
        for i, flat in enumerate(self.sql_log()):
            if fragment in flat:
                return i
        raise AssertionError(f"SQL fragment not executed: {fragment!r}\n" +
                             "\n".join(self.sql_log()))


class _CursorContext:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, *exc_info):
        return False


class _FakeDatabase:
    """Ровно те методы Database, которые нужны понижению."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.stamped = []
        for name, value in DEMOTE_CODES.items():
            setattr(self, name, value)
        self.demote_supervisor_to_operator = _load_demote().__get__(self, _FakeDatabase)
        for name in (
            "_group_active_supervisor_id_tx",
            "_set_operators_supervisor_tx",
            "_sync_group_operators_supervisor_tx",
            "_add_operator_to_group_tx",
        ):
            setattr(self, name, _load_method_bound(self, name))

    def _get_cursor(self):
        return _CursorContext(self.cursor)

    def _stamp_orphan_group_ids_tx(self, cursor, operator_id=None):
        self.stamped.append(operator_id)


def _load_method_bound(instance, name):
    namespace = {"logging": __import__("logging")}
    exec(_method_source(name), namespace)
    return namespace[name].__get__(instance, type(instance))


def _user_row(user_id=55, *, role="sv", supervisor_id=None, direction_id=69,
              department_id=1, sip_number=None, name="Сабыр Азана"):
    # Порядок колонок как в SELECT ... FOR UPDATE внутри метода.
    return (user_id, name, role, supervisor_id, direction_id, department_id, sip_number)


def _group_row(group_id=10, *, department_id=1, direction_id=69, status="active"):
    return (group_id, department_id, direction_id, status)


class DemotionCascadeTests(unittest.TestCase):
    """Database.demote_supervisor_to_operator — каскад в одной транзакции."""

    def _run(self, **kwargs):
        cursor_kwargs = {
            "user_row": kwargs.pop("user_row", _user_row()),
            "headed": kwargs.pop("headed", ()),
            "group_row": kwargs.pop("group_row", _group_row()),
            "led_group_ids": kwargs.pop("led_group_ids", (3,)),
            "group_supervisors": kwargs.pop("group_supervisors", {}),
            "raise_on_role_update": kwargs.pop("raise_on_role_update", False),
            "active_status_period": kwargs.pop("active_status_period", None),
        }
        cursor = _ScriptedCursor(**cursor_kwargs)
        cursor.target_group_id = kwargs.get("group_id", 10)
        db = _FakeDatabase(cursor)
        result = db.demote_supervisor_to_operator(
            kwargs.pop("user_id", 55),
            kwargs.pop("group_id", 10),
            **kwargs,
        )
        return result, cursor, db

    def test_closes_supervisor_memberships_before_enrolling_as_operator(self):
        """Главный инвариант: иначе человек станет собственным супервайзером."""
        _, cursor, _ = self._run()
        closed = cursor.index_of("UPDATE group_supervisor_memberships SET end_date")
        role_changed = cursor.index_of("UPDATE users SET role = 'operator'")
        enrolled = cursor.index_of("INSERT INTO group_operator_memberships")
        self.assertLess(closed, role_changed, "роль меняется раньше, чем снят СВ с групп")
        self.assertLess(role_changed, enrolled, "оператора заводят в группу до смены роли")

    def test_membership_closed_by_date_not_deleted(self):
        """Образец — archive_group: историю членств не стираем."""
        _, cursor, _ = self._run(effective_date="2026-09-04")
        sql = cursor.sql_log()
        self.assertFalse(
            any("DELETE FROM group_supervisor_memberships" in item for item in sql),
            "членства удаляются вместо закрытия датой",
        )
        idx = cursor.index_of("UPDATE group_supervisor_memberships SET end_date")
        self.assertIn("2026-09-04", [str(p) for p in cursor.calls[idx][1]])

    def test_reports_groups_left_without_supervisor(self):
        """Группа без СВ — осознанный NULL (так же поступает archive_group),
        но вызывающая сторона обязана про это узнать."""
        result, _, _ = self._run(led_group_ids=(3, 6), group_supervisors={6: 99})
        self.assertEqual(result["released_group_ids"], [3, 6])
        self.assertEqual(result["orphaned_group_ids"], [3])

    def test_creates_operator_profile_row(self):
        """У действующих СВ строки operator_profiles нет вовсе — её именно создают."""
        _, cursor, _ = self._run()
        idx = cursor.index_of("INSERT INTO operator_profiles")
        self.assertIn("ON CONFLICT (user_id) DO UPDATE", cursor.calls[idx][0])

    def test_clears_sip_number_and_records_it(self):
        """Номер за время в СВ могли выдать другому: два оператора с одним
        добавочным ломают привязку звонков."""
        result, cursor, _ = self._run(user_row=_user_row(sip_number="1234"))
        self.assertTrue(result["sip_number_cleared"])
        idx = cursor.index_of("UPDATE users SET role = 'operator'")
        self.assertIn("sip_number = NULL", cursor.calls[idx][0])
        history = cursor.executemany_calls[0][1]
        self.assertIn(("sip_number", "1234", None), [(r[2], r[3], r[4]) for r in history])

    def test_writes_role_change_to_history(self):
        _, cursor, _ = self._run(user_row=_user_row(supervisor_id=None))
        history = cursor.executemany_calls[0][1]
        self.assertIn(("role", "sv", "operator"), [(r[2], r[3], r[4]) for r in history])

    def test_enrols_into_requested_group(self):
        result, cursor, db = self._run(group_id=10)
        idx = cursor.index_of("INSERT INTO group_operator_memberships")
        self.assertEqual(cursor.calls[idx][1][0], 10)
        self.assertEqual(result["group_id"], 10)
        self.assertEqual(db.stamped, [55], "дни без группы не подобраны")

    def test_rejects_non_supervisor(self):
        for role in ("operator", "trainee", "admin", "trainer"):
            with self.subTest(role=role):
                with self.assertRaises(ValueError) as ctx:
                    self._run(user_row=_user_row(role=role))
                self.assertEqual(str(ctx.exception), DEMOTE_CODES["DEMOTE_NOT_A_SUPERVISOR"])

    def test_rejects_department_head(self):
        """Права главы отдела роль не смотрят — иначе получим оператора с
        админскими правами по отделу."""
        with self.assertRaises(ValueError) as ctx:
            self._run(headed=("Отдел продаж",))
        self.assertTrue(str(ctx.exception).startswith(DEMOTE_CODES["DEMOTE_HEADS_DEPARTMENT"]))
        self.assertIn("Отдел продаж", str(ctx.exception))

    def test_rejects_open_schedule_status_period(self):
        """Пока человек — СВ, синхронизация статусов его не видит (она берёт только
        role='operator'). Сразу после понижения увидит и перепишет users.status по
        периоду — на проде у одного из девяти СВ этот период 'dismissal', то есть
        человека молча пометило бы уволенным, да ещё от имени «Системы»."""
        with self.assertRaises(ValueError) as ctx:
            self._run(active_status_period="dismissal")
        message = str(ctx.exception)
        self.assertTrue(message.startswith(DEMOTE_CODES["DEMOTE_ACTIVE_STATUS_PERIOD"]))
        self.assertIn("Увольнение", message)

    def test_open_period_checked_before_anything_is_written(self):
        cursor = _ScriptedCursor(
            user_row=_user_row(), group_row=_group_row(),
            led_group_ids=(3,), active_status_period="bs",
        )
        cursor.target_group_id = 10
        db = _FakeDatabase(cursor)
        with self.assertRaises(ValueError):
            db.demote_supervisor_to_operator(55, 10)
        self.assertFalse(
            [sql for sql in cursor.sql_log() if sql.startswith("UPDATE")],
            "до отказа что-то уже записано",
        )

    def test_rejects_archived_or_foreign_group(self):
        with self.assertRaises(ValueError) as ctx:
            self._run(group_row=_group_row(status="archived"))
        self.assertEqual(str(ctx.exception), DEMOTE_CODES["DEMOTE_GROUP_ARCHIVED"])

        with self.assertRaises(ValueError) as ctx:
            self._run(group_row=_group_row(department_id=560))
        self.assertEqual(str(ctx.exception), DEMOTE_CODES["DEMOTE_GROUP_OTHER_DEPARTMENT"])

    def test_requires_direction(self):
        """В проде направление заполнено у всех операторов: по нему считаются
        оценки, ЗП и половина отчётов."""
        with self.assertRaises(ValueError) as ctx:
            self._run(
                user_row=_user_row(direction_id=None),
                group_row=_group_row(direction_id=None),
            )
        self.assertEqual(str(ctx.exception), DEMOTE_CODES["DEMOTE_DIRECTION_REQUIRED"])

    def test_falls_back_to_group_direction(self):
        result, _, _ = self._run(
            user_row=_user_row(direction_id=None),
            group_row=_group_row(direction_id=77),
        )
        self.assertEqual(result["direction_id"], 77)

    def test_name_collision_becomes_typed_error(self):
        """unique_name_role (name, role): у одного из девяти СВ в проде уже есть
        уволенный оператор-тёзка. Без обработки это был бы голый 500."""
        with self.assertRaises(ValueError) as ctx:
            self._run(raise_on_role_update=True)
        self.assertEqual(str(ctx.exception), DEMOTE_CODES["DEMOTE_NAME_TAKEN"])


def _load_endpoint(namespace):
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    node = next(
        n for n in module.body
        if isinstance(n, ast.FunctionDef) and n.name == "admin_demote_to_operator"
    )
    node = copy.deepcopy(node)
    node.decorator_list = []
    result = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), result)
    return result["admin_demote_to_operator"]


def _call_center_codes():
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    for n in module.body:
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CALL_CENTER_DEPARTMENT_CODES"
            for t in n.targets
        ):
            value = n.value
            # Константа объявлена как frozenset({...}) — литерал лежит в аргументе.
            if isinstance(value, ast.Call):
                value = value.args[0]
            return set(ast.literal_eval(value))
    raise AssertionError("CALL_CENTER_DEPARTMENT_CODES not found")


class _EndpointDB:
    def __init__(self, *, target_role="sv", department_code="szov", raises=None):
        self.target_role = target_role
        self.department_code = department_code
        self.raises = raises
        self.demote_calls = []
        self.revoked = []
        for name, value in DEMOTE_CODES.items():
            setattr(self, name, value)

    def get_user(self, *, id):
        # Значимы 0=id, 3=role.
        return (int(id), None, "Сабыр Азана", self.target_role, None, None, None)

    def get_user_department(self, user_id):
        return (1, self.department_code)

    def demote_supervisor_to_operator(self, user_id, group_id, **kwargs):
        self.demote_calls.append((user_id, group_id, kwargs))
        if self.raises:
            raise self.raises
        return {"id": user_id, "name": "Сабыр Азана", "group_id": group_id,
                "orphaned_group_ids": []}

    def revoke_all_user_sessions(self, user_id):
        self.revoked.append(user_id)


class DemotionEndpointTests(unittest.TestCase):
    """POST /api/admin/demote_to_operator — валидация и права."""

    def _invoke(self, payload, *, db=None, requester_role="admin", global_admin=True):
        db = db or _EndpointDB()
        state = {}

        class _Request:
            def get_json(self, *a, **k):
                return payload

        def _get_authenticated_requester():
            return 2, (2, None, "Админ", requester_role, None, None, None), None

        namespace = {
            "db": db,
            "request": _Request(),
            "jsonify": lambda payload=None, **kw: payload if payload is not None else kw,
            "logging": __import__("logging"),
            "datetime": __import__("datetime").datetime,
            "CALL_CENTER_DEPARTMENT_CODES": _call_center_codes(),
            "_get_authenticated_requester": _get_authenticated_requester,
            "_normalize_user_role": lambda r: str(r or "").strip().lower(),
            "_is_admin_role": lambda r: r in ("admin", "super_admin"),
            "_is_global_admin_requester": lambda r, i=None: global_admin,
            "_requester_can_access_target_user": lambda *a, **k: state.get("access", True),
            "_DEMOTE_ERROR_RESPONSES": _demote_error_responses(),
        }
        result = _load_endpoint(namespace)()
        body = result[0] if isinstance(result, tuple) else result
        status = result[1] if isinstance(result, tuple) else 200
        return body, status, db

    def test_group_is_required(self):
        """Оператор без группы остаётся без супервайзера и без учёта часов."""
        body, status, db = self._invoke({"user_id": 55})
        self.assertEqual(status, 400)
        self.assertIn("группу", body["error"])
        self.assertEqual(db.demote_calls, [])

    def test_only_admins_may_demote(self):
        body, status, db = self._invoke({"user_id": 55, "group_id": 10}, requester_role="sv")
        self.assertEqual(status, 403)
        self.assertEqual(db.demote_calls, [])

    def test_rejects_non_call_center_department(self):
        """Задача владельца ограничена отделами КЦ."""
        for code in ("front_office", "hr", "accounting", "marketing"):
            with self.subTest(code=code):
                db = _EndpointDB(department_code=code)
                body, status, db = self._invoke({"user_id": 55, "group_id": 10}, db=db)
                self.assertEqual(status, 400)
                self.assertIn("контакт-центра", body["error"])
                self.assertEqual(db.demote_calls, [])

    def test_allows_call_center_departments(self):
        for code in sorted(_call_center_codes()):
            with self.subTest(code=code):
                db = _EndpointDB(department_code=code)
                body, status, db = self._invoke({"user_id": 55, "group_id": 10}, db=db)
                self.assertEqual(status, 200)
                self.assertEqual(len(db.demote_calls), 1)

    def test_department_head_gets_explanatory_409(self):
        db = _EndpointDB(raises=ValueError(
            DEMOTE_CODES["DEMOTE_HEADS_DEPARTMENT"] + ":Отдел продаж"))
        body, status, _ = self._invoke({"user_id": 55, "group_id": 10}, db=db)
        self.assertEqual(status, 409)
        self.assertIn("Отдел продаж", body["error"])
        self.assertIn("глава отдела", body["error"])

    def test_open_status_period_gets_explanatory_409(self):
        db = _EndpointDB(raises=ValueError(
            DEMOTE_CODES["DEMOTE_ACTIVE_STATUS_PERIOD"] + ":Увольнение"))
        body, status, _ = self._invoke({"user_id": 55, "group_id": 10}, db=db)
        self.assertEqual(status, 409)
        self.assertIn("Увольнение", body["error"])
        self.assertIn("графике", body["error"])

    def test_name_collision_is_409_not_500(self):
        db = _EndpointDB(raises=ValueError(DEMOTE_CODES["DEMOTE_NAME_TAKEN"]))
        body, status, _ = self._invoke({"user_id": 55, "group_id": 10}, db=db)
        self.assertEqual(status, 409)
        self.assertIn("именем", body["error"])

    def test_revokes_sessions_so_ui_stops_showing_supervisor(self):
        """Права бэкенд отбирает сразу, но SPA до перезагрузки рисует меню СВ.
        Триггер БД тут не срабатывает — он завязан на смену статуса."""
        body, status, db = self._invoke({"user_id": 55, "group_id": 10})
        self.assertEqual(status, 200)
        self.assertEqual(db.revoked, [55])

    def test_failed_session_revoke_does_not_fail_demotion(self):
        db = _EndpointDB()
        db.revoke_all_user_sessions = lambda user_id: (_ for _ in ()).throw(RuntimeError("boom"))
        body, status, _ = self._invoke({"user_id": 55, "group_id": 10}, db=db)
        self.assertEqual(status, 200)

    def test_rejects_bad_effective_date(self):
        body, status, db = self._invoke(
            {"user_id": 55, "group_id": 10, "effective_date": "04.09.2026"})
        self.assertEqual(status, 400)
        self.assertEqual(db.demote_calls, [])

    def test_passes_through_optional_fields(self):
        body, status, db = self._invoke({
            "user_id": 55, "group_id": 10,
            "direction_id": 77, "effective_date": "2026-09-04",
        })
        self.assertEqual(status, 200)
        user_id, group_id, kwargs = db.demote_calls[0]
        self.assertEqual((user_id, group_id), (55, 10))
        self.assertEqual(kwargs["direction_id"], 77)
        self.assertEqual(kwargs["effective_date"], "2026-09-04")
        self.assertEqual(kwargs["changed_by"], 2)


def _demote_error_responses():
    """_DEMOTE_ERROR_RESPONSES из bot_schedule2.py — тест сверяется с боевой картой."""
    module = source_cache.parse(BOT_PATH.read_text(encoding="utf-8-sig"))
    node = next(
        n for n in module.body
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_DEMOTE_ERROR_RESPONSES" for t in n.targets
        )
    )
    namespace = {"db": type("_Codes", (), DEMOTE_CODES)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace["_DEMOTE_ERROR_RESPONSES"]


class DemotionErrorMapTests(unittest.TestCase):
    def test_every_error_code_has_a_response(self):
        """Новый код без записи в карте улетел бы пользователю как сырой
        DEMOTE_-идентификатор."""
        mapped = set(_demote_error_responses())
        for name, code in DEMOTE_CODES.items():
            if name in ("DEMOTE_HEADS_DEPARTMENT", "DEMOTE_ACTIVE_STATUS_PERIOD"):
                continue  # обрабатываются отдельно — к коду приклеена подробность
            with self.subTest(code=name):
                self.assertIn(code, mapped)


if __name__ == "__main__":
    unittest.main()
