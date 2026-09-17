# -*- coding: utf-8 -*-
"""«Уведомления об изменениях» — суточная сводка изменений графика в Telegram.

Что здесь сторожится и почему именно это.

С 17.09.2026 сводка — одна таблица по макету владельца: «Сотрудник · Группа ·
Было · Стало · Кто изменил». Самое дорогое в ней — неправда в ячейках «Было» и
«Стало», и она тут возникает легко: журнал пишет только ОТЛИЧИЯ дня, а таблица
показывает день целиком. Сняли одну смену из двух — в журнале одна строка
'removed', и честное «Стало» получается только откатом от текущего графика.
Поэтому тесты держат:

* откат: весь день, а не изменённые строки; правки после окна откатываются;
  смена вида при тех же часах видна; несошедшийся откат не выдумывает «нет смены»;
* строку — сотрудник + день, итог за сутки, и выпадение дня, вернувшегося как был;
* первичное внесение графика — не изменение, но перенос смены на пустой день и
  добор оператора — изменение;
* потолки rich-сообщения и обычного текста на заведомо огромных сутках.

Отдельно сторожится граница суток (`changed_at` лежит в алматинском времени без
пояса, а сессия Postgres на Render — в UTC) и область получателя: правило
раздела, а не правило остальных Telegram-отчётов портала. Разница не
академическая — на бою все главы отделов имеют роль `admin`, и по правилу
отчётов каждый из них получал бы сводку по всей компании.

Тест герметичен: `database.py` и `bot_schedule2.py` не импортируются (на
последней строке `database.py` создаётся `Database()` с пулом к боевой БД).
Нужное достаётся через AST, чистый модуль сводки импортируется напрямую.
"""

import ast
import html
import html.parser
import re
import types
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from tests import source_cache

from work_schedules import change_digest as digest


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"


@lru_cache(maxsize=None)
def _parsed(path):
    source = path.read_text(encoding="utf-8-sig")
    return source, source_cache.parse(source)


def _source_of(path, name, class_name=None):
    source, module = _parsed(path)
    body = module.body
    if class_name:
        body = next(node for node in body
                    if isinstance(node, ast.ClassDef) and node.name == class_name).body
    node = next(n for n in body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node)


DAY = date(2026, 9, 14)            # сутки сводки
D18 = date(2026, 9, 18)            # день графика
D19 = date(2026, 9, 19)
T1 = datetime(2026, 9, 14, 10, 0, 0, 111111)
T2 = datetime(2026, 9, 14, 11, 0, 0, 222222)
T3 = datetime(2026, 9, 14, 12, 0, 0, 333333)
LATER = datetime(2026, 9, 15, 8, 0, 0, 444444)   # после окна сводки

_ids = iter(range(1, 10 ** 6))


def journal(operator_id, shift_date, action, changed_at=T1, *, source='supervisor',
            actor_id=7, actor_name='Петров Пётр', start=None, end=None,
            prev_start=None, prev_end=None, shift_type=None, prev_shift_type=None,
            day_was_empty=False, operator_name=None, group_name='Поток 1'):
    """Строка журнала в том виде, в каком её отдаёт get_schedule_change_report_entries."""
    if action in ('added', 'changed') and shift_type is None:
        shift_type = 'regular'
    if action in ('removed', 'changed') and prev_shift_type is None:
        prev_shift_type = 'regular'
    return {
        'id': next(_ids),
        'operator_id': operator_id,
        'operator_name': operator_name or 'Оператор %d' % operator_id,
        'group_name': group_name,
        'shift_date': shift_date,
        'action': action,
        'source': source,
        'start': start, 'end': end, 'prev_start': prev_start, 'prev_end': prev_end,
        'shift_type': shift_type, 'prev_shift_type': prev_shift_type,
        'actor_id': actor_id,
        'actor_name': actor_name,
        'actor_role': 'sv',
        'changed_at': changed_at,
        'day_was_empty': day_was_empty,
    }


def fill(operator_id, shift_date, source='supervisor', changed_at=T1, actor_id=7,
         action='added', day_was_empty=True, **extra):
    """Заполнение дня — как его пишет дифф после 17.09.2026."""
    extra.setdefault('start', '09:00')
    extra.setdefault('end', '18:00')
    return journal(operator_id, shift_date, action, changed_at, source=source,
                   actor_id=actor_id, day_was_empty=day_was_empty, **extra)


def state(*shifts, day_off=False):
    return {'shifts': [(start, end, kind) for start, end, kind in shifts], 'day_off': day_off}


def one_row(entries, current, later=None):
    rows, _ = digest.build_rows(entries, current, later)
    return rows


class DayWindowTests(unittest.TestCase):
    """Граница суток. Самая дорогая ошибка в этой фиче: пятичасовой сдвиг
    тихо съедает или задваивает правки, и заметить это можно только сверив
    сводку с журналом вручную."""

    def test_window_is_half_open_and_naive(self):
        start, end = digest.day_window(DAY)
        self.assertEqual(start, datetime(2026, 9, 14, 0, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 15, 0, 0, 0))
        # Никакого tzinfo: колонка changed_at — TIMESTAMP без пояса, и
        # aware-граница сравнивалась бы с ней через приведение.
        self.assertIsNone(start.tzinfo)
        self.assertIsNone(end.tzinfo)

    def test_windows_of_neighbour_days_do_not_overlap(self):
        _, end = digest.day_window(DAY)
        next_start, _ = digest.day_window(DAY + timedelta(days=1))
        self.assertEqual(end, next_start)

    def test_report_is_about_yesterday(self):
        self.assertEqual(digest.previous_day(datetime(2026, 9, 15, 9, 45)), DAY)
        # Запуск после misfire в другое время тех же суток даёт тот же период.
        self.assertEqual(digest.previous_day(datetime(2026, 9, 15, 23, 59)), DAY)


