# -*- coding: utf-8 -*-
"""Периметр раздела «Оплата счетов», права ролей процесса и проводка во фронте.

Два слоя, и у каждого своя цена ошибки.

1. Кому открыт раздел. Владелец сказал: «раздел доступный пока что только мне»;
   02.10.2026 к пилоту добавлен соисполнитель. Бэкенд пускает только id из
   SECTION_ALLOWED_USER_IDS — роль не помогает, даже super_admin, — а фронт
   держит ТОТ ЖЕ список: иначе раздел показывается одному, а пускает другого.

2. Что человек видит и делает внутри (ТЗ #381, пп. 3, 9, 16): подзадачу
   подразделения выполняет любой его участник, личную — только её исполнитель;
   согласующий видит свои согласования, а не чужие; полный номер карты — только
   у финансового отдела; справочник карт ведёт он же.

Рядовых участников в периметре на проде пока нет, поэтому тесты расширяют
список на время проверки: правила должны быть верны к моменту, когда раздел
откроют шире.
"""

import unittest
from pathlib import Path
from unittest import mock

from payments import access, privacy, workflow

ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / 'src' / 'App.jsx'

ADMIN = access.SECTION_ADMIN_USER_IDS[0]
INITIATOR, MANAGER, APPROVER, ACCOUNTANT, FINANCIER, KEEPER, OUTSIDER = 10, 11, 40, 50, 60, 70, 999
MEMBERS = {'approver': {APPROVER, 41}, 'accounting': {ACCOUNTANT}, 'finance': {FINANCIER},
           'asset_keeper': {KEEPER}}


def ctx(user_id, role='operator'):
    return {'user_id': user_id, 'name': 'Тест', 'role': role, 'department_code': 'szov'}


class WiderPerimeter(unittest.TestCase):
    """Раздел открыт не только администраторам — как будет после пилота."""

    def setUp(self):
        people = (INITIATOR, MANAGER, APPROVER, 41, ACCOUNTANT, FINANCIER, KEEPER)
        patcher = mock.patch.object(access, 'SECTION_ALLOWED_USER_IDS',
                                    tuple(access.SECTION_ALLOWED_USER_IDS) + people)
        patcher.start()
        self.addCleanup(patcher.stop)


class SectionGateTests(unittest.TestCase):
    def test_only_listed_ids_get_in(self):
        for user_id in access.SECTION_ALLOWED_USER_IDS:
            self.assertTrue(access.can_open_section(ctx(user_id, role='operator')))
        self.assertFalse(access.can_open_section(ctx(OUTSIDER, role='super_admin')),
                         'роль super_admin сама по себе не открывает раздел')
        self.assertFalse(access.can_open_section(ctx(None)))
        self.assertFalse(access.can_open_section({'user_id': 'abc'}))

    def test_string_id_from_json_is_accepted(self):
        user_id = access.SECTION_ALLOWED_USER_IDS[0]
        self.assertTrue(access.can_open_section({'user_id': str(user_id)}))

    def test_pilot_perimeter_is_owner_and_coexecutor(self):
        self.assertEqual(tuple(access.SECTION_ALLOWED_USER_IDS), (2, 448))
        self.assertEqual(tuple(access.SECTION_ADMIN_USER_IDS), (2, 448))

    def test_role_member_outside_the_perimeter_is_marked(self):
        """Роль в «Участниках» раздел не открывает — администратор должен это видеть."""
        members = {'accounting': [{'user_id': ADMIN, 'name': 'А'}, {'user_id': OUTSIDER, 'name': 'Б'}]}
        marked = access.mark_section_access(members)['accounting']
        self.assertEqual([person['section_open'] for person in marked], [True, False])
        self.assertEqual(access.mark_section_access({}), {})


