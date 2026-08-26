# -*- coding: utf-8 -*-
"""Каталог статей по разделам — вкладка «Статьи» и счётчики главной.

Что здесь проверяется и почему именно это.

1. КОРЗИНЫ ПОКРЫВАЮТ ВСЕ СТАТУСЫ. Переключатель делит статьи на три группы, а
   статусов в CHECK'е шесть. Забудь один — и статьи с ним исчезнут из раздела
   молча: ни в одной корзине, ни в счётчике. Тест сверяет ARTICLE_BUCKETS с
   самим CHECK'ом в DDL, а не со вторым списком рядом.

2. СЧЁТЧИК И СПИСОК ЗА НИМ — ОДНО ЧИСЛО. Плитка «9 черновиков» обязана
   открываться девятью черновиками. Держится это на двух вещах: общий периметр
   (_browse) и общее определение корзины. Обе проверяются.

3. СТАТЬЯ В ДВУХ РАЗДЕЛАХ НЕ СЧИТАЕТСЯ ДВАЖДЫ. Итог считается отдельным
   запросом, а не суммой плиток, — иначе «Статей» на главной было бы больше,
   чем статей в базе.

4. СТАТЬЯ БЕЗ РАЗДЕЛА ДОСТУПНА. Наследие импорта (см.
   test_wiki_orphans_and_favorites): такая статья не попадает ни на одну плитку
   раздела, и без отдельной плитки «Без раздела» была бы недостижима.

5. ЭКРАН ОТКРЫВАЕТСЯ СПИСКОМ. С 26.08.2026 правая колонка не пустует: пока
   раздел не выбран, там лежит вся корзина целиком. Решения этого экрана живут
   в WikiCatalog.jsx, и сторожит их Python — читая исходник текстом. Причина
   та же, что и у пунктов выше: список за счётчиком обязан быть полным и
   ровно из той вики, по которой счётчик посчитан, а обе эти границы задаются
   параметрами запроса во фронте. Отрисовку экрана проверяет
   tests/wiki_catalog_all.test.mjs — там компонент настоящий.
"""

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

from wiki import articles as wiki_articles  # noqa: E402
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import routes_articles  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki import structure as wiki_structure  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


class BucketsCoverStatusesTest(unittest.TestCase):
    """Корзины витрины против CHECK'а в DDL — один источник, не два списка."""

    def _statuses_from_ddl(self):
        ddl = '\n'.join(s for s in wiki_schema._STATEMENTS if isinstance(s, str))
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS wiki_articles.*?status\s+VARCHAR\([^)]*\).*?"
            r"CHECK \(status IN \((.*?)\)\)",
            ddl, re.S)
        self.assertIsNotNone(match, 'не нашли CHECK статуса статьи в DDL')
        return {value for value in re.findall(r"'([a-z_]+)'", match.group(1))}

    def test_every_status_lands_in_exactly_one_bucket(self):
        from_ddl = self._statuses_from_ddl()
        self.assertEqual(len(from_ddl), 6, 'статусов в CHECK стало другое число')

        covered = [status
                   for statuses in wiki_schema.ARTICLE_BUCKETS.values()
                   for status in statuses]
        self.assertEqual(sorted(covered), sorted(set(covered)),
                         'статус попал сразу в две корзины — счётчики разойдутся')
        self.assertEqual(set(covered), from_ddl,
                         'корзины разошлись с CHECK: статус без корзины исчезнет '
                         'из раздела молча')

    def test_reverse_map_is_built_from_the_buckets(self):
        for bucket, statuses in wiki_schema.ARTICLE_BUCKETS.items():
            for status in statuses:
                self.assertEqual(wiki_schema.BUCKET_OF_STATUS[status], bucket)