class BeforeAfterTests(unittest.TestCase):
    """«Было» и «Стало» — весь день, восстановленный откатом журнала."""

    def test_moved_shift(self):
        rows = one_row(
            [journal(1, D18, 'changed', start='12:00', end='21:00',
                     prev_start='09:00', prev_end='18:00', operator_name='Иванов Иван')],
            {(1, D18): state(('12:00', '21:00', 'regular'))})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['operator_name'], 'Иванов Иван')
        self.assertEqual(rows[0]['group_name'], 'Поток 1')
        self.assertEqual(rows[0]['before'], '18.09, 09:00–18:00')
        self.assertEqual(rows[0]['after'], '18.09, 12:00–21:00')
        self.assertEqual(rows[0]['actors'], 'Петров Пётр')

    def test_removing_one_of_two_shifts_keeps_the_other_in_both_cells(self):
        """Журнал знает только снятую смену. «Стало: нет смены» было бы
        неправдой — утренняя смена осталась."""
        rows = one_row(
            [journal(1, D18, 'removed', prev_start='18:00', prev_end='22:00')],
            {(1, D18): state(('09:00', '13:00', 'regular'))})
        self.assertEqual(rows[0]['before'], '18.09, 09:00–13:00, 18:00–22:00')
        self.assertEqual(rows[0]['after'], '18.09, 09:00–13:00')

    def test_day_off_over_a_shift(self):
        rows = one_row(
            [journal(1, D18, 'removed', prev_start='09:00', prev_end='18:00'),
             journal(1, D18, 'day_off_set')],
            {(1, D18): state(day_off=True)})
        self.assertEqual(rows[0]['before'], '18.09, 09:00–18:00')
        self.assertEqual(rows[0]['after'], '18.09 — выходной')

    def test_shift_removed_to_nothing(self):
        rows = one_row(
            [journal(1, D18, 'removed', prev_start='09:00', prev_end='18:00')],
            {(1, D18): state()})
        self.assertEqual(rows[0]['after'], '18.09 — нет смены')

    def test_shift_type_change_with_the_same_hours_is_visible(self):
        """88 таких правок на бою: без вида смены «Было» и «Стало» совпали бы."""
        rows = one_row(
            [journal(1, D18, 'changed', start='09:00', end='18:00', prev_start='09:00',
                     prev_end='18:00', shift_type='office_practice', prev_shift_type='regular')],
            {(1, D18): state(('09:00', '18:00', 'office_practice'))})
        self.assertEqual(rows[0]['before'], '18.09, 09:00–18:00')
        self.assertEqual(rows[0]['after'], '18.09, 09:00–18:00 (практика в офисе)')

    def test_changes_made_after_the_window_are_rolled_back(self):
        """Сводка уходит в 09:45 — к этому времени смену могли подвинуть ещё
        раз. «Стало» — конец отчётных суток, а не текущий график."""
        # Вечернюю смену сняли в отчётные сутки, утреннюю подвинули уже утром
        # в день рассылки. Без отката поздней правки обе ячейки показали бы
        # сегодняшнее 10:00–14:00, которого вчера ещё не было.
        window = [journal(1, D18, 'removed', T1, prev_start='18:00', prev_end='22:00')]
        later = [journal(1, D18, 'changed', LATER, start='10:00', end='14:00',
                         prev_start='09:00', prev_end='13:00')]
        rows = one_row(window, {(1, D18): state(('10:00', '14:00', 'regular'))}, later)
        self.assertEqual(rows[0]['before'], '18.09, 09:00–13:00, 18:00–22:00')
        self.assertEqual(rows[0]['after'], '18.09, 09:00–13:00')

    def test_database_time_values(self):
        rows = one_row(
            [journal(1, D18, 'changed', start=time(12, 0), end=time(21, 0),
                     prev_start=time(9, 0), prev_end=time(18, 0))],
            {(1, D18): state((time(12, 0), time(21, 0), 'regular'))})
        self.assertEqual(rows[0]['before'], '18.09, 09:00–18:00')

    def test_night_shift(self):
        rows = one_row(
            [journal(1, D18, 'changed', start='21:00', end='03:00',
                     prev_start='20:00', prev_end='02:00')],
            {(1, D18): state(('21:00', '03:00', 'regular'))})
        self.assertEqual(rows[0]['after'], '18.09, 21:00–03:00')

    def test_unmatched_journal_does_not_invent_an_empty_day(self):
        """Правка мимо журнала: в графике нет смены, которую журнал называет
        добавленной. Откату верить нельзя — видны только изменённые смены, а
        пустая сторона не превращается в «нет смены»."""
        rows = one_row(
            [journal(1, D18, 'added', start='18:00', end='22:00')],
            {(1, D18): state()})
        self.assertEqual(rows[0]['before'], '18.09')
        self.assertEqual(rows[0]['after'], '18.09, 18:00–22:00')


