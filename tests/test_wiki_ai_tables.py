# -*- coding: utf-8 -*-
"""Помощник вики и таблицы: делать, заполнять и не ломать то, что настроил человек.

Чистые тесты: ни базы, ни сети, модель — заглушка с готовым ответом.

Повод — просьба владельца 27.11.2026 «обучи нашу ИИ, чтобы она могла делать
такие таблицы и заполнять их» сразу после того, как в редакторе появились
ширины колонок, высота строк и положение таблицы. Проверка показала, что до
этой правки помощник:
  * СТИРАЛ всё это при любой правке статьи — normalize_table снимала с таблицы
    все атрибуты, а заодно выделение цветом и выравнивание внутри клеток;
  * не мог заполнить пустую клетку — старое значение обязательно, а «пусто»
    записать было нечем;
  * при обрезанном старом значении заменял клетку этим обрезком, и хвост
    длинной клетки пропадал;
  * не мог ни вставить строку в середину, ни добавить колонку, ни поставить
    таблицу по центру, ни убрать её по прямой просьбе.
"""

import re
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from wiki.ai import authoring, markup, revise, tablepatch
from wiki.sanitize import sanitize_html

ROOT = Path(__file__).resolve().parents[1]

# Так таблицу сохраняет редактор (getHTML TipTap + узлы WikiTable/WikiTableRow):
# зафиксированные ширины, положение по центру, высота строки, выравнивание и
# выделение цветом внутри клеток.
LAID_OUT = (
    '<table class="wiki-table-fixed wiki-table-center" style="width: 360px">'
    '<colgroup><col style="width: 120px"><col style="width: 140px">'
    '<col style="width: 100px"></colgroup><tbody>'
    '<tr><th colspan="1" rowspan="1" colwidth="120"><p>Город</p></th>'
    '<th colspan="1" rowspan="1" colwidth="140"><p>Комиссия, %</p></th>'
    '<th colspan="1" rowspan="1" colwidth="100"><p>Тариф</p></th></tr>'
    '<tr style="height: 70px"><td colspan="1" rowspan="1" colwidth="120"><p>Алматы</p></td>'
    '<td colspan="1" rowspan="1" colwidth="140"><p style="text-align: center">'
    '<strong>3</strong></p></td>'
    '<td colspan="1" rowspan="1" colwidth="100"><p><mark data-color="#fde68a" '
    'style="background-color: #fde68a; color: inherit">Эконом</mark></p></td></tr>'
    '<tr><td colspan="1" rowspan="1" colwidth="120"><p>Астана</p></td>'
    '<td colspan="1" rowspan="1" colwidth="140"><p></p></td>'
    '<td colspan="1" rowspan="1" colwidth="100"><p></p></td></tr>'
    '</tbody></table>'
)

ARTICLE = '<h1>Тарифы</h1><p>Комиссии по городам.</p>' + LAID_OUT + '<p>Конец.</p>'


def stub(answer, seen=None):
    def generate_fn(system, user, **kwargs):
        if seen is not None:
            seen['system'] = system
            seen['user'] = user
        return answer, {'provider': 'test', 'model': 'stub', 'finish': 'STOP'}
    return generate_fn


def reply(patches='нет', body='<h1>Тарифы</h1><p>Комиссии по городам.</p>'
                              '<p>[[ТАБЛИЦА-1]]</p><p>Конец.</p>',
          changes='- правка'):
    return 'ПРАВКИ ТАБЛИЦ:\n%s\nИЗМЕНЕНИЯ:\n%s\nВОПРОСЫ:\nнет\nСТАТЬЯ:\n%s' % (
        patches, changes, body)


def table_of(html, index=0):
    return BeautifulSoup(html, 'html.parser').find_all('table')[index]


