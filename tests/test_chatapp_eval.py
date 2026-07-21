"""Тесты «Случайного чата» ChatApp (ТП/ОП ТЭЗ).

Проверяется то, где живут реальные грабли этого источника, найденные на живых
данных аккаунта Tez: отделение ручных ответов оператора от автоответов Bitrix,
атрибуция по created.id, приведение сообщения к общей схеме снапшота и карта
«направление оператора -> направление критериев» (у ТЭЗ они разные).

Сама формула оценки не тестируется — она общая для звонков и чатов и живёт во
фронте журнала (см. tests/test_c2d_eval.py)."""
import ast
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BOT_PATH = ROOT / "bot_schedule2.py"

import chatapp_client  # noqa: E402


def _chatapp_namespace(env=None):
    """Выдёргивает из bot_schedule2.py нужные функции, не поднимая всё приложение."""
    wanted_assignments = {
        "CHATAPP_EPISODE_GAP_HOURS",
        "CHATAPP_SYNC_DAYS",
        "CHATAPP_CHAT_DIRECTION_MAP_RAW",
        "_CHATAPP_MEDIA_PLACEHOLDERS",
    }
    wanted_functions = {
        "_chatapp_direction_map",
        "_chatapp_normalize_snapshot_message",
    }
    module = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
    selected = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assignments:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            selected.append(node)

    saved = dict(os.environ)
    if env:
        os.environ.update(env)
    try:
        namespace = {"os": os, "re": re, "datetime": datetime,
                     "timedelta": timedelta, "ZoneInfo": ZoneInfo}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(BOT_PATH), "exec"),
             namespace)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return namespace


# Сообщения ниже — сокращённые копии реальных ответов API (лицензия 72861).
INCOMING = {
    "id": "wamid.IN1", "side": "in", "time": 1784572731, "type": "text",
    "message": {"text": "Ассалаумалейкум", "caption": "", "file": None},
    "fromApp": None, "created": None,
    "fromUser": {"id": "77763068686", "name": "«А»«И»«К»", "phone": "77763068686"},
}
BITRIX_GREETING = {
    "id": "wamid.OUT1", "side": "out", "time": 1784572740, "type": "text",
    "message": {"text": "Здравствуйте! Добро пожаловать в Tez taxi!", "file": None},
    "fromApp": {"id": "bitrix", "sender": "employee"}, "created": {"id": 84210},
}
SYSTEM_ASSIGN = {
    "id": "uuid-1", "side": "out", "time": 1784572761, "type": "system",
    "message": {"text": 'Назначен ответственный "Естай Аяжан [85501]"', "file": None},
    "fromApp": {"id": "webchat", "sender": "system"}, "created": {"id": 85501},
}
OPERATOR_REPLY = {
    "id": "wamid.OUT2", "side": "out", "time": 1784572778, "type": "text",
    "message": {"text": "Сәлеметсіз бе!", "caption": "", "file": None},
    "fromApp": {"id": "webchat", "sender": "employee"}, "created": {"id": 85501},
}
VOICE_FROM_CLIENT = {
    "id": "wamid.IN2", "side": "in", "time": 1784572800, "type": "voice",
    "message": {"text": "", "caption": "",
                "file": {"link": "https://s3.example/x.oga", "name": "x.oga",
                         "contentType": "audio/ogg"}},
    "fromApp": None, "created": None,
}


class AutoreplyAndAttributionTests(unittest.TestCase):
    """Главная развилка источника: кто из исходящих — живой оператор."""

    def test_client_message_has_no_employee(self):
        self.assertFalse(chatapp_client.is_autoreply(INCOMING))
        self.assertIsNone(chatapp_client.employee_id(INCOMING))

    def test_bitrix_greeting_is_autoreply_not_operator_work(self):
        # Приветствие идёт от интеграции, но с created.id владельца аккаунта:
        # если не отсечь, робот «съест» атрибуцию каждого эпизода.
        self.assertTrue(chatapp_client.is_autoreply(BITRIX_GREETING))
        self.assertIsNone(chatapp_client.employee_id(BITRIX_GREETING))

    def test_system_event_is_not_operator_work(self):
        self.assertTrue(chatapp_client.is_autoreply(SYSTEM_ASSIGN))
        self.assertIsNone(chatapp_client.employee_id(SYSTEM_ASSIGN))

    def test_manual_reply_attributes_to_employee(self):
        self.assertFalse(chatapp_client.is_autoreply(OPERATOR_REPLY))
        self.assertEqual(chatapp_client.employee_id(OPERATOR_REPLY), 85501)

    def test_outgoing_without_from_app_counts_as_human(self):
        # Подстраховка: если ChatApp пришлёт исходящее без fromApp, считаем его
        # ручным, а не роботом — потерять работу оператора хуже, чем наоборот.
        msg = dict(OPERATOR_REPLY, fromApp=None)
        self.assertFalse(chatapp_client.is_autoreply(msg))
        self.assertEqual(chatapp_client.employee_id(msg), 85501)


