# -*- coding: utf-8 -*-
"""Точечная правка клеток таблицы. Чистые тесты: ни базы, ни сети.

Модуль появился из боевого дефекта, и тесты закрепляют именно его. Замер на проде
12.08.2026, статья «Реестр акций»: документ менял срок акции 50п-5к с 14 на 20
дней, но таблицы статьи защищены дословно, и модель, не имея доступа к клетке,
приписала РЯДОМ вторую таблицу. В статье оказались одновременно «14 дней» и
«20 дней» — тихая порча данных вместо обновления.

Поэтому правки теперь описываются, а применяет их программа. Здесь проверяется,
что она применяет ровно описанное и отказывается там, где не уверена.
"""

import unittest

from wiki.ai import tablepatch


TABLE = ('<table><tbody>'
         '<tr><th>Акция</th><th>Срок</th><th>Парки</th></tr>'
         '<tr><td>50п-5к</td><td>14 дней</td><td>Все</td></tr>'
         '<tr><td>Лимонопад</td><td>31.08.2026</td><td>Все, кроме Eki</td></tr>'
         '</tbody></table>')


class SerializeTest(unittest.TestCase):
    def test_rows_and_columns_are_numbered(self):
        text = tablepatch.serialize([TABLE])
        self.assertIn('ТАБЛИЦА 1 (строк 3):', text)
        self.assertIn('С1 [шапка]: К1=Акция | К2=Срок | К3=Парки', text)
        self.assertIn('С2: К1=50п-5к | К2=14 дней', text)

    def test_long_cells_are_trimmed_not_dropped(self):
        wide = '<table><tr><td>%s</td></tr></table>' % ('очень длинно ' * 40)
        text = tablepatch.serialize([wide], max_cell=30)
        self.assertIn('С1', text)
        self.assertLess(len(text), 200)

    def test_row_limit_is_announced(self):
        rows = ''.join('<tr><td>%d</td></tr>' % i for i in range(10))
        text = tablepatch.serialize(['<table>%s</table>' % rows], max_rows=3)
        self.assertIn('ещё 7 строк', text)


class ParseTest(unittest.TestCase):
    def test_three_forms(self):
        patches = tablepatch.parse(
            'ПРАВКИ ТАБЛИЦ:\n'
            '- Т1 С2 К2: 14 дней => 20 дней\n'
            '- Т1 +СТРОКА: Осень | 10 дней | Все\n'
            '- Т1 -СТРОКА 3: нет в документе\n'
            'ИЗМЕНЕНИЯ:\n- прочее\n')
        self.assertEqual(['cell', 'add', 'ask_delete'], [p['kind'] for p in patches])
        self.assertEqual(3, len(patches[1]['values']))

    def test_arrow_variants(self):
        """Стрелку модели пишут по-разному — принимаем все виды."""
        for arrow in ('=>', '->', '-->', '→'):
            patches = tablepatch.parse('ПРАВКИ ТАБЛИЦ:\n- Т1 С2 К2: 14 %s 20\n' % arrow)
            self.assertEqual(1, len(patches), arrow)

    def test_explicit_none(self):
        self.assertEqual([], tablepatch.parse('ПРАВКИ ТАБЛИЦ: нет\nСТАТЬЯ:\n<p>x</p>'))

    def test_no_block_means_no_patches(self):
        self.assertEqual([], tablepatch.parse('ИЗМЕНЕНИЯ:\n- правка\nСТАТЬЯ:\n<p>x</p>'))

    def test_block_ends_at_next_section(self):
        patches = tablepatch.parse(
            'ПРАВКИ ТАБЛИЦ:\n- Т1 С2 К2: 14 => 20\nВОПРОСЫ:\n- Т1 С9 К9: a => b\n')
        self.assertEqual(1, len(patches))