class LayoutSurvivesTest(unittest.TestCase):
    """Правка через помощника возвращает таблицу такой, какой её настроил человек."""

    def edit(self, answer, instruction='сократи вступление'):
        return revise.edit_by_instruction(
            current_title='Тарифы', current_html=ARTICLE, instruction=instruction,
            generate_fn=stub(answer))

    def test_prose_edit_keeps_widths_heights_and_position(self):
        result = self.edit(reply(body='<h1>Тарифы</h1><p>Комиссии.</p>'
                                      '<p>[[ТАБЛИЦА-1]]</p><p>Конец.</p>'))
        table = table_of(result['content'])
        self.assertEqual(['wiki-table-fixed', 'wiki-table-center'], table.get('class'))
        self.assertIn('width: 360px', table.get('style'))
        self.assertEqual(3, len(table.find_all('col')))
        self.assertIn('width: 120px', table.find('col').get('style'))
        self.assertEqual('120', table.find('th').get('colwidth'))
        self.assertIn('height: 70px', table.find_all('tr')[1].get('style'))

    def test_formatting_inside_cells_survives(self):
        """Раньше normalize_table снимала атрибуты и со всего, что внутри клеток."""
        result = self.edit(reply())
        self.assertIn('text-align: center', result['content'])
        self.assertIn('data-color="#fde68a"', result['content'])
        self.assertIn('<strong>3</strong>', result['content'])

    def test_header_is_not_forced_on_an_article_table(self):
        """Шапку человек мог выключить сам — правка текста её не возвращает."""
        plain = '<p>Т</p><table><tbody><tr><td>Город</td><td>Тариф</td></tr>' \
                '<tr><td>Алматы</td><td>Эконом</td></tr></tbody></table>'
        result = revise.edit_by_instruction(
            current_title='Т', current_html=plain, instruction='поправь опечатки',
            generate_fn=stub(reply(body='<p>Т</p><p>[[ТАБЛИЦА-1]]</p>')))
        self.assertNotIn('<th', result['content'])

    def test_foreign_table_markup_is_dropped_ours_is_kept(self):
        dirty = ('<table class="MsoTable wiki-table-right" style="border: 1px solid; '
                 'width: 300px" bgcolor="red"><tr style="height: 40px; color: red">'
                 '<td colwidth="150" style="font-size: 8pt" width="99">А</td></tr></table>')
        _body, tables, _images = authoring.protect_tables(dirty, keep_layout=True)
        table = table_of(tables[0])
        self.assertEqual(['wiki-table-right'], table.get('class'))
        self.assertEqual('width: 300px', table.get('style'))
        self.assertNotIn('bgcolor', table.attrs)
        self.assertEqual('height: 40px', table.find('tr').get('style'))
        self.assertEqual({'colwidth': '150'}, table.find('td').attrs)

    def test_document_tables_are_still_unified(self):
        """Таблица из Word — не настройка автора: её ширины по-прежнему уходят."""
        _body, tables, _images = authoring.protect_tables(LAID_OUT)
        self.assertNotIn('colwidth', tables[0])
        self.assertNotIn('height', tables[0])
        self.assertNotIn('wiki-table-fixed', tables[0])
        # А положение — решение автора, не Word: остаётся.
        self.assertIn('wiki-table-center', tables[0])

    def test_nested_table_travels_inside_its_parent(self):
        """Раньше вложенная таблица получала свой маркер и уезжала в конец дублем."""
        nested = ('<table><tr><td>Внешняя<table><tr><td>Внутри</td></tr></table>'
                  '</td></tr></table>')
        body, tables, _images = authoring.protect_tables(nested, keep_layout=True)
        self.assertEqual(1, len(tables))
        self.assertEqual(1, body.count('[[ТАБЛИЦА-'))
        restored, lost = authoring.restore_tables(body, tables)
        self.assertEqual([], lost)
        self.assertEqual(1, restored.count('Внутри'))

    def test_the_sanitizer_keeps_all_of_it(self):
        clean = sanitize_html(LAID_OUT)
        for needle in ('wiki-table-fixed', 'wiki-table-center', 'width: 360px',
                       'colwidth="140"', 'height: 70px', 'style="width: 120px"'):
            self.assertIn(needle, clean)


