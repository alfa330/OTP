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


class DiffExcerptTest(unittest.TestCase):
    def test_shows_where_values_differ(self):
        """Иначе выходит бесполезное «было X → стало X»: различие сидело за обрезкой."""
        before = '1.Участвуют только новые водители и не выполнявшие поездки. Срок 30 дней.'
        after = '1.Участвуют только новые водители и не выполнявшие поездки. Срок 45 дней.'
        was, now = tablepatch._diff_excerpt(before, after)
        self.assertNotEqual(was, now)
        self.assertIn('30 дней', was)
        self.assertIn('45 дней', now)

    def test_short_values_are_shown_as_is(self):
        was, now = tablepatch._diff_excerpt('14 дней', '20 дней')
        self.assertEqual(('14 дней', '20 дней'), (was, now))


class LinkSurvivalTest(unittest.TestCase):
    """Ссылка в клетке обязана пережить правку.

    Дефект найден владельцем: «ИИ при редактировании таблицы некоторые ссылки
    теряла». Причина была прямая — замена значения стирала содержимое клетки
    целиком, вместе с тегом <a>, а модель адреса вообще не видела: в разметке для
    неё клетка выглядела просто текстом «ДОБАВИТЬ ВОДИТЕЛЯ».

    В таблицах вики ссылка это половина смысла: формы Google на каждый парк,
    «ССЫЛКА НА ФОРМУ ДЛЯ ПОПОЛНЕНИЯ», проверка пополнений. Клетка без адреса
    бесполезна, поэтому здесь три проверки на три пути сохранения.
    """

    LINKED = ('<table><tbody>'
              '<tr><th>Парк</th><th>Ссылка на добавление</th></tr>'
              '<tr><td>Честный</td><td><p><a target="_blank" '
              'href="https://docs.google.com/forms/d/e/1FAI/formResponse" '
              'rel="noopener noreferrer">ДОБАВИТЬ ВОДИТЕЛЯ</a></p></td></tr>'
              '</tbody></table>')

    def test_model_sees_the_address(self):
        """Не видя адреса, модель возвращает вместо ссылки простой текст."""
        text = tablepatch.serialize([self.LINKED])
        self.assertIn('ДОБАВИТЬ ВОДИТЕЛЯ (https://docs.google.com/forms/d/e/1FAI/formResponse)',
                      text)

    def test_link_survives_edit_of_a_neighbour_cell(self):
        tables, _c, questions, _r = tablepatch.apply(
            [self.LINKED], [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 1,
                             'was': 'Честный', 'now': 'Честный (Адал)'}])
        self.assertIn('docs.google.com', tables[0])
        self.assertEqual([], questions)

    def test_link_returns_when_model_kept_the_form(self):
        """Модель вернула «ярлык (адрес)» — ссылка собирается обратно с атрибутами."""
        tables, _c, questions, _r = tablepatch.apply(
            [self.LINKED],
            [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 2,
              'was': 'ДОБАВИТЬ ВОДИТЕЛЯ',
              'now': 'ДОБАВИТЬ ВОДИТЕЛЯ ОБНОВЛЁННУЮ '
                     '(https://docs.google.com/forms/d/e/1FAI/formResponse)'}])
        self.assertIn('href="https://docs.google.com/forms/d/e/1FAI/formResponse"', tables[0])
        self.assertIn('target="_blank"', tables[0])
        self.assertIn('ОБНОВЛЁННУЮ', tables[0])
        self.assertEqual([], questions)

    def test_link_returns_by_label_when_address_omitted(self):
        """Модель написала только ярлык — ссылку узнаём по нему."""
        tables, _c, questions, _r = tablepatch.apply(
            [self.LINKED], [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 2,
                             'was': 'ДОБАВИТЬ ВОДИТЕЛЯ',
                             'now': 'ДОБАВИТЬ ВОДИТЕЛЯ — только для новых'}])
        self.assertIn('docs.google.com', tables[0])
        self.assertIn('<a href=', tables[0])
        self.assertEqual([], questions)

    def test_lost_link_is_appended_and_asked_about(self):
        """Ярлык переписан целиком — адрес НЕ теряется, но об этом спрашивают."""
        tables, _c, questions, _r = tablepatch.apply(
            [self.LINKED], [{'kind': 'cell', 'table': 1, 'row': 2, 'col': 2,
                             'was': 'ДОБАВИТЬ ВОДИТЕЛЯ',
                             'now': 'Форма регистрации водителя'}])
        self.assertIn('docs.google.com', tables[0])
        self.assertTrue(any('была ссылка' in q for q in questions))

    def test_new_row_url_becomes_a_link(self):
        """Адрес в новой строке должен быть щёлкаемым, а не текстом."""
        tables, _c, _q, _r = tablepatch.apply(
            [self.LINKED], [{'kind': 'add', 'table': 1,
                             'values': ['Осень', 'https://forms.gle/abc123']}])
        self.assertIn('<a href="https://forms.gle/abc123"', tables[0])

    def test_mailto_survives(self):
        table = ('<table><tr><td>Поддержка</td>'
                 '<td><a href="mailto:help@itaxi.kz">help@itaxi.kz</a></td></tr></table>')
        tables, _c, _q, _r = tablepatch.apply(
            [table], [{'kind': 'cell', 'table': 1, 'row': 1, 'col': 2,
                       'was': 'help@itaxi.kz',
                       'now': 'help@itaxi.kz (mailto:help@itaxi.kz)'}])
        self.assertIn('mailto:help@itaxi.kz', tables[0])

    def test_two_links_in_one_cell_both_survive(self):
        table = ('<table><tr><td>'
                 '<a href="https://a.example/add">Добавить</a> / '
                 '<a href="https://b.example/check">Проверить</a></td></tr></table>')
        tables, _c, _q, _r = tablepatch.apply(
            [table], [{'kind': 'cell', 'table': 1, 'row': 1, 'col': 1,
                       'was': 'Добавить / Проверить',
                       'now': 'Добавить / Проверить (обновлено)'}])
        self.assertIn('https://a.example/add', tables[0])
        self.assertIn('https://b.example/check', tables[0])


if __name__ == '__main__':
    unittest.main()
