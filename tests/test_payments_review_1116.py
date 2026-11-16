# -*- coding: utf-8 -*-
"""«Оплата счетов», разбор выложенных правок владельца 16.11.2026 — без базы, чтобы ловил CI.

Postgres-тесты правок (test_payments_owner_1116_postgres.py) в CI пропускаются: там нет
PAYMENTS_TEST_PORT. Здесь — то же поведение через настоящие ручки Flask и функции flow на
подменённых запросах (харнесс tests/test_payments_no_contract_routes.py):

* документы оплаты — флаги при смене способа оплаты и правке других полей;
* отдел проекта в заявке — только из отделов выбранного проекта; проект убрали — отдел уходит без
  ошибки; скрытый в справочнике отдел заново не ставится, уже стоящий остаётся;
* справочник: проект у заведённого отдела не меняется (перенос дал бы заявкам пару «проект А →
  отдел проекта Б»);
* заявка, закрытая раньше закрывающих документов: что может бухгалтерия и инициатор, инициатор
  снимает свой ошибочный закрывающий документ; доска — только у бухгалтерии;
* перенос срока документов гасит строки колокола со старым сроком.
"""
import unittest
from datetime import date, datetime
from unittest import mock

from payments import access, flow, queries as queries_mod, workflow as wf
from tests.test_payments_no_contract_routes import REQUEST_ID, _Routes, purchase

ACCOUNTANT, INITIATOR = 467, 455     # из периметра раздела (access.SECTION_ALLOWED_USER_IDS), не администраторы
assert ACCOUNTANT in access.SECTION_ALLOWED_USER_IDS and INITIATOR in access.SECTION_ALLOWED_USER_IDS
assert ACCOUNTANT not in access.SECTION_ADMIN_USER_IDS and INITIATOR not in access.SECTION_ADMIN_USER_IDS


def tail_row(**overrides):
    """Заявка, закрытая раньше закрывающих документов: закрыта, этап документов открыт, скана нет."""
    row = purchase(status='done', stage='closing', initiator_id=INITIATOR, initiator_name='Аскаров',
                   docs_deferred_at=datetime(2026, 11, 12, 9, 0), docs_due_on=date(2026, 11, 20),
                   closing_docs_status='none', closed_at=datetime(2026, 11, 12, 9, 0), paid_on=date(2026, 11, 9),
                   submitted_at=datetime(2026, 11, 1, 10, 0))
    row.update(overrides)
    return row


def tail_route(request):
    """Маршрут заявки: всё пройдено, кроме этапа закрывающих документов."""
    built = wf.build_route(request, initiator={'id': INITIATOR, 'name': 'Аскаров'}, manager={'id': 242, 'name': 'Руководитель'})
    return [dict(item, id=index, state='open' if item['kind'] == wf.KIND_CLOSING else 'done', outcome=None, status=None)
            for index, item in enumerate(built, start=1)]


class _As(_Routes):
    def as_user(self, user_id, roles=()):
        self.queries.load_access_context.return_value = {'user_id': user_id, 'name': 'Человек %s' % user_id,
                                                         'role': 'operator'}
        self.queries.roles_of_user.return_value = set(roles)
        self.queries.role_member_ids.return_value = {'accounting': {ACCOUNTANT}}


class ProofFlagRoutes(_Routes):
    def test_switch_to_card_drops_invoice_flags(self):
        client = self.client(purchase(needs_payment_order=True, needs_power_of_attorney=True))
        fields = self.patch(client, {'payment_method': 'card', 'card_recipient': 'employee',
                                     'card_holder_user_id': 10, 'card_holder_name': 'И', 'payment_purpose': 'x'})
        self.assertEqual((fields['needs_payment_order'], fields['needs_power_of_attorney'], fields['needs_transfer_proof']),
                         (False, False, False))

    def test_transfer_proof_on_an_invoice_is_dropped(self):
        client = self.client(purchase())
        self.assertIs(self.patch(client, {'needs_transfer_proof': True})['needs_transfer_proof'], False)

    def test_one_flag_edited_keeps_the_other(self):
        client = self.client(purchase(needs_payment_order=True))
        fields = self.patch(client, {'needs_power_of_attorney': True})
        self.assertEqual((fields['needs_payment_order'], fields['needs_power_of_attorney']), (True, True))


