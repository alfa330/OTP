# -*- coding: utf-8 -*-
"""«Без договора» (10.11.2026): ручки и ход заявки без базы — то, что в CI (там нет Postgres)
обязано ловить поломки записи отметки. Разбор 10.11.2026 показал, что мутации маршрута
правки, окна Helpdesk IT, справочника и хода заявки выживали: здесь они проверяются
поведением — через настоящие ручки Flask и функции flow на подменённых запросах.
"""
import json
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from flask import Flask

from payments import access, directory as directory_mod, flow, queries as queries_mod, routes, routes_directory
from payments import workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
REQUEST_ID = 905
OUR, OTHER = 11, 12          # поставщики: тот, что в заявке, и другой
CONTRACT = {'id': 3, 'number': '12', 'status': 'active', 'counterparty_id': OUR, 'ends_on': date(2026, 12, 31)}


def purchase(**overrides):
    row = {'id': REQUEST_ID, 'status': 'active', 'stage': 'initiation', 'request_kind': 'purchase',
           'legal_entity_id': 2, 'department_id': 5, 'expense_name': 'Столы', 'amount': Decimal('900000'),
           'category_id': 3, 'payment_method': 'invoice', 'object_type': 'service', 'accounting_category': None,
           'justification': 'Новый офис', 'due_on': date(2026, 11, 30), 'initiator_id': 10,
           'initiator_name': 'Инициаторова Ирина', 'counterparty_id': OUR, 'contract_id': None, 'no_contract': False,
           'no_alternatives': True, 'no_alternatives_reason': 'single_supplier', 'legacy_step': None,
           'submitted_at': None, 'it_purchase': False, 'fixed_template_id': None, 'invoice_date': None}
    row.update(overrides)
    return row


class _Routes(unittest.TestCase):
    """Ручки раздела на подменённых запросах (как в test_payments_helpdesk.WiringTests)."""

    def client(self, row):
        self.row = row
        self.queries = mock.MagicMock()
        self.queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админов', 'role': 'admin'}
        self.queries.roles_of_user.return_value = set()
        self.queries.read_request.side_effect = lambda cursor, request_id, lock=False: dict(self.row)
        self.queries.update_request_fields.return_value = {}
        self.queries.list_subtasks.return_value = []
        self.queries.role_member_ids.return_value = {}
        self.queries.today_almaty.return_value = date(2026, 11, 10)
        for name in ('list_attachments', 'list_items', 'list_offers', 'list_events', 'payment_history',
                     'find_duplicates', 'list_observers'):
            getattr(self.queries, name).return_value = []
        self.directory = mock.MagicMock()
        self.directory.get_settings.return_value = {}
        self.directory.read_counterparty.side_effect = lambda cursor, cid: {'id': cid, 'name': 'Поставщик %s' % cid}
        self.directory.read_contract.side_effect = lambda cursor, cid: dict(CONTRACT) if cid == CONTRACT['id'] else None
        for name in ('read_legal_entity', 'read_account', 'department_brief'):
            getattr(self.directory, name).return_value = None
        self.assets_sql = mock.MagicMock()
        self.assets_sql.for_request.return_value = []
        self.assets_sql.of_user.return_value = []
        for patcher in (mock.patch.object(routes, 'queries', self.queries),
                        mock.patch.object(routes, 'directory', self.directory),
                        mock.patch.object(routes_directory, 'directory', self.directory),
                        mock.patch.object(routes_directory, 'queries', self.queries),
                        mock.patch.object(flow, 'directory', self.directory),
                        mock.patch.object(routes, 'assets_sql', self.assets_sql),
                        mock.patch.object(routes, 'notices', mock.MagicMock()),
                        mock.patch.object(flow, 'submit_problems', return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204), resolve_requester=lambda: (ADMIN, None, None)))
        return app.test_client()

    def patch(self, client, body):
        response = client.patch('/api/payments/requests/%s' % REQUEST_ID, json=body)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self.queries.update_request_fields.call_args_list[0].args[2]


