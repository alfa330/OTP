# -*- coding: utf-8 -*-
"""Раздел «Жалобы»: сценарии сервиса на подделках базы и Telegram.

Три правила, которые проще всего сломать правкой «по мелочи»:

* что уходит оператору, решает ТО, НА ЧТО ответили в группе: ответ на
  приглашение «Ответ водителю» — ответ, всё остальное — внутреннее обсуждение;
* кнопка действует только на своё сообщение (callback_data можно подделать);
* сеть не вызывается с открытым курсором — пул делят SSE колокола и аукциона.
"""

import contextlib
import unittest

from complaints import service, telegram


class FakeDb:
    def __init__(self):
        self.depth = 0
        self.max_depth = 0

    @contextlib.contextmanager
    def _get_cursor(self):
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        try:
            yield object()
        finally:
            self.depth -= 1


class FakeQueries:
    UNREAD_ANSWER = 'answer'
    UNREAD_QUESTION = 'question'

    def __init__(self, found=None, complaint=None):
        self.found = found
        self.complaint = complaint or {}
        self.calls = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def find_by_tg_message(self, cursor, chat_id, message_id):
        return self.found

    def add_message(self, cursor, **kwargs):
        self._record('add_message', **kwargs)
        return 1

    def mark_answer(self, cursor, complaint_id):
        self._record('mark_answer', complaint_id)

    def set_question_open(self, cursor, complaint_id, is_open):
        self._record('set_question_open', complaint_id, is_open)

    def notify_author(self, cursor, complaint_id, kind):
        self._record('notify_author', complaint_id, kind)

    def add_event(self, cursor, **kwargs):
        self._record('add_event', **kwargs)

    def touch_activity(self, cursor, complaint_id):
        self._record('touch_activity', complaint_id)

    def get_complaint(self, cursor, complaint_id, viewer_id=None):
        return dict(self.complaint)

    def settings(self, cursor):
        return {'chat_id': -100, 'chat_title': 'Жалобы', 'mention_usernames': []}

    def names(self):
        return [name for name, _a, _k in self.calls]


class FakeTransport:
    def __init__(self, db):
        self.db = db
        self.sent = []

    def send_message(self, chat_id, text, **kwargs):
        assert self.db.depth == 0, 'сеть вызвана с открытым курсором'
        self.sent.append({'chat_id': chat_id, 'text': text, **kwargs})
        return {'message_id': 555}, None

    def edit_message(self, chat_id, message_id, **kwargs):
        assert self.db.depth == 0, 'сеть вызвана с открытым курсором'
        self.sent.append({'edit': message_id, **kwargs})
        return {}, None


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self._queries, self._transport = service.queries, service.transport

    def tearDown(self):
        service.queries, service.transport = self._queries, self._transport

    def wire(self, **kwargs):
        service.queries = FakeQueries(**kwargs)
        service.transport = FakeTransport(self.db)
        return service.queries, service.transport


class IngestTest(ServiceCase):
    MESSAGE = {'message_id': 7, 'text': 'Извинились перед водителем',
               'from': {'id': 1, 'first_name': 'Айгуль'}}

    def test_reply_to_the_answer_prompt_goes_to_the_operator(self):
        queries, _ = self.wire(found={'complaint_id': 12, 'kind': 'prompt',
                                      'prompt_kind': telegram.PROMPT_ANSWER})
        result = service.ingest_group_reply(self.db, chat_id=-100, reply_to_message_id=5,
                                            message=self.MESSAGE)
        self.assertEqual(result, {'complaint_id': 12, 'kind': 'answer'})
        self.assertIn(('notify_author', (12, 'answer'), {}), queries.calls)

    def test_reply_to_the_question_prompt_opens_a_question(self):
        queries, _ = self.wire(found={'complaint_id': 12, 'kind': 'prompt',
                                      'prompt_kind': telegram.PROMPT_QUESTION})
        result = service.ingest_group_reply(self.db, chat_id=-100, reply_to_message_id=5,
                                            message=self.MESSAGE)
        self.assertEqual(result['kind'], 'question')
        self.assertIn(('set_question_open', (12, True), {}), queries.calls)

    def test_reply_to_anything_else_stays_internal(self):
        """Реплай на саму жалобу, на отбивку бота или на ответ оператора —
        внутреннее обсуждение, и оператору о нём не звонят."""
        for kind in ('root', 'notice', 'operator_reply', 'internal', 'answer'):
            queries, _ = self.wire(found={'complaint_id': 12, 'kind': kind, 'prompt_kind': None})
            result = service.ingest_group_reply(self.db, chat_id=-100, reply_to_message_id=5,
                                                message=self.MESSAGE)
            self.assertEqual(result['kind'], 'internal', kind)
            self.assertNotIn('notify_author', queries.names(), kind)

    def test_foreign_thread_is_not_ours(self):
        self.wire(found=None)
        self.assertIsNone(service.ingest_group_reply(self.db, chat_id=-100,
                                                     reply_to_message_id=5, message=self.MESSAGE))

    def test_empty_message_is_skipped(self):
        self.wire(found={'complaint_id': 12, 'kind': 'root', 'prompt_kind': None})
        self.assertIsNone(service.ingest_group_reply(
            self.db, chat_id=-100, reply_to_message_id=5, message={'message_id': 7}))


