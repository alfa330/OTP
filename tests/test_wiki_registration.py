# -*- coding: utf-8 -*-
"""Регистрация водителей во вкладке «Города» (просьба владельца 29.10.2026).

«Куда зарегать водителя айтакси, если он обращается с Рудного?» — подсказать
город, другие таксопарки не предлагать. Набор держит шесть вещей, и каждая
ловит свой класс поломки:

1. ПРИВЯЗКА (DispatcherLinkTest) — все 89 диспетчерских кабинета на 28.10.2026
   находят свой парк вики и свой город: «Адал» — Честный (решение владельца),
   «Честный Темиртау» работает на Темиртау, хотя в кабинете записан в Караганду.
2. ПРАВИЛО (ResolveTest) — свой город, затем города «куда регистрировать» по
   порядку, иначе честное «нет»; одно на карточку, поиск и помощника.
3. ВОПРОС (QuestionTest) — признак берётся из формулировок владельца, а
   «зарегистрирован в Шымкенте» и «после регистрации без комиссии» молчат.
4. ПОМОЩНИК (AssistantRowsTest, PromptTest) — фрагмент несёт город и
   диспетчерскую буквально, правило промпта приходит только с ним.
5. ГРАНИЦА ПРОСТРАНСТВА (DispatcherSpaceGuardTest, DispatcherSqlTest,
   DispatcherRouteTest) — у Тез кабинета нет, и чужой id не открывает ничего.
6. РАЗВЁРТЫВАНИЕ (RegistrationSchemaTest) — таблицы до журнала, заливка
   городов-спутников один раз и только в пустой список.
"""

import ast
import inspect
import re
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from wiki import cities as wiki_cities  # noqa: E402
from wiki import directory  # noqa: E402
from wiki import dispatchers as wiki_dispatchers  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import registration  # noqa: E402
from wiki import routes_ai  # noqa: E402,F401 — источник роута читают тесты
from wiki import routes_articles  # noqa: E402,F401
from wiki import schema as wiki_schema  # noqa: E402
from wiki.ai import answer as ai_answer  # noqa: E402

SPACE = 11

# Диспетчерские кабинета Fleet на 28.10.2026 (кэш «Рассылок»): город кабинета
# и названия. Только названия парков и городов — ни id, ни данных водителей.
FLEET = (
    ('Актау', ('iTaxi', 'Адал (Честный)', 'Аманат', 'Каспий',)),
    ('Актобе', ('Jana Taxi', 'iTaxi', 'Аманат', 'ДЕПАРТАМЕНТ ТАКСИ',)),
    ('Алматы', ('Anytime', 'EKI DONGELEK', 'Global', 'Jana Taxi', 'QAZAQ', 'Salam Taxi',
                'Salam Taxi (доставка)', 'iTaxi', 'iTaxi (Доставка)', 'iTaxi 2', 'iTaxi 3',
                'iTaxi курьер', 'Бизнес Партнёр', 'Департамент (Доставка)', 'Департамент Такси',
                'Достойный Алматы', 'Жана Межгород', 'Ноль Такси', 'Самозанятый Партнер',
                'Стабильный', 'Такси 24', 'Халык', 'Честный', 'Честный Каскелен',)),
    ('Астана', ('Global', 'QAZAQ', 'Tenge Taxi', 'iPartner', 'iTaxi', 'iTaxi (Доставка)',
                'iTaxi 2', 'Бизнес Партнер', 'Департамент Такси', 'Достойный', 'Такси 24',
                'Честный',)),
    ('Атырау', ('Jana Taxi', 'iTaxi', 'Адал',)),
    ('Караганда', ('iTaxi', 'Аманат', 'Честный', 'Честный Темиртау',)),
    ('Кокшетау', ('iTaxi', 'Аманат', 'Департамент Такси',)),
    ('Костанай', ('iTaxi', 'Аманат', 'Департамент такси',)),
    ('Кызылорда', ('iTaxi', 'Аманат', 'Честный',)),
    ('Павлодар', ('iTaxi', 'Аманат', 'ДЕПАРТАМЕНТ ТАКСИ', 'Самозанятый',)),
    ('Петропавловск', ('iTaxi', 'Аманат', 'Департамент такси',)),
    ('Семей', ('iTaxi', 'Адал', 'Аманат',)),
    ('Талдыкорган', ('iTaxi', 'Аманат', 'Честный',)),
    ('Тараз', ('Jana Taxi', 'iTaxi', 'Аманат', 'Департамент Такси',)),
    ('Туркестан', ('iTaxi',)),
    ('Уральск', ('iTaxi', 'Аманат',)),
    ('Усть-Каменогорск', ('iTaxi', 'Усть-Каменогорск',)),
    ('Шымкент', ('Global', 'iTaxi', 'iTaxi (Доставка)', 'iTaxi VIP', 'Адал', 'Бизнес Партнер',
                 'Достойный',)),
)

# Парки вкладки «Таксопарки» в порядке вкладки и города «Городов» (прод, 29.10.2026).
PARKS = [{'id': index + 1, 'name': name} for index, name in enumerate((
    'iTaxi', 'Taxi24', 'Департамент', 'Честный', 'Global', 'iPartner', 'Бизнес Партнер',
    'Халык', 'Qazaq', 'Аманат', 'Стабильный', 'Jana такси', '2dongelek', 'Тенге Такси',
    'Salam такси', 'Ноль такси', 'iTaxi VIP', 'Hokage (Wolt)', 'Центр Регистраций (Регионы)'))]
PARK = {park['name']: park['id'] for park in PARKS}

CITY_NAMES = ('Аксай', 'Актау', 'Актобе', 'Алматы', 'Астана', 'Атырау', 'Жанаозен', 'Жезказган',
              'Караганда', 'Кентау', 'Костанай', 'Кызылорда', 'Павлодар', 'Петропавловск',
              'Рудный', 'Семей', 'Талдыкорган', 'Тараз', 'Темиртау', 'Туркестан', 'Уральск',
              'Усть-Каменогорск', 'Шымкент', 'Экибастуз', 'Кокшетау')

# Как операторы пишут город: «из Рудного», «в Рудном».
CITY_CASES = {
    'Аксай': ('Аксая', 'Аксае'), 'Актау': ('Актау', 'Актау'), 'Актобе': ('Актобе', 'Актобе'),
    'Алматы': ('Алматы', 'Алматы'), 'Астана': ('Астаны', 'Астане'), 'Атырау': ('Атырау', 'Атырау'),
    'Жанаозен': ('Жанаозена', 'Жанаозене'), 'Жезказган': ('Жезказгана', 'Жезказгане'),
    'Караганда': ('Караганды', 'Караганде'), 'Кентау': ('Кентау', 'Кентау'),
    'Костанай': ('Костаная', 'Костанае'), 'Кызылорда': ('Кызылорды', 'Кызылорде'),
    'Павлодар': ('Павлодара', 'Павлодаре'), 'Петропавловск': ('Петропавловска', 'Петропавловске'),
    'Рудный': ('Рудного', 'Рудном'), 'Семей': ('Семея', 'Семее'),
    'Талдыкорган': ('Талдыкоргана', 'Талдыкоргане'), 'Тараз': ('Тараза', 'Таразе'),
    'Темиртау': ('Темиртау', 'Темиртау'), 'Туркестан': ('Туркестана', 'Туркестане'),
    'Уральск': ('Уральска', 'Уральске'),
    'Усть-Каменогорск': ('Усть-Каменогорска', 'Усть-Каменогорске'),
    'Шымкент': ('Шымкента', 'Шымкенте'), 'Экибастуз': ('Экибастуза', 'Экибастузе'),
    'Кокшетау': ('Кокшетау', 'Кокшетау'),
}


