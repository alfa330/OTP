# -*- coding: utf-8 -*-
"""«Расчет ресурсов · Основа ОП» (IC-1, #470): модель файла постановщика и её место в разделе.

Главная проверка — повтор файла «Erlang_C_.xlsx» (руководство ОП, 25.11.2026) на его же
средних по часам: 532 агенто-часа, 560 FTE-часов, 16,5 → 17 FTE, пик 9 человек, только
входящие — 9,8 → 10 FTE. Остальное сторожит то, что «Основа» живёт на разделе линии СЗоВ,
не меняя его: свой префикс ручек, свой прогон аукциона, свои шаблоны и настройки.
"""
import ast
import json
import math
import os
import re
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from resource_fte import op_osnova as m
from resource_fte.calculations import _period_totals

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'op_osnova_erlang_c_2611.json')


def _read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as handle:
        return handle.read()


def _excel():
    with open(FIXTURE, encoding='utf-8') as handle:
        return json.load(handle)


class ErlangCTests(unittest.TestCase):
    def test_service_level_matches_the_hand_formula(self):
        # 20,8 звонка в час × 187 с = 1,0804 Эрл: двое дают SL 65,7 %, трое — 91,1 %.
        self.assertAlmostEqual(m.erlang_c_service_level(20.8, 187, 2, 20), 0.6566, places=3)
        self.assertAlmostEqual(m.erlang_c_service_level(20.8, 187, 3, 20), 0.9106, places=3)

    def test_fewest_agents_reaching_the_target(self):
        self.assertEqual(m.erlang_c_agents(20.8, 187, 0.8, 20), 3)
        self.assertEqual(m.erlang_c_agents(3, 187, 0.8, 20), 1)
        self.assertEqual(m.erlang_c_agents(0, 187, 0.8, 20), 0)

    def test_not_enough_agents_answer_nobody_in_time(self):
        self.assertEqual(m.erlang_c_service_level(100, 187, 5, 20), 0.0)

    def test_large_traffic_stays_finite(self):
        agents = m.erlang_c_agents(2000, 187, 0.8, 20)
        self.assertGreater(agents, 2000 * 187 / 3600)
        self.assertLess(agents, 140)


class StaffHourTests(unittest.TestCase):
    def test_monday_15_00_of_the_file(self):
        # Файл: вход 26,5, соединено 75,2, недозвонов 121,2 → Erlang C 3, итого 8.
        staff = m.staff_hour(26.5, 75.2, 121.2, m.DEFAULT_SETTINGS)
        self.assertEqual(staff['agents_inbound'], 3)
        self.assertEqual(staff['agents'], 8)
        self.assertAlmostEqual(staff['load_out'], 4.9162, places=3)
        self.assertAlmostEqual(staff['fte'], 8 / 0.95, places=6)

    def test_night_hour_keeps_the_minimum(self):
        staff = m.staff_hour(0, 0, 0, m.DEFAULT_SETTINGS)
        self.assertEqual(staff['agents'], 1)
        staff = m.staff_hour(0, 0, 0, {**m.DEFAULT_SETTINGS, 'min_agents_per_hour': 0})
        self.assertEqual(staff['agents'], 0)

    def test_outbound_can_be_left_out(self):
        staff = m.staff_hour(26.5, 75.2, 121.2, {**m.DEFAULT_SETTINGS, 'include_outbound': False})
        self.assertEqual(staff['agents'], 3)
        self.assertEqual(staff['load_out'], 0.0)


class ReproducesTheOwnerFileTests(unittest.TestCase):
    """Модель на средних из самого файла даёт его итоги до единицы."""

    @classmethod
    def setUpClass(cls):
        data = _excel()
        cls.daily = []
        for weekday in range(7):
            agents = [m.staff_hour(data['inbound'][hour][weekday], data['out_connected'][hour][weekday],
                                   data['out_noanswer'][hour][weekday], m.DEFAULT_SETTINGS)
                      for hour in range(24)]
            cls.daily.append(agents)

    def test_agent_hours_per_day(self):
        # «Агенто-часов в день» листа «Расчет ресурсов», Пн..Вс.
        self.assertEqual([sum(h['agents'] for h in day) for day in self.daily], [78, 82, 79, 68, 74, 78, 73])

    def test_peak_per_day(self):
        self.assertEqual([max(h['agents'] for h in day) for day in self.daily], [8, 8, 9, 7, 7, 7, 6])

    def test_inbound_only_agent_hours(self):
        self.assertEqual([sum(h['agents_inbound'] for h in day) for day in self.daily], [47, 47, 48, 44, 46, 44, 42])

    def test_week_totals_through_the_line_summary(self):
        # Сводку периода считает общий построитель линии — тот же, что у «Основы» на экране.
        profiles = [{'daily_fte': sum(h['fte'] for h in day)} for day in self.daily]
        totals = _period_totals(profiles, {**m.DEFAULT_SETTINGS}, 0.0)
        self.assertAlmostEqual(totals['period_fte_hours'], 560.0, places=2)
        self.assertAlmostEqual(totals['base_operators'], 14.0, places=3)
        self.assertAlmostEqual(totals['operators_with_shrinkage'], 16.4706, places=3)
        self.assertEqual(totals['operators_rounded'], 17.0)


class _FakeData(m.OpData):
    """OpData без базы: дни и часы задаются тестом."""

    def __init__(self, today, inbound, outbound=None, complete=None):  # noqa: D401 — no DB
        self.today = today
        self.inbound = inbound
        self.outbound = outbound or {}
        days = set(inbound) | set(self.outbound)
        self.complete_days = sorted(complete if complete is not None else days)
        self.complete_set = set(self.complete_days)


