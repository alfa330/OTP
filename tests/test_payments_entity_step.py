# -*- coding: utf-8 -*-
"""Компанию не знает инициатор — её указывает бухгалтерия (просьба владельца 15.10.2026).

«В разделе «Оплата счетов» при создании заявки, если инициатор знает, для какой
компании закуп, — указывает компанию; если нет — создаётся дополнительный шаг: заявка
уходит в бухгалтерию, и там уточняют, для какой компании закуп».

Правила шага — в workflow.py (их проверяет test_payments_workflow.EntityStepTests).
Здесь — проводка, которой человек пользуется на самом деле: отправка заявки строит
маршрут с бухгалтерией первой, действие бухгалтерии пишет компанию и строит маршрут
заново (согласующего выбирает матрица уже с компанией), ручка предпросмотра говорит
форме, что первой будет бухгалтерия. База не нужна: запросы подменены unittest.mock —
тем же средством, что и в test_payments_closing_wiring.py.
"""

import re
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from flask import Flask

from payments import access, flow, notify, privacy, report, routes, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
REQUEST_ID = 501
INITIATOR = {'id': 7, 'name': 'Инициатор'}
ACCOUNTANT = {'id': 6, 'name': 'Бухгалтер'}
NORTH = {'id': 2, 'name': 'ТОО «Север»', 'is_active': True}
HIDDEN = {'id': 3, 'name': 'ТОО «Старое»', 'is_active': False}
# Лимит согласования на компанию «Север»: по нему заявку утверждает Сауле (п. 6).
NORTH_LIMIT = {'id': 40, 'number': '15', 'issued_on': date(2026, 9, 1), 'status': 'active',
               'legal_entity_id': 2, 'delegate_user_id': 30, 'delegate_name': 'Сауле',
               'all_projects': True, 'all_counterparties': True, 'amount_limit': None}


def purchase_row(**overrides):
    row = {
        'id': REQUEST_ID, 'status': 'active', 'stage': 'approval', 'request_kind': 'purchase',
        'legal_entity_id': None, 'legal_entity_name': None, 'department_id': 5, 'department_name': 'СЗоВ',
        'expense_name': 'Бумага А4', 'amount': Decimal('106000'), 'category_id': 3, 'project_id': None,
        'payment_method': 'invoice', 'counterparty_id': 11, 'counterparty_name': 'ТОО «Канцлер»',
        'object_type': 'service', 'accounting_category': None, 'due_on': date(2026, 10, 20),
        'initiator_id': INITIATOR['id'], 'initiator_name': INITIATOR['name'],
        'manager_id': 8, 'manager_name': 'Руководитель', 'submitted_at': '2026-10-15T10:00:00',
        'submitted_snapshot': None, 'legacy_step': None, 'approver_user_id': None,
    }
    row['submitted_snapshot'] = wf.essentials(row)
    row.update(overrides)
    return row


def entity_subtask(state='open'):
    item = dict(wf.SUBTASK_BY_KIND[wf.KIND_ENTITY])
    return {'id': 91, 'request_id': REQUEST_ID, 'kind': wf.KIND_ENTITY, 'position': item['position'],
            'stage': item['stage'], 'role_code': item['role'], 'assignee_id': None, 'assignee_name': None,
            'state': state, 'status': 'new', 'title': item['title'], 'role_label': 'Бухгалтерия'}


def step(kind, assignee_id, assignee_name, state='pending'):
    item = wf.SUBTASK_BY_KIND[kind]
    return {'id': 100 + item['position'], 'request_id': REQUEST_ID, 'kind': kind, 'position': item['position'],
            'stage': item['stage'], 'role_code': item['role'], 'assignee_id': assignee_id,
            'assignee_name': assignee_name, 'state': state, 'status': None}


def route_entry(route, kind):
    return next(entry for entry in route if entry['kind'] == kind)