def _cities(registration_rules=wiki_cities.DEFAULT_REGISTRATION):
    cities = [{'id': index + 1, 'name': name, 'registration_city_ids': []}
              for index, name in enumerate(CITY_NAMES)]
    by_name = {city['name']: city for city in cities}
    for name, targets in registration_rules:
        by_name[name]['registration_city_ids'] = [by_name[target]['id'] for target in targets]
    return cities


def _dispatchers(cities):
    found = []
    for fleet_city, names in FLEET:
        for name in names:
            found.append({
                'id': len(found) + 1, 'name': name, 'fleet_city': fleet_city,
                'park_id': wiki_dispatchers.auto_park(name, PARKS),
                'city_id': wiki_dispatchers.auto_city(name, fleet_city, cities),
                'status': 'active'})
    return found


def _catalog(registration_rules=wiki_cities.DEFAULT_REGISTRATION):
    cities = _cities(registration_rules)
    return registration.Catalog(cities=cities, parks=PARKS, dispatchers=_dispatchers(cities))


CATALOG = _catalog()


def _city(name):
    return CATALOG.city(name)


def _rows(question, catalog=CATALOG, search_query=None):
    return registration.build_ai_rows(registration.analyze(question, search_query), catalog,
                                      space_id=SPACE)


# ── 1. Привязка диспетчерских ────────────────────────────────────────────────

class DispatcherLinkTest(unittest.TestCase):

    def test_every_live_dispatcher_finds_its_park(self):
        """Разбор на живых 89 названиях: 81 с парком, 8 без него (своих
        карточек в «Таксопарках» у них нет)."""
        linked = {}
        for fleet_city, names in FLEET:
            for name in names:
                park_id = wiki_dispatchers.auto_park(name, PARKS)
                linked.setdefault(park_id, []).append(name)
        names_of = {park['id']: park['name'] for park in PARKS}
        counts = {names_of.get(park_id, None): len(items) for park_id, items in linked.items()}
        self.assertEqual(counts, {
            'iTaxi': 25, 'Аманат': 12, 'Честный': 11, 'Департамент': 9, None: 8,
            'Jana такси': 5, 'Global': 3, 'Бизнес Партнер': 3, 'Qazaq': 2, 'Salam такси': 2,
            'Taxi24': 2, '2dongelek': 1, 'iPartner': 1, 'iTaxi VIP': 1, 'Ноль такси': 1,
            'Стабильный': 1, 'Тенге Такси': 1, 'Халык': 1})
        self.assertEqual(sorted(linked[None]), sorted((
            'Каспий', 'Anytime', 'Достойный Алматы', 'Самозанятый Партнер', 'Достойный',
            'Самозанятый', 'Усть-Каменогорск', 'Достойный')))

    def test_owner_decision_kazakh_names(self):
        """Решение владельца 29.10.2026: «Адал» — Честный, «EKI DONGELEK» —
        2dongelek, «Жана Межгород» — Jana такси."""
        for name, park in (('Адал', 'Честный'), ('Адал (Честный)', 'Честный'),
                           ('EKI DONGELEK', '2dongelek'), ('Жана Межгород', 'Jana такси')):
            self.assertEqual(wiki_dispatchers.auto_park(name, PARKS), PARK[park], name)

    def test_spelling_differences_do_not_split_a_brand(self):
        """«Tenge Taxi» транслит пишет «тене», «Тенге» — «тенге»: сравнивать по
        одному алфавиту значило бы потерять парк."""
        for name, park in (('Tenge Taxi', 'Тенге Такси'), ('Такси 24', 'Taxi24'),
                           ('QAZAQ', 'Qazaq'), ('Бизнес Партнёр', 'Бизнес Партнер'),
                           ('ДЕПАРТАМЕНТ ТАКСИ', 'Департамент'), ('iTaxi VIP', 'iTaxi VIP'),
                           ('iTaxi (Доставка)', 'iTaxi'), ('Salam Taxi (доставка)', 'Salam такси')):
            self.assertEqual(wiki_dispatchers.auto_park(name, PARKS), PARK[park], name)

    def test_two_different_parks_are_not_a_guess(self):
        parks = [{'id': 1, 'name': 'Альфа'}, {'id': 2, 'name': 'Бета'}]
        self.assertIsNone(wiki_dispatchers.auto_park('Альфа Бета', parks))

    def test_city_named_in_the_title_wins_over_the_cabinet(self):
        cities = _cities()
        by_id = {city['id']: city['name'] for city in cities}
        for name, fleet_city, expected in (
                ('Честный Темиртау', 'Караганда', 'Темиртау'),
                ('Достойный Алматы', 'Алматы', 'Алматы'),
                ('Честный Каскелен', 'Алматы', 'Алматы'),       # Каскелена в «Городах» нет
                ('Усть-Каменогорск', 'Усть-Каменогорск', 'Усть-Каменогорск'),
                ('iTaxi', 'Петропавл', 'Петропавловск')):        # разговорная форма
            self.assertEqual(by_id.get(wiki_dispatchers.auto_city(name, fleet_city, cities)),
                             expected, name)
        self.assertIsNone(wiki_dispatchers.auto_city('iTaxi', 'Балхаш', cities))

    def test_brand_without_park_is_its_name_without_cities(self):
        self.assertEqual(wiki_dispatchers.base_name('Достойный Алматы', CITY_NAMES), 'Достойный')
        self.assertEqual(wiki_dispatchers.base_name('Усть-Каменогорск', CITY_NAMES),
                         'Усть-Каменогорск')
        self.assertTrue(wiki_dispatchers.is_generic('Самозанятый Партнер'))
        self.assertTrue(wiki_dispatchers.is_generic('Самозанятый'))
        self.assertFalse(wiki_dispatchers.is_generic('Достойный'))


# ── 2. Правило ───────────────────────────────────────────────────────────────

def _answer(city, park):
    found = CATALOG.resolve(_city(city), 'p:%d' % PARK[park])
    if not found:
        return None
    return found['city']['name'], [d['name'] for d in found['dispatchers']]


