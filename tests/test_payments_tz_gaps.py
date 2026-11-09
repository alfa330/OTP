# -*- coding: utf-8 -*-
"""Сверка ТЗ «Закуп и оплата» 09.11.2026: доделанные пункты — без базы.

Слово владельца: «кроме первого, до 10 все нужно реализовать» — то есть всё, что сверка
назвала сделанным не дословно, кроме подсказки согласующего в форме:

* п. 7 / п. 9 — карточки досок: имена людей (node-тест), полный номер карты финотделу;
* п. 8 — НДС поставщика «не указан» указывает бухгалтер в окне оплаты; «Прошлые оплаты»
  поставщику — своей выборкой;
* п. 10.2 — стоимость 0 не проходит (test_payments_workflow.py);
* п. 11 — акт из истории имущества скачивает тот, кто видит имущество;
* п. 12 — смена компании-владельца — строка истории (Postgres-тест);
* п. 17 — свои сроки у чека и закрывающих документов, напоминания по ним;
* п. 18 — удаление уводит заявку в архив «Удалённые», а не стирает.

Здесь — права, правила сроков, тексты, кому уходят напоминания, и что делают ручки.
Что SQL ЗНАЧИТ — tests/test_payments_tz_gaps_postgres.py.
"""

import unittest
from datetime import date, datetime
from unittest import mock

from payments import access, flow, notify, queries as real_queries, routes, workflow as wf
from tests.test_payments_observers import (
    ADMIN, APPROVER, INITIATOR, OUTSIDER, REQUEST_ID, WATCHER, WiderPerimeter, _RoutesBase, ctx, request_row,
)

ACCOUNTANT, FINANCE, KEEPER = 50, 55, 60
DELETED = {'deleted_at': '2026-11-09T10:00:00', 'deleted_by': ADMIN, 'deleted_by_name': 'Админ',
           'deleted_comment': 'дубль'}


def open_subtask(kind, **extra):
    item = {'id': 7, 'kind': kind, 'state': 'open', 'status': 'new', 'role_code': wf.subtask(kind)['role'],
            'assignee_id': None, 'title': wf.subtask_title(kind), 'role_label': 'Бухгалтерия',
            'stage': wf.subtask(kind)['stage'], 'position': wf.subtask(kind)['position']}
    item.update(extra)
    return item


# ─── П. 18: права на заявку в архиве ─────────────────────────────────────────

class DeletedRequestRightsTests(WiderPerimeter):
    def test_only_the_section_admin_sees_it(self):
        row = request_row(**DELETED)
        self.assertTrue(access.is_deleted(row))
        self.assertTrue(access.can_see_request(ctx(ADMIN), row))
        for who, roles in ((INITIATOR, set()), (ACCOUNTANT, {'accounting'}), (APPROVER, {'approver'})):
            with self.subTest(who=who):
                self.assertFalse(access.can_see_request(ctx(who), row, roles, participant=True, observer=True))

    def test_nothing_changes_it_admin_included(self):
        row = request_row(**DELETED, stage='initiation', paid_on='2026-11-01')
        for who in (ADMIN, INITIATOR):
            with self.subTest(who=who):
                self.assertFalse(access.can_edit_request(ctx(who), row))
                self.assertFalse(access.can_cancel_request(ctx(who), dict(row, paid_on=None)))
                self.assertFalse(access.can_manage_observers(ctx(who), row))
                self.assertFalse(access.can_refund(ctx(who), row, {'accounting'}))
                self.assertFalse(access.can_attach(ctx(who), row, {'accounting'}, can_act=True))
                self.assertFalse(access.can_delete_request(ctx(who), row))
                self.assertFalse(access.can_act_on_subtask(ctx(who), open_subtask('approval'), {'approver': {who}}, row))
        self.assertFalse(access.with_initiator(row))
        # Живая заявка — как раньше.
        live = request_row()
        self.assertTrue(access.can_delete_request(ctx(ADMIN), live))
        self.assertFalse(access.can_delete_request(ctx(INITIATOR), live))
        self.assertTrue(access.can_attach(ctx(ADMIN), None), 'без заявки — прежний ответ администратору')

    def test_request_state_is_deleted_whatever_the_status(self):
        for status in ('active', 'done', 'rejected', 'cancelled'):
            self.assertEqual(wf.request_state(request_row(status=status, **DELETED), date(2026, 11, 9)), 'deleted')
        self.assertEqual(wf.request_state(request_row(), date(2026, 11, 9)), 'active')

    def test_excel_row_of_an_archived_request(self):
        from payments import report
        row = report.enrich(request_row(state='deleted', **DELETED), [])
        self.assertEqual((row['state_label'], row['stage_text']), ('Удалена', ''))
        live = report.enrich(request_row(state='active'), [])
        self.assertEqual((live['state_label'], live['stage_text']), ('В работе', 'Согласование'))


class FlowDeleteTests(unittest.TestCase):
    def test_delete_marks_logs_and_clears_the_bell(self):
        row = request_row(stage='payment')
        cursor = mock.MagicMock()
        with mock.patch.object(flow, 'queries') as queries, mock.patch.object(flow, 'notices') as notices:
            queries.read_request.return_value = row
            flow.delete(cursor, REQUEST_ID, {'id': ADMIN, 'name': 'Админ'}, 'дубль')
        queries.read_request.assert_called_once_with(cursor, REQUEST_ID, lock=True)
        queries.mark_deleted.assert_called_once_with(cursor, REQUEST_ID, {'id': ADMIN, 'name': 'Админ'}, 'дубль')
        notices.clear_all.assert_called_once_with(cursor, REQUEST_ID)
        queries.log_event.assert_called_once_with(cursor, REQUEST_ID, 'deleted', {'id': ADMIN, 'name': 'Админ'},
                                                  comment='дубль', payload={'status': 'active', 'stage': 'payment'})

    def test_deleted_or_missing_request_is_refused(self):
        for found, code, status in ((request_row(**DELETED), 'PAYMENT_REQUEST_DELETED', 409),
                                    (None, 'PAYMENT_REQUEST_NOT_FOUND', 404)):
            with mock.patch.object(flow, 'queries') as queries, mock.patch.object(flow, 'notices'):
                queries.read_request.return_value = found
                with self.assertRaises(flow.FlowError) as raised:
                    flow.delete(mock.MagicMock(), REQUEST_ID, {'id': ADMIN})
                with self.assertRaises(flow.FlowError) as acted:
                    flow.load_for_action(mock.MagicMock(), REQUEST_ID, 'approval')
            self.assertEqual((raised.exception.code, raised.exception.status), (code, status))
            self.assertEqual(acted.exception.code, code)
            queries.mark_deleted.assert_not_called()


