# -*- coding: utf-8 -*-
"""«Оплата по конверсии» и рейтинг дня (владелец, 14.10.2026): чистые правила dial_list/pay.py.

Тариф на сетке конверсий, пример с плаката владельца, показанный процент (не выше ступени,
за которую платим), окно рейтинга по часам Алматы, места при равенствах. Без базы и сети.
"""
import os
import sys
import unittest
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import analytics  # noqa: E402
from dial_list import pay  # noqa: E402

ALMATY = timezone(timedelta(hours=5))
STARTS = [start for start, _cost in pay.TARIFF]


def percent_level(percent):
    """Ступень по показанному проценту — как её прочтёт оператор с плакатом в руках."""
    return bisect_right(STARTS, percent) - 1


class TariffTests(unittest.TestCase):
    """Цена диалога по конверсии: ступени по точной доле, а не по округлённому проценту."""

    # (подписавших, диалогов, конверсия, цена диалога)
    GRID = (
        (0, 10000, '0 %', 30),
        (1000, 10000, '10 %', 30),
        (1990, 10000, '19,9 %', 30),
        (2000, 10000, '20 %', 30),      # 20–29 % плакат не называет: 15 тн за полные 10 %, не ниже 30
        (2500, 10000, '25 %', 30),
        (2990, 10000, '29,9 %', 30),
        (3000, 10000, '30 %', 45),
        (3999, 10000, '39,99 %', 45),   # с одним знаком — «40,0 %», но 40 % ещё не набрано
        (5000, 10000, '50 %', 75),
        (667, 1000, '66,7 %', 90),
        (67, 100, '67 %', 90),
        (6995, 10000, '69,95 %', 90),   # «70,0 %» с одним знаком — и всё равно 90
        (7000, 10000, '70 %', 105),
        (899, 1000, '89,9 %', 120),
        (9000, 10000, '90 %', 135),
        (10000, 10000, '100 %', 135),
    )

    def test_price_on_conversion_grid(self):
        for s, d, label, cost in self.GRID:
            with self.subTest(label):
                self.assertEqual(pay.price(s, d), cost)
                self.assertEqual(pay.salary(d, d, s)["amount"], d * cost)
                # Показанный процент читается в ту же ступень, что и цена.
                self.assertEqual(pay.TARIFF[percent_level(pay.display_percent(s, d))][1], cost)

    def test_tariff_table(self):
        self.assertEqual(pay.CURRENCY, "тн")
        self.assertEqual(pay.TARIFF, ((0, 30), (30, 45), (40, 60), (50, 75), (60, 90), (70, 105), (80, 120),
                                      (90, 135)))
        # Сравнение ступени в целых числах держится на этом: пороги — целые, по возрастанию, с нуля.
        self.assertEqual(STARTS[0], 0)
        self.assertTrue(all(isinstance(start, int) for start in STARTS))
        self.assertEqual(STARTS, sorted(set(STARTS)))
        self.assertEqual(pay.tariff_rows(), [
            {"from": 0, "to": 30, "price": 30}, {"from": 30, "to": 40, "price": 45},
            {"from": 40, "to": 50, "price": 60}, {"from": 50, "to": 60, "price": 75},
            {"from": 60, "to": 70, "price": 90}, {"from": 70, "to": 80, "price": 105},
            {"from": 80, "to": 90, "price": 120}, {"from": 90, "to": None, "price": 135},
        ])

    def test_level_is_exact_and_in_integers(self):
        # Ровно на пороге — уже новая ступень; на одного подписавшего меньше — ещё нет.
        self.assertEqual((pay.price(21, 70), pay.price(20, 70)), (45, 30))     # 30 % ровно и 28,6 %
        self.assertEqual((pay.price(350, 500), pay.price(349, 500)), (105, 90))
        # Подписавших больше диалогов не бывает — считаем как 100 %, а не падаем.
        self.assertEqual(pay.level(12, 10), len(pay.TARIFF) - 1)
        self.assertEqual(pay.level(-3, 10), 0)
        self.assertEqual(pay.level(None, None), 0)


