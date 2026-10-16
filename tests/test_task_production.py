# -*- coding: utf-8 -*-
"""Тип задачи «Продакшн» и его бриф (задача #354).

Справочник брифа живёт в двух местах — task_production (сервер) и
src/components/tasks/productionBrief.js (форма). Форма показывает варианты из
своего файла, сервер принимает только свои, поэтому первым делом сверяем их
буквально. Дальше — правила самого брифа (task_production целиком, он лист без
Flask и базы), правила задачи в database.py (методы вытаскиваются AST'ом:
конструктор Database лезет в боевую базу) и проводка по маркерам там, где
правило живёт в SQL и без базы его не исполнить.
"""

import ast
import json
import re
import sys
import textwrap
import unittest
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import task_production  # noqa: E402

DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
CLI_PATH = ROOT / "scripts" / "task_board.py"
BRIEF_JS_PATH = ROOT / "src" / "components" / "tasks" / "productionBrief.js"
BRIEF_UI_PATH = ROOT / "src" / "components" / "tasks" / "ProductionBriefUi.jsx"
TASKS_VIEW_PATH = ROOT / "src" / "components" / "tasks" / "TasksView.jsx"
WORKSPACE_PATH = ROOT / "src" / "components" / "tasks" / "TaskBoardWorkspace.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


DATABASE_SOURCE = _read(DATABASE_PATH)
BOT_SOURCE = _read(BOT_PATH)


def _valid_brief(**overrides):
    brief = {
        "item": "static_banner",
        "channels": ["social", "outdoor"],
        "sizes": ["9x16", "1x1"],
        "custom_sizes": [{"width": 728, "height": 90, "unit": "px"}],
        "color": "brandbook",
        "colors": [],
    }
    brief.update(overrides)
    return brief


class CatalogSyncTests(unittest.TestCase):
    """Коды, подписи и размеры справочника — одни и те же в форме и на сервере."""

    @classmethod
    def setUpClass(cls):
        source = _read(BRIEF_JS_PATH)
        start = source.index("/* catalog:start */")
        end = source.index("/* catalog:end */")
        cls.block = source[start:end]

    def _js_section(self, name):
        start = self.block.index(f"export const {name} = [")
        rest = self.block[start:]
        return rest[:rest.index("\n];") + 3]

    def _pairs(self, text):
        return re.findall(r"code: '([^']+)', label: '([^']+)'", text)

    def test_items_and_groups_match(self):
        section = self._js_section("PRODUCTION_ITEM_GROUPS")
        expected = []
        for group_code, group_label, items in task_production.ITEM_GROUPS:
            expected.append((group_code, group_label))
            expected.extend(items)
        self.assertEqual(self._pairs(section), expected)

    def test_channels_match(self):
        self.assertEqual(
            self._pairs(self._js_section("PRODUCTION_CHANNELS")),
            list(task_production.CHANNELS),
        )

    def test_size_presets_match_with_dimensions(self):
        rows = re.findall(
            r"\{ code: '([^']+)', group: '([^']+)', label: '([^']+)', "
            r"width: (\d+), height: (\d+), unit: '([^']+)' \}",
            self._js_section("PRODUCTION_SIZE_PRESETS"),
        )
        self.assertEqual(
            [(code, group, label, int(w), int(h), unit) for code, group, label, w, h, unit in rows],
            list(task_production.SIZE_PRESETS),
        )

    def test_units_and_color_modes_match(self):
        self.assertEqual(
            self._pairs(self._js_section("PRODUCTION_SIZE_UNITS")),
            list(task_production.SIZE_UNITS),
        )
        self.assertEqual(
            self._pairs(self._js_section("PRODUCTION_COLOR_MODES")),
            list(task_production.COLOR_MODES),
        )

    def test_limits_match(self):
        source = _read(BRIEF_JS_PATH)
        for js_name, value in (
            ("PRODUCTION_MAX_CUSTOM_SIZES", task_production.MAX_CUSTOM_SIZES),
            ("PRODUCTION_MAX_SIDE", task_production.MAX_SIDE),
            ("PRODUCTION_MAX_COLORS", task_production.MAX_COLORS),
        ):
            self.assertIn(f"export const {js_name} = {value};", source)
        self.assertIn("export const PRODUCTION_TAG = 'production';", source)
        self.assertIn("export const PRODUCTION_DEPARTMENT_CODE = 'marketing';", source)

    def test_codes_are_unique_within_each_list(self):
        item_codes = [code for _g, _t, items in task_production.ITEM_GROUPS for code, _l in items]
        for codes in (
            item_codes,
            [code for code, _label in task_production.CHANNELS],
            [row[0] for row in task_production.SIZE_PRESETS],
            [code for code, _label in task_production.COLOR_MODES],
        ):
            self.assertEqual(len(codes), len(set(codes)), codes)


