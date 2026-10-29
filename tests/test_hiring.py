# -*- coding: utf-8 -*-
"""Раздел «Воронка найма» (#404): правила, права, разбор файла, HeadHunter, отчёты, проводка.

Без базы: процесс на схеме раздела проверяет tests/test_hiring_postgres.py.
"""

import io
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from hiring import access, hh, reports, rules, upload

ROOT = Path(__file__).resolve().parents[1]
BOT_PY = ROOT / 'bot_schedule2.py'
DATABASE_PY = ROOT / 'database.py'
APP_JSX = ROOT / 'src' / 'App.jsx'
SOURCES_PY = ROOT / 'notifications' / 'sources.py'
BELL_JSX = ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx'
SCOPES_PY = ROOT / 'partner_keys' / 'scopes.py'


def ctx(**overrides):
    base = {'user_id': 7, 'name': 'Рекрутер', 'role': 'operator', 'status': 'working', 'department_id': 1,
            'department_code': 'op', 'headed_ids': [], 'headed_codes': [], 'grant': None, 'trainer_group_ids': [],
            'vacancy_department_ids': []}
    base.update(overrides)
    return base


class PhoneTests(unittest.TestCase):
    def test_normalizes_to_plus7(self):
        for raw in ('8 705 123 45 67', '+7 (705) 123-45-67', '77051234567', '7051234567', '  8-705-1234567 '):
            self.assertEqual(rules.normalize_phone(raw), '+77051234567', raw)

    def test_rejects_garbage(self):
        for raw in ('', None, '123', '+1 650 555 1234', '705123456', 'abc'):
            self.assertIsNone(rules.normalize_phone(raw), raw)

    def test_pretty_and_whatsapp(self):
        self.assertEqual(rules.pretty_phone('+77051234567'), '+7 705 123 45 67')
        self.assertEqual(rules.whatsapp_link('87051234567'), 'https://web.whatsapp.com/send?phone=77051234567')
        self.assertEqual(rules.phone_key('8 705 123 45 67'), '7051234567')


class StageTests(unittest.TestCase):
    def test_ten_columns_in_tz_order(self):
        self.assertEqual(rules.STAGE_CODES, ('contacts', 'responses', 'in_work', 'invited_office', 'invited_training',
                                             'refused', 'on_training', 'on_line', 'dropped', 'hired'))

    def test_substages_of_tz_4_1(self):
        self.assertEqual([c for c, _ in rules.SUBSTAGES['in_work']], ['dial', 'no_answer', 'whatsapp'])
        self.assertEqual([c for c, _ in rules.SUBSTAGES['invited_office']], ['expected', 'came', 'no_show'])
        self.assertEqual([c for c, _ in rules.SUBSTAGES['refused']], ['ours', 'candidate'])
        self.assertEqual([c for c, _ in rules.SUBSTAGES['dropped']], ['self', 'we'])
        self.assertTrue(rules.substage_allowed('in_work', None))
        self.assertFalse(rules.substage_allowed('refused', None))
        self.assertFalse(rules.substage_allowed('in_work', 'came'))

    def test_targets_follow_tz_4_2(self):
        self.assertEqual(rules.TARGETS_FROM['in_work'], ['invited_office', 'invited_training', 'refused'])
        self.assertEqual(rules.TARGETS_FROM['invited_office'], ['invited_training', 'refused'])
        self.assertEqual(rules.TARGETS_FROM['on_line'], ['dropped', 'hired'])
        self.assertEqual(rules.TARGETS_FROM['refused'], ['in_work'])
        self.assertNotIn('hired', rules.TARGETS_FROM)

    def test_reasons_seed_is_the_tz_lists(self):
        self.assertEqual(len(rules.SEED_REASONS['refusal_ours']), 10)
        self.assertEqual(len(rules.SEED_REASONS['refusal_candidate']), 9)
        self.assertEqual(len(rules.SEED_REASONS['drop_self']), 6)
        self.assertEqual(len(rules.SEED_REASONS['drop_we']), 5)
        self.assertIn(rules.REASON_NO_SHOW_OFFICE, rules.SEED_REASONS['refusal_candidate'])
        self.assertIn(rules.REASON_NO_CONTACT, rules.SEED_REASONS['refusal_candidate'])
        for names in rules.SEED_REASONS.values():
            self.assertEqual(names[-1], 'Другое')

    def test_sources_seed(self):
        codes = [code for code, *_ in rules.SEED_SOURCES]
        self.assertEqual(codes[:6], ['hh', 'kaspi', 'olx', 'target', 'manual', 'referral'])
        self.assertEqual({kind for _c, _n, kind, _p in rules.SEED_SOURCES}, {'responses', 'contacts'})


