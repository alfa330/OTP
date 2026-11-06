# -*- coding: utf-8 -*-
"""Табель фронт-офисов: состав месяца и сшивка карточек Workpace (front_office_timesheet/data.py).

Правило сшивки — то же, что у «Отметок» (_glb_name_keys / _glb_resolve_entry
/ _glb_index_put из Database): методы вырезаются из исходника, как в
tests/test_group_late_icore_plan.py, — `import database` поднял бы подключение.
Люди выдуманы.
"""

import ast
import copy
import re
import unittest
from datetime import date, datetime, time
from unittest import mock

from front_office_timesheet import data, rules
from tests import source_cache

MEMBERS = ("_glb_name_keys", "_glb_index_put", "_glb_one_edit_apart", "_glb_resolve_entry", "_GLB_NAME_FOLD")


def _db_class():
    tree = source_cache.parse(source_cache.database_flat_path().read_text(encoding="utf-8-sig"))
    cls_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Database")
    namespace = {"re": re, "datetime": datetime}
    attrs = {}
    for node in cls_node.body:
        name = getattr(node, "name", None) or (
            getattr(node.targets[0], "id", None) if isinstance(node, ast.Assign) else None)
        if name not in MEMBERS:
            continue
        module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, "database", "exec"), namespace)
        attrs[name] = namespace[name]
    missing = set(MEMBERS) - set(attrs)
    assert not missing, f"в Database нет: {sorted(missing)}"
    return type("StubDatabase", (), attrs)


StubDatabase = _db_class()
MATCH = dict(name_keys=StubDatabase._glb_name_keys, resolve=StubDatabase._glb_resolve_entry,
             index_put=StubDatabase._glb_index_put)


def person(user_id, name, active=True, **extra):
    base = {'user_id': user_id, 'name': name, 'city': 'Алматы', 'job_title': '', 'active': active,
            'in_department': True, 'fired': not active, 'hire_date': None,
            'member_in_month': True, 'member_ever': True}
    base.update(extra)
    return base


class MatchCardsTests(unittest.TestCase):
    def test_icore_name_on_a_plan_row_wins_over_a_different_workpace_spelling(self):
        people = [person(1, 'Сарсенова Айгерим')]
        rows = [{'employee_id': 'card-1', 'employee': 'Сарсенова Айгерим', 'schedule': 'График iCore'}]
        cards = [{'ext_id': 'card-1', 'full_name': 'САРСЕН АЙГЕРИМ ЕРЛАНКЫЗЫ'}]
        self.assertEqual(data.match_cards(people, cards, rows, {}, **MATCH), {'card-1': 1})

    def test_workpace_name_with_patronymic_matches_by_surname_and_name(self):
        people = [person(1, 'Досанбаев Асан')]
        cards = [{'ext_id': 'card-9', 'full_name': 'Досанбаев Асан Тестович'}]
        self.assertEqual(data.match_cards(people, cards, [], {}, **MATCH), {'card-9': 1})

    def test_two_cards_of_one_person_both_map_to_him(self):
        people = [person(1, 'Магзумов Ербол')]
        cards = [{'ext_id': 'old', 'full_name': 'МАҒЗУМОВ ЕРБОЛ НҰРЛАНҰЛЫ'},
                 {'ext_id': 'new', 'full_name': 'Магзумов Ербол'}]
        self.assertEqual(data.match_cards(people, cards, [], {}, **MATCH), {'old': 1, 'new': 1})

    def test_manual_link_beats_the_name_and_none_excludes(self):
        people = [person(1, 'Иванова Ирина'), person(2, 'Петрова Полина')]
        cards = [{'ext_id': 'a', 'full_name': 'Иванова Ирина'}, {'ext_id': 'b', 'full_name': 'Петрова Полина'}]
        links = {'a': 2, 'b': None}
        self.assertEqual(data.match_cards(people, cards, [], links, **MATCH), {'a': 2})

    def test_fired_namesake_does_not_steal_the_card_of_an_active_one(self):
        people = [person(5, 'Ким Анна', active=False), person(6, 'Ким Анна')]
        cards = [{'ext_id': 'k', 'full_name': 'Ким Анна'}]
        self.assertEqual(data.match_cards(people, cards, [], {}, **MATCH), {'k': 6})

    def test_unknown_card_stays_unmatched(self):
        people = [person(1, 'Иванова Ирина')]
        cards = [{'ext_id': 'x', 'full_name': 'Совсем Другой Человек'}]
        self.assertEqual(data.match_cards(people, cards, [], {}, **MATCH), {})


class IncludeTests(unittest.TestCase):
    END = date(2026, 10, 31)

    def test_rules(self):
        include = data._include
        self.assertTrue(include(person(1, 'А', member_in_month=True), None, self.END, False))
        self.assertFalse(include(person(1, 'А', member_in_month=False), None, self.END, False))
        self.assertTrue(include(person(1, 'А', member_in_month=False), None, self.END, True))
        no_group = dict(member_in_month=False, member_ever=False)
        self.assertTrue(include(person(1, 'А', **no_group), None, self.END, False))
        self.assertFalse(include(person(1, 'А', hire_date=date(2026, 11, 3), **no_group), None, self.END, False))
        self.assertFalse(include(person(1, 'А', active=False, **no_group), None, self.END, False))
        self.assertTrue(include(person(1, 'А', active=False, **no_group), None, self.END, True))

    def test_fired_long_ago_with_an_open_membership_stays_out(self):
        """Членство при увольнении не закрывают: ушедший летом «состоит» в группе
        и в октябре. Без дел в месяце его строки в табеле нет (прод, 06.11.2026)."""
        include = data._include
        self.assertFalse(include(person(9, 'Б', active=False, member_in_month=True), None, self.END, False))
        self.assertTrue(include(person(9, 'Б', active=False, member_in_month=True), None, self.END, True))


