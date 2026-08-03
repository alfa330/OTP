"""Раздел «Табло СЗоВ» (задача #108): доступ, SQL к Oktell, сборка показателей, кэш.

Функции бэкенда вытаскиваем из bot_schedule2.py через ast и исполняем в подготовленном
namespace — так проверяется настоящая логика, а не строковое совпадение. Импортировать сам
модуль нельзя: он на старте поднимает пул к боевой БД и падает на Windows (time.tzset).
"""
import ast
import logging
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_names(source, names, namespace, label="<szov-wallboard>"):
    """Исполняет в namespace перечисленные функции и присваивания модульного уровня."""
    tree = ast.parse(source)
    wanted = set(names)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, label, "exec"), namespace)
    missing = sorted(name for name in wanted if name not in namespace)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return namespace


class _FakeDb:
    """Минимальный db: отделы, отдел пользователя, состав отдела."""

    def __init__(self, departments=None, user_departments=None, members=None):
        self.departments = departments if departments is not None else [{'id': 1, 'code': 'szov'}]
        self.user_departments = user_departments or {}
        self.members = members or {}

    def get_departments(self):
        return self.departments

    def get_user_department_id(self, user_id):
        return self.user_departments.get(int(user_id))

    def get_department_member_ids(self, department_id):
        return self.members.get(int(department_id), set())


class SzovWallboardBackendGuardTests(unittest.TestCase):
    """Доступ: глобальные админы, глава СЗоВ, СВ СЗоВ. Чужие отделы — 403."""

    def _guard(self, *, role, requester_id, headed_department_id=None, user_department_id=None,
               departments=None, global_admin=None):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        calls = {}

        def jsonify(payload):
            return payload

        ns = {
            'time': time,
            'jsonify': jsonify,
            'db': _FakeDb(
                departments=departments,
                user_departments={requester_id: user_department_id} if user_department_id else {},
            ),
            '_get_authenticated_requester': lambda: (requester_id, (requester_id, None, None, role), None),
            '_normalize_user_role': lambda value: str(value or '').strip().lower(),
            '_is_global_admin_requester': (
                global_admin if global_admin is not None
                else (lambda r, rid: r in ('admin', 'super_admin') and headed_department_id is None)
            ),
            '_is_supervisor_role': lambda r: str(r or '').lower() == 'sv',
            '_headed_department_id': lambda rid: headed_department_id,
        }
        _load_names(source, {
            'SZOV_WALLBOARD_DEPARTMENT_CODE',
            '_SZOV_WALLBOARD_DEPARTMENT_CACHE',
            '_SZOV_WALLBOARD_DEPARTMENT_CACHE_TTL',
            '_szov_wallboard_department_id',
            '_szov_wallboard_guard',
        }, ns)
        # Кэш отдела статический на модуль — сбрасываем, чтобы тесты не влияли друг на друга.
        ns['_SZOV_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        ns['_calls'] = calls
        return ns['_szov_wallboard_guard']()

    def test_global_admin_allowed(self):
        requester_id, err = self._guard(role='admin', requester_id=7)
        self.assertIsNone(err)
        self.assertEqual(requester_id, 7)

    def test_super_admin_allowed(self):
        _, err = self._guard(role='super_admin', requester_id=1)
        self.assertIsNone(err)

    def test_szov_department_head_allowed(self):
        _, err = self._guard(role='admin', requester_id=9, headed_department_id=1)
        self.assertIsNone(err)

    def test_head_of_another_department_forbidden(self):
        """Глава ОП с базовой admin-ролью не должен видеть табло СЗоВ (строгая граница отдела)."""
        _, err = self._guard(role='admin', requester_id=11, headed_department_id=367)
        self.assertIsNotNone(err)
        payload, status = err
        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "forbidden"})

    def test_szov_supervisor_allowed(self):
        _, err = self._guard(role='sv', requester_id=21, user_department_id=1)
        self.assertIsNone(err)

    def test_supervisor_of_another_department_forbidden(self):
        _, err = self._guard(role='sv', requester_id=22, user_department_id=367)
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)

    def test_operator_forbidden(self):
        _, err = self._guard(role='operator', requester_id=33, user_department_id=1)
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)

    def test_missing_szov_department_forbids_non_admins(self):
        """Если отдела с кодом szov нет — СВ/главе отказ, а не случайный доступ."""
        _, err = self._guard(role='sv', requester_id=21, user_department_id=1,
                             departments=[{'id': 367, 'code': 'op'}])
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)