class TransitionTests(unittest.TestCase):
    def check(self, card, target, kinds, has_touch=True, payload=None):
        return rules.check_transition(card, target, set(kinds), has_touch=has_touch, payload=payload or {})

    def test_take_requires_free_card(self):
        rule = self.check({'stage': 'contacts', 'recruiter_id': None}, 'in_work', ['recruiter'])
        self.assertTrue(rule.get('take'))
        with self.assertRaises(rules.TransitionError) as caught:
            self.check({'stage': 'contacts', 'recruiter_id': 5}, 'in_work', ['recruiter'])
        self.assertEqual(caught.exception.code, 'HIRING_ALREADY_TAKEN')

    def test_in_work_needs_a_touch_and_fields(self):
        card = {'stage': 'in_work', 'recruiter_id': 7}
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'invited_office', ['owner'], has_touch=False)
        self.assertEqual(caught.exception.code, 'HIRING_NO_TOUCH')
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'invited_office', ['owner'])
        self.assertEqual(caught.exception.code, 'HIRING_NEED_OFFICE_DATE')
        self.check(card, 'invited_office', ['owner'], payload={'office_visit_at': '2026-11-01T10:00'})
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'refused', ['owner'], payload={'initiator': 'ours'})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_REASON')

    def test_office_to_training_only_when_came(self):
        card = {'stage': 'invited_office', 'recruiter_id': 7, 'substage': 'no_show'}
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'invited_training', ['owner'], payload={'group_id': 1})
        self.assertEqual(caught.exception.code, 'HIRING_NOT_CAME')
        self.check(dict(card, substage='came'), 'invited_training', ['owner'], payload={'group_id': 1})
        # Не пришёл — отказ с причиной можно оформить из любого подстатуса.
        self.check(card, 'refused', ['owner'], payload={'initiator': 'candidate', 'reason_id': 3})

    def test_who_may_do_what(self):
        with self.assertRaises(rules.TransitionError) as caught:
            self.check({'stage': 'on_line'}, 'hired', ['owner', 'lead'], payload={'shifts_done': 7})
        self.assertEqual(caught.exception.code, 'HIRING_FORBIDDEN')
        self.assertIn('руководитель отдела', caught.exception.message)
        with self.assertRaises(rules.TransitionError) as caught:
            self.check({'stage': 'on_line'}, 'hired', ['dept_head'], payload={'shifts_done': 6})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_SHIFTS')
        self.check({'stage': 'on_line'}, 'hired', ['dept_head'], payload={'shifts_done': 7})
        self.check({'stage': 'invited_training'}, 'on_training', ['trainer'])
        with self.assertRaises(rules.TransitionError):
            self.check({'stage': 'refused'}, 'in_work', ['owner'])
        self.check({'stage': 'dropped'}, 'in_work', ['lead'])

    def test_drop_needs_initiator_reason_day(self):
        card = {'stage': 'on_training'}
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'dropped', ['trainer'], payload={'initiator': 'self', 'reason_id': 1})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_DAY')
        self.check(card, 'dropped', ['trainer'], payload={'initiator': 'we', 'reason_id': 1, 'day': 3})
        self.assertEqual(rules.drop_stage_for('on_line'), 'line')
        self.assertEqual(rules.drop_stage_for('on_training'), 'training')

    def test_actor_kinds(self):
        caps = {'lead': False, 'recruiter': True, 'headed_ids': [5], 'super_admin': False}
        card = {'recruiter_id': 7, 'group_id': 3, 'department_id': 5}
        self.assertEqual(rules.actor_kinds(caps, card, 7, trainer_group_ids=[3]), {'recruiter', 'owner', 'trainer', 'dept_head'})
        self.assertEqual(rules.actor_kinds(caps, card, 8, trainer_group_ids=[]), {'recruiter', 'dept_head'})
        self.assertIn('lead', rules.actor_kinds({'lead': True, 'headed_ids': []}, card, 8))
        self.assertIn('dept_head', rules.actor_kinds({'super_admin': True, 'headed_ids': []}, card, 8))


