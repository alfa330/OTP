# -*- coding: utf-8 -*-
"""Конкурс «Топ по регистрациям»: матчинг операторов CRM и построение рейтинга.

reg_contest.py — чистая логика без БД и Flask, поэтому импортируется напрямую
(в отличие от bot_schedule2.py, который на старте поднимает пул к боевой БД).
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reg_contest


def _user(uid, name, email=None, direction="Основа", department="СЗоВ — Служба заботы о водителях"):
    return {"id": uid, "name": name, "email": email, "status": "working",
            "role": "operator", "direction_name": direction, "department_name": department}


def _crm_op(login, name, successful, registrations=None, operator_id="1"):
    """Строка CRM в текущем формате: оператор с двумя счётчиками."""
    return {"operator_id": operator_id, "operator_login": login, "operator_name": name,
            "operator_group": None,
            "registrations_count": registrations if registrations is not None else successful,
            "successful_registrations_count": successful}


def _at(minutes):
    """Отметка reached_at: чем меньше minutes, тем раньше набран результат."""
    return datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


class FoldNameTests(unittest.TestCase):
    def test_kazakh_letters_fold_to_russian(self):
        # CRM и наша база пишут одно имя разными алфавитами.
        self.assertEqual(reg_contest.fold_name("Тестбаев Нұрасыл"), reg_contest.fold_name("Тестбаев Нурасыл"))
        self.assertEqual(reg_contest.fold_name("Сынақбай Ерсұлтан"), reg_contest.fold_name("Сынакбай Ерсултан"))

    def test_case_and_spaces_normalized(self):
        self.assertEqual(reg_contest.fold_name("  ИВАНОВ   иван "), "иванов иван")


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
            _user(1, "Совсем Другой", email="operator1@yandextaxi.kz"),
            _user(2, "Тестбаев Асан Асанулы"),
        ]
        user, method = reg_contest.match_operator("operator1@yandextaxi.kz", "Тестбаев Асан", directory)
        self.assertEqual(user["id"], 1)
        self.assertEqual(method, "email")

    def test_exact_name_match_with_kazakh_folding(self):
        directory = [_user(3, "Тестбаев Нұрасыл")]
        user, method = reg_contest.match_operator("operator2@yandextaxi.kz", "Тестбаев Нурасыл", directory)
        self.assertEqual(user["id"], 3)
        self.assertEqual(method, "name")

    def test_prefix_match_crm_name_without_patronymic(self):
        # CRM хранит «Фамилия Имя», у нас — с отчеством.
        directory = [_user(4, "Сынакбай Алихан Тестулы")]
        user, method = reg_contest.match_operator(None, "Сынакбай Алихан", directory)
        self.assertEqual(user["id"], 4)
        self.assertEqual(method, "name_prefix")

    def test_ambiguous_prefix_is_unmatched(self):
        directory = [
            _user(5, "Досанбай Бокен Мадиулы"),
            _user(6, "Досанбай Бокен Тестулы"),
        ]
        user, method = reg_contest.match_operator(None, "Досанбай Бокен", directory)
        self.assertIsNone(user)
        self.assertEqual(method, "none")

    def test_prefix_does_not_glue_half_words(self):
        # «Иванов Ив» не должен матчиться на «Иванов Иван» — только целые слова.
        directory = [_user(7, "Иванов Иван")]
        user, method = reg_contest.match_operator(None, "Иванов Ив", directory)
        self.assertIsNone(user)
        self.assertEqual(method, "none")


class FetchOperatorsTests(unittest.TestCase):
    """Незнакомый формат обязан падать, а не превращаться в пустой срез.

    13.08.2026 CRM убрала из ответа ключ rows, парсер прочитал ноль строк и
    затёр рейтинг, отрапортовав «ok». Эти тесты держат исправленное поведение."""

    def _client(self):
        return reg_contest.RegContestClient("https://crm.example/contest", "token")

    def _fetch(self, payload):
        client = self._client()
        with patch.object(client, "_post", return_value=payload):
            return client.fetch_operators("2026-08-07", "2026-09-07", "2026-09-11")

    def test_current_format_parsed(self):
        payload = {"total_registrations": 5, "total_successful": 2,
                   "operators": [_crm_op("a@x.kz", "Тестов Тест", 2, 5)]}
        self.assertEqual(len(self._fetch(payload)), 1)

    def test_empty_operator_list_is_not_an_error(self):
        # Пустой список сам по себе законен (конкурс только стартовал);
        # защиту «не затирать непустой срез» держит sync_reg_contest.
        self.assertEqual(self._fetch({"total_registrations": 0, "operators": []}), [])

    def test_missing_operators_key_raises(self):
        # Ровно тот случай, что обнулил конкурс: старый ключ rows вместо operators.
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch({"total": 12, "rows": [{"driver_id": "d1"}]})
        self.assertIn("operators", str(ctx.exception))

    def test_missing_successful_counter_raises(self):
        # Промежуточный формат с одним счётчиком: считать по нему места нельзя.
        payload = {"total": 209, "operators": [
            {"operator_id": "1", "operator_login": "a@x.kz",
             "operator_name": "Тестов Тест", "registrations_count": 37}]}
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch(payload)
        self.assertIn("successful_registrations_count", str(ctx.exception))


class LeaderboardTests(unittest.TestCase):
    def _directory(self):
        return [
            _user(10, "Чатовый Первый", email="chat1@yandextaxi.kz", direction="Чат менеджер"),
            _user(11, "Чатовый Второй", email="chat2@yandextaxi.kz", direction="Чат менеджер"),
            _user(20, "Линейный Один", email="line1@yandextaxi.kz", direction="Основа"),
            _user(30, "Верификатор Оп", email="verif@yandextaxi.kz",
                  direction="Верификатор", department="Отдел продаж"),
        ]

    def _entries(self):
        operators = [
            _crm_op("chat1@yandextaxi.kz", "Чатовый Первый", 2, 4, operator_id="1"),
            _crm_op("chat2@yandextaxi.kz", "Чатовый Второй", 2, 9, operator_id="2"),
            _crm_op("line1@yandextaxi.kz", "Линейный Один", 1, 3, operator_id="3"),
            _crm_op("verif@yandextaxi.kz", "Верификатор Оп", 40, 90, operator_id="4"),
            _crm_op("ghost@yandextaxi.kz", "Призрак Пропавший", 5, 5, operator_id="5"),
        ]
        entries = reg_contest.resolve_operators(operators, self._directory())
        # reached_at проставляет БД; chat2 набрал свои 2 раньше, чем chat1.
        stamps = {"1": _at(60), "2": _at(10), "3": _at(30), "4": _at(5), "5": _at(5)}
        for entry in entries:
            entry["reached_at"] = stamps[entry["crm_operator_id"]]
        return entries

    def test_tie_break_by_earlier_reached_at(self):
        chat = reg_contest.build_leaderboards(self._entries())["chat"]
        self.assertEqual([item["name"] for item in chat], ["Чатовый Второй", "Чатовый Первый"])
        self.assertEqual(chat[0]["place"], 1)
        self.assertEqual(chat[0]["drivers"], 2)

    def test_registrations_do_not_outrank_successful(self):
        # У «Первого» регистраций меньше, но тай-брейк смотрит только на время:
        # общее число регистраций на место не влияет вовсе.
        chat = reg_contest.build_leaderboards(self._entries())["chat"]
        self.assertEqual(chat[0]["registrations"], 9)
        self.assertEqual(chat[1]["registrations"], 4)

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

    def test_zero_successful_gets_no_prize_and_ranks_last(self):
        # Одни регистрации без поездок призового места не занимают.
        operators = [
            _crm_op("chat1@yandextaxi.kz", "Чатовый Первый", 0, 12, operator_id="1"),
            _crm_op("chat2@yandextaxi.kz", "Чатовый Второй", 1, 1, operator_id="2"),
        ]
        entries = reg_contest.resolve_operators(operators, self._directory())
        for entry in entries:
            entry["reached_at"] = _at(1)
        chat = reg_contest.build_leaderboards(entries)["chat"]
        self.assertEqual([i["name"] for i in chat], ["Чатовый Второй", "Чатовый Первый"])
        self.assertEqual(chat[0]["prize"], 40000)
        self.assertIsNone(chat[1]["prize"])

    def test_missing_reached_at_sorts_after_stamped(self):
        # Строка, которую синк ещё ни разу не переписывал, не должна ронять
        # сортировку сравнением None с датой.
        operators = [
            _crm_op("chat1@yandextaxi.kz", "Чатовый Первый", 2, 2, operator_id="1"),
            _crm_op("chat2@yandextaxi.kz", "Чатовый Второй", 2, 2, operator_id="2"),
        ]
        entries = reg_contest.resolve_operators(operators, self._directory())
        entries[0]["reached_at"] = None
        entries[1]["reached_at"] = _at(99)
        chat = reg_contest.build_leaderboards(entries)["chat"]
        self.assertEqual([i["name"] for i in chat], ["Чатовый Второй", "Чатовый Первый"])

    def test_counters_survive_string_values_from_crm(self):
        # CRM уже присылала числа строками — счёт не должен превращаться в 0.
        operators = [{"operator_id": "1", "operator_login": "chat1@yandextaxi.kz",
                      "operator_name": "Чатовый Первый", "operator_group": None,
                      "registrations_count": "7", "successful_registrations_count": "3"}]
        entries = reg_contest.resolve_operators(operators, self._directory())
        item = reg_contest.build_leaderboards(entries)["chat"][0]
        self.assertEqual((item["drivers"], item["registrations"]), (3, 7))


if __name__ == "__main__":
    unittest.main()
