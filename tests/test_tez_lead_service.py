# -*- coding: utf-8 -*-
"""Разбор файла базы лидов и пересчёт успешек поверх фейковой БД."""

import io
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tez_lead_service  # noqa: E402
from tez_op_leads import ALMATY_TZ  # noqa: E402


def _xlsx_bytes(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


class ParseLeadsFileTests(unittest.TestCase):
    def test_csv_with_header(self):
        raw = "fio;phone\nТестбаев Сабиржан;77000000107\nСынақов Қуанышбек;87000000106\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "Тестбаев Сабиржан")
        self.assertEqual(rows[0][3], "77000000107")
        self.assertEqual(rows[1][3], "77000000106")   # 8-ка приведена к 7

    def test_csv_comma_delimiter(self):
        raw = "fio,phone\nИванов Иван,+7 701 234 56 78\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(rows[0][3], "77012345678")

    def test_csv_without_header(self):
        """Шапки может не быть — тогда первая колонка ФИО, вторая телефон."""
        raw = "Тестбаев Сабиржан;77000000107\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "77000000107")

    def test_xlsx_with_header(self):
        raw = _xlsx_bytes([["fio", "phone"], ["Тестбаев Сабиржан", "77000000107"]])
        rows = tez_lead_service.parse_leads_file(raw, ".xlsx")
        self.assertEqual(rows[0][1], "Тестбаев Сабиржан")
        self.assertEqual(rows[0][3], "77000000107")

    def test_xlsx_numeric_phone(self):
        """Excel хранит телефон числом — не должно превратиться в 7.7000000107e+10."""
        raw = _xlsx_bytes([["fio", "phone"], ["Тестбаев Сабиржан", 77000000107]])
        rows = tez_lead_service.parse_leads_file(raw, ".xlsx")
        self.assertEqual(rows[0][3], "77000000107")

    def test_invalid_phone_is_kept_as_row(self):
        """Битую строку не выбрасываем: СВ должен увидеть её в отчёте загрузки."""
        raw = "fio;phone\nБез номера;мусор\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][3])

    def test_file_over_row_limit_is_rejected_instead_of_silent_truncation(self):
        original_limit = tez_lead_service.MAX_LEAD_ROWS
        tez_lead_service.MAX_LEAD_ROWS = 2
        try:
            raw = (
                "fio,phone\n"
                "Первый,77010000001\n"
                "Второй,77010000002\n"
                "Третий,77010000003\n"
            ).encode("utf-8")
            with self.assertRaisesRegex(ValueError, "Разделите его на несколько файлов"):
                tez_lead_service.parse_leads_file(raw, ".csv")
        finally:
            tez_lead_service.MAX_LEAD_ROWS = original_limit

    def test_file_at_row_limit_is_accepted(self):
        original_limit = tez_lead_service.MAX_LEAD_ROWS
        tez_lead_service.MAX_LEAD_ROWS = 2
        try:
            raw = (
                "fio,phone\n"
                "Первый,77010000001\n"
                "Второй,77010000002\n"
            ).encode("utf-8")
            rows = tez_lead_service.parse_leads_file(raw, ".csv")
            self.assertEqual(len(rows), 2)
        finally:
            tez_lead_service.MAX_LEAD_ROWS = original_limit

    def test_empty_file(self):
        with self.assertRaises(ValueError):
            tez_lead_service.parse_leads_file(b"", ".csv")

    def test_unsupported_extension(self):
        with self.assertRaises(ValueError):
            tez_lead_service.parse_leads_file(b"x", ".txt")


class FakeDb:
    """Минимальная замена Database для проверки пересчёта."""

    def __init__(self, leads, successes_before=0):
        self._leads = leads
        self._successes_before = successes_before
        self.applied = None

    def count_tez_successes(self, year, month):
        return self._successes_before

    def get_tez_leads_for_recompute(self, year, month, min_billsec=None):
        return self._leads

    def apply_tez_lead_outcomes(self, year, month, outcomes):
        self.applied = outcomes
        stats = {'success': 0, 'already_working': 0, 'not_counted': 0, 'in_progress': 0, 'new': 0}
        for item in outcomes:
            stats[item['status']] = stats.get(item['status'], 0) + 1
        return stats


class RecomputeOutcomesTests(unittest.TestCase):
    def _lead(self, lead_id, month_first_order_at, calls, prev_month_first_order_at=None):
        return {
            'id': lead_id,
            'phone_norm': '77000000000',
            'full_name': 'Тест',
            'month_first_order_at': month_first_order_at,
            'prev_month_first_order_at': prev_month_first_order_at,
            'calls': calls,
        }

    def test_success_is_written_with_trip_month(self):
        """Звонок 25 июня (последние 7 дней) + поездка 23 июля -> успешка июля.

        День поездки внутри отчётного месяца не ограничен (владелец, 2026-08-04),
        но месяц успешки по-прежнему берётся от поездки, а не от звонка.
        """
        call = {
            'general_call_id': 'g1',
            'started_at': datetime(2026, 6, 25, 10, 0, tzinfo=ALMATY_TZ),
            'call_type': 1, 'billsec': 60, 'operator_id': 7, 'employee_name': 'Оператор ОП',
        }
        lead = self._lead('L1', datetime(2026, 7, 23, 9, 0, tzinfo=ALMATY_TZ), [call])
        db = FakeDb([lead])

        stats = tez_lead_service.recompute_outcomes(db, 2026, 7)

        self.assertEqual(stats['success'], 1)
        item = db.applied[0]
        self.assertEqual(item['operator_id'], 7)
        self.assertEqual(item['operator_name'], 'Оператор ОП')
        self.assertEqual(item['success_year'], 2026)
        self.assertEqual(item['success_month'], 7)
        self.assertEqual(item['success_date'], date(2026, 7, 23))

    def test_active_prev_month_is_already_working(self):
        """Заказ в прошлом месяце -> «уже работающий», успешка не пишется."""
        call = {
            'general_call_id': 'g1',
            'started_at': datetime(2026, 7, 2, tzinfo=ALMATY_TZ),
            'call_type': 1, 'billsec': 60, 'operator_id': 7, 'employee_name': 'Оп',
        }
        lead = self._lead('L5', datetime(2026, 7, 10, tzinfo=ALMATY_TZ), [call],
                          prev_month_first_order_at=datetime(2026, 6, 20, tzinfo=ALMATY_TZ))
        db = FakeDb([lead])
        stats = tez_lead_service.recompute_outcomes(db, 2026, 7)
        self.assertEqual(stats['already_working'], 1)
        self.assertEqual(stats['success'], 0)
        self.assertIsNone(db.applied[0]['operator_id'])

    def test_already_working_has_no_operator(self):
        lead = self._lead('L2', datetime(2026, 6, 10, tzinfo=ALMATY_TZ), [])
        db = FakeDb([lead])
        stats = tez_lead_service.recompute_outcomes(db, 2026, 6)
        self.assertEqual(stats['already_working'], 1)
        self.assertIsNone(db.applied[0]['operator_id'])
        self.assertNotIn('success_date', db.applied[0])

    def test_is_late_flag(self):
        """Успешка, найденная после закрытия месяца, помечается как поздняя."""
        call = {
            'general_call_id': 'g1',
            'started_at': datetime(2026, 6, 2, tzinfo=ALMATY_TZ),
            'call_type': 1, 'billsec': 60, 'operator_id': 7, 'employee_name': 'Оп',
        }
        lead = self._lead('L3', datetime(2026, 6, 10, tzinfo=ALMATY_TZ), [call])
        db = FakeDb([lead])
        tez_lead_service.recompute_outcomes(db, 2026, 6, month_closed_before=date(2026, 7, 1))
        self.assertTrue(db.applied[0]['is_late'])

    def test_recompute_is_idempotent(self):
        call = {
            'general_call_id': 'g1',
            'started_at': datetime(2026, 6, 2, tzinfo=ALMATY_TZ),
            'call_type': 1, 'billsec': 60, 'operator_id': 7, 'employee_name': 'Оп',
        }
        lead = self._lead('L4', datetime(2026, 6, 10, tzinfo=ALMATY_TZ), [call])
        db = FakeDb([lead])
        first = tez_lead_service.recompute_outcomes(db, 2026, 6)
        first_applied = db.applied
        second = tez_lead_service.recompute_outcomes(db, 2026, 6)
        self.assertEqual(first, second)
        self.assertEqual(first_applied, db.applied)

    def test_recompute_forwards_lead_version(self):
        """Optimistic locking uses the lead version read before computation."""
        version = datetime(2026, 6, 15, 12, 30, tzinfo=ALMATY_TZ)
        lead = self._lead('L6', None, [])
        lead['version'] = version
        db = FakeDb([lead])

        tez_lead_service.recompute_outcomes(db, 2026, 6)

        self.assertEqual(db.applied[0]['lead_version'], version)

    def test_empty_month(self):
        db = FakeDb([])
        stats = tez_lead_service.recompute_outcomes(db, 2026, 6)
        self.assertEqual(stats['success'], 0)


class FakeMirrorDb:
    """БД для проверки зеркала звонков: помнит journal дней и сохранённые звонки."""

    def __init__(self, synced_days=()):
        self.synced = set(synced_days)
        self.saved = []
        self.marked = []

    def get_tez_call_synced_days(self, start_day, end_day):
        return {d for d in self.synced if start_day <= d <= end_day}

    def mark_tez_call_day_synced(self, day, calls):
        self.synced.add(day)
        self.marked.append((day, calls))

    def save_tez_lead_calls(self, calls):
        self.saved.extend(calls)
        return len(calls)


class FakeBinotel:
    """Отдаёт по одному звонку на день и считает, за какие дни его спрашивали."""

    def __init__(self, per_day=1, sleep_seconds=0.0):
        self.days = []
        self.per_day = per_day
        self.sleep_seconds = sleep_seconds

    def list_calls_for_day(self, day):
        self.days.append(day)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return [{
            'general_call_id': f'{day.isoformat()}-{i}',
            'external_number': '77012345678',
            'start_time': int(datetime(day.year, day.month, day.day, 10, 0,
                                       tzinfo=ALMATY_TZ).timestamp()),
            'call_type': 1,
            'billsec': 30,
            'waitsec': 3,
            'disposition': 'ANSWER',
            'internal_number': '925',
            'employee_name': 'Оператор ОП',
            'employee_email': 'op@example.com',
        } for i in range(self.per_day)]


class CallMirrorTests(unittest.TestCase):
    """Зеркало звонков по дням: полнота окна, идемпотентность, бюджет времени."""

    @staticmethod
    def _resolve(name, call_date):
        return 7 if name == 'Оператор ОП' else None

    def test_covers_whole_window_up_to_today(self):
        """Окно июля — с 24 июня; за сегодня 10 июля это 17 дней, ни днём меньше."""
        db = FakeMirrorDb()
        client = FakeBinotel()
        res = tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 7, 10)
        )
        self.assertEqual(res['days'], 17)
        self.assertEqual(res['days_left'], 0)
        self.assertEqual(min(client.days), date(2026, 6, 24))
        self.assertEqual(max(client.days), date(2026, 7, 10))

    def test_future_days_are_not_requested(self):
        """За дни, которые ещё не наступили, Binotel не спрашиваем."""
        db = FakeMirrorDb()
        client = FakeBinotel()
        tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 7, 3)
        )
        self.assertTrue(all(d <= date(2026, 7, 3) for d in client.days))

    def test_synced_days_are_skipped_but_tail_is_refreshed(self):
        """Перекачанный день второй раз не тянем, а хвост окна обновляем всегда:
        «сегодня» на момент прошлого прогона ещё не закончился."""
        today = date(2026, 7, 10)
        already = {date(2026, 6, 24) + timedelta(days=i) for i in range(17)}
        db = FakeMirrorDb(already)
        client = FakeBinotel()
        res = tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=today
        )
        self.assertEqual(sorted(client.days), [date(2026, 7, 9), today])
        self.assertEqual(res['days'], 2)

    def test_closed_month_is_not_refetched(self):
        """У закрытого месяца перекачивать нечего: порог обновления привязан к
        сегодня, а не к концу окна, иначе июль каждую ночь тянул бы 30–31 число."""
        db = FakeMirrorDb({date(2026, 6, 24) + timedelta(days=i) for i in range(38)})
        client = FakeBinotel()
        res = tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 8, 20)
        )
        self.assertEqual(client.days, [])
        self.assertEqual(res['days'], 0)

    def test_time_budget_leaves_rest_for_next_run(self):
        """Бюджет времени не должен молча терять дни — остаток виден в ответе."""
        db = FakeMirrorDb()
        client = FakeBinotel(sleep_seconds=0.02)
        res = tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 7, 10),
            time_budget=0.0,
        )
        # Хотя бы один день делаем всегда, иначе нулевой бюджет заклинил бы добор.
        self.assertEqual(res['days'], 1)
        self.assertEqual(res['days_left'], 16)

    def test_operator_is_resolved_and_qualifying_computed(self):
        db = FakeMirrorDb()
        client = FakeBinotel()
        tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 6, 24)
        )
        saved = db.saved[0]
        self.assertEqual(saved['phone_norm'], '77012345678')
        self.assertEqual(saved['operator_id'], 7)
        self.assertTrue(saved['is_qualifying'])

    def test_foreign_calls_are_stored_without_operator(self):
        """Звонки ТП/линии зеркало сохраняет, но без оператора — в «Обзвонено»
        они не попадут, а в разборе спора видно, что номеру звонили."""
        db = FakeMirrorDb()
        client = FakeBinotel()

        def resolve(name, call_date):
            return None

        tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, resolve, today=date(2026, 6, 24)
        )
        saved = db.saved[0]
        self.assertIsNone(saved['operator_id'])
        self.assertFalse(saved['is_qualifying'])

    def test_short_call_is_attempt_but_not_qualifying(self):
        """Сброс на первой секунде — это попытка дозвона, но не разговор."""
        db = FakeMirrorDb()
        client = FakeBinotel()
        original = client.list_calls_for_day

        def short(day):
            calls = original(day)
            for c in calls:
                c['billsec'] = 3
                c['disposition'] = 'CANCEL'
            return calls

        client.list_calls_for_day = short
        tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 6, 24)
        )
        saved = db.saved[0]
        self.assertEqual(saved['call_type'], 1)
        self.assertEqual(saved['operator_id'], 7)
        self.assertFalse(saved['is_qualifying'])

    def test_window_before_today_returns_nothing(self):
        """Период целиком в будущем — запросов нет вообще."""
        db = FakeMirrorDb()
        client = FakeBinotel()
        res = tez_lead_service.sync_calls_for_period(
            db, 2026, 7, client, self._resolve, today=date(2026, 6, 1)
        )
        self.assertEqual(res, {'days': 0, 'days_left': 0, 'calls': 0})
        self.assertEqual(client.days, [])


