# -*- coding: utf-8 -*-
"""Проверка полей карточки посылки — правила ТЗ #240, а не «валидация вообще».

Здесь закрыты именно те решения, которые легко потерять при правке формы, и
которые ломаются молча — то есть карточка сохраняется, но не та:

  * офис города, где он один, подставляется НА СЕРВЕРЕ, а не только в форме
    (иначе правило держится лишь до первого запроса мимо интерфейса);
  * смена города обнуляет офис — иначе PATCH отвечал бы «офис не относится к
    городу» человеку, который офис и не трогал;
  * офис из ЧУЖОГО города не принимается;
  * дата приёма не бывает в будущем;
  * снимок водителя снимается сервером, но введённое человеком ФИО главнее;
  * отказ CRM не мешает записать посылку — она уже лежит в офисе.

Сети и базы здесь нет: `offices_in_city` и `fetch_driver` подменяются.
"""

import unittest
from datetime import date, timedelta

from parcels import drivers, queries, routes

# Справочник как на проде: в Алматы офисов несколько, в Таразе один.
OFFICES = {
    'алматы': [
        {'id': 47, 'city': 'Алматы', 'name': 'Офис Алматы №1',
         'address': 'Улица Жамбыла, 172В', 'address_note': None, 'phone': None},
        {'id': 48, 'city': 'Алматы', 'name': 'Офис Алматы №2',
         'address': '7-й микрорайон, 5', 'address_note': None, 'phone': None},
    ],
    'тараз': [
        {'id': 62, 'city': 'Тараз', 'name': 'Офис Тараз',
         'address': 'улица Казыбек би, д 138.', 'address_note': None, 'phone': None},
    ],
}

DRIVER_ID = '9b139a9dbe8d49bfbf8521b619c89198'
OTHER_DRIVER_ID = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'

CRM_ANSWER = {
    'account_id': DRIVER_ID,
    # `yandex_id` у парка — как в живом ответе: из него собирается ссылка на
    # аккаунт водителя во Флите.
    'park': {'name': 'iTaxi Туркестан', 'id': 74,
             'yandex_id': 'cb1562e507f34940bef13b8d19a9221b'},
    'driver': {'last_name': 'Abdikarim', 'first_name': 'Nurkanat',
               'middle_name': 'Izbasaruly', 'phone': '+77719736925',
               'driver_license': 'BB222764'},
    'car': {'model': 'LADA (ВАЗ) Priora', 'license_plate': '252АЕN13',
            'callsign': 'Kuatova'},
}


def valid_payload(**overrides):
    payload = {
        'received_on': date.today().isoformat(),
        'city': 'Тараз',
        'driver_link': DRIVER_ID,
        'kind': 'parcel',
        'description': 'коробка с одеждой',
    }
    payload.update(overrides)
    return payload


def existing_parcel(**overrides):
    parcel = {
        'id': 1,
        'received_on': '2026-08-01',
        'city': 'Тараз',
        'office_id': 62,
        'office_name': 'Офис Тараз',
        'office_address': 'улица Казыбек би, д 138.',
        'driver_account_id': DRIVER_ID,
        'driver_name': 'Abdikarim Nurkanat Izbasaruly',
        'driver_synced_at': '2026-08-01T10:00:00',
        'kind': 'parcel',
        'description': 'коробка с одеждой',
        'sender': None,
        'recipient': None,
        'order_number': None,
        'comment': None,
        'status': 'in_office',
    }
    parcel.update(overrides)
    return parcel


