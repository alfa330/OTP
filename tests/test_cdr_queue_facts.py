# -*- coding: utf-8 -*-
"""Разбор журнала очередей станции (`queuelog`) и дописывание фактов касаниям.

Строки взяты с боевой станции 21.09.2026, поля не придуманы:
  * принятый звонок 1789978364.1127764 — вход 13:13:10, ответ 13:13:11, разговор 90 с,
    трубку положил клиент;
  * брошенный 1789977266.1127509 — вход 12:54:42, отказ 12:56:17, ожидание 95 с.

Что закреплено:
  * ожидание берётся из data станции, а не из разности времён (разность — запасной путь);
  * COMPLETECALLER/COMPLETEAGENT различают, кто положил трубку;
  * ответ не приписывается тому, кто не разговаривал;
  * накопление фактов переживает узкое окно живого хвоста.
"""

import unittest
from datetime import datetime, timedelta

from cdr import queue_facts as Q, touches as T


def row(time_text, callid, event, queue='3041', agent='NONE', data1='', data2='', data3=''):
    return {'time': datetime.strptime(time_text, '%Y-%m-%d %H:%M:%S'), 'callid': callid,
            'queuename': queue, 'agent': agent, 'event': event,
            'data1': data1, 'data2': data2, 'data3': data3}


ANSWERED = '1789978364.1127764'
ABANDONED = '1789977266.1127509'

ANSWERED_ROWS = [
    row('2026-09-21 13:13:10', ANSWERED, 'DID', data1='7009214242'),
    row('2026-09-21 13:13:10', ANSWERED, 'ENTERQUEUE', data2='+77718533633', data3='1'),
    row('2026-09-21 13:13:11', ANSWERED, 'CONNECT', agent='sagidollayev_nurmakhan',
        data1='1', data2='1789978390.1127767', data3='1'),
    row('2026-09-21 13:14:41', ANSWERED, 'COMPLETECALLER', agent='sagidollayev_nurmakhan',
        data1='1', data2='90', data3='1'),
]

ABANDONED_ROWS = [
    row('2026-09-21 12:54:42', ABANDONED, 'ENTERQUEUE', queue='3034', data2='+77053763454'),
    row('2026-09-21 12:56:17', ABANDONED, 'ABANDON', queue='3034', data1='1', data2='1', data3='95'),
]


class BuildFactsTests(unittest.TestCase):
    def test_answered_call_gives_entry_answer_talk_and_side(self):
        fact = Q.build_facts(ANSWERED_ROWS)[ANSWERED]
        self.assertEqual(fact['queued_at'], datetime(2026, 9, 21, 13, 13, 10))
        self.assertEqual(fact['answered_at'], datetime(2026, 9, 21, 13, 13, 11))
        self.assertEqual(fact['wait_seconds'], 1)
        self.assertEqual(fact['talk_seconds'], 90)
        self.assertEqual(fact['hangup_side'], Q.HANGUP_CLIENT)
        self.assertEqual(fact['queue'], '3041')
        self.assertEqual(fact['agent'], 'sagidollayev_nurmakhan')

    def test_agent_hangup_is_told_apart(self):
        rows = ANSWERED_ROWS[:3] + [row('2026-09-21 13:14:41', ANSWERED, 'COMPLETEAGENT',
                                        agent='sagidollayev_nurmakhan', data1='1', data2='90')]
        self.assertEqual(Q.build_facts(rows)[ANSWERED]['hangup_side'], Q.HANGUP_OPERATOR)

    def test_abandoned_call_has_wait_but_no_answer(self):
        fact = Q.build_facts(ABANDONED_ROWS)[ABANDONED]
        self.assertTrue(fact['lost'])
        self.assertIsNone(fact['answered_at'])
        self.assertEqual(fact['wait_seconds'], 95, 'ожидание брошенного — data3 у ABANDON')
        self.assertIsNone(fact['talk_seconds'])

    def test_missing_data_falls_back_to_the_clock(self):
        """Поля data пустеют редко, но ожидание — главная величина табло: разность
        времён входа и исхода даёт ровно то же число."""
        rows = [ABANDONED_ROWS[0],
                row('2026-09-21 12:56:17', ABANDONED, 'ABANDON', queue='3034', data3='')]
        self.assertEqual(Q.build_facts(rows)[ABANDONED]['wait_seconds'], 95)

    def test_second_entry_does_not_reset_the_wait(self):
        """Возврат в очередь после перевода не должен обнулять ожидание задним числом:
        клиент ждёт с первого входа, по нему и считается SL."""
        rows = ANSWERED_ROWS + [row('2026-09-21 13:20:00', ANSWERED, 'ENTERQUEUE')]
        self.assertEqual(Q.build_facts(rows)[ANSWERED]['queued_at'],
                         datetime(2026, 9, 21, 13, 13, 10))

    def test_garbage_rows_are_skipped_without_breaking_the_rest(self):
        rows = [{'time': None, 'callid': 'x', 'event': 'CONNECT'},
                {'callid': '', 'event': 'ENTERQUEUE', 'time': datetime.now()}] + ABANDONED_ROWS
        facts = Q.build_facts(rows)
        self.assertEqual(list(facts), [ABANDONED])

    def test_absurd_wait_is_refused(self):
        rows = [ABANDONED_ROWS[0], row('2026-09-21 12:56:17', ABANDONED, 'ABANDON',
                                       queue='3034', data3='999999')]
        self.assertEqual(Q.build_facts(rows)[ABANDONED]['wait_seconds'], 95,
                         'мусор в data не проходит, остаётся расчёт по часам')


