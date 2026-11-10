# -*- coding: utf-8 -*-
"""«Без договора» в «Оплате счетов» (решение владельца 10.11.2026).

Отметка «Без договора» — осознанный выбор инициатора (или администратора в регулярном
платеже), а не пустое поле: со счётом любой суммы она пускает его в оплату, а бухгалтер
видит отметку и при нужде сам запрашивает договор. Пустое поле свыше 300 000 ₸ держит
счёт у инициатора, как раньше. Здесь — правило без базы и Flask; ход заявки на
настоящем SQL — в test_payments_no_contract_postgres.py.
"""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from payments import fixed, report, workflow as wf

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 10)
ACTIVE = {'number': '12', 'status': 'active', 'counterparty_id': 11, 'ends_on': date(2026, 12, 31)}


class ContractGateNoContractTests(unittest.TestCase):

    def test_no_contract_lets_any_amount_through(self):
        for amount in (Decimal('300000.01'), 900000, Decimal('25000000')):
            with self.subTest(amount=amount):
                self.assertEqual(wf.contract_gate(amount, None, counterparty_id=11, on_date=TODAY, no_contract=True),
                                 (True, None))

    def test_empty_field_over_threshold_still_stops_the_invoice_and_names_both_ways_out(self):
        ok, reason = wf.contract_gate(900000, None, counterparty_id=11, on_date=TODAY)
        self.assertFalse(ok)
        self.assertIn('Договор не указан', reason)
        self.assertIn('«Без договора»', reason, 'инициатор должен знать, что так тоже можно')
        self.assertIn('действующий договор', reason)

    def test_threshold_is_untouched(self):
        self.assertEqual(wf.contract_gate(Decimal('300000'), None), (True, None))
        self.assertEqual(wf.contract_gate(Decimal('300000'), None, no_contract=True), (True, None))
        self.assertFalse(wf.contract_gate(Decimal('300000.01'), None)[0])

    def test_chosen_contract_is_checked_even_with_the_flag(self):
        """Выбранный договор проверяется всегда: отметка не прячет просроченный или чужой договор."""
        for patch, word in (({'status': 'terminated'}, 'Расторгнут'), ({'ends_on': date(2026, 9, 1)}, 'просрочен'),
                            ({'counterparty_id': 12}, 'другим контрагентом'),
                            ({'starts_on': date(2026, 12, 1)}, 'вступает в силу')):
            with self.subTest(patch=patch):
                ok, reason = wf.contract_gate(900000, dict(ACTIVE, **patch), counterparty_id=11, on_date=TODAY,
                                              no_contract=True)
                self.assertFalse(ok)
                self.assertIn(word, reason)
                self.assertIn('«Без договора»', reason)
        self.assertEqual(wf.contract_gate(900000, ACTIVE, counterparty_id=11, on_date=TODAY), (True, None))

    def test_regular_payment_points_to_the_directory(self):
        """У регулярного платежа договор задаёт справочник — выбрать его в заявке нельзя."""
        ok, reason = wf.contract_gate(900000, None, on_date=TODAY, regular=True)
        self.assertFalse(ok)
        self.assertIn('«Регулярные платежи»', reason)
        self.assertIn('администратора раздела', reason)
        self.assertNotIn(wf.CONTRACT_FIX, reason)
        expired = dict(ACTIVE, ends_on=date(2026, 9, 1))
        self.assertIn('«Регулярные платежи»', wf.contract_gate(900000, expired, counterparty_id=11, on_date=TODAY,
                                                               regular=True)[1])


class AccountantAsksForContractTests(unittest.TestCase):
    """Пропущенный «Без договора» счёт бухгалтер может вернуть за договором сам."""

    def test_contract_required_is_a_reason_the_accountant_picks(self):
        codes = [item['code'] for item in wf.clarify_reasons_for(wf.KIND_INVOICE)]
        self.assertIn('contract_required', codes)
        self.assertEqual(wf.missing_for_action(wf.KIND_INVOICE, 'request_info', request={},
                                               fields={'reason': 'contract_required'}), [])
        self.assertEqual(wf.clarify_label('contract_required'), 'Нужен действующий договор')

    def test_ask_is_what_the_initiator_can_do(self):
        """Окно бухгалтера обещает «Инициатор увидит: …» — у регулярного платежа договор просят через
        администратора справочника: в самой заявке его не выбрать."""
        ask = {item['code']: item['ask'] for item in wf.clarify_reasons_for(wf.KIND_INVOICE)}
        self.assertIn('администратор раздела', ask['contract_required'])
        regular = {item['code']: item['ask'] for item in wf.clarify_reasons_for(wf.KIND_INVOICE, {'request_kind': 'regular'})}
        self.assertEqual(regular['contract_required'], wf.CONTRACT_ASK_REGULAR)
        self.assertEqual(regular['requisites'], ask['requisites'], 'остальные причины — как были')
        self.assertEqual(wf.clarify_ask('requisites'), wf.CLARIFY_BY_CODE['requisites']['ask'])
        self.assertEqual(wf.clarify_ask('rework'), '')
        # Список причин — копии: подстановка под заявку не портит общий словарь.
        self.assertEqual(wf.CLARIFY_BY_CODE['contract_required']['ask'], ask['contract_required'])

    def test_missing_text_names_what_to_choose(self):
        self.assertIn('«Без договора»', wf.contract_missing_text({'request_kind': 'purchase'}))
        self.assertIn('«Регулярные платежи»', wf.contract_missing_text({'request_kind': 'regular'}))

    def test_finance_does_not_get_it_and_invoice_missing_stays_system(self):
        self.assertNotIn('contract_required', [item['code'] for item in wf.clarify_reasons_for(wf.KIND_CARD)])
        self.assertTrue(wf.missing_for_action(wf.KIND_INVOICE, 'request_info', request={},
                                              fields={'reason': 'invoice_missing'}))


