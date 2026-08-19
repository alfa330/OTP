# -*- coding: utf-8 -*-
"""Сценарии раздела «Обращения»: доставка, приём ответа, кнопки в группе.

Проверяется не SQL (он в test_crm_access.py), а порядок действий и обещания,
которые раздел даёт пользователю:

* обращение не теряется, если Telegram лежит;
* повтор апдейта Telegram не звонит автору второй раз;
* повторное «Выполнено» не пишет вторую строку в историю;
* сеть НИКОГДА не вызывается с открытым курсором.

Последнее — не стилистика. Пул проекта делится с SSE колокола и аукциона, и он
уже голодал однажды; держать соединение те секунды, пока отвечает Telegram, —
прямой путь это повторить. Проверяется фальшивым пулом, который считает
открытые курсоры.
"""

import unittest
from contextlib import contextmanager

from crm import service, transport


class PoolViolation(AssertionError):
    pass


class FakeDb:
    """Пул, который знает, сколько курсоров открыто прямо сейчас."""

    def __init__(self):
        self.depth = 0
        self.max_depth = 0
        self.opened = 0

    @contextmanager
    def _get_cursor(self):
        self.depth += 1
        self.opened += 1
        self.max_depth = max(self.max_depth, self.depth)
        try:
            yield object()
        finally:
            self.depth -= 1


class FakeQueries:
    """Подмена SQL-слоя: записывает вызовы, отдаёт заготовленные ответы."""

    UNREAD_REPLY = 'reply'
    UNREAD_DONE = 'done'
    UNREAD_PROGRESS = 'progress'

    def __init__(self, db, payload=None, ticket=None, found=None,
                 message_id=1, status_changed=True, thread_message=None):
        self.db = db
        self.calls = []
        self._payload = payload
        self._ticket = ticket
        self._found = found
        self._message_id = message_id
        self._status_changed = status_changed
        self._thread_message = thread_message

    def _record(self, name, **kwargs):
        # Любой вызов SQL-слоя обязан идти с открытым курсором.
        if self.db.depth == 0:
            raise PoolViolation('%s вызван без курсора' % name)
        self.calls.append((name, kwargs))

    def delivery_payload(self, _cursor, ticket_id):
        self._record('delivery_payload', ticket_id=ticket_id)
        return self._payload

    def get_ticket(self, _cursor, ticket_id, viewer_id=None):
        self._record('get_ticket', ticket_id=ticket_id)
        return self._ticket

    def message_of_ticket(self, _cursor, ticket_id, message_id):
        self._record('message_of_ticket', ticket_id=ticket_id, message_id=message_id)
        return self._thread_message

    def set_delivery(self, _cursor, ticket_id, **kwargs):
        self._record('set_delivery', ticket_id=ticket_id, **kwargs)

    def add_message(self, _cursor, **kwargs):
        self._record('add_message', **kwargs)
        return self._message_id

    def add_event(self, _cursor, **kwargs):
        self._record('add_event', **kwargs)

    def touch_inbound(self, _cursor, ticket_id, **kwargs):
        self._record('touch_inbound', ticket_id=ticket_id, **kwargs)

    def touch_outbound(self, _cursor, ticket_id):
        self._record('touch_outbound', ticket_id=ticket_id)

    def set_status(self, _cursor, ticket_id, status, **kwargs):
        self._record('set_status', ticket_id=ticket_id, status=status, **kwargs)
        return self._status_changed

    def mark_seen_by_author(self, _cursor, ticket_id, user_id):
        self._record('mark_seen_by_author', ticket_id=ticket_id, user_id=user_id)
        return True

    def find_ticket_by_tg_message(self, _cursor, chat_id, message_id):
        self._record('find_ticket', chat_id=chat_id, message_id=message_id)
        return self._found

    def kinds(self):
        return [name for name, _kwargs in self.calls]

    def find(self, name):
        return [kwargs for called, kwargs in self.calls if called == name]


class FakeTransport:
    """Сеть. Падает, если её позвали с открытым курсором."""

    # Подделка обязана повторять поверхность настоящего модуля: по этому пределу
    # сервис решает, уместить ли текст в подпись к фото.
    CAPTION_LIMIT = transport.CAPTION_LIMIT

    def __init__(self, db, result=None, error=None, file_error=None):
        self.db = db
        self.sent = []
        self.files = []
        self._result = result if result is not None else {'message_id': 555}
        self._error = error
        self._file_error = file_error

    def _guard(self):
        if self.db.depth != 0:
            raise PoolViolation('сеть вызвана с открытым курсором')

    def send_message(self, chat_id, text, **kwargs):
        self._guard()
        self.sent.append({'chat_id': chat_id, 'text': text, **kwargs})
        return (None, self._error) if self._error else (self._result, None)

    def send_attachment(self, chat_id, **kwargs):
        self._guard()
        self.files.append({'chat_id': chat_id, **kwargs})
        if self._file_error:
            return None, self._file_error
        return dict(self._result, photo=[{'file_id': 'ph1'}]), None


