# -*- coding: utf-8 -*-
"""Конкурс «Топ по регистрациям»: матчинг операторов CRM и построение рейтинга.

reg_contest.py — чистая логика без БД и Flask, поэтому импортируется напрямую
(в отличие от bot_schedule2.py, который на старте поднимает пул к боевой БД).
"""
import ast
import sys
import textwrap
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reg_contest

from tests import source_cache

DATABASE_PATH = Path(__file__).resolve().parents[1] / "database.py"
BOT_PATH = Path(__file__).resolve().parents[1] / "bot_schedule2.py"


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

    def test_missing_registrations_counter_raises(self):
        # Второй счётчик обязателен ровно так же: без проверки CRM могла бы
        # переименовать поле, а мы молча записали бы всем «0 регистраций».
        payload = {"total_successful": 2, "operators": [
            {"operator_id": "1", "operator_login": "a@x.kz",
             "operator_name": "Тестов Тест", "successful_registrations_count": 2}]}
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch(payload)
        self.assertIn("registrations_count", str(ctx.exception))

    def test_duplicate_operator_id_still_raises(self):
        payload = {"operators": [_crm_op("a@x.kz", "Тестов Тест", 2, 5, operator_id="7"),
                                 _crm_op("a@x.kz", "Тестов Тест", 3, 9, operator_id="7")]}
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch(payload)
        self.assertIn("дважды", str(ctx.exception))

    def test_anonymous_rows_are_summed_not_fatal(self):
        # У CRM есть корзина «ничей» — строка без id, логина и ФИО. Приза она
        # не занимает и сопоставлять её не с кем, поэтому вторая такая строка
        # не повод ронять синк и морозить рейтинг всем остальным.
        payload = {"operators": [
            {"operator_id": None, "operator_login": None, "operator_name": None,
             "registrations_count": 6, "successful_registrations_count": 3},
            {"operator_id": None, "operator_login": None, "operator_name": None,
             "registrations_count": 4, "successful_registrations_count": 1},
        ]}
        rows = self._fetch(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["registrations_count"],
                          rows[0]["successful_registrations_count"]), (10, 4))

    def test_rows_without_id_but_with_login_stay_separate(self):
        # Без id, но с логином это всё ещё разные люди — складывать нельзя.
        payload = {"operators": [
            {"operator_id": None, "operator_login": "a@x.kz", "operator_name": "Первый",
             "registrations_count": 5, "successful_registrations_count": 2},
            {"operator_id": None, "operator_login": "b@x.kz", "operator_name": "Второй",
             "registrations_count": 4, "successful_registrations_count": 1},
        ]}
        self.assertEqual(len(self._fetch(payload)), 2)


class OperatorKeyTests(unittest.TestCase):
    def test_operator_id_wins(self):
        self.assertEqual(reg_contest.operator_key(
            {"operator_id": " 42 ", "operator_login": "a@x.kz"}), "42")

    def test_falls_back_to_login_then_name(self):
        self.assertEqual(reg_contest.operator_key(
            {"operator_id": None, "operator_login": "A@X.kz"}), "login:a@x.kz")
        self.assertEqual(reg_contest.operator_key(
            {"operator_id": None, "operator_name": "Тестбаев Нұрасыл"}),
            "name:тестбаев нурасыл")

    def test_fully_anonymous_row_gets_empty_key(self):
        self.assertEqual(reg_contest.operator_key({"operator_id": None}), "")


class SnapshotShrinkTests(unittest.TestCase):
    """Обрезанный ответ CRM опаснее пустого: удалённая строка уносит с собой
    reached_at, а он решает, кому 25 000, а кому 10 000."""

    def _previous(self, count):
        return [{"crm_operator_id": str(i), "user_name": f"Оператор {i}",
                 "operator_name": None} for i in range(count)]

    def _entries(self, count):
        return [{"crm_operator_id": str(i)} for i in range(count)]

    def test_full_response_passes(self):
        self.assertIsNone(reg_contest.check_snapshot_shrink(self._previous(69), self._entries(69)))

    def test_first_snapshot_passes(self):
        self.assertIsNone(reg_contest.check_snapshot_shrink([], self._entries(3)))

    def test_single_disappearance_is_allowed(self):
        # Законный случай: у оператора забрали все регистрации задним числом.
        self.assertIsNone(reg_contest.check_snapshot_shrink(self._previous(69), self._entries(68)))

    def test_truncated_response_is_refused(self):
        reason = reg_contest.check_snapshot_shrink(self._previous(69), self._entries(40))
        self.assertIsNotNone(reason)
        self.assertIn("29", reason)
        self.assertIn("Оператор", reason)

    def test_tiny_snapshot_tolerates_one_loss(self):
        # На трёх операторах 10% — это ноль, но одиночная пропажа законна и там.
        self.assertIsNone(reg_contest.check_snapshot_shrink(self._previous(3), self._entries(2)))


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


