# -*- coding: utf-8 -*-
"""Правила процесса «Закуп и оплата» (ТЗ #381), которые ломаются молча.

Проверяются формулировки ТЗ, а не «работает ли функция»:

  * одна заявка идёт по этапам п. 2, а её подзадачи зависят от способа оплаты и
    категории учёта: счёт — бухгалтерии, карта — финансовому отделу, чек — только
    после карты сотрудника, постановка на учёт — только у имущества;
  * маршрут согласования определяет система (п. 6): регулярный платёж →
    поставщик → лимит согласования → маршрут → любой из утверждающих;
  * заявку с неполными данными не отправить (п. 4), действие без обязательных
    данных не выполнить (пп. 7, 8, 5.3, 10.2, 11);
  * заявка закрывается только при выполнении условий п. 15;
  * колонки досок — дословно «рекомендуемые» из пп. 7–9.

База и Flask здесь не нужны: workflow.py — чистая логика.
"""

import re
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from payments import fixed, flow, schema, sqlutil, workflow as wf

ROOT = Path(__file__).resolve().parents[1]
META_PATH = ROOT / 'src' / 'components' / 'payments' / 'paymentsMeta.js'
TODAY = date(2026, 10, 8)
INITIATOR = {'id': 7, 'name': 'Инициатор'}
MANAGER = {'id': 8, 'name': 'Руководитель'}


def kinds(route):
    return [entry['kind'] for entry in route]


def purchase(**overrides):
    """Полностью заполненная заявка «Новый закуп» с оплатой по счёту."""
    request = {
        'request_kind': 'purchase', 'legal_entity_id': 1, 'department_id': 5, 'expense_name': 'Бумага А4',
        'amount': Decimal('106000'), 'category_id': 3, 'justification': 'Закончилась бумага',
        'due_on': date(2026, 10, 20), 'object_type': 'service', 'accounting_category': None,
        'payment_method': 'invoice', 'counterparty_id': 11, 'counterparty_name': 'ТОО «Канцлер»',
        'no_alternatives': False, 'supplier_choice_reason': 'Дешевле остальных',
    }
    request.update(overrides)
    return request


ITEMS = [{'name': 'Бумага А4', 'quantity': 10, 'unit': 'уп', 'unit_price': 10600}]
OFFERS = [
    {'supplier_name': 'ТОО «Канцлер»', 'amount': 106000, 'is_recommended': True},
    {'supplier_name': 'ИП Бумага', 'amount': 110000, 'is_recommended': False},
    {'supplier_name': 'ТОО «Офис»', 'amount': 120000, 'is_recommended': False},
]


class SpecificationListsTests(unittest.TestCase):
    """Перечни ТЗ лежат в коде дословно: правка названия колонки или этапа — это правка ТЗ."""

    def test_lifecycle_is_the_one_from_clause_2(self):
        self.assertEqual([label for _code, label in wf.STAGES], [
            'Инициация закупа', 'Согласование', 'Оплата', 'Получение', 'Учёт имущества',
            'Закрывающие документы', 'Закрытие'])

    def test_subtasks_named_in_the_spec_keep_their_names(self):
        self.assertEqual(wf.subtask_title('invoice_payment'), 'Оплата счёта')                                # п. 5.1
        self.assertEqual(wf.subtask_title('receipt_confirm'), 'Предоставить чек/подтверждение расхода')      # п. 5.3
        self.assertEqual(wf.subtask_title('asset_registration'), 'Постановка имущества на учёт')             # п. 10.2

    def test_roles_are_the_six_from_clause_3(self):
        self.assertEqual(wf.ROLE_LABELS, {
            'initiator': 'Инициатор', 'manager': 'Непосредственный руководитель', 'approver': 'Утвердитель',
            'accounting': 'Бухгалтерия', 'finance': 'Финансовый отдел',
            'asset_keeper': 'Ответственный за учёт имущества'})
        # Список участников ведут четырём ролям; инициатор и руководитель — люди заявки.
        self.assertEqual(schema.MEMBER_ROLES, ('approver', 'accounting', 'finance', 'asset_keeper'))

    def test_only_two_payment_methods_exist(self):
        self.assertEqual(set(wf.PAYMENT_METHOD_LABELS), {'invoice', 'card'})
        self.assertEqual(set(wf.CARD_RECIPIENT_LABELS), {'employee', 'supplier'})

    def test_board_columns_are_the_recommended_ones(self):
        columns = {board['code']: [label for _key, label in board['columns']] for board in wf.BOARDS}
        self.assertEqual(columns['approval'], [
            'Новые', 'На рассмотрении', 'Требуется уточнение', 'Согласовано', 'Отклонено'])
        self.assertEqual(columns['accounting'], [
            'Новые счета', 'Проверка', 'Требуется уточнение', 'Готово к оплате', 'На оплате', 'Оплачено',
            'Ожидаются закрывающие документы', 'Закрыто'])
        self.assertEqual(columns['finance'], [
            'Новые', 'В работе', 'Требуется уточнение', 'Готово к пополнению', 'Пополнено', 'Ожидается чек',
            'Закрыто'])
        self.assertEqual([board['title'] for board in wf.BOARDS], [
            'Согласование закупа', 'Бухгалтерия / Оплата счетов', 'Финансовый отдел / Пополнение карт'])

    def test_every_column_says_what_lands_in_it(self):
        """Пустая колонка показывает подпись — что в неё попадает (как у колонок
        «Задач»). Подпись есть у каждой колонки и не повторяет её название."""
        from payments import queries
        for board in wf.BOARDS:
            self.assertEqual(set(wf.COLUMN_CAPTIONS[board['code']]), {key for key, _label in board['columns']}, board['code'])
            for key, label in board['columns']:
                caption = wf.column_caption(board['code'], key)
                self.assertTrue(caption, (board['code'], key))
                self.assertNotEqual(caption.lower(), label.lower(), (board['code'], key))
        # Итоговые колонки держат закрытые заявки ровно столько дней, сколько обещает подпись.
        self.assertEqual(wf.column_caption('accounting', 'closed'), 'За последние %d дней' % queries.BOARD_CLOSED_DAYS)
        self.assertEqual(wf.column_caption('nope', 'new'), '')

    def test_closing_document_statuses_and_no_alternatives_reasons(self):
        self.assertEqual([label for _code, label in wf.CLOSING_DOC_STATUSES], [
            'Документы не получены', 'Скан получен', 'Оригинал получен', 'Документы закрыты'])
        reasons = [label for _code, label in wf.NO_ALTERNATIVES_REASONS]
        for expected in ('Единственный поставщик', 'Продление существующей лицензии',
                         'Техническая совместимость', 'Конкретный подрядчик', 'Закуп по действующему договору'):
            self.assertIn(expected, reasons)

    def test_database_lists_match_the_rules(self):
        """CHECK-списки схемы и словари правил — одни и те же коды: иначе значение,
        которое пропустили правила, отвергла бы база (и наоборот)."""
        self.assertEqual(set(schema.REQUEST_KINDS), set(wf.REQUEST_KIND_LABELS))
        self.assertEqual(set(schema.PAYMENT_METHODS), set(wf.PAYMENT_METHOD_LABELS))
        self.assertEqual(set(schema.CARD_RECIPIENTS), set(wf.CARD_RECIPIENT_LABELS))
        self.assertEqual(set(schema.OBJECT_TYPES), set(wf.OBJECT_TYPE_LABELS))
        self.assertEqual(set(schema.ACCOUNTING_CATEGORIES), set(wf.ACCOUNTING_CATEGORY_LABELS))
        self.assertEqual(set(schema.CLOSING_DOC_STATUSES), set(wf.CLOSING_DOC_LABELS))
        self.assertEqual(set(schema.ASSET_STATUSES), set(wf.ASSET_STATUS_LABELS))
        self.assertTrue(set(wf.ATTACHMENT_LABELS) >= set(schema.ATTACHMENT_KINDS))

    def test_every_subtask_has_actions_and_a_known_board(self):
        for item in wf.SUBTASKS:
            self.assertIn(item['kind'], wf.ACTIONS, item['kind'])
            self.assertIn(item['board'], (None, 'approval', 'accounting', 'finance'))
            self.assertIn(item['role'], wf.ROLE_LABELS)
            self.assertTrue(item['brief'], 'исполнителю нужно сказать, что сделать')
        for board in wf.BOARDS:
            keys = [key for key, _label in board['columns']]
            self.assertTrue(set(board['work']) <= set(keys))


