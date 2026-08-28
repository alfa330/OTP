# -*- coding: utf-8 -*-
"""История изменений в графиках смен (задача #235).

Что здесь проверяется и почему именно это.

История пишется не построчно по `work_shifts`, а сравнением состояния дня «до»
и «после» операции. Причина — в самом коде графиков: правка смены физически
делается как удаление старой строки и вставка новой, а публикация аукциона
стирает неделю целиком и собирает её заново. Построчный аудит выдавал бы
«удалена + добавлена» на каждую смену при каждой перепубликации, даже когда
ничего не менялось. Поэтому тесты сосредоточены на диффере:

* повторная публикация без изменений не пишет НИ ОДНОЙ строки;
* сдвиг времени читается как «изменена», а не как пара «удалена/добавлена»;
* добавление, удаление и смена вида смены различаются;
* выходной попадает в историю отдельным действием;
* ночная смена (конец меньше начала) сопоставляется корректно.

Отдельно сторожим проводку актора: сигнатуры публичных методов и вызовы из
роутов должны передавать, КТО правит, — без этого требование задачи («ФИО
супервайзера») не выполняется, а поломка тихая: история просто станет
безымянной.

Тест герметичен: `database.py` и `bot_schedule2.py` не импортируются (на
последней строке `database.py` создаётся `Database()`, который поднимает пул к
боевой БД и валит сбор всего набора). Нужные функции достаются через AST.
"""

import ast
import textwrap
import unittest
from datetime import date, time
from functools import lru_cache
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"

DIFF_METHODS = (
    "_pair_shift_changes",
    "_diff_schedule_day",
)


@lru_cache(maxsize=None)
def _parsed_module(path):
    source = path.read_text(encoding="utf-8-sig")
    return source, source_cache.parse(source)


@lru_cache(maxsize=None)
def _function_source(path, function_name, class_name=None):
    source, module = _parsed_module(path)
    body = module.body
    if class_name:
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        body = class_node.body
    node = next(
        item for item in body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )
    return textwrap.dedent(ast.get_source_segment(source, node))


@lru_cache(maxsize=None)
def _method_node(path, function_name, class_name="Database"):
    _, module = _parsed_module(path)
    class_node = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        item for item in class_node.body
        if isinstance(item, ast.FunctionDef) and item.name == function_name
    )


class _DiffDummy:
    """Минимум, который нужен дифферу: перевод времени смены в минуты."""

    def _schedule_interval_minutes(self, start_value, end_value):
        def to_minutes(value):
            return value.hour * 60 + value.minute

        start_min = to_minutes(start_value)
        end_min = to_minutes(end_value)
        if end_min <= start_min:
            end_min += 24 * 60
        return start_min, end_min


def _make_diff_dummy():
    namespace = {}
    for function_name in DIFF_METHODS:
        exec(_function_source(DATABASE_PATH, function_name, class_name="Database"), namespace)

    dummy = _DiffDummy()
    for function_name in DIFF_METHODS:
        setattr(dummy, function_name, namespace[function_name].__get__(dummy, _DiffDummy))
    return dummy


ACTOR = {"id": 7, "name": "Иванов Иван", "role": "sv", "source": "supervisor"}
DAY = date(2026, 8, 20)


def _hhmm(text):
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def _state(shifts=(), day_off=False):
    return {
        "shifts": [(_hhmm(start), _hhmm(end), shift_type) for start, end, shift_type in shifts],
        "day_off": day_off,
    }


def _actions(rows):
    """Строка журнала — кортеж; действие лежит третьим полем."""
    return [row[2] for row in rows]


