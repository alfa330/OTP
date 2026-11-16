# -*- coding: utf-8 -*-
"""Разбор страниц auto-list базы знаний Яндекс Про (wiki/classifier_source.py).

Фикстуры tests/data/classifier/<город>.json — НАСТОЯЩИЕ состояния страниц
16.11.2026, урезанные до нескольких строк в таблице: все особые строки
(«Другие модели», остатки марок, периоды, оговорки, «<p>» в ячейке, «Lynk&Co»)
и вся разметка компонентов (allowed_ids, флаги скрытия) — как у источника.
Без сети и без базы.
"""

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from wiki import classifier_source as source

DATA = Path(__file__).resolve().parent / 'data' / 'classifier'
TODAY = date(2026, 11, 16)


def state(code):
    return json.loads((DATA / ('%s.json' % code)).read_text(encoding='utf-8'))


def components(page):
    return page['props']['initialProps']['pageProps']['data']['article']['text_components']


class RequirementTest(unittest.TestCase):
    def test_plain_forms(self):
        self.assertEqual(source.parse_requirement('от 2015'), {'year': 2015, 'text': None, 'except': None})
        self.assertEqual(source.parse_requirement('<p>от&nbsp;2015 г.</p>')['year'], 2015)
        self.assertEqual(source.parse_requirement('не допускается'), {'year': 0, 'text': None, 'except': None})
        # Шапка «Год» у Темиртау и Жезказгана: ячейка бывает голым годом.
        self.assertEqual(source.parse_requirement('2014')['year'], 2014)

    def test_exception_of_registered_cars(self):
        cell = ('не допускается (кроме машин 2024-2026 годов, которые были зарегистрированы* '
                'в сервисе не позднее 30 июня 2026)')
        self.assertEqual(source.parse_requirement(cell),
                         {'year': 0, 'text': cell, 'except': [2024, 2026, '2026-06-30']})
        also = ('от 2024 (а также машины 2023 года, которые были зарегистрированы в сервисе не '
                'позднее 22 января 2026)')
        self.assertEqual(source.parse_requirement(also)['except'], [2023, 2023, '2026-01-22'])
        self.assertEqual(source.parse_requirement(also)['year'], 2024)

    def test_missing_closing_bracket(self):
        """Семей, Chevrolet Onix: у ячейки нет закрывающей скобки — исключение всё равно читается."""
        cell = ('не допускается (кроме машин 2024-2026 годов, которые были зарегистрированы* в '
                'сервисе не позднее 23 июля 2026')
        self.assertEqual(source.parse_requirement(cell)['except'], [2024, 2026, '2026-07-23'])

    def test_condition_without_exception(self):
        cell = 'от 2022 (только комплектации с сидениями, не в спортивном стиле)'
        self.assertEqual(source.parse_requirement(cell), {'year': 2022, 'text': cell, 'except': None})

    def test_unreadable_cell_is_kept_as_text(self):
        self.assertEqual(source.parse_requirement('по согласованию'),
                         {'year': None, 'text': 'по согласованию', 'except': None})


class NameTest(unittest.TestCase):
    def test_multiword_brands(self):
        self.assertEqual(source.split_brand('LADA (ВАЗ) Granta'), ('LADA (ВАЗ)', 'Granta'))
        self.assertEqual(source.split_brand('LADA (ВАЗ) 2121 (4x4)'), ('LADA (ВАЗ)', '2121 (4x4)'))
        self.assertEqual(source.split_brand('Lynk&Co 08'), ('Lynk & Co', '08'))
        self.assertEqual(source.split_brand('Lynk & Co 01'), ('Lynk & Co', '01'))
        self.assertEqual(source.split_brand('Land Rover Range Rover'), ('Land Rover', 'Range Rover'))
        self.assertEqual(source.split_brand('Mercedes-Benz 190 (W201)'), ('Mercedes-Benz', '190 (W201)'))
        self.assertEqual(source.split_brand('ИЖ 2126 «Ода»'), ('ИЖ', '2126 «Ода»'))

    def test_brand_ends_at_a_word_boundary(self):
        self.assertEqual(source.split_brand('Landwind X7'), ('Landwind', 'X7'))

    def test_model_key_reads_cyrillic_code_letters_as_latin(self):
        self.assertEqual(source.model_key('Х70'), source.model_key('X70'))
        self.assertEqual(source.model_key('Lynk & Co'), source.model_key('Lynk&Co'))
        # Русское слово остаётся русским.
        self.assertEqual(source.model_key('Patriot Фургон'), 'patriotфургон')

    def test_period_rows(self):
        self.assertEqual(source.split_period('Changan Alsvin (после 15 августа 2025)'),
                         ('Changan Alsvin', ('after', '2025-08-15')))
        before = source.split_period('Changan Alsvin (до 15 августа 2025)')[1]
        self.assertFalse(source.period_active(before, TODAY))
        self.assertTrue(source.period_active(before, date(2025, 8, 14)))
        self.assertTrue(source.period_active(('after', '2025-08-15'), TODAY))
        self.assertEqual(source.split_period('Ford Focus (North America)'),
                         ('Ford Focus (North America)', None))

    def test_tariff_titles(self):
        self.assertEqual(source.tariff_of('Тариф «Комфорт+»'), ('comfort_plus', None))
        self.assertEqual(source.tariff_of('Тариф «Комфорт»'), ('comfort', None))
        self.assertEqual(source.tariff_of('Тариф «Комфорт» (новые правила с 23 июля 2026 года)'),
                         ('comfort', '2026-07-23'))
        self.assertEqual(source.tariff_of('Тариф «Ultima: тариф Premier»'), ('ultima', None))
        self.assertEqual(source.tariff_of('Тариф «Курьер»'), (None, None))