class MarkerTailTest(unittest.TestCase):
    def setUp(self):
        _body, self.tables, _images = authoring.protect_tables(ARTICLE, keep_layout=True)

    def restore(self, marker):
        return authoring.restore_tables('<p>%s</p>' % marker, self.tables)

    def test_bare_marker_keeps_everything(self):
        out, lost = self.restore('[[ТАБЛИЦА-1]]')
        self.assertEqual([], lost)
        self.assertIn('wiki-table-center', out)
        self.assertIn('wiki-table-fixed', out)

    def test_tail_moves_the_table(self):
        out, _lost = self.restore('[[ТАБЛИЦА-1 справа]]')
        self.assertIn('wiki-table-right', out)
        self.assertNotIn('wiki-table-center', out)
        # Положение — не повод терять зафиксированные ширины.
        self.assertIn('wiki-table-fixed', out)
        out, _lost = self.restore('[[ТАБЛИЦА-1 слева]]')
        self.assertNotIn('wiki-table-center', out)
        self.assertNotIn('wiki-table-right', out)

    def test_marker_with_tail_is_found_not_left_as_text(self):
        """Без группы хвоста маркер не находился и оставался в статье текстом."""
        out, lost = self.restore('[[ТАБЛИЦА-1 по центру]]')
        self.assertNotIn('[[', out)
        self.assertEqual([], lost)

    def test_remove_is_explicit_and_reported(self):
        out, lost = self.restore('[[ТАБЛИЦА-1 убрать]]')
        self.assertNotIn('<table', out)
        self.assertEqual([], lost)
        self.assertEqual([1], authoring.removed_tables('<p>[[ТАБЛИЦА-1 убрать]]</p>'))
        result = revise.edit_by_instruction(
            current_title='Тарифы', current_html=ARTICLE, instruction='убери таблицу',
            generate_fn=stub(reply(body='<h1>Тарифы</h1><p>Комиссии по городам.</p>'
                                        '<p>[[ТАБЛИЦА-1 убрать]]</p><p>Конец.</p>')))
        self.assertNotIn('<table', result['content'])
        self.assertTrue(any('убрал таблицу' in w for w in result['warnings']))
        # Одно предупреждение про одну таблицу, а не два.
        self.assertFalse(any('Таблиц в документе' in w for w in result['warnings']))

    def test_dropped_marker_still_returns_the_table(self):
        out, lost = authoring.restore_tables('<p>Текст</p>', self.tables)
        self.assertEqual([1], lost)
        self.assertIn('<table', out)

    def test_model_is_told_the_position_and_the_tail(self):
        seen = {}
        revise.edit_by_instruction(current_title='Тарифы', current_html=ARTICLE,
                                   instruction='поставь таблицу справа',
                                   generate_fn=stub(reply(), seen))
        self.assertIn('ГОТОВЫЕ ТАБЛИЦЫ', seen['user'])
        self.assertIn('[[ТАБЛИЦА-1 по центру]]', seen['user'])
        self.assertIn('Тариф; по центру, ширины колонок выставлены человеком', seen['user'])
        self.assertIn('высота строк выставлена человеком', seen['user'])


class NewTableTest(unittest.TestCase):
    """Таблица, которую модель пишет сама."""

    def test_data_align_becomes_the_editor_class(self):
        out = authoring.canonicalize(
            '<p>Тарифы:</p><table data-align="center" class="x" style="width:9px">'
            '<thead><tr><th>Город</th><th>Тариф</th></tr></thead>'
            '<tbody><tr><td>Алматы</td><td>Эконом</td></tr></tbody></table>')
        table = table_of(out)
        self.assertEqual(['wiki-table-center'], table.get('class'))
        self.assertNotIn('data-align', table.attrs)
        self.assertNotIn('style', table.attrs)

    def test_ragged_rows_are_squared(self):
        out = authoring.canonicalize(
            '<table><tr><th>Город</th><th>Комиссия</th><th>Тариф</th></tr>'
            '<tr><td>Алматы</td><td>3</td></tr>'
            '<tr><td colspan="2">Астана</td></tr></table>')
        rows = table_of(out).find_all('tr')
        self.assertEqual(3, len(rows[1].find_all('td')))
        self.assertEqual(2, len(rows[2].find_all('td')))

    def test_merged_cells_are_not_padded_away(self):
        out = authoring.canonicalize(
            '<table><tr><th rowspan="2">Город</th><th colspan="2">Депозит</th></tr>'
            '<tr><th>пакет</th><th>короб</th></tr>'
            '<tr><td>Алматы</td><td>5000</td><td>12000</td></tr></table>')
        rows = table_of(out).find_all('tr')
        self.assertEqual([2, 2, 3], [len(r.find_all(('td', 'th'))) for r in rows])

    def test_caption_becomes_a_lead_in(self):
        """Подписи узел редактора не знает: её текст встал бы лишней строкой."""
        out = authoring.canonicalize(
            '<table><caption>Тарифы по городам</caption>'
            '<tr><th>Город</th><th>Тариф</th></tr><tr><td>Алматы</td><td>Эконом</td></tr>'
            '</table>')
        self.assertNotIn('<caption', out)
        self.assertTrue(out.startswith('<p><strong>Тарифы по городам</strong></p><table'))

    def test_edit_can_create_a_centered_table(self):
        result = revise.edit_by_instruction(
            current_title='Тарифы', current_html='<h1>Тарифы</h1><p>В Алматы 3 %, '
                                                 'в Астане 4 %.</p>',
            instruction='оформи комиссии таблицей по центру',
            generate_fn=stub(reply(body=(
                '<h1>Тарифы</h1><p>Комиссии по городам:</p>'
                '<table data-align="center"><thead><tr><th>Город</th>'
                '<th>Комиссия, %</th></tr></thead><tbody><tr><td>Алматы</td><td>3</td></tr>'
                '<tr><td>Астана</td><td>4</td></tr></tbody></table>'))))
        table = table_of(result['content'])
        self.assertEqual(['wiki-table-center'], table.get('class'))
        self.assertEqual(2, len(table.find_all('th')))

    def test_every_prompt_teaches_tables(self):
        for prompt in (authoring.SYSTEM_PROMPT, revise.UPDATE_PROMPT, revise.EDIT_PROMPT):
            self.assertIn('ТАБЛИЦЫ', prompt)
            self.assertIn('data-align="center"', prompt)
            self.assertIn('<td></td>', prompt)
        for prompt in (revise.UPDATE_PROMPT, revise.EDIT_PROMPT):
            self.assertIn('Т1 С4 К2: пусто => 5 %', prompt)
            self.assertIn('Т1 +КОЛОНКА', prompt)
            self.assertIn('+СТРОКА после С3', prompt)


