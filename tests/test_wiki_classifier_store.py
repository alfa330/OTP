# -*- coding: utf-8 -*-
"""Сверка «Классификатора авто» с Яндекс Про (wiki/classifier_store.py) — без сети и базы.

Сеть — подделка: настоящие урезанные страницы tests/data/classifier/*.json,
обёрнутые в <script id="__NEXT_DATA__">. Запись в базу подменена — здесь
проверяется порядок решений сверки: что пишется, что нет и почему. SQL на
настоящем Postgres — tests/test_wiki_classifier_store_postgres.py.
"""

import contextlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from wiki import classifier_source as source
from wiki import classifier_store as store
from wiki import yandex_sync

DATA = Path(__file__).resolve().parent / 'data' / 'classifier'
NOW = datetime(2026, 11, 16, 5, 40, tzinfo=ZoneInfo('Asia/Almaty'))
FIXTURES = {code: (DATA / ('%s.json' % code)).read_text(encoding='utf-8').strip()
            for code in ('almaty', 'aktau', 'uralsk', 'semey', 'aksay')}


def page(code):
    """HTML страницы города: своя фикстура, а у прочих городов — страница Аксая."""
    state = FIXTURES.get(code, FIXTURES['aksay'])
    return '<html><script id="__NEXT_DATA__" type="application/json">%s</script></html>' % state


def code_of(url):
    return url.split('/kz-ru/')[1].split('/')[0]


class FakeDb:
    def __init__(self):
        self.cursor = MagicMock()

    @contextlib.contextmanager
    def _get_cursor(self):
        yield self.cursor


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.sleeps = []
        self.patches = [
            patch.object(store, 'load_snapshot', return_value=None),
            patch.object(store, 'store', return_value='changed'),
            patch.object(store, 'mark_error'),
        ]
        self.load, self.store, self.mark = [item.start() for item in self.patches]

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def sync(self, fetch, **extra):
        return store.sync(self.db, fetch=fetch, sleep=self.sleeps.append, now=NOW, **extra)

    def test_all_cities_are_fetched_and_stored(self):
        asked = []

        def fetch(url):
            asked.append(code_of(url))
            return page(code_of(url))

        changed = MagicMock()
        summary = self.sync(fetch, on_change=changed)
        self.assertEqual(summary['status'], 'changed')
        self.assertEqual(summary['cities'], 25)
        self.assertEqual(asked[0], 'almaty')                 # список городов — со страницы Алматы
        self.assertEqual(len(asked), 25)
        self.assertEqual(self.sleeps.count(store.PAUSE_SECONDS), 24)
        data = self.store.call_args[0][1]
        self.assertEqual(data['version'], source.VERSION)
        self.assertEqual(data['source']['checked_at'], '2026-11-16T05:40:00+05:00')
        self.assertEqual({city['id'] for city in data['cities']} & {'astana', 'rudny'},
                         {'astana', 'rudny'})
        changed.assert_called_once_with()
        self.mark.assert_not_called()

    def test_unchanged_source_does_not_reset_the_book(self):
        self.store.return_value = 'same'
        changed = MagicMock()
        self.assertEqual(self.sync(lambda url: page(code_of(url)), on_change=changed)['status'], 'same')
        changed.assert_not_called()

    def test_a_city_that_did_not_open_keeps_the_old_book(self):
        def fetch(url):
            if code_of(url) == 'kostanay':
                raise yandex_sync.SyncError('Источник ответил 502')
            return page(code_of(url))

        summary = self.sync(fetch)
        self.assertEqual(summary['status'], 'error')
        self.assertIn('kostanay', summary['error'])
        self.store.assert_not_called()
        self.assertIn('kostanay', self.mark.call_args[0][1])

    def test_gone_city_is_dropped_with_a_note(self):
        def fetch(url):
            if code_of(url) == 'kentau':
                raise yandex_sync.SyncError('Страницы по этому адресу в базе знаний нет (404)')
            return page(code_of(url))

        summary = self.sync(fetch)
        self.assertEqual((summary['status'], summary['cities']), ('changed', 24))
        warnings = self.store.call_args[1]['warnings']
        self.assertTrue([line for line in warnings if 'kentau' in line and '404' in line])

    def test_almaty_failure_is_an_error(self):
        def fetch(url):
            raise yandex_sync.SyncError('Страница не открылась: timeout')

        summary = self.sync(fetch)
        self.assertEqual(summary['status'], 'error')
        self.assertTrue(summary['error'].startswith('Страница Алматы не открылась'))
        self.store.assert_not_called()

    def test_any_exception_of_a_substituted_loader_is_an_almaty_failure(self):
        def fetch(url):
            raise RuntimeError('подменённый загрузчик')

        self.assertTrue(self.sync(fetch)['error'].startswith('Страница Алматы не открылась'))

    def test_page_without_state_is_retried_once(self):
        tries = {}

        def fetch(url):
            code = code_of(url)
            tries[code] = tries.get(code, 0) + 1
            if code == 'semey' and tries[code] == 1:
                return '<html>частые обращения</html>'
            return page(code)

        self.assertEqual(self.sync(fetch)['status'], 'changed')
        self.assertEqual(tries['semey'], 2)
        self.assertIn(store.RETRY_PAUSE, self.sleeps)

    def test_suspicious_shrink_is_refused(self):
        good, _warnings, _failed = store.build_now(fetch=lambda url: page(code_of(url)),
                                                   sleep=lambda _s: None, now=NOW)
        bigger = json.loads(json.dumps(good))
        for table in bigger['lists']:
            table['rows'] = table['rows'] * 3            # было втрое больше строк
        self.load.return_value = {'data': bigger}
        summary = self.sync(lambda url: page(code_of(url)))
        self.assertEqual(summary['status'], 'error')
        self.store.assert_not_called()
        self.assertIn('подозрительно уменьшился', self.mark.call_args[0][1])

    def test_parser_bug_is_not_called_a_network_failure(self):
        with patch.object(source, 'build', side_effect=KeyError('cities')), \
                self.assertLogs(level='ERROR'):
            summary = self.sync(lambda url: page(code_of(url)))
        self.assertTrue(summary['error'].startswith('Справочник не собрался'))


