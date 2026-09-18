"""Вебхуки Chat2Desk: приём событий и сборка из них обращений для табло.

Зачем эти тесты. Табло СЗоВ «Чат» перестало выкачивать сутки опросом и берёт обращения из
событий, которые вендор присылает сам. Значит показатели табло теперь считаем МЫ, а не он, и
любая ошибка в правилах счёта видна не как поломка, а как чуть другая цифра на стене. Поэтому
правила закреплены тестами по одному: время первого ответа, ответ внутри чата, второе
сообщение оператора подряд, автоопрос оценки, ночной чат на стыке суток.

Функции достаём из bot_schedule2.py через ast — импортировать модуль нельзя, он на старте
поднимает пул к боевой БД (тот же приём, что в test_chat_hourly_report.py).
"""
import ast
import copy
import json
import logging
import os
import re
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
DB_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")

NAMES = {
    '_env_int', '_chat_metrics_parse_number', '_chat_metrics_parse_date',
    '_chat2desk_sync_timezone', '_chat2desk_parse_datetime',
    '_chat2desk_webhook_event_key', '_chat2desk_webhook_events',
    'build_chat_webhook_request_rows', '_chat2desk_webhook_day_rows',
    '_chat2desk_webhook_operator_names', '_chat2desk_webhook_rows_cache',
    '_chat2desk_webhook_names_cache', '_CHAT2DESK_WEBHOOK_NAMES_TTL_SECONDS',
    'CHAT2DESK_WEBHOOK_STALE_SECONDS', 'CHAT2DESK_WEBHOOK_ROWS_TTL_SECONDS',
    '_chat_hourly_number', '_chat_hourly_is_open', '_chat_hourly_request_start',
    '_chat_hourly_response_sums', '_chat_hourly_operator_name',
    '_chat2desk_row_first',
    'CHAT_HOURLY_REQUEST_TYPE', 'CHAT2DESK_WEBHOOK_HOOK_TYPES',
    'CHAT2DESK_WEBHOOK_CLIENT_TYPE', 'CHAT2DESK_WEBHOOK_OPERATOR_TYPE',
    'CHAT2DESK_WEBHOOK_COMMON_REQUEST_TYPE',
    'chat2desk_webhook_signature', 'chat2desk_webhook_signature_ok',
    'chat2desk_webhook_status_name', 'build_chat_webhook_status_rows',
    '_chat2desk_webhook_roster', '_chat2desk_operator_display_name',
    '_szov_chat_wallboard_status', '_szov_chat_wallboard_timelines',
    '_szov_chat_wallboard_day_seconds', '_szov_chat_wallboard_resolve',
    '_SZOV_CHAT_WALLBOARD_STATUSES', '_SZOV_CHAT_WALLBOARD_OTHER_KEY',
    '_szov_chat_wallboard_events_cache',
    'CHAT2DESK_SYNC_TIMEZONE',
}

ALMATY = ZoneInfo('Asia/Almaty')


def _function_source(path, name, class_name=None):
    """Текст функции как есть — для проверок вида «правило записано именно так»."""
    node = source_cache.function_node(path, name, class_name=class_name)
    return ast.get_source_segment(source_cache.read(path), node) or ''


def _namespace():
    tree = source_cache.parse(BOT_SOURCE)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            body.append(node)
        elif isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & NAMES:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {
        'os': os, 're': re, 'time': time, 'logging': logging, 'threading': threading,
        'hmac': __import__('hmac'), 'hashlib': __import__('hashlib'),
        'datetime': datetime, 'timedelta': timedelta, 'date': date, 'ZoneInfo': ZoneInfo,
        '_chat_report_parse_dt': lambda text: None,
        # Сопоставление имени учётки с сотрудником OTP — отдельный кусок монолита со
        # своей базой; здесь достаточно «нашёлся кто-то один».
        '_status_import_resolve_operator_matches': lambda name, lookup: (
            [{'id': 1, 'name': name}] if name in (lookup or {}) else []),
        # Разбор даты дня в монолите опирается на общий парсер импорта статусов —
        # для этих тестов достаточно ISO-формата, в котором день и приходит.
        '_status_import_parse_datetime': lambda value, default_date=None: (
            datetime.strptime(str(value)[:10], '%Y-%m-%d') if value else None),
    }
    exec(compile(module, "<c2d-webhook>", "exec"), ns)
    missing = sorted(name for name in NAMES if name not in ns)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return ns


