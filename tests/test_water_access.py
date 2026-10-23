# -*- coding: utf-8 -*-
"""Периметр раздела «Учёт воды» (решение владельца 01.10.2026).

Границы, которые легко потерять рефакторингом:
  * журнал и настройки — только супер-админ и руководитель регионов / фронт-
    офисов (глава «Фронт офисы»); админ-сотрудник СЗоВ их не получает;
  * выдают воду поимённо названные офисники, руководитель и супер-админ;
    офисник не из списка в раздел не входит;
  * офисник выдаёт только в офисах, к которым ему выдан доступ (23.10.2026),
    руководитель и супер-админ — в любом;
  * колл-центр СЗоВ в любой роли — остатки, условия и проверка, без выдачи;
  * отдел продаж (ОП) — то же, что СЗоВ (02.10.2026);
  * тренер — как операторы своего отдела;
  * ТЭЗ КЦ закрыт целиком: и оператору, и главе, и админу;
  * QR — операторам, как в «Посылках».
"""

import unittest

from water import access


def ctx(role='operator', department_code='front_office', headed_codes=(), city=None, user_id=10):
    return {
        'user_id': user_id, 'name': 'Тест', 'role': role, 'department_id': 1,
        'department_code': department_code, 'city': city,
        'headed_department_ids': [1] if headed_codes else [],
        'headed_department_codes': list(headed_codes),
    }


ISSUER = 424  # один из офисников списка владельца


def manager():
    return ctx(role='admin', department_code='front_office', headed_codes=('front_office',))


class IssuerListTests(unittest.TestCase):
    def test_owner_list_of_office_staff(self):
        """Шесть офисников Алматы и двое Астаны — список владельца 01.10.2026."""
        self.assertEqual(sorted(access.ISSUER_USER_IDS), [419, 421, 422, 423, 424, 425, 426, 509])

    def test_listed_office_staff_issue_but_see_neither_journal_nor_settings(self):
        listed = ctx(user_id=ISSUER)
        self.assertTrue(access.can_open_section(listed))
        self.assertTrue(access.can_issue(listed))
        self.assertFalse(access.can_manage(listed))
        self.assertFalse(access.can_view_journal(listed))
        # Оператор — значит, QR-подтверждение сессии, как в «Посылках».
        self.assertTrue(access.requires_sensitive_qr(listed))

    def test_office_staff_outside_the_list_are_not_let_in(self):
        for role in ('operator', 'sv', 'trainee', 'admin'):
            self.assertFalse(access.can_open_section(ctx(role=role)), role)

    def test_listed_id_in_another_department_gets_that_department_rights(self):
        """Список — это офисники; в СЗоВ тот же id воду не выдаёт."""
        listed = ctx(department_code='szov', user_id=ISSUER)
        self.assertTrue(access.can_open_section(listed))
        self.assertFalse(access.can_issue(listed))


class ManagerTests(unittest.TestCase):
    def test_super_admin_gets_everything(self):
        for code in ('szov', 'front_office', None):
            boss = ctx(role='super_admin', department_code=code)
            self.assertTrue(access.can_open_section(boss), code)
            self.assertTrue(access.can_issue(boss), code)
            self.assertTrue(access.can_manage(boss), code)
            self.assertTrue(access.can_view_journal(boss), code)

    def test_head_of_front_offices_is_the_regional_manager(self):
        head = manager()
        self.assertTrue(access.is_regional_manager(head))
        self.assertTrue(access.can_open_section(head))
        self.assertTrue(access.can_issue(head))
        self.assertTrue(access.can_manage(head))
        self.assertTrue(access.can_view_journal(head))


class CallCentreTests(unittest.TestCase):
    def test_call_centre_sees_stock_and_conditions_in_any_role(self):
        for role in ('operator', 'trainee', 'sv', 'admin'):
            person = ctx(role=role, department_code='szov')
            self.assertTrue(access.can_open_section(person), role)
            self.assertFalse(access.can_issue(person), role)
            self.assertFalse(access.can_manage(person), role)
            self.assertFalse(access.can_view_journal(person), role)

    def test_head_of_call_centre_gets_call_centre_rights(self):
        head = ctx(role='admin', department_code='szov', headed_codes=('szov',))
        self.assertTrue(access.can_open_section(head))
        self.assertFalse(access.can_issue(head))
        self.assertFalse(access.can_manage(head))
        self.assertFalse(access.can_view_journal(head))


