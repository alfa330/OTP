# -*- coding: utf-8 -*-
"""Поиск: порт словаря алиасов и поведение SQL-запроса.

Первая часть — чистый Python: транслитерация, раскладка, алиасы. Это ровно то,
что составляло всю «умность» поиска исходной вики: синонимов Meilisearch,
стоп-слов и кастомных правил ранжирования там НЕ настроено, проверено по
дереву конфигов. Значит при смене движка терять нечего, но перенести словарь
надо точно.

Вторая часть — сам поисковый SQL на настоящем PostgreSQL. Приём тот же, что в
тесте видимости: CTE перекрывает одноимённую таблицу, поэтому боевой текст
запроса исполняется над синтетическими строками через read-only соединение.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import prod_db
from wiki import search as wiki_search
from wiki.text import (
    ALIAS_GROUPS,
    aliases_for_text,
    fix_keyboard_layout,
    normalize_text,
    query_variants,
    search_aliases_for_article,
    transliterate_cyrillic_to_latin,
    transliterate_latin_to_cyrillic,
)

ROOT = Path(__file__).resolve().parents[1]


class NormalizeTest(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertEqual(normalize_text('  Аренда, ТРАНСПОРТА!  '), 'аренда транспорта')

    def test_yo_becomes_cyrillic_e(self):
        """ё сворачивается в КИРИЛЛИЧЕСКУЮ е. В оригинале была латинская e,
        из-за чего нормализованные «хёндай» и «хендай» не совпадали строково
        (спасала только транслитерация), а нормализованный вариант запроса
        превращался в смесь алфавитов и не стеммился как русский."""
        self.assertEqual(normalize_text('хёндай'), 'хендай')
        self.assertEqual(normalize_text('хёндай'), normalize_text('хендай'))

    def test_kazakh_letters_survive(self):
        self.assertEqual(normalize_text('Қарағанды'), 'қарағанды')

    def test_empty(self):
        self.assertEqual(normalize_text(''), '')
        self.assertEqual(normalize_text(None), '')


class TransliterationTest(unittest.TestCase):
    def test_cyrillic_to_latin(self):
        self.assertEqual(transliterate_cyrillic_to_latin('щука'), 'shchuka')
        # ж->zh, ё->e, л->l, т->t, ы->y, й->y
        self.assertEqual(transliterate_cyrillic_to_latin('жёлтый'), 'zheltyy')

    def test_latin_to_cyrillic_longest_first(self):
        """Порядок замен важен: 'sh' не должен съесть часть 'shch'."""
        self.assertEqual(transliterate_latin_to_cyrillic('shchuka'), 'щука')

    def test_keyboard_layout(self):
        self.assertEqual(fix_keyboard_layout('ntrcn'), 'текст')
        self.assertEqual(fix_keyboard_layout('fhtylf'), 'аренда')


class AliasTest(unittest.TestCase):
    def test_dictionary_size(self):
        """Фиксируем реальный объём словаря: 80 групп, а не 110,
        как утверждалось в первичном исследовании."""
        self.assertEqual(len(ALIAS_GROUPS), 80)

    def test_misspelled_car_brand(self):
        """Главный сценарий: «хундай» обязан находить Hyundai."""
        self.assertIn('hyundai', aliases_for_text('хундай'))
        self.assertIn('hyundai', aliases_for_text('хёндай'))
        self.assertIn('хендай', aliases_for_text('hyundai'))

    def test_model_names(self):
        self.assertIn('camry', aliases_for_text('камри'))
        self.assertIn('солярис', aliases_for_text('solaris'))

    def test_short_words_ignored(self):
        self.assertEqual(aliases_for_text('а'), [])

    def test_no_alias_for_unknown(self):
        self.assertEqual(aliases_for_text('невероятнаястатья'), [])

    def test_compound_alias_matches_only_as_whole_phrase(self):
        self.assertIn('рендер', aliases_for_text('Документация render.com'))
        self.assertIn('github', aliases_for_text('Войти через git hub'))
        self.assertIn('wifi', aliases_for_text('Настроить wi-fi'))
        self.assertNotIn('рендер', aliases_for_text('Ссылка https://example.com'))
        self.assertEqual(aliases_for_text('com'), [])

    def test_every_group_is_symmetric(self):
        """Любое написание группы обязано находить все остальные."""
        for group in ALIAS_GROUPS:
            for word in group:
                found = set(aliases_for_text(word))
                expected = {w for w in group if w != word}
                self.assertTrue(expected <= found,
                                'группа %r: из %r не нашлись %r' % (group, word, expected - found))


class KazakhSnippetTest(unittest.TestCase):
    """Отрывок для статьи, написанной по-казахски, обязан быть непустым.

    Замер на проде: по запросу «7 казына» статья «Все акции» (в ней «7 Қазына»)
    находилась, но отрывка не получала — ts_headline искал по оригиналу. В выдаче
    оставался голый заголовок, и это читается как «не нашлось».
    """

    def test_query_has_a_folded_fallback(self):
        self.assertIn('snippet_folded', wiki_search._SEARCH_SQL)
        self.assertIn('snippet_folded', wiki_search._KEYS)

    def test_original_snippet_is_preferred(self):
        """Свёрнутый отрывок — только запас: у русских статей превью дословное."""
        source = wiki_search.__file__
        with open(source, encoding='utf-8') as handle:
            body = handle.read()
        primary = body.index("split_snippet(item['snippet'])")
        fallback = body.index("split_snippet(item.get('snippet_folded'))")
        self.assertLess(primary, fallback, 'запас обязан идти ПОСЛЕ основного')

    def test_service_key_is_not_leaked_to_client(self):
        """snippet_folded — внутреннее поле, наружу уходить не должно."""
        self.assertIn("item.pop('snippet_folded', None)",
                      open(wiki_search.__file__, encoding='utf-8').read())


class JsAliasSyncTest(unittest.TestCase):
    """Клиентский словарь (searchText.js) обязан совпадать с серверным.

    Матчинг машины в поисковой строке идёт на клиенте по этому же словарю;
    разъедутся — «мерс» найдёт статью, но не покажет бар классификатора.
    Тот же приём, что в тесте scrollContainer: читаем исходник как текст.
    """

    JS_PATH = ROOT / 'src' / 'components' / 'wiki' / 'searchText.js'

    def _js_groups(self):
        import re
        source = self.JS_PATH.read_text(encoding='utf-8')
        start = source.index('export const ALIAS_GROUPS = [')
        end = source.index('];', start)
        block = source[start:end]
        groups = []
        for line_group in re.findall(r'\[([^\[\]]+)\]', block):
            words = re.findall(r"'([^']+)'", line_group)
            if words:
                groups.append(words)
        return groups

    def test_alias_groups_match_python(self):
        self.assertEqual(self._js_groups(), ALIAS_GROUPS)

    # Запросы, на которых расходились бы карты транслита, раскладки и порядок
    # вариантов. Текстовое сравнение исходников здесь не помогает: регулярка
    # ломается на записях с пустым значением ('ъ': '') и на ключе-апострофе
    # ("'": 'э'), а поведение важнее данных.
    CORPUS = ['камри', 'rfvhb', 'хундай солярис', 'BYD', 'vw', 'kia',
              "Cee'd", 'wi-fi', 'вай фай', 'render.com', 'Қарағанды',
              'ёж', 'node.js', 'ntrcn', 'fhtylf', 'Аренда транспорта', '']

    def test_js_behaviour_matches_python(self):
        """queryVariants в браузере обязан давать ровно то же, что на сервере.

        Совпадать должны и состав, и ПОРЯДОК: сервер сливает варианты, а клиент
        по ним же ищет машину — разъедутся, и статью найдёт, а бар
        классификатора не откроется.
        """
        node = shutil.which('node')
        if not node:
            self.skipTest('node недоступен')
        script = (
            'import { queryVariants } from %s;'
            'process.stdout.write(JSON.stringify(%s.map(queryVariants)));'
            % (json.dumps(self.JS_PATH.as_uri()), json.dumps(self.CORPUS))
        )
        out = subprocess.run([node, '--input-type=module', '-e', script],
                             capture_output=True, check=True)
        self.assertEqual(json.loads(out.stdout.decode('utf-8')),
                         [query_variants(q) for q in self.CORPUS])
    def test_original_first(self):
        self.assertEqual(query_variants('Аренда')[0], 'Аренда')

    def test_layout_variant_present(self):
        self.assertIn('текст', query_variants('ntrcn'))

    def test_alias_variant_present(self):
        self.assertIn('hyundai', query_variants('хундай'))

    def test_no_duplicates(self):
        variants = query_variants('камри')
        self.assertEqual(len(variants), len(set(variants)))

    def test_empty_query(self):
        self.assertEqual(query_variants(''), [])


class ArticleAliasesTest(unittest.TestCase):
    def test_includes_transliteration_and_synonyms(self):
        value = search_aliases_for_article('Аренда Hyundai', 'Условия', ['транспорт'])
        self.assertIn('hyundai', value)
        self.assertIn('хендай', value, 'синонимы должны попасть в поле поиска')

    def test_no_body_text(self):
        """Тело статьи целиком в алиасы не идёт: в проде три статьи весят
        по 200-900 КБ из-за картинок, раздувать индекс незачем."""
        value = search_aliases_for_article('Заголовок', '', [], 'я' * 50000)
        self.assertLess(len(value), 3000)


class SearchSqlTest(unittest.TestCase):
    """Боевой SQL поиска на синтетических статьях."""

    # Выражение обязано совпадать с генерируемой колонкой из wiki/schema.py —
    # включая свёртку ё, иначе тест проверяет не тот индекс, что на проде.
    STUB = """
