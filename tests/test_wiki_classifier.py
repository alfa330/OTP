# -*- coding: utf-8 -*-
"""«Классификатор авто» в помощнике вики: подходит ли машина под тариф.

Что стережётся (wiki/classifier.py, решение 12.10.2026):
  * годы считает код тем же правилом, что калькулятор на экране
    (max(1980, базовый год модели + поправка города)), и каждое число, которое
    может назвать помощник, лежит в тексте фрагмента буквально;
  * машина узнаётся так, как её называют операторы — по-русски, в падеже,
    кодом русскими буквами, с маркой и без, — и НЕ узнаётся в словах, которые
    просто совпали с названием модели («фамилия», «паспорт», «навигатор»);
  * фрагменты держат контракт строки ретривера, а источник под ответом — это
    сама статья-классификатор с машиной, годом и городом для калькулятора;
  * доступ — как у статьи: вне периметра помощника классификатор молчит, и
    база не тронута, пока вопрос не про машину.

Без базы и без файла экрана: данные синтетические, той же формы, что
src/components/classifier/classifier-data.json. Настоящий файл проверяется
отдельно и только на то, что от его содержания не зависит (DataFileTest).
"""

import ast
import json
import re
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from wiki import classifier
from wiki import routes_ai
from wiki.ai import answer as ai_answer
from wiki.ai import currency
from wiki.ai import store as ai_store

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 12)
ARTICLE = {'id': 36, 'title': 'Классификатор авто', 'slug': 'klassifikator-avto'}

ALL = ['econom', 'intercity', 'comfort', 'comfort_plus', 'business', 'electro', 'ultima']
COBALT_NOTE = ('до 15 августа 2025: от 2024 г. (после 15 августа 2025: не допускается, кроме '
               'машин, зарегистрированных в сервисе не позднее 15 августа 2025)')


def car(brand, model, warnings=None, **years):
    row = {'brand': brand, 'model': model, 'years': years}
    if warnings:
        row['warnings'] = warnings
    return row


DATA = {
    'cities': [
        {'id': 'almaty', 'name': 'Алматы', 'offset': 0, 'tariffs': ALL},
        {'id': 'astana', 'name': 'Астана', 'offset': 1, 'tariffs': ALL},
        {'id': 'shymkent', 'name': 'Шымкент', 'offset': -1, 'tariffs': ALL[:5]},
        {'id': 'ust-kamenogorsk', 'name': 'Усть-Каменогорск', 'offset': -1, 'tariffs': ALL[:4]},
        {'id': 'taraz', 'name': 'Тараз', 'offset': -2, 'tariffs': ALL[:3]},
        {'id': 'turkestan', 'name': 'Туркестан', 'offset': -3, 'tariffs': ALL[:3]},
    ],
    'tariffs': [
        {'key': 'econom', 'name': 'Эконом', 'description': ''},
        {'key': 'intercity', 'name': 'Межгород', 'description': ''},
        {'key': 'comfort', 'name': 'Комфорт', 'description': ''},
        {'key': 'comfort_plus', 'name': 'Комфорт+', 'description': ''},
        {'key': 'business', 'name': 'Бизнес', 'description': ''},
        {'key': 'electro', 'name': 'Электро', 'description': ''},
        {'key': 'ultima', 'name': 'Ultima (Premier)', 'description': ''},
    ],
    'cars': [
        car('Toyota', 'Camry', econom=1985, intercity=1985, comfort=2014, comfort_plus=2021,
            business=2023),
        car('Toyota', 'Corolla', econom=1985, intercity=1985, comfort=2018),
        car('Toyota', 'Alphard'),
        car('Toyota', 'Voxy'),
        car('Toyota', 'Land Cruiser', intercity=1990, comfort_plus=2018),
        car('Toyota', 'Land Cruiser Prado', intercity=1996, comfort_plus=2018),
        car('Toyota', 'RAV 4', intercity=2000, comfort=2014),
        car('Toyota', 'Progres'),
        car('Hyundai', 'Accent', econom=1995, intercity=1995, comfort=2021),
        car('Hyundai', 'Solaris', econom=2005, intercity=2010, comfort=2021),
        car('Hyundai', 'Click', econom=2002),
        car('Hyundai', 'Genesis', comfort=2013),
        car('Kia', 'Rio', econom=2000, intercity=2000, comfort=2021),
        car('Kia', 'K5', comfort=2014, comfort_plus=2021, business=2023),
        car('Kia', 'Sportage', intercity=2004, comfort=2014, comfort_plus=2023),
        car('Kia', "Cee'd", econom=2005, comfort=2018),
        car('Chevrolet', 'Cobalt', {'comfort': COBALT_NOTE}, econom=2004, intercity=2004,
            comfort=2024),
        car('Chevrolet', 'MATIZ', intercity=2004),
        car('Chevrolet', 'Nexia'),
        car('Chevrolet', 'Cruze', econom=2009),
        car('Chevrolet', 'Cruze (HR)', intercity=2001),
        car('Chevrolet', 'Volt'),
        car('Daewoo', 'Matiz', econom=1998, intercity=1998),
        car('Daewoo', 'Nexia', econom=2002, intercity=2002),
        car('Ravon', 'Matiz', intercity=2015),
        car('Lada (VAZ)', '2114', econom=1996, intercity=2001),
        car('Lada (VAZ)', 'Granta', econom=2010, intercity=2011),
        car('Lada (VAZ)', '(кроме указанных моделей)'),
        car('Mercedes-Benz', 'B-klasse', comfort=2018),
        car('Mercedes-Benz', 'C-klasse', comfort=2014, comfort_plus=2021),
        car('Mercedes-Benz', 'E-klasse', comfort=2013, comfort_plus=2018, business=2020),
        car('Mercedes-Benz', 'S-klasse', comfort=2013, business=2017, ultima=2017),
        car('Mercedes-Benz', 'GLC', comfort=2015),
        car('Mercedes-Benz', 'GLS-klasse', comfort_plus=2018),
        car('BMW', '5er', comfort=2013, comfort_plus=2018, business=2020),
        car('BMW', 'X5', comfort=2013, business=2020),
        car('BMW', '520', comfort=2013),
        car('Lexus', 'ES', comfort=2013, business=2020),
        car('Lexus', 'RX', comfort=2013, comfort_plus=2018),
        car('Tesla', 'Model 3', comfort=2017, electro=2018),
        car('Chery', 'Tiggo (T11)', econom=2005),
        car('Chery', 'Tiggo 7 Pro', comfort=2020, comfort_plus=2023),
        car('Chery', 'Tiggo 7 Pro Max', comfort=2022),
        car('Chery', 'Bonus (A13)', econom=2011),
        car('Mazda', 'Familia', econom=1990),
        car('Mazda', '6', econom=2002, comfort=2014),
        car('Honda', 'Passport'),
        car('Honda', 'Fit', econom=2001),
        car('Peugeot', '2008', comfort=2018),
        car('Peugeot', '308', econom=2007),
        car('Peugeot', 'Partner'),
        car('Ford', 'Focus', econom=1998, comfort=2018),
        car('Ford', 'Focus (North America)', intercity=2000),
        car('Jetour', 'X70', comfort=2018, comfort_plus=2023),
        car('Jetour', 'Х70', comfort=2018),                       # русская «Х», как в файле
        car('Lifan', 'X70', intercity=2018),
        car('Nissan', 'Juke', intercity=2010),
        car('Nissan', 'Qashqai', intercity=2007, comfort=2014),
        car('Lincoln', 'Navigator'),
        car('Volkswagen', 'Jetta', econom=1995, comfort=2014),
        car('Volkswagen', 'Polo', econom=2001, comfort=2021),
        car('Jetta', 'VA3', comfort=2020),
        car('Genesis', 'G80', business=2020),
        car('Geely', 'Coolray', comfort=2020),
        car('Haval', 'Jolion', comfort=2021),
        car('BYD', 'Qin Plus', {'comfort_plus': 'от 2024 (а также машины 2023 года, которые были '
                                                'зарегистрированы в сервисе не позднее 22 января 2026)'},
            comfort=2018, comfort_plus=2024, electro=2022),
        car('BYD', 'Han', business=2021),
        car('Audi', '80', econom=1982),
        car('ГАЗ', '(кроме указанных моделей)'),
        car('ГАЗ', '3102 «Волга»'),
        car('УАЗ', 'Patriot', intercity=2013),
        car('Другие модели', '', intercity=1999),
    ],
}

BOOK = classifier.build_book(DATA)


def analyze(question, context=None, *, history=None):
    with patch.object(currency, 'today', return_value=TODAY):
        return classifier.analyze_question(question, context, BOOK, history=history)


def names(question, context=None):
    """Машины, которые уйдут помощнику, либо вид фрагмента, либо None."""
    decision = classifier.plan(analyze(question, context))
    if not decision:
        return None
    if decision['kind'] == 'cars':
        return [item['name'] for item in decision['cars']]
    return decision['kind'] if decision['kind'] != 'brand' else 'brand:' + decision['brand']


def rows(question, context=None):
    return classifier.build_ai_rows(analyze(question, context), BOOK, article=ARTICLE)


def text(question, context=None):
    found = rows(question, context)
    return found[0]['text'] if found else ''


