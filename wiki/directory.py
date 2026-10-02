"""Справочники вики в поиске и в помощнике: офисы и комиссии Яндекса.

Адреса офисов и комиссии Яндекса живут не в статьях, а во вкладках «Офисы»
(wiki_offices, wiki/offices.py) и «Города» (wiki_cities, wiki/cities.py).
Поиск вики и помощник видели только статьи — и находили то, где слово просто
упоминается. Замер на проде 02.10.2026 за 60 дней: «офис», «адрес», «офисы»,
«адреса» — 54 поиска, «адрес офиса в туркестане», «офис алматы», «комиссия
сервиса яндекса» — каждый с выдачей статей и без единого адреса; помощнику
об офисах и комиссиях задано 118 вопросов из ~965 («itaxi адрес в шымкенте»,
«комиссия яндекса в кокшетау», «Комиссия Яндекс по городам»).

Модуль делает три вещи и сам в базу не ходит — SQL живёт в offices.py и
cities.py под стражей пространства (tests/test_wiki_directory_space.py,
tests/test_wiki_cities.py):
  * понимает запрос: город (склонения, казахские буквы, латиница, разговорные
    формы), намерение «где офис» или «сколько комиссия», тариф, парк, слова
    из названия и адреса офиса, номер телефона;
  * решает, что показать (plan) — короткий ответ, а не выдачу справочника:
    генерические слова («офис», «комиссия») сами по себе ведут во вкладку, а
    не высыпают все сорок офисов;
  * отдаёт решение двумя формами: строками выдачи поиска (search_items) и
    фрагментами для помощника (ai_rows) — второе с живыми данными на сегодня,
    без индекса и эмбеддингов, поэтому не устаревает.

Доступ — как у самих вкладок (routes_structure.request_space): пространство
ВЫДАНО человеку (гостю — нет: телефоны парков и адреса в приглашение прочитать
раздел не входят) и вкладка в нём включена. Тумблеры вкладок здесь проверяются
на сервере: иначе поиск показал бы данные вкладки, которой в пространстве нет.
"""

import re

from . import cities as wiki_cities
from . import offices as wiki_offices
from . import parks as wiki_parks
from . import queries
from . import schema
from . import structure
from . import text as wiki_text

# ─────────────────────────────────────────────────────────────────────────────
# Доступ
# ─────────────────────────────────────────────────────────────────────────────


def directory_space(cursor, ctx, requested):
    """Пространство справочников для этого запроса: {id, offices, cities} или None.

    Как request_space, но без ответов ошибкой: поиск и помощник обязаны
    работать и тогда, когда справочник человеку не положен, — просто без него.
    Просьба (requested) принимается, только если пространство выдано не
    гостем; без просьбы берём единственное выданное, а из нескольких не
    выбираем никакое — показать справочник, которого не спрашивали, нельзя.
    """
    allowed = set(queries.spaces_for_user(cursor, ctx, include_guest=False))
    if not allowed:
        return None
    if requested is None:
        if len(allowed) != 1:
            return None
        requested = next(iter(allowed))
    if requested not in allowed:
        return None
    for space in structure.list_spaces(cursor):
        if space['id'] == requested:
            features = schema.space_features(space.get('features'))
            result = {'id': requested,
                      'offices': bool(features.get('offices')),
                      'cities': bool(features.get('cities'))}
            return result if (result['offices'] or result['cities']) else None
    return None


def access_map(cursor, ctx):
    """{space_id: {'offices', 'cities'}} — где справочники человеку открыты сейчас.

    Для истории помощника: источник-справочник из старого ответа показывается,
    только пока доступ к вкладке у человека есть (wiki/ai/store.py).
    """
    allowed = set(queries.spaces_for_user(cursor, ctx, include_guest=False))
    if not allowed:
        return {}
    result = {}
    for space in structure.list_spaces(cursor):
        if space['id'] in allowed:
            features = schema.space_features(space.get('features'))
            result[space['id']] = {'offices': bool(features.get('offices')),
                                   'cities': bool(features.get('cities'))}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Разбор запроса
# ─────────────────────────────────────────────────────────────────────────────

# Буквы, цифры, «+» и «%» — остальное в пробел. Плюс нужен «Комфорт+»: общая
# нормализация поиска его выбрасывает (wiki/text.py), и «комфорт+» стал бы
# «комфортом», то есть другим тарифом.
_NON_WORD = re.compile(r'[^a-zа-я0-9+%]+')
_LETTERS = r'a-zа-я0-9'


def normalize(value):
    """Регистр, казахские буквы и ё к русским, дефис и знаки — в пробел."""
    folded = wiki_text.fold_kazakh(str(value or '').lower())
    return ' '.join(_NON_WORD.sub(' ', folded).split())


def _bounded(pattern):
    return re.compile(r'(?<![%s])(?:%s)' % (_LETTERS, pattern))


# «Где офис». Сильный признак — только сам офис: «офис» ведёт во вкладку, а
# адрес приезжает, когда назван город, парк, название или улица.
_OFFICE_INTENT = _bounded(r'офис|office|филиал|кенсе')

# «Адрес» — слабее: в такси это чаще адрес заказа. Засчитывается рядом с
# городом, парком или словом из названия офиса, а один («адрес», «адреса» —
# 23 поиска за 60 дней) ведёт во вкладку. «Локация» и «местонахождение» — про
# геолокацию водителя, их нет вовсе.
_ADDRESS_HINT = _bounded(r'адрес\w*|мекенжай\w*|где\s+(?:находит|расположен)\w*')

# Адрес заказа и почты — не адрес офиса. Вырезается до поиска признаков, как
# «фронт-офис»: «списалась сумма два раза при смене адреса», «неверный адрес
# подачи», «клиент изменил адрес в заказе», «адрес электронной почты».
_ORDER_ADDRESS = _bounded(
    r'(?:смен|измен|помен|замен|исправ|неверн|неправильн|ошибочн|ввел|ввод|вбил|выбра'
    r'|редакт|корректир|добав|удал|друг|нов|конечн|промежуточн|домашн|электронн|почтов)'
    r'\w*\s+(?:\w+\s+)?адрес\w*'
    r'|адрес\w*\s+(?:\w+\s+)?(?:подач|назначен|доставк|заказ|клиент|пассажир|получател'
    r'|отправител|забор|точк|почт|электрон|email|mail|сайт|прописк|регистрац|проживан)\w*')

# Слабое «где офис» — часы и «куда ехать»: бывают и у отдела («по каким
# графикам работает фин отдел»), поэтому засчитываются, как и адрес, только
# рядом с городом или офисом. И только вопросом: голое «работает» — это и «не
# работает таксометр», и «как работает бонус», а «закрыт» — «закрытие смены».
_OFFICE_HINT = _bounded(
    r'куда\s+(?:ехать|приехать|подъехать|подойти|прийти|обратит|направ)\w*'
    r'|(?:график|режим|час)\w*\s+работ\w*'
    r'|(?:во|до)\s+скольк\w*\s+(?:открыва|закрыва|работа|принима|начина|заканчива)\w*'
    r'|до\s+скольки(?![%s])'
    r'|(?:работает|работают|открыт[аоы]?|закрыт[аоы]?)\s+ли(?![%s])' % (_LETTERS, _LETTERS))

# «Сколько комиссия». С опечатками, которые пишут на самом деле: «коммисия»,
# «камиссия», «комисия». Между «сколько» и глаголом бывает кто и с кого:
# «сколько Яндекс забирает», «сколько с водителя снимают» — но не любое слово:
# «сколько времени забирает проверка» не про комиссию.
_COMMISSION_INTENT = _bounded(
    r'к[оа]м+и?с+и|комис'
    r'|сколько\s+(?:(?:яндекс\w*|yandex|сервис\w*|процент\w*|%|водител\w*|заказ\w*'
    r'|с|со|у|за)\s+){0,2}'
    r'(?:берет|берут|снимает|снимают|забирает|забирают|удерживает|удерживают)')

