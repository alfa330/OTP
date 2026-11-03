# -*- coding: utf-8 -*-
"""Заявки на отпуск, Б/С и больничный (#358, #346): чистые правила, без базы.

Что именно значат правила на схеме — tests/test_leave_requests_postgres.py.
"""
import unittest
from datetime import date

from leave_requests import rules
from leave_requests.rules import LeaveError

TODAY = date(2026, 11, 2)


class PeriodTests(unittest.TestCase):
    def test_days_count_both_ends_inclusive(self):
        self.assertEqual(1, rules.period_days(TODAY, TODAY))
        self.assertEqual(14, rules.period_days(date(2026, 7, 1), date(2026, 7, 14)))

    def test_days_text_declension(self):
        cases = {1: '1 день', 2: '2 дня', 4: '4 дня', 5: '5 дней', 11: '11 дней', 12: '12 дней',
                 14: '14 дней', 21: '21 день', 22: '22 дня', 25: '25 дней', 111: '111 дней'}
        for count, text in cases.items():
            with self.subTest(count):
                self.assertEqual(text, rules.days_text(count))

    def test_format_period_writes_year_only_when_not_current(self):
        self.assertEqual('01.12 — 14.12', rules.format_period(date(2026, 12, 1), date(2026, 12, 14), TODAY))
        self.assertEqual('28.12 — 10.01.2027', rules.format_period(date(2026, 12, 28), date(2027, 1, 10), TODAY))
        self.assertEqual('05.11', rules.format_period(date(2026, 11, 5), date(2026, 11, 5), TODAY))

    def test_validate_accepts_today_and_rejects_past(self):
        rules.validate_period(TODAY, TODAY, TODAY)
        with self.assertRaisesRegex(LeaveError, 'прошло'):
            rules.validate_period(date(2026, 11, 1), date(2026, 11, 5), TODAY)

    def test_validate_rejects_reversed_long_and_far(self):
        with self.assertRaisesRegex(LeaveError, 'раньше даты начала'):
            rules.validate_period(date(2026, 11, 10), date(2026, 11, 9), TODAY)
        with self.assertRaisesRegex(LeaveError, 'не больше 365 дней'):
            rules.validate_period(date(2026, 11, 10), date(2027, 11, 10), TODAY)
        rules.validate_period(date(2026, 11, 10), date(2027, 11, 9), TODAY)
        with self.assertRaisesRegex(LeaveError, 'год вперёд'):
            rules.validate_period(date(2027, 11, 3), date(2027, 11, 5), TODAY)

    def test_sick_leave_goes_back_thirty_days_and_nothing_else_does(self):
        rules.validate_period(date(2026, 10, 3), date(2026, 10, 5), TODAY, 'sick_leave')
        with self.assertRaisesRegex(LeaveError, 'задним числом не больше чем на 30 дней'):
            rules.validate_period(date(2026, 10, 2), date(2026, 10, 5), TODAY, 'sick_leave')
        for kind in ('annual_leave', 'bs', None):
            with self.subTest(kind), self.assertRaisesRegex(LeaveError, 'прошло'):
                rules.validate_period(date(2026, 11, 1), date(2026, 11, 5), TODAY, kind)
        self.assertEqual(date(2026, 10, 3), rules.earliest_start('sick_leave', TODAY))
        self.assertEqual(TODAY, rules.earliest_start('bs', TODAY))

    def test_parse_date_says_what_is_missing(self):
        self.assertEqual(date(2026, 7, 1), rules.parse_date('2026-07-01'))
        self.assertEqual(date(2026, 7, 1), rules.parse_date('2026-07-01T00:00:00'))
        with self.assertRaisesRegex(LeaveError, 'Укажите дату'):
            rules.parse_date('')


