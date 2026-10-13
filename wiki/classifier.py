"""«Классификатор авто» в помощнике вики: подходит ли машина под тариф.

Классификатор — статья вики с пустым телом (schema.CLASSIFIER_SLUG): вместо
текста фронт рисует калькулятор над файлом данных. Помощник видел у этой статьи
только засеянные 205 знаков описания и на вопрос «камри 2015 года проходит в
комфорт?» отвечал «в статьях нет сведений о моделях и годах, обратитесь к
Классификатору авто» — то есть отправлял человека в раздел, который не прочитал.

Устроено по образцу справочников (wiki/directory.py): данные не индексируются, а
приходят к вопросу готовым фрагментом. Причина та же, что у комиссий: ответ — это
ЧИСЛО, и число посчитанное. Требуемый год зависит от города
(max(1980, базовый год модели + поправка города), ровно как в калькуляторе —
src/components/classifier/ClassifierView.jsx), а сверка чисел ответа
(answer.ungrounded_numbers) пропускает только те, что стоят в тексте фрагмента
буквально. Дай модели базовый год и поправку — она сложит сама, и верный ответ
уйдёт в отказ как выдуманный. Поэтому годы по городам и «подходит / не подходит»
считает код, а модель их пересказывает.

Данные читаются из ТОГО ЖЕ файла, что и экран (classifier-data.json): вторая
копия разошлась бы с первой при первой же правке годов.

ДОСТУП — как у самой статьи: фрагменты отдаются, только пока статья-классификатор
лежит в периметре помощника этого человека (wiki/perimeter.py). Значит, работают
и запрет на статью, и рубильник «не отправлять во внешний ИИ», и граница
пространства: кому классификатор не показан на экране, тому его не перескажет
и помощник.

ГЛАВНЫЙ ВРАГ — ЛИШНЯЯ МАШИНА. Моделей полторы тысячи, и названия десятков из них
— обычные слова: Mazda Familia («фамилия»), Honda Passport («паспорт»), Lincoln
Navigator («навигатор»), Chery Bonus, Peugeot Partner, Hyundai Click и Accent.
Поэтому слово называет машину, только когда вопрос это подтверждает:
  * рядом стоит марка («хонда паспорт») — тогда годится любое название;
  * без марки — название из словаря ходовых моделей либо набранное латиницей,
    и вопрос сам про тариф, год или «подходит ли»;
  * слова из _NEVER_ALONE без марки машиной не становятся никогда.
Модель не узнана, а марка названа — помощнику уходит список моделей марки:
«спортейдж» с Sportage он сопоставит сам, а код дальше словаря не угадывает.

Разбор вопроса (analyze, plan) и сборка фрагментов (build_ai_rows) базы не
касаются — единственный запрос модуля спрашивает id статьи.
"""

import itertools
import json
import logging
import os
import re
from functools import lru_cache

from . import directory as wiki_directory
from . import schema
from . import text as wiki_text
from .ai import currency

SOURCE_KIND = 'classifier'
SOURCE_TAB = 'classifier'

# Ветка «не найдено, а взято» — та же, что у справочников: порог близости к
# таким фрагментам не относится, а переспрашивать, когда машина названа, не о чем.
BRANCH = wiki_directory.DIRECTORY_BRANCH

# id фрагментов отрицательные, как у справочника и «Списков Байги», но из своего
# диапазона: в одном ответе встречаются все три.
_CHUNK_BASE = 2000

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'src', 'components', 'classifier', 'classifier-data.json')

# Двойник YEAR_FLOOR из ClassifierView.jsx.
YEAR_FLOOR = 1980

# Сколько машин уходит помощнику разом: «матиз» — это Daewoo, Chevrolet и Ravon.
MAX_CARS = 4
# Городов в вопросе, по которым фрагмент пишется построчно; больше — сводкой.
MAX_CITIES = 3
MAX_YEARS = 3
# Строк в списке моделей марки или тарифа. У самой большой марки 124 модели,
# в «Бизнесе» 56; «Комфорт» на 494 строки в ответ не пересказать.
MAX_LIST = 130
MAX_TARIFF_LIST = 80


# ─────────────────────────────────────────────────────────────────────────────
# Данные
# ─────────────────────────────────────────────────────────────────────────────

def _key(value):
    """Имя для сравнения: только буквы и цифры. «Cee'd» → «ceed», «CR-V» → «crv»."""
    return re.sub(r'[^a-zа-я0-9]', '', wiki_text.fold_kazakh(str(value or '').lower()))


def _words(value):
    return [word for word in re.split(r'[^a-zа-я0-9]+',
                                      wiki_text.fold_kazakh(str(value or '').lower())) if word]


# Кириллические буквы, набранные вместо похожих латинских: в файле лежат
# «Jetour Х70» и «АмберАвто А5» с русскими «Х» и «А».
_LOOKALIKE = str.maketrans('авекмнорстух', 'abekmhopctyx')


def _name_key(value):
    """Ключ имени модели. Русская буква в КОДЕ («Х70», «А5») читается латинской,
    а русское слово («Волга», «Фургон») остаётся как есть."""
    parts = []
    for word in _words(value):
        coded = bool(re.search(r'\d', word)) or len(word) <= 3
        parts.append(word.translate(_LOOKALIKE) if coded else word)
    return ''.join(parts)


# Уточнение в скобках, а не второе имя: «Focus (North America)», «Cruze (HR)».
_QUALIFIERS = frozenset(('northamerica', 'hr', '4x4'))
_BRACKETS = re.compile(r'\(([^)]*)\)|«([^»]*)»')

# Строки-остатки: «Lada (VAZ) — (кроме указанных моделей)», «Москвич — (все модели)».
_CATCH_ALL = re.compile(r'кроме указанных|все модели')
OTHER_BRAND = 'Другие модели'


def _name_parts(model):
    """Название модели → (основное имя, вторые имена из скобок, уточнение ли)."""
    inner = [first or second for first, second in _BRACKETS.findall(model)]
    outer = _BRACKETS.sub(' ', model).strip()
    alternatives, qualified = [], False
    for part in inner:
        if _key(part) in _QUALIFIERS:
            qualified = True
            continue
        # «Bonus 3 (E3/A19)» — два заводских кода через дробь.
        alternatives += [piece for piece in re.split(r'[/,]', part) if _key(piece)]
    return outer, alternatives, qualified


def _subset(left, right):
    return all(right.get(key) == year for key, year in left.items())


def read_data(path=None):
    with open(path or DATA_PATH, encoding='utf-8') as handle:
        return json.load(handle)


def build_book(data):
    """Справочник классификатора с индексами имён. Чистая функция — для тестов."""
    tariffs = [{'key': item['key'], 'name': item['name']} for item in data['tariffs']]
    tariff_names = {item['key']: item['name'] for item in tariffs}
    cities = [{'id': item['id'], 'name': item['name'], 'offset': int(item.get('offset') or 0),
               'tariffs': [key for key in item.get('tariffs') or [] if key in tariff_names]}
              for item in data['cities']]

    cars, catch_all, other = [], {}, None
    for raw in data['cars']:
        brand, model = str(raw.get('brand') or '').strip(), str(raw.get('model') or '').strip()
        years = {key: int(year) for key, year in (raw.get('years') or {}).items()
                 if key in tariff_names}
        warnings = {key: str(note).strip() for key, note in (raw.get('warnings') or {}).items()
                    if key in years and str(note).strip()}
        if brand == OTHER_BRAND:
            other = {'brand': brand, 'model': model, 'years': years, 'warnings': warnings}
            continue
        if _CATCH_ALL.search(model):
            catch_all[brand] = years
            continue
        if not brand or not model:
            continue
        main, alternatives, qualified = _name_parts(model)
        cars.append({'order': len(cars), 'brand': brand, 'model': model,
                     'name': '%s %s' % (brand, model),
                     'years': years, 'warnings': warnings, 'main': main,
                     'alternatives': alternatives, 'qualified': qualified,
                     'key': _name_key(main)})

    # Одна модель двумя строками («Jetour X70» латиницей и «Jetour Х70» с русской
    # «Х», вторая — подмножество первой): остаётся полная. Строки, которые друг
    # другу противоречат, остаются обе — расхождение прятать нельзя.
    kept, seen = [], {}
    for car in cars:
        twin = seen.get((car['brand'], car['key'], car['qualified']))
        if twin is not None and not car['alternatives'] and not twin['alternatives']:
            if _subset(car['years'], twin['years']) and not car['warnings']:
                continue
            if _subset(twin['years'], car['years']) and not twin['warnings']:
                kept[kept.index(twin)] = car
                seen[(car['brand'], car['key'], car['qualified'])] = car
                continue
        seen.setdefault((car['brand'], car['key'], car['qualified']), car)
        kept.append(car)
    cars = kept

    by_brand = {}
    for car in cars:
        by_brand.setdefault(car['brand'], []).append(car)

    book = {'tariffs': tariffs, 'tariff_names': tariff_names, 'cities': cities,
            'cars': cars, 'by_brand': by_brand, 'catch_all': catch_all, 'other': other,
            'models_total': len(cars), 'brands_total': len(by_brand)}
    _index_models(book)
    _index_brands(book)
    _index_aliases(book)
    return book


_STATE = {}


def book():
    """Справочник из файла экрана или None — тогда помощник отвечает без него.

    Читается один раз на процесс. Сбой тоже запоминается: файл приезжает с
    выкладкой, а она процесс перезапускает, — и трейсбек на каждый вопрос про
    машину в лог не пишется."""
    if 'book' not in _STATE:
        try:
            _STATE['book'] = build_book(read_data())
        except Exception:                                # noqa: BLE001
            logging.exception('Помощник: файл классификатора авто не прочитан')
            _STATE['book'] = None
    return _STATE['book']


# ─────────────────────────────────────────────────────────────────────────────
# Как имя звучит
# ─────────────────────────────────────────────────────────────────────────────
#
# В классификаторе названия латиницей, а спрашивают по-русски: «королла», «нексия»,
# «тигго 7 про». Общий транслит вики (wiki/text.py) тут не годится — он побуквенный:
# «королла» → «korolla», а в файле «Corolla». Сравниваются ЗВУКОВЫЕ ключи: обе
# стороны приводятся к тому, как имя произносят («k» и твёрдая «c» — одно, «x» —
# это «ks», двойные буквы одиночные). Имена, которые по-русски читают не так, как
# пишут («Sportage» — «спортейдж», «Juke» — «жук»), лежат в словаре ниже.

