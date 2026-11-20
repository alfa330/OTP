# -*- coding: utf-8 -*-
"""Возврат из «Исхода» в «Активный» у техподдержки Тез КЦ: серверная часть правила.

Решение владельца 16.11.2026: оператор техподдержки простоял в «Исходе» 3 минуты без набора
и разговора — телефон сам ставит «Активный». Кому положено правило и какой срок, решает
сервер (/api/operator/sip_settings → settings.outbound_return), телефон только читает.

Импортировать монолит нельзя (пул к боевой базе, tzset на Windows) — функция достаётся
через ast и исполняется с заглушками.
"""

import ast
import copy
import logging
import os
import unittest
from unittest import mock

from tests import source_cache


BOT_PATH = source_cache.monolith_flat_path()
BOT_SOURCE = source_cache.read(BOT_PATH)
ENV_KEYS = ('TEZ_OUTBOUND_RETURN_SEC', 'TEZ_OUTBOUND_RETURN_WARN_SEC', 'TEZ_OUTBOUND_RETURN_PRESENCE_SEC')


def _load(names, namespace):
    wanted = set(names)
    body = []
    for node in source_cache.parse(BOT_SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            node = copy.deepcopy(node)
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & wanted:
                body.append(copy.deepcopy(node))
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, '<icore-phone-outbound-return>', 'exec'), namespace)
    missing = sorted(name for name in wanted if name not in namespace)
    if missing:
        raise AssertionError('не найдено: %s' % missing)
    return namespace


def _rule(people=None, error=None, seen=None):
    """_icore_phone_outbound_return с составом табло Тез из заглушки."""
    people = people if people is not None else {'tp': [{'id': 5}, {'id': 6}], 'op': [{'id': 9}]}

    def wallboard_people(day=None):
        if seen is not None:
            seen.append(day)
        if error:
            raise error
        return people

    namespace = {'os': os, 'logging': logging, '_tez_wallboard_people': wallboard_people}
    _load(('_env_int', 'ICORE_PHONE_OUTBOUND_RETURN_MAX_SECONDS',
           'ICORE_PHONE_OUTBOUND_RETURN_PRESENCE_SECONDS', '_icore_phone_outbound_return'), namespace)
    return namespace['_icore_phone_outbound_return']


class OutboundReturnRuleTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for key in ENV_KEYS:
            os.environ.pop(key, None)

    def test_support_operator_gets_three_minutes_by_default(self):
        self.assertEqual(_rule()(5, 'binotel'),
                         {'seconds': 180, 'warn_seconds': 30, 'presence_seconds': 60})
        # Идентификатор приходит и строкой (из токена) — правило то же.
        self.assertEqual(_rule()('6', 'binotel')['seconds'], 180)

    def test_sales_and_strangers_get_no_rule(self):
        """Отделу продаж Тез возврат не положен: у них простой «Исхода» кончается «Офлайном»
        (_icore_phone_idle_offline, решение владельца 17.11.2026)."""
        self.assertIsNone(_rule()(9, 'binotel'))
        self.assertIsNone(_rule()(77, 'binotel'))
        # Человека без модели расчёта (глава, СВ) в составе нет вовсе.
        self.assertIsNone(_rule(people={'tp': [], 'op': []})(5, 'binotel'))

    def test_local_pbx_never_reads_the_tez_roster(self):
        seen = []
        rule = _rule(seen=seen)
        self.assertIsNone(rule(5, 'asterisk'))
        self.assertIsNone(rule(5, None))
        self.assertEqual(seen, [])

    def test_switch_off_and_term_come_from_the_environment(self):
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = '0'
        seen = []
        self.assertIsNone(_rule(seen=seen)(5, 'binotel'))
        self.assertEqual(seen, [])                       # выключено — состав не читаем
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = '300'
        self.assertEqual(_rule()(5, 'binotel'),
                         {'seconds': 300, 'warn_seconds': 30, 'presence_seconds': 60})

    def test_warning_never_exceeds_the_term(self):
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = '20'
        self.assertEqual(_rule()(5, 'binotel')['warn_seconds'], 20)
        os.environ['TEZ_OUTBOUND_RETURN_WARN_SEC'] = '0'
        self.assertEqual(_rule()(5, 'binotel')['warn_seconds'], 0)

    def test_presence_window_is_a_minute_unless_set(self):
        """Не задано — минута, каким бы ни был срок; явный 0 — «ставить всегда», без проверки.

        Проверка включена по умолчанию: у техподдержки автоприём, и «Активный» у пустого
        стола сам снял бы трубку."""
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = '240'
        self.assertEqual(_rule()(5, 'binotel')['presence_seconds'], 60)
        os.environ['TEZ_OUTBOUND_RETURN_PRESENCE_SEC'] = '0'
        self.assertEqual(_rule()(5, 'binotel')['presence_seconds'], 0)
        os.environ['TEZ_OUTBOUND_RETURN_PRESENCE_SEC'] = '120'
        self.assertEqual(_rule()(5, 'binotel')['presence_seconds'], 120)
        os.environ['TEZ_OUTBOUND_RETURN_PRESENCE_SEC'] = 'минута'
        self.assertEqual(_rule()(5, 'binotel')['presence_seconds'], 60)

    def test_garbage_in_the_environment_falls_back_and_is_clamped(self):
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = 'три минуты'
        self.assertEqual(_rule()(5, 'binotel')['seconds'], 180)
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = '999999'
        rule = _rule()(5, 'binotel')
        self.assertEqual((rule['seconds'], rule['presence_seconds']), (3600, 60))
        os.environ['TEZ_OUTBOUND_RETURN_PRESENCE_SEC'] = '999999'
        self.assertEqual(_rule()(5, 'binotel')['presence_seconds'], 3600)
        os.environ['TEZ_OUTBOUND_RETURN_SEC'] = '-5'
        self.assertIsNone(_rule()(5, 'binotel'))

    def test_any_failure_means_no_rule_and_is_logged(self):
        """Сбой состава не должен стоить оператору регистрации: правила просто нет."""
        with self.assertLogs(level='ERROR') as logs:
            self.assertIsNone(_rule(error=RuntimeError('boom'))(5, 'binotel'))
        self.assertIn('возврата из «Исхода»', logs.output[0])
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(_rule()('abc', 'binotel'))


