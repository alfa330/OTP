# -*- coding: utf-8 -*-
"""«Отчёт по сменам» · «Чат» (SALES-1, #424): смены верификаторов, обработка чатов, колонки ТЗ."""

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

from flask import Flask
from openpyxl import load_workbook

from op_wallboard import chat as C
from op_wallboard import chat_shifts as S
from op_wallboard import chat_shifts_export as E
from op_wallboard import chat_shifts_routes as R

ROOT = Path(__file__).resolve().parents[1]
NAMES = {7: 'Оператор Семь', 8: 'Оператор Восемь'}


def at(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')


def ev(text, key, note=None):
    return (at(text), key, note)


def client(text, chat='c1'):
    return {'channel_id': 'ch', 'chat_id': chat, 'at': at(text), 'is_echo': False, 'user_id': None,
            'is_bot': False, 'message_id': 'in-%s-%s' % (chat, text), 'author_id': None, 'type': 'text'}


def staff(text, user, chat='c1'):
    return {'channel_id': 'ch', 'chat_id': chat, 'at': at(text), 'is_echo': True, 'user_id': user,
            'is_bot': False, 'message_id': 'out-%s-%s-%s' % (chat, user, text), 'author_id': 'icore:%s' % user,
            'type': 'text'}


def mailing(text, chat='c1'):
    """Эхо без автора — рассылка или автоматика Wazzup («Admin»)."""
    return {'channel_id': 'ch', 'chat_id': chat, 'at': at(text), 'is_echo': True, 'user_id': None,
            'is_bot': False, 'message_id': 'mail-%s-%s' % (chat, text), 'author_id': None, 'type': 'text'}


def bot(text, chat='c1'):
    return {'channel_id': 'ch', 'chat_id': chat, 'at': at(text), 'is_echo': True, 'user_id': None,
            'is_bot': True, 'message_id': 'bot-%s-%s' % (chat, text), 'author_id': 'bot-1', 'type': 'text'}


def report(events, rows=(), phone_offs=None, day_from=date(2026, 11, 10), day_to=date(2026, 11, 12),
           now=at('2026-11-13 12:00:00')):
    return S.build_report(events_by_operator=events, names=NAMES, rows=list(rows), day_from=day_from,
                          day_to=day_to, now=now, phone_offs=phone_offs or {}, verifier_ids={7, 8})


def shifts_of(events, own=(), offs=(), now=at('2026-11-13 12:00:00')):
    return S.build_shifts(events, now, own_messages=sorted(at(t) for t in own),
                          phone_offs=sorted(at(t) for t in offs))


DAY = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 12:00:00', 'перерыв'),
       ev('2026-11-10 12:30:00', 'готов'), ev('2026-11-10 15:00:00', 'тренинг'),
       ev('2026-11-10 15:30:00', 'готов'), ev('2026-11-10 16:00:00', 'тех причина'),
       ev('2026-11-10 16:10:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]


class ShiftTests(unittest.TestCase):
    def test_day_shift_from_start_to_finish(self):
        (shift,) = shifts_of(DAY)
        self.assertEqual((shift['login'], shift['logout'], shift['close']),
                         (at('2026-11-10 09:00:00'), at('2026-11-10 18:00:00'), S.CLOSE_MANUAL))
        self.assertEqual([kind for kind, *_ in shift['segments']],
                         ['active', 'break', 'active', 'training', 'active', 'tech', 'active'])
        self.assertTrue(shift['finished'])

    def test_portal_closed_and_return_within_four_hours_is_the_same_shift(self):
        events = [ev('2026-11-10 09:00:00', 'готов'),
                  ev('2026-11-10 11:00:00', 'выключен', 'Портал закрыт'),
                  ev('2026-11-10 11:30:00', 'готов'), ev('2026-11-10 13:00:00', 'выключен')]
        (shift,) = shifts_of(events)
        offline = [seg for seg in shift['segments'] if seg[0] == S.KIND_OFFLINE]
        self.assertEqual(offline, [(S.KIND_OFFLINE, at('2026-11-10 11:00:00'), at('2026-11-10 11:30:00'),
                                    'портал закрыт')])
        # Вернулся через 4 часа и позже — уже новая смена.
        later = events[:2] + [ev('2026-11-10 15:00:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]
        self.assertEqual(len(shifts_of(later)), 2)

    def test_finish_pressed_by_mistake_keeps_the_shift_but_a_real_finish_ends_it(self):
        mistake = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 12:00:00', 'выключен'),
                   ev('2026-11-10 12:10:00', 'готов'), ev('2026-11-10 13:00:00', 'выключен')]
        self.assertEqual(len(shifts_of(mistake)), 1)
        # Замер 08.10–17.11.2026: возврат через 48 минут — 4 часа после «Закончить смену» — вторая смена.
        second = mistake[:2] + [ev('2026-11-10 14:00:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]
        first, other = shifts_of(second)
        self.assertEqual(first['logout'], at('2026-11-10 12:00:00'))
        self.assertEqual(other['login'], at('2026-11-10 14:00:00'))

    def test_shift_left_open_overnight_ends_on_the_last_message(self):
        events = [ev('2026-11-10 09:00:00', 'готов'),
                  ev('2026-11-11 08:51:17', 'выключен'), ev('2026-11-11 08:51:19', 'готов'),
                  ev('2026-11-11 18:00:00', 'выключен')]
        first, second = shifts_of(events, own=['2026-11-10 10:00:00', '2026-11-10 14:00:00',
                                               '2026-11-10 18:51:00', '2026-11-11 10:00:00',
                                               '2026-11-11 14:00:00', '2026-11-11 17:00:00'])
        self.assertEqual((first['logout'], first['close']), (at('2026-11-10 18:51:00'), S.CLOSE_CUT))
        self.assertEqual((second['login'], second['opened']), (at('2026-11-11 08:51:19'), S.OPENED_STATUS))
        self.assertEqual(second['close'], S.CLOSE_MANUAL)

    def test_whole_day_in_active_without_a_single_message_is_not_a_shift(self):
        """Девять часов «Активного» без сообщений и нажатий, потом «Закончить смену» — человека не
        было: от смены остаётся только нажатие, а смена без секунды — не смена."""
        events = [ev('2026-11-11 08:51:19', 'готов'), ev('2026-11-11 18:00:00', 'выключен')]
        self.assertEqual(shifts_of(events), [])

    def test_quiet_night_in_active_stays_one_shift(self):
        """Ночью верификатор часами ждёт чатов: 5 часов тишины в «Активном», после которых он сам
        вернулся к работе, — ожидание, а не уход."""
        events = [ev('2026-11-10 20:00:00', 'готов'), ev('2026-11-11 09:00:00', 'выключен')]
        (shift,) = shifts_of(events, own=['2026-11-11 01:30:00', '2026-11-11 06:30:00'])
        self.assertEqual((shift['login'], shift['logout']), (at('2026-11-10 20:00:00'), at('2026-11-11 09:00:00')))

    def test_pause_left_for_hours_ends_the_shift_at_the_pause(self):
        events = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 13:00:00', 'перерыв'),
                  ev('2026-11-10 18:00:00', 'готов'), ev('2026-11-10 19:00:00', 'выключен')]
        first, second = shifts_of(events, own=['2026-11-10 12:00:00'])
        self.assertEqual((first['logout'], first['close']), (at('2026-11-10 13:00:00'), S.CLOSE_CUT))
        self.assertEqual(second['login'], at('2026-11-10 18:00:00'))

    def test_no_return_after_four_hours_until_the_portal_closed(self):
        events = [ev('2026-11-10 15:00:00', 'готов'), ev('2026-11-11 03:21:00', 'выключен', 'Портал закрыт')]
        (shift,) = shifts_of(events, own=['2026-11-10 21:00:00'])
        self.assertEqual((shift['logout'], shift['close']), (at('2026-11-10 21:00:00'), S.CLOSE_CUT))

    def test_phone_logout_between_two_starts_is_the_end_of_the_shift(self):
        """Выход iCORE Phone раздел видит как выход: повтор «Активного» после него — «Начать смену»."""
        events = [ev('2026-11-10 06:41:00', 'готов'), ev('2026-11-10 21:01:56', 'готов'),
                  ev('2026-11-11 09:04:00', 'выключен')]
        first, second = shifts_of(events, own=['2026-11-10 08:01:00', '2026-11-10 21:03:00'],
                                  offs=['2026-11-10 08:03:36'])
        self.assertEqual((first['logout'], first['close']), (at('2026-11-10 08:03:36'), S.CLOSE_PHONE))
        self.assertEqual((second['login'], second['opened']), (at('2026-11-10 21:01:56'), S.OPENED_STATUS))

    def test_repeated_press_without_phone_logout_is_just_a_press(self):
        events = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 10:00:00', 'готов'),
                  ev('2026-11-10 18:00:00', 'выключен')]
        (shift,) = shifts_of(events)
        self.assertEqual([seg[0] for seg in shift['segments']], ['active'])

    def test_phone_logout_in_the_silence_ends_the_shift_there(self):
        events = [ev('2026-11-10 09:00:00', 'готов')]
        (shift,) = shifts_of(events, own=['2026-11-10 17:50:00'], offs=['2026-11-10 18:00:00'])
        self.assertEqual((shift['logout'], shift['close']), (at('2026-11-10 18:00:00'), S.CLOSE_PHONE))

    def test_return_by_message_after_a_cut_has_no_recorded_login(self):
        events = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-11 18:00:00', 'выключен')]
        first, second = shifts_of(events, own=['2026-11-10 10:00:00', '2026-11-11 09:30:00',
                                               '2026-11-11 13:00:00', '2026-11-11 16:30:00'])
        self.assertEqual(first['logout'], at('2026-11-10 10:00:00'))
        self.assertEqual((second['login'], second['opened']), (at('2026-11-11 09:30:00'), S.OPENED_RESUME))
        self.assertEqual((second['logout'], second['close']), (at('2026-11-11 18:00:00'), S.CLOSE_MANUAL))

    def test_running_shift_is_not_finished_until_nothing_can_extend_it(self):
        """Ночью в «Активном» человек может молчать часами и вернуться: пока тишина короче 8 часов,
        смена идёт — иначе строка показалась бы завершённой и пропала бы с его следующим ответом."""
        events = [ev('2026-11-13 09:00:00', 'готов')]
        for now in ('2026-11-13 12:00:00', '2026-11-13 16:00:00', '2026-11-13 19:29:00'):
            (running,) = shifts_of(events, own=['2026-11-13 11:30:00'], now=at(now))
            self.assertFalse(running['finished'], now)
        (gone,) = shifts_of(events, own=['2026-11-13 11:30:00'], now=at('2026-11-13 19:31:00'))
        self.assertTrue(gone['finished'])
        self.assertEqual((gone['logout'], gone['close']), (at('2026-11-13 11:30:00'), S.CLOSE_CUT))
        # В перерыве тишина рвёт смену уже через 4 часа.
        paused = [ev('2026-11-13 09:00:00', 'готов'), ev('2026-11-13 11:00:00', 'перерыв')]
        (shift,) = shifts_of(paused, now=at('2026-11-13 15:01:00'))
        self.assertTrue(shift['finished'])
        self.assertEqual(shift['logout'], at('2026-11-13 11:00:00'))

    def test_closed_portal_is_finished_only_when_return_is_no_longer_possible(self):
        events = [ev('2026-11-13 09:00:00', 'готов'), ev('2026-11-13 11:00:00', 'выключен', 'Портал закрыт')]
        (shift,) = shifts_of(events, own=['2026-11-13 10:30:00'], now=at('2026-11-13 12:00:00'))
        self.assertFalse(shift['finished'])
        (shift,) = shifts_of(events, own=['2026-11-13 10:30:00'], now=at('2026-11-13 15:00:01'))
        self.assertTrue(shift['finished'])
        # Вернулся через полтора часа — та же смена, и она идёт дальше.
        returned = events + [ev('2026-11-13 12:30:00', 'готов'), ev('2026-11-13 18:00:00', 'выключен')]
        (shift,) = shifts_of(returned, own=['2026-11-13 10:30:00', '2026-11-13 14:00:00'],
                             now=at('2026-11-13 18:10:00'))
        self.assertEqual((shift['login'], shift['logout'], shift['finished']),
                         (at('2026-11-13 09:00:00'), at('2026-11-13 18:00:00'), False))
        (shift,) = shifts_of(returned, own=['2026-11-13 10:30:00', '2026-11-13 14:00:00'],
                             now=at('2026-11-13 18:15:00'))
        self.assertTrue(shift['finished'])

    def test_quiet_night_then_closed_portal_and_return_stays_one_shift(self):
        """Ночь в «Активном», 5 часов без чатов, компьютер уснул (сторож закрыл портал), человек
        вернулся через полчаса: это одна смена с ожиданием, как и без закрытия портала."""
        events = [ev('2026-11-10 21:00:00', 'готов'), ev('2026-11-11 06:00:00', 'выключен', 'Портал закрыт'),
                  ev('2026-11-11 06:30:00', 'готов'), ev('2026-11-11 09:00:00', 'выключен')]
        (shift,) = shifts_of(events, own=['2026-11-10 22:00:00', '2026-11-11 01:00:00', '2026-11-11 07:00:00'])
        self.assertEqual((shift['login'], shift['logout']), (at('2026-11-10 21:00:00'), at('2026-11-11 09:00:00')))

    def test_phone_logout_and_return_after_two_hours_is_a_new_shift(self):
        events = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 14:10:00', 'готов'),
                  ev('2026-11-10 18:00:00', 'выключен')]
        first, second = shifts_of(events, own=['2026-11-10 11:00:00', '2026-11-10 12:00:00',
                                               '2026-11-10 15:00:00'], offs=['2026-11-10 12:10:00'])
        self.assertEqual((first['logout'], first['close']), (at('2026-11-10 12:10:00'), S.CLOSE_PHONE))
        self.assertEqual(second['login'], at('2026-11-10 14:10:00'))

    def test_quiet_night_before_a_quick_phone_restart_is_waiting(self):
        """Ночью 5 часов без чатов, потом выход iCORE Phone и «Начать смену» через 5 минут: человек
        вернулся сам, и тишина судится как тишина с возвратом — от 8 часов, смена одна."""
        events = [ev('2026-11-10 21:00:00', 'готов'), ev('2026-11-11 04:05:00', 'готов'),
                  ev('2026-11-11 09:00:00', 'выключен')]
        (shift,) = shifts_of(events, own=['2026-11-10 23:00:00', '2026-11-11 06:00:00'],
                             offs=['2026-11-11 04:00:00'])
        self.assertEqual((shift['login'], shift['logout']), (at('2026-11-10 21:00:00'), at('2026-11-11 09:00:00')))

    def test_phone_logout_and_quick_restart_is_one_shift(self):
        events = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 10:10:00', 'готов'),
                  ev('2026-11-10 18:00:00', 'выключен')]
        (shift,) = shifts_of(events, own=['2026-11-10 09:30:00', '2026-11-10 13:00:00'],
                             offs=['2026-11-10 10:00:00'])
        self.assertEqual((shift['login'], shift['logout']), (at('2026-11-10 09:00:00'), at('2026-11-10 18:00:00')))

    def test_zero_length_shift_is_not_a_shift(self):
        events = [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 09:00:00', 'выключен')]
        self.assertEqual(shifts_of(events), [])

    def test_unknown_keys_and_lonely_logouts_are_skipped(self):
        events = [ev('2026-11-10 08:00:00', 'выключен'), ev('2026-11-10 09:00:00', 'готов'),
                  ev('2026-11-10 10:00:00', 'странный'), ev('2026-11-10 11:00:00', 'выключен')]
        (shift,) = shifts_of(events)
        self.assertEqual((shift['login'], shift['logout']), (at('2026-11-10 09:00:00'), at('2026-11-10 11:00:00')))


class ProcessingTests(unittest.TestCase):
    def intervals(self, rows):
        messages = C.classify(rows, {7, 8})
        return S.processing_intervals(S._ordered_by_chat(messages))[0]

    def test_from_the_client_message_to_the_last_reply(self):
        spans = self.intervals([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7),
                                client('2026-11-10 10:03:00'), staff('2026-11-10 10:04:00', 7)])
        self.assertEqual(spans[7], [(at('2026-11-10 10:00:00'), at('2026-11-10 10:04:00'), ('ch', 'c1'))])

    def test_pause_over_fifteen_minutes_breaks_the_processing(self):
        spans = self.intervals([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7),
                                client('2026-11-10 10:20:00'), staff('2026-11-10 10:22:00', 7),
                                client('2026-11-10 11:00:00'), staff('2026-11-10 11:20:00', 7)])
        self.assertEqual([(start, end) for start, end, _chat in spans[7]], [
            (at('2026-11-10 10:00:00'), at('2026-11-10 10:01:00')),
            (at('2026-11-10 10:20:00'), at('2026-11-10 10:22:00')),
            # Ответ через 20 минут: подводка — не больше 5 минут, без скачка на границе куска.
            (at('2026-11-10 11:15:00'), at('2026-11-10 11:20:00')),
        ])

    def test_lead_is_at_most_five_minutes_and_starts_at_the_first_unanswered_message(self):
        spans = self.intervals([client('2026-11-10 10:00:00'), client('2026-11-10 10:02:00'),
                                staff('2026-11-10 10:05:00', 7), client('2026-11-10 10:06:00', 'c2'),
                                staff('2026-11-10 10:14:00', 7, 'c2')])
        self.assertEqual([(start, end) for start, end, _chat in spans[7]], [
            (at('2026-11-10 10:00:00'), at('2026-11-10 10:05:00')),
            (at('2026-11-10 10:09:00'), at('2026-11-10 10:14:00')),
        ])

    def test_colleague_takes_over_from_the_next_client_message(self):
        spans = self.intervals([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 8),
                                client('2026-11-10 10:02:00'), staff('2026-11-10 10:03:00', 7)])
        self.assertEqual(spans[8][0][:2], (at('2026-11-10 10:00:00'), at('2026-11-10 10:01:00')))
        self.assertEqual(spans[7][0][:2], (at('2026-11-10 10:02:00'), at('2026-11-10 10:03:00')))

    def test_follow_up_right_after_a_colleague_starts_at_the_colleague(self):
        spans = self.intervals([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 8),
                                staff('2026-11-10 10:02:00', 7)])
        self.assertEqual(spans[7][0][:2], (at('2026-11-10 10:01:00'), at('2026-11-10 10:02:00')))

    def test_union_and_clip(self):
        chat_a, chat_b = ('ch', 'a'), ('ch', 'b')
        spans = [(at('2026-11-10 10:00:00'), at('2026-11-10 10:04:00'), chat_a),
                 (at('2026-11-10 10:02:00'), at('2026-11-10 10:05:00'), chat_b)]
        self.assertEqual(S.union(spans), [(at('2026-11-10 10:00:00'), at('2026-11-10 10:05:00'))])
        clipped = S.clip(spans, [(at('2026-11-10 09:00:00'), at('2026-11-10 10:03:00'))])
        self.assertEqual(clipped, [(at('2026-11-10 10:00:00'), at('2026-11-10 10:03:00'), chat_a),
                                   (at('2026-11-10 10:02:00'), at('2026-11-10 10:03:00'), chat_b)])


