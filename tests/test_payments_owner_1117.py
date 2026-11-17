# -*- coding: utf-8 -*-
"""«Оплата счетов», правки владельца 17.11.2026 — без базы, чтобы ловил CI.

* пп. 1, 6, 7 — регулярный платёж «со счётом / без счёта», проверка бухгалтерией первым этапом
  (проверяющий из справочника), день оплаты;
* пп. 2–4 — договор у всех участников: срок и лимит с тонами «обратите внимание» (розовый) и
  тревога (красный);
* п. 5 — автозаполнение из документа: ответ модели проверяется, а не принимается на слово;
* п. 8 — проверка поставщика по БИН у договора (выписка КГД): НДС и режим ОУР/СНР;
* п. 9 — готовые проверки для руководителя;
* п. 10 — журнал раздела: «было → стало», секреты не пишутся, сбой журнала не валит дело.

Postgres-тесты тех же правок — test_payments_owner_1117_postgres.py (в CI пропускаются).
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from payments import access, audit, extract, fixed, routes, routes_directory, workflow as wf
from tests.test_payments_no_contract_routes import CONTRACT, REQUEST_ID, _Routes, purchase

TODAY = date(2026, 11, 17)
ACCOUNTANT, INITIATOR = 467, 455
assert ACCOUNTANT in access.SECTION_ALLOWED_USER_IDS and ACCOUNTANT not in access.SECTION_ADMIN_USER_IDS


def regular(**overrides):
    row = {'id': 1, 'request_kind': wf.KIND_REGULAR, 'fixed_template_id': 7, 'legacy_step': None,
           'payment_method': wf.METHOD_INVOICE, 'invoice_required': True, 'amount': Decimal('450000'),
           'legal_entity_id': 2, 'department_id': 5, 'expense_name': 'Аренда', 'counterparty_id': 13,
           'payment_period': 'декабрь 2026', 'object_type': wf.OBJECT_SERVICE}
    row.update(overrides)
    return row


# ─── пп. 2–4: срок и лимит договора ──────────────────────────────────────────

class ContractTermTests(unittest.TestCase):
    def term(self, **contract):
        return wf.contract_term(dict({'status': 'active'}, **contract), today=TODAY, settings={})

    def test_open_ended_contract_is_calm(self):
        self.assertEqual(self.term()['level'], wf.LEVEL_OK)
        self.assertEqual(self.term()['text'], 'Бессрочный')

    def test_far_end_is_calm(self):
        term = self.term(ends_on=date(2027, 3, 1))
        self.assertEqual((term['level'], term['text']), (wf.LEVEL_OK, 'Действует до 01.03.2027'))

    def test_within_warn_days_is_pink(self):
        term = self.term(ends_on=date(2026, 12, 10))
        self.assertEqual(term['level'], wf.LEVEL_ATTENTION)
        self.assertEqual(term['days_left'], 23)
        self.assertIn('осталось 23 дня', term['text'])

    def test_warn_days_come_from_settings(self):
        term = wf.contract_term({'status': 'active', 'ends_on': date(2026, 12, 10)}, today=TODAY,
                                settings={wf.SETTING_CONTRACT_WARN_DAYS: 10})
        self.assertEqual(term['level'], wf.LEVEL_OK)

    def test_expired_and_today_are_red(self):
        self.assertEqual(self.term(ends_on=date(2026, 11, 16))['level'], wf.LEVEL_ALARM)
        self.assertIn('Срок истёк 16.11.2026', self.term(ends_on=date(2026, 11, 16))['text'])
        self.assertEqual(self.term(ends_on=TODAY)['level'], wf.LEVEL_ALARM)
        self.assertIn('Истекает сегодня', self.term(ends_on=TODAY)['text'])

    def test_inactive_contract_is_red_whatever_the_date(self):
        term = wf.contract_term({'status': 'terminated', 'ends_on': date(2027, 12, 31)}, today=TODAY)
        self.assertEqual((term['level'], term['text']), (wf.LEVEL_ALARM, 'Договор расторгнут'))

    def test_not_started_yet_is_pink(self):
        self.assertEqual(self.term(starts_on=date(2026, 12, 1))['level'], wf.LEVEL_ATTENTION)


class ContractLimitTests(unittest.TestCase):
    CONTRACT = {'amount_limit': Decimal('1000000')}

    def test_no_limit_no_block(self):
        self.assertIsNone(wf.contract_limit({'amount_limit': None}, {}, amount=10))
        self.assertIsNone(wf.contract_limit({'amount_limit': 0}, {}, amount=10))

    def test_within_limit_is_calm(self):
        info = wf.contract_limit(self.CONTRACT, {'paid': 100000, 'in_work': 50000}, amount=150000)
        self.assertEqual(info['level'], wf.LEVEL_OK)
        self.assertEqual(info['after'], Decimal('700000'))
        self.assertIn('После этой оплаты останется', info['text'])

    def test_almost_used_is_pink(self):
        info = wf.contract_limit(self.CONTRACT, {'paid': 700000, 'in_work': 100000}, amount=150000)
        self.assertEqual((info['level'], info['percent']), (wf.LEVEL_ATTENTION, 95))

    def test_percent_comes_from_settings(self):
        info = wf.contract_limit(self.CONTRACT, {'paid': 700000}, amount=150000,
                                 settings={wf.SETTING_LIMIT_WARN_PERCENT: 90})
        self.assertEqual(info['level'], wf.LEVEL_OK)

    def test_over_limit_is_red_with_the_overshoot(self):
        info = wf.contract_limit(self.CONTRACT, {'paid': 900000}, amount=150000)
        self.assertEqual(info['level'], wf.LEVEL_ALARM)
        self.assertIn('выходит за лимит', info['text'])
        self.assertIn('50', info['text'])

    def test_paid_request_counts_once(self):
        info = wf.contract_limit(self.CONTRACT, {'paid': 100000}, amount=150000, this_paid=True)
        self.assertEqual(info['after'], Decimal('750000'))
        self.assertTrue(info['text'].startswith('Осталось'))

    def test_view_takes_the_worst_tone(self):
        view = wf.contract_view({'id': 1, 'number': '12', 'status': 'active', 'ends_on': date(2026, 12, 10),
                                 'amount_limit': 1000000}, {'paid': 900000}, amount=150000, today=TODAY)
        self.assertEqual((view['term']['level'], view['limit']['level'], view['level']),
                         (wf.LEVEL_ATTENTION, wf.LEVEL_ALARM, wf.LEVEL_ALARM))
        self.assertIsNone(wf.contract_view(None))


# ─── п. 9: готовые проверки ──────────────────────────────────────────────────

class AutoChecksTests(unittest.TestCase):
    def checks(self, request, **kwargs):
        return {item['code']: item for item in wf.auto_checks(request, **kwargs)}

    def test_order_is_the_owners_list(self):
        codes = [item['code'] for item in wf.auto_checks(
            {'amount': 1000}, contract_info=wf.contract_view({'number': '1', 'status': 'active', 'amount_limit': 5000},
                                                            {}, amount=1000, today=TODAY))]
        self.assertEqual(codes, ['contract', 'limit', 'tax', 'duplicate', 'price'])

    def test_limit_explains_the_overshoot(self):
        view = wf.contract_view({'number': '1', 'status': 'active', 'amount_limit': 7000},
                                {'paid': 60000, 'in_work': 3000}, amount=6000, today=TODAY)
        text = self.checks({'amount': 6000}, contract_info=view)['limit']['text']
        self.assertIn('оплачено', text)
        self.assertIn('в работе', text)

    def test_no_contract_above_threshold_is_red(self):
        check = self.checks({'amount': 400000, 'payment_method': wf.METHOD_INVOICE})['contract']
        self.assertEqual(check['level'], wf.LEVEL_ALARM)
        flagged = self.checks({'amount': 400000, 'payment_method': wf.METHOD_INVOICE, 'no_contract': True})['contract']
        self.assertEqual(flagged['level'], wf.LEVEL_ATTENTION)

    def test_duplicate_is_red_and_points_to_the_request(self):
        check = self.checks({'amount': 1}, duplicates=[{'id': 12}, {'id': 13}])['duplicate']
        self.assertEqual((check['level'], check['request_id']), (wf.LEVEL_ALARM, 12))
        self.assertIn('и ещё 1', check['text'])

    def test_price_rise_over_ten_percent_is_pink(self):
        similar = [{'match': 'item', 'item_name': 'Картридж HP 85A', 'unit_price': 12000, 'paid_on': date(2026, 10, 1),
                    'request_id': 5}]
        check = wf.price_check([{'name': 'картридж hp 85a', 'quantity': 2, 'unit_price': 13500}], similar)
        self.assertEqual((check['level'], check['request_id']), (wf.LEVEL_ATTENTION, 5))
        self.assertIn('+12,5%', check['text'])
        same = wf.price_check([{'name': 'Картридж HP 85A', 'quantity': 1, 'unit_price': 12000}], similar)
        self.assertEqual(same['level'], wf.LEVEL_OK)
        self.assertIn('(0%)', same['text'])

    def test_subcategory_only_match_is_not_a_price(self):
        check = wf.price_check([{'name': 'x', 'unit_price': 1}], [{'match': 'subcategory', 'unit_price': 5}])
        self.assertEqual(check['level'], wf.LEVEL_NONE)

    def test_supplier_tax_states(self):
        self.assertEqual(wf.supplier_tax_check({}, None)['level'], wf.LEVEL_NONE)
        unchecked = wf.supplier_tax_check({}, {'vat_payer': True})
        self.assertEqual((unchecked['level'], unchecked['text']), (wf.LEVEL_ATTENTION, 'С НДС — не проверен по БИН'))
        checked = {'vat_payer': False, 'tax_regime': 'snr', 'tax_regime_note': 'на основе упрощённой декларации',
                   'tax_checked_at': date(2026, 11, 17)}
        self.assertEqual(wf.supplier_tax_check({'supplier_vat': None}, checked)['level'], wf.LEVEL_OK)
        self.assertIn('СНР (на основе упрощённой декларации)', wf.supplier_tax_check({}, checked)['text'])
        clash = wf.supplier_tax_check({'supplier_vat': True}, checked)
        self.assertEqual(clash['level'], wf.LEVEL_ALARM)
        missed = wf.supplier_tax_check({'supplier_vat': False}, dict(checked, vat_payer=True))
        self.assertEqual(missed['level'], wf.LEVEL_ATTENTION)


# ─── пп. 1, 6, 7: регулярный платёж ──────────────────────────────────────────

class RegularCheckRouteTests(unittest.TestCase):
    PEOPLE = dict(initiator={'id': INITIATOR, 'name': 'Инициатор'}, manager={'id': 242, 'name': 'Руководитель'})

    def test_check_comes_first_with_the_checker(self):
        route = wf.build_route(regular(), checker={'id': ACCOUNTANT, 'name': 'Бухгалтер'}, **self.PEOPLE)
        self.assertEqual([item['kind'] for item in route[:3]], [wf.KIND_INITIATION, wf.KIND_REGULAR_CHECK, wf.KIND_MANAGER])
        check = route[1]
        self.assertEqual((check['assignee_id'], check['role_code'], check['stage']),
                         (ACCOUNTANT, wf.ROLE_ACCOUNTING, wf.STAGE_APPROVAL))

    def test_without_checker_any_accountant(self):
        route = wf.build_route(regular(), **self.PEOPLE)
        self.assertIsNone(next(item for item in route if item['kind'] == wf.KIND_REGULAR_CHECK)['assignee_id'])

    def test_purchase_and_legacy_have_no_check(self):
        self.assertNotIn(wf.KIND_REGULAR_CHECK, [item['kind'] for item in wf.build_route(purchase(), **self.PEOPLE)])
        legacy = regular(legacy_step=5, approved_at=date(2026, 1, 1))
        self.assertFalse(wf.needs_regular_check(legacy))

    def test_actions_and_board(self):
        self.assertEqual(wf.ACTIONS[wf.KIND_REGULAR_CHECK], ('approve', 'return'))
        self.assertIn(wf.KIND_REGULAR_CHECK, wf.BOARD_BY_CODE[wf.BOARD_ACCOUNTING]['kinds'])
        request = regular()
        self.assertEqual(wf.board_column(wf.BOARD_ACCOUNTING, request, [{'kind': wf.KIND_REGULAR_CHECK, 'state': 'open'}]),
                         'entity')
        self.assertEqual(wf.board_column(wf.BOARD_ACCOUNTING, request, [{'kind': wf.KIND_REGULAR_CHECK, 'state': 'waiting'}]),
                         'clarification')
        self.assertIsNone(wf.board_column(wf.BOARD_ACCOUNTING, request, [{'kind': wf.KIND_REGULAR_CHECK, 'state': 'done'}]))


class InvoiceRequiredTests(unittest.TestCase):
    def missing(self, request, **kwargs):
        return wf.missing_for_submit(request, items=[{'name': 'Аренда', 'quantity': 1, 'unit_price': 450000}], **kwargs)

    def test_without_invoice_nothing_about_the_invoice(self):
        missing = self.missing(regular(invoice_required=False))
        self.assertFalse([text for text in missing if 'счёт' in text.lower()], missing)
        self.assertTrue(wf.invoice_ready(regular(invoice_required=False), []))
        self.assertTrue(wf.invoice_not_needed(regular(invoice_required=False)))

    def test_with_invoice_it_is_required(self):
        missing = self.missing(regular())
        self.assertIn('Приложите счёт на оплату', missing)
        self.assertFalse(wf.invoice_ready(regular(), []))

    def test_flag_only_for_regular(self):
        self.assertFalse(wf.invoice_not_needed(purchase(invoice_required=False)))
        self.assertFalse(wf.invoice_not_needed(regular(invoice_required=None)))

    def test_template_copies_the_choice(self):
        base = {'id': 7, 'name': 'Аренда', 'counterparty_id': 13}
        self.assertIs(fixed.request_fields(dict(base, invoice_required=False))['invoice_required'], False)
        self.assertIs(fixed.request_fields(base)['invoice_required'], True)

    def test_settings_have_bounds(self):
        self.assertEqual(wf.setting({}, wf.SETTING_CONTRACT_WARN_DAYS), 30)
        self.assertEqual(wf.setting({wf.SETTING_LIMIT_WARN_PERCENT: 120}, wf.SETTING_LIMIT_WARN_PERCENT), 80)


# ─── п. 5, п. 8: разбор документа ────────────────────────────────────────────

class ExtractNormalizeTests(unittest.TestCase):
    def test_invoice_keeps_only_checked_values(self):
        fields = extract.normalize(extract.KIND_INVOICE, {
            'document_kind': 'invoice', 'invoice_number': ' СП-1117 ', 'invoice_date': '17.11.2026',
            'supplier_bin': '9900 0000 0105', 'supplier_iik': 'kz12 3456 7890 1234 5678', 'supplier_bik': 'testkzka',
            'supplier_kbe': '17', 'buyer_bin': '123', 'total_amount': '27 500,00', 'vat_included': 'нет',
            'items': [{'name': 'Интернет', 'quantity': 1, 'unit': 'мес', 'unit_price': 25000},
                      {'name': '', 'unit_price': 1}, {'name': 'Без цены'}],
        })
        self.assertEqual(fields['invoice_number'], 'СП-1117')
        self.assertEqual(fields['invoice_date'], '2026-11-17')
        self.assertEqual(fields['supplier_bin'], '990000000105')
        self.assertEqual(fields['supplier_iik'], 'KZ123456789012345678')
        self.assertEqual(fields['supplier_bik'], 'TESTKZKA')
        self.assertNotIn('buyer_bin', fields, 'БИН не из 12 цифр не берётся')
        self.assertEqual(fields['total_amount'], '27500.00')
        self.assertIs(fields['vat_included'], False)
        self.assertEqual([item['name'] for item in fields['items']], ['Интернет'])
        self.assertIs(fields['_other_document'], False)

    def test_junk_is_dropped(self):
        fields = extract.normalize(extract.KIND_INVOICE, {'invoice_date': '31.02.2026', 'total_amount': '-5',
                                                          'supplier_iik': 'KZ12', 'document_kind': 'other'})
        self.assertEqual(set(fields), {'_other_document'})
        self.assertIs(fields['_other_document'], True)
        self.assertEqual(extract.normalize(extract.KIND_INVOICE, 'не объект'), {'_other_document': False})

    def test_contract_dates_and_periodicity(self):
        fields = extract.normalize(extract.KIND_CONTRACT, {'number': '№ А-02', 'starts_on': '2026-11-01',
                                                           'ends_on': '2026-10-31', 'periodicity': 'ежемесячно',
                                                           'amount_limit': 5400000})
        self.assertNotIn('ends_on', fields, 'окончание раньше начала — не берётся')
        self.assertEqual((fields['periodicity'], fields['amount_limit']), ('monthly', '5400000.00'))

    def test_kgd_regimes(self):
        self.assertEqual(extract.regime_of('Общеустановленный режим'), ('our', None))
        self.assertEqual(extract.regime_of('Специальный налоговый режим на основе упрощённой декларации'),
                         ('snr', 'на основе упрощённой декларации'))
        self.assertEqual(extract.regime_of('СНР для самозанятых'), ('snr', 'для самозанятых'))
        self.assertEqual(extract.regime_of('что-то другое'), (None, None))
        fields = extract.normalize(extract.KIND_KGD, {'bin': '990000000106', 'tax_regime': 'Розничный налог',
                                                      'vat_removed_on': '2026-03-01'})
        self.assertEqual((fields['tax_regime'], fields['vat_payer']), ('snr', False))


class ExtractReadTests(unittest.TestCase):
    def setUp(self):
        import ai_feedback.service as ai
        patches = [mock.patch.object(ai, 'GEMINI_API_KEY', 'test'),
                   mock.patch.object(ai, 'gemini_endpoint', lambda model: ('https://x/%s' % model, {})),
                   mock.patch.object(extract, '_model_chain', lambda: ['m1', 'm2'])]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_falls_through_the_chain(self):
        calls = []

        def post(url, headers, payload):
            calls.append(url)
            if url.endswith('m1'):
                return 429, {}
            return 200, {'candidates': [{'content': {'parts': [{'text': '{"invoice_number": "1"}'}]}}]}

        raw, model = extract.read_document(extract.KIND_INVOICE, b'%PDF', filename='a.pdf', post=post)
        self.assertEqual((raw, model, len(calls)), ({'invoice_number': '1'}, 'm2', 2))

    def test_errors_in_words(self):
        with self.assertRaises(extract.ExtractError) as big:
            extract.read_document(extract.KIND_INVOICE, b'x' * (extract.MAX_BYTES + 1), filename='a.pdf')
        self.assertEqual(big.exception.status, 413)
        with self.assertRaises(extract.ExtractError) as kind:
            extract.read_document(extract.KIND_INVOICE, b'x', filename='a.docx')
        self.assertEqual(kind.exception.status, 415)
        with self.assertRaises(extract.ExtractError) as failed:
            extract.read_document(extract.KIND_INVOICE, b'x', filename='a.png', post=lambda *a: (200, {'candidates': []}))
        self.assertEqual(failed.exception.status, 502)
        with self.assertRaises(extract.ExtractError):
            extract.read_document('passport', b'x', filename='a.pdf')


# ─── п. 10: журнал ───────────────────────────────────────────────────────────

class AuditDiffTests(unittest.TestCase):
    def test_only_changed_fields_without_noise(self):
        changes = audit.diff(
            {'id': 1, 'name': 'Старое', 'amount': Decimal('10.00'), 'note': None, 'updated_at': '2026-01-01',
             'file_blob': 'p/1'},
            {'id': 1, 'name': 'Новое', 'amount': Decimal('10'), 'note': '', 'updated_at': '2026-11-17', 'file_blob': 'p/2'})
        self.assertEqual(changes, {'name': ['Старое', 'Новое']})

    def test_secrets_are_words_only(self):
        changes = audit.diff({'card_number': '0000 0000 0000 0001', 'iin': '000000000001'},
                             {'card_number': '0000 0000 0000 0002', 'iin': '000000000002'})
        self.assertEqual(changes, {'card_number': [None, audit.SECRET_MARK], 'iin': [None, audit.SECRET_MARK]})
        self.assertNotIn('card_number_enc', audit.created({'card_number_enc': 'gAAAA', 'card_last4': '1234'}))

    def test_values_are_plain(self):
        self.assertEqual(audit.plain({'d': date(2026, 11, 17), 'm': Decimal('1.50'), 'w': Decimal('2.00')}),
                         {'d': '2026-11-17', 'm': '1.5', 'w': '2'})

    def test_record_change_actions(self):
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (1,)
        self.assertIsNone(audit.record_change(cursor, {'id': 1}, 'contract', 5, {'number': 'A'}, {'number': 'A'}))
        audit.record_change(cursor, {'id': 1, 'name': 'Админ'}, 'contract', 5, None, {'number': 'A'})
        insert = [call for call in cursor.execute.call_args_list if 'INSERT INTO payment_audit_log' in str(call)]
        self.assertEqual(insert[-1].args[1][2:4], ('create', 'contract'))

    def test_failure_does_not_break_the_action(self):
        cursor = mock.MagicMock()

        def execute(sql, params=None):
            if sql.startswith('INSERT INTO payment_audit_log'):
                raise RuntimeError('нет таблицы')

        cursor.execute.side_effect = execute
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(audit.record(cursor, {'id': 1}, 'update', 'settings', changes={'a': [1, 2]}))
        self.assertIn(mock.call('ROLLBACK TO SAVEPOINT payments_audit'), cursor.execute.call_args_list)


# ─── Ручки ───────────────────────────────────────────────────────────────────

class _AuditRoutes(_Routes):
    def client(self, row):
        client = super().client(row)
        self.audit = mock.MagicMock()
        self.audit.ACTION_LABELS, self.audit.ENTITY_LABELS = audit.ACTION_LABELS, audit.ENTITY_LABELS
        self.audit.ENTITY_TABLES, self.audit.DICTIONARY_ENTITY = audit.ENTITY_TABLES, audit.DICTIONARY_ENTITY
        self.audit.PAGE_SIZE = audit.PAGE_SIZE
        self.audit.journal.return_value = ([], 0)
        self.audit.snapshot.return_value = None
        for patcher in (mock.patch.object(routes, 'audit', self.audit), mock.patch.object(routes_directory, 'audit', self.audit)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def as_user(self, user_id, roles=()):
        self.queries.load_access_context.return_value = {'user_id': user_id, 'name': 'Человек %s' % user_id, 'role': 'operator'}
        self.queries.roles_of_user.return_value = set(roles)


class CardRouteTests(_AuditRoutes):
    def test_card_brings_contract_and_checks_and_writes_a_view(self):
        client = self.client(purchase(contract_id=CONTRACT['id'], amount=Decimal('6000')))
        self.directory.read_contract.side_effect = lambda cursor, cid: dict(CONTRACT, amount_limit=Decimal('7000'))
        self.queries.contract_usage.return_value = {CONTRACT['id']: {'paid': Decimal('60000'), 'in_work': Decimal('0')}}
        response = client.get('/api/payments/requests/%s' % REQUEST_ID)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['contract_view']['limit']['level'], wf.LEVEL_ALARM)
        self.assertEqual([item['code'] for item in body['checks']], ['contract', 'limit', 'tax', 'duplicate', 'price'])
        self.queries.contract_usage.assert_called_once_with(mock.ANY, [CONTRACT['id']], exclude_request_id=REQUEST_ID)
        self.audit.record_view.assert_called_once()
        self.assertEqual(self.audit.record_view.call_args.args[2], REQUEST_ID)


class ExtractRouteTests(_AuditRoutes):
    def test_extract_fills_and_writes_the_journal(self):
        client = self.client(purchase())
        with mock.patch.object(routes.extract, 'read_document', return_value=({'invoice_number': ' 77 '}, 'm1')), \
                mock.patch.object(routes.extract, 'match', return_value={'counterparty_id': 13}):
            response = client.post('/api/payments/extract', data={'kind': 'invoice', 'file': (__import__('io').BytesIO(b'%PDF'), 'с.pdf')},
                                   content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual((body['fields']['invoice_number'], body['matches']['counterparty_id']), ('77', 13))
        self.assertNotIn('_other_document', body['fields'])
        args = self.audit.record.call_args
        self.assertEqual(args.args[2:4], ('extract', 'document'))

    def test_extract_error_is_a_message(self):
        client = self.client(purchase())
        with mock.patch.object(routes.extract, 'read_document',
                               side_effect=extract.ExtractError('ИИ не настроен', status=503, code='PAYMENT_EXTRACT_OFF')):
            response = client.post('/api/payments/extract', data={'kind': 'invoice', 'file': (__import__('io').BytesIO(b'x'), 'с.pdf')},
                                   content_type='multipart/form-data')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json().get('code'), 'PAYMENT_EXTRACT_OFF')


class JournalRouteTests(_AuditRoutes):
    def test_admin_only(self):
        client = self.client(purchase())
        self.as_user(ACCOUNTANT, roles={'accounting'})
        self.assertEqual(client.get('/api/payments/journal').status_code, 403)

    def test_filters_reach_the_query(self):
        client = self.client(purchase())
        response = client.get('/api/payments/journal?entity=contract&from=2026-11-01&request_id=5&page=2')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        kwargs = self.audit.journal.call_args.kwargs
        self.assertEqual((kwargs['entity'], kwargs['date_from'], kwargs['request_id'], kwargs['page']),
                         ('contract', date(2026, 11, 1), 5, 2))
        self.assertEqual(client.get('/api/payments/journal?entity=passport').status_code, 400)

    def test_history_of_a_record_for_its_keeper(self):
        client = self.client(purchase())
        self.audit.history.return_value = [{'id': 1}]
        self.assertEqual(client.get('/api/payments/journal/contract/6').get_json()['items'], [{'id': 1}])
        self.assertEqual(client.get('/api/payments/journal/passport/6').status_code, 404)


class DirectoryRouteTests(_AuditRoutes):
    def template_payload(self, **overrides):
        payload = {'name': 'Аренда', 'amount': 450000, 'periodicity': 'monthly', 'next_due_on': '2026-12-05',
                   'responsible_user_id': INITIATOR, 'object_type': 'service'}
        payload.update(overrides)
        return payload

    def test_checker_must_be_an_accountant(self):
        client = self.client(purchase())
        self.queries.user_brief.side_effect = lambda cursor, uid: {'id': uid, 'name': 'Сотрудник', 'fired': False}
        self.queries.role_member_ids.return_value = {'accounting': {ACCOUNTANT}}
        self.directory.read_template.return_value = None
        response = client.post('/api/payments/templates', json=self.template_payload(checker_user_id=INITIATOR))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'PAYMENTS_CHECKER_NOT_ACCOUNTING')
        self.directory.upsert_template.return_value = 9
        response = client.post('/api/payments/templates',
                               json=self.template_payload(checker_user_id=ACCOUNTANT, invoice_required=False))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fields = self.directory.upsert_template.call_args.kwargs['fields']
        self.assertEqual((fields['checker_user_id'], fields['invoice_required']), (ACCOUNTANT, False))

    def test_old_form_keeps_the_choices(self):
        client = self.client(purchase())
        self.queries.user_brief.side_effect = lambda cursor, uid: {'id': uid, 'name': 'Сотрудник', 'fired': False}
        self.directory.read_template.return_value = {'id': 9, 'invoice_required': False, 'checker_user_id': ACCOUNTANT}
        self.directory.upsert_template.return_value = 9
        response = client.post('/api/payments/templates', json=self.template_payload(id=9))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fields = self.directory.upsert_template.call_args.kwargs['fields']
        self.assertEqual((fields['checker_user_id'], fields['invoice_required']), (ACCOUNTANT, False))

    def contract_payload(self, **overrides):
        payload = {'counterparty_id': 13, 'number': 'А-02', 'status': 'active'}
        payload.update(overrides)
        return payload

    def test_new_contract_needs_the_bin_check(self):
        client = self.client(purchase())
        self.directory.find_duplicate_name.return_value = None
        response = client.post('/api/payments/dictionaries/contracts', json=self.contract_payload())
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body.get('code'), 'PAYMENTS_TAX_CHECK_MISSING')
        self.directory.upsert_contract.assert_not_called()
        self.directory.upsert_contract.return_value = 21
        response = client.post('/api/payments/dictionaries/contracts',
                               json=self.contract_payload(tax_vat=False, tax_regime='snr',
                                                          tax_regime_note='на основе упрощённой декларации', tax_source='kgd'))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        kwargs = self.directory.set_contract_tax.call_args.kwargs
        self.assertEqual((kwargs['vat'], kwargs['regime'], kwargs['source']), (False, 'snr', 'kgd'))

    def test_old_contract_saves_without_the_check(self):
        client = self.client(purchase())
        self.directory.find_duplicate_name.return_value = None
        self.directory.read_contract.side_effect = lambda cursor, cid: {'id': cid, 'tax_vat': None, 'tax_regime': None}
        self.directory.upsert_contract.return_value = 6
        response = client.post('/api/payments/dictionaries/contracts', json=self.contract_payload(id=6))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.directory.set_contract_tax.assert_not_called()

    def test_saved_record_goes_to_the_journal(self):
        client = self.client(purchase())
        self.directory.find_duplicate_name.return_value = None
        self.directory.read_contract.side_effect = lambda cursor, cid: {'id': cid, 'tax_vat': True, 'tax_regime': 'our',
                                                                       'tax_source': 'manual', 'tax_checked_at': date(2026, 11, 1)}
        self.directory.upsert_contract.return_value = 6
        self.audit.snapshot.side_effect = [{'number': 'A'}, {'number': 'B'}]
        client.post('/api/payments/dictionaries/contracts', json=self.contract_payload(id=6))
        args = self.audit.record_change.call_args.args
        self.assertEqual(args[2:6], ('contract', 6, {'number': 'A'}, {'number': 'B'}))


if __name__ == '__main__':
    unittest.main()
