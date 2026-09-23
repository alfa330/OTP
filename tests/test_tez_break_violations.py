"""Перерывы вне графика у Тез КЦ и отбивка о них в Telegram (возврат задачи #292).

Правило сверки общее с СЗоВ (_szov_break_classify) — здесь стережём то, что у Тез своё:
источник факта (события телефона iCORE Phone вместо истории Oktell), одно направление на
оба табло отдела и отбивка, которая молчит, когда нарушений нет.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном namespace: так
проверяется настоящая логика. Импортировать модуль нельзя — на старте он поднимает пул к
боевой БД (тот же приём в test_szov_break_violations.py).
"""
import ast
import asyncio
import functools
import html
import logging
import os
import re
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
DB_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
VIEW = (ROOT / "src" / "components" / "monitoring" / "TezWallboardView.jsx").read_text(encoding="utf-8-sig")
SZOV_VIEW = (ROOT / "src" / "components" / "monitoring" / "SzovWallboardView.jsx").read_text(encoding="utf-8-sig")

NAMES = {
    '_env_int',
    'SZOV_BREAK_MIN_MINUTES', 'SZOV_BREAK_TOLERANCE_MINUTES', 'SZOV_BREAK_MERGE_GAP_MINUTES',
    'SZOV_BREAK_SCAN_LOOKBACK_HOURS', 'SZOV_BREAK_REPORT_MAX_AGE_HOURS',
    'SZOV_BREAK_KIND_OFF_SCHEDULE', 'SZOV_BREAK_KIND_NOT_PLANNED', 'SZOV_BREAK_KIND_NO_SHIFT',
    'SZOV_BREAK_DIRECTION_LINE', 'SZOV_BREAK_DIRECTION_TEZ',
    '_szov_break_merge_episodes', '_szov_break_planned_for_day', '_szov_break_on_shift',
    '_szov_break_classify', '_szov_break_violation_detail',
    '_TEZ_WALLBOARD_MODEL_BY_DIRECTION', 'TEZ_BREAK_NOTE_LIMIT',
    '_tez_break_status_keys', '_tez_break_violations_scan', '_tez_break_violation_lines',
    # Отбивка табло Тез КЦ (23.09.2026): период, итоги часа, отклонения, подпись, картинки.
    'TEZ_AR_TARGET_PERCENT', 'TEZ_AR_BAD_PERCENT', 'TEZ_WALLBOARD_ABANDON_FROM_SECONDS',
    'TEZ_BROADCAST_HOUR_MIN_CALLS', 'TEZ_BROADCAST_TIMEZONE', 'TEZ_BROADCAST_JOURNAL_WAIT_SECONDS',
    'TELEGRAM_MAX_CAPTION_CHARS', 'SZOV_BROADCAST_MODE_ALWAYS', 'SZOV_BROADCAST_MODE_DEVIATIONS',
    '_szov_wallboard_int', '_szov_plural', '_szov_format_age_ru', '_szov_broadcast_stale_note',
    '_op_broadcast_percent', '_op_broadcast_duration',
    '_tez_broadcast_count', '_tez_broadcast_pair', '_tez_broadcast_ar_colors',
    '_tez_broadcast_period', '_tez_broadcast_window_totals', '_tez_broadcast_tp_line',
    '_tez_broadcast_assemble', '_tez_broadcast_deviations', '_tez_broadcast_caption',
    '_TEZ_BROADCAST_TABLE_ROWS', '_tez_broadcast_text', '_tez_broadcast_tp_key_tiles',
    '_tez_render_tp_day_png', '_tez_render_tp_hour_png', '_tez_render_op_png',
    '_tez_broadcast_journal', '_tez_broadcast_mark_reported', '_szov_broadcast_run_job',
    # Личная отбивка админам: адресаты и право на строку «лично мне».
    'SZOV_BROADCAST_DIRECTION_TEZ', 'SZOV_BROADCAST_DIRECTION_LINE',
    '_tez_broadcast_personal_recipients',
    '_szov_broadcast_personal_owner', '_szov_broadcast_personal_state',
    '_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role',
    '_is_admin_role',
}

PARSE = '%Y-%m-%d %H:%M:%S'
NOW = datetime(2026, 9, 22, 18, 0, 0)


def _dt(value):
    return datetime.strptime(value, PARSE)


class _FakeDb:
    """База ровно в том объёме, в каком её трогает разбор перерывов Тез."""

    def __init__(self, episodes=None, planned=None, shifts=None):
        self.episodes = episodes or []
        self.planned = planned or {}
        self.shifts = shifts or {}
        self.saved = []
        self.episode_calls = []

    def _status_profile_for_calculation_model(self, model_code):
        # Настоящий профиль моделей Тез: словарь кабинета плюс словарь телефона.
        return {'break': {'break in work', 'перерыв', 'авто'}}

    def get_phone_break_episodes(self, operator_ids, window_from, window_to, status_keys=None):
        self.episode_calls.append({
            'operator_ids': set(operator_ids),
            'window_from': window_from,
            'window_to': window_to,
            'status_keys': set(status_keys or ()),
        })
        return list(self.episodes)

    def get_planned_breaks_for_days(self, operator_ids, days):
        return self.planned, self.shifts

    def save_szov_break_violations(self, rows):
        self.saved.extend(rows)
        return len(rows)


def _namespace(db=None, people=None, extra=None):
    tree = source_cache.parse(SOURCE)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            body.append(node)
        elif isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & NAMES:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    roster = people if people is not None else {
        'tp': [{'id': 1, 'name': 'Тлеу Аскар'}],
        'op': [{'id': 2, 'name': 'Ким Дана'}],
    }
    ns = {
        'os': os, 're': re, 'logging': logging, 'html': html, 'time': time,
        'datetime': datetime, 'timedelta': timedelta, 'ZoneInfo': ZoneInfo,
        'asyncio': asyncio, 'functools': functools, 'executor_pool': None,
        '_env_int': lambda name, default, minimum=None, maximum=None: default,
        'db': db if db is not None else _FakeDb(),
        # Состав отдела берётся у табло — здесь подменяем, чтобы не ходить в базу.
        '_tez_wallboard_people': lambda *args, **kwargs: roster,
    }
    ns.update(extra or {})
    exec(compile(module, "<tez-breaks>", "exec"), ns)
    missing = sorted(name for name in NAMES if name not in ns)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return ns