class ColumnsTests(unittest.TestCase):
    ROWS = [
        # c1, c2 — параллельно: обработка 10:00–10:04 и 10:02–10:05.
        client('2026-11-10 10:00:00', 'c1'), staff('2026-11-10 10:01:00', 7, 'c1'),
        client('2026-11-10 10:02:00', 'c2'), client('2026-11-10 10:03:00', 'c1'),
        staff('2026-11-10 10:04:00', 7, 'c1'), staff('2026-11-10 10:05:00', 7, 'c2'),
        # c3 — клиент ответил, а продолжил коллега: разговор закончил не он.
        client('2026-11-10 11:00:00', 'c3'), staff('2026-11-10 11:01:00', 7, 'c3'),
        client('2026-11-10 11:02:00', 'c3'), staff('2026-11-10 11:03:00', 8, 'c3'),
        # c4 — «спасибо» клиента после ответа последнее слово у него не отнимает.
        client('2026-11-10 13:00:00', 'c4'), staff('2026-11-10 13:01:00', 7, 'c4'),
        client('2026-11-10 13:02:00', 'c4'),
        # c1 снова через 7 часов — новый диалог того же чата: чат тот же, «взят» один раз.
        client('2026-11-10 17:10:00', 'c1'), staff('2026-11-10 17:11:00', 7, 'c1'),
    ]

    def row(self):
        rep = report({7: DAY}, self.ROWS)
        (row,) = rep['shifts']
        return rep, row

    def test_columns(self):
        _rep, row = self.row()
        self.assertEqual(row['login_at'], '2026-11-10T09:00:00')
        self.assertEqual(row['logout_at'], '2026-11-10T18:00:00')
        self.assertEqual((row['break_seconds'], row['tech_seconds'], row['training_seconds']), (1800, 600, 1800))
        self.assertEqual(row['worked_seconds'], 9 * 3600 - 1800)
        # c1 10:00–10:04, c2 10:02–10:05, c3 11:00–11:01, c4 13:00–13:01, c1 17:10–17:11.
        self.assertEqual(row['handling_seconds'], 240 + 180 + 60 + 60 + 60)
        self.assertEqual(row['active_seconds'], 300 + 60 + 60 + 60)
        self.assertEqual(row['active_seconds'] + row['waiting_seconds'] + row['tech_seconds'],
                         row['worked_seconds'])
        self.assertEqual((row['taken_chats'], row['processed_chats']), (4, 3))
        # Первые ответы — как в «Чатах ОП», одно значение на чат за день: c1 60 с (его возврат в
        # 17:10 того же дня нового первого ответа не даёт), c2 180 с, c3 60 с, c4 60 с.
        self.assertEqual((row['first_count'], row['first_reply_seconds']), (4, 90))

    def test_training_is_waiting_and_breaks_are_not_worked(self):
        _rep, row = self.row()
        active_status = 3 * 3600 + 2.5 * 3600 + 1800 + 110 * 60
        self.assertEqual(row['waiting_seconds'], active_status - row['active_seconds'] + 1800)

    def test_journal_adds_up_to_the_row(self):
        rep, row = self.row()
        journal = rep['journals'][row['key']]
        total = lambda kind: sum(item['seconds'] for item in journal if item['kind'] == kind)
        self.assertEqual(total('work'), row['active_seconds'])
        self.assertEqual(total('wait') + total('training'), row['waiting_seconds'])
        self.assertEqual(total('break'), row['break_seconds'])
        self.assertEqual(total('tech'), row['tech_seconds'])
        self.assertEqual(journal[0]['kind'], 'wait')
        self.assertEqual(journal[1], {'kind': 'work', 'style': 'work', 'label': 'Обработка чатов',
                                      'note': '2 чата', 'start_at': '2026-11-10T10:00:00',
                                      'end_at': '2026-11-10T10:05:00', 'seconds': 300})
        self.assertEqual(journal[-1]['kind'], 'logout')
        self.assertEqual(journal[-1]['seconds'], 0)

    def test_tech_breaks_have_start_and_end(self):
        rep, row = self.row()
        self.assertEqual(rep['tech_breaks'], [{'key': row['key'], 'date': '2026-11-10', 'operator_id': 7,
                                               'name': 'Оператор Семь', 'start_at': '2026-11-10T16:00:00',
                                               'end_at': '2026-11-10T16:10:00', 'seconds': 600}])

    def test_processing_counts_only_in_active(self):
        rows = [client('2026-11-10 11:55:00'), staff('2026-11-10 11:58:00', 7),
                client('2026-11-10 11:59:00'), staff('2026-11-10 12:35:00', 7)]
        (row,) = report({7: DAY}, rows)['shifts']
        # 11:55–11:58 в «Активном»; перерыв 12:00–12:30 — не обработка; ответ в 12:35 после
        # 36 минут тишины — подводка 5 минут, 12:30–12:35, уже после возврата в «Активный».
        self.assertEqual(row['active_seconds'], 180 + 300)

    def test_conversation_continued_by_a_colleague_and_a_later_return(self):
        """Коллега подхватил живой разговор — закончил его коллега; клиент вернулся через три часа с
        новым вопросом — это новый разговор, и «обработал» первого не отнимается задним числом."""
        events = {7: DAY, 8: [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]}
        rows = [client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7),
                client('2026-11-10 10:03:00'), staff('2026-11-10 10:04:00', 8),
                client('2026-11-10 11:00:00', 'c2'), staff('2026-11-10 11:01:00', 7, 'c2'),
                client('2026-11-10 14:00:00', 'c2'), staff('2026-11-10 14:02:00', 8, 'c2')]
        rows7, rows8 = sorted(report(events, rows)['shifts'], key=lambda r: r['operator_id'])
        self.assertEqual((rows7['taken_chats'], rows7['processed_chats']), (2, 1))
        self.assertEqual((rows8['taken_chats'], rows8['processed_chats']), (2, 2))

    def test_two_replies_in_the_same_second_keep_their_real_order(self):
        """Доли секунды решают, кто ответил первым и кто закончил разговор: округление до секунд
        не меняет их местами (message_id нарочно в обратном порядке)."""
        events = {7: DAY, 8: [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]}
        first = staff('2026-11-10 10:00:30', 7)
        first.update(at=at('2026-11-10 10:00:30') + timedelta(microseconds=100000), message_id='b')
        second = staff('2026-11-10 10:00:30', 8)
        second.update(at=at('2026-11-10 10:00:30') + timedelta(microseconds=900000), message_id='a')
        rows7, rows8 = sorted(report(events, [client('2026-11-10 10:00:00'), first, second])['shifts'],
                              key=lambda r: r['operator_id'])
        self.assertEqual((rows7['first_count'], rows7['processed_chats'], rows7['active_seconds']), (1, 0, 30))
        self.assertEqual((rows8['first_count'], rows8['processed_chats']), (0, 1))

    def test_only_finished_shifts_of_the_period(self):
        events = {7: DAY + [ev('2026-11-13 09:00:00', 'готов')]}
        rep = report(events, [staff('2026-11-13 11:30:00', 7)], day_to=date(2026, 11, 13))
        self.assertEqual([row['date'] for row in rep['shifts']], ['2026-11-10'])
        self.assertEqual(report(events, day_from=date(2026, 11, 11))['shifts'], [])

    def test_operators_and_filters(self):
        rep = report({7: DAY, 8: [ev('2026-11-11 09:00:00', 'готов'), ev('2026-11-11 10:00:00', 'выключен')]})
        self.assertEqual([item['id'] for item in rep['operators']], [8, 7])
        from op_wallboard import activity as A
        self.assertEqual(len(A.filter_shifts(rep['shifts'], None, [7], [])), 1)
        self.assertEqual(len(A.filter_shifts(rep['shifts'], 'verifier', [], ['2026-11-11'])), 1)



