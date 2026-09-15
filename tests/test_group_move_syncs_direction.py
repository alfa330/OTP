# -*- coding: utf-8 -*-
"""Направление оператора следует за группой — так же, как супервайзер.

До задачи #228 СВ, переводя оператора в другую группу, сам менял ему направление.
После неё СВ меняет только группу, и направление оставалось от прежней группы: на
проде админы правили его руками уже после перевода. Теперь перевод сам проставляет
действующее направление группы и пишет это в историю от имени того, кто перевёл.

Методы `Database` достаются через `ast`: боевой модуль на импорте поднимает пул к БД.
"""

import ast
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
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


def _class_attributes(*names):
    """Атрибуты класса в порядке объявления: _GROUP_SELECT склеивается из
    _GROUP_EFFECTIVE_DIRECTION_SQL, поэтому присваивания исполняются подряд."""
    namespace = {}
    wanted = set(names)
    for node in DATABASE_CLASS.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in wanted for target in node.targets
        ):
            exec(ast.get_source_segment(DATABASE_SOURCE, node), namespace)
    return {name: namespace[name] for name in names}


class _Cursor:
    """Отвечает по фрагменту SQL и пишет журнал вызовов."""

    def __init__(self, *, user_row=("operator", 69), effective_direction=70):
        self.calls = []
        self._user_row = user_row
        self._effective_direction = effective_direction
        self._next = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        if flat.startswith("SELECT role, direction_id FROM users"):
            self._next = self._user_row
        elif "AS effective_direction_id FROM groups g" in flat:
            self._next = (self._effective_direction,)
        else:
            self._next = None

    def fetchone(self):
        row, self._next = self._next, None
        return row

    def fetchall(self):
        return []

    def sql_log(self):
        return [flat for flat, _ in self.calls]

    def params_of(self, fragment):
        return [params for flat, params in self.calls if fragment in flat]

    def writes(self):
        return [flat for flat in self.sql_log() if flat.startswith(("UPDATE", "INSERT"))]


class _CursorContext:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, *exc_info):
        return False


