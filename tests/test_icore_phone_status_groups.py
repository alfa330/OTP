# -*- coding: utf-8 -*-
"""Статусы iCORE Phone по группе ОП (Основа / ЯР / Поток): серверная часть монолита.

ТЗ от 05.10.2026: у Основы основной статус «Активный», у ЯР и Потока — «Исход» с
автоматическим «Офлайн» после N минут без звонков, у Потока ещё «Автодозвон». Набор
статусов шлёт сервер (/api/operator/sip_settings → settings.status_profile), выброс в
«Офлайн» телефон шлёт обычным событием статуса, а сервер пишет его в отчёт раздела
«Ограничитель Перезвона».

Импортировать bot_schedule2.py/database.py нельзя (пул к боевой БД, tzset на Windows),
поэтому функции достаются через ast и исполняются с заглушками.
"""

import ast
import contextlib
import copy
import logging
import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from tests import source_cache

from oktell_guard import phone as icore_phone
from oktell_guard import queries as guard_queries
from op_wallboard import snapshot as wallboard_snapshot


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"
BOT_SOURCE = source_cache.read(BOT_PATH)
DB_SOURCE = source_cache.read(DB_PATH)


def _module_literal(source, name):
    """Значение литерала модульного уровня (set/dict/tuple) без исполнения модуля."""
    for node in source_cache.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} не найден")


def _class_literal(source, class_name, name):
    cls = next(n for n in source_cache.parse(source).body
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{class_name}.{name} не найден")


def _load_names(source, names, namespace, label):
    """Исполняет в namespace перечисленные функции и присваивания модульного уровня."""
    wanted = set(names)
    body = []
    for node in source_cache.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted:
            node = copy.deepcopy(node)
            if hasattr(node, 'decorator_list'):
                node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted:
                body.append(copy.deepcopy(node))
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, label, "exec"), namespace)
    missing = sorted(name for name in wanted if name not in namespace)
    if missing:
        raise AssertionError(f"не найдено: {missing}")
    return namespace


def _function_source(path, name, class_name=None):
    node = source_cache.function_node(path, name, class_name=class_name)
    return ast.get_source_segment(source_cache.read(path), node)


def _db_method(name):
    node = source_cache.function_copy(DB_PATH, name, class_name="Database")
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {'datetime': datetime, 'date': date, 'timedelta': timedelta,
                 'ZoneInfo': ZoneInfo}
    exec(compile(module, str(DB_PATH), "exec"), namespace)
    return namespace[name]


# ── Часы: новые ключи ───────────────────────────────────────────────────────────

class HoursKeysTests(unittest.TestCase):
    def test_new_working_statuses_count_as_worked_time(self):
        work = _module_literal(DB_SOURCE, 'SCHEDULE_AUTO_WORK_STATUS_KEYS')
        for key in ('исход', 'соединение', 'автодозвон'):
            self.assertIn(key, work, key)
        # Прежние ключи на месте — СЗоВ и Тез считаются как раньше.
        for key in ('готов', 'занят', 'занята', 'перезвон', 'зарезервировано'):
            self.assertIn(key, work, key)

    def test_offline_is_not_worked_nor_break_nor_training(self):
        """«Офлайн» — простой: по ТЗ это время не отработано и не перерыв."""
        for name in ('SCHEDULE_AUTO_WORK_STATUS_KEYS', 'SCHEDULE_AUTO_TALK_STATUS_KEYS',
                     'SCHEDULE_AUTO_BREAK_STATUS_KEYS'):
            self.assertNotIn('офлайн', _module_literal(DB_SOURCE, name), name)
        self.assertNotEqual(_module_literal(DB_SOURCE, 'SCHEDULE_AUTO_TRAINING_STATUS_KEY'), 'офлайн')
        self.assertNotEqual(_module_literal(DB_SOURCE, 'SCHEDULE_AUTO_NO_PHONE_STATUS_KEY'), 'офлайн')

    def test_dialing_is_not_talk(self):
        """«Соединение» — набор до ответа: в разговорное время (эффективность) не идёт."""
        talk = _module_literal(DB_SOURCE, 'SCHEDULE_AUTO_TALK_STATUS_KEYS')
        self.assertNotIn('соединение', talk)
        self.assertNotIn('автодозвон', talk)

    def test_labels_for_every_new_key(self):
        labels = _module_literal(DB_SOURCE, 'SCHEDULE_STATUS_KEY_LABELS')
        self.assertEqual(labels['исход'], 'Исход')
        self.assertEqual(labels['соединение'], 'Соединение')
        self.assertEqual(labels['автодозвон'], 'Автодозвон')
        self.assertEqual(labels['офлайн'], 'Офлайн')
        # Прежняя подпись Oktell-овского «перезвона» не тронута.
        self.assertEqual(labels['перезвон'], 'Перезвон')

    def test_offline_is_not_on_shift_in_hourly_fact(self):
        keys = _class_literal(DB_SOURCE, 'Database', '_HOURLY_NOT_ON_SHIFT_STATUS_KEYS')
        self.assertIn('офлайн', keys)
        for key in ('исход', 'соединение', 'автодозвон', 'перерыв', 'перезвон', 'тренинг'):
            self.assertNotIn(key, keys, key)

    def test_kick_key_matches_the_phone_contract(self):
        """Ключ выброса в отчёте раздела и ключ «не отработано» в часах — одно слово."""
        self.assertEqual(icore_phone.KICK_STATUS_KEY, 'офлайн')
        self.assertIn(icore_phone.KICK_STATUS_KEY,
                      _module_literal(DB_SOURCE, 'SCHEDULE_STATUS_KEY_LABELS'))


