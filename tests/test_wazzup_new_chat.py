"""Новый чат по номеру — кнопка «+» «Чатов ОП» (владелец, 20.10.2026: «в самом wazzup есть
кнопка плюс рядом с поиском… можно начать чат написав номер»; начинать можно во всех
активных каналах, кроме Global и каналов «не писать первыми»).

Без приложения, базы и Wazzup: поддельная база и HTTP из tests/test_wazzup_pilot.py.
"""
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from tests.test_wazzup_pilot import MemoryCursor, MemoryDatabase, fixture
from wazzup import new_chat, pilot

WHATSAPP = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
WABA = '6983db13-ae8e-4b27-b09d-6c10ae4f54cf'
GLOBAL_TAXI = '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2'
GLOBAL_BUSINESS = 'b88e8d3a-f3f4-4854-bbc8-076a0924d118'
NO_FIRST = '0052314a-f3f0-4640-8c4a-49071d1ac392'
BLOCKED = '7dc370c8-7542-43d4-9f92-168fb23ea37c'
INSTAGRAM = '2efaa0a1-0378-4ada-8a9f-808a082adeab'
TEMPLATE = '@template: 11111111-2222-3333-4444-555555555555 {[[Иван]]}'

CHANNELS = [
    {'channelId': WHATSAPP, 'state': 'active', 'transport': 'whatsapp', 'name': 'Jana Taxi Бизнес'},
    {'channelId': WABA, 'state': 'active', 'transport': 'wapi', 'name': 'Help Taxi'},
    {'channelId': GLOBAL_TAXI, 'state': 'active', 'transport': 'wapi', 'name': 'Global Taxi'},
    {'channelId': GLOBAL_BUSINESS, 'state': 'active', 'transport': 'whatsapp', 'name': 'Global Ватсап Бизнес'},
    {'channelId': NO_FIRST, 'state': 'active', 'transport': 'whatsapp', 'name': '2ГИС - не писать первыми '},
    {'channelId': BLOCKED, 'state': 'blocked', 'transport': 'whatsapp', 'name': 'Честный'},
    {'channelId': INSTAGRAM, 'state': 'active', 'transport': 'instagram', 'name': 'taxi_qazaq'},
]


class UnknownChatCursor(MemoryCursor):
    """Чатов в базе нет — как у номера, которому ещё не писали."""

    def execute(self, query, values=()):
        if ' '.join(query.split()).startswith('SELECT chat_type FROM wazzup_chats'):
            self.db.statements.append((query, values))
            self.row = None
            return
        super().execute(query, values)


class UnknownChatDatabase(MemoryDatabase):
    @contextmanager
    def _get_cursor(self):
        with self.lock:
            yield UnknownChatCursor(self)


class ChannelRuleTests(unittest.TestCase):
    def test_owner_rule_all_active_channels_except_global_and_no_first(self):
        self.assertEqual(sorted([WHATSAPP, WABA]),
                         new_chat.new_chat_channel_ids(CHANNELS, pilot.EXCLUDED_CHANNELS))

    def test_no_first_wording_variants(self):
        for name in ('Не писать первым! ', 'Не отправлять сообщения первым ', '2 ГИС не писать первым '):
            channel = {'channelId': 'x', 'state': 'active', 'transport': 'whatsapp', 'name': name}
            self.assertFalse(new_chat.may_start_chat(channel), name)

    def test_allowed_narrows_to_the_persons_channels(self):
        self.assertEqual([WABA], new_chat.new_chat_channel_ids(CHANNELS, (), allowed={WABA, GLOBAL_TAXI}))

    def test_phone_is_international_digits(self):
        self.assertEqual('77001234567', new_chat.normalize_phone('+7 (700) 123-45-67'))
        self.assertEqual('77001234567', new_chat.normalize_phone('8 700 123 45 67'))
        self.assertEqual('998901234567', new_chat.normalize_phone('+998 90 123 45 67'))
        for bad in ('', '12345', '0700123456', '7' * 16, None, 77001234567):
            self.assertEqual('', new_chat.normalize_phone(bad), bad)


