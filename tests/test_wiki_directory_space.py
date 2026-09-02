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
import functools
import inspect
import re
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
from wiki import structure as wiki_structure  # noqa: E402
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


# ─────────────────────────────────────────────────────────────────────────────
# Справочник читают и ЧУЖИЕ разделы
#
# Страж выше обходит два модуля вики, и этого оказалось мало. 31.08.2026
# владелец нашёл в мастере раздела «Обращения» офис «Tez Taxi»: раздел читает
# wiki_offices и wiki_taxi_parks ПРЯМЫМ SQL, мимо wiki/offices.py и
# wiki/parks.py, — то есть мимо места, где страж стоял. Так же читает
# справочник офисов раздел «Посылки».
#
# Поэтому второй страж обходит РЕПОЗИТОРИЙ, а не список модулей: следующий
# раздел, которому понадобится справочник компании, напишет такой же SELECT, и
# поймать его должен тест, а не память ревьюера.
# ─────────────────────────────────────────────────────────────────────────────

# Чтение таблицы, а не упоминание её имени: у DDL в parcels/schema.py стоит
# REFERENCES wiki_offices(id) — это внешний ключ, а не выборка, и границы
# пространства он не требует.
_READ_RE = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+(%s)\b'
                      % '|'.join(SCOPED_TABLES), re.IGNORECASE)

# Граница — это УСЛОВИЕ по space_id, а не слово «space_id» где-нибудь в тексте
# запроса. Проверять вхождением значило бы принимать за границу и комментарий
# внутри SQL, и колонку в SELECT: страж, который так легко успокоить,
# успокоится сам собой при первой же правке.
_SCOPE_RE = re.compile(r'\bspace_id\s*(?:=|<>|!=|\bIN\b|\bNOT\s+IN\b)', re.IGNORECASE)

# Каталоги, которые страж не обходит, и почему.
_SWEEP_SKIP_DIRS = {
    'venv', 'node_modules', 'dist', '__pycache__', '.git', 'assets', 'public',
    'wiki',    # свой страж выше, и он строже: там нужен ещё и keyword-only параметр
    'tests',   # тесты нарочно собирают запросы без границы, чтобы проверить отказ
}


