# -*- coding: utf-8 -*-
"""Раздел «Жалобы»: кто видит и кто разбирает.

Правило видимости живёт в двух формах — access.can_view (карточка по прямой
ссылке) и queries.visibility_sql (список). Здесь они сверяются по строке SQL,
как у «Обращений» (tests/test_crm_access.py); прогон обеих форм на настоящем
PostgreSQL со схемой прода сделан до выката стендом (см. отчёт задачи #297).
"""

import unittest

from complaints import access, queries


def ctx(role='operator', user_id=10, department_id=1, department_code='szov',
        headed=(), headed_codes=(), groups=()):
    return {'user_id': user_id, 'name': 'Тест', 'role': role,
            'department_id': department_id, 'department_code': department_code,
            'headed_department_ids': list(headed), 'headed_department_codes': list(headed_codes),
            'group_ids': list(groups)}


def complaint(**overrides):
    item = {'id': 1, 'target': 'call_center', 'created_by': 10, 'creator_department_id': 1,
            'target_department_id': 367, 'employee_id': 40, 'responsible_id': 50,
            'creator_group_ids': [3], 'status': 'open', 'tg_message_id': 900}
    item.update(overrides)
    return item


ADMIN = ctx(role='super_admin', user_id=1)
AUTHOR = ctx(user_id=10, groups=())
NEIGHBOUR = ctx(user_id=11)
SZOV_SV = ctx(role='sv', user_id=20, groups=(3,))
SZOV_SV_OTHER = ctx(role='sv', user_id=21, groups=(4,))
SZOV_HEAD = ctx(role='admin', user_id=30, headed=(1,), headed_codes=('szov',))
OP_SV = ctx(role='sv', user_id=50, department_id=367, department_code='op', groups=(14,))
OP_SV_2 = ctx(role='sv', user_id=51, department_id=367, department_code='op', groups=(15,))
OP_HEAD = ctx(role='admin', user_id=60, department_id=367, department_code='op',
              headed=(367,), headed_codes=('op',))
OP_OPERATOR = ctx(user_id=40, department_id=367, department_code='op')
TEZ_SV = ctx(role='sv', user_id=80, department_id=560, department_code='tez', groups=(34,))
MARKETING = ctx(role='marketing_manager', user_id=90, department_id=1041,
                department_code='marketing')


class SectionGateTest(unittest.TestCase):
    def test_who_enters_the_section(self):
        """Раздел — для тех, кто разбирает, и для админа. Оператор СЗоВ заводит
        жалобу и видит ответ в «Обращениях» (решение владельца 29.09.2026)."""
        for me in (ADMIN, SZOV_SV, SZOV_HEAD, OP_SV, OP_HEAD):
            self.assertTrue(access.can_open_section(me), me['user_id'])
        # ТЭЗ КЦ в жалобах нет вовсе (владелец, 29.09.2026) — и его СВ раздел
        # не нужен: жалоб на его людей не бывает.
        for me in (AUTHOR, NEIGHBOUR, OP_OPERATOR, MARKETING, TEZ_SV):
            self.assertFalse(access.can_open_section(me), me['user_id'])
        self.assertFalse(access.can_use(TEZ_SV))

    def test_operator_still_uses_the_api_from_crm(self):
        """К API оператор ходит из «Обращений»: создать жалобу, открыть свою,
        ответить на вопрос группы. Закрыть ему API вместе с разделом значило
        бы отрезать весь цикл ТЗ."""
        self.assertTrue(access.can_use(AUTHOR))
        self.assertTrue(access.capabilities(AUTHOR)['can_use'])
        self.assertFalse(access.capabilities(AUTHOR)['can_open'])
        self.assertFalse(access.can_view_analytics(AUTHOR))
        # Рядовой ОП жалоб не принимает и не разбирает — API ему не нужен.
        for me in (OP_OPERATOR, MARKETING):
            self.assertFalse(access.can_use(me), me['user_id'])

    def test_who_creates(self):
        self.assertTrue(access.can_create(AUTHOR))
        self.assertTrue(access.can_create(SZOV_HEAD))
        self.assertTrue(access.can_create(ADMIN))
        self.assertFalse(access.can_create(OP_SV))

    def test_qr_gate_is_the_same_as_in_crm(self):
        self.assertTrue(access.requires_sensitive_qr(AUTHOR))
        self.assertFalse(access.requires_sensitive_qr(SZOV_SV))
        self.assertFalse(access.requires_sensitive_qr(SZOV_HEAD))