class FormulaTest(unittest.TestCase):
    """Расчёт — двойник калькулятора на экране."""

    def city(self, city_id):
        return next(item for item in BOOK['cities'] if item['id'] == city_id)

    def car(self, name):
        return next(item for item in BOOK['cars'] if item['name'] == name)

    def test_city_shifts_the_base_year(self):
        camry = self.car('Toyota Camry')
        self.assertEqual(classifier.required_year(camry, self.city('almaty'), 'comfort'), 2014)
        self.assertEqual(classifier.required_year(camry, self.city('astana'), 'comfort'), 2015)
        self.assertEqual(classifier.required_year(camry, self.city('shymkent'), 'comfort'), 2013)
        self.assertEqual(classifier.required_year(camry, self.city('turkestan'), 'comfort'), 2011)

    def test_floor_is_1980(self):
        audi = self.car('Audi 80')                                    # 1982 − 3 = 1979
        self.assertEqual(classifier.required_year(audi, self.city('turkestan'), 'econom'), 1980)
        self.assertEqual(classifier.required_year(audi, self.city('taraz'), 'econom'), 1980)
        self.assertEqual(classifier.required_year(audi, self.city('almaty'), 'econom'), 1982)

    def test_no_tariff_for_the_model_or_in_the_city(self):
        camry = self.car('Toyota Camry')
        self.assertIsNone(classifier.required_year(camry, self.city('almaty'), 'ultima'))
        # У модели «Бизнес» есть, в Таразе такого тарифа нет.
        self.assertIsNone(classifier.required_year(camry, self.city('taraz'), 'business'))

    def test_the_screen_counts_the_same_way(self):
        """Формула и пол года — те же, что в ClassifierView.jsx: разойдутся — и
        помощник назовёт год, которого нет на экране."""
        view = (ROOT / 'src' / 'components' / 'classifier' / 'ClassifierView.jsx').read_text(
            encoding='utf-8')
        self.assertIn('const YEAR_FLOOR = %s;' % classifier.YEAR_FLOOR, view)
        self.assertRegex(view, r'Math\.max\(YEAR_FLOOR, baseYear \+ cityOffset\)')
        # Тариф показывается, только когда он есть и у города, и у модели.
        self.assertIn('(city.tariffs || [])', view)
        self.assertIn('base === undefined', view)


class DataFileTest(unittest.TestCase):
    """Настоящий файл экрана: только то, что не зависит от его чисел."""

    @classmethod
    def setUpClass(cls):
        cls.data = classifier.read_data()
        cls.book = classifier.build_book(cls.data)

    def test_the_assistant_reads_the_file_of_the_screen(self):
        path = Path(classifier.DATA_PATH)
        self.assertEqual(path, ROOT / 'src' / 'components' / 'classifier' / 'classifier-data.json')
        for name in ('classifier/ClassifierView.jsx', 'wiki/WikiSearch.jsx'):
            source = (ROOT / 'src' / 'components' / name).read_text(encoding='utf-8')
            self.assertIn('classifier-data.json', source, name)

    def test_shape(self):
        self.assertGreater(len(self.book['cars']), 1000)
        self.assertGreaterEqual(len(self.book['cities']), 20)
        keys = {item['key'] for item in self.book['tariffs']}
        for city in self.book['cities']:
            self.assertTrue(set(city['tariffs']) <= keys, city['name'])
            self.assertIsInstance(city['offset'], int)
        for item in self.book['cars']:
            self.assertTrue(set(item['years']) <= keys, item['name'])
            self.assertTrue(set(item['warnings']) <= set(item['years']), item['name'])

    def test_every_car_is_found_by_its_own_name(self):
        """Имя как в классификаторе плюс марка — находится любая из полутора
        тысяч строк, и первой идёт она сама."""
        lost = []
        with patch.object(currency, 'today', return_value=TODAY):
            for item in self.book['cars']:
                question = '%s %s 2015 года комфорт' % (item['brand'], item['model'])
                decision = classifier.plan(classifier.analyze(question, self.book))
                found = decision['cars'] if decision and decision['kind'] == 'cars' else []
                if item not in found:
                    lost.append(item['name'])
        self.assertEqual(lost, [])

    def test_trap_words_stay_trapped_on_the_real_data(self):
        """Слова-ловушки проверяются на настоящих названиях: в синтетике их
        моделей может и не быть."""
        with patch.object(currency, 'today', return_value=TODAY):
            for question in ('как изменить фамилию водителя в комфорте в 2025 году',
                             'паспорт 2015 года подходит для регистрации в комфорт',
                             'навигатор не работает в комфорте с 2024 года',
                             'какой бонус в тарифе комфорт в 2025 году',
                             'партнер не выплатил бонус за комфорт в 2025 году',
                             'прогресс по акции 2025 года в комфорте',
                             'мира хочет работать в комфорте с 2024 года',
                             'территория парка 2022 года в комфорте',
                             'болт или яндекс что выгоднее в комфорте в 2025 году',
                             'какие модели подходят в комфорт'):
                with self.subTest(question=question):
                    decision = classifier.plan(classifier.analyze(question, self.book))
                    self.assertNotEqual((decision or {}).get('kind'), 'cars')

    def test_answer_does_not_depend_on_hash_order(self):
        """Порядок машин — как в файле: «с5» называет четыре модели двух
        прочтений, и набор обязан быть одним и тем же от запуска к запуску."""
        with patch.object(currency, 'today', return_value=TODAY):
            decision = classifier.plan(classifier.analyze('с5 2020 комфорт', self.book))
        orders = [item['order'] for item in decision['cars']]
        self.assertEqual(orders, sorted(orders))
        self.assertGreater(len(orders), 1)


class BookTest(unittest.TestCase):
    def test_leftover_rows_are_not_cars(self):
        listed = [item['name'] for item in BOOK['cars']]
        self.assertFalse([name for name in listed if 'кроме' in name or 'Другие' in name])
        self.assertEqual(set(BOOK['catch_all']), {'Lada (VAZ)', 'ГАЗ'})
        self.assertEqual(BOOK['other']['years'], {'intercity': 1999})

    def test_twin_row_with_a_cyrillic_letter_is_folded_into_the_full_one(self):
        jetour = [item for item in BOOK['cars'] if item['brand'] == 'Jetour']
        self.assertEqual([(item['model'], item['years']) for item in jetour],
                         [('X70', {'comfort': 2018, 'comfort_plus': 2023})])

    def test_conflicting_twins_are_both_kept(self):
        data = dict(DATA, cars=[car('Jetour', 'X70', comfort=2018), car('Jetour', 'Х70', comfort=2020)])
        self.assertEqual(len(classifier.build_book(data)['cars']), 2)

    def test_unknown_tariff_keys_are_dropped(self):
        data = dict(DATA, cars=[car('Toyota', 'Camry', comfort=2014, kids=2020)])
        self.assertEqual(classifier.build_book(data)['cars'][0]['years'], {'comfort': 2014})