class MergeTests(unittest.TestCase):
    def test_narrow_window_does_not_erase_what_is_known(self):
        """Живой хвост спрашивает журнал за два часа, а касания пересобирает за день:
        без накопления утренний звонок терял бы вход в очередь на каждом цикле."""
        base = Q.build_facts(ANSWERED_ROWS)
        late = Q.build_facts([row('2026-09-21 13:14:41', ANSWERED, 'COMPLETEAGENT', data2='90')])
        merged = Q.merge(base, late)
        self.assertEqual(merged[ANSWERED]['queued_at'], datetime(2026, 9, 21, 13, 13, 10))
        self.assertEqual(merged[ANSWERED]['wait_seconds'], 1)
        self.assertEqual(merged[ANSWERED]['hangup_side'], Q.HANGUP_CLIENT,
                         'уже известное не перетирается поздним окном')

    def test_new_call_is_added(self):
        merged = Q.merge(Q.build_facts(ANSWERED_ROWS), Q.build_facts(ABANDONED_ROWS))
        self.assertEqual(sorted(merged), sorted([ANSWERED, ABANDONED]))

    def test_late_events_fill_the_gaps(self):
        entry = Q.build_facts([ANSWERED_ROWS[1]])
        merged = Q.merge(entry, Q.build_facts(ANSWERED_ROWS[2:]))
        self.assertEqual(merged[ANSWERED]['answered_at'], datetime(2026, 9, 21, 13, 13, 11))
        self.assertEqual(merged[ANSWERED]['talk_seconds'], 90)


def touch(linkedid, call_type='Входящий', talk=90):
    return {'linkedid': linkedid, 'phone': '7718533633', 'call_type': call_type,
            'started_at': '2026-09-21 13:12:44', 'answered_at': '', 'talk_seconds': talk,
            'dial_seconds': 117, 'ext': '6229', 'queue': '3041', 'result': 'Разговор'}


class AttachTests(unittest.TestCase):
    def setUp(self):
        self.facts = Q.build_facts(ANSWERED_ROWS + ABANDONED_ROWS)

    def test_incoming_gets_exact_fields(self):
        out = Q.attach([touch(ANSWERED)], self.facts)[0]
        self.assertEqual(out['queued_at'], '2026-09-21 13:13:10')
        self.assertEqual(out['answered_at'], '2026-09-21 13:13:11')
        self.assertEqual((out['wait_seconds'], out['talk_measured_seconds']), (1, 90))
        self.assertEqual(out['hangup_side'], 'client')

    def test_original_touch_is_not_modified(self):
        source = touch(ANSWERED)
        Q.attach([source], self.facts)
        self.assertNotIn('queued_at', source)

    def test_outgoing_is_left_alone(self):
        out = Q.attach([touch(ANSWERED, call_type='Исходящий')], self.facts)[0]
        self.assertNotIn('queued_at', out)

    def test_lost_call_gets_the_wait_but_never_an_answer(self):
        out = Q.attach([touch(ABANDONED, call_type='Входящий (не приняли)', talk=0)],
                       self.facts)[0]
        self.assertEqual(out['wait_seconds'], 95)
        self.assertEqual(out['queued_at'], '2026-09-21 12:54:42')
        self.assertEqual(out['answered_at'], '', 'у брошенного ответа не было')

    def test_unknown_call_stays_as_it_was(self):
        out = Q.attach([touch('9999.1')], self.facts)[0]
        self.assertNotIn('wait_seconds', out)

    def test_no_facts_at_all_changes_nothing(self):
        touches = [touch(ANSWERED)]
        self.assertEqual(Q.attach(touches, {}), touches)


