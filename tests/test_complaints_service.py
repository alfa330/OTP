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
from datetime import datetime

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
    def test_the_training_names_the_complaint(self):
        """«Что причиной была жалоба, её номер и тема» (ТЗ). Сотрудник, дата,
        время и кто провёл — поля самой записи в «Тренингах»."""
        text = service.training_comment(
            {'id': 12, 'target': 'call_center', 'reason_code': 'rude'})
        self.assertEqual(text, 'Жалоба №12 · Оператор колл-центра · '
                               'Грубость / некорректное общение')

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

    def test_plan_is_only_forward(self):
        """Назначить тренинг на прошедшее время нельзя: уведомление о нём
        пришло бы, когда всё кончилось."""
        now = datetime(2026, 9, 30, 12, 0)
        with self.assertRaises(service.ComplaintError):
            service._parse_plan({'date': '2026-09-30', 'time': '11:59'}, now)
        with self.assertRaises(service.ComplaintError):
            service._parse_plan({'date': '2026-09-31', 'time': '10:00'}, now)
        with self.assertRaises(service.ComplaintError):
            service._parse_plan({'date': '2026-10-01', 'time': '25:00'}, now)
        self.assertEqual(service._parse_plan({'date': '2026-10-01', 'time': '9:30'}, now),
                         datetime(2026, 10, 1, 9, 30))


class WorkQueries(FakeQueries):
    """Подделка SQL-слоя под запись работы: помнит, что и в каком порядке
    записано, и отдаёт жалобу с нужными фактами."""

    slot = None

    def training_at_slot(self, cursor, operator_id, day, start, end):
        return self.slot

    def append_training_comment(self, cursor, training_id, line):
        self._record('append_training_comment', training_id, line)

    def create_training(self, cursor, **kwargs):
        self._record('create_training', **kwargs)
        return 77

    def add_work_record(self, cursor, **kwargs):
        self._record('add_work_record', **kwargs)
        return 1

    def set_training_plan(self, cursor, complaint_id, planned_at):
        self._record('set_training_plan', complaint_id, planned_at)

    def set_work_flags(self, cursor, complaint_id, **kwargs):
        self._record('set_work_flags', complaint_id, **kwargs)

    def recompute_status(self, cursor, complaint_id):
        return 'open', 'open'

    def set_review(self, cursor, complaint_id, **kwargs):
        self._record('set_review', complaint_id, **kwargs)
        return self.complaint.get('review_state') == 'pending'

    def set_result(self, cursor, complaint_id, **kwargs):
        self._record('set_result', complaint_id, **kwargs)

    def set_delivery(self, cursor, complaint_id, **kwargs):
        self._record('set_delivery', complaint_id, **kwargs)

    def kwargs_of(self, name):
        return [kw for n, _a, kw in self.calls if n == name]


class RecordWorkTest(ServiceCase):
    CTX = {'user_id': 5, 'name': 'Супервайзер'}
    NOW = datetime(2026, 9, 30, 12, 0)
    COMPLAINT = {'id': 12, 'target': 'call_center', 'reason_code': 'rude',
                 'employee_id': 40, 'work_state': 'pending', 'feedback_done': False,
                 'training_required': False, 'training_done': False}

    def wire_work(self, **changes):
        service.queries = WorkQueries(complaint=dict(self.COMPLAINT, **changes))
        service.transport = FakeTransport(self.db)
        return service.queries

    def test_retired_actions_are_refused(self):
        """Кнопок три — прочие варианты ТЗ сервер больше не принимает, иначе
        «обучение не требуется» осталось бы лазейкой закрыть работу."""
        self.wire_work()
        for action in ('feedback', 'review', 'no_training', 'fired'):
            with self.assertRaises(service.ComplaintError):
                service.record_work(self.db, 12, action=action, comment='x', ctx=self.CTX)

    def test_assigning_a_training_stores_the_plan(self):
        queries = self.wire_work()
        service.record_work(self.db, 12, action='training_assigned',
                            plan={'date': '2026-10-02', 'time': '14:00'},
                            ctx=self.CTX, now=self.NOW)
        record = queries.kwargs_of('add_work_record')[0]
        self.assertEqual(record['planned_at'], datetime(2026, 10, 2, 14, 0))
        self.assertEqual(record['comment'], 'на 02.10.2026 в 14:00')
        self.assertIn(('set_training_plan', (12, datetime(2026, 10, 2, 14, 0)), {}),
                      queries.calls)
        flags = queries.kwargs_of('set_work_flags')[0]['flags']
        self.assertTrue(flags['training_required'])
        self.assertFalse(flags['closed'])
        self.assertNotIn('create_training', queries.names(),
                         'назначенный тренинг — ещё не занятие')

    def test_held_training_goes_to_trainings(self):
        queries = self.wire_work(training_required=True)
        service.record_work(self.db, 12, action='training',
                            training={'date': '2026-09-29', 'start': '10:00', 'end': '10:40'},
                            ctx=self.CTX, now=self.NOW)
        training = queries.kwargs_of('create_training')[0]
        self.assertEqual((training['operator_id'], training['reason']),
                         (40, 'Тренинг по качеству. Разбор ошибок'))
        record = queries.kwargs_of('add_work_record')[0]
        self.assertEqual((record['training_id'], record['comment']),
                         (77, '29.09.2026, 10:00–10:40'))
        flags = queries.kwargs_of('set_work_flags')[0]['flags']
        self.assertEqual((flags['training_done'], flags['training_required'], flags['closed']),
                         (True, False, True))

    def test_second_complaint_joins_the_same_session(self):
        """Две жалобы на одного сотрудника разобрали одним занятием: слот в
        «Тренингах» уникален, второй записи быть не может — вторая жалоба
        привязывается к тому же занятию и дописывается в его текст (раньше —
        отказ 409, и закрыть её было нечем)."""
        queries = self.wire_work(id=13, training_required=True)
        queries.slot = (77, 'Жалоба №12 · Оператор колл-центра · Грубость / некорректное общение')
        service.record_work(self.db, 13, action='training',
                            training={'date': '2026-09-29', 'start': '10:00', 'end': '10:40'},
                            ctx=self.CTX, now=self.NOW)
        self.assertNotIn('create_training', queries.names())
        self.assertEqual(queries.kwargs_of('add_work_record')[0]['training_id'], 77)
        appended = [a for n, a, _k in queries.calls if n == 'append_training_comment']
        self.assertEqual(appended, [(77, 'Жалоба №13 · Оператор колл-центра · '
                                         'Грубость / некорректное общение')])
        # Та же жалоба второй раз свою строку не дублирует.
        queries = self.wire_work(training_required=True)
        queries.slot = (77, 'Жалоба №12 · Оператор колл-центра · Грубость / некорректное общение')
        service.record_work(self.db, 12, action='training',
                            training={'date': '2026-09-29', 'start': '10:00', 'end': '10:40'},
                            ctx=self.CTX, now=self.NOW)
        self.assertNotIn('append_training_comment', queries.names())

    def test_other_measures_need_a_comment(self):
        self.wire_work()
        with self.assertRaises(service.ComplaintError):
            service.record_work(self.db, 12, action='other', comment='  ', ctx=self.CTX)

    def test_training_needs_an_employee(self):
        self.wire_work(employee_id=None, work_state='unassigned')
        with self.assertRaises(service.ComplaintError):
            service.record_work(self.db, 12, action='training_assigned',
                                plan={'date': '2026-10-02', 'time': '14:00'},
                                ctx=self.CTX, now=self.NOW)
        queries = self.wire_work(employee_id=None, work_state='unassigned')
        service.record_work(self.db, 12, action='other', comment='Сотрудника не нашли',
                            ctx=self.CTX)
        self.assertTrue(queries.kwargs_of('set_work_flags')[0]['flags']['closed'])

    def test_network_is_never_called_with_an_open_cursor(self):
        self.wire_work(tg_chat_id=-100, tg_message_id=900)
        service.record_work(self.db, 12, action='other', comment='Беседа', ctx=self.CTX)
        self.assertEqual(self.db.max_depth, 1)
        self.assertTrue(service.transport.sent, 'отбивка о завершённой работе ушла')