class ScheduleDayDiffTests(unittest.TestCase):
    def setUp(self):
        self.dummy = _make_diff_dummy()

    def _diff(self, before, after):
        return self.dummy._diff_schedule_day(42, DAY, before, after, ACTOR)

    def test_unchanged_day_writes_nothing(self):
        """Главное свойство: перепубликация аукциона без правок молчит.

        Публикация физически удаляет и пересоздаёт каждую строку смены с новым
        id, поэтому «ничего не изменилось» обязано давать пустой результат —
        иначе журнал зарастёт шумом за одну неделю.
        """
        state = _state((("09:00", "17:00", "regular"),))
        self.assertEqual(self._diff(state, _state((("09:00", "17:00", "regular"),))), [])

    def test_empty_day_stays_empty(self):
        self.assertEqual(self._diff(_state(), _state()), [])

    def test_added_shift(self):
        rows = self._diff(_state(), _state((("09:00", "17:00", "regular"),)))
        self.assertEqual(_actions(rows), ["added"])
        row = rows[0]
        self.assertEqual(row[0], 42)
        self.assertEqual(row[1], DAY)
        self.assertEqual(row[3], "supervisor")
        self.assertEqual(row[4], _hhmm("09:00"))
        self.assertEqual(row[5], _hhmm("17:00"))
        self.assertIsNone(row[6])
        self.assertIsNone(row[7])
        self.assertEqual(row[10], 7)
        self.assertEqual(row[11], "Иванов Иван")
        self.assertEqual(row[12], "sv")

    def test_removed_shift(self):
        rows = self._diff(_state((("09:00", "17:00", "regular"),)), _state())
        self.assertEqual(_actions(rows), ["removed"])
        row = rows[0]
        self.assertIsNone(row[4])
        self.assertIsNone(row[5])
        self.assertEqual(row[6], _hhmm("09:00"))
        self.assertEqual(row[7], _hhmm("17:00"))

    def test_moved_shift_reads_as_single_change(self):
        """Сдвиг времени — одна правка, а не «удалили и добавили».

        Именно так изменение видит человек, открывший график, и именно этого
        требует постановка: «перенос времени» — отдельное действие.
        """
        rows = self._diff(
            _state((("09:00", "17:00", "regular"),)),
            _state((("10:00", "18:00", "regular"),)),
        )
        self.assertEqual(_actions(rows), ["changed"])
        row = rows[0]
        self.assertEqual(row[6], _hhmm("09:00"))
        self.assertEqual(row[7], _hhmm("17:00"))
        self.assertEqual(row[4], _hhmm("10:00"))
        self.assertEqual(row[5], _hhmm("18:00"))

    def test_non_overlapping_replacement_is_not_a_change(self):
        """Смену сняли утром и поставили вечером — это разные смены."""
        rows = self._diff(
            _state((("08:00", "12:00", "regular"),)),
            _state((("20:00", "23:00", "regular"),)),
        )
        self.assertEqual(sorted(_actions(rows)), ["added", "removed"])

    def test_shift_type_change_keeps_times(self):
        """Границы те же, поменялся вид смены — это тоже правка."""
        rows = self._diff(
            _state((("09:00", "17:00", "regular"),)),
            _state((("09:00", "17:00", "office_practice"),)),
        )
        self.assertEqual(_actions(rows), ["changed"])
        row = rows[0]
        self.assertEqual(row[8], "office_practice")
        self.assertEqual(row[9], "regular")
        self.assertEqual(row[4], row[6])

    def test_merge_of_two_shifts_into_one(self):
        """Добор склеил две смены в одну: одна правка и одно снятие."""
        rows = self._diff(
            _state((("09:00", "13:00", "regular"), ("14:00", "18:00", "regular"))),
            _state((("09:00", "18:00", "regular"),)),
        )
        self.assertEqual(sorted(_actions(rows)), ["changed", "removed"])

    def test_night_shift_pairs_correctly(self):
        """Ночная смена: конец меньше начала, минуты переходят за сутки."""
        rows = self._diff(
            _state((("20:00", "02:00", "regular"),)),
            _state((("21:00", "03:00", "regular"),)),
        )
        self.assertEqual(_actions(rows), ["changed"])

    def test_day_off_set_and_cleared(self):
        set_rows = self._diff(_state(), _state(day_off=True))
        self.assertEqual(_actions(set_rows), ["day_off_set"])

        cleared_rows = self._diff(_state(day_off=True), _state())
        self.assertEqual(_actions(cleared_rows), ["day_off_cleared"])

    def test_day_off_replaces_shift(self):
        """Проставили выходной поверх смены: и снятие смены, и сам выходной."""
        rows = self._diff(
            _state((("09:00", "17:00", "regular"),)),
            _state(day_off=True),
        )
        self.assertEqual(sorted(_actions(rows)), ["day_off_set", "removed"])

    def test_pairing_prefers_bigger_overlap(self):
        """Когда кандидатов несколько, парой считается наибольшее пересечение."""
        pairs, rest_removed, rest_added = self.dummy._pair_shift_changes(
            [(_hhmm("09:00"), _hhmm("13:00")), (_hhmm("18:00"), _hhmm("22:00"))],
            [(_hhmm("18:30"), _hhmm("22:30")), (_hhmm("09:30"), _hhmm("13:30"))],
        )
        self.assertEqual(len(pairs), 2)
        self.assertEqual(rest_removed, [])
        self.assertEqual(rest_added, [])
        matched = {removed[0].strftime("%H:%M"): added[0].strftime("%H:%M") for removed, added in pairs}
        self.assertEqual(matched, {"09:00": "09:30", "18:00": "18:30"})