class RowTests(unittest.TestCase):
    """Строка — сотрудник + день графика, итог за сутки."""

    def test_several_edits_of_one_day_are_one_row(self):
        entries = [
            journal(1, D18, 'changed', T1, start='12:00', end='21:00',
                    prev_start='09:00', prev_end='18:00'),
            journal(1, D18, 'changed', T2, start='10:00', end='19:00',
                    prev_start='12:00', prev_end='21:00', actor_id=8, actor_name='Сабыр Азана'),
        ]
        rows = one_row(entries, {(1, D18): state(('10:00', '19:00', 'regular'))})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['before'], '18.09, 09:00–18:00')
        self.assertEqual(rows[0]['after'], '18.09, 10:00–19:00')
        self.assertEqual(rows[0]['actors'], 'Петров Пётр, Сабыр Азана')

    def test_day_returned_as_it_was_has_no_row(self):
        entries = [
            journal(1, D18, 'removed', T1, prev_start='09:00', prev_end='18:00'),
            fill(1, D18, changed_at=T2),
        ]
        rows, first = digest.build_rows(entries, {(1, D18): state(('09:00', '18:00', 'regular'))})
        self.assertEqual(rows, [])
        # Заполнение шло ПОСЛЕ снятия — это не внесение графика.
        self.assertEqual(first, [])

    def test_day_filled_and_then_moved_shows_the_move_only(self):
        entries = [
            fill(1, D18, source='import', changed_at=T1, actor_id=5, actor_name='Кастек Гаухар'),
            journal(1, D18, 'changed', T2, start='10:00', end='19:00',
                    prev_start='09:00', prev_end='18:00'),
        ]
        rows, first = digest.build_rows(entries, {(1, D18): state(('10:00', '19:00', 'regular'))})
        self.assertEqual(rows[0]['before'], '18.09, 09:00–18:00')
        self.assertEqual(rows[0]['after'], '18.09, 10:00–19:00')
        self.assertEqual(rows[0]['actors'], 'Петров Пётр')
        self.assertEqual(len(first), 1)

    def test_only_entries_give_no_rows(self):
        entries = [fill(operator_id, D18, source='import') for operator_id in range(1, 6)]
        current = {(operator_id, D18): state(('09:00', '18:00', 'regular'))
                   for operator_id in range(1, 6)}
        rows, first = digest.build_rows(entries, current)
        self.assertEqual(rows, [])
        self.assertEqual(len(first), 5)

    def test_transfer_to_an_empty_day_keeps_both_days(self):
        entries = [
            journal(1, D18, 'removed', T1, prev_start='09:00', prev_end='18:00'),
            fill(1, D19, changed_at=T1),
        ]
        current = {(1, D18): state(), (1, D19): state(('09:00', '18:00', 'regular'))}
        rows = one_row(entries, current)
        self.assertEqual([(row['before'], row['after']) for row in rows], [
            ('18.09, 09:00–18:00', '18.09 — нет смены'),
            ('19.09 — нет смены', '19.09, 09:00–18:00'),
        ])

    def test_way_is_named_unless_it_is_manual(self):
        entries = [
            fill(1, D18, source='auction_topup', actor_id=1, actor_name='Ли Анна',
                 start='18:00', end='22:00'),
            journal(2, D18, 'removed', source='swap', actor_id=3, actor_name='Ким Ольга',
                    prev_start='09:00', prev_end='18:00'),
        ]
        current = {(1, D18): state(('18:00', '22:00', 'regular')), (2, D18): state()}
        rows = {row['operator_id']: row for row in one_row(entries, current)}
        # Добор на свободный день — изменение, а не внесение.
        self.assertEqual(rows[1]['before'], '18.09 — нет смены')
        self.assertEqual(rows[1]['actors'], 'Ли Анна (добор с аукциона)')
        self.assertEqual(rows[2]['actors'], 'Ким Ольга (обмен сменами)')

    def test_one_author_with_two_ways(self):
        entries = [
            journal(1, D18, 'changed', T1, source='import', start='10:00', end='19:00',
                    prev_start='09:00', prev_end='18:00', actor_name='Кастек Гаухар'),
            journal(1, D18, 'changed', T2, start='11:00', end='20:00',
                    prev_start='10:00', prev_end='19:00', actor_name='Кастек Гаухар'),
        ]
        rows = one_row(entries, {(1, D18): state(('11:00', '20:00', 'regular'))})
        self.assertEqual(rows[0]['actors'], 'Кастек Гаухар (загрузка из файла, вручную)')

    def test_rows_go_by_group_then_name_then_day_and_groupless_last(self):
        def move(operator_id, day, name, group):
            return journal(operator_id, day, 'removed', prev_start='09:00', prev_end='18:00',
                           operator_name=name, group_name=group)
        entries = [
            move(1, D19, 'Борисов', 'Поток 1'),
            move(2, D18, 'Акимова', ''),
            move(1, D18, 'Борисов', 'Поток 1'),
            move(3, D18, 'Аринов', 'Поток 1'),
            move(4, D18, 'Яковлев', 'Основа'),
        ]
        current = {(entry['operator_id'], entry['shift_date']): state() for entry in entries}
        rows = one_row(entries, current)
        self.assertEqual([(row['operator_name'], row['shift_date']) for row in rows], [
            ('Яковлев', D18), ('Аринов', D18), ('Борисов', D18), ('Борисов', D19), ('Акимова', D18),
        ])