class AccessTests(unittest.TestCase):
    def test_lead_circle(self):
        self.assertTrue(access.is_lead(ctx(role='super_admin')))
        self.assertTrue(access.is_lead(ctx(headed_codes=['hr'])))
        self.assertTrue(access.is_lead(ctx(department_code='hr', role='hr_manager')))
        self.assertTrue(access.is_lead(ctx(grant='lead')))
        self.assertFalse(access.is_lead(ctx(grant='staff')))

    def test_recruiters_and_remote_phones(self):
        remote = ctx(grant='remote')
        self.assertTrue(access.can_open_section(remote))
        self.assertTrue(access.is_remote(remote))
        self.assertFalse(access.sees_phone(remote, {'recruiter_id': None}))
        self.assertFalse(access.sees_phone(remote, {'recruiter_id': 8}))
        self.assertTrue(access.sees_phone(remote, {'recruiter_id': 7}))
        self.assertTrue(access.sees_phone(ctx(grant='staff'), {'recruiter_id': None}))
        self.assertFalse(access.can_upload(remote))
        self.assertFalse(access.can_manage(ctx(grant='staff')))

    def test_trainer_and_dept_head(self):
        self.assertFalse(access.can_open_section(ctx(role='trainer')))
        self.assertTrue(access.can_open_section(ctx(role='trainer', trainer_group_ids=[4])))
        self.assertFalse(access.can_open_section(ctx(role='admin', headed_ids=[9], vacancy_department_ids=[3])))
        self.assertTrue(access.can_open_section(ctx(role='admin', headed_ids=[9], vacancy_department_ids=[9, 3])))
        self.assertFalse(access.can_see_pool(ctx(role='trainer', trainer_group_ids=[4])))

    def test_fired_sees_nothing(self):
        self.assertFalse(access.can_open_section(ctx(role='super_admin', status='fired')))
        self.assertFalse(access.can_open_section(ctx(grant='staff', status='dismissal')))

    def test_stranger(self):
        self.assertFalse(access.can_open_section(ctx()))
        caps = access.capabilities(ctx(grant='staff'))
        self.assertTrue(caps['recruiter'] and caps['take'] and caps['add_response'])
        self.assertFalse(caps['lead'] or caps['manage'] or caps['reports'])
        self.assertEqual(access.scope_label(ctx(grant='remote')), 'Удалённый рекрутер')


class UploadTests(unittest.TestCase):
    def test_csv_with_headers_and_duplicates(self):
        raw = 'ФИО;Телефон;Город;Комментарий\nИванов Иван;8 705 123 45 67;Алматы;ок\nПетров;+7 701 000 00 00;;\nСидоров;77051234567;Астана;повтор\nБез номера;;;\n'.encode('cp1251')
        contacts, errors, total = upload.parse(raw, 'база.csv')
        self.assertEqual(total, 4)
        self.assertEqual([c['phone'] for c in contacts], ['+77051234567', '+77010000000'])
        self.assertEqual(contacts[0]['full_name'], 'Иванов Иван')
        self.assertEqual(contacts[0]['city'], 'Алматы')
        self.assertEqual([e['error'] for e in errors], ['Повтор номера в файле', 'Пустой или неразборный номер'])
        self.assertEqual([e['row'] for e in errors], [4, 5])

    def test_xlsx_without_headers_uses_tz_order(self):
        book = Workbook()
        sheet = book.active
        sheet.append(['Асель', 7051112233, 'Шымкент', None])
        sheet.append([None, None, None, None])
        sheet.append(['Данияр', '8 (777) 555 66 77', '', 'вечером'])
        stream = io.BytesIO()
        book.save(stream)
        contacts, errors, total = upload.parse(stream.getvalue(), 'контакты.xlsx')
        self.assertEqual(total, 2)
        self.assertEqual(errors, [])
        self.assertEqual(contacts[0], {'row': 1, 'full_name': 'Асель', 'phone': '+77051112233', 'city': 'Шымкент', 'comment': None})
        self.assertEqual(contacts[1]['phone'], '+77775556677')
        self.assertEqual(contacts[1]['comment'], 'вечером')

    def test_wrong_format(self):
        with self.assertRaises(upload.UploadError):
            upload.parse(b'x', 'файл.pdf')