class ReattributeCallsTests(unittest.TestCase):
    """Binotel забывает уволенного сотрудника — привязку возвращаем по sip."""

    class Db:
        def __init__(self, calls, owners):
            self._calls = calls
            self._owners = owners
            self.restored = []

        def get_tez_calls_without_operator(self, window_from, window_to):
            return [c for c in self._calls if window_from <= c['started_at'] < window_to]

        def get_tez_call_internal_number_owners(self):
            return self._owners

        def restore_tez_call_operators(self, rows):
            self.restored = rows
            return len(rows)

    @staticmethod
    def _call(sip, billsec=60, call_type=1, day=(2026, 7, 10), gid='g1'):
        return {'general_call_id': gid, 'internal_number': sip, 'call_type': call_type,
                'billsec': billsec, 'started_at': datetime(*day, 12, 0, tzinfo=ALMATY_TZ)}

    @staticmethod
    def _resolve(name, call_date):
        return 353 if name == 'Уволенный оператор ОП' else None

    def test_restores_operator_by_unique_sip_owner(self):
        db = self.Db([self._call('927')], {'927': 'Уволенный оператор ОП'})
        res = tez_lead_service.reattribute_calls_without_operator(db, 2026, 7, self._resolve)
        self.assertEqual(res, {'checked': 1, 'restored': 1})
        self.assertEqual(db.restored[0]['operator_id'], 353)
        self.assertEqual(db.restored[0]['employee_name'], 'Уволенный оператор ОП')
        self.assertTrue(db.restored[0]['is_qualifying'])

    def test_unknown_sip_is_left_alone(self):
        """Номер без однозначного владельца не угадываем — sip передают другим."""
        db = self.Db([self._call('908')], {'927': 'Уволенный оператор ОП'})
        res = tez_lead_service.reattribute_calls_without_operator(db, 2026, 7, self._resolve)
        self.assertEqual(res, {'checked': 1, 'restored': 0})
        self.assertEqual(db.restored, [])

    def test_foreign_department_is_not_restored(self):
        """Владелец номера нашёлся, но он не из ОП — привязку не ставим."""
        db = self.Db([self._call('905')], {'905': 'Оператор техподдержки'})
        res = tez_lead_service.reattribute_calls_without_operator(db, 2026, 7, self._resolve)
        self.assertEqual(res['restored'], 0)

    def test_short_call_restored_but_not_qualifying(self):
        """Короткий звонок — попытка дозвона, но не доказательство привлечения."""
        db = self.Db([self._call('927', billsec=4)], {'927': 'Уволенный оператор ОП'})
        tez_lead_service.reattribute_calls_without_operator(db, 2026, 7, self._resolve)
        self.assertFalse(db.restored[0]['is_qualifying'])

    def test_incoming_call_restored_but_not_qualifying(self):
        db = self.Db([self._call('927', call_type=0)], {'927': 'Уволенный оператор ОП'})
        tez_lead_service.reattribute_calls_without_operator(db, 2026, 7, self._resolve)
        self.assertFalse(db.restored[0]['is_qualifying'])

    def test_only_window_of_period_is_touched(self):
        """Шаг работает по окну месяца, а не по всей истории."""
        db = self.Db([self._call('927', day=(2026, 6, 1))], {'927': 'Уволенный оператор ОП'})
        res = tez_lead_service.reattribute_calls_without_operator(db, 2026, 7, self._resolve)
        self.assertEqual(res, {'checked': 0, 'restored': 0})


