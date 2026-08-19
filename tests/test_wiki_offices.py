# -*- coding: utf-8 -*-
"""Офисы: разбор ссылки 2ГИС и нормализация графика.

Обе функции стоят на входе данных: ошибка в них не падает, а тихо кладёт в базу
неверную точку или пустой график, и увидят это уже операторы. Поэтому фикстуры
взяты из боевой статьи «Адреса офисов» — те самые пятнадцать ссылок.
"""

import unittest

from wiki.offices import (
    DAY_CODES, MAX_PHONES_PER_POINT, clean_phones, link_phones, normalize_schedule,
    parse_map_coords, resolve_map_link, tile_is_valid,
)


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


class TileBoundsTest(unittest.TestCase):
    """Границы тайла.

    Роут тайлов — единственный без авторизации, и проверка границ у него
    единственный ограничитель: без неё чужой запрос превращал бы наш сервер в
    генератор обращений к 2ГИС с любыми номерами.
    """

    def test_real_tile_passes(self):
        # Головной офис в Астане на зуме 16.
        self.assertTrue(tile_is_valid(16, 45767, 21889))

    def test_zoom_is_limited(self):
        self.assertFalse(tile_is_valid(9, 100, 100))
        self.assertFalse(tile_is_valid(19, 100, 100))
        self.assertTrue(tile_is_valid(10, 100, 100))
        self.assertTrue(tile_is_valid(18, 100, 100))

    def test_coordinates_outside_the_grid(self):
        self.assertFalse(tile_is_valid(16, -1, 0))
        self.assertFalse(tile_is_valid(16, 0, -1))
        self.assertFalse(tile_is_valid(16, 2 ** 16, 0))
        self.assertFalse(tile_is_valid(16, 0, 2 ** 16))
        self.assertTrue(tile_is_valid(16, 2 ** 16 - 1, 2 ** 16 - 1))


class PhonesTest(unittest.TestCase):
    """Номера точки.

    Через эти две функции проходит всё, что попадает в wiki_park_phones: и
    форма парка, и форма офиса, и скрипт переноса. Пустая строка, доехавшая до
    базы, значит номер, по которому оператор не дозвонится, — а он видит его
    как обычную строку справочника.
    """

    def test_empty_and_blank_are_dropped(self):
        self.assertEqual(clean_phones(['+7 707 705 08 80', '', '   ', None]),
                         ['+7 707 705 08 80'])

    def test_inner_spaces_are_squeezed(self):
        self.assertEqual(clean_phones(['  +7 707   705 08 80 ']), ['+7 707 705 08 80'])

    def test_repeats_are_dropped_keeping_order(self):
        self.assertEqual(
            clean_phones(['+7 707 705 08 80', '+7 717 000 00 00', '+7 707 705 08 80']),
            ['+7 707 705 08 80', '+7 717 000 00 00'])

    def test_count_is_capped(self):
        many = ['+7 700 000 00 %02d' % index for index in range(20)]
        self.assertEqual(len(clean_phones(many)), MAX_PHONES_PER_POINT)

    def test_none_and_garbage_are_empty(self):
        self.assertEqual(clean_phones(None), [])
        self.assertEqual(clean_phones([]), [])

    def test_link_without_phones_asks_to_leave_them_alone(self):
        # None — «ключа не было»: правка графика офиса не должна стирать номера.
        self.assertIsNone(link_phones({'park_id': 1}))
        self.assertIsNone(link_phones(None))

    def test_link_with_empty_list_clears(self):
        self.assertEqual(link_phones({'phones': []}), [])

    def test_old_single_phone_is_understood(self):
        self.assertEqual(link_phones({'phone': '+7 707 705 08 80'}), ['+7 707 705 08 80'])

    def test_list_wins_over_single(self):
        self.assertEqual(link_phones({'phones': ['+7 717 000 00 00'], 'phone': 'старый'}),
                         ['+7 717 000 00 00'])


# ─────────────────────────────────────────────────────────────────────────────
# Статус за день
# ─────────────────────────────────────────────────────────────────────────────

_WORKDAY = {'from': '09:00', 'to': '19:00', 'break_from': '13:00', 'break_to': '14:00'}

# Костанай из справочника: Пн–Пт полный день, суббота короткая, воскресенье
# выходное. Те же данные проверяет tests/wiki_office_schedule.test.mjs.
_KOSTANAY = {
    'mon': _WORKDAY, 'tue': _WORKDAY, 'wed': _WORKDAY, 'thu': _WORKDAY, 'fri': _WORKDAY,
    'sat': {'from': '10:00', 'to': '13:00'},
    'sun': None,
}


if __name__ == '__main__':
    unittest.main()
