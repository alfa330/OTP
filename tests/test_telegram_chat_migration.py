# -*- coding: utf-8 -*-
"""Группа Telegram стала супергруппой: id чата сменился, а портал помнит старый.

Так группа СЗоВ перестала получать отбивку линии и отчёт по чатам (в журнале портала это видно
как минимум с 05.11.2026, нашлось 12.11): Telegram на каждую отправку отвечал «The group has been
migrated to a supergroup. New id: …», портал писал это в журнал и через час пробовал тот же
старый id.

Что закреплено:
  * получив этот ответ, плановая отправка переносит на новый id всё, что портал помнит о чате
    (db.migrate_telegram_chat_id), и тут же шлёт то же сообщение туда;
  * если новый id в этом же заходе уже есть среди получателей, второй раз группе не шлём;
  * картинки не подменяются текстом в мёртвый чат: ошибка переезда поднимается к вызывающему;
  * обычные отказы (бота выгнали, сеть) идут прежней дорогой;
  * перенос в базе: где записан только старый id — он заменяется; где записаны оба — остаётся
    строка нового, а в реестре чатов бота старая строка выключается, а не удаляется.
"""
import ast
import asyncio
import functools
import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import source_cache  # noqa: E402

BOT_PATH = source_cache.monolith_flat_path()
OLD, NEW = -5405194220, -1003936452440


class Migrated(Exception):
    """Двойник aiogram.utils.exceptions.MigrateToChat: у ошибки есть новый id чата."""

    def __init__(self, new_chat_id):
        super().__init__('The group has been migrated to a supergroup. New id: %s.' % new_chat_id)
        self.migrate_to_chat_id = new_chat_id


