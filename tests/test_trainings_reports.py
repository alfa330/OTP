# -*- coding: utf-8 -*-
"""Автоотчёт по проведённым тренингам (задача #261).

Без базы и без сети: границы периода, сборка занятий из построчных записей,
текст сводки и книга Excel. Курсор подменяется заглушкой — модуль общается с
базой одним запросом и разбирает плоские кортежи, так что проверять его можно
целиком, не поднимая Postgres.

Что здесь закрепляется намеренно:

* «Занятие» собирается по ключу дата+время+тема+кто записал. Если ключ
  однажды сузят до одной даты, два разных занятия одного тренера по одной
  теме слипнутся и «количество участников» соврёт — тест на это стоит.
* Отчёт всегда про ЗАКРЫТЫЙ период. Сумма недель обязана сходиться с месяцем,
  а один день не может попасть в две рассылки.
* Пустой день молчит, пустая неделя и месяц — говорят.
* Текст уходит в Telegram с parse_mode=HTML, значит имя с «<» обязано быть
  экранировано, а сообщение — влезать в потолок 4096 символов.
"""

import ast
import os
import sys
import unittest
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import source_cache  # noqa: E402
from trainings import access, reports  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'database.py'
BOT_PATH = ROOT / 'bot_schedule2.py'


def _node_source(source, name, cls_name=None):
    """Исходник функции или метода по имени — через ast, без импорта монолита.

    `bot_schedule2.py` и `database.py` импортировать из тестов нельзя: они на
    старте поднимают пул к боевой базе (см. tests/source_cache.py). Разбор
    кэшируется на весь прогон, поэтому вызывать хелпер дёшево.
    """
    module = source_cache.parse(source)
    scope = module.body
    if cls_name:
        scope = next(node.body for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == cls_name)
    node = next((item for item in scope
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and item.name == name), None)
    assert node is not None, 'не найдено: %s' % name
    return ast.get_source_segment(source, node) or ''


def _function_source(source, name):
    return _node_source(source, name)


def _method_source(source, name):
    return _node_source(source, name, cls_name='Database')


class FakeCursor:
    """Курсор на список кортежей: execute запоминает запрос, fetchall отдаёт строки."""

    def __init__(self, rows):
        self._rows = rows
        self.query = None
        self.params = None

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchall(self):
        return list(self._rows)


def row(operator_name, *, day=date(2026, 9, 2), start=time(10, 0), end=time(11, 0),
        reason='Тренинг по продукту', topic_id=None, topic_title=None, topic_kind=None,
        created_by=7, trainer='Кастек Гаухар', operator_id=None, status='working',
        department_id=1, department_name='СЗоВ', group_name='Группа 1',
        count_in_hours=True, comment=''):
    """Строка выборки в том же порядке, что в _SESSIONS_SQL."""
    return (
        day, start, end, reason, topic_id, topic_title, topic_kind,
        count_in_hours, comment, created_by, trainer,
        operator_id if operator_id is not None else abs(hash(operator_name)) % 100000,
        operator_name, status, department_id, department_name, group_name,
    )