class FirstEntryTests(unittest.TestCase):
    """Первичное внесение графика — не изменение (постановка 17.09.2026).

    На боевом журнале 24.08–17.09.2026 заполнение пустых дней — 3549
    «правок» из 5635: загрузка файла на месяц вперёд выглядела в сводке как
    перетряска графика. Рядом сторожится обратная ошибка — спрятать настоящую
    правку, похожую на внесение."""

    def test_loading_an_empty_month_is_not_a_change(self):
        rows = [fill(operator_id, DAY + timedelta(days=offset), source='import')
                for operator_id in range(1, 55) for offset in range(7)]
        changes, first = digest.split_first_entries(rows)
        self.assertEqual(changes, [])
        self.assertEqual(len(first), len(rows))

    def test_filling_by_hand_and_publishing_the_auction_are_entries_too(self):
        rows = [
            fill(1, D18, source='supervisor'),
            fill(2, D18, source='auction'),
            fill(3, D18, source='supervisor', action='day_off_set'),
        ]
        changes, first = digest.split_first_entries(rows)
        self.assertEqual(changes, [])
        self.assertEqual(len(first), 3)

    def test_edits_of_a_filled_day_stay(self):
        rows = [
            journal(1, D18, 'changed', start='10:00', end='19:00', prev_start='09:00', prev_end='18:00'),
            journal(2, D18, 'removed', prev_start='09:00', prev_end='18:00'),
            journal(3, D18, 'day_off_cleared'),
        ]
        changes, first = digest.split_first_entries(rows)
        self.assertEqual(len(changes), 3)
        self.assertEqual(first, [])

    def test_second_shift_on_a_filled_day_is_a_change(self):
        """Тот же 'added', что у внесения, но день уже был заполнен."""
        changes, first = digest.split_first_entries([fill(1, D18, day_was_empty=False)])
        self.assertEqual(len(changes), 1)
        self.assertEqual(first, [])

    def test_operator_actions_on_an_empty_day_are_changes(self):
        """Добор почти всегда ложится на свободный день, обмен отдаёт смену
        тому, у кого её не было. Это правки уже внесённого графика."""
        for source in ('swap', 'auction_topup', 'auction_admin', 'shift_request'):
            with self.subTest(source):
                changes, first = digest.split_first_entries([fill(1, D18, source=source)])
                self.assertEqual(len(changes), 1)
                self.assertEqual(first, [])

    def test_unknown_source_is_never_hidden(self):
        changes, _ = digest.split_first_entries([fill(1, D18, source='что_то_новое')])
        self.assertEqual(len(changes), 1)

    def test_rows_written_before_the_flag_are_judged_by_actions(self):
        rows = [
            fill(1, D18, day_was_empty=None),
            fill(2, D18, source='import', action='day_off_set', day_was_empty=None),
            journal(3, D18, 'removed', prev_start='09:00', prev_end='18:00', day_was_empty=None),
            journal(3, D18, 'day_off_set', day_was_empty=None),
        ]
        changes, first = digest.split_first_entries(rows)
        self.assertEqual([row['operator_id'] for row in first], [1, 2])
        self.assertEqual([row['operator_id'] for row in changes], [3, 3])

    def test_one_change_is_never_split_between_the_halves(self):
        """Две пачки строк одной транзакции по одному дню: у второй флаг
        честный True, но правка целиком — не внесение."""
        rows = [
            fill(1, D18, day_was_empty=True),
            fill(1, D18, action='day_off_set', day_was_empty=False),
        ]
        changes, first = digest.split_first_entries(rows)
        self.assertEqual(len(changes), 2)
        self.assertEqual(first, [])

    def test_transfer_is_one_edit_and_one_fill_in_one_operation(self):
        transfer = [journal(1, D18, 'removed', prev_start='09:00', prev_end='18:00'), fill(1, D19)]
        changes, first = digest.split_first_entries(transfer)
        self.assertEqual(len(changes), 2)
        self.assertEqual(first, [])
        # Два заполнения одной операцией — это внесение, а не перенос.
        changes, first = digest.split_first_entries([fill(1, D18), fill(1, D19)])
        self.assertEqual((len(changes), len(first)), (0, 2))
        # Массовая операция на неделю переносом не считается.
        week = [journal(1, D18, 'removed', prev_start='09:00', prev_end='18:00')]
        week += [fill(1, D18 + timedelta(days=offset)) for offset in range(1, 4)]
        changes, first = digest.split_first_entries(week)
        self.assertEqual((len(changes), len(first)), (1, 3))

    def test_order_is_kept(self):
        rows = [
            journal(1, D18, 'changed', T1, start='10:00', end='19:00', prev_start='09:00', prev_end='18:00'),
            fill(2, D18, changed_at=T1),
            journal(3, D18, 'removed', T2, prev_start='09:00', prev_end='18:00'),
            fill(4, D18, changed_at=T3),
            journal(5, D18, 'changed', T3, start='10:00', end='19:00', prev_start='09:00', prev_end='18:00'),
        ]
        changes, first = digest.split_first_entries(rows)
        self.assertEqual([row['operator_id'] for row in changes], [1, 3, 5])
        self.assertEqual([row['operator_id'] for row in first], [2, 4])

    def test_day_of_only_entries_names_them_instead_of_saying_nobody_changed(self):
        first = [fill(operator_id, DAY + timedelta(days=offset), source='import')
                 for operator_id in (1, 2) for offset in range(3)]
        for build in (digest.build_digest, digest.build_digest_rich):
            with self.subTest(build.__name__):
                text = build(DAY, [], escape=html.escape, first_entries=first)
                self.assertIn('Первичное внесение графика — 6 дней у 2 сотрудников', text)
                self.assertNotIn('никто не менял', text)


class _TagBalance(html.parser.HTMLParser):
    """Разметка rich-сообщения сбалансирована: Telegram не принимает сообщение
    с оборванным тегом целиком."""

    VOID = {'br'}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(tag)
        else:
            self.stack.pop()


def _check_markup(testcase, markup):
    parser = _TagBalance()
    parser.feed(markup)
    parser.close()
    testcase.assertEqual(parser.errors, [])
    testcase.assertEqual(parser.stack, [])
    return parser.tags


def table_row(operator_id=1, name='Иванов Иван', group='Поток 1', before='18.09, 09:00–18:00',
              after='18.09, 12:00–21:00', actors='Петров Пётр'):
    return {'operator_id': operator_id, 'operator_name': name, 'group_name': group,
            'shift_date': D18, 'before': before, 'after': after, 'actors': actors}