PAYLOAD = {
    'subject': 'Не приходит бонус', 'body': 'Водитель ждёт', 'priority': 'high',
    'status': 'open', 'due_at': None, 'client_name': None, 'client_phone': None,
    'created_by': 10, 'created_by_name': 'Иванов И.',
    'delivery_status': 'pending', 'tg_message_id': None,
    'chat_id': -1001, 'queue_title': 'iTaxi', 'topic_title': None,
    'department_name': 'СЗоВ', 'scenario_key': 'sapar_docs_missing',
}


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self._real_queries = service.queries
        self._real_transport = service.transport

    def tearDown(self):
        service.queries = self._real_queries
        service.transport = self._real_transport

    def wire(self, *, payload=None, ticket=None, found=None, message_id=1,
             status_changed=True, result=None, error=None, thread_message=None):
        service.queries = FakeQueries(
            self.db, payload=payload, ticket=ticket, found=found,
            message_id=message_id, status_changed=status_changed,
            thread_message=thread_message,
        )
        service.transport = FakeTransport(self.db, result=result, error=error)
        return service.queries, service.transport


class DeliveryTest(ServiceCase):
    def test_successful_delivery_records_root_message(self):
        """Корень нити обязателен: по нему находится тикет при ответе в группе."""
        queries, transport = self.wire(payload=dict(PAYLOAD))
        ok, error = service.deliver_ticket(self.db, 42)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(len(transport.sent), 1)
        added = queries.find('add_message')
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['direction'], 'out')
        self.assertEqual(added[0]['tg_message_id'], 555)
        self.assertEqual(added[0]['tg_chat_id'], -1001)

    def test_no_buttons_under_the_message(self):
        """Кнопок больше нет — убраны решением владельца 19.08.2026."""
        _queries, transport = self.wire(payload=dict(PAYLOAD))
        service.deliver_ticket(self.db, 42)
        self.assertNotIn('reply_markup', transport.sent[0])

    def test_photo_goes_with_the_text_in_one_message(self):
        """Просьба владельца: не отдельным сообщением.

        Раньше подпись к медиа (1024 символа) текст обращения не вмещала. После
        того как текст сократили — вмещает, и два сообщения там, где хватает
        одного, в группе только мешают.
        """
        _queries, transport = self.wire(payload=dict(PAYLOAD))
        service.deliver_ticket(self.db, 42, attachment={
            'filename': 'shot.png', 'stream': b'x', 'mimetype': 'image/png'})
        self.assertEqual(len(transport.files), 1)
        self.assertEqual(transport.sent, [], 'текст ушёл вторым сообщением')
        self.assertIn('Обращение №42', transport.files[0]['caption'])
        self.assertEqual(transport.files[0]['parse_mode'], 'HTML')
        # Файл — часть корневого сообщения, а не отдельная строка переписки.
        messages = [kwargs for name, kwargs in _queries.calls if name == 'add_message']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['attachment']['kind'], 'photo')

    def test_long_text_falls_back_to_two_messages(self):
        """Подпись к медиа ограничена, и терять текст из-за этого нельзя."""
        payload = dict(PAYLOAD, body='я' * 3000)
        _queries, transport = self.wire(payload=payload)
        service.deliver_ticket(self.db, 42, attachment={
            'filename': 'shot.png', 'stream': b'x', 'mimetype': 'image/png'})
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(len(transport.files), 1)

    def test_photo_failure_still_delivers_the_ticket(self):
        """Отказ на файле не должен терять обращение — уходит текстом."""
        _queries, transport = self.wire(payload=dict(PAYLOAD))
        transport._file_error = 'file too big'
        ok, error = service.deliver_ticket(self.db, 42, attachment={
            'filename': 'shot.png', 'stream': b'x', 'mimetype': 'image/png'})
        self.assertTrue(ok, error)
        self.assertEqual(len(transport.sent), 1)

    def test_failed_delivery_keeps_the_ticket(self):
        """Telegram лежит — обращение остаётся с пометкой, а не пропадает."""
        queries, _transport = self.wire(payload=dict(PAYLOAD), error='chat not found')
        ok, error = service.deliver_ticket(self.db, 42)

        self.assertFalse(ok)
        self.assertEqual(error, 'chat not found')
        failure = queries.find('set_delivery')[0]
        self.assertEqual(failure['status'], 'failed')
        self.assertEqual(failure['error'], 'chat not found')
        self.assertIn('send_failed', [e['kind'] for e in queries.find('add_event')])
        # Сообщение в нить не легло: его не было.
        self.assertEqual(queries.find('add_message'), [])

    def test_already_sent_ticket_is_not_sent_twice(self):
        """Кнопка «отправить ещё раз» не должна плодить дубли в чужом чате."""
        payload = dict(PAYLOAD, delivery_status='sent', tg_message_id=777)
        _queries, transport = self.wire(payload=payload)
        ok, error = service.deliver_ticket(self.db, 42)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(transport.sent, [])

    def test_queue_without_chat_is_reported_not_silently_dropped(self):
        payload = dict(PAYLOAD, chat_id=None)
        _queries, transport = self.wire(payload=payload)
        ok, error = service.deliver_ticket(self.db, 42)

        self.assertFalse(ok)
        self.assertIn('Telegram', error)
        self.assertEqual(transport.sent, [])

    def test_network_is_never_called_inside_a_cursor(self):
        self.wire(payload=dict(PAYLOAD))
        service.deliver_ticket(self.db, 42)
        self.assertEqual(self.db.depth, 0)
        # Курсор берётся короткими порциями, а не одним на всю операцию.
        self.assertGreaterEqual(self.db.opened, 2)
        self.assertEqual(self.db.max_depth, 1)


