# -*- coding: utf-8 -*-
"""Карточка обращения картинкой — то, что уходит в Telegram-группу.

Зачем картинка, а не текст. Разметка Telegram знает жирный, курсив и цитату —
и всё. Плашки, галочки в кружках, разделители, спокойный серый фон под
блоками текстом не рисуются, а именно так СЗоВ и попросила: в задаче #206
приложен макет, где обращение выглядит карточкой (задачу поставила Кастек
Гаухар, формат согласован владельцем).

Что при этом НЕ теряется. С картинки нельзя скопировать ИИН и нельзя нажать
на ссылку, поэтому картинка идёт с подписью: номер обращения ссылкой и данные
водителя строками (crm/telegram.py::build_card_caption). Ровно так это и
нарисовано на макете — карточка, а под ней текст с ИИН, парком и периодом.

Чем рисуем. Pillow — он и так в зависимостях, и он не тянет ни браузера, ни
системных библиотек: рендер HTML на Render означал бы Chromium в образе ради
одной картинки. Шрифт лежит рядом (crm/fonts) и не берётся из системы: образ
Render не даёт никаких гарантий по шрифтам, а «на проде вместо букв квадраты»
узнавать из рабочей группы поздно.

Модуль чистый: ни базы, ни сети, ни Flask. Поэтому раскладка проверяется
тестами (tests/test_crm_card.py), а не глазами в чате.
"""

import io
import os

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')

# Рисуем в двойном размере и уменьшаем: Telegram показывает фото шириной около
# 500 точек на обычном экране и вдвое больше на «retina». Без запаса текст на
# телефоне выглядит замыленным — а читать его будут в основном с телефона.
SCALE = 2
WIDTH = 620          # логическая ширина карточки
PAD = 26             # поля
GAP = 16             # расстояние между блоками

# Палитра — та же slate, что во всём интерфейсе (src/components/ui/ios.jsx).
INK = (15, 23, 42)          # slate-900
MUTED = (100, 116, 139)     # slate-500
FAINT = (148, 163, 184)     # slate-400
LINE = (226, 232, 240)      # slate-200
CARD = (255, 255, 255)

GREEN = (16, 185, 129)
GREEN_BG = (236, 253, 245)
ROSE = (244, 63, 94)
ROSE_BG = (255, 241, 242)
SLATE_BG = (241, 245, 249)
AMBER = (245, 158, 11)
AMBER_BG = (255, 251, 235)
AMBER_INK = (180, 83, 9)
BADGE_BG = (254, 226, 226)
BADGE_INK = (185, 28, 28)

# Знаки, которыми crm.scenarios помечает проверенные пункты. Здесь они
# превращаются в рисунок: эмодзи Pillow не рисует (цветные шрифты требуют
# libraqm и системного Noto Color Emoji), да и кружок с галочкой получается
# ровнее нарисованного шрифтом.
MARK_STYLE = {
    '✅': ('check', GREEN, GREEN_BG),
    '❌': ('cross', ROSE, ROSE_BG),
    '❔': ('question', FAINT, SLATE_BG),
}
DEFAULT_MARK = ('check', GREEN, GREEN_BG)


def _font(name, size):
    return ImageFont.truetype(os.path.join(FONTS_DIR, name), int(round(size * SCALE)))


class _Fonts(object):
    """Шрифты создаются один раз на процесс: truetype() читает файл с диска."""

    def __init__(self):
        self._cache = {}

    def get(self, weight, size):
        key = (weight, size)
        if key not in self._cache:
            self._cache[key] = _font('Roboto-%s.ttf' % weight, size)
        return self._cache[key]

    def regular(self, size):
        return self.get('Regular', size)

    def medium(self, size):
        return self.get('Medium', size)

    def bold(self, size):
        return self.get('Bold', size)


FONTS = _Fonts()


def _px(value):
    return int(round(value * SCALE))


def _text_width(draw, text, font):
    return draw.textlength(str(text), font=font) / SCALE


def _wrap(draw, text, font, limit):
    """Перенос по словам. Слово шире строки не режем — рвать ИИН или номер
    документа пополам хуже, чем выйти за поле: такое значение потом не найти."""
    words = str(text or '').split()
    if not words:
        return ['']
    lines, current = [], words[0]
    for word in words[1:]:
        probe = current + ' ' + word
        if _text_width(draw, probe, font) <= limit:
            current = probe
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


class _Canvas(object):
    """Двухпроходная раскладка: сначала считаем высоту, потом рисуем.

    Высота карточки заранее не известна — у «ошибки Sapar» семь строк ответов,
    у «статуса подписания» одна. Заводить холст с запасом и обрезать нельзя:
    обрезка по пустому месту даёт разную высоту полей снизу.
    """

    def __init__(self, draw=None):
        self.draw = draw
        self.y = 0

    @property
    def dry(self):
        return self.draw is None

    def space(self, height):
        self.y += height