class NormalizeBriefTests(unittest.TestCase):
    def test_canonical_form_uses_catalog_order(self):
        brief = task_production.normalize_brief(_valid_brief(
            channels=["outdoor", "social", "outdoor"],
            sizes=["9x16", "1x1", "9x16"],
        ))
        self.assertEqual(brief["channels"], ["social", "outdoor"])
        self.assertEqual(brief["sizes"], ["1x1", "9x16"])
        self.assertEqual(brief["custom_sizes"], [{"width": 728, "height": 90, "unit": "px"}])
        self.assertEqual(brief["colors"], [])

    def test_same_choice_gives_same_brief(self):
        first = task_production.normalize_brief(_valid_brief(channels=["social", "outdoor"]))
        second = task_production.normalize_brief(_valid_brief(channels=["outdoor", "social"]))
        self.assertEqual(first, second)

    def test_json_string_and_bytes_are_accepted(self):
        raw = json.dumps(_valid_brief(), ensure_ascii=False)
        self.assertEqual(
            task_production.normalize_brief(raw),
            task_production.normalize_brief(raw.encode("utf-8")),
        )

    def test_missing_fields_are_listed_in_form_order(self):
        with self.assertRaises(ValueError) as caught:
            task_production.normalize_brief({"color": "custom"})
        self.assertEqual(
            str(caught.exception),
            "PRODUCTION_BRIEF_INCOMPLETE:item,channels,sizes,colors",
        )

    def test_empty_brief_is_incomplete_not_invalid(self):
        for raw in (None, "", "   "):
            with self.assertRaises(ValueError) as caught:
                task_production.normalize_brief(raw)
            self.assertTrue(str(caught.exception).startswith(task_production.INCOMPLETE_PREFIX))

    def test_custom_size_alone_is_enough(self):
        brief = task_production.normalize_brief(_valid_brief(sizes=[]))
        self.assertEqual(brief["sizes"], [])
        self.assertEqual(len(brief["custom_sizes"]), 1)

    def test_custom_size_strings_are_numbers(self):
        brief = task_production.normalize_brief(_valid_brief(
            custom_sizes=[{"width": "300", "height": "250", "unit": "PX"}],
        ))
        self.assertEqual(brief["custom_sizes"], [{"width": 300, "height": 250, "unit": "px"}])

    def test_duplicate_custom_sizes_collapse(self):
        size = {"width": 300, "height": 250, "unit": "px"}
        brief = task_production.normalize_brief(_valid_brief(custom_sizes=[size, dict(size)]))
        self.assertEqual(brief["custom_sizes"], [size])

    def test_unknown_or_broken_values_are_invalid(self):
        broken = (
            _valid_brief(item="billboard_3000"),
            _valid_brief(channels=["tv"]),
            _valid_brief(sizes=["a0"]),
            _valid_brief(color="rainbow"),
            _valid_brief(channels="social"),
            _valid_brief(custom_sizes=[{"width": 0, "height": 90, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": 10.5, "height": 90, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": 10, "height": 90, "unit": "cm"}]),
            _valid_brief(custom_sizes=[{"width": True, "height": 90, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": task_production.MAX_SIDE + 1, "height": 9, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": float("inf"), "height": 9, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": float("nan"), "height": 9, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": "1e400", "height": 9, "unit": "px"}]),
            _valid_brief(custom_sizes=[{"width": "Infinity", "height": 9, "unit": "px"}]),
            '{"item": "flyer", "channels": ["social"], "sizes": [], "color": "brandbook",'
            ' "custom_sizes": [{"width": Infinity, "height": 9, "unit": "px"}]}',
            _valid_brief(custom_sizes=[
                {"width": n, "height": 10, "unit": "px"} for n in range(1, task_production.MAX_CUSTOM_SIZES + 2)
            ]),
            "{не json",
            ["social"],
        )
        for raw in broken:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError) as caught:
                    task_production.normalize_brief(raw)
                self.assertEqual(str(caught.exception), task_production.INVALID_BRIEF)

    def test_custom_tones_need_at_least_one_color(self):
        with self.assertRaises(ValueError) as caught:
            task_production.normalize_brief(_valid_brief(color="custom", colors=[]))
        self.assertEqual(str(caught.exception), "PRODUCTION_BRIEF_INCOMPLETE:colors")

    def test_palette_is_normalized_in_order_without_repeats(self):
        brief = task_production.normalize_brief(_valid_brief(
            color="custom", colors=["#ffd400", "#1a1916", "#FFD400", "#fd4", " #0aF "]))
        # #fd4 — краткая запись, разворачивается в #FFDD44; порядок — как добавляли.
        self.assertEqual(brief["colors"], ["#FFD400", "#1A1916", "#FFDD44", "#00AAFF"])

    def test_palette_is_dropped_when_tones_are_not_custom(self):
        brief = task_production.normalize_brief(_valid_brief(color="designer", colors=["#FF0000"]))
        self.assertEqual(brief["colors"], [])

    def test_palette_holds_up_to_twenty_colors(self):
        twenty = ["#%06X" % (index * 0x0A0A0A) for index in range(1, 21)]
        brief = task_production.normalize_brief(_valid_brief(color="custom", colors=twenty))
        self.assertEqual(len(brief["colors"]), task_production.MAX_COLORS)
        for broken in (twenty + ["#ABCDEF"], ["red"], ["#12345"], [123], "#FFD400"):
            with self.subTest(colors=broken):
                with self.assertRaises(ValueError) as caught:
                    task_production.normalize_brief(_valid_brief(color="custom", colors=broken))
                self.assertEqual(str(caught.exception), task_production.INVALID_BRIEF)


