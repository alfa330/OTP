# -*- coding: utf-8 -*-
"""Импорт статей базы знаний Яндекс Про в вику и живая сверка с источником.

Что здесь проверяется и почему именно это — по пункту на каждое требование
постановки (задача #248, Элекова Арайлым) и по пункту на каждую ловушку
источника, найденную на живых страницах.

1. ПЕРЕНОСИТСЯ ВСЯ СТАТЬЯ, А НЕ ЕЁ ВИДИМАЯ ЧАСТЬ. Текст свёрнутых блоков в
   отрендеренном HTML отсутствует, и разбор обязан идти по __NEXT_DATA__.
   Тест требует, чтобы содержимое раскрывашки оказалось в теле статьи.

2. КАРТИНКИ ПЕРЕЕЗЖАЮТ К НАМ И В WebP. Чужой адрес в теле означает кадр на
   чужом хосте: он не привяжется к статье и однажды пропадёт. Тест требует,
   чтобы в теле не осталось ни одного адреса источника, а в дверь бакета
   уехал НЕПУСТОЙ content_type — пустой отменяет перевод в WebP молча.

3. ПОВТОРНЫЙ ПРОГОН НЕ ПЛОДИТ СТАТЬИ. Ключ — канонический адрес страницы:
   ни название, ни слаг для этого не годятся.

4. НЕИЗМЕНИВШИЙСЯ ИСТОЧНИК НЕ ТРОГАЕТ СТАТЬЮ ВОВСЕ. Любой UPDATE тела кладёт
   редакцию в историю версий, и ночная сверка «на всякий случай» засорила бы
   её тридцатью пустыми редакциями в месяц.

5. АВТОМАТИКА НЕ ЗАТИРАЕТ РУЧНЫЕ ПРАВКИ. Источник изменился, а статью правил
   человек — сверка обязана остановиться и позвать человека, а не переписать
   текст. Затёртую правку не видно никому.

6. ЛОВУШКИ ИСТОЧНИКА. Перекрывающиеся компоненты (текст [1] лежит внутри [0]),
   три написания признака «скрыт» (is_hide / isHide / is_hidden), аудитория
   компонента по городам (в JSON страницы Алматы лежат куски для других
   городов), таблица на тысячи строк, видео, которому в вике нет места.

7. НУМЕРАЦИЯ, РАЗОРВАННАЯ КАРТИНКОЙ. Источник разрывает перечень шагов
   картинкой и продолжает его <ol start="2">. Санитайзер обязан пропустить
   start, иначе шаг 2 читается как шаг 1 — молча.

8. ПРОВЕРКА НА ДУБЛЬ — ОДНА НА ВСЕ ДВЕРИ. У новой двери не должно быть своей.
"""

import json
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
from wiki import edit as wiki_edit  # noqa: E402
from wiki import migration as wiki_migration  # noqa: E402
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import sanitize as wiki_sanitize  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki import storage as wiki_storage  # noqa: E402
from wiki import yandex_pro  # noqa: E402
from wiki import yandex_sync  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

PAGE_URL = ('https://pro.yandex.com/kz-ru/almaty/knowledge-base/taxi/tariffs/intercity')

# Алматы — city-12 в справочнике городов Яндекса, регион 107. Значения взяты со
# живой страницы: они лежат в том же __NEXT_DATA__, и подделывать их нельзя —
# на них держится фильтр аудитории.
ALMATY = {'id': 12, 'code': 'almaty', 'name': 'Алматы', 'region_id': 107}
ASTANA = {'id': 10, 'code': 'nur-sultan', 'name': 'Астана', 'region_id': 106}

IMG_PORTRAIT = 'https://storage.yandexcloud.net/yandexpro-prod/a/phone.png'
IMG_WIDE = 'https://storage.yandexcloud.net/yandexpro-prod/a/desktop.jpg'


def component(kind, values, children=None):
    node = {'id': abs(hash(json.dumps(values, sort_keys=True, ensure_ascii=False))) % 9999,
            'type': kind, 'values': values}
    if children is not None:
        node['children'] = children
    return node


def make_page(components, *, city='almaty', name='Тариф «Межгород»',
              entity_id=2693, last_update='27 мая 2025', build='abc123'):
    """Слепок состояния страницы базы знаний в том виде, в каком его отдаёт Next.js."""
    return {
        'buildId': build,
        'query': {'locale': 'kz-ru', 'city': city, 'category': 'taxi',
                  'subCategory': 'tariffs', 'article': 'intercity'},
        'props': {
            'initialProps': {
                'pageProps': {
                    'data': {
                        'entity_id': entity_id,
                        'category': {'slug': 'taxi', 'name': 'Яндекс Такси'},
                        'subcategory': {'slug': 'tariffs', 'name': 'Классификатор'},
                        'article': {
                            'slug': 'intercity', 'name': name,
                            'last_update': last_update, 'created_at': '10 апр 2024',
                            'text_components': components,
                        },
                    },
                },
            },
            'initialState': {
                'country': {'countries': [
                    {'id': 2, 'code': 'kz', 'name': 'Казахстан',
                     'cities': [ALMATY, ASTANA]},
                ]},
            },
        },
    }


def as_html(page):
    """То же состояние, но внутри страницы — как оно приходит из сети."""
    return ('<!doctype html><html><body><div id="__next">'
            '<h1>Заголовок из разметки</h1>'
            '<script id="__NEXT_DATA__" type="application/json">%s</script>'
            '</body></html>' % json.dumps(page, ensure_ascii=False))


TEXT_FULL = ('<p><strong>Новый тариф «Межгород».</strong></p>'
             '<h3><strong>Как работает тариф</strong></h3>'
             '<p>Заказ можно:</p><ol><li><p>Получить на линии</p></li></ol>')