class ResolveTest(unittest.TestCase):

    def test_owner_example_rudny(self):
        self.assertEqual(_answer('Рудный', 'iTaxi'), ('Костанай', ['iTaxi']))

    def test_satellites_by_region(self):
        self.assertEqual(_answer('Темиртау', 'iTaxi'), ('Караганда', ['iTaxi']))
        self.assertEqual(_answer('Экибастуз', 'Департамент'), ('Павлодар', ['ДЕПАРТАМЕНТ ТАКСИ']))
        self.assertEqual(_answer('Жанаозен', 'Честный'), ('Актау', ['Адал (Честный)']))
        self.assertEqual(_answer('Аксай', 'Аманат'), ('Уральск', ['Аманат']))
        self.assertEqual(_answer('Жезказган', 'Честный'), ('Караганда', ['Честный']))

    def test_own_dispatcher_of_the_city_beats_the_region(self):
        """«Честный Темиртау» — своя диспетчерская Темиртау, хоть в кабинете и
        Караганда; а водителю Караганды она не предлагается."""
        self.assertEqual(_answer('Темиртау', 'Честный'), ('Темиртау', ['Честный Темиртау']))
        self.assertEqual(_answer('Караганда', 'Честный'), ('Караганда', ['Честный']))

    def test_cities_are_tried_in_order(self):
        """Кентау: Туркестан, затем Шымкент — у iTaxi в Туркестане есть, у
        Адала только в Шымкенте."""
        self.assertEqual(_answer('Кентау', 'iTaxi'), ('Туркестан', ['iTaxi']))
        self.assertEqual(_answer('Кентау', 'Честный'), ('Шымкент', ['Адал']))

    def test_nearest_dispatcher_of_the_same_park(self):
        """Владелец 29.10.2026: «водитель Jana из Рудного, в Костанае Jana нет —
        предлагать ближайшую диспу жана такси». Ни в городе, ни по списку —
        ближайший по прямой город, где у ЭТОГО парка диспетчерская есть."""
        self.assertEqual(_answer('Рудный', 'Jana такси'), ('Актобе', ['Jana Taxi']))
        self.assertEqual(_answer('Кентау', 'Аманат'), ('Тараз', ['Аманат']))
        found = CATALOG.resolve(_city('Рудный'), 'p:%d' % PARK['Jana такси'])
        self.assertTrue(found['nearest'])
        self.assertEqual(found['km'], 510)
        # Настроенный список сильнее расстояния: Жезказгану ближе Кызылорда,
        # но владелец решил «Караганда».
        self.assertEqual(_answer('Жезказган', 'iTaxi'), ('Караганда', ['iTaxi']))
        # Парка без единой диспетчерской «ближайшая» не спасает.
        self.assertIsNone(_answer('Рудный', 'Hokage (Wolt)'))

    def test_distance_twin_matches_the_map(self):
        """Координаты — двойник CITY_COORDS схемы «Городов»: разойдутся — и
        «ближайшая» в помощнике перестанет совпадать с точками на карте."""
        source = (ROOT / 'src/components/wiki/kazakhstanMap.js').read_text(encoding='utf-8')
        block = source[source.index('export const CITY_COORDS = {'):]
        block = block[:block.index('};')]
        coords = {name: (float(lat), float(lon)) for name, lat, lon in
                  re.findall(r"'([^']+)':\s*\[([\d.]+),\s*([\d.]+)\]", block)}
        self.assertEqual(coords, registration.CITY_COORDS)
        self.assertAlmostEqual(registration.distance_km('Рудный', 'Костанай'), 43, delta=3)
        self.assertIsNone(registration.distance_km('Рудный', 'Нет такого'))
        for name in CITY_NAMES:
            self.assertIn(name, registration.CITY_COORDS, name)

    def test_main_dispatcher_first_parentheses_last(self):
        self.assertEqual(_answer('Алматы', 'iTaxi')[1],
                         ['iTaxi', 'iTaxi 2', 'iTaxi 3', 'iTaxi курьер', 'iTaxi (Доставка)'])

    def test_block_of_a_satellite(self):
        block = CATALOG.block(_city('Рудный'))
        self.assertEqual(block['state'], 'elsewhere')
        self.assertEqual([target['name'] for target in block['targets']], ['Костанай'])
        self.assertEqual([(entry['name'], entry['city']['name'], entry['own'])
                          for entry in block['parks'] if not entry['nearest']],
                         [('iTaxi', 'Костанай', False), ('Департамент', 'Костанай', False),
                          ('Аманат', 'Костанай', False)])
        # Остальные парки вики — ближайшей диспетчерской, отдельно от ответа
        # города; бренды без парка («Каспий») в ближайшие не идут — шум.
        nearest = {entry['name']: (entry['city']['name'], entry['km'])
                   for entry in block['parks'] if entry['nearest']}
        self.assertEqual(nearest['Jana такси'], ('Актобе', 510))
        self.assertNotIn('Каспий', nearest)
        self.assertNotIn('Hokage (Wolt)', nearest)           # диспетчерских нет вовсе
        self.assertEqual(block['absent'], [])
        self.assertEqual(registration.other_cities(block), ['Костанай'])

    def test_nearest_does_not_change_the_answer_of_the_city(self):
        """Jana в Актобе — запасной путь, а не ответ Костаная: город со своими
        диспетчерскими остаётся «своим», смешанный — смешанным."""
        self.assertEqual(CATALOG.block(_city('Костанай'))['state'], 'own')
        self.assertEqual(CATALOG.block(_city('Темиртау'))['state'], 'mixed')
        self.assertEqual(registration.other_cities(CATALOG.block(_city('Темиртау'))), ['Караганда'])

    def test_every_city_and_every_park_follow_the_rule(self):
        """Все 25 городов × все бренды (владелец 29.10.2026: «проверил не только
        на жана и на других городах?»): только диспетчерские названного парка;
        своя — первой, затем города списка по порядку, затем ближайшая."""
        for city in CATALOG.cities:
            targets = [target['id'] for target in CATALOG.targets(city)]
            for key, brand in CATALOG.brands.items():
                label = '%s × %s' % (city['name'], brand['name'])
                pool = CATALOG.by_brand.get(key) or []
                found = CATALOG.resolve(city, key)
                if not pool:
                    self.assertIsNone(found, label)
                    continue
                self.assertTrue(found['dispatchers'], label)
                for dispatcher in found['dispatchers']:
                    self.assertIn(dispatcher, pool, label)
                    self.assertEqual(dispatcher['city_id'], found['city']['id'], label)
                served = {d['city_id'] for d in pool if d.get('city_id')}
                expected = city['id'] if city['id'] in served else next(
                    (target for target in targets if target in served), None)
                if expected is not None:
                    self.assertEqual((found['city']['id'], bool(found.get('nearest'))),
                                     (expected, False), label)
                    continue
                self.assertTrue(found['nearest'], label)
                km = [registration.distance_km(city['name'], CATALOG.cities_by_id[other]['name'])
                      for other in served]
                self.assertAlmostEqual(
                    registration.distance_km(city['name'], found['city']['name']), min(km),
                    msg=label)

    def test_block_of_a_hub_names_who_registers_here(self):
        block = CATALOG.block(_city('Караганда'))
        self.assertEqual(block['state'], 'own')
        self.assertEqual(block['own_count'], 3)
        self.assertEqual(sorted(feeder['name'] for feeder in block['feeders']),
                         ['Жезказган', 'Темиртау'])

    def test_mixed_city_does_not_claim_its_own_dispatchers_for_everyone(self):
        """Темиртау: своя диспетчерская только у Честного, iTaxi и Аманат — в
        Караганде. «Регистрировать в Темиртау» на вопрос без парка было бы
        неправдой для двух парков из трёх (нашёл стенд 29.10.2026)."""
        block = CATALOG.block(_city('Темиртау'))
        self.assertEqual(block['state'], 'mixed')
        self.assertEqual(registration.other_cities(block), ['Караганда'])
        text = registration.city_text({'city': _city('Темиртау'), 'block': block})
        self.assertIn('зависит от парка', text)
        self.assertIn('Парк Честный — регистрировать в городе Темиртау, диспетчерская '
                      '«Честный Темиртау» (в кабинете Fleet — в списке города Караганда).', text)
        # Условие, а не «все в Караганду»: у Jana там диспетчерской нет, и Gemini
        # на прежнем тексте отвечал «остальных — в Караганду» (замер 29.10.2026).
        self.assertIn('Остальных парков — в городе Караганда, если у парка там есть '
                      'диспетчерская', text)
        self.assertNotIn('регистрировать в городе Темиртау.', text)
        self.assertEqual(CATALOG.block(_city('Караганда'))['state'], 'own')

    def test_city_fragment_lists_no_parks(self):
        """Без парка во фрагменте только город: со списком парков Gemini
        перечислял их таблицей — «другие таксопарки предлагать не стоит»."""
        for question, forbidden in (('куда зарегать водителя с экибастуза', ('Аманат', 'iTaxi')),
                                    ('регистрация костанай', ('Аманат', 'Департамент')),
                                    ('кентау регистрация', ('Global', 'Адал'))):
            text = _rows(question)[0]['text']
            self.assertIn('Диспетчерская зависит от парка водителя.', text, question)
            for name in forbidden:
                self.assertNotIn(name, text, question)

    def test_block_of_a_city_without_rule(self):
        catalog = _catalog(registration_rules=())
        block = catalog.block(catalog.city('Рудный'))
        self.assertEqual(block['state'], 'unset')
        # Правила нет — у каждого парка остаётся только ближайшая диспетчерская.
        self.assertEqual([entry for entry in block['parks'] if not entry['nearest']], [])
        itaxi = next(entry for entry in block['parks'] if entry['name'] == 'iTaxi')
        self.assertEqual((itaxi['city']['name'], itaxi['km']), ('Костанай', 40))

    def test_block_shows_the_cabinet_city_when_it_differs(self):
        entry = next(entry for entry in CATALOG.block(_city('Темиртау'))['parks']
                     if entry['name'] == 'Честный')
        self.assertEqual(entry['dispatchers'][0]['fleet_city_other'], 'Караганда')

    def test_no_dispatchers_no_block(self):
        catalog = registration.Catalog(cities=_cities(), parks=PARKS, dispatchers=[])
        self.assertIsNone(catalog.block(catalog.city('Рудный')))