class BriefTextTests(unittest.TestCase):
    def test_describe_lists_fields_in_order(self):
        rows = task_production.describe(task_production.normalize_brief(_valid_brief(
            color="custom", colors=["#FFD400", "#1A1916"])))
        self.assertEqual(rows, [
            ("Наименование", "Статичный баннер"),
            ("Адаптация", "Социальные сети и мессенджеры; Наружная реклама"),
            ("Размеры", "1:1 — 1080×1080 px; 9:16 — 1080×1920 px; 728×90 px"),
            ("Цветность", "Свои тона: #FFD400, #1A1916"),
        ])

    def test_unknown_codes_are_shown_as_is(self):
        rows = dict(task_production.describe({
            "item": "old_item", "channels": ["gone"], "sizes": ["a0"], "custom_sizes": [], "color": "x",
        }))
        self.assertEqual(rows["Наименование"], "old_item")
        self.assertEqual(rows["Адаптация"], "gone")
        self.assertEqual(rows["Размеры"], "a0")

    def test_describe_accepts_json_text_and_nothing(self):
        self.assertEqual(task_production.describe(None), [])
        self.assertEqual(task_production.describe_text(None), "")
        text = task_production.describe_text(json.dumps(_valid_brief(), ensure_ascii=False))
        self.assertTrue(text.startswith("Наименование: Статичный баннер\n"))

    def test_print_sizes_are_in_millimetres(self):
        rows = dict(task_production.describe(task_production.normalize_brief(
            _valid_brief(sizes=["a4"], custom_sizes=[{"width": 850, "height": 2000, "unit": "mm"}]))))
        self.assertEqual(rows["Размеры"], "A4 — 210×297 мм; 850×2000 мм")

    def test_item_label(self):
        self.assertEqual(task_production.item_label(_valid_brief()), "Статичный баннер")
        self.assertEqual(task_production.item_label(None), "")

    def test_error_messages_name_the_fields(self):
        self.assertEqual(
            task_production.error_message("PRODUCTION_BRIEF_INCOMPLETE:item,description"),
            "В брифе не заполнено: наименование, описание задания",
        )
        self.assertIn("маркетинга", task_production.error_message(task_production.NEEDS_MARKETING_ASSIGNEE))
        self.assertTrue(task_production.error_message(task_production.INVALID_BRIEF))
        self.assertIsNone(task_production.error_message("INVALID_TAG"))


def _database_methods(names):
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    lines = DATABASE_SOURCE.splitlines(keepends=True)
    found = {}
    for node in database_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            # Декоратор (@staticmethod) выше `def` — без него метод теряет природу.
            first = min([node.lineno] + [item.lineno for item in node.decorator_list])
            found[node.name] = textwrap.dedent("".join(lines[first - 1:node.end_lineno]))
    missing = set(names) - set(found)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return found


