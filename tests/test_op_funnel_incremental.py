# -*- coding: utf-8 -*-
"""Инкремент сделок amoCRM «Воронки ОП»: откуда он продолжает (op_funnel.sync.sync_amo_changes).

Закреплено по разбору «Принятия лида в работу» на «Табло ОП» (02.10.2026): начало окна
инкремента считается только от прошлых ИНКРЕМЕНТОВ. Полный прогон перечитывает сделки своего
периода, а не всё изменившееся; догрузка прошлых дней из «Касаний» между двумя инкрементами
сдвигала начало окна вперёд, и сделка, заведённая в этом промежутке и больше не менявшаяся,
в снимок не попадала вовсе — а по снимку считает и табло.
"""

import os
import sys
import time
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_funnel import sync


class _Cursor:
    def __init__(self, lag):
        self.statements = []
        self.description = None
        self._lag = lag

    def execute(self, sql, args=None):
        self.statements.append((' '.join(sql.split()), args))
        self.description = [('lag',)]

    def fetchall(self):
        return [(self._lag,)]


class _Db:
    def __init__(self, lag):
        self.cursor = _Cursor(lag)

    def _get_cursor(self):
        @contextmanager
        def scope():
            yield self.cursor
        return scope()


class IncrementalWindowTests(unittest.TestCase):
    def run_sync(self, lag):
        db = _Db(lag)
        seen = {}

        def fetch(since_epoch):
            seen['since'] = since_epoch
            return [], {}, [], {}, {}

        with mock.patch.object(sync.queries, 'start_run', lambda *a, **k: 1), \
                mock.patch.object(sync.queries, 'finish_run', lambda *a, **k: None), \
                mock.patch.object(sync.sources, 'fetch_amo_changed_leads', fetch):
            summary = sync.sync_amo_changes(db)
        return db.cursor.statements, seen['since'], summary

    def test_window_starts_after_the_last_incremental_only(self):
        statements, since, summary = self.run_sync(600.0)
        sql, args = statements[0]
        self.assertIn("status = 'ok' AND note = %s", sql)
        self.assertEqual(args, ('op_osnova', 'amo', sync.INCREMENTAL_NOTE))
        # 10 минут с прошлого инкремента + 5 минут перекрытия.
        self.assertAlmostEqual(since, time.time() - 600 - 300, delta=5)
        self.assertEqual(summary['status'], 'ok')

    def test_without_incrementals_it_looks_back_a_day_at_most(self):
        _statements, since, _summary = self.run_sync(None)
        self.assertAlmostEqual(since, time.time() - 24 * 3600 - 300, delta=5)

    def test_the_run_is_marked_with_the_same_note_it_is_found_by(self):
        captured = {}

        def start_run(cursor, direction_code, source, day_from, day_to, started_by=None, note=''):
            captured['note'] = note
            return 1

        with mock.patch.object(sync.queries, 'start_run', start_run), \
                mock.patch.object(sync.queries, 'finish_run', lambda *a, **k: None), \
                mock.patch.object(sync.sources, 'fetch_amo_changed_leads', lambda since: ([], {}, [], {}, {})):
            sync.sync_amo_changes(_Db(60.0))
        self.assertEqual(captured['note'], sync.INCREMENTAL_NOTE)
        self.assertEqual(sync.INCREMENTAL_NOTE, 'incremental')   # по ней журнал прячет инкременты


class _FakeAmo:
    """amoCRM в памяти: сделки отдаются страницей, справочники и вход считаются."""

    created = 0

    def __init__(self, leads=None, fail=False):
        _FakeAmo.created += 1
        self.leads, self.fail, self.calls = list(leads or []), fail, []

    def get(self, path, params=None):
        self.calls.append(path)
        if self.fail:
            raise RuntimeError('amoCRM: обрыв')
        if path == '/api/v4/leads':
            return {'_embedded': {'leads': self.leads}}
        if path == '/api/v4/leads/pipelines':
            return {'_embedded': {'pipelines': [{'id': sync.sources.AMO_SALES_PIPELINE_ID, '_embedded': {
                'statuses': [{'id': 142, 'name': 'ПРОШЕЛ РЕГИСТРАЦИЮ'}, {'id': 7, 'name': 'Новая заявка'}]}}]}}
        if path == '/api/v4/leads/loss_reasons':
            return {'_embedded': {'loss_reasons': [{'id': 5, 'name': 'Дорого'}]}}
        if path == '/api/v4/users':
            return {'_embedded': {'users': []}}
        return {'_embedded': {}}


