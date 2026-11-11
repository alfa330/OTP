# -*- coding: utf-8 -*-
"""Запрос снимка «Табло СЗоВ · Линия» (cdr/oktell_wallboard.py) — общий для портала и коннектора.

Что закреплено:
  * коннектор на VM шлюза строит запрос из числа и списка цифр, портал — из реестра тестовых
    номеров; при одних параметрах тексты обязаны совпасть буква в букву: табло не может
    показывать разные цифры в зависимости от того, какой дорогой пришли строки;
  * в запрос коннектора не попадает ничего, кроме числа в границах и ключей из десяти цифр, —
    что бы ни прислал портал;
  * условие «не тестовый номер» имеет ту же форму, что у реестра (test_numbers.keys);
  * константы общего модуля совпадают с константами монолита (их читает ещё биллинг).
"""
import ast
import unittest
from contextlib import contextmanager

from cdr import oktell_wallboard
from test_numbers import keys
from tests import source_cache

NAMES = {
    '_OKTELL_GREETING_ABANDON',
    '_OKTELL_FAILED_CALL',
    '_SZOV_WALLBOARD_QUEUE_LOOKBACK_HOURS',
    '_SZOV_WALLBOARD_TALK_LOOKBACK_HOURS',
    '_oktell_wallboard_totals_sql',
    '_oktell_wallboard_operator_states_sql',
    '_oktell_wallboard_snapshot_sql',
}
TEST_A = '7000000101'
TEST_B = '7012223344'


def _monolith_namespace():
    """Построители запроса портала, вырезанные из монолита (как в tests/test_szov_wallboard.py)."""
    tree = source_cache.parse(source_cache.monolith_flat_path().read_text(encoding='utf-8-sig'))
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            body.append(node)
        elif isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & NAMES:
                body.append(node)
    namespace = {}
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, '<oktell-wallboard-sql>', 'exec'), namespace)
    missing = sorted(NAMES - set(namespace))
    assert not missing, 'не найдено в монолите: %s' % missing
    return namespace


class _Registry:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return [(key,) for key in self.rows]


def _configure_registry(registry):
    if not registry:
        keys.configure(None)
        return

    @contextmanager
    def get_cursor():
        yield _Registry(sorted(registry))

    keys.configure(get_cursor)