def _fake_database():
    names = [
        "_normalize_task_production_brief",
        "_require_task_production_assignee_tx",
        "_edited_task_production_brief",
        "_task_production_needs_assignee_check",
    ]
    body = _database_methods(names)
    namespace = {"task_production": task_production}
    exec("class FakeDatabase:\n" + "".join(textwrap.indent(body[name], "    ") + "\n" for name in names), namespace)
    return namespace["FakeDatabase"]()


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.row


class TaskRulesTests(unittest.TestCase):
    """Правила задачи целиком: бриф + описание задания + исполнитель из маркетинга."""

    DB = _fake_database()

    def test_other_types_never_keep_a_brief(self):
        for tag in ("task", "problem", "suggestion"):
            self.assertIsNone(self.DB._normalize_task_production_brief(tag, _valid_brief(), "текст"))

    def test_production_needs_brief_and_description_reported_together(self):
        with self.assertRaises(ValueError) as caught:
            self.DB._normalize_task_production_brief("production", {"item": "flyer"}, "  ")
        self.assertEqual(
            str(caught.exception),
            "PRODUCTION_BRIEF_INCOMPLETE:channels,sizes,color,description",
        )

    def test_invalid_brief_wins_over_missing_description(self):
        with self.assertRaises(ValueError) as caught:
            self.DB._normalize_task_production_brief("production", _valid_brief(item="nope"), "")
        self.assertEqual(str(caught.exception), task_production.INVALID_BRIEF)

    def test_valid_production_returns_canonical_brief(self):
        brief = self.DB._normalize_task_production_brief("production", _valid_brief(), "Баннер к акции")
        self.assertEqual(brief, task_production.normalize_brief(_valid_brief()))

    def test_all_assignees_must_be_from_marketing(self):
        """Уточнение владельца 16.10.2026: у продакшна в исполнителях только маркетинг."""
        cursor = _Cursor(row=(2,))
        self.DB._require_task_production_assignee_tx(cursor, [7, "12", 7])
        query, params = cursor.executed[0]
        self.assertIn("SELECT COUNT(*) FROM users u JOIN departments d ON d.id = u.department_id", query)
        self.assertEqual(params, ([7, 12], "marketing"))

    def test_one_outsider_in_the_team_is_refused(self):
        for row, ids in (((1,), [7, 12]), ((0,), [7]), (None, [7]), ((0,), [])):
            with self.subTest(row=row, ids=ids):
                with self.assertRaises(ValueError) as caught:
                    self.DB._require_task_production_assignee_tx(_Cursor(row=row), ids)
                self.assertEqual(str(caught.exception), task_production.NEEDS_MARKETING_ASSIGNEE)


class EditRulesTests(unittest.TestCase):
    """Что правка делает с брифом — исполняем правила, а не ищем строки."""

    DB = _fake_database()
    STORED = task_production.normalize_brief(_valid_brief())

    def edited(self, tag, *, current=STORED, has_brief=False, brief=None, description="Описание"):
        return self.DB._edited_task_production_brief(tag, current, has_brief, brief, description)

    def test_type_change_drops_the_brief(self):
        for tag in ("task", "problem", "suggestion"):
            self.assertIsNone(self.edited(tag, has_brief=True, brief=_valid_brief()))

    def test_old_tab_without_brief_keeps_it_untouched(self):
        # Вкладка, открытая до выхода «Продакшна», ключа production_brief не шлёт.
        self.assertIs(self.edited("production"), self.STORED)

    def test_kept_brief_is_not_rechecked_against_the_catalog(self):
        legacy = dict(self.STORED, item="item_removed_later")
        self.assertIs(self.edited("production", current=legacy), legacy)

    def test_description_is_required_even_when_brief_is_kept(self):
        with self.assertRaises(ValueError) as caught:
            self.edited("production", description="   ")
        self.assertEqual(str(caught.exception), "PRODUCTION_BRIEF_INCOMPLETE:description")

    def test_sent_brief_replaces_the_old_one(self):
        brief = self.edited("production", has_brief=True, brief=_valid_brief(color="designer"))
        self.assertEqual(brief["color"], "designer")

    def test_switching_to_production_needs_a_brief(self):
        with self.assertRaises(ValueError) as caught:
            self.edited("production", current=None)
        self.assertEqual(str(caught.exception), "PRODUCTION_BRIEF_INCOMPLETE:item,channels,sizes,color")

    def test_marketing_is_checked_only_when_production_appears_or_team_changes(self):
        check = self.DB._task_production_needs_assignee_check
        self.assertFalse(check(self.STORED, "production", "production", [], []))
        self.assertTrue(check(self.STORED, "production", "task", [], []))
        self.assertTrue(check(self.STORED, "production", "production", [7], []))
        self.assertTrue(check(self.STORED, "production", "production", [], [7]))
        self.assertFalse(check(None, "task", "production", [7], [8]))