NS = _namespace()


def _event(hook_type, event_time, **extra):
    """Тело вебхука в том виде, в каком его шлёт вендор (мануал 1.58, раздел webhooks POST)."""
    payload = {'hook_type': hook_type, 'event_time': event_time}
    payload.update(extra)
    return payload


def _stored(hook_type, at, request_id=1, message_type=None, operator_id=None, **extra):
    """Строка, какой она лежит в c2d_webhook_events (время — стенные часы Алматы)."""
    row = {
        'hook_type': hook_type, 'event_at': at, 'day': at.date(),
        'request_id': request_id, 'message_type': message_type,
        'c2d_operator_id': operator_id, 'channel_id': 2137, 'transport': 'wa_dialog',
    }
    row.update(extra)
    return row


class WebhookNormalizeTests(unittest.TestCase):
    """Тело вендора -> строка базы."""

    def test_utc_event_time_becomes_almaty_wall_clock(self):
        """Вендор шлёт UTC, а весь чатовый контур живёт в часах Алматы."""
        rows = NS['_chat2desk_webhook_events'](_event(
            'inbox', '2026-09-18T07:59:37Z', message_id=110549894, request_id=8705736,
            client_id=10317391, operator_id=39731, dialog_id=4195793, channel_id=17313,
            transport='telegram', type='from_client', is_new_request=True))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['event_at'], datetime(2026, 9, 18, 12, 59, 37))
        self.assertEqual(row['day'], date(2026, 9, 18))
        self.assertEqual(row['message_id'], 110549894)
        self.assertEqual(row['request_id'], 8705736)
        self.assertEqual(row['message_type'], 'from_client')
        self.assertTrue(row['is_new_request'])
        self.assertEqual(row['payload']['hook_type'], 'inbox')

    def test_repeat_delivery_has_the_same_key(self):
        """Вендор повторяет неуспешную доставку трижды — ключ обязан совпасть."""
        body = _event('inbox', '2026-09-18T07:59:37Z', message_id=42, request_id=7,
                      type='from_client')
        first = NS['_chat2desk_webhook_events'](body)[0]
        second = NS['_chat2desk_webhook_events'](dict(body))[0]
        self.assertEqual(first['event_key'], second['event_key'])
        self.assertEqual(first['event_key'], 'inbox:42')

    def test_event_without_message_id_keys_by_request_and_time(self):
        """У закрытия обращения id сообщения нет — ключ собирается из обращения и времени."""
        row = NS['_chat2desk_webhook_events'](_event(
            'close_request', '2026-09-18T07:59:37Z', request_id=7))[0]
        self.assertEqual(row['event_key'], 'close_request:7:2026-09-18 12:59:37')

    def test_unknown_hook_types_and_broken_bodies_are_dropped(self):
        """Лишние события вендора в базу не пускаем, а на мусоре не падаем."""
        rows = NS['_chat2desk_webhook_events']([
            _event('new_qr_code', '2026-09-18T07:59:37Z', message_id=1),
            _event('inbox', '', message_id=2, request_id=7, type='from_client'),
            'не словарь',
            _event('inbox', '2026-09-18T08:00:00Z', message_id=3, request_id=7,
                   type='from_client'),
        ])
        self.assertEqual([row['message_id'] for row in rows], [3])


