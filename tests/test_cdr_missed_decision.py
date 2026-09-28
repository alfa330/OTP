# -*- coding: utf-8 -*-
"""Пропущенные входящие → amoCRM: правило «передавать или нет» (задача #291).

Что закреплено:
  * минута ожидания считается от КОНЦА пропущенного звонка, а не от начала;
  * дозвонился в течение минуты — не передаём; перезвонил и снова не дозвонился —
    решение переносится на последний звонок цепочки;
  * клиент, который перезвонил и ещё говорит (в CDR звонка нет, в журнале очередей —
    есть), считается дозвонившимся; клиент, который ещё ждёт в очереди, — ждём и мы;
  * без свежих данных моста не решаем, но и дольше MAX_HOLD_SECONDS не ждём;
  * решение, принятое с опозданием, всё равно видит разговор после минуты;
  * исходящий с разговором — тоже ответ; «не дошёл до очереди» — нет.

Телефоны учебные (7XX555XXXX): настоящих в репозитории быть не должно.
"""

import unittest
from datetime import datetime, timedelta, timezone

from cdr import missed
from cdr import touches as T

PHONE = '7015550001'
T0 = datetime(2026, 9, 28, 10, 0, 0)


def _epoch(local):
    """Наивное время Алматы → секунды эпохи, как в целой части linkedid станции."""
    return int(local.replace(tzinfo=timezone(timedelta(hours=5))).timestamp())


def missed_call(**over):
    """Пропущенный: пришёл в 10:00:00, вошёл в очередь в 10:00:07, ждал 40 с → конец 10:00:47."""
    base = {
        'linkedid': '1790571600.1', 'phone': PHONE, 'call_day': T0.date(),
        'started_at': T0, 'dial_seconds': 47,
        'queued_at': T0 + timedelta(seconds=7), 'wait_seconds': 40,
        'hangup_side': '', 'line_number': '7475550078', 'queue': '3010', 'result': T.RESULT_BUSY,
    }
    base.update(over)
    return base


def touch(linkedid, started, call_type, talk=0):
    return {'linkedid': linkedid, 'phone': PHONE, 'started_at': started, 'call_type': call_type,
            'talk_seconds': talk, 'dial_seconds': talk + 10, 'queued_at': None,
            'wait_seconds': None}


END = T0 + timedelta(seconds=47)
DEADLINE = END + timedelta(seconds=60)
FRESH = DEADLINE + timedelta(seconds=missed.SETTLE_SECONDS + 5)


class EndOfCallTests(unittest.TestCase):
    def test_end_is_queue_entry_plus_wait(self):
        self.assertEqual(missed.call_end(missed_call()), END)

    def test_the_later_estimate_wins(self):
        # Строка CDR длиннее, чем ожидание по журналу, — берём позднюю: ранняя сократила
        # бы минуту ожидания.
        self.assertEqual(missed.call_end(missed_call(dial_seconds=90)), T0 + timedelta(seconds=90))

    def test_without_queue_facts_the_row_length_is_used(self):
        call = missed_call(queued_at=None, wait_seconds=None)
        self.assertEqual(missed.call_end(call), T0 + timedelta(seconds=47))

    def test_string_times_are_understood(self):
        call = missed_call(started_at='2026-09-28 10:00:00', queued_at='2026-09-28 10:00:07')
        self.assertEqual(missed.call_end(call), END)


class WaitTests(unittest.TestCase):
    def test_waits_the_minute_after_the_end_not_after_the_start(self):
        # 10:01:10 — минута от начала прошла, а от отбоя (10:00:47) ещё нет.
        decision = missed.decide(missed_call(), [], T0 + timedelta(seconds=70), FRESH)
        self.assertEqual(decision.kind, missed.WAIT)
        self.assertEqual(decision.deadline, DEADLINE)

    def test_transfers_after_the_minute_when_nobody_answered(self):
        decision = missed.decide(missed_call(), [], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)
        self.assertEqual(decision.ended_at, END)

    def test_waits_for_the_bridge_to_bring_the_minute(self):
        # Мост последний раз приходил до конца минуты — звонок за эту минуту мог ещё не
        # доехать до портала.
        decision = missed.decide(missed_call(), [], FRESH, DEADLINE - timedelta(seconds=5))
        self.assertEqual(decision.kind, missed.WAIT)

    def test_waits_out_the_longest_greeting_after_the_minute(self):
        # Перезвон последней секунды минуты слушает приветствие до 30 с и до входа в
        # очередь не виден нигде — данные, снятые через 20 с после минуты, ещё слепы.
        seen = DEADLINE + timedelta(seconds=missed.DATA_GRACE_SECONDS + 1)
        decision = missed.decide(missed_call(), [], FRESH, seen)
        self.assertEqual(decision.kind, missed.WAIT)
        self.assertGreaterEqual(missed.GREETING_SECONDS, 30)

    def test_a_silent_bridge_does_not_hold_the_call_forever(self):
        late = DEADLINE + timedelta(seconds=missed.MAX_HOLD_SECONDS + 1)
        decision = missed.decide(missed_call(), [], late, None)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_queue_log_saying_it_was_answered_wins(self):
        # Надстройка станции выбрала строку попытки дозвона, а журнал очередей назвал
        # сторону отбоя разговора — значит, трубку сняли.
        decision = missed.decide(missed_call(hangup_side='client'), [], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.ANSWERED)