class SuccessDropWarningTests(unittest.TestCase):
    """Убыль успешек за период обязана быть заметна — это деньги оператора."""

    def test_recompute_reports_previous_count(self):
        lead = {'id': 'L1', 'phone_norm': '77000000000', 'full_name': 'Тест',
                'month_first_order_at': None, 'prev_month_first_order_at': None, 'calls': []}
        db = FakeDb([lead], successes_before=7)
        stats = tez_lead_service.recompute_outcomes(db, 2026, 7)
        self.assertEqual(stats['successes_before'], 7)

    def test_drop_is_logged(self):
        lead = {'id': 'L1', 'phone_norm': '77000000000', 'full_name': 'Тест',
                'month_first_order_at': None, 'prev_month_first_order_at': None, 'calls': []}
        db = FakeDb([lead], successes_before=7)
        with self.assertLogs('tez_lead_service', level='WARNING') as logs:
            tez_lead_service.recompute_outcomes(db, 2026, 7)
        self.assertTrue(any('УМЕНЬШИЛИСЬ' in line for line in logs.output))


class FirstOrdersContractTests(unittest.TestCase):
    """Контракт TEZ APP: month обязателен в теле, в ответе две оконные даты."""

    def _client_with_capture(self, payload):
        import json as _json
        from tez_first_orders import TezFirstOrdersClient
        sent = {}

        class _Resp:
            status_code = 200
            text = ''
            headers = {}

            def json(self):
                return payload

        class _Session:
            def post(self, url, data=None, headers=None, timeout=None):
                sent.update(_json.loads(data.decode('utf-8')))
                return _Resp()

        client = TezFirstOrdersClient(token='x')
        client.session = _Session()
        return client, sent

    def test_month_is_sent_in_body(self):
        """Без month API отвечает 400 — параметр обязан уходить в запросе."""
        client, sent = self._client_with_capture({'drivers': []})
        client.fetch_first_orders(['77000000101'], month='2026-07')
        self.assertEqual(sent.get('month'), '2026-07')
        self.assertEqual(sent['drivers'][0]['phone'], '+77000000101')

    def test_month_is_validated(self):
        from tez_first_orders import TezFirstOrdersClient
        client = TezFirstOrdersClient(token='x')
        for bad in ('', None, '2026', '07-2026'):
            with self.assertRaises(ValueError):
                client.fetch_first_orders(['77000000101'], month=bad)

    def test_parses_both_window_dates(self):
        payload = {'drivers': [{
            'phone': '+77000000101',
            'month_first_order_at': '2026-07-03T09:00:00+05:00',
            'previous_month_first_order_at': '2026-06-20T10:00:00+05:00',
        }]}
        client, _ = self._client_with_capture(payload)
        res = client.fetch_first_orders(['77000000101'], month='2026-07')
        row = res['77000000101']
        self.assertEqual(row['month'].day, 3)
        self.assertEqual(row['prev'].day, 20)

    def test_parses_last_order_before_at(self):
        """Контракт от 11.08.2026: вместо первого заказа прошлого месяца приходит
        последний заказ до начала запрошенного — на нём держится правило разрыва."""
        payload = {'drivers': [{
            'phone': '+77000000101',
            'month_first_order_at': '2026-08-08T09:52:05+05:00',
            'last_order_before_at': '2026-06-19T10:20:27+05:00',
        }]}
        client, _ = self._client_with_capture(payload)
        row = client.fetch_first_orders(['77000000101'], month='2026-08')['77000000101']
        self.assertEqual(row['month'].day, 8)
        self.assertEqual((row['last_before'].month, row['last_before'].day), (6, 19))
        self.assertIsNone(row['prev'])

    def test_contract_loss_is_logged_not_swallowed(self):
        """Пропажа last_order_before_at не даёт ни ошибки, ни пустого ответа —
        успешки уснувшим просто перестанут начисляться. Ровно так 11.08.2026
        незаметно исчезло previous_month_first_order_at."""
        payload = {'drivers': [{
            'phone': '+77000000101',
            'month_first_order_at': '2026-08-08T09:52:05+05:00',
        }]}
        client, _ = self._client_with_capture(payload)
        with self.assertLogs('tez_first_orders', level='WARNING') as logs:
            client.fetch_first_orders(['77000000101'], month='2026-08')
        self.assertTrue(any('last_order_before_at' in line for line in logs.output))

    def test_missing_driver_marked_checked(self):
        """Номер, которого нет в ответе, всё равно помечается проверенным."""
        client, _ = self._client_with_capture({'drivers': []})
        res = client.fetch_first_orders(['77000000101'], month='2026-07')
        self.assertEqual(res['77000000101'],
                         {'month': None, 'prev': None, 'last_before': None})

    def test_bad_number_does_not_sink_whole_batch(self):
        """400 из-за одного битого номера не должен терять остальные из батча:
        клиент делит батч пополам и изолирует только битый номер."""
        from tez_first_orders import TezFirstOrdersClient
        bad = '77000000001'   # этот номер API «отвергает»

        class _OkResp:
            status_code = 200; text = ''; headers = {}
            def __init__(self, drivers): self._d = drivers
            def json(self): return {'drivers': self._d}

        class _BadResp:
            status_code = 400
            text = '{"error":"bad phone","error_code":30168}'
            headers = {}
            def json(self): return {}

        class _Session:
            def post(self, url, data=None, headers=None, timeout=None):
                import json as _json
                phones = [d['phone'] for d in _json.loads(data.decode())['drivers']]
                if '+' + bad in phones:
                    return _BadResp()
                return _OkResp([{'phone': p, 'month_first_order_at': None,
                                 'previous_month_first_order_at': None} for p in phones])

        client = TezFirstOrdersClient(token='x')
        client.session = _Session()
        res = client.fetch_first_orders(['77000000101', bad, '77000000107'], month='2026-07')
        # хорошие номера получены, битый — в last_invalid, а не потоплен весь батч
        self.assertIn('77000000101', res)
        self.assertIn('77000000107', res)
        self.assertNotIn(bad, res)
        self.assertIn(bad, client.last_invalid)


