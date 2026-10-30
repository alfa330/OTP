# -*- coding: utf-8 -*-
"""Справочник «Парки» в помощнике (wiki/park_cards.py, просьба владельца 30.10.2026).

Что стережётся:
  * парк узнаётся так, как его пишут операторы (вопросы — из журнала помощника
    за 10.08–30.10.2026), а карточка не цепляется к посторонней теме: имя-обычное
    слово («стабильный интернет») — не парк, продолжение разговора берёт парк из
    ПОСЛЕДНЕЙ реплики, сводка всех парков — только на вопрос про парки вообще,
    не про бонусы, пополнение и комиссию Яндекса;
  * на «куда регистрировать» и «адрес в Шымкенте» отвечают регистрация и офисы —
    карточка с городами парка там лишняя, если о парке не спросили другого;
  * текст держит все числа буквально (защита ungrounded_numbers), не говорит
    «нет»/«не указана», когда данных просто не завели, и помечает прошедший срок
    строкой, а не всю карточку;
  * сервер не подшивает карточку под ответ, где парк просто упомянут;
  * граница — как у вкладки: пространство выдано не гостем, вкладка «Парки»
    включена; источник в истории — по доступу сейчас; доступ читается один раз
    на запрос;
  * чип ведёт на страницу парка, а «К помощнику» — в тот же разговор.

Без базы: данные синтетические, той же формы, что на проде (замер 30.10.2026:
19 парков в «Таксопарках», описание у 17, головной офис у 8, акций ноль).
"""

import ast
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from wiki import directory
from wiki import park_cards
from wiki import routes_ai
from wiki.ai import answer as ai_answer
from wiki.ai import currency
from wiki.ai import store as ai_store

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 30)

WITHDRAWAL = ('Комиссия парка при выводе средств:\n'
              '•\tПри снятии суммы от 1000тг до 10 000тг - комиссия составит фиксированные 110тг;\n'
              '•\tПри снятии суммы от 10 001тг до 100 000тг - комиссия составит 1,1% от суммы;')
HEAD = {'id': 2, 'name': 'Офис Алматы №1', 'city': 'Алматы',
        'address': 'Улица Жамбыла, 172В угол улицы Байзакова'}


def park(park_id, name, slug, *, commission=2.0, description=None, head=None, **extra):
    row = {'id': park_id, 'slug': slug, 'name': name, 'description': description,
           'city': None, 'address': None, 'website': None, 'commission': commission,
           'status': 'active', 'position': park_id, 'head_office_id': (head or {}).get('id'),
           'head_office': head, 'promotions_count': 0}
    row.update(extra)
    return row


PARKS = [
    park(1, 'iTaxi', 'itaxi', head=HEAD,
         description='iTaxi — один из крупных партнёров Яндекс Такси в Казахстане.\n\n'
                     'Парк представлен в нескольких крупных городах Казахстана, включая '
                     'Алматы, Астану, Тараз.\n\n' + WITHDRAWAL),
    park(2, 'Taxi24', 'taxi24', head=HEAD, description='Такси 24 — таксопарк.\n' + WITHDRAWAL),
    park(3, 'Департамент', 'departament', head=HEAD, description='Департамент Такси — партнёр.'),
    park(4, 'Честный', 'chestnyy-taksopark', head=HEAD,
         description='Честный — сертифицированный партнёр.\n' + WITHDRAWAL),
    park(5, 'Global', 'global', head=HEAD, description='Global — крупный таксопарк.'),
    park(9, 'Qazaq', 'qazaq', description='Qazaq — казахстанский таксопарк.'),
    park(11, 'Стабильный', 'stabilnyy', description='Стабильный — таксопарк в Алматы.'),
    park(12, 'Jana такси', 'jana-taksi', description='Jana Taxi — парк. Комиссия составляет 2%.'),
    park(13, '2dongelek', '2dongelek', commission=4.6, description='EKI DONGELEK — парк.'),
    park(14, 'Тенге Такси', 'tenge-taksi', description='Тенге Такси — про тенге.'),
    park(16, 'Ноль такси', 'nol-taksi', commission=0.0,
         description='Ноль Такси — 0% комиссии парка.\n\nКомиссия парка при выводе средств:\n\n'
                     '•\tПри снятии суммы от 1000 тг до 10 000 тг - комиссия составит 550 тг.\n'
                     '•\tПри снятии суммы от 10 001 тг до 100 000 тг - комиссия составит 5.5% от суммы.'),
    park(17, 'iTaxi VIP', 'itaxi-vip', description='iTaxi VIP — тот же iTaxi.'),
    park(18, 'Hokage (Wolt)', 'hokage-wolt', commission=None),
    park(19, 'Центр Регистраций (Регионы)', 'centr-registraciy-regiony'),
]
# Номера — синтетические: {park_id: {office_id | None: [{phone, note}]}}.
PHONES = {
    1: {2: [{'phone': '+7 700 000 01 01', 'note': None}]},
    4: {None: [{'phone': '+7 700 000 04 01', 'note': None},
               {'phone': '+7 700 000 04 02', 'note': 'WhatsApp'}],
        2: [{'phone': '+7 700 000 04 03', 'note': None}]},
    16: {None: [{'phone': '+7 700 000 16 01', 'note': None}]},
    18: {None: [{'phone': '+7 700 000 18 01', 'note': None}]},
}
OFFICES = {
    1: [{'office_id': 2, 'name': 'Офис Алматы №1', 'city': 'Алматы', 'is_online': False,
         'phones': PHONES[1][2], 'schedule': None, 'note': None}],
    4: [{'office_id': 2, 'name': 'Офис Алматы №1', 'city': 'Алматы', 'is_online': False,
         'phones': PHONES[4][2], 'schedule': None, 'note': None}],
}
CATALOG = {'parks': PARKS, 'phones': PHONES, 'offices': OFFICES, 'promotions': []}
TOTAL = len(PARKS)