class RedialTests(unittest.TestCase):
    def test_answered_redial_within_the_minute_is_not_transferred(self):
        redial = touch('1790571680.2', END + timedelta(seconds=30), T.TYPE_IN, talk=95)
        decision = missed.decide(missed_call(), [redial], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.ANSWERED)
        self.assertIn('10:01:17', decision.reason)

    def test_the_missed_call_itself_is_not_its_own_redial(self):
        itself = dict(touch('1790571600.1', T0, T.TYPE_IN_MISSED))
        decision = missed.decide(missed_call(), [itself], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_an_answered_call_before_the_missed_one_does_not_count(self):
        earlier = touch('1790571000.1', T0 - timedelta(minutes=5), T.TYPE_IN, talk=60)
        decision = missed.decide(missed_call(), [earlier], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_late_decision_still_sees_a_later_conversation(self):
        # Решение запоздало (портал перезапускался): клиент дозвонился через пять минут —
        # заводить ему перезвон уже незачем.
        later = touch('1790571950.3', END + timedelta(minutes=5), T.TYPE_IN, talk=40)
        now = END + timedelta(minutes=8)
        decision = missed.decide(missed_call(), [later], now, now)
        self.assertEqual(decision.kind, missed.ANSWERED)

    def test_conversation_after_the_decision_moment_is_not_known_yet(self):
        # На момент решения разговора ещё не было — сделку заводим, будущего не видно.
        later = touch('1790571950.3', FRESH + timedelta(minutes=5), T.TYPE_IN, talk=40)
        decision = missed.decide(missed_call(), [later], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_operator_calling_back_and_talking_counts_as_an_answer(self):
        back = touch('1790571690.4', END + timedelta(seconds=40), T.TYPE_OUT, talk=120)
        decision = missed.decide(missed_call(), [back], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.ANSWERED)
        self.assertIn('Оператор перезвонил', decision.reason)

    def test_zero_second_outgoing_is_not_a_conversation(self):
        back = touch('1790571690.4', END + timedelta(seconds=40), T.TYPE_OUT, talk=0)
        decision = missed.decide(missed_call(), [back], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_hanging_up_on_the_greeting_is_not_an_answer(self):
        greeting = touch('1790571690.5', END + timedelta(seconds=20), T.TYPE_IN_BEFORE_QUEUE)
        decision = missed.decide(missed_call(), [greeting], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_missed_again_within_the_minute_chains_to_the_last_call(self):
        again = touch('1790571700.6', END + timedelta(seconds=45), T.TYPE_IN_MISSED)
        decision = missed.decide(missed_call(), [again], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.CHAINED)
        self.assertEqual(decision.next_linkedid, '1790571700.6')

    def test_missed_again_after_the_minute_is_a_separate_call(self):
        again = touch('1790571800.7', DEADLINE + timedelta(seconds=10), T.TYPE_IN_MISSED)
        now = DEADLINE + timedelta(seconds=60)
        decision = missed.decide(missed_call(), [again], now, now)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_answer_beats_a_chain_in_the_same_minute(self):
        # Перезвонил, не дозвонился, перезвонил ещё раз и поговорил — сделка не нужна.
        again = touch('1790571700.6', END + timedelta(seconds=20), T.TYPE_IN_MISSED)
        talk = touch('1790571750.8', END + timedelta(seconds=55), T.TYPE_IN, talk=30)
        decision = missed.decide(missed_call(), [again, talk], FRESH, FRESH)
        self.assertEqual(decision.kind, missed.ANSWERED)


class LiveQueueTests(unittest.TestCase):
    """Звонки, которых в CDR ещё нет: мост присылает их из журнала очередей."""

    def call(self, offset, answered=False, ended=False, greeting=7):
        """Перезвон, пришедший через offset секунд после конца пропущенного; в очередь
        он попадает после приветствия. Целая часть linkedid — секунда прихода по Алматы."""
        arrival = END + timedelta(seconds=offset)
        queued = arrival + timedelta(seconds=greeting)
        return {'linkedid': '%d.9' % _epoch(arrival), 'queued_at': queued,
                'answered_at': queued + timedelta(seconds=3) if answered else None,
                'ended': ended}

    def test_redial_talking_right_now_counts_as_answered(self):
        decision = missed.decide(missed_call(), [], FRESH, FRESH,
                                 queue_calls=[self.call(30, answered=True)],
                                 queue_fresh_until=FRESH)
        self.assertEqual(decision.kind, missed.ANSWERED)
        self.assertIn('оператор ответил', decision.reason)

    def test_redial_still_waiting_in_the_queue_holds_the_decision(self):
        decision = missed.decide(missed_call(), [], FRESH, FRESH,
                                 queue_calls=[self.call(30)], queue_fresh_until=FRESH)
        self.assertEqual(decision.kind, missed.WAIT)

    def test_waiting_redial_does_not_hold_past_the_cap(self):
        late = DEADLINE + timedelta(seconds=missed.MAX_HOLD_SECONDS + 1)
        decision = missed.decide(missed_call(), [], late, late,
                                 queue_calls=[self.call(30)], queue_fresh_until=late)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_redial_after_the_minute_does_not_hold(self):
        decision = missed.decide(missed_call(), [], FRESH, FRESH,
                                 queue_calls=[self.call(70)], queue_fresh_until=FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_stale_snapshot_still_counts_what_it_shows(self):
        # Снимок старый, но ответ оператора в нём уже есть — это случилось и не отменится.
        decision = missed.decide(missed_call(), [], FRESH, FRESH,
                                 queue_calls=[self.call(30, answered=True)],
                                 queue_fresh_until=DEADLINE - timedelta(seconds=1))
        self.assertEqual(decision.kind, missed.ANSWERED)

    def test_stale_snapshot_holds_instead_of_deciding_by_cdr_alone(self):
        # Мост присылает очереди, но в последних циклах журнал не ответил. «В очередях
        # никого» по старому снимку сказать нельзя — клиент может говорить прямо сейчас.
        stale = DEADLINE - timedelta(seconds=1)
        decision = missed.decide(missed_call(), [], FRESH, FRESH, queue_calls=[],
                                 queue_fresh_until=stale)
        self.assertEqual(decision.kind, missed.WAIT)
        late = DEADLINE + timedelta(seconds=missed.MAX_HOLD_SECONDS + 1)
        decision = missed.decide(missed_call(), [], late, late, queue_calls=[],
                                 queue_fresh_until=stale)
        self.assertEqual(decision.kind, missed.TRANSFER, 'но не дольше потолка ожидания')

    def test_empty_fresh_snapshot_lets_it_through(self):
        decision = missed.decide(missed_call(), [], FRESH, FRESH, queue_calls=[],
                                 queue_fresh_until=FRESH)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_bridge_that_never_sent_queues_is_decided_by_cdr_only(self):
        # Мост старой версии: списка не было ни разу — решаем по касаниям, как умеем.
        decision = missed.decide(missed_call(), [], FRESH, FRESH, queue_calls=None)
        self.assertEqual(decision.kind, missed.TRANSFER)

    def test_redial_counts_from_its_arrival_not_from_the_queue_entry(self):
        # Пришёл за 10 с до конца минуты, в очередь попал после приветствия в 26 с — это
        # перезвон в течение минуты: касанием он так и считался бы (по началу звонка).
        call = self.call(50, greeting=26)
        decision = missed.decide(missed_call(), [], FRESH, FRESH, queue_calls=[call],
                                 queue_fresh_until=FRESH)
        self.assertEqual(decision.kind, missed.WAIT)

    def test_queue_entry_is_the_fallback_when_linkedid_is_not_a_time(self):
        for linkedid in ('мусор', '9.9'):
            call = {'linkedid': linkedid, 'queued_at': END + timedelta(seconds=20),
                    'answered_at': END + timedelta(seconds=29), 'ended': False}
            decision = missed.decide(missed_call(), [], FRESH, FRESH, queue_calls=[call],
                                     queue_fresh_until=FRESH)
            self.assertEqual(decision.kind, missed.ANSWERED, linkedid)

    def test_arrival_alone_is_enough(self):
        arrival = END + timedelta(seconds=20)
        call = {'linkedid': '%d.3' % _epoch(arrival), 'queued_at': None,
                'answered_at': arrival + timedelta(seconds=9), 'ended': False}
        decision = missed.decide(missed_call(), [], FRESH, FRESH, queue_calls=[call],
                                 queue_fresh_until=FRESH)
        self.assertEqual(decision.kind, missed.ANSWERED)


class AmoTextTests(unittest.TestCase):
    def test_phone_is_written_as_77_mask(self):
        self.assertEqual(missed.amo_phone('+7 701 555 00 01'), '77015550001')
        self.assertEqual(missed.amo_phone('87015550001'), '77015550001')
        self.assertEqual(missed.amo_phone(''), '')

    def test_lead_name_tells_it_apart_from_the_station_integration(self):
        self.assertEqual(missed.lead_name(PHONE), '77015550001 - Пропущенный входящий')

    def test_note_names_time_line_park_and_wait(self):
        text = missed.note_text(missed_call(), park='Центр регистрации')
        self.assertIn('28.09.2026 в 10:00:00', text)
        self.assertIn('+7 747 555 00 78 (Центр регистрации)', text)
        self.assertIn('Ждал в очереди 40 с', text)
        self.assertIn('нужно перезвонить', text)

    def test_repeat_note_is_short(self):
        text = missed.note_text(missed_call(wait_seconds=None), park='', repeat=True)
        self.assertTrue(text.startswith('Повторный пропущенный входящий'))
        self.assertNotIn('нужно перезвонить', text)
        self.assertIn('Оператор не ответил', text)


if __name__ == '__main__':
    unittest.main()
