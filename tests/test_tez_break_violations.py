"""Перерывы вне графика у Тез КЦ и отбивка о них в Telegram (возврат задачи #292).

Правило сверки общее с СЗоВ (_szov_break_classify) — здесь стережём то, что у Тез своё:
источник факта (события телефона iCORE Phone вместо истории Oktell), одно направление на
оба табло отдела и отбивка, которая молчит, когда нарушений нет.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном namespace: так
проверяется настоящая логика. Импортировать модуль нельзя — на старте он поднимает пул к
боевой БД (тот же приём в test_szov_break_violations.py).
"""
import ast
import html
import logging
import os
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
DB_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
VIEW = (ROOT / "src" / "components" / "monitoring" / "TezWallboardView.jsx").read_text(encoding="utf-8-sig")

NAMES = {
    '_env_int',
    'SZOV_BREAK_MIN_MINUTES', 'SZOV_BREAK_TOLERANCE_MINUTES', 'SZOV_BREAK_MERGE_GAP_MINUTES',
    'SZOV_BREAK_SCAN_LOOKBACK_HOURS', 'SZOV_BREAK_REPORT_MAX_AGE_HOURS',
    'SZOV_BREAK_KIND_OFF_SCHEDULE', 'SZOV_BREAK_KIND_NOT_PLANNED', 'SZOV_BREAK_KIND_NO_SHIFT',
    'SZOV_BREAK_DIRECTION_LINE', 'SZOV_BREAK_DIRECTION_TEZ',
    '_szov_break_merge_episodes', '_szov_break_planned_for_day', '_szov_break_on_shift',
    '_szov_break_classify', '_szov_break_violation_detail',
    '_TEZ_WALLBOARD_MODEL_BY_DIRECTION', 'TEZ_BREAK_NOTE_LIMIT',
    '_tez_break_status_keys', '_tez_break_violations_scan', '_tez_break_broadcast_text',
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


def _namespace(db=None, people=None):
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
        'os': os, 're': re, 'logging': logging, 'html': html,
        'datetime': datetime, 'timedelta': timedelta,
        '_env_int': lambda name, default, minimum=None, maximum=None: default,
        'db': db if db is not None else _FakeDb(),
        # Состав отдела берётся у табло — здесь подменяем, чтобы не ходить в базу.
        '_tez_wallboard_people': lambda *args, **kwargs: roster,
    }
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