def history_of(prior):
    """Прошлые реплики человека — как их отдаёт store.recent_turns, с ответами."""
    turns = []
    for text in prior:
        turns += [{'role': 'user', 'kind': 'question', 'text': text},
                  {'role': 'assistant', 'kind': 'answer', 'text': 'ответ'}]
    return turns


def analysis_of(question, prior=()):
    history = history_of(prior)
    return park_cards.analyze(question, ai_answer.enrich_query(question, history), history)


def decide(question, prior=(), answered=()):
    return park_cards.plan(analysis_of(question, prior), PARKS, answered=answered)


def cards(question, prior=(), answered=()):
    return [item['name'] for item in decide(question, prior, answered)['cards']]


def summary(question, prior=(), answered=()):
    return len(decide(question, prior, answered)['summary'] or [])


def rows(question, prior=(), answered=(), catalog=CATALOG):
    return park_cards.build_ai_rows(analysis_of(question, prior), catalog, space_id=11,
                                    answered=answered, on_date=DAY)


class NamedParkTest(unittest.TestCase):
    """Вопросы журнала, на которые помощник отвечал «нет информации», архивом или
    переспросом, хотя во вкладке ответ был."""

    def test_park_is_found_as_operators_write_it(self):
        for question, expected in (
                ('Комиссия в парке Itaxi', ['iTaxi']),
                ('подскажи комиссию таксопрака Айтакси', ['iTaxi']),
                ('по парку Департамент сколько комиссия', ['Департамент']),
                ('расскажи о таксопарке глобал', ['Global']),
                ('процент комиссия глобал', ['Global']),
                ('екы донгелек коммисяи?', ['2dongelek']),
                ('xtcnysq nfrcjgfhr', ['Честный']),          # забытая раскладка
                ('Taxi24', ['Taxi24']),                      # одно название — тоже вопрос
                ('что такое хокаге', ['Hokage (Wolt)']),
                ('какая комиссия при выводе средств в тасопарке 0 такси', ['Ноль такси']),
                ('itaxi vip', ['iTaxi VIP']),                # не iTaxi вдобавок
                ('Какие акции есть в таксопарке казак', ['Qazaq']),
                ('номер тех поддержки регионы', ['Центр Регистраций (Регионы)'])):
            with self.subTest(question=question):
                self.assertEqual(cards(question), expected)

    def test_money_and_greeting_are_not_parks(self):
        for question in ('сколько стоит 2000 тенге', 'Саламатсыз ба', 'халык банк реквизиты'):
            with self.subTest(question=question):
                self.assertEqual(decide(question)['cards'], [])

    def test_name_made_of_an_ordinary_word_needs_a_reason(self):
        """«Стабильный», «Честный», «Департамент», «Регионы» — ещё и обычные слова:
        без вопроса о парке, без «парк»/«такси» и с другими словами рядом это не
        парк (разбор 30.10.2026). Карточка там отключала бы и переспрос."""
        for question in ('у водителя стабильный интернет, но приложение вылетает',
                         'честный отзыв о работе', 'департамент транспорта штраф',
                         'какие регионы обслуживаем'):
            with self.subTest(question=question):
                self.assertEqual(cards(question), [])
        for question, expected in (('департамент', ['Департамент']),
                                   ('честный таксопарк', ['Честный']),
                                   # «таксопарак» — опечатка журнала, но тоже «парк».
                                   ('честный таксопарак это какой город', ['Честный']),
                                   ('стабильный парк комисиия', ['Стабильный']),
                                   ('честный номера телефонов', ['Честный'])):
            with self.subTest(question=question):
                self.assertEqual(cards(question), expected)

    def test_itaxi_and_itaxi_vip_named_together_are_two_parks(self):
        for question in ('сравни iTaxi и iTaxi VIP', 'чем отличается айтакси от айтакси вип',
                         'комиссия itaxi и itaxi vip'):
            with self.subTest(question=question):
                self.assertEqual(sorted(cards(question)), ['iTaxi', 'iTaxi VIP'])
        self.assertEqual(cards('номер itaxi vip и честный'), ['Честный', 'iTaxi VIP'])

    def test_more_than_three_parks_get_a_summary_of_them(self):
        decision = decide('сравни комиссию iTaxi, Global, Jana такси и Честный')
        self.assertEqual(decision['cards'], [])
        self.assertEqual({item['name'] for item in decision['summary']},
                         {'iTaxi', 'Global', 'Jana такси', 'Честный'})
        row = rows('сравни комиссию iTaxi, Global, Jana такси и Честный')[0]
        self.assertEqual(row['heading_path'], 'iTaxi, Честный, Global, Jana такси')
        self.assertTrue(row['text'].startswith('Таксопарки из вопроса — комиссия парка и номера:'))
        self.assertEqual(decide('сравни вывод средств iTaxi, Global, Jana такси и Честный')['extras'],
                         {'withdrawal'})


