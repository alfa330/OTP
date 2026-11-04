# -*- coding: utf-8 -*-
"""Наследование ограничителя помощника — без Postgres, чтобы сторожил и CI.

tests/test_wiki_ai_perimeter.py проверяет то же над настоящим Postgres, но в
GitHub Actions базы нет, и все его наборы там пропускаются: убрать рекурсию из
AI_CLOSED_SECTIONS_SQL или сам фрагмент из периметра помощника CI не заметил бы.
Фрагмент написан на общем SQL (WITH RECURSIVE, UNION, NOT EXISTS), поэтому его
можно выполнить во встроенном sqlite3 над теми же таблицами. Боевой текст не
меняется — подставляются только параметры psycopg.
"""

import sqlite3
import unittest

from wiki import perimeter
from wiki.ai import index as ai_index

# Дерево как у «Старой вики» на проде: 39 — закрытый корень, 43 — подпапка,
# 90 — подпапка подпапки (в архиве: K2 — статус не спасает), 50 — открытая
# соседняя ветка; 70 ↔ 71 — петля на битых данных.
_SECTIONS = [
    (39, 1, None, 'active'), (43, 0, 39, 'active'), (90, 0, 43, 'archived'),
    (50, 0, None, 'active'), (60, 0, None, 'active'), (61, 1, 60, 'archived'),
    (70, 1, 71, 'active'), (71, 0, 70, 'active'),
]
# статья → разделы
_PLACES = {
    1: [39],        # корень закрытой
    2: [43],        # подпапка
    3: [90],        # внук в архивной папке
    4: [50],        # открытая ветка
    5: [50, 90],    # два раздела, один закрыт — строго закрыта
    6: [60],        # родитель закрытой подпапки — открыт
    7: [61],        # закрытая подпапка в архиве — закрыта всё равно (K2)
    8: [71],        # внутри петли под закрытым 70
    9: [],          # без раздела — открыта (шапка wiki/perimeter.py)
}
_OPEN = {4, 6, 9}


class GateSqliteTest(unittest.TestCase):
    def setUp(self):
        db = sqlite3.connect(':memory:')
        db.executescript("""
            CREATE TABLE wiki_sections (id INTEGER PRIMARY KEY, ai_opt_out BOOLEAN,
                                        parent_section_id INTEGER, status TEXT,
                                        name TEXT);
            CREATE TABLE wiki_articles (id INTEGER PRIMARY KEY, status TEXT,
                                        strict_mode BOOLEAN, ai_opt_out BOOLEAN,
                                        content TEXT, content_plain TEXT);
            CREATE TABLE wiki_article_sections (article_id INTEGER, section_id INTEGER);
        """)
        db.executemany('INSERT INTO wiki_sections VALUES (?, ?, ?, ?, ?)',
                       [row + ('раздел %d' % row[0],) for row in _SECTIONS])
        for article_id, sections in _PLACES.items():
            db.execute("INSERT INTO wiki_articles VALUES (?, 'published', 0, 0, 'x', 'x')",
                       (article_id,))
            db.executemany('INSERT INTO wiki_article_sections VALUES (?, ?)',
                           [(article_id, s) for s in sections])
        # Петля без UNION повисла бы навсегда: обрываем и падаем, а не висим.
        self.steps = 0

        def _guard():
            self.steps += 1
            return 1 if self.steps > 200000 else 0
        db.set_progress_handler(_guard, 100)
        self.addCleanup(db.close)
        self.db = db

    def _ids(self, sql, params=()):
        return {row[0] for row in self.db.execute(sql, params)}

    def test_gate_closes_the_whole_subtree_and_only_it(self):
        got = self._ids('SELECT a.id FROM wiki_articles a WHERE '
                        + perimeter.AI_SECTION_GATE_SQL)
        self.assertEqual(_OPEN, got)

    def test_assistant_perimeter_uses_the_gate(self):
        """Тот самый запрос, которым помощник и партнёрский API режут периметр."""
        candidates = ', '.join(str(i) for i in _PLACES)
        sql = perimeter._AI_ELIGIBLE_SQL.replace('= ANY(%(candidates)s)',
                                                 'IN (%s)' % candidates)
        self.assertNotIn('%(', sql)
        self.assertEqual(_OPEN, self._ids(sql))

    def test_index_selection_uses_the_gate(self):
        """Вход в индекс: закрытая статья не должна попасть в куски и эмбеддинги."""
        sql = ai_index._SELECT_ARTICLE.replace('%(article_id)s', '?')
        got = {aid for aid in _PLACES
               if self.db.execute(sql, (aid,)).fetchone()[3]}
        self.assertEqual(_OPEN, got)

    def test_label_names_the_marked_section_not_the_subfolder(self):
        """Метка в поиске и каталоге — имя ПОМЕЧЕННОГО раздела.

        Подпапка «Старой вики» называется «actions», и метка с её именем ничего
        бы не объяснила. И метка есть ровно у тех статей, которые закрыло
        условие помощника: разойдись они, поиск подписывал бы «закрыто» то, что
        помощник читает, или наоборот.
        """
        ids = ', '.join(str(i) for i in _PLACES)
        sql = perimeter._AI_CLOSED_NAMES_SQL.replace('= ANY(%(ids)s)', 'IN (%s)' % ids)
        self.assertNotIn('%(', sql)
        got = dict(self.db.execute(sql).fetchall())
        self.assertEqual({1: 'раздел 39', 2: 'раздел 39', 3: 'раздел 39', 5: 'раздел 39',
                          7: 'раздел 61', 8: 'раздел 70'}, got)
        self.assertEqual(set(_PLACES) - _OPEN, set(got))


if __name__ == '__main__':
    unittest.main()