class PortalAndConnectorBuildTheSameQuery(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ns = _monolith_namespace()

    def setUp(self):
        keys.configure(None)
        self.addCleanup(keys.configure, None)

    def test_same_text_without_test_numbers(self):
        for sl_seconds in (20, 30):
            with self.subTest(sl_seconds=sl_seconds):
                self.assertEqual(oktell_wallboard.snapshot_sql(sl_seconds, ()),
                                 self.ns['_oktell_wallboard_snapshot_sql'](sl_seconds))

    def test_same_text_with_test_numbers(self):
        """Реестр не пуст: коннектор получает ключи списком и обязан собрать тот же запрос."""
        _configure_registry({TEST_A, TEST_B})
        portal = self.ns['_oktell_wallboard_snapshot_sql'](20)
        self.assertEqual(portal.count("NOT IN ('%s', '%s')" % (TEST_A, TEST_B)), 3,
                         'три места с номером клиента — три условия')
        self.assertEqual(oktell_wallboard.snapshot_sql(20, [TEST_B, TEST_A]), portal)

    def test_portal_fragments_come_from_the_shared_module(self):
        """Фрагменты портала — те же функции общего модуля, а не вторая копия текста."""
        self.assertEqual(self.ns['_oktell_wallboard_operator_states_sql'](),
                         oktell_wallboard.operator_states_sql())
        self.assertEqual(self.ns['_oktell_wallboard_totals_sql'](20), oktell_wallboard.totals_sql(20))

    def test_constants_match_the_monolith(self):
        """Строки результата звонка читает ещё биллинг; окна легов — тесты табло. Разойдутся —
        запрос коннектора перестанет совпадать с запросом портала."""
        self.assertEqual(oktell_wallboard.GREETING_ABANDON, self.ns['_OKTELL_GREETING_ABANDON'])
        self.assertEqual(oktell_wallboard.FAILED_CALL, self.ns['_OKTELL_FAILED_CALL'])
        self.assertEqual(oktell_wallboard.QUEUE_LOOKBACK_HOURS,
                         self.ns['_SZOV_WALLBOARD_QUEUE_LOOKBACK_HOURS'])
        self.assertEqual(oktell_wallboard.TALK_LOOKBACK_HOURS,
                         self.ns['_SZOV_WALLBOARD_TALK_LOOKBACK_HOURS'])


class ConnectorQueryShape(unittest.TestCase):

    def setUp(self):
        self.sql = oktell_wallboard.snapshot_sql(20, [TEST_A])

    def test_single_read_only_statement(self):
        """Один SELECT без записи: учётка и так только читает, но запрос не должен и пытаться."""
        self.assertNotIn(';', self.sql)
        self.assertTrue(self.sql.startswith('SELECT '))
        lowered = self.sql.lower()
        for word in ('update', 'delete', 'merge', 'exec', 'drop ', 'alter ', 'create ', ' into '):
            self.assertNotIn(word, lowered)
        # 'insert' встречается только в имени колонки dt_insert
        self.assertEqual(lowered.count('insert'), lowered.count('dt_insert'))

    def test_every_snapshot_column_is_selected(self):
        """Портал принимает от коннектора только эти колонки — запрос обязан отдавать их все."""
        for column in oktell_wallboard.TOTALS_COLUMNS:
            self.assertIn('t.%s' % column, self.sql)
        for column in oktell_wallboard.STATE_COLUMNS:
            self.assertIn('s.%s' % column, self.sql)
        self.assertEqual(oktell_wallboard.SNAPSHOT_COLUMNS,
                         oktell_wallboard.TOTALS_COLUMNS + oktell_wallboard.STATE_COLUMNS)

    def test_test_numbers_are_excluded_in_all_three_places(self):
        for expr in (oktell_wallboard.WAITING_PHONE, oktell_wallboard.TALKING_PHONE,
                     oktell_wallboard.TOTALS_PHONE):
            self.assertIn("%s NOT IN ('%s')" % (oktell_wallboard.phone_key(expr), TEST_A), self.sql)

    def test_sl_threshold_is_the_requested_one(self):
        self.assertIn('x.LenQueue <= 20 ', self.sql)
        self.assertIn('x.LenQueue <= 45 ', oktell_wallboard.snapshot_sql(45))


class ParamsAreNumbersAndDigitsOnly(unittest.TestCase):
    """Коннектор не верит порталу безусловно: в текст запроса попадают число и цифры."""

    def test_injection_through_test_keys_is_dropped(self):
        hostile = [TEST_A, "1' OR 1=1 --", "7000000101'; DROP TABLE x --", '12345', 7000000101,
                   None, '70000001011', '７０００００００１０', ['7000000101'], b'7000000101']
        self.assertEqual(oktell_wallboard.clean_test_keys(hostile), (TEST_A,))
        sql = oktell_wallboard.snapshot_sql(20, hostile)
        self.assertEqual(sql, oktell_wallboard.snapshot_sql(20, [TEST_A]))
        self.assertNotIn('OR 1=1', sql)
        self.assertNotIn('DROP', sql)

    def test_test_keys_container_shapes(self):
        self.assertEqual(oktell_wallboard.clean_test_keys(None), ())
        self.assertEqual(oktell_wallboard.clean_test_keys('7000000101'), (),
                         'строка — не список ключей: по буквам её не разбираем')
        self.assertEqual(oktell_wallboard.clean_test_keys(42), ())
        self.assertEqual(oktell_wallboard.clean_test_keys({TEST_B, TEST_A, TEST_A}), (TEST_A, TEST_B))

    def test_test_keys_are_capped(self):
        many = ['%010d' % number for number in range(oktell_wallboard.MAX_TEST_KEYS + 50)]
        self.assertEqual(len(oktell_wallboard.clean_test_keys(many)), oktell_wallboard.MAX_TEST_KEYS)

    def test_sl_seconds_is_an_integer_within_bounds(self):
        clean = oktell_wallboard.clean_sl_seconds
        self.assertEqual(clean(20), 20)
        self.assertEqual(clean('30'), 30)
        self.assertEqual(clean(1), 1)
        self.assertEqual(clean(600), 600)
        for bad in (None, '', 'abc', 0, -5, 601, 10 ** 9, True, '20; DROP TABLE x', [20], {}):
            with self.subTest(bad=bad):
                self.assertEqual(clean(bad), oktell_wallboard.DEFAULT_SL_SECONDS)

    def test_hostile_sl_never_reaches_the_text(self):
        sql = oktell_wallboard.snapshot_sql('20 OR 1=1', ())
        self.assertEqual(sql, oktell_wallboard.snapshot_sql(oktell_wallboard.DEFAULT_SL_SECONDS, ()))

    def test_clean_params_is_canonical(self):
        self.assertEqual(oktell_wallboard.clean_params(None), {'sl_seconds': 20, 'test_keys': []})
        self.assertEqual(oktell_wallboard.clean_params('x'), {'sl_seconds': 20, 'test_keys': []})
        params = oktell_wallboard.clean_params({'sl_seconds': '25', 'test_keys': [TEST_B, 'x', TEST_A]})
        self.assertEqual(params, {'sl_seconds': 25, 'test_keys': [TEST_A, TEST_B]})

    def test_same_params_ignores_order_and_junk(self):
        left = {'sl_seconds': 20, 'test_keys': [TEST_A, TEST_B]}
        self.assertTrue(oktell_wallboard.same_params(left, {'sl_seconds': '20', 'test_keys': [TEST_B, TEST_A, 'x']}))
        self.assertFalse(oktell_wallboard.same_params(left, {'sl_seconds': 20, 'test_keys': [TEST_A]}))
        self.assertFalse(oktell_wallboard.same_params(left, {'sl_seconds': 30, 'test_keys': [TEST_A, TEST_B]}))
        self.assertFalse(oktell_wallboard.same_params(left, None))


class NotTestConditionMatchesTheRegistry(unittest.TestCase):
    """Пакета test_numbers в образе шлюза нет, поэтому условие повторено — и сверено здесь."""

    def test_key_expression(self):
        for expr in ('a.AOutNumber', 'x.[number]'):
            self.assertEqual(oktell_wallboard.phone_key(expr), keys.tsql_key(expr))
        self.assertEqual(oktell_wallboard.KEY_LENGTH, keys.KEY_LENGTH)

    def test_condition(self):
        for registry in (set(), {TEST_A}, {TEST_B, TEST_A}, {TEST_A, "x' OR 1=1 --", '123', None}):
            with self.subTest(registry=registry):
                condition = keys.tsql_not_test('x.[number]', registry)
                expected = 'AND %s ' % condition if condition else ''
                self.assertEqual(oktell_wallboard.and_not_test('x.[number]', registry), expected)


if __name__ == '__main__':
    unittest.main()
