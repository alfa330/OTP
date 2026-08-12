# -*- coding: utf-8 -*-
"""Офисы: разбор ссылки 2ГИС и нормализация графика.

Обе функции стоят на входе данных: ошибка в них не падает, а тихо кладёт в базу
неверную точку или пустой график, и увидят это уже операторы. Поэтому фикстуры
взяты из боевой статьи «Адреса офисов» — те самые пятнадцать ссылок.
"""

import unittest

from wiki.offices import DAY_CODES, normalize_schedule, parse_map_coords, resolve_map_link


# Реальный ответ go.2gis.com/xrzn2 (головной офис в Астане).
ASTANA_FULL = ('https://2gis.kz/astana/branches/70000001026025774/firm/'
               '70000001026025775/71.406531,51.173128?m=71.406545%2C51.173129%2F18')


class ParseMapCoordsTest(unittest.TestCase):
    def test_full_link_gives_point(self):
        self.assertEqual(parse_map_coords(ASTANA_FULL), (51.173129, 71.406545))

    def test_m_parameter_wins_over_path(self):
        """Параметр m — центр карты, он ставится ровно на точку.

        В пути у ссылки на филиал стоят координаты здания, и они отличаются от
        центра на десятки метров. Спор решается в пользу m.
        """
        self.assertEqual(parse_map_coords(ASTANA_FULL)[1], 71.406545)

    def test_path_is_used_when_no_m(self):
        url = 'https://2gis.kz/almaty/geo/9430047370201856/76.945465,43.238293'
        self.assertEqual(parse_map_coords(url), (43.238293, 76.945465))

    def test_latitude_first_is_recognised(self):
        # Координаты, скопированные из Яндекс-карт, идут в обратном порядке.
        # Прямое чтение (lon=43.2) в Казахстан не попадает, обратное попадает.
        url = 'https://2gis.kz/almaty?m=43.238293%2C76.945465%2F16'
        self.assertEqual(parse_map_coords(url), (43.238293, 76.945465))

    def test_ambiguous_pair_is_read_as_2gis_writes_it(self):
        """Астана: 51.17 и 71.40 оба допустимы и как широта, и как долгота.

        Разрешать спор «по правдоподобию» здесь нельзя — 2ГИС пишет lon,lat, и
        отгадывание увело бы точку за 2000 км в Тюменскую область.
        """
        self.assertEqual(parse_map_coords('https://2gis.kz/astana?m=71.406545%2C51.173129%2F18'),
                         (51.173129, 71.406545))

    def test_foreign_host_is_rejected(self):
        # Иначе поле «ссылка 2ГИС» стало бы способом заставить наш сервер
        # сходить на произвольный адрес.
        for url in ('https://evil.example.com/71.4,51.1',
                    'https://2gis.kz.evil.com/71.4,51.1',
                    'file:///etc/passwd',
                    'javascript:alert(1)'):
            self.assertIsNone(parse_map_coords(url), url)

    def test_link_without_coordinates(self):
        self.assertIsNone(parse_map_coords('https://go.2gis.com/xrzn2'))
        self.assertIsNone(parse_map_coords(''))
        self.assertIsNone(parse_map_coords(None))


class ResolveMapLinkTest(unittest.TestCase):
    def test_short_link_is_expanded(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 307, ASTANA_FULL

        result = resolve_map_link('https://go.2gis.com/xrzn2', fetch=fetch)
        self.assertEqual(calls, ['https://go.2gis.com/xrzn2'])
        self.assertEqual((result['lat'], result['lon']), (51.173129, 71.406545))
        self.assertEqual(result['resolved_url'], ASTANA_FULL)

    def test_full_link_does_not_touch_network(self):
        def fetch(url):
            raise AssertionError('сеть не нужна: координаты уже в ссылке')

        self.assertEqual(resolve_map_link(ASTANA_FULL, fetch=fetch)['lat'], 51.173129)

    def test_redirect_off_2gis_is_not_followed(self):
        result = resolve_map_link('https://go.2gis.com/xrzn2',
                                  fetch=lambda url: (302, 'https://evil.example.com/71.4,51.1'))
        self.assertIn('error', result)

    def test_redirect_loop_stops(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 307, 'https://go.2gis.com/loop%d' % len(calls)

        self.assertIn('error', resolve_map_link('https://go.2gis.com/x', fetch=fetch, max_hops=3))
        self.assertEqual(len(calls), 3)

    def test_non_2gis_link_is_rejected_before_network(self):
        def fetch(url):
            raise AssertionError('чужой хост не должен запрашиваться')

        self.assertIn('error', resolve_map_link('https://maps.google.com/?q=1,2', fetch=fetch))
        self.assertIn('error', resolve_map_link('', fetch=fetch))


class NormalizeScheduleTest(unittest.TestCase):
    def test_working_day_with_lunch(self):
        result = normalize_schedule({'mon': {'from': '9:00', 'to': '19:00',
                                             'break_from': '13:00', 'break_to': '14:00'}})
        self.assertEqual(result['mon'], {'from': '09:00', 'to': '19:00',
                                         'break_from': '13:00', 'break_to': '14:00'})
        # Непереданные дни — выходные, а не отсутствующие ключи: карточка
        # обязана уметь показать «Вс выходной», не догадываясь по пропуску.
        self.assertEqual(set(result), set(DAY_CODES))
        self.assertIsNone(result['sun'])

    def test_half_a_break_is_dropped(self):
        # Одна граница обеда без второй — опечатка. Сохранить её значит
        # показать «обед 13:00–» и посчитать статус по мусору.
        result = normalize_schedule({'mon': {'from': '09:00', 'to': '19:00', 'break_from': '13:00'}})
        self.assertEqual(result['mon'], {'from': '09:00', 'to': '19:00'})

    def test_broken_times_make_the_day_off(self):
        # Рабочий вторник в фикстуре нужен, чтобы график целиком не схлопнулся
        # в None и проверялось именно поведение отдельного дня.
        good_day = {'from': '09:00', 'to': '19:00'}
        for day in ({'from': '25:00', 'to': '19:00'},
                    {'from': '09:00', 'to': 'вечер'},
                    {'from': '09:00', 'to': '09:00'},
                    {'from': '09:00'},
                    'Пн-Пт 09:00-19:00'):
            result = normalize_schedule({'mon': day, 'tue': good_day})
            self.assertIsNone(result['mon'], day)
            self.assertEqual(result['tue'], good_day, day)

    def test_empty_schedule_is_none(self):
        # У офиса «ОНЛАЙН» часов работы нет. Семь выходных вместо None
        # означали бы «закрыто навсегда» вместо «только по телефону».
        for value in (None, {}, 'null', {'mon': None}, {'mon': {'from': '', 'to': ''}}):
            self.assertIsNone(normalize_schedule(value), value)

    def test_json_string_is_accepted(self):
        result = normalize_schedule('{"sat": {"from": "10:00", "to": "13:00"}}')
        self.assertEqual(result['sat'], {'from': '10:00', 'to': '13:00'})

    def test_unknown_keys_are_ignored(self):
        result = normalize_schedule({'mon': {'from': '09:00', 'to': '19:00', 'note': 'x'},
                                     'holiday': {'from': '09:00', 'to': '19:00'}})
        self.assertEqual(result['mon'], {'from': '09:00', 'to': '19:00'})
        self.assertNotIn('holiday', result)


if __name__ == '__main__':
    unittest.main()
