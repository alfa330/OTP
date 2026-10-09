# -*- coding: utf-8 -*-
"""Номера «Реестра тестовых номеров» не входят ни в один расчёт: стражи мест и фильтров.

Что сторожится:
  * каждое место расчёта несёт условие реестра. SQL ловится записывающим курсором,
    база не нужна: выпадет условие при правке запроса — тест покраснеет;
  * Python-фильтры живых источников: журнал Binotel, метрики Chat2Desk (время ответа,
    оценки, «Чаты» оператора), продуктивность ОП Тез, лиды «Воронки ОП», сделки
    «Принятия лида», связка ИИ-оценки с маркетингом;
  * гейт ИИ-оценки отказывает разговору с тестовым номером;
  * в запросы к Oktell ключи едут только цифрами, пустой реестр текст не меняет;
  * условие ленты вебхуков Chat2Desk совпадает с выражением индекса ДОСЛОВНО.

Поведение на настоящем Postgres проверено стендом (тестовый и обычный номер в каждом
источнике, 27 расчётов), запросы к Oktell — на живом Oktell (итог дня убывает ровно на
звонки номера из реестра). Номера здесь выдуманные.
"""

import ast
import re
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_numbers import keys  # noqa: E402

MARK = 'test_phone_numbers'
TEST, NORM = '7000000101', '7000000202'
BOT_PY = ROOT / 'bot_schedule2.py'
DATABASE_PY = ROOT / 'database.py'


def _read(path):
    return Path(path).read_text(encoding='utf-8-sig')


class Recorder:
    """Курсор-заглушка: запоминает SQL, отдаёт заранее заданные ответы по очереди."""

    def __init__(self, *answers):
        self.sql = []
        self.answers = list(answers)
        self.description = []
        self.rowcount = 0
        self._current = []

    def execute(self, sql, params=None):
        self.sql.append(sql if isinstance(sql, str) else str(sql))
        self._current = self.answers.pop(0) if self.answers else []

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchmany(self, size=None):
        rows, self._current = list(self._current), []
        return rows

    def joined(self):
        return '\n'.join(self.sql)


def _method_source(path, name, cls=None):
    tree = ast.parse(_read(path))
    nodes = tree.body
    if cls:
        nodes = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = _read(path).splitlines()
    return '\n'.join(lines[node.lineno - 1:node.end_lineno])


# ─────────────────────────────────────────────────────────────────────────────
# Ключ, фрагменты и правило «все номера тестовые»
# ─────────────────────────────────────────────────────────────────────────────

class KeyHelpersTests(unittest.TestCase):

    def test_all_test(self):
        registry = frozenset({TEST})
        self.assertTrue(keys.all_test([TEST], registry))
        self.assertTrue(keys.all_test(['+7 700 000 01 01', '87000000101'], registry))
        self.assertFalse(keys.all_test([TEST, NORM], registry), 'настоящий клиент с дописанным номером остаётся')
        self.assertFalse(keys.all_test([], registry), 'сделка без номера не тестовая')
        self.assertFalse(keys.all_test(['6001'], registry))
        self.assertFalse(keys.all_test([TEST], frozenset()))

    def test_drop_test(self):
        calls = [{'external_number': '77000000101'}, {'external_number': '77000000202'}, {}]
        kept = keys.drop_test(calls, lambda c: c.get('external_number'), frozenset({TEST}))
        self.assertEqual(kept, [{'external_number': '77000000202'}, {}])
        self.assertEqual(keys.drop_test(calls, lambda c: c.get('external_number'), frozenset()), calls)

    def test_wazzup_phone_falls_back_to_chat_id_only_for_whatsapp(self):
        expr = keys.wazzup_phone_sql('w')
        self.assertIn("NULLIF(w.contact_phone, '')", expr)
        self.assertIn("WHEN w.chat_type IN ('whatsapp', 'wapi') THEN w.chat_id", expr)
        self.assertNotIn('w.', keys.wazzup_phone_sql())

    def test_two_field_condition_is_one_not_exists(self):
        fragment = keys.sql_not_test_any('r.client_phone', 'r.assigned_phone', digits=True)
        self.assertTrue(fragment.startswith('NOT EXISTS'))
        self.assertIn('IN (RIGHT(COALESCE((r.client_phone)::text', fragment)
        self.assertIn('RIGHT(COALESCE((r.assigned_phone)::text', fragment)

    def test_webhook_join_uses_the_index_expression_verbatim(self):
        """Иначе планировщик уйдёт в полный проход по неделе событий на каждом опросе табло."""
        source = _read(DATABASE_PY)
        index = re.search(r"idx_c2d_webhook_events_phone_tail\s+ON c2d_webhook_events \(\s*([^\n]+)\)\s*\n",
                          source).group(1).strip()
        self.assertEqual(index, r"right(regexp_replace(client_phone, '\D', '', 'g'), 9)")
        self.assertIn(index.replace('client_phone', '_tw.client_phone'), keys.C2D_WEBHOOK_TEST_REQUESTS_SQL)
        self.assertIn('_tw.client_id IS NOT NULL', keys.C2D_WEBHOOK_TEST_REQUESTS_SQL)