class RouteTests(unittest.TestCase):
    def test_invoice_for_a_service_goes_to_accounting(self):
        route = wf.build_route(purchase(), initiator=INITIATOR, manager=MANAGER)
        self.assertEqual(kinds(route), ['initiation', 'manager_approval', 'approval', 'invoice_payment',
                                        'receiving', 'closing_docs'])
        by_kind = {entry['kind']: entry for entry in route}
        # П. 2 и п. 9: исполнитель оплаты — подразделение, а не человек.
        self.assertIsNone(by_kind['invoice_payment']['assignee_id'])
        self.assertEqual(by_kind['invoice_payment']['role_code'], 'accounting')
        self.assertEqual(by_kind['manager_approval']['assignee_id'], 8)
        self.assertEqual(by_kind['receiving']['assignee_id'], 7, 'получение подтверждает инициатор')
        self.assertIsNone(by_kind['approval']['assignee_id'], 'утверждающий не назван — задача у роли')

    def test_employee_card_adds_a_receipt_task_for_the_initiator(self):
        request = purchase(payment_method='card', card_recipient='employee', object_type='goods',
                           accounting_category='consumable')
        route = wf.build_route(request, initiator=INITIATOR, manager=MANAGER)
        self.assertEqual(kinds(route), ['initiation', 'manager_approval', 'approval', 'card_topup',
                                        'receipt_confirm', 'receiving', 'closing_docs'])
        by_kind = {entry['kind']: entry for entry in route}
        self.assertEqual(by_kind['card_topup']['role_code'], 'finance')
        self.assertIsNone(by_kind['card_topup']['assignee_id'])
        self.assertEqual(by_kind['receipt_confirm']['assignee_id'], 7)

    def test_supplier_card_has_no_receipt_and_assets_get_registered(self):
        request = purchase(payment_method='card', card_recipient='supplier', object_type='goods',
                           accounting_category='asset')
        route = wf.build_route(request, initiator=INITIATOR, manager=MANAGER,
                               approver={'id': 3, 'name': 'Утверждающий'})
        self.assertEqual(kinds(route), ['initiation', 'manager_approval', 'approval', 'card_topup',
                                        'receiving', 'asset_registration', 'closing_docs'])
        by_kind = {entry['kind']: entry for entry in route}
        self.assertEqual(by_kind['approval']['assignee_id'], 3)
        self.assertEqual(by_kind['asset_registration']['role_code'], 'asset_keeper')

    def test_service_and_consumables_never_reach_asset_registration(self):
        for request in (purchase(), purchase(object_type='goods', accounting_category='consumable')):
            self.assertNotIn('asset_registration', kinds(wf.build_route(request, initiator=INITIATOR)))

    def test_missing_manager_is_skipped_not_stuck(self):
        route = wf.build_route(purchase(), initiator=INITIATOR, manager=None)
        manager = next(entry for entry in route if entry['kind'] == 'manager_approval')
        self.assertEqual(manager['state'], 'skipped')
        self.assertIn('руководитель', manager['comment'])
        approval = next(entry for entry in route if entry['kind'] == 'approval')
        self.assertEqual(approval['state'], 'pending', 'после пропущенного руководителя заявка идёт утверждающему')

    def test_route_without_the_manager_step_has_no_manager_subtask(self):
        route = wf.build_route(purchase(), initiator=INITIATOR, manager=MANAGER, manager_step=False)
        self.assertNotIn('manager_approval', kinds(route))

    def test_working_statuses_can_be_moved_final_ones_cannot(self):
        self.assertTrue(wf.can_move('invoice_payment', 'checking'))
        self.assertTrue(wf.can_move('card_topup', 'ready'))
        self.assertFalse(wf.can_move('invoice_payment', 'paid'), '«Оплачено» ставит действие с платёжкой')
        self.assertFalse(wf.can_move('approval', 'approved'))
        self.assertFalse(wf.can_move('invoice_payment', 'clarification'))
        self.assertFalse(wf.can_move('initiation', 'draft'))


