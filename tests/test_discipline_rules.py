# -*- coding: utf-8 -*-
"""«Дисциплина» (#254): чистые правила — лестница, окно, плашка, статусы, права, файлы.

Без базы и без Flask. Что они значат на настоящей схеме — в
tests/test_discipline_postgres.py (запускается с DISCIPLINE_TEST_PORT).
"""

import ast
import pathlib
import unittest
from unittest import mock
from datetime import date, datetime, timedelta

from discipline import access, catalog, files, queries, service

AT = datetime(2026, 10, 20, 12, 0)

# Правила ТЗ проверяются для всех ролей; выкладка «пока только супер-админам»
# (access.SUPER_ADMIN_ONLY) — в SuperAdminRolloutTests. Значение флага в коде
# запоминаем до того, как модуль его снимет.
ROLLOUT_IN_CODE = access.SUPER_ADMIN_ONLY


def setUpModule():
    access.SUPER_ADMIN_ONLY = False


def tearDownModule():
    access.SUPER_ADMIN_ONLY = ROLLOUT_IN_CODE


def rec(record_id, days_ago, weight=1, cancelled=False, department=1, title='Опоздание'):
    return {'id': record_id, 'occurred_at': AT - timedelta(days=days_ago), 'weight': weight,
            'cancelled': cancelled, 'department_id': department, 'type_title': title}


SAMPLE = catalog.normalize_settings({'ladder': list(catalog.LADDER_SAMPLE)})
TZ = catalog.normalize_settings({})


class LadderTests(unittest.TestCase):
    def test_default_ladder_is_the_tz_text(self):
        # Критерии приёмки 2–4 написаны под текст ТЗ п. 7.3: акт на третьем.
        self.assertEqual(catalog.DEFAULT_LADDER, ('explanation', 'explanation', 'act'))
        self.assertEqual(TZ['ladder'], ['explanation', 'explanation', 'act'])
        # В интерфейсе она без подписи «как в ТЗ» — «Основная», имя меняет руководитель.
        self.assertEqual(catalog.DEFAULT_SETTINGS['ladders'],
                         [{'id': 'main', 'name': 'Основная', 'steps': ['explanation', 'explanation', 'act']}])

    def test_sample_ladder_titles(self):
        # Скриншот заказчика: тренинг → объясн. №1 → №2 → №3 + акт.
        self.assertEqual(catalog.LADDER_SAMPLE, ('training', 'explanation', 'explanation', 'act'))
        steps = catalog.compute_steps([rec(1, 20), rec(2, 15), rec(3, 10), rec(4, 5)], lambda *_: SAMPLE)
        titles = [catalog.step_title(steps[i]['action'], steps[i]['explanation_no']) for i in (1, 2, 3, 4)]
        self.assertEqual(titles, ['Тренинг', 'Объясн. №1', 'Объясн. №2', 'Объясн. №3 + акт'])

    def test_tz_ladder_puts_act_on_third(self):
        steps = catalog.compute_steps([rec(1, 20), rec(2, 15), rec(3, 10)], lambda *_: TZ)
        self.assertEqual([steps[i]['action'] for i in (1, 2, 3)], ['explanation', 'explanation', 'act'])
        self.assertEqual(catalog.act_step(TZ['ladder']), 3)

    def test_last_step_repeats(self):
        steps = catalog.compute_steps([rec(i, 25 - i) for i in range(1, 7)], lambda *_: TZ)
        self.assertEqual(steps[6]['action'], 'act')

    def test_rolling_window_excludes_old_and_cancelled(self):
        steps = catalog.compute_steps([rec(1, 40), rec(2, 20, cancelled=True), rec(3, 10)], lambda *_: SAMPLE)
        self.assertNotIn(2, steps)
        # Запись 40-дневной давности вне окна 30 дней — третья снова «первая».
        self.assertEqual(steps[3]['ordinal'], 1)
        self.assertEqual(steps[3]['action'], 'training')

    def test_rolling_window_boundary_is_exclusive(self):
        boundary = {'id': 1, 'occurred_at': AT - timedelta(days=30), 'weight': 1, 'department_id': 1}
        current = {'id': 2, 'occurred_at': AT, 'weight': 1, 'department_id': 1}
        self.assertEqual(catalog.compute_steps([boundary, current], lambda *_: SAMPLE)[2]['ordinal'], 1)

    def test_calendar_month_resets_on_first(self):
        month = catalog.normalize_settings({'period_kind': 'month'})
        march = {'id': 1, 'occurred_at': datetime(2026, 3, 30, 10), 'weight': 1}
        april = {'id': 2, 'occurred_at': datetime(2026, 4, 1, 10), 'weight': 1}
        self.assertEqual(catalog.compute_steps([march, april], lambda *_: month)[2]['ordinal'], 1)
        self.assertEqual(catalog.compute_steps([march, april], lambda *_: SAMPLE)[2]['ordinal'], 2)

    def test_weight_moves_ladder_but_not_ordinal(self):
        steps = catalog.compute_steps([rec(1, 10), rec(2, 5, weight=2)], lambda *_: SAMPLE)
        # «Грубое считается за два»: номер — второй, ступень — третья.
        self.assertEqual((steps[2]['ordinal'], steps[2]['weighted']), (2, 3))
        self.assertEqual(steps[2]['action'], 'explanation')

    def test_same_moment_is_ordered_by_id(self):
        same = AT - timedelta(days=1)
        records = [{'id': 7, 'occurred_at': same, 'weight': 1}, {'id': 5, 'occurred_at': same, 'weight': 1}]
        steps = catalog.compute_steps(records, lambda *_: SAMPLE)
        self.assertEqual((steps[5]['ordinal'], steps[7]['ordinal']), (1, 2))


