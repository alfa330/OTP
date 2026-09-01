# -*- coding: utf-8 -*-
"""Главная вики показывает только опубликованное.

Решение владельца 01.09.2026: черновики и архив живут на вкладке «Статьи», где
под них заведены корзины (ARTICLE_BUCKETS), а на главной они мешают. Пропорция,
из-за которой это стало заметно, — боевая: в пространстве «Таксопарки» на 51
опубликованную статью приходится 239 черновиков и 25 архивных, то есть дерево
витрины на 84 % состояло из чужой незаконченной работы, и архивная статья ничем
не отличалась от живой — плашка была только у черновика.

Правило действует на ДВУХ концах главной, и сторожить надо оба:

1. ПОЛКИ («Избранное», «Продолжить чтение», «Популярные») — сервер,
   articles.recent_and_popular. Условие обязано стоять В ЗАПРОСЕ, до LIMIT:
   отсев после него давал бы полку то на десять строк, то на две, в зависимости
   от того, что человек читал вчера.

2. ОГЛАВЛЕНИЕ (правая колонка витрины) — фронт, WikiIndexPanel.jsx. Отсев там
   и должен остаться: тот же список уезжает в пикер внутренних ссылок редактора
   (WikiLibrary отдаёт его в WikiEditor), а цели ссылок на проде почти все
   черновые — фильтр в самом запросе выключил бы пикер целиком.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wiki import articles as wiki_articles  # noqa: E402

PANEL = (ROOT / 'src' / 'components' / 'wiki' / 'WikiIndexPanel.jsx').read_text(encoding='utf-8')
LIBRARY = (ROOT / 'src' / 'components' / 'wiki' / 'WikiLibrary.jsx').read_text(encoding='utf-8')


class RecordingCursor:
    """Запоминает запросы; на любой fetchall отвечает пустотой."""

    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((' '.join(sql.split()), params))

    def fetchall(self):
        return []


class ShelvesTest(unittest.TestCase):
    """Полки главной — настоящий SQL настоящей функции, а не текст файла."""

    def _queries(self):
        cursor = RecordingCursor()
        result = wiki_articles.recent_and_popular(cursor, [1, 2, 3], user_id=7)
        self.assertEqual(sorted(result), ['favorites', 'popular', 'recent'])
        self.assertEqual(len(cursor.queries), 3,
                         'полок три — история, популярное и избранное')
        return [sql for sql, _params in cursor.queries]

    def test_every_shelf_asks_for_published(self):
        for sql in self._queries():
            self.assertIn("a.status = 'published'", sql,
                          f'полка отдаёт черновики и архив: {sql[:80]}…')

    def test_condition_stands_in_where_not_after_limit(self):
        """Отсев в WHERE, а не в питоне после выборки.

        Ловушка не гипотетическая: обрежь список уже после LIMIT — и полка на
        десять строк покажет две, потому что восемь из них были черновиками.
        """
        for sql in self._queries():
            status = sql.index("a.status = 'published'")
            self.assertGreater(status, sql.index('WHERE'), 'условие статуса вне WHERE')
            # У избранного потолка нет — оно короткое по природе.
            if 'LIMIT' in sql:
                self.assertLess(status, sql.index('LIMIT'),
                                'условие статуса уехало за LIMIT')

    def test_empty_perimeter_does_not_touch_the_database(self):
        cursor = RecordingCursor()
        self.assertEqual(
            wiki_articles.recent_and_popular(cursor, [], user_id=7),
            {'recent': [], 'popular': [], 'favorites': []})
        self.assertEqual(cursor.queries, [])


class IndexPanelTest(unittest.TestCase):
    """Оглавление витрины. Отрисовку проверяет tests/wiki_index_panel_published.test.mjs."""

    def test_panel_filters_perimeter_to_published(self):
        self.assertIn("perimeter.filter((a) => a.status === 'published')", PANEL,
                      'оглавление снова показывает черновики и архив')

    def test_no_draft_marks_left_in_the_panel(self):
        """Плашка «Черновик» и счётчик «N черн.» убраны: помечать здесь нечего.

        Ищем по КОДУ, а не по слову «черновик»: объяснение, почему их тут нет,
        живёт в комментарии того же файла, и запрет на слово запретил бы заодно
        и объяснение.
        """
        self.assertNotIn("'draft'", PANEL, 'плашка «Черновик» вернулась в строку статьи')
        self.assertNotIn('черн.', PANEL, 'счётчик черновиков вернулся в шапку панели')
        self.assertEqual(PANEL.count("status === "), 1,
                         'статус сравнивается больше одного раза — отсев размазан по панели')

    def test_index_request_keeps_the_whole_perimeter(self):
        """Фильтр не должен уехать в запрос — на нём стоит пикер внутренних ссылок.

        Пикер получает ровно этот список (WikiLibrary: articles={index} у
        WikiEditor), а цели ссылок в проде почти все черновые: 238 черновиков и
        15 архивных из 253 пар. Запроси мы только опубликованное — пикер стал бы
        пустым, и вставить внутреннюю ссылку было бы неоткуда.
        """
        start = LIBRARY.index('const loadIndex')
        request = LIBRARY[start:start + 800]
        self.assertIn("params: { limit, offset, space_id: spaceId }", request)
        self.assertNotIn('bucket', request,
                         'оглавление запросило одну корзину — пикер ссылок останется пустым')
        self.assertIn('articles={index}', LIBRARY,
                      'пикер внутренних ссылок больше не получает периметр')


if __name__ == '__main__':
    unittest.main()
