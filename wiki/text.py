"""Нормализация запроса, транслитерация и алиасы — порт wiki2.0 utils/text.ts.

Этот модуль и есть вся «умность» поиска исходной вики. Синонимы, стоп-слова и
кастомные правила ранжирования в её Meilisearch НЕ настроены (проверено по
всему дереву конфигов) — работало ровно то, что здесь: приведение регистра,
транслитерация в обе стороны, исправление раскладки и словарь алиасов.

Поэтому при отказе от Meilisearch не теряется ничего существенного: движок
меняется, а этот словарь переносится один в один.

Модуль намеренно без зависимостей — ни database, ни flask: его импортируют
тесты напрямую.

Отличие от оригинала одно, и оно осознанное: там варианты запроса
размножались в четыре параллельных обращения к поисковому движку, а у нас
алиасы вычисляются ОДИН раз при сохранении статьи и ложатся в колонку
search_aliases. Поиск остаётся одним запросом к базе.
"""

import re

CYRILLIC_TO_LATIN = {
    'а': 'a', 'ә': 'ae', 'б': 'b', 'в': 'v', 'г': 'g', 'ғ': 'gh', 'д': 'd',
    'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k',
    'қ': 'q', 'л': 'l', 'м': 'm', 'н': 'n', 'ң': 'ng', 'о': 'o', 'ө': 'oe',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ұ': 'u', 'ү': 'ue',
    'ф': 'f', 'х': 'kh', 'һ': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'і': 'i', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

# Порядок важен: сначала длинные сочетания, иначе 'sh' съест часть 'shch'.
LATIN_TO_CYRILLIC = [
    ('shch', 'щ'), ('sh', 'ш'), ('ch', 'ч'), ('zh', 'ж'), ('kh', 'х'),
    ('ts', 'ц'), ('yu', 'ю'), ('ya', 'я'), ('gh', 'ғ'), ('ng', 'ң'),
    ('oe', 'ө'), ('ae', 'ә'), ('ue', 'ү'),
    ('a', 'а'), ('b', 'б'), ('v', 'в'), ('g', 'г'), ('d', 'д'), ('e', 'е'),
    ('z', 'з'), ('i', 'и'), ('y', 'ы'), ('k', 'к'), ('q', 'қ'), ('l', 'л'),
    ('m', 'м'), ('n', 'н'), ('o', 'о'), ('p', 'п'), ('r', 'р'), ('s', 'с'),
    ('t', 'т'), ('u', 'у'), ('f', 'ф'), ('h', 'һ'), ('c', 'к'),
]

# Раскладка: набрано латиницей вместо кириллицы («ntrcn» вместо «текст»).
EN_TO_RU_LAYOUT = {
    'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е', 'y': 'н', 'u': 'г',
    'i': 'ш', 'o': 'щ', 'p': 'з', '[': 'х', ']': 'ъ',
    'a': 'ф', 's': 'ы', 'd': 'в', 'f': 'а', 'g': 'п', 'h': 'р', 'j': 'о',
    'k': 'л', 'l': 'д', ';': 'ж', "'": 'э',
    'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и', 'n': 'т', 'm': 'ь',
    ',': 'б', '.': 'ю', '/': '.',
}

# Словарь алиасов из оригинала, перенесён дословно. Первый блок — техника,
# дальше марки и модели автомобилей: контент вики про таксопарк, и «хундай»
# там пишут чаще, чем «hyundai».
ALIAS_GROUPS = [
    ['meilisearch', 'meili', 'мейлисерч', 'меилисерч'],
    ['render', 'render.com', 'рендер'],
    ['vercel', 'верцел'],
    ['docker', 'докер'],
    ['postgres', 'postgresql', 'постгрес'],
    ['supabase', 'супабейс'],
    ['wireguard', 'вайргард'],
    ['wifi', 'wi-fi', 'вайфай', 'вай фай'],
    ['github', 'git hub', 'гитхаб'],
    ['node', 'nodejs', 'node.js', 'нода'],
    ['react', 'reactjs', 'реакт'],
    ['python', 'питон'],
    ['flask', 'фласк'],
    ['django', 'джанго'],
    ['redis', 'редис'],
    ['elastic', 'elasticsearch', 'эластик'],
    ['postman', 'постман'],

    ['toyota', 'тойота'],
    ['hyundai', 'хендай', 'хёндай', 'хундай'],
    ['kia', 'киа', 'кио'],
    ['volkswagen', 'vw', 'фольксваген', 'фольц'],
    ['mercedes', 'mercedes-benz', 'benz', 'мерседес', 'мерс'],
    ['bmw', 'бмв'],
    ['audi', 'ауди'],
    ['lexus', 'лексус'],
    ['nissan', 'ниссан'],
    ['chevrolet', 'шевроле', 'шеви'],
    ['mitsubishi', 'митсубиси', 'митсубиши', 'митсу'],
    ['subaru', 'субару'],
    ['mazda', 'мазда'],
    ['honda', 'хонда'],
    ['land rover', 'range rover', 'ленд ровер', 'ренж ровер', 'лендровер'],
    ['renault', 'рено'],
    ['peugeot', 'пежо'],
    ['citroen', 'ситроен'],
    ['opel', 'опель'],
    ['suzuki', 'сузуки'],
    ['skoda', 'шкода'],
    ['chery', 'чери'],
    ['geely', 'джили'],
    ['changan', 'чанган'],
    ['byd', 'бивайди'],
    ['infiniti', 'инфинити'],
    ['cadillac', 'кадиллак'],
    ['dodge', 'додж'],
    ['porsche', 'порше'],
    ['tesla', 'тесла'],
    ['lada', 'лада', 'ваз'],

    ['camry', 'камри'],
    ['solaris', 'солярис'],
    ['accent', 'акцент'],
    ['rio', 'рио'],
    ['optima', 'оптима'],
    ['elantra', 'элантра'],
    ['sonata', 'соната'],
    ['octavia', 'октавия'],
    ['rapid', 'рапид'],
    ['superb', 'суперб'],
    ['astra', 'астра'],
    ['vectra', 'вектра'],
    ['zafira', 'зафира'],
    ['golf', 'гольф'],
    ['passat', 'пассат'],
    ['polo', 'поло'],
    ['tiguan', 'тигуан'],
    ['touareg', 'туарег'],
    ['cruze', 'круз'],
    ['cobalt', 'кобальт'],
    ['aveo', 'авео'],
    ['spark', 'спарк'],
    ['malibu', 'малибу'],
    ['captiva', 'каптива'],
    ['nexia', 'нексия'],
    ['matiz', 'матиз'],
    ['gentra', 'джентра'],
    ['priora', 'приора'],
    ['granta', 'гранта'],
    ['vesta', 'веста'],
    ['kalina', 'калина'],
    ['largus', 'ларгус'],
]

# Буквы русского, казахского и английского алфавитов, цифры, пробел и дефис.
_ALLOWED = re.compile(r'[^a-zа-яёәғқңөұүһі0-9\s-]')
_SPACES = re.compile(r'\s+')
_WORD_SPLIT = re.compile(r'[\s-]+')


def _normalize_for_index(value):
    """Та же нормализация, что применяется к запросу."""
    normalized = str(value or '').lower().replace('ё', 'е')
    normalized = _ALLOWED.sub(' ', normalized)
    return _SPACES.sub(' ', normalized).strip()


# Слово -> все прочие написания той же сущности. Строится один раз при импорте:
# в оригинале на каждое слово запроса перебирались все группы подряд.
#
# ИСПРАВЛЕНО ПРОТИВ ОРИГИНАЛА. Там ключи брались из словаря как есть, а запрос
# перед сравнением проходил через normalizeText, который заменяет ё на
# латинскую e. В результате «хёндай» (ё в словаре) не совпадал с «хeндай»
# (то, во что превращался запрос), и поиск по написанию с ё не находил ничего —
# при том, что именно это написание в словарь и добавляли.
# Ключи нормализуем так же, как запрос; значения храним в исходном виде, потому
# что они уходят в поле поиска статьи и должны там читаться по-человечески.
#
# Многословные написания («render.com», «wi-fi», «git hub», «land rover») лежат
# ОТДЕЛЬНО и сопоставляются целиком. Раньше они разбирались на слова и каждый
# обрывок становился самостоятельным ключом — а это ключи 'com', 'git', 'js',
# 'вай', 'ровер'. Любая статья, где в первых 2000 символах встретилось
# «example.com», получала в search_aliases слова «render рендер» и находилась
# по запросу «рендер». Оригинал сравнивал слово с элементом группы целиком и
# такой болезни не имел.
_ALIAS_INDEX = {}
_PHRASE_INDEX = {}


def _index_alias_group(group):
    for word in group:
        parts = [p for p in _WORD_SPLIT.split(_normalize_for_index(word)) if p]
        if not parts:
            continue
        others = {a for a in group if a != word}
        if len(parts) > 1:
            _PHRASE_INDEX.setdefault(' '.join(parts), set()).update(others)
        elif len(parts[0]) >= 2:
            _ALIAS_INDEX.setdefault(parts[0], set()).update(others)


for _group in ALIAS_GROUPS:
    _index_alias_group(_group)


def normalize_text(text):
    """Регистр, ё -> е, мусорные символы -> пробел, схлопывание пробелов.

    ИСПРАВЛЕНО ПРОТИВ ОРИГИНАЛА: там ё заменялась на ЛАТИНСКУЮ e, из-за чего
    нормализованные написания «хёндай» и «хендай» РАЗЛИЧАЛИСЬ (латинская e
    против кириллической е) и совпадали только после транслитерации в латиницу.
    Кириллическая е даёт то же совпадение транслитов (е и ё обе дают 'e'),
    но вдобавок чистый русский текст — он корректно стеммится to_tsvector и
    напрямую сравнивается с нормализованным запросом.
    """
    return _normalize_for_index(text)


def transliterate_cyrillic_to_latin(text):
    if not text:
        return ''
    return ''.join(CYRILLIC_TO_LATIN.get(char, char) for char in str(text))


def transliterate_latin_to_cyrillic(text):
    if not text:
        return ''
    result = str(text)
    for latin, cyrillic in LATIN_TO_CYRILLIC:
        result = result.replace(latin, cyrillic)
    return result


def fix_keyboard_layout(text):
    """«ntrcn» -> «текст»: человек забыл переключить раскладку."""
    if not text:
        return ''
    return ''.join(EN_TO_RU_LAYOUT.get(char.lower(), char) for char in str(text))


def aliases_for_text(text):
    """Все альтернативные написания слов из текста."""
    matched = set()
    words = [word for word in _WORD_SPLIT.split(normalize_text(text)) if word]
    for word in words:
        if len(word) < 2:
            continue
        matched |= _ALIAS_INDEX.get(word, set())

    # Фразы сравниваем только по целым соседним словам. Благодаря этому
    # «render.com» (render com после нормализации) остаётся рабочим алиасом,
    # но отдельное «example.com» не срабатывает на общий обрывок «com».
    canonical_text = ' '.join(words)
    padded_text = ' %s ' % canonical_text
    for phrase, aliases in _PHRASE_INDEX.items():
        if ' %s ' % phrase in padded_text:
            matched |= aliases
    return sorted(matched)


def query_variants(query):
    """Варианты написания запроса, в порядке убывания близости к оригиналу.

    Порядок повторяет оригинал: исходный текст, нормализованный, транслит в обе
    стороны, исправленная раскладка и её транслит, затем алиасы.
    """
    variants = []

    def add(value):
        trimmed = str(value or '').strip()
        if trimmed and trimmed not in variants:
            variants.append(trimmed)

    add(query)
    normalized = normalize_text(query)
    add(normalized)
    add(transliterate_cyrillic_to_latin(normalized))
    add(transliterate_latin_to_cyrillic(normalized))

    fixed_layout = fix_keyboard_layout(query)
    add(fixed_layout)
    normalized_fixed = normalize_text(fixed_layout)
    add(normalized_fixed)
    add(transliterate_cyrillic_to_latin(normalized_fixed))

    for alias in aliases_for_text(normalized):
        add(alias)
    for alias in aliases_for_text(normalized_fixed):
        add(alias)

    return variants


def search_aliases_for_article(title, summary='', tags=(), plain_text=''):
    """Строка, которая ложится в wiki_articles.search_aliases при сохранении.

    Сюда попадают транслитерации и синонимы заголовка, описания и тегов — то,
    по чему человек может искать, но чего нет в самом тексте статьи. Тело
    статьи целиком сюда НЕ идёт: в проде вики три статьи весят по 200-900 КБ
    из-за картинок, и раздувать индекс бессмысленно.
    """
    source = ' '.join(str(part or '') for part in (title, summary, ' '.join(tags or ())))
    normalized = normalize_text(source)

    parts = {normalized}
    parts.add(transliterate_cyrillic_to_latin(normalized))
    parts.add(transliterate_latin_to_cyrillic(normalized))
    parts.update(aliases_for_text(normalized))
    # Первые слова текста дают шанс найти статью по началу содержимого.
    if plain_text:
        parts.update(aliases_for_text(plain_text[:2000]))

    return ' '.join(sorted(p for p in parts if p))