class SectionFirstReplyTests(unittest.TestCase):
    """Первый ответ — как «Ср. ответ» «Аналитики» раздела «Чаты ОП» (владелец 17.11.2026)."""

    def first(self, rows, events=None):
        rep = report(events or {7: DAY, 8: [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]},
                     rows)
        return {row['operator_id']: (row['first_count'], row['first_reply_seconds']) for row in rep['shifts']}

    def test_one_value_per_chat_per_day(self):
        rows = [client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7),
                client('2026-11-10 17:10:00'), staff('2026-11-10 17:12:00', 7)]
        self.assertEqual(self.first(rows)[7], (1, 60))

    def test_colleague_who_answered_first_takes_the_chat(self):
        rows = [client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 8), staff('2026-11-10 10:02:00', 7)]
        result = self.first(rows)
        self.assertEqual((result[7], result[8]), ((0, None), (1, 60)))

    def test_mailing_sent_first_takes_the_first_reply_from_everyone(self):
        rows = [client('2026-11-10 10:00:00'), mailing('2026-11-10 10:00:30'), staff('2026-11-10 10:01:00', 7)]
        self.assertEqual(self.first(rows)[7], (0, None))

    def test_bots_are_not_a_reply(self):
        rows = [client('2026-11-10 10:00:00'), bot('2026-11-10 10:00:10'), staff('2026-11-10 10:01:00', 7)]
        self.assertEqual(self.first(rows)[7], (1, 60))

    def test_client_waiting_from_before_login_counts_from_his_message(self):
        rows = [client('2026-11-10 08:50:00'), staff('2026-11-10 09:02:00', 7)]
        self.assertEqual(self.first(rows)[7], (1, 720))

    def test_reply_after_midnight_to_yesterdays_message_is_not_a_first_reply(self):
        """Окно — день, как в «Чатах ОП» за один день: в новом дне от клиента нет сообщения."""
        night = {7: [ev('2026-11-10 20:00:00', 'готов'), ev('2026-11-11 08:00:00', 'выключен')]}
        filler = [staff(t, 7, 'f') for t in ('2026-11-10 20:30:00', '2026-11-10 23:00:00',
                                             '2026-11-11 03:00:00', '2026-11-11 07:00:00')]
        rows = filler + [client('2026-11-10 23:50:00'), staff('2026-11-11 00:05:00', 7),
                         client('2026-11-11 00:10:00', 'c2'), staff('2026-11-11 00:12:00', 7, 'c2')]
        self.assertEqual(self.first(rows, night)[7], (1, 120))


