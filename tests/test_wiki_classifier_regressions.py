"""User-facing edge cases found while reviewing classifier mutation coverage."""

import unittest
from datetime import date
from unittest.mock import patch

from wiki import classifier
from wiki.ai import answer, currency


TODAY = date(2026, 10, 12)
ARTICLE = {'id': 36, 'title': 'Классификатор авто', 'slug': 'klassifikator-avto'}
DATA = {
    'cities': [
        {'id': 'almaty', 'name': 'Алматы', 'offset': 0,
         'tariffs': ['econom', 'comfort']},
    ],
    'tariffs': [
        {'key': 'econom', 'name': 'Эконом'},
        {'key': 'comfort', 'name': 'Комфорт'},
    ],
    'cars': [
        {'brand': brand, 'model': model, 'years': {'econom': 2000, 'comfort': 2018}}
        for brand, model in (
            ('Toyota', 'Camry'), ('Toyota', 'Yaris'),
            ('Toyota', 'Allion'), ('Toyota', 'Aurion'),
            ('Honda', 'Civic'), ('Hyundai', 'Accent'), ('Kia', 'Rio'),
            ('Cadillac', 'ATS'), ('Mercedes-Benz', 'CLC-klasse'),
            ('Mercedes-Benz', 'CLS-klasse'), ('Peugeot', '2008'),
            ('Peugeot', '308'), ('Land Rover', 'Range Rover'),
            ('Land Rover', 'Discovery'),
        )
    ],
}
BOOK = classifier.build_book(DATA)


def analyze(question):
    with patch.object(currency, 'today', return_value=TODAY):
        return classifier.analyze(question, BOOK)


def rows(question):
    return classifier.build_ai_rows(analyze(question), BOOK, article=ARTICLE)


class ClassifierRecognitionRegressionTest(unittest.TestCase):
    def test_russian_names_preserve_initial_ya_and_soft_latin_c(self):
        for question, expected in (
                ('ярис 2020 в комфорт', 'Toyota Yaris'),
                ('цивик 2020 в комфорт', 'Honda Civic')):
            with self.subTest(question=question):
                self.assertEqual([row['heading_path'] for row in rows(question)], [expected])

    def test_spoken_mercedes_code_selects_cls_not_clc(self):
        found = rows('мерседес цлс 2020 в комфорт')
        self.assertEqual([row['heading_path'] for row in found], ['Mercedes-Benz CLS-klasse'])
        self.assertEqual(found[0]['ref_key'], 'Mercedes-Benz|CLS-klasse')

    def test_unqualified_common_english_words_do_not_open_a_car(self):
        for question in ('accent', 'rio'):
            with self.subTest(question=question):
                self.assertEqual(rows(question), [])

    def test_short_russian_word_is_not_guessed_as_an_acronym(self):
        # «Ас» sounds like ATS after consonant folding, but does not name Cadillac.
        self.assertEqual(rows('водитель ас в комфорте'), [])

    def test_equally_close_typo_returns_brand_choices_without_selecting_a_model(self):
        # «Арион» is equally close to Toyota Allion and Aurion.
        found = rows('тойота арион 2020 в комфорт')
        self.assertEqual(len(found), 1)
        self.assertIn('Toyota Allion', found[0]['text'])
        self.assertIn('Toyota Aurion', found[0]['text'])
        self.assertIsNone(found[0]['ref_key'])

    def test_explicit_year_after_brand_is_not_a_numeric_peugeot_model(self):
        question = 'пежо 2008 года в комфорт'
        self.assertEqual(analyze(question)['years'], [2008])
        found = rows(question)
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0]['ref_key'])
        # The same digits without «года» do name a model.
        model = rows('пежо 2008 2020 года в комфорт')[0]
        self.assertEqual((model['ref_key'], model['ref_id']), ('Peugeot|2008', 2020))

    def test_car_and_year_do_not_turn_a_service_question_into_tariff_advice(self):
        for name in ('тойота камри', 'камри'):
            question = 'водитель на %s 2015 жалуется на шум двигателя' % name
            with self.subTest(question=question):
                self.assertEqual(rows(question), [])

    def test_car_list_for_another_purpose_does_not_trigger_classifier(self):
        self.assertEqual(rows('какие машины перевозят кошек'), [])

    def test_brand_alias_does_not_choose_range_rover_for_land_rover(self):
        found = rows('ленд ровер 2020 комфорт')
        self.assertEqual(len(found), 1)
        self.assertIn('Land Rover Discovery', found[0]['text'])
        self.assertIn('Land Rover Range Rover', found[0]['text'])
        self.assertIsNone(found[0]['ref_key'])


class ClassifierAnswerRegressionTest(unittest.TestCase):
    def test_selected_city_keeps_missing_city_and_tariff_explanations(self):
        text = rows('камри 2020 в доставку в Алматы и Петропавловске')[0]['text']
        self.assertIn('Toyota Camry, Алматы', text)
        self.assertIn('Города Петропавловск в классификаторе нет', text)
        self.assertIn('По другим тарифам в нём данных нет', text)

    def test_missing_tariff_information_is_an_answer_with_the_classifier_source(self):
        question = 'камри 2020 в доставку в Алматы'
        reply = ('В доступном классификаторе нет данных по тарифу Доставка.'
                 '\nИСТОЧНИКИ: [1]')
        # This has the refusal wording but is the requested classifier limitation.
        self.assertTrue(answer.is_refusal(reply.split('\n')[0]))
        result = answer.compose(question, rows(question),
                                lambda *_args, **_kwargs: (reply, {'provider': 'fake'}),
                                on_date=TODAY)
        self.assertEqual(result['kind'], 'answer')
        self.assertEqual([source['source_kind'] for source in result['sources']], ['classifier'])


if __name__ == '__main__':
    unittest.main()