class SubtaskPermissionTests(WiderPerimeter):
    def task(self, role, assignee=None, state='open', kind=None):
        return {'role_code': role, 'assignee_id': assignee, 'state': state, 'kind': kind}

    def test_personal_task_belongs_to_its_assignee(self):
        task = self.task('manager', MANAGER)
        self.assertTrue(access.can_act_on_subtask(ctx(MANAGER), task, MEMBERS))
        self.assertFalse(access.can_act_on_subtask(ctx(INITIATOR), task, MEMBERS))
        self.assertFalse(access.can_act_on_subtask(ctx(APPROVER), task, MEMBERS))

    def test_department_task_is_open_to_every_member(self):
        """П. 9: исполнитель оплаты — подразделение; задачу берёт любой его участник."""
        approval = self.task('approver')
        self.assertTrue(access.can_act_on_subtask(ctx(APPROVER), approval, MEMBERS))
        self.assertTrue(access.can_act_on_subtask(ctx(41), approval, MEMBERS))
        self.assertFalse(access.can_act_on_subtask(ctx(ACCOUNTANT), approval, MEMBERS), 'бухгалтер не утверждает')
        payment = self.task('accounting')
        self.assertTrue(access.can_act_on_subtask(ctx(ACCOUNTANT), payment, MEMBERS))
        self.assertFalse(access.can_act_on_subtask(ctx(FINANCIER), payment, MEMBERS),
                         'финансовый отдел счета не оплачивает')
        self.assertFalse(access.can_act_on_subtask(ctx(APPROVER), self.task('finance'), MEMBERS),
                         'утверждающий карты не пополняет')

    def test_named_approver_replaces_the_role(self):
        """Матрица назвала согласующего — остальные утверждающие заявку не берут."""
        task = self.task('approver', APPROVER)
        self.assertTrue(access.can_act_on_subtask(ctx(APPROVER), task, MEMBERS))
        self.assertFalse(access.can_act_on_subtask(ctx(41), task, MEMBERS))

    def test_only_an_open_task_can_be_acted_on(self):
        for state in ('pending', 'waiting', 'done', 'skipped'):
            self.assertFalse(access.can_act_on_subtask(ctx(ACCOUNTANT), self.task('accounting', state=state), MEMBERS),
                             state)
            self.assertFalse(access.can_act_on_subtask(ctx(ADMIN), self.task('accounting', state=state), MEMBERS),
                             'администратор тоже не действует по задаче, которая ждёт ответа')

    def test_section_admin_acts_for_anyone_and_it_is_visible(self):
        task = self.task('accounting')
        self.assertTrue(access.can_act_on_subtask(ctx(ADMIN), task, {}))
        self.assertFalse(access.acts_directly(ctx(ADMIN), task, MEMBERS), 'в карточке видно: действует как админ')
        self.assertTrue(access.acts_directly(ctx(ACCOUNTANT), task, MEMBERS))

    def test_nobody_approves_their_own_request(self):
        """Утверждающий, подавший заявку сам, её не утверждает — она уходит остальным."""
        own = {'initiator_id': APPROVER}
        whole_role = self.task('approver', kind='approval')
        self.assertFalse(access.can_act_on_subtask(ctx(APPROVER), whole_role, MEMBERS, own))
        self.assertTrue(access.can_act_on_subtask(ctx(41), whole_role, MEMBERS, own), 'другой утверждающий — да')
        named = self.task('approver', APPROVER, kind='approval')
        self.assertFalse(access.can_act_on_subtask(ctx(APPROVER), named, MEMBERS, own),
                         'даже если матрица назвала согласующим его самого')
        self_managed = self.task('manager', MANAGER, kind='manager_approval')
        self.assertFalse(access.can_act_on_subtask(ctx(MANAGER), self_managed, MEMBERS, {'initiator_id': MANAGER}))
        self.assertTrue(access.can_act_on_subtask(ctx(MANAGER), self_managed, MEMBERS, {'initiator_id': INITIATOR}))
        self.assertTrue(access.can_act_on_subtask(ctx(ADMIN), whole_role, MEMBERS, {'initiator_id': ADMIN}),
                        'на пилоте администратор проходит маршрут за все роли')

    def test_own_request_rule_is_only_about_approvals(self):
        own = {'initiator_id': INITIATOR}
        receiving = self.task('initiator', INITIATOR, kind='receiving')
        self.assertTrue(access.can_act_on_subtask(ctx(INITIATOR), receiving, MEMBERS, own),
                        'получение своей заявки подтверждает сам инициатор')
        payment = self.task('accounting', kind='invoice_payment')
        self.assertTrue(access.can_act_on_subtask(ctx(ACCOUNTANT), payment, MEMBERS, {'initiator_id': ACCOUNTANT}),
                        'бухгалтер оплачивает и свою заявку: её уже согласовал другой')
        self.assertEqual(access.APPROVAL_KINDS, workflow.APPROVAL_KINDS)


