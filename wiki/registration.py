"""Куда регистрировать водителя: город и диспетчерская (просьба владельца 29.10.2026).

«Куда зарегать водителя айтакси, если он обращается с Рудного?» — своих
диспетчерских в Рудном нет, и водителей регистрируют в Костанае, в
диспетчерскую «iTaxi». Владелец: подсказать именно город, другие таксопарки
не предлагать — водитель звонит в свой парк.

Правило одно на карточку города, поиск и помощника:
  1. диспетчерская ЭТОГО парка, которая работает на город водителя
     (dispatchers.auto_city или ручная правка), — регистрируем в ней;
  2. иначе — города «Куда регистрировать» по порядку (Кентау: Туркестан, затем
     Шымкент): первый, где у парка есть диспетчерская;
  3. иначе — диспетчерской этого парка для города нет. Это ответ, а не отказ,
     и другой парк взамен не называется.

Модуль сам в базу не ходит — SQL живёт в dispatchers.py и cities.py под
стражей пространства: здесь разбор вопроса, решение и строки для карточки,
выдачи поиска и фрагментов помощника.
"""

import math
import re
from collections import defaultdict

from . import cities as wiki_cities
from . import directory
from . import dispatchers as wiki_dispatchers
from . import parks as wiki_parks

# Номера фрагментов: у справочника они с нуля, у «Списков Байги» с тысячи, у
# «Классификатора авто» с двух тысяч (сшивка источников ищет фрагмент по
# chunk_id, и одинаковые номера у соседних веток спутали бы цитаты).
_CHUNK_BASE = 3000
SOURCE_KIND = 'registration'
SOURCE_TAB = 'cities'

# Городов и парков в одном ответе. Больше — это уже не вопрос «куда», а
# просьба показать справочник, и для неё есть вкладка.
MAX_CITIES = 3
MAX_BRANDS = 3
# Короткий запрос («рудный регистрация») — сам по себе вопрос к справочнику.
SHORT_WORDS = 4

_LETTERS = r'a-zа-я0-9'


def _bounded(pattern):
    return re.compile(r'(?<![%s])(?:%s)' % (_LETTERS, pattern))


# Корень «регистр» с опечатками, которые пишут на самом деле: «регестр»,
# «ригистр», «зарегестрировать». Одно правило и для признака, и для
# исключений: иначе «зарегестрирован» проходил мимо исключения и уже
# зарегистрированному водителю писали «регистрировать в Костанае» (разбор
# 29.10.2026).
_ROOT = r'р[еи]г[еи]стр'

# «Зарегать», «регистрировать», «регистрация», «регать», казахское «тіркеу»
# (свёрнутое — «тиркеу»). Действие, а не состояние: «зарегистрирован в
# Шымкенте» описывает водителя и вырезается ниже.
_REGISTRATION = _bounded(
    r'(?:за)?%s(?:ир|ац)\w*'
    r'|зар[еи]г\w*'
    r'|р[еи]г(?:ать|аем|аю|ает|ают|аешь|айте|нуть|ну|нем|нешь)(?![%s])'
    r'|тирке\w*' % (_ROOT, _LETTERS))

# То, где слово «регистрация» про другое: уже зарегистрирован, перерегистрация,
# «две недели без комиссии после регистрации», «не может пройти регистрацию»,
# «ссылка на регистрацию», «регистрация в акции», «регистрация ИП», «как
# зарегать машину». Вырезаются до поиска признака — так же, как «адрес подачи»
# у офисов (directory._ORDER_ADDRESS). Список — из журнала помощника за
# 10.08–29.10.2026: каждая форма здесь там встречалась.
_REGISTRATION_ELSEWHERE = _bounded(
    (r'(?:за)?ROOTирова[нл]\w*'
     # «Регистрируется уже неделю» — состояние, а не вопрос, куда.
     r'|ROOTиру(?:ется|ются)\s+(?:уже|давно|неделю|долго|\d)\w*'
     r'|зар[еи]га[нл]\w*'
     r'|перер[еи]г\w*'
     r'|(?:при|после|до|без|во\s+время)\s+(?:за)?ROOTац\w*'
     r'|(?:пройти|проходит|проходить|прошел|прошла|прошли|завершить|закончить|продолжить'
     r'|начать|начал\w*|ожидает)\s+(?:полн\w*\s+)?(?:за)?ROOTац\w*'
     r'|(?:ссылк|форм|документ|анкет|инструкц|статус|этап|шаг)\w*\s+(?:\w+\s+)?'
     r'(?:для\s+|на\s+|по\s+|при\s+)?(?:за)?ROOTац\w*'
     r'|(?:адрес|мест|прописк)\w*\s+ROOTац\w*'
     r'|ROOTац\w*\s+(?:в\s+|на\s+)?(?:акци|приложени|яндекс|tez|тез|егов|egov)\w*'
     # «Зарегистрировать как ИП», «регистрация самозанятых» — статус, а не
     # место. Но «куда регистрировать самозанятого водителя» — вопрос о месте.
     r'|(?:за)?р[еи]г\w*\s+как\s+(?:смз|ип|самозанят|юрлиц|тоо)\w*'
     r'|ROOTац\w*\s+(?:смз|ип|самозанят|юрлиц|тоо)\w*'
     r'|(?:зар[еи]г|ROOT)\w*\s+(?:\w+\s+)?(?:машин|авто|тс|транспорт)\w*').replace('ROOT', _ROOT))