class WebhookSignatureTests(unittest.TestCase):
    """Подпись доставки: алгоритм сверен с контрольным примером вендора."""

    # Пример из документации Postman, раздел Webhook Signature Verification.
    BODY = ('{"message_id":28780,"type":"from_client","text":"test7","transport":"widget",'
            '"client_id":12568,"operator_id":null,"dialog_id":942,"channel_id":1,'
            '"photo":null,"coordinates":null,"audio":null,"pdf":null,'
            '"client":{"id":12568,"phone":"[chat] 1d0bb2e221762cfcc05c",'
            '"client_phone":null,"name":"[chat] 1d0bb2e221762cfcc05c",'
            '"assigned_name":null,"external_id":null},"hook_type":"inbox",'
            '"request_id":5357,"attachments":[],"is_new_request":false,'
            '"is_new_client":false,"insta_comment":false,"extra_data":{},'
            '"is_new":true,"event_time":"2025-04-03T11:50:06.984494Z"}')
    TIMESTAMP = "1743681006"
    SIGNATURE = "88b574d8237e1b7740f88102018b734d3dd5bc6803c509a3694eff9721b0c595"

    def test_algorithm_matches_the_vendor_example(self):
        """Тело + метка времени, HMAC-SHA256, hex — ровно как в примере вендора."""
        self.assertEqual(
            NS['chat2desk_webhook_signature'](self.BODY, self.TIMESTAMP, "md5_api_token"),
            self.SIGNATURE)

    def test_both_forms_of_the_md5_key_are_accepted(self):
        """Вид MD5 (hex или сырые байты) вендор не уточняет — принимаем оба."""
        import hashlib as _hashlib
        token = 'токен'
        digest = _hashlib.md5(token.encode('utf-8'))
        for key in (digest.hexdigest(), digest.digest()):
            signature = NS['chat2desk_webhook_signature'](self.BODY, self.TIMESTAMP, key)
            self.assertTrue(NS['chat2desk_webhook_signature_ok'](
                self.BODY, self.TIMESTAMP, signature, token))

    def test_tampered_body_does_not_pass(self):
        signature = NS['chat2desk_webhook_signature'](self.BODY, self.TIMESTAMP, 'ключ')
        self.assertFalse(NS['chat2desk_webhook_signature_ok'](
            self.BODY + ' ', self.TIMESTAMP, signature, 'ключ'))

    def test_missing_parts_do_not_pass(self):
        self.assertFalse(NS['chat2desk_webhook_signature_ok'](self.BODY, None, 'x', 'токен'))
        self.assertFalse(NS['chat2desk_webhook_signature_ok'](self.BODY, '1', '', 'токен'))


class WebhookNewEventTypesTests(unittest.TestCase):
    """События, которых нет в PDF-мануале, но которые есть у вендора живьём."""

    def test_new_request_carries_the_request_type(self):
        """Тип обращения приходит только здесь — и снимает нужду гадать по ленте."""
        row = NS['_chat2desk_webhook_events'](_event(
            'new_request', '2026-09-18T07:00:00Z', request_id=8891565, client_id=1,
            dialog_id=2, channel_id=18337, transport='telegram', type='common',
            by_client=True))[0]
        self.assertEqual(row['request_type'], 'common')
        self.assertIsNone(row['message_type'])
        self.assertEqual(row['event_key'], 'new_request:8891565:2026-09-18 12:00:00')

    def test_operator_status_event_keys_by_operator(self):
        """У статуса нет ни сообщения, ни обращения: оператор здесь — поле id."""
        row = NS['_chat2desk_webhook_events'](_event(
            'operator_status_updated', '2026-09-18T07:00:00Z', id='2325532',
            email='operator@mail.com', online=0, offline_type='busy', status_id=1))[0]
        self.assertEqual(row['c2d_operator_id'], 2325532)
        self.assertEqual(row['message_type'], 'busy')
        self.assertIsNone(row['request_id'])
        self.assertEqual(row['event_key'], 'operator_status_updated:op2325532:2026-09-18 12:00:00')

    def test_comment_and_imported_message_are_accepted(self):
        """Заметку показывает лента «Чатов водителей», а imported_message не поднимает inbox."""
        rows = NS['_chat2desk_webhook_events']([
            _event('comment', '2026-09-18T07:00:00Z', message_id=1, request_id=7,
                   type='comment', operator_id=40818),
            _event('imported_message', '2026-09-18T07:01:00Z', message_id=2, request_id=7,
                   type='to_client', operator_id=40818),
            _event('system_message', '2026-09-18T07:02:00Z', message_id=3, request_id=7,
                   type='system'),
        ])
        self.assertEqual([row['hook_type'] for row in rows],
                         ['comment', 'imported_message', 'system_message'])

    def test_rating_request_is_dropped_by_its_vendor_type(self):
        """Автоопрос отсеивается типом от вендора, даже если в ленте есть сообщения."""
        start = datetime(2026, 9, 18, 19, 12, 0)
        rows = NS['build_chat_webhook_request_rows']([
            _stored('new_request', start, request_id=9, request_type='rating'),
            _stored('inbox', start + timedelta(seconds=5), request_id=9,
                    message_type='from_client'),
            _stored('new_request', start, request_id=10, request_type='common'),
            _stored('inbox', start + timedelta(seconds=5), request_id=10,
                    message_type='from_client'),
        ], '2026-09-18', operator_names={})
        self.assertEqual([row['request_id'] for row in rows], [10])

    def test_common_request_without_messages_still_counts(self):
        """Тип сказал вендор — чат без единого ответа всё равно чат, он просто висит."""
        start = datetime(2026, 9, 18, 3, 0, 0)
        rows = NS['build_chat_webhook_request_rows'](
            [_stored('new_request', start, request_id=11, request_type='common')],
            '2026-09-18', operator_names={})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['reaction_time'])