class RequestRightsTests(WiderPerimeter):
    def request(self, **overrides):
        row = {'status': 'active', 'stage': 'initiation', 'initiator_id': INITIATOR, 'manager_id': MANAGER,
               'paid_on': None}
        row.update(overrides)
        return row

    def test_request_is_edited_only_while_it_is_with_the_initiator(self):
        """Согласованную сумму нельзя поправить после согласования: правка — только на возврате."""
        self.assertTrue(access.can_edit_request(ctx(INITIATOR), self.request()))
        self.assertTrue(access.can_edit_request(ctx(ADMIN), self.request()))
        self.assertFalse(access.can_edit_request(ctx(MANAGER), self.request()), 'чужую заявку не правят')
        for stage in ('approval', 'payment', 'receiving', 'closing'):
            self.assertFalse(access.can_edit_request(ctx(INITIATOR), self.request(stage=stage)), stage)
            self.assertFalse(access.can_edit_request(ctx(ADMIN), self.request(stage=stage)), stage)
        self.assertFalse(access.can_edit_request(ctx(INITIATOR), self.request(status='done')))

    def test_cancel_until_paid(self):
        on_approval = self.request(stage='approval', current_role_code='approver')
        self.assertTrue(access.can_cancel_request(ctx(INITIATOR), on_approval))
        self.assertTrue(access.can_cancel_request(ctx(ADMIN), on_approval))
        self.assertFalse(access.can_cancel_request(ctx(APPROVER), on_approval))
        paid = self.request(stage='receiving', paid_on='2026-10-08')
        self.assertFalse(access.can_cancel_request(ctx(INITIATOR), paid))
        self.assertFalse(access.can_cancel_request(ctx(ADMIN), paid), 'оплаченную не отменяют даже админу')

    def test_initiator_does_not_cancel_while_the_department_is_paying(self):
        """Платёж в банке мог уйти раньше, чем нажато «Оплачено»: отмена в этот
        момент оставила бы оплату без заявки."""
        for role in ('accounting', 'finance'):
            in_payment = self.request(stage='payment', current_role_code=role)
            self.assertFalse(access.can_cancel_request(ctx(INITIATOR), in_payment), role)
            self.assertTrue(access.can_cancel_request(ctx(ADMIN), in_payment), 'отменяет администратор')
        asked_back = self.request(stage='initiation', current_role_code='initiator')
        self.assertTrue(access.can_cancel_request(ctx(INITIATOR), asked_back),
                        'заявку вернули за счётом — она снова у инициатора')

    def test_full_registry_is_for_accounting_and_admins(self):
        """П. 16: каждый видит то, что требует его действия; реестр целиком нужен бухгалтерии."""
        self.assertTrue(access.sees_all_requests(ctx(ADMIN)))
        self.assertTrue(access.sees_all_requests(ctx(ACCOUNTANT), {'accounting'}))
        for user_id, roles in ((APPROVER, {'approver'}), (FINANCIER, {'finance'}), (KEEPER, {'asset_keeper'}),
                               (INITIATOR, set())):
            self.assertFalse(access.sees_all_requests(ctx(user_id), roles), roles)

    def test_request_is_visible_to_its_people_and_participants(self):
        request = self.request()
        self.assertTrue(access.can_see_request(ctx(INITIATOR), request))
        self.assertTrue(access.can_see_request(ctx(MANAGER), request))
        self.assertFalse(access.can_see_request(ctx(FINANCIER), request, {'finance'}))
        self.assertTrue(access.can_see_request(ctx(FINANCIER), request, {'finance'}, participant=True))
        self.assertTrue(access.can_see_request(ctx(ACCOUNTANT), request, {'accounting'}))
        self.assertFalse(access.can_see_request(ctx(ACCOUNTANT), None, {'accounting'}))

    def test_refund_and_attachments(self):
        paid = self.request(stage='receiving', paid_on='2026-10-08')
        self.assertTrue(access.can_refund(ctx(ACCOUNTANT), paid, {'accounting'}))
        self.assertFalse(access.can_refund(ctx(INITIATOR), paid))
        self.assertFalse(access.can_refund(ctx(ACCOUNTANT), self.request(), {'accounting'}), 'возврат — по оплаченной')
        self.assertTrue(access.can_attach(ctx(INITIATOR), paid))
        self.assertTrue(access.can_attach(ctx(KEEPER), paid, {'asset_keeper'}, can_act=True))
        self.assertFalse(access.can_attach(ctx(KEEPER), paid, {'asset_keeper'}, can_act=False))
        self.assertFalse(access.can_attach(ctx(INITIATOR), self.request(status='done')))