class RichTableTests(unittest.TestCase):
    """Таблица по макету владельца — rich-сообщение (sendRichMessage, поле html)."""

    def test_title_and_columns_follow_the_mockup(self):
        markup = digest.build_digest_rich(DAY, [table_row()], escape=html.escape)
        _check_markup(self, markup)
        self.assertEqual(
            markup,
            '<h3>🔔 Изменения графика — 14.09.2026</h3>'
            '<table><tr><th>Сотрудник</th><th>Группа</th><th>Было: дата и время</th>'
            '<th>Стало: дата и время</th><th>Кто изменил</th></tr>'
            '<tr><td>Иванов Иван</td><td>Поток 1</td><td>18.09, 09:00–18:00</td>'
            '<td>18.09, 12:00–21:00</td><td>Петров Пётр</td></tr></table>')
        # Причины портал не хранит, а «СВ» заменён на того, кто правил.
        self.assertNotIn('Причина', markup)
        self.assertNotIn('<th>СВ</th>', markup)

    def test_employee_without_group_gets_a_dash(self):
        markup = digest.build_digest_rich(DAY, [table_row(group='')], escape=html.escape)
        self.assertIn('<td>Иванов Иван</td><td>—</td>', markup)

    def test_values_are_escaped(self):
        markup = digest.build_digest_rich(
            DAY, [table_row(name='<b>Хакер</b> & Co', group='a<script>', actors='x</td>')],
            escape=html.escape)
        _check_markup(self, markup)
        self.assertNotIn('<script>', markup)
        self.assertNotIn('<b>Хакер', markup)
        self.assertIn('&amp; Co', markup)

    def test_huge_day_stays_under_rich_message_limits_with_an_honest_tail(self):
        """32 768 символов и 500 блоков (строка таблицы — тоже блок). Сверх
        потолка Telegram сообщение не принимает целиком."""
        long_name = 'Я' * 255
        rows = [table_row(operator_id=index, name=long_name, group=long_name, actors=long_name)
                for index in range(1000)]
        markup = digest.build_digest_rich(DAY, rows, escape=html.escape)
        tags = _check_markup(self, markup)
        self.assertLess(len(markup), digest.RICH_TEXT_LIMIT)
        self.assertLess(sum(1 for tag in tags if tag in ('h3', 'p', 'table', 'tr')),
                        digest.RICH_BLOCK_LIMIT)
        shown = markup.count('<tr>') - 2   # без заголовка и хвоста
        rest = 1000 - shown
        self.assertIn('<td colspan="5"><i>… и ещё %d %s' % (rest, digest.changes_word(rest)), markup)

    def test_many_short_rows_are_capped_by_blocks(self):
        rows = [table_row(operator_id=index, name='И', group='', actors='П') for index in range(1000)]
        markup = digest.build_digest_rich(DAY, rows, escape=html.escape)
        self.assertLess(markup.count('<tr>') + 2, digest.RICH_BLOCK_LIMIT)
        self.assertIn('… и ещё', markup)

    def test_no_line_breaks_between_blocks(self):
        markup = digest.build_digest_rich(DAY, [table_row(), table_row(2)], escape=html.escape)
        self.assertNotIn('\n', markup)

    def test_empty_day_stays_short(self):
        markup = digest.build_digest_rich(DAY, [], escape=html.escape)
        _check_markup(self, markup)
        self.assertIn('никто не менял', markup)
        self.assertNotIn('<table', markup)


class TextFallbackTests(unittest.TestCase):
    """Запасной текст: уходит, только если Telegram rich-сообщение не принял."""

    def test_row_reads_as_before_arrow_after(self):
        text = digest.build_digest(DAY, [table_row()], escape=html.escape)
        self.assertIn('<b>🔔 Изменения графика — 14.09.2026</b>', text)
        self.assertIn('• <b>Иванов Иван</b> · Поток 1\n'
                      '   18.09, 09:00–18:00 → 18.09, 12:00–21:00\n'
                      '   Петров Пётр', text)

    def test_huge_day_fits_into_one_message(self):
        """Потолок 4096 — жёсткий: сообщение сверх него просто не уходит."""
        rows = [table_row(operator_id=index, name='Сотрудник с длинной фамилией %d' % index)
                for index in range(300)]
        text = digest.build_digest(DAY, rows, escape=html.escape)
        self.assertLess(len(text), 4096)
        self.assertIn('… и ещё', text)


class ScheduleContractTests(unittest.TestCase):
    """Расписание и подпись под тумблером обязаны совпадать: окно настроек
    обещает получателю конкретные минуты."""

    def test_hint_matches_the_declared_time(self):
        self.assertIn('%02d:%02d' % (digest.SEND_HOUR, digest.SEND_MINUTE), digest.REPORT_HINT)

    def test_cron_job_uses_the_same_constants_and_almaty(self):
        source, _ = _parsed(BOT_PATH)
        job = re.search(
            r"scheduler\.add_job\(\s*send_daily_schedule_change_report,(.*?)\)\s*\n",
            source, re.S)
        self.assertIsNotNone(job, "джоба суточной сводки не зарегистрирована")
        body = job.group(1)
        self.assertIn('schedule_change_digest.SEND_HOUR', body)
        self.assertIn('schedule_change_digest.SEND_MINUTE', body)
        self.assertIn("ZoneInfo('Asia/Almaty')", body)
        self.assertIn("id='work_schedule_change_report_daily'", body)
        self.assertIn('max_instances=1', body)

    def test_job_minute_is_not_taken_by_another_daily_digest(self):
        """Две сводки в одну минуту приходят слипшимся комом."""
        source, _ = _parsed(BOT_PATH)
        busy = set()
        for match in re.finditer(r"CronTrigger\(([^)]*)\)", source):
            args = match.group(1)
            hour = re.search(r"hour=(\d+)", args)
            minute = re.search(r"minute=(\d+)", args)
            if hour and minute:
                busy.add((int(hour.group(1)), int(minute.group(1))))
        self.assertNotIn((digest.SEND_HOUR, digest.SEND_MINUTE), busy)


class DeliveryContractTests(unittest.TestCase):
    """Проводка на стороне монолита — то, что ломается тихо."""

    def test_send_claims_before_building_and_keeps_the_claim_when_empty(self):
        source = _source_of(BOT_PATH, "sync_send_schedule_change_report")
        claim = source.index("claim_schedule_change_report_send")
        send = source.index("_send_schedule_change_report_to")
        self.assertLess(claim, send, "заявка должна браться ДО отправки")
        # Пустые сутки заявку не снимают: сводки за них не будет и завтра.
        empty_branch = source[source.index("reason == 'empty'"):]
        self.assertNotIn("release_schedule_change_report_send",
                         empty_branch.split("else:")[0])

    def test_routes_check_the_same_predicate_as_the_menu(self):
        for name in ("work_schedule_change_report_subscription",
                     "work_schedule_change_report_preview"):
            source = _source_of(BOT_PATH, name)
            self.assertIn("_resolve_work_schedule_viewer", source, name)
            self.assertIn("_schedule_change_report_scope", source, name)
            self.assertIn("403", source, name)

    def test_preview_refuses_without_telegram(self):
        source = _source_of(BOT_PATH, "work_schedule_change_report_preview")
        self.assertIn("TELEGRAM_NOT_CONNECTED", source)

    def test_trainer_is_excluded_from_the_subscription(self):
        source = _source_of(BOT_PATH, "_schedule_change_report_scope")
        self.assertIn("'trainer'", source)

    def test_claim_failure_does_not_stop_the_rest(self):
        source = _source_of(BOT_PATH, "sync_send_schedule_change_report")
        before_claim = source[:source.index("claim_schedule_change_report_send")]
        self.assertGreater(before_claim.rfind("try:"),
                           before_claim.rfind("for recipient in recipients:"))


