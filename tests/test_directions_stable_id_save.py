"""save_directions: стабильный id направления.

Живая строка направления держит id навсегда: переименование/настройки меняются
на месте (операторы и внешние связки не отвязываются), смена критериев уводит
старую шкалу в архивную строку (canonical_id -> живая строка) и перевешивает на
неё исторические оценки. Удаление — деактивация + отвязка операторов.
"""
import ast
import json
import textwrap
import unittest
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


def _module_function_source(source, name):
    module = ast.parse(source)
    node = next(
        n for n in module.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    return ast.get_source_segment(source, node)


def _database_method_source(name):
    source = _read(DATABASE_PATH)
    module = ast.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, method))


def _load_save_directions():
    source = _read(DATABASE_PATH)
    namespace = {
        "json": json, "Dict": Dict, "List": List, "Optional": Optional,
        # контракт normalize_calculation_model_code: допустимые коды моделей
        "CALCULATION_MODEL_OPERATOR": "operator",
        "CALCULATION_MODEL_ALLOWED": {"operator", "chat_manager", "tez_line", "tez_op"},
    }
    exec(textwrap.dedent(_module_function_source(source, "normalize_calculation_model_code")), namespace)
    exec(_database_method_source("save_directions"), namespace)
    return namespace["save_directions"]


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeCursor:
    """Первый fetchall отдаёт активные направления; каждый fetchone — очередной id."""

    def __init__(self, active_rows, returning_ids=()):
        self.active_rows = active_rows
        self.returning_ids = list(returning_ids)
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((" ".join(query.split()), params))

    def executemany(self, query, param_list):
        self.executions.append((" ".join(query.split()), list(param_list)))

    def fetchall(self):
        return self.active_rows

    def fetchone(self):
        return (self.returning_ids.pop(0),)


def _run(active_rows, payload, scope=367, returning_ids=()):
    cursor = _FakeCursor(active_rows, returning_ids)

    class FakeDatabase:
        save_directions = _load_save_directions()

        def _get_cursor(self):
            return _CursorContext(cursor)

    FakeDatabase().save_directions(payload, scope_department_id=scope)
    return cursor


CRITERIA_A = [{"name": "Приветствие", "weight": 50}, {"name": "Скрипт", "weight": 50}]
CRITERIA_B = [{"name": "Скрипт", "weight": 100}]
ROW_OSNOVA = (73, "Основа", True, CRITERIA_A, 1, "operator", 367)
ROW_POTOK = (74, "Поток", True, CRITERIA_A, 1, "operator", 367)


class RenameKeepsIdTests(unittest.TestCase):
    def test_rename_updates_in_place_and_touches_nothing_else(self):
        cursor = _run(
            [ROW_OSNOVA],
            [{"id": 73, "name": "Основа ОП", "hasFileUpload": True,
              "criteria": CRITERIA_A, "calculationModelCode": "operator"}],
        )
        updates = [(q, p) for q, p in cursor.executions if q.startswith("UPDATE directions SET name")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1], ("Основа ОП", True, "operator", 73))
        joined = " | ".join(q for q, _ in cursor.executions)
        self.assertNotIn("UPDATE users", joined)
        self.assertNotIn("INSERT INTO directions", joined)
        self.assertNotIn("is_active = FALSE", joined)
        self.assertNotIn("UPDATE calls", joined)

    def test_noop_save_executes_no_writes(self):
        cursor = _run(
            [ROW_OSNOVA],
            [{"id": 73, "name": "Основа", "hasFileUpload": True,
              "criteria": CRITERIA_A, "calculationModelCode": "operator"}],
        )
        writes = [q for q, _ in cursor.executions if not q.startswith("SELECT")]
        self.assertEqual(writes, [])


class CriteriaChangeArchivesOldScaleTests(unittest.TestCase):
    def test_archive_row_and_frozen_tables_remap(self):
        cursor = _run(
            [ROW_OSNOVA],
            [{"id": 73, "name": "Основа", "hasFileUpload": True,
              "criteria": CRITERIA_B, "calculationModelCode": "operator"}],
            returning_ids=[99],
        )
        joined = [q for q, _ in cursor.executions]
        archive_inserts = [(q, p) for q, p in cursor.executions
                           if q.startswith("INSERT INTO directions") and "canonical_id" in q]
        self.assertEqual(len(archive_inserts), 1)
        self.assertEqual(archive_inserts[0][1], (73,))
        self.assertIn("SELECT name, has_file_upload, criteria, calculation_model_code, version, previous_version_id, department_id, FALSE, id FROM directions WHERE id = %s RETURNING id",
                      archive_inserts[0][0])
        for frozen_table in ("calls", "calibration_rooms", "calibration_room_calls"):
            remaps = [(q, p) for q, p in cursor.executions
                      if q == f"UPDATE {frozen_table} SET direction_id = %s WHERE direction_id = %s"]
            self.assertEqual(len(remaps), 1, frozen_table)
            self.assertEqual(remaps[0][1], (99, 73))
        bumps = [(q, p) for q, p in cursor.executions if "version = version + 1" in q]
        self.assertEqual(len(bumps), 1)
        self.assertEqual(bumps[0][1][-2:], (99, 73))  # previous_version_id=архив, id живой строки
        self.assertNotIn("UPDATE users", " | ".join(joined))


