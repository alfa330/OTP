"""Почасовой отчёт по чатам: подсчёт чатов, время ответа, список онлайн, экономия квоты.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном namespace —
так проверяется настоящая логика. Импортировать модуль нельзя: на старте он поднимает пул
к боевой БД (см. тот же приём в test_szov_wallboard.py).
"""
import ast
import io
import logging
import os
import re
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")

NAMES = {
    '_env_int',
    '_chat2desk_row_first', '_chat2desk_operator_display_name',
    '_chat2desk_extract_response_rows', '_chat2desk_extract_total',
    '_szov_plural', '_szov_format_seconds_mmss',
    'CHAT_HOURLY_TIMEZONE', 'CHAT_HOURLY_REQUEST_TYPE', 'CHAT_HOURLY_STATUS_LABELS',
    'CHAT_HOURLY_CACHE_SECONDS', 'CHAT_HOURLY_BROADCAST_HOURS', 'CHAT_HOURLY_BROADCAST_MINUTE',
    '_chat_hourly_requests_cache', '_chat_hourly_report_cache', '_chat_hourly_lock',
    '_chat_hourly_number', '_chat_hourly_is_open', '_chat_hourly_request_start',
    '_chat_hourly_operator_name', '_chat_hourly_fetch_requests',
    '_chat_hourly_operator_states', '_chat_hourly_online',
    '_chat_hourly_response_times', '_chat_hourly_collect', '_chat_hourly_report',
    '_chat_hourly_seconds', '_chat_hourly_text', '_chat_hourly_caption',
    'CHAT_HOURLY_PRESENCE_ORDER', '_CHAT_HOURLY_PRESENCE_RANK', 'CHAT_HOURLY_PRESENCE_COLORS',
    '_CHAT_HOURLY_TABLE_COLUMNS', '_CHAT_HOURLY_HOUR_COLUMN_KEYS',
    '_chat_hourly_table_columns', '_chat_hourly_table_row',
}


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _FakeChat2Desk:
    """Отдаёт request_stats страницами по убыванию request_start, как настоящий отчёт."""

    def __init__(self, rows, limit=2):
        self.rows = sorted(rows, key=lambda r: r.get('request_start') or '', reverse=True)
        self.limit = limit
        self.pages = 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.pages += 1
        offset = int((params or {}).get('offset') or 0)
        chunk = self.rows[offset:offset + self.limit]
        return _FakeResponse({'data': chunk, 'meta': {'total': len(self.rows)}})


def _namespace(fake_requests=None):
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
    ns = {
        'os': os, 're': re, 'io': io, 'time': time, 'logging': logging,
        'threading': threading, 'datetime': datetime, 'timedelta': timedelta,
        'ZoneInfo': ZoneInfo,
        'requests': fake_requests,
        '_chat2desk_authorization_header': lambda: 'token',
        '_chat2desk_api_base_url': lambda: 'https://chat2desk.test',
        '_chat2desk_api_error_message': lambda response, report, day: 'ошибка Chat2Desk',
        'CHAT2DESK_API_PAGE_LIMIT': 2,
        'CHAT2DESK_API_MAX_PAGES': 50,
        'CHAT2DESK_API_TIMEOUT_SECONDS': 30,
        'CHAT2DESK_STATISTICS_REPORT_REQUEST_STATS': 'request_stats',
    }
    exec(compile(module, "<chat-hourly>", "exec"), ns)
    missing = sorted(name for name in NAMES if name not in ns)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    ns['_chat_hourly_requests_cache'].update(day=None, rows={})
    ns['_chat_hourly_report_cache'].update(ts=0.0, payload=None)
    return ns


def _request(request_id, start, end='', operator='Оператор', reaction=None,
             replies=0, total=None, request_type='common'):
    return {
        'request_id': request_id,
        'request_type': request_type,
        'request_start': start,
        'request_end': end,
        'operator_name': operator,
        'reaction_time': '' if reaction is None else reaction,
        'replies': replies,
        'total_replies_time': '' if total is None else total,
    }