def dismissal(text, user, chat='c1'):
    return {'channel_id': 'ch', 'chat_id': chat, 'at': at(text), 'user_id': user}


class DismissalTests(unittest.TestCase):
    """«Ответ не нужен» — действие сотрудника в переписке (журнал с 17.11.2026, владелец)."""

    EVENTS = {7: DAY, 8: [ev('2026-11-10 09:00:00', 'готов'), ev('2026-11-10 18:00:00', 'выключен')]}

    def rows(self, rows, dismissals):
        rep = S.build_report(events_by_operator=self.EVENTS, names=NAMES, rows=rows, day_from=date(2026, 11, 10),
                             day_to=date(2026, 11, 10), now=at('2026-11-13 12:00:00'), verifier_ids={7, 8},
                             dismissals=dismissals)
        return {row['operator_id']: row for row in rep['shifts']}

    def test_colleague_who_dismissed_after_his_reply_closed_the_conversation(self):
        rows = self.rows([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7), client('2026-11-10 10:02:00')],
                         [dismissal('2026-11-10 10:03:00', 8)])
        self.assertEqual((rows[7]['taken_chats'], rows[7]['processed_chats']), (1, 0))
        self.assertEqual((rows[8]['taken_chats'], rows[8]['processed_chats'], rows[8]['messages']), (1, 1, 0))

    def test_own_dismissal_after_the_thanks_keeps_the_conversation(self):
        rows = self.rows([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7), client('2026-11-10 10:02:00'),
                          staff('2026-11-10 10:04:00', 8)],
                         [dismissal('2026-11-10 10:05:00', 7)])
        self.assertEqual((rows[7]['taken_chats'], rows[7]['processed_chats']), (1, 1))
        self.assertEqual(rows[8]['processed_chats'], 0)

    def test_dismissal_is_processing_but_not_a_first_reply_or_a_message(self):
        rows = self.rows([client('2026-11-10 10:00:00')], [dismissal('2026-11-10 10:02:00', 7)])
        row = rows[7]
        self.assertEqual((row['taken_chats'], row['processed_chats'], row['messages']), (1, 1, 0))
        self.assertEqual((row['first_count'], row['first_reply_seconds']), (0, None))
        self.assertEqual(row['active_seconds'], 120)

    def test_dismissal_before_the_reply_does_not_steal_the_first_reply(self):
        rows = self.rows([client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7)],
                         [dismissal('2026-11-10 10:00:30', 8)])
        self.assertEqual((rows[7]['first_count'], rows[7]['first_reply_seconds']), (1, 60))

