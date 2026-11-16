# -*- coding: utf-8 -*-
"""Срок закрывающих документов на карточках бухгалтерии («Оплата счетов», 16.11.2026, п. 5).

Владелец: «Добавить дедлайн на предоставление документов». Срок ставит бухгалтер (иначе —
по настройке от подтверждения получения), и он нужен не только инициатору: у бухгалтерии на
доске в «Ожидаются закрывающие документы» и на столе в задаче «Проверьте закрывающие
документы» стояла дата оплаты, которая к этому этапу давно прошла. Теперь карточка этой
колонки и задача этапа несут `docs_due_on`; карточкам других колонок и задачам других
этапов он не нужен (`None`).

База не нужна: запросы подменены unittest.mock, как в test_payments_faces.py.
"""

import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from flask import Flask

from payments import access, routes, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
INITIATOR, ACCOUNTANT = 10, 30
RECEIVED = datetime(2026, 11, 10, 15, 30)


def request_row(request_id, **overrides):
    row = {
        'id': request_id, 'status': 'active', 'stage': 'closing', 'request_kind': 'purchase',
        'payment_method': 'invoice', 'amount': 15000, 'expense_name': 'Бумага', 'paid_on': date(2026, 11, 9),
        'initiator_id': INITIATOR, 'initiator_name': 'Иванова Айгерим', 'closing_docs_status': 'none',
        'docs_due_on': None, 'docs_tail': False,
    }
    row.update(overrides)
    return row


def subtask(kind, state, **overrides):
    definition = wf.subtask(kind)
    item = {
        'kind': kind, 'title': definition['title'], 'stage': definition['stage'], 'position': definition['position'],
        'state': state, 'outcome': None, 'status': 'new',
        'role_code': definition['role'], 'role_label': wf.ROLE_LABELS[definition['role']],
        'assignee_id': None, 'assignee_name': None, 'done_by': None, 'done_by_name': None, 'done_at': None,
        'opened_at': None,
    }
    item.update(overrides)
    return item


def at_closing():
    return [subtask('invoice_payment', 'done', done_at=datetime(2026, 11, 9, 11, 0)),
            subtask('receiving', 'done', done_at=RECEIVED),
            subtask('closing_docs', 'open', opened_at=RECEIVED)]


def at_payment():
    return [subtask('approval', 'done', outcome='approved'), subtask('invoice_payment', 'open')]


def app_for(queries, viewer=ADMIN):
    for patcher in (mock.patch.object(routes, 'queries', queries),
                    mock.patch.object(routes.directory, 'get_settings', return_value={})):
        patcher.start()
    app = Flask(__name__)
    app.register_blueprint(routes.build_payments_blueprint(
        db=mock.MagicMock(), require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (viewer, None, None)))
    return app.test_client()


def base_queries(viewer=ADMIN):
    queries = mock.MagicMock()
    queries.load_access_context.return_value = {'user_id': viewer, 'name': 'Смотрящий', 'role': 'admin'}
    queries.roles_of_user.return_value = set()
    queries.is_manager_somewhere.return_value = False
    queries.role_member_ids.return_value = {}
    queries.duplicate_request_ids.return_value = set()
    queries.avatar_refs.return_value = {}
    return queries


class AccountingBoardDocsDeadlineTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def board(self, pairs):
        queries = base_queries()
        queries.board_requests.return_value = pairs
        response = app_for(queries).get('/api/payments/boards/accounting')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return {card['id']: card for column in response.get_json()['columns'] for card in column['cards']}

    def test_docs_column_card_carries_the_accountants_deadline(self):
        cards = self.board([(request_row(1, docs_due_on=date(2026, 11, 26)), at_closing())])
        self.assertEqual(cards[1]['column'], 'awaiting_docs')
        self.assertEqual(cards[1]['docs_due_on'], '2026-11-26')

    def test_without_the_accountants_date_it_counts_from_the_receipt(self):
        cards = self.board([(request_row(2), at_closing())])
        days = wf.SETTINGS_DEFAULTS[wf.SETTING_CLOSING_DOCS_DAYS]
        self.assertEqual(cards[2]['docs_due_on'], (RECEIVED.date() + timedelta(days=days)).isoformat())

    def test_request_closed_before_the_docs_keeps_its_deadline_on_the_card(self):
        tail = request_row(3, status='done', docs_deferred_at=datetime(2026, 11, 12, 9, 0),
                           docs_due_on=date(2026, 11, 20), docs_tail=True)
        cards = self.board([(tail, at_closing())])
        self.assertEqual((cards[3]['column'], cards[3]['docs_due_on'], cards[3]['docs_tail']),
                         ('awaiting_docs', '2026-11-20', True))

    def test_other_columns_have_no_docs_deadline(self):
        cards = self.board([(request_row(4, stage='payment', paid_on=None), at_payment())])
        self.assertNotEqual(cards[4]['column'], 'awaiting_docs')
        self.assertIsNone(cards[4]['docs_due_on'])


class AccountingDeskDocsDeadlineTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def desk(self, rows, steps):
        queries = base_queries(ACCOUNTANT)
        queries.load_access_context.return_value = {'user_id': ACCOUNTANT, 'name': 'Бухгалтер', 'role': 'operator'}
        queries.roles_of_user.return_value = {'accounting'}
        queries.desk_pairs.return_value = [(row['id'], item['kind']) for row, item in rows]
        queries.docs_wanted_ids.return_value = []
        queries.desk_page.return_value = rows
        queries.requests_by_ids.return_value = []
        queries.subtasks_for_requests.return_value = steps
        queries.own_requests.return_value = (0, [])
        with mock.patch.object(access, 'can_open_section', return_value=True):
            response = app_for(queries, ACCOUNTANT).get('/api/payments/desk')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return {task['id']: task for task in response.get_json()['tasks']}, queries

    def test_closing_task_shows_the_docs_deadline_not_the_payment_date(self):
        closing = at_closing()
        tasks, queries = self.desk([(request_row(5, docs_due_on=date(2026, 11, 24)), closing[-1])], {5: closing})
        self.assertEqual((tasks[5]['task'], tasks[5]['docs_due_on']), ('closing_docs', '2026-11-24'))
        queries.subtasks_for_requests.assert_any_call(mock.ANY, [5])

    def test_closed_request_with_docs_in_is_named_so_on_the_desk(self):
        """Заявку закрыли раньше документов, инициатор их приложил: на столе бухгалтерии она снова
        есть (queries._WAITS_FOR_ME_SQL) — и заголовок говорит, почему закрытая заявка в задачах."""
        closing = at_closing()
        tail = request_row(7, status='done', docs_deferred_at=datetime(2026, 11, 12, 9, 0), closing_docs_status='scan',
                           docs_due_on=date(2026, 11, 20), docs_tail=True)
        tasks, _queries = self.desk([(tail, closing[-1])], {7: closing})
        self.assertEqual((tasks[7]['title'], tasks[7]['docs_due_on'], tasks[7]['docs_tail']),
                         ('Проверьте документы закрытой заявки', '2026-11-20', True))

    def test_open_request_keeps_the_usual_title(self):
        closing = at_closing()
        tasks, _queries = self.desk([(request_row(8), closing[-1])], {8: closing})
        self.assertEqual(tasks[8]['title'], 'Проверьте закрывающие документы')

    def test_tasks_of_other_stages_carry_none(self):
        payment = at_payment()
        tasks, queries = self.desk([(request_row(6, stage='payment', paid_on=None), payment[-1])], {})
        self.assertIsNone(tasks[6]['docs_due_on'])
        # Задачи документов нет — подзадачи этой заявки ради срока не читаются.
        for call in queries.subtasks_for_requests.call_args_list:
            self.assertNotIn(6, call.args[1])


if __name__ == '__main__':
    unittest.main()


class TailDocsUploadWiringTests(unittest.TestCase):
    """POST /requests/<id>/attachments: документы в заявку, закрытую раньше них (п. 6). Сообщение
    бухгалтерии flow.files_added собирает в `outbox` внутри транзакции, а в Telegram оно уходит
    после неё (_flush) — как у действий этапов; ссылка — на адрес портала."""

    def setUp(self):
        queries = mock.MagicMock()
        queries.load_access_context.return_value = {'user_id': ADMIN, 'name': 'Админ', 'role': 'admin'}
        queries.roles_of_user.return_value = set()
        tail = request_row(9, status='done', docs_deferred_at=datetime(2026, 11, 12, 9, 0),
                           docs_due_on=date(2026, 11, 20), docs_tail=True, submitted_at='2026-11-01T10:00:00')
        queries.read_request.return_value = tail
        queries.list_subtasks.return_value = at_closing()
        queries.role_member_ids.return_value = {}
        queries.today_almaty.return_value = None
        for name in ('list_attachments', 'list_items', 'list_offers', 'list_events', 'payment_history',
                     'similar_purchases', 'find_duplicates', 'list_observers'):
            getattr(queries, name).return_value = []
        directory = mock.MagicMock()
        directory.read_contract.return_value = None
        directory.read_legal_entity.return_value = None
        directory.read_account.return_value = None
        directory.get_settings.return_value = {}
        assets_sql = mock.MagicMock()
        assets_sql.for_request.return_value = []
        self.calls = []

        def files_added(cursor, request_id, kinds, actor, *, outbox=None, base_url=None):
            self.calls.append((request_id, kinds, base_url))
            outbox.append((777, 'Проверьте документы закрытой заявки', None))
            return True

        for patcher in (mock.patch.object(routes, 'queries', queries),
                        mock.patch.object(routes, 'directory', directory),
                        mock.patch.object(routes, 'assets_sql', assets_sql),
                        mock.patch.object(routes, 'notices', mock.MagicMock()),
                        mock.patch.object(routes.files, 'upload', return_value=('b', 'p/act.pdf')),
                        mock.patch.object(routes.flow, 'files_added', side_effect=files_added),
                        mock.patch.object(routes.flow, 'submit_problems', return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = mock.MagicMock(return_value=mock.MagicMock(status_code=200))
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (ADMIN, None, None),
            send_telegram=self.sent, web_app_base_url='https://portal.example'))
        self.client = app.test_client()

    def test_message_leaves_after_the_transaction_with_the_portal_link(self):
        from io import BytesIO
        response = self.client.post('/api/payments/requests/9/attachments', content_type='multipart/form-data',
                                    data={'files': (BytesIO(b'%PDF-1.4'), 'act.pdf'), 'kinds': '["act"]'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.calls, [(9, ['act'], 'https://portal.example')])
        self.sent.assert_called_once()
        self.assertEqual(self.sent.call_args.args[:2], (777, 'Проверьте документы закрытой заявки'))
        self.assertEqual(response.get_json()['warnings'], [])
