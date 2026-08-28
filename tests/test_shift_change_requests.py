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
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
SOURCES_PATH = ROOT / "notifications" / "sources.py"
BELL_PATH = ROOT / "src" / "components" / "notifications" / "NotificationsBell.jsx"
APP_PATH = ROOT / "src" / "App.jsx"


def _database_source():
    return DATABASE_PATH.read_text(encoding="utf-8-sig")


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


def _module_function_source(name):
    source = _database_source()
    module = source_cache.parse(source)
    func = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return textwrap.dedent(ast.get_source_segment(source, func))


class _Db:
    """Подставной Database: только те методы, что нужны проверяемой логике."""

    def __init__(self):
        namespace = {
            'date': date,
            'datetime': datetime,
            'dt_time': dt_time,
            'timedelta': timedelta,
        }
        exec(_module_function_source('_minutes_to_time'), namespace)
        namespace['_minutes_to_time'] = namespace['_minutes_to_time']
        for method_name in (
            '_normalize_schedule_time',
            '_shift_change_minutes',
            '_normalize_shift_change_kind',
            '_normalize_shift_change_payload',
            '_validate_shift_change_against_schedule',
            '_merge_break_intervals',
            '_swap_extract_interval',
            '_swap_is_interval_fully_covered',
        ):
            exec(_method_source(method_name), namespace)
            setattr(self.__class__, method_name, namespace[method_name])
        self.SHIFT_CHANGE_REQUEST_KINDS = ('shorten', 'extra')


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
        self.assertIn("CHECK (request_kind IN ('shorten', 'extra'))", self.source)

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

    def test_scope_mirrors_the_planner_and_excludes_trainer(self):
        """Тренер видит графики, но решать не может — уведомлять его о том,
        чего он не сделает, значит слать шум."""
        index = self.source.find("def _shift_change_scope_for_requester(")
        self.assertNotEqual(index, -1)
        body = self.source[index:index + 2000]
        self.assertIn("'trainer'", body)
        self.assertIn("'none'", body)
        self.assertIn("_is_global_admin_requester", body)
        self.assertIn("_headed_department_ids", body)


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


if __name__ == "__main__":
    unittest.main()
