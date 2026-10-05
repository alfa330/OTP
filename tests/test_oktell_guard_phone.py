# -*- coding: utf-8 -*-
"""Часть отдела продаж в «Ограничителе Перезвона» (ТЗ 05.10.2026).

В ОП нет Oktell: «выброс» в «Офлайн» делает сам iCORE Phone, а раздел держит
правило автоофлайна и показывает, кого сколько раз выкинуло. Проверяется здесь:

* схема — свои таблицы, порядок «таблица → индекс», Oktell-журнал не тронут;
* правило — нормализация (phone.py) и его хранение (queries.py);
* выбросы — идемпотентная запись, список сотрудников и отчёт на фейковых курсорах;
* HTTP — выбор части по ?department=, гейты, форма ответов.

Базы нет: пакет ничего не импортирует из database, запросы подменяются.
"""

import inspect
import json
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oktell_guard import access, phone, queries  # noqa: E402
from oktell_guard.schema import init_oktell_guard_schema  # noqa: E402

try:
    from flask import Flask
    from oktell_guard.routes import build_oktell_guard_blueprint
except ImportError:  # pragma: no cover
    Flask = None
    build_oktell_guard_blueprint = None


def _norm(sql):
    without_comments = ' '.join(part.split('--')[0] for part in str(sql).splitlines())
    return ' '.join(without_comments.split())


class _RecordingCursor:
    """Пишет каждый execute; отвечает заранее заданными строками по очереди."""

    def __init__(self, results=()):
        # results: список (columns, rows) — по одному на каждый execute с выборкой.
        self._results = list(results)
        self.executed = []
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self.executed.append((_norm(sql), params))
        if self._results:
            columns, rows = self._results.pop(0)
            self.description = [(name,) for name in columns]
            self._rows = list(rows)
        else:
            self.description = None
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


# ─────────────────────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────────────────────

class PhoneSchemaTest(unittest.TestCase):
    def statements(self):
        cursor = _RecordingCursor()
        init_oktell_guard_schema(cursor)
        return [sql for sql, _params in cursor.executed]

    def index_of(self, needle):
        return next(i for i, sql in enumerate(self.statements()) if needle in sql)

    def test_both_tables_are_created(self):
        joined = '\n'.join(self.statements())
        self.assertIn('CREATE TABLE IF NOT EXISTS oktell_guard_phone_settings', joined)
        self.assertIn('CREATE TABLE IF NOT EXISTS oktell_guard_phone_kicks', joined)

    def test_settings_row_for_op_is_seeded_after_its_table(self):
        table_at = self.index_of('CREATE TABLE IF NOT EXISTS oktell_guard_phone_settings')
        seed_at = self.index_of("INSERT INTO oktell_guard_phone_settings (department_code) VALUES ('op')")
        self.assertLess(table_at, seed_at)
        self.assertIn('ON CONFLICT (department_code) DO NOTHING', self.statements()[seed_at])

    def test_settings_defaults_match_the_rule_defaults(self):
        """Строка по умолчанию в базе и правило по умолчанию в коде — одно и то
        же: иначе свежий отдел получал бы одно, а «сбросить» — другое."""
        ddl = self.statements()[self.index_of('CREATE TABLE IF NOT EXISTS oktell_guard_phone_settings')]
        self.assertIn('enabled BOOLEAN NOT NULL DEFAULT TRUE', ddl)
        self.assertIn('threshold_s INTEGER NOT NULL DEFAULT %d' % phone.DEFAULT_PHONE_SETTINGS['threshold_s'], ddl)
        self.assertIn('warn_before_s INTEGER NOT NULL DEFAULT %d' % phone.DEFAULT_PHONE_SETTINGS['warn_before_s'], ddl)
        self.assertIn("DEFAULT '%s'::jsonb" % json.dumps(phone.DEFAULT_PHONE_SETTINGS['groups'],
                                                        separators=(',', ':')), ddl)
        self.assertIn('department_code VARCHAR(32) PRIMARY KEY', ddl)

    def test_kicks_are_idempotent_by_client_key(self):
        """ON CONFLICT (client_key) в record_phone_kick требует уникального
        индекса ровно по client_key — без условия WHERE."""
        table_at = self.index_of('CREATE TABLE IF NOT EXISTS oktell_guard_phone_kicks')
        unique_at = self.index_of('oktell_guard_phone_kicks_client_key_idx')
        sql = self.statements()[unique_at]
        self.assertLess(table_at, unique_at)
        self.assertIn('CREATE UNIQUE INDEX IF NOT EXISTS', sql)
        self.assertIn('ON oktell_guard_phone_kicks (client_key);', sql)
        self.assertNotIn('WHERE', sql)

    def test_kicks_indexes_for_list_and_report(self):
        joined = '\n'.join(self.statements())
        self.assertIn('ON oktell_guard_phone_kicks (user_id, happened_at DESC)', joined)
        self.assertIn('ON oktell_guard_phone_kicks (happened_at DESC)', joined)

    def test_kick_columns(self):
        ddl = self.statements()[self.index_of('CREATE TABLE IF NOT EXISTS oktell_guard_phone_kicks')]
        for fragment in ('id BIGSERIAL PRIMARY KEY',
                         'user_id INTEGER REFERENCES users(id) ON DELETE SET NULL',
                         "department_code VARCHAR(32) NOT NULL DEFAULT 'op'",
                         "status_group VARCHAR(16) NOT NULL DEFAULT ''",
                         'happened_at TIMESTAMP NOT NULL,',
                         'threshold_s INTEGER NOT NULL DEFAULT 0',
                         'client_key VARCHAR(128) NOT NULL,',
                         'received_at TIMESTAMP NOT NULL DEFAULT'):
            self.assertIn(fragment, ddl)

    def test_oktell_journal_is_untouched(self):
        """Серверная сверка Oktell читает oktell_guard_violations: строки
        телефона там «закрывали» бы чужие сегменты «Перезвона»."""
        for sql in self.statements():
            if 'oktell_guard_violations' in sql:
                self.assertNotIn('phone', sql)
        for name in ('violations_between', 'pending_violations', 'report', 'thresholds_by_sip'):
            self.assertNotIn('phone_kicks', inspect.getsource(getattr(queries, name)), name)


