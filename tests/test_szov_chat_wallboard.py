"""Табло СЗоВ, направление «Чат»: статусы чатников, почасовой срез и кэш снимка.

Функции бэкенда вытаскиваем из bot_schedule2.py тем же загрузчиком, что и тесты «Основы»
(`_load_names`): проверяется настоящая логика, а не строковое совпадение.
"""
import logging
import math
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.test_szov_wallboard import _FakeDb, _load_names

ROOT = Path(__file__).resolve().parents[1]

# Имена вымышленные, но в тех же двух формах, что расходятся в жизни: Chat2Desk отдаёт
# «Имя Фамилия», в OTP человек записан как «Фамилия Имя Отчество».
CHAT_MANAGERS = {
    'Алия Тестова': {'id': 235, 'name': 'Тестова Алия Тестовна',
                     'calculation_model_code': 'chat_manager', 'direction_name': 'Чат менеджер'},
    'Бекзат Примеров': {'id': 149, 'name': 'Примеров Бекзат Примерулы',
                        'calculation_model_code': 'chat_manager', 'direction_name': 'Чат менеджер'},
    'Дана Ночная': {'id': 18, 'name': 'Ночная Дана Сменовна',
                    'calculation_model_code': 'chat_manager', 'direction_name': 'Чат менеджер'},
    'Ерлан Учебный': {'id': 37, 'name': 'Учебный Ерлан Тренингулы',
                      'calculation_model_code': 'chat_manager', 'direction_name': 'Чат менеджер'},
}

NAMES = {
    'SZOV_WALLBOARD_DEPARTMENT_CODE',
    'SZOV_CHAT_WALLBOARD_TARGET_SECONDS',
    'SZOV_CHAT_WALLBOARD_CACHE_TTL_SECONDS',
    'SZOV_CHAT_WALLBOARD_REQUESTS_TTL_SECONDS',
    'SZOV_CHAT_WALLBOARD_STALE_MAX_SECONDS',
    'SZOV_CHAT_WALLBOARD_RETRY_AFTER_FAIL_SECONDS',
    'SZOV_CHAT_WALLBOARD_LOCK_WAIT_SECONDS',
    'SZOV_CHAT_WALLBOARD_EVENT_MAX_PAGES',
    '_SZOV_CHAT_WALLBOARD_STATUSES',
    '_SZOV_CHAT_WALLBOARD_OTHER_KEY',
    '_SZOV_CHAT_WALLBOARD_OFFLINE',
    '_SZOV_CHAT_WALLBOARD_STATUS_ORDER',
    '_SZOV_CHAT_WALLBOARD_STATUS_RANK',
    '_SZOV_WALLBOARD_DEPARTMENT_CACHE',
    '_SZOV_WALLBOARD_DEPARTMENT_CACHE_TTL',
    '_szov_wallboard_department_id',
    '_szov_chat_wallboard_events_cache',
    '_szov_chat_wallboard_requests_cache',
    '_szov_chat_wallboard_cache',
    '_szov_chat_wallboard_lock',
    '_operator_info_is_chat_manager',
    '_szov_chat_wallboard_status',
    '_szov_chat_wallboard_operator_lookup',
    '_szov_chat_wallboard_resolve',
    '_szov_chat_wallboard_day_seconds',
    '_szov_chat_wallboard_timelines',
    '_szov_chat_wallboard_online_seconds',
    '_szov_chat_wallboard_required',
    '_szov_chat_wallboard_hourly',
    '_szov_chat_wallboard_now',
    '_szov_chat_wallboard_fetch_events',
    '_szov_chat_wallboard_fetch_snapshot',
    '_wallboard_snapshot_with_cache',
    '_szov_chat_wallboard_snapshot',
}


def _event(name, event, created_at, role='Супервайзер', operator_id=1):
    return {'operator_id': operator_id, 'operator_name': name, 'operator_role': role,
            'event': event, 'dialog_id': '', 'created_at': created_at}


def _operator_row(name, online=1, offline_type=None, dialogs=0, status='enabled'):
    first, last = name.split(' ', 1)
    return {'first_name': first, 'last_name': last, 'online': online, 'status': status,
            'offline_type': offline_type, 'opened_dialogs': dialogs}