class ReviewTest(ServiceCase):
    """Жалоба на Яндекс: супервайзер решает — в группу или «Решено»."""

    CTX = {'user_id': 5, 'name': 'Супервайзер'}
    COMPLAINT = {'id': 30, 'target': 'yandex', 'reason_code': 'tariffs',
                 'review_state': 'pending', 'requires_processing': False,
                 'delivery_status': 'none', 'tg_message_id': None, 'tg_chat_id': None,
                 'driver_name': 'Сериков', 'driver_phone': '+7701', 'city': 'Алматы',
                 'description': 'Дорогие тарифы', 'created_by': 9, 'created_by_name': 'Оператор'}

    def wire_review(self, **changes):
        service.queries = WorkQueries(complaint=dict(self.COMPLAINT, **changes))
        service.transport = FakeTransport(self.db)
        return service.queries

    def test_resolve_needs_the_result_in_words(self):
        self.wire_review()
        with self.assertRaises(service.ComplaintError):
            service.review_resolve(self.db, 30, '   ', ctx=self.CTX)

    def test_resolve_writes_the_result_and_sends_nothing(self):
        queries = self.wire_review()
        service.review_resolve(self.db, 30, 'Разъяснили водителю тарифы', ctx=self.CTX)
        self.assertEqual(queries.kwargs_of('set_review')[0]['state'], 'resolved')
        result = queries.kwargs_of('set_result')[0]
        self.assertEqual((result['code'], result['note'], result['via']),
                         ('solved', 'Разъяснили водителю тарифы', 'icore'))
        self.assertEqual(service.transport.sent, [], 'в группу ничего не уходит')

    def test_second_decision_is_refused(self):
        """Два супервайзера нажали разное одновременно — второе решение не
        перезаписывает первое."""
        self.wire_review(review_state='resolved')
        with self.assertRaises(service.ComplaintError) as caught:
            service.review_resolve(self.db, 30, 'Итог', ctx=self.CTX)
        self.assertEqual(caught.exception.status, 409)

    def test_send_records_the_decision_before_delivery(self):
        queries = self.wire_review()
        # После решения сервис видит жалобу уже «в группу» — как её вернёт база.
        queries.complaint.update(requires_processing=True, review_state='sent',
                                 review_by_name='Супервайзер')
        queries.set_review = lambda cursor, complaint_id, **kw: (
            queries._record('set_review', complaint_id, **kw) or True)
        _item, error = service.review_send(self.db, 30, ctx=self.CTX)
        self.assertIsNone(error)
        self.assertEqual(queries.names()[0], 'set_review')
        sent = service.transport.sent[0]
        self.assertEqual(sent['chat_id'], -100)
        self.assertIn('Проверил и передал', sent['text'])
        self.assertEqual(self.db.max_depth, 1)


if __name__ == '__main__':
    unittest.main()
