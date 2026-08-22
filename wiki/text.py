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

# ─────────────────────────────────────────────────────────────────────────────
# СВЁРТКА КАЗАХСКИХ БУКВ К РУССКИМ ДВОЙНИКАМ
#
# Зачем. Люди набирают казахские слова на русской раскладке: «Казына» вместо
# «Қазына», «Азаттык» вместо «Азаттық», «Тиркеуге» вместо «Тіркеуге». Постгресу
# это разные слова: to_tsvector('russian', 'Қазына') даёт лексему 'қазын', а
# 'Казына' — 'казын', и совпадения нет вообще. Замер на проде: акция «7 Қазына»
# из статьи «Все акции» не находилась по запросу «7 Казына» ни поиском, ни
# помощником — и это не край случая, а типовое поведение оператора.
#
# Триграммы страдают так же: similarity('Қазына', 'Казына') = 0,4 против 1,0
# после свёртки, то есть ниже порога поиска по названию.
#
# Свёртка — тот же приём, которым в разделе уже свёрнуто ё→е (см. шапку
# _SEARCH_STATEMENTS в wiki/schema.py), и по той же причине: конфигурация
# 'russian' не считает эти буквы вариантами одной. Обе стороны — и индекс, и
# запрос — обязаны сворачиваться ОДИНАКОВО, иначе становится только хуже.
#
# Стоимость: казахское слово получает русский стем («қазын» → «казын»), то есть
# два разных казахских слова с қ и к теперь неразличимы. В корпусе (три статьи с
# казахскими буквами: «Азаттық», «Қазына», «Тіркеуге») таких пар нет, а выигрыш
# — найти то, что человек ищет так, как он это пишет.
# ─────────────────────────────────────────────────────────────────────────────

KAZAKH_FOLD = (
    ('ә', 'а'), ('ғ', 'г'), ('қ', 'к'), ('ң', 'н'), ('ө', 'о'),
    ('ұ', 'у'), ('ү', 'у'), ('һ', 'х'), ('і', 'и'), ('ё', 'е'),
)

# Аргументы для translate() в SQL: обе строки одной длины, верхний и нижний
# регистр подряд. Собираются здесь, чтобы во всех запросах раздела стояло одно
# и то же правило, а не восемь его копий.
SQL_FOLD_FROM = ''.join(pair[0] + pair[0].upper() for pair in KAZAKH_FOLD)
SQL_FOLD_TO = ''.join(pair[1] + pair[1].upper() for pair in KAZAKH_FOLD)

_FOLD_TABLE = {ord(source): target
               for lower, upper in KAZAKH_FOLD
               for source, target in ((lower, upper), (lower.upper(), upper.upper()))}


def fold_kazakh(text):
    """Свернуть казахские буквы к русским двойникам (и ё к е)."""
    return str(text or '').translate(_FOLD_TABLE)


def sql_fold(expression):
    """Обернуть SQL-выражение свёрткой. Одна формулировка на весь раздел."""
    return "translate(%s, %s, %s)" % (
        expression, _sql_literal(SQL_FOLD_FROM), _sql_literal(SQL_FOLD_TO))


def _sql_literal(value):
    return "'%s'" % value.replace("'", "''")


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
    # Казахские буквы здесь НЕ сворачиваются намеренно: следом идёт
    # транслитерация, и по оригиналу «Қазына» она даёт алиас «qazyna», а по
    # свёрнутому «казына» — только «kazyna». Свёртка нужна на сравнении текста
    # (SQL и запрос), а не на порождении алиасов: там она сузила бы охват.
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


def alias_phrase(text):
    """«хундай солярис» -> «hyundai solaris»: пословная подстановка алиасов.

    Алиасы попадают в варианты ОТДЕЛЬНЫМИ словами ('hyundai', 'solaris'), и
    пара «марка + модель» из-за этого рассыпается: по одинокому 'hyundai' и
    поиск, и справочник классификатора отвечают первой моделью марки, а не
    запрошенной. Собранная обратно фраза возвращает точную пару.

    Написание выбирается детерминированно (первое по алфавиту), иначе вариант
    плясал бы от запуска к запуску: значения индекса — множества.
    """
    words = [word for word in _WORD_SPLIT.split(normalize_text(text)) if word]
    if len(words) < 2:
        return ''
    substituted = False
    phrase = []
    for word in words:
        options = sorted(_ALIAS_INDEX.get(word, ()))
        phrase.append(options[0] if options else word)
        substituted = substituted or bool(options)
    return ' '.join(phrase) if substituted else ''