class PageTest(unittest.TestCase):
    def test_kazakhstan_cities_come_from_the_page(self):
        cities = source.kazakhstan_cities(state('almaty'))
        codes = {city['code']: city for city in cities}
        self.assertEqual(len(cities), 25)
        self.assertEqual((codes['almaty']['yandex_id'], codes['nur-sultan']['name']), (12, 'Астана'))
        self.assertIn('rudniy', codes)

    def test_audience_is_not_applied(self):
        """«Комфорт+» в Актау подписан аудиторией без Актау — а на странице он есть
        (сервер Яндекса уже собрал её под город, фронт метки не читает)."""
        page = state('aktau')
        plus = next(item for item in components(page)
                    if (item.get('values') or {}).get('title') == 'Тариф «Комфорт+»')
        self.assertNotIn('city-246', plus['values']['allowed_ids'])
        parsed = source.parse_city_page(page, today=TODAY)
        self.assertEqual(sorted(parsed['tariffs']), ['comfort', 'comfort_plus', 'econom', 'intercity'])
        self.assertTrue(parsed['tariffs']['comfort_plus']['rows'])

    def test_hidden_components_are_skipped_in_every_spelling(self):
        for flag in ('is_hide', 'isHide', 'is_hidden'):
            with self.subTest(flag=flag):
                page = copy.deepcopy(state('aktau'))
                plus = next(item for item in components(page)
                            if (item.get('values') or {}).get('title') == 'Тариф «Комфорт+»')
                plus['values'][flag] = True
                self.assertNotIn('comfort_plus', source.parse_city_page(page, today=TODAY)['tariffs'])

    def test_text_rule_any_brand(self):
        aksay = source.parse_city_page(state('aksay'), today=TODAY)
        self.assertEqual(aksay['tariffs']['econom']['any'], 1988)
        self.assertFalse(aksay['tariffs']['econom']['rows'])
        semey = source.parse_city_page(state('semey'), today=TODAY)
        self.assertEqual((semey['tariffs']['econom']['any'], semey['tariffs']['intercity']['any']),
                         (1986, 1986))
        self.assertTrue(any('кроме минивенов' in note for note in semey['tariffs']['intercity']['notes']))

    def test_new_rules_replace_the_old_table_once_in_force(self):
        uralsk = state('uralsk')
        now = source.parse_city_page(uralsk, today=TODAY)['tariffs']['comfort']
        self.assertIn('новые правила', now['title'])
        self.assertIsNone(now['next'])
        before = source.parse_city_page(uralsk, today=date(2026, 7, 1))['tariffs']['comfort']
        self.assertNotIn('новые правила', before['title'])
        self.assertEqual(before['next']['from'], '2026-07-23')

    def test_general_text_and_footnote(self):
        page = source.parse_city_page(state('almaty'), today=TODAY)
        self.assertEqual(len(page['general']), 1)            # второй абзац лежит внутри первого
        self.assertTrue(page['general'][0].startswith('У сервиса Яндекс Такси есть общие требования'))
        self.assertTrue(page['footnote'].startswith('Зарегистрированным в сервисе считается'))
        self.assertEqual(page['updated'], '2 нояб 2026')

    def test_not_a_classifier_page(self):
        with self.assertRaises(source.ClassifierSourceError):
            source.parse_city_page({'props': {}}, today=TODAY)
        with self.assertRaises(source.ClassifierSourceError):
            source.extract_state('<html>страница входа</html>')


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {code: state(code) for code in ('almaty', 'aktau', 'uralsk', 'semey', 'aksay')}
        cls.cities = source.kazakhstan_cities(cls.pages['almaty'])
        cls.data, cls.warnings = source.build(cls.pages, today=TODAY, checked_at='2026-11-16T12:00:00+05:00',
                                              cities=cls.cities)

    def city(self, city_id):
        return next(item for item in self.data['cities'] if item['id'] == city_id)

    def car(self, brand, model):
        return self.data['cars'].index([brand, model])

    def row(self, city_id, key, brand, model):
        table = self.data['lists'][self.city(city_id)['tariffs'][key]['list']]
        number = self.car(brand, model)
        return next((row for row in table['rows'] if row[0] == number), None)

    def test_shape_and_order(self):
        self.assertEqual(self.data['version'], source.VERSION)
        self.assertEqual([city['id'] for city in self.data['cities']],
                         ['almaty', 'semey', 'uralsk', 'aktau', 'aksay'])     # CITY_ORDER
        self.assertEqual(self.city('almaty')['url'], source.city_url('almaty'))
        self.assertEqual(self.data['source']['updated'], '2 нояб 2026')

    def test_same_table_is_stored_once(self):
        """Межгород Алматы и Актау — одна таблица Яндекса, и в справочнике она одна."""
        self.assertEqual(self.city('almaty')['tariffs']['intercity']['list'],
                         self.city('aktau')['tariffs']['intercity']['list'])

    def test_leftover_rows_are_rules(self):
        table = self.data['lists'][self.city('almaty')['tariffs']['intercity']['list']]
        self.assertEqual(table['other'], [1999])
        self.assertEqual(table['brands'].get('LADA (ВАЗ)'), [0])
        self.assertEqual(table['brands'].get('Москвич'), [0])
        names = [' '.join(pair) for pair in self.data['cars']]
        self.assertFalse([name for name in names if 'кроме' in name or 'Другие' in name])

    def test_one_car_one_entry(self):
        lynk = [pair for pair in self.data['cars'] if pair[0] == 'Lynk & Co']
        self.assertTrue(lynk)
        self.assertFalse([pair for pair in self.data['cars'] if pair[0] in ('Lynk&Co', '<p>Chery')])
        self.assertIn(['Chery', 'Arrizo 6'], self.data['cars'])

    def test_period_row_of_today(self):
        """«Changan Alsvin (после 15 августа 2025)» — действующая строка, «до» — нет."""
        row = self.row('almaty', 'comfort', 'Changan', 'Alsvin')
        self.assertEqual(row[1], 0)
        self.assertEqual(self.data['reqs'][row[2]]['except'], [2021, 2025, '2025-08-15'])

    def test_exception_rows_keep_the_cell_text(self):
        row = self.row('aktau', 'comfort', 'Chevrolet', 'Cobalt')
        req = self.data['reqs'][row[2]]
        self.assertEqual((row[1], req['except']), (0, [2024, 2026, '2026-06-30']))
        self.assertIn('зарегистрированы*', req['text'])

    def test_rules_and_notes(self):
        aksay = self.city('aksay')['tariffs']
        self.assertEqual(list(aksay), ['econom'])
        self.assertEqual(aksay['econom']['any'], 1988)
        self.assertTrue(self.data['notes'][aksay['econom']['notes'][0]].startswith('В тарифе могут работать'))

    def test_fingerprint_ignores_the_time_of_the_check(self):
        again, _warnings = source.build(self.pages, today=TODAY, checked_at='2026-11-17T05:40:00+05:00',
                                        cities=self.cities)
        self.assertEqual(source.fingerprint(again), source.fingerprint(self.data))
        changed = copy.deepcopy(self.data)
        changed['lists'][0]['rows'][0][1] += 1
        self.assertNotEqual(source.fingerprint(changed), source.fingerprint(self.data))

    def test_unknown_tariff_goes_to_warnings(self):
        page = copy.deepcopy(self.pages['aksay'])
        components(page).append({'type': 'AccordionStart', 'values': {'title': 'Тариф «Курьер»'},
                                 'children': []})
        _data, warnings = source.build({'aksay': page}, today=TODAY, cities=self.cities)
        self.assertTrue([line for line in warnings if 'Курьер' in line])

    def test_bundled_file_is_this_form(self):
        bundled = json.loads((Path(__file__).resolve().parents[1] / 'wiki' / 'data'
                              / 'classifier-data.json').read_text(encoding='utf-8'))
        self.assertEqual(bundled['version'], source.VERSION)
        self.assertEqual(sorted(bundled), sorted(self.data))


if __name__ == '__main__':
    unittest.main()
