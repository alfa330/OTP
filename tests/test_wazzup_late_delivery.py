"""Опоздавшие входящие Wazzup: время доставки вместо времени отправки.

Случай 05.10.2026 («Чаты ОП»): канал «2ГИС» на обычном WhatsApp простоял
16.09–05.10, клиент написал 25.09 в 18:45, а Wazzup дослал вопросы, когда канал
вернулся, — со временем ОТПРАВКИ. У нас они встали под 25 сентября, и диалог
05.10 выглядел оборванным; в самом Wazzup они стоят там, где оператор их увидел.

Тесты закрепляют то, что ломается молча:
* правило трогает только входящие с минутным временем (у WABA время точное, и
  опоздание там — задержка вебхука, а не канала);
* опоздавшее встаёт в начало минуты доставки, раньше ответа оператора, — кроме
  случая, когда ответ на прежнее опоздавшее этого чата ещё в пути: тогда в точный
  момент прихода (жалоба 06.10, разбор 33 спорных случаев в wazzup/delivery.py);
* опоздавшее не встаёт раньше уже известного в чате, равное время разводится;
* задержка доставки вебхуков вообще — не опоздание канала, время не трогаем;
* приёмник и разовая починка приходят к одному и тому же порядку;
* починка не удаляет эпизод, на который ссылается оценка или выборка (условие
  проверяется на данных в sqlite), и в холостом прогоне откатывает всё.
"""

import ast
import logging
import re
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests import source_cache
from wazzup import delivery

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
API_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
DB_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
VIEW_SOURCE = (ROOT / "src" / "components" / "wazzup" / "WazzupChatsView.jsx").read_text(encoding="utf-8-sig")
QA_SCHEMA = (ROOT / "call_qa" / "rag" / "schema.sql").read_text(encoding="utf-8-sig")
MARKETING_SCHEMA = (ROOT / "call_qa" / "marketing" / "schema.py").read_text(encoding="utf-8-sig")

UTC = timezone.utc
ALMATY = timezone(timedelta(hours=5))
WA, WABA = "ch-wa", "ch-waba"


def almaty(*args):
    return datetime(*args, tzinfo=ALMATY).astimezone(UTC)


