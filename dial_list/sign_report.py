# -*- coding: utf-8 -*-
"""Отчёт «Подписание водителей по городам» — картинка в Telegram-группы раздела «Удаленный КЦ».

Просьба владельца 19.10.2026: руководитель выбирает во вкладке «Аналитика» группы, и два раза в
день — в 10:00 и в 19:00 по Алматы — туда приходит картинка того же вида, что прислал владелец:
шапка с датой, три плашки (всего / подписано / не подписано) и таблица городов с полосой доли
подписавших, отсортированная по ней, и строка «ИТОГО».

«Подписано» — как на картинке владельца (подтвердил 19.10.2026): водитель подписал, в том числе
если документ ещё ждёт подписи Яндекса. В базе раздела это подписавшие (signed_at) и «На проверке
у Яндекса» (sign_status 'processing'). Полностью подписанные отдельно не выделяются. Город — колонка
city базы (из файла базы или из списка городов, см. service.import_cities).

Здесь три вещи и ни базы-соединения, ни сети, ни Flask:
    collect(cur, …)   один запрос: водители базы месяца по городам
    build(rows)       строки отчёта, доли и порядок (чистая функция)
    render_png(…)     картинка (Pillow, шрифт из crm/fonts — образ Render шрифтов не гарантирует)
    slot_for(now)     какой из слотов 10:00 / 19:00 сейчас отправлять
"""
import io
from datetime import datetime, timedelta, timezone

from test_numbers import keys as test_keys
from . import signing

# Когда отчёт уходит в группы — часы по Алматы (владелец, 19.10.2026: «в 10 утра и в 19»).
SLOT_HOURS = (10, 19)
# Сколько слот ждёт отправки: сервер мог перезапуститься ровно в 10:00 (выкладка) — тогда отчёт
# уходит при старте. Старше — пропускаем: утренний отчёт к обеду только путает.
SLOT_LATE_MAX = timedelta(hours=1)

UNKNOWN_CITY = "Город не указан"
# Пороги цвета доли подписавших (у владельца на картинке ≥ 40 % — зелёная, ниже — жёлтая, 0 % —
# красная). Красная — всё, что меньше SHARE_RED_BELOW.
SHARE_GREEN_FROM = 40.0
SHARE_RED_BELOW = 20.0

TITLE = "Подписание водителей по городам"
FILE_NAME = "podpisanie-po-gorodam.png"

# Подписал — в том числе «на проверке у Яндекса» (см. шапку модуля).
_BY_CITY_SQL = """
    SELECT l.city,
           COUNT(*),
           COUNT(*) FILTER (WHERE l.signed_at IS NOT NULL OR l.sign_status = %(processing)s)
    FROM dial_list_leads l
    WHERE l.department_id = %(department_id)s AND l.period = %(period)s
      -- Номер из реестра тестовых — проверка обзвона, а не водитель.
      AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """
    GROUP BY l.city
