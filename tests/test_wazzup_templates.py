"""No production app, vendor mutation or production database access."""
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock, patch

import requests
from flask import Blueprint, Flask, jsonify

from wazzup import templates

CHANNEL = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
OTHER = 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'
TEMPLATE = 'cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee'
GLOBAL = sorted(templates.EXCLUDED_CHANNELS)[0]


def vendor_row(**updates):
    result = dict(templateGuid=TEMPLATE, title='Greeting', status='approved',
                  channels=[CHANNEL], components=[dict(type='BODY', text='Hello {{1}}')],
                  templateCode='@template: ' + TEMPLATE + ' { [[bodyVar1]] }')
    result.update(updates)
    return result


class VendorTemplatesTests(unittest.TestCase):
    def setUp(self):
        templates._cache.clear()
        self.key = patch.object(templates.accounts, 'api_key', return_value='test-private-key')
        self.key.start()
        self.addCleanup(self.key.stop)
        self.transport = Mock()
        self.transport.get.return_value = Mock(status_code=200)
        self.transport.get.return_value.json.return_value = [vendor_row()]

    def listing(self, channel_id=CHANNEL):
        return templates.list_templates('op', channel_id, transport=self.transport)

    def test_normalizes_variables_and_approved_templates(self):
        result = self.listing()
        self.assertEqual(result['items'][0]['variables'], ['bodyVar1'])
        self.assertEqual(result['items'][0]['source'], 'wazzup')
        self.assertTrue(result['items'][0]['supported'])
        self.assertEqual(result['sourceWarnings'], [])
        self.assertEqual(self.transport.get.call_args.kwargs['params'], {'limit': 100, 'offset': 0})

    def test_preview_renders_wire_values_without_another_vendor_request(self):
        self.listing()
        code = '@template: ' + TEMPLATE + ' { [[Алия]] }'
        preview = templates.render_template_preview('op', CHANNEL, code)
        self.assertIn('Алия', preview)
        self.assertNotIn('{{1}}', preview)
        self.assertNotIn('@template:', preview)
        self.assertEqual(self.transport.get.call_count, 1)

    def test_hides_other_channel_global_and_unapproved(self):
        self.transport.get.return_value.json.return_value = [
            vendor_row(channels=[GLOBAL]), vendor_row(channels=[OTHER]),
            vendor_row(status='rejected'), vendor_row(status='paused'), vendor_row(status=None),
        ]
        self.assertEqual(self.listing()['items'], [])
        self.assertEqual(len(self.listing(None)['items']), 1)

    def test_filters_global_from_shared_channel_list(self):
        self.transport.get.return_value.json.return_value = [vendor_row(channels=[CHANNEL, GLOBAL])]
        self.assertEqual(self.listing()['items'][0]['channels'], [CHANNEL])

    def test_media_template_cannot_be_sent_as_text(self):
        self.transport.get.return_value.json.return_value = [vendor_row(components=[
            dict(type='HEADER', format='IMAGE'), dict(type='BODY', text='Hello {{1}}')])]
        result = self.listing()['items'][0]
        self.assertFalse(result['supported'])
        self.assertIn('вложениями', result['unsupportedReason'])

    def test_cache_is_shared_between_channels_and_returned_copies_are_isolated(self):
        first = self.listing()
        first['items'][0]['channels'].clear()
        self.assertEqual(self.listing()['items'][0]['channels'], [CHANNEL])
        self.listing(OTHER)
        self.assertEqual(self.transport.get.call_count, 1)

    def test_concurrent_cold_requests_only_fetch_once(self):
        entered, release = threading.Event(), threading.Event()
        response = self.transport.get.return_value

        def slow_get(*args, **kwargs):
            entered.set()
            release.wait(1)
            return response

        self.transport.get.side_effect = slow_get
        with ThreadPoolExecutor(max_workers=6) as executor:
            first = executor.submit(self.listing)
            self.assertTrue(entered.wait(1))
            rest = [executor.submit(self.listing) for _ in range(5)]
            release.set()
            results = [first.result()] + [future.result() for future in rest]
        self.assertEqual(self.transport.get.call_count, 1)
        self.assertTrue(all(len(result['items']) == 1 for result in results))

    def test_failure_keeps_warm_copy_but_does_not_hammer_vendor(self):
        self.listing()
        entry = next(iter(templates._cache.values()))
        entry['expires'] = 0
        self.transport.get.side_effect = requests.Timeout()
        result = self.listing()
        self.assertTrue(result['stale'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(len(result['sourceWarnings']), 1)
        self.listing()
        self.assertEqual(self.transport.get.call_count, 2)

    def test_expired_stale_copy_is_not_retained_forever(self):
        self.listing()
        entry = next(iter(templates._cache.values()))
        entry['expires'] = 0
        entry['loaded'] -= templates.MAX_STALE + 1
        self.transport.get.side_effect = requests.Timeout()
        result = self.listing()
        self.assertFalse(result['stale'])
        self.assertEqual(result['items'], [])

    def test_cold_errors_are_not_misreported_as_success(self):
        self.transport.get.return_value.status_code = 403
        result = self.listing()
        self.assertEqual(result['items'], [])
        self.assertEqual(len(result['sourceWarnings']), 1)
        self.assertNotIn('test-private-key', str(result))

    def test_key_rotation_discards_previous_key_cache(self):
        self.listing()
        with patch.object(templates.accounts, 'api_key', return_value='rotated-key'):
            self.listing()
        self.assertEqual(self.transport.get.call_count, 2)
        self.assertEqual(len(templates._cache), 1)

    def test_paginates_without_duplicate_first_page(self):
        first_page = [vendor_row() for _ in range(100)]
        self.transport.get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=first_page)),
            Mock(status_code=200, json=Mock(return_value=[vendor_row()])),
        ]
        self.assertEqual(len(self.listing()['items']), 101)
        self.assertEqual(self.transport.get.call_args_list[1].kwargs['params']['offset'], 100)

    def test_regular_text_requires_no_external_request(self):
        self.assertIsNone(templates.validate_template_message('op', CHANNEL, 'Hello'))
        self.transport.get.assert_not_called()

    def test_validates_filled_waba_code_and_channel(self):
        self.listing()
        filled = '@template: ' + TEMPLATE + ' { [[Alex]] }'
        self.assertIsNone(templates.validate_template_message('op', CHANNEL, filled))
        self.assertIsNotNone(templates.validate_template_message('op', OTHER, filled))
        self.assertIsNotNone(templates.validate_template_message('op', GLOBAL, filled))

    def test_rejects_empty_missing_unfilled_and_injected_variables(self):
        self.listing()
        for contents in ('', '[[ ]]', '[[bodyVar1]]', '[[Alex]];[[More]]', '[[Alex]] junk',
                         '[[Alex[[nested]]]]', '[[Alex]];'):
            with self.subTest(contents=contents):
                self.assertIsNotNone(templates.validate_template_message(
                    'op', CHANNEL, '@template: ' + TEMPLATE + ' { ' + contents + ' }'))

    def test_zero_variable_template(self):
        self.transport.get.return_value.json.return_value = [vendor_row(
            templateCode='@template: ' + TEMPLATE + ' { }')]
        self.listing()
        self.assertIsNone(templates.validate_template_message('op', CHANNEL,
                                                             '@template: ' + TEMPLATE + ' { }'))

    def test_malformed_upstream_items_are_skipped(self):
        self.transport.get.return_value.json.return_value = [
            None, {}, vendor_row(templateGuid='wrong'), vendor_row(templateCode='wrong'),
            vendor_row(components='wrong'), vendor_row(channels='wrong'),
        ]
        self.assertEqual(self.listing()['items'], [])


class LocalDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('''CREATE TABLE wazzup_quick_templates (
            id TEXT PRIMARY KEY, account TEXT, title TEXT, body TEXT,
            created_by INTEGER, updated_by INTEGER, updated_at TEXT)''')
        self.conn.execute('''CREATE TABLE wazzup_imported_templates (
            id TEXT PRIMARY KEY, account TEXT NOT NULL, external_id TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, attachment_count INTEGER NOT NULL DEFAULT 0,
            imported_by INTEGER NOT NULL, imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account, external_id))''')

    @contextmanager
    def _get_cursor(self):
        cursor = self.conn.cursor()

        class Adapter:
            def execute(self, sql, values=()):
                if 'pg_advisory_xact_lock' not in sql:
                    cursor.execute(sql.replace('%s', '?').replace('NOW()', 'CURRENT_TIMESTAMP'), values)

            def fetchone(self):
                return cursor.fetchone()

            def fetchall(self):
                return cursor.fetchall()

        try:
            yield Adapter()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()


class TemplateRoutesTests(unittest.TestCase):
    def setUp(self):
        self.db = LocalDatabase()
        self.addCleanup(self.db.conn.close)
        app = Flask(__name__)
        bp = Blueprint('templates-test', __name__, url_prefix='/pilot')
        self.allowed = True
        self.role = 'super_admin'

        def actor():
            return ([42, None, None, self.role], None) if self.allowed else (None, (jsonify(error='Forbidden'), 403))

        templates.register_template_routes(bp, actor, lambda f: f, lambda: ('', 204), self.db)
        app.register_blueprint(bp)
        self.client = app.test_client()
        self.vendor = patch.object(templates, 'list_templates', return_value=dict(
            items=[], stale=False, sourceWarnings=[]))
        self.vendor_mock = self.vendor.start()
        self.addCleanup(self.vendor.stop)

    def create(self, **updates):
        return self.client.post('/pilot/templates', json=dict(title='Greeting', text='Hello', **updates))

    def import_items(self, items=None, **updates):
        payload = dict(account='op', dryRun=False, items=items if items is not None else [dict(
            externalId='source-1', title='Vendor greeting', text='Hello\n{{name}}', files=[])])
        payload.update(updates)
        return self.client.post('/pilot/templates/import', json=payload)

    def test_shared_template_crud(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        item = response.json['item']
        self.assertEqual(item['source'], 'icore')
        self.assertEqual(item['variables'], [])
        path = '/pilot/templates/' + item['id']
        listing = self.client.get('/pilot/templates')
        self.assertEqual(listing.json['items'], [item])
        updated = self.client.patch(path, json=dict(title='Goodbye', text='See you'))
        self.assertEqual(updated.json['item']['text'], 'See you')
        self.assertEqual(self.db.conn.execute('SELECT updated_by FROM wazzup_quick_templates').fetchone()[0], 42)
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.get('/pilot/templates').json['items'], [])
        self.assertEqual(self.client.delete(path).status_code, 404)

    def test_all_operations_use_existing_pilot_guard(self):
        self.allowed = False
        for method, path in [('get', '/pilot/templates'), ('post', '/pilot/templates'),
                             ('patch', '/pilot/templates/' + TEMPLATE), ('delete', '/pilot/templates/' + TEMPLATE)]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 403)

    def test_rejects_wrong_account_and_global_channel(self):
        self.assertEqual(self.client.get('/pilot/templates?account=potok').status_code, 403)
        self.assertEqual(self.create(account='potok').status_code, 400)
        self.assertEqual(self.client.get('/pilot/templates?channelId=' + GLOBAL).status_code, 403)
        self.assertEqual(self.client.get('/pilot/templates?channelId=not-a-uuid').status_code, 400)

    def test_validates_body_types_and_lengths(self):
        for body in ({}, [], {'title': 'a', 'text': ''}, {'title': 'a', 'text': 1},
                     {'title': 'a' * 101, 'text': 'x'}, {'title': 'a', 'text': 'x' * 4097}):
            with self.subTest(body_type=type(body).__name__):
                self.assertEqual(self.client.post('/pilot/templates', json=body).status_code, 400)

    def test_cannot_modify_vendor_or_other_account_template(self):
        self.db.conn.execute('INSERT INTO wazzup_quick_templates(id,account,title,body) VALUES(?,?,?,?)',
                             (TEMPLATE, 'potok', 'Hidden', 'Hidden'))
        self.assertEqual(self.client.get('/pilot/templates').json['items'], [])
        self.assertEqual(self.client.patch('/pilot/templates/' + TEMPLATE,
                                         json=dict(title='New', text='New')).status_code, 404)
        self.assertEqual(self.client.delete('/pilot/templates/' + TEMPLATE).status_code, 404)

    def test_catalogue_limit_prevents_unbounded_growth(self):
        with patch.object(templates, 'MAX_LOCAL', 1):
            self.assertEqual(self.create().status_code, 201)
            self.assertEqual(self.create().status_code, 409)

    def test_options_does_not_require_actor_or_database(self):
        self.allowed = False
        self.assertEqual(self.client.options('/pilot/templates').status_code, 204)
        self.assertEqual(self.client.options('/pilot/templates/import').status_code, 204)

    def test_import_dry_run_reports_changes_without_writing_or_exposing_content(self):
        response = self.import_items(dryRun=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, dict(status='success', dryRun=True, total=1,
                                            created=1, updated=0, unchanged=0, unsupported=0))
        self.assertEqual(self.client.get('/pilot/templates').json['items'], [])
        # Missing dryRun is deliberately a preview, never an implicit mutation.
        payload = dict(account='op', items=[dict(externalId='source-1', title='Private title', text='Private body')])
        result = self.client.post('/pilot/templates/import', json=payload).json
        self.assertTrue(result['dryRun'])
        self.assertNotIn('Private', str(result))
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM wazzup_imported_templates').fetchone()[0], 0)

    def test_import_upserts_stable_id_preserves_whitespace_and_is_shared_across_channels(self):
        self.assertEqual(self.import_items().json['created'], 1)
        first = self.client.get('/pilot/templates?channelId=' + CHANNEL).json['items'][0]
        self.assertEqual(first['source'], 'wazzup')
        self.assertEqual(first['kind'], 'text')
        self.assertEqual(first['channels'], [])
        self.assertEqual(first['variables'], [])
        self.assertTrue(first['supported'])
        self.assertEqual(first['templateCode'], None)
        self.assertEqual(self.client.get('/pilot/templates?channelId=' + OTHER).json['items'][0], first)
        self.assertEqual(self.import_items().json['unchanged'], 1)
        self.db.conn.execute("UPDATE wazzup_imported_templates SET imported_at='unchanged-marker'")
        self.assertEqual(self.import_items().json['unchanged'], 1)
        self.assertEqual(self.db.conn.execute('SELECT imported_at FROM wazzup_imported_templates').fetchone()[0],
                         'unchanged-marker')
        rows = [dict(externalId='source-1', title='Renamed', text=' Leading\nTrailing ', files=[])]
        self.assertEqual(self.import_items(rows, dryRun=True).json['updated'], 1)
        self.assertEqual(self.client.get('/pilot/templates').json['items'][0], first)
        self.assertEqual(self.import_items(rows).json['updated'], 1)
        updated = self.client.get('/pilot/templates').json['items'][0]
        self.assertEqual(updated['id'], first['id'])
        self.assertEqual(updated['text'], ' Leading\nTrailing ')
        self.assertEqual(updated['title'], 'Renamed')

    def test_import_requires_super_admin_and_existing_guard(self):
        for role in ('operator', 'admin', 'sv', 'trainer', ''):
            self.role = role
            self.assertEqual(self.import_items().status_code, 403, role)
        self.role = 'super_admin'
        self.allowed = False
        self.assertEqual(self.import_items().status_code, 403)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM wazzup_imported_templates').fetchone()[0], 0)

    def test_import_with_attachments_remains_visible_but_cannot_be_inserted_as_text(self):
        items = [dict(externalId='file-only', title='File', text='',
                      files=[{'name': 'example.pdf', 'url': 'https://private.invalid/token-secret'}]),
                 dict(externalId='file-and-text', title='Photo', text='Caption', files=['image-id'])]
        response = self.import_items(items)
        self.assertEqual(response.json['unsupported'], 2)
        listing = self.client.get('/pilot/templates').json['items']
        self.assertEqual(len(listing), 2)
        for item in listing:
            self.assertFalse(item['supported'])
            self.assertEqual(item['attachmentCount'], 1)
            self.assertIn('вложения', item['unsupportedReason'])
        self.assertNotIn('token-secret', str(listing))
        self.assertEqual({item['text'] for item in listing}, {'', 'Caption'})
        items[0]['files'] = []
        items[0]['text'] = 'File replaced with text'
        self.assertEqual(self.import_items(items[:1]).json['updated'], 1)
        self.assertEqual(sum(item['supported'] for item in self.client.get('/pilot/templates').json['items']), 1)

    def test_invalid_import_is_atomic_and_does_not_echo_private_values(self):
        good = dict(externalId='good', title='Private title', text='Private body')
        other = {**good, 'externalId': 'bad'}
        malformed = [None, {}, {**other, 'externalId': ''}, {**other, 'externalId': 42},
                     {**other, 'externalId': 'bad\nidentifier'}, {**other, 'title': 'x' * 101},
                     {**other, 'text': 'x' * 4097}, {**other, 'text': ''}, {**other, 'text': None},
                     {**other, 'files': {}}, {**other, 'files': [None]}, {**other, 'text': '\x00'},
                     {**other, 'attachmentUrl': 'unknown file field'}]
        for bad in malformed:
            with self.subTest(bad=bad):
                response = self.import_items([good, bad])
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Private', str(response.json))
                self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM wazzup_imported_templates').fetchone()[0], 0)
        for body in ([], {}, dict(account='potok', items=[good]), dict(account='op', items=[]),
                     dict(account='op', items=[good], dryRun='false'), dict(account='op', items=[good, good])):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/pilot/templates/import', json=body).status_code, 400)

    def test_import_does_not_delete_absent_records_or_modify_local_and_waba_catalogues(self):
        local = self.create().json['item']
        self.import_items()
        self.import_items([dict(externalId='second', title='Second', text='Another reply')])
        listing = self.client.get('/pilot/templates').json['items']
        self.assertEqual(len(listing), 3)
        self.assertIn(local, listing)
        imported = next(item for item in listing if item['source'] == 'wazzup')
        self.assertEqual(self.client.patch('/pilot/templates/' + imported['id'],
                                         json=dict(title='Changed', text='Changed')).status_code, 404)
        self.assertEqual(self.client.delete('/pilot/templates/' + imported['id']).status_code, 404)
        # Import does not feed text rows into the WABA-only catalogue/parser.
        self.vendor_mock.return_value = dict(items=[templates._normalize_waba(vendor_row())], stale=False, sourceWarnings=[])
        self.assertIsNone(templates.validate_template_message('op', CHANNEL,
                          '@template: ' + TEMPLATE + ' { [[Alex]] }'))
        self.assertEqual(templates.render_template_preview('op', CHANNEL,
                         '@template: ' + TEMPLATE + ' { [[Alex]] }'), 'Hello Alex')

    def test_import_capacity_and_request_size_are_bounded_before_mutation(self):
        with patch.object(templates, 'MAX_IMPORTED', 1):
            self.assertEqual(self.import_items().status_code, 200)
            self.assertEqual(self.import_items([dict(externalId='second', title='Second', text='Text')]).status_code, 409)
            self.assertEqual(self.import_items().json['unchanged'], 1)
        with patch.object(templates, 'MAX_IMPORT_BYTES', 10):
            self.assertEqual(self.import_items().status_code, 413)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM wazzup_imported_templates').fetchone()[0], 1)

    def test_imported_replies_keep_existing_account_and_global_access_boundaries(self):
        self.import_items()
        for query in ('?account=potok', '?channelId=' + GLOBAL):
            self.assertEqual(self.client.get('/pilot/templates' + query).status_code, 403)
        self.role = 'operator'
        self.assertEqual(len(self.client.get('/pilot/templates?channelId=' + CHANNEL).json['items']), 1)
        self.allowed = False
        self.assertEqual(self.client.get('/pilot/templates?channelId=' + CHANNEL).status_code, 403)

    def test_import_rolls_back_earlier_rows_when_database_write_fails(self):
        self.db.conn.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON wazzup_imported_templates
            WHEN NEW.external_id = 'fail' BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        self.client.application.config['TESTING'] = True
        with self.assertRaises(sqlite3.IntegrityError):
            self.import_items([dict(externalId='ok', title='First', text='First'),
                               dict(externalId='fail', title='Second', text='Second')])
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM wazzup_imported_templates').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