class MessageRowTests(unittest.TestCase):
    def test_row_keeps_employee_even_for_autoreply(self):
        # В БД пишем created.id как есть; «робот это или нет» решают app_id и
        # app_sender, чтобы правило можно было менять без перезаливки данных.
        row = chatapp_client.message_row(BITRIX_GREETING, 72861, 'caWhatsApp', '77763068686')
        self.assertEqual(row['employee_id'], 84210)
        self.assertEqual(row['app_id'], 'bitrix')
        self.assertEqual(row['app_sender'], 'employee')

    def test_row_unpacks_file_and_utc_time(self):
        row = chatapp_client.message_row(VOICE_FROM_CLIENT, 72861, 'caWhatsApp', 'chat')
        self.assertEqual(row['file_link'], 'https://s3.example/x.oga')
        self.assertEqual(row['file_content_type'], 'audio/ogg')
        self.assertEqual(row['dt'], datetime.fromtimestamp(1784572800, timezone.utc))

    def test_caption_used_when_text_empty(self):
        msg = dict(INCOMING, message={"text": "", "caption": "подпись к фото", "file": None})
        self.assertEqual(chatapp_client.message_row(msg, 1, 'caWhatsApp', 'c')['text'],
                         'подпись к фото')

    def test_chat_row_reads_responsible(self):
        row = chatapp_client.chat_row({
            'id': '77763068686', 'internalId': '77904461', 'licenseId': 72861,
            'messengerType': 'caWhatsApp', 'type': 'private', 'phone': '77763068686',
            'name': 'Тест', 'lastTime': 1784627855, 'responsible': {'id': 96346},
            'status': None,
        })
        self.assertEqual(row['responsible_employee_id'], 96346)
        self.assertEqual(row['license_id'], 72861)


class SnapshotMessageTests(unittest.TestCase):
    """Лента оценки общая для Chat2Desk/Wazzup/ChatApp — схема должна совпадать."""

    @classmethod
    def setUpClass(cls):
        cls.ns = _chatapp_namespace()

    def normalize(self, row):
        return self.ns["_chatapp_normalize_snapshot_message"](row)

    def _row(self, **over):
        base = {
            'message_id': 'm1',
            'dt': datetime(2026, 7, 20, 14, 18, tzinfo=timezone.utc),
            'side': 'out', 'type': 'text', 'text': 'Привет', 'file_link': None,
            'file_name': None, 'file_content_type': None, 'is_deleted': False,
            'human_out': True, 'is_bot': False, 'matched_name': 'Естай Аяжан Еркинкызы',
            'author_name': 'Естай Аяжан',
        }
        base.update(over)
        return base

    def test_operator_reply_is_to_client_with_matched_name(self):
        out = self.normalize(self._row())
        self.assertEqual(out['type'], 'to_client')
        self.assertEqual(out['author'], 'Естай Аяжан Еркинкызы')

    def test_autoreply_marked_separately(self):
        out = self.normalize(self._row(human_out=False))
        self.assertEqual(out['type'], 'autoreply')

    def test_bot_flag_wins_over_human_out(self):
        out = self.normalize(self._row(is_bot=True))
        self.assertEqual(out['type'], 'autoreply')

    def test_incoming_has_no_author(self):
        out = self.normalize(self._row(side='in', matched_name=None, author_name=None))
        self.assertEqual(out['type'], 'from_client')
        self.assertIsNone(out['author'])

    def test_time_is_naive_almaty(self):
        # Лента рендерит new Date(iso); naive-локаль Алматы даёт одинаковое
        # настенное время в любом часовом поясе зрителя (как у c2d/wazzup).
        out = self.normalize(self._row())
        self.assertEqual(out['created'], '2026-07-20T19:18:00')

    def test_media_goes_to_typed_slot(self):
        out = self.normalize(self._row(side='in', type='voice', text='',
                                       file_link='https://s3/x.oga',
                                       file_content_type='audio/ogg'))
        self.assertEqual(out['audio'], 'https://s3/x.oga')
        self.assertEqual(out['attachments'], [])

    def test_document_goes_to_attachments(self):
        out = self.normalize(self._row(side='in', type='file', text='',
                                       file_link='https://s3/doc.pdf',
                                       file_name='doc.pdf',
                                       file_content_type='application/pdf'))
        self.assertEqual(out['attachments'],
                         [{'name': 'doc.pdf', 'link': 'https://s3/doc.pdf'}])
        self.assertIsNone(out['photo'])

    def test_empty_media_gets_placeholder(self):
        out = self.normalize(self._row(side='in', type='image', text='', file_link=None))
        self.assertEqual(out['text'], '[фото]')

    def test_deleted_message_marked(self):
        out = self.normalize(self._row(is_deleted=True))
        self.assertTrue(out['text'].startswith('[удалено]'))