# ── 3. Вопрос ────────────────────────────────────────────────────────────────

class QuestionTest(unittest.TestCase):

    def assertAnswers(self, question, heading, *parts, **kwargs):
        rows = _rows(question, **kwargs)
        self.assertTrue(rows, question)
        self.assertEqual(rows[0]['heading_path'], heading, question)
        for part in parts:
            self.assertIn(part, rows[0]['text'], question)
        return rows

    def test_owner_question_verbatim(self):
        rows = self.assertAnswers('куда зарегать водителя айтакси если он обращается с рудного',
                                  'Рудный › Регистрация водителей', 'парк iTaxi',
                                  'регистрировать в городе Костанай', '«iTaxi»')
        self.assertEqual(len(rows), 1)
        # Другие парки не предлагаются (владелец): во фрагменте их нет вовсе.
        self.assertNotIn('Аманат', rows[0]['text'])
        self.assertNotIn('Департамент', rows[0]['text'])

    def test_other_phrasings(self):
        self.assertAnswers('водитель с Рудного хочет зарегистрироваться в iTaxi',
                           'Рудный › Регистрация водителей', 'Костанай')
        self.assertAnswers('в какой город регистрировать водителя из Темиртау в Честный',
                           'Темиртау › Регистрация водителей', '«Честный Темиртау»',
                           'в кабинете Fleet — в списке города Караганда')
        self.assertAnswers('куда регать водителя из Кентау, он хочет в Адал',
                           'Кентау › Регистрация водителей', 'парк Честный',
                           'в городе Шымкент', '«Адал»')
        self.assertAnswers('куда оформить водителя iTaxi из Жанаозена',
                           'Жанаозен › Регистрация водителей', 'в городе Актау')
        self.assertAnswers('рудный регистрация', 'Рудный › Регистрация водителей',
                           'своих диспетчерских в городе Рудный нет', 'в городе Костанай')

    def test_city_without_park_names_the_city_and_no_single_park(self):
        rows = self.assertAnswers('куда зарегать водителя с экибастуза',
                                  'Экибастуз › Регистрация водителей',
                                  'регистрировать в городе Павлодар')
        self.assertTrue(rows[0]['text'].startswith(
            'Регистрация водителей из города Экибастуз: своих диспетчерских'))

    def test_chain_of_cities_in_words(self):
        self.assertAnswers('кентау регистрация', 'Кентау › Регистрация водителей',
                           'в городе Туркестан; если у парка нет диспетчерской в городе '
                           'Туркестан — в городе Шымкент')

    def test_nearest_dispatcher_is_offered_for_the_same_park(self):
        rows = self.assertAnswers('Jana из рудного куда регистрировать',
                                  'Рудный › Регистрация водителей',
                                  'ближайшая диспетчерская Jana такси — в городе Актобе, около 510 км '
                                  'по прямой: регистрировать там — диспетчерская «Jana Taxi»',
                                  'Диспетчерской Jana такси нет ни в городе Рудный, ни в городе Костанай')
        self.assertNotIn('доступн', rows[0]['text'])
        self.assertNotIn('iTaxi', rows[0]['text'])           # другой парк не предлагается
        self.assertAnswers('регистрация hokage рудный', 'Рудный › Регистрация водителей',
                           'у парка Hokage (Wolt) нет ни одной диспетчерской в кабинете Fleet')

    def test_owner_question_about_jana_verbatim(self):
        """Вопрос владельца 29.10.2026 дословно: помощник ответил «Костанай»,
        потому что «жана такси» не узнавался как Jana, и вопрос шёл без парка."""
        rows = self.assertAnswers(
            'водитель обращается с жана такси а водитель сам в рудном и ничего сделать не может, '
            'в какой из диспетчерских зарегать его',
            'Рудный › Регистрация водителей', 'парк Jana такси', 'в городе Актобе', '«Jana Taxi»')
        self.assertEqual(len(rows), 1)
        self.assertNotIn('регистрировать в городе Костанай', rows[0]['text'])

    def test_jana_spelled_in_cyrillic(self):
        """«Жана такси» — Jana, и в регистрации, и в справочнике офисов. Голое
        «жана» — казахское «жаңа» («новый»), парком не считается."""
        jana = {'id': 12, 'name': 'Jana такси'}
        for text in ('жана такси', 'адрес жана такси шымкент', 'жана таксапарк алматы',
                     'жана taxi'):
            self.assertEqual(directory.match_parks({'variants': directory._variants(text)}, [jana]),
                             [jana], text)
        for text in ('жана жургизуши', 'жаңа жүргізуші тіркеу', 'жана'):
            self.assertEqual(directory.match_parks({'variants': directory._variants(text)}, [jana]),
                             [], text)
        follow = 'куда зарегать водителя айтакси с рудного а жана такси?'
        rows = _rows('а жана такси?', search_query=follow)
        self.assertIn('парк Jana такси', rows[0]['text'])

    def test_every_city_with_every_park_spelling(self):
        """Все города в падежах × написания парков из журнала операторов:
        город и парк узнаются. Не узнанный парк — это ответ «город без парка»,
        как с «жана такси»: Костанай вместо диспетчерской парка."""
        spellings = (
            ('iTaxi', 'айтакси'), ('Taxi24', 'такси 24'), ('Департамент', 'департамент такси'),
            ('Честный', 'адал'), ('Global', 'глобал'), ('iPartner', 'ай партнер'),
            ('Бизнес Партнер', 'бизнес партнер'), ('Халык', 'халык'), ('Qazaq', 'казак такси'),
            ('Аманат', 'аманат'), ('Стабильный', 'стабильный'), ('Jana такси', 'жана такси'),
            ('2dongelek', 'еки донгелек'), ('2dongelek', 'екі дөңгелек'),
            ('Тенге Такси', 'тенге такси'), ('Salam такси', 'салам такси'),
            ('Ноль такси', '0 такси'), ('Ноль такси', 'нольтакси'), ('iTaxi VIP', 'айтакси вип'),
            ('Hokage (Wolt)', 'wolt'))
        templates = ('куда зарегать водителя {park} из {gen}',
                     'водитель обращается с {park}, а сам в {loc}, куда его регистрировать')
        for index, (name, (gen, loc)) in enumerate(sorted(CITY_CASES.items())):
            for park, spelling in spellings:
                question = templates[index % 2].format(park=spelling, gen=gen, loc=loc)
                decisions = registration.plan(registration.analyze(question), CATALOG)
                self.assertEqual([(d['city']['name'], d['brand'] and d['brand']['key'])
                                  for d in decisions], [(name, 'p:%d' % PARK[park])], question)

    def test_greetings_money_and_language_are_not_parks(self):
        """Журнал помощника 29.10.2026: «Саламатсыз ба» читалось Salam такси,
        «2000 тенге» — Тенге Такси, «қазақша» — Qazaq. С «ближайшей» такой
        парк отправлял водителя из Рудного в Алматы за 1490 км."""
        for question in ('Саламатсыз ба, водитель из рудного куда регистрировать?',
                         'Салам! куда зарегать водителя с рудного',
                         'водитель из рудного, на балансе 2000 тенге, куда регистрировать?',
                         'қазақша жауап беріңізші, водитель с рудного куда регать',
                         'Қазақстан бойынша Рудныйдан келген жүргізушіні қайда тіркеу керек',
                         'комиссия ноль процентов, водитель из рудного куда регистрировать',
                         'глобальный сбой, куда регистрировать водителя из рудного',
                         'водитель из рудного, карта халык банк, куда регистрировать',
                         'цепочка заказов, водитель из рудного куда регистрировать'):
            rows = self.assertAnswers(question, 'Рудный › Регистрация водителей',
                                      'регистрировать в городе Костанай')
            self.assertNotIn(', парк ', rows[0]['text'], question)

    def test_kazakh_name_of_karaganda(self):
        self.assertAnswers('Қарағандыдан келген жүргізушіні iTaxi-ге қай қалада тіркеу керек?',
                           'Караганда › Регистрация водителей', 'парк iTaxi',
                           'регистрировать в городе Караганда')

    def test_named_park_spelled_by_its_dispatcher(self):
        """«Адал» и «EKI DONGELEK» — так их называют операторы, и так названы
        диспетчерские: имя диспетчерской — второе имя её парка."""
        self.assertAnswers('куда зарегать водителя eki dongelek из алматы',
                           'Алматы › Регистрация водителей', 'парк 2dongelek',
                           '«EKI DONGELEK»')
        # Второе имя — в самом фрагменте: без него GLM на «хочет в Адал»
        # переспрашивал, парк это или диспетчерская (замер 29.10.2026).
        self.assertAnswers('куда регать водителя из Кентау, он хочет в Адал',
                           'Кентау › Регистрация водителей', 'парк Честный (он же «Адал»)')
        self.assertEqual(CATALOG.aliases('p:%d' % PARK['iTaxi']), [])
        self.assertEqual(CATALOG.aliases('p:%d' % PARK['Jana такси']), ['Жана Межгород'])

    def test_registration_elsewhere_stays_silent(self):
        """Формулировки из журнала помощника 10.08–29.10.2026, где слово есть, а
        вопроса «куда» нет."""
        for question in (
                'например если водитель зарегистрирован по городу Шымкент но работает в Астане, '
                'он будет участвовать в группе по шымкенту или нет?',
                'после регистрации в Алматы первые две недели без комиссии?',
                'Что делать, если водитель из Рудного не может пройти регистрацию как СМЗ?',
                'как зарегать машину водителя в Костанае',
                'ссылка на регистрацию в акции Шымкент',
                'если водитель сделал перерегистрацию в Астане то у него заказы сохранятся ?',
                'как зарегистрировать ИП в Алматы',
                'как оформить возврат водителю iTaxi в Алматы',
                'как зарегать водителя',                        # города нет — сказать нечего
                'сколько стоит регистрация водителя в алматы'):
            self.assertEqual(_rows(question), [], question)

    def test_self_employed_driver_is_still_a_where_question(self):
        self.assertAnswers('куда регистрировать самозанятого водителя из Рудного',
                           'Рудный › Регистрация водителей', 'в городе Костанай')

    def test_generic_brand_name_is_not_a_park(self):
        """Диспетчерская «Самозанятый» в Павлодаре — не ответ на вопрос о
        самозанятом водителе из Рудного."""
        for question in ('куда регистрировать самозанятого водителя из Рудного',
                         'куда зарегать самозанятый водитель из экибастуза'):
            rows = _rows(question)
            self.assertTrue(rows, question)
            self.assertNotIn('парк Самозанятый', rows[0]['text'], question)

    def test_dispatcher_named_after_a_city_is_not_a_park_in_the_question(self):
        """Диспетчерская «Усть-Каменогорск» без парка: её имя — город. Вопрос
        без парка не должен превращаться в вопрос про неё."""
        rows = _rows('куда зарегать водителя из Усть-Каменогорска')
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['text'].startswith(
            'Регистрация водителей из города Усть-Каменогорск: регистрировать'), rows[0]['text'])
        self.assertNotIn('парк Усть-Каменогорск', rows[0]['text'])

    def test_origin_city_of_two(self):
        """«С Рудного … в Астане?» — город водителя тот, что с «с/из»."""
        rows = _rows('водитель с Рудного, можно ли зарегать его в Астане в iTaxi?')
        self.assertEqual([row['heading_path'] for row in rows], ['Рудный › Регистрация водителей'])

    def test_follow_up_keeps_the_topic(self):
        context = 'куда зарегать водителя айтакси если он обращается с рудного а аманат?'
        rows = _rows('а аманат?', search_query=context)
        self.assertEqual(len(rows), 1)
        self.assertIn('парк Аманат', rows[0]['text'])
        context = 'куда зарегать водителя айтакси если он обращается с рудного а из темиртау?'
        rows = _rows('а из темиртау?', search_query=context)
        self.assertIn('Регистрация водителей из города Темиртау, парк iTaxi', rows[0]['text'])

    def test_follow_up_with_its_own_question_does_not_carry(self):
        context = ('куда зарегать водителя айтакси если он обращается с рудного '
                   'а адрес офиса в Костанае?')
        self.assertEqual(_rows('а адрес офиса в Костанае?', search_query=context), [])

    def test_follow_up_must_be_a_city_or_a_park_and_nothing_else(self):
        """Разбор 29.10.2026: после вопроса о Рудном «а после регистрации
        бонус?», «спасибо» и «а лимонопад в Алматы?» приносили строку
        «регистрировать в …», и правило 13 велело начать с неё ответ."""
        first = 'куда зарегать водителя айтакси если он обращается с рудного'
        for follow in ('а после регистрации бонус?', 'спасибо', 'а лимонопад в Алматы?',
                       'а приветственный бонус?', 'как связаться с поддержкой?'):
            self.assertEqual(_rows(follow, search_query='%s %s' % (first, follow)), [], follow)
        rows = _rows('а jana?', search_query='%s а jana?' % first)
        self.assertIn('парк Jana такси', rows[0]['text'])

    def test_short_query_with_its_own_topic_is_not_a_registration_question(self):
        for question in ('лимонопад регистрация алматы', 'регистрация байга алматы',
                         'регистрация на байгу алматы'):
            self.assertEqual(_rows(question), [], question)
        self.assertTrue(_rows('регистрация водителей рудный'))

    def test_typos_people_actually_write(self):
        for question in ('куда заригать водителя с рудного', 'куда регестрировать водителя с рудного',
                         'регестрация рудный', 'куда заригистрировать водителя с рудного',
                         'куда зарегестрировать водителя с рудного'):
            rows = _rows(question)
            self.assertTrue(rows, question)
            self.assertIn('в городе Костанай', rows[0]['text'], question)
        # Та же опечатка в «уже зарегистрирован» — состояние, а не вопрос, куда.
        for question in ('водитель из Рудного уже зарегестрирован, где получить брендинг',
                         'водитель с Рудного зарегестрировался вчера, когда придет бонус',
                         'водитель из Рудного регистрируется уже неделю, почему не активен',
                         # «куда» и парк — сильный повод: здесь спасает только
                         # исключение, проверка посторонних слов не включается.
                         'водитель из Рудного уже зарегестрирован в iTaxi, куда ему за брендингом?'):
            self.assertEqual(_rows(question), [], question)

    def test_kazakh_case_endings_of_the_city(self):
        """«Рудныйдан» — «из Рудного»: падеж срезается только здесь, общий
        разбор городов (офисы, классификатор) не трогается."""
        for question, city in (('Рудныйдан келген жүргізушіні қайда тіркейміз', 'Рудный'),
                               ('Темиртаудан жүргізуші қай қалаға тіркеледі', 'Темиртау'),
                               ('Екібастұздан жүргізушіні қайда тіркеу керек', 'Экибастуз')):
            rows = _rows(question)
            self.assertTrue(rows, question)
            self.assertEqual(rows[0]['heading_path'], '%s › Регистрация водителей' % city)
        self.assertEqual(directory.find_cities({'variants': ['рудныйдан']}, ['Рудный']), [])
        found, origin = registration._kazakh_cases(['водитель костанайдан и в шымкентке'],
                                                   ['Костанай', 'Шымкент'])
        self.assertEqual(found, ['водитель костанай и в шымкент'])
        self.assertEqual(origin, ['Костанай'])

    def test_rudny_is_declined_like_an_adjective(self):
        names = ['Рудный', 'Семей', 'Аксай']
        for text, expected in (('с рудного', 'Рудный'), ('в рудном', 'Рудный'),
                               ('к рудному', 'Рудный'), ('из семея', 'Семей'),
                               ('в аксае', 'Аксай')):
            self.assertEqual(directory.find_cities({'variants': [text]}, names), [expected], text)