class NewChatSendTests(unittest.TestCase):
    def setUp(self):
        for target, value in ((pilot.accounts, 'api_key'),):
            patcher = patch.object(target, value, return_value='isolated-test-key')
            patcher.start()
            self.addCleanup(patcher.stop)
        # Шаблоны WABA без похода к Wazzup: код считается верным, превью — сам текст.
        for name, value in (('validate_template_message', None), ('render_template_preview', 'Здравствуйте, Иван')):
            patcher = patch('wazzup.templates.' + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def app(self, db=None):
        app, db, _, transport = fixture(db=db or UnknownChatDatabase(), channels=lambda account: CHANNELS)
        return app.test_client(), db, transport

    def body(self, channel=WABA, chat='77001234567', text=TEMPLATE, **changes):
        result = dict(account='op', channelId=channel, chatId=chat, text=text,
                      clientMessageId=str(uuid.uuid4()), newChat=True, contactName='  Иван   Петров ')
        result.update(changes)
        return result

    def test_capabilities_list_the_channels_of_the_plus(self):
        client, _, _ = self.app()
        data = client.get('/api/wazzup/pilot').get_json()
        self.assertEqual(sorted([WHATSAPP, WABA]), data['newChatChannelIds'])

    def test_first_template_creates_the_chat_with_the_typed_name(self):
        client, db, transport = self.app()
        response = client.post('/api/wazzup/pilot/send', json=self.body())
        self.assertEqual(201, response.status_code, response.get_json())
        sent = transport.post.call_args.kwargs['json']
        self.assertEqual((WABA, '77001234567', 'whatsapp', TEMPLATE),
                         (sent['channelId'], sent['chatId'], sent['chatType'], sent['text']))
        stored = db.messages['vendor-message-1']
        self.assertEqual({'name': 'Иван Петров', 'phone': '77001234567'}, stored['contact'])
        self.assertEqual('Здравствуйте, Иван', stored['text'])

    def test_plain_text_may_start_a_chat_on_ordinary_whatsapp(self):
        client, _, transport = self.app()
        response = client.post('/api/wazzup/pilot/send', json=self.body(channel=WHATSAPP, text='Здравствуйте'))
        self.assertEqual(201, response.status_code)
        transport.post.assert_called_once()

    def test_waba_first_message_is_template_only(self):
        client, db, transport = self.app()
        response = client.post('/api/wazzup/pilot/send', json=self.body(text='Здравствуйте'))
        self.assertEqual((400, 'NEW_CHAT_FIRST_MESSAGE'), (response.status_code, response.get_json()['code']))
        self.assertFalse(db.outbox)
        transport.post.assert_not_called()

    def test_closed_channels_refuse_the_plus(self):
        client, db, transport = self.app()
        for channel in (NO_FIRST, GLOBAL_BUSINESS):
            response = client.post('/api/wazzup/pilot/send', json=self.body(channel=channel, text='Привет'))
            self.assertEqual((403, 'NEW_CHAT_CHANNEL'), (response.status_code, response.get_json()['code']), channel)
        # Global Taxi отправку закрывает раньше — своим правилом пилота.
        self.assertEqual(403, client.post('/api/wazzup/pilot/send', json=self.body(channel=GLOBAL_TAXI)).status_code)
        self.assertFalse(db.outbox)
        transport.post.assert_not_called()

    def test_number_must_be_international_digits(self):
        client, db, transport = self.app()
        for chat in ('+77001234567', '87001234567', '7700123'):
            response = client.post('/api/wazzup/pilot/send', json=self.body(chat=chat))
            self.assertEqual((400, 'NEW_CHAT_PHONE'), (response.status_code, response.get_json()['code']), chat)
        transport.post.assert_not_called()

    def test_without_the_flag_an_unknown_chat_is_still_refused(self):
        client, _, transport = self.app()
        body = self.body()
        body.pop('newChat')
        response = client.post('/api/wazzup/pilot/send', json=body)
        self.assertEqual(400, response.status_code)
        self.assertEqual('Выберите существующий личный чат WhatsApp', response.get_json()['error'])
        transport.post.assert_not_called()

    def test_reply_or_file_cannot_start_a_chat(self):
        client, _, transport = self.app()
        response = client.post('/api/wazzup/pilot/send', json=self.body(replyToMessageId='m-1'))
        self.assertEqual((400, 'NEW_CHAT_FIRST_MESSAGE'), (response.status_code, response.get_json()['code']))
        transport.post.assert_not_called()

    def test_flag_on_an_existing_chat_changes_nothing(self):
        client, db, transport = self.app(MemoryDatabase())
        response = client.post('/api/wazzup/pilot/send', json=self.body(channel=WHATSAPP, text='Ещё раз'))
        self.assertEqual(201, response.status_code)
        self.assertNotIn('contact', db.messages['vendor-message-1'])
        transport.post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