class OwnerPosterTests(unittest.TestCase):
    """Пример с плаката владельца и «сколько не хватает до следующей ступени»."""

    def test_poster_example(self):
        # Обзвонили 1000, не подняли 500, диалогов 500, подписали 335: 67 %, 90 тн, 45 000 тн.
        self.assertEqual(pay.salary(1000, 500, 335), {
            "currency": "тн", "called": 1000, "dialogs": 500, "no_dialog": 500, "successes": 335,
            "percent": 67.0, "price": 90, "amount": 45000, "level": 4,
            "next": {"from": 70, "price": 105, "need": 15, "amount": 52500},
            "tariff": pay.tariff_rows(),
        })

    def test_no_dialogs(self):
        self.assertIsNone(pay.display_percent(0, 0))
        self.assertEqual(pay.price(0, 0), 30)
        self.assertIsNone(pay.next_level(0, 0))
        salary = pay.salary(12, 0, 0)
        self.assertEqual((salary["percent"], salary["price"], salary["amount"], salary["next"], salary["level"],
                          salary["no_dialog"]), (None, 30, 0, None, 0, 12))
        self.assertEqual(pay.salary(None, None, None)["called"], 0)

    def test_next_level(self):
        self.assertEqual(pay.next_level(349, 500), {"from": 70, "price": 105, "need": 1, "amount": 52500})
        # Не набрано и 30 %: 3 из 7 — первые, что дают ⌈2,1⌉ подписавших.
        self.assertEqual(pay.next_level(0, 7), {"from": 30, "price": 45, "need": 3, "amount": 315})
        self.assertIsNone(pay.next_level(9, 10))          # 90 % — ступень последняя
        self.assertIsNone(pay.next_level(10, 10))
        # need — ровно столько, сколько нужно: на одного меньше — порог ещё не взят.
        for d in range(1, 301):
            for s in range(d + 1):
                nxt = pay.next_level(s, d)
                if nxt is None:
                    self.assertEqual(pay.level(s, d), len(pay.TARIFF) - 1, (s, d))
                    continue
                self.assertGreaterEqual(100 * (s + nxt["need"]), nxt["from"] * d, (s, d))
                self.assertLess(100 * (s + nxt["need"] - 1), nxt["from"] * d, (s, d))
                self.assertGreater(pay.level(s + nxt["need"], d), pay.level(s, d), (s, d))
                self.assertEqual(nxt["amount"], d * nxt["price"])


