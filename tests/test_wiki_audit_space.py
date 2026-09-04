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
import re
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

    def test_space_boundary_is_strict(self):
        clause, params = wiki_structure._audit_filters(space_id=12)
        # СТРОГО. Пока «ничья» запись показывалась в любом журнале, у «Теза» на
        # 99 своих записей приходилось 46 чужих — треть ленты. Ничьих записей
        # больше быть не должно (routes_structure.log_space на записи,
        # schema._restore_audit_space_by_session на уже записанном), а остаток
        # считается отдельно и виден в подвале — не прячется молча.
        self.assertIn('a.space_id = %s', clause)
        self.assertNotIn('IS NULL', clause,
                         'ничья запись снова попадёт в журнал обеих вик')
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
        self.assertTrue(clause.startswith('WHERE a.space_id'))
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

    def test_writers_without_object_name_the_space(self):
        """Запись, у которой объекта ещё нет, обязана назвать пространство САМА.

        Пространство записи считается ПО ОБЪЕКТУ (AUDIT_SPACE_SQL). Пока статьи
        нет — черновик из документа, разбор страницы Яндекс Про, правка
        несохранённого текста — считать не по чему, и запись остаётся ничьей.
        А ничья запись при строгой границе (structure._audit_filters) не попадёт
        уже никуда: раньше она была видна в обоих журналах, теперь — ни в одном.

        Проверка идёт по ВСЕМУ пакету, и это правка от 04.09.2026. Прежде она
        читала один routes_import.py — именно поэтому мимо неё прошёл
        routes_yandex_pro.py, и 36 разборов страниц утекли в журнал «Теза» уже
        ПОСЛЕ того, как болезнь считалась вылеченной. Страж, знающий один файл,
        сторожит не правило, а тот файл.

        Опасны два вида entity_id: литеральный None (объекта нет заведомо) и
        вычисленный вызовом — `_int_or_none(request.form.get('article_id'))`
        возвращает None ровно тогда, когда статью ещё не сохранили. Обоим нужен
        либо space_id аргументом, либо 'space_id' в details: формула читает и
        то, и другое.
        """
        missing = []
        for path in sorted(WIKI.glob('*.py')):
            source = io.open(str(path), encoding='utf-8').read()
            for node in ast.walk(ast.parse(source)):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, 'attr', None) == 'log_action'):
                    continue
                kwargs = {kw.arg: kw.value for kw in node.keywords}
                entity = kwargs.get('entity_id')
                objectless = (entity is None
                              or (isinstance(entity, ast.Constant)
                                  and entity.value is None)
                              or isinstance(entity, ast.Call))
                if not objectless:
                    continue
                details = kwargs.get('details')
                named = 'space_id' in kwargs or (
                    isinstance(details, ast.Dict)
                    and any(isinstance(key, ast.Constant) and key.value == 'space_id'
                            for key in details.keys))
                if named:
                    continue
                action = next((kw.value.value for kw in node.keywords
                               if kw.arg == 'action'
                               and isinstance(kw.value, ast.Constant)), '?')
                missing.append('%s:%s (%s)' % (path.name, node.lineno, action))
        self.assertEqual(
            missing, [],
            'запись без объекта обязана назвать пространство, иначе она не '
            'попадёт ни в один журнал: %s' % ', '.join(missing))

    def test_every_action_is_readable_and_filterable(self):
        """У каждого действия есть русская подпись и группа-чип.

        Молчит такая дыра совершенно одинаково с дырой про пространство: запись
        пишется, читается и выглядит рабочей — только подпись у неё английский
        ключ, а под фильтром её нет вовсе. На 04.09.2026 без подписи было 336
        записей из 1390, без группы — 341; больше всех молчал article.migrate
        (247 записей), то есть основная работа «Переноса».

        Шапка самого словаря (auditEvents.js) обещает ровно это: «остальные 28
        выводились сырым ключом» — как описание прошлого, а не порядка вещей.
        """
        actions = set()
        for path in sorted(WIKI.glob('*.py')):
            source = io.open(str(path), encoding='utf-8').read()
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
                if name == 'log_action':
                    for kw in node.keywords:
                        if kw.arg == 'action' and isinstance(kw.value, ast.Constant):
                            actions.add(kw.value.value)
                # Отказ источника пишет в журнал через свою обёртку, и действие
                # приезжает туда позиционным аргументом: без этой ветки пять
                # действий Яндекс Про страж бы не увидел.
                elif name == '_fail':
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                                and re.match(r'^[a-z_]+\.[a-z_.]+$', arg.value):
                            actions.add(arg.value)
        self.assertGreater(len(actions), 25, 'действия не нашлись — проверь страж')

        meta = io.open(str(ROOT / 'src/components/wiki/auditEvents.js'),
                       encoding='utf-8').read()
        labelled = set(re.findall(r"'([a-z_]+\.[a-z_.]+)':\s*\{\s*label:", meta))
        self.assertEqual(
            sorted(actions - labelled), [],
            'действие показывается сырым английским ключом (ACTION_META)')

        grouped = set()
        for group in wiki_structure.AUDIT_GROUPS.values():
            grouped |= set(group)
        self.assertEqual(
            sorted(actions - grouped), [],
            'действие не попадает ни под один чип фильтра (structure.AUDIT_GROUPS)')

    def test_log_space_lives_in_one_place(self):
        """Помощник один на все двери.

        Он был написан в routes_import.py как приватный `_log_space`, и когда
        та же дыра открылась в routes_yandex_pro.py, второй файл про него
        просто не знал. Копия помощника разошлась бы с оригиналом на первой же
        правке, поэтому он лежит рядом с request_space — там же, откуда его
        берут справочники парков и офисов.
        """
        shared = io.open(str(WIKI / 'routes_structure.py'), encoding='utf-8').read()
        self.assertIn('def log_space(cursor, ctx):', shared,
                      'общий помощник обязан жить рядом с request_space')
        for name in ('routes_import.py', 'routes_yandex_pro.py'):
            source = io.open(str(WIKI / name), encoding='utf-8').read()
            self.assertIn('log_space', source, '%s обязан звать общий помощник' % name)
            self.assertNotIn('def _log_space', source,
                             '%s завёл вторую копию помощника' % name)

    def test_article_create_names_the_space_of_its_section(self):
        """Создание статьи называет пространство разделом, а не связью статьи.

        Связь статьи с разделом на боевой базе появлялась ПОЗЖЕ записи, и три
        записи о создании остались ничьими навсегда.
        """
        source = io.open(str(WIKI / 'routes_edit.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'log_action'
                    and any(kw.arg == 'action' and isinstance(kw.value, ast.Constant)
                            and kw.value.value == 'article.create'
                            for kw in node.keywords)):
                kwargs = {kw.arg for kw in node.keywords}
                self.assertIn('space_id', kwargs,
                              'запись о создании обязана назвать пространство')
                return
        self.fail('запись о создании статьи не найдена — проверь тест')

    def test_object_backfill_runs_every_start(self):
        """Разбор по объекту повторяется, догадка по дате — нет.

        Повтор безопасен: разобрать можно только запись, объект которой есть.
        Намеренно ничья (объекта нет и не было) не разберётся никогда.
        """
        source = io.open(str(WIKI / 'schema.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and 'first_run = ' in (
                    ast.get_source_segment(source, node) or ''):
                func = ast.get_source_segment(source, node)
        self.assertIsNotNone(func, 'миграция журнала не найдена — проверь тест')
        backfill = func.index('UPDATE wiki_audit_log a SET space_id = (')
        guard = func.index('if not first_run:')
        self.assertLess(backfill, guard,
                        'разбор по объекту обязан идти ДО возврата: иначе запись, '
                        'у которой пространство появилось позже, останется ничьей')

    def test_session_restore_runs_every_start(self):
        """Разбор по сессии повторяется вместе с разбором по объекту.

        Он детерминированный и сам себя не расширяет: трогает только записи без
        пространства и только там, где среди соседей ровно одно пространство.
        Уехав под `if not first_run`, он на боевой базе не отработал бы вовсе —
        колонка там появилась ещё 25.08.2026.
        """
        source = io.open(str(WIKI / 'schema.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        migration = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and 'first_run = ' in (
                    ast.get_source_segment(source, node) or ''):
                migration = ast.get_source_segment(source, node)
        self.assertIsNotNone(migration, 'миграция журнала не найдена — проверь тест')
        self.assertIn('_restore_audit_space_by_session(cursor)', migration,
                      'разбор по сессии обязан вызываться из миграции')
        self.assertLess(migration.index('_restore_audit_space_by_session(cursor)'),
                        migration.index('if not first_run:'),
                        'разбор по сессии обязан идти ДО возврата: иначе на '
                        'боевой базе он не отработает никогда')

    def test_session_restore_is_unambiguous_and_signed(self):
        """Правило разбора: ровно одно пространство у соседей, и след в записи.

        Без HAVING count(DISTINCT ...) = 1 правило превратилось бы в догадку
        «где человек обычно работает», а без пометки в details журнал молча
        назначил бы себе хозяина — и отличить восстановленное от известного по
        объекту стало бы нечем.
        """
        source = io.open(str(WIKI / 'schema.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        restore = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == '_restore_audit_space_by_session'):
                restore = ast.get_source_segment(source, node)
        self.assertIsNotNone(restore, 'разбор по сессии не найден')
        self.assertIn('HAVING count(DISTINCT pair.space_id) = 1', restore,
                      'без единственного пространства это догадка, а не разбор')
        self.assertIn("'space_restored'", restore,
                      'восстановленная запись обязана сказать, откуда узнала')
        self.assertIn('a.space_id IS NULL', restore,
                      'разбор не смеет переписывать уже известное пространство')
        # Соседом считается только запись, знающая пространство ПО ОБЪЕКТУ.
        # Иначе разбор перестаёт быть неподвижной точкой: восстановленная
        # запись становится соседом следующего прогона, и на ВТОРОМ деплое
        # пространство получает уже тот, у кого своих соседей не было, — по
        # цепочке, а не по доказательству. Проверено на Postgres: без этой
        # строки второй прогон размечает запись, до которой от якоря 55 минут.
        self.assertIn("NOT (neighbour.details ? 'space_restored')", restore,
                      'восстановленная запись не может быть основанием для другой')

    def test_audit_route_reports_records_outside_spaces(self):
        """Строгая граница не имеет права терять записи молча.

        Ничья запись раньше была видна везде, теперь — нигде. Значит её
        существование обязано быть видно хотя бы числом, иначе первый же новый
        источник ничьих записей пройдёт незамеченным.
        """
        source = io.open(str(WIKI / 'routes_structure.py'), encoding='utf-8').read()
        tree = ast.parse(source)
        route = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'wiki_audit':
                route = ast.get_source_segment(source, node)
        self.assertIsNotNone(route, 'роут журнала не найден')
        self.assertIn('count_audit_outside', route,
                      'роут обязан считать записи вне пространств')
        screen = io.open(str(ROOT / 'src/components/wiki/WikiAudit.jsx'),
                         encoding='utf-8').read()
        self.assertIn('outside', screen,
                      'экран обязан показывать число записей вне пространств')

    def test_outside_count_ignores_the_space_filter(self):
        """Вопрос «сколько записей вне пространств» не сужается пространством.

        Передай space_id в этот счётчик — и он ответит нулём всегда: записи без
        пространства не проходят условие своей же границы.
        """
        cursor = FakeCursor(rows=[(0,)])
        wiki_structure.count_audit_outside(cursor, space_id=12, group='articles')
        self.assertIn('a.space_id IS NULL', cursor.sql)
        self.assertNotIn(12, cursor.params or [])

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


class LogSpaceTest(unittest.TestCase):
    """Пространство для журнала берётся мягко и только своё.

    Мягко — потому что это НЕ доступ: импорт документа никакого пространства не
    проверяет, и отказывать 400 на отсутствующий параметр здесь нельзя (иначе
    старый бандл после деплоя перестал бы собирать черновики). Только своё —
    потому что иначе запись о действии уехала бы в чужой журнал по одному
    параметру строки запроса.
    """

    def _space(self, args=None, form=None, body=None, allowed=(11, 12)):
        from flask import Flask
        from wiki import queries as wiki_queries
        from wiki import routes_structure

        original = wiki_queries.spaces_for_user
        wiki_queries.spaces_for_user = lambda _c, _ctx, **_k: list(allowed)
        self.addCleanup(setattr, wiki_queries, 'spaces_for_user', original)

        app = Flask(__name__)
        with app.test_request_context('/?' + (args or ''), data=form,
                                      json=body if body is not None else None):
            return routes_structure.log_space(object(), {'user_id': 1})

    def test_query_parameter_wins(self):
        self.assertEqual(self._space(args='space_id=12'), 12)

    def test_form_field_is_read_too(self):
        """Черновик из документа уходит формой, а не строкой запроса."""
        self.assertEqual(self._space(form={'space_id': '11'}), 11)

    def test_foreign_space_is_ignored(self):
        """Чужой id не принимаем: запись уехала бы в чужой журнал."""
        self.assertIsNone(self._space(args='space_id=99'))

    def test_absent_parameter_is_not_an_error(self):
        """Нет параметра — нет пространства, но и отказа нет."""
        self.assertIsNone(self._space())


if __name__ == '__main__':
    unittest.main()
