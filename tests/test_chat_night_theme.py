"""Тема «Ночь» «Чатов ОП»: слой собран скриптом из палитры тёмного режима портала.

Слой обязан действовать только внутри окна раздела (и его меню), покрывать
каждую цветовую утилиту окна — иначе новая кнопка останется светлым пятном
в тёмном окне — и грузиться только у выбравших «Ночь».
"""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYER = ROOT / 'src' / 'components' / 'wazzup' / 'chatThemeNight.css'
THEMES_JS = ROOT / 'src' / 'components' / 'wazzup' / 'chatThemes.js'
BUILDER = ROOT / 'scripts' / 'build_chat_night_theme.py'


def builder():
    spec = importlib.util.spec_from_file_location('build_chat_night_theme', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selectors(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'@media[^{]+\{', '', css)
    return [' '.join(s.split()) for s in re.findall(r'([^{}]+)\{[^{}]*\}', css) if s.strip()]


class ChatNightThemeTests(unittest.TestCase):
    def test_every_rule_stays_inside_the_chat_window(self):
        scope = builder().SCOPE
        outside = [s for s in selectors(LAYER.read_text(encoding='utf-8')) if not s.startswith(scope + ' ')]
        self.assertEqual(outside[:5], [], 'Правила «Ночи» вне окна чатов')

    def test_layer_is_current_with_the_window_markup(self):
        """Новая цветовая утилита в окне без пересборки осталась бы светлой в «Ночи»."""
        css, _ = builder().build()
        self.assertEqual(LAYER.read_text(encoding='utf-8'), css,
                         'Слой «Ночи» устарел: прогоните python3 scripts/build_chat_night_theme.py')

    def test_layer_covers_the_window_surfaces(self):
        css = LAYER.read_text(encoding='utf-8')
        for name in ('.bg-white ', '.bg-slate-100 ', '.text-slate-900 ', '.text-slate-500 ',
                     '.border-slate-200 ', '.hover\\:bg-slate-100:hover', '.bg-\\[\\#f2f2f7\\]'):
            self.assertIn(name, css, 'В слое «Ночи» нет %s' % name)

    def test_layer_is_loaded_only_on_demand(self):
        self.assertIn("import('./chatThemeNight.css')", THEMES_JS.read_text(encoding='utf-8'))
        for path in (ROOT / 'src').rglob('*.js*'):
            if path.suffix in ('.js', '.jsx'):
                self.assertNotRegex(path.read_text(encoding='utf-8-sig'),
                                    r"^\s*import\s+['\"].*chatThemeNight\.css['\"]",
                                    'Статический import «Ночи» в %s' % path.relative_to(ROOT))


if __name__ == '__main__':
    unittest.main()
