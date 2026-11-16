# -*- coding: utf-8 -*-
"""Помощник вики на НАСТОЯЩЕМ справочнике Яндекс Про (форма 2): то, чего нет в синтетике.

Числа ниже — с живых страниц auto-list 16.11.2026 (сверены со страницами всех
25 городов): Camry в «Комфорт» Шымкента — с 2010 (по «поправке» Wiki 2.0 было бы
2013), Cobalt в «Комфорт» Актау — «не допускается, кроме машин 2024–2026 годов,
зарегистрированных до 30 июня 2026», у Аксая — один Эконом «любая модель от 1988».
"""

import copy
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from wiki import classifier
from wiki.ai import currency

TODAY = date(2026, 11, 16)
ARTICLE = {'id': 36, 'title': 'Классификатор авто', 'slug': 'klassifikator-avto'}
DATA = classifier.read_data()
BOOK = classifier.build_book(DATA)


def text(question, book=BOOK):
    with patch.object(currency, 'today', return_value=TODAY):
        analysis = classifier.analyze_question(question, None, book)
        found = classifier.build_ai_rows(analysis, book, article=ARTICLE)
    return '\n'.join(row['text'] for row in found)


class RealDataTest(unittest.TestCase):
    def test_each_city_has_the_year_of_its_own_page(self):
        body = text('камри 2012 в комфорт в шымкенте')
        self.assertIn('Toyota Camry, Шымкент, тариф Комфорт: от 2010 года выпуска — машина 2012 года '
                      'подходит.', body)
        body = text('камри 2012 в комфорт в алматы')
        self.assertIn('Toyota Camry, Алматы, тариф Комфорт: от 2014 года выпуска — машина 2012 года '
                      'не подходит.', body)

    def test_exception_of_registered_cars(self):
        body = text('кобальт 2025 в комфорт актау')
        self.assertIn('Chevrolet Cobalt, Актау, тариф Комфорт: в таблице: «не допускается (кроме машин '
                      '2024-2026 годов, которые были зарегистрированы* в сервисе не позднее 30 июня 2026)» '
                      '— машина 2025 года — только если зарегистрирована в сервисе не позднее 30 июня 2026.',
                      body)

    def test_rule_any_brand(self):
        body = text('нексия 2010 в эконом в аксае')
        self.assertIn('Daewoo Nexia, Аксай, тариф Эконом: любая модель не старше 1988 года выпуска '
                      '(правило тарифа в городе) — машина 2010 года подходит.', body)
        listed = text('какие машины подходят в эконом в аксае')
        self.assertTrue(listed.startswith('В городе Аксай в тариф Эконом берут любую модель не старше '
                                          '1988 года выпуска — отдельной таблицы моделей нет.'), listed)

    def test_new_cities_are_cities_of_the_classifier(self):
        """Петропавловск, Жезказган, Кентау, Жанаозен, Аксай теперь в справочнике —
        вопрос про них получает их годы, а не «такого города в классификаторе нет»."""
        with patch.object(currency, 'today', return_value=TODAY):
            analysis = classifier.analyze_question('камри 2015 в комфорт в петропавловске', None, BOOK)
        self.assertEqual((analysis['cities'], analysis['other_cities']), (['Петропавловск'], []))
        self.assertIn('Toyota Camry, Петропавловск, тариф Комфорт: от 2010 года выпуска',
                      text('камри 2015 в комфорт в петропавловске'))
        with patch.object(currency, 'today', return_value=TODAY):
            other = classifier.analyze_question('камри в комфорт в балхаше', None, BOOK)
        self.assertEqual(other['other_cities'], ['Балхаш'])

    def test_lada_is_one_brand_in_every_spelling(self):
        self.assertEqual(classifier._brand_id('LADA (ВАЗ)'), classifier._brand_id('Lada (VAZ)'))
        body = text('лада гранта 2012 межгород алматы')
        self.assertIn('LADA (ВАЗ) Granta, Алматы, тариф Межгород: от 2011 года выпуска — машина 2012 '
                      'года подходит.', body)

    def test_condition_of_the_cell_is_kept(self):
        body = text('byd han 2023 ultima алматы')
        self.assertIn('BYD Han, Алматы, тариф Ultima (Premier): в таблице: «от 2022 (только '
                      'комплектации с сидениями, не в спортивном стиле)» — машина 2023 года подходит.',
                      body)

    def test_general_text_speaks_of_cities_not_of_shifts(self):
        body = text('что такое классификатор авто')
        self.assertIn('Данные Яндекса обновлены %s.' % DATA['source']['updated'], body)
        self.assertIn('Аксай: Эконом — любая модель от 1988.', body)
        self.assertNotIn('строже', body)
        self.assertNotIn('мягче', body)


class UpcomingTest(unittest.TestCase):
    def test_rules_not_yet_in_force_are_named_with_their_date(self):
        data = copy.deepcopy(DATA)
        aksay = next(city for city in data['cities'] if city['id'] == 'aksay')
        aksay['tariffs']['econom']['next'] = {'any': 2000, 'from': '2027-01-01'}
        body = text('нексия 2010 в эконом в аксае', classifier.build_book(data))
        self.assertIn('С 1 января 2027 действуют новые правила: любая модель от 2000.', body)


class DatabaseTouchTest(unittest.TestCase):
    """Помощник зовёт классификатор на КАЖДЫЙ вопрос — база трогается только для
    вопроса про машину (и тогда же сверяется слепок)."""

    def setUp(self):
        classifier.invalidate()

    def tearDown(self):
        classifier.invalidate()

    def cursor(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None                  # слепка нет, статьи нет
        return cursor

    def test_unrelated_question_does_not_touch_the_database(self):
        cursor = self.cursor()
        with patch.object(currency, 'today', return_value=TODAY):
            self.assertEqual(classifier.ai_rows(cursor, 'как оформить возврат в алматы',
                                                article_ids=frozenset({36})), [])
        cursor.execute.assert_not_called()

    def test_car_question_checks_the_snapshot_then_the_article(self):
        cursor = self.cursor()
        with patch.object(currency, 'today', return_value=TODAY):
            classifier.ai_rows(cursor, 'камри 2015 комфорт алматы', article_ids=frozenset({36}))
        sql = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(sql[0], 'SAVEPOINT classifier_book')
        self.assertIn(classifier._ARTICLE_SQL, sql)
        self.assertLess(sql.index('SAVEPOINT classifier_book'), sql.index(classifier._ARTICLE_SQL))


if __name__ == '__main__':
    unittest.main()