# ── 4. Помощник ──────────────────────────────────────────────────────────────

class AssistantRowsTest(unittest.TestCase):

    def test_row_contract_of_the_retriever(self):
        row = _rows('куда зарегать водителя айтакси с рудного')[0]
        self.assertLessEqual(row['chunk_id'], -3001)          # свой диапазон номеров
        self.assertGreater(row['chunk_id'], -4000)
        self.assertIsNone(row['article_id'])
        self.assertEqual(row['found_by'], [directory.DIRECTORY_BRANCH])
        self.assertTrue(row['directory_hit'])
        self.assertEqual((row['source_kind'], row['tab'], row['title']),
                         ('registration', 'cities', 'Города'))
        self.assertEqual((row['ref_id'], row['ref_city'], row['space_id']),
                         (_city('Рудный')['id'], 'Рудный', SPACE))

    def test_numbers_of_dispatcher_names_are_in_the_text(self):
        """Проверка чисел смотрит в текст фрагмента: «Такси 24» в ответе должен
        найтись в нём буквально."""
        row = _rows('куда зарегать водителя такси 24 из астаны')[0]
        self.assertIn('«Такси 24»', row['text'])
        self.assertEqual(ai_answer.ungrounded_numbers(
            'Регистрируйте в Астане, диспетчерская «Такси 24».', [row]), [])

    def test_no_until_before_dates(self):
        """«до/по» перед датой фрагмент не пишет: так помечаются сроки
        (wiki/ai/currency.py)."""
        for row in _rows('кентау регистрация') + _rows('куда зарегать водителя айтакси с рудного'):
            self.assertIsNone(re.search(r'\b(?:до|по)\s+\d', row['text']))

    def test_three_cities_at_most(self):
        rows = _rows('регистрация рудный темиртау экибастуз аксай')
        self.assertLessEqual(len(rows), registration.MAX_CITIES)


