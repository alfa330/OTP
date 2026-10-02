# -*- coding: utf-8 -*-
"""Фильтры поиска «Списков Байги» → условие SQL. Ни базы, ни Flask.

Что умеет поиск (постановка #356, п. 5):

* быстрый поиск по ФИО, номеру ВУ и ID водителя — без учёта регистра и
  казахских букв (Жусип = Жүсіп); каждое слово запроса обязано найтись;
* вставка списка ID или номеров ВУ — поиск сразу по многим водителям; кто из
  списка не нашёлся, экран называет поимённо;
* фильтры: неделя, зачёт, город, таксопарк, приз (+ «только с призом»),
  диапазоны по позиции, сумме, поездкам и сумме приза;
* сортировка по любой колонке, страницы по 50/100/500.

Условие собирается ТОЛЬКО из именованных параметров `%(имя)s`: пользовательский
текст в SQL не попадает никогда, а знак процента в шаблоне LIKE едет значением
параметра (голый `%` в тексте запроса psycopg2 принимает за плейсхолдер).
"""

import re
from datetime import date

from . import parse

PAGE_SIZES = (50, 100, 500)
DEFAULT_PAGE_SIZE = 50

# Сколько значений можно вставить списком. Неделя — 900 водителей; две тысячи
# покрывают «все из двух недель» и не дают превратить поиск в выгрузку базы.
MAX_LIST_TOKENS = 2000
MAX_QUERY_WORDS = 8
MAX_QUERY_LENGTH = 200

# Ключ сортировки → выражение. Белый список: из запроса приходит только ключ.
SORT_COLUMNS = {
    'week': 'r.period_start',
    # Зачёт — в едином для всех недель порядке листов: у каждой недели свой
    # номер листа (листы добавляют и убирают), и сортировка по нему мешала бы
    # зачёты разных недель. Порядок зачёта — его самый ранний лист в выборке.
    'zachet': ('MIN(r.sheet_order) OVER (PARTITION BY r.zachet)', 'r.zachet'),
    'position': 'r.position',
    'driver': 'r.driver_name',
    'prize': 'r.prize_amount',
    'amount': 'r.amount',
    'trips': 'r.trips',
    'city': 'r.city',
    'park': 'r.park',
    'license': 'r.license_key',
    'driver_id': 'r.driver_key',
}
DEFAULT_SORT = ('week', 'desc')

# Хвост любого порядка — порядок файла: свежая неделя, лист, место. Тогда
# одинаковые значения (сотня «Алматы») стоят предсказуемо, а не как придётся,
# и страница 2 не повторяет строк страницы 1.
_TIEBREAK = 'r.period_start DESC, r.sheet_order, r.position, r.id'

# Диапазоны: ключ фильтра → колонка.
RANGES = {
    'position': 'r.position',
    'amount': 'r.amount',
    'trips': 'r.trips',
    'prize_amount': 'r.prize_amount',
}

PRIZE_ANY = 'any'     # только с призом
PRIZE_NONE = 'none'   # без приза

_HEX_ID = re.compile(r'^[0-9a-fA-F]{32}$')
_SPLIT = re.compile(r'[\n\r,;\t]+')
_WORDS = re.compile(r'\S+')
_LIKE_SPECIAL = re.compile(r'([\\%_])')

_MAX_INT = 10 ** 12


def _text(value, limit=255):
    return parse.clean_text(value)[:limit] if value not in (None, '') else ''


def _int_or_none(value):
    number = parse.to_int(value)
    if number is None or abs(number) > _MAX_INT:
        return None
    return number


def _period(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or '').strip()[:10])
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Вставленный список
# ─────────────────────────────────────────────────────────────────────────────

def split_tokens(text):
    """Вставленный текст → значения по одному.

    Делим по строкам, запятым, точкам с запятой и табуляции — так приходят
    колонка из Excel и список из мессенджера. Пробел внутри куска делит его
    только тогда, когда каждая часть сама похожа на ID или номер ВУ: «AB 123456»
    — один номер с пробелом, а «AB123456 CD654321» — два номера.
    """
    tokens = []
    for piece in _SPLIT.split(str(text or '')):
        piece = piece.strip()
        if not piece:
            continue
        parts = piece.split()
        if len(parts) > 1 and all(_looks_like_key(part) for part in parts):
            tokens.extend(parts)
        else:
            tokens.append(piece)
    seen = set()
    unique = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def _looks_like_key(value):
    if _HEX_ID.match(value):
        return True
    key = parse.license_key(value)
    return len(key) >= 6 and any(ch.isdigit() for ch in key) and key.isalnum()