class RecognitionTest(unittest.TestCase):
    """Как называют машину — так она и узнаётся."""

    def check(self, cases):
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(names(question), expected)

    def test_brand_and_model(self):
        self.check({
            'подходит ли тойота камри 2015 в комфорт в алматы': ['Toyota Camry'],
            'Toyota Camry': ['Toyota Camry'],
            'kia rio 2014 эконом астана': ['Kia Rio'],
            'хендай акцент 2012 комфорт шымкент': ['Hyundai Accent'],
            'лада гранта 2015 эконом': ['Lada (VAZ) Granta'],
            'ваз 2114 какого года берут': ['Lada (VAZ) 2114'],
            'хавал джолион 2022 в комфорт': ['Haval Jolion'],
            'бид цинь плюс 2024 комфорт+': ['BYD Qin Plus'],
            'ниссан кашкай 2016 в комфорт туркестан': ['Nissan Qashqai'],
            'фольксваген поло 2019 комфорт': ['Volkswagen Polo'],
            'мазда 6 2015 комфорт': ['Mazda 6'],
            'уаз патриот 2015 межгород': ['УАЗ Patriot'],
            'газ волга эконом': ['ГАЗ 3102 «Волга»'],
        })

    def test_model_alone(self):
        self.check({
            'камри 2015 года проходит в комфорт?': ['Toyota Camry'],
            'камри': ['Toyota Camry'],
            'королла': ['Toyota Corolla'],
            'с какого года королла проходит в комфорт': ['Toyota Corolla'],
            'солярис 2016 подходит в комфорт+?': ['Hyundai Solaris'],
            'кобальт 2024 комфорт': ['Chevrolet Cobalt'],
            'cobalt 2023 комфорт': ['Chevrolet Cobalt'],
            'accent 2012 comfort': ['Hyundai Accent'],
            'джолион 2022 в комфорт подойдет?': ['Haval Jolion'],
            'вокси 2015 в комфорт подходит?': ['Toyota Voxy'],
            'прадо 2015 какой тариф': ['Toyota Land Cruiser Prado'],
            'ленд крузер прадо 2015 комфорт+': ['Toyota Land Cruiser Prado'],
            'спортейдж 2019 комфорт+': ['Kia Sportage'],
            'кулрей 2021 комфорт': ['Geely Coolray'],
            'жук 2014 комфорт проходит?': ['Nissan Juke'],
        })

    def test_one_model_name_under_several_brands(self):
        self.check({
            'матиз подходит в эконом?': ['Chevrolet MATIZ', 'Daewoo Matiz', 'Ravon Matiz'],
            'нексия 2010 эконом': ['Chevrolet Nexia', 'Daewoo Nexia'],
            'х70 2021 комфорт': ['Jetour X70', 'Lifan X70'],
            # С маркой — одна.
            'дэу матиз 2005 эконом': ['Daewoo Matiz'],
            'лифан х70 2019 межгород': ['Lifan X70'],
        })

    def test_codes_typed_with_russian_letters(self):
        self.check({
            'к5 2021 в бизнес': ['Kia K5'],
            'киа к5 2021 бизнес астана': ['Kia K5'],
            'рав4 2017 комфорт': ['Toyota RAV 4'],
            'тойота рав 4 2017 комфорт': ['Toyota RAV 4'],
            'бмв х5 2019 бизнес': ['BMW X5'],
            '2114 2008 года эконом можно?': ['Lada (VAZ) 2114'],
        })

    def test_names_a_brand_reads_its_own_way(self):
        self.check({
            'мерседес е класс 2019 бизнес': ['Mercedes-Benz E-klasse'],
            'мерс s класс 2018 премьер': ['Mercedes-Benz S-klasse'],
            'мерседес е200 2017 комфорт+': ['Mercedes-Benz E-klasse'],
            'е класс 2019 в бизнес': ['Mercedes-Benz E-klasse'],
            # «глс» по буквам совпадает и с GLC: побеждает прочтение по звуку.
            'мерседес глс 2020 комфорт+': ['Mercedes-Benz GLS-klasse'],
            'мерседес глц 2018 комфорт': ['Mercedes-Benz GLC'],
            'bmw 5 серии 2018 комфорт+': ['BMW 5er'],
            'бмв 525 2015 комфорт': ['BMW 5er'],
            'бмв 520 2015 комфорт': ['BMW 520'],            # своя строка сильнее серии
            'лексус ес 250 2018 комфорт плюс': ['Lexus ES'],
            'lexus rx 350 2015': ['Lexus RX'],
            'rx350 2015 комфорт': ['Lexus RX'],
            'тесла модель 3 2022 электро': ['Tesla Model 3'],
            'тесла 3 2022 электро': ['Tesla Model 3'],
        })

    def test_longest_name_wins(self):
        self.check({
            'тигго 7 про 2022 комфорт': ['Chery Tiggo 7 Pro'],
            'чери тигго 7 про макс 2023': ['Chery Tiggo 7 Pro Max'],
            'чери тигго 2010 эконом': ['Chery Tiggo (T11)'],
            'тойота ленд крузер 200 2016 года комфорт+': ['Toyota Land Cruiser'],
        })

    def test_declension(self):
        self.check({
            'водитель на тойоте камри 2015 года, можно ли в комфорт?': ['Toyota Camry'],
            'на солярисе 2016 года можно в комфорт?': ['Hyundai Solaris'],
            'у водителя нексия 2010 года, берут в эконом?': ['Chevrolet Nexia', 'Daewoo Nexia'],
            'на королле 2019 года берут в комфорт?': ['Toyota Corolla'],
            'на кобальте 2023 года можно работать в комфорте?': ['Chevrolet Cobalt'],
            'на гранте 2018 года в эконом возьмут?': ['Lada (VAZ) Granta'],
            'на хендае акцент 2021 года в комфорт': ['Hyundai Accent'],
        })

    def test_misspelling_next_to_the_brand(self):
        # На букву-две от имени — только при названной марке.
        self.check({
            'киа спортидж 2019 комфорт': ['Kia Sportage'],
            'джили кулрэй 2021 комфорт': ['Geely Coolray'],
        })

    def test_model_with_a_year_like_name(self):
        self.check({
            'пежо 2008 в комфорт с какого года': ['Peugeot 2008'],
            'пежо 308 2008 года эконом': ['Peugeot 308'],
        })
        self.assertEqual(analyze('пежо 308 2008 года эконом')['years'], [2008])
        self.assertEqual(analyze('пежо 2008 в комфорт с какого года')['years'], [])

    def test_qualified_row_answers_only_by_its_full_name(self):
        self.check({
            'форд фокус 2012 эконом': ['Ford Focus'],
            'ford focus north america 2012 межгород': ['Ford Focus (North America)'],
            'шевроле круз 2015 эконом': ['Chevrolet Cruze'],
        })

    def test_brand_named_like_a_model_of_another_brand(self):
        self.check({
            'джетта 2015 комфорт': ['Volkswagen Jetta'],        # не марка Jetta
            'jetta va3 2021 комфорт': ['Jetta VA3'],
            'хендай генезис 2015 комфорт': ['Hyundai Genesis'],
            'дженезис g80 2021 бизнес': ['Genesis G80'],
        })

    def test_kazakh(self):
        self.check({
            'камри 2015 комфортқа жарай ма': ['Toyota Camry'],
            'кобальт 2023 жылғы комфортқа өте ме': ['Chevrolet Cobalt'],
        })

    def test_what_else_the_question_names(self):
        analysis = analyze('камри 70 2019 года комфорт+ алмата')
        self.assertEqual((analysis['cities'], analysis['tariffs'], analysis['years']),
                         (['Алматы'], ['comfort_plus'], [2019]))
        both = analyze('Камри 2014 или 2015 в комфорт?')
        self.assertEqual(both['years'], [2014, 2015])
        latin = analyze('camry 2015 comfort almaty')
        self.assertEqual((latin['cities'], latin['tariffs']), (['Алматы'], ['comfort']))
        self.assertEqual(analyze('камри в усть-каменогорске')['cities'], ['Усть-Каменогорск'])
        self.assertEqual(analyze('камри из шымкента в астану')['cities'], ['Шымкент', 'Астана'])

    def test_city_is_read_by_the_rule_of_the_directories(self):
        """Город узнаёт тот же образец, что у «Офисов» и «Городов»
        (directory.city_patterns): склонение, разговорное имя, старое название."""
        from wiki import directory
        found = {name: pattern for pattern, name in directory.city_patterns(['Алматы', 'Усть-Каменогорск'])
                 if pattern.search('из алматы в алмату и в оскемен')}
        self.assertEqual(set(found), {'Алматы', 'Усть-Каменогорск'})
        self.assertEqual(analyze('камри 2015 в комфорт в чимкенте')['cities'], ['Шымкент'])
        self.assertEqual(analyze('камри 2015 в комфорт в оскемене')['cities'], ['Усть-Каменогорск'])
        # Город, которого в классификаторе нет, — отдельно: он не «не назван».
        other = analyze('камри 2015 в комфорт в петропавловске')
        self.assertEqual((other['cities'], other['other_cities']), ([], ['Петропавловск']))

    def test_tariff_words_that_are_not_tariffs(self):
        for question in ('комфортный автомобиль', 'экономия топлива', 'бизнесмен',
                         'электромобиль byd', 'позвонить к +7 705 123 45 67'):
            with self.subTest(question=question):
                self.assertEqual(analyze(question)['tariffs'], [])
        self.assertEqual(analyze('к+ или комфорт плюс')['tariffs'], ['comfort_plus'])
        self.assertEqual(analyze('из эконома в бизнес')['tariffs'], ['econom', 'business'])

    def test_year_out_of_range_is_not_a_year(self):
        self.assertEqual(analyze('камри 2015 года')['years'], [2015])
        self.assertEqual(analyze('камри 1975 года')['years'], [])
        self.assertEqual(analyze('камри 2027 года')['years'], [2027])        # год + 1 — уже продают
        self.assertEqual(analyze('камри 2028 года')['years'], [])


class NoiseTest(unittest.TestCase):
    """Слово, совпавшее с названием модели, — ещё не машина."""

    def silent(self, questions):
        for question in questions:
            with self.subTest(question=question):
                self.assertIsNone(names(question))

    def test_model_names_that_are_ordinary_words(self):
        self.silent((
            'как изменить фамилию водителя в профиле',
            'какая фамилия у водителя на комфорте 2020 года',
            'паспорт 2015 года подходит для регистрации в комфорт',
            'навигатор не работает в комфорте с 2024 года',
            'какой бонус в тарифе комфорт в 2025 году',
            'бонусы 2025 года в комфорте',
            'партнер не выплатил бонус за комфорт',
            'прогресс по акции 2025 года в комфорте',
            'вольт подключение в алматы',
            'клик по кнопке в комфорте 2025',
        ))

    def test_those_words_are_cars_next_to_the_brand(self):
        for question, expected in {
                'мазда фамилия 2001 эконом': ['Mazda Familia'],
                'хонда паспорт 2020 в комфорт': ['Honda Passport'],
                'линкольн навигатор 2015 комфорт': ['Lincoln Navigator'],
                'чери бонус 2012 эконом': ['Chery Bonus (A13)'],
                'пежо партнер 2015 эконом': ['Peugeot Partner'],
                'тойота прогресс 2005 эконом': ['Toyota Progres'],
                'шевроле вольт 2018 электро': ['Chevrolet Volt'],
                'хендай клик 2008 эконом': ['Hyundai Click']}.items():
            with self.subTest(question=question):
                self.assertEqual(names(question), expected)

    def test_question_about_something_else_that_mentions_a_car(self):
        self.silent((
            'водитель на тойоте камри попал в дтп что делать',
            'водитель на тойоте камри 2015 года попал в дтп и клиент пострадал что делать оператору',
            'водитель на тойоте камри жалуется что мало заказов в комфорте',
            'водитель на камри жалуется что мало заказов в комфорте',
            'водитель на киа жалуется на мало заказов в комфорте',
            'кобальт не проходит фотоконтроль',
            'водитель на кобальте не проходит фотоконтроль',
            'сколько стоит аренда камри в день',
            'аренда солярис условия',
            'где взять в аренду кобальт',
            'сделай акцент на тарифе комфорт',
            'позвонить водителю на камри к +7 705 123 45 67',
        ))

    def test_tariff_questions_without_a_car(self):
        self.silent((
            'как подключить тариф комфорт',
            'комиссия яндекса в комфорте в алматы',
            'с какого года действует тариф комфорт',
            'какой год рождения нужен для регистрации',
            'почему водителя перевели с комфорта на эконом',
            'телефон 2019 года подходит для работы в комфорте',
            'можно ли работать на арендной машине в комфорте',
            'можно ли белую машину в бизнес',
            'какие документы на авто нужны для комфорта',
            'какие машины есть в аренде в алматы',
            'какие автомойки работают в алматы',
            'машина с правым рулем подходит?',
            'адрес офиса в астане',
        ))

    def test_another_topic_owns_the_question(self):
        # Цена аренды, комиссия, фотоконтроль: машина, год и тариф стоят в
        # вопросе по своей причине.
        self.silent((
            'аренда камри 2020 года в комфорте сколько стоит',
            'сколько стоит аренда кобальт 2023 комфорт',
            'какая комиссия у яндекса на тойоту камри в комфорте',
            'водитель на кобальте 2020 года не проходит фотоконтроль в комфорте',
            'штраф за камри 2015 в комфорте',
        ))
        # …пока про год машины не спросили прямо.
        self.assertEqual(names('арендная камри 2020 подходит в комфорт?'), ['Toyota Camry'])
        self.assertEqual(names('с какого года камри проходит в комфорт и какая там комиссия'),
                         ['Toyota Camry'])
        self.assertEqual(names('классификатор авто и комиссия'), 'general')
        # «Подходит» рядом с фотоконтролем — про фотоконтроль, а не про тариф.
        self.assertIsNone(names('камри 2015 подходит в комфорт после фотоконтроля?'))

    def test_a_narrative_with_car_year_and_tariff_is_still_about_the_car(self):
        # Камри 2012 года в «Комфорт» не проходит — отсюда и «мало заказов».
        self.assertEqual(names('водитель на камри 2012 жалуется что мало заказов в комфорте'),
                         ['Toyota Camry'])

    def test_vaz_number_without_the_brand_may_be_a_sum(self):
        self.assertEqual(analyze('2114')['cars'][0]['level'], classifier.PROBABLE)
        self.silent(('заказ на 2114 тенге в комфорте', '2114 тенге комфорт'))
        self.assertEqual(names('2114 эконом'), ['Lada (VAZ) 2114'])

    def test_ordinary_word_brands_need_a_model(self):
        self.silent(('газ или бензин что лучше для комфорта', 'джип подходит в комфорт?',
                     'мини тариф комфорт', 'танк 2024 года'))

    def test_question_words_are_not_car_names(self):
        # «модели» по звучанию — Tesla Model Y, «класс» с предлогом — Mercedes B и C.
        self.assertEqual(names('какие модели тойота подходят в бизнес'), 'brand:Toyota')
        self.assertIsNone(names('в класс комфорт перевели водителя'))
        self.assertIsNone(names('с класс комфорт что изменилось'))


