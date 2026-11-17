# -*- coding: utf-8 -*-
"""«Офлайн» за простой в «Исходе» у отдела продаж Тез КЦ: серверная часть правила.

Владелец 17.11.2026: «для обычных телефонов тез такси … если оператор в исходе бездействует в
течении 3 мин, то его переводило в оффлайн». Техподдержке Тез по его решению в тот же день —
прежний возврат в «Активный» (outbound_return). Вечером того же дня: «добавь возможность эту
настройку менять» и «выключить сейчас» — выключатель и срок живут в карточке отдела Тез КЦ
(«Настройки SIP»), по умолчанию правило выключено. Кому положено правило и какой срок, решает
сервер (/api/operator/sip_settings → settings.idle_offline), телефон 3.22.57+ только читает.

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
ENV_KEYS = ('TEZ_OP_IDLE_OFFLINE_SEC', 'TEZ_OP_IDLE_OFFLINE_WARN_SEC',
            'TEZ_OUTBOUND_RETURN_SEC', 'TEZ_OUTBOUND_RETURN_WARN_SEC', 'TEZ_OUTBOUND_RETURN_PRESENCE_SEC')
# Состав табло Тез: техподдержка (tez_line) — 5 и 6, отдел продаж (tez_op) — 9 и 10; отдел — 560.
PEOPLE = {'tp': [{'id': 5}, {'id': 6}], 'op': [{'id': 9}, {'id': 10}], 'department_id': 560}
ON = {'enabled': True, 'seconds': 180}


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
    exec(compile(module, '<icore-phone-idle-offline>', 'exec'), namespace)
    missing = sorted(name for name in wanted if name not in namespace)
    if missing:
        raise AssertionError('не найдено: %s' % missing)
    return namespace


class _Db:
    """Настройка отдела из «Настроек SIP»: что вернуть и кто спрашивал."""

    def __init__(self, rule=ON, error=None):
        self.rule, self.error, self.asked = rule, error, []

    def get_sip_department_idle_offline(self, department_id):
        self.asked.append(department_id)
        if self.error:
            raise self.error
        return self.rule


def _rules(people=None, error=None, seen=None, db=None):
    """(_icore_phone_idle_offline, _icore_phone_outbound_return) с составом табло и настройкой отдела
    из заглушек."""
    people = people if people is not None else PEOPLE

    def wallboard_people(day=None):
        if seen is not None:
            seen.append(day)
        if error:
            raise error
        return people

    namespace = {'os': os, 'logging': logging, '_tez_wallboard_people': wallboard_people,
                 'db': db if db is not None else _Db()}
    _load(('_env_int', 'ICORE_PHONE_IDLE_OFFLINE_MAX_SECONDS', 'ICORE_PHONE_OUTBOUND_RETURN_MAX_SECONDS',
           'ICORE_PHONE_OUTBOUND_RETURN_PRESENCE_SECONDS', '_icore_phone_idle_offline',
           '_icore_phone_outbound_return'), namespace)
    return namespace['_icore_phone_idle_offline'], namespace['_icore_phone_outbound_return']


def _rule(**kwargs):
    return _rules(**kwargs)[0]


class IdleOfflineRuleTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for key in ENV_KEYS:
            os.environ.pop(key, None)

    def test_sales_operator_gets_the_term_set_for_the_department(self):
        db = _Db({'enabled': True, 'seconds': 240})
        self.assertEqual(_rule(db=db)(9, 'binotel'), {'seconds': 240, 'warn_seconds': 30})
        # Идентификатор приходит и строкой (из токена) — правило то же.
        self.assertEqual(_rule(db=db)('10', 'binotel'), {'seconds': 240, 'warn_seconds': 30})
        self.assertEqual(db.asked, [560, 560])          # настройка отдела Тез КЦ, а не чья-то ещё

    def test_switched_off_or_never_set_means_no_rule(self):
        """«Выключить сейчас» (владелец, 17.11.2026 вечером): выключено и не задано — правила нет."""
        for rule in ({'enabled': False, 'seconds': 180}, {'enabled': None, 'seconds': 180}, {}, None,
                     {'enabled': True, 'seconds': 0}):
            self.assertIsNone(_rule(db=_Db(rule))(9, 'binotel'), rule)

    def test_the_old_environment_switch_no_longer_turns_it_on(self):
        """Срок и выключатель теперь в портале: переменная окружения правило не включает."""
        os.environ['TEZ_OP_IDLE_OFFLINE_SEC'] = '180'
        self.assertIsNone(_rule(db=_Db({'enabled': False, 'seconds': 180}))(9, 'binotel'))

    def test_support_and_strangers_get_no_rule_and_never_read_the_setting(self):
        """Техподдержке — «Активный», а не «Офлайн» (решение владельца 17.11.2026: «Оставить
        «Активный»»); человека без модели расчёта (глава, СВ) в составе нет вовсе. Настройку
        отдела читаем только для своих продавцов — не на каждый запрос каждого телефона."""
        db = _Db()
        for user_id in (5, 6, 77):
            self.assertIsNone(_rule(db=db)(user_id, 'binotel'), user_id)
        self.assertIsNone(_rule(people={'tp': [], 'op': [], 'department_id': 560}, db=db)(9, 'binotel'))
        self.assertIsNone(_rule(people={'tp': [{'id': 9}], 'department_id': 560}, db=db)(9, 'binotel'))
        self.assertEqual(db.asked, [])

    def test_without_the_tez_department_there_is_no_rule(self):
        db = _Db()
        self.assertIsNone(_rule(people={'op': [{'id': 9}], 'department_id': None}, db=db)(9, 'binotel'))
        self.assertIsNone(_rule(people={'op': [{'id': 9}]}, db=db)(9, 'binotel'))
        self.assertEqual(db.asked, [])

    def test_one_person_never_gets_both_rules(self):
        """«Активный» и «Офлайн» за один и тот же простой взаимоисключают друг друга: каждому
        из состава Тез — ровно одно правило, остальным — ни одного."""
        idle_offline, outbound_return = _rules()
        for user_id in (5, 6, 9, 10, 77):
            got = [rule for rule in (idle_offline(user_id, 'binotel'), outbound_return(user_id, 'binotel'))
                   if rule is not None]
            self.assertEqual(len(got), 0 if user_id == 77 else 1, user_id)

    def test_local_pbx_reads_neither_the_roster_nor_the_setting(self):
        seen, db = [], _Db()
        rule = _rule(seen=seen, db=db)
        self.assertIsNone(rule(9, 'asterisk'))
        self.assertIsNone(rule(9, None))
        self.assertIsNone(rule(9, ''))
        self.assertEqual(seen, [])
        self.assertEqual(db.asked, [])

    def test_term_is_clamped_and_warning_is_always_shorter(self):
        """Предупреждение «за N секунд» не может начаться раньше самого простоя: как у обзвона
        удалённого КЦ, оно всегда меньше срока; срок из базы держим в пределах телефона."""
        self.assertEqual(_rule(db=_Db({'enabled': True, 'seconds': 99999}))(9, 'binotel')['seconds'], 3600)
        self.assertEqual(_rule(db=_Db({'enabled': True, 'seconds': 20}))(9, 'binotel')['warn_seconds'], 19)
        self.assertEqual(_rule(db=_Db({'enabled': True, 'seconds': 1}))(9, 'binotel'),
                         {'seconds': 1, 'warn_seconds': 0})
        os.environ['TEZ_OP_IDLE_OFFLINE_WARN_SEC'] = '0'
        self.assertEqual(_rule()(9, 'binotel')['warn_seconds'], 0)
        os.environ['TEZ_OP_IDLE_OFFLINE_WARN_SEC'] = '45'
        self.assertEqual(_rule()(9, 'binotel')['warn_seconds'], 45)
        os.environ['TEZ_OP_IDLE_OFFLINE_WARN_SEC'] = 'полминуты'
        self.assertEqual(_rule()(9, 'binotel')['warn_seconds'], 30)
        os.environ['TEZ_OP_IDLE_OFFLINE_WARN_SEC'] = '-5'
        self.assertEqual(_rule()(9, 'binotel')['warn_seconds'], 0)

    def test_any_failure_means_no_rule_and_is_logged(self):
        """Сбой состава или чтения настройки не должен стоить оператору регистрации: правила нет."""
        with self.assertLogs(level='ERROR') as logs:
            self.assertIsNone(_rule(error=RuntimeError('boom'))(9, 'binotel'))
        self.assertIn('«Офлайн» за простой «Исхода»', logs.output[0])
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(_rule(db=_Db(error=RuntimeError('db down')))(9, 'binotel'))
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(_rule()('abc', 'binotel'))
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(_rule(people={'op': [{'id': 'x'}], 'department_id': 560})(9, 'binotel'))


class IdleOfflineEndpointTests(unittest.TestCase):
    def _settings(self, rule, provider='binotel'):
        node = source_cache.function_copy(BOT_PATH, 'operator_sip_settings_endpoint')
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        unknown = object()
        seen = {}

        class _Db:
            @staticmethod
            def get_user_sip_account(user_id):
                return {'provider': provider,
                        'main': {'server': 'sip.example', 'password': 'p', 'number': '920'}}

        def idle_offline(user_id, provider_):
            seen['args'] = (user_id, provider_)
            return rule

        namespace = {
            'request': mock.Mock(method='GET'),
            'jsonify': lambda value: value,
            'logging': logging,
            'db': _Db,
            '_get_authenticated_requester': lambda: (9, {}, None),
            '_dial_list_line_account': lambda user_id: None,
            '_dial_list_phone_settings': lambda user_id: {'enabled': False},
            '_client_cards_phone_settings': lambda user_id: {'enabled': False},
            '_icore_phone_outbound_return': lambda user_id, provider_: None,
            '_icore_phone_idle_offline': idle_offline,
            'SIP_AUTO_ANSWER_DELAY_DEFAULT': 2,
            '_ICORE_PHONE_PROFILE_UNKNOWN': unknown,
            '_icore_phone_status_profile': lambda user_id, provider_: None,
            '_icore_phone_sip_transport': lambda user_id, provider_, main: main,
        }
        exec(compile(module, '<sip-settings-endpoint>', 'exec'), namespace)
        body, code = namespace['operator_sip_settings_endpoint']()
        self.assertEqual(code, 200)
        return body['settings'], seen

    def test_the_rule_travels_to_the_phone_by_the_line_provider(self):
        rule = {'seconds': 180, 'warn_seconds': 30}
        settings, seen = self._settings(rule)
        self.assertIs(settings['idle_offline'], rule)
        self.assertEqual(seen['args'], (9, 'binotel'))
        _, seen = self._settings(None, provider='asterisk')
        self.assertEqual(seen['args'], (9, 'asterisk'))

    def test_no_rule_is_an_explicit_null(self):
        """Ключ есть всегда: по null телефон выключает правило у себя, а не помнит прежнее."""
        settings, _ = self._settings(None)
        self.assertIn('idle_offline', settings)
        self.assertIsNone(settings['idle_offline'])

    def test_rule_has_its_own_key_not_the_outbound_return_one(self):
        """Телефон 3.22.54–3.22.56 знает outbound_return и по нему ставит «Активный»: правило
        «Офлайн» обязано ехать отдельным ключом, иначе старый телефон поставил бы продавцу
        «Активный» вместо «Офлайн»."""
        settings, _ = self._settings({'seconds': 180, 'warn_seconds': 30})
        self.assertIsNone(settings['outbound_return'])
        self.assertNotIn('action', settings['idle_offline'])

    def test_everything_the_rule_calls_exists_in_the_monolith(self):
        """Функция исполняется здесь с заглушками — имена сверяем с самим модулем."""
        defined = set()
        for node in source_cache.parse(BOT_SOURCE).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for name in ('_icore_phone_idle_offline', '_env_int', '_tez_wallboard_people',
                     'ICORE_PHONE_IDLE_OFFLINE_MAX_SECONDS'):
            self.assertIn(name, defined, name)
        body = ast.get_source_segment(
            BOT_SOURCE, source_cache.function_node(BOT_PATH, 'operator_sip_settings_endpoint'))
        self.assertIn('"idle_offline": _icore_phone_idle_offline(requester_id, provider)', body)
        # Настройку отдела правило читает методом базы — он обязан существовать под этим именем.
        rule_body = ast.get_source_segment(
            BOT_SOURCE, source_cache.function_node(BOT_PATH, '_icore_phone_idle_offline'))
        self.assertIn('db.get_sip_department_idle_offline(department_id)', rule_body)
        self.assertIn('def get_sip_department_idle_offline(self, department_id)',
                      source_cache.read(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                     'db', 'sip.py')))


if __name__ == '__main__':
    unittest.main()
