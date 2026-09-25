"""Задача #352: часы супервайзеров по отметкам Clockster.

Правило (supervisor_hours.py) гоняется на выдуманных лентах отметок, а решения,
которые держат его в системе, сторожатся по тексту исходников — как в соседних
тестах раздела: пересчёт по статусам не трогает СВ, смене СВ не ставятся
перерывы, строки СВ идут только в планировщик, СВ видит только свои часы.
"""

import ast
import re
import unittest
from datetime import date
from pathlib import Path

import supervisor_hours as sh

ROOT = Path(__file__).resolve().parents[1]
DATABASE = (ROOT / 'database.py').read_text(encoding='utf-8')
BOT = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
DEPARTMENT_VIEWS = (ROOT / 'src' / 'utils' / 'departmentViews.js').read_text(encoding='utf-8')


def mark(at, kind):
    return {'at': at, 'kind': kind, 'system': 'clockster'}


def hours(raw, break_minutes=60):
    return {
        day: (round(worked / 3600, 2), round(deducted / 3600, 2), round(presence / 3600, 2))
        for day, (worked, deducted, presence) in sh.supervisor_day_hours(raw, break_minutes).items()
    }


class SupervisorClocksterRuleTests(unittest.TestCase):
    def test_night_shift_belongs_to_the_arrival_day(self):
        result = hours([
            mark('2026-09-08T21:00:00+05:00', 'in'),
            mark('2026-09-09T07:30:00+05:00', 'out'),
        ])
        self.assertEqual(result, {date(2026, 9, 8): (9.5, 1.0, 10.5)})

    def test_two_visits_are_summed_and_the_break_is_deducted_once(self):
        result = hours([
            mark('2026-09-10T08:00:00+05:00', 'in'),
            mark('2026-09-10T12:00:00+05:00', 'out'),
            mark('2026-09-10T17:00:00+05:00', 'in'),
            mark('2026-09-10T21:00:00+05:00', 'out'),
        ])
        self.assertEqual(result, {date(2026, 9, 10): (7.0, 1.0, 8.0)})

    def test_hanging_arrival_is_dropped_not_turned_into_a_day_of_work(self):
        result = hours([
            mark('2026-09-15T09:00:00+05:00', 'in'),
            mark('2026-09-16T19:00:00+05:00', 'in'),
            mark('2026-09-17T00:00:00+05:00', 'out'),
        ])
        self.assertEqual(result, {date(2026, 9, 16): (4.0, 1.0, 5.0)})

    def test_repeated_touch_keeps_the_first_arrival(self):
        result = hours([
            mark('2026-09-11T08:00:00+05:00', 'in'),
            mark('2026-09-11T08:02:00+05:00', 'in'),
            mark('2026-09-11T17:00:00+05:00', 'out'),
        ])
        self.assertEqual(result, {date(2026, 9, 11): (8.0, 1.0, 9.0)})

    def test_stray_arrival_after_a_closed_pair_does_not_eat_the_next_shift(self):
        # Ночной СВ: второе касание при уходе терминал угадал приходом.
        result = hours([
            mark('2026-09-20T21:00:00+05:00', 'in'),
            mark('2026-09-21T08:00:00+05:00', 'out'),
            mark('2026-09-21T08:01:00+05:00', 'in'),
            mark('2026-09-21T21:00:00+05:00', 'in'),
            mark('2026-09-22T08:00:00+05:00', 'out'),
        ])
        self.assertEqual(result, {
            date(2026, 9, 20): (10.0, 1.0, 11.0),
            date(2026, 9, 21): (10.0, 1.0, 11.0),
        })

    def test_departure_guessed_as_arrival_does_not_eat_the_next_day(self):
        result = hours([
            mark('2026-09-22T09:00:00+05:00', 'in'),
            mark('2026-09-22T13:00:00+05:00', 'out'),
            mark('2026-09-22T18:00:00+05:00', 'in'),   # уход, угаданный приходом
            mark('2026-09-23T09:00:00+05:00', 'in'),
            mark('2026-09-23T18:00:00+05:00', 'out'),
        ])
        self.assertEqual(result, {
            date(2026, 9, 22): (3.0, 1.0, 4.0),
            date(2026, 9, 23): (8.0, 1.0, 9.0),
        })

    def test_unpaired_marks_give_no_hours(self):
        self.assertEqual(hours([mark('2026-09-12T08:00:00+05:00', 'in')]), {})
        self.assertEqual(hours([mark('2026-09-12T18:00:00+05:00', 'out')]), {})

    def test_session_longer_than_the_limit_is_rejected(self):
        self.assertEqual(hours([
            mark('2026-09-12T06:00:00+05:00', 'in'),
            mark('2026-09-12T23:30:00+05:00', 'out'),
        ]), {})

    def test_break_never_takes_the_day_below_zero(self):
        result = hours([
            mark('2026-09-13T10:00:00+05:00', 'in'),
            mark('2026-09-13T10:40:00+05:00', 'out'),
        ])
        self.assertEqual(result, {date(2026, 9, 13): (0.0, 0.67, 0.67)})

    def test_the_same_mark_from_two_cached_days_is_counted_once(self):
        raw = [
            mark('2026-09-14T09:00:00+05:00', 'in'),
            mark('2026-09-14T18:00:00+05:00', 'out'),
        ]
        self.assertEqual(hours(raw + raw), {date(2026, 9, 14): (8.0, 1.0, 9.0)})

    def test_utc_marks_are_read_in_almaty_time(self):
        result = hours([
            mark('2026-09-14T19:30:00Z', 'in'),   # 00:30 15.09 по Алматы
            mark('2026-09-15T03:30:00Z', 'out'),
        ])
        self.assertEqual(list(result), [date(2026, 9, 15)])

    def test_custom_break_minutes(self):
        result = hours([
            mark('2026-09-14T09:00:00+05:00', 'in'),
            mark('2026-09-14T18:00:00+05:00', 'out'),
        ], break_minutes=30)
        self.assertEqual(result, {date(2026, 9, 14): (8.5, 0.5, 9.0)})

    def test_break_minutes_validation(self):
        self.assertEqual(sh.normalize_break_minutes(None), 60)
        self.assertEqual(sh.normalize_break_minutes(''), 60)
        self.assertEqual(sh.normalize_break_minutes('30'), 30)
        self.assertEqual(sh.normalize_break_minutes(0), 0)
        for bad in (-1, 241, 'час', '1.5'):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                sh.normalize_break_minutes(bad)