class ParseTest(unittest.TestCase):
    def parse(self, *lines):
        return tablepatch.parse('ПРАВКИ ТАБЛИЦ:\n%s\nСТАТЬЯ:\n<p>x</p>' % '\n'.join(lines))

    def test_new_forms(self):
        patches = self.parse('- Т1 С4: Алматы | 5 % | Эконом',
                             '- Т1 +СТРОКА после С3: Караганда | 4 %',
                             '- Т1 +КОЛОНКА: Телефон | 1 | 2',
                             '- Т1 +КОЛОНКА после К1: Офис | А',
                             '- Т1 С2 К2: пусто => 5 %')
        self.assertEqual(['row', 'add', 'col', 'col', 'cell'], [p['kind'] for p in patches])
        self.assertEqual(3, patches[1]['after'])
        self.assertNotIn('after', patches[2])
        self.assertEqual(1, patches[3]['after'])
        self.assertEqual('пусто', patches[4]['was'])

    def test_latin_letters_are_understood(self):
        """Модели иногда пишут латинские T, C, K: правка не должна теряться."""
        patches = self.parse('T1 C2 K2: 14 => 20', 'T1 +СТРОКА: a | b')
        self.assertEqual(['cell', 'add'], [p['kind'] for p in patches])

    def test_unparsed_patch_is_not_lost_silently(self):
        patches = self.parse('Т1 С2 К: 14 => 20')
        self.assertEqual('bad', patches[0]['kind'])
        _t, _c, questions, _r = tablepatch.apply([LAID_OUT], patches)
        self.assertTrue(any('не удалось разобрать' in q for q in questions))

    def test_commentary_is_not_a_patch(self):
        self.assertEqual([], self.parse('Т1 — правок нет'))

    def test_row_form_with_an_arrow_is_not_written_into_cells(self):
        self.assertEqual('bad', self.parse('Т1 С2: Алматы => Астана')[0]['kind'])

    def test_how_models_actually_write_it(self):
        """Стрелки и «после строки 3» по-русски — правка не должна теряться."""
        patches = self.parse('Т1 С2 К2: 14 ==> 20', 'Т1 С2 К2: 14 ⇒ 20',
                             'Т1 +СТРОКА после строки 3: а | б',
                             'Т1 +КОЛОНКА после колонки 2: Офис | А')
        self.assertEqual(['cell', 'cell', 'add', 'col'], [p['kind'] for p in patches])
        self.assertEqual(('14', '20'), (patches[0]['was'], patches[0]['now']))
        self.assertEqual(3, patches[2]['after'])
        self.assertEqual(2, patches[3]['after'])

    def test_span_note_from_the_hint_never_reaches_a_cell(self):
        patches = self.parse('Т1 С1 К2: Депозит [на 2 колонки] => Залог [на 2 колонки]',
                             'Т1 С3: Юг [на 2 строки] | Тараз')
        self.assertEqual(('Депозит', 'Залог'), (patches[0]['was'], patches[0]['now']))
        self.assertEqual(['Юг', 'Тараз'], patches[1]['values'])

    def test_clearing_a_cell(self):
        patches = self.parse('Т1 С2 К2: 3 => пусто')
        self.assertEqual('', patches[0]['now'])
        tables, changes, _q, _r = tablepatch.apply([LAID_OUT], patches)
        cell = table_of(tables[0]).find_all('tr')[1].find_all('td')[1]
        self.assertEqual('', cell.get_text())
        # Абзац с выравниванием остаётся — клетка пустая, а не разобранная.
        self.assertIsNotNone(cell.find('p', style=re.compile('text-align')))
        self.assertEqual(['таблица 1, строка 2: очищено «3»'], changes)


