# -*- coding: utf-8 -*-
"""Удалённый КЦ: «Офлайн» за простой и меню статусов без «Активного» (владелец 16.11.2026: «в
удалённом КЦ операторов выкинуло за 3 минуты без звонков, и у этого направления не должно быть
активного, а только исход»).

Сервер только говорит телефону, кому и сколько: ключи idle_offline_sec, idle_offline_warn_sec и
no_active в блоке dial_list ответа /api/operator/sip_settings. Само правило исполняет телефон
(3.22.55+, docs/DIAL-IDLE-OFFLINE.md в его репозитории). Здесь — без базы. Люди выдуманы.
"""
import os
import unittest
from pathlib import Path
from unittest import mock

from dial_list import service as dial_service

ROOT = Path(__file__).resolve().parents[1]
RULE_ENVS = (dial_service.IDLE_OFFLINE_ENV, dial_service.IDLE_OFFLINE_WARN_ENV, dial_service.NO_ACTIVE_ENV)


class _Service(dial_service.DialListService):
    """Настоящий phone_settings; база подменена четырьмя ответами."""

    def __init__(self, operator, department, personal=None, own_code=''):
        self._operator, self._department = operator, department
        self._personal, self._own_code = personal, own_code

    def dial_operator(self, user_id, operator=None, member=dial_service.MEMBER_NOT_GIVEN):
        return self._operator

    def department_settings(self, department_id):
        return self._department

    def user_setting(self, user_id):
        return self._personal

    def _own_department_code(self, user_id):
        return self._own_code


LINE = {'department_id': 1954, 'department_provider': 'binotel', 'line_member': True}
OWN = {'department_id': 1954, 'department_provider': 'binotel'}
DEPT = {'configured': True, 'enabled': True, 'portion_size': 20, 'profile': 'remote_cc', 'auto_dial': False}


def block(operator=LINE, department=DEPT, personal=None, own_code='szov', env=None):
    """Блок dial_list для телефона. env — переменные правила; остальные из окружения убраны."""
    service = _Service(operator, department, personal, own_code)
    with mock.patch.dict('os.environ', dict(env or {})):
        for name in RULE_ENVS:
            if name not in (env or {}):
                os.environ.pop(name, None)
        return service.phone_settings(701)


def rule(settings):
    return settings['idle_offline_sec'], settings['idle_offline_warn_sec'], settings['no_active']


