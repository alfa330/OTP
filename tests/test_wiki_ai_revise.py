# -*- coding: utf-8 -*-
"""Правка существующей статьи: обновление документом и правка по указанию.

Чистые тесты: ни базы, ни сети. Закрепляется то, ради чего модуль отделён от
сборки статьи с нуля — на входе текст, который люди уже читали, и молча потерять
из него нельзя ничего.

Проверки опираются на живой прогон 11.08.2026 (scratchpad/revise_run.py) по
статье «Реестр акций таксопарка iGroup» и её «новой версии»: там нашлись обе
ошибки, закрытые здесь тестами — утёкшие наружу служебные маркеры и ложное
предупреждение о «выдуманной» дате, которая была в названии статьи.
"""

import unittest

from wiki.ai import revise


ARTICLE = (
    '<h1>Акции</h1>'
    '<p>Реестр действующих акций парка.</p>'
    '<table><tbody>'
    '<tr><th>Акция</th><th>Срок</th></tr>'
    '<tr><td>50п-5к</td><td>14 дней</td></tr>'
    '</tbody></table>'
    '<h1>Завершённые</h1>'
    '<p>Акция «Весна» завершена.</p>'
)

DOCUMENT = (
    '<h1>Обновление</h1>'
    '<table><tbody>'
    '<tr><th>Акция</th><th>Срок</th></tr>'
    '<tr><td>50п-5к</td><td>20 дней</td></tr>'
    '</tbody></table>'
)


def stub(answer, seen=None):
    def generate_fn(system, user, **kwargs):
        if seen is not None:
            seen['system'] = system
            seen['user'] = user
            seen['max_tokens'] = kwargs.get('max_tokens')
        return answer, {'provider': 'test', 'model': 'stub', 'finish': 'STOP'}
    return generate_fn


class ParseReplyTest(unittest.TestCase):
    REPLY = ('ИЗМЕНЕНИЯ:\n- срок 50п-5к: 14 → 20 дней\n'
             'ВОПРОСЫ:\n- удалить ли «Весна»?\n'
             'СТАТЬЯ:\n<h1>Акции</h1><p>[[ТАБЛИЦА-2]]</p>')

    def test_three_parts(self):
        changes, questions, body = revise.parse_reply(self.REPLY)
        self.assertEqual(['срок 50п-5к: 14 → 20 дней'], changes)
        self.assertEqual(['удалить ли «Весна»?'], questions)
        self.assertTrue(body.startswith('<h1>Акции</h1>'))

    def test_no_questions_means_empty_list(self):
        _c, questions, _b = revise.parse_reply(
            'ИЗМЕНЕНИЯ:\n- правка\nВОПРОСЫ:\nнет\nСТАТЬЯ:\n<p>Текст</p>')
        self.assertEqual([], questions)

    def test_markers_never_reach_the_human(self):
        """Замер: маркеры утекли и в список изменений, и в вопрос редактору."""
        changes, questions, _b = revise.parse_reply(
            'ИЗМЕНЕНИЯ:\n- добавлена [[ТАБЛИЦА-4]]\n'
            'ВОПРОСЫ:\n- что с [[ТАБЛИЦА-2]]?\nСТАТЬЯ:\n<p>Т</p>')
        self.assertNotIn('[[', changes[0])
        self.assertIn('таблица 4', changes[0])
        self.assertNotIn('[[', questions[0])

    def test_without_envelope_body_starts_at_html(self):
        _c, _q, body = revise.parse_reply('Вот обновлённая статья:\n<h1>Акции</h1><p>Т</p>')
        self.assertTrue(body.startswith('<h1>'))

    def test_code_fence_removed(self):
        _c, _q, body = revise.parse_reply('```html\nСТАТЬЯ:\n<p>Текст</p>\n```')
        self.assertNotIn('```', body)


class PrepareTest(unittest.TestCase):
    def test_numbering_is_continuous_across_sources(self):
        """Сквозная нумерация: замена таблицы — это замена номера, и только."""
        body, doc, tables, _images = revise._prepare(ARTICLE, DOCUMENT)
        self.assertIn('[[ТАБЛИЦА-1]]', body)
        self.assertIn('[[ТАБЛИЦА-2]]', doc)
        self.assertEqual(2, len(tables))
        self.assertIn('14 дней', tables[0])
        self.assertIn('20 дней', tables[1])


