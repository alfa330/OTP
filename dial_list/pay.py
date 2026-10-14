# -*- coding: utf-8 -*-
"""Оплата оператора по конверсии и рейтинг дня: чистые правила, без базы и сети.

Решение владельца 14.10.2026 («Оплата по конверсии» для операторов удалённого КЦ):
платим за каждый состоявшийся диалог, а цена диалога зависит от конверсии оператора —
доли подписавших среди тех, с кем он поговорил. Почасовой оплаты нет. Пример с
плаката: обзвонили 1000 контактов, не подняли 500, состоялось диалогов 500, подписали
335; конверсия 335 / 500 × 100 = 67 %, при 67 % диалог стоит 90 тн, оплата
500 × 90 = 45 000 тн.

Что здесь решается и почему так:

  * Числа приходят готовыми — те же, что у конверсии в «Моём прогрессе» и в «Аналитике»
    руководителя (service._PROGRESS_CONVERSION_SQL, analytics._OPERATORS_SQL). Диалог —
    водитель, с которым оператор поговорил не меньше signing.SUCCESS_MIN_BILLSEC секунд
    (по водителям, а не по звонкам: два разговора с одним — один диалог). Подписавший —
    успешка, засчитанная оператору (sign_check._RESOLVE_SQL).

  * Тариф (TARIFF): до 30 % конверсии диалог стоит 30 тн, дальше каждые полные 10 % —
    плюс 15 тн. Плакат: «до 20 % — 30 тн», «30 % — 45 тн» … «90 % и выше — 135 тн»; 20–29 %
    плакат не называет — по его же правилу (15 тн за каждые полные 10 %, не ниже 30) это
    30 тн.

  * Ступень — по ТОЧНОЙ доле и в целых числах (100·s ≥ порог·d), а не по округлённому
    проценту: 69,96 % — ещё ступень 60 % (90 тн), хотя с одним знаком это «70,0 %».

  * Показанный процент (display_percent) — как у аналитики (analytics._pct, один знак),
    но округление не переводит через порог тарифа: тогда процент обрезается вниз до
    десятых (69,9 %). Телефон не покажет «70,0 %» рядом с ценой за 60 %.

  * Рейтинг дня. Место — по заработку дня (диалоги дня × цена по конверсии дня); у
    коллег видны только диалоги, подписавшие и конверсия, их суммы сервер не отдаёт
    (решение владельца 14.10.2026). Виден с 21:00 до 10:00 следующего утра по Алматы —
    «чтобы сохранять интригу»; до 21:00 телефон ведёт обратный отсчёт до итогов дня
    (rating_window).
"""
from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone

PERIOD_TZ = timezone(timedelta(hours=5))  # Asia/Almaty

CURRENCY = "тн"

# (нижний порог конверсии, %; цена одного диалога, тн). Пороги — целые проценты по
# возрастанию, первый — 0: на этом стоит сравнение в целых числах (level).
TARIFF = ((0, 30), (30, 45), (40, 60), (50, 75), (60, 90), (70, 105), (80, 120), (90, 135))
_STARTS = tuple(start for start, _cost in TARIFF)

# Рейтинг дня открывается в 21:00 и держится до 10:00 следующего утра (Алматы).
RATING_OPENS_AT = time(21, 0)
RATING_CLOSES_AT = time(10, 0)

_MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                    "сентября", "октября", "ноября", "декабря")


def _counts(s, d):
    """Счётчики → целые: диалогов не меньше нуля, подписавших — от нуля до числа диалогов."""
    d = max(0, int(d or 0))
    return min(max(0, int(s or 0)), d), d