class ProjectDepartmentRoutes(_Routes):
    def departments(self, *rows):
        known = {row['id']: dict(row) for row in rows}
        self.directory.read_project_department.side_effect = lambda cursor, did: known.get(did)

    def test_department_of_another_project_is_refused(self):
        client = self.client(purchase(project_id=5))
        self.departments({'id': 9, 'project_id': 77, 'name': 'Чужой', 'is_active': True})
        response = client.patch('/api/payments/requests/%s' % REQUEST_ID, json={'project_department_id': 9})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Отдел не относится к выбранному проекту', response.get_json()['error'])

    def test_project_change_drops_the_old_department(self):
        client = self.client(purchase(project_id=5, project_department_id=9))
        self.departments({'id': 9, 'project_id': 5, 'name': 'КЦ', 'is_active': True})
        self.assertIsNone(self.patch(client, {'project_id': 6})['project_department_id'])

    def test_project_removed_takes_the_department_away_without_an_error(self):
        """Форма убрала поле «Проект», а отдел ещё прислала: без проекта отдел уходит, ошибки нет."""
        client = self.client(purchase(project_id=5, project_department_id=9))
        self.departments({'id': 9, 'project_id': 5, 'name': 'КЦ', 'is_active': True})
        fields = self.patch(client, {'project_id': None, 'project_department_id': 9})
        self.assertEqual((fields['project_id'], fields['project_department_id']), (None, None))

    def test_hidden_department_is_not_set_again_but_the_saved_one_stays(self):
        hidden = {'id': 9, 'project_id': 5, 'name': 'Старый', 'is_active': False}
        client = self.client(purchase(project_id=5, project_department_id=None))
        self.departments(hidden)
        response = client.patch('/api/payments/requests/%s' % REQUEST_ID, json={'project_department_id': 9})
        self.assertEqual(response.status_code, 400)
        self.assertIn('скрыт в справочнике', response.get_json()['error'])
        self.row = purchase(project_id=5, project_department_id=9)
        fields = self.patch(client, {'project_department_id': 9, 'expense_name': 'Столы и стулья'})
        self.assertEqual(fields['project_department_id'], 9)