def _flat_day(total_per_hour):
    return {hour: {'offered': float(total_per_hour), 'answered': float(total_per_hour), 'lost': 0.0,
                   'talk_seconds': 100.0 * total_per_hour} for hour in range(24)}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 27)  # пятница
        # Четыре прошлых понедельника: по 2 звонка в час, кроме 10:00 — 10 звонков.
        self.mondays = [date(2026, 11, 23) - timedelta(days=7 * k) for k in range(5)]
        inbound = {}
        for day in self.mondays:
            row = _flat_day(2)
            row[10] = {'offered': 10.0, 'answered': 10.0, 'lost': 0.0, 'talk_seconds': 1000.0}
            inbound[day] = row
        self.data = _FakeData(self.today, inbound)
        self.settings = {**m.DEFAULT_SETTINGS, 'inbound_source': 'history', 'history_weeks': 4}

    def test_window_is_same_weekday_before_the_day_and_today(self):
        window = self.data.window(date(2026, 11, 30), 4)
        self.assertEqual(window, self.mondays[:4])
        # Прошедший понедельник видит только то, что было до него.
        self.assertEqual(self.data.window(date(2026, 11, 16), 4), self.mondays[2:5])

    def test_incomplete_days_stay_out_of_the_window(self):
        data = _FakeData(self.today, self.data.inbound, complete=self.mondays[1:])
        self.assertNotIn(self.mondays[0], data.window(date(2026, 11, 30), 4))

    def test_history_profile_is_the_weekday_mean(self):
        profile = m.profile_for_date(self.data, date(2026, 11, 30), self.settings)
        self.assertAlmostEqual(profile['avg_daily_calls'], 23 * 2 + 10, places=6)
        self.assertAlmostEqual(profile['hourly_profile'][10]['avg_calls'], 10.0, places=6)
        self.assertEqual(profile['op_model']['inbound_source'], 'history')
        self.assertFalse(profile['insufficient_history'])
        self.assertAlmostEqual(profile['daily_fte'], sum(h['fte'] for h in profile['hourly_profile']), places=6)

    def test_engine_volume_keeps_the_history_shape(self):
        engine = {date(2026, 11, 30): {'calls': 112.0, 'made_on': '2026-11-26', 'method_label': 'TimesFM',
                                       'calls_low': 90.0, 'calls_high': 130.0}}
        profile = m.profile_for_date(self.data, date(2026, 11, 30), {**self.settings, 'inbound_source': 'timesfm'}, engine)
        self.assertAlmostEqual(profile['avg_daily_calls'], 112.0, places=6)
        # Доля 10:00 в истории — 10 из 56 звонков дня.
        self.assertAlmostEqual(profile['hourly_profile'][10]['avg_calls'], 112.0 * 10 / 56, places=6)
        self.assertEqual(profile['op_model']['inbound_source'], 'timesfm')

    def test_engine_is_ignored_when_the_setting_says_history(self):
        engine = {date(2026, 11, 30): {'calls': 112.0, 'made_on': '2026-11-26', 'method_label': 'TimesFM'}}
        profile = m.profile_for_date(self.data, date(2026, 11, 30), self.settings, engine)
        self.assertAlmostEqual(profile['avg_daily_calls'], 56.0, places=6)

    def test_day_without_history_is_marked_and_not_invented(self):
        engine = {date(2026, 12, 1): {'calls': 300.0, 'made_on': '2026-11-26', 'method_label': 'TimesFM'}}
        profile = m.profile_for_date(self.data, date(2026, 12, 1), {**self.settings, 'inbound_source': 'timesfm'}, engine)
        self.assertTrue(profile['insufficient_history'])
        self.assertEqual(profile['avg_daily_calls'], 0.0)
        self.assertEqual(profile['op_model']['inbound_source'], 'history')

    def test_profile_does_not_trigger_the_line_engine_paths(self):
        # По `engine` и `engine_agents` общий код линии включает Erlang A — у «Основы» их нет.
        profile = m.profile_for_date(self.data, date(2026, 11, 30), self.settings)
        self.assertNotIn('engine', profile)
        self.assertTrue(all('engine_agents' not in row for row in profile['hourly_profile']))

    def test_short_window_is_reported_as_not_enough_history(self):
        # Просили 4 недели, а закрытых понедельников до 16.11 только три — считаем по ним,
        # но день помечен: на экране полоска «истории не хватает».
        profile = m.profile_for_date(self.data, date(2026, 11, 16), self.settings)
        self.assertEqual(profile['history_count'], 3)
        self.assertEqual(profile['used_source_count'], 3)
        self.assertTrue(profile['insufficient_history'])
        self.assertGreater(profile['avg_daily_calls'], 0)

    def test_missing_direction_is_refused_not_counted_company_wide(self):
        # Общие функции линии читают пустой список направлений как «фильтра нет».
        with self.assertRaisesRegex(ValueError, 'Не найдено направление «Основа ОП»'):
            m.require_direction({'selected_direction_ids': []})
        self.assertEqual(m.require_direction({'selected_direction_ids': [73, 91]}), [73, 91])

    def test_actual_counts_only_closed_past_days(self):
        actual = m.actual_for_period(self.data, date(2026, 11, 23), date(2026, 11, 28), self.settings)
        self.assertEqual(list(actual), ['2026-11-23'])
        day = actual['2026-11-23']
        self.assertEqual(day['actual_received_calls'], 23 * 2 + 10)
        self.assertTrue(day['hourly'][10]['has_actual_report'])