# «Оформить водителя», «устроиться в парк» — тоже про регистрацию, но слова
# широкие («оформить возврат», «устроить проверку»). Засчитываются только
# вместе с вопросом о месте: «куда оформить водителя из Рудного».
_REGISTRATION_WEAK = _bounded(
    r'оформ(?:ить|лять|ляем|ляют|им)\w*'
    r'|устро(?:ить|иться|иваться|юсь|ится)\w*')

# «Куда», «в какой город», «в какую диспетчерскую», «по какому городу», «где
# регистрировать», казахское «қайда» / «қай қалаға». С ним вопрос о
# регистрации спрашивает именно место.
_WHERE = _bounded(
    r'куда'
    r'|(?:в|на|к)\s+как(?:ой|ую|ом|ие|ая)\s+(?:город|диспетч|дисп|парк|регион|област)'
    r'|по\s+как(?:ому|ой)\s+(?:город|диспетч|област)'
    r'|как(?:ой|ую|ая|ие)\s+(?:город|диспетч|дисп)'
    r'|в\s+каком\s+город'
    r'|где\s+(?:\w+\s+){0,2}(?:за)?р[еи]г'
    r'|диспетч\w*'
    r'|кайда|кай\s+кал')

# Город водителя: «из Рудного», «с Рудного», «со Шымкента».
_ORIGIN_TAIL = re.compile(r'(?:^|\s)(?:из|с|со|от)\s+$')


def _cleaned(variants):
    return [_REGISTRATION_ELSEWHERE.sub(' ', value) for value in variants or ()]


def asks_registration(variants):
    """Есть ли в написаниях действие «зарегистрировать» (не «зарегистрирован»)."""
    return any(_REGISTRATION.search(value) for value in _cleaned(variants))


def asks_where(variants):
    return any(_WHERE.search(value) for value in _cleaned(variants))


def asks_registration_weakly(variants):
    """«Оформить», «устроиться» — регистрация, если спрошено место (asks_where)."""
    return any(_REGISTRATION_WEAK.search(value) for value in _cleaned(variants))


def analyze(question, search_query=None):
    """Разбор вопроса — поверх общего разбора справочника (directory).

    registration_own — регистрацию спросили в самом вопросе. registration_context
    — только в прошлых репликах: тогда это продолжение («а Аманат?» после
    вопроса о Рудном), и plan примет его, лишь если своя реплика — уточнение
    города или парка без посторонних слов («а после регистрации бонус?» и
    «спасибо» — нет).
    """
    base = directory.analyze_question(question, search_query)
    own_variants = base.get('own_variants') or base['variants']
    reg_own = asks_registration(own_variants) or (
        asks_registration_weakly(own_variants) and asks_where(own_variants))
    reg_context = bool(base.get('own_variants')) and not reg_own and (
        asks_registration(base['variants'])
        or (asks_registration_weakly(base['variants']) and asks_where(base['variants'])))
    return dict(base, registration=bool(reg_own or reg_context),
                registration_own=bool(reg_own), registration_context=bool(reg_context),
                registration_where=asks_where(own_variants),
                registration_short=len(base['words']) <= SHORT_WORDS)


# ── Казахские падежи города ──────────────────────────────────────────────────
#
# «Рудныйдан келген жүргізуші» — «водитель из Рудного». Общий разбор городов
# (directory._city_pattern) знает только русские окончания, и менять его ради
# регистрации нельзя: им живут офисы, комиссии и классификатор. Поэтому падеж
# срезается здесь, в своих написаниях вопроса. Исходный падеж (-дан/-тан/-нан)
# — это «откуда», то есть город водителя.
_KZ_FROM = ('дан', 'ден', 'тан', 'тен', 'нан', 'нен')
_KZ_OTHER = ('нын', 'нин', 'дын', 'дин', 'тын', 'тин', 'га', 'ге', 'ка', 'ке', 'на', 'не',
             'да', 'де', 'та', 'те', 'ны', 'ни', 'ды', 'ди', 'ты', 'ти')


def _kazakh_cases(variants, city_names):
    """(написания со срезанным падежом, города в исходном падеже).

    Срезается только окончание, приросшее к названию города пространства (или
    его разговорной форме): «темиртаудан» → «темиртау», а «тауда» не трогается.
    """
    forms = {}
    for name in city_names:
        folded = directory.normalize(name)
        if folded:
            forms[folded] = name
    for alias, target in directory.CITY_ALIASES.items():
        for folded, name in list(forms.items()):
            if folded == target:
                forms[alias] = name
    pattern = re.compile(r'(?<![%s])(%s)(%s)(?![%s])' % (
        _LETTERS, '|'.join(re.escape(form) for form in sorted(forms, key=len, reverse=True)),
        '|'.join(_KZ_FROM + _KZ_OTHER), _LETTERS))
    found, origin = [], []
    for value in variants or ():
        def cut(match):
            if match.group(2) in _KZ_FROM and forms[match.group(1)] not in origin:
                origin.append(forms[match.group(1)])
            return match.group(1)
        stripped = pattern.sub(cut, value)
        if stripped != value and stripped not in found:
            found.append(stripped)
    return found, origin


