# -*- coding: utf-8 -*-
"""Нарезка статей вики на куски для ИИ-помощника.

Тесты чистые: ни базы, ни сети. Проверяется то, на чём нарезка реально ломалась
при прогоне по боевому корпусу, а не абстрактные свойства:

  * h4 НЕ граница куска (в корпусе их 260 против 144 h1, и 130 сидят в одной
    статье — по h1-h4 получалось 411 огрызков с медианой 165 символов);
  * таблицы разворачиваются в «поле: значение», иначе to_plain_text превращает
    их в поток значений и модель склеивает не тот город с не той комиссией;
  * data-required-for-ack ловится только со значением true/1: на проде 27
    упоминаний атрибута, но 25 из них — "false", и наивная проверка «атрибут
    присутствует» помечала бы обязательными 27 блоков вместо 2;
  * подряд идущие одинаковые строки склеиваются: разметка раскрывашки держит
    название и в data-title, и внутри блока, поэтому заголовок приходил дважды;
  * статья с пустым телом попадает в индекс через content_plain — иначе
    «Классификатор авто» (content длиной 0) выпадала целиком, а это единственная
    статья на проде с настроенными правилами доступа.
"""

import unittest

from wiki.ai.chunker import (HARD_CAP_CHARS, MIN_CHARS, chunk_article,
                            parse_blocks)


def _para(text, times=1):
    return ''.join(f'<p>{text}</p>' for _ in range(times))


LONG = 'Условие аренды описано подробно и занимает много места в тексте. ' * 5


class ParseBlocksTest(unittest.TestCase):
    def test_heading_levels(self):
        blocks = parse_blocks('<h1>А</h1><h2>Б</h2><h3>В</h3><h4>Г</h4><p>текст</p>')
        kinds = [(b['kind'], b['level'], b['text']) for b in blocks]
        self.assertEqual([('heading', 1, 'А'), ('heading', 2, 'Б'),
                          ('heading', 3, 'В'), ('heading', 4, 'Г'),
                          ('text', 0, 'текст')], kinds)

    def test_skips_media_and_scripts(self):
        blocks = parse_blocks(
            '<p>видно</p><script>alert(1)</script><style>p{}</style>'
            '<img src="x.png"><svg><path/></svg>')
        self.assertEqual(['видно'], [b['text'] for b in blocks])

    def test_recurses_into_containers(self):
        blocks = parse_blocks('<div><section><h2>Заголовок</h2><p>тело</p></section></div>')
        self.assertEqual(['Заголовок', 'тело'], [b['text'] for b in blocks])

    def test_list_is_one_block(self):
        """Список не разбирается по li: иначе куски рассыпаются на пункты."""
        blocks = parse_blocks('<ul><li>раз</li><li>два</li></ul>')
        self.assertEqual(1, len(blocks))
        self.assertIn('раз', blocks[0]['text'])
        self.assertIn('два', blocks[0]['text'])


class AckMarkingTest(unittest.TestCase):
    def test_only_true_counts(self):
        html = ('<details data-required-for-ack="false"><summary>Нет</summary>'
                '<p>обычный текст</p></details>')
        self.assertFalse(any(b['ack'] for b in parse_blocks(html)))

    def test_true_marks_children(self):
        html = ('<details data-required-for-ack="true"><summary>Регламент</summary>'
                '<p>обязательный текст</p></details>')
        blocks = parse_blocks(html)
        self.assertTrue(all(b['ack'] for b in blocks))

    def test_numeric_true_counts(self):
        html = '<details data-required-for-ack="1"><p>текст</p></details>'
        self.assertTrue(any(b['ack'] for b in parse_blocks(html)))

    def test_chunk_carries_ack(self):
        html = ('<h1>Заголовок</h1>' + _para('вступление', 3)
                + '<details data-required-for-ack="true"><summary>Чек-лист</summary>'
                + _para(LONG) + '</details>')
        chunks = chunk_article(html)
        self.assertTrue(any(c['requires_ack'] for c in chunks))

    def test_data_title_used_without_summary(self):
        html = '<details data-title="Название блока"><p>тело блока</p></details>'
        texts = [b['text'] for b in parse_blocks(html)]
        self.assertIn('Название блока', texts)