# ─────────────────────────────────────────────────────────────────────────────
# Пакеты: условие в SQL каждого расчёта
# ─────────────────────────────────────────────────────────────────────────────

class PackageSqlTests(unittest.TestCase):

    def test_cdr(self):
        from cdr import lead_queries, missed_queries, queries, touches
        self.assertIn(MARK, queries._FILTER_SQL, '«Касания»: итоги, таблица, выгрузка')
        self.assertIn(MARK, lead_queries._TOUCH_SQL, 'режим «Сделки»')
        for call in (lambda c: queries.day_touches_compact(c, date(2026, 10, 7)),
                     lambda c: queries.sample_operator_calls(c, '6001', date(2026, 10, 7), date(2026, 10, 7),
                                                             [touches.TYPE_IN]),
                     lambda c: queries.sample_day_calls(c, ['6001'], date(2026, 10, 7), [touches.TYPE_IN]),
                     lambda c: missed_queries.candidates(c, datetime(2026, 10, 7)),
                     lambda c: missed_queries.due_retries(c)):
            cursor = Recorder()
            call(cursor)
            self.assertIn(MARK, cursor.joined())

    def test_op_wallboard(self):
        from op_wallboard import routes
        day = date(2026, 10, 7)
        for call in (lambda c: routes.load_queue_answers(c, day),
                     lambda c: routes.load_announcement_deltas(c, day),
                     lambda c: routes.load_lead_touches(c, day),
                     lambda c: routes.load_chat_messages(c, datetime(2026, 10, 7), datetime(2026, 10, 8))):
            cursor = Recorder()
            call(cursor)
            self.assertIn(MARK, cursor.joined())

    def test_lead_deals_drop_only_deals_of_testers(self):
        from op_wallboard import routes
        created = datetime(2026, 10, 7, 9)
        cursor = Recorder([(TEST,)], [('L-test', created, TEST, ''), ('L-mixed', created, f'{TEST},{NORM}', ''),
                                      ('L-norm', created, NORM, ''), ('L-none', created, '', '')])
        deals = routes.load_lead_deals(cursor, date(2026, 10, 7))
        self.assertEqual([d['lead_key'] for d in deals], ['L-mixed', 'L-norm', 'L-none'])

    def test_op_funnel(self):
        from op_funnel import sources, sync
        cursor = Recorder()
        sources.wazzup_daily(cursor, date(2026, 10, 7), date(2026, 10, 7))
        self.assertIn(MARK, cursor.joined())
        rows = [{'phones': TEST, 'phone': ''}, {'phones': f'{TEST},{NORM}', 'phone': ''},
                {'phones': '', 'phone': '7' + TEST}, {'phones': '', 'phone': ''}]
        kept = sync._without_test_leads(Recorder([(TEST,)]), rows)
        self.assertEqual(kept, [rows[1], rows[3]])
        source = _read(ROOT / 'op_funnel' / 'sync.py')
        self.assertEqual(source.count('= _without_test_leads(cursor, '), 2, 'ночной прогон и инкремент amoCRM')

    def test_dial_list(self):
        from dial_list import analytics, sign_check
        self.assertIn(MARK, analytics._LEADS_CTE)
        cursor = Recorder()

        @contextmanager
        def get_cursor():
            yield cursor

        checker = sign_check.SignChecker.__new__(sign_check.SignChecker)
        checker.db = mock.Mock(_get_cursor=get_cursor)
        checker.resolve_successes()
        self.assertIn(MARK, cursor.joined())

    def test_resource_fte_chat(self):
        from resource_fte import chat
        day = date(2026, 10, 7)
        where, _params = chat._chat_billing_window(day, day, 0, 1439)
        self.assertIn(MARK, where)
        for call in (lambda c: chat._latest_chat_day_tx(c),
                     lambda c: chat._hourly_volume_tx(c, day, day),
                     lambda c: chat._reply_stats_tx(c, day, day, 60),
                     lambda c: chat._covered_days_tx(c, day, day),
                     lambda c: chat._weekday_profile_tx(c, day, day),
                     lambda c: chat._daily_history_tx(c, day, day),
                     lambda c: chat._channel_split_tx(c, day, day)):
            cursor = Recorder()
            call(cursor)
            self.assertIn(MARK, cursor.joined())
        cursor = Recorder()
        chat._weekday_profile_tx(cursor, day, day)
        self.assertEqual(cursor.joined().count(MARK), 2, 'и дни выборки, и сами чаты')

    def test_tez_productivity_skips_test_calls(self):
        from tez import op_productivity
        day = date(2026, 10, 7)
        call = {'employee_name': 'Иванова', 'start_time': '2026-10-07 10:00:00', 'billsec': 60}
        calls = {'1': {**call, 'external_number': '77000000101'}, '2': {**call, 'external_number': '77000000202'}}
        with mock.patch.object(op_productivity, '_call_local_date', lambda c, tz: day):
            rows, counters = op_productivity.aggregate_calls(
                calls, lambda name, d: 5, day, day, test_numbers={TEST})
        self.assertEqual(counters['skipped_test_number'], 1)
        self.assertEqual(sum(row.get('calls', 0) for row in rows), 1)

    def test_tez_successes_recompute_ignores_calls_of_test_leads(self):
        source = _method_source(ROOT / 'tez' / 'lead_service.py', 'recompute_outcomes')
        self.assertIn("[] if test_keys.is_test_phone(lead.get('phone_norm'), test_numbers) else lead['calls']",
                      source)