def _account_id_from_link(value):
    """ID водителя из ссылки Флита/CRM — тем же разбором, что у «Посылок».

    Импорт ленивый: parcels.drivers тянет requests и настройки CRM, а большинству
    вставок (голые ID и номера) он не нужен вовсе.
    """
    from parcels.drivers import extract_account_id
    return extract_account_id(value)


def classify_tokens(tokens):
    """Значения списка → (ключи ID, ключи ВУ, {значение: [(вид, ключ), …]}).

    Ссылку на водителя во Флите сотрудник копирует из адресной строки — её
    принимаем наравне с голым ID. Всё прочее ищется и как номер ВУ (он бывает
    из одних цифр — российский формат), и как ID: файл принимает и
    нестандартные ID (с предупреждением), и вставленный такой ID обязан
    находить свою строку.
    """
    driver_keys, license_keys, key_of = [], [], {}
    for token in tokens[:MAX_LIST_TOKENS]:
        if _HEX_ID.match(token):
            key = token.lower()
            driver_keys.append(key)
            key_of[token] = [('driver', key)]
            continue
        if '/' in token or '?' in token or '=' in token:
            account = _account_id_from_link(token)
            if account:
                driver_keys.append(account)
                key_of[token] = [('driver', account)]
                continue
        keys = []
        as_license = parse.license_key(token)
        if as_license:
            license_keys.append(as_license)
            keys.append(('license', as_license))
        as_driver = parse.driver_key(token)
        if as_driver:
            driver_keys.append(as_driver)
            keys.append(('driver', as_driver))
        if keys:
            key_of[token] = keys
    return sorted(set(driver_keys)), sorted(set(license_keys)), key_of


# ─────────────────────────────────────────────────────────────────────────────
# Фильтры
# ─────────────────────────────────────────────────────────────────────────────

def normalize(payload):
    """Тело запроса → чистые фильтры. Негодное значение — как отсутствующее.

    Отказом на кривое значение экран ничего не выиграл бы: фильтры он собирает
    сам, а сломанная ссылка из адресной строки должна открывать раздел, а не
    ошибку.
    """
    data = payload if isinstance(payload, dict) else {}
    query = _text(data.get('q'), MAX_QUERY_LENGTH)
    prize = _text(data.get('prize'))
    filters = {
        'q': query,
        'period': _period(data.get('period')),
        'zachet': _text(data.get('zachet'), 64),
        'city': _text(data.get('city'), 120),
        'park': _text(data.get('park')),
        'prize': prize,
        'list': split_tokens(data.get('list'))[:MAX_LIST_TOKENS],
        # Точный ID водителя — для карточки водителя: его история ищется по ключу,
        # а не разбором списка (нестандартный ID там читался бы как номер ВУ).
        'driver': parse.driver_key(data.get('driver')) if data.get('driver') else '',
    }
    for key in RANGES:
        low = _int_or_none(data.get('%s_min' % key))
        high = _int_or_none(data.get('%s_max' % key))
        if low is not None and high is not None and low > high:
            low, high = high, low
        filters['%s_min' % key] = low
        filters['%s_max' % key] = high
    return filters


def is_empty(filters):
    return not any(value not in (None, '', []) for value in filters.values())


def public(filters):
    """Фильтры для журнала выгрузок: только заданные, даты строками."""
    out = {}
    for key, value in filters.items():
        if value in (None, '', []):
            continue
        if isinstance(value, date):
            value = value.isoformat()
        if key == 'list':
            value = {'count': len(value)}
        out[key] = value
    return out


def _like(value):
    return '%' + _LIKE_SPECIAL.sub(r'\\\1', value) + '%'


