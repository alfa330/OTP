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
import threading
import time
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import leads_file  # noqa: E402
from dial_list import analytics as dial_analytics  # noqa: E402
from dial_list import month_base  # noqa: E402
from dial_list import routes as dial_routes  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list import sign_check, signing  # noqa: E402
from tests import source_cache

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
        # Решают только документы Яндекса: у круга в руках yandex, АВР парка он не читает. Ответы Sapar
        # разбирает _ask — он один у круга, у «Проверить сейчас» в карточке и у перепроверки перед отчётом.
        src = inspect.getsource(sign_check.SignChecker._ask)
        self.assertIn('result.get("yandex")', src)
        for method in (sign_check.SignChecker._ask, sign_check.SignChecker.run, sign_check.SignChecker.refresh):
            self.assertNotIn('park', inspect.getsource(method))


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
        # С 10.11.2026 «раз в 3 часа» — только у тех, у кого срок подписания истёк; остальные — по кругу.
        self.assertEqual(signing.EXPIRED_RECHECK_HOURS, 3)
        self.assertFalse(hasattr(signing, 'RECHECK_HOURS'))

    def test_next_check_tick_is_the_next_scheduler_minute(self):
        self.assertEqual(signing.CHECK_TICK_MINUTES, 5)
        utc = timezone.utc
        for now, tick in (
                (datetime(2026, 11, 10, 14, 7, 30, tzinfo=utc), datetime(2026, 11, 10, 14, 10, tzinfo=utc)),
                (datetime(2026, 11, 10, 14, 9, 59, 999999, tzinfo=utc), datetime(2026, 11, 10, 14, 10, tzinfo=utc)),
                # Ровно в минуту запуска — следующий: этот уже идёт.
                (datetime(2026, 11, 10, 14, 10, tzinfo=utc), datetime(2026, 11, 10, 14, 15, tzinfo=utc)),
                (datetime(2026, 11, 10, 23, 58, 1, tzinfo=utc), datetime(2026, 11, 11, 0, 0, tzinfo=utc)),
        ):
            self.assertEqual(signing.next_check_tick(now), tick, now)
        # По Алматы (+5) минуты те же: пояс — целые часы.
        almaty = datetime(2026, 11, 10, 19, 7, tzinfo=signing.PERIOD_TZ)
        self.assertEqual(signing.next_check_tick(almaty), datetime(2026, 11, 10, 19, 10, tzinfo=signing.PERIOD_TZ))

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

    def test_network_announcement_is_never_a_talk_or_a_success(self):
        # Владелец, 12.10.2026: сообщение сети «абонент недоступен», плечо на котором положил
        # сам телефон (auto_hangup 'announcement'), — недозвон, что бы ни ответил Binotel.
        # Автоответчик отвечает ANSWER с длительностью: без условия такой звонок от 10 секунд
        # попал бы в «поговорили» и мог бы дать оператору успешку.
        best = sign_check.SignChecker._RESOLVE_SQL.split('best AS', 1)[1].split('UPDATE dial_list_leads', 1)[0]
        flat = ' '.join(best.split())
        self.assertIn("UPPER(t.disposition) IN %(answered)s AND t.auto_hangup <> 'announcement' "
                      "AND t.billsec >= %(min_sec)s AND t.requested_at <= c.signed_at", flat)
        att = ' '.join(dial_analytics._LEADS_CTE.split('att AS', 1)[1].split())
        # «Ответили» — то же правило, что у строки выдачи и лида (service.ATTEMPT_TALK_SQL).
        self.assertIn("(t.state = 'finished' AND UPPER(t.disposition) IN %(answered)s AND "
                      + dial_service.ATTEMPT_TALK_SQL + ") AS answered", att)
        # «Поговорили» и секунды разговора — без сообщений сети.
        self.assertIn("(t.state = 'finished' AND UPPER(t.disposition) IN %(answered)s "
                      "AND t.auto_hangup <> 'announcement' AND t.billsec >= %(min_sec)s) AS talked", att)
        self.assertIn("CASE WHEN t.state = 'finished' AND t.auto_hangup <> 'announcement' "
                      "THEN t.billsec ELSE 0 END AS talk_sec", att)
        # Попыткой сообщение сети остаётся — оператор звонил: счёт попыток условие не трогает.
        self.assertIn("COUNT(*) FILTER (WHERE NOT cancelled) AS attempts", dial_analytics._TOTALS_SQL)
        self.assertNotIn('auto_hangup', dial_analytics._TOTALS_SQL.split('per_lead AS', 1)[1])
        # Колонки заводит схема обзвона — до индексов (их порядок сторожит test_dial_list).
        ddl = ' '.join(dial_schema.DDL)
        self.assertIn("ADD COLUMN IF NOT EXISTS auto_hangup VARCHAR(16) NOT NULL DEFAULT ''", ddl)
        self.assertIn("ADD COLUMN IF NOT EXISTS talk_detected BOOLEAN", ddl)

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
    """Круг проверки подписания (владелец, 10.11.2026: «проверять только тех, кто в списке, способом, который
    показывает статусы точно, и максимально быстро»): все неподписавшие базы по кругу, в несколько потоков.
    Sapar и курсор подставные, потоки настоящие."""

    NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)

    def _checker(self, due, fetch, configured=True):
        cur = _FakeCursor(due)

        @contextlib.contextmanager
        def cursor():
            yield cur

        db = type('Db', (), {'_get_cursor': staticmethod(cursor)})()
        written = []
        checker = sign_check.SignChecker(db, fetch=fetch, configured=lambda: configured, sleep=lambda s: None)
        checker.write_modes = []

        def write(items, now, sparse=False):
            written.extend(items)
            checker.write_modes.append(sparse)

        checker._write = write
        checker.close_signed_rows = lambda ids: len(ids)
        checker.resolve_successes = lambda now=None, lead_ids=None: 0
        # ID водителей — своя сверка (driver_ids.py); здесь видно только, что круг её зовёт.
        checker.ids_synced = []
        checker.sync_driver_ids = lambda now=None, force=False: checker.ids_synced.append(now)
        return checker, written, cur

    @staticmethod
    def _unsigned(*args, **kwargs):
        return {'ok': True, 'yandex': [_doc('НаПодписанииУВодителя')]}

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
        summary = checker.run(now=self.NOW)
        self.assertEqual(sorted(calls), [(VALID, 7, 2026), (VALID, 8, 2026)])
        self.assertEqual(summary['signed'], 3)
        self.assertEqual({w[1] for w in written}, {'l1', 'l2', 'l3'})
        self.assertEqual(checker.ids_synced, [self.NOW])        # ID водителей — в начале круга

    def test_sapar_down_stops_the_cycle_and_keeps_check_time(self):
        calls = []

        def fetch(iin, month, year, session=None):
            calls.append(iin)
            time.sleep(0.02)                     # настоящий запрос идёт не мгновенно
            return {'ok': False, 'error': 'timeout', 'systemic': True}

        due = [(f'l{i}', f'{i:012d}', date(2026, 9, 1)) for i in range(60)]
        checker, written, _ = self._checker(due, fetch)
        summary = checker.run(now=self.NOW)
        self.assertEqual(summary['stopped'], 'sapar_unavailable')
        # Остановка — после MAX_SYSTEMIC_ERRORS_IN_ROW сбоев подряд; запросы, уже ушедшие в других потоках,
        # дорабатывают, новых нет.
        self.assertGreaterEqual(len(calls), sign_check.MAX_SYSTEMIC_ERRORS_IN_ROW)
        self.assertLessEqual(len(calls), sign_check.MAX_SYSTEMIC_ERRORS_IN_ROW + 2 * sign_check.PASS_LANES)
        self.assertEqual(len(written), len(calls))
        self.assertTrue(all(w[0] == 'error' and w[3] is True for w in written))
        # Сетевой сбой не сдвигает время проверки.
        write_src = inspect.getsource(sign_check.SignChecker._write)
        self.assertIn('CASE WHEN v.systemic THEN l.sign_checked_at ELSE v.now END', write_src)
        # Круг, не спросивший всех, полным не считается: «обновлено» в «Аналитике» не сдвигается.
        self.assertIsNone(checker.cycle_state(self.NOW)['finished_at'])

    def test_cycle_asks_everyone_and_expired_every_three_hours_in_the_current_base(self):
        checker, _, cur = self._checker([], self._unsigned)
        checker.run(now=self.NOW)
        sql, params = cur.sql[0]
        flat = ' '.join(sql.split())
        self.assertIn("WHERE l.iin <> '' AND l.signed_at IS NULL", flat)
        # Истёкшие (подписать уже нельзя) — только в базе текущего месяца и не чаще раза в 3 часа; остальные —
        # каждый круг: условия на время прошлой проверки у них нет.
        self.assertIn('AND (l.sign_status <> %(expired)s OR (l.period = %(to_period)s AND '
                      '(l.sign_checked_at IS NULL OR l.sign_checked_at < %(expired_before)s)))', flat)
        self.assertIn('AND l.period BETWEEN %(from_period)s AND %(to_period)s AND '
                      + month_base.in_month_base_sql('l'), flat)
        self.assertIn('ORDER BY l.sign_checked_at ASC NULLS FIRST, l.created_at ASC', flat)
        self.assertEqual((params['expired'], params['from_period'], params['to_period'], params['limit']),
                         (signing.EXPIRED, date(2026, 8, 1), date(2026, 9, 1), sign_check.PASS_MAX_LEADS))
        self.assertEqual(params['expired_before'], self.NOW - timedelta(hours=3) + timedelta(minutes=15))
        self.assertEqual(flat.count('sign_checked_at <'), 1)
        self.assertNotIn('due_before', params)          # прежнего «каждого — раз в 3 часа» больше нет

        # «Проверить сейчас» в карточке: этот водитель из базы любого месяца, истёкший — тоже.
        checker, _, cur = self._checker([], self._unsigned)
        checker.run(now=self.NOW, lead_ids=['l1'], force=True)
        sql, params = cur.sql[0]
        flat = ' '.join(sql.split())
        self.assertIn("WHERE l.iin <> '' AND l.signed_at IS NULL AND l.id = ANY(%(lead_ids)s::uuid[]) ORDER BY", flat)
        self.assertEqual(params['lead_ids'], ['l1'])
        self.assertEqual(checker.ids_synced, [])        # сверка ID — дело круга

    def test_cycle_order_is_longest_unasked_first(self):
        calls = []

        def fetch(iin, month, year, session=None):
            calls.append(iin)
            return self._unsigned()

        # Порядок запроса — по времени проверки из базы; оно переписывается не каждый круг.
        due = [(f'l{i}', f'iin-{i}', date(2026, 9, 1)) for i in range(1, 6)]
        checker, _, _ = self._checker(due, fetch)
        checker._seen = {'l1': self.NOW - timedelta(minutes=1), 'l3': self.NOW - timedelta(minutes=9),
                         'l4': self.NOW - timedelta(minutes=5), 'gone': self.NOW - timedelta(minutes=30)}
        checker.run(now=self.NOW, lanes=1)
        # О ком память круга не знает (l2, l5) — первыми, в порядке запроса; остальные — кого дольше не спрашивали.
        self.assertEqual(calls, ['iin-2', 'iin-5', 'iin-3', 'iin-4', 'iin-1'])
        # Спрошенные помнятся на сейчас; кого в круге уже нет (подписал, исключён) — забыт.
        self.assertEqual(checker._seen, {f'l{i}': self.NOW for i in range(1, 6)})

    def test_cycle_runs_in_parallel_lanes_and_card_check_in_one(self):
        lock, active, peak = threading.Lock(), [0], [0]

        def fetch(iin, month, year, session=None):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.01)
            with lock:
                active[0] -= 1
            return self._unsigned()

        due = [(f'l{i}', f'{i:012d}', date(2026, 9, 1)) for i in range(60)]
        checker, written, _ = self._checker(due, fetch)
        summary = checker.run(now=self.NOW)
        self.assertEqual(sign_check.PASS_LANES, 6)
        self.assertEqual((peak[0], summary['lanes'], summary['checked'], summary['stopped']), (6, 6, 60, ''))
        # Круг пишет «только если есть что писать»: иначе переписывал бы всю базу каждые несколько минут.
        self.assertTrue(checker.write_modes and all(mode is True for mode in checker.write_modes))
        # У каждой записи последним — время ответа Sapar (у круга с заданным `now` — оно).
        self.assertTrue(all(w == ('ok', w[1], signing.UNSIGNED, w[3], None, self.NOW) for w in written))

        peak[0] = 0
        checker, written, _ = self._checker(due[:5], fetch)
        summary = checker.run(now=self.NOW, lead_ids=[row[0] for row in due[:5]], force=True)
        # «Проверить сейчас» в карточке — одним потоком, и время проверки пишется всегда: человек спросил сам.
        self.assertEqual((peak[0], summary['lanes'], summary['checked']), (1, 1, 5))
        self.assertEqual(checker.write_modes, [False])
        # Счётчик круга такая проверка не трогает.
        self.assertEqual(checker.cycle_state(self.NOW)['total'], 0)

    def test_every_answer_gets_its_own_time(self):
        ticks = iter(datetime(2026, 9, 29, 12, 0, second, tzinfo=timezone.utc) for second in range(60))
        due = [(f'l{i}', f'{i:012d}', date(2026, 9, 1)) for i in range(3)]
        checker, written, _ = self._checker(due, self._unsigned)
        with unittest.mock.patch.object(sign_check, '_utcnow', lambda: next(ticks)):
            checker.run(lanes=1)
        # Без заданного `now` у каждого ответа Sapar своё время — оно и в записи, и в памяти круга.
        self.assertEqual(len({w[5] for w in written}), 3)
        self.assertEqual({w[1]: w[5] for w in written}, checker._seen)

    def test_cycle_yields_to_the_report_refresh(self):
        calls = []

        def fetch(iin, month, year, session=None):
            calls.append(iin)
            if len(calls) == 3:
                # Посреди круга поставили перепроверку перед отчётом (request_refresh): у неё срок.
                checker._refresh = {'bases': [], 'fresh_before': self.NOW, 'deadline': self.NOW, 'reason': 'отчёт'}
            time.sleep(0.005)
            return self._unsigned()

        due = [(f'l{i}', f'{i:012d}', date(2026, 9, 1)) for i in range(40)]
        checker, written, _ = self._checker(due, fetch)
        summary = checker.run(now=self.NOW, lanes=1)
        self.assertEqual(summary['stopped'], 'refresh')
        self.assertTrue(3 <= len(calls) < 10, calls)        # остальных спросит следующий круг
        self.assertEqual(len(written), len(calls))          # что спросили — записано
        self.assertTrue(checker._again)                     # после перепроверки круг продолжится сам
        self.assertIsNone(checker.cycle_state(self.NOW)['finished_at'])

    def test_cycle_state_counts_down_to_the_end_of_the_cycle(self):
        gate, reached, calls = threading.Event(), threading.Event(), []

        def fetch(iin, month, year, session=None):
            calls.append(iin)
            if len(calls) == 6:
                reached.set()
                gate.wait(5)                    # круг стоит на шестом водителе из десяти
            return self._unsigned()

        due = [(f'l{i}', f'{i:012d}', date(2026, 9, 1)) for i in range(10)]
        checker, _, _ = self._checker(due, fetch)
        # Кругов ещё не было: «обновлено» нет, круг начнётся с запуском планировщика.
        self.assertEqual(checker.cycle_state(self.NOW), {
            'running': False, 'total': 0, 'done': 0, 'finished_at': None,
            'eta_at': signing.next_check_tick(self.NOW)})
        worker = threading.Thread(target=lambda: checker.run(now=self.NOW, lanes=1))
        worker.start()
        try:
            self.assertTrue(reached.wait(5))
            for _ in range(300):
                if checker._cycle['done'] == 5:
                    break
                time.sleep(0.01)
            # Первые секунды круга, а прошлого не было — оценить конец не по чему.
            state = checker.cycle_state(self.NOW + timedelta(seconds=10))
            self.assertEqual((state['running'], state['total'], state['done'], state['eta_at']), (True, 10, 5, None))
            # Дальше — по скорости круга: 5 из 10 за 50 секунд — ещё 50.
            later = self.NOW + timedelta(seconds=50)
            self.assertEqual(checker.cycle_state(later)['eta_at'], later + timedelta(seconds=50))
        finally:
            gate.set()
            worker.join(5)
        end = self.NOW + timedelta(minutes=3)
        state = checker.cycle_state(end)
        # Круг спросил всех — он полный: его конец и есть «обновлено».
        self.assertEqual((state['running'], state['done'], state['finished_at']), (False, 10, self.NOW))
        self.assertEqual(checker._cycle['leads'], 10)
        checker._cycle['duration_sec'] = 600.0
        # Поток проверки свободен: следующий круг начнётся с запуском планировщика и пройдёт как прошлый.
        self.assertEqual(checker.cycle_state(end)['eta_at'], signing.next_check_tick(end) + timedelta(seconds=600))
        # Поток занят (пауза между кругами, перепроверка перед отчётом): круг вот-вот начнётся.
        checker._running = True
        self.assertEqual(checker.cycle_state(end)['eta_at'], end + timedelta(seconds=600))
        # Новый круг только начался, водителей в нём вдвое больше — идти будет вдвое дольше прошлого.
        checker._cycle.update(running=True, started_at=end, total=20, done=0)
        self.assertEqual(checker.cycle_state(end + timedelta(seconds=5))['eta_at'], end + timedelta(seconds=1200))

    def test_broken_write_is_not_a_full_cycle(self):
        checker, _, _ = self._checker([('l1', VALID, date(2026, 9, 1))], self._unsigned)

        def broken(items, now, sparse=False):
            raise RuntimeError('база недоступна')

        checker._write = broken
        with self.assertRaises(RuntimeError):
            checker.run(now=self.NOW)
        state = checker.cycle_state(self.NOW)
        self.assertEqual((state['running'], state['finished_at']), (False, None))

    def test_seen_at_is_the_later_of_the_row_and_the_memory(self):
        checker = sign_check.SignChecker(db=None, configured=lambda: True)
        early, late = self.NOW - timedelta(minutes=40), self.NOW - timedelta(minutes=3)
        self.assertIsNone(checker.seen_at('l1'))
        self.assertEqual(checker.seen_at('l1', early), early)           # память не знает — время из строки
        checker._seen['l1'] = late
        self.assertEqual(checker.seen_at('l1', early), late)
        self.assertEqual(checker.seen_at('l1'), late)
        self.assertEqual(checker.seen_at('l1', self.NOW), self.NOW)     # строку записали позже — она и права
        self.assertEqual(checker.seen_at('l1', early.replace(tzinfo=None)), late)   # время без пояса — UTC

    def test_cycle_write_touches_a_row_only_when_there_is_something_to_write(self):
        # Настоящим SQL это прогоняет tests/test_dial_list_sign_cycle_postgres.py; здесь — что условие на месте.
        ok = ' '.join(sign_check.SignChecker._SPARSE_OK_SQL.split())
        self.assertEqual(ok, "AND (v.signed_at IS NOT NULL OR l.sign_status <> v.code OR l.sign_docs <> v.docs "
                             "OR l.sign_error <> '' OR l.sign_checked_at IS NULL "
                             "OR l.sign_checked_at < v.now - make_interval(mins => 30))")
        error = ' '.join(sign_check.SignChecker._SPARSE_ERROR_SQL.split())
        self.assertEqual(error, "AND (l.sign_error <> v.error OR (NOT v.systemic AND (l.sign_checked_at IS NULL "
                                "OR l.sign_checked_at < v.now - make_interval(mins => 30))))")
        self.assertEqual(sign_check.STAMP_MINUTES, 30)
        write = inspect.getsource(sign_check.SignChecker._write)
        self.assertIn('(self._SPARSE_OK_SQL if sparse else "")', write)
        self.assertIn('(self._SPARSE_ERROR_SQL if sparse else "")', write)
        self.assertIn('def _write(self, items, now, sparse=False)', write)

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