class _Base(unittest.TestCase):
    def setUp(self):
        self.crm_calls = []

        def fake_offices(_cursor, city):
            return list(OFFICES.get(str(city or '').strip().lower(), []))

        def fake_fetch(account_id, **_kwargs):
            self.crm_calls.append(account_id)
            return dict(CRM_ANSWER, account_id=account_id)

        self._swap(queries, 'offices_in_city', fake_offices)
        self._swap(drivers, 'fetch_driver', fake_fetch)

    def _swap(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def validate(self, payload, existing=None):
        return routes._validate(cursor=None, data=payload, existing=existing)


class PlaceTests(_Base):
    def test_single_office_city_fills_the_office_on_the_server(self):
        """Правило должно держаться и когда запрос пришёл мимо интерфейса."""
        fields, error = self.validate(valid_payload(city='Тараз'))
        self.assertIsNone(error)
        self.assertEqual(fields['office_id'], 62)
        self.assertEqual(fields['office_name'], 'Офис Тараз')
        self.assertEqual(fields['office_address'], 'улица Казыбек би, д 138.')

    def test_multi_office_city_demands_a_choice(self):
        fields, error = self.validate(valid_payload(city='Алматы'))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'OFFICE_REQUIRED')

    def test_multi_office_city_accepts_the_chosen_one(self):
        fields, error = self.validate(valid_payload(city='Алматы', office_id=48))
        self.assertIsNone(error)
        self.assertEqual(fields['office_id'], 48)
        self.assertEqual(fields['office_name'], 'Офис Алматы №2')

    def test_office_from_another_city_is_refused(self):
        fields, error = self.validate(valid_payload(city='Тараз', office_id=47))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'OFFICE_MISMATCH')

    def test_city_without_offices_points_at_the_wiki(self):
        fields, error = self.validate(valid_payload(city='Жанаозен'))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'CITY_WITHOUT_OFFICES')
        self.assertIn('Вики', error[0])

    def test_city_is_required(self):
        for value in ('', '   ', None):
            fields, error = self.validate(valid_payload(city=value))
            self.assertIsNone(fields, repr(value))
            self.assertEqual(error[1], 'CITY_REQUIRED')

    def test_city_name_is_taken_from_the_directory_not_from_the_request(self):
        """Иначе в реестре появились бы «тараз», «Тараз » и «ТАРАЗ» как три города."""
        fields, error = self.validate(valid_payload(city='  тараз '))
        self.assertIsNone(error)
        self.assertEqual(fields['city'], 'Тараз')

    def test_changing_the_city_resets_the_office(self):
        """Старый офис остался в другом городе — тащить его в проверку нельзя."""
        fields, error = self.validate({'city': 'Алматы'}, existing=existing_parcel())
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'OFFICE_REQUIRED',
                         'должен спросить офис Алматы, а не жаловаться на офис Тараза')

    def test_changing_the_city_to_a_single_office_one_just_works(self):
        parcel = existing_parcel(city='Алматы', office_id=47, office_name='Офис Алматы №1')
        fields, error = self.validate({'city': 'Тараз'}, existing=parcel)
        self.assertIsNone(error)
        self.assertEqual(fields['office_id'], 62)

    def test_editing_something_else_keeps_the_place_untouched(self):
        fields, error = self.validate({'description': 'два пакета'},
                                      existing=existing_parcel())
        self.assertIsNone(error)
        self.assertNotIn('city', fields)
        self.assertNotIn('office_id', fields)


class DateTests(_Base):
    def test_date_is_required(self):
        fields, error = self.validate(valid_payload(received_on=''))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'RECEIVED_ON_REQUIRED')

    def test_future_date_is_refused(self):
        tomorrow = (queries.today_almaty() + timedelta(days=1)).isoformat()
        fields, error = self.validate(valid_payload(received_on=tomorrow))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'RECEIVED_ON_FUTURE')

    def test_today_is_allowed(self):
        today = queries.today_almaty().isoformat()
        fields, error = self.validate(valid_payload(received_on=today))
        self.assertIsNone(error)
        self.assertEqual(fields['received_on'].isoformat(), today)

    def test_a_year_old_date_is_refused_as_a_typo_in_the_year(self):
        old = (queries.today_almaty() - timedelta(days=500)).isoformat()
        fields, error = self.validate(valid_payload(received_on=old))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'RECEIVED_ON_TOO_OLD')

    def test_nonsense_date_is_refused_not_rolled_over(self):
        """«13-й месяц» должен отвечать отказом, а не превращаться в январь."""
        for value in ('2026-13-40', 'вчера', '25.08.2026'):
            fields, error = self.validate(valid_payload(received_on=value))
            self.assertIsNone(fields, repr(value))
            self.assertEqual(error[1], 'RECEIVED_ON_REQUIRED')