WITH wiki_articles AS (
    SELECT id::int, slug::text, title::text, summary::text, status::text,
           views::int, NULL::timestamp AS updated_at,
           content_plain::text, search_aliases::text,
           setweight(to_tsvector('russian', translate(coalesce(title, ''), 'ёЁ', 'еЕ')),          'A') ||
           setweight(to_tsvector('russian', translate(coalesce(search_aliases, ''), 'ёЁ', 'еЕ')), 'B') ||
           setweight(to_tsvector('russian', translate(coalesce(summary, ''), 'ёЁ', 'еЕ')),        'C') ||
           setweight(to_tsvector('russian', translate(coalesce(content_plain, ''), 'ёЁ', 'еЕ')),  'D') AS search_vector
      FROM (VALUES {rows}) AS t(
        id, slug, title, summary, status, views, content_plain, search_aliases)
),
wiki_article_sections AS (
    SELECT article_id::int, section_id::int
      FROM (VALUES {sections}) AS t(article_id, section_id)
),
"""

    has_trigram = False

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()
        cur = cls.conn.cursor()
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        cls.has_trigram = cur.fetchone() is not None
        cur.close()
        prod_db.rollback()

    def run_search(self, rows, query, *, sections=None, with_trigram=False,
                   section_id=None, ids=(1, 2, 3, 4, 5)):
        """Боевой SQL целиком, только wiki_articles подменена синтетикой.

        Параметры собираются ровно как в wiki_search._run, включая слияние всех
        написаний запроса — иначе тест проверял бы не тот запрос, что уходит
        в прод.
        """
        stub = self.STUB.format(
            rows=', '.join(rows),
            sections=', '.join(sections) if sections
            else '(NULL::int, NULL::int)',
        )
        sql = (wiki_search.build_sql(with_trigram)
               .replace('WITH q AS (', stub + 'q AS (', 1))
        variants = [v for v in query_variants(query) if len(v) >= 2]
        cur = self.conn.cursor()
        try:
            cur.execute(sql, {
                'ids': list(ids),
                'variants': variants,
                'prefixes': [wiki_search.prefix_tsquery(v) for v in variants],
                'looses': [wiki_search.prefix_tsquery(v, ' | ') for v in variants],
                'section': section_id,
                'limit': 10,
            })
            return wiki_search._rows_to_items(cur)
        finally:
            prod_db.rollback()
            cur.close()

    ARTICLES = [
        "(1, 'arenda', 'Аренда транспорта', 'Условия аренды машины', 'published', 10,"
        " 'Здесь описаны условия аренды автомобиля в парке', 'arenda transporta')",
        "(2, 'bally', 'Баллы приоритета', 'Как начисляются баллы', 'published', 5,"
        " 'Баллы приоритета влияют на распределение заказов', 'bally prioriteta')",
        "(3, 'hyundai', 'Обслуживание Hyundai', 'Сервис', 'published', 1,"
        " 'Регламент обслуживания', 'hyundai хендай хёндай хундай')",
        "(4, 'vozvraty', 'Инструкция по возвратам', 'Порядок возврата средств', 'published', 3,"
        " 'Возврат оформляется через приложение', 'instruktsiya po vozvratam')",
        "(5, 'otchyot', 'Сводка за смену', 'Ежедневная сводка', 'published', 2,"
        " 'Ежедневный отчёт по сменам сдаётся до полуночи', 'svodka za smenu')",
    ]

    def test_finds_by_title(self):
        found = self.run_search(self.ARTICLES, 'аренда')
        self.assertEqual([a['id'] for a in found], [1])

    def test_finds_by_body(self):
        found = self.run_search(self.ARTICLES, 'заказов')
        self.assertEqual([a['id'] for a in found], [2])

    def test_title_ranks_above_body(self):
        """Вес A у заголовка обязан перебивать вес D у текста."""
        found = self.run_search(self.ARTICLES, 'баллы')
        self.assertEqual(found[0]['id'], 2)

    def test_snippet_is_highlighted(self):
        found = self.run_search(self.ARTICLES, 'аренды')
        self.assertTrue(found)
        self.assertIn('<mark>', found[0]['snippet'])

    def test_alias_field_is_searchable(self):
        """«хундай» лежит в search_aliases статьи — и находит её."""
        found = self.run_search(self.ARTICLES, 'хундай')
        self.assertEqual([a['id'] for a in found], [3])

    def test_section_filter(self):
        found = self.run_search(self.ARTICLES, 'аренда',
                                sections=['(1, 7)'], section_id=7)
        self.assertEqual([a['id'] for a in found], [1])

        nothing = self.run_search(self.ARTICLES, 'аренда',
                                  sections=['(1, 7)'], section_id=9)
        self.assertEqual(nothing, [])

    def test_perimeter_is_respected(self):
        """Выдача обязана пересекаться с множеством видимых статей."""
        found = self.run_search(self.ARTICLES, 'аренда', ids=[2])
        self.assertEqual(found, [], 'статья вне периметра не должна попасть в выдачу')

    def test_nothing_found(self):
        self.assertEqual(self.run_search(self.ARTICLES, 'бетономешалка'), [])

    def test_prefix_finds_partially_typed_word(self):
        """Поиск по мере ввода: «инструк» короче стема «инструкц», без ':*'
        полнотекст его не находил — ровно жалоба «не находит то, что есть»."""
        found = self.run_search(self.ARTICLES, 'инструк')
        self.assertIn(4, [a['id'] for a in found])

    def test_prefix_multiword(self):
        found = self.run_search(self.ARTICLES, 'инструкция по возв')
        self.assertEqual([a['id'] for a in found], [4])

    def test_yo_in_body_found_by_e_query(self):
        """«отчет» обязан находить «отчёт» в теле: конфигурация 'russian'
        сама ё/е не склеивает, склейка — наша, с обеих сторон."""
        found = self.run_search(self.ARTICLES, 'отчет')
        self.assertIn(5, [a['id'] for a in found])

    def test_yo_query_finds_yo_body(self):
        found = self.run_search(self.ARTICLES, 'отчёт')
        self.assertIn(5, [a['id'] for a in found])

    def test_trigram_variant_parses(self):
        """Форма запроса с триграммами обязана быть валидной, даже если
        расширение ещё не установлено — тогда её просто не используют."""
        if not self.has_trigram:
            self.skipTest('pg_trgm ещё не установлен в этой базе')
        found = self.run_search(self.ARTICLES, 'аренда', with_trigram=True)
        self.assertTrue(found)

    def test_trigram_typo_word_similarity(self):
        """Опечатка внутри слова: сходство считается с лучшим словом
        заголовка (word_similarity), а не с целой строкой."""
        if not self.has_trigram:
            self.skipTest('pg_trgm ещё не установлен в этой базе')
        found = self.run_search(self.ARTICLES, 'инстукция', with_trigram=True)
        self.assertIn(4, [a['id'] for a in found])


class SearchMergeTest(SearchSqlTest):
    """Слияние вариантов, ступени и деградация многословного запроса.

    Наследуется от SearchSqlTest ради общего соединения и run_search: класс
    добавляет только собственный набор статей и проверки того, что чинилось.
    """

    # Отдельный набор, чтобы не трогать проверки базового класса.
    ROWS = [
        "(1, 'hyundai', 'Обслуживание Hyundai', 'Сервис', 'published', 9,"
        " 'Регламент обслуживания', 'hyundai хендай хёндай хундай')",
        "(2, 'solaris', 'Solaris в парке', 'Условия', 'published', 1,"
        " 'Про модель', 'solaris солярис')",
        "(3, 'srez', 'Положение о проведении ежемесячного среза знаний', 'Порядок',"
        " 'published', 4, 'Срез проводится ежемесячно', 'polozhenie')",
        "(4, 'zakazy', 'Цепочка заказов', 'Как включить', 'published', 2,"
        " 'Баллы приоритета влияют на распределение заказов', 'tsepochka')",
        "(5, 'uchet', 'Учёт рабочего времени', 'Табель', 'published', 3,"
        " 'Табель заполняется ежедневно', 'uchet')",
        # Написание «хундай» есть только в алиасах: ни в заголовке, ни в
        # описании, ни в теле его нет — значит подсвечивать нечего.
        "(6, 'reglament', 'Сервисный регламент', 'Плановые работы', 'published', 1,"
        " 'Порядок планового обслуживания', 'хундай хендай')",
    ]

    def run_search(self, rows, query, **kwargs):
        kwargs.setdefault('ids', (1, 2, 3, 4, 5, 6))
        return super(SearchMergeTest, self).run_search(rows, query, **kwargs)

    def ids(self, found):
        return [item['id'] for item in found]

    def test_variants_are_merged_not_short_circuited(self):
        """«hyundai solaris» обязан отдать ОБЕ статьи.

        Раньше выигрывал первый вариант, давший строки: выдача варианта
        «хендай» возвращалась целиком, а «solaris» не пробовался никогда.
        """
        found = self.ids(self.run_search(self.ROWS, 'hyundai solaris', with_trigram=True))
        self.assertIn(1, found)
        self.assertIn(2, found)

    def test_exact_title_outranks_frequent_body(self):
        """Ступень важнее суммы рангов: заголовок бьёт частоту в теле."""
        rows = [
            "(1, 'a', 'Заказы', 'Коротко', 'published', 0, 'Ничего', 'zakazy')",
            "(2, 'b', 'Прочее', 'Разное', 'published', 99,"
            " '%s', 'prochee')" % ('заказ ' * 60),
        ]
        found = self.ids(self.run_search(rows, 'заказы', with_trigram=True, ids=[1, 2]))
        self.assertEqual(found[0], 1, 'точный заголовок обязан быть первым')

    def test_multiword_degrades_instead_of_empty(self):
        """Лишнее/опечатанное слово больше не обнуляет выдачу.

        websearch_to_tsquery склеивает слова через AND; у Meilisearch на этот
        случай matchingStrategy='last'. Наш эквивалент — OR-овый tsquery
        последней ступенью.
        """
        found = self.ids(self.run_search(self.ROWS, 'срез занний', with_trigram=True))
        self.assertEqual(found[0], 3)

    def test_yo_folded_on_article_side_for_trigrams(self):
        """«учот» -> «Учёт рабочего времени»: ё сворачивается и у статьи.

        Полнотекстовая ветка сворачивает ё сама, а триграммная сравнивала
        запрос со НЕсвёрнутым заголовком, и порог 0.45 не брался.
        """
        found = self.ids(self.run_search(self.ROWS, 'учот рабочего', with_trigram=True))
        self.assertIn(5, found)

    def test_typo_is_covered_by_aliases_not_by_body_scan(self):
        """Опечатка ловится алиасами: в них лежат заголовок, описание и теги.

        Отдельного триграммного слоя по ТЕЛУ статьи нет намеренно — замер на
        боевой базе показал 13 попаданий из 15 уже на этом слое, а добор по
        телу давал в основном шум (см. wiki/search.py).
        """
        found = self.ids(self.run_search(self.ROWS, 'полжение о срезе',
                                         with_trigram=True))
        self.assertIn(3, found)

    def test_highlights_are_marked_fragments_only(self):
        found = self.run_search(self.ROWS, 'срез', with_trigram=True)
        self.assertTrue(found)
        self.assertTrue(found[0]['highlights'])
        for fragment in found[0]['highlights']:
            self.assertIn('<mark>', fragment)
        self.assertEqual(found[0]['snippet'], found[0]['highlights'][0])

    def test_no_bogus_snippet_when_match_is_alias_only(self):
        """Совпало только в алиасах — сниппета нет, а не кусок чужого текста.

        Раньше ts_headline на промахе отдавал НАЧАЛО статьи, фронт рисовал его
        как найденный фрагмент, и слово подсветки не совпадало с показанным.
        """
        found = self.run_search(self.ROWS, 'хундай', with_trigram=True,
                                ids=[6])
        self.assertTrue(found)
        self.assertEqual(found[0]['id'], 6)
        self.assertEqual(found[0]['snippet'], '')
        self.assertEqual(found[0]['highlights'], [])

    def test_long_query_is_capped(self):
        self.assertEqual(len(('а' * 500)[:wiki_search.MAX_QUERY_CHARS]),
                         wiki_search.MAX_QUERY_CHARS)


class PrefixTsqueryTest(unittest.TestCase):
    def test_words_get_prefix_marker(self):
        self.assertEqual(wiki_search.prefix_tsquery('Инструкция по возв'),
                         'инструкция:* & по:* & возв:*')

    def test_single_letters_dropped(self):
        self.assertEqual(wiki_search.prefix_tsquery('а б в'), '')

    def test_tsquery_operators_stripped(self):
        """Слова собираются только из букв и цифр — операторы tsquery
        (&, |, !, :, скобки, кавычки) не способны сломать запрос."""
        self.assertEqual(wiki_search.prefix_tsquery("ар!ен(да) & 'смз:'"),
                         'ар:* & ен:* & да:* & смз:*')

    def test_yo_folded(self):
        self.assertEqual(wiki_search.prefix_tsquery('отчёт'), 'отчет:*')

    def test_empty(self):
        self.assertEqual(wiki_search.prefix_tsquery(''), '')
        self.assertEqual(wiki_search.prefix_tsquery(None), '')


if __name__ == '__main__':
    unittest.main()