class ChatHourlyResponseTimeTests(unittest.TestCase):
    """Первый ответ и ответ внутри чата — разные цифры, и вторая считается без первого."""

    def setUp(self):
        self.ns = _namespace()

    def test_first_reply_is_the_plain_average_of_reaction_time(self):
        """Так же считает веб-отчёт Chat2Desk, по которому владелец сверяется."""
        rows = [_request(1, '2026-08-07 10:00:00', reaction=10, replies=1, total=10),
                _request(2, '2026-08-07 10:05:00', reaction=30, replies=1, total=30)]
        first, _inner = self.ns['_chat_hourly_response_times'](rows)
        self.assertEqual(first, 20)

    def test_inner_reply_excludes_the_first_answer(self):
        """total_replies_time включает первый ответ — иначе получилось бы то же самое число."""
        # 3 ответа, суммарно 97 с, из них первый — 2 с. Внутри чата: (97-2)/2 = 47,5.
        rows = [_request(1, '2026-08-07 10:00:00', reaction=2, replies=3, total=97)]
        first, inner = self.ns['_chat_hourly_response_times'](rows)
        self.assertEqual(first, 2)
        self.assertEqual(inner, 47.5)

    def test_single_reply_chats_do_not_affect_the_inner_average(self):
        """Чат с единственным ответом ответов «внутри» не содержит вовсе."""
        rows = [_request(1, '2026-08-07 10:00:00', reaction=5, replies=1, total=5),
                _request(2, '2026-08-07 10:10:00', reaction=10, replies=3, total=130)]
        _first, inner = self.ns['_chat_hourly_response_times'](rows)
        self.assertEqual(inner, 60)

    def test_inner_average_is_weighted_by_replies(self):
        """Средним от средних длинный диалог весил бы столько же, сколько короткий."""
        rows = [_request(1, '2026-08-07 10:00:00', reaction=0, replies=2, total=100),
                _request(2, '2026-08-07 10:10:00', reaction=0, replies=11, total=100)]
        _first, inner = self.ns['_chat_hourly_response_times'](rows)
        self.assertEqual(inner, 200 / 11)

    def test_missing_numbers_are_not_zeros(self):
        """Пустая строка в Chat2Desk означает «нет данных» — нулём её считать нельзя."""
        rows = [_request(1, '2026-08-07 10:00:00', reaction=None, replies=0, total=None)]
        self.assertEqual(self.ns['_chat_hourly_response_times'](rows), (None, None))


class ChatHourlyFetchTests(unittest.TestCase):
    """Выгрузка обращений: фильтр по типу и экономия квоты Chat2Desk."""

    def _fetch(self, rows, limit=2):
        api = _FakeChat2Desk(rows, limit=limit)
        ns = _namespace(fake_requests=api)
        return ns, api

    def test_rating_requests_are_not_chats(self):
        """request_type='rating' — автоматический опрос после диалога, а не чат."""
        ns, _api = self._fetch([
            _request(1, '2026-08-07 10:00:00'),
            _request(2, '2026-08-07 10:01:00', request_type='rating'),
        ])
        rows = ns['_chat_hourly_fetch_requests']('2026-08-07')
        self.assertEqual([r['request_id'] for r in rows], [1])

    def test_first_run_reads_the_whole_day(self):
        ns, api = self._fetch([_request(i, '2026-08-07 %02d:00:00' % i) for i in range(1, 7)])
        rows = ns['_chat_hourly_fetch_requests']('2026-08-07')
        self.assertEqual(len(rows), 6)
        self.assertEqual(api.pages, 3)

    def test_second_run_stops_at_the_oldest_open_request(self):
        """Закрытое обращение больше не меняется — глубже за ним листать незачем."""
        rows = [_request(i, '2026-08-07 %02d:00:00' % i, end='2026-08-07 %02d:30:00' % i)
                for i in range(1, 7)]
        rows[-1] = _request(6, '2026-08-07 06:00:00')  # самое свежее ещё открыто
        ns, api = self._fetch(rows)
        ns['_chat_hourly_fetch_requests']('2026-08-07')
        self.assertEqual(api.pages, 3)
        api.pages = 0
        again = ns['_chat_hourly_fetch_requests']('2026-08-07')
        self.assertEqual(len(again), 6)
        # хватило одной страницы: она уже уходит ниже открытого обращения на 06:00
        self.assertEqual(api.pages, 1)

    def test_second_run_stops_when_a_closed_day_brings_nothing_new(self):
        """Все обращения закрыты — меняться нечему, новые лежат сверху."""
        rows = [_request(i, '2026-08-07 %02d:00:00' % i, end='2026-08-07 %02d:30:00' % i)
                for i in range(1, 7)]
        ns, api = self._fetch(rows)
        ns['_chat_hourly_fetch_requests']('2026-08-07')
        self.assertEqual(api.pages, 3)
        api.pages = 0
        again = ns['_chat_hourly_fetch_requests']('2026-08-07')
        self.assertEqual(len(again), 6)
        self.assertEqual(api.pages, 1)

    def test_reopened_day_page_updates_a_request_that_has_closed(self):
        """Обращение, закрывшееся между заходами, обязано приехать с временем ответа."""
        ns, api = self._fetch([_request(1, '2026-08-07 09:00:00'),
                               _request(2, '2026-08-07 10:00:00')])
        ns['_chat_hourly_fetch_requests']('2026-08-07')
        api.rows[0] = _request(2, '2026-08-07 10:00:00', end='2026-08-07 10:05:00',
                               reaction=12, replies=1, total=12)
        rows = {r['request_id']: r for r in ns['_chat_hourly_fetch_requests']('2026-08-07')}
        self.assertEqual(rows[2]['reaction_time'], 12)
        self.assertFalse(ns['_chat_hourly_is_open'](rows[2]))

    def test_new_day_drops_the_cache(self):
        ns, api = self._fetch([_request(1, '2026-08-07 10:00:00', end='2026-08-07 10:05:00')])
        ns['_chat_hourly_fetch_requests']('2026-08-07')
        api.rows = [_request(9, '2026-08-08 09:00:00')]
        rows = ns['_chat_hourly_fetch_requests']('2026-08-08')
        self.assertEqual([r['request_id'] for r in rows], [9])