class CountingCursor:
    """Курсор, отвечающий тремя заготовленными выборками каталога.

    Различает запросы по тому, что в них есть: разбор SQL здесь не нужен,
    достаточно узнать три запроса catalog_counts друг от друга.
    """

    def __init__(self, *, by_section, orphans, totals):
        self.by_section = by_section        # [(section_id, status, count), ...]
        self.orphans = orphans              # [(status, count), ...]
        self.totals = totals                # [(status, count), ...]
        self.queries = []
        self._rows = []

    def execute(self, sql, params=None):
        flat = ' '.join(sql.split())
        self.queries.append(flat)
        if 'FROM wiki_article_sections s' in flat and 'GROUP BY s.section_id' in flat:
            self._rows = self.by_section
        elif 'NOT EXISTS' in flat:
            self._rows = self.orphans
        else:
            self._rows = self.totals

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class CatalogCountsTest(unittest.TestCase):
    """Раскладка статусов по корзинам и три независимых счёта."""

    def make(self):
        return CountingCursor(
            by_section=[
                (3, 'published', 5),
                (3, 'draft', 2),
                (3, 'on_approval', 1),
                (4, 'published', 4),
                (4, 'archived', 3),
                (4, 'expired', 1),
            ],
            orphans=[('draft', 2)],
            # Одна и та же статья лежит и в 3, и в 4: сумма по разделам даёт 9
            # опубликованных, а их восемь.
            totals=[('published', 8), ('draft', 4), ('on_approval', 1),
                    ('archived', 3), ('expired', 1)],
        )

    def test_statuses_collapse_into_three_buckets(self):
        counts = wiki_articles.catalog_counts(self.make(), {1, 2, 3})
        self.assertEqual(counts['sections'][3],
                         {'published': 5, 'draft': 3, 'archived': 0})
        self.assertEqual(counts['sections'][4],
                         {'published': 4, 'draft': 0, 'archived': 4},
                         'expired обязан лежать в архиве, а не пропадать')

    def test_totals_are_counted_separately_from_sections(self):
        counts = wiki_articles.catalog_counts(self.make(), {1, 2, 3})
        self.assertEqual(counts['totals']['published'], 8,
                         'статья в двух разделах посчиталась дважды')
        self.assertEqual(counts['totals']['draft'], 5)
        self.assertEqual(counts['totals']['archived'], 4)

    def test_orphans_have_their_own_count(self):
        counts = wiki_articles.catalog_counts(self.make(), {1, 2, 3})
        self.assertEqual(counts['orphans'], {'published': 0, 'draft': 2, 'archived': 0})

    def test_unknown_status_is_not_dropped(self):
        """Седьмой статус в базе должен где-то всплыть, а не исчезнуть."""
        cursor = CountingCursor(by_section=[(3, 'wat', 2)], orphans=[], totals=[])
        counts = wiki_articles.catalog_counts(cursor, {1})
        self.assertEqual(sum(counts['sections'][3].values()), 2)

    def test_empty_perimeter_asks_the_database_nothing(self):
        cursor = CountingCursor(by_section=[], orphans=[], totals=[])
        counts = wiki_articles.catalog_counts(cursor, set())
        self.assertEqual(cursor.queries, [])
        self.assertEqual(counts['totals'], {'published': 0, 'draft': 0, 'archived': 0})


class ListArticlesFilterTest(unittest.TestCase):
    """Параметры выборки: корзина шире одного статуса, «без раздела» — флаг."""

    def params_for(self, **kwargs):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        wiki_articles.list_articles(cursor, {1, 2}, **kwargs)
        return cursor.execute.call_args[0][1]

    def test_single_status_still_works(self):
        self.assertEqual(self.params_for(status='draft')['statuses'], ['draft'])

    def test_bucket_widens_to_all_of_its_statuses(self):
        statuses = wiki_schema.ARTICLE_BUCKETS['draft']
        self.assertEqual(self.params_for(statuses=statuses)['statuses'], list(statuses))

    def test_bucket_wins_over_single_status(self):
        params = self.params_for(status='published', statuses=('draft', 'on_approval'))
        self.assertEqual(params['statuses'], ['draft', 'on_approval'])

    def test_no_filter_means_no_status_condition(self):
        self.assertIsNone(self.params_for()['statuses'])

    def test_orphans_flag_is_off_by_default(self):
        self.assertFalse(self.params_for()['orphans'])
        self.assertTrue(self.params_for(orphans_only=True)['orphans'])

    def test_empty_perimeter_short_circuits(self):
        cursor = MagicMock()
        self.assertEqual(wiki_articles.list_articles(cursor, set()), [])
        cursor.execute.assert_not_called()


