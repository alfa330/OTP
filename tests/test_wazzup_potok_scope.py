"""Группа «Поток» в «Чатах ОП» (владелец, 20.10.2026: «Поток» пишет только в канал
+77070253322, доступ — обычным QR от СВ, без смены).

Без приложения, базы и Wazzup: поддельная база и HTTP из tests/test_wazzup_pilot.py.
"""
import json
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from flask import Flask, jsonify

from tests.test_wazzup_pilot import MemoryDatabase, fixture, user
from wazzup import access, chat_list, chat_search, pilot, workspace_routes
from wazzup.realtime import EventBroker
from wazzup.unread import register_unread_routes

HELP = '6983db13-ae8e-4b27-b09d-6c10ae4f54cf'
OTHER = '24ea28f0-ba8a-4bb0-8c1e-576c0ce0db8e'
CHANNELS = [{'channelId': HELP, 'state': 'active', 'transport': 'wapi', 'name': 'Help Taxi'},
            {'channelId': OTHER, 'state': 'active', 'transport': 'whatsapp', 'name': 'Jana Taxi Бизнес'}]


def potok_access():
    return {'mode': access.MODE_POTOK, 'can_process': True, 'locked': False,
            'channels': access.POTOK_CHANNEL_IDS, 'user_id': 42}


class PotokChannelTests(unittest.TestCase):
    def test_potok_channel_is_the_help_taxi_number(self):
        self.assertEqual({HELP}, access.POTOK_CHANNEL_IDS)
        # Окно CRM (владелец, 23.10.2026) пишет в тот же номер — номер CRM «Потока».
        self.assertEqual({HELP}, access.CRM_CHANNEL_IDS)
        self.assertEqual({access.MODE_OPERATOR, access.MODE_POTOK, access.MODE_CRM}, access.GATED_MODES)
        # В показатели входят ответы операторов; окна CRM без своего права — нет.
        self.assertEqual({access.MODE_OPERATOR, access.MODE_POTOK}, access.CREDITED_MODES)


class PotokPilotTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(pilot.accounts, 'api_key', return_value='isolated-test-key')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.broker = EventBroker()
        self.app, self.db, _, self.transport = fixture(
            db=MemoryDatabase(user('operator')), broker=self.broker,
            channels=lambda account: CHANNELS, access=potok_access)
        self.client = self.app.test_client()

    def body(self, channel=HELP, **changes):
        result = dict(account='op', channelId=channel, chatId='77000000000', text='Здравствуйте',
                      clientMessageId=str(uuid.uuid4()))
        result.update(changes)
        return result

    def test_reply_in_own_channel_is_sent_without_a_shift_and_credited(self):
        # Смены у «Потока» нет: таблица статусов даже не читается.
        self.db.shift = ('перерыв', datetime(2026, 10, 20, 9, 0), 'wzws-x', None)
        response = self.client.post('/api/wazzup/pilot/send', json=self.body())
        self.assertEqual(201, response.status_code, response.get_json())
        self.transport.post.assert_called_once()
        self.assertFalse([sql for sql, _ in self.db.statements if 'operator_status_events' in sql])
        body = self.body()
        self.client.post('/api/wazzup/pilot/send', json=body)
        self.assertEqual('icore:42', self.db.outbox_authors[body['clientMessageId']])

    def test_other_channels_are_refused_before_anything_is_written(self):
        response = self.client.post('/api/wazzup/pilot/send', json=self.body(OTHER))
        self.assertEqual((403, 'CHANNEL_NOT_ALLOWED'), (response.status_code, response.get_json()['code']))
        refresh = self.client.post('/api/wazzup/pilot/refresh',
                                   json={'account': 'op', 'channelId': OTHER, 'chatId': '77000000000', 'messageIds': []})
        self.assertEqual(403, refresh.status_code)
        typing = self.client.post('/api/wazzup/pilot/typing', json={
            'account': 'op', 'channelId': OTHER, 'chatId': '77000000000', 'clientId': str(uuid.uuid4()),
            'typing': True, 'sequence': 1})
        self.assertEqual(403, typing.status_code)
        self.assertFalse(self.db.outbox)
        self.transport.post.assert_not_called()

    def test_plus_starts_chats_only_in_own_channel(self):
        data = self.client.get('/api/wazzup/pilot').get_json()
        self.assertTrue(data['enabled'])
        self.assertEqual([HELP], data['newChatChannelIds'])

    def test_stream_carries_only_own_channel(self):
        response = self.client.get('/api/wazzup/pilot/stream', buffered=False)
        frames = iter(response.response)
        self.assertIn(b'event: connected', next(frames))
        other = dict(account='op', channelId=OTHER, chatId='77000000000', messageId='m-other', status='read')
        own = dict(account='op', channelId=HELP, chatId='77000000000', messageId='m-own', status='read')
        self.broker.publish(other)
        frame = next(frames).decode()
        self.assertIn('event: cursor', frame)
        self.assertNotIn('m-other', frame)
        self.assertIn(f'id: {self.broker.current_seq()}', frame)
        self.broker.publish(other)
        self.broker.publish(own)
        frame = next(frames).decode()
        self.assertIn('event: change', frame)
        self.assertEqual([own], json.loads(frame.split('data: ', 1)[1])['changes'])
        response.close()