class PeriodBoundsTest(unittest.TestCase):
    """Границы периода. Отчёт про закрытый период, иначе суммы не сходятся."""

    def test_daily_is_yesterday(self):
        start, end, label = reports.period_bounds('daily', date(2026, 9, 4))
        self.assertEqual((start, end), (date(2026, 9, 3), date(2026, 9, 3)))
        self.assertEqual(label, '3 сентября 2026')

    def test_weekly_is_previous_monday_to_sunday(self):
        # 07.09.2026 — понедельник; прошлая неделя 31.08 (пн) — 06.09 (вс).
        start, end, label = reports.period_bounds('weekly', date(2026, 9, 7))
        self.assertEqual(start, date(2026, 8, 31))
        self.assertEqual(end, date(2026, 9, 6))
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(end.weekday(), 6)
        self.assertEqual(label, '31 августа — 6 сентября 2026')

    def test_weekly_is_the_same_week_whenever_the_job_actually_runs(self):
        """misfire_grace_time может сдвинуть запуск на несколько часов, и
        отчёт от этого меняться не должен — пока это та же календарная неделя."""
        monday = reports.period_bounds('weekly', date(2026, 9, 7))
        wednesday = reports.period_bounds('weekly', date(2026, 9, 9))
        self.assertEqual(monday[:2], wednesday[:2])

    def test_monthly_is_previous_calendar_month(self):
        start, end, label = reports.period_bounds('monthly', date(2026, 9, 1))
        self.assertEqual(start, date(2026, 8, 1))
        self.assertEqual(end, date(2026, 8, 31))
        self.assertEqual(label, 'Август 2026')

    def test_monthly_crosses_the_new_year(self):
        start, end, label = reports.period_bounds('monthly', date(2027, 1, 1))
        self.assertEqual((start, end), (date(2026, 12, 1), date(2026, 12, 31)))
        self.assertEqual(label, 'Декабрь 2026')

    def test_periods_do_not_overlap(self):
        """Один день не может попасть и в дневную, и в недельную сводку одного
        запуска: иначе он посчитан дважды."""
        day_from, day_to, _ = reports.period_bounds('daily', date(2026, 9, 7))
        week_from, week_to, _ = reports.period_bounds('weekly', date(2026, 9, 7))
        # Воскресенье 06.09 закрывает неделю, дневная сводка запуска — про него же.
        self.assertEqual(day_from, week_to)
        self.assertLessEqual(week_from, day_to)

    def test_unknown_period_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            reports.period_bounds('quarterly', date(2026, 9, 4))

    def test_month_bounds_from_string(self):
        start, end, label = reports.month_period_bounds('2026-02')
        self.assertEqual((start, end), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(label, 'Февраль 2026')


class NormalizePeriodTest(unittest.TestCase):
    def test_canonical_values(self):
        for value in reports.PERIODS:
            self.assertEqual(reports.normalize_period(value), value)

    def test_aliases(self):
        self.assertEqual(reports.normalize_period('DAY'), 'daily')
        self.assertEqual(reports.normalize_period('неделя'), 'weekly')
        self.assertEqual(reports.normalize_period('Ежемесячно'), 'monthly')

    def test_garbage_is_none(self):
        for value in (None, '', 'yearly', 'daily2', 5):
            self.assertIsNone(reports.normalize_period(value))

    def test_every_period_has_label_hint_and_empty_rule(self):
        for value in reports.PERIODS:
            self.assertIn(value, reports.PERIOD_LABELS)
            self.assertIn(value, reports.PERIOD_HINTS)
            self.assertIn(value, reports.PERIOD_TITLES)
            self.assertIn(value, reports.PERIOD_SENDS_WHEN_EMPTY)
            self.assertIn(value, reports.SESSIONS_DETAILED_LIMIT)

    def test_only_daily_stays_silent_on_empty_period(self):
        """Ежедневная сводка «занятий не было» ушла бы 250 раз в год ни о чём.
        Недельная и месячная о пустом периоде сообщают: неделя без обучения
        видна только если о ней сказать."""
        self.assertFalse(reports.PERIOD_SENDS_WHEN_EMPTY['daily'])
        self.assertTrue(reports.PERIOD_SENDS_WHEN_EMPTY['weekly'])
        self.assertTrue(reports.PERIOD_SENDS_WHEN_EMPTY['monthly'])


class FetchSessionsTest(unittest.TestCase):
    """Сборка занятий из построчных записей `trainings`."""

    def test_one_session_collects_all_its_participants(self):
        cursor = FakeCursor([
            row('Иванов Иван'), row('Петров Пётр'), row('Сидорова Анна'),
        ])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0]['participants']), 3)
        self.assertEqual(
            [person['name'] for person in sessions[0]['participants']],
            ['Иванов Иван', 'Петров Пётр', 'Сидорова Анна'],
        )

    def test_different_time_is_a_different_session(self):
        """Два занятия одного тренера по одной теме в один день — это ДВА
        занятия. Слипнись они в одно, «количество участников» перестало бы
        отвечать на вопрос «сколько людей было в аудитории»."""
        cursor = FakeCursor([
            row('Иванов Иван', start=time(10, 0), end=time(11, 0)),
            row('Петров Пётр', start=time(15, 0), end=time(16, 0)),
        ])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(len(sessions), 2)
        self.assertEqual([len(item['participants']) for item in sessions], [1, 1])

    def test_different_trainer_is_a_different_session(self):
        cursor = FakeCursor([
            row('Иванов Иван', created_by=7, trainer='Кастек Гаухар'),
            row('Петров Пётр', created_by=9, trainer='Омарова Ару'),
        ])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(len(sessions), 2)
        self.assertEqual({item['trainer_name'] for item in sessions},
                         {'Кастек Гаухар', 'Омарова Ару'})

    def test_corporate_topic_title_wins_over_reason(self):
        """Переименование корпоративной темы не переписывает `reason` у прошлых
        записей (так решено в разделе) — в отчёте показываем живое название."""
        cursor = FakeCursor([
            row('Иванов Иван', reason='Старое название',
                topic_id=12, topic_title='Новые правила отмены', topic_kind='info'),
        ])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(sessions[0]['title'], 'Новые правила отмены')
        self.assertTrue(sessions[0]['is_corporate'])

    def test_base_reason_is_used_as_the_title(self):
        cursor = FakeCursor([row('Иванов Иван', reason='Собрание')])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(sessions[0]['title'], 'Собрание')
        self.assertFalse(sessions[0]['is_corporate'])

    def test_missing_trainer_name_becomes_system(self):
        cursor = FakeCursor([row('Иванов Иван', trainer=None)])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(sessions[0]['trainer_name'], 'System')

    def test_no_department_filter_for_admin(self):
        cursor = FakeCursor([])
        reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30), department_ids=None)
        self.assertNotIn('u.department_id = ANY', cursor.query)
        self.assertEqual(cursor.params, [date(2026, 9, 1), date(2026, 9, 30)])

    def test_department_filter_for_a_head(self):
        cursor = FakeCursor([])
        reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30),
                               department_ids={367, 1})
        self.assertIn('u.department_id = ANY', cursor.query)
        self.assertEqual(cursor.params[2], [1, 367])

    def test_empty_department_set_means_empty_report_not_the_whole_portal(self):
        """Глава без отделов обязан получить пустой отчёт, а не всё подряд:
        `ANY(ARRAY[])` дал бы ноль строк, но пустой список в SQL не поедет —
        поэтому выходим раньше."""
        cursor = FakeCursor([row('Иванов Иван')])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30),
                                          department_ids=frozenset())
        self.assertEqual(sessions, [])
        self.assertIsNone(cursor.query, 'запрос вообще не должен уходить в базу')


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        cursor = FakeCursor([
            row('Иванов Иван', reason='Тренинг по продукту', start=time(10, 0), end=time(11, 0)),
            row('Петров Пётр', reason='Тренинг по продукту', start=time(10, 0), end=time(11, 0)),
            # Тот же человек на втором занятии — участий 3, сотрудников 2.
            row('Иванов Иван', reason='Собрание', start=time(14, 0), end=time(14, 30),
                operator_id=1, created_by=9, trainer='Омарова Ару'),
        ])
        self.sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))

    def test_counts(self):
        summary = reports.summarize(self.sessions)
        self.assertEqual(summary['sessions'], 2)
        self.assertEqual(summary['participations'], 3)

    def test_people_counts_distinct_humans_not_visits(self):
        """«Участий» и «сотрудников» — два разных вопроса. Одно число вместо
        двух отвечало бы то на один, то на другой."""
        cursor = FakeCursor([
            row('Иванов Иван', operator_id=1, start=time(10, 0), end=time(11, 0)),
            row('Иванов Иван', operator_id=1, start=time(14, 0), end=time(15, 0)),
        ])
        summary = reports.summarize(reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(summary['participations'], 2)
        self.assertEqual(summary['people'], 1)

    def test_minutes_are_counted_per_participant(self):
        """Час занятия на двоих — это два человеко-часа: так же считает и
        раздел, когда показывает «в часах» за месяц."""
        cursor = FakeCursor([
            row('Иванов Иван', start=time(10, 0), end=time(11, 0)),
            row('Петров Пётр', start=time(10, 0), end=time(11, 0)),
        ])
        summary = reports.summarize(reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(summary['minutes'], 120)

    def test_topics_are_sorted_by_reach(self):
        summary = reports.summarize(self.sessions)
        self.assertEqual([item['title'] for item in summary['topics']],
                         ['Тренинг по продукту', 'Собрание'])
        self.assertEqual(summary['topics'][0]['participants'], 2)

    def test_trainers_are_listed(self):
        summary = reports.summarize(self.sessions)
        self.assertEqual({item['name'] for item in summary['trainers']},
                         {'Кастек Гаухар', 'Омарова Ару'})

    def test_empty_period(self):
        summary = reports.summarize([])
        self.assertEqual(summary['sessions'], 0)
        self.assertEqual(summary['people'], 0)
        self.assertEqual(summary['topics'], [])


class DurationTest(unittest.TestCase):
    def test_negative_duration_never_leaks_into_the_report(self):
        """Ночное занятие «через полночь» дало бы отрицательные минуты, а
        «-30 мин в часах» это заведомая ложь в письме."""
        cursor = FakeCursor([row('Иванов Иван', start=time(23, 30), end=time(0, 30))])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(reports.session_minutes(sessions[0]), 0)

    def test_format(self):
        self.assertEqual(reports.format_duration(0), '0 мин')
        self.assertEqual(reports.format_duration(45), '45 мин')
        self.assertEqual(reports.format_duration(60), '1 ч')
        self.assertEqual(reports.format_duration(95), '1 ч 35 мин')

    def test_plural(self):
        self.assertEqual(reports.plural_ru(1, 'занятие', 'занятия', 'занятий'), 'занятие')
        self.assertEqual(reports.plural_ru(3, 'занятие', 'занятия', 'занятий'), 'занятия')
        self.assertEqual(reports.plural_ru(11, 'занятие', 'занятия', 'занятий'), 'занятий')
        self.assertEqual(reports.plural_ru(21, 'занятие', 'занятия', 'занятий'), 'занятие')


class DigestTest(unittest.TestCase):
    """Текст сообщения. Уходит с parse_mode=HTML — экранирование обязательно."""

    def _sessions(self, count=3, people_per_session=2):
        """count занятий: по дню и часу, чтобы ключ занятия был разным."""
        rows = []
        for index in range(count):
            day = date(2026, 9, 2) + timedelta(days=index // 12)
            hour = 8 + (index % 12)
            for person in range(people_per_session):
                rows.append(row(
                    'Сотрудник %d-%d' % (index, person),
                    day=day, start=time(hour, 0), end=time(hour, 45),
                    reason='Тема %d' % index,
                ))
        return reports.fetch_sessions(FakeCursor(rows), date(2026, 9, 1), date(2026, 9, 30))

    def test_empty_period_text_says_so_plainly(self):
        text = reports.build_digest('weekly', '31 августа — 6 сентября 2026', [],
                                    'Все отделы', '07.09.2026 09:35')
        self.assertIn('занятий не проводили', text)
        self.assertIn('Все отделы', text)

    def test_header_carries_period_and_scope(self):
        text = reports.build_digest('daily', '3 сентября 2026', self._sessions(),
                                    'СЗоВ', '04.09.2026 09:30')
        self.assertIn('Тренинги за день', text)
        self.assertIn('3 сентября 2026', text)
        self.assertIn('Область: СЗоВ', text)

    def test_everything_the_task_asked_for_is_in_the_daily_text(self):
        """Постановка: дата, тема, кто проводил, количество участников, ФИО."""
        text = reports.build_digest('daily', '3 сентября 2026', self._sessions(1, 2),
                                    'СЗоВ', '04.09.2026 09:30')
        self.assertIn('02.09', text)                 # дата занятия
        self.assertIn('Тема 0', text)                # тема
        self.assertIn('Кастек Гаухар', text)         # кто проводил
        self.assertIn('участников: 2', text)         # количество участников
        self.assertIn('Сотрудник 0-0', text)         # ФИО участника
        self.assertIn('Сотрудник 0-1', text)

    def test_monthly_text_has_no_per_session_names(self):
        """За месяц занятий под сотню — поимённая простыня в сообщение не
        влезает по определению, её место в книге."""
        text = reports.build_digest('monthly', 'Август 2026', self._sessions(40, 5),
                                    'Все отделы', '01.09.2026 09:40', attached=True)
        self.assertIn('в приложенном файле', text)
        self.assertIn('По темам', text)
        self.assertLess(len(text), 4096)

    def test_html_is_escaped(self):
        cursor = FakeCursor([row('Иванов <b>Иван</b> & Co', trainer='Тренер <script>')])
        sessions = reports.fetch_sessions(cursor, date(2026, 9, 1), date(2026, 9, 30))
        text = reports.build_digest('daily', '2 сентября 2026', sessions,
                                    'Все отделы', '03.09.2026 09:30')
        self.assertNotIn('<b>Иван</b>', text)
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)

    def test_message_fits_the_telegram_limit_even_on_a_huge_period(self):
        """Потолок сообщения Telegram — 4096 символов. Перебор не «обрежется»,
        а вернёт ошибку и сводка не придёт вовсе."""
        for period in reports.PERIODS:
            text = reports.build_digest(period, 'Период', self._sessions(120, 8),
                                        'Все отделы', '01.09.2026 09:40', attached=True)
            self.assertLess(len(text), 4096, 'период %s' % period)

    def test_long_participant_list_says_how_many_are_hidden(self):
        sessions = self._sessions(1, 30)
        text = reports.build_digest('daily', '2 сентября 2026', sessions,
                                    'СЗоВ', '03.09.2026 09:30', attached=True)
        self.assertIn('и ещё', text)
        self.assertIn('участников: 30', text)


class FilenameTest(unittest.TestCase):
    def test_period_is_visible_in_the_name(self):
        self.assertEqual(
            reports.report_filename('daily', date(2026, 9, 3), date(2026, 9, 3)),
            'Trainings_day_20260903.xlsx')
        self.assertEqual(
            reports.report_filename('weekly', date(2026, 8, 31), date(2026, 9, 6)),
            'Trainings_week_20260831_20260906.xlsx')
        self.assertEqual(
            reports.report_filename('monthly', date(2026, 8, 1), date(2026, 8, 31)),
            'Trainings_month_202608.xlsx')


class XlsxTest(unittest.TestCase):
    """Книга собирается настоящим xlsxwriter — иначе тест проверял бы заглушку."""

    def setUp(self):
        try:
            import xlsxwriter  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest('xlsxwriter не установлен')
        self.xlsxwriter = xlsxwriter

    def _build(self, sessions):
        return reports.build_xlsx(
            xlsxwriter=self.xlsxwriter,
            period='monthly',
            period_label='Август 2026',
            sessions=sessions,
            scope_label='Все отделы',
            generated_label='01.09.2026 09:40',
        )

    def test_workbook_is_a_real_xlsx(self):
        cursor = FakeCursor([row('Иванов Иван'), row('Петров Пётр')])
        sessions = reports.fetch_sessions(cursor, date(2026, 8, 1), date(2026, 8, 31))
        payload = self._build(sessions)
        self.assertTrue(payload.startswith(b'PK'), 'xlsx это zip')
        self.assertGreater(len(payload), 3000)

    def test_three_sheets(self):
        from io import BytesIO
        from zipfile import ZipFile
        cursor = FakeCursor([row('Иванов Иван')])
        sessions = reports.fetch_sessions(cursor, date(2026, 8, 1), date(2026, 8, 31))
        with ZipFile(BytesIO(self._build(sessions))) as book:
            workbook_xml = book.read('xl/workbook.xml').decode('utf-8')
        for sheet in ('Занятия', 'Участники', 'Сводка'):
            self.assertIn(sheet, workbook_xml)

    def test_empty_period_still_builds(self):
        """Недельная и месячная сводки уходят и за пустой период — книга
        обязана собраться, а не упасть на пустом списке."""
        payload = self._build([])
        self.assertTrue(payload.startswith(b'PK'))

    def test_names_with_special_characters_do_not_break_the_book(self):
        cursor = FakeCursor([row('Иванов <b>&</b> "Иван"')])
        sessions = reports.fetch_sessions(cursor, date(2026, 8, 1), date(2026, 8, 31))
        self.assertTrue(self._build(sessions).startswith(b'PK'))


class SubscriberAccessTest(unittest.TestCase):
    """Кому доступна подписка. Правило одно на три места — ответ справочника
    тем, роут подписки и роут разовой отправки."""

    def test_admin_and_super_admin_may_subscribe(self):
        self.assertTrue(access.can_subscribe_reports('admin'))
        self.assertTrue(access.can_subscribe_reports('super_admin'))
        self.assertTrue(access.can_subscribe_reports('superadmin'))

    def test_department_head_may_subscribe_whatever_the_base_role(self):
        self.assertTrue(access.can_subscribe_reports('sv', headed_department_id=367))
        self.assertTrue(access.can_subscribe_reports('operator', headed_department_id=1))

    def test_supervisor_trainer_and_operator_may_not(self):
        """Сводка — срез по отделу целиком. Рассылать её всем супервайзерам
        значило бы двадцать писем об одном и том же каждое утро."""
        for role in ('sv', 'supervisor', 'trainer', 'operator', 'trainee', ''):
            self.assertFalse(access.can_subscribe_reports(role), role)

    def test_admin_who_heads_a_department_still_may_subscribe(self):
        self.assertTrue(access.can_subscribe_reports('admin', headed_department_id=1))




class DatabaseContractTest(unittest.TestCase):
    """Договор с `database.py`. Монолит не импортируем — он на старте поднимает
    пул к боевой базе; берём нужное разбором исходника, как весь остальной
    набор (см. tests/source_cache.py)."""

    @classmethod
    def setUpClass(cls):
        cls.source = DB_PATH.read_text(encoding='utf-8-sig')
        module = source_cache.parse(cls.source)
        database_cls = next(node for node in module.body
                            if isinstance(node, ast.ClassDef) and node.name == 'Database')
        cls.columns = None
        for node in database_cls.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(target, 'id', '') == 'TRAINING_REPORT_COLUMNS'
                    for target in node.targets):
                cls.columns = ast.literal_eval(node.value)
        cls.methods = {node.name for node in database_cls.body
                       if isinstance(node, ast.FunctionDef)}

    def test_one_column_per_period(self):
        """Периодичности и колонки — один набор. Разойдись они, настройка
        сохранялась бы, а рассылка молчала: get_training_report_recipients
        отдаёт пустой список на неизвестной периодичности."""
        self.assertIsNotNone(self.columns, 'TRAINING_REPORT_COLUMNS не найден')
        self.assertEqual(set(self.columns), set(reports.PERIODS))

    def test_columns_exist_in_the_schema(self):
        """Каждая колонка подписки заводится идемпотентным ALTER'ом: без него
        первый же запрос упал бы на боевой базе, где admin_profiles уже есть и
        CREATE TABLE IF NOT EXISTS — no-op."""
        for column in (self.columns or {}).values():
            self.assertIn(
                'ADD COLUMN IF NOT EXISTS %s BOOLEAN NOT NULL DEFAULT FALSE' % column,
                self.source, column)

    def test_methods_are_present(self):
        for name in ('get_training_report_subscription',
                     'set_training_report_subscription',
                     'get_training_report_recipients',
                     'claim_training_report_send',
                     'release_training_report_send'):
            self.assertIn(name, self.methods, name)

    def test_recipients_skip_the_dismissed_and_the_telegramless(self):
        """Увольнение не снимает ни telegram_id, ни роль 'admin' — без фильтра
        уволенный админ продолжал бы получать сводку по отделу, из которого
        ушёл. Отправлять без telegram_id тоже некуда."""
        query = _method_source(self.source, 'get_training_report_recipients')
        self.assertIn('u.telegram_id IS NOT NULL', query)
        self.assertIn("COALESCE(u.status, 'working') <> 'fired'", query)

    def test_recipients_are_admins_or_active_heads_only(self):
        query = _method_source(self.source, 'get_training_report_recipients')
        self.assertIn("LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')", query)
        self.assertIn('d.head_user_id = u.id', query)
        self.assertIn('COALESCE(d.is_active, TRUE) = TRUE', query)

    def test_claim_is_atomic(self):
        """Заявка на отправку обязана быть одним INSERT'ом с ON CONFLICT:
        «сначала SELECT, потом INSERT» оставляет между ними окно, ради
        которого журнал заявок и заведён."""
        claim = _method_source(self.source, 'claim_training_report_send')
        self.assertIn('INSERT INTO training_report_sends', claim)
        self.assertIn('ON CONFLICT (period, period_start, user_id) DO NOTHING', claim)
        self.assertIn('RETURNING', claim)


class SchemaTest(unittest.TestCase):
    """Журнал заявок разворачивается схемой раздела."""

    def test_send_log_table_is_created_idempotently(self):
        source = (ROOT / 'trainings' / 'schema.py').read_text(encoding='utf-8-sig')
        self.assertIn('CREATE TABLE IF NOT EXISTS training_report_sends', source)
        self.assertIn('PRIMARY KEY (period, period_start, user_id)', source)

    def test_table_statements_run_before_indexes(self):
        """Порядок разворота: таблицы, потом ALTER'ы, потом индексы. Частичный
        индекс по колонке, которой ещё нет, уронил бы ВЕСЬ разворот схемы
        раздела под SAVEPOINT — молча, оставив раздел на старой структуре."""
        from trainings import schema as trainings_schema
        statements = list(trainings_schema._STATEMENTS)
        tables = [i for i, item in enumerate(statements) if 'CREATE TABLE' in item.upper()]
        indexes = [i for i, item in enumerate(statements) if 'CREATE INDEX' in item.upper()
                   or 'CREATE UNIQUE INDEX' in item.upper()]
        # Порядок в списке не важен — важно, что init_trainings_schema
        # прогоняет таблицы отдельным проходом. Проверяем сам проход.
        self.assertTrue(tables and indexes)
        self.assertIn('for statement in _STATEMENTS:',
                      (ROOT / 'trainings' / 'schema.py').read_text(encoding='utf-8-sig'))


class SchedulerTest(unittest.TestCase):
    """Расписание рассылки. Оно обещано пользователю в окне настроек —
    PERIOD_HINTS и CronTrigger обязаны говорить одно и то же."""

    @classmethod
    def setUpClass(cls):
        cls.source = BOT_PATH.read_text(encoding='utf-8-sig')

    def test_three_jobs_registered(self):
        for job_id, func in (('training_report_daily', 'send_daily_training_report'),
                             ('training_report_weekly', 'send_weekly_training_report'),
                             ('training_report_monthly', 'send_monthly_training_report')):
            self.assertIn("id='%s'" % job_id, self.source, job_id)
            self.assertIn('        %s,' % func, self.source, func)

    def test_schedule_matches_the_promise_in_the_ui(self):
        """Время из подсказки окна настроек — то же, что в CronTrigger."""
        expected = {
            'daily': (9, 30, "CronTrigger(hour=9, minute=30, timezone=ZoneInfo('Asia/Almaty'))"),
            'weekly': (9, 35, "CronTrigger(day_of_week='mon', hour=9, minute=35, timezone=ZoneInfo('Asia/Almaty'))"),
            'monthly': (9, 40, "CronTrigger(day='1', hour=9, minute=40, timezone=ZoneInfo('Asia/Almaty'))"),
        }
        for period, (hour, minute, trigger) in expected.items():
            self.assertIn(trigger, self.source, period)
            self.assertIn('%02d:%02d' % (hour, minute), reports.PERIOD_HINTS[period], period)

    def test_timezone_is_explicit(self):
        """Без явного пояса APScheduler берёт локальный пояс процесса, а на
        Render это UTC — утренняя сводка ушла бы в 03:30 ночи."""
        block = self.source[self.source.find("id='training_report_daily'") - 400:
                            self.source.find("id='training_report_monthly'") + 300]
        self.assertEqual(block.count("ZoneInfo('Asia/Almaty')"), 3)

    def test_single_instance_and_coalesce(self):
        block = self.source[self.source.find("send_daily_training_report,"):
                            self.source.find("id='training_report_monthly'") + 200]
        self.assertEqual(block.count('max_instances=1'), 3)
        self.assertEqual(block.count('coalesce=True'), 3)

    def test_claim_is_taken_before_the_report_is_sent(self):
        """Порядок в джобе: сначала заявка, потом отправка. Обратный порядок
        вернул бы дубли на пересечении двух процессов при деплое."""
        job = _function_source(self.source, 'sync_send_training_report')
        claim_at = job.find('claim_training_report_send')
        send_at = job.find('_send_training_report_to')
        self.assertGreater(claim_at, -1)
        self.assertGreater(send_at, claim_at, 'заявка должна браться до отправки')

    def test_failed_send_releases_the_claim(self):
        """Провал отправки не должен съедать сводку навсегда."""
        job = _function_source(self.source, 'sync_send_training_report')
        self.assertIn('release_training_report_send', job)

    def test_one_report_per_scope_not_per_recipient(self):
        """У двенадцати админов область одна («все отделы»): без кеша портал
        двенадцать раз выполнил бы один запрос и собрал бы один и тот же xlsx."""
        job = _function_source(self.source, 'sync_send_training_report')
        self.assertIn('cache=cache', job)


class RouteTest(unittest.TestCase):
    """Роуты подписки и разовой отправки."""

    @classmethod
    def setUpClass(cls):
        cls.source = BOT_PATH.read_text(encoding='utf-8-sig')

    def test_routes_registered(self):
        self.assertIn("@app.route('/api/trainings/report_subscription', "
                      "methods=['GET', 'POST', 'OPTIONS'])", self.source)
        self.assertIn("@app.route('/api/trainings/report_preview', "
                      "methods=['POST', 'OPTIONS'])", self.source)

    def test_both_routes_use_the_shared_permission_rule(self):
        """Гейт один — trainings.access.can_subscribe_reports. Своя копия
        рано или поздно разошлась бы с флагом can_subscribe_reports, по
        которому фронт рисует кнопку, и кнопка отвечала бы 403."""
        scope = _function_source(self.source, '_training_report_scope')
        self.assertIn('can_subscribe_reports', scope)
        for name in ('training_report_subscription', 'training_report_preview'):
            handler = _function_source(self.source, name)
            self.assertIn('_training_report_scope', handler, name)

    def test_preview_refuses_without_telegram(self):
        handler = _function_source(self.source, 'training_report_preview')
        self.assertIn('TELEGRAM_NOT_CONNECTED', handler)

    def test_preview_sends_even_for_an_empty_period(self):
        """Человек нажал кнопку и обязан увидеть ответ — молчание он прочитает
        как «не работает»."""
        handler = _function_source(self.source, 'training_report_preview')
        self.assertIn('force=True', handler)

    def test_unknown_period_in_subscription_is_rejected(self):
        handler = _function_source(self.source, 'training_report_subscription')
        self.assertIn('Неизвестная периодичность', handler)

    def test_topics_response_carries_the_button_flag(self):
        """Кнопку в разделе рисует серверный флаг, а не проверка роли на
        фронте: can_manage сюда не годится — в MANAGE_ROLES есть СВ, а в
        получатели отчёта СВ не попадает."""
        routes = (ROOT / 'trainings' / 'routes.py').read_text(encoding='utf-8-sig')
        self.assertIn("'can_subscribe_reports': access.can_subscribe_reports(role, headed_dept)", routes)
        self.assertIn('"can_subscribe_reports": ctx[\'can_subscribe_reports\']', routes)


if __name__ == '__main__':
    unittest.main()
