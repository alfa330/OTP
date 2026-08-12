# -*- coding: utf-8 -*-
"""Раздел «Обращения» со стороны Telegram: формат, кнопки, приём ответов.

Всё проверяемое здесь работает в чужом рабочем чате, где ошибка видна десяткам
людей и не отменяется. Поэтому формат сообщения и разбор ответа отделены от
сети и проверяются тестами, а не глазами в живой группе.
"""

import unittest

from crm import bot as crm_bot
from crm import telegram


class TicketMessageTest(unittest.TestCase):
    def build(self, **kwargs):
        payload = dict(
            ticket_id=42, subject='Не приходит бонус', body='Водитель ждёт ответа',
            queue_title='iTaxi', priority='normal', author_name='Иванов И.',
        )
        payload.update(kwargs)
        return telegram.build_ticket_message(**payload)

    def test_number_is_in_the_header(self):
        """По номеру сотрудник понимает, о чём речь, а мы находим запись."""
        self.assertIn('Обращение №42', self.build())

    def test_reply_instruction_is_always_present(self):
        """Ответ «просто в чат» до системы не дойдёт: бот видит только реплаи."""
        self.assertIn('Ответьте на это сообщение', self.build())

    def test_user_text_is_escaped(self):
        """Незакрытая скобка в описании не должна ломать разметку сообщения."""
        message = self.build(body='Сравните <b>суммы</b> и & проверьте')
        self.assertIn('&lt;b&gt;', message)
        self.assertIn('&amp;', message)

    def test_normal_priority_is_not_shouted(self):
        """Обычный приоритет не красится и не занимает строку — это не событие."""
        self.assertNotIn('Приоритет', self.build(priority='normal'))
        self.assertIn('Приоритет', self.build(priority='critical'))

    def test_optional_blocks_are_skipped_when_empty(self):
        message = self.build()
        self.assertNotIn('Клиент', message)
        self.assertNotIn('Тема:', message)
        self.assertNotIn('Ответ нужен до', message)

    def test_client_and_due_appear_when_given(self):
        message = self.build(client_name='Асель', client_phone='+7 700 000 00 00',
                             due_text='12.08 19:00', topic_title='Бонусы')
        self.assertIn('Асель · +7 700 000 00 00', message)
        self.assertIn('Ответ нужен до', message)
        self.assertIn('Бонусы', message)

    def test_message_fits_telegram_limit(self):
        """4096 — жёсткий предел Bot API: длинное описание режем, а не теряем всё."""
        message = self.build(body='я' * 9000)
        self.assertLessEqual(len(message), telegram.MESSAGE_LIMIT)
        self.assertIn('Обращение №42', message)
        self.assertIn('Ответьте на это сообщение', message)


class KeyboardTest(unittest.TestCase):
    def test_new_ticket_offers_both_actions(self):
        keyboard = telegram.build_keyboard(42, 'open')
        labels = [b['text'] for b in keyboard['inline_keyboard'][0]]
        self.assertEqual(len(labels), 2)
        self.assertTrue(any('Выполнено' in label for label in labels))

    def test_ticket_in_progress_offers_only_completion(self):
        """«Беру в работу» второй раз бессмысленно."""
        keyboard = telegram.build_keyboard(42, 'in_progress')
        self.assertEqual(len(keyboard['inline_keyboard'][0]), 1)

    def test_closed_ticket_has_no_buttons(self):
        """Мёртвая кнопка под сообщением — источник лишних кликов."""
        for status in ('resolved', 'cancelled'):
            self.assertIsNone(telegram.build_keyboard(42, status), status)

    def test_callback_roundtrip(self):
        for status, action in (('open', 'work'), ('answered', 'done')):
            keyboard = telegram.build_keyboard(7, status)
            data = keyboard['inline_keyboard'][0][0]['callback_data']
            self.assertEqual(telegram.parse_callback(data)[1], 7)
            del action

    def test_bad_callback_never_raises(self):
        """Кнопка из старого сообщения не должна ронять обработчик."""
        for data in (None, '', 'crm:', 'crm:done:', 'crm:done:абв', 'other:done:1', 'crm:unknown:1'):
            self.assertIsNone(telegram.parse_callback(data), repr(data))


class Message(dict):
    """Ответ из группы в виде словаря — как приходит в вебхуке."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


class IncomingReplyTest(unittest.TestCase):
    def test_sender_name_prefers_full_name(self):
        self.assertEqual(
            telegram.sender_name({'first_name': 'Аружан', 'last_name': 'О.'}),
            'Аружан О.',
        )

    def test_sender_name_falls_back_to_username_then_id(self):
        self.assertEqual(telegram.sender_name({'username': 'aru'}), '@aru')
        self.assertEqual(telegram.sender_name({'id': 555}), 'Telegram 555')
        self.assertIsNone(telegram.sender_name(None))

    def test_caption_counts_as_text(self):
        """Скриншот с подписью — самый частый ответ; подпись терять нельзя."""
        self.assertEqual(telegram.message_text({'caption': 'вот тут'}), 'вот тут')

    def test_largest_photo_size_is_taken(self):
        attachment = telegram.extract_attachment({'photo': [
            {'file_id': 'small', 'file_size': 100},
            {'file_id': 'big', 'file_size': 900},
        ]})
        self.assertEqual(attachment['file_id'], 'big')
        self.assertEqual(attachment['kind'], 'photo')

    def test_document_keeps_name_and_mime(self):
        attachment = telegram.extract_attachment({'document': {
            'file_id': 'f1', 'file_name': 'акт.pdf', 'mime_type': 'application/pdf',
            'file_size': 2048,
        }})
        self.assertEqual(attachment['name'], 'акт.pdf')
        self.assertEqual(attachment['mime'], 'application/pdf')
        self.assertEqual(attachment['size'], 2048)

    def test_plain_text_has_no_attachment(self):
        self.assertIsNone(telegram.extract_attachment({'text': 'ответили словами'}))


class BotFilterTest(unittest.TestCase):
    """Фильтр обработчика: он регистрируется ПЕРВЫМ и не должен хватать чужое."""

    def message(self, chat_type='supergroup', reply=True, text='ответ'):
        return Message(
            chat=Message(id=-100, type=chat_type),
            reply_to_message=Message(message_id=5) if reply else None,
            text=text,
        )

    def test_group_reply_is_ours(self):
        self.assertTrue(crm_bot._is_group_reply(self.message()))

    def test_private_chat_is_not_ours(self):
        """Личка бота — это меню супервайзера, туда лезть нельзя."""
        self.assertFalse(crm_bot._is_group_reply(self.message(chat_type='private')))

    def test_plain_group_message_is_not_ours(self):
        """Без реплая ответ не связать с обращением, и перехватывать его незачем."""
        self.assertFalse(crm_bot._is_group_reply(self.message(reply=False)))

    def test_commands_are_left_to_other_sections(self):
        """Реплаем в чате контроля опозданий шлют /report — он должен дойти."""
        self.assertFalse(crm_bot._is_group_reply(self.message(text='/report 01.08')))

    def test_attachment_without_text_is_still_ours(self):
        """Реплай одним скриншотом — валидный ответ, текста в нём нет."""
        message = self.message(text=None)
        message['caption'] = None
        self.assertTrue(crm_bot._is_group_reply(message))


if __name__ == '__main__':
    unittest.main()
