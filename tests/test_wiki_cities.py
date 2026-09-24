# -*- coding: utf-8 -*-
"""Вкладка «Города» (задача #322): тарифы Яндекс Go по городу и ручные поля.

Набор держит пять вещей, и каждая ловит свой класс поломки:

1. РАЗБОР (YandexTariffParseTest) — страница тарифов Яндекс Go складывается в
   плоский вид правильно: цена маршрута не теряется в «прочем», «Далее по
   городу» из двух строк читается одной ценой, заказ по телефону — отличиями.
2. РУЧНЫЕ ПОЛЯ (CityFieldTest) — опечатка в комиссии отказывает, а не
   сохраняется пустотой.
3. ГРАНИЦА ПРОСТРАНСТВА (CitySpaceGuardTest, CitySqlScopeTest, CityRouteTest) —
   у Таксопарков и Тез свои города: у каждой функции справочника есть
   space_id, и он доходит до SQL; роут отвечает «не найдено» на чужой город.
4. СВЕРКА (CitySyncTest) — неизменившийся источник не трогает слепок, ошибка
   его не стирает, обход качает страницы без открытого соединения.
5. РАЗВЁРТЫВАНИЕ (CitySchemaTest) — таблица до журнала, сид один раз,
   ночная сверка зарегистрирована.
"""

import ast
import inspect
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

from wiki import cities as wiki_cities  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki import yandex_tariffs  # noqa: E402

SPACE = 11


# ── Фикстура страницы ────────────────────────────────────────────────────────

def _price(name, price, group='main', code='x'):
    return {'id': code, 'name': name, 'visual_group': group, 'price': price}


CITY_PRICES = [
    _price('Минимальная стоимость (включено 3 мин и 1 км)', '400 $SIGN$$CURRENCY$',
           code='taximeter.min_price_included_distance_and_time'),
    _price('Бесплатное ожидание', '2 мин', code='free_waiting'),
    _price('Платное ожидание (не включено в минимальную стоимость!)', '40 $SIGN$$CURRENCY$/мин',
           code='paid_waiting'),
    _price('Далее по городу', '58 $SIGN$$CURRENCY$/км', code='taximeter.meter_next_inside_area'),
    _price('Далее по городу', '27 $SIGN$$CURRENCY$/мин', code='taximeter.meter_next_inside_area'),
    _price('Количество бесплатного ожидания в пути (в секундах)', '600', 'other',
           'free_waiting_in_transit_1'),
    _price('Общаюсь текстом', '0 $SIGN$$CURRENCY$', 'other', 'communicate_only_in_chat'),
    _price('Детское кресло', '300 $SIGN$$CURRENCY$', 'other', 'childchair_v2'),
    _price('Подача с парковки аэропорта', '265 тенге', 'other', 'extra_fee_params_1'),
]

ROUTE_PRICES = [
    _price('Город — Аэропорт(включено 2 ч)', '1900 $SIGN$$CURRENCY$', 'other', 'fixed_route'),
    _price('Далее', '10 $SIGN$$CURRENCY$/мин', 'other', 'taximeter.meter_next'),
    _price('От двери до двери', '225 $SIGN$$CURRENCY$', 'other', 'door_to_door'),
]


def _zonal(**overrides):
    econom_app = {'name': 'Тариф «Круглосуточно»', 'title': 'ежедневно',
                  'category_type': 'application',
                  'price_groups': [
                      {'id': 'free_route', 'name': 'По городу (Актау)', 'prices': CITY_PRICES},
                      {'id': 'to_airport', 'name': 'В аэропорт (Город — Аэропорт)',
                       'prices': ROUTE_PRICES},
                      # Тот же маршрут второй группой — у Яндекса так «туда» и
                      # «из города»: в карточке он нужен один раз.
                      {'id': 'from_city', 'name': 'Из Актау (Город — Аэропорт)',
                       'prices': ROUTE_PRICES},
                  ]}
    econom_phone = dict(econom_app, category_type='call_center', price_groups=[
        {'id': 'free_route', 'name': 'По городу (Актау)', 'prices': [
            CITY_PRICES[0],
            _price('Бесплатное ожидание', '4 мин', code='free_waiting'),
            CITY_PRICES[2], CITY_PRICES[3], CITY_PRICES[4],
            _price('Надбавка за заказ с помощью телефона', '80 $SIGN$$CURRENCY$',
                   code='fix_charge'),
        ]},
    ])
    comfort = {'name': 'Комфорт', 'class': 'business', 'intervals': [econom_app]}
    zonal = {
        'currency_rules': {'sign': '₸'},
        'zoneName': 'aktau',
        'isZoneUnsupported': False,
        'callCenter': {'phone': '+77751113333', 'formattedPhone': '+7 (775) 111-33-33'},
        'max_tariffs': [
            {'name': 'Эконом', 'class': 'econom', 'intervals': [econom_app, econom_phone]},
            comfort,
        ],
    }
    zonal.update(overrides)
    return zonal