def _rounded(canvas, box, radius, fill, outline=None):
    if canvas.dry:
        return
    x0, y0, x1, y1 = box
    canvas.draw.rounded_rectangle(
        (_px(x0), _px(y0), _px(x1), _px(y1)), radius=_px(radius),
        fill=fill, outline=outline, width=SCALE if outline else 0)


def _write(canvas, xy, text, font, fill):
    if canvas.dry:
        return
    canvas.draw.text((_px(xy[0]), _px(xy[1])), str(text), font=font, fill=fill)


def _mark_icon(canvas, x, y, size, kind, color):
    """Кружок со знаком. Рисуем линиями, а не шрифтом: эмодзи Pillow не берёт."""
    if canvas.dry:
        return
    box = (_px(x), _px(y), _px(x + size), _px(y + size))
    canvas.draw.ellipse(box, fill=color)
    pen = max(2, _px(1.6))
    left, top = _px(x), _px(y)
    step = _px(size)
    if kind == 'check':
        canvas.draw.line([(left + step * 0.27, top + step * 0.52),
                          (left + step * 0.44, top + step * 0.69),
                          (left + step * 0.75, top + step * 0.33)],
                         fill=CARD, width=pen, joint='curve')
    elif kind == 'cross':
        canvas.draw.line([(left + step * 0.32, top + step * 0.32),
                          (left + step * 0.68, top + step * 0.68)], fill=CARD, width=pen)
        canvas.draw.line([(left + step * 0.68, top + step * 0.32),
                          (left + step * 0.32, top + step * 0.68)], fill=CARD, width=pen)
    else:
        font = FONTS.bold(size * 0.62)
        glyph = '!' if kind == 'alert' else '?'
        width = canvas.draw.textlength(glyph, font=font)
        canvas.draw.text((left + (step - width) / 2, top + step * 0.16), glyph,
                         font=font, fill=CARD)


# ─────────────────────────────────────────────────────────────────────────────
# Блоки
# ─────────────────────────────────────────────────────────────────────────────

def _draw_header(canvas, *, heading, subtitle, badge, measure):
    inner = WIDTH - PAD * 2
    if badge:
        font = FONTS.bold(10)
        text = badge.upper()
        width = _text_width(measure, text, font)
        _rounded(canvas, (PAD, canvas.y, PAD + width + 20, canvas.y + 20), 10, BADGE_BG)
        _write(canvas, (PAD + 10, canvas.y + 4.5), text, font, BADGE_INK)
        canvas.space(20 + 12)

    font = FONTS.bold(19)
    for line in _wrap(measure, heading, font, inner):
        _write(canvas, (PAD, canvas.y), line, font, INK)
        canvas.space(25)
    canvas.space(3)

    font = FONTS.regular(12.5)
    _write(canvas, (PAD, canvas.y), subtitle, font, MUTED)
    canvas.space(17)

    canvas.space(14)
    if not canvas.dry:
        canvas.draw.line([(_px(PAD), _px(canvas.y)), (_px(WIDTH - PAD), _px(canvas.y))],
                         fill=LINE, width=SCALE)


def _draw_warning(canvas, items):
    for text in items:
        _rounded(canvas, (PAD, canvas.y, WIDTH - PAD, canvas.y + 30), 9, AMBER_BG)
        _mark_icon(canvas, PAD + 11, canvas.y + 8, 14, 'alert', AMBER)
        _write(canvas, (PAD + 33, canvas.y + 7.5), text, FONTS.medium(12.5), AMBER_INK)
        canvas.space(30 + 6)
    canvas.space(-6)


def _draw_rows(canvas, rows, measure, title=None):
    """Пары «подпись — значение». Подпись бледная, значение тёмное: перечень
    читается по столбцу ответов, а не построчно целиком."""
    if title:
        _write(canvas, (PAD, canvas.y), title, FONTS.bold(13), INK)
        canvas.space(23)
    label_font, value_font = FONTS.regular(12.5), FONTS.medium(13)
    label_width = max([_text_width(measure, row['label'] + ':', label_font)
                       for row in rows] + [0])
    label_width = min(label_width, WIDTH * 0.42)
    value_x = PAD + label_width + 10
    for row in rows:
        _write(canvas, (PAD, canvas.y + 0.5), row['label'] + ':', label_font, MUTED)
        lines = _wrap(measure, row['value'], value_font, WIDTH - PAD - value_x)
        for index, line in enumerate(lines):
            _write(canvas, (value_x, canvas.y), line, value_font, INK)
            if index < len(lines) - 1:
                canvas.space(18)
        canvas.space(21)
    canvas.space(-4)


