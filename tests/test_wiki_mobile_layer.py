# -*- coding: utf-8 -*-
"""Страж мобильного слоя вики (src/components/wiki/wiki-mobile.css).

Владелец 14.09.2026: «раздел Вики в мобильной версии — ничего не читабельно,
сделай удобно в стиле iOS; изменения только к мобильной версии». Слой держит
три решения, и каждое ломается молча — на компьютере дефекта не видно вовсе.

  * ТОЛЬКО ТЕЛЕФОН. Каждое правило заперто на body.mobile-shell: без класса
    оболочки ни одно не срабатывает, и настольная вики остаётся прежней.
  * ПОДКЛЮЧЕНИЕ. Слой приезжает @import'ом из wiki-theme.css; @import после
    первого правила браузер отбрасывает целиком — без ошибки.
  * ВЕС. Разметка на утилитах Tailwind, которые идут в бандле позже; а общий
    слой mobile-shell.css местами ставит !important. Своё правило, которое
    проиграло по весу, выглядит сделанным, но не действует.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / 'src' / 'components' / 'wiki'
LAYER = (WIKI / 'wiki-mobile.css').read_text(encoding='utf-8')
THEME = (WIKI / 'wiki-theme.css').read_text(encoding='utf-8')
SHELL = (ROOT / 'src' / 'components' / 'common' / 'mobile-shell.css').read_text(encoding='utf-8')


def rules(css):
    """Пары (селектор, тело) без комментариев."""
    plain = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', plain)]


def top_level_parts(selector):
    """Части списка селекторов: запятые внутри :is()/:not()/:has() не делят."""
    parts, depth, buf = [], 0, ''
    for ch in selector:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(buf)
            buf = ''
        else:
            buf += ch
    parts.append(buf)
    return [' '.join(p.split()) for p in parts if p.strip()]


def specificity(selector):
    """Грубый подсчёт (id, класс, тег) — достаточный для селекторов этих файлов."""
    s = re.sub(r':not\(([^()]*)\)', r' \1', selector)
    s = re.sub(r':has\(([^()]*)\)', r' \1', s)
    classes = len(re.findall(r'\.[\w-]+', s)) + len(re.findall(r'\[[^\]]+\]', s)) \
        + len(re.findall(r'(?<!:):(?!is|not|has|where)[\w-]+', s))
    tags = len(re.findall(r'(?:^|[\s>+~(])([a-z][\w-]*)', s))
    return (0, classes, tags)


class OnlyPhoneTests(unittest.TestCase):

    def test_every_rule_is_locked_to_the_shell(self):
        found = rules(LAYER)
        self.assertGreater(len(found), 40)
        for selector, _ in found:
            for part in top_level_parts(selector):
                self.assertTrue(
                    part.startswith('body.mobile-shell')
                    or part.startswith('html[data-otp-theme="dark"] body.mobile-shell'),
                    f'правило «{part}» действует и на компьютере',
                )

    def test_no_width_media_query(self):
        """Условие «мы на телефоне» живёт одним классом, второй копии в CSS нет."""
        self.assertNotIn('@media', LAYER)


class WiringTests(unittest.TestCase):

    def test_layer_is_imported_before_the_first_rule(self):
        plain = re.sub(r'/\*.*?\*/', '', THEME, flags=re.S)
        first_rule = plain.index('{')
        at = plain.index("@import './wiki-mobile.css';")
        self.assertLess(at, first_rule)

    def test_hooks_are_in_the_markup(self):
        hooks = {
            'WikiArticle.jsx': ('wiki-m-toolbar', 'wiki-m-back', 'wiki-m-actions', 'wiki-m-article',
                                'wiki-m-article-head', 'wiki-m-article-body', 'wiki-m-toc',
                                'wiki-m-article-foot'),
            'WikiHome.jsx': ('wiki-m-stats', 'wiki-m-stat', 'wiki-m-panel', 'wiki-m-panel-list',
                             'wiki-m-mini', 'wiki-m-popular', 'wiki-m-popular-list', 'wiki-m-pop'),
            'WikiLibrary.jsx': ('wiki-m-hero',),
            'WikiCatalog.jsx': ('wiki-m-row',),
            'WikiIndexPanel.jsx': ('wiki-m-index',),
            'WikiParkRail.jsx': ('wiki-m-parks',),
        }
        for name, names in hooks.items():
            source = (WIKI / name).read_text(encoding='utf-8')
            for hook in names:
                self.assertRegex(source, rf'(?<![\w-]){re.escape(hook)}(?![\w-])', f'{name}: {hook}')
                self.assertIn(f'.{hook}', LAYER, f'метка {hook} без правила')

    def test_header_selector_matches_the_markup(self):
        """Шапка отбирается по месту (метки в WikiView.jsx нет): это единственный
        <header> прямо в колонке раздела, а поле поиска — кнопка-заглушка листа."""
        view = (WIKI / 'WikiView.jsx').read_text(encoding='utf-8')
        self.assertEqual(view.count('<header '), 1)
        search = (WIKI / 'WikiSearch.jsx').read_text(encoding='utf-8')
        self.assertIn('aria-haspopup="dialog"', search)


class WeightTests(unittest.TestCase):

    def test_font_steps_match_whole_tokens(self):
        """`~=` — целая лексема: `sm:text-[13px]` на телефоне не действует."""
        self.assertIn('[class~="text-[10.5px]"]', LAYER)
        self.assertNotIn('[class*="text-[', LAYER)

    def test_article_card_beats_the_shell_max_width(self):
        """Общий слой ставит `max-width: 100% !important` всему с подстрокой `w-[`
        — она есть в shadow-[…] карточки статьи. Карточка оставалась 358 px, и
        справа шла серая полоса. Между двумя !important решает вес."""
        shell = next(s for s, body in rules(SHELL)
                     if 'max-width: 100% !important' in body and '[class*="w-["]' in s)
        shell_part = next(p for p in top_level_parts(shell) if '[class*="w-["]' in p)
        mine = next(s for s, body in rules(LAYER) if 'max-width: none !important' in body)
        self.assertIn('.wiki-m-article', mine)
        self.assertGreater(specificity(mine), specificity(shell_part))

    def test_rows_undo_the_shell_column_rule(self):
        """В каталоге строка статьи распадалась в столбик — иконка над заголовком,
        меню отдельной строкой: общий слой ставит колонкой любой items-start."""
        shell = next(s for s, body in rules(SHELL) if 'flex-direction: column' in body and ':has(> .flex-1)' in s)
        mine = next(s for s, body in rules(LAYER) if 'flex-direction: row' in body)
        self.assertGreater(specificity(top_level_parts(mine)[0]), specificity(top_level_parts(shell)[0]))


if __name__ == '__main__':
    unittest.main()
