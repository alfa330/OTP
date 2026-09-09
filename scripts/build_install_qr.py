# -*- coding: utf-8 -*-
"""QR-код на портал для инструкции по установке —
src/components/common/install-qr.svg.

Зачем он в инструкции. Инструкцию нередко читают НЕ на том устройстве, куда
ставят: человек сидит за компьютером, а значок нужен на телефоне. Диктовать
адрес голосом или пересылать себе ссылку в мессенджер (откуда установка вообще
не работает — там встроенный браузер) — лишние шаги, на которых бросают. Камера
телефона открывает портал за одно наведение.

Почему файл собирается заранее и лежит в репозитории, а не рисуется на лету.
Кодировщик QR — это лишняя зависимость в бандле ради картинки, которая меняется
раз в жизнь: адрес портала постоянный. Готовый SVG весит несколько килобайт,
кэшируется как обычная статика и не требует ни строчки кода на клиенте.

Три решения по самому коду.

1. УРОВЕНЬ КОРРЕКЦИИ H (30 %). В середине кода лежит наш знак, то есть часть
   модулей закрыта — в нашем случае 12 % тёмных. При уровне ниже код перестал
   бы читаться на половине телефонов; H держит до 30 %, запас двукратный.
   Проверено не на глаз: собранный SVG отрисован в PNG и прочитан декодером
   jsQR (он же лежит в node_modules) — вернул ровно этот адрес.

2. ЗНАК ЛЕЖИТ НА БЕЛОЙ ПОДЛОЖКЕ со скруглением. Без неё тёмные модули под
   краями знака сливаются с ним, и камере труднее найти границы.

3. ЦВЕТ МОДУЛЕЙ — slate-900 (#0f172a), а не чистый чёрный: тот же цвет текста,
   что во всех окнах портала. Контраст с белым 17:1, для распознавания этого
   более чем достаточно.

Нужен segno (`pip install segno`) — чистый Python, без зависимостей. В
requirements его нет намеренно: он нужен ровно здесь и только при смене адреса
портала.

Запуск:  python3 scripts/build_install_qr.py
"""

import os
import sys

try:
    import segno
except ImportError:  # pragma: no cover — инструмент разработчика
    raise SystemExit('Нужен segno: pip install segno')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = os.path.join(ROOT, 'src', 'components', 'common', 'sidebar-logo-mark.svg')
OUT = os.path.join(ROOT, 'src', 'components', 'common', 'install-qr.svg')

# Адрес портала. Сменится домен — прогон повторить.
URL = 'https://alfa330.github.io/OTP/'

SIZE = 512                 # сторона картинки в единицах viewBox
QUIET_ZONE = 4             # модулей белого поля по краям — столько требует стандарт;
                           # с двумя код читался глазом, но не читался декодером
MODULE_COLOR = '#0f172a'
LOGO_SHARE = 0.26          # доля стороны под белую подложку со знаком
GRADIENT = (('0', '#22409B'), ('0.45', '#4A3A96'), ('1', '#7B2E92'))


def mark_paths():
    """Контур фирменного знака из того же файла, что в сайдбаре."""
    with open(MARK, encoding='utf-8') as handle:
        svg = handle.read()
    inner = svg.split('>', 1)[1].rsplit('</svg>', 1)[0]
    return ' '.join(inner.split()).replace('fill="#fff"', 'fill="url(#markGradient)"')


def build():
    qr = segno.make(URL, error='h')
    matrix = [[bool(cell) for cell in row] for row in qr.matrix]
    count = len(matrix) + QUIET_ZONE * 2
    step = SIZE / count

    # Середина кода: сколько модулей закрывает подложка со знаком.
    logo_side = SIZE * LOGO_SHARE
    logo_from = (SIZE - logo_side) / 2
    logo_to = logo_from + logo_side

    rects = []
    hidden = 0
    for row_index, row in enumerate(matrix):
        for col_index, is_dark in enumerate(row):
            if not is_dark:
                continue
            x = (col_index + QUIET_ZONE) * step
            y = (row_index + QUIET_ZONE) * step
            # Модули под знаком не рисуем вовсе — иначе они просвечивают из-под
            # его полупрозрачных краёв при масштабировании.
            if x + step > logo_from and x < logo_to and y + step > logo_from and y < logo_to:
                hidden += 1
                continue
            # Модуль рисуется с крошечным перехлёстом: без него сглаживание
            # оставляет между соседями светлые швы, и декодер видит не сплошной
            # блок, а решётку из точек. Скругление углов по той же причине
            # убрано — красивее, но нечитаемо.
            rects.append(
                '<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f"/>'
                % (x, y, step + 0.6, step + 0.6)
            )

    stops = ''.join(
        '<stop offset="%s" stop-color="%s"/>' % (offset, color) for offset, color in GRADIENT
    )
    mark_side = logo_side * 0.72
    mark_offset = (SIZE - mark_side) / 2

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %(size)d %(size)d" '
        'width="%(size)d" height="%(size)d" role="img" '
        'aria-label="QR-код: портал iCORE">'
        '<defs><linearGradient id="markGradient" x1="0" y1="0" x2="0.72" y2="1">%(stops)s'
        '</linearGradient></defs>'
        '<rect width="%(size)d" height="%(size)d" rx="%(radius).1f" fill="#ffffff"/>'
        '<g fill="%(color)s">%(rects)s</g>'
        '<rect x="%(lx).2f" y="%(lx).2f" width="%(lside).2f" height="%(lside).2f" '
        'rx="%(lradius).2f" fill="#ffffff"/>'
        '<svg x="%(mx).2f" y="%(mx).2f" width="%(mside).2f" height="%(mside).2f" '
        'viewBox="0 0 389 389">%(mark)s</svg>'
        '</svg>'
    ) % {
        'size': SIZE,
        'radius': SIZE * 0.06,
        'color': MODULE_COLOR,
        'rects': ''.join(rects),
        'stops': stops,
        'lx': logo_from,
        'lside': logo_side,
        'lradius': logo_side * 0.28,
        'mx': mark_offset,
        'mside': mark_side,
        'mark': mark_paths(),
    }

    with open(OUT, 'w', encoding='utf-8') as handle:
        handle.write(svg)

    total = sum(sum(1 for cell in row if cell) for row in matrix)
    print('%s: версия %d, модулей %d, под знаком скрыто %d (%.1f %%), %.1f КБ' % (
        os.path.relpath(OUT, ROOT), qr.version, total, hidden,
        hidden * 100.0 / total, os.path.getsize(OUT) / 1024.0))


if __name__ == '__main__':
    sys.exit(build())