# Тот же текст без вводного абзаца — источник присылает его ВТОРЫМ компонентом.
TEXT_TAIL = ('<h3><strong>Как работает тариф</strong></h3>'
             '<p>Заказ можно:</p><ol><li><p>Получить на линии</p></li></ol>')


def full_components():
    """Страница со всеми ловушками сразу — на ней и проверяем разбор."""
    return [
        component('YTextArea', {'text': TEXT_FULL, 'is_hide': False}),
        # Ловушка: этот компонент целиком лежит внутри предыдущего.
        component('YTextArea', {'text': TEXT_TAIL, 'is_hide': False}),
        component('ImageSlider', {'dataList': [
            {'url': IMG_PORTRAIT, 'name': 'Откройте раздел «Межгород»', 'image_id': 1},
            {'url': IMG_WIDE, 'name': '', 'image_id': 2},
        ]}),
        # Продолжение перечня после картинки — с нумерации 2.
        component('YTextArea', {'text': '<ol start="2"><li><p>Выбрать самому</p></li></ol>'}),
        # Три написания признака «скрыт» — ни один из этих кусков в статью
        # попасть не должен.
        component('YTextArea', {'text': '<p>СКРЫТО-1</p>', 'is_hide': True}),
        component('YTextArea', {'text': '<p>СКРЫТО-2</p>', 'isHide': True}),
        component('YTextArea', {'text': '<p>СКРЫТО-3</p>', 'is_hidden': True}),
        # Аудитория: первый кусок — для Астаны, второй — для Алматы.
        component('YTextArea', {'text': '<p>ТОЛЬКО-АСТАНА</p>',
                                'allowed_ids': ['city-10']}),
        component('YTextArea', {'text': '<p>Условие для Алматы</p>',
                                'allowed_ids': ['city-12', 'region-107']}),
        component('YTextArea', {'text': '<p>ЗАПРЕЩЕНО-В-АЛМАТЫ</p>',
                                'forbidden_ids': ['city-12']}),
        component('AccordionStart', {'title': 'Полные условия тарифа'}, children=[
            component('LeaveRequest', {'url': 'https://taxi.yandex.kz/tariff/intercity',
                                       'title': 'Полные условия'}),
            component('YTextArea', {'text': '<p>Рейтинг не ниже 4,75.</p>'}),
            component('Table', {'head': ['Город', 'Цена'],
                                'body': [['Алматы', '1000'], ['Астана', '1200']]}),
        ]),
        component('VideoInternal', {'url': 'https://runtime.strm.yandex.ru/player/video/v1',
                                    'title': 'Как выполнить брендирование'}),
        component('TariffCarClassifier', {}),
    ]


def parsed_page(components=None, **kwargs):
    return yandex_pro.parse_article(make_page(components or full_components(), **kwargs),
                                    PAGE_URL)


def image_map(parsed, *, portrait=(499, 1080), wide=(1280, 720)):
    sizes = {IMG_PORTRAIT: portrait, IMG_WIDE: wide}
    out = {}
    for index, item in enumerate(parsed['images'], start=1):
        width, height = sizes.get(item['url'], (0, 0))
        out[item['url']] = {'url': '/api/wiki/file/uuid-%d' % index,
                            'width': width, 'height': height}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Адрес страницы
# ─────────────────────────────────────────────────────────────────────────────

class UrlTest(unittest.TestCase):
    def test_query_tail_is_dropped(self):
        """В постановке ссылка пришла с хвостом '?section=' — страница та же.

        Не отбросив хвост, тот же источник получил бы два разных ключа, и
        повторный импорт завёл бы вторую статью.
        """
        parts = yandex_pro.parse_url(PAGE_URL + '?section=&utm_source=x#top')
        self.assertEqual(parts['url'], PAGE_URL)
        self.assertEqual(parts['slug'], 'intercity')
        self.assertEqual(parts['city'], 'almaty')

    def test_foreign_hosts_are_refused(self):
        for url in ('https://example.com/kz-ru/almaty/knowledge-base/taxi/t/a',
                    'https://pro.yandex.com.evil.tld/kz-ru/a/knowledge-base/t/s/a',
                    'https://pro.yandex.com/kz-ru/almaty/blog/taxi/tariffs/intercity',
                    'не ссылка', '', None):
            self.assertIsNone(yandex_pro.parse_url(url), url)

    def test_city_is_optional(self):
        url = 'https://pro.yandex.com/kz-ru/knowledge-base/taxi/tariffs/auto-list'
        parts = yandex_pro.parse_url(url)
        self.assertIsNotNone(parts)
        self.assertIsNone(parts['city'])
        self.assertEqual(parts['url'], url)


# ─────────────────────────────────────────────────────────────────────────────
# Разбор страницы
# ─────────────────────────────────────────────────────────────────────────────

