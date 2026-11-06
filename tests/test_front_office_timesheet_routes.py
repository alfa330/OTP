# -*- coding: utf-8 -*-
"""Табель фронт-офисов: ручки /api/front_office_timesheet и их подключение.

Решение владельца 06.11.2026: выгружают глава фронт-офисов и суперадмины,
остальным — отказ. Сбор месяца подменён (его проверяет test_front_office_timesheet_data.py).
"""

import unittest
from datetime import date, time
from functools import wraps
from unittest import mock
from urllib.parse import unquote

from flask import Flask

from front_office_timesheet import data, routes, rules
from tests import source_cache


def _inputs(pending=()):
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    people = [{'user_id': 1, 'name': 'Альфа Анна', 'city': 'Астана', 'job_title': '', 'active': True},
              {'user_id': 2, 'name': 'Бета Болат', 'city': 'Алматы', 'job_title': 'техперсонал', 'active': True}]
    spans = {(1, date(2026, 10, 1)): rules.shift_span(date(2026, 10, 1), time(9), time(19))}
    return {
        'month': '2026-10', 'start': start, 'end': end, 'window_start': date(2026, 9, 1),
        'today': date(2026, 11, 6), 'fact_until': end, 'department_id': 909, 'people': people,
        'spans': spans, 'shift_days': {}, 'days_off': set(), 'periods': {}, 'membership': {},
        'facts': {}, 'has_card': {1}, 'holidays': set(), 'pending_days': list(pending),
        'without_card': ['Бета Болат'],
    }


def _passthrough(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


class RoutesTests(unittest.TestCase):
    def client(self, allowed=True):
        app = Flask(__name__)
        app.register_blueprint(routes.build_front_office_timesheet_blueprint(
            db=object(),
            require_api_key=_passthrough,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (7, (7, 'login', 'Имя', 'admin'), None),
            can_export=lambda requester_id, requester: allowed,
            today_fn=lambda: date(2026, 11, 6),
        ))
        return app.test_client()

    def test_outsiders_get_403(self):
        with mock.patch.object(data, 'collect') as collect:
            response = self.client(allowed=False).get('/api/front_office_timesheet/export?month=2026-10')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'TIMESHEET_FORBIDDEN')
        collect.assert_not_called()

    def test_month_is_required(self):
        response = self.client().get('/api/front_office_timesheet/export?month=10-2026')
        self.assertEqual(response.status_code, 400)

    def test_export_returns_the_workbook_with_a_readable_name(self):
        with mock.patch.object(data, 'collect', return_value=_inputs()) as collect:
            response = self.client().get(
                '/api/front_office_timesheet/export?month=2026-10&city=Астана&job_title=менеджер')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(collect.call_args.kwargs, {'with_facts': True})
        self.assertEqual(response.mimetype, routes.XLSX_MIME)
        self.assertTrue(response.data.startswith(b'PK'))
        disposition = unquote(response.headers['Content-Disposition'])
        self.assertIn('Табель фронт-офисы 2026-10.xlsx', disposition)

    def test_days_not_collected_by_the_attendance_section_are_refused(self):
        with mock.patch.object(data, 'collect', return_value=_inputs(pending=['2026-10-30'])):
            response = self.client().get('/api/front_office_timesheet/export?month=2026-10')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'TIMESHEET_PENDING_DAYS')

    def test_empty_selection_is_a_clear_404(self):
        with mock.patch.object(data, 'collect', return_value=_inputs()):
            response = self.client().get('/api/front_office_timesheet/export?month=2026-10&city=Караганда')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'TIMESHEET_EMPTY')

    def test_options_list_cities_people_and_positions_of_the_month(self):
        with mock.patch.object(data, 'collect', return_value=_inputs()) as collect:
            response = self.client().get('/api/front_office_timesheet/options?month=2026-10')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(collect.call_args.kwargs, {'with_facts': False})
        payload = response.get_json()
        self.assertEqual(payload['cities'], [{'value': 'Алматы', 'label': 'Алматы'},
                                             {'value': 'Астана', 'label': 'Астана'}])
        self.assertEqual([p['name'] for p in payload['people']], ['Бета Болат', 'Альфа Анна'])
        self.assertEqual(payload['job_titles'], ['менеджер', 'техперсонал'])
        self.assertEqual(payload['without_card'], ['Бета Болат'])

    def test_unexpected_failure_is_a_500_without_details(self):
        with mock.patch.object(data, 'collect', side_effect=RuntimeError('секрет')):
            response = self.client().get('/api/front_office_timesheet/export?month=2026-10')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('секрет', response.get_data(as_text=True))


class WiringTests(unittest.TestCase):
    """Подключение в bot_schedule2: только суперадмин и глава отдела front_office."""

    def test_rule_and_registration(self):
        bot = source_cache.monolith_flat_text()
        start = bot.index('def _front_office_timesheet_allowed(requester_id, requester):')
        body = bot[start:bot.index('try:', start)]
        self.assertIn('if _is_super_admin_role(requester[3]):', body)
        self.assertIn('department_id = db.get_front_office_department_id()', body)
        self.assertIn('return int(department_id) in {int(d) for d in _headed_department_ids(requester_id)}', body)
        self.assertNotIn('_is_admin_role', body)
        self.assertNotIn('_is_global_admin_requester', body)
        self.assertIn('build_front_office_timesheet_blueprint(', bot)
        self.assertIn('can_export=_front_office_timesheet_allowed,', bot)


if __name__ == '__main__':
    unittest.main()