class BoardTests(WiderPerimeter):
    def test_everyone_sees_only_their_board(self):
        self.assertEqual(access.boards_for(ctx(ADMIN)), ['approval', 'accounting', 'finance'])
        self.assertEqual(access.boards_for(ctx(APPROVER), {'approver'}), ['approval'])
        self.assertEqual(access.boards_for(ctx(ACCOUNTANT), {'accounting'}), ['accounting'])
        self.assertEqual(access.boards_for(ctx(FINANCIER), {'finance'}), ['finance'])
        self.assertEqual(access.boards_for(ctx(KEEPER), {'asset_keeper'}), [])
        self.assertEqual(access.boards_for(ctx(INITIATOR)), [])
        # Руководитель согласует закупы подчинённых — ему нужна доска согласования.
        self.assertEqual(access.boards_for(ctx(MANAGER), is_manager=True), ['approval'])
        self.assertFalse(access.can_open_board(ctx(FINANCIER), 'accounting', {'finance'}))

    def test_department_boards_show_all_tasks_approval_board_only_own(self):
        self.assertEqual(access.board_scope(ctx(ACCOUNTANT), 'accounting', {'accounting'}), 'all')
        self.assertEqual(access.board_scope(ctx(FINANCIER), 'finance', {'finance'}), 'all')
        self.assertEqual(access.board_scope(ctx(APPROVER), 'approval', {'approver'}), 'own')
        self.assertEqual(access.board_scope(ctx(ADMIN), 'approval'), 'all')


class CardNumberTests(WiderPerimeter):
    """П. 5.2: полный номер карты — финансовому отделу и по правам доступа."""

    def test_full_number_for_finance_admin_and_the_initiator_who_typed_it(self):
        request = {'initiator_id': INITIATOR, 'card_id': None}
        self.assertTrue(access.can_view_card_number(ctx(FINANCIER), request, {'finance'}))
        self.assertTrue(access.can_view_card_number(ctx(ADMIN), request))
        self.assertTrue(access.can_view_card_number(ctx(INITIATOR), request))
        from_directory = {'initiator_id': INITIATOR, 'card_id': 7}
        self.assertFalse(access.can_view_card_number(ctx(INITIATOR), from_directory),
                         'карту из справочника вводил не он: чужой номер через свой черновик не читается')
        self.assertTrue(access.can_view_card_number(ctx(FINANCIER), from_directory, {'finance'}))
        for user_id, roles in ((APPROVER, {'approver'}), (ACCOUNTANT, {'accounting'}), (MANAGER, set()),
                               (KEEPER, {'asset_keeper'})):
            self.assertFalse(access.can_view_card_number(ctx(user_id), request, roles), roles)

    def test_cards_directory_is_kept_by_finance(self):
        self.assertTrue(access.can_manage_cards(ctx(FINANCIER), {'finance'}))
        self.assertFalse(access.can_manage_cards(ctx(ACCOUNTANT), {'accounting'}))
        self.assertTrue(access.can_view_dictionary(ctx(FINANCIER), 'cards', {'finance'}))
        self.assertFalse(access.can_view_dictionary(ctx(ACCOUNTANT), 'cards', {'accounting'}))
        self.assertTrue(access.can_view_dictionary(ctx(ACCOUNTANT), 'contracts', {'accounting'}))