class ClockAndSavepointTests(unittest.TestCase):
    def test_today_is_almaty_not_the_utc_session(self):
        # Сессии пула в UTC: до 05:00 по Алматы закрытые вчерашние сутки считались «сегодня».
        with mock.patch('cdr.queries.today_almaty', return_value=date(2026, 11, 21)):
            self.assertEqual(m._today_tx(object()), date(2026, 11, 21))

    def test_optional_part_rolls_back_to_its_savepoint(self):
        class FakeCursor:
            def __init__(self):
                self.sql = []

            def execute(self, sql, params=None):
                self.sql.append(sql)

        def broken():
            raise RuntimeError('SQL упал')

        cursor = FakeCursor()
        with self.assertLogs(level='ERROR'):
            self.assertEqual(m.optional_tx(cursor, broken, {}, 'проверка'), {})
        self.assertEqual(cursor.sql, ['SAVEPOINT op_optional_part', 'ROLLBACK TO SAVEPOINT op_optional_part'])
        cursor.sql.clear()
        self.assertEqual(m.optional_tx(cursor, lambda: 5, 0, 'проверка'), 5)
        self.assertEqual(cursor.sql, ['SAVEPOINT op_optional_part', 'RELEASE SAVEPOINT op_optional_part'])

    def test_engine_forecast_is_an_optional_part(self):
        source = _read('resource_fte/op_osnova.py')
        body = source[source.index('def _engine_forecasts_tx'):]
        body = body[:body.index('\n\n\n')]
        self.assertIn('return optional_tx(cursor, read, {},', body)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_the_owner_file(self):
        cfg = m.normalize_settings({})
        self.assertEqual((cfg['aht_in_seconds'], cfg['aht_out_seconds'], cfg['noanswer_seconds']), (187.0, 187.0, 30.0))
        self.assertEqual((cfg['sl_target'], cfg['sl_seconds'], cfg['occ'], cfg['ur']), (0.8, 20.0, 0.85, 0.95))
        self.assertEqual((cfg['weekly_hours_per_operator'], cfg['shrinkage_coeff']), (40.0, 0.85))

    def test_stored_garbage_falls_back_to_defaults(self):
        cfg = m.normalize_settings({'occ': 'abc', 'sl_target': 5, 'inbound_source': 'oracle', 'history_weeks': 40})
        self.assertEqual(cfg['occ'], 0.85)
        self.assertEqual(cfg['sl_target'], 0.8)
        self.assertEqual(cfg['inbound_source'], 'timesfm')
        self.assertEqual(cfg['history_weeks'], 4)

    def test_form_out_of_range_is_refused_with_a_reason(self):
        current = m.normalize_settings({})
        with self.assertRaisesRegex(ValueError, 'Цель SL'):
            m.validate_settings_payload({'sl_target': 1.5}, current)
        with self.assertRaisesRegex(ValueError, 'Источник прогноза'):
            m.validate_settings_payload({'inbound_source': 'x'}, current)
        merged = m.validate_settings_payload({'aht_in_seconds': '150,5', 'include_outbound': False}, current)
        self.assertEqual(merged['aht_in_seconds'], 150.5)
        self.assertFalse(merged['include_outbound'])

    def test_every_bounded_key_has_a_default(self):
        for key, low, high, integer, message in m._NUMERIC_BOUNDS:
            self.assertIn(key, m.DEFAULT_SETTINGS)
            self.assertTrue(low <= m.DEFAULT_SETTINGS[key] <= high, key)
            self.assertTrue(message)

    def test_owner_enters_templates_by_hand(self):
        payload = m.get_shift_templates()
        self.assertEqual(payload['templates'], [])
        self.assertEqual([item['rate'] for item in payload['rates']], [1.0, 0.75, 0.5])


class AuctionCatalogTests(unittest.TestCase):
    """Штат расчёта и участники аукциона — одни и те же люди «Основы ОП»."""

    def test_catalog_run_matches_the_module(self):
        from db import core
        mode = core.SHIFT_AUCTION_MODE_OP_OSNOVA
        self.assertEqual(mode, m.OP_AUCTION_MODE)
        self.assertIn(mode, core.SHIFT_AUCTION_MODES)
        self.assertEqual(core.SHIFT_AUCTION_MODE_DEPARTMENT_CODE[mode], m.OP_DEPARTMENT_CODE)
        self.assertEqual(core.SHIFT_AUCTION_MODE_DIRECTION_NAME[mode], m.OP_DIRECTION_NAME)
        self.assertNotIn(mode, core.SHIFT_AUCTION_UPLOAD_MODES)
        self.assertNotIn(mode, core.SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES)
        self.assertEqual(core.shift_auction_night_shifts(mode), (('20:00', '08:00'),))

    def test_settings_rows_stay_unique(self):
        from db import core
        rows = list(core.SHIFT_AUCTION_SETTINGS_ROW_ID.values())
        self.assertEqual(len(rows), len(set(rows)))
        self.assertEqual(core.SHIFT_AUCTION_SETTINGS_ROW_ID[core.SHIFT_AUCTION_MODE_LINE], 1)
        self.assertEqual(core.SHIFT_AUCTION_SETTINGS_ROW_ID[core.SHIFT_AUCTION_MODE_OP_OSNOVA], 5)

    def test_operator_of_osnova_lands_in_its_run_and_szov_keeps_its_own(self):
        from db import core
        self.assertEqual(core.shift_auction_mode_matches_direction('Основа ОП', 'op'), 'op_osnova')
        self.assertEqual(core.shift_auction_mode_matches_direction('Основа', 'szov'), 'line')
        self.assertIsNone(core.shift_auction_mode_matches_direction('Основа', 'op'))
        self.assertIsNone(core.shift_auction_mode_matches_direction('Поток', 'op'))
        self.assertEqual(core.shift_auction_modes_for_departments(['op']), ('op_osnova',))
        self.assertEqual(core.shift_auction_modes_for_departments(['szov']), ('line', 'chat'))

    def test_schema_seeds_the_settings_row(self):
        source = _read('db/schema.py')
        self.assertIn('SHIFT_AUCTION_SETTINGS_ROW_ID[SHIFT_AUCTION_MODE_OP_OSNOVA], SHIFT_AUCTION_MODE_OP_OSNOVA', source)