class SupervisorDayExplanationTests(unittest.TestCase):
    """Окно дня РОП: какие отметки вошли в пары и почему остальные не засчитаны."""

    def test_day_shows_reasons_and_the_night_departure(self):
        raw = [
            mark('2026-09-15T08:00:00+05:00', 'in'),
            mark('2026-09-15T08:05:00+05:00', 'in'),          # повторное касание
            mark('2026-09-15T13:00:00+05:00', 'out'),
            mark('2026-09-15T21:00:00+05:00', 'in'),
            mark('2026-09-16T07:00:00+05:00', 'out'),          # закрывает смену 15-го
            mark('2026-09-16T12:00:00+05:00', 'out'),          # без прихода
        ]
        day = sh.explain_day(raw, date(2026, 9, 15), 60)
        rows = [(r['at'][11:16], r['kind'], r['status'], r['reason'], r['next_day']) for r in day['marks']]
        self.assertEqual(rows, [
            ('08:00', 'in', 'used', None, False),
            ('08:05', 'in', 'ignored', 'repeat_touch', False),
            ('13:00', 'out', 'used', None, False),
            ('21:00', 'in', 'used', None, False),
            ('07:00', 'out', 'used', None, True),
        ])
        self.assertEqual(day['presence_seconds'], 15 * 3600)
        self.assertEqual(day['worked_seconds'], 14 * 3600)
        next_day = sh.explain_day(raw, date(2026, 9, 16), 60)
        statuses = [(r['at'][11:16], r['status'], r['reason']) for r in next_day['marks']]
        self.assertEqual(statuses, [('07:00', 'previous_day', None), ('12:00', 'ignored', 'unpaired_out')])
        self.assertEqual(next_day['worked_seconds'], 0)

    def test_manual_departure_closes_a_forgotten_shift(self):
        raw = [mark('2026-09-18T09:04:00+05:00', 'in')]
        self.assertEqual(sh.explain_day(raw, date(2026, 9, 18), 60)['marks'][0]['reason'], 'unpaired_in')
        raw.append({'at': '2026-09-18T18:00:00+05:00', 'kind': 'out', 'source': 'manual', 'id': 7})
        day = sh.explain_day(raw, date(2026, 9, 18), 60)
        self.assertEqual([(r['source'], r['id'], r['status']) for r in day['marks']],
                         [('clockster', None, 'used'), ('manual', 7, 'used')])
        self.assertEqual(round(day['worked_seconds'] / 3600, 2), 7.93)

    def test_corrected_clockster_mark_is_shown_but_not_paired(self):
        raw = [
            mark('2026-09-20T09:00:00+05:00', 'in'),
            mark('2026-09-20T18:00:00+05:00', 'in'),       # уход, угаданный приходом
            {'at': '2026-09-20T18:00:00+05:00', 'kind': 'out', 'source': 'manual', 'id': 9},
        ]
        replaced = {sh.mark_key('2026-09-20T18:00:00+05:00', 'in')}
        day = sh.explain_day(raw, date(2026, 9, 20), 60, replaced)
        self.assertEqual([(r['at'][11:16], r['kind'], r['source'], r['status']) for r in day['marks']], [
            ('09:00', 'in', 'clockster', 'used'),
            ('18:00', 'out', 'manual', 'used'),
            ('18:00', 'in', 'clockster', 'replaced'),
        ])
        self.assertEqual(day['worked_seconds'], 8 * 3600)
        # Пересчёт видит ту же ленту: исходная отметка из неё убрана.
        kept = sh.drop_replaced(raw, replaced)
        self.assertEqual(round(sum(v[0] for v in sh.supervisor_day_hours(kept, 60).values()) / 3600, 2), 8.0)

    def test_manual_mark_is_not_merged_with_the_same_terminal_mark(self):
        raw = [
            mark('2026-09-19T09:00:00+05:00', 'in'),
            {'at': '2026-09-19T09:00:00+05:00', 'kind': 'in', 'source': 'manual', 'id': 3},
            mark('2026-09-19T18:00:00+05:00', 'out'),
        ]
        day = sh.explain_day(raw, date(2026, 9, 19), 60)
        self.assertEqual(len(day['marks']), 3)
        self.assertEqual(day['worked_seconds'], 8 * 3600)