class ParseTest(unittest.TestCase):
    def setUp(self):
        self.parsed = parsed_page()
        self.content, self.warnings = yandex_pro.build_content(
            self.parsed, image_map(self.parsed))

    # ── Требование 1: переносится вся статья ─────────────────────────────
    def test_collapsed_block_content_is_in_the_body(self):
        """Текст раскрывашки есть в теле, и заголовком раздела, а не <details>.

        Раскрывающиеся блоки редактор вики в импортированном тексте разбирает в
        абзацы при первом же сохранении — известный дефект; заголовок надёжнее
        и попадает в оглавление витрины.
        """
        self.assertIn('<h2>Полные условия тарифа</h2>', self.content)
        self.assertIn('Рейтинг не ниже 4,75', self.content)
        self.assertNotIn('<details', self.content)

    def test_html_of_the_page_is_ignored(self):
        """Разбор идёт по состоянию, а не по разметке."""
        parsed = yandex_pro.parse_article(as_html(make_page(full_components())), PAGE_URL)
        self.assertEqual(parsed['title'], 'Тариф «Межгород»')
        content, _ = yandex_pro.build_content(parsed, image_map(parsed))
        self.assertNotIn('Заголовок из разметки', content)

    def test_source_link_closes_the_article(self):
        """«В конце статьи добавить ссылку на официальный источник» — из постановки."""
        self.assertIn(PAGE_URL, self.content)
        self.assertIn('27 мая 2025', self.content)
        tail = self.content[-400:]
        self.assertIn('Источник', tail)

    # ── Требование 6: ловушки источника ─────────────────────────────────
    def test_overlapping_components_are_taken_once(self):
        """Текст [1] лежит внутри [0]: страница показывает его один раз."""
        self.assertEqual(self.content.count('Как работает тариф'), 1)
        self.assertEqual(self.content.count('Получить на линии'), 1)

    def test_all_three_spellings_of_hidden_are_honoured(self):
        for marker in ('СКРЫТО-1', 'СКРЫТО-2', 'СКРЫТО-3'):
            self.assertNotIn(marker, self.content, marker)

    def test_content_for_another_city_is_dropped(self):
        """В JSON страницы Алматы лежат куски для других городов."""
        self.assertNotIn('ТОЛЬКО-АСТАНА', self.content)
        self.assertNotIn('ЗАПРЕЩЕНО-В-АЛМАТЫ', self.content)
        self.assertIn('Условие для Алматы', self.content)

    def test_the_same_page_for_another_city_keeps_its_own_content(self):
        """Город берётся из АДРЕСА страницы: он и есть то, что открыл человек."""
        astana = yandex_pro.parse_article(
            make_page(full_components(), city='nur-sultan'),
            PAGE_URL.replace('/almaty/', '/nur-sultan/'))
        content, _ = yandex_pro.build_content(astana, image_map(astana))
        self.assertIn('ТОЛЬКО-АСТАНА', content)
        self.assertNotIn('Условие для Алматы', content)

    def test_audience_filter_is_off_when_the_city_is_unknown(self):
        """Города в адресе нет — куски по городам не выбрасываем.

        Потерять содержимое по причине, которой у нас нет, хуже, чем взять
        лишнее: во втором случае это видно читателю.
        """
        page = make_page(full_components(), city=None)
        page['query'].pop('city')
        parsed = yandex_pro.parse_article(
            page, 'https://pro.yandex.com/kz-ru/knowledge-base/taxi/tariffs/intercity')
        content, _ = yandex_pro.build_content(parsed, image_map(parsed))
        self.assertIn('ТОЛЬКО-АСТАНА', content)
        self.assertIn('Условие для Алматы', content)

    def test_short_table_moves_and_long_one_becomes_a_link(self):
        self.assertIn('<table>', self.content)
        self.assertIn('<th><p>Город</p></th>', self.content)

        rows = [['Марка %d' % i, 'от 200%d' % (i % 10)]
                for i in range(yandex_pro.MAX_TABLE_ROWS + 5)]
        parsed = parsed_page([component('Table', {'head': ['Марка', 'Годы'],
                                                  'body': rows})])
        content, warnings = yandex_pro.build_content(parsed, {})
        self.assertNotIn('<table>', content)
        self.assertIn('Смотреть в базе знаний', content)
        self.assertTrue(any('длиннее' in w for w in warnings), warnings)

    def test_video_becomes_a_link_and_a_warning(self):
        """Видео в вике не живёт: в белом списке нет ни video, ни iframe."""
        self.assertNotIn('<video', self.content)
        self.assertNotIn('<iframe', self.content)
        self.assertIn('Как выполнить брендирование', self.content)
        self.assertTrue(any('Видео' in w for w in self.warnings), self.warnings)

    def test_yandex_own_widget_is_reported_not_silently_dropped(self):
        self.assertTrue(any('классификатор автомобилей' in w for w in self.warnings),
                        self.warnings)

    # ── Требование 7: нумерация, разорванная картинкой ───────────────────
    def test_broken_numbering_keeps_its_start(self):
        self.assertIn('<ol start="2">', self.content)

    def test_sanitizer_lets_ol_start_through(self):
        """Сторож на белый список: без start второй кусок перечня врёт номером."""
        self.assertIn('start', wiki_sanitize.ALLOWED_ATTRIBUTES['ol'])
        cleaned = wiki_sanitize.sanitize_html('<ol start="4"><li>шаг</li></ol>')
        self.assertIn('start="4"', cleaned)

    # ── Требование 2: картинки ──────────────────────────────────────────
    def test_body_has_no_source_image_addresses(self):
        self.assertNotIn('storage.yandexcloud.net', self.content)
        self.assertNotIn('yandexcloud', self.content)

    def test_portrait_screenshot_is_narrow_and_wide_one_is_full(self):
        """Скриншот телефона 499x1080 во всю колонку занимает три экрана."""
        images = re.findall(r'<img[^>]*>', self.content)
        self.assertEqual(len(images), 2, images)
        self.assertIn('data-width="%d"' % yandex_pro.PORTRAIT_WIDTH, images[0])
        self.assertIn('data-align="center"', images[0])
        self.assertIn('width: %d%%' % yandex_pro.PORTRAIT_WIDTH, images[0])
        self.assertIn('data-width="%d"' % yandex_pro.WIDE_WIDTH, images[1])

    def test_caption_becomes_alt_and_a_visible_line(self):
        self.assertIn('alt="Откройте раздел «Межгород»"', self.content)
        self.assertIn('<em>Откройте раздел «Межгород»</em>', self.content)

    def test_image_without_our_address_is_not_left_as_a_foreign_link(self):
        """Кадр, который не удалось уложить, из тела ИСЧЕЗАЕТ, а не остаётся чужой ссылкой."""
        content, warnings = yandex_pro.build_content(self.parsed, {})
        self.assertNotIn('<img', content)
        self.assertTrue(any('Не перенесено картинок: 2' == w for w in warnings), warnings)

    def test_shorthand_margin_is_never_used(self):
        """Сокращённое margin санитайзер вырезает целиком — центрирование пропало бы."""
        self.assertNotIn('margin:', self.content)
        self.assertIn('margin-left: auto', self.content)

    # ── Отпечаток источника ─────────────────────────────────────────────
    def test_fingerprint_ignores_the_page_build(self):
        """buildId меняется у Яндекса сам — статья не должна переписываться от этого."""
        other = parsed_page(build='zzz999')
        self.assertEqual(self.parsed['fingerprint'], other['fingerprint'])

    def test_fingerprint_notices_a_changed_word(self):
        changed = full_components()
        changed[0] = component('YTextArea', {'text': TEXT_FULL.replace('Новый', 'Старый')})
        self.assertNotEqual(self.parsed['fingerprint'],
                            parsed_page(changed)['fingerprint'])

    def test_fingerprint_notices_a_changed_picture(self):
        changed = full_components()
        changed[2] = component('ImageSlider', {'dataList': [
            {'url': IMG_PORTRAIT.replace('phone', 'phone2'), 'name': 'Откройте раздел «Межгород»'},
            {'url': IMG_WIDE, 'name': ''},
        ]})
        self.assertNotEqual(self.parsed['fingerprint'],
                            parsed_page(changed)['fingerprint'])

    def test_a_page_without_an_article_is_an_honest_error(self):
        for page in ({}, {'props': {}}, make_page([], name='')):
            with self.assertRaises(yandex_pro.SourceError):
                yandex_pro.parse_article(page, PAGE_URL)

    def test_a_page_that_is_not_a_page_is_an_honest_error(self):
        with self.assertRaises(yandex_pro.SourceError):
            yandex_pro.parse_article('<html><body>вход в аккаунт</body></html>', PAGE_URL)

    def test_summary_is_the_first_words_of_the_source(self):
        summary = yandex_pro.summary_of(self.parsed)
        self.assertTrue(summary.startswith('Новый тариф «Межгород»'), summary)
        self.assertLessEqual(len(summary), 281)