class TypeListsTests(unittest.TestCase):
    """Список типов задачи перечислен в восьми местах — они обязаны совпадать."""

    EXPECTED = {"task", "problem", "suggestion", "production"}

    @staticmethod
    def _block(source, start_marker, end_marker):
        start = source.index(start_marker)
        return source[start:source.index(end_marker, start + len(start_marker))]

    def test_every_list_of_types_is_the_same(self):
        view = _read(TASKS_VIEW_PATH)
        meta = self._block(view, "const TAG_META = {", "};")
        options = self._block(view, "const TAG_OPTIONS = [", "];")
        lists = {
            "CHECK в CREATE TABLE": set(re.findall(r"'(\w+)'", self._block(
                DATABASE_SOURCE, "CHECK (tag IN (", "))"))),
            "CHECK в миграции": set(re.findall(r"'(\w+)'", self._block(
                DATABASE_SOURCE, "ADD CONSTRAINT tasks_tag_check", "));"))),
            "TASK_TAG_CODES": set(re.findall(r"'(\w+)'", self._block(
                DATABASE_SOURCE, "TASK_TAG_CODES = (", ")\n"))) | {task_production.PRODUCTION_TAG},
            "TASK_ALLOWED_TAGS": set(re.findall(r"'(\w+)'", self._block(BOT_SOURCE, "TASK_ALLOWED_TAGS = {", "}"))),
            "TASK_TAG_LABELS": set(re.findall(r"'(\w+)':", self._block(BOT_SOURCE, "TASK_TAG_LABELS = {", "}"))),
            "CLI TAG_LABELS": set(re.findall(r"'(\w+)':", self._block(_read(CLI_PATH), "TAG_LABELS = {", "}"))),
            # Во фронте продакшн записан константой PRODUCTION_TAG.
            "TAG_META": set(re.findall(r"^\s+(\w+):\s+\{", meta, re.M))
            | ({task_production.PRODUCTION_TAG} if "[PRODUCTION_TAG]: {" in meta else set()),
            "TAG_OPTIONS": set(re.findall(r"value: '(\w+)'", options))
            | ({task_production.PRODUCTION_TAG} if "value: PRODUCTION_TAG" in options else set()),
            "TAG_LABEL доски": set(re.findall(r"(\w+): '", self._block(
                _read(WORKSPACE_PATH), "const TAG_LABEL = {", "};"))),
        }
        for name, values in lists.items():
            with self.subTest(name=name):
                self.assertEqual(values, self.EXPECTED, name)


def _insert_placeholders_and_params(method):
    """(%s в INSERT INTO tasks, значений в кортеже) — должны совпадать.

    Добавить колонку и забыть плейсхолдер (или наоборот) — ошибка, которая
    вылезет только на живой базе; здесь она видна сразу.
    """
    module = source_cache.parse(DATABASE_SOURCE)
    database_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    function = next(
        node for node in database_class.body if isinstance(node, ast.FunctionDef) and node.name == method
    )
    for call in ast.walk(function):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute" and len(call.args) == 2):
            continue
        query = call.args[0]
        if isinstance(query, ast.Constant) and isinstance(query.value, str) and "INSERT INTO tasks (" in query.value:
            assert isinstance(call.args[1], ast.Tuple)
            return query.value.count("%s"), len(call.args[1].elts)
    raise AssertionError(f"в {method} нет INSERT INTO tasks")


def _method_source(name):
    start = DATABASE_SOURCE.index(f"    def {name}(")
    following = DATABASE_SOURCE.find("\n    def ", start + 10)
    return DATABASE_SOURCE[start:following]


