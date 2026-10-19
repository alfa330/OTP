"""Ответ из окна iCORE и входящее канала с минутным временем.

Жалоба 19.10.2026 («Чаты ОП», канал «2ГИС»): оператор отправил вопрос в 11:23:29,
клиент ответил «жок» — сообщение пришло в 11:23:54, а в ленте встало ВЫШЕ вопроса.
Входящие этого канала приходят с минутой (11:23:00.002), а ответ из окна iCORE —
с точным временем отправки. В окне самого Wazzup у каждого сообщения есть точное
время (timestampMsg), и по нему вопрос идёт раньше ответа.

Тесты закрепляют то, что ломается молча:
* входящее, пришедшее вовремя, не встаёт раньше сообщения с точным временем,
  которое к его приходу уже было в чате; минутные между собой — по номеру;
* оба чата из жалобы читаются в порядке точного времени окна Wazzup — и через
  приёмник (вебхук за вебхуком), и через разовую починку истории;
* починка: охранное условие записи, холостой прогон только читает, оценённый
  эпизод не удаляется и не теряет сообщение, «ждут ответа» возвращается только
  там, где счётчик промолчал из-за этой ошибки.
"""

import unittest
from datetime import timedelta

from tests.test_wazzup_late_delivery import (
    ALL_REFERENCE_TABLES, LATER_MARK, WA, WABA, Database, _Cursor, _db, _webhook_order, almaty)
from wazzup import delivery
from wazzup.pilot import EXCLUDED_CHANNELS

STEP = timedelta(microseconds=1)
REPAIR = "SELECT m.message_id, m.channel_id, m.chat_id, m.is_echo, m.dt, m.created_at, m.wazzup_dt IS NOT NULL, m.is_deleted"
EPISODES = "FROM wazzup_episodes e WHERE e.channel_id = %s AND e.chat_id = %s ORDER BY e.started_at"
STATE = "SELECT answered_through, unread_count FROM wazzup_chat_read_state"

# Чат 1 из жалобы (номер и тексты заменены). Время — Алматы: (id, исходящее,
# время строки, приход). У входящего время строки — dateTime вебхука (минута и
# номер), у ответа из окна iCORE — момент отправки: send() пишет строку архива
# сразу после ответа Wazzup, а эхо Wazzup время уже не двигает.
CHAT_1 = [
    ("from-2gis", False, (2026, 10, 19, 11, 21, 0, 1000), (2026, 10, 19, 11, 21, 33, 712000)),
    ("salem", False, (2026, 10, 19, 11, 21, 0, 2000), (2026, 10, 19, 11, 21, 33, 908000)),
    ("greet", True, (2026, 10, 19, 11, 21, 40, 385000), (2026, 10, 19, 11, 21, 40, 386000)),
    ("wolt?", False, (2026, 10, 19, 11, 21, 0, 3000), (2026, 10, 19, 11, 21, 51, 84000)),
    ("courier", False, (2026, 10, 19, 11, 22, 0, 1000), (2026, 10, 19, 11, 22, 40, 317000)),
    ("register?", True, (2026, 10, 19, 11, 23, 29, 537000), (2026, 10, 19, 11, 23, 29, 538000)),
    ("no", False, (2026, 10, 19, 11, 23, 0, 2000), (2026, 10, 19, 11, 23, 54, 940000)),
    ("wolt", False, (2026, 10, 19, 11, 23, 0, 3000), (2026, 10, 19, 11, 23, 55, 95000)),
    ("only", False, (2026, 10, 19, 11, 23, 0, 4000), (2026, 10, 19, 11, 23, 55, 167000)),
    ("sorry", True, (2026, 10, 19, 11, 24, 23, 15000), (2026, 10, 19, 11, 24, 23, 16000)),
]
# Порядок окна Wazzup по точному времени (timestampMsg), снят 19.10.2026.
CHAT_1_WAZZUP = ["from-2gis", "salem", "greet", "wolt?", "courier", "register?",
                 "no", "wolt", "only", "sorry"]
# Так лента стояла до правки: ответы клиента — над вопросом, на который отвечали.
CHAT_1_BEFORE = ["from-2gis", "salem", "wolt?", "greet", "courier", "no", "wolt", "only",
                 "register?", "sorry"]