class AnsweredElsewhereTest(unittest.TestCase):
    """Город регистрации и офис города отвечают соседние справочники: в описании
    парка перечислены города, и модель взяла бы город оттуда."""

    def test_registration_and_office_answers_drop_the_card(self):
        question = 'водитель обращается с кентау хочет зарегатся в айтакси, в какой его регать?'
        self.assertEqual(cards(question, answered={'registration'}), [])
        self.assertEqual(cards('itaxi адрес в шымкенте', answered={'office'}), [])
        # Комиссия Яндекса в городе — не ответ про парк: карточка остаётся.
        self.assertEqual(cards('комиссия честный в алматы', answered={'city'}), ['Честный'])

    def test_registration_question_without_its_rows_drops_the_card_too(self):
        """Город не назван или неизвестен — регистрация строк не даёт (попросит
        город), а карточка с городами из описания подсказала бы модели чужой."""
        for question in ('куда регистрировать водителя айтакси?',
                         'водитель из Лисаковска хочет в jana такси, куда регать?'):
            with self.subTest(question=question):
                self.assertEqual(cards(question), [])
        # Спросили и о самом парке — карточка нужна.
        self.assertEqual(cards('номер айтакси, водитель из рудного, куда регать'), ['iTaxi'])

    def test_asked_about_the_park_itself_keeps_the_card(self):
        for question in ('номер айтакси в шымкенте', 'акции айтакси в шымкенте',
                         'сайт айтакси в шымкенте', 'байланыс айтакси шымкент',
                         'сколько берет айтакси в шымкенте', '% айтакси в шымкенте'):
            with self.subTest(question=question):
                self.assertEqual(cards(question, answered={'office'}), ['iTaxi'])


class FollowUpTest(unittest.TestCase):
    def test_short_follow_up_takes_the_park_of_the_last_reply(self):
        for question in ('а номер?', 'а какая комиссия парка', 'а при выводе сколько?',
                         'подробнее', 'а адрес?'):
            with self.subTest(question=question):
                self.assertEqual(cards(question, prior=['Комиссия в парке Itaxi']), ['iTaxi'])
        # Реплика-продолжение отсылает к своей предыдущей.
        self.assertEqual(cards('а сайт?', prior=['Комиссия в парке Itaxi', 'а номер?']), ['iTaxi'])
        # Из двух названных — последний.
        self.assertEqual(cards('а номер?', prior=['комиссия iTaxi', 'а Global?']), ['Global'])

    def test_new_topic_is_not_a_follow_up(self):
        for prior, question in (
                (['номер айтакси'], 'условия конкурса лимонопад'),
                (['астана тенге такси офис'], 'как поменять номер клиента'),
                (['itaxi адрес в шымкенте'], 'акции на такси про'),     # приложение
                (['Комиссия в парке Itaxi'], 'спасибо'),
                (['Комиссия в парке Itaxi'], 'а в астане?'),
                # Журнал: тема сменилась в средней реплике — парк двухходовой давности не тянем.
                (['itaxi адрес в шымкенте', 'акции на такси про'], 'акции'),
                (['комиссия iTaxi', 'какая комиссия эконом в Алматы'], 'а при выводе?'),
                # Длиннее пяти слов — уже отдельный вопрос.
                (['Комиссия в парке Itaxi'], 'а какой номер у вас для связи')):
            with self.subTest(question=question):
                self.assertEqual(decide(question, prior=prior)['cards'], [])

    def test_follow_up_of_a_summary_repeats_the_summary(self):
        decision = decide('а номера?', prior=['комиссия всех парков'])
        self.assertEqual((len(decision['summary']), decision['extras']), (TOTAL, set()))
        decision = decide('а при выводе?', prior=['комиссия парков'])
        self.assertEqual((len(decision['summary']), decision['extras']), (TOTAL, {'withdrawal'}))
        # Журнал: после сводки спросили про офисы — их даёт справочник офисов.
        self.assertIsNone(decide('номера телефонов офисов',
                                 prior=['номера техподдержки', 'номера телефонов парков'])['summary'])

    def test_question_about_every_park_is_not_about_the_last_one(self):
        decision = decide('инфо про каждый таксопарк', prior=['департамент'])
        self.assertEqual(decision['cards'], [])
        self.assertEqual(len(decision['summary']), TOTAL)
        self.assertEqual(decision['extras'], {'info'})

    def test_without_history_there_is_no_follow_up(self):
        # Витрина /ai/search спрашивает без разговора.
        analysis = park_cards.analyze('а номер?', 'Комиссия в парке Itaxi а номер?')
        self.assertEqual(park_cards.plan(analysis, PARKS)['cards'], [])