def method_source(name):
    tree = ast.parse(DATABASE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'Database':
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(DATABASE, item)
    raise AssertionError(f'Database.{name} не найден')


def function_source(source, name):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f'{name} не найдена')


class SupervisorClocksterWiringTests(unittest.TestCase):
    def test_status_recalculation_skips_clockster_supervisors_before_reading_shifts(self):
        body = method_source('_recalculate_auto_daily_hours_tx')
        skip = body.index('self._clockster_hours_supervisor_ids_tx(cursor, op_ids)')
        self.assertLess(skip, body.index('FROM work_shifts'))

    def test_supervisor_shift_keeps_only_manual_breaks(self):
        body = method_source('_save_shift_tx')
        adjust = body.index('self._adjust_shift_breaks_against_occupied_tx(')
        guard = body.index('self._clockster_hours_supervisor_ids_tx(cursor, [operator_id])')
        self.assertLess(adjust, guard)
        tail = body[guard:guard + 900]
        # Дни, когда человек ещё был оператором, остаются операторскими.
        self.assertIn('not self._operator_membership_days_tx(', tail)
        # Присланные РОП перерывы — как есть, в пределах смены, без автоматики.
        self.assertIn('self._normalize_shift_breaks(breaks)', tail)
        self.assertIn("int(item['start']) >= shift_start_min and int(item['end']) <= shift_end_min", tail)

    def test_auto_supervisor_breaks_are_dropped_once(self):
        start = DATABASE.index("('task352_drop_auto_supervisor_breaks',)")
        block = DATABASE[start:start + 400]
        self.assertIn('if cursor.fetchone():', block)
        self.assertIn('DELETE FROM shift_breaks sb', block)
        self.assertIn('ON CONFLICT (job_key) DO NOTHING RETURNING job_key', DATABASE[start - 300:start])

    def test_schedule_breaks_override_the_default_break(self):
        body = method_source('recalculate_supervisor_clockster_hours')
        override = body.index('if key in schedule_breaks:')
        self.assertLess(override, body.index('elif keep_before and day < keep_before'))
        day_view = method_source('get_supervisor_clockster_day')
        self.assertIn("'break_source': 'schedule' if schedule_break is not None else 'settings'", day_view)

    def test_shift_changes_recalculate_supervisor_hours_after_commit(self):
        for name in ('save_shift', 'delete_shift'):
            body = method_source(name)
            with self.subTest(method=name):
                call = body.index('self._recalculate_supervisor_shift_day(')
                # После выхода из with: пересчёт читает базу своим соединением.
                self.assertGreater(call, body.rindex('with self._get_cursor() as cursor:'))
                self.assertRegex(body[call - 120:call], r'\n        (if deleted:\n            )?(#[^\n]*\n        )*$')

    def test_status_recalculation_keeps_days_when_the_supervisor_was_an_operator(self):
        body = method_source('_recalculate_auto_daily_hours_tx')
        self.assertIn('self._operator_membership_days_tx(', body)
        self.assertIn('key in supervisor_operator_days', body)

    def test_recalculation_touches_only_supervisor_days_with_known_marks(self):
        body = method_source('recalculate_supervisor_clockster_hours')
        self.assertIn('self._supervisor_card_marks_multi_tx(', body)
        # Уход ночной смены лежит в следующем дне: он без отметок — день не трогаем.
        self.assertIn('(day + timedelta(days=1)) not in unknown_days', body)
        self.assertIn('if (user_id, day) in operator_days:', body)
        self.assertIn('keep_before and day < keep_before', body)

    def test_synced_days_take_marks_from_the_store_others_from_the_cache(self):
        loader = method_source('_supervisor_card_marks_multi_tx')
        self.assertIn('FROM supervisor_clockster_sync_days', loader)
        self.assertIn('FROM supervisor_clockster_marks', loader)
        self.assertIn("'%clockster%'", loader)
        self.assertIn('cache_days = sorted(cache_ok - synced)', loader)
        self.assertIn('unknown_days = cache_bad - synced', loader)
        store = method_source('store_supervisor_clockster_marks')
        # Замена за период: убранная в Clockster отметка не должна остаться.
        self.assertLess(store.index('DELETE FROM supervisor_clockster_marks WHERE mark_at >= %s AND mark_at < %s'),
                        store.index('INSERT INTO supervisor_clockster_marks'))
        self.assertIn('INSERT INTO supervisor_clockster_sync_days', store)
        # Окно дня и проверка исправлений читают тот же источник.
        self.assertIn('self._supervisor_card_marks_multi_tx(', method_source('_supervisor_card_marks_tx'))
        self.assertIn("'day_built': day in known_days,", method_source('get_supervisor_clockster_day'))

    def test_startup_cleanup_spares_operator_days(self):
        start = DATABASE.index('DELETE FROM shift_breaks sb')
        block = DATABASE[start:start + 900]
        self.assertIn('NOT EXISTS', block)
        self.assertIn('group_operator_memberships', block)

    def test_manual_marks_join_the_recalculation_even_without_a_card(self):
        body = method_source('recalculate_supervisor_clockster_hours')
        self.assertIn('manual_marks, replaced_marks = self._supervisor_manual_marks_tx(', body)
        self.assertIn('supervisor_hours.drop_replaced(', body)
        self.assertIn('+ manual_marks.get(user_id, [])', body)
        # Отметки читаются с дня накануне: повторное касание после полуночи на
        # краю окна не должно стать новым приходом.
        self.assertIn('start - timedelta(days=1), end + timedelta(days=1)', body)

    def test_manual_mark_validation_before_insert(self):
        validate = method_source('_validate_supervisor_mark_input')
        for guard in ("'Отметка не может быть в будущем'", "'Слишком старая дата: такой месяц уже закрыт'"):
            with self.subTest(guard=guard):
                self.assertIn(guard, validate)
        body = method_source('add_supervisor_manual_mark')
        insert = body.index('INSERT INTO supervisor_manual_marks')
        for guard in ('self._validate_supervisor_mark_input(', "'Такая отметка уже есть'", 'raise PermissionError(',
                      "'Эта отметка уже исправлена — измените исправление'",
                      "'Исходной отметки нет среди отметок Clockster этого сотрудника'"):
            with self.subTest(guard=guard):
                self.assertLess(body.index(guard), insert)
        self.assertIn('self._recalculate_supervisor_days(', body[insert:])

    def test_mark_recalculation_keeps_the_closed_month_break(self):
        body = method_source('_recalculate_supervisor_days')
        # Уход после полуночи закрывает смену накануне; закрытый месяц — прежний вычет.
        self.assertIn('min(days) - timedelta(days=1), max(days), [user_id]', body)
        self.assertIn('keep_break_before=today.replace(day=1)', body)

    def test_editing_a_manual_mark_keeps_history(self):
        body = method_source('update_supervisor_manual_mark')
        self.assertIn('SET deleted_at = NOW(), deleted_by = %s', body)
        self.assertIn('replaces_mark_id', body)
        self.assertNotIn('UPDATE supervisor_manual_marks SET mark_at', body)

    def test_mark_changes_are_authorized_by_the_mark_owner(self):
        route = function_source(BOT, 'supervisor_manual_mark_item')
        owner = route.index('owner_id = db.supervisor_manual_mark_user(mark_id)')
        self.assertLess(owner, route.index('_supervisor_marks_access(requester_id'))
        self.assertLess(route.index("if access != 'edit':"), route.index('db.update_supervisor_manual_mark('))

    def test_view_date_is_checked_before_writing(self):
        for name, write in (('add_supervisor_manual_mark', 'db.add_supervisor_manual_mark('),
                            ('supervisor_manual_mark_item', 'db.update_supervisor_manual_mark(')):
            route = function_source(BOT, name)
            with self.subTest(route=name):
                self.assertLess(route.index('_supervisor_view_date('), route.index(write))

    def test_manual_marks_are_soft_deleted(self):
        body = method_source('delete_supervisor_manual_mark')
        self.assertIn('SET deleted_at = NOW(), deleted_by = %s', body)
        self.assertNotIn('DELETE FROM supervisor_manual_marks', body)

    def test_only_head_or_global_admin_edits_marks_supervisor_only_views(self):
        access = function_source(BOT, '_supervisor_marks_access')
        self.assertIn("return 'view'", access)
        self.assertIn('int(target_user_id) == int(requester_id)', access)
        add = function_source(BOT, 'add_supervisor_manual_mark')
        self.assertIn("if access != 'edit':", add)

    def test_card_of_another_person_cannot_be_taken(self):
        body = method_source('save_supervisor_hours_settings')
        check = body.index("raise ValueError('Эта карточка Clockster уже привязана к другому сотруднику')")
        self.assertLess(check, body.index('INSERT INTO glb_employee_links'))

    def test_supervisor_row_counts_only_clockster_hours(self):
        body = method_source('get_daily_hours_for_all_month')
        start = body.index('if supervisors_mode:\n                    # Часы СВ')
        self.assertIn('accounted_hours = float(regular_hours or 0.0)', body[start:start + 300])

    def test_settings_are_validated_before_the_first_write(self):
        body = method_source('save_supervisor_hours_settings')
        self.assertLess(body.index('raise PermissionError'), body.index('INSERT INTO supervisor_hours_settings'))
        self.assertLess(body.index("raise ValueError('Карточка Clockster не найдена')"),
                        body.index('INSERT INTO glb_employee_links'))

    def test_only_planner_rows_ask_for_supervisors(self):
        # Импорт/выгрузка Excel, симуляция и «смены коллег» сопоставляли бы СВ по ФИО.
        self.assertEqual(BOT.count('include_supervisors='), 1)
        route = function_source(BOT, 'get_operators_with_schedules')
        self.assertIn("include_supervisors=_normalize_user_role(user_data[3]) != 'trainer'", route)

    def test_supervisor_sees_only_own_hours_row(self):
        route = function_source(BOT, 'sv_daily_hours')
        start = route.index("== 'supervisors':")
        branch = route[start:route.index('if headed_dept_id is not None and not is_global_admin:', start)]
        self.assertIn('user_scope = [requester_id]', branch)
        self.assertNotIn("request.args.get('id')", branch)

    def test_supervisor_cannot_set_own_break(self):
        scope = function_source(BOT, '_supervisor_hours_settings_scope')
        self.assertNotIn('_is_supervisor_role', scope)
        self.assertIn('_headed_department_ids(requester_id)', scope)

    def test_nightly_job_syncs_clockster_then_recalculates(self):
        job = function_source(BOT, 'group_late_nightly_job')
        recalc = job.index('db.recalculate_supervisor_clockster_hours(')
        self.assertLess(job.index('_attendance_cache.backfill(db, days=7)'), recalc)
        self.assertLess(job.index('_sync_supervisor_clockster_marks(start - timedelta(days=1), today)'), recalc)
        self.assertIn('keep_break_before=today.replace(day=1)', job[recalc:recalc + 200])

    def test_general_excel_report_keeps_supervisors(self):
        # СВ ушли из операторского списка — в общий файл они идут своими строками.
        start = BOT.index("# СВ отделов с часами по Clockster (задача #352) в операторский список")
        self.assertIn("people='supervisors'", BOT[start:start + 1500])

    def test_frontend_mirrors_the_department_list(self):
        python_codes = set(sh.SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES)
        match = re.search(r"SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENTS = new Set\(\[([^\]]*)\]\)", DEPARTMENT_VIEWS)
        self.assertIsNotNone(match)
        js_codes = set(re.findall(r"'([^']+)'", match.group(1)))
        self.assertEqual(js_codes, python_codes)