class DriverTests(_Base):
    def test_link_is_accepted_and_the_driver_is_taken_from_it(self):
        link = ('https://fleet.yandex.kz/contractors'
                '?park_id=cb1562e507f34940bef13b8d19a9221b&contractor_id=%s' % DRIVER_ID)
        fields, error = self.validate(valid_payload(driver_link=link))
        self.assertIsNone(error)
        self.assertEqual(fields['driver_account_id'], DRIVER_ID)
        self.assertEqual(self.crm_calls, [DRIVER_ID])

    def test_snapshot_comes_from_crm_not_from_the_request(self):
        """Клиент мог прислать любое ФИО — в карточке должен быть тот, чей ID."""
        fields, error = self.validate(valid_payload(
            driver_phone='+70000000000', driver_park='Чужой парк'))
        self.assertIsNone(error)
        self.assertEqual(fields['driver_phone'], '+77719736925')
        self.assertEqual(fields['driver_park'], 'iTaxi Туркестан')
        self.assertEqual(fields['driver_car'], 'LADA (ВАЗ) Priora · 252АЕN13')
        self.assertIsNotNone(fields['driver_info'])
        self.assertIsNotNone(fields['driver_synced_at'])

    def test_manual_name_wins_over_the_snapshot(self):
        """ФИО правят как раз тогда, когда в CRM оно латиницей или с опечаткой."""
        fields, error = self.validate(valid_payload(driver_name='Абдикарим Нурканат'))
        self.assertIsNone(error)
        self.assertEqual(fields['driver_name'], 'Абдикарим Нурканат')
        self.assertEqual(fields['driver_phone'], '+77719736925')

    def test_driver_id_is_required(self):
        for value in ('', '123456', 'не ссылка'):
            fields, error = self.validate(valid_payload(driver_link=value))
            self.assertIsNone(fields, repr(value))
            self.assertEqual(error[1], 'DRIVER_ID_REQUIRED')

    def test_crm_failure_does_not_block_saving(self):
        """Посылка уже лежит в офисе — не сохранить её из-за чужого сервиса нельзя."""
        def broken(_account_id, **_kwargs):
            raise drivers.DriverLookupError('CRM молчит', code='crm_unavailable')

        self._swap(drivers, 'fetch_driver', broken)
        fields, error = self.validate(valid_payload(driver_name='Абдикарим Н.'))
        self.assertIsNone(error)
        self.assertEqual(fields['driver_account_id'], DRIVER_ID)
        self.assertEqual(fields['driver_name'], 'Абдикарим Н.')
        self.assertIsNone(fields['driver_phone'])
        self.assertIsNone(fields['driver_synced_at'])

    def test_editing_the_description_does_not_touch_crm(self):
        """Правка описания не должна ни ждать чужой сервис, ни зависеть от него."""
        fields, error = self.validate({'description': 'два пакета'},
                                      existing=existing_parcel())
        self.assertIsNone(error)
        self.assertEqual(self.crm_calls, [])
        self.assertNotIn('driver_account_id', fields)

    def test_replacing_the_driver_refreshes_the_snapshot(self):
        fields, error = self.validate({'driver_link': OTHER_DRIVER_ID},
                                      existing=existing_parcel())
        self.assertIsNone(error)
        self.assertEqual(fields['driver_account_id'], OTHER_DRIVER_ID)
        self.assertEqual(self.crm_calls, [OTHER_DRIVER_ID])

    def test_same_driver_with_a_snapshot_is_not_refetched(self):
        fields, error = self.validate({'driver_link': DRIVER_ID},
                                      existing=existing_parcel())
        self.assertIsNone(error)
        self.assertEqual(self.crm_calls, [])

    def test_card_without_a_snapshot_gets_one_on_the_next_save(self):
        """Карточка, заведённая при недоступной CRM, дозаполняется сама."""
        fields, error = self.validate({'driver_link': DRIVER_ID},
                                      existing=existing_parcel(driver_synced_at=None))
        self.assertIsNone(error)
        self.assertEqual(self.crm_calls, [DRIVER_ID])