class SummaryTest(unittest.TestCase):
    def test_questions_about_parks_in_general(self):
        for question in ('комиссия парков', 'комиссия всех парков', 'какая комиссия парка',
                         'номера телефонов парков', 'каталок таксопарков',
                         'какой номер у теххподдержки разных таксопарков', 'какие у нас парки',
                         'комиссия яндекса и номера всех парков'):
            with self.subTest(question=question):
                decision = decide(question)
                self.assertEqual(decision['cards'], [])
                self.assertEqual(len(decision['summary'] or []), TOTAL)

    def test_other_questions_with_the_word_park_are_left_to_articles(self):
        for question in (
                'номер таксопарка достойыный',          # парка во вкладке нет
                'что такое таксопарк',
                'Какие документы нужны для подключения к парку?',
                'Каакая комиссия у яндекса в городе Уральск в таксопарке Амаанат',
                'комиссия яндекса в парках',
                'комиссия яндекса во всех парках',      # «все» не снимает «Яндекс»
                'какая комиссия яндекса в каждом парке',
                'парковый ип',
                'Офисы таксопарков в актау',
                'комиссия',                              # комиссия Яндекса — «Города»
                'какие есть акции для новичков в парках',
                'Сколько процентов берет парк с бонусов?',
                'комиссия парка с акции',
                'какой процент парка при пополнении'):
            with self.subTest(question=question):
                decision = decide(question)
                self.assertEqual((decision['cards'], decision['summary']), ([], None))

    def test_extras_follow_the_question(self):
        self.assertEqual(decide('комиссия парков')['extras'], set())
        # Первая фраза описания — только на «инфо про каждый парк», не на любое «инфо».
        self.assertEqual(decide('информация и номера парков')['extras'], set())
        # «Таксопарак» — опечатка журнала, и сводка его понимает.
        self.assertEqual(summary('комиссия всех таксопараков'), TOTAL)
        self.assertEqual(decide('комиссия при выводе во всех парках')['extras'], {'withdrawal'})
        # «Выведи» — просьба показать, а не вывод денег; пополнение — не вывод.
        self.assertNotIn('withdrawal', park_cards.asked_facts(['выведи список парков']))
        self.assertEqual(park_cards.asked_facts(['пополнение через каспи']), {'topup'})

    def test_kazakh_questions(self):
        for question in ('таксопарктердің комиссиясы қанша', 'парктердің телефон нөмірлері',
                         'барлық парктер'):
            with self.subTest(question=question):
                self.assertEqual(summary(question), TOTAL)
        self.assertIn('phone', analysis_of('ай такси нөмірі')['park_facts'])
        self.assertEqual(cards('ай такси нөмірі', answered={'office'}), ['iTaxi'])


class CardTextTest(unittest.TestCase):
    def text(self, question, catalog=CATALOG):
        return rows(question, catalog=catalog)[0]['text']

    def test_card_holds_every_fact_of_the_tab(self):
        text = self.text('честный')
        for line in ('Таксопарк «Честный».', 'Комиссия парка Честный: 2%.',
                     'Головной офис парка Честный: «Офис Алматы №1», город Алматы, адрес: '
                     'Улица Жамбыла, 172В угол улицы Байзакова.',
                     'Телефон парка Честный: +7 700 000 04 01.',
                     'Телефон парка Честный: +7 700 000 04 02 (WhatsApp).',
                     'Телефон парка Честный в офисе «Офис Алматы №1» (Алматы): +7 700 000 04 03.',
                     'Описание парка Честный:',
                     '• При снятии суммы от 10 001тг до 100 000тг - комиссия составит 1,1% от суммы;'):
            self.assertIn(line, text.splitlines())

    def test_legacy_fields_and_office_without_numbers(self):
        legacy = dict(PARKS[0], city='Алматы', head_office=None, head_office_id=None,
                      address='ул. Абая 1.')
        offices = {1: [{'office_id': 7, 'name': 'Офис Астана', 'city': 'Астана', 'phones': []}]}
        text = self.text('айтакси', dict(CATALOG, parks=[legacy], offices=offices, phones={}))
        for line in ('Город парка iTaxi: Алматы.', 'Адрес парка iTaxi: ул. Абая 1.',
                     'Офис парка iTaxi: «Офис Астана» (Астана).'):
            self.assertIn(line, text.splitlines())

    def test_missing_data_is_not_a_negative_fact(self):
        text = self.text('честный')
        # Акций и сайта во вкладке нет — о них ни слова: «акций нет» спорило бы со
        # статьёй «Все акции».
        self.assertNotIn('кци', text.replace('Таксопарк', ''))
        self.assertNotIn('Сайт', text)
        # Комиссии нет — строки нет: «в карточке не указана» модель перенесла бы, и
        # пробел не ушёл бы супервайзеру.
        self.assertNotIn('Комиссия парка', self.text('хокаге'))
        self.assertIn('Комиссия парка Ноль такси: 0%.', self.text('ноль такси'))

    def test_promotions_of_this_park_only(self):
        promotions = [
            {'id': 7, 'title': 'Быстрый старт', 'description': '5000 тг за 20 поездок.',
             'starts_at': '2026-10-01T00:00:00', 'ends_at': '2026-11-30T23:59:00',
             'park_ids': [1], 'is_running': True},
            {'id': 8, 'title': 'Чужая', 'description': None, 'starts_at': None, 'ends_at': None,
             'park_ids': [2], 'is_running': True}]
        catalog = dict(CATALOG, parks=[dict(PARKS[0], website='https://itaxi.example'), PARKS[1]],
                       promotions=promotions)
        text = rows('айтакси', catalog=catalog)[0]['text']
        self.assertIn('Сайт парка iTaxi: https://itaxi.example.', text)
        self.assertIn('Акция парка iTaxi «Быстрый старт», с 01.10.2026 по 30.11.2026: '
                      '5000 тг за 20 поездок.', text)
        self.assertNotIn('Чужая', text)

    def test_expired_line_is_marked_not_the_whole_card(self):
        catalog = dict(CATALOG, parks=[dict(PARKS[0], description='Розыгрыш авто до 30.09.2026.\n'
                                                                  'Бонус до 31.12.2026.\n'
                                                                  'Приз до 30.10.2026.')])
        row = rows('айтакси', catalog=catalog)[0]
        self.assertIn('(срок истёк 30.09.2026) Розыгрыш авто до 30.09.2026.', row['text'])
        self.assertIn('\nБонус до 31.12.2026.', row['text'])
        # Срок «до сегодня» ещё идёт.
        self.assertIn('\nПриз до 30.10.2026.', row['text'])
        # Комиссия и номера — живые: весь фрагмент «уже не действует» не становится.
        self.assertFalse(currency.chunk_currency(row, DAY)['stale'])

    def test_numbers_of_an_answer_are_found_in_the_card(self):
        row = rows('вывод средств с ноль такси сколько комиссия')[0]
        answer = ('Комиссия парка Ноль такси — 0%. При выводе от 1000 до 10 000 тг — 550 тг, '
                  'от 10 001 до 100 000 тг — 5.5%. Телефон: +7 700 000 16 01.')
        self.assertEqual(ai_answer.ungrounded_numbers(answer, [row], on_date=DAY), [])