class OpBillingRowsTests(unittest.TestCase):
    """Сведение строк «Касаний» в строки Oktell — без базы."""

    def setUp(self):
        from resource_fte import op_billing
        self.b = op_billing
        day = date(2026, 11, 24)
        # (день, час, очередь, номер, поступило, обслужено, в SL, сброс до очереди, разговор,
        #  ожидание принятых, ожидание потерянных, общее время)
        self.raw = [
            (day, 10, '3034', '7470939675', 10, 8, 7, 0, 800, 80, 30, 910),
            (day, 10, '', '7470939675', 0, 0, 0, 3, 0, 0, 0, 0),     # до очереди не дошли: парк по номеру
            (day, 11, '3010', '7475777778', 5, 5, 5, 0, 500, 10, 0, 510),
            (day, 11, '', '7000000000', 0, 0, 0, 2, 0, 0, 0, 0),     # незнакомый номер без очереди
        ]

    def test_outgoing_counts_only_connected_calls_like_the_line(self):
        # Oktell берёт в «Звонков» исхода только соединённые (call_result 5); у «Основы» недозвоны
        # шли в счёт и топили ATT исхода (стенд 27.11: 45 с). dial_seconds «Касаний» — звонок
        # целиком: AHT — он сам, а не разговор плюс он (разговор считался дважды). «Дозвон» —
        # статус «Соединение», как State 2 у линии; гудки «Касаний» легли бы в OCC второй раз.
        day = date(2026, 11, 24)
        talk_kind, dial_kind = self.b.activity_mod.KIND_TALK, self.b.activity_mod.KIND_DIAL

        class FakeRoster:
            def __init__(self, *_args):
                self.operator_ids, self.exts, self.names = [7], {7: '201'}, {7: 'Основа · Оператор'}

            def in_group(self, *_args):
                return True

            def operator_on_ext(self, ext, _day):
                return 7 if ext == '201' else None

            def pieces(self, _operator_id):
                return [(dial_kind, datetime(2026, 11, 24, 9, 59, 30), datetime(2026, 11, 24, 10, 0)),
                        (talk_kind, datetime(2026, 11, 24, 10, 0), datetime(2026, 11, 24, 10, 10))]

        class FakeCursor:
            def execute(self, sql, params=None):
                self.sql, self.params = sql, params

            def fetchall(self):
                # день, номер, принято, разговор вх., ожидание вх., соединено исх., разговор исх.,
                # соединённые звонки целиком (гудки и разговор)
                return [(day, '201', 2, 200, 20, 3, 300, 330)]

        cursor = FakeCursor()
        with mock.patch.object(self.b, 'Roster', FakeRoster), mock.patch.object(self.b, '_not_test', return_value=''):
            calls, states = self.b.operator_rows(cursor, day, day, 0, 1439, datetime(2026, 11, 25))
        self.assertEqual(cursor.params['connected'], 'Разговор')
        self.assertEqual(cursor.sql.count('t.call_type = %(out)s AND t.result = %(connected)s'), 3)
        self.assertIn('SUM(GREATEST(COALESCE(t.dial_seconds, 0), COALESCE(t.talk_seconds, 0)))', cursor.sql)
        self.assertEqual((calls[0]['served_out'], calls[0]['call_out_seconds'], calls[0]['handle_out_seconds']),
                         (3, 300, 330))
        self.assertEqual((states[0]['dial_wait_out_seconds'], states[0]['dial_out_seconds']), (30, 0))
        # Разговор статусов (10 мин) делится по разговорам: 200 вх. к 300 исх.
        self.assertEqual((states[0]['talk_in_seconds'], states[0]['talk_out_seconds']), (240, 360))

    def test_tech_break_and_training_are_pause_like_the_line(self):
        # У линии «Пауза» — State 9 со всеми подпричинами; у «Основы» тренинг и тех. перерыв
        # выпадали из времени в системе, и UTZ доходил до 100 % (разбор 27.11: 11.11, тренинг 3 ч).
        day = date(2026, 11, 24)
        kinds = self.b.activity_mod
        pieces = [(kinds.KIND_BREAK, datetime(2026, 11, 24, 9, 0), datetime(2026, 11, 24, 9, 10)),
                  (kinds.KIND_TECH, datetime(2026, 11, 24, 9, 10), datetime(2026, 11, 24, 9, 15)),
                  (kinds.KIND_TRAINING, datetime(2026, 11, 24, 9, 15), datetime(2026, 11, 24, 10, 15)),
                  (kinds.KIND_WAIT, datetime(2026, 11, 24, 10, 15), datetime(2026, 11, 24, 10, 45))]

        class FakeRoster:
            def __init__(self, *_args):
                self.operator_ids, self.exts, self.names = [7], {7: '201'}, {7: 'Основа · Оператор'}

            def in_group(self, *_args):
                return True

            def operator_on_ext(self, _ext, _day):
                return 7

            def pieces(self, _operator_id):
                return pieces

        class FakeCursor:
            def execute(self, sql, params=None):
                pass

            def fetchall(self):
                return []

        with mock.patch.object(self.b, 'Roster', FakeRoster), mock.patch.object(self.b, '_not_test', return_value=''):
            _calls, states = self.b.operator_rows(FakeCursor(), day, day, 0, 1439, datetime(2026, 11, 25))
        self.assertEqual(states[0]['pause_seconds'], (10 + 5 + 60) * 60)
        self.assertEqual(states[0]['wait_seconds'], 30 * 60)

    def test_park_map_does_not_depend_on_the_report_window(self):
        # Сброс на приветствии идёт в парк по карте «Касаний», а не по строкам выбранного
        # периода и окна: 02.11 «Утро» отдавало iTaxi 0 сбросов из 6 (разбор 27.11).
        raw = [(date(2026, 11, 2), 7, '', '7470939675', 0, 0, 0, 3, 0, 0, 0, 0)]
        self.assertEqual(self.b.aggregate_rows(raw, 'park'), [])
        rows = self.b.aggregate_rows(raw, 'park', {'7470939675': '3034'})
        self.assertEqual([(row['taxi_park'], row['greet_drop']) for row in rows], [('Jana такси', 3)])

    def test_park_map_window_is_the_touches_rule(self):
        seen = []

        def line_rows(_cursor, since):
            seen.append(since)
            return [('7470939675', '3034', 5)]

        with mock.patch('cdr.queries.today_almaty', return_value=date(2026, 11, 27)), \
                mock.patch('cdr.queries.line_queue_rows', side_effect=line_rows):
            known = self.b.known_lines(object(), date(2026, 11, 17))
            self.b.known_lines(object(), date(2026, 8, 1))
        self.assertEqual(seen, [date(2026, 9, 28), date(2026, 8, 1)])
        self.assertEqual(known, {'7470939675': '3034'})
        # То же окно, что у раздела «Касания».
        self.assertIn(f'LINE_PARKS_WINDOW_DAYS = {self.b.KNOWN_LINES_WINDOW_DAYS}', _read('cdr/routes.py'))

    def test_report_rows_read_the_shared_park_map_but_not_for_hours(self):
        class FakeCursor:
            def execute(self, sql, params=None):
                pass

            def fetchall(self):
                return [(date(2026, 11, 2), 7, '', '7470939675', 0, 0, 0, 3, 0, 0, 0, 0)]

        day = date(2026, 11, 2)
        with mock.patch.object(self.b, 'known_lines', return_value={'7470939675': '3034'}) as known, \
                mock.patch.object(self.b, '_not_test', return_value=''):
            park = self.b.report_rows(FakeCursor(), day, day, 360, 719, 20, 'park')
            hours = self.b.report_rows(FakeCursor(), day, day, 360, 719, 20, 'hour')
        self.assertEqual(known.call_count, 1)
        self.assertEqual(park[0]['greet_drop'], 3)
        self.assertEqual(hours[0]['report_hour'], 7)

    def test_detail_lists_only_calls_the_cuts_count(self):
        # «Не приняли» без очереди в «Потеряно» не идут — и строкой «Детализации» тоже.
        with mock.patch.object(self.b, '_not_test', return_value=''):
            sql = self.b._detail_sql(object(), False)
        self.assertIn("AND (t.call_type = %(before)s OR t.queue <> '')", sql)

    def test_park_rows_follow_the_queue_and_the_line_number(self):
        rows = {row['taxi_park']: row for row in self.b.aggregate_rows(self.raw, 'park')}
        self.assertEqual(set(rows), {'Jana такси', 'Центр регистрации'})
        self.assertEqual(rows['Jana такси']['greet_drop'], 3)
        self.assertEqual(rows['Jana такси']['arrived'], 10)
        self.assertEqual(rows['Центр регистрации']['served_sl'], 5)
        self.assertEqual(rows['Jana такси']['rating_count'], 0)

    def test_hour_rows_keep_calls_without_a_park(self):
        rows = {row['report_hour']: row for row in self.b.aggregate_rows(self.raw, 'hour')}
        self.assertEqual(rows[11]['greet_drop'], 2)
        self.assertEqual(rows[10]['talk_seconds'], 800)

    def test_line_rows_carry_the_number(self):
        rows = self.b.aggregate_rows(self.raw, 'line')
        self.assertTrue(all(row['line_number'] for row in rows))

    def test_detail_row_marks_drops_and_talk_only_for_answered(self):
        when = datetime(2026, 11, 24, 23, 59, 48)
        lost = self.b._detail_row((when, '3041', '7470939685', '77001112233', self.b.IN_LOST, 0), 1, {})
        self.assertEqual((lost['queue_drop'], lost['ivr_drop'], lost['talk_seconds']), (1, 0, 0))
        before = self.b._detail_row((when, '', '7470939675', '77001112233', self.b.IN_BEFORE_QUEUE, 0), 2,
                                    {'7470939675': '3034'})
        self.assertEqual((before['ivr_drop'], before['park']), (1, 'Jana такси'))
        answered = self.b._detail_row((when, '3010', '7475777778', '77001112233', self.b.IN_ANSWERED, 161), 3, {})
        self.assertEqual(answered['talk_seconds'], 161)

    def test_detail_snapshot_round_trips(self):
        moment = datetime(2026, 11, 24, 23, 59, 48)
        snapshot = int(moment.strftime('%Y%m%d%H%M%S'))
        self.assertEqual(self.b._snapshot_moment(snapshot), moment)
        self.assertIsNone(self.b._snapshot_moment(None))


class BackendRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _read('bot_schedule2.py')
        cls.tree = ast.parse(cls.source)

    def _route_functions(self, prefix):
        found = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and decorator.args
                        and isinstance(decorator.args[0], ast.Constant)
                        and str(decorator.args[0].value).startswith(prefix)):
                    found[str(decorator.args[0].value)] = node
        return found

    def test_every_op_route_is_declared(self):
        routes = self._route_functions('/api/resource_fte/op/')
        for path in ('overview', 'day/<string:report_date>', 'settings', 'recalculate', 'shift_templates',
                     'schedule_preview', 'saved_schedule', 'operator_availability', 'rate_overrides',
                     'billing', 'billing_operators', 'billing_details', 'billing_grouping',
                     'billing_grouping_comment', 'billing_export'):
            self.assertIn(f'/api/resource_fte/op/{path}', routes)

    def test_op_routes_use_the_op_guard(self):
        routes = self._route_functions('/api/resource_fte/op/')
        for path, node in routes.items():
            if path.endswith('/rate_overrides'):
                continue  # общая ручка линии и чата: ставка — человека, а не направления
            body = ast.get_source_segment(self.source, node)
            self.assertIn('_resource_fte_op_route_guard()', body, path)
            self.assertNotIn('_resource_fte_route_guard()', body.replace('_resource_fte_op_route_guard()', ''), path)

    def test_saved_schedule_is_scoped_to_the_osnova_run(self):
        node = self._route_functions('/api/resource_fte/op/')['/api/resource_fte/op/saved_schedule']
        body = ast.get_source_segment(self.source, node)
        self.assertEqual(body.count('direction_mode=OP_AUCTION_MODE'), 2)

    def test_guard_admits_only_super_admin_and_sales_management(self):
        # Общий админ другого отдела не ведёт аукцион «Основы» — и расчёт ему не открыт,
        # иначе планировщик показал бы ему аукцион его отдела вместо «Основы» (разбор 27.11).
        node = self._route_functions('/api/resource_fte/op/')['/api/resource_fte/op/overview']
        guard = self.source[self.source.index('def _resource_fte_op_route_guard'):]
        guard = guard[:guard.index('def _resource_fte_op_value_error')]
        self.assertNotIn("role == 'admin' and not", guard)
        self.assertIn('if OP_DEPARTMENT_CODE in codes:', guard)
        self.assertIn('_resource_fte_op_route_guard()', ast.get_source_segment(self.source, node))

    def test_billing_does_not_need_oktell(self):
        # У «Основы» своя АТС: проверка готовности Oktell отказала бы её биллингу.
        parse = self.source[self.source.index('def _op_billing_parse_request_args'):]
        parse = parse[:parse.index('def _op_billing_now')]
        self.assertNotIn('_oktell_api_ready', parse)
        export = self._route_functions('/api/resource_fte/op/')['/api/resource_fte/op/billing_export']
        self.assertIn('_OP_BILLING_EXPORT_NOTE', ast.get_source_segment(self.source, export))

    def test_grouping_shifts_are_optional_under_a_savepoint(self):
        body = self.source[self.source.index('def _op_billing_grouping_report'):]
        body = body[:body.index('\n\n\n')]
        self.assertIn('op_osnova.optional_tx(', body)
        self.assertNotIn('except Exception', body)

    def test_export_route_passes_the_osnova_labels(self):
        export = self._route_functions('/api/resource_fte/op/')['/api/resource_fte/op/billing_export']
        body = ast.get_source_segment(self.source, export)
        for name in ('_OP_BILLING_EXPORT_TITLE', '_OP_BILLING_EXPORT_SOURCE', '_OP_BILLING_GROUPING_HEADERS',
                     '_OP_BILLING_GROUPING_EMPTY', '_OP_BILLING_EXPORT_DROP'):
            self.assertIn(name, body)
        self.assertIn('generated_at=_op_billing_now()', body)

    def test_session_carries_the_direction_model_for_sales(self):
        # По модели направления фронт открывает аукцион только операторам «Основы ОП»;
        # спрашивалась она лишь у СЗоВ, и у людей ОП поле приходило пустым (стенд 27.11).
        self.assertIn("str(department_code or '').strip().lower() in ('szov', 'op'):", self.source)

    def test_line_routes_are_untouched(self):
        routes = self._route_functions('/api/resource_fte/')
        for path in ('/api/resource_fte/overview', '/api/resource_fte/schedule_preview',
                     '/api/resource_fte/saved_schedule', '/api/resource_fte/settings'):
            body = ast.get_source_segment(self.source, routes[path])
            self.assertNotIn('op_osnova', body, path)