# ── Посторонние слова ────────────────────────────────────────────────────────
#
# Слабый повод (короткий запрос, «из Рудного», два города) и продолжение
# разговора засчитываются, только когда кроме города и парка в реплике нет
# своей темы: «рудный регистрация» — вопрос к справочнику, а «лимонопад
# регистрация алматы» и «а после регистрации бонус?» — про акцию и бонус
# (разбор 29.10.2026: оба приносили строку «регистрировать в …»). Слова
# самого вопроса о регистрации объяснены по началу слова.
_EXPLAINED = (
    'зарег', 'зариг', 'регистр', 'регестр', 'ригистр', 'регат', 'регнут', 'тирке', 'оформ',
    'устро', 'диспетч', 'куда', 'кайда', 'город', 'кала', 'регион', 'област', 'водител',
    'жургиз', 'таксист', 'курьер', 'обраща', 'звони', 'пишет', 'хоче', 'хоти', 'хотя', 'хочу',
    'приехал', 'переехал', 'живет', 'находит', 'новый', 'новичок', 'кандидат', 'парк',
    'такси', 'taxi', 'если', 'тогда', 'можно', 'нужно', 'надо', 'подскаж', 'скаж',
    'пожалуйста', 'какой', 'какую', 'каком', 'какая', 'какие', 'какому', 'какого', 'него',
    'туда', 'сюда', 'тоже', 'также', 'ещё', 'еще', 'сейчас', 'теперь', 'именно', 'вообще',
    'келген', 'керек', 'тиркеу', 'жане',
)


def _unexplained(variants, catalog, entries):
    """Слова реплики, которых не объясняют город, названный парк и слова вопроса."""
    patterns = [pattern for pattern, _name in directory.city_patterns(catalog.city_names)]
    brand_forms = set()
    for entry in entries:
        for words in directory._park_alternatives(entry['name']):
            for word in words:
                brand_forms |= directory._park_word_forms(word)
    best = None
    for value in variants or ():
        text = value
        for pattern in patterns:
            text = pattern.sub(' ', text)
        rest = []
        for word in text.split():
            if len(word) < 4 or word.isdigit() or word.startswith(_EXPLAINED):
                continue
            if directory._park_word_forms(word) & brand_forms:
                continue
            rest.append(word)
        if best is None or len(rest) < len(best):
            best = rest
    return best or []


# ─────────────────────────────────────────────────────────────────────────────
# Парки и диспетчерские
# ─────────────────────────────────────────────────────────────────────────────

def _norm(value):
    return directory.normalize(value)


def brands(dispatchers, parks, city_names):
    """{ключ: {'key', 'name', 'park_id', 'position'}} и [(ключ, диспетчерская)].

    Бренд — парк вики, к которому привязана диспетчерская, а у непривязанной —
    её название без городов: «Достойный» в Астане, Шымкенте и «Достойный
    Алматы» — одна строка карточки, а не три.
    """
    park_by_id = {park['id']: (index, park) for index, park in enumerate(parks)}
    found, keyed = {}, []
    for dispatcher in dispatchers:
        if dispatcher.get('park_id') in park_by_id:
            index, park = park_by_id[dispatcher['park_id']]
            key, name, park_id, position = 'p:%d' % park['id'], park['name'], park['id'], index
        else:
            name = wiki_dispatchers.base_name(dispatcher.get('name'), city_names)
            key, park_id, position = 'n:' + _norm(name), None, len(parks)
        found.setdefault(key, {'key': key, 'name': name, 'park_id': park_id, 'position': position})
        keyed.append((key, dispatcher))
    return found, keyed


def _ordered(found):
    """Парки вики — как во вкладке «Таксопарки», за ними бренды без парка."""
    return sorted(found.values(), key=lambda brand: (brand['position'], _norm(brand['name'])))


def _dispatcher_order(brand_name):
    """«iTaxi» первой, за ней «iTaxi 2», «iTaxi 3», «iTaxi курьер», а со
    скобками («iTaxi (Доставка)») последними: таксиста регистрируют в
    основной, и первой её и надо видеть."""
    target = _norm(brand_name)

    def key(dispatcher):
        name = _norm(dispatcher.get('name'))
        return (name != target, '(' in str(dispatcher.get('name') or ''), len(name), name)
    return key