SPACES = [
    {'id': 1, 'name': 'СЗоВ', 'icon': None},
    {'id': 2, 'name': 'ОП', 'icon': None},
    # Пространство без единого доступного раздела в ответе не нужно: заголовок
    # над пустой сеткой ничего не сообщает.
    {'id': 9, 'name': 'Пустое', 'icon': None},
]

SECTIONS = [
    {'id': 3, 'space_id': 1, 'parent_section_id': None, 'name': 'Оператор',
     'icon': None, 'department_name': 'СЗоВ'},
    {'id': 4, 'space_id': 1, 'parent_section_id': 3, 'name': 'Регламенты',
     'icon': None, 'department_name': 'СЗоВ'},
    # Не в периметре — в каталог попасть не должен.
    {'id': 5, 'space_id': 2, 'parent_section_id': None, 'name': 'Чужой',
     'icon': None, 'department_name': 'ОП'},
]


def make_context():
    return {
        'user_id': 42, 'otp_role': 'admin', 'department_id': None,
        'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
        'wiki_roles': [{'id': 5, 'code': 'wiki_admin', 'can_read': True,
                        'can_create': True, 'can_edit': True, 'can_delete': True,
                        'can_publish': True, 'can_approve': True,
                        'can_manage_users': True, 'can_manage_structure': True,
                        'can_manage_access': True}],
        'access_mode': 'auto',
    }


