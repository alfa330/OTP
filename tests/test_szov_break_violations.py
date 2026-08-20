"""Выходы на перерыв мимо графика (задача #114): склейка эпизодов, сверка с графиком, текст.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном namespace —
так проверяется настоящая логика. Импортировать модуль нельзя: на старте он поднимает пул
к боевой БД (тот же приём в test_szov_wallboard.py и test_chat_hourly_report.py).
"""
import ast
import logging
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

NAMES = {
    '_env_int', '_szov_plural',
    'SZOV_BREAK_ICODE', 'SZOV_BREAK_MIN_MINUTES', 'SZOV_BREAK_TOLERANCE_MINUTES',
    'SZOV_BREAK_MERGE_GAP_MINUTES', 'SZOV_BREAK_SCAN_LOOKBACK_HOURS',
    'SZOV_BREAK_REPORT_MAX_AGE_HOURS', 'SZOV_BREAK_NOTE_LIMIT',
    'SZOV_BREAK_KIND_OFF_SCHEDULE', 'SZOV_BREAK_KIND_NOT_PLANNED', 'SZOV_BREAK_KIND_NO_SHIFT',
    '_oktell_break_episodes_sql', '_szov_break_parse_time', '_szov_break_merge_episodes',
    '_szov_break_planned_for_day', '_szov_break_classify',
    '_szov_break_violation_detail', '_szov_break_violation_notes',
}


def _namespace():
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
    ns = {'os': os, 'logging': logging, 'datetime': datetime, 'timedelta': timedelta}
    exec(compile(module, "<szov-breaks>", "exec"), ns)
    missing = sorted(name for name in NAMES if name not in ns)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return ns


def _episode(operator_id, name, start, end=None):
    parse = '%Y-%m-%d %H:%M:%S'
    return {
        'operator_id': operator_id,
        'name': name,
        'started_at': datetime.strptime(start, parse),
        'ended_at': None if end is None else datetime.strptime(end, parse),
    }


NOW = datetime(2026, 8, 19, 23, 59, 0)


class BreakEpisodeSqlTests(unittest.TestCase):
    """Запрос к Oktell: только «Перерыв», и ни одного слова из чёрного списка прокси."""

    def setUp(self):
        self.ns = _namespace()

    def test_only_the_break_reason_is_selected(self):
        """State=2 — любая пауза; перерыв из графика — это ровно ICode 4."""
        sql = self.ns['_oktell_break_episodes_sql'](datetime(2026, 8, 19, 10), datetime(2026, 8, 19, 13))
        self.assertIn('x.State = 2', sql)
        self.assertIn(f"x.ICode = {self.ns['SZOV_BREAK_ICODE']}", sql)
        self.assertEqual(self.ns['SZOV_BREAK_ICODE'], 4)

    def test_dates_go_as_iso_not_as_locale_dependent_literals(self):
        """Локаль сервера dmy: '2026-08-19 10:00' там падает с ошибкой 242."""
        sql = self.ns['_oktell_break_episodes_sql'](datetime(2026, 8, 19, 10), datetime(2026, 8, 19, 13))
        self.assertIn("CONVERT(datetime, '2026-08-19T10:00:00', 126)", sql)
        self.assertIn("CONVERT(datetime, '2026-08-19T13:00:00', 126)", sql)

    def test_query_passes_the_proxy_keyword_blocklist(self):
        """Прокси Oktell режет запрос, если в ТЕКСТЕ есть insert/update/delete/drop."""
        sql = self.ns['_oktell_break_episodes_sql'](datetime(2026, 8, 19, 10), datetime(2026, 8, 19, 13)).lower()
        for word in ('insert', 'update', 'delete', 'drop'):
            self.assertNotIn(word, sql, f"прокси отклонит запрос из-за подстроки {word!r}")

    def test_single_statement_only(self):
        """Несколько запросов в одной строке прокси запрещает (HTTP 400)."""
        sql = self.ns['_oktell_break_episodes_sql'](datetime(2026, 8, 19, 10), datetime(2026, 8, 19, 13))
        self.assertNotIn(';', sql)


