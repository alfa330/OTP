# -*- coding: utf-8 -*-
"""«Мои заявки» на рабочем столе «Оплаты счетов» — просьба владельца 22.10.2026.

«В разделе оплата счетов, как человек сразу же сможет [видеть] свою задачу и текущий
статус» — рядом с задачами стола стоят отправленные заявки человека, которые идут у
других: на каком этапе каждая, у кого она и полоса этапов. Заявки, по которым ждут его
самого, стоят в задачах, и второй раз их не показываем.

Здесь — без базы (unittest.mock, как в test_payments_observers.py): что отдают /desk
(`mine`) и /ping (`counters.own`), что имя бухгалтера заменено подразделением (п. 9),
какие поля уходят наружу и что строка выборки разбирается по своим полям. Ответ /desk
сверяется с образцом tests/data/payments_desk_mine.json — по нему же рисует блок node-тест
(tests/payments_own_requests.test.mjs): ключи ответа у сервера и у интерфейса одни.
Что SQL отбора ЗНАЧИТ — на настоящем Postgres, tests/test_payments_own_requests_postgres.py.
"""

import json
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from payments import access, queries as real_queries, routes, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
INITIATOR, ACCOUNTANT, APPROVER = 10, 50, 70
SAMPLE = Path(__file__).parent / 'data' / 'payments_desk_mine.json'

# Поля строки «Моих заявок»: только то, что строка показывает, и ничего сверх — ни снимка
# условий заявки, ни карты, ни обоснования, ни инициатора (это он сам).
OWN_CARD_KEYS = {
    'id', 'expense_name', 'counterparty_name', 'amount', 'refund_amount', 'payment_method', 'due_on', 'paid_on',
    'status', 'stage', 'stage_label', 'current_kind', 'current_role', 'current_assignee_name', 'lifecycle',
}


def own_row(request_id, **overrides):
    """Строка заявки так, как её отдаёт queries.own_requests (_request_row)."""
    row = {
        'id': request_id, 'status': 'active', 'stage': 'approval', 'stage_label': 'Согласование',
        'state': 'active', 'request_kind': 'purchase', 'payment_method': 'invoice', 'object_type': 'service',
        'expense_name': 'Бумага А4', 'counterparty_name': 'ТОО «Бумага»', 'amount': 25000, 'refund_amount': None,
        'initiator_id': INITIATOR, 'initiator_name': 'Инициаторова Ирина', 'submitted_at': '2026-10-15T10:00:00',
        'due_on': '2026-10-30', 'paid_on': None, 'current_kind': 'approval', 'current_status': 'new',
        'current_role': 'approver', 'current_role_code': 'approver', 'current_assignee_id': APPROVER,
        'current_assignee_name': 'Утвердова Ульяна', 'clarify_reason': None, 'clarify_by_name': None,
        'clarify_comment': None, 'clarify_from': None, 'card_mask': '•••• 4242', 'justification': 'Нужна бумага',
        'submitted_snapshot': {'amount': 25000},
    }
    row.update(overrides)
    return row


def at_accounting(request_id=31):
    return own_row(request_id, stage='payment', stage_label='Оплата', current_kind='invoice_payment',
                   current_role='accounting', current_role_code='accounting', current_assignee_id=ACCOUNTANT,
                   current_assignee_name='Счётова Сауле', expense_name='Картриджи', counterparty_name='ИП Пример',
                   amount=45000, refund_amount=5000, due_on=None)


def subtask(request_id, kind, state, status=None):
    item = wf.subtask(kind)
    return {'id': request_id * 100 + item['position'], 'request_id': request_id, 'kind': kind,
            'position': item['position'], 'stage': item['stage'], 'role_code': item['role'],
            'state': state, 'status': status, 'title': item['title']}


def route(request_id, current):
    """Подзадачи заявки на счёт: всё до `current` выполнено, `current` открыта, дальше — впереди."""
    kinds = ['initiation', 'approval', 'invoice_payment', 'receiving', 'closing_docs']
    return [subtask(request_id, kind, 'done' if kinds.index(kind) < kinds.index(current)
                    else 'open' if kind == current else 'pending') for kind in kinds]


