"""Catalog scope applies to cards, counts, facets and the migration fallback."""
import unittest
from unittest import mock

import psycopg2
from call_qa import api


class CatalogScopeTests(unittest.TestCase):
    def test_department_and_user_scope_reach_every_catalog_query(self):
        for family in ([83], []):
            with self.subTest(family=family):
                conn = mock.MagicMock()
                cur = conn.cursor.return_value
                cur.fetchall.return_value = []
                cur.fetchone.side_effect = [(0,), (0, 0, 0, None, 'model')]
                with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
                     mock.patch.object(api, '_scoped_qa_family', return_value=family) as scope:
                    result = api.adjudications_list(department='tez', allowed_direction_ids=[83])
                scope.assert_called_once_with(cur, [83], department='tez')
                self.assertEqual(result['health']['status'], 'healthy')
                self.assertEqual(result['items'], [])
                queries = [(str(c.args[0]), c.args[1] if len(c.args) > 1 else ())
                           for c in cur.execute.call_args_list]
                for sql, params in queries:
                    if 'FROM qa_policy_rule_catalog c' in sql:
                        self.assertIn('c.direction_id = ANY(%s)', sql)
                        self.assertIn(family or [-1], params)
                    if 'WITH scoped_rules' in sql:
                        self.assertEqual(params[:4], (family, family, family, family))
                        self.assertIn('FROM scoped_knowledge', sql)
                        self.assertIn('FROM scoped_rules', sql)
                conn.close.assert_called_once()

    def test_legacy_fallback_preserves_department_and_permissions(self):
        conn = mock.MagicMock()
        def execute(sql, params=None):
            if 'qa_policy_rule_catalog' in sql:
                raise psycopg2.errors.UndefinedTable('catalog migration pending')
        conn.cursor.return_value.execute.side_effect = execute
        with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
             mock.patch.object(api, '_scoped_qa_family', return_value=[83]), \
             mock.patch.object(api.runtime_store, 'is_schema_compat_error', return_value=True), \
             mock.patch.object(api, '_legacy_adjudications_page', return_value={'items': []}) as legacy:
            api.adjudications_list(department='tez', allowed_direction_ids=[83])
        self.assertEqual(legacy.call_args.kwargs['department'], 'tez')
        self.assertEqual(legacy.call_args.kwargs['allowed_direction_ids'], [83])

    def test_legacy_query_cannot_return_another_department(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
             mock.patch.object(api, '_scoped_qa_family', return_value=[83]):
            api._legacy_adjudications_page(department='tez', allowed_direction_ids=[83], direction='72')
        sql, params = cur.execute.call_args.args
        self.assertIn('a.direction_id = ANY(%s)', sql)
        self.assertIn('a.direction_id=%s', sql)
        self.assertEqual(params[:2], [[83], 72])

    def test_unavailable_modern_filters_never_silently_broaden_legacy_catalog(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.execute.side_effect = psycopg2.errors.UndefinedTable('migration pending')
        with mock.patch.object(api.config, 'connect_ro', return_value=conn), \
             mock.patch.object(api.runtime_store, 'is_schema_compat_error', return_value=True), \
             mock.patch.object(api, '_legacy_adjudications_page') as legacy:
            result = api.adjudications_list(department='tez', status='draft')
        legacy.assert_not_called()
        self.assertEqual(result['health']['status'], 'error')


if __name__ == '__main__':
    unittest.main()