class AcceptTest(unittest.TestCase):
    """Чем слабее названа машина, тем больше обязан сказать сам вопрос."""

    def test_brand_and_model_need_little(self):
        self.assertEqual(names('тойота камри'), ['Toyota Camry'])
        self.assertEqual(names('тойота камри 2015'), ['Toyota Camry'])
        self.assertEqual(names('у водителя тойота камри хочет в комфорт плюс'), ['Toyota Camry'])
        self.assertEqual(names('водитель на тойота камри 2015 года спрашивает почему не дают комфорт'),
                         ['Toyota Camry'])

    def test_dictionary_name_needs_a_tariff_or_a_bare_question(self):
        self.assertEqual(names('камри в комфорт'), ['Toyota Camry'])
        self.assertEqual(names('камри 2015'), ['Toyota Camry'])
        self.assertEqual(names('камри 2015 подходит?'), ['Toyota Camry'])
        self.assertIsNone(names('камри 2015 года стоит на штрафстоянке'))

    def test_common_word_needs_more(self):
        # «акцент» — и машина, и слово: тарифа мало, нужен ещё год или «подходит».
        self.assertEqual(names('акцент 2012'), ['Hyundai Accent'])
        self.assertEqual(names('акцент в комфорт'), ['Hyundai Accent'])
        self.assertEqual(names('акцент 2012 года берут в комфорт водителю'), ['Hyundai Accent'])
        self.assertIsNone(names('сделай акцент на тарифе комфорт'))
        self.assertIsNone(names('акцент'))

    def test_levels(self):
        level = {hit['cars'][0]['name']: hit['level'] for question in (
            'тойота камри', 'камри', 'акцент', 'вокси') for hit in analyze(question)['cars']}
        self.assertEqual(level, {'Toyota Camry': classifier.NAMED, 'Hyundai Accent': classifier.PROBABLE,
                                 'Toyota Voxy': classifier.NAMED})
        self.assertEqual(analyze('тойота камри')['cars'][0]['level'], classifier.SURE)
        self.assertEqual(analyze('фокус')['cars'][0]['level'], classifier.PROBABLE)
        self.assertEqual(analyze('к5')['cars'][0]['level'], classifier.PROBABLE)   # два знака


class PlanTest(unittest.TestCase):
    def test_brand_list(self):
        self.assertEqual(names('какие киа проходят в комфорт+'), 'brand:Kia')
        self.assertEqual(names('киа комфорт'), 'brand:Kia')
        self.assertEqual(names('киа 2019 года в комфорт подходит?'), 'brand:Kia')
        # Модель код не узнал — помощник найдёт её в списке марки сам.
        self.assertEqual(names('киа соренто 2019 комфорт'), 'brand:Kia')
        self.assertIsNone(names('киа'))
        self.assertIsNone(names('водитель на киа'))

    def test_tariff_list(self):
        self.assertEqual(names('какие машины подходят в бизнес'), 'tariff')
        self.assertEqual(names('список авто для тарифа электро'), 'tariff')

    def test_general(self):
        for question in ('что такое классификатор авто', 'как пользоваться классификатором',
                         'с какого года машина подходит в комфорт',
                         'какого года машины берут в эконом в шымкенте',
                         'подходит ли машина 2015 года в комфорт',
                         'какие машины подходят для работы в такси', 'с какого года принимают авто'):
            with self.subTest(question=question):
                self.assertEqual(names(question), 'general')

    def test_no_more_than_four_cars(self):
        data = dict(DATA, cars=[car(brand, 'E5', comfort=2020)
                                for brand in ('BYD', 'Chery', 'Kaiyi', 'Weltmeister', 'Zotye')])
        book = classifier.build_book(data)
        with patch.object(currency, 'today', return_value=TODAY):
            analysis = classifier.analyze('е5 2021 комфорт', book)
            decision = classifier.plan(analysis)
            found = classifier.build_ai_rows(analysis, book, article=ARTICLE)
        self.assertEqual((len(decision['cars']), decision['more']), (classifier.MAX_CARS, 1))
        self.assertEqual([row['heading_path'] for row in found],
                         ['BYD E5', 'Chery E5', 'Kaiyi E5', 'Weltmeister E5'])
        self.assertTrue(found[-1]['text'].endswith(
            'С таким названием в классификаторе есть ещё 1 модель — назовите марку.'))
        self.assertNotIn('назовите марку', found[0]['text'])


class FollowUpTest(unittest.TestCase):
    """Разговор о машине продолжают одним словом."""

    FIRST = 'камри 2015 года проходит в комфорт в алматы'

    def follow(self, question):
        return analyze(question, '%s %s' % (self.FIRST, question))

    def test_new_city_year_tariff_keep_the_car(self):
        city = self.follow('а в астане?')
        self.assertEqual((names('а в астане?', self.FIRST + ' а в астане?'), city['cities'],
                          city['tariffs'], city['years']),
                         (['Toyota Camry'], ['Астана'], ['comfort'], [2015]))
        year = self.follow('а 2012 года?')
        self.assertEqual((year['cities'], year['years']), (['Алматы'], [2012]))
        tariff = self.follow('а в бизнес?')
        self.assertEqual((tariff['tariffs'], tariff['years'], tariff['cities']),
                         (['business'], [2015], ['Алматы']))

    def test_new_car_keeps_the_rest(self):
        other = self.follow('а солярис?')
        self.assertEqual(([hit['cars'][0]['name'] for hit in other['cars']], other['tariffs'],
                          other['years'], other['cities']),
                         (['Hyundai Solaris'], ['comfort'], [2015], ['Алматы']))

    def test_another_topic_does_not_drag_the_car(self):
        for question in ('а какая комиссия?', 'а адрес офиса в астане?', 'спасибо',
                         'а как вывести деньги?'):
            with self.subTest(question=question):
                self.assertIsNone(names(question, '%s %s' % (self.FIRST, question)))

    def test_past_talk_about_something_else_gives_nothing(self):
        self.assertIsNone(names('а в астане?', 'адрес офиса в алматы а в астане?'))
        self.assertIsNone(names('а в шымкенте?', 'комиссия яндекса в алматы а в шымкенте?'))

    def test_question_is_read_once(self):
        # enrich_query отдаёт сам вопрос, когда истории нет.
        same = analyze('камри 2014 или 2015 в комфорт?', 'камри 2014 или 2015 в комфорт?')
        self.assertEqual(same['years'], [2014, 2015])
        self.assertNotIn('continued', same)

    def test_flattened_query_keeps_the_last_city(self):
        # Без отдельной истории границ реплик нет: речь о последнем названном городе.
        analysis = analyze('а 2012 года?', 'камри 2015 в комфорт в алматы а в астане? а 2012 года?')
        self.assertEqual((analysis['cities'], analysis['years']), (['Астана'], [2012]))

    def test_brand_alone_replaces_the_car_of_the_conversation(self):
        # «а киа?» после камри — список моделей Kia по тому же тарифу и городу.
        analysis = self.conversation('а киа?', self.FIRST)
        self.assertEqual(classifier.plan(analysis), {'kind': 'brand', 'brand': 'Kia'})
        self.assertEqual((analysis['tariffs'], analysis['cities']), (['comfort'], ['Алматы']))

    def test_list_question_carries_over_to_the_next_city(self):
        analysis = self.conversation('а в астане?', 'какие машины подходят в бизнес')
        self.assertEqual(classifier.plan(analysis), {'kind': 'tariff'})
        self.assertTrue(classifier.build_ai_rows(analysis, BOOK, article=ARTICLE)[0]['text'].startswith(
            'Модели тарифа Бизнес в «Классификаторе авто», город Астана.'))

    def conversation(self, question, *prior):
        history = [{'role': 'user', 'text': turn} for turn in prior]
        # Ответ помощника может перечислять и другие машины: выбор делает пользователь.
        history.insert(1, {'role': 'assistant', 'text': 'Toyota Corolla 2024, Бизнес, Шымкент'})
        return analyze(question, ai_answer.enrich_query(question, history), history=history)

    def test_later_year_replaces_the_previous_year_and_prefills_the_calculator(self):
        analysis = self.conversation('а в астане?', self.FIRST, 'а 2012 года?')
        found = classifier.build_ai_rows(analysis, BOOK, article=ARTICLE)
        self.assertEqual(analysis['years'], [2012])
        self.assertEqual((found[0]['ref_id'], found[0]['ref_city']), (2012, 'astana'))
        self.assertIn('машина 2012 года не подходит', found[0]['text'])
        self.assertNotIn('машина 2015 года', found[0]['text'])

    def test_latest_model_tariff_and_city_survive_the_next_follow_up(self):
        for prior, question, expected in (
                ('а солярис?', 'а 2012 года?', ('Hyundai Solaris', 'comfort', 'Алматы')),
                ('а в бизнес?', 'а 2012 года?', ('Toyota Camry', 'business', 'Алматы')),
                ('а в астане?', 'а 2012 года?', ('Toyota Camry', 'comfort', 'Астана'))):
            with self.subTest(prior=prior):
                analysis = self.conversation(question, self.FIRST, prior)
                cars = classifier.plan(analysis)['cars']
                self.assertEqual(([car['name'] for car in cars], analysis['tariffs'],
                                  analysis['cities'], analysis['years']),
                                 ([expected[0]], [expected[1]], [expected[2]], [2012]))

    def test_comparisons_in_one_turn_are_preserved(self):
        for prior, question, field, expected in (
                ('а 2012 или 2014 года?', 'а в астане?', 'years', [2012, 2014]),
                ('а в алматы и астане?', 'а 2012 года?', 'cities', ['Алматы', 'Астана']),
                ('а в комфорт и бизнес?', 'а 2012 года?', 'tariffs', ['comfort', 'business'])):
            with self.subTest(field=field):
                analysis = self.conversation(question, self.FIRST, prior)
                self.assertEqual(analysis[field], expected)
        analysis = self.conversation('а 2012 года?', self.FIRST, 'а камри и солярис?')
        self.assertEqual([car['name'] for car in classifier.plan(analysis)['cars']],
                         ['Toyota Camry', 'Hyundai Solaris'])

    def test_unrelated_turn_breaks_the_classifier_context(self):
        for unrelated in ('а какая комиссия?', 'а адрес офиса в астане?', 'спасибо'):
            with self.subTest(unrelated=unrelated):
                analysis = self.conversation('а в шымкенте?', self.FIRST, unrelated)
                self.assertIsNone(classifier.plan(analysis))
                self.assertEqual((analysis['cars'], analysis['years'], analysis['tariffs']),
                                 ([], [], []))
        analysis = self.conversation('а какая комиссия?', self.FIRST, 'а в астане?')
        self.assertIsNone(classifier.plan(analysis))

    def test_explicit_history_does_not_use_the_flattened_query(self):
        analysis = analyze('а в астане?', self.FIRST + ' а в астане?', history=[])
        self.assertIsNone(classifier.plan(analysis))

    def test_turn_the_classifier_kept_silent_on_does_not_lend_its_car(self):
        # Про машину спросили не классификатор: шум двигателя, штрафстоянка.
        # Короткое «а в комфорт?» следом эту машину за собой не тянет.
        for first in ('водитель на камри 2015 жалуется на шум двигателя',
                      'камри 2015 года стоит на штрафстоянке'):
            with self.subTest(first=first):
                self.assertIsNone(classifier.plan(self.conversation('а в комфорт?', first)))

    def test_city_outside_the_classifier_is_said_in_the_follow_up(self):
        analysis = self.conversation('а в петропавловске?', self.FIRST)
        self.assertEqual((analysis['cities'], analysis['other_cities']), ([], ['Петропавловск']))
        body = classifier.build_ai_rows(analysis, BOOK, article=ARTICLE)[0]['text']
        self.assertIn('Города Петропавловск в классификаторе нет', body)
        self.assertNotIn('Toyota Camry, Алматы,', body)

    def test_tariff_outside_the_classifier_is_said_in_the_follow_up(self):
        analysis = self.conversation('а в доставку?', self.FIRST)
        self.assertEqual((analysis['tariffs'], analysis['other_tariff'], analysis['cities']),
                         ([], True, ['Алматы']))
        body = classifier.build_ai_rows(analysis, BOOK, article=ARTICLE)[0]['text']
        self.assertIn('Классификатор ведёт только тарифы', body)

    def test_city_outside_the_classifier_survives_the_next_follow_up(self):
        analysis = self.conversation('а 2012 года?', 'камри 2015 в комфорт в петропавловске')
        self.assertEqual((analysis['years'], analysis['cities'], analysis['other_cities']),
                         ([2012], [], ['Петропавловск']))
        body = classifier.build_ai_rows(analysis, BOOK, article=ARTICLE)[0]['text']
        self.assertIn('Города Петропавловск в классификаторе нет', body)