class _RecordingCursor:
    """Курсор, который только записывает SQL: что запрос НАПИСАН. Тесты на настоящем Postgres
    (*_postgres.py) в CI пропускаются — условие «не в архиве» сторожит здесь и там."""

    def __init__(self):
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append(' '.join(str(sql).split()))

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,) * 12

    @property
    def sql(self):
        return '\n'.join(self.calls)


class ArchiveSqlIsWrittenTests(unittest.TestCase):
    """П. 18: каждая выборка рабочих списков отсекает архив «Удалённые»."""

    def sql_of(self, call, *args, **kwargs):
        cursor = _RecordingCursor()
        call(cursor, *args, **kwargs)
        return cursor.sql

    def test_registry_and_its_counters(self):
        self.assertIn('(r.deleted_at IS NULL)', self.sql_of(real_queries.list_requests, viewer_id=1))
        self.assertIn('(r.deleted_at IS NOT NULL)',
                      self.sql_of(real_queries.list_requests, state='deleted', deleted_visible=True, viewer_id=ADMIN))
        self.assertIn('(FALSE)', self.sql_of(real_queries.list_requests, state='deleted', viewer_id=1),
                      'не администратору срез «Удалённые» пуст')
        counters = self.sql_of(real_queries.state_counters, viewer_id=ADMIN, deleted_visible=True)
        self.assertIn('COUNT(*) FILTER (WHERE r.deleted_at IS NOT NULL) AS deleted', counters)
        self.assertIn("COUNT(*) FILTER (WHERE r.deleted_at IS NULL AND r.status = 'active') AS open_count", counters)
        self.assertIn('(r.deleted_at IS NULL)', self.sql_of(real_queries.state_counters, viewer_id=1))

    def test_boards_desk_and_reminders(self):
        self.assertIn('(r.deleted_at IS NULL)', self.sql_of(real_queries.board_requests, 'approval'))
        self.assertIn("r.status = 'active' AND r.deleted_at IS NULL AND s.state = 'open'",
                      self.sql_of(real_queries.desk_pairs, 1, set()))
        desk = self.sql_of(real_queries.desk_count, 1, set())
        self.assertEqual(desk.count('r.deleted_at IS NULL'), 2, 'и задачи, и «приложите документы»')
        self.assertIn('r.deleted_at IS NULL', self.sql_of(real_queries.docs_wanted_ids, 1))
        self.assertIn('r.deleted_at IS NULL', self.sql_of(real_queries.own_open_count, 1, set()))
        self.assertIn('r.deleted_at IS NULL', self.sql_of(real_queries.role_backlog, 'approver'))
        self.assertIn("WHERE status = 'active' AND deleted_at IS NULL", self.sql_of(real_queries.due_requests, date(2026, 11, 9), 2))
        self.assertIn("WHERE r.status = 'active' AND r.deleted_at IS NULL AND s.state = 'open'",
                      self.sql_of(real_queries.stage_deadline_rows))

    def test_payment_references(self):
        request = {'id': 1, 'counterparty_id': 2, 'invoice_number': '7', 'invoice_date': date(2026, 11, 1), 'amount': 10}
        self.assertIn('AND r.deleted_at IS NULL', self.sql_of(real_queries.find_duplicates, request))
        self.assertIn('AND b.deleted_at IS NULL', self.sql_of(real_queries.duplicate_request_ids, [1]))
        self.assertIn("r.status IN ('active', 'done') AND r.deleted_at IS NULL",
                      self.sql_of(real_queries.payment_history, counterparty_id=2))
        from payments import directory as real_directory, legacy as real_legacy
        self.assertIn('r.deleted_at IS NULL', real_legacy._PENDING_SQL)
        self.assertIn("r.status = 'active' AND r.deleted_at IS NULL", ' '.join(real_directory._TEMPLATE_SQL.split()),
                      'регулярный платёж: «открытая заявка» — не из архива')


class _ScriptedCursor(_RecordingCursor):
    """Записывает SQL с параметрами и отвечает по очереди заготовленными строками."""

    def __init__(self, answers=()):
        super().__init__()
        self.params = []
        self.answers = list(answers)

    def execute(self, sql, params=None):
        super().execute(sql, params)
        self.params.append(params)
        self.rowcount = 1

    def fetchone(self):
        return self.answers.pop(0) if self.answers else None


