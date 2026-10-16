# -*- coding: utf-8 -*-
"""Build the Cherry Biome palette for the Chats OP workspace.

Only neutral surfaces and the blue interface accent are recolored. Status,
warning, error, note, and read-receipt colors keep their existing meaning.
The shared scanner and selector parser are reused without changing their
module state. Custom workspace details live in chat_cherry_theme_chrome.css.

Run: python scripts/build_chat_cherry_theme.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_chat_night_theme as night  # noqa: E402
import build_dark_theme as dark  # noqa: E402

ROOT = dark.ROOT
OUT = os.path.join(ROOT, 'src', 'components', 'wazzup', 'chatThemeCherry.css')
CHROME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_cherry_theme_chrome.css')
SCOPE = ('html:not([data-otp-theme="dark"]) '
         ':is(.wz-workspace[data-chat-theme="cherry"], body[data-wz-chat-cherry] > [role="menu"])')

# Dark overlays keep their original fills. The workspace, selected row, and
# message colors follow the approved palette; other steps support controls.
BG_NEUTRAL = {
    'white': '#fff8fb',
    '50': '#fff3f8',
    '100': '#f9dce9',
    '200': '#f2cddd',
    '300': '#e8b7cc',
    '400': '#cf97b1',
    '500': '#b77894',
    '600': '#975571',
}

TEXT_NEUTRAL = {
    '300': '#c89bb0',
    '400': '#80556b',
    '500': '#75495f',
    '600': '#684056',
    '700': '#5b374a',
    '800': '#513143',
    '900': '#492b3c',
    '950': '#35202d',
    'black': '#35202d',
}

LINE_NEUTRAL = {
    '50': '#fcebf3',
    '100': '#f6deea',
    '200': '#efc9dc',
    '300': '#e4b2cb',
    '400': '#d597b7',
    '500': '#bd7b9c',
    '600': '#a76385',
    '700': '#894a6b',
    '800': '#713c58',
    '900': '#5b3048',
    '950': '#482639',
}

ACCENT = {
    '50': '#fff0f6',
    '100': '#f9dce9',
    '200': '#f6bfd6',
    '300': '#ee9fc1',
    '400': '#dc6c9c',
    '500': '#bd3976',
    '600': '#ac2c65',
    '700': '#922551',
    '800': '#7d2047',
    '900': '#661b3c',
    '950': '#431128',
}

# Links remain readable on both neutral surfaces and outgoing pink bubbles.
ACCENT_TEXT = {
    '50': '#fff0f6',
    '100': '#fde4ef',
    '200': '#f7c9dd',
    '300': '#ec9fbe',
    '400': '#b73570',
    '500': '#9b2559',
    '600': '#8f2252',
    '700': '#7c1d46',
    '800': '#68193c',
    '900': '#541530',
    '950': '#3b1023',
}


def declaration(prop, color, alpha):
    """Return one themed declaration, or None for a preserved color."""
    family, shade = dark.split_color(color)
    if family in ('gray', 'zinc', 'neutral', 'stone'):
        family = 'slate'
    if family not in ('slate', 'white', 'black', 'blue'):
        return None
    accent = family == 'blue'

    if prop == 'bg':
        if family == 'black':
            return None
        if family == 'white':
            if alpha is not None and alpha < 0.6:
                return None
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
    else:
        if family in ('white', 'black'):
            return None
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
        # Rebase the parsed selector without mutating shared module globals.
        source_scope = dark.ROOT_SEL
        if not selector.startswith(source_scope + ' '):
            raise ValueError('Unexpected utility selector scope: %s' % selector)
        selector = SCOPE + selector[len(source_scope):]
        rows.append((media, selector + dark.SELECTOR_TAIL.get(prop, ''), decl))
    return sorted(rows, key=lambda row: (row[0] or '', row[1]))


HEADER = (
    '/* GENERATED FILE: edit scripts/build_chat_cherry_theme.py and\n'
    '   scripts/chat_cherry_theme_chrome.css, then run:\n'
    '       python scripts/build_chat_cherry_theme.py\n'
    '\n'
    '   Cherry Biome palette for Chats OP and its menus. Loaded on demand. */\n'
)


def build():
    with open(CHROME, encoding='utf-8') as source:
        chrome = source.read().strip()
    rows = utility_rows()
    css = '\n'.join([HEADER, chrome, '', '/* Workspace color utilities. */', dark.emit(rows), ''])
    return css, len(rows)


def main():
    css, count = build()
    with open(OUT, 'w', encoding='utf-8', newline='\n') as output:
        output.write(css)
    print('%s: %d rules' % (os.path.relpath(OUT, ROOT), count))


if __name__ == '__main__':
    main()