class PromptTest(unittest.TestCase):

    def test_rule_comes_only_with_registration_rows(self):
        rows = _rows('куда зарегать водителя айтакси с рудного')
        self.assertIn('13. РЕГИСТРАЦИЯ ВОДИТЕЛЕЙ', ai_answer.system_prompt(rows))
        self.assertNotIn('РЕГИСТРАЦИЯ ВОДИТЕЛЕЙ', ai_answer.system_prompt([]))
        prompt = ai_answer.system_prompt(rows)
        self.assertLess(prompt.index('13. РЕГИСТРАЦИЯ'), prompt.index('ФОРМАТ ОТВЕТА'))

    def test_rule_says_what_the_owner_asked(self):
        rule = ' '.join(ai_answer.REGISTRATION_RULE.split())
        self.assertIn('Другие парки и их диспетчерские НЕ', rule)
        self.assertIn('это ответ, а не отказ', rule)
        self.assertIn('БЛИЖАЙШУЮ диспетчерскую парка', rule)

    def test_directory_label_and_pipeline_gates(self):
        rows = _rows('куда зарегать водителя айтакси с рудного')
        prompt = ai_answer.build_user_prompt('куда зарегать', rows)
        self.assertIn('Справочник «Города», запись «Рудный › Регистрация водителей»', prompt)
        self.assertEqual(ai_answer.usable_chunks(rows), rows)
        self.assertEqual(ai_answer.should_clarify('куда регать рудный', rows), (False, None))

    def test_missing_dispatcher_answer_is_not_a_refusal(self):
        body = 'У Jana такси нет диспетчерской для водителей из Рудного — ни в Рудном, ни в Костанае.'
        self.assertFalse(ai_answer.is_refusal(body))
        self.assertFalse(ai_answer.admits_missing(body))

    def test_dispatcher_is_missing_in_refusal_words_is_still_an_answer(self):
        """Разбор 29.10.2026: «В представленных данных диспетчерской … нет» —
        отказ по словам, а по сути ответ справочника. Без защиты он терял
        источник и уходил супервайзеру (should_escalate)."""
        rows = _rows('куда зарегать водителя из Рудного в Jana')
        for body in ('В представленных данных диспетчерской Jana такси для Рудного нет.',
                     'В доступных данных у парка Jana такси диспетчерской для водителей из '
                     'Рудного нет.'):
            result = ai_answer.compose('куда зарегать водителя из Рудного в Jana', rows,
                                       lambda system, user, b=body, **_k: (
                                           b + '\nИСТОЧНИКИ: [1]', {}))
            self.assertEqual(result['kind'], 'answer', body)
            self.assertTrue(result['sources'], body)
            self.assertTrue(ai_answer.registration_answer(result), body)
        # Отказ без строк регистрации — по-прежнему отказ.
        refused = ai_answer.compose('что-то', [], lambda *a, **k: ('В доступных статьях этого нет.', {}))
        self.assertFalse(ai_answer.registration_answer(refused))

    def test_registration_answer_is_not_escalated(self):
        source = inspect.getsource(sys.modules['wiki.routes_ai'])
        self.assertIn('and not ai_answer.registration_answer(result)', source)


# ── 5. Поиск ─────────────────────────────────────────────────────────────────

class SearchItemsTest(unittest.TestCase):

    def test_item_opens_the_city_of_the_driver(self):
        analysis = registration.analyze('рудный регистрация itaxi')
        item = registration.search_items(analysis, CATALOG, space_id=SPACE)[0]
        self.assertEqual(item['kind'], 'registration')
        self.assertEqual(item['id'], item['ref_id'])
        self.assertEqual(item['id'], _city('Рудный')['id'])
        self.assertEqual((item['park'], item['target'], item['own']), ('iTaxi', 'Костанай', False))
        self.assertEqual([d['name'] for d in item['dispatchers']], ['iTaxi'])

    def test_city_item_without_park(self):
        item = registration.search_items(registration.analyze('регистрация рудный'), CATALOG,
                                         space_id=SPACE)[0]
        self.assertEqual((item['state'], item['targets']), ('elsewhere', ['Костанай']))
        # Подпись «Регистрируем здесь · N парков» — без ближайших чужих городов.
        item = registration.search_items(registration.analyze('регистрация костанай'), CATALOG,
                                         space_id=SPACE)[0]
        self.assertEqual(item['state'], 'own')
        self.assertNotIn('Jana такси', [d['name'] for d in item['dispatchers']])

    def test_park_item_names_the_nearest_city(self):
        item = registration.search_items(registration.analyze('регистрация jana рудный'), CATALOG,
                                         space_id=SPACE)[0]
        self.assertEqual((item['park'], item['target'], item['nearest'], item['km']),
                         ('Jana такси', 'Актобе', True, 510))

    def test_registration_failure_does_not_take_offices_down(self):
        """Разбор 29.10.2026: регистрация и справочник офисов шли под одним
        савпоинтом поиска, и сбой регистрации отнимал адрес офиса."""
        source = ' '.join(inspect.getsource(sys.modules['wiki.routes_articles']).split())
        self.assertIn("SAVEPOINT wiki_search_registration", source)
        self.assertIn("ROLLBACK TO SAVEPOINT wiki_search_registration", source)
        self.assertLess(source.index("RELEASE SAVEPOINT wiki_search_registration"),
                        source.index("cursor.execute('SAVEPOINT wiki_search_directory')"))

    def test_long_phrase_is_not_a_search_query(self):
        cursor = MagicMock()
        long = 'куда зарегать водителя айтакси если он обращается с рудного и хочет работать'
        self.assertEqual(registration.search(cursor, {}, long, requested_space=SPACE), [])
        cursor.execute.assert_not_called()