class SummaryTextTest(unittest.TestCase):
    def test_numbers_without_an_office_are_not_read_as_office_ones(self):
        text = rows('комиссия парков')[0]['text']
        self.assertIn('Таксопарки во вкладке «Парки» — %d. Комиссия парка и номера:' % TOTAL, text)
        self.assertIn('Честный: комиссия парка 2%; телефоны: +7 700 000 04 01, +7 700 000 04 02 '
                      '(WhatsApp); в офисе «Офис Алматы №1» (Алматы) +7 700 000 04 03.', text)
        self.assertIn('2dongelek: комиссия парка 4,6%.', text)
        self.assertIn('Hokage (Wolt): комиссия парка не указана; телефон: +7 700 000 18 01.', text)

    def test_withdrawal_and_gist_on_request(self):
        text = rows('комиссия при выводе во всех парках')[0]['text']
        self.assertIn('Ноль такси, комиссия при выводе средств: При снятии суммы от 1000 тг до '
                      '10 000 тг - комиссия составит 550 тг; При снятии суммы от 10 001 тг до '
                      '100 000 тг - комиссия составит 5.5% от суммы.', text)
        self.assertNotIn('о парке:', text)
        info = rows('инфо про каждый таксопарк')[0]['text']
        self.assertIn('iTaxi, о парке: iTaxi — один из крупных партнёров Яндекс Такси в Казахстане.',
                      info)

    def test_running_promotions_are_listed_with_their_parks(self):
        promotions = [{'id': 7, 'title': 'Быстрый старт', 'starts_at': None,
                       'ends_at': '2026-11-30T23:59:00', 'park_ids': [1, 4], 'is_running': True}]
        text = rows('комиссия парков', catalog=dict(CATALOG, promotions=promotions))[0]['text']
        self.assertIn('Акция «Быстрый старт» — парки: iTaxi, Честный; по 30.11.2026.', text)

    def test_withdrawal_lines_and_gist_helpers(self):
        self.assertEqual(park_cards.withdrawal_lines(WITHDRAWAL), [
            'При снятии суммы от 1000тг до 10 000тг - комиссия составит фиксированные 110тг',
            'При снятии суммы от 10 001тг до 100 000тг - комиссия составит 1,1% от суммы'])
        self.assertEqual(park_cards.withdrawal_lines('Комиссия составляет 2%.'), [])
        # Пункты без заголовка «…при выводе средств:» — не строки вывода.
        self.assertEqual(park_cards.withdrawal_lines(WITHDRAWAL.replace('средств:', 'средств')), [])
        self.assertEqual(park_cards.gist('Первая фраза. Вторая.'), 'Первая фраза.')
        self.assertEqual(park_cards.gist('а' * 300), 'а' * park_cards.GIST_LIMIT + '…')
        self.assertEqual(park_cards.gist(None), '')


class RowContractTest(unittest.TestCase):
    def test_row_keeps_the_retriever_contract(self):
        row = rows('Комиссия в парке Itaxi')[0]
        self.assertEqual((row['title'], row['heading_path'], row['article_id']),
                         ('Парки', 'iTaxi', None))
        self.assertEqual((row['source_kind'], row['tab'], row['ref_id'], row['ref_key'],
                          row['space_id']), ('park', 'parks', 1, 'itaxi', 11))
        self.assertEqual(row['found_by'], [directory.DIRECTORY_BRANCH])
        self.assertTrue(row['directory_hit'])
        self.assertEqual(row['parks'], [{'id': 1, 'name': 'iTaxi', 'commission': 2.0}])
        # Номера фрагментов не пересекаются с соседними ветками (регистрация — 3000).
        self.assertLess(row['chunk_id'], -4000)
        self.assertGreater(row['chunk_id'], -5000)
        summary_row = rows('комиссия парков')[0]
        self.assertEqual((summary_row['heading_path'], summary_row['ref_id'], summary_row['ref_key']),
                         ('Все парки', None, None))

    def test_long_slug_leaves_the_chip_on_the_tab(self):
        catalog = dict(CATALOG, parks=[dict(PARKS[0], slug='x' * 65)])
        self.assertIsNone(rows('айтакси', catalog=catalog)[0]['ref_key'])

    def test_store_keeps_what_the_chip_needs(self):
        source = ai_answer.build_sources([1], rows('Комиссия в парке Itaxi'), 'Комиссия парка iTaxi: 2%.')[0]
        self.assertEqual((source['source_kind'], source['tab'], source['ref_id'], source['ref_key'],
                          source['space_id']), ('park', 'parks', 1, 'itaxi', 11))
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (10, None)]
        ai_store.append_message(cursor, 5, role='assistant', text='ответ', sources=[source])
        stored = cursor.execute.call_args_list[-1].args[1]
        self.assertEqual((stored['source_kind'], stored['tab'], stored['ref_id'], stored['ref_key'],
                          stored['article_id'], stored['chunk_id']),
                         ('park', 'parks', 1, 'itaxi', None, None))