class EditRouteTests(_Routes):
    """PATCH /requests/<id> — тот путь, которым пишет форма заявки."""

    OFFER_OTHER = [{'counterparty_id': OTHER, 'amount': 900000, 'is_recommended': True}]

    def test_supplier_change_without_the_key_drops_the_flag(self):
        client = self.client(purchase(no_contract=True))
        fields = self.patch(client, {'offers': self.OFFER_OTHER})
        self.assertEqual((fields['counterparty_id'], fields['contract_id'], fields['no_contract']), (OTHER, None, False))

    def test_supplier_change_with_a_fresh_choice_keeps_it(self):
        """Форма шлёт отметку, только если её выбрали при этом поставщике (contractChoice) — ей верим."""
        client = self.client(purchase(no_contract=False))
        fields = self.patch(client, {'offers': self.OFFER_OTHER, 'no_contract': True, 'contract_id': None})
        self.assertIs(fields['no_contract'], True)

    def test_contract_beats_the_flag_and_the_flag_drops_the_old_contract(self):
        client = self.client(purchase())
        fields = self.patch(client, {'contract_id': CONTRACT['id'], 'no_contract': True})
        self.assertEqual((fields['contract_id'], fields['no_contract']), (CONTRACT['id'], False))
        self.queries.update_request_fields.reset_mock()
        self.row = purchase(contract_id=CONTRACT['id'])
        fields = self.patch(client, {'no_contract': True})
        self.assertEqual((fields['contract_id'], fields['no_contract']), (None, True))

    def test_card_payment_drops_the_flag_and_text_false_is_false(self):
        client = self.client(purchase(no_contract=True))
        fields = self.patch(client, {'payment_method': 'card'})
        self.assertIs(fields['no_contract'], False)
        self.queries.update_request_fields.reset_mock()
        fields = self.patch(client, {'no_contract': 'false'})
        self.assertIs(fields['no_contract'], False)

    def test_foreign_contract_is_refused_even_with_the_flag(self):
        client = self.client(purchase(counterparty_id=OTHER))
        response = client.patch('/api/payments/requests/%s' % REQUEST_ID,
                                json={'contract_id': CONTRACT['id'], 'no_contract': True})
        self.assertEqual(response.status_code, 400)
        self.assertIn('заключён с другим поставщиком', response.get_json()['error'])


class CardTests(_Routes):
    """Карточка (_full): проверка договора и «чего не хватает» у инициатора."""

    def card(self, row):
        client = self.client(row)
        response = client.get('/api/payments/requests/%s' % REQUEST_ID)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_contract_check_respects_the_flag(self):
        self.assertTrue(self.card(purchase(no_contract=True))['contract_check']['ok'],
                        'у бухгалтера под «Без договора» не должно быть «Договор не указан…»')
        self.assertFalse(self.card(purchase())['contract_check']['ok'])

    def test_answering_accounting_without_a_choice_is_named_in_advance(self):
        asked = dict(current_kind=wf.KIND_INITIATION, clarify_from=wf.KIND_INVOICE)
        self.assertEqual(self.card(purchase(**asked))['submit_problems'], [wf.contract_missing_text(purchase())])
        self.assertEqual(self.card(purchase(no_contract=True, **asked))['submit_problems'], [])
        # Вернули не с оплаты (согласующий — на доработку) — выбор договора не требуется.
        self.assertEqual(self.card(purchase(current_kind=wf.KIND_INITIATION, clarify_from=wf.KIND_APPROVAL))
                         ['submit_problems'], [])

    def test_regular_request_reads_the_template_choice(self):
        regular = dict(request_kind='regular', fixed_template_id=7, current_kind=wf.KIND_INITIATION,
                       clarify_from=wf.KIND_INVOICE)
        client = self.client(purchase(**regular))
        self.directory.read_template.return_value = {'id': 7, 'contract_id': None, 'no_contract': True}
        self.assertEqual(client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()['submit_problems'], [],
                         'администратор поставил «Без договора» — инициатору ответить можно')
        self.directory.read_template.return_value = {'id': 7, 'contract_id': None, 'no_contract': False}
        body = client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['submit_problems'], [wf.contract_missing_text({'request_kind': 'regular'})])

    def test_accountant_sees_the_regular_ask(self):
        row = purchase(request_kind='regular', fixed_template_id=7)
        client = self.client(row)
        self.queries.list_subtasks.return_value = [
            {'id': 1, 'kind': wf.KIND_INVOICE, 'state': 'open', 'role_code': 'accounting', 'assignee_id': None,
             'status': 'checking', 'position': 5, 'stage': 'payment'}]
        with mock.patch.object(access, 'can_act_on_subtask', return_value=True):
            body = client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        ask = {item['code']: item['ask'] for item in body['clarify_reasons']}
        self.assertEqual(ask['contract_required'], wf.CONTRACT_ASK_REGULAR)