class DatabaseWiringTests(unittest.TestCase):
    """Где правило живёт в SQL — проверяем, что оно там есть."""

    def test_one_list_of_types_everywhere(self):
        self.assertIn("TASK_TAG_CODES = ('task', 'problem', 'suggestion', task_production.PRODUCTION_TAG)",
                      DATABASE_SOURCE)
        # Старые литералы списка типов не должны остаться ни в одной проверке.
        self.assertNotIn("not in ('task', 'problem', 'suggestion')", DATABASE_SOURCE)
        self.assertNotIn("not in {'task', 'problem', 'suggestion'}", DATABASE_SOURCE)
        for name in ("create_task", "edit_task", "get_tasks_for_requester"):
            self.assertIn("TASK_TAG_CODES", _method_source(name), name)

    def test_schema_knows_production(self):
        self.assertIn("CHECK (tag IN ('task', 'problem', 'suggestion', 'production'))", DATABASE_SOURCE)
        self.assertIn("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS production_brief JSONB;", DATABASE_SOURCE)
        # Ограничение меняется только когда в нём ещё нет нового типа: иначе
        # каждый старт брал бы на tasks эксклюзивную блокировку.
        self.assertIn("position('production' IN pg_get_constraintdef(oid)) > 0", DATABASE_SOURCE)

    def test_create_validates_and_stores_brief(self):
        source = _method_source("create_task")
        self.assertIn("production_brief=None", source)
        self.assertIn("self._normalize_task_production_brief(", source)
        self.assertIn("self._require_task_production_assignee_tx(cursor, assignee_ids_norm)", source)
        self.assertIn("production_brief, created_at, updated_at", source)
        self.assertIn("Json(production_brief_norm)", source)
        self.assertEqual(*_insert_placeholders_and_params("create_task"))

    def test_edit_wires_the_brief_rules(self):
        """Сами правила исполняет EditRulesTests; здесь — что edit_task их зовёт."""
        source = _method_source("edit_task")
        self.assertIn("production_brief=_UNSET", source)
        self.assertIn("has_production_brief = production_brief is not _UNSET", source)
        self.assertIn("production_brief_new = self._edited_task_production_brief(", source)
        self.assertIn("if self._task_production_needs_assignee_check(", source)
        self.assertIn("self._require_task_production_assignee_tx(cursor, assignee_ids_new)", source)
        self.assertIn("production_brief = %s,", source)
        self.assertIn('changed_fields.append("production_brief")', source)
        # Сверка состава — после того, как известен диф исполнителей.
        self.assertLess(source.index("removed_assignees = ["),
                        source.index("if self._task_production_needs_assignee_check("))

    def test_regulation_insert_placeholders_match_params(self):
        self.assertEqual(*_insert_placeholders_and_params("materialize_due_regulation_tasks"))

    def test_list_export_and_regulations_carry_the_brief(self):
        listing = _method_source("get_tasks_for_requester")
        self.assertIn("t.info_request_id, t.production_brief", listing)
        self.assertIn('"production_brief": task_production.stored_brief(row[43])', listing)
        export = _method_source("get_tasks_for_export")
        self.assertIn('"production_brief": task_production.stored_brief(row[18])', export)
        regulation = _method_source("materialize_due_regulation_tasks")
        self.assertIn("t.recurrence_next_at, t.estimate_minutes, t.production_brief", regulation)
        self.assertIn("Json(template_production_brief)", regulation)

    def test_recipients_report_department(self):
        source = _method_source("get_task_recipients")
        self.assertIn("LEFT JOIN departments d ON d.id = u.department_id", source)
        self.assertIn('"department_code": row[5]', source)