class PeriodTests(unittest.TestCase):
    def test_months_follow_the_message_retention(self):
        months = S.available_months(date(2026, 11, 17))
        self.assertEqual([item['value'] for item in months], ['2026-11', '2026-10'])
        self.assertEqual(months[1]['label'], 'Октябрь 2026 · с 04.10')
        self.assertEqual(S.retention_floor(date(2026, 11, 17)), date(2026, 10, 4))


class ExportTests(unittest.TestCase):
    def test_workbook(self):
        rep = report({7: DAY}, ColumnsTests.ROWS)
        rows = rep['shifts']
        rows[0]['logout_missing'] = True
        book = load_workbook(BytesIO(E.workbook(rows, rep['tech_breaks'], month_label='Ноябрь 2026',
                                                generated_at='17.11.2026 16:00')))
        self.assertEqual(book.sheetnames, ['Смены', 'Технические перерывы', 'Как считается'])
        sheet = book['Смены']
        self.assertEqual([cell.value for cell in sheet[1]], [
            'Дата', 'Оператор', 'Группа', 'Время входа в систему', 'Среднее время первого ответа',
            'Общее время обработки чатов', 'Активное время работы', 'Время ожидания', 'Время перерывов',
            'Время технического перерыва', 'Количество взятых в работу чатов',
            'Количество обработанных чатов', 'Время выхода из системы', 'Фактически отработанное время'])
        values = [cell.value for cell in sheet[2]]
        self.assertEqual(values[1:3], ['Оператор Семь', 'Верификаторы'])
        self.assertEqual(values[7], timedelta(seconds=rows[0]['waiting_seconds']))
        self.assertEqual(values[10:12], [4, 3])
        self.assertEqual(sheet.cell(row=2, column=8).number_format, '[hh]:mm:ss')
        self.assertTrue(sheet.cell(row=2, column=13).font.italic)
        tech = book['Технические перерывы']
        self.assertEqual([cell.value for cell in tech[1]], ['Дата', 'Оператор', 'Начало', 'Окончание',
                                                            'Длительность'])
        self.assertEqual(tech.cell(row=2, column=2).value, 'Оператор Семь')
        self.assertEqual(str(tech.cell(row=2, column=3).value), '16:00:00')
        notes = [sheet.cell(row=r, column=1).value for r in range(3, 10)]
        self.assertFalse(any('выходил из системы' in str(n) for n in notes))
        rows[0]['offline_seconds'] = 1800
        sheet = load_workbook(BytesIO(E.workbook(rows, rep['tech_breaks'], month_label='Ноябрь 2026',
                                                 generated_at='17.11.2026 16:00')))['Смены']
        notes = [sheet.cell(row=r, column=1).value for r in range(3, 10)]
        self.assertTrue(any('В 1 из 1 смен оператор выходил из системы' in str(n) for n in notes))
        self.assertEqual(str(tech.cell(row=2, column=4).value), '16:10:00')
        self.assertEqual(tech.cell(row=2, column=5).value, timedelta(seconds=600))


