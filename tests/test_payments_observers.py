# -*- coding: utf-8 -*-
"""Наблюдатели заявки «Оплаты счетов» — просьба владельца 15.10.2026.

«Ты уходишь в отпуск и добавляешь одного или нескольких наблюдателей, чтобы они
следили за прогрессом; инициатор заявки сам может добавить их в начале и в
прогрессе заявки».

Здесь — без базы (unittest.mock, как в test_payments_closing_wiring.py):
* права: кто добавляет и снимает, кому наблюдение открывает заявку;
* что делает flow.change_observers — проверки, история, колокол добавленным,
  погашенные сведения у снятых;
* какие уведомления о ходе заявки доходят до наблюдателей (колокол «к сведению»,
  без Telegram) и где они к ним подключены;
* ручки: создание и правка со списком, «добавить» и «снять», видимость карточки.

Что SQL наблюдателей ЗНАЧИТ (реестр, «Наблюдаю», каскады) — на настоящем Postgres,
tests/test_payments_observers_postgres.py.
"""

import contextlib
import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from flask import Flask

from payments import access, flow, notify, queries as real_queries, routes, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
INITIATOR, WATCHER, SECOND, OUTSIDER, APPROVER, FIRED = 10, 20, 21, 30, 40, 50
REQUEST_ID = 905


def request_row(**overrides):
    row = {
        'id': REQUEST_ID, 'status': 'active', 'stage': 'approval', 'request_kind': 'purchase',
        'payment_method': 'invoice', 'object_type': 'service', 'expense_name': 'Бумага А4', 'amount': 25000,
        'initiator_id': INITIATOR, 'initiator_name': 'Ерсултан Айгерим', 'submitted_at': '2026-10-15T10:00:00',
        'closing_docs_status': 'none', 'has_card': False, 'current_role_code': 'approver',
    }
    row.update(overrides)
    return row


def ctx(user_id):
    return {'user_id': user_id, 'name': 'Тест', 'role': 'operator'}


class WiderPerimeter(unittest.TestCase):
    """Раздел открыт не только администраторам — как будет после пилота."""

    def setUp(self):
        people = (INITIATOR, WATCHER, SECOND, APPROVER)
        patcher = mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS',
                                    tuple(access.SECTION_ALLOWED_USER_IDS) + people)
        patcher.start()
        self.addCleanup(patcher.stop)


# ─── Права ───────────────────────────────────────────────────────────────────

class ObserverRightsTests(WiderPerimeter):
    def test_initiator_and_admin_manage_while_request_is_active(self):
        """«Инициатор сам может добавить их в начале и в прогрессе» — пока заявка в работе,
        на любом этапе, а не только пока она у него (правка полей после отправки закрыта)."""
        for stage in ('initiation', 'approval', 'payment', 'closing'):
            row = request_row(stage=stage)
            self.assertTrue(access.can_manage_observers(ctx(INITIATOR), row), stage)
            self.assertTrue(access.can_manage_observers(ctx(ADMIN), row), 'администратор раздела — за любого')
        self.assertFalse(access.can_edit_request(ctx(INITIATOR), request_row(stage='payment')),
                         'а поля заявки на этом этапе уже не правятся — наблюдатели живут отдельно')

    def test_nobody_else_manages(self):
        row = request_row()
        for user_id in (WATCHER, APPROVER, OUTSIDER, None):
            self.assertFalse(access.can_manage_observers(ctx(user_id), row), user_id)
        self.assertFalse(access.can_manage_observers(ctx(INITIATOR), None))

    def test_closed_request_keeps_its_observers(self):
        for status in ('done', 'rejected', 'cancelled'):
            row = request_row(status=status)
            self.assertFalse(access.can_manage_observers(ctx(INITIATOR), row), status)
            self.assertFalse(access.can_manage_observers(ctx(ADMIN), row), status)

    def test_observing_opens_the_request_and_nothing_else(self):
        row = request_row()
        self.assertFalse(access.can_see_request(ctx(WATCHER), row))
        self.assertTrue(access.can_see_request(ctx(WATCHER), row, observer=True))
        self.assertFalse(access.can_see_request(ctx(WATCHER), None, observer=True))
        # Ничего, кроме просмотра: ни правки, ни отмены, ни файлов, ни номера карты.
        self.assertFalse(access.can_edit_request(ctx(WATCHER), request_row(stage='initiation')))
        self.assertFalse(access.can_cancel_request(ctx(WATCHER), row))
        self.assertFalse(access.can_attach(ctx(WATCHER), row))
        self.assertFalse(access.can_view_card_number(ctx(WATCHER), row))


