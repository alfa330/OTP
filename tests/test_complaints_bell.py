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
from pathlib import Path

from complaints import queries as complaint_queries
from notifications import sources


class FakeCursor:
    def __init__(self):
        self.commands = []

    def execute(self, sql, params=None):
        self.commands.append(sql.strip().split()[0].upper())


class FakeCrmQueries:
    def __init__(self, rows, reviews=()):
        self.rows = rows
        self.reviews = list(reviews)

    def unread_for_bell(self, cursor, user_id, limit):
        return len(self.rows), self.rows[:limit]

    def review_for_bell(self, cursor, user_id, limit):
        return len(self.reviews), self.reviews[:limit]


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

    def wire(self, tickets=(), complaints=(), work=(), reviews=()):
        fake = FakeCrmQueries(list(tickets), reviews)
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

    def test_ticket_waiting_for_my_review_is_a_task_in_the_crm_source(self):
        """Задача #297: «Сотрудничество с Яндексом» сначала проверяет
        супервайзер. Его задача приходит строкой «Обращений» — там он её и
        решает; своего раздела у проверки обращений нет."""
        self.wire(
            tickets=[(7, 'Термокороб', 'reply', datetime(2026, 10, 7, 10, 0), 'Яндекс Доставка')],
            reviews=[(110, 'Сотрудничество с Яндексом', datetime(2026, 10, 7, 11, 7),
                      'Оператор', 1)],
        )
        total, items = sources.crm(FakeCursor(), {'user_id': 55}, 5)
        self.assertEqual(total, 2, 'бейдж раздела — ответы и задачи проверки вместе')
        self.assertEqual([item['id'] for item in items], ['review:110', 7], 'свежее сверху')
        task = items[0]
        self.assertEqual((task['source'], task['view'], task['target']),
                         ('crm', 'crm_tickets', 110), 'переход открывает само обращение')
        self.assertEqual(task['title'], 'Сотрудничество с Яндексом')
        self.assertIn('отправить в группу или решено', task['body'])

    def test_review_tasks_share_the_limit_and_the_counter(self):
        self.wire(
            tickets=[(i, 'Т', 'reply', datetime(2026, 10, 7, 9, i), 'Q') for i in range(1, 4)],
            reviews=[(100 + i, 'Сотрудничество с Яндексом', datetime(2026, 10, 7, 10, i),
                      'Оператор', 3) for i in range(1, 4)],
        )
        total, items = sources.crm(FakeCursor(), {'user_id': 55}, 4)
        self.assertEqual(total, 6, 'счётчик считает всё')
        self.assertEqual(len(items), 4, 'а порция — одна на источник')

    def test_reviewed_ticket_tells_the_author_so(self):
        """Супервайзер закрыл обращение своим итогом — автору это и есть ответ."""
        self.wire(tickets=[(110, 'Сотрудничество с Яндексом', 'reviewed',
                            datetime(2026, 10, 7, 11, 30), 'Сотрудничество')])
        _total, items = sources.crm(FakeCursor(), {'user_id': 158}, 5)
        self.assertEqual(items[0]['body'], 'Супервайзер проверил: решено')
        self.assertEqual(items[0]['id'], 110)

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