class DictionaryRightsTests(WiderPerimeter):
    DICTIONARIES = ('projects', 'categories', 'asset_categories', 'legal_entities', 'counterparties',
                    'counterparty_accounts', 'contracts', 'cards', 'limits', 'routes')

    def test_admin_saves_any_dictionary(self):
        for name in self.DICTIONARIES:
            self.assertTrue(access.can_save_dictionary(ctx(ADMIN), name))
            self.assertTrue(access.can_save_dictionary(ctx(ADMIN), name, row_id=5))

    def test_initiator_writes_no_dictionary(self):
        """Нового поставщика он заводит названием в заявке; карточку целиком — нет:
        в ней согласующий поставщика, и человек назначил бы согласующим себя."""
        for name in self.DICTIONARIES:
            self.assertFalse(access.can_save_dictionary(ctx(INITIATOR), name), name)
            self.assertFalse(access.can_save_dictionary(ctx(INITIATOR), name, row_id=5), name)

    def test_finance_keeps_cards_and_the_asset_keeper_keeps_asset_categories(self):
        self.assertTrue(access.can_save_dictionary(ctx(FINANCIER), 'cards', 5, {'finance'}))
        self.assertFalse(access.can_save_dictionary(ctx(FINANCIER), 'limits', None, {'finance'}))
        self.assertTrue(access.can_save_dictionary(ctx(KEEPER), 'asset_categories', None, {'asset_keeper'}))
        self.assertFalse(access.can_save_dictionary(ctx(KEEPER), 'cards', None, {'asset_keeper'}))
        self.assertFalse(access.can_save_dictionary(ctx(APPROVER), 'limits', None, {'approver'}),
                         'утверждающий сам себе лимит не назначает')

    def test_outsider_adds_nothing(self):
        self.assertFalse(access.can_save_dictionary(ctx(OUTSIDER), 'counterparties'))

    def test_assets_registry(self):
        self.assertTrue(access.can_manage_assets(ctx(KEEPER), {'asset_keeper'}))
        self.assertTrue(access.can_view_assets(ctx(ACCOUNTANT), {'accounting'}))
        self.assertFalse(access.can_manage_assets(ctx(ACCOUNTANT), {'accounting'}))
        self.assertFalse(access.can_view_assets(ctx(INITIATOR)))