CHAT_2 = [
    ("hi", False, (2026, 10, 19, 12, 31, 0, 1000), (2026, 10, 19, 12, 31, 38, 764000)),
    ("how-help", True, (2026, 10, 19, 12, 31, 52, 413000), (2026, 10, 19, 12, 31, 52, 414000)),
    ("volt?", False, (2026, 10, 19, 12, 32, 0, 1000), (2026, 10, 19, 12, 32, 30, 463000)),
    ("drivers?", False, (2026, 10, 19, 12, 32, 0, 2000), (2026, 10, 19, 12, 32, 30, 737000)),
    ("couriers?", False, (2026, 10, 19, 12, 32, 0, 3000), (2026, 10, 19, 12, 32, 36, 888000)),
    ("center", True, (2026, 10, 19, 12, 33, 1, 954000), (2026, 10, 19, 12, 33, 1, 955000)),
    ("data", True, (2026, 10, 19, 12, 33, 13, 894000), (2026, 10, 19, 12, 33, 13, 895000)),
    ("eda?", False, (2026, 10, 19, 12, 33, 0, 3000), (2026, 10, 19, 12, 33, 45, 653000)),
    ("taxi", True, (2026, 10, 19, 12, 34, 6, 11000), (2026, 10, 19, 12, 34, 6, 12000)),
]
CHAT_2_WAZZUP = ["hi", "how-help", "volt?", "drivers?", "couriers?", "center", "data",
                 "eda?", "taxi"]


def _history(rows, chat="77000000000", channel=WA):
    """Строки чата так, как они лежат в базе до правки (время Wazzup), в порядке прихода."""
    stored = [{"message_id": mid, "channel_id": channel, "chat_id": chat, "is_echo": echo,
               "dt": almaty(*sent), "created_at": almaty(*received), "late": False,
               "is_deleted": False, "chat_type": "whatsapp"} for mid, echo, sent, received in rows]
    return sorted(stored, key=lambda r: (r["created_at"], r["dt"], r["message_id"]))


def _repair_rows(history):
    return [(m["message_id"], m["channel_id"], m["chat_id"], m["is_echo"], m["dt"],
             m["created_at"], m["late"], m["is_deleted"], m["chat_type"]) for m in history]


class _RepairCursor(_Cursor):
    """Заглушка с fetchone и числом вставленных в «ждут ответа»."""

    def execute(self, query, params=None):
        super().execute(query, params)
        if self.executions[-1][0].startswith("INSERT INTO wazzup_unanswered_messages"):
            self.rowcount = len(params[3])

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _repair(histories, apply=True, episodes=None, states=None, update_rowcount=None,
            waiting_since=None, kept_tables=()):
    """Настоящая починка над курсором-заглушкой. episodes — {chat_id: строки
    (id, kind, started_at, ended_at, kept)}, states — {chat_id: (answered, unread)}."""
    rows = [row for history in histories for row in _repair_rows(history)]
    episodes = episodes or {}
    states = states or {}
    cursor = _RepairCursor(answers={
        "SELECT pg_advisory_xact_lock": [],
        REPAIR: rows,
        "WHERE to_regclass(name) IS NOT NULL": [(t,) for t in kept_tables],
        EPISODES: lambda params: episodes.get(params[1], []),
        STATE: lambda params: [states[params[2]]] if params[2] in states else [],
        "DELETE FROM wazzup_episodes e": lambda params: [
            (kind,) for chat_rows in episodes.values()
            for eid, kind, *_ in chat_rows if eid in params[0]],
    }, update_rowcount=update_rowcount)
    result = _db(cursor).retime_wazzup_inbound_after_replies(
        account="op", apply=apply, waiting_since=waiting_since)
    return cursor, result


def _order_after_repair(history, cursor):
    moved = {}
    for _, (ids, _old, new) in [(sql, params[:3]) for sql, params in cursor.sql("UPDATE wazzup_messages m")]:
        moved.update(zip(ids, new))
    final = {m["message_id"]: moved.get(m["message_id"], m["dt"]) for m in history}
    return [mid for mid, _ in sorted(final.items(), key=lambda item: (item[1], item[0]))]