def _page(zonal=None):
    state = {'tjson': {}, 'initialState': {'zonaltariffdescription': zonal or _zonal()}}
    return ('<html><script>window.x=1;</script><script>__init__.default('
            + json.dumps(state, ensure_ascii=False) + ')</script></html>')


class YandexTariffParseTest(unittest.TestCase):

    def test_canonical_url_drops_tails_and_refuses_strangers(self):
        canon = yandex_tariffs.canonical_url
        # Ссылка из файла постановки ведёт на класс тарифа, данные — те же.
        self.assertEqual(canon('https://taxi.yandex.kz/ust_kamenogorsk/tariff/econom/'),
                         'https://taxi.yandex.kz/ust_kamenogorsk/tariff')
        self.assertEqual(canon('taxi.yandex.ru/almaty/tariff?utm=1#x'),
                         'https://taxi.yandex.ru/almaty/tariff')
        self.assertEqual(canon('https://taxi.yandex.kz/ru_kz/chimkent/tariff'),
                         'https://taxi.yandex.kz/ru_kz/chimkent/tariff')
        self.assertIsNone(canon('https://example.com/almaty/tariff'))
        self.assertIsNone(canon('https://taxi.yandex.ru/almaty'))
        self.assertIsNone(canon('https://taxi.yandex.ru/tariff'))
        self.assertIsNone(canon(''))
        self.assertEqual(yandex_tariffs.zone_of_url('https://taxi.yandex.kz/ru_kz/chimkent/tariff'),
                         'chimkent')

    def test_city_rows_are_read_as_one_price_each(self):
        data = yandex_tariffs.parse_page(_page())
        self.assertEqual(data['zone'], 'aktau')
        self.assertEqual(data['phone'], '+7 (775) 111-33-33')
        econom = data['tariffs'][0]
        self.assertEqual(econom['from'], '400 ₸')
        rows = {row['label']: row for row in econom['intervals'][0]['rows']}
        self.assertEqual(rows['Минимальная стоимость']['note'], 'включено 3 мин и 1 км')
        # Две строки Яндекса — одна цена в карточке.
        self.assertEqual(rows['Далее по городу']['value'], '58 ₸/км + 27 ₸/мин')
        # Секунды — минутами, и это цена поездки, а не опция.
        self.assertEqual(rows['Бесплатное ожидание в пути']['value'], '10 мин')
        # Восклицательный знак источника уточнению не нужен.
        self.assertEqual(rows['Платное ожидание']['note'], 'не включено в минимальную стоимость')

    def test_options_are_split_into_paid_and_free(self):
        options = {o['label']: o for o in
                   yandex_tariffs.parse_page(_page())['tariffs'][0]['intervals'][0]['options']}
        self.assertTrue(options['Общаюсь текстом']['free'])
        self.assertFalse(options['Детское кресло']['free'])
        # «тенге» словом — тем же знаком, что и остальные цены.
        self.assertEqual(options['Подача с парковки аэропорта']['value'], '265 ₸')
        self.assertIn('От двери до двери', options)

    def test_fixed_route_keeps_its_price_and_is_not_duplicated(self):
        """Цена маршрута лежит у Яндекса в «прочем» рядом с детским креслом;
        по группе показа она ушла бы в опции, и маршрут остался бы без цены."""
        routes = yandex_tariffs.parse_page(_page())['tariffs'][0]['intervals'][0]['routes']
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['title'], 'Город — Аэропорт')
        self.assertEqual(routes[0]['value'], '1900 ₸')
        self.assertEqual(routes[0]['note'], 'включено 2 ч')
        self.assertEqual(routes[0]['rows'][0], {'label': 'Далее', 'value': '10 ₸/мин', 'note': None})

    def test_phone_order_is_shown_as_differences(self):
        data = yandex_tariffs.parse_page(_page())
        econom, comfort = data['tariffs']
        self.assertTrue(econom['by_phone'])
        self.assertEqual([(r['label'], r['value']) for r in econom['phone_diff']],
                         [('Бесплатное ожидание', '4 мин'),
                          ('Надбавка за заказ с помощью телефона', '80 ₸')])
        self.assertFalse(comfort['by_phone'])
        self.assertEqual(comfort['phone_diff'], [])

    def test_refusals_speak_human(self):
        with self.assertRaises(yandex_tariffs.TariffSourceError):
            yandex_tariffs.parse_page('<html>капча</html>')
        with self.assertRaises(yandex_tariffs.TariffSourceError):
            yandex_tariffs.parse_page(_page(_zonal(isZoneUnsupported=True)))
        with self.assertRaises(yandex_tariffs.TariffSourceError):
            yandex_tariffs.parse_page(_page(_zonal(max_tariffs=[])))

    def test_fingerprint_follows_content_not_bytes(self):
        first = yandex_tariffs.parse_page(_page())
        # Та же страница с другой обвязкой — тот же отпечаток.
        second = yandex_tariffs.parse_page('<!-- build 2 -->' + _page())
        self.assertEqual(yandex_tariffs.fingerprint(first), yandex_tariffs.fingerprint(second))
        changed = _zonal()
        changed['callCenter'] = {'formattedPhone': '+7 (700) 000-00-00'}
        self.assertNotEqual(yandex_tariffs.fingerprint(first),
                            yandex_tariffs.fingerprint(yandex_tariffs.parse_page(_page(changed))))

    def test_fetch_refuses_foreign_host_before_network(self):
        with self.assertRaises(yandex_tariffs.TariffSourceError):
            yandex_tariffs.fetch_page('https://evil.example/almaty/tariff')