class WrittenWithoutPostgresTests(unittest.TestCase):
    """То, что иначе держали бы только тесты на Postgres (в CI они пропускаются)."""

    def test_supplier_typed_by_name_gets_no_vat(self):
        from payments import directory as real_directory
        cursor = _ScriptedCursor(answers=[None, (77,)])
        self.assertEqual(real_directory.find_or_create_counterparty(cursor, 'Sulpak', 10), (77, True))
        insert = next(index for index, sql in enumerate(cursor.calls) if sql.startswith('INSERT INTO payment_counterparties'))
        columns = cursor.calls[insert].split('(', 1)[1].split(')', 1)[0].replace(' ', '').split(',')
        self.assertIsNone(cursor.params[insert][columns.index('vat_payer')], 'НДС не указан, а не «без НДС»')

    def test_accountant_sets_only_unknown_vat(self):
        from payments import directory as real_directory
        cursor = _ScriptedCursor()
        self.assertTrue(real_directory.set_counterparty_vat(cursor, 7, False))
        self.assertEqual(cursor.calls, ['UPDATE payment_counterparties SET vat_payer = %s WHERE id = %s AND vat_payer IS NULL'])
        self.assertEqual(cursor.params, [(False, 7)])

    def test_owner_company_change_writes_a_history_row(self):
        from payments import assets as real_assets
        current = {'legal_entity_id': 1, 'legal_entity_name': 'ТОО «Первая»', 'responsible_user_id': 10,
                   'responsible_name': 'Инициаторова', 'status': 'in_use', 'city': 'Алматы'}
        cursor = _ScriptedCursor(answers=[('ТОО «Вторая»',)])
        real_assets.update_details(cursor, 5, {'legal_entity_id': 2}, actor={'id': 60, 'name': 'Складов'}, current=current)
        moves = [(sql, params) for sql, params in zip(cursor.calls, cursor.params) if 'INSERT INTO payment_asset_moves' in sql]
        self.assertEqual(len(moves), 1)
        params = moves[0][1]
        self.assertEqual(params[1], 'owner')
        self.assertIn(('ТОО «Первая»', 'ТОО «Вторая»'), [(params[11], params[19])])
        cursor = _ScriptedCursor()
        real_assets.update_details(cursor, 5, {'legal_entity_id': 1, 'note': 'стол'}, actor={'id': 60}, current=current)
        self.assertFalse([sql for sql in cursor.calls if 'payment_asset_moves' in sql], 'компания та же — истории нет')

    def test_stock_issue_of_an_archived_request_does_not_hold_the_item(self):
        from payments import assets as real_assets
        cursor = _RecordingCursor()
        real_assets.issued_from_stock(cursor, [5])
        self.assertIn('JOIN payment_requests er ON er.id = e.request_id AND er.deleted_at IS NULL', cursor.sql)

    def test_payment_settles_the_vat_choice(self):
        """«Оплачено» с выбором НДС кладёт его в карточку поставщика (flow.act → _settle_supplier_vat)."""
        row = request_row(stage='payment', counterparty_id=7, counterparty_vat=None)
        with mock.patch.object(flow, 'queries') as queries, mock.patch.object(flow, 'notices'), \
                mock.patch.object(flow, 'directory') as directory, mock.patch.object(flow, 'advance', return_value=None), \
                mock.patch.object(flow, '_notify_initiator'):
            queries.read_request.return_value = row
            queries.read_subtask.return_value = open_subtask('invoice_payment')
            queries.list_attachments.return_value = [{'kind': 'payment_order', 'subtask_kind': 'invoice_payment'}]
            queries.update_request_fields.return_value = {}
            directory.set_counterparty_vat.return_value = True
            flow.act(mock.MagicMock(), REQUEST_ID, 'invoice_payment', 'pay', {'id': ACCOUNTANT, 'name': 'Т'},
                     fields={'paid_on': date(2026, 11, 9), 'paid_amount': 25000, 'supplier_vat': True},
                     outbox=[], base_url=None)
            directory.set_counterparty_vat.assert_called_once_with(mock.ANY, 7, True)
            kinds = [call.args[2] for call in queries.log_event.call_args_list]
            self.assertLess(kinds.index('supplier_vat'), kinds.index('paid'))
            # НДС уже известен — выбор не нужен и в карточку не пишется.
            directory.set_counterparty_vat.reset_mock()
            queries.read_request.return_value = dict(row, counterparty_vat=False)
            flow.act(mock.MagicMock(), REQUEST_ID, 'invoice_payment', 'pay', {'id': ACCOUNTANT, 'name': 'Т'},
                     fields={'paid_on': date(2026, 11, 9), 'paid_amount': 25000}, outbox=[], base_url=None)
            directory.set_counterparty_vat.assert_not_called()

    def test_card_view_is_journaled_once_per_person_and_number(self):
        cursor = _ScriptedCursor()
        real_queries.log_card_shown(cursor, [3, 1, 3], {'id': 55, 'name': 'Финансов'})
        self.assertEqual(len(cursor.calls), 1)
        sql = cursor.calls[0]
        self.assertIn("jsonb_build_object('shown', TRUE, 'last4', r.card_last4)", sql)
        self.assertIn("WHERE r.id = ANY(%(ids)s) AND NOT EXISTS (SELECT 1 FROM payment_events e WHERE e.request_id = r.id "
                      "AND e.kind = 'card_revealed' AND e.actor_id = %(actor_id)s AND e.payload ? 'shown' "
                      "AND e.payload->>'last4' IS NOT DISTINCT FROM r.card_last4)", sql)
        self.assertIn('ON CONFLICT DO NOTHING', sql, 'вторая вкладка в ту же секунду — без второй строки')
        from payments import schema as real_schema
        self.assertTrue(any('ux_payment_events_card_shown' in statement and "payload ? 'shown'" in statement
                            for statement in real_schema.statements()))
        self.assertEqual(cursor.params[0]['ids'], [1, 3])
        cursor = _ScriptedCursor()
        self.assertEqual(real_queries.log_card_shown(cursor, [3], {'id': None}), 0)
        self.assertEqual(cursor.calls, [], 'без человека журнал не пишется')


# ─── П. 17: сроки этапов после оплаты ────────────────────────────────────────