def _draw_checks(canvas, block, measure):
    heading = 'Проверено оператором — %d из %d' % (block['confirmed'], block['total'])
    _write(canvas, (PAD, canvas.y), heading, FONTS.bold(13), INK)
    canvas.space(23)
    font = FONTS.regular(12.5)
    for row in block['rows']:
        kind, color, background = MARK_STYLE.get(row['mark'], DEFAULT_MARK)
        text = row['text'] + (' — ' + row['note'] if row.get('note') else '')
        lines = _wrap(measure, text, font, WIDTH - PAD * 2 - 46)
        height = 28 + (len(lines) - 1) * 17
        _rounded(canvas, (PAD, canvas.y, WIDTH - PAD, canvas.y + height), 9, background)
        _mark_icon(canvas, PAD + 10, canvas.y + 7, 15, kind, color)
        for index, line in enumerate(lines):
            _write(canvas, (PAD + 35, canvas.y + 7 + index * 17), line, font, INK)
        canvas.space(height + 5)
    canvas.space(-5)


def _draw_actions(canvas, block, measure):
    font = FONTS.regular(12.5)
    for title, items, color in (('Выполнено', block['done'], GREEN),
                                ('Не выполнено', block['undone'], ROSE)):
        if not items:
            continue
        _mark_icon(canvas, PAD, canvas.y + 1.5, 14, 'check' if color == GREEN else 'cross', color)
        _write(canvas, (PAD + 21, canvas.y), title + ':', FONTS.medium(12.5), INK)
        offset = 21 + _text_width(measure, title + ':', FONTS.medium(12.5)) + 6
        lines = _wrap(measure, ' · '.join(items), font, WIDTH - PAD * 2 - offset)
        for index, line in enumerate(lines):
            _write(canvas, (PAD + (offset if index == 0 else 21), canvas.y + index * 17),
                   line, font, MUTED)
        canvas.space(len(lines) * 17 + 6)
    canvas.space(-6)


def _draw_text(canvas, text, measure):
    font = FONTS.medium(14)
    for line in _wrap(measure, text, font, WIDTH - PAD * 2):
        _write(canvas, (PAD, canvas.y), line, font, INK)
        canvas.space(20)
    canvas.space(-4)


DRAWERS = {
    'warning': lambda canvas, block, measure: _draw_warning(canvas, block['items']),
    'data': lambda canvas, block, measure: _draw_rows(canvas, block['rows'], measure),
    'list': lambda canvas, block, measure: _draw_rows(canvas, block['rows'], measure),
    'sapar': lambda canvas, block, measure: _draw_rows(canvas, block['rows'], measure,
                                                       title=block.get('title')),
    # Что сказал справочник компании. Рисуется теми же строками, что и снимок
    # Sapar: это один жанр — «не оператор ответил, а система посмотрела».
    'table': lambda canvas, block, measure: _draw_rows(canvas, block['rows'], measure,
                                                       title=block.get('title')),
    'checks': _draw_checks,
    'actions': _draw_actions,
    'text': lambda canvas, block, measure: _draw_text(canvas, block['text'], measure),
}


def _layout(canvas, *, heading, subtitle, blocks, badge, measure):
    canvas.space(PAD)
    _draw_header(canvas, heading=heading, subtitle=subtitle, badge=badge, measure=measure)
    for block in blocks:
        drawer = DRAWERS.get(block.get('kind'))
        if not drawer:
            continue
        canvas.space(GAP)
        drawer(canvas, block, measure)
    canvas.space(PAD)
    return canvas.y


def render_ticket_card(*, heading, subtitle, blocks, badge='Требуется действие'):
    """PNG карточки обращения. Возвращает bytes.

    blocks — то, что отдал crm.scenarios.body_blocks. Блок данных водителя сюда
    НЕ передаётся: он уходит подписью к картинке, потому что ИИН из картинки не
    скопировать (так же и на макете СЗоВ — карточка, под ней текст с данными).
    """
    measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    height = _layout(_Canvas(), heading=heading, subtitle=subtitle, blocks=blocks,
                     badge=badge, measure=measure)
    image = Image.new('RGB', (_px(WIDTH), _px(height)), CARD)
    draw = ImageDraw.Draw(image)
    _layout(_Canvas(draw), heading=heading, subtitle=subtitle, blocks=blocks,
            badge=badge, measure=measure)

    buffer = io.BytesIO()
    # optimize=True режет вес примерно вдвое: карточка почти вся однотонная, а в
    # группу их за день уходит много.
    image.save(buffer, format='PNG', optimize=True)
    buffer.seek(0)
    return buffer.getvalue()
