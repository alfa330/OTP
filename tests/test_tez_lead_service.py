# -*- coding: utf-8 -*-
"""Разбор файла базы лидов и пересчёт успешек поверх фейковой БД."""

import io
import sys
import unittest
from datetime import date, datetime
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
        raw = "fio;phone\nЕрметов Сабиржан;77023227108\nМәліков Қуанышбек;87018457385\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "Ерметов Сабиржан")
        self.assertEqual(rows[0][3], "77023227108")
        self.assertEqual(rows[1][3], "77018457385")   # 8-ка приведена к 7

    def test_csv_comma_delimiter(self):
        raw = "fio,phone\nИванов Иван,+7 701 234 56 78\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(rows[0][3], "77012345678")

    def test_csv_without_header(self):
        """Шапки может не быть — тогда первая колонка ФИО, вторая телефон."""
        raw = "Ерметов Сабиржан;77023227108\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "77023227108")

    def test_xlsx_with_header(self):
        raw = _xlsx_bytes([["fio", "phone"], ["Ерметов Сабиржан", "77023227108"]])
        rows = tez_lead_service.parse_leads_file(raw, ".xlsx")
        self.assertEqual(rows[0][1], "Ерметов Сабиржан")
        self.assertEqual(rows[0][3], "77023227108")

    def test_xlsx_numeric_phone(self):
        """Excel хранит телефон числом — не должно превратиться в 7.7023227108e+10."""
        raw = _xlsx_bytes([["fio", "phone"], ["Ерметов Сабиржан", 77023227108]])
        rows = tez_lead_service.parse_leads_file(raw, ".xlsx")
        self.assertEqual(rows[0][3], "77023227108")

    def test_invalid_phone_is_kept_as_row(self):
        """Битую строку не выбрасываем: СВ должен увидеть её в отчёте загрузки."""
        raw = "fio;phone\nБез номера;мусор\n"
        rows = tez_lead_service.parse_leads_file(raw.encode("utf-8"), ".csv")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][3])

    def test_empty_file(self):
        with self.assertRaises(ValueError):
            tez_lead_service.parse_leads_file(b"", ".csv")

    def test_unsupported_extension(self):
        with self.assertRaises(ValueError):
            tez_lead_service.parse_leads_file(b"x", ".txt")


class FakeDb:
    """Минимальная замена Database для проверки пересчёта."""

    def __init__(self, leads):
        self._leads = leads
        self.applied = None

    def get_tez_leads_for_recompute(self, year, month):
        return self._leads

    def apply_tez_lead_outcomes(self, year, month, outcomes):
        self.applied = outcomes
        stats = {'success': 0, 'already_working': 0, 'not_counted': 0, 'in_progress': 0, 'new': 0}
        for item in outcomes:
            stats[item['status']] = stats.get(item['status'], 0) + 1
        return stats


class RecomputeOutcomesTests(unittest.TestCase):
    def _lead(self, lead_id, first_order_at, calls):
        return {
            'id': lead_id,
            'phone_norm': '77000000000',
            'full_name': 'Тест',
            'first_order_at': first_order_at,
            'calls': calls,
        }

    def test_success_is_written_with_trip_month(self):
        """Лид июньской базы с поездкой 3 июля даёт успешку, помеченную ИЮЛЕМ."""
        call = {
            'general_call_id': 'g1',
            'started_at': datetime(2026, 6, 25, 10, 0, tzinfo=ALMATY_TZ),
            'call_type': 1, 'billsec': 60, 'operator_id': 7, 'employee_name': 'Оператор ОП',
        }
        lead = self._lead('L1', datetime(2026, 7, 3, 9, 0, tzinfo=ALMATY_TZ), [call])
        db = FakeDb([lead])

        stats = tez_lead_service.recompute_outcomes(db, 2026, 6)

        self.assertEqual(stats['success'], 1)
        item = db.applied[0]
        self.assertEqual(item['operator_id'], 7)
        self.assertEqual(item['operator_name'], 'Оператор ОП')
        self.assertEqual(item['success_year'], 2026)
        self.assertEqual(item['success_month'], 7)
        self.assertEqual(item['success_date'], date(2026, 7, 3))

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

    def test_empty_month(self):
        db = FakeDb([])
        stats = tez_lead_service.recompute_outcomes(db, 2026, 6)
        self.assertEqual(stats['success'], 0)


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

            def json(self):
                raise ValueError('not json')

        class _Session:
            def post(self, *a, **k):
                return _Resp()

        client = TezFirstOrdersClient(token='x')
        client.session = _Session()
        with self.assertRaises(RuntimeError) as ctx:
            client.fetch_first_orders(['77000409090'])
        msg = str(ctx.exception)
        self.assertIn('Cloudflare', msg)
        self.assertIn('1020', msg)


if __name__ == "__main__":
    unittest.main()
