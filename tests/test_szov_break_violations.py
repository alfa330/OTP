"""Выходы на перерыв мимо графика (задача #114): склейка эпизодов, сверка с графиком, текст.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном namespace —
так проверяется настоящая логика. Импортировать модуль нельзя: на старте он поднимает пул
к боевой БД (тот же приём в test_szov_wallboard.py и test_chat_hourly_report.py).
"""
import ast
import logging
import os
import re
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
    'SZOV_BREAK_DIRECTION_LINE', 'SZOV_BREAK_DIRECTION_CHAT',
    '_szov_chat_break_episodes', '_szov_chat_wallboard_resolve',
    '_status_import_normalize_operator_name', '_status_import_operator_name_variants',
    '_status_import_dedupe_operator_infos', '_status_import_resolve_operator_matches',
    '_KZ_TO_RU_FOLD',
    '_oktell_break_episodes_sql', '_szov_break_parse_time', '_szov_break_merge_episodes',
    '_szov_break_planned_for_day', '_szov_break_classify', '_szov_break_on_shift',
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
    ns = {'os': os, 're': re, 'logging': logging, 'datetime': datetime, 'timedelta': timedelta}
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
        # Смена интервалом, а не датой: у ночной смены дата и «на смене ли человек» расходятся.
        self.shifts = {(1, '2026-08-19'): [(9 * 60, 21 * 60)]}

    def _classify(self, episodes, plan=None, shifts=None):
        return self.ns['_szov_break_classify'](
            episodes,
            self.plan if plan is None else plan,
            self.shifts if shifts is None else shifts,
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
                               plan={}, shifts={(1, '2026-08-19'): [(9 * 60, 21 * 60)]})
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_NOT_PLANNED'])

    def test_day_without_a_shift_is_reported_separately(self):
        """«Смены нет» и «перерывов нет» — разные истории, и в списке они читаются по-разному."""
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 16:30:00', '2026-08-19 16:45:00')],
                               plan={}, shifts={})
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_NO_SHIFT'])

    def test_status_flicker_is_not_judged_at_all(self):
        """Из 787 эпизодов за пять дней 333 длились меньше минуты — это не перерывы."""
        found = self._classify([_episode(1, 'Иванов', '2026-08-19 16:30:00', '2026-08-19 16:31:00')])
        self.assertEqual(found, [])

    def test_open_break_is_judged_by_the_time_already_spent(self):
        """Ещё не закончившийся перерыв судим по отсиженному, а конец дописываем позже."""
        now = datetime(2026, 8, 19, 16, 40)
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 16:30:00', None)], self.plan, self.shifts, now)
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0]['ended_at'])
        self.assertIsNone(found[0]['duration_minutes'])

    def test_open_break_shorter_than_the_floor_waits_for_the_next_scan(self):
        now = datetime(2026, 8, 19, 16, 31)
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 16:30:00', None)], self.plan, self.shifts, now)
        self.assertEqual(found, [])

    def test_night_shift_tail_is_matched_against_yesterdays_plan(self):
        """Ночная смена держит перерывы на дате смены, уводя минуты за 1440."""
        plan = {(1, '2026-08-18'): [(25 * 60, 25 * 60 + 15)]}   # 01:00 следующих суток
        shifts = {(1, '2026-08-18'): [(15 * 60, 26 * 60)]}      # 15:00–02:00
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 01:03:00', '2026-08-19 01:15:00')],
            plan, shifts, NOW)
        self.assertEqual(found, [])

    def test_night_tail_does_not_make_the_next_day_a_shift(self):
        """Боевой случай: смена 17.08 15:00–02:00 с перерывом в 00:00 18-го. Перерыв,
        взятый 18-го в 13:36, к этой смене отношения не имеет — «смены на день нет»,
        а не «по графику в 00:00»."""
        plan = {(1, '2026-08-18'): [(24 * 60, 24 * 60 + 30)]}
        shifts = {(1, '2026-08-18'): [(15 * 60, 26 * 60)]}
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 13:36:00', '2026-08-19 14:14:00')],
            plan, shifts, NOW)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_NO_SHIFT'])
        self.assertIsNone(found[0]['planned_start_minutes'])

    def test_break_outside_a_night_shift_plan_is_still_off_schedule(self):
        """А внутри ночной смены сдвиг остаётся сдвигом: человек на смене, время не то."""
        plan = {(1, '2026-08-18'): [(25 * 60, 25 * 60 + 15)]}
        shifts = {(1, '2026-08-18'): [(15 * 60, 30 * 60)]}     # 15:00–06:00
        found = self.ns['_szov_break_classify'](
            [_episode(1, 'Иванов', '2026-08-19 03:00:00', '2026-08-19 03:20:00')],
            plan, shifts, NOW)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_OFF_SCHEDULE'])

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