class ContentTests(_Base):
    def test_kind_is_required_and_checked(self):
        for value in ('', 'посылка', 'box'):
            fields, error = self.validate(valid_payload(kind=value))
            self.assertIsNone(fields, repr(value))
            self.assertEqual(error[1], 'KIND_REQUIRED')

    def test_all_three_kinds_of_the_spec_are_accepted(self):
        for value in ('parcel', 'document', 'other'):
            fields, error = self.validate(valid_payload(kind=value))
            self.assertIsNone(error, value)
            self.assertEqual(fields['kind'], value)

    def test_description_is_required_even_for_other(self):
        """«Другое» без расшифровки — это запись, по которой ничего не найти."""
        fields, error = self.validate(valid_payload(kind='other', description='   '))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'DESCRIPTION_REQUIRED')

    def test_optional_fields_stay_optional(self):
        fields, error = self.validate(valid_payload())
        self.assertIsNone(error)
        for name in ('sender', 'recipient', 'order_number', 'comment'):
            self.assertNotIn(name, fields)

    def test_optional_fields_are_stored_when_filled(self):
        fields, error = self.validate(valid_payload(
            sender='Петров П.П.', recipient='Иван Иванов',
            order_number='250825-77', comment='звонить после 18:00'))
        self.assertIsNone(error)
        self.assertEqual(fields['sender'], 'Петров П.П.')
        self.assertEqual(fields['recipient'], 'Иван Иванов')
        self.assertEqual(fields['order_number'], '250825-77')
        self.assertEqual(fields['comment'], 'звонить после 18:00')

    def test_emptied_optional_field_becomes_null_not_an_empty_string(self):
        fields, error = self.validate({'sender': '   '}, existing=existing_parcel())
        self.assertIsNone(error)
        self.assertIsNone(fields['sender'])

    def test_new_card_starts_in_the_office(self):
        fields, error = self.validate(valid_payload())
        self.assertIsNone(error)
        self.assertEqual(fields['status'], 'in_office')

    def test_status_is_not_editable_through_the_card_form(self):
        """У статуса свой роут: «кто изменил статус» — отдельный вопрос ТЗ."""
        fields, error = self.validate({'status': 'given_to_sender'},
                                      existing=existing_parcel())
        self.assertIsNone(error)
        self.assertNotIn('status', fields)

    def test_unknown_status_on_creation_is_refused(self):
        fields, error = self.validate(valid_payload(status='отдали'))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'STATUS_UNKNOWN')


if __name__ == '__main__':
    unittest.main()


class OrderLinkTests(_Base):
    """Заказ прикрепляется ССЫЛКОЙ (решение владельца 25.08.2026).

    Проверка схемы здесь не формальность: значение уходит в карточку как
    `<a href>`, и `javascript:` в нём — это выполнение кода у того, кто по
    ссылке щёлкнет. Правило продублировано во фронте (`safeLink`), но ЗАПИСЬ
    закрывает только сервер: в базу ссылка попадает через API.
    """

    def test_link_is_optional(self):
        fields, error = self.validate(valid_payload())
        self.assertIsNone(error)
        self.assertNotIn('order_url', fields)

    def test_link_is_stored_as_is(self):
        url = 'https://fleet.yandex.kz/orders/401220d7ef4bebb78b02303530848695?park_id=cb15'
        fields, error = self.validate(valid_payload(order_url=url))
        self.assertIsNone(error)
        self.assertEqual(fields['order_url'], url)

    def test_scheme_is_added_when_pasted_without_it(self):
        """«fleet.yandex.kz/orders/…» из адресной строки — обычная копипаста."""
        fields, error = self.validate(valid_payload(order_url='fleet.yandex.kz/orders/1'))
        self.assertIsNone(error)
        self.assertEqual(fields['order_url'], 'https://fleet.yandex.kz/orders/1')

    def test_dangerous_schemes_are_refused(self):
        for value in ('javascript:alert(1)', 'JavaScript:alert(1)',
                      'javascript://x.com/%0aalert(1)', 'data:text/html,x',
                      'vbscript:msgbox(1)', 'mailto:a@b.kz'):
            fields, error = self.validate(valid_payload(order_url=value))
            self.assertIsNone(fields, value)
            self.assertEqual(error[1], 'ORDER_URL_INVALID', value)

    def test_value_without_a_host_is_refused(self):
        for value in ('не ссылка', 'https://', 'просто текст с пробелами'):
            fields, error = self.validate(valid_payload(order_url=value))
            self.assertIsNone(fields, value)
            self.assertEqual(error[1], 'ORDER_URL_INVALID', value)

    def test_absurdly_long_link_is_refused(self):
        fields, error = self.validate(valid_payload(
            order_url='https://fleet.yandex.kz/orders/' + 'a' * 2100))
        self.assertIsNone(fields)
        self.assertEqual(error[1], 'ORDER_URL_INVALID')

    def test_emptying_the_link_stores_null_not_an_empty_string(self):
        fields, error = self.validate({'order_url': '   '}, existing=existing_parcel())
        self.assertIsNone(error)
        self.assertIsNone(fields['order_url'])

    def test_park_id_is_snapshotted_for_the_account_link(self):
        """Без id парка ссылку на аккаунт водителя не собрать."""
        fields, error = self.validate(valid_payload())
        self.assertIsNone(error)
        self.assertEqual(fields['driver_park_id'], 'cb1562e507f34940bef13b8d19a9221b')
