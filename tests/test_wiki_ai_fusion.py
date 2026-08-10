# -*- coding: utf-8 -*-
"""Слияние ветвей ретривера. Чистые тесты: ни базы, ни сети.

Закрепляется решение, которое далось замером и легко откатится назад «для
симметрии»: ветки НЕРАВНОПРАВНЫ. Порядок задаёт вектор, лексика только добирает
пропущенное. Замер на боевом корпусе (29 вопросов, 202 куска), первым
результатом по-русски: вектор 22/25, равновесный RRF 19/25, RRF с весом лексики
0,2-0,5 — 20/25, слияние dense-first — 22/25. Полнота у всех 25/25.

Причина провала RRF механическая: при RRF_K = 60 разница вкладов первого и
второго места одной ветки 0,00003, а вклад лексического первого места с весом 0,2
— 0,0033, в сто раз больше. Лексика становится решающим тайбрейкером.
"""

import unittest

from wiki.ai.retrieve import BRANCH_WEIGHTS, RRF_K, fuse, fuse_rrf, search_hybrid


def row(chunk_id, article_id, **extra):
    base = {'chunk_id': chunk_id, 'article_id': article_id, 'chunk_idx': 0,
            'slug': f'a{article_id}', 'title': f'Статья {article_id}',
            'heading_path': '', 'text': f'текст {chunk_id}',
            'requires_ack': False}
    base.update(extra)
    return base


class FuseTest(unittest.TestCase):
    def test_dense_order_is_preserved(self):
        dense = [row(1, 10), row(2, 11), row(3, 12)]
        lexical = [row(9, 13)]
        out = fuse(lexical, dense, limit=10, per_article=3)
        self.assertEqual([1, 2, 3, 9], [item['chunk_id'] for item in out])

    def test_lexical_only_rows_go_after_dense(self):
        dense = [row(1, 10)]
        lexical = [row(5, 11), row(6, 12)]
        out = fuse(lexical, dense, limit=10, per_article=3)
        self.assertEqual([1, 5, 6], [item['chunk_id'] for item in out])

    def test_duplicates_are_not_repeated(self):
        dense = [row(1, 10), row(2, 11)]
        lexical = [row(2, 11), row(3, 12)]
        out = fuse(lexical, dense, limit=10, per_article=3)
        self.assertEqual([1, 2, 3], [item['chunk_id'] for item in out])

    def test_found_by_marks_both_branches(self):
        dense = [row(1, 10), row(2, 11)]
        lexical = [row(2, 11)]
        out = {item['chunk_id']: item['found_by'] for item in fuse(lexical, dense)}
        self.assertEqual([1], out[1])          # только вектор
        self.assertEqual([0, 1], out[2])       # обе ветки

    def test_per_article_cap(self):
        dense = [row(i, 10) for i in range(1, 6)]
        out = fuse([], dense, limit=10, per_article=2)
        self.assertEqual(2, len(out))

    def test_cap_does_not_stop_other_articles(self):
        """Лимит на статью не должен обрывать выдачу целиком."""
        dense = [row(1, 10), row(2, 10), row(3, 10), row(4, 11)]
        out = fuse([], dense, limit=10, per_article=2)
        self.assertEqual([1, 2, 4], [item['chunk_id'] for item in out])

    def test_limit(self):
        dense = [row(i, 100 + i) for i in range(10)]
        self.assertEqual(3, len(fuse([], dense, limit=3, per_article=3)))

    def test_empty_branches(self):
        self.assertEqual([], fuse([], [], limit=5, per_article=3))

    def test_lexical_alone_still_works(self):
        """Вектора нет (нет ключа, лежит провайдер) — выдача не пустеет."""
        lexical = [row(1, 10), row(2, 11)]
        out = fuse(lexical, [], limit=5, per_article=3)
        self.assertEqual([1, 2], [item['chunk_id'] for item in out])


class RrfStillAvailableTest(unittest.TestCase):
    """RRF оставлен как инструмент для равноправных ветвей — он должен работать."""

    def test_weights_shift_order(self):
        dense = [row(1, 10), row(2, 11)]
        lexical = [row(2, 11), row(1, 10)]
        heavy_lexical = fuse_rrf(lexical, dense, limit=5, weights=(5.0, 1.0))
        self.assertEqual(2, heavy_lexical[0]['chunk_id'])
        heavy_dense = fuse_rrf(lexical, dense, limit=5, weights=(0.0, 1.0))
        self.assertEqual(1, heavy_dense[0]['chunk_id'])

    def test_default_weights_favour_dense(self):
        self.assertLess(BRANCH_WEIGHTS[0], BRANCH_WEIGHTS[1])

    def test_rrf_k_is_documented_value(self):
        self.assertEqual(60, RRF_K)


class _FakeCursor:
    """Курсор, отдающий заранее заданные строки: проверяем только развилку."""

    def __init__(self, lexical_rows):
        self._rows = lexical_rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return self._rows


class DegradedModeTest(unittest.TestCase):
    def test_no_vector_means_degraded_but_alive(self):
        cursor = _FakeCursor([
            (1, 10, 'Статья', 'slug', 0, '', 'текст', False, 1.5, True),
        ])
        found = search_hybrid(cursor, article_ids=[10], query='аренда',
                              query_vector=None)
        self.assertTrue(found['degraded'])
        self.assertEqual(1, found['branches']['lexical'])
        self.assertEqual(0, found['branches']['dense'])
        self.assertEqual(1, len(found['rows']))

    def test_empty_perimeter_short_circuits(self):
        class Exploding:
            def execute(self, *args, **kwargs):
                raise AssertionError('запрос при пустом периметре не нужен')

        found = search_hybrid(Exploding(), article_ids=[], query='аренда')
        self.assertEqual([], found['rows'])
        self.assertTrue(found['degraded'])


if __name__ == '__main__':
    unittest.main()