def iso(dt):
    """dateTime так, как его присылает Wazzup."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _database_class():
    """Нужные методы Database без импорта монолита (он поднимает пул к базе)."""
    methods = {"store_wazzup_messages", "_wazzup_chat_history_tx", "_wazzup_recent_arrivals_tx",
               "_refresh_wazzup_chat_tx", "_wazzup_episode_kept_sql",
               "retime_late_wazzup_messages"}
    attrs = {"WAZZUP_EPISODE_REFERENCES", "WAZZUP_EPISODE_LOCK_KEY"}
    module = source_cache.parse(DB_SOURCE)
    source_class = next(node for node in module.body
                        if isinstance(node, ast.ClassDef) and node.name == "Database")
    body = []
    for node in source_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in methods:
            body.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id in attrs):
            body.append(node)
    found = {getattr(n, "name", None) or n.targets[0].id for n in body}
    missing = (methods | attrs) - found
    if missing:
        raise AssertionError(f"в Database нет: {sorted(missing)}")
    test_module = ast.fix_missing_locations(ast.Module(
        body=[ast.ClassDef(name="Database", bases=[], keywords=[], body=body,
                           decorator_list=[])], type_ignores=[]))
    namespace = {"logging": logging}
    exec(compile(test_module, str(DATABASE_PATH), "exec"), namespace)
    return namespace["Database"]


Database = _database_class()
ALL_REFERENCE_TABLES = tuple(table for table, _ in Database.WAZZUP_EPISODE_REFERENCES)

HISTORY_MARK = "SELECT is_echo, dt, created_at, wazzup_dt IS NOT NULL"
RECENT_MARK = "SELECT dt, created_at FROM wazzup_messages WHERE account = %s"
REPAIR_MARK = "SELECT m.message_id, m.channel_id"


class _Connection:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


class _Cursor:
    """Курсор-заглушка: пишет каждый запрос, на SELECT/RETURNING отдаёт
    заготовленные строки по признаку запроса."""

    def __init__(self, answers=None, update_rowcount=None):
        self.answers = answers or {}
        self.update_rowcount = update_rowcount
        self.executions = []
        self.connection = _Connection()
        self.rowcount = 0
        self._rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self.executions.append((sql, params))
        self._rows = []
        self.rowcount = 0
        for marker, rows in self.answers.items():
            if marker in sql:
                self._rows = list(rows(params) if callable(rows) else rows)
                break
        if sql.startswith("UPDATE wazzup_messages m"):
            self.rowcount = (self.update_rowcount if self.update_rowcount is not None
                             else len(params[0]))
        elif sql.startswith("INSERT INTO wazzup_messages"):
            self.rowcount = 1

    def fetchall(self):
        return list(self._rows)

    def sql(self, prefix):
        return [(sql, params) for sql, params in self.executions if sql.startswith(prefix)]


def _recent_answer(rows):
    """Ответ на выборку недавних приходов: rows — (dt, created_at) аккаунта."""
    def answer(params):
        _, dt_from, dt_to, arrived_from, arrived_to = params
        return [(dt, created) for dt, created in rows
                if dt_from <= dt < dt_to and arrived_from <= created < arrived_to]
    return answer


class _TableCursor(_Cursor):
    """Курсор поверх «таблицы» в памяти: вставка вебхука кладёт строку, выборки
    истории чата и недавних приходов отвечают из неё — так приёмник проходится
    вебхук за вебхуком."""

    def __init__(self, table):
        super().__init__()
        self.table = table
        self.received_at = None

    def execute(self, query, params=None):
        super().execute(query, params)
        sql = self.executions[-1][0]
        if sql.startswith("INSERT INTO wazzup_messages"):
            stored = params[4]
            self.table.append({
                "message_id": params[0], "channel_id": params[1], "chat_id": params[3],
                "is_echo": params[5],
                "dt": stored if isinstance(stored, datetime) else delivery.parse_wazzup_dt(stored),
                "created_at": self.received_at, "late": params[-1] is not None})
        elif HISTORY_MARK in sql:
            channel_id, chat_id, since, until = params
            self._rows = [(r["is_echo"], r["dt"], r["created_at"], r["late"]) for r in self.table
                          if r["channel_id"] == channel_id and r["chat_id"] == chat_id
                          and since <= r["created_at"] < until]
        elif RECENT_MARK in sql:
            self._rows = _recent_answer([(r["dt"], r["created_at"]) for r in self.table])(params)


def _db(cursor):
    database = Database()

    @contextmanager
    def _get_cursor():
        yield cursor

    database._get_cursor = _get_cursor
    return database


# Чат из жалобы (номер и тексты заменены). Время — Алматы: (id, исходящее,
# dateTime из вебхука, приход вебхука[, канал]). В самом Wazzup порядок такой:
# вопрос (пришёл 12:22), приветствие и «какой город?» (нажаты 12:25–12:26,
# телефон отправил в 12:35), два досланных вопроса (12:35:10), ответ клиента
# (12:43), ответ оператора (12:48).
INCIDENT = [
    ("q1", False, (2026, 9, 25, 18, 45, 0, 1000), (2026, 10, 5, 12, 22, 8, 822700)),
    ("q2", False, (2026, 9, 25, 18, 45, 0, 2000), (2026, 10, 5, 12, 35, 10, 161292)),
    ("q3", False, (2026, 9, 25, 18, 45, 0, 3000), (2026, 10, 5, 12, 35, 10, 313565)),
    ("hello", True, (2026, 10, 5, 12, 35, 0, 1000), (2026, 10, 5, 12, 35, 18, 734677)),
    ("city?", True, (2026, 10, 5, 12, 35, 0, 2000), (2026, 10, 5, 12, 35, 18, 838870)),
    ("city", False, (2026, 10, 5, 12, 35, 0, 3000), (2026, 10, 5, 12, 43, 27, 469946)),
    ("answer", True, (2026, 10, 5, 12, 49, 0, 1000), (2026, 10, 5, 12, 50, 24, 245567)),
]
INCIDENT_WAZZUP_ORDER = ["q1", "hello", "city?", "q2", "q3", "city", "answer"]

# Первое обращение после возврата канала: три досланных вопроса пришли в одну
# минуту, приветствие и вопрос оператора — ответ на них (у самого клиента до этого
# ничего не было, ответить раньше оператор не мог).
FIRST_CONTACT = [
    ("x1", False, (2026, 9, 21, 22, 59, 0, 2000), (2026, 10, 5, 12, 39, 39, 199000)),
    ("x2", False, (2026, 9, 21, 22, 59, 0, 3000), (2026, 10, 5, 12, 39, 39, 382000)),
    ("x3", False, (2026, 9, 21, 22, 59, 0, 4000), (2026, 10, 5, 12, 39, 39, 487000)),
    ("hi", True, (2026, 10, 5, 12, 39, 0, 1000), (2026, 10, 5, 12, 39, 51, 38000)),
    ("where?", True, (2026, 10, 5, 12, 39, 0, 2000), (2026, 10, 5, 12, 39, 51, 211000)),
]

# Обычная работа: текст клиента пришёл вовремя, пропущенный звонок — через 4,5
# минуты, оператор ответил на звонок в ту же минуту, когда о нём узнал.
MISSED_CALL = [
    ("text", False, (2026, 9, 2, 11, 54, 0, 1000), (2026, 9, 2, 11, 55, 5, 458000)),
    ("call", False, (2026, 9, 2, 11, 55, 0, 1000), (2026, 9, 2, 11, 59, 27, 174000)),
    ("greet", True, (2026, 9, 2, 11, 59, 0, 1000), (2026, 9, 2, 11, 59, 38, 274000)),
    ("no-calls", True, (2026, 9, 2, 11, 59, 0, 2000), (2026, 9, 2, 11, 59, 49, 103000)),
]

# Ответ на первое опоздавшее пришёл раньше второго опоздавшего в той же минуте:
# второе встаёт ПОСЛЕ ответа — так его увидел оператор (разбор, находка «ответ,
# пришедший раньше, уезжал вниз»).
REPLY_KNOWN = [
    ("qa", False, (2026, 9, 25, 10, 0, 0, 1000), (2026, 10, 5, 12, 39, 50)),
    ("reply", True, (2026, 10, 5, 12, 40, 0, 1000), (2026, 10, 5, 12, 40, 5)),
    ("qb", False, (2026, 9, 25, 10, 0, 0, 2000), (2026, 10, 5, 12, 40, 30)),
]

# Канал жив, но вебхуки лежали у нас: вопрос и ответ пришли повторной доставкой
# через 10 минут, и WABA-сообщения в те же минуты тоже опоздали. В Wazzup вопрос
# был вовремя — время из вебхука верное, двигать его нельзя.
OUR_OUTAGE = [
    ("q", False, (2026, 10, 5, 10, 0, 0, 1000), (2026, 10, 5, 10, 10, 2)),
    ("r", True, (2026, 10, 5, 10, 2, 0, 1000), (2026, 10, 5, 10, 10, 3)),
]
OUR_OUTAGE_WABA = [
    ("w1", False, (2026, 10, 5, 10, 1, 13, 377000), (2026, 10, 5, 10, 9, 40), WABA),
    ("w2", False, (2026, 10, 5, 10, 3, 47, 230000), (2026, 10, 5, 10, 9, 58), WABA),
]


def _webhook_order(rows, batches=()):
    """Прогоняет сообщения через настоящий store_wazzup_messages в порядке
    прихода: по одному вебхуку на сообщение, а id из batches — одним вебхуком
    (момент прихода — у первого). Возвращает id чата WA в порядке ленты и время по id."""
    table = []
    cursor = _TableCursor(table)
    database = _db(cursor)
    grouped = {mid: group for group in batches for mid in group}
    done = set()
    for row in sorted(rows, key=lambda r: r[3]):
        mid = row[0]
        if mid in done:
            continue
        group = [r for r in rows if r[0] in grouped.get(mid, (mid,))]
        cursor.received_at = almaty(*row[3])
        payload = []
        for gid, echo, sent, _received, *channel in group:
            channel_id = channel[0] if channel else WA
            message = {"messageId": gid, "channelId": channel_id,
                       "chatId": "77000000000" if channel_id == WA else "77011111111",
                       "chatType": "whatsapp", "dateTime": iso(almaty(*sent)), "isEcho": echo,
                       "type": "text", "text": gid}
            if echo:
                message.update(authorId="9156630", authorName="оператор")
            payload.append(message)
            done.add(gid)
        database.store_wazzup_messages(payload, account="op", received_at=cursor.received_at)
    chat = [r for r in table if r["channel_id"] == WA]
    ordered = sorted(chat, key=lambda r: (r["dt"], r["message_id"]))
    return [r["message_id"] for r in ordered], {r["message_id"]: r["dt"] for r in chat}


def _repair_order(rows, others=()):
    """Те же сообщения, уже лежащие в базе с временем Wazzup, — через настоящую
    разовую починку; others — сообщения других каналов (для задержки вебхуков).
    Возвращает id в порядке ленты после неё."""
    stored = [(mid, WA, "77000000000", echo, almaty(*sent), almaty(*received), False)
              for mid, echo, sent, received in rows]
    stored.sort(key=lambda r: (r[5], r[4], r[0]))
    arrivals = ([(r[4], r[5]) for r in stored]
                + [(almaty(*sent), almaty(*received)) for _, _, sent, received, *_ in others])
    cursor = _Cursor(answers={REPAIR_MARK: stored,
                              RECENT_MARK: _recent_answer(arrivals),
                              "WHERE to_regclass(name) IS NOT NULL": []})
    _db(cursor).retime_late_wazzup_messages(account="op", apply=True)
    moved = {}
    for _, (ids, new_dts) in cursor.sql("UPDATE wazzup_messages m"):
        moved.update(zip(ids, new_dts))
    final = {r[0]: moved.get(r[0], r[4]) for r in stored}
    return [mid for mid, _ in sorted(final.items(), key=lambda item: (item[1], item[0]))]


class MinuteStampTests(unittest.TestCase):
    def test_parse_accepts_wazzup_forms(self):
        expected = datetime(2026, 10, 5, 7, 35, 0, 1000, tzinfo=UTC)
        self.assertEqual(delivery.parse_wazzup_dt("2026-10-05T07:35:00.001Z"), expected)
        self.assertEqual(delivery.parse_wazzup_dt("2026-10-05T12:35:00.001+05:00"), expected)
        # без пояса — UTC: так строку читает Postgres в UTC-сессии Render
        self.assertEqual(delivery.parse_wazzup_dt("2026-10-05T07:35:00.001"), expected)
        self.assertEqual(delivery.parse_wazzup_dt(expected), expected)
        for bad in (None, "", "   ", "вчера", 12345):
            self.assertIsNone(delivery.parse_wazzup_dt(bad), bad)

    def test_minute_stamp_shape(self):
        minute = datetime(2026, 10, 5, 7, 35, tzinfo=UTC)
        self.assertTrue(delivery.is_minute_stamp(minute.replace(microsecond=1000)))
        self.assertTrue(delivery.is_minute_stamp(minute.replace(microsecond=99000)))
        self.assertFalse(delivery.is_minute_stamp(minute), "ровная минута — не счётчик")
        self.assertFalse(delivery.is_minute_stamp(minute.replace(microsecond=100000)))
        self.assertFalse(delivery.is_minute_stamp(minute.replace(microsecond=1500)))
        self.assertFalse(delivery.is_minute_stamp(minute.replace(second=47, microsecond=230000)),
                         "точное время WABA")
        self.assertFalse(delivery.is_minute_stamp(minute.replace(second=47, microsecond=50000)),
                         "точное время WABA с малыми миллисекундами — секунды не нулевые")


class LateInboundTests(unittest.TestCase):
    SENT = almaty(2026, 9, 25, 18, 45, 0, 2000)
    RECEIVED = almaty(2026, 10, 5, 12, 35, 10, 161292)

    def test_only_minute_stamped_inbound_is_late(self):
        self.assertTrue(delivery.is_late_inbound(self.SENT, self.RECEIVED, False))
        self.assertFalse(delivery.is_late_inbound(self.SENT, self.RECEIVED, True),
                         "исходящее: его минута — момент отправки, эхо приходит позже")
        precise = almaty(2026, 9, 2, 17, 52, 47, 230000)
        self.assertFalse(delivery.is_late_inbound(precise, precise + timedelta(minutes=8), False),
                         "WABA: опоздал вебхук, время в нём верное")
        small_ms = almaty(2026, 10, 5, 12, 31, 47, 50000)
        self.assertFalse(delivery.is_late_inbound(small_ms, small_ms + timedelta(minutes=8), False))
        self.assertFalse(delivery.is_late_inbound(self.SENT, None, False))
        self.assertFalse(delivery.is_late_inbound(None, self.RECEIVED, False))

    def test_threshold_is_strict(self):
        sent = almaty(2026, 10, 5, 12, 27, 0, 1000)
        self.assertFalse(delivery.is_late_inbound(sent, sent + delivery.LATE_DELIVERY, False))
        self.assertTrue(delivery.is_late_inbound(
            sent, sent + delivery.LATE_DELIVERY + timedelta(microseconds=1), False))
        # обычная задержка канала (минута + доставка вебхука) — не опоздание
        self.assertFalse(delivery.is_late_inbound(sent, sent + timedelta(seconds=66), False))

    def test_delivery_time(self):
        start = delivery.delivery_time(self.RECEIVED, pending=False)
        self.assertEqual(start.replace(microsecond=0), almaty(2026, 10, 5, 12, 35))
        self.assertEqual(start.microsecond, 102)  # 10,161 с / 100 000 — до счётчика …:00.001
        self.assertEqual(delivery.delivery_time(self.RECEIVED, pending=True), self.RECEIVED)

    def test_delivery_time_never_before_what_was_already_there(self):
        known_reply = almaty(2026, 10, 5, 12, 35, 0, 1000)
        self.assertEqual(delivery.delivery_time(self.RECEIVED, False, after=known_reply),
                         known_reply + timedelta(microseconds=1))
        start = delivery.delivery_time(self.RECEIVED, False)
        self.assertEqual(delivery.delivery_time(self.RECEIVED, False, after=start),
                         start + timedelta(microseconds=1), "равное время разводится")
        earlier = almaty(2026, 10, 5, 12, 22)
        self.assertEqual(delivery.delivery_time(self.RECEIVED, False, after=earlier), start)
        self.assertEqual(delivery.delivery_time(self.RECEIVED, True, after=known_reply),
                         self.RECEIVED)

    def test_start_of_minute_keeps_arrival_order(self):
        minute = almaty(2026, 10, 5, 12, 35)
        times = [delivery.delivery_time(minute + timedelta(seconds=s), False)
                 for s in (0, 10.161292, 10.313565, 59.999999)]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(set(times)), len(times))
        self.assertLess(times[-1], minute + timedelta(milliseconds=1), "весь хвост до …:00.001")


class ReplyPendingTests(unittest.TestCase):
    A = almaty(2026, 10, 5, 12, 35, 10)

    def _msg(self, echo, dt, created_at, late=False):
        return {"is_echo": echo, "dt": dt, "created_at": created_at, "late": late}

    def _late(self, created_at):
        return self._msg(False, created_at.replace(second=0, microsecond=0), created_at, True)

    def test_unanswered_late_inbound_earlier_in_burst(self):
        self.assertTrue(delivery.reply_pending([self._late(almaty(2026, 10, 5, 12, 22, 8))], self.A))

    def test_known_reply_after_it_clears(self):
        q1 = self._late(almaty(2026, 10, 5, 12, 22, 8))
        reply = self._msg(True, almaty(2026, 10, 5, 12, 30, 0, 1000), almaty(2026, 10, 5, 12, 30, 20))
        self.assertFalse(delivery.reply_pending([q1, reply], self.A))
        # ответ, пришедший ПОСЛЕ этого сообщения, приёмник в ту секунду не знал
        late_reply = self._msg(True, almaty(2026, 10, 5, 12, 35, 0, 1000),
                               almaty(2026, 10, 5, 12, 35, 18))
        self.assertTrue(delivery.reply_pending([q1, late_reply], self.A))

    def test_window_edges(self):
        minute = self.A.replace(second=0, microsecond=0)
        self.assertTrue(delivery.reply_pending([self._late(self.A - delivery.PENDING_WINDOW)], self.A),
                        "ровно час до прихода — ещё в окне")
        self.assertFalse(delivery.reply_pending(
            [self._late(self.A - delivery.PENDING_WINDOW - timedelta(microseconds=1))], self.A))
        self.assertTrue(delivery.reply_pending(
            [self._late(minute - timedelta(microseconds=1))], self.A))
        self.assertFalse(delivery.reply_pending([self._late(minute)], self.A),
                         "пришло в начале той же минуты — уже не «раньше минуты»")

    def test_on_time_question_is_ordinary_work(self):
        on_time = self._msg(False, almaty(2026, 10, 5, 12, 22, 0, 1000),
                            almaty(2026, 10, 5, 12, 22, 30), late=False)
        self.assertFalse(delivery.reply_pending([on_time], self.A),
                         "вовремя пришедший вопрос — обычная работа, ответ не застревал")


class DeliveryBacklogTests(unittest.TestCase):
    A = almaty(2026, 10, 5, 10, 10, 2)

    def test_window(self):
        self.assertEqual(delivery.recent_window(self.A),
                         (self.A - timedelta(days=1), self.A - timedelta(minutes=2)))

    def test_precise_channels_late_too_means_webhook_backlog(self):
        late = [(self.A - timedelta(minutes=9, seconds=s), self.A - timedelta(seconds=s))
                for s in (5, 20, 40)]
        on_time = [(self.A - timedelta(seconds=s + 1, microseconds=230000), self.A - timedelta(seconds=s))
                   for s in (10, 30)]
        self.assertTrue(delivery.delivery_backlog(late))
        self.assertTrue(delivery.delivery_backlog(late + on_time[:1]), "большинство опоздало")
        self.assertTrue(delivery.delivery_backlog(late[:1] + on_time[:1]),
                        "ровно половина — доставка уже под вопросом, время не трогаем")
        self.assertFalse(delivery.delivery_backlog(on_time))
        self.assertFalse(delivery.delivery_backlog(late[:1] + on_time), "меньшинство — нет")

    def test_minute_channel_and_quiet_night_say_nothing(self):
        minute_channel = [(almaty(2026, 9, 25, 18, 45, 0, 1000), self.A - timedelta(seconds=30))]
        self.assertFalse(delivery.delivery_backlog(minute_channel),
                         "опоздание минутного канала — его собственное, не доставки")
        self.assertFalse(delivery.delivery_backlog([]))


class ChatOrderTests(unittest.TestCase):
    """Приёмник (вебхук за вебхуком) и разовая починка (по уже лежащим строкам)
    приходят к одному порядку ленты."""

    def _both(self, rows, expected, others=()):
        self.assertEqual(_webhook_order(list(rows) + list(others))[0], expected)
        self.assertEqual(_repair_order(rows, others), expected)

    def test_incident_chat_reads_like_wazzup(self):
        self._both(INCIDENT, INCIDENT_WAZZUP_ORDER)
        order, times = _webhook_order(INCIDENT)
        # весь диалог — в одном дне, вопросы — во время прихода
        self.assertEqual({dt.astimezone(ALMATY).date() for dt in times.values()},
                         {datetime(2026, 10, 5).date()})
        clock = {mid: dt.astimezone(ALMATY).strftime("%H:%M") for mid, dt in times.items()}
        self.assertEqual((clock["q1"], clock["q2"], clock["city"]), ("12:22", "12:35", "12:43"))

    def test_first_contact_after_outage_puts_questions_before_replies(self):
        self._both(FIRST_CONTACT, ["x1", "x2", "x3", "hi", "where?"])

    def test_missed_call_comes_before_the_reply_to_it(self):
        self._both(MISSED_CALL, ["text", "call", "greet", "no-calls"])

    def test_reply_already_there_stays_above(self):
        self._both(REPLY_KNOWN, ["qa", "reply", "qb"])

    def test_webhook_backlog_keeps_wazzup_time(self):
        self._both(OUR_OUTAGE, ["q", "r"], others=OUR_OUTAGE_WABA)
        _, times = _webhook_order(OUR_OUTAGE + OUR_OUTAGE_WABA)
        self.assertEqual(times["q"], almaty(2026, 10, 5, 10, 0, 0, 1000))
        # без опоздавших WABA рядом тот же вопрос — опоздание канала, он сдвигается
        self.assertEqual(_webhook_order(OUR_OUTAGE)[0], ["r", "q"])

    def test_one_webhook_with_several_late_keeps_their_order(self):
        rows = [(f"b{i}", False, (2026, 9, 25, 18, 45, 0, 1000 * (i + 1)),
                 (2026, 10, 5, 12, 35, 10, 161292)) for i in range(4)]
        order, times = _webhook_order(rows, batches=[tuple(r[0] for r in rows)])
        self.assertEqual(order, ["b0", "b1", "b2", "b3"])
        self.assertEqual(len(set(times.values())), 4, "одинакового времени нет")
        self.assertEqual(_repair_order(rows), ["b0", "b1", "b2", "b3"])

    def test_arrivals_closer_than_tenth_of_second_do_not_collide(self):
        rows = [("c0", False, (2026, 9, 25, 18, 45, 0, 1000), (2026, 10, 5, 12, 35, 10, 160000)),
                ("c1", False, (2026, 9, 25, 18, 45, 0, 2000), (2026, 10, 5, 12, 35, 10, 200000))]
        order, times = _webhook_order(rows)
        self.assertEqual(order, ["c0", "c1"])
        self.assertLess(times["c0"], times["c1"])
        self.assertEqual(_repair_order(rows), ["c0", "c1"])


class StoreMessagesTests(unittest.TestCase):
    RECEIVED = almaty(2026, 10, 5, 12, 35, 10, 161292)

    def _store(self, message, received_at, recent=()):
        cursor = _Cursor(answers={HISTORY_MARK: [], RECENT_MARK: _recent_answer(recent)})
        stored = _db(cursor).store_wazzup_messages([message], account="op",
                                                   received_at=received_at)
        self.assertEqual(stored, 1)
        insert = cursor.sql("INSERT INTO wazzup_messages")
        self.assertEqual(len(insert), 1)
        sql, params = insert[0]
        self.assertIn("is_edited, is_deleted, account, wazzup_dt)", sql)
        # время задаётся только вставкой: повторная доставка его не двигает
        self.assertNotIn("dt = EXCLUDED", sql)
        self.assertNotIn("wazzup_dt = EXCLUDED", sql)
        return params[4], params[-1], cursor

    @staticmethod
    def _message(**overrides):
        message = {"messageId": "m-1", "channelId": WA, "chatId": "77000000000",
                   "chatType": "whatsapp", "dateTime": "2026-09-25T13:45:00.002Z",
                   "isEcho": False, "type": "text", "text": "вопрос",
                   "contact": {"name": "77000000000", "phone": "77000000000"},
                   "status": "inbound"}
        message.update(overrides)
        return message

    def test_late_inbound_from_webhook_gets_delivery_time(self):
        dt, wazzup_dt, cursor = self._store(self._message(), self.RECEIVED)
        self.assertEqual(dt, delivery.delivery_time(self.RECEIVED, pending=False))
        self.assertEqual(wazzup_dt, "2026-09-25T13:45:00.002Z", "время Wazzup — рядом, как пришло")
        (history_sql, history_params), = [e for e in cursor.executions if HISTORY_MARK in e[0]]
        self.assertIn("FROM wazzup_messages WHERE channel_id = %s AND chat_id = %s "
                      "AND created_at >= %s AND created_at < %s", history_sql)
        self.assertEqual(history_params, (WA, "77000000000",
                                          self.RECEIVED - timedelta(hours=1, minutes=1),
                                          self.RECEIVED))
        (recent_sql, recent_params), = [e for e in cursor.executions if RECENT_MARK in e[0]]
        self.assertIn("WHERE account = %s AND dt >= %s AND dt < %s "
                      "AND created_at >= %s AND created_at < %s", recent_sql)
        self.assertEqual(recent_params, ("op", self.RECEIVED - timedelta(days=1), self.RECEIVED,
                                         self.RECEIVED - timedelta(minutes=2), self.RECEIVED))

    def test_webhook_backlog_keeps_time(self):
        recent = [(self.RECEIVED - timedelta(minutes=9, seconds=13, microseconds=377000),
                   self.RECEIVED - timedelta(seconds=30))]
        dt, wazzup_dt, cursor = self._store(self._message(), self.RECEIVED, recent=recent)
        self.assertEqual(dt, "2026-09-25T13:45:00.002Z")
        self.assertIsNone(wazzup_dt)
        self.assertEqual([e for e in cursor.executions if HISTORY_MARK in e[0]], [],
                         "при задержке доставки история чата не нужна")

    def test_everything_else_keeps_wazzup_time_without_lookups(self):
        cases = {
            "без момента прихода (забор «Потока»)": (self._message(), None),
            "исходящее": (self._message(isEcho=True, authorId="9156630",
                                        authorName="оператор"), self.RECEIVED),
            "точное время WABA": (self._message(dateTime="2026-10-05T07:31:47.230Z"),
                                  self.RECEIVED),
            "точное время WABA с малыми мс": (self._message(dateTime="2026-10-05T07:31:47.050Z"),
                                              self.RECEIVED),
            "живое входящее": (self._message(dateTime="2026-10-05T07:35:00.004Z"),
                               self.RECEIVED),
            "время не разобрать": (self._message(dateTime="2026-09-25 вечером"), self.RECEIVED),
        }
        for label, (message, received_at) in cases.items():
            with self.subTest(label):
                dt, wazzup_dt, cursor = self._store(message, received_at)
                self.assertEqual(dt, message["dateTime"])
                self.assertIsNone(wazzup_dt)
                lookups = [(sql, params) for sql, params in cursor.sql("SELECT")
                           if not sql.startswith("SELECT pg_advisory_xact_lock")]
                self.assertEqual(lookups, [], "выборки нужны только опоздавшим")

    def test_backlog_is_checked_once_per_webhook(self):
        cursor = _Cursor(answers={HISTORY_MARK: [], RECENT_MARK: []})
        messages = [self._message(messageId=f"m-{i}", dateTime=f"2026-09-25T13:45:00.00{i + 1}Z")
                    for i in range(3)]
        _db(cursor).store_wazzup_messages(messages, account="op", received_at=self.RECEIVED)
        self.assertEqual(len([e for e in cursor.executions if RECENT_MARK in e[0]]), 1)


class RetimeRepairTests(unittest.TestCase):
    CH = WA

    def _history(self):
        late = almaty(2026, 10, 5, 12, 35, 10, 161292)
        rows = [  # (message_id, channel_id, chat_id, is_echo, dt, created_at, late)
            ("a1", self.CH, "c-1", False, almaty(2026, 9, 25, 18, 45, 0, 2000), late, False),
            ("a2", self.CH, "c-1", False, almaty(2026, 9, 25, 18, 45, 0, 3000),
             late + timedelta(seconds=0.15), False),
            ("a3", self.CH, "c-1", False, almaty(2026, 10, 1, 9, 0, 0, 1000),
             late + timedelta(seconds=0.3), False),
            # исходящее — не трогаем, даже если эхо опоздало
            ("e1", self.CH, "c-1", True, almaty(2026, 10, 5, 12, 25, 0, 1000),
             late + timedelta(minutes=8), False),
            # точное время (WABA) — не трогаем
            ("w1", WABA, "c-2", False, almaty(2026, 9, 2, 17, 52, 47, 230000),
             almaty(2026, 9, 2, 18, 0, 45), False),
        ]
        return sorted(rows, key=lambda r: (r[1], r[2], r[5], r[4], r[0]))

    def _run(self, apply, history=None, update_rowcount=None, kept=((51357, None),)):
        kept_rows = [(eid, end or almaty(2026, 10, 5, 12, 49, 0, 1000)) for eid, end in kept]
        cursor = _Cursor(answers={
            "SELECT pg_advisory_xact_lock": [],
            REPAIR_MARK: history if history is not None else self._history(),
            RECENT_MARK: [],
            "WHERE to_regclass(name) IS NOT NULL": [(name,) for name in ALL_REFERENCE_TABLES],
            "DELETE FROM wazzup_episodes e": [("unanswered",), ("dialog",)],
            "SELECT e.id, e.ended_at FROM wazzup_episodes e": kept_rows,
        }, update_rowcount=update_rowcount)
        result = _db(cursor).retime_late_wazzup_messages(account="op", apply=apply)
        return cursor, result

    def test_moves_only_late_inbound_and_rebuilds_from_earliest(self):
        cursor, result = self._run(apply=True)
        self.assertEqual(cursor.executions[0], ("SELECT pg_advisory_xact_lock(%s)",
                                                (Database.WAZZUP_EPISODE_LOCK_KEY,)),
                         "ночная сборка эпизодов ждёт конца починки")
        (select_sql, _), = cursor.sql(REPAIR_MARK)
        self.assertIn("ORDER BY m.channel_id, m.chat_id, m.created_at, m.dt, m.message_id",
                      select_sql, "чат — в порядке прихода, один вебхук — по времени Wazzup")
        (update_sql, (ids, new_dts)), = cursor.sql("UPDATE wazzup_messages m")
        self.assertIn("SET wazzup_dt = m.dt, dt = v.new_dt", update_sql)
        self.assertIn("m.wazzup_dt IS NULL", update_sql, "повторный запуск строк не двигает")
        self.assertEqual(ids, ["a1", "a2", "a3"])
        self.assertEqual(new_dts, sorted(new_dts), "порядок прихода сохранён")
        self.assertEqual(result["messages"], 3)
        self.assertEqual(result["chats"], 1)
        # эпизоды чата — с самого раннего сдвинутого сообщения
        (delete_sql, delete_params), = cursor.sql("DELETE FROM wazzup_episodes e")
        self.assertIn("WHERE e.channel_id = %s AND e.chat_id = %s AND e.ended_at >= %s AND NOT (",
                      delete_sql)
        self.assertEqual(delete_params, (self.CH, "c-1", almaty(2026, 9, 25, 18, 45, 0, 2000)))
        self.assertEqual(result["episodes_deleted"], {"unanswered": 1, "dialog": 1})
        self.assertEqual(result["episodes_kept"], [51357])
        # сдвинутое раньше конца оставленного оценённого эпизода сборка уже не подберёт
        self.assertEqual(result["outside_episodes"], 3)
        self.assertEqual(result["outside_chats"], ["c-1"])
        # сводка чата пересчитана
        self.assertEqual(len(cursor.sql("INSERT INTO wazzup_chats")), 1)
        self.assertEqual(cursor.connection.rollbacks, 0)

    def test_nothing_outside_when_no_evaluated_episode_stays(self):
        _, result = self._run(apply=True, kept=())
        self.assertEqual((result["outside_episodes"], result["outside_chats"]), (0, []))

    def test_already_retimed_rows_stay_and_count_as_pending(self):
        q1 = (self.CH, "c-1", False, almaty(2026, 10, 5, 12, 22, 0, 88),
              almaty(2026, 10, 5, 12, 22, 8), True)  # уже сдвинута приёмником
        q2 = (self.CH, "c-1", False, almaty(2026, 9, 25, 18, 45, 0, 2000),
              almaty(2026, 10, 5, 12, 35, 10, 161292), False)
        cursor, _ = self._run(apply=True, history=[("q1",) + q1, ("q2",) + q2])
        (_, (ids, new_dts)), = cursor.sql("UPDATE wazzup_messages m")
        self.assertEqual(ids, ["q2"])
        self.assertEqual(new_dts, [q2[4]], "q1 без ответа — q2 встаёт в точный момент прихода")

    def test_dry_run_rolls_back(self):
        cursor, result = self._run(apply=False)
        self.assertEqual(cursor.connection.rollbacks, 1)
        self.assertFalse(result["applied"])
        self.assertEqual(result["messages"], 3)

    def test_nothing_to_move_touches_nothing(self):
        cursor, result = self._run(apply=True, history=[])
        self.assertEqual(result["messages"], 0)
        self.assertEqual(cursor.sql("UPDATE"), [])
        self.assertEqual(cursor.sql("DELETE"), [])

    def test_partial_update_aborts(self):
        with self.assertRaises(RuntimeError):
            self._run(apply=True, update_rowcount=2)

    def test_pull_account_is_refused(self):
        with self.assertRaises(ValueError):
            _db(_Cursor()).retime_late_wazzup_messages(account="potok", apply=False)


class KeptEpisodeSqlTests(unittest.TestCase):
    """Условие «эпизод нельзя удалять» — на данных (sqlite), а не по тексту."""

    def _kept_sql(self, present=ALL_REFERENCE_TABLES):
        cursor = _Cursor(answers={"WHERE to_regclass(name) IS NOT NULL": [(t,) for t in present]})
        return Database._wazzup_episode_kept_sql(cursor)

    def _episodes(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE wazzup_episodes (id INTEGER PRIMARY KEY, channel_id TEXT,
                        chat_id TEXT, started_at TEXT, ended_at TEXT, journal_evaluated_at TEXT)""")
        conn.execute("""CREATE TABLE c2d_chat_snapshots (source TEXT, wz_channel_id TEXT,
                        wz_chat_id TEXT, episode_start TEXT)""")
        for table, column in Database.WAZZUP_EPISODE_REFERENCES:
            conn.execute(f"CREATE TABLE {table} (subject_kind TEXT, {column} INTEGER)")
        start = "2026-10-05 07:35:00"
        episodes = {"free": 1, "journal": 2, "snapshot": 3, "snapshot_other_start": 4,
                    "other_kind_same_id": 5}
        for table_index, (table, _) in enumerate(Database.WAZZUP_EPISODE_REFERENCES):
            episodes[table] = 100 + table_index
        for eid in episodes.values():
            conn.execute("INSERT INTO wazzup_episodes VALUES (?, 'ch', ?, ?, ?, NULL)",
                         (eid, f"chat-{eid}", start, "2026-10-05 07:49:00"))
        conn.execute("UPDATE wazzup_episodes SET journal_evaluated_at = 'x' WHERE id = 2")
        conn.execute("INSERT INTO c2d_chat_snapshots VALUES ('wazzup', 'ch', 'chat-3', ?)", (start,))
        conn.execute("INSERT INTO c2d_chat_snapshots VALUES ('wazzup', 'ch', 'chat-4', "
                     "'2026-10-05 07:20:00')")
        conn.execute("INSERT INTO ai_evaluation_runs VALUES ('call', 5)")
        for table, column in Database.WAZZUP_EPISODE_REFERENCES:
            conn.execute(f"INSERT INTO {table} VALUES ('wz_episode', ?)", (episodes[table],))
        return conn, episodes

    def test_only_free_episodes_are_deletable(self):
        conn, episodes = self._episodes()
        deletable = {row[0] for row in conn.execute(
            f"SELECT e.id FROM wazzup_episodes e WHERE NOT {self._kept_sql()}")}
        self.assertEqual(deletable, {episodes["free"], episodes["snapshot_other_start"],
                                     episodes["other_kind_same_id"]})

    def test_missing_table_is_left_out_not_failing(self):
        present = tuple(t for t in ALL_REFERENCE_TABLES if t != "qa_gold_labels")
        sql = self._kept_sql(present)
        self.assertNotIn("qa_gold_labels", sql)
        conn, episodes = self._episodes()
        conn.execute("DROP TABLE qa_gold_labels")
        deletable = {row[0] for row in conn.execute(
            f"SELECT e.id FROM wazzup_episodes e WHERE NOT {sql}")}
        self.assertIn(episodes["qa_gold_labels"], deletable)
        self.assertNotIn(episodes["ai_evaluation_runs"], deletable)