class PlateTests(unittest.TestCase):
    def test_tz_texts(self):
        plate = catalog.plate(catalog.next_step([], AT, SAMPLE))
        self.assertEqual((plate['tone'], plate['title']), ('gray', 'Нарушений за период нет'))
        self.assertEqual(plate['text'], 'Это будет первое — тренинг.')
        plate = catalog.plate(catalog.next_step([rec(1, 3)], AT, SAMPLE))
        self.assertEqual(plate['tone'], 'yellow')
        self.assertTrue(plate['title'].startswith('За период уже 1 нарушение (17.10, Опоздание)'))
        self.assertEqual(plate['text'], 'Это будет второе — объяснительная №1.')

    def test_act_plate_is_red(self):
        plate = catalog.plate(catalog.next_step([rec(1, 6), rec(2, 4)], AT, TZ))
        self.assertEqual(plate['tone'], 'red')
        self.assertIn('Это третье — потребуется акт', plate['text'])

    def test_control_goes_on_top(self):
        plate = catalog.plate(catalog.next_step([], AT, SAMPLE),
                              {'end_date': date(2026, 11, 3), 'reason': 'Акт А-2026-0001'})
        self.assertEqual(plate['title'], 'Оператор на особом контроле до 03.11.2026')
        self.assertIn('Обязательно назначение меры', plate['text'])

    def test_plural(self):
        self.assertEqual([catalog.plural_violations(n) for n in (1, 2, 5, 11, 21, 22)],
                         ['нарушение', 'нарушения', 'нарушений', 'нарушений', 'нарушение', 'нарушения'])


class StateTests(unittest.TestCase):
    def test_order_follows_tz(self):
        base = {'needs_explanation': True}
        self.assertEqual(catalog.record_state(base, 'act'), 'explanation_requested')
        received = dict(base, explanation_received_at=AT)
        self.assertEqual(catalog.record_state(received, 'act'), 'act_required')
        self.assertEqual(catalog.record_state(received, 'act', act_has_file=True), 'act_done')
        self.assertEqual(catalog.record_state(received, 'act', act_has_file=True, under_control=True),
                         'measure_required')
        approved = dict(received, measure_state='approved')
        self.assertEqual(catalog.record_state(approved, 'act', act_has_file=True, under_control=True),
                         'measure_applied')
        self.assertEqual(catalog.record_state(dict(approved, closed_at=AT), 'act'), 'closed')
        self.assertEqual(catalog.record_state(dict(approved, cancelled_at=AT), 'act'), 'cancelled')

    def test_training_step_needs_no_explanation(self):
        self.assertEqual(catalog.record_state({'needs_explanation': True}, 'training'), 'training_required')
        self.assertEqual(catalog.close_blockers({'needs_explanation': True}, 'training'), ['training'])
        self.assertEqual(catalog.record_state({'needs_explanation': True, 'training_id': 3}, 'training'),
                         'training_done')

    def test_type_can_switch_explanation_off(self):
        self.assertFalse(catalog.needs_explanation({'needs_explanation': False}, 'explanation'))
        self.assertEqual(catalog.close_blockers({'needs_explanation': False}, 'explanation'), [])

    def test_close_blockers_for_act_under_control(self):
        record = {'needs_explanation': True}
        self.assertEqual(catalog.close_blockers(record, 'act', under_control=True),
                         ['explanation', 'act', 'measure'])
        self.assertEqual(catalog.close_blockers(dict(record, refused_at=AT, measure_state='approved'), 'act',
                                                act_has_file=True, under_control=True), [])

    def test_working_days_skip_weekend(self):
        friday = datetime(2026, 10, 16, 15, 0)
        self.assertEqual(catalog.add_working_days(friday, 2), datetime(2026, 10, 20, 15, 0))

    def test_overdue(self):
        record = {'needs_explanation': True, 'created_at': datetime(2026, 10, 12, 9, 0)}
        self.assertTrue(catalog.explanation_overdue(record, 'explanation', SAMPLE, AT))
        self.assertFalse(catalog.explanation_overdue(dict(record, explanation_received_at=AT), 'explanation',
                                                     SAMPLE, AT))
        self.assertFalse(catalog.explanation_overdue(record, 'training', SAMPLE, AT))