class ApprovalMatrixTests(unittest.TestCase):
    """П. 6: инициатор согласующего не выбирает — его определяет матрица."""

    def resolve(self, request=None, **kwargs):
        return wf.resolve_approval(request=request or purchase(), on_date=TODAY, **kwargs)

    def limit(self, **overrides):
        row = {'id': 1, 'status': 'active', 'delegate_user_id': 3, 'delegate_name': 'Сауле',
               'amount_limit': None, 'all_projects': True, 'all_counterparties': True,
               'project_ids': [], 'counterparty_ids': []}
        row.update(overrides)
        return row

    def test_nobody_configured_means_any_approver(self):
        basis = self.resolve()
        self.assertEqual(basis['source'], 'role')
        self.assertIsNone(basis['approver_user_id'])
        self.assertTrue(basis['manager_step'], 'у нового закупа этап руководителя есть')

    def test_regular_payment_skips_the_manager_by_default(self):
        basis = self.resolve(purchase(request_kind='regular'))
        self.assertFalse(basis['manager_step'])
        self.assertIn('регулярный платёж', basis['manager_reason'])

    def test_template_approver_wins_within_its_limit(self):
        template = {'name': 'Аренда', 'approver_user_id': 4, 'approver_name': 'Зарина', 'amount_limit': 200000}
        counterparty = {'name': 'ТОО', 'approver_user_id': 3, 'approver_name': 'Сауле', 'approval_limit': None}
        request = purchase(request_kind='regular', amount=150000)
        basis = self.resolve(request, template=template, counterparty=counterparty)
        self.assertEqual((basis['source'], basis['approver_user_id']), ('template', 4))
        # Сумма выше лимита платежа — он согласующего не называет, идём дальше по матрице.
        over = self.resolve(dict(request, amount=250000), template=template, counterparty=counterparty)
        self.assertEqual((over['source'], over['approver_user_id']), ('supplier', 3))
        self.assertFalse(over['trace'][0]['ok'])
        self.assertIn('превышает лимит', over['trace'][0]['text'])

    def test_template_does_not_apply_to_a_new_purchase(self):
        template = {'name': 'Аренда', 'approver_user_id': 4, 'approver_name': 'Зарина', 'amount_limit': None}
        self.assertEqual(self.resolve(template=template)['source'], 'role')

    def test_supplier_approver_works_within_the_approval_limit(self):
        counterparty = {'name': 'ТОО «Канцлер»', 'approver_user_id': 3, 'approver_name': 'Сауле',
                        'approval_limit': 500000}
        self.assertEqual(self.resolve(counterparty=counterparty)['source'], 'supplier')
        over = self.resolve(purchase(amount=600000), counterparty=counterparty)
        self.assertEqual(over['source'], 'role')
        self.assertIn('превышает лимит согласования', over['trace'][0]['text'])

    def test_limit_conditions_are_checked_and_explained(self):
        cases = [
            ({'status': 'cancelled'}, 'отменён'),
            ({'starts_on': date(2026, 11, 1)}, 'срок действия не покрывает'),
            ({'legal_entity_id': 99}, 'другой компании'),
            ({'department_id': 99}, 'другого подразделения'),
            ({'category_id': 99}, 'другая категория'),
            ({'payment_method': 'card'}, 'только для способа'),
            ({'request_kind': 'regular'}, 'только для заявок'),
            ({'all_counterparties': False, 'counterparty_ids': [50]}, 'отсутствует в перечне поставщиков'),
            ({'all_projects': False, 'project_ids': [50]}, 'проект заявки не входит'),
            ({'amount_limit': 100000}, 'превышает лимит'),
        ]
        for patch, reason in cases:
            with self.subTest(patch=patch):
                evaluation = wf.evaluate_limit(self.limit(**patch), request=purchase(), on_date=TODAY)
                self.assertFalse(evaluation['applies'])
                self.assertIn(reason, evaluation['reason'])
                self.assertIn('не применён. Причина:', evaluation['reason'])
        ok = wf.evaluate_limit(self.limit(legal_entity_id=1, department_id=5, category_id=3,
                                          payment_method='invoice', request_kind='purchase',
                                          all_counterparties=False, counterparty_ids=[11],
                                          amount_limit=106000), request=purchase(), on_date=TODAY)
        self.assertTrue(ok['applies'], ok['reason'])
        self.assertIsNone(ok['reason'])

    def test_amount_ladder_smallest_sufficient_limit_wins(self):
        small = self.limit(id=1, delegate_user_id=4, delegate_name='Зарина', amount_limit=200000)
        large = self.limit(id=2, delegate_user_id=3, delegate_name='Сауле', amount_limit=1000000)
        basis = self.resolve(purchase(amount=150000), limits=[large, small])
        self.assertEqual((basis['source'], basis['approver_user_id'], basis['limit_id']), ('limit', 4, 1))
        self.assertEqual(self.resolve(purchase(amount=500000), limits=[large, small])['approver_user_id'], 3)
        nobody = self.resolve(purchase(amount=5000000), limits=[large, small])
        self.assertEqual(nobody['source'], 'role')
        self.assertEqual(len([step for step in nobody['trace'] if not step['ok']]), 2)

    def test_more_specific_limit_beats_a_generic_one(self):
        generic = self.limit(id=1, delegate_user_id=3, delegate_name='Сауле', amount_limit=1000000)
        by_supplier = self.limit(id=2, delegate_user_id=4, delegate_name='Зарина', amount_limit=1000000,
                                 all_counterparties=False, counterparty_ids=[11])
        self.assertEqual(self.resolve(limits=[generic, by_supplier])['approver_user_id'], 4)
        other_supplier = self.resolve(purchase(counterparty_id=12), limits=[generic, by_supplier])
        self.assertEqual(other_supplier['approver_user_id'], 3)

    def test_equal_limits_for_different_people_are_flagged(self):
        first = self.limit(id=1, delegate_user_id=3, delegate_name='Сауле', amount_limit=500000)
        second = self.limit(id=2, delegate_user_id=4, delegate_name='Зарина', amount_limit=500000)
        self.assertTrue(self.resolve(limits=[first, second])['conflict'])
        self.assertFalse(self.resolve(limits=[first])['conflict'])

    def test_limit_with_an_order_is_named_by_the_order(self):
        with_order = self.limit(number='15', issued_on=date(2026, 9, 1))
        self.assertEqual(wf.limit_label(with_order), 'Приказ №15 от 01.09.2026')
        self.assertEqual(wf.limit_label(self.limit()), 'Лимит согласования (Сауле)')

    def test_route_names_the_default_approver_and_the_manager_step(self):
        routes = [
            {'id': 1, 'name': 'Все заявки', 'is_active': True, 'manager_step': 'auto', 'approver_user_id': 3,
             'approver_name': 'Сауле', 'position': 0},
            {'id': 2, 'name': 'Карты', 'is_active': True, 'manager_step': 'skip', 'payment_method': 'card',
             'approver_user_id': 4, 'approver_name': 'Зарина', 'position': 0},
            {'id': 3, 'name': 'Отключён', 'is_active': False, 'manager_step': 'required',
             'payment_method': 'card', 'request_kind': 'purchase', 'position': 0},
        ]
        by_invoice = self.resolve(routes=routes)
        self.assertEqual((by_invoice['source'], by_invoice['approver_user_id'], by_invoice['route_id']),
                         ('route', 3, 1))
        self.assertTrue(by_invoice['manager_step'])
        by_card = self.resolve(purchase(payment_method='card'), routes=routes)
        self.assertEqual((by_card['approver_user_id'], by_card['route_id']), (4, 2), 'точнее — раньше')
        self.assertFalse(by_card['manager_step'])
        required = self.resolve(purchase(request_kind='regular'), routes=[
            {'id': 9, 'name': 'Регулярные с руководителем', 'is_active': True, 'manager_step': 'required',
             'request_kind': 'regular', 'position': 0}])
        self.assertTrue(required['manager_step'])

    def test_route_amount_bounds(self):
        routes = [{'id': 1, 'name': 'Крупные', 'is_active': True, 'amount_from': 1000000, 'manager_step': 'auto',
                   'approver_user_id': 3, 'approver_name': 'Сауле', 'position': 0}]
        self.assertEqual(self.resolve(routes=routes)['source'], 'role')
        self.assertEqual(self.resolve(purchase(amount=1000000), routes=routes)['source'], 'route')

    def test_order_of_sources_is_template_supplier_limit_route_role(self):
        counterparty = {'name': 'ТОО', 'approver_user_id': 30, 'approver_name': 'По поставщику',
                        'approval_limit': None}
        limits = [self.limit(delegate_user_id=40, delegate_name='По лимиту')]
        routes = [{'id': 1, 'name': 'Маршрут', 'is_active': True, 'manager_step': 'auto', 'approver_user_id': 50,
                   'approver_name': 'По маршруту', 'position': 0}]
        self.assertEqual(self.resolve(counterparty=counterparty, limits=limits, routes=routes)['approver_user_id'], 30)
        self.assertEqual(self.resolve(limits=limits, routes=routes)['approver_user_id'], 40)
        self.assertEqual(self.resolve(routes=routes)['approver_user_id'], 50)