# Процент — слабее: «сколько процентов по налогам», «ТаксиПро сколько % Qr» —
# не про комиссию Яндекса. Комиссией становится рядом с городом, тарифом или
# словом «яндекс»/«сервис».
_PERCENT_HINT = re.compile(r'(?<![a-zа-я])процент|%')
_YANDEX_WORD = _bounded(r'яндекс|yandex|сервис')

# Комиссия за пополнение, вывод и перевод, налоги — это не комиссия Яндекса за
# тариф, а другой вопрос (статьи о платежах и самозанятости). Отвечать на
# «комиссия при выводе» карточкой тарифов значило бы шуметь.
_PAYMENT_CONTEXT = _bounded(
    r'пополн|вывод|вывест|вывед|перевод|снят|баланс|кошел|карт[аеуыо]|kaspi|каспи|штраф'
    r'|налог|ндс|ипн|самозанят')

# «Комиссия таксопарка Стабильный» — комиссия ПАРКА, своя у каждого парка. Без
# города это вопрос к карточке парка, а не к тарифам Яндекса по городам.
# С опечатками из журнала: «таксопрака», «таксапарк».
_PARK_CONTEXT = _bounded(r'такс[оа]\s*п(?:ар|ра)к|парк(?:а|у|ом|е|ов|и)?(?![%s])' % _LETTERS)

# Вопрос про числа — «сколько», «какая», «по городам», «тарифов», «Яндекса»:
# тогда помощнику нужна таблица комиссий всех городов.
_NUMBERS_QUESTION = _bounded(
    r'сколько|какая|какой|какие|каков|по\s+город|наших\s+город|процент|%|тариф|яндекс|yandex')

# «Две недели без комиссии» — акция, а не вопрос о размере комиссии: про неё
# пишут статьи, и таблица тарифов на 25 городов там была бы шумом.
_PROMO_CONTEXT = _bounded(r'без\s+к[оа]м+и?с+и')

# Тарифы: слово оператора → название тарифа, как его пишет Яндекс в слепке
# (свёрнутое). Длинные формы раньше коротких: «грузовой межгород» — один тариф,
# а не «грузовой» и «межгород». Названия «Business» и «Premier» Яндекс пишет
# латиницей, поэтому «бизнес» и «премьер» без словаря не нашлись бы.
_TARIFF_WORDS = (
    (r'груз\w*\s+межгород|cargo\s+intercity', 'грузовой межгород'),
    (r'комфорт\s*(?:\+|плюс)|к\s*\+|comfort\s*(?:\+|plus)|komfort\s*\+', 'комфорт+'),
    (r'эконом|econom|ekonom', 'эконом'),
    (r'комфорт|comfort|komfort', 'комфорт'),
    (r'бизнес|business|biznes', 'business'),
    (r'премьер|премьeр|premier|ультима|ultima', 'premier'),
    (r'электро|electro', 'электро'),
    (r'доставк|экспресс|express', 'доставка'),
    (r'курьер|courier|kurer', 'курьер'),
    (r'грузов|cargo', 'грузовой'),
    (r'межгород|междугород|intercity', 'межгород'),
    (r'тариф\w*\s+вместе|combo', 'вместе'),
)
_TARIFF_PATTERNS = tuple((_bounded(pattern), name) for pattern, name in _TARIFF_WORDS)

# Слова, которые не называют ни офис, ни улицу: по ним сужать выдачу нельзя.
_STOP_WORDS = frozenset((
    'офис', 'офиса', 'офисы', 'офисов', 'офисе', 'адрес', 'адреса', 'адресов',
    'город', 'города', 'городе', 'улица', 'улицы', 'проспект', 'микрорайон',
    'дом', 'где', 'как', 'какой', 'какая', 'какие', 'куда', 'сколько', 'есть',
    'для', 'это', 'находится', 'работает', 'график', 'работы', 'такси', 'taxi',
    'яндекс', 'яндекса', 'yandex', 'тариф', 'тарифа', 'тарифы', 'комиссия',
    'комиссии', 'водителя', 'водителей', 'водитель', 'парк', 'парка', 'таксопарк',
    'таксопарка', 'онлайн', 'номер', 'телефон', 'brand', 'филиал',
))

# Разговорные и старые названия городов → название, как в справочнике.
# Применяются, только если такой город в пространстве есть.
CITY_ALIASES = {
    'алмата': 'алматы', 'алмате': 'алматы', 'алмату': 'алматы', 'алма ата': 'алматы',
    'нур султан': 'астана', 'нурсултан': 'астана', 'акмола': 'астана',
    'чимкент': 'шымкент',
    'оскемен': 'усть каменогорск', 'усть каменка': 'усть каменогорск',
    'устька': 'усть каменогорск',
    'орал': 'уральск',
    'семипалатинск': 'семей',
    'кустанай': 'костанай',
    'актюбинск': 'актобе',
    'петропавл': 'петропавловск',
    'экбастуз': 'экибастуз', 'екибастуз': 'экибастуз',
    'туркистан': 'туркестан',
    'джезказган': 'жезказган',
    'талдыкурган': 'талдыкорган',
    'кзыл орда': 'кызылорда', 'кызыл орда': 'кызылорда',
}

_VOWELS = 'аеиоуыэюя'


def _city_pattern(folded_name):
    """Город со склонением последнего слова: «в Астане», «из Шымкента».

    Короткие названия склоняем осторожнее: «Актау» — только как есть, а
    «Тараз» — «Таразе», «Тараза». Иначе «акта» (родительный «акт») стало бы
    Актау.
    """
    words = folded_name.split()
    if not words:
        return None
    *head, last = words
    if last.endswith('й'):
        tail = re.escape(last[:-1]) + '(?:й|е|я|ю|ем)'
    elif last[-1] in 'ая' and len(last) >= 6:
        tail = re.escape(last[:-1]) + '(?:а|я|ы|и|е|у|ю|ой|ей)'
    elif last[-1] in _VOWELS:
        tail = re.escape(last)
    else:
        tail = re.escape(last) + '(?:а|е|у|ом|ы)?'
    body = r'\s+'.join([re.escape(word) for word in head] + [tail])
    return re.compile(r'(?<![%s])%s(?![%s])' % (_LETTERS, body, _LETTERS))


def _variants(text):
    """Написания запроса, в которых ищем город, парк и тариф.

    Латиница — двумя путями: транслитом («almaty» → «алматы») и как забытая
    раскладка («fkvfns» → «алматы»). Раскладку чиним только у запроса из одной
    латиницы: в смешанном латинские слова — бренды («itaxi адрес»).
    """
    base = normalize(text)
    found = [base]
    if re.search(r'[a-z]', base):
        found.append(normalize(wiki_text.transliterate_latin_to_cyrillic(base)))
        if not re.search(r'[а-я]', base):
            found.append(normalize(wiki_text.fix_keyboard_layout(str(text or '').lower())))
    return [value for index, value in enumerate(found) if value and value not in found[:index]]


# «Фронт-офис», «бэк-офис» — отделы и должности, а не здание с адресом:
# «менеджеры фронт-офиса» не про то, куда ехать.
_DEPARTMENT_OFFICE = _bounded(r'(?:фронт|бэк|бек|front|back)\s*офис\w*')


def analyze(query):
    """Что в запросе есть для справочника. Чистая функция — её и проверяют тесты.

    office_intent / commission_intent — сильные признаки, сами по себе;
    address_hint / office_hint / percent_hint — слабые, plan() засчитывает их
    только рядом с названным городом, офисом, парком или тарифом и когда
    прочие слова запроса объяснены (_leftover).
    """
    variants = _variants(query)
    base = variants[0] if variants else ''
    cleaned = [_ORDER_ADDRESS.sub(' ', _DEPARTMENT_OFFICE.sub(' ', value)) for value in variants]
    payment = bool(_PAYMENT_CONTEXT.search(base) or _PROMO_CONTEXT.search(base))
    tariffs = []
    for value in variants:
        rest = value
        for pattern, name in _TARIFF_PATTERNS:
            if pattern.search(rest):
                if name not in tariffs:
                    tariffs.append(name)
                rest = pattern.sub(' ', rest)
    words = base.split()
    commission = any(_COMMISSION_INTENT.search(value) for value in variants)
    percent = any(_PERCENT_HINT.search(value) for value in variants)
    office = any(_OFFICE_INTENT.search(value) for value in cleaned)
    return {
        'query': str(query or ''),
        'variants': variants,
        'words': words,
        'office_intent': office,
        'address_hint': any(_ADDRESS_HINT.search(value) for value in cleaned),
        'office_hint': any(_OFFICE_HINT.search(value) for value in cleaned),
        # Офис назван прямо. Ключ отдельный от office_intent по смыслу: сводку
        # всех офисов помощнику дают только на него.
        'office_noun': office,
        'commission_intent': commission and not payment,
        'percent_hint': percent and not payment,
        'yandex_word': bool(_YANDEX_WORD.search(base)),
        'numbers_question': bool(_NUMBERS_QUESTION.search(base)),
        'park_context': bool(_PARK_CONTEXT.search(base)),
        'tariffs': tariffs,
        'digits': ''.join(re.findall(r'\d', str(query or ''))),
    }


