# -*- coding: utf-8 -*-
"""Сборка тёмного слоя портала — src/theme-dark.css.

Портал светлый: тёмной темы в проекте нет, и классов `dark:*` в разделах нет
намеренно (Tailwind работает в режиме media, они сработали бы от темы системы).
Тёмный режим здесь — ОТДЕЛЬНЫЙ слой поверх собранных утилит, включаемый
атрибутом `data-otp-theme="dark"` на <html>. Ни одна строка разметки под него
не переписана: 54 тысячи строк App.jsx переписывать на `dark:*` нельзя.

Почему генератор, а не рукописный CSS. Правил здесь около шестисот: цвет
задаётся утилитой Tailwind (`bg-white`, `hover:bg-slate-50`, `ring-slate-200/70`),
и на каждую такую утилиту нужна своя строка — руками это не поддерживается и
не проверяется. Скрипт берёт палитру из одного места и раскладывает её по
селекторам.

Что попадает в слой:
  * ПОЛНАЯ решётка базовых утилит (без модификаторов) — она детерминирована и
    не зависит от того, что сейчас написано в разделах;
  * модификаторы (`hover:`, `focus:`, `/70`, `sm:` …) — только те, что реально
    встречаются в src. Новый `hover:bg-teal-50`, добавленный в разделе после
    сборки, останется светлым, пока скрипт не прогонят снова.

Чего слой НЕ трогает специально:
  * `text-white` и светлый текст 50–200 — он лежит на цветных кнопках и на
    синем сайдбаре, перекрасить его значило бы стереть надписи;
  * заливки `bg-<цвет>-400…950` — это акценты (кнопки, бейджи), они и в темноте
    обязаны читаться теми же;
  * `bg-black/NN` и `bg-white/10…50` — это шторки модалок и подсветка на
    цветном фоне, а не поверхности;
  * тренажёры вики (`.wt-root`, `.wt-overlay`) — их экраны повторяют чужие
    приложения по скриншотам, палитра снята пипеткой, и темнить их нельзя.
    Отдельного исключения не потребовалось: внутри тренажёров нет ни одной
    цветовой утилиты Tailwind, вся палитра живёт в переменных `--wt-*`.

Запуск:  python3 scripts/build_dark_theme.py
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
OUT = os.path.join(SRC, 'theme-dark.css')

ROOT_SEL = 'html[data-otp-theme="dark"]'

# ─────────────────────────────────────────────────────────────────────────────
# Палитра. Графит с холодным уклоном — тот же тон, что у системной тёмной темы
# macOS: полотно почти чёрное, карточка чуть светлее, шрифт не белый (#fff на
# графите режет глаз), а на пару ступеней мягче.
# ─────────────────────────────────────────────────────────────────────────────

# Заливки нейтральной шкалы. Шкала «перевёрнута»: чем светлее исходный оттенок,
# тем темнее замена. Разрыв на 700 умышленный — оттенки 700…950 в светлой теме
# сами по себе ТЁМНЫЕ (подсказки, тёмные шапки, чипы с белым текстом), их нельзя
# инвертировать в белый, их надо лишь приподнять над карточкой, чтобы они не
# слились с ней в один прямоугольник.
BG_NEUTRAL = {
    'white': '#1e2127',   # карточка — основная поверхность
    '50':    '#17191e',   # полотно страницы, самое тёмное
    '100':   '#22262d',   # чип, подсветка строки
    '200':   '#2a2f37',
    '300':   '#333941',
    '400':   '#3d434d',
    '500':   '#464d58',
    '600':   '#4b5360',
    '700':   '#3c424c',   # ниже — «уже тёмные» в светлой теме
    '800':   '#343a43',
    '900':   '#2b3039',
    '950':   '#23272e',
}

# Текст. 50…200 не трогаем (он лежит на цветном), 300 трогаем — в портале это
# «выключено»/«еле заметно» на белой карточке, и в темноте оно обязано погаснуть
# наоборот.
TEXT_NEUTRAL = {
    '300':  '#666f7d',
    '400':  '#7d8695',
    '500':  '#939cab',
    '600':  '#a8b1bf',
    '700':  '#c1c8d3',
    '800':  '#d5dae2',
    '900':  '#e8ecf2',
    '950':  '#eef1f6',
    'black': '#e8ecf2',
}

# Границы, кольца, разделители.
LINE_NEUTRAL = {
    '50':   '#1c1f25',
    '100':  '#24282f',
    '200':  '#2e333c',
    '300':  '#3a414b',
    '400':  '#464e59',
    '500':  '#525a67',
    '600':  '#626b78',
    '700':  '#6b7481',
    '800':  '#757e8b',
    '900':  '#7f8895',
    '950':  '#89929f',
}

# Цветные семейства: (RGB оттенка 500, текст на замену 500, текст на замену 600+).
# Подложки 50/100/200 в темноте становятся не «светлым цветом», а прозрачной
# заливкой того же цвета: плотный светлый прямоугольник на графите бьёт по
# глазам, а прозрачная — сохраняет смысл (предупреждение, успех, ошибка).
HUES = {
    'blue':    ((59, 130, 246),  '#93c5fd', '#93c5fd'),
    'indigo':  ((99, 102, 241),  '#a5b4fc', '#a5b4fc'),
    'violet':  ((139, 92, 246),  '#c4b5fd', '#c4b5fd'),
    'purple':  ((168, 85, 247),  '#d8b4fe', '#d8b4fe'),
    'fuchsia': ((217, 70, 239),  '#f0abfc', '#f0abfc'),
    'pink':    ((236, 72, 153),  '#f9a8d4', '#f9a8d4'),
    'rose':    ((244, 63, 94),   '#fda4af', '#fda4af'),
    'red':     ((239, 68, 68),   '#fca5a5', '#fca5a5'),
    'orange':  ((249, 115, 22),  '#fdba74', '#fdba74'),
    'amber':   ((245, 158, 11),  '#fcd34d', '#fcd34d'),
    'yellow':  ((234, 179, 8),   '#fde047', '#fde047'),
    'lime':    ((132, 204, 22),  '#bef264', '#bef264'),
    'green':   ((34, 197, 94),   '#86efac', '#86efac'),
    'emerald': ((16, 185, 129),  '#6ee7b7', '#6ee7b7'),
    'teal':    ((20, 184, 166),  '#5eead4', '#5eead4'),
    'cyan':    ((6, 182, 212),   '#67e8f9', '#67e8f9'),
    'sky':     ((14, 165, 233),  '#7dd3fc', '#7dd3fc'),
}

# Доля цвета, подмешиваемая в тёмную поверхность. ПОДМЕШИВАЕМАЯ, а не
# положенная сверху с прозрачностью: прозрачная плашка выглядит правильно,
# только пока под ней тёмная карточка. В «Опросах» такой же чип `bg-blue-50
# text-blue-700` лежит на СИНЕЙ кнопке — прозрачная заливка там сливалась с
# кнопкой, и счётчик пропадал. Непрозрачный цвет одинаково читается везде.
HUE_BG_MIX = {'50': 0.10, '100': 0.16, '200': 0.24}
HUE_LINE_MIX = {'100': 0.30, '200': 0.42, '300': 0.55}
HUE_BG_BASE = (30, 33, 39)      # #1e2127 — карточка
HUE_LINE_BASE = (46, 51, 60)    # #2e333c — кант

NEUTRALS = ('slate', 'gray')
SHADES = ('50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950')

# ─────────────────────────────────────────────────────────────────────────────
# Разбор утилит
# ─────────────────────────────────────────────────────────────────────────────

FAMILIES = '|'.join(NEUTRALS + tuple(HUES) + ('zinc', 'neutral', 'stone'))
TOKEN_RE = re.compile(
    r'(?<![\w:/.-])'
    r'((?:[a-z0-9-]+:)*)'
    r'(bg|text|border|ring|divide|placeholder)-'
    r'((?:white|black)|(?:' + FAMILIES + r')-(?:50|[1-9]00|950))'
    r'(/\d{1,3})?'
    r'(?![\w/-])'
)

# Модификатор → псевдокласс. `dark` и `peer-*` в слой не берём: первый — это
# тема системы (у нас свой атрибут), второй требует соседа в селекторе.
PSEUDO = {
    'hover': ':hover',
    'focus': ':focus',
    'focus-visible': ':focus-visible',
    'focus-within': ':focus-within',
    'active': ':active',
    'disabled': ':disabled',
    'enabled': ':enabled',
    'even': ':nth-child(even)',
    'odd': ':nth-child(odd)',
    'first': ':first-child',
    'last': ':last-child',
    'placeholder': '::placeholder',
    'before': '::before',
    'after': '::after',
}
ANCESTOR = {'group-hover': '.group:hover '}
SCREENS = {'sm': 640, 'md': 768, 'lg': 1024, 'xl': 1280, '2xl': 1536}
SKIP_VARIANTS = ('dark', 'file')


def mix_rgb(base, color, amount):
    """Подмешать цвет в основу: '#rrggbb' на выходе."""
    return '#%02x%02x%02x' % tuple(
        round(base[i] + (color[i] - base[i]) * amount) for i in range(3))


def rgba(hex_or_rgb, alpha):
    if isinstance(hex_or_rgb, str):
        h = hex_or_rgb.lstrip('#')
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    else:
        rgb = hex_or_rgb
    return 'rgba(%d, %d, %d, %s)' % (rgb[0], rgb[1], rgb[2], ('%.3f' % alpha).rstrip('0').rstrip('.'))


def split_color(color):
    """'slate-200' → ('slate', '200'); 'white' → ('white', None)."""
    if color in ('white', 'black'):
        return color, None
    family, shade = color.rsplit('-', 1)
    return family, shade


def declaration(prop, color, alpha):
    """Что тёмный слой объявляет для одной утилиты. None — «не трогаем»."""
    family, shade = split_color(color)
    if family in ('zinc', 'neutral', 'stone'):
        family = 'slate'
    is_neutral = family in ('slate', 'gray', 'white', 'black')

    if prop == 'bg':
        if family == 'black':
            return None                      # шторка модалки — она и должна быть чёрной
        if family == 'white':
            if alpha is not None and alpha < 0.6:
                return None                  # подсветка на цветном фоне, не поверхность
            value = BG_NEUTRAL['white']
        elif is_neutral:
            value = BG_NEUTRAL[shade]
        else:
            if shade not in HUE_BG_MIX:
                return None                  # 400…950 — акцентная заливка, остаётся собой
            value = mix_rgb(HUE_BG_BASE, HUES[family][0], HUE_BG_MIX[shade])
            if alpha is not None:
                return 'background-color: %s' % rgba(value, alpha)
            return 'background-color: %s' % value
        if alpha is not None:
            return 'background-color: %s' % rgba(value, alpha)
        return 'background-color: %s' % value

    if prop in ('text', 'placeholder'):
        if family in ('white', 'black') and alpha is not None:
            # `text-white/70` лежит на цветной кнопке — там всё в порядке.
            return None
        if family == 'white':
            return None
        if is_neutral:
            key = 'black' if family == 'black' else shade
            value = TEXT_NEUTRAL.get(key)
        else:
            if shade in ('50', '100', '200', '300', '400'):
                return None                  # светлый цветной текст — уже на тёмном
            value = HUES[family][1] if shade == '500' else HUES[family][2]
        if not value:
            return None
        if alpha is not None:
            # `text-blue-900/80` в «Технических проблемах»: тёмно-синий на
            # 80 % — это подпись поля, а не текст на цветной кнопке.
            return 'color: %s' % rgba(value, alpha)
        return 'color: %s' % value

    # border / ring / divide
    if family in ('white', 'black'):
        return None                          # кант на цветном фоне
    if is_neutral:
        if alpha is not None and alpha <= 0.25 and int(shade) >= 700:
            # `ring-slate-900/5` — не рамка, а имитация тени под белой карточкой.
            value = 'rgba(255, 255, 255, 0.08)'
        else:
            value = LINE_NEUTRAL[shade]
            if alpha is not None:
                value = rgba(value, alpha)
    else:
        if shade not in HUE_LINE_MIX:
            return None
        value = mix_rgb(HUE_LINE_BASE, HUES[family][0], HUE_LINE_MIX[shade])
        if alpha is not None:
            value = rgba(value, alpha)

    if prop == 'ring':
        return '--tw-ring-color: %s' % value
    return 'border-color: %s' % value


def escape_class(name):
    out = name.replace('\\', '\\\\')
    for char in (':', '/', '.', '[', ']', '#', '(', ')', ',', '%', '!'):
        out = out.replace(char, '\\' + char)
    return out


def build_selector(prefix, class_name):
    """Селектор тёмного слоя для утилиты с её модификаторами.

    Возвращает (медиазапрос | None, селектор) либо None, если модификатор не
    поддержан. Специфичность всегда выше самой утилиты, а правила с
    псевдоклассами — ещё и выше базовых, поэтому наведение не гаснет.
    """
    media = None
    ancestor = ''
    pseudo = ''
    for variant in filter(None, prefix.split(':')):
        if variant in SKIP_VARIANTS or variant.startswith('peer-'):
            return None
        if variant in SCREENS:
            media = '@media (min-width: %dpx)' % SCREENS[variant]
        elif variant in ANCESTOR:
            ancestor = ANCESTOR[variant]
        elif variant in PSEUDO:
            pseudo += PSEUDO[variant]
        else:
            return None
    return media, '%s %s.%s%s' % (ROOT_SEL, ancestor, escape_class(class_name), pseudo)


# Хвост селектора для утилит, которые Tailwind вешает не на сам элемент.
SELECTOR_TAIL = {
    'divide': ' > :not([hidden]) ~ :not([hidden])',
    'placeholder': '::placeholder',
}


def scan_tokens():
    """Утилиты с модификаторами, реально встречающиеся в разделах."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != 'node_modules']
        for fn in filenames:
            if not fn.endswith(('.jsx', '.js')):
                continue
            with open(os.path.join(dirpath, fn), encoding='utf-8', errors='ignore') as fh:
                text = fh.read()
            for m in TOKEN_RE.finditer(text):
                prefix, prop, color, alpha = m.groups()
                if not prefix and not alpha:
                    continue                 # базовая решётка строится отдельно
                found.add((prefix, prop, color, alpha or ''))
    return found


