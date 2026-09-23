# -*- coding: utf-8 -*-
"""Тонкий скролл портала (`.thin-scroll`) и тело окон IosModal.

Ломается молча: Chrome с версии 121 отключает всю ::-webkit-scrollbar-
стилизацию элемента, если у него задано хоть одно из scrollbar-width /
scrollbar-color. Правило `width: 3px` при этом остаётся в файле и выглядит
работающим, а на экране — штатная полоса в ~11 px. Так и было до 23.09.2026:
второй блок `.thin-scroll` с безусловным scrollbar-width стоял выше
канонического, и все места с этим классом были толстыми (замер в Chrome 151 с
постоянными полосами macOS: 11 px вместо 3; тело IosModal без класса — 15 px).
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPORTS = '@supports not selector(::-webkit-scrollbar)'


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


def _without_comments(css):
    return re.sub(r'/\*[\s\S]*?\*/', '', css)


def _supports_ranges(css):
    """Диапазоны блоков @supports для Firefox — внутри них стандартные
    свойства как раз и нужны. По балансу скобок: внутри лежат правила."""
    ranges, at = [], css.find(SUPPORTS)
    while at >= 0:
        depth, index = 0, css.index('{', at)
        while index < len(css):
            if css[index] == '{':
                depth += 1
            elif css[index] == '}':
                depth -= 1
                if depth == 0:
                    break
            index += 1
        ranges.append((at, index))
        at = css.find(SUPPORTS, index)
    return ranges


class ThinScrollTests(unittest.TestCase):

    def test_standard_properties_only_for_firefox(self):
        for name in (('src', 'styles.css'), ('src', 'theme-dark.css')):
            css = _without_comments(_read(*name))
            ranges = _supports_ranges(css)
            for block in re.finditer(r'([^{}]*)\{([^{}]*)\}', css):
                selectors = [part.strip() for part in block.group(1).split(',')]
                if not any(re.fullmatch(r'(html\[[^\]]+\]\s+)?\.(thin|crm)-scroll', part)
                           for part in selectors):
                    continue
                if not re.search(r'scrollbar-(width|color)', block.group(2)):
                    continue
                inside = any(start < block.start() < end for start, end in ranges)
                self.assertTrue(inside, '%s: «%s» задаёт scrollbar-width/color вне @supports — '
                                        'Chrome отключит ::-webkit-scrollbar, и полоса станет '
                                        'штатной' % ('/'.join(name), block.group(1).strip()))

    def test_one_definition_three_pixels(self):
        css = _without_comments(_read('src', 'styles.css'))
        # Один набор правил на класс: два блока с разными цветами и ширинами
        # и привели к тому, что один из них молча выключал другой.
        self.assertEqual(len(re.findall(r'\.thin-scroll::-webkit-scrollbar\s*[,{]', css)), 1)
        rule = css[css.index('.thin-scroll::-webkit-scrollbar {'):]
        rule = rule[:rule.index('}')]
        self.assertIn('width: 3px;', rule)

    def test_every_ios_window_scrolls_thin(self):
        """Тело IosModal — прокрутка каждого окна в стиле iOS («Результаты»
        новости и ещё сотня): системная полоса вдоль скруглённого окна чужая."""
        ios = _read('src', 'components', 'ui', 'ios.jsx')
        modal = ios[ios.index('export const IosModal'):]
        self.assertIn('className="thin-scroll flex-1 overflow-y-auto', modal)

    def test_the_phone_still_draws_no_bars(self):
        """На телефоне полос нет вовсе, и тонкий класс этого не отменяет."""
        shell = _read('src', 'components', 'common', 'mobile-shell.css')
        block = shell[shell.index('body.mobile-shell ::-webkit-scrollbar {'):]
        self.assertIn('width: 0 !important;', block[:block.index('}')])


if __name__ == '__main__':
    unittest.main()