class RuleTests(unittest.TestCase):
    MINUTE = almaty(2026, 10, 19, 11, 23)

    def at(self, seconds, ms=0):
        return self.MINUTE + timedelta(seconds=seconds, milliseconds=ms)

    def test_in_time_is_the_complement_of_late(self):
        stamp = self.MINUTE.replace(microsecond=2000)
        for lag in (timedelta(0), timedelta(seconds=54), delivery.LATE_DELIVERY):
            self.assertTrue(delivery.is_in_time_inbound(stamp, stamp + lag, False), lag)
            self.assertFalse(delivery.is_late_inbound(stamp, stamp + lag, False), lag)
        later = stamp + delivery.LATE_DELIVERY + STEP
        self.assertFalse(delivery.is_in_time_inbound(stamp, later, False))
        self.assertTrue(delivery.is_late_inbound(stamp, later, False))
        self.assertFalse(delivery.is_in_time_inbound(stamp, stamp, True), "исходящее не двигаем")
        self.assertFalse(delivery.is_in_time_inbound(self.at(47, 230), self.at(48), False),
                         "точное время WABA")
        self.assertFalse(delivery.is_in_time_inbound(stamp, None, False), "без момента прихода")
        self.assertFalse(delivery.is_in_time_inbound(None, self.at(54), False))

    def test_goes_right_after_the_latest_exact_message_already_there(self):
        stamp = self.MINUTE.replace(microsecond=2000)
        reply = self.at(29, 537)
        self.assertEqual(delivery.after_known_replies(stamp, [reply]), reply + STEP)
        chained = reply + STEP
        self.assertEqual(delivery.after_known_replies(stamp, [reply, chained]), chained + STEP,
                         "за уже сдвинутым тем же правилом — по порядку прихода")
        next_minute = self.at(65)
        self.assertEqual(delivery.after_known_replies(stamp, [reply, next_minute]),
                         next_minute + STEP, "точное время следующей минуты тоже уже было в чате")

    def test_minute_stamps_and_earlier_messages_do_not_move_it(self):
        stamp = self.MINUTE.replace(microsecond=2000)
        self.assertEqual(delivery.after_known_replies(stamp, []), stamp)
        self.assertEqual(delivery.after_known_replies(
            stamp, [self.MINUTE.replace(microsecond=1000), self.MINUTE.replace(microsecond=3000),
                    self.MINUTE + timedelta(minutes=1, milliseconds=1)]), stamp,
            "минутные — номер в минуте уже рассудил, как у телефона")
        self.assertEqual(delivery.after_known_replies(stamp, [self.MINUTE - timedelta(seconds=3)]),
                         stamp, "ответ раньше его минуты")
        self.assertEqual(delivery.after_known_replies(stamp, [stamp]), stamp, "оно само")

    def test_replay_keeps_what_it_is_told_and_still_counts_it(self):
        history = _history(CHAT_1)
        moves = delivery.replay_after_replies(history)
        self.assertEqual([mid for mid, _, _ in moves], ["wolt?", "no", "wolt", "only"])
        for _, old, new in moves:
            self.assertGreater(new, old)
            self.assertLess(new - old, timedelta(minutes=1), "в пределах минуты ответа")
        kept = delivery.replay_after_replies(history, keep=frozenset({"no"}))
        self.assertEqual([mid for mid, _, _ in kept], ["wolt?", "wolt", "only"])
        by_id = {mid: new for mid, _, new in kept}
        register = almaty(2026, 10, 19, 11, 23, 29, 537000)
        self.assertEqual(by_id["wolt"], register + STEP, "«no» на месте — следующее за вопросом")
        self.assertEqual(by_id["only"], register + 2 * STEP)


