# -*- coding: utf-8 -*-
"""Поиск задач с опечатками (task_search.py) — владелец 27.11.2026.

«Сделать поиск задачи, чтобы можно было искать задачу легко и просто, так же
учитывать ошибки букв и тд, поиск будет искать по выбранной доске и проекту».
Охват — забота базы (tests/test_task_search_wiring.py); здесь — что прощается,
что нет и в каком порядке встают находки. Модуль чистый: ни базы, ни Flask.
"""
import random
import time
import unittest

import task_search as ts


def doc(id_, subject='', description='', people=(), code='', number=0, aliases=(), rank=None):
    return ts.Document(id=id_, subject=subject, description=description, people=tuple(people),
                       key_code=code, key_number=number, key_aliases=tuple(aliases),
                       created_rank=float(id_ if rank is None else rank))


def ids(hits):
    return [hit.id for hit in hits]


def tiers(hits):
    return {hit.id: hit.tier for hit in hits}


class FoldTests(unittest.TestCase):
    def test_case_yo_and_kazakh_letters_fold(self):
        self.assertEqual(ts.fold('Отчёт ЁЛКА'), 'отчет елка')
        self.assertEqual(ts.fold('Қазына Тіркеу Әсел'), 'казына тиркеу асел')

    def test_length_is_kept_for_highlight_offsets(self):
        # «İ».lower() даёт две буквы — подсветка уехала бы на знак.
        for text in ('İstanbul Отчёт', 'Ёжик', 'ẞtraße', 'ǅ'):
            self.assertEqual(len(ts.fold(text)), len(text), text)

    def test_words_keep_positions_of_the_original(self):
        text = 'Проверить «Отчёт» по ЗП'
        words = ts.words_with_positions(text)
        self.assertIn((11, 16, 'отчет'), words)
        self.assertEqual(text[11:16], 'Отчёт')


class DistanceTests(unittest.TestCase):
    def test_one_edit_of_every_kind(self):
        for left, right in (('отчет', 'отчт'), ('отчет', 'отчеет'), ('отчет', 'отчот'),
                            ('отчет', 'отчте'), ('сотрудник', 'сотрдуник'), ('отчет', 'отчет')):
            self.assertTrue(ts.within_one(left, right), (left, right))
        for left, right in (('отчет', 'очтте'), ('отчет', 'отч'), ('табель', 'бельта')):
            self.assertFalse(ts.within_one(left, right), (left, right))

    def test_distance_respects_the_limit(self):
        self.assertEqual(ts.distance_within('инвентаризация', 'инвинтаризацыя', 2), 2)
        self.assertIsNone(ts.distance_within('инвентаризация', 'инвинтаризацыйя', 2))
        self.assertEqual(ts.distance_within('сотрдуник', 'сотрудник', 2), 1)  # перестановка — одна правка
        self.assertIsNone(ts.distance_within('отчет', 'отчт', 0))

    def test_typo_budget_grows_with_length(self):
        self.assertEqual([ts.typo_budget(w) for w in ('зп', 'акт', 'план', 'отчет', 'зарплата', 'инвентарь')],
                         [0, 0, 1, 1, 1, 2])
        # Девять букв — уже две правки: «сатрудниг» — это «сотрудник».
        self.assertEqual(ts.match_word('сатрудниг', 'сотрудник')[0], ts.FUZZY)
        self.assertEqual(ts.typo_budget('12345678'), 0)  # номер — не опечатка