# ─── Тексты ──────────────────────────────────────────────────────────────────

class ObserverTextsTests(unittest.TestCase):
    def test_added_observer_is_told_whose_request_it_is(self):
        event = notify.watching(request_row())
        self.assertEqual(event['kind'], 'watching')
        self.assertEqual(event['title'], 'Вас добавили наблюдателем заявки')
        # Сумма — с неразрывными пробелами, как везде в разделе (workflow.fmt_money).
        self.assertEqual(event['body'], '№%s · Бумага А4 · 25 000 ₸ · инициатор: Ерсултан Айгерим' % REQUEST_ID)
        self.assertEqual(notify.watching(request_row(initiator_name=None))['body'], notify.brief(request_row()))

    def test_imperative_titles_become_statements_for_observers(self):
        """«Приложите закрывающие документы», «Требуется уточнение» — дело инициатора;
        наблюдателю — что происходит."""
        docs = notify.docs_needed(request_row())
        self.assertEqual(notify.for_observer(docs)['title'], 'Бухгалтерия ждёт закрывающие документы')
        self.assertEqual(docs['title'], 'Приложите закрывающие документы', 'событие инициатора не меняется')
        ask = notify.clarification(request_row(), reason_label='Нет счёта', comment='', by_label='Бухгалтерия')
        self.assertEqual(notify.for_observer(ask)['title'], 'Инициатора попросили уточнить заявку')
        for event in (notify.approved(request_row(), by_name='А'), notify.paid(request_row(paid_on='2026-10-15')),
                      notify.returned(request_row(), by_name='А', comment='уточните'),
                      notify.rejected(request_row(), by_name='А', comment=None), notify.closed(request_row()),
                      notify.cancelled(request_row()), notify.overdue(request_row(due_on='2026-10-01'), 3)):
            self.assertIs(notify.for_observer(event), event, event['kind'])

    def test_initiator_tasks_tell_observers_what_the_request_waits_for(self):
        """Разбор 15.10.2026: чек и получение — задачи инициатора, и наблюдатель должен знать,
        что заявка ждёт его (ради отпуска их и добавляют). Чужие задачи наблюдателю не копируются."""
        for kind, title in ((wf.KIND_RECEIVING, 'Заявка ждёт подтверждения получения от инициатора'),
                            (wf.KIND_RECEIPT, 'Заявка ждёт чек от инициатора')):
            task = notify.task(request_row(), {'kind': kind})
            self.assertEqual(task['subtask'], kind)
            seen = notify.for_observer(task)
            self.assertEqual((seen['kind'], seen['title']), ('waits_initiator', title))
            self.assertEqual(task['kind'], 'task', 'задача инициатора остаётся задачей')
        payment = notify.task(request_row(), {'kind': wf.KIND_INVOICE})
        self.assertIs(notify.for_observer(payment), payment)


# ─── flow.change_observers ───────────────────────────────────────────────────

