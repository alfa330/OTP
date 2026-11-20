# -*- coding: utf-8 -*-
"""Показатели чатов СЗоВ по Syntony (syntony_chats/metrics.py) — без базы и сети.

Правила — решения владельца 20.11.2026 (SZOV-12): онлайн — ответ в Syntony за 15 минут;
первая реакция — первый ответ сотрудника, при передаче ИИ — от момента передачи; «всего» —
диалоги, где водитель писал в окне, «поступивших» — начатые в нём. Люди и номера выдуманы.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from syntony_chats import metrics  # noqa: E402
from syntony_chats.metrics import Conversation, Message  # noqa: E402

TZ = timezone(timedelta(hours=5))
DAY = datetime(2026, 11, 20, tzinfo=TZ)
SZOV = 'chan-szov'
OTHER = 'chan-op'


def at(hour, minute=0, second=0, day=DAY):
    return day + timedelta(hours=hour, minutes=minute, seconds=second)


_counter = [0]


def msg(conversation, moment, kind, author=None, delete_scope=None):
    """kind: driver | human | ai | system."""
    _counter[0] += 1
    direction = 'inbound' if kind == 'driver' else 'outbound'
    sender = {'driver': 'contact', 'human': 'human', 'ai': 'ai', 'system': 'system'}[kind]
    return Message(id='m%05d' % _counter[0], conversation_id=conversation, direction=direction,
                   sender_type=sender, author_name=author, created_at=moment,
                   delete_scope=delete_scope)


def conv(cid, created, channel=SZOV, handoff=None, is_test=False):
    return Conversation(id=cid, channel_id=channel, created_at=created,
                        handoff_requested_at=handoff, is_test=is_test)


class FirstReactionTests(unittest.TestCase):
    def test_plain_chat_first_reply_from_first_driver_message(self):
        c = conv('c1', at(10))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 0, 0), 'driver'),
            msg('c1', at(10, 0, 30), 'driver'),
            msg('c1', at(10, 2, 0), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 120)
        self.assertEqual(facts.first_reaction_author, 'Ахметова Айгерим')
        # Ход — подряд идущие сообщения водителя: ждал он с первого из них.
        self.assertEqual(facts.turns, [(at(10), at(10, 2), 'Ахметова Айгерим')])

    def test_handoff_before_first_human_reply_counts_from_handoff(self):
        c = conv('c1', at(10), handoff=at(10, 5))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 0), 'driver'),
            msg('c1', at(10, 1), 'ai'),
            msg('c1', at(10, 4), 'driver'),
            msg('c1', at(10, 5), 'ai'),          # «передаю специалисту»
            msg('c1', at(10, 8), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 180)

    def test_handoff_after_first_human_reply_is_ignored(self):
        # Syntony помнит только ПОСЛЕДНЮЮ передачу: ночью ответил человек, днём ИИ передал снова.
        c = conv('c1', at(0), handoff=at(14))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(0, 0), 'driver'),
            msg('c1', at(0, 9), 'human', 'Ахметова Айгерим'),
            msg('c1', at(13, 59), 'driver'),
            msg('c1', at(14, 1), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 9 * 60)

    def test_ai_only_chat_has_no_first_reaction_and_no_turns(self):
        c = conv('c1', at(10))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 0), 'driver'),
            msg('c1', at(10, 1), 'ai'),
            msg('c1', at(10, 3), 'driver'),
            msg('c1', at(10, 4), 'ai'),
        ])
        self.assertIsNone(facts.first_reaction_seconds)
        self.assertEqual(facts.turns, [])
        self.assertEqual(len(facts.driver_times), 2)

    def test_ai_then_human_without_handoff_counts_from_first_driver_message(self):
        # Буквально по решению: «от первого сообщения водителя» (передачи не было).
        c = conv('c1', at(10))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 0), 'driver'),
            msg('c1', at(10, 1), 'ai'),
            msg('c1', at(10, 3), 'driver'),
            msg('c1', at(10, 6), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 6 * 60)
        # А ход, отвеченный человеком, — второй: с 10:03.
        self.assertEqual(facts.turns, [(at(10, 3), at(10, 6), 'Ахметова Айгерим')])

    def test_proactive_message_before_driver_is_not_a_reply(self):
        c = conv('c1', at(9))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(9, 0), 'human', 'Ахметова Айгерим'),
            msg('c1', at(9, 30), 'driver'),
            msg('c1', at(9, 32), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 120)
        self.assertEqual(facts.turns, [(at(9, 30), at(9, 32), 'Ахметова Айгерим')])
        self.assertEqual(len(facts.replies), 2)

    def test_system_lines_do_not_break_or_answer_a_turn(self):
        c = conv('c1', at(10))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 0), 'driver'),
            msg('c1', at(10, 1), 'system'),
            msg('c1', at(10, 5), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 300)
        self.assertEqual(len(facts.turns), 1)

    def test_unsent_message_is_not_a_reply(self):
        c = conv('c1', at(10))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 0), 'driver'),
            msg('c1', at(10, 1), 'human', 'Ахметова Айгерим', delete_scope='unsent'),
            msg('c1', at(10, 7), 'human', 'Ахметова Айгерим'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 7 * 60)

    def test_unordered_input_is_sorted(self):
        c = conv('c1', at(10))
        facts = metrics.conversation_facts(c, [
            msg('c1', at(10, 4), 'human', 'Ахметова Айгерим'),
            msg('c1', at(10, 0), 'driver'),
        ])
        self.assertEqual(facts.first_reaction_seconds, 240)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.conversations = {
            'old': conv('old', at(22, day=DAY - timedelta(days=1))),     # начат вчера
            'new': conv('new', at(10)),
            'ai': conv('ai', at(10, 30)),
            'test': conv('test', at(10), is_test=True),
            'op': conv('op', at(10), channel=OTHER),
        }
        self.messages = [
            msg('old', at(22, day=DAY - timedelta(days=1)), 'driver'),
            msg('old', at(22, 5, day=DAY - timedelta(days=1)), 'human', 'Ахметова Айгерим'),
            msg('old', at(10, 10), 'driver'),
            msg('old', at(10, 20), 'human', 'Ахметова Айгерим'),          # ход 10 мин
            msg('new', at(10, 0), 'driver'),
            msg('new', at(10, 2), 'human', 'Бекова Динара'),              # первая реакция 2 мин
            msg('new', at(11, 0), 'driver'),
            msg('new', at(11, 4), 'human', 'Бекова Динара'),              # ход 4 мин (другой час)
            msg('ai', at(10, 30), 'driver'),
            msg('ai', at(10, 31), 'ai'),
            msg('test', at(10, 0), 'driver'),
            msg('test', at(10, 1), 'human', 'Бекова Динара'),
            msg('op', at(10, 0), 'driver'),
            msg('op', at(10, 3), 'human', 'Ахметова Айгерим'),
        ]
        self.facts, self.activity = metrics.analyze(
            self.conversations, self.messages, channels={SZOV})

    def test_only_szov_channels_and_no_test_numbers_in_chats(self):
        self.assertEqual(set(self.facts), {'old', 'new', 'ai'})

    def test_activity_counts_replies_on_any_channel_and_test_numbers(self):
        # Онлайн — ответ на ЛЮБОМ номере сети; ответ на тестовый номер — тоже работа.
        self.assertEqual(self.activity['Ахметова Айгерим'],
                         [at(22, 5, day=DAY - timedelta(days=1)), at(10, 3), at(10, 20)])
        self.assertEqual(self.activity['Бекова Динара'], [at(10, 1), at(10, 2), at(11, 4)])

    def test_total_and_new_for_day_and_hour(self):
        self.assertEqual(metrics.chat_counts(self.facts, DAY, DAY + timedelta(days=1)), (3, 2))
        self.assertEqual(metrics.chat_counts(self.facts, at(10), at(11)), (3, 2))
        self.assertEqual(metrics.chat_counts(self.facts, at(11), at(12)), (1, 0))

    def test_window_sums_first_over_new_inner_per_conversation(self):
        sums = metrics.window_sums(self.facts, DAY, DAY + timedelta(days=1))
        self.assertEqual(sums['chats'], 3)
        # Первая реакция — только у начатых сегодня и отвеченных людьми: 'new' (2 мин).
        self.assertEqual((sums['first_sum'], sums['first_count']), (120.0, 1))
        # Внутри: 'old' — 10 мин; 'new' — среднее (2 + 4) / 2 = 3 мин; сумма по диалогам.
        self.assertEqual((sums['inner_sum'], sums['inner_count']), (600.0 + 180.0, 2))
        self.assertEqual(metrics.average(sums, 'inner'), 390.0)

    def test_hour_slices_inner_by_turn_start(self):
        sums = metrics.window_sums(self.facts, at(11), at(12))
        self.assertEqual((sums['inner_sum'], sums['inner_count']), (240.0, 1))
        self.assertEqual(sums['first_count'], 0)

    def test_turn_started_yesterday_belongs_to_yesterday(self):
        # Водитель писал вчера в 23:50 и сегодня в 00:05, ответили в 00:20: ход один и начат
        # вчера — в сегодняшнее «внутри чата» он не идёт, во вчерашнее (выгрузка дня) — идёт.
        yesterday = DAY - timedelta(days=1)
        conversations = {'x': conv('x', at(23, 0, day=yesterday))}
        messages = [msg('x', at(23, 50, day=yesterday), 'driver'),
                    msg('x', at(0, 5), 'driver'),
                    msg('x', at(0, 20), 'human', 'Бекова Динара')]
        facts, _ = metrics.analyze(conversations, messages, channels={SZOV})
        today = metrics.window_sums(facts, DAY, DAY + timedelta(days=1))
        self.assertEqual((today['chats'], today['inner_count']), (1, 0))
        before = metrics.window_sums(facts, yesterday, DAY)
        self.assertEqual((before['inner_sum'], before['inner_count']), (30 * 60.0, 1))

    def test_average_none_when_nothing_to_measure(self):
        self.assertIsNone(metrics.average({'first_sum': 0, 'first_count': 0}, 'first'))
        self.assertIsNone(metrics.average(None, 'inner'))

    def test_operator_sums(self):
        sums = metrics.operator_sums(self.facts, DAY, DAY + timedelta(days=1), lambda name: name)
        self.assertEqual(sums['Бекова Динара']['chats'], 1)
        self.assertEqual(sums['Бекова Динара']['first_count'], 1)
        self.assertEqual(sums['Бекова Динара']['inner_sum'], 180.0)
        self.assertEqual(sums['Ахметова Айгерим']['chats'], 1)      # только 'old' (СЗоВ)
        self.assertEqual(sums['Ахметова Айгерим']['first_count'], 0)
        self.assertEqual(sums['Ахметова Айгерим']['inner_sum'], 600.0)

    def test_operator_sums_key_none_skips(self):
        sums = metrics.operator_sums(self.facts, DAY, DAY + timedelta(days=1),
                                     lambda name: None if name == 'Ахметова Айгерим' else name)
        self.assertEqual(set(sums), {'Бекова Динара'})


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.people = {
            'Ахметова Айгерим Болатқызы': {'id': 1, 'name': 'Ахметова Айгерим', 'status': 'working'},
            'Ахметова Айгерим': {'id': 1, 'name': 'Ахметова Айгерим', 'status': 'working'},
            'Бекова Динара': {'id': 2, 'name': 'Бекова Динара', 'status': 'working'},
            'Сапарова Алия': {'id': 3, 'name': 'Сапарова Алия', 'status': 'fired'},
        }
        self.roster = [
            {'id': 1, 'name': 'Ахметова Айгерим', 'status': 'working'},
            {'id': 2, 'name': 'Бекова Динара', 'status': 'working'},
            {'id': 3, 'name': 'Сапарова Алия', 'status': 'fired'},
            {'id': 4, 'name': 'Жумабаева Сауле', 'status': 'working'},
        ]

    def resolve(self, name):
        return self.people.get(name)

    def test_online_within_window_since_is_stretch_start(self):
        activity = {
            'Ахметова Айгерим Болатқызы': [at(9, 0), at(9, 10), at(9, 20)],   # одна полоса
            'Ахметова Айгерим': [at(8, 0)],                                  # вторая учётка, раньше
            'Бекова Динара': [at(9, 2)],                                     # 23 мин назад
            'Кто-то из ОП': [at(9, 24)],
        }
        online, offline, unmatched = metrics.presence_now(
            activity, self.roster, self.resolve, at(9, 25))
        self.assertEqual([item['name'] for item in online], ['Ахметова Айгерим'])
        self.assertEqual(online[0]['since'], at(9, 0))
        self.assertEqual(online[0]['seconds'], 25 * 60)
        self.assertEqual(online[0]['last_reply_at'], at(9, 20))
        # Уволенной нет; кто ушёл недавно — сверху, кого не было вовсе — в конце.
        self.assertEqual([item['name'] for item in offline], ['Бекова Динара', 'Жумабаева Сауле'])
        # «Не в системе с» — последний ответ плюс окно: по тому же правилу, что и онлайн.
        self.assertEqual(offline[0]['since'], at(9, 17))
        self.assertIsNone(offline[1]['since'])
        self.assertEqual(unmatched, ['Кто-то из ОП'])

    def test_exactly_window_old_reply_is_offline(self):
        activity = {'Бекова Динара': [at(9, 0)]}
        online, _offline, _ = metrics.presence_now(activity, self.roster, self.resolve, at(9, 15))
        self.assertEqual(online, [])
        online, _offline, _ = metrics.presence_now(activity, self.roster, self.resolve,
                                                   at(9, 14, 59))
        self.assertEqual(len(online), 1)

    def test_future_replies_ignored(self):
        activity = {'Бекова Динара': [at(9, 30)]}
        online, offline, _ = metrics.presence_now(activity, self.roster, self.resolve, at(9, 0))
        self.assertEqual(online, [])
        self.assertIsNone([item for item in offline if item['operator_id'] == 2][0]['since'])

    def test_intervals_merge_and_clip(self):
        intervals = metrics.online_intervals([at(9, 0), at(9, 10), at(10, 0)], 15 * 60,
                                             until=at(10, 5))
        self.assertEqual(intervals, [(at(9, 0), at(9, 25)), (at(10, 0), at(10, 5))])

    def test_online_seconds_by_hour_merges_two_accounts(self):
        activity = {'Ахметова Айгерим Болатқызы': [at(9, 50)], 'Ахметова Айгерим': [at(9, 55)]}
        per_hour = metrics.online_seconds_by_hour(activity, self.resolve, DAY, 24)
        # 9:50–10:10 одной полосой: 10 мин в 9-м часу, 10 мин в 10-м — без двойного счёта.
        self.assertEqual(per_hour[9], {'Ахметова Айгерим': 600})
        self.assertEqual(per_hour[10], {'Ахметова Айгерим': 600})


class HourlyRowsTests(unittest.TestCase):
    def resolve(self, name):
        return {'id': 1, 'name': 'Ахметова Айгерим'} if name == 'Ахметова Айгерим' else None

    def test_heads_threshold_partial_hour_and_shape(self):
        conversations = {'c1': conv('c1', at(10, 0))}
        messages = [msg('c1', at(10, 0), 'driver'),
                    msg('c1', at(10, 2), 'human', 'Ахметова Айгерим')]
        facts, activity = metrics.analyze(conversations, messages, channels={SZOV})
        activity['Ахметова Айгерим'].append(at(11, 59, 30))   # 30 с в 11-м часу
        rows = metrics.hourly_rows(facts, activity, self.resolve, lambda name: name, DAY,
                                   now=at(12, 10))
        self.assertEqual(len(rows), 13)
        ten, eleven, twelve = rows[10], rows[11], rows[12]
        self.assertEqual((ten['chats'], ten['chats_new'], ten['operators_online']), (1, 1, 1))
        self.assertEqual(ten['first_reply_seconds'], 120.0)
        self.assertEqual(ten['operators'], [{'name': 'Ахметова Айгерим', 'seconds': 900}])
        self.assertEqual(ten['operators_reply'][0]['name'], 'Ахметова Айгерим')
        self.assertEqual((eleven['operators_online'], eleven['operators_under_minute']), (0, 1))
        self.assertEqual(twelve['operators_online'], 1)          # 12:00–12:10 под «сейчас»
        self.assertTrue(twelve['partial'])
        self.assertFalse(ten['partial'])

    def test_full_day_has_24_hours(self):
        rows = metrics.hourly_rows({}, {}, self.resolve, lambda name: name, DAY, full_day=True)
        self.assertEqual(len(rows), 24)
        self.assertFalse(any(row['partial'] for row in rows))
        self.assertIsNone(rows[0]['inner_reply_seconds'])


if __name__ == '__main__':
    unittest.main()
