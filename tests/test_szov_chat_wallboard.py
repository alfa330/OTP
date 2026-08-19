"""Табло СЗоВ, направление «Чат»: статусы чатников, почасовой срез и кэш снимка.

Функции бэкенда вытаскиваем из bot_schedule2.py тем же загрузчиком, что и тесты «Основы»
(`_load_names`): проверяется настоящая логика, а не строковое совпадение.
"""
import logging
import math
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from io import BytesIO
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
    'SZOV_CHAT_WALLBOARD_ONLINE_MIN_SECONDS',
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
    '_szov_chat_wallboard_hourly',
    '_szov_chat_wallboard_display_names',
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
        _load_names(source, {'_chat_hourly_response_times', '_chat_hourly_response_sums'}, ns)
        _load_names(source, NAMES, ns)
        ns['_SZOV_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        ns['_szov_chat_wallboard_events_cache'].update(day=None, rows={}, newest=None, truncated=False)
        ns['_szov_chat_wallboard_requests_cache'].update(day=None, ts=0.0)
        ns['_szov_chat_wallboard_cache'].update(ts=0.0, payload=None, failed_at=0.0, error=None)
        ns['_fake_requests'] = fake_requests
        return ns


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

    def test_online_seconds_land_in_every_hour_they_touched_and_stop_at_now(self):
        """Смена через границу часа считается в каждом часе своей частью и не идёт в будущее."""
        ns = self._namespace()
        timelines = {'Алия Тестова': [(8 * 3600 + 1800, 'online', 'Онлайн', '')]}
        per_hour = ns['_szov_chat_wallboard_online_seconds'](timelines, 10 * 3600 + 900)
        # Разбивка по людям: из неё считаются и голова часа, и ФИО в подсказке графика.
        self.assertEqual(per_hour, {8: {'Алия Тестова': 1800}, 9: {'Алия Тестова': 3600},
                                    10: {'Алия Тестова': 900}})

    def test_busy_and_training_do_not_hold_the_line(self):
        """Занят и тренинг — это НЕ на линии; отрезки одного человека внутри часа складываются."""
        ns = self._namespace()
        timelines = {'Алия Тестова': [
            (9 * 3600, 'online', 'Онлайн', ''),
            (9 * 3600 + 600, 'busy', 'Занят', ''),
            (9 * 3600 + 1200, 'break', 'Перерыв', ''),
            (9 * 3600 + 1800, 'online', 'Онлайн', ''),
        ],
            # Весь час занят, весь час на тренинге: линию не держали, в счёт не идут.
            'Бекзат Примеров': [(9 * 3600, 'busy', 'Занят', '')],
            'Ерлан Учебный': [(9 * 3600, 'training', 'Тренинг', '')]}
        per_hour = ns['_szov_chat_wallboard_online_seconds'](timelines, 10 * 3600)
        self.assertEqual(per_hour, {9: {'Алия Тестова': 600 + 1800}})

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
        # Нераспознанный `tech.break` тянул бы «Онлайн» до самого «сейчас» — человек попал бы
        # ещё и в 10-й час.
        per_hour = ns['_szov_chat_wallboard_online_seconds'](timelines, 11 * 3600)
        self.assertEqual(per_hour, {9: {'Алия Тестова': 1800}})

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
    """Почасовые строки графика: чаты, время ответа и КТО держал линию."""

    def _rows(self, ns, timelines, requests, now, **kwargs):
        return {row['hour']: row
                for row in ns['_szov_chat_wallboard_hourly'](requests, timelines, now, **kwargs)}

    def test_hours_stop_at_the_current_hour(self):
        ns = self._namespace()
        now = datetime(2026, 8, 18, 13, 36, 0)
        rows = ns['_szov_chat_wallboard_hourly']([], {}, now)
        self.assertEqual([row['hour'] for row in rows], list(range(0, 14)))
        self.assertTrue(rows[-1]['partial'])
        self.assertFalse(rows[-2]['partial'])

    def test_closed_day_covers_all_24_hours(self):
        """Выгрузка за прошедший день: сутки закрыты, «час идёт» там некому идти."""
        ns = self._namespace()
        rows = ns['_szov_chat_wallboard_hourly']([], {}, None, full_day=True)
        self.assertEqual([row['hour'] for row in rows], list(range(0, 24)))
        self.assertFalse(any(row['partial'] for row in rows))

    def test_people_are_counted_by_heads(self):
        """Решение владельца 19.08.2026 (вечер): двое на линии — это 2, а не доля часа."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 13, 20, 0)
        timelines = {
            'Алия Тестова': [(13 * 3600, 'online', 'Онлайн', '')],
            'Бекзат Примеров': [(13 * 3600, 'online', 'Онлайн', '')],
        }
        rows = self._rows(ns, timelines, [], now)
        self.assertEqual(rows[13]['operators_online'], 2)
        self.assertIsInstance(rows[13]['operators_online'], int)

    def test_two_half_shifts_count_as_two_people(self):
        """Сменились по получасу — на линии в этом часу было двое (прежняя FTE давала 1,0)."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 10, 0, 0)
        timelines = {
            'Алия Тестова': [(9 * 3600, 'online', 'Онлайн', ''),
                             (9 * 3600 + 1800, 'logout', 'Не в системе', '')],
            'Бекзат Примеров': [(9 * 3600 + 1800, 'online', 'Онлайн', ''),
                                (10 * 3600, 'logout', 'Не в системе', '')],
        }
        rows = self._rows(ns, timelines, [], now)
        self.assertEqual(rows[9]['operators_online'], 2)
        # Минуты смены остались рядом: по ним видно, что часа на двоих было ровно один.
        self.assertEqual(rows[9]['online_seconds'], 3600)

    def test_visit_under_a_minute_is_not_a_person_but_is_not_lost(self):
        """Заглянул на 40 секунд — не человек часа; в счёт не идёт, но и не исчезает."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 10, 0, 0)
        timelines = {'Алия Тестова': [(9 * 3600, 'online', 'Онлайн', ''),
                                      (9 * 3600 + 40, 'logout', 'Не в системе', '')]}
        rows = self._rows(ns, timelines, [], now)
        self.assertEqual(rows[9]['operators_online'], 0)
        self.assertEqual(rows[9]['operators'], [])
        self.assertEqual(rows[9]['operators_under_minute'], 1)
        self.assertEqual(rows[9]['online_seconds'], 40)

    def test_a_full_minute_already_counts(self):
        """Порог — минута и больше: ровно минута человека уже делает человеком часа."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 10, 0, 0)
        timelines = {'Алия Тестова': [(9 * 3600, 'online', 'Онлайн', ''),
                                      (9 * 3600 + 60, 'logout', 'Не в системе', '')]}
        rows = self._rows(ns, timelines, [], now)
        self.assertEqual(rows[9]['operators_online'], 1)
        self.assertEqual(rows[9]['operators_under_minute'], 0)

    def test_row_carries_the_people_by_name_and_minutes(self):
        """Подсказка графика показывает ФИО: сверху те, кто держал линию дольше."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 10, 0, 0)
        timelines = {
            'Алия Тестова': [(9 * 3600, 'online', 'Онлайн', ''),
                             (9 * 3600 + 1200, 'break', 'Перерыв', '')],
            'Бекзат Примеров': [(9 * 3600, 'online', 'Онлайн', '')],
        }
        names = {'Алия Тестова': 'Тестова Алия Тестовна',
                 'Бекзат Примеров': 'Примеров Бекзат Примерулы'}
        rows = self._rows(ns, timelines, [], now, names=names)
        self.assertEqual([person['name'] for person in rows[9]['operators']],
                         ['Примеров Бекзат Примерулы', 'Тестова Алия Тестовна'])
        self.assertEqual([person['seconds'] for person in rows[9]['operators']], [3600, 1200])

    def test_name_without_a_matched_employee_stays_itself(self):
        """ФИО не нашли — оставляем имя учётки: минуты есть, а человека под ними быть обязано."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 10, 0, 0)
        timelines = {'Алия Тестова': [(9 * 3600, 'online', 'Онлайн', '')]}
        rows = self._rows(ns, timelines, [], now, names={})
        self.assertEqual([person['name'] for person in rows[9]['operators']], ['Алия Тестова'])

    def test_row_carries_the_minutes_the_shift_stood_on_the_line(self):
        """Рядом с головами — сколько всего минут смена простояла на линии в этом часу."""
        ns = self._namespace()
        now = datetime(2026, 8, 18, 10, 0, 0)
        timelines = {
            'Алия Тестова': [(9 * 3600, 'online', 'Онлайн', '')],
            'Бекзат Примеров': [(9 * 3600, 'online', 'Онлайн', ''),
                                (9 * 3600 + 1800, 'break', 'Перерыв', '')],
        }
        rows = self._rows(ns, timelines, [], now)
        self.assertEqual(rows[9]['online_seconds'], 3600 + 1800)
        self.assertEqual(rows[9]['operators_online'], 2)

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
        # Час — промежуток: заявка из 12:30 в строку 11 часа не попадает.
        self.assertEqual(rows[11]['chats'], 1)
        self.assertEqual(rows[12]['chats'], 1)
        self.assertEqual(rows[11]['inner_reply_seconds'], 240)
        self.assertEqual(rows[11]['operators_online'], 2)
        self.assertNotIn('operators_required', rows[11])

    def test_hour_without_people_still_carries_the_chats(self):
        ns = self._namespace()
        now = datetime(2026, 8, 18, 3, 0, 0)
        rows = self._rows(ns, {}, [_request('2026-08-18 02:10:00', 20, 3, 740)], now)
        self.assertEqual(rows[2]['chats'], 1)
        self.assertEqual(rows[2]['operators_online'], 0)
        self.assertEqual(rows[2]['operators'], [])
        self.assertEqual(rows[2]['online_seconds'], 0)


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

    def test_broadcast_stays_on_the_line_direction(self):
        """Отбивка собрана по показателям линии — на экране чатов ей делать нечего."""
        chat_board = self.view[self.view.index("const ChatWallboard ="):
                               self.view.index("export default function SzovWallboardView")]
        self.assertNotIn("Отбивка", chat_board)

    def test_chat_direction_offers_the_widget_too(self):
        """Виджет «поверх окон» есть у обоих направлений, у каждого со своим набором."""
        chat_board = self.view[self.view.index("const ChatWallboard ="):
                               self.view.index("export default function SzovWallboardView")]
        self.assertIn("onToggleWidget={onToggleWidget}", chat_board)
        self.assertIn("<WidgetButton direction={direction}", self.view)
        widget = (ROOT / "src" / "components" / "monitoring" / "SzovWallboardWidget.jsx").read_text(encoding="utf-8-sig")
        self.assertIn("direction = 'osnova'", widget)
        self.assertIn("wallboardDirection(direction)", widget)
        for key in ('chat_online', 'chat_busy', 'chat_training', 'chat_inner', 'chat_shift_list'):
            self.assertIn(f"key: '{key}'", self.shared, key)

    def test_chosen_direction_is_remembered(self):
        self.assertIn("otp:szov-wallboard-direction", self.view)
        self.assertIn("readStoredDirection(userId)", self.view)

    def test_every_person_on_shift_carries_a_status(self):
        """Запрос владельца: статус рядом с каждым чатником, включая «Онлайн»."""
        column = self.board[self.board.index('const ChatPeopleColumn'):self.board.index('const formatPeople')]
        self.assertIn('{item.status}', column)
        self.assertNotIn("item.status_key !== 'online'", column)

    def test_chat_board_shows_the_three_asked_counters(self):
        for label in ('label="Онлайн"', 'label="Занят"', 'label="Тренинг"'):
            self.assertIn(label, self.board, label)

    def test_chart_pairs_minutes_with_people_on_the_line(self):
        self.assertIn('yAxisId="left"', self.board)
        self.assertIn('yAxisId="right"', self.board)
        self.assertIn('dataKey="innerMinutes"', self.board)
        self.assertIn('dataKey="online"', self.board)
        self.assertIn('<ReferenceLine yAxisId="left" y={targetSeconds / 60}', self.board)
        # У каждого ряда подпись: две оси без легенды прочитать невозможно.
        self.assertIn('<Legend', self.board)
        for name in ('name="Чатников на линии"', 'name="Ответ внутри чата"'):
            self.assertIn(name, self.board, name)

    def test_the_hour_hint_pairs_people_with_minutes_on_the_line(self):
        """Головы без минут врут: двое по часу и двое по десять минут — разные вещи."""
        self.assertIn("label={{ value: 'чатники'", self.board)
        self.assertIn("${formatPeople(row.online) || '0'} чел.", self.board)
        self.assertIn("{formatMinutes(row.onlineSeconds, 0)} на линии", self.board)
        self.assertIn('onlineSeconds: row.online_seconds ?? null', self.board)
        # Не добравшие до минуты видны отдельной пометкой, а не растворяются в счётчике.
        self.assertIn("{formatInt(row.underMinute)} меньше минуты", self.board)

    def test_required_staffing_is_gone_from_the_board(self):
        """Расчёт «сколько нужно под 2 минуты» убран: пропорция от факта завышала в разы."""
        self.assertNotIn('dataKey="required"', self.board)
        self.assertNotIn('Нужно под цель', self.board)
        api = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        self.assertNotIn('operators_required', api)

    def test_chat_direction_offers_the_excel_export(self):
        """Выгрузка есть только у «Чата» и только когда снимок уже пришёл."""
        self.assertIn("/api/szov_wallboard/chat_export", self.view)
        self.assertIn("<ChatExportControls", self.view)
        line_board = self.view[self.view.index("const LineWallboard ="):
                               self.view.index("const chatExportFileName =")]
        self.assertNotIn("ChatExportControls", line_board)
        chat_board = self.view[self.view.index("const ChatWallboard ="):
                               self.view.index("export default function SzovWallboardView")]
        self.assertIn("{snapshot ? (\n                <ChatExportControls", chat_board)

    def test_export_period_uses_the_shared_picker(self):
        """Пикер тот же, что в «Чатах ChatApp» — календарь на разделы один, а не копия."""
        self.assertIn("import { IosDateRangePicker, isoDate, rangeLabel } from '../ui/DateRangePicker';",
                      self.view)
        self.assertIn("<IosDateRangePicker", self.view)
        self.assertIn("query.set('date_from', from)", self.view)
        self.assertIn("query.set('date_to', to)", self.view)
        # «Весь период» тут не годится: выгрузка качает Chat2Desk по дню на день.
        self.assertIn("chatExportPresets", self.view)
        self.assertNotIn("'Весь период'", self.view)
        # Потолок периода гасит кнопку ДО запроса, а не после минуты ожидания.
        self.assertIn("const CHAT_EXPORT_MAX_DAYS = 31;", self.view)
        self.assertIn("disabled={busy || tooLong}", self.view)

    def test_export_file_name_carries_the_period(self):
        """Имя собирает фронт: Content-Disposition через CORS сюда не доходит."""
        self.assertIn("const chatExportFileName = (snapshot, from, to) => {", self.view)
        self.assertIn("if (from && to && from !== to) return `szov_wallboard_chat_${day(from)}_${day(to)}.xlsx`;",
                      self.view)
        self.assertIn("String(snapshot?.chat2desk_now || '').slice(11, 16)", self.view)

    def test_export_failure_is_reported_by_toast(self):
        """showToast держим в ref: он новый на каждом рендере, а табло рендерится по опросу."""
        self.assertIn("Не удалось выгрузить показатели", self.view)
        self.assertIn("const toastRef = useRef(showToast);", self.view)
        element = self.view[self.view.index("<ChatExportControls"):]
        element = element[:element.index("/>")]
        for prop in ('apiBaseUrl={apiBaseUrl}', 'withAccessTokenHeader={withAccessTokenHeader}',
                     'showToast={showToast}', 'snapshot={snapshot}'):
            self.assertIn(prop, element, prop)

    def test_hourly_chart_can_show_who_was_on_the_line(self):
        """Переключатель у «По часам»: график тот же, меняется только подсказка (запрос владельца)."""
        self.assertIn("export const HOURLY_TOOLTIP_MODES = [", self.board)
        self.assertIn("{ key: 'people', label: 'Кто на линии'", self.board)
        self.assertIn("<SegmentedSwitch", self.board)
        self.assertIn("tooltipMode === 'people' ? <PeopleTooltip /> : <ChartTooltip />", self.board)
        self.assertIn("people: Array.isArray(row.operators) ? row.operators : []", self.board)
        self.assertIn("underMinute: Number(row.operators_under_minute) || 0", self.board)

    def test_people_on_the_line_are_whole_people(self):
        """Дробей на графике больше нет: считаем головами (решение владельца 19.08.2026, вечер)."""
        self.assertIn("return String(Math.round(number));", self.board)
        self.assertNotIn("rounded.toFixed(1)", self.board)
        self.assertIn('allowDecimals={false}', self.board)

    def test_chart_hours_are_labelled_as_intervals(self):
        """«12–13», как в почасовом отчёте: иначе непонятно, промежуток это или накопление."""
        self.assertIn("const hourLabel = (hour) => `${String(hour).padStart(2, '0')}–"
                      "${String((hour + 1) % 24).padStart(2, '0')}`;", self.board)
        self.assertIn("rows || []", self.board)


# Имена сборщика выгрузки. Отдельным набором: файл собирается из готовых дней, источники
# Chat2Desk ему не нужны, и тянуть в namespace весь их обвес было бы шумом.
EXPORT_NAMES = {
    'SZOV_CHAT_WALLBOARD_TARGET_SECONDS',
    'SZOV_CHAT_EXPORT_MAX_DAYS',
    'SZOV_CHAT_EXPORT_WORKERS',
    '_SZOV_CHAT_EXPORT_STATUS_FILL',
    '_szov_chat_export_minutes',
    '_szov_chat_export_hour_label',
    '_szov_chat_export_parse_day',
    '_szov_chat_export_period',
    '_szov_chat_export_day_people',
    '_szov_chat_export_finish_day',
    '_szov_chat_export_today_block',
    '_szov_chat_export_file_name',
    '_szov_chat_wallboard_workbook',
}


def _export_namespace():
    """namespace для сборщика выгрузки: openpyxl настоящий, дни подаём руками."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
    ns = {
        'math': math,
        'datetime': datetime,
        'timedelta': timedelta,
        'ZoneInfo': ZoneInfo,
        'BytesIO': BytesIO,
        'Workbook': Workbook,
        'Font': Font,
        'PatternFill': PatternFill,
        'Alignment': Alignment,
        'Border': Border,
        'Side': Side,
        'get_column_letter': get_column_letter,
        'CHAT_HOURLY_TIMEZONE': 'Asia/Almaty',
        '_env_int': lambda name, default, minimum=None, maximum=None: default,
    }
    _load_names(source, EXPORT_NAMES, ns)
    return ns


# Час выгрузки: люди уже посчитаны головами, как их отдаёт _szov_chat_wallboard_hourly.
def _export_hour(hour, chats, inner, first, people, *, under=0, partial=False):
    return {
        'hour': hour, 'chats': chats, 'inner_reply_seconds': inner, 'first_reply_seconds': first,
        'operators_online': len(people),
        'operators': [{'name': name, 'seconds': seconds} for name, seconds in people],
        'operators_under_minute': under,
        'online_seconds': sum(seconds for _name, seconds in people),
        'partial': partial,
    }


ALIA, BEK, DANA = 'Тестова Алия Тестовна', 'Примеров Бекзат Примерулы', 'Ночная Дана Сменовна'


def _export_day(ns, day, *, chats_open=None, error=None, truncated=False, unmatched=(),
                partial_last=False):
    hourly = [
        _export_hour(9, 10, 90.0, 60.0, [(ALIA, 3600), (DANA, 1800)], under=1),
        _export_hour(10, 30, 210.0, 120.0, [(ALIA, 3600), (BEK, 3600), (DANA, 900)]),
        _export_hour(11, 0, None, None, []),
        _export_hour(15, 6, 105.0, 75.0, [(ALIA, 1800)], partial=partial_last),
    ]
    return ns['_szov_chat_export_finish_day'](day, hourly, {
        'chats': 46, 'chats_open': chats_open,
        'first_reply_seconds': 90.0, 'inner_reply_seconds': 150.0,
        'reply_sums': {'first_sum': 4140.0, 'first_count': 46,
                       'inner_sum': 6900.0, 'inner_count': 46},
    }, events_truncated=truncated, unmatched_names=unmatched, error=error)


NOW_BLOCK = {
    'operators_online': 3, 'operators_busy': 1, 'operators_on_training': 1,
    'operators_on_break': 2, 'operators_on_holiday': 1, 'operators_other': 0,
    'operators_offline': 4, 'open_chats': 12,
    'operators': [
        {'name': ALIA, 'status': 'Онлайн', 'status_key': 'online', 'seconds': 1800, 'open_chats': 7},
        {'name': DANA, 'status': 'Онлайн', 'status_key': 'online', 'seconds': None, 'open_chats': 5},
        {'name': 'Учебный Ерлан Тренингулы', 'status': 'Тренинг', 'status_key': 'training',
         'seconds': 600, 'open_chats': 0},
    ],
}


class ChatWallboardExportPeriodTests(unittest.TestCase):
    """Период выгрузки: что разрешено просить и как это превращается в дни."""

    @classmethod
    def setUpClass(cls):
        cls.ns = _export_namespace()

    def _period(self, date_from, date_to, today=date(2026, 8, 19)):
        return self.ns['_szov_chat_export_period'](date_from, date_to, today)

    def test_no_dates_mean_today(self):
        """Самый частый случай — глянул на табло и забрал цифры: он не стоит ни одного запроса."""
        self.assertEqual(self._period(None, None), ['2026-08-19'])

    def test_period_is_expanded_day_by_day(self):
        self.assertEqual(self._period('2026-08-17', '2026-08-19'),
                         ['2026-08-17', '2026-08-18', '2026-08-19'])

    def test_reversed_period_is_straightened(self):
        self.assertEqual(self._period('2026-08-19', '2026-08-17'),
                         ['2026-08-17', '2026-08-18', '2026-08-19'])

    def test_one_date_is_a_single_day(self):
        self.assertEqual(self._period('2026-08-17', None), ['2026-08-17'])
        self.assertEqual(self._period(None, '2026-08-17'), ['2026-08-17'])

    def test_tail_in_the_future_is_cut_off(self):
        """Chat2Desk отдал бы пустые сутки, а в файле они читались бы как день простоя."""
        self.assertEqual(self._period('2026-08-18', '2026-08-25'), ['2026-08-18', '2026-08-19'])

    def test_broken_and_oversized_periods_are_refused(self):
        for date_from, date_to in (('2026-13-01', None), (None, 'вчера')):
            with self.assertRaises(ValueError):
                self._period(date_from, date_to)
        with self.assertRaises(ValueError):
            self._period('2026-08-25', '2026-08-26')  # весь период в будущем
        with self.assertRaises(ValueError):
            self._period('2026-01-01', '2026-08-19')  # больше потолка суток

    def test_file_name_says_what_is_inside(self):
        name = self.ns['_szov_chat_export_file_name']
        self.assertEqual(name({'from': '2026-08-17', 'to': '2026-08-19'}),
                         'szov_wallboard_chat_20260817_20260819.xlsx')
        # У сегодняшнего дня в имени стоит момент данных, а не скачивания.
        self.assertEqual(name({'from': '2026-08-19', 'to': '2026-08-19', 'day': '2026-08-19',
                               'chat2desk_now': '2026-08-19 15:20:30'}),
                         'szov_wallboard_chat_20260819_1520.xlsx')
        self.assertEqual(name({'from': '2026-08-17', 'to': '2026-08-17'}),
                         'szov_wallboard_chat_20260817.xlsx')


class ChatWallboardExcelExportTests(unittest.TestCase):
    """Выгрузка показателей табло по чатам в .xlsx: те же цифры, что на экране."""

    @classmethod
    def setUpClass(cls):
        cls.ns = _export_namespace()

    def _payload(self, days, **overrides):
        payload = {
            'from': days[0]['day'], 'to': days[-1]['day'], 'today': '2026-08-19',
            'days': days, 'target_seconds': 120, 'now': NOW_BLOCK,
            'chat2desk_now': '2026-08-19 15:20:30', 'stale': False,
            'generated_at': '2026-08-19 15:21',
        }
        payload.update(overrides)
        return payload

    def _workbook(self, payload):
        from openpyxl import load_workbook
        return load_workbook(BytesIO(self.ns['_szov_chat_wallboard_workbook'](payload)))

    def _today(self):
        return self._payload([_export_day(self.ns, '2026-08-19', chats_open=12, partial_last=True)])

    def _period(self):
        return self._payload([
            _export_day(self.ns, '2026-08-17', truncated=True, unmatched=['Посторонний Оператор']),
            _export_day(self.ns, '2026-08-18'),
            _export_day(self.ns, '2026-08-19', chats_open=12, partial_last=True),
        ], stale=True, age_seconds=420, error='Chat2Desk не отвечает')

    def _find_row(self, sheet, label):
        for row in sheet.iter_rows(min_col=1, max_col=3, values_only=True):
            if row and row[0] == label:
                return row
        raise AssertionError(f'строка «{label}» не найдена')

    def test_minutes_keep_the_gap_between_no_data_and_zero(self):
        """None -> пустая ячейка, а не ноль: иначе «мерить было нечего» станет идеалом."""
        minutes = self.ns['_szov_chat_export_minutes']
        self.assertIsNone(minutes(None))
        self.assertIsNone(minutes('—'))
        self.assertEqual(minutes(0), 0.0)
        self.assertEqual(minutes(150), 2.5)
        self.assertEqual(minutes(13824, 0), 230.0)

    def test_hour_is_labelled_as_an_interval(self):
        """Час — промежуток, как в почасовом отчёте, а полночь замыкает сутки."""
        self.assertEqual(self.ns['_szov_chat_export_hour_label'](12), '12:00–13:00')
        self.assertEqual(self.ns['_szov_chat_export_hour_label'](23), '23:00–00:00')

    def test_single_day_has_no_day_sheet(self):
        """На однодневной выгрузке лист «По дням» повторял бы сводку — его нет."""
        self.assertEqual(self._workbook(self._today()).sheetnames,
                         ['Показатели', 'По часам', 'Люди на линии', 'Чатники'])

    def test_period_adds_the_day_sheet(self):
        self.assertEqual(self._workbook(self._period()).sheetnames,
                         ['Показатели', 'По дням', 'По часам', 'Люди на линии', 'Чатники'])

    def test_shift_sheet_appears_only_with_today_inside(self):
        """Состава смены «сейчас» у прошедшего дня не существует: живой список — только на сейчас."""
        payload = self._payload([_export_day(self.ns, '2026-08-17')], now=None, chat2desk_now=None)
        wb = self._workbook(payload)
        self.assertNotIn('Чатники', wb.sheetnames)
        with self.assertRaises(AssertionError):
            self._find_row(wb['Показатели'], 'Открыто чатов')
        self.assertEqual(self._find_row(wb['Показатели'], 'Дата')[1], '2026-08-17')

    def test_summary_repeats_the_tiles_of_the_board(self):
        sheet = self._workbook(self._today())['Показатели']
        self.assertEqual(self._find_row(sheet, 'Дата')[1], '2026-08-19')
        self.assertEqual(self._find_row(sheet, 'Данные Chat2Desk на')[1], '2026-08-19 15:20:30')
        self.assertEqual(self._find_row(sheet, 'Цель по ответу внутри чата, мин')[1], 2.0)
        self.assertEqual(self._find_row(sheet, 'Онлайн')[1], 3)
        self.assertEqual(self._find_row(sheet, 'Не в системе')[1], 4)
        self.assertEqual(self._find_row(sheet, 'Открыто чатов')[1], 12)
        self.assertEqual(self._find_row(sheet, 'Чатов')[1], 46)
        self.assertEqual(self._find_row(sheet, 'Открыто сейчас')[1], 12)
        self.assertEqual(self._find_row(sheet, 'Ответ внутри чата, мин')[1], 2.5)
        # Цель в пояснении — по-русски и без «,0»: файл читают глазами, а не только формулами.
        self.assertEqual(self._find_row(sheet, 'Ответ внутри чата, мин')[2],
                         'Цель — не больше 2 мин')
        self.assertEqual(self._find_row(sheet, 'Первый ответ, мин')[1], 1.5)
        # Измеренных часов три (09, 10, 15), в цель уложились два.
        self.assertEqual(self._find_row(sheet, 'Часов измерено')[1], 3)
        self.assertEqual(self._find_row(sheet, 'Часов в цели')[1], 2)

    def test_summary_counts_people_as_heads(self):
        """Людей — головами: трое разных за день и трое в самом плотном часу."""
        sheet = self._workbook(self._today())['Показатели']
        self.assertEqual(self._find_row(sheet, 'Людей на линии')[1], 3)
        self.assertEqual(self._find_row(sheet, 'Максимум людей в часе')[1], 3)
        self.assertEqual(self._find_row(sheet, 'На линии, мин')[1], 255.0)

    def test_period_averages_go_by_requests_not_by_days(self):
        """Среднее за период считается по обращениям всех дней, а не как среднее средних."""
        days = [
            _export_day(self.ns, '2026-08-17'),
            _export_day(self.ns, '2026-08-18'),
        ]
        # Второй день: вдвое больше обращений и ответ по 4 минуты. Среднее средних дало бы
        # 3,25 мин, среднее по обращениям — 3,5.
        days[1]['reply_sums'] = {'first_sum': 100.0, 'first_count': 1,
                                 'inner_sum': 240.0 * 92, 'inner_count': 92}
        days[1]['inner_reply_seconds'] = 240.0
        sheet = self._workbook(self._payload(days))['Показатели']
        self.assertEqual(self._find_row(sheet, 'Ответ внутри чата, мин')[1], 3.5)
        self.assertEqual(self._find_row(sheet, 'Дней в периоде')[1], 2)

    def test_hours_carry_numbers_and_names(self):
        """По файлу считают дальше: минуты — числа, люди — целые, состав часа — ФИО."""
        sheet = self._workbook(self._today())['По часам']
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header, ['Час', 'Чатов', 'Ответ внутри чата, мин', 'Первый ответ, мин',
                                  'В цели', 'Людей на линии', 'На линии, мин', 'Кто на линии',
                                  'Примечание'])
        first = [cell.value for cell in sheet[2]]
        self.assertEqual(first[0], '09:00–10:00')
        self.assertEqual(first[1:7], [10, 1.5, 1.0, 'да', 2, 90.0])
        self.assertEqual(first[7], f'{ALIA}, {DANA}')
        self.assertEqual(first[8], 'ещё 1 меньше минуты')

    def test_hour_without_answers_stays_empty_and_current_hour_is_marked(self):
        sheet = self._workbook(self._today())['По часам']
        empty = [cell.value for cell in sheet[4]]
        self.assertIsNone(empty[2])
        self.assertIsNone(empty[3])
        self.assertFalse(empty[4])
        self.assertEqual(empty[5], 0)
        current = [cell.value for cell in sheet[5]]
        self.assertEqual(current[4], 'да')  # 105 c против цели 120 c
        self.assertEqual(current[8], 'час идёт')

    def test_daily_total_row_keeps_heads_out_of_the_sum(self):
        """В итоге по людям стоит максимум в часе: суммировать головы разных часов нельзя."""
        sheet = self._workbook(self._today())['По часам']
        total = [cell.value for cell in sheet[6]]
        self.assertEqual(total[0], 'За сутки')
        self.assertEqual(total[1], 46)
        self.assertEqual(total[2], 2.5)
        self.assertEqual(total[4], '2 из 3')
        self.assertEqual(total[5], 3)
        self.assertEqual(total[7], 'разных людей: 3')
        # Фильтр стоит только по часам: строка итога под ним.
        self.assertEqual(sheet.auto_filter.ref, 'A1:I5')

    def test_period_hours_carry_the_day(self):
        sheet = self._workbook(self._period())['По часам']
        self.assertEqual([cell.value for cell in sheet[1]][0], 'День')
        self.assertEqual([cell.value for cell in sheet[2]][0], '2026-08-17')
        self.assertEqual([cell.value for cell in sheet[6]][0], '2026-08-18')
        total = [cell.value for cell in sheet[14]]
        self.assertEqual(total[0], '2026-08-17 — 2026-08-19')
        self.assertEqual(total[1], 'За период')

    def test_day_sheet_lists_every_day_with_its_note(self):
        sheet = self._workbook(self._period())['По дням']
        rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2, max_col=9)]
        self.assertEqual([row[0] for row in rows], ['2026-08-17', '2026-08-18', '2026-08-19'])
        self.assertEqual(rows[0][1:8], [46, 2.5, 1.5, '2 из 3', 3, 3, 255.0])
        self.assertEqual(rows[0][8], 'события обрезаны')
        self.assertFalse(rows[1][8])

    def test_failed_day_says_why_instead_of_showing_zeros(self):
        """День не дался — в файле причина, а не нули: нули прочитались бы как простой смены."""
        payload = self._payload([
            self.ns['_szov_chat_export_finish_day']('2026-08-17', [], {},
                                                    error='Chat2Desk HTTP 500'),
            _export_day(self.ns, '2026-08-19', chats_open=12),
        ])
        wb = self._workbook(payload)
        notes = [row[1] for row in wb['Показатели'].iter_rows(min_col=1, max_col=2, values_only=True)
                 if row and row[0] == 'Внимание']
        self.assertTrue(any('2026-08-17' in str(note) and 'HTTP 500' in str(note) for note in notes))
        day_rows = [[cell.value for cell in row] for row in wb['По дням'].iter_rows(min_row=2, max_col=9)]
        self.assertEqual(day_rows[0][1], 0)
        self.assertIn('HTTP 500', day_rows[0][8])

    def test_people_sheet_sums_the_line_time(self):
        """Лист «Люди на линии»: сверху тот, кто держал линию дольше всех."""
        sheet = self._workbook(self._today())['Люди на линии']
        self.assertEqual([cell.value for cell in sheet[1]],
                         ['Сотрудник', 'Часов на линии', 'На линии, мин'])
        rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2, max_col=3)]
        self.assertEqual(rows[0], [ALIA, 3, 150.0])
        self.assertEqual(rows[1], [BEK, 1, 60.0])
        self.assertEqual(rows[2], [DANA, 2, 45.0])

    def test_people_sheet_counts_days_on_a_period(self):
        sheet = self._workbook(self._period())['Люди на линии']
        self.assertEqual([cell.value for cell in sheet[1]][3], 'Дней на линии')
        rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2, max_col=4)]
        self.assertEqual(rows[0], [ALIA, 9, 450.0, 3])

    def test_day_people_follow_the_same_threshold_as_hours(self):
        """Атом счёта один: день складывается из тех же людей, что стоят в подсказке часа."""
        people = self.ns['_szov_chat_export_day_people']([
            _export_hour(9, 0, None, None, [(ALIA, 3600)], under=2),
            _export_hour(10, 0, None, None, [(ALIA, 600), (DANA, 1800)]),
        ])
        self.assertEqual(people, {ALIA: 4200, DANA: 1800})

    def test_shift_sheet_keeps_the_since_midnight_wording(self):
        """seconds=None — человек в статусе с начала суток; «0 мин» тут соврало бы."""
        sheet = self._workbook(self._today())['Чатники']
        rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2, max_col=4)]
        self.assertEqual(rows[0], [ALIA, 'Онлайн', 30.0, 7])
        self.assertEqual(rows[1], [DANA, 'Онлайн', 'с начала суток', 5])
        self.assertEqual(rows[2], ['Учебный Ерлан Тренингулы', 'Тренинг', 10.0, 0])
        # Вышедшие в список не идут, но их число видно — иначе смена выглядит меньше.
        self.assertIn('Не в системе: 4',
                      [cell.value for cell in sheet['A'] if isinstance(cell.value, str)])

    def test_status_colour_matches_the_chip_on_the_board(self):
        sheet = self._workbook(self._today())['Чатники']
        self.assertIn(self.ns['_SZOV_CHAT_EXPORT_STATUS_FILL']['online'],
                      str(sheet.cell(row=2, column=2).fill.fgColor.rgb))
        self.assertIn(self.ns['_SZOV_CHAT_EXPORT_STATUS_FILL']['training'],
                      str(sheet.cell(row=4, column=2).fill.fgColor.rgb))

    def test_file_warns_about_stale_and_truncated_data(self):
        """Оговорки живут В ФАЙЛЕ: он уходит в переписку без экрана, с которого его сняли."""
        sheet = self._workbook(self._period())['Показатели']
        notes = [row[1] for row in sheet.iter_rows(min_col=1, max_col=2, values_only=True)
                 if row and row[0] == 'Внимание']
        self.assertEqual(len(notes), 3)
        self.assertIn('7 мин', notes[0])
        self.assertIn('Chat2Desk не отвечает', notes[0])
        self.assertIn('обрезаны', notes[1])
        self.assertIn('2026-08-17', notes[1])
        self.assertIn('Посторонний Оператор', notes[2])

    def test_clean_snapshot_has_no_warnings(self):
        """Шум = брак: пока всё в порядке, строк «Внимание» в файле нет."""
        sheet = self._workbook(self._today())['Показатели']
        self.assertEqual([row[0] for row in sheet.iter_rows(min_col=1, max_col=1, values_only=True)
                          if row and row[0] == 'Внимание'], [])

    def test_empty_day_does_not_break_the_file(self):
        """Ночь, чатов ещё нет: файл собирается, а не падает на пустых списках."""
        payload = self._payload(
            [self.ns['_szov_chat_export_finish_day']('2026-08-19', [], {'chats': 0})],
            now={'operators_online': 0, 'operators_offline': 0, 'open_chats': 0, 'operators': []})
        wb = self._workbook(payload)
        self.assertEqual([cell.value for cell in wb['По часам'][2]][0], 'За сутки')
        self.assertIsNone(self._find_row(wb['Показатели'], 'Ответ внутри чата, мин')[1])
        self.assertEqual(wb['Люди на линии'].cell(row=2, column=1).value,
                         'Никто не выходил на линию')

if __name__ == '__main__':
    unittest.main()
