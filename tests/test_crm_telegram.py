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
            queue_title='iTaxi', priority='normal',
        )
        payload.update(kwargs)
        return telegram.build_ticket_message(**payload)

    def test_number_is_in_the_header(self):
        """По номеру сотрудник понимает, о чём речь, а мы находим запись."""
        self.assertIn('Обращение №42', self.build())

    def test_no_reply_instruction(self):
        """Владелец убрал её как лишнюю (19.08.2026).

        Механику реплая это не меняет: бот по-прежнему видит только ответы на
        свои сообщения, а под сообщением остаются кнопки «Беру в работу» и
        «Выполнено» — они работают без реплая.
        """
        self.assertNotIn('Ответьте на это сообщение', self.build())

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

    def test_no_author_department_or_time(self):
        """Просьба владельца 19.08.2026: в группе это лишнее.

        Отвечают не человеку, а обращению; кто завёл, из какого отдела и когда —
        видно в карточке, куда ведёт ссылка на номере.
        """
        message = self.build()
        for word in ('Обратился', 'СЗоВ', 'Иванов'):
            self.assertNotIn(word, message, word)

    def test_number_links_to_the_card(self):
        """Из чата нужен не раздел, а именно это обращение."""
        message = self.build()
        self.assertIn('view=crm_tickets', message)
        self.assertIn('ticket_id=42', message)
        self.assertIn('>Обращение №42</a>', message)
        self.assertEqual(telegram.ticket_link(0), '')

    def test_link_is_escaped_for_an_attribute(self):
        """Амперсанд между параметрами обязан быть &amp;, иначе Telegram ругнётся."""
        self.assertIn('&amp;ticket_id=42', self.build())

    def test_own_wording_flips_what_is_bold(self):
        """Тематика формулирует сама → просьба обычным, данные жирным.

        Взгляд должен падать на номер ВУ и город, а не на слова «прошу проверить».
        """
        plain = self.build(subject='Прошу проверить на выдачу термокороба',
                           body='LL190044 · Астана', own_wording=True)
        self.assertIn('Прошу проверить на выдачу термокороба', plain)
        self.assertNotIn('<b>Прошу проверить', plain)
        self.assertIn('<b>LL190044 · Астана</b>', plain)

        usual = self.build(subject='Тема', body='Устройство: Android')
        self.assertIn('<b>Тема</b>', usual)
        self.assertIn('<b>Устройство:</b> Android', usual)

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
        # Шапка с номером и ссылкой обязана уцелеть: без неё обрезанное
        # сообщение уже не привязать к обращению.
        self.assertIn('Обращение №42', message)
        self.assertIn('ticket_id=42', message)


class NoButtonsTest(unittest.TestCase):
    """Кнопок под сообщением больше нет (решение владельца 19.08.2026).

    Из группы они выглядели так, будто ничего не делают: нажатие отвечало
    всплывающей подсказкой на пару секунд и меняло сами кнопки, а в чате после
    него не оставалось ничего. Статус обращения ведут в iCORE.
    """

    def test_keyboard_builder_is_gone(self):
        self.assertFalse(hasattr(telegram, 'build_keyboard'))

    def test_callback_parsing_is_gone(self):
        self.assertFalse(hasattr(telegram, 'parse_callback'))
        self.assertFalse(hasattr(telegram, 'CALLBACK_TAKE'))
        self.assertFalse(hasattr(telegram, 'CALLBACK_DONE'))


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


class ReplyMessageTest(unittest.TestCase):
    """Уточнение оператора, которое уходит в рабочую группу реплаем."""

    def build(self, **kwargs):
        kwargs.setdefault('ticket_id', 10)
        kwargs.setdefault('author_name', 'Кастек Гаухар')
        kwargs.setdefault('body', 'Документы так и не пришли')
        return telegram.build_reply_message(**kwargs)

    def test_iin_stands_next_to_the_ticket_number(self):
        """Просьба СЗоВ 19.08.2026.

        Номер опознаёт обращение для нас, а специалист в группе работает по
        водителю. Реплай в Telegram сворачивается в одну строку, поэтому
        исходное сообщение с ИИН видно не всегда.
        """
        text = self.build(iin='060606202020')
        self.assertIn('№10', text)
        self.assertIn('ИИН 060606202020', text)
        header = text.splitlines()[0]
        self.assertIn('ИИН 060606202020', header)

    def test_without_iin_the_header_stays_as_it_was(self):
        """У обращения без ИИН лишнего разделителя в заголовке быть не должно."""
        text = self.build(iin=None)
        header = text.splitlines()[0]
        self.assertNotIn('ИИН', header)
        self.assertNotIn('·', header)

    def test_iin_is_escaped_like_every_other_value(self):
        """В заголовок идёт HTML: неэкранированное значение сломало бы разметку."""
        text = self.build(iin='<b>060606202020')
        self.assertIn('&lt;b&gt;060606202020', text)
        self.assertNotIn('<b>060606202020', text)

    def test_body_and_author_are_still_there(self):
        text = self.build(iin='060606202020')
        self.assertIn('Документы так и не пришли', text)
        self.assertIn('Кастек Гаухар', text)