class ChangeObserversTests(WiderPerimeter):
    def setUp(self):
        super().setUp()
        self.queries = mock.MagicMock()
        self.queries.list_observers.return_value = [
            {'user_id': WATCHER, 'name': 'Наблюдаев Нурлан', 'department_name': 'IT', 'fired': False}]
        self.queries.users_brief.side_effect = lambda _c, ids: {
            uid: {'id': uid, 'name': 'Человек %s' % uid, 'fired': uid == FIRED} for uid in ids if uid != 999}
        self.queries.add_observers.side_effect = lambda _c, _r, ids, _a: sorted(ids)
        self.queries.remove_observers.side_effect = lambda _c, _r, ids: sorted(ids)
        self.notices = mock.MagicMock()
        for patcher in (mock.patch.object(flow, 'queries', self.queries),
                        mock.patch.object(flow, 'notices', self.notices)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.actor = {'id': INITIATOR, 'name': 'Ерсултан Айгерим'}

    def change(self, add=(), remove=(), actor=None):
        return flow.change_observers(mock.MagicMock(), request_row(), actor or self.actor, add=add, remove=remove)

    def test_add_logs_one_event_and_tells_the_added(self):
        change = self.change(add=[SECOND, WATCHER, SECOND])
        self.assertEqual(change, {'added': [{'id': SECOND, 'name': 'Человек %s' % SECOND}], 'removed': []})
        self.queries.add_observers.assert_called_once_with(mock.ANY, REQUEST_ID, [SECOND], INITIATOR)
        self.queries.remove_observers.assert_called_once_with(mock.ANY, REQUEST_ID, [])
        self.queries.log_event.assert_called_once_with(mock.ANY, REQUEST_ID, 'observers', self.actor, payload=change)
        push = self.notices.push.call_args.kwargs
        self.assertEqual(push['user_ids'], {SECOND})
        self.assertEqual(push['kind'], 'watching')
        self.assertFalse(push['actionable'], 'наблюдателю — «к сведению», задач у него нет')

    def test_remove_dims_the_bell_of_the_removed(self):
        change = self.change(remove=[WATCHER])
        self.assertEqual(change, {'added': [], 'removed': [{'id': WATCHER, 'name': 'Наблюдаев Нурлан'}]})
        self.notices.mark_seen.assert_called_once_with(mock.ANY, WATCHER, REQUEST_ID)
        self.notices.push.assert_not_called()

    def test_only_the_named_are_removed(self):
        """Разбор 15.10.2026: форма доработки присылала список целиком, и наблюдатель,
        которого администратор добавил, пока форма была открыта, молча снимался. Теперь
        снимается только названный — добавленные в окне заявки остаются."""
        self.queries.list_observers.return_value = [
            {'user_id': WATCHER, 'name': 'Наблюдаев Нурлан', 'department_name': 'IT', 'fired': False},
            {'user_id': OUTSIDER, 'name': 'Добавленный в окне', 'department_name': None, 'fired': False}]
        change = self.change(add=[SECOND])
        self.assertEqual(change['removed'], [])
        self.queries.remove_observers.assert_called_once_with(mock.ANY, REQUEST_ID, [])
        self.notices.mark_seen.assert_not_called()

    def test_nothing_changed_writes_nothing(self):
        self.assertEqual(self.change(add=[WATCHER]), {'added': [], 'removed': []})
        self.assertEqual(self.change(remove=[SECOND]), {'added': [], 'removed': []}, 'снимать того, кого нет, — не событие')
        self.assertEqual(self.change(add=[WATCHER], remove=[WATCHER]), {'added': [], 'removed': []},
                         'и добавить, и снять одного — он остаётся')
        self.queries.log_event.assert_not_called()
        self.notices.push.assert_not_called()

    def test_initiator_unknown_and_fired_are_refused(self):
        for ids, code, status in (([INITIATOR], 'PAYMENT_OBSERVER_INVALID', 400),
                                  ([999], 'PAYMENT_OBSERVER_INVALID', 404),
                                  ([FIRED], 'PAYMENT_OBSERVER_INVALID', 404)):
            with self.assertRaises(flow.FlowError) as caught:
                self.change(add=ids)
            self.assertEqual((caught.exception.code, caught.exception.status), (code, status), ids)
        self.queries.add_observers.assert_not_called()

    def test_fired_observer_already_in_the_list_can_stay_or_go(self):
        """Уволенный, уже стоящий в списке, проверку «действующий» не проходит заново —
        иначе его не снять и не сохранить форму рядом с ним."""
        self.queries.list_observers.return_value = [
            {'user_id': FIRED, 'name': 'Бывшев Борис', 'department_name': None, 'fired': True}]
        self.assertEqual(self.change(add=[FIRED, SECOND])['added'], [{'id': SECOND, 'name': 'Человек %s' % SECOND}])
        self.assertEqual(self.change(remove=[FIRED])['removed'], [{'id': FIRED, 'name': 'Бывшев Борис'}])

    def test_limit_counts_what_stays(self):
        self.queries.list_observers.return_value = [
            {'user_id': 100 + i, 'name': 'Н%s' % i, 'department_name': None, 'fired': False}
            for i in range(wf.MAX_OBSERVERS)]
        with self.assertRaises(flow.FlowError) as caught:
            self.change(add=[SECOND])
        self.assertEqual(caught.exception.code, 'PAYMENT_OBSERVERS_LIMIT')
        # Заменить одного другим — можно: список остаётся в пределе.
        self.assertEqual(len(self.change(add=[SECOND], remove=[100])['added']), 1)

    def test_admin_adding_tells_everyone_added_but_not_himself(self):
        self.change(add=[ADMIN, SECOND], actor={'id': ADMIN, 'name': 'Админ'})
        self.assertEqual(self.notices.push.call_args.kwargs['user_ids'], {SECOND})

    def test_observer_outside_the_perimeter_gets_no_bell(self):
        self.change(add=[OUTSIDER])
        self.notices.push.assert_not_called()


# ─── Ход заявки — наблюдателям ───────────────────────────────────────────────

class ObserversHearProgressTests(WiderPerimeter):
    def setUp(self):
        super().setUp()
        self.queries = mock.MagicMock()
        self.queries.observer_ids.return_value = {WATCHER, SECOND, OUTSIDER}
        self.queries.telegram_recipients.side_effect = lambda _c, user_ids=(): [
            {'user_id': uid, 'chat_id': 'chat-%s' % uid} for uid in user_ids]
        self.notices = mock.MagicMock()
        for patcher in (mock.patch.object(flow, 'queries', self.queries),
                        mock.patch.object(flow, 'notices', self.notices)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def pushes(self):
        return [call.kwargs for call in self.notices.push.call_args_list]

    def test_initiator_event_is_copied_to_observers_without_telegram(self):
        outbox = []
        event = notify.returned(request_row(), by_name='Утверждающий', comment='уточните сумму')
        flow._notify_initiator(mock.MagicMock(), request_row(), event, outbox=outbox, base_url='https://x',
                               actor={'id': SECOND}, subtask_id=7, actionable=True)
        initiator, observers = self.pushes()
        self.assertEqual(initiator['user_ids'], {INITIATOR})
        self.assertTrue(initiator['actionable'])
        self.assertEqual(observers['user_ids'], {WATCHER}, 'без автора действия и без тех, кому раздел закрыт')
        self.assertFalse(observers['actionable'])
        self.assertEqual(observers['subtask_id'], 7, 'копия «заявка ждёт инициатора» гаснет вместе с его задачей')
        flow._notify_initiator(mock.MagicMock(), request_row(), notify.closed(request_row()), outbox=[], base_url=None)
        self.assertIsNone(self.pushes()[-1]['subtask_id'], 'сведения о ходе к задаче не привязаны')
        self.assertEqual(observers['title'], 'Заявку вернули на доработку')
        self.assertEqual([chat for chat, _text, _markup in outbox], ['chat-%s' % INITIATOR],
                         'в Telegram — только инициатору: у наблюдателя задачи нет')

    def test_initiator_is_never_his_own_observer(self):
        self.queries.observer_ids.return_value = {INITIATOR, WATCHER}
        flow._notify_observers(mock.MagicMock(), request_row(), notify.closed(request_row()))
        self.assertEqual(self.pushes()[0]['user_ids'], {WATCHER})

    def test_no_observers_no_extra_rows(self):
        self.queries.observer_ids.return_value = set()
        flow._notify_observers(mock.MagicMock(), request_row(), notify.closed(request_row()))
        self.notices.push.assert_not_called()

    def test_closing_documents_reach_observers_as_a_statement(self):
        self.queries.read_request.return_value = request_row(stage='closing')
        self.queries.list_subtasks.return_value = [{'kind': wf.KIND_CLOSING, 'state': 'pending'}]
        self.queries.role_member_ids.return_value = {'accounting': {APPROVER}}
        self.queries.open_subtask.return_value = {'id': 3, 'kind': wf.KIND_CLOSING, 'role_code': 'accounting',
                                                  'assignee_id': None}
        flow.advance(mock.MagicMock(), REQUEST_ID, {'id': APPROVER, 'name': 'Бухгалтер'}, outbox=[])
        titles = {push['title']: push for push in self.pushes()}
        self.assertEqual(titles['Приложите закрывающие документы']['user_ids'], {INITIATOR})
        observers = titles['Бухгалтерия ждёт закрывающие документы']
        self.assertEqual(observers['user_ids'], {WATCHER, SECOND})
        self.assertEqual(observers['subtask_id'], 3, 'гаснет, когда бухгалтерия закроет документы')

    def test_receiving_opened_observers_learn_it_waits_for_the_initiator(self):
        self.queries.read_request.return_value = request_row(stage='receiving')
        self.queries.list_subtasks.return_value = [{'kind': wf.KIND_RECEIVING, 'state': 'pending'}]
        self.queries.role_member_ids.return_value = {}
        self.queries.open_subtask.return_value = {'id': 5, 'kind': wf.KIND_RECEIVING, 'role_code': wf.ROLE_INITIATOR,
                                                  'assignee_id': INITIATOR}
        outbox = []
        flow.advance(mock.MagicMock(), REQUEST_ID, {'id': APPROVER, 'name': 'Бухгалтер'}, outbox=outbox)
        initiator, observers = self.pushes()
        self.assertEqual((initiator['user_ids'], initiator['kind'], initiator['actionable']), ({INITIATOR}, 'task', True))
        self.assertEqual((observers['user_ids'], observers['kind'], observers['actionable'], observers['subtask_id']),
                         ({WATCHER, SECOND}, 'waits_initiator', False, 5))
        self.assertEqual(observers['title'], 'Заявка ждёт подтверждения получения от инициатора')
        self.assertEqual([chat for chat, _t, _m in outbox], ['chat-%s' % INITIATOR], 'Telegram — только инициатору')

    def test_tasks_of_others_are_not_copied_to_observers(self):
        self.queries.read_request.return_value = request_row()
        self.queries.list_subtasks.return_value = [{'kind': wf.KIND_APPROVAL, 'state': 'pending'}]
        self.queries.role_member_ids.return_value = {'approver': {APPROVER}}
        self.queries.open_subtask.return_value = {'id': 6, 'kind': wf.KIND_APPROVAL, 'role_code': 'approver',
                                                  'assignee_id': None}
        flow.advance(mock.MagicMock(), REQUEST_ID, {'id': INITIATOR, 'name': 'Ерсултан Айгерим'}, outbox=[])
        self.assertEqual([push['user_ids'] for push in self.pushes()], [{APPROVER}])

    def test_cancel_tells_observers(self):
        self.queries.read_request.return_value = request_row(current_assignee_id=APPROVER)
        flow.cancel(mock.MagicMock(), REQUEST_ID, {'id': INITIATOR, 'name': 'Ерсултан Айгерим'}, 'не нужно')
        self.assertEqual(self.pushes()[0]['user_ids'], {APPROVER, WATCHER, SECOND})

    def test_deadline_reminders_reach_observers_in_one_query(self):
        today = date(2026, 10, 15)
        self.notices.push.return_value = 1
        due = [(request_row(id=1, due_on='2026-10-16'), 'soon'), (request_row(id=2, due_on='2026-10-14'), 'overdue')]
        self.queries.due_requests.return_value = due
        self.queries.observers_by_request.return_value = {1: {WATCHER}, 2: {SECOND, OUTSIDER}}
        self.queries.role_member_ids.return_value = {}
        directory = mock.MagicMock()
        directory.get_settings.return_value = {wf.SETTING_DUE_SOON_DAYS: 2}
        with mock.patch.object(flow, 'directory', directory):
            flow.remind_deadlines(mock.MagicMock(), today=today)
        self.queries.observers_by_request.assert_called_once_with(mock.ANY, [1, 2])
        self.queries.observer_ids.assert_not_called()
        recipients = [push['user_ids'] for push in self.pushes()]
        self.assertEqual(recipients, [{INITIATOR, WATCHER}, {INITIATOR, SECOND}])


# ─── Ручки ───────────────────────────────────────────────────────────────────

class _RoutesBase(WiderPerimeter):
    """Blueprint раздела с подменённым слоем запросов и flow.change_observers."""

    def setUp(self):
        super().setUp()
        self.me = INITIATOR
        self.queries = mock.MagicMock()
        self.queries.load_access_context.side_effect = lambda _c, uid: {'user_id': uid, 'name': 'Т', 'role': 'operator'}
        self.queries.roles_of_user.return_value = set()
        self.queries.read_request.return_value = request_row()
        self.queries.role_member_ids.return_value = {'approver': {APPROVER}, 'accounting': {APPROVER}}
        self.queries.participates.return_value = False
        self.queries.observes.return_value = False
        self.queries.list_observers.return_value = [
            {'user_id': WATCHER, 'name': 'Наблюдаев Нурлан', 'department_name': 'IT', 'fired': False},
            {'user_id': OUTSIDER, 'name': 'Закрытов Захар', 'department_name': None, 'fired': False}]
        for name in ('list_attachments', 'list_items', 'list_offers', 'list_events', 'payment_history',
                     'find_duplicates', 'list_subtasks'):
            getattr(self.queries, name).return_value = []
        self.queries.avatar_refs.return_value = {WATCHER: ('bucket', 'avatars/20.jpg')}
        self.flow_set = mock.MagicMock(return_value={'added': [], 'removed': []})
        directory = mock.MagicMock()
        directory.read_contract.return_value = None
        directory.read_legal_entity.return_value = None
        directory.read_account.return_value = None
        directory.get_settings.return_value = dict(wf.SETTINGS_DEFAULTS)
        assets_sql = mock.MagicMock()
        assets_sql.for_request.return_value = []
        for patcher in (mock.patch.object(routes, 'queries', self.queries),
                        mock.patch.object(routes, 'directory', directory),
                        mock.patch.object(routes, 'assets_sql', assets_sql),
                        mock.patch.object(routes, 'notices', mock.MagicMock()),
                        mock.patch.object(flow, 'submit_problems', return_value=[]),
                        mock.patch.object(flow, 'change_observers', self.flow_set)):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.me, None, None),
            avatar_url=lambda bucket, path: 'https://photo/%s' % path))
        self.client = app.test_client()