class GroupSplitTests(unittest.TestCase):
    """Оператор сменил направление посреди конкурса.

    CRM держит все его регистрации одной строкой и про наши направления не
    знает, поэтому счёт делим сами по дате регистрации: заработанное в прежней
    группе там и остаётся, новая группа получает только то, что после перехода.
    Живой случай — Нургазы Жанеля (CRM 393): 3 сентября перешла из чатов на
    линию, где её полный счёт стоял на третьем призовом месте.
    """

    SPLIT = {"crm_operator_id": "7", "operator_name": "Перешедший Оператор",
             "switch_date": "2026-09-04", "before_group": "chat", "after_group": "line"}

    def _entries(self, registrations=22, successful=10):
        entry = {"crm_operator_id": "7", "operator_login": "move@yandextaxi.kz",
                 "operator_name": "Перешедший Оператор", "user_id": 149,
                 "user_name": "Перешедший Оператор", "contest_group": "line",
                 "match_method": "email", "registrations": registrations,
                 "successful": successful}
        other = dict(entry, crm_operator_id="8", operator_login="stay@yandextaxi.kz",
                     operator_name="Осевший Оператор", user_id=150,
                     user_name="Осевший Оператор", registrations=5, successful=1)
        return [entry, other]

    def _split(self, before, entries=None, splits=None):
        return reg_contest.apply_group_splits(
            entries if entries is not None else self._entries(),
            {"2026-09-04": {"7": before}} if before is not None else {},
            splits if splits is not None else [self.SPLIT])

    def test_before_snapshot_ends_the_day_before_the_switch(self):
        # Срез «до перехода» просим по последний день в прежней группе:
        # 4 сентября — первый линейный день, значит 3-е ещё чатовое.
        self.assertEqual(reg_contest.split_before_to("2026-09-04"), "2026-09-03")

    def test_counters_split_between_two_groups(self):
        result = self._split({"registrations": 18, "successful": 8})
        parts = {e["crm_operator_id"]: e for e in result["entries"]}
        self.assertEqual(set(parts), {"7#chat", "7#line", "8"})
        self.assertEqual((parts["7#chat"]["contest_group"],
                          parts["7#chat"]["registrations"],
                          parts["7#chat"]["successful"]), ("chat", 18, 8))
        self.assertEqual((parts["7#line"]["contest_group"],
                          parts["7#line"]["registrations"],
                          parts["7#line"]["successful"]), ("line", 4, 2))
        # Сумма частей равна тому, что прислала CRM: делим, а не дорисовываем.
        self.assertEqual(parts["7#chat"]["registrations"] + parts["7#line"]["registrations"], 22)
        self.assertEqual(parts["7#chat"]["successful"] + parts["7#line"]["successful"], 10)
        self.assertEqual(result["notes"], [])
        self.assertEqual(result["origins"], {"7#chat": "7", "7#line": "7"})

    def test_person_stays_the_same_in_both_parts(self):
        # Обе части — один и тот же человек: аватарка, подсветка «Вы» и приз
        # ищут его по user_id.
        parts = {e["crm_operator_id"]: e for e in
                 self._split({"registrations": 18, "successful": 8})["entries"]}
        for key in ("7#chat", "7#line"):
            self.assertEqual(parts[key]["user_id"], 149)
            self.assertEqual(parts[key]["user_name"], "Перешедший Оператор")

    def test_untouched_operators_keep_their_key(self):
        parts = {e["crm_operator_id"]: e for e in
                 self._split({"registrations": 18, "successful": 8})["entries"]}
        self.assertEqual(parts["8"]["contest_group"], "line")
        self.assertEqual(parts["8"]["successful"], 1)

    def test_group_without_a_single_registration_gets_no_row(self):
        # Пустая часть — строка «0 из 0» в чужом рейтинге: в срез не идёт.
        result = self._split({"registrations": 0, "successful": 0})
        parts = {e["crm_operator_id"]: e for e in result["entries"]}
        self.assertEqual(set(parts), {"7#line", "8"})
        self.assertEqual((parts["7#line"]["registrations"], parts["7#line"]["successful"]), (22, 10))
        self.assertEqual(result["origins"], {"7#line": "7"})

    def test_everything_earned_before_the_switch_leaves_the_new_group_empty(self):
        # Живой случай на 07.09.2026: после перехода ни одной регистрации —
        # весь счёт остаётся в прежней группе, в новой строки нет вовсе.
        result = self._split({"registrations": 22, "successful": 10})
        parts = {e["crm_operator_id"]: e for e in result["entries"]}
        self.assertEqual(set(parts), {"7#chat", "8"})
        self.assertEqual((parts["7#chat"]["registrations"], parts["7#chat"]["successful"]), (22, 10))

    def test_missing_from_before_snapshot_counts_as_zero(self):
        # Оператора не было в срезе «до» — значит до перехода он не привёл
        # никого, а не «делить нечего».
        parts = {e["crm_operator_id"]: e for e in self._split(None)["entries"]}
        self.assertEqual(set(parts), {"7#line", "8"})
        self.assertEqual(parts["7#line"]["registrations"], 22)

    def test_before_bigger_than_total_is_clipped_and_noted(self):
        # CRM переписывает прошлое, поэтому срез «до» умеет обогнать общий.
        # Отрицательный остаток в рейтинг не отдаём, но и молчать нельзя.
        result = self._split({"registrations": 30, "successful": 12})
        parts = {e["crm_operator_id"]: e for e in result["entries"]}
        self.assertEqual((parts["7#chat"]["registrations"], parts["7#chat"]["successful"]), (22, 10))
        self.assertNotIn("7#line", parts)
        self.assertEqual(len(result["notes"]), 2)
        self.assertIn("Перешедший Оператор", result["notes"][0])

    def test_operator_absent_from_crm_is_noted_not_crashed(self):
        result = self._split({"registrations": 1, "successful": 1},
                             entries=[self._entries()[1]])
        self.assertEqual([e["crm_operator_id"] for e in result["entries"]], ["8"])
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("нет в выдаче CRM", result["notes"][0])

    def test_empty_split_list_changes_nothing(self):
        entries = self._entries()
        result = reg_contest.apply_group_splits(entries, {}, [])
        self.assertIs(result["entries"], entries)
        self.assertEqual(result["origins"], {})

    def test_counts_by_operator_reads_both_counters(self):
        counts = reg_contest.counts_by_operator([
            _crm_op("move@yandextaxi.kz", "Перешедший Оператор", 8, 18, operator_id="7")])
        self.assertEqual(counts["7"], {"registrations": 18, "successful": 8})

    def test_parts_rank_and_win_prizes_in_their_own_groups(self):
        # Ради этого всё и делается: часть «до» борется за приз в чатах,
        # часть «после» — в линии, каждая своим счётом.
        entries = self._split({"registrations": 18, "successful": 8})["entries"]
        for entry in entries:
            entry["reached_at"] = _at(10)
        boards = reg_contest.build_leaderboards(entries)
        self.assertEqual([(i["crm_operator_id"], i["drivers"]) for i in boards["chat"]],
                         [("7#chat", 8)])
        self.assertEqual(boards["chat"][0]["prize"], 40000)
        self.assertEqual([i["crm_operator_id"] for i in boards["line"]], ["7#line", "8"])
        self.assertEqual(boards["line"][0]["drivers"], 2)

    def test_live_contest_split_is_inside_the_contest_window(self):
        # Страж конфига: дата перехода вне окна конкурса означала бы, что одна
        # из групп получает пустой срез, а вторая — весь счёт целиком.
        contest = reg_contest.CONTEST
        for split in contest.get("splits") or []:
            switch = date.fromisoformat(split["switch_date"])
            self.assertGreater(switch, date.fromisoformat(contest["registered_from"]))
            self.assertLessEqual(switch, date.fromisoformat(contest["registered_to"]))
            self.assertIn(split["before_group"], reg_contest.GROUP_LABELS)
            self.assertIn(split["after_group"], reg_contest.GROUP_LABELS)
            self.assertNotEqual(split["before_group"], split["after_group"])


