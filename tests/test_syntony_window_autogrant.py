"""Сторож списка окна Syntony (владелец, 20.11.2026: «Сделай так, чтобы автоматом новые
добавлялись»). Что держат тесты:

- новые работающие сотрудники ОП и СЗоВ попадают в список с рабочей почтой из карточки
  (занятая другой строкой — нет) и сразу записываются у Syntony;
- кого убирали из списка — не возвращаем; уволенных и чужие отделы не трогаем;
- пустой строке подставляется почта из карточки, вписанная руками — никогда;
- без ключа Syntony — ни одной записи; отказ подключения целиком останавливает прогон,
  остальные отказы — пауза 6 часов для этого человека;
- за прогон — не больше партии людей и бюджета времени разговоров с Syntony;
- курсор закрыт, пока Syntony отвечает; поток — один на процесс.

SQL не исполняется: подставной склад. Настоящие запросы — tests/test_syntony_window_postgres.py.
"""

import contextlib
import threading
import unittest
import unittest.mock

from syntony_window import autogrant, client, rules
from tests.test_syntony_window import (
    ADMIN, CONFIG, FIRED, NOT_LINKED, OPERATOR, SUPER, SV, TRAINEE, FakeResponse, FakeStore, Recorder,
)


class Store(FakeStore):
    """Склад с отделами и следом снятий — всё, что читает сторож."""

    def __init__(self):
        super().__init__()
        self.departments = {SUPER: 'development', ADMIN: 'szov', SV: 'op', OPERATOR: 'op',
                            TRAINEE: 'szov', FIRED: 'op'}

    def _row(self, uid):
        return {'id': uid, 'name': self.users[uid]['name'],
                'card_email': rules.clean_email(self.users[uid]['card_email'])}

    def new_staff(self, cursor, department_codes, limit):
        revoked = {fields['user_id'] for action, fields in self.journal if action == 'revoke'}
        codes = {code.lower() for code in department_codes}
        found = [uid for uid in sorted(self.users)
                 if self.departments.get(uid) in codes and self._working(uid)
                 and uid not in self.access and uid not in revoked]
        return [self._row(uid) for uid in found[:limit]]

    def rows_awaiting_card_email(self, cursor, limit):
        rows = [self._row(uid) for uid in sorted(self.access)
                if self.access[uid]['email'] == '' and self._working(uid)]
        return [row for row in rows if row['card_email']][:limit]

    def fill_empty_email(self, cursor, user_id, email):
        row = self.access.get(user_id)
        if not row or row['email']:
            return False
        row.update(email=email, link_state='unknown')
        return True

    def unregistered(self, cursor, limit):
        return [uid for uid in sorted(self.access)
                if self.access[uid]['link_state'] == 'unknown' and self._working(uid)][:limit]


class CountingDb:
    def __init__(self):
        self.open = 0

    def _get_cursor(self):
        db = self

        @contextlib.contextmanager
        def cm():
            db.open += 1
            try:
                yield object()
            finally:
                db.open -= 1
        return cm()


class Clock:
    def __init__(self, step=0.0):
        self.now, self.step = 1000.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class Run:
    """Прогон сторожа на подставных складе, Syntony, часах и сне."""

    def __init__(self, *replies, configured=True, store=None, clock=None):
        self.store = store or Store()
        self.db = CountingDb()
        self.syntony = Recorder(*(replies or (NOT_LINKED,)))
        self.configured = configured
        self.clock = clock or Clock()
        self.backoff = {}
        self.slept = []
        self.cursors_during_network = []

    def _post(self, url, **kwargs):
        self.cursors_during_network.append(self.db.open)
        return self.syntony(url, **kwargs)

    def __call__(self, **overrides):
        options = dict(load_config=lambda: CONFIG if self.configured else None,
                       request_window=lambda config, body: client.request_window(config, body, post=self._post),
                       store=self.store, sleep=self.slept.append, clock=self.clock, backoff=self.backoff)
        options.update(overrides)
        return autogrant.run_once(self.db, **options)

    @property
    def asked(self):
        return [call['json'] for call in self.syntony.calls]