class UpdateTest(unittest.TestCase):
    def test_model_never_rewrites_tables_only_marks_them(self):
        """В ТЕЛЕ статьи таблиц нет — только маркеры.

        Содержимое таблиц модель при этом видит, отдельным блоком: без него ей
        нечего сверять с документом и нечем указать номер клетки. Разница
        принципиальная — видеть можно, переписывать нельзя.
        """
        seen = {}
        revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='50п-5к 20 дней', filename='new.docx', kind='Word',
            generate_fn=stub('СТАТЬЯ:\n<h1>Акции</h1><p>[[ТАБЛИЦА-2]]</p>', seen))
        body = seen['user'].split('СОДЕРЖИМОЕ ТАБЛИЦ')[0]
        self.assertNotIn('<table', body)
        self.assertNotIn('14 дней', body)
        self.assertIn('[[ТАБЛИЦА-1]]', seen['user'])
        self.assertIn('[[ТАБЛИЦА-2]]', seen['user'])

    def test_new_table_replaces_the_old_one(self):
        result = revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='50п-5к 20 дней', generate_fn=stub(
                'ИЗМЕНЕНИЯ:\n- срок 14 → 20 дней\nСТАТЬЯ:\n'
                '<h1>Акции</h1><p>[[ТАБЛИЦА-2]]</p>'),
            filename='new.docx', kind='Word')
        self.assertIn('20 дней', result['content'])
        self.assertIn('срок 14 → 20 дней', result['changes'])

    def test_dropped_marker_does_not_lose_the_table(self):
        result = revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='', generate_fn=stub('СТАТЬЯ:\n<h1>Акции</h1><p>Ничего</p>'),
            filename='new.docx', kind='Word')
        self.assertIn('14 дней', result['content'])
        self.assertIn('20 дней', result['content'])
        self.assertIn('не размещённое по разделам', result['content'])

    def test_shrinking_is_reported(self):
        """Пропавший раздел — главный риск правки, и он обязан быть назван.

        Считается доля от ТЕКУЩЕЙ статьи, а не от документа: потерять раздел,
        которого документ не касался, страшнее, чем не дописать новый.
        """
        long_article = ('<h1>Акции</h1><p>%s</p><h1>Порядок</h1><p>%s</p>'
                        % ('Подробное описание условий. ' * 20,
                           'Пошаговый порядок подключения. ' * 20))
        result = revise.update_from_document(
            current_title='Акции', current_html=long_article, document_html='',
            document_text='', generate_fn=stub(
                'ИЗМЕНЕНИЯ:\n- сокращено\nСТАТЬЯ:\n<h1>Акции</h1><p>Кратко.</p>'),
            filename='new.docx', kind='Word')
        self.assertTrue(any('короче' in w for w in result['warnings']),
                        result['warnings'])

    def test_missing_change_list_is_reported(self):
        result = revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='', generate_fn=stub('СТАТЬЯ:\n<h1>Акции</h1><p>[[ТАБЛИЦА-1]]</p>'),
            filename='new.docx', kind='Word')
        self.assertTrue(any('не перечислил изменения' in w for w in result['warnings']))

    def test_file_branch_used_for_pdf(self):
        calls = {}

        def generate_file_fn(system, user, **kwargs):
            calls['mime'] = kwargs.get('mime')
            calls['system'] = system
            return 'СТАТЬЯ:\n<h1>Акции</h1><p>[[ТАБЛИЦА-1]]</p>', {'provider': 'vertex'}

        def text_fn(*_args, **_kwargs):
            raise AssertionError('для файла текстовый путь не годится')

        result = revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, generate_fn=text_fn,
            blob=b'%PDF-1.4', mime='application/pdf', filename='new.pdf', kind='PDF',
            generate_file_fn=generate_file_fn)
        self.assertEqual('application/pdf', calls['mime'])
        self.assertIn('ЧИТАЕШЬ САМ', calls['system'])
        self.assertIn('14 дней', result['content'])


class TablePatchIntegrationTest(unittest.TestCase):
    """Правки клеток должны доходить до результата через обычный путь обновления."""

    def test_cell_patch_changes_the_table(self):
        result = revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='50п-5к 20 дней', filename='new.docx', kind='Word',
            generate_fn=stub('\n'.join([
                'ПРАВКИ ТАБЛИЦ:',
                '- Т1 С2 К2: 14 дней => 20 дней',
                'ИЗМЕНЕНИЯ:',
                '- срок обновлён',
                'СТАТЬЯ:',
                '<h1>Акции</h1><p>[[ТАБЛИЦА-1]]</p>'
                '<h1>Завершённые</h1><p>Акция «Весна» завершена.</p>'])))
        self.assertIn('20 дней', result['content'])
        self.assertNotIn('14 дней', result['content'])
        self.assertTrue(any('строка 2' in c for c in result['changes']))

    def test_model_sees_table_contents_but_cannot_rewrite_them(self):
        seen = {}
        revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='', filename='new.docx', kind='Word',
            generate_fn=stub('СТАТЬЯ:\n<p>[[ТАБЛИЦА-1]]</p>', seen))
        # Содержимое видно — иначе нечего сверять и нечем указать номер клетки.
        self.assertIn('С2: К1=50п-5к', seen['user'])
        # А в самом тексте статьи таблицы по-прежнему только маркерами.
        body = seen['user'].split('СОДЕРЖИМОЕ ТАБЛИЦ')[0]
        self.assertNotIn('<table', body)

    def test_deletion_request_becomes_a_question(self):
        result = revise.update_from_document(
            current_title='Акции', current_html=ARTICLE, document_html=DOCUMENT,
            document_text='', filename='new.docx', kind='Word',
            generate_fn=stub('\n'.join([
                'ПРАВКИ ТАБЛИЦ:',
                '- Т1 -СТРОКА 2: нет в документе',
                'ИЗМЕНЕНИЯ:',
                '- сверено',
                'СТАТЬЯ:',
                '<h1>Акции</h1><p>[[ТАБЛИЦА-1]]</p>'
                '<h1>Завершённые</h1><p>Акция «Весна» завершена.</p>'])))
        self.assertIn('50п-5к', result['content'])
        self.assertTrue(any('Удалить' in q for q in result['questions']))