class FillTest(unittest.TestCase):
    def apply(self, *patches, table=LAID_OUT):
        return tablepatch.apply([table], list(patches))

    def test_empty_cell_is_filled_by_the_word_empty(self):
        tables, changes, questions, _r = self.apply(
            {'kind': 'cell', 'table': 1, 'row': 3, 'col': 2, 'was': 'пусто', 'now': '4'})
        cells = table_of(tables[0]).find_all('tr')[2].find_all('td')
        self.assertEqual('4', cells[1].get_text())
        # Абзац клетки и её ширина на месте — так её хранит редактор.
        self.assertEqual('140', cells[1].get('colwidth'))
        self.assertIsNotNone(cells[1].find('p'))
        self.assertEqual(['таблица 1, строка 3: заполнено «4»'], changes)
        self.assertEqual([], questions)

    def test_empty_is_not_accepted_for_a_filled_cell(self):
        _t, changes, questions, _r = self.apply(
            {'kind': 'cell', 'table': 1, 'row': 2, 'col': 1, 'was': 'пусто', 'now': 'Шымкент'})
        self.assertEqual([], changes)
        self.assertTrue(any('Алматы' in q for q in questions))

    def test_a_value_is_not_accepted_for_an_empty_cell(self):
        """Раньше пустая клетка сходилась с любым старым значением — защита от
        съехавшей нумерации там не работала."""
        _t, changes, questions, _r = self.apply(
            {'kind': 'cell', 'table': 1, 'row': 3, 'col': 2, 'was': '3', 'now': '4'})
        self.assertEqual([], changes)
        self.assertTrue(questions)

    def test_a_dash_for_an_empty_cell_is_fine(self):
        _t, changes, _q, _r = self.apply(
            {'kind': 'cell', 'table': 1, 'row': 3, 'col': 3, 'was': '—', 'now': 'Комфорт'})
        self.assertEqual(1, len(changes))

    def test_row_fill_writes_only_into_empty_cells(self):
        tables, changes, questions, _r = self.apply(
            {'kind': 'row', 'table': 1, 'row': 3, 'values': ['Астана', '4', 'Комфорт']})
        cells = [c.get_text() for c in table_of(tables[0]).find_all('tr')[2].find_all('td')]
        self.assertEqual(['Астана', '4', 'Комфорт'], cells)
        self.assertEqual(['таблица 1, строка 3: заполнено «4», «Комфорт»'], changes)
        self.assertEqual([], questions)

    def test_row_fill_does_not_overwrite(self):
        tables, _c, questions, _r = self.apply(
            {'kind': 'row', 'table': 1, 'row': 2, 'values': ['Шымкент', '', '']})
        self.assertIn('Алматы', tables[0])
        self.assertTrue(any('там уже «Алматы»' in q for q in questions))

    def test_row_fill_extra_values_are_asked_about(self):
        _t, _c, questions, _r = self.apply(
            {'kind': 'row', 'table': 1, 'row': 3, 'values': ['', '4', 'Комфорт', 'лишнее']})
        self.assertTrue(any('лишнее' in q for q in questions))

    def test_image_only_cell_is_not_empty(self):
        table = ('<table><tr><th>Значок</th></tr>'
                 '<tr><td><img src="/api/wiki/file/x"></td></tr></table>')
        self.assertIn('(картинка)', tablepatch.serialize([table]))
        tables, changes, questions, _r = self.apply(
            {'kind': 'row', 'table': 1, 'row': 2, 'values': ['текст']}, table=table)
        self.assertIn('<img', tables[0])
        self.assertEqual([], changes)
        self.assertTrue(questions)
        tables, changes, questions, _r = self.apply(
            {'kind': 'cell', 'table': 1, 'row': 2, 'col': 1, 'was': 'пусто', 'now': 'т'},
            table=table)
        self.assertIn('<img', tables[0])
        self.assertEqual([], changes)

    def test_serialize_shows_empty_cells_and_spans(self):
        text = tablepatch.serialize([LAID_OUT])
        self.assertIn('С3: К1=Астана | К2=(пусто) | К3=(пусто)', text)
        merged = ('<table><tr><th rowspan="2">Город</th><th colspan="2">Депозит</th></tr>'
                  '<tr><th>пакет</th><th>короб</th></tr></table>')
        text = tablepatch.serialize([merged])
        self.assertIn('К1=Город [на 2 строки]', text)
        self.assertIn('К2=Депозит [на 2 колонки]', text)


