# -*- coding: utf-8 -*-
"""Страж тёмного режима.

Режим личный: он выдан поимённо одному аккаунту и должен таким остаться.
Проверяем не «красиво ли», а четыре границы, за которыми правка перестаёт
быть личной настройкой и становится изменением портала для всех:

  * слой заскоуплен на атрибут — светлая тема не может пострадать;
  * слой не попадает в общий бандл (только динамический import);
  * право проверяется по логину, а не по сохранённому в браузере выбору;
  * экраны тренажёров вики слой не трогает — их палитра снята со скриншотов
    чужих приложений, и темнить её нельзя.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
THEME_CSS = ROOT / "src" / "theme-dark.css"
THEME_UTIL = ROOT / "src" / "utils" / "darkTheme.js"
APP_JSX = ROOT / "src" / "App.jsx"
BUILDER = ROOT / "scripts" / "build_dark_theme.py"

SCOPE = 'html[data-otp-theme="dark"]'
DARK_THEME_STORAGE_KEY = 'otp.theme'


def read(path):
    return path.read_text(encoding="utf-8-sig")


def split_selectors(selector):
    """Разрез по запятым ВЕРХНЕГО уровня.

    Наивный split(',') рвёт `:is(code, mark)` пополам, и страж сообщал бы о
    несуществующих правилах без scope — на огрызок `mark)` он и ругался."""
    parts, depth, current = [], 0, []
    for char in selector:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


class DarkThemeLayerTests(unittest.TestCase):
    def test_every_rule_is_scoped_to_the_attribute(self):
        """Ни одно правило слоя не должно действовать без атрибута темы.

        Иначе тёмный режим одного аккаунта перекрасил бы портал всем.
        """
        css = read(THEME_CSS)
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        css = re.sub(r"@media[^{]+\{", "", css)          # медиаблоки разворачиваем
        unscoped = []
        for selector in re.findall(r"([^{}]+)\{[^{}]*\}", css):
            selector = " ".join(selector.split())
            if not selector:
                continue
            for part in split_selectors(selector):
                if not part.startswith(SCOPE):
                    unscoped.append(part)
        self.assertEqual([], unscoped[:10],
                         "Правила слоя без scope темы: %s" % unscoped[:10])

    def test_layer_never_touches_wiki_trainers(self):
        """Экраны тренажёров повторяют чужие приложения по скриншотам."""
        css = read(THEME_CSS)
        self.assertNotIn(".wt-root", css)
        self.assertNotIn(".wt-overlay", css)

    def test_layer_covers_the_structural_utilities(self):
        """Обрезанный или несобранный слой — это половина портала в темноте."""
        css = read(THEME_CSS)
        for selector in (
            "%s .bg-white " % SCOPE,
            "%s .bg-slate-50 " % SCOPE,
            "%s .bg-gray-50 " % SCOPE,
            "%s .text-slate-900 " % SCOPE,
            "%s .border-slate-200 " % SCOPE,
            "%s .tv-root " % SCOPE,
            "%s .wiki-scope " % SCOPE,
            "%s .msv-card " % SCOPE,        # «Мониторинговая шкала»
            "%s .ce-switch-track " % SCOPE, # «Журнал оценок» в iframe
        ):
            self.assertIn(selector, css, "В слое нет правила для %s" % selector)

    def test_layer_is_loaded_only_on_demand(self):
        """Слой весит десятки килобайт и нужен одному аккаунту.

        Статический import утянул бы его в бандл всем — режим обязан
        подгружаться динамически.
        """
        util = read(THEME_UTIL)
        self.assertIn("import('../theme-dark.css')", util)
        for path in (ROOT / "src").rglob("*.js*"):
            if path.suffix not in (".js", ".jsx"):
                continue
            source = read(path)
            self.assertNotRegex(
                source, r"^\s*import\s+['\"].*theme-dark\.css['\"]",
                "Статический import слоя в %s" % path.relative_to(ROOT))

    def test_builder_stays_the_source_of_truth(self):
        """Слой собран скриптом — рукописная правка в нём потеряется."""
        self.assertTrue(BUILDER.exists())
        self.assertIn("СОБРАН СКРИПТОМ", read(THEME_CSS)[:400])


class DarkThemeEmbeddedBundleTests(unittest.TestCase):
    """«Журнал оценок» — отдельная сборка в iframe, свой документ.

    Слой темы туда не достаёт сам по себе, а решение о теме принимает портал:
    он один знает, кому режим выдан. Проверяем обе половины связки — без любой
    из них раздел остаётся светлым островом внутри тёмного портала.
    """

    def test_parent_passes_theme_into_the_frame(self):
        app = read(APP_JSX)
        self.assertIn("call_evaluation.html${darkThemeActive ? '?theme=dark' : ''}", app)
        # Полотно вокруг рамки задано инлайновым стилем — CSS его не достанет.
        self.assertIn("const callEvaluationCanvas = darkThemeActive ?", app)

    def test_frame_takes_theme_only_from_the_address(self):
        source = read(ROOT / 'src' / 'call_evaluation' / 'main.jsx')
        self.assertIn("get('theme') === 'dark'", source)
        self.assertIn("import('../theme-dark.css')", source)
        # Читать сохранённый выбор самой сборке нельзя: тогда тему получил бы
        # любой, у кого этот ключ когда-то остался в браузере.
        self.assertNotIn(DARK_THEME_STORAGE_KEY, source)


class DarkThemeAccessTests(unittest.TestCase):
    def test_access_is_decided_by_login(self):
        util = read(THEME_UTIL)
        self.assertIn("DARK_THEME_LOGINS", util)
        self.assertIn("user?.login", util)
        # Сохранённый в браузере выбор права НЕ даёт: он только запоминает,
        # что человек выбрал, а пускает список логинов.
        self.assertIn("localStorage.getItem(DARK_THEME_STORAGE_KEY)", util)

    def test_app_applies_theme_only_for_allowed_account(self):
        app = read(APP_JSX)
        self.assertIn("const darkThemeActive = darkThemeAllowed && darkTheme;", app)
        self.assertIn("applyDarkTheme(darkThemeActive", app)
        self.assertIn("const darkThemeAllowed = canUseDarkTheme(user)", app)

    def test_avatar_click_toggles_theme_without_opening_account_menu(self):
        """Тычок в аватар — это переключатель, а не вход в меню аккаунта."""
        app = read(APP_JSX)
        self.assertIn(
            "onClick={darkThemeAllowed ? ((e) => { e.stopPropagation(); toggleDarkTheme(); }) : undefined}",
            app)
        # Мемоизированный сайдбар обязан видеть смену темы, иначе подсказка
        # на аватаре останется от прошлого состояния.
        self.assertRegex(app, r"darkThemeAllowed,\s*\n\s*darkTheme,\s*\n\s*toggleDarkTheme,")


if __name__ == "__main__":
    unittest.main()
