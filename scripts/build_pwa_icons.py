# -*- coding: utf-8 -*-
"""Иконки портала для установки на телефон — public/icons/*.png.

Зачем скрипт, если файлы всё равно лежат в репозитории. Иконку нельзя
«нарисовать заново» руками: знак у портала один, он живёт в
`src/components/common/sidebar-logo-mark.svg`, и иконка обязана быть ИМ, а не
похожим кружком. Скрипт — это записанный рецепт: поменяется знак или палитра —
прогон повторяется, и все размеры пересобираются одинаково.

Что получается:
  * `icon-192.png`, `icon-512.png` — манифест, purpose "any maskable";
  * `apple-touch-icon.png` (180) — плитка на домашнем экране iPhone.

Три решения, которые стоит объяснить.

1. БЕЛЫЙ ЗНАК НА ГРАДИЕНТЕ, а не градиентный знак на белом (как в favicon).
   Домашний экран телефона — это чужой фон: обои, тёмная тема, светлая тема.
   Прозрачный или белый фон иконки iOS заливает чёрным, а Android кладёт на
   свою белую плашку, и знак теряется. Плитка с собственным фоном выглядит
   приложением на любом экране. Палитра снята пипеткой с favicon.ico
   (синий #22409B → фиолетовый #7B2E92), знак — тот же, что в сайдбаре.

2. ЗНАК ЗАНИМАЕТ 68 % ПЛИТКИ. Иконка объявлена maskable: Android обрезает её
   под форму своей темы (круг, «капля», квадрат со скруглением) и гарантирует
   неприкосновенным только центральный круг диаметром 80 %, то есть радиусом
   40 % от стороны. Сам знак круглый и занимает 77 % своего холста, поэтому при
   68 % его радиус — 26 % стороны: до границы неприкосновенного круга остаётся
   полторы своих толщины. Та же картинка честно работает и как maskable, и как
   обычная, поэтому файлов три, а не шесть.
   Первая версия была 56 % — на домашнем экране рядом с чужими значками знак
   читался мелким: у системных иконок глиф занимает как раз около двух третей.

3. РЕНДЕР ЧЕРЕЗ CHROME, а не через питоновский растеризатор SVG. cairosvg тянет
   за собой системный cairo, которого нет ни на одной из машин проекта и не
   будет на Render. Chrome здесь и так есть (им же снимаются скриншоты вики),
   а рисует он ровно тем движком, которым потом покажет знак в сайдбаре.

Запуск:  python3 scripts/build_pwa_icons.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = os.path.join(ROOT, 'src', 'components', 'common', 'sidebar-logo-mark.svg')
OUT_DIR = os.path.join(ROOT, 'public', 'icons')

MASTER = 1024                     # мастер-рендер, из него уменьшаются остальные
MARK_SCALE = 0.68                 # доля плитки под знак (см. пункт 2 в шапке)
GRADIENT = 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)'

# (имя файла, сторона в пикселях)
SIZES = [
    ('icon-192.png', 192),
    ('icon-512.png', 512),
    ('apple-touch-icon.png', 180),
]

# Где искать Chrome. Первый существующий и берём.
CHROME_CANDIDATES = [
    os.path.expanduser(
        '~/.cache/puppeteer/chrome-headless-shell/mac_arm-151.0.7922.47/'
        'chrome-headless-shell-mac-arm64/chrome-headless-shell'
    ),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    # Свежая версия chrome-headless-shell из кэша puppeteer — версия в имени
    # папки меняется с каждым обновлением, поэтому ищем перебором.
    cache = os.path.expanduser('~/.cache/puppeteer/chrome-headless-shell')
    if os.path.isdir(cache):
        for version in sorted(os.listdir(cache), reverse=True):
            for root, _dirs, files in os.walk(os.path.join(cache, version)):
                if 'chrome-headless-shell' in files:
                    return os.path.join(root, 'chrome-headless-shell')
    found = shutil.which('google-chrome') or shutil.which('chromium')
    if found:
        return found
    raise SystemExit('Chrome не найден — иконки собрать нечем.')


def build_page():
    """HTML с фирменным знаком: белые заливки внутри SVG остаются белыми."""
    svg = open(MARK, encoding='utf-8').read()
    inner = svg.split('>', 1)[1].rsplit('</svg>', 1)[0]
    side = int(MASTER * MARK_SCALE)
    return (
        '<!doctype html><html><body style="margin:0">'
        '<div style="width:%dpx;height:%dpx;background:%s;'
        'display:flex;align-items:center;justify-content:center">'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 389 389" '
        'width="%d" height="%d">%s</svg>'
        '</div></body></html>' % (MASTER, MASTER, GRADIENT, side, side, inner)
    )


def render_master(chrome, workdir):
    page = os.path.join(workdir, 'icon.html')
    shot = os.path.join(workdir, 'master.png')
    with open(page, 'w', encoding='utf-8') as handle:
        handle.write(build_page())
    subprocess.run(
        [
            chrome, '--headless', '--disable-gpu', '--hide-scrollbars',
            '--force-device-scale-factor=1',
            '--window-size=%d,%d' % (MASTER, MASTER),
            '--screenshot=%s' % shot,
            'file://%s' % page,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not os.path.exists(shot):
        raise SystemExit('Chrome не отдал снимок — иконки не собраны.')
    return shot


def main():
    chrome = find_chrome()
    os.makedirs(OUT_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as workdir:
        master = Image.open(render_master(chrome, workdir))
        # RGB, а не RGBA: плитка обязана быть непрозрачной — прозрачность на
        # домашнем экране iPhone заливается чёрным.
        master = master.convert('RGB')
        for name, side in SIZES:
            icon = master.resize((side, side), Image.LANCZOS)
            path = os.path.join(OUT_DIR, name)
            icon.save(path, format='PNG', optimize=True)
            print('%-24s %4dx%-4d %6.1f КБ' % (
                name, side, side, os.path.getsize(path) / 1024.0))


if __name__ == '__main__':
    sys.exit(main())