def where(filters):
    """(условие SQL, параметры). Пустые фильтры — 'TRUE'."""
    clauses, params = [], {}

    if filters.get('period'):
        clauses.append('r.period_start = %(period)s')
        params['period'] = filters['period']
    for key in ('zachet', 'city', 'park'):
        if filters.get(key):
            clauses.append('r.%s = %%(%s)s' % (key, key))
            params[key] = filters[key]

    prize = filters.get('prize')
    if prize == PRIZE_ANY:
        clauses.append('r.has_prize')
    elif prize == PRIZE_NONE:
        clauses.append('NOT r.has_prize')
    elif prize:
        clauses.append('r.prize_name = %(prize)s')
        params['prize'] = prize

    for key, column in RANGES.items():
        low, high = filters.get('%s_min' % key), filters.get('%s_max' % key)
        if low is not None:
            clauses.append('%s >= %%(%s_min)s' % (column, key))
            params['%s_min' % key] = low
        if high is not None:
            clauses.append('%s <= %%(%s_max)s' % (column, key))
            params['%s_max' % key] = high

    # Каждое слово запроса обязано найтись. Слово сворачивается тем же правилом,
    # что строки при загрузке (parse.fold_text), и ищется ещё в латинском виде —
    # номер ВУ, набранный в русской раскладке («ав123456»), тоже находится.
    words = _WORDS.findall(parse.fold_text(filters.get('q')))[:MAX_QUERY_WORDS]
    for index, word in enumerate(words):
        name = 'w%d' % index
        params[name] = _like(word)
        variant = parse.latin_variant(word)
        if variant != word:
            params[name + 'l'] = _like(variant)
            clauses.append("(r.search_text LIKE %%(%s)s ESCAPE '\\' OR r.search_text LIKE %%(%sl)s ESCAPE '\\')"
                           % (name, name))
        else:
            clauses.append("r.search_text LIKE %%(%s)s ESCAPE '\\'" % name)

    if filters.get('driver'):
        clauses.append('r.driver_key = %(driver)s')
        params['driver'] = filters['driver']

    if filters.get('list'):
        # Только ветки, у которых есть ключи. Заглушка [''] вместо пустого
        # списка находила бы КАЖДУЮ строку с пустым номером ВУ — чужих водителей
        # в выдаче, в итогах, в выгрузке и в карточке водителя.
        driver_keys, license_keys, _ = classify_tokens(filters['list'])
        parts = []
        if driver_keys:
            params['list_ids'] = driver_keys
            parts.append('r.driver_key = ANY(%(list_ids)s)')
        if license_keys:
            params['list_licenses'] = license_keys
            parts.append('r.license_key = ANY(%(list_licenses)s)')
        clauses.append('(%s)' % ' OR '.join(parts) if parts else 'FALSE')

    return (' AND '.join(clauses) if clauses else 'TRUE'), params


def order_by(sort, direction):
    """ORDER BY по белому списку. Незнакомый ключ — порядок файла."""
    key = sort if sort in SORT_COLUMNS else DEFAULT_SORT[0]
    desc = str(direction or '').lower() == 'desc' if sort in SORT_COLUMNS else DEFAULT_SORT[1] == 'desc'
    columns = SORT_COLUMNS[key] if isinstance(SORT_COLUMNS[key], tuple) else (SORT_COLUMNS[key],)
    direction = 'DESC' if desc else 'ASC'
    head = ', '.join('%s %s NULLS LAST' % (column, direction) for column in columns)
    return '%s, %s' % (head, _TIEBREAK), key, ('desc' if desc else 'asc')


def page_args(payload):
    """(страница с 1, размер страницы) из тела запроса."""
    data = payload if isinstance(payload, dict) else {}
    size = _int_or_none(data.get('size'))
    size = size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE
    page = _int_or_none(data.get('page')) or 1
    return max(1, min(page, 100000)), size


def not_found(tokens, found_driver_keys, found_license_keys):
    """Значения списка, которых нет в выборке, — в том виде, как их вставили.
    Значение найдено, если нашёлся любой из его ключей (как ID или как ВУ)."""
    _, _, key_of = classify_tokens(tokens)
    missing = []
    for token in tokens:
        keys = key_of.get(token) or []
        found = any(key in (found_driver_keys if kind == 'driver' else found_license_keys)
                    for kind, key in keys)
        if not found:
            missing.append(token)
    return missing