class RecordingDatabase:
    def __init__(self):
        self.statements = []

    @contextmanager
    def _get_cursor(self):
        database = self

        class Cursor:
            def execute(self, sql, params=()):
                database.statements.append((' '.join(sql.split()), params))

            def fetchall(self):
                return []

        yield Cursor()


class PotokUnreadTests(unittest.TestCase):
    def snapshot(self, scope):
        from flask import Blueprint
        db = RecordingDatabase()
        bp = Blueprint('unread_test', __name__, url_prefix='/api/wazzup/pilot')
        register_unread_routes(bp, lambda: (user(), None), lambda fn: fn, lambda: ('', 204), db,
                               channel_scope=lambda: scope)
        app = Flask(__name__)
        app.register_blueprint(bp)
        response = app.test_client().get('/api/wazzup/pilot/unread')
        self.assertEqual(200, response.status_code)
        return db.statements[0]

    def test_waiting_counter_counts_only_own_channel(self):
        sql, params = self.snapshot(access.POTOK_CHANNEL_IDS)
        self.assertIn('AND s.channel_id = ANY(%s)', sql)
        self.assertEqual(('', [HELP]), params)
        sql, params = self.snapshot(None)
        self.assertNotIn('ANY', sql)
        self.assertEqual(('',), params)


class PotokQueryTests(unittest.TestCase):
    def test_chat_list_and_channel_counts_take_the_scope(self):
        count, page = chat_list.chat_list_queries('op', None, '', 30, 0, None, channel_ids={HELP})
        self.assertIn('c.channel_id = ANY(%s)', count[0])
        self.assertEqual(['op', [HELP]], count[1])
        self.assertEqual(['op', [HELP], 30, 0], page[1])
        executed = []

        class Cursor:
            def execute(self, sql, params):
                executed.append((sql, params))

            def fetchall(self):
                return [('77000000000', 1)]

        items = [{'chatId': '77000000000'}]
        chat_list.attach_channel_counts(Cursor(), 'op', items, channel_ids={HELP})
        self.assertEqual(('op', ['77000000000'], [HELP]), executed[0][1])
        self.assertEqual(1, items[0]['channelsCount'])

    def test_message_search_takes_the_scope(self):
        sql, params = chat_search.recent_hits_query('op', 'регистрация', channel_ids={HELP})
        self.assertIn('WHERE m.account = %s AND m.channel_id = ANY(%s)', ' '.join(sql.split()))
        self.assertEqual(['op', [HELP]], params[:2])
        sql, params = chat_search.message_hits_query('op', 'регистрация', channel_ids={HELP})
        self.assertIn('AND m.channel_id = ANY(%s)', sql)
        self.assertEqual([HELP], params[2])
        unscoped, _ = chat_search.recent_hits_query('op', 'регистрация')
        self.assertNotIn('ANY', unscoped)


class PotokWorkspaceTests(unittest.TestCase):
    def app(self, state):
        bp = workspace_routes.build_wazzup_workspace_blueprint(
            db=None, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            chat_access=lambda: state, current_session_id=lambda: '00000000-0000-4000-8000-000000000001',
            approver_context=None, approval_perimeter_error=None, send_telegram=None, secret='s',
            sales_department_id=367, portal_qr=lambda session, user_id: ('OTPQ:test', datetime(2030, 1, 1)),
            store=type('Store', (), {'session_is_live': lambda self, session, user_id: True})())
        app = Flask(__name__)
        app.register_blueprint(bp)
        return app.test_client()

    def test_lock_screen_qr_is_shown_to_potok_but_shift_is_not_theirs(self):
        client = self.app(dict(potok_access(), locked=True, can_process=False))
        state = client.get('/api/wazzup/workspace').get_json()
        self.assertEqual(('potok', True, None), (state['mode'], state['locked'], state['shift']))
        qr = client.post('/api/wazzup/workspace/qr')
        self.assertEqual(200, qr.status_code)
        self.assertEqual('OTPQ:test', qr.get_json()['qr_payload'])
        self.assertEqual(403, client.post('/api/wazzup/workspace/status', json={'status_key': 'готов'}).status_code)
        self.assertEqual(403, client.post('/api/wazzup/workspace/heartbeat', json={}).status_code)


if __name__ == '__main__':
    unittest.main()