# ── Резолвер группы статусов ────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows=None, error=None):
        self.rows = list(rows or [])
        self.error = error
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.error:
            raise self.error

    def fetchall(self):
        return list(self.rows)


class StatusGroupResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.load = staticmethod(_db_method('_load_operator_status_groups_tx'))
        cls.public = staticmethod(_db_method('get_operator_status_groups'))

    def _run(self, rows, ids=(1,), as_of=date(2026, 10, 5)):
        cursor = _FakeCursor(rows)
        return self.load(object(), cursor, list(ids), as_of), cursor

    def test_sql_uses_the_group_ladder_with_deterministic_ties(self):
        _, cursor = self._run([])
        sql, params = cursor.calls[0]
        self.assertIn('ORDER BY gom.start_date DESC, gom.id DESC', sql)
        self.assertIn('gom.start_date <= %(day)s::date', sql)
        self.assertIn('gom.end_date IS NULL OR gom.end_date >= %(day)s::date', sql)
        self.assertIn('u.id = ANY(%(ids)s)', sql)
        self.assertIn('LEFT JOIN departments dep ON dep.id = u.department_id', sql)
        # «Сегодня» приходит из Python (Алмата): часы базы в UTC.
        self.assertNotIn('CURRENT_DATE', sql)
        self.assertEqual(params, {'day': date(2026, 10, 5), 'ids': [1]})

    def test_datetime_and_missing_date_become_a_date(self):
        _, cursor = self._run([], as_of=datetime(2026, 10, 5, 23, 59))
        self.assertEqual(cursor.calls[0][1]['day'], date(2026, 10, 5))
        _, cursor = self._run([], as_of=None)
        self.assertIsInstance(cursor.calls[0][1]['day'], date)

    def test_no_ids_no_query(self):
        result, cursor = self._run([], ids=())
        self.assertEqual(result, {})
        self.assertEqual(cursor.calls, [])

    def test_mapping(self):
        rows = [
            # Поток по группе.
            (1, 'operator', 'op', 14, 'Поток 1', 'op_potok', 'op_potok'),
            # Стажёр без группы — по направлению.
            (2, 'trainee', 'op', None, '', '', 'op_osnova'),
            # Группа на дату решает, даже если она не ОП: направление не смотрим.
            (3, 'operator', 'op', 13, 'Верификатор', 'op_verificator', 'op_potok'),
            # СВ — прежний набор, хоть и в группе ЯР.
            (4, 'sv', 'op', 15, 'ЯР', 'op_yandex_reg', 'op_yandex_reg'),
            # ЯР.
            (5, 'operator', 'op', 15, 'ЯР', 'op_yandex_reg', ''),
            # Не ОП вовсе.
            (6, 'operator', 'tez', 34, 'Линия', 'tez_line', 'tez_line'),
        ]
        result, _ = self._run(rows, ids=(1, 2, 3, 4, 5, 6))
        self.assertEqual(result[1], {
            'role': 'operator', 'department_code': 'op', 'group_id': 14,
            'group_name': 'Поток 1', 'group_model': 'op_potok',
            'direction_model': 'op_potok', 'status_group': 'potok'})
        self.assertEqual(result[2]['status_group'], 'osnova')
        self.assertIsNone(result[2]['group_id'])
        self.assertIsNone(result[3]['status_group'])
        self.assertIsNone(result[4]['status_group'])
        self.assertEqual(result[5]['status_group'], 'yar')
        self.assertIsNone(result[6]['status_group'])

    def test_public_wrapper_uses_its_own_cursor(self):
        cursor = _FakeCursor([(7, 'operator', 'op', 36, 'Основа', 'op_osnova', '')])
        load = self.load

        class _Db:
            @contextlib.contextmanager
            def _get_cursor(self):
                yield cursor

            def _load_operator_status_groups_tx(self, cur, ids, as_of):
                return load(self, cur, ids, as_of)

        self.assertEqual(self.public(_Db(), [], date(2026, 10, 5)), {})
        result = self.public(_Db(), [7, None], date(2026, 10, 5))
        self.assertEqual(result[7]['status_group'], 'osnova')


