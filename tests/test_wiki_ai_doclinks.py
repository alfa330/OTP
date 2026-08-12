# -*- coding: utf-8 -*-
"""Ссылки документа: извлечение из PDF и путь до статьи.

Дефект найден сверкой боевого файла «Акции 24.07.2026 - коррект.pdf» со статьёй,
собранной из него же: в PDF семь аннотаций-ссылок (три адреса — форма регистрации
в акциях, форма-редактор, админка yataxi), а в статье не оказалось НИ ОДНОГО, зато
стоял пустой href="#".

Причина не в модели: в PDF адрес лежит в АННОТАЦИИ страницы, а на самой странице
видны только слова «по ссылке» или «форму регистрации». Ни vision, ни извлечённый
pypdf-текст адреса не содержат — увидеть его нечем. Поэтому адреса достаются
программой и подкладываются модели отдельным списком, а потерянные дописываются
в конец статьи.
"""

import unittest

from wiki import importer
from wiki.ai import authoring

LINKS = [
    {'url': 'https://docs.google.com/forms/d/e/1FAIpQLSeFBF/viewform?usp=dialog',
     'label': 'форму регистрации в акциях', 'page': 1},
    {'url': 'https://backend.yataxi.kz/admin/driver-accounts/',
     'label': 'по ссылке', 'page': 1},
]


def _pdf_with_link(url, label='po ssylke'):
    """Минимальный PDF с текстом и аннотацией-ссылкой на него."""
    content = ('BT /F1 12 Tf 72 700 Td (%s) Tj ET' % label).encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        ('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
         '/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> '
         '/Annots [6 0 R] >>').encode('latin-1'),
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(content), content),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        ('<< /Type /Annot /Subtype /Link /Rect [70 690 300 715] '
         '/A << /S /URI /URI (%s) >> >>' % url).encode('latin-1'),
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    start = len(out)
    out += b'xref\n0 %d\n' % (len(objects) + 1)
    out += b'0000000000 65535 f \n'
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += (b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'
            % (len(objects) + 1, start))
    return bytes(out)


class PdfLinkExtractionTest(unittest.TestCase):
    def test_annotation_url_is_found(self):
        # Ярлык латиницей: минимальный PDF собран на Helvetica без встроенной
        # кодировки, кириллица в такой поток просто не пишется. Проверяется тут
        # механика извлечения, а не кодировки — на боевом файле она уже прошла.
        data = _pdf_with_link('https://forms.gle/abc123')
        found = importer.pdf_links(data)
        self.assertEqual(1, len(found))
        self.assertEqual('https://forms.gle/abc123', found[0]['url'])

    def test_label_comes_from_the_text_under_the_rectangle(self):
        """Ярлык нужен, чтобы адрес встал на своё место, а не в конец статьи."""
        data = _pdf_with_link('https://forms.gle/abc123', label='po ssylke')
        found = importer.pdf_links(data)
        self.assertIn('po ssylke', found[0]['label'])

    def test_duplicates_are_collapsed(self):
        """На боевом файле один адрес стоял семь раз — список должен быть по адресам."""
        data = _pdf_with_link('https://forms.gle/abc123')
        found = importer.pdf_links(data + b'')
        self.assertEqual(1, len({item['url'] for item in found}))

    def test_broken_pdf_does_not_raise(self):
        with self.assertRaises(Exception):
            importer.pdf_links(b'not a pdf at all')


class PromptBlockTest(unittest.TestCase):
    def test_block_lists_label_and_url(self):
        block = authoring.links_block(LINKS)
        self.assertIn('форму регистрации в акциях', block)
        self.assertIn('https://backend.yataxi.kz/admin/driver-accounts/', block)

    def test_empty_links_give_empty_block(self):
        self.assertEqual('', authoring.links_block([]))
        self.assertEqual('', authoring.links_block(None))

    def test_url_without_address_is_skipped(self):
        self.assertEqual('', authoring.links_block([{'url': '', 'label': 'пусто'}]))


class MissingLinksTest(unittest.TestCase):
    def test_present_link_is_not_reported(self):
        html = '<p><a href="%s">по ссылке</a></p>' % LINKS[1]['url']
        self.assertEqual([], authoring.missing_links(html, [LINKS[1]]))

    def test_absent_link_is_reported(self):
        self.assertEqual(1, len(authoring.missing_links('<p>без ссылок</p>', [LINKS[1]])))

    def test_escaped_ampersand_still_counts_as_present(self):
        """Санитайзер экранирует &, и адрес с параметрами иначе считался бы пропавшим."""
        html = '<p><a href="https://x.test/a?b=1&amp;c=2">форма</a></p>'
        self.assertEqual([], authoring.missing_links(
            html, [{'url': 'https://x.test/a?b=1&c=2', 'label': 'форма'}]))

    def test_lost_links_are_appended_not_dropped(self):
        html = authoring.append_links('<h1>Акции</h1>', [LINKS[1]])
        self.assertIn('Ссылки из документа', html)
        self.assertIn(LINKS[1]['url'], html)


class ComposeLinksTest(unittest.TestCase):
    def _generate(self, answer):
        seen = {}

        def generate_fn(system, user, **kwargs):
            seen['user'] = user
            return answer, {'provider': 'test', 'model': 'stub', 'finish': 'STOP'}

        return generate_fn, seen

    def test_links_reach_the_prompt(self):
        generate_fn, seen = self._generate('СТАТЬЯ:\n<h1>Х</h1><p>Текст</p>')
        authoring.compose(filename='a.docx', kind='Word',
                          source_html='<p>Текст</p>', generate_fn=generate_fn,
                          links=LINKS)
        self.assertIn('backend.yataxi.kz', seen['user'])

    def test_unplaced_link_is_appended_with_a_warning(self):
        generate_fn, _seen = self._generate('СТАТЬЯ:\n<h1>Х</h1><p>Текст</p>')
        result = authoring.compose(filename='a.docx', kind='Word',
                                   source_html='<p>Текст</p>',
                                   generate_fn=generate_fn, links=LINKS)
        self.assertIn('Ссылки из документа', result['content'])
        self.assertTrue(any('не расставил' in w for w in result['warnings']))

    def test_placed_link_gives_no_warning(self):
        answer = ('СТАТЬЯ:\n<h1>Х</h1><p><a href="%s">по ссылке</a> '
                  '<a href="%s">форма</a></p>' % (LINKS[1]['url'], LINKS[0]['url']))
        generate_fn, _seen = self._generate(answer)
        result = authoring.compose(filename='a.docx', kind='Word',
                                   source_html='<p>Текст</p>',
                                   generate_fn=generate_fn, links=LINKS)
        self.assertEqual([], [w for w in result['warnings'] if 'не расставил' in w])
        self.assertNotIn('Ссылки из документа', result['content'])


if __name__ == '__main__':
    unittest.main()
