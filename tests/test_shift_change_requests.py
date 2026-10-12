# -*- coding: utf-8 -*-
"""Заявки операторов на изменение своей смены (задача #17).

Тесты герметичные: методы вынимаются из database.py разбором AST и
выполняются с подставным `self`. К базе не ходим — ни к прод, ни к локальной
(см. память tests-hermetic-vs-prod-db).
"""

import ast
import textwrap
import unittest
from datetime import date, datetime, time as dt_time, timedelta
from functools import lru_cache
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
SOURCES_PATH = ROOT / "notifications" / "sources.py"
BELL_PATH = ROOT / "src" / "components" / "notifications" / "NotificationsBell.jsx"
APP_PATH = ROOT / "src" / "App.jsx"


# Исходник и вырезанные из него методы запоминаем на весь файл: заглушка _Db
# собирается в setUp почти каждого теста, а каждое вырезание — это чтение
# трёх мегабайт и поиск отрезка в них. Файл за время прогона не меняется.
@lru_cache(maxsize=None)
def _database_source():
    return DATABASE_PATH.read_text(encoding="utf-8-sig")


@lru_cache(maxsize=None)
def _method_source(name):
    source = _database_source()
    module = source_cache.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    method = next(
        node for node in database_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, method))


@lru_cache(maxsize=None)
def _module_function_source(name):
    source = _database_source()
    module = source_cache.parse(source)
    func = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, func))


@lru_cache(maxsize=None)
def _class_constant(name):
    """Значение простой константы класса Database — из исходника, без импорта модуля."""
    source = _database_source()
    module = source_cache.parse(source)
    database_class = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )
    for node in database_class.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("в Database нет константы %s" % name)


class _Db:
    """Подставной Database: только те методы, что нужны проверяемой логике."""

    MODULE_FUNCTIONS = ('_time_to_minutes', '_minutes_to_time')
    METHODS = (
        '_normalize_schedule_date',
        '_normalize_schedule_time',
        '_schedule_interval_minutes',
        '_shift_change_minutes',
        '_normalize_shift_change_kind',
        '_normalize_shift_change_payload',
        '_validate_shift_change_against_schedule',
        '_shift_change_pending_conflict',
        '_shift_change_day_pieces',
        '_shift_change_started',
        '_shift_change_target_date',
        '_validate_shift_change_move',
        '_shift_change_day_off_payload',
        '_validate_shift_change_day_off',
        '_serialize_shift_change_request_row',
        '_merge_break_intervals',
        '_swap_extract_interval',
        '_swap_is_interval_fully_covered',
    )

    def __init__(self):
        namespace = {
            'date': date,
            'datetime': datetime,
            'dt_time': dt_time,
            'timedelta': timedelta,
        }
        for function_name in self.MODULE_FUNCTIONS:
            exec(_module_function_source(function_name), namespace)
        for method_name in self.METHODS:
            exec(_method_source(method_name), namespace)
            setattr(self.__class__, method_name, namespace[method_name])
        self.namespace = namespace
        # Виды — из самого модуля. Свой список в тесте пережил бы появление
        # нового вида и оставил бы его без единой проверки.
        self.SHIFT_CHANGE_REQUEST_KINDS = _class_constant('SHIFT_CHANGE_REQUEST_KINDS')
        self.SHIFT_CHANGE_EXCLUSIVE_KINDS = _class_constant('SHIFT_CHANGE_EXCLUSIVE_KINDS')


def _assert_stub_is_complete(testcase, db, methods):
    """Каждый self.<помощник> и каждое имя модуля, которые читает вырезанный
    метод, должны быть в заглушке. Иначе недостающее имя падает NameError
    только на той ветке, до которой дошёл тест, а на остальных молчит."""
    import builtins

    for method_name in methods:
        func = ast.parse(_method_source(method_name)).body[0]
        local_names = {item.arg for item in ast.walk(func) if isinstance(item, ast.arg)}
        local_names |= {
            item.id for item in ast.walk(func)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
        }
        local_names |= {
            item.name for item in ast.walk(func)
            if isinstance(item, ast.FunctionDef) and item is not func
        }
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == 'self'
            ):
                testcase.assertTrue(
                    hasattr(db, node.attr),
                    "%s зовёт self.%s, которого нет в заглушке" % (method_name, node.attr),
                )
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in local_names or hasattr(builtins, node.id):
                    continue
                testcase.assertIn(
                    node.id, set(db.namespace),
                    "%s читает имя модуля %s, которого нет в заглушке" % (method_name, node.id),
                )


class StubCompletenessTests(unittest.TestCase):
    def test_pure_helpers_stub_is_complete(self):
        _assert_stub_is_complete(self, _Db(), _Db.METHODS)

    def test_the_check_itself_notices_a_missing_helper(self):
        """Сверка, которая ничего не ловит, хуже её отсутствия."""
        db = _Db()
        del db.namespace['_minutes_to_time']
        with self.assertRaises(AssertionError):
            _assert_stub_is_complete(self, db, _Db.METHODS)


class ShiftChangeMinutesTests(unittest.TestCase):
    def setUp(self):
        self.db = _Db()

    def test_plain_interval(self):
        self.assertEqual(self.db._shift_change_minutes('09:00', '18:00'), (540, 1080))

    def test_midnight_end_is_lifted_over_the_day(self):
        """00:00 как конец смены — это конец суток, а не их начало."""
        self.assertEqual(self.db._shift_change_minutes('17:00', '00:00'), (1020, 1440))

    def test_night_shift_crosses_midnight(self):
        self.assertEqual(self.db._shift_change_minutes('17:00', '02:00'), (1020, 1560))

    def test_longer_than_a_day_is_rejected(self):
        """Иначе опечатка превращается в тридцатичасовую смену."""
        with self.assertRaises(ValueError):
            self.db._shift_change_minutes('09:00', '09:00')


class ShortenPayloadTests(unittest.TestCase):
    def setUp(self):
        self.db = _Db()

    def _payload(self, seg_start, seg_end, new_start, new_end):
        return self.db._normalize_shift_change_payload('shorten', {
            'segment': {'start': seg_start, 'end': seg_end},
            'newStart': new_start,
            'newEnd': new_end,
        })

    def test_leaving_earlier(self):
        payload = self._payload('09:00', '18:00', '09:00', '15:00')
        self.assertEqual(payload['newStartMin'], 540)
        self.assertEqual(payload['newEndMin'], 900)

    def test_coming_later_and_leaving_earlier_at_once(self):
        payload = self._payload('09:00', '18:00', '10:00', '17:00')
        self.assertEqual((payload['newStartMin'], payload['newEndMin']), (600, 1020))

    def test_outside_the_shift_is_rejected(self):
        with self.assertRaises(ValueError):
            self._payload('09:00', '18:00', '08:00', '15:00')
        with self.assertRaises(ValueError):
            self._payload('09:00', '18:00', '09:00', '19:00')

    def test_unchanged_bounds_are_rejected(self):
        """Заявка «ничего не менять» дошла бы до руководителя пустой."""
        with self.assertRaises(ValueError):
            self._payload('09:00', '18:00', '09:00', '18:00')

    def test_night_shift_new_end_is_lifted_into_the_segment(self):
        """Смена 17:00-02:00, уйти в 01:00: «01:00» это 60 минут, а кусок
        живёт в координатах 1020-1560 — без подъёма проверка «внутри смены»
        отвергла бы совершенно нормальную заявку."""
        payload = self._payload('17:00', '02:00', '17:00', '01:00')
        self.assertEqual(payload['newStartMin'], 1020)
        self.assertEqual(payload['newEndMin'], 1500)

    def test_night_shift_new_start_after_midnight(self):
        payload = self._payload('17:00', '02:00', '01:00', '02:00')
        self.assertEqual(payload['newStartMin'], 1500)
        self.assertEqual(payload['newEndMin'], 1560)


class ExtraPayloadTests(unittest.TestCase):
    def setUp(self):
        self.db = _Db()

    def test_normalizes_to_minutes(self):
        payload = self.db._normalize_shift_change_payload('extra', {'start': '19:00', 'end': '22:00'})
        self.assertEqual((payload['startMin'], payload['endMin']), (1140, 1320))
        self.assertEqual((payload['start'], payload['end']), ('19:00', '22:00'))

    def test_crossing_midnight_is_allowed(self):
        payload = self.db._normalize_shift_change_payload('extra', {'start': '22:00', 'end': '02:00'})
        self.assertEqual((payload['startMin'], payload['endMin']), (1320, 1560))

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.db._normalize_shift_change_kind('delete')


class ValidateAgainstScheduleTests(unittest.TestCase):
    """Сверка с графиком на сейчас — она же защищает от гонки «смену подвинули
    между подачей и согласованием»."""

    def setUp(self):
        self.db = _Db()

    def test_extra_overlapping_existing_shift_is_rejected(self):
        window = [{'start': 540, 'end': 1080}]  # 09:00-18:00
        payload = self.db._normalize_shift_change_payload('extra', {'start': '17:00', 'end': '20:00'})
        with self.assertRaises(ValueError):
            self.db._validate_shift_change_against_schedule('extra', payload, window)

    def test_extra_after_the_shift_is_accepted(self):
        window = [{'start': 540, 'end': 1080}]
        payload = self.db._normalize_shift_change_payload('extra', {'start': '19:00', 'end': '22:00'})
        self.db._validate_shift_change_against_schedule('extra', payload, window)

    def test_shorten_of_a_shift_that_moved_is_rejected(self):
        payload = self.db._normalize_shift_change_payload('shorten', {
            'segment': {'start': '09:00', 'end': '18:00'},
            'newStart': '09:00',
            'newEnd': '15:00',
        })
        moved_window = [{'start': 600, 'end': 1140}]  # смену сдвинули на 10:00-19:00
        with self.assertRaises(ValueError):
            self.db._validate_shift_change_against_schedule('shorten', payload, moved_window)

    def test_shorten_of_an_intact_shift_is_accepted(self):
        payload = self.db._normalize_shift_change_payload('shorten', {
            'segment': {'start': '09:00', 'end': '18:00'},
            'newStart': '09:00',
            'newEnd': '15:00',
        })
        self.db._validate_shift_change_against_schedule('shorten', payload, [{'start': 540, 'end': 1080}])