class ViewTest(unittest.TestCase):
    def test_author_sees_own(self):
        self.assertTrue(access.can_view(AUTHOR, complaint()))
        self.assertFalse(access.can_view(NEIGHBOUR, complaint()))

    def test_the_accused_never_sees_it(self):
        """Жалоба бывает и на СВ, и на главу — им её тоже не видно."""
        self.assertFalse(access.can_view(OP_OPERATOR, complaint()))
        self.assertFalse(access.can_view(OP_SV, complaint(employee_id=50)))
        self.assertFalse(access.can_view(OP_HEAD, complaint(employee_id=60)))
        self.assertFalse(access.can_view(AUTHOR, complaint(employee_id=10)))
        self.assertTrue(access.can_view(ADMIN, complaint(employee_id=1)))

    def test_department_of_the_accused_sees_it(self):
        self.assertTrue(access.can_view(OP_SV, complaint()))
        self.assertTrue(access.can_view(OP_SV_2, complaint()))
        self.assertTrue(access.can_view(OP_HEAD, complaint()))
        self.assertFalse(access.can_view(TEZ_SV, complaint()))

    def test_intake_side_sees_what_its_operators_filed(self):
        self.assertTrue(access.can_view(SZOV_SV, complaint()))
        self.assertTrue(access.can_view(SZOV_HEAD, complaint()))
        self.assertFalse(access.can_view(SZOV_SV_OTHER, complaint()))


class HandleTest(unittest.TestCase):
    def test_department_of_the_accused_handles(self):
        for me in (OP_SV, OP_SV_2, OP_HEAD, ADMIN):
            self.assertTrue(access.can_handle(me, complaint()), me['user_id'])

    def test_intake_side_sees_but_does_not_handle(self):
        """Жалобу на ОП разбирает ОП, даже если приняла её СЗоВ."""
        self.assertFalse(access.can_handle(SZOV_SV, complaint()))
        self.assertFalse(access.can_handle(SZOV_HEAD, complaint()))

    def test_operator_never_handles(self):
        self.assertFalse(access.can_handle(AUTHOR, complaint()))

    def test_complaints_without_a_department_belong_to_the_intake(self):
        rental = complaint(target='car_rental', target_department_id=None, employee_id=None,
                           responsible_id=None)
        self.assertTrue(access.can_handle(SZOV_SV, rental))
        self.assertTrue(access.can_handle(SZOV_HEAD, rental))
        self.assertFalse(access.can_handle(SZOV_SV_OTHER, rental))
        self.assertFalse(access.can_set_employee(SZOV_SV, rental))

    def test_work_can_be_explained_without_an_employee(self):
        """Сотрудника не нашли — СВ всё равно пишет, почему работать не с кем:
        иначе подтверждённая жалоба без сотрудника не закрылась бы никогда.
        Какие именно записи тогда можно, решает service.record_work."""
        self.assertTrue(access.can_record_work(OP_SV, complaint()))
        self.assertTrue(access.can_record_work(OP_SV, complaint(employee_id=None)))
        self.assertFalse(access.can_record_work(AUTHOR, complaint(employee_id=None)))
        rental = complaint(target='car_rental', target_department_id=None, employee_id=None)
        self.assertFalse(access.can_record_work(SZOV_SV, rental))

    def test_internal_discussion_is_for_handlers_only(self):
        self.assertTrue(access.can_see_internal(OP_SV, complaint()))
        self.assertFalse(access.can_see_internal(AUTHOR, complaint()))
        self.assertFalse(access.can_see_internal(SZOV_SV, complaint()))

    def test_write_to_group(self):
        self.assertTrue(access.can_write_to_group(AUTHOR, complaint()))
        self.assertFalse(access.can_write_to_group(AUTHOR, complaint(status='closed')))
        self.assertFalse(access.can_write_to_group(AUTHOR, complaint(tg_message_id=None)))

    def test_open_question_is_answered_even_after_the_result(self):
        """Итог группа могла поставить раньше, чем оператор ответил на вопрос."""
        closed = complaint(status='closed', question_open_at='2026-09-29T10:00:00')
        self.assertTrue(access.can_write_to_group(AUTHOR, closed))

    def test_handlers_do_not_write_as_the_operator(self):
        """Из iCORE в группу пишет только автор: сообщение СВ уходило бы с
        подписью «Ответ оператора» и закрывало бы чужой вопрос."""
        for me in (OP_SV, OP_HEAD, ADMIN):
            self.assertFalse(access.can_write_to_group(me, complaint()), me['user_id'])

    def test_settings_and_delete(self):
        self.assertTrue(access.can_manage_settings(SZOV_SV))
        self.assertFalse(access.can_manage_settings(OP_SV))
        self.assertTrue(access.can_delete(ADMIN))
        self.assertFalse(access.can_delete(SZOV_HEAD))