class ContractGateTests(unittest.TestCase):
    """Счёт свыше 300 000 ₸ — только по действующему договору с этим поставщиком."""

    def test_threshold_is_strictly_greater(self):
        self.assertEqual(wf.contract_gate(Decimal('300000'), None), (True, None))
        ok, reason = wf.contract_gate(Decimal('300000.01'), None)
        self.assertFalse(ok)
        self.assertIn('договор', reason)

    def test_missing_expired_terminated_and_foreign_contracts_do_not_count(self):
        active = {'number': '12', 'status': 'active', 'counterparty_id': 11, 'ends_on': date(2026, 12, 31)}
        self.assertTrue(wf.contract_gate(900000, active, counterparty_id=11, on_date=TODAY)[0])
        for patch, word in (({'status': 'terminated'}, 'Расторгнут'), ({'ends_on': date(2026, 9, 1)}, 'просрочен'),
                            ({'counterparty_id': 12}, 'другим контрагентом'),
                            ({'starts_on': date(2026, 11, 1)}, 'вступает в силу')):
            with self.subTest(patch=patch):
                ok, reason = wf.contract_gate(900000, dict(active, **patch), counterparty_id=11, on_date=TODAY)
                self.assertFalse(ok)
                self.assertIn(word, reason)


class SubmitCompletenessTests(unittest.TestCase):
    """Цель ТЗ: «исключить подачу заявок с неполными данными»."""

    def missing(self, request=None, **kwargs):
        kwargs.setdefault('items', ITEMS)
        kwargs.setdefault('offers', OFFERS)
        return wf.missing_for_submit(request or purchase(), **kwargs)

    def test_complete_purchase_can_be_submitted(self):
        self.assertEqual(self.missing(), [])

    def test_every_field_of_clause_4_1_is_required(self):
        cases = [
            ({'legal_entity_id': None}, 'компанию'), ({'department_id': None}, 'подразделение'),
            ({'expense_name': ' '}, 'наименование'), ({'amount': 0}, 'больше нуля'),
            ({'category_id': None}, 'категорию закупа'), ({'justification': ''}, 'обоснуйте'),
            ({'due_on': None}, 'желаемый срок'), ({'object_type': None}, 'товар или услуга'),
            ({'payment_method': None}, 'способ оплаты'), ({'supplier_choice_reason': ''}, 'Обоснуйте выбор'),
        ]
        for patch, word in cases:
            with self.subTest(patch=patch):
                self.assertTrue(any(word in text for text in self.missing(purchase(**patch))), patch)
        self.assertTrue(any('позицию' in text for text in self.missing(items=[])))

    def test_goods_need_an_accounting_category_services_do_not(self):
        goods = self.missing(purchase(object_type='goods', accounting_category=None))
        self.assertTrue(any('категорию учёта' in text for text in goods))
        self.assertEqual(self.missing(purchase(object_type='goods', accounting_category='asset')), [])

    def test_ordinary_purchase_needs_several_suppliers(self):
        two = self.missing(offers=OFFERS[:2])
        self.assertTrue(any('не меньше 3 вариантов поставщиков' in text for text in two))
        # Минимальное количество настраивает администратор (п. 4.2).
        self.assertEqual(self.missing(offers=OFFERS[:2], settings={'min_suppliers': 2}), [])
        self.assertEqual(wf.setting({'min_suppliers': 'мусор'}, 'min_suppliers'), 3)
        self.assertEqual(wf.setting({'min_suppliers': 99}, 'min_suppliers'), 3)

    def test_exactly_one_recommended_supplier_from_the_directory(self):
        none_marked = [dict(offer, is_recommended=False) for offer in OFFERS]
        self.assertTrue(any('одного рекомендуемого' in text for text in self.missing(offers=none_marked)))
        unknown = self.missing(purchase(counterparty_id=None))
        self.assertTrue(any('нет в справочнике' in text for text in unknown))
        broken = OFFERS[:2] + [{'supplier_name': '', 'amount': 0, 'is_recommended': False}]
        self.assertTrue(any('У поставщика №3' in text for text in self.missing(offers=broken)))

    def test_no_alternatives_needs_a_reason_and_one_supplier(self):
        single = [OFFERS[0]]
        request = purchase(no_alternatives=True, no_alternatives_reason=None, supplier_choice_reason='')
        self.assertTrue(any('причину отсутствия альтернатив' in text for text in self.missing(request, offers=single)))
        request['no_alternatives_reason'] = 'other'
        self.assertTrue(any('Опишите причину' in text for text in self.missing(request, offers=single)))
        request['no_alternatives_comment'] = 'Только он возит в регион'
        self.assertEqual(self.missing(request, offers=single), [])
        request['no_alternatives_reason'] = 'single_supplier'
        self.assertEqual(self.missing(request, offers=single), [], 'обоснование выбора тут не требуется')
        self.assertTrue(any('поставщика и стоимость' in text for text in self.missing(request, offers=[])))

    def test_card_payment_needs_recipient_holder_number_and_purpose(self):
        card = purchase(payment_method='card', card_recipient=None, card_holder_name='', payment_purpose='')
        texts = self.missing(card, card_number_ok=False)
        for word in ('чью карту', 'ФИО владельца карты', 'полный номер карты', 'назначение перевода'):
            self.assertTrue(any(word in text for text in texts), word)
        employee = purchase(payment_method='card', card_recipient='employee', card_holder_name='',
                            payment_purpose='На картриджи')
        self.assertTrue(any('ФИО сотрудника' in text for text in self.missing(employee)))
        employee['card_holder_name'] = 'Иванов Иван'
        self.assertEqual(self.missing(employee), [])

    def test_invoice_may_come_later_but_a_started_one_must_be_complete(self):
        self.assertEqual(self.missing(), [], 'счёт у нового закупа появляется после согласования')
        started = self.missing(purchase(invoice_number='118'))
        self.assertTrue(any('Приложите счёт' in text for text in started))
        self.assertTrue(any('дату счёта' in text for text in started))
        complete = purchase(invoice_number='118', invoice_date=date(2026, 10, 1))
        self.assertEqual(self.missing(complete, attachments=[{'kind': 'invoice'}]), [])
        self.assertTrue(wf.invoice_ready(complete, [{'kind': 'invoice'}]))
        self.assertFalse(wf.invoice_ready(complete, [{'kind': 'offer'}]))

    def test_regular_payment_asks_for_period_amount_and_invoice(self):
        regular = {'request_kind': 'regular', 'legal_entity_id': 1, 'department_id': 5,
                   'expense_name': 'Аренда офиса', 'amount': 200000, 'fixed_template_id': 9,
                   'counterparty_id': 11, 'payment_period': '', 'payment_method': 'invoice'}
        item = [{'name': 'Аренда офиса', 'quantity': 1, 'unit_price': 200000}]
        texts = wf.missing_for_submit(regular, items=item)
        for word in ('период оплаты', 'Приложите счёт', 'номер счёта', 'дату счёта'):
            self.assertTrue(any(word in text for text in texts), word)
        self.assertFalse(any('поставщик' in text.lower() and 'вариант' in text.lower() for text in texts),
                         'сравнение поставщиков у регулярного платежа не требуется')
        regular.update(payment_period='октябрь 2026', invoice_number='АР-10', invoice_date=date(2026, 10, 1))
        self.assertEqual(wf.missing_for_submit(regular, items=item, attachments=[{'kind': 'invoice'}]), [])
        # Календарь создаёт заявку первого числа — счёта у него ещё нет.
        regular.update(invoice_number='', invoice_date=None)
        self.assertEqual(wf.missing_for_submit(regular, items=item, invoice_required=False), [])
        without_template = dict(regular, fixed_template_id=None, counterparty_id=None)
        self.assertTrue(any('из справочника' in text for text in
                            wf.missing_for_submit(without_template, items=item, invoice_required=False)))
        # П. 10 действует и для регулярного платежа: у товара категорию учёта даёт справочник.
        goods = dict(regular, object_type='goods', accounting_category=None)
        self.assertTrue(any('категория учёта' in text for text in
                            wf.missing_for_submit(goods, items=item, invoice_required=False)))
        self.assertEqual(wf.missing_for_submit(dict(goods, accounting_category='consumable'), items=item,
                                               invoice_required=False), [])

    def test_unknown_kind_is_the_only_complaint(self):
        self.assertEqual(len(wf.missing_for_submit({'request_kind': None})), 1)