# ─────────────────────────────────────────────────────────────────────────────
# Правило (phone.py)
# ─────────────────────────────────────────────────────────────────────────────

class PhoneRuleTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(phone.normalize_phone_settings(None), {
            'enabled': True, 'threshold_s': 300, 'warn_before_s': 60, 'groups': ['yar', 'potok'],
        })

    def test_threshold_and_warning_are_clamped(self):
        rule = phone.normalize_phone_settings({'threshold_s': 5, 'warn_before_s': 999})
        self.assertEqual(rule['threshold_s'], 60)
        # Предупреждение не раньше, чем через полминуты простоя.
        self.assertEqual(rule['warn_before_s'], 30)
        self.assertEqual(phone.normalize_phone_settings({'threshold_s': 99999})['threshold_s'], 3600)
        self.assertEqual(phone.normalize_phone_settings({'warn_before_s': -5})['warn_before_s'], 0)
        self.assertEqual(phone.normalize_phone_settings({'threshold_s': 'мусор'})['threshold_s'], 300)

    def test_string_booleans_are_parsed(self):
        """bool('false') — True: строка из формы не должна включать выбросы."""
        self.assertIs(phone.normalize_phone_settings({'enabled': 'false'})['enabled'], False)
        self.assertIs(phone.normalize_phone_settings({'enabled': '0'})['enabled'], False)
        self.assertIs(phone.normalize_phone_settings({'enabled': 'true'})['enabled'], True)
        self.assertIs(phone.normalize_phone_settings({'enabled': False})['enabled'], False)

    def test_groups_only_from_the_idle_list(self):
        """Основы в правиле нет: «Исхода» у неё нет, считать нечего."""
        self.assertEqual(phone.normalize_groups(['potok', 'osnova', 'POTOK', 'yar']), ['yar', 'potok'])
        self.assertEqual(phone.normalize_groups([]), [])
        self.assertEqual(phone.normalize_groups(None), ['yar', 'potok'])
        self.assertEqual(phone.normalize_groups('potok'), ['potok'])
        # JSONB, прочитанный как текст.
        self.assertEqual(phone.normalize_groups('["potok"]'), ['potok'])

    def test_status_group_ladder(self):
        self.assertEqual(phone.status_group_for('operator', 'op_potok', 'op_osnova'), 'potok')
        # Действующее членство решает, даже если группа не ОП.
        self.assertIsNone(phone.status_group_for('operator', 'op_verificator', 'op_potok'))
        self.assertEqual(phone.status_group_for('trainee', '', 'op_yandex_reg'), 'yar')
        self.assertIsNone(phone.status_group_for('sv', 'op_potok', ''))

    def test_kick_detection_and_key(self):
        self.assertTrue(phone.is_kick_event(' Офлайн ', 'АВТО'))
        self.assertFalse(phone.is_kick_event('офлайн', ''))
        self.assertFalse(phone.is_kick_event('выключен', 'авто'))
        self.assertEqual(phone.kick_client_key('abc'), 'phone|abc')
        self.assertTrue(phone.kick_client_key('', 7, datetime(2026, 10, 5, 12, 0)).startswith('phone|7|2026-10-05T12:00'))

    def test_status_profile_payload(self):
        potok = phone.status_profile_payload('potok', {'threshold_s': 240})
        self.assertEqual(potok['statuses'], ['outbound', 'autodial', 'break', 'tech_break', 'training'])
        self.assertEqual(potok['start'], 'outbound')
        self.assertEqual(potok['idle_offline'], {'enabled': True, 'threshold_s': 240, 'warn_before_s': 60})
        osnova = phone.status_profile_payload('osnova', None)
        self.assertEqual(osnova['start'], 'active')
        self.assertFalse(osnova['idle_offline']['enabled'])
        self.assertFalse(phone.status_profile_payload('yar', {'groups': ['potok']})['idle_offline']['enabled'])
        self.assertFalse(phone.status_profile_payload('yar', {'enabled': False})['idle_offline']['enabled'])
        self.assertIsNone(phone.status_profile_payload(None))
        for profile in phone.STATUS_PROFILES.values():
            self.assertNotIn('offline', profile['statuses'])

    def test_status_profile_report_connecting_flag(self):
        """Серверный выключатель фазы «Соединение» в отчёте: по умолчанию она
        шлётся, и это всегда настоящий bool — телефон читает его как JSON-bool."""
        self.assertIs(phone.status_profile_payload('yar')['report_connecting'], True)
        self.assertIs(phone.status_profile_payload('yar', None, report_connecting=False)
                      ['report_connecting'], False)
        self.assertIs(phone.status_profile_payload('osnova', {'enabled': False}, 0)
                      ['report_connecting'], False)
        self.assertIs(phone.status_profile_payload('potok', report_connecting='да')
                      ['report_connecting'], True)
        # Флаг не трогает ни набор статусов, ни правило автоофлайна.
        on = phone.status_profile_payload('potok', {'threshold_s': 240})
        off = phone.status_profile_payload('potok', {'threshold_s': 240}, report_connecting=False)
        self.assertEqual(dict(on, report_connecting=None), dict(off, report_connecting=None))
        self.assertIsNone(phone.status_profile_payload('tez', None, report_connecting=False))

    def test_changes_are_validated_before_merging(self):
        """Ревью 05.10.2026 (G2): нормализация на непонятное отвечает значением
        ПО УМОЛЧАНИЮ, поэтому правку проверяем отдельно и отказываем."""
        good = {'enabled': 'нет', 'threshold_s': '240', 'warn_before_s': 45.0,
                'groups': ('Potok',), 'dry_run': True}
        self.assertEqual(phone.validate_phone_settings_changes(good),
                         {'enabled': 'нет', 'threshold_s': '240', 'warn_before_s': 45.0,
                          'groups': ('Potok',)})
        self.assertEqual(phone.validate_phone_settings_changes(None), {})
        self.assertEqual(phone.validate_phone_settings_changes({'enabled': None, 'groups': None}), {})
        self.assertEqual(phone.validate_phone_settings_changes({'groups': []}), {'groups': []})
        for value in (True, False, 'true', 'OFF', ' Да ', '0'):
            self.assertEqual(phone.validate_phone_settings_changes({'enabled': value}),
                             {'enabled': value})
        for bad in ({'enabled': 'выкл'}, {'enabled': ''}, {'enabled': 1}, {'enabled': []},
                    {'threshold_s': '5 мин'}, {'threshold_s': True}, {'threshold_s': [300]},
                    {'threshold_s': float('nan')}, {'threshold_s': float('inf')},
                    {'warn_before_s': 'минута'}, {'warn_before_s': False}, {'warn_before_s': {}},
                    {'groups': 5}, {'groups': 'yar'}, {'groups': ['yar', 7]},
                    {'groups': {'yar': True}}, {'groups': ['yarr']}, {'groups': ['osnova']},
                    ['enabled'], 'enabled'):
            with self.subTest(bad=bad):
                with self.assertRaises(phone.PhoneSettingsError) as caught:
                    phone.validate_phone_settings_changes(bad)
                self.assertRegex(str(caught.exception), '[А-Яа-я]', 'причина — по-русски')
        self.assertTrue(issubclass(phone.PhoneSettingsError, ValueError))


