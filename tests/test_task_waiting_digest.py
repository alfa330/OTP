# -*- coding: utf-8 -*-
"""Сообщение бота после входа — задачи, которые ждут человека (19.11.2026).

Уведомление о постановке уходит только на уже привязанный Telegram: задачу поставили
за 14 минут до того, как исполнитель вошёл в бота, — и в Telegram о ней не пришло
ничего. Теперь вход досылает такие задачи одним сообщением; здесь — его текст и кнопка
(tasks/waiting_digest.py). Смысл выборки — в test_task_waiting_digest_postgres.py,
вызов из входа — в test_bot_access_menu.py. Люди и задачи выдуманы.
"""

import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks import waiting_digest  # noqa: E402


def _link(task_id, key):
    return f"https://portal.test/?view=tasks&task={key}" if key else f"https://portal.test/?view=tasks&task_id={task_id}"


def _item(task_id, subject='Тема', **extra):
    item = {'id': task_id, 'subject': subject, 'status': 'assigned', 'priority': 'normal',
            'due_at': None, 'key': None, 'creator_name': 'Постановщик Тестовый'}
    item.update(extra)
    return item


class WaitingDigestMessageTest(unittest.TestCase):

    def test_nothing_waiting_means_no_message(self):
        self.assertIsNone(waiting_digest.build_message({'total': 0, 'items': []}, _link))
        self.assertIsNone(waiting_digest.build_message(None, _link))

    def test_single_task_reads_like_the_assignment_notice_and_has_the_open_button(self):
        text, markup = waiting_digest.build_message(
            {'total': 1, 'items': [_item(7001, 'Контент к празднику', key='PR-7')]}, _link)
        self.assertEqual(text, '\n'.join([
            '<b>📋 Вас ждут задачи: 1</b>',
            '',
            '1. PR-7 · <a href="https://portal.test/?view=tasks&amp;task=PR-7">Контент к празднику</a>'
            ' — от Постановщик Тестовый',
            '',
            '<b>Откройте раздел «Задачи», чтобы посмотреть детали.</b>',
        ]))
        self.assertEqual(markup, {'inline_keyboard': [[
            {'text': 'Открыть задачу', 'url': 'https://portal.test/?view=tasks&task=PR-7'}]]})

    def test_list_numbers_tasks_marks_only_what_differs_and_has_no_button(self):
        text, markup = waiting_digest.build_message({'total': 3, 'items': [
            _item(1, 'Срочное', due_at=datetime(2026, 11, 25, 18, 0), priority='urgent'),
            _item(2, 'На доработку', status='returned', due_at='2026-11-26T09:30:00'),
            _item(3, 'Без срока', priority='critical', creator_name=None),
        ]}, _link)
        lines = text.split('\n')
        self.assertEqual(lines[0], '<b>📋 Вас ждут задачи: 3</b>')
        self.assertEqual(lines[2], '1. <a href="https://portal.test/?view=tasks&amp;task_id=1">Срочное</a>'
                                   ' — от Постановщик Тестовый, до 25.11.2026 18:00, срочная')
        self.assertEqual(lines[3], '2. <a href="https://portal.test/?view=tasks&amp;task_id=2">На доработку</a>'
                                   ' — от Постановщик Тестовый, до 26.11.2026 09:30, возвращена на доработку')
        self.assertEqual(lines[4], '3. <a href="https://portal.test/?view=tasks&amp;task_id=3">Без срока</a>'
                                   ' — критичная')
        self.assertNotIn('обычная', text)  # обычная срочность — не отличие, её не пишем
        self.assertIsNone(markup)          # у списка ссылка на каждой теме

    def test_names_and_subjects_are_escaped(self):
        text, _ = waiting_digest.build_message({'total': 1, 'items': [
            _item(5, 'Цены <b>&</b> акции', creator_name='Иван <script>', key='A&B-1')]}, _link)
        self.assertIn('Цены &lt;b&gt;&amp;&lt;/b&gt; акции', text)
        self.assertIn('от Иван &lt;script&gt;', text)
        self.assertIn('A&amp;B-1 · ', text)
        self.assertNotIn('<script>', text)

    def test_without_a_link_the_subject_is_plain_and_there_is_no_button(self):
        text, markup = waiting_digest.build_message({'total': 1, 'items': [_item(7, 'Тема')]},
                                                    lambda task_id, key: '')
        self.assertIn('1. Тема — от Постановщик Тестовый', text)
        self.assertNotIn('<a ', text)
        self.assertIsNone(markup)

    def test_more_than_listed_ends_with_the_rest_count(self):
        items = [_item(i, f'Задача {i}') for i in range(1, waiting_digest.DIGEST_LIMIT + 1)]
        text, markup = waiting_digest.build_message({'total': 14, 'items': items}, _link)
        self.assertIn('<b>📋 Вас ждут задачи: 14</b>', text)
        self.assertIn(f'{waiting_digest.DIGEST_LIMIT}. <a ', text)
        self.assertIn(f'…и ещё {14 - waiting_digest.DIGEST_LIMIT} — в разделе «Задачи».', text)
        self.assertIsNone(markup)

    def test_long_subjects_stay_within_the_telegram_limit(self):
        items = [_item(i, 'Т' * 255, creator_name='Ф' * 160, key=f'LONGCODE-{i}',
                       due_at=datetime(2026, 12, 1, 10, 0), status='returned', priority='critical')
                 for i in range(1, waiting_digest.DIGEST_LIMIT + 1)]
        long_link = lambda task_id, key: 'https://portal.test/' + 'p' * 300 + f'?task={key}'  # noqa: E731
        text, _ = waiting_digest.build_message({'total': 40, 'items': items}, long_link)
        self.assertLessEqual(len(text), 4096)
        listed = re.findall(r'^(\d+)\. ', text, flags=re.M)
        self.assertEqual(listed, [str(n) for n in range(1, len(listed) + 1)])
        self.assertIn(f'…и ещё {40 - len(listed)} — в разделе «Задачи».', text)
        self.assertTrue(text.endswith('<b>Откройте раздел «Задачи», чтобы посмотреть детали.</b>'))

    def test_total_never_below_what_is_listed(self):
        text, _ = waiting_digest.build_message({'total': 0, 'items': [_item(1), _item(2)]}, _link)
        self.assertIn('Вас ждут задачи: 2', text)
        self.assertNotIn('и ещё', text)


if __name__ == '__main__':
    unittest.main()