class RichDeliveryTests(unittest.TestCase):
    """Доставка: функции монолита исполняются по-настоящему, сеть и база — подставные."""

    @staticmethod
    def _function(name, namespace, class_name=None):
        source = _source_of(BOT_PATH if class_name is None else DATABASE_PATH, name, class_name)
        exec(compile(ast.parse(source.strip() if class_name is None else _dedent(source)),
                     name, 'exec'), namespace)
        return namespace[name]

    def _send(self, payload, rich_result, force=False):
        calls = []
        namespace = {
            'logging': types.SimpleNamespace(warning=lambda *args, **kwargs: None),
            '_build_schedule_change_report_payload': lambda *args: payload,
            '_tg_send_rich_message': lambda chat_id, markup: calls.append('rich') or rich_result,
            '_tg_send_message': lambda chat_id, text: calls.append('text') or ({}, None),
        }
        send = self._function('_send_schedule_change_report_to', namespace)
        result = send({'id': 1, 'telegram_id': 55}, DAY, force=force)
        return result, calls

    def test_rich_message_goes_first(self):
        result, calls = self._send(([{}], '<h3>…</h3>', 'текст'), ({}, None, False))
        self.assertEqual(result, (True, 'sent'))
        self.assertEqual(calls, ['rich'])

    def test_refused_rich_message_falls_back_to_text(self):
        result, calls = self._send(([{}], '<h3>…</h3>', 'текст'),
                                   (None, "Bad Request: can't parse rich message", True))
        self.assertEqual(result, (True, 'sent'))
        self.assertEqual(calls, ['rich', 'text'])

    def test_network_failure_is_not_retried_as_text(self):
        """Таймаут не значит «не дошло»: повтор текстом прислал бы сводку дважды."""
        result, calls = self._send(([{}], '<h3>…</h3>', 'текст'), (None, 'Read timed out', False))
        self.assertEqual(result, (False, 'send_failed'))
        self.assertEqual(calls, ['rich'])

    def test_day_without_rows_is_silent_unless_forced(self):
        result, calls = self._send(([], '<h3>…</h3>', 'текст'), ({}, None, False))
        self.assertEqual(result, (False, 'empty'))
        self.assertEqual(calls, [])
        result, calls = self._send(([], '<h3>…</h3>', 'текст'), ({}, None, False), force=True)
        self.assertEqual(result, (True, 'sent'))

    def test_payload_rolls_back_from_the_schedule_after_the_window(self):
        loaded = [
            fill(1, D18, source='import'),
            journal(3, D18, 'changed', start='10:00', end='19:00', prev_start='09:00',
                    prev_end='18:00', operator_name='Иванов Иван'),
        ]
        asked = {}

        def day_states(keys, since):
            asked['keys'], asked['since'] = set(keys), since
            return ({(1, D18): state(('09:00', '18:00', 'regular')),
                     (3, D18): state(('10:00', '19:00', 'regular'))}, [])

        namespace = {
            'db': types.SimpleNamespace(
                get_schedule_change_report_entries=lambda *args, **kwargs: loaded,
                get_schedule_change_report_day_states=day_states),
            'schedule_change_digest': digest,
            '_escape_telegram_html': html.escape,
        }
        build = self._function('_build_schedule_change_report_payload', namespace)
        rows, markup, text = build(DAY, [1])
        self.assertEqual([row['operator_id'] for row in rows], [3])
        self.assertEqual(asked['keys'], {(1, D18), (3, D18)})
        self.assertEqual(asked['since'], datetime(2026, 9, 15))
        self.assertIn('<td>18.09, 09:00–18:00</td><td>18.09, 10:00–19:00</td>', markup)
        self.assertIn('18.09, 09:00–18:00 → 18.09, 10:00–19:00', text)

    def test_empty_window_does_not_query_the_schedule(self):
        def day_states(keys, since):
            raise AssertionError('лишний запрос к базе')

        namespace = {
            'db': types.SimpleNamespace(
                get_schedule_change_report_entries=lambda *args, **kwargs: [],
                get_schedule_change_report_day_states=day_states),
            'schedule_change_digest': digest,
            '_escape_telegram_html': html.escape,
        }
        rows, _, _ = self._function('_build_schedule_change_report_payload', namespace)(DAY, None)
        self.assertEqual(rows, [])

    def test_rich_sender_tells_refusal_from_network_failure(self):
        sent = []

        class Response:
            def __init__(self, body, status_code):
                self.body = body
                self.status_code = status_code

            def json(self):
                return self.body

        def make(answer, status_code=400):
            def post(url, json=None, timeout=None):
                sent.append((url, json))
                if isinstance(answer, Exception):
                    raise answer
                return Response(answer, status_code)
            namespace = {
                'os': types.SimpleNamespace(getenv=lambda key: 'TOKEN'),
                'requests': types.SimpleNamespace(post=post),
                '_telegram_exception_text': str,
            }
            return self._function('_tg_send_rich_message', namespace)

        self.assertEqual(make({'ok': False, 'description': 'Bad Request'})(55, '<p>x</p>'),
                         (None, 'Bad Request', True))
        self.assertEqual(make(TimeoutError('Read timed out'))(55, '<p>x</p>'),
                         (None, 'Read timed out', False))
        # Ошибка на стороне Telegram — не отказ: сообщение могло уйти.
        self.assertEqual(make({'ok': False, 'description': 'Internal Server Error'}, 500)(55, '<p>x</p>'),
                         (None, 'Internal Server Error', False))
        self.assertEqual(make({'ok': True, 'result': {'message_id': 9}}, 200)(55, '<p>x</p>'),
                         ({'message_id': 9}, None, False))
        url, payload = sent[0]
        self.assertTrue(url.endswith('/botTOKEN/sendRichMessage'))
        self.assertEqual(payload['rich_message']['html'], '<p>x</p>')
        self.assertTrue(payload['rich_message']['skip_entity_detection'])


def _dedent(source):
    import textwrap
    return textwrap.dedent(source)