class RowInsertTest(unittest.TestCase):
    MERGED = ('<table><tbody><tr><th>Зона</th><th>Город</th><th>Тариф</th></tr>'
              '<tr><td rowspan="2">Юг</td><td>Алматы</td><td>Эконом</td></tr>'
              '<tr><td>Шымкент</td><td>Эконом</td></tr>'
              '<tr><td>Север</td><td>Астана</td><td>Комфорт</td></tr></tbody></table>')

    def test_insert_after_a_row(self):
        tables, changes, _q, _r = tablepatch.apply(
            [LAID_OUT], [{'kind': 'add', 'table': 1, 'after': 2,
                          'values': ['Караганда', '4', 'Эконом']}])
        rows = table_of(tables[0]).find_all('tr')
        self.assertEqual(4, len(rows))
        self.assertEqual('Караганда', rows[2].find('td').get_text())
        # Ширины колонок новой строке — как у соседей.
        self.assertEqual(['120', '140', '100'], [c.get('colwidth') for c in rows[2].find_all('td')])
        self.assertEqual(['таблица 1: после строки 2 вставлена строка «Караганда»'], changes)

    def test_insert_inside_a_merged_block_extends_it(self):
        tables, _c, _q, _r = tablepatch.apply(
            [self.MERGED], [{'kind': 'add', 'table': 1, 'after': 2,
                             'values': ['Тараз', 'Эконом']}])
        table = table_of(tables[0])
        self.assertEqual('3', table.find('td', string='Юг').get('rowspan'))
        new_row = table.find_all('tr')[2]
        self.assertEqual(['Тараз', 'Эконом'], [c.get_text() for c in new_row.find_all('td')])

    def test_several_inserts_keep_their_order_and_original_numbers(self):
        tables, _c, _q, _r = tablepatch.apply(
            [self.MERGED], [
                {'kind': 'add', 'table': 1, 'after': 4, 'values': ['Север', 'Кокшетау', 'Эконом']},
                {'kind': 'add', 'table': 1, 'after': 1, 'values': ['Запад', 'Актау', 'Эконом']},
                {'kind': 'add', 'table': 1, 'after': 1, 'values': ['Запад', 'Атырау', 'Эконом']},
                {'kind': 'add', 'table': 1, 'values': ['Центр', 'Караганда', 'Эконом']},
            ])
        cities = [row.find_all('td')[1].get_text() if len(row.find_all('td')) == 3
                  else row.find_all('td')[0].get_text()
                  for row in table_of(tables[0]).find_all('tr')[1:]]
        self.assertEqual(['Актау', 'Атырау', 'Алматы', 'Шымкент', 'Астана', 'Кокшетау',
                          'Караганда'], cities)

    def test_cell_patch_uses_numbers_from_before_the_insert(self):
        tables, _c, questions, _r = tablepatch.apply(
            [self.MERGED], [
                {'kind': 'add', 'table': 1, 'after': 1, 'values': ['Запад', 'Актау', 'Эконом']},
                {'kind': 'cell', 'table': 1, 'row': 4, 'col': 3, 'was': 'Комфорт',
                 'now': 'Бизнес'},
            ])
        self.assertEqual([], questions)
        self.assertIn('Бизнес', tables[0])