class MatchWordTests(unittest.TestCase):
    def kind(self, token, word):
        result = ts.match_word(token, word)
        return result[0] if result else None

    def test_whole_word_and_beginning(self):
        self.assertEqual(self.kind('отчет', 'отчет'), ts.EXACT)
        self.assertEqual(self.kind('отч', 'отчет'), ts.PREFIX)

    def test_two_letters_only_as_a_whole_word(self):
        """«оп» — отдел ОП, а не все «операторы»."""
        self.assertEqual(self.kind('оп', 'оп'), ts.EXACT)
        self.assertIsNone(self.kind('оп', 'операторы'))
        self.assertIsNone(self.kind('зп', 'зпт'))

    def test_endings_are_the_same_word(self):
        for token, word in (('табель', 'табеля'), ('отчеты', 'отчет'), ('водитель', 'водителей'),
                            ('заявка', 'заявки'), ('смена', 'смены'), ('акты', 'акт'),
                            ('сотрудникам', 'сотрудник'), ('бекова', 'бековой')):
            self.assertEqual(self.kind(token, word), ts.STEM, (token, word))

    def test_a_long_tail_is_another_word(self):
        self.assertNotEqual(self.kind('марка', 'маркетинг'), ts.STEM)
        self.assertNotEqual(self.kind('отчет', 'отчество'), ts.STEM)
        # «план» → «плат» — одна правка при той же первой букве: это опечатка
        # (вторая ступень, «похожие»), а не то же слово с другим окончанием.
        self.assertEqual(self.kind('план', 'плат'), ts.FUZZY)

    def test_inside_a_word_from_four_letters(self):
        self.assertEqual(self.kind('плата', 'зарплата'), ts.INFIX)
        self.assertIsNone(self.kind('лат', 'зарплата'))

    def test_typos_by_length(self):
        self.assertEqual(self.kind('отчт', 'отчет'), ts.FUZZY)
        self.assertEqual(self.kind('зарплта', 'зарплата'), ts.STEM)  # общая основа «зарпл»
        self.assertEqual(self.kind('сотрдуник', 'сотрудник'), ts.FUZZY)
        self.assertEqual(self.kind('атчет', 'отчет'), ts.FUZZY)  # первая буква, но вторая на месте
        self.assertEqual(self.kind('инвинтаризацыя', 'инвентаризация'), ts.FUZZY)  # две правки от 9 букв
        self.assertIsNone(self.kind('отчетк', 'очетта'))
        self.assertIsNone(self.kind('акт', 'акр'))  # трёхбуквенное — только точно

    def test_four_letters_keep_the_first_letter(self):
        """«план» не находит «клан», а «жана» находит «Жанну»."""
        self.assertIsNone(self.kind('план', 'клан'))
        self.assertEqual(self.kind('жана', 'жанна'), ts.FUZZY)
        self.assertIsNone(self.kind('марк', 'мария'))  # две правки

    def test_typo_while_typing(self):
        self.assertEqual(self.kind('атчетн', 'отчетность'), ts.PREFIX_FUZZY)
        self.assertIsNone(self.kind('атче', 'отчетность'))  # у коротких «набирается с опечаткой» нет

    def test_numbers_are_never_typos(self):
        self.assertIsNone(self.kind('125', '126'))
        self.assertEqual(self.kind('12', '125'), ts.PREFIX)
        self.assertEqual(self.kind('2025', '2025'), ts.EXACT)


class ParseQueryTests(unittest.TestCase):
    def tokens(self, query, variant=0):
        parsed = ts.parse_query(query)
        return [(token.text, token.required) for token in parsed.variants[variant].tokens]

    def test_prepositions_are_optional(self):
        self.assertEqual(self.tokens('Отчёт по ЗП'), [('отчет', True), ('зп', True), ('по', False)])
        # Из одних предлогов — всё же ищем.
        self.assertEqual(self.tokens('по'), [('по', True)])

    def test_single_letters_are_ignored_but_numbers_count(self):
        self.assertTrue(ts.parse_query('а').empty)
        self.assertFalse(ts.parse_query('3').empty)
        self.assertEqual(ts.parse_query('3').numbers, [3])

    def test_task_keys(self):
        for query in ('HR-125', 'hr-125', '#HR-125', 'hr125', 'HR 125', 'рк-125', 'HR - 125'):
            parsed = ts.parse_query(query)
            self.assertIn(('HR', 125), parsed.keys, query)
            self.assertTrue(parsed.key_only, query)
        self.assertEqual(ts.parse_query('IC2-5').keys[0], ('IC2', 5))
        self.assertFalse(ts.parse_query('HR-125 табель').key_only)

    def test_wrong_layout_becomes_a_second_spelling(self):
        parsed = ts.parse_query('jnxtn pf yjz,hm')
        self.assertEqual(parsed.layout_text, 'отчет за ноябрь')
        self.assertLess(parsed.variants[1].penalty, 1.0)
        # Знаки препинания — тоже буквы русской раскладки («,» — «б»).
        self.assertEqual(ts.parse_query('j,jhel').layout_text, 'оборуд')
        # Чинить нечего — второго написания нет.
        self.assertEqual(len(ts.parse_query('отчёт').variants), 1)

    def test_cyrillic_typed_for_latin(self):
        token = ts.parse_query('еудупкфь').variants[0].tokens[0]
        self.assertEqual(token.alternate, 'telegram')


