"""Cherry remains scoped, legible and sharp without changing chat typography."""
import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WAZZUP = ROOT / 'src' / 'components' / 'wazzup'
LAYER = WAZZUP / 'chatThemeCherry.css'
WALLPAPER = WAZZUP / 'chatThemeCherry.svg'
THEMES_JS = WAZZUP / 'chatThemes.js'
BUILDER = ROOT / 'scripts' / 'build_chat_cherry_theme.py'


def builder():
    spec = importlib.util.spec_from_file_location('build_chat_cherry_theme', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selectors(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'@media[^{]+\{', '', css)
    for group in re.findall(r'([^{}]+)\{[^{}]*\}', css):
        depth, current = 0, []
        for char in group + ',':
            depth += char in '(['
            depth -= char in ')]'
            if char == ',' and depth == 0:
                selector = ' '.join(''.join(current).split())
                if selector:
                    yield selector.replace("'", '"')
                current = []
            else:
                current.append(char)


def contrast(first, second):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def theme_colors():
    source = THEMES_JS.read_text(encoding='utf-8')
    block = re.search(r"id:\s*'cherry'(?P<theme>.*?)\n\s*\},", source, re.S)
    if block is None:
        raise AssertionError('Cherry is missing from the theme registry')
    return dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", block['theme']))


class ChatCherryThemeTests(unittest.TestCase):
    def test_rules_stay_in_the_cherry_window_and_its_portal_menus(self):
        scope = builder().SCOPE.replace("'", '"')
        self.assertIn('.wz-workspace[data-chat-theme="cherry"]', scope)
        self.assertIn('body[data-wz-chat-cherry] > [role="menu"]', scope)
        allowed = (
            scope + ' ',
            'html:not([data-otp-theme="dark"]) .wz-workspace[data-chat-theme="cherry"]',
            'html:not([data-otp-theme="dark"]) body[data-wz-chat-cherry] > [role="menu"]',
        )
        for selector in selectors(LAYER.read_text(encoding='utf-8')):
            self.assertTrue(selector.startswith(allowed), 'Unscoped Cherry rule: ' + selector)
            self.assertTrue(selector.startswith('html:not([data-otp-theme="dark"]) '),
                            'Cherry must yield to the portal dark theme: ' + selector)

    def test_generated_layer_covers_the_current_window(self):
        css, _ = builder().build()
        self.assertEqual(LAYER.read_text(encoding='utf-8'), css,
                         'Run python scripts/build_chat_cherry_theme.py after changing chat colors')
        for name in ('.bg-white ', '.bg-slate-100 ', '.text-slate-900 ', '.text-slate-500 ',
                     '.border-slate-200 ', '.hover\\:bg-slate-100:hover', '.bg-blue-600 '):
            self.assertIn(name, css)

    def test_statuses_notes_and_read_receipts_keep_their_semantic_colors(self):
        cherry = builder()
        css = LAYER.read_text(encoding='utf-8')
        for color in ('orange-600', 'emerald-500', 'rose-600', 'amber-700', 'sky-500'):
            for prop in ('bg', 'text', 'border', 'ring'):
                self.assertIsNone(cherry.declaration(prop, color, None), (prop, color))
        for name in ('.bg-orange-600', '.text-orange-700', '.bg-emerald-500', '.text-rose-600',
                     '.text-amber-700', '.text-sky-500', '.bg-\\[\\#faf7ef\\]'):
            self.assertNotIn(name, css)

    def test_text_and_message_metadata_remain_legible(self):
        cherry = builder()
        colors = theme_colors()
        pairs = [
            ('primary text', cherry.TEXT_NEUTRAL['900'], cherry.BG_NEUTRAL['white'], 4.5),
            ('secondary text', cherry.TEXT_NEUTRAL['500'], cherry.BG_NEUTRAL['white'], 4.5),
            ('selected-row text', cherry.TEXT_NEUTRAL['500'], cherry.BG_NEUTRAL['100'], 4.5),
            ('link', cherry.ACCENT_TEXT['600'], cherry.BG_NEUTRAL['white'], 4.5),
            ('button label', '#ffffff', cherry.ACCENT['600'], 4.5),
        ]
        for bubble in ('incoming', 'outgoing'):
            pairs.extend([
                (bubble + ' text', cherry.TEXT_NEUTRAL['900'], colors[bubble], 4.5),
                (bubble + ' secondary text', cherry.TEXT_NEUTRAL['500'], colors[bubble], 4.5),
                (bubble + ' link', cherry.ACCENT_TEXT['600'], colors[bubble], 4.5),
                (bubble + ' metadata', colors['meta'], colors[bubble], 4.5),
                (bubble + ' read receipt', colors['read'], colors[bubble], 3),
            ])
        for label, ink, ground, minimum in pairs:
            self.assertGreaterEqual(contrast(ink, ground), minimum, label)

    def test_typography_is_unchanged_and_wallpaper_has_no_blur(self):
        css = re.sub(r'/\*.*?\*/', '', LAYER.read_text(encoding='utf-8'), flags=re.S)
        self.assertNotRegex(css, r'(?i)@font-face|(?:^|[;{])\s*font(?:-[\w-]+)?\s*:')
        wallpaper_rules = [body for body in re.findall(r'\{([^{}]*)\}', css)
                           if 'chatThemeCherry.svg' in body]
        self.assertTrue(wallpaper_rules, 'The pixel wallpaper must be in the theme layer')
        for body in wallpaper_rules:
            self.assertNotRegex(body, r'(?i)blur\s*\(')
        svg = WALLPAPER.read_text(encoding='utf-8')
        self.assertIn('shape-rendering="crispEdges"', svg)
        self.assertNotRegex(svg, r'(?i)<filter\b|<feGaussianBlur\b|<image\b')
        self.assertNotRegex(svg, r'(?i)<script\b|\bon\w+\s*=|href=["\'](?!#)')

    def test_layer_and_wallpaper_load_only_with_the_selected_theme(self):
        self.assertIn("import('./chatThemeCherry.css')", THEMES_JS.read_text(encoding='utf-8'))
        for path in (ROOT / 'src').rglob('*'):
            if path.suffix in ('.js', '.jsx'):
                self.assertNotRegex(path.read_text(encoding='utf-8-sig'),
                                    r'(?m)^\s*import\s+[\'"](?:[^\'"\n]*/)?chatThemeCherry\.css[\'"]',
                                    str(path.relative_to(ROOT)))
        self.assertNotIn('chatThemeCherry.svg', (WAZZUP / 'chatThemes.css').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
