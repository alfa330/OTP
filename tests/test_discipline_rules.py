# -*- coding: utf-8 -*-
"""«Дисциплина» (#254): чистые правила — лестница, окно, плашка, статусы, права, файлы.

Без базы и без Flask. Что они значат на настоящей схеме — в
tests/test_discipline_postgres.py (запускается с DISCIPLINE_TEST_PORT).
"""

import unittest
from unittest import mock
from datetime import date, datetime, timedelta

from discipline import access, catalog, files, service

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
        self.assertEqual(access.ROLLOUT_USER_IDS, frozenset({56}))
        with mock.patch.object(access, 'SUPER_ADMIN_ONLY', True):
            operator = ctx('operator', user_id=56)
            self.assertTrue(access.can_open_section(operator))
            self.assertEqual(access.scope(operator), access.SCOPE_OWN)
            # Тот же профиль с другим id — закрыто; поимённый вне отделов раздела
            # или админ без отдела под рукой — тоже: правила ТЗ никто не отменял.
            for who in (ctx('operator', user_id=57), ctx('operator', user_id=56, department_id=4),
                        ctx('admin', user_id=56)):
                self.assertFalse(access.can_open_section(who), who)


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