# ─────────────────────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────────────────────

class SchemaTest(unittest.TestCase):
    def _ddl(self):
        return '\n'.join(s for s in wiki_schema._YANDEX_PRO_STATEMENTS
                         if isinstance(s, str))

    def test_url_is_unique(self):
        """Ключ повторного прогона держится индексом, а не аккуратностью кода."""
        self.assertRegex(self._ddl(),
                         r'CREATE UNIQUE INDEX[^;]+wiki_yandex_pages \(url\)')

    def test_tables_are_created_by_init(self):
        source = (ROOT / 'wiki' / 'schema.py').read_text(encoding='utf-8')
        self.assertIn('for statement in _YANDEX_PRO_STATEMENTS:', source,
                      'новый DDL не прогоняется в init_wiki_schema')

    def test_image_map_exists(self):
        """Без карты «кадр источника -> файл» каждая сверка заливала бы копии."""
        self.assertIn('wiki_yandex_images', self._ddl())
        self.assertIn('source_url', self._ddl())

    def test_source_code_matches_the_migration_module(self):
        self.assertEqual(yandex_pro.SOURCE, wiki_migration.SOURCE_YANDEX_PRO)
        self.assertIn(wiki_migration.SOURCE_YANDEX_PRO, wiki_migration.SOURCE_LABELS)


# ─────────────────────────────────────────────────────────────────────────────
# Сверка: курсор-заглушка на живых функциях модуля
# ─────────────────────────────────────────────────────────────────────────────

class FakeCursor:
    """Курсор, который отвечает на запросы модуля сверки заранее готовым.

    Полноценный Postgres здесь не нужен: проверяется порядок решений, а не SQL.
    Ответы раздаются по узнаваемому куску запроса — так же, как это делают
    другие тесты раздела.
    """

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.queries = []
        self._result = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.queries.append((' '.join(str(sql).split()), params))
        self._result = None
        for marker, value in self.answers.items():
            if marker in ' '.join(str(sql).split()):
                self._result = value
                return

    def fetchone(self):
        value = self._result
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def fetchall(self):
        value = self._result
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def sql_of(self, marker):
        return [q for q, _p in self.queries if marker in q]


PAGE_COLUMNS = yandex_sync._PAGE_COLUMNS


