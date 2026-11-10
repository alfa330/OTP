# -*- coding: utf-8 -*-
"""Сессия Oktell закрывается при подключении к обзвону (владелец 10.11.2026: «если у них
активная сессия октела есть, её нужно выкинуть в момент подключения к icore phone»).

Сервер только говорит телефону, кому это делать: флаг oktell_logout в блоке dial_list ответа
/api/operator/sip_settings. Настоящим SQL то же правило проверяет
tests/test_calls_api_postgres.py (нужен локальный Postgres); здесь — без базы, чтобы правило
сторожилось в любом прогоне. Люди выдуманы.
"""
import unittest
from pathlib import Path
from unittest import mock

from dial_list import service as dial_service

ROOT = Path(__file__).resolve().parents[1]


class _Service(dial_service.DialListService):
    """Настоящий phone_settings; база подменена четырьмя ответами."""

    def __init__(self, operator, department, personal=None, own_code=''):
        self._operator, self._department = operator, department
        self._personal, self._own_code = personal, own_code
        self.own_code_reads = 0

    def dial_operator(self, user_id, operator=None, member=dial_service.MEMBER_NOT_GIVEN):
        return self._operator

    def department_settings(self, department_id):
        return self._department

    def user_setting(self, user_id):
        return self._personal

    def _own_department_code(self, user_id):
        self.own_code_reads += 1
        return self._own_code


LINE = {'department_id': 1954, 'department_provider': 'binotel', 'line_member': True}
OWN = {'department_id': 1954, 'department_provider': 'binotel'}
DEPT = {'configured': True, 'enabled': True, 'portion_size': 20, 'profile': 'remote_cc', 'auto_dial': False}


def block(operator=LINE, department=DEPT, personal=None, own_code='szov', env=None):
    service = _Service(operator, department, personal, own_code)
    with mock.patch.dict('os.environ', {} if env is None else {dial_service.OKTELL_LOGOUT_ENV: env}):
        if env is None:
            import os
            os.environ.pop(dial_service.OKTELL_LOGOUT_ENV, None)
        return service.phone_settings(701), service


class OktellLogoutFlagTests(unittest.TestCase):
    def test_szov_operator_on_a_dial_out_line(self):
        settings, service = block()
        self.assertIs(settings['oktell_logout'], True)
        self.assertIs(settings['enabled'], True)
        self.assertIs(settings['switchable'], False)        # переключатель — только фронт-офису
        self.assertEqual(service.own_code_reads, 1)         # свой отдел читается один раз

    def test_other_departments_do_not_work_in_oktell(self):
        for code in ('front_office', 'hr', 'tez', 'op', ''):
            self.assertIs(block(own_code=code)[0]['oktell_logout'], False, code)

    def test_own_staff_of_the_line_department(self):
        """Сотрудник самого удалённого КЦ: чужой линии нет, свой отдел не читаем."""
        settings, service = block(operator=OWN)
        self.assertIs(settings['oktell_logout'], False)
        self.assertEqual(service.own_code_reads, 0)

    def test_only_when_dial_out_is_on_for_the_person(self):
        self.assertIs(block(personal=False)[0]['oktell_logout'], False)
        self.assertIs(block(department=dict(DEPT, enabled=False))[0]['oktell_logout'], False)
        self.assertIs(block(department=dict(DEPT, enabled=False), personal=True)[0]['oktell_logout'], True)

    def test_tez_autodial_line_counts_as_dial_out_too(self):
        self.assertIs(block(department=dict(DEPT, profile='tez'))[0]['oktell_logout'], True)

    def test_server_switch(self):
        for value in ('0', 'false', 'off', 'no'):
            self.assertIs(block(env=value)[0]['oktell_logout'], False, value)
        for value in ('1', 'true', ''):
            self.assertIs(block(env=value)[0]['oktell_logout'], True, value)

    def test_not_binotel_has_no_dial_out_block(self):
        settings, _service = block(operator={'department_id': 1, 'department_provider': 'asterisk'})
        self.assertEqual(settings, {'enabled': False})

    def test_codes_and_wiring(self):
        self.assertEqual(dial_service.OKTELL_LOGOUT_DEPARTMENT_CODES, ('szov',))
        # Блок уходит телефону как есть — монолит его не пересобирает.
        monolith = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('return _dial_list_service.phone_settings(int(user_id), member=_dial_list_line_member(user_id))',
                      monolith)
        self.assertIn('"dial_list": _dial_list_phone_settings(requester_id),', monolith)


if __name__ == '__main__':
    unittest.main()
