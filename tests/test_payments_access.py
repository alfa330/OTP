# -*- coding: utf-8 -*-
"""Периметр раздела «Оплата счетов» и его проводка во фронте.

Владелец сказал: «раздел доступный пока что только мне». Проверяем именно это:
  * бэкенд пускает только id из SECTION_ALLOWED_USER_IDS — роль не помогает,
    даже super_admin;
  * фронт держит ТОТ ЖЕ список — иначе раздел показывается одному человеку,
    а пускает другого (или наоборот — открывается лишь прямым адресом);
  * пункт меню объявлен в обеих ролевых ветках сайдбара, а гард видимости и
    редирект по ?view= знают ключ payments;
  * права на шаге: человек — свой шаг, участник роли — шаг роли, чужой — нет.
"""

import unittest
from pathlib import Path

from payments import access

ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / 'src' / 'App.jsx'


def ctx(user_id, role='operator'):
    return {'user_id': user_id, 'name': 'Тест', 'role': role, 'department_code': 'szov'}


class SectionGateTests(unittest.TestCase):
    def test_only_listed_ids_get_in(self):
        for user_id in access.SECTION_ALLOWED_USER_IDS:
            self.assertTrue(access.can_open_section(ctx(user_id, role='operator')))
        self.assertFalse(access.can_open_section(ctx(999, role='super_admin')),
                         'роль super_admin сама по себе не открывает раздел')
        self.assertFalse(access.can_open_section(ctx(None)))
        self.assertFalse(access.can_open_section({'user_id': 'abc'}))

    def test_string_id_from_json_is_accepted(self):
        user_id = access.SECTION_ALLOWED_USER_IDS[0]
        self.assertTrue(access.can_open_section({'user_id': str(user_id)}))

    def test_pilot_perimeter_is_the_owner_only(self):
        self.assertEqual(tuple(access.SECTION_ALLOWED_USER_IDS), (2,))
        self.assertEqual(tuple(access.SECTION_ADMIN_USER_IDS), (2,))


class StepPermissionTests(unittest.TestCase):
    MEMBERS = {'founder': {40, 41}, 'accounting': {50}}

    def test_assignee_acts_on_own_step_only(self):
        step = {'role_code': 'initiator', 'assignee_id': 10}
        self.assertTrue(access.can_act_on_step(ctx(10), step, self.MEMBERS))
        self.assertFalse(access.can_act_on_step(ctx(11), step, self.MEMBERS))

    def test_role_member_acts_on_role_step(self):
        step = {'role_code': 'founder', 'assignee_id': None}
        self.assertTrue(access.can_act_on_step(ctx(40), step, self.MEMBERS))
        self.assertTrue(access.can_act_on_step(ctx(41), step, self.MEMBERS))
        self.assertFalse(access.can_act_on_step(ctx(50), step, self.MEMBERS), 'бухгалтер не Учредитель')

    def test_delegate_replaces_the_role(self):
        """Шаг 9 достался Директору по развитию — Учредитель на него уже не отписывается."""
        step = {'role_code': 'founder', 'assignee_id': 9}
        self.assertTrue(access.can_act_on_step(ctx(9), step, self.MEMBERS))
        self.assertFalse(access.can_act_on_step(ctx(40), step, self.MEMBERS))

    def test_section_admin_acts_for_anyone(self):
        step = {'role_code': 'accounting', 'assignee_id': None}
        self.assertTrue(access.can_act_on_step(ctx(2), step, {}))

    def test_edit_and_cancel_follow_payment(self):
        active = {'status': 'active', 'initiator_id': 10, 'current_step': 7}
        paid = dict(active, current_step=11)
        self.assertTrue(access.can_edit_request(ctx(10), active))
        self.assertFalse(access.can_edit_request(ctx(10), paid), 'после оплаты инициатор не правит')
        self.assertTrue(access.can_edit_request(ctx(2), paid), 'администратор — с записью в истории')
        self.assertTrue(access.can_cancel_request(ctx(10), active))
        self.assertFalse(access.can_cancel_request(ctx(10), paid))
        self.assertFalse(access.can_cancel_request(ctx(2), paid), 'оплаченную не отменяют даже админу')
        self.assertFalse(access.can_edit_request(ctx(11), active), 'чужую заявку не правят')


class FrontendWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP_JSX, encoding='utf-8') as handle:
            cls.app = handle.read()

    def test_allowlist_matches_the_backend(self):
        block = self.app.split('const PAYMENTS_ALLOWED_USER_IDS')[1].split(';')[0]
        for user_id in access.SECTION_ALLOWED_USER_IDS:
            self.assertIn(str(user_id), block)
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

    def test_menu_item_lives_in_both_role_branches(self):
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'payments')"), 2)
        self.assertEqual(self.app.count('<span className="sidebar-text">Оплата счетов</span>'), 2)

    def test_icon_token_is_mapped(self):
        fa_icon = (ROOT / 'src' / 'components' / 'common' / 'FaIcon.jsx').read_text(encoding='utf-8')
        self.assertIn("'fa-file-invoice':", fa_icon)


if __name__ == '__main__':
    unittest.main()