def page_row(**over):
    row = {'article_id': 715, 'url': PAGE_URL, 'entity_id': 2693,
           'source_slug': 'intercity', 'source_title': 'Тариф «Межгород»',
           'source_updated': '27 мая 2025', 'fingerprint': 'f' * 64,
           'content_hash': 'c' * 32, 'auto_sync': True, 'ai_format': False,
           'linked_by': 42, 'linked_at': None, 'last_checked_at': None,
           'last_changed_at': None, 'last_status': 'changed', 'last_error': None}
    row.update(over)
    return tuple(row[key] for key in PAGE_COLUMNS)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.parsed = parsed_page()
        self.page = make_page(full_components())
        self.updates = []
        self.stored = []

        def fake_update(_cursor, article_id, fields, **kwargs):
            self.updates.append((article_id, dict(fields), kwargs.get('comment')))
            return True

        def fake_store(_cursor, _gcs, **kwargs):
            self.stored.append(kwargs)
            return ('11111111-1111-1111-1111-11111111111%d' % len(self.stored),
                    '/api/wiki/file/uuid-%d' % len(self.stored))

        for module, name, replacement in (
            (wiki_edit, 'update_article', fake_update),
            (wiki_storage, 'store_file', fake_store),
        ):
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

    def _cursor(self, **answers):
        base = {'FROM wiki_yandex_pages WHERE article_id': page_row(),
                'FROM wiki_yandex_images': [],
                'SELECT width, height FROM wiki_files': (499, 1080)}
        base.update(answers)
        return FakeCursor(base)

    def _sync(self, cursor, *, state_hash='c' * 32, force=False, fingerprint=None):
        original = wiki_edit.current_state
        wiki_edit.current_state = lambda _c, _id: {'content_hash': state_hash}
        self.addCleanup(setattr, wiki_edit, 'current_state', original)
        page = self.page if fingerprint is None else self.page
        return yandex_sync.sync_article(
            cursor, {}, article_id=715, editor_id=42, force=force,
            page_html=json.dumps(page, ensure_ascii=False),
            blobs={IMG_PORTRAIT: (b'\x89PNG', 'image/png'),
                   IMG_WIDE: (b'\xff\xd8\xff', 'image/jpeg')})

    # ── Требование 4: неизменившийся источник не трогает статью ─────────
    def test_unchanged_source_does_not_touch_the_article(self):
        cursor = self._cursor(**{'FROM wiki_yandex_pages WHERE article_id':
                                 page_row(fingerprint=self.parsed['fingerprint'])})
        result = self._sync(cursor)
        self.assertEqual(result['status'], yandex_sync.STATUS_OK)
        self.assertEqual(self.updates, [],
                         'сверка переписала статью, у которой источник не менялся')
        self.assertTrue(cursor.sql_of('UPDATE wiki_yandex_pages'),
                        'прогон сверки не отмечен')

    # ── Требование 5: автоматика не затирает ручные правки ─────────────
    def test_hand_edited_article_is_not_overwritten(self):
        cursor = self._cursor()
        result = self._sync(cursor, state_hash='d' * 32)
        self.assertEqual(result['status'], yandex_sync.STATUS_CONFLICT)
        self.assertEqual(self.updates, [],
                         'сверка затёрла правки человека — этого нельзя НИКОГДА')

    def test_conflict_remembers_the_new_fingerprint(self):
        """Иначе каждая ночь повторяла бы одно и то же сообщение о конфликте."""
        cursor = self._cursor()
        self._sync(cursor, state_hash='d' * 32)
        marked = [p for q, p in cursor.queries if 'UPDATE wiki_yandex_pages' in q]
        self.assertTrue(marked)
        self.assertEqual(marked[-1]['fingerprint'], self.parsed['fingerprint'])

    def test_force_overwrites_on_purpose(self):
        cursor = self._cursor()
        result = self._sync(cursor, state_hash='d' * 32, force=True)
        self.assertEqual(result['status'], yandex_sync.STATUS_CHANGED)
        self.assertEqual(len(self.updates), 1)
        self.assertIn('Обновление из базы знаний', self.updates[0][2])

    def test_changed_source_updates_the_body(self):
        cursor = self._cursor()
        result = self._sync(cursor)
        self.assertEqual(result['status'], yandex_sync.STATUS_CHANGED)
        self.assertEqual(len(self.updates), 1)
        _id, fields, comment = self.updates[0]
        self.assertIn('content', fields)
        self.assertIn('Полные условия тарифа', fields['content'])
        self.assertEqual(comment, yandex_sync.COMMENT_SYNC)

    # ── Требование 2: картинки в WebP через единственную дверь ──────────
    def test_images_go_to_the_bucket_with_a_content_type(self):
        """Пустой content_type отменяет перевод в WebP МОЛЧА (wiki/images.py)."""
        self._sync(self._cursor())
        self.assertEqual(len(self.stored), 2, self.stored)
        for call in self.stored:
            self.assertTrue(call['content_type'], 'картинка уехала без типа')
            self.assertTrue(call['content_type'].startswith('image/'), call['content_type'])

    def test_known_image_is_not_uploaded_again(self):
        """Повторная сверка не заливает те же кадры: в wiki_files нет дедупликации."""
        cursor = self._cursor(**{
            'FROM wiki_yandex_images': [(IMG_PORTRAIT, '/api/wiki/file/old-1', 499, 1080),
                                        (IMG_WIDE, '/api/wiki/file/old-2', 1280, 720)]})
        self._sync(cursor)
        self.assertEqual(self.stored, [], 'кадры залились второй раз')

    def test_content_type_falls_back_to_the_address(self):
        """CDN может ответить без Content-Type — тогда тип берётся из расширения."""
        self.assertEqual(yandex_sync._content_type_of(IMG_PORTRAIT, None), 'image/png')
        self.assertEqual(yandex_sync._content_type_of(IMG_WIDE, ''), 'image/jpeg')
        self.assertEqual(
            yandex_sync._content_type_of('https://x/y/a.jpeg?v=1', 'text/html'),
            'image/jpeg')
        self.assertIsNone(yandex_sync._content_type_of('https://x/y/page', 'text/html'))

    def test_unlinked_article_cannot_be_synced(self):
        cursor = FakeCursor({'FROM wiki_yandex_pages WHERE article_id': None})
        with self.assertRaises(yandex_sync.SyncError):
            yandex_sync.sync_article(cursor, {}, article_id=715, page_html='{}')

    # ── Связка уже написанной статьи ────────────────────────────────────
    def test_linking_an_existing_article_does_not_rewrite_it(self):
        """Постановка про «Межгород»: статья в вике уже есть, следить надо за Яндексом.

        Связка обязана только начать сверку. Перепиши она текст сразу — и
        кнопка «Связать» уничтожала бы статью, которую писали руками.
        """
        cursor = self._cursor(**{'FROM wiki_yandex_pages WHERE url': None})
        result = yandex_sync.link_article(
            cursor, article_id=715, url=PAGE_URL, linked_by=42,
            fetch_page_fn=lambda _u: json.dumps(self.page, ensure_ascii=False))
        self.assertEqual(result['entity_id'], 2693)
        self.assertEqual(self.updates, [], 'связка переписала статью')
        inserted = [p for q, p in cursor.queries if 'INSERT INTO wiki_yandex_pages' in q]
        self.assertTrue(inserted)
        self.assertIsNone(inserted[0]['content_hash'],
                          'у связанной статьи отпечаток тела должен быть пустым: '
                          'это и означает «тело писали не мы»')

    def test_a_linked_hand_written_article_reports_a_conflict(self):
        """Пустой отпечаток тела = сверка зовёт человека, а не переписывает."""
        cursor = self._cursor(**{'FROM wiki_yandex_pages WHERE article_id':
                                 page_row(content_hash=None)})
        result = self._sync(cursor)
        self.assertEqual(result['status'], yandex_sync.STATUS_CONFLICT)
        self.assertEqual(self.updates, [])

    def test_two_articles_cannot_watch_the_same_page(self):
        """Иначе ночная сверка писала бы обеим один текст — тот самый дубль."""
        cursor = self._cursor(**{'FROM wiki_yandex_pages WHERE url':
                                 page_row(article_id=999)})
        with self.assertRaises(yandex_sync.SyncError):
            yandex_sync.link_article(
                cursor, article_id=715, url=PAGE_URL, linked_by=42,
                fetch_page_fn=lambda _u: json.dumps(self.page, ensure_ascii=False))

    def test_ai_formatting_is_asked_not_to_touch_pictures(self):
        """Импортёр уже расставил размеры по самим кадрам — модели их трогать нельзя."""
        self.assertIn('КАРТИНКИ НЕ ТРОГАЙ', yandex_sync.FORMAT_INSTRUCTION)
        self.assertIn('НЕ МЕНЯЯ ни одного слова', yandex_sync.FORMAT_INSTRUCTION)

    def test_ai_failure_does_not_lose_the_article(self):
        def broken(*_a, **_k):
            raise RuntimeError('модель недоступна')

        content, warnings = yandex_sync.format_with_ai(
            'Тариф', '<p>текст</p>', generate_fn=broken)
        self.assertEqual(content, '<p>текст</p>')
        self.assertTrue(warnings)