class DirectionMapTests(unittest.TestCase):
    """У ТЭЗ кнопка висит на «ТП линия», а критерии берутся у «ТП чат»."""

    def test_default_maps_tp_line_to_tp_chat(self):
        ns = _chatapp_namespace()
        self.assertEqual(ns["_chatapp_direction_map"](), {83: 84})

    def test_several_pairs(self):
        ns = _chatapp_namespace({"CHATAPP_CHAT_DIRECTION_MAP": "83:84, 78:90"})
        self.assertEqual(ns["_chatapp_direction_map"](), {83: 84, 78: 90})

    def test_garbage_is_ignored(self):
        ns = _chatapp_namespace({"CHATAPP_CHAT_DIRECTION_MAP": "83:84,xx,,7:"})
        self.assertEqual(ns["_chatapp_direction_map"](), {83: 84})

    def test_empty_map_disables_the_button(self):
        ns = _chatapp_namespace({"CHATAPP_CHAT_DIRECTION_MAP": ""})
        self.assertEqual(ns["_chatapp_direction_map"](), {})


class TokenStoreTests(unittest.TestCase):
    """Квота 100 tokens.make в сутки: лишний вход — реальный риск, не стиль."""

    class _Store:
        def __init__(self, data=None):
            self.data = data
            self.saves = 0

        def load(self):
            return self.data

        def save(self, data):
            self.data = data
            self.saves += 1

    def _client(self, store):
        return chatapp_client.ChatAppClient('e@x', 'p', 'app_1', token_store=store)

    def test_live_token_is_reused_without_network(self):
        store = self._Store({'accessToken': 'live',
                             'accessTokenEndTime': int(datetime.now(timezone.utc).timestamp()) + 3600})
        client = self._client(store)
        client._make_tokens = lambda: self.fail('нельзя тратить квоту при живом токене')
        client._refresh_tokens = lambda _t: self.fail('refresh при живом токене не нужен')
        self.assertEqual(client.access_token(), 'live')

    def test_expired_access_goes_through_refresh_not_login(self):
        now = int(datetime.now(timezone.utc).timestamp())
        store = self._Store({'accessToken': 'old', 'accessTokenEndTime': now - 10,
                             'refreshToken': 'r', 'refreshTokenEndTime': now + 86400})
        client = self._client(store)
        client._make_tokens = lambda: self.fail('пока жив refreshToken, вход запрещён')
        client._refresh_tokens = lambda t: client._store_tokens(
            {'accessToken': 'fresh', 'accessTokenEndTime': now + 3600, 'refreshToken': t})
        self.assertEqual(client.access_token(), 'fresh')
        self.assertEqual(store.data['accessToken'], 'fresh')

    def test_dead_refresh_falls_back_to_login(self):
        now = int(datetime.now(timezone.utc).timestamp())
        store = self._Store({'accessToken': 'old', 'accessTokenEndTime': now - 10,
                             'refreshToken': 'r', 'refreshTokenEndTime': now - 1})
        client = self._client(store)
        client._make_tokens = lambda: client._store_tokens(
            {'accessToken': 'new', 'accessTokenEndTime': now + 3600})
        client._refresh_tokens = lambda _t: self.fail('протухший refresh дёргать незачем')
        self.assertEqual(client.access_token(), 'new')

    def test_empty_store_logs_in(self):
        store = self._Store(None)
        client = self._client(store)
        client._make_tokens = lambda: client._store_tokens({'accessToken': 'first'})
        self.assertEqual(client.access_token(), 'first')
        self.assertEqual(store.saves, 1)


