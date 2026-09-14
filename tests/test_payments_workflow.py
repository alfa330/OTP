# -*- coding: utf-8 -*-
"""Правила процесса «Согласование — Оплата счетов» (задача #179), которые ломаются молча.

Проверяются формулировки постановок, а не «работает ли функция»:

  * маршрут — 12 шагов с ответственными дословно по постановке Зарины Алиевой;
    шаг 2 без руководителя пропускается, а не зависает;
  * договор: свыше 300 000 ₸ — СТРОГО больше; отсутствующий, просроченный,
    расторгнутый и чужой договор равнозначны отсутствию (Колкомбаева, п. 1–4);
  * Приказ применяется только при выполнении ВСЕХ условий, и для каждого
    невыполненного есть причина словами (п. 12); при нескольких Приказах —
    конкретный проект старше «всех», новый старше старого (п. 10);
  * обязательные вложения на шагах 1, 7, 11 и условные на шаге 10.

База и Flask здесь не нужны: workflow.py — чистая логика.
"""

import unittest
from datetime import date
from decimal import Decimal

from payments import workflow, schema


class RouteTests(unittest.TestCase):
    def test_twelve_steps_with_roles_from_the_specification(self):
        roles = [step['role'] for step in workflow.STEPS]
        self.assertEqual(len(roles), 12)
        self.assertEqual(roles, [
            'initiator', 'manager', 'founder',                 # согласование закупки
            'initiator', 'accounting',                          # реквизиты
            'initiator', 'initiator', 'accounting', 'founder',  # счёт
            'accounting',                                       # оплата
            'initiator', 'accounting',                          # закрытие
        ])

    def test_required_files_are_on_steps_1_7_and_11(self):
        required = [step['no'] for step in workflow.STEPS if step.get('files_required')]
        self.assertEqual(required, [1, 7, 11])

    def test_route_assigns_people_and_skips_missing_manager(self):
        route = workflow.build_route(initiator={'id': 2, 'name': 'Инициатор'}, manager=None)
        by_no = {entry['step_no']: entry for entry in route}
        self.assertEqual(by_no[1]['assignee_id'], 2)
        self.assertEqual(by_no[2]['state'], 'skipped')
        self.assertIn('руководитель', by_no[2]['comment'])
        self.assertIsNone(by_no[3]['assignee_id'], 'шаг Учредителя — у роли, не у человека')
        self.assertEqual(workflow.next_open_step(route, 1), 3)

    def test_route_with_manager_and_delegate(self):
        route = workflow.build_route(initiator={'id': 2, 'name': 'И'}, manager={'id': 5, 'name': 'Р'},
                                     approver={'id': 9, 'name': 'Директор по развитию'})
        by_no = {entry['step_no']: entry for entry in route}
        self.assertEqual(by_no[2]['assignee_id'], 5)
        self.assertEqual(by_no[9]['assignee_id'], 9)
        self.assertIsNone(by_no[3]['assignee_id'], 'Приказ касается счёта (шаг 9), а не закупа (шаг 3)')
        self.assertIsNone(workflow.next_open_step(route, 12))

    def test_returns_lead_to_the_initiator_steps(self):
        self.assertEqual(workflow.step(2)['returns_to'], 1)
        self.assertEqual(workflow.step(3)['returns_to'], 1)
        self.assertEqual(workflow.step(8)['returns_to'], 7)
        self.assertEqual(workflow.step(9)['returns_to'], 7)
        for no in (1, 4, 5, 6, 7, 10, 11, 12):
            self.assertIsNone(workflow.step(no).get('returns_to'), 'шаг %s не возвращает' % no)


