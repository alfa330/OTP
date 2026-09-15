# -*- coding: utf-8 -*-
"""«Уведомления об изменениях» — суточная сводка правок графика в Telegram.

Что здесь сторожится и почему именно это.

Требование владельца к этой сводке было сформулировано отдельной фразой:
«нельзя, чтобы это выглядело как спам». На боевых данных за сутки в журнале
79–585 строк, до 92 затронутых сотрудников и до 63 разных дней графика — то
есть наивная сводка «строка на каждого» не просто нечитаема, она физически не
уходит: потолок сообщения Telegram 4096 символов, и превышение означает не
обрезку, а несостоявшуюся отправку. Поэтому тесты держат четыре вещи:

* единицу счёта — «правка» = сотрудник + день графика + операция, а не строка
  журнала: одна загрузка файла пишет 369 строк, и по строкам автор выглядел бы
  как человек, поменявший график 369 раз;
* разделение массовых операций и ручных правок — иначе импорт вытесняет из
  сводки всех, кто правил смены руками;
* молчание операторских обменов в списке имён — за сутки их полтора десятка,
  и это чужие имена в сводке для руководителя;
* бюджет символов на заведомо огромных сутках.

Отдельно сторожится граница суток (`changed_at` лежит в алматинском времени без
пояса, а сессия Postgres на Render — в UTC) и область получателя: правило
раздела, а не правило остальных Telegram-отчётов портала. Разница не
академическая — на бою все шесть глав отделов имеют роль `admin`, и по правилу
отчётов каждый из них получал бы сводку по всей компании.

Тест герметичен: `database.py` и `bot_schedule2.py` не импортируются (на
последней строке `database.py` создаётся `Database()` с пулом к боевой БД).
Нужное достаётся через AST, чистый модуль сводки импортируется напрямую.
"""

import ast
import html
import re
import unittest
from datetime import date, datetime, timedelta
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


def entry(operator_id, operator_name, shift_date, action, source, actor_id,
          actor_name, actor_role, changed_at, **extra):
    row = {
        'operator_id': operator_id,
        'operator_name': operator_name,
        'shift_date': shift_date,
        'action': action,
        'source': source,
        'actor_id': actor_id,
        'actor_name': actor_name,
        'actor_role': actor_role,
        'changed_at': changed_at,
        'start': None, 'end': None, 'prev_start': None, 'prev_end': None,
    }
    row.update(extra)
    return row


DAY = date(2026, 9, 14)
T1 = datetime(2026, 9, 14, 10, 0, 0, 111111)
T2 = datetime(2026, 9, 14, 11, 0, 0, 222222)
T3 = datetime(2026, 9, 14, 12, 0, 0, 333333)


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