class DepartmentPrivacyTests(WiderPerimeter):
    """П. 9: «инициатор не должен знать, кто именно внутри бухгалтерии или финансового
    отдела выполняет платёж» — ему показывается подразделение, а не фамилия."""

    def card(self):
        return {
            'request': {'id': 1, 'initiator_id': INITIATOR, 'manager_id': MANAGER, 'clarify_from': 'invoice_payment',
                        'clarify_by_name': 'Бухгалтер Б.', 'current_role_code': 'accounting',
                        'current_assignee_id': ACCOUNTANT, 'current_assignee_name': 'Бухгалтер Б.'},
            'subtasks': [
                # Инициатор уже ответил (outcome — 'submitted'), а кто спрашивал — подзадача помнит.
                {'kind': 'initiation', 'outcome': 'submitted', 'clarify_reason': 'requisites',
                 'clarify_by': ACCOUNTANT, 'clarify_by_name': 'Бухгалтер Б.', 'assignee_id': INITIATOR,
                 'assignee_name': 'Инициатор И.'},
                {'kind': 'manager_approval', 'assignee_id': MANAGER, 'assignee_name': 'Руководитель Р.',
                 'done_by': MANAGER, 'done_by_name': 'Руководитель Р.'},
                {'kind': 'approval', 'assignee_id': None, 'done_by': APPROVER, 'done_by_name': 'Утверждающий У.'},
                {'kind': 'invoice_payment', 'assignee_id': ACCOUNTANT, 'assignee_name': 'Бухгалтер Б.',
                 'done_by': ACCOUNTANT, 'done_by_name': 'Бухгалтер Б.', 'clarify_by': ACCOUNTANT,
                 'clarify_by_name': 'Бухгалтер Б.'},
                {'kind': 'card_topup', 'assignee_id': None, 'done_by': FINANCIER, 'done_by_name': 'Финансист Ф.'},
            ],
            'events': [
                {'kind': 'created', 'actor_id': INITIATOR, 'actor_name': 'Инициатор И.', 'payload': {}},
                {'kind': 'approved', 'actor_id': APPROVER, 'actor_name': 'Утверждающий У.', 'payload': {'kind': 'approval'}},
                {'kind': 'clarification', 'actor_id': None, 'actor_name': 'iCore',
                 'payload': {'kind': 'invoice_payment', 'system': True}},
                {'kind': 'clarified', 'actor_id': INITIATOR, 'actor_name': 'Инициатор И.',
                 'payload': {'kind': 'invoice_payment'}},
                {'kind': 'subtask_moved', 'actor_id': ACCOUNTANT, 'actor_name': 'Бухгалтер Б.',
                 'payload': {'kind': 'invoice_payment'}},
                {'kind': 'paid', 'actor_id': ACCOUNTANT, 'actor_name': 'Бухгалтер Б.', 'payload': {'kind': 'invoice_payment'}},
                {'kind': 'topped_up', 'actor_id': ADMIN, 'actor_name': 'Администратор А.', 'payload': {'kind': 'card_topup'}},
                {'kind': 'attachment_added', 'actor_id': ACCOUNTANT, 'actor_name': 'Бухгалтер Б.',
                 'payload': {'kind': 'act', 'subtask_kind': None}},
                {'kind': 'docs_status', 'actor_id': ACCOUNTANT, 'actor_name': 'Бухгалтер Б.', 'payload': {'to': 'scan'}},
                {'kind': 'reassigned', 'actor_id': ADMIN, 'actor_name': 'Администратор А.',
                 'payload': {'kind': 'invoice_payment', 'assignee_id': ACCOUNTANT, 'assignee_name': 'Бухгалтер Б.'}},
                {'kind': 'closed', 'actor_id': ACCOUNTANT, 'actor_name': 'Бухгалтер Б.', 'payload': {}},
            ],
            'attachments': [
                {'kind': 'invoice', 'uploaded_by': INITIATOR, 'uploaded_by_name': 'Инициатор И.', 'subtask_kind': 'initiation'},
                {'kind': 'payment_order', 'uploaded_by': ACCOUNTANT, 'uploaded_by_name': 'Бухгалтер Б.',
                 'subtask_kind': 'invoice_payment'},
                {'kind': 'transfer_proof', 'uploaded_by': FINANCIER, 'uploaded_by_name': 'Финансист Ф.',
                 'subtask_kind': None},
            ],
        }

    def test_who_sees_the_names(self):
        self.assertTrue(access.sees_department_people(ctx(ADMIN)))
        self.assertTrue(access.sees_department_people(ctx(ACCOUNTANT), {'accounting'}))
        self.assertTrue(access.sees_department_people(ctx(FINANCIER), {'finance'}))
        for user_id, roles in ((INITIATOR, set()), (MANAGER, set()), (APPROVER, {'approver'}),
                               (KEEPER, {'asset_keeper'})):
            self.assertFalse(access.sees_department_people(ctx(user_id), roles), roles)

    def test_department_staff_are_shown_as_the_department(self):
        card = privacy.hide_department_people(self.card(), MEMBERS)
        text = repr(card)
        self.assertNotIn('Бухгалтер Б.', text)
        self.assertNotIn('Финансист Ф.', text)
        self.assertNotIn('Администратор А.', text, 'администратор, оплативший за отдел, — тоже отдел')
        events = {event['kind']: event for event in card['events']}
        self.assertEqual(events['paid']['actor_name'], 'Бухгалтерия')
        self.assertEqual(events['topped_up']['actor_name'], 'Финансовый отдел')
        self.assertEqual(events['docs_status']['actor_name'], 'Бухгалтерия')
        self.assertEqual(events['closed']['actor_name'], 'Бухгалтерия')
        self.assertEqual(events['attachment_added']['actor_name'], 'Бухгалтерия')
        self.assertEqual(events['reassigned']['payload']['assignee_name'], 'Бухгалтерия')
        self.assertIsNone(events['paid']['actor_id'])
        payment = next(item for item in card['subtasks'] if item['kind'] == 'invoice_payment')
        self.assertEqual((payment['assignee_name'], payment['done_by_name'], payment['clarify_by_name']),
                         ('Бухгалтерия', 'Бухгалтерия', 'Бухгалтерия'))
        self.assertEqual(card['subtasks'][0]['clarify_by_name'], 'Бухгалтерия', 'кто вернул заявку инициатору')
        self.assertEqual(card['request']['clarify_by_name'], 'Бухгалтерия')
        self.assertEqual(card['request']['current_assignee_name'], 'Бухгалтерия')
        self.assertEqual([item['uploaded_by_name'] for item in card['attachments']],
                         ['Инициатор И.', 'Бухгалтерия', 'Финансовый отдел'])

    def test_people_of_the_request_and_the_system_keep_their_names(self):
        card = privacy.hide_department_people(self.card(), MEMBERS)
        events = {event['kind']: event for event in card['events']}
        self.assertEqual(events['created']['actor_name'], 'Инициатор И.')
        self.assertEqual(events['clarified']['actor_name'], 'Инициатор И.', 'ответ инициатора — его, а не отдела')
        self.assertEqual(events['approved']['actor_name'], 'Утверждающий У.', 'согласующего знают все участники')
        self.assertEqual(events['clarification']['actor_name'], 'iCore', 'запрос раздела остаётся запросом раздела')
        names = {item['kind']: item.get('done_by_name') for item in card['subtasks']}
        self.assertEqual((names['manager_approval'], names['approval']), ('Руководитель Р.', 'Утверждающий У.'))

    def test_approver_who_is_also_an_accountant_stays_a_person(self):
        """Утвердил как согласующий — подписан именем, даже если он же числится в бухгалтерии."""
        members = dict(MEMBERS, accounting={ACCOUNTANT, APPROVER})
        card = self.card()
        card['events'].append({'kind': 'attachment_added', 'actor_id': APPROVER, 'actor_name': 'Утверждающий У.',
                               'payload': {'kind': 'other', 'subtask_kind': None}})
        hidden = privacy.hide_department_people(card, members)
        self.assertEqual(hidden['events'][-1]['actor_name'], 'Утверждающий У.')

    def test_return_for_rework_keeps_the_approver_and_card_requests_name_finance(self):
        rework = {'kind': 'initiation', 'clarify_reason': 'rework', 'clarify_by': APPROVER,
                  'clarify_by_name': 'Утверждающий У.'}
        self.assertEqual(privacy.hide_in_subtask(dict(rework))['clarify_by_name'], 'Утверждающий У.')
        asked = {'kind': 'initiation', 'clarify_reason': 'card', 'clarify_by': FINANCIER,
                 'clarify_by_name': 'Финансист Ф.'}
        self.assertEqual(privacy.hide_in_subtask(dict(asked), 'card')['clarify_by_name'], 'Финансовый отдел')
        self.assertEqual(privacy.hide_in_subtask(dict(asked), 'invoice')['clarify_by_name'], 'Бухгалтерия')

    def test_row_of_the_registry(self):
        row = privacy.hide_in_row({'clarify_from': 'card_topup', 'clarify_by_name': 'Финансист Ф.'})
        self.assertEqual(row['clarify_by_name'], 'Финансовый отдел')
        by_approver = privacy.hide_in_row({'clarify_from': 'approval', 'clarify_by_name': 'Утверждающий У.'})
        self.assertEqual(by_approver['clarify_by_name'], 'Утверждающий У.')