class BinotelBatchTruncationTests(unittest.TestCase):
    """history-by-external-number режет ответ по числу звонков и роняет номера;
    клиент должен дробить пачку, чтобы не терять звонки (а с ними успешки)."""

    def _client(self, calls_by_phone, guard):
        import tez_binotel_calls as tbc

        class _Client(tbc.BinotelApiClient):
            def __init__(self):
                self.api_key = 'k'; self.api_secret = 's'
                self.base_url = tbc.DEFAULT_API_URL; self.tz = tbc.DEFAULT_TZ
                self.timeout = 10; self.session = None
                self.requests = []

            def _post(self, endpoint, params):
                nums = params['externalNumbers']
                self.requests.append(list(nums))
                details = []
                for n in nums:
                    details.extend(calls_by_phone.get(n, []))
                # Эмуляция обрезки: если суммарно звонков >= лимита, отдаём только
                # первые `guard`, теряя хвостовые номера (как реальный Binotel).
                if len(details) >= guard:
                    details = details[:guard]
                return {"status": "success", "callDetails": details}

        return _Client()

    def test_batch_split_recovers_dropped_numbers(self):
        import tez_binotel_calls as tbc
        # 6 номеров по 100 звонков = 600 > guard(400): без дробления часть выпадет.
        calls = {}
        gid = 0
        for i in range(6):
            phone = f"7700000000{i}"
            arr = []
            for _ in range(100):
                gid += 1
                arr.append({"generalCallID": str(gid), "callType": "1", "billsec": "30",
                            "externalNumber": phone, "startTime": "1784571656",
                            "employeeData": {"name": "Оп"}})
            calls[phone] = arr
        client = self._client(calls, guard=400)
        # маленький порог/размер, чтобы форсировать дробление
        tbc.MAX_EXTERNAL_NUMBERS_PER_REQUEST = 6
        tbc.EXTERNAL_NUMBERS_TRUNCATION_GUARD = 400
        got = client.list_calls_by_external_numbers(list(calls.keys()))
        got_phones = {c["external_number"] for c in got}
        self.assertEqual(len(got_phones), 6, "ни один номер не должен потеряться")
        self.assertEqual(len(got), 600, "все звонки должны вернуться после дробления")