DB_PATH = ROOT / "database.py"


def _episode_builder():
    """Достаёт из database.py сборщик эпизодов с заглушкой курсора.

    Импортировать database.py на Windows нельзя (там time.tzset), поэтому берём
    только нужные методы класса — тем же приёмом, что и для bot_schedule2.py."""
    wanted = {"build_chatapp_episodes", "_store_chatapp_episode_tx"}
    wanted_attrs = {"CHATAPP_EPISODE_LOCK_KEY", "_CHATAPP_HUMAN_OUT"}
    module = ast.parse(DB_PATH.read_text(encoding="utf-8-sig"))
    body = []
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "Database":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in wanted:
                    body.append(item)
                elif isinstance(item, ast.Assign):
                    names = {t.id for t in item.targets if isinstance(t, ast.Name)}
                    if names & wanted_attrs:
                        body.append(item)
    klass = ast.ClassDef(name="EpisodeBuilder", bases=[], keywords=[],
                         body=body, decorator_list=[])
    ast.fix_missing_locations(klass)
    import logging as _logging
    namespace = {"datetime": datetime, "timedelta": timedelta,
                 "dt_timezone": timezone, "logging": _logging,
                 "Json": lambda v: v}
    exec(compile(ast.Module(body=[klass], type_ignores=[]), str(DB_PATH), "exec"),
         namespace)
    return namespace["EpisodeBuilder"]


class _FakeCursor:
    """Отвечает на три запроса сборщика и копит вставленные эпизоды."""

    def __init__(self, messages, contacts):
        self.messages = messages
        self.contacts = contacts
        self.inserted = []
        self._result = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        if 'pg_try_advisory_xact_lock' in text:
            self._result = [(True,)]
        elif 'FROM chatapp_messages' in text:
            self._result = list(self.messages)
        elif 'FROM chatapp_chats' in text:
            self._result = list(self.contacts)
        elif 'INSERT INTO chatapp_episodes' in text:
            self.inserted.append(params)
            self.rowcount = 1
            self._result = []
        else:
            raise AssertionError(f'неожиданный запрос: {text[:120]}')

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result or []


