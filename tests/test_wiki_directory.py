# -*- coding: utf-8 -*-
"""Справочники вики в поиске и в помощнике: офисы и комиссии Яндекса.

Что стережётся (wiki/directory.py, решение 02.10.2026):
  * город узнаётся так, как его пишут операторы: склонения, казахские буквы,
    латиница, забытая раскладка, старые названия — и не узнаётся там, где это
    другое слово («акта» не Актау);
  * генерические слова ведут во вкладку, а не высыпают все офисы; «комиссия
    при выводе» и «фронт-офис» справочника не касаются;
  * комиссии склеиваются тем же правилом, что карточка города (cityRules.js):
    по коду тарифа, без скрытых, свои тарифы после яндексовских;
  * фрагменты помощника держат контракт строки ретривера, а все числа ответа
    (телефон, дом, процент) лежат в их тексте буквально;
  * граница — как у самих вкладок: пространство выдано не гостем, вкладка
    включена; поиск при сбое справочника не падает.

Без базы: данные синтетические, той же формы, что на проде (замер 02.10.2026:
45 офисов, 25 городов, 19 парков в «Таксопарках»).
"""

import ast
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from wiki import directory
from wiki.ai import answer as ai_answer
from wiki.ai import store as ai_store

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 2)            # четверг

WEEK = {code: {'from': '09:00', 'to': '19:00', 'break_from': '13:00', 'break_to': '14:00'}
        for code in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')}


def office(office_id, city, name, *, kind='park', address='ул. Абая 1', phone=None,
           no_office=False, parks=(), all_parks=False, schedule=WEEK, **extra):
    row = {'id': office_id, 'slug': 'o%s' % office_id, 'name': name, 'city': city,
           'address': None if no_office else address, 'address_note': None,
           'phone': phone, 'schedule': None if no_office else schedule, 'kind': kind,
           'partner_label': None, 'no_office': no_office, 'all_parks': all_parks,
           'status': 'active', 'day': None, 'closed_from': None, 'closed_until': None,
           'closed_note': None, 'updated_at': '2026-10-01T10:00:00',
           'parks': list(parks), 'telegram_usernames': ['manager_nick']}
    row.update(extra)
    return row


OFFICES = [
    office(1, 'Алматы', 'Офис для подключения тарифа «Бизнес»', address='ул. Байзакова 78А'),
    office(2, 'Алматы', 'Офис Алматы №1', address='Улица Жамбыла, 172В угол улицы Байзакова',
           phone='+7 700 123 45 67',
           parks=[{'park_id': 10, 'name': 'iTaxi', 'phones': [{'phone': '+7 700 765 43 21',
                                                               'note': 'WhatsApp'}],
                   'schedule': None, 'note': None}]),
    office(3, 'Алматы', 'Офис Алматы №2', address='7-й микрорайон, 5'),
    office(4, 'Алматы', 'Брендирование Алматы №1', kind='partner', address='ул. Витебская 44'),
    office(5, 'Алматы', 'Брендирование Алматы №2', kind='partner', address='пр. Райымбека 162'),
    office(6, 'Алматы', 'Офис для подключения тарифа «Wolt»', address='ул. Толе Би 4'),
    office(7, 'Астана', 'Офис Астана', address='Проспект Сарыарка, 31', phone='8 700 123 45 67'),
    office(8, 'Шымкент', 'Офис Шымкент', address='проспект Республики 17',
           address_note='- остановка Рахат\n- после пересечения улиц'),
    office(9, 'Караганда', 'Офис Караганда', address='улица Абдирова 4'),
    office(10, 'Петропавловск', 'Петропавловск онлайн', no_office=True),
    office(11, 'Усть-Каменогорск', 'Офис Усть-Каменогорск', address='ул. Назарбаева 61'),
    office(12, 'Костанай', 'Офис Костанай', address='улица Амангельды 25',
           closed_from='2026-09-28', closed_until='2026-10-05', closed_note='ремонт'),
]

PARKS = [{'id': 10, 'name': 'iTaxi'}, {'id': 11, 'name': 'iTaxi VIP'},
         {'id': 12, 'name': 'Jana такси'}, {'id': 13, 'name': 'Hokage (Wolt)'},
         {'id': 14, 'name': 'Бизнес Партнер'}, {'id': 15, 'name': 'Стабильный'}]


def city(city_id, name, tariffs, meta, *, extra=(), options=(), park=None,
         serving=None, driver=()):
    return {'id': city_id, 'name': name, 'tariffs': list(tariffs), 'tariff_meta': dict(meta),
            'extra_tariffs': list(extra), 'option_commissions': list(options),
            'park_commission': park, 'serving_office_id': serving,
            'driver_office_ids': list(driver), 'updated_at': None}


def t(code, name):
    return {'class': code, 'name': name}


CITIES = [
    city(1, 'Алматы', [t('econom', 'Эконом'), t('business', 'Комфорт'), t('comfortplus', 'Комфорт+'),
                        t('vip', 'Business'), t('intercity_preorder', 'Межгород')],
         {'econom': {'commission': 17.1}, 'business': {'commission': 19.2},
          'comfortplus': {'commission': 19.2}, 'vip': {'commission': 19.2}},
         options=[{'name': 'По делам / Домой / Мой район', 'commission': 9.3}]),
    city(2, 'Кокшетау', [t('econom', 'Эконом'), t('business', 'Комфорт'),
                         t('international_courier', 'Курьер')],
         {'econom': {'commission': 11.4}, 'business': {'commission': 12.4},
          'international_courier': {'commission': 11.8}}),
    city(3, 'Шымкент', [t('econom', 'Эконом'), t('comfortplus', 'Комфорт+'), t('cargo', 'Грузовой'),
                        t('cargo_intercity', 'Грузовой Межгород')],
         {'econom': {'commission': 11.9}, 'comfortplus': {'commission': 14.5},
          'cargo': {'commission': 11.6}, 'cargo_intercity': {'commission': 10.0}}),
    # Своего офиса нет — водителей направляют в Караганду (driver_office_ids).
    city(4, 'Темиртау', [t('econom', 'Эконом')], {'econom': {'commission': 11.4}}, driver=[9]),
    city(5, 'Павлодар', [t('econom', 'Эконом')], {'econom': {'commission': 11.4, 'hidden': True}},
         extra=[{'name': 'Курьер', 'commission': 12.0}]),
]

SPACE = {'id': 11, 'offices': True, 'cities': True}


def decide(query, **kwargs):
    analysis = directory.analyze(query)
    kwargs.setdefault('offices', OFFICES)
    kwargs.setdefault('cities', CITIES)
    kwargs.setdefault('parks', PARKS)
    return directory.plan(analysis, **kwargs)


def items(query, **kwargs):
    analysis = directory.analyze(query)
    decision = directory.plan(analysis, offices=OFFICES, cities=CITIES, parks=PARKS, **kwargs)
    return directory.search_items(decision, space_id=11, day=DAY, cities=CITIES,
                                  parks=directory.match_parks(analysis, PARKS))


NAMES = sorted({row['city'] for row in OFFICES} | {row['name'] for row in CITIES}
               | {'Семей', 'Актау', 'Уральск', 'Туркестан'})


def cities_in(query):
    return directory.find_cities(directory.analyze(query), NAMES)


class CityRecognitionTest(unittest.TestCase):
    def test_declension_and_kazakh_letters(self):
        cases = {
            'адрес офиса в астане': ['Астана'],
            'itaxi адрес в шымкенте': ['Шымкент'],
            'офис в костанае': ['Костанай'],
            'офис в семее': ['Семей'],
            'график работы офиса в караганде': ['Караганда'],
            'Қарағанды офис': ['Караганда'],
            'офис в усть-каменогорске': ['Усть-Каменогорск'],
            'из шымкента в туркестан': ['Шымкент', 'Туркестан'],
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(cities_in(query), expected)

    def test_old_names_latin_and_wrong_layout(self):
        self.assertEqual(cities_in('офис нур-султан'), ['Астана'])
        self.assertEqual(cities_in('офис в чимкенте'), ['Шымкент'])
        self.assertEqual(cities_in('алмата офис'), ['Алматы'])
        self.assertEqual(cities_in('almaty office'), ['Алматы'])
        self.assertEqual(cities_in('fkvfns'), ['Алматы'])          # «алматы» в чужой раскладке

    def test_short_names_and_other_words_stay_other_words(self):
        # «акта» — родительный «акт», а не Актау; «Орал» (Уральск) — целым словом.
        self.assertEqual(cities_in('подписание акта'), [])
        self.assertEqual(cities_in('водитель оралкин'), [])
        self.assertEqual(cities_in('офис в орале'), ['Уральск'])
        # Глагол «орал» — город по форме, но без «где» и «сколько» справочник молчит.
        decision = decide('оператор орал на водителя')
        self.assertEqual((decision['offices'], decision['cities']), ([], []))

    def test_alias_only_for_city_of_this_space(self):
        # В пространстве без Астаны «нур-султан» никого не находит.
        analysis = directory.analyze('офис нур-султан')
        self.assertEqual(directory.find_cities(analysis, ['Алматы']), [])


class IntentTest(unittest.TestCase):
    def test_office_and_commission_intents(self):
        self.assertTrue(directory.analyze('адреса офисов')['office_intent'])
        self.assertTrue(directory.analyze('где находится офис')['office_intent'])
        self.assertTrue(directory.analyze('коммисия яндекса')['commission_intent'])
        self.assertTrue(directory.analyze('камиссия')['commission_intent'])
        self.assertFalse(directory.analyze('тариф межгород')['commission_intent'])

    def test_weak_hints_need_a_city_or_an_office(self):
        # «По каким графикам работает фин отдел» — про отдел, а не про офис.
        self.assertFalse(directory.analyze('во сколько открывается')['office_intent'])
        self.assertTrue(directory.analyze('во сколько открывается')['office_hint'])
        self.assertEqual(decide('по каким графикам работает фин отдел')['offices'], [])
        self.assertFalse(decide('по каким графикам работает фин отдел')['offices_tab'])
        self.assertEqual([e['office']['id'] for e in decide('во сколько открывается в шымкенте')['offices']],
                         [8])
        # Процент — слабее комиссии: без города и тарифа это не комиссия Яндекса.
        self.assertIsNone(decide('проценты')['cities_tab'])
        self.assertTrue(decide('сколько процентов в кокшетау')['cities'])

    def test_address_is_weak_and_order_address_is_not_an_office(self):
        # «Адрес» в такси чаще адрес заказа: сильный признак — только офис.
        self.assertFalse(directory.analyze('адрес алматы')['office_intent'])
        self.assertTrue(directory.analyze('адрес алматы')['address_hint'])
        self.assertEqual([e['office']['id'] for e in decide('адрес алматы')['offices']], [2, 3, 1])
        # Один «адрес» — 23 поиска за 60 дней — ведёт во вкладку.
        self.assertTrue(decide('адрес')['offices_tab'])
        self.assertTrue(decide('адреса')['offices_tab'])
        for query in ('смена адреса', 'смена адреса алматы', 'адрес почты',
                      'неверный адрес подачи астана', 'локация алматы',
                      'двойное списание при смене адреса', 'где находится кнопка',
                      'автомойка адреса в алматы', 'клиент изменил адрес в заказе в алматы'):
            with self.subTest(query=query):
                decision = decide(query)
                self.assertEqual(decision['offices'], [])
                self.assertFalse(decision['offices_tab'])

    def test_hours_count_only_as_a_question(self):
        # «Работает» без «ли» — это и «не работает таксометр», и «как работает бонус».
        for query in ('не работает таксометр алматы', 'закрытие смены астана',
                      'как работает подключение', 'как открыть смену алматы',
                      'как работает бонус алматы', 'работает ли эконом в кокшетау'):
            with self.subTest(query=query):
                decision = decide(query)
                self.assertEqual((decision['offices'], decision['offices_tab']), ([], False))
        for query, office_id in (('работает ли сегодня костанай', 12),
                                 ('до скольки работает шымкент', 8),
                                 ('график работы в шымкенте', 8)):
            with self.subTest(query=query):
                self.assertEqual([e['office']['id'] for e in decide(query)['offices']], [office_id])

    def test_words_between_how_much_and_the_verb(self):
        self.assertTrue(directory.analyze('сколько яндекс забирает с заказа')['commission_intent'])
        self.assertTrue(directory.analyze('сколько с водителя снимают')['commission_intent'])
        # Между ними — кто и с кого, а не любое слово.
        self.assertFalse(directory.analyze('сколько времени забирает проверка')['commission_intent'])
        self.assertEqual([e['city']['name'] for e in decide('сколько яндекс забирает шымкент')['cities']],
                         ['Шымкент'])

    def test_no_door_into_an_empty_tab(self):
        self.assertFalse(decide('офис', offices=[])['offices_tab'])
        self.assertFalse(decide('адрес', offices=[])['offices_tab'])

    def test_promo_and_taxes_are_not_the_commission_table(self):
        for query in ('2 недели без комиссии', 'сколько процентов по налогам самозанятый'):
            with self.subTest(query=query):
                analysis = directory.analyze(query)
                self.assertFalse(analysis['commission_intent'] or analysis['percent_hint'])

    def test_named_park_without_city_is_park_commission(self):
        self.assertIsNone(decide('айтакси комиссия')['cities_tab'])
        self.assertIsNone(decide('комиссия таксопрака стабильный')['cities_tab'])
        self.assertTrue(decide('комиссия яндекса в кокшетау itaxi')['cities'])

    def test_department_office_is_not_an_address(self):
        # «фронт-офис» — отдел и должность, а не здание.
        for query in ('фронт офис', 'менеджеры фронт-офиса', 'бэк-офис'):
            with self.subTest(query=query):
                self.assertFalse(directory.analyze(query)['office_intent'])

    def test_payment_commission_is_not_yandex_commission(self):
        for query in ('комиссия при пополнении', 'какая комиссия при выводе средств',
                      'комиссия за перевод на карту'):
            with self.subTest(query=query):
                self.assertFalse(directory.analyze(query)['commission_intent'])

    def test_tariff_words(self):
        self.assertEqual(directory.analyze('комиссия комфорт+ шымкент')['tariffs'], ['комфорт+'])
        self.assertEqual(directory.analyze('комфорт плюс')['tariffs'], ['комфорт+'])
        self.assertEqual(directory.analyze('комфорт')['tariffs'], ['комфорт'])
        # «Бизнес» у Яндекса пишется латиницей: Business.
        self.assertEqual(directory.analyze('комиссия бизнес')['tariffs'], ['business'])
        self.assertEqual(directory.analyze('грузовой межгород')['tariffs'], ['грузовой межгород'])
        self.assertEqual(directory.analyze('доставка')['tariffs'], ['доставка'])


class ParkTest(unittest.TestCase):
    def names(self, query):
        return [park['name'] for park in directory.match_parks(directory.analyze(query), PARKS)]

    def test_brand_spelled_the_way_it_is_said(self):
        self.assertEqual(self.names('Адрес Ай такси в Туркестан'), ['iTaxi'])
        self.assertEqual(self.names('айтакси офис'), ['iTaxi'])
        self.assertEqual(self.names('itaxi адрес'), ['iTaxi'])
        self.assertEqual(self.names('jana taxi адрес астана'), ['Jana такси'])
        self.assertEqual(self.names('wolt адреса'), ['Hokage (Wolt)'])

    def test_longer_name_wins_and_all_words_needed(self):
        self.assertEqual(self.names('itaxi vip офис'), ['iTaxi VIP'])
        # «бизнес» — тариф, а не «Бизнес Партнер»: нужны все слова названия.
        self.assertEqual(self.names('бизнес офис'), [])


class PlanTest(unittest.TestCase):
    def test_generic_words_lead_to_the_tab(self):
        decision = decide('офис')
        self.assertEqual(decision['offices'], [])
        self.assertTrue(decision['offices_tab'])
        self.assertEqual(decide('комиссия')['cities_tab'], {'tariffs': []})

    def test_city_shows_main_office_first_and_rest_behind_a_door(self):
        decision = decide('офис алматы')
        names = [entry['office']['name'] for entry in decision['offices']]
        self.assertEqual(names[:2], ['Офис Алматы №1', 'Офис Алматы №2'])
        self.assertEqual(len(names), directory.SEARCH_OFFICES)
        self.assertEqual(decision['offices_more'], {'city': 'Алматы', 'count': 6})

    def test_office_name_and_address_words_narrow(self):
        branding = [entry['office']['id'] for entry in decide('брендирование алматы')['offices']]
        self.assertEqual(branding, [4, 5])
        wolt = [entry['office']['id'] for entry in decide('wolt адреса офисов')['offices']]
        self.assertEqual(wolt, [6])
        street = [entry['office']['id'] for entry in decide('офис на жамбыла')['offices']]
        self.assertEqual(street, [2])

    def test_phone_digits_find_the_office_whatever_prefix(self):
        found = {entry['office']['id'] for entry in decide('офис 87001234567')['offices']}
        self.assertEqual(found, {2, 7})        # «+7 700 …» и «8 700 …» — один номер

    def test_city_without_office_points_where_drivers_go(self):
        decision = decide('офис темиртау')
        self.assertEqual([(e['office']['id'], e['for_city']) for e in decision['offices']],
                         [(9, 'Темиртау')])

    def test_no_office_record_is_an_answer(self):
        decision = decide('где находится офис в петропавловске')
        self.assertTrue(decision['offices'][0]['office']['no_office'])

    def test_card_offices_come_first_like_city_offices(self):
        # Запись «офиса нет» и одни партнёры — не свой офис: карточка города
        # называет, куда направлять водителя (cityOffices в cityRules.js).
        petropavl = city(6, 'Петропавловск', [t('econom', 'Эконом')], {}, driver=[7])
        decision = decide('офис петропавловск', cities=CITIES + [petropavl])
        self.assertEqual([(e['office']['id'], e['for_city']) for e in decision['offices']],
                         [(7, 'Петропавловск'), (10, None)])
        branding = office(20, 'Темиртау', 'Брендирование Темиртау', kind='partner')
        decision = decide('офис темиртау', offices=OFFICES + [branding])
        self.assertEqual([(e['office']['id'], e['for_city']) for e in decision['offices']],
                         [(9, 'Темиртау'), (20, None)])
        zone = city(7, 'Петропавловск', [t('econom', 'Эконом')], {}, serving=9)
        decision = decide('офис петропавловск', cities=CITIES + [zone])
        self.assertEqual(decision['offices'][0]['office']['id'], 9)

    def test_one_office_for_two_named_cities_is_one_row(self):
        decision = decide('офис караганда темиртау')
        self.assertEqual([(e['office']['id'], e['for_city']) for e in decision['offices']],
                         [(9, 'Темиртау')])

    def test_commissions_of_a_city_narrowed_to_the_tariff(self):
        entry = decide('комиссия эконом алматы')['cities'][0]
        self.assertTrue(entry['narrowed'])
        self.assertEqual([tariff['name'] for tariff in entry['tariffs']], ['Эконом'])
        # «Business» — код vip, «Комфорт» — код business: склейка по коду.
        business = decide('комиссия бизнес алматы')['cities'][0]
        self.assertEqual([tariff['name'] for tariff in business['tariffs']], ['Business'])

    def test_missing_tariff_falls_back_to_all_of_the_city(self):
        entry = decide('комиссия межгород кокшетау')['cities'][0]
        self.assertFalse(entry['narrowed'])
        self.assertEqual(len(entry['tariffs']), 3)

    def test_nothing_where_directory_has_nothing_to_say(self):
        for query in ('бизнес', 'межгород', 'электро', 'комиссия при выводе средств',
                      'комиссия таксопарка стабильный', 'фронт офис',
                      'как водителю оформить возврат в алматы'):
            with self.subTest(query=query):
                decision = decide(query)
                self.assertEqual(decision['offices'], [])
                self.assertEqual(decision['cities'], [])
                self.assertFalse(decision['offices_tab'])
                self.assertIsNone(decision['cities_tab'])

    def test_short_city_query_shows_both(self):
        decision = decide('Алматы')
        self.assertTrue(decision['offices'])
        self.assertTrue(decision['cities'])

    def test_city_with_unexplained_words_is_not_a_directory_query(self):
        # «Бонусы Туркестан» — про бонусы; «межгород алматы» — тариф, объяснено.
        decision = decide('бонусы алматы')
        self.assertEqual((decision['offices'], decision['cities']), ([], []))
        self.assertTrue(decide('межгород алматы')['cities'])
        self.assertTrue(decide('в шымкенте')['offices'])            # склонение — не лишнее слово

    def test_brand_in_other_alphabet_finds_the_office(self):
        self.assertEqual([e['office']['id'] for e in decide('офис алматы вольт')['offices']], [6])

    def test_switched_off_tab_says_nothing(self):
        decision = decide('офис алматы', features={'offices': False, 'cities': True})
        self.assertEqual(decision['offices'], [])
        self.assertFalse(decision['offices_tab'])

    def test_no_cities_tab_without_commissions(self):
        # У Тез городов нет — дверь «по городам» вела бы в пустую вкладку.
        self.assertIsNone(decide('комиссия', cities=[])['cities_tab'])


class CommissionTwinTest(unittest.TestCase):
    """Те же случаи, что tests/wiki_city_rules.test.mjs для cityTariffs."""

    def test_join_by_code_hidden_out_extra_after(self):
        pavlodar = next(row for row in CITIES if row['name'] == 'Павлодар')
        self.assertEqual([tariff['name'] for tariff in directory.visible_tariffs(pavlodar)],
                         ['Курьер'])
        almaty = next(row for row in CITIES if row['name'] == 'Алматы')
        names = [tariff['name'] for tariff in directory.visible_tariffs(almaty)]
        self.assertEqual(names, ['Эконом', 'Комфорт', 'Комфорт+', 'Business', 'Межгород'])

    def test_format_like_the_card(self):
        self.assertEqual(directory.format_percent(4.5), '4,5%')
        self.assertEqual(directory.format_percent(12), '12%')
        self.assertEqual(directory.format_percent(17.1), '17,1%')
        self.assertEqual(directory.commission_range([9, 14, 11.5]), '9–14%')
        self.assertEqual(directory.commission_range([12, 12]), '12%')
        self.assertEqual(directory.commission_range([]), '')

    def test_option_commissions_are_not_tariffs(self):
        entry = items('комиссия алматы')[0]
        self.assertEqual(entry['kind'], 'city')
        self.assertNotIn('По делам / Домой / Мой район', [row['name'] for row in entry['tariffs']])
        self.assertEqual(entry['options'][0]['commission'], '9,3%')
        self.assertEqual(entry['range'], '17,1–19,2%')


class OfficeDayTest(unittest.TestCase):
    def test_week_folds_like_the_card(self):
        schedule = dict(WEEK)
        schedule['sat'] = {'from': '10:00', 'to': '15:00'}
        schedule['sun'] = None
        self.assertEqual(directory.week_lines(schedule),
                         'Пн–Пт 09:00–19:00, обед 13:00–14:00; Сб 10:00–15:00; Вс выходной')

    def test_closure_reports_reopening_day(self):
        kostanay = next(row for row in OFFICES if row['id'] == 12)
        status = directory.office_status(kostanay, DAY)
        self.assertEqual((status['state'], status['reopens'], status['note']),
                         ('closed', '2026-10-05', 'ремонт'))

    def test_manual_mark_wins(self):
        marked = office(99, 'Алматы', 'Офис', day={'state': 'closed', 'source': 'manual',
                                                   'note': 'свет отключили'})
        status = directory.office_status(marked, DAY)
        self.assertEqual((status['state'], status['note']), ('closed', 'свет отключили'))


class LiveStatusTest(unittest.TestCase):
    """Те же случаи, что officeStatus/untilText в officeSchedule.js."""

    def at(self, schedule, hour, minute=0, day=DAY):
        now = datetime(day.year, day.month, day.day, hour, minute)
        status = directory.live_status(schedule, now)
        return status['state'], directory.until_text(status)

    def test_open_lunch_and_closed(self):
        self.assertEqual(self.at(WEEK, 10), ('open', 'до 19:00'))
        self.assertEqual(self.at(WEEK, 13, 20), ('break', 'до 14:00'))
        self.assertEqual(self.at(WEEK, 8), ('closed', 'до 09:00'))
        self.assertEqual(self.at(WEEK, 20, 30), ('closed', 'до завтра 09:00'))

    def test_weekend_and_overnight(self):
        weekdays = dict(WEEK, sat=None, sun=None)
        friday = date(2026, 10, 2)
        self.assertEqual(self.at(weekdays, 20, day=friday), ('closed', 'до понедельника 09:00'))
        night = {code: {'from': '22:00', 'to': '02:00'} for code in WEEK}
        self.assertEqual(self.at(night, 1, 30), ('open', 'до 02:00'))       # вчерашняя смена
        self.assertEqual(self.at(None, 10), ('none', None))


class SearchItemsTest(unittest.TestCase):
    def test_office_row_carries_what_the_badge_needs_and_no_nicks(self):
        row = items('офис астана')[0]
        self.assertEqual(row['kind'], 'office')
        for key in ('schedule', 'day', 'closed_from', 'closed_until', 'updated_at', 'space_id'):
            self.assertIn(key, row)
        self.assertNotIn('telegram_usernames', row)

    def test_named_park_phone_goes_first(self):
        row = next(item for item in items('itaxi офис алматы') if item.get('id') == 2)
        self.assertEqual((row['phone'], row['phone_park'], row['phone_note']),
                         ('+7 700 765 43 21', 'iTaxi', 'WhatsApp'))

    def test_other_park_phone_is_never_shown_unlabelled(self):
        # Своего номера у офиса нет, есть номер iTaxi: без названного iTaxi
        # строка номера не показывает — он читался бы номером офиса.
        shared = office(30, 'Атырау', 'Офис Атырау', phone=None,
                        parks=[{'park_id': 10, 'name': 'iTaxi',
                                'phones': [{'phone': '+7 700 765 43 21', 'note': 'WhatsApp'}]}])
        analysis = directory.analyze('офис атырау')
        decision = directory.plan(analysis, offices=[shared], cities=[], parks=PARKS)
        row = directory.search_items(decision, space_id=11, day=DAY, cities=[], parks=[])[0]
        self.assertEqual((row['phone'], row['phone_park']), (None, None))
        own = directory.search_items(decision, space_id=11, day=DAY, cities=[],
                                     parks=[{'id': 10, 'name': 'iTaxi'}])[0]
        self.assertEqual((own['phone'], own['phone_park']), ('+7 700 765 43 21', 'iTaxi'))

    def test_more_offices_become_a_door(self):
        kinds = [row['kind'] for row in items('офис алматы')]
        self.assertEqual(kinds, ['office', 'office', 'office', 'offices_tab'])

    def test_tariff_summary_without_city(self):
        row = items('комиссия эконом')[-1]
        self.assertEqual(row['kind'], 'cities_tab')
        self.assertEqual(row['tariffs'][0]['name'], 'Эконом')
        self.assertEqual(row['tariffs'][0]['range'], '11,4–17,1%')


class AiRowsTest(unittest.TestCase):
    REQUIRED = ('chunk_id', 'article_id', 'title', 'slug', 'heading_path',
                'text', 'requires_ack', 'found_by')

    def rows(self, query):
        return directory.build_ai_rows(directory.analyze(query), space=SPACE, offices=OFFICES,
                                       cities=CITIES, parks=PARKS, day=DAY)

    def test_row_contract_of_the_retriever(self):
        rows = self.rows('адрес офиса в шымкенте')
        self.assertTrue(rows)
        for row in rows:
            for key in self.REQUIRED:
                self.assertIn(key, row)
            self.assertIsNone(row['article_id'])
            self.assertLess(row['chunk_id'], 0)
            self.assertEqual(row['found_by'], [directory.DIRECTORY_BRANCH])
            self.assertTrue(row['directory_hit'])
            self.assertEqual((row['tab'], row['space_id']), ('offices', 11))

    def test_every_number_is_in_the_text(self):
        text = self.rows('itaxi адрес алматы №1 жамбыла')[0]['text']
        for number in ('+7 700 123 45 67', '172В', '09:00–19:00', '+7 700 765 43 21'):
            self.assertIn(number, text)
        self.assertNotIn('manager_nick', text)          # ники — не во внешний ИИ

    def test_landmarks_in_one_line_without_bullets(self):
        text = self.rows('офис шымкент')[0]['text']
        self.assertIn('Ориентиры «Офис Шымкент»: остановка Рахат; после пересечения улиц.', text)

    def test_closure_is_written_as_reopening_day_not_until(self):
        # «до <даты>» помечается как истёкший срок (wiki/ai/currency.py).
        text = self.rows('офис костанай')[0]['text']
        self.assertIn('закрыт, откроется 05.10.2026 (ремонт)', text)
        self.assertNotIn(' до 05.10', text)

    def test_city_commissions_and_all_cities(self):
        row = self.rows('комиссия яндекса в кокшетау')[0]
        self.assertEqual((row['tab'], row['ref_id']), ('cities', 2))
        self.assertIn('Кокшетау, тариф Курьер: комиссия Яндекса 11,8%.', row['text'])
        overview = self.rows('комиссия яндекса по городам эконом')[0]
        self.assertIn('Алматы: Эконом 17,1%.', overview['text'])

    def test_silent_without_intent(self):
        self.assertEqual(self.rows('как оформить возврат в алматы'), [])

    def test_all_offices_only_when_offices_are_asked(self):
        # «Смена адреса» в вопросе про двойное списание — не повод для сорока адресов.
        self.assertEqual(self.rows('списалась сумма два раза при смене адреса'), [])
        overview = self.rows('какие офисы сегодня закрыты')
        self.assertEqual(overview[0]['source_kind'], 'offices')
        self.assertIn('Офис Костанай»: улица Амангельды 25 — закрыт, откроется 05.10.2026.',
                      overview[0]['text'])

    def test_commission_table_only_for_number_questions(self):
        self.assertTrue(self.rows('коммисия яндекса в общем за сервис за заказ'))
        self.assertTrue(self.rows('комиссия'))
        self.assertEqual(self.rows('получил сообщение что две недели будет без комиссии'), [])

    def test_weak_hints_reach_the_assistant(self):
        # Поиск на те же слова отвечает — помощник молчать не должен.
        cases = {
            'Сколько процентов берёт Яндекс в Кокшетау?': 'Кокшетау › Комиссия Яндекса',
            'Какой процент у Яндекса в Шымкенте?': 'Шымкент › Комиссия Яндекса',
            'Сколько Яндекс забирает с заказа в Шымкенте?': 'Шымкент › Комиссия Яндекса',
            'Работает ли сегодня Шымкент?': 'Шымкент › Офис Шымкент',
            'Во сколько открывается Костанай?': 'Костанай › Офис Костанай',
            'Подскажи адрес в городе Астане?': 'Астана › Офис Астана',
        }
        for question, heading in cases.items():
            with self.subTest(question=question):
                self.assertEqual([row['heading_path'] for row in self.rows(question)], [heading])

    def test_order_address_and_bare_works_stay_out_of_the_prompt(self):
        for question in ('Клиент изменил адрес в заказе в Алматы, как пересчитать стоимость?',
                         'Пассажир указал неверный адрес подачи в Астане',
                         'Водитель в Алматы не может найти адрес клиента',
                         'почему у водителя в алматы не работает приложение',
                         'как водителю открыть смену в шымкенте'):
            with self.subTest(question=question):
                self.assertEqual(self.rows(question), [])


class AiQuestionTest(unittest.TestCase):
    """ai_rows разбирает вопрос с прошлыми репликами — ровно то, что получит /ask."""

    def rows(self, question, search_query=None, now=None):
        analysis = directory.analyze_question(question, search_query)
        return directory.build_ai_rows(analysis, space=SPACE, offices=OFFICES, cities=CITIES,
                                       parks=PARKS, day=DAY, now=now)

    def test_question_is_read_once(self):
        # enrich_query отдаёт сам вопрос, если истории нет: читать его дважды
        # значило потерять таблицу на «комиссия сервиса» и номер «123 45 67».
        for question in ('комиссия сервиса', 'комиссия водителя'):
            with self.subTest(question=question):
                self.assertEqual([row['heading_path'] for row in self.rows(question, question)],
                                 ['Комиссия Яндекса по городам'])
        found = [row['ref_id'] for row in self.rows('чей номер 123 45 67 офис',
                                                    'чей номер 123 45 67 офис')]
        self.assertEqual(sorted(found), [2, 7])

    def test_follow_up_keeps_the_topic_and_takes_the_new_city(self):
        rows = self.rows('а в шымкенте?', 'адрес офиса в алматы а в шымкенте?')
        self.assertEqual([row['heading_path'] for row in rows], ['Шымкент › Офис Шымкент'])
        phone = self.rows('а телефон 123 45 67 чей?',
                          'адрес офиса в алматы а телефон 123 45 67 чей?')
        self.assertEqual([row['ref_id'] for row in phone], [2])

    def test_status_is_live_when_the_clock_is_known(self):
        evening = datetime(2026, 10, 2, 20, 30)
        text = self.rows('Офис в Астане сейчас открыт?', now=evening)[0]['text']
        self.assertIn('сейчас 20:30 — закрыт до завтра 09:00', text)
        self.assertNotIn(': открыт', text)
        lunch = self.rows('Офис в Астане сейчас открыт?', now=datetime(2026, 10, 2, 13, 20))
        self.assertIn('сейчас 13:20 — на обеде до 14:00', lunch[0]['text'])
        overview = self.rows('какие офисы сейчас открыты', now=evening)[0]['text']
        self.assertIn('статус на 02.10.2026, сейчас 20:30', overview)
        self.assertIn('Офис Астана»: Проспект Сарыарка, 31 — сегодня 09:00–19:00, обед '
                      '13:00–14:00; сейчас закрыт до завтра 09:00.', overview)
        # Закрытие на срок сильнее графика — живой расчёт его не перебивает.
        self.assertIn('закрыт, откроется 05.10.2026', overview)


class AnswerPipelineTest(unittest.TestCase):
    def rows(self):
        return directory.build_ai_rows(directory.analyze('адрес офиса в шымкенте'), space=SPACE,
                                       offices=OFFICES, cities=CITIES, parks=PARKS, day=DAY)

    def test_directory_rows_pass_the_floor_and_skip_clarify(self):
        rows = self.rows()
        self.assertEqual(ai_answer.usable_chunks(rows), rows)
        article = {'chunk_id': 5, 'article_id': 77, 'text': 'x', 'similarity': 0.7,
                   'found_by': [1]}
        self.assertFalse(ai_answer.should_clarify('офис шымкент', rows + [article])[0])

    def test_prompt_labels_directory_and_rule_says_it_wins(self):
        prompt = ai_answer.build_user_prompt('офис шымкент', self.rows())
        self.assertIn('[1] Справочник «Офисы», запись «Шымкент › Офис Шымкент»', prompt)
        self.assertIn('Справочник «Офисы»', ai_answer.SYSTEM_PROMPT)
        self.assertIn('верен справочник', ai_answer.SYSTEM_PROMPT)

    def test_source_points_to_the_tab(self):
        rows = self.rows()
        sources = ai_answer.build_sources([1], rows, 'Адрес: проспект Республики 17.')
        self.assertEqual(sources[0]['source_kind'], 'office')
        self.assertEqual((sources[0]['tab'], sources[0]['ref_id'], sources[0]['space_id']),
                         ('offices', 8, 11))
        self.assertIsNone(sources[0]['article_id'])


class HistoryAccessTest(unittest.TestCase):
    def test_directory_source_shown_only_while_tab_is_open(self):
        # Строки — по именам колонок запроса: их порядок знает только store.
        message = dict.fromkeys(ai_store._MESSAGE_FIELDS)
        message.update(id=1, seq=1, role='assistant', kind='answer', text='текст')
        source = dict.fromkeys(ai_store.SOURCE_ROW)
        source.update(message_id=1, ord=0, title='Офисы', slug='',
                      heading_path='Шымкент › Офис Шымкент', quote='проспект Республики 17',
                      quote_ok=True, requires_ack=False, attributed=False, stale=False,
                      stale_note='', stale_kind='', source_kind='office', tab='offices',
                      ref_id=8, ref_city='Шымкент', space_id=11)
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [tuple(message[name] for name in ai_store._MESSAGE_FIELDS)],
            [tuple(source[name] for name in ai_store.SOURCE_ROW)],
        ] * 2
        shown = ai_store.chat_messages(cursor, 5, visible_article_ids={1},
                                       directory_access={11: {'offices': True, 'cities': True}})
        source = shown[0]['sources'][0]
        self.assertTrue(source['available'])
        self.assertEqual((source['tab'], source['ref_id']), ('offices', 8))
        hidden = ai_store.chat_messages(cursor, 5, visible_article_ids={1},
                                        directory_access={11: {'offices': False, 'cities': True}})
        closed = hidden[0]['sources'][0]
        self.assertFalse(closed['available'])
        self.assertEqual((closed['title'], closed['quote'], closed['tab']),
                         ('Справочник недоступен', '', None))


class AccessTest(unittest.TestCase):
    SPACES = [{'id': 11, 'features': {}}, {'id': 12, 'features': {'offices': False,
                                                                  'cities': False}}]

    def space(self, allowed, requested):
        with patch.object(directory.queries, 'spaces_for_user', return_value=allowed) as spaces, \
             patch.object(directory.structure, 'list_spaces', return_value=self.SPACES):
            result = directory.directory_space(MagicMock(), {}, requested)
        # Гостю справочник не положен: спрашиваем без гостевой выдачи.
        self.assertFalse(spaces.call_args.kwargs['include_guest'])
        return result

    def test_space_must_be_granted_and_tab_on(self):
        self.assertEqual(self.space([11], 11), {'id': 11, 'offices': True, 'cities': True})
        self.assertEqual(self.space([11], None)['id'], 11)        # единственное — его
        self.assertIsNone(self.space([11, 12], None))            # из нескольких — никакое
        self.assertIsNone(self.space([11], 12))                  # чужое или гостевое
        self.assertIsNone(self.space([12], 12))                  # вкладки выключены
        self.assertIsNone(self.space([], None))


from tests.test_wiki_search_filters import RouteHarness  # noqa: E402


class SearchRouteDirectoryTest(RouteHarness):
    """GET /api/wiki/search → directory: отдельным полем, учтён в журнале,
    молчит при фильтрах статей и не роняет поиск своим сбоем."""

    def found(self, cursor, ctx, query, *, requested_space):
        self.asked = {'query': query, 'space': requested_space}
        return [{'kind': 'office', 'id': 1, 'space_id': requested_space}]

    def test_directory_is_a_separate_field_and_counted(self):
        self.patch(directory, 'search', self.found)
        response = self.client.get('/api/wiki/search?q=офис+алматы&space_id=11')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['items'], [])
        self.assertEqual(data['directory'], [{'kind': 'office', 'id': 1, 'space_id': 11}])
        self.assertEqual(self.asked, {'query': 'офис алматы', 'space': 11})
        # «Справочник ответил» — не дыра в базе знаний для отчёта «искали и не нашли».
        self.assertEqual(self.logged['results_count'], 1)

    def test_article_filters_silence_directory(self):
        called = []
        self.patch(directory, 'search', lambda *a, **k: called.append(1) or [])
        for query in ('article_type=regulation', 'author_id=3', 'match=title', 'section_id=4'):
            with self.subTest(query=query):
                response = self.client.get('/api/wiki/search?q=офис+алматы&' + query)
                self.assertEqual(response.get_json()['directory'], [])
        self.assertEqual(called, [])

    def test_directory_failure_keeps_search_alive(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError('справочник упал')

        self.patch(directory, 'search', broken)
        response = self.client.get('/api/wiki/search?q=офис+алматы')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['directory'], [])
        statements = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertIn('ROLLBACK TO SAVEPOINT wiki_search_directory', statements)

    def test_short_query_answers_empty_directory(self):
        response = self.client.get('/api/wiki/search?q=о')
        self.assertEqual(response.get_json(), {'items': [], 'query': 'о', 'directory': []})


class NoRawSqlTest(unittest.TestCase):
    def test_directory_reads_only_through_guarded_readers(self):
        """SQL справочников — в offices.py/cities.py/parks.py под стражей
        пространства (test_wiki_directory_space, test_wiki_cities); здесь его
        быть не должно, иначе эта стража до него не дотянется."""
        tree = ast.parse((ROOT / 'wiki' / 'directory.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotRegex(node.value.upper(), r'\bSELECT\b.*\bFROM\b')
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, 'execute')

    def test_directory_cities_sql_is_scoped(self):
        from wiki import cities as wiki_cities
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        wiki_cities.directory_cities(cursor, space_id=11)
        sql = ' '.join(cursor.execute.call_args.args[0].split())
        self.assertIn("c.space_id = %(space)s AND c.status = 'active'", sql)
        self.assertIn('oo.space_id = c.space_id', sql)
        self.assertEqual(cursor.execute.call_args.args[1], {'space': 11})


if __name__ == '__main__':
    unittest.main()
