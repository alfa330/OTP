# -*- coding: utf-8 -*-
"""Центр уведомлений: порядок, изоляция источников и правила «прочитано».

Отдельного внимания стоит последний класс. Список того, что колокол разрешает
гасить, существует в двух местах — в Python (mark_seen) и в JSX (CLEARABLE), —
и это ровно тот вид раздвоения, на котором ломаются такие вещи: расхождение не
даст ни ошибки, ни падения, просто кнопка «отметить прочитанным» начнёт
обнулять счётчик обязательного документа, ничего с ним не сделав. Поэтому
списки сверяются по исходникам.
"""

import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from notifications import sources
from tests import prod_db

ROOT = Path(__file__).resolve().parents[1]
BELL_JSX = ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx'


class FakeCursor:
    """Курсор, которому важны только SAVEPOINT'ы: сами источники подменяются."""

    def __init__(self):
        self.commands = []

    def execute(self, sql, params=None):
        self.commands.append(sql.strip().split()[0].upper())

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _item(source, title, tone='default', at=None):
    return {'source': source, 'id': 1, 'title': title, 'body': '', 'at': at,
            'view': source, 'target': None, 'tone': tone}


class CollectOrderTest(unittest.TestCase):
    """Порядок в общем списке."""

    def setUp(self):
        self.original = dict(sources._HANDLERS)

    def tearDown(self):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(self.original)

    def _stub(self, mapping):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(mapping)

    def test_overdue_goes_first(self):
        self._stub({
            'events': lambda c, v: (1, [_item('events', 'Новый пост')]),
            'wiki_ack': lambda c, v: (1, [_item('wiki_ack', 'Регламент', tone='warning')]),
        })
        _, items = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual('Регламент', items[0]['title'])

    def test_sources_keep_their_own_order(self):
        """Внутри источника порядок задаёт его ORDER BY и трогать его нельзя.

        У ознакомлений и опросов `at` — это СРОК, а не время события. Сортировка
        всего списка по дате подняла бы наверх самый дальний дедлайн.
        """
        self._stub({'wiki_ack': lambda c, v: (3, [
            _item('wiki_ack', 'Завтра', at='2026-08-10T00:00:00'),
            _item('wiki_ack', 'Через месяц', at='2026-09-10T00:00:00'),
            _item('wiki_ack', 'Без срока', at=None),
        ])})
        _, items = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(['Завтра', 'Через месяц', 'Без срока'],
                         [i['title'] for i in items])

    def test_total_is_sum_of_sources(self):
        self._stub({
            'events': lambda c, v: (2, []),
            'lms': lambda c, v: (3, []),
        })
        counts, _ = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(5, counts['total'])

    def test_broken_source_does_not_kill_the_rest(self):
        """Сломанный раздел даёт ноль, а не 500 на весь колокол."""
        def explode(cursor, viewer):
            raise RuntimeError('таблицы ещё нет')

        self._stub({'wiki_ack': explode, 'lms': lambda c, v: (4, [_item('lms', 'Урок')])})
        with self.assertLogs(level='ERROR'):
            counts, items = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(0, counts['wiki_ack'])
        self.assertEqual(4, counts['lms'])
        self.assertEqual(4, counts['total'])
        self.assertEqual(['Урок'], [i['title'] for i in items])

    def test_broken_source_rolls_back_to_savepoint(self):
        """Иначе упавший источник оставил бы транзакцию в aborted-состоянии."""
        def explode(cursor, viewer):
            cursor.execute('SELECT неверно')
            raise RuntimeError('boom')

        self._stub({'events': explode})
        cursor = FakeCursor()
        with self.assertLogs(level='ERROR'):
            sources.collect(cursor, {'user_id': 1})
        self.assertIn('ROLLBACK', cursor.commands)
        self.assertEqual(cursor.commands.count('SAVEPOINT'), cursor.commands.count('RELEASE'))

    def test_hidden_source_is_skipped_entirely(self):
        called = []
        self._stub({'four_you': lambda c, v: (called.append(1), (9, []))[1]})
        counts, _ = sources.collect(FakeCursor(), {'user_id': 1, 'hidden_sources': ('four_you',)})
        self.assertEqual(0, counts['four_you'])
        self.assertEqual([], called)