class CityFieldTest(unittest.TestCase):

    def test_percent_accepts_russian_decimal_and_refuses_typos(self):
        parse = wiki_cities.parse_percent
        self.assertEqual(parse('4,5'), 4.5)
        self.assertEqual(parse('12 %'), 12.0)
        self.assertEqual(parse(7), 7.0)
        self.assertIsNone(parse(''))
        self.assertIsNone(parse(None))
        for bad in ('сто', '4.5.1', '101', '-1'):
            with self.assertRaises(wiki_cities.CityFieldError, msg=bad):
                parse(bad)

    def test_tariff_meta_keeps_only_filled_entries_with_codes(self):
        meta = wiki_cities.clean_tariff_meta({
            'econom': {'commission': '12', 'requirement': '  Авто от 2007 года ', 'hidden': False},
            'vip': {'hidden': True},
            'business': {'commission': '', 'requirement': ''},
            'BAD KEY': {'commission': 5},
            'express': 'не словарь',
        })
        self.assertEqual(sorted(meta), ['econom', 'vip'])
        self.assertEqual(meta['econom'], {'commission': 12.0, 'requirement': 'Авто от 2007 года',
                                          'hidden': False})
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_tariff_meta({'econom': {'commission': 'много'}})

    def test_lists_drop_nameless_rows_and_cap_length(self):
        self.assertEqual(wiki_cities.clean_services([{'title': 'Аренда авто', 'note': ''},
                                                     {'title': ' '}]),
                         [{'title': 'Аренда авто', 'note': None}])
        self.assertEqual(wiki_cities.clean_extra_tariffs([{'name': 'Свой', 'price': 'от 350 ₸'}]),
                         [{'name': 'Свой', 'commission': None, 'requirement': None,
                           'price': 'от 350 ₸'}])
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_services([{'title': 'x%d' % i} for i in range(25)])
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_services('строка')


