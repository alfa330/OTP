# -*- coding: utf-8 -*-
"""Сборка статьи из документа. Чистые тесты: ни базы, ни сети.

Закрепляется главное свойство фичи: ТАБЛИЦА НЕ ПРОХОДИТ ЧЕРЕЗ МОДЕЛЬ там, где
документ уже дал готовую сетку. Всё остальное в этом слое подчинено этому — и
маркеры, и возврат потерянной таблицы в конец, и предупреждения.

Числа в проверках взяты с настоящих файлов (scratchpad/check_parse.py):
  * mammoth отдаёт таблицу Word как <table><tbody><tr><td><p>…</p> — без <th> и
    без <thead> вовсе. Значит шапку обязана поднимать наша программа, иначе
    таблица уходит в индекс помощника как «знач | знач» без имён полей;
  * openpyxl в потоковом режиме теряет объединённые клетки: строка «Комиссия, %»
    над двумя колонками приезжает как ('Парк', 'Комиссия, %', None, …).
"""

import unittest

from wiki.ai import authoring


DOCX_LIKE = (
    '<h1>Условия аренды</h1>'
    '<p>Минимальный срок аренды 14 дней. Депозит 30 000 тг.</p>'
    '<table><tbody>'
    '<tr><td><p>Парк</p></td><td><p>Комиссия, %</p></td><td><p>Аренда</p></td></tr>'
    '<tr><td><p>Anytime</p></td><td><p>3,5</p></td><td><p>9 500</p></td></tr>'
    '</tbody></table>'
    '<p>Телефон +7 700 000 01 10.</p>'
)


class TableProtectionTest(unittest.TestCase):
    def test_table_is_replaced_by_marker(self):
        html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        self.assertIn('[[ТАБЛИЦА-1]]', html)
        self.assertNotIn('<table', html)
        self.assertEqual(1, len(tables))
        self.assertIn('Anytime', tables[0])

    def test_header_row_is_promoted(self):
        """mammoth <th> не даёт — шапку поднимаем сами, иначе индекс без имён полей."""
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        self.assertIn('<th>Парк</th>', tables[0])
        self.assertIn('<th>Комиссия, %</th>', tables[0])

    def test_digit_in_header_does_not_block_promotion(self):
        """«Комиссия, %» содержит знак, но это шапка, а не данные.

        Наивное правило «в шапке нет цифр» на таком заголовке ломается — а он
        типовой: в корпусе колонки называются «Депозит, тг», «Комиссия, %».
        """
        _html, tables, _images = authoring.protect_tables(
            '<table><tr><td>Город</td><td>Депозит, тг</td></tr>'
            '<tr><td>Алматы</td><td>5000</td></tr></table>')
        self.assertIn('<th>Депозит, тг</th>', tables[0])

    def test_numeric_first_row_is_not_a_header(self):
        """Первая строка из чисел — данные. В выгрузках Excel так бывает."""
        _html, tables, _images = authoring.protect_tables(
            '<table><tr><td>2024</td><td>15</td></tr>'
            '<tr><td>2025</td><td>18</td></tr></table>')
        self.assertNotIn('<th>', tables[0])

    def test_merged_cells_survive(self):
        """colspan и rowspan — смысл, а не оформление: они обязаны выжить."""
        _html, tables, _images = authoring.protect_tables(
            '<table><tr><th rowspan="2">Город</th><th colspan="2">Депозит</th></tr>'
            '<tr><th>пакет</th><th>короб</th></tr>'
            '<tr><td>Алматы</td><td>5000</td><td>12000</td></tr></table>')
        self.assertIn('rowspan="2"', tables[0])
        self.assertIn('colspan="2"', tables[0])

    def test_presentation_attributes_are_dropped(self):
        _html, tables, _images = authoring.protect_tables(
            '<table style="width:600px" class="MsoTable">'
            '<colgroup><col width="200"></colgroup>'
            '<tr><td colwidth="120" style="font-size:8pt">Парк</td>'
            '<td>Комиссия</td></tr>'
            '<tr><td>Anytime</td><td>3,5</td></tr></table>')
        self.assertNotIn('style', tables[0])
        self.assertNotIn('colgroup', tables[0])
        self.assertNotIn('colwidth', tables[0])

    def test_lone_paragraph_in_cell_is_unwrapped(self):
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        self.assertNotIn('<p>', tables[0])

    def test_hints_describe_each_table(self):
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        hints = authoring.table_hints(tables)
        self.assertIn('[[ТАБЛИЦА-1]]', hints)
        self.assertIn('Парк', hints)


