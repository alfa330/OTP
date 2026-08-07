# -*- coding: utf-8 -*-
"""Конкурс «Топ по регистрациям»: матчинг операторов CRM и построение рейтинга.

reg_contest.py — чистая логика без БД и Flask, поэтому импортируется напрямую
(в отличие от bot_schedule2.py, который на старте поднимает пул к боевой БД).
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reg_contest


def _user(uid, name, email=None, direction="Основа", department="СЗоВ — Служба заботы о водителях"):
    return {"id": uid, "name": name, "email": email, "status": "working",
            "role": "operator", "direction_name": direction, "department_name": department}


def _crm_row(login, name, driver_id, first_trip_at, registered_at="2026-08-10T10:00:00+05:00",
             operator_id="1", trips=1):
    return {"operator_id": operator_id, "operator_login": login, "operator_name": name,
            "operator_group": None, "driver_id": driver_id, "driver_phone": "+77010000000",
            "driver_name": "Водитель", "registered_at": registered_at,
            "first_trip_at": first_trip_at, "trips_count": trips}


class FoldNameTests(unittest.TestCase):
    def test_kazakh_letters_fold_to_russian(self):
        # CRM и наша база пишут одно имя разными алфавитами.
        self.assertEqual(reg_contest.fold_name("Жакуп Нұрасыл"), reg_contest.fold_name("Жакуп Нурасыл"))
        self.assertEqual(reg_contest.fold_name("Санақ Ерсұлтан"), reg_contest.fold_name("Санак Ерсултан"))

    def test_case_and_spaces_normalized(self):
        self.assertEqual(reg_contest.fold_name("  ИВАНОВ   иван "), "иванов иван")


class MaskPhoneTests(unittest.TestCase):
    def test_only_last_four_digits_visible(self):
        # Телефон — персональные данные: оператору отдаём только хвост.
        self.assertEqual(reg_contest.mask_phone("+77011234567"), "••• 4567")
        self.assertEqual(reg_contest.mask_phone("8 (701) 123-45-67"), "••• 4567")

    def test_empty_phone_stays_empty(self):
        self.assertIsNone(reg_contest.mask_phone(None))
        self.assertIsNone(reg_contest.mask_phone(""))
        self.assertIsNone(reg_contest.mask_phone("—"))


class ClassifyGroupTests(unittest.TestCase):
    def test_szov_chat_manager_is_chat(self):
        self.assertEqual(reg_contest.classify_group("Чат менеджер", "СЗоВ — Служба заботы о водителях"), "chat")

    def test_szov_other_directions_are_line(self):
        self.assertEqual(reg_contest.classify_group("Основа", "СЗоВ — Служба заботы о водителях"), "line")
        self.assertEqual(reg_contest.classify_group("СМЗ", "СЗоВ — Служба заботы о водителях"), "line")

    def test_other_departments_are_off(self):
        # Верификаторы ОП регистрируют водителей по работе — они вне зачёта.
        self.assertEqual(reg_contest.classify_group("Верификатор", "Отдел продаж"), "off")
        self.assertEqual(reg_contest.classify_group("Регионы", "Фронт офисы"), "off")


class MatchOperatorTests(unittest.TestCase):
    def test_email_match_wins_over_name(self):
        directory = [
            _user(1, "Совсем Другой", email="arman_aset_co@yandextaxi.kz"),
            _user(2, "Арман Асет Арманулы"),
        ]
        user, method = reg_contest.match_operator("arman_aset_co@yandextaxi.kz", "Арман Асет", directory)
        self.assertEqual(user["id"], 1)
        self.assertEqual(method, "email")

    def test_exact_name_match_with_kazakh_folding(self):
        directory = [_user(3, "Жакуп Нұрасыл")]
        user, method = reg_contest.match_operator("zhakup@yandextaxi.kz", "Жакуп Нурасыл", directory)
        self.assertEqual(user["id"], 3)
        self.assertEqual(method, "name")

    def test_prefix_match_crm_name_without_patronymic(self):
        # CRM хранит «Фамилия Имя», у нас — с отчеством.
        directory = [_user(4, "Жансерик Алихан Русланулы")]
        user, method = reg_contest.match_operator(None, "Жансерик Алихан", directory)
        self.assertEqual(user["id"], 4)
        self.assertEqual(method, "name_prefix")

    def test_ambiguous_prefix_is_unmatched(self):
        directory = [
            _user(5, "Желдербай Бокен Мадиулы"),
            _user(6, "Желдербай Бокен Русланулы"),
        ]
        user, method = reg_contest.match_operator(None, "Желдербай Бокен", directory)
        self.assertIsNone(user)
        self.assertEqual(method, "none")

    def test_prefix_does_not_glue_half_words(self):
        # «Иванов Ив» не должен матчиться на «Иванов Иван» — только целые слова.
        directory = [_user(7, "Иванов Иван")]
        user, method = reg_contest.match_operator(None, "Иванов Ив", directory)
        self.assertIsNone(user)
        self.assertEqual(method, "none")


class LeaderboardTests(unittest.TestCase):
    def _entries(self):
        directory = [
            _user(10, "Чатовый Первый", email="chat1@yandextaxi.kz", direction="Чат менеджер"),
            _user(11, "Чатовый Второй", email="chat2@yandextaxi.kz", direction="Чат менеджер"),
            _user(20, "Линейный Один", email="line1@yandextaxi.kz", direction="Основа"),
            _user(30, "Верификатор Оп", email="verif@yandextaxi.kz",
                  direction="Верификатор", department="Отдел продаж"),
        ]
        rows = [
            # chat1 — 2 водителя, последняя поездка 10-го.
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d1", "2026-08-09T12:00:00+05:00"),
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d2", "2026-08-10T12:00:00+05:00"),
            # chat2 — тоже 2 водителя, но последняя поездка РАНЬШЕ (9-го утром)
            # -> по правилу тай-брейка chat2 выше.
            _crm_row("chat2@yandextaxi.kz", "Чатовый Второй", "d3", "2026-08-08T09:00:00+05:00"),
            _crm_row("chat2@yandextaxi.kz", "Чатовый Второй", "d4", "2026-08-09T09:00:00+05:00"),
            # линия — 1 водитель.
            _crm_row("line1@yandextaxi.kz", "Линейный Один", "d5", "2026-08-11T15:00:00+05:00"),
            # верификатор ОП — вне зачёта.
            _crm_row("verif@yandextaxi.kz", "Верификатор Оп", "d6", "2026-08-11T16:00:00+05:00"),
            # не сопоставленный оператор CRM — тоже вне зачёта, но виден.
            _crm_row("ghost@yandextaxi.kz", "Призрак Пропавший", "d7", "2026-08-11T17:00:00+05:00"),
        ]
        return reg_contest.resolve_rows(rows, directory)

    def test_tie_break_by_earlier_last_trip(self):
        boards = reg_contest.build_leaderboards(self._entries())
        chat = boards["chat"]
        self.assertEqual([item["name"] for item in chat], ["Чатовый Второй", "Чатовый Первый"])
        self.assertEqual(chat[0]["place"], 1)
        self.assertEqual(chat[0]["drivers"], 2)

    def test_prizes_follow_places(self):
        boards = reg_contest.build_leaderboards(self._entries())
        self.assertEqual(boards["chat"][0]["prize"], 40000)
        self.assertEqual(boards["chat"][1]["prize"], 20000)
        self.assertEqual(boards["line"][0]["prize"], 40000)

    def test_off_bucket_keeps_unmatched_and_other_departments(self):
        boards = reg_contest.build_leaderboards(self._entries())
        off_names = {item["name"] for item in boards["off"]}
        self.assertEqual(off_names, {"Верификатор Оп", "Призрак Пропавший"})
        ghost = next(i for i in boards["off"] if i["name"] == "Призрак Пропавший")
        self.assertEqual(ghost["match_method"], "none")

    def test_rows_sorted_by_registration(self):
        boards = reg_contest.build_leaderboards(self._entries())
        rows = boards["chat"][0]["rows"]
        self.assertEqual([r["driver_id"] for r in rows], ["d3", "d4"])

    def test_datetime_values_from_db_are_supported(self):
        # После чтения из Postgres даты — datetime, а не строки.
        entries = self._entries()
        for e in entries:
            e["first_trip_at"] = datetime.fromisoformat(e["first_trip_at"]).astimezone(timezone.utc)
            e["registered_at"] = datetime.fromisoformat(e["registered_at"]).astimezone(timezone.utc)
        boards = reg_contest.build_leaderboards(entries)
        self.assertEqual(boards["chat"][0]["name"], "Чатовый Второй")


class PendingRegistrationsTests(unittest.TestCase):
    """Регистрации без поездки (first_trip_at = null) — когда CRM начнёт
    отдавать их по include_no_trip: считаются в registrations, но не в drivers."""

    def _directory(self):
        return [
            _user(10, "Чатовый Первый", email="chat1@yandextaxi.kz", direction="Чат менеджер"),
            _user(11, "Чатовый Второй", email="chat2@yandextaxi.kz", direction="Чат менеджер"),
        ]

    def test_pending_rows_count_as_registrations_only(self):
        rows = [
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d1", "2026-08-09T12:00:00+05:00"),
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d2", None, trips=0),
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d3", None, trips=0),
        ]
        entries = reg_contest.resolve_rows(rows, self._directory())
        item = reg_contest.build_leaderboards(entries)["chat"][0]
        self.assertEqual(item["drivers"], 1)
        self.assertEqual(item["registrations"], 3)
        self.assertEqual(len(item["rows"]), 3)

    def test_all_qualified_registrations_equal_drivers(self):
        rows = [_crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d1", "2026-08-09T12:00:00+05:00")]
        entries = reg_contest.resolve_rows(rows, self._directory())
        item = reg_contest.build_leaderboards(entries)["chat"][0]
        self.assertEqual(item["registrations"], item["drivers"])

    def test_pending_only_participant_ranks_below_and_gets_no_prize(self):
        # Одни «ожидающие» регистрации не дают ни места в топе, ни приза.
        rows = [
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d1", None, trips=0),
            _crm_row("chat2@yandextaxi.kz", "Чатовый Второй", "d2", "2026-08-09T12:00:00+05:00"),
        ]
        entries = reg_contest.resolve_rows(rows, self._directory())
        chat = reg_contest.build_leaderboards(entries)["chat"]
        self.assertEqual([i["name"] for i in chat], ["Чатовый Второй", "Чатовый Первый"])
        self.assertEqual(chat[0]["prize"], 40000)
        self.assertIsNone(chat[1]["prize"])

    def test_pending_rows_do_not_touch_tie_break(self):
        # Поздняя «ожидающая» регистрация не должна портить время последней
        # засчитанной поездки при равном счёте.
        rows = [
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d1", "2026-08-09T12:00:00+05:00"),
            _crm_row("chat1@yandextaxi.kz", "Чатовый Первый", "d2", None,
                     registered_at="2026-08-20T10:00:00+05:00", trips=0),
            _crm_row("chat2@yandextaxi.kz", "Чатовый Второй", "d3", "2026-08-10T12:00:00+05:00"),
        ]
        entries = reg_contest.resolve_rows(rows, self._directory())
        chat = reg_contest.build_leaderboards(entries)["chat"]
        self.assertEqual(chat[0]["name"], "Чатовый Первый")
        self.assertEqual(chat[0]["last_trip_at"], "2026-08-09T12:00:00+05:00")


if __name__ == "__main__":
    unittest.main()