# ─────────────────────────────────────────────────────────────────────────────
# Запросы
# ─────────────────────────────────────────────────────────────────────────────

SETTINGS_COLUMNS = ['enabled', 'threshold_s', 'warn_before_s', 'groups',
                    'updated_by', 'updated_at', 'updated_by_name']


class PhoneSettingsQueryTest(unittest.TestCase):
    def test_reads_and_normalizes_the_row(self):
        moment = datetime(2026, 10, 5, 9, 30)
        cursor = _RecordingCursor([(SETTINGS_COLUMNS,
                                    [(False, 240, 45, ['potok'], 5, moment, 'Глава ОП')])])
        rule = queries.get_phone_settings(cursor, 'op')
        self.assertEqual(rule, {'enabled': False, 'threshold_s': 240, 'warn_before_s': 45,
                                'groups': ['potok'], 'updated_at': moment, 'updated_by': 5,
                                'updated_by_name': 'Глава ОП'})
        sql, params = cursor.executed[0]
        self.assertIn('FROM oktell_guard_phone_settings s', sql)
        self.assertEqual(params, {'department_code': 'op'})

    def test_missing_row_gives_defaults(self):
        rule = queries.get_phone_settings(_RecordingCursor([(SETTINGS_COLUMNS, [])]))
        self.assertEqual(rule['threshold_s'], 300)
        self.assertEqual(rule['groups'], ['yar', 'potok'])
        self.assertIs(rule['enabled'], True)
        self.assertIsNone(rule['updated_at'])

    def test_groups_as_json_text_and_empty_list(self):
        text = queries.get_phone_settings(_RecordingCursor([(SETTINGS_COLUMNS,
                                                             [(True, 300, 60, '["yar"]', None, None, None)])]))
        self.assertEqual(text['groups'], ['yar'])
        empty = queries.get_phone_settings(_RecordingCursor([(SETTINGS_COLUMNS,
                                                              [(True, 300, 60, [], None, None, None)])]))
        self.assertEqual(empty['groups'], [], 'пустой список — осознанное «ни одной группы»')

    def test_save_merges_changes_over_current(self):
        before = (True, 300, 60, ['yar', 'potok'], None, None, None)
        after = (True, 120, 60, ['potok'], 7, datetime(2026, 10, 5), 'Админ')
        cursor = _RecordingCursor([(SETTINGS_COLUMNS, [before]), ([], []), (SETTINGS_COLUMNS, [after])])
        saved = queries.save_phone_settings(
            cursor, 'op', {'threshold_s': 120, 'groups': ['potok'], 'enabled': None,
                           'dry_run': True, 'department_code': 'szov'},
            updated_by=7)
        self.assertEqual(saved['threshold_s'], 120)
        upsert_sql, params = cursor.executed[1]
        self.assertIn('INSERT INTO oktell_guard_phone_settings', upsert_sql)
        self.assertIn('ON CONFLICT (department_code) DO UPDATE SET', upsert_sql)
        self.assertEqual(params['department_code'], 'op', 'отдел — из аргумента, не из тела')
        self.assertEqual(params['threshold_s'], 120)
        # Порог уменьшили — предупреждение подрезано под него (120 − 30).
        self.assertEqual(params['warn_before_s'], 60)
        self.assertIs(params['enabled'], True, 'null = «не трогать»')
        self.assertEqual(json.loads(params['groups']), ['potok'])
        self.assertEqual(params['updated_by'], 7)
        self.assertNotIn('dry_run', params)

    def test_save_refuses_bad_values_before_touching_the_base(self):
        """Сценарий из ревью: правило выключено, порог 600, только ЯР. Кривая
        правка раньше молча сохраняла «включено / 300 / обе группы»."""
        for bad in ({'enabled': 'выкл'}, {'threshold_s': '10m'}, {'groups': 1},
                    {'threshold_s': 240, 'warn_before_s': True}):
            with self.subTest(bad=bad):
                cursor = _RecordingCursor([(SETTINGS_COLUMNS,
                                            [(False, 600, 60, ['yar'], None, None, None)])])
                with self.assertRaises(ValueError):
                    queries.save_phone_settings(cursor, 'op', bad, updated_by=7)
                self.assertEqual(cursor.executed, [], 'ни чтения, ни записи')

    def test_save_without_a_real_change_does_not_touch_the_row(self):
        """Форма шлёт поле и при уходе с него без изменений. «Кто и когда
        правил» при этом переписываться не должно — строку не трогаем вовсе."""
        moment = datetime(2026, 10, 1, 9, 0)
        before = (False, 240, 45, ['potok'], 5, moment, 'Глава ОП')
        for same in ({}, None, {'threshold_s': 240}, {'threshold_s': '240', 'enabled': 'нет'},
                     {'warn_before_s': 45, 'groups': ['POTOK']}, {'enabled': None},
                     {'dry_run': True}):
            with self.subTest(same=same):
                cursor = _RecordingCursor([(SETTINGS_COLUMNS, [before])])
                saved = queries.save_phone_settings(cursor, 'op', same, updated_by=7)
                self.assertEqual(len(cursor.executed), 1, 'только чтение текущего правила')
                self.assertNotIn('INSERT', cursor.executed[0][0])
                self.assertEqual(saved['updated_by'], 5)
                self.assertEqual(saved['updated_at'], moment)
                self.assertEqual(saved['updated_by_name'], 'Глава ОП')
                self.assertEqual(saved['threshold_s'], 240)
        # Значение за пределами подрезается до того же, что уже стоит, — тоже не правка.
        cursor = _RecordingCursor([(SETTINGS_COLUMNS, [(True, 3600, 60, ['yar'], 5, moment, 'Глава ОП')])])
        queries.save_phone_settings(cursor, 'op', {'threshold_s': 99999}, updated_by=7)
        self.assertEqual(len(cursor.executed), 1)
        # А настоящая правка одного поля пишет строку.
        cursor = _RecordingCursor([(SETTINGS_COLUMNS, [before]), ([], []), (SETTINGS_COLUMNS, [before])])
        queries.save_phone_settings(cursor, 'op', {'enabled': True}, updated_by=7)
        self.assertIn('INSERT INTO oktell_guard_phone_settings', cursor.executed[1][0])
        self.assertEqual(cursor.executed[1][1]['updated_by'], 7)

    def test_save_trims_warning_to_new_threshold(self):
        before = (True, 300, 120, ['yar'], None, None, None)
        cursor = _RecordingCursor([(SETTINGS_COLUMNS, [before]), ([], []), (SETTINGS_COLUMNS, [before])])
        queries.save_phone_settings(cursor, 'op', {'threshold_s': 90})
        params = cursor.executed[1][1]
        self.assertEqual(params['threshold_s'], 90)
        self.assertEqual(params['warn_before_s'], 60)