def base_tokens():
    """Полная решётка базовых утилит — не зависит от текущей разметки."""
    out = []
    for family in NEUTRALS:
        for shade in SHADES:
            for prop in ('bg', 'text', 'border', 'ring', 'divide'):
                out.append(('', prop, '%s-%s' % (family, shade), ''))
    out.append(('', 'bg', 'white', ''))
    out.append(('', 'text', 'black', ''))
    for family in NEUTRALS:
        for shade in ('300', '400', '500'):
            out.append(('', 'placeholder', '%s-%s' % (family, shade), ''))
    for family in HUES:
        for shade in ('50', '100', '200'):
            out.append(('', 'bg', '%s-%s' % (family, shade), ''))
        for shade in ('100', '200', '300'):
            out.append(('', 'border', '%s-%s' % (family, shade), ''))
            out.append(('', 'ring', '%s-%s' % (family, shade), ''))
        for shade in ('500', '600', '700', '800', '900', '950'):
            out.append(('', 'text', '%s-%s' % (family, shade), ''))
    return out


def rules_for(tokens):
    """(медиазапрос, селектор, объявление) — по одной строке на утилиту."""
    rows = []
    for prefix, prop, color, alpha in tokens:
        a = int(alpha[1:]) / 100.0 if alpha else None
        decl = declaration(prop, color, a)
        if not decl:
            continue
        class_name = '%s%s-%s%s' % (prefix, prop, color, alpha)
        built = build_selector(prefix, class_name)
        if not built:
            continue
        media, selector = built
        rows.append((media, selector + SELECTOR_TAIL.get(prop, ''), decl))
    rows.sort(key=lambda r: (r[0] or '', r[1]))
    return rows