class ColumnTest(unittest.TestCase):
    def test_column_on_the_right(self):
        tables, changes, questions, _r = tablepatch.apply(
            [LAID_OUT], [{'kind': 'col', 'table': 1,
                          'values': ['Телефон', '+7 700 000 00 01', '+7 700 000 00 02']}])
        table = table_of(tables[0])
        rows = table.find_all('tr')
        self.assertEqual('th', rows[0].find_all(('td', 'th'))[-1].name)
        self.assertEqual(['Телефон', '+7 700 000 00 01', '+7 700 000 00 02'],
                         [row.find_all(('td', 'th'))[-1].get_text() for row in rows])
        self.assertEqual(['таблица 1: добавлена колонка «Телефон»'], changes)
        self.assertEqual([], questions)

    def test_fixed_table_gets_a_width_for_the_new_column(self):
        """Витрина рисует сохранённое: без своей <col> колонка получила бы остаток."""
        tables, _c, _q, _r = tablepatch.apply(
            [LAID_OUT], [{'kind': 'col', 'table': 1, 'values': ['Телефон', '1', '2']}])
        table = table_of(tables[0])
        self.assertEqual(4, len(table.find_all('col')))
        self.assertEqual('width: 120px', table.find_all('col')[-1].get('style'))
        self.assertEqual('width: 480px', table.get('style'))
        self.assertEqual('120', table.find_all('tr')[1].find_all('td')[-1].get('colwidth'))

    def test_column_after_a_merged_header_cell(self):
        merged = ('<table><tr><th rowspan="2">Город</th><th colspan="2">Депозит</th></tr>'
                  '<tr><th>пакет</th><th>короб</th></tr>'
                  '<tr><td>Алматы</td><td>5000</td><td>12000</td></tr></table>')
        tables, _c, questions, _r = tablepatch.apply(
            [merged], [{'kind': 'col', 'table': 1, 'after': 1,
                        'values': ['Офис', '', 'ул. Абая 1']}])
        rows = table_of(tables[0]).find_all('tr')
        self.assertEqual(['Город', 'Офис', 'Депозит'],
                         [c.get_text() for c in rows[0].find_all('th')])
        # Строка 2: «Город» тянется сверху, новая клетка встаёт в его ряд — первой.
        self.assertEqual(['', 'пакет', 'короб'], [c.get_text() for c in rows[1].find_all('th')])
        self.assertEqual(['Алматы', 'ул. Абая 1', '5000', '12000'],
                         [c.get_text() for c in rows[2].find_all('td')])
        self.assertEqual([], questions)

    def test_column_through_a_merged_cell_widens_it(self):
        """Объединённую клетку, через которую идёт новая колонка, не режут, а расширяют."""
        table = ('<table><tr><th>Город</th><th>пакет</th><th>короб</th></tr>'
                 '<tr><td>Алматы</td><td colspan="2">по запросу</td></tr>'
                 '<tr><td>Астана</td><td>5000</td><td>12000</td></tr></table>')
        tables, _c, questions, _r = tablepatch.apply(
            [table], [{'kind': 'col', 'table': 1, 'after': 2,
                       'values': ['Пломба', 'есть', '300']}])
        rows = table_of(tables[0]).find_all('tr')
        self.assertEqual(['Город', 'пакет', 'Пломба', 'короб'],
                         [c.get_text() for c in rows[0].find_all('th')])
        self.assertEqual('3', rows[1].find_all('td')[1].get('colspan'))
        self.assertEqual(2, len(rows[1].find_all('td')))
        self.assertEqual(['Астана', '5000', '300', '12000'],
                         [c.get_text() for c in rows[2].find_all('td')])
        # Значение для объединённой клетки не потеряно молча — о нём спрашивают.
        self.assertTrue(any('«есть»' in q for q in questions))
        # Заголовок раздела на всю ширину при колонке справа тоже расширяется.
        section = ('<table><tr><th colspan="2">Раздел</th></tr>'
                   '<tr><td>а</td><td>б</td></tr></table>')
        tables, _c, questions, _r = tablepatch.apply(
            [section], [{'kind': 'col', 'table': 1, 'values': ['', 'в']}])
        rows = table_of(tables[0]).find_all('tr')
        self.assertEqual('3', rows[0].find('th').get('colspan'))
        self.assertEqual(['а', 'б', 'в'], [c.get_text() for c in rows[1].find_all('td')])
        self.assertEqual([], questions)

    def test_two_columns_keep_the_order_they_were_given(self):
        tables, _c, _q, _r = tablepatch.apply(
            [LAID_OUT], [{'kind': 'col', 'table': 1, 'values': ['А']},
                         {'kind': 'col', 'table': 1, 'values': ['Б']},
                         {'kind': 'col', 'table': 1, 'after': 1, 'values': ['В']}])
        head = [c.get_text() for c in table_of(tables[0]).find_all('tr')[0].find_all('th')]
        self.assertEqual(['Город', 'В', 'Комиссия, %', 'Тариф', 'А', 'Б'], head)

    def test_header_cell_out_of_range_is_rejected(self):
        _t, changes, _q, rejected = tablepatch.apply(
            [LAID_OUT], [{'kind': 'col', 'table': 1, 'after': 9, 'values': ['А']}])
        self.assertEqual([], changes)
        self.assertTrue(rejected)


class FormattingSurvivesTest(unittest.TestCase):
    """Правка значения не снимает с клетки её оформление."""

    def patch(self, cell_html, was, now):
        table = '<table><tr><td>%s</td></tr></table>' % cell_html
        tables, changes, questions, _r = tablepatch.apply(
            [table], [{'kind': 'cell', 'table': 1, 'row': 1, 'col': 1, 'was': was, 'now': now}])
        return table_of(tables[0]).find('td'), changes, questions

    def test_bold_and_alignment_stay(self):
        cell, changes, _q = self.patch(
            '<p style="text-align: center"><strong>14 дней</strong></p>', '14 дней', '20 дней')
        self.assertEqual('<td><p style="text-align: center"><strong>20 дней</strong></p></td>',
                         str(cell))
        self.assertEqual(['таблица 1, строка 1: «14 дней» → «20 дней»'], changes)

    def test_part_formatting_stays_around_the_change(self):
        cell, _c, _q = self.patch('<p><strong>Срок:</strong> 14 дней</p>', '14 дней', '20 дней')
        self.assertEqual('<p><strong>Срок:</strong> 20 дней</p>', ''.join(map(str, cell.contents)))

    def test_whole_wrapper_returns_when_the_value_is_rewritten(self):
        cell, _c, _q = self.patch(
            '<p><mark data-color="#fde68a">Эконом</mark></p>', 'Эконом', 'Комфорт плюс')
        self.assertIn('<mark data-color="#fde68a">Комфорт плюс</mark>', str(cell))

    def test_model_can_set_bold_itself(self):
        cell, _c, _q = self.patch('<p><em>Итого</em></p>', 'Итого', '<strong>Итого</strong>')
        self.assertEqual('<td><p><strong>Итого</strong></p></td>', str(cell))

    def test_trimmed_old_value_keeps_the_rest_of_the_cell(self):
        """Раньше клетка заменялась обрезком, и всё, что стояло дальше, пропадало."""
        long_text = ('Выполнить 50 поездок в течение 14 дней. Бонус начисляется в '
                     'понедельник, если водитель не нарушал правила парка.')
        cell, _c, _q = self.patch('<p>%s</p>' % long_text, 'в течение 14 дней',
                                  'в течение 20 дней')
        self.assertEqual(long_text.replace('14', '20'), cell.get_text())

    def test_case_only_fix_is_a_change(self):
        cell, changes, _q = self.patch('<p><strong>алматы</strong></p>', 'алматы', 'Алматы')
        self.assertEqual('<td><p><strong>Алматы</strong></p></td>', str(cell))
        self.assertEqual(1, len(changes))

    def test_digit_inside_another_number_is_not_touched(self):
        cell, _c, _q = self.patch('<p>14 из 40</p>', '14 из 40', '15 из 40')
        self.assertEqual('15 из 40', cell.get_text())