class LegacyRequestTests(unittest.TestCase):
    """Заявки первой версии процесса (12 шагов), которые застал перенос (payments/legacy.py).

    Они приняты и согласованы по прежним правилам: трёх поставщиков, обоснования
    и подразделения у них нет. Новая полнота не должна ни остановить их, ни
    отправить на согласование заново.
    """

    LEGACY = {'request_kind': 'purchase', 'legal_entity_id': 1, 'department_id': None, 'expense_name': 'Стулья',
              'amount': Decimal('106000'), 'category_id': 3, 'payment_method': 'invoice', 'counterparty_id': 11,
              'object_type': 'service', 'project_id': None, 'legacy_step': 7}
    ITEM = [{'name': 'Стулья', 'quantity': 1, 'unit_price': 106000}]
    INVOICE = {'invoice_number': 'СЧ-7', 'invoice_date': date(2026, 10, 1)}

    def test_accepted_request_needs_only_the_company_and_the_invoice(self):
        texts = wf.missing_for_submit(self.LEGACY, items=self.ITEM)
        self.assertEqual(len(texts), 3, texts)
        for word in ('Приложите счёт', 'номер счёта', 'дату счёта'):
            self.assertTrue(any(word in text for text in texts), word)
        with_invoice = dict(self.LEGACY, **self.INVOICE)
        self.assertEqual(wf.missing_for_submit(with_invoice, items=self.ITEM, attachments=[{'kind': 'invoice'}]), [])
        # На шаге 5 первой версии компанию ещё выбирала бухгалтерия — её и просим.
        no_company = dict(with_invoice, legal_entity_id=None, legacy_step=5)
        self.assertEqual(wf.missing_for_submit(no_company, items=self.ITEM, attachments=[{'kind': 'invoice'}]),
                         ['Выберите компанию-плательщика'])

    def test_old_fixed_payment_without_a_directory_entry_is_not_stuck(self):
        regular = dict(self.LEGACY, request_kind='regular', fixed_template_id=None, **self.INVOICE)
        self.assertEqual(wf.missing_for_submit(regular, items=self.ITEM, attachments=[{'kind': 'invoice'}]), [])

    def test_draft_of_the_old_process_follows_the_new_rules(self):
        draft = dict(self.LEGACY, legacy_step=1)
        self.assertFalse(wf.accepted_before(draft))
        self.assertTrue(any('подразделение' in text for text in wf.missing_for_submit(draft, items=self.ITEM)))
        self.assertFalse(wf.accepted_before(purchase()), 'заявка по ТЗ — не «принятая раньше»')

    def test_filling_an_empty_field_is_not_a_change_of_terms(self):
        snapshot = wf.essentials(self.LEGACY)
        filled = dict(self.LEGACY, department_id=5, project_id=2)
        self.assertFalse(wf.essentials_changed(snapshot, filled))
        self.assertEqual(wf.essentials_diff(snapshot, filled), [])
        dearer = dict(filled, amount=Decimal('900000'))
        self.assertTrue(wf.essentials_changed(snapshot, dearer), 'сумму и у старой заявки молча не меняют')
        self.assertEqual(wf.essentials_diff(snapshot, dearer), ['сумма'])

    def test_request_made_by_the_spec_gets_no_such_allowance(self):
        native = purchase(project_id=None)
        snapshot = wf.essentials(native)
        self.assertTrue(wf.essentials_changed(snapshot, dict(native, project_id=2)))


class RouteExecutorsTests(unittest.TestCase):
    """П. 1: «исключить потерю заявок» — заявка не уходит роли, в которой никого нет."""

    MEMBERS = {'approver': {3, 4}, 'accounting': {6}}

    def gaps(self, members=None, approver=None, **request):
        route = wf.build_route(purchase(**request), initiator=INITIATOR, manager=MANAGER, approver=approver)
        return flow.route_gaps(route, self.MEMBERS if members is None else members, INITIATOR['id'])

    def test_staffed_route_has_no_gaps(self):
        self.assertEqual(self.gaps(), [])

    def test_card_and_assets_need_their_own_people(self):
        self.assertTrue(any('Финансовый отдел' in text for text in self.gaps(payment_method='card')))
        asset = self.gaps(object_type='goods', accounting_category='asset')
        self.assertTrue(any('учёт имущества' in text for text in asset))
        staffed = dict(self.MEMBERS, finance={9}, asset_keeper={23})
        self.assertEqual(self.gaps(staffed, payment_method='card', object_type='goods',
                                   accounting_category='asset'), [])

    def test_empty_accounting_is_named_once(self):
        gaps = self.gaps({'approver': {3}})
        self.assertEqual(len([text for text in gaps if 'Бухгалтерия' in text]), 1,
                         'оплата и закрывающие документы — одна роль, одна строка')

    def test_initiator_is_not_their_own_approver(self):
        only_me = {'approver': {INITIATOR['id']}, 'accounting': {6}}
        self.assertTrue(any('Утвердитель' in text for text in self.gaps(only_me)))
        self.assertEqual(self.gaps({'approver': {INITIATOR['id'], 3}, 'accounting': {6}}), [])
        named = self.gaps({'approver': set(), 'accounting': {6}}, approver={'id': 3, 'name': 'Сауле'})
        self.assertEqual(named, [], 'согласующего назвала матрица — роль целиком не нужна')


class SearchPatternTests(unittest.TestCase):
    def test_wildcards_from_the_query_are_literal(self):
        self.assertEqual(sqlutil.like_pattern('СЧ-100'), '%СЧ-100%')
        self.assertEqual(sqlutil.like_pattern('%%%'), '%\\%\\%\\%%')
        self.assertEqual(sqlutil.like_pattern('a_b'), '%a\\_b%')
        self.assertEqual(sqlutil.like_pattern('a\\b'), '%a\\\\b%')