class SalesTests(unittest.TestCase):
    """«Раздел учёт воды, для ОП нужно открыть доступ» (02.10.2026) — права
    колл-центра СЗоВ: остатки, условия, проверка; без выдачи и журнала."""

    def test_sales_see_stock_and_conditions_in_any_role(self):
        for role in ('operator', 'trainee', 'sv', 'admin', 'trainer'):
            person = ctx(role=role, department_code='op')
            self.assertTrue(access.can_open_section(person), role)
            self.assertFalse(access.can_issue(person), role)
            self.assertFalse(access.can_manage(person), role)
            self.assertFalse(access.can_view_journal(person), role)

    def test_head_of_sales_gets_sales_rights(self):
        head = ctx(role='admin', department_code='op', headed_codes=('op',))
        self.assertTrue(access.can_open_section(head))
        self.assertFalse(access.can_issue(head))
        self.assertFalse(access.can_manage(head))
        self.assertFalse(access.can_view_journal(head))

    def test_sales_operators_confirm_by_qr(self):
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='op')))


class TrainerTests(unittest.TestCase):
    """«Тренеру можно выдать доступ как и операторам» (01.10.2026)."""

    def test_trainer_of_call_centre_gets_call_centre_rights(self):
        trainer = ctx(role='trainer', department_code='szov')
        self.assertTrue(access.can_open_section(trainer))
        self.assertFalse(access.can_issue(trainer))
        self.assertFalse(access.can_view_journal(trainer))

    def test_trainer_of_closed_department_stays_out(self):
        for code in ('tez', None):
            self.assertFalse(access.can_open_section(ctx(role='trainer', department_code=code)), code)

    def test_trainer_in_front_offices_follows_the_issuer_list(self):
        self.assertFalse(access.can_open_section(ctx(role='trainer')))
        self.assertTrue(access.can_issue(ctx(role='trainer', user_id=ISSUER)))


class ClosedTests(unittest.TestCase):
    def test_tez_call_centre_is_closed_entirely(self):
        for person in (ctx(department_code='tez'),
                       ctx(role='sv', department_code='tez'),
                       ctx(role='admin', department_code='tez'),
                       ctx(role='admin', department_code='tez', headed_codes=('tez',)),
                       ctx(department_code='tez', user_id=ISSUER)):
            self.assertFalse(access.can_open_section(person), person)
            self.assertFalse(access.can_issue(person), person)
            self.assertFalse(access.can_manage(person), person)

    def test_tez_head_is_not_let_in_by_another_membership(self):
        """ТЭЗ закрыт раньше любых других оснований — и отдела СЗоВ тоже."""
        self.assertFalse(access.can_open_section(
            ctx(role='admin', department_code='szov', headed_codes=('tez',))))

    def test_other_departments_and_their_admins_are_closed(self):
        for code in ('marketing', 'hr', 'it', None, ''):
            self.assertFalse(access.can_open_section(ctx(department_code=code)), code)
            self.assertFalse(access.can_open_section(ctx(role='admin', department_code=code)), code)
        self.assertFalse(access.can_open_section(
            ctx(role='admin', department_code='marketing', headed_codes=('marketing',))))


class QrTests(unittest.TestCase):
    def test_operators_of_both_departments_confirm_by_qr(self):
        self.assertTrue(access.requires_sensitive_qr(ctx(user_id=ISSUER)))
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='szov')))

    def test_supervisor_head_and_admin_do_not(self):
        self.assertFalse(access.requires_sensitive_qr(ctx(role='sv')))
        self.assertFalse(access.requires_sensitive_qr(manager()))
        self.assertFalse(access.requires_sensitive_qr(ctx(role='super_admin')))