class _FakeCursor:
    """Курсор, отвечающий на запросы метода по тексту SQL."""

    def __init__(self, answers):
        self.answers = answers
        self.queries = []
        self._last = None

    def execute(self, sql, params=None):
        self.queries.append(sql)
        self._last = next((rows for marker, rows in self.answers if marker in sql), [])

    def fetchall(self):
        return self._last


class DayStatesQueryTests(unittest.TestCase):
    """get_schedule_change_report_day_states — исполняется по-настоящему на подставном курсоре."""

    def _run(self, keys, answers):
        _, module = _parsed(DATABASE_PATH)
        class_node = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'Database')
        source, _ = _parsed(DATABASE_PATH)
        namespace = {}
        for name in ('get_schedule_change_report_day_states', '_schedule_change_report_entry'):
            node = next(item for item in class_node.body
                        if isinstance(item, ast.FunctionDef) and item.name == name)
            fresh = ast.parse(_dedent(ast.get_source_segment(source, node))).body[0]
            fresh.decorator_list = []
            exec(compile(ast.Module(body=[fresh], type_ignores=[]), name, 'exec'), namespace)
        columns = next(
            item for item in class_node.body
            if isinstance(item, ast.Assign)
            and any(getattr(target, 'id', None) == 'SCHEDULE_CHANGE_REPORT_COLUMNS' for target in item.targets))
        cursor = _FakeCursor(answers)

        @contextmanager
        def get_cursor():
            yield cursor

        fake = types.SimpleNamespace(
            _get_cursor=get_cursor,
            SCHEDULE_CHANGE_REPORT_COLUMNS=ast.literal_eval(columns.value),
            _schedule_change_report_entry=namespace['_schedule_change_report_entry'],
        )
        result = namespace['get_schedule_change_report_day_states'](fake, keys, datetime(2026, 9, 15))
        return result, cursor

    def _journal_row(self, operator_id, shift_date):
        return (99, operator_id, 'Оператор', shift_date, 'changed', 'supervisor',
                time(10), time(19), time(9), time(18), 'regular', 'regular',
                7, 'Петров Пётр', 'sv', LATER, False)

    def test_cross_product_is_cut_to_the_asked_days(self):
        (states, later), cursor = self._run(
            {(1, D18), (2, D19)},
            [('FROM work_shifts', [(1, D18, time(9), time(18), 'regular'),
                                   (1, D19, time(8), time(17), 'regular')]),
             ('FROM days_off', [(2, D19), (2, D18)]),
             ('FROM work_shift_changes', [self._journal_row(1, D18), self._journal_row(2, D18)])])
        self.assertEqual(set(states), {(1, D18), (2, D19)})
        self.assertEqual(states[(1, D18)]['shifts'], [(time(9), time(18), 'regular')])
        self.assertTrue(states[(2, D19)]['day_off'])
        self.assertFalse(states[(1, D18)]['day_off'])
        self.assertEqual([(row['operator_id'], row['shift_date']) for row in later], [(1, D18)])
        self.assertEqual(later[0]['prev_shift_type'], 'regular')

    def test_schedule_and_journal_are_read_in_one_snapshot(self):
        _, cursor = self._run({(1, D18)}, [])
        self.assertIn('REPEATABLE READ', cursor.queries[0])
        self.assertIn('c.changed_at >= %s', cursor.queries[-1])

    def test_no_keys_no_queries(self):
        (states, later), cursor = self._run(set(), [])
        self.assertEqual((states, later, cursor.queries), ({}, [], []))


class ScopeTests(unittest.TestCase):
    """Область получателя. Главный дефект, который здесь ловится: все главы
    отделов на бою имеют роль `admin`, и правило «admin -> все отделы» (оно
    работает в отчётах по тренингам и сменам ставок) выдало бы каждому из них
    сводку по всей компании."""

    @staticmethod
    def _scope_row():
        source, module = _parsed(DATABASE_PATH)
        # Функция опирается на normalize_role_value и ROLE_ALIASES модуля —
        # берём их оттуда же, а не пишем в тесте копию.
        helpers = []
        for node in module.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'ROLE_ALIASES'
                    for target in node.targets):
                helpers.append(ast.get_source_segment(source, node))
            if isinstance(node, ast.FunctionDef) and node.name == 'normalize_role_value':
                helpers.append(ast.get_source_segment(source, node))
        namespace = {'Optional': __import__('typing').Optional}
        exec("\n\n".join(helpers), namespace)
        method = _source_of(DATABASE_PATH, "_schedule_change_report_scope_row", "Database")
        # Снимаем @staticmethod — функция нужна сама по себе. Узел из свежего
        # разбора, а не из общего дерева source_cache: его менять нельзя.
        node = ast.parse(method).body[0]
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<scope>", "exec"), namespace)
        return namespace["_schedule_change_report_scope_row"]

    def test_super_admin_alias_is_recognised(self):
        # В базе встречается и 'superadmin' — маршрут его нормализует, и
        # область сводки от маршрута отличаться не должна.
        result = self._scope_row()('superadmin', [1], ['СЗоВ'])
        self.assertEqual(result['scope'], 'global')

    def test_head_with_admin_role_gets_only_own_department(self):
        scope = self._scope_row()
        result = scope('admin', [1], ['СЗоВ — Служба заботы о водителях'])
        self.assertEqual(result['scope'], 'department')
        self.assertEqual(result['department_ids'], [1])
        self.assertEqual(result['scope_label'], 'СЗоВ — Служба заботы о водителях')

    def test_admin_without_department_sees_everything(self):
        result = self._scope_row()('admin', [], [])
        self.assertEqual(result['scope'], 'global')
        self.assertIsNone(result['department_ids'])

    def test_super_admin_stays_global_even_heading_a_department(self):
        # То же решение, что в _is_global_admin_requester.
        result = self._scope_row()('super_admin', [1], ['СЗоВ'])
        self.assertEqual(result['scope'], 'global')

    def test_head_of_several_departments_gets_all_of_them(self):
        result = self._scope_row()('admin', [1, 367], ['Отдел продаж', 'СЗоВ'])
        self.assertEqual(result['department_ids'], [1, 367])
        self.assertIn('Отдел продаж', result['scope_label'])
        self.assertIn('СЗоВ', result['scope_label'])

    def test_recipients_sql_filters_fired_trainers_and_telegramless(self):
        source = _source_of(DATABASE_PATH, "get_schedule_change_report_recipients", "Database")
        self.assertIn("u.telegram_id IS NOT NULL", source)
        # 'dismissal' — такое же увольнение: триггер гасит по нему сессии, и
        # остальные рассылки портала отсекают оба значения.
        self.assertIn("NOT IN ('fired', 'dismissal')", source)
        self.assertIn("<> 'trainer'", source)
        self.assertIn("schedule_change_report_enabled", source)

    def test_entries_are_filtered_by_change_time_and_operator_department(self):
        source = _source_of(DATABASE_PATH, "get_schedule_change_report_entries", "Database")
        # Окно по changed_at: три существующих читателя журнала фильтруют по
        # shift_date и для суточной сводки не годятся.
        self.assertIn("c.changed_at >= %s AND c.changed_at < %s", source)
        self.assertNotIn("BETWEEN", source)
        # Отдел — оператора, а не автора правки.
        self.assertIn("op.department_id = ANY", source)

    def test_group_is_the_one_on_the_schedule_day(self):
        source = _source_of(DATABASE_PATH, "get_schedule_change_report_entries", "Database")
        self.assertIn("m.start_date <= c.shift_date", source)
        self.assertIn("m.end_date >= c.shift_date", source)
        self.assertIn("LIMIT 1", source)

    def test_empty_department_scope_returns_nothing(self):
        source = _source_of(DATABASE_PATH, "get_schedule_change_report_entries", "Database")
        self.assertIn("return []", source)

    def test_subscription_checks_the_right_on_write_too(self):
        source = _source_of(DATABASE_PATH, "set_schedule_change_report_subscription", "Database")
        right = source.index("SCHEDULE_CHANGE_REPORT_SUBSCRIBER_SQL")
        insert = source.index("INSERT INTO admin_profiles")
        self.assertLess(right, insert,
                        "INSERT ... ON CONFLICT завёл бы настройку тому, кому не положено")