class _FakeDatabase:
    """Ровно те методы Database, которые участвуют в переводе в группу."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.stamped = []
        self._GROUP_EFFECTIVE_DIRECTION_SQL = _class_attributes(
            "_GROUP_EFFECTIVE_DIRECTION_SQL"
        )["_GROUP_EFFECTIVE_DIRECTION_SQL"]
        for name in (
            "add_operator_to_group",
            "_add_operator_to_group_tx",
            "_group_active_supervisor_id_tx",
            "_set_operators_supervisor_tx",
            "_group_effective_direction_id_tx",
            "_sync_operator_direction_from_group_tx",
        ):
            namespace = {}
            exec(_method_source(name), namespace)
            setattr(self, name, namespace[name].__get__(self, _FakeDatabase))

    def _get_cursor(self):
        return _CursorContext(self.cursor)

    def _stamp_orphan_group_ids_tx(self, cursor, operator_id=None):
        self.stamped.append(operator_id)


class SyncOperatorDirectionFromGroupTests(unittest.TestCase):
    def _sync(self, *, user_row=("operator", 69), effective_direction=70, changed_by=21):
        cursor = _Cursor(user_row=user_row, effective_direction=effective_direction)
        db = _FakeDatabase(cursor)
        result = db._sync_operator_direction_from_group_tx(cursor, 6, 181, changed_by=changed_by)
        return result, cursor

    def test_moving_into_group_takes_its_direction(self):
        result, cursor = self._sync(user_row=("operator", 70), effective_direction=69)

        self.assertEqual(result, 69)
        self.assertEqual(cursor.params_of("UPDATE users SET direction_id"), [(69, 181)])
        self.assertEqual(cursor.params_of("UPDATE operator_profiles SET direction_id"), [(69, 181)])
        # в истории карточки видно, кто перевёл и откуда куда
        self.assertEqual(cursor.params_of("INSERT INTO user_history"), [(181, 21, "70", "69")])

    def test_same_direction_writes_nothing(self):
        result, cursor = self._sync(user_row=("operator", 70), effective_direction=70)

        self.assertEqual(result, 70)
        self.assertEqual(cursor.writes(), [])

    def test_group_without_direction_keeps_operators_own(self):
        result, cursor = self._sync(user_row=("operator", 69), effective_direction=None)

        self.assertEqual(result, 69)
        self.assertEqual(cursor.writes(), [])

    def test_trainee_without_direction_gets_the_groups(self):
        result, cursor = self._sync(user_row=("trainee", None), effective_direction=83)

        self.assertEqual(result, 83)
        self.assertEqual(cursor.params_of("INSERT INTO user_history"), [(181, 21, None, "83")])

    def test_only_operators_and_trainees_follow_the_group(self):
        # У тренера направления нет по определению, у СВ и админа — не по группе.
        for role in ("trainer", "sv", "admin"):
            with self.subTest(role=role):
                result, cursor = self._sync(user_row=(role, None), effective_direction=70)

                self.assertIsNone(result)
                self.assertEqual(cursor.writes(), [])

    def test_missing_user_writes_nothing(self):
        result, cursor = self._sync(user_row=None)

        self.assertIsNone(result)
        self.assertEqual(cursor.writes(), [])


class AddOperatorToGroupSyncsDirectionTests(unittest.TestCase):
    def test_enrolment_takes_group_direction_on_behalf_of_the_mover(self):
        cursor = _Cursor(user_row=("operator", 70), effective_direction=69)
        db = _FakeDatabase(cursor)

        result = db.add_operator_to_group(6, 181, assigned_by=55)

        self.assertEqual(result, 69)
        self.assertEqual(cursor.params_of("INSERT INTO user_history"), [(181, 55, "70", "69")])
        log = cursor.sql_log()
        enrolled = next(i for i, sql in enumerate(log) if "INSERT INTO group_operator_memberships" in sql)
        synced = next(i for i, sql in enumerate(log) if sql.startswith("UPDATE users SET direction_id"))
        self.assertLess(enrolled, synced)
        self.assertEqual(db.stamped, [181])

    def test_explicit_direction_turns_sync_off(self):
        # Создание с направлением из формы, массовая панель с выбранным
        # направлением, понижение СВ — там направление уже решено.
        cursor = _Cursor(user_row=("operator", 70), effective_direction=69)
        db = _FakeDatabase(cursor)

        result = db.add_operator_to_group(6, 181, assigned_by=55, sync_direction=False)

        self.assertIsNone(result)
        self.assertFalse([sql for sql in cursor.sql_log() if "direction_id" in sql])
        self.assertEqual(db.stamped, [181])


class GroupEffectiveDirectionTests(unittest.TestCase):
    def test_helper_uses_the_shared_expression(self):
        cursor = _Cursor(effective_direction=83)
        db = _FakeDatabase(cursor)

        self.assertEqual(db._group_effective_direction_id_tx(cursor, 34), 83)
        flat, params = cursor.calls[-1]
        self.assertIn("live.id = gd.canonical_id", flat)
        self.assertEqual(params, (34,))

    def test_expression_rules(self):
        sql = " ".join(
            _class_attributes("_GROUP_EFFECTIVE_DIRECTION_SQL")["_GROUP_EFFECTIVE_DIRECTION_SQL"].split()
        )
        # группа на архивной версии направления получает живую строку
        self.assertIn("WHEN gd.is_active THEN gd.id WHEN live.is_active THEN live.id", sql)
        # у группы без направления — только однозначное живое направление отдела по модели
        self.assertIn("WHERE g.direction_id IS NULL", sql)
        self.assertIn("md.calculation_model_code = g.calculation_model_code", sql)
        self.assertIn("HAVING COUNT(*) = 1", sql)

    def test_group_payload_exposes_effective_direction_to_the_card(self):
        attrs = _class_attributes("_GROUP_EFFECTIVE_DIRECTION_SQL", "_GROUP_SELECT")
        self.assertIn(
            attrs["_GROUP_EFFECTIVE_DIRECTION_SQL"] + " AS effective_direction_id",
            attrs["_GROUP_SELECT"],
        )
        namespace = {
            "normalize_calculation_model_code": lambda code, _name: code,
            "CALCULATION_MODEL_DESCRIPTIONS": {},
        }
        exec(_method_source("_group_row_to_dict"), namespace)
        row = (34, "ТП", 560, 79, "tez_line", None, "active", None, None, None, None,
               "ТП линия", 15, [], 83)

        group = namespace["_group_row_to_dict"](None, row)

        self.assertEqual(group["direction_id"], 79)
        self.assertEqual(group["effective_direction_id"], 83)


if __name__ == "__main__":
    unittest.main()
