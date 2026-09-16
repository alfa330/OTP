# -*- coding: utf-8 -*-
"""Привязка звонков к сделкам (cdr/leads.py): правила, которые ломаются молча.

Каждое из них — не теория, а разбор на живых данных отдела продаж:

  * звонок, начавшийся за секунды ДО появления сделки в CRM, — её звонок
    (карточку заводят по факту уже начатого разговора; без допуска за три дня
    сентября 2026 терялось 417 касаний из 2 903);
  * допуск не отбирает звонок у ПРЕДЫДУЩЕЙ сделки того же номера;
  * окна соседних сделок одного номера стыкуются встык — звонок считается один раз;
  * у сделки с уникальным номером окно можно отключить, у дубля — нельзя;
  * «Ответственный звонил» решается по внутреннему номеру, а потом по ФИО.

Модуль чистый, сети и базы нет. Телефоны учебные (7XX555XXXX).
"""

import unittest
from datetime import datetime

from cdr import leads as L


def lead(key, phones, moment, **extra):
    row = {'key': key, 'phones': list(phones), 'moment': datetime.fromisoformat(moment)}
    row.update(extra)
    return row


def touch(phone, started, **extra):
    row = {'phone': phone, 'started_at': datetime.fromisoformat(started), 'call_type': 'Исходящий',
           'result': 'Не ответил', 'talk_seconds': 0, 'ext': '6474', 'operator': 'Жупан Аружан'}
    row.update(extra)
    return row


WINDOW_TO = datetime(2026, 9, 30)


