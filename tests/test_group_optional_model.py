# -*- coding: utf-8 -*-
"""Модель расчёта у группы необязательна — как и направление.

Раньше группу нельзя было создать без модели: форма подставляла «Операторскую», и
группа бэк-офиса (ООЗ), где по моделям ничего не считают, показывала модель, которую
ей никто не задавал. Теперь «не выбрано» — это NULL в `groups.calculation_model_code`:
своей модели у группы нет, и сотрудник считается по модели своего направления — той же
лестницей, что уже действует для людей без группы.

Методы `Database` достаются через `ast`: боевой модуль на импорте поднимает пул к БД.
Константы моделей и `normalize_calculation_model_code` берутся из самого модуля, а не
подменяются заглушками — иначе тест не заметил бы, что метод зовёт чужое имя.

Смысл самих SQL-выражений сверяет tests/test_group_optional_model_postgres.py на
настоящей базе; здесь закреплён их текст и всё, что вокруг него на Python.
"""

import ast
import calendar
import textwrap
import unittest
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

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
BOT_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
GROUPS_VIEW = (
    ROOT / "src" / "components" / "groups" / "GroupsView.jsx"
).read_text(encoding="utf-8-sig")

_MODEL_NAMES = {
    "CALCULATION_MODEL_OPERATOR",
    "CALCULATION_MODEL_CHAT_MANAGER",
    "CALCULATION_MODEL_TEZ_LINE",
    "CALCULATION_MODEL_TEZ_OP",
    "CALCULATION_MODEL_OP_VERIFICATOR",
    "CALCULATION_MODEL_OP_YANDEX_REG",
    "CALCULATION_MODEL_OP_OSNOVA",
    "CALCULATION_MODEL_OP_POTOK",
    "CALCULATION_MODEL_OP_SALES_CODES",
    "CALCULATION_MODEL_ALLOWED",
    "CALCULATION_MODEL_DESCRIPTIONS",
}

# Выражение лестницы целиком: любая перестановка ступеней или подмена колонки —
# это уже другое правило расчёта, и тест обязан его заметить.
MEMBER_MODEL_SQL = (
    "COALESCE( gr.calculation_model_code, "
    "(SELECT od.calculation_model_code FROM users ou "
    "JOIN directions od ON od.id = ou.direction_id WHERE ou.id = gom.operator_id) )"
)

_DATABASE_LINES = DATABASE_SOURCE.splitlines(keepends=True)


def _segment(node):
    """Исходник оператора по номерам строк. `ast.get_source_segment` на файле в
    2,5 МБ стоит 0,3 с за вызов, а вызовов здесь десятки."""
    lines = list(_DATABASE_LINES[node.lineno - 1:node.end_lineno])
    lines[0] = lines[0][node.col_offset:]
    return "".join(lines)


def _is_model_definition(node):
    is_model_constant = isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id in _MODEL_NAMES
        for target in node.targets
    )
    is_normalizer = (
        isinstance(node, ast.FunctionDef)
        and node.name == "normalize_calculation_model_code"
    )
    return is_model_constant or is_normalizer


# Настоящие константы моделей и нормализатор — в порядке объявления.
_MODEL_DEFINITIONS = "\n".join(
    _segment(node) for node in DATABASE_MODULE.body if _is_model_definition(node)
)


def _module_namespace():
    namespace = {"Optional": Optional, "List": List, "Dict": Dict, "Any": Any,
                 "calendar": calendar, "date": date}
    exec(_MODEL_DEFINITIONS, namespace)
    return namespace


def _method_node(name):
    return next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _method_source(name):
    return textwrap.dedent(_segment(_method_node(name)))


def _class_attribute(name):
    node = next(
        node
        for node in DATABASE_CLASS.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    namespace = {}
    exec(_segment(node), namespace)
    return namespace[name]


def _flat(sql):
    return " ".join(str(sql).split())


class _Cursor:
    """Пишет журнал запросов; на fetchall отдаёт заранее заданные строки."""

    def __init__(self, rows=()):
        self.calls = []
        self._rows = list(rows)

    def execute(self, sql, params=None):
        self.calls.append((_flat(sql), params))

    def fetchone(self):
        return (77,)

    def fetchall(self):
        return list(self._rows)

    def params_of(self, fragment):
        return [params for flat, params in self.calls if fragment in flat]

    def sql_of(self, fragment):
        (flat,) = [flat for flat, _ in self.calls if fragment in flat]
        return flat


class _CursorContext:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, *exc_info):
        return False