class StageDeadlineRulesTests(unittest.TestCase):
    TODAY = date(2026, 11, 9)

    def test_settings_give_the_days(self):
        self.assertEqual(wf.SETTINGS_DEFAULTS[wf.SETTING_RECEIPT_DAYS], 3, 'решение владельца: чек — 3 дня')
        self.assertEqual(wf.SETTINGS_DEFAULTS[wf.SETTING_CLOSING_DOCS_DAYS], 10, 'документы — 10 дней')
        self.assertEqual(wf.stage_due_on('receipt_confirm', date(2026, 11, 6)), date(2026, 11, 9))
        self.assertEqual(wf.stage_due_on('closing_docs', datetime(2026, 11, 1, 23, 50)), date(2026, 11, 11))
        self.assertEqual(wf.stage_due_on('receipt_confirm', date(2026, 11, 6), {wf.SETTING_RECEIPT_DAYS: 5}),
                         date(2026, 11, 11))
        self.assertEqual(wf.stage_due_on('receipt_confirm', date(2026, 11, 6), {wf.SETTING_RECEIPT_DAYS: 0}),
                         date(2026, 11, 9), 'ноль вне границ — значение по умолчанию')
        self.assertIsNone(wf.stage_due_on('invoice_payment', date(2026, 11, 6)), 'у оплаты срок — свой, «Срок оплаты»')
        self.assertIsNone(wf.stage_due_on('closing_docs', None))

    def test_start_of_the_deadline(self):
        opened, received = datetime(2026, 11, 5, 9), datetime(2026, 11, 2, 17)
        self.assertEqual(wf.stage_deadline_start('receipt_confirm', opened_at=opened, received_at=received), date(2026, 11, 5))
        self.assertEqual(wf.stage_deadline_start('closing_docs', opened_at=opened, received_at=received), date(2026, 11, 2))
        self.assertEqual(wf.stage_deadline_start('closing_docs', opened_at=opened), date(2026, 11, 5),
                         'не знаем, когда получили, — с открытия этапа')
        self.assertIsNone(wf.stage_deadline_start('approval', opened_at=opened))

    def test_when_to_remind(self):
        today, yesterday = self.TODAY, date(2026, 11, 8)
        self.assertEqual(wf.deadline_mark(date(2026, 11, 8), today), 'overdue')
        self.assertEqual(wf.deadline_mark(date(2026, 11, 10), today, opened_on=yesterday), 'soon', 'за день до срока')
        self.assertEqual(wf.deadline_mark(today, today, opened_on=yesterday), 'soon', 'и в сам день срока')
        self.assertIsNone(wf.deadline_mark(date(2026, 11, 11), today, opened_on=yesterday), 'рано')
        self.assertIsNone(wf.deadline_mark(date(2026, 11, 10), today, opened_on=today),
                          'этап открылся сегодня — задача только что пришла, «срок завтра» — шум')
        self.assertEqual(wf.deadline_mark(date(2026, 11, 8), today, opened_on=today), 'overdue')
        self.assertIsNone(wf.deadline_mark(None, today))

    def test_deadline_of_the_open_stage_for_the_card(self):
        subtasks = [{'kind': 'receiving', 'state': 'done', 'done_at': datetime(2026, 11, 3, 12)},
                    {'kind': 'closing_docs', 'state': 'open', 'opened_at': datetime(2026, 11, 4, 9)}]
        self.assertEqual(wf.stage_deadline(subtasks, None, self.TODAY),
                         {'kind': 'closing_docs', 'due_on': date(2026, 11, 13), 'days_left': 4})
        receipt = [{'kind': 'receipt_confirm', 'state': 'open', 'opened_at': datetime(2026, 11, 5, 9)}]
        self.assertEqual(wf.stage_deadline(receipt, None, self.TODAY)['days_left'], -1)
        self.assertIsNone(wf.stage_deadline([{'kind': 'closing_docs', 'state': 'pending'}], None, self.TODAY))
        self.assertIsNone(wf.stage_deadline([], None, self.TODAY))


class StageReminderTextsTests(unittest.TestCase):
    def test_titles_say_what_and_when(self):
        row = request_row()
        soon = notify.stage_due_soon(row, 'receipt_confirm', date(2026, 11, 10), 1)
        self.assertEqual((soon['kind'], soon['title'], soon['tone']),
                         ('receipt_due_soon', 'Чек — срок завтра, 10.11.2026', 'default'))
        today = notify.stage_due_soon(row, 'closing_docs', date(2026, 11, 9), 0)
        self.assertEqual((today['kind'], today['title']), ('docs_due_soon', 'Закрывающие документы — срок сегодня, 09.11.2026'))
        late = notify.stage_overdue(row, 'closing_docs', date(2026, 11, 7), 2)
        self.assertEqual((late['kind'], late['title'], late['tone']),
                         ('docs_overdue', 'Закрывающие документы просрочены на 2 дн. — срок был 07.11.2026', 'warning'))
        self.assertEqual(notify.stage_overdue(row, 'receipt_confirm', date(2026, 11, 8), 0)['title'],
                         'Чек просрочен на 1 дн. — срок был 08.11.2026')
        self.assertEqual(soon['body'], notify.brief(row))
        self.assertNotIn('telegram', soon, 'напоминание — только колокол: Telegram — о новой задаче (п. 17)')


class StageRemindersRecipientsTests(WiderPerimeter):
    TODAY = date(2026, 11, 9)

    def remind(self, rows, watchers=None, members=None):
        pushes = []
        with mock.patch.object(flow, 'queries') as queries, mock.patch.object(flow, 'notices') as notices, \
                mock.patch.object(flow, 'directory') as directory:
            directory.get_settings.return_value = dict(wf.SETTINGS_DEFAULTS)
            queries.stage_deadline_rows.return_value = rows
            queries.observers_by_request.return_value = watchers or {}
            notices.push.side_effect = lambda cursor, **kwargs: pushes.append(kwargs) or len(kwargs['user_ids'])
            created = flow._remind_stage_deadlines(mock.MagicMock(), self.TODAY,
                                                   members or {'accounting': {ACCOUNTANT}})
        return created, pushes

    @staticmethod
    def stage(kind, opened_at, *, assignee=None, role=None):
        return {'kind': kind, 'opened_at': opened_at, 'assignee_id': assignee,
                'role_code': role or wf.subtask(kind)['role']}

    def test_receipt_goes_to_the_holder_initiator_and_observers(self):
        rows = [(request_row(id=1), self.stage('receipt_confirm', datetime(2026, 11, 7, 9), assignee=INITIATOR), None)]
        created, pushes = self.remind(rows, watchers={1: {WATCHER}})
        self.assertEqual(created, 2)
        self.assertEqual(pushes[0]['user_ids'], {INITIATOR, WATCHER})
        self.assertEqual(pushes[0]['kind'], 'receipt_due_soon')
        self.assertEqual(pushes[0]['dedupe_key'], 'receipt_due_soon:1:2026-11-10')

    def test_receipt_passed_to_someone_else_reaches_them_too(self):
        """Этап чека администратор передал другому — «тот, у кого этап» (решение владельца) — он,
        а инициатор слышит о сроке, как и о сроке оплаты."""
        rows = [(request_row(id=7), self.stage('receipt_confirm', datetime(2026, 11, 1), assignee=APPROVER), None)]
        _created, pushes = self.remind(rows)
        self.assertEqual(pushes[0]['user_ids'], {APPROVER, INITIATOR})
        self.assertEqual(pushes[0]['kind'], 'receipt_overdue')

    def test_closing_documents_go_to_accounting_initiator_and_observers_always(self):
        """Решение владельца дословно: «тот, у кого этап, инициатор и наблюдатели» — при любом
        статусе документов (инициатор не выпадает и после «Оригинал получен»)."""
        received = datetime(2026, 10, 28, 15)
        rows = [(request_row(id=number, closing_docs_status=status), self.stage('closing_docs', datetime(2026, 10, 29)), received)
                for number, status in ((2, 'none'), (3, 'scan'), (4, 'original'))]
        with mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS', tuple(access.SECTION_ALLOWED_USER_IDS) + (ACCOUNTANT,)):
            _created, pushes = self.remind(rows, watchers={4: {WATCHER}})
        self.assertEqual([(push['request_id'], push['user_ids']) for push in pushes],
                         [(2, {ACCOUNTANT, INITIATOR}), (3, {ACCOUNTANT, INITIATOR}), (4, {ACCOUNTANT, INITIATOR, WATCHER})])
        self.assertTrue(all(push['kind'] == 'docs_overdue' and push['tone'] == 'warning' for push in pushes))

    def test_nothing_due_or_nobody_to_tell(self):
        early = [(request_row(id=5), self.stage('receipt_confirm', datetime(2026, 11, 9, 8), assignee=INITIATOR), None)]
        self.assertEqual(self.remind(early), (0, []))
        self.assertEqual(self.remind([]), (0, []))
        closed_room = [(request_row(id=6, initiator_id=OUTSIDER),
                        self.stage('receipt_confirm', datetime(2026, 11, 1), assignee=OUTSIDER), None)]
        self.assertEqual(self.remind(closed_room), (0, []), 'раздел не открыт — напоминать некому')