class HHTests(unittest.TestCase):
    def test_not_configured_without_env(self):
        self.assertIsNone(hh.config_from_env({}))
        self.assertIsNone(hh.config_from_env({'HH_ACCESS_TOKEN': 'x'}))
        self.assertEqual(hh.config_from_env({'HH_ACCESS_TOKEN': 'x', 'HH_USER_AGENT': 'iCore/1.0 (a@b)'})['employer_id'], None)

    def test_fetch_responses_with_fake_transport(self):
        calls = []

        def transport(method, url, headers, params):
            calls.append((url, dict(params)))
            self.assertEqual(headers['Authorization'], 'Bearer tok')
            self.assertEqual(headers['HH-User-Agent'], 'iCore/1.0 (a@b)')
            if url.endswith('/me'):
                return 200, {'employer': {'id': '42'}}
            if '/vacancies/active' in url:
                return 200, {'items': [{'id': '100', 'name': 'Водитель'}], 'pages': 1}
            if url.endswith('/negotiations/response'):
                return 200, {'items': [
                    {'id': 'n1', 'created_at': '2026-10-29T12:00:00+0500',
                     'resume': {'id': 'r1', 'first_name': 'Иван', 'last_name': 'Иванов', 'area': {'name': 'Алматы'},
                                'alternate_url': 'https://hh.kz/resume/r1'}, 'vacancy': {'id': '100', 'name': 'Водитель'}},
                    {'id': 'n2', 'created_at': '2026-10-29T11:00:00+0500',
                     'resume': {'id': 'r2', 'first_name': 'Пётр', 'last_name': 'Петров',
                                'contact': [{'type': {'id': 'cell'}, 'value': {'formatted': '+7 (701) 000-00-00'}}]},
                     'vacancy': {'id': '100', 'name': 'Водитель'}},
                    {'id': 'old', 'created_at': '2026-10-01T11:00:00+0500', 'resume': {'id': 'r9'}},
                ]}
            if url.endswith('/resumes/r1'):
                return 200, {'id': 'r1', 'contact': [{'type': {'id': 'cell'}, 'value': {'country': '7', 'city': '705', 'number': '1234567'}}]}
            return 404, None

        config = {'token': 'tok', 'user_agent': 'iCore/1.0 (a@b)', 'employer_id': None}
        items, state, problems = hh.fetch_responses(config, {'since': '2026-10-20T00:00:00'}, transport=transport)
        self.assertEqual(problems, [])
        self.assertEqual([i['phone'] for i in items], ['+77051234567', '+77010000000'])
        self.assertEqual(items[0]['full_name'], 'Иванов Иван')
        self.assertEqual(items[0]['city'], 'Алматы')
        self.assertEqual(items[0]['ref'], 'https://hh.kz/resume/r1')
        self.assertEqual(items[0]['received_at'], datetime(2026, 10, 29, 12, 0))
        self.assertEqual(state['employer_id'], '42')
        self.assertEqual(state['seen'], ['n1', 'n2'])
        self.assertTrue(state['since'].startswith('2026-10-29T11:00'))
        # Второй опрос с закладкой: те же отклики не повторяются.
        items2, _state2, _problems = hh.fetch_responses(config, state, transport=transport)
        self.assertEqual(items2, [])

    def test_token_rejected(self):
        with self.assertRaises(hh.HHError):
            hh.fetch_responses({'token': 't', 'user_agent': 'u', 'employer_id': '1'}, {},
                               transport=lambda *_args: (403, {}))