class SettingsTests(unittest.TestCase):
    def test_allowed_kinds_follow_switches(self):
        self.assertEqual((), rules.allowed_kinds(None))
        self.assertEqual((), rules.allowed_kinds({'enabled': False, 'allow_annual_leave': True, 'allow_bs': True}))
        self.assertEqual(('annual_leave', 'bs'),
                         rules.allowed_kinds({'enabled': True, 'allow_annual_leave': True, 'allow_bs': True}))
        self.assertEqual(('bs',), rules.allowed_kinds({'enabled': True, 'allow_annual_leave': False, 'allow_bs': True}))
        self.assertEqual(('annual_leave', 'bs', 'sick_leave'), rules.allowed_kinds(
            {'enabled': True, 'allow_annual_leave': True, 'allow_bs': True, 'allow_sick_leave': True}))

    def test_first_status_skips_missing_or_disabled_supervisor_step(self):
        on = {'supervisor_step': True}
        off = {'supervisor_step': False}
        self.assertEqual('supervisor', rules.first_status(on, 300))
        self.assertEqual('head', rules.first_status(on, None))
        self.assertEqual('head', rules.first_status(off, 300))

    def test_patch_normalization(self):
        self.assertEqual({'enabled': True, 'long_bs_days': None},
                         rules.normalize_settings_patch({'enabled': 1, 'long_bs_days': '', 'junk': 5}))
        self.assertEqual({'long_bs_days': 30}, rules.normalize_settings_patch({'long_bs_days': '30'}))
        self.assertEqual({'allow_sick_leave': False}, rules.normalize_settings_patch({'allow_sick_leave': 0}))
        with self.assertRaisesRegex(LeaveError, 'от 1 до 366'):
            rules.normalize_settings_patch({'long_bs_days': 400})
        with self.assertRaisesRegex(LeaveError, 'число дней'):
            rules.normalize_settings_patch({'long_bs_days': 'месяц'})

    def test_enabled_department_keeps_at_least_one_kind(self):
        with self.assertRaisesRegex(LeaveError, 'хотя бы один вид'):
            rules.check_settings({'enabled': True, 'allow_annual_leave': False, 'allow_bs': False})
        rules.check_settings({'enabled': False, 'allow_annual_leave': False, 'allow_bs': False})
        rules.check_settings({'enabled': True, 'allow_annual_leave': False, 'allow_bs': False,
                              'allow_sick_leave': True})

    def test_long_bs_default_only_for_bs_from_threshold(self):
        self.assertTrue(rules.default_exclude_from_tenure('bs', 30, 30))
        self.assertFalse(rules.default_exclude_from_tenure('bs', 29, 30))
        self.assertFalse(rules.default_exclude_from_tenure('annual_leave', 60, 30))
        self.assertFalse(rules.default_exclude_from_tenure('bs', 90, None))

    def test_settings_belong_to_admin_and_heads_of_their_departments(self):
        admin = {'user_id': 1, 'is_admin': True, 'headed_department_ids': []}
        head = {'user_id': 100, 'is_admin': False, 'headed_department_ids': [1, '3']}
        supervisor = {'user_id': 300, 'is_admin': False, 'headed_department_ids': []}
        self.assertIsNone(rules.settings_departments(admin))
        self.assertEqual(frozenset({1, 3}), rules.settings_departments(head))
        self.assertEqual(frozenset(), rules.settings_departments(supervisor))
        self.assertTrue(rules.can_manage_settings(admin))
        self.assertTrue(rules.can_manage_settings(head))
        self.assertFalse(rules.can_manage_settings(supervisor))


class JournalTests(unittest.TestCase):
    def test_month_bounds(self):
        self.assertEqual((date(2026, 11, 1), date(2026, 11, 30)), rules.parse_month('2026-11', TODAY))
        self.assertEqual((date(2026, 12, 1), date(2026, 12, 31)), rules.parse_month('2026-12', TODAY))
        self.assertEqual((date(2028, 2, 1), date(2028, 2, 29)), rules.parse_month('2028-02', TODAY))
        self.assertEqual((date(2026, 11, 1), date(2026, 11, 30)), rules.parse_month('', TODAY))
        with self.assertRaisesRegex(LeaveError, 'месяц'):
            rules.parse_month('ноябрь', TODAY)

    def test_filters(self):
        self.assertEqual((None, None), rules.journal_filter('', None))
        self.assertEqual(('bs', ('approved',)), rules.journal_filter('bs', 'approved'))
        self.assertEqual((None, ('supervisor', 'head', 'proposal')), rules.journal_filter(None, 'open'))
        with self.assertRaises(LeaveError):
            rules.journal_filter('dismissal', '')
        with self.assertRaises(LeaveError):
            rules.journal_filter('', 'all')