class _FakeCursor:
    """Курсор ровно того объёма, что нужен upsert_reg_contest_operators."""

    def __init__(self, previous_rows):
        self._previous_rows = previous_rows
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchall(self):
        return self._previous_rows


class SplitJournalTests(unittest.TestCase):
    """Разделение оператора не должно читаться как просадка от CRM.

    Журнал изменений отвечает операторам на вопрос «почему у меня стало
    меньше» и метит просадки на самом синке. Переразметка строки на две части
    — наше решение, а не действие CRM: если бы она попала в журнал, админ
    увидел бы «пропал из выдачи (было 10 из 22)» ровно там, где ничего не
    пропадало. Метод исполняется настоящий (через AST — импорт database.py
    поднял бы пул к боевой БД), с фейковым курсором.
    """

    ROW = ("393", "Нургазы Жанеля Багдаткызы", "Нургазы Жанеля Багдаткызы", 22, 10)

    def _run(self, previous_rows, entries, split_origins=None):
        source, module = source_cache.read(str(DATABASE_PATH)), source_cache.tree(str(DATABASE_PATH))
        class_node = next(n for n in module.body
                          if isinstance(n, ast.ClassDef) and n.name == "Database")
        node = next(n for n in class_node.body
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "upsert_reg_contest_operators")
        namespace = {}
        journal, snapshot = [], []

        def execute_values(cursor, sql, argslist, template=None):
            (journal if "reg_contest_operator_changes" in sql else snapshot).extend(argslist)

        namespace["execute_values"] = execute_values
        exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)

        cursor = _FakeCursor(previous_rows)

        class _Dummy:
            def _get_cursor(self):
                from contextlib import contextmanager

                @contextmanager
                def scope():
                    yield cursor

                return scope()

            _reg_contest_change_is_drop = staticmethod(
                lambda change: any(
                    change[f"{f}_before"] is not None
                    and (change[f"{f}_after"] is None
                         or change[f"{f}_after"] < change[f"{f}_before"])
                    for f in ("registrations", "successful")))

            @staticmethod
            def _reg_contest_decrease_note(decreases):
                return "; ".join(str(c["crm_operator_id"]) for c in decreases) or None

        dummy = _Dummy()
        result = namespace["upsert_reg_contest_operators"].__get__(dummy, _Dummy)(
            "top_registrations_2026_09", entries, split_origins=split_origins)
        return result, journal, snapshot

    def _parts(self, chat=(18, 8), line=(4, 2)):
        base = {"operator_login": "nurgazy@yandextaxi.kz",
                "operator_name": "Нургазы Жанеля Багдаткызы", "user_id": 149,
                "user_name": "Нургазы Жанеля Багдаткызы", "match_method": "name"}
        return [dict(base, crm_operator_id="393#chat", contest_group="chat",
                     registrations=chat[0], successful=chat[1]),
                dict(base, crm_operator_id="393#line", contest_group="line",
                     registrations=line[0], successful=line[1])]

    def test_first_split_run_writes_nothing_to_the_journal(self):
        result, journal, snapshot = self._run(
            [self.ROW], self._parts(),
            split_origins={"393#chat": "393", "393#line": "393"})
        self.assertEqual(journal, [])
        self.assertEqual(result["decreases"], 0)
        self.assertIsNone(result["decrease_note"])
        # Обе части при этом в срезе — переразметка молчит, но происходит.
        self.assertEqual({row[1] for row in snapshot}, {"393#chat", "393#line"})

    def test_real_change_of_a_part_still_reaches_the_journal(self):
        # Когда части уже лежат в срезе, они живут по общим правилам.
        previous = [("393#chat", "Нургазы Жанеля Багдаткызы", "Нургазы Жанеля Багдаткызы", 18, 8),
                    ("393#line", "Нургазы Жанеля Багдаткызы", "Нургазы Жанеля Багдаткызы", 4, 2)]
        result, journal, _ = self._run(
            previous, self._parts(line=(4, 1)),
            split_origins={"393#chat": "393", "393#line": "393"})
        self.assertEqual([row[1] for row in journal], ["393#line"])
        self.assertEqual(result["decreases"], 1)

    def test_disappearance_of_an_ordinary_operator_is_still_logged(self):
        # Глушим только переразметку: настоящая пропажа строки обязана
        # оставаться видимой.
        previous = [self.ROW,
                    ("500", "Пропавший Оператор", "Пропавший Оператор", 7, 3)]
        result, journal, _ = self._run(
            previous, self._parts(),
            split_origins={"393#chat": "393", "393#line": "393"})
        self.assertEqual([row[1] for row in journal], ["500"])
        self.assertEqual(result["decreases"], 1)

    def test_without_split_origins_behaviour_is_unchanged(self):
        result, journal, _ = self._run([self.ROW], self._parts())
        self.assertEqual(sorted(row[1] for row in journal), ["393", "393#chat", "393#line"])
        self.assertEqual(result["decreases"], 1)