class ActionRequirementsTests(unittest.TestCase):
    def missing(self, kind, action, **kwargs):
        kwargs.setdefault('request', purchase())
        return wf.missing_for_action(kind, action, **kwargs)

    def test_return_and_reject_need_words(self):
        self.assertEqual(self.missing('approval', 'approve'), [])
        self.assertTrue(self.missing('approval', 'return', fields={'comment': ' '}))
        self.assertTrue(self.missing('manager_approval', 'reject', fields={}))
        self.assertEqual(self.missing('approval', 'reject', fields={'comment': 'Не нужно'}), [])

    def test_request_info_takes_a_reason_from_the_list_of_this_task(self):
        self.assertTrue(self.missing('invoice_payment', 'request_info', fields={}))
        self.assertEqual(self.missing('invoice_payment', 'request_info', fields={'reason': 'requisites'}), [])
        self.assertTrue(self.missing('card_topup', 'request_info', fields={'reason': 'requisites'}),
                        'причина бухгалтерии финансовому отделу не подходит')
        self.assertTrue(self.missing('invoice_payment', 'request_info', fields={'reason': 'other'}))
        self.assertEqual(self.missing('invoice_payment', 'request_info',
                                      fields={'reason': 'other', 'comment': 'Нужна доверенность'}), [])
        # Системные причины ставит сам раздел — человек их не выбирает.
        self.assertTrue(self.missing('invoice_payment', 'request_info', fields={'reason': 'invoice_missing'}))
        self.assertNotIn('invoice_missing', [item['code'] for item in wf.clarify_reasons_for('invoice_payment')])
        self.assertEqual(wf.clarify_label('rework'), 'Возврат на доработку')

    def test_payment_records_date_amount_and_the_payment_order(self):
        texts = self.missing('invoice_payment', 'pay', fields={})
        self.assertEqual(len(texts), 3)
        ready = {'paid_on': TODAY, 'paid_amount': 106000}
        self.assertTrue(self.missing('invoice_payment', 'pay', fields=ready, file_kinds={'invoice'}))
        self.assertEqual(self.missing('invoice_payment', 'pay', fields=ready, file_kinds={'payment_order'}), [])
        self.assertTrue(self.missing('card_topup', 'top_up', fields=ready, file_kinds={'payment_order'}))
        self.assertEqual(self.missing('card_topup', 'top_up', fields=ready, file_kinds={'transfer_proof'}), [])

    def test_receipt_and_receiving(self):
        self.assertTrue(self.missing('receipt_confirm', 'provide'))
        self.assertEqual(self.missing('receipt_confirm', 'provide', file_kinds={'receipt'}), [])
        self.assertTrue(self.missing('receiving', 'confirm', fields={}))
        self.assertEqual(self.missing('receiving', 'confirm', fields={'received_on': TODAY}), [],
                         'у услуги количества нет')
        goods = purchase(object_type='goods', accounting_category='consumable')
        self.assertTrue(self.missing('receiving', 'confirm', request=goods, fields={'received_on': TODAY}))
        self.assertEqual(self.missing('receiving', 'confirm', request=goods,
                                      fields={'received_on': TODAY, 'received_quantity': '10 уп'}), [])

    def test_asset_card_needs_all_twelve_fields_and_the_act(self):
        asset = {'name': 'Ноутбук', 'category_id': 1, 'serial_number': 'SN-1', 'inventory_number': 'ИНВ-1',
                 'received_on': TODAY, 'cost': 350000, 'legal_entity_id': 1, 'city': 'Алматы', 'department_id': 5,
                 'responsible_user_id': 7, 'location': 'Офис, каб. 12', 'status': 'in_use'}
        self.assertEqual(len(wf.ASSET_REQUIRED_FIELDS), 12)
        self.assertEqual(wf.asset_missing(asset), [])
        for key, label in wf.ASSET_REQUIRED_FIELDS:
            with self.subTest(field=key):
                self.assertEqual(wf.asset_missing(dict(asset, **{key: None})), [label])
        self.assertEqual(wf.asset_missing(dict(asset, cost=0)), [], 'нулевая стоимость — это стоимость')
        self.assertEqual(wf.asset_missing(dict(asset, status='lost')), ['статус имущества'])
        request = purchase(object_type='goods', accounting_category='asset')
        self.assertTrue(self.missing('asset_registration', 'register', request=request, assets=[]))
        no_act = self.missing('asset_registration', 'register', request=request, assets=[asset])
        self.assertEqual(no_act, ['Приложите акт приёма-передачи'])
        self.assertEqual(self.missing('asset_registration', 'register', request=request, assets=[asset],
                                      file_kinds={'handover_act'}), [])
        gaps = self.missing('asset_registration', 'register', request=request,
                            assets=[asset, dict(asset, inventory_number='')], file_kinds={'handover_act'})
        self.assertEqual(gaps, ['Имущество №2: не заполнено — инвентарный номер'])

    def test_documents_close_only_after_a_scan_or_the_original(self):
        self.assertTrue(self.missing('closing_docs', 'docs_close', request=purchase(closing_docs_status='none')))
        for status in ('scan', 'original'):
            self.assertEqual(self.missing('closing_docs', 'docs_close',
                                          request=purchase(closing_docs_status=status)), [])


class ClosingConditionsTests(unittest.TestCase):
    """П. 15: заявка закрывается только после всех обязательных этапов."""

    def done(self, *kinds_done):
        return [{'kind': kind, 'state': 'done', 'outcome': None} for kind in kinds_done]

    def test_service(self):
        request = purchase(closing_docs_status='closed')
        conditions = wf.closing_conditions(request, self.done('approval', 'invoice_payment', 'receiving'))
        self.assertEqual([item['label'] for item in conditions], [
            'Закуп согласован', 'Оплата проведена', 'Услуга получена', 'Закрывающие документы получены'])
        self.assertTrue(all(item['ok'] for item in conditions))
        self.assertEqual(wf.closing_blockers(purchase(closing_docs_status='scan'),
                                             self.done('approval', 'invoice_payment')),
                         ['Услуга получена', 'Закрывающие документы получены'])

    def test_consumable_goods(self):
        request = purchase(object_type='goods', accounting_category='consumable', closing_docs_status='closed')
        conditions = wf.closing_conditions(request, self.done('approval', 'card_topup', 'receiving'))
        self.assertEqual([item['label'] for item in conditions], [
            'Закуп согласован', 'Оплата проведена', 'Товар получен', 'Закрывающие документы получены'])
        self.assertTrue(all(item['ok'] for item in conditions))

    def test_assets_need_number_act_and_place(self):
        request = purchase(object_type='goods', accounting_category='asset', closing_docs_status='closed')
        subtasks = self.done('approval', 'card_topup', 'receiving', 'asset_registration')
        asset = {'inventory_number': 'ИНВ-1', 'city': 'Алматы', 'department_id': 5, 'responsible_user_id': 7}
        conditions = wf.closing_conditions(request, subtasks, assets=[asset], has_handover_act=True)
        self.assertEqual([item['label'] for item in conditions], [
            'Закуп согласован', 'Оплата проведена', 'Товар получен', 'Инвентарный номер присвоен',
            'Акт приёма-передачи приложен', 'Указаны город, подразделение и ответственный сотрудник',
            'Закрывающие документы получены'])
        self.assertTrue(all(item['ok'] for item in conditions))
        self.assertEqual(wf.closing_blockers(request, subtasks, assets=[], has_handover_act=False), [
            'Инвентарный номер присвоен', 'Акт приёма-передачи приложен',
            'Указаны город, подразделение и ответственный сотрудник'])
        self.assertEqual(wf.closing_blockers(request, subtasks, assets=[dict(asset, city='')],
                                             has_handover_act=True),
                         ['Указаны город, подразделение и ответственный сотрудник'])

    def test_rejected_approval_is_not_an_approval(self):
        subtasks = [{'kind': 'approval', 'state': 'done', 'outcome': 'rejected'}]
        self.assertFalse(wf.closing_conditions(purchase(), subtasks)[0]['ok'])


