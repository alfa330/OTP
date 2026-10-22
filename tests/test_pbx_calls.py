# -*- coding: utf-8 -*-
"""Живое состояние звонков по событиям AMI (cdr/pbx_calls.py).

События — в том виде и порядке, в каком их отдаёт станция (Asterisk 16, снято 22.10.2026):
входящий через очередь, исходящий оператора набором `<префикс>*<номер>`, заявка автообзвона,
не принятый, сброс в очереди. Телефоны учебные (7XX555XXXX), номера операторов выдуманные.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import pbx_calls  # noqa: E402

T0 = datetime(2026, 10, 22, 12, 0, 0)


class Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


def ev(name, **fields):
    out = {'Event': name}
    out.update(fields)
    return out


CLIENT, TRUNK = '+77015550001', 'PJSIP/+77475550000_jana-00001a97'
LID = '1792000000.19203'


def incoming_to_queue(tracker, clock, ext='6726', answer=True):
    """Входящий на очередь 3034: транк → очередь → звон оператору → ответ."""
    out = []
    out += tracker.feed(ev('Newchannel', Channel=TRUNK, Uniqueid=LID, Linkedid=LID,
                           Context='from-trunk', Exten='+77470939675', CallerIDNum=CLIENT))
    clock.tick(7)
    out += tracker.feed(ev('QueueCallerJoin', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                           Position='1', Count='1'))
    clock.tick(2)
    out += tracker.feed(ev('AgentCalled', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                           Interface='Local/%s@from-queue/n' % ext, MemberName='Operator_One',
                           DestChannel='Local/%s@from-queue-00001a36;1' % ext))
    out += tracker.feed(ev('Newchannel', Channel='Local/%s@from-queue-00001a36;1' % ext,
                           Uniqueid=LID + '4', Linkedid=LID, Context='from-queue', Exten=ext))
    out += tracker.feed(ev('Newchannel', Channel='Local/%s@from-queue-00001a36;2' % ext,
                           Uniqueid=LID + '5', Linkedid=LID, Context='from-queue', Exten=ext))
    out += tracker.feed(ev('Newchannel', Channel='PJSIP/%s-00001a98' % ext, Uniqueid=LID + '6',
                           Linkedid=LID, Context='from-internal', Exten=''))
    out += tracker.feed(ev('DialBegin', Channel='Local/%s@from-queue-00001a36;2' % ext,
                           DestChannel='PJSIP/%s-00001a98' % ext, Uniqueid=LID + '5', Linkedid=LID,
                           DestUniqueid=LID + '6'))
    out += tracker.feed(ev('Newstate', Channel='PJSIP/%s-00001a98' % ext, ChannelState='5',
                           Uniqueid=LID + '6', Linkedid=LID))
    if not answer:
        return out
    clock.tick(3)
    out += tracker.feed(ev('DialEnd', Channel='Local/%s@from-queue-00001a36;2' % ext,
                           DestChannel='PJSIP/%s-00001a98' % ext, Uniqueid=LID + '5', Linkedid=LID,
                           DialStatus='ANSWER'))
    out += tracker.feed(ev('AgentConnect', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                           Interface='Local/%s@from-queue/n' % ext, MemberName='Operator_One',
                           HoldTime='5', RingTime='3',
                           DestChannel='Local/%s@from-queue-00001a36;1' % ext))
    out += tracker.feed(ev('BridgeEnter', Channel='PJSIP/%s-00001a98' % ext, Uniqueid=LID + '6',
                           Linkedid=LID, BridgeUniqueid='b1'))
    out += tracker.feed(ev('BridgeEnter', Channel=TRUNK, Uniqueid=LID, Linkedid=LID,
                           BridgeUniqueid='b2'))
    return out


def test_incoming_queue_call_goes_queued_ringing_talking():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    changes = incoming_to_queue(tracker, clock)
    states = [c['state'] for c in changes]
    assert states[0] == 'new'
    assert 'queued' in states and 'ringing' in states and states[-1] == 'talking'
    call = tracker.live()[0]
    assert call['direction'] == 'in'
    assert call['phone'] == '7015550001'
    assert call['did'] == '+77470939675'
    assert call['queue'] == '3034'
    assert call['queued_at'] == '2026-10-22T12:00:07'
    assert call['ringing_at'] == '2026-10-22T12:00:09'
    assert call['answered_at'] == '2026-10-22T12:00:12'
    assert call['ext'] == '6726'
    assert call['ringing'] == []
    assert call['wait_seconds'] == 5
    assert call['agent_channel'] == 'PJSIP/6726-00001a98'
    assert call['channel'] == TRUNK


def test_client_hangs_up_first_names_the_side_and_ends_the_call():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock)
    clock.tick(40)
    side = tracker.feed(ev('HangupRequest', Channel=TRUNK, Uniqueid=LID, Linkedid=LID))
    assert side[0]['hangup_side'] == 'client'
    changes = tracker.feed(ev('Hangup', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Cause='16'))
    assert len(changes) == 1
    call = changes[0]
    assert call['state'] == 'ended'
    assert call['ended_at'] == '2026-10-22T12:00:52'
    assert call['hangup_side'] == 'client'
    assert call['hangup_cause'] == 16
    # Хвост: отбой телефона оператора после — сторону не меняет.
    tracker.feed(ev('Hangup', Channel='PJSIP/6726-00001a98', Uniqueid=LID + '6', Linkedid=LID, Cause='16'))
    assert tracker.live()[0]['hangup_side'] == 'client'


def test_operator_hangup_first_is_operator_side_even_without_agentcomplete():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock)
    changes = tracker.feed(ev('HangupRequest', Channel='PJSIP/6726-00001a98', Uniqueid=LID + '6',
                              Linkedid=LID))
    assert changes[0]['hangup_side'] == 'operator'
    assert changes[0]['state'] == 'talking', 'запрос отбоя — ещё не отбой'
    # Станция отбивает транк РАНЬШЕ канала оператора, когда трубку положил оператор
    # (снято 22.10.2026) — порядок Hangup сторону не меняет.
    tracker.feed(ev('SoftHangupRequest', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Cause='16'))
    ended = tracker.feed(ev('Hangup', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Cause='16'))
    assert ended[0]['state'] == 'ended'
    assert ended[0]['hangup_side'] == 'operator'
    tracker.feed(ev('Hangup', Channel='PJSIP/6726-00001a98', Uniqueid=LID + '6', Linkedid=LID, Cause='16'))
    assert tracker.live()[0]['hangup_side'] == 'operator'


def test_agentcomplete_reason_wins_over_hangup_order():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock)
    tracker.feed(ev('AgentComplete', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                    Reason='caller', TalkTime='40'))
    tracker.feed(ev('HangupRequest', Channel='PJSIP/6726-00001a98', Uniqueid=LID + '6', Linkedid=LID))
    assert tracker.live()[0]['hangup_side'] == 'client'


def test_ringing_phone_hangup_is_not_an_operator_decision():
    """Очередь сняла вызов с одного оператора и отдала другому: его телефон отбился
    причиной 26, разговора не было — сторона пустая, звонок жив."""
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock, ext='6417', answer=False)
    assert tracker.live()[0]['ringing'] == ['6417']
    tracker.feed(ev('AgentRingNoAnswer', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                    Interface='Local/6417@from-queue/n', MemberName='Operator_Two', RingTime='15000'))
    changes = tracker.feed(ev('Hangup', Channel='PJSIP/6417-00001a98', Uniqueid=LID + '6',
                              Linkedid=LID, Cause='26'))
    call = tracker.live()[0]
    assert call['ringing'] == []
    assert call['state'] == 'queued'
    assert call['hangup_side'] == ''
    assert changes == [] or changes[0]['state'] != 'ended'


def test_abandoned_in_queue():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock, answer=False)
    tracker.feed(ev('QueueCallerAbandon', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                    Position='1', OriginalPosition='1', HoldTime='21'))
    changes = tracker.feed(ev('Hangup', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Cause='16'))
    call = changes[0]
    assert call['state'] == 'ended'
    assert call['answered_at'] == ''
    assert call['dial_status'] == 'ABANDON'
    assert call['hangup_side'] == '', 'разговора не было — сторону не ставим'


def outgoing(tracker, clock, ext='6737', exten='3333*+77015550002'):
    lid = '1792000001.100'
    out = []
    out += tracker.feed(ev('Newchannel', Channel='PJSIP/%s-000016a1' % ext, Uniqueid=lid, Linkedid=lid,
                           Context='from-internal', Exten=exten, CallerIDNum=ext))
    clock.tick(1)
    out += tracker.feed(ev('Newchannel', Channel='PJSIP/+77475550022_jana-000016a2', Uniqueid=lid + '1',
                           Linkedid=lid, Context='from-internal', Exten='s'))
    out += tracker.feed(ev('DialBegin', Channel='PJSIP/%s-000016a1' % ext, Uniqueid=lid, Linkedid=lid,
                           Context='macro-dialout-trunk', Exten='s',
                           DestChannel='PJSIP/+77475550022_jana-000016a2', DestUniqueid=lid + '1',
                           DestCallerIDNum='+77015550002', DialString='+77015550002@jana'))
    clock.tick(2)
    out += tracker.feed(ev('Newstate', Channel='PJSIP/+77475550022_jana-000016a2', ChannelState='5',
                           Uniqueid=lid + '1', Linkedid=lid))
    return lid, out


def test_outgoing_call_parses_prefix_and_phone_from_dialed_exten():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    lid, changes = outgoing(tracker, clock)
    call = tracker.live()[0]
    assert call['direction'] == 'out'
    assert call['ext'] == '6737'
    assert call['dial_prefix'] == '3333'
    assert call['phone'] == '7015550002'
    assert call['state'] == 'ringing'
    assert call['ringing_at'] == '2026-10-22T12:00:01', 'набор пошёл — DialBegin'
    clock.tick(5)
    tracker.feed(ev('DialEnd', Channel='PJSIP/6737-000016a1', Uniqueid=lid, Linkedid=lid,
                    DestChannel='PJSIP/+77475550022_jana-000016a2', DialStatus='ANSWER'))
    tracker.feed(ev('BridgeEnter', Channel='PJSIP/6737-000016a1', Uniqueid=lid, Linkedid=lid))
    call = tracker.live()[0]
    assert call['state'] == 'talking'
    assert call['answered_at'] == '2026-10-22T12:00:08'
    assert call['ext'] == '6737', 'мост оператора-инициатора не делает его «ответившим» другим номером'
    # Клиент положил трубку: запрос отбоя пришёл на транк.
    tracker.feed(ev('HangupRequest', Channel='PJSIP/+77475550022_jana-000016a2', Uniqueid=lid + '1',
                    Linkedid=lid))
    tracker.feed(ev('Hangup', Channel='PJSIP/+77475550022_jana-000016a2', Uniqueid=lid + '1', Linkedid=lid,
                    Cause='16'))
    ended = tracker.feed(ev('Hangup', Channel='PJSIP/6737-000016a1', Uniqueid=lid, Linkedid=lid, Cause='16'))
    assert ended[0]['state'] == 'ended'
    assert ended[0]['hangup_side'] == 'client'


def test_outgoing_busy_keeps_dial_status():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    lid, _ = outgoing(tracker, clock)
    tracker.feed(ev('DialEnd', Channel='PJSIP/6737-000016a1', Uniqueid=lid, Linkedid=lid,
                    DestChannel='PJSIP/+77475550022_jana-000016a2', DialStatus='BUSY'))
    tracker.feed(ev('Hangup', Channel='PJSIP/+77475550022_jana-000016a2', Uniqueid=lid + '1', Linkedid=lid,
                    Cause='17'))
    ended = tracker.feed(ev('Hangup', Channel='PJSIP/6737-000016a1', Uniqueid=lid, Linkedid=lid, Cause='17'))
    assert ended[0]['dial_status'] == 'BUSY'
    assert ended[0]['answered_at'] == ''
    assert ended[0]['hangup_side'] == ''
    assert ended[0]['hangup_cause'] == 17


def test_originated_call_without_exten_learns_direction_from_dialbegin():
    """Канал, поднятый нашим Originate: Exten у Newchannel пуст, направление даёт набор."""
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    tracker.expect_originate('otp-cmd-15', 15)
    lid = '1792000002.7'
    tracker.feed(ev('OriginateResponse', ActionID='otp-cmd-15', Response='Success', Uniqueid=lid,
                    Channel='PJSIP/6726-00002001', Exten='3333*77015550003'))
    tracker.feed(ev('Newchannel', Channel='PJSIP/6726-00002001', Uniqueid=lid, Linkedid=lid,
                    Context='from-internal', Exten='', CallerIDNum='6726'))
    tracker.feed(ev('Newchannel', Channel='PJSIP/+77475550022_jana-00002002', Uniqueid=lid + '1',
                    Linkedid=lid, Context='from-internal', Exten='s'))
    tracker.feed(ev('DialBegin', Channel='PJSIP/6726-00002001', Uniqueid=lid, Linkedid=lid,
                    Context='macro-dialout-trunk', Exten='s',
                    DestChannel='PJSIP/+77475550022_jana-00002002', DestUniqueid=lid + '1',
                    DestCallerIDNum='+77015550003', DialString='+77015550003@jana'))
    call = tracker.live()[0]
    assert call['origin'] == 'portal'
    assert call['command_id'] == 15
    assert call['direction'] == 'out'
    assert call['ext'] == '6726'
    assert call['phone'] == '7015550003'
    assert call['state'] == 'ringing'


def test_originate_failure_does_not_create_a_call():
    tracker = pbx_calls.CallTracker(now=Clock())
    tracker.expect_originate('otp-cmd-16', 16)
    changes = tracker.feed(ev('OriginateResponse', ActionID='otp-cmd-16', Response='Failure', Reason='3',
                              Uniqueid='<null>'))
    assert changes == []
    assert tracker.live() == []
    # Чужой OriginateResponse (itgrix, FOP2) нас не касается.
    assert tracker.feed(ev('OriginateResponse', ActionID='foreign', Response='Success', Uniqueid='9.9')) == []


AUTO_LID = '1792000003.50'
AUTO_REQ = 'Local/3016@ext-to-queue-00001a34;2'


def autodial_request(tracker, clock):
    """Заявка автообзвона в том порядке, в каком её отдаёт станция (снято 22.10.2026): заявка
    встаёт в очередь с номером клиента и ждёт оператора; занятому телефон «BUSY» раз в 5 с."""
    tracker.feed(ev('Newchannel', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID,
                    Linkedid=AUTO_LID, Context='ext-to-queue', Exten='3016'))
    tracker.feed(ev('Newchannel', Channel=AUTO_REQ, Uniqueid=AUTO_LID + '1', Linkedid=AUTO_LID,
                    Context='ext-to-queue', Exten='3016'))
    tracker.feed(ev('QueueCallerJoin', Channel=AUTO_REQ, Uniqueid=AUTO_LID + '1', Linkedid=AUTO_LID,
                    Queue='3016', CallerIDNum='+77015550004', Position='1', Count='1'))


def test_autodial_request_waits_for_an_operator_and_is_not_answered_by_joining_the_queue():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    autodial_request(tracker, clock)
    call = tracker.live()[0]
    assert call['direction'] == 'autodial'
    assert call['queue'] == '3016'
    assert call['phone'] == '7015550004', 'номер клиента приходит с заявкой'
    assert call['state'] == 'queued'
    assert call['answered_at'] == '', 'встать в очередь — не значит, что клиент ответил'
    # Занятый оператор: попытка раз в пять секунд — не звонок человеку и не изменение звонка.
    for _ in range(3):
        clock.tick(5)
        assert tracker.feed(ev('AgentCalled', Channel=AUTO_REQ, Uniqueid=AUTO_LID + '1', Linkedid=AUTO_LID,
                               Queue='3016', Interface='Local/6726@from-queue/n',
                               DestChannel='Local/6726@from-queue-00001a8a;1')) == []
        assert tracker.feed(ev('AgentRingNoAnswer', Channel=AUTO_REQ, Uniqueid=AUTO_LID + '1',
                               Linkedid=AUTO_LID, Queue='3016', Interface='Local/6726@from-queue/n')) == []
    assert tracker.live()[0]['ringing'] == []


def reserve_and_dial(tracker, clock):
    clock.tick(30)
    tracker.feed(ev('AgentConnect', Channel=AUTO_REQ, Uniqueid=AUTO_LID + '1', Linkedid=AUTO_LID, Queue='3016',
                    Interface='Local/6728@from-queue/n', MemberName='Operator_Three', HoldTime='30'))
    tracker.feed(ev('BridgeEnter', Channel='PJSIP/6728-00002001', Uniqueid=AUTO_LID + '3', Linkedid=AUTO_LID))
    call = tracker.live()[0]
    assert call['ext'] == '6728'
    assert call['agent_channel'] == 'PJSIP/6728-00002001'
    assert call['state'] == 'ringing', 'оператор на линии, клиента набирают — это ещё не разговор'
    assert call['answered_at'] == ''
    clock.tick(1)
    tracker.feed(ev('DialBegin', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID,
                    Linkedid=AUTO_LID, Exten='7778*77015550004',
                    DestChannel='PJSIP/+77475550078-00001a88', DestUniqueid=AUTO_LID + '4',
                    DestCallerIDNum='7778*77015550004'))
    call = tracker.live()[0]
    assert call['dial_prefix'] == '7778'
    assert call['ringing_at'] == '2026-10-22T12:00:31'
    assert call['answered_at'] == ''


def test_autodial_operator_is_reserved_first_then_the_client_is_dialled():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    autodial_request(tracker, clock)
    reserve_and_dial(tracker, clock)


def test_autodial_client_did_not_answer():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    autodial_request(tracker, clock)
    reserve_and_dial(tracker, clock)
    clock.tick(25)
    tracker.feed(ev('DialEnd', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID, Linkedid=AUTO_LID,
                    DestChannel='PJSIP/+77475550078-00001a88', DialStatus='CANCEL'))
    tracker.feed(ev('HangupRequest', Channel='PJSIP/6728-00002001', Uniqueid=AUTO_LID + '3', Linkedid=AUTO_LID))
    ended = tracker.feed(ev('Hangup', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID,
                            Linkedid=AUTO_LID, Cause='16'))
    call = ended[0]
    assert call['state'] == 'ended'
    assert call['answered_at'] == ''
    assert call['dial_status'] == 'CANCEL'
    assert call['hangup_side'] == '', 'разговора не было — и стороны отбоя нет'


def test_autodial_client_answers_and_hangs_up():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    autodial_request(tracker, clock)
    reserve_and_dial(tracker, clock)
    clock.tick(8)
    tracker.feed(ev('DialEnd', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID, Linkedid=AUTO_LID,
                    DestChannel='PJSIP/+77475550078-00001a88', DialStatus='ANSWER'))
    tracker.feed(ev('BridgeEnter', Channel='PJSIP/+77475550078-00001a88', Uniqueid=AUTO_LID + '4', Linkedid=AUTO_LID))
    call = tracker.live()[0]
    assert call['state'] == 'talking'
    assert call['answered_at'] == '2026-10-22T12:00:39'
    assert call['ext'] == '6728'
    clock.tick(60)
    tracker.feed(ev('HangupRequest', Channel='PJSIP/+77475550078-00001a88', Uniqueid=AUTO_LID + '4',
                    Linkedid=AUTO_LID))
    assert tracker.live()[0]['hangup_side'] == 'client'


def test_the_same_event_twice_is_not_a_change():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock)
    again = ev('AgentConnect', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
               Interface='Local/6726@from-queue/n', MemberName='Operator_One', HoldTime='5')
    assert tracker.feed(again) == []
    before = tracker.live()[0]['seq']
    assert tracker.feed(ev('BridgeEnter', Channel=TRUNK, Uniqueid=LID, Linkedid=LID)) == []
    assert tracker.live()[0]['seq'] == before


def test_internal_call_between_operators():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    lid = '1792000004.1'
    tracker.feed(ev('Newchannel', Channel='PJSIP/6715-00003001', Uniqueid=lid, Linkedid=lid,
                    Context='from-internal', Exten='6726', CallerIDNum='6715'))
    tracker.feed(ev('Newchannel', Channel='PJSIP/6726-00003002', Uniqueid=lid + '1', Linkedid=lid,
                    Context='from-internal', Exten=''))
    tracker.feed(ev('DialBegin', Channel='PJSIP/6715-00003001', Uniqueid=lid, Linkedid=lid,
                    DestChannel='PJSIP/6726-00003002', DestUniqueid=lid + '1'))
    call = tracker.live()[0]
    assert call['direction'] == 'internal'
    assert call['phone'] == ''
    assert call['ext'] == '6715'
    assert call['ringing'] == ['6726']
    assert call['did'] == '6726'
    clock.tick(4)
    tracker.feed(ev('DialEnd', Channel='PJSIP/6715-00003001', Uniqueid=lid, Linkedid=lid,
                    DestChannel='PJSIP/6726-00003002', DialStatus='ANSWER'))
    tracker.feed(ev('BridgeEnter', Channel='PJSIP/6726-00003002', Uniqueid=lid + '1', Linkedid=lid))
    tracker.feed(ev('BridgeEnter', Channel='PJSIP/6715-00003001', Uniqueid=lid, Linkedid=lid))
    call = tracker.live()[0]
    assert call['state'] == 'talking'
    assert (call['ext'], call['peer_ext']) == ('6715', '6726'), 'в разговоре оба'
    assert call['agent_channel'] == 'PJSIP/6715-00003001'
    tracker.feed(ev('HangupRequest', Channel='PJSIP/6726-00003002', Uniqueid=lid + '1', Linkedid=lid))
    assert tracker.live()[0]['hangup_side'] == 'operator'


def test_expire_forgets_ended_calls_after_the_keep_window():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock, keep_ended_seconds=60)
    incoming_to_queue(tracker, clock)
    tracker.feed(ev('Hangup', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Cause='16'))
    tracker.feed(ev('Hangup', Channel='PJSIP/6726-00001a98', Uniqueid=LID + '6', Linkedid=LID, Cause='16'))
    clock.tick(30)
    assert tracker.expire() == []
    clock.tick(31)
    assert tracker.expire() == [LID]
    assert tracker.live() == []
    assert tracker._channels == {}


def test_recording_file_bumps_the_call():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock)
    before = tracker.live()[0]['seq']
    changes = tracker.set_recording(LID, 'q-3034-77015550001-20261022-120012-%s.wav' % LID)
    assert changes[0]['recording_file'].startswith('q-3034-')
    assert changes[0]['seq'] > before
    assert tracker.set_recording(LID, changes[0]['recording_file']) == [], 'то же имя — не изменение'


def test_unrelated_events_are_ignored_and_snapshots_are_copies():
    tracker = pbx_calls.CallTracker(now=Clock())
    assert tracker.feed(ev('QueueMemberStatus', Queue='3017', Status='6')) == []
    assert tracker.feed(ev('RTCPSent', Channel='PJSIP/6726-1')) == []
    assert tracker.feed(ev('Hangup', Channel='PJSIP/x-1', Uniqueid='no.such', Linkedid='no.such')) == []
    changes = tracker.feed(ev('Newchannel', Channel=TRUNK, Uniqueid=LID, Linkedid=LID,
                              Context='from-trunk', Exten='7475550000', CallerIDNum=CLIENT))
    changes[0]['phone'] = 'tampered'
    assert tracker.live()[0]['phone'] == '7015550001'


@pytest.mark.parametrize('exten, expected', [
    ('3333*+77015550001', ('3333', '7015550001')),
    ('5050*77015550001', ('5050', '7015550001')),
    ('7778*87015550001', ('7778', '7015550001')),
    ('6726', ('', '')),
    ('*43', ('', '')),
    ('', ('', '')),
])
def test_parse_dial(exten, expected):
    assert pbx_calls.parse_dial(exten) == expected


def test_dial_string_builds_station_dial_and_rejects_garbage():
    assert pbx_calls.dial_string('3333', '+7 (701) 555-00-01') == '3333*77015550001'
    assert pbx_calls.dial_string('5050', '87015550001') == '5050*77015550001'
    with pytest.raises(ValueError):
        pbx_calls.dial_string('33', '7015550001')
    with pytest.raises(ValueError):
        pbx_calls.dial_string('3333', '555')


@pytest.mark.parametrize('name, kind, ext', [
    ('PJSIP/6726-00001a98', 'ext', '6726'),
    ('PJSIP/+77475550022_jana-000016a2', 'trunk', ''),
    ('PJSIP/yVnnduj2_70425550042-0000068e', 'trunk', ''),
    ('Local/6726@from-queue-00001a36;1', 'local', ''),
    ('Message/ast_msg_queue', 'other', ''),
])
def test_channel_kind(name, kind, ext):
    assert pbx_calls.channel_kind(name) == kind
    assert pbx_calls.ext_of_channel(name) == ext


def test_ext_of_interface():
    assert pbx_calls.ext_of_interface('Local/6726@from-queue/n') == '6726'
    assert pbx_calls.ext_of_interface('PJSIP/6726') == '6726'
    assert pbx_calls.ext_of_interface('PJSIP/6726-00001a98') == '6726'
    assert pbx_calls.ext_of_interface('Local/3016@ext-to-queue-00001a34;1') == '3016'
    assert pbx_calls.ext_of_interface('') == ''


def test_bootstrap_snapshot_of_a_queued_call_knows_the_queue_not_a_fake_did():
    """CoreShowChannels при входе: транк клиента уже в очереди, Exten = номер очереди."""
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    tracker.feed(ev('Newchannel', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Context='ext-queues',
                    Exten='3034', CallerIDNum=CLIENT), at=datetime(2026, 10, 22, 11, 58, 30))
    call = tracker.live()[0]
    assert call['queue'] == '3034'
    assert call['did'] == ''
    assert call['started_at'] == '2026-10-22T11:58:30'
    assert call['direction'] == 'in'


def test_queue_attempts_to_a_busy_operator_change_nothing():
    """Снято 22.10.2026: к занятому оператору очередь «предлагает» звонок каждые 2 с, Local сразу
    уходит в BUSY, телефон не набирается. Ни «звонит», ни изменения звонка — ни одной пачки."""
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    tracker.feed(ev('Newchannel', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Context='from-pstn',
                    Exten='7475550000', CallerIDNum=CLIENT))
    tracker.feed(ev('QueueCallerJoin', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034'))
    for attempt in range(3):
        clock.tick(2)
        local = 'Local/6726@from-queue-00001a8%d' % attempt
        first, second = '%s.%d1' % (LID, attempt), '%s.%d2' % (LID, attempt)
        assert tracker.feed(ev('Newchannel', Channel=local + ';1', Uniqueid=first,
                               Linkedid=LID, Context='from-queue', Exten='6726')) == []
        assert tracker.feed(ev('Newchannel', Channel=local + ';2', Uniqueid=second,
                               Linkedid=LID, Context='from-queue', Exten='6726')) == []
        assert tracker.feed(ev('AgentCalled', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                               Interface='Local/6726@from-queue/n', DestChannel=local + ';1')) == []
        assert tracker.feed(ev('DialBegin', Channel=TRUNK, Uniqueid=LID, Linkedid=LID,
                               DestChannel=local + ';1', DialString='Local/6726@from-queue/n')) == []
        assert tracker.feed(ev('AgentRingNoAnswer', Channel=TRUNK, Uniqueid=LID, Linkedid=LID, Queue='3034',
                               Interface='Local/6726@from-queue/n', RingTime='0')) == []
        assert tracker.feed(ev('Hangup', Channel=local + ';1', Uniqueid=first, Linkedid=LID, Cause='0')) == []
        assert tracker.feed(ev('Hangup', Channel=local + ';2', Uniqueid=second, Linkedid=LID, Cause='17')) == []
    call = tracker.live()[0]
    assert (call['state'], call['ringing'], call['ringing_at']) == ('queued', [], '')


def test_answered_autodial_survives_the_local_optimization():
    """После ответа клиента станция сводит пару Local накоротко: Local-каналы (и первый канал
    звонка) отбиваются, а клиент и оператор говорят дальше с тем же linkedid."""
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    autodial_request(tracker, clock)
    reserve_and_dial(tracker, clock)
    tracker.feed(ev('Newchannel', Channel='PJSIP/6728-00002001', Uniqueid=AUTO_LID + '3', Linkedid=AUTO_LID,
                    Context='from-internal', Exten='s'))
    tracker.feed(ev('Newchannel', Channel='PJSIP/+77475550078-00001a88', Uniqueid=AUTO_LID + '4',
                    Linkedid=AUTO_LID, Context='from-internal', Exten='s'))
    clock.tick(8)
    tracker.feed(ev('DialEnd', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID, Linkedid=AUTO_LID,
                    DestChannel='PJSIP/+77475550078-00001a88', DialStatus='ANSWER'))
    # Оптимизация Local: оба Local отбиваются, первым — первый канал звонка.
    tracker.feed(ev('Hangup', Channel='Local/3016@ext-to-queue-00001a34;1', Uniqueid=AUTO_LID,
                    Linkedid=AUTO_LID, Cause='16'))
    tracker.feed(ev('Hangup', Channel=AUTO_REQ, Uniqueid=AUTO_LID + '1', Linkedid=AUTO_LID, Cause='16'))
    assert tracker.live()[0]['state'] == 'talking', 'разговор продолжается после ухода Local'
    clock.tick(40)
    tracker.feed(ev('HangupRequest', Channel='PJSIP/+77475550078-00001a88', Uniqueid=AUTO_LID + '4',
                    Linkedid=AUTO_LID))
    tracker.feed(ev('Hangup', Channel='PJSIP/+77475550078-00001a88', Uniqueid=AUTO_LID + '4',
                    Linkedid=AUTO_LID, Cause='16'))
    assert tracker.live()[0]['state'] == 'talking', 'у оператора канал ещё жив'
    ended = tracker.feed(ev('Hangup', Channel='PJSIP/6728-00002001', Uniqueid=AUTO_LID + '3',
                            Linkedid=AUTO_LID, Cause='16'))
    assert (ended[0]['state'], ended[0]['hangup_side']) == ('ended', 'client')
    assert ended[0]['ended_at'] == '2026-10-22T12:01:19'


def test_seed_restores_a_waiting_caller_and_an_outgoing_talk_and_marks_them_partial():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    items = [
        # Клиент ждёт в очереди 3034 полторы минуты (моста нет).
        {'Channel': TRUNK, 'Uniqueid': LID, 'Linkedid': LID, 'Context': 'ext-queues', 'Exten': '3034',
         'CallerIDNum': CLIENT, 'Duration': '00:01:30', 'BridgeId': ''},
        # Исходящий разговор: канал оператора в macro-dialout-trunk (Exten 's') и транк с набором.
        {'Channel': 'PJSIP/6737-000016c2', 'Uniqueid': '9.1', 'Linkedid': '9.1', 'Context': 'macro-dialout-trunk',
         'Exten': 's', 'CallerIDNum': '6737', 'Duration': '00:00:50', 'BridgeId': 'b9'},
        {'Channel': 'PJSIP/77475550033_itaxi_OS-000016c3', 'Uniqueid': '9.2', 'Linkedid': '9.1',
         'Context': 'from-trunk-to-header', 'Exten': '', 'CallerIDNum': '3333*+77015550005',
         'Duration': '00:00:49', 'BridgeId': 'b9'},
        # Отбился, пока список выдавался, — не воскрешаем.
        {'Channel': 'PJSIP/+77475550000_jana-0000ffff', 'Uniqueid': '8.1', 'Linkedid': '8.1',
         'Context': 'ext-queues', 'Exten': '3034', 'CallerIDNum': '+77015550009', 'Duration': '00:00:10'},
    ]
    changed = tracker.seed(items, now=T0, skip_uniqueids={'8.1'})
    calls = {c['linkedid']: c for c in changed}
    assert set(calls) == {LID, '9.1'}
    waiting = calls[LID]
    assert (waiting['state'], waiting['queue'], waiting['queued_at'], waiting['did']) == \
        ('queued', '3034', '2026-10-22T11:58:30', '')
    assert waiting['partial'] is True
    talk = calls['9.1']
    assert (talk['direction'], talk['phone'], talk['dial_prefix'], talk['ext'], talk['state']) == \
        ('out', '7015550005', '3333', '6737', 'talking')
    assert talk['started_at'] == '2026-10-22T11:59:10'
    assert talk['partial'] is True
    assert pbx_calls.CallTracker(now=clock).seed([], now=T0) == []


def test_reconcile_closes_calls_whose_hangup_was_lost():
    clock = Clock()
    tracker = pbx_calls.CallTracker(now=clock)
    incoming_to_queue(tracker, clock)
    known = tracker.channel_ids()
    # Станция говорит: из каналов звонка жив только канал оператора (отбой транка потерян).
    still = {LID + '6'}
    changed = tracker.reconcile(still, known)
    assert changed and changed[0]['state'] == 'ended'
    assert tracker.reconcile(still, known) == [], 'повторная сверка ничего не меняет'


def test_forget_originate_drops_the_pending_entry():
    tracker = pbx_calls.CallTracker(now=Clock())
    tracker.expect_originate('otp-cmd-3', 3)
    tracker.forget_originate('otp-cmd-3')
    assert tracker.feed(ev('OriginateResponse', ActionID='otp-cmd-3', Response='Success', Uniqueid='1.1')) == []
