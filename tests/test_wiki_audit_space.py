# -*- coding: utf-8 -*-
"""Журнал вики принадлежит пространству.

У «Таксопарков» и «Теза» журнал был общий: обе вкладки «Журнал» показывали одни
и те же записи, то есть кто в чужой вике что правил. Здесь проверяется, что
граница стоит во всех трёх местах сразу — в выборке, в счётчике и в чипах
групп, — и что формула пространства знает все типы объектов, которые пишут
роуты.

Тесты намеренно без базы: SQL проверяется на реальной Postgres отдельным
прогоном, а здесь — правила, которые обязаны держаться сами по себе.
"""

import ast
import io
import unittest
from pathlib import Path

from wiki import schema as wiki_schema
from wiki import structure as wiki_structure

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / 'wiki'


class FakeCursor(object):
    """Курсор, который запоминает запрос вместо того, чтобы его исполнять."""

    def __init__(self, rows=()):
        self.sql = None
        self.params = None
        self._rows = list(rows)

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class AuditFilterTest(unittest.TestCase):
    """Условие пространства в WHERE."""

    def test_space_adds_clause_with_null(self):
        clause, params = wiki_structure._audit_filters(space_id=12)
        # Запись без пространства видна в любом журнале: у части действий
        # объекта уже нет, и спрятать их везде значило бы вычеркнуть из аудита
        # ровно то, ради чего он ведётся.
        self.assertIn('a.space_id = %s OR a.space_id IS NULL', clause)
        self.assertEqual(params, [12])

    def test_without_space_no_clause(self):
        clause, params = wiki_structure._audit_filters()
        self.assertNotIn('space_id', clause)
        self.assertEqual(params, [])

    def test_space_first_among_filters(self):
        # Пространство — граница, а не уточнение: оно стоит первым и потому
        # отсекает чужое до всех остальных условий.
        clause, params = wiki_structure._audit_filters(
            space_id=12, group='articles', date_from='2026-08-01')
        self.assertTrue(clause.startswith('WHERE (a.space_id'))
        self.assertEqual(params[0], 12)

    def test_space_id_is_cast_to_int(self):
        # В параметры уходит число, а не строка из запроса: сравнение
        # integer-колонки со строкой Postgres не переживёт.
        _clause, params = wiki_structure._audit_filters(space_id='12')
        self.assertEqual(params, [12])


class AuditReadersTest(unittest.TestCase):
    """Все три читателя журнала обязаны нести границу — иначе «показано 20 из
    3»: выборка своя, а счётчик общий."""

    def test_list_audit_filters_by_space(self):
        cursor = FakeCursor()
        wiki_structure.list_audit(cursor, limit=10, space_id=12)
        self.assertIn('a.space_id', cursor.sql)
        self.assertIn(12, cursor.params)

    def test_count_audit_filters_by_space(self):
        cursor = FakeCursor(rows=[(0,)])
        wiki_structure.count_audit(cursor, space_id=12)
        self.assertIn('a.space_id', cursor.sql)
        self.assertIn(12, cursor.params)

    def test_group_counts_filter_by_space(self):
        cursor = FakeCursor(rows=[])
        wiki_structure.audit_group_counts(cursor, space_id=12)
        self.assertIn('a.space_id', cursor.sql)
        self.assertIn(12, cursor.params)


class AuditSpaceFormulaTest(unittest.TestCase):
    """Формула пространства записи."""

    def test_substitution_leaves_no_placeholders(self):
        sql = wiki_schema.audit_space_sql('a.entity_type', 'a.entity_id', 'a.details')
        for name in ('%(etype)s', '%(eid)s', '%(details)s'):
            self.assertNotIn(name, sql)
        self.assertIn('a.entity_type', sql)
        self.assertIn('a.entity_id', sql)
        self.assertIn('a.details', sql)

    def test_identity_substitution_keeps_placeholders(self):
        # Запись подставляет имена собственных параметров — формула обязана
        # остаться пригодной для execute с ними же.
        sql = wiki_schema.audit_space_sql('%(etype)s', '%(eid)s', '%(details)s')
        self.assertEqual(sql, wiki_schema.AUDIT_SPACE_SQL)

    def test_every_declared_entity_is_in_sql(self):
        for entity in wiki_schema.AUDIT_SPACE_ENTITIES:
            self.assertIn("WHEN '%s'" % entity, wiki_schema.AUDIT_SPACE_SQL)

    def test_details_space_id_is_guarded(self):
        # details приходит снаружи: «space_id»: «потом» не должен ронять
        # запись в журнал, то есть и само действие.
        self.assertIn("~ '^[0-9]+$'", wiki_schema.AUDIT_SPACE_SQL)


