# -*- coding: utf-8 -*-
"""Данные водителя из CRM yataxi: разбор ссылки и разбор ответа.

Две вещи здесь ломаются молча, поэтому обе закрыты тестами.

1. **Ссылка.** В живой ссылке Флита ДВА 32-значных значения, и первым идёт
   `park_id`. «Первое найденное» записало бы в карточку посылки чужого человека
   — а такую ошибку в реестре никто не заметит, потому что ФИО в ней будет
   настоящее, просто не то. Набор случаев здесь тот же, что у клиентского
   близнеца `parcelMeta.extractAccountId` (tests/parcel_meta.test.mjs).

2. **Ответ.** На пустой `account_id` CRM отдаёт HTML-страницу с кодом 200 — тот
   же капкан, что у конкурса регистраций и обзвона фронт-офиса. Разбор поэтому
   строгий: незнакомый формат = ошибка, а не «водитель без полей».

Живой ответ CRM снят 25.08.2026 и лежит здесь целиком — по нему проверяется
раскладка по колонкам карточки.
"""

import unittest

import requests

from parcels import drivers

# Живые ссылки и id (владелец, 25.08.2026). Водитель в обеих ссылках один.
DRIVER_ID = '9b139a9dbe8d49bfbf8521b619c89198'
PARK_ID = 'cb1562e507f34940bef13b8d19a9221b'
LINK_WITH_PARAMS = (
    'https://fleet.yandex.kz/contractors'
    '?park_id=%s&contractor_id=%s&candidate_id=b4df0290-2759-47e5-9920-c4494a4e4f05'
    % (PARK_ID, DRIVER_ID)
)
LINK_WITH_PATH = (
    'https://fleet.yandex.kz/contractors/%s/details?park_id=%s' % (DRIVER_ID, PARK_ID)
)

# Ответ живого API, снятый 25.08.2026.
LIVE_RESPONSE = {
    'data': {
        'account_id': DRIVER_ID,
        'park': {'yandex_id': PARK_ID, 'id': 74, 'name': 'iTaxi Туркестан'},
        'driver': {
            'first_name': 'Nurkanat',
            'last_name': 'Abdikarim',
            'middle_name': 'Izbasaruly',
            'full_name': 'Abdikarim Nurkanat',
            'phone': '+77719736925',
            'driver_license': 'BB222764',
            'email': None,
        },
        'employment': {'work_status': 'working', 'is_blocked': False,
                       'hire_date': '2022-03-23', 'fire_date': None},
        'balance': {'current': 0, 'limit': -60},
        'car': {
            'id': 'cd44e21214aa4a35b677ec9160a2f752',
            'model': 'LADA (ВАЗ) Priora',
            'license_plate': '252АЕN13',
            'year': 2013,
            'color': 'Черный',
            'callsign': 'Kuatova',
            'tariffs': ['courier', 'econom', 'express'],
        },
        'orders': {'today': 0, 'week': 0, 'month': 0, 'total': 0},
    }
}


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('не JSON')
        return self._payload