class CarTextTest(unittest.TestCase):
    """Карточка машины: число для каждого города, о котором могут спросить."""

    def test_named_city(self):
        self.assertEqual(text('подходит ли тойота камри 2015 в комфорт в алматы').splitlines(), [
            'Toyota Camry — данные «Классификатора авто», город Алматы. '
            'Год выпуска «от N» значит «N и новее».',
            'Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска — машина 2015 года подходит.',
            'Toyota Camry, Алматы, тариф Эконом: от 1985 года выпуска — машина 2015 года подходит.',
            'Toyota Camry, Алматы, тариф Межгород: от 1985 года выпуска — машина 2015 года подходит.',
            'Toyota Camry, Алматы, тариф Комфорт+: от 2021 года выпуска — машина 2015 года не подходит.',
            'Toyota Camry, Алматы, тариф Бизнес: от 2023 года выпуска — машина 2015 года не подходит.',
            'Toyota Camry, Алматы: в тарифы Электро, Ultima (Premier) модель не внесена.',
        ])

    def test_boundary_year_fits(self):
        body = text('камри 2015 комфорт астана')
        self.assertIn('Toyota Camry, Астана, тариф Комфорт: от 2015 года выпуска — '
                      'машина 2015 года подходит.', body)
        self.assertIn('машина 2014 года не подходит', text('камри 2014 комфорт астана'))

    def test_asked_tariff_goes_first_even_when_the_model_is_not_in_it(self):
        lines = text('солярис в бизнес в алматы подходит?').splitlines()
        self.assertEqual(lines[1], 'Hyundai Solaris, Алматы, тариф Бизнес: модель в этот тариф не внесена.')
        self.assertIn('Hyundai Solaris, Алматы, тариф Комфорт: от 2021 года выпуска.', lines)

    def test_tariff_missing_in_the_city(self):
        lines = text('камри в бизнес в таразе').splitlines()
        self.assertEqual(lines[1], 'Toyota Camry: в городе Тараз тарифа Бизнес в классификаторе нет.')
        # Непрошеный тариф, которого в городе нет, не упоминается вовсе.
        self.assertNotIn('Комфорт+', '\n'.join(lines))

    def test_several_years(self):
        self.assertIn('Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска — машина '
                      '2013 года не подходит, 2014 года подходит.',
                      text('камри 2013 или 2014 в комфорт алматы'))

    def test_without_a_year_there_is_no_verdict(self):
        body = text('камри комфорт алматы')
        self.assertIn('Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска.', body)
        self.assertNotIn('подходит', body)

    def test_no_city_groups_cities_by_year(self):
        lines = text('камри 2015 года проходит в комфорт?').splitlines()
        self.assertEqual(lines[0], 'Toyota Camry — данные «Классификатора авто». Требуемый год '
                                   'выпуска зависит от города. Год выпуска «от N» значит «N и новее».')
        self.assertEqual(lines[1], 'Toyota Camry, тариф Комфорт: Астана — от 2015; Алматы — от 2014; '
                                   'Шымкент, Усть-Каменогорск — от 2013; Тараз — от 2012; '
                                   'Туркестан — от 2011. Во всех этих городах машина 2015 года подходит.')
        business = next(line for line in lines if 'тариф Бизнес' in line)
        self.assertEqual(business, 'Toyota Camry, тариф Бизнес: Астана — от 2024; Алматы — от 2023; '
                                   'Шымкент — от 2022. Во всех этих городах машина 2015 года не '
                                   'подходит. В городах Усть-Каменогорск, Тараз, Туркестан тарифа '
                                   'Бизнес в классификаторе нет.')

    def test_mixed_verdict_is_written_at_each_year(self):
        line = text('камри 2014 в комфорт').splitlines()[1]
        self.assertIn('Астана — от 2015 (машина 2014 года не подходит)', line)
        self.assertIn('Алматы — от 2014 (машина 2014 года подходит)', line)
        self.assertNotIn('Во всех этих городах', line)

    def test_most_cities_without_the_tariff_are_not_listed(self):
        line = next(item for item in text('мерседес s класс').splitlines() if 'Ultima' in item)
        self.assertTrue(line.endswith('В остальных городах тарифа Ultima (Premier) в классификаторе нет.'))

    def test_every_number_of_every_city_is_in_the_text(self):
        body = text('камри')
        camry = next(item for item in BOOK['cars'] if item['name'] == 'Toyota Camry')
        for city in BOOK['cities']:
            for key in city['tariffs']:
                required = classifier.required_year(camry, city, key)
                if required is not None:
                    self.assertIn('от %s' % required, body)

    def test_warning_is_verbatim_and_next_to_its_tariff(self):
        lines = text('кобальт 2024 комфорт астана').splitlines()
        self.assertEqual(lines[1], 'Chevrolet Cobalt, Астана, тариф Комфорт: от 2025 года выпуска — '
                                   'машина 2024 года не подходит. Оговорка классификатора: %s.'
                         % COBALT_NOTE)
        self.assertNotIn('Оговорка', lines[2])
        grouped = text('кобальт комфорт').splitlines()[1]
        self.assertTrue(grouped.endswith('Оговорка классификатора: %s.' % COBALT_NOTE))

    def test_model_in_no_tariff(self):
        self.assertEqual(text('альфард 2016 комфорт+ подходит'),
                         'Toyota Alphard: модель есть в «Классификаторе авто», но ни в один тариф '
                         'не внесена — тарифами не поддерживается.')

    def test_city_that_is_not_in_the_classifier(self):
        lines = text('камри 2015 в комфорт в петропавловске').splitlines()
        self.assertEqual(lines[1], 'Города Петропавловск в классификаторе нет. Города классификатора: '
                                   'Алматы, Астана, Шымкент, Усть-Каменогорск, Тараз, Туркестан.')
        self.assertIn('тариф Комфорт: Астана — от 2015', lines[2])

    def test_tariff_that_is_not_in_the_classifier(self):
        body = text('гранта 2018 в доставку подходит?')
        self.assertIn('Классификатор ведёт только тарифы: Эконом, Межгород, Комфорт, Комфорт+, Бизнес, '
                      'Электро, Ultima (Premier). По другим тарифам в нём данных нет.', body)

    def test_three_cities_line_by_line(self):
        body = text('нексия 2010 года алматы астана туркестан эконом')
        for line in ('Chevrolet Nexia: модель есть в «Классификаторе авто»',):
            self.assertIn(line, body)
        daewoo = rows('нексия 2010 года алматы астана туркестан эконом')[1]['text']
        self.assertIn('Daewoo Nexia — данные «Классификатора авто», города Алматы, Астана, Туркестан.',
                      daewoo)
        self.assertIn('Daewoo Nexia, Туркестан, тариф Эконом: от 1999 года выпуска', daewoo)
        self.assertIn('Daewoo Nexia, Туркестан: в тариф Комфорт модель не внесена.', daewoo)