class SetEntityTests(unittest.TestCase):
    """POST /act {kind: entity_clarify, action: set_entity} → flow.act → _set_entity."""

    def setUp(self):
        self.row = purchase_row()
        queries = mock.MagicMock()
        queries.read_request.side_effect = lambda cursor, request_id, lock=False: dict(self.row)
        queries.read_subtask.return_value = entity_subtask()

        def update(cursor, request_id, fields):
            self.row.update(fields)
            if 'legal_entity_id' in fields:
                self.row['legal_entity_name'] = {2: NORTH['name'], 3: HIDDEN['name']}.get(fields['legal_entity_id'])
            return {}

        queries.update_request_fields.side_effect = update
        queries.role_member_ids.return_value = {'approver': {30, 31}, 'accounting': {ACCOUNTANT['id']}}
        queries.user_brief.side_effect = lambda cursor, user_id: {'id': user_id, 'name': 'Сауле', 'fired': False}
        queries.today_almaty.return_value = date(2026, 10, 15)
        # Как после отправки без компании: руководитель записан, утверждение ждёт своей очереди.
        self.steps = [entity_subtask(), step(wf.KIND_MANAGER, 8, 'Руководитель'), step(wf.KIND_APPROVAL, None, None)]
        queries.list_subtasks.side_effect = lambda cursor, request_id: [dict(item) for item in self.steps]
        self.events = [{'id': 1, 'kind': 'created', 'payload': None}, {'id': 2, 'kind': 'submitted', 'payload': {}}]
        queries.list_events.side_effect = lambda cursor, request_id, **kwargs: [dict(item) for item in self.events]
        queries.resolve_manager.return_value = {'id': 12, 'name': 'Новый руководитель', 'source': 'supervisor'}
        directory = mock.MagicMock()
        directory.read_legal_entity.side_effect = lambda cursor, entity_id: {2: NORTH, 3: HIDDEN}.get(entity_id)
        directory.read_counterparty.return_value = None
        directory.read_template.return_value = None
        directory.list_limits.return_value = [NORTH_LIMIT]
        directory.list_routes.return_value = []
        self.queries, self.directory = queries, directory
        self.notify_initiator = mock.MagicMock()
        self.notices = mock.MagicMock()
        self.advance = mock.MagicMock(return_value=wf.KIND_MANAGER)
        for patcher in (mock.patch.object(flow, 'queries', queries),
                        mock.patch.object(flow, 'directory', directory),
                        mock.patch.object(flow, 'notices', self.notices),
                        mock.patch.object(flow, '_notify_initiator', self.notify_initiator),
                        mock.patch.object(flow, 'advance', self.advance)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def act(self, **fields):
        return flow.act(mock.MagicMock(), REQUEST_ID, wf.KIND_ENTITY, 'set_entity', ACCOUNTANT, fields=fields)

    def test_company_is_written_and_the_route_is_built_again_with_it(self):
        result = self.act(legal_entity_id=2, comment='Закуп для парка «Север»')
        self.assertEqual(result, {'next': wf.KIND_MANAGER})
        self.queries.update_request_fields.assert_called_once_with(mock.ANY, REQUEST_ID, {'legal_entity_id': 2})
        # Этап уточнения закрыт тем, кто указал компанию, а маршрут после него — заново.
        self.queries.complete_subtask.assert_called_once_with(mock.ANY, 91, ACCOUNTANT, outcome='entity_set',
                                                              comment='Закуп для парка «Север»')
        route_call = self.queries.write_route.call_args
        self.assertEqual(route_call.kwargs, {'keep': (wf.KIND_ENTITY,)})
        route = route_call.args[2]
        self.assertEqual([entry['kind'] for entry in route], [
            'initiation', 'manager_approval', 'approval', 'invoice_payment', 'receiving', 'closing_docs'])
        approval = next(entry for entry in route if entry['kind'] == wf.KIND_APPROVAL)
        self.assertEqual(approval['assignee_id'], 30, 'лимит на компанию «Север» — утверждает Сауле')
        manager = next(entry for entry in route if entry['kind'] == wf.KIND_MANAGER)
        self.assertEqual(manager['assignee_id'], 8, 'руководитель — тот, что записан при отправке')
        basis = self.queries.set_route_basis.call_args.args[2]
        self.assertEqual((basis['approver_user_id'], basis['source'], basis['limit_id']), (30, 'limit', 40))
        self.advance.assert_called_once()

    def test_terms_snapshot_now_carries_the_company(self):
        self.act(legal_entity_id=2)
        snapshot = self.queries.touch_snapshot.call_args.args[2]
        self.assertEqual(snapshot['legal_entity_id'], 2)
        self.assertNotIn('stale', snapshot)

    def test_stale_card_mark_survives(self):
        self.row['submitted_snapshot'] = dict(self.row['submitted_snapshot'], stale=True)
        self.act(legal_entity_id=2)
        self.assertTrue(self.queries.touch_snapshot.call_args.args[2].get('stale'))

    def test_history_and_the_initiator_learn_the_company(self):
        self.act(legal_entity_id=2)
        event = next(call for call in self.queries.log_event.call_args_list if call.args[2] == 'entity_set')
        self.assertEqual(event.args[3], ACCOUNTANT)
        self.assertEqual(event.kwargs['payload']['kind'], wf.KIND_ENTITY)
        self.assertEqual((event.kwargs['payload']['legal_entity_id'], event.kwargs['payload']['legal_entity_name']),
                         (2, NORTH['name']))
        notice = self.notify_initiator.call_args.args[2]
        self.assertEqual(notice['kind'], 'entity_set')
        self.assertIn(NORTH['name'], notice['body'])

    def test_accounting_bell_is_cleared(self):
        # «Уточните компанию» гаснет у всей бухгалтерии, как у любого выполненного этапа.
        self.act(legal_entity_id=2)
        self.notices.clear_subtask.assert_called_once_with(mock.ANY, 91)

    def reassigned(self, kind, user_id, name):
        self.events.append({'id': len(self.events) + 1, 'kind': 'reassigned',
                            'payload': {'kind': kind, 'assignee_id': user_id, 'assignee_name': name}})

    def test_admin_reassignment_survives_the_rebuild(self):
        # Пока компанию уточняли, администратор передал этапы другим людям — их выбор остаётся.
        self.steps = [entity_subtask(), step(wf.KIND_MANAGER, 55, 'Заместитель'), step(wf.KIND_APPROVAL, 77, 'Петров')]
        self.reassigned(wf.KIND_MANAGER, 55, 'Заместитель')
        self.reassigned(wf.KIND_APPROVAL, 77, 'Петров')
        self.act(legal_entity_id=2)
        route = self.queries.write_route.call_args.args[2]
        self.assertEqual(route_entry(route, wf.KIND_MANAGER)['assignee_id'], 55)
        self.assertEqual((route_entry(route, wf.KIND_APPROVAL)['assignee_id'],
                          route_entry(route, wf.KIND_APPROVAL)['assignee_name']), (77, 'Петров'))

    def test_preliminary_approver_gives_way_to_the_matrix(self):
        # Утверждение стояло на том, кого матрица назвала при отправке без компании, — его меняют.
        self.row['approver_user_id'] = 31
        self.steps = [entity_subtask(), step(wf.KIND_MANAGER, 8, 'Руководитель'), step(wf.KIND_APPROVAL, 31, 'Зарина')]
        self.act(legal_entity_id=2)
        self.assertEqual(route_entry(self.queries.write_route.call_args.args[2], wf.KIND_APPROVAL)['assignee_id'], 30)

    def test_reassigned_to_the_preliminary_approver_is_still_a_choice(self):
        # Стенд 15.10.2026: администратор передал утверждение Зарине — той же, кого назвала матрица
        # без компании. Это его решение, а не совпадение: Зарина и остаётся.
        self.row['approver_user_id'] = 31
        self.steps = [entity_subtask(), step(wf.KIND_MANAGER, 8, 'Руководитель'), step(wf.KIND_APPROVAL, 31, 'Зарина')]
        self.reassigned(wf.KIND_APPROVAL, 31, 'Зарина')
        self.act(legal_entity_id=2)
        self.assertEqual(route_entry(self.queries.write_route.call_args.args[2], wf.KIND_APPROVAL)['assignee_id'], 31)

    def test_reassignment_before_the_last_sending_does_not_count(self):
        # Передали до прежней отправки — после неё маршрут уже строился заново: выбирает матрица.
        self.events.insert(1, {'id': 9, 'kind': 'reassigned', 'payload': {'kind': wf.KIND_APPROVAL, 'assignee_id': 77}})
        self.steps = [entity_subtask(), step(wf.KIND_MANAGER, 8, 'Руководитель'), step(wf.KIND_APPROVAL, 77, 'Петров')]
        self.act(legal_entity_id=2)
        self.assertEqual(route_entry(self.queries.write_route.call_args.args[2], wf.KIND_APPROVAL)['assignee_id'], 30)

    def test_fired_manager_is_replaced(self):
        # Руководителя уволили, пока заявка ждала бухгалтерию, — этап у нового, и заявка его запоминает.
        self.queries.user_brief.side_effect = lambda cursor, user_id: {'id': user_id, 'name': 'X', 'fired': user_id == 8}
        self.act(legal_entity_id=2)
        self.assertEqual(route_entry(self.queries.write_route.call_args.args[2], wf.KIND_MANAGER)['assignee_id'], 12)
        self.queries.update_request_fields.assert_any_call(mock.ANY, REQUEST_ID,
                                                           {'manager_id': 12, 'manager_name': 'Новый руководитель'})

    def test_without_a_company_nothing_moves(self):
        with self.assertRaises(flow.FlowError) as caught:
            self.act()
        self.assertEqual(caught.exception.missing, ['Выберите компанию'])
        self.queries.update_request_fields.assert_not_called()
        self.queries.write_route.assert_not_called()

    def test_hidden_or_unknown_company_is_refused(self):
        for entity_id in (HIDDEN['id'], 999):
            with self.subTest(entity_id=entity_id), self.assertRaises(flow.FlowError) as caught:
                self.act(legal_entity_id=entity_id)
            self.assertEqual(caught.exception.code, 'PAYMENT_ENTITY_UNKNOWN')
        self.queries.update_request_fields.assert_not_called()
        self.queries.complete_subtask.assert_not_called()

    def test_route_nobody_can_walk_is_refused_before_the_step_closes(self):
        # Утверждать некому, кроме самого инициатора, — заявка не уходит в пустоту (п. 1).
        self.queries.role_member_ids.return_value = {'approver': {INITIATOR['id']}, 'accounting': {ACCOUNTANT['id']}}
        self.directory.list_limits.return_value = []
        with self.assertRaises(flow.FlowError) as caught:
            self.act(legal_entity_id=2)
        self.assertEqual(caught.exception.code, 'PAYMENTS_ROLES_NOT_CONFIGURED')
        self.queries.complete_subtask.assert_not_called()
        self.queries.write_route.assert_not_called()


class SubmitWithoutCompanyTests(unittest.TestCase):
    """flow.submit: заявка без компании уходит сначала бухгалтерии."""

    def submit(self, entities=(NORTH,), clarified=None, **overrides):
        row = purchase_row(stage='initiation', submitted_at=None, submitted_snapshot=None)
        row.update(overrides)
        queries = mock.MagicMock()
        queries.read_request.return_value = row
        initiation = {'id': 90, 'kind': 'initiation', 'state': 'open', 'outcome': None}
        queries.read_subtask.side_effect = lambda cursor, request_id, kind: (
            initiation if kind == wf.KIND_INITIATION else (clarified if kind == wf.KIND_ENTITY else None))
        queries.resolve_manager.return_value = {'id': 8, 'name': 'Руководитель'}
        queries.role_member_ids.return_value = {'approver': {30}, 'accounting': {ACCOUNTANT['id']}}
        queries.today_almaty.return_value = date(2026, 10, 15)
        directory = mock.MagicMock()
        directory.read_counterparty.return_value = None
        directory.list_limits.return_value = []
        directory.list_routes.return_value = []
        directory.list_legal_entities.return_value = list(entities)
        advance = mock.MagicMock(return_value=wf.KIND_ENTITY)
        for patcher in (mock.patch.object(flow, 'queries', queries),
                        mock.patch.object(flow, 'directory', directory),
                        mock.patch.object(flow, 'notices', mock.MagicMock()),
                        mock.patch.object(flow, 'submit_problems', return_value=[]),
                        mock.patch.object(flow, 'advance', advance)):
            patcher.start()
            self.addCleanup(patcher.stop)
        result = flow.submit(mock.MagicMock(), REQUEST_ID, INITIATOR)
        return queries, result

    def test_first_stop_is_accounting(self):
        queries, result = self.submit()
        self.assertEqual(result['next'], wf.KIND_ENTITY)
        route = queries.write_route.call_args.args[2]
        self.assertEqual([entry['kind'] for entry in route][:3], ['initiation', 'entity_clarify', 'manager_approval'])
        snapshot = queries.mark_submitted.call_args.kwargs['snapshot']
        self.assertIsNone(snapshot['legal_entity_id'], 'условия на момент отправки — без компании')

    def test_known_company_skips_the_step(self):
        queries, _result = self.submit(legal_entity_id=2)
        self.assertNotIn('entity_clarify', [entry['kind'] for entry in queries.write_route.call_args.args[2]])
        self.assertEqual(queries.write_route.call_args.kwargs, {'keep': ()})

    def test_history_says_where_the_request_went(self):
        queries, _result = self.submit()
        event = next(call for call in queries.log_event.call_args_list if call.args[2] == 'submitted')
        self.assertTrue(event.kwargs['payload']['entity_step'])
        queries, _result = self.submit(legal_entity_id=2)
        event = next(call for call in queries.log_event.call_args_list if call.args[2] == 'submitted')
        self.assertFalse(event.kwargs['payload']['entity_step'])

    def test_empty_company_directory_does_not_strand_the_request(self):
        # Бухгалтерии не из чего выбрать — заявка без компании не уходит, а не застревает у неё.
        with self.assertRaises(flow.FlowError) as caught:
            self.submit(entities=())
        self.assertEqual(caught.exception.code, 'PAYMENTS_ROLES_NOT_CONFIGURED')
        self.assertIn('Реквизиты компаний', str(caught.exception))
        self.submit(entities=(), legal_entity_id=2)  # с компанией справочник не нужен — уходит

    def test_resent_after_rework_keeps_the_clarification_done(self):
        # Бухгалтерия указала компанию, потом инициатор на доработке сменил сумму — согласование
        # заново, а этап уточнения в «Ходе заявки» остаётся выполненным, а не «не требуется».
        sent = purchase_row(legal_entity_id=2)
        queries, result = self.submit(
            clarified=dict(entity_subtask('done'), outcome='entity_set'), legal_entity_id=2,
            submitted_at='2026-10-15T10:00:00', submitted_snapshot=wf.essentials(sent), amount=Decimal('900000'))
        self.assertTrue(result['rerouted'])
        self.assertEqual(queries.write_route.call_args.kwargs, {'keep': (wf.KIND_ENTITY,)})

    def test_company_cleared_on_rework_sends_it_to_accounting_again(self):
        sent = purchase_row(legal_entity_id=2)
        queries, _result = self.submit(
            clarified=dict(entity_subtask('done'), outcome='entity_set'), legal_entity_id=None,
            submitted_at='2026-10-15T10:00:00', submitted_snapshot=wf.essentials(sent))
        self.assertIn(wf.KIND_ENTITY, [entry['kind'] for entry in queries.write_route.call_args.args[2]])
        self.assertEqual(queries.write_route.call_args.kwargs, {'keep': ()}, 'этап уточнения — заново, с начала')


class CancelDuringClarificationTests(unittest.TestCase):
    """Пока бухгалтерия только уточняет компанию, до оплаты далеко — инициатор вправе отменить."""

    def test_constant_matches_the_workflow(self):
        self.assertEqual(access.ENTITY_KIND, wf.KIND_ENTITY)

    def test_initiator_may_cancel_while_accounting_picks_the_company(self):
        me = {'user_id': INITIATOR['id']}
        clarifying = purchase_row(current_role_code='accounting', current_kind=wf.KIND_ENTITY)
        self.assertTrue(access.can_cancel_request(me, clarifying))
        paying = purchase_row(current_role_code='accounting', current_kind=wf.KIND_INVOICE)
        self.assertFalse(access.can_cancel_request(me, paying), 'оплату счёта отменяет только администратор')
        self.assertFalse(access.can_cancel_request({'user_id': 99}, clarifying), 'чужую заявку — нет')


class PrivacyTests(unittest.TestCase):
    """П. 9: кто именно в бухгалтерии указал компанию, инициатор не видит — только «Бухгалтерия»."""

    def test_step_belongs_to_accounting(self):
        self.assertEqual(privacy.DEPARTMENT_KINDS[wf.KIND_ENTITY], 'accounting')

    def test_names_are_hidden_from_the_initiator(self):
        full = {
            'request': purchase_row(),
            'subtasks': [dict(entity_subtask('done'), done_by=6, done_by_name='Бухгалтер Иванова')],
            'events': [{'id': 1, 'kind': 'entity_set', 'actor_id': 6, 'actor_name': 'Бухгалтер Иванова',
                        'payload': {'kind': wf.KIND_ENTITY, 'legal_entity_name': NORTH['name']}}],
            'attachments': [],
        }
        privacy.hide_department_people(full, {'accounting': {6}})
        self.assertEqual(full['subtasks'][0]['done_by_name'], 'Бухгалтерия')
        self.assertEqual(full['events'][0]['actor_name'], 'Бухгалтерия')


class TextsTests(unittest.TestCase):
    def test_task_and_notice_say_what_to_do(self):
        self.assertEqual(notify.TASK_TITLES[wf.KIND_ENTITY], 'Уточните компанию')
        notice = notify.entity_set(purchase_row(legal_entity_name=NORTH['name']))
        self.assertEqual(notice['title'], 'Компания указана')
        self.assertIn(NORTH['name'], notice['body'])
        self.assertIn(NORTH['name'], notice['telegram'])

    def test_excel_says_what_the_accounting_does_on_approval(self):
        row = report.enrich(purchase_row(current_kind=wf.KIND_ENTITY), [])
        self.assertEqual(row['stage_text'], 'Согласование — уточнение компании')
        self.assertEqual(report.enrich(purchase_row(current_kind=wf.KIND_APPROVAL), [])['stage_text'], 'Согласование')


class RecordingCursor:
    """Курсор, который только записывает запросы: SQL раздела без базы."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))


class RouteSqlTests(unittest.TestCase):
    """queries.write_route(keep) и set_route_basis — то, что моки flow не видят."""

    def test_rebuilt_route_leaves_the_clarification_step_alone(self):
        from payments import queries
        cursor = RecordingCursor()
        # Маршрут после уточнения строится с этапом в списке (как если бы его вернула матрица) —
        # keep не даёт ни переписать его заново, ни пометить пропущенным.
        route = wf.build_route(purchase_row(), initiator=INITIATOR, manager={'id': 8, 'name': 'Руководитель'})
        self.assertIn(wf.KIND_ENTITY, [entry['kind'] for entry in route])
        queries.write_route(cursor, REQUEST_ID, route, keep=(wf.KIND_ENTITY,))
        inserted = [params[1] for sql, params in cursor.calls if sql.startswith('INSERT INTO payment_subtasks')]
        self.assertNotIn(wf.KIND_ENTITY, inserted, 'пройденный этап не сбрасывается в «не начат»')
        self.assertIn(wf.KIND_MANAGER, inserted)
        sql, params = cursor.calls[-1]
        self.assertTrue(sql.startswith("UPDATE payment_subtasks SET state = 'skipped'"), sql)
        self.assertIn(wf.KIND_ENTITY, params[3], 'и не помечается «не требуется»')

    def test_without_keep_old_steps_outside_the_route_are_skipped(self):
        from payments import queries
        cursor = RecordingCursor()
        route = wf.build_route(purchase_row(legal_entity_id=2), initiator=INITIATOR, manager=None)
        queries.write_route(cursor, REQUEST_ID, route)
        _sql, params = cursor.calls[-1]
        self.assertNotIn(wf.KIND_ENTITY, params[3], 'компанию указал инициатор — этап уточнения не нужен')

    def test_route_basis_is_written_with_its_approver_and_limit(self):
        from payments import queries
        cursor = RecordingCursor()
        queries.set_route_basis(cursor, REQUEST_ID, {'approver_user_id': 30, 'limit_id': 40, 'basis': 'Приказ №21'})
        sql, params = cursor.calls[-1]
        self.assertIn('route_basis = %s, approver_user_id = %s, approval_order_id = %s', sql)
        self.assertEqual(params[1:], (30, 40, REQUEST_ID))
        self.assertNotIn('submitted_at', sql, 'время отправки уточнение компании не трогает')

    def test_basis_goes_to_the_same_columns_as_on_submit(self):
        from payments import queries
        basis = {'approver_user_id': 30, 'limit_id': 40}
        sent, again = RecordingCursor(), RecordingCursor()
        queries.mark_submitted(sent, REQUEST_ID, snapshot={}, basis=basis)
        queries.set_route_basis(again, REQUEST_ID, basis)

        def assigned(call):
            sql, params = call
            return dict(zip(re.findall(r'(\w+) = %s', sql), params))

        on_submit, on_clarify = assigned(sent.calls[0]), assigned(again.calls[0])
        for column in ('approver_user_id', 'approval_order_id'):
            self.assertEqual(on_clarify[column], on_submit[column], column)


class ActRouteTests(unittest.TestCase):
    """POST /requests/<id>/act: компания из запроса доходит до flow.act числом."""

    def test_company_from_the_request_reaches_the_flow(self):
        row = purchase_row()
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        queries.read_request.return_value = row
        queries.role_member_ids.return_value = {'accounting': {ACCOUNTANT['id']}}
        queries.list_subtasks.return_value = [entity_subtask()]
        queries.today_almaty.return_value = date(2026, 10, 15)
        for name in ('list_attachments', 'list_items', 'list_offers', 'list_events', 'payment_history',
                     'find_duplicates'):
            getattr(queries, name).return_value = []
        directory = mock.MagicMock()
        for name in ('read_contract', 'read_legal_entity', 'read_account'):
            getattr(directory, name).return_value = None
        assets_sql = mock.MagicMock()
        assets_sql.for_request.return_value = []
        act = mock.MagicMock(return_value={'next': wf.KIND_MANAGER})
        for patcher in (mock.patch.object(routes, 'queries', queries),
                        mock.patch.object(routes, 'directory', directory),
                        mock.patch.object(routes, 'assets_sql', assets_sql),
                        mock.patch.object(routes, 'notices', mock.MagicMock()),
                        mock.patch.object(flow, 'load_for_action', return_value=(row, entity_subtask())),
                        mock.patch.object(flow, 'act', act),
                        mock.patch.object(flow, 'submit_problems', return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None)))
        response = app.test_client().post('/api/payments/requests/%s/act' % REQUEST_ID, json={
            'kind': wf.KIND_ENTITY, 'action': 'set_entity', 'legal_entity_id': '2', 'comment': 'Для ИП'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fields = act.call_args.kwargs['fields']
        self.assertEqual((fields['legal_entity_id'], fields['comment']), (2, 'Для ИП'))
        self.assertEqual(act.call_args.args[2:4], (wf.KIND_ENTITY, 'set_entity'))
        # Без компании — отказ с пунктом ещё до движка: форма покажет его красным у поля.
        act.reset_mock()
        refused = app.test_client().post('/api/payments/requests/%s/act' % REQUEST_ID,
                                         json={'kind': wf.KIND_ENTITY, 'action': 'set_entity'})
        self.assertEqual(refused.status_code, 400, refused.get_data(as_text=True))
        self.assertEqual(refused.get_json().get('missing'), ['Выберите компанию'])
        act.assert_not_called()


class RoutePreviewTests(unittest.TestCase):
    """GET /route-preview: форма заявки показывает, что первой будет бухгалтерия."""

    def preview(self, query):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        queries.resolve_manager.return_value = None
        queries.today_almaty.return_value = date(2026, 10, 15)
        directory = mock.MagicMock()
        directory.read_counterparty.return_value = None
        directory.read_contract.return_value = None
        basis = {'approver_user_id': None, 'approver_name': None, 'basis': 'Любой из утверждающих',
                 'manager_step': True, 'trace': []}
        for patcher in (mock.patch.object(routes, 'queries', queries),
                        mock.patch.object(routes, 'directory', directory),
                        mock.patch.object(flow, 'approval_basis', return_value=basis)):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None)))
        response = app.test_client().get('/api/payments/route-preview?' + query)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_purchase_without_company_starts_with_accounting(self):
        self.assertTrue(self.preview('amount=106000&request_kind=purchase')['entity_step'])

    def test_company_given_or_regular_payment_has_no_such_step(self):
        self.assertFalse(self.preview('amount=106000&request_kind=purchase&legal_entity_id=2')['entity_step'])
        self.assertFalse(self.preview('amount=200000&request_kind=regular')['entity_step'])


if __name__ == '__main__':
    unittest.main()