class OutboundReturnEndpointTests(unittest.TestCase):
    def _settings(self, rule):
        node = source_cache.function_copy(BOT_PATH, 'operator_sip_settings_endpoint')
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        unknown = object()
        seen = {}

        class _Db:
            @staticmethod
            def get_user_sip_account(user_id):
                return {'provider': 'binotel',
                        'main': {'server': 'sip.example', 'password': 'p', 'number': '915'}}

        def outbound_return(user_id, provider):
            seen['args'] = (user_id, provider)
            return rule

        namespace = {
            'request': mock.Mock(method='GET'),
            'jsonify': lambda value: value,
            'logging': logging,
            'db': _Db,
            '_get_authenticated_requester': lambda: (5, {}, None),
            '_dial_list_line_account': lambda user_id: None,
            '_dial_list_phone_settings': lambda user_id: {'enabled': False},
            '_dial_list_outgoing_routes': lambda user_id: None,
            '_client_cards_phone_settings': lambda user_id: {'enabled': False},
            '_icore_phone_outbound_return': outbound_return,
            '_icore_phone_idle_offline': lambda user_id, provider: None,
            'SIP_AUTO_ANSWER_DELAY_DEFAULT': 2,
            '_ICORE_PHONE_PROFILE_UNKNOWN': unknown,
            '_icore_phone_status_profile': lambda user_id, provider: None,
            '_icore_phone_sip_transport': lambda user_id, provider, main: main,
        }
        exec(compile(module, '<sip-settings-endpoint>', 'exec'), namespace)
        body, code = namespace['operator_sip_settings_endpoint']()
        self.assertEqual(code, 200)
        return body['settings'], seen

    def test_the_rule_travels_to_the_phone_by_the_line_provider(self):
        rule = {'seconds': 180, 'warn_seconds': 30, 'presence_seconds': 180}
        settings, seen = self._settings(rule)
        self.assertIs(settings['outbound_return'], rule)
        self.assertEqual(seen['args'], (5, 'binotel'))

    def test_no_rule_is_an_explicit_null(self):
        """Ключ есть всегда: по null телефон выключает правило у себя, а не помнит прежнее."""
        settings, _ = self._settings(None)
        self.assertIn('outbound_return', settings)
        self.assertIsNone(settings['outbound_return'])

    def test_everything_the_rule_calls_exists_in_the_monolith(self):
        """Функция исполняется здесь с заглушками — имена сверяем с самим модулем."""
        defined = set()
        for node in source_cache.parse(BOT_SOURCE).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for name in ('_icore_phone_outbound_return', '_env_int', '_tez_wallboard_people',
                     'ICORE_PHONE_OUTBOUND_RETURN_MAX_SECONDS',
                     'ICORE_PHONE_OUTBOUND_RETURN_PRESENCE_SECONDS'):
            self.assertIn(name, defined, name)
        body = ast.get_source_segment(
            BOT_SOURCE, source_cache.function_node(BOT_PATH, 'operator_sip_settings_endpoint'))
        self.assertIn('"outbound_return": _icore_phone_outbound_return(requester_id, provider)', body)


if __name__ == '__main__':
    unittest.main()