# Центры городов — двойник CITY_COORDS схемы «Городов»
# (src/components/wiki/kazakhstanMap.js; расхождение стережёт тест). Нужны для
# «ближайшей диспетчерской парка»: город без координат в расчёт не входит.
CITY_COORDS = {
    'Аксай': (51.171, 53.003), 'Актау': (43.651, 51.160), 'Актобе': (50.283, 57.167),
    'Алматы': (43.238, 76.946), 'Астана': (51.169, 71.449), 'Атырау': (47.094, 51.924),
    'Аксу': (52.041, 76.927), 'Аркалык': (50.248, 66.912), 'Балхаш': (46.848, 74.995),
    'Жанаозен': (43.341, 52.862), 'Жаркент': (44.163, 80.001), 'Жезказган': (47.783, 67.714),
    'Житикара': (52.190, 61.201), 'Караганда': (49.806, 73.085), 'Кандыагаш': (49.476, 57.422),
    'Кентау': (43.517, 68.504), 'Кокшетау': (53.283, 69.383), 'Конаев': (43.867, 77.063),
    'Костанай': (53.214, 63.625), 'Кульсары': (46.953, 54.019), 'Кызылорда': (44.849, 65.482),
    'Лисаковск': (52.537, 62.494), 'Павлодар': (52.287, 76.967),
    'Петропавловск': (54.875, 69.163), 'Риддер': (50.344, 83.513), 'Рудный': (52.973, 63.117),
    'Сарыагаш': (41.460, 69.170), 'Сатпаев': (47.900, 67.533), 'Семей': (50.411, 80.227),
    'Степногорск': (52.350, 71.883), 'Талдыкорган': (45.016, 78.374), 'Тараз': (42.900, 71.367),
    'Темиртау': (50.055, 72.965), 'Туркестан': (43.297, 68.252), 'Уральск': (51.233, 51.367),
    'Усть-Каменогорск': (49.948, 82.628), 'Шымкент': (42.342, 69.590),
    'Щучинск': (52.936, 70.189), 'Экибастуз': (51.724, 75.323),
}


def distance_km(first, second):
    """Расстояние между городами по прямой, км, или None без координат."""
    a, b = CITY_COORDS.get(first), CITY_COORDS.get(second)
    if not a or not b:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def resolve(city, key, *, by_brand, cities_by_id):
    """Куда регистрировать водителя города city в бренд key — или None.

    Третий шаг — просьба владельца 29.10.2026: «водитель Jana из Рудного, в
    Костанае Jana нет — предлагать ближайшую диспу Jana». Если ни в городе, ни
    в городах списка у парка диспетчерской нет, берётся ближайший по прямой
    город, где она у этого парка есть (nearest=True, km). Другой парк взамен
    по-прежнему не предлагается.
    """
    pool = by_brand.get(key) or []
    own = [d for d in pool if d.get('city_id') == city['id']]
    if own:
        return {'city': city, 'dispatchers': own, 'own': True}
    for target_id in city.get('registration_city_ids') or []:
        target = cities_by_id.get(target_id)
        if target is None:
            continue
        found = [d for d in pool if d.get('city_id') == target_id]
        if found:
            return {'city': target, 'dispatchers': found, 'own': False}
    best = None
    for city_id in sorted({d.get('city_id') for d in pool if d.get('city_id') is not None}):
        target = cities_by_id.get(city_id)
        km = distance_km(city['name'], target['name']) if target else None
        if km is not None and (best is None or km < best[0]):
            best = (km, target)
    if best:
        km, target = best
        return {'city': target, 'dispatchers': [d for d in pool if d.get('city_id') == target['id']],
                'own': False, 'nearest': True, 'km': max(10, int(round(km / 10.0)) * 10)}
    return None


def _dispatcher_view(dispatcher, served):
    """Диспетчерская для показа. Город кабинета — только когда он другой:
    «Честный Темиртау» в кабинете записан в Караганду, и оператор будет
    искать её в списке Караганды."""
    fleet_city = (dispatcher.get('fleet_city') or '').strip()
    other = fleet_city if fleet_city and _norm(fleet_city) != _norm(served) else None
    return {'id': dispatcher.get('id'), 'name': dispatcher.get('name'),
            'fleet_city': fleet_city or None, 'fleet_city_other': other}