class WindowTests(unittest.TestCase):

    def test_звонок_за_секунды_до_создания_сделки_принадлежит_ей(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 12:10:53')]
        calls = [touch('7015550001', '2026-09-10 12:10:51', call_type='Входящий',
                       talk_seconds=80, result='Разговор')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual(len(got['a']), 1)

    def test_звонок_раньше_допуска_не_принадлежит_сделке(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 12:10:53')]
        calls = [touch('7015550001', '2026-09-10 12:05:00')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual(got['a'], [])

    def test_окна_дублей_стыкуются_встык_и_звонок_считается_один_раз(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00'),
                 lead('b', ['7015550001'], '2026-09-12 10:00:00')]
        calls = [touch('7015550001', '2026-09-10 10:30:00'),
                 touch('7015550001', '2026-09-12 09:59:30'),   # в допуске второй
                 touch('7015550001', '2026-09-13 08:00:00')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual([t['started_at'].isoformat(sep=' ') for t in got['a']],
                         ['2026-09-10 10:30:00'])
        self.assertEqual(len(got['b']), 2)
        self.assertEqual(leads[0]['dup_n'], 2)
        self.assertEqual((leads[0]['dup_i'], leads[1]['dup_i']), (1, 2))

    def test_допуск_не_отбирает_у_предыдущей_сделки_её_собственный_звонок(self):
        # Две сделки одного номера с разницей в минуту. Звонок в 09:59:30 создал
        # ПЕРВУЮ карточку (начался за полминуты до неё) — и должен остаться у неё,
        # хотя формально попадает и в двухминутный допуск второй. Окно второй
        # начинается не раньше момента создания первой.
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00'),
                 lead('b', ['7015550001'], '2026-09-10 10:01:00')]
        calls = [touch('7015550001', '2026-09-10 09:59:30'),
                 touch('7015550001', '2026-09-10 10:00:40')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual([t['started_at'].isoformat(sep=' ') for t in got['a']],
                         ['2026-09-10 09:59:30'])
        # Звонок в 10:00:40 — за 20 секунд до второй карточки: это её звонок.
        self.assertEqual([t['started_at'].isoformat(sep=' ') for t in got['b']],
                         ['2026-09-10 10:00:40'])

    def test_правая_граница_окна_включает_весь_последний_день(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00')]
        calls = [touch('7015550001', '2026-09-14 23:59:00'),
                 touch('7015550001', '2026-09-15 00:00:10')]
        got = L.assign_touches(leads, calls, window_to=datetime(2026, 9, 14))
        self.assertEqual(len(got['a']), 1)

    def test_без_окна_у_уникального_номера_берутся_все_звонки(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00')]
        calls = [touch('7015550001', '2026-08-01 10:00:00'),
                 touch('7015550001', '2026-09-20 10:00:00')]
        got = L.assign_touches(leads, calls, window_to=datetime(2026, 9, 14), no_window=True)
        self.assertEqual(len(got['a']), 2)

    def test_без_окна_дубли_номера_всё_равно_режутся_окнами(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00'),
                 lead('b', ['7015550001'], '2026-09-12 10:00:00')]
        calls = [touch('7015550001', '2026-09-13 10:00:00')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO, no_window=True)
        self.assertEqual(got['a'], [])
        self.assertEqual(len(got['b']), 1)

    def test_сделка_с_двумя_телефонами_собирает_звонки_с_обоих(self):
        leads = [lead('a', ['7015550001', '7025550002'], '2026-09-10 10:00:00')]
        calls = [touch('7015550001', '2026-09-10 11:00:00'),
                 touch('7025550002', '2026-09-10 12:00:00')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual(len(got['a']), 2)

    def test_чужой_номер_никуда_не_попадает(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00')]
        calls = [touch('7995550009', '2026-09-10 11:00:00')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual(got['a'], [])

    def test_касания_отсортированы_по_времени(self):
        leads = [lead('a', ['7015550001'], '2026-09-10 10:00:00')]
        calls = [touch('7015550001', '2026-09-11 10:00:00'),
                 touch('7015550001', '2026-09-10 10:30:00')]
        got = L.assign_touches(leads, calls, window_to=WINDOW_TO)
        self.assertEqual([t['started_at'].day for t in got['a']], [10, 11])


class ResponsibleTests(unittest.TestCase):

    def test_по_внутреннему_номеру(self):
        calls = [touch('7015550001', '2026-09-10 10:00:00', ext='6474', operator='Кто-то')]
        self.assertEqual(L.responsible_called({'6474'}, 'Другой Человек', calls), 'да')

    def test_по_фио_когда_номера_в_карточке_нет(self):
        calls = [touch('7015550001', '2026-09-10 10:00:00', ext='6656', operator='Зинеден Аружан')]
        self.assertEqual(L.responsible_called(set(), 'Аружан Зинеден', calls), 'да')

    def test_нет_когда_звонили_другие(self):
        calls = [touch('7015550001', '2026-09-10 10:00:00', ext='6656', operator='Зинеден Аружан')]
        self.assertEqual(L.responsible_called({'6474'}, 'Жупан Аружан', calls), 'нет')

    def test_пусто_без_звонков_или_без_ответственного(self):
        calls = [touch('7015550001', '2026-09-10 10:00:00')]
        self.assertEqual(L.responsible_called({'6474'}, 'Жупан Аружан', []), '')
        self.assertEqual(L.responsible_called(set(), '', calls), '')

    def test_только_свои_звонки(self):
        calls = [touch('7015550001', '2026-09-10 10:00:00', ext='6474'),
                 touch('7015550001', '2026-09-10 11:00:00', ext='6656', operator='Зинеден Аружан')]
        own = L.own_touches({'6474'}, 'Жупан Аружан', calls)
        self.assertEqual([t['ext'] for t in own], ['6474'])


class AggregateTests(unittest.TestCase):

    def test_реакция_и_разговоры(self):
        row = lead('a', ['7015550001'], '2026-09-10 10:00:00')
        calls = [touch('7015550001', '2026-09-10 09:59:50', call_type='Входящий',
                       talk_seconds=40, result='Разговор', operator='А'),
                 touch('7015550001', '2026-09-10 10:03:00', operator='Б'),
                 touch('7015550001', '2026-09-10 10:05:00', talk_seconds=90, result='Разговор',
                       operator='А')]
        agg = L.aggregate(row, calls)
        self.assertEqual(agg['touches'], 3)
        self.assertEqual(agg['outgoing'], 2)
        self.assertEqual(agg['incoming'], 1)
        self.assertEqual(agg['talks'], 2)
        self.assertEqual(agg['talk_seconds'], 130)
        self.assertEqual(agg['reaction_min'], 3.0)      # до первого ИСХОДЯЩЕГО
        self.assertEqual(agg['before_lead'], 1)
        self.assertEqual(agg['operators'], ['А', 'Б'])

    def test_без_звонков_всё_пустое(self):
        agg = L.aggregate(lead('a', ['7015550001'], '2026-09-10 10:00:00'), [])
        self.assertEqual(agg['touches'], 0)
        self.assertIsNone(agg['reaction_min'])
        self.assertIsNone(agg['first_at'])

    def test_число_блоков(self):
        self.assertEqual(L.blocks_for(0), (1, True))
        self.assertEqual(L.blocks_for(4), (4, True))
        self.assertEqual(L.blocks_for(7), (7, False))
        self.assertEqual(L.blocks_for(40), (12, False))

    def test_телефоны_сделки_из_строки_и_из_запасного_поля(self):
        self.assertEqual(L.lead_phones({'phones': '7015550001,7025550002'}),
                         ['7015550001', '7025550002'])
        self.assertEqual(L.lead_phones({'phones': '', 'phone': '+7 701 555 00 01'}),
                         ['7015550001'])
        self.assertEqual(L.lead_phones({'phones': '', 'phone': '123'}), [])


class SummaryTests(unittest.TestCase):

    def test_итоги_по_набору(self):
        a = lead('a', ['7015550001'], '2026-09-10 10:00:00')
        b = lead('b', ['7025550002'], '2026-09-10 11:00:00')
        calls_a = [touch('7015550001', '2026-09-10 10:01:00', talk_seconds=30, result='Разговор')]
        rows = [
            {'lead': a, 'touches': calls_a, 'agg': L.aggregate(a, calls_a), 'lead_type': 'форма',
             'stage': 'ПРОШЕЛ РЕГИСТРАЦИЮ', 'park': 'iTaxi', 'owner_called': 'да'},
            {'lead': b, 'touches': [], 'agg': L.aggregate(b, []), 'lead_type': 'wz',
             'stage': 'Диалоги', 'park': 'iTaxi', 'owner_called': ''},
        ]
        summary = L.summarize(rows)
        self.assertEqual(summary['leads'], 2)
        self.assertEqual(summary['with_touches'], 1)
        self.assertEqual(summary['touches'], 1)
        self.assertEqual(summary['talks'], 1)
        self.assertEqual(summary['median_reaction_min'], 1.0)
        self.assertEqual(summary['owner_called'], {'yes': 1, 'no': 0, 'blank': 1})
        self.assertEqual(summary['by_lead_type'], [['форма', 1, 1], ['wz', 1, 0]])
        self.assertEqual(summary['distribution'], [(0, 1), (1, 1)])
        self.assertEqual(summary['max_touches'], 1)


if __name__ == '__main__':
    unittest.main()
