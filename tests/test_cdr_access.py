# -*- coding: utf-8 -*-
"""Периметр раздела «Касания».

Проверяется правило, которое легко потерять при правке: раздел принадлежит
отделу продаж, но открыт и глобальным админам, которые ни в каком отделе не
состоят. При этом «глава отдела» — не роль, а признак: назначение главой
ЗАМЕНЯЕТ базовую роль и режет периметр своим отделом (действующая семантика
портала, та же в parcels/access.py и crm/access.py).

Отдельно закреплено, что оператору раздел закрыт: выгрузка — это телефоны
клиентов за период целиком, инструмент разбора работы отдела, а не личный
кабинет. Если однажды понадобится «свои звонки оператору», это будет другой
экран и другое право, а не ослабление этого.

Модуль `cdr.access` чистый — ни базы, ни Flask, поэтому импортируется напрямую.
"""

import unittest

from cdr import access


def ctx(role='operator', department_code=None, headed_ids=None, headed_codes=None):
    return {
        'user_id': 1, 'name': 'Кто-то', 'role': role,
        'department_id': None, 'department_code': department_code,
        'headed_department_ids': headed_ids or [],
        'headed_department_codes': headed_codes or [],
    }


class RoleTests(unittest.TestCase):
    def test_supervisor_spellings_are_one_role(self):
        self.assertEqual(access.normalize_role('supervisor'), 'sv')
        self.assertEqual(access.normalize_role('SV'), 'sv')
        self.assertEqual(access.normalize_role('superadmin'), 'super_admin')

    def test_unknown_role_falls_to_operator(self):
        """Правильная сторона ошибки: незнакомая роль — закрыто, а не открыто."""
        self.assertEqual(access.normalize_role('директор'), 'operator')
        self.assertFalse(access.can_open_section(ctx(role='директор',
                                                     department_code='op')))


class SectionAccessTests(unittest.TestCase):
    def test_super_admin_always_in(self):
        self.assertTrue(access.can_open_section(ctx(role='super_admin')))

    def test_global_admin_is_in_without_any_department(self):
        self.assertTrue(access.can_open_section(ctx(role='admin')))

    def test_admin_who_heads_another_department_is_out(self):
        """Назначение главой заменяет базовую роль: глава СЗоВ не читает звонки
        отдела продаж."""
        head_of_szov = ctx(role='admin', department_code='szov',
                           headed_ids=[1], headed_codes=['szov'])
        self.assertFalse(access.is_global_admin(head_of_szov))
        self.assertFalse(access.can_open_section(head_of_szov))

    def test_head_of_sales_is_in(self):
        self.assertTrue(access.can_open_section(
            ctx(role='admin', department_code='op', headed_ids=[367],
                headed_codes=['op'])))

    def test_sales_supervisor_is_in(self):
        self.assertTrue(access.can_open_section(ctx(role='sv', department_code='op')))

    def test_supervisor_of_another_department_is_out(self):
        self.assertFalse(access.can_open_section(ctx(role='sv', department_code='szov')))

    def test_sales_operator_is_out(self):
        """Выгрузка — это телефоны клиентов за период целиком, а не свои звонки."""
        self.assertFalse(access.can_open_section(ctx(role='operator',
                                                     department_code='op')))

    def test_trainer_is_out_even_in_sales(self):
        self.assertFalse(access.can_open_section(ctx(role='trainer',
                                                     department_code='op')))

    def test_department_code_is_case_insensitive(self):
        self.assertTrue(access.can_open_section(ctx(role='sv', department_code='OP')))
        self.assertTrue(access.can_open_section(ctx(role='sv', department_code=' op ')))


class SyncRightTests(unittest.TestCase):
    """Право «дозаказать сутки со станции» сейчас равно праву на чтение, но
    названо отдельно: у действия своя цена (минуты работы моста и нагрузка на
    станцию), и сузить его завтра надо будет в одном месте, а не по всем роутам."""

    def test_sync_follows_read_for_everyone(self):
        for who in (ctx(role='super_admin'), ctx(role='admin'),
                    ctx(role='sv', department_code='op'),
                    ctx(role='operator', department_code='op'),
                    ctx(role='trainer', department_code='op')):
            self.assertEqual(access.can_sync(who), access.can_open_section(who),
                             who['role'])


class CapabilitiesTests(unittest.TestCase):
    def test_capabilities_describe_the_same_rules(self):
        caps = access.capabilities(ctx(role='sv', department_code='op'))
        self.assertEqual(caps, {'can_open': True, 'can_sync': True,
                                'is_global_admin': False, 'is_department_head': False})

    def test_capabilities_for_a_stranger_are_all_false(self):
        caps = access.capabilities(ctx(role='operator', department_code='szov'))
        self.assertFalse(caps['can_open'])
        self.assertFalse(caps['can_sync'])


if __name__ == '__main__':
    unittest.main()