def _sql_strings(path):
    """Строки-константы файла, кроме докстрок.

    Докстроки исключены по той же причине, что и в _scoped_functions: имя
    таблицы попадает туда постоянно — объяснением, откуда взялись данные, или
    ссылкой на соседний модуль. SQL в докстроке не живёт.
    """
    tree = ast.parse(Path(path).read_text(encoding='utf-8'), str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            text = ast.get_docstring(node, clean=False)
            if text:
                docstrings.add(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                yield node.lineno, node.value
        # f-строка запроса собирается из кусков, и таблица с условием могут
        # лежать в разных. Склеиваем весь литерал: подставляемое выражение
        # заменяем пробелом — что в нём, страж всё равно не знает, а склеенные
        # без него слова дали бы ложное «FROM wiki_officesчто-то».
        elif isinstance(node, ast.JoinedStr):
            parts = [piece.value if isinstance(piece, ast.Constant)
                     and isinstance(piece.value, str) else ' '
                     for piece in node.values]
            yield node.lineno, ''.join(parts)


@functools.lru_cache(maxsize=1)
def _repository_sql_reads():
    """[(файл, строка, sql)] — все чтения справочников вне пакета wiki.

    Кеш на один вызов: обход разбирает весь репозиторий (bot_schedule2.py — 56
    тысяч строк), и повторять его для каждого теста набора незачем.
    """
    found = []
    for path in sorted(ROOT.rglob('*.py')):
        relative = path.relative_to(ROOT)
        if any(part in _SWEEP_SKIP_DIRS or part.startswith('.') for part in relative.parts):
            continue
        for lineno, value in _sql_strings(path):
            if _READ_RE.search(value):
                found.append((relative.as_posix(), lineno, ' '.join(value.split())))
    return tuple(found)


# Функции разделов, у которых пространство — обязательный аргумент.
_MUST_PASS_SPACES = ('taxi_parks', 'city_offices',
                     'list_offices', 'offices_in_city', 'read_office')


class ConsumerDirectorySweepTest(unittest.TestCase):
    """Кто угодно вне вики, читающий справочник, обязан спросить пространство."""

    def test_every_outside_read_mentions_space(self):
        unscoped = ['%s:%d — %s' % (path, lineno, sql[:90])
                    for path, lineno, sql in _repository_sql_reads()
                    if not _SCOPE_RE.search(sql)]
        self.assertEqual(unscoped, [], 'чтение справочника без пространства:\n' +
                         '\n'.join(unscoped))

    def test_a_mention_of_space_is_not_a_boundary(self):
        """Слово в комментарии или колонка в SELECT границей не являются."""
        self.assertIsNone(_SCOPE_RE.search(
            'SELECT o.id, o.space_id -- пространство FROM wiki_offices o'))
        self.assertTrue(_SCOPE_RE.search('WHERE o.space_id = ANY(%(spaces)s)'))
        self.assertTrue(_SCOPE_RE.search('WHERE o.space_id IN (11, 12)'))

    def test_the_sweep_actually_finds_the_known_readers(self):
        """Страж, переставший что-либо находить, тест не роняет — и молча
        перестаёт сторожить. Поэтому проверяем и сам обход: оба известных
        читателя обязаны в него попадать."""
        files = {path for path, _, _ in _repository_sql_reads()}
        self.assertIn('crm/queries.py', files)
        self.assertIn('parcels/queries.py', files)

    def test_every_call_of_a_scoped_reader_passes_the_space(self):
        """Забытый аргумент упал бы TypeError'ом в проде, а не в CI.

        parcels/routes.py зовёт offices_in_city из _validate — то есть в момент,
        когда менеджер сохраняет карточку посылки. Поймать это на сборке дешевле,
        чем тем же TypeError'ом у человека в форме.
        """
        bad = []
        for package in (ROOT / 'crm', ROOT / 'parcels'):
            for file in sorted(package.rglob('*.py')):
                tree = ast.parse(file.read_text(encoding='utf-8'), str(file))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
                    if name not in _MUST_PASS_SPACES:
                        continue
                    if not any(word.arg == 'space_ids' for word in node.keywords):
                        bad.append('%s:%d — %s()'
                                   % (file.relative_to(ROOT).as_posix(), node.lineno, name))
        self.assertEqual(bad, [], 'вызов справочника без space_ids:\n' + '\n'.join(bad))

    def test_ddl_reference_is_not_mistaken_for_a_read(self):
        """REFERENCES wiki_offices(id) — внешний ключ карточки посылки. Требовать
        от него пространство значило бы просить границу у колонки."""
        self.assertIsNone(_READ_RE.search(
            'office_id INTEGER REFERENCES wiki_offices(id) ON DELETE SET NULL'))


class SectionSpaceSourceTest(unittest.TestCase):
    """Откуда чужой раздел берёт пространство — и что делает, не найдя его."""

    def test_departments_of_the_section_resolve_to_spaces(self):
        cursor = _RecordingCursor([(SPACE,)])
        self.assertEqual(
            wiki_structure.space_ids_for_departments(cursor, ['SZoV', ' szov ']),
            [SPACE])
        sql, params = cursor.calls[0]
        self.assertIn('wiki_space_departments', sql)
        self.assertIn("sp.status = 'active'", sql)
        # Код отдела приводится к нижнему регистру по обе стороны сравнения, и
        # дубли схлопываются: иначе 'szov' и 'SZoV' дали бы два одинаковых
        # условия, а пространство — дважды.
        self.assertEqual(params, (['szov'],))

    def test_no_departments_means_no_spaces(self):
        """Пустой список отделов не должен превращаться в «все пространства»."""
        cursor = _RecordingCursor([(SPACE,)])
        self.assertEqual(wiki_structure.space_ids_for_departments(cursor, []), [])
        self.assertEqual(cursor.calls, [])

    def test_space_without_departments_is_not_ours(self):
        """«Пусто = видно всем» здесь НЕ действует: запрос требует строку в
        wiki_space_departments, а не её отсутствие. Иначе первое же
        полунастроенное пространство снова вылило бы офисы в чужой раздел."""
        source = inspect.getsource(wiki_structure.space_ids_for_departments)
        self.assertNotIn('NOT EXISTS', source)


# Справочники, которые показывают ЧУЖУЮ ОРГСТРУКТУРУ, если забыть про
# пространство: отделы, направления, группы и сотрудники.
#
# Дыра была ровно такой: 02.09.2026 владелец увидел в «Таксопарках» отдел
# «Тез КЦ». Обе функции сужались только границей отдела РАЗДАЮЩЕГО, а у
# супер-админа и администратора вики её нет вовсе — значит отдавались все семь
# отделов и все 192 сотрудника, включая 21 человека чужой компании.
CATALOG_ENDPOINTS = ('/access/subjects', '/access/people')


class SubjectCatalogSpaceTest(unittest.TestCase):
    """Пространство — вторая граница справочников, рядом с границей отдела."""

    def test_both_catalogs_take_the_space_separately(self):
        """Границы РАЗНЫЕ и приходят разными аргументами.

        Подмешать пространство в department_ids было бы соблазнительно и
        неверно: этим параметром справочник заодно решает, предлагать ли роли
        вики и должности (они адресуют людей по всей компании, мимо отдела).
        Супер-админ, открывший форму в конкретном пространстве, потерял бы
        правило на должность — то есть починка одной дыры сломала бы рабочий
        случай.
        """
        for func in (wiki_structure.subject_catalog, wiki_structure.grantable_people):
            names = inspect.signature(func).parameters
            self.assertIn('space_department_ids', names, func.__name__)
            self.assertIn('department_ids', names, func.__name__)

    def test_the_two_boundaries_add_up(self):
        """Пересечение, а не замена: у супервайзера остаётся его отдел."""
        narrow = wiki_structure.narrow_to_space
        self.assertIsNone(narrow(None, None))
        self.assertEqual(narrow(None, [1, 367]), [1, 367])
        self.assertEqual(narrow([1], [1, 367]), [1])
        # Чужой отдел в чужом пространстве — пусто, а НЕ «значит без границы».
        self.assertEqual(narrow([560], [1, 367]), [])

    def test_an_empty_space_is_not_all_departments(self):
        """Пространству не выдали отделов — справочник пуст, а не полон.

        Подмена пустого списка на «все» вернула бы дыру целиком: первое же
        полунастроенное пространство снова показало бы чужую оргструктуру.
        """
        source = inspect.getsource(wiki_structure.space_department_ids)
        self.assertIn('wiki_space_departments', source)
        self.assertNotIn('NOT EXISTS', source)
        self.assertEqual(wiki_structure.narrow_to_space(None, []), [])

    def test_routes_resolve_the_space_before_answering(self):
        """Оба роута спрашивают request_space — то есть проверяют, что
        пространство вообще выдано спрашивающему. Без этого чужой space_id в
        строке запроса перечислил бы отделы соседней вики."""
        source = (ROOT / 'wiki' / 'routes_structure.py').read_text(encoding='utf-8')
        for name in ('def wiki_access_subjects', 'def wiki_access_people'):
            body = source[source.index(name):]
            body = body[:body.index('\n    @wiki_route')] if '\n    @wiki_route' in body else body
            self.assertIn('request_space(cursor, ctx)', body, name)
            self.assertIn('space_department_ids', body, name)

    def test_the_only_way_to_get_every_department_is_gated(self):
        """Полный список отделов нужен КОНСТРУКТОРУ пространств — ему их
        раздавать. Это исключение обязано быть явным и под тем же гейтом, что и
        сам конструктор: иначе «покажи всё» станет обходом границы."""
        source = (ROOT / 'wiki' / 'routes_structure.py').read_text(encoding='utf-8')
        body = source[source.index('def wiki_access_subjects'):]
        body = body[:body.index('\n    @wiki_route')]
        self.assertIn("request.args.get('scope') == 'all'", body)
        self.assertIn('_may_manage_space(ctx)', body)

    def test_counters_are_counted_inside_the_boundary(self):
        """Плитки на главной видит КАЖДЫЙ вошедший, а считались они по всей базе.

        Имён это не выдавало, но «Пространств: 2» сообщало сотруднику «Тез КЦ» о
        существовании чужой вики, а «Статей: 340» при двенадцати своих отвечало
        на тот же вопрос числом.
        """
        names = inspect.signature(queries.counters).parameters
        self.assertIn('space_ids', names)
        source = inspect.getsource(queries.counters)
        # Все три числа — внутри границы, а не одно поправленное снаружи.
        self.assertIn('wiki_spaces', source)
        self.assertIn('wiki_sections', source)
        self.assertIn('wiki_article_sections', source)
        ping = (ROOT / 'wiki' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn('queries.counters(cursor, space_ids=', ping)
        # Правка отдельного ключа снаружи запроса — то самое второе место,
        # которое однажды разойдётся с первым.
        self.assertNotIn("payload['counters']['spaces'] =", ping)

    def test_every_caller_in_the_front_passes_a_space_or_asks_for_all(self):
        """СТРАЖ ОТ ВОЗВРАТА, и главный здесь.

        Дыру вернёт не правка сервера, а новый экран, который позовёт справочник
        без space_id: сервер у человека с ОДНИМ пространством ответит молча и
        правильно, а у владельца с двумя — 400, и это заметят не сразу.
        Поэтому проверяется каждый вызов во фронте, а не список известных.
        """
        offenders = []
        for path in sorted((ROOT / 'src' / 'components').rglob('*.jsx')):
            text = path.read_text(encoding='utf-8')
            for endpoint in CATALOG_ENDPOINTS:
                for match in re.finditer(re.escape(endpoint) + r'`', text):
                    # Хвост вызова до конца строки: там и лежат params.
                    tail = text[match.end():text.index('\n', match.end())]
                    if 'space_id' in tail or "scope: 'all'" in tail:
                        continue
                    offenders.append('%s: %s%s' % (path.name, endpoint, tail.rstrip()))
        self.assertEqual(offenders, [], 'вызов справочника без пространства: %s' % offenders)


if __name__ == '__main__':
    unittest.main()