class PhoneKickQueryTest(unittest.TestCase):
    def test_new_kick_is_inserted(self):
        cursor = _RecordingCursor([(['id'], [(11,)])])
        moment = datetime(2026, 10, 5, 14, 3, 20)
        inserted = queries.record_phone_kick(
            cursor, user_id='42', happened_at=moment, client_key='phone|GUID-1',
            status_group='Potok', threshold_s=300)
        self.assertIs(inserted, True)
        sql, params = cursor.executed[0]
        self.assertIn('INSERT INTO oktell_guard_phone_kicks', sql)
        self.assertIn('ON CONFLICT (client_key) DO NOTHING', sql)
        self.assertNotIn('oktell_guard_violations', sql)
        self.assertEqual(params, {'user_id': 42, 'department_code': 'op', 'status_group': 'potok',
                                  'happened_at': moment, 'threshold_s': 300,
                                  'client_key': 'phone|GUID-1'})

    def test_repeat_delivery_is_not_a_second_kick(self):
        cursor = _RecordingCursor([(['id'], [])])
        self.assertIs(queries.record_phone_kick(
            cursor, user_id=42, happened_at=None, client_key='phone|GUID-1'), False)

    def test_no_key_means_no_write(self):
        cursor = _RecordingCursor()
        self.assertIs(queries.record_phone_kick(
            cursor, user_id=42, happened_at=None, client_key='  '), False)
        self.assertEqual(cursor.executed, [])

    def test_key_is_trimmed_to_the_column(self):
        cursor = _RecordingCursor([(['id'], [(1,)])])
        queries.record_phone_kick(cursor, user_id=1, happened_at=None, client_key='x' * 300,
                                  threshold_s='мусор')
        params = cursor.executed[0][1]
        self.assertEqual(len(params['client_key']), 128)
        self.assertEqual(params['threshold_s'], 0)