class Log:
    def __init__(self):
        self.lines = []

    def _write(self, level, message, *args, **_kwargs):
        self.lines.append((level, message % args if args else message))

    def info(self, message, *args, **kwargs):
        self._write('info', message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self._write('warning', message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._write('error', message, *args, **kwargs)

    def of(self, level):
        return [text for kind, text in self.lines if kind == level]


class FakeDb:
    def __init__(self, chats=None, subscriptions=None, moved=None):
        self.chats = chats or []
        self.subscriptions = subscriptions or []
        self.moved = {'szov_wallboard_broadcast_chats': 1} if moved is None else moved
        self.migrations = []
        self.marked = []

    def get_szov_broadcast_chats(self, direction):
        return self.chats

    def get_chat_hourly_subscriptions(self):
        return self.subscriptions

    def mark_chat_hourly_subscription_sent(self, chat_id):
        self.marked.append(chat_id)

    def migrate_telegram_chat_id(self, old_chat_id, new_chat_id):
        self.migrations.append((old_chat_id, new_chat_id))
        return self.moved


def _functions(*names):
    tree = source_cache.parse(BOT_PATH.read_text(encoding='utf-8-sig'))
    found = [node for node in tree.body
             if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names]
    missing = set(names) - {node.name for node in found}
    assert not missing, 'нет в bot_schedule2.py: %s' % sorted(missing)
    return found


def _namespace(*names, **extra):
    namespace = {'asyncio': asyncio, 'functools': functools, 'executor_pool': None,
                 'SZOV_BROADCAST_MODE_DEVIATIONS': 'deviations', 'logging': Log()}
    namespace.update(extra)
    exec(compile(ast.Module(body=_functions(*names), type_ignores=[]), str(BOT_PATH), 'exec'), namespace)
    return namespace


class FollowMigrationTests(unittest.TestCase):

    def _run(self, db, also_subscribed=(), send_fails=False):
        sent = []

        async def send(chat_id):
            if send_fails:
                raise RuntimeError('Forbidden: bot is not a member of the supergroup chat')
            sent.append(chat_id)

        ns = _namespace('_telegram_follow_migration', db=db)
        result = asyncio.run(ns['_telegram_follow_migration'](
            str(OLD), Migrated(NEW), 'Отбивка табло', send, also_subscribed=also_subscribed))
        return result, sent, ns['logging']

    def test_subscriptions_move_and_the_message_goes_to_the_new_chat(self):
        db = FakeDb(moved={'szov_wallboard_broadcast_chats': 1, 'chat_hourly_subscriptions': 1})
        result, sent, log = self._run(db)
        self.assertEqual(db.migrations, [(str(OLD), NEW)])
        self.assertEqual(sent, [NEW])
        self.assertEqual(result, NEW)
        self.assertEqual(log.of('warning'), [
            'Отбивка табло: группа %s стала супергруппой %s — подписки перенесены на новый id: '
            'chat_hourly_subscriptions 1, szov_wallboard_broadcast_chats 1' % (OLD, NEW)])

    def test_no_second_message_when_the_new_chat_is_subscribed_itself(self):
        db = FakeDb()
        result, sent, _log = self._run(db, also_subscribed=[str(OLD), str(NEW), 777])
        self.assertEqual(db.migrations, [(str(OLD), NEW)], 'подписки переносятся в любом случае')
        self.assertEqual(sent, [])
        self.assertIsNone(result)

    def test_failure_in_the_new_chat_is_reported_to_the_caller(self):
        with self.assertRaises(RuntimeError):
            self._run(FakeDb(), send_fails=True)

    def test_real_aiogram_error_carries_the_new_id(self):
        try:
            from aiogram.utils.exceptions import MigrateToChat
        except Exception:  # noqa: BLE001 - aiogram ставится не на всякой машине разработчика
            self.skipTest('aiogram не установлен')
        error = MigrateToChat(NEW)
        self.assertEqual(error.migrate_to_chat_id, NEW)
        self.assertIn('migrated to a supergroup', str(error))


class DeliverTests(unittest.TestCase):
    """Картинки не подменяются текстом в чат, которого больше нет."""

    def _bot(self, album_error):
        calls = []

        class Bot:
            async def send_media_group(self, chat_id, group):
                calls.append(('album', chat_id))
                raise album_error

            async def send_photo(self, chat_id, photo, caption=None, parse_mode=None):
                calls.append(('photo', chat_id))
                raise album_error

            async def send_message(self, chat_id, text, parse_mode=None):
                calls.append(('text', chat_id))

        return Bot(), calls

    def _types(self):
        class MediaGroup:
            def attach_photo(self, *args, **kwargs):
                pass

        return type('types', (), {'InputFile': staticmethod(lambda blob, filename=None: blob),
                                  'MediaGroup': MediaGroup})

    def _deliver(self, name, error, *args):
        from io import BytesIO
        bot, calls = self._bot(error)
        ns = _namespace(name, bot=bot, types=self._types(), BytesIO=BytesIO,
                        _chat_hourly_text=lambda data: 'текст отчёта')
        try:
            asyncio.run(ns[name](*args))
            raised = None
        except Exception as exc:  # noqa: BLE001
            raised = exc
        return raised, calls, ns['logging']

    def test_wallboard_album_to_a_migrated_group_is_not_replaced_with_text(self):
        raised, calls, log = self._deliver('_szov_broadcast_deliver', Migrated(NEW),
                                           OLD, 'цифры', [('a.png', b'1'), ('b.png', b'2')])
        self.assertIsInstance(raised, Migrated)
        self.assertEqual(calls, [('album', OLD)])
        self.assertEqual(log.of('error'), [])

    def test_wallboard_album_failure_still_falls_back_to_text(self):
        raised, calls, log = self._deliver('_szov_broadcast_deliver', RuntimeError('Bad Request: PHOTO_INVALID'),
                                           OLD, 'цифры', [('a.png', b'1'), ('b.png', b'2')])
        self.assertIsNone(raised)
        self.assertEqual(calls, [('album', OLD), ('text', OLD)])
        self.assertEqual(len(log.of('error')), 1)

    def test_chat_report_picture_to_a_migrated_group_is_not_replaced_with_text(self):
        raised, calls, _log = self._deliver('_chat_hourly_deliver', Migrated(NEW), OLD, {}, 'подпись', b'png')
        self.assertIsInstance(raised, Migrated)
        self.assertEqual(calls, [('photo', OLD)])

    def test_chat_report_picture_failure_still_falls_back_to_text(self):
        raised, calls, _log = self._deliver('_chat_hourly_deliver', RuntimeError('обрыв'), OLD, {}, 'подпись', b'png')
        self.assertIsNone(raised)
        self.assertEqual(calls, [('photo', OLD), ('text', OLD)])


class WallboardJobTests(unittest.TestCase):

    def _run(self, chats, dead=(OLD,), new_chat_fails=False):
        delivered, done = [], []
        db = FakeDb(chats=chats)

        async def deliver(chat_id, text, media):
            if chat_id in dead:
                raise Migrated(NEW)
            if new_chat_fails and chat_id == NEW:
                raise RuntimeError('Forbidden: bot was kicked from the supergroup chat')
            delivered.append(chat_id)

        async def prepare():
            return {'calls': 1}, 'цифры', [('a.png', b'1'), ('b.png', b'2')]

        async def on_delivered(data):
            done.append(data)

        ns = _namespace('_szov_broadcast_run_job', '_telegram_follow_migration', db=db,
                        _szov_broadcast_deliver=deliver)
        asyncio.run(ns['_szov_broadcast_run_job'](
            direction='osnova', label='Отбивка табло', prepare=prepare, deviations=lambda data: [],
            on_delivered=on_delivered))
        return db, delivered, done, ns['logging']

    def test_migrated_group_gets_the_broadcast_at_its_new_id(self):
        db, delivered, done, log = self._run([
            {'chat_id': OLD, 'is_enabled': True, 'mode': 'always'},
            {'chat_id': -100777, 'is_enabled': True, 'mode': 'always'}])
        self.assertEqual(db.migrations, [(OLD, NEW)])
        self.assertEqual(delivered, [NEW, -100777])
        self.assertEqual(len(done), 1, 'нарушения перерывов помечаются: отбивка дошла')
        self.assertEqual(log.of('error'), [])
        self.assertIn('Отбивка табло отправлена — чат %s (always)' % NEW, log.of('info'))

    def test_group_listed_under_both_ids_gets_one_message(self):
        db, delivered, _done, log = self._run([
            {'chat_id': OLD, 'is_enabled': True, 'mode': 'always'},
            {'chat_id': NEW, 'is_enabled': True, 'mode': 'always'}])
        self.assertEqual(db.migrations, [(OLD, NEW)])
        self.assertEqual(delivered, [NEW])
        self.assertEqual(log.of('error'), [])

    def test_failure_in_the_new_chat_is_logged_and_others_still_get_theirs(self):
        db, delivered, done, log = self._run([
            {'chat_id': OLD, 'is_enabled': True, 'mode': 'always'},
            {'chat_id': -100777, 'is_enabled': True, 'mode': 'always'}], new_chat_fails=True)
        self.assertEqual(db.migrations, [(OLD, NEW)])
        self.assertEqual(delivered, [-100777])
        self.assertEqual(len(done), 1)
        self.assertEqual(log.of('error'), [
            'Отбивка табло: чат %s не получил сообщение: Forbidden: bot was kicked from the supergroup chat' % OLD])

    def test_ordinary_failure_goes_the_old_way(self):
        db = FakeDb(chats=[{'chat_id': 5, 'is_enabled': True, 'mode': 'always'}])

        async def deliver(chat_id, text, media):
            raise RuntimeError('Forbidden: bot was kicked from the group chat')

        async def prepare():
            return {}, 'цифры', []

        ns = _namespace('_szov_broadcast_run_job', '_telegram_follow_migration', db=db,
                        _szov_broadcast_deliver=deliver)
        asyncio.run(ns['_szov_broadcast_run_job'](
            direction='osnova', label='Отбивка табло', prepare=prepare, deviations=lambda data: []))
        self.assertEqual(db.migrations, [])
        self.assertEqual(ns['logging'].of('error'), [
            'Отбивка табло: чат 5 не получил сообщение: Forbidden: bot was kicked from the group chat'])


class ChatReportJobTests(unittest.TestCase):

    def _run(self, subscriptions):
        delivered = []
        db = FakeDb(subscriptions=subscriptions, moved={'chat_hourly_subscriptions': 1})

        async def deliver(chat_id, data, caption, image):
            if int(chat_id) == OLD:
                raise Migrated(NEW)
            delivered.append(chat_id)

        async def prepare():
            return {}, 'подпись', b'png'

        ns = _namespace('chat_hourly_broadcast_job', '_telegram_follow_migration', db=db,
                        _chat_hourly_deliver=deliver, _chat_hourly_prepare=prepare)
        asyncio.run(ns['chat_hourly_broadcast_job']())
        return db, delivered, ns['logging']

    def test_migrated_group_gets_the_report_and_the_new_id_is_marked_sent(self):
        db, delivered, log = self._run([{'chat_id': str(OLD)}, {'chat_id': '-100888'}])
        self.assertEqual(db.migrations, [(str(OLD), NEW)])
        self.assertEqual(delivered, [NEW, '-100888'])
        self.assertEqual(db.marked, [NEW, '-100888'])
        self.assertEqual(log.of('error'), [])

    def test_group_subscribed_under_both_ids_gets_one_report(self):
        db, delivered, _log = self._run([{'chat_id': str(OLD)}, {'chat_id': str(NEW)}])
        self.assertEqual(delivered, [str(NEW)])
        self.assertEqual(db.marked, [str(NEW)])


class JobsFollowTheMigrationTests(unittest.TestCase):
    """Каждая плановая рассылка по сохранённым чатам идёт за переездом группы."""

    def test_every_scheduled_sender_follows_the_migration(self):
        source = BOT_PATH.read_text(encoding='utf-8-sig')
        for name in ('_szov_broadcast_run_job', 'chat_hourly_broadcast_job', 'amo_leads_broadcast_job',
                     'front_office_calls_broadcast_job'):
            with self.subTest(name=name):
                node = source_cache.function_node(BOT_PATH, name)
                block = ast.get_source_segment(source, node)
                self.assertEqual(block.count('await _telegram_follow_migration('), 1)
                self.assertIn("getattr(exc, 'migrate_to_chat_id', None)", block)


class RecordingCursor:
    """Курсор, который помнит запросы и отдаёт заданное число затронутых строк."""

    def __init__(self, rowcounts):
        self.rowcounts = list(rowcounts)
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((' '.join(sql.split()), params))
        self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 0


class DatabaseMigrationTests(unittest.TestCase):
    """Порядок и смысл запросов; сами запросы на настоящем Postgres сверены при выкладке
    (12.11.2026: только старый id, оба id в одном направлении, реестр, повторный вызов)."""

    def _db(self, rowcounts):
        import time
        if not hasattr(time, 'tzset'):          # db/core.py зовёт tzset при импорте; на Windows его нет
            self.skipTest('db.core не импортируется на Windows')
        from db.it_tickets import ItTicketsMixin

        cursor = RecordingCursor(rowcounts)

        class Db(ItTicketsMixin):
            @contextmanager
            def _get_cursor(self):
                yield cursor

        return Db(), cursor

    def test_everything_the_portal_knows_about_the_chat_moves_in_one_transaction(self):
        #            отбивки: убрано, перенесено | лиды | чаты по часам | обзвон | реестр
        db, cursor = self._db([0, 1, 0, 0, 0, 1, 1, 0, 1, 0])
        moved = db.migrate_telegram_chat_id(str(OLD), str(NEW))
        self.assertEqual(moved, {'szov_wallboard_broadcast_chats': 1, 'chat_hourly_subscriptions': 1,
                                 'front_office_call_subscriptions': 1, 'it_ticket_channels': 1})
        tables = [sql.split(' WHERE ')[0] for sql, _params in cursor.executed]
        self.assertEqual(len(cursor.executed), 10)
        self.assertTrue(tables[0].startswith('DELETE FROM szov_wallboard_broadcast_chats AS stale USING'))
        self.assertEqual(tables[1], 'UPDATE szov_wallboard_broadcast_chats SET chat_id = %s')
        for index, table in enumerate(('amo_lead_subscriptions', 'chat_hourly_subscriptions',
                                       'front_office_call_subscriptions')):
            delete_sql, delete_params = cursor.executed[2 + index * 2]
            update_sql, update_params = cursor.executed[3 + index * 2]
            self.assertEqual(delete_sql, 'DELETE FROM %s WHERE chat_id = %%s AND EXISTS '
                                         '(SELECT 1 FROM %s WHERE chat_id = %%s)' % (table, table))
            self.assertEqual(delete_params, (str(OLD), str(NEW)), 'в подписках id записан текстом')
            self.assertEqual(update_sql, "UPDATE %s SET chat_id = %%s, chat_type = 'supergroup' "
                                         "WHERE chat_id = %%s" % table)
            self.assertEqual(update_params, (str(NEW), str(OLD)))
        self.assertEqual(cursor.executed[0][1], (OLD, NEW))
        self.assertEqual(cursor.executed[1][1], (NEW, OLD))
        # Реестр чатов бота: при двух строках старая выключается, а не удаляется — на неё
        # ссылаются отправленные IT-заявки.
        self.assertIn('SET is_active = FALSE', cursor.executed[8][0])
        self.assertNotIn('DELETE', cursor.executed[8][0])
        self.assertIn("SET chat_id = %s, chat_type = 'supergroup'", cursor.executed[9][0])
        self.assertIn('NOT EXISTS (SELECT 1 FROM it_ticket_channels WHERE chat_id = %s)', cursor.executed[9][0])

    def test_unknown_chat_and_same_id_change_nothing(self):
        db, cursor = self._db([])
        self.assertEqual(db.migrate_telegram_chat_id(OLD, NEW), {})
        self.assertEqual(len(cursor.executed), 10)
        db, cursor = self._db([])
        self.assertEqual(db.migrate_telegram_chat_id(NEW, NEW), {})
        self.assertEqual(cursor.executed, [])


if __name__ == '__main__':
    unittest.main()