class ChatBreakEpisodeTests(unittest.TestCase):
    """Факт по чату приезжает лентой статусов Chat2Desk, а не историей Oktell."""

    def setUp(self):
        self.ns = _namespace()
        self.lookup = {'иванов иван': [{'id': 11, 'name': 'Иванов Иван'}]}

    def _episodes(self, entries, day='2026-08-19'):
        return self.ns['_szov_chat_break_episodes'](
            {'Иванов Иван': entries}, self.lookup, day)

    def test_break_run_becomes_an_episode_ending_at_the_next_status(self):
        got = self._episodes([
            (9 * 3600, 'online', 'Онлайн', ''),
            (13 * 3600, 'break', 'Перерыв', ''),
            (13 * 3600 + 900, 'online', 'Онлайн', ''),
        ])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['operator_id'], 11)
        self.assertEqual(got[0]['started_at'], datetime(2026, 8, 19, 13, 0))
        self.assertEqual(got[0]['ended_at'], datetime(2026, 8, 19, 13, 15))

    def test_break_still_running_has_no_end(self):
        """Человек на перерыве прямо сейчас: длительность уточнит следующий заход."""
        got = self._episodes([
            (9 * 3600, 'online', 'Онлайн', ''),
            (13 * 3600, 'break', 'Перерыв', ''),
        ])
        self.assertEqual(len(got), 1)
        self.assertIsNone(got[0]['ended_at'])

    def test_only_the_break_status_counts(self):
        """Решение владельца: тренинг, «занят», тех.перерыв и отпуск к графику не относятся."""
        got = self._episodes([
            (9 * 3600, 'online', 'Онлайн', ''),
            (10 * 3600, 'training', 'Тренинг', ''),
            (11 * 3600, 'busy', 'Занят', ''),
            (12 * 3600, 'tech', 'Тех. перерыв', ''),
            (14 * 3600, 'holiday', 'Отпуск', ''),
            (15 * 3600, 'online', 'Онлайн', ''),
        ])
        self.assertEqual(got, [])

    def test_unknown_chat_account_is_skipped(self):
        """Имя учётки Chat2Desk разошлось с ФИО — человека в счёт не берём, а не гадаем."""
        got = self.ns['_szov_chat_break_episodes'](
            {'Кто-то Чужой': [(13 * 3600, 'break', 'Перерыв', '')]}, self.lookup, '2026-08-19')
        self.assertEqual(got, [])

    def test_episodes_feed_the_same_rule_as_the_line(self):
        """Правило одно на два направления: иначе за одно нарушение наказывали бы по-разному."""
        episodes = self._episodes([
            (9 * 3600, 'online', 'Онлайн', ''),
            (16 * 3600, 'break', 'Перерыв', ''),
            (16 * 3600 + 1200, 'online', 'Онлайн', ''),
        ])
        merged = self.ns['_szov_break_merge_episodes'](episodes)
        found = self.ns['_szov_break_classify'](
            merged, {(11, '2026-08-19'): [(14 * 60, 14 * 60 + 15)]},
            {(11, '2026-08-19'): [(9 * 60, 21 * 60)]},
            datetime(2026, 8, 19, 23, 0), direction=self.ns['SZOV_BREAK_DIRECTION_CHAT'])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['kind'], self.ns['SZOV_BREAK_KIND_OFF_SCHEDULE'])
        self.assertEqual(found[0]['direction'], 'chat')

    def test_direction_is_stamped_on_every_violation(self):
        """Отбивка «Линии» не должна забирать нарушения чатников и наоборот."""
        line = self.ns['_szov_break_classify'](
            [_episode(1, 'Оператор', '2026-08-19 16:30:00', '2026-08-19 16:45:00')],
            {}, {(1, '2026-08-19'): [(9 * 60, 21 * 60)]}, NOW)
        self.assertEqual(line[0]['direction'], self.ns['SZOV_BREAK_DIRECTION_LINE'])


class ChatBreakStatusVocabularyTests(unittest.TestCase):
    """Ключ статуса берётся из общего каталога табло — второго словаря быть не должно."""

    def test_break_key_matches_the_wallboard_catalog(self):
        source = SOURCE
        block = source[source.index('_SZOV_CHAT_WALLBOARD_STATUSES = {'):]
        block = block[:block.index('}')]
        self.assertIn("'break': ('break'", block)
        # Разбор перерывов чата опирается ровно на этот ключ.
        episodes_src = source[source.index('def _szov_chat_break_episodes('):]
        episodes_src = episodes_src[:episodes_src.index('\ndef ')]
        self.assertIn("entry[1] != 'break'", episodes_src)


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