EMPLOYEE_COLUMNS = ['id', 'name', 'role', 'sip_number', 'department_name', 'group_name',
                    'group_model', 'direction_model', 'kicks_30d', 'last_kick_at']


class PhoneEmployeesQueryTest(unittest.TestCase):
    RULE = {'enabled': True, 'groups': ['yar', 'potok']}

    def run_list(self, rows, rule=None):
        cursor = _RecordingCursor([(EMPLOYEE_COLUMNS, rows)])
        result = queries.list_phone_employees(cursor, 'op', since=date(2026, 9, 5),
                                              as_of=date(2026, 10, 5), rule=rule or self.RULE)
        return result, cursor.executed[0]

    def test_groups_and_participation(self):
        last = datetime(2026, 10, 4, 11, 0)
        rows = [
            (1, 'Айгерим', 'operator', '6701', 'Отдел продаж', 'Поток 1', 'op_potok', 'op_osnova', 3, last),
            (2, 'Бекзат', 'trainee', '', 'Отдел продаж', '', '', 'op_yandex_reg', 0, None),
            (3, 'Вера', 'operator', '6703', 'Отдел продаж', 'Основа', 'op_osnova', '', 0, None),
            (4, 'Гульнар', 'operator', '6704', 'Отдел продаж', 'Верификатор', 'op_verificator', 'op_potok', 0, None),
        ]
        result, _ = self.run_list(rows)
        self.assertEqual(result[0], {
            'id': 1, 'name': 'Айгерим', 'role': 'operator', 'sip_number': '6701',
            'department_name': 'Отдел продаж', 'status_group': 'potok', 'group_label': 'Поток',
            'group_name': 'Поток 1', 'participates': True, 'kicks_30d': 3, 'last_kick_at': last,
        })
        self.assertEqual((result[1]['status_group'], result[1]['participates']), ('yar', True))
        # Основа видна, но правило её не касается.
        self.assertEqual((result[2]['status_group'], result[2]['group_label'], result[2]['participates']),
                         ('osnova', 'Основа', False))
        # Действующая группа не ОП — направление не спасает.
        self.assertEqual((result[3]['status_group'], result[3]['participates']), ('', False))

    def test_disabled_rule_means_nobody_participates(self):
        rows = [(1, 'А', 'operator', '1', 'ОП', '', 'op_potok', '', 0, None)]
        result, _ = self.run_list(rows, rule={'enabled': False, 'groups': ['potok']})
        self.assertFalse(result[0]['participates'])

    def test_sql_uses_the_group_ladder_and_python_date(self):
        _, (sql, params) = self.run_list([])
        self.assertIn('ORDER BY gom.start_date DESC, gom.id DESC', sql)
        self.assertIn('gom.start_date <= %(day)s::date', sql)
        self.assertIn('(gom.end_date IS NULL OR gom.end_date >= %(day)s::date)', sql)
        self.assertNotIn('CURRENT_DATE', sql)
        self.assertIn('FROM oktell_guard_phone_kicks', sql)
        self.assertNotIn('oktell_guard_violations', sql)
        self.assertEqual(params['day'], date(2026, 10, 5))
        self.assertEqual(params['since'], date(2026, 9, 5))
        self.assertEqual(params['department_code'], 'op')
        self.assertEqual(params['roles'], ['operator', 'trainee'])
        self.assertEqual(params['inactive'], ['fired', 'dismissal'])
        self.assertEqual(params['op_department_id'], 367)

    def test_rule_is_read_when_not_given(self):
        cursor = _RecordingCursor([(SETTINGS_COLUMNS, [(True, 300, 60, ['potok'], None, None, None)]),
                                   (EMPLOYEE_COLUMNS,
                                    [(1, 'А', 'operator', '1', 'ОП', '', 'op_yandex_reg', '', 0, None)])])
        result = queries.list_phone_employees(cursor, 'op', since=date(2026, 9, 5), as_of=date(2026, 10, 5))
        self.assertFalse(result[0]['participates'], 'ЯР выключили из правила')


REPORT_COLUMNS = ['day', 'user_id', 'name', 'sip_number', 'status_group', 'kicks', 'first_at', 'last_at']