class _DeskBase(unittest.TestCase):
    """Blueprint раздела с подменённым слоем запросов; раздел открыт людям теста."""

    def setUp(self):
        patcher = mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS',
                                    tuple(access.SECTION_ALLOWED_USER_IDS) + (INITIATOR, ACCOUNTANT, APPROVER))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.me = INITIATOR
        self.queries = mock.MagicMock()
        self.queries.load_access_context.side_effect = lambda _c, uid: {'user_id': uid, 'name': 'Т', 'role': 'operator'}
        self.queries.roles_of_user.return_value = set()
        self.queries.desk_pairs.return_value = []
        self.queries.docs_wanted_ids.return_value = []
        self.queries.desk_page.return_value = []
        self.queries.requests_by_ids.return_value = []
        self.queries.role_member_ids.return_value = {'accounting': {ACCOUNTANT}, 'approver': {APPROVER}}
        self.queries.own_requests.return_value = (0, [])
        self.queries.subtasks_for_requests.return_value = {}
        patcher = mock.patch.object(routes, 'queries', self.queries)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.me, None, None), avatar_url=None))
        self.client = app.test_client()

    def desk(self):
        response = self.client.get('/api/payments/desk', query_string={'limit': 20, 'offset': 0})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def sample_desk(self):
        """Стол из образца. В задачах — своя заявка №55, по которой ждут получения; в «Моих
        заявках» — №40 у согласующего и №31 в бухгалтерии (всего таких заявок 7). №55 в блок не
        приходит: её отсеивает сам запрос (queries._OWN_SQL), здесь он подменён."""
        waiting = own_row(55, stage='receiving', stage_label='Получение', current_kind='receiving',
                          current_role='initiator', current_role_code='initiator', current_assignee_id=INITIATOR,
                          current_assignee_name='Инициаторова Ирина', expense_name='Стулья', paid_on='2026-10-14',
                          created_at='2026-10-12T09:30:00', department_name='IT-отдел')
        self.queries.desk_pairs.return_value = [(55, 'receiving')]
        self.queries.desk_page.return_value = [(waiting, dict(subtask(55, 'receiving', 'open', 'new'),
                                                              role_label='Инициатор',
                                                              assignee_name='Инициаторова Ирина'))]
        self.queries.own_requests.return_value = (7, [own_row(40), at_accounting(31)])
        self.queries.subtasks_for_requests.return_value = {40: route(40, 'approval'), 31: route(31, 'invoice_payment')}
        return self.desk()


class OwnRequestsOnDeskTests(_DeskBase):
    def test_desk_carries_own_requests_where_each_is_now(self):
        body = self.sample_desk()
        self.assertEqual(body['mine']['total'], 7, 'на счётчике — все такие заявки, а не показанные')
        items = body['mine']['items']
        self.assertEqual([item['id'] for item in items], [40, 31], 'порядок — как отдал запрос: новые сверху')
        self.queries.own_requests.assert_called_once_with(mock.ANY, INITIATOR, set(), False, limit=routes.OWN_ON_DESK)
        self.queries.subtasks_for_requests.assert_called_once_with(mock.ANY, [40, 31])
        for item in items:
            self.assertEqual(set(item), OWN_CARD_KEYS)
        first, second = items
        self.assertEqual(first['current_assignee_name'], 'Утвердова Ульяна', 'согласующего знают все люди заявки')
        self.assertEqual([stage['state'] for stage in first['lifecycle'] if stage['state'] != 'skipped'],
                         ['done', 'current', 'pending', 'pending', 'pending', 'pending'])
        self.assertEqual(next(stage['code'] for stage in second['lifecycle'] if stage['state'] == 'current'), 'payment')

    def test_desk_answer_matches_the_sample_the_interface_is_tested_on(self):
        """Образец tests/data/payments_desk_mine.json — это ответ этой ручки: по нему node-тест
        рисует блок. Переименуют поле здесь — упадёт этот тест, а не блок у людей."""
        body = self.sample_desk()
        sample = json.loads(SAMPLE.read_text(encoding='utf-8'))
        self.assertEqual(body, sample)

    def test_accountant_name_is_replaced_by_the_department(self):
        """П. 9: инициатор не знает, кто именно в бухгалтерии платит."""
        self.queries.own_requests.return_value = (1, [at_accounting()])
        self.queries.subtasks_for_requests.return_value = {31: route(31, 'invoice_payment')}
        self.assertEqual(self.desk()['mine']['items'][0]['current_assignee_name'], 'Бухгалтерия')
        # Администратор раздела имена подразделений видит.
        self.me = ADMIN
        self.queries.own_requests.return_value = (1, [at_accounting()])
        self.assertEqual(self.desk()['mine']['items'][0]['current_assignee_name'], 'Счётова Сауле')

    def test_roles_and_admin_right_reach_the_query(self):
        """«Ждёт ли заявка меня» считается теми же правами, что и задачи стола: роль и право
        администратора утверждать свою — иначе заявка встала бы и в задачи, и в блок."""
        self.queries.roles_of_user.return_value = {'accounting'}
        self.desk()
        self.queries.own_requests.assert_called_with(mock.ANY, INITIATOR, {'accounting'}, False, limit=routes.OWN_ON_DESK)
        self.me = ADMIN
        self.desk()
        self.queries.own_requests.assert_called_with(mock.ANY, ADMIN, {'accounting'}, True, limit=routes.OWN_ON_DESK)

    def test_no_own_requests_means_empty_block(self):
        body = self.desk()
        self.assertEqual(body['mine'], {'items': [], 'total': 0})
        self.queries.subtasks_for_requests.assert_called_once_with(mock.ANY, [])

    def test_ping_counts_own_requests_for_the_first_frame(self):
        self.queries.role_members.return_value = {}
        self.queries.desk_count.return_value = 2
        self.queries.own_open_count.return_value = 4
        self.queries.is_manager_somewhere.return_value = False
        with mock.patch.object(routes.schema, 'schema_ready', return_value=True):
            body = self.client.get('/api/payments/ping').get_json()
        self.assertEqual(body['counters'], {'desk': 2, 'own': 4})
        self.queries.own_open_count.assert_called_once_with(mock.ANY, INITIATOR, set(), False)
        # Администратор утверждает и свои заявки — для счётчика они его задачи, как и на столе.
        self.me = ADMIN
        with mock.patch.object(routes.schema, 'schema_ready', return_value=True):
            self.client.get('/api/payments/ping')
        self.queries.own_open_count.assert_called_with(mock.ANY, ADMIN, set(), True)

    def test_ping_before_the_schema_is_ready_counts_nothing(self):
        """Раздел ещё разворачивается — таблиц нет, считать нечего (запрос упал бы)."""
        self.queries.role_members.return_value = {}
        self.queries.is_manager_somewhere.return_value = False
        with mock.patch.object(routes.schema, 'schema_ready', return_value=False):
            body = self.client.get('/api/payments/ping').get_json()
        self.assertEqual(body['counters'], {'desk': 0, 'own': 0})
        self.queries.own_open_count.assert_not_called()