class SzovWallboardSqlTests(unittest.TestCase):
    """SQL должен повторять формулы биллинга и корректно определять «сейчас»."""

    @classmethod
    def setUpClass(cls):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.ns = _load_names(source, {
            '_OKTELL_GREETING_ABANDON',
            '_OKTELL_FAILED_CALL',
            '_SZOV_WALLBOARD_QUEUE_LOOKBACK_HOURS',
            '_SZOV_WALLBOARD_TALK_LOOKBACK_HOURS',
            '_oktell_wallboard_totals_sql',
            '_oktell_wallboard_operator_states_sql',
        }, {})
        cls.totals_sql = cls.ns['_oktell_wallboard_totals_sql'](20)
        cls.states_sql = cls.ns['_oktell_wallboard_operator_states_sql']()

    def test_single_statement_only(self):
        """Прокси Oktell отклоняет несколько запросов в одной строке."""
        for sql in (self.totals_sql, self.states_sql):
            self.assertNotIn(';', sql)

    def test_no_write_keywords_beyond_whitelisted_column(self):
        """У прокси наивный блоклист по подстроке. Разрешена только колонка dt_insert."""
        for sql in (self.totals_sql, self.states_sql):
            lowered = sql.lower()
            self.assertNotIn('update', lowered)
            self.assertNotIn('delete', lowered)
            self.assertNotIn(' insert', lowered)
            # 'insert' встречается только как часть имени колонки dt_insert
            self.assertEqual(lowered.count('insert'), lowered.count('dt_insert'))

    def test_day_window_is_oktell_local_day(self):
        """Границу суток берём у самого Oktell, иначе табло и источник разойдутся."""
        self.assertIn("x.dt_insert >= CONVERT(date, GETDATE())", self.totals_sql)
        self.assertIn("x.dt_insert < DATEADD(day, 1, CONVERT(date, GETDATE()))", self.totals_sql)

    def test_totals_reuse_billing_formulas(self):
        grt = self.ns['_OKTELL_GREETING_ABANDON']
        fail = self.ns['_OKTELL_FAILED_CALL']
        self.assertIn("x.route = 'incoming'", self.totals_sql)
        self.assertIn("x.taxi_park <> ''", self.totals_sql)
        self.assertIn(f"x.result_call <> N'{fail}'", self.totals_sql)
        # arrived / served / lost — те же коды результата, что в _oktell_billing_sql
        self.assertIn(f"x.result_call <> N'{grt}' AND x.call_result IN (13,19,5) THEN 1", self.totals_sql)
        self.assertIn(f"x.result_call <> N'{grt}' AND x.call_result IN (5) THEN 1", self.totals_sql)
        self.assertIn(f"x.result_call <> N'{grt}' AND x.call_result IN (13,19) THEN 1", self.totals_sql)
        self.assertIn(f"x.result_call = N'{grt}' THEN 1", self.totals_sql)
        # SL по порогу секунд ожидания в очереди
        self.assertIn("x.LenQueue <= 20", self.totals_sql)

    def test_queue_counts_only_chains_that_never_reached_an_operator(self):
        """Открытый лег ct=4/IVR без ЛЮБОГО лега ct=5 — иначе поймаем пост-обработку."""
        self.assertIn("a.ConnectionType = 4 AND a.BLineNum = N'IVR' AND a.TimeStop IS NULL", self.totals_sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM oktell.dbo.A_Stat_Connections_1x1 d "
                      "WHERE d.IdChain = a.IdChain AND d.ConnectionType = 5)", self.totals_sql)
        self.assertIn("GROUP BY a.IdChain", self.totals_sql)
        hours = self.ns['_SZOV_WALLBOARD_QUEUE_LOOKBACK_HOURS']
        self.assertIn(f"DATEADD(hour, -{hours}, GETDATE())", self.totals_sql)

    def test_talking_counts_open_operator_legs(self):
        self.assertIn("COUNT(DISTINCT c.IdChain) AS talking_now", self.totals_sql)
        self.assertIn("c.ConnectionType = 5 AND c.TimeStop IS NULL", self.totals_sql)

    def test_states_take_latest_row_per_user_and_skip_offline(self):
        self.assertIn("ROW_NUMBER() OVER (PARTITION BY h.UserId ORDER BY h.Enumerator DESC)", self.states_sql)
        self.assertIn("x.rn = 1", self.states_sql)
        # 0 Выключен и 7 Без телефона — не на линии
        self.assertIn("x.State NOT IN (0, 7)", self.states_sql)
        self.assertIn("oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo", self.states_sql)