class EpisodeBuildTests(unittest.TestCase):
    """Нарезка по паузе и атрибуция — сердце «Случайного чата»."""

    BASE = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)

    @classmethod
    def setUpClass(cls):
        cls.Builder = _episode_builder()

    def _msg(self, minutes, side='in', employee=None, user=None,
             human_out=None, is_bot=False):
        if human_out is None:
            human_out = side == 'out'
        return (72861, 'caWhatsApp', '7777', self.BASE + timedelta(minutes=minutes),
                side, employee, 'webchat', 'employee', 'text', user, is_bot, human_out)

    def _build(self, messages, now_offset_minutes=600, **kwargs):
        builder = self.Builder()
        cursor = _FakeCursor(messages, [(72861, 'caWhatsApp', '7777', 'Клиент', '7777')])

        class _Ctx:
            def __enter__(self_inner):
                return cursor

            def __exit__(self_inner, *a):
                return False

        builder._get_cursor = lambda: _Ctx()
        result = builder.build_chatapp_episodes(
            now=self.BASE + timedelta(minutes=now_offset_minutes), **kwargs)
        return result, cursor.inserted

    def test_pause_splits_into_two_episodes(self):
        msgs = [self._msg(0), self._msg(2, 'out', 85501, 338),
                self._msg(60 * 8), self._msg(60 * 8 + 3, 'out', 85501, 338)]
        result, inserted = self._build(msgs, now_offset_minutes=60 * 20)
        self.assertEqual(result['stored'], 2)
        self.assertEqual(len(inserted), 2)

    def test_short_pause_stays_one_episode(self):
        msgs = [self._msg(0), self._msg(2, 'out', 85501, 338),
                self._msg(120), self._msg(123, 'out', 85501, 338)]
        result, inserted = self._build(msgs)
        self.assertEqual(result['stored'], 1)
        self.assertEqual(inserted[0][7], 4)  # messages_count

    def test_open_tail_is_not_stored(self):
        # Последний эпизод ещё «живой» (тишина меньше паузы) — его дособерут
        # следующей ночью, иначе оценка попадёт на недописанный диалог.
        msgs = [self._msg(0), self._msg(2, 'out', 85501, 338)]
        result, inserted = self._build(msgs, now_offset_minutes=10)
        self.assertEqual(result['stored'], 0)
        self.assertEqual(result['skipped_open'], 1)

    def test_dominant_operator_wins_attribution(self):
        msgs = [self._msg(0),
                self._msg(1, 'out', 92101, 345),
                self._msg(2, 'out', 92101, 345),
                self._msg(3, 'out', 98402, 347)]
        _result, inserted = self._build(msgs)
        row = inserted[0]
        self.assertEqual(row[12], 345)              # operator_user_id
        self.assertAlmostEqual(row[13], 0.667, 3)   # operator_share
        self.assertEqual(row[11], 'dialog')         # kind

    def test_bitrix_autoreply_does_not_take_attribution(self):
        # Приветствие робота — единственное исходящее: оператора нет, и это
        # «без ответа», а не диалог.
        msgs = [self._msg(0), self._msg(1, 'out', 84210, None, human_out=False)]
        _result, inserted = self._build(msgs)
        row = inserted[0]
        self.assertIsNone(row[12])
        self.assertEqual(row[11], 'unanswered')

    def test_unmapped_employee_leaves_episode_unattributed(self):
        # Сотрудник есть, привязки к users нет -> эпизод не попадёт в выборку
        # (pick требует operator_user_id), но сохраняется как dialog.
        msgs = [self._msg(0), self._msg(1, 'out', 99667, None)]
        _result, inserted = self._build(msgs)
        self.assertIsNone(inserted[0][12])
        self.assertEqual(inserted[0][11], 'dialog')

    def test_bot_flag_excludes_from_human_outbound(self):
        msgs = [self._msg(0), self._msg(1, 'out', 85501, 338, is_bot=True)]
        _result, inserted = self._build(msgs)
        self.assertEqual(inserted[0][10], 0)        # human_outbound_count
        self.assertEqual(inserted[0][11], 'unanswered')

    def test_outbound_only_episode(self):
        msgs = [self._msg(0, 'out', 85501, 338), self._msg(1, 'out', 85501, 338)]
        _result, inserted = self._build(msgs)
        self.assertEqual(inserted[0][11], 'outbound_only')

    def test_overlong_episode_is_force_closed(self):
        msgs = [self._msg(i, 'out' if i % 2 else 'in', 85501 if i % 2 else None,
                          338 if i % 2 else None) for i in range(12)]
        result, inserted = self._build(msgs, force_close_msgs=5)
        self.assertGreaterEqual(result['stored'], 2)
        self.assertTrue(inserted[0][15])            # force_closed

    def test_concurrent_run_backs_off(self):
        builder = self.Builder()
        cursor = _FakeCursor([], [])
        cursor.execute = lambda sql, params=None: setattr(cursor, '_result', [(False,)])

        class _Ctx:
            def __enter__(self_inner):
                return cursor

            def __exit__(self_inner, *a):
                return False

        builder._get_cursor = lambda: _Ctx()
        self.assertEqual(builder.build_chatapp_episodes(now=self.BASE)['locked'], True)


class ConfigTests(unittest.TestCase):
    def test_license_ids_parsed(self):
        cfg = chatapp_client.get_config({
            'CHATAPP_EMAIL': 'e', 'CHATAPP_PASSWORD': 'p', 'CHATAPP_APP_ID': 'a',
            'CHATAPP_LICENSE_IDS': '72861, 70651', 'CHATAPP_COMPANY_ID': '71322',
        })
        self.assertEqual(cfg['license_ids'], [72861, 70651])
        self.assertEqual(cfg['company_id'], 71322)
        self.assertTrue(chatapp_client.api_ready(cfg))

    def test_not_ready_without_password(self):
        cfg = chatapp_client.get_config({'CHATAPP_EMAIL': 'e', 'CHATAPP_APP_ID': 'a'})
        self.assertFalse(chatapp_client.api_ready(cfg))
        self.assertEqual(cfg['license_ids'], [])


if __name__ == '__main__':
    unittest.main()
