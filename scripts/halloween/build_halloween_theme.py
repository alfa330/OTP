"""Хэллоуин-2026: оранжевые акценты поверх тёмного слоя портала.

Макет владельца (30.10.2026): портал тёмный, акценты — оранжевые. Тёмный слой
(src/theme-dark.css, scripts/build_dark_theme.py) уже красит поверхности; здесь
синие и индиго-акценты, которые он оставляет «собой», становятся тыквенными:
сплошные заливки (кнопки, активные плитки), текст-ссылки, рамки и кольца фокуса,
градиенты, иконки. Мягкие подложки 50–300 не трогаем: голубой чип статуса рядом
с оранжевым «Срочно» должен остаться другим цветом — это смысл, а не украшение.

Вес селекторов подобран так, чтобы порядок загрузки двух файлов был неважен:
    html[data-otp-theme="dark"]:where([data-otp-halloween]) body .утилита  (0,2,2)
бьёт базовое правило тёмного слоя (0,2,1) и уступает его :hover (0,3,1) — то
есть «синий → наведение серым» продолжает работать, а наш :hover (0,3,2)
бьёт его :hover. Праздничный атрибут стоит в :where — веса не добавляет.

Запуск: python3 scripts/halloween/build_halloween_theme.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from build_dark_theme import ANCESTOR, PSEUDO, SCREENS, escape_class  # noqa: E402

SRC = os.path.join(ROOT, 'src')
OUT = os.path.join(SRC, 'components', 'seasonal', 'halloween', 'halloween-theme.css')
ROOT_SEL = 'html[data-otp-theme="dark"]:where([data-otp-halloween]) body'
VARS_SEL = 'html[data-otp-theme="dark"][data-otp-halloween]'

ORANGE = {
    '300': (253, 186, 116), '400': (251, 146, 60), '500': (249, 115, 22), '600': (234, 88, 12),
    '700': (194, 65, 12), '800': (154, 52, 18), '900': (124, 45, 18), '950': (67, 20, 7),
}
TEXT_ON_DARK = (253, 186, 116)       # orange-300: ссылка/значок на графите, контраст ~10
SOLID = ('400', '500', '600', '700', '800', '900', '950')

TOKEN_RE = re.compile(
    r'(?<![\w:/.-])'
    r'((?:[a-z0-9-]+:)*)'
    r'(bg|text|border|ring|outline|from|via|to|fill|stroke|accent|shadow)-'
    r'(blue|indigo)-(50|[1-9]00|950)'
    r'(/\d{1,3})?'
    r'(?![\w/-])'
)

EXTRA_PSEUDO = {'checked': ':checked', 'aria-selected': '[aria-selected="true"]',
                'aria-pressed': '[aria-pressed="true"]', 'aria-current': '[aria-current="page"]'}
EXTRA_ANCESTOR = {'group-focus': '.group:focus ', 'group-focus-within': '.group:focus-within ',
                  'group-active': '.group:active '}
PEER = {'peer-checked': '.peer:checked ~ ', 'peer-focus': '.peer:focus ~ ', 'peer-hover': '.peer:hover ~ '}


def color(rgb, alpha=None):
    if alpha is None:
        return '#%02x%02x%02x' % rgb
    return 'rgba(%d, %d, %d, %s)' % (rgb + (('%.2f' % alpha).rstrip('0').rstrip('.'),))


def declaration(prop, shade, alpha):
    """Объявление для одной утилиты; None — оставить как есть."""
    if prop == 'bg':
        return 'background-color: %s' % color(ORANGE[shade], alpha) if shade in SOLID else None
    if prop == 'text':
        return 'color: %s' % color(TEXT_ON_DARK, alpha) if shade in SOLID else None
    if prop in ('border', 'outline'):
        if shade not in SOLID:
            return None
        name = 'border-color' if prop == 'border' else 'outline-color'
        return '%s: %s' % (name, color(ORANGE[shade], alpha if alpha is not None else 0.85))
    if prop == 'ring':
        # Кольца — фокус и выбор, не статусы: тыквенные все, светлые — прозрачнее.
        if shade in ('50', '100', '200'):
            return '--tw-ring-color: rgb(249 115 22 / %s)' % ('%.2f' % (alpha if alpha is not None else 0.35))
        rgb = ORANGE['500']
        opacity = ('%.2f' % alpha) if alpha is not None else 'var(--tw-ring-opacity, 1)'
        return '--tw-ring-color: rgb(%d %d %d / %s)' % (rgb + (opacity,))
    if prop == 'from':
        return '--tw-gradient-from: %s var(--tw-gradient-from-position)' % color(ORANGE[shade], alpha) if shade in ('300',) + SOLID else None
    if prop == 'via':
        if shade not in ('300',) + SOLID:
            return None
        return ('--tw-gradient-stops: var(--tw-gradient-from), %s var(--tw-gradient-via-position), '
                'var(--tw-gradient-to)' % color(ORANGE[shade], alpha))
    if prop == 'to':
        return '--tw-gradient-to: %s var(--tw-gradient-to-position)' % color(ORANGE[shade], alpha) if shade in ('300',) + SOLID else None
    if prop in ('fill', 'stroke'):
        return '%s: %s' % (prop, color(ORANGE[shade], alpha)) if shade in SOLID else None
    if prop == 'accent':
        return 'accent-color: %s' % color(ORANGE['500']) if shade in SOLID else None
    if prop == 'shadow':
        return '--tw-shadow-color: %s' % color(ORANGE['500'], alpha if alpha is not None else 0.35)
    return None


def selector_for(prefix, class_name, prop):
    media, ancestor, pseudo = None, '', ''
    for variant in filter(None, prefix.split(':')):
        if variant in SCREENS:
            media = '@media (min-width: %dpx)' % SCREENS[variant]
        elif variant in ANCESTOR:
            ancestor = ANCESTOR[variant]
        elif variant in EXTRA_ANCESTOR:
            ancestor = EXTRA_ANCESTOR[variant]
        elif variant in PEER:
            ancestor = PEER[variant]
        elif variant in PSEUDO:
            pseudo += PSEUDO[variant]
        elif variant in EXTRA_PSEUDO:
            pseudo += EXTRA_PSEUDO[variant]
        else:
            return None
    # Текст на мягкой синей подложке (чип статуса) остаётся парой к подложке.
    guard = ':where(:not([class*="bg-blue-"]):not([class*="bg-indigo-"]))' if prop == 'text' else ''
    return media, '%s %s.%s%s%s' % (ROOT_SEL, ancestor, escape_class(class_name), guard, pseudo)


def scan():
    found = set()
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in ('node_modules', 'seasonal')]
        for fn in filenames:
            if not fn.endswith(('.jsx', '.js')):
                continue
            with open(os.path.join(dirpath, fn), encoding='utf-8', errors='ignore') as fh:
                for m in TOKEN_RE.finditer(fh.read()):
                    found.add(m.groups()[:4] + ((m.group(5) or ''),))
    # Базовая решётка — на случай утилиты, которой пока нет в разметке.
    for family in ('blue', 'indigo'):
        for shade in SOLID[:5]:
            for prop in ('bg', 'text', 'border', 'ring'):
                found.add(('', prop, family, shade, ''))
    return found


CHROME = '''/* Сайдбар: та же графитовая плита тёмного слоя, выбранный раздел — тыквенный.
   Телефон: акцент нижнего бара и шторки со ссылками («Все отделы», «Имя»). */
%(vars)s {
    --otp-bar-card: #1c1714;
    --otp-bar-hover: #2b1f18;
    --otp-bar-select: #c2410c;
    --otp-bar-select-ink: #fff7ed;
    --otp-bar-knob: #2b1f18;
    --mtb-accent: #fb923c;
    --sheet-accent: #f97316;
}

/* Экран входа — ночь: тёплый отсвет снизу, лиловая дымка сверху. */
%(vars)s .auth-screen {
    background-image:
        radial-gradient(90%% 55%% at 50%% 112%%, rgba(234, 88, 12, 0.3), rgba(234, 88, 12, 0) 65%%),
        radial-gradient(70%% 45%% at 88%% -6%%, rgba(124, 58, 237, 0.22), rgba(124, 58, 237, 0) 70%%),
        linear-gradient(180deg, #0f0c13 0%%, #17111a 100%%) !important;
}

/* Логотип: на месте знака iCORE — тыква (наброски владельца). Здесь, а не в
   слое украшений: этот файл main.jsx грузит сразу, и знак не сменится на
   глазах через секунду после загрузки. Правила не требуют тёмной темы. Плашка
   со знаком на входе сохраняет рамку и размер — свой svg только прячется. */
html[data-otp-halloween] .auth-card h1 > span > span:last-child {
    background-image: url('../../assistant/pumpkin/assets/mascot-logo.webp');
    background-position: center;
    background-size: 88%%;
    background-repeat: no-repeat;
}

html[data-otp-halloween] .auth-card h1 > span > span:last-child > svg {
    visibility: hidden;
}

/* Сайдбар: праздничная версия логотипа целиком (оранжевая плашка, белые буквы,
   тёмное место под знак) и тыква поверх тёмного места. Картинку логотипа
   прячем, а не подменяем: кнопка «4 You» поверх знака остаётся на месте. */
html[data-otp-halloween] .sidebar-logo-full {
    background: url('./assets/sidebar-logo-halloween.svg') center / 100%% 100%% no-repeat;
}

html[data-otp-halloween] .sidebar-logo-full > img {
    visibility: hidden;
}

html[data-otp-halloween] .sidebar-logo-full::after {
    content: '';
    position: absolute;
    top: 3px;
    right: 4px;
    bottom: 3px;
    width: 52px;
    background: url('../../assistant/pumpkin/assets/mascot-logo.webp') center / contain no-repeat;
    pointer-events: none;
}

/* Колокол в свёрнутом сайдбаре: плитка значка в styles.css — синий #2563eb. */
html[data-otp-halloween] body:not(.mobile-shell) .sidebar.collapsed:not(:hover):not(:has(.sidebar-holds-open)) .sidebar-bell-slot > div > button > svg:first-child {
    background: #ea580c;
}

/* Карточка входа на ночном фоне: тонкая тёплая кайма и отсвет, иначе её край
   почти не отличить от фона. */
%(vars)s .auth-card {
    box-shadow: 0 0 0 1px rgba(251, 146, 60, 0.16), 0 24px 70px rgba(0, 0, 0, 0.55), 0 0 90px rgba(234, 88, 12, 0.12) !important;
}

/* Поля входа одинаковые. В тёмном слое общее правило input[type=text] (из
   палитры разделов) даёт полю «Логин» рамку и свой фон, а «Пароль» под него не
   попадает — на входе тёмную тему раньше не видел никто, разнобой всплыл сейчас. */
%(vars)s .auth-card input {
    border: 0 !important;
    background: #22262d !important;
}

%(vars)s .auth-card input:focus {
    background: #1e2127 !important;
}

/* Выбор облика помощника: выбранная строка и кружок — тёплые, а не индиго. */
%(vars)s .aorb-appearance-option:has(input:checked) {
    background: #3a2318;
    color: #fed7aa;
}

%(vars)s .aorb-appearance-option input {
    accent-color: #f97316;
}

%(vars)s .aorb-appearance-trigger:hover,
%(vars)s .aorb-appearance-trigger[aria-expanded="true"] {
    background: #3a2318;
    color: #fdba74;
}

@media print {
    html[data-otp-halloween] .sidebar-logo-full {
        background: none;
    }

    html[data-otp-halloween] .sidebar-logo-full > img {
        visibility: visible;
    }

    html[data-otp-halloween] .sidebar-logo-full::after {
        content: none;
    }
}

/* Полотно разделов: тёплый отсвет снизу — виден в просветах между карточками. */
%(vars)s .main-content {
    background-image: radial-gradient(120%% 50%% at 50%% 118%%, rgba(234, 88, 12, 0.14), rgba(234, 88, 12, 0) 62%%) !important;
}
'''


def main():
    tokens = scan()
    rows, skipped = [], 0
    for prefix, prop, family, shade, alpha in tokens:
        a = int(alpha[1:]) / 100.0 if alpha else None
        decl = declaration(prop, shade, a)
        if not decl:
            continue
        built = selector_for(prefix, '%s%s-%s-%s%s' % (prefix, prop, family, shade, alpha), prop)
        if not built:
            skipped += 1
            continue
        rows.append(built + (decl,))
    rows.sort(key=lambda r: (r[0] or '', r[1]))
    lines = [
        '/* СОБРАН СКРИПТОМ scripts/halloween/build_halloween_theme.py — руками не править.',
        '   Хэллоуин-2026: оранжевые акценты поверх тёмного слоя (до конца 1 ноября). */',
        '',
        CHROME % {'vars': VARS_SEL},
    ]
    for media, selector, decl in [r for r in rows if r[0] is None]:
        lines.append('%s { %s !important; }' % (selector, decl))
    for media in sorted({r[0] for r in rows if r[0] is not None}):
        lines.append('')
        lines.append('%s {' % media)
        for _, selector, decl in [r for r in rows if r[0] == media]:
            lines.append('    %s { %s !important; }' % (selector, decl))
        lines.append('}')
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    print('%s: %d правил, пропущено модификаторов: %d' % (os.path.relpath(OUT, ROOT), len(rows), skipped))


if __name__ == '__main__':
    main()