class CapabilitiesTests(WiderPerimeter):
    def test_summary_for_the_frontend(self):
        admin = access.capabilities(ctx(ADMIN))
        self.assertTrue(admin['is_admin'] and admin['can_create'] and admin['sees_all_requests'])
        self.assertEqual(admin['boards'], ['approval', 'accounting', 'finance'])
        finance = access.capabilities(ctx(FINANCIER), {'finance'})
        self.assertEqual(finance['boards'], ['finance'])
        self.assertTrue(finance['can_manage_cards'])
        self.assertFalse(finance['is_admin'] or finance['sees_all_requests'] or finance['can_view_assets'])
        initiator = access.capabilities(ctx(INITIATOR))
        self.assertTrue(initiator['can_create'])
        self.assertEqual(initiator['boards'], [])
        outsider = access.capabilities(ctx(OUTSIDER))
        self.assertFalse(outsider['can_create'] or outsider['is_admin'] or outsider['boards'])
        # В сводке только то, что читает интерфейс: лишний флаг — ложный след для сопровождающего.
        self.assertEqual(set(admin), {'can_create', 'is_admin', 'sees_all_requests', 'boards', 'can_view_assets',
                                      'can_manage_assets', 'can_manage_cards'})


class FrontendWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP_JSX.read_text(encoding='utf-8')

    def test_allowlist_matches_the_backend(self):
        block = self.app.split('const PAYMENTS_ALLOWED_USER_IDS')[1].split(';')[0]
        listed = {int(x) for x in block.split('[')[1].split(']')[0].split(',') if x.strip()}
        self.assertEqual(listed, set(access.SECTION_ALLOWED_USER_IDS))

    def test_predicate_uses_only_the_list(self):
        gate = self.app.split('const canAccessPaymentsForUser')[1].split(';')[0]
        self.assertIn('PAYMENTS_ALLOWED_USER_IDS.has(Number(userLike?.id))', gate)
        self.assertNotIn("'super_admin'", gate)
        self.assertNotIn("'admin'", gate)

    def test_component_is_lazy_loaded_and_rendered(self):
        self.assertIn("const PaymentsView = lazyWithRetry(() => import('./components/payments/PaymentsView'));", self.app)
        self.assertIn('view === "payments" && canAccessPaymentsSection', self.app)
        self.assertIn("if (view === 'payments' && canAccessPaymentsSection) return;", self.app)
        self.assertIn("(requestedViewFromUrl !== 'payments' || canAccessPaymentsSection)", self.app)

    def test_menu_item_exists_for_every_role_branch(self):
        """Инициатором закупа бывает любой сотрудник (п. 3): пункт меню есть у админов,
        у руководителей и — отдельным объявлением — у всех остальных ролей. Три, а
        не два: без третьего оператор попадал в раздел только по ссылке."""
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'payments')"), 3)
        self.assertEqual(self.app.count('<span className="sidebar-text">Оплата счетов</span>'), 3)
        self.assertIn('{canAccessPaymentsSection && !isAdminLikeRole && !isDepartmentManager && (', self.app)

    def test_trainer_is_not_bounced_out_of_the_section(self):
        """У тренера свой список разрешённых разделов; без «payments» в нём его
        уводило в «Опросы» сразу после входа — так и было на стенде."""
        block = self.app.split('const TRAINER_ALLOWED_VIEWS = Object.freeze([')[1].split(']);')[0]
        self.assertIn("'payments'", block)

    def test_bell_opens_the_request(self):
        """Щелчок по уведомлению ведёт в раздел и открывает заявку из уведомления."""
        self.assertIn("if (nextView === 'payments' && Number(target)) {", self.app)
        self.assertIn('focusRequest={paymentsFocusRequest}', self.app)
        self.assertIn("if (view !== 'payments') setPaymentsFocusRequest(null);", self.app)

    def test_my_assets_block_is_in_both_profiles(self):
        """П. 12: имущество, переданное сотруднику, видно в его профиле — в обоих видах «Профиля»."""
        self.assertEqual(self.app.count('<MyAssetsCard'), 2)
        profile = (ROOT / 'src' / 'components' / 'profile' / 'ProfileView.jsx').read_text(encoding='utf-8')
        self.assertIn('{failed ? null : myAssets}', profile)
        card = (ROOT / 'src' / 'components' / 'profile' / 'MyAssetsCard.jsx').read_text(encoding='utf-8')
        self.assertIn('/api/payments/my-assets', card)

    def test_icon_token_is_mapped(self):
        fa_icon = (ROOT / 'src' / 'components' / 'common' / 'FaIcon.jsx').read_text(encoding='utf-8')
        self.assertIn("'fa-file-invoice':", fa_icon)
        self.assertIn("'fa-box':", fa_icon)


if __name__ == '__main__':
    unittest.main()