# ── 6. Граница пространства ──────────────────────────────────────────────────

SCOPED_TABLES = ('wiki_dispatchers', 'wiki_taxi_parks', 'wiki_cities', 'wiki_city_registration')


def _scoped_functions(module):
    tree = ast.parse(inspect.getsource(module))
    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=False)
        tables = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                if docstring is not None and inner.value == docstring:
                    continue
                tables.update(t for t in SCOPED_TABLES if t in inner.value)
            if isinstance(inner, ast.Name) and inner.id in ('_DISPATCHER_SELECT',
                                                           '_REGISTRATION_IDS_SQL'):
                tables.add('wiki_dispatchers')
        if tables:
            found[node.name] = sorted(tables)
    return found


class DispatcherSpaceGuardTest(unittest.TestCase):
    """У каждой функции, чей SQL трогает справочник, — именованный space_id."""

    def test_every_reader_and_writer_takes_the_space(self):
        missing = []
        for module in (wiki_dispatchers, wiki_cities):
            found = _scoped_functions(module)
            for name in found:
                if module is wiki_cities and name == 'due_cities':
                    continue      # ночной обход: пространство — часть ответа (test_wiki_cities)
                parameter = inspect.signature(getattr(module, name)).parameters.get('space_id')
                if parameter is None or parameter.kind != inspect.Parameter.KEYWORD_ONLY:
                    missing.append('%s.%s' % (module.__name__, name))
        self.assertGreaterEqual(len(_scoped_functions(wiki_dispatchers)), 7)
        self.assertEqual(missing, [])

    def test_registration_module_has_no_sql(self):
        """Правило — без SQL: база только через стражей пространства."""
        source = inspect.getsource(registration)
        self.assertIsNone(re.search(r'\b(?:SELECT|INSERT|UPDATE|DELETE)\b\s', source))
        self.assertNotIn('cursor.execute', source)


class _RecordingCursor:
    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def asked_space(self, value):
        for sql, params in self.calls:
            if 'space_id' not in sql:
                continue
            flat = params.values() if isinstance(params, dict) else (params or ())
            if value in list(flat):
                return True
        return False


class DispatcherSqlTest(unittest.TestCase):

    def test_reads_and_writes_carry_the_space(self):
        for call in (
                lambda c: wiki_dispatchers.list_dispatchers(c, space_id=SPACE),
                lambda c: wiki_dispatchers.get_dispatcher(c, 3, space_id=SPACE),
                lambda c: wiki_dispatchers.sync_state(c, space_id=SPACE),
                lambda c: wiki_dispatchers.update_dispatcher(c, 3, space_id=SPACE, park=(5, True)),
                lambda c: wiki_cities.registration_cities(c, space_id=SPACE),
                lambda c: wiki_cities.set_registration_cities(c, 3, [9, 5], space_id=SPACE)):
            cursor = _RecordingCursor()
            call(cursor)
            self.assertTrue(cursor.asked_space(SPACE), cursor.calls)

    def test_links_stay_inside_the_space(self):
        """Парк и город у диспетчерской — только живые и своего пространства:
        и при чтении (JOIN), и при записи (подзапрос)."""
        cursor = _RecordingCursor()
        wiki_dispatchers.list_dispatchers(cursor, space_id=SPACE)
        sql = cursor.calls[0][0]
        self.assertIn('p.space_id = d.space_id', sql)
        self.assertIn('c.space_id = d.space_id', sql)
        cursor = _RecordingCursor()
        wiki_dispatchers.update_dispatcher(cursor, 3, space_id=SPACE, park=(5, True),
                                           city=(None, True))
        sql, params = cursor.calls[0]
        self.assertIn("p.space_id = d.space_id AND p.status = 'active'", sql)
        self.assertIn("c.space_id = d.space_id AND c.status = 'active'", sql)
        self.assertEqual(params, [5, True, None, True, 3, SPACE])
        self.assertFalse(wiki_dispatchers.update_dispatcher(_RecordingCursor(), 3, space_id=SPACE))

    def test_registration_cities_stay_inside_the_space(self):
        cursor = _RecordingCursor()
        wiki_cities.set_registration_cities(cursor, 3, [9, 5], space_id=SPACE)
        delete, insert = cursor.calls[0][0], cursor.calls[1][0]
        self.assertIn('c.space_id = %s', delete)
        self.assertIn('t.space_id = %s', insert)
        self.assertIn('c.space_id = t.space_id', insert)
        self.assertIn('t.id <> c.id', insert)                # не сам себе город
        self.assertIn('WITH ORDINALITY', insert)             # порядок — порядок выбора
        self.assertEqual(cursor.calls[1][1], ([9, 5], 3, SPACE))
        cursor = _RecordingCursor()
        wiki_cities.set_registration_cities(cursor, 3, [], space_id=SPACE)
        self.assertEqual(len(cursor.calls), 1)

    def test_city_ids_are_cleaned(self):
        self.assertEqual(wiki_cities.clean_city_ids(['9', 5, 9, 'x', -1, None]), [9, 5])
        self.assertEqual(wiki_cities.clean_city_ids(None), [])
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_city_ids('9,5')
        with self.assertRaises(wiki_cities.CityFieldError):
            wiki_cities.clean_city_ids(list(range(1, 10)))

    def test_sync_goes_only_into_the_cabinet_space(self):
        cursor = _RecordingCursor(rows=[(SPACE,)])            # пространство по умолчанию — 11
        self.assertEqual(wiki_dispatchers.sync_from_fleet(cursor, space_id=12),
                         {'status': 'no_source'})
        self.assertFalse(any('wiki_dispatchers' in sql for sql, _ in cursor.calls))

    def test_empty_source_changes_nothing(self):
        """Кэш пуст только при смене аккаунта или сорвавшемся опросе — помечать
        по нему 89 диспетчерских ушедшими нельзя."""
        cursor = _RecordingCursor(rows=[(SPACE,), ('driver_mailing_parks',)])
        self.assertEqual(wiki_dispatchers.sync_from_fleet(cursor, space_id=SPACE),
                         {'status': 'empty_source'})
        self.assertFalse(any('wiki_dispatchers' in sql for sql, _ in cursor.calls))
        cursor = _RecordingCursor(rows=[(SPACE,), (None,)])   # таблицы «Рассылок» нет
        self.assertEqual(wiki_dispatchers.sync_from_fleet(cursor, space_id=SPACE),
                         {'status': 'no_source'})

    def test_sync_keeps_hand_made_links_and_marks_the_gone(self):
        source = inspect.getsource(wiki_dispatchers.sync_from_fleet)
        flat = ' '.join(source.split())
        self.assertIn('CASE WHEN d.park_manual THEN d.park_id ELSE EXCLUDED.park_id END', flat)
        self.assertIn('CASE WHEN d.city_manual THEN d.city_id ELSE EXCLUDED.city_id END', flat)
        self.assertIn("SET status = 'gone'", flat)
        self.assertIn('fleet_park_id <> ALL(%s)', flat)
        self.assertIn("AT TIME ZONE 'Asia/Almaty'", flat)      # время кабинета — по Алматы
        self.assertIn('ORDER BY park_id', ' '.join(
            inspect.getsource(wiki_dispatchers._source_rows).split()))