class PhoneReportQueryTest(unittest.TestCase):
    def test_rows_and_filters(self):
        first, last = datetime(2026, 10, 5, 10, 0), datetime(2026, 10, 5, 16, 0)
        cursor = _RecordingCursor([(REPORT_COLUMNS,
                                    [(date(2026, 10, 5), 1, 'Айгерим', '6701', 'potok', 2, first, last),
                                     (date(2026, 10, 4), None, '(неизвестный)', '', '', 1, first, first)])])
        rows = queries.phone_report(cursor, date(2026, 9, 22), date(2026, 10, 5), 'op')
        self.assertEqual(rows[0], {'day': date(2026, 10, 5), 'user_id': 1, 'name': 'Айгерим',
                                   'sip_number': '6701', 'status_group': 'potok', 'group_label': 'Поток',
                                   'kicks': 2, 'first_at': first, 'last_at': last})
        self.assertEqual(rows[1]['group_label'], '')
        sql, params = cursor.executed[0]
        self.assertIn('FROM oktell_guard_phone_kicks k', sql)
        self.assertIn('k.department_code = %(department_code)s', sql)
        self.assertIn('ORDER BY day DESC, name', sql)
        self.assertNotIn('oktell_guard_violations', sql)
        self.assertEqual(params, {'date_from': date(2026, 9, 22), 'date_to': date(2026, 10, 5),
                                  'department_code': 'op'})


ACCESS_COLUMNS = ['id', 'name', 'role', 'department_code', 'is_department_head',
                  'headed_department_code', 'headed_department_codes']


class AccessContextTest(unittest.TestCase):
    def test_all_headed_departments_are_returned(self):
        cursor = _RecordingCursor([(ACCESS_COLUMNS, [(9, 'Глава', 'admin', 'tez', True, 'op', ['op', 'szov'])])])
        ctx = queries.access_context(cursor, 9)
        self.assertEqual(ctx['headed_department_codes'], ['op', 'szov'])
        self.assertEqual(ctx['department_code'], 'op')
        self.assertEqual(access.visible_department_codes(ctx), ['szov', 'op'])
        sql, params = cursor.executed[0]
        self.assertIn('ORDER BY 1, h.id', sql, 'отделы главы — в определённом порядке')
        self.assertEqual(params['op_department_id'], 367)

    def test_own_department_wins_among_headed(self):
        cursor = _RecordingCursor([(ACCESS_COLUMNS, [(9, 'Глава', 'admin', 'szov', True, 'op', ['op', 'szov'])])])
        self.assertEqual(queries.access_context(cursor, 9)['department_code'], 'szov')

    def test_old_row_shape_still_works(self):
        cursor = _RecordingCursor([(ACCESS_COLUMNS[:-1], [(9, 'Глава', 'admin', '', True, 'op')])])
        ctx = queries.access_context(cursor, 9)
        self.assertEqual(ctx['headed_department_codes'], ['op'])
        self.assertEqual(ctx['department_code'], 'op')

    def test_not_a_head(self):
        cursor = _RecordingCursor([(ACCESS_COLUMNS, [(5, 'СВ', 'sv', 'op', False, '', [])])])
        ctx = queries.access_context(cursor, 5)
        self.assertEqual(ctx['department_code'], 'op')
        self.assertEqual(access.visible_department_codes(ctx), ['op'])


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def context(role, *, department_code='op', is_department_head=False, headed=None):
    if headed is None:
        headed = [department_code] if is_department_head and department_code else []
    return {
        'id': 42,
        'name': 'Тест',
        'role': role,
        'department_code': department_code,
        'is_department_head': is_department_head,
        'headed_department_code': headed[0] if headed else '',
        'headed_department_codes': headed,
    }


OP_HEAD = context('admin', is_department_head=True)
OP_SV = context('sv')
SZOV_HEAD = context('admin', department_code='szov', is_department_head=True)
SZOV_SV = context('sv', department_code='szov')
ADMIN = context('admin', department_code='')

PHONE_RULE = {'enabled': True, 'threshold_s': 300, 'warn_before_s': 60, 'groups': ['yar', 'potok'],
              'updated_at': None, 'updated_by': None, 'updated_by_name': None}


class _Db:
    @contextmanager
    def _get_cursor(self):
        yield object()


