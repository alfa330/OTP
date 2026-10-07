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
import logging
import textwrap
import unittest
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from unittest import mock

from group_late import config as attendance_config
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
        self.assertIs(row[13], True)

    def test_removed_shift(self):
        rows = self._diff(_state((("09:00", "17:00", "regular"),)), _state())
        self.assertEqual(_actions(rows), ["removed"])
        row = rows[0]
        self.assertIsNone(row[4])
        self.assertIsNone(row[5])
        self.assertEqual(row[6], _hhmm("09:00"))
        self.assertEqual(row[7], _hhmm("17:00"))
        self.assertIs(row[13], False)

    def test_row_remembers_whether_the_day_was_empty(self):
        """По флагу сводка отличает первичное внесение от правки.

        Из одних действий этого не видно: вторая смена в день, где смена уже
        стоит, и смена на пустой день пишутся одинаковым 'added' — на бою таких
        «вторых» смен 40, и без флага они пропали бы из сводки как внесение.
        """
        cases = (
            ("смена на пустой день", _state(), _state((("09:00", "13:00", "regular"),)), True),
            ("выходной на пустой день", _state(), _state(day_off=True), True),
            ("вторая смена к имеющейся",
             _state((("09:00", "13:00", "regular"),)),
             _state((("09:00", "13:00", "regular"), ("18:00", "22:00", "regular"))), False),
            ("смена поверх выходного", _state(day_off=True),
             _state((("09:00", "17:00", "regular"),)), False),
        )
        for title, before, after, expected in cases:
            with self.subTest(title):
                rows = self._diff(before, after)
                self.assertTrue(rows)
                self.assertEqual({row[13] for row in rows}, {expected})

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


