# -*- coding: utf-8 -*-
"""Офисы: разбор ссылки 2ГИС и нормализация графика.

Обе функции стоят на входе данных: ошибка в них не падает, а тихо кладёт в базу
неверную точку или пустой график, и увидят это уже операторы. Поэтому фикстуры
взяты из боевой статьи «Адреса офисов» — те самые пятнадцать ссылок.
"""

import time
import unittest
from datetime import date, datetime

from urllib.parse import quote

from wiki import offices as wiki_offices
from wiki.offices import (
    DAY_CODES, MAX_PHONES_PER_POINT, clean_phones, link_phones, normalize_schedule,
    parse_day, parse_map_coords, parse_page_coords, resolve_map_link,
    schedule_state_on, snapshot_offices_day, tile_is_valid, unwrap_map_link,
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
            return 307, ASTANA_FULL, ''

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
                                  fetch=lambda url: (302, 'https://evil.example.com/71.4,51.1', ''))
        self.assertIn('error', result)

    def test_redirect_loop_stops(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 307, 'https://go.2gis.com/loop%d' % len(calls), ''

        self.assertIn('error', resolve_map_link('https://go.2gis.com/x', fetch=fetch, max_hops=3))
        self.assertEqual(len(calls), 3)

    def test_promo_interstitial_is_unwrapped(self):
        """С 21.08.2026 2ГИС уводит любую страницу карты на /museum.

        Заглушка отдаёт 200 без Location, так что обход редиректов
        останавливался на ней, а адрес точки всё это время лежал в return_url.
        """
        museum = ('https://2gis.kz/museum?return_url=https%3A%2F%2F2gis.kz%2Falmaty%2Finside%2F'
                  '9430047375127902%2Ffirm%2F70000001041528994%3Fm%3D76.911483%252C43.259332%252F17.67')

        def fetch(url):
            raise AssertionError('адрес точки уже в самой ссылке')

        result = resolve_map_link(museum, fetch=fetch)
        self.assertEqual((result['lat'], result['lon']), (43.259332, 76.911483))

    def test_promo_interstitial_after_redirect_is_unwrapped(self):
        # Короткая ссылка разворачивается в страницу, а та — в заглушку.
        hops = ['https://2gis.kz/almaty/firm/70000001041528994?m=76.911483%2C43.259332%2F17',
                'https://2gis.kz/museum?return_url=x']

        def fetch(url):
            return 302, ('https://2gis.kz/museum?return_url='
                         + 'https%3A%2F%2F2gis.kz%2Falmaty%2Ffirm%2F70000001041528994'
                           '%3Fm%3D76.911483%252C43.259332%252F17'), ''

        result = resolve_map_link('https://go.2gis.com/f7Q9R', fetch=fetch)
        self.assertEqual((result['lat'], result['lon']), (43.259332, 76.911483))

    def test_interstitial_bouncing_back_stops_after_one_request(self):
        """Заглушка возвращает на ту же страницу — второй запрос бесполезен."""
        calls = []
        page = 'https://2gis.kz/almaty/firm/9429940001330174'

        def fetch(url):
            calls.append(url)
            return 302, 'https://2gis.kz/museum?return_url=' + quote(page, safe=''), ''

        self.assertIn('error', resolve_map_link(page, fetch=fetch))
        self.assertEqual(calls, [page])

    def test_return_url_to_foreign_host_is_ignored(self):
        # Иначе заглушка стала бы обходом запрета на чужие хосты.
        self.assertEqual(
            unwrap_map_link('https://2gis.kz/museum?return_url=https%3A%2F%2Fevil.example.com%2F1'),
            'https://2gis.kz/museum?return_url=https%3A%2F%2Fevil.example.com%2F1')

    def test_upstream_failure_is_not_blamed_on_the_link(self):
        """Сеть отвалилась — ссылка может быть верной, и это разные ответы."""
        result = resolve_map_link('https://go.2gis.com/xrzn2', fetch=lambda url: (None, None, ''))
        self.assertTrue(result.get('upstream'))
        self.assertIn('2ГИС', result['error'])

    def test_non_2gis_link_is_rejected_before_network(self):
        def fetch(url):
            raise AssertionError('чужой хост не должен запрашиваться')

        self.assertIn('error', resolve_map_link('https://maps.google.com/?q=1,2', fetch=fetch))
        self.assertIn('error', resolve_map_link('', fetch=fetch))


class ParsePageCoordsTest(unittest.TestCase):
    """Страница точки: половина ссылок 2ГИС не несёт координат вовсе.

    Куски разметки взяты со страниц из справочника — «Офис Яндекса» в Алматы и
    «Wolt» на Толе би.
    """

    WOLT = ('<a class="_1pl504b" href="/almaty/directions/points/'
            '%7C76.960924%2C43.255707%3B70000001031695380">маршрут</a>'
            # Первой на странице стоит граница области — двести километров мимо.
            '{"centroid":"POINT(78.381313 45.013629)"}'
            '{"point":{"lat":43.255707,"lon":76.960924}}')

    def test_point_is_taken_from_the_route_link_of_this_object(self):
        self.assertEqual(
            parse_page_coords(self.WOLT, 'https://2gis.kz/almaty/firm/70000001031695380'),
            (43.255707, 76.960924))

    def test_foreign_route_link_is_not_taken_for_this_object(self):
        """Соседний филиал в списке — не наша точка."""
        html = ('<a href="/almaty/directions/points/%7C76.9%2C43.2%3B111111111111">n</a>'
                '"selection":"POINT(76.946655 43.257699)"')
        self.assertEqual(parse_page_coords(html, 'https://2gis.kz/almaty/firm/9429940001330174'),
                         (43.257699, 76.946655))

    def test_selection_is_the_fallback(self):
        self.assertEqual(
            parse_page_coords('"selection":"POINT(71.406514 51.172930)"',
                              'https://2gis.kz/astana/firm/70000001026025775'),
            (51.17293, 71.406514))

    def test_page_without_a_point(self):
        self.assertIsNone(parse_page_coords('<html>ничего</html>', 'https://2gis.kz/almaty'))
        self.assertIsNone(parse_page_coords('', 'https://2gis.kz/almaty'))


class ResolveViaPageTest(unittest.TestCase):
    def test_link_without_coordinates_is_resolved_by_the_page(self):
        """«Поделиться» у 2ГИС отдаёт /firm/<id> без единой цифры координат."""
        page = ('<a href="/almaty/directions/points/'
                '%7C76.960924%2C43.255707%3B70000001031695380">м</a>')
        result = resolve_map_link('https://2gis.kz/almaty/firm/70000001031695380',
                                  fetch=lambda url: (200, None, page))
        self.assertEqual((result['lat'], result['lon']), (43.255707, 76.960924))

    def test_relative_redirect_is_followed(self):
        """/geo/<id> уводит на /firm/<id> заголовком без схемы и хоста."""
        page = ('<a href="/almaty/directions/points/'
                '%7C76.960924%2C43.255707%3B70000001031695380">м</a>')
        hops = {'https://2gis.kz/almaty/geo/70000001031695380':
                    (302, '/almaty/firm/70000001031695380', ''),
                'https://2gis.kz/almaty/firm/70000001031695380': (200, None, page)}
        result = resolve_map_link('https://2gis.kz/almaty/geo/70000001031695380',
                                  fetch=lambda url: hops[url])
        self.assertEqual((result['lat'], result['lon']), (43.255707, 76.960924))

    def test_relative_redirect_cannot_leave_2gis(self):
        # urljoin по протокол-относительному адресу увёл бы на чужой хост.
        calls = []

        def fetch(url):
            calls.append(url)
            return 302, '//evil.example.com/71.4,51.1', ''

        self.assertIn('error', resolve_map_link('https://go.2gis.com/x', fetch=fetch))
        self.assertEqual(calls, ['https://go.2gis.com/x'])

    def test_page_of_a_foreign_host_is_never_read(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 302, 'https://evil.example.com/71.4,51.1', ''

        self.assertIn('error', resolve_map_link('https://go.2gis.com/x', fetch=fetch))
        self.assertEqual(calls, ['https://go.2gis.com/x'])


class TileBreakerTest(unittest.TestCase):
    """Предохранитель на тайлах.

    2ГИС перестал отвечать серверу (замер 21.08.2026): каждый поход висел до
    таймаута и держал нить waitress. Считаем не «сколько раз повторить», а
    «когда перестать ходить вовсе».
    """

    def setUp(self):
        wiki_offices._TILE_FAILS = 0
        wiki_offices._TILE_MUTED_UNTIL = 0.0

    tearDown = setUp

    def test_series_of_failures_mutes_requests(self):
        for _ in range(wiki_offices._TILE_FAIL_LIMIT):
            self.assertFalse(wiki_offices.tiles_muted())
            wiki_offices._tile_result(False)
        self.assertTrue(wiki_offices.tiles_muted())

    def test_muted_fetch_does_not_touch_the_network(self):
        for _ in range(wiki_offices._TILE_FAIL_LIMIT):
            wiki_offices._tile_result(False)
        # Молчание обязано быть мгновенным: смысл ровно в том, чтобы не занять
        # нить на таймаут.
        started = time.monotonic()
        self.assertIsNone(wiki_offices.fetch_tile(16, 46765, 24033))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_success_resets_the_counter(self):
        wiki_offices._tile_result(False)
        wiki_offices._tile_result(False)
        wiki_offices._tile_result(True)
        wiki_offices._tile_result(False)
        self.assertFalse(wiki_offices.tiles_muted())

    def test_mute_expires(self):
        for _ in range(wiki_offices._TILE_FAIL_LIMIT):
            wiki_offices._tile_result(False)
        self.assertTrue(wiki_offices.tiles_muted())
        wiki_offices._TILE_MUTED_UNTIL = time.monotonic() - 1
        self.assertFalse(wiki_offices.tiles_muted())


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
                         [{'phone': '+7 707 705 08 80', 'note': None}])

    def test_inner_spaces_are_squeezed(self):
        self.assertEqual(clean_phones(['  +7 707   705 08 80 ']),
                         [{'phone': '+7 707 705 08 80', 'note': None}])

    def test_repeats_are_dropped_keeping_order(self):
        self.assertEqual(
            clean_phones(['+7 707 705 08 80', '+7 717 000 00 00', '+7 707 705 08 80']),
            [{'phone': '+7 707 705 08 80', 'note': None},
             {'phone': '+7 717 000 00 00', 'note': None}])

    def test_note_travels_with_the_number(self):
        self.assertEqual(
            clean_phones([{'phone': '+7 707 705 08 80', 'note': '  только WhatsApp '}]),
            [{'phone': '+7 707 705 08 80', 'note': 'только WhatsApp'}])

    def test_repeat_keeps_the_note_it_had(self):
        # Записку у повтора терять нельзя: человек мог дописать её вторым вводом.
        self.assertEqual(
            clean_phones([{'phone': '+7 707 705 08 80'},
                          {'phone': '+7 707 705 08 80', 'note': 'звонить после 10'}]),
            [{'phone': '+7 707 705 08 80', 'note': 'звонить после 10'}])

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
        self.assertEqual(link_phones({'phone': '+7 707 705 08 80'}),
                         [{'phone': '+7 707 705 08 80', 'note': None}])

    def test_list_wins_over_single(self):
        self.assertEqual(link_phones({'phones': ['+7 717 000 00 00'], 'phone': 'старый'}),
                         [{'phone': '+7 717 000 00 00', 'note': None}])


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


class ScheduleStateOnTest(unittest.TestCase):
    """Суточный вердикт по графику.

    Близнец officeStatusOn из officeSchedule.js: одно правило, две реализации —
    сервер пишет им историю, клиент рисует прошедшие дни. Разойдясь, они дадут
    расхождение, которое видно только глазами оператора, поэтому набор случаев
    здесь и в mjs-тесте намеренно один и тот же.
    """

    def test_workday_is_open(self):
        self.assertEqual(schedule_state_on(_KOSTANAY, date(2026, 8, 17)), 'open')  # понедельник

    def test_short_saturday_is_open_too(self):
        self.assertEqual(schedule_state_on(_KOSTANAY, date(2026, 8, 15)), 'open')

    def test_day_off_is_closed(self):
        self.assertEqual(schedule_state_on(_KOSTANAY, date(2026, 8, 16)), 'closed')  # воскресенье

    def test_iso_string_is_accepted(self):
        self.assertEqual(schedule_state_on(_KOSTANAY, '2026-08-16'), 'closed')

    def test_empty_schedule_has_no_state(self):
        # Офис «ОНЛАЙН»: часов работы нет, и «закрыт» про него было бы неправдой.
        self.assertIsNone(schedule_state_on(None, date(2026, 8, 17)))
        self.assertIsNone(schedule_state_on({'mon': None, 'sun': None}, date(2026, 8, 17)))

    def test_broken_date_has_no_state(self):
        self.assertIsNone(schedule_state_on(_KOSTANAY, 'позавчера'))

    def test_parse_day_reads_iso_and_datetime(self):
        self.assertEqual(parse_day('2026-08-19'), date(2026, 8, 19))
        self.assertEqual(parse_day(datetime(2026, 8, 19, 23, 45)), date(2026, 8, 19))
        self.assertIsNone(parse_day('19.08.2026'))
        self.assertIsNone(parse_day(None))


class _SnapshotCursor:
    """Курсор ровно на тех двух запросах, которые делает snapshot_offices_day.

    База здесь не нужна: проверяем не SQL, а обещания снимка — что ручную
    отметку он не трогает и что повторный прогон ничего не переписывает.
    """

    def __init__(self, offices, existing=()):
        self.offices = list(offices)
        self.rows = {key: value for key, value in existing}
        self.rowcount = 0
        self._fetched = []

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        if text.startswith('SELECT id, schedule'):
            self._fetched = self.offices
            return
        if 'INSERT INTO wiki_office_days' in text:
            office_id, day, state = params
            key = (office_id, day)
            if key in self.rows:
                self.rowcount = 0  # ON CONFLICT DO NOTHING
            else:
                self.rows[key] = ('auto', state)
                self.rowcount = 1
            return
        raise AssertionError('неожиданный запрос: %s' % text)

    def fetchall(self):
        return self._fetched


class SnapshotOfficesDayTest(unittest.TestCase):
    DAY = date(2026, 8, 16)  # воскресенье

    def test_writes_one_row_per_office_with_schedule(self):
        cursor = _SnapshotCursor([(1, _KOSTANAY), (2, _KOSTANAY)])
        self.assertEqual(snapshot_offices_day(cursor, self.DAY), 2)
        self.assertEqual(cursor.rows[(1, self.DAY)], ('auto', 'closed'))

    def test_office_without_schedule_is_skipped(self):
        # Иначе «ОНЛАЙН» каждый день попадал бы в историю закрытым.
        cursor = _SnapshotCursor([(1, None), (2, _KOSTANAY)])
        self.assertEqual(snapshot_offices_day(cursor, self.DAY), 1)
        self.assertNotIn((1, self.DAY), cursor.rows)

    def test_manual_record_survives(self):
        cursor = _SnapshotCursor([(1, _KOSTANAY)],
                                 existing=[((1, self.DAY), ('manual', 'open'))])
        self.assertEqual(snapshot_offices_day(cursor, self.DAY), 0)
        self.assertEqual(cursor.rows[(1, self.DAY)], ('manual', 'open'))

    def test_second_run_changes_nothing(self):
        cursor = _SnapshotCursor([(1, _KOSTANAY)])
        self.assertEqual(snapshot_offices_day(cursor, self.DAY), 1)
        self.assertEqual(snapshot_offices_day(cursor, self.DAY), 0)

    def test_broken_day_writes_nothing(self):
        cursor = _SnapshotCursor([(1, _KOSTANAY)])
        self.assertEqual(snapshot_offices_day(cursor, 'позавчера'), 0)
        self.assertEqual(cursor.rows, {})


if __name__ == '__main__':
    unittest.main()
