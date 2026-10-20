"""Тема «Роза» «Чатов ОП»: тема на всё окно, слой собран скриптом.

Слой обязан действовать только внутри окна раздела (и его меню), покрывать
каждую цветовую утилиту окна — иначе новая кнопка останется серым пятном в
розовом окне, — не трогать смысловые цвета, не ухудшать контраст текста и
грузиться только у тех, кто тему выбрал.
"""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAZZUP = ROOT / 'src' / 'components' / 'wazzup'
LAYER = WAZZUP / 'chatThemeRose.css'
MOSAIC = WAZZUP / 'chatThemeRose.svg'
THEMES_JS = WAZZUP / 'chatThemes.js'
THEMES_CSS = WAZZUP / 'chatThemes.css'
BUILDER = ROOT / 'scripts' / 'build_chat_rose_theme.py'


def builder():
    spec = importlib.util.spec_from_file_location('build_chat_rose_theme', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selectors(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'@media[^{]+\{', '', css)
    out = []
    for group in re.findall(r'([^{}]+)\{[^{}]*\}', css):
        out.extend(' '.join(part.split()) for part in builder_split(group))
    return [s for s in out if s]


def builder_split(group):
    """Запятые верхнего уровня: :is(a, b) не режем."""
    parts, depth, current = [], 0, []
    for char in group:
        depth += char in '(['
        depth -= char in ')]'
        if char == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(char)
    parts.append(''.join(current))
    return parts


def luminance(hex_color):
    channels = []
    for i in (1, 3, 5):
        c = int(hex_color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class ChatRoseThemeTests(unittest.TestCase):
    def test_every_rule_stays_inside_the_rose_window(self):
        scope = builder().SCOPE
        self.assertIn('.wz-workspace[data-chat-theme="rose"]', scope)
        self.assertIn('body[data-wz-chat-rose] > [role="menu"]', scope)
        allowed = (scope + ' ',
                   "html:not([data-otp-theme='dark']) .wz-workspace[data-chat-theme='rose']",
                   "html:not([data-otp-theme='dark']) body[data-wz-chat-rose] > [role='menu']")
        outside = [s for s in selectors(LAYER.read_text(encoding='utf-8')) if not s.startswith(allowed)]
        self.assertEqual(outside[:5], [], 'Правила «Розы» вне её окна')

    def test_layer_is_current_with_the_window_markup(self):
        """Новая цветовая утилита в окне без пересборки осталась бы серой в «Розе»."""
        css, _ = builder().build()
        self.assertEqual(LAYER.read_text(encoding='utf-8'), css,
                         'Слой «Розы» устарел: прогоните python3 scripts/build_chat_rose_theme.py')

    def test_layer_covers_the_window_surfaces_and_accents(self):
        css = LAYER.read_text(encoding='utf-8')
        for name in ('.bg-white ', '.bg-slate-100 ', '.text-slate-900 ', '.text-slate-500 ',
                     '.border-slate-200 ', '.hover\\:bg-slate-100:hover', '.bg-blue-600 ', '.text-blue-600 '):
            self.assertIn(name, css, 'В слое «Розы» нет %s' % name)

    def test_blue_accent_becomes_rose(self):
        rose = builder()
        css = LAYER.read_text(encoding='utf-8')
        for prop, color, value in (('bg', 'blue-600', 'background-color: %s' % rose.ACCENT['600']),
                                   ('text', 'blue-600', 'color: %s' % rose.ACCENT_TEXT['600']),
                                   ('ring', 'blue-500', '--tw-ring-color: %s' % rose.ACCENT['500'])):
            self.assertEqual(rose.declaration(prop, color, None), value)
            self.assertIn('.%s-%s { %s !important; }' % (prop, color, value), css)

    def test_meaningful_colours_stay_as_they_are(self):
        """Ждут ответа, статус смены, ошибка, заметка, «прочитано» — цвет здесь смысл."""
        css = LAYER.read_text(encoding='utf-8')
        for name in ('.bg-orange-600', '.text-orange-700', '.bg-emerald-500', '.text-rose-600',
                     '.text-amber-700', '.text-sky-500', '.bg-\\[\\#faf7ef\\]'):
            self.assertNotIn(name, css, '«Роза» перекрасила смысловой цвет %s' % name)

    def test_text_reads_no_worse_than_in_the_standard_theme(self):
        rose = builder()
        card = rose.BG_NEUTRAL['white']
        pairs = (
            ('основной текст', rose.TEXT_NEUTRAL['900'], card, 4.5),
            ('вторичный текст', rose.TEXT_NEUTRAL['500'], card, 4.76),
            ('вторичный текст на выбранной строке', rose.TEXT_NEUTRAL['500'], rose.BG_NEUTRAL['100'], 4.34),
            ('ссылка', rose.ACCENT_TEXT['600'], card, 4.5),
            ('белый на кнопке', '#ffffff', rose.ACCENT['600'], 4.5),
        )
        for name, ink, ground, floor in pairs:
            self.assertGreaterEqual(contrast(ink, ground), floor, name)

    def test_layer_is_loaded_only_on_demand(self):
        self.assertIn("import('./chatThemeRose.css')", THEMES_JS.read_text(encoding='utf-8'))
        for path in (ROOT / 'src').rglob('*.js*'):
            if path.suffix in ('.js', '.jsx'):
                self.assertNotRegex(path.read_text(encoding='utf-8-sig'),
                                    r"^\s*import\s+['\"].*chatThemeRose\.css['\"]",
                                    'Статический import «Розы» в %s' % path.relative_to(ROOT))

    def test_mosaic_travels_only_with_the_layer(self):
        """Мозаика нужна одной теме — в общем файле тем её байтов нет."""
        self.assertIn("url('./chatThemeRose.svg')", LAYER.read_text(encoding='utf-8'))
        self.assertNotIn('chatThemeRose.svg', THEMES_CSS.read_text(encoding='utf-8'))
        svg = MOSAIC.read_text(encoding='utf-8')
        self.assertLess(len(svg.encode('utf-8')), 4096)
        self.assertNotRegex(svg, r'(?i)<script|on\w+=|href="(?!#)')


if __name__ == '__main__':
    unittest.main()