class ChangePayloadTests(unittest.TestCase):
    """Перенос смены (#349): любые новые границы, в том числе в другой день."""

    def setUp(self):
        self.db = _Db()
        self.day = date(2026, 10, 14)

    def _payload(self, seg_start, seg_end, new_start, new_end, new_date=None):
        body = {
            'segment': {'start': seg_start, 'end': seg_end},
            'newStart': new_start,
            'newEnd': new_end,
        }
        if new_date:
            body['newDate'] = new_date
        return self.db._normalize_shift_change_payload('change', body, self.day)

    def test_bounds_may_leave_the_old_shift(self):
        """То, что сокращение запрещает: раньше начать и позже закончить."""
        payload = self._payload('09:00', '18:00', '08:00', '19:00')
        self.assertEqual((payload['newStartMin'], payload['newEndMin']), (480, 1140))
        self.assertEqual(payload['dayShift'], 0)
        self.assertEqual(payload['newDate'], '2026-10-14')

    def test_same_length_shift_is_a_change(self):
        """«Без обязательного сокращения или увеличения»: длительность прежняя."""
        payload = self._payload('09:00', '18:00', '11:00', '20:00')
        self.assertEqual(
            payload['newEndMin'] - payload['newStartMin'],
            payload['segment']['endMin'] - payload['segment']['startMin'],
        )

    def test_other_day_is_allowed(self):
        payload = self._payload('09:00', '18:00', '09:00', '18:00', new_date='2026-10-16')
        self.assertEqual(payload['dayShift'], 2)
        self.assertEqual(payload['newDate'], '2026-10-16')

    def test_earlier_day_is_allowed_too(self):
        payload = self._payload('09:00', '18:00', '10:00', '19:00', new_date='2026-10-13')
        self.assertEqual(payload['dayShift'], -1)

    def test_unchanged_shift_is_rejected(self):
        with self.assertRaises(ValueError):
            self._payload('09:00', '18:00', '09:00', '18:00')
        with self.assertRaises(ValueError):
            self._payload('09:00', '18:00', '09:00', '18:00', new_date='2026-10-14')

    def test_start_belongs_to_the_new_day_not_to_the_night_tail(self):
        """Сокращение поднимает «01:00» в хвост ночной смены. Перенос — нет:
        здесь 01:00 — раннее утро нового дня, иначе перенос на утро нельзя
        было бы отличить от «оставить только хвост»."""
        payload = self._payload('17:00', '02:00', '01:00', '06:00')
        self.assertEqual((payload['newStartMin'], payload['newEndMin']), (60, 360))

    def test_end_may_cross_midnight(self):
        payload = self._payload('09:00', '18:00', '22:00', '06:00')
        self.assertEqual((payload['newStartMin'], payload['newEndMin']), (1320, 1800))

    def test_shift_date_is_required(self):
        with self.assertRaises(ValueError):
            self.db._normalize_shift_change_payload('change', {
                'segment': {'start': '09:00', 'end': '18:00'},
                'newStart': '10:00',
                'newEnd': '19:00',
            })

    def test_malformed_new_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self._payload('09:00', '18:00', '10:00', '19:00', new_date='16.10.2026')


class MoveValidationTests(unittest.TestCase):
    """Сверка переноса с графиком — и при подаче, и при согласовании.

    Отказы сверяем по тексту, а не по одному лишь ValueError: у функции их
    пять, и «упало, но по другой причине» выглядело бы как пройденный тест.
    """

    def setUp(self):
        self.db = _Db()
        self.day = date(2026, 10, 14)
        self.now = datetime(2026, 10, 12, 10, 0)  # за два дня до смены

    def _payload(self, seg_start, seg_end, new_start, new_end, new_date=None):
        body = {
            'segment': {'start': seg_start, 'end': seg_end},
            'newStart': new_start,
            'newEnd': new_end,
        }
        if new_date:
            body['newDate'] = new_date
        return self.db._normalize_shift_change_payload('change', body, self.day)

    def _check(self, payload, day_map, now=None):
        self.db._validate_shift_change_move(self.day, payload, day_map, now or self.now)

    def _refused(self, message, payload, day_map, now=None):
        with self.assertRaisesRegex(ValueError, message):
            self._check(payload, day_map, now)

    def test_move_may_overlap_its_own_old_place(self):
        """Сдвиг на два часа пересекается с прежним местом смены — это и есть
        перенос, а не конфликт."""
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '18:00'}]}
        self._check(self._payload('09:00', '18:00', '11:00', '20:00'), day_map)

    def test_extended_shift_no_longer_matches(self):
        """Сокращению хватило бы «кусок покрыт графиком». Перенос вычитает
        кусок целиком, и от продлённой смены остался бы обрубок 18:00-20:00.
        Вторая проверка — перенос в свободный день: там отказ не спутать с
        пересечением, и «покрытие» вместо совпадения прошло бы молча."""
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '20:00'}]}
        self._refused('Смена изменилась', self._payload('09:00', '18:00', '11:00', '20:00'), day_map)
        self._refused(
            'Смена изменилась',
            self._payload('09:00', '18:00', '09:00', '18:00', new_date='2026-10-16'), day_map,
        )

    def test_shortened_shift_no_longer_matches(self):
        day_map = {'2026-10-14': [{'start': '10:00', 'end': '17:00'}]}
        self._refused(
            'Смена изменилась',
            self._payload('09:00', '18:00', '09:00', '18:00', new_date='2026-10-16'), day_map,
        )

    def test_missing_shift_is_rejected(self):
        self._refused('Смена изменилась', self._payload('09:00', '18:00', '11:00', '20:00'), {})

    def test_overlap_with_another_piece_is_rejected(self):
        """_save_shift_tx пересекающуюся строку молча удалил бы."""
        day_map = {'2026-10-14': [
            {'start': '09:00', 'end': '13:00'},
            {'start': '15:00', 'end': '19:00'},
        ]}
        self._refused('уже есть смена', self._payload('15:00', '19:00', '12:00', '16:00'), day_map)

    def test_touching_another_piece_is_accepted(self):
        day_map = {'2026-10-14': [
            {'start': '09:00', 'end': '13:00'},
            {'start': '15:00', 'end': '19:00'},
        ]}
        self._check(self._payload('15:00', '19:00', '13:00', '17:00'), day_map)

    def test_other_day_overlap_is_rejected(self):
        day_map = {
            '2026-10-14': [{'start': '09:00', 'end': '18:00'}],
            '2026-10-16': [{'start': '10:00', 'end': '14:00'}],
        }
        self._refused(
            'уже есть смена',
            self._payload('09:00', '18:00', '09:00', '18:00', new_date='2026-10-16'), day_map,
        )

    def test_other_day_free_slot_is_accepted(self):
        day_map = {
            '2026-10-14': [{'start': '09:00', 'end': '18:00'}],
            '2026-10-16': [{'start': '10:00', 'end': '14:00'}],
        }
        self._check(
            self._payload('09:00', '18:00', '15:00', '20:00', new_date='2026-10-16'), day_map
        )

    def test_night_tail_of_the_previous_day_blocks_the_morning(self):
        """Ночная 20:00-08:00 хранится строкой вчерашнего дня, но утро нового
        дня она занимает."""
        day_map = {
            '2026-10-14': [{'start': '12:00', 'end': '18:00'}],
            '2026-10-15': [{'start': '20:00', 'end': '08:00'}],
        }
        self._refused(
            'уже есть смена',
            self._payload('12:00', '18:00', '06:00', '12:00', new_date='2026-10-16'), day_map,
        )
        # Сразу после хвоста — уже можно.
        self._check(
            self._payload('12:00', '18:00', '08:00', '12:00', new_date='2026-10-16'), day_map
        )

    def test_next_day_morning_blocks_a_move_into_the_night(self):
        day_map = {
            '2026-10-14': [{'start': '12:00', 'end': '18:00'}],
            '2026-10-15': [{'start': '01:00', 'end': '09:00'}],
        }
        self._refused('уже есть смена', self._payload('12:00', '18:00', '20:00', '02:00'), day_map)

    def test_touching_at_midnight_is_accepted(self):
        """Стык в полночь — законное состояние графика."""
        day_map = {
            '2026-10-14': [{'start': '12:00', 'end': '18:00'}],
            '2026-10-15': [{'start': '00:00', 'end': '08:00'}],
        }
        self._check(self._payload('12:00', '18:00', '18:00', '00:00'), day_map)

    def test_night_shift_moved_onto_its_own_tail(self):
        """Ночная 17:00-02:00 переезжает на утро следующего дня: её же хвост
        ей не помеха."""
        day_map = {'2026-10-14': [{'start': '17:00', 'end': '02:00'}]}
        self._check(
            self._payload('17:00', '02:00', '00:00', '09:00', new_date='2026-10-15'), day_map
        )

    def test_started_shift_cannot_be_moved(self):
        """Одобрение бывает и через сутки после подачи. Увезти смену, которая
        уже идёт, — обнулить отработанное."""
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '18:00'}]}
        payload = self._payload('09:00', '18:00', '11:00', '20:00')
        self._check(payload, day_map, now=datetime(2026, 10, 14, 8, 59))
        self._refused('уже началась', payload, day_map, now=datetime(2026, 10, 14, 9, 0))
        self._refused('уже началась', payload, day_map, now=datetime(2026, 10, 15, 12, 0))

    def test_new_time_in_the_past_is_rejected(self):
        """Смена вечером, сейчас полдень: увезти её на утро уже нельзя."""
        day_map = {'2026-10-14': [{'start': '18:00', 'end': '22:00'}]}
        payload = self._payload('18:00', '22:00', '09:00', '13:00')
        self._refused('уже прошло', payload, day_map, now=datetime(2026, 10, 14, 12, 0))
        # На завтрашнее утро — можно.
        self._check(
            self._payload('18:00', '22:00', '09:00', '13:00', new_date='2026-10-15'),
            day_map, now=datetime(2026, 10, 14, 12, 0),
        )


