# -*- coding: utf-8 -*-
"""Задача #330: живой таймлайн статусов у Тез КЦ.

Отрезок статуса в базе строится МЕЖДУ двумя переключениями, поэтому идущего
прямо сейчас статуса там нет вовсе — его достраивает сервер. Здесь закреплены
рамки этой достройки (правила стоят денег: по соответствию считают дисциплину)
и то, что автообновление не дёргает тяжёлые ручки раздела.

Методы database.py гоняются по-настоящему: узел достаётся из исходника и
исполняется в своём окружении — импортировать модуль нельзя, он на импорте
поднимает пул к боевой базе.
"""

import ast
import copy
import re
import unittest
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / 'database.py'
BOT_PATH = ROOT / 'bot_schedule2.py'
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
PHONE = (ROOT / 'src' / 'components' / 'schedule' / 'MyShiftsMobile.jsx').read_text(encoding='utf-8')
DATABASE_SRC = source_cache.read(DATABASE_PATH)
BOT_SRC = source_cache.read(BOT_PATH)


def load_db_method(name):
    node = copy.deepcopy(source_cache.function_node(DATABASE_PATH, name, class_name='Database'))
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {'datetime': datetime, 'timedelta': timedelta, 'dt_time': dt_time, 'date': date}
    exec(compile(module, str(DATABASE_PATH), 'exec'), namespace)
    return namespace[name]


