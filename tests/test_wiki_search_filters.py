# -*- coding: utf-8 -*-
"""Фильтры поиска по вике: что из адреса доезжает до SQL.

Три фильтра, все необязательные: тип документа, создатель статьи и область
поиска («везде» против «только в названиях»). Здесь проверяется РАЗБОР адреса и
периметр списка авторов; сам SQL проверен на настоящем Postgres в
test_wiki_search.py::SearchFiltersTest.

Отдельный набор, а не дописка к test_wiki_browse_scope: тот отвечает на вопрос
«какой периметр запрашивает эндпоинт», этот — «во что превращаются параметры».
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
from wiki import queries  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki import search as wiki_search  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

WIKI_ADMIN_CAPS = {
    'can_read': True, 'can_create': True, 'can_edit': True, 'can_delete': True,
    'can_publish': True, 'can_approve': True, 'can_manage_users': True,
    'can_manage_structure': True, 'can_manage_access': True,
}


@unittest.skipIf(Flask is None, 'flask не установлен')
class RouteHarness(unittest.TestCase):
    """Поднятый на моках блюпринт вики. Своих проверок не несёт.

    Отдельной базой, а не наследованием одного набора от другого: наследуй
    «Авторы» набор про разбор параметров — и пятнадцать его проверок прогонялись
    бы дважды, ничего при этом не проверяя во второй раз.
    """

    def setUp(self):
        self.seen = {}
        self.visible = {7, 8, 9}

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        self.cursor = cursor

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        context = {'user_id': 42, 'otp_role': 'admin', 'department_id': None,
                   'direction_id': None, 'headed_department_ids': [],
                   'group_ids': [],
                   'wiki_roles': [dict(WIKI_ADMIN_CAPS, id=5, code='wiki_admin')],
                   'access_mode': 'auto'}
        self.patch(queries, 'load_access_context', lambda _c, _u: dict(context))
        self.patch(queries, 'allowed_section_ids',
                   lambda *a, **k: {1})
        self.patch(wiki_articles, 'visible_article_ids',
                   lambda *a, **k: set(self.visible))
        # Журнал запросов и наличие pg_trgm к разбору параметров отношения не
        # имеют, а с MagicMock-курсором дали бы шум в проверках. Аргументы
        # записи всё же запоминаем: флаг «выдача сужена» проверяется ниже.
        self.logged = {}

        def spy_log(_cursor, **kwargs):
            self.logged = dict(kwargs)
            return True

        self.patch(wiki_search, 'log_query', spy_log)
        self.patch(wiki_schema, 'trigram_available', lambda _c: False)

        def spy_search(_cursor, visible, query, **kwargs):
            self.seen = dict(kwargs, query=query, visible=set(visible))
            return []

        self.patch(wiki_search, 'search', spy_search)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)


class SearchFilterParamsTest(RouteHarness):
    """Адрес -> аргументы wiki_search.search."""

    def search(self, query_string):
        response = self.client.get('/api/wiki/search?q=аренда&' + query_string)
        self.assertEqual(response.status_code, 200, response.data)
        return self.seen

    # ── Тип документа ────────────────────────────────────────────────────
    def test_no_filters_means_none(self):
        seen = self.search('')
        self.assertIsNone(seen['article_types'])
        self.assertIsNone(seen['author_ids'])
        self.assertEqual(seen['scope'], wiki_search.MATCH_ALL)

    def test_single_type(self):
        self.assertEqual(self.search('article_type=regulation')['article_types'],
                         ['regulation'])

    def test_several_types(self):
        seen = self.search('article_type=regulation&article_type=instruction')
        self.assertEqual(seen['article_types'], ['regulation', 'instruction'])

    def test_unknown_type_is_dropped_silently(self):
        """Опечатка в адресе гасится, а не отказывает в выдаче."""
        seen = self.search('article_type=regulation&article_type=выдумка')
        self.assertEqual(seen['article_types'], ['regulation'])

    def test_only_unknown_types_mean_no_filter(self):
        self.assertIsNone(self.search('article_type=выдумка')['article_types'])

    def test_duplicate_types_collapse_but_keep_order(self):
        seen = self.search('article_type=instruction&article_type=regulation'
                           '&article_type=instruction')
        self.assertEqual(seen['article_types'], ['instruction', 'regulation'])

    # ── Создатель ────────────────────────────────────────────────────────
    def test_authors_are_parsed_as_numbers(self):
        self.assertEqual(self.search('author_id=11&author_id=22')['author_ids'],
                         [11, 22])

    def test_garbage_author_is_dropped(self):
        self.assertEqual(self.search('author_id=11&author_id=абв')['author_ids'], [11])

    def test_zero_author_is_not_a_filter(self):
        """id=0 не бывает: это «не передали», а не «автор номер ноль»."""
        self.assertIsNone(self.search('author_id=0')['author_ids'])

    def test_filter_values_are_capped(self):
        seen = self.search('&'.join('author_id=%d' % i for i in range(1, 120)))
        self.assertEqual(len(seen['author_ids']), 50)

    # ── Область поиска ───────────────────────────────────────────────────
    def test_match_title(self):
        self.assertEqual(self.search('match=title')['scope'], wiki_search.MATCH_TITLE)

    def test_match_unknown_falls_back_to_everything(self):
        self.assertEqual(self.search('match=заголовки')['scope'], wiki_search.MATCH_ALL)

    def test_perimeter_scope_is_not_the_search_scope(self):
        """?scope=all — про периметр, а не про область поиска.

        Параметры разные намеренно: ?scope=all уже означает «показать весь
        портал» (для администратора доступов), и если бы область поиска читалась
        из того же слова, одно тихо ломало бы другое.
        """
        self.assertEqual(self.search('scope=all')['scope'], wiki_search.MATCH_ALL)

    # ── Фильтры не расширяют периметр ────────────────────────────────────
    def test_filters_never_widen_the_perimeter(self):
        seen = self.search('article_type=regulation&author_id=999&match=title')
        self.assertEqual(seen['visible'], self.visible)

    # ── Журнал «искали и не нашли» ───────────────────────────────────────
    def test_plain_search_is_not_marked_as_filtered(self):
        self.search('')
        self.assertFalse(self.logged['filtered'])

    def test_every_filter_marks_the_log_row(self):
        """Ноль находок при фильтре — не дыра в базе знаний.

        Отчёт «искали и не нашли» стоит на results_count = 0 и объявлен
        единственным источником правды о том, чего в вике не написано. Запрос,
        суженный типом или создателем, вернул ноль по другой причине —
        «статья не того вида», и лечится это не написанием статьи. Не пометь мы
        такие строки — отчёт начал бы требовать писать уже написанное.
        """
        for filter_string in ('article_type=regulation', 'author_id=11', 'match=title'):
            self.search(filter_string)
            self.assertTrue(self.logged['filtered'], filter_string)

    def test_unknown_filter_values_do_not_mark_the_log_row(self):
        """Фильтр, который сервер погасил, выдачу не сужал — и метки не даёт."""
        self.search('article_type=выдумка&match=заголовки')
        self.assertFalse(self.logged['filtered'])


class SearchAuthorsRouteTest(RouteHarness):
    """Список создателей для фильтра — строго по периметру витрины."""

    def test_authors_are_counted_over_the_visible_perimeter(self):
        captured = {}

        def spy_authors(_cursor, visible):
            captured['visible'] = set(visible)
            return [{'id': 11, 'name': 'Айгуль', 'articles': 3}]

        self.patch(wiki_articles, 'authors_of', spy_authors)
        response = self.client.get('/api/wiki/search/authors')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.get_json()['items'],
                         [{'id': 11, 'name': 'Айгуль', 'articles': 3}])
        self.assertEqual(captured['visible'], self.visible)

    def test_empty_perimeter_asks_the_database_nothing(self):
        cursor = MagicMock()
        self.assertEqual(wiki_articles.authors_of(cursor, set()), [])
        cursor.execute.assert_not_called()


class SearchFiltersSourceTest(unittest.TestCase):
    """Решения фильтров, которые видно только в исходнике фронта.

    Тест читает .jsx и .js текстом. Причина та же, что у стража каталога
    (test_wiki_catalog.CatalogScreenSourceTest): каждая проверка здесь — про
    молчаливый отказ, который сборка пропускает. Фильтр, не доехавший до
    запроса, не падает — он просто ничего не фильтрует, и человек об этом
    не узнает.

    Исходник читается ДВАЖДЫ: целиком (там, где проверяется видимая строка) и
    без комментариев (там, где проверяется код) — иначе объяснение «раньше
    здесь стоял ?scope=» роняло бы тест, описывая то, чего в коде уже нет.
    """

    FRONT = ROOT / 'src' / 'components' / 'wiki'

    @classmethod
    def read(cls, name):
        return (cls.FRONT / name).read_text(encoding='utf-8')

    @classmethod
    def strip_comments(cls, text):
        without_blocks = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        return '\n'.join(line for line in without_blocks.splitlines()
                          if not line.lstrip().startswith('//'))

    @classmethod
    def setUpClass(cls):
        cls.helper = cls.read('searchFilters.js')
        cls.helper_code = cls.strip_comments(cls.helper)
        cls.panel = cls.read('WikiSearchFilters.jsx')
        cls.panel_code = cls.strip_comments(cls.panel)
        cls.header = cls.read('WikiSearch.jsx')
        cls.header_code = cls.strip_comments(cls.header)
        cls.library = cls.read('WikiLibrary.jsx')
        cls.library_code = cls.strip_comments(cls.library)

    # ── Оба поиска ───────────────────────────────────────────────────────
    def test_both_search_screens_send_the_filters(self):
        """Поисков в разделе два, и фильтры обязаны доехать из обоих.

        Поле в шапке (WikiSearch) и витрина «Все статьи вики» (WikiLibrary)
        ходят в /search независимо. Поправь одно — фильтр работал бы в шапке и
        молча не работал на витрине, а выглядело бы это как «иногда не
        фильтрует».
        """
        for name, code in (('WikiSearch.jsx', self.header_code),
                           ('WikiLibrary.jsx', self.library_code)):
            self.assertIn('searchParams(term, filters', code,
                          '%s не собирает параметры фильтров' % name)
            self.assertNotRegex(code, r"params:\s*\{\s*q:\s*term",
                                '%s всё ещё шлёт голый запрос без фильтров' % name)

    def test_filters_are_in_the_effect_dependencies(self):
        """Смена фильтра обязана перезапросить выдачу.

        И именно ключом, а не самим объектом: объект фильтров пересобирается на
        каждый рендер родителя, и в зависимостях эффекта он гонял бы поиск по
        кругу (та же грабля, что с нестабильным showToast).
        """
        for name, code in (('WikiSearch.jsx', self.header_code),
                           ('WikiLibrary.jsx', self.library_code)):
            self.assertIn('filtersKey(filters)', code,
                          '%s не перезапрашивает выдачу при смене фильтра' % name)

    # ── Область поиска ───────────────────────────────────────────────────
    def test_scope_goes_in_match_not_in_scope(self):
        """?scope=all уже занят переключателем периметра.

        Уедь область поиска в то же слово — «показать весь портал» и «искать
        только в названиях» тихо ломали бы друг друга.
        """
        self.assertIn("params.set('match'", self.helper_code)
        self.assertNotIn("params.set('scope'", self.helper_code)

    def test_scope_values_match_the_server(self):
        values = set(re.findall(r"export const MATCH_(?:ALL|TITLE) = '([a-z]+)'",
                                self.helper))
        self.assertEqual(values, set(wiki_search.MATCH_SCOPES))

    # ── Тип документа ────────────────────────────────────────────────────
    def test_filterable_types_are_known_to_the_server(self):
        """Список типов во фронте — подмножество серверного.

        Появись во фронте тип, которого нет в ARTICLE_TYPES, сервер погасил бы
        его в None (правило _article_types) — и галочка в панели просто ничего
        бы не делала.
        """
        types = self.read('articleTypes.js')
        block = re.search(r'export const ARTICLE_TYPES = \[(.*?)\n\];', types, re.S)
        self.assertIsNotNone(block, 'не нашли список типов во фронте')
        front = set(re.findall(r"value:\s*'([a-z_]+)'", block.group(1)))
        self.assertTrue(front <= set(wiki_schema.ARTICLE_TYPES),
                        'во фронте есть типы, которых сервер не знает: %s'
                        % (front - set(wiki_schema.ARTICLE_TYPES)))

    def test_type_options_come_from_the_shared_list(self):
        """Панель не заводит своей копии списка типов."""
        self.assertIn('FILTERABLE_TYPES', self.helper_code)
        self.assertIn('TYPE_OPTIONS', self.panel_code)
        self.assertNotIn("'regulation'", self.panel_code)

    def test_type_labels_name_a_set_not_a_single_document(self):
        """«Регламенты», а не «Регламент»: фильтр называет НАБОР статей."""
        self.assertIn('typePlural', self.helper_code)

    # ── Слои поверх поиска ───────────────────────────────────────────────
    def test_click_inside_the_panel_does_not_close_the_search(self):
        """Панель фильтров лежит в портале body, то есть ВНЕ выпадашки поиска.

        Без этой проверки первый же щелчок по фильтру считался бы щелчком мимо:
        выдача схлопывалась бы вместе с панелью, а выбор терялся.
        """
        self.assertIn('isInsideSearchFilters', self.header_code)
        self.assertIn('data-wiki-search-filters', self.panel_code)

    def test_escape_serves_the_top_layer_first(self):
        """Escape при открытой панели гасит панель, а не поиск целиком."""
        self.assertGreaterEqual(self.header_code.count('hasOpenFilterLayer()'), 2,
                                'Escape поля и Escape окна должны спрашивать оба')

    def test_filters_are_not_a_keyboard_row(self):
        """Кнопка фильтров не входит в список строк выдачи.

        Стрелки и Enter принадлежат выдаче: попади фильтр в searchRows — Enter
        «открывал» бы его вместо статьи, а pickRow упал бы на row.item.
        """
        rows = re.search(r'export const searchRows = \((.*?)\n\};',
                         self.header_code, re.S)
        self.assertIsNotNone(rows)
        self.assertNotIn('filter', rows.group(1))

    def test_filter_row_stands_above_the_scroller(self):
        """Строка фильтров — над прокруткой выдачи, а не внутри неё.

        Уехав вместе со списком, кнопка исчезала бы ровно тогда, когда до неё
        дошла очередь, — на длинной выдаче.
        """
        self.assertLess(self.header_code.index('{filtersSlot}'),
                        self.header_code.index('ref={listRef}'))

    def test_results_pane_prop_has_a_default(self):
        """Новый проп ResultsPane не обязателен: его рендерят и тестом."""
        self.assertIn('filtersSlot = null', self.header_code)

    # ── Состояние фильтров ───────────────────────────────────────────────
    def test_header_search_resets_filters_when_it_closes(self):
        """Поиск в шапке начинает следующий заход с чистого листа.

        Переживи фильтры закрытие — человек вернулся бы через час, набрал слово
        и не нашёл статью, которая есть. Это тот самый молчаливый отказ, который
        в вике ловили уже не раз; на витрине («Все статьи вики») фильтры живут
        дольше, но там они и видны на экране постоянно.
        """
        reset = re.search(r'if \(active\) return;(.*?)\}, \[active\]\);',
                          self.header_code, re.S)
        self.assertIsNotNone(reset, 'не нашли сброс при закрытии поиска')
        self.assertIn('setFilters(EMPTY_FILTERS)', reset.group(1))

    def test_filter_row_appears_together_with_the_results(self):
        """Фильтры показываются с двух символов, как и сама выдача.

        На телефоне пане рисуется всегда, и без этого условия полоса фильтров
        мигала бы над подсказкой «введите минимум два символа» — то есть
        предлагала бы сузить то, чего ещё нет.
        """
        self.assertIn('term.length >= 2 ? (', self.header_code)
        self.assertIn('{searching && (', self.library_code)

    def test_authors_are_dropped_when_the_space_changes(self):
        """У соседней вики свой периметр и свои люди.

        Не обнули мы список — фильтр предлагал бы создателей прошлого
        пространства, и поиск по ним всегда возвращал бы пусто.
        """
        for name, code in (('WikiSearch.jsx', self.header_code),
                           ('WikiLibrary.jsx', self.library_code)):
            self.assertIn('setAuthors([]); }, [spaceId]', code,
                          '%s не сбрасывает создателей при смене пространства' % name)

    def test_authors_are_loaded_on_demand(self):
        """Список создателей стоит обхода периметра — грузим при раскрытии панели."""
        for name, code in (('WikiSearch.jsx', self.header_code),
                           ('WikiLibrary.jsx', self.library_code)):
            self.assertIn('onNeedAuthors={loadAuthors}', code, name)
        self.assertIn('onNeedAuthors', self.panel_code)

    def test_open_panel_shrinks_the_result_list(self):
        """Раскрытая панель не выталкивает выдачу за нижний край экрана.

        Внешнего потолка по высоте у выпадашки нет — maxHeight отдан только
        внутреннему скроллеру. Панель прибавляется сверху, поэтому список
        обязан сжаться, а не блок вырасти.
        """
        self.assertIn("filtersOpen ? '34vh' : '60vh'", self.header_code)

    def test_the_filter_click_does_not_wait_for_the_typing_debounce(self):
        """Щелчок по чипу не залипает на четверть секунды.

        Дебаунс — про набор текста: запрос тот же, а эффект перезапустился —
        значит сработал фильтр.
        """
        self.assertIn('sameTerm ? 0 : 250', self.header_code)

    def test_the_counter_does_not_lie_at_the_ceiling(self):
        """«Найдено: 20» при сорока подходящих статьях — ложь на экране."""
        self.assertIn('SEARCH_LIMIT', self.library_code)
        self.assertIn("found.length >= SEARCH_LIMIT ? '+' : ''", self.library_code)

    # ── Подписи ──────────────────────────────────────────────────────────
    def test_the_migration_caveat_is_on_screen(self):
        """Перенос из старой вики пишет создателем того, кто переносил.

        На нём висит почти вся база. Без этой оговорки человек выберет его,
        получит почти всю вику и решит, что фильтр сломан.
        """
        self.assertIn('перенесённых из старой вики', self.panel)

    def test_the_author_list_shows_how_many_articles_each_has(self):
        """Число рядом с именем объясняет порядок списка и цену выбора."""
        self.assertIn('author.articles', self.panel_code)

    def test_the_person_is_called_a_creator_not_an_author(self):
        """«Создатель», а не «Автор».

        В «Аналитике» авторами названы те, кто ПРАВИЛ статьи (analytics.py
        считает их по wiki_article_versions.editor_id) — это сознательно другая
        величина. Одно слово на две разные цифры в одном разделе читалось бы
        как ошибка в одной из них.
        """
        self.assertIn('Создатель', self.panel)
        self.assertNotRegex(self.panel_code, r'>\s*Автор')


if __name__ == '__main__':
    unittest.main()