def emit(rows):
    """Правила блоками: сперва безусловные, потом каждый @media своим блоком.

    Медиазапрос сохраняется дословно. Правило из `@media print` обязано
    остаться в `@media print`: вынесенное наружу, оно начнёт действовать на
    экране — а печатная версия как раз перекрашивает то, что на экране должно
    выглядеть иначе."""
    lines = []
    for media, selector, decl in [r for r in rows if r[0] is None]:
        lines.append('%s { %s !important; }' % (selector, decl))
    for media in sorted({r[0] for r in rows if r[0] is not None}):
        lines.append('')
        lines.append('%s {' % media)
        for _, selector, decl in [r for r in rows if r[0] == media]:
            lines.append('    %s { %s !important; }' % (selector, decl))
        lines.append('}')
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Разделы со своей палитрой
#
# Не всё в портале раскрашено утилитами Tailwind. У «Задач» собственная
# дизайн-система: 2600 строк CSS, которые раздел вставляет тегом <style> при
# загрузке, — там и переменные, и полторы сотни строк с хексами прямо в
# правилах. Такой раздел решётка утилит не красит вовсе: в тёмной теме он
# остался бы светлым островом.
#
# Поэтому эти источники разбираются построчно: каждое объявление со светлым
# цветом получает тёмного двойника с тем же селектором. Роль цвета берётся из
# свойства — заливка, кант или текст, — и подставляется по одной шкале, чтобы
# раздел попал в ту же палитру, что и остальной портал.
#
# Насыщенные цвета остаются цветами: у «Задач» тон — это смысл (срочная,
# критичная, возвращена), и превращать плашки в серые нельзя. Светлая заливка
# становится прозрачной того же тона, тёмный цветной текст — светлым.
# ─────────────────────────────────────────────────────────────────────────────