class RemoteCcIdleRuleTests(unittest.TestCase):
    def test_owner_rule_three_minutes_and_no_active(self):
        """Гость линии из СЗоВ: 3 минуты без звонка — «Офлайн», предупреждение за 30 с, без «Активного»."""
        settings = block()
        self.assertEqual(rule(settings), (180, 30, True))
        self.assertIs(settings['enabled'], True)
        # Типы — как их читает телефон: целые секунды и логический флаг.
        self.assertIs(type(settings['idle_offline_sec']), int)
        self.assertIs(type(settings['idle_offline_warn_sec']), int)
        self.assertIs(type(settings['no_active']), bool)

    def test_everyone_on_the_remote_cc_dial_out(self):
        """Правило — про направление, а не про отдел человека: свои сотрудники удалённого КЦ и
        гости линии из любого отдела."""
        self.assertEqual(rule(block(operator=OWN, own_code='')), (180, 30, True))
        for code in ('szov', 'front_office', 'hr', 'tez', 'op', ''):
            self.assertEqual(rule(block(own_code=code)), (180, 30, True), code)

    def test_only_when_dial_out_is_on_for_the_person(self):
        """Обычный телефон на линии удалённого КЦ (менеджер фронт-офиса выключил себе обзвон,
        отдел выключил обзвон) живёт прежним набором статусов."""
        self.assertEqual(rule(block(personal=False)), (0, 0, False))
        self.assertEqual(rule(block(department=dict(DEPT, enabled=False))), (0, 0, False))
        self.assertEqual(rule(block(department=dict(DEPT, enabled=False), personal=True)), (180, 30, True))

    def test_tez_autodial_keeps_its_own_statuses(self):
        """Автообзвон Тез: «Активный» — статус автоцикла, «Офлайн» — за итог, не заполненный в срок."""
        tez = dict(DEPT, profile='tez', auto_dial=True)
        self.assertEqual(rule(block(department=tez)), (0, 0, False))
        self.assertEqual(rule(block(department=tez, operator=OWN, own_code='')), (0, 0, False))

    def test_department_without_a_profile_is_remote_cc(self):
        legacy = {key: value for key, value in DEPT.items() if key != 'profile'}
        self.assertEqual(rule(block(department=legacy)), (180, 30, True))
        self.assertEqual(rule(block(department=dict(DEPT, profile=None))), (180, 30, True))

    def test_term_switch(self):
        env = dial_service.IDLE_OFFLINE_ENV
        self.assertEqual(rule(block(env={env: '0'})), (0, 0, True))            # выброс выключен, меню без «Активного»
        self.assertEqual(rule(block(env={env: '600'})), (600, 30, True))
        self.assertEqual(rule(block(env={env: ' 240 '})), (240, 30, True))
        self.assertEqual(rule(block(env={env: '99999'})), (3600, 30, True))    # не больше часа
        self.assertEqual(rule(block(env={env: '-5'})), (0, 0, True))
        for garbage in ('abc', '3m', '1.5'):
            self.assertEqual(rule(block(env={env: garbage})), (180, 30, True), garbage)
        self.assertEqual(rule(block(env={env: ''})), (180, 30, True))

    def test_warning_is_always_shorter_than_the_term(self):
        term, warn = dial_service.IDLE_OFFLINE_ENV, dial_service.IDLE_OFFLINE_WARN_ENV
        self.assertEqual(rule(block(env={warn: '0'})), (180, 0, True))         # без предупреждения
        self.assertEqual(rule(block(env={warn: '60'})), (180, 60, True))
        self.assertEqual(rule(block(env={warn: '500'})), (180, 179, True))
        self.assertEqual(rule(block(env={warn: 'abc'})), (180, 30, True))
        self.assertEqual(rule(block(env={term: '20'})), (20, 19, True))        # 30 по умолчанию не влезает в срок
        self.assertEqual(rule(block(env={term: '1'})), (1, 0, True))
        self.assertEqual(rule(block(env={term: '0', warn: '60'})), (0, 0, True))

    def test_no_active_switch(self):
        env = dial_service.NO_ACTIVE_ENV
        for value in ('0', 'false', 'off', 'no', 'OFF'):
            self.assertEqual(rule(block(env={env: value})), (180, 30, False), value)
        for value in ('1', 'true', ''):
            self.assertEqual(rule(block(env={env: value})), (180, 30, True), value)

    def test_not_binotel_has_no_dial_out_block(self):
        self.assertEqual(block(operator={'department_id': 1, 'department_provider': 'asterisk'}),
                         {'enabled': False})

    def test_defaults_and_wiring(self):
        self.assertEqual(dial_service.IDLE_OFFLINE_DEFAULT_SEC, 180)           # «за 3 минуты»
        self.assertEqual(dial_service.IDLE_OFFLINE_WARN_DEFAULT_SEC, 30)
        self.assertEqual(dial_service.IDLE_OFFLINE_MAX_SEC, 3600)
        self.assertEqual(dial_service.idle_offline_warn_seconds(0), 0)
        self.assertEqual(dial_service.idle_offline_warn_seconds(None), 0)
        # Блок уходит телефону как есть — монолит его не пересобирает; режим переключателя
        # («Удалённый КЦ» в меню статуса) отвечает тем же блоком.
        monolith = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('return _dial_list_service.phone_settings(int(user_id), member=_dial_list_line_member(user_id))',
                      monolith)
        self.assertIn('"dial_list": _dial_list_phone_settings(requester_id),', monolith)


if __name__ == '__main__':
    unittest.main()