class BreakEpisodeMergeTests(unittest.TestCase):
    """Мерцание статуса — один перерыв, а не два нарушения."""

    def setUp(self):
        self.ns = _namespace()

    def test_short_gap_between_episodes_is_one_break(self):
        merged = self.ns['_szov_break_merge_episodes']([
            _episode(1, 'Иванов', '2026-08-19 14:00:00', '2026-08-19 14:05:00'),
            _episode(1, 'Иванов', '2026-08-19 14:06:00', '2026-08-19 14:20:00'),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['started_at'], datetime(2026, 8, 19, 14, 0))
        self.assertEqual(merged[0]['ended_at'], datetime(2026, 8, 19, 14, 20))

    def test_long_gap_keeps_two_breaks(self):
        merged = self.ns['_szov_break_merge_episodes']([
            _episode(1, 'Иванов', '2026-08-19 14:00:00', '2026-08-19 14:05:00'),
            _episode(1, 'Иванов', '2026-08-19 15:00:00', '2026-08-19 15:20:00'),
        ])
        self.assertEqual(len(merged), 2)

    def test_episodes_of_different_operators_never_merge(self):
        merged = self.ns['_szov_break_merge_episodes']([
            _episode(1, 'Иванов', '2026-08-19 14:00:00', '2026-08-19 14:05:00'),
            _episode(2, 'Петров', '2026-08-19 14:05:30', '2026-08-19 14:20:00'),
        ])
        self.assertEqual(len(merged), 2)

    def test_open_episode_keeps_open_end(self):
        """Оператор всё ещё на перерыве: конца нет, длительность уточнит следующий заход."""
        merged = self.ns['_szov_break_merge_episodes']([
            _episode(1, 'Иванов', '2026-08-19 14:00:00', '2026-08-19 14:05:00'),
            _episode(1, 'Иванов', '2026-08-19 14:06:00', None),
        ])
        self.assertEqual(len(merged), 1)
        self.assertIsNone(merged[0]['ended_at'])


class BreakScheduleMatchTests(unittest.TestCase):
    """Сверка с графиком: перерыв обязан стоять у ЭТОГО оператора и на ЭТО время."""

    def setUp(self):
        self.ns = _namespace()
        self.plan = {(1, '2026-08-19'): [(14 * 60, 14 * 60 + 15)]}
        self.shift_days = {(1, '2026-08-19')}

    def _classify(self, episodes, plan=None, shift_days=None):
        return self.ns['_szov_break_classify'](
            episodes,
            self.plan if plan is None else plan,
            self.shift_days if shift_days is None else shift_days,
            NOW,
        )

    def test_break_inside_the_planned_window_is_not_a_violation(self):
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 14:02:00', '2026-08-19 14:14:00')])
        self.assertEqual(found, [])

    def test_small_shift_stays_within_tolerance(self):
        """Перерывы стоят на пятиминутной сетке — «вышел на 8 минут раньше» не нарушение."""
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 13:52:00', '2026-08-19 14:05:00')])
        self.assertEqual(found, [])

    def test_break_at_a_different_hour_is_a_violation(self):
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 16:30:00', '2026-08-19 16:45:00')])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_OFF_SCHEDULE'])
        self.assertEqual(found[0]['planned_start_minutes'], 14 * 60)
        self.assertEqual(found[0]['deviation_minutes'], 150)
        self.assertEqual(found[0]['duration_minutes'], 15)

    def test_shift_without_planned_breaks(self):
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 16:30:00', '2026-08-19 16:45:00')],
                               plan={}, shift_days={(1, '2026-08-19')})
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_NOT_PLANNED'])

    def test_day_without_a_shift_is_reported_separately(self):
        """«Смены нет» и «перерывов нет» — разные истории, и в списке они читаются по-разному."""
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 16:30:00', '2026-08-19 16:45:00')],
                               plan={}, shift_days=set())
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_NO_SHIFT'])

    def test_status_flicker_is_not_judged_at_all(self):
        """Из 787 эпизодов за пять дней 333 длились меньше минуты — это не перерывы."""
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 16:30:00', '2026-08-19 16:31:00')])
        self.assertEqual(found, [])

    def test_open_break_is_judged_by_the_time_already_spent(self):
        """Ещё не закончившийся перерыв судим по отсиженному, а конец дописываем позже."""
        now = datetime(2026, 8, 19, 16, 40)
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 16:30:00', None)], self.plan, self.shift_days, now)
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0]['ended_at'])
        self.assertIsNone(found[0]['duration_minutes'])

    def test_open_break_shorter_than_the_floor_waits_for_the_next_scan(self):
        now = datetime(2026, 8, 19, 16, 31)
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 16:30:00', None)], self.plan, self.shift_days, now)
        self.assertEqual(found, [])

    def test_night_shift_tail_is_matched_against_yesterdays_plan(self):
        """Ночная смена держит перерывы на дате смены, уводя минуты за 1440."""
        plan = {(1, '2026-08-18'): [(25 * 60, 25 * 60 + 15)]}   # 01:00 следующих суток
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 01:03:00', '2026-08-19 01:15:00')],
            plan, {(1, '2026-08-18')}, NOW)
        self.assertEqual(found, [])

    def test_planned_lookup_shifts_the_night_tail_back_a_day(self):
        plan = {(1, '2026-08-18'): [(25 * 60, 25 * 60 + 15)], (1, '2026-08-19'): [(600, 615)]}
        got = self.ns['_szov_break_planned_for_day'](plan, 1, datetime(2026, 8, 19).date())
        self.assertEqual(got, [(60, 75), (600, 615)])

    def test_daytime_breaks_of_the_previous_day_do_not_leak_in(self):
        """Сдвигаем только хвост за полночь: обычный вчерашний перерыв к сегодня не относится."""
        plan = {(1, '2026-08-18'): [(600, 615)]}
        self.assertEqual(self.ns['_szov_break_planned_for_day'](plan, 1, datetime(2026, 8, 19).date()), [])