class AnswerTest(unittest.TestCase):
    def test_rule_comes_only_with_park_rows(self):
        self.assertIn('14. ПАРКИ.', ai_answer.system_prompt(rows('комиссия честный')))
        self.assertNotIn('14. ПАРКИ.', ai_answer.system_prompt([{'source_kind': 'article'}]))
        prompt = ai_answer.build_user_prompt('комиссия честный', rows('комиссия честный'))
        self.assertIn('[1] Справочник «Парки», запись «Честный»', prompt)

    def test_named_park_skips_the_clarify_gate(self):
        # «расскажи о таксопарке глобал» на проде 30.10.2026 ушло в переспрос.
        found = rows('расскажи о таксопарке глобал')
        article = {'chunk_id': 5, 'article_id': 77, 'text': 'x', 'similarity': 0.7, 'found_by': [1]}
        self.assertEqual(ai_answer.usable_chunks(found), found)
        self.assertFalse(ai_answer.should_clarify('расскажи о таксопарке глобал', found + [article])[0])

    def test_card_is_attached_only_when_the_answer_names_the_park_and_its_number(self):
        card = rows('ноль такси номер')[0]
        article = {'chunk_id': 5, 'article_id': 77, 'title': 'Все акции', 'slug': 'akcii',
                   'heading_path': '', 'text': 'Ноль такси: лимит на заправку 5000 тг в неделю.',
                   'similarity': 0.8, 'found_by': [1]}
        chunks = [card, article]
        # Ответ из статьи, где парк упомянут, — карточку сервер сам не ставит, хотя
        # слов у них общих хватает на опору (ноль, такси, комиссии, парка, выводе…).
        about_fuel = ('В Ноль такси лимит на заправку — 5000 тг в неделю; комиссии парка '
                      'при выводе средств это не касается.')
        self.assertGreaterEqual(ai_answer._support_score(card, about_fuel), ai_answer.SUPPORT_FLOOR)
        self.assertEqual([s['article_id'] for s in ai_answer.build_sources([2], chunks, about_fuel)],
                         [77])
        # Номер из карточки — ставит, даже если модель её не назвала.
        phone = 'Телефон Ноль такси: +7 700 000 16 01.'
        sources = ai_answer.build_sources([], chunks, phone)
        self.assertEqual((sources[0]['source_kind'], sources[0]['attributed']), ('park', True))
        # «110 тенге» — деньги, а не парк «Тенге Такси».
        catalog = dict(CATALOG, phones={14: {None: [{'phone': '+7 700 000 14 01', 'note': None}]}})
        tenge = rows('тенге такси', catalog=catalog)[0]
        self.assertFalse(ai_answer._names_park(tenge, 'Комиссия при выводе — 110 тенге, '
                                                      'звонить +7 700 000 14 01.'))
        self.assertTrue(ai_answer._names_park(tenge, 'Телефон Тенге Такси: +7 700 000 14 01.'))

    def test_card_is_attached_under_its_own_commission(self):
        """«2%» — одна цифра, и числа от двух знаков её не видят; а это главный
        ответ на вопрос «комиссия в парке Itaxi»."""
        card = rows('Комиссия в парке Itaxi')[0]
        self.assertTrue(ai_answer._names_park(card, 'Комиссия парка iTaxi — 2%.'))
        self.assertTrue(ai_answer._names_park(card, 'Комиссия парка iTaxi — 2 %.'))
        self.assertFalse(ai_answer._names_park(card, 'Комиссия парка iTaxi — 12%.'))
        self.assertFalse(ai_answer._names_park(card, 'Комиссия Яндекса — 2%.'))
        dongelek = rows('екы донгелек комиссия')[0]
        self.assertTrue(ai_answer._names_park(dongelek, 'У 2dongelek комиссия парка 4,6%.'))
        sources = ai_answer.build_sources([], [card], 'Комиссия парка iTaxi — 2%.')
        self.assertEqual([(s['source_kind'], s['attributed']) for s in sources], [('park', True)])

    def test_summary_is_attached_for_a_table_of_parks(self):
        summary_row = rows('комиссия парков')[0]
        self.assertTrue(ai_answer._names_park(summary_row, '| iTaxi | 2% |\n| 2dongelek | 4,6% |'))
        self.assertFalse(ai_answer._names_park(summary_row, 'Комиссия iTaxi — 2%.'))

    def test_missing_fact_in_the_card_goes_to_the_supervisor(self):
        # Пробел в данных вкладки — тоже вопрос супервайзеру (#321).
        self.assertTrue(ai_answer.admits_missing('В карточке парка Hokage (Wolt) нет информации '
                                                 'о комиссии.'))
        self.assertTrue(ai_answer.admits_missing('В доступных фрагментах нет информации о комиссии '
                                                 'парка Hokage (Wolt).'))


