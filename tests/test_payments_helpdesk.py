# -*- coding: utf-8 -*-
"""IT-закуп через Helpdesk IT (просьба владельца 04.11.2026).

«Человек создаёт заявку; если закуп относится к IT — запрос поступает руководителю на
согласование, далее в Helpdesk IT: они проверяют, есть ли такое у них, какое имущество
сейчас числится за сотрудником, и прикрепляют варианты поставщиков». Решения владельца в
том же разговоре: IT-закуп узнаётся по категории (отметка «Helpdesk IT» в справочнике);
нашлось в наличии — выдают, заявка закрывается «Выдано из наличия»; цены ставит Helpdesk
IT по рекомендуемому поставщику, и утверждающего выбирают уже по этой сумме.

Здесь — правила (workflow), права (access), тексты (notify) и проводка: ход заявки
(flow) и ручки (routes) с подменёнными запросами — тем же средством, что и в
test_payments_entity_step.py. Что те же запросы значат на настоящей базе, проверяет
tests/test_payments_helpdesk_postgres.py.
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from flask import Flask

from payments import access, flow, notify, report, routes, routes_directory, workflow as wf

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
REQUEST_ID = 812
INITIATOR = {'id': 7, 'name': 'Инициаторова Ирина'}
MANAGER = {'id': 8, 'name': 'Руководов Рустам'}
HELPDESK = {'id': 9, 'name': 'Хелпов Хасан'}
# Лимит «до 500 000 ₸ — Утвердова»: по нему и решается, кто утверждает, когда сумма стала настоящей.
SMALL_LIMIT = {'id': 41, 'number': '7', 'issued_on': date(2026, 9, 1), 'status': 'active',
               'delegate_user_id': 30, 'delegate_name': 'Утвердова Ульяна', 'all_projects': True, 'all_counterparties': True,
               'amount_limit': Decimal('500000')}


def purchase(**overrides):
    row = {
        'id': REQUEST_ID, 'status': 'active', 'request_kind': 'purchase', 'legal_entity_id': 2,
        'department_id': 5, 'expense_name': 'Ноутбук для работы', 'amount': Decimal('0'), 'category_id': 3,
        'payment_method': 'invoice', 'object_type': 'goods', 'accounting_category': 'asset',
        'justification': 'Старый не тянет 1С', 'due_on': date(2026, 11, 20), 'initiator_id': INITIATOR['id'],
        'initiator_name': INITIATOR['name'], 'manager_id': MANAGER['id'], 'it_purchase': True,
        'category_helpdesk': True, 'no_alternatives': False, 'counterparty_id': None, 'legacy_step': None,
    }
    row.update(overrides)
    return row


def kinds(route):
    return [entry['kind'] for entry in route]


def offers(*amounts, recommended=0):
    return [{'supplier_name': 'Поставщик %s' % (index + 1), 'counterparty_id': 100 + index, 'amount': Decimal(amount),
             'is_recommended': index == recommended} for index, amount in enumerate(amounts)]


def items(*prices):
    return [{'name': 'Позиция %s' % (index + 1), 'quantity': Decimal('1'), 'unit': 'шт', 'unit_price': Decimal(price)}
            for index, price in enumerate(prices)]


class RouteTests(unittest.TestCase):
    def test_it_purchase_goes_through_helpdesk_after_the_manager(self):
        route = wf.build_route(purchase(), initiator=INITIATOR, manager=MANAGER)
        self.assertEqual(kinds(route)[:4], ['initiation', 'manager_approval', 'it_review', 'approval'])
        step = next(entry for entry in route if entry['kind'] == wf.KIND_IT)
        # Задача списку «Helpdesk IT», как бухгалтерии: исполнителя-человека у неё нет.
        self.assertEqual((step['role_code'], step['assignee_id'], step['state'], step['stage']),
                         ('helpdesk', None, 'pending', 'approval'))

    def test_order_is_manager_helpdesk_company_approver(self):
        """Владелец: «сначала руководитель… и хелпдеск уже делает свою работу»; компанию без
        инициатора бухгалтерия уточняет после (03.11.2026) — Helpdesk стоит между ними."""
        route = wf.build_route(purchase(legal_entity_id=None), initiator=INITIATOR, manager=MANAGER)
        self.assertEqual(kinds(route)[:5], ['initiation', 'manager_approval', 'it_review', 'entity_clarify', 'approval'])
        positions = [entry['position'] for entry in route]
        self.assertEqual(positions, sorted(positions), 'advance открывает этапы по позиции')

    def test_no_helpdesk_where_it_is_not_needed(self):
        for row in (purchase(it_purchase=False), purchase(request_kind='regular'), purchase(legacy_step=4)):
            self.assertNotIn(wf.KIND_IT, kinds(wf.build_route(row, initiator=INITIATOR, manager=MANAGER)), row)

    def test_route_keeps_the_decision_made_at_sending(self):
        """Отметку у категории сняли, пока заявка в пути, — маршрут в пути не меняется (и наоборот)."""
        self.assertTrue(wf.needs_it_step(purchase(category_helpdesk=False)))
        self.assertFalse(wf.needs_it_step(purchase(it_purchase=False, category_helpdesk=True)))
        self.assertTrue(wf.it_category(purchase(it_purchase=False)))
        self.assertFalse(wf.it_category(purchase(category_helpdesk=False)))

    def test_who_brings_suppliers_follows_the_route_that_will_be_built(self):
        draft = purchase(it_purchase=False, submitted_at=None)
        self.assertTrue(wf.suppliers_by_helpdesk(draft), 'черновик: решает отметка категории сейчас')
        self.assertFalse(wf.suppliers_by_helpdesk(dict(draft, category_helpdesk=False)))
        sent = purchase(submitted_at='2026-11-04T10:00:00', it_purchase=True, category_helpdesk=False)
        sent['submitted_snapshot'] = wf.essentials(sent)
        self.assertTrue(wf.suppliers_by_helpdesk(sent), 'ответ без смены условий — прежним маршрутом')
        changed = dict(sent, category_id=4)
        self.assertFalse(wf.suppliers_by_helpdesk(changed), 'сменили категорию — новый маршрут, по новой категории')


class ManagerAndCardTests(unittest.TestCase):
    def test_manager_comes_first_even_when_the_route_skips_him(self):
        """При отправке цены нет: маршрут «до N ₸ — без руководителя» по нулевой сумме поставил бы
        руководителя ПОСЛЕ Helpdesk IT, когда сумма станет настоящей (разбор 04.11.2026)."""
        skip = {'name': 'Мелкое — без руководителя', 'manager_step': 'skip'}
        self.assertEqual(wf.manager_step_needed(skip, purchase())[0], True)
        self.assertEqual(wf.manager_step_needed(skip, purchase(it_purchase=False))[0], True, 'черновик IT-категории')
        self.assertEqual(wf.manager_step_needed(skip, purchase(it_purchase=False, category_helpdesk=False))[0], False)
        self.assertEqual(wf.manager_step_needed(skip, purchase(request_kind='regular'))[0], False)

    def test_supplier_card_waits_for_the_supplier(self):
        row = purchase(submitted_at=None, payment_method='card', card_recipient='supplier', card_holder_name='',
                       payment_purpose='')
        missing = wf.missing_for_submit(row, items=items('0'), offers=[], attachments=[], settings={})
        self.assertEqual(missing, [wf.IT_SUPPLIER_CARD])
        # Поставщика Helpdesk уже выбрал (заявка вернулась на доработку) — карта как у любого закупа.
        chosen = dict(row, counterparty_id=100)
        missing = wf.missing_for_submit(chosen, items=items('0'), offers=[], attachments=[], settings={}, card_number_ok=False)
        self.assertIn('Укажите полный номер карты', missing)
        self.assertNotIn(wf.IT_SUPPLIER_CARD, missing)
        # Обычный закуп с картой поставщика — по-старому.
        plain = dict(row, it_purchase=False, category_helpdesk=False)
        self.assertNotIn(wf.IT_SUPPLIER_CARD, wf.missing_for_submit(plain, items=items('1'), offers=[], attachments=[], settings={}))


class SubmitTests(unittest.TestCase):
    """Что инициатор IT-закупа заполняет сам: что и сколько; поставщиков и цены — нет."""

    def missing(self, row, **kwargs):
        return wf.missing_for_submit(row, items=kwargs.get('items', items('0')), offers=kwargs.get('offers', []),
                                     attachments=[], settings={})

    def test_it_purchase_without_suppliers_and_prices_can_be_sent(self):
        self.assertEqual(self.missing(purchase(submitted_at=None)), [])

    def test_the_same_draft_in_a_regular_category_needs_both(self):
        missing = self.missing(purchase(submitted_at=None, it_purchase=False, category_helpdesk=False))
        self.assertIn('Сумма заявки должна быть больше нуля', missing)
        self.assertTrue(any(text.startswith('Нужно не меньше 3 вариантов поставщиков') for text in missing), missing)

    def test_positions_are_still_required(self):
        missing = self.missing(purchase(submitted_at=None), items=[])
        self.assertEqual(missing, ['Добавьте хотя бы одну позицию: что и сколько'])


class ActionTests(unittest.TestCase):
    def missing(self, action, **fields):
        return wf.missing_for_action(wf.KIND_IT, action, request=purchase(), fields=fields, settings={})

    def test_actions_of_the_stage(self):
        self.assertEqual(wf.ACTIONS[wf.KIND_IT], ('it_offers', 'it_stock', 'return'))
        self.assertEqual(wf.ACTION_LABELS['it_offers'], 'Передать на утверждение')
        self.assertEqual(wf.ACTION_LABELS['it_stock'], 'Выдать из наличия')
        self.assertTrue(wf.can_move(wf.KIND_IT, 'in_progress'))
        self.assertFalse(wf.can_move(wf.KIND_IT, 'offers'), 'итоговые колонки — только действием')

    def test_offers_follow_the_initiators_rules(self):
        missing = self.missing('it_offers', items=items('450000'), offers=offers('450000'))
        self.assertTrue(any(text.startswith('Нужно не меньше 3 вариантов поставщиков') for text in missing), missing)
        self.assertIn('Обоснуйте выбор рекомендуемого поставщика', missing)
        self.assertIn('Добавьте хотя бы одну позицию', self.missing('it_offers', items=[], offers=[]))

    def test_prices_must_be_those_of_the_recommended_supplier(self):
        fields = dict(items=items('300000'), offers=offers('450000', '470000', '455000'), supplier_choice_reason='Дешевле',
                      counterparty_id=100)
        self.assertEqual(self.missing('it_offers', **fields), [
            'Сумма позиций — %s, а у рекомендуемого поставщика — %s: поставьте цены позиций по нему'
            % (wf.fmt_money(300000), wf.fmt_money(450000))])
        fields['items'] = items('400000', '50000')
        self.assertEqual(self.missing('it_offers', **fields), [])
        self.assertEqual(self.missing('it_offers', **dict(fields, items=items('0'))),
                         ['Поставьте цены позиций по рекомендуемому поставщику'])

    def test_single_supplier_with_a_reason(self):
        fields = dict(items=items('120000'), offers=offers('120000'), no_alternatives=True,
                      no_alternatives_reason='license_renewal', counterparty_id=100)
        self.assertEqual(self.missing('it_offers', **fields), [])
        self.assertEqual(self.missing('it_offers', **dict(fields, no_alternatives_reason=None)),
                         ['Выберите причину отсутствия альтернатив'])

    def test_issue_from_stock_needs_what_or_a_word(self):
        self.assertEqual(self.missing('it_stock'), ['Выберите, что выдаёте из наличия, или напишите об этом в комментарии'])
        self.assertEqual(self.missing('it_stock', comment='Выдал ноутбук со склада'), [])
        self.assertEqual(self.missing('it_stock', stock_assets=[{'id': 5}]), [])


class BoardTests(unittest.TestCase):
    def column(self, state, status=None, outcome=None, request_status='active'):
        step = {'kind': wf.KIND_IT, 'state': state, 'status': status, 'outcome': outcome}
        return wf.board_column(wf.BOARD_HELPDESK, {'status': request_status}, [step])

    def test_columns(self):
        self.assertEqual(self.column('open', 'new'), 'new')
        self.assertEqual(self.column('open', 'in_progress'), 'in_progress')
        self.assertEqual(self.column('open', 'odd'), 'new')
        self.assertEqual(self.column('waiting', 'clarification'), 'clarification')
        self.assertEqual(self.column('done', outcome=wf.IT_OUTCOME_OFFERS), 'offers')
        self.assertEqual(self.column('done', outcome=wf.IT_OUTCOME_STOCK, request_status='done'), 'stock')
        self.assertIsNone(self.column('pending'))
        self.assertIsNone(self.column('done', outcome=wf.IT_OUTCOME_OFFERS, request_status='rejected'))
        self.assertIsNone(self.column('open', 'new', request_status='cancelled'))
        self.assertIsNone(wf.board_column(wf.BOARD_HELPDESK, {'status': 'active'}, []))

    def test_the_board_belongs_to_the_role(self):
        board = wf.board(wf.BOARD_HELPDESK)
        self.assertEqual((board['kinds'], board['roles'], board['work']), ((wf.KIND_IT,), ('helpdesk',), ('new', 'in_progress')))
        self.assertEqual(wf.subtask(wf.KIND_IT)['board'], wf.BOARD_HELPDESK)


class IssuedFromStockTests(unittest.TestCase):
    def steps(self, outcome):
        return [{'kind': wf.KIND_INITIATION, 'stage': 'initiation', 'state': 'done'},
                {'kind': wf.KIND_MANAGER, 'stage': 'approval', 'state': 'done'},
                {'kind': wf.KIND_IT, 'stage': 'approval', 'state': 'done', 'outcome': outcome},
                {'kind': wf.KIND_APPROVAL, 'stage': 'approval', 'state': 'skipped'},
                {'kind': wf.KIND_INVOICE, 'stage': 'payment', 'state': 'skipped'},
                {'kind': wf.KIND_RECEIVING, 'stage': 'receiving', 'state': 'skipped'},
                {'kind': wf.KIND_CLOSING, 'stage': 'closing', 'state': 'skipped'}]

    def test_closing_conditions_of_a_request_issued_from_stock(self):
        self.assertTrue(wf.issued_from_stock(self.steps(wf.IT_OUTCOME_STOCK)))
        self.assertFalse(wf.issued_from_stock(self.steps(wf.IT_OUTCOME_OFFERS)))
        self.assertEqual(wf.closing_conditions(purchase(status='done'), self.steps(wf.IT_OUTCOME_STOCK)),
                         [{'label': 'Выдано из наличия', 'ok': True}])

    def test_lifecycle_shows_only_what_was_passed(self):
        stages = {item['code']: item['state'] for item in
                  wf.lifecycle(purchase(status='done', stage='closed'), self.steps(wf.IT_OUTCOME_STOCK))}
        self.assertEqual(stages, {'initiation': 'done', 'approval': 'done', 'payment': 'skipped', 'receiving': 'skipped',
                                  'assets': 'skipped', 'closing': 'skipped', 'closed': 'done'})


class AccessTests(unittest.TestCase):
    def ctx(self, user_id):
        return {'user_id': user_id}

    def test_board_and_scope(self):
        self.assertEqual(access.boards_for(self.ctx(HELPDESK['id']), {'helpdesk'}), ['helpdesk'])
        self.assertEqual(access.board_scope(self.ctx(HELPDESK['id']), 'helpdesk', {'helpdesk'}), 'all')

    def test_any_member_acts_on_the_stage(self):
        step = {'kind': wf.KIND_IT, 'role_code': 'helpdesk', 'assignee_id': None, 'state': 'open'}
        members = {'helpdesk': {HELPDESK['id'], 10}}
        self.assertTrue(access.can_act_on_subtask(self.ctx(HELPDESK['id']), step, members, purchase()))
        self.assertTrue(access.can_act_on_subtask(self.ctx(10), step, members, purchase()))
        self.assertFalse(access.can_act_on_subtask(self.ctx(MANAGER['id']), step, members, purchase()))
        # Свою IT-заявку участник Helpdesk проверяет сам: это не согласование, а подбор.
        self.assertTrue(access.can_act_on_subtask(self.ctx(INITIATOR['id']), step, {'helpdesk': {INITIATOR['id']}},
                                                  purchase()))

    def test_stock_is_open_to_helpdesk_but_not_the_registry(self):
        self.assertTrue(access.can_check_stock(self.ctx(HELPDESK['id']), {'helpdesk'}))
        self.assertFalse(access.can_view_assets(self.ctx(HELPDESK['id']), {'helpdesk'}))
        self.assertTrue(access.can_check_stock(self.ctx(5), {'asset_keeper'}))
        self.assertFalse(access.can_check_stock(self.ctx(INITIATOR['id']), set()))
        self.assertTrue(access.can_check_stock(self.ctx(ADMIN), set()))

    def test_assets_for_the_one_who_holds_the_step(self):
        """Проверку Helpdesk IT или утверждение администратор мог передать человеку не из роли."""
        self.assertFalse(access.sees_initiator_assets(self.ctx(41), purchase(), set()))
        self.assertTrue(access.sees_initiator_assets(self.ctx(41), purchase(), set(), participant=True))
        self.assertFalse(access.sees_initiator_assets(self.ctx(41), None, set(), participant=True))

    def test_who_sees_the_employees_assets(self):
        request = purchase()
        for user_id, roles in ((HELPDESK['id'], {'helpdesk'}), (30, {'approver'}), (5, {'asset_keeper'}),
                               (6, {'accounting'}), (INITIATOR['id'], set()), (MANAGER['id'], set()), (ADMIN, set())):
            self.assertTrue(access.sees_initiator_assets(self.ctx(user_id), request, roles), user_id)
        for user_id, roles in ((40, {'finance'}), (41, set())):
            self.assertFalse(access.sees_initiator_assets(self.ctx(user_id), request, roles), user_id)


class NotifyTests(unittest.TestCase):
    def test_no_zero_amount_before_helpdesk(self):
        self.assertEqual(notify.brief(purchase(amount=Decimal('0'))), '№812 · Ноутбук для работы')
        self.assertEqual(notify.brief(purchase(amount=Decimal('450000'))),
                         '№812 · Ноутбук для работы · %s' % wf.fmt_money(450000))
        self.assertNotIn('0 ₸', notify.task(purchase(), {'kind': wf.KIND_IT})['telegram'])
        self.assertEqual(notify.TASK_TITLES[wf.KIND_IT], 'Проверьте IT-закуп')

    def test_texts_of_the_outcomes(self):
        offered = notify.it_offers(purchase(amount=Decimal('450000'), counterparty_name='Sulpak'))
        self.assertEqual((offered['kind'], offered['title']), ('it_offers', 'Helpdesk IT подобрал поставщиков'))
        self.assertIn('Sulpak', offered['body'])
        issued = notify.issued_from_stock(purchase(status='done'), [{'name': 'Ноутбук HP'}])
        self.assertEqual((issued['kind'], issued['title']), ('it_stock', 'Выдано из наличия'))
        self.assertIn('Ноутбук HP', issued['body'])


class ReportTests(unittest.TestCase):
    def test_export_names_the_helpdesk_stage_and_the_stock_issue(self):
        active = report.enrich(purchase(state='active', stage='approval', current_kind=wf.KIND_IT, amount=Decimal('0')), [])
        self.assertEqual(active['stage_text'], 'Согласование — проверка Helpdesk IT')
        issued = report.enrich(purchase(state='done', status='done', stage='closed', issued_from_stock=True,
                                        amount=Decimal('450000')), [])
        self.assertEqual((issued['state_label'], issued['total_amount']), ('Выдано из наличия', Decimal('0')))
        bought = report.enrich(purchase(state='done', status='done', stage='closed', amount=Decimal('450000')), [])
        self.assertEqual((bought['state_label'], bought['total_amount']), ('Закрыта', Decimal('450000')))


class _FlowBase(unittest.TestCase):
    """flow.act на подменённых запросах: заявка в памяти, подзадачи — списком."""

    def setUp(self):
        self.row = purchase(submitted_at='2026-11-04T10:00:00', counterparty_id=None)
        self.row['submitted_snapshot'] = wf.essentials(self.row)
        self.steps = [
            dict(self.step(wf.KIND_MANAGER, MANAGER), state='done'),
            dict(self.step(wf.KIND_IT, None), state='open', status='in_progress', id=77),
            self.step(wf.KIND_APPROVAL, None),
            self.step(wf.KIND_INVOICE, None),
        ]
        queries = mock.MagicMock()
        queries.read_request.side_effect = lambda cursor, request_id, lock=False: dict(self.row)

        def update(cursor, request_id, fields):
            self.row.update(fields)
            return {}

        def replace(cursor, request_id, rows):
            self.row['amount'] = wf.items_total(rows)
            return self.row['amount']

        queries.update_request_fields.side_effect = update
        queries.replace_items.side_effect = replace
        queries.save_offers.return_value = [501, 502, 503]
        queries.read_subtask.side_effect = lambda cursor, request_id, kind: next(
            (dict(item) for item in self.steps if item['kind'] == kind), None)
        queries.list_subtasks.side_effect = lambda cursor, request_id: [dict(item) for item in self.steps]
        queries.list_attachments.return_value = []
        queries.list_events.return_value = [{'id': 1, 'kind': 'submitted', 'payload': {}}]
        queries.role_member_ids.return_value = {'approver': {30, 31}, 'accounting': {6}, 'asset_keeper': {5},
                                                'helpdesk': {HELPDESK['id']}}
        queries.user_brief.side_effect = lambda cursor, user_id: {'id': user_id, 'name': 'Утвердова Ульяна', 'fired': False}
        queries.today_almaty.return_value = date(2026, 11, 4)
        directory = mock.MagicMock()
        directory.read_counterparty.return_value = None
        directory.read_template.return_value = None
        directory.list_limits.return_value = [SMALL_LIMIT]
        directory.list_routes.return_value = []
        directory.list_legal_entities.return_value = [{'id': 2}]
        directory.get_settings.return_value = {}
        self.queries, self.directory = queries, directory
        self.notify_initiator = mock.MagicMock()
        self.advance = mock.MagicMock(return_value=wf.KIND_APPROVAL)
        for patcher in (mock.patch.object(flow, 'queries', queries),
                        mock.patch.object(flow, 'directory', directory),
                        mock.patch.object(flow, 'notices', mock.MagicMock()),
                        mock.patch.object(flow, '_notify_initiator', self.notify_initiator),
                        mock.patch.object(flow, 'advance', self.advance)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def step(kind, person):
        item = wf.SUBTASK_BY_KIND[kind]
        return {'id': 100 + item['position'], 'request_id': REQUEST_ID, 'kind': kind, 'position': item['position'],
                'stage': item['stage'], 'role_code': item['role'], 'assignee_id': (person or {}).get('id'),
                'assignee_name': (person or {}).get('name'), 'state': 'pending', 'status': None,
                'title': item['title'], 'role_label': wf.ROLE_LABELS[item['role']]}

    def act(self, action, **fields):
        return flow.act(mock.MagicMock(), REQUEST_ID, wf.KIND_IT, action, HELPDESK, fields=fields)


class OffersFlowTests(_FlowBase):
    def fields(self, amount='450000'):
        rows = offers(amount, '470000', '455000')
        return dict(items=items(amount), offers=rows, supplier_choice_reason='Дешевле и есть в Алматы',
                    counterparty_id=rows[0]['counterparty_id'], comment='Модель — ThinkPad E14')

    def test_suppliers_prices_and_a_new_route(self):
        result = self.act('it_offers', **self.fields())
        self.assertEqual(result, {'next': wf.KIND_APPROVAL, 'offer_ids': [501, 502, 503]})
        self.queries.save_offers.assert_called_once()
        self.assertEqual(self.row['amount'], Decimal('450000.00'), 'сумма заявки — из цен позиций')
        self.assertEqual(self.row['counterparty_id'], 100, 'поставщик заявки — рекомендуемый')
        self.assertEqual(self.row['supplier_choice_reason'], 'Дешевле и есть в Алматы')
        # Утверждающего выбрали уже по настоящей сумме: лимит «до 500 000 ₸ — Утвердова».
        basis = self.queries.set_route_basis.call_args.args[2]
        self.assertEqual((basis['approver_user_id'], basis['amount']), (30, '450000.00'))
        route = self.queries.write_route.call_args.args[2]
        self.assertEqual(next(entry for entry in route if entry['kind'] == wf.KIND_APPROVAL)['assignee_id'], 30)
        keep = self.queries.write_route.call_args.kwargs['keep']
        self.assertEqual(set(keep), {wf.KIND_IT, wf.KIND_MANAGER}, 'пройденные этапы остаются пройденными')
        complete = self.queries.complete_subtask.call_args
        self.assertEqual((complete.args[1], complete.kwargs['outcome'], complete.kwargs['comment']),
                         (77, wf.IT_OUTCOME_OFFERS, 'Модель — ThinkPad E14'))
        # Снимок условий — с новой суммой и поставщиком: ответ инициатора на будущий возврат
        # не сочтёт их «сменой условий».
        snapshot = self.queries.touch_snapshot.call_args.args[2]
        self.assertEqual((snapshot['amount'], snapshot['counterparty_id']), ('450000.00', 100))
        event = self.queries.log_event.call_args
        self.assertEqual(event.args[2], 'it_offers')
        self.assertEqual((event.kwargs['payload']['recommended'], event.kwargs['payload']['offers']), ('Поставщик 1', 3))
        self.assertEqual(self.notify_initiator.call_args.args[2]['kind'], 'it_offers')
        self.advance.assert_called_once()

    def test_changed_supplier_drops_the_old_contract(self):
        self.row.update(counterparty_id=55, contract_id=9, counterparty_account_id=4)
        self.act('it_offers', **self.fields())
        self.assertEqual((self.row['contract_id'], self.row['counterparty_account_id']), (None, None))

    def test_incomplete_offers_change_nothing(self):
        with self.assertRaises(flow.FlowError) as caught:
            self.act('it_offers', **dict(self.fields(), items=items('1')))
        self.assertEqual(caught.exception.code, 'PAYMENT_ACTION_INCOMPLETE')
        self.queries.save_offers.assert_not_called()
        self.queries.write_route.assert_not_called()

    def test_empty_helpdesk_role_is_named(self):
        gaps = flow.route_gaps([{'kind': wf.KIND_IT, 'role_code': 'helpdesk', 'state': 'pending'}], {}, INITIATOR['id'])
        self.assertEqual(gaps, ['Проверить IT-закуп некому: в роли «Helpdesk IT» нет участников'])
        # Этап, который пересборка не трогает (пройден или выполняется сейчас), опустевшая роль не держит.
        self.assertEqual(flow.route_gaps([{'kind': wf.KIND_IT, 'role_code': 'helpdesk', 'state': 'pending'}], {},
                                         INITIATOR['id'], settled=(wf.KIND_IT,)), [])

    def test_helpdesk_role_emptied_meanwhile_does_not_block_the_pass(self):
        """Администратор лично передал этап человеку не из роли, а роль опустела: «Передать на
        утверждение» всё равно проходит — этап Helpdesk уже не держит маршрут (разбор 04.11.2026)."""
        self.queries.role_member_ids.return_value = {'approver': {30, 31}, 'accounting': {6}, 'asset_keeper': {5}}
        self.steps[1].update(assignee_id=41, assignee_name='Лично назначенный')
        self.assertEqual(self.act('it_offers', **self.fields())['next'], wf.KIND_APPROVAL)

    def test_new_supplier_drops_the_supplier_card(self):
        self.queries.role_member_ids.return_value = dict(self.queries.role_member_ids.return_value, finance={9})
        self.row.update(payment_method='card', card_recipient='supplier', counterparty_id=55, card_id=12)
        self.act('it_offers', **self.fields())
        self.queries.set_card_number.assert_called_once_with(mock.ANY, REQUEST_ID, '')
        self.assertIsNone(self.row['card_id'])
        # Карта сотрудника от смены поставщика не зависит.
        self.queries.set_card_number.reset_mock()
        self.row.update(card_recipient='employee', counterparty_id=55, card_id=12)
        self.act('it_offers', **self.fields())
        self.queries.set_card_number.assert_not_called()
        self.assertEqual(self.row['card_id'], 12)


class StockFlowTests(_FlowBase):
    def test_issued_from_stock_closes_the_request(self):
        issued = [{'id': 5, 'name': 'Ноутбук HP ProBook', 'inventory_number': 'IT-0042'}]
        result = self.act('it_stock', stock_assets=issued, comment='Забрать в 305 кабинете')
        self.assertEqual(result, {'next': None})
        complete = self.queries.complete_subtask.call_args
        self.assertEqual((complete.args[1], complete.kwargs['outcome']), (77, wf.IT_OUTCOME_STOCK))
        self.queries.skip_pending.assert_called_once_with(mock.ANY, REQUEST_ID, 'Не потребовалось: выдано из наличия')
        self.assertEqual(self.queries.close_request.call_args.args[1:3], (REQUEST_ID, 'done'))
        event = next(call for call in self.queries.log_event.call_args_list if call.args[2] == 'it_stock')
        self.assertEqual(event.kwargs['payload']['assets'], issued)
        self.assertEqual(self.notify_initiator.call_args.args[2]['kind'], 'it_stock')
        self.advance.assert_not_called()
        self.queries.write_route.assert_not_called()


class WiringTests(unittest.TestCase):
    """Ручки: /act (разбор поставщиков и порядок файлов), /stock, /route-preview, карточка."""

    def client(self, user_id=ADMIN, roles=()):
        self.queries = mock.MagicMock()
        self.queries.load_access_context.return_value = {'user_id': user_id, 'name': 'Кто-то', 'role': 'admin'}
        self.queries.roles_of_user.return_value = set(roles)
        self.directory = mock.MagicMock()
        self.directory.get_settings.return_value = {}
        self.assets_sql = mock.MagicMock()
        self.queries.resolve_manager.return_value = None
        self.queries.today_almaty.return_value = date(2026, 11, 4)
        patches = [mock.patch.object(routes, 'queries', self.queries),
                   mock.patch.object(routes, 'directory', self.directory),
                   mock.patch.object(routes_directory, 'directory', self.directory),
                   mock.patch.object(routes, 'assets_sql', self.assets_sql),
                   mock.patch.object(routes, 'notices', mock.MagicMock())]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.build_payments_blueprint(
            db=mock.MagicMock(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204), resolve_requester=lambda: (user_id, None, None)))
        return app.test_client()

    def test_stock_answers_helpdesk_only(self):
        client = self.client(user_id=HELPDESK['id'], roles={'helpdesk'})
        with mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS', (HELPDESK['id'], 41)):
            self.assets_sql.list_assets.return_value = (1, [{'id': 5, 'name': 'Ноутбук HP', 'inventory_number': 'IT-1',
                                                             'cost': Decimal('1'), 'responsible_name': 'Склад'}])
            # Уже выдан по другой заявке, а по акту не передан — склад называет заявку.
            self.assets_sql.issued_from_stock.return_value = {5: {'request_id': 610}}
            response = client.get('/api/payments/stock?q=ноут')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(self.assets_sql.list_assets.call_args.kwargs,
                             {'limit': routes.STOCK_PAGE_MAX, 'status': 'in_stock', 'query': 'ноут'})
            item = response.get_json()['items'][0]
            self.assertNotIn('cost', item, 'стоимость Helpdesk не нужна')
            self.assertEqual((item['name'], item['issued_request_id']), ('Ноутбук HP', 610))
            self.assets_sql.issued_from_stock.assert_called_once_with(mock.ANY, [5])
            other = self.client(user_id=41, roles=set())
            self.assertEqual(other.get('/api/payments/stock').status_code, 403)
            # Проверку этой заявки администратор передал ему лично — склад открыт, но только по ней.
            self.queries.role_member_ids.return_value = {}
            self.queries.read_subtask.return_value = {'kind': wf.KIND_IT, 'role_code': 'helpdesk', 'assignee_id': 41,
                                                      'state': 'open'}
            self.assets_sql.list_assets.return_value = (0, [])
            self.assets_sql.issued_from_stock.return_value = {}
            self.assertEqual(other.get('/api/payments/stock?request=%s' % REQUEST_ID).status_code, 200)
            self.queries.read_subtask.assert_called_with(mock.ANY, REQUEST_ID, wf.KIND_IT)
            self.queries.read_subtask.return_value = dict(self.queries.read_subtask.return_value, state='done')
            self.assertEqual(other.get('/api/payments/stock?request=%s' % REQUEST_ID).status_code, 403)

    def test_category_editor_passes_the_helpdesk_flag(self):
        """Справочник «Категории закупа»: отметку «Helpdesk IT» прислали — записываем; не прислали
        (форма прежней сборки во время выкладки) — None, и отметка у категории остаётся как была."""
        client = self.client()
        self.directory.find_duplicate_name.return_value = None
        self.directory.upsert_category.return_value = 2
        self.assertEqual(client.post('/api/payments/dictionaries/categories',
                                     json={'id': 2, 'name': 'IT и связь', 'via_helpdesk': True}).status_code, 200)
        self.assertIs(self.directory.upsert_category.call_args.kwargs['via_helpdesk'], True)
        client.post('/api/payments/dictionaries/categories', json={'id': 2, 'name': 'IT и связь', 'via_helpdesk': False})
        self.assertIs(self.directory.upsert_category.call_args.kwargs['via_helpdesk'], False)
        client.post('/api/payments/dictionaries/categories', json={'id': 2, 'name': 'IT и связь'})
        self.assertIsNone(self.directory.upsert_category.call_args.kwargs['via_helpdesk'])

    def test_route_preview_names_the_helpdesk_step(self):
        client = self.client()
        self.directory.category_helpdesk.return_value = True
        self.directory.read_counterparty.return_value = None
        self.directory.read_contract.return_value = None
        with mock.patch.object(flow, 'approval_basis', return_value={'trace': []}):
            body = client.get('/api/payments/route-preview?amount=0&category_id=3&request_kind=purchase').get_json()
        self.assertTrue(body['it_step'])
        self.directory.category_helpdesk.assert_called_once_with(mock.ANY, 3)

    def card_answers(self, row):
        """Что нужно ручке, чтобы собрать карточку заявки в ответ (_full)."""
        self.queries.read_request.return_value = row
        self.queries.list_subtasks.return_value = []
        for name in ('list_attachments', 'list_items', 'list_offers', 'list_events', 'payment_history',
                     'find_duplicates', 'list_observers'):
            getattr(self.queries, name).return_value = []
        for name in ('read_contract', 'read_legal_entity', 'read_account'):
            getattr(self.directory, name).return_value = None
        self.assets_sql.for_request.return_value = []
        self.assets_sql.of_user.return_value = []

    def test_offers_are_parsed_and_their_files_wait_for_the_offer_ids(self):
        client = self.client()
        step = dict(_FlowBase.step(wf.KIND_IT, None), state='open')
        order = []
        self.card_answers(purchase())
        self.queries.role_member_ids.return_value = {}
        self.directory.read_counterparty.side_effect = lambda cursor, cid: {'id': cid, 'name': 'Sulpak'}
        with mock.patch.object(flow, 'load_for_action', return_value=(purchase(), step)), \
                mock.patch.object(flow, 'act', side_effect=lambda *a, **k: order.append(('act', k['fields'])) or
                                  {'next': 'approval', 'offer_ids': [601]}), \
                mock.patch.object(flow, 'submit_problems', return_value=[]), \
                mock.patch.object(routes.files, 'drop'), \
                mock.patch.object(routes.files, 'upload', side_effect=lambda gcs, **kw: order.append('upload') or ('b', 'p')):
            from io import BytesIO
            import json
            payload = {'kind': 'it_review', 'action': 'it_offers', 'no_alternatives': True,
                       'no_alternatives_reason': 'single_supplier',
                       'items': [{'name': 'Ноутбук', 'quantity': 1, 'unit_price': 450000}],
                       'offers': [{'counterparty_id': 100, 'amount': 450000, 'is_recommended': True}]}
            response = client.post('/api/payments/requests/%s/act' % REQUEST_ID, data={
                'payload': json.dumps(payload), 'kinds': json.dumps(['offer']), 'offers': json.dumps([0]),
                'files': (BytesIO(b'%PDF-1.4'), 'kp.pdf')}, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(order[0][0], 'act', 'КП сохраняются после того, как у вариантов появились номера')
        self.assertIn('upload', order)
        fields = order[0][1]
        self.assertEqual((fields['counterparty_id'], fields['no_alternatives'], fields['no_alternatives_reason']),
                         (100, True, 'single_supplier'))
        self.assertEqual(fields['items'][0]['unit_price'], Decimal('450000.00'))
        attachment = self.queries.add_attachment.call_args.kwargs
        self.assertEqual((attachment['kind'], attachment['offer_id'], attachment['subtask_kind']), ('offer', 601, 'it_review'))

    def act_request(self, client, payload):
        import json
        return client.post('/api/payments/requests/%s/act' % REQUEST_ID, data={'payload': json.dumps(payload)},
                           content_type='multipart/form-data')

    def acting(self):
        """Этап Helpdesk IT открыт, ход заявки подменён — проверяется разбор ручки и её отказы."""
        client = self.client()
        self.card_answers(purchase())
        self.queries.role_member_ids.return_value = {}
        self.acted = []
        step = dict(_FlowBase.step(wf.KIND_IT, None), state='open')
        for patcher in (mock.patch.object(flow, 'load_for_action', return_value=(purchase(), step)),
                        mock.patch.object(flow, 'act', side_effect=lambda *a, **k: self.acted.append(k['fields']) or
                                          {'next': None, 'offer_ids': []}),
                        mock.patch.object(flow, 'submit_problems', return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        return client

    def test_stock_already_issued_by_another_request_is_refused(self):
        client = self.acting()
        self.assets_sql.read_asset.side_effect = lambda cursor, asset_id, lock=False: {
            'id': asset_id, 'name': 'Ноутбук HP', 'inventory_number': 'IT-%s' % asset_id, 'status': 'in_stock'}
        self.assets_sql.issued_from_stock.return_value = {5: {'request_id': 610}}
        response = self.act_request(client, {'kind': 'it_review', 'action': 'it_stock', 'stock_asset_ids': [5]})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['code'], 'PAYMENT_STOCK_GONE')
        self.assertIn('уже выдан по заявке №610', response.get_json()['error'])
        self.assertEqual(self.acted, [], 'до хода заявки дело не дошло')
        # Под блокировкой: две одновременные выдачи одной вещи не пройдут обе.
        self.assertTrue(all(call.kwargs.get('lock') for call in self.assets_sql.read_asset.call_args_list))
        # Свободная вещь — проходит, повтор в списке не удваивает выдачу.
        self.assets_sql.issued_from_stock.return_value = {}
        response = self.act_request(client, {'kind': 'it_review', 'action': 'it_stock', 'stock_asset_ids': [7, 5, 7]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual([item['id'] for item in self.acted[0]['stock_assets']], [7, 5])
        # Не на складе — тоже отказ.
        self.assets_sql.read_asset.side_effect = lambda cursor, asset_id, lock=False: {
            'id': asset_id, 'name': 'Ноутбук HP', 'inventory_number': 'IT-1', 'status': 'in_use'}
        self.assertEqual(self.act_request(client, {'kind': 'it_review', 'action': 'it_stock',
                                                   'stock_asset_ids': [5]}).status_code, 409)

    def test_three_suppliers_with_a_reason_pass_the_handler(self):
        """Главный путь R3 — через ручку и настоящие правила missing_for_action, не в обход разбора."""
        client = self.acting()
        self.directory.read_counterparty.side_effect = lambda cursor, cid: {'id': cid, 'name': 'Поставщик %s' % cid}
        payload = {'kind': 'it_review', 'action': 'it_offers', 'no_alternatives': False,
                   'supplier_choice_reason': 'Дешевле и доставка завтра',
                   'items': [{'name': 'Ноутбук', 'quantity': 1, 'unit_price': 450000}],
                   'offers': [{'counterparty_id': 100, 'amount': 450000, 'is_recommended': True},
                              {'counterparty_id': 101, 'amount': 470000}, {'counterparty_id': 102, 'amount': 455000}]}
        response = self.act_request(client, payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fields = self.acted[0]
        self.assertEqual((fields['supplier_choice_reason'], fields['counterparty_id'], fields['no_alternatives'], len(fields['offers'])),
                         ('Дешевле и доставка завтра', 100, False, 3))
        response = self.act_request(client, dict(payload, supplier_choice_reason=''))
        self.assertEqual(response.status_code, 400)
        self.assertIn('Обоснуйте выбор рекомендуемого поставщика', response.get_json()['missing'])

    def test_card_shows_the_employees_assets_to_helpdesk(self):
        client = self.client(user_id=HELPDESK['id'], roles={'helpdesk'})
        with mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS', (HELPDESK['id'],)):
            self.card_answers(purchase())
            self.queries.participates.return_value = True
            self.queries.role_member_ids.return_value = {'helpdesk': {HELPDESK['id']}}
            self.assets_sql.of_user.return_value = [{'id': 1, 'name': 'Монитор Dell', 'inventory_number': 'IT-7',
                                                     'cost': Decimal('90000'), 'status': 'in_use'}]
            with mock.patch.object(flow, 'submit_problems', return_value=[]):
                body = client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()
            self.assertEqual([item['name'] for item in body['initiator_assets']], ['Монитор Dell'])
            self.assertNotIn('cost', body['initiator_assets'][0])
            self.assets_sql.of_user.assert_called_once_with(mock.ANY, INITIATOR['id'])
            # Не IT-закуп — имущество сотрудника в окне не нужно.
            self.queries.read_request.return_value = purchase(it_purchase=False)
            with mock.patch.object(flow, 'submit_problems', return_value=[]):
                self.assertIsNone(client.get('/api/payments/requests/%s' % REQUEST_ID).get_json()['initiator_assets'])


if __name__ == '__main__':
    unittest.main()