def _context():
    return {'user_id': 42, 'otp_role': 'sv', 'department_id': 560, 'direction_id': None,
            'headed_department_ids': [], 'group_ids': [], 'wiki_roles': [],
            'access_mode': 'auto'}


@unittest.skipIf(Flask is None, 'flask не установлен')
class DispatcherRouteTest(unittest.TestCase):

    def build(self, spaces, can_write=True):
        from wiki import access as wiki_access
        from wiki.routes import build_wiki_blueprint
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 0
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        context = _context()
        for module, name, value in (
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'spaces_for_user', lambda _c, _ctx, **_k: list(spaces)),
            (wiki_access, 'has_write_capability', lambda _caps: can_write),
        ):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)
        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _u, cursor=None: True,
            client_ip=lambda: '127.0.0.1'))
        app.config['TESTING'] = True
        return app.test_client(), cursor

    def test_foreign_space_is_not_found_on_every_door(self):
        client, _ = self.build([12])
        for method, url in (('get', '/api/wiki/dispatchers?space_id=11'),
                            ('post', '/api/wiki/dispatchers/sync?space_id=11'),
                            ('patch', '/api/wiki/dispatchers/1?space_id=11')):
            response = getattr(client, method)(url, json={'park_id': 1})
            self.assertEqual(response.status_code, 404, '%s %s' % (method, url))

    def test_reader_sees_the_list_but_cannot_change_it(self):
        client, _ = self.build([12], can_write=False)
        response = client.get('/api/wiki/dispatchers')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['can_manage'])
        self.assertEqual(client.post('/api/wiki/dispatchers/sync').status_code, 403)
        self.assertEqual(client.patch('/api/wiki/dispatchers/1', json={'park_id': 1}).status_code,
                         403)

    def test_unknown_dispatcher_and_bad_choice(self):
        client, cursor = self.build([12])
        self.assertEqual(client.patch('/api/wiki/dispatchers/1', json={'park_id': 1}).status_code,
                         404)
        row = (1, 'abc', 'Адал', 'Атырау', 4, 'Честный', False, 6, 'Атырау', False, 'active',
               None, None, None)
        cursor.fetchone.side_effect = [row] + [None] * 10
        response = client.patch('/api/wiki/dispatchers/1', json={'park_id': 'Честный'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse([c for c in cursor.execute.call_args_list
                          if 'UPDATE wiki_dispatchers' in str(c.args[0])])

    def test_hand_link_is_saved_and_logged(self):
        client, cursor = self.build([12])
        row = (1, 'abc', 'Адал', 'Атырау', None, None, False, 6, 'Атырау', False, 'active',
               None, None, None)
        cursor.fetchone.side_effect = [row, row] + [None] * 10
        response = client.patch('/api/wiki/dispatchers/1', json={'park_id': 4})
        self.assertEqual(response.status_code, 200, response.get_json())
        updates = [c for c in cursor.execute.call_args_list
                   if 'UPDATE wiki_dispatchers' in str(c.args[0])]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].args[1][:2], [4, True])
        logged = [c for c in cursor.execute.call_args_list if 'wiki_audit_log' in str(c.args[0])]
        self.assertTrue(any('dispatcher.update' in str(c) for c in logged))

    def test_city_patch_saves_registration_cities(self):
        client, cursor = self.build([12])
        row = [None] * len(wiki_cities._SUMMARY_KEYS) + [None]
        row[0], row[1], row[13] = 4, 'Рудный', 'active'
        cursor.fetchone.side_effect = [tuple(row)] + [None] * 30
        response = client.patch('/api/wiki/cities/4', json={'registration_city_ids': [11]})
        self.assertEqual(response.status_code, 200, response.get_json())
        sql = [str(c.args[0]) for c in cursor.execute.call_args_list]
        self.assertTrue(any('INSERT INTO wiki_city_registration' in q for q in sql))
        self.assertTrue(any('UPDATE wiki_cities SET updated_at' in q for q in sql))

        client, cursor = self.build([12])
        cursor.fetchone.side_effect = [tuple(row)] + [None] * 30
        response = client.patch('/api/wiki/cities/4', json={'registration_city_ids': '11'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'WIKI_CITY_FIELD')


# ── 7. Развёртывание ─────────────────────────────────────────────────────────

class RegistrationSchemaTest(unittest.TestCase):

    def test_tables_keys_and_order(self):
        ddl = ' '.join(' '.join(wiki_schema._REGISTRATION_STATEMENTS).split())
        self.assertIn('space_id INTEGER NOT NULL REFERENCES wiki_spaces(id) ON DELETE CASCADE', ddl)
        self.assertIn('park_id INTEGER REFERENCES wiki_taxi_parks(id) ON DELETE SET NULL', ddl)
        self.assertIn('city_id INTEGER REFERENCES wiki_cities(id) ON DELETE SET NULL', ddl)
        self.assertIn('UNIQUE (space_id, fleet_park_id)', ddl)
        self.assertIn("CHECK (status IN ('active', 'gone'))", ddl)
        self.assertIn('target_id INTEGER NOT NULL REFERENCES wiki_cities(id) ON DELETE CASCADE', ddl)
        self.assertIn('CHECK (city_id <> target_id)', ddl)
        source = inspect.getsource(wiki_schema.init_wiki_schema)
        self.assertLess(source.index('_seed_cities(cursor)'),
                        source.index('_REGISTRATION_STATEMENTS'))
        self.assertLess(source.index('_seed_city_registration(cursor)'),
                        source.index('_scope_audit_to_space(cursor)'))
        self.assertIn("WHEN 'dispatcher'", wiki_schema.AUDIT_SPACE_SQL)

    def test_seed_once_marked_and_only_with_a_space(self):
        cursor = _RecordingCursor(rows=[None])                # пространства нет — метки нет
        wiki_schema._seed_city_registration(cursor)
        self.assertFalse(any('wiki_seeds' in sql for sql, _ in cursor.calls))

        cursor = _RecordingCursor(rows=[(SPACE,), None])      # метка уже стоит
        wiki_schema._seed_city_registration(cursor)
        self.assertFalse(any('INSERT INTO wiki_city_registration' in sql
                             for sql, _ in cursor.calls))

        cursor = _RecordingCursor(rows=[(SPACE,), (wiki_schema.REGISTRATION_SEED,)])
        wiki_schema._seed_city_registration(cursor)
        inserts = [(sql, params) for sql, params in cursor.calls
                   if sql.startswith('INSERT INTO wiki_city_registration')]
        self.assertEqual(len(inserts), len(wiki_cities.DEFAULT_REGISTRATION))
        sql, params = inserts[0]
        self.assertIn('NOT EXISTS (SELECT 1 FROM wiki_city_registration r WHERE r.city_id = c.id)',
                      sql)
        self.assertEqual(params, (['Костанай'], SPACE, 'Рудный'))

    def test_seed_is_the_owner_decision(self):
        self.assertEqual(dict(wiki_cities.DEFAULT_REGISTRATION), {
            'Рудный': ('Костанай',), 'Темиртау': ('Караганда',), 'Экибастуз': ('Павлодар',),
            'Жанаозен': ('Актау',), 'Аксай': ('Уральск',), 'Кентау': ('Туркестан', 'Шымкент'),
            'Жезказган': ('Караганда',)})
        names = {name for name, _url in wiki_cities.DEFAULT_CITIES} | {'Кокшетау'}
        for name, targets in wiki_cities.DEFAULT_REGISTRATION:
            self.assertIn(name, names)
            for target in targets:
                self.assertIn(target, names)


if __name__ == '__main__':
    unittest.main()