class TableTest(unittest.TestCase):
    def test_header_becomes_field_value(self):
        html = ('<table><tr><th>Город</th><th>Комиссия</th></tr>'
                '<tr><td>Алматы</td><td>5%</td></tr>'
                '<tr><td>Астана</td><td>7%</td></tr></table>')
        chunks = chunk_article(html)
        text = '\n'.join(c['text'] for c in chunks)
        self.assertIn('Город: Алматы; Комиссия: 5%', text)
        self.assertIn('Город: Астана; Комиссия: 7%', text)
        # Ровно та ошибка, ради которой слой и написан: значения не должны
        # склеиваться в поток без разделителей.
        self.assertNotIn('Алматы 5% Астана', text)

    def test_table_without_header(self):
        html = '<table><tr><td>2026</td><td>отчёт</td></tr></table>'
        text = '\n'.join(c['text'] for c in chunk_article(html))
        self.assertIn('2026 | отчёт', text)

    def test_caption_kept(self):
        html = ('<table><caption>Тарифы</caption><tr><th>А</th></tr>'
                '<tr><td>1</td></tr></table>')
        text = '\n'.join(c['text'] for c in chunk_article(html))
        self.assertIn('Таблица: Тарифы', text)

    def test_rows_are_separate_lines(self):
        """Строки таблицы — отдельные строки куска: длинную таблицу рвём по ним."""
        rows = ''.join(f'<tr><td>Город{i}</td><td>{i}%</td></tr>' for i in range(40))
        html = f'<table><tr><th>Город</th><th>Ставка</th></tr>{rows}</table>'
        chunks = chunk_article(html)
        for chunk in chunks:
            for line in chunk['text'].splitlines():
                # Ни одна строка не должна оказаться обрубком «Город»
                self.assertFalse(line.endswith('Город:'), line)


class ChunkingTest(unittest.TestCase):
    def test_h4_does_not_split(self):
        html = ('<h1>Раздел</h1>' + _para(LONG)
                + '<h4>Подпункт</h4>' + _para(LONG))
        chunks = chunk_article(html)
        paths = {c['heading_path'] for c in chunks}
        self.assertTrue(all(p.startswith('Раздел') for p in paths), paths)

    def test_h2_splits_when_enough_text(self):
        html = ('<h1>Первый</h1>' + _para(LONG)
                + '<h2>Второй</h2>' + _para(LONG))
        chunks = chunk_article(html)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(any('Второй' in c['heading_path'] for c in chunks))

    def test_small_sections_merge(self):
        """Секция из одного заголовка не должна уезжать отдельным куском."""
        html = ''.join(f'<h2>Пункт {i}</h2><p>коротко</p>' for i in range(5))
        chunks = chunk_article(html)
        self.assertEqual(1, len(chunks))
        self.assertIn('Пункт 4', chunks[0]['text'])

    def test_heading_path_is_hierarchical(self):
        html = ('<h1>Аренда</h1><h2>Условия</h2><h3>Залог</h3>' + _para(LONG))
        chunks = chunk_article(html)
        self.assertEqual('Аренда > Условия > Залог', chunks[0]['heading_path'])

    def test_sibling_heading_replaces_not_nests(self):
        html = ('<h1>А</h1><h2>Б1</h2>' + _para(LONG)
                + '<h2>Б2</h2>' + _para(LONG))
        paths = [c['heading_path'] for c in chunk_article(html)]
        self.assertTrue(any(p == 'А > Б2' for p in paths), paths)

    def test_hard_cap_respected(self):
        html = _para(LONG, 40)
        for chunk in chunk_article(html):
            self.assertLessEqual(len(chunk['text']), HARD_CAP_CHARS)

    def test_single_huge_paragraph_split_on_words(self):
        html = '<p>' + ('слово ' * 2000) + '</p>'
        chunks = chunk_article(html)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk['text']), HARD_CAP_CHARS)
            self.assertNotIn('сло\n', chunk['text'])

    def test_adjacent_duplicates_collapsed(self):
        html = '<p>Чек-лист оператора</p><p>Чек-лист оператора</p><p>дальше текст</p>'
        text = chunk_article(html)[0]['text']
        self.assertEqual(1, text.count('Чек-лист оператора'))

    def test_empty_html_uses_fallback(self):
        chunks = chunk_article('', 'Классификатор авто. Проверка по тарифам.')
        self.assertEqual(1, len(chunks))
        self.assertIn('Классификатор', chunks[0]['text'])

    def test_empty_html_without_fallback(self):
        self.assertEqual([], chunk_article(''))
        self.assertEqual([], chunk_article(None))

    def test_fallback_ignored_when_html_has_text(self):
        chunks = chunk_article('<p>реальное тело статьи</p>', 'запасной текст')
        text = '\n'.join(c['text'] for c in chunks)
        self.assertIn('реальное тело', text)
        self.assertNotIn('запасной', text)

    def test_no_empty_chunks(self):
        html = '<h1>Пусто</h1><p></p><p>   </p><h2>Тоже</h2>'
        for chunk in chunk_article(html):
            self.assertTrue(chunk['text'].strip())

    def test_min_chars_is_a_merge_threshold_not_a_filter(self):
        """Короткая статья целиком остаётся куском, а не выбрасывается."""
        chunks = chunk_article('<p>Термопакет выдаётся в офисе.</p>')
        self.assertEqual(1, len(chunks))
        self.assertLess(len(chunks[0]['text']), MIN_CHARS)


if __name__ == '__main__':
    unittest.main()