@unittest.skipIf(Flask is None, 'flask не установлен')
class CatalogRouteTest(unittest.TestCase):
    """/catalog и /articles: один периметр, одно определение корзины."""

    def setUp(self):
        self.list_calls = []
        self.perimeter_calls = []

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        context = make_context()
        self.context = context

        def fake_perimeter(_cursor, _ctx, **kwargs):
            self.perimeter_calls.append(kwargs)
            # Разделы 3 и 4 открыты, 5 — нет.
            return collect_subjects(user_id=42, otp_role='admin'), {3, 4}, {7, 8, 9}

        def fake_list(_cursor, _visible, **kwargs):
            self.list_calls.append(kwargs)
            return []

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(self.context)),
            # Курсор здесь один на все запросы; расчёт способностей ходит в базу
            # первым и без подмены разобрал бы чужие строки как выписанные права.
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'log_action', lambda *a, **k: None),
            (wiki_perimeter, 'read_perimeter', fake_perimeter),
            (wiki_articles, 'list_articles', fake_list),
            (wiki_articles, 'catalog_counts', lambda *a, **k: {
                'sections': {3: {'published': 5, 'draft': 1, 'archived': 0},
                             4: {'published': 2, 'draft': 0, 'archived': 0}},
                'orphans': {'published': 0, 'draft': 2, 'archived': 0},
                'totals': {'published': 7, 'draft': 3, 'archived': 0},
            }),
            (wiki_structure, 'list_spaces', lambda *a, **k: [dict(s) for s in SPACES]),
            (wiki_structure, 'list_sections', lambda *a, **k: [dict(s) for s in SECTIONS]),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)
        # routes_articles держит собственные ссылки на модули — подменяем и их.
        for name, replacement in (('structure', wiki_structure),):
            self.addCleanup(setattr, routes_articles, name,
                            getattr(routes_articles, name))

        self.qr_confirmed = True

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            # Гейт QR-подтверждения стоит в общем декораторе роутов
            # (wiki/routes.py), значит действует и на каталог — см.
            # test_catalog_is_behind_the_qr_gate ниже.
            sensitive_access_granted=lambda _user_id, cursor=None: self.qr_confirmed,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    # ── /catalog ─────────────────────────────────────────────────────────
    def test_only_perimeter_sections_are_returned(self):
        data = self.client.get('/api/wiki/catalog').get_json()
        self.assertEqual([s['id'] for s in data['sections']], [3, 4])
        self.assertNotIn(5, [s['id'] for s in data['sections']],
                         'раздел вне периметра попал в каталог')

    def test_spaces_without_visible_sections_are_dropped(self):
        data = self.client.get('/api/wiki/catalog').get_json()
        self.assertEqual([sp['id'] for sp in data['spaces']], [1])

    def test_archived_sections_and_spaces_are_not_asked_for(self):
        """Архивный дубль неотличим от живого в сетке плиток."""
        seen = []
        original = wiki_structure.list_sections
        wiki_structure.list_sections = lambda *a, **k: (seen.append(k) or
                                                        [dict(s) for s in SECTIONS])
        self.addCleanup(setattr, wiki_structure, 'list_sections', original)
        self.client.get('/api/wiki/catalog')
        self.assertEqual(seen[0].get('include_archived'), False)

    def test_counts_ride_along_with_each_section(self):
        data = self.client.get('/api/wiki/catalog').get_json()
        by_id = {s['id']: s for s in data['sections']}
        self.assertEqual(by_id[3]['counts']['published'], 5)
        self.assertEqual(by_id[4]['counts']['draft'], 0)

    def test_totals_and_orphans_are_reported(self):
        data = self.client.get('/api/wiki/catalog').get_json()
        self.assertEqual(data['totals'], {'published': 7, 'draft': 3, 'archived': 0})
        self.assertEqual(data['orphans']['draft'], 2)
        self.assertEqual(data['sections_total'], 2,
                         'счётчик «Разделов» на главной берётся отсюда')

    def test_catalog_is_behind_the_qr_gate(self):
        """Каталог выкладывает названия разделов и объём каждого — это вход в
        раздел, и он обязан быть за тем же QR-подтверждением, что и всё
        остальное. Гейт живёт в общем декораторе (wiki/routes.py), поэтому
        проверяем не реализацию, а факт: неподтверждённый оператор получает 403.
        """
        self.context['otp_role'] = 'operator'
        self.qr_confirmed = False
        response = self.client.get('/api/wiki/catalog')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'SENSITIVE_ACCESS_REQUIRED')

        self.qr_confirmed = True
        self.assertEqual(self.client.get('/api/wiki/catalog').status_code, 200)

    def test_reader_gets_403(self):
        """Каталог — для редакторов. Гейт на сервере, а не только в меню:
        гард во фронте прячет вкладку, но не запрет по прямому адресу.
        """
        self.context['wiki_roles'] = [{
            'id': 9, 'code': 'wiki_reader', 'can_read': True,
            'can_create': False, 'can_edit': False, 'can_delete': False,
            'can_publish': False, 'can_approve': False,
            'can_manage_users': False, 'can_manage_structure': False,
            'can_manage_access': False,
        }]
        self.context['otp_role'] = 'operator'
        response = self.client.get('/api/wiki/catalog')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_EDITOR_ONLY')

    def test_reader_still_gets_the_article_list(self):
        """Закрыт КАТАЛОГ, а не чтение: витрина и поиск читателю нужны."""
        self.context['wiki_roles'] = [{
            'id': 9, 'code': 'wiki_reader', 'can_read': True,
            'can_create': False, 'can_edit': False, 'can_delete': False,
            'can_publish': False, 'can_approve': False,
            'can_manage_users': False, 'can_manage_structure': False,
            'can_manage_access': False,
        }]
        self.context['otp_role'] = 'operator'
        self.assertEqual(self.client.get('/api/wiki/articles').status_code, 200)

    def test_editor_without_publish_is_let_in(self):
        """Достаточно любой из трёх способностей правки, а не всех сразу."""
        self.context['wiki_roles'] = [{
            'id': 8, 'code': 'wiki_editor', 'can_read': True,
            'can_create': False, 'can_edit': True, 'can_delete': False,
            'can_publish': False, 'can_approve': False,
            'can_manage_users': False, 'can_manage_structure': False,
            'can_manage_access': False,
        }]
        self.context['otp_role'] = 'operator'
        self.assertEqual(self.client.get('/api/wiki/catalog').status_code, 200)

    def test_catalog_uses_the_personal_perimeter(self):
        """Тот же периметр, что у списка и поиска: иначе плитка солжёт."""
        self.client.get('/api/wiki/catalog')
        self.assertEqual(self.perimeter_calls[-1],
                         {'master_key': False, 'space_id': None})

    def test_catalog_narrows_to_the_chosen_space(self):
        """Переключатель в шапке доезжает до периметра, а не фильтрует ответ.

        Сужать обязан сервер: статью чужого пространства видно ещё и по
        авторству, мимо разделов, и фильтр по дереву во фронте её не отсеял бы.
        """
        self.client.get('/api/wiki/catalog?space_id=7')
        self.assertEqual(self.perimeter_calls[-1],
                         {'master_key': False, 'space_id': 7})

    def test_broken_space_id_is_ignored_not_rejected(self):
        """Мусор в адресе — как отсутствующий параметр, а не 400: витрина
        не должна отказывать в списке статей из-за опечатки в ссылке."""
        self.client.get('/api/wiki/catalog?space_id=abc')
        self.assertEqual(self.perimeter_calls[-1],
                         {'master_key': False, 'space_id': None})

    # ── /articles ────────────────────────────────────────────────────────
    def test_bucket_expands_into_its_statuses(self):
        self.client.get('/api/wiki/articles?bucket=draft')
        self.assertEqual(self.list_calls[-1]['statuses'],
                         wiki_schema.ARTICLE_BUCKETS['draft'])

    def test_unknown_bucket_does_not_narrow_anything(self):
        self.client.get('/api/wiki/articles?bucket=wat')
        self.assertIsNone(self.list_calls[-1]['statuses'])

    def test_section_none_asks_for_orphans(self):
        self.client.get('/api/wiki/articles?section_id=none')
        self.assertTrue(self.list_calls[-1]['orphans_only'])
        self.assertIsNone(self.list_calls[-1]['section_id'])

    def test_numeric_section_is_not_mistaken_for_orphans(self):
        self.client.get('/api/wiki/articles?section_id=4')
        self.assertEqual(self.list_calls[-1]['section_id'], 4)
        self.assertFalse(self.list_calls[-1]['orphans_only'])

    def test_articles_share_the_catalog_perimeter(self):
        """Плитка и список за ней обязаны считать периметр одинаково."""
        self.client.get('/api/wiki/catalog')
        self.client.get('/api/wiki/articles?section_id=3&bucket=published')
        self.assertEqual(self.perimeter_calls[-1], self.perimeter_calls[-2])