class TezBreakBroadcastTextTests(unittest.TestCase):
    """Текст сообщения: молчание без нарушений и человеческие формулировки."""

    def setUp(self):
        self.ns = _namespace()

    def _violation(self, name, started, kind='not_planned', planned_start=None):
        return {'operator_name': name, 'started_at': started, 'violation_date': started[:10],
                'kind': kind, 'planned_start_minutes': planned_start}

    def test_no_violations_means_no_message(self):
        """Пустой текст — это и есть «писать не о чем»: джоба тогда молчит."""
        self.assertEqual(self.ns['_tez_break_broadcast_text']([]), '')
        self.assertEqual(self.ns['_tez_break_broadcast_text'](None), '')

    def test_line_names_the_person_the_time_and_the_reason(self):
        text = self.ns['_tez_break_broadcast_text']([
            self._violation('Тлеу Аскар', '2026-09-22 16:00:00'),
        ])
        self.assertIn('Тез КЦ', text)
        self.assertIn('Тлеу Аскар', text)
        self.assertIn('22.09', text)
        self.assertIn('16:00', text)
        self.assertIn('перерывов в графике на этот день нет', text)

    def test_wording_is_the_same_as_in_the_szov_broadcast(self):
        """Одно нарушение обязано читаться одинаково, в какой бы отдел про него ни написали."""
        row = self._violation('Тлеу Аскар', '2026-09-22 16:00:00',
                              kind='off_schedule', planned_start=840)
        text = self.ns['_tez_break_broadcast_text']([row])
        self.assertIn(self.ns['_szov_break_violation_detail'](row), text)

    def test_long_list_is_cut_with_a_counter(self):
        """Поимённо — до предела, остальные счётчиком: сообщение читают с телефона."""
        limit = self.ns['TEZ_BREAK_NOTE_LIMIT']
        rows = [self._violation(f'Оператор {i}', '2026-09-22 16:00:00') for i in range(limit + 3)]
        text = self.ns['_tez_break_broadcast_text'](rows)
        self.assertEqual(text.count('•'), limit)
        self.assertIn('и ещё 3', text)

    def test_names_are_escaped_for_html(self):
        """Сообщение уходит с parse_mode HTML: имя с «<» иначе уронило бы отправку."""
        text = self.ns['_tez_break_broadcast_text']([
            self._violation('Тлеу <Аскар>', '2026-09-22 16:00:00'),
        ])
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
        """Смена отдела начинается затемно, а окно захода — три часа: гоняй разбор только
        по часам отбивки, и перерыв в 05:22 не разобрал бы никто."""
        self.assertIn('tez_break_scan_job,', SOURCE)
        self.assertIn("id='tez_break_scan'", SOURCE)
        self.assertIn("CronTrigger(hour='*', minute=TEZ_BREAK_SCAN_MINUTE,", SOURCE)
        # А сообщения — по своему расписанию, в рабочие часы.
        self.assertIn('tez_break_broadcast_job,', SOURCE)
        self.assertIn("id=f'tez_break_broadcast_{_hour:02d}{_minute:02d}'", SOURCE)
        self.assertIn('def _tez_break_broadcast_send_times():', SOURCE)

    def test_journal_fills_even_without_recipients(self):
        """Журнал в iCore обязан наполняться и тогда, когда отбивку никто не получает."""
        scan = SOURCE[SOURCE.index('async def tez_break_scan_job():'):]
        scan = scan[:scan.index('\nasync def ', 10)]
        self.assertIn('_tez_break_violations_scan', scan)
        self.assertNotIn('get_szov_broadcast_chats', scan)
        job = SOURCE[SOURCE.index('async def tez_break_broadcast_job():'):]
        job = job[:job.index('\n@app.route')]
        # Отправка разбор НЕ повторяет: он идёт своей почасовой джобой.
        self.assertNotIn('_tez_break_violations_scan', job)
        self.assertIn('if not recipients:', job)
        # Помечаем прочитанными только после удачной доставки: разом упавшая отправка
        # иначе проглотила бы предупреждения.
        self.assertIn('if delivered:', job)
        self.assertIn('mark_szov_break_violations_reported', job)

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

    def test_test_send_writes_even_when_there_is_nothing_to_report(self):
        """Кнопку нажал человек и ждёт ответа: молчание тут читалось бы как поломка."""
        send = SOURCE[SOURCE.index('async def _tez_break_broadcast_send('):]
        send = send[:send.index('\nasync def ', 10)]
        self.assertIn('сообщение проверочное', send)
        self.assertNotIn('mark_szov_break_violations_reported', send)
        self.assertIn('SZOV_BROADCAST_DIRECTION_TEZ: _tez_break_broadcast_send,', SOURCE)

    def test_screen_has_both_buttons_and_hides_the_modes(self):
        """Режимы получателя у Тез бессмысленны: сообщение и так уходит только при
        нарушениях, а выбор, который ни на что не влияет, — обещание без содержания."""
        self.assertIn('<BreakViolationsControls', VIEW)
        self.assertIn('<BroadcastControls', VIEW)
        self.assertIn('withModes={false}', VIEW)
        self.assertIn('canManageBroadcast ?', VIEW)
        # Журнал открыт всем, кто видит табло, — он стоит вне проверки прав на отбивку.
        self.assertLess(VIEW.index('<BreakViolationsControls'), VIEW.index('canManageBroadcast ?'))


if __name__ == '__main__':
    unittest.main()