class SzovWallboardOperatorMappingTests(unittest.TestCase):
    """Раскладка статусов Oktell по ведрам и отделение «Перезвона» от «Перерыва»."""

    def _build(self, rows, *, matched_names=None, members=None):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        matched = matched_names or {}

        ns = {
            'time': time,
            'db': _FakeDb(members={1: members if members is not None else {10, 11, 12, 13, 14, 15}}),
            '_status_import_build_operator_lookup': lambda restrict_to_ids=None: {'lookup': True},
            '_status_import_resolve_operator_matches': (
                lambda name, lookup: ([matched[name]] if name in matched else [])
            ),
        }
        _load_names(source, {
            'SZOV_WALLBOARD_DEPARTMENT_CODE',
            '_SZOV_WALLBOARD_DEPARTMENT_CACHE',
            '_SZOV_WALLBOARD_DEPARTMENT_CACHE_TTL',
            '_SZOV_WALLBOARD_STATE_BUCKETS',
            '_SZOV_WALLBOARD_BREAK_REASONS',
            '_SZOV_WALLBOARD_RECALL_ICODE',
            '_szov_wallboard_department_id',
            '_szov_wallboard_int',
            '_szov_wallboard_operator_lookup',
            '_szov_wallboard_build_operators',
        }, ns)
        ns['_SZOV_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        return ns['_szov_wallboard_build_operators'](rows)

    def test_buckets_and_online_total(self):
        rows = [
            {'operator_name': 'Готовый', 'state': 1, 'icode': -1, 'in_state_seconds': 10},
            {'operator_name': 'Занятый', 'state': 5, 'icode': -1, 'in_state_seconds': 20},
            {'operator_name': 'Перерывный', 'state': 2, 'icode': 4, 'in_state_seconds': 30},
            {'operator_name': 'Перезвонный', 'state': 2, 'icode': 2, 'in_state_seconds': 40},
            {'operator_name': 'Резервный', 'state': 6, 'icode': -1, 'in_state_seconds': 50},
            {'operator_name': 'Отошедший', 'state': 3, 'icode': -1, 'in_state_seconds': 60},
        ]
        matched = {row['operator_name']: {'id': 10 + idx, 'name': row['operator_name']}
                   for idx, row in enumerate(rows)}
        result = self._build(rows, matched_names=matched)
        self.assertEqual(result['free'], 1)
        self.assertEqual(result['talking'], 1)
        self.assertEqual(result['on_break'], 1)
        self.assertEqual(result['on_recall'], 1)
        self.assertEqual(result['other'], 2)  # резерв + нет на месте
        # Онлайн — сумма всех ведер, чтобы плитки на экране сходились
        self.assertEqual(result['online'], 6)

    def test_recall_is_separated_from_break(self):
        """«Перезвон» — под-причина перерыва (ICode=2), но на табло это отдельный список."""
        rows = [
            {'operator_name': 'A', 'state': 2, 'icode': 2, 'in_state_seconds': 5},
            {'operator_name': 'B', 'state': 2, 'icode': 4, 'in_state_seconds': 7},
        ]
        matched = {'A': {'id': 10, 'name': 'Оператор А'}, 'B': {'id': 11, 'name': 'Оператор Б'}}
        result = self._build(rows, matched_names=matched)
        self.assertEqual([item['name'] for item in result['recall_list']], ['Оператор А'])
        self.assertEqual(result['recall_list'][0]['reason'], 'Перезвон')
        self.assertEqual([item['name'] for item in result['break_list']], ['Оператор Б'])
        self.assertEqual(result['break_list'][0]['reason'], 'Перерыв')

    def test_unmatched_technical_accounts_are_excluded(self):
        """admin/supervisor в Oktell — служебные учётки; они не должны завышать «свободных»."""
        rows = [
            {'operator_name': 'admin', 'state': 1, 'icode': -1, 'in_state_seconds': 9000},
            {'operator_name': 'supervisor', 'state': 1, 'icode': -1, 'in_state_seconds': 100},
            {'operator_name': 'Настоящий', 'state': 1, 'icode': -1, 'in_state_seconds': 10},
        ]
        result = self._build(rows, matched_names={'Настоящий': {'id': 10, 'name': 'Настоящий'}})
        self.assertEqual(result['free'], 1)
        self.assertEqual(result['online'], 1)
        self.assertEqual(result['unmatched_names'], ['admin', 'supervisor'])

    def test_break_lists_are_sorted_by_time_in_status_desc(self):
        rows = [
            {'operator_name': 'A', 'state': 2, 'icode': 4, 'in_state_seconds': 30},
            {'operator_name': 'B', 'state': 2, 'icode': 4, 'in_state_seconds': 300},
            {'operator_name': 'C', 'state': 2, 'icode': 4, 'in_state_seconds': 120},
        ]
        matched = {name: {'id': 10 + i, 'name': name} for i, name in enumerate(['A', 'B', 'C'])}
        result = self._build(rows, matched_names=matched)
        self.assertEqual([item['seconds'] for item in result['break_list']], [300, 120, 30])

    def test_no_szov_department_yields_empty_counts(self):
        rows = [{'operator_name': 'X', 'state': 1, 'icode': -1, 'in_state_seconds': 1}]
        result = self._build(rows, members=set())
        self.assertEqual(result['online'], 0)


class SzovWallboardSnapshotTests(unittest.TestCase):
    """Сборка показателей и общий TTL-кэш."""

    TOTALS_ROW = {
        'oktell_now': '2026-08-03 12:07:18',
        'queue_now': 3,
        'queue_max_wait_seconds': 42,
        'talking_now': 5,
        'arrived': 424,
        'served': 392,
        'lost': 32,
        'greet_drop': 18,
        'served_sl': 309,
        'wait_seconds': 10000.0,
        'max_wait_seconds': 376.063,
        'talk_seconds': 107708.0,
    }

    def _namespace(self, totals_row=None, state_rows=None, fail=False):
        source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        state = {'calls': 0}

        def fake_query(sql):
            state['calls'] += 1
            if fail:
                raise RuntimeError("Oktell proxy HTTP 500")
            if 'A_UserStateHistory' in sql:
                return list(state_rows or [])
            return [dict(totals_row if totals_row is not None else self.TOTALS_ROW)]

        ns = {
            'time': time,
            'datetime': datetime,
            'logging': logging,
            'threading': threading,
            # Константы модуля читаются через _env_int; в тесте берём значения по умолчанию.
            '_env_int': lambda name, default, minimum=None, maximum=None: default,
            'OKTELL_BILLING_SL_DEFAULT_SECONDS': 20,
            '_oktell_query': fake_query,
            'db': _FakeDb(members={1: {10, 11}}),
            '_status_import_build_operator_lookup': lambda restrict_to_ids=None: {'lookup': True},
            '_status_import_resolve_operator_matches': lambda name, lookup: [],
        }
        _load_names(source, {
            'SZOV_WALLBOARD_DEPARTMENT_CODE',
            'SZOV_WALLBOARD_CACHE_TTL_SECONDS',
            'SZOV_WALLBOARD_STALE_MAX_SECONDS',
            '_SZOV_WALLBOARD_DEPARTMENT_CACHE',
            '_SZOV_WALLBOARD_DEPARTMENT_CACHE_TTL',
            '_SZOV_WALLBOARD_QUEUE_LOOKBACK_HOURS',
            '_SZOV_WALLBOARD_TALK_LOOKBACK_HOURS',
            '_SZOV_WALLBOARD_STATE_BUCKETS',
            '_SZOV_WALLBOARD_BREAK_REASONS',
            '_SZOV_WALLBOARD_RECALL_ICODE',
            '_OKTELL_GREETING_ABANDON',
            '_OKTELL_FAILED_CALL',
            '_szov_wallboard_cache',
            '_szov_wallboard_lock',
            '_szov_wallboard_int',
            '_szov_wallboard_ratio',
            '_szov_wallboard_department_id',
            '_szov_wallboard_operator_lookup',
            '_szov_wallboard_build_operators',
            '_oktell_wallboard_totals_sql',
            '_oktell_wallboard_operator_states_sql',
            '_szov_wallboard_fetch_snapshot',
            '_szov_wallboard_snapshot',
        }, ns)
        ns['_SZOV_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        ns['_szov_wallboard_cache'].update(ts=0.0, payload=None)
        ns['_query_state'] = state
        return ns

    def test_today_metrics_match_billing_definitions(self):
        ns = self._namespace()
        snap = ns['_szov_wallboard_fetch_snapshot']()
        today = snap['today']
        # Тождество источника: дошедшие до очереди = принятые + потерянные
        self.assertEqual(today['arrived'], today['served'] + today['lost'])
        # Всего входящих включает сброшенных на приветствии
        self.assertEqual(today['total'], 424 + 18)
        self.assertAlmostEqual(today['ar_ratio'], 32 / 424)
        self.assertAlmostEqual(today['sl_ratio'], 309 / 424)
        self.assertAlmostEqual(today['avg_wait_seconds'], 10000.0 / 424)
        self.assertEqual(today['max_wait_seconds'], 376)
        self.assertEqual(snap['sl_threshold_seconds'], 20)

    def test_now_block_carries_queue_and_operator_counts(self):
        ns = self._namespace()
        snap = ns['_szov_wallboard_fetch_snapshot']()
        self.assertEqual(snap['now']['queue'], 3)
        self.assertEqual(snap['now']['queue_max_wait_seconds'], 42)
        self.assertEqual(snap['now']['talking_calls'], 5)
        self.assertEqual(snap['oktell_now'], '2026-08-03 12:07:18')

    def test_empty_day_does_not_divide_by_zero(self):
        empty = {key: 0 for key in self.TOTALS_ROW}
        empty['oktell_now'] = '2026-08-03 00:01:00'
        empty['max_wait_seconds'] = None
        ns = self._namespace(totals_row=empty)
        today = ns['_szov_wallboard_fetch_snapshot']()['today']
        self.assertIsNone(today['ar_ratio'])
        self.assertIsNone(today['sl_ratio'])
        self.assertIsNone(today['avg_wait_seconds'])
        self.assertIsNone(today['max_wait_seconds'])
        self.assertIsNone(today['avg_talk_seconds'])
        self.assertEqual(today['total'], 0)

    def test_two_oktell_queries_per_snapshot(self):
        """Прокси Oktell низкоконкурентный: снимок должен стоить ровно два запроса."""
        ns = self._namespace()
        ns['_szov_wallboard_fetch_snapshot']()
        self.assertEqual(ns['_query_state']['calls'], 2)

    def test_cache_coalesces_concurrent_viewers(self):
        ns = self._namespace()
        first = ns['_szov_wallboard_snapshot']()
        self.assertFalse(first['stale'])
        calls_after_first = ns['_query_state']['calls']
        for _ in range(5):
            again = ns['_szov_wallboard_snapshot']()
            self.assertFalse(again['stale'])
        # Пять «зрителей» внутри TTL не должны добавить ни одного запроса к Oktell
        self.assertEqual(ns['_query_state']['calls'], calls_after_first)

    def test_stale_snapshot_served_when_oktell_fails(self):
        """Табло на стене не должно гаснуть из-за одной ошибки Oktell."""
        ns = self._namespace()
        good = ns['_szov_wallboard_snapshot']()
        self.assertFalse(good['stale'])

        broken_calls = {'n': 0}

        def broken(sql):
            broken_calls['n'] += 1
            raise RuntimeError("Oktell proxy HTTP 500")

        ns['_oktell_query'] = broken
        ns['_szov_wallboard_cache']['ts'] = time.time() - 60  # кэш просрочен, но в пределах stale-окна
        stale = ns['_szov_wallboard_snapshot']()
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['today']['served'], 392)
        self.assertIn('500', stale['error'])
        self.assertGreaterEqual(stale['age_seconds'], 60)

    def test_error_propagates_when_no_snapshot_ever_succeeded(self):
        ns = self._namespace(fail=True)
        with self.assertRaises(RuntimeError):
            ns['_szov_wallboard_snapshot']()

    def test_stale_window_expiry_raises_instead_of_showing_ancient_data(self):
        ns = self._namespace()
        ns['_szov_wallboard_snapshot']()

        def broken(sql):
            raise RuntimeError("Oktell proxy HTTP 500")

        ns['_oktell_query'] = broken
        ns['_szov_wallboard_cache']['ts'] = time.time() - (ns['SZOV_WALLBOARD_STALE_MAX_SECONDS'] + 5)
        with self.assertRaises(RuntimeError):
            ns['_szov_wallboard_snapshot']()


class SzovWallboardWiringTests(unittest.TestCase):
    """Раздел должен быть подключён во всех точках App.jsx, иначе он не откроется."""

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.api = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.view = (
            ROOT / "src" / "components" / "monitoring" / "SzovWallboardView.jsx"
        ).read_text(encoding="utf-8-sig")
        cls.faicon = (
            ROOT / "src" / "components" / "common" / "FaIcon.jsx"
        ).read_text(encoding="utf-8-sig")

    def test_backend_endpoint_is_registered_and_guarded(self):
        self.assertIn("@app.route('/api/szov_wallboard/snapshot', methods=['GET', 'OPTIONS'])", self.api)
        self.assertIn("def api_szov_wallboard_snapshot():", self.api)
        self.assertIn("requester_id, err = _szov_wallboard_guard()", self.api)
        # OPTIONS обязателен: фронт живёт на другом origin
        self.assertIn("return _build_cors_preflight_response()", self.api)

    def test_frontend_gate_matches_department_by_code(self):
        self.assertIn("const SZOV_WALLBOARD_DEPARTMENT_CODE = 'szov';", self.app)
        self.assertIn("const canAccessSzovWallboardForUser = (userLike) => {", self.app)
        # Тот же намеренный вырез, что у ChatApp: глава чужого отдела не проходит как админ
        self.assertIn("if (role === 'admin' && !isDepartmentHead(userLike)) return true;", self.app)
        self.assertIn("isSzovWallboardDepartmentHead(userLike)", self.app)

    def test_all_five_wiring_points_present(self):
        self.assertIn(
            "const SzovWallboardView = lazyWithRetry(() => import('./components/monitoring/SzovWallboardView'));",
            self.app,
        )
        self.assertIn("const canAccessSzovWallboardSection = canAccessSzovWallboardForUser(user);", self.app)
        self.assertIn("handleSidebarViewNavigation(e, 'szov_wallboard')", self.app)
        self.assertIn('view === "szov_wallboard" && canAccessSzovWallboardSection', self.app)
        self.assertIn("szov_wallboard: 'SZoV wallboard',", self.app)

    def test_sidebar_item_present_for_both_admin_and_manager_branches(self):
        """Пункт виден и админам, и главе/СВ — значит <li> должен встречаться дважды."""
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'szov_wallboard')"), 2)
        self.assertEqual(self.app.count('<span className="sidebar-text">Табло СЗоВ</span>'), 2)

    def test_redirect_guards_do_not_bounce_the_view(self):
        self.assertIn("(requestedViewFromUrl !== 'szov_wallboard' || canAccessSzovWallboardSection)", self.app)
        self.assertIn("if (view === 'szov_wallboard' && !canAccessSzovWallboardSection) {", self.app)
        # allowlist отдела не должен уводить с раздела, у него собственный предикат
        self.assertIn("if (view === 'szov_wallboard' && canAccessSzovWallboardSection) return;", self.app)

    def test_icon_token_is_mapped(self):
        """tests/test_faicon_mappings.py падает, если fa-токен не замаплен."""
        for token in ("fa-tachometer-alt", "fa-mug-hot", "fa-phone-volume", "fa-rotate", "fa-expand"):
            self.assertIn(f"'{token}'", self.faicon, token)

    def test_view_polls_without_reloading_and_guards_overlap(self):
        self.assertIn("const POLL_INTERVAL_MS = 10000;", self.view)
        self.assertIn("if (inFlightRef.current) return;", self.view)
        self.assertIn("new AbortController()", self.view)
        # Скрытую вкладку не опрашиваем
        self.assertIn("visibilitychange", self.view)

    def test_view_renders_every_requested_metric(self):
        for label in (
            "Звонков в очереди",
            "Максимальное ожидание",
            "AR на текущий момент",
            "Всего входящих",
            "Принято",
            "Потеряно",
            "Среднее ожидание",
            "Максимальное за день",
            "Онлайн",
            "В разговоре",
            "Свободны",
            "На перерыве",
            "На перезвоне",
        ):
            self.assertIn(label, self.view, label)

    def test_numbers_use_tabular_nums(self):
        """Требование владельца: цифры не должны «прыгать» при обновлении."""
        self.assertIn("tabular-nums", self.view)

    def test_fullscreen_reuses_the_same_markup(self):
        """Полный экран не должен дублировать разметку — один WallboardBody с масштабом."""
        self.assertEqual(self.view.count("<WallboardBody"), 2)
        self.assertIn("scale={1.6}", self.view)


if __name__ == "__main__":
    unittest.main()