class FakeCursor:
    """Отвечает по ключевым словам запроса; запоминает параметры."""

    def __init__(self, tables):
        self.tables = tables
        self.calls = []
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        for marker, rows in self.tables:
            if marker in flat:
                self._rows = list(rows)
                return
        self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDb(StubDatabase):
    def __init__(self, cursor):
        self.cursor = cursor

    def _get_cursor(self):
        cursor = self.cursor

        class _Ctx:
            def __enter__(self_inner):
                return cursor

            def __exit__(self_inner, *exc):
                return False
        return _Ctx()


class CollectTests(unittest.TestCase):
    def test_month_is_assembled_from_schedule_and_attendance(self):
        october_7 = date(2026, 10, 7)
        tables = [
            ("FROM departments", [(909, 'Фронт офисы')]),
            ("WITH dept_members", [
                (1, 'Альфа Анна', 'Астана', '', 'working', 909, None, True, True),
                (2, 'Бета Болат', '', 'техперсонал', 'working', 909, None, True, True),
                (3, 'Гамма Гуля', 'Алматы', '', 'fired', 909, None, False, True),
            ]),
            ("GROUP BY m.operator_id", [(1, date(2026, 8, 1), None), (2, date(2026, 8, 1), None)]),
            ("FROM work_shifts", [(1, october_7, time(9), time(19)), (2, october_7, time(9), time(19))]),
            ("FROM days_off", [(1, date(2026, 10, 10))]),
            ("FROM operator_schedule_status_periods", [(2, 'bs', date(2026, 10, 12), date(2026, 10, 14))]),
            ("FROM glb_employees", [('card-a', 'АЛЬФА АННА СЕРИКОВНА')]),
            ("FROM glb_employee_links", []),
        ]
        cursor = FakeCursor(tables)
        rows = [{
            'date': '2026-10-07', 'employee_id': 'card-a', 'employee': 'Альфа Анна', 'schedule': 'График iCore',
            'fact_in': '2026-10-07T09:12:00+05:00', 'fact_out': '2026-10-07T19:03:00+05:00',
            'marks': [{'at': '2026-10-07T09:12:00+05:00', 'kind': 'in'},
                      {'at': '2026-10-07T19:03:00+05:00', 'kind': 'out'}],
        }]
        with mock.patch.object(data.attendance_cache, 'rows_for',
                               return_value={'rows': rows, 'pending_days': []}) as rows_for, \
                mock.patch.object(data, 'kz_holidays', return_value=set()):
            inputs = data.collect(FakeDb(cursor), '2026-10', date(2026, 11, 6))
        rows_for.assert_called_once()
        self.assertEqual(rows_for.call_args.args[1:], (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(rows_for.call_args.kwargs, {'department': 'Регионы'})

        self.assertEqual([p['user_id'] for p in inputs['people']], [1, 2])   # уволенный без дел в месяце — нет
        self.assertEqual(inputs['has_card'], {1})
        self.assertEqual(inputs['without_card'], ['Бета Болат'])
        self.assertEqual(inputs['facts'][(1, october_7)]['fact_in'].hour, 9)

        model = rules.build_model(inputs)
        by_name = {p['name']: p for city in model['cities'] for p in city['people']}
        anna = by_name['Альфа Анна']['cells']
        self.assertEqual((anna[6]['code'], anna[6]['late']), ('р', 12))
        self.assertEqual(anna[9]['code'], 'в')
        bolat = by_name['Бета Болат']['cells']
        self.assertEqual(bolat[6]['code'], '')                  # смена есть, карточки нет — не «н»
        self.assertEqual([c['code'] for c in bolat[11:14]], ['Б/С', 'Б/С', 'Б/С'])
        self.assertEqual([city['city'] for city in model['cities']], ['Астана', 'Город не указан'])

    def test_options_do_not_read_attendance(self):
        cursor = FakeCursor([("FROM departments", [(909, 'Фронт офисы')])])
        with mock.patch.object(data.attendance_cache, 'rows_for') as rows_for:
            inputs = data.collect(FakeDb(cursor), '2026-10', date(2026, 11, 6), with_facts=False)
        rows_for.assert_not_called()
        self.assertEqual(data.options_payload(inputs)['people'], [])

    def test_bad_month_and_missing_department(self):
        with self.assertRaises(data.TimesheetError) as bad:
            data.collect(FakeDb(FakeCursor([])), '2026-13', date(2026, 11, 6))
        self.assertEqual(bad.exception.status, 400)
        with self.assertRaises(data.TimesheetError) as missing:
            data.collect(FakeDb(FakeCursor([])), '2026-10', date(2026, 11, 6))
        self.assertEqual(missing.exception.status, 404)

    def test_workpace_department_comes_from_the_plan_config(self):
        self.assertEqual(data.workpace_department_name(), 'Регионы')


if __name__ == '__main__':
    unittest.main()