class _FakeDatabase:
    """Настоящие методы работы с группой — без пула и без схемы."""

    _METHODS = (
        "create_group",
        "change_group_model",
        "_normalize_calculation_model_code",
        "_load_operator_calculation_models_tx",
        "_load_segments_by_operator_tx",
        "_get_operator_month_segments_tx",
    )

    def __init__(self, group=None, rows=()):
        self.cursor = _Cursor(rows)
        self.group = group
        self.aggregated = []
        self._GROUP_MEMBER_MODEL_SQL = _class_attribute("_GROUP_MEMBER_MODEL_SQL")
        namespace = _module_namespace()
        for name in self._METHODS:
            exec(_method_source(name), namespace)
            setattr(self, name, namespace[name].__get__(self, _FakeDatabase))

    def _get_cursor(self):
        return _CursorContext(self.cursor)

    def get_group(self, group_id):
        return self.group if self.group is not None else {"id": group_id}

    def _aggregate_segment_from_daily_tx(self, cursor, operator_id, month, start_day, end_day,
                                         group_id, calculation_model_code):
        self.aggregated.append((operator_id, group_id, calculation_model_code))
        return {}

    def inserted_model(self):
        (params,) = self.cursor.params_of("INSERT INTO groups")
        return params[3]


class CreateGroupModelTests(unittest.TestCase):
    def test_group_without_a_model_stores_null(self):
        for nothing in (None, "", "   "):
            with self.subTest(model=nothing):
                db = _FakeDatabase()
                db.create_group("Группа ООЗ", calculation_model_code=nothing, department_id=2008)
                self.assertIsNone(db.inserted_model())

    def test_direction_does_not_put_its_model_into_the_group(self):
        # Форма показывает ровно то, что сохранится: «без модели» при выбранном
        # направлении остаётся «без модели», а в справочник направлений никто не ходит.
        db = _FakeDatabase()
        db.create_group("Чаты без модели", calculation_model_code=None, direction_id=69)

        self.assertIsNone(db.inserted_model())
        self.assertFalse(db.cursor.params_of("FROM directions"))

    def test_chosen_model_is_stored_as_before(self):
        for raw, stored in (
            ("chat_manager", "chat_manager"),
            (" Op_Potok ", "op_potok"),
            ("operator", "operator"),
            # незнакомый код по-прежнему сводится к операторской модели
            ("нет такой модели", "operator"),
        ):
            with self.subTest(model=raw):
                db = _FakeDatabase()
                db.create_group("Группа", calculation_model_code=raw)
                self.assertEqual(db.inserted_model(), stored)

    def test_insert_passes_the_model_through_untouched(self):
        db = _FakeDatabase()
        db.create_group("Группа", calculation_model_code="tez_op", direction_id=78,
                        department_id=560, table_url="https://t")

        (params,) = db.cursor.params_of("INSERT INTO groups")
        self.assertEqual(params, ("Группа", 560, 78, "tez_op", "https://t"))
        # ни COALESCE, ни дефолта на стороне SQL: что передано, то и записано
        self.assertEqual(
            db.cursor.sql_of("INSERT INTO groups"),
            "INSERT INTO groups (name, department_id, direction_id, calculation_model_code, "
            "table_url, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "RETURNING id",
        )

    def test_endpoint_hands_the_model_over_as_sent(self):
        start = BOT_SOURCE.index("def create_group_endpoint():")
        handler = BOT_SOURCE[start:BOT_SOURCE.index("\n@app.route(", start)]

        self.assertIn("calculation_model_code=data.get('calculation_model_code'),", handler)
        # ручка не подставляет модель сама: «не выбрано» доходит до базы как есть
        self.assertNotIn("'operator'", handler)
        self.assertNotIn("CALCULATION_MODEL_", handler)


class GroupPayloadTests(unittest.TestCase):
    def setUp(self):
        namespace = _module_namespace()
        exec(_method_source("_group_row_to_dict"), namespace)
        self.to_dict = namespace["_group_row_to_dict"]

    def _row(self, model):
        return (40, "Группа ООЗ", 2008, None, model, None, "active", None, None, None, None,
                None, 1, [], None)

    def test_group_without_a_model_is_not_reported_as_operator(self):
        for nothing in (None, "", "  "):
            with self.subTest(model=nothing):
                group = self.to_dict(None, self._row(nothing))
                self.assertIsNone(group["calculation_model_code"])
                self.assertIsNone(group["calculation_model_name"])

    def test_group_with_a_model_keeps_code_and_name(self):
        group = self.to_dict(None, self._row("chat_manager"))

        self.assertEqual(group["calculation_model_code"], "chat_manager")
        self.assertEqual(group["calculation_model_name"], "Модель чат-менеджера")