class InstructionIsASourceTest(unittest.TestCase):
    def test_numbers_from_the_instruction_are_not_invented(self):
        result = revise.edit_by_instruction(
            current_title='Тарифы', current_html=ARTICLE,
            instruction='заполни Астану: комиссия 4,75 %, тариф Комфорт, депозит 15 000 тг',
            generate_fn=stub(reply(patches='- Т1 С3: | 4,75 | Комфорт')))
        self.assertEqual([], [w for w in result['warnings'] if 'которых нет' in w])
        self.assertIn('4,75', result['content'])
        self.assertTrue(any('заполнено' in c for c in result['changes']))

    def test_instruction_is_not_a_measure_of_length(self):
        """Указание длиннее статьи не делает её «короче документа».

        Найдено на стенде: с указанием в источниках короткая статья с таблицей
        получала «Текста в статье заметно меньше, чем в документе (145 против 256)».
        """
        result = revise.edit_by_instruction(
            current_title='Тарифы', current_html=ARTICLE,
            instruction=('заполни Астану: комиссия 4 %, тариф Комфорт; после Алматы вставь '
                         'Караганду 4,5 % Эконом; добавь колонку Телефон: +7 700 000 00 01, '
                         '+7 700 000 00 02; остальное оставь как есть, ничего не сокращай'),
            generate_fn=stub(reply(patches='- Т1 С3: | 4 | Комфорт')))
        self.assertFalse(any('заметно меньше' in w for w in result['warnings']),
                         result['warnings'])

    def test_invented_numbers_in_a_filled_table_are_still_caught(self):
        result = revise.edit_by_instruction(
            current_title='Тарифы', current_html=ARTICLE, instruction='заполни Астану',
            generate_fn=stub(reply(patches='- Т1 С3: | 7 777 | Комфорт')))
        self.assertTrue(any('7 777' in w or '7777' in w for w in result['warnings']))


class ParityTest(unittest.TestCase):
    """Имена классов таблицы — одни на сервер, узел редактора и стили."""

    def test_editor_and_styles_know_every_class(self):
        node = (ROOT / 'src/components/wiki/WikiTableTools.js').read_text(encoding='utf-8')
        css = (ROOT / 'src/components/wiki/wiki-theme.css').read_text(encoding='utf-8')
        for name in markup.TABLE_CLASSES:
            self.assertIn("'%s'" % name, node, name)
            self.assertIn('.%s' % name, css, name)

    def test_row_height_format_matches_the_editor(self):
        node = (ROOT / 'src/components/wiki/WikiTableTools.js').read_text(encoding='utf-8')
        self.assertIn('height: ${attributes.height}px', node)
        self.assertTrue(markup._ROW_STYLE_RE.match('height: 70px'))
        self.assertFalse(markup._ROW_STYLE_RE.match('height: 70%'))

    def test_marker_regexes_agree(self):
        """Хвост маркера таблицы узнают и сборка, и правка (сдвиг номеров, humanize)."""
        marker = '[[ТАБЛИЦА-3 по центру]]'
        self.assertTrue(authoring._TABLE_TOKEN_RE.search(marker))
        self.assertTrue(revise._MARKER_RE.search(marker))
        self.assertEqual('таблица 3', revise.humanize(marker))


if __name__ == '__main__':
    unittest.main()