class Catalog:
    """Справочник регистрации пространства: города, парки, диспетчерские."""

    def __init__(self, *, cities, parks, dispatchers):
        self.cities = list(cities)
        self.parks = list(parks)
        self.dispatchers = [d for d in dispatchers if (d.get('status') or 'active') == 'active']
        self.cities_by_id = {city['id']: city for city in self.cities}
        self.city_names = [city['name'] for city in self.cities]
        self.brands, keyed = brands(self.dispatchers, self.parks, self.city_names)
        self.by_brand = defaultdict(list)
        for key, dispatcher in keyed:
            self.by_brand[key].append(dispatcher)
        for key, pool in self.by_brand.items():
            pool.sort(key=_dispatcher_order(self.brands[key]['name']))
        # Парк вики без единой диспетчерской («Hokage (Wolt)») — тоже бренд:
        # назвали его в вопросе — честный ответ «диспетчерской нет», а не
        # ответ про все парки города.
        for index, park in enumerate(self.parks):
            self.brands.setdefault('p:%d' % park['id'], {
                'key': 'p:%d' % park['id'], 'name': park['name'], 'park_id': park['id'],
                'position': index})

    def city(self, name):
        folded = _norm(name)
        return next((city for city in self.cities if _norm(city['name']) == folded), None)

    def resolve(self, city, key):
        return resolve(city, key, by_brand=self.by_brand, cities_by_id=self.cities_by_id)

    def targets(self, city):
        return [self.cities_by_id[target_id] for target_id in city.get('registration_city_ids') or []
                if target_id in self.cities_by_id]

    def block(self, city):
        """Блок «Регистрация водителей» карточки города. None — диспетчерских
        в пространстве нет вовсе (у Тез кабинета нет): блоку нечего сказать."""
        if not self.dispatchers or city is None:
            return None
        entries, absent = [], []
        for brand in _ordered(self.brands):
            if not self.by_brand.get(brand['key']):
                continue          # парк без диспетчерских — карточке города сказать нечего
            found = self.resolve(city, brand['key'])
            if found and found.get('nearest') and brand['park_id'] is None:
                # Бренды без парка вики — только свои и по списку: «ближайший
                # Каспий» в карточке Алматы — шум, а не сведение.
                continue
            if found:
                served = found['city']['name']
                entries.append({
                    'key': brand['key'], 'name': brand['name'], 'park_id': brand['park_id'],
                    'city': {'id': found['city']['id'], 'name': served},
                    'own': found['own'],
                    # Ни в городе, ни по списку — ближайшая диспетчерская парка
                    # (просьба владельца 29.10.2026); карточка показывает такие
                    # строки отдельно, свёрнутыми.
                    'nearest': bool(found.get('nearest')),
                    'km': found.get('km'),
                    'dispatchers': [_dispatcher_view(d, served) for d in found['dispatchers']],
                })
            elif brand['park_id'] is not None:
                # Парк вики, которому не нашлось и ближайшей диспетчерской (у
                # города нет координат).
                absent.append(brand['name'])
        own_count = sum(1 for d in self.dispatchers if d.get('city_id') == city['id'])
        targets = [{'id': target['id'], 'name': target['name']} for target in self.targets(city)]
        # «Смешанный» город: своя диспетчерская есть не у всех парков. В
        # Темиртау она одна — «Честный Темиртау», а iTaxi и Аманат регистрируют
        # в Караганде, и заголовок «регистрируем в Темиртау» был бы неправдой
        # для двух парков из трёх. Ближайшие диспетчерские в расчёт не идут:
        # Jana в Актобе не делает Костанай «смешанным».
        configured = [entry for entry in entries if not entry['nearest']]
        if own_count:
            state = 'mixed' if any(not entry['own'] for entry in configured) else 'own'
        else:
            state = 'elsewhere' if targets else 'unset'
        feeders = [{'id': other['id'], 'name': other['name']} for other in self.cities
                   if city['id'] in (other.get('registration_city_ids') or [])]
        return {'state': state, 'own_count': own_count, 'targets': targets,
                'parks': entries, 'absent': absent, 'feeders': feeders}

    def aliases(self, key):
        """Другие имена парка — названия его диспетчерских, в которых имени
        парка нет: у Честного «Адал», у 2dongelek «EKI DONGELEK»."""
        brand = self.brands.get(key)
        if not brand or brand['park_id'] is None:
            return []
        park = next((park for park in self.parks if park['id'] == brand['park_id']), None)
        found = []
        for dispatcher in self.by_brand.get(key) or []:
            name = wiki_dispatchers.base_name(dispatcher.get('name'), self.city_names)
            if park is None or directory.match_parks(
                    {'variants': directory._variants(dispatcher.get('name'))}, [park]):
                continue
            if name not in found:
                found.append(name)
        return found

    def question_parks(self):
        """Чем парк называют в вопросе: имена парков вики и названия диспетчерских.

        Название диспетчерской идёт вторым именем её парка, только если в нём
        нет имени парка («Адал» у Честного, «EKI DONGELEK» у 2dongelek):
        «iTaxi 2» и так находит iTaxi. Бренд без парка («Достойный», «Каспий»)
        — своим названием. Общие слова («Самозанятый») брендом не служат.
        """
        entries = [{'id': 'p:%d' % park['id'], 'name': park['name']} for park in self.parks]
        park_by_id = {park['id']: park for park in self.parks}
        seen = {(entry['id'], _norm(entry['name'])) for entry in entries}
        cities = {_norm(name) for name in self.city_names}
        cities |= {alias for alias, target in directory.CITY_ALIASES.items() if target in cities}
        for dispatcher in self.dispatchers:
            name = wiki_dispatchers.base_name(dispatcher.get('name'), self.city_names)
            if wiki_dispatchers.is_generic(name):
                continue
            if _norm(name) in cities:
                # Диспетчерская «Усть-Каменогорск»: её имя — это город. Брендом
                # в вопросе оно служить не может, иначе «куда зарегать водителя
                # из Усть-Каменогорска» читалось бы как вопрос про этот парк, и
                # помощник предлагал бы его водителю (нашёл разбор 29.10.2026).
                continue
            park = park_by_id.get(dispatcher.get('park_id'))
            if park is not None:
                if directory.match_parks({'variants': directory._variants(dispatcher.get('name'))},
                                         [park]):
                    continue
                entry = {'id': 'p:%d' % park['id'], 'name': name}
            else:
                entry = {'id': 'n:' + _norm(name), 'name': name}
            if (entry['id'], _norm(entry['name'])) not in seen:
                seen.add((entry['id'], _norm(entry['name'])))
                entries.append(entry)
        return entries

    def named_brands(self, variants):
        """(бренды, совпавшие имена) — парки, названные в этих написаниях.

        Имена — те, по которым узнали («Адал» у Честного): по ним же plan
        считает слова парка объяснёнными (_unexplained)."""
        if not variants:
            return [], []
        keys, entries = [], []
        for entry in directory.match_parks({'variants': variants}, self.question_parks()):
            if entry['id'] in self.brands:
                entries.append(entry)
                if entry['id'] not in keys:
                    keys.append(entry['id'])
        return [self.brands[key] for key in keys], entries