class SetModelLaterTests(unittest.TestCase):
    def test_first_model_of_a_group_is_logged_with_empty_old_value(self):
        db = _FakeDatabase(group={"id": 40, "calculation_model_code": None, "direction_name": None})

        result = db.change_group_model(40, "operator", changed_by=7)

        self.assertTrue(result["changed"])
        (logged,) = db.cursor.params_of("INSERT INTO group_model_change_log")
        self.assertEqual(logged, (40, None, "operator", 7, False))
        (updated,) = db.cursor.params_of("UPDATE groups SET calculation_model_code")
        self.assertEqual(updated, ("operator", 40))


class SchemaTests(unittest.TestCase):
    def test_model_column_is_nullable_in_group_and_its_month_snapshot(self):
        for table in ("groups", "group_month_snapshots"):
            with self.subTest(table=table):
                statement = (
                    f"ALTER TABLE {table} ALTER COLUMN calculation_model_code DROP NOT NULL;"
                )
                self.assertIn(statement, DATABASE_SOURCE)
                # снять NOT NULL можно только с уже созданной таблицы
                self.assertGreater(
                    DATABASE_SOURCE.index(statement),
                    DATABASE_SOURCE.index(f"CREATE TABLE IF NOT EXISTS {table} ("),
                )

    def test_not_null_is_dropped_once_and_not_on_every_start(self):
        # Безусловный ALTER брал бы ACCESS EXCLUSIVE на groups при каждом выкате.
        def drop_once(table):
            return (
                "IF EXISTS ( SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                f"AND table_name = '{table}' AND column_name = 'calculation_model_code' "
                "AND is_nullable = 'NO' ) THEN "
                f"ALTER TABLE {table} ALTER COLUMN calculation_model_code DROP NOT NULL; END IF;"
            )

        # Блок целиком и именно в cursor.execute: текст, который никто не исполняет,
        # колонку не освободит.
        self.assertIn(
            'cursor.execute(""" DO $$ BEGIN '
            + drop_once("groups") + " " + drop_once("group_month_snapshots")
            + ' END $$; """)',
            _flat(_method_source("_init_db")),
        )

    def test_month_freeze_keeps_the_group_model_as_it_was(self):
        freeze = _flat(_method_source("_freeze_month_to_snapshots_tx"))
        # В снимок группы уходит сырое значение колонки: NULL остаётся NULL.
        self.assertIn(
            "SELECT g.id, g.name, g.status, g.direction_id, g.calculation_model_code, "
            "g.table_url, g.archived_at FROM groups g",
            freeze,
        )
        self.assertIn(
            "for g_id, g_name, g_status, g_dir, g_model, g_url, g_arch in cursor.fetchall() or []:",
            freeze,
        )
        self.assertIn(
            "(g_id, month, g_name, g_status, g_dir, g_model, g_url, bool(g_arch), frozen_at)",
            freeze,
        )