class CloudflareDetectionTests(unittest.TestCase):
    """403 от TEZ APP c Cloudflare-заглушкой должен опознаваться (а не течь в UI сырым)."""

    def test_detects_cloudflare_page(self):
        from tez_first_orders import _looks_like_cloudflare_block
        cf = '<!DOCTYPE html><html class="no-js" lang="en-US"> error code: 1020 cloudflare'
        self.assertTrue(_looks_like_cloudflare_block(cf))

    def test_ignores_normal_json(self):
        from tez_first_orders import _looks_like_cloudflare_block
        self.assertFalse(_looks_like_cloudflare_block('{"drivers": []}'))

    def test_cloudflare_403_raises_clean_message(self):
        """403 с Cloudflare-заглушкой -> понятный RuntimeError (а не NameError/HTML).

        Ветка исполняется только на Cloudflare-пути (прод-IP), поэтому локальный
        smoke-тест её не задевал — тут прогоняем её напрямую через фейковую сессию.
        """
        from tez_first_orders import TezFirstOrdersClient

        class _Resp:
            status_code = 403
            text = ('<!DOCTYPE html><html class="no-js" lang="en-US"> '
                    'error code: 1020 cloudflare')
            headers = {'cf-ray': 'abc123-AKX', 'server': 'cloudflare'}

            def json(self):
                raise ValueError('not json')

        class _Session:
            def post(self, *a, **k):
                return _Resp()

        client = TezFirstOrdersClient(token='x')
        client.session = _Session()
        with self.assertRaises(RuntimeError) as ctx:
            client.fetch_first_orders(['77000000101'], month='2026-07')
        msg = str(ctx.exception)
        self.assertIn('Cloudflare', msg)
        self.assertIn('1020', msg)
        self.assertIn('abc123-AKX', msg)   # CF-Ray попадает в диагностику