def _entity_types_written(path):
    """Типы объектов, с которыми в этом файле зовут log_action."""
    tree = ast.parse(io.open(str(path), encoding='utf-8').read())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
        if name != 'log_action':
            continue
        for keyword in node.keywords:
            if keyword.arg != 'entity_type':
                continue
            # Тип бывает и выражением: у гостевого доступа он выбирается
            # тернарником («раздел или статья»), и обе ветки — настоящие
            # значения, которые доедут до базы.
            values = ([keyword.value.body, keyword.value.orelse]
                      if isinstance(keyword.value, ast.IfExp) else [keyword.value])
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
    return found


class AuditSpaceGuardTest(unittest.TestCase):
    """Страж от возврата.

    Забытый тип объекта не ломается и ничего не сообщает: запись просто
    получает space_id = NULL и оказывается в журнале ВСЕХ пространств — то
    самое состояние, из которого раздел выбирался. Молчаливая дыра ловится
    только так: перечнем типов, которые пишут роуты, против перечня, который
    формула умеет разобрать.
    """

    def test_all_written_entity_types_are_known(self):
        written = set()
        for path in sorted(WIKI.glob('*.py')):
            written |= _entity_types_written(path)
        self.assertTrue(written, 'log_action в пакете не нашёлся — проверь тест')
        unknown = written - set(wiki_schema.AUDIT_SPACE_ENTITIES)
        self.assertEqual(
            unknown, set(),
            'формула пространства не знает типы %s — запись уйдёт в журнал всех '
            'пространств сразу (wiki/schema.py: AUDIT_SPACE_SQL)' % sorted(unknown))

    def test_audit_route_asks_for_space(self):
        source = io.open(str(WIKI / 'routes_structure.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        route = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'wiki_audit':
                route = ast.get_source_segment(source, node)
        self.assertIsNotNone(route, 'роут журнала не найден')
        # Пространство берётся тем же request_space, что у справочников: там
        # же и отказ по чужому id. Без него граница держалась бы только во
        # фронте, а запрос руками её обходил бы.
        self.assertIn('request_space', route)
        self.assertIn("'space_id': space_id", route)

    def test_audit_route_asks_the_role_ladder(self):
        """Дверь журнала — должность, и проверяет её ОДНА формула.

        Решение владельца 25.08.2026: журнал открыт «с должности СВ и выше».
        Способностью это право не выражается, поэтому на роуте больше нет
        capability=can_manage_access, а гейт стоит в теле. Выведи ту же
        лестницу вторым местом (здесь, в /ping или во фронте) — и места
        однажды разойдутся: вкладка появится у того, кому роут отвечает 403.
        """
        source = io.open(str(WIKI / 'routes_structure.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        route = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'wiki_audit':
                route = ast.get_source_segment(source, node)
        self.assertIsNotNone(route, 'роут журнала не найден')
        self.assertIn('may_read_audit', route,
                      'журнал обязан спрашивать лестницу должностей')
        self.assertNotIn("capability='can_manage_access'", source.split('def wiki_audit')[0][-400:],
                         'способность больше не открывает журнал')
        # Тот же ответ уходит фронту готовым признаком — иначе он выведет свой.
        ping = io.open(str(WIKI / 'routes.py'), encoding='utf-8').read()
        self.assertIn('"can_read_audit": wiki_access.may_read_audit(', ping,
                      '/ping обязан отдавать признак той же функцией')


if __name__ == '__main__':
    unittest.main()
