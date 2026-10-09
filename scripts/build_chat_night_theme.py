# -*- coding: utf-8 -*-
"""Сборка темы «Ночь» раздела «Чаты ОП» — src/components/wazzup/chatThemeNight.css.

«Ночь» — одна из тем чатов (src/components/wazzup/chatThemes.js): оператор в
светлом портале выбирает тёмное окно раздела. Палитра — ТА ЖЕ, что у тёмного
режима портала (scripts/build_dark_theme.py: шкалы заливок, текста и кантов,
цветные подложки), поэтому раздел в «Ночи» выглядит ровно как в тёмном режиме
портала. Палитру и разбор утилит не копируем — берём из build_dark_theme.

Отличия от слоя портала:
  * действует только внутри окна раздела с атрибутом data-chat-theme="night";
  * утилиты — только те, что встречаются в файлах окна (FILES): слой грузится,
    лишь когда тему выбрали, и полная решётка портала ему не нужна.

То, что живёт не в утилитах (полотно окна, ползунки, заметки, плеер, эмодзи,
пузыри по переменным темы), — рукописное, в chatThemes.css.

Запуск:  python3 scripts/build_chat_night_theme.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_dark_theme as dark  # noqa: E402

ROOT = dark.ROOT
OUT = os.path.join(ROOT, 'src', 'components', 'wazzup', 'chatThemeNight.css')
# Окно раздела и его меню: меню рисуются в портал (вне окна) и в «Ночи»
# помечаются меткой на body (useNightMenus в chatThemes.js).
SCOPE = ':is(.wz-workspace[data-chat-theme="night"], body[data-wz-chat-night] > [role="menu"])'

# Что рисуется внутри окна раздела: сам раздел и его компоненты, плюс общие
# примитивы, из которых собраны шапка, поиск и меню.
WAZZUP_DIR = os.path.join(ROOT, 'src', 'components', 'wazzup')
SHARED = ('ios.jsx', 'CustomSelect.jsx', 'DateRangePicker.jsx')


def files():
    paths = [os.path.join(WAZZUP_DIR, name) for name in os.listdir(WAZZUP_DIR)
             if name.endswith(('.js', '.jsx'))]
    paths += [os.path.join(ROOT, 'src', 'components', 'ui', name) for name in SHARED]
    return sorted(paths)


def sources():
    for path in files():
        with open(path, encoding='utf-8', errors='ignore') as fh:
            yield fh.read()


def utility_rows():
    found = set()
    for text in sources():
        for match in dark.TOKEN_RE.finditer(text):
            prefix, prop, color, alpha = match.groups()
            found.add((prefix, prop, color, alpha or ''))
    return dark.rules_for(sorted(found))


def arbitrary_rows():
    """Утилиты с цветом в квадратных скобках — как arbitrary_rules() портала."""
    rows, seen = [], set()
    for text in sources():
        for prefix, prop, value in dark.ARBITRARY_RE.findall(text):
            key = (prefix, prop, value.lower())
            if key in seen:
                continue
            seen.add(key)
            replacement = dark.dark_counterpart(value, dark.ARBITRARY_ROLE[prop])
            if not replacement:
                continue
            built = dark.build_selector(prefix, '%s%s-[%s]' % (prefix, prop, value))
            if not built:
                continue
            media, selector = built
            decl = {'bg': 'background-color: %s', 'ring': '--tw-ring-color: %s',
                    'border': 'border-color: %s'}.get(prop, 'color: %s') % replacement
            rows.append((media, selector, decl))
    rows.sort(key=lambda row: row[1])
    return rows


HEADER = (
    '/* ЭТОТ ФАЙЛ СОБРАН СКРИПТОМ. Правки вносить в scripts/build_chat_night_theme.py\n'
    '   (палитра — в scripts/build_dark_theme.py), затем прогнать:\n'
    '       python3 scripts/build_chat_night_theme.py\n'
    '\n'
    '   Тема «Ночь» раздела «Чаты ОП»: палитра тёмного режима портала, но только\n'
    '   внутри окна раздела с data-chat-theme="night" и его меню. Грузится по\n'
    '   требованию (loadNightTheme в chatThemes.js). Рукописная часть темы —\n'
    '   chatThemes.css. */\n'
)


def build():
    previous = dark.ROOT_SEL
    dark.ROOT_SEL = SCOPE
    try:
        rows = utility_rows() + arbitrary_rows()
    finally:
        dark.ROOT_SEL = previous
    return HEADER + '\n' + dark.emit(rows) + '\n', len(rows)


def main():
    css, count = build()
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(css)
    print('%s: %d правил' % (os.path.relpath(OUT, ROOT), count))


if __name__ == '__main__':
    main()
