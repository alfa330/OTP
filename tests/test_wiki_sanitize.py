# -*- coding: utf-8 -*-
"""Серверная санитизация HTML статей.

Два требования одновременно, и они тянут в разные стороны:
  1. не пропустить исполняемый код — в общем портале правка статьи не должна
     превращаться в stored-XSS для всех читателей;
  2. не разрушить контент — по дампу прод-базы вики style встречается 2756 раз
     (font-size 1822, color 1359, font-family 978), colspan/rowspan по 1913,
     а 35 раскрывающихся блоков держатся на data-атрибутах.

Наивный санитайзер с дефолтным набором прошёл бы первое и провалил второе.
"""

import unittest

from wiki.sanitize import sanitize_html, to_plain_text


class DangerousContentTest(unittest.TestCase):
    def test_script_is_removed(self):
        out = sanitize_html('<p>текст</p><script>alert(1)</script>')
        self.assertNotIn('script', out.lower())
        self.assertIn('текст', out)

    def test_event_handlers_are_removed(self):
        out = sanitize_html('<p onclick="alert(1)" onerror="x()">текст</p>')
        self.assertNotIn('onclick', out.lower())
        self.assertNotIn('onerror', out.lower())

    def test_javascript_href_is_removed(self):
        out = sanitize_html('<a href="javascript:alert(1)">клик</a>')
        self.assertNotIn('javascript', out.lower())

    def test_data_html_in_href_is_removed(self):
        """data:text/html в ссылке — исполняемая страница."""
        out = sanitize_html('<a href="data:text/html;base64,PHNjcmlwdD4=">клик</a>')
        self.assertNotIn('data:text/html', out.lower())

    def test_iframe_and_object_are_removed(self):
        out = sanitize_html('<iframe src="//evil"></iframe><object data="x"></object>')
        self.assertNotIn('iframe', out.lower())
        self.assertNotIn('object', out.lower())

    def test_css_expression_is_dropped(self):
        out = sanitize_html('<p style="width: expression(alert(1))">т</p>')
        self.assertNotIn('expression', out.lower())

    def test_css_url_is_dropped(self):
        out = sanitize_html('<p style="background-color: url(javascript:alert(1))">т</p>')
        self.assertNotIn('javascript', out.lower())

    def test_position_fixed_is_dropped(self):
        """Иначе правкой статьи можно накрыть собой интерфейс портала."""
        out = sanitize_html('<div style="position: fixed; top: 0; z-index: 9999">т</div>')
        self.assertNotIn('position', out.lower())
        self.assertNotIn('z-index', out.lower())

    def test_layout_breaking_classes_are_dropped(self):
        out = sanitize_html('<div class="fixed inset-0 z-[9999] text-lg">т</div>')
        self.assertNotIn('fixed', out)
        self.assertIn('text-lg', out, 'обычные классы оформления должны остаться')

    def test_external_link_gets_rel(self):
        out = sanitize_html('<a href="https://example.com" target="_blank">x</a>')
        self.assertIn('noopener', out)

    def test_comments_are_stripped(self):
        out = sanitize_html('<p>т</p><!-- секрет -->')
        self.assertNotIn('секрет', out)


class ContentPreservationTest(unittest.TestCase):
    """Самое частое в реальном контенте обязано пережить санитизацию."""

    def test_typography_styles_survive(self):
        html = ('<span style="font-size: 14pt; color: rgb(17,17,17); '
                'font-family: Arial; background-color: #ff0">текст</span>')
        out = sanitize_html(html)
        for prop in ('font-size', 'color', 'font-family', 'background-color'):
            self.assertIn(prop, out, prop)

    def test_table_attributes_survive(self):
        html = ('<table><colgroup><col style="width: 120px"></colgroup><tbody>'
                '<tr><td colspan="2" rowspan="3">я</td><th colwidth="80">з</th></tr>'
                '</tbody></table>')
        out = sanitize_html(html)
        for token in ('colspan', 'rowspan', 'colwidth', 'colgroup', 'tbody'):
            self.assertIn(token, out, token)

    def test_collapsible_block_survives(self):
        """35 раскрывающихся блоков в контенте держатся на этих атрибутах."""
        html = ('<details data-wiki-collapsible="1" data-title="Блок" '
                'data-default-open="false" data-allow-multiple="true" '
                'data-required-for-ack="true" open>'
                '<summary>Заголовок</summary><p>тело</p></details>')
        out = sanitize_html(html)
        for token in ('data-wiki-collapsible', 'data-title', 'data-default-open',
                      'data-allow-multiple', 'data-required-for-ack',
                      '<details', '<summary'):
            self.assertIn(token, out, token)

    def test_mark_color_survives(self):
        out = sanitize_html('<mark data-color="yellow" style="background-color: #ff0">в</mark>')
        self.assertIn('data-color', out)
        self.assertIn('<mark', out)

    def test_base64_image_survives(self):
        out = sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=" alt="к">')
        self.assertIn('data:image/png', out)

    def test_non_image_data_src_is_dropped(self):
        out = sanitize_html('<img src="data:text/html;base64,PHNjcmlwdD4=">')
        self.assertNotIn('data:text/html', out)

    def test_headings_and_lists_survive(self):
        html = '<h1>З</h1><h2>П</h2><ul><li>раз</li></ul><ol><li>два</li></ol><blockquote>ц</blockquote>'
        out = sanitize_html(html)
        for token in ('<h1', '<h2', '<ul', '<ol', '<li', '<blockquote'):
            self.assertIn(token, out, token)

    def test_empty_input(self):
        self.assertEqual(sanitize_html(''), '')
        self.assertEqual(sanitize_html(None), '')


class PlainTextTest(unittest.TestCase):
    def test_tags_removed(self):
        self.assertEqual(to_plain_text('<p>Привет <b>мир</b></p>'), 'Привет мир')

    def test_base64_images_excluded(self):
        """В проде вики base64-картинки дают 81 % объёма контента —
        тащить их в поисковый индекс бессмысленно."""
        html = '<p>текст</p><img src="data:image/png;base64,' + 'A' * 5000 + '">'
        out = to_plain_text(html)
        self.assertLess(len(out), 100)
        self.assertIn('текст', out)

    def test_entities_decoded(self):
        self.assertEqual(to_plain_text('<p>&quot;а&quot; &amp; &lt;б&gt;</p>'), '"а" & <б>')

    def test_limit(self):
        self.assertEqual(len(to_plain_text('<p>' + 'я' * 500 + '</p>', limit=100)), 100)


if __name__ == '__main__':
    unittest.main()