class AccessTest(unittest.TestCase):
    SPACES = [{'id': 11, 'features': {}}, {'id': 12, 'features': {'parks': False}}]

    def space(self, allowed, requested, **kwargs):
        with patch.object(directory.queries, 'spaces_for_user', return_value=allowed) as spaces, \
             patch.object(directory.structure, 'list_spaces', return_value=self.SPACES):
            result = directory.directory_space(MagicMock(), {}, requested, **kwargs)
        self.assertFalse(spaces.call_args.kwargs['include_guest'])
        return result

    def test_parks_tab_must_be_on_and_granted_not_as_a_guest(self):
        self.assertEqual(self.space([11], 11, tabs=('parks',)), {'id': 11, 'parks': True})
        self.assertIsNone(self.space([12], 12, tabs=('parks',)))
        self.assertIsNone(self.space([11], 12, tabs=('parks',)))
        # Офисы и города спрашиваются по-прежнему: у Тез «Парки» выключены, а офисы — нет.
        self.assertEqual(self.space([12], 12), {'id': 12, 'offices': True, 'cities': True})

    def test_access_is_read_once_per_request(self):
        """Регистрация, офисы с городами и парки спрашивают пространство за один
        вопрос трижды — запросы к базе уходят один раз (ctx живёт запрос)."""
        ctx = {}
        with patch.object(directory.queries, 'spaces_for_user', return_value=[11]) as spaces, \
             patch.object(directory.structure, 'list_spaces', return_value=self.SPACES) as listed:
            for tabs in (directory.DIRECTORY_TABS, ('cities',), ('parks',)):
                self.assertEqual(directory.directory_space(MagicMock(), ctx, 11, tabs=tabs)['id'], 11)
            self.assertEqual(directory.access_map(MagicMock(), ctx)[11]['parks'], True)
            # Новый запрос — новый ctx: отозванный доступ не переживает память.
            directory.directory_space(MagicMock(), {}, 11)
        self.assertEqual((spaces.call_count, listed.call_count), (2, 2))

    def test_history_shows_a_park_source_only_while_the_tab_is_open(self):
        with patch.object(directory.queries, 'spaces_for_user', return_value=[11, 12]), \
             patch.object(directory.structure, 'list_spaces', return_value=self.SPACES):
            access = directory.access_map(MagicMock(), {})
        self.assertEqual(access[11], {'offices': True, 'cities': True, 'parks': True})
        self.assertFalse(access[12]['parks'])
        self.assertTrue(ai_store._directory_available('park', 'parks', 11, access))
        self.assertFalse(ai_store._directory_available('park', 'parks', 12, access))

    def test_closed_tab_reads_nothing(self):
        cursor = MagicMock()
        with patch.object(park_cards.directory, 'directory_space', return_value=None), \
             patch.object(park_cards.wiki_parks, 'list_parks') as listed:
            self.assertEqual(park_cards.ai_rows(cursor, {}, 'комиссия честный', space_id=11), [])
        listed.assert_not_called()
        cursor.execute.assert_not_called()


class LoadTest(unittest.TestCase):
    def test_details_are_read_once_and_only_for_shown_parks(self):
        cursor = MagicMock()
        stale = {'id': 9, 'title': 'Закончилась', 'park_ids': [4], 'is_running': False}
        with patch.object(park_cards.directory, 'directory_space',
                          return_value={'id': 11, 'parks': True}) as space, \
             patch.object(park_cards.wiki_parks, 'list_parks', return_value=PARKS) as listed, \
             patch.object(park_cards.wiki_offices, 'offices_by_park', return_value=OFFICES) as offices, \
             patch.object(park_cards.wiki_offices, 'phones_by_park', return_value=PHONES) as phones, \
             patch.object(park_cards.wiki_parks, 'list_promotions', return_value=[stale]) as promos:
            found = park_cards.ai_rows(cursor, {}, 'комиссия честный', space_id=11)
            self.assertEqual(space.call_args.kwargs['tabs'], ('parks',))
            self.assertEqual(listed.call_args.kwargs['space_id'], 11)
            self.assertEqual(offices.call_args.args[1], [4])
            self.assertEqual(phones.call_args.args[1], [4])
            self.assertEqual(promos.call_args.kwargs['space_id'], 11)
            self.assertEqual([row['heading_path'] for row in found], ['Честный'])
            # Закончившаяся акция в карточку не попадает.
            self.assertNotIn('Закончилась', found[0]['text'])
            # Продолжение — по разговору, который передал маршрут.
            history = history_of(['комиссия честный'])
            again = park_cards.ai_rows(cursor, {}, 'а номер?', space_id=11,
                                       search_query='комиссия честный а номер?', history=history)
            self.assertEqual([row['heading_path'] for row in again], ['Честный'])
            offices.reset_mock()
            self.assertEqual(park_cards.ai_rows(cursor, {}, 'как оформить смз', space_id=11), [])
            offices.assert_not_called()