class WebhookRequestRowsTests(unittest.TestCase):
    """Лента событий -> обращения в форме request_stats."""

    def _rows(self, events, day='2026-09-18', names=None):
        return NS['build_chat_webhook_request_rows'](events, day, operator_names=names or {})

    def test_first_reply_and_inner_reply_follow_the_vendor_rule(self):
        """Реакция — до первого ответа; «внутри чата» — среднее по ответам, первый включён."""
        start = datetime(2026, 9, 18, 10, 0, 0)
        rows = self._rows([
            _stored('inbox', start, message_type='from_client'),
            _stored('outbox', start + timedelta(seconds=30), message_type='to_client',
                    operator_id=39731),
            _stored('inbox', start + timedelta(seconds=60), message_type='from_client'),
            _stored('outbox', start + timedelta(seconds=150), message_type='to_client',
                    operator_id=39731),
        ], names={39731: 'Айман Абдрахманова'})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['reaction_time'], 30)
        self.assertEqual(row['replies'], 2)
        self.assertEqual(row['total_replies_time'], 120)
        self.assertEqual(row['average_replies_time'], 60)
        self.assertEqual(row['operator_name'], 'Айман Абдрахманова')
        self.assertEqual(row['incoming_messages'], 2)
        self.assertEqual(row['outgoing_messages'], 2)

    def test_second_operator_message_in_a_row_is_not_a_reply(self):
        """Оператор дописал вдогонку — отвечать в этот момент не на что."""
        start = datetime(2026, 9, 18, 10, 0, 0)
        row = self._rows([
            _stored('inbox', start, message_type='from_client'),
            _stored('outbox', start + timedelta(seconds=20), message_type='to_client',
                    operator_id=1),
            _stored('outbox', start + timedelta(seconds=25), message_type='to_client',
                    operator_id=1),
        ])[0]
        self.assertEqual(row['replies'], 1)
        self.assertEqual(row['average_replies_time'], 20)
        self.assertEqual(row['outgoing_messages'], 2)

    def test_autoreply_and_system_messages_do_not_count(self):
        """В счётчиках вендора их нет — значит и во времени ответа быть не должно."""
        start = datetime(2026, 9, 18, 10, 0, 0)
        row = self._rows([
            _stored('inbox', start, message_type='from_client'),
            _stored('outbox', start + timedelta(seconds=1), message_type='autoreply'),
            _stored('outbox', start + timedelta(seconds=2), message_type='system'),
            _stored('outbox', start + timedelta(seconds=90), message_type='to_client',
                    operator_id=1),
        ])[0]
        self.assertEqual(row['reaction_time'], 90)
        self.assertEqual(row['replies'], 1)

    def test_rating_survey_request_is_not_a_chat(self):
        """Автоопрос оценки: в ленте ни одного сообщения клиента или оператора."""
        start = datetime(2026, 9, 18, 19, 12, 0)
        rows = self._rows([
            _stored('outbox', start, request_id=76019573, message_type='autoreply'),
            _stored('close_request', start + timedelta(minutes=18), request_id=76019573),
        ])
        self.assertEqual(rows, [])

    def test_chat_started_yesterday_stays_in_yesterday(self):
        """Сутки обращения — по первому событию, как request_start у вендора."""
        night = datetime(2026, 9, 17, 23, 50, 0)
        rows = self._rows([
            _stored('inbox', night, request_id=5, message_type='from_client'),
            _stored('outbox', night + timedelta(minutes=20), request_id=5,
                    message_type='to_client', operator_id=1),
            _stored('inbox', datetime(2026, 9, 18, 0, 30), request_id=6,
                    message_type='from_client'),
        ])
        self.assertEqual([row['request_id'] for row in rows], [6])

    def test_open_chat_has_no_end_and_closed_one_has(self):
        """«Открыт» на табло — это пустой request_end, как в отчёте вендора."""
        start = datetime(2026, 9, 18, 10, 0, 0)
        rows = self._rows([
            _stored('inbox', start, request_id=1, message_type='from_client'),
            _stored('inbox', start, request_id=2, message_type='from_client'),
            _stored('close_request', start + timedelta(minutes=5), request_id=2),
        ])
        by_id = {row['request_id']: row for row in rows}
        self.assertEqual(by_id[1]['request_end'], '')
        self.assertTrue(NS['_chat_hourly_is_open'](by_id[1]))
        self.assertEqual(by_id[2]['request_end'], '2026-09-18 10:05:00')
        self.assertFalse(NS['_chat_hourly_is_open'](by_id[2]))

    def test_transferred_dialog_keeps_the_operator_name(self):
        """Чат передали — без dialog_transferred у него не было бы имени вовсе."""
        start = datetime(2026, 9, 18, 10, 0, 0)
        row = self._rows([
            _stored('inbox', start, message_type='from_client'),
            _stored('dialog_transferred', start + timedelta(seconds=5), operator_id=77),
        ], names={77: 'Темирлан Ерланов'})[0]
        self.assertEqual(row['operator_name'], 'Темирлан Ерланов')
        self.assertIsNone(row['reaction_time'])
        self.assertEqual(row['replies'], 0)
        self.assertIsNone(row['average_replies_time'])

    def test_rows_feed_the_same_metric_functions_as_the_vendor_report(self):
        """Форма строки — вендорская: показатели считает та же функция, что и раньше."""
        start = datetime(2026, 9, 18, 10, 0, 0)
        rows = self._rows([
            _stored('inbox', start, request_id=1, message_type='from_client'),
            _stored('outbox', start + timedelta(seconds=10), request_id=1,
                    message_type='to_client', operator_id=1),
            _stored('inbox', start, request_id=2, message_type='from_client'),
            _stored('outbox', start + timedelta(seconds=50), request_id=2,
                    message_type='to_client', operator_id=1),
        ])
        first_sum, first_count, inner_sum, inner_count = NS['_chat_hourly_response_sums'](rows)
        self.assertEqual((first_sum, first_count), (60, 2))
        self.assertEqual((inner_sum, inner_count), (60, 2))
        self.assertEqual(rows[0]['request_type'], NS['CHAT_HOURLY_REQUEST_TYPE'])


