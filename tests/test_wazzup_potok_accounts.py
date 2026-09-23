"""«Чаты ОП» на двух аккаунтах Wazzup: реестр, ключ автора, забор «Потока».

Второй аккаунт («Поток») вебхуком к нам не приходит, а история из v3 не
читается — переписку тянет wazzup/potok_sync.py из внутреннего API окна чатов.
Тесты закрепляют то, что ломается молча: перевод полей окна в форму вебхука,
границу окна при листании, ключ автора по имени и то, что оба аккаунта живут
в одних таблицах, но не смешиваются.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from wazzup import accounts, names, potok_client, potok_sync

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
DB_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
VIEW_SOURCE = (ROOT / "src" / "components" / "wazzup" / "WazzupChatsView.jsx").read_text(encoding="utf-8-sig")
APP_SOURCE = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
LINK_SOURCE = (ROOT / "src" / "components" / "wazzup" / "chatLink.js").read_text(encoding="utf-8-sig")

MS = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731


class AccountsRegistryTests(unittest.TestCase):
    def test_default_and_unknown(self):
        self.assertEqual(accounts.normalize_account(None), "op")
        self.assertEqual(accounts.normalize_account(""), "op")
        self.assertEqual(accounts.normalize_account(" Potok "), "potok")
        self.assertIsNone(accounts.normalize_account("tez"),
                          "незнакомый аккаунт → None, маршрут отвечает 400")

    def test_order_and_public_shape(self):
        public = accounts.public_accounts()
        self.assertEqual([a["key"] for a in public], ["op", "potok"])
        self.assertEqual([a["label"] for a in public], ["Верификаторы", "Поток"])
        self.assertEqual(public[1]["workspace"], "2682-1109")
        for a in public:
            self.assertNotIn("api_key_env", a, "имена переменных окружения на фронт не уходят")

    def test_webhook_token_routes_to_account(self):
        env = {"WAZZUP_WEBHOOK_TOKEN": "tok-op", "WAZZUP_POTOK_WEBHOOK_TOKEN": "tok-potok"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(accounts.account_by_webhook_token("tok-op"), "op")
            self.assertEqual(accounts.account_by_webhook_token("tok-potok"), "potok")
            self.assertIsNone(accounts.account_by_webhook_token("tok-other"))
        # пустой токен в окружении никого не пускает — даже пустой строкой
        with mock.patch.dict(os.environ, {"WAZZUP_WEBHOOK_TOKEN": "", "WAZZUP_POTOK_WEBHOOK_TOKEN": ""}):
            self.assertIsNone(accounts.account_by_webhook_token(""))

    def test_viewer_user_default_and_override(self):
        with mock.patch.dict(os.environ, {"WAZZUP_OP_POTOK_VIEWER_USER_ID": ""}):
            self.assertEqual(accounts.viewer_user_id("potok"), "166")
        with mock.patch.dict(os.environ, {"WAZZUP_OP_POTOK_VIEWER_USER_ID": "398"}):
            self.assertEqual(accounts.viewer_user_id("potok"), "398")


class AuthorKeyTests(unittest.TestCase):
    def test_op_keeps_webhook_author_id(self):
        self.assertEqual(names.author_key("op", 13950986, "Сариева Айдана"), "13950986")
        self.assertIsNone(names.author_key("op", None, "Сариева Айдана"))

    def test_potok_key_is_normalized_name(self):
        # в окне подписано «API • Имя», в поле authorName — без префикса; оба дают один ключ
        self.assertEqual(names.author_key("potok", None, "Жолмаганбет Ардак"),
                         "potok:жолмаганбет ардак")
        self.assertEqual(names.author_key("potok", None, "API • Жолмаганбет Ардак"),
                         "potok:жолмаганбет ардак")
        self.assertEqual(names.author_key("potok", None, "Жолмағанбет Ардақ"),
                         "potok:жолмаганбет ардак", "казахские буквы → тот же человек")
        self.assertIsNone(names.author_key("potok", None, ""))


class MessageConversionTests(unittest.TestCase):
    """Снято с живого ответа окна 23.09.2026."""

    CHAT = {"chatId": "77002199865", "chatType": "whatsapp", "contactName": "77002199865",
            "userName": None, "userPhone": None,
            "chats": [{"channelId": "35ac2ea6-9592-45bd-9d9b-1435007d9f60"}]}

    def test_incoming_text(self):
        m = {"id": "b2aaf59e", "channelId": "35ac2ea6", "chatId": "77073734426", "chatType": "whatsapp",
             "datetime": 1790144503231, "incoming": True, "status": 99, "type": 1,
             "text": "Салеметсиздерме", "authorName": "77073734426",
             "messageEditingByUser": False, "messageDeleteByUser": False}
        out = potok_sync.to_webhook_message(m, self.CHAT)
        self.assertEqual(out["messageId"], "b2aaf59e")
        self.assertFalse(out["isEcho"])
        self.assertEqual(out["type"], "text")
        self.assertEqual(out["status"], "inbound")
        self.assertIsNone(out["authorName"], "у входящих authorName — телефон клиента, не автор")
        self.assertEqual(out["dateTime"], "2026-09-23T06:21:43.231000+00:00")
        self.assertEqual(out["contact"], {"name": "77002199865", "phone": "77002199865"})

    def test_contact_ignores_responsible_user_fields(self):
        # userName/userPhone в строке списка — ответственный менеджер, не клиент
        chat = dict(self.CHAT, userName="Аман Алан", userPhone="77072480500")
        self.assertEqual(potok_sync.chat_contact(chat), {"name": "77002199865", "phone": "77002199865"})

    def test_outgoing_by_manager(self):
        m = {"id": "x1", "channelId": "35ac2ea6", "chatId": "77002199865", "chatType": "whatsapp",
             "datetime": 1790142011046, "incoming": False, "status": 3, "type": 1,
             "text": "Пожалуйста, опишите Ваш вопрос", "authorName": "Жолмаганбет Ардак",
             "displayAuthorName": "API • Жолмаганбет Ардак"}
        out = potok_sync.to_webhook_message(m, self.CHAT)
        self.assertTrue(out["isEcho"])
        self.assertEqual(out["status"], "read")
        self.assertEqual(out["authorName"], "Жолмаганбет Ардак")
        self.assertIsNone(out["authorId"], "id автора окно не отдаёт — ключ строит store по имени")

    def test_media_gets_store_uri_and_type_from_content_type(self):
        m = {"id": "ff56", "channelId": "35ac2ea6", "chatId": "77711447271", "datetime": 1790137634080,
             "incoming": True, "status": 99, "type": 3, "contentType": "audio/mpeg",
             "contentSha": "94417783463ff328a0c271f6bfab7339380237c8",
             "filename": "audio-wamid.HBgLNzc3MTE0NDcyNzEVAgASGBQzQUM5NzY4MUU0NDNENUI2RUM3OAA=.mp3"}
        out = potok_sync.to_webhook_message(m, self.CHAT)
        self.assertEqual(out["type"], "audio")
        self.assertEqual(out["contentUri"],
                         "https://store.wazzup24.com/94417783463ff328a0c271f6bfab7339380237c8/"
                         "?filename=audio-wamid.HBgLNzc3MTE0NDcyNzEVAgASGBQzQUM5NzY4MUU0NDNENUI2RUM3OAA%3D.mp3")
        pdf = dict(m, type=5, contentType="application/pdf", filename="910204000228.pdf")
        self.assertEqual(potok_sync.message_type(pdf), "document")
        self.assertEqual(potok_sync.message_type({"type": 2}), "image")

    def test_error_deleted_and_template(self):
        self.assertEqual(potok_sync.message_status({"incoming": False, "status": 2, "errorCode": "E1"}), "error")
        self.assertEqual(potok_sync.message_status({"incoming": False, "status": 2}), "delivered")
        self.assertEqual(potok_sync.message_status({"incoming": False, "status": 1}), "sent")
        self.assertEqual(potok_sync.message_type({"type": 1, "isHsm": True, "text": "t"}), "wapi_template")
        out = potok_sync.to_webhook_message(
            {"id": "d", "channelId": "c", "chatId": "1", "datetime": 1, "incoming": False,
             "messageDeleteByUser": True}, self.CHAT)
        self.assertTrue(out["isDeleted"])

    def test_channel_falls_back_to_chat_row(self):
        out = potok_sync.to_webhook_message(
            {"id": "d", "chatId": "77002199865", "datetime": 1, "incoming": True}, self.CHAT)
        self.assertEqual(out["channelId"], "35ac2ea6-9592-45bd-9d9b-1435007d9f60")


class _FakePagingClient(potok_client.WazzupInternalClient):
    """Подменяем сетевой слой: страницы отдаём из словаря."""

    def __init__(self, chats_pages, messages_pages):
        super().__init__(api_key="k", account_id="1", viewer_user_id="1", viewer_name="x", min_interval=0)
        self._chats_pages = chats_pages
        self._messages_pages = messages_pages
        self.calls = []

    def _get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "v2/chats":
            key = (params["name"], params["offset"]) if params.get("name") else params["offset"]
            page = self._chats_pages.get(key, [])
            if page == "CEILING":
                raise potok_client.WazzupInternalError(
                    'v2/chats: HTTP 500 {"errors":[{"code":"CHAT_CAN_NOT_GET_CHATS"}]}')
            return {"data": page}
        rows = self._messages_pages.get((params["chatId"], params["offset"]), [])
        # как настоящий сервер: сообщения несут chatId запрошенного чата
        return {"messages": [dict(m, chatId=m.get("chatId", params["chatId"])) for m in rows]}


class PagingTests(unittest.TestCase):
    def test_chats_stop_at_first_page_crossing_since(self):
        since = 1000
        pages = {
            0: [{"chatId": "a", "lastMessage": {"datetime": 3000}}] * 100,
            100: [{"chatId": "b", "lastMessage": {"datetime": 1500}}] * 99
                 + [{"chatId": "old", "lastMessage": {"datetime": 500}}],
            200: [{"chatId": "never", "lastMessage": {"datetime": 400}}],
        }
        client = _FakePagingClient(pages, {})
        rows = list(client.iter_chats(since))
        self.assertEqual(len(rows), 199, "старый чат на границе отброшен, дальше не листаем")
        self.assertEqual([c[1]["offset"] for c in client.calls], [0, 100])

    def test_messages_page_by_offset_until_short_page_or_since(self):
        since = 100
        pages = {
            ("a", 0): [{"datetime": 2000 - i} for i in range(998)],      # «полная» страница ≈ 998, вся в окне
            ("a", 998): [{"datetime": 50}],                              # старше окна
            ("b", 0): [{"datetime": 900}, {"datetime": 800}],            # короткая — последняя
        }
        client = _FakePagingClient({}, pages)
        self.assertEqual(len(list(client.iter_messages("a", since, chat_type="whatsapp"))), 998)
        self.assertEqual([c[1]["offset"] for c in client.calls if c[1].get("chatId") == "a"], [0, 998])
        client.calls.clear()
        self.assertEqual(len(list(client.iter_messages("b", since, chat_type="whatsapp"))), 2)
        self.assertEqual(len(client.calls), 1, "короткая страница не тянет за собой пустой запрос")

    def test_messages_request_always_carries_chat_type(self):
        """Без chatType сервер молча отдаёт ленту всего аккаунта — одну на любой чат.
        Ровно так первый прогон на проде записал 551 сообщение и 33 тысячи раз их
        перезаписал. Тип обязателен, канал — если он 36 символов."""
        client = _FakePagingClient({}, {("a", 0): [{"datetime": 5}]})
        list(client.iter_messages("a", 1, chat_type="whatsapp",
                                  channel_id="35ac2ea6-9592-45bd-9d9b-1435007d9f60"))
        params = client.calls[0][1]
        self.assertEqual(params["chatType"], "whatsapp")
        self.assertEqual(params["channelId"], "35ac2ea6-9592-45bd-9d9b-1435007d9f60")
        with self.assertRaises(potok_client.WazzupInternalError):
            list(client.iter_messages("a", 1, chat_type=None))

    def test_foreign_chat_messages_are_refused(self):
        """Если сервер отдал сообщения другого чата — контракт поменялся: шумим,
        а не пишем чужое под этим чатом."""
        client = _FakePagingClient({}, {("a", 0): [{"datetime": 5, "chatId": "someone-else"}]})
        with self.assertRaises(potok_client.WazzupInternalError):
            list(client.iter_messages("a", 1, chat_type="whatsapp"))

    def test_chat_identity_from_list_row(self):
        row = {"chatId": "77002199865", "chatType": "whatsapp",
               "chats": [{"chatType": "whatsapp", "channelId": "35ac2ea6-9592-45bd-9d9b-1435007d9f60"}]}
        self.assertEqual(potok_client.WazzupInternalClient.chat_identity(row),
                         ("77002199865", "whatsapp", "35ac2ea6-9592-45bd-9d9b-1435007d9f60"))


class _FakeDb:
    def __init__(self, last=None, known=None):
        self.batches = []
        self.last = last
        self.known = known or {}

    def store_wazzup_messages(self, messages, account="op"):
        self.batches.append((account, list(messages)))
        return len(messages)

    def wazzup_last_message_at(self, account="op"):
        return self.last

    def wazzup_chat_last_messages(self, account="op"):
        return dict(self.known)


class SyncTests(unittest.TestCase):
    def test_sync_writes_only_window_and_tags_account(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        since = now - timedelta(days=1)
        chat = {"chatId": "77002199865", "chatType": "whatsapp", "lastMessage": {"datetime": MS(now)},
                "chats": [{"channelId": "ch"}]}
        pages_chats = {0: [chat]}
        pages_msgs = {("77002199865", 0): [
            {"id": "new", "channelId": "ch", "chatId": "77002199865", "datetime": MS(now), "incoming": True},
            {"id": "old", "channelId": "ch", "chatId": "77002199865",
             "datetime": MS(since - timedelta(hours=1)), "incoming": True},
        ]}
        client = _FakePagingClient(pages_chats, pages_msgs)
        db = _FakeDb()
        stats = potok_sync.sync_account(db, client, since, account="potok")
        self.assertEqual(stats["chats_done"], 1)
        self.assertEqual(stats["messages"], 1, "сообщение старше окна в базу не идёт")
        self.assertEqual(db.batches[0][0], "potok")
        self.assertEqual(db.batches[0][1][0]["messageId"], "new")

    def test_list_ceiling_falls_back_to_phone_patterns(self):
        """Список чатов кончается 500 на глубине ~7850: остаток добираем масками
        номеров через поиск name=, уже виденные чаты не перечитываем."""
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        since = now - timedelta(days=45)
        row = lambda cid: {"chatId": cid, "chatType": "whatsapp",  # noqa: E731
                           "lastMessage": {"datetime": MS(now)}, "chats": [{"channelId": "ch"}]}
        pages = {0: [row("77001111111")] * 1, 100: "CEILING"}
        # маска 7700 находит и уже виденный чат, и новый; остальные маски пусты
        pages[("7700", 0)] = [row("77001111111"), row("77002222222")]
        msgs = {("77001111111", 0): [{"id": "a", "channelId": "ch", "datetime": MS(now), "incoming": True}],
                ("77002222222", 0): [{"id": "b", "channelId": "ch", "datetime": MS(now), "incoming": True}]}
        client = _FakePagingClient(pages, msgs)
        # первая страница «полная» только по флагу: делаем её из 100 строк одного чата
        client._chats_pages[0] = [row("77001111111")] * 100
        db = _FakeDb()
        stats = potok_sync.sync_account(db, client, since, account="potok")
        self.assertTrue(stats["list_ceiling"])
        self.assertEqual(stats["patterns_done"], len(potok_sync.PHONE_PATTERNS))
        self.assertEqual(stats["chats_done"], 2, "новый чат из маски догружен, виденный — один раз")
        names = [c[1].get("name") for c in client.calls if c[0] == "v2/chats" and c[1].get("name")]
        self.assertEqual(names[0], "7700")
        self.assertEqual(len(set(names)), 100)
        requested = [c[1]["chatId"] for c in client.calls if c[0] == "v2/messages"]
        self.assertEqual(requested, ["77001111111", "77002222222"])

    def test_other_list_errors_still_raise(self):
        pages = {0: [{"chatId": "x", "chatType": "whatsapp", "lastMessage": {"datetime": 10 ** 13}, "chats": [{"channelId": "c"}]}] * 100}
        client = _FakePagingClient(pages, {})
        client._get = lambda path, params: (_ for _ in ()).throw(potok_client.WazzupInternalError("v2/chats: HTTP 502 bad gateway"))
        with self.assertRaises(potok_client.WazzupInternalError):
            potok_sync.sync_account(_FakeDb(), client, datetime(2026, 1, 1, tzinfo=timezone.utc), account="potok")

    def test_known_chats_are_skipped_without_message_requests(self):
        """Возобновление после рестарта: чат, чьё последнее сообщение уже у нас,
        не запрашивается; чат с более свежим lastMessage — запрашивается."""
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        since = now - timedelta(days=45)
        fresh = {"chatId": "new", "chatType": "whatsapp", "lastMessage": {"datetime": MS(now)},
                 "chats": [{"channelId": "ch"}]}
        stale = {"chatId": "old", "chatType": "whatsapp",
                 "lastMessage": {"datetime": MS(now - timedelta(hours=1))}, "chats": [{"channelId": "ch"}]}
        client = _FakePagingClient({0: [fresh, stale]}, {
            ("new", 0): [{"id": "n1", "channelId": "ch", "datetime": MS(now), "incoming": True}],
            ("old", 0): [{"id": "o1", "channelId": "ch", "datetime": MS(now - timedelta(hours=1)), "incoming": True}],
        })
        # «old» уже загружен ровно до его последнего сообщения, «new» — устарел на час
        db = _FakeDb(known={"old": now - timedelta(hours=1), "new": now - timedelta(hours=1)})
        stats = potok_sync.sync_account(db, client, since, account="potok")
        self.assertEqual(stats["skipped_known"], 1)
        self.assertEqual(stats["chats_done"], 1)
        requested = [c[1]["chatId"] for c in client.calls if c[0] == "v2/messages"]
        self.assertEqual(requested, ["new"], "сообщения запрошены только у изменившегося чата")

    def test_run_sync_incremental_uses_last_message_minus_overlap(self):
        last = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        db = _FakeDb(last=last)
        seen = {}

        def factory(account):
            client = _FakePagingClient({0: []}, {})
            seen["client"] = client
            return client

        def fake_sync(db_, client, since, account):
            seen["since"] = since
            return {"chats_seen": 0, "chats_done": 0, "messages": 0, "skipped_chats": 0}

        with mock.patch.object(potok_sync, "sync_account", side_effect=fake_sync):
            stats = potok_sync.run_sync(db, account="potok", client_factory=factory,
                                        now=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(seen["since"], last - timedelta(hours=2))
        self.assertEqual(stats["mode"], "incremental")
        self.assertFalse(potok_sync.STATE["running"])

    def test_run_sync_history_mode_and_empty_db(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        captured = {}

        def fake_sync(db_, client, since, account):
            captured["since"] = since
            return {"chats_seen": 0, "chats_done": 0, "messages": 0, "skipped_chats": 0}

        with mock.patch.object(potok_sync, "sync_account", side_effect=fake_sync):
            stats = potok_sync.run_sync(_FakeDb(), account="potok", days=45,
                                        client_factory=lambda a: _FakePagingClient({}, {}), now=now)
            self.assertEqual(captured["since"], now - timedelta(days=45))
            self.assertEqual(stats["mode"], "history")
            # пустая база без days — берём стандартные 45 дней
            potok_sync.run_sync(_FakeDb(), account="potok",
                                client_factory=lambda a: _FakePagingClient({}, {}), now=now)
            self.assertEqual(captured["since"], now - timedelta(days=potok_sync.DEFAULT_HISTORY_DAYS))

    def test_parallel_run_exits_instead_of_waiting(self):
        self.assertTrue(potok_sync._RUN_LOCK.acquire(blocking=False))
        try:
            self.assertEqual(potok_sync.run_sync(_FakeDb(), account="potok"), {"locked": True})
        finally:
            potok_sync._RUN_LOCK.release()


class WiringSourceTests(unittest.TestCase):
    """Обе половины — база и приложение — знают про аккаунт."""

    def test_schema_has_account_everywhere(self):
        for table in ("wazzup_messages", "wazzup_chats", "wazzup_operator_map"):
            self.assertIn(f"ALTER TABLE {table}\n                    ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'op';",
                          DB_SOURCE.replace("\r\n", "\n"), table)
        self.assertIn("def store_wazzup_messages(self, messages, account='op'):", DB_SOURCE)
        self.assertIn("def cleanup_wazzup_messages(self, retention_days=45):", DB_SOURCE)
        self.assertIn("WHERE m.account = %s AND (e.le IS NULL OR m.dt > e.le)", DB_SOURCE,
                      "эпизоды ИИ-оценки — только по историческому аккаунту")
        self.assertIn("FULL JOIN (SELECT * FROM wazzup_operator_map WHERE account = %s) map", DB_SOURCE)

    def test_api_routes_take_account(self):
        self.assertIn("@app.route('/api/wazzup/accounts'", API_SOURCE)
        self.assertIn("@app.route('/api/wazzup/potok/sync'", API_SOURCE)
        self.assertIn("id='wazzup_potok_sync_10min'", API_SOURCE)
        # джоба проходит полное окно (с пропуском загруженного): так оборванная
        # деплоем загрузка истории доводится сама, без ручного перезапуска
        self.assertIn("db, account='potok', days=wazzup_potok_sync.DEFAULT_HISTORY_DAYS))", API_SOURCE)
        self.assertIn("account = wazzup_accounts.account_by_webhook_token(token)", API_SOURCE)
        self.assertIn("db.store_wazzup_messages(payload.get('messages'), account=account)", API_SOURCE)
        # каждая читающая ручка валидирует аккаунт и отвечает 400 на чужой
        self.assertGreaterEqual(API_SOURCE.count('return jsonify({"error": "unknown account"}), 400'), 5)
        self.assertNotIn("_WAZZUP_KAZ_TRANS = str.maketrans", API_SOURCE,
                         "нормализация имён переехала в wazzup/names.py")

    def test_frontend_switches_account_everywhere(self):
        self.assertIn('data-testid="wazzup-account-switch"', VIEW_SOURCE)
        self.assertIn("params: { account }", VIEW_SOURCE)                  # аналитика / авторы
        self.assertIn("account: accountRef.current", VIEW_SOURCE)         # чаты / лента
        self.assertIn("syncWazzupChatDeepLink(selected, account)", VIEW_SOURCE)
        self.assertIn("buildWazzupChatLink(selected, account)", VIEW_SOURCE)
        self.assertIn("история хранится 45 дней", VIEW_SOURCE)
        self.assertNotIn("WAZZUP_APP_BASE", VIEW_SOURCE, "воркспейс — по аккаунту, не константой")
        self.assertIn("export const WAZZUP_ACCOUNT_QUERY_PARAM = 'account';", LINK_SOURCE)
        # уход из раздела снимает и account=, там же где chat=
        self.assertEqual(APP_SOURCE.count("url.searchParams.delete(WAZZUP_ACCOUNT_QUERY_PARAM);"), 2)


if __name__ == "__main__":
    unittest.main()