class DepartmentReviewTest(unittest.TestCase):
    """Жалобу на Яндекс проверяет любой супервайзер отдела оператора, а не
    только супервайзер его группы (владелец, 09.10.2026) — значит, любой из
    них её видит, разбирает и решает. Остальные жалобы без отдела — как были."""

    @staticmethod
    def yandex(**overrides):
        fields = dict(target='yandex', target_department_id=None, employee_id=None,
                      responsible_id=None, review_state='pending')
        fields.update(overrides)
        return complaint(**fields)

    def test_supervisor_of_a_neighbouring_group_reviews(self):
        for me in (SZOV_SV, SZOV_SV_OTHER, ctx(role='sv', user_id=22)):
            self.assertTrue(access.can_view(me, self.yandex()), me['user_id'])
            self.assertTrue(access.can_handle(me, self.yandex()), me['user_id'])
            self.assertTrue(access.can_review(me, self.yandex()), me['user_id'])

    def test_decided_complaint_stays_in_sight_of_the_department(self):
        """Решивший не теряет её из виду: review_state остаётся и после решения."""
        decided = self.yandex(review_state='resolved', status='closed')
        self.assertTrue(access.can_view(SZOV_SV_OTHER, decided))
        self.assertFalse(access.can_review(SZOV_SV_OTHER, decided))

    def test_other_departments_and_operators_do_not(self):
        for me in (TEZ_SV, OP_SV, NEIGHBOUR):
            self.assertFalse(access.can_review(me, self.yandex()), me['user_id'])
        self.assertFalse(access.can_view(TEZ_SV, self.yandex()))

    def test_supervisor_does_not_review_his_own(self):
        own = self.yandex(created_by=21, creator_group_ids=[])
        self.assertTrue(access.can_view(SZOV_SV_OTHER, own), 'своё видит как автор')
        self.assertFalse(access.can_review(SZOV_SV_OTHER, own))

    def test_complaints_without_review_keep_the_group_rule(self):
        """Аренда авто проверку не проходит: её по-прежнему разбирает СВ группы."""
        rental = complaint(target='car_rental', target_department_id=None, employee_id=None,
                           responsible_id=None)
        self.assertFalse(access.can_view(SZOV_SV_OTHER, rental))
        self.assertFalse(access.can_handle(SZOV_SV_OTHER, rental))

    def test_list_and_work_queue_say_the_same(self):
        rule = ('(c.review_state IS NOT NULL AND c.creator_department_id = %(viewer_department)s'
                ' AND c.created_by IS DISTINCT FROM %(viewer_id)s)')
        sql, params = queries.visibility_sql(SZOV_SV_OTHER)
        self.assertIn(rule, sql)
        self.assertEqual(params['viewer_department'], 1)
        sql, _params = queries.handling_sql(SZOV_SV_OTHER)
        self.assertIn('(c.target_department_id IS NULL AND %s)' % rule, sql)

    def test_task_rule_is_the_one_of_the_cooperation_tickets(self):
        rule = ' '.join(queries.reviewer_sql('viewer_id').split())
        self.assertIn('rv.department_id = c.creator_department_id', rule)
        self.assertIn('rv.id IS DISTINCT FROM c.created_by', rule)
        self.assertNotIn('group_operator_memberships', rule)


class VisibilitySqlTest(unittest.TestCase):
    """Вторая форма того же правила — для списка."""

    def test_admin_has_no_filter(self):
        self.assertEqual(queries.visibility_sql(ADMIN)[0], 'TRUE')

    def test_the_accused_is_cut_off_first(self):
        for me in (AUTHOR, OP_SV, OP_HEAD, SZOV_SV):
            sql, params = queries.visibility_sql(me)
            self.assertTrue(sql.startswith('(c.employee_id IS DISTINCT FROM %(viewer_id)s AND ('),
                            me['user_id'])
            self.assertEqual(params['viewer_id'], me['user_id'])

    def test_operator_sees_by_author_and_responsibility_only(self):
        sql, params = queries.visibility_sql(AUTHOR)
        self.assertIn('c.created_by = %(viewer_id)s', sql)
        self.assertIn('c.responsible_id = %(viewer_id)s', sql)
        self.assertNotIn('target_department_id', sql)
        self.assertNotIn('group_operator_memberships', sql)

    def test_supervisor_sees_department_and_own_groups(self):
        sql, params = queries.visibility_sql(OP_SV)
        self.assertIn('c.target_department_id = %(viewer_department)s', sql)
        self.assertEqual(params['viewer_department'], 367)
        self.assertIn('group_operator_memberships', sql)
        self.assertIn('gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE', sql)
        self.assertEqual(params['viewer_groups'], [14])

    def test_head_sees_both_sides(self):
        sql, params = queries.visibility_sql(OP_HEAD)
        self.assertIn('c.target_department_id = ANY(%(headed)s)', sql)
        self.assertIn('c.creator_department_id = ANY(%(headed)s)', sql)
        self.assertEqual(params['headed'], [367])

    def test_handling_sql_mirrors_can_handle(self):
        sql, _params = queries.handling_sql(SZOV_SV)
        # Приёмщик разбирает только жалобы без отдела.
        self.assertIn('(c.target_department_id IS NULL AND EXISTS', sql)
        sql, _params = queries.handling_sql(OP_SV)
        self.assertIn('c.target_department_id = %(viewer_department)s', sql)
        self.assertEqual(queries.handling_sql(ADMIN)[0], 'TRUE')


if __name__ == '__main__':
    unittest.main()
