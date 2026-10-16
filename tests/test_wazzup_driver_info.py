"""Данные водителя из CRM для карточки «Чатов ОП»: подбор таксопарка и города
amoCRM и раскладка карточки CRM для экрана (wazzup/driver_info.py).

Названия парков — те формы, что CRM и amoCRM отдавали 16.10.2026 (сверка на
216 водителях из сделок верификаторов); люди и номера — выдуманные."""
import pytest

from wazzup import driver_info as info

AMO = {
    'parks': [{'id': id_, 'name': name} for id_, name in (
        (1, 'Jana Taxi'), (2, 'Адал'), (3, 'Честный'), (4, 'Департамент'), (5, 'iTaxi'), (6, 'iTaxi  (доставка)'),
        (7, 'iTaxi 2'), (8, 'iTaxi 3'), (9, 'Salam Taxi'), (10, 'Каспи'), (11, '2 Донгелек'), (12, 'Такси 24'),
        (13, 'Ноль такси'), (14, 'Аманат'), (15, 'Усть-Каменогорск'), (16, 'Tenge Taxi'), (17, 'Qazaq'))],
    'cities': [{'id': id_, 'name': name} for id_, name in (
        (101, 'Алматы'), (102, 'Астана'), (103, 'Нур-Султан'), (104, 'Актау'), (105, 'Шымкент'), (106, 'Кокшетау'),
        (107, 'Тараз'), (108, 'Усть-Каменогорск'), (109, 'Петропавловск'), (110, 'Петропавлск'), (111, 'Павлодар'))],
}


def park_id(n):
    return '%032x' % n


DICTIONARY = {
    'parks': [{'yandexId': park_id(n), 'name': name, 'brand': brand, 'city': city} for n, name, brand, city in (
        (1, 'Jana', 'Jana', 'Алматы'), (2, 'Честный', 'Честный', 'Шымкент'), (3, 'Честный', 'Честный', 'Актау'),
        (4, 'ДепТакси', 'ДепТакси', 'Кокшетау'), (5, 'iTaxi (доставка)', 'iTaxi (доставка)', 'Алматы'),
        (6, 'Salam Taxi (доставка)', 'Salam Taxi', 'Алматы'), (7, 'Каспий', 'Каспий', 'Актау'),
        (8, '2донгелек', 'Eki Dongelek', 'Алматы'), (9, 'Такси 24', 'Такси 24', 'Алматы'),
        (10, 'Ноль такси', 'Ноль такси', 'Алматы'), (11, 'Amanat', 'Amanat', 'Павлодар'),
        (12, 'Усть-Каменогорск', 'Усть-Каменогорск', 'Усть-Каменогорск'), (13, 'Tenge', 'Tenge', 'Астана'),
        (14, 'Жана межгород', 'Jana', 'Алматы'), (15, 'Самозанятый Партнер', 'Самозанятый Партнер', 'Алматы'),
        (16, 'iTaxi 3', 'iTaxi', 'Алматы'), (17, 'iTaxi', 'iTaxi', 'Петропавловск'),
        (18, 'Честный', 'Честный', 'Алматы'), (19, 'Qazaq', 'Qazaq', 'Астана'))],
    'categories': [{'value': 'econom', 'label': 'Эконом'}, {'value': 'courier', 'label': 'Курьер'},
                   {'value': 'express', 'label': 'Экспресс'}, {'value': 'intercity', 'label': 'Межгород'}],
}


def crm(name, n):
    return {'park': {'name': name, 'yandex_id': park_id(n), 'id': 7}}


@pytest.mark.parametrize('crm_name, n, park, city', [
    ('Jana Taxi Алматы', 1, 'Jana Taxi', 'Алматы'),
    ('Jana', 1, 'Jana Taxi', 'Алматы'),
    ('Адал Шымкент', 2, 'Адал', 'Шымкент'),
    ('Адал (Честный) Актау', 3, 'Адал', 'Актау'),
    ('Честный Алматы', 18, 'Честный', 'Алматы'),
    ('Департамент Такси Кокшетау', 4, 'Департамент', 'Кокшетау'),
    ('iTaxi (Доставка) Алматы', 5, 'iTaxi  (доставка)', 'Алматы'),
    ('Salam Taxi (доставка) Алматы', 6, 'Salam Taxi', 'Алматы'),
    ('Каспий Актау', 7, 'Каспи', 'Актау'),
    ('EKI DONGELEK Алматы', 8, '2 Донгелек', 'Алматы'),
    ('Такси 24  Алматы', 9, 'Такси 24', 'Алматы'),
    ('Ноль Такси Алматы', 10, 'Ноль такси', 'Алматы'),
    ('Аманат Павлодар', 11, 'Аманат', 'Павлодар'),
    ('Усть-Каменогорск', 12, 'Усть-Каменогорск', 'Усть-Каменогорск'),
    ('Tenge Taxi Астана', 13, 'Tenge Taxi', 'Астана'),
    ('Жана межгород Алматы', 14, 'Jana Taxi', 'Алматы'),
    ('iTaxi 3 Алматы', 16, 'iTaxi 3', 'Алматы'),
    ('iTaxi Петропавловск', 17, 'iTaxi', 'Петропавловск'),
    ('QAZAQ  Астана', 19, 'Qazaq', 'Астана'),
])
def test_the_crm_park_of_the_driver_gives_the_amo_park_and_city(crm_name, n, park, city):
    hint = info.suggest(crm(crm_name, n), DICTIONARY, AMO)
    assert (hint['park'] or {}).get('name') == park
    assert (hint['city'] or {}).get('name') == city
    assert isinstance(hint['park']['id'], int) and isinstance(hint['city']['id'], int)