class CarryRecomputeTests(unittest.TestCase):
    """Перенос в пересчёте: только добавляет успешку и не задваивает поездку."""

    def _carried(self, lead_id, carry_trip, calls, phone='77000000001'):
        return {
            'id': lead_id, 'phone_norm': phone, 'full_name': 'Перенос',
            'month_first_order_at': None, 'prev_month_first_order_at': None,
            'lead_year': 2026, 'lead_month': 7, 'is_carried': True,
            'carry_first_order_at': carry_trip, 'carry_last_order_before_at': None,
            'calls': calls,
        }

    def _own(self, lead_id, trip, calls, phone='77000000001'):
        return {
            'id': lead_id, 'phone_norm': phone, 'full_name': 'Свой',
            'month_first_order_at': trip, 'prev_month_first_order_at': None,
            'lead_year': 2026, 'lead_month': 8, 'is_carried': False,
            'carry_first_order_at': None, 'carry_last_order_before_at': None,
            'calls': calls,
        }

    def _call(self, when, operator_id=3, gid='c1'):
        return {'general_call_id': gid, 'started_at': when, 'call_type': 1,
                'billsec': 60, 'operator_id': operator_id, 'employee_name': 'Оператор'}

    def test_carried_lead_gets_success_in_the_trip_month(self):
        """Звонок 28 июля (последние 7 дней), поездка 5 августа -> успешка августа
        по июльскому лиду. Именно за этим перенос и нужен: раньше про такого
        водителя после 31 июля просто больше не спрашивали."""
        lead = self._carried('L1', datetime(2026, 8, 5, 12, tzinfo=ALMATY_TZ),
                             [self._call(datetime(2026, 7, 28, 12, tzinfo=ALMATY_TZ))])
        db = FakeDb([lead])
        tez_lead_service.recompute_outcomes(db, 2026, 8)
        item = db.applied[0]
        self.assertEqual(item['status'], 'success')
        self.assertEqual((item['success_year'], item['success_month']), (2026, 8))
        self.assertEqual((item['lead_year'], item['lead_month']), (2026, 7))

    def test_carried_lead_with_a_mid_month_call_is_not_counted(self):
        """Звонок 10 июля, поездка 5 августа -> НЕ успешка. Перенос продлевает
        поиск поездки, но окно звонка не расширяет."""
        lead = self._carried('L1', datetime(2026, 8, 5, 12, tzinfo=ALMATY_TZ),
                             [self._call(datetime(2026, 7, 10, 12, tzinfo=ALMATY_TZ))])
        db = FakeDb([lead])
        tez_lead_service.recompute_outcomes(db, 2026, 8)
        self.assertEqual(db.applied[0]['status'], 'not_counted')

    def test_carried_lead_never_writes_its_own_status(self):
        """Иначе пересчёт месяца базы вернёт всё назад, и статус будет мигать."""
        lead = self._carried('L1', None, [])
        db = FakeDb([lead])
        tez_lead_service.recompute_outcomes(db, 2026, 8)
        self.assertFalse(db.applied[0]['write_status'])

    def test_own_lead_still_writes_its_status(self):
        lead = self._own('L2', None, [])
        db = FakeDb([lead])
        tez_lead_service.recompute_outcomes(db, 2026, 8)
        self.assertTrue(db.applied[0]['write_status'])

    def test_one_trip_gives_one_success_when_phone_is_in_both_bases(self):
        """374 номера на проде есть и в июльской, и в августовской базе, и оба
        лида видят ОДИН список звонков. UNIQUE(lead_id) от дубля не спасает."""
        trip = datetime(2026, 8, 5, 12, tzinfo=ALMATY_TZ)
        calls = [self._call(datetime(2026, 7, 10, 12, tzinfo=ALMATY_TZ)),
                 self._call(datetime(2026, 8, 2, 12, tzinfo=ALMATY_TZ), operator_id=4, gid='c2')]
        db = FakeDb([self._carried('L1', trip, calls), self._own('L2', trip, calls)])
        tez_lead_service.recompute_outcomes(db, 2026, 8)
        successes = [i for i in db.applied if i['status'] == 'success']
        self.assertEqual(len(successes), 1)
        # Побеждает лид того месяца, в котором поездка.
        self.assertEqual(successes[0]['lead_id'], 'L2')

    def test_losing_carried_lead_keeps_its_status_untouched(self):
        """Проигравший дедуп перенесённый лид не должен получить чужой статус."""
        trip = datetime(2026, 8, 5, 12, tzinfo=ALMATY_TZ)
        calls = [self._call(datetime(2026, 8, 2, 12, tzinfo=ALMATY_TZ))]
        db = FakeDb([self._carried('L1', trip, calls), self._own('L2', trip, calls)])
        tez_lead_service.recompute_outcomes(db, 2026, 8)
        self.assertNotIn('L1', [i['lead_id'] for i in db.applied])