class HelpdeskRouteTests(_Routes):
    """Окно Helpdesk IT: договор или «Без договора» вместе с оплатой счётом (K5)."""

    PAY = {'kind': 'it_review', 'action': 'it_offers', 'no_alternatives': True, 'no_alternatives_reason': 'single_supplier',
           'object_type': 'service', 'payment_method': 'invoice',
           'items': [{'name': 'Ноутбук', 'quantity': 1, 'unit_price': 450000}],
           'offers': [{'counterparty_id': OUR, 'amount': 450000, 'is_recommended': True}]}

    def act(self, body):
        client = self.client(purchase(it_purchase=True, payment_method=None, counterparty_id=None,
                                      invoice_number='СЧ-1', invoice_date=date(2026, 11, 13)))
        # Счёт поставщика уже у этапа Helpdesk IT (13.11.2026): первый опрос вложений — проверка ручки.
        self.queries.list_attachments.side_effect = [[{'kind': 'invoice', 'subtask_kind': wf.KIND_IT}]] + [[] for _ in range(8)]
        step = {'id': 77, 'kind': wf.KIND_IT, 'state': 'open', 'role_code': 'asset_keeper', 'assignee_id': None}
        acted = []
        with mock.patch.object(flow, 'load_for_action', return_value=(dict(self.row), step)), \
                mock.patch.object(flow, 'act', side_effect=lambda *a, **k: acted.append(k['fields']) or
                                  {'next': None, 'offer_ids': []}):
            response = client.post('/api/payments/requests/%s/act' % REQUEST_ID,
                                   data={'payload': json.dumps(body)}, content_type='multipart/form-data')
        return response, acted

    def test_no_contract_and_contract_reach_the_flow(self):
        response, acted = self.act(dict(self.PAY, no_contract=True, contract_id=None))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIs(acted[0]['payment']['no_contract'], True)
        response, acted = self.act(dict(self.PAY, no_contract=False, contract_id=CONTRACT['id']))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual((acted[0]['payment']['contract_id'], acted[0]['payment']['no_contract']), (CONTRACT['id'], False))

    def test_contract_of_another_supplier_is_refused(self):
        offers = [{'counterparty_id': OTHER, 'amount': 450000, 'is_recommended': True}]
        response, acted = self.act(dict(self.PAY, offers=offers, contract_id=CONTRACT['id']))
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(acted, [])