class _Cursor:
    def __init__(self, data):
        self.data = data
        self.rows = []

    def execute(self, sql, params=None):
        if 'to_regclass' in sql:
            self.rows = [('dismissals' in self.data,)]
        elif 'wazzup_messages' in sql:
            self.rows = [(m['channel_id'], m['chat_id'], m['at'], m['is_echo'], m['user_id'], m['is_bot'],
                          m['message_id'], m['author_id'], m['type']) for m in self.data['messages']]
        elif 'FROM wazzup_reply_dismissals' in sql:
            self.rows = [(d['channel_id'], d['chat_id'], d['at'], d['user_id']) for d in self.data['dismissals']]
        elif 'UNION' in sql:
            self.rows = [(7,)]
        elif 'NOT LIKE' in sql:
            self.rows = []
        elif 'client_event_id LIKE' in sql:
            self.rows = [(7,) + item for item in self.data['events']]
        elif 'FROM users' in sql:
            self.rows = [(7, NAMES[7])]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.refusal = None
        self.data = {'events': list(DAY), 'messages': list(ColumnsTests.ROWS)}
        data = self.data

        class _Db:
            @contextmanager
            def _get_cursor(self_inner):
                yield _Cursor(data)

        app = Flask(__name__)
        app.register_blueprint(R.build_chat_shifts_blueprint(
            db=_Db(), require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            guard=lambda: (10, self.refusal), now=lambda: at('2026-11-13 12:00:00')))
        self.client = app.test_client()

    def test_month_without_journals(self):
        body = self.client.get('/api/op_wallboard/chat_shifts?month=2026-11').get_json()
        self.assertEqual(len(body['shifts']), 1)
        self.assertNotIn('journals', body)
        self.assertNotIn('tech_breaks', body)
        self.assertEqual(body['groups'], [{'key': 'verifier', 'label': 'Верификаторы'}])
        self.assertEqual(body['day_from'], '2026-11-01')

    def test_journal_is_the_row_of_the_month(self):
        response = self.client.get('/api/op_wallboard/chat_shifts/journal?operator_id=7&start=2026-11-10T09:00:00')
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['end_at'], '2026-11-10T18:00:00')
        self.assertEqual(body['segments'][-1]['kind'], 'logout')
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts/journal?operator_id=7'
                                         '&start=2026-11-10T09:00:05').status_code, 404)
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts/journal?operator_id=x').status_code, 400)

    def test_export_follows_the_filters(self):
        response = self.client.get('/api/op_wallboard/chat_shifts/export?month=2026-11&days=2026-11-10')
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.data))['Смены']
        self.assertEqual(sheet.cell(row=2, column=2).value, 'Оператор Семь')
        response = self.client.get('/api/op_wallboard/chat_shifts/export?month=2026-11&days=2026-11-11')
        self.assertIsNone(load_workbook(BytesIO(response.data))['Смены'].cell(row=2, column=2).value)
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts/export?month=2026-11&group=osnova')
                         .status_code, 400)

    def test_dismissals_are_read_when_the_journal_exists(self):
        self.data['dismissals'] = [{'channel_id': 'ch', 'chat_id': 'c1', 'at': at('2026-11-10 17:12:00'),
                                    'user_id': 8}]
        (row,) = self.client.get('/api/op_wallboard/chat_shifts?month=2026-11').get_json()['shifts']
        # c1: после его ответа в 17:11 «Ответ не нужен» нажал другой сотрудник — разговор закончил он.
        self.assertEqual((row['taken_chats'], row['processed_chats']), (4, 2))
        self.data.pop('dismissals')
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts?month=2026-12').status_code, 400)

    def test_refused_and_bad_months(self):
        self.refusal = ('forbidden', 403)
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts?month=2026-11').status_code, 403)
        self.refusal = None
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts?month=2026-1').status_code, 400)
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts?month=2026-12').status_code, 400)
        # Переписка хранится 45 суток: при «сейчас» 13.11 от сентября остался день 30.09, август вычищен.
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts?month=2026-09').status_code, 200)
        self.assertEqual(self.client.get('/api/op_wallboard/chat_shifts?month=2026-08').status_code, 400)


