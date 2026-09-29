# -*- coding: utf-8 -*-
"""«Обзвон»: ИИН в базе, проверка подписания документов в Sapar, успешки, аналитика.

Запрос владельца 29.09.2026. Здесь — правила без базы и сети: общий статус пакета
документов, время подписи, месяц документов, ИИН в файле, порядок работы прогона
проверки с подставными Sapar и курсором, инварианты SQL засчитывания успешки.
Настоящий SQL прогнан на локальном Postgres при разработке (стенд вне репозитория).
"""
import contextlib
import inspect
import io
import os
import pathlib
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import leads_file  # noqa: E402
from dial_list import analytics as dial_analytics  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list import sign_check, signing  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIGN_JS = ROOT / 'src' / 'components' / 'dial_list' / 'signStatus.js'

# Синтетические ИИН — собраны по алгоритму, живых людей за ними нет.
VALID = '900101300007'
LOST_ZERO = '050312500771'   # родился в 2005: Excel/CSV съедает ведущий ноль


def _doc(status, signed=None):
    return {'ServiceId': 1, 'Status': status, 'DocSignDateByDriver': signed}


class AggregateStatusTests(unittest.TestCase):
    """Правило владельца: общий статус — минимальный по успешности."""

    def test_owner_example(self):
        # Подписан + Не подписан + На проверке у Яндекса = Не подписан.
        docs = [_doc('Подписано'), _doc('НаПодписанииУВодителя'), _doc('НаПодписанииУЯндекса')]
        self.assertEqual(signing.aggregate(docs), signing.UNSIGNED)

    def test_signed_only_when_all_signed(self):
        self.assertEqual(signing.aggregate([_doc('Подписано'), _doc('Signed')]), signing.SIGNED)
        self.assertEqual(signing.aggregate([_doc('Подписано'), _doc('НаПодписанииУЯндекса')]), signing.PROCESSING)

    def test_order_of_ranks(self):
        order = [signing.SIGNED, signing.PROCESSING, signing.UNSIGNED, signing.NOT_FORMED,
                 signing.EXPIRED, signing.REJECTED]
        self.assertEqual(sorted(order, key=lambda g: -signing.SIGN_RANK[g]), order)
        self.assertEqual(signing.aggregate([_doc('Подписано'), _doc('СрокПодписанияИстек')]), signing.EXPIRED)
        self.assertEqual(signing.aggregate([_doc('Подписано'), _doc('Cancelled')]), signing.REJECTED)

    def test_empty_and_unknown(self):
        self.assertEqual(signing.aggregate([]), signing.NO_DOCS)
        self.assertEqual(signing.aggregate(None), signing.NO_DOCS)
        # Незнакомое слово Sapar успешкой не становится никогда.
        self.assertEqual(signing.group_of('НовыйСтатус'), signing.UNSIGNED)
        self.assertEqual(signing.aggregate([_doc('Подписано'), _doc('НовыйСтатус')]), signing.UNSIGNED)

    def test_park_documents_do_not_count(self):
        # Решают только документы Яндекса: у прогона в руках yandex, АВР парка он не читает.
        src = inspect.getsource(sign_check.SignChecker.run)
        self.assertIn('result.get("yandex")', src)
        self.assertNotIn('park', src)


class SignMomentTests(unittest.TestCase):
    def test_naive_sapar_time_is_utc_and_any_fraction(self):
        moment = signing.parse_sapar_time('2026-09-10T01:19:48.21285')
        self.assertEqual(moment, datetime(2026, 9, 10, 1, 19, 48, 212850, tzinfo=timezone.utc))
        self.assertEqual(signing.parse_sapar_time('2026-09-10T01:19:48Z').tzinfo, timezone.utc)
        self.assertEqual(signing.parse_sapar_time('2026-09-10T06:19:48+05:00'),
                         datetime(2026, 9, 10, 1, 19, 48, tzinfo=timezone.utc))
        self.assertIsNone(signing.parse_sapar_time(''))
        self.assertIsNone(signing.parse_sapar_time('вчера'))

    def test_package_signed_at_last_signature_and_never_after_detection(self):
        detected = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        docs = [_doc('Подписано', '2026-09-18T10:00:00'), _doc('Подписано', '2026-09-19T08:30:00.5')]
        self.assertEqual(signing.signed_moment(docs, detected),
                         datetime(2026, 9, 19, 8, 30, 0, 500000, tzinfo=timezone.utc))
        future = [_doc('Подписано', '2026-09-21T00:00:00')]
        self.assertEqual(signing.signed_moment(future, detected), detected)
        without_date = [_doc('Подписано', '2026-09-18T10:00:00'), _doc('Подписано', None)]
        self.assertEqual(signing.signed_moment(without_date, detected), detected)

    def test_stored_document_rows_carry_no_personal_data(self):
        rows = signing.document_rows([{'ServiceId': 7, 'Status': 'Подписано', 'DriverIin': VALID,
                                       'DriverFio': 'Иванов', 'Sum': 1000, 'DocSignDateByDriver': None}])
        self.assertEqual(set(rows[0]), {'id', 'status', 'group', 'signed_at'})