class StatusNoticeTest(unittest.TestCase):
    """Отбивка в группу о том, что обращение закрыли из системы."""

    def test_iin_stands_next_to_the_number(self):
        text = telegram.build_status_notice(ticket_id=10, status='resolved',
                                            actor_name='Кастек Гаухар', iin='060606202020')
        self.assertIn('№10 · ИИН 060606202020 — решено', text)
        self.assertIn('Кастек Гаухар', text)

    def test_without_iin_the_wording_stays_as_it_was(self):
        text = telegram.build_status_notice(ticket_id=10, status='resolved')
        self.assertEqual(text, '✅ Обращение №10 — решено')


class EveryGroupMessageCarriesTheIinTest(unittest.TestCase):
    """Просьба владельца 19.08.2026: ИИН — во всех сообщениях по обращению.

    В группе идут обращения по разным водителям, и номер обращения опознаёт их
    только для нас. Поэтому проверяется КАЖДОЕ исходящее сообщение: добавят
    новый вид — тест напомнит, что ИИН в нём тоже нужен.
    """

    IIN = '060606202020'

    def test_initial_message(self):
        """У исходного ИИН приходит в теме — её собирает scenarios.render_subject."""
        text = telegram.build_ticket_message(
            ticket_id=10, subject='Документы не поступили · ИИН %s' % self.IIN,
            body='''ИИН водителя: %s
Таксопарк: iTaxi''' % self.IIN,
            queue_title='iTaxi Sapar',
        )
        self.assertIn(self.IIN, text)

    def test_clarification_message(self):
        text = telegram.build_reply_message(ticket_id=10, author_name='Кастек Гаухар',
                                            body='уточнение', iin=self.IIN)
        self.assertIn(self.IIN, text)

    def test_status_notice(self):
        text = telegram.build_status_notice(ticket_id=10, status='resolved', iin=self.IIN)
        self.assertIn(self.IIN, text)

    def test_no_other_group_message_builder_appeared(self):
        """Список видов сообщений закрыт: появится новый — этот тест упадёт, и
        про ИИН в нём не забудут."""
        builders = sorted(name for name in dir(telegram) if name.startswith('build_'))
        self.assertEqual(builders, ['build_reply_message', 'build_status_notice',
                                    'build_ticket_message'])


class BodyMarkupTest(unittest.TestCase):
    """Разметка готового текста для Telegram.

    Сам текст собирает crm.scenarios и разметки не несёт — он показывается ещё и
    в карточке обращения. Выделение подписей живёт здесь.
    """

    BODY = '''
iTaxi · Алматы · период февраль 2026

Тип ошибки: Сайт не загружается
Текст ошибки: сломалось: код 500

✅ Проверено: повторный вход, ожидание 5 минут
'''

    def test_labels_become_bold(self):
        out = telegram.format_body(self.BODY)
        self.assertIn('<b>Тип ошибки:</b> Сайт не загружается', out)
        self.assertIn('<b>✅ Проверено:</b> повторный вход, ожидание 5 минут', out)

    def test_line_without_a_label_stays_plain(self):
        out = telegram.format_body(self.BODY)
        self.assertIn('iTaxi · Алматы · период февраль 2026', out)
        self.assertNotIn('<b>iTaxi', out)

    def test_only_the_first_colon_splits_the_line(self):
        out = telegram.format_body(self.BODY)
        self.assertIn('<b>Текст ошибки:</b> сломалось: код 500', out)

    def test_blank_lines_survive(self):
        """Пустая строка — граница блока, и в Telegram она тоже нужна."""
        out = telegram.format_body(self.BODY)
        self.assertEqual(out.count(chr(10) * 2), 2)

    def test_values_are_escaped(self):
        out = telegram.format_body('Текст ошибки: <b>500</b>')
        self.assertIn('&lt;b&gt;500&lt;/b&gt;', out)
        self.assertNotIn('<b>500</b>', out)

    def test_long_phrase_with_a_colon_is_not_a_label(self):
        long = '%s: ответ' % ('о' * 70)
        out = telegram.format_body(long)
        self.assertNotIn('<b>', out)

    def test_ticket_message_uses_the_markup(self):
        out = telegram.build_ticket_message(
            ticket_id=12, subject='Тема · ИИН 060606202020', body=self.BODY,
            queue_title='iTaxi Sapar')
        self.assertIn('<b>Тип ошибки:</b>', out)


if __name__ == '__main__':
    unittest.main()