class ListTextTest(unittest.TestCase):
    def test_brand_list_preserves_the_model_tariff_warning(self):
        body = text('Какие Chevrolet 2025 года проходят в комфорт в Алматы?')
        line = next(line for line in body.splitlines() if line.startswith('Chevrolet Cobalt:'))
        self.assertIn('Комфорт от 2024', line)
        self.assertIn('Оговорка классификатора: ' + COBALT_NOTE, line)
        self.assertEqual(body.count(COBALT_NOTE), 1)
        self.assertNotIn(COBALT_NOTE, text('Какие Chevrolet проходят в эконом в Алматы?'))

    def test_brand_list_for_a_tariff_in_a_city(self):
        lines = text('какие киа проходят в комфорт+ в шымкенте').splitlines()
        self.assertEqual(lines, [
            'Kia — модели в «Классификаторе авто», тариф Комфорт+, город Шымкент. '
            'Год выпуска «от N» значит «N и новее».',
            'Kia K5: Комфорт+ от 2020.',
            'Kia Sportage: Комфорт+ от 2022.',
            "Не внесены в тариф Комфорт+: Kia Rio, Kia Cee'd.",
        ])

    def test_brand_list_without_a_city_says_whose_years_these_are(self):
        lines = text('киа комфорт').splitlines()
        self.assertEqual(lines[0], 'Kia — модели в «Классификаторе авто», тариф Комфорт, годы для '
                                   'города Алматы; в других городах год другой — для точного года '
                                   'назовите город. Год выпуска «от N» значит «N и новее».')
        self.assertIn('Kia Rio: Комфорт от 2021.', lines)

    def test_brand_list_with_every_tariff_and_a_year(self):
        lines = text('киа 2019 года подходит?', 'киа 2019 года подходит?').splitlines()
        self.assertEqual(lines, [])                          # одной марки и года мало
        lines = text('киа 2019 года в комфорт подходит? астана').splitlines()
        self.assertIn('Kia Rio: Комфорт от 2022 (машина 2019 года не подходит).', lines)
        self.assertIn('Kia K5: Комфорт от 2015 (машина 2019 года подходит).', lines)

    def test_brand_leftover_models(self):
        body = text('какие лада подходят в эконом')
        self.assertIn('Lada (VAZ) 2114: Эконом от 1996.', body)
        self.assertIn('Остальные модели Lada (VAZ), которых нет в этом списке, в тарифы не внесены.', body)

    def test_brand_tariff_missing_in_the_city(self):
        body = text('какие киа в бизнес в таразе')
        self.assertIn('В городе Тараз тарифа Бизнес в классификаторе нет.', body)

    def test_short_tariff_is_listed(self):
        lines = text('какие машины подходят в бизнес в шымкенте').splitlines()
        self.assertEqual(lines[0], 'Модели тарифа Бизнес в «Классификаторе авто», город Шымкент. '
                                   'Год выпуска «от N» значит «N и новее».')
        self.assertIn('BMW 5er, тариф Бизнес: от 2019.', lines)
        self.assertIn('Toyota Camry, тариф Бизнес: от 2022.', lines)
        self.assertEqual(lines[1:], sorted(lines[1:], key=str.lower))

    def test_long_tariff_is_a_count_and_a_request(self):
        with patch.object(classifier, 'MAX_TARIFF_LIST', 5):
            body = text('какие машины подходят в комфорт')
        self.assertTrue(body.startswith('В тарифе Комфорт в «Классификаторе авто» '))
        self.assertIn('Назовите марку или модель', body)
        self.assertNotIn('Toyota Camry', body)
        count = re.search(r'авто» (\d+) модел', body).group(1)
        self.assertEqual(int(count), sum(1 for item in BOOK['cars'] if 'comfort' in item['years']))

    def test_tariff_missing_in_the_city(self):
        self.assertEqual(text('какие машины подходят в бизнес в таразе'),
                         'В городе Тараз тарифа Бизнес в классификаторе нет.')

    def test_general(self):
        lines = text('что такое классификатор авто').splitlines()
        self.assertIn('Тарифы классификатора: Эконом, Межгород, Комфорт, Комфорт+, Бизнес, Электро, '
                      'Ultima (Premier).', lines[0])
        self.assertIn('Алматы: Эконом, Межгород, Комфорт, Комфорт+, Бизнес, Электро, Ultima (Premier) '
                      '— базовые требования.', lines)
        self.assertIn('Астана: Эконом, Межгород, Комфорт, Комфорт+, Бизнес, Электро, Ultima (Premier) '
                      '— требования на 1 год строже, чем в Алматы.', lines)
        self.assertIn('Туркестан: Эконом, Межгород, Комфорт — требования на 3 года мягче, чем в Алматы.',
                      lines)
        self.assertIn('Строка «Другие модели» (те, которых в классификаторе нет), город Алматы: '
                      'Межгород от 1999.', lines)
        self.assertEqual(lines[-1], 'В классификаторе %s, %s.' % (
            classifier._count(len(BOOK['cars']), 'модель', 'модели', 'моделей'),
            classifier._count(len(BOOK['by_brand']), 'марка', 'марки', 'марок')))

    def test_general_for_a_named_city(self):
        body = text('с какого года машина подходит в комфорт в астане')
        self.assertIn('Астана: ', body)
        self.assertNotIn('Шымкент: ', body)

    def test_count_words(self):
        forms = ('модель', 'модели', 'моделей')
        self.assertEqual([classifier._count(number, *forms) for number in (1, 2, 5, 11, 21, 493, 112)],
                         ['1 модель', '2 модели', '5 моделей', '11 моделей', '21 модель',
                          '493 модели', '112 моделей'])


class RowContractTest(unittest.TestCase):
    REQUIRED = ('chunk_id', 'article_id', 'title', 'slug', 'heading_path', 'text',
                'requires_ack', 'found_by')

    def test_row_contract_of_the_retriever(self):
        found = rows('матиз подходит в эконом?')
        self.assertEqual([row['heading_path'] for row in found],
                         ['Chevrolet MATIZ', 'Daewoo Matiz', 'Ravon Matiz'])
        for row in found:
            for key in self.REQUIRED:
                self.assertIn(key, row)
            # Статья настоящая — сам классификатор.
            self.assertEqual((row['article_id'], row['slug'], row['title']),
                             (36, 'klassifikator-avto', 'Классификатор авто'))
            self.assertEqual(row['found_by'], [classifier.BRANCH])
            self.assertTrue(row['directory_hit'])
            self.assertEqual((row['source_kind'], row['tab'], row['space_id']),
                             ('classifier', 'classifier', None))
        ids = [row['chunk_id'] for row in found]
        self.assertEqual(len(set(ids)), 3)
        # Свой диапазон: в одном ответе встречаются и справочник (−1…), и Байга (−1001…).
        self.assertTrue(all(value <= -2001 for value in ids))

    def test_reference_for_the_calculator(self):
        row = rows('подходит ли тойота камри 2015 в комфорт в алматы')[0]
        self.assertEqual((row['ref_key'], row['ref_id'], row['ref_city']),
                         ('Toyota|Camry', 2015, 'almaty'))
        # Год не один, город не один — калькулятор оставит свои.
        loose = rows('камри 2014 или 2015 в комфорт алматы астана')[0]
        self.assertEqual((loose['ref_key'], loose['ref_id'], loose['ref_city']),
                         ('Toyota|Camry', None, None))
        lada = rows('лада гранта 2015 эконом')[0]
        self.assertEqual(lada['ref_key'], 'Lada (VAZ)|Granta')

    def test_reference_fits_the_columns(self):
        for item in classifier.build_book(classifier.read_data())['cars']:
            self.assertLessEqual(len('%s|%s' % (item['brand'], item['model'])), 64, item['name'])
        self.assertLessEqual(len(classifier.SOURCE_KIND), 16)
        self.assertLessEqual(len(classifier.SOURCE_TAB), 16)

    def test_lists_carry_no_car(self):
        for question in ('какие киа проходят в комфорт+', 'какие машины подходят в бизнес',
                         'что такое классификатор авто'):
            with self.subTest(question=question):
                row = rows(question)[0]
                self.assertEqual((row['ref_key'], row['ref_id'], row['ref_city'], row['names']),
                                 (None, None, None, []))

    def test_silent_when_there_is_nothing_to_say(self):
        self.assertEqual(rows('как оформить возврат в алматы'), [])