class RoleTests(unittest.TestCase):
    SV = {'user_id': 300, 'is_supervisor': True, 'headed_department_ids': []}
    HEAD = {'user_id': 100, 'is_supervisor': False, 'headed_department_ids': [1]}
    ADMIN = {'user_id': 1, 'is_admin': True}
    REQUEST = {'department_id': 1}

    def test_roles(self):
        self.assertEqual('supervisor', rules.reviewer_role(self.SV, self.REQUEST, in_scope=True))
        self.assertIsNone(rules.reviewer_role(self.SV, self.REQUEST, in_scope=False))
        self.assertEqual('head', rules.reviewer_role(self.HEAD, self.REQUEST, in_scope=True))
        # Отдел в заявке — снимок: переведённого оператора прежний глава не решает.
        self.assertIsNone(rules.reviewer_role(self.HEAD, self.REQUEST, in_scope=False))
        self.assertIsNone(rules.reviewer_role(self.HEAD, {'department_id': 2}, in_scope=True))
        self.assertEqual('head', rules.reviewer_role(self.ADMIN, {'department_id': 2}, in_scope=False))

    def test_supervisor_acts_only_on_own_step(self):
        self.assertEqual(('approve', 'propose', 'reject'), rules.reviewer_actions('supervisor', 'supervisor'))
        for status in ('head', 'proposal', 'approved', 'rejected', 'cancelled'):
            with self.subTest(status):
                self.assertEqual((), rules.reviewer_actions('supervisor', status))

    def test_head_decides_finally_on_any_open_step_but_proposes_only_for_supervisor(self):
        self.assertEqual(('approve', 'propose', 'reject'), rules.reviewer_actions('head', 'supervisor'))
        self.assertEqual(('approve', 'reject'), rules.reviewer_actions('head', 'head'))
        self.assertEqual((), rules.reviewer_actions('head', 'proposal'))
        self.assertEqual('approved', rules.status_after_approve('head'))
        self.assertEqual('head', rules.status_after_approve('supervisor'))

    def test_sick_leave_is_never_moved_to_other_dates(self):
        self.assertEqual(('approve', 'reject'), rules.reviewer_actions('supervisor', 'supervisor', 'sick_leave'))
        self.assertEqual(('approve', 'reject'), rules.reviewer_actions('head', 'supervisor', 'sick_leave'))
        self.assertEqual(('approve', 'reject'), rules.reviewer_actions('head', 'head', 'sick_leave'))
        self.assertEqual(('approve', 'propose', 'reject'), rules.reviewer_actions('supervisor', 'supervisor', 'bs'))
        self.assertFalse(rules.default_exclude_from_tenure('sick_leave', 90, 30))

    def test_awaits_counts_only_own_step(self):
        self.assertTrue(rules.awaits('supervisor', 'supervisor'))
        self.assertFalse(rules.awaits('supervisor', 'head'))
        self.assertTrue(rules.awaits('head', 'head'))
        self.assertFalse(rules.awaits('head', 'supervisor'))

    def test_operator_actions(self):
        self.assertEqual(('edit', 'cancel'), rules.operator_actions('supervisor'))
        self.assertEqual(('edit', 'cancel'), rules.operator_actions('head'))
        self.assertEqual(('accept', 'decline', 'edit', 'cancel'), rules.operator_actions('proposal'))
        for status in ('approved', 'rejected', 'cancelled'):
            self.assertEqual((), rules.operator_actions(status))


class TextTests(unittest.TestCase):
    def test_required_and_limit(self):
        self.assertIsNone(rules.clean_text('  ', 10))
        with self.assertRaisesRegex(LeaveError, 'Напишите причину'):
            rules.clean_text(' ', 10, required=True, empty_message='Напишите причину')
        with self.assertRaisesRegex(LeaveError, 'не больше 10'):
            rules.clean_text('x' * 11, 10)

    def test_kind_codes_match_schedule_statuses(self):
        """Утверждённая заявка ставит в график период с кодом вида — коды обязаны
        совпадать со статусами графика (SCHEDULE_SPECIAL_STATUS_META)."""
        import ast
        from tests import source_cache
        tree = ast.parse(source_cache.database_flat_text())
        meta = next(
            node.value for node in tree.body
            if isinstance(node, ast.AnnAssign) and getattr(node.target, 'id', '') == 'SCHEDULE_SPECIAL_STATUS_META'
        )
        codes = {key.value for key in meta.keys}
        for kind in rules.KINDS:
            self.assertIn(kind, codes)


if __name__ == '__main__':
    unittest.main()