"""


def clean_city(value):
    """Город из файла → как храним: без лишних пробелов, не длиннее колонки (64)."""
    return " ".join(str(value or "").split())[:64]


def collect(cur, department_id, period):
    """Строки (город, всего, подписали) базы месяца `period` отдела."""
    cur.execute(_BY_CITY_SQL, {"department_id": int(department_id), "period": period,
                               "processing": signing.PROCESSING})
    return [(row[0] or "", int(row[1] or 0), int(row[2] or 0)) for row in cur.fetchall()]


def _share(part, whole):
    return 100.0 * part / whole if whole else 0.0


def build(rows):
    """[(город, всего, подписали)] → {"cities": [...], "totals": {...}}.

    Города — по доле подписавших, от большей; при равной доле — крупнее выше, потом по алфавиту.
    Водители без города — одной строкой в конце: их доля ничего не говорит о городе. Строки одного
    города с разным написанием пробелов склеиваются (clean_city)."""
    merged = {}
    for city, total, signed in rows:
        key = clean_city(city)
        acc = merged.setdefault(key, [0, 0])
        acc[0] += int(total or 0)
        acc[1] += int(signed or 0)
    cities = []
    for key, (total, signed) in merged.items():
        if not total:
            continue
        cities.append({
            "city": key or UNKNOWN_CITY,
            "known": bool(key),
            "total": total,
            "signed": signed,
            "unsigned": total - signed,
            "signed_pct": _share(signed, total),
            "unsigned_pct": _share(total - signed, total),
        })
    cities.sort(key=lambda c: (not c["known"], -c["signed_pct"], -c["total"], c["city"]))
    total = sum(c["total"] for c in cities)
    signed = sum(c["signed"] for c in cities)
    return {
        "cities": cities,
        "totals": {"total": total, "signed": signed, "unsigned": total - signed,
                   "signed_pct": _share(signed, total), "unsigned_pct": _share(total - signed, total)},
    }


def slot_for(now):
    """Слот отправки (datetime по Алматы) для момента `now` (aware) или None — отправлять нечего.

    Слот — последний из SLOT_HOURS, наступивший к `now`, если с него прошло не больше
    SLOT_LATE_MAX. Повтор того же слота не отправляется (метка last_slot у группы)."""
    local = now.astimezone(signing.PERIOD_TZ)
    candidates = []
    for days_back in (0, 1):
        day = (local - timedelta(days=days_back)).date()
        for hour in SLOT_HOURS:
            moment = datetime(day.year, day.month, day.day, hour, 0, tzinfo=signing.PERIOD_TZ)
            if moment <= local:
                candidates.append(moment)
    if not candidates:
        return None
    slot = max(candidates)
    return slot if local - slot <= SLOT_LATE_MAX else None


# ── картинка ─────────────────────────────────────────────────────────────────────────────────

# Рисуем вдвое крупнее и отдаём как есть: Telegram показывает фото до 1280 точек по длинной
# стороне, а на телефоне с плотным экраном — вдвое больше; без запаса мелкий текст мылится.
SCALE = 2
WIDTH = 1016            # логическая ширина, как у картинки владельца
PAD = 14                # поля таблицы и плашек
TEXT_X = 32             # отступ текста в строках

NAVY = (30, 41, 66)
INK = (33, 41, 54)
MUTED = (102, 110, 124)
WHITE = (255, 255, 255)
ZEBRA = (245, 246, 248)
TRACK = (232, 235, 240)
TOTAL_BG = (222, 228, 239)
CARD_BG = (237, 240, 245)
GREEN = (47, 158, 92)
GREEN_BG = (232, 245, 237)
AMBER = (226, 166, 36)
RED = (217, 84, 79)
RED_BG = (251, 234, 234)
NOT_SIGNED_INK = (78, 86, 99)

# Колонки таблицы: правые края чисел и место полосы (логические точки).
COL_TOTAL_RIGHT = 405
COL_SIGNED_RIGHT = 521
COL_UNSIGNED_RIGHT = 646
BAR_X = 673
BAR_W = 130
BAR_H = 11
COL_PCT_RIGHT = 873
COL_NOT_PCT_RIGHT = 997

ROW_H = 44
HEAD_H = 34
CARD_H = 70


def _fonts():
    from PIL import ImageFont
    from crm.card import FONTS_DIR
    import os

    def load(weight, size):
        return ImageFont.truetype(os.path.join(FONTS_DIR, "Roboto-%s.ttf" % weight), int(round(size * SCALE)))

    return load


def share_color(pct):
    if pct >= SHARE_GREEN_FROM:
        return GREEN
    if pct >= SHARE_RED_BELOW:
        return AMBER
    return RED


def fmt_int(value):
    """23771 → «23 771», как на картинке владельца."""
    return "{:,}".format(int(value)).replace(",", " ")


def fmt_pct(value):
    """48.26 → «48.3%» — с точкой, как на картинке владельца."""
    return "%.1f%%" % value


TOP = 93                # верх плашек
TOTAL_GAP = 14          # отступ строки «ИТОГО» от таблицы
BOTTOM = 16             # поле снизу


def table_top():
    """Где начинается первая строка городов (под плашками и шапкой таблицы)."""
    return TOP + CARD_H + 20 + HEAD_H + 10


def height_for(report):
    return int(table_top() + len(report["cities"]) * ROW_H + TOTAL_GAP + ROW_H + BOTTOM)


def render_png(report, generated_at):
    """PNG отчёта (bytes). generated_at — момент среза (aware): дата и время в подзаголовке."""
    from PIL import Image, ImageDraw

    load = _fonts()
    px = lambda v: int(round(v * SCALE))  # noqa: E731 — локальный перевод в точки холста
    height = height_for(report)
    image = Image.new("RGB", (px(WIDTH), px(height)), WHITE)
    draw = ImageDraw.Draw(image)

    def text(x, y, value, font, fill, anchor="ls"):
        draw.text((px(x), px(y)), str(value), font=font, fill=fill, anchor=anchor)

    def box(x0, y0, x1, y1, radius, fill):
        draw.rounded_rectangle((px(x0), px(y0), px(x1), px(y1)), radius=px(radius), fill=fill)

    local = generated_at.astimezone(signing.PERIOD_TZ)
    text(28, 40, TITLE, load("Bold", 21), NAVY)
    text(28, 67, "на %s" % local.strftime("%d.%m.%Y, %H:%M"), load("Regular", 14), MUTED)

    totals = report["totals"]
    gap = 19
    card_w = (WIDTH - PAD * 2 - gap * 2) / 3.0
    cards = (
        ("Всего водителей", fmt_int(totals["total"]), None, NAVY, CARD_BG, NAVY),
        ("Подписано", fmt_int(totals["signed"]), fmt_pct(totals["signed_pct"]), GREEN, GREEN_BG, GREEN),
        ("Не подписано", fmt_int(totals["unsigned"]), fmt_pct(totals["unsigned_pct"]), RED, RED_BG, RED),
    )
    top = TOP
    for index, (label, number, pct, bar, background, ink) in enumerate(cards):
        x0 = PAD + index * (card_w + gap)
        x1 = x0 + card_w
        box(x0, top, x1, top + CARD_H, 9, background)
        box(x0, top, x0 + 7, top + CARD_H, 3, bar)
        text(x0 + 31, top + 23, label, load("Regular", 15), MUTED)
        text(x0 + 31, top + 56, number, load("Bold", 30), ink)
        if pct:
            text(x1 - 16, top + 51, pct, load("Bold", 20), ink, anchor="rs")

    y = top + CARD_H + 20
    box(PAD, y, WIDTH - PAD, y + HEAD_H, 7, NAVY)
    head = load("Bold", 14.5)
    middle = y + HEAD_H / 2.0
    text(TEXT_X, middle, "Город", head, WHITE, anchor="lm")
    text(COL_TOTAL_RIGHT, middle, "Всего", head, WHITE, anchor="rm")
    text(COL_SIGNED_RIGHT, middle, "Подписан", head, WHITE, anchor="rm")
    text(COL_UNSIGNED_RIGHT, middle, "Не подп.", head, WHITE, anchor="rm")
    text(BAR_X, middle, "% подписания", head, WHITE, anchor="lm")
    text(COL_NOT_PCT_RIGHT, middle, "% не подп.", head, WHITE, anchor="rm")
    y = table_top()

    regular, bold = load("Regular", 15), load("Bold", 15)

    def draw_row(y0, item, *, fill=None, strong=False):
        if fill:
            box(PAD, y0, WIDTH - PAD, y0 + ROW_H, 7, fill)
        mid = y0 + ROW_H / 2.0
        font = bold if strong else regular
        name_fill = INK if (strong or item.get("known", True)) else MUTED
        text(TEXT_X, mid, item["city"], font, name_fill, anchor="lm")
        text(COL_TOTAL_RIGHT, mid, fmt_int(item["total"]), font, INK, anchor="rm")
        text(COL_SIGNED_RIGHT, mid, fmt_int(item["signed"]), font, INK, anchor="rm")
        text(COL_UNSIGNED_RIGHT, mid, fmt_int(item["unsigned"]), font, INK, anchor="rm")
        color = share_color(item["signed_pct"])
        bar_top = mid - BAR_H / 2.0
        box(BAR_X, bar_top, BAR_X + BAR_W, bar_top + BAR_H, BAR_H / 2.0, TRACK)
        filled = BAR_W * max(0.0, min(100.0, item["signed_pct"])) / 100.0
        if filled >= 1:
            box(BAR_X, bar_top, BAR_X + max(filled, BAR_H), bar_top + BAR_H, BAR_H / 2.0, color)
        text(COL_PCT_RIGHT, mid, fmt_pct(item["signed_pct"]), bold, color, anchor="rm")
        text(COL_NOT_PCT_RIGHT, mid, fmt_pct(item["unsigned_pct"]), bold, NOT_SIGNED_INK, anchor="rm")

    for index, item in enumerate(report["cities"]):
        draw_row(y, item, fill=ZEBRA if index % 2 == 0 else None)
        y += ROW_H
    y += TOTAL_GAP
    draw_row(y, dict(totals, city="ИТОГО", known=True), fill=TOTAL_BG, strong=True)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def now_utc():
    return datetime.now(timezone.utc)