def _request(start, reaction, replies, total, operator='Алия Тестова', end='x'):
    # average_replies_time = total_replies_time / replies — так его отдаёт Chat2Desk.
    return {'request_start': start, 'request_end': end, 'request_type': 'common',
            'operator_name': operator, 'reaction_time': reaction,
            'replies': replies, 'total_replies_time': total,
            'average_replies_time': (total / replies) if (total is not None and replies) else ''}


class _Harness:
    """Namespace с настоящими функциями табло и поддельными Chat2Desk/БД."""

    def _namespace(self, *, members=None, operators=None, requests_get=None):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        matched = operators if operators is not None else CHAT_MANAGERS

        class _FakeRequests:
            def __init__(self, handler):
                self.handler = handler
                self.calls = []

            def get(self, url, headers=None, timeout=None, params=None):
                self.calls.append(params or {})
                return self.handler(params or {})

        fake_requests = _FakeRequests(requests_get or (lambda params: None))
        ns = {
            'time': time,
            'math': math,
            'logging': logging,
            'threading': threading,
            'datetime': datetime,
            'ZoneInfo': ZoneInfo,
            'requests': fake_requests,
            're': __import__('re'),
            '_env_int': lambda name, default, minimum=None, maximum=None: default,
            'CHAT2DESK_API_PAGE_LIMIT': 200,
            'CHAT2DESK_API_TIMEOUT_SECONDS': 45,
            'CHAT2DESK_STATISTICS_REPORT_OPERATOR_EVENTS': 'operator_events',
            'CHAT_HOURLY_TIMEZONE': 'Asia/Almaty',
            'CHAT_HOURLY_REQUEST_TYPE': 'common',
            'CHAT2DESK_STATISTICS_REPORT_REQUEST_STATS': 'request_stats',
            'db': _FakeDb(members={1: members if members is not None else {18, 37, 149, 235}}),
            '_chat2desk_authorization_header': lambda: 'token',
            '_chat2desk_api_base_url': lambda: 'https://api.example',
            '_chat2desk_api_error_message': lambda response, report, day: f'HTTP {response.status_code}',
            '_chat2desk_extract_response_rows': lambda payload: (payload or {}).get('data') or [],
            '_chat2desk_extract_total': lambda payload: ((payload or {}).get('meta') or {}).get('total'),
            '_chat2desk_row_first': lambda row, *keys: next(
                (row[key] for key in keys if row.get(key) not in (None, '')), None),
            '_chat2desk_operator_display_name': lambda row: (
                f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()),
            '_chat_hourly_number': lambda value: (
                None if str(value if value is not None else '').strip() == ''
                else float(str(value).strip())),
            '_chat_hourly_is_open': lambda row: str(row.get('request_end') or '').strip() in ('', 'None', 'null'),
            '_chat_hourly_request_start': lambda row: str(row.get('request_start') or '').strip(),
            '_chat_hourly_operator_name': lambda row: str(row.get('operator_name') or '').strip(),
            '_chat_hourly_lock': threading.Lock(),
            '_chat_hourly_requests_cache': {'day': None, 'rows': {}},
            '_status_import_build_operator_lookup': lambda restrict_to_ids=None: {
                'lookup': [dict(info, id=info['id']) for info in matched.values()
                           if restrict_to_ids is None or info['id'] in restrict_to_ids]
            },
            '_status_import_resolve_operator_matches': lambda name, lookup: (
                [matched[name]] if name in matched and any(
                    info['id'] == matched[name]['id'] for info in lookup.get('lookup', [])) else []
            ),
        }
        # Ответ внутри чата считает боевая функция отчёта по чатам — берём её как есть.
        _load_names(source, {'_chat_hourly_response_times'}, ns)
        _load_names(source, NAMES, ns)
        ns['_SZOV_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        ns['_szov_chat_wallboard_events_cache'].update(day=None, rows={}, newest=None, truncated=False)
        ns['_szov_chat_wallboard_requests_cache'].update(day=None, ts=0.0)
        ns['_szov_chat_wallboard_cache'].update(ts=0.0, payload=None, failed_at=0.0, error=None)
        ns['_fake_requests'] = fake_requests
        return ns


class ChatWallboardStaffingTests(_Harness, unittest.TestCase):
    """Правая ось графика: сколько чатников нужно под цель в 2 минуты."""

    def test_required_scales_with_how_far_we_are_from_target(self):
        ns = self._namespace()
        # Втрое медленнее цели при трёх людях на линии — нужно втрое больше.
        self.assertEqual(ns['_szov_chat_wallboard_required'](3.0, 360), 9)

    def test_required_rounds_up_partial_person(self):
        ns = self._namespace()
        self.assertEqual(ns['_szov_chat_wallboard_required'](2.0, 130), 3)

    def test_required_never_drops_below_one_while_people_worked(self):
        """Ответ вдвое быстрее цели не означает «людей не нужно» — линию кто-то держит."""
        ns = self._namespace()
        self.assertEqual(ns['_szov_chat_wallboard_required'](0.5, 60), 1)

    def test_required_is_unknown_without_response_time_or_people(self):
        ns = self._namespace()
        self.assertIsNone(ns['_szov_chat_wallboard_required'](0.0, 360))
        self.assertIsNone(ns['_szov_chat_wallboard_required'](3.0, None))


class ChatWallboardTimelineTests(_Harness, unittest.TestCase):
    """Ленты статусов: что считается «на линии» и как достраивается ночная смена."""

    def test_only_status_events_of_szov_chat_managers_get_in(self):
        ns = self._namespace()
        events = [
            _event('Алия Тестова', 'online', '2026-08-18 09:00:00'),
            _event('Алия Тестова', 'take_chat', '2026-08-18 09:05:00'),
            _event('Посторонний Оператор', 'online', '2026-08-18 09:10:00'),
            _event('Администратор компании', 'offline', '2026-08-18 09:15:00', role='admin'),
        ]
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, unmatched = ns['_szov_chat_wallboard_timelines'](events, lookup)
        self.assertEqual(list(timelines), ['Алия Тестова'])
        self.assertEqual([entry[1] for entry in timelines['Алия Тестова']], ['online'])
        # Админская учётка чатов не ведёт и в «потерянные имена» не попадает.
        self.assertEqual(unmatched, ['Посторонний Оператор'])

    def test_night_shift_is_backfilled_from_midnight(self):
        """Первое событие суток — выход: человек работал с полуночи, а не появился в 02:00."""
        ns = self._namespace()
        events = [_event('Дана Ночная', 'logout', '2026-08-18 02:00:45')]
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, _ = ns['_szov_chat_wallboard_timelines'](events, lookup)
        entries = timelines['Дана Ночная']
        self.assertEqual(entries[0][0], 0)
        self.assertEqual(entries[0][1], 'online')
        self.assertEqual(entries[1][1], 'offline')

    def test_morning_login_is_not_backfilled(self):
        ns = self._namespace()
        events = [_event('Алия Тестова', 'login', '2026-08-18 09:00:00'),
                  _event('Алия Тестова', 'online', '2026-08-18 09:00:00')]
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, _ = ns['_szov_chat_wallboard_timelines'](events, lookup)
        self.assertEqual(timelines['Алия Тестова'][0][0], 9 * 3600)

    def test_online_seconds_split_across_hours_and_stop_at_now(self):
        ns = self._namespace()
        timelines = {'Алия Тестова': [(8 * 3600 + 1800, 'online', 'Онлайн', '')]}
        per_hour = ns['_szov_chat_wallboard_online_seconds'](timelines, 10 * 3600 + 900)
        self.assertEqual(per_hour[8], 1800)
        self.assertEqual(per_hour[9], 3600)
        self.assertEqual(per_hour[10], 900)

    def test_break_and_busy_do_not_hold_the_line(self):
        ns = self._namespace()
        timelines = {'Алия Тестова': [
            (9 * 3600, 'online', 'Онлайн', ''),
            (9 * 3600 + 600, 'busy', 'Занят', ''),
            (9 * 3600 + 1200, 'break', 'Перерыв', ''),
            (9 * 3600 + 1800, 'online', 'Онлайн', ''),
        ]}
        per_hour = ns['_szov_chat_wallboard_online_seconds'](timelines, 10 * 3600)
        self.assertEqual(per_hour[9], 600 + 1800)

    def test_status_spellings_are_normalized(self):
        """Chat2Desk пишет один статус четырьмя способами — ключ должен получиться один."""
        ns = self._namespace()
        for raw in ('tech break', 'tech_break', 'tech.break', 'status.tech.break', 'TECH BRAKE'):
            self.assertEqual(ns['_szov_chat_wallboard_status'](raw), ('tech', 'Тех. перерыв'), raw)
        self.assertEqual(ns['_szov_chat_wallboard_status']('status.online'), ('online', 'Онлайн'))
        self.assertIsNone(ns['_szov_chat_wallboard_status']('take_chat'))

    def test_unknown_live_status_goes_to_the_other_bucket(self):
        """Незнакомый статус не выдумываем ключом, иначе человек выпадет из всех счётчиков."""
        ns = self._namespace()
        self.assertEqual(ns['_szov_chat_wallboard_status']('dinner', unknown_as_other=True),
                         ('other', 'dinner'))

    def test_status_event_with_a_prefixed_spelling_breaks_the_online_span(self):
        """Раньше `tech.break` не распознавался, и человек «держал линию» весь перерыв."""
        ns = self._namespace()
        events = [_event('Алия Тестова', 'online', '2026-08-18 09:00:00'),
                  _event('Алия Тестова', 'status.tech.break', '2026-08-18 09:30:00')]
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, _ = ns['_szov_chat_wallboard_timelines'](events, lookup)
        per_hour = ns['_szov_chat_wallboard_online_seconds'](timelines, 10 * 3600)
        self.assertEqual(per_hour[9], 1800)

    def test_truncated_events_do_not_backfill_the_night(self):
        """Выгрузка обрезалась — самое раннее событие уже не начало смены, достраивать нельзя."""
        ns = self._namespace()
        ns['_szov_chat_wallboard_events_cache']['truncated'] = True
        events = [_event('Дана Ночная', 'break', '2026-08-18 14:10:00')]
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, _ = ns['_szov_chat_wallboard_timelines'](events, lookup)
        self.assertEqual([entry[1] for entry in timelines['Дана Ночная']], ['break'])
        self.assertEqual(ns['_szov_chat_wallboard_online_seconds'](timelines, 15 * 3600), {})


class ChatWallboardHourlyTests(_Harness, unittest.TestCase):
    """Почасовые строки графика."""

    def _rows(self, ns, timelines, requests, now):
        return {row['hour']: row for row in ns['_szov_chat_wallboard_hourly'](requests, timelines, now)}

    def test_hours_stop_at_the_current_hour(self):
        ns = self._namespace()
        now = datetime(2026, 8, 18, 13, 36, 0)
        rows = ns['_szov_chat_wallboard_hourly']([], {}, now)
        self.assertEqual([row['hour'] for row in rows], list(range(0, 14)))
        self.assertTrue(rows[-1]['partial'])
        self.assertFalse(rows[-2]['partial'])

    def test_partial_hour_counts_average_people_not_person_hours(self):
        """Час прожит на треть: два человека на линии — это два, а не 0,67."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 13, 20, 0)
        timelines = {
            'Алия Тестова': [(13 * 3600, 'online', 'Онлайн', '')],
            'Бекзат Примеров': [(13 * 3600, 'online', 'Онлайн', '')],
        }
        rows = self._rows(ns, timelines, [], now)
        self.assertEqual(rows[13]['operators_online'], 2.0)

    def test_row_carries_chats_response_and_required(self):
        ns = self._namespace()
        now = datetime(2026, 8, 18, 12, 0, 0)
        timelines = {'Алия Тестова': [(11 * 3600, 'online', 'Онлайн', '')],
                     'Бекзат Примеров': [(11 * 3600, 'online', 'Онлайн', '')]}
        requests = [
            # Ответ внутри чата = average_replies_time = total/replies = 720/3 = 240 c.
            _request('2026-08-18 11:05:00', 20, 3, 720),
            _request('2026-08-18 12:30:00', 10, 2, 200),
        ]
        rows = self._rows(ns, timelines, requests, now)
        self.assertEqual(rows[11]['chats'], 1)
        self.assertEqual(rows[11]['inner_reply_seconds'], 240)
        self.assertEqual(rows[11]['operators_online'], 2.0)
        self.assertEqual(rows[11]['operators_required'], 4)  # 2 × 240 / 120

    def test_hour_without_people_has_no_recommendation(self):
        ns = self._namespace()
        now = datetime(2026, 8, 18, 3, 0, 0)
        rows = self._rows(ns, {}, [_request('2026-08-18 02:10:00', 20, 3, 740)], now)
        self.assertEqual(rows[2]['chats'], 1)
        self.assertIsNone(rows[2]['operators_required'])


class ChatWallboardNowTests(_Harness, unittest.TestCase):
    """Счётчики «сейчас»: онлайн, занят, тренинг."""

    def _now(self, ns, events, operator_rows, now_seconds=13 * 3600 + 36 * 60):
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, _ = ns['_szov_chat_wallboard_timelines'](events, lookup)
        return ns['_szov_chat_wallboard_now'](timelines, operator_rows, lookup, now_seconds)

    def test_busy_is_not_confused_with_a_finished_shift(self):
        """Ключевое: у вышедшего offline_type остаётся прежним, поэтому статус берём из событий."""
        ns = self._namespace()
        events = [
            _event('Бекзат Примеров', 'busy', '2026-08-18 10:08:34'),
            _event('Дана Ночная', 'logout', '2026-08-18 02:00:45'),
        ]
        rows = [
            # Оба висят в живом списке одинаково: online=0, offline_type='busy'.
            _operator_row('Бекзат Примеров', online=0, offline_type='busy'),
            _operator_row('Дана Ночная', online=0, offline_type='busy'),
        ]
        now = self._now(ns, events, rows)
        self.assertEqual(now['operators_busy'], 1)
        self.assertEqual(now['operators_offline'], 1)
        self.assertEqual([person['name'] for person in now['operators']],
                         ['Примеров Бекзат Примерулы'])

    def test_counts_by_status_and_open_chats(self):
        ns = self._namespace()
        events = [
            _event('Алия Тестова', 'online', '2026-08-18 13:10:46'),
            _event('Ерлан Учебный', 'study', '2026-08-18 13:30:00'),
            _event('Бекзат Примеров', 'break', '2026-08-18 13:20:00'),
        ]
        rows = [
            _operator_row('Алия Тестова', dialogs=75),
            _operator_row('Ерлан Учебный', dialogs=3),
            _operator_row('Бекзат Примеров', dialogs=1),
        ]
        now = self._now(ns, events, rows)
        self.assertEqual(now['operators_online'], 1)
        self.assertEqual(now['operators_on_training'], 1)
        self.assertEqual(now['operators_on_break'], 1)
        self.assertEqual(now['open_chats'], 79)
        # Первым в списке тот, кто держит линию, дальше — по убыванию доступности.
        self.assertEqual([person['status_key'] for person in now['operators']],
                         ['online', 'training', 'break'])

    def test_time_in_status_counts_from_the_last_event(self):
        ns = self._namespace()
        events = [_event('Алия Тестова', 'online', '2026-08-18 13:00:00')]
        now = self._now(ns, events, [_operator_row('Алия Тестова')],
                        now_seconds=13 * 3600 + 36 * 60)
        self.assertEqual(now['operators'][0]['seconds'], 36 * 60)

    def test_operator_without_events_falls_back_to_the_live_flag(self):
        """Смена началась вчера: событий сегодня нет, но человек на линии."""
        ns = self._namespace()
        now = self._now(ns, [], [_operator_row('Алия Тестова', online=1, dialogs=12)])
        self.assertEqual(now['operators_online'], 1)
        self.assertIsNone(now['operators'][0]['seconds'])

    def test_disabled_accounts_are_ignored(self):
        ns = self._namespace()
        now = self._now(ns, [], [_operator_row('Алия Тестова', online=1, status='deleted')])
        self.assertEqual(now['operators_online'], 0)
        self.assertEqual(now['operators'], [])

    def test_only_szov_chat_managers_are_counted(self):
        ns = self._namespace(members=set())
        now = self._now(ns, [], [_operator_row('Алия Тестова', online=1)])
        self.assertEqual(now['operators_online'], 0)

    def test_unknown_status_is_counted_in_other_not_lost(self):
        ns = self._namespace()
        now = self._now(ns, [], [_operator_row('Алия Тестова', online=1, offline_type='dinner')])
        self.assertEqual(now['operators_other'], 1)
        self.assertEqual(now['operators_online'], 0)
        self.assertEqual(now['operators'][0]['status'], 'dinner')

    def test_account_disabled_midday_counts_as_offline(self):
        """Учётки в живом списке уже нет: на линии человека нет, а плитка «Онлайн» не должна врать."""
        ns = self._namespace()
        events = [_event('Алия Тестова', 'online', '2026-08-18 09:00:00')]
        now = self._now(ns, events, [])
        self.assertEqual(now['operators_online'], 0)
        self.assertEqual(now['operators_offline'], 1)
        self.assertEqual(now['operators'], [])

    def test_strangers_do_not_pollute_the_rename_diagnostic(self):
        """В диагностику идут только потерянные чат-менеджеры, а не операторы других отделов."""
        ns = self._namespace()
        lookup, _ = ns['_szov_chat_wallboard_operator_lookup']()
        timelines, unmatched = ns['_szov_chat_wallboard_timelines']([], lookup)
        ns['_szov_chat_wallboard_now'](timelines, [_operator_row('Чужой Оператор', online=1)],
                                       lookup, 10 * 3600)
        self.assertEqual(unmatched, [])


class ChatWallboardEventsFetchTests(_Harness, unittest.TestCase):
    """Догрузка событий: страницы качаем сверху и до уже известных."""

    def _paged(self, pages):
        def handler(params):
            offset = int(params.get('offset') or 0)
            index = offset // 200
            rows = pages[index] if index < len(pages) else []

            class _Response:
                status_code = 200

                @staticmethod
                def json():
                    return {'data': rows, 'meta': {'total': sum(len(page) for page in pages)}}
            return _Response()
        return handler

    def test_first_pass_walks_the_whole_day(self):
        first = [_event('Алия Тестова', 'online', f'2026-08-18 09:{index:02d}:00') for index in range(60)]
        second = [_event('Алия Тестова', 'busy', f'2026-08-18 08:{index:02d}:00') for index in range(60)]
        ns = self._namespace(requests_get=self._paged([first[:200] + second[:200]]))
        rows = ns['_szov_chat_wallboard_fetch_events']('2026-08-18')
        self.assertEqual(len(rows), 120)
        self.assertEqual(len(ns['_fake_requests'].calls), 1)

    def test_second_pass_stops_at_known_events(self):
        page_one = [_event('Алия Тестова', 'online', f'2026-08-18 12:{index:02d}:00', operator_id=index)
                    for index in range(59, -1, -1)]
        page_two = [_event('Алия Тестова', 'busy', f'2026-08-18 11:{index:02d}:00', operator_id=index)
                    for index in range(59, -1, -1)]
        pages = [(page_one + page_two)[:200]]
        ns = self._namespace(requests_get=self._paged(pages))
        ns['_szov_chat_wallboard_fetch_events']('2026-08-18')
        calls_after_first = len(ns['_fake_requests'].calls)
        rows = ns['_szov_chat_wallboard_fetch_events']('2026-08-18')
        # Ни одного нового события: страниц столько же, дублей в кэше нет.
        self.assertEqual(len(rows), 120)
        self.assertEqual(len(ns['_fake_requests'].calls), calls_after_first + 1)

    def test_new_day_drops_the_cache(self):
        ns = self._namespace(requests_get=self._paged([[_event('Алия Тестова', 'online', '2026-08-18 09:00:00')]]))
        ns['_szov_chat_wallboard_fetch_events']('2026-08-18')
        ns['_szov_chat_wallboard_fetch_events']('2026-08-19')
        self.assertEqual(ns['_szov_chat_wallboard_events_cache']['day'], '2026-08-19')


class ChatWallboardSnapshotCacheTests(_Harness, unittest.TestCase):
    """Общий кэш снимка: квота Chat2Desk одна на компанию, N открытых табло — один запрос."""

    def _namespace_with_fetch(self, payloads):
        ns = self._namespace()
        state = {'calls': 0}

        def fake_fetch():
            state['calls'] += 1
            result = payloads[min(state['calls'] - 1, len(payloads) - 1)]
            if isinstance(result, Exception):
                raise result
            return dict(result)

        ns['_szov_chat_wallboard_fetch_snapshot'] = fake_fetch
        ns['_fetch_state'] = state
        return ns

    def test_second_call_within_ttl_reuses_the_snapshot(self):
        ns = self._namespace_with_fetch([{'day': '2026-08-18'}])
        first = ns['_szov_chat_wallboard_snapshot']()
        second = ns['_szov_chat_wallboard_snapshot']()
        self.assertEqual(ns['_fetch_state']['calls'], 1)
        self.assertFalse(first['stale'])
        self.assertFalse(second['stale'])

    def test_failure_returns_the_last_snapshot_marked_stale(self):
        ns = self._namespace_with_fetch([{'day': '2026-08-18'}, RuntimeError('Chat2Desk HTTP 500')])
        ns['_szov_chat_wallboard_snapshot']()
        ns['_szov_chat_wallboard_cache']['ts'] -= ns['SZOV_CHAT_WALLBOARD_CACHE_TTL_SECONDS'] + 1
        stale = ns['_szov_chat_wallboard_snapshot']()
        self.assertTrue(stale['stale'])
        self.assertIn('Chat2Desk', stale['error'])
        self.assertEqual(stale['day'], '2026-08-18')

    def test_failure_without_any_snapshot_raises(self):
        ns = self._namespace_with_fetch([RuntimeError('Chat2Desk HTTP 500')])
        with self.assertRaises(RuntimeError):
            ns['_szov_chat_wallboard_snapshot']()


class ChatWallboardWiringTests(unittest.TestCase):
    """Разводка на фронте: переключатель направления, отдельный опрос, состав экрана."""

    @classmethod
    def setUpClass(cls):
        cls.view = (ROOT / "src" / "components" / "monitoring" / "SzovWallboardView.jsx").read_text(encoding="utf-8-sig")
        cls.board = (ROOT / "src" / "components" / "monitoring" / "SzovChatWallboard.jsx").read_text(encoding="utf-8-sig")
        cls.shared = (ROOT / "src" / "components" / "monitoring" / "szovWallboardShared.js").read_text(encoding="utf-8-sig")

    def test_header_offers_both_directions(self):
        self.assertIn("{ key: 'osnova', label: 'Основа'", self.view)
        self.assertIn("{ key: 'chat', label: 'Чат'", self.view)
        self.assertIn("<SegmentedSwitch value={direction} options={DIRECTIONS}", self.view)

    def test_only_the_open_direction_is_polled(self):
        """Закрытое направление не должно тратить квоту Chat2Desk: хук живёт в своём компоненте."""
        self.assertIn("if (direction === 'chat') {", self.view)
        line_board = self.view[self.view.index("const LineWallboard ="):self.view.index("const ChatWallboard =")]
        self.assertNotIn("useSzovChatWallboardSnapshot", line_board)
        chat_board = self.view[self.view.index("const ChatWallboard ="):
                               self.view.index("export default function SzovWallboardView")]
        self.assertNotIn("useSzovWallboardSnapshot", chat_board)

    def test_chat_direction_polls_its_own_endpoint_slower(self):
        self.assertIn("const CHAT_POLL_INTERVAL_MS = 60000;", self.shared)
        self.assertIn("path: '/api/szov_wallboard/chat_snapshot'", self.shared)
        self.assertIn("pollIntervalMs: CHAT_POLL_INTERVAL_MS", self.shared)

    def test_broadcast_and_widget_stay_on_the_line_direction(self):
        chat_board = self.view[self.view.index("const ChatWallboard ="):
                               self.view.index("export default function SzovWallboardView")]
        self.assertNotIn("Отбивка", chat_board)
        self.assertNotIn("onToggleWidget", chat_board)

    def test_chosen_direction_is_remembered(self):
        self.assertIn("otp:szov-wallboard-direction", self.view)
        self.assertIn("readStoredDirection(userId)", self.view)

    def test_chat_board_shows_the_three_asked_counters(self):
        for label in ('label="Онлайн"', 'label="Занят"', 'label="Тренинг"'):
            self.assertIn(label, self.board, label)

    def test_chart_pairs_minutes_with_people_and_shows_the_target(self):
        self.assertIn('yAxisId="left"', self.board)
        self.assertIn('yAxisId="right"', self.board)
        self.assertIn('dataKey="innerMinutes"', self.board)
        self.assertIn('dataKey="required"', self.board)
        self.assertIn('dataKey="online"', self.board)
        self.assertIn('<ReferenceLine yAxisId="left" y={targetSeconds / 60}', self.board)
        # У каждого ряда подпись: две оси без легенды прочитать невозможно.
        self.assertIn('<Legend', self.board)
        for name in ('name="Было на линии"', 'name="Нужно под цель"', 'name="Ответ внутри чата"'):
            self.assertIn(name, self.board, name)

    def test_chart_hours_come_from_the_snapshot(self):
        self.assertIn("const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`;", self.board)
        self.assertIn("rows || []", self.board)


if __name__ == '__main__':
    unittest.main()