import colorsys

PALETTE_SOURCES = (
    ('src/components/tasks/TasksView.jsx', 'style'),
    # Вики. Переменные --wiki-* перекрыты вручную в dark_theme_chrome.css, но
    # цвет ТЕКСТА СТАТЬИ переменной не задан: `.wiki-prose { color: #334155 }`
    # стоит литералом, и без разбора этого файла тело статьи оставалось
    # тёмно-серым на графите — то есть статьи было не прочитать.
    ('src/components/wiki/wiki-theme.css', 'css'),
    ('src/components/wiki/wiki-blocks.css', 'css'),
    # Раздел рисует свои стили тегом <style> прямо в разметке.
    ('src/components/monitoring/MonitoringScaleView.jsx', 'jsx-style'),
    ('src/components/technical/TechnicalIssuesView.jsx', 'jsx-style'),
    # «Журнал оценок» — ОТДЕЛЬНАЯ сборка, живущая в iframe: Tailwind там нет
    # вовсе, вся палитра в этом файле (см. call_evaluation.html).
    ('src/call_evaluation/styles.css', 'css'),
    ('src/components/news/news-modal.css', 'css'),
    ('src/components/lms/LmsRichText.css', 'css'),
    ('src/components/ui/markdown.css', 'css'),
)

SURFACE_PROPS = ('background', 'background-color')
LINE_PROPS = ('border', 'border-color', 'border-top', 'border-bottom', 'border-left',
              'border-right', 'border-top-color', 'border-bottom-color',
              'border-left-color', 'border-right-color', 'outline', 'outline-color')