class WebhookReadinessTests(unittest.TestCase):
    """Когда табло верит своей базе, а когда возвращается к опросу вендора."""

    class _Db:
        def __init__(self, first_at, last_at, events):
            self.health = {'first_event_at': first_at, 'last_event_at': last_at,
                           'events_today': len(events)}
            self.events = events
            self.reads = 0

        def get_c2d_webhook_health(self):
            return self.health

        def get_c2d_webhook_events(self, day_from, day_to):
            self.reads += 1
            return self.events

        def get_c2d_operator_names(self, days=30):
            return {39731: 'Айман Абдрахманова'}

    def _run(self, first_at, last_at, events=(), day=None):
        ns = _namespace()
        ns['db'] = self._Db(first_at, last_at, list(events))
        ns['_chat2desk_webhook_rows_cache'].update(day=None, ts=0.0, rows=None, ready=False)
        ns['_chat2desk_webhook_names_cache'].update(ts=0.0, names=None)
        today = day or datetime.now(ALMATY).replace(tzinfo=None).date()
        rows, ready = ns['_chat2desk_webhook_day_rows'](today.strftime('%Y-%m-%d'))
        return rows, ready, ns['db']

    def _today_at(self, hour, minute=0):
        today = datetime.now(ALMATY).replace(tzinfo=None).date()
        return datetime(today.year, today.month, today.day, hour, minute)

    def test_live_stream_started_before_the_day_is_trusted(self):
        """Поток идёт и охватывает сутки целиком — табло читает базу и никуда не ходит."""
        now = datetime.now(ALMATY).replace(tzinfo=None)
        start = self._today_at(9)
        rows, ready, db = self._run(
            now - timedelta(days=3), now - timedelta(minutes=2),
            [_stored('inbox', start, message_type='from_client'),
             _stored('outbox', start + timedelta(seconds=15), message_type='to_client',
                     operator_id=39731)])
        self.assertTrue(ready)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reaction_time'], 15)
        self.assertEqual(rows[0]['operator_name'], 'Айман Абдрахманова')

    def test_stream_that_went_quiet_is_not_trusted(self):
        """Вендор отключает вебхук после пяти часов отказов — молчание читается как сбой."""
        now = datetime.now(ALMATY).replace(tzinfo=None)
        rows, ready, db = self._run(now - timedelta(days=3), now - timedelta(hours=3))
        self.assertFalse(ready)
        self.assertEqual(rows, [])
        self.assertEqual(db.reads, 0)

    def test_first_day_of_the_stream_is_not_trusted(self):
        """Вебхук включили днём — утро этих суток в поток не попало, объём был бы занижен."""
        now = datetime.now(ALMATY).replace(tzinfo=None)
        rows, ready, db = self._run(self._today_at(11), now - timedelta(minutes=1))
        self.assertFalse(ready)
        self.assertEqual(db.reads, 0)

    def test_empty_stream_is_not_trusted(self):
        """Ни одного события — это «вебхук ещё не включён», а не «чатов не было»."""
        rows, ready, db = self._run(None, None)
        self.assertFalse(ready)


