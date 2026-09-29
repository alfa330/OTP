# -*- coding: utf-8 -*-
"""Колокол по жалобам: ответ автору — в «Обращениях», задача СВ — в «Жалобах».

Автор работает со своими жалобами в «Обращениях» (решение владельца
29.09.2026), поэтому ответ для водителя и вопрос группы приходят ему строкой
источника crm: общий бейдж, общий пульс раздела и переход туда же. В источнике
complaints остаются только задачи разбора. Разойдись это — бейдж «Жалоб»
загорелся бы у оператора, которому раздел не открыт.
"""

import unittest
from datetime import datetime

from complaints import queries as complaint_queries
from notifications import sources


class FakeCursor:
    def __init__(self):
        self.commands = []

    def execute(self, sql, params=None):
        self.commands.append(sql.strip().split()[0].upper())


class FakeCrmQueries:
    def __init__(self, rows):
        self.rows = rows

    def unread_for_bell(self, cursor, user_id, limit):
        return len(self.rows), self.rows[:limit]


def complaint_row(complaint_id, at, kind='answer'):
    return {'id': complaint_id, 'role': 'author', 'kind': kind, 'at': at,
            'target': 'call_center', 'reason_code': 'rude', 'driver_name': 'Сериков',
            'employee_name': None}


class BellSplitTest(unittest.TestCase):
    def setUp(self):
        self._crm = sources._crm_queries
        self._author = complaint_queries.author_bell_items
        self._work = complaint_queries.work_bell_items

    def tearDown(self):
        sources._crm_queries = self._crm
        complaint_queries.author_bell_items = self._author
        complaint_queries.work_bell_items = self._work

    def wire(self, tickets=(), complaints=(), work=()):
        fake = FakeCrmQueries(list(tickets))
        sources._crm_queries = lambda: fake
        complaint_queries.author_bell_items = (
            lambda cursor, user_id, limit: (len(complaints), list(complaints)[:limit]))
        complaint_queries.work_bell_items = (
            lambda cursor, user_id, limit: (len(work), list(work)[:limit]))

    def test_answer_to_own_complaint_comes_with_crm(self):
        self.wire(
            tickets=[(7, 'Термокороб', 'reply', datetime(2026, 9, 29, 10, 0), 'Яндекс Доставка')],
            complaints=[complaint_row(12, datetime(2026, 9, 29, 11, 0), kind='question')],
        )
        total, items = sources.crm(FakeCursor(), {'user_id': 10}, 5)
        self.assertEqual(total, 2)
        # Свежее сверху: вопрос по жалобе пришёл позже ответа по обращению.
        self.assertEqual([item['id'] for item in items], ['complaint:12', 7])
        first = items[0]
        self.assertEqual((first['source'], first['view'], first['target']),
                         ('crm', 'crm_tickets', 'complaint:12'))
        self.assertEqual(first['tone'], 'warning', 'вопрос группы горит')
        self.assertIn('Жалоба №12', first['title'])

    def test_one_limit_for_both_parts(self):
        self.wire(
            tickets=[(i, 'Т', 'reply', datetime(2026, 9, 29, 9, i), 'Q') for i in range(1, 4)],
            complaints=[complaint_row(i, datetime(2026, 9, 29, 10, i)) for i in range(1, 4)],
        )
        total, items = sources.crm(FakeCursor(), {'user_id': 10}, 4)
        self.assertEqual(total, 6, 'счётчик считает всё')
        self.assertEqual(len(items), 4, 'а порция — одна на источник')

    def test_broken_complaints_do_not_zero_the_crm_source(self):
        """Жалобы — свой пакет со своей миграцией: их сбой не должен обнулить
        обращения, которые collect откатил бы вместе с ними."""
        self.wire(tickets=[(7, 'Т', 'reply', datetime(2026, 9, 29, 10, 0), 'Q')])

        def broken(cursor, user_id, limit):
            raise RuntimeError('relation "complaints" does not exist')

        complaint_queries.author_bell_items = broken
        cursor = FakeCursor()
        with self.assertLogs(level='ERROR'):
            total, items = sources.crm(cursor, {'user_id': 10}, 5)
        self.assertEqual((total, [item['id'] for item in items]), (1, [7]))
        self.assertIn('ROLLBACK', cursor.commands)

    def test_complaints_source_is_only_the_work(self):
        self.wire(work=[{'id': 5, 'role': 'work', 'kind': 'work',
                         'at': datetime(2026, 9, 29, 9, 0), 'target': 'call_center',
                         'reason_code': 'rude', 'driver_name': 'Сериков',
                         'employee_name': 'Иванова'}],
                  complaints=[complaint_row(12, datetime(2026, 9, 29, 11, 0))])
        total, items = sources.complaints(FakeCursor(), {'user_id': 50}, 5)
        self.assertEqual(total, 1)
        self.assertEqual((items[0]['id'], items[0]['view'], items[0]['target']),
                         ('work:5', 'complaints', 5))
        self.assertEqual(items[0]['title'], 'Жалоба на Иванова')


if __name__ == '__main__':
    unittest.main()