# ─────────────────────────────────────────────────────────────────────────────
# Решение: какие города и парки ответить
# ─────────────────────────────────────────────────────────────────────────────

def _origin_cities(variants, named):
    """Названные города, перед которыми стоит «из/с/со/от» — город водителя."""
    found = []
    patterns = directory.city_patterns(named)
    for pattern, name in patterns:
        for value in variants or ():
            match = pattern.search(value)
            if match and _ORIGIN_TAIL.search(value[:match.start()]) and name not in found:
                found.append(name)
    return [name for name in named if name in found]


def plan(analysis, catalog):
    """[{'city', 'brand', 'found'} | {'city', 'brand': None, 'block'}] или [].

    Справочник отвечает, когда спросили регистрацию и назвали город:
      * сильный повод — «куда …» или назван парк: отвечаем всегда;
      * слабый — второй город («из Рудного в Костанай?»), город водителя («из
        Рудного») или короткий запрос («рудный регистрация»): только если в
        реплике нет своей темы (_unexplained);
      * продолжение разговора («а аманат?», «а из темиртау?») — только если
        своя реплика называет город или парк и ничего больше.
    «Как зарегать машину» и «после регистрации две недели без комиссии» сюда
    не доходят — их вырезает _REGISTRATION_ELSEWHERE.
    """
    if not analysis.get('registration') or not catalog.dispatchers:
        return []
    own_variants = analysis.get('own_variants') or analysis['variants']
    kazakh_own, origin_own = _kazakh_cases(own_variants, catalog.city_names)
    kazakh_all, origin_all = _kazakh_cases(analysis['variants'], catalog.city_names)
    # С синонимами парков («жана такси» → Jana): иначе «жана» в продолжении
    # «а жана такси?» осталось бы необъяснённым словом (_unexplained).
    own_variants = directory._park_variants(list(own_variants) + kazakh_own)
    all_variants = directory._park_variants(list(analysis['variants']) + kazakh_all)
    own_cities = directory.find_cities({'variants': own_variants}, catalog.city_names)
    own_brands, own_entries = catalog.named_brands(own_variants)
    if analysis.get('registration_own'):
        where = analysis.get('registration_where')
    elif analysis.get('registration_context'):
        if not (own_cities or own_brands) or _unexplained(own_variants, catalog, own_entries):
            return []
        where = True                  # тему задала прошлая реплика
    else:
        return []
    if own_cities:
        named, origin = own_cities, _origin_cities(own_variants, own_cities) + origin_own
    else:
        named = directory.find_cities({'variants': all_variants}, catalog.city_names)
        origin = _origin_cities(all_variants, named) + origin_all
    if not named:
        return []
    origin = [name for name in named if name in origin]
    named_brands, entries = own_brands, own_entries
    if not named_brands:
        named_brands, entries = catalog.named_brands(all_variants)
    if not (where or named_brands):
        weak = origin or len(named) > 1 or analysis.get('registration_short')
        if not weak or _unexplained(own_variants, catalog, entries):
            return []
    chosen = origin or named
    decisions = []
    for name in chosen[:MAX_CITIES]:
        city = catalog.city(name)
        if city is None:
            continue
        if named_brands:
            for brand in named_brands[:MAX_BRANDS]:
                decisions.append({'city': city, 'brand': brand,
                                  'found': catalog.resolve(city, brand['key'])})
        else:
            decisions.append({'city': city, 'brand': None, 'block': catalog.block(city)})
    return decisions


# ─────────────────────────────────────────────────────────────────────────────
# Тексты: фрагмент помощника и строка поиска
# ─────────────────────────────────────────────────────────────────────────────

def _label(dispatcher):
    """«iTaxi» или «Честный Темиртау» (в кабинете Fleet — в списке города Караганда).

    Не «записана в город Караганда»: так Gemini читал Караганду городом
    регистрации водителя из Темиртау (замер 29.10.2026). Город регистрации —
    тот, на который диспетчерская работает; кабинет нужен, чтобы её найти."""
    text = '«%s»' % dispatcher['name']
    if dispatcher.get('fleet_city_other'):
        text += ' (в кабинете Fleet — в списке города %s)' % dispatcher['fleet_city_other']
    return text