def level(s, d):
    """Номер ступени TARIFF при s подписавших из d диалогов — по точной доле, в целых
    числах: последняя ступень, у которой 100·s ≥ порог·d. Нет диалогов — первая."""
    s, d = _counts(s, d)
    if d <= 0:
        return 0
    # Порог — целое число процентов, поэтому 100·s ≥ порог·d ⇔ порог ≤ ⌊100·s / d⌋.
    return bisect_right(_STARTS, 100 * s // d) - 1


def _percent_level(percent):
    """Ступень по уже округлённому проценту — чтобы поймать округление через порог."""
    return max(0, bisect_right(_STARTS, percent) - 1)


def price(s, d):
    """Цена одного диалога, тн."""
    return TARIFF[level(s, d)][1]


def display_percent(s, d):
    """Конверсия для показа: как analytics._pct — доля с одним знаком, нет диалогов —
    None. Если же округление переводит через порог тарифа (69,96 % → «70,0 %», а цена ещё
    за 60 %), процент обрезается вниз до десятых: показанный процент и цена не расходятся."""
    d = int(d or 0)
    if d <= 0:
        return None
    s = int(s or 0)
    shown = round(100.0 * s / d, 1)
    if _percent_level(shown) > level(s, d):
        shown = (1000 * s // d) / 10
    return shown


def next_level(s, d):
    """Следующая ступень: с какого процента, цена диалога на ней, сколько ещё подписавших
    нужно при тех же диалогах и сколько тогда вышла бы оплата. Нет диалогов или ступень
    последняя — None."""
    s, d = _counts(s, d)
    if d <= 0:
        return None
    index = level(s, d) + 1
    if index >= len(TARIFF):
        return None
    start, cost = TARIFF[index]
    # Подписавших на ступени — не меньше ⌈порог·d / 100⌉.
    return {"from": start, "price": cost, "need": max(1, (start * d + 99) // 100 - s), "amount": d * cost}


def tariff_rows():
    """Тариф для экрана «из чего сложилась»: от, до (не включая; у последней — None), цена."""
    rows = []
    for index, (start, cost) in enumerate(TARIFF):
        end = TARIFF[index + 1][0] if index + 1 < len(TARIFF) else None
        rows.append({"from": start, "to": end, "price": cost})
    return rows


def salary(called, dialogs, successes):
    """Зарплата за базу месяца и из чего она сложилась — для «Моего прогресса».

    called — водители базы, которым оператор звонил (без попыток, отменённых до ответа),
    dialogs — с кем состоялся диалог, successes — подписавшие, засчитанные ему. no_dialog —
    «не подняли» с плаката: звонил, а диалога не было. Оплата — dialogs × цена ступени."""
    called, dialogs, successes = (max(0, int(x or 0)) for x in (called, dialogs, successes))
    cost = price(successes, dialogs)
    return {
        "currency": CURRENCY,
        "called": called,
        "dialogs": dialogs,
        "no_dialog": max(0, called - dialogs),
        "successes": successes,
        "percent": display_percent(successes, dialogs),
        "price": cost,
        "amount": dialogs * cost,
        "level": level(successes, dialogs),
        "next": next_level(successes, dialogs),
        "tariff": tariff_rows(),
    }


def _seconds_until(now, moment):
    """Целые секунды от now до moment — вверх и не меньше 1: отсчёт на телефоне не дойдёт
    до нуля раньше, чем рейтинг откроется (закроется)."""
    delta = moment - now
    micro = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    return max(1, -(-micro // 1000000))


def rating_window(now):
    """Окно рейтинга на момент now (aware datetime в любом поясе; без пояса — Алматы).

    С 21:00 открыт рейтинг сегодняшнего дня, до 10:00 — ещё вчерашнего, между 10:00 и
    21:00 он скрыт и идёт отсчёт до итогов сегодняшнего. → {"visible", "day" (date),
    "opens_at", "closes_at" (aware, +05:00)} и у открытого "seconds_to_close", у скрытого —
    "seconds_to_open"."""
    local = now.astimezone(PERIOD_TZ) if now.tzinfo is not None else now.replace(tzinfo=PERIOD_TZ)
    today, clock = local.date(), local.time()
    if clock >= RATING_OPENS_AT:
        visible, day = True, today
    elif clock < RATING_CLOSES_AT:
        visible, day = True, today - timedelta(days=1)
    else:
        visible, day = False, today
    opens_at = datetime.combine(day, RATING_OPENS_AT, tzinfo=PERIOD_TZ)
    closes_at = datetime.combine(day + timedelta(days=1), RATING_CLOSES_AT, tzinfo=PERIOD_TZ)
    window = {"visible": visible, "day": day, "opens_at": opens_at, "closes_at": closes_at}
    if visible:
        window["seconds_to_close"] = _seconds_until(local, closes_at)
    else:
        window["seconds_to_open"] = _seconds_until(local, opens_at)
    return window


def day_label(day):
    """«14 октября» — подпись дня рейтинга: склонять месяцы телефону незачем."""
    return f"{day.day} {_MONTHS_GENITIVE[day.month - 1]}"


def rank(rows):
    """Места рейтинга дня: больше заработок — выше; при равном — больше подписавших, затем
    больше диалогов. Полное равенство всех трёх — одно место на всех («1, 2, 2, 4»), внутри
    него строки идут по имени. Строки — словари с amount, successes, dialogs и name; →
    новые словари с "place", по порядку мест."""
    ordered = sorted(rows, key=lambda r: (-r["amount"], -r["successes"], -r["dialogs"],
                                          str(r.get("name") or "").casefold()))
    ranked, place, previous = [], 0, None
    for index, row in enumerate(ordered, 1):
        score = (row["amount"], row["successes"], row["dialogs"])
        if score != previous:
            place, previous = index, score
        ranked.append(dict(row, place=place))
    return ranked