_CYRILLIC_SOUND = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'zh', 'з': 'z',
    'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p',
    'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 's', 'ч': 'ch',
    'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e',
}
_VOWELS = 'аеиоуыэюяaeiouy'
_LATIN_SOUND = (('igh', 'ai'), ('sch', 'sh'), ('tch', 'ch'), ('ph', 'f'), ('th', 't'),
                ('ck', 'k'), ('kh', 'h'), ('gh', 'g'), ('qu', 'kv'), ('q', 'k'), ('x', 'ks'),
                ('w', 'v'), ('tz', 's'), ('ts', 's'), ('ee', 'i'), ('oo', 'u'))
_DOUBLE = re.compile(r'([a-z])\1+')


def _squeeze(value):
    return _DOUBLE.sub(r'\1', value.replace('ts', 's'))


def _cyrillic_sound(word):
    """«королла» → «korola», «солярис» → «solaris», «ярис» → «iaris»."""
    out, previous = [], ''
    for char in word:
        if char in 'юя':
            vowel = 'u' if char == 'ю' else 'a'
            # После согласной «я» только смягчает («солярис»), в начале слова и
            # после гласной звучит «йа» («ярис», «тойота»).
            out.append(vowel if previous and previous not in _VOWELS else 'i' + vowel)
        else:
            out.append(_CYRILLIC_SOUND.get(char, char))
        previous = char
    return _squeeze(''.join(out))


def _latin_sounds(word):
    """Звуковые ключи латинского имени. Несколько — там, где читают по-разному:
    «g» перед «e» («Gentra» — «джентра», «Getz» — «гетц») и немое «e» на конце
    («Cruze» — «круз», «Forte» — «форте»)."""
    value = word
    for source, target in _LATIN_SOUND:
        value = value.replace(source, target)
    out = []
    for index, char in enumerate(value):
        following = value[index + 1:index + 2]
        if char == 'c' and following != 'h':
            out.append('s' if following and following in 'eiy' else 'k')
        elif char == 'y':
            out.append('i')
        elif char == 'j':
            out.append('dzh')
        else:
            out.append(char)
    base = _squeeze(''.join(out))
    found = [base]
    if re.search(r'g[ei]', base):
        found.append(re.sub(r'g(?=[ei])', 'dzh', base))
    for value in list(found):
        if len(value) > 3 and value.endswith('e') and value[-2] not in 'aeiou':
            found.append(value[:-1])
    return found


def _sounds(word):
    if re.search(r'[а-я]', word):
        return [_cyrillic_sound(word)]
    return _latin_sounds(word) if re.search(r'[a-z]', word) else [word]


def _joined(options, limit=256):
    """Склейки вариантов по словам: [['land'], ['kruiser']] → ['landkruiser'].

    Списком и в порядке вариантов: первым идёт прочтение из первых вариантов
    каждого слова, и результат не зависит от того, как легли строки в множестве."""
    found = []
    for combo in itertools.islice(itertools.product(*options), limit):
        value = ''.join(combo)
        if value not in found:
            found.append(value)
    return found


def _in_order(cars):
    """Машины без повторов, в порядке файла данных."""
    unique = {car['order']: car for car in cars}
    return [unique[order] for order in sorted(unique)]


