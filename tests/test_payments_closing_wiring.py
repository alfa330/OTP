# -*- coding: utf-8 -*-
"""«Оплата проведена» насквозь: ручка карточки заявки → поле `closing`, и закрытие заявки.

Жалоба 13.10.2026: карту сотрудника пополнили — карточка сразу показала галочку
«Оплата проведена», хотя чека ещё нет (п. 5.3). Правило живёт в workflow.py, но
человек видит не его, а ответ GET /api/payments/requests/<id>, и заявку закрывает
flow._finish. Здесь проверяется именно эта проводка. База не нужна: запросы
подменены unittest.mock — тем же средством, что и в test_payments_access.py.
"""

import unittest
from unittest import mock

from flask import Flask

from payments import access, flow, routes, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
REQUEST_ID = 471


def card_request(**overrides):
    row = {
        'id': REQUEST_ID, 'status': 'active', 'stage': 'payment', 'request_kind': 'purchase',
        'payment_method': 'card', 'card_recipient': 'employee', 'object_type': 'goods',
        'accounting_category': 'consumable', 'closing_docs_status': 'closed', 'amount': 15000,
        'initiator_id': 10, 'initiator_name': 'Инициатор', 'paid_on': '2026-10-13', 'paid_amount': 15000,
        'submitted_at': '2026-10-12T10:00:00', 'has_card': False,
    }
    row.update(overrides)
    return row


def route(recipient='employee', **states):
    """Маршрут заявки на карту, как его строит раздел; состояние этапа — по имени, остальные пройдены."""
    built = wf.build_route(card_request(card_recipient=recipient), initiator={'id': 10, 'name': 'Инициатор'},
                           manager={'id': 11, 'name': 'Руководитель'})
    return [dict(item, id=index, state=states.get(item['kind'], 'done'), outcome=None, status=None)
            for index, item in enumerate(built, start=1)]


class CardOfTheRequestTests(unittest.TestCase):
    """GET /api/payments/requests/<id> — то, что рисует блок «Что осталось до закрытия»."""

    def read(self, request_row, subtasks):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        queries.read_request.return_value = request_row
        queries.role_member_ids.return_value = {}
        queries.list_subtasks.return_value = subtasks
        queries.today_almaty.return_value = None
        for name in ('list_attachments', 'list_items', 'list_offers', 'list_events', 'payment_history',
                     'find_duplicates', 'list_observers'):
            getattr(queries, name).return_value = []
        directory = mock.MagicMock()
        directory.read_contract.return_value = None
        directory.read_legal_entity.return_value = None
        directory.read_account.return_value = None
        assets_sql = mock.MagicMock()
        assets_sql.for_request.return_value = []
        patches = [
            mock.patch.object(routes, 'queries', queries),
            mock.patch.object(routes, 'directory', directory),
            mock.patch.object(routes, 'assets_sql', assets_sql),
            mock.patch.object(routes, 'notices', mock.MagicMock()),
            mock.patch.object(flow, 'submit_problems', return_value=[]),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None)))
        response = app.test_client().get('/api/payments/requests/%s' % REQUEST_ID)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def closing(self, request_row, subtasks):
        return {item['label']: item['ok'] for item in self.read(request_row, subtasks)['closing']}

    def test_topped_up_employee_card_is_not_paid_until_the_receipt(self):
        """Ровно снимок владельца: карта пополнена, текущий этап — «Предоставить чек»."""
        waiting = route(receipt_confirm='open', receiving='pending', closing_docs='pending')
        closing = self.closing(card_request(), waiting)
        self.assertEqual(list(closing), ['Закуп согласован', 'Оплата проведена', 'Товар получен',
                                         'Закрывающие документы получены'])
        self.assertTrue(closing['Закуп согласован'])
        self.assertFalse(closing['Оплата проведена'], 'карта пополнена, чека нет — галочки быть не должно')
        self.assertFalse(closing['Товар получен'])

    def test_receipt_puts_the_tick(self):
        closing = self.closing(card_request(), route(receiving='pending', closing_docs='pending'))
        self.assertTrue(closing['Оплата проведена'])
        self.assertFalse(closing['Товар получен'])

    def test_supplier_card_is_paid_by_the_transfer(self):
        subtasks = route('supplier', receiving='pending', closing_docs='pending')
        self.assertNotIn('receipt_confirm', [item['kind'] for item in subtasks])
        self.assertTrue(self.closing(card_request(card_recipient='supplier'), subtasks)['Оплата проведена'])


class FinishTests(unittest.TestCase):
    """flow.advance → _finish: заявка закрывается только по условиям п. 15."""

    def advance(self, subtasks):
        queries = mock.MagicMock()
        queries.read_request.return_value = card_request(stage='closing')
        queries.list_subtasks.return_value = subtasks
        queries.list_attachments.return_value = []
        assets_sql = mock.MagicMock()
        assets_sql.for_request.return_value = []
        for patcher in (mock.patch.object(flow, 'queries', queries),
                        mock.patch.object(flow, 'assets_sql', assets_sql),
                        mock.patch.object(flow, 'notices', mock.MagicMock()),
                        mock.patch.object(flow, '_notify_initiator', mock.MagicMock())):
            patcher.start()
            self.addCleanup(patcher.stop)
        return queries, lambda: flow.advance(mock.MagicMock(), REQUEST_ID, {'id': ADMIN, 'name': 'Админ'})

    def test_request_without_the_receipt_is_not_closed(self):
        # Ручная правка маршрута: этап чека завис, а следом всё пройдено.
        queries, advance = self.advance(route(receipt_confirm='waiting'))
        with self.assertRaises(flow.FlowError) as caught:
            advance()
        self.assertEqual(caught.exception.code, 'PAYMENT_CLOSE_BLOCKED')
        self.assertEqual(caught.exception.missing, ['Оплата проведена'])
        queries.close_request.assert_not_called()

    def test_passed_route_closes_the_request(self):
        queries, advance = self.advance(route())
        self.assertIsNone(advance())
        self.assertEqual(queries.close_request.call_args.args[1:3], (REQUEST_ID, 'done'))


if __name__ == '__main__':
    unittest.main()