class ContractGateTests(unittest.TestCase):
    ACTIVE = {'id': 1, 'number': '25', 'status': 'active', 'counterparty_id': 7,
              'starts_on': date(2026, 1, 1), 'ends_on': date(2026, 12, 31)}

    def test_threshold_is_strictly_greater_than_300000(self):
        self.assertEqual(workflow.contract_gate(300000, None), (True, None))
        ok, reason = workflow.contract_gate(300001, None)
        self.assertFalse(ok)
        self.assertIn('300 000', reason)
        self.assertIn('договор', reason.lower())

    def test_active_contract_passes(self):
        self.assertEqual(workflow.contract_gate(500000, self.ACTIVE, counterparty_id=7,
                                                on_date=date(2026, 9, 1)), (True, None))

    def test_expired_contract_is_treated_as_missing(self):
        """Пример из ТЗ: договор до 31.08.2026, счёт от 01.09.2026 — остаётся у инициатора."""
        contract = dict(self.ACTIVE, ends_on=date(2026, 8, 31))
        ok, reason = workflow.contract_gate(500000, contract, counterparty_id=7, on_date=date(2026, 9, 1))
        self.assertFalse(ok)
        self.assertIn('просрочен', reason)
        self.assertIn('31.08.2026', reason)

    def test_non_active_statuses_are_treated_as_missing(self):
        for status in ('terminated', 'cancelled', 'archived', 'inactive'):
            ok, reason = workflow.contract_gate(400000, dict(self.ACTIVE, status=status), counterparty_id=7,
                                                on_date=date(2026, 9, 1))
            self.assertFalse(ok, status)
            self.assertIn('считается отсутствующим', reason)
        self.assertEqual(set(schema.CONTRACT_STATUSES), {'active', 'terminated', 'cancelled', 'archived', 'inactive'})

    def test_contract_with_another_counterparty_does_not_count(self):
        ok, reason = workflow.contract_gate(400000, self.ACTIVE, counterparty_id=8, on_date=date(2026, 9, 1))
        self.assertFalse(ok)
        self.assertIn('другим контрагентом', reason)

    def test_open_ended_contract_passes(self):
        contract = dict(self.ACTIVE, ends_on=None)
        self.assertEqual(workflow.contract_gate(10 ** 7, contract, counterparty_id=7,
                                                on_date=date(2030, 1, 1)), (True, None))


def order(**overrides):
    base = {
        'id': 15, 'number': '15', 'issued_on': date(2026, 8, 1), 'starts_on': date(2026, 8, 1), 'ends_on': None,
        'status': 'active', 'replaces_role': 'founder', 'delegate_user_id': 9, 'delegate_name': 'Директор по развитию',
        'amount_limit': Decimal('5000000'), 'all_projects': False, 'all_counterparties': False,
        'project_ids': [1], 'counterparty_ids': [7],
    }
    base.update(overrides)
    return base


class OrderTests(unittest.TestCase):
    ON = date(2026, 9, 1)

    def evaluate(self, o, **kw):
        params = dict(amount=3500000, project_id=1, counterparty_id=7, on_date=self.ON)
        params.update(kw)
        return workflow.evaluate_order(o, **params)

    def test_example_from_the_specification_applies(self):
        """Счёт №123: Проект А, Поставщик 1, 3 500 000 ₸, лимит 5 000 000 — Директор по развитию."""
        result = self.evaluate(order())
        self.assertTrue(result['applies'])
        self.assertTrue(all(check['ok'] for check in result['checks']))
        self.assertIsNone(result['reason'])

    def test_counterparty_outside_the_order_blocks_it(self):
        result = self.evaluate(order(), counterparty_id=3)
        self.assertFalse(result['applies'])
        self.assertIn('Приказ №15 не применён', result['reason'])
        self.assertIn('контрагент', result['reason'])

    def test_amount_over_limit_blocks_it(self):
        result = self.evaluate(order(), amount=6000000)
        self.assertFalse(result['applies'])
        self.assertIn('6 000 000', result['reason'])
        self.assertIn('5 000 000', result['reason'])

    def test_project_outside_the_order_blocks_it(self):
        result = self.evaluate(order(), project_id=2)
        self.assertFalse(result['applies'])
        self.assertIn('проект', result['reason'])

    def test_expired_or_cancelled_order_is_not_applied(self):
        self.assertFalse(self.evaluate(order(ends_on=date(2026, 8, 31)))['applies'])
        self.assertFalse(self.evaluate(order(status='cancelled'))['applies'])
        self.assertFalse(self.evaluate(order(starts_on=date(2026, 10, 1)))['applies'])

    def test_all_projects_and_all_counterparties_flags(self):
        result = self.evaluate(order(all_projects=True, all_counterparties=True, project_ids=[], counterparty_ids=[]),
                               project_id=99, counterparty_id=99)
        self.assertTrue(result['applies'])

    def test_no_limit_means_any_amount(self):
        self.assertTrue(self.evaluate(order(amount_limit=None), amount=10 ** 9)['applies'])

    def test_resolver_falls_back_to_the_founder_with_reasons(self):
        basis = workflow.resolve_invoice_approver([order()], amount=6000000, project_id=1, counterparty_id=7,
                                                  on_date=self.ON)
        self.assertIsNone(basis['approver_user_id'])
        self.assertEqual(basis['standard_label'], 'Учредитель')
        self.assertEqual(len(basis['evaluations']), 1)
        self.assertIn('превышает лимит', basis['evaluations'][0]['reason'])

    def test_resolver_picks_the_delegate_and_records_the_basis(self):
        basis = workflow.resolve_invoice_approver([order()], amount=3500000, project_id=1, counterparty_id=7,
                                                  on_date=self.ON)
        self.assertEqual(basis['approver_user_id'], 9)
        self.assertEqual(basis['order_number'], '15')
        self.assertFalse(basis['conflict'])

    def test_specific_project_beats_all_projects_and_newer_beats_older(self):
        broad = order(id=1, number='1', issued_on=date(2026, 7, 1), all_projects=True, project_ids=[],
                      delegate_user_id=11, delegate_name='Широкий')
        narrow = order(id=2, number='2', issued_on=date(2026, 6, 1), delegate_user_id=12, delegate_name='Узкий')
        basis = workflow.resolve_invoice_approver([broad, narrow], amount=100, project_id=1, counterparty_id=7,
                                                  on_date=self.ON)
        self.assertEqual(basis['approver_user_id'], 12, 'конкретный проект старше «всех проектов»')
        self.assertTrue(basis['conflict'], 'два применимых Приказа с разными людьми — предупреждение')

        newer = order(id=3, number='3', issued_on=date(2026, 8, 15), delegate_user_id=13)
        basis = workflow.resolve_invoice_approver([narrow, newer], amount=100, project_id=1, counterparty_id=7,
                                                  on_date=self.ON)
        self.assertEqual(basis['approver_user_id'], 13, 'новый Приказ старше старого при равной конкретности')