class DayOffTests(unittest.TestCase):
    """Выходной по заявке (#349): только на день со сменами, снимаются все."""

    def setUp(self):
        self.db = _Db()
        self.day = date(2026, 10, 14)
        self.now = datetime(2026, 10, 12, 10, 0)

    def _check(self, snapshot_shifts, current_shifts, now=None):
        self.db._validate_shift_change_day_off(
            self.day,
            self.db._shift_change_day_off_payload(snapshot_shifts),
            current_shifts,
            now or self.now,
        )

    def _refused(self, message, snapshot_shifts, current_shifts, now=None):
        with self.assertRaisesRegex(ValueError, message):
            self._check(snapshot_shifts, current_shifts, now)

    def test_client_payload_is_ignored(self):
        """Что снимется, решает график, а не присланное клиентом."""
        self.assertEqual(
            self.db._normalize_shift_change_payload(
                'day_off', {'shifts': [{'start': '00:00', 'end': '01:00'}]}
            ),
            {},
        )

    def test_snapshot_lists_every_piece_of_the_day(self):
        payload = self.db._shift_change_day_off_payload([
            {'start': '15:00', 'end': '19:00'},
            {'start': '09:00', 'end': '13:00'},
        ])
        self.assertEqual(
            [(item['start'], item['end']) for item in payload['shifts']],
            [('09:00', '13:00'), ('15:00', '19:00')],
        )
        self.assertEqual(payload['totalMin'], 480)

    def test_touching_rows_are_one_piece(self):
        """Как в «Моих сменах»: 09-13 и 13-18 оператор видит одной сменой."""
        payload = self.db._shift_change_day_off_payload([
            {'start': '09:00', 'end': '13:00'},
            {'start': '13:00', 'end': '18:00'},
        ])
        self.assertEqual(len(payload['shifts']), 1)
        self.assertEqual(payload['totalMin'], 540)

    def test_night_shift_counts_whole(self):
        """Ночная смена, начавшаяся в день выходного, снимается целиком —
        вместе с часами после полуночи."""
        payload = self.db._shift_change_day_off_payload([{'start': '20:00', 'end': '08:00'}])
        self.assertEqual(payload['totalMin'], 720)
        self.assertEqual(payload['shifts'][0]['endMin'], 1920)

    def test_day_without_shifts_is_rejected(self):
        self._refused('смен нет', [], [])

    def test_intact_day_is_accepted(self):
        shifts = [{'start': '09:00', 'end': '13:00'}, {'start': '15:00', 'end': '19:00'}]
        self._check(shifts, list(reversed(shifts)))

    def test_changed_day_is_rejected(self):
        """Между подачей и решением смену сократили или добавили часть —
        руководитель одобрял бы не то, что видит в заявке."""
        filed = [{'start': '09:00', 'end': '18:00'}]
        self._refused('Смена изменилась', filed, [{'start': '09:00', 'end': '15:00'}])
        self._refused('Смена изменилась', filed, filed + [{'start': '19:00', 'end': '21:00'}])
        self._refused('смен нет', filed, [])

    def test_started_day_is_rejected(self):
        shifts = [{'start': '09:00', 'end': '18:00'}]
        self._check(shifts, shifts, now=datetime(2026, 10, 14, 8, 59))
        self._refused('уже началась', shifts, shifts, now=datetime(2026, 10, 14, 9, 0))

    def test_later_piece_does_not_hide_a_started_one(self):
        """Утренняя часть уже отработана — выходной стёр бы и её."""
        shifts = [{'start': '09:00', 'end': '13:00'}, {'start': '15:00', 'end': '19:00'}]
        self._refused('уже началась', shifts, shifts, now=datetime(2026, 10, 14, 14, 0))


class PendingConflictTests(unittest.TestCase):
    """Какие ожидающие заявки на один день уживаются друг с другом."""

    def setUp(self):
        self.db = _Db()
        self.kinds = self.db.SHIFT_CHANGE_REQUEST_KINDS

    def test_nothing_pending_never_conflicts(self):
        for kind in self.kinds:
            self.assertFalse(self.db._shift_change_pending_conflict(kind, set()))

    def test_same_kind_twice_is_a_duplicate(self):
        for kind in self.kinds:
            self.assertTrue(self.db._shift_change_pending_conflict(kind, {kind}))

    def test_shorten_and_extra_coexist(self):
        """Прежнее поведение: сократить и добавить часть в один день можно."""
        self.assertFalse(self.db._shift_change_pending_conflict('shorten', {'extra'}))
        self.assertFalse(self.db._shift_change_pending_conflict('extra', {'shorten'}))

    def test_move_and_day_off_stand_alone(self):
        """Оба сверяют смену со снимком точно: чужое одобрение раньше своего
        оставило бы такую заявку висеть с ошибкой, а сокращение после переноса
        разорвало бы смену надвое."""
        for exclusive in self.db.SHIFT_CHANGE_EXCLUSIVE_KINDS:
            for other in self.kinds:
                self.assertTrue(self.db._shift_change_pending_conflict(exclusive, {other}))
                self.assertTrue(self.db._shift_change_pending_conflict(other, {exclusive}))

    def test_exclusive_kinds_are_real_kinds(self):
        self.assertTrue(set(self.db.SHIFT_CHANGE_EXCLUSIVE_KINDS) <= set(self.kinds))


class SerializerTests(unittest.TestCase):
    """«Было / станет» считает сервер — клиентов трое, и копий арифметики быть не должно."""

    def setUp(self):
        self.db = _Db()

    def _item(self, kind, payload):
        stamp = datetime(2026, 10, 12, 10, 0)
        return self.db._serialize_shift_change_request_row((
            1, 7, 'Оператор', 3, 'Отдел', 9, 'Супервайзер', kind, date(2026, 10, 14), payload,
            'pending', 'причина', None, None, None, None, None, stamp, stamp,
            'Линия', 5, 'Группа', 9, 'Супервайзер',
        ))

    def test_day_off_shows_what_is_taken_away(self):
        """Без своей ветки выходной читался как сокращение «0 → 0», и
        руководитель не видел цену решения."""
        payload = self.db._shift_change_day_off_payload([
            {'start': '09:00', 'end': '13:00'},
            {'start': '15:00', 'end': '19:00'},
        ])
        item = self._item('day_off', payload)
        self.assertEqual(
            (item['minutesBefore'], item['minutesAfter'], item['minutesDelta']), (480, 0, -480)
        )

    def test_change_delta_may_be_zero_or_of_any_sign(self):
        def delta(new_start, new_end):
            payload = self.db._normalize_shift_change_payload('change', {
                'segment': {'start': '09:00', 'end': '18:00'},
                'newStart': new_start,
                'newEnd': new_end,
            }, date(2026, 10, 14))
            return self._item('change', payload)['minutesDelta']

        self.assertEqual(delta('11:00', '20:00'), 0)
        self.assertEqual(delta('08:00', '19:00'), 120)
        self.assertEqual(delta('12:00', '16:00'), -300)

    def test_old_kinds_are_counted_as_before(self):
        shorten = self.db._normalize_shift_change_payload('shorten', {
            'segment': {'start': '09:00', 'end': '18:00'},
            'newStart': '09:00',
            'newEnd': '15:00',
        })
        self.assertEqual(self._item('shorten', shorten)['minutesDelta'], -180)
        extra = self.db._normalize_shift_change_payload('extra', {'start': '19:00', 'end': '22:00'})
        item = self._item('extra', extra)
        self.assertEqual((item['minutesBefore'], item['minutesAfter']), (0, 180))

    def test_every_kind_has_its_own_bell_label(self):
        """Вид без подписи остаётся в колоколе голой датой."""
        source = SOURCES_PATH.read_text(encoding="utf-8-sig")
        index = source.find("_SHIFT_REQUEST_KIND_LABELS = {")
        self.assertNotEqual(index, -1)
        labels = ast.literal_eval(source[source.index("{", index):source.index("}", index) + 1])
        self.assertEqual(set(labels), set(self.db.SHIFT_CHANGE_REQUEST_KINDS))
        self.assertTrue(all(str(text).strip() for text in labels.values()))


@lru_cache(maxsize=None)
def _module_assign_source(name):
    """Присваивание константы уровня модуля — вместе с её значением."""
    source = _database_source()
    module = source_cache.parse(source)
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.get_source_segment(source, node)
    raise AssertionError("в database.py нет константы %s" % name)


