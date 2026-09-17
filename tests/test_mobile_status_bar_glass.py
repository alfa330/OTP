# -*- coding: utf-8 -*-
"""Страж верха экрана на телефоне: полоса с часами — место стекла iOS 26.

Владелец 17.09.2026 прислал снимок вики из установленного портала (под часами
размытая страница) и попросил, «чтобы ничего не мешало iOS 26», во всех разделах.

Стекло рисует система, а гасит его WebKit по своему правилу
(LocalFrameView::fixedContainerEdges): в точке посередине экрана, в 4 px от
верхнего края, ищется position: fixed или sticky шириной от 90% экрана, и если
такой нашёлся, полоса заливается сплошным цветом (его фоном, а при
backdrop-filter — цветом страницы). Найденный элемент держит заливку, пока он в
дереве и visibility: visible. На компьютере этого не увидеть: оболочка
выключена, выреза нет, а Chrome полосу состояния не рисует вовсе.

Замер на стенде (393×852, вырез 59 px, обход всех разделов) нашёл у края
липкие шапки «Отделов», «SIP-настроек», «Технических проблем», шапку «Курсов»,
имя в шапке «Профиля» и стекло полосы дней аукциона. Кроме заливки, шапки
уезжали под часы: заголовок раздела читался сквозь время и заряд.

Решения:
  * липнущее к верху СТРАНИЦЫ на телефоне липнет ниже выреза
    (mobile-sticky-top);
  * панели фильтров выше пары строк (260–410 px на телефоне) не липнут вовсе
    (mobile-sticky-off): ниже выреза они закрывали бы полэкрана;
  * имя в шапке «Профиля» и стекло полосы дней аукциона начинаются под вырезом.

Таблицы со своей прокруткой (max-h + overflow-auto) метки не получают: они
липнут к своему блоку, а не к экрану.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SHELL_CSS = (ROOT / 'src' / 'components' / 'common' / 'mobile-shell.css').read_text(encoding='utf-8')
AUCTION_CSS = (ROOT / 'src' / 'components' / 'resources' / 'shift-auction-mobile.css').read_text(encoding='utf-8')


def rule_body(source, selector):
    """Тело правила по точному селектору (селектор с начала строки)."""
    match = re.search(r'(?m)^' + re.escape(selector) + r' \{\n(.*?)\n\}', source, re.S)
    if not match:
        raise AssertionError(f'нет правила {selector!r}')
    return match.group(1)


# Липкие шапки у верха страницы: файл, кусок их классов, метка.
MARKED = (
    ('src/components/departments/DepartmentsView.jsx', 'sticky top-0 z-10 -mx-1 rounded-2xl', 'mobile-sticky-top'),
    ('src/components/sip/SipSettingsView.jsx', 'sticky top-0 z-10 -mx-1 rounded-2xl', 'mobile-sticky-off'),
    ('src/components/technical/TechnicalIssuesView.jsx', 'sticky top-0 z-10 rounded-xl border border-blue-200', 'mobile-sticky-off'),
    ('src/components/lms/LmsView.jsx', 'fixed top-0 right-0 z-40 bg-white', 'mobile-sticky-top'),
    ('src/components/lms/LmsView.jsx', 'xl:col-span-5 flex flex-col gap-6 sticky top-6', 'mobile-sticky-off'),
    ('src/components/olx/OlxAdsView.jsx', 'sticky top-2 z-20', 'mobile-sticky-top'),
    ('src/components/wiki/WikiEditor.jsx', 'sticky top-0 z-20 flex flex-wrap items-center gap-0.5', 'mobile-sticky-top'),
    ('src/components/wiki/WikiAudit.jsx', 'sticky top-0 z-10 border-b border-slate-100 bg-white/90', 'mobile-sticky-top'),
    ('src/components/driver_mailings/MailingJournal.jsx', 'sticky top-0 z-10 border-b border-slate-100 bg-white/90', 'mobile-sticky-top'),
    ('src/components/surveys/SurveysView.jsx', 'sticky top-0 z-10 -mx-1 space-y-2 rounded-xl', 'mobile-sticky-top'),
    ('src/components/call_qa/CallReviewCard.jsx', 'sticky top-0 z-10 mb-2 rounded-2xl', 'mobile-sticky-top'),
    ('src/App.jsx', 'bg-white border-b border-gray-200 shadow-sm sticky top-0 z-10', 'mobile-sticky-top'),
)


class ShellRuleTests(unittest.TestCase):
    """Две метки и их правила живут в оболочке и только на телефоне."""

    def test_sticky_top_waits_below_the_notch(self):
        self.assertIn('top: env(safe-area-inset-top);', rule_body(SHELL_CSS, 'body.mobile-shell .mobile-sticky-top'))
        # Полоса выбора объявлений держит свой зазор от края и под вырезом.
        self.assertIn(
            'top: calc(env(safe-area-inset-top) + 0.5rem);',
            rule_body(SHELL_CSS, 'body.mobile-shell .mobile-sticky-top.top-2'),
        )

    def test_tall_panels_do_not_stick_on_the_phone(self):
        self.assertIn('position: relative;', rule_body(SHELL_CSS, 'body.mobile-shell .mobile-sticky-off'))

    def test_marks_do_nothing_on_the_desktop(self):
        """На компьютере шапки липнут, как липли: правила меток есть только
        под body.mobile-shell."""
        for line in SHELL_CSS.splitlines():
            if re.search(r'\.mobile-sticky-(top|off)\b', line) and line.rstrip().endswith('{'):
                self.assertTrue(line.startswith('body.mobile-shell '), line)


class MarkedHeadersTests(unittest.TestCase):
    """Каждая липкая шапка, найденная у верхней грани, несёт свою метку."""

    def test_every_page_level_header_is_marked(self):
        for path, snippet, mark in MARKED:
            source = (ROOT / path).read_text(encoding='utf-8')
            lines = [line for line in source.splitlines() if snippet in line]
            self.assertTrue(lines, f'{path}: шапка «{snippet}» не найдена — сверить метку заново')
            for line in lines:
                self.assertIn(mark, line, f'{path}: у шапки «{snippet}» нет метки {mark}')


class ScrollTitleTests(unittest.TestCase):
    """Имя в шапке «Профиля» — fixed во всю ширину. Прибитое к нулю, оно гасило
    стекло над часами и, стоило раз прокрутить, держало заливку до ухода из
    раздела."""

    def test_title_starts_below_the_notch(self):
        body = rule_body(SHELL_CSS, '.mobile-scroll-title')
        self.assertIn('top: env(safe-area-inset-top);', body)
        self.assertNotIn('top: 0;', body)
        # Строка имени стоит там же, где стояла: высота и поле уменьшены на вырез.
        self.assertIn('height: calc(46px + max(10px, env(safe-area-inset-top)) - env(safe-area-inset-top));', body)
        self.assertIn('padding: calc(max(10px, env(safe-area-inset-top)) - env(safe-area-inset-top)) 62px 0 16px;', body)


class SheetGlassTests(unittest.TestCase):
    """Шторка разделов («Ещё»). Владелец 17.09.2026: «при входе в раздел, где
    можно просмотреть другие разделы, полоска не идёт как стекло».

    Мешали трое, все по тому же правилу WebKit: сам лист (fixed во весь экран
    со своим фоном), body, прибитый замком прокрутки (на коротком разделе
    ростом ровно с экран), и липкая полоса с именем, прилипавшая к нулю. Слой
    выше окна больше чем на 5% WebKit панелью не считает — лист и body
    вытянуты за нижнюю грань."""

    LOCK = (ROOT / 'src' / 'utils' / 'pageScrollLock.js').read_text(encoding='utf-8')
    TITLE = (ROOT / 'src' / 'components' / 'common' / 'MobileSheetTitle.jsx').read_text(encoding='utf-8')

    def test_sheet_is_taller_than_the_screen(self):
        body = rule_body(SHELL_CSS, 'body.mobile-shell .sidebar')
        self.assertIn('--sheet-overhang: 20vh;', body)
        self.assertIn('height: calc(100% + var(--sheet-overhang));', body)
        self.assertIn('bottom: auto;', body)
        # Приближение — от верхней кромки: от середины лист отходил от верхней
        # грани и открывал там затемнение (серая полоса на снимке владельца).
        self.assertIn('transform-origin: 50% 0;', body)

    def test_hidden_dimmer_is_not_visible_to_webkit(self):
        """Покадровый замер 17.09.2026: при каждом открытии WebKit находил
        затемнение под шторкой у верхней грани и держал серую заливку, пока
        элемент видим — а opacity: 0 для этого «видим»."""
        self.assertIn('visibility: hidden;', rule_body(SHELL_CSS, 'body.mobile-shell .sidebar-overlay'))
        self.assertIn('visibility: visible;', rule_body(SHELL_CSS, 'body.mobile-shell .sidebar-overlay.active'))
        motion = (ROOT / 'src' / 'components' / 'common' / 'mobile-motion.css').read_text(encoding='utf-8')
        self.assertIn('visibility 0s linear var(--ios-sheet-out);', rule_body(motion, 'body.mobile-shell .sidebar-overlay'))

    def test_last_row_stays_above_the_bar(self):
        """Лишнее уходит за нижнюю грань — нижнее поле списка растёт на столько
        же во всех трёх положениях бара, иначе «Выйти» уезжает под край."""
        self.assertIn('+ var(--sheet-overhang));', rule_body(SHELL_CSS, 'body.mobile-shell .sidebar > div'))
        for side in ('right', 'left'):
            self.assertIn(
                'padding-bottom: calc(max(8px, env(safe-area-inset-bottom)) + var(--sheet-overhang));',
                rule_body(SHELL_CSS, f'body[data-tabbar-side="{side}"].mobile-shell .sidebar > div'),
            )

    def test_locked_body_is_taller_than_the_screen(self):
        self.assertIn("style.minHeight = '120vh';", self.LOCK)
        self.assertIn('minHeight: style.minHeight,', self.LOCK)
        self.assertIn('style.minHeight = savedStyle.minHeight;', self.LOCK)

    def test_name_bar_is_pinned_to_the_sheet_not_sticky(self):
        """Липкая шапка во всю ширину у верхней грани — тот же fixed/sticky, что
        гасит стекло. Абсолютную от листа правило WebKit не видит, а стоит она
        так же неподвижно: отсчёт от .sidebar, не от прокручиваемого блока."""
        body = rule_body(SHELL_CSS, 'body.mobile-shell .mobile-sheet-topbar')
        self.assertIn('position: absolute;', body)
        self.assertIn('top: 0;', body)
        self.assertIn('height: calc(max(10px, env(safe-area-inset-top)) + 44px);', body)
        # Из потока шапка ушла — портрет стоит на прежнем месте.
        self.assertIn('margin-top: 46px;', rule_body(SHELL_CSS, 'body.mobile-shell .mobile-sheet-topbar + .mobile-sheet-account'))
        # Стекло — отдельным слоем (как у полосы дней аукциона) и по доле пути.
        glass = rule_body(SHELL_CSS, 'body.mobile-shell .mobile-sheet-topbar__glass')
        self.assertIn('opacity: var(--sheet-glass, 0);', glass)
        self.assertIn('backdrop-filter: saturate(180%) blur(22px);', glass)
        self.assertNotIn('backdrop-filter', body)
        self.assertIn('<span className="mobile-sheet-topbar__glass" aria-hidden="true" />', self.TITLE)


class AuctionStripGlassTests(unittest.TestCase):
    """Стекло прилипшей полосы дней продолжается вверх только до выреза: на 86%
    непрозрачности оно делало полосу с часами сплошной."""

    def test_glass_stops_at_the_notch(self):
        for selector in (
            'body.mobile-shell .sa-m-root .sa-m-strip[data-stuck] .sa-m-strip__glass',
            'body.mobile-shell .sa-m-root .sa-m-strip.sa-m-strip--bell[data-stuck] .sa-m-strip__glass',
        ):
            body = rule_body(AUCTION_CSS, selector)
            self.assertRegex(body, r'top: calc\(env\(safe-area-inset-top\) - ', selector)
        self.assertNotIn('top: calc(-1 * ', AUCTION_CSS)


if __name__ == '__main__':
    unittest.main()