class WebhookStatusRowsTests(unittest.TestCase):
    """Статусы чатников: события вендора -> лента, как из отчёта operator_events."""

    ROSTER = [
        {'id': 39731, 'first_name': 'Айман', 'last_name': 'Абдрахманова',
         'status': 'enabled', 'role': 'operator'},
        {'id': 55, 'first_name': 'Админ', 'last_name': '', 'status': 'enabled',
         'role': 'admin'},
    ]

    def _status(self, operator_id, at, online=1, offline_type=None):
        return {'c2d_operator_id': operator_id, 'event_at': at,
                'online': online, 'offline_type': offline_type}

    def test_status_name_matches_the_vendor_report_wording(self):
        """Имя статуса дальше разбирает общий парсер — отдаём его же слова."""
        name = NS['chat2desk_webhook_status_name']
        self.assertEqual(name(1, None), 'online')
        self.assertEqual(name(1, 'busy'), 'busy')
        self.assertEqual(name(1, 'tech break'), 'tech break')
        self.assertEqual(name(0, 'busy'), 'logout')
        self.assertEqual(name(0, None), 'logout')

    def test_carried_status_opens_the_day_at_midnight(self):
        """Заступивший вчера вечером не должен выглядеть сегодня «не в системе»."""
        rows = NS['build_chat_webhook_status_rows'](
            [self._status(39731, datetime(2026, 9, 17, 22, 0))],
            [self._status(39731, datetime(2026, 9, 18, 9, 30), online=1,
                          offline_type='break')],
            '2026-09-18', roster=NS['_chat2desk_webhook_roster'](self.ROSTER))
        self.assertEqual([(row['created_at'], row['event']) for row in rows], [
            ('2026-09-18 00:00:00', 'online'),
            ('2026-09-18 09:30:00', 'break'),
        ])
        self.assertEqual(rows[0]['operator_name'], 'Айман Абдрахманова')

    def test_operator_missing_from_the_roster_is_dropped(self):
        """Имени взять негде, а безымянную ленту всё равно не с кем сопоставить."""
        rows = NS['build_chat_webhook_status_rows'](
            [], [self._status(999999, datetime(2026, 9, 18, 9, 0))],
            '2026-09-18', roster=NS['_chat2desk_webhook_roster'](self.ROSTER))
        self.assertEqual(rows, [])

    def test_role_travels_with_the_row(self):
        """Админская учётка чатов не ведёт — её отсеивает сборщик лент по роли."""
        rows = NS['build_chat_webhook_status_rows'](
            [], [self._status(55, datetime(2026, 9, 18, 9, 0))],
            '2026-09-18', roster=NS['_chat2desk_webhook_roster'](self.ROSTER))
        self.assertEqual(rows[0]['operator_role'], 'admin')

    def test_rows_build_the_same_timeline_as_the_vendor_report(self):
        """Ради этого форма и вендорская: ленты строит та же функция, что и раньше."""
        NS['_szov_chat_wallboard_events_cache'].update(truncated=False)
        rows = NS['build_chat_webhook_status_rows'](
            [self._status(39731, datetime(2026, 9, 17, 23, 0))],
            [self._status(39731, datetime(2026, 9, 18, 12, 0), offline_type='break'),
             self._status(39731, datetime(2026, 9, 18, 12, 30)),
             self._status(55, datetime(2026, 9, 18, 9, 0))],
            '2026-09-18', roster=NS['_chat2desk_webhook_roster'](self.ROSTER))
        timelines, unmatched = NS['_szov_chat_wallboard_timelines'](
            rows, {'Айман Абдрахманова': 1}, truncated=False)
        self.assertEqual(list(timelines), ['Айман Абдрахманова'])
        self.assertEqual([(entry[0], entry[1]) for entry in timelines['Айман Абдрахманова']],
                         [(0, 'online'), (43200, 'break'), (45000, 'online')])
        self.assertEqual(unmatched, [])