class _FakeCursor:
    """Курсор по сценарию: первая подошедшая подстрока запроса задаёт ответ."""

    def __init__(self, script=()):
        self.script = list(script)
        self.log = []
        self._rows = []

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.log.append((text, params))
        self._rows = []
        for needle, rows in self.script:
            if needle in text:
                self._rows = list(rows(params) if callable(rows) else rows)
                return

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def statements(self, needle):
        return [(text, params) for text, params in self.log if needle in text]


class _WriterDb(_Db):
    """Заглушка для путей с базой: подача заявки и запись её в график.

    Правила — настоящие, вырезанные из модуля. Подменено только то, что ходит
    в базу или пишет график: эти вызовы записываются, и тест сверяет, что
    именно и в каком порядке было позвано.
    """

    MODULE_FUNCTIONS = _Db.MODULE_FUNCTIONS + (
        '_normalize_work_shift_type_value',
        '_work_shift_type_priority',
    )
    MODULE_CONSTANTS = (
        'WORK_SHIFT_TYPE_REGULAR',
        'WORK_SHIFT_TYPE_OFFICE_PRACTICE',
        'WORK_SHIFT_TYPE_PHONE_SHIFT',
        'WORK_SHIFT_TYPE_ALLOWED',
        'WORK_SHIFT_TYPE_PRIORITY',
    )
    METHODS = tuple(name for name in _Db.METHODS if name != '_serialize_shift_change_request_row') + (
        '_load_shift_change_move_map_tx',
        '_apply_shift_change_request_tx',
        '_apply_shift_change_move_tx',
        '_apply_shift_change_day_off_tx',
        'create_shift_change_request',
    )
    SHIFT_CHANGE_REQUEST_MAX_AHEAD_DAYS = 45

    def __init__(self, day_map=None, cursor=None, now=None):
        import json
        from typing import Optional

        namespace = {
            'date': date,
            'datetime': datetime,
            'dt_time': dt_time,
            'timedelta': timedelta,
            'json': json,
            'Optional': Optional,
            # Роль на стенде уже нормализована.
            'normalize_role_value': lambda value: value,
        }
        for constant_name in self.MODULE_CONSTANTS:
            exec(_module_assign_source(constant_name), namespace)
        for function_name in self.MODULE_FUNCTIONS:
            exec(_module_function_source(function_name), namespace)
        for method_name in self.METHODS:
            exec(_method_source(method_name), namespace)
            setattr(self.__class__, method_name, namespace[method_name])
        self.namespace = namespace
        self.SHIFT_CHANGE_REQUEST_KINDS = _class_constant('SHIFT_CHANGE_REQUEST_KINDS')
        self.SHIFT_CHANGE_EXCLUSIVE_KINDS = _class_constant('SHIFT_CHANGE_EXCLUSIVE_KINDS')
        self.day_map = day_map or {}
        self.cursor = cursor or _FakeCursor()
        self.now = now or datetime(2026, 10, 12, 10, 0)
        self.calls = []

    # ── то, что ходит в базу ──────────────────────────────────────────────

    def _get_cursor(self):
        from contextlib import contextmanager

        @contextmanager
        def manager():
            yield self.cursor

        return manager()

    def _almaty_now(self):
        return self.now

    def _load_operator_shift_map_for_period_tx(self, cursor, operator_id, start_date_obj, end_date_obj):
        self.calls.append(('load_map', start_date_obj, end_date_obj))
        return {
            key: [dict(item) for item in value]
            for key, value in self.day_map.items()
            if start_date_obj.strftime('%Y-%m-%d') <= key <= end_date_obj.strftime('%Y-%m-%d')
        }

    def _load_shift_change_window_tx(self, cursor, operator_id, shift_date_obj):
        self.calls.append(('load_window', shift_date_obj))
        raise _WindowPathTaken()

    def _load_day_shift_breaks_tx(self, cursor, operator_id, shift_date):
        self.calls.append(('breaks_snapshot', shift_date))
        return ['снимок перерывов']

    def _schedule_change_actor(self, cursor, actor_id=None, source='supervisor', actor_name=None):
        return {'id': actor_id, 'source': source}

    def _snapshot_schedule_days_tx(self, cursor, keys):
        self.calls.append(('snapshot', tuple(keys)))
        return {'снимок': tuple(keys)}

    def _save_shift_tx(self, **kwargs):
        self.calls.append(('save_shift', kwargs))
        return 501

    def _set_day_off_tx(self, cursor, operator_id, day_off_date):
        self.calls.append(('set_day_off', operator_id, day_off_date))

    def _clear_day_schedule_tx(self, cursor, operator_id, target_date):
        self.calls.append(('clear_day', operator_id, target_date))

    def _record_schedule_day_changes_tx(self, cursor, before, actor):
        self.calls.append(('history', before, actor))

    def _recalculate_auto_daily_hours_tx(self, cursor, operator_ids, start_date, end_date):
        self.calls.append(('hours', tuple(operator_ids), start_date, end_date))

    def _get_operator_group_id_tx(self, cursor, operator_id, shift_date_obj):
        return 1

    def _group_active_supervisor_id_tx(self, cursor, group_id):
        return 2

    def _select_shift_change_request_by_id_tx(self, cursor, request_id):
        return request_id

    def _serialize_shift_change_request_row(self, row):
        return {'id': row}

    def named(self, name):
        return [call for call in self.calls if call[0] == name]


class _WindowPathTaken(Exception):
    """Заявка ушла в перезапись окна — путь сокращения и доп. части."""


class WriterStubCompletenessTests(unittest.TestCase):
    def test_writer_stub_is_complete(self):
        # У диспетчера за развилкой лежит прежний путь перезаписи окна со
        # своими помощниками; заглушка обрывает его на
        # _load_shift_change_window_tx, поэтому сам диспетчер не сверяем.
        methods = [name for name in _WriterDb.METHODS if name != '_apply_shift_change_request_tx']
        _assert_stub_is_complete(self, _WriterDb(), methods)


class DispatchTests(unittest.TestCase):
    """Какой дорогой одобренная заявка идёт в график."""

    def setUp(self):
        self.day = date(2026, 10, 14)

    def test_move_and_day_off_do_not_rewrite_the_window(self):
        """Перезапись окна склеила бы смены, соприкасающиеся в полночь, и
        пометила бы соседний день выходным."""
        for kind, writer in (
            ('change', '_apply_shift_change_move_tx'),
            ('day_off', '_apply_shift_change_day_off_tx'),
        ):
            db = _WriterDb()
            seen = []
            setattr(db, writer, lambda *args, _seen=seen: _seen.append(args) or ['готово'])
            result = db._apply_shift_change_request_tx(db.cursor, 7, self.day, kind, {'x': 1}, 9)
            self.assertEqual(result, ['готово'], kind)
            self.assertEqual(seen, [(db.cursor, 7, self.day, {'x': 1}, 9)], kind)
            self.assertEqual(db.named('load_window'), [], kind)

    def test_old_kinds_keep_the_window_path(self):
        for kind in ('shorten', 'extra'):
            db = _WriterDb()
            db._apply_shift_change_move_tx = lambda *args: self.fail('сокращение ушло в перенос')
            db._apply_shift_change_day_off_tx = lambda *args: self.fail('сокращение ушло в выходной')
            with self.assertRaises(_WindowPathTaken):
                db._apply_shift_change_request_tx(db.cursor, 7, self.day, kind, {}, 9)