class WebhookOrderTests(unittest.TestCase):
    """Приёмник: вебхук за вебхуком, ответы iCORE — строкой архива из send()."""

    def test_complaint_chats_read_like_wazzup_window(self):
        self.assertEqual(_webhook_order(CHAT_1)[0], CHAT_1_WAZZUP)
        self.assertEqual(_webhook_order(CHAT_2)[0], CHAT_2_WAZZUP)

    def test_moved_message_keeps_its_minute_and_unique_time(self):
        order, times = _webhook_order(CHAT_1)
        register = almaty(2026, 10, 19, 11, 23, 29, 537000)
        self.assertEqual([times[m] for m in ("no", "wolt", "only")],
                         [register + STEP, register + 2 * STEP, register + 3 * STEP])
        self.assertEqual(times["courier"], almaty(2026, 10, 19, 11, 22, 0, 1000),
                         "после ответа в той же минуте ничего не было — время Wazzup")
        self.assertEqual(len(set(times.values())), len(times))

    def test_one_webhook_with_two_answers_keeps_their_order(self):
        rows = [r for r in CHAT_1 if r[0] not in ("wolt", "only")]
        order, times = _webhook_order(CHAT_1, batches=[("no", "wolt", "only")])
        self.assertEqual(order, CHAT_1_WAZZUP)
        self.assertLess(times["no"], times["wolt"])
        self.assertLess(times["wolt"], times["only"])
        self.assertEqual(_webhook_order(rows)[0], [m for m in CHAT_1_WAZZUP if m in {r[0] for r in rows}])

    def test_without_a_reply_from_icore_nothing_changes(self):
        phone_replies = [(mid, echo, sent if not echo else (sent[:5] + (0, 1000 * (i % 3 + 1))), received)
                         for i, (mid, echo, sent, received) in enumerate(CHAT_2)]
        order, times = _webhook_order(phone_replies)
        for mid, echo, sent, _ in phone_replies:
            self.assertEqual(times[mid], almaty(*sent), f"{mid}: ответы с телефона — минутные, время Wazzup")


class StoreLookupTests(unittest.TestCase):
    RECEIVED = almaty(2026, 10, 19, 11, 23, 54, 940000)

    def _store(self, messages, later=()):
        cursor = _Cursor(answers={LATER_MARK: [(dt,) for dt in later]})
        _db(cursor).store_wazzup_messages(messages, account="op", received_at=self.RECEIVED)
        return cursor

    @staticmethod
    def _message(mid="m-1", dt="2026-10-19T06:23:00.002Z", **overrides):
        message = {"messageId": mid, "channelId": WA, "chatId": "77000000000",
                   "chatType": "whatsapp", "dateTime": dt, "isEcho": False, "type": "text",
                   "text": "жок"}
        message.update(overrides)
        return message

    def test_on_time_inbound_asks_only_its_chat_after_its_minute_stamp(self):
        cursor = self._store([self._message()])
        (sql, params), = [e for e in cursor.executions if LATER_MARK in e[0]]
        self.assertEqual(params, (WA, "77000000000", delivery.parse_wazzup_dt("2026-10-19T06:23:00.002Z")))
        (_, insert), = cursor.sql("INSERT INTO wazzup_messages")
        self.assertEqual(insert[4], "2026-10-19T06:23:00.002Z", "ничего точного позже — время Wazzup")
        self.assertIsNone(insert[-1])

    def test_after_reply_goes_right_behind_it_without_wazzup_time(self):
        reply = almaty(2026, 10, 19, 11, 23, 29, 537000)
        cursor = self._store([self._message()], later=[reply])
        (_, insert), = cursor.sql("INSERT INTO wazzup_messages")
        self.assertEqual(insert[4], reply + STEP)
        self.assertIsNone(insert[-1], "время Wazzup в той же минуте — «ждут ответа» сравнивает время ленты")

    def test_echo_and_exact_time_do_not_look_up(self):
        for message in (self._message(isEcho=True, authorId="1", authorName="оператор"),
                        self._message(dt="2026-10-19T06:23:47.230Z"),
                        self._message(channelId=WABA, dt="2026-10-19T06:23:29.537Z")):
            with self.subTest(message["dateTime"]):
                cursor = self._store([message], later=[almaty(2026, 10, 19, 11, 23, 50)])
                self.assertEqual([e for e in cursor.executions if LATER_MARK in e[0]], [])