INK_PROPS = ('color', 'caret-color', '-webkit-text-fill-color')

# Цвет в правиле бывает не только хексом. Полоса чередования строк в таблице
# статьи вики записана как `rgba(248, 250, 252, 0.75)` — искали бы только
# хексы, она осталась бы светлой, и текст на ней стал бы белым по белому.
HEX_RE = re.compile(
    r'#[0-9a-fA-F]{3,8}\b'
    r'|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*[\d.]+\s*)?\)')


def split_css_color(token):
    """'rgba(248, 250, 252, .75)' → ('#f8fafc', 0.75); хекс → (хекс, None)."""
    if token.startswith('#'):
        return token, None
    parts = [part.strip() for part in token[token.index('(') + 1:-1].split(',')]
    channels = [max(0, min(255, int(float(part)))) for part in parts[:3]]
    alpha = float(parts[3]) if len(parts) > 3 else None
    return '#%02x%02x%02x' % tuple(channels), alpha


def hex_to_hsl(value):
    h = value.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    h = h[:6]
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)   # (hue, lightness, saturation)


def colorfulness(value):
    """Насыщенность по HLS и абсолютный размах каналов.

    Порог «серый или цветной» стоит на HLS-насыщенности, потому что она
    единственная сравнивает размах каналов с ТЕМ, ЧТО ВОЗМОЖНО при такой
    светлоте. Абсолютный размах для этого не годится в обе стороны:
    у #111827 (почти чёрный slate) он всего 22, но это предел для такой
    темноты, а у #dbeafe (blue-100) — 35, и это уже почти максимум для
    такой светлоты.

    Порог 0.45, а не 0.16, как было сперва: у нейтральных серых портала
    насыщенность доходит до 0.4 (#f8fafc, #f1f5f9, #111827), а у самых
    бледных настоящих оттенков — 0.75 и выше (#eef2ff, #f0fdf4, #fef2f2).
    Заниженный порог красил системный серый #f2f2f7 (0.24) в синий и залил
    им целое полотно списка чатов Wazzup.

    Второе условие — для почти белого с размахом в один-два канала: там
    насыщенность скачет от округления.
    """
    h = value.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    h = h[:6]
    channels = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return max(channels) - min(channels)