class StageRemindersClearedByTheActionTests(unittest.TestCase):
    """«Отправить чек» и «Документы закрыты» гасят напоминания о сроке своего этапа: у них нет
    подзадачи (clear_subtask их не трогает), и иначе «просрочен» висел бы, когда дело сделано."""

    def act(self, kind, action, row):
        with mock.patch.object(flow, 'queries') as queries, mock.patch.object(flow, 'notices') as notices, \
                mock.patch.object(flow, 'advance', return_value=None), mock.patch.object(flow, '_docs_received'), \
                mock.patch.object(flow, '_set_docs'), mock.patch.object(flow, 'directory'):
            queries.read_request.return_value = row
            queries.read_subtask.return_value = open_subtask(kind)
            queries.list_attachments.return_value = [{'kind': 'receipt', 'subtask_kind': kind}]
            flow.act(mock.MagicMock(), REQUEST_ID, kind, action, {'id': INITIATOR, 'name': 'Т'}, fields={},
                     outbox=[], base_url=None)
        return [call.args[2] for call in notices.clear_kind.call_args_list]

    def test_receipt_provided(self):
        cleared = self.act('receipt_confirm', 'provide', request_row(stage='payment'))
        self.assertIn('receipt_due_soon', cleared)
        self.assertIn('receipt_overdue', cleared)

    def test_documents_closed(self):
        cleared = self.act('closing_docs', 'docs_close', request_row(stage='closing', closing_docs_status='original'))
        self.assertEqual({'docs_due_soon', 'docs_overdue'} - set(cleared), set())


# ─── Ручки ───────────────────────────────────────────────────────────────────

class _GapRoutes(_RoutesBase):
    def setUp(self):
        super().setUp()
        # Бухгалтерия, финансовый отдел и учёт имущества — тоже в периметре раздела.
        perimeter = mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS',
                                      tuple(access.SECTION_ALLOWED_USER_IDS) + (ACCOUNTANT, FINANCE, KEEPER))
        perimeter.start()
        self.addCleanup(perimeter.stop)
        # Хранилище — подменой, но исключение у неё настоящее: его ловит обёртка маршрута.
        self.files = mock.MagicMock()
        self.files.FileError = routes.files.FileError
        patcher = mock.patch.object(routes, 'files', self.files)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.roles = {}
        self.queries.roles_of_user.side_effect = lambda _c, uid: set(self.roles.get(uid, ()))
        self.queries.today_almaty.return_value = date(2026, 11, 9)


class DeleteRouteTests(_GapRoutes):
    def test_admin_moves_the_request_to_the_archive_and_files_stay(self):
        self.me = ADMIN
        with mock.patch.object(flow, 'delete') as delete:
            response = self.client.delete('/api/payments/requests/%s' % REQUEST_ID, json={'comment': ' дубль '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'success', 'deleted': True})
        delete.assert_called_once_with(mock.ANY, REQUEST_ID, {'id': ADMIN, 'name': 'Т'}, 'дубль')
        self.files.drop.assert_not_called()

    def test_without_a_body_and_by_someone_else(self):
        self.me = ADMIN
        with mock.patch.object(flow, 'delete') as delete:
            self.assertEqual(self.client.delete('/api/payments/requests/%s' % REQUEST_ID).status_code, 200)
        self.assertIsNone(delete.call_args.args[3])
        self.me = INITIATOR
        with mock.patch.object(flow, 'delete') as delete:
            self.assertEqual(self.client.delete('/api/payments/requests/%s' % REQUEST_ID).status_code, 403)
        delete.assert_not_called()

    def test_deleted_request_is_read_only_for_the_admin_and_hidden_from_others(self):
        self.queries.read_request.return_value = request_row(**DELETED)
        self.me = ADMIN
        body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['request']['deleted_comment'], 'дубль', 'администратор открывает заявку из архива')
        permissions = body['permissions']
        self.assertFalse(any(permissions[key] for key in ('can_edit', 'can_cancel', 'can_delete', 'can_attach',
                                                          'can_refund', 'can_manage_observers', 'can_reassign',
                                                          'can_act', 'can_set_vat')))
        self.me = INITIATOR
        self.assertEqual(self.client.get('/api/payments/requests/%s' % REQUEST_ID).status_code, 404)