class SignRefreshTests(unittest.TestCase):
    """Перепроверка перед отчётом о подписании (владелец, 20.10.2026): все неподписавшие баз отчёта,
    кроме только что проверенных, — параллельно, к сроку. Sapar подменён, потоки настоящие."""

    NOW = datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc)          # 9:30 по Алматы
    FRESH = datetime(2026, 10, 20, 4, 15, tzinfo=timezone.utc)
    DEADLINE = datetime(2026, 10, 20, 4, 59, tzinfo=timezone.utc)
    BASES = [(1954, date(2026, 10, 1))]

    def _checker(self, due, fetch, configured=True):
        checker, written, cur = SignCheckRunTests._checker(self, due, fetch, configured)
        closed, resolved = [], []
        checker.close_signed_rows = lambda ids: closed.extend(ids) or len(ids)
        checker.resolve_successes = lambda now=None, lead_ids=None: resolved.append(now) or 0
        return checker, written, cur, closed, resolved

    def test_due_sql_bases_and_freshness(self):
        checker, _, cur, _, _ = self._checker([], lambda *a, **k: {'ok': True, 'yandex': []})
        checker.refresh([(1954, date(2026, 10, 1)), (2001, date(2026, 9, 1))], self.FRESH, self.DEADLINE,
                        now=self.NOW)
        sql, params = cur.sql[0]
        self.assertIn('l.signed_at IS NULL', sql)
        self.assertIn('(l.sign_checked_at IS NULL OR l.sign_checked_at < %(fresh_before)s)', sql)
        self.assertIn('(l.department_id = %(d0)s AND l.period = %(p0)s) OR '
                      '(l.department_id = %(d1)s AND l.period = %(p1)s)', sql)
        self.assertEqual((params['d0'], params['p0'], params['d1'], params['p1'], params['fresh_before']),
                         (1954, date(2026, 10, 1), 2001, date(2026, 9, 1), self.FRESH))

    def test_every_driver_once_in_parallel_lanes(self):
        import threading
        import time
        lock, active, peak, calls = threading.Lock(), [0], [0], []

        def fetch(iin, month, year, session=None):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                calls.append((iin, month, year))
            time.sleep(0.01)
            with lock:
                active[0] -= 1
            signed = iin.endswith('0')
            return {'ok': True, 'yandex': [_doc('Подписано' if signed else 'НаПодписанииУВодителя',
                                                '2026-10-20T03:00:00' if signed else None)]}

        due = [(f'l{i}', f'{i:012d}', date(2026, 10, 1)) for i in range(40)]
        checker, written, _, closed, resolved = self._checker(due, fetch)
        summary = checker.refresh(self.BASES, self.FRESH, self.DEADLINE, reason='тест', now=self.NOW)
        self.assertEqual(sorted(calls), sorted((f'{i:012d}', 9, 2026) for i in range(40)))
        self.assertEqual(peak[0], sign_check.REFRESH_LANES)
        self.assertEqual((summary['due'], summary['checked'], summary['signed'], summary['stopped']), (40, 40, 4, ''))
        self.assertEqual({w[1] for w in written}, {f'l{i}' for i in range(40)})
        self.assertEqual(sorted(closed), sorted(f'l{i}' for i in range(0, 40, 10)))
        self.assertEqual(resolved, [self.NOW])
        self.assertIs(checker.last_refresh, summary)

    def test_deadline_stops_new_requests(self):
        calls = []
        due = [(f'l{i}', f'{i:012d}', date(2026, 10, 1)) for i in range(5)]
        checker, written, _, _, _ = self._checker(due, lambda iin, *a, **k: calls.append(iin) or {'ok': True})
        summary = checker.refresh(self.BASES, self.FRESH, self.NOW, now=self.NOW)   # срок уже вышел
        self.assertEqual((calls, written, summary['stopped']), ([], [], 'deadline'))

    def test_skips_those_the_cycle_asked_after_fresh_before(self):
        calls = []
        due = [(f'l{i}', f'{i:012d}', date(2026, 10, 1)) for i in range(4)]
        checker, written, _, _, _ = self._checker(
            due, lambda iin, *a, **k: calls.append(iin) or {'ok': True, 'yandex': []})
        # В строке базы время проверки переписывается не каждый круг — запрос считает «старыми» и тех, кого круг
        # спросил минуту назад. Кого он на самом деле спросил после fresh_before, знает его память.
        checker._seen = {'l0': self.FRESH + timedelta(minutes=1), 'l1': self.FRESH - timedelta(minutes=1),
                         'l2': self.FRESH}
        summary = checker.refresh(self.BASES, self.FRESH, self.DEADLINE, now=self.NOW)
        self.assertEqual(sorted(calls), ['000000000001', '000000000003'])
        self.assertEqual((summary['due'], summary['checked'], summary['lanes']), (2, 2, 2))
        # Пишет перепроверка так же, как круг, — только если есть что писать.
        self.assertEqual(checker.write_modes, [True])
        self.assertEqual({w[1] for w in written}, {'l1', 'l3'})

    def test_sapar_down_stops_and_keeps_check_time(self):
        import time
        calls = []

        def fetch(iin, month, year, session=None):
            calls.append(iin)
            time.sleep(0.02)                     # настоящий запрос идёт не мгновенно
            return {'ok': False, 'error': 'timeout', 'systemic': True}

        due = [(f'l{i}', f'{i:012d}', date(2026, 10, 1)) for i in range(60)]
        checker, written, _, _, _ = self._checker(due, fetch)
        summary = checker.refresh(self.BASES, self.FRESH, self.DEADLINE, now=self.NOW)
        self.assertEqual(summary['stopped'], 'sapar_unavailable')
        # Остановка — после MAX_SYSTEMIC_ERRORS_IN_ROW сбоев подряд; запросы, уже ушедшие в других потоках,
        # дорабатывают, новых нет.
        self.assertLessEqual(len(calls), sign_check.MAX_SYSTEMIC_ERRORS_IN_ROW + 2 * sign_check.REFRESH_LANES)
        self.assertEqual(len(written), len(calls))
        self.assertTrue(all(w[0] == 'error' and w[3] is True for w in written))

    def test_not_configured_and_no_bases(self):
        checker, _, cur, _, _ = self._checker([], lambda *a, **k: None, configured=False)
        self.assertEqual(checker.refresh(self.BASES, self.FRESH, self.DEADLINE, now=self.NOW)['stopped'],
                         'not_configured')
        checker, _, cur, _, _ = self._checker([], lambda *a, **k: None)
        self.assertEqual(checker.refresh([], self.FRESH, self.DEADLINE, now=self.NOW)['due'], 0)
        self.assertEqual(cur.sql, [])

    def test_refresh_waits_for_running_check_then_normal_runs_resume(self):
        import threading
        import time
        gate, order = threading.Event(), []
        checker = sign_check.SignChecker(db=None, configured=lambda: True)
        checker.run = lambda **kw: (order.append(('run', kw.get('reason'))), gate.wait(5))
        checker.refresh = lambda **kw: order.append(('refresh', kw['reason']))
        self.assertTrue(checker.request('планировщик'))
        # Обычный прогон идёт — перепроверка ждёт его и идёт сразу за ним; поздняя заменяет раннюю.
        self.assertFalse(checker.request_refresh(self.BASES, self.FRESH, self.DEADLINE, reason='первая'))
        self.assertFalse(checker.request_refresh(self.BASES, self.FRESH, self.DEADLINE, reason='вторая'))
        self.assertFalse(checker.request('загрузка базы'))
        gate.set()
        for _ in range(100):
            if not checker._running:
                break
            time.sleep(0.02)
        self.assertEqual(order, [('run', 'планировщик'), ('refresh', 'вторая'), ('run', 'повтор после прогона')])

    def test_refresh_alone_does_not_start_a_normal_run(self):
        import time
        order = []
        checker = sign_check.SignChecker(db=None, configured=lambda: True)
        checker.run = lambda **kw: order.append(('run', kw.get('reason')))
        checker.refresh = lambda **kw: order.append(('refresh', kw['reason']))
        self.assertTrue(checker.request_refresh(self.BASES, self.FRESH, self.DEADLINE, reason='перед отчётом'))
        for _ in range(100):
            if not checker._running:
                break
            time.sleep(0.02)
        self.assertEqual(order, [('refresh', 'перед отчётом')])