class RequirementsTests(unittest.TestCase):
    def test_step_1_needs_a_file(self):
        self.assertTrue(workflow.missing_requirements(1, {}, []))
        self.assertEqual(workflow.missing_requirements(1, {}, [{'kind': 'offer'}]), [])

    def test_step_7_needs_invoice_description_entity_and_amount(self):
        missing = workflow.missing_requirements(7, {'amount': 0}, [])
        self.assertEqual(len(missing), 5)
        ok = {'amount': 1000, 'invoice_description': 'что и куда', 'legal_entity_id': 1, 'counterparty_id': 2}
        self.assertEqual(workflow.missing_requirements(7, ok, [{'kind': 'invoice'}]), [])
        self.assertTrue(workflow.missing_requirements(7, ok, [{'kind': 'other'}]), 'файл не того вида — не счёт')

    def test_step_10_files_are_conditional(self):
        base = {'paid_on': date(2026, 9, 1), 'paid_amount': 100}
        self.assertEqual(workflow.missing_requirements(10, base, []), [])
        self.assertTrue(workflow.missing_requirements(10, dict(base, needs_payment_order=True), []))
        self.assertEqual(workflow.missing_requirements(
            10, dict(base, needs_payment_order=True, needs_power_of_attorney=True),
            [{'kind': 'payment_order'}, {'kind': 'power_of_attorney'}]), [])

    def test_step_11_needs_the_act(self):
        self.assertTrue(workflow.missing_requirements(11, {}, [{'kind': 'invoice'}]))
        self.assertEqual(workflow.missing_requirements(11, {}, [{'kind': 'act'}]), [])


class StateAndMoneyTests(unittest.TestCase):
    def test_request_state_precedence(self):
        today = date(2026, 9, 14)
        self.assertEqual(workflow.request_state({'status': 'done', 'due_on': date(2020, 1, 1)}, today), 'done')
        self.assertEqual(workflow.request_state({'status': 'active', 'block_code': 'contract_required',
                                                 'due_on': date(2020, 1, 1)}, today), 'blocked')
        self.assertEqual(workflow.request_state({'status': 'active', 'due_on': date(2026, 9, 13), 'current_step': 7}, today),
                         'overdue')
        self.assertEqual(workflow.request_state({'status': 'active', 'due_on': date(2026, 9, 13), 'current_step': 11}, today),
                         'active', 'оплаченная заявка не бывает просроченной')
        self.assertEqual(workflow.request_state({'status': 'active', 'due_on': date(2026, 9, 14), 'current_step': 7}, today),
                         'active', 'срок сегодня — ещё не просрочка')

    def test_money_parsing_and_formatting(self):
        self.assertEqual(workflow.to_decimal('1 234,50'), Decimal('1234.50'))
        self.assertEqual(workflow.to_decimal('300 000 ₸'), Decimal('300000'))
        self.assertEqual(workflow.to_decimal(None), Decimal('0'))
        self.assertEqual(workflow.fmt_money(Decimal('1234567.5')), '1 234 567,50 ₸')
        self.assertEqual(workflow.fmt_money(300000), '300 000 ₸')

    def test_items_total(self):
        items = [{'name': 'Бумага А4', 'quantity': 1, 'unit_price': '2 500'},
                 {'name': 'Карандаш', 'quantity': 3, 'unit_price': 150}]
        self.assertEqual(workflow.items_total(items), Decimal('2950.00'))


if __name__ == '__main__':
    unittest.main()