class FakeCursor:
    """Курсор на одну выборку: метод делает ровно один execute + fetchall."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        return self.rows


class Stub:
    LIVE_STATUS_TAIL_EARLY_LOGIN_MARGIN = timedelta(hours=1)


TODAY = date(2026, 9, 21)
YESTERDAY = TODAY - timedelta(days=1)


def window(rows, now):
    method = load_db_method('_live_status_tail_window_tx')
    return method(Stub(), FakeCursor(rows), [7], now)


class LiveStatusTailWindowTests(unittest.TestCase):
    """Докуда и с какого момента честно тянуть текущий статус."""

    def test_inside_the_shift_the_tail_reaches_now(self):
        rows = [(7, TODAY, dt_time(9, 0), dt_time(19, 0))]
        now = datetime.combine(TODAY, dt_time(11, 30))
        self.assertEqual(
            window(rows, now),
            {7: (datetime.combine(TODAY, dt_time(8, 0)), now)}
        )

    def test_after_the_shift_the_tail_stops_at_its_end(self):
        """Иначе «Готов» растёт у того, чей телефон не прислал «выключен» при
        уходе домой: у Тез КЦ такие сутки не редкость."""
        rows = [(7, TODAY, dt_time(9, 0), dt_time(19, 0))]
        now = datetime.combine(TODAY, dt_time(19, 30))
        _, cap = window(rows, now)[7]
        self.assertEqual(cap, datetime.combine(TODAY, dt_time(19, 0)))

    def test_before_the_shift_there_is_no_tail_at_all(self):
        rows = [(7, TODAY, dt_time(12, 0), dt_time(16, 0))]
        now = datetime.combine(TODAY, dt_time(11, 24))
        self.assertEqual(window(rows, now), {})

    def test_night_shift_from_yesterday_keeps_its_own_start(self):
        """Ночная 21:00–09:00 заходит в сегодня своим концом: пол считается от
        вчерашнего начала, иначе утро такой смены осталось бы без хвоста."""
        rows = [(7, YESTERDAY, dt_time(21, 0), dt_time(9, 0))]
        now = datetime.combine(TODAY, dt_time(8, 0))
        self.assertEqual(
            window(rows, now),
            {7: (datetime.combine(YESTERDAY, dt_time(20, 0)), now)}
        )

    def test_day_off_has_no_tail(self):
        """Сравнивать не с чем, а растущая полоса в выходной — лишний шум."""
        self.assertEqual(window([], datetime.combine(TODAY, dt_time(11, 30))), {})

    def test_yesterday_day_shift_does_not_cover_today(self):
        rows = [(7, YESTERDAY, dt_time(13, 0), dt_time(21, 0))]
        self.assertEqual(window(rows, datetime.combine(TODAY, dt_time(11, 30))), {})


class LiveStatusTailEventTests(unittest.TestCase):
    """Какое событие считается текущим статусом, а какое — залипшим."""

    def _run(self, event_at, tail_window, known_until=None):
        now = datetime.combine(TODAY, dt_time(12, 30))
        rows = [(7, event_at, 'готов', None)]

        class Owner(Stub):
            def _live_status_track_operator_ids_tx(self, cursor, operator_ids=None):
                return [7]

            def _normalize_import_status_key(self, value):
                return str(value or '').strip().lower()

            def _load_operator_calculation_models_tx(self, cursor, operator_ids):
                return {7: 'operator'}

            def _live_status_tail_window_tx(self, cursor, operator_ids, as_of_dt):
                return {7: tail_window} if tail_window else {}

            def _imported_status_segment_payload(self, **kwargs):
                return {'start': kwargs['start_at_value'], 'end': kwargs['end_at_value']}

        namespace_globals = {'CHAT_MANAGER_ACTION_STATUS_KEYS': set(), 'CALCULATION_MODEL_OPERATOR': 'operator'}
        node = copy.deepcopy(source_cache.function_node(DATABASE_PATH, '_load_live_status_tail_for_operators', class_name='Database'))
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {'datetime': datetime, 'timedelta': timedelta, **namespace_globals}
        exec(compile(module, str(DATABASE_PATH), 'exec'), namespace)
        return namespace['_load_live_status_tail_for_operators'](
            Owner(), FakeCursor(rows), [7], as_of=now, known_until=known_until
        )

    def test_status_set_in_this_shift_becomes_the_tail(self):
        tail_window = (datetime.combine(TODAY, dt_time(11, 0)), datetime.combine(TODAY, dt_time(12, 30)))
        result = self._run(datetime.combine(TODAY, dt_time(11, 29)), tail_window)
        self.assertEqual(result[7]['end'], datetime.combine(TODAY, dt_time(12, 30)))

    def test_status_left_from_yesterday_is_not_current(self):
        """Забытый вчера «Готов» в 12:01 нарисовал бы работу с полуночи и 100 %
        соответствия человеку, которого на линии нет."""
        tail_window = (datetime.combine(TODAY, dt_time(11, 0)), datetime.combine(TODAY, dt_time(12, 30)))
        self.assertEqual(self._run(datetime.combine(YESTERDAY, dt_time(8, 23)), tail_window), {})

    def test_without_a_shift_window_there_is_no_tail(self):
        self.assertEqual(self._run(datetime.combine(TODAY, dt_time(11, 29)), None), {})

    def test_tail_starts_where_saved_segments_end(self):
        """Пересборка после события дописывает отрезок до своего момента, и
        хвост от того же события лёг бы поверх него: в подписях под полосой
        «Готов» посчитался бы дважды."""
        tail_window = (datetime.combine(TODAY, dt_time(11, 0)), datetime.combine(TODAY, dt_time(12, 30)))
        result = self._run(
            datetime.combine(TODAY, dt_time(11, 29)),
            tail_window,
            known_until={7: datetime.combine(TODAY, dt_time(12, 20))}
        )
        self.assertEqual(result[7]['start'], datetime.combine(TODAY, dt_time(12, 20)))

    def test_nothing_to_draw_when_segments_already_reach_the_cap(self):
        tail_window = (datetime.combine(TODAY, dt_time(11, 0)), datetime.combine(TODAY, dt_time(12, 30)))
        self.assertEqual(
            self._run(
                datetime.combine(TODAY, dt_time(11, 29)),
                tail_window,
                known_until={7: datetime.combine(TODAY, dt_time(12, 30))}
            ),
            {}
        )

    def test_last_end_is_the_latest_across_days(self):
        # Декоратор @staticmethod с узла снят — зовём как обычную функцию.
        last_end = load_db_method('_timeline_days_last_end')({
            '2026-09-20': [{'end': '2026-09-20T23:59:00'}],
            '2026-09-21': [{'end': '2026-09-21T09:00:00'}, {'end': '2026-09-21T11:42:22'}],
        })
        self.assertEqual(last_end, datetime(2026, 9, 21, 11, 42, 22))


class LiveStatusScopeTests(unittest.TestCase):
    """Живой режим включён только там, где статусы приезжают пушем."""

    def test_only_tez_department_is_live(self):
        match = re.search(r"LIVE_STATUS_TRACK_DEPARTMENT_CODES = \(([^)]*)\)", DATABASE_SRC)
        self.assertIsNotNone(match)
        self.assertEqual(
            [code.strip().strip("'\"") for code in match.group(1).split(',') if code.strip()],
            ['tez']
        )

    def test_narrow_endpoint_keeps_the_usual_perimeter(self):
        start = BOT_SRC.index("@app.route('/api/work_schedules/status_track'")
        end = BOT_SRC.index("@app.route('/api/work_schedules/direction'", start)
        handler = BOT_SRC[start:end]
        # Оператор получает только себя, руководитель — своих людей тем же
        # фильтром, что и в сетке: своего правила видимости здесь нет.
        self.assertIn("if _normalize_user_role(requester[3]) == 'operator':", handler)
        self.assertIn('operator_ids = [int(requester_id)]', handler)
        self.assertIn('_resolve_work_schedule_viewer()', handler)
        self.assertIn('_filter_operators_for_requester_scope(', handler)


class LiveStatusRefreshTests(unittest.TestCase):
    """Автообновление не должно возвращать раздел к тяжёлым запросам."""

    def test_timer_asks_only_the_narrow_endpoint(self):
        self.assertEqual(APP.count('/api/work_schedules/status_track?'), 2)
        for loader in ('const loadMyStatusTrack = useCallback(', 'const loadPlannerStatusTrack = useCallback('):
            block = APP[APP.index(loader):]
            block = block[:block.index('}, [')]
            self.assertIn('/api/work_schedules/status_track?', block)
            for heavy in ('/api/work_schedules/my?', '/api/work_schedules/operators?'):
                self.assertNotIn(heavy, block)

    def test_hidden_tab_does_not_poll(self):
        """Скрытая вкладка запросов не шлёт: смотреть там некому."""
        self.assertEqual(APP.count("if (document.visibilityState === 'visible') loadMyStatusTrack();"), 1)
        self.assertEqual(APP.count("if (document.visibilityState === 'visible') loadPlannerStatusTrack(plannerLiveStatusDateKey);"), 1)
        self.assertEqual(APP.count('setInterval(tick, LIVE_STATUS_TRACK_REFRESH_MS)'), 2)

    def test_every_consumer_reads_the_same_timeline(self):
        """Сетка, «Мои смены» и выгрузка обязаны брать статусы из одного места:
        разойдутся источники — у оператора и руководителя разойдутся проценты."""
        self.assertEqual(APP.count('plannerTimelineDaysWithLiveTail('), 3, 'три потребителя — три вызова')
        # Вызов стоит в самом начале каждого потребителя — там, где он берёт
        # карту дней; запас с избытком, чтобы тест не ловил соседний код.
        for consumer in (
            'const buildPlannerStatusAnalysisFromOperators',   # сетка и окна дня
            'const buildPlannerStatusMatchTimelineMap',        # выгрузка соответствия
            'const myTimelineOperator = useMemo(',             # «Мои смены»
        ):
            head = APP[APP.index(consumer):][:2500]
            self.assertIn('plannerTimelineDaysWithLiveTail(', head, consumer)

    def test_operator_sees_the_moment_the_numbers_are_made_for(self):
        """Живая полоса обрывается на середине смены — без «на 11:25» это
        читается как потеря данных."""
        self.assertIn('asOf={track.asOfLabel', APP)
        self.assertIn('на {asOf}', PHONE)
        self.assertIn('Обновить статусы', PHONE)
        self.assertIn('Обновить статусы', APP)


if __name__ == '__main__':
    unittest.main()