class RestoreTest(unittest.TestCase):
    def test_marker_is_replaced_by_original_table(self):
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        restored, lost = authoring.restore_tables(
            '<h1>Тарифы</h1><p>[[ТАБЛИЦА-1]]</p>', tables)
        self.assertEqual([], lost)
        self.assertIn('<th>Парк</th>', restored)
        self.assertIn('Anytime', restored)

    def test_marker_survives_mangling(self):
        """Модель переставляет пробелы и теряет скобку — токен всё равно узнаём."""
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        for mangled in ('[[ТАБЛИЦА 1]]', '[ТАБЛИЦА-1]', '[[ ТАБЛИЦА-1 ]]'):
            restored, lost = authoring.restore_tables('<p>%s</p>' % mangled, tables)
            self.assertEqual([], lost, mangled)
            self.assertIn('Anytime', restored, mangled)

    def test_lost_table_goes_to_the_end_not_to_nowhere(self):
        """Проглоченный маркер не должен стоить документу таблицы.

        Тихо потерять данные документа нельзя: заметить пропавшую таблицу можно
        только зная, что она была.
        """
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        restored, lost = authoring.restore_tables('<p>Модель забыла маркер</p>', tables)
        self.assertEqual([1], lost)
        self.assertIn('Anytime', restored)
        self.assertIn('не размещённое по разделам', restored)

    def test_invented_marker_is_dropped(self):
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        restored, _lost = authoring.restore_tables(
            '<p>[[ТАБЛИЦА-1]]</p><p>[[ТАБЛИЦА-7]]</p>', tables)
        self.assertNotIn('ТАБЛИЦА-7', restored)

    def test_table_is_pulled_out_of_leaf_element(self):
        """Таблица внутри li/blockquote для чанкера индекса не существует.

        Обход добирается до таблицы только через контейнеры, иначе она попадает в
        индекс потоком значений — «Алматы 5% Астана 7%» вместо «Город: Алматы».
        """
        _html, tables, _images = authoring.protect_tables(DOCX_LIKE)
        for wrapper in ('li', 'blockquote', 'p'):
            restored, _lost = authoring.restore_tables(
                '<%s>[[ТАБЛИЦА-1]]</%s>' % (wrapper, wrapper), tables)
            self.assertNotIn('<%s><table' % wrapper, restored)
            self.assertIn('<table', restored)


class ImageProtectionTest(unittest.TestCase):
    """Картинки защищены тем же приёмом и по той же причине, что таблицы.

    Картинка из Word уже загружена в хранилище и получила постоянный адрес
    /api/wiki/file/<uuid>. Модель такой адрес не воспроизведёт, а canonicalize
    выбрасывает тег img целиком — без маркеров скриншоты инструкции исчезали бы
    молча: файл в бакете лежит и место занимает, а в статье его нет.
    """

    WITH_IMAGE = ('<p>До</p><img src="/api/wiki/file/abc" alt="Схема">'
                  '<p>После</p>')

    def test_image_becomes_a_marker(self):
        html, _tables, images = authoring.protect_tables(self.WITH_IMAGE)
        self.assertIn('[[КАРТИНКА-1]]', html)
        self.assertNotIn('<img', html)
        self.assertEqual(1, len(images))
        self.assertIn('/api/wiki/file/abc', images[0])

    def test_alt_is_kept(self):
        _html, _tables, images = authoring.protect_tables(self.WITH_IMAGE)
        self.assertIn('alt="Схема"', images[0])

    def test_image_without_src_is_dropped(self):
        html, _tables, images = authoring.protect_tables('<p>Т</p><img alt="пусто">')
        self.assertEqual([], images)
        self.assertNotIn('КАРТИНКА', html)

    def test_image_returns_to_its_marker(self):
        _html, _tables, images = authoring.protect_tables(self.WITH_IMAGE)
        restored, _lost = authoring.restore_tables(
            '<h1>Как сделать</h1><p>[[КАРТИНКА-1]]</p>', [], images)
        self.assertIn('/api/wiki/file/abc', restored)

    def test_lost_image_is_not_thrown_away(self):
        _html, _tables, images = authoring.protect_tables(self.WITH_IMAGE)
        restored, _lost = authoring.restore_tables('<h1>Х</h1><p>Текст</p>', [], images)
        self.assertIn('/api/wiki/file/abc', restored)
        self.assertIn('не размещённое по разделам', restored)

    def test_prompt_mentions_image_markers(self):
        prompt = authoring.build_user_prompt(
            filename='a.docx', kind='Word', body_html='<p>[[КАРТИНКА-1]]</p>',
            tables=[], images=['<img src="/api/wiki/file/abc">'])
        self.assertIn('[[КАРТИНКА-1]]', prompt)
        self.assertIn('КАРТИНОК В ДОКУМЕНТЕ: 1', prompt)


