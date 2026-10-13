"""Status checks cannot become sends, including lost/missing first requests."""
import unittest
import uuid
from unittest.mock import patch

from tests.test_wazzup_pilot import CHANNEL, MemoryDatabase, fixture, user


class SendCheckRoutesTests(unittest.TestCase):
    def setUp(self):
        self.app, self.db, _, self.transport = fixture()
        self.client = self.app.test_client()
        self.body = dict(account='op', channelId=CHANNEL, chatId='77000000000',
                         clientMessageId=str(uuid.uuid4()))

    def test_check_reads_as_actor_and_does_not_require_send_key_or_active_channel(self):
        with patch('wazzup.send_check.inspect_send', return_value={
                'state': 'unknown', 'error': 'Not confirmed', 'candidates': []}) as inspect, \
                patch('wazzup.pilot.accounts.api_key', return_value=''):
            response = self.client.post('/api/wazzup/pilot/send/check', json=self.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['clientMessageId'], self.body['clientMessageId'])
        self.assertIn('checkedAt', response.json)
        self.assertEqual(inspect.call_args.args[1:],
                         (self.body['clientMessageId'], 42, CHANNEL, self.body['chatId']))
        self.transport.post.assert_not_called()
        self.assertFalse(self.db.outbox)

    def test_check_enforces_processing_access_and_section_guard(self):
        for kwargs in ({'guard_denied': True}, {'db': MemoryDatabase(user('sv'))},
                       {'access': lambda: {'can_process': False}}):
            app, db, _, transport = fixture(**kwargs)
            self.assertEqual(app.test_client().post('/api/wazzup/pilot/send/check',
                             json=self.body).status_code, 403)
            transport.post.assert_not_called()
            self.assertFalse(db.statements)

    def test_invalid_input_and_excluded_channels_never_read_or_send(self):
        bodies = [[], {}, dict(self.body, account='potok'), dict(self.body, channelId=None),
                  dict(self.body, clientMessageId='not-an-id'), dict(self.body, chatId=[]),
                  dict(self.body, chatId='x' * 101)]
        for body in bodies:
            self.assertEqual(self.client.post('/api/wazzup/pilot/send/check', json=body).status_code, 400)
        self.assertEqual(self.client.post('/api/wazzup/pilot/send/check', json=dict(
            self.body, channelId='99df6893-fb6b-4e1d-a78e-9e6e6b37abb2')).status_code, 403)
        self.assertFalse(self.db.statements)
        self.transport.post.assert_not_called()

    def test_options_needs_no_actor_or_database(self):
        self.assertLess(self.client.options('/api/wazzup/pilot/send/check').status_code, 300)
        self.assertFalse(self.db.statements)
        self.transport.post.assert_not_called()