class TemplateRouteTests(_Routes):
    """Справочник «Регулярные платежи»: явный выбор и окно прежней сборки (без ключа)."""

    BODY = {'id': 7, 'name': 'Аренда офиса', 'amount': 500000, 'periodicity': 'monthly', 'next_due_on': '2026-12-05',
            'responsible_user_id': 10, 'counterparty_id': OUR}

    def save(self, body, stored):
        client = self.client(purchase())
        self.queries.user_brief.return_value = {'id': 10, 'name': 'Инициаторова Ирина', 'fired': False}
        self.directory.read_template.return_value = dict(stored, id=7)
        self.directory.upsert_template.return_value = 7
        response = client.post('/api/payments/templates', json=body)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self.directory.upsert_template.call_args.kwargs['fields']

    def test_old_window_keeps_the_stored_choice(self):
        self.assertIs(self.save(dict(self.BODY), {'no_contract': True})['no_contract'], True)
        self.assertIs(self.save(dict(self.BODY), {'no_contract': False})['no_contract'], False)

    def test_explicit_choice_wins_and_contract_drops_the_flag(self):
        self.assertIs(self.save(dict(self.BODY, no_contract=False), {'no_contract': True})['no_contract'], False)
        self.assertIs(self.save(dict(self.BODY, no_contract=True), {'no_contract': False})['no_contract'], True)
        fields = self.save(dict(self.BODY, no_contract=True, contract_id=CONTRACT['id']), {'no_contract': False})
        self.assertEqual((fields['contract_id'], fields['no_contract']), (CONTRACT['id'], False))