class _CardsHarness:
    """Настоящие _supervisor_clockster_cards_tx и сшивка ФИО из database.py без базы."""

    def __init__(self, links, cards, supervisors):
        self._links = links
        self._cards = cards
        self._supervisors = supervisors   # все СВ с часами по Clockster: [(id, ФИО)]

    def _clockster_hours_supervisor_ids_tx(self, cursor, user_ids=None):
        return {uid for uid, _ in self._supervisors}

    class _Cursor:
        def __init__(self, harness):
            self.harness = harness
            self.rows = []

        def execute(self, sql, params=None):
            if 'glb_employee_links' in sql:
                self.rows = list(self.harness._links)
            elif 'glb_employees' in sql:
                self.rows = list(self.harness._cards)
            elif 'FROM users' in sql:
                wanted = set(params[0])
                self.rows = [row for row in self.harness._supervisors if row[0] in wanted]
            else:
                raise AssertionError(sql)

        def fetchall(self):
            return self.rows


def _load_cards_method():
    tree = ast.parse(DATABASE)
    namespace = {'re': re}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'Database':
            for item in node.body:
                if isinstance(item, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == '_GLB_NAME_FOLD' for t in item.targets):
                    setattr(_CardsHarness, '_GLB_NAME_FOLD', eval(compile(ast.Expression(item.value), 'db', 'eval')))
                if isinstance(item, ast.FunctionDef) and item.name in (
                        '_supervisor_clockster_cards_tx', '_glb_name_keys', '_glb_index_put'):
                    item.decorator_list = []
                    module = ast.Module(body=[item], type_ignores=[])
                    exec(compile(module, 'database.py', 'exec'), namespace)
    _CardsHarness._glb_name_keys = classmethod(namespace['_glb_name_keys'])
    _CardsHarness._glb_index_put = staticmethod(namespace['_glb_index_put'])
    _CardsHarness.match = namespace['_supervisor_clockster_cards_tx']


