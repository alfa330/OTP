# -*- coding: utf-8 -*-
"""Граница пространства у справочников парков и офисов.

Справочник был общекомпанейским: одна таблица офисов на всю вику. Пока
пространство было одно, это совпадало с правдой; со вторым («Тез») совпадать
перестало — стоило включить вкладку «Офисы» конструктором, и сотрудник Тез КЦ
видел адреса, телефоны и графики офисов Таксопарков, а через фильтр «по парку»
— и весь список парков.

Набор проверяет границу с трёх сторон, и каждая ловит свой класс возврата:

1. СТРАЖ (DirectorySpaceGuardTest). Обходит wiki/offices.py и wiki/parks.py
   разбором AST и требует space_id у КАЖДОЙ функции, чей SQL упоминает таблицу
   со space_id. Именно страж, а не перечисление известных функций: следующая
   добавленная функция без параметра — это ровно тот способ, которым дыра
   вернётся, и поймать его должен тест, а не ревью.
2. SQL (DirectorySqlScopeTest). Функция не просто ПРИНИМАЕТ space_id, но и
   доносит его до запроса: параметр, потерянный по дороге, выглядит как
   работающая граница.
3. РОУТ (RequestSpaceTest, DirectoryRouteSpaceTest). Пространство приходит из
   запроса, то есть от пользователя, и без проверки чужой id в строке запроса
   открыл бы соседнюю вику — гард во фронте отсекает пункт меню, но не запрос.
"""

import ast
import inspect
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from wiki import offices as wiki_offices  # noqa: E402
from wiki import parks as wiki_parks  # noqa: E402
from wiki import queries  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

# Таблицы, у которых есть своя колонка space_id (schema._scope_directories_to_space).
SCOPED_TABLES = ('wiki_offices', 'wiki_taxi_parks', 'wiki_promotions')

# Функции, которым space_id не нужен, и почему. Список именно с причинами:
# «просто исключение» через полгода читается как забытая функция.
EXEMPT = {
    'snapshot_offices_day':
        'ночной снимок статуса дня идёт по ВСЕМ офисам всех пространств — '
        'это job, а не чей-то запрос (bot_schedule2)',
    'logo_space_ids':
        'отвечает НА вопрос «в каких пространствах этот файл — логотип»: '
        'пространство здесь результат, а не условие выборки. Сверяет его с '
        'пространствами читателя роут /file/<id> — см. tests/test_wiki_park_logo.py',
}


def _scoped_functions(module):
    """{имя функции: список таблиц} — только те, чей SQL трогает таблицу со space_id.

    Docstring из разбора исключён: SQL функции в нём не живёт, а имя таблицы
    попадает туда постоянно — хоть ссылкой на файл тестов
    (`tests/test_wiki_offices.py`), хоть объяснением, откуда пришли значения.
    Так в список однажды попала `day_state` — чистая функция, которая базу не
    видит вовсе и получает уже прочитанные поля.
    """
    tree = ast.parse(inspect.getsource(module))
    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tables = set()
        docstring = ast.get_docstring(node, clean=False)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                if docstring is not None and inner.value == docstring:
                    continue
                for table in SCOPED_TABLES:
                    if table in inner.value:
                        tables.add(table)
        if tables:
            found[node.name] = sorted(tables)
    return found


class DirectorySpaceGuardTest(unittest.TestCase):
    """У каждой функции справочника, читающей эти таблицы, обязан быть space_id."""

    def _check(self, module):
        missing = []
        for name, tables in _scoped_functions(module).items():
            if name in EXEMPT:
                continue
            signature = inspect.signature(getattr(module, name))
            if 'space_id' not in signature.parameters:
                missing.append('%s (%s)' % (name, ', '.join(tables)))
        self.assertEqual(missing, [], 'без space_id в %s: %s'
                         % (module.__name__, ', '.join(missing)))

    def test_offices_module_is_fully_scoped(self):
        self._check(wiki_offices)

    def test_parks_module_is_fully_scoped(self):
        self._check(wiki_parks)

    def test_space_id_is_keyword_only(self):
        """Именованным, а не позиционным: у офисных функций уже есть office_id,
        park_id и day, и позиционный space_id рано или поздно встал бы не туда —
        причём молча, потому что все они целые числа."""
        for module in (wiki_offices, wiki_parks):
            for name in _scoped_functions(module):
                if name in EXEMPT:
                    continue
                parameter = inspect.signature(getattr(module, name)).parameters['space_id']
                self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY,
                                 '%s.%s' % (module.__name__, name))

    def test_exemptions_still_exist(self):
        """Исключение, переименованное или удалённое, обязано уронить тест.

        Иначе список исключений однажды начнёт прощать не то, что в нём
        написано: имя в нём есть, а функция под ним уже другая."""
        for name in EXEMPT:
            self.assertTrue(hasattr(wiki_offices, name) or hasattr(wiki_parks, name), name)