class AutograntTests(unittest.TestCase):
    def test_newcomers_of_op_and_szov_are_added_with_card_email_and_registered(self):
        run = Run()
        summary = run()
        # SV, оператор (ОП) и админ, стажёр (СЗоВ); супер-админ — в другом отделе, Глеб уволен.
        self.assertEqual(summary['granted'], [ADMIN, SV, OPERATOR, TRAINEE])
        self.assertEqual({uid: run.store.access[uid]['email'] for uid in summary['granted']},
                         {ADMIN: '', SV: 'sv@icore.kz', OPERATOR: 'aigerim@icore.kz', TRAINEE: ''})
        self.assertEqual(summary['registered'], {'not_linked': 4})
        self.assertEqual(sorted(body['partnerUserId'] for body in run.asked),
                         sorted(str(uid) for uid in (ADMIN, SV, OPERATOR, TRAINEE)))
        self.assertTrue(all(body['scope'] == 'inbox' and 'phones' not in body for body in run.asked))
        self.assertEqual({uid: run.store.access[uid]['link_state'] for uid in summary['granted']},
                         dict.fromkeys(summary['granted'], 'not_linked'))
        self.assertEqual([(action, fields['actor_id'], fields['actor_name']) for action, fields in run.store.journal],
                         [('grant', None, 'Автоматически')] * 4)
        self.assertNotIn(SUPER, run.store.access)
        self.assertNotIn(FIRED, run.store.access)

    def test_people_once_removed_from_the_list_are_not_returned(self):
        run = Run()
        run.store.journal.append(('revoke', {'user_id': OPERATOR}))
        self.assertNotIn(OPERATOR, run()['granted'])
        self.assertNotIn(OPERATOR, run.store.access)

    def test_card_email_taken_by_another_row_is_not_copied(self):
        run = Run()
        run.store.add(SUPER, 'aigerim@icore.kz', link_state='linked')
        run()
        self.assertEqual(run.store.access[OPERATOR]['email'], '')

    def test_empty_row_gets_the_card_email_but_a_typed_email_is_never_touched(self):
        run = Run()
        run.store.add(SV, '', link_state='not_linked')
        run.store.add(OPERATOR, 'typed@icore.kz', link_state='not_linked')
        summary = run()
        self.assertEqual(summary['emails'], [SV])
        self.assertEqual(run.store.access[SV]['email'], 'sv@icore.kz')
        self.assertEqual(run.store.access[OPERATOR]['email'], 'typed@icore.kz')
        sv_body = next(body for body in run.asked if body['partnerUserId'] == str(SV))
        self.assertEqual(sv_body['partnerUserEmail'], 'sv@icore.kz', 'новая почта — новая запись у Syntony')
        self.assertIn(('email', {'user_id': SV, 'user_name': 'Старший Смены', 'email': 'sv@icore.kz',
                                 'actor_id': None, 'actor_name': 'Автоматически'}), run.store.journal)

    def test_nothing_happens_without_the_key(self):
        run = Run(configured=False)
        self.assertEqual(run(), {'skipped': 'not_configured'})
        self.assertEqual((run.store.access, run.asked), ({}, []))

    def test_refusal_of_the_whole_connection_stops_the_run(self):
        run = Run(FakeResponse(401, {'error': {'code': 'invalid_key', 'message': 'ключ не тот'}}))
        summary = run()
        self.assertEqual((summary['stopped'], len(run.asked)), ('invalid_key', 1))
        self.assertEqual(len(summary['granted']), 4, 'список пополнен, запись у Syntony — со следующего прогона')
        self.assertEqual(run.backoff, {}, 'отказ подключения — не про человека: паузы для него нет')

    def test_other_refusals_rest_six_hours_for_that_person(self):
        bad = FakeResponse(400, {'error': {'code': 'invalid_request', 'message': 'плохое имя'}})
        store = Store()
        store.departments = {OPERATOR: 'op'}
        clock = Clock()
        run = Run(bad, NOT_LINKED, store=store, clock=clock)
        self.assertEqual(run()['registered'], {'invalid_request': 1})
        self.assertEqual(run()['registered'], {}, 'в паузе Syntony не спрашиваем')
        clock.now += autogrant.RETRY_AFTER_SECONDS + 1
        self.assertEqual(run()['registered'], {'not_linked': 1})
        self.assertEqual(len(run.asked), 2)

    def test_a_run_is_limited_by_the_batch_and_by_the_talk_budget(self):
        run = Run()
        summary = run(batch=2)
        self.assertEqual(summary['granted'], [ADMIN, SV])
        slow = Run(clock=Clock(step=31.0))
        self.assertEqual(sum(slow(budget=60)['registered'].values()), 1, 'второй запрос — уже за бюджетом')

    def test_pause_between_requests_and_no_cursor_while_syntony_answers(self):
        run = Run()
        run()
        self.assertEqual(run.slept, [autogrant.PAUSE_SECONDS] * 3)
        self.assertEqual(run.cursors_during_network, [0, 0, 0, 0])

    def test_switch_reads_the_environment(self):
        self.assertTrue(autogrant.is_enabled({}))
        for value in ('false', '0', 'off', 'No'):
            self.assertFalse(autogrant.is_enabled({autogrant.ENV_ENABLED: value}))
        self.assertTrue(autogrant.is_enabled({autogrant.ENV_ENABLED: 'true'}))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self._saved = (autogrant._worker, autogrant._worker_pid, autogrant._loop)
        autogrant._worker = autogrant._worker_pid = None
        self.started = threading.Event()
        self.release = threading.Event()

        def loop(db, interval, first_delay):
            self.started.set()
            self.release.wait(5)
        autogrant._loop = loop

    def tearDown(self):
        self.release.set()
        autogrant._worker, autogrant._worker_pid, autogrant._loop = self._saved

    def test_worker_starts_once_per_process(self):
        with unittest.mock.patch.dict('os.environ', {client.ENV_KEY: 'sk_live_test'}, clear=False):
            first = autogrant.start_worker(object())
            self.assertTrue(self.started.wait(5))
            self.assertIs(autogrant.start_worker(object()), first)
        self.assertTrue(first.daemon)

    def test_worker_stays_off_without_the_key_or_with_the_switch(self):
        with unittest.mock.patch.dict('os.environ', {client.ENV_KEY: ''}, clear=False):
            self.assertIsNone(autogrant.start_worker(object()))
        with unittest.mock.patch.dict('os.environ', {client.ENV_KEY: 'sk_live_test', autogrant.ENV_ENABLED: 'false'},
                                      clear=False):
            self.assertIsNone(autogrant.start_worker(object()))
        self.assertFalse(self.started.is_set())


if __name__ == '__main__':
    unittest.main()
