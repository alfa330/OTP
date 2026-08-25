# -*- coding: utf-8 -*-
"""Раздел «Обращения» со стороны Telegram: формат, кнопки, приём ответов.

Всё проверяемое здесь работает в чужом рабочем чате, где ошибка видна десяткам
людей и не отменяется. Поэтому формат сообщения и разбор ответа отделены от
сети и проверяются тестами, а не глазами в живой группе.
"""

import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from crm import bot as crm_bot
from crm import telegram, transport


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

    def test_header_opens_with_the_request_not_with_the_number(self):
        """ТЗ задачи #206: первой строкой — чего от группы хотят.

        Номер сам по себе не говорит ничего, и стоять первым ему незачем: в
        чате, куда падают десятки сообщений, взгляд должен цепляться за просьбу.
        """
        message = self.build(heading='Просьба снять оплату за подписание документов')
        first, second = message.split(chr(10))[:2]
        self.assertEqual(first, '🎫 <b>Просьба снять оплату за подписание документов</b>')
        self.assertIn('Обращение №42', second)
        self.assertIn('iTaxi', second)

    def test_subject_is_the_heading_when_the_topic_has_no_wording(self):
        """Свободное обращение и тематика без своей просьбы — заголовком тема."""
        self.assertIn('<b>Не приходит бонус</b>', self.build())

    def test_topic_title_is_not_repeated_next_to_the_heading(self):
        """Раньше в шапке стояли и «Тема: …», и она же строкой ниже — одно и то
        же дважды на одном экране."""
        message = self.build(heading='Просьба снять оплату за подписание документов')
        self.assertEqual(message.count('Просьба снять оплату за подписание документов'), 1)

    def test_no_reply_instruction(self):
        """Владелец убрал её как лишнюю (19.08.2026).

        Механику реплая это не меняет: бот по-прежнему видит только ответы на
        свои сообщения.
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
                             due_text='12.08 19:00', heading='Просьба вернуть бонус')
        self.assertIn('Асель · +7 700 000 00 00', message)
        self.assertIn('Ответ нужен до', message)
        self.assertIn('Просьба вернуть бонус', message)

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


class FakeDispatcher:
    """Диспетчер aiogram в том объёме, который нужен register(): декоратор."""

    def __init__(self):
        self.handler = None

    def message_handler(self, *_filters, **_kwargs):
        def keep(func):
            self.handler = func
            return func
        return keep


class FakeTypes:
    class ContentTypes:
        ANY = 'any'


class ReplyReceiptTest(unittest.TestCase):
    """Расписка за принятый ответ: реакция на сообщение, а не текст в чат.

    Гоняется НАСТОЯЩИЙ обработчик (crm.bot.register), а не его пересказ: именно
    он решает, чем расписываться, и именно он раньше писал в группу «✅ Ответ
    отправлен оператору по обращению №N». aiogram для этого не нужен — из него
    обработчику нужен только декоратор.
    """

    # Набор реакций у Telegram закрытый (ReactionTypeEmoji, Bot API 7.0+).
    # Список держим в тесте, а не в коде раздела: он нужен ровно затем, чтобы
    # поймать правку эмодзи на то, которое Telegram отвергнет целиком
    # (REACTION_INVALID) — например на «✅», как было в прежнем тексте расписки.
    ALLOWED_REACTIONS = (
        '👍', '👎', '❤', '🔥', '🥰', '👏', '😁', '🤔', '🤯', '😱', '🤬', '😢',
        '🎉', '🤩', '🤮', '💩', '🙏', '👌', '🕊', '🤡', '🥱', '🥴', '😍', '🐳',
        '❤‍🔥', '🌚', '🌭', '💯', '🤣', '⚡', '🍌', '🏆', '💔', '🤨', '😐',
        '🍓', '🍾', '💋', '🖕', '😈', '😴', '😭', '🤓', '👻', '👨‍💻', '👀',
        '🎃', '🙈', '😇', '😨', '🤝', '✍', '🤗', '🫡', '🎅', '🎄', '☃', '💅',
        '🤪', '🗿', '🆒', '💘', '🙉', '🦄', '😘', '💊', '🙊', '😎', '👾',
        '🤷‍♂', '🤷', '🤷‍♀', '😡',
    )

    def setUp(self):
        self.dispatcher = FakeDispatcher()
        # Пул настоящий: обработчик уносит и приём ответа, и расписку в
        # исполнителя, и подмена этого не проверила бы.
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.pool.shutdown)
        self.replies = []
        self.reactions = []
        self.accepted = {'ticket_id': 42}
        self.reaction_error = None
        crm_bot.register(self.dispatcher, object(), self.pool, FakeTypes)

    def message(self, message_id=900):
        async def reply(text, **_kwargs):
            self.replies.append(text)

        return Message(
            chat=Message(id=-1001, type='supergroup'),
            reply_to_message=Message(message_id=555),
            message_id=message_id, text='ответили', reply=reply,
        )

    def _ingest(self, _db, **_kwargs):
        return self.accepted

    def _set_reaction(self, chat_id, message_id, emoji):
        self.reactions.append({'chat_id': chat_id, 'message_id': message_id,
                               'emoji': emoji})
        if self.reaction_error:
            return None, self.reaction_error
        return True, None

    def handle(self, message):
        with mock.patch.object(crm_bot.service, 'ingest_group_reply', self._ingest),                 mock.patch.object(crm_bot.transport, 'set_message_reaction',
                                  self._set_reaction):
            asyncio.run(self.dispatcher.handler(message))

    def test_accepted_reply_is_marked_with_a_reaction(self):
        """Расписка ставится на сообщение сотрудника, а не пишется в чат."""
        self.handle(self.message())
        self.assertEqual(self.reactions, [{'chat_id': -1001, 'message_id': 900,
                                           'emoji': telegram.REPLY_REACTION}])
        self.assertEqual(self.replies, [])

    def test_every_reply_gets_a_reaction_not_only_the_first(self):
        """Гейта «раз на обращение» больше нет: реакция чат не засоряет.

        У текста он был обязателен, иначе расписки заняли бы половину переписки.
        Реакция не занимает строки, а подтверждение нужно на КАЖДЫЙ ответ —
        иначе сотрудник со второго раза не знает, дошло ли до системы.
        """
        for message_id in (900, 901, 902):
            self.handle(self.message(message_id))
        self.assertEqual([item['message_id'] for item in self.reactions],
                         [900, 901, 902])
        self.assertEqual(self.replies, [])

    def test_foreign_reply_is_not_marked(self):
        """Реплай на отчёт другого раздела: ни реакции, ни ответа."""
        self.accepted = None
        try:
            self.handle(self.message())
        except BaseException as error:  # SkipHandler, если aiogram установлен
            if crm_bot.SkipHandler is None or not isinstance(error, crm_bot.SkipHandler):
                raise
        self.assertEqual(self.reactions, [])
        self.assertEqual(self.replies, [])

    def test_refused_reaction_is_logged_and_not_replaced_by_text(self):
        """Отказ реакции слышно в логах, но в чат раздел всё равно не пишет."""
        self.reaction_error = 'REACTION_INVALID'
        with self.assertLogs(level='WARNING') as logs:
            self.handle(self.message())
        self.assertEqual(self.replies, [])
        self.assertTrue(any('реакция' in line for line in logs.output), logs.output)

    def test_reaction_emoji_is_one_telegram_allows(self):
        """«✅» и любое незнакомое эмодзи Telegram отвергает целиком."""
        self.assertIn(telegram.REPLY_REACTION, self.ALLOWED_REACTIONS)


class ReactionPayloadTest(unittest.TestCase):
    """Вызов setMessageReaction: форму запроса Telegram проверяет строго."""

    def call_with(self, emoji):
        with mock.patch.object(transport, '_call', return_value=(True, None)) as call:
            transport.set_message_reaction(-1001, '900', emoji)
        self.assertEqual(call.call_args[0][0], 'setMessageReaction')
        return call.call_args[1]['json_payload']

    def test_reaction_goes_as_a_list_of_reaction_type(self):
        payload = self.call_with('👍')
        self.assertEqual(payload['reaction'], [{'type': 'emoji', 'emoji': '👍'}])
        self.assertEqual(payload['chat_id'], -1001)
        # id сообщения приходит из апдейта — Telegram ждёт число.
        self.assertEqual(payload['message_id'], 900)

    def test_empty_emoji_removes_the_reaction(self):
        self.assertEqual(self.call_with(None)['reaction'], [])


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

    def test_card_caption(self):
        """Подпись к карточке — единственное место, откуда ИИН можно
        скопировать: с картинки его не выделить."""
        text = telegram.build_card_caption(
            ticket_id=10,
            data_rows=[{'label': 'ИИН', 'value': self.IIN},
                       {'label': 'Таксопарк', 'value': 'iTaxi'}])
        self.assertIn(self.IIN, text)

    def test_no_other_group_message_builder_appeared(self):
        """Список видов сообщений закрыт: появится новый — этот тест упадёт, и
        про ИИН в нём не забудут."""
        builders = sorted(name for name in dir(telegram) if name.startswith('build_'))
        self.assertEqual(builders, ['build_card_caption', 'build_reply_message',
                                    'build_status_notice', 'build_ticket_message'])


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
