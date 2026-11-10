# -*- coding: utf-8 -*-
"""Звонки из iCORE Phone — в «Звонки» «Учёта часов» (задача SZOV-4; владелец 10.11.2026: «все
звонки нужно учитывать»).

Раздел «Удаленный КЦ» отвечает на один вопрос синка звонков: сколько раз телефон сотрудника
набрал водителя за день. Здесь правило сторожится без базы — в любом прогоне; настоящим SQL его
проверяет tests/test_dial_list_line_members_postgres.py (нужен локальный Postgres), а сложение
со звонками Oktell — tests/test_oktell_calls_sync.py. Люди выдуманы.
"""
import contextlib
import os
import unittest
from datetime import date
from unittest import mock

from dial_list import service as dial_service
from test_numbers import keys as test_keys


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows=()):
        self.cursor = _Cursor(rows)
        self.opened = 0

    @contextlib.contextmanager
    def _get_cursor(self):
        self.opened += 1
        yield self.cursor


def _service(rows=()):
    service = dial_service.DialListService.__new__(dial_service.DialListService)
    service.db = _Db(rows)
    return service


def _env(value=None):
    patch = mock.patch.dict('os.environ', {} if value is None else {dial_service.HOURS_CALLS_ENV: value})
    patch.start()
    if value is None:
        os.environ.pop(dial_service.HOURS_CALLS_ENV, None)
    return patch


class HoursCallsTests(unittest.TestCase):
    def setUp(self):
        patch = _env()
        self.addCleanup(patch.stop)

    def test_counts_come_back_by_person_and_almaty_day(self):
        service = _service([(701, date(2026, 11, 10), 120), (702, date(2026, 11, 10), 95),
                            (701, date(2026, 11, 11), 3)])
        result = service.hours_calls(date(2026, 11, 10), date(2026, 11, 11))

        self.assertEqual(result, {(701, '2026-11-10'): 120, (702, '2026-11-10'): 95, (701, '2026-11-11'): 3})
        (sql, params), = service.db.cursor.executed
        self.assertIs(sql, dial_service.DialListService._HOURS_CALLS_SQL)
        self.assertEqual(params, {'day_from': date(2026, 11, 10), 'day_to': date(2026, 11, 11), 'codes': ['szov']})

    def test_server_switch_returns_nothing_without_reading(self):
        for value in ('0', 'false', 'off', 'no'):
            patch = _env(value)
            try:
                service = _service([(701, date(2026, 11, 10), 120)])
                self.assertEqual(service.hours_calls(date(2026, 11, 10), date(2026, 11, 10)), {}, value)
                self.assertEqual(service.db.opened, 0, value)
            finally:
                patch.stop()
        for value in ('1', 'true', ''):
            patch = _env(value)
            try:
                service = _service([(701, date(2026, 11, 10), 120)])
                self.assertEqual(len(service.hours_calls(date(2026, 11, 10), date(2026, 11, 10))), 1, value)
            finally:
                patch.stop()

    def test_only_szov_staff(self):
        self.assertEqual(dial_service.HOURS_CALLS_DEPARTMENT_CODES, ('szov',))
        sql = dial_service.DialListService._HOURS_CALLS_SQL
        # Отдел — где человек числится (users.department_id), а не отдел базы или линии.
        self.assertIn('JOIN users u ON u.id = t.operator_id', sql)
        self.assertIn('JOIN departments d ON d.id = u.department_id', sql)
        self.assertIn('lower(d.code) = ANY(%(codes)s)', sql)
        self.assertNotIn('dial_list_leads l', sql)
        self.assertNotIn('dial_list_line_members', sql)

    def test_every_placed_call_counts(self):
        """«Все звонки»: без ответа, короткие, сброшенные оператором — тоже звонки. Отказ АТС
        (набора не было) и проверочные звонки на номера «Реестра тестовых номеров» — нет."""
        sql = dial_service.DialListService._HOURS_CALLS_SQL
        self.assertIn("t.state <> 'failed'", sql)
        self.assertIn(test_keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL, sql)
        for forbidden in ('cancelled', 'billsec', 'disposition', 'DIALOG', 'auto'):
            self.assertNotIn(forbidden, sql.replace(test_keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL, ''), forbidden)

    def test_day_is_the_almaty_day_of_the_call_start(self):
        sql = dial_service.DialListService._HOURS_CALLS_SQL
        self.assertIn("(t.requested_at AT TIME ZONE 'Asia/Almaty')::date AS day", sql)
        # Границы периода — моменты: индекс по requested_at работает, сутки — алматинские.
        self.assertIn("t.requested_at >= (%(day_from)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'", sql)
        self.assertIn("t.requested_at < ((%(day_to)s::date) + 1)::timestamp AT TIME ZONE 'Asia/Almaty'", sql)
        self.assertIn('GROUP BY 1, 2', sql)


if __name__ == '__main__':
    unittest.main()