class RepairTests(unittest.TestCase):
    def test_complaint_chats_read_like_wazzup_window_after_repair(self):
        first, second = _history(CHAT_1, chat="c-1"), _history(CHAT_2, chat="c-2")
        cursor, result = _repair([first, second])
        self.assertEqual(_order_after_repair(first, cursor), CHAT_1_WAZZUP)
        self.assertEqual(_order_after_repair(second, cursor), CHAT_2_WAZZUP)
        self.assertEqual(_order_after_repair(first, _Cursor()), CHAT_1_BEFORE, "так было до починки")
        self.assertEqual((result["messages"], result["chats"]), (5, 2))
        # и приёмник ставит те же сообщения на то же время
        _, times = _webhook_order(CHAT_1)
        (_, (ids, olds, news, account)), = cursor.sql("UPDATE wazzup_messages m")
        self.assertEqual(account, "op")
        for mid, new in zip(ids, news):
            if mid in times:
                self.assertEqual(new, times[mid], mid)

    def test_update_is_guarded_and_partial_update_aborts(self):
        cursor, _ = _repair([_history(CHAT_1)])
        (sql, _), = cursor.sql("UPDATE wazzup_messages m")
        self.assertIn("SET dt = v.new_dt", sql)
        self.assertIn("m.dt = v.old_dt AND m.wazzup_dt IS NULL AND NOT m.is_echo", sql,
                      "строку, которую успели поменять, починка не трогает")
        self.assertNotIn("wazzup_dt =", sql.split("WHERE")[0], "время Wazzup не пишется")
        with self.assertRaises(RuntimeError):
            _repair([_history(CHAT_1)], update_rowcount=3)
        self.assertEqual(len(cursor.sql("INSERT INTO wazzup_chats")), 1, "сводка чата пересчитана")

    def test_dry_run_only_reads(self):
        episodes = {"77000000000": [(1, "dialog", almaty(2026, 10, 19, 11, 21), almaty(2026, 10, 19, 11, 24, 24), False)]}
        states = {"77000000000": (almaty(2026, 10, 19, 11, 21, 40, 385000), 0)}
        dry_cursor, dry = _repair([_history(CHAT_1)], apply=False, episodes=episodes, states=states,
                                  waiting_since=almaty(2026, 10, 19))
        writes = [sql for sql, _ in dry_cursor.executions
                  if sql.split()[0] in ("UPDATE", "DELETE", "INSERT") or "FOR UPDATE" in sql]
        self.assertEqual(writes, [], "холостой прогон годится для соединения только на чтение")
        _, real = _repair([_history(CHAT_1)], episodes=episodes, states=states,
                          waiting_since=almaty(2026, 10, 19))
        self.assertFalse(dry["applied"])
        for key in ("messages", "chats", "episodes_deleted", "waiting_chats", "waiting_messages"):
            self.assertEqual(dry[key], real[key], key)

    def test_second_run_finds_nothing(self):
        history = _history(CHAT_1)
        cursor, _ = _repair([history])
        (_, (ids, _, news, _)), = cursor.sql("UPDATE wazzup_messages m")
        moved = dict(zip(ids, news))
        for msg in history:
            msg["dt"] = moved.get(msg["message_id"], msg["dt"])
        again, result = _repair([history])
        self.assertEqual(result["messages"], 0)
        self.assertEqual(again.sql("UPDATE"), [])

    def test_pull_account_is_refused(self):
        with self.assertRaises(ValueError):
            _db(_Cursor()).retime_wazzup_inbound_after_replies(account="potok")


class RepairEpisodeTests(unittest.TestCase):
    START, END = almaty(2026, 10, 19, 11, 21), almaty(2026, 10, 19, 11, 24, 23, 15000)

    def test_free_episodes_from_the_earliest_move_are_rebuilt(self):
        earlier = (10, "dialog", almaty(2026, 10, 18, 9), almaty(2026, 10, 18, 9, 30), False)
        touched = (11, "dialog", self.START, self.END, False)
        later = (12, "unanswered", almaty(2026, 10, 19, 20), almaty(2026, 10, 19, 20, 5), False)
        cursor, result = _repair([_history(CHAT_1)], episodes={"77000000000": [earlier, touched, later]})
        (sql, (ids,)), = cursor.sql("DELETE FROM wazzup_episodes e")
        self.assertEqual(ids, [11, 12], "раньше сдвинутого — не трогаем, позже — пересобирается тоже")
        self.assertIn("e.id = ANY(%s) AND NOT", sql, "оценённый не удаляется даже по id")
        self.assertEqual(result["episodes_deleted"], {"dialog": 1, "unanswered": 1})
        self.assertEqual(result["stale_episodes"], [])

    def test_evaluated_episode_stays_and_keeps_its_last_message(self):
        # оценённый эпизод кончается вопросом оператора: ответ клиента ушёл бы за его
        # конец, и сборка сделала бы из него эпизод в одно сообщение
        rows = [r for r in CHAT_1 if r[0] != "sorry"]
        register = almaty(2026, 10, 19, 11, 23, 29, 537000)
        kept = (20, "dialog", self.START, register, True)
        cursor, result = _repair([_history(rows)], episodes={"77000000000": [kept]}, kept_tables=ALL_REFERENCE_TABLES)
        (_, (ids, _, news, _)), = cursor.sql("UPDATE wazzup_messages m")
        self.assertEqual(ids, ["wolt?"], "«no/wolt/only» остались на месте")
        self.assertEqual(result["kept_in_place"], 3)
        self.assertEqual(result["episodes_kept"], [20])
        self.assertEqual(cursor.sql("DELETE"), [])

    def test_free_episode_before_an_evaluated_one_is_reported_not_lost(self):
        touched = (30, "dialog", self.START, self.END, False)
        evaluated = (31, "dialog", almaty(2026, 10, 19, 20), almaty(2026, 10, 19, 20, 5), True)
        cursor, result = _repair([_history(CHAT_1)], episodes={"77000000000": [touched, evaluated]})
        self.assertEqual(cursor.sql("DELETE"), [], "сборка после оценённого его бы не собрала")
        self.assertEqual(result["stale_episodes"], [30])