class BillingExportLabelsTests(unittest.TestCase):
    """Файлы «Основы» строят те же функции, что у линии: заголовок, источник и подписи колонок —
    свои («Касания», люди в часе), а файл линии остаётся прежним (стенд 27.11: «Биллинг Oktell —
    Таксопарки» и «Источник: Oktell» в выгрузке «Основы»)."""

    @classmethod
    def setUpClass(cls):
        from tests.test_oktell_billing_report import _extract_namespace
        cls.ns = _extract_namespace()
        cls.op = {}
        for node in ast.parse(_read('bot_schedule2.py')).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], 'id', '').startswith('_OP_BILLING_')):
                cls.op[node.targets[0].id] = ast.literal_eval(node.value)
        cls.params = {'start_day': date(2026, 11, 17), 'end_day': date(2026, 11, 23),
                      'minute_from': 0, 'minute_to': 1439}
        cls.report = cls.ns['_oktell_billing_build_report']([
            {'report_date': '2026-11-17', 'taxi_park': 'iTaxi', 'arrived': 10, 'served': 9, 'served_sl': 8,
             'greet_drop': 0, 'talk_seconds': 900, 'wait_ok_seconds': 30, 'wait_lost_seconds': 5,
             'total_seconds': 940},
        ])

    @staticmethod
    def _cells(output):
        from openpyxl import load_workbook
        workbook = load_workbook(output)
        return [str(cell.value) for sheet in workbook.worksheets for row in sheet.iter_rows()
                for cell in row if cell.value is not None], workbook

    def test_line_workbook_keeps_its_title_and_source(self):
        _cells, workbook = self._cells(self.ns['_oktell_billing_export_workbook']('park', self.params, self.report, 20))
        sheet = workbook['Итого за период']
        self.assertEqual(sheet['A1'].value, 'Биллинг Oktell — Таксопарки')
        self.assertTrue(sheet['A4'].value.startswith('Источник: Oktell (входящие звонки), сформировано '))

    def test_osnova_workbook_names_its_own_source(self):
        output = self.ns['_oktell_billing_export_workbook'](
            'park', self.params, self.report, 20, note=self.op['_OP_BILLING_EXPORT_NOTE'].format(sl=20),
            title=self.op['_OP_BILLING_EXPORT_TITLE'], source=self.op['_OP_BILLING_EXPORT_SOURCE'],
            generated_at=datetime(2026, 11, 27, 14, 22))
        cells, workbook = self._cells(output)
        sheet = workbook['Итого за период']
        self.assertEqual(sheet['A1'].value, 'Биллинг «Основы ОП» — Таксопарки')
        self.assertEqual(sheet['A4'].value,
                         'Источник: «Касания» отдела продаж (входящие звонки), сформировано 27.11.2026 14:22')
        self.assertFalse([cell for cell in cells if 'oktell' in cell.lower()])

    @staticmethod
    def _table(sheet, first_header):
        row = next(index for index in range(1, sheet.max_row + 1) if sheet.cell(row=index, column=1).value == first_header)
        return row, [cell.value for cell in sheet[row] if cell.value is not None]

    def test_line_workbook_keeps_its_columns(self):
        _cells, workbook = self._cells(self.ns['_oktell_billing_export_workbook']('park', self.params, self.report, 20))
        _row, headers = self._table(workbook['Итого за период'], 'Таксопарк')
        self.assertEqual(headers[-1], 'Ср. оценка')

    def test_osnova_number_workbook_drops_name_and_rating(self):
        drops = self.op['_OP_BILLING_EXPORT_DROP']
        report = self.ns['_oktell_billing_build_report']([
            {'report_date': '2026-11-17', 'taxi_park': 'iTaxi', 'line_number': '7470943333', 'arrived': 10,
             'served': 9, 'served_sl': 8, 'greet_drop': 2, 'talk_seconds': 900, 'wait_ok_seconds': 30,
             'wait_lost_seconds': 5, 'total_seconds': 940},
        ], include_line=True)
        output = self.ns['_oktell_billing_export_workbook']('line', self.params, report, 20, drop_columns=drops['line'])
        _cells, workbook = self._cells(output)
        sheet = workbook['Итого за период']
        row, headers = self._table(sheet, 'Номер')
        self.assertEqual(headers[:3], ['Номер', 'Таксопарк', 'Поступило'])
        self.assertNotIn('Название', headers)
        self.assertNotIn('Ср. оценка', headers)
        values = [cell.value for cell in sheet[row + 1]]
        self.assertEqual(values[headers.index('Сброс на приветствии')], 2)
        talk = headers.index('Ср. разговор') + 1
        self.assertEqual(sheet.cell(row=row + 1, column=talk).number_format, '[h]:mm:ss')
        days = workbook['По дням']
        self.assertEqual([cell.value for cell in days[1]][:4], ['Дата', 'Номер', 'Таксопарк', 'Поступило'])
        self.assertEqual(days.cell(row=2, column=talk + 1).number_format, '[h]:mm:ss')

    def test_osnova_operator_workbook_drops_postprocessing_and_hold(self):
        item = {'operator': 'Основа · Оператор', 'served': 3, 'talk_seconds': 300, 'handle_seconds': 330,
                'talk_state_seconds': 360, 'postproc_seconds': 0, 'hold_seconds': 0, 'wait_seconds': 600,
                'pause_seconds': 60, 'active_seconds': 400, 'dial_wait_seconds': 0}
        report = {'operators': [item], 'totals': dict(item), 'days': []}
        output = self.ns['_oktell_billing_export_workbook'](
            'operator', self.params, report, 20, direction='incoming',
            drop_columns=self.op['_OP_BILLING_EXPORT_DROP']['operator'])
        _cells, workbook = self._cells(output)
        sheet = workbook['Итого за период']
        row, headers = self._table(sheet, 'Оператор')
        self.assertNotIn('Постобработка', headers)
        self.assertNotIn('Удержание', headers)
        values = [cell.value for cell in sheet[row + 1]]
        self.assertEqual(values[headers.index('Ожидание')], timedelta(seconds=600))
        self.assertEqual(values[headers.index('Пауза')], timedelta(seconds=60))

    def test_grouping_headers_say_people_for_the_osnova_and_shifts_for_the_line(self):
        report = {'days': [{'date': '2026-11-17', 'hours': []}]}
        _cells, line = self._cells(self.ns['_oktell_billing_grouping_workbook'](report))
        self.assertEqual([c.value for c in line.active[1]][7:10], ['Прогноз смен', 'Запланировано смен', 'Факт смен'])
        _cells, osnova = self._cells(self.ns['_oktell_billing_grouping_workbook'](
            report, headers=self.op['_OP_BILLING_GROUPING_HEADERS'], empty_text=self.op['_OP_BILLING_GROUPING_EMPTY']))
        self.assertEqual([c.value for c in osnova.active[1]][7:10], ['Прогноз людей', 'По графику', 'Факт на линии'])
        cells, _empty = self._cells(self.ns['_oktell_billing_grouping_workbook'](
            {'days': []}, headers=self.op['_OP_BILLING_GROUPING_HEADERS'], empty_text=self.op['_OP_BILLING_GROUPING_EMPTY']))
        self.assertEqual(cells, [self.op['_OP_BILLING_GROUPING_EMPTY']])