class ChangeUnitTests(unittest.TestCase):
    """Единица счёта. Правка — сотрудник + день графика + операция."""

    def test_rows_of_one_operation_on_one_day_are_one_change(self):
        # Реальный случай: смену подвинули и сняли выходной — журнал пишет две
        # строки об одном действии человека.
        entries = [
            entry(1, 'Иванов', date(2026, 9, 16), 'changed', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(1, 'Иванов', date(2026, 9, 16), 'day_off_cleared', 'supervisor', 7, 'Петров', 'sv', T1),
        ]
        stats = digest.summarize(entries)
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['actors'][0]['changes'], 1)
        self.assertEqual(stats['operators'][0]['changes'], 1)

    def test_same_day_touched_twice_counts_twice(self):
        # Два захода в тот же день того же человека — это два раза, а не один:
        # иначе «кому сколько раз меняли» перестаёт отвечать на свой вопрос.
        entries = [
            entry(1, 'Иванов', date(2026, 9, 16), 'changed', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(1, 'Иванов', date(2026, 9, 16), 'changed', 'supervisor', 7, 'Петров', 'sv', T2),
        ]
        self.assertEqual(digest.summarize(entries)['total'], 2)

    def test_same_microsecond_different_actors_are_different_operations(self):
        # Ключ операции — пара (автор, время), а не одно время.
        entries = [
            entry(1, 'Иванов', date(2026, 9, 16), 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(1, 'Иванов', date(2026, 9, 16), 'added', 'supervisor', 8, 'Сидоров', 'sv', T1),
        ]
        stats = digest.summarize(entries)
        self.assertEqual(stats['total'], 2)
        self.assertEqual(len(stats['actors']), 2)

    def test_counts_add_up(self):
        """Сумма по авторам плюс операторские операции равна шапке.

        Руководитель складывает числа в сообщении; если они не сходятся,
        сводке перестают верить целиком."""
        entries = [
            entry(1, 'Иванов', date(2026, 9, 16), 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(2, 'Сидорова', date(2026, 9, 17), 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(3, 'Кимов', date(2026, 9, 18), 'added', 'swap', 3, 'Кимов', 'operator', T2),
            entry(4, 'Ли', date(2026, 9, 19), 'added', 'auction_topup', 4, 'Ли', 'operator', T3),
        ]
        stats = digest.summarize(entries)
        by_actor = sum(row['changes'] for row in stats['actors'])
        self.assertEqual(by_actor + stats['self_total'], stats['total'])


class AntiSpamTests(unittest.TestCase):
    """Всё, что мешает сводке превратиться в простыню."""

    def _mass_import(self, operators=54, days=7):
        rows = []
        for operator_id in range(1, operators + 1):
            for offset in range(days):
                rows.append(entry(operator_id, 'Оператор %d' % operator_id,
                                  DAY + timedelta(days=offset), 'added', 'import',
                                  7, 'Кастек Гаухар', 'sv', T1))
        return rows

    def test_mass_operation_is_one_line_with_its_way_and_run_count(self):
        text = digest.build_digest(DAY, self._mass_import(), 'СЗоВ', escape=html.escape)
        self.assertIn('Кастек Гаухар', text)
        # Способ назван, и видно, что это ОДИН заход, а не 378 правок руками.
        self.assertIn('загрузка из файла (1 заход)', text)
        # Одна строка на автора, а не строка на каждого затронутого оператора.
        self.assertEqual(text.count('👤'), 1)

    def test_manual_work_is_not_drowned_by_a_mass_operation(self):
        rows = self._mass_import()
        rows += [
            entry(900, 'Петрова', DAY + timedelta(days=i), 'changed', 'supervisor',
                  8, 'Элекова Арайлым', 'sv', datetime(2026, 9, 14, 13, i, 0, i + 1))
            for i in range(12)
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        self.assertIn('Элекова Арайлым', text)
        self.assertIn('вручную (12 заходов)', text)

    def test_operator_self_service_has_no_names(self):
        """Обмены и доборы — счётчиком. Действующее лицо там механика, а не
        автор: ровно так же устроен экран истории (IMPERSONAL_SOURCES)."""
        rows = [
            entry(i, 'Оператор %d' % i, DAY, 'added', 'swap', i, 'Оператор %d' % i,
                  'operator', datetime(2026, 9, 14, 10, i, 0, i + 1))
            for i in range(1, 17)
        ]
        rows += [
            entry(100, 'Начальникова', DAY, 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(101, 'Начальникова2', DAY, 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(102, 'Начальникова3', DAY, 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(103, 'Начальникова4', DAY, 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(104, 'Начальникова5', DAY, 'added', 'supervisor', 7, 'Петров', 'sv', T1),
            entry(105, 'Начальникова6', DAY, 'added', 'supervisor', 7, 'Петров', 'sv', T1),
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        actors_block = text.split('<b>Кому меняли</b>')[0]
        self.assertIn('Петров', actors_block)
        for i in range(1, 17):
            self.assertNotIn('Оператор %d,' % i, actors_block)
        self.assertIn('обмен сменами — 16', text)

    def test_empty_day_says_so_and_stays_short(self):
        text = digest.build_digest(DAY, [], 'Тез КЦ', escape=html.escape)
        self.assertIn('никто не менял', text)
        self.assertNotIn('Кто менял', text)
        self.assertLess(len(text), 200)

    def test_few_changes_are_listed_instead_of_three_echoing_blocks(self):
        rows = [
            entry(1, 'Иванов', date(2026, 9, 16), 'changed', 'supervisor', 7, 'Петров', 'sv', T1,
                  start='10:00', end='19:00', prev_start='09:00', prev_end='18:00'),
            entry(2, 'Сидорова', date(2026, 9, 17), 'day_off_set', 'supervisor', 7, 'Петров', 'sv', T2),
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        self.assertNotIn('<b>Кто менял</b>', text)
        self.assertNotIn('<b>Кому меняли</b>', text)
        self.assertIn('Иванов', text)
        self.assertIn('09:00—18:00 → 10:00—19:00', text)

    def test_huge_day_still_fits_into_one_telegram_message(self):
        """Потолок 4096 — жёсткий: сообщение сверх него просто не уходит."""
        rows = []
        stamp = 0
        for operator_id in range(1, 121):
            for offset in range(-30, 33):
                stamp += 1
                rows.append(entry(
                    operator_id,
                    'Сотрудник с очень длинной фамилией номер %d' % operator_id,
                    DAY + timedelta(days=offset), 'changed', 'supervisor',
                    1000 + (operator_id % 30),
                    'Руководитель с длинным именем номер %d' % (operator_id % 30),
                    'sv', datetime(2026, 9, 14, 8, 0, 0, stamp)))
        text = digest.build_digest(DAY, rows, 'СЗоВ — Служба заботы о водителях',
                                   generated_label='15.09.2026 09:45', escape=html.escape)
        self.assertLess(len(text), 4096)
        # И при этом сводка осталась сводкой, а не обрубком.
        self.assertIn('<b>Кто менял</b>', text)
        self.assertIn('… и ещё', text)

    def test_long_lists_are_cut_with_an_honest_tail(self):
        rows = [
            entry(i, 'Оператор %d' % i, DAY, 'added', 'supervisor', 7, 'Петров', 'sv',
                  datetime(2026, 9, 14, 10, 0, 0, i))
            for i in range(1, 41)
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        operators_block = text.split('<b>Кому меняли</b>')[1]
        self.assertEqual(operators_block.count('\n• '), digest.OPERATORS_LIMIT)
        self.assertIn('… и ещё 28 сотрудников', operators_block)


class TextTests(unittest.TestCase):

    def test_values_are_escaped(self):
        rows = [
            entry(1, '<b>Хакер</b> & Co', DAY, 'added', 'supervisor', 7, 'a<script>', 'sv',
                  datetime(2026, 9, 14, 10, 0, 0, i))
            for i in range(1, 12)
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ & Ко', escape=html.escape)
        self.assertNotIn('<script>', text)
        self.assertNotIn('<b>Хакер', text)
        self.assertIn('&amp;', text)

    def test_header_separates_the_two_date_axes(self):
        """`changed_at` — когда правили, `shift_date` — что правили. Даты из
        октября под сентябрьским заголовком не должны читаться как ошибка."""
        rows = [
            entry(i, 'Оператор %d' % i, date(2026, 10, 5), 'added', 'supervisor', 7,
                  'Петров', 'sv', datetime(2026, 9, 14, 10, 0, 0, i))
            for i in range(1, 12)
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        self.assertIn('Изменения в графике за 14 сентября 2026', text)
        self.assertIn('Дни графика, которых коснулись правки', text)
        self.assertIn('05.10', text)

    def test_plural_forms(self):
        self.assertEqual(digest.changes_word(1), 'правка')
        self.assertEqual(digest.changes_word(573), 'правки')
        self.assertEqual(digest.changes_word(12), 'правок')
        self.assertEqual(digest.people_counted(1), 'сотрудник')
        self.assertEqual(digest.people_counted(73), 'сотрудника')
        self.assertEqual(digest.people_counted(15), 'сотрудников')
        self.assertEqual(digest.people_word(21), 'сотрудника')

    def test_unknown_source_still_names_the_author(self):
        """Набор источников пополняется без миграции (см. DDL журнала).
        Незнакомый код обязан попасть в сводку, а не исчезнуть из неё."""
        rows = [
            entry(i, 'Оператор %d' % i, DAY, 'added', 'что_то_новое', 7, 'Петров', 'sv',
                  datetime(2026, 9, 14, 10, 0, 0, i))
            for i in range(1, 12)
        ]
        stats = digest.summarize(rows)
        self.assertEqual(stats['self_total'], 0)
        self.assertEqual(stats['actors'][0]['name'], 'Петров')


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

    def test_empty_day_is_silent_unless_forced(self):
        source = _source_of(BOT_PATH, "_send_schedule_change_report_to")
        self.assertIn("if not entries and not force:", source)
        self.assertIn("'empty'", source)

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


class ReviewFixesTests(unittest.TestCase):
    """Дефекты, найденные разбором перед выкладкой. У каждого реальный
    сценарий, поэтому каждый держится своим тестом."""

    def test_detailed_list_is_one_bullet_per_change(self):
        # Выходной на день со сменой: журнал пишет две строки об одном действии,
        # а шапка считает одну правку — перечень обязан с ней совпасть.
        rows = [
            entry(1, 'Иванов', date(2026, 9, 16), 'removed', 'supervisor', 7, 'Петров', 'sv', T1,
                  prev_start='09:00', prev_end='18:00'),
            entry(1, 'Иванов', date(2026, 9, 16), 'day_off_set', 'supervisor', 7, 'Петров', 'sv', T1),
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        self.assertIn('Правок: <b>1</b>', text)
        self.assertEqual(text.count('\n• '), 1)
        self.assertIn('смена удалена 09:00—18:00, проставлен выходной', text)

    def test_split_shift_keeps_both_parts(self):
        rows = [
            entry(1, 'Иванов', date(2026, 9, 16), 'added', 'supervisor', 7, 'Петров', 'sv', T1,
                  start='09:00', end='13:00'),
            entry(1, 'Иванов', date(2026, 9, 16), 'added', 'supervisor', 7, 'Петров', 'sv', T1,
                  start='18:00', end='22:00'),
        ]
        text = digest.build_digest(DAY, rows, 'СЗоВ', escape=html.escape)
        self.assertIn('09:00—13:00', text)
        self.assertIn('18:00—22:00', text)

    def test_detailed_list_respects_the_budget(self):
        long_name = 'Очень-очень длинное имя сотрудника ' * 10
        rows = []
        for index in range(5):
            for action in ('removed', 'added', 'changed', 'day_off_set'):
                rows.append(entry(
                    index, long_name + str(index), date(2026, 9, 16), action, 'supervisor',
                    7, long_name, 'sv', datetime(2026, 9, 14, 10, 0, 0, index + 1),
                    start='09:00', end='18:00', prev_start='08:00', prev_end='17:00'))
        text = digest.build_digest(DAY, rows, 'СЗоВ', '15.09.2026 09:45', escape=html.escape)
        self.assertLess(len(text), 4096)
        self.assertIn('… и ещё', text)

    def test_claim_failure_does_not_stop_the_rest(self):
        source = _source_of(BOT_PATH, "sync_send_schedule_change_report")
        before_claim = source[:source.index("claim_schedule_change_report_send")]
        self.assertGreater(before_claim.rfind("try:"),
                           before_claim.rfind("for recipient in recipients:"))

    def test_toggle_takes_telegram_state_from_the_response(self):
        source = APP_PATH.read_text(encoding="utf-8-sig")
        toggle = source[source.index("const toggleChangeReport"):]
        toggle = toggle[:toggle.index("}, [changeReportSaving]);")]
        self.assertIn("data?.telegram_connected", toggle)

    def test_opening_the_window_rereads_the_state(self):
        source = APP_PATH.read_text(encoding="utf-8-sig")
        block = source[source.index("{changeReport.canSubscribe && ("):]
        block = block[:block.index("setShowChangeReportModal(true)")]
        self.assertIn("loadChangeReportState()", block)

    def test_disabled_send_button_looks_disabled(self):
        source = APP_PATH.read_text(encoding="utf-8-sig")
        button = source[:source.index("onClick={sendChangeReportNow}")]
        button = button[button.rfind("<button"):]
        self.assertIn("disabled:opacity-40", button)


if __name__ == "__main__":
    unittest.main()