class SearchOrderTests(unittest.TestCase):
    def test_exact_before_similar_and_similar_kept(self):
        docs = [doc(1, 'Отчёт по ЗП'), doc(2, 'Отчеты водителей'), doc(3, 'Отчт с ошибкой в теме'),
                doc(4, 'Купить воду')]
        hits = ts.search('отчет', docs)
        self.assertEqual(ids(hits), [1, 2, 3])
        self.assertEqual(tiers(hits), {1: ts.TIER_EXACT, 2: ts.TIER_EXACT, 3: ts.TIER_SIMILAR})

    def test_typo_finds_the_task(self):
        docs = [doc(1, 'Заполнить табель за ноябрь'), doc(2, 'Купить воду'), doc(3, 'Сверить график смен')]
        for query in ('табль', 'тбель', 'табелль', 'таблеь', 'заполнть табель', 'ТАБЕЛЬ НОЯБРЬ', 'табеля'):
            self.assertEqual(ids(ts.search(query, docs))[:1], [1], query)

    def test_subject_before_people_before_description(self):
        docs = [doc(1, 'Позвонить в банк', description='Попросить Бекову прислать выписку'),
                doc(2, 'Сверить кассу', people=['Бекова Дана']),
                doc(3, 'Больничный Бековой')]
        self.assertEqual(ids(ts.search('бекова', docs)), [3, 2, 1])

    def test_all_words_across_fields(self):
        """«табель иванов» — тема про табель, а Иванов — исполнитель."""
        docs = [doc(1, 'Табель за ноябрь', people=['Иванов Иван']), doc(2, 'Табель за октябрь', people=['Петрова Анна']),
                doc(3, 'Договор Иванова')]
        self.assertEqual(ids(ts.search('табель иванов', docs)), [1])

    def test_stop_words_do_not_have_to_match(self):
        docs = [doc(1, 'Отчёт ЗП'), doc(2, 'Отчёт по рекламе')]
        self.assertEqual(ids(ts.search('отчет по зп', docs)), [1])

    def test_partial_match_only_when_nothing_has_all_words(self):
        docs = [doc(1, 'Отчёт по ЗП за октябрь'), doc(2, 'Сдать налоговую отчётность'), doc(3, 'Купить воду')]
        hits = ts.search('отчет октябрь налоги', docs)
        self.assertEqual(ids(hits)[:2], [1, 2])
        self.assertTrue(all(hit.tier == ts.TIER_PARTIAL for hit in hits))
        self.assertNotIn(3, ids(hits))
        # Есть задача со всеми словами — частичных нет вовсе.
        docs.append(doc(4, 'Отчёт за октябрь: налоги'))
        self.assertEqual(ids(ts.search('отчет октябрь налоги', docs)), [4])

    def test_phrase_in_a_row_wins(self):
        """Те же слова, но подряд — выше, даже у задачи постарше."""
        docs = [doc(1, 'Табель за ноябрь — заполнить', rank=10), doc(2, 'Заполнить табель за ноябрь', rank=0)]
        self.assertEqual(ids(ts.search('заполнить табель', docs)), [2, 1])

    def test_last_letter_swap_is_not_an_ending_by_itself(self):
        """«табль»: «табло» (заменена последняя буква) — не точнее «табель»
        (пропущена буква). Обе — похожие, на равных; решает свежесть."""
        docs = [doc(1, 'Перенести табло СЗоВ', rank=1), doc(2, 'Заполнить табель за ноябрь', rank=2)]
        hits = ts.search('табль', docs)
        self.assertEqual(ids(hits), [2, 1])
        self.assertEqual(tiers(hits), {1: ts.TIER_SIMILAR, 2: ts.TIER_SIMILAR})
        # Набранная форма есть в задачах — значит, это слово, и соседняя форма
        # с другим окончанием — точное совпадение, а тема — выше описания.
        docs = [doc(1, 'Зарплата за ноябрь'), doc(2, 'Выплаты', description='Пересчитать зарплату водителям')]
        hits = ts.search('зарплату', docs)
        self.assertEqual(ids(hits), [1, 2])
        self.assertEqual(tiers(hits), {1: ts.TIER_EXACT, 2: ts.TIER_EXACT})

    def test_fresher_wins_a_tie(self):
        docs = [doc(1, 'Табель за ноябрь', rank=10), doc(2, 'Табель за ноябрь', rank=20)]
        self.assertEqual(ids(ts.search('табель', docs)), [2, 1])

    def test_no_noise_from_unrelated_short_words(self):
        """«марк» — маркетинг, похожее «март» ниже; «Мария» не находится вовсе."""
        docs = [doc(1, 'План маркетинга'), doc(2, 'Отчёт за март'), doc(3, 'Задача', people=['Мария Ким'])]
        hits = ts.search('марк', docs)
        self.assertEqual(ids(hits), [1, 2])
        self.assertEqual(tiers(hits), {1: ts.TIER_EXACT, 2: ts.TIER_SIMILAR})

    def test_description_is_searched_without_typos(self):
        docs = [doc(1, 'Выплаты', description='Пересчитать зарплату водителям'), doc(2, 'Зарплата за ноябрь')]
        self.assertEqual(ids(ts.search('зарплату', docs)), [2, 1])
        # Опечатка прощается в теме, но не в описании: там случайное слово длинной
        # постановки тащило бы посторонние задачи.
        docs = [doc(1, 'Выплаты', description='Кассовая книга'), doc(2, 'Купить воду')]
        self.assertEqual(ids(ts.search('касовая', docs)), [])

    def test_kazakh_spelling(self):
        docs = [doc(1, 'Акция «7 Қазына»')]
        self.assertEqual(ids(ts.search('казына', docs)), [1])

    def test_empty_queries(self):
        docs = [doc(1, 'Отчёт')]
        for query in ('', '   ', 'а', '—', '!!!'):
            self.assertEqual(ts.search(query, docs), [], query)
        self.assertEqual(ts.search('отчет', []), [])


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            doc(1, 'Сверить график', code='HR', number=125),
            doc(2, 'Табель отдела HR', code='FIN', number=125),
            doc(3, 'Перенесённая задача', code='ACC', number=7, aliases=[('HR', 9)]),
            doc(4, 'Отчёт за 3 квартал'),
            # Старше задачи со словом «3» в теме: номер побеждает весом, а не свежестью.
            doc(5, 'Акт сверки', code='HR', number=3, rank=0),
        ]

    def test_full_key_finds_only_that_task(self):
        for query in ('HR-125', 'hr125', 'рк-125', '#HR-125'):
            hits = ts.search(query, self.docs)
            self.assertEqual(ids(hits), [1], query)
            self.assertEqual(hits[0].tier, ts.TIER_KEY)

    def test_old_key_after_moving_to_another_board(self):
        self.assertEqual(ids(ts.search('HR-9', self.docs)), [3])

    def test_bare_number_is_the_task_number_first(self):
        hits = ts.search('3', self.docs)
        self.assertEqual(ids(hits), [5, 4])
        self.assertTrue(hits[0].key_matched)

    def test_key_with_words(self):
        hits = ts.search('HR-125 график', self.docs)
        self.assertEqual(ids(hits)[0], 1)