def find_cities(analysis, names):
    """Названные в запросе города — в том написании, как в справочнике.

    names — все города пространства (из офисов и из «Городов»). Порядок —
    как в запросе: «из Шымкента в Туркестан» сначала Шымкент.
    """
    by_folded = {}
    for name in names:
        folded = normalize(name)
        if folded and folded not in by_folded:
            by_folded[folded] = name
    forms = [(folded, folded) for folded in by_folded]
    forms += [(alias, target) for alias, target in CITY_ALIASES.items() if target in by_folded]
    hits = {}
    for form, target in forms:
        pattern = _city_pattern(form)
        if pattern is None:
            continue
        for value in analysis['variants']:
            match = pattern.search(value)
            if match:
                position = hits.get(target)
                if position is None or match.start() < position:
                    hits[target] = match.start()
                break
    # «Усть-Каменогорск» содержит «каменогорск», а не наоборот; одинаковые
    # позиции у разных городов бывают только у вложенных названий — берём длинное.
    ordered = sorted(hits.items(), key=lambda item: (item[1], -len(item[0])))
    result, taken = [], []
    for folded, position in ordered:
        if any(position >= start and position < start + len(other) for start, other in taken):
            continue
        taken.append((position, folded))
        result.append(by_folded[folded])
    return result


# ── Парки ─────────────────────────────────────────────────────────────────────

_GENERIC_PARK_WORDS = frozenset(('такси', 'taxi', 'парк', 'таксопарк', 'park'))

# Буквы, которых нет в общем транслите (wiki/text.py): без них «taxi» стал бы
# «таxи», и «Ай такси» не нашло бы iTaxi.
_CYRILLIC_EXTRA = (('x', 'кс'), ('w', 'в'), ('j', 'дж'))


def _cyrillic(word):
    value = wiki_text.transliterate_latin_to_cyrillic(word)
    for latin, cyrillic in _CYRILLIC_EXTRA:
        value = value.replace(latin, cyrillic)
    return normalize(value)


def _park_word_forms(word):
    forms = {word, _cyrillic(word), normalize(wiki_text.transliterate_cyrillic_to_latin(word))}
    # «iTaxi» говорят «Ай такси», «iPartner» — «Ай партнер»: английская i в
    # начале бренда читается «ай».
    if len(word) > 2 and word[0] == 'i' and re.match(r'[a-z]', word[1]):
        forms.add('ай' + _cyrillic(word[1:]))
    return {form.replace(' ', '') for form in forms if form}


def _park_alternatives(name):
    """«Hokage (Wolt)» — это и «Hokage», и «Wolt»: скобки дают второе имя."""
    raw = str(name or '')
    inner = re.findall(r'\(([^)]*)\)', raw)
    outer = re.sub(r'\([^)]*\)', ' ', raw)
    result = []
    for part in [outer] + inner:
        words = [word for word in normalize(part).split()
                 if word not in _GENERIC_PARK_WORDS and not word.isdigit()]
        if words:
            result.append(words)
    return result


def match_parks(analysis, parks):
    """Парки, названные в запросе: все значимые слова названия на месте."""
    tokens = set()
    compact = []
    for value in analysis['variants']:
        tokens.update(value.split())
        compact.append(value.replace(' ', ''))
    found = []
    for park in parks:
        for words in _park_alternatives(park.get('name')):
            ok = True
            for word in words:
                forms = _park_word_forms(word)
                if forms & tokens:
                    continue
                if len(word) >= 4 and any(form in blob for form in forms if len(form) >= 5
                                          for blob in compact):
                    continue
                ok = False
                break
            if ok:
                found.append(park)
                break
    # «iTaxi VIP» и «iTaxi» оба подходят под «itaxi vip» — остаётся тот, чьё
    # название длиннее и потому точнее.
    names = {park['id']: normalize(park.get('name')) for park in found}
    return [park for park in found
            if not any(other != park['id'] and names[park['id']] in names[other]
                       and names[park['id']] != names[other] for other in names)]


# ── Офисы: слова названия и адреса, телефон ───────────────────────────────────

def _word_forms(word):
    """Слово и его написание другим алфавитом, без мягкого знака: «Wolt» в
    названии офиса и «Вольт» в запросе — одно слово («волт»)."""
    forms = {word, word.replace('ь', '').replace('ъ', '')}
    if re.search(r'[a-z]', word):
        forms.add(_cyrillic(word).replace('ь', '').replace(' ', ''))
    return {form for form in forms if form}


def _office_words(office):
    words = set()
    for field in ('name', 'address', 'address_note', 'partner_label'):
        for word in normalize(office.get(field)).split():
            if len(word) >= 4 and word not in _STOP_WORDS and not word.isdigit():
                words |= _word_forms(word)
    return words


def _city_forms(city_names):
    """Названия городов пространства и их разговорные формы — свёрнутые."""
    folded = {normalize(name) for name in city_names if name}
    folded.discard('')
    return folded | {alias for alias, target in CITY_ALIASES.items() if target in folded}


def _query_words(analysis, city_names):
    """Значимые слова запроса без названий городов — для поиска по офису.

    Город вырезается по тому же образцу со склонениями, что и находится:
    «в туркестане» иначе осталось бы лишним словом «туркестане». Тарифы
    оставляем: «Офис для подключения тарифа «Бизнес»» называют именно так —
    «бизнес офис»."""
    patterns = [_city_pattern(form) for form in _city_forms(city_names)]
    words = set()
    for value in analysis['variants']:
        text = value
        for pattern in patterns:
            if pattern is not None:
                text = pattern.sub(' ', text)
        for word in text.split():
            if len(word) >= 4 and word not in _STOP_WORDS and not word.isdigit():
                words |= _word_forms(word)
    return words


def _stem_hit(word, candidates):
    """Слово запроса совпадает со словом записи с точностью до окончания.

    Окончание — это несколько букв: «жамбыла» и «жамбыл» одно слово, а
    «автомойка» и «авто» из «Аренда Авто» — нет, хотя одно начинается с другого."""
    stem = word[:max(4, len(word) - 2)]
    return any(candidate == word or (
        abs(len(candidate) - len(word)) <= 3
        and (candidate.startswith(stem) or word.startswith(candidate[:max(4, len(candidate) - 2)])))
        for candidate in candidates)


def _digits(value):
    return ''.join(re.findall(r'\d', str(value or '')))


def office_hits(analysis, offices, city_names):
    """Офисы, у которых в названии, адресе или ориентирах есть слово запроса,
    или телефон которых назван цифрами.

    Телефон сравниваем по последним цифрам: в карточке он записан как
    набрали («+7 700 …» или «8 700 …»), а человек вводит как привык. Шесть
    цифр — порог: короче это номер дома или процент, а не телефон.
    """
    words = _query_words(analysis, city_names)
    tail = analysis['digits'][-10:] if len(analysis['digits']) >= 6 else ''
    hits = []
    for office in offices:
        own = _office_words(office)
        hit = bool(own and words and any(_stem_hit(word, own) for word in words))
        if not hit and tail:
            phones = [office.get('phone')] + [phone.get('phone')
                                              for park in office.get('parks') or []
                                              for phone in park.get('phones') or []]
            hit = any(_digits(phone).endswith(tail) for phone in phones if phone)
        if hit:
            hits.append(office)
    return hits