_load_cards_method()


def match_cards(people, cards, links=(), settings=None, all_supervisors=None):
    harness = _CardsHarness(list(links), list(cards), list(all_supervisors or people))
    cursor = _CardsHarness._Cursor(harness)
    result = harness.match(cursor, people, settings or {})
    return {uid: (card or {}).get('ext_id') for uid, card in result.items()}


class SupervisorClocksterCardMatchingTests(unittest.TestCase):
    def test_short_name_in_clockster_matches_full_name_here(self):
        self.assertEqual(
            match_cards([(1, 'Иванов Пётр Сергеевич')], [('clockster:10', 'Иванов Пётр')]),
            {1: 'clockster:10'},
        )

    def test_different_patronymics_are_different_people(self):
        self.assertEqual(
            match_cards([(1, 'Ким Александр Сергеевич')], [('clockster:10', 'Ким Александр Викторович')]),
            {1: None},
        )

    def test_namesakes_in_clockster_are_not_matched(self):
        self.assertEqual(
            match_cards([(1, 'Ким Александр')],
                        [('clockster:10', 'Ким Александр'), ('clockster:11', 'Ким Александр')]),
            {1: None},
        )

    def test_namesakes_among_supervisors_are_not_matched(self):
        self.assertEqual(
            match_cards([(1, 'Ким Александр Сергеевич'), (2, 'Ким Александр Олегович')],
                        [('clockster:10', 'Ким Александр')]),
            {1: None, 2: None},
        )

    def test_manual_link_wins_and_no_card_wins_over_everything(self):
        cards = [('clockster:10', 'Иванов Пётр'), ('clockster:20', 'Другой Человек')]
        self.assertEqual(
            match_cards([(1, 'Иванов Пётр')], cards, links=[('clockster:20', 1)]),
            {1: 'clockster:20'},
        )
        self.assertEqual(
            match_cards([(1, 'Иванов Пётр')], cards, settings={1: {'no_card': True}}),
            {1: None},
        )

    def test_namesake_outside_the_passed_people_still_blocks_the_match(self):
        # Окно дня и пересчёт после ручной отметки зовут сшивку с одним человеком.
        self.assertEqual(
            match_cards([(1, 'Ким Александр Сергеевич')], [('clockster:10', 'Ким Александр')],
                        all_supervisors=[(1, 'Ким Александр Сергеевич'), (2, 'Ким Александр Олегович')]),
            {1: None},
        )

    def test_card_linked_to_someone_else_is_not_auto_matched(self):
        self.assertEqual(
            match_cards([(1, 'Иванов Пётр')], [('clockster:10', 'Иванов Пётр')], links=[('clockster:10', 99)]),
            {1: None},
        )


