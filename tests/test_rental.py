# -*- coding: utf-8 -*-
"""Раздел «Аренда авто» — ночной отчёт по арендным машинам (#398): правила, источники, проводка.

Что сторожится:
  * госномер находится в именах объектов всех трёх GPS-систем и не находится в VIN;
  * правила ТЗ: «Статус аренды» только из статуса Yume «В аренде», офлайн — больше 4 часов,
    машина в двух системах — одной строкой по свежему сигналу, машины парка без GPS — «Нет GPS»;
  * «Состояние» по адресу — во всех форматах, которые реально отдают ГЛОНАСС, Wialon и как
    его пишет ночная смена (Утеген Батыра 15А/15/Отарская — база, 15/1 и «Автомастерская» — ремонт);
  * «Уточнить» — только у машин парка не в аренде, чужие машины GPS не шумят;
  * клиенты Yume, Wialon, ГЛОНАСС и Efir разбирают ответы так, как они приходят на самом деле
    (проверено на живых системах 27.10.2026), а сбой одного источника не роняет отчёт;
  * Excel — столбцы ТЗ, «Статус аренды» только за 01:00, строки из внешних систем — текстом;
  * права: ООЗ и его глава, супер-админ; уволенный — нет;
  * раздел подключён: схема, Blueprint, флаг профиля, планировщик 01:00 и 07:00, меню и гарды.

Процесс на настоящей базе — tests/test_rental_postgres.py.
"""

import json
import sys
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rental import access, collect, plates, report, rules  # noqa: E402
from rental.sources import EfirSource, GlonassSource, SourceError, WialonSource, YumeSource  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
BOT_PY = ROOT / 'bot_schedule2.py'
DATABASE_PY = ROOT / 'database.py'

NOW = 1793080800.0  # 27.10.2026 16:00 по Алматы


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is ValueError:
            raise ValueError('not json')
        return self._payload