class MemberModelLadderTests(unittest.TestCase):
    """У группы без модели сотрудник считается по модели своего направления."""

    def test_expression_is_group_model_then_member_direction(self):
        sql = _flat(_class_attribute("_GROUP_MEMBER_MODEL_SQL"))

        self.assertEqual(sql, MEMBER_MODEL_SQL)
        # psycopg2 принял бы знак процента за плейсхолдер
        self.assertNotIn("%", sql)

    def test_report_segments_read_the_model_through_the_ladder(self):
        # Имя направления нарочно похоже на код модели: перепутанные колонки
        # дали бы сегменту модель «op_potok».
        db = _FakeDatabase(rows=[
            (4, 6, "Без модели", "chat_manager", "op_potok", date(2026, 9, 10), None),
            (5, 6, "Без модели", None, "op_potok", date(2026, 8, 1), date(2026, 9, 20)),
        ])

        segments = db._load_segments_by_operator_tx(
            db.cursor, [4, 5], date(2026, 9, 1), date(2026, 9, 30))

        self.assertEqual(
            {op: [(s["group_id"], s["calculation_model_code"], s["direction_name"],
                   s["start_day"], s["end_day"]) for s in items]
             for op, items in segments.items()},
            {
                4: [(6, "chat_manager", "op_potok", 10, 30)],
                # база не нашла ни модели группы, ни направления — операторская
                5: [(6, "operator", "op_potok", 1, 20)],
            },
        )
        self.assertEqual(
            db.cursor.sql_of("FROM group_operator_memberships gom"),
            "SELECT gom.operator_id, gom.group_id, gr.name, " + MEMBER_MODEL_SQL + " , d.name, "
            "gom.start_date, gom.end_date FROM group_operator_memberships gom "
            "JOIN groups gr ON gr.id = gom.group_id "
            "LEFT JOIN directions d ON d.id = gr.direction_id "
            "WHERE gom.operator_id = ANY(%s) AND gom.start_date <= %s "
            "AND (gom.end_date IS NULL OR gom.end_date >= %s) "
            "ORDER BY gom.operator_id, gom.start_date",
        )

    def test_month_segments_read_the_model_through_the_ladder(self):
        db = _FakeDatabase(rows=[
            (6, "Без модели", None, "op_potok", "chat_manager", date(2026, 9, 10), None),
        ])

        segments = db._get_operator_month_segments_tx(db.cursor, 4, "2026-09")

        self.assertEqual(
            [(s["group_id"], s["calculation_model_code"], s["direction_name"]) for s in segments],
            [(6, "chat_manager", "op_potok")],
        )
        # агрегаты сегмента считаются той же моделью, что уходит в ответ и в снимок
        self.assertEqual(db.aggregated, [(4, 6, "chat_manager")])
        self.assertEqual(
            db.cursor.sql_of("FROM group_operator_memberships gom"),
            "SELECT gom.group_id, gr.name, gr.direction_id, d.name, " + MEMBER_MODEL_SQL + " , "
            "gom.start_date, gom.end_date FROM group_operator_memberships gom "
            "JOIN groups gr ON gr.id = gom.group_id "
            "LEFT JOIN directions d ON d.id = gr.direction_id "
            "WHERE gom.operator_id = %s AND gom.start_date <= %s "
            "AND (gom.end_date IS NULL OR gom.end_date >= %s) ORDER BY gom.start_date",
        )

    def test_my_hours_segments_use_the_shared_expression(self):
        source = _method_source("get_daily_hours_for_operator_month")

        self.assertIn(
            'SELECT gom.group_id, gr.name, gr.direction_id, d.name, '
            '""" + self._GROUP_MEMBER_MODEL_SQL + """, gom.start_date, gom.end_date '
            'FROM group_operator_memberships gom',
            _flat(source),
        )
        self.assertNotIn("gr.calculation_model_code", source)
        self.assertIn(
            "for g_id, g_name, dir_id, dir_name, model_code, seg_start, seg_end in "
            "_seg_cursor.fetchall() or []:",
            source,
        )

    def test_hours_accounting_of_a_group_falls_back_to_the_member_direction(self):
        source = _method_source("get_daily_hours_by_supervisor_month")

        self.assertIn(
            "COALESCE(g.calculation_model_code, d.calculation_model_code) as calculation_model_code",
            source,
        )
        self.assertNotIn(" g.calculation_model_code as calculation_model_code", source)
        # d в ветке группы — направление СОТРУДНИКА, а не направление группы
        self.assertIn(
            "JOIN users u ON u.id = gom.operator_id "
            "LEFT JOIN work_hours w ON w.operator_id = u.id AND w.month = %s "
            "LEFT JOIN directions d ON u.direction_id = d.id "
            "WHERE gom.group_id = %s",
            _flat(source),
        )

    def test_billing_efficiency_report_follows_the_same_ladder(self):
        source = _flat(_method_source("get_billing_operator_efficiency_report"))

        self.assertIn(
            "JOIN users u ON u.id = ot.operator_id "
            "LEFT JOIN directions od ON od.id = u.direction_id",
            source,
        )
        self.assertIn(
            "COALESCE( NULLIF(g.calculation_model_code, ''), "
            "NULLIF(od.calculation_model_code, ''), 'operator' ) = 'operator'",
            source,
        )

    def test_period_resolver_falls_back_when_the_group_has_no_model(self):
        # строки резолвера: (сотрудник, модель группы, имя направления, модель направления)
        db = _FakeDatabase(rows=[
            (4, None, "Чат менеджер", "chat_manager"),
            (5, "operator", "Чат менеджер", "chat_manager"),
            (6, None, None, None),
            (7, None, "Поток", "op_potok"),
        ])

        models = db._load_operator_calculation_models_tx(db.cursor, [4, 5, 6, 7, 8],
                                                         as_of=date(2026, 9, 15))

        self.assertEqual(
            models,
            {4: "chat_manager", 5: "operator", 6: "operator", 7: "op_potok", 8: "operator"},
        )
        resolver = db.cursor.sql_of("FROM users u")
        # модель группы приходит как есть: COALESCE на стороне SQL спрятал бы «без модели»
        self.assertIn(
            "SELECT u.id, g.calculation_model_code AS group_model, d.name AS direction_name, "
            "d.calculation_model_code AS direction_model FROM users u",
            resolver,
        )
        self.assertIn(
            "LEFT JOIN LATERAL ( SELECT gr.calculation_model_code "
            "FROM group_operator_memberships gom JOIN groups gr ON gr.id = gom.group_id",
            resolver,
        )
        self.assertIn("LEFT JOIN directions d ON d.id = u.direction_id", resolver)