class FrontendWiringTests(unittest.TestCase):
    def test_view_config_reuses_the_line(self):
        source = _read('src/components/resources/ResourceFteView.jsx')
        # Участок линии (LINE_DIRECTION → DIRECTION_CONFIG) прежний: «Основа» дописывается ниже.
        line_region = source[source.index('const LINE_DIRECTION = {'):source.index('const DIRECTION_CONFIG = {')]
        self.assertNotIn('OP_DIRECTION', line_region)
        self.assertIn('DIRECTION_CONFIG.op = OP_DIRECTION;', source)
        block = source[source.index('const OP_DIRECTION = {'):source.index('// Вводные «Основы ОП»')]
        self.assertIn('...LINE_DIRECTION', block)
        self.assertIn("apiPrefix: '/api/resource_fte/op'", block)
        for flag in ('hasUpload', 'hasOktellSync', 'hasDirectionPicker', 'hasHistoryPairs', 'hasWeekPicker',
                     'hasBillingExportTypes'):
            self.assertRegex(block, rf'{flag}: false')
        for flag in ('hasBilling', 'hasLosses', 'hasBillingTalkTime'):
            self.assertRegex(block, rf'{flag}: true')
        # «Звонки» и «Биллинг» — по «Касаниям», под своими ручками (владелец 27.11: «да сделай»).
        tabs = re.search(r"const OP_VIEW_TAB_KEYS = \[([^\]]+)\]", source).group(1)
        self.assertIn("'losses'", tabs)
        self.assertIn('BILLING_VIEW_KEY', tabs)
        self.assertIn("label: 'Биллинг'", source[source.index('const VIEW_TABS_OP'):source.index('const OP_DIRECTION')])
        billing = source[source.index('const OP_BILLING = {'):source.index('const OP_DIRECTION = {')]
        self.assertIn("endpoint: 'billing'", billing)
        self.assertNotIn('Oktell', billing)

    def test_wrapper_passes_the_op_direction_and_auction_link(self):
        source = _read('src/components/resources/ResourceOpFteView.jsx')
        self.assertIn('direction="op"', source)
        self.assertIn('onOpenShiftAuction={onOpenShiftAuction}', source)

    def test_planner_keeps_line_keys_and_gives_op_its_own(self):
        source = _read('src/components/resources/ResourceSchedulePlanner.jsx')
        self.assertIn("if (prefix.includes('/resource_fte/op')) return 'op';", source)
        self.assertIn("`${TEMPLATE_STORAGE_KEY}_${plannerDirectionFor(apiPrefix)}`", source)
        self.assertIn("plannerDirectionFor(apiPrefix) === 'line'\n    ? TEMPLATE_STORAGE_KEY", source)
        self.assertIn("if (direction === 'op') return 'op_osnova';", source)
        # Пустые умолчания «Основы» не должны запускать загрузку по кругу.
        self.assertIn('if (emptyDefaultTemplatesRef.current === apiPrefix) return;', source)

    def test_menu_item_exists_in_both_role_branches(self):
        source = _read('src/App.jsx')
        self.assertEqual(source.count("handleSidebarViewNavigation(e, 'resource_fte_op'"), 2)
        self.assertEqual(source.count('<ResourceOpFteView'), 2)
        self.assertIn("(requestedViewFromUrl !== 'resource_fte_op' || canAccessOpResourceFteSection)", source)

    def test_auction_handoff_keeps_the_osnova_run(self):
        source = _read('src/App.jsx')
        self.assertIn("['chat', 'line', 'op_osnova'].includes(directionRaw)", source)

    def test_menu_predicate_matches_the_auction_staff(self):
        app = _read('src/App.jsx')
        block = app[app.index('const canAccessOpResourceFteForUser'):]
        block = block[:block.index('};') + 2]
        self.assertNotIn("role === 'admin' && !headedCodes.length", block)
        self.assertIn("return ownCode === OP_RESOURCE_DEPARTMENT_CODE || headedCodes.includes(OP_RESOURCE_DEPARTMENT_CODE);", block)

    def test_planner_ignores_lots_of_another_run(self):
        planner = _read('src/components/resources/ResourceSchedulePlanner.jsx')
        self.assertIn("String(snapshot.direction_mode) === auctionDirectionFor(apiPrefix);", planner)
        self.assertIn('const lots = sameDirection && Array.isArray(snapshot.lots) ? snapshot.lots : [];', planner)

    def test_day_cards_use_the_osnova_history_flag(self):
        view = _read('src/components/resources/ResourceFteView.jsx')
        self.assertEqual(view.count('cfg.hasHistoryPairs || isOp'), 2)

    def test_auction_generation_button_leads_to_the_osnova_planner(self):
        # Глава отдела продаж — админ: «Генерация графиков» без прогона открыла бы ему «Линию» СЗоВ.
        app = _read('src/App.jsx')
        self.assertIn("navigateToView(direction === 'op_osnova' ? 'resource_fte_op' : 'resource_fte');", app)
        view = _read('src/components/resources/ShiftAuctionView.jsx')
        self.assertEqual(view.count('onClick={() => onOpenResourceGeneration(direction)}'), 2)
        self.assertNotIn('onClick={onOpenResourceGeneration}', view)

    def test_line_view_keeps_its_week_picker_and_history_pairs(self):
        source = _read('src/components/resources/ResourceFteView.jsx')
        line_region = source[source.index('const LINE_DIRECTION = {'):source.index('const DIRECTION_CONFIG = {')]
        self.assertIn('hasWeekPicker: true', line_region)
        self.assertIn('hasHistoryPairs: true', line_region)
        # Прироста у «Основы» нет — его блок и подписи скрыты только для неё.
        self.assertIn("{activeDashboardView === 'overview' && !isOp && (", source)

    def test_line_billing_tables_keep_their_captions(self):
        source = _read('src/components/resources/ResourceFteView.jsx')
        line = source[source.index('const BILLING_LINE_CAPTIONS = {'):source.index('const BillingTable = (')]
        self.assertIn("talk: 'Принятые звонки целиком, как в Oktell: приветствие, очередь и разговор'", line)
        self.assertIn("captions = BILLING_LINE_CAPTIONS, showRating = true", source)
        self.assertIn('const OpBillingTable = (props) => <BillingTable {...props} captions={OP_BILLING_CAPTIONS} showRating={false} />;', source)
        self.assertIn('cfg.hasBillingTalkTime ? (isOp ? OpBillingTable : BillingTable) : ChatBillingTable', source)

    def test_billing_totals_are_not_read_before_their_declaration(self):
        # «Операторы → Исход» у «Основы» ронял экран: подсказка «Дозвон» читала billingTotals
        # строкой выше его объявления (TDZ). У линии гудков в отчёте нет, у «Касаний» — есть.
        source = _read('src/components/resources/ResourceFteView.jsx')
        code = '\n'.join(line.split('//')[0] for line in source.splitlines())
        declaration = code.index('const billingTotals = ')
        self.assertNotIn('billingTotals', code[:declaration])
        self.assertIn('formatDurationHms(billingReport?.totals?.dial_wait_seconds)', code)

    def test_osnova_captions_do_not_borrow_the_line_wording(self):
        # Отчётов «Основа» не загружает, Oktell у неё нет: подписи свои, у линии — прежние.
        source = _read('src/components/resources/ResourceFteView.jsx')
        self.assertIn("hint={isOp ? OP_BILLING_CAPTIONS.tileTalk : 'Как в Oktell: с приветствием и очередью'}", source)
        self.assertIn("hint={isOp ? OP_PERIOD_CARD_TEXT.forecastHint : 'Сумма прогнозных FTE по загруженным дням'}", source)
        self.assertIn("hint={isOp ? OP_PERIOD_CARD_TEXT.actualHint : 'Из разговорной нагрузки отчетов, без смен'}", source)
        self.assertEqual(source.count("isOp ? OP_PERIOD_CARD_TEXT.daysLabel : 'Дни с отчетами'"), 2)
        # Компьютер и телефон: на телефоне подпись внутри шаблонной строки (`${…}`).
        self.assertEqual(source.count("{isOp ? OP_BILLING_CAPTIONS.groupingDelta : 'факт смен минус прогноз'}"), 2)
        self.assertEqual(source.count("${isOp ? OP_BILLING_CAPTIONS.groupingDelta : 'факт смен минус прогноз'}"), 1)
        line_groups = source[source.index('const DISPLAY_GROUPS = ['):]
        self.assertIn("['metricHistoryWarnings', 'Дни с отчетами']", line_groups[:line_groups.index('];')])

    def test_osnova_operator_table_hides_postprocessing(self):
        source = _read('src/components/resources/ResourceFteView.jsx')
        wrapper = source[source.index('const OpBillingOperatorTable'):source.index('const OP_BILLING = {')]
        self.assertIn('showPostproc={false}', wrapper)
        self.assertIn('(isOp ? OpBillingOperatorTable : BillingOperatorTable)', source)
        # У линии по умолчанию всё прежнее.
        self.assertIn('showPostproc = true, ahtTitle = BILLING_OPERATOR_AHT_TITLE, '
                      'dialWaitTitle = BILLING_OPERATOR_DIAL_WAIT_TITLE,', source)
        self.assertIn("const BILLING_OPERATOR_AHT_TITLE = 'Average Handling Time — звонок целиком (с IVR и очередью) "
                      "плюс удержание и постобработка';", source)

    def test_osnova_texts_on_empty_states_and_hints(self):
        source = _read('src/components/resources/ResourceFteView.jsx')
        self.assertEqual(source.count('isOp ? OP_EMPTY_TEXT.'), 4)
        op_direction = source[source.index('const OP_DIRECTION = {'):]
        self.assertIn("historyPickerHint: 'точка = есть звонки'", op_direction[:op_direction.index('};')])
        self.assertIn('? OP_BILLING_CAPTIONS.operatorHint', source)
        self.assertIn('isOp ? (billingIsOutgoing ? OP_BILLING_CAPTIONS.ahtOutgoing : OP_BILLING_CAPTIONS.ahtIncoming)',
                      source)

    def test_number_without_a_name_is_not_repeated(self):
        source = _read('src/components/resources/ResourceFteView.jsx')
        self.assertIn('{billingLineLabel(item.line) && billingLineDisplayNumber(item.line) '
                      '? `${billingLineDisplayNumber(item.line)} · ${billingParkLabel(item.park)}` '
                      ': billingParkLabel(item.park)}', source)

    def test_auction_screen_knows_the_run(self):
        view = _read('src/components/resources/ShiftAuctionView.jsx')
        self.assertIn("const AUCTION_DIRECTION_OP_OSNOVA = 'op_osnova';", view)
        participants = _read('src/components/resources/shiftAuctionParticipants.js')
        self.assertIn("[SHIFT_AUCTION_MODE_OP_OSNOVA]: 'основа оп'", participants)
        memory = _read('src/components/resources/shiftAuctionDirectionMemory.js')
        self.assertIn("{ code: 'op', label: 'Отдел продаж', directions: [SHIFT_AUCTION_MODE_OP_OSNOVA] }", memory)


if __name__ == '__main__':
    unittest.main()