# ─────────────────────────────────────────────────────────────────────────────
# Двери
# ─────────────────────────────────────────────────────────────────────────────

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
class RouteTest(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.recorded = []
        self.updated = []
        self.permissions = {'can_read': True, 'can_edit': True, 'can_publish': True}
        self.page_answer = page_row()

        cursor = FakeCursor({'FROM wiki_yandex_pages WHERE article_id': self.page_answer,
                             'FROM wiki_yandex_images': [],
                             'SELECT width, height FROM wiki_files': (499, 1080),
                             'FROM wiki_yandex_pages WHERE url': None,
                             'SELECT slug FROM wiki_articles': ('yandex-intercity',)})
        self.cursor = cursor
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def fake_create(_cursor, **kwargs):
            self.created.append(kwargs)
            return 715

        def fake_update(_cursor, article_id, fields, **kwargs):
            self.updated.append((article_id, dict(fields)))
            return True

        page = make_page(full_components())
        patches = [
            (queries, 'load_access_context', lambda _c, _u: make_context()),
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'log_action', lambda *a, **k: None),
            (queries, 'spaces_for_user', lambda *a, **k: [11]),
            (queries, 'allowed_section_ids', lambda *a, **k: {8}),
            (queries, 'section_rules_for_user', lambda *a, **k: {}),
            (wiki_perimeter, 'read_perimeter',
             lambda *a, **k: (collect_subjects(user_id=42, otp_role='admin'), {8}, {715})),
            (wiki_articles, 'visible_article_ids', lambda *a, **k: {715}),
            (wiki_articles, 'get_article',
             lambda _c, article_id=None: {'id': 715, 'title': 'Тариф «Межгород»',
                                          'slug': 'yandex-intercity', 'status': 'draft',
                                          'section_ids': [8]}),
            (wiki_articles, 'effective_permissions',
             lambda *a, **k: dict(self.permissions)),
            (wiki_migration, 'duplicate_probe',
             lambda _c, _ctx, **k: {'items': [], 'degraded': False}),
            (wiki_migration, 'record', lambda _c, **k: self.recorded.append(k)),
            (wiki_edit, 'create_article', fake_create),
            (wiki_edit, 'update_article', fake_update),
            (wiki_edit, 'current_state', lambda _c, _id: {'content_hash': 'c' * 32}),
            (wiki_edit, 'slug_is_free', lambda _c, _slug: True),
            (wiki_edit, 'default_section_id', lambda *a, **k: 8),
            (wiki_storage, 'store_file',
             lambda _c, _g, **k: ('11111111-1111-1111-1111-111111111111',
                                  '/api/wiki/file/uuid-1')),
            (yandex_sync, 'fetch_page',
             lambda _url: json.dumps(page, ensure_ascii=False)),
            (yandex_sync, 'fetch_image', lambda _url: (b'\x89PNG', 'image/png')),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x',
                 'bucket_name': lambda: 'bucket', 'client': lambda: MagicMock()},
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def test_preview_returns_a_body_and_creates_nothing(self):
        answer = self.client.post('/api/wiki/yandex/preview', json={'url': PAGE_URL})
        self.assertEqual(answer.status_code, 200, answer.get_json())
        body = answer.get_json()
        self.assertIn('Полные условия тарифа', body['content'])
        self.assertEqual(body['source']['entity_id'], 2693)
        self.assertEqual(self.created, [], 'предпросмотр создал статью')

    # ── Требование 3: повторный прогон ──────────────────────────────────
    def test_import_creates_a_draft(self):
        answer = self.client.post('/api/wiki/yandex/import',
                                  json={'url': PAGE_URL, 'section_ids': [8]})
        self.assertEqual(answer.status_code, 201, answer.get_json())
        body = answer.get_json()
        self.assertEqual(body['status'], 'draft')
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.recorded), 1)
        self.assertEqual(self.recorded[0]['source'], wiki_migration.SOURCE_YANDEX_PRO)
        self.assertEqual(self.recorded[0]['source_id'], 2693)

    def test_import_cannot_publish_even_if_asked(self):
        answer = self.client.post('/api/wiki/yandex/import', json={
            'url': PAGE_URL, 'section_ids': [8],
            'status': 'published', 'is_published': True})
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(answer.get_json()['status'], 'draft')
        self.assertEqual([f for _id, f in self.updated if f.get('status')], [],
                         'импорт выпустил статью — этого не должно быть НИКОГДА')

    def test_second_import_of_the_same_page_returns_the_same_article(self):
        self.cursor.answers['FROM wiki_yandex_pages WHERE url'] = self.page_answer
        answer = self.client.post('/api/wiki/yandex/import',
                                  json={'url': PAGE_URL, 'section_ids': [8]})
        self.assertEqual(answer.status_code, 200)
        body = answer.get_json()
        self.assertFalse(body['created'])
        self.assertEqual(body['id'], 715)
        self.assertEqual(self.created, [], 'создали вторую копию той же страницы')

    def test_a_bad_link_is_a_human_message_not_a_crash(self):
        answer = self.client.post('/api/wiki/yandex/preview',
                                  json={'url': 'https://example.com/a'})
        self.assertEqual(answer.status_code, 400)
        self.assertIn('базы знаний', answer.get_json()['error'])

    def test_sync_needs_the_right_to_edit_this_article(self):
        self.permissions = {'can_read': True, 'can_edit': False}
        answer = self.client.post('/api/wiki/yandex/715/sync', json={})
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer.get_json()['code'], 'WIKI_FORBIDDEN')

    def test_linking_an_existing_article_needs_the_right_to_edit_it(self):
        self.permissions = {'can_read': True, 'can_edit': False}
        answer = self.client.post('/api/wiki/yandex/715/link', json={'url': PAGE_URL})
        self.assertEqual(answer.status_code, 403)

    def test_linking_an_existing_article_creates_nothing(self):
        self.cursor.answers['FROM wiki_yandex_pages WHERE url'] = None
        answer = self.client.post('/api/wiki/yandex/715/link', json={'url': PAGE_URL})
        self.assertEqual(answer.status_code, 201, answer.get_json())
        self.assertEqual(self.created, [], 'связка создала вторую статью')
        self.assertEqual(self.updated, [], 'связка тронула текст статьи')

    def test_list_of_links_is_narrowed_to_the_perimeter(self):
        answer = self.client.get('/api/wiki/yandex')
        self.assertEqual(answer.status_code, 200)
        self.assertIn('totals', answer.get_json())

    def test_unlink_keeps_the_article(self):
        answer = self.client.delete('/api/wiki/yandex/715')
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_json()['status'], 'unlinked')
        self.assertTrue(self.cursor.sql_of('DELETE FROM wiki_yandex_pages'))
        self.assertEqual(self.updated, [], 'отписка тронула текст статьи')