class EpisodeReferenceListTests(unittest.TestCase):
    """Новая таблица оценки со ссылкой на субъект без строки в перечне — красный тест."""

    @staticmethod
    def _subject_tables():
        tables = {}
        blocks = {m.group(1): m.group(2) for source in (QA_SCHEMA, MARKETING_SCHEMA)
                  for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\s*\);?",
                                       source, re.S)}
        altered = set(re.findall(r"ALTER TABLE (\w+)\s+ADD COLUMN IF NOT EXISTS subject_kind",
                                 QA_SCHEMA))
        for name, body in blocks.items():
            if "subject_kind" not in body and name not in altered:
                continue
            column = next((c for c in ("call_id", "subject_id") if re.search(rf"\b{c}\b", body)),
                          None)
            if column:
                tables[name] = column
        return tables

    def test_every_subject_table_protects_episodes(self):
        found = self._subject_tables()
        self.assertGreaterEqual(len(found), 11)
        self.assertEqual(found, dict(Database.WAZZUP_EPISODE_REFERENCES))


class WiringTests(unittest.TestCase):
    def test_schema_and_webhook(self):
        self.assertIn("ALTER TABLE wazzup_messages\n                    ADD COLUMN IF NOT EXISTS wazzup_dt TIMESTAMPTZ;",
                      DB_SOURCE.replace("\r\n", "\n"))
        self.assertIn("db.store_wazzup_messages(payload.get('messages'), account=account,\n"
                      "                                          received_at=datetime.now(timezone.utc))",
                      API_SOURCE.replace("\r\n", "\n"))
        # забор «Потока» момент прихода не передаёт: у окна чатов время точное
        potok = (ROOT / "wazzup" / "potok_sync.py").read_text(encoding="utf-8")
        self.assertIn("db.store_wazzup_messages(batch, account=account)", potok)

    def test_thread_api_returns_wazzup_time(self):
        self.assertTrue("author_id, status, is_edited, is_deleted, wazzup_dt" in API_SOURCE)
        self.assertIn("'wazzupDt': r[11].isoformat() if r[11] else None", API_SOURCE)

    def test_thread_view_uses_local_day_and_late_note(self):
        self.assertIn("const day = localDayKey(m.dt);", VIEW_SOURCE)
        self.assertNotIn("(m.dt || '').slice(0, 10)", VIEW_SOURCE, "день по UTC — сдвиг на 5 часов")
        self.assertIn("const lateNote = lateDeliveryNote(msg);", VIEW_SOURCE)
        self.assertRegex(VIEW_SOURCE, r"\{lateNote \? \(\s*<span[^>]*title=\{lateNote\}")


if __name__ == "__main__":
    unittest.main()