def test_the_city_comes_from_the_park_and_old_or_misspelt_amo_names_are_not_chosen():
    assert info.suggest(crm('Tenge Taxi Астана', 13), DICTIONARY, AMO)['city'] == {'id': 102, 'name': 'Астана'}
    assert info.suggest(crm('iTaxi Петропавловск', 17), DICTIONARY, AMO)['city'] == {
        'id': 109, 'name': 'Петропавловск'}


def test_a_park_amo_does_not_list_is_left_to_the_person_but_the_city_is_still_given():
    hint = info.suggest(crm('Самозанятый Партнер Алматы', 15), DICTIONARY, AMO)
    assert hint['park'] is None and hint['city'] == {'id': 101, 'name': 'Алматы'}
    assert hint['crmPark'] == 'Самозанятый Партнер Алматы' and hint['crmCity'] == 'Алматы'


def test_without_the_dictionary_the_city_is_read_from_the_tail_of_the_crm_name():
    data = {'park': {'name': 'Jana Taxi Тараз', 'yandex_id': park_id(99)}}
    for dictionary in (DICTIONARY, None, {}):
        hint = info.suggest(data, dictionary, AMO)
        assert hint['park'] == {'id': 1, 'name': 'Jana Taxi'} and hint['city'] == {'id': 107, 'name': 'Тараз'}


def test_nothing_known_gives_nothing_and_never_raises():
    for data in (None, {}, {'park': None}, {'park': {'name': '', 'yandex_id': None}}):
        assert info.suggest(data, DICTIONARY, AMO) == {'park': None, 'city': None, 'crmPark': None,
                                                       'crmCity': None}
    assert info.suggest(crm('Jana Taxi Алматы', 1), DICTIONARY, None)['park'] is None


def test_the_name_key_drops_case_yo_the_taxi_word_and_signs_but_not_inside_words():
    assert info.name_key('  Ноль  Такси ') == info.name_key('ноль такси') == 'ноль'
    assert info.name_key('iTaxi  (доставка)') == info.name_key('iTaxi (Доставка)') == 'itaxiдоставка'
    assert info.name_key('ДепТакси') == 'дептакси'
    assert info.name_key('Каспий') == info.name_key('Каспи') == 'каспи'
    assert info.name_key(None) == ''


DRIVER_DATA = {
    'account_id': 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
    'park': {'yandex_id': park_id(1), 'id': 98, 'name': 'Jana'},
    'driver': {'first_name': 'Тест', 'last_name': 'Тестов', 'middle_name': 'Тестович', 'full_name': 'Тестов Тест',
               'phone': '+77000000000', 'iin': None, 'driver_license': 'AA0000000', 'email': None},
    'employment': {'work_status': 'working', 'employment_type': 'park_employee',
                   'work_rule': {'id': 'x', 'name': 'Штатный'}, 'is_blocked': False, 'is_archive': False,
                   'hire_date': None, 'fire_date': None, 'created_date': '2026-10-16 14:33:30'},
    'balance': {'current': 12500.5, 'limit': -50, 'frozen': 0, 'blocked': None, 'last_transaction_date': None},
    'car': {'id': 'c', 'model': 'LADA (ВАЗ) Priora', 'license_plate': '000AAA01', 'year': 2013, 'color': 'Белый',
            'callsign': '42', 'tariffs': ['courier', 'econom', 'premium']},
    'dispatcher': {'current_status': None, 'rating': None, 'photo_control': {'status': None}},
    'orders': {'today': 1, 'week': 1234, 'month': 0, 'total': 1234, 'first_order_at': '2026-10-16 15:00:00',
               'last_order_at': None,
               'latest_orders': [{'booked_at': '2026-10-16 15:00:00', 'category': 'econom', 'price': 3510,
                                  'mileage': 11306.28, 'payment_method': 'cash'}],
               'last_7_days': {'count': 1, 'items': [{}]}},
}