class DedupeTest(unittest.TestCase):
    def test_near_identical_questions_collapse(self):
        """Замер: про исчезнувшую акцию задавалось два почти одинаковых вопроса."""
        lines = [
            'Строку «Розыгрыш Elantra» (таблица 1, строка 17) ИИ предлагает удалить: '
            'акции нет в новом документе. Удалить её?',
            'Акция «Розыгрыш Elantra» (Таблица 1, строка 17) отсутствует в новом '
            'документе. Нужно ли её удалить?',
        ]
        self.assertEqual(1, len(revise.dedupe(lines)))

    def test_different_changes_are_kept(self):
        """Спрятать настоящее изменение хуже, чем показать его дважды."""
        lines = ['убран абзац про доставку', 'добавлен раздел про термопакеты']
        self.assertEqual(2, len(revise.dedupe(lines)))


class EditTest(unittest.TestCase):
    def test_instruction_reaches_the_model(self):
        seen = {}
        revise.edit_by_instruction(
            current_title='Акции', current_html=ARTICLE,
            instruction='сократи вдвое',
            generate_fn=stub('СТАТЬЯ:\n<h1>Акции</h1><p>[[ТАБЛИЦА-1]]</p>', seen))
        self.assertIn('сократи вдвое', seen['user'])
        self.assertIn('УКАЗАНИЕ РЕДАКТОРА', seen['user'])

    def test_empty_instruction_is_rejected(self):
        with self.assertRaises(ValueError):
            revise.edit_by_instruction(current_title='Акции', current_html=ARTICLE,
                                       instruction='   ', generate_fn=stub(''))

    def test_title_counts_as_a_source_for_numbers(self):
        """Замер: дата из НАЗВАНИЯ статьи объявлялась выдуманной.

        Ложное предупреждение обесценивает все остальные — их перестают читать.
        """
        result = revise.edit_by_instruction(
            current_title='Реестр акций от 24.07.2026', current_html=ARTICLE,
            instruction='добавь вступление',
            generate_fn=stub('ИЗМЕНЕНИЯ:\n- вступление\nСТАТЬЯ:\n'
                             '<h1>Акции</h1><p>Реестр от 24.07.2026.</p>'
                             '<p>[[ТАБЛИЦА-1]]</p><h1>Завершённые</h1>'
                             '<p>Акция «Весна» завершена.</p>'))
        self.assertEqual([], [w for w in result['warnings'] if 'которых нет' in w])

    def test_invented_number_is_still_caught(self):
        result = revise.edit_by_instruction(
            current_title='Акции', current_html=ARTICLE,
            instruction='добавь вступление',
            generate_fn=stub('ИЗМЕНЕНИЯ:\n- вступление\nСТАТЬЯ:\n'
                             '<h1>Акции</h1><p>Депозит 45 000 тг.</p>'
                             '<p>[[ТАБЛИЦА-1]]</p><h1>Завершённые</h1>'
                             '<p>Акция «Весна» завершена.</p>'))
        self.assertTrue(any('которых нет' in w for w in result['warnings']))

    def test_tables_are_untouched_by_a_prose_instruction(self):
        result = revise.edit_by_instruction(
            current_title='Акции', current_html=ARTICLE,
            instruction='перепиши в деловом тоне',
            generate_fn=stub('СТАТЬЯ:\n<h1>Акции</h1><p>Настоящий реестр содержит '
                             'перечень действующих акций парка.</p><p>[[ТАБЛИЦА-1]]</p>'
                             '<h1>Завершённые</h1><p>Акция «Весна» завершена.</p>'))
        self.assertIn('<th>Акция</th>', result['content'])
        self.assertIn('14 дней', result['content'])


if __name__ == '__main__':
    unittest.main()