class SupplierVatWithPaymentTests(_GapRoutes):
    """П. 8: НДС поставщика «не указан» бухгалтер выбирает в окне оплаты, и выбор уходит вместе
    с «Оплачено» — до нажатия его можно поменять (разбор 09.11.2026: мгновенная запись по
    одному касанию закрепляла бы ошибку навсегда)."""

    def setUp(self):
        super().setUp()
        self.row = request_row(stage='payment', counterparty_id=7, counterparty_name='Sulpak', counterparty_vat=None)
        self.queries.read_request.return_value = self.row
        self.queries.role_member_ids.return_value = {'approver': {APPROVER}, 'accounting': {ACCOUNTANT}}
        self.roles = {ACCOUNTANT: {'accounting'}}
        self.queries.list_subtasks.return_value = [open_subtask('invoice_payment')]
        self.queries.list_attachments.return_value = [{'kind': 'payment_order', 'subtask_kind': 'invoice_payment'}]
        self.queries.public_attachment.side_effect = lambda item: item
        load = mock.patch.object(flow, 'load_for_action', side_effect=lambda _c, _rid, kind: (self.row, open_subtask(kind)))
        load.start()
        self.addCleanup(load.stop)
        self.flow_act = mock.patch.object(flow, 'act', return_value={'next': 'receiving'}).start()
        self.addCleanup(mock.patch.stopall)
        self.url = '/api/payments/requests/%s/act' % REQUEST_ID
        self.me = ACCOUNTANT

    def pay(self, **extra):
        return self.client.post(self.url, json=dict(kind='invoice_payment', action='pay', paid_on='2026-11-09',
                                                     paid_amount=25000, **extra))

    def test_choice_goes_with_the_payment(self):
        self.assertEqual(self.pay(supplier_vat=False).status_code, 200)
        self.assertIs(self.flow_act.call_args.kwargs['fields']['supplier_vat'], False)

    def test_payment_without_the_choice_is_refused_at_the_field(self):
        response = self.pay()
        self.assertEqual(response.status_code, 400)
        self.assertTrue(any(item.startswith('Укажите НДС поставщика') for item in response.get_json()['missing']))
        self.flow_act.assert_not_called()
        self.assertEqual(self.pay(supplier_vat='').status_code, 400, 'пусто — не выбрано')

    def test_known_vat_needs_no_choice(self):
        self.row['counterparty_vat'] = True
        self.assertEqual(self.pay().status_code, 200)

    def test_card_offers_the_choice_only_to_the_payer(self):
        self.assertTrue(self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()['permissions']['can_set_vat'])
        self.row['counterparty_vat'] = True
        self.assertFalse(self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()['permissions']['can_set_vat'])
        self.row['counterparty_vat'] = None
        self.me, self.roles = APPROVER, {APPROVER: {'approver'}}
        self.queries.participates.return_value = True
        self.assertFalse(self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()['permissions']['can_set_vat'],
                         'утверждающий заявку видит, но НДС при оплате указывает бухгалтерия')

    def test_separate_instant_write_is_gone(self):
        self.assertEqual(self.client.post('/api/payments/requests/%s/supplier-vat' % REQUEST_ID,
                                          json={'vat_payer': True}).status_code, 404)


class SettleSupplierVatTests(unittest.TestCase):
    """flow._settle_supplier_vat — что «Оплачено» делает с выбором НДС."""

    def settle(self, written, settled=None, choice=False):
        with mock.patch.object(flow, 'directory') as directory, mock.patch.object(flow, 'queries') as queries:
            directory.set_counterparty_vat.return_value = written
            directory.read_counterparty.return_value = {'vat_payer': settled}
            flow._settle_supplier_vat(mock.MagicMock(), request_row(counterparty_id=7, counterparty_name='Sulpak'),
                                      {'id': ACCOUNTANT, 'name': 'Т'}, choice, 'invoice_payment')
        return directory, queries

    def test_choice_lands_in_the_supplier_card_and_the_history(self):
        directory, queries = self.settle(True)
        directory.set_counterparty_vat.assert_called_once_with(mock.ANY, 7, False)
        queries.log_event.assert_called_once_with(mock.ANY, REQUEST_ID, 'supplier_vat', {'id': ACCOUNTANT, 'name': 'Т'},
                                                  payload={'kind': 'invoice_payment', 'counterparty_id': 7,
                                                           'counterparty_name': 'Sulpak', 'vat_payer': False})

    def test_someone_else_set_the_same_vat_meanwhile(self):
        _directory, queries = self.settle(False, settled=False, choice=False)
        queries.log_event.assert_not_called()

    def test_someone_else_set_another_vat_meanwhile(self):
        with self.assertRaises(flow.FlowError) as raised:
            self.settle(False, settled=True, choice=False)
        self.assertEqual((raised.exception.code, raised.exception.status), ('PAYMENT_VAT_CHANGED', 409))


class DeletedRequestHiddenFromActionsTests(_GapRoutes):
    """Заявка из архива для не-администратора — как несуществующая и в ручках действий."""

    def setUp(self):
        super().setUp()
        self.queries.read_request.return_value = request_row(**DELETED)
        patcher = mock.patch.object(flow, 'load_for_action', side_effect=flow.FlowError(
            'Заявка удалена — действия по ней недоступны', code='PAYMENT_REQUEST_DELETED', status=409))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_outsider_gets_not_found_admin_gets_the_reason(self):
        body = {'kind': 'approval', 'action': 'approve'}
        self.me = APPROVER
        response = self.client.post('/api/payments/requests/%s/act' % REQUEST_ID, json=body)
        self.assertEqual((response.status_code, response.get_json()['code']), (404, 'PAYMENT_REQUEST_NOT_FOUND'))
        self.assertEqual(self.client.post('/api/payments/requests/%s/move' % REQUEST_ID,
                                          json={'kind': 'approval', 'status': 'review'}).status_code, 404)
        self.me = ADMIN
        response = self.client.post('/api/payments/requests/%s/act' % REQUEST_ID, json=body)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'PAYMENT_REQUEST_DELETED'))

    def test_files_and_assignee_of_an_archived_request_stay_as_they_were(self):
        self.me = ADMIN
        self.queries.read_attachment.return_value = {'id': 5, 'request_id': REQUEST_ID, 'kind': 'invoice',
                                                     'subtask_kind': None, 'uploaded_by': ADMIN, 'bucket': 'b',
                                                     'blob_path': 'p', 'file_name': 'счёт.pdf'}
        response = self.client.delete('/api/payments/requests/%s/attachments/5' % REQUEST_ID)
        self.assertEqual((response.status_code, response.get_json()['code']), (409, 'PAYMENT_REQUEST_DELETED'))
        self.queries.remove_attachment.assert_not_called()
        self.files.drop.assert_not_called()
        # Этап и человек — настоящие: отказ должен прийти от архива, а не от «сотрудник не найден».
        self.queries.read_subtask.return_value = open_subtask('approval', title='Утверждение', role_label='Утвердитель')
        self.queries.user_brief.return_value = {'id': APPROVER, 'name': 'Утвердова', 'fired': False}
        response = self.client.post('/api/payments/requests/%s/assignee' % REQUEST_ID,
                                    json={'kind': 'approval', 'user_id': APPROVER})
        self.assertEqual((response.status_code, response.get_json()['error']), (404, 'Заявка не найдена или закрыта'))
        self.queries.set_subtask_assignee.assert_not_called()
        # Та же передача у живой заявки проходит — тест различает именно архив.
        self.queries.read_request.return_value = request_row()
        with mock.patch.object(flow, 'announce_task'):
            self.assertEqual(self.client.post('/api/payments/requests/%s/assignee' % REQUEST_ID,
                                              json={'kind': 'approval', 'user_id': APPROVER}).status_code, 200)
        self.queries.set_subtask_assignee.assert_called_once()