class AnswerPipelineTest(unittest.TestCase):
    QUESTION = 'подходит ли тойота камри 2015 в комфорт в алматы'

    def compose(self, reply, question=None, extra=(), found=None):
        chunks = (rows(question or self.QUESTION) if found is None else found) + list(extra)
        seen = {}

        def generate(system, user, history=()):
            seen['system'], seen['user'] = system, user
            return reply, {'provider': 'fake'}

        result = ai_answer.compose(question or self.QUESTION, chunks, generate, on_date=TODAY)
        return result, seen

    def test_rows_pass_the_floor_and_skip_clarify(self):
        found = rows('камри 2015')
        self.assertEqual(ai_answer.usable_chunks(found), found)
        article = {'chunk_id': 5, 'article_id': 77, 'text': 'x', 'similarity': 0.7, 'found_by': [1]}
        self.assertFalse(ai_answer.should_clarify('камри 2015', found + [article])[0])

    def test_prompt_labels_the_classifier_and_brings_its_rule(self):
        _result, seen = self.compose('Да.\nИСТОЧНИКИ: [1]')
        self.assertIn('[1] Справочник «Классификатор авто», запись «Toyota Camry»', seen['user'])
        self.assertIn('12. КЛАССИФИКАТОР АВТО', seen['system'])
        self.assertNotIn('СПИСКИ БАЙГИ', seen['system'])
        self.assertLess(seen['system'].index('12. КЛАССИФИКАТОР АВТО'),
                        seen['system'].index('ФОРМАТ ОТВЕТА'))

    def test_rule_comes_only_with_classifier_rows(self):
        article = {'chunk_id': 5, 'article_id': 77, 'text': 'x', 'similarity': 0.9, 'found_by': [1]}
        self.assertEqual(ai_answer.system_prompt([article]), ai_answer.SYSTEM_PROMPT)
        self.assertNotIn('КЛАССИФИКАТОР АВТО', ai_answer.SYSTEM_PROMPT)
        both = ai_answer.system_prompt(rows('камри 2015') + [dict(article, source_kind='baiga')])
        self.assertLess(both.index('11. СПИСКИ БАЙГИ'), both.index('12. КЛАССИФИКАТОР АВТО'))
        self.assertEqual(both.count('ФОРМАТ ОТВЕТА'), 1)

    def test_answer_gets_the_classifier_as_its_source(self):
        result, _seen = self.compose('Да, Toyota Camry 2015 года в Алматы подходит в Комфорт: '
                                     'нужен год от 2014.\nИСТОЧНИКИ: [1]')
        self.assertEqual(result['kind'], 'answer')
        source = result['sources'][0]
        self.assertEqual((source['source_kind'], source['tab'], source['slug'], source['article_id'],
                          source['ref_key'], source['ref_id'], source['ref_city']),
                         ('classifier', 'classifier', 'klassifikator-avto', 36, 'Toyota|Camry', 2015,
                          'almaty'))
        self.assertEqual(source['quote'], 'Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска '
                                          '— машина 2015 года подходит.')
        self.assertFalse(source['stale'])
        self.assertEqual(result['notes'], [])

    def test_quote_is_the_line_of_the_asked_tariff(self):
        """Ответ Vertex дословно (13.10.2026): года в нём нет, и строка «Комфорт+:
        … не подходит» совпадала с ним не хуже нужной — под «да» вставала цитата
        «не подходит». Цитата берётся из строк спрошенного тарифа."""
        result, _seen = self.compose('Да, Toyota Camry 2015 года выпуска подходит для работы в '
                                     'тарифе «Комфорт» в городе Алматы.\nИСТОЧНИКИ: [1]')
        self.assertEqual(result['sources'][0]['quote'], 'Toyota Camry, Алматы, тариф Комфорт: от '
                                                        '2014 года выпуска — машина 2015 года подходит.')
        # Без города — строка тарифа со всеми городами, а не соседнего тарифа.
        question = 'камри 2015 года проходит в комфорт?'
        result, _seen = self.compose('Да, Toyota Camry 2015 года подходит в Комфорт во всех городах.'
                                     '\nИСТОЧНИКИ: [1]', question=question)
        self.assertTrue(result['sources'][0]['quote'].startswith('Toyota Camry, тариф Комфорт: Астана'))

    def test_evidence_is_the_asked_tariff_and_the_notes(self):
        row = rows('подходит ли тойота камри 2015 в комфорт в алматы')[0]
        self.assertEqual(row['evidence'], 'Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска — '
                                          'машина 2015 года подходит.')
        # По строке на город, оговорки — тоже опора.
        row = rows('камри 2015 в комфорт и бизнес в алматы, астане и петропавловске')[0]
        self.assertEqual(row['evidence'].splitlines(), [
            'Города Петропавловск в классификаторе нет. Города классификатора: '
            'Алматы, Астана, Шымкент, Усть-Каменогорск, Тараз, Туркестан.',
            'Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска — машина 2015 года подходит.',
            'Toyota Camry, Алматы, тариф Бизнес: от 2023 года выпуска — машина 2015 года не подходит.',
            'Toyota Camry, Астана, тариф Комфорт: от 2015 года выпуска — машина 2015 года подходит.',
            'Toyota Camry, Астана, тариф Бизнес: от 2024 года выпуска — машина 2015 года не подходит.',
        ])
        # Город не из классификатора: оговорка и строка тарифа по всем городам.
        evidence = rows('камри 2015 в комфорт в петропавловске')[0]['evidence'].splitlines()
        self.assertEqual(len(evidence), 2)
        self.assertTrue(evidence[0].startswith('Города Петропавловск в классификаторе нет.'))
        self.assertTrue(evidence[1].startswith('Toyota Camry, тариф Комфорт: Астана — от 2015;'))
        # Тарифа в городе нет или модель в него не внесена — это тоже строка тарифа.
        self.assertEqual(rows('камри в бизнес в таразе')[0]['evidence'],
                         'Toyota Camry: в городе Тараз тарифа Бизнес в классификаторе нет.')
        self.assertEqual(rows('солярис в бизнес в алматы подходит?')[0]['evidence'],
                         'Hyundai Solaris, Алматы, тариф Бизнес: модель в этот тариф не внесена.')
        # Тариф не спросили — опоры нет, цитата выбирается из всего текста.
        for question in ('камри 2015 алматы', 'альфард 2016 комфорт+ подходит',
                         'гранта 2018 в доставку подходит?', 'какие киа проходят в комфорт+'):
            with self.subTest(question=question):
                self.assertIsNone(rows(question)[0]['evidence'])
        # Модели уходит текст целиком.
        full = rows('подходит ли тойота камри 2015 в комфорт в алматы')[0]
        self.assertIn('тариф Комфорт+', full['text'])

    def test_source_is_attached_when_the_answer_names_the_car(self):
        result, _seen = self.compose('Да, Toyota Camry 2015 года подходит: нужен год от 2014.')
        self.assertEqual([(source['source_kind'], source['attributed']) for source in result['sources']],
                         [('classifier', True)])

    def test_shared_year_alone_does_not_attach_the_classifier(self):
        """Ответ про акцию с «2014 годом» не подписывается карточкой машины,
        которая в вопросе просто упомянута."""
        article = {'chunk_id': 5, 'article_id': 77, 'title': 'Акции', 'slug': 'akcii',
                   'heading_path': 'Бонус', 'similarity': 0.9, 'found_by': [1], 'requires_ack': False,
                   'text': 'Бонус 2014 года: 5000 тенге за 100 поездок в тарифе Комфорт.'}
        result, _seen = self.compose('Бонус 2014 года — 5000 тенге за 100 поездок.\nИСТОЧНИКИ: [2]',
                                     extra=[article])
        self.assertEqual([source['source_kind'] for source in result['sources']], ['article'])

    def test_short_model_code_needs_the_brand_in_the_answer(self):
        chunk = rows('к5 2021 в бизнес')[0]
        self.assertFalse(ai_answer._names_car(chunk, 'Модель K5 подходит.'))
        self.assertTrue(ai_answer._names_car(chunk, 'Kia K5 2021 года не подходит в Бизнес.'))
        self.assertFalse(ai_answer._names_car(chunk, 'В Kia Rio год от 2021, к K50 не относится.'))
        self.assertFalse(ai_answer._names_car(rows('что такое классификатор авто')[0], 'Kia K5'))

    def test_not_in_the_tariff_is_an_answer_not_a_refusal(self):
        result, _seen = self.compose(
            'В доступных данных классификатора Hyundai Solaris в тариф Бизнес не внесена.'
            '\nИСТОЧНИКИ: [1]', question='солярис в бизнес подходит?')
        self.assertEqual(result['kind'], 'answer')
        self.assertEqual(result['sources'][0]['source_kind'], 'classifier')

    def test_plain_refusal_stays_a_refusal(self):
        result, _seen = self.compose('В доступных вам статьях этого нет.',
                                     question='солярис в бизнес подходит?')
        self.assertEqual((result['kind'], result['sources']), ('no_answer', []))

    def test_recomputed_year_gives_the_classifier_lines_instead_of_a_refusal(self):
        result, _seen = self.compose('Toyota Camry в Шымкенте берут с 2009 года.\nИСТОЧНИКИ: [1]')
        self.assertEqual(result['kind'], 'answer')
        self.assertEqual(result['text'], rows(self.QUESTION)[0]['text'])
        self.assertEqual(result['meta']['ungrounded'], ['2009'])
        self.assertEqual(result['sources'][0]['source_kind'], 'classifier')

    def test_invented_number_without_the_classifier_cited_is_still_withheld(self):
        article = {'chunk_id': 5, 'article_id': 77, 'title': 'Акции', 'slug': 'akcii',
                   'heading_path': '', 'similarity': 0.9, 'found_by': [1], 'requires_ack': False,
                   'text': 'Бонус 5000 тенге.'}
        result, _seen = self.compose('Бонус 7777 тенге.\nИСТОЧНИКИ: [2]', extra=[article])
        self.assertEqual(result['kind'], 'no_answer')
        self.assertEqual(result['meta']['rejected'], 'числа не найдены в источниках')

    def test_every_year_of_the_fragment_is_grounded(self):
        chunk = rows('камри')
        answer = ('Toyota Camry в Комфорт: Астана — от 2015, Алматы — от 2014, Шымкент — от 2013, '
                  'Туркестан — от 2011; в Бизнес: Астана — от 2024.')
        self.assertEqual(ai_answer.ungrounded_numbers(answer, chunk, 'камри', TODAY), [])
        self.assertEqual(ai_answer.ungrounded_numbers('в Караганде от 2009 года', chunk, 'камри', TODAY),
                         ['2009'])

    def test_date_in_a_warning_is_not_an_expired_fragment(self):
        """«До 15 августа 2025» в оговорке к одному тарифу не объявляет
        недействующей всю карточку машины."""
        found = rows('кобальт 2024 комфорт астана')
        self.assertTrue(currency.deadlines(found[0]['text']))         # дата со словом «до» есть
        marked = currency.mark_chunks(found, TODAY)
        self.assertEqual([(row['stale'], row['stale_note']) for row in marked], [(False, '')])
        result, seen = self.compose(
            'Chevrolet Cobalt в Астане: Комфорт — от 2025 года, машина 2024 года не подходит. '
            'Оговорка: до 15 августа 2025: от 2024 г.\nИСТОЧНИКИ: [1]',
            question='кобальт 2024 комфорт астана')
        self.assertNotIn('СРОК ИСТЁК', seen['user'])
        self.assertEqual((result['kind'], result['notes']), ('answer', []))
        self.assertFalse(result['sources'][0]['stale'])
        # А та же дата в статье по-прежнему срок.
        article = dict(found[0], source_kind='article')
        self.assertTrue(currency.mark_chunks([article], TODAY)[0]['stale'])