class TrainingPlansTest(unittest.TestCase):
    """«В этот день супервайзерам отдела приходит уведомление о наличии
    запланированного тренинга, оператору тоже» (владелец, 30.09.2026)."""

    def setUp(self):
        self._today = complaint_queries.training_day_bell_items

    def tearDown(self):
        complaint_queries.training_day_bell_items = self._today

    def wire(self, rows):
        seen = {}

        def fake(cursor, user_id, day, limit):
            seen.update(user_id=user_id, day=day)
            return len(rows), list(rows)[:limit]

        complaint_queries.training_day_bell_items = fake
        return seen

    def test_employee_sees_only_the_time(self):
        """Жалоба на сотрудника ему не видна никогда — строка не выдаёт её ни
        номером, ни темой, ни переходом в раздел."""
        self.wire([{'id': 12, 'side': 'self', 'employee_name': None,
                    'training_planned_at': datetime(2026, 9, 30, 14, 0)}])
        total, items = sources.training_plans(FakeCursor(), {'user_id': 40}, 5)
        self.assertEqual(total, 1)
        item = items[0]
        self.assertEqual((item['source'], item['title']), ('training_plans', 'Тренинг сегодня в 14:00'))
        self.assertIsNone(item['view'])
        self.assertIsNone(item['target'])
        text = ' '.join(str(value) for value in item.values())
        for word in ('Жалоб', 'жалоб', '12'):
            self.assertNotIn(word, text)

    def test_supervisor_goes_to_the_complaint(self):
        seen = self.wire([{'id': 12, 'side': 'team', 'employee_name': 'Иванова',
                           'training_planned_at': datetime(2026, 9, 30, 9, 30)}])
        _total, items = sources.training_plans(FakeCursor(), {'user_id': 50}, 5)
        item = items[0]
        self.assertEqual((item['title'], item['body']), ('Иванова', 'Тренинг сегодня в 09:30'))
        self.assertEqual((item['view'], item['target']), ('complaints', 12))
        self.assertEqual(seen['day'], sources._almaty_now().date(), 'строка живёт в свой день')

    def test_source_is_registered_and_labelled(self):
        self.assertIn('training_plans', sources.SOURCES)
        bell = (Path(__file__).resolve().parents[1] / 'src' / 'components' / 'notifications'
                / 'NotificationsBell.jsx').read_text(encoding='utf-8')
        self.assertIn("training_plans: { label: 'Тренинги'", bell)

    def test_one_row_per_training_time_for_the_employee(self):
        """Две жалобы, один тренинг в 14:00 — у сотрудника одна строка и
        счётчик 1: иначе две одинаковые строки с одним ключом и намёк, что
        причин две."""
        import inspect
        source = inspect.getsource(complaint_queries.training_day_bell_items)
        self_branch = source[source.index("'self'"):]
        self.assertIn('GROUP BY c.training_planned_at', self_branch)

    def test_plan_is_internal_for_the_author(self):
        """Когда коллеге назначили тренинг — деталь работы с ним: оператору,
        заведшему жалобу, не уходит (queries.public_item)."""
        self.assertIn('training_planned_at', complaint_queries.INTERNAL_FIELDS)

    def test_day_change_is_scheduled(self):
        """Строка появляется в полночь дня тренинга — записи в базе в этот
        момент нет, триггер не разбудит, нужен момент в next_change_at."""
        class Cursor(FakeCursor):
            def __init__(self):
                super().__init__()
                self.sql = ''

            def execute(self, sql, params=None):
                self.sql += sql
                super().execute(sql, params)

            def fetchone(self):
                return (None,)

        cursor = Cursor()
        hidden = tuple(name for name in sources.SOURCES if name != 'training_plans')
        sources.next_change_at(cursor, {'user_id': 1, 'hidden_sources': hidden})
        self.assertIn("date_trunc('day', c.training_planned_at)", cursor.sql)


class ReviewBellTest(unittest.TestCase):
    """Жалоба на Яндекс ждёт проверки — задача супервайзера группы оператора."""

    def setUp(self):
        self._work = complaint_queries.work_bell_items

    def tearDown(self):
        complaint_queries.work_bell_items = self._work

    def test_review_item_reads_as_a_task(self):
        complaint_queries.work_bell_items = lambda cursor, user_id, limit: (1, [{
            'id': 30, 'role': 'work', 'kind': 'review', 'at': datetime(2026, 9, 30, 9, 0),
            'target': 'yandex', 'reason_code': 'tariffs', 'driver_name': 'Сериков',
            'employee_name': None, 'training_required': False, 'training_planned_at': None}])
        total, items = sources.complaints(FakeCursor(), {'user_id': 50}, 5)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]['id'], 'review:30')
        self.assertEqual(items[0]['title'], 'Жалоба на Яндекс №30')
        self.assertIn('отправить в группу или решено', items[0]['body'])

    def test_work_item_names_the_next_step(self):
        row = {'id': 5, 'role': 'work', 'kind': 'work', 'at': datetime(2026, 9, 29, 9, 0),
               'target': 'call_center', 'reason_code': 'rude', 'driver_name': 'Сериков',
               'employee_name': 'Иванова', 'training_required': True,
               'training_planned_at': datetime(2026, 10, 2, 14, 0)}
        complaint_queries.work_bell_items = lambda cursor, user_id, limit: (1, [row])
        _total, items = sources.complaints(FakeCursor(), {'user_id': 50}, 5)
        self.assertTrue(items[0]['body'].startswith('Тренинг назначен на 02.10 в 14:00'))
        row.update(training_required=False, training_planned_at=None)
        _total, items = sources.complaints(FakeCursor(), {'user_id': 50}, 5)
        self.assertTrue(items[0]['body'].startswith('Назначьте тренинг или запишите принятые меры'))

    def test_counter_and_bell_share_the_reviewer_rule(self):
        """Бейдж «Жалоб» и строки колокола считаются одним условием —
        иначе цифра и список разойдутся молча."""
        import inspect
        self.assertIn("reviewer_sql('viewer_id')", inspect.getsource(complaint_queries.counters))
        self.assertIn("reviewer_sql('user_id')", inspect.getsource(complaint_queries.work_bell_items))


if __name__ == '__main__':
    unittest.main()
