# -*- coding: utf-8 -*-
"""Сборка темы «Роза» раздела «Чаты ОП» — src/components/wazzup/chatThemeRose.css.

«Роза» — тема чатов (src/components/wazzup/chatThemes.js): розовое окно
раздела целиком, так же как «Ночь» делает его тёмным. Разбор
утилит и набор файлов окна — те же, что у «Ночи» (build_chat_night_theme),
палитра своя:
  * нейтральная шкала (slate/gray и белый) — те же ступени светлоты в тон розы:
    поверхности, канты и текст; контраст текста не ниже, чем был;
  * синий — акцент интерфейса (ссылки, кнопки, фокус) — становится розовым;
  * остальные цвета несут смысл (ждут ответа, статус смены, ошибка, заметка,
    «прочитано») и остаются как есть;
  * тёмные заливки 700…950 (подсказки, просмотр вложений) остаются тёмными.

Рукописная часть (полотно окна, мозаика переписки, меню, ползунки, плеер) —
scripts/chat_rose_theme_chrome.css. В отличие от «Ночи», она тоже лежит в
слое: не выбравшим «Розу» её байты не нужны. Слой грузится только у того,
кто тему выбрал.

Запуск:  python3 scripts/build_chat_rose_theme.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_chat_night_theme as night  # noqa: E402
import build_dark_theme as dark  # noqa: E402

ROOT = dark.ROOT
OUT = os.path.join(ROOT, 'src', 'components', 'wazzup', 'chatThemeRose.css')
CHROME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_rose_theme_chrome.css')
# Окно раздела и его меню: меню рисуются в портал (вне окна) и в «Розе»
# помечаются меткой на body (useThemeMenus в chatThemes.js).
SCOPE = ':is(.wz-workspace[data-chat-theme="rose"], body[data-wz-chat-rose] > [role="menu"])'

# Поверхности: белая карточка и подложки 50…600. Тёмные 700…950 не трогаем.
BG_NEUTRAL = {
    'white': '#fff7fa',
    '50':    '#fdf0f5',
    '100':   '#f9e4ec',
    '200':   '#f2d3df',
    '300':   '#e8bfd0',
    '400':   '#d29fb5',
    '500':   '#b78199',
    '600':   '#9c6880',
}

# Текст. 50…200 — светлый текст на тёмном и цветном, его не трогаем.
TEXT_NEUTRAL = {
    '300':   '#d4abbd',
    '400':   '#b08294',
    '500':   '#875b6d',
    '600':   '#6f4758',
    '700':   '#5a3747',
    '800':   '#452a37',
    '900':   '#331e28',
    '950':   '#26151d',
    'black': '#26151d',
}

# Канты, кольца, разделители.
LINE_NEUTRAL = {
    '50':  '#fbedf2',
    '100': '#f5e0e8',
    '200': '#eed0dc',
    '300': '#e3bccc',
    '400': '#d1a0b5',
    '500': '#bc86a0',
    '600': '#a26c86',
    '700': '#88566f',
    '800': '#6f455a',
    '900': '#5a3849',
    '950': '#4b2e3c',
}

# Синий акцент → розовый: заливки и канты по ступеням, текст — на ступень
# темнее заливки, чтобы ссылка на розоватой карточке читалась не хуже синей.
ACCENT = {
    '50':  '#fdedf3',
    '100': '#fadbe7',
    '200': '#f5bfd3',
    '300': '#ee9fbe',
    '400': '#e07ca4',
    '500': '#d0568b',
    '600': '#bb4078',
    '700': '#9e3263',
    '800': '#822952',
    '900': '#6a2244',
    '950': '#43132a',
}
ACCENT_TEXT = {
    '50':  '#fff0f6',
    '100': '#fde6ef',
    '200': '#f9cfdf',
    '300': '#f2aecb',
    '400': '#d86a99',
    '500': '#c24a80',
    '600': '#ad3a6e',
    '700': '#8f2f5c',
    '800': '#76274c',
    '900': '#5f203e',
    '950': '#3d1227',
}


def declaration(prop, color, alpha):
    """Что слой «Розы» объявляет для одной утилиты. None — «не трогаем»."""
    family, shade = dark.split_color(color)
    if family in ('gray', 'zinc', 'neutral', 'stone'):
        family = 'slate'
    if family not in ('slate', 'white', 'black', 'blue'):
        return None                          # смысловой цвет остаётся собой
    accent = family == 'blue'

    if prop == 'bg':
        if family == 'black':
            return None                      # шторка модалки
        if family == 'white':
            if alpha is not None and alpha < 0.6:
                return None                  # подсветка на цветном фоне, не поверхность
            value = BG_NEUTRAL['white']
        else:
            value = (ACCENT if accent else BG_NEUTRAL).get(shade)
    elif prop in ('text', 'placeholder'):
        if family == 'white':
            return None
        if family == 'black':
            if alpha is not None:
                return None
            value = TEXT_NEUTRAL['black']
        else:
            value = (ACCENT_TEXT if accent else TEXT_NEUTRAL).get(shade)
    else:                                    # border / ring / divide
        if family in ('white', 'black'):
            return None                      # кант на цветном фоне
        value = (ACCENT if accent else LINE_NEUTRAL).get(shade)
    if not value:
        return None
    if alpha is not None:
        value = dark.rgba(value, alpha)
    if prop == 'bg':
        return 'background-color: %s' % value
    if prop in ('text', 'placeholder'):
        return 'color: %s' % value
    if prop == 'ring':
        return '--tw-ring-color: %s' % value
    return 'border-color: %s' % value


def utility_rows():
    found = set()
    for text in night.sources():
        for match in dark.TOKEN_RE.finditer(text):
            prefix, prop, color, alpha = match.groups()
            found.add((prefix, prop, color, alpha or ''))
    rows = []
    for prefix, prop, color, alpha in sorted(found):
        decl = declaration(prop, color, int(alpha[1:]) / 100.0 if alpha else None)
        if not decl:
            continue
        built = dark.build_selector(prefix, '%s%s-%s%s' % (prefix, prop, color, alpha))
        if not built:
            continue
        media, selector = built
        rows.append((media, selector + dark.SELECTOR_TAIL.get(prop, ''), decl))
    rows.sort(key=lambda row: (row[0] or '', row[1]))
    return rows


HEADER = (
    '/* ЭТОТ ФАЙЛ СОБРАН СКРИПТОМ. Правки вносить в scripts/build_chat_rose_theme.py\n'
    '   и scripts/chat_rose_theme_chrome.css, затем прогнать:\n'
    '       python3 scripts/build_chat_rose_theme.py\n'
    '\n'
    '   Тема «Роза» раздела «Чаты ОП»: розовое окно раздела с data-chat-theme="rose"\n'
    '   и его меню. Слой грузится по требованию (loadThemeLayer в chatThemes.js). */\n'
)


def build():
    with open(CHROME, encoding='utf-8') as fh:
        chrome = fh.read().strip()
    previous = dark.ROOT_SEL
    dark.ROOT_SEL = SCOPE
    try:
        rows = utility_rows()
    finally:
        dark.ROOT_SEL = previous
    css = '\n'.join([
        HEADER,
        chrome,
        '',
        '/* ═══ Утилиты Tailwind окна раздела ═══════════════════════════════════ */',
        dark.emit(rows),
        '',
    ])
    return css, len(rows)


def main():
    css, count = build()
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(css)
    print('%s: %d правил' % (os.path.relpath(OUT, ROOT), count))


if __name__ == '__main__':
    main()