class SurveyWindowTimezoneTest(unittest.TestCase):
    """Окно теста считается во времени Алматы, а не в UTC базы.

    starts_at/ends_at хранятся наивными во времени Алматы: их пишет
    _parse_survey_schedule_value без tzinfo, а сравнивает survey_test_status с
    datetime.now() — процесс живёт в Asia/Almaty. База стоит в UTC, поэтому
    голый CURRENT_TIMESTAMP в запросе даёт сдвиг ровно на 5 часов, и колокол
    показывает тест открытым ещё пять часов после закрытия.

    Дефект молчаливый: ошибки нет, цифра просто неверная. Поэтому проверяется и
    исходник (чтобы CURRENT_TIMESTAMP не вернулся), и поведение на настоящей
    базе.
    """

    SOURCE = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')

    def test_window_does_not_use_bare_current_timestamp(self):
        block = re.search(r'def surveys\(.*?\n    \)\n', self.SOURCE, re.S)
        self.assertIsNotNone(block, 'не найдено тело запроса опросов')
        sql = block.group(0)
        self.assertNotRegex(
            sql, r'(starts_at|ends_at)\s*[<>]=?\s*CURRENT_TIMESTAMP',
            'окно теста снова сравнивается с UTC-временем базы — сдвиг 5 часов',
        )
        self.assertIn('%(now)s', sql, 'время должно приходить параметром')

    def test_almaty_now_matches_process_clock(self):
        """Тот же вызов, что и у Database.survey_test_status."""
        self.assertAlmostEqual(
            sources._almaty_now().timestamp(), datetime.now().timestamp(), delta=2,
        )

    def test_closed_window_is_closed_on_real_postgres(self):
        """Окно, закрывшееся два часа назад, обязано считаться закрытым."""
        reason = prod_db.skip_reason()
        if reason:
            self.skipTest(reason)
        now = sources._almaty_now()
        closed = now - timedelta(hours=2)
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute('SELECT %(ends)s::timestamp > %(now)s::timestamp',
                           {'ends': closed, 'now': now})
            self.assertFalse(cursor.fetchone()[0],
                             'закрывшийся тест не должен считаться открытым')

            # Показываем, чем именно был дефект: сдвиг между часами процесса и
            # часами базы. Утверждение делаем только если сдвиг реально есть —
            # на машине разработчика в UTC его не будет, и падать тут не за что.
            cursor.execute('SELECT CURRENT_TIMESTAMP::timestamp')
            db_now = cursor.fetchone()[0]
            shift_hours = (now - db_now).total_seconds() / 3600
            if shift_hours <= 2:
                self.skipTest('часы процесса и базы совпадают (сдвиг %.1f ч) — '
                              'сравнивать не с чем' % shift_hours)
            cursor.execute('SELECT %(ends)s::timestamp > CURRENT_TIMESTAMP',
                           {'ends': closed})
            self.assertTrue(
                cursor.fetchone()[0],
                'при сдвиге %.1f ч голый CURRENT_TIMESTAMP обязан был бы считать '
                'закрытый тест открытым — иначе дефект был не в этом' % shift_hours,
            )
        finally:
            prod_db.rollback()
            cursor.close()


class MarkSeenRulesTest(unittest.TestCase):
    """Что вообще можно погасить просмотром, а что только действием."""

    def test_action_bound_sources_are_not_clearable(self):
        cursor = FakeCursor()
        for source in ('wiki_ack', 'surveys'):
            self.assertFalse(
                sources.mark_seen(cursor, 1, source),
                'источник %s нельзя гасить: он снимается действием, иначе счётчик '
                'обязательного документа обнулялся бы просмотром колокола' % source,
            )
        self.assertEqual([], cursor.commands)

    def test_watermark_sources_are_clearable(self):
        for source in ('events', 'four_you', 'lms'):
            cursor = FakeCursor()
            self.assertTrue(sources.mark_seen(cursor, 1, source))
            self.assertTrue(cursor.commands, 'должен был выполнить запрос')

    def test_unknown_source_is_ignored(self):
        self.assertFalse(sources.mark_seen(FakeCursor(), 1, 'нет-такого'))


class FrontendAgreesWithBackendTest(unittest.TestCase):
    """Списки гасимых источников во фронте и в бэке обязаны совпадать."""

    def test_clearable_lists_match(self):
        source = BELL_JSX.read_text(encoding='utf-8')
        match = re.search(r"const CLEARABLE = \[([^\]]*)\]", source)
        self.assertIsNotNone(match, 'в компоненте пропал список CLEARABLE')
        frontend = set(re.findall(r"'([a-z_]+)'", match.group(1)))

        backend = {name for name in sources.SOURCES
                   if sources.mark_seen(FakeCursor(), 1, name)}
        self.assertEqual(
            backend, frontend,
            'фронт предлагает гасить не то, что умеет гасить сервер: '
            'кнопка «отметить прочитанным» будет обнулять счётчик впустую',
        )

    def test_every_source_has_a_label_in_the_bell(self):
        """Иначе в колоколе вместо раздела покажется его технический код."""
        source = BELL_JSX.read_text(encoding='utf-8')
        block = re.search(r"const SOURCE_META = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(block)
        labelled = set(re.findall(r"^\s{4}([a-z_]+):", block.group(1), re.M))
        self.assertEqual(set(sources.SOURCES), labelled)


if __name__ == '__main__':
    unittest.main()