class _Cursor:
    """Курсор, который запоминает запрос и отдаёт заранее заданные строки."""

    def __init__(self, rows=(), one=(0,)):
        self.calls = []
        self.rows = list(rows)
        self.one = one

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


def selected_row(**values):
    """Строка выборки own_requests так, как её отдаёт Postgres: total, поля заявки, дополнения."""
    fields = real_queries._REQUEST_FIELDS + real_queries._REQUEST_EXTRA
    data = dict.fromkeys(fields)
    data.update({'status': 'active', 'stage': 'payment', 'attachments_count': 0, 'has_card': False})
    data.update(values)
    return (values.pop('total', 1),) + tuple(data[name] for name in fields)


class OwnSqlTests(unittest.TestCase):
    """Что запрос НАПИСАН и как разобрана его строка; что он значит — в *_postgres.py."""

    def test_own_requests_are_sent_active_not_waiting_for_me_newest_first(self):
        cursor = _Cursor()
        self.assertEqual(real_queries.own_requests(cursor, INITIATOR, {'approver'}, True, limit=5), (0, []))
        sql, params = cursor.calls[0]
        self.assertIn("r.initiator_id = %(user_id)s AND r.status = 'active' AND r.submitted_at IS NOT NULL", sql)
        # Ждёт ли заявка самого человека — тем же условием, что задачи стола и «приложите документы».
        self.assertIn('NOT EXISTS (SELECT 1 FROM payment_subtasks s WHERE s.request_id = r.id AND '
                      + ' '.join(real_queries._WAITS_FOR_ME_SQL.split()) + ')', sql)
        self.assertIn("AND NOT (r.closing_docs_status = 'none' AND EXISTS (SELECT 1 FROM payment_subtasks c "
                      "WHERE c.request_id = r.id AND c.kind = %(closing)s AND c.state = 'open'))", sql)
        self.assertTrue(sql.endswith('ORDER BY r.id DESC LIMIT %(limit)s'), sql[-80:])
        self.assertIn('COUNT(*) OVER () AS total', sql)
        self.assertEqual(params, dict(real_queries._desk_params(INITIATOR, {'approver'}, True), limit=5))

    def test_own_requests_row_is_read_field_by_field(self):
        """Строка выборки начинается с total: срез [1:] — и поля встают на свои места."""
        cursor = _Cursor(rows=[selected_row(total=7, id=31, expense_name='Картриджи', current_role_code='accounting'),
                               selected_row(total=7, id=30, expense_name='Бумага', stage='approval')])
        total, rows = real_queries.own_requests(cursor, INITIATOR, set(), limit=5)
        self.assertEqual(total, 7)
        self.assertEqual([(row['id'], row['expense_name'], row['stage_label']) for row in rows],
                         [(31, 'Картриджи', 'Оплата'), (30, 'Бумага', 'Согласование')])
        self.assertEqual(rows[0]['current_role'], 'accounting')

    def test_counter_uses_the_same_condition(self):
        cursor = _Cursor(one=(7,))
        self.assertEqual(real_queries.own_open_count(cursor, INITIATOR, set(), False), 7)
        sql, params = cursor.calls[0]
        self.assertEqual(sql, ' '.join(('SELECT COUNT(*) FROM payment_requests r WHERE ' + real_queries._OWN_SQL).split()))
        self.assertEqual(params, real_queries._desk_params(INITIATOR, set(), False))


if __name__ == '__main__':
    unittest.main()