class SyncLockTests(unittest.TestCase):
    """Один прогон сверки на процесс: параллельный только удвоит расход лимитов."""

    def test_second_entry_is_refused_not_queued(self):
        with tez_lead_service.sync_lock():
            with self.assertRaises(tez_lead_service.SyncBusy):
                with tez_lead_service.sync_lock():
                    self.fail('внутрь пустили второй прогон')

    def test_lock_is_released_after_failure(self):
        """Упавший прогон не имеет права оставить замок закрытым навсегда."""
        with self.assertRaises(ValueError):
            with tez_lead_service.sync_lock():
                raise ValueError('шаг сверки упал')
        with tez_lead_service.sync_lock():
            pass

    def test_nightly_skips_instead_of_waiting(self):
        """Ночная джоба не прерывает ручную сверку и не встаёт в очередь."""
        with tez_lead_service.sync_lock():
            report = tez_lead_service.run_nightly(
                db=None, first_orders_client=None, binotel_client=None,
                resolve_operator=None, today=date(2026, 8, 10),
            )
        self.assertEqual(report, {'skipped': 'locked'})


class RecomputeQueryTests(unittest.TestCase):
    """Порог длительности уезжает в SQL, а не отбрасывается уже в Python."""

    def test_recompute_asks_db_for_qualifying_calls_only(self):
        seen = {}

        class Db(FakeDb):
            def get_tez_leads_for_recompute(self, year, month, min_billsec=None):
                seen['min_billsec'] = min_billsec
                return self._leads

        tez_lead_service.recompute_outcomes(Db([]), 2026, 8, min_billsec=15)
        self.assertEqual(seen['min_billsec'], 15)