class WiringTests(unittest.TestCase):
    MONITORING = ROOT / 'src' / 'components' / 'monitoring'

    def test_blueprint_registered_with_the_report_guard(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        block = source[source.index('«Отчёт по сменам» · «Чат» (SALES-1'):]
        block = block[:block.index('Отчёт по сменам · чат: Blueprint НЕ подключён')]
        self.assertIn('build_chat_shifts_blueprint(', block)
        self.assertIn('guard=op_activity_routes.make_activity_guard(_op_wallboard_guard)', block)

    def test_screen_and_excel_use_the_same_tz_column_titles(self):
        import re
        shared = (self.MONITORING / 'opChatShiftsShared.js').read_text(encoding='utf-8-sig')
        block = shared[shared.index('export const OP_CHAT_SHIFT_COLUMNS'):shared.index('];', shared.index('export const OP_CHAT_SHIFT_COLUMNS'))]
        labels = re.findall(r"label: '([^']+)'", block)
        self.assertEqual(labels, [title for title, _width in E.COLUMNS])

    def test_screen_paths_match_the_blueprint(self):
        shared = (self.MONITORING / 'opChatShiftsShared.js').read_text(encoding='utf-8-sig')
        for path in ('/api/op_wallboard/chat_shifts', '/api/op_wallboard/chat_shifts/journal',
                     '/api/op_wallboard/chat_shifts/export'):
            self.assertIn("'%s'" % path, shared)

    def test_one_sheet_two_reports_opened_on_the_board_direction(self):
        """Окно одно на табло («Отчёт по сменам»), отчётов в нём два — переключатель «Линия / Чат» в
        шапке окна, а открывается окно на направлении, на котором стоит табло."""
        sheet = (self.MONITORING / 'OpActivityReport.jsx').read_text(encoding='utf-8-sig')
        view = (self.MONITORING / 'OpWallboardView.jsx').read_text(encoding='utf-8-sig')
        self.assertIn('<SegmentedSwitch value={shown} options={OP_WALLBOARD_VIEWS} onChange={switchReport} />', sheet)
        self.assertIn('useShiftReport(LINE_REPORT', sheet)
        self.assertIn('useShiftReport(CHAT_SHIFT_REPORT', sheet)
        self.assertIn('config={CHAT_SHIFT_JOURNAL}', sheet)
        root = view[view.index('export default function OpWallboardView('):]
        self.assertIn('setReportDirection(direction);', root)
        self.assertIn('direction={reportDirection}', root)
        self.assertIn('onDirectionChange={setReportDirection}', root)



class _RecordingCursor:
    """Отвечает по тексту SQL, как _Cursor, и запоминает параметры: фальшивый курсор, который смотрит
    только на текст, не заметил бы, что LIKE потерял «%» (разбор SALES-1, мутация M1)."""

    def __init__(self, data, log):
        self.data, self.log, self.rows = data, log, []

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        if 'to_regclass' in sql:
            self.rows = [(False,)]
        elif 'UNION' in sql and 'wazzup_messages' not in sql:
            self.rows = [(7,), (8,)]
        elif 'NOT LIKE' in sql:
            self.rows = []
        elif 'client_event_id LIKE' in sql:
            self.rows = list(self.data['events'])
        elif 'FROM users' in sql:
            self.rows = [(7, NAMES[7]), (8, NAMES[8])]
        elif 'wazzup_messages' in sql:
            self.rows = [(m['channel_id'], m['chat_id'], m['at'], m['is_echo'], m['user_id'], m['is_bot'],
                          m['message_id'], m['author_id'], m['type']) for m in self.data['messages']]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


SEPT = [ev('2026-09-29 09:00:00', 'готов'), ev('2026-09-29 18:00:00', 'выключен'),
        ev('2026-09-30 09:00:00', 'готов'), ev('2026-09-30 18:00:00', 'выключен')]


class RoutesReviewTests(unittest.TestCase):
    """Ручки на двух людях с записью запросов (тесты разбора SALES-1, срез «ручки»)."""

    def setUp(self):
        self.log = []
        self.moment = at('2026-11-13 12:00:00')
        # Порядок — как у запроса: ORDER BY operator_id, event_at.
        self.data = {'events': [(7,) + item for item in SEPT + DAY]
                     + [(8, at('2026-11-10 09:00:00'), 'готов', None), (8, at('2026-11-10 17:00:00'), 'выключен', None)],
                     'messages': [staff('2026-09-29 10:00:00', 7), staff('2026-09-30 10:00:00', 7),
                                  staff('2026-11-10 10:00:00', 7), staff('2026-11-10 10:00:00', 8, 'c2')]}
        data, log = self.data, self.log

        class _Db:
            @contextmanager
            def _get_cursor(self_inner):
                yield _RecordingCursor(data, log)

        app = Flask(__name__)
        self.bp = R.build_chat_shifts_blueprint(
            db=_Db(), require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            guard=lambda: (10, None), now=lambda: self.moment)
        app.register_blueprint(self.bp)
        self.client = app.test_client()

    def test_section_events_are_read_by_prefix_pattern(self):
        self.client.get('/api/op_wallboard/chat_shifts?month=2026-11')
        ((_sql, params),) = [item for item in self.log if 'client_event_id LIKE' in item[0]]
        self.assertEqual(params[1], 'wzws-%')
        ((_sql, params),) = [item for item in self.log if 'NOT LIKE' in item[0]]
        self.assertEqual(params[1], 'wzws-%')

    def test_previous_month_is_live_on_the_first_day(self):
        """1-го числа ночная смена 31-го ещё дописывается: прошлый месяц кэшируется как текущий."""
        from unittest import mock
        self.moment = at('2026-11-01 08:00:00')
        with mock.patch.object(R.time, 'time', return_value=1000.0):
            self.bp.month_report('2026-10')
        calls = len(self.log)
        with mock.patch.object(R.time, 'time', return_value=1000.0 + R.CURRENT_MONTH_TTL_SECONDS + 1):
            self.bp.month_report('2026-10')
        self.assertGreater(len(self.log), calls)

    def test_journal_is_bound_to_the_operator(self):
        url = '/api/op_wallboard/chat_shifts/journal?operator_id=%s&start=2026-11-10T09:00:00'
        self.assertEqual(self.client.get(url % 7).get_json()['name'], NAMES[7])
        self.assertEqual(self.client.get(url % 8).get_json()['name'], NAMES[8])
        self.assertEqual(self.client.get(url % 9).status_code, 404)

    def test_export_filters_operators(self):
        response = self.client.get('/api/op_wallboard/chat_shifts/export?month=2026-11&operator_ids=8')
        sheet = load_workbook(BytesIO(response.data))['Смены']
        self.assertEqual(sheet.cell(row=2, column=2).value, NAMES[8])
        self.assertIsNone(sheet.cell(row=3, column=2).value)

    def test_partial_month_starts_at_the_retention_floor(self):
        body = self.client.get('/api/op_wallboard/chat_shifts?month=2026-09').get_json()
        self.assertEqual(body['day_from'], '2026-09-30')
        self.assertEqual([row['date'] for row in body['shifts']], ['2026-09-30'])

    def test_messages_go_to_the_report_one_by_one(self):
        rows = R.message_rows([('ch', 'c1', at('2026-11-10 10:00:00'), 1, 7, 0, 'm1', 'icore:7', 'text')])
        item = next(rows)
        self.assertEqual(item, {'channel_id': 'ch', 'chat_id': 'c1', 'at': at('2026-11-10 10:00:00'),
                                'is_echo': True, 'user_id': 7, 'is_bot': False, 'message_id': 'm1',
                                'author_id': 'icore:7', 'type': 'text'})
        self.assertIs(item['is_echo'], True)
        self.assertIs(item['is_bot'], False)


class WindowAndNoiseTests(unittest.TestCase):
    """Запасы времени сообщения у смены и смены-шум (разбор SALES-1, срезы «расчёт» и «ручки»)."""

    def test_message_stamped_just_before_login_belongs_to_the_shift(self):
        (row,) = report({7: DAY}, [client('2026-11-10 08:58:00'), staff('2026-11-10 08:59:30', 7)])['shifts']
        self.assertEqual(row['taken_chats'], 1)

    def test_message_a_few_minutes_after_finish_belongs_to_the_shift(self):
        (row,) = report({7: DAY}, [client('2026-11-10 18:01:00'), staff('2026-11-10 18:03:00', 7)])['shifts']
        self.assertEqual(row['taken_chats'], 1)
        (row,) = report({7: DAY}, [client('2026-11-10 18:05:00'), staff('2026-11-10 18:06:00', 7)])['shifts']
        self.assertEqual(row['taken_chats'], 0)

    def test_client_waiting_from_before_login_counts_from_login(self):
        (row,) = report({7: DAY}, [client('2026-11-10 08:55:00'), staff('2026-11-10 09:02:00', 7)])['shifts']
        self.assertEqual((row['active_seconds'], row['handling_seconds']), (120, 120))

    def test_colleague_in_a_later_conversation_does_not_take_the_chat(self):
        rows = [client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7),
                client('2026-11-11 10:00:00'), staff('2026-11-11 10:01:00', 8)]
        events = {7: DAY, 8: [ev('2026-11-11 09:00:00', 'готов'), ev('2026-11-11 18:00:00', 'выключен')]}
        row7 = next(r for r in report(events, rows)['shifts'] if r['operator_id'] == 7)
        self.assertEqual((row7['taken_chats'], row7['processed_chats']), (1, 1))

    def test_staff_of_another_group_continuing_takes_the_chat(self):
        rows = [client('2026-11-10 10:00:00'), staff('2026-11-10 10:01:00', 7),
                client('2026-11-10 10:02:00'), staff('2026-11-10 10:03:00', 99)]
        (row,) = report({7: DAY}, rows)['shifts']
        self.assertEqual((row['taken_chats'], row['processed_chats']), (1, 0))

    def test_shift_shorter_than_a_minute_without_messages_is_noise(self):
        events = {7: [ev('2026-11-10 21:02:28', 'готов'), ev('2026-11-10 21:02:29', 'выключен'),
                      ev('2026-11-11 09:00:00', 'готов'), ev('2026-11-11 09:00:50', 'выключен')]}
        self.assertEqual(report(events)['shifts'], [])
        rep = report(events, [client('2026-11-11 09:00:10'), staff('2026-11-11 09:00:40', 7)])
        self.assertEqual([row['date'] for row in rep['shifts']], ['2026-11-11'])

    def test_retention_follows_the_chat_export(self):
        from op_wallboard import chat_export
        self.assertEqual(S.RETENTION_DAYS, chat_export.RETENTION_DAYS)
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('retention_days=min(op_chat_shifts.RETENTION_DAYS, _OP_CHAT_SHIFTS_STATUS_DAYS)', source)


if __name__ == '__main__':
    unittest.main()