class BoardColumnTests(unittest.TestCase):
    """Доска — вид на подзадачу: колонка следует за её состоянием (п. 2)."""

    def column(self, board, subtasks, status='active', focus=None):
        return wf.board_column(board, {'status': status}, subtasks, focus_kind=focus)

    def task(self, kind, state, status=None, outcome=None):
        return {'kind': kind, 'state': state, 'status': status, 'outcome': outcome}

    def test_approval_board(self):
        manager_done = [self.task('manager_approval', 'done'), self.task('approval', 'open', 'review')]
        self.assertEqual(self.column('approval', manager_done), 'review')
        self.assertEqual(self.column('approval', manager_done, focus='manager_approval'), 'approved',
                         'руководитель видит свою задачу согласованной')
        self.assertEqual(self.column('approval', [self.task('approval', 'open', 'new')]), 'new')
        self.assertEqual(self.column('approval', [self.task('approval', 'waiting')]), 'clarification')
        rejected = [self.task('approval', 'done', outcome='rejected')]
        self.assertEqual(self.column('approval', rejected, status='rejected'), 'rejected')
        # Отклонённая заявка не бывает «новой», даже если подзадача осталась открытой.
        stray = [self.task('approval', 'open', 'new')]
        self.assertEqual(self.column('approval', stray, status='rejected', focus='approval'), 'rejected')
        self.assertIsNone(self.column('approval', [self.task('approval', 'pending')]))

    def test_accounting_board(self):
        self.assertIsNone(self.column('accounting', [self.task('invoice_payment', 'pending')]),
                          'до согласования счёта на доске бухгалтерии нет')
        for status in ('new', 'checking', 'ready', 'paying'):
            self.assertEqual(self.column('accounting', [self.task('invoice_payment', 'open', status)]), status)
        self.assertEqual(self.column('accounting', [self.task('invoice_payment', 'waiting')]), 'clarification')
        paid = [self.task('invoice_payment', 'done'), self.task('closing_docs', 'pending')]
        self.assertEqual(self.column('accounting', paid), 'paid')
        paid[1] = self.task('closing_docs', 'open', 'awaiting')
        self.assertEqual(self.column('accounting', paid), 'awaiting_docs')
        paid[1] = self.task('closing_docs', 'done')
        self.assertEqual(self.column('accounting', paid, status='done'), 'closed')
        # Заявка с картой тоже приходит в бухгалтерию — за закрывающими документами.
        card = [self.task('card_topup', 'done'), self.task('closing_docs', 'open', 'awaiting')]
        self.assertEqual(self.column('accounting', card), 'awaiting_docs')

    def test_finance_board(self):
        self.assertIsNone(self.column('finance', [self.task('card_topup', 'pending')]),
                          'на доску попадают только согласованные пополнения (п. 9)')
        for status in ('new', 'in_progress', 'ready'):
            self.assertEqual(self.column('finance', [self.task('card_topup', 'open', status)]), status)
        self.assertEqual(self.column('finance', [self.task('card_topup', 'waiting')]), 'clarification')
        employee = [self.task('card_topup', 'done'), self.task('receipt_confirm', 'open', 'new')]
        self.assertEqual(self.column('finance', employee), 'awaiting_receipt')
        employee[1] = self.task('receipt_confirm', 'done')
        self.assertEqual(self.column('finance', employee), 'closed')
        supplier = [self.task('card_topup', 'done')]
        self.assertEqual(self.column('finance', supplier), 'topped_up')
        self.assertEqual(self.column('finance', supplier, status='done'), 'closed')

    def test_cancelled_and_rejected_requests_leave_the_department_boards(self):
        task = [self.task('invoice_payment', 'open', 'new')]
        self.assertIsNone(self.column('accounting', task, status='cancelled'))
        self.assertIsNone(self.column('accounting', task, status='rejected'))
        self.assertIsNone(self.column('approval', [self.task('approval', 'open', 'new')], status='cancelled'))


class StateTests(unittest.TestCase):
    def test_state_order(self):
        base = {'status': 'active', 'stage': 'payment', 'due_on': date(2026, 10, 1)}
        self.assertEqual(wf.request_state(base, TODAY), 'overdue')
        self.assertEqual(wf.request_state(dict(base, paid_on=date(2026, 10, 5)), TODAY), 'active',
                         'оплаченная заявка не просрочена')
        returned = dict(base, stage='initiation', submitted_at='2026-10-02T10:00:00')
        self.assertEqual(wf.request_state(returned, TODAY), 'clarification', 'ответ инициатора важнее просрочки')
        draft = dict(base, stage='initiation', submitted_at=None, due_on=None)
        self.assertEqual(wf.request_state(draft, TODAY), 'active')
        for status in ('done', 'rejected', 'cancelled'):
            self.assertEqual(wf.request_state(dict(base, status=status), TODAY), status)

    def test_lifecycle_hides_stages_the_request_does_not_have(self):
        subtasks = [
            {'kind': 'initiation', 'stage': 'initiation', 'state': 'done'},
            {'kind': 'approval', 'stage': 'approval', 'state': 'done'},
            {'kind': 'invoice_payment', 'stage': 'payment', 'state': 'open'},
            {'kind': 'receiving', 'stage': 'receiving', 'state': 'pending'},
            {'kind': 'closing_docs', 'stage': 'closing', 'state': 'pending'},
        ]
        states = {item['code']: item['state'] for item in
                  wf.lifecycle({'status': 'active', 'stage': 'payment', 'submitted_at': 'x'}, subtasks)}
        self.assertEqual(states, {'initiation': 'done', 'approval': 'done', 'payment': 'current',
                                  'receiving': 'pending', 'assets': 'skipped', 'closing': 'pending',
                                  'closed': 'pending'})

    def test_returned_request_stays_on_the_stage_that_asked(self):
        subtasks = [
            {'kind': 'initiation', 'stage': 'initiation', 'state': 'open'},
            {'kind': 'approval', 'stage': 'approval', 'state': 'done'},
            {'kind': 'invoice_payment', 'stage': 'payment', 'state': 'waiting'},
        ]
        states = {item['code']: item['state'] for item in
                  wf.lifecycle({'status': 'active', 'stage': 'initiation', 'submitted_at': 'x'}, subtasks)}
        self.assertEqual((states['initiation'], states['approval'], states['payment']), ('done', 'done', 'current'))
        fresh = wf.lifecycle({'status': 'active', 'stage': 'initiation', 'submitted_at': None}, subtasks[:1])
        self.assertEqual(fresh[0]['state'], 'current')
        closed = wf.lifecycle({'status': 'done', 'stage': 'closed'}, [])
        self.assertEqual(closed[-1], {'code': 'closed', 'label': 'Закрытие', 'state': 'done'})


class EssentialsTests(unittest.TestCase):
    """Смена существенных условий после согласования возвращает заявку на согласование."""

    def test_changed_amount_supplier_or_method_means_reapproval(self):
        request = purchase()
        snapshot = wf.essentials(request)
        self.assertFalse(wf.essentials_changed(snapshot, request))
        self.assertFalse(wf.essentials_changed(snapshot, dict(request, justification='другой текст', notes='x')),
                         'описание — не существенное условие')
        for patch, label in (({'amount': Decimal('900000')}, 'сумма'), ({'counterparty_id': 12}, 'поставщик'),
                             ({'payment_method': 'card'}, 'способ оплаты'), ({'legal_entity_id': 2}, 'компания')):
            with self.subTest(patch=patch):
                changed = dict(request, **patch)
                self.assertTrue(wf.essentials_changed(snapshot, changed))
                self.assertEqual(wf.essentials_diff(snapshot, changed), [label])

    def test_amount_is_compared_as_money_not_as_text(self):
        snapshot = wf.essentials(purchase(amount='106000'))
        self.assertFalse(wf.essentials_changed(snapshot, purchase(amount=Decimal('106000.00'))))

    def test_stale_flag_marks_a_changed_card_number(self):
        request = purchase()
        snapshot = dict(wf.essentials(request), stale=True)
        self.assertTrue(wf.essentials_changed(snapshot, request))
        self.assertEqual(wf.essentials_diff(snapshot, request), ['номер карты'])
        self.assertTrue(wf.essentials_changed(None, request), 'нет снимка — согласование с начала')