class DepartmentProjectIsFixedTests(_Routes):
    def save(self, client, body):
        return client.post('/api/payments/dictionaries/project_departments', json=body)

    def setUp(self):
        self.client_ = self.client(purchase())
        self.directory.find_duplicate_name.return_value = None
        self.directory.list_projects.return_value = [{'id': 5, 'name': 'Tez'}, {'id': 6, 'name': 'Яндекс'}]
        self.directory.read_project_department.return_value = {'id': 9, 'project_id': 5, 'name': 'КЦ', 'is_active': True}
        self.directory.upsert_project_department.return_value = 9

    def test_existing_department_does_not_move_to_another_project(self):
        response = self.save(self.client_, {'id': 9, 'project_id': 6, 'name': 'КЦ'})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['code'], 'PAYMENTS_DEPARTMENT_PROJECT_FIXED')
        self.directory.upsert_project_department.assert_not_called()

    def test_rename_inside_its_project_is_saved(self):
        response = self.save(self.client_, {'id': 9, 'project_id': 5, 'name': 'Колл-центр'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.directory.upsert_project_department.call_args.kwargs['project_id'], 5)

    def test_new_department_takes_any_project(self):
        response = self.save(self.client_, {'project_id': 6, 'name': 'Продажи'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.directory.read_project_department.assert_not_called()


class DeferredRequestCardTests(_As):
    """GET карточки заявки, закрытой раньше документов: что кому можно (TAIL_ACTIONS, задача инициатора)."""

    def card(self, user_id, roles=()):
        row = tail_row()
        client = self.client(row)
        self.as_user(user_id, roles)
        self.queries.list_subtasks.return_value = tail_route(row)
        response = client.get('/api/payments/requests/%s' % REQUEST_ID)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['permissions']

    def test_accounting_has_only_the_document_actions(self):
        permissions = self.card(ACCOUNTANT, {'accounting'})
        self.assertTrue(permissions['can_act'])
        self.assertEqual(permissions['actions'], ['docs_original', 'docs_close', 'docs_due'],
                         '«закрыть заявку, документы — позже» у закрытой заявки нет')
        self.assertFalse(permissions['can_upload_docs'])

    def test_initiator_has_the_upload_task_and_no_actions(self):
        permissions = self.card(INITIATOR)
        self.assertTrue(permissions['can_upload_docs'])
        self.assertFalse(permissions['can_act'])
        self.assertEqual(permissions.get('actions') or [], [])


class DeferredRequestFileRemovalTests(_As):
    """DELETE вложения: в закрытой раньше документов заявке свой закрывающий документ снимают, как в идущей."""

    def remove(self, kind, closing_docs_status='scan'):
        row = tail_row(closing_docs_status=closing_docs_status)
        client = self.client(row)
        self.as_user(INITIATOR)
        self.queries.list_subtasks.return_value = tail_route(row)
        attachment = {'id': 50, 'request_id': REQUEST_ID, 'kind': kind, 'uploaded_by': INITIATOR, 'subtask_kind': None,
                      'bucket': 'b', 'blob_path': 'p/x.pdf'}
        self.queries.read_attachment.return_value = attachment
        # Проверке «единственный закрывающий документ» — два файла; карточке после удаления — пусто.
        calls = iter([[attachment, dict(attachment, id=51, kind='waybill')]])
        self.queries.list_attachments.side_effect = lambda *args, **kwargs: next(calls, [])
        return client.delete('/api/payments/requests/%s/attachments/50' % REQUEST_ID)

    def test_own_closing_document_is_removed(self):
        response = self.remove('act')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.queries.remove_attachment.assert_called_once()

    def test_other_files_of_a_closed_request_stay(self):
        response = self.remove('other')
        self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        self.queries.remove_attachment.assert_not_called()


class BoardTailClauseTests(unittest.TestCase):
    """Срок давности досок: исключение для закрытой раньше документов — только у доски бухгалтерии."""

    def sql_of(self, code):
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []
        queries_mod.board_requests(cursor, code)
        return cursor.execute.call_args_list[0].args[0]

    def test_only_accounting_keeps_the_deferred_request(self):
        self.assertIn('r.docs_deferred_at IS NOT NULL', self.sql_of(wf.BOARD_ACCOUNTING))
        for code in (wf.BOARD_APPROVAL, 'finance'):
            self.assertNotIn('r.docs_deferred_at IS NOT NULL', self.sql_of(code), code)


class DocsDeadlineNoticesTests(unittest.TestCase):
    """flow._docs_due: строки колокола со старым сроком гаснут, задача инициатора — с новой датой."""

    def run_due(self, request):
        queries = mock.MagicMock()
        queries.list_subtasks.return_value = []
        queries.read_request.return_value = request
        notices = mock.MagicMock()
        directory = mock.MagicMock()
        directory.get_settings.return_value = {}
        with mock.patch.object(flow, 'queries', queries), mock.patch.object(flow, 'notices', notices), \
                mock.patch.object(flow, 'directory', directory), mock.patch.object(flow, '_notify_initiator') as told:
            flow._docs_due(mock.MagicMock(), request, {'kind': wf.KIND_CLOSING}, {'id': ACCOUNTANT, 'name': 'Б'},
                           date(2026, 12, 6), None)
        cleared = [call.args[2] for call in notices.clear_kind.call_args_list]
        return cleared, notices.retitle_kind.call_args_list, told

    def test_old_deadline_lines_go_and_the_task_gets_the_new_date(self):
        cleared, retitled, told = self.run_due(purchase(initiator_id=INITIATOR, closing_docs_status='none', docs_due_on=None))
        self.assertEqual(set(cleared), {'docs_due_soon', 'docs_overdue', 'docs_due'})
        self.assertEqual([call.args[2:] for call in retitled],
                         [('docs_needed', 'Приложите закрывающие документы до 06.12.2026', [INITIATOR])])
        told.assert_called_once()

    def test_closed_request_keeps_its_own_wording(self):
        _cleared, retitled, _told = self.run_due(tail_row(docs_due_on=None))
        self.assertEqual(retitled[0].args[3], 'Заявка закрыта — приложите закрывающие документы до 06.12.2026')

    def test_with_the_scan_in_only_the_old_lines_go(self):
        cleared, retitled, told = self.run_due(purchase(initiator_id=INITIATOR, closing_docs_status='scan', docs_due_on=None))
        self.assertEqual(set(cleared), {'docs_due_soon', 'docs_overdue', 'docs_due'})
        self.assertEqual(retitled, [])
        told.assert_not_called()


if __name__ == '__main__':
    unittest.main()