class AssetEditAnswersWithHistoryTests(_GapRoutes):
    """Правка описания имущества отвечает и историей: строка «Смена компании-владельца» (п. 12)
    видна в окне сразу, без повторного открытия карточки."""

    def test_patch_returns_moves(self):
        from payments import routes_assets
        self.me, self.roles = KEEPER, {KEEPER: {'asset_keeper'}}
        current = {'id': 42, 'name': 'Ноутбук', 'category_id': 1, 'serial_number': 'SN', 'inventory_number': 'ИНВ-1',
                   'received_on': '2026-11-01', 'cost': 350000, 'legal_entity_id': 1, 'city': 'Алматы',
                   'department_id': 5, 'responsible_user_id': 7, 'location': 'Офис', 'status': 'in_use'}
        moves = [{'id': 9, 'kind': 'owner', 'from_legal_entity_name': 'ТОО «Первая»', 'to_legal_entity_name': 'ТОО «Вторая»'}]
        with mock.patch.object(routes_assets, 'assets_sql') as assets_sql:
            assets_sql.read_asset.return_value = current
            assets_sql.list_moves.return_value = moves
            response = self.client.patch('/api/payments/assets/42', json={'legal_entity_id': 2})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['moves'], moves)
        assets_sql.update_details.assert_called_once_with(mock.ANY, 42, {'legal_entity_id': 2},
                                                          actor={'id': KEEPER, 'name': 'Т'}, current=current)


class DictionaryVatFromAnOldTabTests(_GapRoutes):
    """Окно справочника, открытое до выкладки, «НДС не указан» не знает: у него «без НДС»
    выбрано и тогда, когда никто ничего не выбирал. Такое сохранение не затирает «не указан»
    у заведённого поставщика; новое окно шлёт метку `vat_tristate`, и его выбору верят."""

    def save(self, current_vat, **payload):
        from payments import routes_directory
        self.me = ADMIN
        with mock.patch.object(routes_directory, 'directory') as directory:
            directory.find_duplicate_name.return_value = None
            directory.read_counterparty.return_value = {'id': 7, 'vat_payer': current_vat}
            directory.upsert_counterparty.return_value = 7
            response = self.client.post('/api/payments/dictionaries/counterparties',
                                        json=dict({'name': 'Sulpak'}, **payload))
        self.assertEqual(response.status_code, 200, response.get_json())
        return directory.upsert_counterparty.call_args.kwargs['fields']['vat_payer']

    def test_old_tab_keeps_unknown_vat(self):
        self.assertIsNone(self.save(None, id=7, vat_payer=False))

    def test_new_tab_is_believed(self):
        self.assertIs(self.save(None, id=7, vat_payer=False, vat_tristate=True), False)
        self.assertIsNone(self.save(False, id=7, vat_payer=None, vat_tristate=True), 'новое окно умеет снять отметку')
        self.assertIs(self.save(None, id=7, vat_payer=True), True, '«с НДС» старое окно не выдумывает')

    def test_known_vat_and_new_records_as_before(self):
        self.assertIs(self.save(True, id=7, vat_payer=False), False)
        self.assertIs(self.save(None, vat_payer=False), False, 'новая запись — как раньше')


class ArchiveSliceOfTheRegistryTests(_GapRoutes):
    """Срез реестра «Удалённые» открыт только администратору раздела — и в выгрузке тоже."""

    def setUp(self):
        super().setUp()
        self.queries.list_requests.return_value = (0, [])
        self.queries.state_counters.return_value = {}

    def visible_for(self, who, roles=()):
        self.me, self.roles = who, {who: set(roles)}
        self.queries.list_requests.reset_mock()
        self.queries.state_counters.reset_mock()
        self.assertEqual(self.client.get('/api/payments/requests?state=deleted').status_code, 200)
        listed = self.queries.list_requests.call_args.kwargs['deleted_visible']
        counted = self.queries.state_counters.call_args.kwargs['deleted_visible']
        self.assertEqual(listed, counted)
        return listed

    def test_only_the_section_admin(self):
        self.assertIs(self.visible_for(ADMIN), True)
        self.assertIs(self.visible_for(INITIATOR), False)
        self.assertIs(self.visible_for(ACCOUNTANT, ['accounting']), False, 'полный реестр — ещё не архив')