class SupervisorClocksterFrontendTests(unittest.TestCase):
    def test_hours_cells_of_supervisors_are_not_editable(self):
        for anchor in ('function handleHoursCellClick(', 'function openCellDetail(', 'function startHourSelectionDrag('):
            start = APP.index(anchor)
            with self.subTest(anchor=anchor):
                self.assertIn('if (isClocksterHoursRow(', APP[start:start + 700])
        # Вместо окна правки у СВ открывается окно отметок дня.
        for anchor in ('function handleHoursCellClick(', 'function openCellDetail('):
            start = APP.index(anchor)
            with self.subTest(anchor=anchor):
                self.assertIn('setSupervisorDayMarks({ operator, dateStr: dayToDateStr(day) });', APP[start:start + 900])

    def test_clockster_sync_is_for_the_head_and_above_and_stores_marks(self):
        route = function_source(BOT, 'sync_supervisor_hours_from_clockster')
        self.assertIn('_supervisor_hours_settings_scope(requester_id', route)
        self.assertIn('SUPERVISOR_CLOCKSTER_SYNC_MAX_DAYS', route)
        # Отметки сохраняются (окно дня их покажет), день накануне и следующий — ради ночных смен.
        self.assertIn('_sync_supervisor_clockster_marks(date_from - timedelta(days=1), min(date_to + timedelta(days=1), today))', route)
        self.assertLess(route.index('_sync_supervisor_clockster_marks('), route.index('db.recalculate_supervisor_clockster_hours('))
        helper = function_source(BOT, '_sync_supervisor_clockster_marks')
        self.assertIn('db.store_supervisor_clockster_marks(by_card, date_from, date_to)', helper)
        # Кнопка — только РОП и те, кто выше, и только на вкладке СВ.
        self.assertIn("group.label === 'Работа' && canSyncSupervisorHours && showSupervisorHoursTabs && hoursPeopleKind !== 'operators'", APP)

    def test_supervisor_rows_do_not_need_a_group_choice(self):
        self.assertIn("&& !(showSupervisorHoursTabs && hoursPeopleKind !== 'operators');", APP)
        self.assertIn(') : hoursNeedGroupChoice ? (', APP)

    def test_segment_is_hidden_for_groups_of_other_departments(self):
        route = function_source(BOT, 'list_groups_endpoint')
        self.assertIn('"supervisor_hours_department_ids": db.clockster_hours_department_ids()', route)
        start = APP.index('const showSupervisorHoursTabs = selectedGroupHasSupervisorHours && (')
        self.assertLess(APP.index('const selectedGroupHasSupervisorHours = !selectedHoursGroup'), start)
        self.assertIn("if (!showSupervisorHoursTabs && hoursPeopleKind !== 'operators') setHoursPeopleKind('operators');", APP)

    def test_day_marks_window_is_rendered_on_desktop_and_phone(self):
        self.assertEqual(APP.count('<SupervisorDayMarksModal'), 2)

    def test_supervisor_breaks_are_manual_in_the_shift_editor(self):
        start = APP.index('if (isPlannerSupervisorRow(op)) {\n                    const manualBreaks')
        block = APP[start:start + 900]
        self.assertIn('suggestedBreaks: []', block)
        self.assertIn('manualOnly: true', block)
        self.assertIn('modalState.svBreaksEdited === plannerSvBreaksKey', block)
        self.assertEqual(APP.count('breaks: modalBreaksPayload()'), 2)
        self.assertEqual(APP.count('onClick={addPlannerSupervisorBreak}'), 2)
        self.assertEqual(APP.count('onClick={() => removePlannerSupervisorBreak(i)}'), 2)

    def test_planner_break_simulation_leaves_supervisors_out(self):
        start = APP.index('const cloneOperatorsForBreakSimulation = (sourceOperators = []) => {')
        self.assertIn('.filter(op => !isPlannerSupervisorRow(op))', APP[start:start + 200])

    def test_plain_supervisor_cannot_open_supervisor_rows_for_editing(self):
        for anchor in ('const openEditModal = (opId, date, editIndex = null) => {',
                       'const toggleDayOff = async (opId, date) => {'):
            start = APP.index(anchor)
            with self.subTest(anchor=anchor):
                self.assertIn('if (isPlannerRowLocked(opId)) return;', APP[start:start + 200])


if __name__ == '__main__':
    unittest.main()