class FakeSession:
    """Отвечает по первому совпавшему правилу (метод, кусок адреса, функция от аргументов)."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def _dispatch(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for route_method, fragment, handler in self.routes:
            if route_method == method and fragment in url:
                return handler(kwargs)
        raise AssertionError('неожиданный запрос %s %s' % (method, url))

    def post(self, url, **kwargs):
        return self._dispatch('POST', url, kwargs)

    def get(self, url, **kwargs):
        return self._dispatch('GET', url, kwargs)


class PlatesTests(unittest.TestCase):
    def test_plate_is_found_in_every_real_name_format(self):
        cases = {
            '010XYZ08': {'010XYZ08'},
            'Hongqi EQM5 020XY02': {'020XY02'},
            'Taxi Technol 040XY02 Elantra ABCDE12FGHJ345678': {'040XY02'},
            '030XYZ08 (маяк)': {'030XYZ08'},
            'P1234 02': {'P123402'},
            '050 XY | 02': {'050XY02'},
            '060XYZ | 02': {'060XYZ02'},
            'GoPartners  070XY02': {'070XY02'},
        }
        for name, expected in cases.items():
            self.assertEqual(plates.find_in_name(name), expected, name)

    def test_vin_does_not_yield_a_fake_plate(self):
        # Первая сверка 27.10 искала по склеенной строке и находила «K874025» внутри VIN.
        self.assertEqual(plates.find_in_name('Taxi Technologies 080XY02 KLMNP34QRST567890K874025'), {'080XY02'})

    def test_normalize_maps_cyrillic_twins_and_drops_spaces(self):
        self.assertEqual(plates.normalize(' 501 abe 08'), '501ABE08')
        self.assertEqual(plates.normalize('501АВЕ08'), '501ABE08')
        self.assertEqual(plates.normalize(None), '')


class AddressStateTests(unittest.TestCase):
    def test_real_address_formats(self):
        cases = [
            ('15/1 улица Утеген Батыра, Алматы, Казахстан', rules.STATE_REPAIR),      # ГЛОНАСС
            ('15А улица Утеген Батыра, Алматы, Казахстан', rules.STATE_BASE),        # ГЛОНАСС
            ('улица Отарская, Алматы, Казахстан', rules.STATE_BASE),                  # ГЛОНАСС
            ('Утеген Батыра ул., 15/1, Алматы, Әуезов ауданы, Kazakhstan', rules.STATE_REPAIR),  # Wialon
            ('Утеген Батыра ул., 15А, Алматы, Әуезов ауданы, Kazakhstan', rules.STATE_BASE),     # Wialon
            ('Отар Көшесі, Алматы, Әуезов ауданы, Kazakhstan', rules.STATE_BASE),                # Wialon
            ('Ауэзовский р-н, ул. Утеген Батыра, 15/1', rules.STATE_REPAIR),         # ночная смена
            ('Автомастерская, 17Б', rules.STATE_REPAIR),                              # ночная смена
            ('Утеген батыра 15', rules.STATE_BASE),                                   # ТЗ п. 2.5
        ]
        for address, expected in cases:
            self.assertEqual(rules.state_by_address(address), expected, address)

    def test_other_houses_and_streets_are_unknown(self):
        for address in ('Утеген Батыра ул., 150, Алматы', 'Утеген Батыра ул., 15Б, Алматы',
                        'проспект Абая, Алматы, Казахстан', 'улица Отрар, Алматы', '', None):
            self.assertIsNone(rules.state_by_address(address), address)

    def test_city_after_the_house_is_not_a_house_letter(self):
        self.assertEqual(rules.state_by_address('Утеген Батыра 15 Алматы'), rules.STATE_BASE)
        self.assertEqual(rules.state_by_address('Алматы 050000, Утеген Батыра 15а'), rules.STATE_BASE)


def _unit(site, plate, t, address=None):
    return {'site': site, 'name': plate, 'plates': {plate}, 't': t, 'lat': 43.2, 'lon': 76.8, 'address': address}


class RowsTests(unittest.TestCase):
    def test_gps_status_threshold_is_four_hours(self):
        self.assertEqual(rules.gps_status(NOW - 4 * 3600, NOW), rules.GPS_ONLINE)
        self.assertEqual(rules.gps_status(NOW - 4 * 3600 - 1, NOW), rules.GPS_OFFLINE)
        self.assertEqual(rules.gps_status(None, NOW), rules.GPS_OFFLINE)

    def test_rent_status_only_from_yume_in_rent(self):
        self.assertEqual(rules.rent_status({'status': 2}), rules.RENT_IN)
        for status in (0, 1, 3, 4, 5, 6):
            self.assertEqual(rules.rent_status({'status': status}), rules.RENT_NO)
        self.assertEqual(rules.rent_status(None), rules.RENT_NO)

    def test_car_in_two_systems_is_one_row_by_the_freshest_signal(self):
        rows = rules.build_rows([
            _unit('ГЛОНАСС', '111AAA02', NOW - 600, 'улица Отарская, Алматы'),
            _unit('Wialon', '111AAA02', NOW - 60, 'Абая 10'),
        ], {}, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['gps_site'], 'Wialon')
        self.assertEqual(rows[0]['address'], 'Абая 10')
        self.assertEqual(rows[0]['gps_sites'], ['Wialon', 'ГЛОНАСС'])

    def test_fleet_car_without_gps_is_its_own_row_and_deleted_cars_are_not(self):
        yume = {'222BBB02': {'status': 2, 'deleted': False, 'model': 'BYD Han'},
                '333CCC02': {'status': 5, 'deleted': True, 'model': 'Geely'}}
        rows = rules.build_rows([], yume, NOW)
        self.assertEqual([(r['plate'], r['gps_status'], r['rent_status']) for r in rows],
                         [('222BBB02', rules.GPS_NONE, rules.RENT_IN)])

    def test_yume_silent_means_unknown_rent_and_no_yume_rows(self):
        rows = rules.build_rows([_unit('Efir', '444DDD02', NOW)], {'555EEE02': {'status': 2}}, NOW, yume_ok=False)
        self.assertEqual([(r['plate'], r['rent_status']) for r in rows], [('444DDD02', None)])

    def test_state_is_filled_by_address_and_marked_so(self):
        rows = rules.build_rows([_unit('Wialon', '666FFF02', NOW, 'Утеген Батыра ул., 15/1, Алматы')], {}, NOW)
        self.assertEqual((rows[0]['state'], rows[0]['state_source']), (rules.STATE_REPAIR, rules.STATE_BY_ADDRESS))

    def test_check_only_for_fleet_cars_not_in_rent_and_not_at_base(self):
        yume = {'777GGG02': {'status': 0, 'deleted': False}, '888HHH02': {'status': 2, 'deleted': False}}
        rows = {r['plate']: r for r in rules.build_rows([
            _unit('Wialon', '777GGG02', NOW, 'Северное Кольцо, 80'),       # свободна и не на базе → уточнить
            _unit('Wialon', '888HHH02', NOW, 'Северное Кольцо, 80'),       # в аренде → у водителя
            _unit('Efir', '999III02', NOW, 'Северное Кольцо, 80'),          # не наша (GoPartners) → не шумим
        ], yume, NOW)}
        self.assertTrue(rules.needs_check(rows['777GGG02']))
        self.assertFalse(rules.needs_check(rows['888HHH02']))
        self.assertFalse(rules.needs_check(rows['999III02']))

    def test_attention_first(self):
        yume = {'200AAA02': {'status': 0, 'deleted': False}, '300AAA02': {'status': 2, 'deleted': False}}
        rows = rules.build_rows([
            _unit('Wialon', '100AAA02', NOW),
            _unit('Wialon', '200AAA02', NOW, 'Абая 1'),
            _unit('Wialon', '400AAA02', NOW - 5 * 3600),
        ], yume, NOW)
        self.assertEqual([r['plate'] for r in rows], ['400AAA02', '300AAA02', '200AAA02', '100AAA02'])


class SlotTests(unittest.TestCase):
    def test_current_and_next_slot(self):
        self.assertEqual(rules.current_slot(datetime(2026, 10, 28, 0, 30)), ('morning', datetime(2026, 10, 27, 7)))
        self.assertEqual(rules.current_slot(datetime(2026, 10, 28, 1, 0)), ('night', datetime(2026, 10, 28, 1)))
        self.assertEqual(rules.current_slot(datetime(2026, 10, 28, 9, 0)), ('morning', datetime(2026, 10, 28, 7)))
        self.assertEqual(rules.next_slot(datetime(2026, 10, 28, 0, 30)), ('night', datetime(2026, 10, 28, 1)))
        self.assertEqual(rules.next_slot(datetime(2026, 10, 28, 3, 0)), ('morning', datetime(2026, 10, 28, 7)))
        self.assertEqual(rules.next_slot(datetime(2026, 10, 28, 7, 0)), ('night', datetime(2026, 10, 29, 1)))

    def test_catch_up_window_is_six_hours(self):
        slot = datetime(2026, 10, 28, 1)
        self.assertTrue(rules.can_catch_up(slot, datetime(2026, 10, 28, 6, 59)))
        self.assertFalse(rules.can_catch_up(slot, datetime(2026, 10, 28, 7, 0)))
        self.assertFalse(rules.can_catch_up(slot, datetime(2026, 10, 28, 0, 59)))


class YumeSourceTests(unittest.TestCase):
    def _session(self, pages, status=200):
        def login(kwargs):
            self.assertEqual(kwargs['json'], {'username': 'u', 'password': 'p'})
            return FakeResponse(payload={'access': 'jwt'})

        def inventories(kwargs):
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer jwt')
            if status != 200:
                return FakeResponse(status)
            page = kwargs['params']['page']
            return FakeResponse(payload={'results': pages[page - 1], 'next': 'x' if page < len(pages) else None})

        return FakeSession([('POST', '/v1/auth/login/', login), ('GET', '/v1/crm/inventories/', inventories)])

    def test_fleet_by_plate_prefers_live_record_in_rent(self):
        def item(number, status, deleted=False, brand='Hongqi', model='E-QM5'):
            return {'name': 'HONGQI EQM5', 'status': status, 'deleted': deleted,
                    'car': {'brand': brand, 'model': model, 'number': number}}
        pages = [[item('111AAA02', 5, deleted=True), item('111AAA02', 0), item('222ВВВ02', 2)],
                 [item('111AAA02', 1), item('', 0), item(None, 0)]]
        cars = YumeSource('u', 'p', session=self._session(pages)).fetch()
        self.assertEqual(set(cars), {'111AAA02', '222BBB02'})
        self.assertEqual(cars['111AAA02'], {'status': 1, 'deleted': False, 'model': 'Hongqi E-QM5'})

    def test_closed_inventory_says_which_right_is_missing(self):
        with self.assertRaises(SourceError) as caught:
            YumeSource('u', 'p', session=self._session([[]], status=403)).fetch()
        self.assertIn('просмотр инвентаря', str(caught.exception))

    def test_without_credentials_nothing_is_called(self):
        with self.assertRaises(SourceError):
            YumeSource('', '', session=FakeSession([])).fetch()


class WialonSourceTests(unittest.TestCase):
    def _factory(self, accounts):
        sessions = iter(accounts)
        return lambda: next(sessions)

    def _account(self, *, token_ok=True, items=(), uid=77, geocoded=None):
        def authorize(kwargs):
            self.assertEqual(kwargs['data']['access_type'], str(0x300))
            location = 'https://hosting.wialon.com/post_token.html?access_token=tok&svc_error=0' if token_ok else 'x'
            return FakeResponse(301, headers={'Location': location})

        def ajax(kwargs):
            svc = kwargs['data']['svc']
            if svc == 'token/login':
                return FakeResponse(payload={'eid': 'sid', 'user': {'id': uid}})
            if svc == 'core/search_items':
                return FakeResponse(payload={'items': list(items)})
            return FakeResponse(payload={})

        def geocode(kwargs):
            self.assertEqual(kwargs['params']['uid'], uid)
            coords = json.loads(kwargs['params']['coords'])
            names = (geocoded or {})
            return FakeResponse(payload=[names.get((c['lat'], c['lon']), 'адрес') for c in coords])

        return FakeSession([('POST', 'oauth/authorize.html', authorize), ('POST', 'ajax.html', ajax),
                            ('GET', 'gis_geocode', geocode)])

    def test_units_addresses_and_extra_points(self):
        first = self._account(items=[{'nm': 'Hongqi EQM5 020XY02', 'pos': {'t': NOW, 'x': 76.85, 'y': 43.24}},
                                     {'nm': '030XYZ08 (маяк)', 'pos': None}],
                              geocoded={(43.24, 76.85): 'Утеген Батыра ул., 15А', (43.0, 76.0): 'Efir-адрес'})
        second = self._account(token_ok=False)
        pauses = []
        source = WialonSource([{'login': 'a', 'password': 'b'}, {'login': 'c', 'password': 'd'},
                               {'login': '', 'password': ''}], session_factory=self._factory([first, second]),
                              sleep=pauses.append)
        units, extra, errors = source.fetch([(43.0, 76.0)])
        self.assertEqual([(u['name'], u['address']) for u in units],
                         [('Hongqi EQM5 020XY02', 'Утеген Батыра ул., 15А'), ('030XYZ08 (маяк)', None)])
        self.assertEqual(units[0]['plates'], {'020XY02'})
        self.assertEqual(extra, ['Efir-адрес'])
        self.assertEqual(len(errors), 1)
        # Причина отказа видна в отчёте: статус и то, что редиректа с токеном не было; повтор — один.
        self.assertIn('учётка 2: вход в Wialon не удался (HTTP 301, без редиректа)', errors[0])
        self.assertEqual(pauses, [3.0])

    def test_login_is_retried_once_and_wialon_code_is_kept(self):
        answers = iter(['https://hosting.wialon.com/post_token.html?svc_error=1003',
                        'https://hosting.wialon.com/post_token.html?access_token=tok&svc_error=0'])

        def authorize(_kwargs):
            return FakeResponse(301, headers={'Location': next(answers)})

        session = FakeSession([
            ('POST', 'oauth/authorize.html', authorize),
            ('POST', 'ajax.html', lambda kw: FakeResponse(payload={'eid': 'sid', 'user': {'id': 1}}
                                                          if kw['data']['svc'] == 'token/login' else {'items': []})),
        ])
        source = WialonSource([{'login': 'a', 'password': 'b'}], session_factory=lambda: session, sleep=lambda _s: None)
        _units, _extra, errors = source.fetch()
        self.assertEqual(errors, [])

        always_refused = FakeSession([('POST', 'oauth/authorize.html', lambda _kw: FakeResponse(
            301, headers={'Location': 'https://hosting.wialon.com/post_token.html?svc_error=1003'}))])
        source = WialonSource([{'login': 'a', 'password': 'b'}], session_factory=lambda: always_refused,
                              sleep=lambda _s: None)
        self.assertIn('код Wialon 1003', source.fetch()[2][0])


class GlonassSourceTests(unittest.TestCase):
    def test_last_data_by_vehicles_ids_in_utc(self):
        def lastdata(kwargs):
            self.assertEqual(kwargs['json'], {'vehiclesIds': [5]})   # не vehicleIds и не голый массив
            self.assertEqual(kwargs['headers']['X-Auth'], 'auth')
            return FakeResponse(payload=[{'vehicleId': 5, 'recordTime': '2026-10-27T06:04:20Z',
                                          'latitude': 43.2, 'longitude': 76.8,
                                          'address': '15А улица Утеген Батыра, Алматы, Казахстан'}])

        session = FakeSession([
            ('POST', '/v3/auth/login', lambda kw: FakeResponse(payload={'AuthId': 'auth'})),
            ('POST', '/v3/vehicles/find', lambda kw: FakeResponse(payload=[{'vehicleId': 5, 'name': '050 XY | 02'}])),
            ('POST', '/v3/vehicles/getlastdata', lastdata),
        ])
        units = GlonassSource('l', 'p', session=session).fetch()
        expected_ts = datetime(2026, 10, 27, 6, 4, 20, tzinfo=timezone.utc).timestamp()
        self.assertEqual(units, [{'site': 'ГЛОНАСС', 'name': '050 XY | 02', 'plates': {'050XY02'}, 't': expected_ts,
                                  'lat': 43.2, 'lon': 76.8, 'address': '15А улица Утеген Батыра, Алматы, Казахстан'}])


class EfirSourceTests(unittest.TestCase):
    def test_units_from_existing_tracking_session(self):
        session = FakeSession([
            ('POST', 'go-efirsoft.com/api/auth/login', lambda kw: FakeResponse(payload={'success': True, 'data': 'jwt'})),
            ('GET', '/api/tracking/sessions', lambda kw: FakeResponse(payload={'data': [{'_id': 's1'}]})),
            ('GET', '/api/tracking/s1/units', lambda kw: FakeResponse(payload=[
                {'name': 'GoPartners  070XY02', 'position': {'t': 1793080931000, 'lt': 43.23, 'ln': 76.67}}])),
        ])
        units = EfirSource('https://go-efirsoft.com/login', 'l', 'p', session=session).fetch()
        self.assertEqual(units[0]['plates'], {'070XY02'})
        self.assertEqual(units[0]['t'], 1793080931.0)
        self.assertNotIn('/tracking/new', ' '.join(call[1] for call in session.calls))


class CollectTests(unittest.TestCase):
    def test_one_silent_source_does_not_drop_the_report(self):
        class Yume:
            def fetch(self):
                return {'111AAA02': {'status': 2, 'deleted': False, 'model': 'BYD Han'},
                        '999ZZZ02': {'status': 2, 'deleted': False, 'model': 'Kaiyi E5'}}

        class Glonass:
            def fetch(self):
                raise SourceError('вход в ГЛОНАСС не удался (HTTP 401)')

        class Efir:
            def fetch(self):
                return [{'site': 'Efir', 'name': 'GoPartners 070XY02', 'plates': {'070XY02'}, 't': NOW,
                         'lat': 43.0, 'lon': 76.0, 'address': None}]

        class Wialon:
            def fetch(self, extra):
                assert extra == [(43.0, 76.0)]
                return ([{'site': 'Wialon', 'name': '111AAA02', 'plates': {'111AAA02'}, 't': NOW,
                          'lat': 43.24, 'lon': 76.85, 'address': 'Утеген Батыра ул., 15А'}], ['Абая 1'], [])

        result = collect.collect({}, now_ts=NOW, make_sources=lambda _c: (Yume(), Wialon(), Glonass(), Efir()))
        sources = result['sources']
        self.assertEqual(sources['glonass'], {'ok': False, 'count': 0, 'error': 'вход в ГЛОНАСС не удался (HTTP 401)'})
        self.assertTrue(sources['wialon']['ok'] and sources['efir']['ok'] and sources['yume']['ok'])
        rows = {row['plate']: row for row in result['rows']}
        self.assertEqual(rows['070XY02']['address'], 'Абая 1')
        self.assertEqual(rows['999ZZZ02']['gps_status'], rules.GPS_NONE)
        self.assertEqual(rows['111AAA02']['state'], rules.STATE_BASE)

    def test_config_reads_four_wialon_accounts_from_env(self):
        config = collect.config_from_env({'WIALON_LOGIN_1': 'a', 'WIALON_PASSWORD_1': 'b', 'WIALON_TOKEN_3': 't',
                                          'YUME_CLOUD_LOGIN': 'y'})
        self.assertEqual(len(config['wialon']), 4)
        self.assertEqual(config['wialon'][0], {'token': '', 'login': 'a', 'password': 'b'})
        self.assertEqual(config['wialon'][2]['token'], 't')
        self.assertEqual(config['yume']['login'], 'y')


class ReportTests(unittest.TestCase):
    def _book(self, kind, rows):
        from openpyxl import load_workbook
        rep = {'id': 1, 'kind': kind, 'slot_at': datetime(2026, 10, 28, rules.KIND_HOUR[kind]),
               'collected_at': datetime(2026, 10, 28, rules.KIND_HOUR[kind], 1), 'status': 'ready',
               'sources': {'efir': {'ok': False, 'count': 0, 'error': 'не ответил'}}, 'error': None}
        stream, written = report.build_workbook(rep, rows, generated_by='Глава ООЗ', generated_at=datetime(2026, 10, 28, 8))
        return load_workbook(BytesIO(stream.getvalue())), written

    def test_rent_column_only_in_night_report(self):
        row = {'gps_site': 'Wialon', 'plate': '111AAA02', 'rent_status': 'in_rent', 'gps_status': 'online',
               'state': 'base', 'signal_at': datetime(2026, 10, 28, 0, 58), 'address': '=HYPERLINK("x")'}
        book, written = self._book(rules.KIND_NIGHT, [row])
        sheet = book['Отчёт']
        self.assertEqual(written, 1)
        self.assertEqual([c.value for c in sheet[1]], ['GPS-сайт', 'Гос. номер', 'Статус аренды', 'Статус GPS',
                                                      'Состояние', 'Последнее время отправки трекинга',
                                                      'Местоположение / Адрес'])
        self.assertEqual([c.value for c in sheet[2]][:5], ['Wialon', '111AAA02', 'В аренде', 'Онлайн', 'На базе'])
        self.assertEqual(sheet['G2'].data_type, 's')   # адрес с «=» не стал формулой
        morning, _ = self._book(rules.KIND_MORNING, [row])
        self.assertNotIn('Статус аренды', [c.value for c in morning['Отчёт'][1]])
        context = [tuple(c.value for c in r) for r in book['Контекст'].iter_rows()]
        self.assertIn(('Efir', 'не ответил: не ответил'), context)

    def test_file_name_has_no_colon(self):
        rep = {'kind': rules.KIND_NIGHT, 'slot_at': datetime(2026, 10, 28, 1)}
        self.assertEqual(report.report_filename(rep), 'Аренда авто 28.10.2026 01-00.xlsx')


class AccessTests(unittest.TestCase):
    def test_ooz_staff_head_and_super_admin(self):
        self.assertTrue(access.can_open_section({'role': 'operator', 'department_code': 'request_processing_department'}))
        self.assertTrue(access.can_open_section({'role': 'admin', 'department_code': 'szov',
                                                 'headed_codes': ['request_processing_department']}))
        self.assertTrue(access.can_open_section({'role': 'super_admin'}))
        self.assertFalse(access.can_open_section({'role': 'admin', 'department_code': 'szov'}))
        self.assertFalse(access.can_open_section({'role': 'operator', 'department_code': 'request_processing_department',
                                                  'status': 'fired'}))

    def test_out_of_window_collect_only_for_super_admin(self):
        self.assertTrue(access.capabilities({'role': 'super_admin'})['collect_any_time'])
        self.assertFalse(access.capabilities({'role': 'operator',
                                              'department_code': 'request_processing_department'})['collect_any_time'])


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT_PY.read_text(encoding='utf-8')
        cls.app = APP_JSX.read_text(encoding='utf-8')
        cls.database = DATABASE_PY.read_text(encoding='utf-8')

    def test_schema_blueprint_flag_and_scheduler(self):
        self.assertIn('self._init_rental_schema_tx(cursor)', self.database)
        self.assertIn('build_rental_blueprint(', self.bot)
        self.assertIn('"rental_access": rental_access,', self.bot)
        self.assertIn("for _rental_kind, _rental_hour in (('night', 1), ('morning', 7)):", self.bot)
        self.assertIn("timezone=ZoneInfo('Asia/Almaty')", self.bot[self.bot.index('rental_night_report_job'):])

    def test_menu_guards_and_view(self):
        self.assertIn("const RentalView = lazyWithRetry(() => import('./components/rental/RentalView'));", self.app)
        self.assertIn("userLike?.rental_access === true", self.app)
        self.assertIn("(requestedViewFromUrl !== 'rental' || canAccessRentalSection)", self.app)
        self.assertIn("if (view === 'rental' && !canAccessRentalSection) {", self.app)
        self.assertIn("if (view === 'rental' && canAccessRentalSection) return;", self.app)
        self.assertIn("handleSidebarViewNavigation(e, 'rental')", self.app)
        self.assertIn("{view === 'rental' && canAccessRentalSection && (", self.app)


if __name__ == '__main__':
    unittest.main()