# Числительные, с которых начинаются названия акций. Список короткий и только
# КАЗАХСКИЙ — с буквами, которых нет в русском алфавите. Это не лень, а защита
# от ложных разбиений: русские числительные являются началом обычных слов
# («онлайн» начинается с «он», «одинаковый» с «один»), и разрезать по ним значит
# насыпать в поиск мусора. У казахских такого пересечения нет, потому что слово
# обязано содержать ә, ғ, қ, ң, ө, ұ, ү, һ или і.
_KZ_NUMERALS = ('бір', 'екі', 'үш', 'төрт', 'бес', 'жеті', 'сегіз', 'тоғыз')
_KZ_SPECIFIC = set('әғқңөұүһі')


def split_glued_numeral(text):
    """«жетіқазына» -> «жеті қазына». Пусто, если разрезать нечего.

    Зачем. Акция записана в вике как «7 Қазына», а называют её слитно —
    распознавание речи так и отдаёт: «Жетіқазына». Слитный токен не совпадает
    ни с чем: в куске лежат «7» и «казына», а в запросе одно длинное слово, и
    редкая лексема «казын», по которой этот кусок только и находится, в запрос
    не попадает вовсе. Проверено на проде 22.08.2026 — помощник вики отвечал
    «в доступных вам статьях этого нет», хотя акция в статье «Все акции» есть.

    Разрезаем ТОЛЬКО когда остаток осмысленной длины: «бесік» (колыбель) от
    «бес» + «ік» уберечь важнее, чем найти лишнее.
    """
    words = [w for w in _WORD_SPLIT.split(normalize_text(text)) if w]
    out, changed = [], False
    for word in words:
        if not (_KZ_SPECIFIC & set(word)):
            out.append(word)
            continue
        for numeral in _KZ_NUMERALS:
            rest = word[len(numeral):]
            if word.startswith(numeral) and len(rest) >= 4:
                out.extend((numeral, rest))
                changed = True
                break
        else:
            out.append(word)
    return ' '.join(out) if changed else ''


def query_variants(query):
    """Варианты написания запроса, в порядке убывания близости к оригиналу.

    Порядок: исходный текст, нормализованный, транслит в обе стороны, фраза с
    подставленными алиасами, сами алиасы и только в конце — исправленная
    раскладка.

    ОТЛИЧИЕ ОТ ОРИГИНАЛА в порядке и в условии. Там раскладка стояла до алиасов,
    но это было безопасно: варианты уходили в движок параллельно и результаты
    сливались. Пока поиск перебирал варианты по очереди, до алиасов дело могло
    не дойти вовсе — запрос «BYD» возвращал «Инвентаризацию автопарка», потому
    что fix_keyboard_layout('BYD') = 'инв' и префиксный tsquery «инв:*» её
    находил. Сейчас варианты снова сливаются (см. wiki/search.py), поэтому
    порядок решает не всё — но мусорный вариант всё равно тащил бы в выдачу
    посторонние статьи, и его лучше не порождать.

    Раскладку чиним только когда её есть смысл чинить: запрос не опознан
    словарём и длиннее трёх символов. У коротких латинских запросов флип
    случайно совпадает с русским префиксом ('vw' -> 'мц', 'kia' -> 'лшф'),
    а «ntrcn» -> «текст» и «fhtylf» -> «аренда» продолжают работать.
    """
    variants = []

    def add(value):
        trimmed = str(value or '').strip()
        if trimmed and trimmed not in variants:
            variants.append(trimmed)

    add(query)
    normalized = normalize_text(query)
    add(normalized)
    # Свёрнутый вариант — отдельным пунктом, а не заменой нормализованного:
    # normalize_text обязан остаться как есть, потому что следом идёт
    # транслитерация, и по оригиналу «Қарағанды» она даёт «qaraghandy». Свёртка
    # добавляет второй путь («караганды»), не отнимая первый.
    add(fold_kazakh(normalized))
    # Слипшееся числительное разводим ДО транслитерации: разрезанному варианту
    # тоже полагаются и свёртка, и транслит.
    split = split_glued_numeral(normalized)
    if split:
        add(split)
        add(fold_kazakh(split))
    add(transliterate_cyrillic_to_latin(normalized))
    add(transliterate_latin_to_cyrillic(normalized))

    base_aliases = aliases_for_text(normalized)
    add(alias_phrase(normalized))
    for alias in base_aliases:
        add(alias)

    fixed_layout = fix_keyboard_layout(query)
    if fixed_layout != query and not base_aliases and len(str(query or '').strip()) >= 4:
        add(fixed_layout)
        normalized_fixed = normalize_text(fixed_layout)
        add(normalized_fixed)
        add(transliterate_cyrillic_to_latin(normalized_fixed))
        add(alias_phrase(normalized_fixed))
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