class DisplayPercentTests(unittest.TestCase):
    """Показанный процент — как у аналитики руководителя (analytics._pct), но никогда не в
    ступени выше той, за которую платим: «70,0 %» рядом с ценой за 60 % телефон не покажет."""

    def test_rounding_never_crosses_a_paid_threshold(self):
        # Перебор долей s/d до d = 2500: все s при d ≤ 600 и, дальше, все s в пределах 1 п.п. от
        # каждого порога. Округление до десятых сдвигает процент не больше чем на 0,05 п.п. —
        # через порог оно может перейти только там (полный перебор до 2500 при разработке нашёл
        # те же расхождения с аналитикой, что и этот).
        crossings, wrong = [], []
        for d in range(1, 2501):
            if d <= 600:
                shares = range(d + 1)
            else:
                shares = set()
                for start in STARTS[1:]:
                    shares.update(range(max(0, (start - 1) * d // 100 - 1), min(d, (start + 1) * d // 100 + 1) + 1))
            for s in shares:
                shown, manager, paid = pay.display_percent(s, d), analytics._pct(s, d), pay.level(s, d)
                if percent_level(shown) > paid:
                    wrong.append(('показан процент ступени выше оплаченной', s, d, shown))
                if shown == manager:
                    continue
                # Отличие от аналитики — только на переходе порога и ровно на десятую вниз.
                if (percent_level(manager) <= paid or round(manager * 10) - round(shown * 10) != 1
                        or shown != (1000 * s // d) / 10):
                    wrong.append(('лишнее отличие от аналитики', s, d, shown, manager))
                crossings.append((s, d))
        self.assertEqual(wrong, [])
        # Перебор не пустой: переходы есть у каждого порога, и самый ранний — уже при 203 диалогах.
        self.assertIn((142, 203), crossings)
        self.assertIn((1397, 1997), crossings)
        self.assertEqual({percent_level(analytics._pct(s, d)) for s, d in crossings}, set(range(1, len(STARTS))))

    def test_examples(self):
        self.assertEqual((analytics._pct(6995, 10000), pay.display_percent(6995, 10000)), (70.0, 69.9))
        self.assertEqual((analytics._pct(3999, 10000), pay.display_percent(3999, 10000)), (40.0, 39.9))
        self.assertEqual((analytics._pct(1397, 1997), pay.display_percent(1397, 1997)), (70.0, 69.9))
        # Вне порога — ровно как у аналитики, с обычным округлением.
        for s, d in ((335, 500), (2, 3), (7, 3 * 7), (1, 7), (6994, 10000), (7000, 10000)):
            self.assertEqual(pay.display_percent(s, d), analytics._pct(s, d), (s, d))
        self.assertEqual(pay.display_percent(2, 3), 66.7)


class RatingWindowTests(unittest.TestCase):
    """Рейтинг открыт с 21:00 до 10:00 следующего утра по Алматы, в остальное время — отсчёт."""

    # (момент по Алматы, открыт ли, чей день, секунд до закрытия / открытия)
    CASES = (
        ((2026, 10, 14, 9, 59, 59), True, date(2026, 10, 13), 1),
        ((2026, 10, 14, 10, 0, 0), False, date(2026, 10, 14), 11 * 3600),
        ((2026, 10, 14, 20, 59, 59), False, date(2026, 10, 14), 1),
        ((2026, 10, 14, 21, 0, 0), True, date(2026, 10, 14), 13 * 3600),
        ((2026, 10, 14, 23, 59, 59), True, date(2026, 10, 14), 10 * 3600 + 1),
        ((2026, 10, 15, 0, 0, 0), True, date(2026, 10, 14), 10 * 3600),
    )

    def test_hours_in_almaty(self):
        for moment, visible, day, seconds in self.CASES:
            with self.subTest(moment):
                window = pay.rating_window(datetime(*moment, tzinfo=ALMATY))
                self.assertEqual((window["visible"], window["day"]), (visible, day))
                self.assertEqual(window["seconds_to_close" if visible else "seconds_to_open"], seconds)
                self.assertNotIn("seconds_to_open" if visible else "seconds_to_close", window)
                self.assertEqual(window["opens_at"], datetime.combine(day, time(21), tzinfo=ALMATY))
                self.assertEqual(window["closes_at"], datetime.combine(day + timedelta(days=1), time(10),
                                                                       tzinfo=ALMATY))
                self.assertEqual(window["opens_at"].isoformat(), f"{day.isoformat()}T21:00:00+05:00")
                # Тот же момент в UTC (так ходят часы сервера) — то же окно.
                utc = datetime(*moment, tzinfo=ALMATY).astimezone(timezone.utc)
                self.assertEqual(pay.rating_window(utc), window)

    def test_utc_moments(self):
        # 16:00 UTC — 21:00 по Алматы: открылись итоги дня; 04:59 UTC — ещё вчерашние.
        opened = pay.rating_window(datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc))
        self.assertEqual((opened["visible"], opened["day"], opened["seconds_to_close"]),
                         (True, date(2026, 10, 14), 13 * 3600))
        morning = pay.rating_window(datetime(2026, 10, 15, 4, 59, tzinfo=timezone.utc))
        self.assertEqual((morning["visible"], morning["day"], morning["seconds_to_close"]),
                         (True, date(2026, 10, 14), 60))
        # 05:00 UTC — 10:00 по Алматы: закрыт, до итогов нового дня 11 часов.
        closed = pay.rating_window(datetime(2026, 10, 15, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((closed["visible"], closed["day"], closed["seconds_to_open"]),
                         (False, date(2026, 10, 15), 11 * 3600))
        # Без пояса — время Алматы.
        self.assertEqual(pay.rating_window(datetime(2026, 10, 14, 21, 0)), pay.rating_window(
            datetime(2026, 10, 14, 21, 0, tzinfo=ALMATY)))

    def test_seconds_round_up_and_never_reach_zero(self):
        for micro, seconds in ((999999, 1), (200000, 1), (0, 1)):
            window = pay.rating_window(datetime(2026, 10, 14, 20, 59, 59, micro, tzinfo=ALMATY))
            self.assertEqual(window["seconds_to_open"], seconds, micro)
        window = pay.rating_window(datetime(2026, 10, 14, 20, 59, 58, 500000, tzinfo=ALMATY))
        self.assertEqual(window["seconds_to_open"], 2)

    def test_day_label(self):
        self.assertEqual(pay.day_label(date(2026, 10, 14)), "14 октября")
        self.assertEqual([pay.day_label(date(2026, month, 1)) for month in range(1, 13)],
                         ["1 января", "1 февраля", "1 марта", "1 апреля", "1 мая", "1 июня", "1 июля",
                          "1 августа", "1 сентября", "1 октября", "1 ноября", "1 декабря"])


class RankTests(unittest.TestCase):
    """Места рейтинга: заработок, затем подписавшие, затем диалоги; равным — одно место."""

    @staticmethod
    def row(name, successes, dialogs):
        cost = pay.price(successes, dialogs)
        return {"name": name, "successes": successes, "dialogs": dialogs, "amount": dialogs * cost}

    def test_places_with_ties(self):
        rows = [
            self.row("Викторов Влад", 3, 3),         # 100 % → 135 × 3 = 405
            self.row("Ахметова Айгерим", 3, 9),      # 33,3 % → 45 × 9 = 405: те же деньги, больше диалогов
            self.row("Агеев Арман", 3, 8),           # 37,5 % → 45 × 8 = 360
            self.row("Борисова Бэла", 1, 12),        # 8,3 % → 30 × 12 = 360: меньше подписавших
            self.row("Дмитриева Дана", 2, 4),        # 50 % → 75 × 4 = 300
            self.row("гусев Глеб", 2, 4),            # то же самое — то же место, по имени без регистра
            self.row("Егоров Ербол", 0, 0),
        ]
        ranked = pay.rank(rows)
        self.assertEqual([(r["name"], r["place"]) for r in ranked], [
            ("Ахметова Айгерим", 1), ("Викторов Влад", 2), ("Агеев Арман", 3), ("Борисова Бэла", 4),
            ("гусев Глеб", 5), ("Дмитриева Дана", 5), ("Егоров Ербол", 7),
        ])
        # Чужие поля строки сохраняются, исходные словари не меняются.
        self.assertEqual(ranked[0]["amount"], 405)
        self.assertTrue(all("place" not in r for r in rows))

    def test_places_one_two_two_four(self):
        ranked = pay.rank([self.row("Г", 2, 3), self.row("Б", 2, 4), self.row("А", 2, 4), self.row("В", 3, 3)])
        self.assertEqual([(r["name"], r["place"]) for r in ranked], [("В", 1), ("А", 2), ("Б", 2), ("Г", 4)])
        self.assertEqual(pay.rank([]), [])


if __name__ == '__main__':
    unittest.main()
