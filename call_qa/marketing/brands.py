# -*- coding: utf-8 -*-
"""Бренд таксопарка по сырому написанию из CRM (ТЗ #317, ФТ-05).

Поле «Таксопарк привлечения» в amoCRM — мультиселект, и живёт в нём не бренд, а
«бренд + город + услуга»: «itaxi (алматы)», «itaxi 2 астана», «jana taxi
алматы», а у части сделок — склейка всех городов сразу на полстроки. Пока
словарь заводил каждое новое написание отдельным парком, фильтр «iTaxi» ловил
около трети сделок iTaxi (прод, 24.09.2026: 7 092 сделки у брендов ТЗ против
14 355 у их городских вариантов).

Правило:
  * семь брендов ТЗ узнаются по НАЧАЛУ названия — что бы ни шло следом
    (город, «2», «доставка», «vip»);
  * у остальных парков отбрасываются города и служебные слова, остаток и есть
    бренд («достойный астана» → «Достойный»);
  * склейка мультиселекта относится к бренду первого значения;
  * одно название города без бренда — не парк: такая сделка остаётся в
    «Не определено», а не заводит «парк Усть-Каменогорск».

Модуль чистый (ни базы, ни сети): его зовут и связыватель, и чистка словаря,
и тесты.
"""

import re

# Бренды из ФТ-05. Префиксы сравниваются с началом значения после чистки
# (нижний регистр, «ё»→«е», подчёркивания и лишние пробелы убраны), поэтому
# «itaxi 2 (астана)», «itaxi_2_astana» и «ITAXI» — это один iTaxi.
KNOWN_BRANDS = (
    ('itaxi', 'iTaxi', ('itaxi', 'i taxi', 'айтакси')),
    ('jana', 'Jana', ('jana', 'жана')),
    ('tenge', 'Tenge', ('tenge', 'тенге')),
    ('amanat', 'Amanat', ('amanat', 'аманат')),
    ('noltaxi', 'NolTaxi', ('noltaxi', 'nol taxi', 'nol taksi', 'ноль такси', 'нольтакси',
                            '0 такси')),
    ('adal', 'Adal', ('adal', 'адал')),
    ('qazaq', 'Qazaq', ('qazaq', 'казах', 'қазақ')),
)
KNOWN_CODES = frozenset(code for code, _, _ in KNOWN_BRANDS)

_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'і': 'i', 'ғ': 'g', 'қ': 'q', 'ң': 'n', 'ө': 'o', 'ұ': 'u', 'ү': 'u',
    'һ': 'h', 'ә': 'a',
}

# Города, которые пишут рядом с брендом. И кириллицей, и транслитом: код
# записи словаря строится транслитом, и он тоже встречается как написание.
CITIES = frozenset((
    'алматы', 'астана', 'нур-султан', 'шымкент', 'актау', 'актобе', 'атырау', 'караганда',
    'кокшетау', 'костанай', 'кызылорда', 'павлодар', 'петропавловск', 'семей',
    'талдыкорган', 'тараз', 'уральск', 'туркестан', 'жанаозен', 'усть-каменогорск',
    'каскелен', 'экибастуз', 'темиртау', 'жезказган', 'конаев', 'капчагай', 'балхаш',
    'сатпаев', 'рудный', 'риддер', 'кулсары', 'жаркент', 'степногорск', 'щучинск',
    'almaty', 'astana', 'shymkent', 'aktau', 'aktobe', 'atyrau', 'karaganda', 'kokshetau',
    'kostanay', 'kyzylorda', 'pavlodar', 'petropavlovsk', 'semey', 'taldykorgan', 'taraz',
    'uralsk', 'turkestan', 'zhanaozen', 'ust-kamenogorsk', 'ust_kamenogorsk', 'kaskelen',
))

# Латинская форма каждого города — тем же транслитом, что и коды словаря:
# строка «ekibastuz» — это город, а не парк.
CITIES = CITIES | frozenset(
    ''.join(_TRANSLIT.get(char, char) for char in city) for city in CITIES)

# Слова услуги, а не бренда: «itaxi (доставка)», «жана межгород».
SERVICE_WORDS = frozenset(('доставка', 'dostavka', 'курьер', 'kurer', 'vip', 'межгород',
                           'mezhgorod'))

_PARENS_RE = re.compile(r'\([^)]*\)')
_SPACES_RE = re.compile(r'\s+')


def _clean(text):
    text = str(text or '').lower().replace('ё', 'е').replace('_', ' ')
    return _SPACES_RE.sub(' ', text).strip()


def _known(part):
    """Код бренда ТЗ, если значение с него начинается. Иначе None."""
    # Город с похожим началом — не бренд: «жанаозен» начинается с «жана».
    if part.split(' ', 1)[0] in CITIES:
        return None
    for code, _title, prefixes in KNOWN_BRANDS:
        for prefix in prefixes:
            if part.startswith(prefix):
                return code
    return None


def _generic(part):
    """Остаток названия без города и услуги. Пустая строка — бренда нет."""
    words = [word for word in _PARENS_RE.sub(' ', part).split()
             if word not in CITIES and word not in SERVICE_WORDS]
    # «Департамент такси» и «департамент» — один парк: хвостовое «такси» у
    # русского названия не различает бренды. «Такси 24» (слово в начале) и
    # «Salam taxi» (латиницей, часть имени) не трогаются.
    if len(words) > 1 and words[-1] == 'такси':
        words = words[:-1]
    return ' '.join(words).strip(' -,.')



def code_of(text):
    """Код для адресной строки и пресета: «Ноль такси» → nol_taksi.

    Кириллица в коде превращалась бы в проценты на полстроки URL."""
    latin = ''.join(_TRANSLIT.get(char, char) for char in str(text or '').lower())
    return re.sub(r'[^a-z0-9]+', '_', latin).strip('_')[:64]


def park_brand(raw):
    """Сырое написание парка → (код, подпись) бренда или None.

    None — бренда в значении нет (пусто или один город): сделка уходит в
    корзину «Не определено», а не заводит в словаре парк-призрак.
    """
    text = _clean(raw)
    if not text:
        return None
    # Мультиселект: значения через запятую. Относим к бренду ПЕРВОГО значения,
    # у которого бренд вообще есть, — склейку «itaxi (алматы), ipartner
    # (астана), itaxi (шымкент)…» заводили под iTaxi, и это её смысл.
    for part in (piece.strip() for piece in text.split(',')):
        if not part:
            continue
        code = _known(part)
        if code:
            return code, next(title for known, title, _ in KNOWN_BRANDS if known == code)
        rest = _generic(part)
        if rest:
            code = code_of(rest)
            if code:
                return code, rest[:1].upper() + rest[1:]
    return None