def section(view, title):
    return {row['label']: row['value'] for row in next(s for s in view['sections'] if s['title'] == title)['rows']}


def test_the_crm_card_is_laid_out_in_russian_and_empty_values_are_not_shown():
    view = info.present(DRIVER_DATA, DICTIONARY)
    assert view['name'] == 'Тестов Тест Тестович'   # отчество — только в частях
    assert view['status'] == {'label': 'Работает', 'tone': 'ok'}
    assert view['car'] == 'LADA (ВАЗ) Priora, 2013, белый' and view['ordersTotal'] == 1234
    assert view['city'] == 'Алматы' and view['park'] == 'Jana'
    assert view['fleetUrl'] == ('https://fleet.yandex.kz/contractors/a1b2c3d4e5f60718293a4b5c6d7e8f90/details'
                                '?park_id=' + park_id(1))
    assert section(view, 'Водитель') == {'ФИО': 'Тестов Тест Тестович', 'Телефон': '+77000000000',
                                         'Водительское удостоверение': 'AA0000000'}
    work = section(view, 'Работа')
    assert work['Занятость'] == 'Сотрудник парка' and work['Зарегистрирован'] == '16.10.2026 14:33'
    assert 'Уволен' not in work and 'В архиве' not in work
    assert section(view, 'Машина')['Тарифы'] == 'Курьер, Эконом, premium'
    orders = next(s for s in view['sections'] if s['title'] == 'Заказы')
    assert orders['stats'] == [{'label': 'Сегодня', 'value': '1'}, {'label': '7 дней', 'value': '1 234'},
                               {'label': '30 дней', 'value': '0'}, {'label': 'Всего', 'value': '1 234'}]
    assert section(view, 'Заказы') == {'Первый заказ': '16.10.2026 15:00'}
    assert section(view, 'Последние заказы') == {
        '16.10.2026 15:00': 'Эконом · 3 510 ₸ · 11,3 км · наличные'}
    assert section(view, 'Баланс') == {'Баланс': '12 500,5 ₸', 'Лимит': '−50 ₸'}
    assert 'Диспетчерская' not in [s['title'] for s in view['sections']]


def test_rows_say_what_they_are_so_the_screen_can_style_them():
    view = info.present(DRIVER_DATA, DICTIONARY)
    rows = {row['key']: row for s in view['sections'] for row in s['rows']}
    assert rows['status'] == {'key': 'status', 'label': 'Статус', 'value': 'Работает', 'tone': 'ok'}
    assert rows['plate']['mono'] is True and rows['license']['mono'] is True and 'mono' not in rows['car']
    assert rows['tariffs']['chips'] == ['Курьер', 'Эконом', 'premium']
    assert {'name', 'phone', 'park', 'parkCity', 'registered', 'balance', 'limit', 'order'} <= set(rows)
    no_tariffs = info.present(dict(DRIVER_DATA, car={'model': 'X'}), None)
    assert 'tariffs' not in {row['key'] for s in no_tariffs['sections'] for row in s['rows']}


def test_blocked_fired_and_unknown_statuses_and_unavailable_orders():
    data = dict(DRIVER_DATA, employment=dict(DRIVER_DATA['employment'], is_blocked=True), orders=None)
    view = info.present(data, None)
    assert view['status'] == {'label': 'Заблокирован', 'tone': 'bad'} and view['ordersTotal'] is None
    assert section(view, 'Заказы') == {'Заказы': 'база заказов CRM сейчас недоступна'}
    assert section(view, 'Машина')['Тарифы'] == 'courier, econom, premium'   # без справочника — как есть
    fired = info.present(dict(DRIVER_DATA, employment={'work_status': 'fired', 'fire_date': '2026-10-01'}), None)
    assert fired['status'] == {'label': 'Уволен', 'tone': 'bad'} and section(fired, 'Работа')['Уволен'] == '01.10.2026'
    odd = info.present(dict(DRIVER_DATA, employment={'work_status': 'on_hold'}), None)
    assert odd['status'] == {'label': 'on_hold', 'tone': 'muted'}
    assert info.present({}, None) == {'name': None, 'phone': None, 'status': None, 'park': None, 'city': None,
                                      'car': None, 'ordersTotal': None, 'fleetUrl': None, 'sections': []}


def test_numbers_money_and_dates():
    assert info._money(-50) == '−50 ₸' and info._money(0) == '0 ₸' and info._money(None) is None
    assert info._number(1234567) == '1 234 567' and info._number(4.95) == '4,95'
    assert info._number(True) is None and info._number('x') is None
    assert info._when('2026-03-14') == '14.03.2026' and info._when('2026-03-14 09:20:11') == '14.03.2026 09:20'
    assert info._when('вчера') == 'вчера' and info._when('') is None