class CallbackTest(ServiceCase):
    COMPLAINT = {'id': 12, 'tg_chat_id': -100, 'tg_message_id': 900, 'status': 'open',
                 'target': 'car_rental', 'reason_code': 'terms'}

    def test_button_under_another_message_is_refused(self):
        self.wire(complaint=self.COMPLAINT)
        text = service.handle_callback(self.db, chat_id=-100, message_id=901,
                                       data='cmp:a:12', from_user={'id': 1})
        self.assertEqual(text, 'Эта кнопка относится к другой жалобе')
        self.assertEqual(service.transport.sent, [])

    def test_foreign_button_is_not_ours(self):
        self.wire(complaint=self.COMPLAINT)
        self.assertIsNone(service.handle_callback(self.db, chat_id=-100, message_id=900,
                                                  data='editsv_5', from_user={'id': 1}))

    def test_answer_button_posts_a_prompt_outside_the_cursor(self):
        queries, transport = self.wire(complaint=self.COMPLAINT)
        service.handle_callback(self.db, chat_id=-100, message_id=900, data='cmp:a:12',
                                from_user={'id': 777, 'first_name': 'Айгуль'})
        self.assertEqual(transport.sent[0]['reply_to_message_id'], 900)
        self.assertTrue(transport.sent[0]['reply_markup']['force_reply'])
        prompt = [kw for name, _a, kw in queries.calls if name == 'add_message'][0]
        self.assertEqual((prompt['kind'], prompt['prompt_kind'], prompt['tg_message_id']),
                         ('prompt', 'answer', 555))
        self.assertEqual(self.db.max_depth, 1)

    def test_closed_complaint_still_takes_an_answer(self):
        """Итог поставили раньше разъяснения — «Ответ водителю» всё равно
        работает, иначе цикл ТЗ обрывался бы на закрытии."""
        queries, transport = self.wire(complaint=dict(self.COMPLAINT, status='closed'))
        text = service.handle_callback(self.db, chat_id=-100, message_id=900,
                                       data='cmp:a:12', from_user={'id': 1})
        self.assertIn('ответом', text)
        self.assertTrue(transport.sent[0]['reply_markup']['force_reply'])


class TrainingCommentTest(unittest.TestCase):
    def test_what_the_specification_asks_to_see_in_the_training(self):
        """«По какому сотруднику, что причиной была жалоба, номер обращения,
        тема жалобы, какая ОС проведена, проводилось ли доп. обучение, дата,
        кто провёл, результат». Сотрудник, дата и кто провёл — поля записи."""
        text = service.training_comment(
            {'id': 12, 'target': 'call_center', 'reason_code': 'rude'},
            action='feedback', comment='Разобрали звонок', outcome='Признал ошибку',
            need_training=True)
        self.assertEqual(text.split('\n'), [
            'Жалоба №12 · Оператор колл-центра · Грубость / некорректное общение',
            'Обратная связь: Разобрали звонок',
            'Дополнительное обучение: назначено',
            'Результат: Признал ошибку',
        ])

    def test_training_slot_must_make_sense(self):
        with self.assertRaises(service.ComplaintError):
            service._parse_training({'date': '2026-09-28', 'start': '10:00', 'end': '09:59'},
                                    'Обратная связь')
        with self.assertRaises(service.ComplaintError):
            service._parse_training({'date': '2026-09-28', 'start': '10:00', 'end': '11:00',
                                     'reason': 'Собрание'}, None)
        day, start, end, reason = service._parse_training(
            {'date': '2026-09-28', 'start': '9:05', 'end': '9:35'}, 'Обратная связь')
        self.assertEqual((str(day), start.strftime('%H:%M'), end.strftime('%H:%M'), reason),
                         ('2026-09-28', '09:05', '09:35', 'Обратная связь'))


if __name__ == '__main__':
    unittest.main()