class StoreTest(unittest.TestCase):
    """Источник в истории: статья есть, а куска в индексе нет."""

    def source(self):
        chunk = rows('подходит ли тойота камри 2015 в комфорт в алматы')
        return ai_answer.build_sources([1], ai_answer.usable_chunks(chunk),
                                       'Toyota Camry 2015 подходит: от 2014.')[0]

    def test_saved_with_the_article_and_without_the_chunk(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (10, None)]
        ai_store.append_message(cursor, 5, role='assistant', text='ответ', sources=[self.source()])
        params = cursor.execute.call_args_list[-1].args[1]
        self.assertEqual((params['source_kind'], params['tab'], params['article_id'], params['chunk_id'],
                          params['ref_key'], params['ref_id'], params['ref_city'], params['slug']),
                         ('classifier', 'classifier', 36, None, 'Toyota|Camry', 2015, 'almaty',
                          'klassifikator-avto'))

    def read(self, visible):
        row = dict.fromkeys(ai_store.SOURCE_ROW)
        row.update(message_id=10, ord=0, article_id=36, title='Классификатор авто',
                   slug='klassifikator-avto', heading_path='Toyota Camry', quote='от 2014',
                   quote_ok=True, requires_ack=False, attributed=False, stale=False, stale_note='',
                   stale_kind='', source_kind='classifier', tab='classifier', ref_id=2015,
                   ref_city='almaty', ref_key='Toyota|Camry', space_id=None, visible=visible)
        message = dict.fromkeys(ai_store._MESSAGE_FIELDS)
        message.update(id=10, seq=2, role='assistant', kind='answer', text='ответ')
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[tuple(message[name] for name in ai_store._MESSAGE_FIELDS)],
                                       [tuple(row[name] for name in ai_store.SOURCE_ROW)]]
        return ai_store.chat_messages(cursor, 5, visible_article_ids=[36])[0]['sources'][0]

    def test_shown_while_the_article_is_readable(self):
        shown = self.read(True)
        self.assertEqual((shown['available'], shown['slug'], shown['tab'], shown['ref_key'],
                          shown['ref_id'], shown['ref_city'], shown['quote']),
                         (True, 'klassifikator-avto', 'classifier', 'Toyota|Camry', 2015, 'almaty',
                          'от 2014'))

    def test_hidden_when_the_article_is_closed(self):
        hidden = self.read(False)
        self.assertEqual((hidden['available'], hidden['title'], hidden['slug'], hidden['tab'],
                          hidden['ref_key'], hidden['quote']),
                         (False, 'Статья недоступна', None, None, None, ''))


class AccessTest(unittest.TestCase):
    """Классификатор читается, только пока его статья в периметре помощника."""

    def cursor(self, row=(36, 'Классификатор авто', 'klassifikator-avto')):
        cursor = MagicMock()
        cursor.fetchone.return_value = row
        return cursor

    def ask(self, cursor, question, article_ids, **extra):
        with patch.object(classifier, 'book', return_value=BOOK), \
                patch.object(currency, 'today', return_value=TODAY):
            return classifier.ai_rows(cursor, question, article_ids=article_ids, **extra)

    def test_rows_when_the_article_is_in_the_perimeter(self):
        cursor = self.cursor()
        found = self.ask(cursor, 'камри 2015 комфорт алматы', frozenset({36, 40}))
        self.assertEqual([row['heading_path'] for row in found], ['Toyota Camry'])
        self.assertEqual(found[0]['article_id'], 36)
        cursor.execute.assert_called_once_with(classifier._ARTICLE_SQL, ('klassifikator-avto',))

    def test_silent_outside_the_perimeter(self):
        for article_ids in (frozenset(), frozenset({40, 41})):
            with self.subTest(article_ids=article_ids):
                self.assertEqual(self.ask(self.cursor(), 'камри 2015 комфорт алматы', article_ids), [])

    def test_silent_without_the_article(self):
        self.assertEqual(self.ask(self.cursor(row=None), 'камри 2015 комфорт', frozenset({36})), [])

    def test_database_is_not_touched_until_the_question_is_about_a_car(self):
        cursor = self.cursor()
        self.assertEqual(self.ask(cursor, 'как оформить возврат в алматы', frozenset({36})), [])
        cursor.execute.assert_not_called()

    def test_follow_up_reaches_the_rows(self):
        found = self.ask(self.cursor(), 'а в астане?', frozenset({36}),
                         search_query='камри 2015 года проходит в комфорт в алматы а в астане?')
        self.assertIn('Toyota Camry, Астана, тариф Комфорт: от 2015', found[0]['text'])

    def test_missing_data_file_silences_the_classifier_once(self):
        with patch.object(classifier, 'read_data', side_effect=OSError('нет файла')), \
                patch.dict(classifier._STATE, clear=True), \
                self.assertLogs(level='ERROR') as logs:
            cursor = self.cursor()
            for _attempt in range(3):
                self.assertEqual(classifier.ai_rows(cursor, 'камри 2015 комфорт',
                                                    article_ids=frozenset({36})), [])
            cursor.execute.assert_not_called()
        self.assertEqual(len(logs.records), 1)               # не трейсбек на каждый вопрос

    def test_the_slug_is_the_one_the_schema_seeds(self):
        from wiki import schema
        self.assertEqual(schema.CLASSIFIER_SLUG, 'klassifikator-avto')
        article = (ROOT / 'src' / 'components' / 'wiki' / 'WikiArticle.jsx').read_text(encoding='utf-8')
        self.assertIn("export const CLASSIFIER_SLUG = '%s';" % schema.CLASSIFIER_SLUG, article)

    def test_the_only_sql_is_the_article_lookup(self):
        tree = ast.parse((ROOT / 'wiki' / 'classifier.py').read_text(encoding='utf-8'))
        queries = [node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and re.search(r'\b(SELECT|INSERT|UPDATE|DELETE)\b', node.value)]
        self.assertEqual(queries, [classifier._ARTICLE_SQL])


class RouteWiringTest(unittest.TestCase):
    def test_failure_does_not_take_the_answer_away(self):
        cursor = MagicMock()
        with patch.object(classifier, 'ai_rows', side_effect=RuntimeError('сбой')), \
                self.assertLogs(level='ERROR'):
            self.assertEqual(routes_ai.classifier_rows(cursor, 'камри 2015', frozenset({36})), [])
        self.assertEqual([call.args[0] for call in cursor.execute.call_args_list],
                         ['SAVEPOINT wiki_ai_classifier', 'ROLLBACK TO SAVEPOINT wiki_ai_classifier'])

    def test_savepoint_is_released_on_success(self):
        cursor = MagicMock()
        with patch.object(classifier, 'ai_rows', return_value=['строка']) as called:
            self.assertEqual(routes_ai.classifier_rows(cursor, 'камри 2015', frozenset({36}), 'было'),
                             ['строка'])
        called.assert_called_once_with(cursor, 'камри 2015', article_ids=frozenset({36}),
                                       search_query='было', history=None)
        self.assertEqual([call.args[0] for call in cursor.execute.call_args_list],
                         ['SAVEPOINT wiki_ai_classifier', 'RELEASE SAVEPOINT wiki_ai_classifier'])

    def test_separate_turns_reach_the_classifier(self):
        cursor = MagicMock()
        history = [{'role': 'user', 'text': 'камри 2015 в комфорт в алматы'},
                   {'role': 'user', 'text': 'а 2012 года?'}]
        with patch.object(classifier, 'book', return_value=BOOK), \
                patch.object(currency, 'today', return_value=TODAY):
            cursor.fetchone.return_value = (36, ARTICLE['title'], ARTICLE['slug'])
            found = routes_ai.classifier_rows(cursor, 'а в астане?', frozenset({36}),
                                             ai_answer.enrich_query('а в астане?', history),
                                             history=history)
        self.assertEqual((found[0]['ref_id'], found[0]['ref_city']), (2012, 'astana'))

    def function(self, name):
        tree = ast.parse((ROOT / 'wiki' / 'routes_ai.py').read_text(encoding='utf-8'))
        node = next(item for item in ast.walk(tree)
                    if isinstance(item, ast.FunctionDef) and item.name == name)
        return ast.unparse(node)

    def test_ask_puts_the_classifier_between_the_directory_and_the_articles(self):
        ask = self.function('wiki_ai_ask')
        self.assertIn("rows = lists + directory_rows(cursor, ctx, question, space_id, search_query) + "
                      "classifier_rows(cursor, question, scope['article_ids'], search_query, history=history) + "
                      "found['rows']", ask)

    def test_showcase_shows_what_ask_will_get(self):
        search = self.function('wiki_ai_search')
        self.assertIn('cars = classifier_rows(cursor, query, article_ids)', search)
        self.assertIn("rows = baiga + directory + cars + found['rows']", search)
        self.assertIn("'classifier': len(cars)", search)

    def test_the_perimeter_is_the_assistants_own(self):
        # Не visible_article_ids и не мастер-ключ: то же множество, по которому
        # помощник ищет статьи.
        ask = self.function('wiki_ai_ask')
        self.assertIn('scope = wiki_perimeter.assistant_perimeter(cursor, ctx, space_id)', ask)
        search = self.function('wiki_ai_search')
        self.assertIn("article_ids = scope['article_ids']", search)

    def test_chip_opens_the_calculator_on_the_car(self):
        """Куда ведёт чип, решает чистая функция (tests/assistant_source_target.test.mjs);
        здесь — что экраны зовут именно её."""
        wiki = (ROOT / 'src' / 'components' / 'wiki' / 'WikiView.jsx').read_text(encoding='utf-8')
        self.assertIn('const car = classifierPrefillOf(source);', wiki)
        self.assertIn('setSearchTarget({ slug: CLASSIFIER_SLUG, prefill, from });', wiki)
        self.assertIn("if (source?.tab === CLASSIFIER_TAB) {", wiki)
        self.assertIn("if (directoryFocus.tab === CLASSIFIER_TAB) {", wiki)
        # Справочник — вкладка, классификатор — статья: проверка вкладки идёт раньше.
        self.assertLess(wiki.index('if (!slug && source?.tab) { openDirectory(source); return; }'),
                        wiki.index('if (source?.tab === CLASSIFIER_TAB) {'))
        orb = (ROOT / 'src' / 'components' / 'assistant' / 'AssistantOrb.jsx').read_text(
            encoding='utf-8')
        self.assertLess(orb.index('if (source?.tab === CLASSIFIER_TAB && onOpenWikiTarget) {'),
                        orb.index('onOpenWikiArticle?.(slug, quote);'))
        thread = (ROOT / 'src' / 'components' / 'assistant' / 'assistantThread.jsx').read_text(
            encoding='utf-8')
        self.assertIn('classifier: Car', thread)

    def test_json_file_is_valid_for_both_sides(self):
        data = json.loads(Path(classifier.DATA_PATH).read_text(encoding='utf-8'))
        self.assertEqual(sorted(data), ['cars', 'cities', 'tariffs'])


if __name__ == '__main__':
    unittest.main()