class _FakeSession:
    """Подменяет requests: сеть в тестах не трогаем."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        if self.error:
            raise self.error
        return self.response


CONFIG = {'url': 'https://example.invalid/driver-info', 'token': 'тестовый-токен'}


class LinkParsingTests(unittest.TestCase):
    def test_params_link_gives_the_driver_not_the_park(self):
        self.assertEqual(drivers.extract_account_id(LINK_WITH_PARAMS), DRIVER_ID)
        self.assertNotEqual(drivers.extract_account_id(LINK_WITH_PARAMS), PARK_ID)

    def test_path_link_gives_the_driver_though_park_id_is_in_the_query(self):
        self.assertEqual(drivers.extract_account_id(LINK_WITH_PATH), DRIVER_ID)

    def test_bare_id_is_taken_and_lowercased(self):
        self.assertEqual(drivers.extract_account_id(DRIVER_ID), DRIVER_ID)
        self.assertEqual(drivers.extract_account_id(DRIVER_ID.upper()), DRIVER_ID)
        self.assertEqual(drivers.extract_account_id('  %s ' % DRIVER_ID), DRIVER_ID)

    def test_link_without_scheme_is_parsed(self):
        self.assertEqual(
            drivers.extract_account_id('fleet.yandex.kz/contractors/%s/details' % DRIVER_ID),
            DRIVER_ID,
        )

    def test_old_fleet_and_yataxi_admin_links(self):
        self.assertEqual(
            drivers.extract_account_id('https://fleet.yandex.ru/drivers/%s/card' % DRIVER_ID),
            DRIVER_ID,
        )
        self.assertEqual(
            drivers.extract_account_id(
                'https://backend.yataxi.kz/admin/driver-accounts/%s' % DRIVER_ID),
            DRIVER_ID,
        )

    def test_a_link_without_a_driver_refuses_instead_of_guessing(self):
        self.assertIsNone(drivers.extract_account_id(
            'https://fleet.yandex.kz/parks?park_id=%s' % PARK_ID))
        self.assertIsNone(drivers.extract_account_id('https://fleet.yandex.kz/contractors'))

    def test_garbage_does_not_raise(self):
        for value in ('', None, '   ', 'не ссылка вовсе', '123456', 'http://['):
            self.assertIsNone(drivers.extract_account_id(value), repr(value))


class FetchTests(unittest.TestCase):
    def test_bad_id_is_refused_before_the_network(self):
        session = _FakeSession()
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.fetch_driver('123456', config=CONFIG, session=session)
        self.assertEqual(caught.exception.code, 'bad_account_id')
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(session.calls, [], 'опечатка не должна дойти до CRM')

    def test_token_goes_in_the_header_and_id_in_the_body(self):
        session = _FakeSession(_FakeResponse(200, LIVE_RESPONSE))
        drivers.fetch_driver(DRIVER_ID, config=CONFIG, session=session)
        call = session.calls[0]
        self.assertEqual(call['headers']['X-Integration-Token'], CONFIG['token'])
        self.assertEqual(call['json'], {'account_id': DRIVER_ID})

    def test_missing_token_says_so_instead_of_calling(self):
        session = _FakeSession()
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.fetch_driver(DRIVER_ID, config={'url': CONFIG['url'], 'token': ''},
                                 session=session)
        self.assertEqual(caught.exception.code, 'not_configured')
        self.assertEqual(session.calls, [])

    def test_unknown_driver_is_404_and_says_so(self):
        session = _FakeSession(_FakeResponse(404, {'error': 'not_found'}))
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.fetch_driver(DRIVER_ID, config=CONFIG, session=session)
        self.assertEqual(caught.exception.code, 'driver_not_found')
        self.assertEqual(caught.exception.status, 404)

    def test_bad_token_is_reported_as_ours_not_as_the_drivers_fault(self):
        session = _FakeSession(_FakeResponse(401, {'error': 'unauthorized'}))
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.fetch_driver(DRIVER_ID, config=CONFIG, session=session)
        self.assertEqual(caught.exception.code, 'crm_unauthorized')

    def test_html_with_code_200_is_an_error_not_an_empty_driver(self):
        """Главная ловушка этой CRM: вёрстка вместо ошибки, код 200."""
        session = _FakeSession(_FakeResponse(200, None, text='<!doctype html><html>…'))
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.fetch_driver(DRIVER_ID, config=CONFIG, session=session)
        self.assertEqual(caught.exception.code, 'crm_bad_payload')

    def test_json_without_data_is_also_an_error(self):
        for payload in ({}, {'data': None}, {'data': {}}, {'data': 'строка'}):
            session = _FakeSession(_FakeResponse(200, payload))
            with self.assertRaises(drivers.DriverLookupError) as caught:
                drivers.fetch_driver(DRIVER_ID, config=CONFIG, session=session)
            self.assertEqual(caught.exception.code, 'crm_bad_payload', repr(payload))

    def test_network_failure_reads_as_try_again(self):
        session = _FakeSession(error=requests.ConnectionError('нет сети'))
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.fetch_driver(DRIVER_ID, config=CONFIG, session=session)
        self.assertEqual(caught.exception.code, 'crm_unavailable')


class SummarizeTests(unittest.TestCase):
    def test_live_response_lays_out_into_card_columns(self):
        summary = drivers.summarize(LIVE_RESPONSE['data'])
        self.assertEqual(summary['account_id'], DRIVER_ID)
        self.assertEqual(summary['phone'], '+77719736925')
        self.assertEqual(summary['license'], 'BB222764')
        self.assertEqual(summary['park'], 'iTaxi Туркестан')
        self.assertEqual(summary['callsign'], 'Kuatova')
        self.assertEqual(summary['car'], 'LADA (ВАЗ) Priora · 252АЕN13')

    def test_full_name_is_assembled_from_parts_not_taken_ready(self):
        """У живого водителя готовое `full_name` КОРОЧЕ: отчества в нём нет.

        А посылку ищут и по отчеству, поэтому собираем из частей.
        """
        summary = drivers.summarize(LIVE_RESPONSE['data'])
        self.assertEqual(summary['name'], 'Abdikarim Nurkanat Izbasaruly')
        self.assertNotEqual(summary['name'], LIVE_RESPONSE['data']['driver']['full_name'])

    def test_ready_full_name_is_the_fallback(self):
        data = {'driver': {'full_name': 'Иванов Иван'}}
        self.assertEqual(drivers.summarize(data)['name'], 'Иванов Иван')

    def test_whole_answer_is_kept_for_later_questions(self):
        summary = drivers.summarize(LIVE_RESPONSE['data'])
        self.assertEqual(summary['info'], LIVE_RESPONSE['data'])
        # Блоки, которые сегодня не нужны, но приезжают, — не теряем.
        self.assertIn('employment', summary['info'])
        self.assertIn('orders', summary['info'])

    def test_empty_and_broken_payloads_do_not_raise(self):
        for data in ({}, None, 'строка', {'driver': None, 'car': 'нет'}):
            summary = drivers.summarize(data)
            self.assertIsNone(summary['name'])
            self.assertIsNone(summary['phone'])

    def test_long_values_are_trimmed_here_not_on_insert(self):
        """Иначе длинное значение из CRM роняет сохранение карточки целиком."""
        data = {'driver': {'last_name': 'Ф' * 500, 'phone': '7' * 90},
                'park': {'name': 'П' * 400}}
        summary = drivers.summarize(data)
        self.assertLessEqual(len(summary['name']), 200)
        self.assertLessEqual(len(summary['phone']), 32)
        self.assertLessEqual(len(summary['park']), 160)

    def test_car_without_a_plate_or_model_does_not_become_a_stray_separator(self):
        self.assertEqual(drivers.summarize({'car': {'model': 'Priora'}})['car'], 'Priora')
        self.assertEqual(drivers.summarize({'car': {'license_plate': '001'}})['car'], '001')
        self.assertIsNone(drivers.summarize({'car': {}})['car'])


class LookupTests(unittest.TestCase):
    def test_lookup_goes_from_a_link_straight_to_a_laid_out_card(self):
        session = _FakeSession(_FakeResponse(200, LIVE_RESPONSE))
        summary = drivers.lookup(LINK_WITH_PARAMS, config=CONFIG, session=session)
        self.assertEqual(summary['account_id'], DRIVER_ID)
        self.assertEqual(session.calls[0]['json'], {'account_id': DRIVER_ID})

    def test_unparsable_link_says_what_to_paste(self):
        with self.assertRaises(drivers.DriverLookupError) as caught:
            drivers.lookup('какая-то строка', config=CONFIG, session=_FakeSession())
        self.assertEqual(caught.exception.code, 'bad_account_id')
        self.assertIn('ссылку', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
