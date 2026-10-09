"""Real query/result integration on SQLite; no external connections or messages."""
import json
import sqlite3
import threading
import unittest

from wazzup.realtime import EventBroker, HYDRATE_BATCH_SIZE, broadcast_changes
from wazzup.pilot import MESSAGE_SELECT, message_item


class Archive:
    def __init__(self):
        self.connection = sqlite3.connect(':memory:')
        self.calls = []
        self.connection.executescript('''
            CREATE TABLE wazzup_messages (
                account TEXT, channel_id TEXT, chat_id TEXT, message_id TEXT, dt TEXT,
                is_echo BOOLEAN, type TEXT, text TEXT, content_uri TEXT, author_name TEXT,
                author_id TEXT, status TEXT, is_edited BOOLEAN, is_deleted BOOLEAN, wazzup_dt TEXT
            );
            CREATE TABLE wazzup_pilot_outbox (account TEXT, message_id TEXT, author_name TEXT, reply_to_message_id TEXT, request_id TEXT);
            CREATE TABLE wazzup_chats (
                account TEXT, channel_id TEXT, chat_id TEXT, chat_type TEXT, contact_name TEXT,
                contact_phone TEXT, last_message_at TEXT, last_message_text TEXT,
                last_message_is_echo BOOLEAN, messages_count INTEGER, inbound_count INTEGER,
                outbound_count INTEGER
            );
        ''')

    def add(self, message_id, *, account='op', status='read', text='Complete message', author=None,
            channel='channel', chat='chat', deleted=False, reply_to=None,
            provider_author='Provider author', request_id=None):
        self.connection.execute('INSERT INTO wazzup_messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (account, channel, chat, message_id, '2026-10-06T10:00:00+00:00', True, 'text', text,
             None, provider_author, None, status, False, deleted, None))
        if author or reply_to or request_id:
            self.connection.execute('''INSERT INTO wazzup_pilot_outbox
                (account,message_id,author_name,reply_to_message_id,request_id) VALUES (?,?,?,?,?)''',
                (account, message_id, author, reply_to, request_id))

    def summary(self):
        self.connection.execute('INSERT INTO wazzup_chats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('op', 'channel', 'chat', 'whatsapp', 'Test name', '70000000000',
             '2026-10-06T10:00:00+00:00', 'Complete message', True, 12, 7, 5))

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if 'm.message_id=ANY(%s)' in sql:
            ids = params[0]
            sql = sql.replace('m.message_id=ANY(%s)', 'm.message_id IN (' + ','.join('?' * len(ids)) + ')')
            params = ids
        self.cursor = self.connection.execute(sql.replace('%s', '?'), params)

    def fetchall(self):
        return self.cursor.fetchall()


def change(message_id='message', **updates):
    event = dict(account='op', channelId='channel', chatId='chat', messageId=message_id,
                 status='sent', statusOnly=False, affectsList=True,
                 isEcho=True, createdAt='2026-10-06T10:00:00+00:00', emittedAt=1791280800000)
    event.update(updates)
    return event


class RealtimeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.archive = Archive()
        self.addCleanup(self.archive.connection.close)
        self.broker = EventBroker()
        for _ in range(6):
            self.assertTrue(self.broker.acquire())

    def quote_from_delivery_and_history(self, message_id):
        """Exercise both production SQL queries and their field-to-JSON mapping."""
        sequence = self.broker.current_seq()
        broadcast_changes(self.archive, [change(message_id)], self.broker)
        events, _ = self.broker.wait(sequence)
        self.assertEqual(1, len(events))
        live = events[0]['message']
        self.archive.execute(MESSAGE_SELECT + '''
            WHERE m.account=%s AND m.channel_id=%s AND m.chat_id=%s AND m.message_id=%s
            ORDER BY m.dt DESC, m.message_id DESC LIMIT 51''',
            ('op', 'channel', 'chat', message_id))
        rows = self.archive.fetchall()
        self.assertEqual(1, len(rows))
        history = message_item(rows[0])
        self.assertEqual(live, history, 'Initial history and SSE must render the same reply')
        return live, events[0].get('chat')

    def test_reply_quote_hydrates_original_and_preserves_message_and_chat_field_offsets(self):
        self.archive.add('original', text='When will the documents be ready?', provider_author='Original author')
        self.archive.add('answer', text='They are ready.', author='Reply operator', reply_to='original')
        self.archive.summary()

        message, chat = self.quote_from_delivery_and_history('answer')

        self.assertEqual('original', message['replyToMessageId'])
        self.assertEqual('When will the documents be ready?', message['replyText'])
        self.assertEqual('Original author', message['replyAuthorName'])
        self.assertEqual('They are ready.', message['text'])
        self.assertEqual('Reply operator', message['authorName'])
        self.assertEqual('read', message['status'])
        self.assertEqual('Test name', chat['contactName'])
        self.assertEqual('70000000000', chat['contactPhone'])
        self.assertEqual(12, chat['messagesCount'])
        # Галочки строки списка: последнее сообщение чата и его статус. У обоих
        # сообщений фикстуры одно время — при равенстве решает id, как в запросе.
        self.assertEqual(('original', 'read'), (chat['lastMessageId'], chat['lastMessageStatus']))
        self.assertEqual(2, len(self.archive.calls), 'One shared hydration query and one history query')

    def test_icore_send_reaches_live_and_history_with_the_same_client_id(self):
        # The sender's optimistic bubble is matched by this id even when the
        # archive row arrives over SSE before the send request returns.
        self.archive.add('accepted', text='Sent from iCORE', author='Operator', request_id='client-uuid')
        self.archive.add('vendor-ui', text='Sent from the Wazzup window')
        self.archive.summary()

        message, chat = self.quote_from_delivery_and_history('accepted')
        foreign, _ = self.quote_from_delivery_and_history('vendor-ui')

        self.assertEqual('client-uuid', message['clientMessageId'])
        self.assertIsNone(foreign['clientMessageId'])
        self.assertEqual('Test name', chat['contactName'], 'chat summary offset follows the new field')

    def test_reply_quote_never_reads_original_from_another_account_channel_or_chat(self):
        for label, scope in (
                ('account', {'account': 'potok'}),
                ('channel', {'channel': 'another-channel'}),
                ('chat', {'chat': 'another-chat'})):
            with self.subTest(scope=label):
                original_id, reply_id = 'foreign-' + label, 'reply-' + label
                self.archive.add(original_id, text='Private foreign text', provider_author='Foreign author', **scope)
                self.archive.add(reply_id, text='Local message', reply_to=original_id)

                message, _ = self.quote_from_delivery_and_history(reply_id)

                self.assertEqual(original_id, message['replyToMessageId'])
                self.assertIsNone(message['replyText'])
                self.assertIsNone(message['replyAuthorName'])
                self.assertNotIn('Private foreign text', json.dumps(message))
                self.assertNotIn('Foreign author', json.dumps(message))

    def test_reply_quote_does_not_attach_outbox_metadata_from_another_account(self):
        self.archive.add('original', text='Original text')
        self.archive.add('message', text='Local message')
        self.archive.connection.execute('''INSERT INTO wazzup_pilot_outbox
            (account,message_id,author_name,reply_to_message_id) VALUES (?,?,?,?)''',
            ('potok', 'message', 'Foreign operator', 'original'))

        message, _ = self.quote_from_delivery_and_history('message')

        self.assertEqual('Provider author', message['authorName'])
        self.assertIsNone(message['replyToMessageId'])
        self.assertIsNone(message['replyText'])
        self.assertIsNone(message['replyAuthorName'])

    def test_reply_quote_hides_deleted_original_text_in_history_and_realtime(self):
        self.archive.add('deleted-original', text='Deleted confidential text', deleted=True)
        self.archive.add('answer', text='Visible reply', reply_to='deleted-original')

        message, _ = self.quote_from_delivery_and_history('answer')

        self.assertEqual('deleted-original', message['replyToMessageId'])
        self.assertIsNone(message['replyText'])
        self.assertEqual('Visible reply', message['text'])
        self.assertNotIn('Deleted confidential text', json.dumps(message))

    def test_six_readers_share_one_query_and_local_author_and_chat_summary(self):
        self.archive.add('message', author='Local operator')
        self.archive.summary()
        results, started = [], threading.Barrier(7)

        def reader():
            started.wait()
            results.append(self.broker.wait(0, timeout=2)[0])

        threads = [threading.Thread(target=reader) for _ in range(6)]
        for thread in threads:
            thread.start()
        started.wait()
        broadcast_changes(self.archive, [change()], self.broker)
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(self.archive.calls))
        self.assertEqual(6, len(results))
        for events in results:
            self.assertEqual('Complete message', events[0]['message']['text'])
            self.assertEqual('Local operator', events[0]['message']['authorName'])
            self.assertEqual('read', events[0]['status'])
            self.assertEqual('read', events[0]['message']['status'])
            self.assertEqual('Test name', events[0]['chat']['contactName'])
            self.assertEqual(12, events[0]['chat']['messagesCount'])
            self.assertEqual('70000000000', events[0]['chat']['contactPhone'])

    def test_pure_delivery_statuses_need_no_database_reads(self):
        events = [change(status=status, statusOnly=True, affectsList=False)
                  for status in ('sent', 'delivered', 'read')]
        broadcast_changes(self.archive, events, self.broker)
        self.assertFalse(self.archive.calls)
        for _ in range(6):
            received, _ = self.broker.wait(0)
            self.assertEqual(1, len(received))
            self.assertEqual('read', received[0]['status'])
            self.assertTrue(received[0]['statusOnly'])
            self.assertNotIn('message', received[0])

    def test_insert_then_status_retains_full_message_without_shared_mutation(self):
        self.archive.add('message', status='sent')
        self.archive.summary()
        broadcast_changes(self.archive, [change()], self.broker)
        self.broker.publish(change(status='read', statusOnly=True, affectsList=False))
        events, _ = self.broker.wait(0)
        self.assertFalse(events[0]['statusOnly'])
        self.assertTrue(events[0]['affectsList'])
        self.assertEqual('read', events[0]['message']['status'])
        self.assertIn('chat', events[0])
        later, _ = self.broker.wait(1)
        self.assertTrue(later[0]['statusOnly'])
        self.assertFalse(later[0]['affectsList'])
        self.assertNotIn('message', later[0])
        self.assertEqual('sent', self.broker.events[0][1]['message']['status'])

    def test_hydration_observing_newer_status_cannot_be_rolled_back_by_queued_status(self):
        self.archive.add('message', status='read')
        broadcast_changes(self.archive, [change()], self.broker)
        self.broker.publish(change(status='delivered', statusOnly=True, affectsList=False))
        events, _ = self.broker.wait(0)
        self.assertEqual('read', events[0]['status'])
        self.assertEqual('read', events[0]['message']['status'])

    def test_insert_and_status_same_batch_have_one_complete_hydration(self):
        self.archive.add('message')
        broadcast_changes(self.archive, [change(), change(status='read', statusOnly=True,
                                                         affectsList=False)], self.broker)
        events, _ = self.broker.wait(0)
        self.assertEqual(1, len(events))
        self.assertEqual(1, len(self.archive.calls))
        self.assertIn('message', events[0])
        self.assertFalse(events[0]['statusOnly'])

    def test_edit_missing_from_archive_requests_reconciliation_instead_of_stale_text(self):
        self.archive.add('message')
        broadcast_changes(self.archive, [change()], self.broker)
        self.broker.publish(change(statusOnly=False))
        events, _ = self.broker.wait(0)
        self.assertNotIn('message', events[0])
        self.assertNotIn('chat', events[0])
        self.assertFalse(events[0]['statusOnly'])

    def test_large_batch_bounded_queries_and_other_account_excluded(self):
        count = HYDRATE_BATCH_SIZE * 2 + 1
        for index in range(count):
            self.archive.add(str(index))
        self.archive.add('foreign-message', account='potok')
        events = [change(str(index)) for index in range(count)] + [change('foreign-message')]
        broadcast_changes(self.archive, events, self.broker)
        self.assertEqual(3, len(self.archive.calls))
        self.assertTrue(all(len(params[0]) <= HYDRATE_BATCH_SIZE for _, params in self.archive.calls))
        received, _ = self.broker.wait(0)
        self.assertEqual(count + 1, len(received))
        self.assertNotIn('message', received[-1])

    def test_no_subscribers_do_not_create_hydration_load(self):
        for _ in range(6):
            self.broker.release()
        broadcast_changes(self.archive, [change()], self.broker)
        self.assertFalse(self.archive.calls)

    def test_ring_is_bounded_by_bytes_and_lagging_clients_reload(self):
        broker = EventBroker(capacity=2048, max_bytes=1500)
        for index in range(4):
            broker.publish(change(str(index), message=dict(text='a' * 600)))
        self.assertLessEqual(broker.buffer_bytes, 1500)
        self.assertEqual(([{'reload': True}], 4), broker.wait(0))

    def test_oversized_message_falls_back_without_sending_truncated_text(self):
        broker = EventBroker(max_bytes=1500)
        original = change(message=dict(text='Я' * 3000))
        broker.publish(original)
        events, _ = broker.wait(0)
        self.assertNotIn('message', events[0])
        self.assertFalse(events[0]['statusOnly'])
        self.assertEqual('Я' * 3000, original['message']['text'])
        self.assertLessEqual(broker.buffer_bytes, 1500)

    def test_listener_reconnect_reloads_instead_of_replaying_stale_hydration(self):
        self.broker.publish(change(message=dict(text='Old snapshot')))
        self.broker.set_ready(False)
        self.assertEqual([{'unavailable': True}], self.broker.wait(0)[0])
        self.broker.set_ready(True)
        self.assertEqual([{'reload': True}], self.broker.wait(0)[0])


if __name__ == '__main__':
    unittest.main()