class ObserverRoutesTests(_RoutesBase):
    """Ручки наблюдателей: права, разбор, что уходит в flow."""

    def test_card_lists_observers_with_access_marks_and_photos(self):
        body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual([(p['user_id'], p['section_open']) for p in body['observers']],
                         [(WATCHER, True), (OUTSIDER, False)])
        self.assertEqual(body['photos'], {str(WATCHER): 'https://photo/avatars/20.jpg'})
        self.assertTrue(body['permissions']['can_manage_observers'])

    def test_observer_opens_the_card_but_manages_nothing(self):
        self.me = WATCHER
        self.queries.observes.return_value = True
        response = self.client.get('/api/payments/requests/%s' % REQUEST_ID)
        self.assertEqual(response.status_code, 200)
        permissions = response.get_json()['permissions']
        self.assertFalse(any(permissions[key] for key in ('can_manage_observers', 'can_edit', 'can_cancel', 'can_act',
                                                          'can_attach', 'can_delete', 'can_refund')))
        self.queries.observes.assert_called_with(mock.ANY, REQUEST_ID, WATCHER)

    def test_stranger_does_not_learn_the_request_exists(self):
        self.me = SECOND
        self.assertEqual(self.client.get('/api/payments/requests/%s' % REQUEST_ID).status_code, 404)
        response = self.client.post('/api/payments/requests/%s/observers' % REQUEST_ID, json={'user_ids': [SECOND]})
        self.assertEqual(response.status_code, 404, 'себя в наблюдатели чужой заявки не добавить')
        self.flow_set.assert_not_called()

    def test_add_is_for_the_initiator(self):
        response = self.client.post('/api/payments/requests/%s/observers' % REQUEST_ID,
                                    json={'user_ids': [SECOND, str(OUTSIDER), SECOND]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.flow_set.call_args.kwargs, {'add': [SECOND, OUTSIDER]})
        self.queries.read_request.assert_any_call(mock.ANY, REQUEST_ID, lock=True)

    def test_observer_cannot_add_or_remove(self):
        self.me = WATCHER
        self.queries.observes.return_value = True
        self.assertEqual(self.client.post('/api/payments/requests/%s/observers' % REQUEST_ID,
                                          json={'user_ids': [SECOND]}).status_code, 403)
        self.assertEqual(self.client.delete('/api/payments/requests/%s/observers/%s' % (REQUEST_ID, WATCHER)).status_code, 403)
        self.flow_set.assert_not_called()

    def test_closed_request_is_refused_with_its_own_reason(self):
        self.queries.read_request.return_value = request_row(status='done')
        response = self.client.post('/api/payments/requests/%s/observers' % REQUEST_ID, json={'user_ids': [SECOND]})
        self.assertEqual(response.status_code, 403)
        self.assertIn('закрыта', response.get_json()['error'])

    def test_bad_lists_are_refused_before_anything_is_written(self):
        for payload in ({'user_ids': []}, {'user_ids': 'abc'}, {'user_ids': [True]}, {'user_ids': ['x']}, {}):
            response = self.client.post('/api/payments/requests/%s/observers' % REQUEST_ID, json=payload)
            self.assertEqual(response.status_code, 400, payload)
        self.flow_set.assert_not_called()

    def test_remove_names_only_the_removed(self):
        response = self.client.delete('/api/payments/requests/%s/observers/%s' % (REQUEST_ID, OUTSIDER))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.flow_set.call_args.kwargs, {'remove': [OUTSIDER]})
        self.queries.read_request.assert_any_call(mock.ANY, REQUEST_ID, lock=True)

    def test_users_list_says_who_can_open_the_section(self):
        self.queries.list_users.return_value = [{'id': WATCHER, 'name': 'А'}, {'id': OUTSIDER, 'name': 'Б'}]
        body = self.client.get('/api/payments/users').get_json()
        self.assertEqual([(u['id'], u['section_open']) for u in body['users']], [(WATCHER, True), (OUTSIDER, False)])

    def test_photos_are_signed_after_the_connection_is_released(self):
        """Подпись ссылки может ходить в сеть — её не делают при открытом соединении с базой
        (на стенде без ключа GCS она висела в транзакции, и DDL старта ждал её)."""
        signed_inside = []

        @contextlib.contextmanager
        def cursor():
            state['open'] = True
            try:
                yield mock.MagicMock()
            finally:
                state['open'] = False

        state = {'open': False}
        db = mock.MagicMock()
        db._get_cursor.side_effect = cursor
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=db, require_api_key=lambda handler: handler, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (INITIATOR, None, None),
            avatar_url=lambda bucket, path: signed_inside.append(state['open']) or 'https://photo/%s' % path))
        body = app.test_client().get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['photos'], {str(WATCHER): 'https://photo/avatars/20.jpg'})
        self.assertEqual(signed_inside, [False])

    def test_failed_signature_pauses_photos_instead_of_waiting_on_every_card(self):
        """Разбор 15.10.2026: на стенде без ключа GCS каждая подпись ждала сеть, и окно заявки
        с десятью наблюдателями открывалось 20 с. Не вышла подпись — пауза, а не новая попытка."""
        self.queries.avatar_refs.return_value = {WATCHER: ('b', 'a/20.jpg'), OUTSIDER: ('b', 'a/30.jpg')}
        calls = []
        clock = {'now': 1000.0}

        def sign(bucket, path):
            calls.append(path)
            return None  # так отвечает монолит, когда подпись не удалась

        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204), resolve_requester=lambda: (INITIATOR, None, None),
            avatar_url=sign))
        client = app.test_client()
        with mock.patch.object(routes.time, 'monotonic', side_effect=lambda: clock['now']):
            self.assertEqual(client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()['photos'], {})
            self.assertEqual(len(calls), 1, 'после первой неудачи остальных не подписываем')
            client.get('/api/payments/requests/%s' % REQUEST_ID)
            self.assertEqual(len(calls), 1, 'пока идёт пауза — ни одной попытки')
            clock['now'] += routes.PHOTO_RETRY_SECONDS + 1
            client.get('/api/payments/requests/%s' % REQUEST_ID)
            self.assertEqual(len(calls), 2, 'пауза прошла — пробуем снова')

    def test_every_card_reply_signs_the_photos(self):
        """Ответ с карточкой (_full) — только через _reply_card: иначе фото наблюдателей пропадут."""
        source = Path(routes.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count('jsonify(full'), 1, 'jsonify(full…) — только внутри _reply_card')
        self.assertEqual(source.count('full = _full('), source.count('return _reply_card(full'))

    def test_meta_carries_the_limit(self):
        self.queries.role_members.return_value = {}
        self.queries.desk_count.return_value = 0
        self.queries.own_open_count.return_value = 0
        self.queries.is_manager_somewhere.return_value = False
        with mock.patch.object(routes.schema, 'schema_ready', return_value=True):
            body = self.client.get('/api/payments/ping').get_json()
        self.assertEqual(body['meta']['max_observers'], wf.MAX_OBSERVERS)