class LayoutTests(unittest.TestCase):
    def test_forgotten_russian_layout(self):
        docs = [doc(1, 'Отчёт за ноябрь'), doc(2, 'Купить воду')]
        hits = ts.search('jnxtn', docs)
        self.assertEqual(ids(hits), [1])
        self.assertTrue(hits[0].via_layout)

    def test_forgotten_english_layout(self):
        docs = [doc(1, 'Telegram-бот не присылает коды'), doc(2, 'Купить воду')]
        self.assertEqual(ids(ts.search('еудупкфь', docs)), [1])
        self.assertEqual(ids(ts.search('еудуп', docs)), [1])  # началом слова — тоже

    def test_latin_words_are_found_as_they_are(self):
        docs = [doc(1, 'Выгрузка в Excel'), doc(2, 'Купить воду')]
        hits = ts.search('excel', docs)
        self.assertEqual(ids(hits), [1])
        self.assertFalse(hits[0].via_layout)


class DescribeTests(unittest.TestCase):
    def describe(self, query, document):
        parsed = ts.parse_query(query)
        hit = ts.search(parsed, [document])[0]
        return ts.describe(parsed, hit, document)

    def test_prefix_highlights_only_what_was_typed(self):
        match = self.describe('отч', doc(1, 'Сдать Отчёт'))
        self.assertEqual(match['subject'], [[6, 9]])

    def test_whole_word_for_typos_and_endings(self):
        subject = 'Заполнить табель за ноябрь'
        match = self.describe('табль', doc(1, subject))
        start, end = match['subject'][0]
        self.assertEqual(subject[start:end], 'табель')
        self.assertEqual(match['tier'], ts.TIER_SIMILAR)

    def test_inside_word(self):
        subject = 'Выдать зарплату'
        start, end = self.describe('плату', doc(1, subject))['subject'][0]
        self.assertEqual(subject[start:end], 'плату')

    def test_description_snippet_when_subject_has_no_word(self):
        description = ('Первая строка постановки, которая длиннее сорока знаков точно.\n'
                       'Данные взять из выгрузки Oktell за прошлый месяц.')
        match = self.describe('oktell', doc(1, 'Выплаты', description=description))
        snippet = match['snippet']
        self.assertTrue(snippet['cut_start'])
        self.assertNotIn('\n', snippet['text'])
        start, end = snippet['ranges'][0]
        self.assertEqual(snippet['text'][start:end], 'Oktell')

    def test_people_when_found_by_name(self):
        match = self.describe('жана', doc(1, 'Сверить кассу', people=['Жанна Ахметова', 'Иванов Иван']))
        self.assertEqual(match['people'], [{'name': 'Жанна Ахметова', 'ranges': [[0, 5]]}])
        self.assertIsNone(match['snippet'])

    def test_positions_are_utf16_like_the_browser(self):
        """Эмодзи — один знак в Python и два в JavaScript: подсветка в позициях
        UTF-16, иначе каждое «🔥» перед словом сдвигало её на букву."""
        subject = '🔥🔥 Срочно: табель'
        start, end = self.describe('табель', doc(1, subject))['subject'][0]
        self.assertEqual(subject.encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le'), 'табель')
        snippet = self.describe('договор', doc(1, 'Выплаты', description='👉 Проверить договор'))['snippet']
        start, end = snippet['ranges'][0]
        self.assertEqual(snippet['text'].encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le'), 'договор')
        person = self.describe('жана', doc(1, 'Касса', people=['😀 Жанна Ахметова']))['people'][0]
        start, end = person['ranges'][0]
        self.assertEqual(person['name'].encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le'), 'Жанна')
        # Без знаков вне BMP позиции те же, что у Python.
        self.assertEqual(self.describe('табель', doc(1, 'Сдать табель'))['subject'], [[6, 12]])

    def test_letters_and_digits_and_hyphens(self):
        """«2ГИС» находится по «гис» и по «2 гис», «e-mail» — по «email»."""
        docs = [doc(1, 'Интеграция 2ГИС для водителей'), doc(2, 'Настроить e-mail рассылку'), doc(3, 'Версия 1.2.5')]
        self.assertEqual(ids(ts.search('гис', docs)), [1])
        self.assertEqual(ids(ts.search('2 гис', docs)), [1])
        self.assertEqual(ids(ts.search('email', docs)), [2])
        subject = 'Интеграция 2ГИС для водителей'
        start, end = self.describe('гис', doc(1, subject))['subject'][0]
        self.assertEqual(subject[start:end], 'ГИС')

    def test_key_and_layout_flags(self):
        self.assertTrue(self.describe('HR-5', doc(1, 'Акт', code='HR', number=5))['key'])
        self.assertTrue(self.describe('jnxtn', doc(1, 'Отчёт'))['layout'])
        self.assertFalse(self.describe('отчет', doc(1, 'Отчёт'))['layout'])


class SpeedTests(unittest.TestCase):
    """Задач на проде ~2 тыс. (ноябрь 2026): поиск по ним — десятки миллисекунд."""

    def test_three_thousand_tasks(self):
        rnd = random.Random(7)
        verbs = ['Заполнить', 'Проверить', 'Подготовить', 'Согласовать', 'Выгрузить', 'Отправить', 'Сверить']
        objects = ['табель', 'отчёт', 'договор', 'график смен', 'акт сверки', 'счёт', 'заявку', 'рассылку']
        tails = ['за ноябрь', 'по ЗП', 'для СЗоВ', 'водителей', 'операторов ОП', 'в Excel', 'Oktell', '2025']
        names = ['Иванов Иван', 'Петрова Анна', 'Жанна Ахметова', 'Нурлан Сериков', 'Мария Ким']
        docs = [doc(index, ' '.join([rnd.choice(verbs), rnd.choice(objects), rnd.choice(tails)]),
                    description='Нужно до пятницы, данные взять из выгрузки Oktell. ' * rnd.randint(0, 3),
                    people=rnd.sample(names, 2), code='HR', number=index)
                for index in range(1, 3001)]
        started = time.perf_counter()
        for query in ('отчт', 'табель ноябрь', 'жана', 'HR-125', 'jnxtn', 'сотрдуник', 'выгрузку oktell'):
            ts.search(query, docs)
        per_query = (time.perf_counter() - started) / 7
        # Обычно ~25 мс; потолок с запасом на медленную машину CI.
        self.assertLess(per_query, 0.5)


if __name__ == '__main__':
    unittest.main()
