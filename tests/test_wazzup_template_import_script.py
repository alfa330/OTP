import unittest

from scripts.import_wazzup_templates import browser_export_payload


GUID = 'cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee'


def export(**updates):
    row = dict(guid=GUID, name='Reply &amp; help', text='First -&gt; second\n&lt;b&gt;text&lt;/b&gt;', files=[])
    row.update(updates)
    return dict(workspace='6757-7677', total=1, data=[row])


class BrowserTemplateExportTests(unittest.TestCase):
    def test_preview_preserves_newlines_and_decodes_entities_once_as_text(self):
        payload = browser_export_payload(export())
        self.assertTrue(payload['dryRun'])
        self.assertEqual(payload['items'][0]['text'], 'First -> second\n<b>text</b>')
        self.assertEqual(payload['items'][0]['title'], 'Reply & help')
        self.assertEqual(payload['items'][0]['externalId'], GUID)
        literal = browser_export_payload(export(text='&amp;gt;'))
        self.assertEqual(literal['items'][0]['text'], '&gt;')
        self.assertFalse(browser_export_payload(export(), apply=True)['dryRun'])

    def test_incomplete_wrong_account_or_duplicate_exports_fail_whole_package(self):
        complete = export()
        invalid = [None, {}, {**complete, 'workspace': '2682-1109'},
                   {**complete, 'total': 64}, {**complete, 'total': True},
                   {**complete, 'total': 2, 'data': complete['data'] * 2},
                   export(guid='invalid'), export(text=None), export(files=None)]
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(ValueError):
                browser_export_payload(document, apply=True)

    def test_attachment_only_export_keeps_attachment_metadata_for_unsupported_marker(self):
        payload = browser_export_payload(export(text='', files=[dict(name='example.pdf')]))
        self.assertEqual(payload['items'][0]['files'], [dict(name='example.pdf')])


if __name__ == '__main__':
    unittest.main()