class TezBreakScanTests(unittest.TestCase):
    """Разбор перерывов отдела: что берём за факт и что из этого нарушение."""

    def test_status_keys_come_from_the_calculation_model(self):
        """Перерыв в журнале нарушений и перерыв в учёте часов — одно и то же множество.

        Разъедься они, и человек попадал бы в нарушители за минуты, которых в его же
        часах нет."""
        db = _FakeDb()
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_status_keys'](), {'break in work', 'перерыв', 'авто'})
        self.assertEqual(ns['_TEZ_WALLBOARD_MODEL_BY_DIRECTION']['tp'], 'tez_line')

    def test_break_matching_the_schedule_is_not_a_violation(self):
        """Вышел тогда, когда перерыв стоит в графике, — молчим."""
        db = _FakeDb(
            episodes=[{'operator_id': 1, 'started_at': _dt('2026-09-22 14:00:00'),
                       'ended_at': _dt('2026-09-22 14:15:00')}],
            planned={(1, '2026-09-22'): [(14 * 60, 14 * 60 + 15)]},
            shifts={(1, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 0)
        self.assertEqual(db.saved, [])

    def test_break_off_the_schedule_is_written_with_the_tez_direction(self):
        """Сдвинулся дальше допуска — нарушение, и лежит оно в своём направлении."""
        db = _FakeDb(
            episodes=[{'operator_id': 1, 'started_at': _dt('2026-09-22 16:00:00'),
                       'ended_at': _dt('2026-09-22 16:20:00')}],
            planned={(1, '2026-09-22'): [(14 * 60, 14 * 60 + 15)]},
            shifts={(1, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 1)
        row = db.saved[0]
        self.assertEqual(row['direction'], ns['SZOV_BREAK_DIRECTION_TEZ'])
        self.assertEqual(row['direction'], 'tez')
        self.assertEqual(row['kind'], ns['SZOV_BREAK_KIND_OFF_SCHEDULE'])
        self.assertEqual(row['operator_name'], 'Тлеу Аскар')
        self.assertEqual(row['deviation_minutes'], 120)

    def test_both_directions_of_the_department_are_scanned(self):
        """ТП и ОП сверяются вместе: перерыв стоит в графике у человека, а не у линии."""
        db = _FakeDb(
            episodes=[{'operator_id': 2, 'started_at': _dt('2026-09-22 13:00:00'),
                       'ended_at': _dt('2026-09-22 13:30:00')}],
            shifts={(2, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 1)
        self.assertEqual(db.episode_calls[0]['operator_ids'], {1, 2})
        self.assertEqual(db.saved[0]['operator_name'], 'Ким Дана')
        self.assertEqual(db.saved[0]['kind'], ns['SZOV_BREAK_KIND_NOT_PLANNED'])

    def test_short_flicker_is_not_a_break(self):
        """Статус мигнул на минуту — это не перерыв, а мерцание: порог общий с СЗоВ."""
        db = _FakeDb(
            episodes=[{'operator_id': 1, 'started_at': _dt('2026-09-22 16:00:00'),
                       'ended_at': _dt('2026-09-22 16:01:00')}],
            shifts={(1, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 0)

    def test_window_is_the_shared_lookback(self):
        """Окно разбора и перекрытие — те же, что у СЗоВ: правило одно на компанию."""
        db = _FakeDb()
        ns = _namespace(db=db)
        ns['_tez_break_violations_scan'](now=NOW)
        call = db.episode_calls[0]
        self.assertEqual(call['window_from'],
                         NOW - timedelta(hours=ns['SZOV_BREAK_SCAN_LOOKBACK_HOURS']))
        # Верхняя граница чуть впереди «сейчас»: иначе только что начавшийся перерыв не
        # попал бы в окно вовсе.
        self.assertGreater(call['window_to'], NOW)
        self.assertEqual(call['status_keys'], {'break in work', 'перерыв', 'авто'})

    def test_empty_roster_does_not_touch_the_database(self):
        """Состав пуст — сверять не с чем; молча писать нули в журнал нельзя."""
        db = _FakeDb()
        ns = _namespace(db=db, people={'tp': [], 'op': []})
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 0)
        self.assertEqual(db.episode_calls, [])


class TezBreakViolationLinesTests(unittest.TestCase):
    """Строки о перерывах в подписи отбивки: молчание без нарушений и человеческие формулировки."""

    def setUp(self):
        self.ns = _namespace()

    def _violation(self, name, started, kind='not_planned', planned_start=None):
        return {'operator_name': name, 'started_at': started, 'violation_date': started[:10],
                'kind': kind, 'planned_start_minutes': planned_start}

    def test_no_violations_means_no_lines(self):
        self.assertEqual(self.ns['_tez_break_violation_lines']([]), [])
        self.assertEqual(self.ns['_tez_break_violation_lines'](None), [])

    def test_line_names_the_person_the_time_and_the_reason(self):
        text = '\n'.join(self.ns['_tez_break_violation_lines']([
            self._violation('Тлеу Аскар', '2026-09-22 16:00:00'),
        ]))
        self.assertIn('Перерывы не по графику', text)
        self.assertIn('Тлеу Аскар', text)
        self.assertIn('22.09', text)
        self.assertIn('16:00', text)
        self.assertIn('перерывов в графике на этот день нет', text)

    def test_wording_is_the_same_as_in_the_szov_broadcast(self):
        """Одно нарушение обязано читаться одинаково, в какой бы отдел про него ни написали."""
        row = self._violation('Тлеу Аскар', '2026-09-22 16:00:00',
                              kind='off_schedule', planned_start=840)
        text = '\n'.join(self.ns['_tez_break_violation_lines']([row]))
        self.assertIn(self.ns['_szov_break_violation_detail'](row), text)

    def test_long_list_is_cut_with_a_counter(self):
        """Поимённо — до предела, остальные счётчиком: сообщение читают с телефона."""
        limit = self.ns['TEZ_BREAK_NOTE_LIMIT']
        rows = [self._violation(f'Оператор {i}', '2026-09-22 16:00:00') for i in range(limit + 3)]
        text = '\n'.join(self.ns['_tez_break_violation_lines'](rows))
        self.assertEqual(text.count('•'), limit)
        self.assertIn('и ещё 3', text)

    def test_names_are_escaped_for_html(self):
        """Подпись уходит с parse_mode HTML: имя с «<» иначе уронило бы отправку."""
        text = '\n'.join(self.ns['_tez_break_violation_lines']([
            self._violation('Тлеу <Аскар>', '2026-09-22 16:00:00'),
        ]))
        self.assertIn('&lt;Аскар&gt;', text)
        self.assertNotIn('<Аскар>', text)


class TezBreakEpisodeQueryTests(unittest.TestCase):
    """Запрос за эпизодами: что именно считается фактом перерыва."""

    def setUp(self):
        self.body = DB_SOURCE[DB_SOURCE.index('def get_phone_break_episodes('):]
        self.body = self.body[:self.body.index('\n    def ', 10)]

    def test_only_live_phone_events_are_taken(self):
        """Ночная выгрузка кабинета размечает ПРОШЕДШИЕ сутки: её «break in work»
        приехал бы эпизодом в полсмены и сделал бы нарушением каждый обед."""
        self.assertIn('e.client_event_id IS NOT NULL', self.body)
        self.assertIn("e.event_kind = 'status'", self.body)

    def test_end_of_the_episode_is_the_next_event_of_the_same_person(self):
        """Фильтр по статусу — СНАРУЖИ подзапроса: изнутри «следующим» оказался бы
        следующий перерыв, а не возврат на линию."""
        self.assertIn('LEAD(e.event_at) OVER (PARTITION BY e.operator_id', self.body)
        subquery = self.body[self.body.index('FROM ('):self.body.index(') x')]
        self.assertNotIn('status_key) = ANY', subquery)
        self.assertIn('WHERE regexp_replace(lower(btrim(x.status_key))', self.body)


class TezBreakWiringTests(unittest.TestCase):
    """Проводка: расписание, ручка журнала, получатели и кнопки на экране."""

    def test_scan_runs_every_hour_around_the_clock(self):
        """Смена отдела начинается затемно, а окно захода — три часа: разбор идёт своей джобой
        каждый час, независимо от отбивки."""
        self.assertIn('tez_break_scan_job,', SOURCE)
        self.assertIn("id='tez_break_scan'", SOURCE)
        self.assertIn("CronTrigger(hour='*', minute=TEZ_BREAK_SCAN_MINUTE,", SOURCE)

    def test_broadcast_is_hourly_around_the_clock(self):
        """Решение владельца 23.09.2026: каждый час круглые сутки, как у СЗоВ и ОП. Отдельной
        отбивки только о перерывах больше нет — они едут в подписи отбивки табло."""
        self.assertIn("TEZ_BROADCAST_SEND_TIMES = (os.getenv('TEZ_BROADCAST_SEND_TIMES') "
                      "or _SZOV_BROADCAST_HOURLY).strip()", SOURCE)
        self.assertIn('for _hour, _minute in _tez_broadcast_send_times():', SOURCE)
        self.assertIn('tez_broadcast_job,', SOURCE)
        self.assertIn("id=f'tez_wallboard_broadcast_{_hour:02d}{_minute:02d}'", SOURCE)
        for gone in ('tez_break_broadcast_job', '_tez_break_broadcast_send',
                     'TEZ_BREAK_BROADCAST_SEND_TIMES'):
            self.assertNotIn(gone, SOURCE)

    def test_journal_fills_even_without_recipients(self):
        """Журнал в iCore обязан наполняться и тогда, когда отбивку никто не получает."""
        scan = SOURCE[SOURCE.index('async def tez_break_scan_job():'):]
        scan = scan[:scan.index('\ndef ', 10)]
        self.assertIn('_tez_break_violations_scan', scan)
        self.assertNotIn('get_szov_broadcast_chats', scan)
        job = SOURCE[SOURCE.index('async def tez_broadcast_job():'):]
        job = job[:job.index('\n@app.route')]
        # Отбивка разбор НЕ повторяет, а прочитанными нарушения помечает только после доставки.
        self.assertNotIn('_tez_break_violations_scan', job)
        self.assertIn('on_delivered=_tez_broadcast_mark_reported', job)
        self.assertIn('personal=_tez_broadcast_personal_recipients', job)
        self.assertIn('deviations=_tez_broadcast_deviations', job)

    def test_journal_endpoint_is_registered_and_guarded(self):
        self.assertIn(
            "@app.route('/api/tez_wallboard/break_violations', methods=['GET', 'OPTIONS'])",
            SOURCE)
        handler = SOURCE[SOURCE.index('def api_tez_wallboard_break_violations():'):]
        handler = handler[:handler.index('\ndef ', 10)]
        # Права те же, что у самого табло: журнал смотрят те же СВ и руководитель отдела.
        self.assertIn('requester_id, err = _tez_wallboard_guard()', handler)
        self.assertIn("if request.method == 'OPTIONS':", handler)
        self.assertIn('direction=SZOV_BREAK_DIRECTION_TEZ', handler)

    def test_direction_is_allowed_everywhere_it_is_checked(self):
        """Список направлений лежит в четырёх местах, и разъехаться им нельзя."""
        self.assertIn("SZOV_BROADCAST_DIRECTION_TEZ = 'tez'", SOURCE)
        self.assertIn('SZOV_BROADCAST_DIRECTION_OP, SZOV_BROADCAST_DIRECTION_TEZ)', SOURCE)
        self.assertIn("SZOV_BROADCAST_DIRECTIONS = ('osnova', 'chat', 'op', 'tez')", DB_SOURCE)
        self.assertIn("CHECK (direction IN ('osnova', 'chat', 'op', 'tez'))", DB_SOURCE)
        self.assertIn("CHECK (direction IN ('line', 'chat', 'tez'))", DB_SOURCE)

    def test_broadcast_settings_belong_to_the_tez_department_head(self):
        guard = SOURCE[SOURCE.index('def _szov_broadcast_guard():'):]
        guard = guard[:guard.index('\ndef ', 10)]
        self.assertIn('elif direction == SZOV_BROADCAST_DIRECTION_TEZ:', guard)
        self.assertIn("globals().get('_tez_wallboard_department_id')", guard)
        self.assertIn('const canManageTezBroadcastForUser',
                      (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig'))

    def test_test_send_and_preview_use_the_wallboard_broadcast(self):
        """Кнопка проверки шлёт настоящую отбивку табло и нарушения прочитанными не помечает."""
        self.assertIn('SZOV_BROADCAST_DIRECTION_TEZ: _tez_broadcast_send,', SOURCE)
        send = SOURCE[SOURCE.index('async def _tez_broadcast_send('):]
        send = send[:send.index('\n_TEZ_BROADCAST_PREVIEW_IMAGES')]
        self.assertNotIn('mark_szov_break_violations_reported', send)
        preview = SOURCE[SOURCE.index('def api_szov_wallboard_broadcast_preview():'):]
        preview = preview[:preview.index('\ndef ', 10)]
        self.assertIn('return _tez_broadcast_preview()', preview)

    def test_screen_has_both_buttons_and_shows_the_modes(self):
        """С отбивкой табло режимы получателя обрели смысл: «только при отклонениях» пишет, лишь
        когда прошедший час ТП вне нормы."""
        self.assertIn('<BreakViolationsControls', VIEW)
        self.assertIn('<BroadcastControls', VIEW)
        self.assertNotIn('withModes={false}', VIEW)
        self.assertNotIn('Отбивка перерывов', VIEW)
        self.assertIn('canManageBroadcast ?', VIEW)
        # Журнал открыт всем, кто видит табло, — он стоит вне проверки прав на отбивку.
        self.assertLess(VIEW.index('<BreakViolationsControls'), VIEW.index('canManageBroadcast ?'))


# --- Отбивка табло: правила ---------------------------------------------------------------------

ZONE = ZoneInfo('Asia/Almaty')


def _ts(value):
    return int(datetime.strptime(value, PARSE).replace(tzinfo=ZONE).timestamp())


LINE = '77003000770'


def _call(started, disposition='ANSWER', stage='901', line=LINE, call_type=0):
    return {'call_type': call_type, 'line_number': line, 'disposition': disposition,
            'waitsec': 12, 'internal_number': stage, 'start_time': _ts(started)}


def _snapshot(day='2026-09-23', **extra):
    snapshot = {
        'day': day,
        'binotel_now': '%s 10:00:07' % day,
        'stale': False,
        'age_seconds': 4,
        'diagnostics': {'tp_line_number': LINE},
        'tp': {'now': {'queue': 1, 'operators_online': 4, 'operators_free': 2,
                       'operators_talking': 2, 'operators_on_break': 1},
               'today': {'arrived': 99, 'served': 99, 'lost': 0, 'ar_ratio': 0.0, 'sl_ratio': 0.9,
                         'avg_wait_seconds': 18, 'avg_talk_seconds': 129,
                         'outgoing_success': 10, 'outgoing_total': 46}},
        'op': {'now': {'operators_online': 5, 'operators_free': 3, 'operators_talking': 2,
                       'operators_on_break': 0},
               'today': {'outgoing_success': 65, 'outgoing_total': 152,
                         'outgoing_success_ratio': 0.4276, 'avg_talk_seconds': 95}},
    }
    snapshot.update(extra)
    return snapshot


class _AssembleDb:
    def __init__(self, violations=()):
        self.violations = list(violations)

    def get_unreported_szov_break_violations(self, max_age_hours, direction=None):
        assert direction == 'tez', direction
        return list(self.violations)


def _assemble(snapshot, calls, now=datetime(2026, 9, 23, 10, 0, 5), violations=(), **kwargs):
    ns = _namespace(db=_AssembleDb(violations))
    return ns, ns['_tez_broadcast_assemble'](snapshot, calls, kwargs.get('journal_error'), now,
                                             kwargs.get('snapshot_error'))


class TezBroadcastPeriodTests(unittest.TestCase):
    """Какой час судит отбивка и чем она считает его приём и потери."""

    def test_last_full_hour_is_judged(self):
        ns = _namespace()
        for now, expected in ((datetime(2026, 9, 23, 10, 0, 5), (9, 10)),
                              (datetime(2026, 9, 23, 10, 37), (9, 10)),
                              (datetime(2026, 9, 23, 0, 0, 3), (23, 0))):
            start, end = ns['_tez_broadcast_period'](now)
            self.assertEqual((start.hour, end.hour), expected, now)

    def test_hour_totals_use_the_wallboard_rule_on_the_hour_window(self):
        """Одно определение «потеряно» у плитки и у строки в Telegram: приветствие в AR не идёт,
        чужая линия и исходящие — тоже, звонки соседних часов — мимо окна."""
        calls = [
            _call('2026-09-23 09:05:00'),
            _call('2026-09-23 09:10:00', 'NOANSWER', 'Очередь'),
            _call('2026-09-23 09:20:00', 'NOANSWER', 'Приветствие в рабочее время New'),
            _call('2026-09-23 09:30:00', line='77000000000'),
            _call('2026-09-23 09:40:00', call_type=1),
            _call('2026-09-23 08:59:59', 'NOANSWER', 'Очередь'),
            _call('2026-09-23 10:00:00', 'NOANSWER', 'Очередь'),
        ]
        ns = _namespace()
        totals = ns['_tez_broadcast_window_totals'](calls, LINE, datetime(2026, 9, 23, 9),
                                                    datetime(2026, 9, 23, 10))
        self.assertEqual((totals['arrived'], totals['served'], totals['lost']), (2, 1, 1))
        self.assertEqual(totals['ar_ratio'], 0.5)

    def test_day_tiles_take_intake_from_the_fresh_journal(self):
        """Ночью табло никто не смотрит и журнал в снимке старый: итог дня пересчитан по журналу
        отбивки, а кабинетные SL, ожидание и разговор остаются из снимка."""
        calls = [_call('2026-09-23 08:10:00'), _call('2026-09-23 09:10:00'),
                 _call('2026-09-23 09:20:00', 'NOANSWER', 'Очередь')]
        _ns, data = _assemble(_snapshot(), calls)
        self.assertFalse(data['day_closed'])
        self.assertEqual(data['day_label'], 'за день')
        self.assertEqual((data['tp_day']['arrived'], data['tp_day']['lost']), (3, 1))
        self.assertEqual(data['tp_day']['sl_ratio'], 0.9)
        self.assertEqual((data['tp_hour']['arrived'], data['tp_hour']['lost']), (2, 1))
        self.assertEqual(data['hour_label'], '09:00–10:00')
        self.assertEqual(data['stamp'], '23.09 10:00')

    def test_midnight_reports_the_closed_day_without_cabinet_numbers(self):
        """В 00:00 час и итог дня — вчерашние, а снимок живёт новыми сутками. Кабинетных цифр за
        прошлые сутки взять негде — их нет вовсе, а не нули нового дня."""
        calls = [_call('2026-09-22 23:10:00'), _call('2026-09-22 23:20:00', 'NOANSWER', 'Очередь'),
                 _call('2026-09-22 12:00:00')]
        _ns, data = _assemble(_snapshot(), calls, now=datetime(2026, 9, 23, 0, 0, 4))
        self.assertTrue(data['day_closed'])
        self.assertEqual(data['day_label'], 'за 22.09')
        self.assertEqual(data['hour_label'], '23:00–24:00')
        self.assertEqual(data['tp_day'], {'arrived': 3, 'served': 2, 'lost': 1,
                                          'ar_ratio': round(1 / 3.0, 4)})
        self.assertEqual((data['tp_hour']['arrived'], data['tp_hour']['lost']), (2, 1))

    def test_line_is_found_by_the_roster_when_the_snapshot_missed_it(self):
        calls = [_call('2026-09-23 09:10:00', stage='901'), _call('2026-09-23 09:20:00', stage='901')]
        snapshot = _snapshot(diagnostics={})
        ns = _namespace(db=_AssembleDb(), people={'tp': [{'id': 1, 'sip_number': '901'}], 'op': []})
        data = ns['_tez_broadcast_assemble'](snapshot, calls, None, datetime(2026, 9, 23, 10, 0, 5))
        self.assertEqual(data['tp_hour']['arrived'], 2)

    def test_no_journal_means_no_hour(self):
        _ns, data = _assemble(_snapshot(), None, journal_error='too frequent')
        self.assertIsNone(data['tp_hour'])
        # Итог дня тогда остаётся кабинетным, как на стене, — без подмены.
        self.assertEqual(data['tp_day']['arrived'], 99)


class TezBroadcastDeviationTests(unittest.TestCase):
    """Отклонения: AR прошедшего часа выше потолка при достаточной выборке и молчание источника."""

    def _deviations(self, hour, **data):
        ns = _namespace()
        base = {'tp': {'now': {}}, 'tp_hour': hour, 'hour_label': '09:00–10:00',
                'snapshot_stale': False, 'snapshot_age_seconds': 0}
        base.update(data)
        return ns['_tez_broadcast_deviations'](base)

    def test_high_ar_with_enough_calls_is_a_warning_with_counts(self):
        notes = self._deviations({'arrived': 5, 'served': 4, 'lost': 1, 'ar_ratio': 0.2})
        self.assertEqual(len(notes), 1)
        self.assertIn('Обратите внимание (за час 09:00–10:00)', notes[0])
        self.assertIn('20,0 %', notes[0])
        # Выборка крошечная — без «1 из 5» процент читался бы как авария.
        self.assertIn('(1 из 5)', notes[0])
        self.assertIn('до 5 %', notes[0])

    def test_too_few_calls_are_not_judged(self):
        self.assertEqual(self._deviations({'arrived': 4, 'served': 3, 'lost': 1, 'ar_ratio': 0.25}), [])

    def test_ar_at_the_ceiling_is_the_norm(self):
        """Потолок: ровно 5 % — норма, как у плитки (arCeilingTone)."""
        self.assertEqual(self._deviations({'arrived': 20, 'served': 19, 'lost': 1, 'ar_ratio': 0.05}), [])

    def test_silent_sources_are_deviations(self):
        self.assertIn('Журнал звонков Binotel не обновился', self._deviations(None)[0])
        self.assertIn('Страница очереди Binotel недоступна', self._deviations({}, tp=None)[0])
        stale = self._deviations({'arrived': 0, 'served': 0, 'lost': 0, 'ar_ratio': None},
                                 snapshot_stale=True, snapshot_age_seconds=900)
        self.assertIn('Binotel', stale[-1])
        self.assertIn('15 минут', stale[-1])
        self.assertEqual(self._deviations({}, snapshot_error='boom'),
                         ['Binotel не отвечает — показателей табло Тез КЦ нет.'])

    def test_break_violations_are_not_deviations(self):
        """Решение владельца 23.09.2026: перерывы — строкой в подписи, но режим «только при
        отклонениях» они не будят (так же у «Линии»)."""
        calm = {'arrived': 6, 'served': 6, 'lost': 0, 'ar_ratio': 0.0}
        notes = self._deviations(calm, break_violations=[VIOLATION])
        self.assertEqual(notes, [])


class TezBroadcastCaptionTests(unittest.TestCase):
    """Подпись: отклонения, под ними перерывы; лимит подписи альбома Telegram."""

    def _data(self, violations=(), hour=None):
        return {'tp': {'now': {}}, 'hour_label': '09:00–10:00', 'snapshot_stale': False,
                'tp_hour': hour or {'arrived': 5, 'served': 4, 'lost': 1, 'ar_ratio': 0.2},
                'break_violations': list(violations)}

    def test_warning_first_then_breaks(self):
        ns = _namespace()
        caption = ns['_tez_broadcast_caption'](self._data([VIOLATION]))
        self.assertLess(caption.index('Обратите внимание'), caption.index('Перерывы не по графику'))
        self.assertIn('Тлеу Аскар', caption)

    def test_calm_hour_without_breaks_has_no_caption(self):
        ns = _namespace()
        calm = {'arrived': 6, 'served': 6, 'lost': 0, 'ar_ratio': 0.0}
        self.assertEqual(ns['_tez_broadcast_caption'](self._data(hour=calm)), '')

    def test_long_break_list_is_cut_to_fit_the_album_caption(self):
        """Подпись альбома — до 1024 символов; не влезли перерывы — режем их до счётчика, а
        предупреждение не трогаем никогда."""
        ns = _namespace()
        long_name = 'Оператор с очень длинной фамилией и именем ' * 3
        rows = [dict(VIOLATION, id=i, operator_name=long_name) for i in range(6)]
        caption = ns['_tez_broadcast_caption'](self._data(rows))
        self.assertLessEqual(len(caption), ns['TELEGRAM_MAX_CAPTION_CHARS'])
        self.assertIn('Обратите внимание', caption)
        self.assertIn('и ещё', caption)


class TezBroadcastPictureTests(unittest.TestCase):
    """Картинки: те же плитки и тот же цвет, что на стене."""

    def setUp(self):
        self.captured = []
        self.ns = _namespace(db=_AssembleDb(), extra={})
        self.ns['_szov_render_tiles_png'] = lambda title, subtitle, key_tiles, stat_tiles: (
            self.captured.append((title, subtitle, key_tiles, stat_tiles)) or b'png')

    def _data(self, **extra):
        calls = [_call('2026-09-23 09:10:00'), _call('2026-09-23 09:20:00', 'NOANSWER', 'Очередь')]
        data = self.ns['_tez_broadcast_assemble'](_snapshot(), calls, None,
                                                  datetime(2026, 9, 23, 10, 0, 5))
        data.update(extra)
        return data

    def test_ar_tile_follows_the_ceiling(self):
        colors = self.ns['_tez_broadcast_ar_colors']
        self.assertEqual(colors(0.05), ('#d1fae5', '#047857'))
        self.assertEqual(colors(0.07), ('#fef3c7', '#b45309'))
        self.assertEqual(colors(0.0701), ('#ffe4e6', '#be123c'))
        self.assertEqual(colors(None), ('#f1f5f9', '#334155'))

    def test_day_picture_has_cabinet_row_and_people_now(self):
        self.ns['_tez_render_tp_day_png'](self._data())
        title, subtitle, keys, rows = self.captured[-1]
        self.assertEqual(title, 'Табло Тез КЦ · ТП')
        self.assertIn('за день', subtitle)
        self.assertEqual([tile[0] for tile in keys], ['Входящих', 'Принято', 'Потеряно', 'AR'])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], ('SL', '90,0 %'))
        self.assertEqual(rows[1][0], ('В очереди', '1'))

    def test_closed_day_picture_drops_the_cabinet_row(self):
        self.ns['_tez_render_tp_day_png'](self._data(day_closed=True, day_label='за 22.09'))
        _title, subtitle, _keys, rows = self.captured[-1]
        self.assertIn('за 22.09', subtitle)
        self.assertEqual(len(rows), 1)

    def test_hour_picture_is_only_intake(self):
        """SL и ожидание кабинет отдаёт только за день — у часа их нет вовсе."""
        self.ns['_tez_render_tp_hour_png'](self._data())
        title, subtitle, keys, rows = self.captured[-1]
        self.assertEqual(title, 'Табло Тез КЦ · ТП · за час')
        self.assertIn('09:00–10:00', subtitle)
        self.assertEqual(keys[0][1], '2')
        self.assertEqual(rows, [])

    def test_empty_count_is_a_dash_not_zero(self):
        self.ns['_tez_render_tp_hour_png'](self._data(tp_hour={}))
        _title, _subtitle, keys, _rows = self.captured[-1]
        self.assertEqual([tile[1] for tile in keys], ['—', '—', '—', '—'])

    def test_op_picture_mirrors_the_wall(self):
        self.ns['_tez_render_op_png'](self._data())
        title, _subtitle, keys, rows = self.captured[-1]
        self.assertEqual(title, 'Табло Тез КЦ · ОП')
        self.assertEqual([tile[0] for tile in keys], ['Онлайн', 'Свободны', 'В разговоре', 'Перерыв'])
        self.assertEqual(rows[0], [('Поднято / совершено', '65 / 152'), ('Дозвон', '42,8 %'),
                                   ('Ср. разговор', '1:35')])
        # Жёлтый — только когда на перерыве кто-то есть: жёлтый ноль — цвет без смысла.
        self.assertEqual(keys[3][2:], ('#f1f5f9', '#334155'))
        on_break = dict(_snapshot()['op'], now={'operators_on_break': 2})
        self.ns['_tez_render_op_png'](self._data(op=on_break))
        self.assertEqual(self.captured[-1][2][3][2:], ('#fef3c7', '#b45309'))

    def test_renderer_draws_no_rows_for_an_empty_list(self):
        renderer = SOURCE[SOURCE.index('def _szov_render_tiles_png('):]
        renderer = renderer[:renderer.index('\ndef ', 10)]
        self.assertIn('if not stat_tiles:\n        rows = []', renderer)


class TezBroadcastJournalTests(unittest.TestCase):
    """Журнал для отбивки: выкачанный ПОСЛЕ конца часа, а не какой лежит в кэше."""

    def _ns(self, journal, api_calls=None, wait=30):
        ns = _namespace()
        ns['TEZ_BROADCAST_JOURNAL_WAIT_SECONDS'] = wait
        ns['_tez_wallboard_journal'] = journal

        class _FastAsyncio:
            get_event_loop = staticmethod(asyncio.get_event_loop)

            @staticmethod
            async def sleep(_seconds):
                return None
        ns['asyncio'] = _FastAsyncio
        return ns

    def test_waits_until_the_journal_covers_the_hour(self):
        """Кэш табло свежий, но выкачан до конца часа — ждём следующую выкачку."""
        hour_end = time.time() - 30
        answers = [(['old'], 40, None), (['old'], 40, None), (['fresh'], 5, None)]
        ns = self._ns(lambda day_key: answers.pop(0))
        today = datetime.now(ZONE).strftime('%Y-%m-%d')
        calls, error = asyncio.run(ns['_tez_broadcast_journal'](today, hour_end))
        self.assertEqual(calls, ['fresh'])
        self.assertIsNone(error)

    def test_gives_up_with_a_dash_rather_than_a_cut_hour(self):
        ns = self._ns(lambda day_key: (['old'], 999, 'too frequent'), wait=0)
        today = datetime.now(ZONE).strftime('%Y-%m-%d')
        calls, error = asyncio.run(ns['_tez_broadcast_journal'](today, time.time()))
        self.assertIsNone(calls)
        self.assertEqual(error, 'too frequent')

    def test_yesterday_is_fetched_directly_not_through_the_wall_cache(self):
        """Вчерашние сутки в кэше табло не лежат, и выбивать оттуда сегодняшний журнал нельзя."""
        body = SOURCE[SOURCE.index('async def _tez_broadcast_journal('):]
        body = body[:body.index('\nasync def ', 10)]
        self.assertIn('if day_key != today_key:', body)
        self.assertIn('list_calls_for_day(day_key)', body)
        direct = body[body.index('if day_key != today_key:'):body.index('deadline =')]
        self.assertNotIn('_tez_wallboard_journal', direct)
        self.assertIn('run_in_executor(\n                None,', direct)


# --- Общий обход получателей: группы и личные подписчики ----------------------------------------

class _FakeBot:
    """Доставка, которая запоминает адресатов и умеет «быть заблокированной»."""

    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    async def deliver(self, chat_id, text, media):
        if chat_id in self.fail_for:
            raise RuntimeError('Forbidden: bot was blocked by the user')
        self.sent.append((chat_id, text, media))


class _RunJobDb:
    def __init__(self, chats=()):
        self.chats = list(chats)

    def get_szov_broadcast_chats(self, direction):
        assert direction == 'tez', direction
        return list(self.chats)


VIOLATION = {'id': 11, 'operator_name': 'Тлеу Аскар', 'started_at': '2026-09-22 16:00:00',
             'violation_date': '2026-09-22', 'kind': 'not_planned', 'planned_start_minutes': None}


def _run_job(chats=(), people=(), notes=(), fail_for=()):
    bot = _FakeBot(fail_for)
    delivered = []
    prepared = []

    def personal():
        return list(people)

    async def prepare():
        prepared.append(1)
        return {'break_violations': [VIOLATION]}, 'caption', [('a.png', b'1'), ('b.png', b'2')]

    async def on_delivered(data):
        delivered.append(data)

    ns = _namespace(db=_RunJobDb(chats), extra={'_szov_broadcast_deliver': bot.deliver})
    asyncio.run(ns['_szov_broadcast_run_job'](
        direction='tez', label='Отбивка табло Тез КЦ', prepare=prepare,
        deviations=lambda data: list(notes), on_delivered=on_delivered, personal=personal))
    return bot, delivered, prepared


class TezBroadcastRecipientsTests(unittest.TestCase):
    """Группы и админы, включившие отбивку себе, идут одним обходом со своими режимами."""

    ADMIN = {'id': 5, 'name': 'Админ', 'telegram_id': 555, 'mode': 'always'}

    def test_admin_gets_it_even_without_any_group(self):
        bot, delivered, prepared = _run_job(people=[self.ADMIN])
        self.assertEqual([item[0] for item in bot.sent], [555])
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(delivered), 1)

    def test_groups_and_admins_get_one_and_the_same_album(self):
        bot, _delivered, prepared = _run_job(
            chats=[{'chat_id': -100, 'is_enabled': True, 'mode': 'always'},
                   {'chat_id': -200, 'is_enabled': False, 'mode': 'always'}],
            people=[self.ADMIN])
        self.assertEqual(sorted(item[0] for item in bot.sent), [-100, 555])
        # Сбор один на всех получателей.
        self.assertEqual(len(prepared), 1)

    def test_personal_mode_is_respected(self):
        """«Только при отклонениях» у личной подписки работает так же, как у группы."""
        quiet = dict(self.ADMIN, mode='deviations')
        bot, delivered, _prepared = _run_job(people=[quiet])
        self.assertEqual(bot.sent, [])
        self.assertEqual(delivered, [])
        bot, _delivered, _prepared = _run_job(people=[quiet], notes=['Обратите внимание…'])
        self.assertEqual([item[0] for item in bot.sent], [555])

    def test_blocked_bot_does_not_cost_the_others(self):
        bot, delivered, _prepared = _run_job(
            chats=[{'chat_id': -100, 'is_enabled': True, 'mode': 'always'}],
            people=[self.ADMIN], fail_for={555})
        self.assertEqual([item[0] for item in bot.sent], [-100])
        self.assertEqual(len(delivered), 1)

    def test_nobody_to_write_means_nothing_is_collected(self):
        """Получателей нет — отбивка не ходит в Binotel вовсе."""
        bot, _delivered, prepared = _run_job(chats=[{'chat_id': -100, 'is_enabled': False}])
        self.assertEqual(bot.sent, [])
        self.assertEqual(prepared, [])

    def test_failing_personal_lookup_does_not_silence_the_groups(self):
        class _Db(_RunJobDb):
            def get_tez_broadcast_personal_recipients(self, tez_department_id=None):
                raise RuntimeError('column does not exist')
        ns = _namespace(db=_Db(), extra={'_tez_wallboard_department_id': lambda: 7})
        with self.assertLogs(level='ERROR'):
            self.assertEqual(ns['_tez_broadcast_personal_recipients'](), [])

    def test_personal_recipients_are_judged_by_the_tez_department(self):
        calls = []

        class _Db(_RunJobDb):
            def get_tez_broadcast_personal_recipients(self, tez_department_id=None):
                calls.append(tez_department_id)
                return []
        ns = _namespace(db=_Db(), extra={'_tez_wallboard_department_id': lambda: 7})
        ns['_tez_broadcast_personal_recipients']()
        self.assertEqual(calls, [7])


def _owner_namespace(requester, db=None):
    """requester — кортеж как у db.get_user: (id, telegram_id, name, role)."""
    return _namespace(db=db or _PersonalDb(), extra={
        '_get_authenticated_requester': lambda: (requester[0], requester, None),
    })


class _PersonalDb:
    def __init__(self, states=None):
        self.states = states or {}

    def get_tez_broadcast_personal(self, user_id):
        return self.states.get(user_id, {'enabled': False, 'mode': 'always'})


class TezPersonalBroadcastOwnerTests(unittest.TestCase):
    """Кому форма показывает строку «лично мне». Гейт отбивки к этому моменту пройден:
    админ — глава чужого отдела табло Тез не видит и сюда не попадает."""

    def test_admin_and_super_admin_get_the_row(self):
        for role in ('admin', 'super_admin', 'superadmin', 'Super Admin'):
            with self.subTest(role=role):
                ns = _owner_namespace((300, 777, 'Глава', role))
                self.assertIsNotNone(ns['_szov_broadcast_personal_owner']('tez'))

    def test_roles_below_admin_do_not(self):
        """Форму открывает любой глава Тез КЦ, но лично себе получает только админ."""
        for role in ('sv', 'supervisor', 'operator', 'trainer', ''):
            with self.subTest(role=role):
                ns = _owner_namespace((3, 777, 'СВ', role))
                self.assertIsNone(ns['_szov_broadcast_personal_owner']('tez'))
                self.assertIsNone(ns['_szov_broadcast_personal_state']('tez'))

    def test_only_the_tez_wallboard_has_it(self):
        ns = _owner_namespace((300, 777, 'Админ', 'admin'))
        for direction in ('osnova', 'chat', 'op'):
            with self.subTest(direction=direction):
                self.assertIsNone(ns['_szov_broadcast_personal_owner'](direction))
                self.assertIsNone(ns['_szov_broadcast_personal_state'](direction))

    def test_state_says_mode_and_whether_telegram_is_linked(self):
        db = _PersonalDb({300: {'enabled': True, 'mode': 'deviations'}})
        linked = _owner_namespace((300, 777, 'Админ', 'admin'), db=db)
        self.assertEqual(linked['_szov_broadcast_personal_state']('tez'),
                         {'enabled': True, 'mode': 'deviations', 'telegram_connected': True})
        unlinked = _owner_namespace((301, None, 'Админ', 'admin'), db=db)
        self.assertEqual(unlinked['_szov_broadcast_personal_state']('tez'),
                         {'enabled': False, 'mode': 'always', 'telegram_connected': False})


class TezPersonalBroadcastWiringTests(unittest.TestCase):
    """Ручки, схема и форма личной отбивки."""

    def _handler(self, name):
        body = SOURCE[SOURCE.index(f'def {name}():'):]
        return body[:body.index('\n@app.route')]

    def test_settings_endpoint_toggles_it_behind_the_broadcast_guard(self):
        handler = self._handler('api_szov_wallboard_broadcast')
        marker = "elif 'personal' in payload or 'personal_mode' in payload:"
        self.assertLess(handler.index('requester_id, err = _szov_broadcast_guard()'),
                        handler.index(marker))
        branch = handler[handler.index(marker):handler.index('            else:')]
        self.assertIn('owner = _szov_broadcast_personal_owner(direction)', branch)
        self.assertIn('if owner is None:', branch)
        # Включить без Telegram нельзя — писать некуда; выключить можно всегда.
        self.assertIn('if enabled and not owner[1]:', branch)
        self.assertIn("mode=payload.get('personal_mode')", branch)
        # Личная настройка — не общий список получателей: в «кто менял» не пишется.
        self.assertNotIn('_log_szov_broadcast_change', branch)
        self.assertIn('"personal": _szov_broadcast_personal_state(direction),', handler)

    def test_test_send_writes_only_to_the_one_who_pressed(self):
        handler = self._handler('api_szov_wallboard_broadcast_test')
        branch = handler[handler.index("if payload.get('personal'):"):handler.index('    else:')]
        self.assertIn('owner = _szov_broadcast_personal_owner(direction)', branch)
        self.assertIn('chat_id = int(owner[1])', branch)
        self.assertNotIn("payload.get('chat_id')", branch)

    def test_schema_and_recipient_rule(self):
        self.assertIn('ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS '
                      'tez_broadcast_personal_enabled BOOLEAN NOT NULL DEFAULT FALSE;', DB_SOURCE)
        self.assertIn('ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS '
                      "tez_broadcast_personal_mode VARCHAR(16) NOT NULL DEFAULT 'always';", DB_SOURCE)
        method = DB_SOURCE[DB_SOURCE.index('def get_tez_broadcast_personal_recipients('):]
        method = method[:method.index('\n# Initialize database')]
        self.assertIn('u.telegram_id IS NOT NULL', method)
        self.assertIn("NOT IN ('fired', 'dismissal')", method)
        # Лестница раздела: супер-админ, админ без отдела, админ — глава Тез КЦ.
        self.assertIn("IN ('super_admin', 'superadmin', 'super-admin', 'super admin')", method)
        self.assertIn("LOWER(COALESCE(u.role, '')) = 'admin'", method)
        self.assertIn('NOT EXISTS (', method)
        self.assertIn('AND d.id = %s', method)
        self.assertIn("COALESCE(ap.tez_broadcast_personal_mode, 'always')", method)
        setter = DB_SOURCE[DB_SOURCE.index('def set_tez_broadcast_personal('):]
        setter = setter[:setter.index('\n    def ', 10)]
        self.assertIn('if mode not in self.SZOV_BROADCAST_MODES:', setter)

    def test_form_shows_the_row_only_when_the_server_sends_it(self):
        self.assertIn("const personal = state?.personal || null;", SZOV_VIEW)
        self.assertIn('{personal ? (', SZOV_VIEW)
        self.assertIn('Лично мне в Telegram', SZOV_VIEW)
        self.assertIn("{ personal: next }", SZOV_VIEW)
        self.assertIn("{ personal_mode: mode }", SZOV_VIEW)
        self.assertIn('personal.enabled && withModes ?', SZOV_VIEW)
        self.assertIn('sendNow({ personal: true })', SZOV_VIEW)
        self.assertIn('sendNow({ chat_id: item.chat_id })', SZOV_VIEW)
        # Переключатель гаснет только на включение: выключить без Telegram можно.
        self.assertIn('disabled={busy || (!personal.enabled && !personal.telegram_connected)}', SZOV_VIEW)

if __name__ == '__main__':
    unittest.main()