class HelpersTest(unittest.TestCase):
    def test_shrink_reason(self):
        old = {'cities': [{}] * 25, 'lists': [{'rows': [[0, 1]] * 100}]}
        self.assertIsNone(store.shrink_reason(old, None))
        self.assertIsNone(store.shrink_reason({'cities': [{}] * 25, 'lists': [{'rows': [[0, 1]] * 70}]}, old))
        self.assertIn('строк', store.shrink_reason({'cities': [{}] * 25, 'lists': [{'rows': [[0, 1]] * 50}]}, old))
        self.assertIn('городов', store.shrink_reason({'cities': [{}] * 10, 'lists': old['lists']}, old))

    def test_bundled_file_round_trip(self):
        data = store.read_bundled()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'nested' / 'classifier-data.json'
            store.write_bundled(data, str(target))
            self.assertEqual(store.read_bundled(str(target)), data)
            self.assertTrue(target.read_text(encoding='utf-8').endswith('}\n'))

    def test_current_data_without_a_snapshot_is_the_file(self):
        data, origin = store.current_data(None)
        self.assertEqual((origin, data['version']), ('file', source.VERSION))
        with patch.object(store, 'load_snapshot', return_value=None):
            self.assertEqual(store.current_data(MagicMock())[1], 'file')
        with patch.object(store, 'load_snapshot', return_value={'data': {'version': 2, 'cities': []}}):
            self.assertEqual(store.current_data(MagicMock()), ({'version': 2, 'cities': []}, 'snapshot'))

    def test_404_is_gone_other_errors_are_not(self):
        with patch.object(yandex_sync, 'fetch_page',
                          side_effect=yandex_sync.SyncError('Страницы по этому адресу в базе знаний нет (404)')):
            with self.assertRaises(store.FetchError) as caught:
                store.fetch_page('https://pro.yandex.com/kz-ru/kentau/x')
        self.assertTrue(caught.exception.gone)
        with patch.object(yandex_sync, 'fetch_page', side_effect=yandex_sync.SyncError('Источник ответил 502')):
            with self.assertRaises(store.FetchError) as caught:
                store.fetch_page('https://pro.yandex.com/kz-ru/kentau/x')
        self.assertFalse(caught.exception.gone)


class WiringTest(unittest.TestCase):
    """Сверка встроена в планировщик и в схему вики — сторожа текста монолита."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_daily_job_in_the_yandex_pool(self):
        monolith = (self.ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertIn("id='wiki_classifier_sync_daily'", monolith)
        self.assertIn('run_in_executor(yandex_pro_pool, wiki_classifier_sync_job)', monolith)
        job = monolith[monolith.index('def wiki_classifier_sync_job():'):]
        job = job[:job.index('async def run_wiki_classifier_sync_async')]
        # Догоняющий прогон после деплоя не перекачивает Яндекс, если сверка уже была.
        self.assertIn('wiki_classifier_store.is_due(cursor)', job)
        self.assertIn('on_change=wiki_classifier.invalidate', job)

    def test_snapshot_table_in_the_wiki_schema(self):
        from wiki import schema
        self.assertIn('CREATE TABLE IF NOT EXISTS wiki_classifier_snapshot',
                      schema._CLASSIFIER_SNAPSHOT_STATEMENTS[0])
        init = (self.ROOT / 'wiki' / 'schema.py').read_text(encoding='utf-8')
        self.assertIn('for statement in _CLASSIFIER_SNAPSHOT_STATEMENTS:', init)


if __name__ == '__main__':
    unittest.main()