def hsl_hex(hue, lightness, saturation):
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


def hsl_channels(hue, lightness, saturation):
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return (round(r * 255), round(g * 255), round(b * 255))


def dark_counterpart(value, role):
    hue, lightness, saturation = hex_to_hsl(value)
    chroma = colorfulness(value)
    # Три условия, и достаточно любого. Первое — обычные серые. Второе — почти
    # чёрные и почти белые: у #0f172a насыщенность 0.47, а размах всего 27,
    # то есть это slate-900, а не синий; у настоящего индиго-800 (#3730a3) при
    # похожей насыщенности размах 115. Третье — белёсое, где насыщенность
    # скачет от округления одного канала.
    neutral = (saturation < 0.45
               or (saturation < 0.75 and chroma <= 32)
               or chroma <= 6)
    tint_saturation = min(0.75, max(0.45, saturation))

    if role == 'surface':
        if neutral:
            if lightness >= 0.985:
                return '#1e2127'          # белая карточка
            if lightness >= 0.93:
                return '#242830'          # чип, подложка вкладок
            if lightness >= 0.82:
                return '#2a2f37'
            if lightness >= 0.45:
                return '#343a44'
            # Уже тёмная поверхность светлой темы — блок кода в статье вики,
            # тёмная плашка, подсказка. Переворачивать её в светлую нельзя:
            # белый прямоугольник кода на графите бьёт по глазам сильнее, чем
            # что-либо ещё на экране. Оставляем тёмной, но чуть светлее
            # карточки, чтобы она от карточки отличалась.
            return '#2b3039'
        if lightness >= 0.82:
            return mix_rgb(HUE_BG_BASE, hsl_channels(hue, 0.55, tint_saturation), 0.16)
        if lightness >= 0.62:
            return mix_rgb(HUE_BG_BASE, hsl_channels(hue, 0.55, tint_saturation), 0.26)
        return None                       # насыщенный акцент остаётся собой

    if role == 'line':
        if neutral:
            if lightness >= 0.82:
                return '#2e333c'
            if lightness >= 0.45:
                return '#3a414b'
            return '#454c58'
        if lightness >= 0.62:
            return mix_rgb(HUE_LINE_BASE, hsl_channels(hue, 0.55, tint_saturation), 0.42)
        return None

    # role == 'ink'
    if lightness >= 0.55:
        # Светлый текст НЕ трогаем ни в каком виде. В светлой теме он лежит на
        # тёмном или цветном — на плашке [data-tone='dark'] в вики, на цветной
        # кнопке, — и там ничего не изменилось. А если он всё-таки на белом,
        # то это «еле заметная» подпись, и на графите она читается как есть.
        # Правило важнее, чем кажется: без него #cbd5e1 с тёмной плашки вики
        # ушёл бы в #7d8695, то есть стал бы серым по тёмно-серому.
        return None
    if neutral:
        if lightness <= 0.22:
            return '#e8ecf2'
        if lightness <= 0.45:
            return '#d5dae2'
        if lightness <= 0.68:
            return '#a8b1bf'
        return '#7d8695'
    if lightness <= 0.72:
        return hsl_hex(hue, 0.74, min(max(tint_saturation, 0.45), 0.85))
    return None


