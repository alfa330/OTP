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
from datetime import datetime

from cdr import queue_facts as Q


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


if __name__ == '__main__':
    unittest.main()
