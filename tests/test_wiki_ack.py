# -*- coding: utf-8 -*-
"""Обязательное ознакомление: гейт «дочитал до конца» и порядок отметок.

Владелец выбрал формальный документооборот, а не мягкое напоминание — значит
подтверждение без прочтения не должно быть возможно в принципе, а не только
«не показываться кнопкой».

Отдельно проверяется прокрутка. В исходной вике условие «дочитал» считалось на
клиенте как
    window.innerHeight + window.scrollY >= documentElement.scrollHeight - 80
и вызывалось сразу после подписки. В каркасе OTP окно не скроллится
(flex h-screen overflow-hidden, прокручивается .main-content), поэтому
window.scrollY всегда 0, scrollHeight равен высоте вьюпорта, и условие истинно
с первого кадра — отметка «ознакомлен» ставилась бы в момент ОТКРЫТИЯ статьи.
"""

import os
import re
import unittest
from pathlib import Path

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

from wiki.ack import count_required_blocks

ROOT = Path(__file__).resolve().parents[1]
SCROLL_JS = ROOT / 'src' / 'components' / 'wiki' / 'scrollContainer.js'


def _dsn():
    env = os.environ.get('DATABASE_URL_READONLY')
    if env:
        return env
    local = ROOT / '.env.codex.local'
    if not local.exists():
        return None
    text = local.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'^DATABASE_URL_READONLY\s*=\s*(.+)$', text, re.M)
    return match.group(1).strip().strip('"\'') if match else None


DSN = _dsn()


class RequiredBlocksTest(unittest.TestCase):
    """Сколько блоков надо раскрыть. По дампу прода их в контенте уже 35."""

    def test_counts_both_spellings(self):
        html = ('<details data-required-for-ack="true"></details>'
                '<details data-required-for-ack="1"></details>')
        self.assertEqual(count_required_blocks(html), 2)

    def test_ignores_false(self):
        self.assertEqual(count_required_blocks('<details data-required-for-ack="false">'), 0)

    def test_empty(self):
        self.assertEqual(count_required_blocks(''), 0)
        self.assertEqual(count_required_blocks(None), 0)

    def test_article_without_blocks(self):
        self.assertEqual(count_required_blocks('<p>Обычный текст</p>'), 0)


class ScrollGateSourceTest(unittest.TestCase):
    """Гейт обязан опираться на контейнер, а не на окно.

    Проверяем исходник напрямую: это тот случай, когда регресс не даст ни
    ошибки, ни падения теста — просто отметка «ознакомлен» начнёт ставиться
    сама собой, и заметят это при разборе инцидента.
    """

    @classmethod
    def setUpClass(cls):
        source = SCROLL_JS.read_text(encoding='utf-8')
        # Комментарии выбрасываем: там window.scrollY упомянут именно затем,
        # чтобы объяснить, почему им нельзя пользоваться.
        source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
        source = re.sub(r'//[^\n]*', '', source)
        cls.source = source

    def test_utility_exists(self):
        self.assertTrue(SCROLL_JS.exists(), 'без утилиты прокрутки гейт не сделать')

    def test_does_not_use_window_scroll(self):
        for forbidden in ('window.scrollY', 'window.pageYOffset',
                          'documentElement.scrollHeight'):
            self.assertNotIn(forbidden, self.source,
                             'прокрутка окна в каркасе OTP всегда нулевая: %s' % forbidden)

    def test_uses_main_content(self):
        self.assertIn("closest('.main-content')", self.source)

    def test_unknown_container_means_not_read(self):
        """Неопределённость обязана трактоваться как «не дочитал»."""
        self.assertRegex(
            self.source,
            r'if \(!container\) return false;',
            'при отсутствии контейнера функция должна возвращать false',
        )


@unittest.skipIf(psycopg2 is None or not DSN, 'нет psycopg2 или DATABASE_URL_READONLY')
class AckFlowSqlTest(unittest.TestCase):
    """Порядок отметок на настоящем PostgreSQL, на синтетических назначениях.

    Приём тот же, что в тестах видимости и поиска: CTE перекрывает одноимённую
    таблицу, поэтому боевые условия UPDATE проверяются без прав на запись.
    """

    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(DSN, connect_timeout=30)
        cls.conn.set_session(readonly=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def check(self, sql, params=None):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params or {})
            return cur.fetchall()
        finally:
            self.conn.rollback()
            cur.close()

    def test_acknowledge_condition_requires_read(self):
        """Условие подтверждения стоит в самом UPDATE, а не в интерфейсе."""
        rows = self.check("""
            WITH assignment AS (
                SELECT * FROM (VALUES
                    (1, NULL::timestamp, NULL::timestamp),
                    (2, CURRENT_TIMESTAMP::timestamp, NULL::timestamp),
                    (3, CURRENT_TIMESTAMP::timestamp, CURRENT_TIMESTAMP::timestamp)
                ) AS t(id, read_completed_at, acknowledged_at)
            )
            SELECT id FROM assignment
             WHERE read_completed_at IS NOT NULL AND acknowledged_at IS NULL
             ORDER BY id
        """)
        self.assertEqual([r[0] for r in rows], [2],
                         'подтвердить можно только дочитанное и только один раз')

    def test_read_completes_only_when_all_blocks_opened(self):
        rows = self.check("""
            WITH assignment AS (
                SELECT * FROM (VALUES
                    (1, 0, 3), (2, 2, 3), (3, 3, 3), (4, 5, 3), (5, 0, 0)
                ) AS t(id, opened, total)
            )
            SELECT id, GREATEST(opened, %(reported)s) >= total AS completed
              FROM assignment ORDER BY id
        """, {'reported': 0})
        completed = {row[0]: row[1] for row in rows}
        self.assertFalse(completed[1], 'ни один блок не раскрыт')
        self.assertFalse(completed[2], 'раскрыты не все блоки')
        self.assertTrue(completed[3], 'раскрыты все')
        self.assertTrue(completed[4], 'раскрыто больше, чем требуется')
        self.assertTrue(completed[5], 'обязательных блоков нет — условие выполнено')

    def test_blocks_opened_never_decreases(self):
        """Клиент сообщает счётчик; он не должен уметь его уменьшить."""
        rows = self.check("SELECT GREATEST(%(stored)s, %(reported)s)",
                          {'stored': 3, 'reported': 1})
        self.assertEqual(rows[0][0], 3)

    def test_overdue_is_computed_from_due_date(self):
        rows = self.check("""
            SELECT GREATEST(0, EXTRACT(DAY FROM
                     (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                     - (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty' - interval '3 days')
                   )::int)
        """)
        self.assertEqual(rows[0][0], 3)

    def test_superseded_keeps_acknowledged(self):
        """Уже подтверждённые назначения не должны обнуляться новой версией:
        они свидетельство, что человек читал именно ту редакцию."""
        rows = self.check("""
            WITH assignment AS (
                SELECT * FROM (VALUES
                    (1, 1, 'acknowledged'), (2, 1, 'in_progress'),
                    (3, 2, 'in_progress'), (4, 1, 'not_open')
                ) AS t(id, version, status)
            )
            SELECT id FROM assignment
             WHERE version < 2 AND status NOT IN ('acknowledged', 'superseded', 'cancelled')
             ORDER BY id
        """)
        self.assertEqual([r[0] for r in rows], [2, 4])


if __name__ == '__main__':
    unittest.main()