class CanonTest(unittest.TestCase):
    def test_deep_headings_become_h3(self):
        """h4 чанкер индекса границей не считает — раздел под ним не найдётся."""
        html = authoring.canonicalize('<h1>Раздел</h1><h4>Подраздел</h4><p>Текст</p>')
        self.assertIn('<h3>Подраздел</h3>', html)
        self.assertNotIn('<h4', html)

    def test_headings_are_lifted_to_h1(self):
        """Верхний уровень всегда h1, вложенность сохраняется.

        Замер на трёх настоящих файлах: из Word модель принесла h2, из Excel и
        PDF — h1. Уровень это оформление, и выравнивать его должна программа.
        """
        html = authoring.canonicalize('<h2>Раздел</h2><h3>Внутри</h3><h2>Второй</h2>')
        self.assertIn('<h1>Раздел</h1>', html)
        self.assertIn('<h2>Внутри</h2>', html)
        self.assertIn('<h1>Второй</h1>', html)

    def test_styles_and_classes_are_stripped(self):
        html = authoring.canonicalize(
            '<p style="font-size:18pt" class="MsoNormal">Текст</p>')
        self.assertEqual('<p>Текст</p>', html)

    def test_forbidden_containers_are_unwrapped_not_kept(self):
        html = authoring.canonicalize('<div><span>Текст</span></div>')
        self.assertNotIn('<div', html)
        self.assertNotIn('<span', html)
        self.assertIn('Текст', html)

    def test_collapsible_is_removed(self):
        """details/summary редактор TipTap уничтожит при первом сохранении."""
        html = authoring.canonicalize(
            '<details><summary>Заголовок</summary><p>Тело</p></details><p>Дальше</p>')
        self.assertNotIn('details', html)
        self.assertIn('Дальше', html)

    def test_empty_paragraphs_are_dropped(self):
        html = authoring.canonicalize('<p>Текст</p><p>&nbsp;</p><p>  </p>')
        self.assertEqual(1, html.count('<p>'))

    def test_leading_title_duplicate_is_dropped(self):
        html = authoring.drop_leading_title('<h1>Аренда авто</h1><p>Текст</p>',
                                            'Аренда авто')
        self.assertNotIn('<h1>', html)
        self.assertIn('Текст', html)

    def test_leading_other_heading_is_kept(self):
        html = authoring.drop_leading_title('<h1>Общие условия</h1><p>Текст</p>',
                                            'Аренда авто')
        self.assertIn('<h1>Общие условия</h1>', html)


class EnvelopeTest(unittest.TestCase):
    def test_three_parts_are_parsed(self):
        title, summary, body = authoring._envelope(
            'НАЗВАНИЕ: Аренда авто\nКРАТКО: Условия аренды.\nСТАТЬЯ:\n<p>Текст</p>')
        self.assertEqual('Аренда авто', title)
        self.assertEqual('Условия аренды.', summary)
        self.assertEqual('<p>Текст</p>', body)

    def test_code_fence_is_removed(self):
        _t, _s, body = authoring._envelope(
            '```html\nНАЗВАНИЕ: X\nСТАТЬЯ:\n<p>Текст</p>\n```')
        self.assertNotIn('```', body)

    def test_without_envelope_html_is_still_found(self):
        title, _s, body = authoring._envelope('Вот статья:\n<h1>Раздел</h1><p>Текст</p>')
        self.assertEqual('', title)
        self.assertTrue(body.startswith('<h1>'))