class _RecordingCursor:
    """Курсор, который запоминает запросы и ничего не находит."""

    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def mentions_space(self, value):
        """Хоть один запрос спросил про space_id И получил это значение."""
        for sql, params in self.calls:
            if 'space_id' not in sql:
                continue
            flat = params.values() if isinstance(params, dict) else (params or ())
            if value in list(flat):
                return True
        return False


SPACE = 11


class DirectorySqlScopeTest(unittest.TestCase):
    """Параметр доезжает до запроса, а не теряется по дороге."""

    def test_list_offices_filters_by_space(self):
        cursor = _RecordingCursor()
        wiki_offices.list_offices(cursor, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_cities_filter_is_scoped(self):
        """Список городов — тоже сведение о соседней вике («у них есть Атырау»),
        пусть и без адреса."""
        cursor = _RecordingCursor()
        wiki_offices.cities(cursor, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_get_office_is_scoped(self):
        cursor = _RecordingCursor()
        wiki_offices.get_office(cursor, 7, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_update_office_is_scoped(self):
        cursor = _RecordingCursor()
        wiki_offices.update_office(cursor, 7, {'city': 'Астана'}, space_id=SPACE)
        sql, params = cursor.calls[-1]
        self.assertIn('space_id = %s', sql)
        self.assertEqual(list(params)[-2:], [7, SPACE])

    def test_closure_is_scoped(self):
        cursor = _RecordingCursor()
        wiki_offices.set_office_closure(cursor, 7, '2026-08-17', None, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))
        cursor = _RecordingCursor()
        wiki_offices.clear_office_closure(cursor, 7, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_office_day_writes_through_the_office(self):
        """В wiki_office_days пространства нет, оно только у самого офиса —
        значит и запись, и снятие отметки обязаны идти через wiki_offices."""
        for call in (
            lambda c: wiki_offices.set_office_day(c, 7, '2026-08-17', 'closed', space_id=SPACE),
            lambda c: wiki_offices.clear_office_day(c, 7, '2026-08-17', space_id=SPACE),
            lambda c: wiki_offices.read_office_day(c, 7, '2026-08-17', space_id=SPACE),
        ):
            cursor = _RecordingCursor()
            call(cursor)
            self.assertIn('wiki_offices', cursor.calls[-1][0])
            self.assertTrue(cursor.mentions_space(SPACE))

    def test_slug_is_free_asks_within_the_space(self):
        """Слаг уникален в пространстве. Спроси шире — и офис «Астана» у Тез
        получил бы слаг astana-2, по номеру которого читается, сколько
        одноимённых записей лежит в чужой вике."""
        cursor = _RecordingCursor()
        wiki_offices.slug_is_free(cursor, 'astana', space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))
        cursor = _RecordingCursor()
        wiki_parks.slug_is_free(cursor, 'astana', space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_list_parks_and_promotions_are_scoped(self):
        cursor = _RecordingCursor()
        wiki_parks.list_parks(cursor, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))
        cursor = _RecordingCursor()
        wiki_parks.list_promotions(cursor, space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_get_park_asks_slug_within_the_space(self):
        cursor = _RecordingCursor()
        self.assertIsNone(wiki_parks.get_park(cursor, 'yandex', space_id=SPACE))
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_update_park_and_promotion_are_scoped(self):
        cursor = _RecordingCursor()
        wiki_parks.update_park(cursor, 3, {'city': 'Астана'}, space_id=SPACE)
        self.assertEqual(list(cursor.calls[-1][1])[-2:], [3, SPACE])
        cursor = _RecordingCursor()
        wiki_parks.update_promotion(cursor, 3, {'title': 'Акция'}, space_id=SPACE)
        self.assertEqual(list(cursor.calls[-1][1])[-2:], [3, SPACE])

    def test_offices_by_park_is_scoped(self):
        cursor = _RecordingCursor()
        wiki_offices.offices_by_park(cursor, [3], space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))

    def test_phones_by_park_is_scoped(self):
        cursor = _RecordingCursor()
        wiki_offices.phones_by_park(cursor, [3], space_id=SPACE)
        self.assertTrue(cursor.mentions_space(SPACE))


class CrossSpaceLinkTest(unittest.TestCase):
    """Связь «офис ↔ парк» и «акция ↔ парк» границу не пересекает.

    Проверяем на отсеве, а не на записи: чужой id приезжает из тела запроса, и
    единственная нужная гарантия — что после него связи не появилось.
    """

    def test_foreign_park_does_not_get_linked_to_the_office(self):
        # own_park_ids ничего не нашёл — значит парк не наш.
        cursor = _RecordingCursor()
        wiki_offices.set_office_parks(cursor, 7, [{'park_id': 999}], space_id=SPACE)
        inserts = [sql for sql, _ in cursor.calls
                   if 'INSERT INTO wiki_office_taxi_parks' in sql]
        self.assertEqual(inserts, [])

    def test_foreign_office_does_not_get_linked_to_the_park(self):
        cursor = _RecordingCursor()
        wiki_offices.set_park_offices(cursor, 3, [{'office_id': 999,
                                                   'phones': [{'phone': '+7'}]}],
                                      space_id=SPACE)
        inserts = [sql for sql, _ in cursor.calls
                   if 'INSERT INTO wiki_office_taxi_parks' in sql]
        self.assertEqual(inserts, [])

    def test_foreign_office_number_is_not_written(self):
        """Номер к чужому офису не пишем: связи для него нет, и строка осталась
        бы висеть невидимым мусором, который вернётся, когда офис выберут."""
        cursor = _RecordingCursor()
        wiki_offices.set_park_numbers(cursor, 3, [{'office_id': 999, 'phone': '+7'}],
                                      space_id=SPACE)
        inserts = [sql for sql, _ in cursor.calls
                   if 'INSERT INTO wiki_park_phones' in sql]
        self.assertEqual(inserts, [])

    def test_online_number_without_office_is_still_written(self):
        """Номер без офиса писать надо: его вторая сторона — сам парк, а он
        свой (роут проверил). Иначе правка «принимаем только по телефону»
        молча теряла бы номер."""
        cursor = _RecordingCursor()
        wiki_offices.set_park_numbers(cursor, 3, [{'office_id': None, 'phone': '+7'}],
                                      space_id=SPACE)
        inserts = [sql for sql, _ in cursor.calls
                   if 'INSERT INTO wiki_park_phones' in sql]
        self.assertEqual(len(inserts), 1)

    def test_promotion_park_link_is_chosen_by_query(self):
        """Парк акции выбирается запросом с условием на пространство, а не
        подставляется значением: акция Тез, привязанная к парку Таксопарков,
        показала бы этот парк в своей карточке — утечка в обход вкладки."""
        cursor = _RecordingCursor()
        wiki_parks.set_promotion_parks(cursor, 5, [999], space_id=SPACE)
        insert = [sql for sql, _ in cursor.calls
                  if 'INSERT INTO wiki_promotion_taxi_parks' in sql]
        self.assertEqual(len(insert), 1)
        self.assertIn('space_id', insert[0])
        self.assertIn('SELECT', insert[0])


def _context(role='sv'):
    return {
        'user_id': 42,
        'otp_role': role,
        'department_id': 560,
        'direction_id': None,
        'headed_department_ids': [],
        'group_ids': [],
        'wiki_roles': [],
        'access_mode': 'auto',
    }


class _SpaceHarness:
    """Блюпринт вики на подменённом курсоре с заданным набором пространств."""

    def build(self, spaces):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 0

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        context = _context()
        for name, value in (
            ('load_access_context', lambda _c, _u: dict(context)),
            ('granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            ('spaces_for_user', lambda _c, _ctx, **_k: list(spaces)),
        ):
            original = getattr(queries, name)
            setattr(queries, name, value)
            self.addCleanup(setattr, queries, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
        ))
        app.config['TESTING'] = True
        return app.test_client(), cursor


@unittest.skipIf(Flask is None, 'flask не установлен')
class RequestSpaceTest(_SpaceHarness, unittest.TestCase):
    """Как роут выбирает пространство и что отвечает на чужое."""

    def test_foreign_space_is_not_found(self):
        """404, а не 403: существование чужого пространства — тоже сведение о
        соседней вике, и «доступ запрещён» подтверждало бы, что оно есть."""
        client, _ = self.build([12])
        for url in ('/api/wiki/offices?space_id=11', '/api/wiki/parks?space_id=11',
                    '/api/wiki/promotions?space_id=11'):
            response = client.get(url)
            self.assertEqual(response.status_code, 404, url)
            self.assertEqual(response.get_json().get('code'), 'WIKI_SPACE_NOT_FOUND', url)

    def test_single_space_needs_no_parameter(self):
        """У большинства сотрудников пространство одно. Требовать называть
        единственно возможное значение значило бы ломать внешние вызовы
        (scripts/migrate_wiki_offices.py) ради формальности."""
        client, _ = self.build([12])
        self.assertEqual(client.get('/api/wiki/offices').status_code, 200)

    def test_two_spaces_without_parameter_is_a_bad_request(self):
        """Молча выбрать первое значит показать справочник, которого не
        спрашивали, — и без единого признака, что он не тот."""
        client, _ = self.build([11, 12])
        response = client.get('/api/wiki/offices')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SPACE_REQUIRED')

    def test_no_spaces_at_all_is_refused(self):
        client, _ = self.build([])
        self.assertEqual(client.get('/api/wiki/offices').status_code, 403)

    def test_own_space_passes(self):
        client, _ = self.build([11, 12])
        self.assertEqual(client.get('/api/wiki/offices?space_id=12').status_code, 200)


@unittest.skipIf(Flask is None, 'flask не установлен')
class DirectoryRouteSpaceTest(_SpaceHarness, unittest.TestCase):
    """Пространство доезжает до SQL из КАЖДОГО роута справочников."""

    READ_ROUTES = (
        '/api/wiki/offices?space_id=12',
        '/api/wiki/parks?space_id=12',
        '/api/wiki/promotions?space_id=12',
        '/api/wiki/parks/yandex?space_id=12',
    )

    def test_every_read_route_asks_the_database_about_space(self):
        for url in self.READ_ROUTES:
            client, cursor = self.build([11, 12])
            client.get(url)
            asked = [call for call in cursor.execute.call_args_list
                     if 'space_id' in ' '.join(str(call.args[0]).split())]
            self.assertTrue(asked, url)

    def test_write_routes_refuse_a_foreign_space(self):
        client, _ = self.build([12])
        for method, url in (('post', '/api/wiki/offices?space_id=11'),
                            ('patch', '/api/wiki/offices/1?space_id=11'),
                            ('delete', '/api/wiki/offices/1?space_id=11'),
                            ('post', '/api/wiki/parks?space_id=11'),
                            ('patch', '/api/wiki/parks/1?space_id=11'),
                            ('delete', '/api/wiki/parks/1?space_id=11'),
                            ('post', '/api/wiki/promotions?space_id=11'),
                            ('patch', '/api/wiki/promotions/1?space_id=11'),
                            ('delete', '/api/wiki/promotions/1?space_id=11'),
                            ('put', '/api/wiki/offices/1/closure?space_id=11'),
                            ('delete', '/api/wiki/offices/1/closure?space_id=11'),
                            ('put', '/api/wiki/offices/1/day/2026-08-17?space_id=11'),
                            ('delete', '/api/wiki/offices/1/day/2026-08-17?space_id=11')):
            response = getattr(client, method)(url, json={'name': 'Офис',
                                                          'state': 'closed'})
            self.assertEqual(response.status_code, 404, '%s %s' % (method, url))

    def test_space_can_come_in_the_body(self):
        """Тело — для внешних вызовов: scripts/migrate_wiki_offices.py шлёт
        JSON и параметров в строке не ставит."""
        client, _ = self.build([12])
        response = client.post('/api/wiki/offices',
                               json={'name': 'Офис', 'space_id': 11})
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
