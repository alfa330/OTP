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
import functools
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


@functools.lru_cache(maxsize=None)
def _read(path):
    return Path(path).read_text(encoding='utf-8-sig')


@functools.lru_cache(maxsize=None)
def _parse(path):
    """Монолит разбирается секунды — один раз на файл."""
    return ast.parse(_read(path))


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
    tree = _parse(path)
    nodes = tree.body
    if cls:
        nodes = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = _read(path).splitlines()
    return '\n'.join(lines[node.lineno - 1:node.end_lineno])


def _exec_method(path, name, cls=None):
    """Метод так, как его вырезают тесты проекта: AST → exec в пустом пространстве имён
    (модульных имён монолита там нет — реестр обязан импортироваться внутри метода)."""
    import textwrap
    namespace = {}
    exec(compile(textwrap.dedent(_method_source(path, name, cls=cls)), str(path), 'exec'), namespace)
    return namespace[name]


# ─────────────────────────────────────────────────────────────────────────────
# Ключ, фрагменты и правило «все номера тестовые»
# ─────────────────────────────────────────────────────────────────────────────

class KeyHelpersTests(unittest.TestCase):

    def test_journal_row_checks_the_c2d_request_number_too(self):
        """Оценка чата Chat2Desk с клиентом-идентификатором: в phone_number «[wa_…] KZ.…»,
        номер — в assigned_phone обращения. Без снапшота подзапрос пуст — строка не выпадает."""
        plain, aliased = keys.sql_calls_not_test(), keys.sql_calls_not_test('c')
        self.assertTrue(plain.startswith(keys.sql_not_test('phone_number') + ' AND NOT EXISTS ('))
        self.assertIn('WHERE _ts.id = c2d_snapshot_id AND ', plain)
        # Обращение у снапшота есть только у Chat2Desk — соединение не должно терять Wazzup.
        self.assertIn('LEFT JOIN c2d_requests _tr ON _tr.request_id = _ts.request_id', plain)
        self.assertIn(keys.sql_digits_key('_tr.assigned_phone'), plain)
        # WhatsApp Wazzup без contact_phone: номер — сам chat_id; у Telegram chat_id не номер.
        self.assertIn("CASE WHEN _ts.source = 'wazzup' AND _ts.transport IN ('whatsapp', 'wapi') THEN "
                      + keys.sql_key('_ts.wz_chat_id') + ' END', plain)
        self.assertIn('WHERE _ts.id = c.c2d_snapshot_id AND ', aliased)
        self.assertTrue(aliased.startswith(keys.sql_not_test('c.phone_number')))
        self.assertNotIn('NOT IN', plain)

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

    def test_two_field_condition_is_a_not_exists_per_field(self):
        """IN-список давал вложенный цикл с поиском по реестру на каждую строку (вчетверо
        дороже на годовом окне c2d_requests) — по NOT EXISTS на поле, в скобках."""
        fragment = keys.sql_not_test_any('r.client_phone', 'r.assigned_phone', digits=True)
        self.assertEqual(fragment, '(' + keys.sql_not_test('r.client_phone', digits=True) + ' AND '
                         + keys.sql_not_test('r.assigned_phone', digits=True) + ')')
        self.assertNotIn(' IN (', fragment)
        self.assertEqual(keys.sql_not_test_keys('k1', 'k2'),
                         "(NOT EXISTS (SELECT 1 FROM test_phone_numbers _tpn WHERE _tpn.phone_key = k1)"
                         " AND NOT EXISTS (SELECT 1 FROM test_phone_numbers _tpn WHERE _tpn.phone_key = k2))")

    def test_key_has_the_same_form_everywhere(self):
        """Ключ — последние 10 ASCII-цифр во всех трёх мирах; длина 11 или ключ T-SQL без
        очистки молча перестали бы совпадать с реестром."""
        self.assertEqual(keys.KEY_LENGTH, 10)
        self.assertEqual(keys.sql_key('x'), "RIGHT(REGEXP_REPLACE(COALESCE((x)::text, ''), '[^0-9]', '', 'g'), 10)")
        self.assertEqual(keys.sql_digits_key('x'), "RIGHT(COALESCE((x)::text, ''), 10)")
        self.assertEqual(keys.tsql_key('x'),
                         "RIGHT(REPLACE(REPLACE(REPLACE(ISNULL(x, ''), '+', ''), ' ', ''), '-', ''), 10)")
        self.assertEqual(keys.tsql_not_test('x', frozenset({NORM, TEST})),
                         keys.tsql_key('x') + f" NOT IN ('{TEST}', '{NORM}')")
        for raw in ('+7 (700) 000-01-01', '8 700 000 01 01', '77000000101', '7000000101'):
            self.assertEqual(keys.phone_key(raw), TEST, raw)
        self.assertIsNone(keys.phone_key('٧٠٠٠٠٠٠١٠١'), 'не ASCII-цифры ключом не становятся')

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
        # Робот пропущенных пишет условие литералом (его SQL разбирает тест грамматики) —
        # литерал обязан совпадать с правилом пакета дословно.
        source = _read(ROOT / 'cdr' / 'missed_queries.py')
        for alias in ('t', 'm'):
            self.assertIn(keys.sql_not_test(f'{alias}.phone', digits=True), source)

    def test_deals_mode_drops_only_deals_of_testers(self):
        """«Касания» → «Сделки»: сделка выпадает, только если тестовые ВСЕ её номера."""
        from cdr import lead_queries

        def deal(key, phones):
            row = dict.fromkeys(lead_queries._LEAD_KEYS)
            row.update({'lead_key': key, 'phones': phones, 'phone': ''})
            return tuple(row[k] for k in lead_queries._LEAD_KEYS)

        cursor = Recorder([(TEST,)], [deal('L-test', TEST), deal('L-mixed', f'{TEST},{NORM}'),
                                      deal('L-norm', NORM), deal('L-none', '')])
        kept = lead_queries.select_leads(cursor, 'amo', 'op_osnova', date(2026, 10, 1), date(2026, 10, 7))
        self.assertEqual([d['lead_key'] for d in kept], ['L-mixed', 'L-norm', 'L-none'])

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
        cursor = Recorder([('L-test', created, TEST, ''), ('L-mixed', created, f'{TEST},{NORM}', ''),
                           ('L-norm', created, NORM, ''), ('L-none', created, '', '')], [(TEST,)])
        deals = routes.load_lead_deals(cursor, date(2026, 10, 7))
        self.assertEqual([d['lead_key'] for d in deals], ['L-mixed', 'L-norm', 'L-none'])
        # Сделки — первым запросом: его параметры проверяет test_op_wallboard_lead_speed.
        self.assertIn('op_funnel_leads', cursor.sql[0])

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
        # Журнал этапов — сырьё: пишется по всем сделкам ДО отбора, иначе номер, внесённый
        # в реестр по ошибке, навсегда съел бы историю сделки.
        for name in ('sync_direction', 'sync_amo_changes'):
            body = _method_source(ROOT / 'op_funnel' / 'sync.py', name)
            self.assertLess(body.index('queries.log_lead_stages(cursor, '), body.index('_without_test_leads(cursor, '),
                            name)

    def test_dial_list(self):
        from dial_list import analytics, sign_check
        self.assertIn(keys.DIAL_LIST_LEAD_NOT_TEST_SQL, analytics._LEADS_CTE)
        self.assertIn("_tpn.phone_key = RIGHT(COALESCE((l.phone_norm)::text, ''), 10)",
                      keys.DIAL_LIST_LEAD_NOT_TEST_SQL)
        cursor = Recorder()

        @contextmanager
        def get_cursor():
            yield cursor

        checker = sign_check.SignChecker.__new__(sign_check.SignChecker)
        checker.db = mock.Mock(_get_cursor=get_cursor)
        checker.resolve_successes()
        self.assertIn(MARK, cursor.joined())

    def test_dial_list_overview_and_progress(self):
        """Сводка дня руководителя и «Мой прогресс» оператора — без работы по тестовому лиду."""
        from dial_list import service
        cursor = Recorder()

        @contextmanager
        def get_cursor():
            yield cursor

        svc = service.DialListService(mock.Mock(_get_cursor=get_cursor))
        for departments in (None, [1954]):
            cursor.sql.clear()
            svc.overview(departments, date(2026, 10, 7))
            # Попытки, выдачи, успешки — каждая по своему соединению с лидом.
            self.assertEqual(cursor.joined().count(keys.DIAL_LIST_LEAD_NOT_TEST_SQL), 3)
        cursor.sql.clear()
        svc.operator_context = lambda uid: {'user_id': uid, 'department_id': 1954, 'name': 'Оператор'}
        svc.operator_progress(7)
        progress, outcomes, subtypes = cursor.sql
        self.assertEqual(progress.count(keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL), 1)
        self.assertEqual(progress.count(keys.DIAL_LIST_ASSIGNMENT_NOT_TEST_SQL), 1)
        self.assertIn(keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL, outcomes)
        self.assertIn(keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL, subtypes)

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
        joined = cursor.joined()
        self.assertEqual(joined.count(chat._NOT_TEST_SQL) + joined.count(chat._NOT_TEST_SQL_R), 2,
                         'и дни выборки, и сами чаты')
        # «Последний день с данными»: MAX(day) с условием реестра терял оптимизацию min/max и
        # читал всю c2d_requests (стенд: 88 мс против 0,4) — обратный проход по индексу дня.
        cursor = Recorder([(date(2026, 10, 8),)])
        self.assertEqual(chat._latest_chat_day_tx(cursor), date(2026, 10, 8))
        self.assertIn('ORDER BY day DESC LIMIT 1', cursor.sql[0])
        self.assertNotIn('MAX(day)', cursor.sql[0])
        self.assertIsNone(chat._latest_chat_day_tx(Recorder([])))

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
        """Пересчёт сам берёт ключи реестра у базы: у тестового лида звонков нет."""
        from tez import lead_service
        keys.invalidate_cache()
        self.addCleanup(keys.invalidate_cache)
        seen = {}

        def outcome(trip_at, prev_at, calls, **_kw):
            seen[len(seen)] = calls
            return {'status': 'not_counted', 'rule': '', 'operator_id': None}

        @contextmanager
        def get_cursor():
            yield Recorder([(TEST,)])

        call = {'general_call_id': 'g1', 'employee_name': 'Иванова'}
        db = mock.Mock(_get_cursor=get_cursor)
        db.get_tez_leads_for_recompute.return_value = [
            {'id': 'a', 'phone_norm': '7' + TEST, 'calls': [call], 'month_first_order_at': None,
             'prev_month_first_order_at': None},
            {'id': 'b', 'phone_norm': '7' + NORM, 'calls': [call], 'month_first_order_at': None,
             'prev_month_first_order_at': None}]
        db.count_tez_successes.return_value = 0
        db.apply_tez_lead_outcomes.return_value = {}
        with mock.patch.object(lead_service, 'compute_lead_outcome', outcome):
            lead_service.recompute_outcomes(db, 2026, 10)
        self.assertEqual(seen, {0: [], 1: [call]})

    def test_tez_productivity_takes_registry_keys_from_the_db(self):
        from tez import op_productivity
        keys.invalidate_cache()
        self.addCleanup(keys.invalidate_cache)

        @contextmanager
        def get_cursor():
            yield Recorder([(TEST,)])

        self.assertEqual(op_productivity._test_numbers(mock.Mock(_get_cursor=get_cursor)), frozenset({TEST}))
        self.assertEqual(op_productivity._test_numbers(object()), frozenset())


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
        # И каждый загрузчик считает признак своим полем, а не FALSE.
        for fragment in ("test_keys.sql_is_test('c.phone_number')",
                         "test_keys.sql_is_test(test_keys.wazzup_phone_sql('e'), digits=True)",
                         "test_keys.sql_is_test('ic.phone_number')",
                         'test_keys.sql_is_test_any(',
                         "test_keys.sql_is_test('e.contact_phone', digits=True)"):
            self.assertEqual(source.count('""" + ' + fragment), 1, fragment)

    def test_every_subject_kind_has_its_phone_key(self):
        """Звонок, эпизод Wazzup, импортированный звонок, заявка Chat2Desk (оба поля) и эпизод
        ChatApp: выпавший ключ вернул бы тестовые разговоры своего вида в очередь и списки."""
        from call_qa import api
        self.assertEqual(api._SUBJECT_PHONE_KEYS, (
            keys.sql_key('c.phone_number'),
            keys.sql_digits_key(keys.wazzup_phone_sql('e')),
            keys.sql_key("COALESCE(NULLIF(ic.phone_normalized, ''), ic.phone_number)"),
            keys.sql_digits_key('cs.client_phone'),
            keys.sql_digits_key('(SELECT r.assigned_phone FROM c2d_requests r WHERE r.request_id = cs.request_id)'),
            keys.sql_digits_key('ce.contact_phone'),
        ))
        self.assertEqual(api._SUBJECT_NOT_TEST, keys.sql_not_test_keys(*api._SUBJECT_PHONE_KEYS))

    # Сколько раз в функции применено общее условие ИИ-оценки: (_SUBJECT_EXISTS, _SUBJECT_NOT_TEST).
    API_APPLIED = {
        '_queue_items': (1, 0), 'review_queue_count': (1, 0), 'review_queue_days': (1, 0),
        'filter_options': (1, 0), 'evaluations_count': (1, 0), 'evaluations_list': (1, 0),
        '_evaluations_where': (1, 0), '_reviewed_metrics': (0, 1), '_filtered_stats': (2, 0),
        'stats': (2, 1), 'marketing_options': (1, 0),
    }

    def test_every_ai_qa_reader_applies_the_condition(self):
        for name, (exists, not_test) in self.API_APPLIED.items():
            code = '\n'.join(line for line in _method_source(ROOT / 'call_qa' / 'api.py', name).splitlines()
                             if not line.strip().startswith('#'))
            with self.subTest(name=name):
                self.assertEqual(len(re.findall(r'_SUBJECT_EXISTS\b', code)), exists)
                self.assertEqual(len(re.findall(r'_SUBJECT_NOT_TEST\b', code)), not_test)

    def test_batch_eval_and_linker(self):
        source = _read(ROOT / 'call_qa' / 'batch_eval.py')
        self.assertIn("test_keys.sql_not_test('c.phone_number')", source)
        self.assertIn('_WZ_NOT_TEST if is_wz else _CA_NOT_TEST', source)
        self.assertIn('_C2D_NOT_TEST.format(alias="t")', source)
        from call_qa.marketing import linker
        cursor = Recorder([(TEST,)], [('call', 1, '77000000101', datetime(2026, 10, 7)),
                                      ('call', 2, '77000000202', datetime(2026, 10, 7))])
        with mock.patch.object(linker, '_SUBJECT_PHONE', 'x', create=True):
            subjects = linker._fetch_subjects(cursor, only_new=False)
        self.assertEqual([s['call_id'] for s in subjects], [2])
        # Ключи — первым запросом, субъекты — последним: по последнему запросу тесты
        # связки проверяют условие редакции правил (test_ai_qa_marketing).
        self.assertEqual(cursor.sql, [f'SELECT phone_key FROM {MARK}', cursor.sql[-1]])
        self.assertNotIn(MARK, cursor.sql[-1])


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
        # Успешки ОП Тез — при чтении: пересчёт закрытых месяцев не идёт, снятая им успешка
        # иначе осталась бы в рейтинге, «Учёте часов», плане и воронке навсегда.
        'count_tez_successes', 'get_tez_operator_successes', 'get_tez_successes_by_day',
        'get_tez_successes_for_day', 'get_tez_successes_operator_day', 'get_tez_success_counts_for_operators',
        # Детализация и выгрузка лидов — тот же периметр, что у воронки.
        '_tez_leads_detail_filters', 'get_tez_leads_report',
    )

    # Сколько раз условие ПРИМЕНЕНО в методе (а не просто упомянуто): снятое второе условие,
    # «AND TRUE» вместо фрагмента или «if False» вместо проверки номера разбор находил
    # мутациями, а проверка «есть хоть одно упоминание» их пропускала.
    _SQL_S = "test_keys.sql_not_test('s.phone_norm', digits=True)"
    APPLIED = {
        '_c2d_requests_where': [("test_keys.sql_not_test_any('r.client_phone', 'r.assigned_phone', digits=True)]", 1)],
        'pick_wazzup_episode': [("test_keys.sql_not_test(test_keys.wazzup_phone_sql('e'), digits=True)]", 1)],
        'pick_chatapp_episode': [("test_keys.sql_not_test('e.contact_phone', digits=True)]", 1)],
        'list_wazzup_episodes': [('["TRUE", test_keys.sql_not_test(test_keys.wazzup_phone_sql(), digits=True)]', 1)],
        'get_c2d_webhook_events': [('+ test_keys.C2D_WEBHOOK_TEST_REQUESTS_SQL +', 1)],
        'get_c2d_open_chats_by_operator': [('AND request_id NOT IN ("""'
                                            ' + test_keys.C2D_WEBHOOK_TEST_REQUESTS_SQL + """)', 1)],
        'wazzup_operator_analytics': [("test_keys.sql_not_test(test_keys.wazzup_phone_sql('m'), digits=True)]", 1)],
        'refresh_tez_op_chat_metrics': [('AND {not_test_chat}', 1)],
        'get_tez_lead_funnel': [('AND """ + not_test + """', 4)],
        'import_single_random_call': [('if test_keys.is_test_phone(phone, test_keys.cached_keys(self._get_cursor)):', 1)],
        'import_calls_from_distribution': [("if test_keys.is_test_phone(c.get('phone'), test_numbers):", 1),
                                           ('test_numbers = test_keys.cached_keys(self._get_cursor)', 1)],
        'get_imported_calls_status_counts_by_operator': [('AND """ + test_keys.sql_not_test(', 1)],
        'get_operator_stats': [('AND """ + not_test_calls + """', 2)],
        'get_call_evaluations': [('AND """ + test_keys.sql_calls_not_test() + """', 1),
                                 ('AND """ + test_keys.sql_not_test(', 1)],
        'get_operator_score_aggregates_for_month': [
            ('filter_clause = "month = %s AND is_draft = FALSE AND " + test_keys.sql_calls_not_test()', 1)],
        'get_feedback_sla_rows_for_month': [('AND {not_test_calls}', 1)],
        'get_operators_summary_for_month': [('AND """ + test_keys.sql_calls_not_test() + """', 1)],
        'get_week_call_stats': [('AND """ + test_keys.sql_calls_not_test() + """', 1)],
        'get_average_scores_for_period': [('AND """ + test_keys.sql_calls_not_test() + """', 1)],
        'count_tez_successes': [('+ ' + _SQL_S + ',', 1)],
        'get_tez_operator_successes': [('AND {' + _SQL_S + '}', 1)],
        'get_tez_successes_by_day': [('AND {' + _SQL_S + '}', 1)],
        'get_tez_successes_for_day': [('AND {' + _SQL_S + '}', 1)],
        'get_tez_successes_operator_day': [('AND {' + _SQL_S + '}', 1)],
        'get_tez_success_counts_for_operators': [('AND """ + ' + _SQL_S + ' + """', 1)],
        '_tez_leads_detail_filters': [("sql = ' AND ' + test_keys.sql_not_test('l.phone_norm', digits=True)", 1)],
        'get_tez_leads_report': [("WHERE \"\"\" + test_keys.sql_not_test('r.phone_norm', digits=True) + \"\"\"", 1)],
    }

    def test_every_place_imports_the_registry_locally(self):
        """Импорт внутри метода: тесты вырезают методы через AST, модульного импорта там нет."""
        self.assertEqual(set(self.APPLIED), set(self.PLACES))
        for name in self.PLACES:
            with self.subTest(name=name):
                body = _method_source(DATABASE_PY, name, cls='Database')
                self.assertIn('from test_numbers import keys as test_keys', body)
                for needle, count in self.APPLIED[name]:
                    self.assertEqual(body.count(needle), count, needle)

    def test_tez_funnel_filters_lead_counts_and_successes(self):
        """База, звонки, перенос и успешки — одним условием: числитель и знаменатель
        конверсии считаются по одному периметру."""
        body = _method_source(DATABASE_PY, 'get_tez_lead_funnel', cls='Database')
        self.assertEqual(body.count('+ not_test +'), 4)

    def test_tez_success_readers_run_with_the_registry_condition(self):
        """Методы исполняются (вырезанные AST, без модульных имён) и шлют условие в SQL."""
        db = mock.Mock()
        cursor = Recorder()

        @contextmanager
        def get_cursor():
            yield cursor

        db._get_cursor = get_cursor
        db._TEZ_GROUP_FILTER_SQL = ''
        db._tez_leads_detail_filters = _exec_method(DATABASE_PY, '_tez_leads_detail_filters', cls='Database')
        calls = (('count_tez_successes', (2026, 9)), ('get_tez_operator_successes', (2026, 9)),
                 ('get_tez_successes_by_day', (2026, 9)), ('get_tez_successes_for_day', (2026, 9, date(2026, 9, 20))),
                 ('get_tez_successes_operator_day', (2026, 9)),
                 ('get_tez_success_counts_for_operators', ([7], 2026, 9)),
                 ('count_tez_leads_detail', (2026, 9)), ('get_tez_leads_detail', (2026, 9)),
                 ('get_tez_leads_report', (2026, 9)))
        for name, args in calls:
            with self.subTest(name=name):
                cursor.sql.clear()
                cursor.answers = [[(0,)]] if name.startswith('count_') else []
                _exec_method(DATABASE_PY, name, cls='Database')(db, *args)
                self.assertIn(MARK, cursor.joined())


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

    # Живые источники бота: где ключи реестра берутся и где применяются. Подмена
    # current_keys() пустым набором или снятый фильтр звонков разбором не ловились.
    BOT_APPLIED = {
        'api_ai_qa_pull_call': [('if phone and test_keys.is_test_phone(phone, test_keys.current_keys()):', 1)],
        '_ai_qa_sample_binotel_candidates': [('for call in test_keys.drop_test_calls(client.list_calls_for_day(day)):', 1)],
        '_ai_qa_sample_c2d_candidates': [('AND {not_test}', 1)],
        '_binotel_random_call': [('calls = test_keys.drop_test_calls(client.list_calls_by_internal_number(', 1)],
        '_chat2desk_build_daily_metrics': [('test_numbers=test_keys.current_keys(),', 1)],
        '_chat_hourly_fetch_requests': [('test_numbers = test_keys.current_keys()', 1),
                                        ('and not test_keys.c2d_row_is_test(row, test_numbers)]', 1)],
        '_szov_chat_wallboard_day_requests': [('test_numbers = test_keys.current_keys()', 1),
                                              ('and not test_keys.c2d_row_is_test(row, test_numbers)]', 1)],
        '_tez_wallboard_journal_worker': [('calls = test_keys.drop_test_calls(', 1)],
        '_tez_broadcast_journal': [('lambda: test_keys.drop_test_calls(', 1)],
        'sync_binotel_evaluation_calls': [('day_calls = test_keys.drop_test_calls(client.list_calls_for_day(day))', 1)],
        '_oktell_wallboard_totals_sql': [("test_keys.tsql_and_not_test('a.AOutNumber')", 1),
                                         ("test_keys.tsql_and_not_test('c.AOutNumber')", 1),
                                         ("test_keys.tsql_and_not_test('x.[number]')", 1)],
        '_chat2desk_build_metrics_from_statistics_rows': [('test_keys.c2d_row_is_test(row, test_numbers)', 3),
                                                          ('test_keys.c2d_test_request_ids(', 1)],
    }

    def test_bot_live_sources_take_and_apply_the_registry(self):
        for name, needles in self.BOT_APPLIED.items():
            body = _method_source(BOT_PY, name)
            for needle, count in needles:
                with self.subTest(name=name, needle=needle):
                    self.assertEqual(body.count(needle), count)

    def test_journal_and_pull_call(self):
        report = _method_source(BOT_PY, 'handle_monthly_report')
        self.assertEqual(report.count("test_keys.sql_calls_not_test()"), 2)
        sv = _method_source(BOT_PY, 'show_sv_evaluations')
        self.assertEqual(sv.count("test_keys.sql_calls_not_test()"), 1)
        # Журнал во всех агрегатах — одним условием, с номером обращения у чатов Chat2Desk.
        source = _read(DATABASE_PY)
        self.assertEqual(source.count("test_keys.sql_calls_not_test()"), 7)
        self.assertNotIn("test_keys.sql_not_test('phone_number')", source)
        pull = _method_source(BOT_PY, 'api_ai_qa_pull_call')
        self.assertIn('test_keys.is_test_phone(phone, test_keys.current_keys())', pull)

    def test_chat_hourly_and_ai_sample(self):
        for name in ('_chat_hourly_fetch_requests', '_szov_chat_wallboard_day_requests'):
            self.assertIn('test_keys.c2d_row_is_test(row, test_numbers)', _method_source(BOT_PY, name))
        sample = _method_source(BOT_PY, '_ai_qa_sample_c2d_candidates')
        self.assertIn('{not_test}', sample)


if __name__ == '__main__':
    unittest.main()