class OfficeGrantTests(unittest.TestCase):
    """«…выдаём доступ, чтобы они могли выдавать воду только по доступным им
    офисам» (владелец 23.10.2026). Офисы — строки доступа из контекста."""

    def test_issuer_issues_only_in_granted_offices(self):
        issuer = dict(ctx(user_id=ISSUER), issue_office_ids=[2, 1, 2])
        self.assertTrue(access.issues_by_office_grant(issuer))
        self.assertEqual(access.issue_office_ids(issuer), [1, 2])
        self.assertTrue(access.can_issue_in_office(issuer, 1))
        self.assertTrue(access.can_issue_in_office(issuer, '2'))
        self.assertFalse(access.can_issue_in_office(issuer, 3))
        self.assertFalse(access.can_issue_in_office(issuer, None))
        self.assertFalse(access.can_issue_in_office(issuer, 'x'))

    def test_no_rows_mean_no_office_not_every_office(self):
        """Строк доступа нет (не выданы, не прочитались) — ни одного офиса, а не
        «любой»: так было до 23.10.2026, и правило владельца именно это сняло."""
        for issuer in (ctx(user_id=ISSUER), dict(ctx(user_id=ISSUER), issue_office_ids=None),
                       dict(ctx(user_id=ISSUER), issue_office_ids=[])):
            self.assertEqual(access.issue_office_ids(issuer), [])
            self.assertFalse(access.can_issue_in_office(issuer, 1))
            # Раздел при этом открыт, и человек — по-прежнему из выдающих.
            self.assertTrue(access.can_open_section(issuer))
            self.assertTrue(access.can_issue(issuer))

    def test_manager_and_super_admin_issue_anywhere_without_rows(self):
        for boss in (manager(), ctx(role='super_admin', department_code='szov')):
            self.assertFalse(access.issues_by_office_grant(boss))
            self.assertIsNone(access.issue_office_ids(boss))
            self.assertTrue(access.can_issue_in_office(boss, 7))
            # Даже чужие строки в контексте их не сужают.
            self.assertTrue(access.can_issue_in_office(dict(boss, issue_office_ids=[1]), 7))

    def test_those_who_do_not_issue_get_no_office_even_with_rows(self):
        for person in (ctx(department_code='szov'), ctx(department_code='op'),
                       ctx(role='admin', department_code='szov', headed_codes=('szov',)),
                       ctx(user_id=10), ctx(department_code='tez', user_id=ISSUER),
                       ctx(department_code='szov', user_id=ISSUER)):
            person = dict(person, issue_office_ids=[1])
            self.assertFalse(access.issues_by_office_grant(person), person)
            self.assertEqual(access.issue_office_ids(person), [], person)
            self.assertFalse(access.can_issue_in_office(person, 1), person)


class CapabilitiesTests(unittest.TestCase):
    def test_capabilities_of_an_issuer(self):
        self.assertEqual(access.capabilities(dict(ctx(city='Алматы', user_id=ISSUER), issue_office_ids=[1])), {
            'can_open': True, 'can_issue': True, 'issue_office_ids': [1], 'can_manage': False,
            'can_view_journal': False, 'requires_qr': True, 'is_global_admin': False, 'default_city': 'Алматы',
        })

    def test_offices_of_the_manager_and_the_call_centre(self):
        self.assertIsNone(access.capabilities(manager())['issue_office_ids'])
        self.assertEqual(access.capabilities(ctx(department_code='szov'))['issue_office_ids'], [])

    def test_capabilities_of_the_call_centre(self):
        caps = access.capabilities(ctx(department_code='szov'))
        self.assertEqual((caps['can_open'], caps['can_issue'], caps['can_manage'], caps['can_view_journal']),
                         (True, False, False, False))

    def test_capabilities_of_the_regional_manager(self):
        caps = access.capabilities(manager())
        self.assertEqual((caps['can_open'], caps['can_issue'], caps['can_manage'], caps['can_view_journal']),
                         (True, True, True, True))


if __name__ == '__main__':
    unittest.main()