class RegularTemplateTests(unittest.TestCase):

    def template(self, **patch):
        base = {'id': 9, 'name': 'Аренда офиса', 'counterparty_id': 11, 'contract_id': None, 'no_contract': True,
                'legal_entity_id': 1, 'payment_method': 'invoice'}
        base.update(patch)
        return base

    def test_request_takes_the_choice_from_the_template(self):
        self.assertIs(fixed.request_fields(self.template())['no_contract'], True)
        self.assertIs(fixed.request_fields(self.template(no_contract=False))['no_contract'], False)
        self.assertIs(fixed.request_fields(self.template(no_contract=None))['no_contract'], False)

    def test_contract_beats_the_flag(self):
        fields = fixed.request_fields(self.template(contract_id=3))
        self.assertEqual((fields['contract_id'], fields['no_contract']), (3, False))


class ExportTests(unittest.TestCase):

    def test_contract_column_says_no_contract(self):
        self.assertEqual(wf.contract_text({'contract_number': '0025'}), '0025')
        self.assertEqual(wf.contract_text({'no_contract': True}), 'Без договора')
        self.assertEqual(wf.contract_text({'no_contract': False}), '')
        self.assertEqual(wf.contract_text({}), '')
        # Договор выбран (номер не подтянулся) — отметке рядом не место.
        self.assertEqual(wf.contract_text({'contract_id': 3, 'no_contract': True}), '')
        row = report.enrich({'id': 1, 'amount': 900000, 'no_contract': True, 'payment_method': 'invoice'}, [])
        self.assertEqual(row['contract_label'], 'Без договора')
        self.assertIn(('Договор', 'contract_label', 'text'), report.COLUMNS)


class WiringTests(unittest.TestCase):
    """Проводка по исходнику: флаг доходит от формы до правила и обратно."""

    def read(self, path):
        return (ROOT / path).read_text(encoding='utf-8')

    def test_flag_is_a_request_field_and_a_template_field(self):
        queries = self.read('payments/queries.py')
        self.assertIn("'contract_id', 'no_contract',", queries.split('_REQUEST_FIELDS = (')[1].split(')')[0])
        self.assertIn("'no_contract'", queries.split('EDITABLE_FIELDS = (')[1].split(')')[0])
        self.assertIn("values['no_contract'] = bool(values.get('no_contract')) and not values.get('contract_id')", queries)
        directory = self.read('payments/directory.py')
        for block in ('_TEMPLATE_FIELDS = (', '_TEMPLATE_WRITE = ('):
            self.assertIn("'no_contract'", directory.split(block)[1].split(')')[0])
        schema = self.read('payments/schema.py')
        for table in ('payment_requests', 'payment_fixed_templates'):
            # Под проверкой наличия: ALTER не берёт блокировку таблицы на каждом старте.
            self.assertIn("table_name = '%s'\n                          AND column_name = 'no_contract'" % table, schema)
            self.assertIn('ALTER TABLE %s ADD COLUMN no_contract BOOLEAN NOT NULL DEFAULT FALSE' % table, schema)

    def test_every_gate_call_passes_the_flag(self):
        for path in ('payments/flow.py', 'payments/routes.py'):
            source = self.read(path)
            calls = source.split('workflow.contract_gate(')[1:]
            self.assertTrue(calls, path)
            for call in calls:
                head = call.split('\n\n')[0]
                self.assertIn('no_contract=', head, path)
                self.assertIn('regular=', head, path)

    def test_supplier_change_drops_the_flag(self):
        routes = self.read('payments/routes.py')
        self.assertIn("fields.setdefault('no_contract', False)", routes)
        flow = self.read('payments/flow.py')
        self.assertIn('changes.update(contract_id=None, no_contract=False, counterparty_account_id=None)', flow)

    def test_helpdesk_may_choose_it(self):
        routes = self.read('payments/routes.py')
        keys = routes.split('HELPDESK_PAYMENT_KEYS = (')[1].split(')\n')[0]
        self.assertIn("'contract_id'", keys)
        self.assertIn("'no_contract'", keys)


if __name__ == '__main__':
    unittest.main()