# ── Граница пространства ─────────────────────────────────────────────────────

SCOPED_TABLES = ('wiki_cities', 'wiki_offices', 'wiki_city_offices')
EXEMPT = {
    'due_cities': 'ночной обход идёт по всем пространствам: пространство здесь — '
                  'часть ответа, а запись результата (store_tariffs) идёт через него',
}


def _scoped_functions(module):
    tree = ast.parse(inspect.getsource(module))
    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=False)
        tables = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                if docstring is not None and inner.value == docstring:
                    continue
                tables.update(t for t in SCOPED_TABLES if t in inner.value)
            # Колонки сводки собраны в модульную константу — функция, которая
            # её подставляет, читает те же таблицы.
            if isinstance(inner, ast.Name) and inner.id in ('_SUMMARY_COLUMNS', '_SUMMARY_FROM'):
                tables.add('wiki_cities')
        if tables:
            found[node.name] = sorted(tables)
    return found


class CitySpaceGuardTest(unittest.TestCase):
    """У каждой функции справочника городов — именованный space_id."""

    def test_every_reader_and_writer_takes_the_space(self):
        found = _scoped_functions(wiki_cities)
        self.assertGreaterEqual(len(found), 8, found)
        missing = []
        for name in found:
            if name in EXEMPT:
                continue
            parameter = inspect.signature(getattr(wiki_cities, name)).parameters.get('space_id')
            if parameter is None or parameter.kind != inspect.Parameter.KEYWORD_ONLY:
                missing.append(name)
        self.assertEqual(missing, [])

    def test_exemptions_still_exist(self):
        for name in EXEMPT:
            self.assertTrue(hasattr(wiki_cities, name), name)


class _RecordingCursor:
    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def asked_space(self, value):
        for sql, params in self.calls:
            if 'space_id' not in sql:
                continue
            flat = params.values() if isinstance(params, dict) else (params or ())
            if value in list(flat):
                return True
        return False


class CitySqlScopeTest(unittest.TestCase):

    def test_reads_and_writes_carry_the_space(self):
        for call, rows in (
            (lambda c: wiki_cities.list_cities(c, space_id=SPACE), []),
            (lambda c: wiki_cities.get_city(c, 3, space_id=SPACE), []),
            (lambda c: wiki_cities.find_by_name(c, 'Алматы', space_id=SPACE), []),
            (lambda c: wiki_cities.own_office(c, 5, space_id=SPACE), []),
            (lambda c: wiki_cities.update_city(c, 3, {'note': 'x'}, space_id=SPACE), []),
            (lambda c: wiki_cities.store_tariffs(c, 3, error='нет ответа', space_id=SPACE), []),
            (lambda c: wiki_cities.create_city(c, fields={'name': 'Алматы'}, created_by=1,
                                               space_id=SPACE), [(1,)]),
        ):
            cursor = _RecordingCursor(rows=rows)
            call(cursor)
            self.assertTrue(cursor.asked_space(SPACE), cursor.calls)

    def test_serving_office_is_joined_within_the_space(self):
        """Колонка офиса приходит из формы: на чужой офис сводка обязана
        ответить «офиса нет», а не его названием."""
        cursor = _RecordingCursor()
        wiki_cities.list_cities(cursor, space_id=SPACE)
        sql = cursor.calls[0][0]
        self.assertIn('so.space_id = c.space_id', sql)
        self.assertIn('o.space_id = c.space_id', sql)

    def test_driver_offices_stay_inside_the_space(self):
        """«Куда направлять водителя» — связь двух справочников: удалять можно
        только у города своего пространства, вставлять — только офис того же
        пространства, что и город. Чужой id не попадает в список молча."""
        cursor = _RecordingCursor()
        wiki_cities.set_city_offices(cursor, 3, [9, 5], space_id=SPACE)
        delete, insert = cursor.calls[0][0], cursor.calls[1][0]
        self.assertIn('c.space_id = %s', delete)
        self.assertIn('o.space_id = %s', insert)
        self.assertIn('c.space_id = o.space_id', insert)
        self.assertIn("NOT o.no_office", insert)
        self.assertIn('WITH ORDINALITY', insert)          # порядок отметок — порядок показа
        self.assertEqual(cursor.calls[1][1], ([9, 5], 3, SPACE))
        # Пустой список — только снять старые связи.
        cursor = _RecordingCursor()
        wiki_cities.set_city_offices(cursor, 3, [], space_id=SPACE)
        self.assertEqual(len(cursor.calls), 1)

    def test_office_ids_are_cleaned(self):
        self.assertEqual(wiki_cities.clean_office_ids(['9', 5, 9, 'x', -1, None]), [9, 5])
        self.assertEqual(wiki_cities.clean_office_ids(None), [])
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_office_ids('9,5')
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_office_ids(list(range(1, 30)))

    def test_summary_lists_only_live_offices_of_the_space(self):
        cursor = _RecordingCursor()
        wiki_cities.list_cities(cursor, space_id=SPACE)
        sql = cursor.calls[0][0]
        self.assertIn('FROM wiki_city_offices co', sql)
        self.assertIn('oo.space_id = c.space_id', sql)
        self.assertIn('ORDER BY co.position', sql)

    def test_foreign_office_is_not_kept(self):
        self.assertIsNone(wiki_cities.own_office(_RecordingCursor(), 5, space_id=SPACE))
        self.assertIsNone(wiki_cities.own_office(_RecordingCursor(), None, space_id=SPACE))

    def test_changing_the_link_drops_the_old_snapshot(self):
        """Тарифы Алматы под адресом Астаны до следующей сверки — худший исход:
        выглядит как рабочая карточка и врёт."""
        cursor = _RecordingCursor()
        wiki_cities.update_city(cursor, 3, {'yandex_url': 'https://taxi.yandex.ru/astana/tariff'},
                                space_id=SPACE)
        sql = cursor.calls[-1][0]
        self.assertIn('yandex_data = CASE WHEN yandex_url IS NOT DISTINCT FROM', sql)
        self.assertIn('yandex_checked_at = CASE', sql)