@unittest.skipIf(Flask is None, 'flask не установлен')
class PhoneRoutesTest(unittest.TestCase):
    def client(self, requester):
        self.calls = []

        def record(name, result):
            def fn(_cursor, *args, **kwargs):
                self.calls.append((name, args, kwargs))
                return result(*args, **kwargs) if callable(result) else result
            return fn

        for name, replacement in (
            ('access_context', lambda _cursor, _uid: dict(requester)),
            ('get_settings', record('get_settings', {'enabled': True, 'threshold_s': 180})),
            ('current_release', record('current_release', None)),
            ('list_employees', record('list_employees', [{'id': 1, 'name': 'СЗоВ'}])),
            ('report', record('report', [])),
            ('rejected_count', record('rejected_count', 0)),
            ('pending_count', record('pending_count', 0)),
            ('save_settings', record('save_settings', {'threshold_s': 180})),
            ('bulk_set_rules', record('bulk_set_rules', 0)),
            ('get_phone_settings', record('get_phone_settings', dict(PHONE_RULE))),
            ('save_phone_settings', record('save_phone_settings',
                                           lambda department, changes, **kw: dict(PHONE_RULE, **changes))),
            ('list_phone_employees', record('list_phone_employees', [
                {'id': 7, 'name': 'Айгерим', 'status_group': 'potok', 'participates': True,
                 'kicks_30d': 2, 'last_kick_at': None}])),
            ('phone_report', record('phone_report', [
                {'day': date(2026, 10, 5), 'user_id': 7, 'name': 'Айгерим', 'kicks': 2},
                {'day': date(2026, 10, 4), 'user_id': 8, 'name': 'Бекзат', 'kicks': 3}])),
        ):
            patcher = patch.object(queries, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        app = Flask(__name__)
        app.register_blueprint(build_oktell_guard_blueprint(
            db=_Db(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (requester['id'], None, None),
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def called(self, name):
        return [call for call in self.calls if call[0] == name]

    # ── выбор части ─────────────────────────────────────────────────────────
    def test_op_head_gets_the_sales_part_by_default(self):
        body = self.client(OP_HEAD).get('/api/oktell_guard/settings').get_json()
        self.assertEqual(body['department'], 'op')
        self.assertEqual(body['departments'], [{'code': 'op', 'name': 'Отдел продаж'}])
        self.assertEqual(body['phone_settings']['threshold_s'], 300)
        self.assertIs(body['can_manage'], True)
        self.assertEqual(body['group_labels'], {'osnova': 'Основа', 'yar': 'ЯР', 'potok': 'Поток'})
        self.assertEqual(body['idle_groups'], ['yar', 'potok'])
        # Ничего из Oktell: ни настроек, ни версии агента.
        self.assertNotIn('settings', body)
        self.assertNotIn('release', body)
        self.assertEqual(self.called('get_settings'), [])

    def test_op_supervisor_reads_without_manage(self):
        body = self.client(OP_SV).get('/api/oktell_guard/settings?department=op').get_json()
        self.assertEqual(body['department'], 'op')
        self.assertIs(body['can_manage'], False)

    def test_szov_people_get_exactly_what_they_had(self):
        for requester in (SZOV_HEAD, SZOV_SV):
            body = self.client(requester).get('/api/oktell_guard/settings').get_json()
            self.assertEqual(body['department'], 'szov')
            self.assertEqual(body['departments'], [{'code': 'szov', 'name': 'СЗоВ'}])
            self.assertEqual(body['settings'], {'enabled': True, 'threshold_s': 180})
            self.assertIn('release', body)
            self.assertNotIn('phone_settings', body)

    def test_global_admin_sees_both_and_switches(self):
        client = self.client(ADMIN)
        default = client.get('/api/oktell_guard/settings').get_json()
        self.assertEqual(default['department'], 'szov')
        self.assertEqual([d['code'] for d in default['departments']], ['szov', 'op'])
        sales = client.get('/api/oktell_guard/settings?department=OP').get_json()
        self.assertEqual(sales['department'], 'op')
        self.assertIs(sales['can_manage'], True)

    def test_foreign_part_is_403_and_unknown_is_400(self):
        for requester, foreign in ((OP_HEAD, 'szov'), (OP_SV, 'szov'),
                                   (SZOV_HEAD, 'op'), (SZOV_SV, 'op')):
            client = self.client(requester)
            for url in ('/api/oktell_guard/settings', '/api/oktell_guard/employees',
                        '/api/oktell_guard/report'):
                response = client.get('%s?department=%s' % (url, foreign))
                self.assertEqual(response.status_code, 403, '%s %s' % (url, foreign))
                self.assertEqual(response.get_json()['error'], 'Раздел вам не открыт')
            self.assertEqual(self.called('list_employees'), [])
            self.assertEqual(self.called('report'), [])
            self.assertEqual(self.called('phone_report'), [])
            self.assertEqual(self.called('list_phone_employees'), [])
        response = self.client(ADMIN).get('/api/oktell_guard/employees?department=tez')
        self.assertEqual(response.status_code, 400)

    # ── сотрудники и отчёт ──────────────────────────────────────────────────
    def test_op_employees(self):
        client = self.client(OP_SV)
        body = client.get('/api/oktell_guard/employees').get_json()
        self.assertEqual(body['department'], 'op')
        self.assertEqual(body['employees'][0]['status_group'], 'potok')
        (_name, args, kwargs), = self.called('list_phone_employees')
        self.assertEqual(args, ('op',))
        self.assertEqual(kwargs['as_of'], date.today())
        self.assertEqual(kwargs['since'], date.today() - timedelta(days=30))
        self.assertEqual(self.called('list_employees'), [])

    def test_szov_employees_unchanged(self):
        body = self.client(SZOV_SV).get('/api/oktell_guard/employees').get_json()
        self.assertEqual(body['employees'], [{'id': 1, 'name': 'СЗоВ'}])
        self.assertEqual(self.called('list_employees')[0][2]['department_code'], 'szov')

    def test_op_report(self):
        client = self.client(OP_HEAD)
        body = client.get('/api/oktell_guard/report?from=2026-09-22&to=2026-10-05').get_json()
        self.assertEqual(body['department'], 'op')
        self.assertEqual(body['total'], 5)
        self.assertEqual(len(body['rows']), 2)
        (_name, args, kwargs), = self.called('phone_report')
        self.assertEqual(args, (date(2026, 9, 22), date(2026, 10, 5)))
        self.assertEqual(kwargs['department_code'], 'op')
        self.assertEqual(self.called('report'), [])

    def test_op_report_default_range_and_bad_dates(self):
        client = self.client(OP_HEAD)
        body = client.get('/api/oktell_guard/report').get_json()
        today = date.today()
        self.assertEqual((body['from'], body['to']), (str(today - timedelta(days=13)), str(today)))
        bad = client.get('/api/oktell_guard/report?from=вчера')
        self.assertEqual(bad.status_code, 400)

    # ── правка правила ОП ───────────────────────────────────────────────────
    def test_op_head_and_admin_save_the_rule(self):
        for requester in (OP_HEAD, ADMIN):
            client = self.client(requester)
            response = client.put('/api/oktell_guard/phone/settings',
                                  json={'threshold_s': 240, 'groups': ['potok']})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['phone_settings']['threshold_s'], 240)
            (_name, args, kwargs), = self.called('save_phone_settings')
            self.assertEqual(args, ('op', {'threshold_s': 240, 'groups': ['potok']}))
            self.assertEqual(kwargs['updated_by'], 42)
            self.assertEqual(client.post('/api/oktell_guard/phone/settings?department=op',
                                         json={'enabled': False}).status_code, 200)

    def test_op_supervisor_cannot_save(self):
        for role in ('sv', 'supervisor'):
            response = self.client(context(role)).put('/api/oktell_guard/phone/settings',
                                                      json={'enabled': False})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()['error'], 'Недостаточно прав')
            self.assertEqual(self.called('save_phone_settings'), [])

    def test_szov_people_do_not_reach_the_sales_rule(self):
        for requester in (SZOV_HEAD, SZOV_SV, context('operator')):
            response = self.client(requester).put('/api/oktell_guard/phone/settings',
                                                  json={'enabled': False})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()['error'], 'Раздел вам не открыт')
            self.assertEqual(self.called('save_phone_settings'), [])

    def test_bad_rule_value_is_400_with_the_reason(self):
        """G2: непригодное значение — 400 с причиной по-русски, а не 200 с
        правилом по умолчанию и не 500 из общего перехватчика."""
        client = self.client(OP_HEAD)

        def refuse(_cursor, _department, changes, **_kw):
            return phone.validate_phone_settings_changes(changes)

        with patch.object(queries, 'save_phone_settings', refuse):
            for bad in ({'enabled': 'выкл'}, {'threshold_s': '5 мин'},
                        {'warn_before_s': True}, {'groups': 1}, {'groups': ['yarr']}):
                with self.subTest(bad=bad):
                    response = client.put('/api/oktell_guard/phone/settings', json=bad)
                    self.assertEqual(response.status_code, 400)
                    self.assertRegex(response.get_json()['error'], '[А-Яа-я]')
            self.assertEqual(client.put('/api/oktell_guard/phone/settings',
                                        json={'threshold_s': 240}).status_code, 200)

    def test_other_errors_of_the_save_are_not_turned_into_400(self):
        """400 — только за присланное значение. Чужой ValueError из глубины
        (драйвер, разбор строки базы) остаётся ошибкой сервера: иначе сбой базы
        выглядел бы как «вы прислали не то»."""
        client = self.client(OP_HEAD)

        def broken(_cursor, _department, _changes, **_kw):
            raise ValueError('boom')

        with patch.object(queries, 'save_phone_settings', broken):
            response = client.put('/api/oktell_guard/phone/settings', json={'threshold_s': 240})
        self.assertEqual(response.status_code, 500)

    def test_phone_settings_refuse_other_departments(self):
        response = self.client(ADMIN).put('/api/oktell_guard/phone/settings?department=szov',
                                          json={'enabled': False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.called('save_phone_settings'), [])

    # ── Oktell остаётся за СЗоВ ─────────────────────────────────────────────
    def test_op_people_cannot_touch_oktell(self):
        for requester in (OP_HEAD, OP_SV):
            client = self.client(requester)
            for method, url in (('put', '/api/oktell_guard/settings'),
                                ('post', '/api/oktell_guard/settings'),
                                ('post', '/api/oktell_guard/employees/bulk'),
                                ('post', '/api/oktell_guard/release')):
                response = getattr(client, method)(url, json={'enabled': False, 'user_ids': [1]})
                self.assertEqual(response.status_code, 403, url)
                self.assertEqual(response.get_json()['error'], 'Недостаточно прав')
            download = client.get('/api/oktell_guard/download')
            self.assertEqual(download.status_code, 403)
            self.assertEqual(self.called('save_settings'), [])
            self.assertEqual(self.called('bulk_set_rules'), [])

    def test_bulk_refuses_the_sales_department(self):
        client = self.client(ADMIN)
        response = client.post('/api/oktell_guard/employees/bulk?department=op',
                               json={'user_ids': [1], 'enabled': False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'У отдела продаж персональных правил нет')
        self.assertEqual(self.called('bulk_set_rules'), [])

    def test_oktell_settings_save_refuses_the_sales_department(self):
        client = self.client(ADMIN)
        response = client.put('/api/oktell_guard/settings?department=op', json={'threshold_s': 300})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.called('save_settings'), [])
        self.assertEqual(client.put('/api/oktell_guard/settings', json={'threshold_s': 200}).status_code, 200)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