class ChatHourlyOnlineTests(unittest.TestCase):
    """Статусы из /v1/operators: живые учётки, «онлайн» отдельно от «на статусе»."""

    OPERATORS = [
        {'first_name': 'Асан', 'last_name': 'Тестбаев', 'status': 'enabled',
         'online': 1, 'offline_type': None, 'opened_dialogs': 61},
        {'first_name': 'Бекзат', 'last_name': 'Сынакбай', 'status': 'enabled',
         'online': 1, 'offline_type': '', 'opened_dialogs': 60},
        {'first_name': 'Ерсын', 'last_name': 'Досанбаев', 'status': 'enabled',
         'online': 1, 'offline_type': 'holiday', 'opened_dialogs': 15},
        {'first_name': 'Мади', 'last_name': 'Кыдырбай', 'status': 'enabled',
         'online': 0, 'offline_type': None, 'opened_dialogs': 0},
        {'first_name': 'Администратор', 'last_name': 'sales', 'status': 'admin',
         'online': 1, 'offline_type': None, 'opened_dialogs': 0},
        {'first_name': 'Уволенный', 'last_name': 'Оператор', 'status': 'deleted',
         'online': 1, 'offline_type': 'busy', 'opened_dialogs': 0},
    ]

    def setUp(self):
        ns = _namespace()
        self.states = ns['_chat_hourly_operator_states'](self.OPERATORS)
        self.online, self.on_status = ns['_chat_hourly_online'](self.states)

    def test_dead_accounts_never_appear(self):
        """Удалённых и админскую учётку не берём: они висят online от последнего входа."""
        self.assertEqual(set(self.states),
                         {'Асан Тестбаев', 'Бекзат Сынакбай',
                          'Ерсын Досанбаев', 'Мади Кыдырбай'})

    def test_only_logged_in_and_free_operators_are_online(self):
        self.assertEqual([item['name'] for item in self.online],
                         ['Асан Тестбаев', 'Бекзат Сынакбай'])

    def test_operators_on_a_status_are_listed_apart(self):
        """«Онлайн» на табло — это свободные и в разговоре, перерыв туда не входит."""
        self.assertEqual(self.on_status, [{'name': 'Ерсын Досанбаев', 'status': 'отпуск'}])

    def test_status_labels_are_russian(self):
        self.assertEqual(self.states['Ерсын Досанбаев']['status'], 'отпуск')
        self.assertEqual(self.states['Асан Тестбаев']['status'], 'онлайн')
        self.assertEqual(self.states['Мади Кыдырбай']['status'], 'офлайн')

    def test_presence_splits_online_status_and_offline(self):
        """По присутствию строятся и порядок строк, и цвет — оно должно быть явным полем."""
        self.assertEqual(self.states['Асан Тестбаев']['presence'], 'online')
        self.assertEqual(self.states['Ерсын Досанбаев']['presence'], 'status')
        self.assertEqual(self.states['Мади Кыдырбай']['presence'], 'offline')

    def test_open_chats_are_kept_per_operator(self):
        self.assertEqual(self.states['Асан Тестбаев']['open_chats'], 61)