class CardOfTheRequestTests(_GapRoutes):
    def test_finance_department_sees_the_full_number_and_the_view_is_logged(self):
        self.queries.read_request.return_value = request_row(has_card=True, card_mask='•••• 1111')
        self.queries.card_number.return_value = '4111111111111111'
        self.me, self.roles = FINANCE, {FINANCE: {'finance'}}
        with mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS', tuple(access.SECTION_ALLOWED_USER_IDS) + (FINANCE,)), \
                mock.patch.object(routes.access, 'sees_all_requests', return_value=True):
            body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['card_number'], '4111 1111 1111 1111')
        self.queries.log_card_shown.assert_called_once_with(mock.ANY, [REQUEST_ID], {'id': FINANCE, 'name': 'Т'})

    def test_admin_sees_it_as_on_the_finance_board(self):
        self.queries.read_request.return_value = request_row(has_card=True, card_mask='•••• 1111')
        self.queries.card_number.return_value = '4111111111111111'
        self.me = ADMIN
        body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['card_number'], '4111 1111 1111 1111')
        self.queries.log_card_shown.assert_called_once_with(mock.ANY, [REQUEST_ID], {'id': ADMIN, 'name': 'Т'})

    def test_initiator_gets_the_mask_and_nothing_is_logged(self):
        self.queries.read_request.return_value = request_row(has_card=True, card_mask='•••• 1111')
        self.me = INITIATOR
        body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertNotIn('card_number', body, 'инициатору — по кнопке, как раньше')
        self.assertTrue(body['permissions']['can_view_card'])
        self.queries.log_card_shown.assert_not_called()

    def test_supplier_history_and_stage_deadline_in_the_card(self):
        self.queries.read_request.return_value = request_row(counterparty_id=7)
        self.queries.today_almaty.return_value = date(2026, 11, 9)
        self.queries.list_subtasks.return_value = [
            {'kind': 'receipt_confirm', 'state': 'open', 'opened_at': datetime(2026, 11, 8, 9), 'role_code': 'initiator',
             'assignee_id': INITIATOR, 'status': 'new', 'id': 3, 'title': 'Чек', 'role_label': 'Инициатор',
             'stage': 'payment', 'position': 40}]
        body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['supplier_history'], [])
        self.queries.payment_history.assert_any_call(mock.ANY, counterparty_id=7, exclude_request_id=REQUEST_ID,
                                                     limit=routes.SUPPLIER_HISTORY_LIMIT,
                                                     visible_to=INITIATOR, visible_roles=set())
        self.assertEqual(body['stage_deadline']['kind'], 'receipt_confirm')
        self.assertEqual(body['stage_deadline']['due_on'], '2026-11-11')
        self.queries.read_request.return_value = request_row(counterparty_id=None)
        self.queries.list_subtasks.return_value = []
        body = self.client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
        self.assertEqual(body['supplier_history'], [])
        self.assertIsNone(body['stage_deadline'])


class AssetActDownloadTests(_GapRoutes):
    def setUp(self):
        super().setUp()
        self.queries.read_attachment.return_value = {'id': 31, 'request_id': REQUEST_ID, 'bucket': 'b',
                                                     'blob_path': 'p', 'file_name': 'акт.pdf'}
        self.queries.read_request.return_value = request_row(**DELETED)
        self.me = KEEPER

    def download(self):
        with mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS', tuple(access.SECTION_ALLOWED_USER_IDS) + (KEEPER,)):
            return self.client.get('/api/payments/attachments/31/download')

    def test_asset_keeper_downloads_the_act_of_an_archived_request(self):
        self.roles = {KEEPER: {'asset_keeper'}}
        routes.assets_sql.attachment_in_history.return_value = True
        # Хранилища в тесте нет: 503 значит, что граница видимости пройдена.
        self.assertEqual(self.download().status_code, 503)

    def test_other_files_of_the_request_stay_closed(self):
        self.roles = {KEEPER: {'asset_keeper'}}
        routes.assets_sql.attachment_in_history.return_value = False
        self.assertEqual(self.download().status_code, 404)
        self.roles = {}
        routes.assets_sql.attachment_in_history.return_value = True
        self.assertEqual(self.download().status_code, 404, 'имущество не видит — и акт не откроет')


class FinanceBoardTests(_GapRoutes):
    def board(self, code, who, roles):
        topup = {'id': 3, 'kind': 'card_topup', 'state': 'open', 'status': 'new', 'role_code': 'finance',
                 'assignee_id': None, 'position': 30, 'stage': 'payment', 'title': 'Пополнение карты',
                 'role_label': 'Финансовый отдел'}
        approval = dict(topup, id=4, kind='approval', role_code='approver', position=20, stage='approval',
                        title='Утверждение', role_label='Утвердитель')
        self.queries.board_requests.return_value = [
            (request_row(id=41, has_card=True, card_mask='•••• 1111', payment_method='card'), [topup, approval])]
        self.queries.card_numbers.return_value = {41: '4111 1111 1111 1111'}
        self.queries.is_manager_somewhere.return_value = False
        self.queries.avatar_refs.return_value = {}
        self.me, self.roles = who, {who: roles}
        with mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS', tuple(access.SECTION_ALLOWED_USER_IDS) + (who,)):
            response = self.client.get('/api/payments/boards/%s' % code)
        self.assertEqual(response.status_code, 200, response.get_json())
        return [card for column in response.get_json()['columns'] for card in column['cards']]

    def test_finance_card_carries_the_full_number(self):
        cards = self.board('finance', FINANCE, {'finance'})
        self.assertEqual([card['card_number'] for card in cards], ['4111 1111 1111 1111'])
        self.queries.card_numbers.assert_called_once_with(mock.ANY, [41])
        self.queries.log_card_shown.assert_called_once_with(mock.ANY, [41], {'id': FINANCE, 'name': 'Т'})

    def test_other_boards_keep_the_mask(self):
        """Полный номер — только на доске финотдела: тем, кому он положен (финотдел,
        администратор), другие доски его не показывают."""
        for code, who, roles in (('approval', APPROVER, {'approver'}), ('approval', FINANCE, {'finance', 'approver'}),
                                 ('accounting', ADMIN, set()), ('accounting', FINANCE, {'finance', 'accounting'})):
            with self.subTest(board=code, who=who):
                self.queries.card_numbers.reset_mock()
                self.queries.log_card_shown.reset_mock()
                cards = self.board(code, who, roles)
                self.assertTrue(all(card['card_number'] is None for card in cards))
                self.queries.card_numbers.assert_not_called()
                self.queries.log_card_shown.assert_not_called()

    def test_board_never_shows_the_archive_even_by_url(self):
        self.me = ADMIN
        self.queries.board_requests.return_value = []
        self.queries.is_manager_somewhere.return_value = False
        response = self.client.get('/api/payments/boards/approval?state=deleted')
        self.assertEqual(response.status_code, 200)
        self.assertIs(self.queries.board_requests.call_args.kwargs['deleted_visible'], False,
                      'срез «Удалённые» — только реестр администратора, доска — текущая работа')


if __name__ == '__main__':
    unittest.main()