class RepairWaitingTests(unittest.TestCase):
    """До правки «ждут ответа» сравнивал с ответом минуту клиента и молчал."""
    SINCE = almaty(2026, 10, 19)
    REGISTER = almaty(2026, 10, 19, 11, 23, 29, 537000)

    def _rows(self):
        return [r for r in CHAT_1 if r[0] != "sorry"]  # клиенту так и не ответили

    def _run(self, state, waiting_since=SINCE, rows=None, channel=WA):
        history = _history(rows or self._rows(), channel=channel)
        return _repair([history], states={"77000000000": state}, waiting_since=waiting_since)

    def test_unanswered_tail_returns_to_waiting(self):
        cursor, result = self._run((self.REGISTER, 0))
        (_, params), = cursor.sql("INSERT INTO wazzup_unanswered_messages")
        account, channel, chat, ids, sent = params
        self.assertEqual((account, channel, chat), ("op", WA, "77000000000"))
        self.assertEqual(ids, ["no", "wolt", "only"])
        self.assertEqual(sent, [self.REGISTER + STEP, self.REGISTER + 2 * STEP, self.REGISTER + 3 * STEP])
        (_, update), = cursor.sql("UPDATE wazzup_chat_read_state")
        self.assertEqual(update[:2], (3, "only"), "сколько вставлено и последнее входящее")
        (state_sql, _), = [e for e in cursor.executions if STATE in e[0]]
        self.assertIn("FOR UPDATE", state_sql)
        self.assertEqual((result["waiting_chats"], result["waiting_messages"]), (["77000000000"], 3))

    def test_counter_left_alone_where_it_was_not_silent(self):
        cases = {
            "счётчик уже ведёт чат": dict(state=(self.REGISTER, 2)),
            "оператор ответил позже": dict(state=(self.REGISTER + timedelta(minutes=1), 0)),
            "ответов не было — сняли «Ответ не нужен»": dict(state=(None, 0)),
            "сдвинуто раньше выбранного момента": dict(state=(self.REGISTER, 0),
                                                       waiting_since=almaty(2026, 10, 20)),
            "не просили": dict(state=(self.REGISTER, 0), waiting_since=None),
            "последнее входящее — не сдвинутое": dict(
                state=(self.REGISTER, 0),
                rows=self._rows() + [("later", False, (2026, 10, 19, 11, 30, 0, 1000),
                                      (2026, 10, 19, 11, 30, 5))]),
            "Global счётчик не ведёт": dict(state=(self.REGISTER, 0), channel=sorted(EXCLUDED_CHANNELS)[0]),
        }
        for label, case in cases.items():
            with self.subTest(label):
                cursor, result = self._run(**case)
                self.assertEqual(cursor.sql("INSERT INTO wazzup_unanswered_messages"), [])
                self.assertEqual(cursor.sql("UPDATE wazzup_chat_read_state"), [])
                self.assertEqual(result["waiting_chats"], [])


if __name__ == "__main__":
    unittest.main()