class MoveWriterTests(unittest.TestCase):
    """Запись переноса в график: точечно, без перезаписи дня."""

    def setUp(self):
        self.day = date(2026, 10, 14)
        self.other = date(2026, 10, 17)
        self.day_map = {
            '2026-10-14': [{'start': '09:00', 'end': '13:00'}, {'start': '15:00', 'end': '19:00'}],
            '2026-10-17': [{'start': '10:00', 'end': '14:00'}],
        }
        self.rows = [
            (11, dt_time(9, 0), dt_time(13, 0), 'regular'),
            (12, dt_time(15, 0), dt_time(19, 0), 'office_practice'),
        ]

    def _db(self, rows=None, source_day_left=True, day_map=None, now=None):
        cursor = _FakeCursor([
            ('SELECT id, start_time, end_time, shift_type FROM work_shifts', self.rows if rows is None else rows),
            ('SELECT 1 FROM work_shifts', [(1,)] if source_day_left else []),
        ])
        return _WriterDb(day_map=self.day_map if day_map is None else day_map, cursor=cursor, now=now)

    def _payload(self, db, seg_start, seg_end, new_start, new_end, new_date=None):
        body = {'segment': {'start': seg_start, 'end': seg_end}, 'newStart': new_start, 'newEnd': new_end}
        if new_date:
            body['newDate'] = new_date
        return db._normalize_shift_change_payload('change', body, self.day)

    def test_only_the_moved_piece_is_deleted_and_lands_on_the_new_day(self):
        db = self._db()
        payload = self._payload(db, '15:00', '19:00', '15:00', '19:00', new_date='2026-10-17')
        affected = db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)

        self.assertEqual(affected, [self.day, self.other])
        deletes = db.cursor.statements('DELETE FROM work_shifts')
        self.assertEqual(len(deletes), 1)
        # Утренний кусок 09-13 (строка 11) остаётся как был — со своим видом и перерывами.
        self.assertEqual(deletes[0][1], ([12],))
        self.assertEqual(db.named('clear_day'), [])

        saves = db.named('save_shift')
        self.assertEqual(len(saves), 1)
        saved = saves[0][1]
        self.assertEqual(saved['shift_date'], self.other)
        self.assertEqual((saved['start_time'], saved['end_time']), (dt_time(15, 0), dt_time(19, 0)))
        # Вид смены едет вместе с ней.
        self.assertEqual(saved['shift_type'], 'office_practice')
        self.assertIsNone(saved['breaks'])
        self.assertEqual(saved['day_breaks_snapshot'], ['снимок перерывов'])
        self.assertEqual(db.named('breaks_snapshot'), [('breaks_snapshot', self.other)])

    def test_history_and_hours_cover_both_days(self):
        db = self._db()
        payload = self._payload(db, '15:00', '19:00', '15:00', '19:00', new_date='2026-10-17')
        db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)

        self.assertEqual(db.named('snapshot'), [('snapshot', ((7, self.day), (7, self.other)))])
        history = db.named('history')
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][1], {'снимок': ((7, self.day), (7, self.other))})
        self.assertEqual(history[0][2], {'id': 9, 'source': 'shift_request'})
        self.assertEqual(
            db.named('hours'),
            [('hours', (7,), self.day, self.day), ('hours', (7,), self.other, self.other)],
        )
        # Снимок истории снят до удаления, запись — после сохранения.
        order = [call[0] for call in db.calls if call[0] in ('snapshot', 'save_shift', 'history')]
        self.assertEqual(order, ['snapshot', 'save_shift', 'history'])

    def test_day_that_still_has_a_shift_is_not_marked_as_day_off(self):
        db = self._db(source_day_left=True)
        payload = self._payload(db, '15:00', '19:00', '15:00', '19:00', new_date='2026-10-17')
        db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.named('set_day_off'), [])

    def test_emptied_day_becomes_a_day_off(self):
        """Пустая ячейка читается как «график ещё не составили» (задача #37)."""
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '18:00'}]}
        db = self._db(rows=[(21, dt_time(9, 0), dt_time(18, 0), 'regular')],
                      source_day_left=False, day_map=day_map)
        payload = self._payload(db, '09:00', '18:00', '09:00', '18:00', new_date='2026-10-17')
        db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.named('set_day_off'), [('set_day_off', 7, self.day)])

    def test_move_inside_the_day_never_sets_a_day_off(self):
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '18:00'}]}
        db = self._db(rows=[(21, dt_time(9, 0), dt_time(18, 0), 'regular')],
                      source_day_left=False, day_map=day_map)
        payload = self._payload(db, '09:00', '18:00', '11:00', '20:00')
        affected = db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(affected, [self.day])
        self.assertEqual(db.named('set_day_off'), [])
        self.assertEqual(db.cursor.statements('SELECT 1 FROM work_shifts'), [])
        self.assertEqual(db.named('save_shift')[0][1]['shift_date'], self.day)
        self.assertEqual(db.named('hours'), [('hours', (7,), self.day, self.day)])

    def test_piece_merged_from_two_rows_moves_whole(self):
        """Оператор видит 09-18 одной сменой, а в базе это две строки встык."""
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '13:00'}, {'start': '13:00', 'end': '18:00'}]}
        rows = [
            (31, dt_time(9, 0), dt_time(13, 0), 'regular'),
            (32, dt_time(13, 0), dt_time(18, 0), 'phone_shift'),
        ]
        db = self._db(rows=rows, source_day_left=False, day_map=day_map)
        payload = self._payload(db, '09:00', '18:00', '10:00', '19:00')
        db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.cursor.statements('DELETE FROM work_shifts')[0][1], ([31, 32],))
        # Как при слиянии для показа — самый специфичный вид.
        self.assertEqual(db.named('save_shift')[0][1]['shift_type'], 'phone_shift')

    def test_stale_request_writes_nothing(self):
        """Между подачей и решением смену поправили — график не трогаем."""
        day_map = {'2026-10-14': [{'start': '09:00', 'end': '13:00'}, {'start': '15:00', 'end': '20:00'}]}
        db = self._db(day_map=day_map)
        payload = self._payload(db, '15:00', '19:00', '15:00', '19:00', new_date='2026-10-17')
        with self.assertRaisesRegex(ValueError, 'Смена изменилась'):
            db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.cursor.statements('DELETE FROM work_shifts'), [])
        self.assertEqual(db.named('save_shift') + db.named('history') + db.named('set_day_off'), [])

    def test_started_shift_is_not_moved_on_late_approval(self):
        db = self._db(now=datetime(2026, 10, 14, 16, 0))
        payload = self._payload(db, '15:00', '19:00', '15:00', '19:00', new_date='2026-10-17')
        with self.assertRaisesRegex(ValueError, 'уже началась'):
            db._apply_shift_change_move_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.cursor.statements('DELETE FROM work_shifts'), [])


class DayOffWriterTests(unittest.TestCase):
    """Запись выходного: только день заявки, с историей и пересчётом часов."""

    def setUp(self):
        self.day = date(2026, 10, 14)
        self.day_map = {'2026-10-14': [{'start': '09:00', 'end': '13:00'}, {'start': '15:00', 'end': '19:00'}]}

    def test_day_off_is_set_with_history_and_hours(self):
        db = _WriterDb(day_map=self.day_map)
        payload = db._shift_change_day_off_payload(self.day_map['2026-10-14'])
        affected = db._apply_shift_change_day_off_tx(db.cursor, 7, self.day, payload, 9)

        self.assertEqual(affected, [self.day])
        self.assertEqual(db.named('load_map'), [('load_map', self.day, self.day)])
        self.assertEqual(db.named('set_day_off'), [('set_day_off', 7, self.day)])
        # Очистка дня сняла бы и отметку выходного.
        self.assertEqual(db.named('clear_day'), [])
        self.assertEqual(db.named('snapshot'), [('snapshot', ((7, self.day),))])
        history = db.named('history')
        self.assertEqual(len(history), 1)
        # В истории графика видно, что смену снял не руководитель по своей воле.
        self.assertEqual(history[0][2], {'id': 9, 'source': 'shift_request'})
        self.assertEqual(db.named('hours'), [('hours', (7,), self.day, self.day)])
        order = [call[0] for call in db.calls if call[0] in ('snapshot', 'set_day_off', 'history', 'hours')]
        self.assertEqual(order, ['snapshot', 'set_day_off', 'history', 'hours'])

    def test_changed_day_is_left_alone(self):
        db = _WriterDb(day_map={'2026-10-14': [{'start': '09:00', 'end': '13:00'}]})
        payload = db._shift_change_day_off_payload(self.day_map['2026-10-14'])
        with self.assertRaisesRegex(ValueError, 'Смена изменилась'):
            db._apply_shift_change_day_off_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.named('set_day_off') + db.named('history') + db.named('hours'), [])

    def test_late_approval_does_not_erase_a_worked_day(self):
        """Одобрение через сутки стёрло бы отработанную смену вместе с часами."""
        db = _WriterDb(day_map=self.day_map, now=datetime(2026, 10, 15, 9, 0))
        payload = db._shift_change_day_off_payload(self.day_map['2026-10-14'])
        with self.assertRaisesRegex(ValueError, 'уже началась'):
            db._apply_shift_change_day_off_tx(db.cursor, 7, self.day, payload, 9)
        self.assertEqual(db.named('set_day_off'), [])