# ─────────────────────────────────────────────────────────────────────────────
# ИИ-оценка
# ─────────────────────────────────────────────────────────────────────────────

class AiQaTests(unittest.TestCase):

    def test_projection_pools_and_counts(self):
        from call_qa import api
        self.assertIn(MARK, api._SUBJECT_EXISTS, 'очередь, списки, дни, выгрузка, сводка дня')
        self.assertIn(MARK, api._RAW_BENCHMARK_SQL)
        for build in (api._wz_candidates, api._ca_candidates, api._c2d_candidates):
            sql, _params = build([1])
            self.assertIn(MARK, sql)
        for kind in ('wz_episode', 'ca_episode'):
            cursor = Recorder()
            api._episode_eligibility_counts(cursor, [1], kind)
            self.assertIn(MARK, cursor.joined())
        cursor = Recorder()
        api._c2d_eligibility_counts(cursor, [1])
        self.assertIn(MARK, cursor.joined())
        cursor = Recorder()
        api._reviewed_metrics(cursor)
        self.assertIn(MARK, cursor.sql[0])
        random_call = _method_source(ROOT / 'call_qa' / 'api.py', 'random_call')
        self.assertEqual(random_call.count('test_keys.sql_not_test('), 2, 'оба пула «Случайного звонка»')

    def test_gate_refuses_test_numbers_of_every_kind(self):
        from call_qa import subjects
        for kind in ('call', 'imported_call', 'wz_episode', 'c2d_snapshot', 'ca_episode'):
            verdict = subjects.eligibility({'kind': kind, 'is_test_number': True})
            self.assertFalse(verdict['ok'], kind)
            self.assertEqual(verdict['reason'], subjects.REASON_TEST_NUMBER)
        self.assertTrue(subjects.eligibility({'kind': 'call'})['ok'])
        source = _read(ROOT / 'call_qa' / 'subjects.py')
        self.assertEqual(source.count('"is_test_number": bool(row['), 5, 'признак ставит каждый загрузчик')

    def test_batch_eval_and_linker(self):
        source = _read(ROOT / 'call_qa' / 'batch_eval.py')
        self.assertIn("test_keys.sql_not_test('c.phone_number')", source)
        self.assertIn('_WZ_NOT_TEST if is_wz else _CA_NOT_TEST', source)
        self.assertIn('_C2D_NOT_TEST.format(alias="t")', source)
        from call_qa.marketing import linker
        cursor = Recorder([('call', 1, '77000000101', datetime(2026, 10, 7)),
                           ('call', 2, '77000000202', datetime(2026, 10, 7))], [(TEST,)])
        with mock.patch.object(linker, '_SUBJECT_PHONE', 'x', create=True):
            subjects = linker._fetch_subjects(cursor, only_new=False)
        self.assertEqual([s['call_id'] for s in subjects], [2])