class SettingsTests(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(catalog.SettingsError):
            catalog.normalize_settings({'ladder': []})
        with self.assertRaises(catalog.SettingsError):
            catalog.normalize_settings({'ladder': ['nap']})
        with self.assertRaises(catalog.SettingsError):
            catalog.normalize_settings({'period_days': 0})
        with self.assertRaises(catalog.SettingsError):
            catalog.normalize_settings({'period_kind': 'week'})
        result = catalog.normalize_settings({'period_days': '45', 'telegram': {'act': False, 'unknown': True}})
        self.assertEqual(result['period_days'], 45)
        self.assertFalse(result['telegram']['act'])
        self.assertNotIn('unknown', result['telegram'])

    def test_defaults_match_tz(self):
        self.assertEqual([t['title'] for t in catalog.DEFAULT_TYPES][:2],
                         ['Сон на рабочем месте', 'Посторонние сайты, игры, соцсети на рабочем ПК'])
        self.assertEqual(len(catalog.DEFAULT_TYPES), 9)
        self.assertEqual(len(catalog.DEFAULT_MEASURES), 7)
        self.assertEqual(catalog.DEFAULT_SETTINGS['control_days'], 30)
        self.assertEqual(catalog.DEFAULT_SETTINGS['explanation_days'], 2)


def ctx(role, *, user_id=10, department_id=1, headed=(), groups=()):
    return {'user_id': user_id, 'role': role, 'department_id': department_id,
            'headed_department_ids': list(headed), 'group_ids': list(groups),
            'section_departments': {'szov': 1, 'op': 2, 'tez': 3}}


class AccessTests(unittest.TestCase):
    def test_circles(self):
        self.assertEqual(access.scope(ctx('super_admin', department_id=None)), access.SCOPE_ALL)
        self.assertFalse(access.can_open_section(ctx('admin')))
        self.assertTrue(access.can_open_section(ctx('admin', headed=[1])))
        # Глава отдела вне раздела (Удалённый КЦ) — не руководитель «Дисциплины».
        self.assertFalse(access.can_open_section(ctx('admin', department_id=4, headed=[4])))
        self.assertEqual(access.scope(ctx('sv', groups=[5])), access.SCOPE_GROUPS)
        self.assertFalse(access.can_open_section(ctx('sv', department_id=4)))
        self.assertEqual(access.scope(ctx('trainee')), access.SCOPE_OWN)
        self.assertFalse(access.can_open_section(ctx('trainer')))

    def test_no_qr_for_anyone(self):
        # Владелец, 23.10.2026: «для операторов сделать доступ без qr». Правила
        # QR в разделе нет вовсе, и права его не обещают ни одной роли.
        self.assertFalse(hasattr(access, 'requires_sensitive_qr'))
        for who in (ctx('operator'), ctx('trainee'), ctx('sv', groups=[5]), ctx('admin', headed=[1])):
            self.assertNotIn('requires_qr', access.capabilities(who), who['role'])

    def test_supervisor_group_scope(self):
        sv = ctx('sv', groups=[5])
        mine = {'id': 1, 'role': 'operator', 'department_id': 1, 'group_ids': [5]}
        other = {'id': 2, 'role': 'operator', 'department_id': 1, 'group_ids': [6]}
        self.assertTrue(access.operator_in_scope(sv, mine))
        self.assertFalse(access.operator_in_scope(sv, other))
        self.assertFalse(access.operator_in_scope(sv, dict(mine, role='sv')))
        own = {'operator_id': 1, 'department_id': 1, 'created_by': 10, 'operator_group_ids': [5]}
        self.assertTrue(access.can_view_record(sv, own))
        self.assertTrue(access.can_edit_record(sv, own))
        self.assertFalse(access.can_cancel_record(sv, own))
        # Владелец: «СВ видит только свою группу» — человека перевели, его история
        # ушла к новому СВ, даже записи, которые прежний завёл сам.
        moved = dict(own, operator_group_ids=[6])
        self.assertFalse(access.can_view_record(sv, moved))
        self.assertFalse(access.can_edit_record(sv, moved))
        self.assertFalse(access.can_close_record(sv, moved))
        colleague = dict(own, created_by=99)
        self.assertTrue(access.can_view_record(sv, colleague))
        self.assertFalse(access.can_edit_record(sv, colleague))
        self.assertFalse(access.can_close_record(sv, colleague, under_control=True))
        self.assertTrue(access.can_close_record(sv, colleague))

    def test_supervisor_does_not_touch_approved_measure(self):
        sv = ctx('sv', groups=[5])
        record = {'operator_id': 1, 'department_id': 1, 'created_by': 10, 'operator_group_ids': [5]}
        self.assertTrue(access.can_propose_measure(sv, dict(record, measure_state='proposed'), under_control=True))
        # Утвердил руководитель — СВ не трогает; применена без утверждения — поправить может.
        approved = dict(record, measure_state='approved', measure_approved_by=100)
        self.assertFalse(access.can_propose_measure(sv, approved, under_control=True))
        self.assertTrue(access.can_propose_measure(sv, dict(record, measure_state='approved'), under_control=True))
        head = ctx('sv', headed=[1], department_id=1)
        self.assertTrue(access.can_propose_measure(head, approved))
        # Утверждённое удержание не меняет никто — оно могло уйти в выгрузку.
        money = dict(approved, measure_finance='yes')
        self.assertTrue(access.measure_locked(money))
        self.assertFalse(access.can_propose_measure(head, money))

    def test_operator_sees_only_own(self):
        operator = ctx('operator', user_id=50)
        self.assertTrue(access.can_view_record(operator, {'operator_id': 50, 'department_id': 1}))
        self.assertFalse(access.can_view_record(operator, {'operator_id': 51, 'department_id': 1}))
        self.assertFalse(access.can_see_internal(operator, {}))

    def test_head_of_two_departments(self):
        head = ctx('sv', headed=[1, 2], department_id=1)
        self.assertEqual(access.managed_department_ids(head), {1, 2})
        self.assertTrue(access.can_view_record(head, {'operator_id': 1, 'department_id': 2}))
        self.assertFalse(access.can_view_record(head, {'operator_id': 1, 'department_id': 3,
                                                       'operator_department_id': 3}))


class FileCheckTests(unittest.TestCase):
    def test_signature_must_match(self):
        name, ext, mime = files.check('act', filename='акт.pdf', data=b'%PDF-1.4' + b'0' * 20)
        self.assertEqual((name, ext, mime), ('акт.pdf', 'pdf', 'application/pdf'))
        with self.assertRaises(files.FileError):
            files.check('act', filename='акт.pdf', data=b'MZ' + b'0' * 20)
        with self.assertRaises(files.FileError):
            files.check('act', filename='акт.pdf', data=b'\x89PNG' + b'0' * 20)

    def test_kind_whitelist_and_limits(self):
        with self.assertRaises(files.FileError):
            files.check('act', filename='акт.docx', data=b'PK\x03\x04' + b'0' * 20)
        files.check('evidence', filename='запись.mp4', data=b'\x00\x00\x00\x18ftypisom' + b'0' * 20)
        big = b'%PDF' + b'0' * (catalog.FILE_KINDS['explanation']['max_bytes'])
        with self.assertRaises(files.FileError):
            files.check('explanation', filename='o.pdf', data=big)

    def test_name_without_extension_gets_it_from_signature(self):
        name, ext, _mime = files.check('explanation', filename='pdf', data=b'%PDF-1.4' + b'0' * 20)
        self.assertEqual(ext, 'pdf')
        self.assertTrue(name.endswith('.pdf'))

    def test_cyrillic_name_is_kept(self):
        self.assertEqual(files.safe_name('../../Объяснительная Иванова.jpg'), 'Объяснительная Иванова.jpg')


if __name__ == '__main__':
    unittest.main()


class TimeValueTests(unittest.TestCase):
    """Смена и тайм-код записи экрана — только время (владелец, 20.10.2026):
    набранное приводится к одному виду, свободный текст сервер не пускает."""

    def _refused(self, func, raw):
        with self.assertRaises(service.DisciplineError) as caught:
            func(raw)
        return caught.exception.code

    def test_shift(self):
        self.assertEqual(service._shift_value('9:00-18:00'), '09:00–18:00')
        self.assertEqual(service._shift_value(' 20:00 — 08:00 '), '20:00–08:00')   # ночная
        self.assertIsNone(service._shift_value(''))
        for raw in ('1234', '9-18', '24:00–08:00', 'Линия 2', '09:00–09:00'):
            self.assertEqual(self._refused(service._shift_value, raw), 'SHIFT_FORMAT', raw)

    def test_timecodes(self):
        self.assertEqual(service._timecodes_value('14:32-14:41; 9:05'), '14:32–14:41, 09:05')
        self.assertEqual(service._timecodes_value('14:32–14:32'), '14:32')
        self.assertEqual(service._timecodes_value('23:58–00:03'), '23:58–00:03')
        self.assertIsNone(service._timecodes_value('  '))
        for raw in ('видео 10:15', '–14:41', '25:00', '10:00, 10:01, 10:02, 10:03, 10:04, 10:05'):
            self.assertEqual(self._refused(service._timecodes_value, raw), 'EVIDENCE_NOTE', raw)


class SuperAdminRolloutTests(unittest.TestCase):
    """Владелец, 20.10.2026: «опубликовать на данный момент для суперадминов»."""

    def test_flag_is_on_until_the_section_opens(self):
        self.assertTrue(ROLLOUT_IN_CODE)

    def test_only_super_admin_opens_section(self):
        with mock.patch.object(access, 'SUPER_ADMIN_ONLY', True):
            self.assertTrue(access.can_open_section(ctx('super_admin', department_id=None)))
            for who in (ctx('admin', headed=[1]), ctx('sv', groups=[5]), ctx('operator'), ctx('trainee'), ctx('admin')):
                self.assertFalse(access.can_open_section(who), who['role'])

    def test_named_operator_enters_by_the_usual_rules(self):
        """Владелец, 23.10.2026: «открой данный раздел для … как оператору» — 56.
        Список снимает только флаг выкладки: дальше обычные правила ТЗ."""
        self.assertEqual(access.ROLLOUT_USER_IDS, frozenset({56, 202}))
        with mock.patch.object(access, 'SUPER_ADMIN_ONLY', True):
            operator = ctx('operator', user_id=56)
            self.assertTrue(access.can_open_section(operator))
            self.assertEqual(access.scope(operator), access.SCOPE_OWN)
            # Тот же профиль с другим id — закрыто; поимённый вне отделов раздела
            # или админ без отдела под рукой — тоже: правила ТЗ никто не отменял.
            for who in (ctx('operator', user_id=57), ctx('operator', user_id=56, department_id=4),
                        ctx('admin', user_id=56)):
                self.assertFalse(access.can_open_section(who), who)


class ManagerGrantTests(unittest.TestCase):
    """Владелец, 29.10.2026: «дай ей полный доступ только для отдела СзоВ» —
    202, СВ СЗоВ (access.MANAGER_GRANTS): весь отдел, как у главы, а не своя группа."""

    def test_grant_in_code(self):
        self.assertEqual(access.MANAGER_GRANTS, {202: ('szov',)})
        for user_id, codes in access.MANAGER_GRANTS.items():
            self.assertTrue(set(codes) <= set(catalog.SECTION_DEPARTMENT_CODES), user_id)
            # Пока стоит флаг, без списка выкладки допуск в раздел не пустит.
            if ROLLOUT_IN_CODE:
                self.assertIn(user_id, access.ROLLOUT_USER_IDS)

    def test_named_supervisor_runs_the_whole_szov(self):
        named = ctx('sv', user_id=202, groups=[5])
        self.assertEqual(access.managed_department_ids(named), {1})
        self.assertEqual(access.scope(named), access.SCOPE_MANAGER)
        self.assertFalse(access.is_supervisor(named))
        caps = access.capabilities(named)
        for key in ('can_create', 'can_manage', 'can_controls', 'can_settings', 'can_reports', 'can_audit'):
            self.assertTrue(caps[key], key)
        self.assertFalse(caps['is_operator'])
        # Человек чужой группы, запись завёл другой СВ — всё, что может глава.
        stranger = {'id': 1, 'role': 'operator', 'department_id': 1, 'group_ids': [6]}
        record = {'operator_id': 1, 'department_id': 1, 'created_by': 99, 'operator_department_id': 1,
                  'operator_group_ids': [6]}
        self.assertTrue(access.operator_in_scope(named, stranger))
        for check in (access.can_view_record, access.can_edit_record, access.can_cancel_record,
                      access.can_work_record, access.can_upload_explanation, access.can_approve_measure):
            self.assertTrue(check(named, record), check.__name__)
        self.assertTrue(access.can_close_record(named, record, under_control=True))
        self.assertTrue(access.can_propose_measure(named, dict(record, measure_state='approved',
                                                               measure_approved_by=100)))
        self.assertTrue(access.can_manage_department(named, 1))

    def test_only_szov(self):
        named = ctx('sv', user_id=202, groups=[5])
        for department in (2, 3):
            self.assertFalse(access.can_manage_department(named, department))
            self.assertFalse(access.operator_in_scope(named, {'id': 1, 'role': 'operator', 'department_id': department,
                                                              'group_ids': [5]}))
            elsewhere = {'operator_id': 1, 'department_id': department, 'operator_department_id': department,
                         'operator_group_ids': [5], 'created_by': 202}
            for check in (access.can_view_record, access.can_edit_record, access.can_cancel_record,
                          access.can_approve_measure):
                self.assertFalse(check(named, elsewhere), (department, check.__name__))
        # СЗоВ нет среди действующих отделов раздела — допуск ничего не даёт.
        closed = dict(named, section_departments={'op': 2, 'tez': 3})
        self.assertEqual(access.managed_department_ids(closed), set())
        self.assertFalse(access.can_open_section(closed))

    def test_others_keep_their_circles(self):
        self.assertEqual(access.scope(ctx('sv', user_id=203, groups=[5])), access.SCOPE_GROUPS)
        self.assertEqual(access.managed_department_ids(ctx('admin', user_id=203, headed=[2])), {2})
        # Глава ОП с допуском на СЗоВ — оба отдела.
        with mock.patch.object(access, 'MANAGER_GRANTS', {203: ('szov',)}):
            self.assertEqual(access.managed_department_ids(ctx('sv', user_id=203, headed=[2])), {1, 2})

    def test_grant_holders_by_department(self):
        sections = {'szov': 1, 'op': 2, 'tez': 3}
        self.assertEqual(access.granted_manager_ids(sections, [1]), {202})
        self.assertEqual(access.granted_manager_ids(sections, [2, 3]), set())
        self.assertEqual(access.granted_manager_ids({'op': 2}, [1]), set())
        with mock.patch.object(access, 'MANAGER_GRANTS', {7: ('op', 'TEZ'), 8: ('szov',)}):
            self.assertEqual(access.granted_manager_ids(sections, [3]), {7})
            self.assertEqual(access.granted_manager_ids(sections, [1, 2]), {7, 8})

    def test_rollout_flag_still_closes_the_section(self):
        with mock.patch.object(access, 'SUPER_ADMIN_ONLY', True), \
                mock.patch.object(access, 'ROLLOUT_USER_IDS', frozenset()):
            self.assertFalse(access.can_open_section(ctx('sv', user_id=202, groups=[5])))
        with mock.patch.object(access, 'SUPER_ADMIN_ONLY', True):
            self.assertTrue(access.can_open_section(ctx('sv', user_id=202, groups=[5])))

    def test_grant_holds_while_she_is_a_szov_supervisor(self):
        """Допуск — «супервайзеру СзоВ»: перевели в другой отдел или сменили роль —
        он молчит, и сервер отвечает так же, как меню (canAccessDisciplineSectionForUser)."""
        for who in (ctx('sv', user_id=202, department_id=4), ctx('admin', user_id=202),
                    ctx('trainer', user_id=202)):
            self.assertEqual(access.managed_department_ids(who), set(), who)
            self.assertFalse(access.can_open_section(who), who)
        # СВ ОП — обычный СВ своих групп; оператор СЗоВ — только свои записи.
        moved = ctx('sv', user_id=202, department_id=2, groups=[7])
        self.assertEqual(access.managed_department_ids(moved), set())
        self.assertEqual(access.scope(moved), access.SCOPE_GROUPS)
        self.assertEqual(access.scope(ctx('operator', user_id=202)), access.SCOPE_OWN)
        # Строка users с ролью «supervisor» — тот же СВ.
        row = {'user_id': 202, 'role': 'supervisor', 'department_id': 1, 'section_departments': {'szov': 1}}
        self.assertEqual(access.granted_department_ids(row), {1})

    def test_department_managers_without_a_database(self):
        """queries.department_managers там, где нет Postgres (CI): глава, затем
        поимённые — один раз, только работающие и только с действующим допуском."""
        sections = {'szov': 1, 'op': 2, 'tez': 3}
        cur = _Cursor([(202, 'sv', 1), (203, 'sv', 4), (204, 'admin', 1)])
        grants = {202: ('szov',), 203: ('szov',), 204: ('szov',), 1: ('szov',), 205: ('op',)}
        with mock.patch.object(queries, 'department_heads', return_value={1: 1}), \
                mock.patch.object(access, 'MANAGER_GRANTS', grants):
            self.assertEqual(queries.department_managers(cur, [1], sections=sections), [1, 202])
        sql, params = cur.queries[-1]
        self.assertIn("NOT IN ('fired', 'dismissal')", sql)
        # Главу второй раз не спрашиваем, допуск на ОП к СЗоВ не относится.
        self.assertEqual(params, ([202, 203, 204],))
        cur = _Cursor()
        with mock.patch.object(queries, 'department_heads', return_value={3: 300}), \
                mock.patch.object(access, 'MANAGER_GRANTS', {202: ('szov',)}):
            self.assertEqual(queries.department_managers(cur, [3], sections=sections), [300])
        self.assertEqual(cur.queries, [])

    def test_every_manager_notice_goes_through_department_managers(self):
        """Все места рассылки «руководителю» в service.py — через department_managers;
        голый department_heads — только «ответственный» за автоконтроль и строка
        руководителя в акте. Postgres-тесты рассылки в CI пропускаются, этот — нет."""
        tree = ast.parse(pathlib.Path(service.__file__).read_text(encoding='utf-8'))
        callers = {'department_heads': set(), 'department_managers': set()}
        for top in tree.body:
            if isinstance(top, ast.FunctionDef):
                for node in ast.walk(top):
                    if isinstance(node, ast.Attribute) and node.attr in callers:
                        callers[node.attr].add(top.name)
        self.assertEqual(callers['department_heads'], {'_auto_control', 'act_draft_context'})
        self.assertEqual(callers['department_managers'], {'_push_act_basis', 'settle_ladder', '_notify_new_record',
                                                          'record_refusal', '_notify_finance', 'run_reminders'})

    def test_act_leader_is_the_head_when_she_approves(self):
        """Разбор 29.10.2026: акт утверждает и поимённый руководитель, но строка
        «С актом ознакомлен руководитель отдела» — главы, а не СВ с допуском."""
        self.assertEqual(service.act_leader_id(202, 1), 1)
        self.assertIsNone(service.act_leader_id(202, None))
        # Как раньше: утвердил глава или супер-админ — он; не утверждён — глава.
        self.assertEqual(service.act_leader_id(1, 1), 1)
        self.assertEqual(service.act_leader_id(7, 1), 7)
        self.assertEqual(service.act_leader_id(None, 1), 1)
        with mock.patch.object(access, 'MANAGER_GRANTS', {1: ('szov',)}):
            self.assertEqual(service.act_leader_id(1, 1), 1)

    def test_act_draft_names_the_head_when_she_composes_and_approves(self):
        """Черновик акта без базы: составила и утвердила 202 — «Мною, супервайзером …»
        она, а «ознакомлен руководитель» — глава отдела, строка не пустеет."""
        people = {1: {'name': 'Глава Отдела', 'role': 'admin'}, 202: {'name': 'Поимённая', 'role': 'sv'},
                  50: {'name': 'Оператор', 'role': 'operator', 'gender': 'female'}}
        with mock.patch.object(queries, 'department_names', return_value={1: {'id': 1, 'name': 'СЗоВ'}}), \
                mock.patch.object(queries, 'department_heads', return_value={1: 1}), \
                mock.patch.object(queries, 'people', side_effect=lambda _cur, ids: {
                    i: people[i] for i in ids if i in people}), \
                mock.patch.object(queries, 'control_of_act', return_value=None):
            context = service.act_draft_context(
                None, {'user_id': 202, 'name': 'Поимённая'},
                {'id': 9, 'department_id': 1, 'approved_by': 202, 'operator_id': 50},
                {'created_by': 202, 'created_at': AT, 'occurred_at': AT},
                settings_for=lambda *_args: catalog.normalize_settings({}))
        self.assertEqual((context['composer_name'], context['composer_role'], context['head_name']),
                         ('Поимённая', 'supervisor', 'Глава Отдела'))


class _Cursor:
    """Курсор без базы: запоминает запросы и отдаёт заготовленные строки."""

    def __init__(self, rows=()):
        self.rows, self.queries = list(rows), []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return self.rows


class LadderLibraryTests(unittest.TestCase):
    """Лестницы отдела с названиями (владелец, 21.10.2026): по умолчанию одна
    «Основная» с порядком ТЗ, без подписи «как в ТЗ»; свои лестницы заводит
    руководитель, действует одна."""

    def test_default_is_one_named_ladder_in_tz_order(self):
        settings = catalog.normalize_settings({})
        self.assertEqual(settings['ladders'], [{'id': 'main', 'name': 'Основная', 'steps': list(catalog.LADDER_TZ)}])
        self.assertEqual((settings['active_ladder'], settings['ladder']), ('main', list(catalog.LADDER_TZ)))
        self.assertFalse(hasattr(catalog, 'LADDER_PRESETS'))

    def test_new_ladder_becomes_active_and_drives_counting(self):
        base = catalog.normalize_settings({})
        settings = catalog.normalize_settings({'ladders': base['ladders'] + [
            {'id': 'l1', 'name': '  Строгая  ', 'steps': ['explanation', 'act']}], 'active_ladder': 'l1'}, base=base)
        self.assertEqual([l['name'] for l in settings['ladders']], ['Основная', 'Строгая'])
        self.assertEqual(settings['ladder'], ['explanation', 'act'])
        self.assertEqual(catalog.counting_rules(settings)['ladder'], ['explanation', 'act'])
        renamed = catalog.normalize_settings({'ladders': [dict(l, name='Новое имя') if l['id'] == 'l1' else l
                                                          for l in settings['ladders']]}, base=settings)
        # Переименование — не правило счёта.
        self.assertEqual(catalog.counting_rules(renamed), catalog.counting_rules(settings))

    def test_names_are_required_and_unique(self):
        base = catalog.normalize_settings({})
        bad = (
            {'ladders': [{'id': 'main', 'name': ' ', 'steps': ['act']}], 'active_ladder': 'main'},
            {'ladders': [{'id': 'a', 'name': 'Мягкая', 'steps': ['act']}, {'id': 'b', 'name': 'мягкая', 'steps': ['act']}],
             'active_ladder': 'a'},
            {'ladders': [{'id': 'a', 'name': 'X' * 61, 'steps': ['act']}], 'active_ladder': 'a'},
            {'ladders': [{'id': 'a', 'name': 'Мягкая', 'steps': ['act']}], 'active_ladder': 'нет такой'},
            {'ladders': [{'id': 'A B', 'name': 'Мягкая', 'steps': ['act']}], 'active_ladder': 'A B'},
            {'ladders': []},
        )
        for raw in bad:
            with self.assertRaises(catalog.SettingsError, msg=str(raw)):
                catalog.normalize_settings(raw, base=base)

    def test_legacy_forms_keep_working(self):
        # Сохранено до названных лестниц: своя лестница отдела становится «Основной».
        stored = catalog.normalize_settings({}, base={'ladder': list(catalog.LADDER_SAMPLE)})
        self.assertEqual(stored['ladders'], [{'id': 'main', 'name': 'Основная', 'steps': list(catalog.LADDER_SAMPLE)}])
        # Прежняя форма запроса — ступени действующей лестницы.
        legacy = catalog.normalize_settings({'ladder': ['training', 'act']}, base=stored)
        self.assertEqual((legacy['ladder'], legacy['ladders'][0]['steps']), (['training', 'act'], ['training', 'act']))