# Слова самого вопроса к справочнику — объяснены по определению: «адрес»,
# «работает», «откроется», «процентов», «Яндекс». По началу слова: формы
# одного слова перечислять незачем.
_DIRECTORY_VOCABULARY = (
    'офис', 'office', 'филиал', 'кенсе', 'адрес', 'мекенжай', 'находит', 'располож',
    'работ', 'открыв', 'открыт', 'закрыв', 'закрыт', 'принима', 'начина', 'заканчива',
    'график', 'режим', 'часы', 'часов', 'скольк', 'ехать', 'приехать', 'подъехать',
    'подойти', 'прийти', 'обратит', 'направ', 'процент', 'комис', 'комм', 'камис',
    'яндекс', 'yandex', 'сервис', 'тариф', 'город',
)

# Слова, которые сами ничего не называют: «подскажите адрес в Актобе» — вопрос
# об адресе, «подскажите» его не меняет. Короче четырёх букв отбрасываются и так.
_FILLER_WORDS = frozenset((
    'подскажи', 'подскажите', 'скажи', 'скажите', 'пожалуйста', 'можно', 'нужно', 'надо',
    'нужен', 'нужна', 'хочу', 'хочет', 'хотят', 'знать', 'узнать', 'сегодня', 'сейчас',
    'завтра', 'вчера', 'субботу', 'субботам', 'воскресенье', 'воскресеньям', 'выходные',
    'выходным', 'выходных', 'праздники', 'праздничные', 'обед', 'обеда', 'перерыв', 'всех',
    'всего', 'наши', 'наших', 'нашего', 'нашем', 'вашего', 'ваши', 'вообще', 'именно',
    'тоже', 'также', 'каком', 'какую', 'каких', 'какого', 'точный', 'точная', 'точно',
    'берет', 'берут', 'снимает', 'снимают', 'забирает', 'забирают', 'удерживает',
    'удерживают', 'списывает', 'взимает', 'взимается', 'взымается', 'составляет',
    'водителю', 'водителям', 'области', 'районе',
))


def _leftover(analysis, city_names, *, hits=(), parks=(), strip_tariffs=True):
    """Слова запроса, которых справочник не объясняет. [] — объяснено всё.

    Объясняют: город, тариф (strip_tariffs), названный парк, слово из названия
    или адреса найденного офиса, слова самого вопроса и «подскажите». «Адрес в
    Актобе» объяснён, «автомойка адреса по Атырау» — нет: «автомойка» не офис.
    Хватает одного написания без лишних слов — латиница и её транслит.
    """
    patterns = [_city_pattern(form) for form in _city_forms(city_names)]
    own = set()
    for office in hits:
        own |= _office_words(office)
    park_forms = set()
    for park in parks:
        for words in _park_alternatives(park.get('name')):
            for word in words:
                park_forms |= _park_word_forms(word)
    best = None
    for value in analysis.get('own_variants') or analysis['variants']:
        text = value
        for pattern in patterns:
            if pattern is not None:
                text = pattern.sub(' ', text)
        if strip_tariffs:
            for pattern, _name in _TARIFF_PATTERNS:
                text = pattern.sub(' ', text)
        rest = []
        for word in text.split():
            if len(word) < 4 or word.isdigit() or word in _STOP_WORDS or word in _FILLER_WORDS:
                continue
            if word.startswith(_DIRECTORY_VOCABULARY):
                continue
            forms = _word_forms(word)
            if forms & park_forms or (own and any(_stem_hit(form, own) for form in forms)):
                continue
            rest.append(word)
        if best is None or len(rest) < len(best):
            best = rest
    return best or []


# ─────────────────────────────────────────────────────────────────────────────
# Комиссии: тот же расчёт, что у карточки города (src/components/wiki/cityRules.js)
# ─────────────────────────────────────────────────────────────────────────────


def format_percent(value):
    """17.1 → «17,1%», 12 → «12%». Двойник formatPercent из cityRules.js."""
    if value is None or value == '':
        return ''
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ''
    return _number(number) + '%'


def _number(number):
    text = ('%.2f' % round(number, 2)).rstrip('0').rstrip('.')
    return text.replace('.', ',')


def commission_range(values):
    """«12–19%», «12%» или '' — двойник commissionRange из cityRules.js."""
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return ''
    low, high = min(numbers), max(numbers)
    if low == high:
        return format_percent(low)
    return '%s–%s' % (_number(low), format_percent(high))


def visible_tariffs(city):
    """Тарифы города для показа: слепок Яндекса + ручные поля + свои тарифы.

    Двойник cityTariffs (cityRules.js) без скрытых: ручные поля привязаны к
    КОДУ тарифа, названия — только из слепка, скрытые не показываются и в
    диапазон не входят, свои тарифы идут после яндексовских. Расхождение с
    карточкой стережёт tests/test_wiki_directory.py на тех же случаях, что
    tests/wiki_city_rules.test.mjs.
    """
    meta = city.get('tariff_meta') or {}
    result = []
    for tariff in city.get('tariffs') or []:
        own = meta.get(tariff.get('class')) or {}
        if own.get('hidden'):
            continue
        result.append({'name': tariff.get('name') or tariff.get('class'),
                       'code': tariff.get('class'),
                       'commission': own.get('commission')})
    for tariff in city.get('extra_tariffs') or []:
        result.append({'name': tariff.get('name'), 'code': None,
                       'commission': tariff.get('commission')})
    return result


def city_commissions(city, wanted=None):
    """Тарифы с заполненной комиссией; wanted — названия тарифов из запроса.

    Нет ни одного названного тарифа в городе — отдаём все: «комфорт+ в
    Темиртау» честнее ответить списком того, что есть, чем пустотой.
    """
    tariffs = [tariff for tariff in visible_tariffs(city) if tariff['commission'] is not None]
    if wanted:
        chosen = [tariff for tariff in tariffs if normalize(tariff['name']) in wanted]
        if chosen:
            return chosen, True
    return tariffs, False


# ─────────────────────────────────────────────────────────────────────────────
# Офис на день
# ─────────────────────────────────────────────────────────────────────────────

_DAY_SHORT = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')


def _day_text(value):
    if not value:
        return 'выходной'
    text = '%s–%s' % (value['from'], value['to'])
    if value.get('break_from') and value.get('break_to'):
        text += ', обед %s–%s' % (value['break_from'], value['break_to'])
    return text


def week_lines(schedule):
    """«Пн–Пт 09:00–18:00, обед 13:00–14:00; Сб–Вс выходной» — подряд идущие
    одинаковые дни сворачиваются, как в карточке офиса."""
    normalized = wiki_offices.normalize_schedule(schedule)
    if not normalized:
        return ''
    days = [_day_text(normalized.get(code)) for code in wiki_offices.DAY_CODES]
    parts, start = [], 0
    for index in range(1, len(days) + 1):
        if index == len(days) or days[index] != days[start]:
            label = _DAY_SHORT[start] if index - 1 == start \
                else '%s–%s' % (_DAY_SHORT[start], _DAY_SHORT[index - 1])
            parts.append('%s %s' % (label, days[start]))
            start = index
    return '; '.join(parts)


def hours_on(schedule, day):
    normalized = wiki_offices.normalize_schedule(schedule)
    if not normalized or day is None:
        return ''
    value = normalized.get(wiki_offices.DAY_CODES[day.weekday()])
    return _day_text(value) if value else ''


def office_status(office, day):
    """Статус на день — тем же правилом, что вкладка (offices.day_state)."""
    state = wiki_offices.day_state(
        no_office=office.get('no_office'), record=office.get('day'),
        closed_from=office.get('closed_from'), closed_until=office.get('closed_until'),
        schedule=office.get('schedule'), day=day)
    result = {'state': state, 'label': wiki_offices.DAY_STATE_LABELS.get(state, '')}
    record = office.get('day') or {}
    closure = (state == 'closed' and not (record.get('state') and record.get('source') == 'manual')
               and wiki_offices.closure_covers(office.get('closed_from'),
                                               office.get('closed_until'), day))
    if closure:
        until = wiki_offices.parse_day(office.get('closed_until'))
        result['reopens'] = until.isoformat() if until else None
        result['note'] = office.get('closed_note') or None
    elif record.get('source') == 'manual' and record.get('note'):
        result['note'] = record.get('note')
    if state == 'open':
        result['hours'] = hours_on(office.get('schedule'), day)
    return result