class OperatorReplyTest(ServiceCase):
    """Ответ оператора — в том числе на КОНКРЕТНОЕ сообщение нити.

    В нить падает вся ветка обсуждения из группы, и «ответить вообще» там
    читается как реплика в разговор нескольких людей сразу.
    """

    def test_without_a_target_answers_the_ticket_itself(self):
        _queries, transport = self.wire(payload=dict(PAYLOAD, tg_message_id=555))
        ok, error = service.post_operator_reply(
            self.db, 42, 'уточняю', author_user_id=2, author_name='Руслан')
        self.assertTrue(ok, error)
        self.assertEqual(transport.sent[0]['reply_to_message_id'], 555)

    def test_answers_the_chosen_message(self):
        queries, transport = self.wire(
            payload=dict(PAYLOAD, tg_message_id=555),
            thread_message={'id': 7, 'tg_message_id': 777, 'body': 'а что с парком',
                            'author_name': 'Гаухар'})
        ok, error = service.post_operator_reply(
            self.db, 42, 'по парку iTaxi', author_user_id=2, author_name='Руслан',
            reply_to=7)
        self.assertTrue(ok, error)
        self.assertEqual(transport.sent[0]['reply_to_message_id'], 777)
        added = queries.find('add_message')[0]
        self.assertEqual(added['reply_to_tg_message_id'], 777)

    def test_target_is_looked_up_in_this_ticket_only(self):
        """Иначе оператор заставил бы бота ответить на любое сообщение в группе."""
        queries, transport = self.wire(payload=dict(PAYLOAD, tg_message_id=555),
                                       thread_message=None)
        ok, error = service.post_operator_reply(
            self.db, 42, 'текст', author_user_id=2, author_name='Руслан', reply_to=999)
        self.assertFalse(ok)
        self.assertIn('не найдено', error)
        self.assertEqual(transport.sent, [], 'в группу всё равно ушло')
        asked = queries.find('message_of_ticket')[0]
        self.assertEqual(asked['ticket_id'], 42)
        self.assertEqual(asked['message_id'], 999)

    def test_target_without_a_telegram_number_is_rejected(self):
        """Строка нити есть, а в Telegram её нет — отвечать не на что."""
        _queries, transport = self.wire(
            payload=dict(PAYLOAD, tg_message_id=555),
            thread_message={'id': 7, 'tg_message_id': None, 'body': 'заметка',
                            'author_name': None})
        ok, _error = service.post_operator_reply(
            self.db, 42, 'текст', author_user_id=2, author_name='Руслан', reply_to=7)
        self.assertFalse(ok)
        self.assertEqual(transport.sent, [])


