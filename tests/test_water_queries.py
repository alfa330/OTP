# -*- coding: utf-8 -*-
"""SQL-слой «Учёта воды» без базы: то, что решает правило, а не синтаксис.

Синтаксис и настоящие ответы Postgres проверены прогоном на стенде; здесь —
логика вокруг запросов, которую легко сломать правкой: приветственный блок
обязан находиться и тогда, когда он старше окна последних выдач.
"""

import unittest
from datetime import datetime, timedelta

from water import queries

ACCOUNT = 'b' * 32


def row(kind, days_ago, account=ACCOUNT):
    at = datetime(2026, 9, 30, 12, 0) - timedelta(days=days_ago)
    # _HISTORY_COLUMNS: id, kind, blocks, created_at, driver_account_id,
    # orders_total, office_name, city, issued_by_name
    return (days_ago, kind, 1, at, account, 100, 'Офис', 'Алматы', 'Сотрудник')


class ScriptedCursor:
    """Отдаёт заранее заготовленные ответы по очереди запросов."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sql = []
        self._current = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self._current = self.answers.pop(0) if self.answers else []

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        return self._current[0] if self._current else None


class PersonHistoryTests(unittest.TestCase):
    def test_old_welcome_outside_the_window_is_still_found(self):
        """Полгода еженедельных блоков: приветственный — 21-я запись с конца.
        Без отдельного поиска новый аккаунт того же человека снова получил бы
        «можно выдать приветственный»."""
        window = [row('activity', 7 * n) for n in range(20)]
        cursor = ScriptedCursor(window, [row('welcome', 200)])
        history = queries.person_history(cursor, ACCOUNT, '900101300123')
        self.assertEqual(len(history), 21)
        self.assertEqual(history[-1]['kind'], 'welcome')
        welcome_sql, params = cursor.sql[1]
        self.assertIn("kind = 'welcome'", welcome_sql)
        self.assertEqual(params['iin'], '900101300123')

    def test_welcome_inside_the_window_costs_no_second_query(self):
        cursor = ScriptedCursor([row('activity', 1), row('welcome', 8)])
        history = queries.person_history(cursor, ACCOUNT, None)
        self.assertEqual([item['kind'] for item in history], ['activity', 'welcome'])
        self.assertEqual(len(cursor.sql), 1)

    def test_never_welcomed_person_gets_nothing_extra(self):
        cursor = ScriptedCursor([row('activity', 3)], [])
        history = queries.person_history(cursor, ACCOUNT, None)
        self.assertEqual(len(history), 1)


class CanceledIssuesTests(unittest.TestCase):
    """Отменённая выдача не считается ни в праве водителя, ни в расходе."""

    def test_history_skips_canceled_in_both_queries(self):
        cursor = ScriptedCursor([row('activity', 3)], [])
        queries.person_history(cursor, ACCOUNT, '900101300123')
        for sql, _params in cursor.sql:
            self.assertIn('canceled_at IS NULL', sql)
        # Условие по аккаунту ИЛИ ИИН — в скобках: без них «AND canceled_at»
        # прилип бы только к ИИН, и отменённые по аккаунту вернулись бы.
        self.assertIn('WHERE (driver_account_id', cursor.sql[0][0])

    def test_dashboard_counts_only_live_issues(self):
        from datetime import date
        cursor = ScriptedCursor([])
        settings = {'low_threshold': 20, 'buy_threshold': 10}
        queries.dashboard(cursor, settings, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30),
                          today=date(2026, 9, 30))
        issues_block = cursor.sql[0][0].split('FROM water_issues')[1].split('GROUP BY')[0]
        self.assertIn('canceled_at IS NULL', issues_block)

    def test_dashboard_carries_the_office_address(self):
        """«Остатки» показывают адрес офиса: по нему офис узнают за стойкой."""
        from datetime import date
        row = (1, 'Алматы', 'Офис для подключения тарифа «Бизнес»', 'ул. Байзакова 78А', 0,
               None, None, datetime(2026, 9, 30, 11, 8), 0, 0, 0, 0, 15)
        cursor = ScriptedCursor([row])
        rows = queries.dashboard(cursor, {'low_threshold': 20, 'buy_threshold': 10},
                                 date_from=date(2026, 9, 2), date_to=date(2026, 10, 1),
                                 today=date(2026, 10, 1))
        self.assertIn('o.address', cursor.sql[0][0])
        self.assertEqual((rows[0]['address'], rows[0]['stock'], rows[0]['status'], rows[0]['intake_blocks']),
                         ('ул. Байзакова 78А', 0, 'buy', 15))

    def test_cancel_locks_the_row_and_never_cancels_twice(self):
        cursor = ScriptedCursor([(7, 3, ACCOUNT, None, 2, None)])
        issue = queries.issue_for_cancel(cursor, 7, for_update=True)
        self.assertIn('FOR UPDATE', cursor.sql[0][0])
        self.assertEqual(issue['blocks'], 2)
        cursor = ScriptedCursor([], [], [])
        queries.cancel_issue(cursor, issue, reason='ошиблись', actor={'user_id': 1, 'name': 'Р'})
        self.assertIn('AND canceled_at IS NULL', cursor.sql[0][0])
        self.assertIn('stock = stock + %s', cursor.sql[1][0])
        self.assertEqual(cursor.sql[1][1], (2, 3))


class RecipientsTests(unittest.TestCase):
    def test_gone_recipients_fall_back_to_the_head(self):
        """Выбранных уволили или у них нет Telegram — о закупке всё равно
        узнает глава фронт-офисов, а не никто."""
        cursor = ScriptedCursor([], [(403,)], [(403, 'Глава', 777)])
        recipients = queries.notify_recipients(cursor, {'notify_user_ids': [57]})
        self.assertEqual(recipients, [{'user_id': 403, 'name': 'Глава', 'chat_id': 777}])

    def test_candidates_are_those_who_issue_or_manage(self):
        """Получатель «Требуется закупка» — кто выдаёт воду или ведёт учёт:
        отбор теми же правилами water.access, что и вход в раздел."""
        # id, name, city, has_telegram, role, department, headed ids, headed codes
        rows = [
            (1, 'Супер-админ', None, True, 'super_admin', 'szov', [], []),
            (2, 'Руководитель', 'Алматы', True, 'admin', 'front_office', [909], ['front_office']),
            (424, 'Офисник из списка', 'Алматы', False, 'operator', 'front_office', [], []),
            (10, 'Офисник не из списка', 'Алматы', True, 'operator', 'front_office', [], []),
            (3, 'Админ СЗоВ', None, True, 'admin', 'szov', [], []),
            (4, 'Глава ТЭЗ', None, True, 'admin', 'tez', [560], ['tez']),
        ]
        cursor = ScriptedCursor(rows)
        people = queries.notify_candidates(cursor)
        self.assertEqual([item['id'] for item in people], [1, 2, 424])
        self.assertEqual(people[2], {'id': 424, 'name': 'Офисник из списка', 'city': 'Алматы',
                                     'has_telegram': False})
        self.assertIn("COALESCE(u.status, '') <> 'fired'", cursor.sql[0][0])


if __name__ == '__main__':
    unittest.main()