class ChatHourlyCollectTests(unittest.TestCase):
    """Сборка отчёта: час считается по закончившемуся часу, а не по начавшейся минуте."""

    ROWS = [
        _request(1, '2026-08-07 09:30:00', end='2026-08-07 09:40:00',
                 operator='Алихан', reaction=10, replies=1, total=10),
        _request(2, '2026-08-07 10:10:00', end='2026-08-07 10:20:00',
                 operator='Алихан', reaction=20, replies=3, total=80),
        _request(3, '2026-08-07 10:50:00', operator='Ерланов', reaction=30, replies=2, total=90),
        _request(4, '2026-08-07 11:05:00', operator='Ерланов', reaction=40, replies=1, total=40),
    ]

    def _collect(self, at='2026-08-07 11:00:00'):
        api = _FakeChat2Desk(self.ROWS, limit=10)
        ns = _namespace(fake_requests=api)
        ns['_chat_hourly_fetch_operators'] = lambda: []
        now = datetime.strptime(at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Almaty'))
        return ns, ns['_chat_hourly_collect'](now=now)

    def test_hour_window_is_the_finished_hour(self):
        _ns, data = self._collect()
        self.assertEqual(data['hour_label'], '10:00–11:00')
        self.assertEqual(data['chats_hour'], 2)
        self.assertEqual(data['chats_day'], 4)

    def test_open_chats_are_those_without_an_end(self):
        _ns, data = self._collect()
        self.assertEqual(data['chats_open'], 2)

    def test_operators_are_ranked_by_chats_with_the_hour_alongside(self):
        _ns, data = self._collect()
        self.assertEqual([(o['name'], o['chats'], o['chats_hour']) for o in data['operators']],
                         [('Алихан', 2, 1), ('Ерланов', 2, 1)])

    def test_response_times_are_computed_per_operator(self):
        """Время ответа по чатнику считается теми же формулами, что и итог."""
        _ns, data = self._collect()
        by_name = {o['name']: o for o in data['operators']}
        # Алихан: заявки 1 и 2 → первый ответ (10+20)/2 = 15, внутри чата (80-20)/2 = 30
        self.assertEqual(by_name['Алихан']['first_reply_day'], 15)
        self.assertEqual(by_name['Алихан']['inner_reply_day'], 30)
        # Ерланов: заявки 3 и 4 → (30+40)/2 = 35, внутри чата (90-30)/1 = 60
        self.assertEqual(by_name['Ерланов']['first_reply_day'], 35)
        self.assertEqual(by_name['Ерланов']['inner_reply_day'], 60)
        # за час у Алихана только заявка 2
        self.assertEqual(by_name['Алихан']['first_reply_hour'], 20)

    def test_operator_row_carries_status_and_open_chats(self):
        api = _FakeChat2Desk(self.ROWS, limit=10)
        ns = _namespace(fake_requests=api)
        ns['_chat_hourly_fetch_operators'] = lambda: [
            {'first_name': 'Ерланов', 'last_name': '', 'status': 'enabled',
             'online': 1, 'offline_type': None, 'opened_dialogs': 12},
        ]
        now = datetime(2026, 8, 7, 11, 0, tzinfo=ZoneInfo('Asia/Almaty'))
        by_name = {o['name']: o for o in ns['_chat_hourly_collect'](now=now)['operators']}
        self.assertEqual(by_name['Ерланов']['status'], 'онлайн')
        self.assertEqual(by_name['Ерланов']['open_chats'], 12)
        # у того, кого нет в Chat2Desk, статуса и открытых чатов просто нет
        self.assertEqual(by_name['Алихан']['status'], '—')
        self.assertIsNone(by_name['Алихан']['open_chats'])

    def test_online_operator_without_chats_is_still_a_row(self):
        """Иначе «кто онлайн» пришлось бы держать отдельным списком рядом с таблицей."""
        api = _FakeChat2Desk(self.ROWS, limit=10)
        ns = _namespace(fake_requests=api)
        ns['_chat_hourly_fetch_operators'] = lambda: [
            {'first_name': 'Новичок', 'last_name': '', 'status': 'enabled',
             'online': 1, 'offline_type': None, 'opened_dialogs': 0},
        ]
        now = datetime(2026, 8, 7, 11, 0, tzinfo=ZoneInfo('Asia/Almaty'))
        data = ns['_chat_hourly_collect'](now=now)
        row = next(o for o in data['operators'] if o['name'] == 'Новичок')
        self.assertEqual(row['chats'], 0)
        self.assertEqual(row['status'], 'онлайн')

    def test_rows_go_online_then_status_then_offline(self):
        """Владелец просил видеть в первых строках тех, кто держит линию прямо сейчас."""
        api = _FakeChat2Desk(self.ROWS, limit=10)
        ns = _namespace(fake_requests=api)
        ns['_chat_hourly_fetch_operators'] = lambda: [
            # у «Алихана» чатов больше, но он офлайн и обязан уехать вниз
            {'first_name': 'Алихан', 'last_name': '', 'status': 'enabled',
             'online': 0, 'offline_type': None, 'opened_dialogs': 0},
            {'first_name': 'Ерланов', 'last_name': '', 'status': 'enabled',
             'online': 1, 'offline_type': 'break', 'opened_dialogs': 3},
            {'first_name': 'Новичок', 'last_name': '', 'status': 'enabled',
             'online': 1, 'offline_type': None, 'opened_dialogs': 1},
        ]
        now = datetime(2026, 8, 7, 11, 0, tzinfo=ZoneInfo('Asia/Almaty'))
        data = ns['_chat_hourly_collect'](now=now)
        self.assertEqual([o['name'] for o in data['operators']],
                         ['Новичок', 'Ерланов', 'Алихан'])
        self.assertEqual([o['presence'] for o in data['operators']],
                         ['online', 'status', 'offline'])

    def test_hour_and_day_response_times_differ(self):
        _ns, data = self._collect()
        self.assertEqual(data['first_reply_day'], 25)     # (10+20+30+40)/4
        self.assertEqual(data['first_reply_hour'], 25)    # (20+30)/2
        self.assertEqual(data['inner_reply_day'], 40)     # ((80-20)+(90-30))/(2+1)
        self.assertEqual(data['inner_reply_hour'], 40)

    def test_midnight_report_has_no_hour_block(self):
        """В 00:00 прошедший час — уже вчерашние сутки, отдельной строкой его не показываем."""
        _ns, data = self._collect(at='2026-08-07 00:00:00')
        self.assertIsNone(data['hour_label'])

    def test_operator_list_failure_does_not_lose_the_numbers(self):
        api = _FakeChat2Desk(self.ROWS, limit=10)
        ns = _namespace(fake_requests=api)

        def boom():
            raise RuntimeError("Chat2Desk 500")

        ns['_chat_hourly_fetch_operators'] = boom
        now = datetime(2026, 8, 7, 11, 0, tzinfo=ZoneInfo('Asia/Almaty'))
        data = ns['_chat_hourly_collect'](now=now)
        self.assertEqual(data['chats_day'], 4)
        self.assertTrue(data['online_error'])
        self.assertEqual(data['online'], [])


class ChatHourlyTextTests(unittest.TestCase):
    """Текст отчёта: все пять запрошенных владельцем показателей на месте."""

    DATA = {
        'generated_at': '07.08.2026 11:00',
        'day': '2026-08-07',
        'hour_label': '10:00–11:00',
        'chats_day': 337, 'chats_hour': 52, 'chats_open': 133,
        'first_reply_day': 54, 'first_reply_hour': 21,
        'inner_reply_day': 170, 'inner_reply_hour': 290,
        'operators': [{'name': 'Бекзат Сынакбай', 'status': 'онлайн', 'chats': 73,
                       'chats_hour': 17, 'first_reply_day': 33, 'first_reply_hour': 12,
                       'inner_reply_day': 190, 'inner_reply_hour': 210, 'open_chats': 63},
                      {'name': 'Нурлан Айтбай', 'status': 'офлайн', 'chats': 13,
                       'chats_hour': 0, 'first_reply_day': 96, 'first_reply_hour': None,
                       'inner_reply_day': None, 'inner_reply_hour': None, 'open_chats': 0}],
        'online': [{'name': 'Асан Тестбаев', 'open_chats': 63}],
        'on_status': [{'name': 'Карим Тестов', 'status': 'перерыв'}],
        'online_error': None,
    }

    def setUp(self):
        self.text = _namespace()['_chat_hourly_text'](self.DATA)

    def test_all_five_metrics_are_present(self):
        self.assertIn('<b>Количество чатов:</b> 52 за 10:00–11:00, 337 с начала суток', self.text)
        self.assertIn('<b>Открыто сейчас:</b> 133', self.text)
        self.assertIn('<b>Среднее время первого ответа:</b> 0:21 (за час), 0:54 (сутки)', self.text)
        self.assertIn('<b>Среднее время ответа внутри чата:</b> 4:50 (за час), 2:50 (сутки)', self.text)
        self.assertIn('<b>Чатники за сутки (2):</b>', self.text)
        self.assertIn('<b>Онлайн (1):</b>', self.text)

    def test_operator_hour_count_is_hidden_when_it_is_zero(self):
        """«(за час 0)» у каждого второго — это шум, а не информация."""
        self.assertIn('• Бекзат Сынакбай — 73 (за час 17),', self.text)
        self.assertIn('• Нурлан Айтбай — 13, первый ответ 1:36', self.text)

    def test_response_times_are_broken_down_per_operator(self):
        """Владелец просил видеть время ответа не только итогом, но и по чатникам."""
        self.assertIn('• Бекзат Сынакбай — 73 (за час 17), '
                      'первый ответ 0:33, внутри чата 3:10', self.text)

    def test_status_operators_are_named_with_their_status(self):
        self.assertIn('<b>На статусе:</b> Карим Тестов — перерыв', self.text)

    def test_missing_numbers_are_dashes_not_zeros(self):
        data = dict(self.DATA, first_reply_hour=None, inner_reply_hour=None)
        text = _namespace()['_chat_hourly_text'](data)
        self.assertIn('<b>Среднее время первого ответа:</b> — (за час), 0:54 (сутки)', text)

    def test_empty_day_says_so(self):
        data = dict(self.DATA, operators=[], online=[], on_status=[])
        text = _namespace()['_chat_hourly_text'](data)
        self.assertIn('чатов пока не было', text)
        self.assertIn('<b>Онлайн:</b> никого', text)


class ChatHourlyTableTests(unittest.TestCase):
    """Таблица-картинка: набор колонок и что попадает в ячейки."""

    OPERATOR = {
        'name': 'Бекзат Сынакбай', 'status': 'онлайн',
        'chats': 82, 'chats_hour': 30,
        'first_reply_day': 93, 'first_reply_hour': 99,
        'inner_reply_day': 848, 'inner_reply_hour': 1625,
        'open_chats': 70,
    }

    def setUp(self):
        self.ns = _namespace()

    def test_hour_columns_disappear_in_the_midnight_report(self):
        """В отчёте за 00:00 часа нет — пустые колонки показывать нечестно."""
        with_hour = [c[0] for c in self.ns['_chat_hourly_table_columns'](True)]
        without = [c[0] for c in self.ns['_chat_hourly_table_columns'](False)]
        self.assertIn('chats_hour', with_hour)
        self.assertNotIn('chats_hour', without)
        self.assertNotIn('first_hour', without)
        self.assertNotIn('inner_hour', without)
        self.assertIn('chats', without)
        self.assertIn('first_day', without)

    def test_both_response_times_are_columns_for_hour_and_day(self):
        """Ровно то, что просил владелец: время ответа в разрезе по операторам."""
        keys = [c[0] for c in self.ns['_chat_hourly_table_columns'](True)]
        for key in ('first_hour', 'inner_hour', 'first_day', 'inner_day'):
            self.assertIn(key, keys)

    def test_cells_show_minutes_and_seconds(self):
        cells = self.ns['_chat_hourly_table_row'](self.OPERATOR)
        self.assertEqual(cells['first_day'], '1:33')
        self.assertEqual(cells['inner_day'], '14:08')
        self.assertEqual(cells['first_hour'], '1:39')
        self.assertEqual(cells['inner_hour'], '27:05')
        self.assertEqual(cells['chats'], '82')
        self.assertEqual(cells['open_chats'], '70')
        self.assertEqual(cells['status'], 'онлайн')

    def test_zero_and_missing_are_dashes(self):
        """Колонка из нулей — визуальный шум, значимые числа в ней теряются."""
        cells = self.ns['_chat_hourly_table_row']({
            'name': 'Нурлан Айтбай', 'status': 'офлайн',
            'chats': 13, 'chats_hour': 0,
            'first_reply_day': 36, 'first_reply_hour': None,
            'inner_reply_day': None, 'inner_reply_hour': None,
            'open_chats': 0,
        })
        self.assertEqual(cells['chats_hour'], '—')
        self.assertEqual(cells['first_hour'], '—')
        self.assertEqual(cells['inner_day'], '—')
        self.assertEqual(cells['open_chats'], '—')
        self.assertEqual(cells['first_day'], '0:36')

    def test_unknown_operator_has_no_status(self):
        cells = self.ns['_chat_hourly_table_row']({'name': 'Кто-то', 'chats': 1})
        self.assertEqual(cells['status'], '—')

    def test_only_presence_that_means_something_is_coloured(self):
        """Зелёный — на линии, янтарный — залогинен, но не на ней. Офлайн не красим."""
        colors = self.ns['CHAT_HOURLY_PRESENCE_COLORS']
        self.assertEqual(colors['online'][0], '#d1fae5')
        self.assertEqual(colors['status'][0], '#fef3c7')
        self.assertNotIn('offline', colors)
        self.assertNotIn('unknown', colors)

    def test_presence_order_puts_the_line_first(self):
        self.assertEqual(self.ns['CHAT_HOURLY_PRESENCE_ORDER'],
                         ('online', 'status', 'offline', 'unknown'))

    def test_caption_does_not_repeat_the_table(self):
        """Цифры уже на картинке — дублировать их подписью значит шуметь."""
        caption = self.ns['_chat_hourly_caption']({
            'generated_at': '07.08.2026 12:00', 'hour_label': '11:00–12:00',
            'chats_day': 390, 'chats_hour': 67, 'online_error': None,
        })
        self.assertIn('<b>Чаты</b> — 07.08.2026 12:00, за 11:00–12:00', caption)
        for gone in ('390', '67', 'Открыто', 'Среднее'):
            self.assertNotIn(gone, caption, gone)

    def test_caption_warns_when_statuses_are_missing(self):
        caption = self.ns['_chat_hourly_caption']({
            'generated_at': '07.08.2026 12:00', 'hour_label': None,
            'online_error': 'Chat2Desk 500',
        })
        self.assertIn('Статусы операторов Chat2Desk не отдал', caption)


class ChatHourlyWiringTests(unittest.TestCase):
    """Проводка: подписка, команды, джоба каждый час."""

    def setUp(self):
        self.source = SOURCE
        self.database = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_subscription_table_and_methods_exist(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS chat_hourly_subscriptions", self.database)
        for method in ('add_chat_hourly_subscription', 'remove_chat_hourly_subscription',
                       'get_chat_hourly_subscriptions', 'mark_chat_hourly_subscription_sent'):
            self.assertIn(f"def {method}(", self.database, method)

    def test_commands_are_registered(self):
        for command in ('chats', 'chats_subscribe', 'chats_unsubscribe'):
            self.assertIn(f"@dp.message_handler(commands=['{command}'])", self.source, command)

    def test_job_runs_every_hour(self):
        self.assertIn("id='chat_hourly_broadcast'", self.source)
        self.assertIn("CronTrigger(hour=CHAT_HOURLY_BROADCAST_HOURS, "
                      "minute=CHAT_HOURLY_BROADCAST_MINUTE,", self.source)
        self.assertEqual(_namespace()['CHAT_HOURLY_BROADCAST_HOURS'], '*')

    def test_job_skips_chat2desk_when_nobody_is_subscribed(self):
        """Квота Chat2Desk общая на компанию — почасовая джоба без подписчиков её не тратит."""
        job = self.source[self.source.index("async def chat_hourly_broadcast_job("):]
        job = job[:job.index("\n\n\n")]
        self.assertLess(job.index("if not subscriptions:"), job.index("_chat_hourly_prepare"))

    def test_access_is_not_open_to_everyone(self):
        guard = self.source[self.source.index("def _chat_hourly_access_allowed("):]
        guard = guard[:guard.index("\n\n\n")]
        self.assertIn("_is_admin_role", guard)
        self.assertIn("headed_department_id_for_user", guard)
        self.assertIn("_is_supervisor_role", guard)


if __name__ == '__main__':
    unittest.main()