class BatchWriteTests(unittest.TestCase):
    """Результат пересчёта пишется пачками, а не строкой на лид.

    Построчный вариант делал два statement'а на лида (на июльской базе ~14 400
    round-trip'ов) и держал всё это время advisory-лок периода, который делит с
    импортом базы. Обработка лидов прошлого месяца удваивает число лидов.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def _body(self, name, size=9000):
        """Тело метода до следующего `def` — срез фиксированной длины выталкивал
        бы искомое за границу при каждом росте функции."""
        start = self.src.index("def %s" % name)
        nxt = self.src.find("\n    def ", start + 1)
        end = min(nxt if nxt != -1 else len(self.src), start + size)
        return self.src[start:end]

    def test_outcomes_are_written_with_execute_values(self):
        body = self._body('apply_tez_lead_outcomes')
        self.assertIn('execute_values', body)
        # Построчных запросов остаться не должно.
        self.assertNotIn('DELETE FROM tez_lead_successes WHERE lead_id = %s', body)
        self.assertIn('WHERE lead_id = ANY(%s::uuid[]) AND year = %s AND month = %s', body)

    def test_success_removal_is_scoped_to_the_period(self):
        """Успешка, забронированная переносом в следующем месяце, пересчёту
        месяца базы не принадлежит — снести её он не имеет права."""
        body = self._body('apply_tez_lead_outcomes')
        delete_at = body.index('DELETE FROM tez_lead_successes')
        self.assertIn('AND year = %s AND month = %s', body[delete_at:delete_at + 300])

    def test_carried_lead_keeps_its_own_status(self):
        """Статус перенесённого лида принадлежит месяцу его базы: перепиши его
        отсюда — и пересчёт того месяца вернёт всё назад, статус будет мигать."""
        body = self._body('apply_tez_lead_outcomes')
        self.assertIn('CASE WHEN v.write_status THEN v.status ELSE l.status END', body)

    def test_period_comes_from_the_item_not_the_arguments(self):
        """Иначе UPDATE перенесённого лида не найдёт строку (год/месяц чужие),
        вернёт ноль строк — и перенос молча не работает."""
        body = self._body('apply_tez_lead_outcomes')
        self.assertIn("int(item.get('lead_year') or year)", body)
        self.assertIn('AND l.year = v.lead_year', body)

    def test_locks_are_taken_in_a_deterministic_order(self):
        """Два периода в одной транзакции: без сортировки будет дедлок с
        параллельным импортом/удалением базы соседнего месяца."""
        body = self._body('apply_tez_lead_outcomes')
        self.assertIn('periods = sorted(', body)

    def test_optimistic_version_check_survived_the_rewrite(self):
        """Проверка xmin — единственное, что не даёт перезаписать лид, изменившийся
        между чтением и записью; при переходе на пачки её легко потерять."""
        body = self._body('apply_tez_lead_outcomes')
        self.assertIn('v.version IS NULL OR l.xmin::text = v.version', body)

    def test_skipped_leads_are_counted_not_swallowed(self):
        """Раньше несовпадение версии проглатывалось молча, и «перенос не работает»
        выглядело как «успешек нет» без единой строки в логе."""
        body = self._body('apply_tez_lead_outcomes')
        self.assertIn("stats['skipped']", body)
        self.assertIn('logging.warning', body)

    def test_first_orders_are_written_with_execute_values(self):
        body = self._body('save_tez_first_orders')
        self.assertIn('execute_values', body)
        # Найденная дата поездки не должна перетираться более поздним ответом.
        self.assertIn('COALESCE(l.month_first_order_at, v.month_at)', body)

    def test_successes_have_period_index_and_dupe_guard(self):
        self.assertIn('idx_tez_successes_period', self.src)
        self.assertIn('uq_tez_successes_phone_date', self.src)
        # Дубли на проде обязаны дать WARNING, а не уронить старт приложения.
        self.assertIn('EXCEPTION WHEN unique_violation THEN', self.src)


if __name__ == "__main__":
    unittest.main()
