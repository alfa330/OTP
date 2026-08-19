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

from crm import service


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
                 message_id=1, status_changed=True):
        self.db = db
        self.calls = []
        self._payload = payload
        self._ticket = ticket
        self._found = found
        self._message_id = message_id
        self._status_changed = status_changed

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

    def __init__(self, db, result=None, error=None):
        self.db = db
        self.sent = []
        self.edited = []
        self._result = result if result is not None else {'message_id': 555}
        self._error = error

    def _guard(self):
        if self.db.depth != 0:
            raise PoolViolation('сеть вызвана с открытым курсором')

    def send_message(self, chat_id, text, **kwargs):
        self._guard()
        self.sent.append({'chat_id': chat_id, 'text': text, **kwargs})
        return (None, self._error) if self._error else (self._result, None)

    def edit_reply_markup(self, chat_id, message_id, markup=None):
        self._guard()
        self.edited.append((chat_id, message_id, markup))
        return {}, None

    def send_attachment(self, chat_id, **kwargs):
        self._guard()
        return self._result, None


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
             status_changed=True, result=None, error=None):
        service.queries = FakeQueries(
            self.db, payload=payload, ticket=ticket, found=found,
            message_id=message_id, status_changed=status_changed,
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

    def test_message_carries_buttons(self):
        """Кнопка «Выполнено» и есть «мгновенное уведомление о выполнении»."""
        _queries, transport = self.wire(payload=dict(PAYLOAD))
        service.deliver_ticket(self.db, 42)
        markup = transport.sent[0].get('reply_markup')
        self.assertIsNotNone(markup)
        labels = [b['text'] for b in markup['inline_keyboard'][0]]
        self.assertTrue(any('Выполнено' in label for label in labels))

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


class GroupButtonTest(ServiceCase):
    TICKET = {'tg_chat_id': -1001, 'tg_message_id': 555, 'status': 'open', 'created_by': 10}

    def test_done_resolves_and_notifies_the_author(self):
        queries, transport = self.wire(ticket=dict(self.TICKET))
        text, ok = service.apply_group_action(self.db, 'done', 42, {'first_name': 'Аружан'})

        self.assertTrue(ok)
        self.assertIn('уведомлён', text)
        changed = queries.find('set_status')[0]
        self.assertEqual(changed['status'], 'resolved')
        self.assertTrue(changed['notify_author'])
        self.assertEqual(changed['unread_kind'], 'done')

    def test_take_moves_to_in_progress(self):
        queries, _transport = self.wire(ticket=dict(self.TICKET))
        _text, ok = service.apply_group_action(self.db, 'work', 42, {'username': 'aru'})
        self.assertTrue(ok)
        self.assertEqual(queries.find('set_status')[0]['status'], 'in_progress')

    def test_repeated_press_changes_nothing(self):
        """Второе «Выполнено» — не событие: ни истории, ни звонка автору."""
        queries, transport = self.wire(ticket=dict(self.TICKET), status_changed=False)
        text, ok = service.apply_group_action(self.db, 'done', 42, {'first_name': 'А'})

        self.assertTrue(ok)
        self.assertEqual(text, 'Уже отмечено')
        self.assertEqual(queries.find('add_event'), [])
        self.assertEqual(transport.edited, [])

    def test_buttons_are_refreshed_after_the_status_changed(self):
        _queries, transport = self.wire(ticket=dict(self.TICKET))
        service.apply_group_action(self.db, 'done', 42, {'first_name': 'А'})
        self.assertEqual(len(transport.edited), 1)
        # У решённого кнопок нет — Telegram получает пустую разметку.
        self.assertIsNone(transport.edited[0][2])

    def test_unknown_action_is_rejected_without_touching_the_ticket(self):
        queries, _transport = self.wire(ticket=dict(self.TICKET))
        _text, ok = service.apply_group_action(self.db, 'delete_everything', 42, None)
        self.assertFalse(ok)
        self.assertEqual(queries.calls, [])

    def test_missing_ticket_is_reported(self):
        self.wire(ticket=None)
        text, ok = service.apply_group_action(self.db, 'done', 42, None)
        self.assertFalse(ok)
        self.assertIn('не найдено', text)

    def test_editing_buttons_happens_outside_the_cursor(self):
        self.wire(ticket=dict(self.TICKET))
        service.apply_group_action(self.db, 'done', 42, {'first_name': 'А'})
        self.assertEqual(self.db.depth, 0)
        self.assertEqual(self.db.max_depth, 1)


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