class DocumentMonthTests(unittest.TestCase):
    def test_base_checks_previous_month_documents(self):
        self.assertEqual(signing.doc_month_for(date(2026, 9, 1)), (8, 2026))
        self.assertEqual(signing.doc_month_for(date(2026, 1, 1)), (12, 2025))

    def test_window_is_current_and_previous_base(self):
        self.assertEqual(signing.check_window(date(2026, 9, 29)), (date(2026, 8, 1), date(2026, 9, 1)))
        self.assertEqual(signing.check_window(date(2026, 1, 3)), (date(2025, 12, 1), date(2026, 1, 1)))

    def test_owner_numbers(self):
        self.assertEqual(signing.SUCCESS_MIN_BILLSEC, 10)
        self.assertEqual(signing.RECHECK_HOURS, 3)

    def test_status_code_for_row(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(signing.status_code(VALID, 'unsigned', now), signing.SIGNED)
        self.assertEqual(signing.status_code('', '', None), signing.NO_IIN)
        self.assertEqual(signing.status_code(VALID, '', None), signing.NOT_CHECKED)
        self.assertEqual(signing.status_code(VALID, 'processing', None), signing.PROCESSING)


class IinInFileTests(unittest.TestCase):
    def test_clean_iin(self):
        self.assertEqual(dial_service.clean_iin(VALID), VALID)
        self.assertEqual(dial_service.clean_iin('9001 0130 0007'), VALID)
        self.assertEqual(dial_service.clean_iin(LOST_ZERO[1:]), LOST_ZERO)   # ноль вернули
        self.assertEqual(dial_service.clean_iin('900101300008'), '')         # контрольная не сходится
        self.assertEqual(dial_service.clean_iin(''), '')
        self.assertEqual(dial_service.clean_iin('5.03125E+11'), '')

    def test_mask_iin_keeps_tail(self):
        self.assertEqual(dial_service.mask_iin(VALID), '•••• •••• 0007')
        self.assertEqual(dial_service.mask_iin(''), '')

    def test_csv_with_iin_header(self):
        raw = f'fio;phone;iin\nИванов;87011234567;{VALID}\n'.encode('utf-8')
        rows = leads_file.parse_leads_file(raw, '.csv', with_iin=True)
        self.assertEqual(rows, [(2, 'Иванов', '87011234567', '77011234567', VALID)])

    def test_missing_iin_column_rejects_the_file(self):
        raw = 'fio;phone\nИванов;87011234567\n'.encode('utf-8')
        with self.assertRaises(ValueError) as ctx:
            leads_file.parse_leads_file(raw, '.csv', with_iin=True)
        self.assertIn('iin', str(ctx.exception))

    def test_headerless_file_third_column_is_iin(self):
        raw = f'Иванов,87011234567,{VALID}\n'.encode('utf-8')
        self.assertEqual(leads_file.parse_leads_file(raw, '.csv', with_iin=True)[0][4], VALID)

    def test_xlsx_numeric_iin_gets_leading_zero(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(['ФИО', 'Телефон', 'ИИН'])
        ws.append(['Иванов', 87011234567, int(LOST_ZERO)])
        buf = io.BytesIO()
        wb.save(buf)
        rows = leads_file.parse_leads_file(buf.getvalue(), '.xlsx', with_iin=True)
        self.assertEqual(rows[0][4], LOST_ZERO)

    def test_other_bases_keep_old_format(self):
        # TEZ и прочие: ИИН не ищется, строки по-прежнему из четырёх полей.
        raw = f'fio;phone;iin\nИванов;87011234567;{VALID}\n'.encode('utf-8')
        self.assertEqual(len(leads_file.parse_leads_file(raw, '.csv')[0]), 4)

    def test_upload_requires_iin_and_starts_check(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        upload = src[src.index('def leads_upload'):src.index('def leads_summary')]
        self.assertIn('with_iin=True', upload)
        self.assertIn('request_sign_check(', upload)


class ImportTests(unittest.TestCase):
    """Строка без ИИН не загружается; ИИН уникален в базе месяца, как и номер."""

    def _import(self, rows, existing=()):
        executed = []

        class Cur:
            def execute(self, sql, params=None):
                executed.append(sql)
                self._sql = sql

            def fetchone(self):
                return ('batch-1',)

            def fetchall(self):
                return list(existing) if 'SELECT phone_norm, iin' in self._sql else []

        @contextlib.contextmanager
        def cursor():
            yield Cur()

        db = type('Db', (), {'_get_cursor': staticmethod(cursor)})()
        svc = dial_service.DialListService(db, sign_checker=object())
        captured = {}

        def fake_values(cur, sql, values, page_size=100, fetch=False, template=None):
            captured['values'] = values
            return [(True,) for _ in values]

        original = dial_service.execute_values
        dial_service.execute_values = fake_values
        try:
            counts = svc.import_leads(1, 5, 'база.xlsx', rows, period=date(2026, 9, 1))
        finally:
            dial_service.execute_values = original
        return counts, captured.get('values', [])

    def test_counts_and_rows(self):
        other = '900101300017'
        self.assertTrue(dial_service.iin_rules.is_valid(other))
        rows = [
            (2, 'А', '', '77010000001', VALID),
            (3, 'Б', '', '77010000002', ''),               # без ИИН
            (4, 'В', '', '77010000003', '900101300008'),   # ошибка в ИИН
            (5, 'Г', '', '77010000004', VALID),            # тот же водитель другим номером
            (6, 'Д', '', '', other),                       # без номера
            (7, 'Е', '', '77010000005', other),
        ]
        counts, values = self._import(rows)
        self.assertEqual((counts['rows_new'], counts['rows_bad_iin'], counts['rows_duplicate'], counts['rows_invalid']),
                         (2, 2, 1, 1))
        self.assertEqual(counts['bad_iin_rows'], [3, 4])
        self.assertEqual(counts['invalid_rows'], [6])
        self.assertEqual([v[6] for v in values], [VALID, other])

    def test_iin_already_owned_by_another_number_in_month_is_duplicate(self):
        counts, values = self._import([(2, 'А', '', '77010000009', VALID)], existing=[('77010000001', VALID)])
        self.assertEqual((counts['rows_new'], counts['rows_duplicate']), (0, 1))
        self.assertEqual(values, [])

    def test_reupload_changes_iin_only_until_signed(self):
        sql = dial_service.DialListService._IMPORT_SQL
        self.assertIn('iin = CASE WHEN dial_list_leads.signed_at IS NULL THEN EXCLUDED.iin', sql)
        self.assertIn('sign_checked_at = CASE WHEN dial_list_leads.signed_at IS NULL AND dial_list_leads.iin <> EXCLUDED.iin', sql)


class PoolAndJournalTests(unittest.TestCase):
    def test_signed_driver_never_returns_to_pool(self):
        self.assertIn('AND l.signed_at IS NULL', dial_service.DialListService._POOL_SQL)
        src = inspect.getsource(dial_service.DialListService.requeue_lead)
        self.assertIn('уже подписал документы', src)

    def test_sign_filters_are_known_and_rejected_otherwise(self):
        svc = dial_service.DialListService(db=None, sign_checker=object())
        svc.department_settings = lambda d: {"max_attempts": 3, "retry_after_hours": 24,
                                             "_period": dial_service.current_period()}
        with self.assertRaises(dial_service.DialListError):
            svc.leads_journal(1, sign='nope')
        for sql in dial_service.JOURNAL_SIGN_FILTERS.values():
            self.assertNotIn('%', sql)   # без параметров: подставляется в текст запроса

    def test_frontend_twin_labels_and_filters(self):
        js = SIGN_JS.read_text(encoding='utf-8')
        for code, label in signing.SIGN_STATUS_LABELS.items():
            self.assertIn(f"{code}: {{ label: '{label}'", js, code)
        js_codes = set(re.findall(r"^\s{4}(\w+): \{ label:", js, flags=re.M))
        self.assertEqual(js_codes, set(signing.SIGN_STATUS_LABELS))
        filters = set(re.findall(r"\{ value: '(\w*)', label:", js[js.index('SIGN_FILTER_OPTIONS'):js.index('SIGN_BREAKDOWN')]))
        self.assertEqual(filters - {''}, set(dial_service.JOURNAL_SIGN_FILTERS))

    def test_journal_carries_iin_tail_only(self):
        src = inspect.getsource(dial_service.DialListService._journal_row)
        self.assertIn('mask_iin(r[39])', src)
        self.assertNotIn('"iin":', src)


class SuccessAttributionTests(unittest.TestCase):
    """Успешка — последнему, кто поговорил с водителем ≥10 с ДО подписи."""

    def test_resolve_sql_rules(self):
        sql = sign_check.SignChecker._RESOLVE_SQL
        for fragment in ("t.state = 'finished' AND NOT t.cancelled",
                         'UPPER(t.disposition) IN %(answered)s',
                         't.billsec >= %(min_sec)s',
                         't.requested_at <= c.signed_at',
                         'ORDER BY c.id, t.requested_at DESC',
                         'success_resolved_at IS NULL',
                         'FOR UPDATE OF l SKIP LOCKED'):
            self.assertIn(fragment, sql)
        # Звонок, начатый до подписи и ещё идущий, откладывает решение (но не вечно).
        self.assertIn('t.state IN %(in_flight)s', sql)
        self.assertIn('t.requested_at > %(stale_before)s', sql)

    def test_signed_rows_leave_operator_lists_but_not_live_calls(self):
        src = inspect.getsource(sign_check.SignChecker.close_signed_rows)
        self.assertIn("a.state = 'issued'", src)
        self.assertIn('t.state IN %s', src)
        worked = inspect.getsource(dial_service.DialListService.operator_worked)
        self.assertIn('r[16]', worked)   # закрытое из-за подписи не «Не дозвонились»


class _FakeCursor:
    def __init__(self, due):
        self.due = due
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        last = self.sql[-1][0]
        if 'FROM dial_list_leads l' in last and 'LIMIT' in last:
            return self.due
        return []

    def fetchone(self):
        return None


class SignCheckRunTests(unittest.TestCase):
    def _checker(self, due, fetch, configured=True):
        cur = _FakeCursor(due)

        @contextlib.contextmanager
        def cursor():
            yield cur

        db = type('Db', (), {'_get_cursor': staticmethod(cursor)})()
        written = []
        checker = sign_check.SignChecker(db, fetch=fetch, configured=lambda: configured, sleep=lambda s: None)
        checker._write = lambda items, now: written.extend(items)
        checker.close_signed_rows = lambda ids: len(ids)
        checker.resolve_successes = lambda now=None, lead_ids=None: 0
        return checker, written, cur

    def test_not_configured_does_nothing(self):
        calls = []
        checker, written, cur = self._checker([], lambda *a, **k: calls.append(a), configured=False)
        self.assertEqual(checker.run()['stopped'], 'not_configured')
        self.assertEqual((calls, cur.sql), ([], []))

    def test_one_request_per_iin_and_document_month(self):
        calls = []

        def fetch(iin, month, year, session=None):
            calls.append((iin, month, year))
            return {'ok': True, 'yandex': [_doc('Подписано', '2026-09-20T10:00:00')]}

        due = [('l1', VALID, date(2026, 9, 1)), ('l2', VALID, date(2026, 9, 1)), ('l3', VALID, date(2026, 8, 1))]
        checker, written, _ = self._checker(due, fetch)
        summary = checker.run(now=datetime(2026, 9, 29, 12, tzinfo=timezone.utc))
        self.assertEqual(calls, [(VALID, 8, 2026), (VALID, 7, 2026)])
        self.assertEqual(summary['signed'], 3)
        self.assertEqual({w[1] for w in written}, {'l1', 'l2', 'l3'})

    def test_sapar_down_stops_the_run_and_keeps_check_time(self):
        calls = []

        def fetch(iin, month, year, session=None):
            calls.append(iin)
            return {'ok': False, 'error': 'timeout', 'systemic': True}

        due = [(f'l{i}', f'{i:012d}', date(2026, 9, 1)) for i in range(20)]
        checker, written, _ = self._checker(due, fetch)
        summary = checker.run(now=datetime(2026, 9, 29, 12, tzinfo=timezone.utc))
        self.assertEqual(len(calls), sign_check.MAX_SYSTEMIC_ERRORS_IN_ROW)
        self.assertEqual(summary['stopped'], 'sapar_unavailable')
        self.assertTrue(all(w[0] == 'error' and w[3] is True for w in written))
        # Сетевой сбой не сдвигает время проверки — водитель снова «пора» через полчаса.
        write_src = inspect.getsource(sign_check.SignChecker._write)
        self.assertIn('CASE WHEN v.systemic THEN l.sign_checked_at ELSE v.now END', write_src)

    def test_due_rule_every_three_hours_and_manual_check_ignores_it(self):
        due_sql = sign_check.SignChecker._DUE_SQL
        self.assertIn('l.signed_at IS NULL', due_sql)
        run = inspect.getsource(sign_check.SignChecker.run)
        self.assertIn('due_filter = "" if force else', run)
        self.assertIn('l.period BETWEEN %(from_period)s AND %(to_period)s', run)

    def test_requests_coalesce_while_running(self):
        import threading
        import time
        gate = threading.Event()
        runs = []
        checker = sign_check.SignChecker(db=None, configured=lambda: True)
        checker.run = lambda **kw: (runs.append(kw.get('reason')), gate.wait(5))
        self.assertTrue(checker.request('a'))
        self.assertFalse(checker.request('b'))
        self.assertFalse(checker.request('c'))
        gate.set()
        for _ in range(50):
            if len(runs) == 2 and not checker._running:
                break
            time.sleep(0.02)
        self.assertEqual(len(runs), 2)          # прогон + один повтор, а не три

    def test_runs_in_own_single_thread_not_bot_pool(self):
        src = inspect.getsource(sign_check.SignChecker.request)
        self.assertIn('ThreadPoolExecutor(max_workers=1', src)
        self.assertNotIn('run_in_executor', inspect.getsource(sign_check))


class SchemaAndWiringTests(unittest.TestCase):
    def test_schema_columns_and_safe_unique_index(self):
        ddl = ' '.join(dial_schema.DDL)
        for column in ('iin VARCHAR(12)', 'sign_status VARCHAR(16)', 'sign_docs JSONB', 'sign_checked_at',
                       'signed_at TIMESTAMP', 'success_attempt_id UUID', 'success_operator_id INTEGER',
                       'success_resolved_at', 'rows_bad_iin'):
            self.assertIn(column, ddl)
        # Без FK на попытку: лид → попытки → выдачи → лид было бы кольцом каскадов.
        self.assertIn('ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_attempt_id UUID', dial_schema.DDL)
        unique = next(s for s in dial_schema.DDL if 'uq_dial_list_leads_period_iin' in s)
        self.assertIn('EXCEPTION WHEN unique_violation', unique)

    def test_scheduler_asks_every_half_hour(self):
        text = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        job = text[text.index('async def dial_list_sign_check_job'):]
        self.assertIn("_dial_list_service.request_sign_check('планировщик')", job[:600])
        self.assertIn("id='dial_list_sign_check'", text)
        block = text[text.index("dial_list_sign_check_job,\n"):text.index("id='dial_list_sign_check'")]
        self.assertIn("minute='20,50'", block)

    def test_new_routes_are_manager_only(self):
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        for name in ('def analytics(', 'def lead_sign_check('):
            body = src[src.index(name):src.index('@bp.route', src.index(name))]
            self.assertIn('_manager(', body)
        self.assertNotIn('/api/operator/dial_list/sign', src)

    def test_analytics_has_no_phones_or_iin(self):
        src = inspect.getsource(dial_analytics)
        self.assertNotIn('phone_norm', src)
        self.assertNotIn("'iin'", src)
        self.assertNotIn('full_name', src)

    def test_analytics_funnel_is_nested(self):
        # Успешка требует разговора ≥10 с — воронка «поговорили → успешки» честная.
        self.assertIn('AND t.billsec >= %(min_sec)s) AS talked', dial_analytics._LEADS_CTE)
        self.assertEqual(dial_analytics._TOTAL_KEYS[:7],
                         ('leads', 'with_iin', 'called', 'answered', 'talked', 'signed', 'successes'))


if __name__ == '__main__':
    unittest.main()
