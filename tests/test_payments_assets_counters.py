# -*- coding: utf-8 -*-
"""Счётчики плиток статусов над реестром имущества.

Просьба владельца 13.10.2026: вкладки «Имущество», «Справочники» и «Заявки» «нужно
переделать, чтобы разделение было более заметным и понятным на первый взгляд». Статусы
имущества были списком «Все статусы» — теперь это плитки со счётчиками, как состояния у
заявок: сколько в эксплуатации, на складе, в ремонте и списано, видно сразу.

Что проверяется:
* assets.status_counts считает по ТЕКУЩЕМУ отбору без учёта самого статуса — выбранная
  плитка не обнуляет соседние;
* условие отбора у списка и у счётчиков одно (assets._where);
* GET /api/payments/assets отдаёт `counters` рядом со списком.

База не нужна: курсор и запросы подменены unittest.mock.
"""

import unittest
from unittest import mock

from flask import Flask

from payments import access, assets, routes, routes_assets, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
STATUSES = [code for code, _label in wf.ASSET_STATUSES]


def cursor_with(rows):
    cursor = mock.MagicMock()
    cursor.fetchall.return_value = rows
    return cursor


class StatusCountsTests(unittest.TestCase):
    def test_every_status_is_present_and_the_total_is_the_sum(self):
        cursor = cursor_with([('in_use', 24), ('repair', 12)])
        counts = assets.status_counts(cursor)
        self.assertEqual(counts, {'in_use': 24, 'in_stock': 0, 'repair': 12, 'written_off': 0, 'all': 36})
        self.assertEqual(set(counts) - {'all'}, set(STATUSES))
        sql, params = cursor.execute.call_args[0]
        self.assertIn('FROM payment_assets a', sql)
        self.assertIn('GROUP BY a.status', sql)
        self.assertNotIn('WHERE', sql)
        self.assertEqual(params, {})

    def test_the_chosen_status_does_not_narrow_the_counters(self):
        """Выбрали плитку «В ремонте» — соседние плитки не должны показать нули."""
        cursor = cursor_with([('in_use', 3), ('repair', 1)])
        counts = assets.status_counts(cursor, status='repair', city='Алматы', department_id=7, query='монитор')
        self.assertEqual(counts['in_use'], 3)
        self.assertEqual(counts['all'], 4)
        sql, params = cursor.execute.call_args[0]
        self.assertNotIn('a.status =', sql)
        self.assertNotIn('status', params)
        # Остальной отбор — тот же, что у списка.
        self.assertIn('a.city = %(city)s', sql)
        self.assertIn('a.department_id = %(department_id)s', sql)
        self.assertIn('a.name ILIKE %(like)s', sql)
        self.assertEqual(params['city'], 'Алматы')
        self.assertEqual(params['department_id'], 7)

    def test_callers_filters_are_left_intact(self):
        filters = {'status': 'repair', 'city': 'Алматы'}
        assets.status_counts(cursor_with([]), **filters)
        self.assertEqual(filters, {'status': 'repair', 'city': 'Алматы'})

    def test_unknown_status_counts_into_the_total_only(self):
        """Статус, которого раздел не знает (старые данные), своей плитки не получает, но в «Всё» входит."""
        counts = assets.status_counts(cursor_with([('in_use', 2), ('lost', 5), ('repair', None)]))
        self.assertNotIn('lost', counts)
        self.assertEqual(counts['all'], 7)
        self.assertEqual(counts['repair'], 0)

    def test_empty_register(self):
        counts = assets.status_counts(cursor_with([]))
        self.assertEqual(counts, {'in_use': 0, 'in_stock': 0, 'repair': 0, 'written_off': 0, 'all': 0})


class OneWhereForListAndCountersTests(unittest.TestCase):
    def test_list_still_filters_by_status(self):
        cursor = cursor_with([])
        assets.list_assets(cursor, status='repair', category_id='5', limit=20, offset=40)
        sql, params = cursor.execute.call_args[0]
        self.assertIn('a.status = %(status)s', sql)
        self.assertIn('a.category_id = %(category_id)s', sql)
        self.assertIn('ORDER BY a.id DESC', sql)
        self.assertEqual((params['status'], params['category_id'], params['limit'], params['offset']), ('repair', 5, 20, 40))

    def test_for_request_reads_everything_of_the_request(self):
        cursor = cursor_with([])
        self.assertEqual(assets.for_request(cursor, 17), [])
        sql, params = cursor.execute.call_args[0]
        self.assertIn('a.request_id = %(request_id)s', sql)
        self.assertNotIn('LIMIT', sql)
        self.assertEqual(params, {'request_id': 17})

    def test_where_is_shared(self):
        where, params = assets._where(city='Астана', query='  ноутбук ')
        self.assertTrue(where.startswith(' WHERE '))
        self.assertIn(' AND ', where)
        self.assertEqual(params['city'], 'Астана')
        self.assertEqual(assets._where(), ('', {}))


class AssetsListEndpointTests(unittest.TestCase):
    """GET /api/payments/assets: счётчики приходят вместе со списком."""

    def get(self, query=''):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        assets_sql = mock.MagicMock()
        assets_sql.list_assets.return_value = (1, [{'id': 5, 'name': 'Монитор', 'status': 'repair'}])
        assets_sql.filter_values.return_value = {'cities': ['Алматы'], 'departments': []}
        assets_sql.status_counts.return_value = {'in_use': 24, 'in_stock': 0, 'repair': 12, 'written_off': 0, 'all': 36}
        for patcher in (mock.patch.object(routes, 'queries', queries),
                        mock.patch.object(routes_assets, 'assets_sql', assets_sql)):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None)))
        response = app.test_client().get('/api/payments/assets' + query)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json(), assets_sql

    def test_counters_come_with_the_list(self):
        body, assets_sql = self.get('?status=repair&city=%D0%90%D0%BB%D0%BC%D0%B0%D1%82%D1%8B&limit=50')
        self.assertEqual(body['counters'], {'in_use': 24, 'in_stock': 0, 'repair': 12, 'written_off': 0, 'all': 36})
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['facets'], {'cities': ['Алматы'], 'departments': []})
        # Список и счётчики получают один и тот же отбор; статус из счётчиков выбрасывает сам запрос.
        list_kwargs = assets_sql.list_assets.call_args[1]
        count_kwargs = assets_sql.status_counts.call_args[1]
        self.assertEqual(list_kwargs['status'], 'repair')
        self.assertEqual(list_kwargs['city'], 'Алматы')
        self.assertEqual((list_kwargs['limit'], list_kwargs['offset']), (50, 0))
        self.assertEqual({key: value for key, value in list_kwargs.items() if key not in ('limit', 'offset')}, count_kwargs)

    def test_unknown_status_is_refused_before_any_query(self):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        assets_sql = mock.MagicMock()
        for patcher in (mock.patch.object(routes, 'queries', queries),
                        mock.patch.object(routes_assets, 'assets_sql', assets_sql)):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None)))
        response = app.test_client().get('/api/payments/assets?status=lost')
        self.assertEqual(response.status_code, 400)
        assets_sql.list_assets.assert_not_called()
        assets_sql.status_counts.assert_not_called()


if __name__ == '__main__':
    unittest.main()