# Те же пары стоят в tests/payments_meta.test.mjs (LINE_TOTALS): форма и сервер
# обязаны сойтись до тиына. Меняете одну таблицу — меняйте обе.
LINE_TOTALS = [
    ('5', '2000', '10000'),
    ('2,5', '10,01', '25.03'),
    ('1,5', '33,33', '50'),
    ('0,333', '3', '1'),
    ('3', '0,335', '1.02'),
    ('1,0005', '100', '100.1'),
    ('0,0004', '100', '0'),
    ('1234567,891', '98765,43', '121932628618.81'),
    ('20', '2 500', '50000'),
    ('7', '0,1', '0.7'),
    ('0.1', '0.2', '0.02'),
    ('3', '1 234,565', '3703.71'),
    ('2.675', '1', '2.68'),
    ('1', '2.675', '2.68'),
]


class MoneyTests(unittest.TestCase):
    def test_item_total_rounds_half_up_to_tiyn(self):
        self.assertEqual(wf.item_total({'quantity': '2.5', 'unit_price': '10.01'}), Decimal('25.03'))
        self.assertEqual(wf.items_total([{'quantity': 1, 'unit_price': 2500}, {'quantity': 3, 'unit_price': 150}]),
                         Decimal('2950.00'))

    def test_line_totals_match_the_form(self):
        for quantity, price, expected in LINE_TOTALS:
            with self.subTest(quantity=quantity, price=price):
                self.assertEqual(wf.item_total({'quantity': quantity, 'unit_price': price}), Decimal(expected))

    def test_money_and_date_format(self):
        self.assertEqual(wf.fmt_money('1234567.5'), '1 234 567,50 ₸')
        self.assertEqual(wf.fmt_date(date(2026, 10, 8)), '08.10.2026')


class FrontendAgreesWithRulesTests(unittest.TestCase):
    """Подписи живут в двух местах — в правилах сервера и в paymentsMeta.js: сервер
    пишет их в уведомления и выгрузку, форма — в селекторы. Разойдутся — человек
    увидит «Оплата счёта» на экране и другое слово в письме."""

    @classmethod
    def setUpClass(cls):
        cls.source = META_PATH.read_text(encoding='utf-8')

    def block(self, name):
        return self.source.split('export const %s' % name)[1].split('};')[0]

    def assert_labels(self, name, labels):
        block = self.block(name)
        for code, label in labels.items():
            self.assertRegex(block, r"\b%s: (\{ label: )?'%s'" % (code, re.escape(label)), '%s.%s' % (name, code))

    def test_labels_of_choices(self):
        self.assert_labels('REQUEST_KIND_LABELS', wf.REQUEST_KIND_LABELS)
        self.assert_labels('PAYMENT_METHOD_LABELS', wf.PAYMENT_METHOD_LABELS)
        self.assert_labels('CARD_RECIPIENT_LABELS', wf.CARD_RECIPIENT_LABELS)
        self.assert_labels('OBJECT_TYPE_LABELS', wf.OBJECT_TYPE_LABELS)
        self.assert_labels('ACCOUNTING_CATEGORY_LABELS', wf.ACCOUNTING_CATEGORY_LABELS)
        self.assert_labels('ROLE_LABELS', wf.ROLE_LABELS)
        self.assert_labels('ATTACHMENT_LABELS', wf.ATTACHMENT_LABELS)
        self.assert_labels('DOCS_STATUS_META', wf.CLOSING_DOC_LABELS)
        self.assert_labels('ASSET_STATUS_META', wf.ASSET_STATUS_LABELS)
        self.assert_labels('CONTRACT_STATUS_META', wf.CONTRACT_STATUS_LABELS)
        self.assert_labels('PERIOD_META', fixed.PERIOD_LABELS)

    def test_codes_of_choices(self):
        def values(name):
            return re.findall(r"value: '([a-z_]+)'", self.source.split('export const %s' % name)[1].split('];')[0])

        self.assertEqual(values('REQUEST_KIND_OPTIONS'), list(schema.REQUEST_KINDS))
        self.assertEqual(values('PAYMENT_METHOD_OPTIONS'), list(schema.PAYMENT_METHODS))
        self.assertEqual(values('CARD_RECIPIENT_OPTIONS'), list(schema.CARD_RECIPIENTS))
        self.assertEqual(values('OBJECT_TYPE_OPTIONS'), list(schema.OBJECT_TYPES))
        self.assertEqual(values('ACCOUNTING_CATEGORY_OPTIONS'), list(schema.ACCOUNTING_CATEGORIES))
        self.assertEqual(values('MANAGER_STEP_OPTIONS'), list(schema.MANAGER_STEP_MODES))
        self.assertEqual(values('PARTY_KIND_OPTIONS'), list(wf.SUPPLIER_KIND_LABELS))
        roles = re.findall(r"code: '([a-z_]+)'", self.source.split('export const MEMBER_ROLES')[1].split('];')[0])
        self.assertEqual(roles, list(schema.MEMBER_ROLES))

    def test_directories_are_the_eight_from_clause_13(self):
        """П. 13 называет восемь справочников — они стоят первыми, теми же словами и в том же порядке."""
        source = (ROOT / 'src' / 'components' / 'payments' / 'PaymentsDictionaries.jsx').read_text(encoding='utf-8')
        sections = source.split('const SECTIONS = [')[1].split('];')[0]
        labels = re.findall(r"\{ value: '[a-z_]+', label: '([^']+)'", sections)
        self.assertEqual(labels[:8], [
            'Поставщики', 'Договоры', 'Реквизиты компаний', 'Банковские реквизиты поставщиков',
            'Банковские карты сотрудников/поставщиков', 'Регулярные платежи', 'Лимиты согласования',
            'Маршруты согласования'])

    def test_contract_threshold(self):
        self.assertIn('export const CONTRACT_THRESHOLD = %s;' % int(wf.CONTRACT_REQUIRED_OVER), self.source)

    def test_every_event_kind_is_described_in_words(self):
        """История — журнал для людей (п. 18): неизвестный вид события показался бы кодом."""
        described = set(re.findall(r"case '([a-z_]+)':", self.source.split('export const describeEvent')[1]))
        # card_revealed (кто смотрел номер карты) видит администратор — и оно описано словами.
        self.assertEqual(set(schema.EVENT_KINDS) - described, set())

    def test_events_written_by_the_code_are_declared(self):
        written = set()
        for path in (ROOT / 'payments').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            written |= set(re.findall(r"log_event\(\s*cursor,\s*[\w\[\]'.]+,\s*'([a-z_]+)'", text))
            written |= set(re.findall(r"event_kind='([a-z_]+)'", text))
        self.assertTrue(written, 'не нашёл ни одной записи события — изменился вызов, проверка ослепла')
        self.assertEqual(written - set(schema.EVENT_KINDS), set())


if __name__ == '__main__':
    unittest.main()