class IncrementalAmoLoadTests(unittest.TestCase):
    """С 02.10.2026 инкремент ходит раз в 3 минуты: один вход в amoCRM на срок токена, а не на
    прогон, и справочники раз в 15 минут — иначе amoCRM получал бы впятеро больше входов и
    справочников ради тех же сделок."""

    def setUp(self):
        sources = sync.sources
        self.sources = sources
        self.saved = dict(sources._incremental)
        sources._incremental.update(client=None, dictionaries=None, dictionaries_at=0.0)
        self.addCleanup(lambda: sources._incremental.update(self.saved))
        _FakeAmo.created = 0
        self.amo = _FakeAmo(leads=[{'id': 1, 'status_id': 7}])
        self.amo_created = 0
        import amocrm.leads as amo_leads

        def make_client():
            self.amo_created += 1
            return self.amo

        patcher = mock.patch.object(amo_leads, 'AmoClient', make_client)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Часы — только модуля источников: глобальный time.time() трогать незачем.
        self.clock = {'now': 1_000_000.0}
        fake_time = mock.Mock()
        fake_time.time = lambda: self.clock['now']
        patcher = mock.patch.object(sources, 'time', fake_time)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dictionary_calls(self):
        return self.amo.calls.count('/api/v4/leads/pipelines')

    def test_one_login_and_dictionaries_once_per_quarter_hour(self):
        for _ in range(5):                          # пять прогонов по три минуты
            leads, stages, _users, reasons, _phones = self.sources.fetch_amo_changed_leads(0)
            self.clock['now'] += 180
        self.assertEqual(self.amo_created, 1)
        self.assertEqual(self.dictionary_calls(), 1)
        self.assertEqual((stages[7], reasons[5]), ('Новая заявка', 'Дорого'))
        self.clock['now'] += 60                     # 16 минут с первого чтения
        self.sources.fetch_amo_changed_leads(0)
        self.assertEqual(self.dictionary_calls(), 2)

    def test_unknown_stage_or_reason_rereads_the_dictionaries_at_once(self):
        self.sources.fetch_amo_changed_leads(0)
        self.amo.leads = [{'id': 2, 'status_id': 99}]
        self.sources.fetch_amo_changed_leads(0)
        self.assertEqual(self.dictionary_calls(), 2)
        self.amo.leads = [{'id': 3, 'status_id': 7, 'loss_reason_id': 77}]
        self.sources.fetch_amo_changed_leads(0)
        self.assertEqual(self.dictionary_calls(), 3)

    def test_failure_drops_the_shared_client_and_the_next_run_logs_in_again(self):
        self.sources.fetch_amo_changed_leads(0)
        self.amo.fail = True
        with self.assertRaises(RuntimeError):
            self.sources.fetch_amo_changed_leads(0)
        self.assertIsNone(self.sources._incremental['client'])
        self.amo.fail = False
        self.sources.fetch_amo_changed_leads(0)
        self.assertEqual(self.amo_created, 2)

    def test_own_client_is_used_and_kept_out_of_the_cache(self):
        own = _FakeAmo(leads=[])
        self.sources.fetch_amo_changed_leads(0, client=own)
        self.assertIn('/api/v4/leads', own.calls)
        self.assertEqual(self.amo_created, 0)
        self.assertIsNone(self.sources._incremental['client'])


class IncrementalScheduleTests(unittest.TestCase):
    """Расписание — в монолите, поэтому по исходнику, как WiringTests отбивки."""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'bot_schedule2.py'), encoding='utf-8-sig') as fh:
            cls.source = fh.read()

    def test_deals_every_three_minutes_off_the_round_minutes(self):
        block = self.source[self.source.index('op_funnel_amo_incremental_job,\n'):]
        block = block[:block.index('coalesce=True')]
        self.assertIn("CronTrigger(minute='2-59/3', timezone=ZoneInfo('Asia/Almaty'))", block)
        self.assertIn("max_instances=1", block)

    def test_linking_stays_once_a_quarter_hour(self):
        job = self.source[self.source.index('async def op_funnel_amo_incremental_job'):]
        job = job[:job.index('\n\n\n')]
        self.assertIn('OP_FUNNEL_LINK_INTERVAL_SECONDS = 15 * 60', self.source)
        guard = job.index("if time.time() - _op_funnel_link_state['at'] < OP_FUNNEL_LINK_INTERVAL_SECONDS:")
        self.assertLess(job.index('sync_amo_changes(db)'), guard)
        self.assertLess(guard, job.index('_ai_qa_marketing_link_now(full=False)'))


if __name__ == '__main__':
    unittest.main()