class ApplyTest(unittest.TestCase):
    def test_cell_is_replaced(self):
        tables, changes, questions, rejected = tablepatch.apply(
            [TABLE], [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 2,
                       'was': '14 дней', 'now': '20 дней'}])
        self.assertIn('20 дней', tables[0])
        self.assertNotIn('14 дней', tables[0])
        self.assertEqual(1, len(changes))
        self.assertEqual([], questions + rejected)

    def test_wrong_old_value_is_not_applied(self):
        """Защита от съехавшей нумерации: модель ошибётся в номере, но не в тексте."""
        tables, changes, questions, _rejected = tablepatch.apply(
            [TABLE], [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 2,
                       'was': '30 дней', 'now': '20 дней'}])
        self.assertIn('14 дней', tables[0])
        self.assertNotIn('20 дней', tables[0])
        self.assertEqual([], changes)
        self.assertTrue(any('не применена' in q for q in questions))

    def test_partial_old_value_is_enough(self):
        """Модель обрезает длинные клетки — сверяем по вхождению."""
        long_cell = '<table><tr><td>Выполнить первые 50 поездок в течение 14 дней.</td></tr></table>'
        tables, changes, _q, _r = tablepatch.apply(
            [long_cell], [{'kind': 'cell', 'table': 1, 'row': 1, 'col': 1,
                           'was': 'в течение 14 дней', 'now': 'в течение 20 дней'}])
        self.assertIn('в течение 20 дней', tables[0])
        self.assertEqual(1, len(changes))

    def test_added_row_matches_column_count(self):
        tables, changes, _q, _r = tablepatch.apply(
            [TABLE], [{'kind': 'add', 'table': 1, 'values': ['Осень', '10 дней']}])
        self.assertEqual(4, tables[0].count('<tr'))
        self.assertEqual(3, tables[0].split('<tr>')[-1].count('<td'))
        self.assertEqual(1, len(changes))

    def test_extra_cells_are_trimmed(self):
        tables, _c, _q, _r = tablepatch.apply(
            [TABLE], [{'kind': 'add', 'table': 1,
                       'values': ['a', 'b', 'c', 'd', 'e']}])
        self.assertEqual(3, tables[0].split('<tr>')[-1].count('<td'))

    def test_deletion_only_asks(self):
        """Тихо снесённая строка регламента стоит денег — решает человек."""
        tables, changes, questions, _r = tablepatch.apply(
            [TABLE], [{'kind': 'ask_delete', 'table': 1, 'row': 3,
                       'reason': 'нет в документе'}])
        self.assertIn('Лимонопад', tables[0])
        self.assertEqual([], changes)
        self.assertTrue(any('Лимонопад' in q and 'Удалить' in q for q in questions))

    def test_missing_table_row_or_column_is_rejected(self):
        for patch, hint in (
                ({'kind': 'cell', 'table': 9, 'row': 1, 'col': 1, 'was': 'a', 'now': 'b'},
                 'таблицы'),
                ({'kind': 'cell', 'table': 1, 'row': 99, 'col': 1, 'was': 'a', 'now': 'b'},
                 'строки'),
                ({'kind': 'cell', 'table': 1, 'row': 2, 'col': 9, 'was': 'a', 'now': 'b'},
                 'колонки')):
            _t, changes, _q, rejected = tablepatch.apply([TABLE], [patch])
            self.assertEqual([], changes, hint)
            self.assertTrue(any(hint in r for r in rejected), hint)

    def test_source_tables_are_not_mutated(self):
        original = TABLE
        tablepatch.apply([TABLE], [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 2,
                                    'was': '14 дней', 'now': '20 дней'}])
        self.assertEqual(original, TABLE)

    def test_merged_cells_survive_a_patch(self):
        merged = ('<table><tr><th rowspan="2">Город</th><th colspan="2">Депозит</th></tr>'
                  '<tr><th>пакет</th><th>короб</th></tr>'
                  '<tr><td>Алматы</td><td>5000</td><td>12000</td></tr></table>')
        tables, changes, _q, _r = tablepatch.apply(
            [merged], [{'kind': 'cell', 'table': 1, 'row': 3, 'col': 2,
                        'was': '5000', 'now': '6000'}])
        self.assertIn('rowspan="2"', tables[0])
        self.assertIn('colspan="2"', tables[0])
        self.assertIn('6000', tables[0])
        self.assertEqual(1, len(changes))


if __name__ == '__main__':
    unittest.main()