def role_of(prop):
    if prop in SURFACE_PROPS:
        return 'surface'
    if prop in LINE_PROPS:
        return 'line'
    if prop in INK_PROPS:
        return 'ink'
    return None


def strip_comments(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def split_selectors(selector):
    """Разрезать список селекторов по запятым ВЕРХНЕГО уровня.

    Наивный split(',') рвёт `:is(strong, b)` пополам и выдаёт два огрызка,
    один из которых — незакрытая скобка. Такое правило браузер отбрасывает
    целиком, вместе с соседями по строке.
    """
    parts, depth, current = [], 0, []
    for char in selector:
        if char in '([':
            depth += 1
        elif char in ')]':
            depth = max(0, depth - 1)
        if char == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append(''.join(current).strip())
    return [part for part in parts if part]


def iter_rules(css):
    """(медиазапрос | None, селектор, тело) по всему файлу.

    Разбор со счётчиком скобок, а не регуляркой: правила внутри `@media`
    обязаны и в тёмном слое остаться внутри своего `@media`. Регулярка их
    просто не видела и выносила наружу — правила из `@media print` начинали
    действовать на экране и перекрашивали то, что видно всегда.

    `@keyframes` пропускаем целиком: внутри не селекторы, а проценты.
    """
    index, length = 0, len(css)
    media = None
    media_depth = None
    depth = 0
    while index < length:
        brace = css.find('{', index)
        if brace < 0:
            break
        prelude = css[index:brace].strip()
        close = _matching_brace(css, brace)
        if close < 0:
            break
        if prelude.startswith('@'):
            name = prelude.split()[0].lower()
            if name in ('@media', '@supports'):
                inner = css[brace + 1:close]
                for _, selector, body in iter_rules(inner):
                    yield (prelude if media is None else media, selector, body)
            # @keyframes, @font-face и прочее пропускаем
            index = close + 1
            continue
        yield (media, prelude, css[brace + 1:close])
        index = close + 1


def _matching_brace(text, opening):
    depth = 0
    for position in range(opening, len(text)):
        if text[position] == '{':
            depth += 1
        elif text[position] == '}':
            depth -= 1
            if depth == 0:
                return position
    return -1


def palette_rules():
    """Тёмные двойники правил из разделов с собственной палитрой."""
    rows = []
    for rel, kind in PALETTE_SOURCES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        if kind == 'style':
            start = text.find('styleTag.textContent = `')
            end = text.find('`;', start)
            if start < 0 or end < 0:
                continue
            css = text[start + len('styleTag.textContent = `'):end]
        elif kind == 'jsx-style':
            css = '\n'.join(re.findall(r'<style>\{`(.*?)`\}', text, flags=re.S))
        else:
            css = text
        css = strip_comments(css)
        for media, selector, body in iter_rules(css):
            selector = ' '.join(selector.split())
            if not selector or selector.startswith('%') or '&' in selector:
                continue
            for declaration in body.split(';'):
                if ':' not in declaration:
                    continue
                prop, _, value = declaration.partition(':')
                prop = prop.strip().lower()
                value = value.strip()
                role = role_of(prop)
                if not role or prop.startswith('--') or not HEX_RE.search(value):
                    continue
                def swap(match, _role=role):
                    source, alpha = split_css_color(match.group(0))
                    replacement = dark_counterpart(source, _role)
                    if not replacement:
                        return match.group(0)
                    if alpha is None:
                        return replacement
                    return rgba(replacement, alpha)

                new_value = HEX_RE.sub(swap, value)
                if new_value == value:
                    continue
                for part in split_selectors(selector):
                    rows.append((media, '%s %s' % (ROOT_SEL, part),
                                 '%s: %s' % (prop, new_value)))
    return rows


# Произвольные цвета в утилитах — bg-[#f2f2f7]. Их пять на весь проект, но одна
# такая заливка держит целую колонку раздела (полотно списка чатов Wazzup),
# и без неё половина экрана остаётся светлой.
ARBITRARY_RE = re.compile(
    r'(?<![\w:/.-])((?:[a-z0-9-]+:)*)(bg|text|border|ring)-\[(#[0-9a-fA-F]{3,8})\](?![\w-])')

ARBITRARY_ROLE = {'bg': 'surface', 'border': 'line', 'ring': 'line', 'text': 'ink'}


def arbitrary_rules():
    """Тёмные двойники утилит с цветом в квадратных скобках."""
    rows = []
    seen = set()
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != 'node_modules']
        for fn in filenames:
            if not fn.endswith(('.jsx', '.js')):
                continue
            with open(os.path.join(dirpath, fn), encoding='utf-8', errors='ignore') as fh:
                text = fh.read()
            for prefix, prop, value in ARBITRARY_RE.findall(text):
                key = (prefix, prop, value.lower())
                if key in seen:
                    continue
                seen.add(key)
                replacement = dark_counterpart(value, ARBITRARY_ROLE[prop])
                if not replacement:
                    continue
                built = build_selector(prefix, '%s%s-[%s]' % (prefix, prop, value))
                if not built:
                    continue
                media, selector = built
                if prop == 'bg':
                    decl = 'background-color: %s' % replacement
                elif prop == 'ring':
                    decl = '--tw-ring-color: %s' % replacement
                elif prop == 'border':
                    decl = 'border-color: %s' % replacement
                else:
                    decl = 'color: %s' % replacement
                rows.append((media, selector, decl))
    rows.sort(key=lambda r: r[1])
    return rows