# ─────────────────────────────────────────────────────────────────────────────
# Границы, которые нельзя нарушать
# ─────────────────────────────────────────────────────────────────────────────

class BoundaryTest(unittest.TestCase):
    """Сторожа на текст исходников: то, что ломается молча."""

    def _read(self, *parts):
        return (ROOT.joinpath(*parts)).read_text(encoding='utf-8')

    # ── Требование 8: одна проверка на дубль ────────────────────────────
    def test_the_new_door_uses_the_shared_duplicate_probe(self):
        source = self._read('wiki', 'routes_yandex_pro.py')
        self.assertIn('wiki_migration.duplicate_probe(', source)
        self.assertNotRegex(source, r'\ndef _duplicates\(',
                            'у импортёра Яндекс Про появилась своя проверка дублей')

    def test_the_bucket_has_exactly_one_door(self):
        """Байты уходят в бакет только через wiki/storage.py — второй путь разойдётся молча."""
        for name in ('yandex_pro.py', 'yandex_sync.py', 'routes_yandex_pro.py'):
            source = self._read('wiki', name)
            self.assertNotIn('upload_from_string', source, name)
        self.assertIn('wiki_storage.store_file(', self._read('wiki', 'yandex_sync.py'))

    def test_the_parser_stays_free_of_network_and_database(self):
        """Разбор обязан гоняться тестом без сети и без бакета."""
        source = self._read('wiki', 'yandex_pro.py')
        for forbidden in ('import requests', 'from flask', 'cursor'):
            self.assertNotIn(forbidden, source, forbidden)

    def test_routes_are_registered_after_migration(self):
        source = self._read('wiki', 'routes.py')
        self.assertIn('routes_yandex_pro.register(', source)
        self.assertLess(source.index('routes_migration.register('),
                        source.index('routes_yandex_pro.register('),
                        'дверь Яндекс Про берёт помощники у routes_edit и делит '
                        'очередь с routes_migration — регистрировать её надо после')

    def test_night_walk_does_not_hold_a_cursor_while_on_the_network(self):
        """Обработчики вики держат соединение из пула на 40 — качать под ним нельзя."""
        source = self._read('wiki', 'yandex_sync.py')
        self.assertIn('_prefetch_images', source)
        walk = source[source.index('def sync_all('):]
        self.assertLess(walk.index('fetch_page'), walk.index('sync_article('),
                        'страница качается уже под открытым курсором')