class BreakParseTests(unittest.TestCase):
    def setUp(self):
        self.ns = _namespace()

    def test_parses_oktell_datetime(self):
        self.assertEqual(self.ns['_szov_break_parse_time']('2026-08-19 14:03:07'),
                         datetime(2026, 8, 19, 14, 3, 7))

    def test_empty_and_broken_values_are_none(self):
        for value in (None, '', '   ', 'нет данных'):
            self.assertIsNone(self.ns['_szov_break_parse_time'](value))


class BreakNoteTests(unittest.TestCase):
    """Текст уведомления: формулировка из постановки, но без шума при нескольких строках."""

    def setUp(self):
        self.ns = _namespace()

    def _violation(self, name, started, kind='off_schedule', planned=14 * 60):
        return {'operator_name': name, 'started_at': f'2026-08-19T{started}:00',
                'kind': kind, 'planned_start_minutes': planned}

    def test_no_violations_no_notes(self):
        self.assertEqual(self.ns['_szov_break_violation_notes']([]), [])
        self.assertEqual(self.ns['_szov_break_violation_notes'](None), [])

    def test_single_violation_uses_the_wording_from_the_task(self):
        notes = self.ns['_szov_break_violation_notes']([self._violation('Иванов Иван', '16:30')])
        self.assertEqual(len(notes), 1)
        self.assertIn('Обратите внимание: Иванов Иван вышел(а) на перерыв не по графику', notes[0])
        self.assertIn('в 16:30', notes[0])
        self.assertIn('по графику в 14:00', notes[0])

    def test_several_violations_come_as_one_note_with_a_list(self):
        """Шесть раз подряд «Обратите внимание» читается как шум, а не как предупреждение."""
        notes = self.ns['_szov_break_violation_notes']([
            self._violation('Иванов Иван', '16:30'),
            self._violation('Петров Пётр', '17:05'),
        ])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].count('Обратите внимание'), 1)
        self.assertIn('2 оператора вышли на перерыв не по графику', notes[0])
        self.assertIn('• 16:30 Иванов Иван', notes[0])
        self.assertIn('• 17:05 Петров Пётр', notes[0])

    def test_long_list_is_cut_and_points_to_icore(self):
        limit = self.ns['SZOV_BREAK_NOTE_LIMIT']
        many = [self._violation(f'Оператор {i}', '16:%02d' % i) for i in range(limit + 3)]
        note = self.ns['_szov_break_violation_notes'](many)[0]
        self.assertIn(f'{limit + 3} операторов вышли на перерыв не по графику', note)
        self.assertEqual(note.count('•'), limit)
        self.assertIn('и ещё 3', note)

    def test_detail_explains_every_kind(self):
        self.assertEqual(self.ns['_szov_break_violation_detail'](
            {'kind': 'not_planned'}), 'перерывов в графике на этот день нет')
        self.assertEqual(self.ns['_szov_break_violation_detail'](
            {'kind': 'no_shift'}), 'смены в графике на этот день нет')
        self.assertEqual(self.ns['_szov_break_violation_detail'](
            {'kind': 'off_schedule', 'planned_start_minutes': 1505}), 'по графику в 01:05')


class BreakScanWindowTests(unittest.TestCase):
    """Окна разбора обязаны перекрываться, иначе перерыв на стыке часов теряется."""

    def setUp(self):
        self.ns = _namespace()

    def test_lookback_is_longer_than_the_hourly_step(self):
        self.assertGreater(self.ns['SZOV_BREAK_SCAN_LOOKBACK_HOURS'], 1)

    def test_report_window_covers_more_than_one_scan(self):
        self.assertGreaterEqual(self.ns['SZOV_BREAK_REPORT_MAX_AGE_HOURS'],
                                self.ns['SZOV_BREAK_SCAN_LOOKBACK_HOURS'])


if __name__ == '__main__':
    unittest.main()