# ─────────────────────────────────────────────────────────────────────────────
# Монолиты
# ─────────────────────────────────────────────────────────────────────────────

class DatabaseMethodsTests(unittest.TestCase):

    PLACES = (
        '_c2d_requests_where', 'pick_wazzup_episode', 'pick_chatapp_episode', 'list_wazzup_episodes',
        'get_c2d_webhook_events', 'get_c2d_open_chats_by_operator', 'wazzup_operator_analytics',
        'refresh_tez_op_chat_metrics', 'get_tez_lead_funnel', 'import_single_random_call',
        'import_calls_from_distribution', 'get_imported_calls_status_counts_by_operator',
        'get_operator_stats', 'get_call_evaluations', 'get_operator_score_aggregates_for_month',
        'get_feedback_sla_rows_for_month', 'get_operators_summary_for_month', 'get_week_call_stats',
        'get_average_scores_for_period',
    )

    def test_every_place_imports_the_registry_locally(self):
        """Импорт внутри метода: тесты вырезают методы через AST, модульного импорта там нет."""
        for name in self.PLACES:
            with self.subTest(name=name):
                body = _method_source(DATABASE_PY, name, cls='Database')
                self.assertIn('from test_numbers import keys as test_keys', body)
                self.assertRegex(body, r'test_keys\.(sql_|is_test_phone|cached_keys|C2D_WEBHOOK)')

    def test_tez_funnel_filters_all_three_lead_counts(self):
        body = _method_source(DATABASE_PY, 'get_tez_lead_funnel', cls='Database')
        self.assertEqual(body.count('+ not_test +'), 3)