class EffectiveDirectionTests(unittest.TestCase):
    """Группа без направления и без модели в отделе с единственным направлением."""

    def test_group_without_a_model_takes_the_only_live_direction_of_its_department(self):
        sql = _flat(_class_attribute("_GROUP_EFFECTIVE_DIRECTION_SQL"))

        self.assertIn(
            "(SELECT MIN(md.id) FROM directions md WHERE g.direction_id IS NULL "
            "AND md.is_active AND md.department_id = g.department_id "
            "AND (g.calculation_model_code IS NULL "
            "OR md.calculation_model_code = g.calculation_model_code) "
            "HAVING COUNT(*) = 1)",
            sql,
        )


class GroupsViewTests(unittest.TestCase):
    """Проводка формы: сами правила — в src/utils/groupForm.js (tests/group_form.test.mjs)."""

    def test_create_form_opens_and_resets_without_a_model(self):
        self.assertIn("const [form, setForm] = useState({ ...EMPTY_GROUP_FORM });", GROUPS_VIEW)
        self.assertIn(
            "const openCreate = () => { setForm({ ...EMPTY_GROUP_FORM }); "
            "setSuggestions([]); setCreateOpen(true); };",
            GROUPS_VIEW,
        )
        self.assertIn(
            "const closeCreate = () => { setCreateOpen(false); "
            "setForm({ ...EMPTY_GROUP_FORM }); setSuggestions([]); };",
            GROUPS_VIEW,
        )

    def test_create_form_is_wired_to_the_form_rules(self):
        self.assertIn("Модель расчёта (опционально)", GROUPS_VIEW)
        self.assertIn("const body = createGroupBody(form, { force });", GROUPS_VIEW)
        self.assertIn(
            "onChange={(v) => setForm(groupFormAfterDirectionPick(form, v, directions))}",
            GROUPS_VIEW,
        )
        self.assertIn(
            "value={form.calculation_model_code}\n"
            "                            placeholder={NO_GROUP_MODEL_LABEL}\n"
            "                            onChange={(v) => setForm({ ...form, calculation_model_code: v })}\n"
            "                            options={groupModelOptions(calcModels)}\n",
            GROUPS_VIEW,
        )

    def test_hint_names_both_fallbacks(self):
        # у бэк-офиса направлений нет: подсказка обязана сказать и про операторскую
        self.assertIn(
            "'Без модели сотрудники группы считаются по модели своего направления, "
            "а без направления — по операторской.'",
            GROUPS_VIEW,
        )

    def test_card_does_not_invent_a_model(self):
        self.assertIn(
            "{g.calculation_model_code && (\n"
            "                                                <IosBadge tone={MODEL_TONE[g.calculation_model_code] || 'slate'}>",
            GROUPS_VIEW,
        )

    def test_model_window_starts_empty_for_a_group_without_a_model(self):
        self.assertIn("const [newModelCode, setNewModelCode] = useState('');", GROUPS_VIEW)
        self.assertIn("setNewModelCode(g.calculation_model_code || '');", GROUPS_VIEW)
        self.assertIn(
            "{modelGroup.calculation_model_code ? (\n"
            "                            <IosBadge tone={MODEL_TONE[modelGroup.calculation_model_code] || 'slate'}>",
            GROUPS_VIEW,
        )
        self.assertIn(
            "disabled={modelBusy || !modelGroup || !newModelCode "
            "|| newModelCode === modelGroup?.calculation_model_code}",
            GROUPS_VIEW,
        )
        self.assertIn(
            "{modelGroup?.calculation_model_code ? 'Сменить модель' : 'Задать модель'}",
            GROUPS_VIEW,
        )

    def test_hours_accounting_screen_uses_the_screen_model_rule(self):
        # правило — в src/utils/hoursScreenModel.js (tests/hours_screen_model.test.mjs)
        self.assertIn("import { hoursScreenModelCode } from './utils/hoursScreenModel';", APP)
        self.assertIn(
            "const group = selectedGroupId && Array.isArray(groupsList)\n"
            "                ? groupsList.find(x => String(x.id) === String(selectedGroupId))\n"
            "                : null;\n"
            "            return hoursScreenModelCode(group, operators);",
            APP,
        )


if __name__ == "__main__":
    unittest.main()