class WebhookRouteTests(unittest.TestCase):
    """Ручка приёма: секрет в пути, тонкий обработчик, честный код ответа."""

    def setUp(self):
        node = source_cache.function_copy(ROOT / "bot_schedule2.py", "chat2desk_webhook")
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        self.saved = []
        self.responses = []

        class _Db:
            def __init__(self, sink):
                self.sink = sink
                self.fail = False

            def save_c2d_webhook_events(self, events):
                if self.fail:
                    raise RuntimeError("база недоступна")
                self.sink.extend(events)
                return len(events)

        class _Request:
            headers = {}

            def __init__(self, payload):
                self.payload = payload

            def get_json(self, silent=False):
                return self.payload

        self.db = _Db(self.saved)
        self.Request = _Request
        import hmac as hmac_module
        self.ns = {
            'hmac': hmac_module, 'logging': logging,
            'jsonify': lambda payload: payload,
            'db': self.db,
            'CHAT2DESK_WEBHOOK_TOKEN': 'секрет',
            '_chat2desk_webhook_events': NS['_chat2desk_webhook_events'],
            '_chat2desk_webhook_headers_logged': True,
        }
        self.module = module

    def _call(self, token, payload):
        self.ns['request'] = self.Request(payload)
        exec(compile(self.module, "<c2d-webhook-route>", "exec"), self.ns)
        return self.ns['chat2desk_webhook'](token)

    def test_wrong_token_is_not_found(self):
        """Секрет в пути — единственная аутентификация, и промах не должен намекать на ручку."""
        payload, status = self._call('другой', _event('inbox', '2026-09-18T07:00:00Z',
                                                      message_id=1, request_id=1))
        self.assertEqual(status, 404)
        self.assertEqual(self.saved, [])

    def test_valid_delivery_is_stored(self):
        payload, status = self._call('секрет', _event(
            'inbox', '2026-09-18T07:00:00Z', message_id=1, request_id=1, type='from_client'))
        self.assertEqual(status, 200)
        self.assertEqual(payload['stored'], 1)
        self.assertEqual(self.saved[0]['request_id'], 1)

    def test_broken_body_is_a_bad_request(self):
        payload, status = self._call('секрет', None)
        self.assertEqual(status, 400)

    def test_write_failure_answers_500_so_the_vendor_retries(self):
        """Вендор повторит доставку трижды, а запись идемпотентна — глотать событие нельзя."""
        self.db.fail = True
        payload, status = self._call('секрет', _event(
            'inbox', '2026-09-18T07:00:00Z', message_id=1, request_id=1, type='from_client'))
        self.assertEqual(status, 500)


class WebhookStorageTests(unittest.TestCase):
    """Схема и ретеншн: неделя — решение владельца 18.09.2026."""

    def test_table_and_indexes_are_created(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS c2d_webhook_events', DB_SOURCE)
        self.assertIn('idx_c2d_webhook_events_day', DB_SOURCE)
        self.assertIn('event_key TEXT NOT NULL UNIQUE', DB_SOURCE)

    def test_save_is_idempotent_by_event_key(self):
        source = _function_source(ROOT / "database.py", "save_c2d_webhook_events",
                                  class_name='Database')
        self.assertIn('ON CONFLICT (event_key) DO NOTHING', source)

    def test_retention_is_one_week(self):
        source = _function_source(ROOT / "database.py", "cleanup_c2d_eval_data",
                                  class_name='Database')
        self.assertIn('webhook_days=7', source)
        self.assertIn('DELETE FROM c2d_webhook_events WHERE day < CURRENT_DATE - %s', source)

    def test_wallboard_prefers_webhooks_and_falls_back_to_polling(self):
        source = _function_source(ROOT / "bot_schedule2.py",
                                  "_szov_chat_wallboard_day_requests")
        self.assertIn('_chat2desk_webhook_day_rows(day_str)', source)
        self.assertIn('_chat_hourly_fetch_requests(day_str)', source)


if __name__ == '__main__':
    unittest.main()