class CheckNowTests(unittest.TestCase):
    """Проверка «сейчас» (владелец, 11.10.2026: «подписавших не выдавать»): её ждёт
    оператор, поэтому параллельно, с таймаутом на запрос и сроком на всё; Sapar лежит —
    не ждём его и какое-то время не спрашиваем. Итог пишется так же, как у прогона.
    Sapar подменён, потоки — настоящие."""

    NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    BASE = date(2026, 10, 1)

    def setUp(self):
        self._executors = []
        executor_class = sign_check.ThreadPoolExecutor

        def executor(*args, **kwargs):
            pool = executor_class(*args, **kwargs)
            self._executors.append(pool)
            # check_now не ждёт потоки по контракту. Тест обязан дождаться своих:
            # иначе поздняя lane попадёт под _new_session следующего теста.
            self.addCleanup(pool.shutdown, wait=True)
            return pool

        patch = unittest.mock.patch.object(sign_check, 'ThreadPoolExecutor', executor)
        patch.start()
        self.addCleanup(patch.stop)

    def _checker(self, fetch, configured=True):
        checker = sign_check.SignChecker(db=None, fetch=fetch, configured=lambda: configured, sleep=lambda s: None)
        written, resolved = [], []
        checker._write = lambda items, now: written.extend(items)
        checker.resolve_successes = lambda now=None, lead_ids=None: resolved.append((now, list(lead_ids))) or 0
        return checker, written, resolved

    def _check(self, checker, leads, **kwargs):
        params = dict(timeout=3, workers=4, budget_sec=5, max_systemic=2, now=self.NOW)
        params.update(kwargs)
        return checker.check_now(leads, **params)

    def _wait_lanes(self):
        """Дождаться именно своих пулов, включая ещё не начавшие работу lane."""
        for executor in self._executors:
            executor.shutdown(wait=True)

    def test_not_configured_asks_nothing(self):
        calls = []
        checker, written, _ = self._checker(lambda *a, **k: calls.append(a), configured=False)
        summary = self._check(checker, [('l1', VALID, self.BASE)])
        self.assertEqual((summary['stopped'], calls, written), ('not_configured', [], []))

    def test_one_request_per_iin_and_month_and_verdicts_like_the_run(self):
        docs = {'iin-signed': [_doc('Подписано', '2026-10-10T05:00:00')],
                'iin-wait': [_doc('Подписано'), _doc('НаПодписанииУЯндекса')],
                'iin-no': [_doc('НаПодписанииУВодителя')]}
        calls, lock = [], threading.Lock()

        def fetch(iin, month, year, session=None, timeout=None):
            with lock:
                calls.append((iin, month, year, timeout))
            return {'ok': True, 'yandex': docs[iin]}

        checker, written, resolved = self._checker(fetch)
        leads = [('l1', 'iin-signed', self.BASE), ('l2', 'iin-signed', self.BASE), ('l3', 'iin-wait', self.BASE),
                 ('l4', 'iin-no', self.BASE), ('l5', '', self.BASE), ('l6', 'iin-signed', date(2026, 9, 1))]
        summary = self._check(checker, leads)
        # Один запрос на (ИИН, месяц документов): база октября — документы сентября; без ИИН — никак.
        self.assertEqual(sorted(calls), [('iin-no', 9, 2026, 3), ('iin-signed', 8, 2026, 3),
                                         ('iin-signed', 9, 2026, 3), ('iin-wait', 9, 2026, 3)])
        self.assertEqual(sorted(summary['signed']), ['l1', 'l2', 'l6'])
        self.assertEqual(summary['processing'], ['l3'])
        self.assertEqual((summary['checked'], summary['errors'], summary['stopped']), (5, 0, ''))
        # Записи для _write — те же, что у прогона: ok, лид, статус, документы, момент подписи.
        by_lead = {w[1]: w for w in written}
        self.assertEqual(set(by_lead), {'l1', 'l2', 'l3', 'l4', 'l6'})
        self.assertEqual(by_lead['l1'][2], signing.SIGNED)
        self.assertEqual(by_lead['l1'][4], datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((by_lead['l3'][2], by_lead['l3'][4]), (signing.PROCESSING, None))
        self.assertEqual(by_lead['l4'][2], signing.UNSIGNED)
        # Успешка — сразу и только по новым подписавшим.
        self.assertEqual([(now, sorted(ids)) for now, ids in resolved], [(self.NOW, ['l1', 'l2', 'l6'])])
        # Память круга знает и об этих ответах: очередь круга и карточка водителя видят точное время.
        self.assertEqual(checker._seen, {lead: self.NOW for lead in ('l1', 'l2', 'l3', 'l4', 'l6')})

    def test_parallel_workers_each_with_own_session(self):
        barrier, lock = threading.Barrier(4, timeout=5), threading.Lock()
        seen, sessions = [], []

        class Session:
            closed = False

            def close(self):
                self.closed = True

        def new_session():
            session = Session()
            with lock:
                sessions.append(session)
            return session

        def fetch(iin, month, year, session=None, timeout=None):
            barrier.wait()          # пройти можно только вчетвером — запросы идут одновременно
            with lock:
                seen.append(session)
            return {'ok': True, 'yandex': []}

        checker, _, _ = self._checker(fetch)
        with unittest.mock.patch.object(sign_check, '_new_session', new_session):
            summary = self._check(checker, [(f'l{n}', f'iin-{n}', self.BASE) for n in range(4)], workers=4)
            self._wait_lanes()
        self.assertEqual((summary['checked'], summary['errors']), (4, 0))
        self.assertEqual(len({id(s) for s in seen}), 4)          # у каждого потока свой Session
        self.assertEqual(len(sessions), 4)
        self.assertTrue(all(s.closed for s in sessions))          # и поток закрывает его сам

    def test_sapar_down_stops_early_and_backs_off(self):
        gate, calls = threading.Event(), []

        def fetch(iin, month, year, session=None, timeout=None):
            calls.append(iin)
            if len(calls) > 2:
                gate.wait(5)        # третий запрос «висит», пока проверка не кончится
            return {'ok': False, 'error': 'timeout', 'systemic': True}

        checker, written, resolved = self._checker(fetch)
        leads = [(f'l{n}', f'iin-{n}', self.BASE) for n in range(6)]
        try:
            summary = self._check(checker, leads, workers=1, max_systemic=2)
        finally:
            gate.set()
        self._wait_lanes()
        self.assertEqual((summary['stopped'], summary['systemic'], summary['errors']), ('sapar_unavailable', 2, 2))
        self.assertLessEqual(len(calls), 3)                       # остальных не спрашивали
        # Сетевой сбой — как у прогона: ошибка с systemic, время проверки не сдвигается.
        self.assertEqual([(w[0], w[3]) for w in written], [('error', True), ('error', True)])
        self.assertEqual(resolved, [])
        # Пока Sapar «лежит», следующие проверки его не спрашивают и не ждут.
        asked = len(calls)
        again = self._check(checker, leads[:1])
        self.assertEqual((again['stopped'], len(calls)), ('backoff', asked))
        # Пауза прошла — спрашиваем снова (у проверки перед звонком хватает одного сбоя).
        checker._now_backoff_until = 0.0
        self.assertEqual(self._check(checker, leads[:1], max_systemic=1)['stopped'], 'sapar_unavailable')
        self.assertEqual(len(calls), asked + 1)
        self.assertEqual(sign_check.NOW_BACKOFF_SEC, 30)

    def test_budget_does_not_wait_for_a_hung_request(self):
        gate = threading.Event()

        def fetch(iin, month, year, session=None, timeout=None):
            if iin == 'iin-slow':
                gate.wait(5)
            return {'ok': True, 'yandex': [_doc('НаПодписанииУВодителя')]}

        checker, written, _ = self._checker(fetch)
        leads = [('l-slow', 'iin-slow', self.BASE), ('l1', 'iin-1', self.BASE), ('l2', 'iin-2', self.BASE)]
        started = time.monotonic()
        try:
            summary = self._check(checker, leads, workers=2, budget_sec=1)
        finally:
            gate.set()
        self.assertLess(time.monotonic() - started, 4)            # не ждали «зависший» запрос (5 с)
        self._wait_lanes()
        self.assertEqual(summary['stopped'], 'budget')
        self.assertEqual(sorted(w[1] for w in written), ['l1', 'l2'])      # что пришло к сроку — записано
        self.assertEqual(checker._now_backoff_until, 0.0)                    # медленный — не значит «лежит»

    def test_fetch_exception_is_a_systemic_error(self):
        def fetch(iin, month, year, session=None, timeout=None):
            raise RuntimeError('обрыв соединения')

        checker, written, _ = self._checker(fetch)
        summary = self._check(checker, [('l1', 'iin-1', self.BASE)])
        self.assertEqual((summary['errors'], summary['systemic'], summary['stopped']), (1, 1, ''))
        self.assertEqual(written, [('error', 'l1', 'обрыв соединения', True)])

    def test_same_reading_as_the_run(self):
        now_src = inspect.getsource(sign_check.SignChecker.check_now)
        ask_src = inspect.getsource(sign_check.SignChecker._ask)        # запросы круга и перепроверки
        self.assertIn('result.get("yandex")', now_src)
        self.assertNotIn('park', now_src)
        for src in (now_src, ask_src):
            self.assertIn('_package_verdict(', src)
        self.assertIn('_ask(', inspect.getsource(sign_check.SignChecker.run))
        self.assertIn('_ask(', inspect.getsource(sign_check.SignChecker.refresh))
        # Проверка «сейчас» пишет время всегда (без sparse): по нему выдача видит, что водителя только что спросили.
        self.assertIn('self._write(pending, now)', now_src)
        self.assertIn('self.resolve_successes(now, lead_ids=summary["signed"])', now_src)
        # Строки со списков снимает вызывающий: пул выдачи, проверка в транзакции звонка.
        self.assertNotIn('close_signed_rows', now_src)


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

    def test_scheduler_wakes_the_check_every_five_minutes_and_at_start(self):
        text = (source_cache.monolith_flat_path()).read_text(encoding='utf-8')
        job = text[text.index('async def dial_list_sign_check_job'):]
        self.assertIn("_dial_list_service.request_sign_check('планировщик')", job[:700])
        self.assertIn("id='dial_list_sign_check'", text)
        block = text[text.index("dial_list_sign_check_job,\n"):text.index("id='dial_list_sign_check'")]
        # Шаг планировщика — тот же, по которому идёт отсчёт в «Аналитике» (signing.next_check_tick).
        self.assertIn("CronTrigger(minute='*/%d'," % signing.CHECK_TICK_MINUTES, block)
        tail = text[text.index("id='dial_list_sign_check'"):]
        tail = tail[:tail.index('    )')]
        # И раз при старте сервера: перезапуск обрывает идущий круг, новый начинается сразу.
        self.assertIn("next_run_time=datetime.now(ZoneInfo('Asia/Almaty'))", tail)

    def test_sign_routes_split_manager_card_and_operator_status(self):
        # Аналитика и «Проверить сейчас» с карточкой (маски ИИН и номера, документы) — только
        # руководителю. С 12.10.2026 у телефона своя ручка «Проверить на подписание»
        # (владелец: «без передачи данных на телефон»): она в зоне оператора и отдаёт один
        # статус — ни карточки, ни документов, ни ИИН.
        src = inspect.getsource(dial_routes.build_dial_list_blueprint)
        for name in ('def analytics(', 'def lead_sign_check('):
            body = src[src.index(name):src.index('@bp.route', src.index(name))]
            self.assertIn('_manager(', body)
        operator_part = src[src.index('# ── телефон оператора'):src.index('# ── руководитель')]
        operator_routes = re.findall(r"@bp\.route\('([^']+)'", operator_part)
        self.assertEqual([r for r in operator_routes if r.rsplit('/', 1)[-1].startswith('sign')],
                         ['/api/operator/dial_list/leads/<lead_id>/sign_check'])
        manager_part = src[src.index('# ── руководитель'):]
        self.assertNotIn('/api/operator/', manager_part)
        service = inspect.getsource(dial_service.DialListService.operator_sign_check)
        self.assertNotIn('check_lead_signature(', service)
        for forbidden in ('lead_card', '_journal_row', '_sign_block', 'mask_iin', 'mask_phone', 'sign_docs',
                          'documents'):
            self.assertNotIn(forbidden, service, forbidden)
        # В ответ идут только статус, подпись и время проверки (весь ответ ручки — в
        # OperatorSignCheckTests.test_response_carries_status_only).
        returned = service[service.rindex('return {'):]
        for forbidden in ('iin', 'error', 'signed_at', 'docs', 'phone'):
            self.assertNotIn(forbidden, returned, forbidden)
        # ИИН из базы читается только для запроса в Sapar — сам запрос его не возвращает.
        self.assertNotIn('sign_docs', dial_service.DialListService._OPERATOR_SIGN_LEAD_SQL)
        self.assertNotIn('phone_norm', dial_service.DialListService._OPERATOR_SIGN_LEAD_SQL)

    def test_operator_labels_reuse_journal_wording(self):
        # Причина «не подписал» на телефоне — словами SIGN_STATUS_LABELS (двойник на сайте
        # сторожит test_frontend_twin_labels_and_filters): поменяли там — поменяется и тут.
        for code in (signing.NOT_FORMED, signing.EXPIRED, signing.REJECTED, signing.NO_DOCS):
            detail = signing.SIGN_STATUS_LABELS[code]
            self.assertEqual(dial_service.operator_sign_status(code, None)[2],
                             f"Не подписал · {detail[0].lower()}{detail[1:]}")
        waiting = signing.SIGN_STATUS_LABELS[signing.PROCESSING]
        self.assertEqual(dial_service.operator_sign_status(signing.PROCESSING, None)[2],
                         f"Подписал · {waiting[0].lower()}{waiting[1:]}")

    def test_analytics_has_no_phones_or_iin(self):
        src = inspect.getsource(dial_analytics)
        # Дашборд о номерах не знает вовсе. «Воронка ОП» Тез (06.11.2026) сводит базу успешек со звонками
        # Binotel по номеру: номер — только в соединениях, во внешний SELECT не попадает (что в ответе
        # воронки нет номеров, сверяет test_dial_list_tez_funnel_postgres).
        self.assertNotIn('phone_norm', src[:src.index('# ── Воронка ОП Тез')])
        for sql in (dial_analytics._FUNNEL_SQL, dial_analytics._REFUSALS_SQL):
            self.assertNotIn('phone_norm', sql[sql.rindex('SELECT'):])
        self.assertNotIn("'iin'", src)
        self.assertNotIn('full_name', src)

    def test_analytics_funnel_is_nested(self):
        # Успешка требует разговора ≥10 с — воронка «поговорили → успешки» честная.
        self.assertIn('AND t.billsec >= %(min_sec)s) AS talked', dial_analytics._LEADS_CTE)
        self.assertEqual(dial_analytics._TOTAL_KEYS[:7],
                         ('leads', 'with_iin', 'called', 'answered', 'talked', 'signed', 'successes'))


class SignCycleOnScreenTests(unittest.TestCase):
    """Что видит руководитель (владелец, 10.11.2026: «отображать ближайшее обновление… обратный отсчёт»):
    отсчёт до конца круга в «Аналитике» и точное время проверки в карточке водителя — из памяти круга."""

    NOW = datetime(2026, 11, 10, 14, 7, 30, tzinfo=timezone.utc)

    def test_analytics_reads_its_columns_and_leaves_the_cycle_to_the_service(self):
        last = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
        row = tuple(range(1, len(dial_analytics._TOTAL_KEYS) + 1)) + (last, 7, 3)

        class Cursor:
            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return row

            def fetchall(self):
                return []

        data = dial_analytics.department_analytics(Cursor(), 1954, date(2026, 11, 1), today=date(2026, 11, 10),
                                                   configured=True)
        self.assertEqual(data['sign_check'], {
            'configured': True, 'active': True, 'doc_month': '2026-10-01',
            'last_checked_at': last.isoformat(), 'never_checked': 3, 'errors': 7, 'min_billsec': 10})
        # «Следующая — через 3 часа после самой старой проверки» ушла вместе с трёхчасовым правилом.
        self.assertNotIn('MIN(l.sign_checked_at)', dial_analytics._TOTALS_SQL)
        self.assertNotIn('RECHECK_HOURS', inspect.getsource(dial_analytics))

    def test_analytics_block_carries_the_cycle_for_the_countdown(self):
        checker = sign_check.SignChecker(db=None, configured=lambda: True)
        checker._cycle.update(finished_at=self.NOW - timedelta(minutes=4), duration_sec=720.0, leads=24000)
        svc = dial_service.DialListService(db=None, sign_checker=checker)
        self.assertEqual(svc._sign_cycle({'active': True, 'configured': True}, now=self.NOW), {
            'server_now': '2026-11-10T14:07:30+00:00',
            'updated_at': '2026-11-10T14:03:30+00:00',
            # Поток проверки свободен: круг начнётся с запуском планировщика в 14:10 и пройдёт за 12 минут.
            'next_update_at': '2026-11-10T14:22:00+00:00',
            'updating': False,
        })
        checker._cycle.update(running=True, started_at=self.NOW - timedelta(minutes=3), total=24000, done=6000)
        block = svc._sign_cycle({'active': True, 'configured': True}, now=self.NOW)
        # Круг идёт: четверть за 3 минуты — ещё 9.
        self.assertEqual((block['updating'], block['next_update_at']), (True, '2026-11-10T14:16:30+00:00'))
        # Старый месяц (база больше не проверяется) и Sapar не настроен — отсчёта нет.
        for check in ({'active': False, 'configured': True}, {'active': True, 'configured': False}):
            block = svc._sign_cycle(check, now=self.NOW)
            self.assertEqual((block['updated_at'], block['next_update_at'], block['updating']), (None, None, False))
            self.assertEqual(block['server_now'], '2026-11-10T14:07:30+00:00')
        # Проверка, подменённая без памяти круга (тесты других мест раздела), ничего не ломает.
        bare = dial_service.DialListService(db=None, sign_checker=object())
        block = bare._sign_cycle({'active': True, 'configured': True}, now=self.NOW)
        self.assertEqual((block['updated_at'], block['next_update_at'], block['updating']), (None, None, False))
        analytics = inspect.getsource(dial_service.DialListService.analytics)
        self.assertIn('data["sign_check"].update(self._sign_cycle(data["sign_check"]))', analytics)

    def test_card_shows_the_time_the_cycle_remembers(self):
        checker = sign_check.SignChecker(db=None, configured=lambda: True)
        stored, seen = self.NOW - timedelta(minutes=25), self.NOW - timedelta(minutes=2)
        checker._seen['lead-1'] = seen
        svc = dial_service.DialListService(db=None, sign_checker=checker)
        self.assertEqual(svc._sign_checked_at('lead-1', stored), seen)
        self.assertEqual(svc._sign_checked_at('lead-2', stored), stored)       # круг до него ещё не дошёл
        bare = dial_service.DialListService(db=None, sign_checker=object())
        self.assertEqual(bare._sign_checked_at('lead-1', stored), stored)
        # Строка журнала и карточка водителя собираются одним местом — время проверки идёт через память круга.
        row = inspect.getsource(dial_service.DialListService._journal_row)
        self.assertIn('self._sign_block(r[39], r[40], self._sign_checked_at(r[0], r[41]), r[42], r[43]', row)


if __name__ == '__main__':
    unittest.main()