def _labels(found):
    served = found['city']['name']
    views = [_dispatcher_view(d, served) for d in found['dispatchers']]
    word = 'диспетчерская' if len(views) == 1 else 'диспетчерские'
    return '%s %s' % (word, ', '.join(_label(view) for view in views))


def _targets_text(names):
    """«в городе Туркестан; если у парка нет диспетчерской в городе Туркестан —
    в городе Шымкент»."""
    text = 'в городе %s' % names[0]
    for previous, name in zip(names, names[1:]):
        text += '; если у парка нет диспетчерской в городе %s — в городе %s' % (previous, name)
    return text


def brand_text(decision, catalog):
    """Фрагмент «город + парк». Город и диспетчерская — дословно, по строке.

    Слов «до/по» перед датами здесь нет и быть не должно (wiki/ai/currency.py
    считает «до 12.03» сроком); названия городов — в именительном падеже
    («в городе Костанай»): склонять чужие названия программой — значит
    однажды написать «в Кентаую».
    """
    city, brand, found = decision['city'], decision['brand'], decision['found']
    head = 'Регистрация водителей из города %s, парк %s' % (city['name'], brand['name'])
    aliases = catalog.aliases(brand['key'])
    if aliases:
        # «Хочет в Адал» — GLM переспрашивал, парк это или диспетчерская, когда
        # во фрагменте стоял только «парк Честный» (замер 29.10.2026).
        head += ' (он же %s)' % ', '.join('«%s»' % name for name in aliases)
    targets = [target['name'] for target in catalog.targets(city)]
    if found and found.get('nearest'):
        # Ни в городе, ни в городах списка — ближайшая диспетчерская ЭТОГО
        # парка (просьба владельца 29.10.2026: «предлагать ближайшую диспу
        # жана такси»). Расстояние по прямой — чтобы оператор понимал, далеко
        # ли, а не принимал Актобе за соседний город.
        places = [city['name']] + targets
        missing = ('В городе %s диспетчерской %s нет.' % (places[0], brand['name'])
                   if len(places) == 1 else
                   'Диспетчерской %s нет ни в городе %s.' % (brand['name'],
                                                            ', ни в городе '.join(places)))
        return ('%s: ближайшая диспетчерская %s — в городе %s, около %d км по прямой: '
                'регистрировать там — %s. %s'
                % (head, brand['name'], found['city']['name'], found['km'], _labels(found),
                   missing))
    if found:
        text = '%s: регистрировать в городе %s — %s.' % (head, found['city']['name'],
                                                          _labels(found))
        if not found['own']:
            text += ' Своей диспетчерской %s в городе %s нет.' % (brand['name'], city['name'])
        return text
    if not catalog.by_brand.get(brand['key']):
        return ('%s: у парка %s нет ни одной диспетчерской в кабинете Fleet.'
                % (head, brand['name']))
    if targets:
        return ('%s: диспетчерской %s для водителей из города %s нет — ни в городе %s, ни в '
                'городе %s.' % (head, brand['name'], city['name'], city['name'],
                                ', ни в городе '.join(targets)))
    return ('%s: диспетчерской %s в городе %s нет, а другой город для регистрации у города %s '
            'не задан.' % (head, brand['name'], city['name'], city['name']))


def other_cities(block):
    """Города, куда регистрируют парки без своей диспетчерской в городе, — по порядку."""
    found = []
    for entry in block.get('parks') or []:
        if not entry['own'] and not entry.get('nearest') and entry['city']['name'] not in found:
            found.append(entry['city']['name'])
    return found


# Без парка диспетчерскую не назвать — во фрагменте это сказано фактом, а не
# списком парков: со списком Gemini на «водитель с Экибастуза» перечислял
# таблицей все парки Павлодара, хотя правило промпта просит этого не делать
# (замер 29.10.2026). Владелец: «другие таксопарки предлагать не стоит».
_ASK_PARK = 'Диспетчерская зависит от парка водителя.'


def city_text(decision):
    """Фрагмент «город без парка»: куда регистрируют, без перечня парков."""
    city, block = decision['city'], decision['block']
    name = city['name']
    if block is None:
        return ''
    if block['state'] == 'own':
        return 'Регистрация водителей из города %s: регистрировать в городе %s. %s' % (
            name, name, _ASK_PARK)
    if block['state'] == 'mixed':
        # Своя диспетчерская есть не у всех: названы именно они — без этого
        # «Честный Темиртау» не найти, — а про остальных сказано условие, а не
        # «все в Караганду»: у Jana в Караганде диспетчерской нет.
        # Город регистрации — у каждого такого парка прямо: с одной пометкой
        # кабинета Gemini писал «Честный — регистрировать в Караганде».
        own = ['Парк %s — регистрировать в городе %s, %s.' % (entry['name'], name, _labels(entry))
               for entry in block['parks'] if entry['own']]
        others = other_cities(block)
        return ('Регистрация водителей из города %s: зависит от парка. %s Остальных парков — '
                'в %s %s, если у парка там есть диспетчерская. %s'
                % (name, ' '.join(own), 'городе' if len(others) == 1 else 'городах',
                   ', '.join(others), _ASK_PARK))
    if block['state'] == 'elsewhere':
        return ('Регистрация водителей из города %s: своих диспетчерских в городе %s нет — '
                'регистрировать %s. %s' % (name, name,
                                            _targets_text([t['name'] for t in block['targets']]),
                                            _ASK_PARK))
    return ('Регистрация водителей из города %s: своих диспетчерских в городе %s нет, и '
            'город для регистрации не задан.' % (name, name))


