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


class RecipientsTests(unittest.TestCase):
    def test_gone_recipients_fall_back_to_the_head(self):
        """Выбранных уволили или у них нет Telegram — о закупке всё равно
        узнает глава фронт-офисов, а не никто."""
        cursor = ScriptedCursor([], [(403,)], [(403, 'Глава', 777)])
        recipients = queries.notify_recipients(cursor, {'notify_user_ids': [57]})
        self.assertEqual(recipients, [{'user_id': 403, 'name': 'Глава', 'chat_id': 777}])

    def test_candidates_exclude_heads_of_other_departments(self):
        cursor = ScriptedCursor([])
        queries.notify_candidates(cursor)
        sql = cursor.sql[0][0]
        self.assertIn("u.role = 'admin' AND u.id NOT IN (SELECT id FROM heads)", sql)
        self.assertNotIn("u.role IN ('admin', 'super_admin')", sql)


if __name__ == '__main__':
    unittest.main()