class ProviderTests(unittest.TestCase):
    """Ключи для живых источников: приложение настраивает пакет при старте."""

    def setUp(self):
        keys.configure(None)
        self.addCleanup(keys.configure, None)

    def configure(self, registry):
        @contextmanager
        def get_cursor():
            yield Recorder([(key,) for key in sorted(registry)])

        keys.configure(get_cursor)

    def test_unconfigured_registry_is_empty(self):
        """Так в тестах и скриптах: вырезанная функция монолита ведёт себя как прежде."""
        self.assertEqual(keys.current_keys(), frozenset())
        self.assertEqual(keys.tsql_and_not_test('t.[number]'), '')
        calls = [{'external_number': '77000000101'}]
        self.assertEqual(keys.drop_test_calls(calls), calls)

    def test_oktell_condition_takes_only_digit_keys(self):
        self.configure({TEST, "x' OR 1=1 --"})
        condition = keys.tsql_and_not_test('t.[number]')
        self.assertTrue(condition.startswith('AND RIGHT('))
        self.assertIn(f"NOT IN ('{TEST}')", condition)
        self.assertNotIn('OR 1=1', condition)
        self.assertTrue(condition.endswith(' '), 'склеивается со следующей частью WHERE')

    def test_drop_test_calls(self):
        self.configure({TEST})
        calls = [{'external_number': '77000000101'}, {'external_number': '77000000202'}]
        self.assertEqual(keys.drop_test_calls(calls), [calls[1]])
        self.assertIsNone(keys.drop_test_calls(None))

    def test_chat2desk_rows(self):
        registry = frozenset({TEST})
        self.assertTrue(keys.c2d_row_is_test({'phone': '77000000101'}, registry))
        self.assertTrue(keys.c2d_row_is_test(
            {'phone': '[wa_dialog] KZ.1000000000000001', 'assigned_phone': '77000000101'}, registry))
        self.assertFalse(keys.c2d_row_is_test({'phone': '77000000202'}, registry))
        self.assertFalse(keys.c2d_row_is_test({'phone': '77000000101'}, frozenset()))
        ids = keys.c2d_test_request_ids(
            registry, [{'request_id': '5', 'phone': '77000000101'}, {'request_id': 6, 'phone': '77000000202'}],
            None, [{'request_id': 7, 'assigned_phone': '87000000101'}])
        self.assertEqual(ids, {5, 7})


class BotScheduleTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = _read(BOT_PY)
        cls.tree = ast.parse(cls.source)

    def setUp(self):
        keys.configure(None)
        self.addCleanup(keys.configure, None)

    def test_app_configures_the_registry_at_start(self):
        self.assertIn('_test_numbers_keys.configure(db._get_cursor)', self.source)

    def test_every_oktell_builder_carries_the_condition(self):
        for name, count in (('_oktell_wallboard_totals_sql', 3), ('_oktell_wallboard_hourly_sql', 1),
                            ('_oktell_billing_sql', 1), ('_oktell_billing_detail_source_sql', 1),
                            ('_oktell_billing_operator_calls_sql', 1), ('_oktell_resource_hourly_sql', 1),
                            ('_oktell_eval_operators_sql', 1), ('_oktell_eval_sample_sql', 1)):
            with self.subTest(name=name):
                body = _method_source(BOT_PY, name)
                self.assertEqual(body.count('test_keys.tsql_and_not_test('), count)
                self.assertIn('from test_numbers import keys as test_keys', body)

    def test_extracted_builder_works_in_a_bare_namespace(self):
        """Тесты вырезают построители через AST: вставка реестра не должна требовать
        ничего, кроме самого пакета."""
        node = next(n for n in self.tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == '_oktell_resource_hourly_sql')
        namespace = {'_OKTELL_GREETING_ABANDON': 'g', '_OKTELL_FAILED_CALL': 'f'}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PY), 'exec'), namespace)
        sql = namespace['_oktell_resource_hourly_sql']('20261008', '20261009')
        self.assertNotIn('RIGHT(REPLACE', sql, 'без настройки реестр пуст — запрос прежний')

        @contextmanager
        def get_cursor():
            yield Recorder([(TEST,)])

        keys.configure(get_cursor)
        self.assertIn(f"NOT IN ('{TEST}')", namespace['_oktell_resource_hourly_sql']('20261008', '20261009'))

    def test_binotel_journal_is_filtered_everywhere(self):
        """Каждый выкачанный журнал Binotel проходит через фильтр реестра."""
        calls = [m.start() for m in re.finditer(r'\.list_calls_for_day\(', self.source)]
        self.assertGreaterEqual(len(calls), 4)
        for start in calls:
            window = self.source[max(0, start - 160):start]
            self.assertIn('test_keys.drop_test_calls(', window, self.source[start - 80:start + 40])
        self.assertIn('test_keys.drop_test_calls(client.list_calls_by_internal_number(', self.source)

    def test_chat_metrics_drop_test_requests(self):
        """Время ответа и оценки — без тестовых обращений, «Чаты» оператора — за вычетом их."""
        from test_chat_report_import import OPERATORS, _chat_report_namespace
        ns = _chat_report_namespace()
        lookup, index = {}, []
        for oid, person in OPERATORS:
            for variant in ns['_status_import_operator_name_variants'](person):
                lookup.setdefault(variant, []).append({'id': oid, 'name': person})
            index.append({'id': oid, 'name': person, 'tokens': ns['_chat_report_name_tokens'](person)})
        name = OPERATORS[1][1]
        request_stats = [
            {'operator_name': name, 'request_start': '2026-06-10 10:00:00', 'reaction_time': '10',
             'phone': '77000000202', 'request_id': 1},
            {'operator_name': name, 'request_start': '2026-06-10 10:05:00', 'reaction_time': '500',
             'phone': '77000000101', 'request_id': 2},
        ]
        ratings = [{'operator_name': name, 'created_at': '2026-06-10 12:00:00', 'rating_scale_score': '1',
                    'phone': '[wa_dialog] KZ.1000000000000001', 'request_id': 2}]
        operator_stats = [{'operator_name': name, 'requests_took_part': '2', 'date': '2026-06-10'}]
        build = ns['_chat2desk_build_metrics_from_statistics_rows']
        report = build('2026-06-10', None, ratings, lookup, index, operator_stats_rows=operator_stats,
                       request_stats_rows=request_stats, test_numbers={TEST})
        metric = next(m for m in report['metrics'] if m['day'] == '2026-06-10')
        self.assertEqual(metric['avg_response_time_seconds'], 10.0)
        self.assertEqual(metric['chats_count'], 1)
        self.assertEqual(report['low_rating_count'], 0)
        self.assertEqual(report['excluded_test_number_rows'], 2)
        # Без реестра — ровно прежние цифры.
        before = build('2026-06-10', None, ratings, lookup, index, operator_stats_rows=operator_stats,
                       request_stats_rows=request_stats)
        metric = next(m for m in before['metrics'] if m['day'] == '2026-06-10')
        self.assertEqual((metric['avg_response_time_seconds'], metric['chats_count'], before['low_rating_count']),
                         (255.0, 2, 1))

    def test_journal_and_pull_call(self):
        report = _method_source(BOT_PY, 'handle_monthly_report')
        self.assertEqual(report.count("test_keys.sql_not_test('phone_number')"), 2)
        sv = _method_source(BOT_PY, 'show_sv_evaluations')
        self.assertEqual(sv.count("test_keys.sql_not_test('phone_number')"), 1)
        pull = _method_source(BOT_PY, 'api_ai_qa_pull_call')
        self.assertIn('test_keys.is_test_phone(phone, test_keys.current_keys())', pull)

    def test_chat_hourly_and_ai_sample(self):
        for name in ('_chat_hourly_fetch_requests', '_szov_chat_wallboard_day_requests'):
            self.assertIn('test_keys.c2d_row_is_test(row, test_numbers)', _method_source(BOT_PY, name))
        sample = _method_source(BOT_PY, '_ai_qa_sample_c2d_candidates')
        self.assertIn('{not_test}', sample)


if __name__ == '__main__':
    unittest.main()