class ScheduleChangeActorWiringTests(unittest.TestCase):
    """Сторож проводки: актор должен доезжать от роута до записи в журнал."""

    def _arg_names(self, function_name, path=DATABASE_PATH, class_name="Database"):
        node = _method_node(path, function_name, class_name)
        names = [arg.arg for arg in node.args.args]
        names += [arg.arg for arg in node.args.kwonlyargs]
        return names

    def test_public_shift_methods_accept_actor(self):
        for function_name in (
            "save_shift",
            "delete_shift",
            "toggle_day_off",
            "save_shifts_bulk",
            "apply_work_schedule_bulk_actions",
            "import_work_schedule_excel_entries",
        ):
            with self.subTest(function_name):
                self.assertIn("actor_id", self._arg_names(function_name))

    def test_routes_pass_actor_into_db(self):
        """Без этих вызовов история была бы безымянной, а тесты — зелёными."""
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        expectations = (
            "db.save_shift(",
            "db.delete_shift(",
            "db.toggle_day_off(",
            "db.save_shifts_bulk(",
            "db.apply_work_schedule_bulk_actions(",
            "db.import_work_schedule_excel_entries(",
        )
        for call in expectations:
            with self.subTest(call):
                index = source.find(call)
                self.assertNotEqual(index, -1, f"нет вызова {call}")
                tail = source[index:index + 600]
                self.assertIn("actor_id=", tail, f"{call} вызван без actor_id")

    def test_every_shift_writer_records_history(self):
        """Каждый путь, который сам стирает или собирает день, обязан писать историю.

        Список закрытый: новый способ поменять график без записи в журнал —
        это дыра, которую видно только глазами на проде.
        """
        source, module = _parsed_module(DATABASE_PATH)
        class_node = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Database"
        )
        writers = (
            "publish_shift_auction_test_to_work_schedules",
            "post_auction_claim_lot",
            "post_auction_claim_saved_shift",
            "admin_unclaim_shift",
            "operator_cancel_post_auction_claim",
            "admin_claim_shift_for_operator",
            "respond_shift_swap_request",
            # Заявку на изменение смены в график вписывает не публичный
            # respond_shift_change_request, а этот приватный шаг — историю
            # сторожим там, где происходит сама запись.
            "_apply_shift_change_request_tx",
            "save_shift",
            "delete_shift",
            "toggle_day_off",
            "apply_work_schedule_bulk_actions",
            "import_work_schedule_excel_entries",
            "save_schedule_status_period",
            "save_shifts_bulk",
        )
        by_name = {
            node.name: node for node in class_node.body
            if isinstance(node, ast.FunctionDef)
        }
        for function_name in writers:
            with self.subTest(function_name):
                node = by_name.get(function_name)
                self.assertIsNotNone(node, f"метод {function_name} исчез")
                calls = {
                    sub.func.attr
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                }
                self.assertTrue(
                    "_record_schedule_day_changes_tx" in calls or "_schedule_change_audit" in calls,
                    f"{function_name} меняет график, но не пишет историю",
                )

    def test_history_route_uses_viewer_gate_and_scope(self):
        """Историю читает тот, кто видит графики (включая тренера), и только
        по своим операторам: `_resolve_management_requester` тут был бы лишним
        запретом, а отсутствие фильтра зоны видимости — утечкой чужого отдела."""
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        index = source.find("def get_work_schedule_history(")
        self.assertNotEqual(index, -1, "роут истории исчез")
        body = source[index:index + 4000]
        self.assertIn("_resolve_work_schedule_viewer()", body)
        self.assertIn("_filter_operators_for_requester_scope(", body)
        self.assertNotIn("_resolve_management_requester()", body)


class ScheduleChangeSchemaTests(unittest.TestCase):
    def test_table_and_indexes_are_idempotent(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("CREATE TABLE IF NOT EXISTS work_shift_changes", source)
        self.assertIn("idx_work_shift_changes_operator_date", source)
        self.assertIn("idx_work_shift_changes_date", source)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_work_shift_changes_operator_date", source)

    def test_actor_name_is_denormalised(self):
        """Имя автора хранится копией: после увольнения и переименования
        строка журнала обязана оставаться читаемой."""
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("actor_name VARCHAR(255) NOT NULL DEFAULT ''", source)
        self.assertIn("actor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL", source)
        self.assertIn("COALESCE(NULLIF(c.actor_name, ''), actor.name, '')", source)


if __name__ == "__main__":
    unittest.main()
