# -*- coding: utf-8 -*-
"""ID водителя у базы «Удаленного КЦ» (владелец, 29.10.2026: «в детализацию и в отчеты добавить id и иин
водителя») — правила сверки со списком Sapar, без базы и сети (dial_list/driver_ids.py).

Что защищаем:
  * значение колонки — ID без повторов, по алфавиту, через запятую: иначе та же строка переписывалась бы
    при каждой сверке (порядок у Sapar не обещан);
  * «спросили — Sapar не знает» ('') отличается от «не спрашивали» (NULL), а прежний ID не стирается,
    если водитель пропал из списка;
  * в Sapar ходят вне транзакции: восьмимегабайтный список не держит строки водителей замком.

Настоящий SQL на настоящей схеме — tests/test_dial_list_driver_ids_postgres.py.
"""
import contextlib
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import driver_ids  # noqa: E402

A, B, C = 'a' * 32, 'b' * 32, 'c' * 32


class IdsTextTests(unittest.TestCase):
    def test_sorted_unique_comma_separated(self):
        self.assertEqual(driver_ids.ids_text([B, A, B, ' ', None]), f'{A}, {B}')
        self.assertEqual(driver_ids.ids_text([f' {C} ']), C)
        self.assertEqual(driver_ids.ids_text([]), '')
        self.assertEqual(driver_ids.ids_text(None), '')


class ChangesTests(unittest.TestCase):
    def test_what_gets_written(self):
        leads = [
            ('l1', '111', None),           # не спрашивали, Sapar знает → ID
            ('l2', '222', None),           # не спрашивали, Sapar не знает → '' (спросили)
            ('l3', '333', 'old'),          # был ID, из списка пропал → не трогаем: это всё ещё его ID
            ('l4', '444', f'{A}, {B}'),    # то же значение в другом порядке → писать нечего
            ('l5', '555', ''),             # Sapar раньше не знал, теперь знает → ID
            ('l6', '666', ''),             # не знал и не знает → писать нечего
            ('l7', '777', A),              # аккаунтов стало два → оба
        ]
        known = {'111': [C], '444': [B, A], '555': [A], '777': [B, A]}
        self.assertEqual(driver_ids.changes(leads, known), [
            ('l1', '111', C), ('l2', '222', ''), ('l5', '555', A), ('l7', '777', f'{A}, {B}')])


class _Cursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, sql, params=None):
        self.db.statements.append(' '.join(sql.split()))
        self.rows = [(True,)] if 'SELECT EXISTS' in sql else list(self.db.leads)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class _Db:
    """Считает открытые курсоры (= транзакции): запрос в Sapar при открытом — ошибка."""

    def __init__(self, leads):
        self.leads = leads
        self.open = 0
        self.statements = []

    @contextlib.contextmanager
    def _get_cursor(self):
        self.open += 1
        try:
            yield _Cursor(self)
        finally:
            self.open -= 1


class SyncWithoutDbTests(unittest.TestCase):
    def test_sapar_is_asked_outside_any_transaction(self):
        db = _Db([('l1', '111', None)])
        seen = []

        def fetch(month, year, wanted=None):
            seen.append((month, year, wanted, db.open))
            return {'ok': False, 'error': 'Sapar лежит'}

        summary = driver_ids.sync(db, fetch, date(2026, 10, 29))
        # База октября — по документам сентября (как проверка подписания); список Sapar от месяца не
        # зависит, но запрос — тот же, что у всего раздела.
        self.assertEqual(seen, [(9, 2026, {'111'}, 0)])
        self.assertEqual((summary['ok'], summary['error'], summary['updated']), (False, 'Sapar лежит', 0))
        # Отказ — ничего не пишем: неспрошенные остаются неспрошенными и попадут в следующую сверку.
        self.assertFalse(any(s.startswith('UPDATE') for s in db.statements))

    def test_window_is_current_and_previous_month(self):
        db = _Db([])
        driver_ids.sync(db, lambda *a, **k: self.fail('спрашивать некого'), date(2026, 10, 29))
        due = db.statements[0]
        self.assertIn('period BETWEEN %(from_period)s AND %(to_period)s', due)
        self.assertIn("iin <> '' AND yandex_driver_ids IS NULL", due)


if __name__ == '__main__':
    unittest.main()