class SyncSplitFlowTests(unittest.TestCase):
    """Синк целиком: сколько раз он ходит в CRM и что кладёт в срез.

    `sync_reg_contest` живёт в bot_schedule2.py, который импортировать из
    тестов нельзя (на старте поднимает пул к боевой БД), поэтому функция
    достаётся через AST и исполняется с фейковыми клиентом и базой.
    """

    CONTEST = {
        "code": "test_contest",
        "registered_from": "2026-08-07",
        "registered_to": "2026-09-07",
        "trip_deadline": "2026-09-11",
        "prizes": {"chat": [40000], "line": [40000]},
        "splits": [{"crm_operator_id": "7", "operator_name": "Перешедший Оператор",
                    "switch_date": "2026-09-04",
                    "before_group": "chat", "after_group": "line"}],
    }

    class _Client:
        def __init__(self):
            self.calls = []

        def fetch_operators(self, registered_from, registered_to, trip_deadline):
            self.calls.append((registered_from, registered_to, trip_deadline))
            successful = 8 if registered_to == "2026-09-03" else 10
            registrations = 18 if registered_to == "2026-09-03" else 22
            return [_crm_op("move@yandextaxi.kz", "Перешедший Оператор",
                            successful, registrations, operator_id="7")]

    class _Db:
        def __init__(self):
            self.upserted = None

        def get_reg_contest_sync_state(self, code):
            return {"total_rows": 1}

        def get_reg_contest_operator_directory(self):
            return [_user(149, "Перешедший Оператор", email="move@yandextaxi.kz")]

        def get_reg_contest_operators(self, code):
            return []

        def upsert_reg_contest_operators(self, code, entries, split_origins=None):
            self.upserted = {"entries": entries, "split_origins": split_origins}
            return {"total": len(entries), "changes": 0, "decreases": 0,
                    "decrease_note": None}

    def _sync(self):
        import logging
        import time as time_module

        source = source_cache.read(str(BOT_PATH))
        node = next(n for n in source_cache.tree(str(BOT_PATH)).body
                    if isinstance(n, ast.FunctionDef) and n.name == "sync_reg_contest")
        client, db = self._Client(), self._Db()
        namespace = {"reg_contest": reg_contest, "db": db, "logging": logging,
                     "time": time_module}
        exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
        with patch.object(reg_contest, "CONTEST", self.CONTEST), \
             patch.object(reg_contest, "get_config", lambda: {"url": "u", "token": "t"}), \
             patch.object(reg_contest.RegContestClient, "from_config",
                          classmethod(lambda cls, config=None: client)):
            result = namespace["sync_reg_contest"](triggered_by="test")
        return result, client, db

    def test_sync_asks_crm_for_the_period_before_the_switch(self):
        result, client, db = self._sync()
        self.assertEqual(result["status"], "success")
        # Второй запрос отличается ТОЛЬКО концом окна регистраций: срок
        # поездки остаётся конкурсным, иначе часть зачётов пропала бы.
        self.assertEqual(client.calls, [("2026-08-07", "2026-09-07", "2026-09-11"),
                                        ("2026-08-07", "2026-09-03", "2026-09-11")])

    def test_sync_writes_two_parts_with_their_origins(self):
        _, _, db = self._sync()
        parts = {e["crm_operator_id"]: e for e in db.upserted["entries"]}
        self.assertEqual(set(parts), {"7#chat", "7#line"})
        self.assertEqual((parts["7#chat"]["contest_group"], parts["7#chat"]["successful"]),
                         ("chat", 8))
        self.assertEqual((parts["7#line"]["contest_group"], parts["7#line"]["successful"]),
                         ("line", 2))
        self.assertEqual(db.upserted["split_origins"], {"7#chat": "7", "7#line": "7"})


if __name__ == "__main__":
    unittest.main()