class RouteTest(unittest.TestCase):
    """routes_ai.directory_rows: карточки — последними, у каждого справочника свой
    савпоинт, и сбой одного не отнимает у оператора другие."""

    REGISTRATION = [{'source_kind': 'registration', 'chunk_id': -3001}]
    OFFICE = [{'source_kind': 'office', 'chunk_id': -1}]
    CARD = [{'source_kind': 'park', 'chunk_id': -4001}]
    HISTORY = [{'role': 'user', 'text': 'комиссия честный'}]

    def run_rows(self, *, directory_error=False, parks_error=False):
        cursor = MagicMock()
        seen = {}

        def directory_rows(*_args, **_kwargs):
            if directory_error:
                raise RuntimeError('справочник упал')
            return list(self.OFFICE)

        def park_rows(*args, **kwargs):
            seen.update(question=args[2], **kwargs)
            if parks_error:
                raise RuntimeError('парки упали')
            return list(self.CARD)

        with patch.object(routes_ai.wiki_registration, 'ai_rows', return_value=list(self.REGISTRATION)), \
             patch.object(routes_ai.wiki_directory, 'ai_rows', side_effect=directory_rows), \
             patch.object(routes_ai.wiki_park_cards, 'ai_rows', side_effect=park_rows), \
             patch.object(routes_ai.logging, 'exception'):
            found = routes_ai.directory_rows(cursor, {}, 'а номер?', 11, 'комиссия честный а номер?',
                                             history=self.HISTORY)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        return found, seen, statements

    def test_cards_go_last_and_know_the_conversation(self):
        found, seen, statements = self.run_rows()
        self.assertEqual([row['source_kind'] for row in found], ['registration', 'office', 'park'])
        self.assertEqual(seen['answered'], {'registration', 'office'})
        self.assertEqual((seen['question'], seen['search_query'], seen['history']),
                         ('а номер?', 'комиссия честный а номер?', self.HISTORY))
        self.assertLess(statements.index('SAVEPOINT wiki_ai_parks'),
                        statements.index('RELEASE SAVEPOINT wiki_ai_parks'))

    def test_failures_stay_inside_their_savepoints(self):
        found, _seen, statements = self.run_rows(parks_error=True)
        self.assertEqual([row['source_kind'] for row in found], ['registration', 'office'])
        self.assertLess(statements.index('SAVEPOINT wiki_ai_parks'),
                        statements.index('ROLLBACK TO SAVEPOINT wiki_ai_parks'))
        found, seen, statements = self.run_rows(directory_error=True)
        self.assertEqual([row['source_kind'] for row in found], ['registration', 'park'])
        self.assertEqual(seen['answered'], {'registration'})
        self.assertIn('ROLLBACK TO SAVEPOINT wiki_ai_directory', statements)

    def test_ask_passes_the_conversation(self):
        tree = ast.parse((ROOT / 'wiki' / 'routes_ai.py').read_text(encoding='utf-8'))
        ask = ast.unparse(next(node for node in ast.walk(tree)
                               if isinstance(node, ast.FunctionDef) and node.name == 'wiki_ai_ask'))
        self.assertIn('directory_rows(cursor, ctx, question, space_id, search_query, history=history)',
                      ask)


class FrontWiringTest(unittest.TestCase):
    """Переход с чипа на страницу парка и назад. Куда ведёт чип, решает чистая
    функция (tests/assistant_source_target.test.mjs: parkTarget); здесь — что
    экраны зовут её и что дверь доезжает до кнопки «назад»."""

    def read(self, *parts):
        return (ROOT.joinpath(*parts)).read_text(encoding='utf-8')

    def test_chip_opens_the_park_page_with_a_door(self):
        view = self.read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn("if (tabKey !== 'offices' && tabKey !== 'cities' && tabKey !== 'parks') return;",
                      view)
        self.assertIn('const page = parkTarget(target);', view)
        self.assertIn('if (page.park) setSearchTarget({ park: page.park, '
                      'from: door || returnDoor(tab, catalogMode) });', view)
        self.assertIn('}, [activeSpace, tab, catalogMode]);', view)

    def test_back_to_the_assistant_opens_the_same_conversation(self):
        view = self.read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn("const assistantDoor = (chatId) => ({ ...returnDoor('assistant'), "
                      "chatId: chatId || null });", view)
        self.assertIn('const from = assistantDoor(chatId);', view)
        self.assertIn('setAssistantChat({ chatId: exit.chatId, nonce: Date.now() });', view)
        assistant = self.read('src', 'components', 'wiki', 'WikiAssistant.jsx')
        self.assertIn('(slug, highlight, source) => onOpenArticle?.(slug, highlight, source, activeId)',
                      assistant)

    def test_library_shows_the_park_and_returns_through_the_door(self):
        library = self.read('src', 'components', 'wiki', 'WikiLibrary.jsx')
        self.assertIn('useState(() => searchTarget?.park || null)', library)
        self.assertIn('if (!searchTarget?.park) return;', library)
        self.assertIn('setOpenParkSlug(searchTarget.park);', library)
        self.assertIn('onBack={closePark}', library)
        self.assertIn('backLabel={door?.label}', library)
        self.assertIn('onOpenPark={(slug) => { setDoor(null); setOpenParkSlug(slug); }}', library)
        close = library.split('const closePark = useCallback(() => {', 1)[1].split('}, [', 1)[0]
        self.assertIn('returnTo(exit);', close)
        park = self.read('src', 'components', 'wiki', 'WikiPark.jsx')
        self.assertIn("backLabel = 'К статьям'", park)
        self.assertEqual(park.count('<ArrowLeft size={14} /> {backLabel}'), 2)


class NoRawSqlTest(unittest.TestCase):
    def test_park_cards_read_only_through_guarded_readers(self):
        """SQL парков и офисов — в parks.py/offices.py под стражей пространства
        (test_wiki_directory_space); здесь его быть не должно."""
        tree = ast.parse((ROOT / 'wiki' / 'park_cards.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotRegex(node.value.upper(), r'\bSELECT\b.*\bFROM\b')
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, 'execute')


if __name__ == '__main__':
    unittest.main()