class RulesCursor:
    """Курсор для правил статей: одна заготовленная выборка и счётчик запросов."""

    def __init__(self, article_rules=()):
        self.article_rules = list(article_rules)   # [(article_id, mode, r, c, e, d, p, a)]
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(' '.join(sql.split()))

    def fetchall(self):
        return self.article_rules


class ListPermissionsTest(unittest.TestCase):
    """Права на каждую статью выдачи — для меню действий в каталоге.

    Меню «три точки» у строки статьи не имеет права предлагать действие, на
    которое сервер ответит 403: роль этого не знает — у статьи есть свои
    правила доступа. Поэтому права считаются на сервере и приезжают в списке.
    Ценой этого не должен становиться N+1: выдача бывает в 200 строк.
    """

    def setUp(self):
        self.subjects = collect_subjects(user_id=42, otp_role='sv')
        self.section_calls = []

    def ctx(self, **caps):
        base = {'can_read': True, 'can_create': True, 'can_edit': True,
                'can_delete': True, 'can_publish': True, 'can_approve': False,
                'can_manage_users': False, 'can_manage_structure': False,
                'can_manage_access': False}
        base.update(caps)
        return {'user_id': 42, 'otp_role': 'sv', 'capabilities': base}

    def section_rules(self, granted=('can_read', 'can_edit', 'can_delete')):
        rule = {name: name in granted for name in
                ('can_read', 'can_create', 'can_edit', 'can_delete',
                 'can_publish', 'can_approve')}

        def fn(_cursor, section_ids, _subjects, _user_id):
            self.section_calls.append(list(section_ids))
            return {section_id: [dict(rule)] for section_id in section_ids}

        return fn

    def article(self, article_id, **extra):
        row = {'id': article_id, 'section_ids': [3], 'visibility_mode': 'inherit',
               'strict_mode': False, 'author_id': 7, 'owner_user_id': None}
        row.update(extra)
        return row

    def test_two_queries_for_a_whole_page_of_articles(self):
        """Двести статей — те же два запроса, что и одна."""
        cursor = RulesCursor()
        rows = [self.article(i) for i in range(1, 201)]
        rights = wiki_articles.permissions_for_articles(
            cursor, self.ctx(), rows, self.subjects, {3}, self.section_rules())
        self.assertEqual(len(rights), 200)
        self.assertEqual(len(self.section_calls), 1, 'правила разделов спросили построчно')
        self.assertEqual(len(cursor.queries), 1, 'правила статей спросили построчно')

    def test_sections_are_asked_once_as_a_set(self):
        """Общая ветка не спрашивается заново на каждую статью из неё."""
        cursor = RulesCursor()
        rows = [self.article(1, section_ids=[3, 4]), self.article(2, section_ids=[4]),
                self.article(3, section_ids=[5])]
        wiki_articles.permissions_for_articles(
            cursor, self.ctx(), rows, self.subjects, {3, 4}, self.section_rules())
        # 5 в периметр не входит — за его правилами не идём вовсе.
        self.assertEqual(self.section_calls, [[3, 4]])

    def test_empty_list_asks_the_database_nothing(self):
        cursor = RulesCursor()
        self.assertEqual(
            wiki_articles.permissions_for_articles(
                cursor, self.ctx(), [], self.subjects, {3}, self.section_rules()),
            {})
        self.assertEqual(cursor.queries, [])
        self.assertEqual(self.section_calls, [])

    def test_rights_are_per_article_not_shared(self):
        """Запрет на ОДНОЙ статье не должен гасить кнопки у соседних строк."""
        cursor = RulesCursor(article_rules=[
            (2, 'deny', False, False, True, True, False, False),
        ])
        rows = [self.article(1), self.article(2), self.article(3)]
        rights = wiki_articles.permissions_for_articles(
            cursor, self.ctx(), rows, self.subjects, {3}, self.section_rules())
        self.assertTrue(rights[1]['can_edit'])
        self.assertFalse(rights[2]['can_edit'], 'запрет правилом статьи не сработал')
        self.assertFalse(rights[2]['can_delete'])
        self.assertTrue(rights[3]['can_edit'])

    def test_designated_owner_keeps_edit_without_any_rule(self):
        """Ради этого owner_user_id и выбирается списком (_LIST_KEYS).

        Владелец статьи правит её всегда. Без поля в выдаче меню скрывало бы
        «Редактировать» у владельца его же статьи — и человек решил бы, что
        доступ отобрали.
        """
        cursor = RulesCursor()
        rows = [self.article(1, owner_user_id=42)]
        rights = wiki_articles.permissions_for_articles(
            cursor, self.ctx(), rows, self.subjects, set(), self.section_rules(granted=()))
        self.assertTrue(rights[1]['can_edit'])

    def test_capability_still_gates_the_object_right(self):
        """Правило раздела даёт удаление, а способности нет — значит нельзя."""
        cursor = RulesCursor()
        rights = wiki_articles.permissions_for_articles(
            cursor, self.ctx(can_delete=False), [self.article(1)],
            self.subjects, {3}, self.section_rules())
        self.assertTrue(rights[1]['can_edit'])
        self.assertFalse(rights[1]['can_delete'])

    def test_one_article_goes_through_the_same_calculation(self):
        """effective_permissions — тот же расчёт, а не второй его экземпляр."""
        cursor = RulesCursor()
        article = self.article(1)
        one = wiki_articles.effective_permissions(
            cursor, self.ctx(), article, self.subjects, {3}, self.section_rules())
        many = wiki_articles.permissions_for_articles(
            cursor, self.ctx(), [article], self.subjects, {3}, self.section_rules())
        self.assertEqual(dict(one), dict(many[1]))