GREETING_DROP = '1790420937.1198448'


def greeting_drop(**over):
    """Непринятый 26.09.2026 16:08:57 на очередь 3010: 6 с на приветствии «Центра
    регистрации», отбой ещё в `ext-queues` до `Queue()`. Строка станции у него такая же, как
    у брошенного в очереди, а в журнале — ни одного события."""
    base = {'linkedid': GREETING_DROP, 'phone': '7776084066', 'call_type': 'Входящий (не приняли)',
            'started_at': '2026-09-26 16:08:57', 'answered_at': '', 'talk_seconds': 0,
            'dial_seconds': 6, 'ext': '', 'queue': '3010', 'result': 'Сброс без разговора',
            'line_number': '7475777778'}
    base.update(over)
    return base


JOURNAL_READ = datetime(2026, 9, 27, 1, 0, 0)   # суточное окно с часовым хвостом


class NeverEnteredTests(unittest.TestCase):
    """«Не дошёл до очереди» по журналу: у брошенного на приветствии нет ENTERQUEUE.

    Разбор 01–28.09.2026: 27 непринятых с очередью в очередь не входили (3038 — отбой на
    4–16 с при входе на 17–18 с; 3010/3001/3007 — за секунду до входа), а считались «не
    приняли», и табло писало их в потерянные. Решать по ОТСУТСТВИЮ события можно только
    при полном журнале — это и закреплено."""

    def setUp(self):
        self.facts = Q.build_facts(ANSWERED_ROWS + ABANDONED_ROWS)

    def test_no_entry_in_a_complete_journal_means_before_the_queue(self):
        out = Q.attach([greeting_drop()], self.facts, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], T.TYPE_IN_BEFORE_QUEUE)
        self.assertEqual(out['result'], T.RESULT_BEFORE_QUEUE)
        self.assertEqual(out['queue'], '3010', 'по очереди выводится таксопарк линии')
        self.assertEqual((out['ext'], out['answered_at'], out['dial_seconds']), ('', '', 6))
        self.assertNotIn('queued_at', out)

    def test_quiet_journal_is_still_an_answer(self):
        """Ночью журнал бывает пустым целиком — это «событий не было», а не «не знаю»."""
        out = Q.attach([greeting_drop()], {}, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], T.TYPE_IN_BEFORE_QUEUE)

    def test_did_mark_alone_is_not_an_entry(self):
        """23.09.2026 17:09: станция записала DID (последний шаг перед Queue()) — и клиент
        положил трубку в ту же секунду. В очереди он не был."""
        facts = Q.build_facts([row('2026-09-26 16:09:03', GREETING_DROP, 'DID', queue='3010',
                                   data1='7475777778')])
        out = Q.attach([greeting_drop()], facts, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], T.TYPE_IN_BEFORE_QUEUE)

    def test_call_abandoned_in_the_queue_stays_missed(self):
        lost = greeting_drop(linkedid=ABANDONED, phone='7053763454', queue='3034',
                             started_at='2026-09-21 12:54:26', dial_seconds=111)
        out = Q.attach([lost], self.facts, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], 'Входящий (не приняли)')
        self.assertEqual(out['wait_seconds'], 95)

    def test_without_a_journal_nothing_is_decided(self):
        self.assertEqual(Q.attach([greeting_drop()], self.facts)[0]['call_type'],
                         'Входящий (не приняли)')
        self.assertEqual(Q.attach([greeting_drop()], {})[0]['call_type'],
                         'Входящий (не приняли)')

    def test_journal_that_ends_before_the_call_does_not_decide(self):
        """Обрезанный потолком или ещё не дочитанный журнал: вход мог быть в хвосте."""
        early = datetime(2026, 9, 26, 16, 9, 0)   # звонок кончился в 16:09:03
        out = Q.attach([greeting_drop()], self.facts, journal_until=early)[0]
        self.assertEqual(out['call_type'], 'Входящий (не приняли)')

    def test_operator_rung_means_the_queue_was_reached(self):
        out = Q.attach([greeting_drop(ext='6360', result='Не ответил')], self.facts,
                       journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], 'Входящий (не приняли)')

    def test_call_without_a_queue_is_left_to_the_gluing(self):
        out = Q.attach([greeting_drop(queue='')], self.facts, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], 'Входящий (не приняли)')

    def test_entry_under_another_callid_of_the_same_caller_counts(self):
        """Страховка сопоставления: вход того же клиента во время звонка, но под другим
        callid (перевод через внутренний канал) — звонок в очередь дошёл."""
        facts = Q.build_facts([row('2026-09-26 16:09:01', '1790420941.1198450', 'ENTERQUEUE',
                                   queue='3010', data2='+77776084066')])
        out = Q.attach([greeting_drop()], facts, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], 'Входящий (не приняли)')

    def test_the_callers_next_call_does_not_count(self):
        """12.09.2026: клиент бросил на приветствии в 03:50 и через две минуты позвонил
        снова — вход второго звонка первому не засчитывается."""
        facts = Q.build_facts([row('2026-09-26 16:11:13', '1790421066.1198460', 'ENTERQUEUE',
                                   queue='3010', data2='+77776084066')])
        out = Q.attach([greeting_drop()], facts, journal_until=JOURNAL_READ)[0]
        self.assertEqual(out['call_type'], T.TYPE_IN_BEFORE_QUEUE)

    def test_answered_and_outgoing_are_never_touched(self):
        for call_type in ('Входящий', 'Исходящий'):
            out = Q.attach([greeting_drop(call_type=call_type)], {}, journal_until=JOURNAL_READ)[0]
            self.assertEqual(out['call_type'], call_type)