class RouteWiringTests(unittest.TestCase):
    def test_type_is_allowed_and_named(self):
        self.assertIn("TASK_ALLOWED_TAGS = {'task', 'problem', 'suggestion', 'production'}", BOT_SOURCE)
        self.assertIn("'production': 'Продакшн'", BOT_SOURCE)
        self.assertIn("'production_brief': 'бриф'", BOT_SOURCE)

    def test_create_and_edit_pass_the_brief_and_explain_refusals(self):
        self.assertIn("production_brief_raw = (request.form.get('production_brief') or '').strip() or None",
                      BOT_SOURCE)
        self.assertIn("production_brief=production_brief_raw", BOT_SOURCE)
        self.assertIn("has_production_brief = 'production_brief' in data", BOT_SOURCE)
        self.assertIn('edit_kwargs["production_brief"] = data.get(\'production_brief\')', BOT_SOURCE)
        self.assertEqual(BOT_SOURCE.count("production_error = task_production.error_message(code)"), 2)

    def test_task_production_is_imported_where_used(self):
        """Модуль импортируется внутри функций (как task_photos): тесты вырезают
        функции бота AST'ом, и импорт на уровне модуля до них бы не доехал."""
        tree = source_cache.parse(BOT_SOURCE)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            uses = any(
                isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
                and child.value.id == "task_production"
                for child in ast.walk(node)
            )
            if not uses:
                continue
            imports = any(
                isinstance(child, ast.Import) and any(alias.name == "task_production" for alias in child.names)
                for child in ast.walk(node)
            )
            self.assertTrue(imports, f"{node.name} зовёт task_production без импорта")

    def test_export_has_brief_column(self):
        self.assertIn("'brief':       ('Бриф', 48),", BOT_SOURCE)
        self.assertIn("return task_production.describe_text(task.get('production_brief'))", BOT_SOURCE)

    def test_telegram_names_what_to_make_wherever_task_first_reaches_assignee(self):
        # Постановка, вынос из бэклога и регламентная итерация — во всех трёх
        # сообщениях исполнитель впервые узнаёт о задаче.
        self.assertIn("production_line = _task_production_item_line(created.get('production_brief'))", BOT_SOURCE)
        self.assertIn("_task_production_item_line(task_ctx.get('production_brief')) if event_norm == 'promoted'",
                      BOT_SOURCE)
        self.assertEqual(BOT_SOURCE.count("_task_production_item_line(task_ctx.get('production_brief'))"), 2)
        self.assertIn('"production_brief": row[14]', BOT_SOURCE)


class FrontWiringTests(unittest.TestCase):
    def test_production_type_always_offered_with_marketing_only_team(self):
        """Тип виден всегда; при нём в исполнителях только маркетинг, остальных
        выбор «Продакшна» снимает (уточнение владельца 16.10.2026)."""
        source = _read(TASKS_VIEW_PATH)
        composer = source[source.index("const TaskComposerForm = ("):source.index("const TASK_MESSAGE_KIND_LABEL")]
        self.assertIn("onClick={() => pickTag(option.value)}", composer)
        self.assertIn("{ tag, assigneeIds: assigneeIds.filter((id) => marketingIds.has(String(id))) }", composer)
        self.assertIn("? recipientOptions.filter((option) => marketingIds.has(option.value)", composer)
        self.assertIn("options={assigneeOptions}", composer)
        self.assertIn("&& (!isProduction || marketingIds.has(String(currentUserId)));", composer)
        self.assertNotIn("typeInline", composer)
        # Бриф — сразу под «Типом» в раскрытой панели.
        self.assertLess(composer.index('data-issue-anchor="tag"'), composer.index("<ProductionBriefFields"))
        self.assertIn("[PRODUCTION_TAG]: { label: 'Продакшн', badge: 'tv-badge-fuchsia' },", source)
        self.assertIn("production_brief: values.tag === PRODUCTION_TAG ? briefToPayload(values.productionBrief) : null,",
                      source)
        self.assertIn("body.append('production_brief', JSON.stringify(payload.production_brief))", source)

    def test_pinned_quick_form_offers_production_only_for_production_tasks(self):
        """У быстрой формы закреплённой задачи полей брифа нет — продакшн там
        только показывается у задачи, которая уже этого типа."""
        source = _read(TASKS_VIEW_PATH)
        self.assertIn("(quickForm.tag === PRODUCTION_TAG ? TAG_OPTIONS : BASIC_TAG_OPTIONS).map(", source)
        # Полный список типов — только там, где рядом есть поля брифа.
        self.assertEqual(source.count("{TAG_OPTIONS.map((option) => ("), 1)
        self.assertIn("productionAllowedFor(\n          values.assigneeIds,", source)

    def test_board_and_cli_name_the_type(self):
        self.assertIn("production: 'Продакшн'", _read(WORKSPACE_PATH))
        self.assertIn("'production': 'продакшн'", _read(CLI_PATH))

    def test_ui_file_does_not_collide_with_logic_file_on_mac(self):
        """macOS не различает регистр: ProductionBrief.jsx рядом с productionBrief.js
        для импорта без расширения — один и тот же файл, и сборка брала не тот."""
        same_stem = [
            path.name for path in BRIEF_UI_PATH.parent.iterdir()
            if path.suffix in (".js", ".jsx") and path.stem.lower() == "productionbrief"
        ]
        self.assertEqual(same_stem, ["productionBrief.js"])


if __name__ == "__main__":
    unittest.main()