class FrontendTest(unittest.TestCase):
    """Интерфейсные решения в этом проекте сторожит pytest, читая .jsx текстом."""

    def setUp(self):
        src = ROOT / 'src' / 'components' / 'wiki'
        self.dialog = (src / 'WikiYandexImport.jsx').read_text(encoding='utf-8')
        self.view = (src / 'WikiView.jsx').read_text(encoding='utf-8')
        self.queue = (src / 'WikiMigration.jsx').read_text(encoding='utf-8')

    def test_dialog_talks_to_all_the_doors(self):
        for door in ('/yandex/preview', '/yandex/import', '/yandex`', '/sync', '/link'):
            self.assertIn(door, self.dialog, door)

    def test_found_duplicate_can_be_linked_instead_of_copied(self):
        """Дубль — чаще повод связать существующую статью, чем завести вторую.

        Ровно этот случай в постановке #248: «Тариф „Межгород"» в вике уже
        написан руками. Без кнопки «Связать» единственным выходом была бы
        вторая статья с тем же текстом.
        """
        self.assertIn('linkExisting', self.dialog)
        block = self.dialog[self.dialog.index('duplicates.slice'):]
        self.assertIn('linkExisting(d)', block[:1600])

    def test_import_is_gated_exactly_like_a_new_article(self):
        """Способность та же, и гостевое пространство исключено так же.

        Кнопка, которая всегда отвечает «нет права», — мёртвая кнопка; этот
        довод уже записан у «Новой статьи», и второй гейт обязан ему следовать.
        """
        button = self.view[self.view.index('setYandexImport({})') - 900:
                           self.view.index('setYandexImport({})')]
        self.assertIn('capabilities.can_create', button)
        self.assertIn('!activeSpace?.guest_only', button)

    def test_dialog_lives_at_the_section_level(self):
        """Открывается из шапки — значит обязан работать с любой вкладки."""
        self.assertIn('<WikiYandexImport', self.view)
        self.assertLess(self.view.index('<WikiSpaceModal'),
                        self.view.index('<WikiYandexImport'))

    def test_source_label_is_not_hardcoded_in_the_queue(self):
        """Появился второй источник — подпись «старая вики» перестала быть верной."""
        self.assertIn('SOURCE_LABELS', self.queue)
        self.assertNotRegex(self.queue, r'>\s*\n?\s*старая вики\s*\n',
                            'подпись источника снова зашита строкой')

    def test_source_labels_match_the_server(self):
        server = set(wiki_migration.SOURCE_LABELS)
        found = re.search(r'const SOURCE_LABELS = \{(.*?)\};', self.queue, re.S)
        self.assertIsNotNone(found, 'во фронте нет словаря подписей источников')
        front = set(re.findall(r'^\s*([a-z_]+):', found.group(1), re.M))
        self.assertEqual(front, server,
                         'коды источников во фронте и на сервере разошлись')

    def test_every_badge_tone_exists_in_the_kit(self):
        """Незнакомый тон бейдж молча подменяет на серый.

        BADGE_TONES в UI-ките — slate/green/red/blue/amber, и `emerald`/`rose`
        выглядят правдоподобно, но дают ровно серую плашку: «источник не
        прочитался» теряется среди «совпадает с источником». Ошибку не видно ни
        в сборке, ни в консоли.
        """
        kit = (ROOT / 'src' / 'components' / 'ui' / 'ios.jsx').read_text(encoding='utf-8')
        block = re.search(r'const BADGE_TONES = \{(.*?)\};', kit, re.S)
        self.assertIsNotNone(block, 'в UI-ките нет BADGE_TONES')
        known = set(re.findall(r'^\s*([a-z]+):', block.group(1), re.M))
        used = set(re.findall(r"tone: '([a-z]+)'", self.dialog))
        self.assertTrue(used, 'в диалоге не нашлось ни одного тона')
        self.assertFalse(used - known,
                         'тона, которых нет в ките: %s' % sorted(used - known))

    def test_the_dialog_says_the_article_arrives_as_a_draft(self):
        """Обещание «ничего не публикуется само» должно стоять там, где нажимают."""
        self.assertIn('ЧЕРНОВИКОМ', self.dialog)

    def test_overwrite_button_appears_only_on_conflict(self):
        """Единственное действие, затирающее работу человека, — не по умолчанию."""
        self.assertIn("item.last_status === 'conflict'", self.dialog)
        self.assertLess(self.dialog.index("item.last_status === 'conflict'"),
                        self.dialog.index('onClick={onForce}'),
                        'кнопка «Переписать» стоит вне проверки на конфликт')


class SchedulerTest(unittest.TestCase):
    """Ночная сверка: без неё «обновляется автоматически» остаётся обещанием."""

    def setUp(self):
        self.monolith = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')

    def test_job_is_registered_daily(self):
        self.assertIn("id='wiki_yandex_pro_sync_daily'", self.monolith)
        self.assertIn('run_wiki_yandex_pro_sync_async,', self.monolith)
        self.assertIn("CronTrigger(hour=_env_int('WIKI_YANDEX_PRO_SYNC_HOUR'",
                      self.monolith)

    def test_job_runs_in_its_own_pool(self):
        """Обход — десятки запросов наружу; в общем пуле он занял бы четверть приложения."""
        self.assertIn("yandex_pro_pool = ThreadPoolExecutor(max_workers=1",
                      self.monolith)
        self.assertIn('run_in_executor(yandex_pro_pool, wiki_yandex_pro_sync_job)',
                      self.monolith)

    def test_job_never_lets_an_exception_escape(self):
        """Упавшая джоба не должна валить планировщик — у соседей так же."""
        body = self.monolith[self.monolith.index('def wiki_yandex_pro_sync_job('):]
        body = body[:body.index('async def run_wiki_yandex_pro_sync_async')]
        self.assertIn('except Exception:', body)
        self.assertIn('logging.exception', body)


if __name__ == '__main__':
    unittest.main()