def main():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dark_theme_chrome.css'),
              encoding='utf-8') as fh:
        chrome = fh.read()

    base = rules_for(base_tokens())
    variants = rules_for(sorted(scan_tokens()))
    palette = palette_rules() + arbitrary_rules()

    header = (
        '/* ЭТОТ ФАЙЛ СОБРАН СКРИПТОМ. Правки вносить в scripts/build_dark_theme.py\n'
        '   и в scripts/dark_theme_chrome.css, затем прогнать:\n'
        '       python3 scripts/build_dark_theme.py\n'
        '\n'
        '   Тёмный режим портала. Включается атрибутом data-otp-theme="dark" на\n'
        '   <html>; файл подгружается по требованию (динамический import в App.jsx),\n'
        '   поэтому светлым пользователям он не стоит ни байта.\n'
        '\n'
        '   Слой перекрывает УЖЕ СОБРАННЫЕ утилиты Tailwind, разметка не менялась.\n'
        '   Отсюда !important: утилита и перекрытие — это один и тот же класс, и\n'
        '   спорить с ним специфичностью пришлось бы на каждой строке. */\n'
    )
    body = '\n'.join([
        header,
        chrome.rstrip(),
        '',
        '/* ═══ Утилиты Tailwind: базовая решётка ═══════════════════════════════ */',
        emit(base),
        '',
        '/* ═══ Утилиты Tailwind: модификаторы, встреченные в разделах ══════════ */',
        emit(variants),
        '',
        '/* ═══ Разделы со своей палитрой (см. PALETTE_SOURCES) ═════════════════ */',
        emit(palette),
        '',
    ])
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(body)
    print('%s: %d правил базовой решётки, %d с модификаторами, %d из разделов со своей палитрой' % (
        os.path.relpath(OUT, ROOT), len(base), len(variants), len(palette)))


if __name__ == '__main__':
    main()