class FormSendsObserversTests(_RoutesBase):
    """Создание и правка заявки: наблюдатели формы уходят в flow.change_observers разницей."""

    def create(self, payload):
        self.queries.role_member_ids.return_value = {'approver': {APPROVER}, 'accounting': {APPROVER}}
        self.queries.create_request.return_value = REQUEST_ID
        self.queries.save_offers.return_value = []
        base = {'request_kind': 'purchase', 'expense_name': 'Бумага', 'submit': False}
        base.update(payload)
        return self.client.post('/api/payments/requests', json=base)

    def test_created_with_observers(self):
        response = self.create({'observers_add': [WATCHER, SECOND]})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(self.flow_set.call_args.kwargs, {'add': [WATCHER, SECOND]})

    def test_created_without_observers_does_not_touch_them(self):
        self.assertEqual(self.create({}).status_code, 201)
        self.flow_set.assert_not_called()

    def test_bad_list_refuses_creation_before_the_request_is_written(self):
        self.assertEqual(self.create({'observers_add': 'Иванов'}).status_code, 400)
        self.queries.create_request.assert_not_called()

    def test_edit_sends_the_difference_only_when_there_is_one(self):
        self.queries.read_request.return_value = request_row(stage='initiation', submitted_at=None)
        self.queries.update_request_fields.return_value = {}
        response = self.client.patch('/api/payments/requests/%s' % REQUEST_ID, json={'notes': 'правка'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.flow_set.assert_not_called()
        # Форма добавила одного и сняла другого — тех, кого добавили в окне заявки, ручка не трогает.
        response = self.client.patch('/api/payments/requests/%s' % REQUEST_ID,
                                     json={'observers_add': [SECOND], 'observers_remove': [WATCHER]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.flow_set.call_args.kwargs, {'add': [SECOND], 'remove': [WATCHER]})
        response = self.client.patch('/api/payments/requests/%s' % REQUEST_ID, json={'observers_remove': [WATCHER]})
        self.assertEqual(self.flow_set.call_args.kwargs, {'add': [], 'remove': [WATCHER]})
        self.assertEqual(self.client.patch('/api/payments/requests/%s' % REQUEST_ID,
                                           json={'observers_remove': 'все'}).status_code, 400)


# ─── SQL: что запрос НАПИСАН (смысл — в *_postgres.py) ──────────────────────

def squash(sql):
    return re.sub(r'\s+', ' ', sql)


class RegistrySqlTests(unittest.TestCase):
    """CI Postgres не поднимает, и *_postgres.py там пропускается целиком. Без этих
    проверок в CI проходили бы «заявку с наблюдателем видит любой», «Наблюдаю» — чужие
    наблюдения и колокол уволенным (разбор 15.10.2026, мутации M17, M18, M20)."""

    def test_visibility_is_tied_to_the_viewer(self):
        self.assertRegex(squash(real_queries._VISIBLE_SQL),
                         r'EXISTS \( ?SELECT 1 FROM payment_request_observers (\w+) WHERE \1\.request_id = r\.id '
                         r'AND \1\.user_id = %\(visible_to\)s ?\)')

    def test_watching_slice_is_tied_to_the_viewer(self):
        self.assertRegex(squash(real_queries._WATCHING_SQL),
                         r'EXISTS \( ?SELECT 1 FROM payment_request_observers (\w+) WHERE \1\.request_id = r\.id '
                         r'AND \1\.user_id = %\(viewer_id\)s ?\)')

    def test_progress_goes_only_to_working_observers(self):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [(5, 20), (5, 21), (6, 20)]
        self.assertEqual(real_queries.observers_by_request(cursor, [6, 5, 5]), {5: {20, 21}, 6: {20}})
        sql, params = cursor.execute.call_args.args
        self.assertRegex(squash(sql), r"u\.status <> 'fired'")
        self.assertEqual(params, ([5, 6],))
        cursor.reset_mock()
        self.assertEqual(real_queries.observers_by_request(cursor, []), {})
        cursor.execute.assert_not_called()

    def test_visibility_and_watching_slice_mention_observers(self):
        self.assertIn('payment_request_observers', real_queries._VISIBLE_SQL)
        params = {}
        where = real_queries._filter_clause(params, state='watching', viewer_id=WATCHER)
        self.assertIn(real_queries._WATCHING_SQL, where)
        self.assertEqual(params['viewer_id'], WATCHER)
        self.assertNotIn(real_queries._WATCHING_SQL, real_queries._filter_clause({}, state='open', viewer_id=WATCHER))

    def test_state_counters_have_the_watching_slice(self):
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (9, 5, 1, 0, 0, 2, 1, 1, 3)
        counters = real_queries.state_counters(cursor, viewer_id=WATCHER, state='watching')
        self.assertEqual(counters['watching'], 3)
        sql, params = cursor.execute.call_args.args
        self.assertIn(real_queries._WATCHING_SQL, sql)
        self.assertEqual(params['viewer_id'], WATCHER)


if __name__ == '__main__':
    unittest.main()
