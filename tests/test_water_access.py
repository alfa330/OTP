# -*- coding: utf-8 -*-
"""Периметр раздела «Учёт воды».

Границы, которые легко потерять рефакторингом:
  * вход — фронт-офисы и колл-центр СЗоВ (любая роль), глобальный админ;
  * выдаёт воду только фронт-офис: колл-центр проверяет, но не выдаёт;
  * учёт (офисы, поступления, пересчёт, условия, пороги) — глава фронт-офисов
    («региональный руководитель») и глобальный админ; глава СЗоВ — нет;
  * QR — операторам, как в «Посылках».
"""

import unittest
from unittest import mock

from water import access


class _AfterPilot(unittest.TestCase):
    """Периметр ПОСЛЕ пилота: флаг «только супер-админ» снят."""

    def setUp(self):
        patcher = mock.patch.object(access, 'PILOT', False)
        patcher.start()
        self.addCleanup(patcher.stop)


class PilotTests(unittest.TestCase):
    """Пилот: супер-админ и поимённый список офисников (01.10.2026), больше никто."""

    def test_only_super_admin_and_listed_people_get_in_during_the_pilot(self):
        self.assertTrue(access.PILOT)
        self.assertTrue(access.can_open_section(ctx(role='super_admin', department_code='szov')))
        for role, code, heads in (('operator', 'front_office', ()), ('admin', None, ()),
                                  ('admin', 'front_office', ('front_office',)), ('sv', 'szov', ())):
            self.assertFalse(access.can_open_section(ctx(role=role, department_code=code, headed_codes=heads)),
                             (role, code))

    def test_owner_list_of_office_staff(self):
        """Шесть офисников Алматы и двое Астаны — список владельца 01.10.2026."""
        self.assertEqual(sorted(access.PILOT_USER_IDS), [419, 421, 422, 423, 424, 425, 426, 509])

    def test_listed_office_operator_gets_in_and_issues_but_does_not_manage(self):
        listed = dict(ctx(role='operator', department_code='front_office'), user_id=424)
        self.assertTrue(access.can_open_section(listed))
        self.assertTrue(access.can_issue(listed))
        self.assertFalse(access.can_manage(listed))
        # Оператор — значит, QR-подтверждение сессии, как в «Посылках».
        self.assertTrue(access.requires_sensitive_qr(listed))

    def test_listed_id_in_another_department_still_only_checks(self):
        """Список открывает вход, права внутри — по отделу: СЗоВ воду не выдаёт."""
        listed = dict(ctx(role='operator', department_code='szov'), user_id=424)
        self.assertTrue(access.can_open_section(listed))
        self.assertFalse(access.can_issue(listed))


def ctx(role='operator', department_code='front_office', headed_codes=(), city=None):
    return {
        'user_id': 10, 'name': 'Тест', 'role': role, 'department_id': 1,
        'department_code': department_code, 'city': city,
        'headed_department_ids': [1] if headed_codes else [],
        'headed_department_codes': list(headed_codes),
    }


class EntryTests(_AfterPilot):
    def test_front_office_and_call_centre_get_in(self):
        self.assertTrue(access.can_open_section(ctx(department_code='front_office')))
        self.assertTrue(access.can_open_section(ctx(department_code='szov')))

    def test_other_departments_do_not(self):
        for code in ('op', 'tez', 'marketing', 'hr', None, ''):
            self.assertFalse(access.can_open_section(ctx(department_code=code)), code)

    def test_global_admin_gets_in_without_department(self):
        self.assertTrue(access.can_open_section(ctx(role='super_admin', department_code=None)))
        self.assertTrue(access.can_open_section(ctx(role='admin', department_code=None)))

    def test_head_of_another_department_is_not_a_global_admin(self):
        head = ctx(role='admin', department_code='op', headed_codes=('op',))
        self.assertFalse(access.can_open_section(head))


class IssueTests(_AfterPilot):
    def test_front_office_issues_in_any_role(self):
        for role in ('operator', 'trainee', 'sv'):
            self.assertTrue(access.can_issue(ctx(role=role)), role)

    def test_trainer_is_not_let_in(self):
        """Раздел тренер не просил — правило «буквально и не расширять»."""
        for code in ('front_office', 'szov'):
            self.assertFalse(access.can_open_section(ctx(role='trainer', department_code=code)), code)

    def test_call_centre_never_issues(self):
        for role in ('operator', 'sv'):
            self.assertFalse(access.can_issue(ctx(role=role, department_code='szov')), role)
        self.assertFalse(access.can_issue(ctx(role='admin', department_code='szov',
                                              headed_codes=('szov',))))


class ManageTests(_AfterPilot):
    def test_head_of_front_offices_is_the_regional_manager(self):
        head = ctx(role='admin', headed_codes=('front_office',))
        self.assertTrue(access.can_manage(head))

    def test_rank_and_file_front_office_does_not_manage(self):
        for role in ('operator', 'sv', 'trainee'):
            self.assertFalse(access.can_manage(ctx(role=role)), role)

    def test_head_of_call_centre_does_not_manage(self):
        self.assertFalse(access.can_manage(ctx(role='admin', department_code='szov',
                                               headed_codes=('szov',))))

    def test_global_admin_manages(self):
        self.assertTrue(access.can_manage(ctx(role='super_admin', department_code='szov')))


class QrTests(_AfterPilot):
    def test_operators_of_both_departments_confirm_by_qr(self):
        self.assertTrue(access.requires_sensitive_qr(ctx()))
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='szov')))

    def test_supervisor_head_and_admin_do_not(self):
        self.assertFalse(access.requires_sensitive_qr(ctx(role='sv')))
        self.assertFalse(access.requires_sensitive_qr(ctx(role='admin', headed_codes=('front_office',))))
        self.assertFalse(access.requires_sensitive_qr(ctx(role='super_admin')))


class CapabilitiesTests(_AfterPilot):
    def test_capabilities_mirror_the_predicates(self):
        caps = access.capabilities(ctx(city='Алматы'))
        self.assertEqual(caps, {
            'can_open': True, 'can_issue': True, 'can_manage': False,
            'requires_qr': True, 'is_global_admin': False, 'default_city': 'Алматы',
        })


if __name__ == '__main__':
    unittest.main()