class OpenCallsTests(unittest.TestCase):
    """Звонки, которых ещё нет в CDR (задача #291): кто сейчас в очереди или говорит.

    Заявка автообзвона 28.09.2026 входила в очередь 3035 с номером клиента в data2, ждала
    72 с и получила CONNECT — но события DID у неё не было: это наш исходящий звонок."""

    NOW = datetime(2026, 9, 21, 13, 14, 0)
    DIAL_REQUEST = '1789978300.1127700'

    def facts(self, extra=()):
        rows = list(ANSWERED_ROWS[:3]) + list(extra) + [
            row('2026-09-21 13:12:30', self.DIAL_REQUEST, 'ENTERQUEUE', queue='3035',
                data2='+77015550009'),
            row('2026-09-21 13:13:42', self.DIAL_REQUEST, 'CONNECT', queue='3035',
                agent='sagidollayev_nurmakhan', data1='72'),
        ]
        return Q.build_facts(rows)

    def test_caller_and_did_are_read(self):
        fact = Q.build_facts(ANSWERED_ROWS)[ANSWERED]
        self.assertEqual(fact['caller'], '7718533633')
        self.assertEqual(fact['did'], '7009214242')

    def test_conversation_in_progress_is_listed_with_its_answer(self):
        calls = Q.open_calls(self.facts(), set(), self.NOW)
        self.assertEqual([c['linkedid'] for c in calls], [ANSWERED])
        self.assertEqual(calls[0]['phone'], '7718533633')
        self.assertEqual(calls[0]['answered_at'], '2026-09-21 13:13:11')
        self.assertFalse(calls[0]['ended'])

    def test_dial_request_without_did_is_not_an_incoming_call(self):
        linkedids = [c['linkedid'] for c in Q.open_calls(self.facts(), set(), self.NOW)]
        self.assertNotIn(self.DIAL_REQUEST, linkedids)

    def test_calls_already_in_cdr_are_not_repeated(self):
        self.assertEqual(Q.open_calls(self.facts(), {ANSWERED}, self.NOW), [])

    def test_finished_call_not_yet_in_cdr_is_listed_as_ended(self):
        facts = Q.build_facts(ANSWERED_ROWS)
        calls = Q.open_calls(facts, set(), self.NOW)
        self.assertTrue(calls[0]['ended'])

    def test_old_calls_fall_out_of_the_list(self):
        later = datetime(2026, 9, 21, 13, 13, 10) + timedelta(minutes=Q.OPEN_CALL_MINUTES + 1)
        self.assertEqual(Q.open_calls(self.facts(), set(), later), [])

    def test_merge_keeps_the_did_seen_in_an_earlier_window(self):
        early = Q.build_facts(ANSWERED_ROWS[:2])
        late = Q.build_facts(ANSWERED_ROWS[2:3])
        merged = Q.merge(early, late)
        self.assertEqual(merged[ANSWERED]['did'], '7009214242')
        self.assertEqual(merged[ANSWERED]['caller'], '7718533633')
        self.assertIsNotNone(merged[ANSWERED]['answered_at'])

    def test_did_is_part_of_what_the_bridge_asks_for(self):
        self.assertIn('DID', Q.WANTED_EVENTS)


if __name__ == '__main__':
    unittest.main()
