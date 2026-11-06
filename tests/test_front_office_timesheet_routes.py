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

    def test_scope_needs_no_month_and_is_closed_to_outsiders(self):
        with mock.patch.object(data, 'scope', return_value={'department_id': 909}):
            response = self.client().get('/api/front_office_timesheet/scope')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'department_id': 909})
        with mock.patch.object(data, 'scope') as scope:
            denied = self.client(allowed=False).get('/api/front_office_timesheet/scope')
        self.assertEqual(denied.status_code, 403)
        scope.assert_not_called()

    def test_data_is_the_same_timesheet_for_the_screen(self):
        with mock.patch.object(data, 'collect', return_value=_inputs()) as collect:
            response = self.client().get('/api/front_office_timesheet/data?month=2026-10&city=Астана')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(collect.call_args.kwargs, {'with_facts': True})
        payload = response.get_json()
        self.assertEqual((payload['month'], payload['title'], payload['fact_until']),
                         ('2026-10', 'октябрь 2026', '2026-10-31'))
        self.assertEqual(len(payload['days']), 31)
        self.assertEqual(payload['days'][0], {'date': '2026-10-01', 'day': 1, 'weekday': 'чт', 'weekend': False,
                                              'holiday': False, 'future': False})
        self.assertTrue(payload['days'][2]['weekend'])
        self.assertEqual([city['city'] for city in payload['cities']], ['Астана'])
        person = payload['cities'][0]['people'][0]
        self.assertEqual((person['name'], person['active'], person['job_title']), ('Альфа Анна', True, 'менеджер'))
        self.assertEqual(person['cells'][0]['code'], 'н')
        self.assertEqual(person['totals'], {'worked': 0, 'missed': 1, 'late': 0, 'early': 0})
        self.assertEqual(payload['without_card'], ['Бета Болат'])

    def test_data_refuses_days_the_attendance_section_did_not_collect(self):
        with mock.patch.object(data, 'collect', return_value=_inputs(pending=['2026-10-30'])):
            response = self.client().get('/api/front_office_timesheet/data?month=2026-10')
        self.assertEqual(response.status_code, 503)

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


class FrontendWiringTests(unittest.TestCase):
    """«Учет часов»: при группе фронт-офисов вместо таблицы часов — табель (06.11.2026)."""

    def test_hours_page_swaps_the_table_for_the_timesheet(self):
        app = source_cache.read(source_cache.ROOT / 'src' / 'App.jsx')
        self.assertIn('{showTimesheetView ? (', app)
        self.assertIn('<FrontOfficeTimesheetView', app)
        # Главе фронт-офисов — весь раздел, отдельной страницы табеля больше нет.
        self.assertEqual(app.count('{( view === "sv_hours" && (<HoursAccountingView user={user} svList={svList} showToast={showToast} />))}'), 2)
        self.assertNotIn('FrontOfficeTimesheetPage', app)
        # Сегмент «Табель» гасит подсветку показателей, выбор показателя выключает табель.
        self.assertIn('const activeInGroup = !showTimesheetView && group.tabs.some(', app)
        self.assertIn('setHoursTimesheetMode(false);', app)
        self.assertIn('headedDepartmentId: hoursDepartmentScopeId,', app)
        # Главе фронт-офисов в окне отчёта — только табель.
        self.assertIn('timesheetOnly: isHoursDepartmentHead && timesheetDepartmentId != null', app)


if __name__ == '__main__':
    unittest.main()