def _row(index, *, city, text, space_id):
    return {
        # Отрицательный id: у настоящих кусков он положителен (см. _CHUNK_BASE).
        'chunk_id': -(_CHUNK_BASE + index + 1),
        'article_id': None,
        'chunk_idx': 0,
        'title': 'Города',
        'slug': '',
        'heading_path': '%s › Регистрация водителей' % city['name'],
        'text': text,
        'requires_ack': False,
        'historical': False,
        'similarity': None,
        'found_by': [directory.DIRECTORY_BRANCH],
        'directory_hit': True,
        'source_kind': SOURCE_KIND,
        'tab': SOURCE_TAB,
        # Чип под ответом открывает карточку города водителя: в ней и город
        # регистрации, и диспетчерские остальных парков.
        'ref_id': city['id'],
        'ref_city': city['name'],
        'space_id': space_id,
    }


def build_ai_rows(analysis, catalog, *, space_id):
    """Фрагменты помощника из уже прочитанного справочника. Без базы — для тестов."""
    rows = []
    for decision in plan(analysis, catalog):
        text = brand_text(decision, catalog) if decision['brand'] else city_text(decision)
        if text:
            rows.append(_row(len(rows), city=decision['city'], text=text, space_id=space_id))
    return rows


def search_items(analysis, catalog, *, space_id):
    """Строки «Регистрация» для выдачи поиска (GET /api/wiki/search → directory).

    id и ref_id — город водителя: строка открывает его карточку, как источник
    под ответом помощника (WikiView.openDirectory берёт ref_id у всех строк,
    кроме записей офиса и города)."""
    items = []
    for decision in plan(analysis, catalog):
        city = decision['city']
        item = {'kind': 'registration', 'space_id': space_id, 'id': city['id'],
                'ref_id': city['id'], 'name': city['name'], 'park': None, 'target': None,
                'own': False, 'dispatchers': [], 'state': None, 'targets': [], 'others': [],
                'nearest': False, 'km': None}
        if decision['brand']:
            found = decision['found']
            item['park'] = decision['brand']['name']
            if found:
                served = found['city']['name']
                item.update(target=served, own=found['own'], dispatchers=[
                    _dispatcher_view(d, served) for d in found['dispatchers']],
                    nearest=bool(found.get('nearest')), km=found.get('km'))
        else:
            block = decision['block'] or {}
            item.update(state=block.get('state'), own=block.get('state') == 'own',
                        targets=[target['name'] for target in block.get('targets') or []],
                        others=other_cities(block),
                        # Ближайшие диспетчерские — не ответ города: «Регистрируем
                        # здесь · 3 парка», а не «· 17».
                        dispatchers=[{'name': entry['name']} for entry in block.get('parks') or []
                                     if not entry.get('nearest')])
        items.append(item)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Вход из роутов
# ─────────────────────────────────────────────────────────────────────────────

def load(cursor, *, space_id):
    """Справочник регистрации пространства — через читающие функции под стражей."""
    return Catalog(cities=wiki_cities.registration_cities(cursor, space_id=space_id),
                   parks=wiki_parks.list_parks(cursor, space_id=space_id),
                   dispatchers=wiki_dispatchers.list_dispatchers(cursor, space_id=space_id))


def _space(cursor, ctx, requested):
    """Пространство — как у справочников: выдано не гостем, вкладка «Города» включена."""
    space = directory.directory_space(cursor, ctx, requested)
    return space if space and space.get('cities') else None


def ai_rows(cursor, ctx, question, *, space_id, search_query=None):
    """Фрагменты «Регистрация водителей» к вопросу помощника — или []."""
    analysis = analyze(question, search_query)
    if not analysis['registration']:
        return []
    space = _space(cursor, ctx, space_id)
    if space is None:
        return []
    return build_ai_rows(analysis, load(cursor, space_id=space['id']), space_id=space['id'])


def search(cursor, ctx, query, *, requested_space):
    """Строки «Регистрация» для поисковой строки — или []."""
    analysis = analyze(query)
    if not analysis['words'] or len(analysis['words']) > directory.SEARCH_MAX_WORDS:
        return []
    if not analysis['registration']:
        return []
    space = _space(cursor, ctx, requested_space)
    if space is None:
        return []
    return search_items(analysis, load(cursor, space_id=space['id']), space_id=space['id'])
