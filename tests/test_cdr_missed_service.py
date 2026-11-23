# -*- coding: utf-8 -*-
"""Робот «пропущенные входящие → amoCRM»: цикл разбора (задача #291).

Базы и amoCRM здесь нет: слой SQL робота (`cdr.missed_queries`) заменён двойником на
словарях с той же семантикой, писатель amoCRM — записывающим двойником.

Что закреплено:
  * первый цикл ставит отметку «с какого момента работаем» — накопленные до включения
    звонки сделок не получают;
  * строка `sending` появляется в журнале ДО запроса в amoCRM;
  * дозвонившийся клиент и перезвон в цепочке amoCRM не трогают вовсе — писатель даже
    не заводится;
  * итог последнего звонка цепочки раздаётся предыдущим: сделка или «дозвонился»;
  * наша сделка, ещё стоящая в «Новой заявке», — повод дописать примечание, а не
    заводить вторую; ушедшая дальше или удалённая — нет; amoCRM не ответила про неё —
    решение откладывается;
  * пока по номеру есть наша строка «в полёте» (`sending`/`error`), новую сделку тому же
    клиенту не заводим: по той строке сделка могла уже появиться;
  * сбой amoCRM — `error` и повтор; перед повтором и перед «не удалось» сначала ищем уже
    заведённую сделку; не смогли спросить — вслепую не повторяем;
  * повтор, пока строка ждала, находит нашу сделку по другому звонку — `repeat`, не вторую;
  * amoCRM не пускает при входе — звонок ложится `error` без попытки и уходит, когда вход
    заработает, а за горизонтом становится `failed` и виден в разделе.

Переадресация в Syntony (SALES-4, SyntonyCycleTests):
  * то же решение — та же отметка `sending` до запроса, затем заявка через форму;
  * проверки «у клиента уже есть наша сделка» нет: второй звонок того же клиента — второй
    запрос, открытую заявку Syntony дополняет сама; amoCRM не трогается вовсе;
  * сбой — `error` и повтор без «поиска потерянной», 400 — сразу `failed`;
  * не больше SYNTONY_POSTS_PER_CYCLE запросов за цикл, с повторами вместе;
  * строка amoCRM, упавшая до переезда, повторяется уже в Syntony;
  * при обоих флагах окружения робот идёт в Syntony, без ключа формы — выключен.

Телефоны учебные (7XX555XXXX).
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from cdr import missed_config, missed_service
from cdr import missed_queries as real_mq
from cdr import touches as T
from cdr.missed_amo import LeadReadError

NOW = datetime(2026, 9, 28, 12, 0, 0)
PHONE = '7015550001'


def missed_touch(linkedid, started, phone=PHONE, wait=40):
    return {
        'linkedid': linkedid, 'phone': phone, 'call_day': started.date(), 'started_at': started,
        'call_type': T.TYPE_IN_MISSED, 'talk_seconds': 0, 'dial_seconds': wait + 7,
        'queued_at': started + timedelta(seconds=7), 'wait_seconds': wait, 'hangup_side': '',
        'line_number': '7475550078', 'queue': '3010', 'result': T.RESULT_BUSY,
    }


def talk_touch(linkedid, started, phone=PHONE):
    return {
        'linkedid': linkedid, 'phone': phone, 'call_day': started.date(), 'started_at': started,
        'call_type': T.TYPE_IN, 'talk_seconds': 90, 'dial_seconds': 100, 'queued_at': None,
        'wait_seconds': None, 'hangup_side': 'client', 'line_number': '', 'queue': '3010',
        'result': T.RESULT_TALK,
    }


class FakeStore(object):
    """Двойник cdr.missed_queries на словарях — те же имена и та же семантика."""

    SENDING, CREATED, REPEAT, CHAINED, ANSWERED, ERROR, FAILED = (
        real_mq.SENDING, real_mq.CREATED, real_mq.REPEAT, real_mq.CHAINED, real_mq.ANSWERED,
        real_mq.ERROR, real_mq.FAILED)
    CHAIN_LOOKBACK = real_mq.CHAIN_LOOKBACK

    def __init__(self, touches=(), enabled_since=None):
        self.touches = list(touches)
        self.rows = {}
        self.enabled_since = enabled_since
        self.runs = []
        self.clock = 1790000000

    # состояние
    def ensure_state(self, cursor, now):
        if self.enabled_since is None:
            self.enabled_since = now
        return self.enabled_since

    def mark_run(self, cursor, error=None):
        self.runs.append(error)

    # выборки
    def candidates(self, cursor, since, limit=50):
        out = [dict(t) for t in self.touches
               if t['call_type'] == T.TYPE_IN_MISSED and t['started_at'] >= since
               and (t['linkedid'], t['phone']) not in self.rows]
        return sorted(out, key=lambda t: t['started_at'])[:limit]

    def phone_touches(self, cursor, phones, since):
        out = {}
        for t in self.touches:
            if t['phone'] in phones and t['started_at'] >= since:
                out.setdefault(t['phone'], []).append(dict(t))
        return out

    def due_retries(self, cursor, limit=20):
        """Все `error` (паузу двойник не выдерживает) и `sending`, помеченные зависшими."""
        out = []
        for (linkedid, phone), row in sorted(self.rows.items(), key=lambda kv: kv[1]['started_at']):
            if row['status'] == 'error' or (row['status'] == 'sending' and row.get('stale')):
                out.append({'linkedid': linkedid, 'phone': phone,
                            'call_day': row['started_at'].date(), 'started_at': row['started_at'],
                            'status': row['status'], 'attempts': row['attempts'],
                            'sent_epoch': row.get('sent_epoch'), 'line_number': '',
                            'queue': '3010', 'wait_seconds': 40, 'crm': row.get('crm'),
                            'error': row.get('error')})
        return out[:limit]

    # запись
    def insert_decision(self, cursor, touch, status, reason, ended_at=None, next_linkedid=None,
                        amo_lead_id=None, error=None, crm=None):
        assert status in real_mq.STATUSES
        assert crm in (None,) + real_mq.CRMS
        key = (touch['linkedid'], touch['phone'])
        if key in self.rows:
            return False
        self.clock += 10
        self.rows[key] = {'status': status, 'reason': reason, 'next': next_linkedid,
                          'amo_lead_id': amo_lead_id, 'started_at': touch['started_at'],
                          'phone': touch['phone'], 'attempts': 1 if status == 'sending' else 0,
                          'sent_epoch': self.clock if status == 'sending' else None,
                          'error': error, 'crm': crm, 'syntony_sent': False}
        return True

    def mark_sent_syntony(self, cursor, linkedid, phone):
        self.rows[(linkedid, phone)].update(status='created', crm='syntony', syntony_sent=True,
                                            error=None)

    def mark_created(self, cursor, linkedid, phone, lead_id, contact_id=None, reason=None):
        self.rows[(linkedid, phone)].update(status='created', amo_lead_id=lead_id, error=None)

    def mark_error(self, cursor, linkedid, phone, error, final=False):
        self.rows[(linkedid, phone)].update(status='failed' if final else 'error',
                                            error=str(error), stale=False)

    def mark_repeat(self, cursor, linkedid, phone, lead_id, reason):
        self.rows[(linkedid, phone)].update(status='repeat', amo_lead_id=lead_id, reason=reason,
                                            error=None)

    def reclaim(self, cursor, linkedid, phone, status, attempts, crm=None):
        row = self.rows.get((linkedid, phone))
        if not row or row['status'] != status or row['attempts'] != int(attempts or 0):
            return None
        self.clock += 10
        row.update(status='sending', attempts=row['attempts'] + 1, sent_epoch=self.clock,
                   stale=False, crm=crm or row.get('crm'))
        return row['attempts']

    def pending_rows(self, cursor, phone, since, linkedid, before):
        return sum(1 for (lid, ph), row in self.rows.items()
                   if ph == phone and lid != linkedid and row['status'] in ('sending', 'error')
                   and since <= row['started_at'] < before)

    def latest_own_lead(self, cursor, phone, since):
        own = [r for r in self.rows.values() if r['phone'] == phone and r['status'] == 'created'
               and r['amo_lead_id'] and r['started_at'] >= since]
        if not own:
            return None
        best = max(own, key=lambda r: r['started_at'])
        return best['amo_lead_id'], best['started_at']

    def resolve_chain(self, cursor, phone, before, lead_id=None, answered_reason=None,
                      syntony=False):
        for row in self.rows.values():
            if (row['phone'] == phone and row['status'] == 'chained' and not row['amo_lead_id']
                    and not row.get('syntony_sent')
                    and before - self.CHAIN_LOOKBACK <= row['started_at'] < before):
                if lead_id:
                    row['amo_lead_id'] = lead_id
                elif syntony:
                    row.update(syntony_sent=True, crm='syntony')
                elif answered_reason:
                    row.update(status='answered', reason=answered_reason)


class FakeQueries(object):
    def __init__(self, live_at, queue_calls=None, queue_at=None):
        self.live_at = live_at
        self.queue_calls = queue_calls
        self.queue_at = queue_at

    def now_almaty(self):
        return NOW

    def agent_state(self, cursor):
        return {'live_at': self.live_at}

    def load_queue_calls(self, cursor):
        return self.queue_calls, self.queue_at


ENTRY_STAGE = (missed_config.PIPELINE_ID, missed_config.STATUS_ID)


class FakeWriter(object):
    def __init__(self, store, lead_states=None):
        self.store = store
        self.created = []
        self.notes = []
        self.lead_states = lead_states or {}
        self.fail_create = None
        self.recent = None
        self.recent_fails = False
        self.recent_calls = []
        self.next_id = 57100000
        self.rows_at_create = []

    def find_contact_id(self, phone):
        return 75000001

    def create_missed_lead(self, phone, contact_id=None):
        # Что лежало в журнале в момент запроса: отметка обязана появиться ДО него.
        self.rows_at_create.append({k: dict(v) for k, v in self.store.rows.items()})
        if self.fail_create:
            raise self.fail_create
        self.next_id += 1
        self.created.append((phone, contact_id, self.next_id))
        # Заведённая сделка в amoCRM сразу стоит в «Новой заявке».
        self.lead_states.setdefault(self.next_id, ENTRY_STAGE)
        return self.next_id, contact_id

    def lead_state(self, lead_id):
        state = self.lead_states.get(lead_id)
        if isinstance(state, Exception):
            raise state
        return state

    def add_note(self, lead_id, text):
        self.notes.append((lead_id, text))
        return True

    def recent_own_lead(self, phone, since_epoch):
        self.recent_calls.append(since_epoch)
        if self.recent_fails:
            raise RuntimeError('amoCRM молчит')
        return self.recent


class _Db(object):
    def _get_cursor(self):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            yield None
        return scope()


class CycleTests(unittest.TestCase):
    def run_cycle(self, store, queries=None, writer=None, factory=None, now=NOW):
        queries = queries or FakeQueries(live_at=(now - timedelta(seconds=5)).isoformat())
        self.writer = writer or getattr(self, 'writer', None) or FakeWriter(store)
        self.factory_calls = 0

        def default_factory():
            self.factory_calls += 1
            return self.writer

        with mock.patch.object(missed_service, 'mq', store), \
                mock.patch.object(missed_service, 'queries', queries):
            return missed_service.run_cycle(_Db(), now=now, writer_factory=factory or default_factory)

    def setUp(self):
        self.writer = None

    def since(self):
        return NOW - timedelta(hours=1)

    def row(self, store, linkedid='1.1'):
        return store.rows[(linkedid, PHONE)]

    # ── включение ────────────────────────────────────────────────────────────

    def test_first_cycle_marks_the_start_and_ignores_older_calls(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=10))])
        summary = self.run_cycle(store)
        self.assertEqual(store.enabled_since, NOW)
        self.assertFalse(summary)
        self.assertEqual(store.rows, {})

    def test_calls_older_than_the_horizon_are_left_alone(self):
        old = NOW - timedelta(hours=missed_service.HORIZON_HOURS, minutes=5)
        store = FakeStore([missed_touch('1.1', old)], enabled_since=NOW - timedelta(days=2))
        self.run_cycle(store)
        self.assertEqual(store.rows, {})

    # ── передача ─────────────────────────────────────────────────────────────

    def test_nobody_answered_creates_a_lead_with_the_mark_written_first(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        summary = self.run_cycle(store)
        self.assertEqual(summary['created'], 1)
        # В момент запроса строка уже была — и именно `sending`.
        self.assertEqual(self.writer.rows_at_create[0][('1.1', PHONE)]['status'], 'sending')
        row = self.row(store)
        self.assertEqual(row['status'], 'created')
        self.assertEqual(row['amo_lead_id'], self.writer.created[0][2])
        self.assertEqual(self.writer.created[0][1], 75000001)   # привязан найденный контакт
        self.assertEqual(len(self.writer.notes), 1)
        self.assertIn('Пропущенный входящий звонок', self.writer.notes[0][1])

    def test_still_within_the_minute_nothing_happens(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(seconds=60))],
                          enabled_since=self.since())
        summary = self.run_cycle(store)
        self.assertFalse(summary)
        self.assertEqual(store.rows, {})
        self.assertEqual(self.factory_calls, 0)

    def test_second_cycle_does_not_create_a_second_lead(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        self.run_cycle(store)
        self.run_cycle(store)
        self.assertEqual(len(self.writer.created), 1)

    def test_answered_redial_writes_answered_without_touching_amo(self):
        start = NOW - timedelta(minutes=5)
        store = FakeStore([missed_touch('1.1', start),
                           talk_touch('1.2', start + timedelta(seconds=70))],
                          enabled_since=self.since())
        summary = self.run_cycle(store)
        self.assertEqual(summary['answered'], 1)
        self.assertEqual(self.row(store)['status'], 'answered')
        self.assertEqual(self.factory_calls, 0)

    def test_chain_gets_the_lead_of_its_last_call(self):
        start = NOW - timedelta(minutes=5)
        store = FakeStore([missed_touch('1.1', start),
                           missed_touch('1.2', start + timedelta(seconds=80))],
                          enabled_since=self.since())
        summary = self.run_cycle(store)
        self.assertEqual((summary['chained'], summary['created']), (1, 1))
        self.assertEqual(len(self.writer.created), 1)
        first, last = self.row(store, '1.1'), self.row(store, '1.2')
        self.assertEqual(first['status'], 'chained')
        self.assertEqual(first['amo_lead_id'], last['amo_lead_id'])

    def test_chain_that_ends_with_a_conversation_is_closed_as_answered(self):
        start = NOW - timedelta(minutes=5)
        store = FakeStore([missed_touch('1.1', start),
                           missed_touch('1.2', start + timedelta(seconds=80)),
                           talk_touch('1.3', start + timedelta(seconds=170))],
                          enabled_since=self.since())
        self.run_cycle(store)
        self.assertEqual(self.row(store, '1.1')['status'], 'answered')
        self.assertEqual(self.row(store, '1.2')['status'], 'answered')
        self.assertEqual(self.factory_calls, 0)

    # ── наша сделка уже есть ────────────────────────────────────────────────

    def seeded_store(self, first_lead=57000001, status='created'):
        store = FakeStore([missed_touch('1.2', NOW - timedelta(minutes=5))],
                          enabled_since=NOW - timedelta(hours=2))
        store.rows[('1.1', PHONE)] = {
            'status': status, 'amo_lead_id': first_lead if status == 'created' else None,
            'reason': '', 'next': None, 'started_at': NOW - timedelta(minutes=40), 'phone': PHONE,
            'attempts': 1, 'sent_epoch': 1789990000, 'error': None}
        return store

    def test_open_own_lead_in_the_entry_stage_gets_a_note_not_a_twin(self):
        store = self.seeded_store()
        writer = FakeWriter(store, lead_states={57000001: ENTRY_STAGE})
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['repeat'], 1)
        self.assertEqual(writer.created, [])
        row = self.row(store, '1.2')
        self.assertEqual((row['status'], row['amo_lead_id']), ('repeat', 57000001))
        self.assertEqual(writer.notes[0][0], 57000001)
        self.assertTrue(writer.notes[0][1].startswith('Повторный'))

    def test_own_lead_already_taken_into_work_does_not_stop_a_new_one(self):
        store = self.seeded_store()
        writer = FakeWriter(store, lead_states={
            57000001: (missed_config.PIPELINE_ID, 48846280)})   # «Принято в работу»
        self.assertEqual(self.run_cycle(store, writer=writer)['created'], 1)

    def test_deleted_own_lead_does_not_stop_a_new_one(self):
        store = self.seeded_store()
        writer = FakeWriter(store, lead_states={})               # lead_state → None
        self.assertEqual(self.run_cycle(store, writer=writer)['created'], 1)

    def test_amo_not_answering_about_our_lead_defers_instead_of_a_twin(self):
        store = self.seeded_store()
        writer = FakeWriter(store, lead_states={57000001: LeadReadError('429')})
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual((summary['deferred'], summary['created']), (1, 0))
        self.assertNotIn(('1.2', PHONE), store.rows, 'решения нет — звонок ждёт цикла')
        writer.lead_states[57000001] = ENTRY_STAGE
        self.assertEqual(self.run_cycle(store, writer=writer)['repeat'], 1)

    def test_in_flight_row_of_the_same_client_holds_a_new_lead(self):
        # По первому звонку запрос ушёл, ответ потерялся: строка `error`, а сделка в amoCRM
        # может уже быть. Второй пропущенный того же клиента новую не заводит, пока повтор
        # первого не выяснил, что там.
        store = self.seeded_store(status='error')
        store.rows[('1.1', PHONE)]['stale'] = False
        writer = FakeWriter(store)
        writer.recent_fails = True          # повтор первого пока не может спросить amoCRM
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['deferred'], 1)
        self.assertEqual(writer.created, [])
        # amoCRM ответила: сделка по первому звонку есть — второй становится повтором.
        writer.recent_fails = False
        writer.recent = 57200001
        writer.lead_states[57200001] = ENTRY_STAGE
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual((summary['recovered'], summary['repeat']), (1, 1))
        self.assertEqual(writer.created, [])
        self.assertEqual(self.row(store, '1.2')['amo_lead_id'], 57200001)

    # ── сбои ─────────────────────────────────────────────────────────────────

    def test_amo_failure_leaves_error_and_the_retry_creates_the_lead(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        writer = FakeWriter(store)
        writer.fail_create = RuntimeError('amoCRM отклонила запись (500)')
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(self.row(store)['status'], 'error')
        self.assertTrue(store.runs and store.runs[-1])   # ошибка записана в состояние

        writer.fail_create = None
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(self.row(store)['status'], 'created')
        self.assertEqual(self.row(store)['attempts'], 2)
        # Перед повтором спросили amoCRM — не заведена ли сделка первой попыткой.
        self.assertEqual(len(writer.recent_calls), 1)

    def test_retry_finds_our_lead_made_by_another_call_meanwhile(self):
        # Строка ждала повтора, а клиенту тем временем завели нашу сделку по другому звонку.
        store = self.seeded_store()
        store.touches = []
        store.rows[('1.2', PHONE)] = {
            'status': 'error', 'amo_lead_id': None, 'reason': '', 'next': None,
            'started_at': NOW - timedelta(minutes=10), 'phone': PHONE, 'attempts': 1,
            'sent_epoch': 1790000100, 'error': 'amoCRM 502'}
        writer = FakeWriter(store, lead_states={57000001: ENTRY_STAGE})
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['repeat'], 1)
        self.assertEqual(writer.created, [])
        self.assertEqual((self.row(store, '1.2')['status'], self.row(store, '1.2')['amo_lead_id']),
                         ('repeat', 57000001))

    def test_two_waiting_rows_of_one_client_do_not_block_each_other(self):
        # amoCRM не пускала, и оба пропущенных одного клиента легли ошибками без попытки.
        # Вход заработал: первый заводит сделку, второй становится её повтором — а не оба
        # ждут друг друга до горизонта и уходят в «не передан».
        store = FakeStore(enabled_since=self.since())
        for linkedid, minutes in (('1.1', 30), ('1.2', 20)):
            store.rows[(linkedid, PHONE)] = {
                'status': 'error', 'amo_lead_id': None, 'reason': '', 'next': None,
                'started_at': NOW - timedelta(minutes=minutes), 'phone': PHONE, 'attempts': 0,
                'sent_epoch': None, 'error': 'amoCRM недоступна'}
        writer = FakeWriter(store)
        self.run_cycle(store, writer=writer)
        self.run_cycle(store, writer=writer)
        self.assertEqual(len(writer.created), 1)
        self.assertEqual(self.row(store, '1.1')['status'], 'created')
        self.assertEqual(self.row(store, '1.2')['status'], 'repeat')
        self.assertEqual(self.row(store, '1.2')['amo_lead_id'], writer.created[0][2])

    def stuck_store(self, **over):
        store = FakeStore(enabled_since=self.since())
        row = {'status': 'sending', 'amo_lead_id': None, 'reason': '', 'next': None,
               'started_at': NOW - timedelta(minutes=20), 'phone': PHONE, 'attempts': 1,
               'sent_epoch': 1790000000, 'error': None, 'stale': True}
        row.update(over)
        store.rows[('1.1', PHONE)] = row
        return store

    def test_stuck_sending_finds_the_lead_that_was_created_after_all(self):
        store = self.stuck_store()
        writer = FakeWriter(store)
        writer.recent = 57200001
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['recovered'], 1)
        self.assertEqual(writer.created, [])
        self.assertEqual(self.row(store)['amo_lead_id'], 57200001)
        self.assertEqual(writer.recent_calls, [1790000000 - 120])

    def test_no_blind_retry_when_amo_cannot_be_asked(self):
        store = self.stuck_store()
        writer = FakeWriter(store)
        writer.recent_fails = True
        self.run_cycle(store, writer=writer)
        self.assertEqual(writer.created, [])
        self.assertEqual(self.row(store)['status'], 'sending')

    def test_attempts_run_out_into_failed(self):
        store = self.stuck_store(attempts=missed_service.MAX_ATTEMPTS)
        summary = self.run_cycle(store)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(self.row(store)['status'], 'failed')

    def test_last_attempt_that_did_create_the_lead_is_not_called_failed(self):
        # Пятая попытка завела сделку, ответ потерялся. «Перезвоните вручную» здесь было бы
        # враньём: сделка есть, и клиенту перезвонили бы дважды.
        store = self.stuck_store(attempts=missed_service.MAX_ATTEMPTS)
        writer = FakeWriter(store)
        writer.recent = 57200002
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual((summary['recovered'], summary['failed']), (1, 0))
        self.assertEqual(self.row(store)['status'], 'created')

    def test_syntony_row_without_an_answer_is_not_sent_to_amo_after_rollback(self):
        # Робот вернули в amoCRM, а строка ушла в Syntony без ответа: заявка там могла
        # появиться. Сделка amoCRM поверх неё — два перезвона; строка видна в разделе.
        for status, error in (('sending', None), ('error', 'Syntony не ответила вовремя'),
                              ('error', 'Syntony: сбой запроса (ConnectionError)')):
            with self.subTest(status=status, error=error):
                store = self.stuck_store(status=status, stale=status == 'sending', crm='syntony',
                                         error=error)
                writer = FakeWriter(store)
                summary = self.run_cycle(store, writer=writer)
                self.assertEqual(summary['failed'], 1)
                self.assertEqual(writer.created, [])
                self.assertEqual(writer.recent_calls, [])
                row = self.row(store)
                self.assertEqual(row['status'], 'failed')
                self.assertEqual(row['error'], missed_service.SYNTONY_UNKNOWN_TEXT)

    def test_syntony_row_rejected_with_a_code_goes_to_amo(self):
        store = self.stuck_store(status='error', stale=False, crm='syntony',
                                 error='форма Syntony не найдена — ключ формы сменили? '
                                       '(HTTP 404 unknown_form)')
        writer = FakeWriter(store)
        summary = self.run_cycle(store, writer=writer)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(self.row(store)['crm'], 'amo')

    def test_reclaim_compares_status_and_attempts(self):
        store = self.stuck_store(status='error', stale=False)
        self.assertIsNone(store.reclaim(None, '1.1', PHONE, 'error', 0), 'чужое число попыток')
        self.assertEqual(store.reclaim(None, '1.1', PHONE, 'error', 1), 2)
        self.assertIsNone(store.reclaim(None, '1.1', PHONE, 'error', 1), 'второй раз не берётся')

    def test_amo_login_failure_records_the_call_and_sends_it_later(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())

        def broken():
            raise RuntimeError('amoCRM: вход вернул 503')
        self.run_cycle(store, factory=broken)
        row = self.row(store)
        self.assertEqual((row['status'], row['sent_epoch']), ('error', None))
        self.assertIn('вход вернул 503', store.runs[-1])
        # amoCRM ожила — звонок уходит следующим циклом, и без поиска «потерянной» сделки:
        # запроса не было, а поиск от начала эпохи нашёл бы любую нашу старую.
        summary = self.run_cycle(store)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(self.writer.recent_calls, [])

    def test_login_failing_past_the_horizon_ends_in_failed(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())

        def broken():
            raise RuntimeError('amoCRM: вход вернул 503')
        self.run_cycle(store, factory=broken)
        later = NOW + timedelta(hours=missed_service.HORIZON_HOURS)
        self.run_cycle(store, factory=broken, now=later,
                       queries=FakeQueries(live_at=later.isoformat()))
        self.assertEqual(self.row(store)['status'], 'failed')

    def test_stale_bridge_holds_the_decision(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=3))],
                          enabled_since=self.since())
        stale = FakeQueries(live_at=(NOW - timedelta(minutes=3)).isoformat())
        self.assertFalse(self.run_cycle(store, queries=stale))
        self.assertEqual(store.rows, {})

    def test_live_queue_call_of_the_same_phone_counts_as_answered(self):
        start = NOW - timedelta(minutes=3)
        store = FakeStore([missed_touch('1.1', start)], enabled_since=self.since())
        redial = start + timedelta(seconds=80)
        stamp = lambda moment: moment.strftime('%Y-%m-%d %H:%M:%S')  # noqa: E731
        queue = FakeQueries(
            live_at=(NOW - timedelta(seconds=5)).isoformat(),
            queue_calls=[{'linkedid': '9.9', 'phone': PHONE, 'queued_at': stamp(redial),
                          'answered_at': stamp(redial + timedelta(seconds=4)), 'ended': False},
                         {'linkedid': '9.8', 'phone': '7015550002', 'queued_at': stamp(redial),
                          'answered_at': '', 'ended': False}],
            queue_at=(NOW - timedelta(seconds=5)).isoformat())
        self.assertEqual(self.run_cycle(store, queries=queue)['answered'], 1)

    def test_transfers_per_cycle_are_capped(self):
        start = NOW - timedelta(minutes=30)
        calls = [missed_touch('1.%d' % i, start + timedelta(seconds=i), phone='70155500%02d' % i)
                 for i in range(missed_service.MAX_TRANSFERS_PER_CYCLE + 3)]
        store = FakeStore(calls, enabled_since=self.since())
        self.assertEqual(self.run_cycle(store)['created'], missed_service.MAX_TRANSFERS_PER_CYCLE)
        self.assertEqual(self.run_cycle(store)['created'], 3)


class FakeSyntonyWriter(object):
    """Двойник SyntonyMissedWriter: запоминает отправки и что лежало в журнале в тот момент."""

    def __init__(self, store):
        self.store = store
        self.sent = []
        self.rows_at_send = []
        self.fail = None

    def send(self, touch, park=''):
        self.rows_at_send.append({k: dict(v) for k, v in self.store.rows.items()})
        if self.fail:
            raise self.fail
        self.sent.append((touch['linkedid'], touch['phone'], park))


class SyntonyCycleTests(unittest.TestCase):
    """Тот же цикл с переадресацией в Syntony (SALES-4). Заготовки — общие с CycleTests."""

    since = CycleTests.since
    row = CycleTests.row
    seeded_store = CycleTests.seeded_store
    stuck_store = CycleTests.stuck_store

    def run_syntony(self, store, writer=None, factory=None, now=NOW, queries=None,
                    amo_check=None):
        queries = queries or FakeQueries(live_at=(now - timedelta(seconds=5)).isoformat())
        self.syntony = writer or getattr(self, 'syntony', None) or FakeSyntonyWriter(store)
        self.amo = getattr(self, 'amo', None) or FakeWriter(store)
        self.factory_calls = 0

        def default_factory():
            self.factory_calls += 1
            return self.syntony

        with mock.patch.object(missed_service, 'mq', store), \
                mock.patch.object(missed_service, 'queries', queries):
            return missed_service.run_cycle(_Db(), now=now, writer_factory=factory or default_factory,
                                            crm=missed_config.SYNTONY,
                                            amo_check_factory=amo_check or (lambda: self.amo))

    def setUp(self):
        self.syntony = None
        self.amo = None

    def test_missed_call_goes_to_syntony_with_the_mark_written_first(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        summary = self.run_syntony(store)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(self.syntony.sent, [('1.1', PHONE, 'Центр регистрации')])
        before = self.syntony.rows_at_send[0][('1.1', PHONE)]
        self.assertEqual((before['status'], before['crm']), ('sending', 'syntony'))
        row = self.row(store)
        self.assertEqual((row['status'], row['crm'], row['syntony_sent'], row['amo_lead_id']),
                         ('created', 'syntony', True, None))

    def test_answered_redial_records_syntony_and_sends_nothing(self):
        start = NOW - timedelta(minutes=5)
        store = FakeStore([missed_touch('1.1', start),
                           talk_touch('1.2', start + timedelta(seconds=70))],
                          enabled_since=self.since())
        summary = self.run_syntony(store)
        self.assertEqual(summary['answered'], 1)
        self.assertEqual((self.row(store)['status'], self.row(store)['crm']),
                         ('answered', 'syntony'))
        self.assertEqual(self.factory_calls, 0)

    def test_chain_gets_the_syntony_mark_of_its_last_call(self):
        start = NOW - timedelta(minutes=5)
        store = FakeStore([missed_touch('1.1', start),
                           missed_touch('1.2', start + timedelta(seconds=80))],
                          enabled_since=self.since())
        summary = self.run_syntony(store)
        self.assertEqual((summary['chained'], summary['created']), (1, 1))
        self.assertEqual([item[0] for item in self.syntony.sent], ['1.2'])
        first = self.row(store, '1.1')
        self.assertEqual((first['status'], first['syntony_sent'], first['crm']),
                         ('chained', True, 'syntony'))

    def test_second_call_of_the_same_client_is_sent_again_not_held(self):
        # В amoCRM вторая сделка не заводилась, пока первая в «Новой заявке». Syntony сама
        # кладёт второе обращение комментарием в открытую заявку — поэтому просто отправляем.
        store = self.seeded_store()
        store.rows[('1.1', PHONE)].update(status='created', amo_lead_id=None, crm='syntony',
                                          syntony_sent=True)
        summary = self.run_syntony(store)
        self.assertEqual((summary['created'], summary['repeat'], summary['deferred']), (1, 0, 0))
        self.assertEqual([item[0] for item in self.syntony.sent], ['1.2'])

    def test_amo_is_never_asked_in_syntony_mode(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        writer = FakeSyntonyWriter(store)
        with mock.patch.object(missed_service, '_writer',
                               side_effect=AssertionError('amoCRM не должна заводиться')), \
                mock.patch.object(missed_service, '_syntony_writer', return_value=writer), \
                mock.patch.object(missed_service, 'mq', store), \
                mock.patch.object(missed_service, 'queries',
                                  FakeQueries(live_at=(NOW - timedelta(seconds=5)).isoformat())):
            summary = missed_service.run_cycle(_Db(), now=NOW, crm=missed_config.SYNTONY)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(len(writer.sent), 1)

    def test_failure_is_retried_without_looking_for_a_lost_lead(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        writer = FakeSyntonyWriter(store)
        writer.fail = missed_service_syntony_error('больше 30 заявок в минуту (HTTP 429)')
        summary = self.run_syntony(store, writer=writer)
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(self.row(store)['status'], 'error')
        self.assertIn('429', store.runs[-1])

        writer.fail = None
        summary = self.run_syntony(store, writer=writer)
        self.assertEqual(summary['created'], 1)
        row = self.row(store)
        self.assertEqual((row['status'], row['attempts'], row['syntony_sent']), ('created', 2, True))

    def test_rejected_request_is_failed_at_once(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())
        writer = FakeSyntonyWriter(store)
        writer.fail = missed_service_syntony_error('Syntony отклонила заявку (HTTP 400 '
                                                   'phone_required)', final=True)
        summary = self.run_syntony(store, writer=writer)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(self.row(store)['status'], 'failed')
        # Повторов нет: тот же запрос Syntony отвергнет снова.
        self.run_syntony(store, writer=writer)
        self.assertEqual(len(writer.rows_at_send), 1)

    def test_stuck_sending_is_simply_sent_again(self):
        store = self.stuck_store(crm='syntony')
        summary = self.run_syntony(store)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(self.row(store)['attempts'], 2)

    def test_amo_row_failed_before_the_switch_is_retried_in_syntony(self):
        store = self.stuck_store(status='error', stale=False, crm=None)
        summary = self.run_syntony(store)
        self.assertEqual(summary['created'], 1)
        row = self.row(store)
        self.assertEqual((row['crm'], row['syntony_sent']), ('syntony', True))
        # Сначала спросили amoCRM, не завела ли сделку прошлая попытка.
        self.assertEqual(self.amo.recent_calls, [1790000000 - 120])

    def test_amo_row_whose_lead_exists_is_closed_by_it_not_sent_to_syntony(self):
        # Перезапуск при включении Syntony убил процесс после того, как amoCRM завела сделку,
        # но до отметки: строка `sending`. Сделка есть — в Syntony клиента второй раз не шлём.
        store = self.stuck_store(crm=None)
        self.amo = FakeWriter(store)
        self.amo.recent = 57200001
        summary = self.run_syntony(store)
        self.assertEqual((summary['recovered'], summary['created']), (1, 0))
        self.assertEqual(self.syntony.rows_at_send, [])
        row = self.row(store)
        self.assertEqual((row['status'], row['amo_lead_id']), ('created', 57200001))
        self.assertEqual(self.amo.notes[0][0], 57200001)

    def test_amo_row_is_not_sent_blindly_when_amo_cannot_be_asked(self):
        store = self.stuck_store(crm=None)

        def no_amo():
            raise RuntimeError('amoCRM не настроена')
        summary = self.run_syntony(store, amo_check=no_amo)
        self.assertEqual(self.syntony.rows_at_send, [])
        self.assertEqual(self.row(store)['status'], 'sending')
        self.assertFalse(summary['created'])
        # За горизонтом — «не передан», чтобы перезвонили руками, а не вечное ожидание.
        old = self.stuck_store(crm=None, started_at=NOW - timedelta(
            hours=missed_service.HORIZON_HOURS, minutes=1))
        self.syntony = None
        self.assertEqual(self.run_syntony(old, amo_check=no_amo)['failed'], 1)
        self.assertEqual(self.syntony.rows_at_send, [])

    def test_failed_posts_also_count_toward_the_cap(self):
        # Форма отвечает 429 на всё: упавшие запросы тоже тратят лимит цикла, иначе робот
        # долбил бы форму сорока запросами за цикл и не выходил из 429.
        start = NOW - timedelta(minutes=30)
        calls = [missed_touch('1.%d' % i, start + timedelta(seconds=i), phone='70155520%02d' % i)
                 for i in range(missed_service.SYNTONY_POSTS_PER_CYCLE + 3)]
        store = FakeStore(calls, enabled_since=self.since())
        writer = FakeSyntonyWriter(store)
        writer.fail = missed_service_syntony_error('больше 30 заявок в минуту (HTTP 429)')
        summary = self.run_syntony(store, writer=writer)
        self.assertEqual(len(writer.rows_at_send), missed_service.SYNTONY_POSTS_PER_CYCLE)
        self.assertEqual(summary['errors'], missed_service.SYNTONY_POSTS_PER_CYCLE)

    def test_amo_row_failing_again_in_syntony_names_syntony(self):
        # Повтор упал и в Syntony: подпись «не передан в …» обязана назвать Syntony — туда
        # и уходил последний запрос.
        store = self.stuck_store(status='error', stale=False, crm='amo')
        writer = FakeSyntonyWriter(store)
        writer.fail = missed_service_syntony_error('Syntony: HTTP 503')
        self.run_syntony(store, writer=writer)
        self.assertEqual((self.row(store)['status'], self.row(store)['crm']), ('error', 'syntony'))

    def test_attempts_and_horizon_end_in_failed_without_a_request(self):
        store = self.stuck_store(attempts=missed_service.MAX_ATTEMPTS, crm='syntony')
        summary = self.run_syntony(store)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(self.syntony.rows_at_send, [])
        old = self.stuck_store(started_at=NOW - timedelta(hours=missed_service.HORIZON_HOURS,
                                                          minutes=1), crm='syntony')
        self.syntony = None
        self.assertEqual(self.run_syntony(old)['failed'], 1)

    def test_posts_per_cycle_are_capped_with_retries_counted(self):
        start = NOW - timedelta(minutes=30)
        calls = [missed_touch('1.%d' % i, start + timedelta(seconds=i), phone='70155500%02d' % i)
                 for i in range(missed_service.SYNTONY_POSTS_PER_CYCLE + 2)]
        store = FakeStore(calls, enabled_since=self.since())
        store.rows[('0.9', '7015559999')] = {
            'status': 'error', 'amo_lead_id': None, 'reason': '', 'next': None,
            'started_at': NOW - timedelta(minutes=40), 'phone': '7015559999', 'attempts': 1,
            'sent_epoch': 1790000100, 'error': 'HTTP 503', 'crm': 'syntony'}
        writer = FakeSyntonyWriter(store)
        self.run_syntony(store, writer=writer)
        self.assertEqual(len(writer.rows_at_send), missed_service.SYNTONY_POSTS_PER_CYCLE)
        self.assertEqual(writer.sent[0][0], '0.9', 'повтор идёт первым')
        self.run_syntony(store, writer=writer)
        self.assertEqual(len(writer.sent), missed_service.SYNTONY_POSTS_PER_CYCLE + 3)

    def test_retries_alone_are_capped_too(self):
        # После сбоя Syntony повторов может набраться больше, чем форма примет за минуту.
        store = FakeStore(enabled_since=self.since())
        count = missed_service.SYNTONY_POSTS_PER_CYCLE + 2
        for i in range(count):
            phone = '70155510%02d' % i
            store.rows[('0.%d' % i, phone)] = {
                'status': 'error', 'amo_lead_id': None, 'reason': '', 'next': None,
                'started_at': NOW - timedelta(minutes=40 - i), 'phone': phone, 'attempts': 1,
                'sent_epoch': 1790000100, 'error': 'HTTP 503', 'crm': 'syntony'}
        writer = FakeSyntonyWriter(store)
        self.run_syntony(store, writer=writer)
        self.assertEqual(len(writer.sent), missed_service.SYNTONY_POSTS_PER_CYCLE)
        self.run_syntony(store, writer=writer)
        self.assertEqual(len(writer.sent), count)

    def test_missing_form_key_records_the_call_and_sends_it_later(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=self.since())

        def broken():
            raise missed_service_syntony_error('Syntony: не задан ключ формы SYNTONY_LEADS_FORM_KEY')
        self.run_syntony(store, factory=broken)
        row = self.row(store)
        self.assertEqual((row['status'], row['crm'], row['sent_epoch']), ('error', 'syntony', None))
        self.assertIn('Syntony недоступна', store.runs[-1])
        self.assertEqual(self.run_syntony(store)['created'], 1)


def missed_service_syntony_error(text, final=False):
    from cdr.missed_syntony import SyntonyFormError
    return SyntonyFormError(text, final=final)


class TargetTests(unittest.TestCase):
    """Какую CRM выбирает окружение и когда робот вообще работает."""

    def env(self, **values):
        import os
        clean = {k: v for k, v in os.environ.items()
                 if k not in (missed_config.ENABLED_ENV, missed_config.SYNTONY_ENABLED_ENV,
                              'SYNTONY_LEADS_FORM_KEY')}
        clean.update(values)
        return mock.patch.dict('os.environ', clean, clear=True)

    def test_syntony_wins_over_amo(self):
        with self.env(CDR_MISSED_AMO_ENABLED='1', CDR_MISSED_SYNTONY_ENABLED='1'):
            self.assertEqual(missed_config.target(), missed_config.SYNTONY)
        with self.env(CDR_MISSED_AMO_ENABLED='1'):
            self.assertEqual(missed_config.target(), missed_config.AMO)
        with self.env(CDR_MISSED_SYNTONY_ENABLED='0', CDR_MISSED_AMO_ENABLED='1'):
            self.assertEqual(missed_config.target(), missed_config.AMO)

    def test_syntony_without_a_form_key_is_off_not_amo(self):
        # Переадресация включена, а ключа нет: молча слать в amoCRM нельзя — это не то, что
        # велено; робот выключен, и старт пишет это в журнал.
        with self.env(CDR_MISSED_AMO_ENABLED='1', CDR_MISSED_SYNTONY_ENABLED='1'):
            with mock.patch('amocrm.leads.is_configured', return_value=True):
                self.assertFalse(missed_service.is_enabled())
        with self.env(CDR_MISSED_SYNTONY_ENABLED='1', SYNTONY_LEADS_FORM_KEY='sf_a1b2c3d4e5f6a7b8c9d0e1f2'):
            self.assertTrue(missed_service.is_enabled())
        with self.env(CDR_MISSED_SYNTONY_ENABLED='1', SYNTONY_LEADS_FORM_KEY='sf_ кривой\n'):
            self.assertFalse(missed_service.is_enabled())

    def test_cycle_follows_the_environment(self):
        store = FakeStore([missed_touch('1.1', NOW - timedelta(minutes=5))],
                          enabled_since=NOW - timedelta(hours=1))
        writer = FakeSyntonyWriter(store)
        queries = FakeQueries(live_at=(NOW - timedelta(seconds=5)).isoformat())
        with self.env(CDR_MISSED_AMO_ENABLED='1', CDR_MISSED_SYNTONY_ENABLED='1',
                      SYNTONY_LEADS_FORM_KEY='sf_a1b2c3d4e5f6a7b8c9d0e1f2'), \
                mock.patch.object(missed_service, 'mq', store), \
                mock.patch.object(missed_service, 'queries', queries), \
                mock.patch.object(missed_service, '_syntony_writer', return_value=writer), \
                mock.patch.object(missed_service, '_writer',
                                  side_effect=AssertionError('amoCRM не должна заводиться')):
            summary = missed_service.run_cycle(_Db(), now=NOW)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(len(writer.sent), 1)


class ReclaimSqlTests(unittest.TestCase):
    def test_reclaim_is_a_compare_and_swap_on_status_and_attempts(self):
        import inspect
        sql = ' '.join(inspect.getsource(real_mq.reclaim).split())
        self.assertIn('AND status = %s AND attempts = %s', sql)
        self.assertNotIn("AND phone = %s AND status IN", sql)


class ConfigTests(unittest.TestCase):
    def test_robot_is_off_unless_the_variable_is_set(self):
        import os
        with mock.patch.dict('os.environ', {}, clear=False):
            os.environ.pop(missed_config.ENABLED_ENV, None)
            os.environ.pop(missed_config.SYNTONY_ENABLED_ENV, None)
            self.assertFalse(missed_config.enabled())
            self.assertIsNone(missed_config.target())
        with mock.patch.dict('os.environ', {missed_config.ENABLED_ENV: '1'}):
            self.assertTrue(missed_config.enabled())
        with mock.patch.dict('os.environ', {missed_config.ENABLED_ENV: 'нет'}):
            self.assertFalse(missed_config.enabled())

    def test_stage_is_new_request_of_the_sales_pipeline(self):
        self.assertEqual((missed_config.PIPELINE_ID, missed_config.STATUS_ID), (5524684, 48846277))
        self.assertEqual(missed_config.TAG_NAME, 'Пропущенный входящий')


if __name__ == '__main__':
    unittest.main()