class _FixedNow(datetime):
    """«Сегодня» для проверок — 7 октября 2026: метод сам спрашивает время."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 7, 12, 0, tzinfo=tz)


TODAY = date(2026, 10, 7)
PLAN_OPERATOR = 509      # оператор отдела, чей план «Отметки» берут из графика
OTHER_OPERATOR = 77      # оператор отдела, у которого план приходит из источников


class _ReopenCursor:
    def __init__(self, plan_operators=(PLAN_OPERATOR,), fail_on=None):
        self.plan_operators = set(plan_operators)
        self.fail_on = fail_on
        self.calls = []
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        if self.fail_on and flat.startswith(self.fail_on):
            raise RuntimeError("сбой базы")
        if flat.startswith("SELECT u.id"):
            self._rows = [(operator_id,) for operator_id in params[0]
                          if operator_id in self.plan_operators]
        elif flat.startswith("UPDATE glb_attendance_days"):
            self.rowcount = len(params[0])

    def fetchall(self):
        return self._rows

    def statements(self, prefix):
        return [call for call in self.calls if call[0].startswith(prefix)]


def _class_constant(name):
    _, module = _parsed_module(DATABASE_PATH)
    class_node = next(node for node in module.body
                      if isinstance(node, ast.ClassDef) and node.name == "Database")
    node = next(item for item in class_node.body
                if isinstance(item, ast.Assign) and getattr(item.targets[0], "id", None) == name)
    return ast.literal_eval(node.value)


def _make_reopen_dummy():
    namespace = {"datetime": _FixedNow, "date": date, "timedelta": timedelta, "logging": logging}
    exec(_function_source(DATABASE_PATH, "_glb_reopen_attendance_days_tx", class_name="Database"),
         namespace)
    dummy = type("ReopenDummy", (), {
        "GLB_ATTENDANCE_REOPEN_DAYS": _class_constant("GLB_ATTENDANCE_REOPEN_DAYS"),
        "_glb_reopen_attendance_days_tx": namespace["_glb_reopen_attendance_days_tx"],
    })
    return dummy()


class AttendanceReopenTests(unittest.TestCase):
    """Правка графика задним числом возвращает день «Отметок» в пересборку.

    Боевой случай 07.10.2026: отделу «Регионы» график на 1–6 октября внесли 6–7
    октября. Раздел «Отметки» эти дни уже собрал без плана и сам не пересчитывал —
    58 человеко-дней стояли «Вне графика» при заведённых сменах."""

    WINDOW = _class_constant("GLB_ATTENDANCE_REOPEN_DAYS")

    def setUp(self):
        self.dummy = _make_reopen_dummy()
        self.differ = _make_diff_dummy()

    def _rows(self, operator_id, day, before=None, after=None):
        """Строки журнала — настоящим диффером: раскладку кортежа читает метод."""
        before = before if before is not None else _state()
        after = after if after is not None else _state((("09:00", "19:00", "regular"),))
        return self.differ._diff_schedule_day(operator_id, day, before, after, ACTOR)

    def _reopen(self, rows, cursor=None):
        cursor = cursor or _ReopenCursor()
        return self.dummy._glb_reopen_attendance_days_tx(cursor, rows), cursor

    def test_shift_entered_for_a_past_day_reopens_that_day(self):
        day = TODAY - timedelta(days=2)
        result, cursor = self._reopen(self._rows(PLAN_OPERATOR, day))
        self.assertEqual(result, 1)
        asked = cursor.statements("SELECT u.id")[0][1]
        self.assertEqual(asked, ([PLAN_OPERATOR], sorted(attendance_config.ICORE_PLAN_DEPARTMENTS.values())))
        self.assertEqual(cursor.statements("UPDATE glb_attendance_days")[0][1], ([day],))

    def test_removed_and_moved_shifts_count_too(self):
        day = TODAY - timedelta(days=1)
        shift = _state((("09:00", "19:00", "regular"),))
        for after in (_state(), _state((("10:00", "19:00", "regular"),))):
            with self.subTest(after=after):
                _, cursor = self._reopen(self._rows(PLAN_OPERATOR, day, before=shift, after=after))
                self.assertEqual(cursor.statements("UPDATE glb_attendance_days")[0][1], ([day],))

    def test_today_and_future_never_touch_the_cache(self):
        """Обычная правка графика (вперёд) не должна стоить ни одного запроса:
        сегодняшний день раздел и так собирает живьём."""
        rows = self._rows(PLAN_OPERATOR, TODAY) + self._rows(PLAN_OPERATOR, TODAY + timedelta(days=5))
        result, cursor = self._reopen(rows)
        self.assertEqual((result, cursor.calls), (0, []))

    def test_day_off_alone_is_not_a_plan_change(self):
        rows = self._rows(PLAN_OPERATOR, TODAY - timedelta(days=2),
                          before=_state(), after=_state(day_off=True))
        self.assertEqual(_actions(rows), ["day_off_set"])
        result, cursor = self._reopen(rows)
        self.assertEqual((result, cursor.calls), (0, []))

    def test_departments_with_source_plan_keep_their_days(self):
        """У остальных отделов план приходит из Workpace и Clockster: правка их
        графика «Отметки» не меняет, и пересобирать день незачем."""
        result, cursor = self._reopen(self._rows(OTHER_OPERATOR, TODAY - timedelta(days=2)))
        self.assertEqual(result, 0)
        self.assertEqual(cursor.statements("UPDATE"), [])

    def test_only_days_of_plan_operators_are_reopened(self):
        own_day, foreign_day = TODAY - timedelta(days=3), TODAY - timedelta(days=4)
        rows = self._rows(PLAN_OPERATOR, own_day) + self._rows(OTHER_OPERATOR, foreign_day)
        _, cursor = self._reopen(rows)
        self.assertEqual(cursor.statements("SELECT u.id")[0][1][0], [OTHER_OPERATOR, PLAN_OPERATOR])
        self.assertEqual(cursor.statements("UPDATE glb_attendance_days")[0][1], ([own_day],))

    def test_days_beyond_the_window_are_left_to_the_refresh_button(self):
        """Давний день Clockster отдаёт уже неполным — автоматическая пересборка
        стёрла бы из него строки центрального офиса."""
        edge = TODAY - timedelta(days=self.WINDOW)
        _, cursor = self._reopen(self._rows(PLAN_OPERATOR, edge - timedelta(days=1)))
        self.assertEqual(cursor.calls, [])
        _, cursor = self._reopen(self._rows(PLAN_OPERATOR, edge))
        self.assertEqual(cursor.statements("UPDATE glb_attendance_days")[0][1], ([edge],))

    def test_no_switched_department_means_no_work(self):
        with mock.patch.object(attendance_config, "ICORE_PLAN_DEPARTMENTS", {}):
            result, cursor = self._reopen(self._rows(PLAN_OPERATOR, TODAY - timedelta(days=2)))
        self.assertEqual((result, cursor.calls), (0, []))

    def test_failure_never_cancels_the_schedule_edit(self):
        """Метод зовётся в транзакции правки графика: сбой откатывается до
        SAVEPOINT и наружу не выходит — иначе вместе с ним пропала бы смена."""
        for fail_on in ("SELECT u.id", "UPDATE glb_attendance_days"):
            with self.subTest(fail_on=fail_on):
                cursor = _ReopenCursor(fail_on=fail_on)
                with self.assertLogs(level="ERROR"):
                    result, _ = self._reopen(self._rows(PLAN_OPERATOR, TODAY - timedelta(days=2)),
                                             cursor)
                self.assertEqual(result, 0)
                order = [call[0] for call in cursor.calls]
                self.assertEqual(order[0], "SAVEPOINT sp_glb_reopen_attendance")
                self.assertEqual(order[-1], "ROLLBACK TO SAVEPOINT sp_glb_reopen_attendance")
                self.assertNotIn("RELEASE SAVEPOINT sp_glb_reopen_attendance", order)

    def test_unreadable_change_row_is_not_fatal_either(self):
        """До SAVEPOINT откатывать нечего — но и наружу сбой выйти не должен."""
        cursor = _ReopenCursor()
        with self.assertLogs(level="ERROR"):
            result = self.dummy._glb_reopen_attendance_days_tx(cursor, [(PLAN_OPERATOR,)])
        self.assertEqual((result, cursor.calls), (0, []))

    def test_success_releases_the_savepoint(self):
        _, cursor = self._reopen(self._rows(PLAN_OPERATOR, TODAY - timedelta(days=2)))
        order = [call[0] for call in cursor.calls]
        self.assertEqual(order[0], "SAVEPOINT sp_glb_reopen_attendance")
        self.assertEqual(order[-1], "RELEASE SAVEPOINT sp_glb_reopen_attendance")

    def test_day_is_reopened_not_erased(self):
        """Строки дня остаются на экране до пересборки, а запись реестра дней —
        на месте: по ней же часы СВ узнают, какие дни собраны с Clockster."""
        source = _function_source(DATABASE_PATH, "_glb_reopen_attendance_days_tx", class_name="Database")
        self.assertNotIn("DELETE", source)
        self.assertNotIn("glb_attendance_rows", source)
        _, cursor = self._reopen(self._rows(PLAN_OPERATOR, TODAY - timedelta(days=2)))
        update = cursor.statements("UPDATE glb_attendance_days")[0][0]
        self.assertIn("SET built_at = day::timestamp AT TIME ZONE 'Asia/Almaty' WHERE day = ANY(%s)", update)

    def test_history_writer_reopens_after_recording(self):
        """Точка одна на все пути записи графика (их сторожит
        test_every_shift_writer_records_history), поэтому и вызов нужен один."""
        writer = _function_source(DATABASE_PATH, "_record_schedule_day_changes_tx", class_name="Database")
        insert_at = writer.index("INSERT INTO work_shift_changes")
        call_at = writer.index("self._glb_reopen_attendance_days_tx(cursor, rows)")
        self.assertLess(insert_at, call_at)


class ScheduleChangeSchemaTests(unittest.TestCase):
    def test_table_and_indexes_are_idempotent(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("CREATE TABLE IF NOT EXISTS work_shift_changes", source)
        self.assertIn("idx_work_shift_changes_operator_date", source)
        self.assertIn("idx_work_shift_changes_date", source)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_work_shift_changes_operator_date", source)

    def test_day_was_empty_column_is_added_idempotently(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        self.assertIn(
            "ALTER TABLE work_shift_changes ADD COLUMN IF NOT EXISTS day_was_empty BOOLEAN NULL",
            source)

    def test_insert_columns_match_the_diff_row(self):
        """Кортеж диффа и список колонок INSERT расходятся молча: execute_values
        упадёт только на проде, на первой же правке графика."""
        writer = _function_source(DATABASE_PATH, "_record_schedule_day_changes_tx", class_name="Database")
        columns = writer[writer.index("INSERT INTO work_shift_changes ("):]
        columns = columns[columns.index("(") + 1:columns.index(")")]
        names = [name.strip() for name in columns.split(",") if name.strip()]
        self.assertEqual(names[-1], "day_was_empty")

        rows = _make_diff_dummy()._diff_schedule_day(
            42, DAY, _state(), _state((("09:00", "17:00", "regular"),)), ACTOR)
        self.assertEqual(len(rows[0]), len(names))

    def test_actor_name_is_denormalised(self):
        """Имя автора хранится копией: после увольнения и переименования
        строка журнала обязана оставаться читаемой."""
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("actor_name VARCHAR(255) NOT NULL DEFAULT ''", source)
        self.assertIn("actor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL", source)
        self.assertIn("COALESCE(NULLIF(c.actor_name, ''), actor.name, '')", source)


if __name__ == "__main__":
    unittest.main()