class FlowTests(unittest.TestCase):
    """Ход заявки на подменённых запросах: гейт, запрос бухгалтера, ответ регулярной заявки."""

    def setUp(self):
        self.row = purchase(stage='payment', submitted_at='2026-11-10T10:00:00')
        self.row['submitted_snapshot'] = wf.essentials(self.row)
        self.queries = mock.MagicMock()
        self.queries.read_request.side_effect = lambda cursor, request_id, lock=False: dict(self.row)
        self.queries.list_attachments.return_value = [{'kind': 'invoice'}]
        self.queries.today_almaty.return_value = date(2026, 11, 10)

        def update(cursor, request_id, fields):
            changes = {key: (self.row.get(key), value) for key, value in fields.items() if self.row.get(key) != value}
            self.row.update(fields)
            return changes

        self.queries.update_request_fields.side_effect = update
        self.directory = mock.MagicMock()
        self.directory.read_contract.side_effect = lambda cursor, cid: dict(CONTRACT) if cid == CONTRACT['id'] else None
        self.ask = mock.MagicMock(return_value={'next': wf.KIND_INITIATION})
        for patcher in (mock.patch.object(flow, 'queries', self.queries),
                        mock.patch.object(flow, 'directory', self.directory),
                        mock.patch.object(flow, '_ask_initiator', self.ask)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_gate_lets_no_contract_through(self):
        self.row.update(invoice_number='СЧ-1', invoice_date=date(2026, 11, 10), no_contract=True)
        self.assertEqual(flow._invoice_gate(None, self.row), (None, None))
        self.row['no_contract'] = False
        reason, text = flow._invoice_gate(None, self.row)
        self.assertEqual(reason, 'contract_required')
        self.assertIn(wf.CONTRACT_FIX, text)
        self.row['request_kind'] = 'regular'
        self.assertIn(wf.CONTRACT_FIX_REGULAR, flow._invoice_gate(None, self.row)[1])

    def request_info(self, reason, comment=''):
        step = {'id': 5, 'kind': wf.KIND_INVOICE, 'state': 'open'}
        with mock.patch.object(flow, 'load_for_action', return_value=(dict(self.row), step)):
            flow.act(None, REQUEST_ID, wf.KIND_INVOICE, 'request_info', {'id': 50, 'name': 'Бухгалтер'},
                     fields={'reason': reason, 'comment': comment})
        return self.ask.call_args.kwargs

    def test_accountant_asking_for_a_contract_drops_the_flag_and_says_what_to_do(self):
        self.row['no_contract'] = True
        sent = self.request_info('contract_required')
        self.assertIs(self.row['no_contract'], False, 'ответ без правки больше не вернёт счёт бухгалтерии')
        self.assertEqual(sent['comment'], wf.CLARIFY_BY_CODE['contract_required']['ask'])
        # Свой комментарий бухгалтера — его и видит инициатор; другая причина отметку не трогает.
        self.row['no_contract'] = True
        self.assertEqual(self.request_info('requisites', 'БИН не сходится')['comment'], 'БИН не сходится')
        self.assertIs(self.row['no_contract'], True)
        self.assertEqual(self.request_info('requisites')['comment'], wf.CLARIFY_BY_CODE['requisites']['ask'])

    def test_regular_request_gets_the_directory_instruction(self):
        self.row.update(request_kind='regular', fixed_template_id=7, no_contract=True)
        self.assertEqual(self.request_info('contract_required')['comment'], wf.CONTRACT_ASK_REGULAR)

    def test_regular_reply_takes_the_choice_from_the_directory(self):
        """«Отправить ответ» без формы: «Без договора», поставленное в справочнике после возврата, доходит."""
        self.row.update(request_kind='regular', fixed_template_id=7, stage='initiation', no_contract=False)
        self.directory.read_template.return_value = {'id': 7, 'contract_id': None, 'no_contract': True}
        synced = flow._sync_template_contract(None, self.row, {'id': 10, 'name': 'Инициаторова Ирина'})
        self.assertIs(synced['no_contract'], True)
        edited = [call for call in self.queries.log_event.call_args_list if call.args[2] == 'edited']
        self.assertEqual(edited[0].kwargs['payload']['changes'], {'no_contract': (False, True)})
        # Ничего не поменялось — и в истории ничего.
        self.queries.log_event.reset_mock()
        flow._sync_template_contract(None, self.row, {'id': 10})
        self.queries.log_event.assert_not_called()
        # Новый закуп справочник не трогает.
        self.assertIsNone(flow._template_contract(None, purchase()))

    def test_submit_syncs_before_checking(self):
        self.row.update(request_kind='regular', fixed_template_id=7, stage='initiation', status='active')
        self.directory.read_template.return_value = {'id': 7, 'contract_id': None, 'no_contract': True}
        with mock.patch.object(flow, 'submit_problems', side_effect=AssertionError('stop')) as problems:
            with self.assertRaises(AssertionError):
                flow.submit(None, REQUEST_ID, {'id': 10, 'name': 'Инициаторова Ирина'})
        self.assertIs(problems.call_args.args[1]['no_contract'], True, 'проверка видит выбор справочника')


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return (7,)


class WriteCoercionTests(unittest.TestCase):
    """Колонка NOT NULL: «не выбрано» пишется False, а рядом с договором отметки нет."""

    def test_create_request(self):
        for fields, expected in (({'no_contract': None}, False), ({'no_contract': True}, True),
                                 ({'no_contract': True, 'contract_id': 3}, False)):
            cursor = _FakeCursor()
            with mock.patch.object(queries_mod, 'replace_items'), mock.patch.object(queries_mod, 'ensure_initiation'), \
                    mock.patch.object(queries_mod, 'log_event'):
                queries_mod.create_request(cursor, fields=dict(fields, request_kind='purchase'), items=[],
                                           actor={'id': 10}, initiator={'id': 10, 'name': 'И'})
            insert = next(params for sql, params in cursor.calls if sql.startswith('INSERT INTO payment_requests'))
            self.assertIs(insert['no_contract'], expected, fields)

    def test_upsert_template(self):
        position = directory_mod._TEMPLATE_WRITE.index('no_contract')
        for fields, expected in (({'no_contract': None}, False), ({'no_contract': True}, True),
                                 ({'no_contract': True, 'contract_id': 3}, False)):
            cursor = _FakeCursor()
            directory_mod.upsert_template(cursor, fields=fields, actor_id=10)
            self.assertIs(cursor.calls[0][1][position], expected, fields)


if __name__ == '__main__':
    unittest.main()
