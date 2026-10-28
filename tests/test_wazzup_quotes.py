"""Цитата ответа Wazzup (wazzup/quotes.py) из вебхука и из окна чатов «Потока»."""
import unittest

from wazzup import potok_sync
from wazzup.quotes import NO_QUOTE, QUOTE_COLUMNS, quote_values

# Форма окна чатов — как в тест-чате аккаунта «op» 28.10.2026; id и номер вымышленные.
WINDOW_OWN_REPLY = {
    'id': 'reply-own', 'incoming': True, 'status': 99, 'type': 1,
    'text': 'Тест', 'authorName': '77000000000', 'channelId': 'channel-1',
    'chatId': '77000000000', 'chatType': 'whatsapp', 'datetime': 1793185258550,
    'quotedMessage': {'authorName': '77000000000', 'displayAuthorName': '+77000000000', 'contentSha': None,
                      'filename': None, 'id': 'own-message', 'status': 99, 'text': 'Тест', 'type': 1},
}
WINDOW_REPLY_TO_PHONE = {
    'id': 'reply-operator', 'incoming': True, 'status': 99, 'type': 1,
    'text': 'Ааа ок', 'chatId': '77000000000', 'chatType': 'whatsapp', 'datetime': 1791572100720,
    'quotedMessage': {'authorName': 'Phone', 'displayAuthorName': 'Wazzup • Phone', 'contentSha': None,
                      'id': 'operator-message', 'status': 3, 'text': 'Документы получили', 'type': 1},
}


class QuoteValuesTests(unittest.TestCase):
    def test_webhook_quote_of_the_drivers_own_message(self):
        self.assertEqual(('own', False, 'text', 'Тест', None), quote_values({'quotedMessage': {
            'messageId': 'own', 'isEcho': False, 'status': 'inbound', 'type': 'text', 'text': 'Тест'}}))

    def test_webhook_quote_of_an_operator_message_keeps_its_author(self):
        self.assertEqual(('op-1', True, 'image', None, 'Айгуль'), quote_values({'quotedMessage': {
            'messageId': 'op-1', 'isEcho': True, 'type': 'image', 'text': '', 'authorName': 'Айгуль'}}))

    def test_status_decides_the_side_without_is_echo_and_the_client_phone_is_not_an_author(self):
        self.assertEqual((False, None), quote_values({'quotedMessage': {
            'messageId': 'x', 'status': 'inbound', 'authorName': '77000000000'}})[1::3])
        self.assertEqual((False, None), quote_values({'quotedMessage': {
            'id': 'x', 'status': 99, 'authorName': '77000000000'}})[1::3])
        self.assertEqual((True, 'Phone'), quote_values({'quotedMessage': {
            'id': 'x', 'status': 3, 'authorName': 'Phone'}})[1::3])
        self.assertIsNone(quote_values({'quotedMessage': {'messageId': 'x'}})[1])

    def test_no_quote_or_a_quote_without_id_stores_nothing(self):
        for message in ({}, {'quotedMessage': None}, {'quotedMessage': 'own'}, {'quotedMessage': {}},
                        {'quotedMessage': {'text': 'без id'}}, None):
            with self.subTest(message=message):
                self.assertEqual(NO_QUOTE, quote_values(message))
        self.assertEqual(len(QUOTE_COLUMNS), len(NO_QUOTE))


class PotokWindowQuoteTests(unittest.TestCase):
    def test_window_quote_becomes_the_webhook_form_and_the_same_columns(self):
        converted = potok_sync.to_webhook_message(WINDOW_OWN_REPLY, {'chatType': 'whatsapp'})
        self.assertEqual({'messageId': 'own-message', 'isEcho': False, 'type': 'text', 'text': 'Тест',
                          'authorName': None}, converted['quotedMessage'])
        self.assertEqual(('own-message', False, 'text', 'Тест', None), quote_values(converted))
        phone = potok_sync.to_webhook_message(WINDOW_REPLY_TO_PHONE, {'chatType': 'whatsapp'})
        self.assertEqual(('operator-message', True, 'text', 'Документы получили', 'Phone'), quote_values(phone))

    def test_message_without_quote_converts_to_none(self):
        plain = dict(WINDOW_OWN_REPLY, quotedMessage=None)
        self.assertIsNone(potok_sync.to_webhook_message(plain, {'chatType': 'whatsapp'})['quotedMessage'])
        self.assertIsNone(potok_sync.to_webhook_quote({'text': 'без id'}))


if __name__ == '__main__':
    unittest.main()