class CatalogScreenSourceTest(unittest.TestCase):
    """Решения вкладки «Статьи», которые видно только в исходнике фронта.

    Тест читает WikiCatalog.jsx текстом. Это не придирка к стилю: каждая
    проверка здесь — про ЛОЖЬ НА ЭКРАНЕ, которую сборка пропускает молча.

    Читаем исходник ДВАЖДЫ — целиком и без комментариев. Целиком нужен там, где
    проверяется видимая человеку строка; без комментариев — там, где проверяется
    сам код, иначе объяснение «раньше здесь стоял потолок в 200 записей» роняло
    бы тест, описывая ровно то, чего в коде уже нет.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / 'src' / 'components' / 'wiki' / 'WikiCatalog.jsx').read_text(
            encoding='utf-8')
        without_blocks = re.sub(r'/\*.*?\*/', '', cls.src, flags=re.S)
        cls.code = '\n'.join(
            line for line in without_blocks.splitlines()
            if not line.lstrip().startswith('//'))

    def _buckets_block(self):
        block = re.search(r'const BUCKETS = \[(.*?)\n\];', self.code, re.S)
        self.assertIsNotNone(block, 'не нашли список корзин во фронте')
        return block.group(1)

    def test_screen_opens_with_the_list_not_with_a_question(self):
        """Правая колонка больше не встречает просьбой выбрать раздел."""
        self.assertNotIn('Выберите раздел слева', self.src)

    def test_every_bucket_has_a_name_for_its_whole_contents(self):
        """У каждой корзины сервера есть подпись «вся корзина» во фронте.

        Строка возврата над деревом называется по текущей корзине. Появись на
        сервере четвёртая, а подпись к ней нет — кнопка молча обещала бы «Все
        статьи» и приводила в чужую выборку.
        """
        block = self._buckets_block()
        self.assertEqual(set(re.findall(r"key:\s*'([a-z]+)'", block)),
                         set(wiki_schema.ARTICLE_BUCKETS))
        for bucket in wiki_schema.ARTICLE_BUCKETS:
            described = re.search(
                r"key:\s*'%s'.*?emptyHere:\s*'[^']*'" % bucket, block, re.S)
            self.assertIsNotNone(described, 'нет описания корзины %s' % bucket)
            for field in ('all', 'nothing', 'emptyHere'):
                self.assertRegex(described.group(0), r"\b%s:\s*'" % field,
                                 'у корзины %s нет подписи %s' % (bucket, field))

    def test_the_list_is_taken_in_pages(self):
        """Список забирается страницами, а не одним запросом с потолком.

        Потолок ответа /articles — 200 записей, а черновиков в проде 235.
        Вернись сюда одиночный запрос с limit в потолок, и «Черновики 235»
        открывались бы двумя сотнями: счётчик и список за ним разошлись бы
        молча — ровно тот дефект, ради которого появился articleIndex.js.
        """
        self.assertIn('fetchArticleIndex', self.code)
        self.assertNotRegex(self.code, r'limit:\s*\d')

    def test_page_count_comes_from_the_catalog_not_from_total_visible(self):
        """Сколько страниц забирать, говорит каталог, а не размер периметра.

        total_visible считается ДО фильтра по корзине (см. articleIndex.js) и
        завысил бы число страниц: лишние пустые запросы на каждый заход. Точное
        число этой выборки уже пришло с каталогом — им и пользуемся.
        """
        self.assertNotIn('total_visible', self.code)
        self.assertRegex(self.code, r'totals\?\.\[bucket\]')

    def test_row_action_refreshes_whatever_list_is_on_screen(self):
        """Действие над строкой обновляет ПОКАЗАННЫЙ список, а не только раздел.

        Пока строки статей жили лишь у выбранного раздела, `if (selected)`
        перед тихим перезапросом было верным. Основная выборка экрана теперь —
        вся корзина, и в ней selected === null: с прежним условием
        заархивированная статья оставалась бы в списке, споря со счётчиком,
        который reloadCatalog обновляет всегда.
        """
        act = re.search(r'const act = .*?\n    \};', self.code, re.S)
        self.assertIsNotNone(act, 'не нашли обработчик действий над строкой')
        self.assertNotRegex(act.group(0), r'if \(selected\)\s*loadArticles')
        self.assertRegex(act.group(0), r'loadArticles\(\s*selected \? selected\.id : ALL_ID')

    def test_quiet_refresh_does_not_claim_the_race_key(self):
        """Ключ гонки забирает только открытие выборки, не тихое обновление.

        Иначе ответ по разделу, выбранному, пока летел тихий запрос, признаётся
        чужим: список остаётся прежним, а спиннер над ним не гаснет — экран
        встаёт намертво до перезагрузки страницы.
        """
        loader = re.search(r'const loadArticles = .*?\n    \}, \[', self.code, re.S)
        self.assertIsNotNone(loader, 'не нашли загрузчик списка')
        body = loader.group(0)
        self.assertRegex(body, r'if \(!quiet\) \{ wantedRef\.current = wanted;')
        # Второго, безусловного присвоения быть не должно.
        self.assertEqual(len(re.findall(r'wantedRef\.current = wanted', body)), 1)

    def test_a_dead_catalog_does_not_spin_forever(self):
        """Каталог не ответил — говорим об этом, а не крутим спиннер.

        Загрузчик ждёт каталога (из него берётся число страниц), а busy теперь
        поднят с первого кадра. Без отдельной ветки правая колонка осталась бы
        в «Загружаем…» навсегда, и выбраться оттуда можно было бы только
        случайным нажатием.
        """
        self.assertRegex(self.code, r'const catalogLost = !catalog && !loading;')
        self.assertRegex(self.code, r'\{!catalogLost && busy &&')
        self.assertIn('Список не загрузился', self.src)

    def test_the_list_is_shown_in_pages(self):
        """Строки режутся страницами общим пейджером, а не своим.

        Своя вёрстка пейджера рядом с IosPager разошлась бы с ним на первой же
        правке отступа — ровно та причина, по которой его когда-то и вынесли из
        карточки сессий в общий модуль.
        """
        self.assertIn('IosPager', self.code)
        self.assertRegex(self.code, r'const ROWS_PER_PAGE = \d+;')
        self.assertRegex(self.code, r'pageView\.rows\.map\(')

    def test_the_pager_stands_above_the_list(self):
        """Пейджер стоит выше списка, а не под ним.

        Под списком до него пришлось бы прокручивать всю страницу — ровно та
        работа, ради избавления от которой страницы и заводят. То же решение и
        по той же причине принято в карточке сессий.
        """
        pager = self.code.index('IosPager\n')
        rows = self.code.index('pageView.rows.map(')
        self.assertLess(pager, rows, 'пейджер оказался ниже списка')

    def test_paging_does_not_break_the_filter(self):
        """Фильтр отбирает по всему списку, а страница режет уже отобранное.

        Спроси страницу у сервера — и «Ничего не найдено» означало бы «нет на
        этой странице». Порядок здесь и есть гарантия: сначала shown (фильтр),
        потом окно страницы над ним.
        """
        self.assertRegex(self.code, r'pageWindow\(shown, page\)')
        # Страница сбрасывается на первую, когда меняется то, над чем она стоит.
        self.assertRegex(self.code, r'useEffect\(\(\) => \{ setPage\(1\); \}, \[filter\]\)')
        self.assertRegex(self.code, r'if \(!quiet\) \{[^}]*setPage\(1\);')

    def test_a_quiet_refresh_keeps_the_page_the_person_is_reading(self):
        """Тихое обновление НЕ отбрасывает на первую страницу.

        Человек читает седьмую страницу и архивирует с неё строку. Сбрось мы
        страницу — список прыгнул бы в начало на каждое действие над строкой.
        """
        loader = re.search(r'const loadArticles = .*?\n    \}, \[', self.code, re.S)
        self.assertIsNotNone(loader)
        self.assertEqual(len(re.findall(r'setPage\(1\)', loader.group(0))), 1)

    def test_the_full_list_is_asked_within_one_space(self):
        """Запрос списка уходит с space_id.

        Выборка «все статьи» — это отсутствие фильтра по разделу, и без
        space_id она собралась бы по всем викам сразу. На экране живёт одна —
        та же, по которой каталог посчитал числа рядом со списком.
        """
        params = re.search(r'params:\s*\{(?:[^{}]|\{[^{}]*\})*\}', self.code, re.S)
        self.assertIsNotNone(params, 'не нашли параметры запроса /articles')
        self.assertIn('space_id', params.group(0))


if __name__ == '__main__':
    unittest.main()