def _distance(left, right, limit):
    """Расстояние Левенштейна или limit + 1, если оно больше предела."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, start=1):
        current = [row]
        for column, other in enumerate(right, start=1):
            current.append(min(previous[column] + 1, current[column - 1] + 1,
                               previous[column - 1] + (char != other)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


# ── Буквы кода: «к5», «х70», «рх 350», «црв» ─────────────────────────────────
#
# Коды моделей набирают русскими буквами двумя способами сразу — по виду («х5»,
# «с5») и по звуку («рх» — «эр-икс», «ес»), поэтому у буквы до двух прочтений.
# Первым стоит прочтение по звуку: «мерседес глс» — GLS, а не GLC.
_CODE_LETTERS = {
    'а': 'a', 'б': 'b', 'в': 'vb', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'j', 'з': 'z',
    'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'nh', 'о': 'o', 'п': 'p',
    'р': 'rp', 'с': 'sc', 'т': 't', 'у': 'uy', 'ф': 'f', 'х': 'xh', 'ц': 'c', 'э': 'e',
}


def _is_code(word):
    """Код, а не слово: с цифрой либо из одной-трёх букв («es», «рх», «к5»)."""
    return bool(re.search(r'\d', word)) or len(word) <= 3


def _code_readings(word):
    """Латинские прочтения кода, набранного с русскими буквами: «рх» → ['rx', …]."""
    options = []
    for char in word:
        if char in _CODE_LETTERS:
            options.append(_CODE_LETTERS[char])
        elif re.match(r'[a-z0-9]', char):
            options.append(char)
        else:
            return []
    return _joined(options)


def _spellings(words):
    """Окно слов вопроса → (точные написания, звуковые ключи)."""
    exact, sound = [], []
    for word in words:
        if re.search(r'[а-я]', word):
            readings = _code_readings(word) if _is_code(word) else []
            exact.append(readings + [word])
        else:
            exact.append([word])
        sound.append(_sounds(word))
    return _joined(exact), _joined(sound)


# Имена машин склоняют: «на тойоте», «у нексии», «на солярисе». Окончание снимаем
# с последнего слова окна и пробуем слово без него, с «а» и с «я/й» на конце.
_CASE_ENDINGS = ('ами', 'ях', 'ам', 'ах', 'ом', 'ем', 'ой', 'ей', 'ов', 'ию', 'ии',
                 'а', 'я', 'у', 'ю', 'е', 'ы', 'и')


def _case_forms(word):
    """Слово вопроса в именительном падеже: «тойоте» → «тойота», «нексии» →
    «нексия», «хендае» → «хендай». Лишние формы безвредны — им не с чем совпасть."""
    if len(word) < 4 or not re.fullmatch(r'[а-я]+', word):
        return []
    forms = []
    for ending in _CASE_ENDINGS:
        stem = word[:-len(ending)]
        if not word.endswith(ending) or len(stem) < 3:
            continue
        if ending in ('ии', 'ию'):
            found = [stem + 'ия']
        elif ending in ('я', 'ю', 'е'):
            found = [stem, stem + 'а', stem + 'й', stem + 'я']
        else:
            found = [stem, stem + 'а']
        forms += [form for form in found if form not in forms and form != word]
    return forms


def _readings(words):
    """Окно как набрано и с последним словом в именительном падеже."""
    return [list(words)] + [list(words[:-1]) + [form] for form in _case_forms(words[-1])]


def _never_alone(words):
    return any(form in _NEVER_ALONE for word in words for form in [word] + _case_forms(word))


# ─────────────────────────────────────────────────────────────────────────────
# Индексы имён
# ─────────────────────────────────────────────────────────────────────────────

def _index_models(book):
    exact, sound = {}, {}
    for car in book['cars']:
        car['sounds'] = set(_joined([_sounds(word) for word in _words(car['main'])])) \
            if re.search(r'[a-zа-я]{3}', _key(car['main'])) else set()
        car['alt_keys'] = {_name_key(part) for part in car['alternatives']}
        exact.setdefault(car['key'], []).append(car)
        # Полное имя со скобками — единственный путь к строке-уточнению:
        # «Focus (North America)» по одному «focus» не отвечает.
        full = _name_key(car['model'])
        if full != car['key']:
            exact.setdefault(full, []).append(car)
        for value in car['sounds']:
            sound.setdefault(value, []).append(car)
    # «Focus» и «Focus (North America)» — разные строки с разными годами. По
    # имени без уточнения отвечает основная; строка с уточнением — только когда
    # основной нет вовсе («Liberty (Patriot)» и «Liberty (North America)»).
    for index in (exact, sound):
        for value, found in index.items():
            plain = [car for car in found if not car['qualified']]
            by_brand = {car['brand'] for car in plain}
            index[value] = plain + [car for car in found
                                    if car['qualified'] and car['brand'] not in by_brand]
    book['model_exact'], book['model_sound'] = exact, sound


# Как марку называют по-русски и сокращённо. Сюда — только то, чего звуковой ключ
# не даёт («шевроле», «пежо», «хендай») и короткие имена составных марок.
_BRAND_NAMES = {
    'Hyundai': 'хендай хендэ хундай хюндай хендаи хундаи хёндэ',
    'Volkswagen': 'фольксваген фольцваген фольц vw',
    'Chevrolet': 'шевроле шеви шевролет',
    'Renault': 'рено',
    'Peugeot': 'пежо',
    'Citroen': 'ситроен ситроэн',
    'Daewoo': 'дэу деу дэо део даево',
    'Mitsubishi': 'митсубиси мицубиси митсубиши мицубиши митсу',
    'Mercedes-Benz': 'mercedes мерседес мерс бенц мерседесбенц',
    'BMW': 'бмв бэха беха',
    'Skoda': 'шкода',
    'Porsche': 'порше порш',
    'Jaguar': 'ягуар',
    'Chrysler': 'крайслер',
    'SsangYong': 'ссангйонг ссангенг саньенг сангйонг сангенг',
    'Land Rover': 'лендровер лэндровер',
    'Great Wall': 'грейтволл грейтвол',
    'BYD': 'бид бивайди бьюди бюд',
    'Hongqi': 'хунци хонгчи хончи хонки',
    'LiXiang': 'лисян лисянг лиауто liauto ликсян',
    'Jetour': 'джетур жетур',
    'Haval': 'хавейл хавэйл',
    'Changan': 'чанъань чангань',
    'FAW': 'фав',
    'GAC': 'гак',
    'DongFeng': 'донгфенг дунфэн донфенг дунфен',
    'Zeekr': 'зикр зикер',
    'Xpeng': 'икспенг сяопэн',
    'Voyah': 'воя',
    'Leapmotor': 'липмотор',
    'Wuling': 'вулинг улинг',
    'Buick': 'бьюик',
    'Genesis': 'дженезис генезис',
    'Lada (VAZ)': 'lada vaz лада ваз жигули',
    'Rolls-Royce': 'роллсройс ролсройс',
    'Bentley': 'бентли',
    'Dodge': 'додж',
    'Dacia': 'дачия дация',
    'Jaecoo': 'джейку жаеку',
    'Belgee': 'белджи',
    'Seres Aito': 'seres aito серес аито',
    'Alfa Romeo': 'альфаромео',
    'Iran Khodro': 'иранходро',
    'Subaru': 'субарик',
    'Infiniti': 'инфинити',
    'Cadillac': 'кадилак',
    'Suzuki': 'сузуки',
    'Lexus': 'лексус',
    'Geely': 'джили',
    'Chery': 'чери черри',
    'JAC': 'джак',
    'Exeed': 'эксид',
    'Tesla': 'тесла',
    'Ravon': 'равон',
    'Jeep': 'джип',
    'Hummer': 'хаммер',
    'Brilliance': 'бриллианс брилианс',
    'Zotye': 'зоти зотье',
    'Acura': 'акура',
    'Daihatsu': 'дайхатсу дайхацу',
}

# Марки, чьё имя — обычное слово («газ», «джип», «мини», «танк») или совпадает с
# моделью другой марки («Jetta», «Datsun», «Genesis»): маркой их делает только
# модель рядом, а список «всех моделей марки» по такому слову не отдаётся.
_AMBIGUOUS_BRANDS = frozenset((
    'ГАЗ', 'Jeep', 'MINI', 'Smart', 'Tank', 'Ora', 'Nio', 'Neta', 'Rover', 'Saturn',
    'Scion', 'Genesis', 'Jetta', 'Datsun', 'Livan', 'MG', 'AC', 'ZX', 'LTI', 'Beijing',
    'Maybach', 'Vauxhall', 'Mercury', 'Polestar',
))


def _index_brands(book):
    exact, sound = {}, {}
    for brand in book['by_brand']:
        full = _key(brand)
        exact.setdefault(full, brand)
        for value in _joined([_sounds(word) for word in _words(brand)]):
            if len(value) >= 3:
                sound.setdefault(value, brand)
        for name in (_BRAND_NAMES.get(brand) or '').split():
            exact.setdefault(_key(name), brand)
    book['brand_exact'], book['brand_sound'] = exact, sound


# Модели, которые по-русски называют не так, как они записаны в классификаторе.
# Слева — как пишут (слова через пробел: «ленд крузер»), справа — марка и модель;
# марка None — модель с таким именем у любой марки («дастер» — Renault и Dacia).
_MODEL_NAMES = (
    ('прадо|ленд крузер прадо|лэнд крузер прадо|лендкрузер прадо', 'Toyota', 'Land Cruiser Prado'),
    ('ленд крузер|лэнд крузер|лендкрузер|крузак', 'Toyota', 'Land Cruiser'),
    ('хайлендер|хайландер', 'Toyota', 'Highlander'),
    ('рав 4|рав4|раф 4|раф4', 'Toyota', 'RAV 4'),
    ('марк 2|марк два|марк ii|mark 2', 'Toyota', 'Mark II'),
    ('марк х|марк икс|mark x', 'Toyota', 'Mark X'),
    ('хайлюкс|хайлукс', 'Toyota', 'Hilux'),
    ('хайс|хайэйс|хиас', 'Toyota', 'HiAce'),
    ('чайзер', 'Toyota', 'Chaser'),
    ('краун', 'Toyota', 'Crown'),
    ('секвойя', 'Toyota', 'Sequoia'),
    ('туксон|туссан|тусон|таксон', 'Hyundai', 'Tucson'),
    ('грандер|грандеур', 'Hyundai', 'Grandeur'),
    ('спортейдж|спортаж|спортедж|спортэйдж|спортеж', 'Kia', 'Sportage'),
    ('сид|сиид|ceed', 'Kia', "Cee'd"),
    ('лачетти|ласетти', None, 'Lacetti'),
    ('трейлблейзер', 'Chevrolet', 'TrailBlazer'),
    ('нива', 'Chevrolet', 'Niva'),
    ('нива', 'Lada (VAZ)', '2121 (4x4)'),
    ('иксрей|икс рей|хрей|x ray', 'Lada (VAZ)', 'XRAY'),
    ('нива тревел|нива трэвел', 'Lada (VAZ)', 'Niva Travel'),
    ('дастер', None, 'Duster'),
    ('жук|джук', 'Nissan', 'Juke'),
    ('ноут|нот', 'Nissan', 'Note'),
    ('икстрейл|икс трейл|х трейл|хтрейл|экстрейл|иксрейл', 'Nissan', 'X-Trail'),
    ('патфайндер', 'Nissan', 'Pathfinder'),
    ('лиф', 'Nissan', 'Leaf'),
    ('блюберд', 'Nissan', 'Bluebird'),
    ('скайлайн', 'Nissan', 'Skyline'),
    ('аутлендер|оутлендер|аутлэндер', 'Mitsubishi', 'Outlander'),
    ('мираж', 'Mitsubishi', 'Mirage'),
    ('степвагон|степвгн|степ вагон', 'Honda', 'Stepwgn'),
    ('стрим', 'Honda', 'Stream'),
    ('шатл|шаттл', 'Honda', 'Shuttle'),
    ('элизион|элюзион|элисион', 'Honda', 'Elysion'),
    ('джаз', 'Honda', 'Jazz'),
    ('туран', 'Volkswagen', 'Touran'),
    ('каравелла', 'Volkswagen', 'Caravelle'),
    ('фьюжн|фьюжен', 'Ford', 'Fusion'),
    ('эскейп', 'Ford', 'Escape'),
    ('рейнджер', 'Ford', 'Ranger'),
    ('аутбек|аутбэк', 'Subaru', 'Outback'),
    ('гелик|гелендваген', 'Mercedes-Benz', 'G-klasse'),
    ('кулрей|кулрэй', 'Geely', 'Coolray'),
    ('цинь|чин|кин', 'BYD', 'Qin'),
    ('цинь плюс|чин плюс|кин плюс', 'BYD', 'Qin Plus'),
    ('сигал|сигул', 'BYD', 'Seagull'),
    ('долфин|дельфин', 'BYD', 'Dolphin'),
    ('рендж ровер|рейндж ровер|ренж ровер', 'Land Rover', 'Range Rover'),
    ('ку3', 'Audi', 'Q3'), ('ку5', 'Audi', 'Q5'), ('ку7', 'Audi', 'Q7'), ('ку8', 'Audi', 'Q8'),
)

# Имена из словаря, которые в обычной речи значат и другое: «акцент», «фокус»,
# «жук». Машиной их делает только вопрос о тарифе или годе (accept).
_COMMON_NAMES = frozenset((
    'акцент', 'фокус', 'жук', 'гольф', 'поло', 'астра', 'соната', 'оптима', 'веста',
    'калина', 'нива', 'фит', 'джаз', 'стрим', 'лиф', 'ноут', 'нот', 'сид', 'спарк',
    'круз', 'рапид', 'рио', 'мираж', 'гелик', 'краун', 'шатл', 'шаттл', 'чин', 'кин',
    'дельфин', 'туран', 'крузак',
    'accent', 'focus', 'click', 'matrix', 'fit', 'jazz', 'polo', 'golf', 'rio',
))

# Ходовые модели, чьё русское имя ничего другого не значит: совпадения по
# звучанию хватает, чтобы назвать машину одним словом («королла», «приус»).
# Остальным совпавшим по звучанию словам нужен вопрос о тарифе или годе.
_KNOWN_NAMES = frozenset((
    'королла приус альфард эстима ярис авенсис аурис вокси ноах ипсум харриер венза '
    'фортунер авалон сиенна калдина аллион премио '
    'элантра крета гетц старекс палисад аванте стария '
    'церато серато соренто пиканто карнивал селтос мохав каденза стингер кворис '
    'ланос эпика оникс монза дамас эквинокс орландо малибу каптива '
    'приора ларгус гранта '
    'логан сандеро каптюр каптур аркана флюенс колеос '
    'кашкай альмера теана тиида сентра террано мурано цефиро вингроад эльгранд '
    'лансер паджеро делика '
    'джетта пассат тигуан терамонт '
    'октавия фабия кодиак карок суперб '
    'цивик сивик везел элизион '
    'форестер импреза легаси витара свифт '
    'мондео фиеста куга эксплорер экоспорт '
    'джолион ховер тигго арризо аризо эмгранд монджаро тугелла окаванго алсвин эадо '
    'чазор дашинг солано'
).split())

# Слова, которые без марки машиной не становятся НИКОГДА: названия моделей,
# совпавшие с обычными словами вики таксопарка, именами и названиями сервисов.
# «Фамилия» — Mazda Familia, «паспорт» — Honda Passport, «вольт» — Chevrolet Volt
# (и сервис Wolt), «болт» — Chevrolet Bolt (и сервис Bolt), «мира» — Daihatsu Mira.
_NEVER_ALONE = frozenset((
    'фамилия паспорт навигатор партнер эксперт бонус прогресс контур караван талисман '
    'элемент пилот лого сити классик компас транзит галакси аврора аура браво панда '
    'фантом номад шанс патриот купе квест титан верна аслан мира кара сейф гост тоска '
    'болт вольт трекер принц эскорт шума клик юань хан сафари армада фуга глория '
    'сильвия серена леон фелиция лиана карина вита селеста каспер пегас легенд '
    'легенда кольт сигма кадет омега лагуна символ бриз амулет фора вега родео аксиом '
    'неон калибр дарт нитро вайб пикник корона эхо дуэт опа блейд раш ист '
    'марк порте виста прерия микра куб марч стар лайф стрит дрим фри ван сил '
    'тан сонг сон рей чанс пони грейс фрид джейд логотип территория линия смайли дир '
    'поер сейл темпо '
    'bonus partner expert city life logo element pilot passport star ace one free dream '
    'coupe hatch classic safe deer sport max plus pro tour volt bolt note cube leaf '
    'march echo duet rush wish galaxy transit legend stream '
    'insight patriot volga vita aura neon rodeo panda bravo mira cara '
    'forte pride morning soul ray venue kona pony genesis scion datsun liberty quest'
).split())


def _index_aliases(book):
    """Русские имена моделей → машины: словарь выше и общий словарь алиасов вики
    (wiki/text.ALIAS_GROUPS — им же поиск на экране узнаёт «камри»)."""
    alias = {}

    def add(name, cars):
        words = wiki_directory.normalize(name).split()
        if not words or not cars:
            return
        bucket = alias.setdefault(''.join(words), [])
        bucket += [car for car in cars if car not in bucket]

    for names, brand, model in _MODEL_NAMES:
        key = _name_key(_name_parts(model)[0])
        cars = [car for car in book['model_exact'].get(key, ())
                if (brand is None or car['brand'] == brand)
                and (brand is None or car['model'] == model)]
        for name in names.split('|'):
            add(name, cars)
    for group in wiki_text.ALIAS_GROUPS:
        latin = [_key(name) for name in group if not re.search(r'[а-яё]', name)]
        cars = [car for name in latin for car in book['model_exact'].get(name, ())]
        brand = next((book['brand_exact'][name] for name in latin
                      if name in book['brand_exact']), None)
        if cars and brand:
            # «land rover» и «range rover» в одной группе: какое русское имя —
            # марка, а какое — модель, из неё не понять. Оба есть в словарях выше.
            continue
        for name in group:
            if not re.search(r'[а-яё]', name):
                continue
            if cars:
                add(name, cars)
            elif brand:
                book['brand_exact'].setdefault(_key(name), brand)
    book['alias'] = alias


# ─────────────────────────────────────────────────────────────────────────────
# Слова вопроса
# ─────────────────────────────────────────────────────────────────────────────

_L = 'a-zа-я0-9'


def _starts(pattern):
    return re.compile(r'(?<![%s])(?:%s)' % (_L, pattern))


def _whole(pattern):
    return re.compile(r'(?<![%s])(?:%s)(?![%s])' % (_L, pattern, _L))


# Тарифы классификатора. Длинное раньше короткого: «комфорт+» — не «комфорт».
# «Комфортный», «экономия», «бизнесмен», «электромобиль» — не тарифы.
_TARIFF_WORDS = (
    ('comfort_plus', r'комфорт\s*(?:\+|плюс)|к\+(?![%s])|(?:comfort|komfort)\s*(?:\+|plus)' % _L),
    ('comfort', r'комфорт(?!н|аб)|comfort(?!a)|komfort'),
    ('econom', r'эконом(?!и|н)|econom(?!i|y)|ekonom(?!i)'),
    ('business', r'бизнес(?!м)|business|biznes'),
    ('electro', r'электро(?![%s])|electro(?![%s])' % (_L, _L)),
    ('ultima', r'ультим[а-я]*|ultima|премьер[а-я]*|premier'),
    ('intercity', r'межгород[а-я]*|междугород[а-я]*|intercity'),
)
_TARIFF_PATTERNS = tuple((key, _starts(pattern)) for key, pattern in _TARIFF_WORDS)
_TARIFF_WORD = _starts(r'тариф[а-я]*')
# Тарифы, которых в классификаторе нет: о них он молчит, и это надо сказать.
_OTHER_TARIFF = _starts(r'доставк[а-я]*|курьер[а-я]*|грузов[а-я]*|минив[эе]н[а-я]*')

_CLASSIFIER_WORD = _starts(r'классификатор[а-я]*|klassifikator|жиктеуиш[а-я]*')

# «С какого года», «год выпуска», «не старше» — вопрос про год машины.
_YEAR_WORD = _starts(
    r'(?:с|со|от)\s+как(?:ого|их)\s+год[а-я]*|как(?:ого|ой|ие|их|ому)\s+год[а-я]*'
    r'|год[а-я]*\s+выпуск[а-я]*|выпуск[а-я]*\s+(?:\d{4}\s+)?год[а-я]*|год[а-я]*\s+(?:авто|машин)[а-я]*'
    r'|не\s+старше|не\s+младше|(?:миним|максим)[а-я]*\s+(?:год|возраст)[а-я]*'
    r'|возраст[а-я]*\s+(?:авто|машин)[а-я]*|кай\s+жыл[а-я]*|нешинши\s+жыл[а-я]*|жылдан\s+бастап')

# «Подходит ли», «проходит», «берут», «допускается».
_FIT = _starts(
    r'подход[а-я]*|подойд[а-я]*|проход[а-я]*|пройд[а-я]*|допуск[а-я]*|допуст[а-я]*'
    r'|берут(?![%(l)s])|принима(?:ют|ется|ются|ем)(?![%(l)s])|возьмут(?![%(l)s])'
    r'|попада[а-я]*|попаде[а-я]*|годит[а-я]*|соответств[а-я]*|поддержива[а-я]*'
    r'|разрешен[а-я]*|доступ(?:ен|на|но|ны)(?![%(l)s])'
    r'|жарай[а-я]*|жарамды|сайкес|келе(?:ди)?\s+ме|оте(?:ди)?\s+ме|бола(?:ды)?\s+ма' % {'l': _L})

_CAR_NOUN = _starts(
    r'машин[а-я]*|авто(?![%(l)s])|автомобил[а-я]*|автомашин[а-я]*|тачк[а-я]*'
    r'|модел[ьией][а-я]*|марк[аиуе](?![%(l)s])|марок(?![%(l)s])|колик[а-я]*' % {'l': _L})

# «Какие машины», «список моделей» — просят перечень, а не одну машину.
_LISTED_NOUN = (r'(?:машин[а-я]*|авто(?![%(l)s])|автомобил[а-я]*|автомашин[а-я]*|модел[а-я]*'
                r'|марк[аи]?(?![%(l)s])|марок(?![%(l)s]))' % {'l': _L})
# Между «какие» и «машины» слов нет: «какие документы на авто нужны» — про
# документы, а не просьба о перечне.
_LIST = _starts(
    r'как(?:ие|их)\s+%(noun)s|(?:список|перечень)\s+(?:[а-я]+\s+)?%(noun)s'
    r'|кандай\s+(?:колик|машина)[а-я]*' % {'noun': _LISTED_NOUN})
_WHICH = _whole(r'какие|каких|какая|какой|кандай')

# Темы, у которых машина, год и тариф стоят в вопросе по своей причине: цена
# аренды («аренда кобальт 2023 комфорт»), комиссия, фотоконтроль, штраф, ДТП.
# Такой вопрос — не к классификатору, пока в нём не спросили про год машины
# прямо. Про аренду допускается и «подходит ли»: арендованную машину тоже
# спрашивают, возьмут ли её в тариф.
_RENT = _starts(r'аренд[а-я]*|выкуп[а-я]*')
_OTHER_TOPIC = _starts(r'комисс[а-я]*|комис[а-я]*|процент[а-я]*|фотоконтрол[а-я]*'
                       r'|дкк(?![%(l)s])|штраф[а-я]*|дтп(?![%(l)s])|авари[а-я]*' % {'l': _L})

# Слова, которые ничего не называют: по ним вопрос не становится «о другом».
_FILLER = frozenset((
    'для это эта этот эти при под над без или как что где когда какой какая какие каких '
    'какого какому каком какую сколько ли же бы вот ну а и в во на по за из от до у к ко с со '
    'о об не ни да нет то уже еще только тоже также там тут здесь есть был была было были '
    'будет будут если чтобы потому почему зачем кто кого кому чем чей его ее их он она они '
    'оно мне меня мой моя мое мы нам нас наш наша вы вам вас ваш ты тебе тебя '
    'подскажи подскажите скажи скажите пожалуйста плиз привет здравствуйте добрый день '
    'вечер утро спасибо можно нужно надо нужен нужна нужны хочу хочет хотят хотел хотела '
    'интересует узнать уточнить уточни уточните проверь проверьте проверить посмотри '
    'посмотрите сейчас сегодня теперь вообще именно точно ок хорошо такой такая такие '
    'такого свой своя свою своей своего этом этой этого том той того всех все весь '
    'водитель водителя водителю водителей водители водителем клиент клиента человек '
    'спрашивает спросил спросила говорит звонит пишет интересуется уточняет просит '
    'работать работает работу работы выйти выходить ездить таксовать подключить '
    'подключиться подключение подключения подключают зарегистрировать регистрация '
    'регистрации добавить поставить встать стать яндекс яндекса яндексе yandex про pro go '
    'такси taxi город городе города городу городах казахстан казахстане рк кз '
    'год года году годов годы годах лет выпуска выпуск выпущен выпущена новее старше '
    'младше тариф тарифа тарифе тарифу тарифы тарифов тарифом тарифам тарифах класс '
    'класса классе классу будет ли сможет может могут возможно значит получается '
    'ма ме ба бе па пе уш ушин бул осы сол ол деп жане мен калай канша кандай кай неше '
    'бар жок керек туралы бойынша жыл жылы жылгы жылдын'
).split())

# Города Казахстана, которых в классификаторе нет: вопрос «в Петропавловске»
# обязан получить «такого города в классификаторе нет», а не годы всех остальных.
_OTHER_CITIES = (
    'Петропавловск', 'Жезказган', 'Балхаш', 'Кентау', 'Каскелен', 'Конаев', 'Капшагай',
    'Сатпаев', 'Степногорск', 'Риддер', 'Щучинск', 'Талгар', 'Есик', 'Жанаозен', 'Аксай',
    'Кульсары', 'Сарань', 'Лисаковск', 'Аркалык', 'Шахтинск', 'Жаркент', 'Байконур',
)

_YEAR = re.compile(r'^(19[89]\d|20\d\d)(?:г|гг|года|год|году|жыл[а-я]*)?$')
_YEAR_TAIL = re.compile(r'^(?:г|гг|год|года|году|годов|жыл[а-я]*)$')


def _year_limit():
    return currency.today().year + 1


def _tokens(base):
    out, position = [], 0
    for word in base.split(' '):
        if word:
            out.append({'word': word, 'start': position, 'end': position + len(word),
                        'kind': None, 'signal': False})
        position += len(word) + 1
    return out


def _mark(tokens, start, end, kind):
    for token in tokens:
        if token['start'] < end and token['end'] > start and token['kind'] is None:
            token['kind'] = kind


@lru_cache(maxsize=4)
def _city_patterns(names):
    return wiki_directory.city_patterns(names)


def _find_cities(base, tokens, names, kind):
    """Города в вопросе, в порядке упоминания. Латиницу («almaty») читаем
    транслитом по словам — как общий разбор справочников."""
    found = []
    for pattern, name in _city_patterns(tuple(names)):
        for match in pattern.finditer(base):
            found.append((match.start(), name))
            _mark(tokens, match.start(), match.end(), kind)
    for token in tokens:
        if token['kind'] is None and re.search(r'[a-z]', token['word']):
            spelled = wiki_directory.normalize(
                wiki_text.transliterate_latin_to_cyrillic(token['word']))
            for pattern, name in _city_patterns(tuple(names)):
                if pattern.fullmatch(spelled):
                    found.append((token['start'], name))
                    token['kind'] = kind
                    break
    ordered = []
    for _position, name in sorted(found):
        if name not in ordered:
            ordered.append(name)
    return ordered


# ─────────────────────────────────────────────────────────────────────────────
# Машина в тексте вопроса
# ─────────────────────────────────────────────────────────────────────────────

# Насколько твёрдо названа машина:
#   1 — марка и модель («тойота камри»);
#   2 — модель из словаря, латиницей или кодом с цифрой («камри», «cobalt», «к5»);
#   3 — имя, совпавшее только по звучанию, либо ходовое слово («акцент»).
SURE, NAMED, PROBABLE = 1, 2, 3

_MERCEDES = 'Mercedes-Benz'
_CLASS_WORD = re.compile(r'^(?:класс[а-я]*|klasse|klass|class)$')
# Буквы-предлоги: «в класс комфорт», «с класс» — не Mercedes B и C без марки.
_PREPOSITION_LETTERS = frozenset('вскоуаия')
_LEXUS_CODES = frozenset(('es', 'gs', 'is', 'rx', 'ct', 'gx', 'hs', 'ls', 'lx', 'nx', 'lc'))


def _free(tokens, start, size):
    window = tokens[start:start + size]
    return window if len(window) == size and all(t['kind'] is None for t in window) else None


def _take(window, kind):
    for token in window:
        token['kind'] = kind


def _among(cars, brand):
    return [car for car in cars if brand is None or car['brand'] == brand]


def _named(book, words, brand=None):
    """Модель по окну слов — точным написанием или словарным именем."""
    found = []
    for reading in _readings(words):
        exact, _sound = _spellings(reading)
        for value in exact:
            for car in _among(book['model_exact'].get(value, ()), brand):
                if car not in found:
                    found.append(car)
        for car in _among(book['alias'].get(''.join(reading), ()), brand):
            if car not in found:
                found.append(car)
        if brand is not None:
            # Второе имя из скобок («Liberty (Patriot)», «190 (W201)») — только при марке.
            for car in book['by_brand'].get(brand, ()):
                if car not in found and car['alt_keys'] & set(exact):
                    found.append(car)
        if found:
            break
    return _in_order(found)


def _sounded(book, words, brand=None):
    found = []
    for reading in _readings(words):
        _exact, sound = _spellings(reading)
        for value in sound:
            for car in _among(book['model_sound'].get(value, ()), brand):
                if car not in found:
                    found.append(car)
        if found:
            break
    return _in_order(found)


def _closest(book, words, brand):
    """Модель марки, отличная от набранного на букву-две: «кулрей» — Coolray,
    «патфайндер» — Pathfinder. Только при названной марке: по всей базе на одну
    букву от имени машины лежит слишком много обычных слов («символ» — Symbol)."""
    sound = set()
    for reading in _readings(words):
        sound |= set(_spellings(reading)[1])
    best, cars = None, []
    for value in sorted(sound):
        if len(value) < 5 or not re.search(r'[a-z]{4}', value):
            continue
        limit = 2 if len(value) >= 9 else 1
        for car in book['by_brand'].get(brand, ()):
            for other in car['sounds']:
                distance = _distance(value, other, limit)
                if distance > limit:
                    continue
                if best is None or distance < best:
                    best, cars = distance, [car]
                elif distance == best and car not in cars:
                    cars.append(car)
    # Две разные модели на равном расстоянии — не угадываем.
    return cars if len(cars) == 1 else []


def _brand_special(book, brand, words):
    """Имена, которые марка читает по-своему: «бмв 5 серии», «мерседес е 200»,
    «тесла 3», «лексус рх 350»."""
    cars = book['by_brand'].get(brand, ())
    first = words[0]
    exact, _sound = _spellings([first])

    def by_key(key):
        return [car for car in cars if car['key'] == key and not car['qualified']]

    if brand == 'BMW':
        match = re.match(r'^([1-8])(\d\d[a-z]?)?$', first)
        if match and (len(words) == 1 or re.match(r'^(?:сери[а-я]*|series|er)$', words[1])):
            return by_key(first) or by_key(match.group(1) + 'er')
    if brand == _MERCEDES:
        letters = []
        for value in exact:
            match = re.match(r'^([a-z]{1,3})\d{0,3}$', value)
            if match and match.group(1) not in letters:
                letters.append(match.group(1))
        # Одной букве нужен хвост — «класс» или объём мотора: «мерседес с» ничего
        # не называет. Двух-трёх букв хватает самих: «мерседес глс».
        tail_ok = (len(words) == 1 and (re.search(r'\d', first) or len(first) >= 2)) or (
            len(words) == 2 and (_CLASS_WORD.match(words[1]) or re.match(r'^\d{2,3}$', words[1])))
        if letters and tail_ok:
            for suffix in ('klasse', ''):
                for value in letters:
                    found = by_key(value + suffix)
                    if found:
                        return found
    if brand == 'Tesla' and len(words) == 1:
        for value in exact:
            found = by_key('model' + value)
            if found:
                return found
    if brand == 'Lexus':
        letters = [value[:2] for value in exact if re.match(r'^[a-z]{2}\d{0,3}[a-z]?$', value)]
        if len(words) == 2 and not re.match(r'^\d{3}[a-z]?$', words[1]):
            letters = []
        for value in letters:
            if value in _LEXUS_CODES:
                return by_key(value)
    return []


def _model_near_brand(book, tokens, brand, start, end):
    """Модель рядом с маркой: после неё («тойота камри») или перед («камри
    тойота»). Возвращает (машины, окно слов) или ([], None)."""
    for size in (4, 3, 2, 1):
        window = _free(tokens, end, size)
        if not window:
            continue
        words = [token['word'] for token in window]
        # Сначала то, что марка читает по-своему: «мерседес глс» по буквам
        # совпадает и с GLC, а «лексус рх 350» обязан забрать и объём мотора.
        cars = _brand_special(book, brand, words) if size <= 2 else []
        if not cars:
            cars = _named(book, words, brand)
        if not cars:
            cars = _sounded(book, words, brand)
        if not cars and size <= 3:
            cars = _closest(book, words, brand)
        if cars:
            return cars, window
    for size in (3, 2, 1):
        if start - size < 0:
            continue
        window = _free(tokens, start - size, size)
        if not window:
            continue
        words = [token['word'] for token in window]
        cars = _named(book, words, brand) or _sounded(book, words, brand)
        if cars:
            return cars, window
    return [], None


def _unanchored(book, tokens, start, size):
    """Модель без марки в окне слов → (машины, твёрдость) или ([], None)."""
    window = _free(tokens, start, size)
    if not window:
        return [], None
    words = [token['word'] for token in window]
    # Имя машины не начинается со служебного слова и не содержит слов самого
    # вопроса: «модели» по звучанию — Tesla Model Y, а «про» в «тигго 7 про» —
    # часть имени, но только после него.
    if words[0] in _FILLER or any(token['signal'] for token in window) or _never_alone(words):
        return [], None
    raw = ''.join(words)
    letters = sum(1 for word in words if re.search(r'[a-zа-я]{2}', word))

    for reading in _readings(words):
        cars = book['alias'].get(''.join(reading))
        if cars:
            common = size == 1 and reading[0] in _COMMON_NAMES
            return cars, PROBABLE if common else NAMED

    exact, _sound = _spellings(words)
    if re.search(r'\d', raw) and re.search(r'[a-zа-я]', raw):
        # Код с буквой и цифрой: «к5», «х70», «cs35 plus», «rav4».
        cars = _in_order([car for value in exact for car in book['model_exact'].get(value, ())])
        if cars:
            # Два знака («к5», «а4», «h1») — это и формат листа, и тег разметки.
            return cars, PROBABLE if len(raw) <= 2 else NAMED
        # «rx 350», «рх350» — Lexus с объёмом мотора.
        for value in exact:
            match = re.match(r'^([a-z]{2})\d{3}[a-z]?$', value)
            if match and match.group(1) in _LEXUS_CODES:
                return [car for car in book['by_brand'].get('Lexus', ())
                        if car['key'] == match.group(1)], NAMED
    elif raw.isdigit():
        # Номерные модели ВАЗ («2114», «21099») — годом им не быть, а вот суммой
        # («заказ на 2114 тенге») — вполне: без марки это слабое имя.
        cars = [car for car in book['model_exact'].get(raw, ())
                if car['brand'] == 'Lada (VAZ)' and len(raw) >= 4]
        return (cars, PROBABLE) if cars else ([], None)
    elif not re.search(r'[а-я]', raw):
        # Латиницей: имя машины, набранное как в классификаторе.
        cars = list(book['model_exact'].get(raw, ())) if len(raw) >= 3 else []
        if cars:
            return cars, PROBABLE if (raw in _COMMON_NAMES or len(raw) == 3) else NAMED
        return [], None

    # Кириллицей и не из словаря: только по звучанию. Имя — слово от четырёх
    # букв в начале окна («вокси», «джолион»); за ним идут цифра и «про»,
    # «плюс», «макс» («тигго 7 про»).
    if len(words[0]) < 4 or not re.fullmatch(r'[а-я]+', words[0]):
        return [], None
    cars = _sounded(book, words)
    if cars:
        known = all(any(form in _KNOWN_NAMES for form in [word] + _case_forms(word))
                    for word in words if re.fullmatch(r'[а-я]{4,}', word))
        return cars, NAMED if (letters >= 2 or known) else PROBABLE
    return [], None


def _mercedes_class(book, tokens):
    """«е класс», «s-class» без марки — Mercedes E и S. Русская буква-предлог
    («в класс», «с класс») так не читается: ей нужна марка рядом."""
    found = []
    for index in range(len(tokens) - 1):
        window = _free(tokens, index, 2)
        if not window or not _CLASS_WORD.match(window[1]['word']):
            continue
        letter = window[0]['word']
        if not re.match(r'^[a-zа-я]{1,3}$', letter) or letter in _PREPOSITION_LETTERS:
            continue
        exact, _sound = _spellings([letter])
        cars = [car for car in book['by_brand'].get(_MERCEDES, ())
                if any(car['key'] == value + 'klasse' for value in exact) and not car['qualified']]
        if len(cars) == 1:
            _take(window, 'model')
            found.append({'cars': cars, 'level': NAMED})
    return found


def _find_brands(book, tokens):
    found, index = [], 0
    while index < len(tokens):
        hit = None
        for size in (3, 2, 1):
            window = _free(tokens, index, size)
            if not window:
                continue
            words = [token['word'] for token in window]
            brand = None
            for reading in _readings(words):
                exact, sound = _spellings(reading)
                brand = next((book['brand_exact'][value] for value in sorted(exact)
                              if value in book['brand_exact']), None)
                if brand is None and all(re.search(r'[а-я]', word) and len(word) >= 3
                                         for word in reading):
                    brand = next((book['brand_sound'][value] for value in sorted(sound)
                                  if value in book['brand_sound']), None)
                if brand is not None:
                    break
            if brand is not None:
                hit = (brand, window, index, index + size)
                break
        if hit:
            found.append({'brand': hit[0], 'window': hit[1], 'start': hit[2], 'end': hit[3]})
            index = hit[3]
        else:
            index += 1
    return found


def _find_cars(book, tokens):
    """Машины и марки вопроса: ([{'cars', 'level'}], [марки без модели], [слова
    после марки, не узнанные как модель])."""
    found, loose, unknown = [], [], []
    brands = _find_brands(book, tokens)
    for hit in brands:
        cars, window = _model_near_brand(book, tokens, hit['brand'], hit['start'], hit['end'])
        if cars:
            _take(hit['window'], 'brand')
            _take(window, 'model')
            found.append({'cars': cars, 'level': SURE})
    found += _mercedes_class(book, tokens)
    for hit in brands:
        if hit['window'][0]['kind'] is not None:
            continue
        # Имя марки, совпавшее с моделью другой марки, сначала пробуем моделью:
        # «джетта» — Volkswagen Jetta, а не марка Jetta без модели.
        if hit['brand'] in _AMBIGUOUS_BRANDS:
            continue
        _take(hit['window'], 'brand')
        loose.append(hit['brand'])
        rest = [token['word'] for token in tokens[hit['end']:hit['end'] + 2]
                if token['kind'] is None and not token['signal']
                and token['word'] not in _FILLER and re.search(r'[a-zа-я]', token['word'])]
        unknown += rest[:1]
    index = 0
    while index < len(tokens):
        step = 1
        for size in range(min(4, len(tokens) - index), 0, -1):
            cars, level = _unanchored(book, tokens, index, size)
            if cars:
                _take(tokens[index:index + size], 'model')
                found.append({'cars': cars, 'level': level})
                step = size
                break
        index += step
    return found, loose, unknown


# ─────────────────────────────────────────────────────────────────────────────
# Разбор вопроса
# ─────────────────────────────────────────────────────────────────────────────

def analyze(question, book):
    """Что в вопросе есть для классификатора. Без базы — её и проверяют тесты."""
    base = wiki_directory.normalize(question)
    tokens = _tokens(base)
    city_names = [city['name'] for city in book['cities']]
    cities = _find_cities(base, tokens, city_names, 'city')
    other_cities = _find_cities(base, tokens, _OTHER_CITIES, 'other_city')

    tariffs = []
    for key, pattern in _TARIFF_PATTERNS:
        for match in pattern.finditer(base):
            covered = [token for token in tokens
                       if token['start'] < match.end() and token['end'] > match.start()]
            if covered and all(token['kind'] is None for token in covered):
                _take(covered, 'tariff')
                tariffs.append((match.start(), key))
    named = []
    for _position, key in sorted(tariffs):
        if key not in named:
            named.append(key)
    tariffs = named

    signals = {
        'classifier': bool(_CLASSIFIER_WORD.search(base)),
        'year_word': bool(_YEAR_WORD.search(base)),
        'fit': bool(_FIT.search(base)),
        'car_noun': bool(_CAR_NOUN.search(base)),
        'list': bool(_LIST.search(base)),
        'which': bool(_WHICH.search(base)),
        'other_tariff': bool(_OTHER_TARIFF.search(base)),
        'rent': bool(_RENT.search(base)),
        'other_topic': bool(_OTHER_TOPIC.search(base)),
    }
    for pattern in (_CLASSIFIER_WORD, _YEAR_WORD, _FIT, _CAR_NOUN, _LIST, _TARIFF_WORD,
                    _OTHER_TARIFF):
        for match in pattern.finditer(base):
            for token in tokens:
                if token['start'] < match.end() and token['end'] > match.start():
                    token['signal'] = True

    # Машины ищем до годов: «пежо 2008» — модель, а не год. Слова самого вопроса
    # («модель», «класс») пока не погашены — рядом с маркой они часть имени
    # («тесла модель 3», «мерседес е класс»).
    limit = _year_limit()
    for index, token in enumerate(tokens):
        match = _YEAR.match(token['word'])
        following = tokens[index + 1]['word'] if index + 1 < len(tokens) else ''
        if (match and token['kind'] is None and YEAR_FLOOR <= int(match.group(1)) <= limit
                and (match.group(0) != match.group(1) or _YEAR_TAIL.match(following))):
            token['kind'] = 'year'
    found, loose, unknown = _find_cars(book, tokens)
    for token in tokens:
        match = _YEAR.match(token['word'])
        if match and token['kind'] is None and YEAR_FLOOR <= int(match.group(1)) <= limit:
            token['kind'] = 'year'
    years = []
    for token in tokens:
        if token['kind'] == 'year':
            value = int(_YEAR.match(token['word']).group(1))
            if value not in years:
                years.append(value)

    leftover = [token['word'] for token in tokens
                if token['kind'] is None and not token['signal'] and len(token['word']) >= 3
                and token['word'] not in _FILLER and not token['word'].isdigit()]
    return {
        'question': str(question or ''),
        'cars': found,
        'brands': loose,
        'unknown': unknown,
        'cities': cities,
        'other_cities': other_cities,
        'tariffs': tariffs,
        'years': years[:MAX_YEARS],
        'leftover': leftover,
        **signals,
    }


def _strength(analysis):
    """(сильных признаков, слабых): тариф, «с какого года» и слово «классификатор»
    говорят сами; год, «подходит» и «машина» — только вместе или в коротком вопросе."""
    strong = sum(bool(analysis[name]) for name in ('tariffs', 'year_word', 'classifier'))
    medium = sum(bool(analysis[name]) for name in ('years', 'fit', 'car_noun'))
    return strong, medium


def accept(analysis, level):
    """Вправе ли вопрос назвать машину при такой твёрдости имени.

    Чем слабее названа машина, тем больше вопрос обязан сказать сам:

        «тойота камри»       марка и модель — тариф или «с какого года» (если
                             вопрос не рассказ о другом), год или «подходит» в
                             коротком вопросе, либо вопрос из одной машины
        «камри», «cobalt»    имя из словаря — то же, но год и «подходит» поодиночке
                             считаются только в вопросе без посторонних слов
        «акцент», «вокси»    ходовое слово или совпадение по звучанию — тариф
                             вместе с годом или «подходит», либо любой признак
                             в вопросе без посторонних слов

    «Водитель на тойоте камри 2015 года попал в ДТП» — про ДТП, хотя и машина, и
    год названы: посторонние слова перевешивают одиночный слабый признак.
    """
    strong, medium = _strength(analysis)
    extra = len(analysis['leftover'])
    # Тариф или «с какого года» говорят сами — пока вопрос не рассказ о другом:
    # «водитель на камри жалуется, что мало заказов в комфорте» — про заказы.
    spoken = bool(strong) and (bool(medium) or extra <= 2)
    if level == SURE:
        return spoken or (medium >= 2 and extra <= 3) or (medium >= 1 and extra <= 1) or extra == 0
    if level == NAMED:
        return spoken or (medium >= 2 and extra <= 2) or extra == 0
    return (bool(strong) and bool(medium) and extra <= 1) or (extra == 0 and bool(strong or medium))


def _another_topic(analysis):
    """Вопрос про аренду, комиссию или фотоконтроль, а не про год машины."""
    asked = analysis['year_word'] or analysis['classifier']
    return ((analysis['other_topic'] and not asked)
            or (analysis['rent'] and not (asked or analysis['fit'])))


def plan(analysis):
    """Что отдать помощнику: {'kind', 'cars', 'brand'} или None.

        cars     карточки названных машин
        brand    список моделей марки — модель не узнана или просят перечень
        tariff   список моделей тарифа — «какие машины подходят в бизнес»
        general  города, тарифы и правило — машина не названа
    """
    if _another_topic(analysis):
        return None
    cars = []
    for hit in analysis['cars']:
        if accept(analysis, hit['level']):
            cars += [car for car in hit['cars'] if car not in cars]
    if cars:
        return {'kind': 'cars', 'cars': cars[:MAX_CARS], 'more': max(0, len(cars) - MAX_CARS)}

    strong, medium = _strength(analysis)
    if analysis['brands']:
        # Марка без модели: перечень просят прямо («какие киа в комфорт»), либо
        # вопрос про машину этой марки, а модель код не узнал («киа спортаж
        # 2019 комфорт») — тогда её найдёт в списке сам помощник. Рассказ о
        # другом («водитель на киа жалуется на заказы в комфорте») — не повод.
        extra = len(analysis['leftover'])
        asked = bool(strong) and (analysis['list'] or analysis['which'] or extra <= 1
                                  or (medium and extra <= 2))
        guessed = bool(analysis['unknown']) and medium >= 2 and extra <= 2
        if asked or guessed:
            return {'kind': 'brand', 'brand': analysis['brands'][0]}
        return None
    if analysis['list'] and analysis['tariffs']:
        return {'kind': 'tariff'}
    if analysis['classifier']:
        return {'kind': 'general'}
    # Машина не названа, а вопрос о ней: «с какого года берут в комфорт», «какие
    # машины подходят». Постороннее слово без слова «машина» — уже другая тема:
    # «телефон 2019 года подходит для работы в комфорте» — про телефон.
    about_car = analysis['car_noun'] or not analysis['leftover']
    if analysis['list'] and (analysis['fit'] or analysis['year_word']) and not analysis['leftover']:
        return {'kind': 'general'}
    if about_car and analysis['tariffs'] and (
            analysis['year_word'] or (analysis['fit'] and (analysis['years'] or analysis['car_noun']))
            or (analysis['car_noun'] and analysis['years'])):
        return {'kind': 'general'}
    if analysis['year_word'] and analysis['car_noun']:
        return {'kind': 'general'}
    return None


def _context(question, search_query):
    question, context = str(question or ''), str(search_query or '')
    if question and context.endswith(question):
        context = context[:len(context) - len(question)]
    return context.strip()


def _continue(own, past):
    """Уточнение заменяет названные поля, остальные берёт из прошлого вопроса."""
    said = (own['cars'] or own['brands'] or own['cities'] or own['tariffs'] or own['years']
            or own['other_cities'] or own['other_tariff'] or own['fit'] or own['year_word'])
    if own['leftover'] or not said or _another_topic(own):
        return own
    if past is None or not plan(past):
        return own
    merged = dict(own)
    if not own['cars'] and not own['brands']:
        merged['cars'], merged['brands'], merged['unknown'] = (
            past['cars'], past['brands'], past['unknown'])
    if not own['years']:
        merged['years'] = past['years']
    if not own['tariffs'] and not own['other_tariff']:
        merged['tariffs'], merged['other_tariff'] = past['tariffs'], past['other_tariff']
    if not own['cities'] and not own['other_cities']:
        merged['cities'], merged['other_cities'] = past['cities'], past['other_cities']
    for name in ('classifier', 'year_word', 'fit', 'car_noun', 'list', 'which'):
        merged[name] = own[name] or past[name]
    merged['continued'] = True
    return merged


def analyze_question(question, search_query, book, *, history=None):
    """Разбор уточнений по отдельным репликам, в порядке разговора.

    «2015» → «а 2012?» заменяет год, а «2014 или 2015?» сохраняет оба: это
    нельзя различить в склеенном search_query. Посторонняя тема обрывает цепочку.
    history берётся до текущего вопроса; реплики помощника полями не управляют.
    search_query оставлен для вызовов без отдельной истории.
    """
    own = analyze(question, book)
    if history is not None:
        past = None
        for turn in history:
            if turn.get('role') != 'user' or not str(turn.get('text') or '').strip():
                continue
            past = _continue(analyze(turn['text'], book), past)
        return _continue(own, past)
    context = _context(question, search_query)
    if not context or context == str(question or ''):
        return own
    past = analyze(context, book)
    # В старом склеенном запросе границ реплик нет; прежнее правило города.
    past['cities'] = past['cities'][-1:]
    return _continue(own, past)


# ─────────────────────────────────────────────────────────────────────────────
# Расчёт: тот же, что у калькулятора
# ─────────────────────────────────────────────────────────────────────────────

def required_year(car, city, key):
    """С какого года модель берут в тариф в городе; None — тарифа у модели или
    в городе нет. Двойник requiredYear из ClassifierView.jsx."""
    base = car['years'].get(key)
    if base is None or key not in city['tariffs']:
        return None
    return max(YEAR_FLOOR, base + city['offset'])


def _verdict(required, years):
    """«машина 2015 года подходит» — по каждому году из вопроса."""
    if not years:
        return ''
    parts = ['%s года %s' % (year, 'подходит' if year >= required else 'не подходит')
             for year in years]
    return 'машина ' + ', '.join(parts)


def _listed(names):
    return ', '.join(names)


def _count(number, one, few, many):
    """«493 модели», «153 марки», «56 моделей»."""
    tail = number % 100
    if 11 <= tail <= 14:
        word = many
    else:
        word = {1: one, 2: few, 3: few, 4: few}.get(number % 10, many)
    return '%s %s' % (number, word)


def _tariff_order(book, asked):
    keys = [item['key'] for item in book['tariffs']]
    return [key for key in asked if key in keys] + [key for key in keys if key not in asked]


def _warning(car, key):
    note = car['warnings'].get(key)
    return ' Оговорка классификатора: %s.' % note.rstrip('.') if note else ''


_HEAD_RULE = 'Год выпуска «от N» значит «N и новее».'


def _car_city_lines(book, car, city, asked, years):
    names = book['tariff_names']
    lines, absent = [], []
    for key in _tariff_order(book, asked):
        label = '%s, %s, тариф %s' % (car['name'], city['name'], names[key])
        if key not in city['tariffs']:
            if key in asked:
                lines.append('%s: в городе %s тарифа %s в классификаторе нет.' % (
                    car['name'], city['name'], names[key]))
            continue
        required = required_year(car, city, key)
        if required is None:
            if key in asked:
                lines.append('%s: модель в этот тариф не внесена.' % label)
            else:
                absent.append(names[key])
            continue
        verdict = _verdict(required, years)
        lines.append('%s: от %s года выпуска%s.%s' % (
            label, required, ' — ' + verdict if verdict else '', _warning(car, key)))
    if absent:
        lines.append('%s, %s: в %s %s модель не внесена.' % (
            car['name'], city['name'], 'тариф' if len(absent) == 1 else 'тарифы', _listed(absent)))
    return lines


def _car_all_cities_lines(book, car, asked, years):
    """Город не назван: по тарифу — города, сгруппированные по требуемому году."""
    names = book['tariff_names']
    lines, absent = [], []
    for key in _tariff_order(book, asked):
        if key not in car['years']:
            if key in asked:
                lines.append('%s, тариф %s: модель в этот тариф не внесена.' % (
                    car['name'], names[key]))
            else:
                absent.append(names[key])
            continue
        groups, without = {}, []
        for city in book['cities']:
            required = required_year(car, city, key)
            if required is None:
                without.append(city['name'])
            else:
                groups.setdefault(required, []).append(city['name'])
        ordered = sorted(groups, reverse=True)
        verdicts = {_verdict(required, years) for required in ordered}
        # Вердикт один на все города — пишем его один раз, а не при каждом годе.
        same = len(verdicts) == 1 and len(ordered) > 1
        parts = []
        for required in ordered:
            verdict = '' if same else _verdict(required, years)
            parts.append('%s — от %s%s' % (_listed(groups[required]), required,
                                           ' (%s)' % verdict if verdict else ''))
        line = '%s, тариф %s: %s.' % (car['name'], names[key], '; '.join(parts))
        if same and years:
            line += ' Во всех этих городах %s.' % verdicts.pop()
        if without:
            line += (' В остальных городах тарифа %s в классификаторе нет.' % names[key]
                     if len(without) > len(book['cities']) / 2
                     else ' В городах %s тарифа %s в классификаторе нет.' % (
                         _listed(without), names[key]))
        lines.append(line + _warning(car, key))
    if absent:
        lines.append('%s: в %s %s модель не внесена.' % (
            car['name'], 'тариф' if len(absent) == 1 else 'тарифы', _listed(absent)))
    return lines


def _notes(book, analysis):
    """Оговорки к любому фрагменту: города и тарифа из вопроса в классификаторе нет."""
    lines = []
    if analysis['other_cities']:
        lines.append('%s %s в классификаторе нет. Города классификатора: %s.' % (
            'Города' if len(analysis['other_cities']) == 1 else 'Городов',
            _listed(analysis['other_cities']), _listed([city['name'] for city in book['cities']])))
    if analysis['other_tariff']:
        lines.append('Классификатор ведёт только тарифы: %s. По другим тарифам в нём данных нет.'
                     % _listed([item['name'] for item in book['tariffs']]))
    return lines


def _cities_of(book, analysis):
    by_name = {city['name']: city for city in book['cities']}
    return [by_name[name] for name in analysis['cities'] if name in by_name][:MAX_CITIES]


def car_fragment(book, car, analysis):
    """Карточка машины: (текст, опора). По строке на тариф, с маркой, моделью и
    городом в каждой.

    Помощник цитирует одну лучшую строку (answer.pick_excerpt), а сверка чисел
    смотрит только в текст фрагмента — год обязан стоять здесь буквально, для
    каждого города, о котором могут спросить.

    Опора — оговорки и строки СПРОШЕННЫХ тарифов: по ней сервер выбирает цитату
    под ответом (answer._evidence). Строки тарифов почти одинаковы, и на ответ
    «Да, Camry 2015 подходит в Комфорт» без года в нём строка «Комфорт+: … не
    подходит» совпадала не хуже и выигрывала длиной — под «да» стояла цитата
    «не подходит» (замер на Vertex 13.10.2026). Тариф не спросили — опоры нет, и
    цитата выбирается из всего текста. Модели уходит текст целиком.
    """
    cities = _cities_of(book, analysis)
    asked, years = analysis['tariffs'], analysis['years']
    notes = _notes(book, analysis)
    if not car['years']:
        lines = ['%s: модель есть в «Классификаторе авто», но ни в один тариф не внесена — '
                 'тарифами не поддерживается.' % car['name']]
        return '\n'.join(lines + notes), None
    # Спрошенный тариф даёт ровно одну строку на город, и такие строки идут
    # первыми (_tariff_order): год, «не внесена» или «тарифа в городе нет».
    count = len([key for key in asked if key in book['tariff_names']])
    if cities:
        lines = ['%s — данные «Классификатора авто», %s %s. %s' % (
            car['name'], 'город' if len(cities) == 1 else 'города',
            _listed([city['name'] for city in cities]), _HEAD_RULE)]
        lines += notes
        focus = list(notes)
        for city in cities:
            own = _car_city_lines(book, car, city, asked, years)
            lines += own
            focus += own[:count]
    else:
        lines = ['%s — данные «Классификатора авто». Требуемый год выпуска зависит от города. %s'
                 % (car['name'], _HEAD_RULE)]
        lines += notes
        own = _car_all_cities_lines(book, car, asked, years)
        lines += own
        focus = notes + own[:count]
    return '\n'.join(lines), ('\n'.join(focus) if count else None)


def car_text(book, car, analysis):
    """Текст карточки машины (car_fragment без опоры)."""
    return car_fragment(book, car, analysis)[0]


def _scope(book, analysis):
    """Город списка: названный, иначе Алматы — базовый, как на экране."""
    cities = _cities_of(book, analysis)
    if cities:
        return cities[0], 'город %s' % cities[0]['name']
    base = next((city for city in book['cities'] if city['offset'] == 0), book['cities'][0])
    return base, ('годы для города %s; в других городах год другой — для точного года '
                  'назовите город' % base['name'])


def _model_line(book, car, city, keys, years):
    names = book['tariff_names']
    parts = []
    for key in keys:
        required = required_year(car, city, key)
        if required is None:
            continue
        verdict = _verdict(required, years)
        warning = _warning(car, key)
        parts.append('%s от %s%s%s' % (
            names[key], required, ' (%s)' % verdict if verdict else '',
            '.' + warning.rstrip('.') if warning else ''))
    return parts


def brand_text(book, brand, analysis):
    """Модели марки с годами — на «какие киа проходят в комфорт» и когда модель
    названа так, что код её не узнал («киа спортаж»): помощник найдёт её в списке."""
    cars = book['by_brand'].get(brand, [])
    city, scope = _scope(book, analysis)
    asked = [key for key in analysis['tariffs'] if key in city['tariffs']]
    keys = asked or [key for key in _tariff_order(book, []) if key in city['tariffs']]
    names = book['tariff_names']
    title = '%s — модели в «Классификаторе авто»%s, %s. %s' % (
        brand, ', %s %s' % ('тариф' if len(asked) == 1 else 'тарифы',
                            _listed([names[key] for key in asked])) if asked else '',
        scope, _HEAD_RULE)
    lines, absent = [title], []
    for car in cars[:MAX_LIST]:
        parts = _model_line(book, car, city, keys, analysis['years'])
        if parts:
            lines.append('%s: %s.' % (car['name'], ', '.join(parts)))
        else:
            absent.append(car['name'])
    if absent:
        where = ('в %s %s' % ('тариф' if len(asked) == 1 else 'тарифы',
                              _listed([names[key] for key in asked]))
                 if asked else 'ни в один тариф города %s' % city['name'])
        lines.append('Не внесены %s: %s.' % (where, _listed(absent)))
    if len(cars) > MAX_LIST:
        lines.append('Показаны первые %s из %s — назовите модель.' % (
            MAX_LIST, _count(len(cars), 'модели', 'моделей', 'моделей')))
    if brand in book['catch_all'] and not book['catch_all'][brand]:
        lines.append('Остальные модели %s, которых нет в этом списке, в тарифы не внесены.' % brand)
    missing = [names[key] for key in analysis['tariffs'] if key not in city['tariffs']]
    if missing:
        lines.append('В городе %s %s %s в классификаторе нет.' % (
            city['name'], 'тарифа' if len(missing) == 1 else 'тарифов', _listed(missing)))
    return '\n'.join(lines + _notes(book, analysis))


def tariff_text(book, analysis):
    """Модели тарифа. Короткий тариф («Бизнес», «Электро») — списком, длинный —
    числом и просьбой назвать марку: 494 строки ответом не пересказать."""
    names = book['tariff_names']
    city, scope = _scope(book, analysis)
    lines = []
    for key in analysis['tariffs'][:2]:
        if key not in city['tariffs']:
            lines.append('В городе %s тарифа %s в классификаторе нет.' % (city['name'], names[key]))
            continue
        cars = [car for car in book['cars'] if key in car['years']]
        if len(cars) > MAX_TARIFF_LIST:
            lines.append('В тарифе %s в «Классификаторе авто» %s — такой список в ответ '
                         'не помещается. Назовите марку или модель: год для неё есть в '
                         'классификаторе. Полный список — в статье «Классификатор авто», блок '
                         '«Списки по тарифам».' % (
                             names[key], _count(len(cars), 'модель', 'модели', 'моделей')))
            continue
        lines.append('Модели тарифа %s в «Классификаторе авто», %s. %s' % (
            names[key], scope, _HEAD_RULE))
        for car in sorted(cars, key=lambda item: item['name'].lower()):
            required = required_year(car, city, key)
            verdict = _verdict(required, analysis['years'])
            lines.append('%s, тариф %s: от %s%s.%s' % (
                car['name'], names[key], required, ' (%s)' % verdict if verdict else '',
                _warning(car, key)))
    return '\n'.join(lines + _notes(book, analysis))


def _shift_text(offset):
    if not offset:
        return 'базовые требования'
    years = abs(offset)
    word = 'год' if years == 1 else 'года' if years < 5 else 'лет'
    return 'требования на %s %s %s, чем в Алматы' % (years, word, 'строже' if offset > 0 else 'мягче')


def general_text(book, analysis):
    """Города, тарифы и правило — когда машина в вопросе не названа."""
    names = book['tariff_names']
    lines = [
        '«Классификатор авто» показывает по марке, модели, городу и году выпуска, в какие '
        'тарифы допускается автомобиль. Тарифы классификатора: %s.'
        % _listed([item['name'] for item in book['tariffs']]),
        'Требуемый год выпуска зависит от модели и от города: чтобы получить год, назовите '
        'марку и модель автомобиля, тариф и город.',
        'Города и тарифы классификатора:',
    ]
    chosen = _cities_of(book, analysis) or book['cities']
    for city in chosen:
        lines.append('%s: %s — %s.' % (city['name'], _listed([names[key] for key in city['tariffs']]),
                                       _shift_text(city['offset'])))
    if book['other'] and book['other']['years']:
        base = next((city for city in book['cities'] if city['offset'] == 0), book['cities'][0])
        parts = ['%s от %s' % (names[key], required_year(book['other'], base, key))
                 for key in _tariff_order(book, []) if required_year(book['other'], base, key)]
        if parts:
            lines.append('Строка «Другие модели» (те, которых в классификаторе нет), город %s: %s.'
                         % (base['name'], ', '.join(parts)))
    lines.append('В классификаторе %s, %s.' % (
        _count(book['models_total'], 'модель', 'модели', 'моделей'),
        _count(book['brands_total'], 'марка', 'марки', 'марок')))
    return '\n'.join(lines + _notes(book, analysis))


# ─────────────────────────────────────────────────────────────────────────────
# Фрагменты для помощника
# ─────────────────────────────────────────────────────────────────────────────

def _row(index, *, article, heading, text, car=None, analysis=None, evidence=None):
    cities = (analysis or {}).get('cities') or []
    years = (analysis or {}).get('years') or []
    return {
        'chunk_id': -(_CHUNK_BASE + index + 1),
        # Статья настоящая: чип под ответом открывает её, а история проверяет
        # доступ к ней на сейчас — как у любой статьи.
        'article_id': article['id'],
        'chunk_idx': 0,
        'title': article['title'],
        'slug': article['slug'],
        'heading_path': heading,
        'text': text,
        # Строки, которыми фрагмент подтверждает ответ, — для цитаты (car_fragment).
        'evidence': evidence,
        'requires_ack': False,
        'historical': False,
        'similarity': None,
        'found_by': [BRANCH],
        'directory_hit': True,
        'source_kind': SOURCE_KIND,
        'tab': SOURCE_TAB,
        # Машина, год и город для экрана: чип открывает калькулятор уже на них.
        # Год — только когда он в вопросе один, город — id из файла данных.
        'ref_key': '%s|%s' % (car['brand'], car['model']) if car else None,
        'ref_id': years[0] if car and len(years) == 1 else None,
        'ref_city': cities[0] if car and len(cities) == 1 else None,
        'space_id': None,
        # Имена машины — по ним сервер решает, подшивать ли фрагмент к ответу,
        # которого модель им не подписала (answer.build_sources).
        'names': [car['brand'], car['main']] if car else [],
    }


def build_ai_rows(analysis, book, *, article):
    """Фрагменты помощника из разобранного вопроса. Без базы — для тестов."""
    decision = plan(analysis)
    if not decision:
        return []
    city_ids = {city['name']: city['id'] for city in book['cities']}
    shown = dict(analysis, cities=[city_ids[name] for name in analysis['cities']
                                   if name in city_ids][:MAX_CITIES])
    rows = []
    if decision['kind'] == 'cars':
        for car in decision['cars']:
            text, evidence = car_fragment(book, car, analysis)
            rows.append(_row(len(rows), article=article, heading=car['name'], text=text,
                             evidence=evidence, car=car, analysis=shown))
        if decision['more']:
            rows[-1]['text'] += ('\nС таким названием в классификаторе есть ещё %s — назовите '
                                 'марку.' % _count(decision['more'], 'модель', 'модели', 'моделей'))
        return rows
    if decision['kind'] == 'brand':
        text = brand_text(book, decision['brand'], analysis)
        heading = 'Марка %s' % decision['brand']
    elif decision['kind'] == 'tariff':
        text = tariff_text(book, analysis)
        heading = 'Тариф %s' % _listed([book['tariff_names'][key] for key in analysis['tariffs'][:2]])
    else:
        text = general_text(book, analysis)
        heading = 'Города и тарифы'
    return [_row(0, article=article, heading=heading, text=text)] if text else []


_ARTICLE_SQL = 'SELECT id, title, slug FROM wiki_articles WHERE slug = %s'


def readable_article(cursor, article_ids):
    """Статья-классификатор, если она в периметре помощника этого человека."""
    cursor.execute(_ARTICLE_SQL, (schema.CLASSIFIER_SLUG,))
    row = cursor.fetchone()
    if not row or row[0] not in article_ids:
        return None
    return {'id': row[0], 'title': row[1] or 'Классификатор авто', 'slug': row[2]}


def ai_rows(cursor, question, *, article_ids, search_query=None, history=None):
    """Фрагменты классификатора к вопросу помощника — или [].

    article_ids — периметр помощника (perimeter.assistant_perimeter): уже без
    черновиков, строгого режима и статей с рубильником «не для ИИ», уже суженный
    до пространства. База спрашивается, только когда вопрос вообще про машину.
    """
    data = book()
    if data is None:
        return []
    analysis = analyze_question(question, search_query, data, history=history)
    if not plan(analysis):
        return []
    article = readable_article(cursor, article_ids)
    if article is None:
        return []
    return build_ai_rows(analysis, data, article=article)