class CitySyncTest(unittest.TestCase):

    def test_unchanged_source_does_not_touch_the_snapshot(self):
        cursor = _RecordingCursor(rows=[('abc',)])
        status = wiki_cities.store_tariffs(cursor, 3, data={'tariffs': []}, digest='abc',
                                           space_id=SPACE)
        self.assertEqual(status, 'same')
        self.assertNotIn('yandex_data =', cursor.calls[-1][0])
        self.assertNotIn('yandex_changed_at', cursor.calls[-1][0])

    def test_changed_source_rewrites_the_snapshot_and_its_date(self):
        cursor = _RecordingCursor(rows=[('old',)])
        status = wiki_cities.store_tariffs(cursor, 3, data={'zone': 'aktau'}, digest='new',
                                           space_id=SPACE)
        self.assertEqual(status, 'changed')
        self.assertIn('yandex_data = %s', cursor.calls[-1][0])
        self.assertIn('yandex_changed_at', cursor.calls[-1][0])

    def test_error_keeps_yesterdays_tariffs(self):
        cursor = _RecordingCursor()
        self.assertEqual(wiki_cities.store_tariffs(cursor, 3, error='Яндекс ответил 503',
                                                   space_id=SPACE), 'error')
        self.assertEqual(len(cursor.calls), 1)
        self.assertNotIn('yandex_data', cursor.calls[0][0])

    def test_walk_fetches_without_an_open_cursor(self):
        state = {'open': 0, 'fetched_open': []}
        cursors = []

        class DB:
            @contextmanager
            def _get_cursor(self):
                state['open'] += 1
                cursor = _RecordingCursor(rows=[] if cursors else [
                    (1, SPACE, 'https://taxi.yandex.ru/aktau/tariff'),
                    (2, 12, 'https://taxi.yandex.ru/almaty/tariff'),
                ])
                if len(cursors) >= 1:
                    cursor.rows = [(None,)]   # прежнего отпечатка нет
                cursors.append(cursor)
                try:
                    yield cursor
                finally:
                    state['open'] -= 1

        def fetch(url):
            state['fetched_open'].append(state['open'])
            if 'almaty' in url:
                raise yandex_tariffs.TariffSourceError('Яндекс ответил 503')
            return _page()

        summary = wiki_cities.sync_due(DB(), fetch=fetch, pause=0, sleep=lambda _s: None)
        self.assertEqual(summary, {'cities': 2, 'checked': 2, 'changed': 1, 'errors': 1})
        # Сеть — только при закрытом соединении.
        self.assertEqual(state['fetched_open'], [0, 0])
        # Результат пишется через пространство каждого города, а не одно на всех.
        self.assertTrue(cursors[1].asked_space(SPACE))
        self.assertTrue(cursors[2].asked_space(12))