class SchemaTests(unittest.TestCase):

    def test_schema_has_column_table_and_index(self):
        source, _ = _parsed(DATABASE_PATH)
        self.assertIn("ADD COLUMN IF NOT EXISTS schedule_change_report_enabled", source)
        self.assertIn("CREATE TABLE IF NOT EXISTS schedule_change_report_sends", source)
        self.assertIn("PRIMARY KEY (period_start, user_id)", source)
        self.assertIn("idx_work_shift_changes_changed_at", source)

    def test_claim_survives_a_missing_table(self):
        source = _source_of(DATABASE_PATH, "claim_schedule_change_report_send", "Database")
        self.assertIn("to_regclass", source)
        self.assertIn("ON CONFLICT (period_start, user_id) DO NOTHING", source)


class InterfaceTests(unittest.TestCase):
    """Пункт меню и окно. Проверяется по исходнику: собирать React в pytest
    незачем, а ломается здесь ровно то, что видно регуляркой."""

    @staticmethod
    def _app_source():
        return APP_PATH.read_text(encoding="utf-8-sig")

    def test_menu_item_exists_and_is_gated_by_the_server_flag(self):
        source = self._app_source()
        self.assertIn("Уведомления об изменениях", source)
        block = source[source.index("{changeReport.canSubscribe && ("):]
        self.assertIn("Уведомления об изменениях", block[:2000])

    def test_menu_closes_before_the_modal_opens(self):
        """Панель меню живёт на z-[80] и перекрыла бы окно, а щелчки в окне
        не закрывали бы её."""
        source = self._app_source()
        block = source[source.index("{changeReport.canSubscribe && ("):]
        block = block[:block.index("setShowChangeReportModal(true)")]
        self.assertIn("setShowPlannerTopActionsMenu(false)", block)

    def test_modal_is_not_rendered_inside_the_menu_container(self):
        source = self._app_source()
        menu_start = source.index('ref={plannerTopActionsMenuRef}')
        modal_start = source.index("open={showChangeReportModal}")
        # Окно стоит в хвосте разметки, рядом с остальными модалками, далеко
        # за пределами контейнера меню.
        self.assertGreater(modal_start, menu_start)
        self.assertNotIn('plannerTopActionsMenuRef',
                         source[source.index("{/* ── «Уведомления об изменениях»"):modal_start])

    def test_subscription_is_not_confused_with_the_requests_watch(self):
        """В разделе уже есть подписка «Уведомлять меня о заявках отдела».
        Два тумблера уведомлений обязаны называться по-разному."""
        source = self._app_source()
        self.assertIn("Уведомлять меня о заявках отдела", source)
        self.assertIn("Уведомления об изменениях", source)

    def test_forbidden_is_not_an_error_on_the_client(self):
        source = self._app_source()
        loader = source[source.index("const loadChangeReportState"):]
        loader = loader[:loader.index("}, [isOperatorSelfSchedules, user]);")]
        self.assertIn("response.status === 403", loader)
        self.assertIn("canSubscribe: false", loader)

    def test_toggle_takes_telegram_state_from_the_response(self):
        source = self._app_source()
        toggle = source[source.index("const toggleChangeReport"):]
        toggle = toggle[:toggle.index("}, [changeReportSaving]);")]
        self.assertIn("data?.telegram_connected", toggle)

    def test_opening_the_window_rereads_the_state(self):
        source = self._app_source()
        block = source[source.index("{changeReport.canSubscribe && ("):]
        block = block[:block.index("setShowChangeReportModal(true)")]
        self.assertIn("loadChangeReportState()", block)

    def test_disabled_send_button_looks_disabled(self):
        source = self._app_source()
        button = source[:source.index("onClick={sendChangeReportNow}")]
        button = button[button.rfind("<button"):]
        self.assertIn("disabled:opacity-40", button)


if __name__ == "__main__":
    unittest.main()
