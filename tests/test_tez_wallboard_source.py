# -*- coding: utf-8 -*-
"""Источник табло Тез КЦ: разбор ответов кабинета Binotel на синтетических фикстурах.

Фикстуры в tests/fixtures/tez_wallboard — живые ответы кабинета, из которых убраны
люди: ФИО, почты, SIP-логины, пароли и телефоны клиентов заменены на выдуманные, а
разметка и ключи оставлены как есть. Иначе тест проверял бы не кабинет, а свою же
заглушку — и не заметил бы, что вёрстку в кабинете поменяли.

Модуль импортируется напрямую: он не тянет ни Flask, ни базу.
"""

import ast
import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tez_wallboard_source as source  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "tez_wallboard"

# Учётки линий, лежащие в фикстурах: ни одна не должна пережить разбор.
SECRET_VALUES = ("sip-fixture-903", "Fixture-Pass-903", "line903@example.kz")
# Поля кабинета, которых в разборе быть не должно ни на каком уровне вложенности.
SECRET_KEY_RE = re.compile(r"login|password|endpointdata|exthash|email|secret", re.IGNORECASE)


def fixture(name):
    return FIXTURES.joinpath(name).read_text(encoding="utf-8")


def walk(node, path="$"):
    """Все пары (путь, значение) вложенной структуры — для поиска утечек."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield "%s.%s" % (path, key), key
            for item in walk(value, "%s.%s" % (path, key)):
                yield item
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            for item in walk(value, "%s[%d]" % (path, index)):
                yield item
    else:
        yield path, node


class QueuePageTests(unittest.TestCase):
    def setUp(self):
        self.idle = source.parse_queue_page(fixture("queue_idle.html"))
        self.busy = source.parse_queue_page(fixture("queue_busy.html"))

    def test_totals_of_the_day_come_from_the_page(self):
        self.assertEqual(58, self.idle["served"])
        self.assertEqual(0, self.idle["lost"])
        self.assertEqual(0, self.idle["queue"])
        self.assertAlmostEqual(0.983, self.idle["sl_ratio"], places=4)
        self.assertEqual(2, self.idle["avg_wait_seconds"])
        self.assertEqual(129, self.idle["avg_talk_seconds"])

    def test_sl_threshold_is_read_from_the_caption(self):
        # Порог берём из подписи колонки, а не из своей константы: поменяют его
        # в кабинете — табло обязано поехать следом, а не спорить с кабинетом.
        self.assertEqual(20, self.idle["sl_threshold_seconds"])
        without_caption = fixture("queue_idle.html").replace("SLA: 20 секунд", "SLA")
        self.assertEqual(source.DEFAULT_SL_THRESHOLD_SECONDS,
                         source.parse_queue_page(without_caption)["sl_threshold_seconds"])

    def test_counters_are_read_by_caption_not_by_ordinal_class(self):
        # li.state-ul__item--one…--eleven — порядковые номера. Меняем их местами:
        # если разбор опирался на номер, счётчики перепутаются молча.
        html = (fixture("queue_idle.html")
                .replace("state-ul__item--four", "state-ul__item--TMP")
                .replace("state-ul__item--eight", "state-ul__item--four")
                .replace("state-ul__item--TMP", "state-ul__item--eight"))
        self.assertEqual(self.idle["counters"], source.parse_queue_page(html)["counters"])

    def test_counter_captions_survive_word_wrap_hyphen(self):
        # «приоста-<br>новлено» — перенос посреди слова, а не дефис в подписи.
        self.assertIn("paused", self.idle["counters"])
        self.assertEqual(0, self.idle["counters"]["paused"])

    def test_unknown_caption_is_reported_and_not_guessed(self):
        html = fixture("queue_idle.html").replace(
            'p class="state-ul__info">статус<br>активен',
            'p class="state-ul__info">статус<br>бодрствует')
        parsed = source.parse_queue_page(html)
        self.assertIn("статус бодрствует", parsed["unknown_counters"])
        self.assertIsNone(parsed["counters"].get("status_active"))

    def test_operator_rows_carry_status_and_time_in_status(self):
        by_number = {row["number"]: row for row in self.idle["operators"]}
        self.assertEqual(11, len(self.idle["operators"]))
        self.assertEqual("break", by_number["906"]["status_key"])
        self.assertEqual(677, by_number["906"]["in_state_seconds"])
        self.assertEqual("phone_offline", by_number["915"]["status_key"])
        self.assertEqual("free", by_number["903"]["status_key"])
        self.assertEqual(28880, by_number["914"]["in_state_seconds"])

    def test_status_is_taken_from_russian_text_not_from_css_class(self):
        # «перерыв в работе» и «телефон офлайн» лежат в одном классе
        # employees__item--inactive: по классу их не различить.
        by_number = {row["number"]: row for row in self.idle["operators"]}
        self.assertNotEqual(by_number["906"]["status_key"], by_number["915"]["status_key"])

    def test_busy_queue_separates_talking_from_waiting(self):
        summary = source.summarize_queue_operators(self.busy)
        self.assertEqual({"operators_total": 11, "operators_online": 5, "operators_free": 3,
                          "operators_talking": 2, "operators_on_break": 1, "operators_other": 5},
                         {k: v for k, v in summary.items() if k != "break_list"})
        self.assertEqual([{"name": "Оспан Айгерим Канаткызы", "number": "906",
                           "reason": "Перерыв", "reason_key": "break",
                           "since": None, "seconds": 342}],
                         summary["break_list"])

    def test_page_that_is_not_a_queue_is_rejected(self):
        # Кабинет отдаёт форму логина и скелет SPA с кодом 200 — по статусу
        # ответа живость не проверить, только по содержимому.
        for body in ("<html><body>logining[email]</body></html>", "", "<html>SPA</html>"):
            with self.assertRaises(source.CabinetParseError):
                source.parse_queue_page(body)


class QueueCountersGuardTests(unittest.TestCase):
    def test_valid_page_passes(self):
        parsed = source.parse_queue_page(fixture("queue_idle.html"))
        self.assertIs(parsed, source.validate_queue_counters(parsed))

    def test_status_identity_skew_rejects_the_snapshot(self):
        parsed = source.parse_queue_page(fixture("queue_counters_skewed.html"))
        with self.assertRaises(source.CabinetParseError) as ctx:
            source.validate_queue_counters(parsed)
        self.assertIn("сотрудников с тел. линией", str(ctx.exception))

    def test_workload_identity_skew_rejects_the_snapshot(self):
        parsed = source.parse_queue_page(fixture("queue_idle.html"))
        parsed["counters"]["not_working"] += 1
        with self.assertRaises(source.CabinetParseError):
            source.validate_queue_counters(parsed)

    def test_missing_counter_rejects_the_snapshot(self):
        parsed = source.parse_queue_page(fixture("queue_idle.html"))
        parsed["counters"].pop("members")
        with self.assertRaises(source.CabinetParseError):
            source.validate_queue_counters(parsed)

    def test_lost_operator_rows_reject_the_snapshot(self):
        # Счётчики и таблица людей разбираются РАЗНЫМ кодом: счётчики по подписям,
        # строки по классам таблицы. Поменяй кабинет разметку строки — таблица
        # разберётся в пустоту, счётчики сойдутся сами с собой, и стена покажет
        # «Онлайн 0» при одиннадцати работающих людях. Тихий перекос страшнее
        # честной надписи «данные замерли».
        parsed = source.parse_queue_page(fixture("queue_idle.html"))
        parsed["operators"] = []
        with self.assertRaises(source.CabinetParseError) as ctx:
            source.validate_queue_counters(parsed)
        self.assertIn("разобрано строк сотрудников", str(ctx.exception))

    def test_rows_must_agree_with_the_working_counter(self):
        # Второе тождество между теми же двумя половинами: сколько людей стоит
        # «в ожидании»/«разговаривает» в таблице, столько же кабинет насчитал
        # «в работе» в блоке счётчиков.
        parsed = source.parse_queue_page(fixture("queue_busy.html"))
        for row in parsed["operators"]:
            if row["status_key"] == "talking":
                row["status_key"] = "inactive"
                break
        with self.assertRaises(source.CabinetParseError) as ctx:
            source.validate_queue_counters(parsed)
        self.assertIn("по счётчику", str(ctx.exception))

    def test_busy_queue_passes_all_identities(self):
        parsed = source.parse_queue_page(fixture("queue_busy.html"))
        self.assertIs(parsed, source.validate_queue_counters(parsed))


class PresenceAxisTests(unittest.TestCase):
    """Ось «сейчас» для отдела без очереди (ОП)."""

    def setUp(self):
        self.employees = source.parse_employees(fixture("employees_day.json"))
        self.op = [number for number, record in self.employees["employees"].items()
                   if record["department"] == "Отдел продаж"]
        self.op.sort()

    def test_talking_comes_from_active_calls_only(self):
        # В presenceState значения «разговаривает» не существует в принципе —
        # без активных звонков плитка была бы вечным нулём.
        calls = [{"employee_number": self.op[0], "call_type": "outgoing"}]
        summary = source.summarize_presence_operators(self.employees, self.op, live_calls=calls)
        self.assertEqual(1, summary["operators_talking"])
        self.assertEqual(len(self.op), summary["operators_total"])

    def test_free_needs_a_registered_phone_not_a_status(self):
        # 08.09.2026 все семеро продавцов стояли inactive, при этом один номер
        # сделал за день 50 звонков: статусы у ОП просто не проставляют.
        registered = {number: True for number in self.op}
        summary = source.summarize_presence_operators(
            self.employees, self.op, endpoints=registered)
        self.assertEqual(len(self.op), summary["operators_free"])
        self.assertFalse(summary["free_unknown"])

    def test_without_the_endpoint_list_free_is_unknown_not_zero(self):
        # Ноль здесь читался бы со стены как «на линии никого», хотя мы просто не знаем.
        # «В разговоре» и «на перерыве» при этом известны всегда — их и показываем.
        calls = [{"employee_number": self.op[0]}]
        summary = source.summarize_presence_operators(self.employees, self.op, live_calls=calls)
        self.assertIsNone(summary["operators_free"])
        self.assertIsNone(summary["operators_online"])
        self.assertIsNone(summary["operators_other"])
        self.assertEqual(1, summary["operators_talking"])
        self.assertEqual(len(self.op), summary["operators_total"])
        self.assertTrue(summary["free_unknown"])

    def test_ranks_do_not_overlap(self):
        # Разговор > перерыв > свободен. Иначе человек с зарегистрированным
        # телефоном попал бы и в «свободен», и в «на перерыве», и сумма разрядов
        # перестала бы сходиться с числом людей.
        people = self.employees["employees"]
        people[self.op[0]]["presence_state"] = "break in work"
        people[self.op[1]]["presence_state"] = "break in work"
        calls = [{"employee_number": self.op[0]}]
        summary = source.summarize_presence_operators(
            self.employees, self.op, live_calls=calls,
            endpoints={number: True for number in self.op})
        self.assertEqual(1, summary["operators_talking"])
        self.assertEqual(1, summary["operators_on_break"])
        self.assertEqual(len(self.op) - 2, summary["operators_free"])
        self.assertEqual(
            summary["operators_total"],
            summary["operators_online"] + summary["operators_on_break"]
            + summary["operators_other"])

    def test_break_list_carries_the_time_in_status(self):
        people = self.employees["employees"]
        people[self.op[0]]["presence_state"] = "break in work"
        people[self.op[0]]["presence_state_updated_at"] = 1788868000
        summary = source.summarize_presence_operators(
            self.employees, self.op, now_ts=1788868300)
        self.assertEqual(1, len(summary["break_list"]))
        item = summary["break_list"][0]
        self.assertEqual(300, item["seconds"])
        self.assertEqual("break", item["reason_key"])

    def test_unknown_time_in_status_is_empty_not_zero(self):
        people = self.employees["employees"]
        people[self.op[0]]["presence_state"] = "break in work"
        people[self.op[0]]["presence_state_updated_at"] = None
        summary = source.summarize_presence_operators(self.employees, self.op, now_ts=1788868300)
        self.assertIsNone(summary["break_list"][0]["seconds"])


class QueueClientsTests(unittest.TestCase):
    def test_empty_queue_has_no_rows(self):
        self.assertEqual([], source.parse_queue_clients(fixture("queue_idle.html")))

    def test_client_row_gives_waiting_time_and_never_the_phone(self):
        # Разметку занятой очереди живьём снять не удалось, поэтому строку
        # собираем сами по шапке таблицы. Тест закрепляет ровно одно: номер
        # звонящего парсер наружу не отдаёт — на стене висит время ожидания.
        html = fixture("queue_idle.html").replace(
            "</table>\n\t\t\t\t\t\t\t<br><br><br>",
            '<tr class="clients__line"><td>Клиент</td>'
            '<td>77015550001</td><td>00:42</td></tr></table>\n\t\t\t\t\t\t\t<br><br><br>',
            1)
        rows = source.parse_queue_clients(html)
        self.assertEqual([{"waiting_text": "00:42", "waiting_seconds": 42}], rows)
        self.assertNotIn("77015550001", json.dumps(rows, ensure_ascii=False))

    def test_unverified_rows_do_not_reach_the_snapshot(self):
        self.assertFalse(source.QUEUE_CLIENT_ROWS_VERIFIED)


class DurationTests(unittest.TestCase):
    def test_known_shapes(self):
        self.assertEqual(129, source.parse_duration("02:09"))
        self.assertEqual(28545, source.parse_duration("07:55:45"))
        self.assertEqual(42, source.parse_duration("42"))
        self.assertEqual(0, source.parse_duration("00:00"))

    def test_nothing_to_parse_is_none_not_zero(self):
        # Ноль означал бы «мгновенно» — это враньё, а не отсутствие данных.
        for value in ("", None, "—", "15:49\xa0 08-09-2026"):
            self.assertIsNone(source.parse_duration(value), value)

    def test_percent(self):
        self.assertAlmostEqual(0.983, source.parse_percent("98.30%"), places=4)
        self.assertAlmostEqual(1.0, source.parse_percent("100,00%"), places=4)
        self.assertIsNone(source.parse_percent(""))


class QueueIdTests(unittest.TestCase):
    def test_id_comes_from_the_index_page(self):
        self.assertEqual("4242", source.resolve_queue_id(fixture("queues_index.html")))

    def test_no_hardcoded_queue_id_in_the_module(self):
        # Очередь в кабинете могут пересобрать: зашитый id молча показал бы
        # чужую очередь. Проверяем и живой id прода, и id фикстуры.
        text = ROOT.joinpath("tez_wallboard_source.py").read_text(encoding="utf-8")
        self.assertNotIn("6121", text)
        self.assertNotIn("queueID=4242", text)

    def test_missing_queue_gives_none(self):
        self.assertIsNone(source.resolve_queue_id("<html>нет очередей</html>"))


class EmployeesTests(unittest.TestCase):
    def setUp(self):
        self.parsed = source.parse_employees(fixture("employees_day.json"))

    def test_whitelisted_fields_only(self):
        record = self.parsed["employees"]["903"]
        self.assertEqual(
            {"number", "ext_id", "name", "department", "presence_state",
             "presence_state_updated_at", "call_center_enabled", "stats"},
            set(record))
        self.assertEqual(
            {"incoming_success", "incoming_failed", "incoming_billsec", "incoming_waitsec",
             "outgoing_amount", "outgoing_success", "outgoing_failed", "outgoing_billsec"},
            set(record["stats"]))

    def test_no_sip_credentials_leave_the_parser(self):
        # В ответе кабинета лежат живые login/password ВСЕХ линий. Обход
        # рекурсивный: утечка в любом вложенном словаре — инцидент.
        for path, value in walk(self.parsed):
            self.assertIsNone(SECRET_KEY_RE.search(str(path)),
                              "секретное поле в разборе: %s" % path)
        dumped = json.dumps(self.parsed, ensure_ascii=False)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, dumped)

    def test_no_customer_phone_numbers_leave_the_parser(self):
        # incomingUniqueCheck/outgoingUniqueCheck — это телефоны клиентов.
        dumped = json.dumps(self.parsed, ensure_ascii=False)
        self.assertEqual([], re.findall(r"\b7\d{10}\b", dumped))

    def test_queue_membership_flag_is_kept(self):
        # В отделе Binotel номеров больше, чем в очереди: 901 очередь не
        # обслуживает, и в счётчики ТП попадать не должна.
        self.assertFalse(self.parsed["employees"]["901"]["call_center_enabled"])
        self.assertTrue(self.parsed["employees"]["903"]["call_center_enabled"])

    def test_departments_are_listed(self):
        names = {dep["name"] for dep in self.parsed["departments"].values()}
        self.assertIn("Отдел продаж", names)
        self.assertIn("Технический отдел", names)

    def test_broken_payload_is_rejected(self):
        for payload in ("{}", '{"pageData": {}}', "не json"):
            with self.assertRaises(source.CabinetParseError):
                source.parse_employees(payload)


class DayTotalsTests(unittest.TestCase):
    def setUp(self):
        self.employees = source.parse_employees(fixture("employees_day.json"))
        self.queue = source.parse_queue_page(fixture("queue_idle.html"))
        self.tp = [number for number, record in self.employees["employees"].items()
                   if record["department"] == "Технический отдел" and record["call_center_enabled"]]
        self.op = [number for number, record in self.employees["employees"].items()
                   if record["department"] == "Отдел продаж"]

    def test_tp_takes_queue_numbers_and_counts_talk_time_itself(self):
        totals = source.day_totals(self.employees, self.tp, queue=self.queue)
        self.assertEqual(58, totals["served"])
        self.assertEqual(0, totals["lost"])
        self.assertEqual(58, totals["arrived"])
        self.assertEqual(0.0, totals["ar_ratio"])
        self.assertAlmostEqual(0.983, totals["sl_ratio"], places=4)
        self.assertEqual(2, totals["avg_wait_seconds"])
        # 7525 / 58 = 129,74 с. Усекаем, а не округляем: «02:09» на странице
        # кабинета — это 129 секунд, а округление дало бы на стене 2:10.
        self.assertEqual(129, totals["avg_talk_seconds"])
        self.assertEqual(79, totals["outgoing_total"])
        self.assertEqual(32, totals["outgoing_success"])

    def test_op_has_no_queue_and_therefore_no_service_level(self):
        totals = source.day_totals(self.employees, self.op)
        self.assertIsNone(totals["sl_ratio"])
        self.assertEqual(50, totals["outgoing_total"])
        self.assertEqual(28, totals["outgoing_success"])

    def test_nothing_to_divide_gives_none_not_zero(self):
        # Ноль на табло читается как «всё хорошо, звонков нет» — опасная ложь.
        # Пустой состав — это не «отдел не звонил», а сорванная привязка людей к
        # линиям (sip_number не уникален, переименование направления обнуляет
        # direction_id — оба случая в этом проекте уже были). Поэтому пусто ВСЁ,
        # включая исходящие: иначе стена обвинила бы отдел продаж в безделье.
        totals = source.day_totals(self.employees, [])
        self.assertIsNone(totals["ar_ratio"])
        self.assertIsNone(totals["sl_ratio"])
        self.assertIsNone(totals["avg_wait_seconds"])
        self.assertIsNone(totals["avg_talk_seconds"])
        self.assertIsNone(totals["outgoing_total"])
        self.assertIsNone(totals["outgoing_success"])

    def test_unmatched_numbers_are_empty_not_zero(self):
        # Состав есть, но ни один номер не нашёлся в ответе кабинета — тот же
        # диагноз, что и у пустого состава.
        totals = source.day_totals(self.employees, ["777", "778"])
        self.assertIsNone(totals["outgoing_total"])
        self.assertIsNone(totals["served"])

    def test_outgoing_talk_time_is_counted_apart_for_sales(self):
        # У ОП входящих нет вовсе, и «средняя длительность разговора» из
        # постановки может значить только исходящие.
        totals = source.day_totals(self.employees, self.op)
        self.assertIsNone(totals["avg_talk_seconds"])
        self.assertEqual(60, totals["avg_outgoing_talk_seconds"])

    def test_lost_calls_make_the_ar_ratio(self):
        queue = dict(self.queue, served=90, lost=10, sl_ratio=None, avg_wait_seconds=None)
        totals = source.day_totals(self.employees, self.tp, queue=queue)
        self.assertEqual(100, totals["arrived"])
        self.assertAlmostEqual(0.1, totals["ar_ratio"], places=4)

    def test_directions_are_counted_over_different_people(self):
        tp_totals = source.day_totals(self.employees, self.tp)
        op_totals = source.day_totals(self.employees, self.op)
        self.assertNotEqual(tp_totals["outgoing_total"], op_totals["outgoing_total"])


class LiveCallsTests(unittest.TestCase):
    def test_incoming_and_outgoing_are_distinguished(self):
        incoming = source.parse_live_calls(fixture("live_calls_incoming.json"))
        outgoing = source.parse_live_calls(fixture("live_calls_outgoing.json"))
        self.assertEqual(["incoming", "incoming"], [call["call_type"] for call in incoming])
        self.assertEqual(["outgoing"], [call["call_type"] for call in outgoing])
        self.assertEqual({"909", "903"}, {call["employee_number"] for call in incoming})

    def test_empty_body_is_an_empty_list(self):
        # Когда звонков нет, кабинет отдаёт два байта «[]» — и иногда ничего.
        for payload in (fixture("live_calls_empty.json"), "", b"", b"[]", None, []):
            self.assertEqual([], source.parse_live_calls(payload))

    def test_json_is_parsed_regardless_of_content_type(self):
        # Кабинет отдаёт JSON с Content-Type: text/html — разбираем текст сами.
        raw = fixture("live_calls_incoming.json")
        self.assertEqual(2, len(source.parse_live_calls(raw.encode("utf-8"))))

    def test_negative_billsec_is_ring_time_not_a_crash(self):
        calls = source.parse_live_calls(fixture("live_calls_outgoing.json"))
        self.assertEqual(-13, calls[0]["billsec"])
        self.assertEqual(13, calls[0]["ring_seconds"])
        self.assertEqual(16, calls[0]["duration"])

    def test_customer_numbers_never_leave_the_parser(self):
        dumped = json.dumps(source.parse_live_calls(fixture("live_calls_incoming.json")),
                            ensure_ascii=False)
        self.assertEqual([], re.findall(r"\b7\d{10}\b", dumped))
        self.assertNotIn("Калиев", dumped)

    def test_html_instead_of_json_is_rejected(self):
        with self.assertRaises(source.CabinetParseError):
            source.parse_live_calls("<!doctype html><html><body>SPA</body></html>")

    def test_active_calls_are_counted_per_direction(self):
        calls = source.parse_live_calls(fixture("live_calls_incoming.json"))
        self.assertEqual(2, source.count_active_calls(calls, ["903", "909"]))
        self.assertEqual(1, source.count_active_calls(calls, ["909"]))
        self.assertEqual(0, source.count_active_calls(calls, ["920", "921"]))


class EndpointsTests(unittest.TestCase):
    def test_registration_map(self):
        states = source.parse_endpoints(fixture("endpoints.html"))
        self.assertTrue(states["903"])
        self.assertFalse(states["901"])
        self.assertEqual(19, len(states))

    def test_sip_logins_are_not_taken_from_the_tooltip(self):
        dumped = json.dumps(source.parse_endpoints(fixture("endpoints.html")), ensure_ascii=False)
        self.assertNotIn("sip-fixture", dumped)

    def test_credentials_table_does_not_survive_parsing(self):
        """Ни одно значение колонок «Логин»/«Пароль» не выходит из разбора.

        Проверка берёт значения ИЗ САМОЙ фикстуры, а не сверяется со списком
        заглушек: тогда она не зависит от того, чем обезличена фикстура, и
        переживёт её переливку свежим дампом кабинета.
        """
        html = fixture("endpoints.html")
        head = html.find('<th class="password">')
        self.assertNotEqual(-1, head, "в фикстуре пропала таблица учётных данных")
        block = html[head:html.find("</table>", head)]
        values = set()
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S):
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) >= 4 and cells[0].isdigit():
                values.update(cells[2:4])
        self.assertTrue(values, "колонки логина и пароля в фикстуре не разобрались")
        dumped = json.dumps(source.parse_endpoints(html), ensure_ascii=False)
        for value in sorted(values):
            self.assertNotIn(value, dumped)

    def test_foreign_page_is_rejected(self):
        with self.assertRaises(source.CabinetParseError):
            source.parse_endpoints("<html><body>logining[email]</body></html>")


class FixtureNeedlesTests(unittest.TestCase):
    """Иголки для проверок «учётка не пережила разбор» обязаны БЫТЬ в фикстурах.

    08.09.2026 их там не было вовсе: обезличивание при добавлении фикстуры
    прошло по одним значениям, а проверки искали другие — и проходили вхолостую,
    пока в endpoints.html лежали 19 боевых SIP-учёток. Барьер, который ищет
    отсутствующее, не барьер. Этот тест падает раньше, чем страж успеет стать
    бесполезным.
    """

    def test_needles_are_present_in_fixtures(self):
        blob = fixture("endpoints.html") + fixture("employees_day.json")
        for secret in SECRET_VALUES:
            self.assertIn(
                secret, blob,
                "иголка %r исчезла из фикстур — проверка разбора стала холостой. "
                "Либо верните значение в фикстуру, либо обновите SECRET_VALUES "
                "под новое обезличивание." % secret)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeHttpSession:
    def __init__(self, router):
        self.router = router
        self.urls = []

    def get(self, url, timeout=None, headers=None):
        self.urls.append(url)
        return _FakeResponse(self.router(url))


class _FakeClient:
    base_url = "https://cabinet.example"

    def __init__(self, router):
        self.session = _FakeHttpSession(router)
        self.logins = 0

    def authenticate(self):
        self.logins += 1
        return self


class CabinetSessionTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("TEZ_WALLBOARD_QUEUE_ID", None)

    def tearDown(self):
        os.environ.pop("TEZ_WALLBOARD_QUEUE_ID", None)
        if self._env is not None:
            os.environ["TEZ_WALLBOARD_QUEUE_ID"] = self._env

    def _session(self, pages):
        pages = list(pages)
        client = _FakeClient(lambda url: pages.pop(0) if pages else "")
        return source.CabinetSession(config={"login": "x", "password": "y",
                                             "base_url": "https://cabinet.example",
                                             "tz": "Asia/Almaty"},
                                     client=client), client

    def test_relogin_happens_on_the_login_form_marker(self):
        # Признак протухшей сессии — форма логина в ответе, а не таймер:
        # cookie кабинета живут долго, но серверный сборщик мусора рубит их
        # когда захочет.
        session, client = self._session([
            "<html>%s</html>" % source.LOGIN_FORM_MARKER,
            "<html>данные</html>",
        ])
        self.assertEqual("<html>данные</html>", session.get_text("/main/?module=x"))
        self.assertEqual(1, client.logins)
        self.assertEqual(2, len(client.session.urls))

    def test_relogin_is_attempted_once(self):
        session, client = self._session(["<html>%s</html>" % source.LOGIN_FORM_MARKER] * 4)
        with self.assertRaises(source.CabinetAuthError):
            session.get_text("/main/?module=x")
        self.assertEqual(1, client.logins)
        self.assertEqual(2, len(client.session.urls))

    def test_queue_id_is_fetched_once_and_cached(self):
        session, client = self._session([fixture("queues_index.html")])
        self.assertEqual("4242", session.queue_id())
        self.assertEqual("4242", session.queue_id())
        self.assertEqual(1, len(client.session.urls))

    def test_env_overrides_the_queue_id_without_a_request(self):
        os.environ["TEZ_WALLBOARD_QUEUE_ID"] = "777"
        session, client = self._session([])
        self.assertEqual("777", session.queue_id())
        self.assertEqual([], client.session.urls)

    def test_index_without_a_queue_is_an_error(self):
        session, _ = self._session(["<html>очередей нет</html>"])
        with self.assertRaises(source.CabinetParseError):
            session.queue_id()

    def test_endpoints_have_their_own_ttl(self):
        # Ручка дорогая (кабинет пингует каждую линию, ~3,5 с) — в общий шаг
        # опроса её пускать нельзя.
        session, client = self._session([fixture("endpoints.html")] * 3)
        first, age = session.endpoints(ttl_seconds=120)
        self.assertEqual(0, age)
        second, age = session.endpoints(ttl_seconds=120)
        self.assertIs(first, second)
        self.assertEqual(1, len(client.session.urls))
        session.endpoints(ttl_seconds=0)
        self.assertEqual(2, len(client.session.urls))

    def test_network_failure_is_wrapped(self):
        class _Boom:
            base_url = "https://cabinet.example"
            logins = 0

            class session:
                @staticmethod
                def get(url, timeout=None, headers=None):
                    raise OSError("сеть отвалилась")

            def authenticate(self):
                return self

        session = source.CabinetSession(config={"login": "x", "password": "y"}, client=_Boom())
        with self.assertRaises(source.CabinetError):
            session.get_text("/main/?module=x")

    def test_missing_credentials_are_named(self):
        session = source.CabinetSession(config={"login": "", "password": ""})
        with self.assertRaises(source.CabinetAuthError):
            session.ensure()
        self.assertFalse(source.is_configured({"login": "", "password": ""}))
        self.assertTrue(source.is_configured({"login": "a", "password": "b"}))


class _FakeSession:
    """Кабинет из фикстур: маршрутизация по адресу, без сети."""

    tz_name = "Asia/Almaty"

    def __init__(self, live="live_calls_incoming.json"):
        self.live = live
        self.paths = []

    def queue_id(self):
        return "4242"

    def get_text(self, path, timeout=None):
        self.paths.append(path)
        if "module=stateOfQueues" in path:
            return fixture("queue_idle.html")
        if "module=analyticsEmployees" in path:
            return fixture("employees_day.json")
        if "action=loadCalls" in path:
            return fixture(self.live)
        if "hs_pbx_listOfEndpoints" in path:
            return fixture("endpoints.html")
        raise AssertionError("незнакомый адрес кабинета: %s" % path)

    def endpoints(self, ttl_seconds=None, force=False):
        return source.parse_endpoints(self.get_text(source.ENDPOINTS_PATH)), 0


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.session = _FakeSession()
        self.snapshot = source.fetch_snapshot(self.session)

    def test_one_walk_over_the_cabinet(self):
        # Сессия у кабинета одна (PHPSESSID в cookie), поэтому обход
        # последовательный и без повторов.
        self.assertEqual(3, len(self.session.paths))
        self.assertIn("queueID=4242", self.session.paths[0])
        self.assertIn("module=analyticsEmployees", self.session.paths[1])
        self.assertIn("action=loadCalls", self.session.paths[2])
        self.assertIn("&mbav=1", self.session.paths[2])

    def test_shape_of_the_snapshot(self):
        self.assertEqual(
            {"day", "binotel_now", "binotel_now_source", "binotel_now_ts",
             "sl_threshold_seconds", "queue_id", "queue", "queue_error",
             "queue_clients", "employees", "live_calls", "endpoints",
             "endpoints_age_seconds", "diagnostics"},
            set(self.snapshot))
        self.assertEqual(20, self.snapshot["sl_threshold_seconds"])
        self.assertEqual("4242", self.snapshot["queue_id"])
        self.assertIsNone(self.snapshot["endpoints"])
        self.assertRegex(self.snapshot["day"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(self.snapshot["binotel_now"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_cabinet_clock_is_preferred_over_ours(self):
        self.assertEqual("cabinet", self.snapshot["binotel_now_source"])
        # 1788868288 в Asia/Almaty — 08.09.2026 16:51:28.
        self.assertEqual("2026-09-08 16:51:28", self.snapshot["binotel_now"])

    def test_without_active_calls_the_clock_is_ours_and_it_is_said(self):
        snapshot = source.fetch_snapshot(_FakeSession(live="live_calls_empty.json"))
        self.assertEqual("local", snapshot["binotel_now_source"])
        self.assertEqual("local", snapshot["diagnostics"]["binotel_now_source"])

    def test_unverified_client_rows_are_not_shown(self):
        self.assertEqual([], self.snapshot["queue_clients"])
        self.assertTrue(self.snapshot["diagnostics"]["queue_rows_unverified"])

    def test_endpoints_are_optional(self):
        snapshot = source.fetch_snapshot(_FakeSession(), with_endpoints=True)
        self.assertEqual(19, len(snapshot["endpoints"]))
        self.assertEqual(0, snapshot["endpoints_age_seconds"])

    def test_skewed_page_kills_the_queue_but_not_the_sales_board(self):
        # Радиус поражения — только ТП: очередь есть лишь у них. Раньше поехавшая
        # вёрстка ОДНОЙ страницы снимала со стены оба табло, включая то, которому
        # очередь вообще не нужна.
        class _Skewed(_FakeSession):
            def get_text(self, path, timeout=None):
                if "module=stateOfQueues" in path:
                    self.paths.append(path)
                    return fixture("queue_counters_skewed.html")
                return _FakeSession.get_text(self, path, timeout=timeout)

        snapshot = source.fetch_snapshot(_Skewed())
        self.assertIsNone(snapshot["queue"])
        self.assertIn("сотрудников с тел. линией", snapshot["queue_error"])
        # Материал для ОП приехал целиком.
        self.assertTrue(snapshot["employees"]["employees"])
        self.assertEqual(snapshot["queue_error"], snapshot["diagnostics"]["queue_error"])

    def test_broken_queue_page_is_still_a_parse_error(self):
        # Сам страж при этом обязан срабатывать: перекос ловится, просто дальше
        # обрабатывается как отказ одной половины, а не всего снимка.
        with self.assertRaises(source.CabinetParseError):
            source.validate_queue_counters(
                source.parse_queue_page(fixture("queue_counters_skewed.html")))

    def test_login_failure_kills_everything(self):
        # А вот вход — общая беда: следующие два запроса тоже вернут форму логина.
        class _NoAuth(_FakeSession):
            def get_text(self, path, timeout=None):
                raise source.CabinetAuthError("Binotel: вход в кабинет не выполнен")

        with self.assertRaises(source.CabinetAuthError):
            source.fetch_snapshot(_NoAuth())

    def test_no_credentials_and_no_customer_data_in_the_snapshot(self):
        for path, value in walk(self.snapshot):
            self.assertIsNone(SECRET_KEY_RE.search(str(path)),
                              "секретное поле в снимке: %s" % path)
        dumped = json.dumps(self.snapshot, ensure_ascii=False)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, dumped)
        self.assertEqual([], re.findall(r"\b7\d{10}\b", dumped))


class ModuleBoundaryTests(unittest.TestCase):
    def test_module_does_not_depend_on_flask_or_database(self):
        # Ради этого модуль и вынесен в отдельный файл: database.py на импорте
        # поднимает пул к боевой базе, и тесты разбора стали бы походом в прод.
        tree = ast.parse(ROOT.joinpath("tez_wallboard_source.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(set(), imported & {"flask", "database", "bot_schedule2"})


if __name__ == "__main__":
    unittest.main()