# ── /api/operator/sip_settings: status_profile ─────────────────────────────────

class _StubDb:
    def __init__(self, groups=None, error=None):
        self.groups = groups or {}
        self.error = error
        self.calls = []

    def get_operator_status_groups(self, ids, as_of):
        self.calls.append((list(ids), as_of))
        if self.error:
            raise self.error
        return {uid: self.groups[uid] for uid in ids if uid in self.groups}

    @contextlib.contextmanager
    def _get_cursor(self):
        yield object()


BOT_HELPERS = ('_env_bool', '_ICORE_PHONE_PROFILE_UNKNOWN', '_icore_phone_idle_rule',
               '_icore_phone_status_profile',
               '_record_icore_phone_idle_kick', '_icore_phone_kick_after_status_event')


def _bot_namespace(db, now=datetime(2026, 10, 5, 10, 0)):
    namespace = {
        'os': os, 'logging': logging, 'datetime': datetime, 'ZoneInfo': ZoneInfo,
        'db': db,
        '_current_almaty_datetime': lambda: now,
    }
    return _load_names(BOT_SOURCE, BOT_HELPERS, namespace, '<icore-phone-status-groups>')


class SipSettingsStatusProfileTests(unittest.TestCase):
    def setUp(self):
        self.rule = {'enabled': True, 'threshold_s': 240, 'warn_before_s': 45,
                     'groups': ['yar', 'potok']}
        patcher = mock.patch.object(guard_queries, 'get_phone_settings', create=True,
                                    side_effect=lambda cursor, code='op': dict(self.rule))
        self.get_settings = patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('ICORE_PHONE_STATUS_PROFILES', None)
        os.environ.pop('ICORE_PHONE_REPORT_CONNECTING', None)

    def test_endpoint_wires_status_profile_and_keeps_the_old_pins(self):
        body = _function_source(BOT_PATH, 'operator_sip_settings_endpoint')
        self.assertIn('"status_profile": _icore_phone_status_profile(requester_id, provider)', body)
        # Прежние литералы, которые сторожат другие тесты, не тронуты.
        for pinned in ('db.get_user_sip_account(requester_id)', '"autodial": autodial',
                       '"autodial_code"', '**main', '"provider": provider',
                       '"binotel": account.get("binotel")',
                       '"auto_answer": bool(account.get("auto_answer", False))',
                       '"auto_answer_delay"', '"dial_list": _dial_list_phone_settings(requester_id)',
                       "provider == 'binotel'"):
            self.assertIn(pinned, body, pinned)

    def _endpoint_settings(self, profile_result, line_account=None, seen=None):
        """Тело ручки, исполненное с заглушками: что уходит телефону в settings.

        line_account — регистрация на линии удалённого КЦ для сотрудника другого
        отдела (_dial_list_line_account); None — человек на линии не сидит."""
        node = copy.deepcopy(source_cache.function_node(BOT_PATH, 'operator_sip_settings_endpoint'))
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        unknown = object()
        seen = seen if seen is not None else {}

        class _Db:
            @staticmethod
            def get_user_sip_account(user_id):
                seen['own_account_read'] = True
                return {'provider': 'asterisk',
                        'main': {'server': 'pbx', 'password': 'p', 'number': '6735'}}

        def status_profile(user_id, provider):
            seen['profile_provider'] = provider
            return unknown if profile_result == 'unknown' else profile_result

        namespace = {
            'request': mock.Mock(method='GET'),
            'jsonify': lambda value: value,
            'logging': logging,
            'db': _Db,
            '_get_authenticated_requester': lambda: (5, {}, None),
            '_dial_list_line_account': lambda user_id: line_account,
            '_dial_list_phone_settings': lambda user_id: {'enabled': bool(line_account)},
            'SIP_AUTO_ANSWER_DELAY_DEFAULT': 3,
            '_ICORE_PHONE_PROFILE_UNKNOWN': unknown,
            '_icore_phone_status_profile': status_profile,
        }
        exec(compile(module, '<sip-settings-endpoint>', 'exec'), namespace)
        body, code = namespace['operator_sip_settings_endpoint']()
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'success')
        return body['settings']

    def test_employee_seated_on_a_remote_cc_line_registers_on_that_line(self):
        """Сотрудник другого отдела на линии удалённого КЦ (07.10.2026): телефону уходит
        учётка ЛИНИИ, а не телефония его отдела — её настройки даже не читаются."""
        line_account = {
            'provider': 'binotel',
            'main': {'username': 'lg905', 'password': 'pw', 'server': 'sip53.binotel.com',
                     'domain': 'sip53.binotel.com', 'auth_id': 'lg905', 'number': '905'},
            'autodial': None, 'autodial_code': '', 'fop2_enabled': False,
            'auto_answer': True, 'auto_answer_delay': 3, 'binotel': None,
        }
        seen = {}
        settings = self._endpoint_settings(None, line_account=line_account, seen=seen)
        self.assertEqual((settings['provider'], settings['number'], settings['server'], settings['username']),
                         ('binotel', '905', 'sip53.binotel.com', 'lg905'))
        self.assertIsNone(settings['autodial'])
        self.assertIs(settings['fop2_enabled'], False)
        self.assertIsNone(settings['binotel'])
        self.assertEqual(settings['dial_list'], {'enabled': True})
        # Набор статусов групп ОП считается по провайдеру ЛИНИИ: у Binotel его нет.
        self.assertEqual(seen['profile_provider'], 'binotel')
        self.assertNotIn('own_account_read', seen)
        # Привязки нет — путь прежний: настройки собственного отдела.
        seen = {}
        settings = self._endpoint_settings(None, seen=seen)
        self.assertEqual((settings['provider'], settings['number']), ('asterisk', '6735'))
        self.assertTrue(seen['own_account_read'])
        body = _function_source(BOT_PATH, 'operator_sip_settings_endpoint')
        self.assertIn('_dial_list_line_account(requester_id) or db.get_user_sip_account(requester_id)', body)

    def test_endpoint_has_three_states_of_the_key(self):
        """Объект — набор, null — набора точно нет, ключа нет — посчитать не удалось."""
        profile = {'group': 'yar'}
        settings = self._endpoint_settings(profile)
        self.assertIs(settings['status_profile'], profile)
        settings = self._endpoint_settings(None)
        self.assertIn('status_profile', settings)
        self.assertIsNone(settings['status_profile'])
        settings = self._endpoint_settings('unknown')
        self.assertNotIn('status_profile', settings)
        # Остальное при сбое набора уходит как обычно — регистрация не страдает.
        self.assertEqual(settings['number'], '6735')
        self.assertEqual(settings['provider'], 'asterisk')
        self.assertEqual(settings['dial_list'], {'enabled': False})

    def test_endpoint_compares_the_sentinel_by_identity(self):
        body = _function_source(BOT_PATH, 'operator_sip_settings_endpoint')
        self.assertIn('is _ICORE_PHONE_PROFILE_UNKNOWN', body)
        self.assertIn('del payload["settings"]["status_profile"]', body)

    def test_potok_operator_gets_the_profile_with_the_rule(self):
        db = _StubDb({5: {'status_group': 'potok'}})
        ns = _bot_namespace(db)
        profile = ns['_icore_phone_status_profile'](5, 'asterisk')
        self.assertEqual(profile, {
            'group': 'potok', 'group_label': 'Поток',
            'statuses': ['outbound', 'autodial', 'break', 'tech_break', 'training'],
            'start': 'outbound',
            'report_connecting': True,
            'idle_offline': {'enabled': True, 'threshold_s': 240, 'warn_before_s': 45},
        })
        # Группа — на сегодня по Алмате, из Python.
        self.assertEqual(db.calls, [([5], date(2026, 10, 5))])
        self.get_settings.assert_called_once()
        self.assertEqual(self.get_settings.call_args[0][1], 'op')

    def test_osnova_has_no_idle_offline(self):
        ns = _bot_namespace(_StubDb({5: {'status_group': 'osnova'}}))
        profile = ns['_icore_phone_status_profile'](5, 'asterisk')
        self.assertEqual(profile['statuses'], ['active', 'break', 'tech_break', 'training'])
        self.assertEqual(profile['start'], 'active')
        self.assertFalse(profile['idle_offline']['enabled'])

    def test_binotel_stays_legacy_without_touching_the_db(self):
        db = _StubDb({5: {'status_group': 'potok'}})
        ns = _bot_namespace(db)
        self.assertIsNone(ns['_icore_phone_status_profile'](5, 'binotel'))
        self.assertEqual(db.calls, [])

    def test_no_group_is_legacy(self):
        ns = _bot_namespace(_StubDb({5: {'status_group': None}}))
        self.assertIsNone(ns['_icore_phone_status_profile'](5, 'asterisk'))
        ns = _bot_namespace(_StubDb({}))
        self.assertIsNone(ns['_icore_phone_status_profile'](5, 'asterisk'))

    def test_any_failure_is_unknown_not_legacy_and_not_an_error(self):
        """Сбой — не «набора нет»: None телефон сохранил бы в реестр на следующую смену."""
        ns = _bot_namespace(_StubDb(error=RuntimeError('db down')))
        unknown = ns['_ICORE_PHONE_PROFILE_UNKNOWN']
        self.assertIsNotNone(unknown)
        self.assertNotIsInstance(unknown, dict)
        with self.assertLogs(level='ERROR'):
            self.assertIs(ns['_icore_phone_status_profile'](5, 'asterisk'), unknown)
        # Мусор вместо id — тоже сбой расчёта, а не исключение в ручку.
        ns = _bot_namespace(_StubDb({5: {'status_group': 'yar'}}))
        with self.assertLogs(level='ERROR'):
            self.assertIs(ns['_icore_phone_status_profile']('abc', 'asterisk'),
                          ns['_ICORE_PHONE_PROFILE_UNKNOWN'])

    def test_definite_answers_are_never_the_sentinel(self):
        ns = _bot_namespace(_StubDb({5: {'status_group': 'potok'}}))
        unknown = ns['_ICORE_PHONE_PROFILE_UNKNOWN']
        self.assertIsInstance(ns['_icore_phone_status_profile'](5, 'asterisk'), dict)
        self.assertIsNone(ns['_icore_phone_status_profile'](5, 'binotel'))
        self.assertIsNone(ns['_icore_phone_status_profile'](6, 'asterisk'))
        os.environ['ICORE_PHONE_STATUS_PROFILES'] = '0'
        self.assertIsNone(ns['_icore_phone_status_profile'](5, 'asterisk'))
        self.assertIsNot(ns['_icore_phone_status_profile'](5, 'asterisk'), unknown)

    def test_report_connecting_follows_the_env_on_every_call(self):
        ns = _bot_namespace(_StubDb({5: {'status_group': 'osnova'}}))
        profile = ns['_icore_phone_status_profile']
        self.assertIs(profile(5, 'asterisk')['report_connecting'], True)
        for value in ('0', 'false', 'off', ''):
            os.environ['ICORE_PHONE_REPORT_CONNECTING'] = value
            self.assertIs(profile(5, 'asterisk')['report_connecting'], False, value)
        # Без перезапуска: переменная читается на каждый запрос.
        os.environ['ICORE_PHONE_REPORT_CONNECTING'] = '1'
        self.assertIs(profile(5, 'asterisk')['report_connecting'], True)
        # Клапан трогает только отчёт о фазе: набор и правило те же.
        os.environ['ICORE_PHONE_REPORT_CONNECTING'] = '0'
        self.assertEqual(profile(5, 'asterisk')['statuses'],
                         ['active', 'break', 'tech_break', 'training'])

    def test_kill_switch(self):
        db = _StubDb({5: {'status_group': 'potok'}})
        ns = _bot_namespace(db)
        os.environ['ICORE_PHONE_STATUS_PROFILES'] = '0'
        self.assertIsNone(ns['_icore_phone_status_profile'](5, 'asterisk'))
        self.assertEqual(db.calls, [])
        os.environ['ICORE_PHONE_STATUS_PROFILES'] = '1'
        self.assertIsNotNone(ns['_icore_phone_status_profile'](5, 'asterisk'))

    def test_profile_survives_an_unavailable_guard_table_with_the_kick_paused(self):
        """Правило не прочиталось — набор статусов всё равно уходит, но выброс выключен.

        «Включено, 5 минут» по умолчанию превратило бы выключенное руководителем правило
        в действующее: телефон применяет его на лету и выбросил бы оператора в «Офлайн»."""
        self.get_settings.side_effect = RuntimeError('relation does not exist')
        ns = _bot_namespace(_StubDb({5: {'status_group': 'yar'}}))
        with self.assertLogs(level='ERROR'):
            profile = ns['_icore_phone_status_profile'](5, 'asterisk')
        self.assertEqual(profile['group'], 'yar')
        self.assertEqual(profile['statuses'], ['outbound', 'break', 'tech_break', 'training'])
        self.assertEqual(profile['idle_offline'], {
            'enabled': False,
            'threshold_s': icore_phone.IDLE_THRESHOLD_DEFAULT_S,
            'warn_before_s': icore_phone.WARN_BEFORE_DEFAULT_S,
        })

    def test_failed_rule_read_is_a_normalized_disabled_rule(self):
        self.get_settings.side_effect = RuntimeError('pool timeout')
        ns = _bot_namespace(_StubDb())
        with self.assertLogs(level='ERROR'):
            rule = ns['_icore_phone_idle_rule']()
        expected = icore_phone.normalize_phone_settings(None)
        expected['enabled'] = False
        self.assertEqual(rule, expected)
        # Общие умолчания модуля fallback не портит.
        self.assertTrue(icore_phone.normalize_phone_settings(None)['enabled'])

    def test_successful_rule_read_is_passed_through(self):
        ns = _bot_namespace(_StubDb())
        self.assertEqual(ns['_icore_phone_idle_rule'](), self.rule)
        self.rule['enabled'] = False
        self.assertFalse(ns['_icore_phone_idle_rule']()['enabled'])