class DeletionTests(unittest.TestCase):
    def test_missing_direction_is_deactivated_and_users_detached(self):
        cursor = _run(
            [ROW_OSNOVA, ROW_POTOK],
            [{"id": 73, "name": "Основа", "hasFileUpload": True,
              "criteria": CRITERIA_A, "calculationModelCode": "operator"}],
        )
        deact = [(q, p) for q, p in cursor.executions if "SET is_active = FALSE" in q]
        self.assertEqual(len(deact), 1)
        self.assertEqual(deact[0][1], ([74],))
        detach = [(q, p) for q, p in cursor.executions
                  if q == "UPDATE users SET direction_id = NULL WHERE direction_id = ANY(%s)"]
        self.assertEqual(len(detach), 1)
        self.assertEqual(detach[0][1], ([74],))


class ValidationTests(unittest.TestCase):
    def test_foreign_or_stale_id_is_rejected(self):
        with self.assertRaises(ValueError):
            _run([ROW_OSNOVA], [{"id": 70, "name": "Чужое", "criteria": []}])

    def test_duplicate_names_are_rejected(self):
        with self.assertRaises(ValueError):
            _run([ROW_OSNOVA], [
                {"id": 73, "name": "Основа", "criteria": CRITERIA_A},
                {"name": "Основа", "criteria": []},
            ])


class LegacyPayloadTests(unittest.TestCase):
    def test_entry_without_id_matches_by_name(self):
        cursor = _run(
            [ROW_OSNOVA],
            [{"name": "Основа", "hasFileUpload": True,
              "criteria": CRITERIA_A, "calculationModelCode": "operator"}],
        )
        writes = [q for q, _ in cursor.executions if not q.startswith("SELECT")]
        self.assertEqual(writes, [])

    def test_new_direction_inserted_with_version_1(self):
        cursor = _run(
            [ROW_OSNOVA],
            [
                {"id": 73, "name": "Основа", "hasFileUpload": True,
                 "criteria": CRITERIA_A, "calculationModelCode": "operator"},
                {"name": "Новое", "hasFileUpload": False, "criteria": [],
                 "calculationModelCode": "operator"},
            ],
        )
        inserts = [(q, p) for q, p in cursor.executions
                   if q.startswith("INSERT INTO directions") and "VALUES (%s, %s, %s, %s, 1, NULL, %s)" in q]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1], [("Новое", False, "[]", "operator", 367)])


class SchemaAndCallQaLinkageTests(unittest.TestCase):
    """Статические инварианты: canonical_id и связка направлений в ИИ-оценке."""

    def test_canonical_id_migration_present(self):
        db = _read(DATABASE_PATH)
        self.assertIn("ADD COLUMN IF NOT EXISTS canonical_id INTEGER REFERENCES directions(id)", db)
        self.assertIn("UPDATE directions SET canonical_id = chain.head", db)

    def test_call_qa_uses_canonical_identity(self):
        criteria = _read(ROOT / "call_qa" / "evaluation" / "criteria.py")
        self.assertIn("canonical_id = int(row[3] or row[0])", criteria)
        self.assertIn('criterion_identity(canonical_id', criteria)
        self.assertIn("scale_fingerprint(canonical_id", criteria)

    def test_call_qa_filters_use_direction_family(self):
        cfg = _read(ROOT / "call_qa" / "config.py")
        self.assertIn("def op_direction_id_family(cur)", cfg)
        api = _read(ROOT / "call_qa" / "api.py")
        self.assertIn("config.op_direction_id_family(cur)", api)
        self.assertNotIn("(config.OP_DIRECTION_IDS, limit)", api)
        batch = _read(ROOT / "call_qa" / "batch_eval.py")
        self.assertIn("config.op_direction_id_family(cur)", batch)


if __name__ == "__main__":
    unittest.main()