class WarningsTest(unittest.TestCase):
    def test_invented_number_is_reported(self):
        warnings = authoring.structure_warnings(
            source_html='', source_text='Депозит 30 000 тг, срок 14 дней',
            result_html='<h1>Условия</h1><p>Депозит 45 000 тг</p>', lost_tables=[])
        self.assertTrue(any('которых нет в документе' in w for w in warnings))

    def test_matching_numbers_are_silent(self):
        warnings = authoring.structure_warnings(
            source_html='', source_text='Депозит 30 000 тг, срок 14 дней',
            result_html='<h1>Условия</h1><p>Депозит 30 000 тг, срок 14 дней</p>',
            lost_tables=[])
        self.assertEqual([], [w for w in warnings if 'которых нет' in w])

    def test_lost_table_is_reported(self):
        warnings = authoring.structure_warnings(
            source_html='<table><tr><td>1</td></tr></table>', source_text='',
            result_html='<h1>Х</h1>', lost_tables=[1])
        self.assertTrue(any('не расставила' in w for w in warnings))

    def test_missing_heading_is_reported(self):
        warnings = authoring.structure_warnings(
            source_html='', source_text='', result_html='<p>Просто текст</p>',
            lost_tables=[])
        self.assertTrue(any('нет ни одного заголовка' in w for w in warnings))

    def test_shrinking_is_reported(self):
        """Пересказ вместо переноса — тоже дефект, и он должен быть назван."""
        warnings = authoring.structure_warnings(
            source_html='', source_text='х' * 4000,
            result_html='<h1>Итог</h1><p>%s</p>' % ('х' * 500), lost_tables=[])
        self.assertTrue(any('заметно меньше' in w for w in warnings))

    def test_truncation_is_reported(self):
        """Обрыв по потолку вывода приходит с HTTP 200 и выглядит нормальным."""
        self.assertIsNotNone(authoring.truncation_warning({'finish': 'MAX_TOKENS'}))
        self.assertIsNotNone(authoring.truncation_warning({'finish': 'length'}))
        self.assertIsNone(authoring.truncation_warning({'finish': 'STOP'}))


class ComposeTest(unittest.TestCase):
    def _generate(self, answer):
        seen = {}

        def generate_fn(system, user, **kwargs):
            seen['system'] = system
            seen['user'] = user
            seen['max_tokens'] = kwargs.get('max_tokens')
            return answer, {'provider': 'test', 'model': 'stub', 'finish': 'STOP'}

        return generate_fn, seen

    def test_model_never_sees_the_table(self):
        """Главное свойство фичи, зафиксированное тестом."""
        generate_fn, seen = self._generate(
            'НАЗВАНИЕ: Аренда\nКРАТКО: Кратко.\nСТАТЬЯ:\n'
            '<h1>Условия</h1><p>[[ТАБЛИЦА-1]]</p>')
        result = authoring.compose(
            filename='rent.docx', kind='Word', source_html=DOCX_LIKE,
            source_text='Минимальный срок аренды 14 дней. Депозит 30 000 тг.',
            generate_fn=generate_fn)
        self.assertNotIn('Anytime', seen['user'])
        self.assertIn('[[ТАБЛИЦА-1]]', seen['user'])
        self.assertIn('Anytime', result['content'])
        self.assertEqual(1, result['tables'])

    def test_output_cap_is_raised_for_an_article(self):
        """Потолок ответа в чате статью обрезал бы на середине."""
        generate_fn, seen = self._generate('СТАТЬЯ:\n<h1>Х</h1><p>Текст</p>')
        authoring.compose(filename='a.docx', kind='Word', source_html='<p>Текст</p>',
                          generate_fn=generate_fn)
        self.assertEqual(authoring.MAX_OUTPUT_TOKENS, seen['max_tokens'])

    def test_result_is_sanitized(self):
        generate_fn, _seen = self._generate(
            'СТАТЬЯ:\n<h1>Х</h1><script>alert(1)</script><p>Текст</p>')
        result = authoring.compose(filename='a.docx', kind='Word',
                                   source_html='<p>Текст</p>', generate_fn=generate_fn)
        self.assertNotIn('script', result['content'])

    def test_summary_falls_back_to_text(self):
        generate_fn, _seen = self._generate('СТАТЬЯ:\n<h1>Х</h1><p>Про аренду</p>')
        result = authoring.compose(filename='a.docx', kind='Word',
                                   source_html='<p>Про аренду</p>',
                                   generate_fn=generate_fn)
        self.assertIn('Про аренду', result['summary'])

    def test_file_branch_uses_the_file_function(self):
        """PDF и скан уходят в модель файлом — иначе таблицы из них не собрать."""
        calls = {}

        def generate_file_fn(system, user, **kwargs):
            calls['mime'] = kwargs.get('mime')
            calls['blob'] = kwargs.get('blob')
            calls['system'] = system
            return 'СТАТЬЯ:\n<h1>Х</h1><p>Текст</p>', {'provider': 'vertex'}

        def generate_fn(*_args, **_kwargs):
            raise AssertionError('текстовый путь для файла использоваться не должен')

        result = authoring.compose(
            filename='doc.pdf', kind='PDF', generate_fn=generate_fn,
            blob=b'%PDF-1.4', mime='application/pdf',
            generate_file_fn=generate_file_fn)
        self.assertEqual('application/pdf', calls['mime'])
        self.assertEqual(b'%PDF-1.4', calls['blob'])
        self.assertIn('ЧИТАЕШЬ САМ', calls['system'])
        self.assertIn('Текст', result['content'])


if __name__ == '__main__':
    unittest.main()