class FilingTests(unittest.TestCase):
    """Подача заявки новых видов: что проверяется и что уходит в базу."""

    def setUp(self):
        self.day = '2026-10-14'
        self.day_map = {
            '2026-10-14': [{'start': '09:00', 'end': '13:00'}, {'start': '15:00', 'end': '19:00'}],
            '2026-10-16': [{'start': '10:00', 'end': '14:00'}],
        }

    def _db(self, pending=(), day_map=None, now=None):
        cursor = _FakeCursor([
            ('SELECT id, role FROM users', [(7, 'operator')]),
            ('SELECT request_kind FROM work_shift_change_requests', [(kind,) for kind in pending]),
            ('SELECT department_id FROM users', [(3,)]),
            ('INSERT INTO work_shift_change_requests', [(77,)]),
        ])
        return _WriterDb(day_map=self.day_map if day_map is None else day_map, cursor=cursor, now=now)

    def _inserted(self, db):
        import json

        inserts = db.cursor.statements('INSERT INTO work_shift_change_requests')
        self.assertEqual(len(inserts), 1)
        params = inserts[0][1]
        return {'kind': params[3], 'shift_date': params[4], 'payload': json.loads(params[5]), 'comment': params[6]}

    def _move(self, new_date=None, new_start='15:00', new_end='19:00'):
        body = {'segment': {'start': '15:00', 'end': '19:00'}, 'newStart': new_start, 'newEnd': new_end}
        if new_date:
            body['newDate'] = new_date
        return body

    def test_day_off_snapshot_comes_from_the_schedule(self):
        db = self._db()
        result = db.create_shift_change_request(
            7, 'day_off', self.day, {'shifts': [{'start': '00:00', 'end': '00:30'}], 'totalMin': 1},
            request_comment='  к врачу  ',
        )
        self.assertEqual(result, {'id': 77})
        stored = self._inserted(db)
        self.assertEqual(stored['kind'], 'day_off')
        self.assertEqual(stored['shift_date'], date(2026, 10, 14))
        self.assertEqual(stored['comment'], 'к врачу')
        self.assertEqual(
            [(item['start'], item['end']) for item in stored['payload']['shifts']],
            [('09:00', '13:00'), ('15:00', '19:00')],
        )
        self.assertEqual(stored['payload']['totalMin'], 480)
        # День берётся по своим строкам, а не по окну из трёх суток.
        self.assertEqual(db.named('load_map'), [('load_map', date(2026, 10, 14), date(2026, 10, 14))])
        self.assertEqual(db.named('load_window'), [])

    def test_day_off_needs_a_shift_that_day(self):
        """Смены вчера и завтра выходной на пустой день не оправдывают."""
        day_map = {'2026-10-13': [{'start': '20:00', 'end': '08:00'}], '2026-10-15': [{'start': '09:00', 'end': '18:00'}]}
        db = self._db(day_map=day_map)
        with self.assertRaisesRegex(ValueError, 'смен нет'):
            db.create_shift_change_request(7, 'day_off', self.day, None)
        self.assertEqual(db.cursor.statements('INSERT INTO'), [])

    def test_move_is_stored_with_its_new_day(self):
        db = self._db()
        db.create_shift_change_request(7, 'change', self.day, self._move(new_date='2026-10-17'))
        stored = self._inserted(db)
        self.assertEqual(stored['kind'], 'change')
        self.assertEqual(stored['payload']['newDate'], '2026-10-17')
        self.assertEqual(stored['payload']['dayShift'], 3)
        # Сверка видит день заявки, новый день и по соседу с каждой стороны.
        self.assertEqual(db.named('load_map'), [('load_map', date(2026, 10, 13), date(2026, 10, 18))])

    def test_move_is_checked_against_the_schedule_when_filed(self):
        db = self._db()
        with self.assertRaisesRegex(ValueError, 'уже есть смена'):
            db.create_shift_change_request(
                7, 'change', self.day, self._move(new_date='2026-10-16', new_start='12:00', new_end='16:00')
            )
        self.assertEqual(db.cursor.statements('INSERT INTO'), [])

    def test_move_target_day_lives_in_the_same_window(self):
        db = self._db()
        with self.assertRaisesRegex(ValueError, 'прошедший день'):
            db.create_shift_change_request(7, 'change', self.day, self._move(new_date='2026-10-11'))
        with self.assertRaisesRegex(ValueError, 'слишком далеко'):
            db.create_shift_change_request(7, 'change', self.day, self._move(new_date='2026-11-27'))
        # Последний день окна — ещё можно.
        db.create_shift_change_request(7, 'change', self.day, self._move(new_date='2026-11-26'))
        self.assertEqual(len(db.cursor.statements('INSERT INTO')), 1)

    def test_pending_requests_of_the_day_reach_the_conflict_rule(self):
        for pending, kind, payload in (
            (('shorten',), 'day_off', None),
            (('extra',), 'change', self._move(new_date='2026-10-17')),
            (('change',), 'day_off', None),
            (('day_off',), 'day_off', None),
        ):
            db = self._db(pending=pending)
            with self.assertRaisesRegex(ValueError, 'уже есть заявка'):
                db.create_shift_change_request(7, kind, self.day, payload)
            self.assertEqual(db.cursor.statements('INSERT INTO'), [], (pending, kind))
        # Запрос ожидающих — по оператору и дню, без отбора по виду.
        db = self._db()
        db.create_shift_change_request(7, 'day_off', self.day, None)
        text, params = db.cursor.statements('SELECT request_kind FROM work_shift_change_requests')[0]
        self.assertIn("status = 'pending'", text)
        self.assertNotIn('request_kind = %s', text)
        self.assertEqual(params, (7, date(2026, 10, 14)))

    def test_old_kinds_still_go_through_the_window(self):
        db = self._db()
        with self.assertRaises(_WindowPathTaken):
            db.create_shift_change_request(7, 'extra', self.day, {'start': '20:00', 'end': '22:00'})
        # Ожидающий перенос на этот день не даёт подать и прежний вид.
        db = self._db(pending=('change',))
        with self.assertRaisesRegex(ValueError, 'уже есть заявка'):
            db.create_shift_change_request(7, 'extra', self.day, {'start': '20:00', 'end': '22:00'})
        self.assertEqual(db.named('load_window'), [])

    def test_started_shift_is_refused_when_filed(self):
        db = self._db(now=datetime(2026, 10, 14, 9, 30))
        with self.assertRaisesRegex(ValueError, 'уже началась'):
            db.create_shift_change_request(7, 'day_off', self.day, None)
        db = self._db(now=datetime(2026, 10, 14, 15, 0))
        with self.assertRaisesRegex(ValueError, 'уже началась'):
            db.create_shift_change_request(7, 'change', self.day, self._move(new_date='2026-10-17'))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.source = _database_source()

    def test_table_and_indexes_are_idempotent(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS work_shift_change_requests", self.source)
        for index in (
            'idx_shift_change_requests_operator',
            'idx_shift_change_requests_status_date',
            'idx_shift_change_requests_supervisor_status',
        ):
            self.assertIn("CREATE INDEX IF NOT EXISTS %s" % index, self.source)

    def test_status_and_kind_are_constrained(self):
        self.assertIn("work_shift_change_requests_kind_check", self.source)
        self.assertIn("work_shift_change_requests_status_check", self.source)
        kinds = _class_constant('SHIFT_CHANGE_REQUEST_KINDS')
        check = "CHECK (request_kind IN (%s))" % ", ".join("'%s'" % kind for kind in kinds)
        # Список зашит дважды — в CREATE TABLE для свежей базы и в замене
        # ограничения для живой. Поменяй одно без другого, и стенд с продом
        # разойдутся.
        self.assertEqual(self.source.count(check), 2)

    def test_kind_constraint_is_replaced_only_when_stale(self):
        """DROP + ADD на каждом старте держал бы блокировку таблицы до конца
        всей инициализации, а её читает колокол каждого пользователя."""
        index = self.source.find("ADD CONSTRAINT work_shift_change_requests_kind_check")
        self.assertNotEqual(index, -1)
        block = self.source[self.source.rfind("DO $$", 0, index):index]
        newest_kind = _class_constant('SHIFT_CHANGE_REQUEST_KINDS')[-1]
        self.assertIn(
            "position('''%s''' IN pg_get_constraintdef(oid)) = 0" % newest_kind, block
        )
        self.assertIn("DROP CONSTRAINT work_shift_change_requests_kind_check", block)
        self.assertIn("IF NOT EXISTS", block)

    def test_new_kinds_have_their_own_writers(self):
        """Перезапись окна склеила бы смены, соприкасающиеся в полночь, и
        пометила бы соседний день выходным — перенос и выходной пишутся сами."""
        dispatcher = _method_source('_apply_shift_change_request_tx')
        self.assertIn("_apply_shift_change_move_tx", dispatcher)
        self.assertIn("_apply_shift_change_day_off_tx", dispatcher)

        move = _method_source('_apply_shift_change_move_tx')
        self.assertIn("_validate_shift_change_move", move)
        self.assertIn("_save_shift_tx", move)
        self.assertIn("_set_day_off_tx", move)
        self.assertIn("_record_schedule_day_changes_tx", move)
        self.assertIn("_recalculate_auto_daily_hours_tx", move)
        # Вид переносимой смены едет вместе с ней.
        self.assertIn("shift_type=piece_type", move)
        self.assertNotIn("_clear_day_schedule_tx", move)

        day_off = _method_source('_apply_shift_change_day_off_tx')
        self.assertIn("_validate_shift_change_day_off", day_off)
        self.assertIn("_set_day_off_tx", day_off)
        self.assertIn("_record_schedule_day_changes_tx", day_off)
        self.assertIn("_recalculate_auto_daily_hours_tx", day_off)

    def test_filing_checks_new_kinds_against_the_schedule(self):
        method = _method_source('create_shift_change_request')
        self.assertIn("_shift_change_pending_conflict", method)
        self.assertIn("_validate_shift_change_move", method)
        self.assertIn("_validate_shift_change_day_off", method)
        # Снимок смен для выходного — из графика, не из тела запроса.
        self.assertIn("_shift_change_day_off_payload", method)

    def test_change_source_is_registered(self):
        """Незарегистрированный источник молча становится 'system', и в
        истории графика причина правки теряется."""
        self.assertIn("'shift_request',", self.source)

    def test_approval_locks_the_row(self):
        """Без FOR UPDATE два руководителя одобрили бы одну заявку дважды."""
        method = _method_source('respond_shift_change_request')
        self.assertIn("FOR UPDATE", method)
        self.assertIn("Заявка уже обработана", method)

    def test_writer_goes_through_save_shift_tx(self):
        """Свой INSERT в work_shifts не почистил бы пересечения и перерывы."""
        method = _method_source('_apply_shift_change_request_tx')
        self.assertIn("_save_shift_tx", method)
        self.assertIn("_clear_day_schedule_tx", method)
        self.assertIn("_record_schedule_day_changes_tx", method)
        self.assertIn("_recalculate_auto_daily_hours_tx", method)
        # День, опустевший после сокращения, обязан стать выходным, а не
        # пустой ячейкой — иначе это читается как «график не составлен» (#37).
        self.assertIn("_set_day_off_tx", method)

    def test_approval_revalidates_against_the_schedule(self):
        method = _method_source('_apply_shift_change_request_tx')
        self.assertIn("_validate_shift_change_against_schedule", method)


class RouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = BOT_PATH.read_text(encoding="utf-8-sig")

    def _route_body(self, function_name, size=4000):
        index = self.source.find("def %s(" % function_name)
        self.assertNotEqual(index, -1, "роут %s исчез" % function_name)
        return self.source[index:index + size]

    def test_operator_routes_are_operator_only(self):
        body = self._route_body('shift_change_requests')
        self.assertIn("_work_schedule_operator_requester()", body)

    def test_operator_route_is_open_to_front_office(self):
        """Обмены фронт-офису закрыты, а свои заявки — нет: тут оператор не
        видит ничьих смен, кроме собственных."""
        body = self._route_body('shift_change_requests')
        self.assertNotIn("_operator_colleague_schedules_hidden", body)

    def test_review_queue_uses_viewer_gate_and_scope(self):
        body = self._route_body('review_shift_change_requests')
        self.assertIn("_resolve_work_schedule_viewer()", body)
        self.assertIn("_filter_operators_for_requester_scope(", body)

    def test_decision_requires_management_and_scope(self):
        """Без периметра по одному id можно было бы согласовать смену в
        чужом отделе."""
        body = self._route_body('respond_shift_change_request')
        self.assertIn("_resolve_management_requester()", body)
        self.assertIn("_filter_operators_for_requester_scope(", body)

    def test_cancel_is_available_to_the_author(self):
        body = self._route_body('respond_shift_change_request')
        self.assertIn("_work_schedule_operator_requester()", body)

    def _notify_scope_body(self):
        index = self.source.find("def _shift_change_scope_for_requester(")
        self.assertNotEqual(index, -1)
        return self.source[index:index + 2600]

    def test_notify_scope_excludes_trainer(self):
        """Тренер видит графики, но решать не может — уведомлять его о том,
        чего он не сделает, значит слать шум."""
        body = self._notify_scope_body()
        self.assertIn("'trainer'", body)
        self.assertIn("'none'", body)

    def test_notify_scope_is_not_the_visibility_perimeter(self):
        """Уведомление адресное, а не «всем, кто видит раздел».

        Первая версия зеркалила периметр видимости, и каждый глобальный админ
        получал уведомление о КАЖДОЙ заявке в компании, а супервайзер — о
        заявках всего отдела, включая чужих операторов.
        """
        body = self._notify_scope_body()
        self.assertNotIn("_is_global_admin_requester", body)
        self.assertNotIn("'scope': 'all'", body)
        self.assertNotIn("_department_scope_id_for_requester", body)

    def test_notify_scope_targets_direct_supervisor(self):
        body = self._notify_scope_body()
        self.assertIn("descriptor['supervisor_id'] = int(requester_id)", body)

    def test_head_notifications_are_opt_in(self):
        body = self._notify_scope_body()
        self.assertIn("_headed_department_ids", body)
        self.assertIn("get_shift_change_notify_optin", body)

    def test_only_department_head_can_subscribe(self):
        """У прямого СВ выключателя нет: заявку его человека нельзя
        пропустить по своему желанию."""
        index = self.source.find("def _shift_change_can_watch_department(")
        self.assertNotEqual(index, -1)
        body = self.source[index:index + 700]
        self.assertIn("_headed_department_ids", body)
        self.assertIn("'trainer'", body)

    def test_watch_route_is_guarded(self):
        body = self._route_body('set_shift_change_watch')
        self.assertIn("_resolve_work_schedule_viewer()", body)
        self.assertIn("_shift_change_can_watch_department(", body)
        self.assertIn("403", body)


class NotificationSourceTests(unittest.TestCase):
    def test_source_is_registered_on_both_sides(self):
        source = SOURCES_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("'shift_requests'", source)
        self.assertIn("def shift_requests(cursor, viewer, limit):", source)
        self.assertIn("'shift_requests': shift_requests,", source)

    def test_source_is_two_sided(self):
        source = SOURCES_PATH.read_text(encoding="utf-8-sig")
        index = source.find("def shift_requests(")
        body = source[index:index + 5000]
        # Оператору — только РЕШЕНИЕ и только непросмотренное.
        self.assertIn("operator_seen_at IS NULL", body)
        self.assertIn("r.status IN ('approved', 'rejected')", body)
        # Руководителю — только ОЖИДАЮЩИЕ и не свои.
        self.assertIn("r.status = 'pending'", body)
        self.assertIn("r.operator_id <> %(user_id)s", body)

    def test_side_is_chosen_by_role_not_by_scope_shape(self):
        """Пустая граница руководителя не должна ронять его на операторскую
        ветку — иначе он перестал бы видеть заявки вовсе."""
        source = SOURCES_PATH.read_text(encoding="utf-8-sig")
        index = source.find("def shift_requests(")
        body = source[index:index + 2000]
        self.assertIn("viewer.get('role')", body)
        self.assertIn("== 'operator'", body)

    def test_scope_clause_targets_supervisor_and_opted_in_head(self):
        source = SOURCES_PATH.read_text(encoding="utf-8-sig")
        index = source.find("def _shift_request_scope_clause(")
        self.assertNotEqual(index, -1)
        body = source[index:index + 2000]
        self.assertIn("r.supervisor_id = %(scr_supervisor)s", body)
        self.assertIn("op.department_id = ANY(%(scr_departments)s)", body)
        # Без подписки главе отдела достаются только «беспризорные» заявки.
        self.assertIn("r.supervisor_id IS NULL", body)
        self.assertIn("head_all", body)
        # Границы «видно всё» у уведомлений больше нет.
        self.assertNotIn("if scope == 'all'", body)

    def test_trigger_targets_are_narrow(self):
        """Триггер колокола будит того же, кого и сводка: автора, прямого СВ и
        подписанного главу отдела. Админов в списке быть не должно."""
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        index = source.find("ELSIF TG_TABLE_NAME = 'work_shift_change_requests'")
        self.assertNotEqual(index, -1)
        body = source[index:index + 2200]
        self.assertIn("NEW.operator_id", body)
        self.assertIn("NEW.supervisor_id", body)
        self.assertIn("shift_change_notify_optin", body)
        self.assertIn("d.head_user_id", body)
        # Заявка без СВ обязана дойти до главы отдела и без подписки.
        self.assertIn("NEW.supervisor_id IS NULL", body)

    def test_optin_table_is_idempotent(self):
        source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("CREATE TABLE IF NOT EXISTS shift_change_notify_optin", source)

    def test_unsubscribe_deletes_the_row(self):
        """«Не подписан» — одно состояние, и двух его записей быть не должно."""
        method = _method_source('set_shift_change_notify_optin')
        self.assertIn("DELETE FROM shift_change_notify_optin", method)

    def test_viewer_context_carries_the_scope(self):
        source = BOT_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("'shift_requests': _shift_change_scope_for_requester(", source)

    def test_bell_has_a_label(self):
        """Источник без подписи во фронте рисуется безымянной строкой."""
        bell = BELL_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("shift_requests:", bell)

    def test_source_is_not_clearable(self):
        """Очередь согласования нельзя гасить просмотром: она снимается
        решением. Кнопка «очистить» на ней врала бы."""
        bell = BELL_PATH.read_text(encoding="utf-8-sig")
        index = bell.find("const CLEARABLE")
        self.assertNotEqual(index, -1)
        self.assertNotIn("shift_requests", bell[index:index + 200])
        sources = SOURCES_PATH.read_text(encoding="utf-8-sig")
        mark_seen_index = sources.find("def mark_seen(")
        self.assertNotIn("shift_requests", sources[mark_seen_index:])


class ScopeClauseBehaviourTests(unittest.TestCase):
    """Не текст условия, а его поведение: кого оно впустит, а кого нет.

    Проверка строкой поймала бы переименование, но не смену смысла — а именно
    смысл тут и ломался: границей уведомления был периметр видимости.
    """

    def setUp(self):
        from notifications.sources import _shift_request_scope_clause
        self.clause = _shift_request_scope_clause

    def _build(self, descriptor):
        params = {}
        return self.clause(descriptor, params), params

    def test_unknown_descriptor_lets_nobody_through(self):
        for descriptor in (None, {}, {'scope': 'all'}, {'scope': 'departments'}):
            with self.subTest(descriptor=descriptor):
                sql, _ = self._build(descriptor)
                self.assertEqual(sql.strip(), 'AND FALSE')

    def test_plain_supervisor_gets_only_his_own_people(self):
        sql, params = self._build({'scope': 'targets', 'supervisor_id': 7,
                                   'department_ids': [], 'head_all': False})
        self.assertIn('r.supervisor_id = %(scr_supervisor)s', sql)
        self.assertEqual(params['scr_supervisor'], 7)
        # Никакого отдела: СВ не будят из-за чужих операторов.
        self.assertNotIn('department_id', sql)
        self.assertNotIn('scr_departments', params)

    def test_head_without_subscription_gets_only_orphan_requests(self):
        sql, params = self._build({'scope': 'targets', 'supervisor_id': 7,
                                   'department_ids': [3], 'head_all': False})
        self.assertIn('r.supervisor_id IS NULL', sql)
        self.assertEqual(params['scr_departments'], [3])

    def test_head_with_subscription_gets_the_whole_department(self):
        sql, params = self._build({'scope': 'targets', 'supervisor_id': 7,
                                   'department_ids': [3, 4], 'head_all': True})
        self.assertIn('op.department_id = ANY(%(scr_departments)s)', sql)
        self.assertNotIn('r.supervisor_id IS NULL', sql)
        self.assertEqual(params['scr_departments'], [3, 4])

    def test_conditions_are_joined_by_or(self):
        """Глава отдела, который ещё и чей-то прямой СВ, обязан получать оба
        потока, а не выбирать один."""
        sql, _ = self._build({'scope': 'targets', 'supervisor_id': 7,
                              'department_ids': [3], 'head_all': True})
        self.assertIn(' OR ', sql)

    def test_empty_targets_let_nobody_through(self):
        sql, _ = self._build({'scope': 'targets', 'supervisor_id': None,
                              'department_ids': [], 'head_all': True})
        self.assertEqual(sql.strip(), 'AND FALSE')


class FrontendWiringTests(unittest.TestCase):
    def setUp(self):
        self.source = APP_PATH.read_text(encoding="utf-8-sig")

    def test_requests_tab_survives_the_colleague_shifts_gate(self):
        """Фронт-офисам закрыты «Замены» и «Смены коллег», но не свои заявки."""
        index = self.source.find("if (operatorColleagueShiftsHidden")
        self.assertNotEqual(index, -1)
        self.assertIn("operatorSelfTab !== 'requests'", self.source[index:index + 400])

    def test_review_button_is_outside_the_day_mode_gate(self):
        """Счётчик неразобранных заявок не должен исчезать в «Неделя»/«Месяц».

        Кнопка стоит сразу после «Агрегировать день», как и просили, но её
        рендер не завёрнут в проверку режима: между ними закрывается фрагмент
        режима «День».
        """
        aggregate = self.source.find("'Агрегация...' : 'Агрегировать день'")
        self.assertNotEqual(aggregate, -1, "кнопка «Агрегировать день» исчезла")
        button = self.source.find('title="Заявки операторов', aggregate)
        self.assertNotEqual(button, -1, "кнопка «Запросы» стоит не после агрегации")
        self.assertIn("</>", self.source[aggregate:button])

    def test_planner_receives_the_focus_prop_everywhere(self):
        """Пункт раздела объявлен в трёх ветках сайдбара — проп нужен во всех,
        иначе переход из колокола работает только части ролей."""
        self.assertEqual(self.source.count("shiftRequestFocus={shiftRequestFocus}"), 3)

    def test_bell_click_opens_the_section(self):
        self.assertIn("nextView === 'work_schedules' && Number(target)", self.source)


def _js_block(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


class FourKindsFrontendTests(unittest.TestCase):
    """Форма, списки и очередь знают все виды, которые принимает сервер (#349).

    С двумя видами фронт жил на развилке «shorten, иначе extra». Новый вид на
    такой развилке не падает — он молча превращается в чужой: «Изменить»
    упиралось бы в «новые границы должны быть внутри смены», а «Выходной»
    показывал бы поля времени и рисовался в списках прочерками.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = APP_PATH.read_text(encoding="utf-8-sig")
        cls.phone = (ROOT / "src" / "components" / "schedule" / "MyShiftsMobile.jsx").read_text(encoding="utf-8")
        cls.comment = (ROOT / "src" / "components" / "schedule" / "ShiftRequestComment.jsx").read_text(encoding="utf-8")
        cls.kinds = set(_class_constant('SHIFT_CHANGE_REQUEST_KINDS'))

    def test_selector_offers_every_server_kind(self):
        import re

        options = _js_block(self.app, "const SHIFT_CHANGE_KIND_OPTIONS = [", "];")
        self.assertEqual(set(re.findall(r"value: '([a-z_]+)'", options)), self.kinds)
        # Выбор — список проекта, а не сегменты: четыре подписи в полосу на
        # телефоне не помещаются. Якорь aria-подписи держит и тест формы.
        form = _js_block(self.app, "const shiftChangeModal = (", "</IosModal>")
        self.assertIn("options={SHIFT_CHANGE_KIND_OPTIONS}", form)
        self.assertIn('ariaLabel="Что нужно изменить"', form)
        self.assertNotIn("<IosSegmented", form)

    def test_every_kind_has_a_title_a_tile_and_a_hint_line(self):
        import re

        labels = _js_block(self.app, "const SHIFT_CHANGE_KIND_LABELS = {", "};")
        self.assertEqual(set(re.findall(r"^\s*([a-z_]+):", labels, re.M)), self.kinds)
        tiles = _js_block(self.app, "const SHIFT_CHANGE_PHONE_TILES = {", "};")
        self.assertEqual(set(re.findall(r"^\s*([a-z_]+): \{", tiles, re.M)), self.kinds)
        # Значок плитки обязан существовать в PHONE_ICONS — иначе на телефоне
        # карточка заявки упала бы на undefined вместо компонента.
        icons = _js_block(self.phone, "export const PHONE_ICONS = {", "};")
        for icon in re.findall(r"icon: '([A-Za-z]+)'", tiles):
            self.assertRegex(icons, r"\b%s:" % icon)
        hints = _js_block(self.app, "const SHIFT_CHANGE_KIND_HINTS = [", "];")
        self.assertEqual(len(re.findall(r"^\s*'", hints, re.M)), len(self.kinds))
        # Словарь подписей один: вторая копия внутри компонента разошлась бы.
        self.assertEqual(self.app.count("const SHIFT_CHANGE_KIND_LABELS = {"), 1)

    def test_form_logic_is_not_a_two_way_fork(self):
        logic = _js_block(self.app, "const openShiftChangeModal = useCallback(", "const cancelShiftChangeRequest = useCallback(")
        self.assertNotIn("kind === 'extra' ? 'extra' : 'shorten'", logic)
        self.assertIn("SHIFT_CHANGE_KIND_VALUES.includes(kind) ? kind : 'shorten'", logic)
        # У каждого нового вида — своя ветка проверки формы и своё тело запроса.
        problem = _js_block(logic, "const shiftChangeFormProblem = useMemo(", "const submitShiftChangeRequest = useCallback(")
        self.assertIn("form.kind === 'day_off'", problem)
        self.assertIn("form.kind === 'change'", problem)
        submit = logic[logic.index("const submitShiftChangeRequest = useCallback("):]
        self.assertIn("payload.newDate = shiftChangeTargetDate", submit)
        self.assertIn("form.kind === 'day_off'", submit)

    def test_form_checks_what_the_server_checks(self):
        """Отказ, который можно показать до отправки, не должен приходить после неё."""
        problem = _js_block(self.app, "const shiftChangeFormProblem = useMemo(", "const submitShiftChangeRequest = useCallback(")
        for message in (
            'Смена уже началась — выходной на этот день взять нельзя',
            'Смена уже началась — перенести её нельзя',
            'Новое время смены уже прошло',
            'В это время у вас уже есть смена',
            'Смена не изменилась',
        ):
            self.assertIn(message, problem)
        database = _database_source()
        for message in (
            'Смена уже началась — выходной на этот день взять нельзя',
            'Смена уже началась — перенести её нельзя',
            'Новое время смены уже прошло',
            'Смена не изменилась',
        ):
            self.assertIn(message, database)

    def test_lists_and_form_use_the_tested_arithmetic(self):
        """Подписи заявок и расчёты формы живут в shiftChangeForm.js и покрыты
        его тестами (tests/shift_change_form.test.mjs). Здесь — что экраны
        пользуются именно ими, а не своей копией."""
        self.assertIn("(item) => requestIntervalText(item, formatDateRuDayMonth)", self.app)
        logic = _js_block(self.app, "const openShiftChangeModal = useCallback(", "const cancelShiftChangeRequest = useCallback(")
        for call in ("occupiedAroundDay(", "overlapsAny(busy, startMin, endMin)", "moveTimeline({", "momentPassed(dayKey, minutes, nowKey"):
            self.assertIn(call, logic)
        module = (ROOT / "src" / "components" / "schedule" / "shiftChangeForm.js").read_text(encoding="utf-8")
        text = _js_block(module, "export function requestIntervalText(", "\n}\n")
        self.assertIn("item?.kind === 'day_off'", text)
        self.assertIn("payload.shifts", text)
        # Перенос в другой день называет новую дату.
        self.assertIn("payload.newDate !== item?.shiftDate", text)

    def test_operator_comment_is_a_quote_everywhere(self):
        """Причина была самой бледной строкой карточки, и её пропускали."""
        self.assertEqual(self.app.count('<ShiftRequestComment text={item.requestComment}'), 2)
        self.assertIn('comment={item.requestComment}', self.app)
        self.assertNotIn('<div className="mt-1 text-[12px] leading-snug text-slate-500">\n'
                         '                                                                    {item.requestComment}', self.app)
        card = _js_block(self.phone, 'export const MyShiftsRequestCard', 'export const MyShiftsCandidateRow')
        self.assertIn('<ShiftRequestComment text={comment}', card)
        # Блок не режет текст и не зависит от правил оболочки телефона для
        # flex-блоков и произвольных сеток (mobile-shell.css).
        markup = self.comment[self.comment.index('return ('):]
        for trap in ('truncate', 'line-clamp', ' flex ', 'grid-cols-['):
            self.assertNotIn(trap, markup)
        self.assertIn('whitespace-pre-line', markup)
        self.assertIn('break-words', markup)

    def test_reason_field_is_not_the_last_line_under_the_timeline(self):
        form = _js_block(self.app, "const shiftChangeModal = (", "</IosModal>")
        self.assertLess(form.index('aria-label="Причина заявки"'), form.index('<ShiftChangeTimeline'))
        self.assertNotIn('Комментарий · необязательно', form)

    def test_texts_do_not_promise_only_two_kinds(self):
        for stale in (
            'Заявки операторов на сокращение смены и дополнительную часть',
            'Заявки на сокращение и доп. часть',
            'Сократить или добавить часть смены"',
            "request_kind must be 'shorten' or 'extra'",
        ):
            self.assertNotIn(stale, self.app)
            self.assertNotIn(stale, _database_source())


if __name__ == "__main__":
    unittest.main()