class ReportFilterTests(unittest.TestCase):
    def test_month_and_cohort_bounds(self):
        filters = reports.parse_filters({'month': '2026-10', 'recruiters': '3,5,x', 'sources': '2'})
        self.assertEqual(filters['cohort_from'], datetime(2026, 10, 1))
        self.assertEqual(filters['cohort_to'], datetime(2026, 11, 1))
        self.assertEqual(filters['recruiters'], [3, 5])
        self.assertEqual(filters['sources'], [2])
        self.assertIsNone(filters['stage'])

    def test_period_overrides_month_and_stage_filter(self):
        filters = reports.parse_filters({'from': '2026-09-15', 'to': '2026-09-20', 'stage': 'on_line', 'stage_from': '2026-09-01'})
        self.assertIsNone(filters['month'])
        self.assertEqual(filters['cohort_to'], datetime(2026, 9, 21))
        self.assertEqual(filters['stage'], 'on_line')
        self.assertEqual(filters['stage_from'], datetime(2026, 9, 1))
        self.assertIsNone(filters['stage_to'])

    def test_recruiter_is_forced_for_non_lead(self):
        filters = reports.parse_filters({'recruiters': '1,2'}, forced_recruiter_id=9)
        self.assertEqual(filters['recruiters'], [9])

    def test_bad_month_falls_back_to_current(self):
        filters = reports.parse_filters({'month': 'oops'})
        self.assertEqual(filters['month'], rules.month_of(rules.now_almaty()))

    def test_rates(self):
        self.assertEqual(rules.through_rate(1, 3), 33.3)
        self.assertIsNone(rules.through_rate(1, 0))
        self.assertEqual(rules.month_bounds('2026-12'), (datetime(2026, 12, 1), datetime(2027, 1, 1)))


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT_PY.read_text(encoding='utf-8')
        cls.app = APP_JSX.read_text(encoding='utf-8')
        cls.database = DATABASE_PY.read_text(encoding='utf-8')
        cls.sources = SOURCES_PY.read_text(encoding='utf-8')
        cls.bell = BELL_JSX.read_text(encoding='utf-8')
        cls.scopes = SCOPES_PY.read_text(encoding='utf-8')

    def test_schema_blueprint_flag_and_scheduler(self):
        self.assertIn('self._init_hiring_schema_tx(cursor)', self.database)
        self.assertIn("TG_TABLE_NAME = 'hiring_notifications'", self.database)
        self.assertIn("('trg_bell_hiring_notifications_insert', 'hiring_notifications', 'AFTER INSERT', ''),", self.database)
        self.assertIn('build_hiring_blueprint(', self.bot)
        self.assertIn('build_hiring_external_blueprint(', self.bot)
        self.assertIn('"hiring_access": hiring_access,', self.bot)
        for name in ('olx', 'hh', 'next_contacts', 'auto_return', 'overdue'):
            self.assertIn("('%s', CronTrigger(" % name, self.bot)
        self.assertIn("id='hiring_%s' % _hiring_name", self.bot)

    def test_bell_source(self):
        self.assertIn("'hiring': ('hiring',),", self.sources)
        self.assertIn("'hiring': hiring,", self.sources)
        self.assertIn("'hiring'})", self.sources)
        self.assertIn("hiring: { label: 'Воронка найма'", self.bell)

    def test_partner_scope(self):
        self.assertIn("HIRING_LEADS = 'hiring_leads'", self.scopes)
        self.assertIn("'POST /api/hiring/external/responses'", self.scopes)

    def test_menu_guards_and_view(self):
        self.assertIn("const HiringView = lazyWithRetry(() => import('./components/hiring/HiringView'));", self.app)
        self.assertIn("userLike?.hiring_access === true", self.app)
        self.assertIn("(requestedViewFromUrl !== 'hiring' || canAccessHiringSection) &&", self.app)
        self.assertIn("if (view === 'hiring' && !canAccessHiringSection) {", self.app)
        self.assertIn("if (view === 'hiring' && canAccessHiringSection) return;", self.app)
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'hiring')"), 1)
        self.assertEqual(self.app.count('<span className="sidebar-text">Воронка найма</span>'), 1)
        self.assertIn("{view === 'hiring' && canAccessHiringSection && (", self.app)
        self.assertIn('|| canAccessHiringSection\n', self.app)
        self.assertEqual(self.app.count("if (nextView !== 'hiring') url.searchParams.delete('card');"), 2)
        self.assertIn("if (nextView === 'hiring' && Number(target)) {", self.app)
        self.assertIn("if (view !== 'hiring') setHiringFocusRequest(null);", self.app)
        trainer_block = self.app[self.app.index('const TRAINER_ALLOWED_VIEWS'):self.app.index(']);', self.app.index('const TRAINER_ALLOWED_VIEWS'))]
        self.assertIn("'hiring',", trainer_block)


if __name__ == '__main__':
    unittest.main()