# ── Роуты ────────────────────────────────────────────────────────────────────

def _context():
    return {'user_id': 42, 'otp_role': 'sv', 'department_id': 560, 'direction_id': None,
            'headed_department_ids': [], 'group_ids': [], 'wiki_roles': [],
            'access_mode': 'auto'}


@unittest.skipIf(Flask is None, 'flask не установлен')
class CityRouteTest(unittest.TestCase):

    def build(self, spaces, can_write=True):
        from wiki import access as wiki_access
        from wiki.routes import build_wiki_blueprint
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
        for module, name, value in (
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'spaces_for_user', lambda _c, _ctx, **_k: list(spaces)),
            (wiki_access, 'has_write_capability', lambda _caps: can_write),
        ):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)
        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _u, cursor=None: True,
            client_ip=lambda: '127.0.0.1'))
        app.config['TESTING'] = True
        return app.test_client(), cursor

    def test_foreign_space_is_not_found_on_every_door(self):
        client, _ = self.build([12])
        for method, url in (('get', '/api/wiki/cities?space_id=11'),
                            ('post', '/api/wiki/cities?space_id=11'),
                            ('get', '/api/wiki/cities/1?space_id=11'),
                            ('patch', '/api/wiki/cities/1?space_id=11'),
                            ('delete', '/api/wiki/cities/1?space_id=11'),
                            ('post', '/api/wiki/cities/1/sync?space_id=11')):
            response = getattr(client, method)(url, json={'name': 'Алматы'})
            self.assertEqual(response.status_code, 404, '%s %s' % (method, url))

    def test_two_spaces_without_parameter_is_a_bad_request(self):
        client, _ = self.build([11, 12])
        self.assertEqual(client.get('/api/wiki/cities').status_code, 400)

    def test_list_asks_the_database_about_the_space(self):
        client, cursor = self.build([11, 12])
        self.assertEqual(client.get('/api/wiki/cities?space_id=12').status_code, 200)
        asked = [call for call in cursor.execute.call_args_list
                 if 'wiki_cities' in str(call.args[0]) and '12' in str(call.args[1:])]
        self.assertTrue(asked)

    def test_reader_cannot_write(self):
        client, _ = self.build([12], can_write=False)
        self.assertEqual(client.post('/api/wiki/cities', json={'name': 'Алматы'}).status_code, 403)
        self.assertEqual(client.post('/api/wiki/cities/1/sync').status_code, 403)

    def test_bad_link_and_bad_percent_are_refused_before_writing(self):
        client, cursor = self.build([12])
        for body in ({'name': 'Алматы', 'yandex_url': 'https://example.com/almaty/tariff'},
                     {'name': 'Алматы', 'park_commission': 'сто'}):
            response = client.post('/api/wiki/cities', json=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.get_json()['code'], 'WIKI_CITY_FIELD')
        inserts = [c for c in cursor.execute.call_args_list if 'INSERT INTO wiki_cities' in str(c)]
        self.assertEqual(inserts, [])

    def test_adding_an_archived_city_brings_its_card_back(self):
        """Переключателя архива у вкладки нет (решение владельца 24.09.2026):
        добавить город снова — единственный путь назад, и он обязан вернуть ту
        же карточку, а не завести вторую пустую рядом."""
        client, cursor = self.build([12])
        cursor.fetchone.side_effect = [(25, 'archived')] + [None] * 10
        response = client.post('/api/wiki/cities', json={'name': 'Кызылорда',
                                                         'park_commission': '9'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['restored'])
        sql = [str(c.args[0]) for c in cursor.execute.call_args_list]
        self.assertFalse(any('INSERT INTO wiki_cities' in q for q in sql))
        updates = [c for c in cursor.execute.call_args_list if 'UPDATE wiki_cities' in str(c.args[0])]
        self.assertEqual(len(updates), 1)
        # Поля формы не применяются — они стёрли бы прежние комиссии и услуги.
        self.assertNotIn('park_commission', str(updates[0].args[0]))
        self.assertIn('status = %s', str(updates[0].args[0]))

    def test_active_duplicate_is_refused(self):
        client, cursor = self.build([12])
        cursor.fetchone.side_effect = [(4, 'active')] + [None] * 10
        response = client.post('/api/wiki/cities', json={'name': 'Алматы'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'WIKI_CITY_EXISTS')

    def test_driver_offices_alone_are_a_change(self):
        """Правка одного списка офисов — тоже правка: не «Нечего обновлять», и
        дата «Обновлено» сдвигается."""
        client, cursor = self.build([12])
        row = [None] * len(wiki_cities._SUMMARY_KEYS) + [None]
        row[0], row[1], row[13] = 4, 'Алматы', 'active'
        cursor.fetchone.side_effect = [tuple(row)] + [None] * 10
        response = client.patch('/api/wiki/cities/4', json={'driver_office_ids': [9, 5]})
        self.assertEqual(response.status_code, 200, response.get_json())
        sql = [str(c.args[0]) for c in cursor.execute.call_args_list]
        self.assertTrue(any('INSERT INTO wiki_city_offices' in q for q in sql))
        self.assertTrue(any('UPDATE wiki_cities SET updated_at' in q for q in sql))

    def test_driver_offices_in_wrong_shape_are_refused(self):
        client, cursor = self.build([12])
        row = [None] * len(wiki_cities._SUMMARY_KEYS) + [None]
        row[0], row[1], row[13] = 4, 'Алматы', 'active'
        cursor.fetchone.side_effect = [tuple(row)] + [None] * 10
        response = client.patch('/api/wiki/cities/4', json={'driver_office_ids': '9,5'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'WIKI_CITY_FIELD')

    def test_list_shows_only_live_cities(self):
        cursor = _RecordingCursor()
        wiki_cities.list_cities(cursor, space_id=SPACE)
        self.assertIn("c.status = 'active'", cursor.calls[0][0])


# ── Развёртывание ────────────────────────────────────────────────────────────

class CitySchemaTest(unittest.TestCase):

    def test_table_is_scoped_from_day_one(self):
        ddl = ' '.join(' '.join(wiki_schema._CITY_STATEMENTS).split())
        self.assertIn('space_id INTEGER NOT NULL REFERENCES wiki_spaces(id) ON DELETE CASCADE', ddl)
        self.assertIn('uq_wiki_cities_space_name ON wiki_cities(space_id, lower(name))', ddl)
        self.assertIn('REFERENCES wiki_offices(id) ON DELETE SET NULL', ddl)
        # Связь «куда направлять водителя» — с ключами на оба справочника.
        self.assertIn('city_id INTEGER NOT NULL REFERENCES wiki_cities(id) ON DELETE CASCADE', ddl)
        self.assertIn('office_id INTEGER NOT NULL REFERENCES wiki_offices(id) ON DELETE CASCADE', ddl)

    def test_cities_are_created_before_the_audit_reads_them(self):
        """Формула пространства журнала читает wiki_cities — таблица обязана
        появиться раньше разбора истории журнала."""
        source = inspect.getsource(wiki_schema.init_wiki_schema)
        self.assertLess(source.index('_scope_directories_to_space(cursor)'),
                        source.index('_CITY_STATEMENTS'))
        self.assertLess(source.index('_seed_cities(cursor)'),
                        source.index('_scope_audit_to_space(cursor)'))
        self.assertIn('city', wiki_schema.AUDIT_SPACE_ENTITIES)
        self.assertIn("WHEN 'city'", wiki_schema.AUDIT_SPACE_SQL)
        self.assertIn('cities', wiki_schema.SPACE_FEATURES)

    def test_seed_goes_to_the_default_space_once(self):
        cursor = _RecordingCursor(rows=[(11,), None])
        wiki_schema._seed_cities(cursor)
        inserts = [params for sql, params in cursor.calls if sql.startswith('INSERT INTO wiki_cities')]
        self.assertEqual(len(inserts), len(wiki_cities.DEFAULT_CITIES))
        self.assertTrue(all(params[0] == 11 for params in inserts))

        cursor = _RecordingCursor(rows=[(11,), (1,)])   # город уже есть
        wiki_schema._seed_cities(cursor)
        self.assertFalse(any(sql.startswith('INSERT') for sql, _ in cursor.calls))

    def test_seeded_links_are_canonical_and_names_are_unique(self):
        names = [name for name, _url in wiki_cities.DEFAULT_CITIES]
        self.assertEqual(len(names), 24)
        self.assertEqual(len(set(names)), 24)
        # Опечатка постановки («Экбастуз») исправлена: офис пишет «Экибастуз».
        self.assertIn('Экибастуз', names)
        for name, url in wiki_cities.DEFAULT_CITIES:
            self.assertEqual(yandex_tariffs.canonical_url(url), url, name)

    def test_nightly_sync_is_registered_after_start(self):
        source = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        block = source[source.index("id='wiki_city_tariffs_sync_daily'") - 600:
                       source.index("id='wiki_city_tariffs_sync_daily'") + 400]
        self.assertIn('run_wiki_city_tariffs_sync_async', block)
        self.assertIn('next_run_time', block)
        self.assertIn('yandex_pro_pool, wiki_city_tariffs_sync_job', source)

    def test_audit_labels_and_filter_know_city_actions(self):
        meta = (ROOT / 'src/components/wiki/auditEvents.js').read_text(encoding='utf-8')
        from wiki import structure as wiki_structure
        for action in ('city.create', 'city.update', 'city.archive', 'city.restore',
                       'city.sync'):
            self.assertRegex(meta, r"'%s':\s*\{\s*label:" % re.escape(action))
            self.assertIn(action, wiki_structure.AUDIT_GROUPS['places'])



class CityScrollTest(unittest.TestCase):
    """Тонкие полосы во вкладке «Города» (просьба владельца 24.09.2026).

    Три места, и в каждом полоса была толще задуманной по своей причине
    (замер в Chrome с постоянными полосами macOS): страница вики — ~11 px
    индиговая от custom-scrollbar, список городов — 6 px от правила
    .wiki-scope ::-webkit-scrollbar, выпадающий список в редакторе — штатные
    15 px."""

    def test_wiki_page_scrolls_with_the_portal_thin_thumb(self):
        css = (ROOT / 'src/styles.css').read_text(encoding='utf-8')
        rule = css[css.index('.main-content:has(> .wiki-scope)::-webkit-scrollbar,'):]
        self.assertIn('width: 3px;', rule[:rule.index('}')])
        # custom-scrollbar задаёт scrollbar-width, а с ним Chrome псевдоэлементы
        # не применяет — сброс обязан стоять, и только для WebKit/Blink.
        reset = css[css.index('@supports selector(::-webkit-scrollbar) {'):]
        reset = reset[:reset.index('}\n}') + 3]
        self.assertIn('.main-content:has(> .wiki-scope)', reset)
        self.assertIn('scrollbar-width: auto;', reset)

    def test_thin_scroll_stays_thin_inside_the_wiki(self):
        css = (ROOT / 'src/components/wiki/wiki-theme.css').read_text(encoding='utf-8')
        rule = css[css.index('.wiki-scope .thin-scroll::-webkit-scrollbar {'):]
        self.assertIn('width: 3px;', rule[:rule.index('}')])
        jsx = (ROOT / 'src/components/wiki/WikiCities.jsx').read_text(encoding='utf-8')
        self.assertRegex(jsx, r'<ul className="[^"]*thin-scroll')

    def test_ios_select_list_scrolls_thin(self):
        jsx = (ROOT / 'src/components/ui/CustomSelect.jsx').read_text(encoding='utf-8')
        self.assertIn("${isIos ? ' thin-scroll' : ''}", jsx)


if __name__ == '__main__':
    unittest.main()