# ── Офис сейчас: двойник officeStatus/untilText (src/components/wiki/officeSchedule.js)

_MINUTES_IN_DAY = 1440

_DAY_UNTIL = {'mon': 'понедельника', 'tue': 'вторника', 'wed': 'среды', 'thu': 'четверга',
              'fri': 'пятницы', 'sat': 'субботы', 'sun': 'воскресенья'}


def office_now():
    """Сейчас по времени офисов: вся страна живёт в Asia/Almaty (UTC+5)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(wiki_offices.OFFICE_TIME_ZONE))


def _minutes(value):
    found = re.match(r'^(\d{1,2}):(\d{2})$', str(value or ''))
    if not found or int(found.group(1)) > 23 or int(found.group(2)) > 59:
        return None
    return int(found.group(1)) * 60 + int(found.group(2))


def _span(day, start, end):
    """Интервал дня в минутах; закрытие раньше открытия — через полночь."""
    if not day:
        return None
    low, high = _minutes(day.get(start)), _minutes(day.get(end))
    if low is None or high is None or low == high:
        return None
    if high < low:
        high += _MINUTES_IN_DAY
    return low, high


def _clock(minutes):
    total = minutes % _MINUTES_IN_DAY
    return '%02d:%02d' % (total // 60, total % 60)


def live_status(schedule, now):
    """Статус на текущую минуту: {'state': 'open'|'break'|'closed'|'none', ...}.

    Тот же расчёт, что у живого бейджа вкладки: вчерашний интервал через
    полночь ещё может быть открыт, обед — отдельное состояние, у закрытого —
    ближайшее открытие (opens_at, opens_code, opens_in — сдвиг в днях)."""
    normalized = wiki_offices.normalize_schedule(schedule)
    if not normalized:
        return {'state': 'none'}
    index, minutes = now.weekday(), now.hour * 60 + now.minute

    def day_at(offset):
        return normalized.get(wiki_offices.DAY_CODES[(index + offset) % 7])

    for offset, shift in ((0, 0), (-1, _MINUTES_IN_DAY)):
        interval = _span(day_at(offset), 'from', 'to')
        if not interval:
            continue
        at = minutes + shift
        if at < interval[0] or at >= interval[1]:
            continue
        lunch = _span(day_at(offset), 'break_from', 'break_to')
        if lunch and lunch[0] <= at < lunch[1]:
            return {'state': 'break', 'until': _clock(lunch[1])}
        return {'state': 'open', 'until': _clock(interval[1])}
    for offset in range(7):
        interval = _span(day_at(offset), 'from', 'to')
        if not interval or (offset == 0 and minutes >= interval[0]):
            continue
        return {'state': 'closed', 'opens_at': _clock(interval[0]),
                'opens_code': wiki_offices.DAY_CODES[(index + offset) % 7], 'opens_in': offset}
    return {'state': 'closed'}


def until_text(status):
    """«до 19:00», «до завтра 10:00», «до понедельника 09:00» или None."""
    if status.get('state') in ('open', 'break'):
        return 'до %s' % status['until'] if status.get('until') else None
    if status.get('state') != 'closed' or not status.get('opens_at'):
        return None
    if status['opens_in'] == 0:
        return 'до %s' % status['opens_at']
    if status['opens_in'] == 1:
        return 'до завтра %s' % status['opens_at']
    return 'до %s %s' % (_DAY_UNTIL.get(status['opens_code'], ''), status['opens_at'])


def _live_applies(office, day):
    """Живой расчёт по часам — когда за день ничего не утверждал человек: как
    OfficeStatusBadge, отметка дежурного и закрытие на срок сильнее графика."""
    if office.get('no_office'):
        return False
    record = office.get('day') or {}
    if record.get('state') and record.get('source') == 'manual':
        return False
    if wiki_offices.closure_covers(office.get('closed_from'), office.get('closed_until'), day):
        return False
    return bool(wiki_offices.normalize_schedule(office.get('schedule')))


def _office_phones(office, parks=()):
    """Телефоны точки: общий офиса и номера парков у этой точки.

    parks — названные в запросе парки: их номера идут первыми — человек
    спросил про iTaxi, и общий номер офиса для него второй."""
    wanted = {park['id'] for park in parks}
    own = [{'park': None, 'park_id': None, 'phone': office['phone'], 'note': None}] \
        if office.get('phone') else []
    named, other = [], []
    for park in office.get('parks') or []:
        for phone in park.get('phones') or []:
            if phone.get('phone'):
                (named if park['park_id'] in wanted else other).append(
                    {'park': park.get('name'), 'park_id': park['park_id'],
                     'phone': phone['phone'], 'note': phone.get('note') or None})
    return named + own + other


def _row_phone(office, parks=()):
    """Номер для строки выдачи: свой номер офиса или номер НАЗВАННОГО парка.

    Номер другого парка без подписи читался бы номером офиса: оператор дал бы
    водителю Jana WhatsApp iTaxi. Подписанные номера всех парков — в карточке
    офиса и у помощника, а строке хватает одного, который можно назвать."""
    wanted = {park['id'] for park in parks}
    for phone in _office_phones(office, parks):
        if phone['park_id'] is None or phone['park_id'] in wanted:
            return phone
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Решение: что показать
# ─────────────────────────────────────────────────────────────────────────────

# Строк офисов в выдаче поиска. Остальные — одной строкой «ещё N во вкладке».
SEARCH_OFFICES = 3
# Офисов во фрагментах помощника на город: ему нужен весь город, но не сорок записей.
AI_OFFICES = 6
# Длиннее — это вопрос к статьям, а не поиск адреса: «как водителю оформить
# возврат в Алматы» не должен начинаться со справочника.
MAX_CITY_ONLY_WORDS = 3


def _office_order(office):
    """Сначала главный офис города («Офис Алматы №1»), потом прочие офисы парков
    («для подключения тарифа «Бизнес»»), «офиса в городе нет», партнёрские
    точки последними. Главный — тот, в чьём названии нет ничего, кроме слова
    «офис», города и номера."""
    if office.get('kind') == 'partner':
        return 3
    if office.get('no_office'):
        return 2
    city_words = set(normalize(office.get('city')).split())
    rest = [word for word in normalize(office.get('name')).split()
            if word not in city_words and word != 'офис' and not word.isdigit()]
    return 0 if not rest else 1


def _city_offices(name, own, card, offices):
    """Офисы названного города в порядке показа: [(office, for_city)].

    Сначала «куда направлять водителя» из карточки города — тем же правилом,
    что cityOffices во фронте (src/components/wiki/cityRules.js): отмеченные
    офисы, иначе зона. Офис другого города помечен for_city: «сюда направляют
    водителей из Темиртау». Потом свои записи города из вкладки: главный офис,
    прочие офисы парков, «офиса нет», партнёрские точки (_office_order). Так
    Петропавловск с записью «офиса нет» всё равно называет офис, куда ехать."""
    by_id = {office['id']: office for office in offices}
    chosen = []
    if card:
        chosen = [by_id[i] for i in card.get('driver_office_ids') or [] if i in by_id]
        if not chosen and card.get('serving_office_id') in by_id:
            chosen = [by_id[card['serving_office_id']]]
    folded = normalize(name)
    result = [(office, None if normalize(office.get('city')) == folded else name)
              for office in chosen]
    taken = {office['id'] for office in chosen}
    result += [(office, None) for office in sorted(own, key=_office_order)
               if office['id'] not in taken]
    return result


def _serves(office, parks):
    if not parks:
        return True
    if office.get('all_parks'):
        return True
    ids = {park['park_id'] for park in office.get('parks') or []}
    return any(park['id'] in ids for park in parks)


def plan(analysis, *, offices, cities, parks=(), features=None, limit=SEARCH_OFFICES):
    """Что из справочников показать на этот запрос.

    Возвращает {'offices': [{'office', 'for_city'}], 'offices_more':
    {'city', 'count'} | None, 'cities': [{'city', 'tariffs', 'narrowed'}],
    'offices_tab': bool, 'cities_tab': {'tariffs'} | None}. Пусто — значит
    справочнику сказать нечего, и выдача остаётся выдачей статей.
    """
    features = features or {'offices': True, 'cities': True}
    offices = offices if features.get('offices') else []
    cities = cities if features.get('cities') else []
    result = {'offices': [], 'offices_more': None, 'cities': [],
              'offices_tab': False, 'cities_tab': None}
    names = [office.get('city') for office in offices if office.get('city')]
    names += [city.get('name') for city in cities if city.get('name')]
    named = find_cities(analysis, names)
    if analysis.get('own_variants'):
        # Продолжение разговора: город из самого вопроса сильнее прошлых
        # реплик — «а в Шымкенте?» после Алматы спрашивает только про Шымкент.
        named = find_cities({'variants': analysis['own_variants']}, names) or named
    tariffs = set(analysis['tariffs'])
    hits = office_hits(analysis, offices, names) if offices else []
    named_parks = match_parks(analysis, parks) if parks else []
    short = len(analysis['words']) <= MAX_CITY_ONLY_WORDS
    # Слабые признаки — только когда остальные слова объяснены: «адрес в
    # Актобе» — да, «автомойка адреса по Атырау» и «работает ли фин отдел» — нет.
    # Тариф объясняет процент («процент эконом алматы»), но не офис: «работает
    # ли эконом в Кокшетау» — про тариф. Назван город — слово объясняет только
    # его офис: «автомойка» у точки в Талдыкоргане не делает «автомойка адреса
    # по Атырау» вопросом об офисах Атырау.
    by_city = {}
    for office in offices:
        by_city.setdefault(normalize(office.get('city')), []).append(office)
    card_by_name = {normalize(city.get('name')): city for city in cities}
    if named:
        own_ids = {office['id'] for name in named
                   for office, _for_city in _city_offices(name, by_city.get(normalize(name)) or [],
                                                          card_by_name.get(normalize(name)), offices)}
        explaining = [office for office in hits if office['id'] in own_ids]
    else:
        explaining = hits
    leftover = _leftover(analysis, names, hits=explaining, parks=named_parks)
    office_leftover = _leftover(analysis, names, hits=explaining, parks=named_parks,
                                strip_tariffs=False)
    anchored = bool(named or hits or named_parks)
    weak_office = analysis.get('address_hint') or analysis.get('office_hint')
    # Офисов в пространстве нет — и говорить о них нечего: дверь вела бы в
    # пустую вкладку.
    office_intent = bool(offices) and bool(
        analysis['office_intent']
        or (weak_office and anchored and not office_leftover)
        # «адрес», «адреса» без города — дверь во вкладку; «адрес почты» — нет.
        or (analysis.get('address_hint') and not anchored and not office_leftover))
    commission_intent = bool(cities) and bool(
        analysis['commission_intent']
        or (analysis.get('percent_hint') and not leftover
            and bool(named or tariffs or analysis.get('yandex_word'))))

    if not office_intent and not commission_intent:
        # Ни «где», ни «сколько»: справочник говорит, только когда запрос — это
        # город («Атырау») или город с тем, что объясняет остальные слова:
        # название точки, тариф, парк («брендирование алматы», «межгород
        # алматы»). «Бонусы Туркестан» — про бонусы, а не про офис.
        if not named or not short or leftover:
            return result
        show_offices = bool(offices) and not tariffs
        show_cities = bool(cities) and (bool(tariffs) or not hits)
    else:
        show_offices = office_intent
        show_cities = commission_intent

    if show_offices:
        if named:
            listed = {}
            for name in named:
                own = by_city.get(normalize(name)) or []
                shown = _city_offices(name, own, card_by_name.get(normalize(name)), offices)
                narrowed = [entry for entry in shown if entry[0] in hits]
                if not narrowed and named_parks:
                    narrowed = [entry for entry in shown if _serves(entry[0], named_parks)]
                    if len(narrowed) == len(shown):
                        narrowed = []
                shown = narrowed or shown
                for office, for_city in shown[:limit]:
                    # «Караганда Темиртау»: офис Караганды — и свой, и тот, куда
                    # направляют из Темиртау. Одна строка с обеими пометками.
                    entry = listed.get(office['id'])
                    if entry is None:
                        entry = listed[office['id']] = {'office': office, 'for_city': for_city}
                        result['offices'].append(entry)
                    elif for_city and not entry['for_city']:
                        entry['for_city'] = for_city
                if len(shown) > limit and own:
                    # Дверь ведёт во вкладку с фильтром города — там свои
                    # записи города, их и считаем.
                    result['offices_more'] = {'city': name, 'count': len(own)}
            if office_intent and not result['offices']:
                # Город назван, а точки в нём нет ни своей, ни «куда направлять»:
                # честный ответ — вкладка целиком, а не молчание.
                result['offices_tab'] = True
        elif hits:
            shown = sorted(hits, key=_office_order)
            for office in shown[:limit]:
                result['offices'].append({'office': office, 'for_city': None})
            if len(shown) > limit:
                result['offices_tab'] = True
        elif office_intent:
            result['offices_tab'] = True

    if show_cities:
        if named:
            for name in named:
                card = card_by_name.get(normalize(name))
                if card is None:
                    continue
                chosen, narrowed = city_commissions(card, tariffs)
                if chosen or card.get('option_commissions') or card.get('park_commission') is not None:
                    result['cities'].append({'city': card, 'tariffs': chosen,
                                             'narrowed': narrowed})
        elif (commission_intent and not analysis['park_context'] and not named_parks
              and any(city_commissions(city)[0] for city in cities)):
            # Вкладка — только если в ней есть что смотреть: у Тез городов нет.
            # Назван парк без города («комиссия честный») — это комиссия парка.
            result['cities_tab'] = {'tariffs': sorted(tariffs)}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Выдача поиска
# ─────────────────────────────────────────────────────────────────────────────


def _office_item(entry, *, space_id, day, parks):
    """Строка офиса. Поля графика и дня — затем, чтобы бейдж в выдаче считал
    фронт тем же правилом, что и вкладка (officeDayStatus.js, живое «Открыто
    до 19:00»): два разных бейджа на один офис читались бы как два ответа."""
    office = entry['office']
    phone = _row_phone(office, parks)
    return {
        'kind': 'office',
        'space_id': space_id,
        'id': office['id'],
        'name': office.get('name'),
        'city': office.get('city'),
        'for_city': entry.get('for_city'),
        'address': None if office.get('no_office') else office.get('address'),
        'kind_of_point': office.get('kind'),
        'partner_label': office.get('partner_label') or None,
        'no_office': bool(office.get('no_office')),
        'phone': phone['phone'] if phone else None,
        # Номер названного парка — с подписью, как в карточке офиса: «iTaxi:
        # +7 701 … · WhatsApp».
        'phone_park': phone['park'] if phone else None,
        'phone_note': phone['note'] if phone else None,
        'schedule': wiki_offices.normalize_schedule(office.get('schedule')),
        'day': office.get('day'),
        'closed_from': office.get('closed_from'),
        'closed_until': office.get('closed_until'),
        'closed_note': office.get('closed_note'),
        'updated_at': office.get('updated_at'),
        'status': office_status(office, day),
    }


def _city_item(entry, *, space_id):
    city = entry['city']
    tariffs = entry['tariffs']
    return {
        'kind': 'city',
        'space_id': space_id,
        'id': city['id'],
        'name': city.get('name'),
        'narrowed': entry['narrowed'],
        'tariffs': [{'name': tariff['name'], 'commission': format_percent(tariff['commission'])}
                    for tariff in tariffs],
        'range': commission_range([tariff['commission'] for tariff in visible_tariffs(city)]),
        'options': [{'name': option.get('name'), 'commission': format_percent(option.get('commission'))}
                    for option in city.get('option_commissions') or []
                    if option.get('commission') is not None],
        'park_commission': format_percent(city.get('park_commission')) or None,
    }


def _tariff_summary(cities, tariffs):
    """«Эконом: 11,4–17,1% в 22 городах» — для запроса без города."""
    rows = []
    for name in tariffs:
        values = []
        for city in cities:
            for tariff in visible_tariffs(city):
                if tariff['commission'] is not None and normalize(tariff['name']) == name:
                    values.append(tariff['commission'])
                    break
        if values:
            label = next((tariff['name'] for city in cities for tariff in visible_tariffs(city)
                          if normalize(tariff['name']) == name), name)
            rows.append({'name': label, 'range': commission_range(values), 'cities': len(values)})
    return rows


def search_items(decision, *, space_id, day, cities, parks=()):
    """Строки справочника для выдачи поиска (GET /api/wiki/search → directory)."""
    items = [_office_item(entry, space_id=space_id, day=day, parks=parks)
             for entry in decision['offices']]
    if decision['offices_more']:
        items.append({'kind': 'offices_tab', 'space_id': space_id,
                      'city': decision['offices_more']['city'],
                      'count': decision['offices_more']['count']})
    elif decision['offices_tab']:
        items.append({'kind': 'offices_tab', 'space_id': space_id, 'city': None, 'count': None})
    items += [_city_item(entry, space_id=space_id) for entry in decision['cities']]
    if decision['cities_tab'] is not None:
        items.append({'kind': 'cities_tab', 'space_id': space_id,
                      'tariffs': _tariff_summary(cities, decision['cities_tab']['tariffs'])})
    return items


# Наибольшее число слов запроса, при котором поиск зовёт справочник. Дальше —
# фраза, а не поиск адреса; помощник спрашивает справочник при любой длине.
SEARCH_MAX_WORDS = 6


def load(cursor, *, space, day):
    """Данные справочников пространства — через читающие функции под стражей."""
    offices = wiki_offices.list_offices(cursor, day=day, space_id=space['id']) \
        if space.get('offices') else []
    cities = wiki_cities.directory_cities(cursor, space_id=space['id']) \
        if space.get('cities') else []
    parks = wiki_parks.list_parks(cursor, space_id=space['id']) \
        if space.get('offices') else []
    return offices, cities, parks


def search(cursor, ctx, query, *, requested_space):
    """Справочник для поисковой строки. [] — ему нечего сказать или не положено."""
    analysis = analyze(query)
    if not analysis['words'] or len(analysis['words']) > SEARCH_MAX_WORDS:
        return []
    space = directory_space(cursor, ctx, requested_space)
    if space is None:
        return []
    day = wiki_offices.office_today()
    offices, cities, parks = load(cursor, space=space, day=day)
    named_parks = match_parks(analysis, parks) if parks else []
    decision = plan(analysis, offices=offices, cities=cities, parks=parks, features=space)
    return search_items(decision, space_id=space['id'], day=day, cities=cities,
                        parks=named_parks)


# ─────────────────────────────────────────────────────────────────────────────
# Фрагменты для помощника
# ─────────────────────────────────────────────────────────────────────────────

# Номер ветки поиска помощника (wiki/ai/retrieve.py: 0 слова, 1 смысл,
# 2 опечатка, 3 название). Строки справочника — пятая ветка: их не находят,
# а берут по городу и намерению, и порог сходства к ним не относится.
DIRECTORY_BRANCH = 4


def _dmy(value):
    day = wiki_offices.parse_day(value)
    return day.strftime('%d.%m.%Y') if day else ''


_LIVE_WORDS = {'open': 'открыт', 'break': 'на обеде', 'closed': 'закрыт'}


def _live_text(office, day, now, *, clock=True):
    """«сегодня 09:00–19:00, обед 13:00–14:00; сейчас 20:30 — закрыт до завтра
    09:00» или None, когда живой расчёт не применяется (_live_applies).
    clock=False — без минут: в сводке всех офисов время стоит в заголовке.

    Без этого помощник отвечал на «офис сейчас открыт?» статусом за весь день:
    в 20:30 — «открыт», хотя бейдж вкладки в ту же минуту пишет «Закрыто»."""
    if now is None or not _live_applies(office, day):
        return None
    status = live_status(office.get('schedule'), now)
    if status['state'] == 'none':
        return None
    hours = hours_on(office.get('schedule'), day)
    until = until_text(status)
    return '%s; сейчас%s %s%s' % ('сегодня ' + hours if hours else 'сегодня выходной',
                                  ' %s —' % now.strftime('%H:%M') if clock else '',
                                  _LIVE_WORDS[status['state']], ' ' + until if until else '')


def _office_text(entry, day, parks, now=None):
    """Факты офиса — по строке на факт, с городом и названием в каждой второй.

    Помощник цитирует одну лучшую строку (answer.pick_excerpt), а проверка
    чисел смотрит только в текст фрагмента: телефон, дом и часы обязаны быть
    здесь буквально. Слов «до/по» перед датами нет: так помечаются истёкшие
    сроки (wiki/ai/currency.py), а закрытие на срок пишется днём открытия.
    «до 19:00» — время, не дата, и сроком не считается.
    """
    office = entry['office']
    city = office.get('city') or ''
    name = '«%s»' % office.get('name')
    lines = []
    head = '%s, город %s' % (name, city)
    if office.get('kind') == 'partner':
        head += ' — партнёрская точка' + (' (%s)' % office['partner_label']
                                          if office.get('partner_label') else '')
    if entry.get('for_city'):
        head += '; сюда направляют водителей из города %s (своего офиса там нет)' % entry['for_city']
    lines.append(head + '.')
    if office.get('no_office'):
        lines.append('%s: офиса в городе нет, принимают по телефону.' % city)
    elif office.get('address'):
        lines.append('Адрес %s (%s): %s.' % (name, city,
                                            str(office['address']).strip().rstrip('.,')))
    # Ориентиры в карточке — строкой на ориентир, часто с маркером «- »; здесь
    # одной строкой: помощник цитирует одну строку, и «остановка Рахат» без
    # адреса рядом читалась бы обрывком.
    landmarks = [re.sub(r'^[\s\-–—•*]+', '', note).strip().rstrip(',;')
                 for note in str(office.get('address_note') or '').splitlines()]
    landmarks = [note for note in landmarks if note]
    if landmarks:
        lines.append('Ориентиры %s: %s.' % (name, '; '.join(landmarks).rstrip('.')))
    for phone in _office_phones(office, parks):
        if phone['park']:
            line = 'Телефон парка %s в %s: %s' % (phone['park'], name, phone['phone'])
        else:
            line = 'Телефон %s: %s' % (name, phone['phone'])
        if phone.get('note'):
            line += ' (%s)' % phone['note']
        lines.append(line + '.')
    week = week_lines(office.get('schedule'))
    if week:
        lines.append('График %s: %s.' % (name, week))
    status = office_status(office, day)
    today = day.strftime('%d.%m.%Y')
    live = _live_text(office, day, now)
    if live:
        lines.append('Статус %s, %s: %s.' % (name, today, live))
    elif status['state'] == 'open':
        text = 'Статус %s на %s: открыт' % (name, today)
        if status.get('hours'):
            text += ', часы работы %s' % status['hours']
        lines.append(text + '.')
    elif status['state'] == 'closed':
        text = 'Статус %s на %s: закрыт' % (name, today)
        if status.get('reopens'):
            text += ', откроется %s' % _dmy(status['reopens'])
        elif 'reopens' in status:
            text += ', срок открытия не известен'
        if status.get('note'):
            text += ' (%s)' % status['note']
        lines.append(text + '.')
    elif status['state'] == 'none':
        lines.append('График %s не заполнен.' % name)
    return '\n'.join(lines)


def _city_text(entry):
    city = entry['city']
    name = city.get('name')
    lines = []
    tariffs = entry['tariffs']
    if tariffs:
        lines.append('Комиссия Яндекса в городе %s по тарифам:' % name)
        for tariff in tariffs:
            lines.append('%s, тариф %s: комиссия Яндекса %s.' % (
                name, tariff['name'], format_percent(tariff['commission'])))
    options = [option for option in city.get('option_commissions') or []
               if option.get('commission') is not None]
    for option in options:
        lines.append('%s, доп. опция «%s»: комиссия %s (добавляется к комиссии тарифа).' % (
            name, option.get('name'), format_percent(option.get('commission'))))
    if city.get('park_commission') is not None:
        lines.append('%s: комиссия парка %s.' % (name, format_percent(city['park_commission'])))
    return '\n'.join(lines)


def _all_cities_text(cities, tariffs):
    """Комиссии по всем городам — на вопрос без города («комиссия по городам»)."""
    lines = []
    for city in cities:
        chosen, _narrowed = city_commissions(city, set(tariffs))
        if tariffs:
            chosen = [tariff for tariff in chosen if normalize(tariff['name']) in tariffs]
        if not chosen:
            continue
        lines.append('%s: %s.' % (city.get('name'), ', '.join(
            '%s %s' % (tariff['name'], format_percent(tariff['commission']))
            for tariff in chosen)))
    if not lines:
        return ''
    return 'Комиссия Яндекса по тарифам и городам:\n' + '\n'.join(lines)


def _all_offices_text(offices, day, now=None):
    """Офисы парков всех городов с адресом, часами и статусом — на вопрос без
    города («адреса офисов», «какие офисы сейчас открыты»)."""
    today = day.strftime('%d.%m.%Y')
    lines = ['Офисы парков по городам, статус на %s%s:' % (
        today, ', сейчас %s' % now.strftime('%H:%M') if now is not None else '')]
    for office in sorted(offices, key=lambda item: (normalize(item.get('city')), _office_order(item))):
        if office.get('kind') == 'partner':
            continue
        if office.get('no_office'):
            lines.append('%s: офиса нет, принимают по телефону.' % office.get('city'))
            continue
        if not office.get('address'):
            continue
        status = office_status(office, day)
        state = _live_text(office, day, now, clock=False)
        if not state:
            state = {'open': 'открыт', 'closed': 'закрыт'}.get(status['state'], 'график не заполнен')
            if status['state'] == 'open' and status.get('hours'):
                state += ', сегодня %s' % status['hours']
            if status['state'] == 'closed' and status.get('reopens'):
                state += ', откроется %s' % _dmy(status['reopens'])
        lines.append('%s, офис «%s»: %s — %s.' % (office.get('city'), office.get('name'),
                                                str(office['address']).strip().rstrip('.,'),
                                                state))
    return '\n'.join(lines) if len(lines) > 1 else ''


def _row(index, *, kind, tab, title, heading, text, space_id, ref_id=None, ref_city=None):
    return {
        # Отрицательный id: у настоящих кусков он из BIGSERIAL и положителен,
        # а сшивка источников ищет фрагмент по chunk_id.
        'chunk_id': -(index + 1),
        'article_id': None,
        'chunk_idx': 0,
        'title': title,
        'slug': '',
        'heading_path': heading,
        'text': text,
        'requires_ack': False,
        'historical': False,
        'similarity': None,
        'found_by': [DIRECTORY_BRANCH],
        'directory_hit': True,
        'source_kind': kind,
        'tab': tab,
        'ref_id': ref_id,
        'ref_city': ref_city,
        'space_id': space_id,
    }


def analyze_question(question, search_query=None):
    """Разбор вопроса помощнику вместе с прошлыми репликами.

    search_query (answer.enrich_query) — прошлые реплики И сам вопрос в конце.
    Склеить его с вопросом значило бы прочитать вопрос дважды: «комиссия
    сервиса» стала бы фразой из четырёх слов и потеряла таблицу комиссий, а
    «123 45 67» — номером «12345671234567». Поэтому реплики идут ПОСЛЕ вопроса
    (город из вопроса — первым: «а в Шымкенте?» после Алматы), а число слов,
    цифры телефона и «лишние слова» — только из самого вопроса.
    """
    question = str(question or '')
    context = str(search_query or '')
    if question and context.endswith(question):
        context = context[:len(context) - len(question)]
    context = context.strip()
    if not context or context == question:
        return analyze(question)
    analysis = analyze('%s %s' % (question, context))
    own = analyze(question)
    analysis.update(query=own['query'], words=own['words'], digits=own['digits'],
                    own_variants=own['variants'])
    if own['tariffs']:
        # «а комфорт?» после эконома — про комфорт; город так же (plan).
        analysis['tariffs'] = own['tariffs']
    return analysis


def asks_directory(analysis):
    """Есть ли в вопросе хоть какой-то признак справочника — до чтения базы.

    Слабые признаки тоже: решает plan(), а без них помощник молчал на «Сколько
    процентов берёт Яндекс в Кокшетау?», хотя поиск на те же слова отвечает."""
    return bool(analysis['office_intent'] or analysis['commission_intent']
                or analysis.get('address_hint') or analysis.get('office_hint')
                or analysis.get('percent_hint'))


def ai_rows(cursor, ctx, question, *, space_id, search_query=None):
    """Фрагменты справочников к вопросу помощника — живые, на сейчас.

    space_id — то, что помощник уже выбрал (routes_ai.effective_space). Его
    граница шире справочной: помощник пускает гостей, справочник — нет,
    поэтому пространство проверяется здесь ещё раз (directory_space).
    search_query — вопрос, дополненный прошлыми репликами («а в Шымкенте?»):
    город и намерение часто только в них.
    """
    space = directory_space(cursor, ctx, space_id)
    if space is None:
        return []
    analysis = analyze_question(question, search_query)
    # Помощнику фраза длинная — это нормально, поэтому «только город» тут
    # не нужен: без признака справочник молчит, с ним — решает plan().
    if not asks_directory(analysis):
        return []
    now = office_now()
    day = now.date()
    offices, cities, parks = load(cursor, space=space, day=day)
    return build_ai_rows(analysis, space=space, offices=offices, cities=cities,
                         parks=parks, day=day, now=now)


def build_ai_rows(analysis, *, space, offices, cities, parks, day, now=None):
    """Фрагменты помощника из уже прочитанного справочника. Без базы — для тестов.

    now — текущее время офисов: с ним статус офиса живой («сейчас 20:30 —
    закрыт до завтра 09:00»), без него — за день."""
    if not asks_directory(analysis):
        return []
    decision = plan(analysis, offices=offices, cities=cities, parks=parks, features=space,
                    limit=AI_OFFICES)
    named_parks = match_parks(analysis, parks) if parks else []
    rows = []
    for entry in decision['offices']:
        office = entry['office']
        rows.append(_row(len(rows), kind='office', tab='offices',
                         title='Офисы', heading='%s › %s' % (office.get('city'), office.get('name')),
                         text=_office_text(entry, day, named_parks, now), space_id=space['id'],
                         ref_id=office['id'], ref_city=office.get('city')))
    # Все офисы — только когда спросили именно про офисы: «адреса» в вопросе
    # без города — не повод класть в промпт сорок адресов.
    if decision['offices_tab'] and not decision['offices'] and analysis.get('office_noun'):
        text = _all_offices_text(offices, day, now)
        if text:
            rows.append(_row(len(rows), kind='offices', tab='offices', title='Офисы',
                             heading='Все офисы', text=text, space_id=space['id']))
    for entry in decision['cities']:
        text = _city_text(entry)
        if text:
            city = entry['city']
            rows.append(_row(len(rows), kind='city', tab='cities', title='Города',
                             heading='%s › Комиссия Яндекса' % city.get('name'), text=text,
                             space_id=space['id'], ref_id=city['id'],
                             ref_city=city.get('name')))
    # Таблица всех городов — на вопрос про числа («сколько», «по городам»,
    # «тарифов Яндекса»), про названный тариф или короткий («комиссия»).
    named_tariffs = bool(decision['cities_tab'] and decision['cities_tab']['tariffs'])
    wants_table = (analysis.get('numbers_question') or named_tariffs
                   or len(analysis['words']) <= 3)
    if decision['cities_tab'] is not None and wants_table:
        text = _all_cities_text(cities, decision['cities_tab']['tariffs'])
        if text:
            rows.append(_row(len(rows), kind='cities', tab='cities', title='Города',
                             heading='Комиссия Яндекса по городам', text=text,
                             space_id=space['id']))
    return rows