class IncomingReplyTest(ServiceCase):
    FOUND = {'ticket_id': 42, 'status': 'open', 'created_by': 10, 'subject': 'тема'}

    def message(self, **kwargs):
        base = {'message_id': 900, 'text': 'ответили', 'from': {'first_name': 'Аружан'}}
        base.update(kwargs)
        return base

    def test_reply_lands_in_the_thread_and_wakes_the_author(self):
        queries, _transport = self.wire(found=dict(self.FOUND))
        accepted = service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=555, message=self.message())

        self.assertEqual(accepted['ticket_id'], 42)
        added = queries.find('add_message')[0]
        self.assertEqual(added['direction'], 'in')
        self.assertEqual(added['author_name'], 'Аружан')
        self.assertEqual(queries.find('touch_inbound')[0]['unread_kind'], 'reply')

    def test_first_reply_gets_a_receipt_and_the_rest_stay_quiet(self):
        """Расписка один раз на обращение: дальше в группе идёт живой разговор."""
        _queries, _transport = self.wire(found=dict(self.FOUND, status='open'))
        first = service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=555, message=self.message())
        self.assertTrue(first['announce'])

        self.wire(found=dict(self.FOUND, status='answered'))
        later = service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=555, message=self.message())
        self.assertFalse(later['announce'])

    def test_duplicate_update_does_not_wake_the_author_again(self):
        """Telegram штатно повторяет апдейт — второй звонок был бы ложным."""
        queries, _transport = self.wire(found=dict(self.FOUND), message_id=None)
        accepted = service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=555, message=self.message())

        self.assertEqual(accepted['ticket_id'], 42)
        self.assertFalse(accepted['announce'])
        self.assertEqual(queries.find('touch_inbound'), [])
        self.assertEqual(queries.find('add_event'), [])

    def test_reply_to_a_foreign_message_is_not_ours(self):
        """Реплай на отчёт другого раздела — не ошибка, просто не наше дело."""
        queries, _transport = self.wire(found=None)
        self.assertIsNone(service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=1, message=self.message()))
        self.assertEqual(queries.find('add_message'), [])

    def test_empty_reply_is_ignored(self):
        """Реплай без текста и без файла (например, стикер-реакция) — не ответ."""
        queries, _transport = self.wire(found=dict(self.FOUND))
        self.assertIsNone(service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=555,
            message={'message_id': 901, 'from': {'first_name': 'Аружан'}}))
        self.assertEqual(queries.calls, [])

    def test_screenshot_without_caption_is_accepted(self):
        queries, _transport = self.wire(found=dict(self.FOUND))
        accepted = service.ingest_group_reply(
            self.db, chat_id=-1001, reply_to_message_id=555,
            message=self.message(text=None, photo=[{'file_id': 'big', 'file_size': 10}]))

        self.assertEqual(accepted['ticket_id'], 42)
        self.assertEqual(queries.find('add_message')[0]['attachment']['file_id'], 'big')


class SystemStatusTest(ServiceCase):
    TICKET = {'tg_chat_id': -1001, 'tg_message_id': 555, 'status': 'answered', 'created_by': 10}

    def test_closing_from_icore_tells_the_group(self):
        """Иначе коллеги продолжат разбираться с уже решённым вопросом."""
        _queries, transport = self.wire(ticket=dict(self.TICKET))
        ok, error = service.change_status_from_system(
            self.db, 42, 'resolved', actor_user_id=10, actor_name='Иванов И.')

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(len(transport.sent), 1)
        self.assertIn('решено', transport.sent[0]['text'])
        self.assertEqual(transport.sent[0]['reply_to_message_id'], 555)

    def test_author_closing_clears_own_unread(self):
        """Он только что всё видел — точка «непрочитано» была бы враньём."""
        queries, _transport = self.wire(ticket=dict(self.TICKET))
        service.change_status_from_system(
            self.db, 42, 'resolved', actor_user_id=10, actor_name='Иванов И.')
        self.assertEqual(queries.find('mark_seen_by_author')[0]['user_id'], 10)

    def test_reopening_does_not_spam_the_group(self):
        """Отбивка нужна только при закрытии: «снова в работе» группа увидит по нити."""
        _queries, transport = self.wire(ticket=dict(self.TICKET))
        service.change_status_from_system(
            self.db, 42, 'open', actor_user_id=10, actor_name='Иванов И.')
        self.assertEqual(transport.sent, [])

    def test_author_is_not_notified_about_own_action(self):
        queries, _transport = self.wire(ticket=dict(self.TICKET))
        service.change_status_from_system(
            self.db, 42, 'resolved', actor_user_id=10, actor_name='Иванов И.')
        self.assertFalse(queries.find('set_status')[0]['notify_author'])


class SlaTest(unittest.TestCase):
    def test_no_sla_means_no_due_date(self):
        self.assertIsNone(service.compute_due_at(None))
        self.assertIsNone(service.compute_due_at(0))

    def test_sla_is_counted_from_now(self):
        from datetime import datetime
        due = service.compute_due_at(120)
        self.assertGreater(due, datetime.now())
        self.assertLess((due - datetime.now()).total_seconds(), 2 * 3600 + 5)


if __name__ == '__main__':
    unittest.main()