# ── /api/operator/status_event: запись выброса ──────────────────────────────────

class KickHookTests(unittest.TestCase):
    def setUp(self):
        self.kicks = []
        self.inserted = True

        def _record(cursor, **kwargs):
            if isinstance(self.inserted, Exception):
                raise self.inserted
            self.kicks.append(kwargs)
            return self.inserted

        for name, side_effect in (
                ('get_phone_settings', lambda cursor, code='op': {
                    'enabled': True, 'threshold_s': 300, 'warn_before_s': 60,
                    'groups': ['yar', 'potok']}),
                ('record_phone_kick', _record)):
            patcher = mock.patch.object(guard_queries, name, create=True, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db = _StubDb({9: {'status_group': 'yar'}})
        self.ns = _bot_namespace(self.db)
        self.hook = self.ns['_icore_phone_kick_after_status_event']

    def test_kick_is_recorded_once_per_phone_event(self):
        at = datetime(2026, 10, 5, 11, 7, 3)
        ok = self.hook(9, 'офлайн', 'авто', at, 'GUID-1', {'duplicate': False, 'event_id': 1})
        self.assertTrue(ok)
        self.assertEqual(self.kicks, [{
            'user_id': 9, 'happened_at': at, 'client_key': 'phone|GUID-1',
            'department_code': 'op', 'status_group': 'yar', 'threshold_s': 300}])
        # Группа — на день события, а не на «сегодня».
        self.assertEqual(self.db.calls, [([9], date(2026, 10, 5))])

    def test_key_spelling_is_normalised(self):
        self.assertTrue(self.hook(9, ' Офлайн ', 'АВТО', datetime(2026, 10, 5, 11, 0), 'G', {}))
        self.assertEqual(len(self.kicks), 1)

    def test_noop_and_ordinary_offline_are_not_kicks(self):
        at = datetime(2026, 10, 5, 11, 0)
        # Повтор того же состояния — не новый выброс.
        self.assertFalse(self.hook(9, 'офлайн', 'авто', at, 'G1', {'duplicate': False, 'noop': True}))
        # «Офлайн» без пометки — перепосылка после сна, не выброс.
        self.assertFalse(self.hook(9, 'офлайн', None, at, 'G2', {}))
        self.assertFalse(self.hook(9, 'исход', 'авто', at, 'G3', {}))
        self.assertFalse(self.hook(9, 'выключен', None, at, 'G4', {}))
        self.assertEqual(self.kicks, [])

    def test_duplicate_delivery_is_left_to_the_unique_key(self):
        """Транспортный повтор события пишется тем же ключом — второй строки не даёт база."""
        at = datetime(2026, 10, 5, 11, 0)
        self.inserted = False
        self.assertFalse(self.hook(9, 'офлайн', 'авто', at, 'G1', {'duplicate': True}))
        self.assertEqual([k['client_key'] for k in self.kicks], ['phone|G1'])

    def test_aware_time_is_stored_as_naive_almaty(self):
        at = datetime(2026, 10, 5, 19, 30, tzinfo=timezone.utc)
        self.hook(9, 'офлайн', 'авто', at, 'G', {})
        self.assertEqual(self.kicks[0]['happened_at'], datetime(2026, 10, 6, 0, 30))
        self.assertEqual(self.db.calls[0][1], date(2026, 10, 6))

    def test_without_guid_the_key_is_operator_and_moment(self):
        at = datetime(2026, 10, 5, 11, 0)
        self.hook(9, 'офлайн', 'авто', at, None, {})
        self.assertEqual(self.kicks[0]['client_key'], 'phone|9|2026-10-05T11:00:00')

    def test_failures_never_escape(self):
        self.inserted = RuntimeError('insert failed')
        with self.assertLogs(level='ERROR'):
            self.assertFalse(self.hook(9, 'офлайн', 'авто', datetime(2026, 10, 5), 'G', {}))

    def test_unread_rule_stores_zero_threshold_not_the_default(self):
        """Порог в строке выброса — действовавший или 0, но не выдуманные 300 секунд."""
        guard_queries.get_phone_settings.side_effect = RuntimeError('pool timeout')
        with self.assertLogs(level='ERROR'):
            self.assertTrue(self.hook(9, 'офлайн', 'авто', datetime(2026, 10, 5, 9), 'G', {}))
        self.assertEqual(self.kicks[0]['threshold_s'], 0)
        self.assertEqual(self.kicks[0]['status_group'], 'yar')

    def test_kick_stores_the_threshold_in_effect(self):
        guard_queries.get_phone_settings.side_effect = lambda cursor, code='op': {
            'enabled': False, 'threshold_s': 600, 'warn_before_s': 60, 'groups': ['yar']}
        self.assertTrue(self.hook(9, 'офлайн', 'авто', datetime(2026, 10, 5, 9), 'G', {}))
        self.assertEqual(self.kicks[0]['threshold_s'], 600)

    def test_unknown_group_still_records_the_kick(self):
        self.ns['db'] = _StubDb(error=RuntimeError('group lookup failed'))
        with self.assertLogs(level='ERROR'):
            self.assertTrue(self.hook(9, 'офлайн', 'авто', datetime(2026, 10, 5, 9), 'G', {}))
        self.assertEqual(self.kicks[0]['status_group'], '')

    def test_endpoint_calls_the_hook_after_the_event_and_keeps_its_response(self):
        body = _function_source(BOT_PATH, 'operator_status_event_endpoint')
        append_at = body.index('db.append_operator_status_event(')
        hook_at = body.index('_icore_phone_kick_after_status_event(')
        reply_at = body.index('return jsonify({"status": "success", "result": result}), 200')
        self.assertLess(append_at, hook_at)
        self.assertLess(hook_at, reply_at)
        self.assertIn("data.get('state_note'), event_at_value", body)
        # Хук ничего не возвращает в ответ — ответ телефону прежний.
        self.assertNotIn('= _icore_phone_kick_after_status_event(', body)


# ── Табло: каталог статусов ─────────────────────────────────────────────────────

class WallboardCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = _load_names(BOT_SOURCE, ('_TEZ_WALLBOARD_STATUS_CATALOG', '_TEZ_WALLBOARD_STATUS_UNKNOWN',
                                      '_TEZ_WALLBOARD_CABINET_STATUS_KEYS',
                                      '_tez_wallboard_status_entry',
                                      '_TEZ_WALLBOARD_RECALL_TONE_KEY'),
                         {'re': re}, '<tez-wallboard-catalog>')
        cls.entry = staticmethod(ns['_tez_wallboard_status_entry'])
        cls.recall_tone = ns['_TEZ_WALLBOARD_RECALL_TONE_KEY']

    def test_new_phone_statuses_have_their_own_tones(self):
        self.assertEqual(self.entry('исход'), ('Исход', 'outgoing', 30))
        self.assertEqual(self.entry('соединение'), ('Соединение', 'connecting', 15))
        self.assertEqual(self.entry('автодозвон'), ('Автодозвон', 'autodial', 25))
        self.assertEqual(self.entry('офлайн'), ('Офлайн', 'idle_offline', 65))

    def test_new_outbound_shares_the_recall_block_with_the_old_one(self):
        self.assertEqual(self.entry('исход')[1], self.recall_tone)
        self.assertEqual(self.entry('перезвон')[1], self.recall_tone)

    def test_dialing_is_not_talking_and_idle_offline_is_not_logout(self):
        """«Соединение» как разговор дало бы ложный ответ входящего на табло ОП, а «Офлайн»
        как выход — потерянное время входа и снятую сверку линии."""
        self.assertNotEqual(self.entry('соединение')[1], 'talking')
        self.assertNotEqual(self.entry('офлайн')[1], 'offline')
        self.assertEqual(self.entry('выключен')[1], 'offline')

    def test_order_from_work_to_absence(self):
        weights = [self.entry(key)[2] for key in (
            'занят', 'соединение', 'готов', 'автодозвон', 'исход', 'тренинг',
            'тех причина', 'перерыв', 'офлайн', 'выключен')]
        self.assertEqual(weights, sorted(weights))


class OpWallboardCountsTests(unittest.TestCase):
    def test_autodial_and_dialing_count_as_online(self):
        self.assertEqual(wallboard_snapshot.ONLINE_STATUS_KEYS,
                         ('free', 'talking', 'autodial', 'connecting'))
        self.assertNotIn('connecting', wallboard_snapshot.PAUSE_STATUS_KEYS)
        self.assertNotIn('idle_offline', wallboard_snapshot.ONLINE_STATUS_KEYS)
        self.assertNotIn('idle_offline', wallboard_snapshot.PAUSE_STATUS_KEYS)

    def test_count_now_with_new_tones(self):
        rows = [{'in_roster': True, 'status_key': key}
                for key in ('autodial', 'free', 'talking', 'connecting', 'idle_offline', 'break')]
        now = wallboard_snapshot.count_now(rows)
        self.assertEqual(now['operators_total'], 6)
        self.assertEqual(now['operators_online'], 4)
        # Набирающий онлайн, но не «свободен» и не «в разговоре».
        self.assertEqual(now['operators_free'], 1)
        self.assertEqual(now['operators_talking'], 1)
        self.assertEqual(now['operators_on_break'], 1)
        self.assertEqual(now['operators_offline'], 0)

    def test_dialing_osnova_operator_stays_online(self):
        """Раньше на наборе оператор Основы оставался «Активным»; плитка не должна проседать."""
        def online(keys):
            return wallboard_snapshot.count_now(
                [{'in_roster': True, 'status_key': key} for key in keys])['operators_online']

        self.assertEqual(online(['free'] * 3), 3)
        self.assertEqual(online(['free', 'connecting', 'talking']), 3)
        # Вне состава набирающий, как и любой другой, в цифры не входит.
        self.assertEqual(wallboard_snapshot.count_now(
            [{'in_roster': False, 'status_key': 'connecting'}])['operators_online'], 0)

    def test_idle_offline_does_not_end_the_session(self):
        """Выброс — не выход: время входа считается от первого события дня как обычно."""
        catalog = {'офлайн': ('Офлайн', 'idle_offline', 65), 'выключен': ('Не в сети', 'offline', 70),
                   'исход': ('Исход', 'outgoing', 30)}

        def entry(key):
            return catalog.get(key, ('Нет событий', 'unknown', 90))

        day = date(2026, 10, 5)
        events = [(datetime(2026, 10, 5, 9, 0), 'офлайн'), (datetime(2026, 10, 5, 9, 5), 'исход')]
        self.assertEqual(wallboard_snapshot.entry_moment(events, day, entry), datetime(2026, 10, 5, 9, 0))


if __name__ == '__main__':
    unittest.main()
