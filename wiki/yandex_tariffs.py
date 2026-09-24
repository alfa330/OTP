"""Тарифы Яндекс Go по городу: разбор страницы taxi.yandex.*/<город>/tariff.

Источник — публичная страница тарифов для пассажира (ссылки на неё по каждому
городу прислала постановщица задачи #322). Страница собрана на сервере, и всё,
что на ней показано по заказам, лежит одним JSON в вызове
`__init__.default({...})`, по пути `initialState.zonaltariffdescription`:

  * `max_tariffs[]` — ВСЕ тарифы города сразу, а не только открытый: адрес с
    хвостом `/tariff/econom` отдаёт тот же набор, поэтому одного запроса на
    город хватает;
  * у тарифа `intervals[]` — периоды действия («Круглосуточно / ежедневно»)
    двух видов: `application` (заказ из приложения) и `call_center` (заказ по
    телефону — есть не у всех тарифов и не во всех городах);
  * у периода `price_groups[]` — «По городу» (`free_route`) и фиксированные
    маршруты («В аэропорт», «Из Актау»…), у группы `prices[]` — строки цены;
  * у строки `visual_group`: `main` — сама цена (минималка, км, минута,
    ожидание), `other` — опции заказа (детское кресло, животное, подача с
    парковки аэропорта…);
  * `callCenter.formattedPhone` — номер заказа по телефону.

Разбор кладёт это в плоский вид, который удобно показать карточкой и сравнить
с прошлым прогоном (отпечаток содержимого, а не байтов страницы: у страницы
меняется сборка фронта, а тарифы — нет).

Модуль не ходит в базу и не знает о Flask — сеть здесь одна функция
(fetch_page), и её подменяют в тестах: tests/test_wiki_cities.py.
"""

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

# Домены Яндекс Go, с которых принимаем ссылку. Закрытый список: адрес приходит
# из формы, и «проверить ссылку» не должно превращаться в запрос к чему угодно
# с сервера портала.
SOURCE_HOSTS = ('taxi.yandex.ru', 'taxi.yandex.kz', 'taxi.yandex.com')

# Таймаут и предел размера — те же рассуждения, что у базы знаний Яндекс Про
# (wiki/yandex_sync.py): страница ~420 КБ, ручная дверь работает под курсором
# из пула вики, и «подождём подольше» означает «подержим соединение подольше».
PAGE_TIMEOUT = 20
MAX_PAGE_BYTES = 8 * 1024 * 1024

# Вызов, в котором лежит состояние страницы. Ищем по нему, а не по <script id>:
# у страницы тарифов (в отличие от базы знаний Про) это не Next.js, и отдельного
# тега с данными нет.
_STATE_CALL = '__init__.default('

# Строка минимальной стоимости — по ней считается «от 400 ₸» в строке тарифа.
_MIN_PRICE_ID = 'taximeter.min_price_included_distance_and_time'

# «Количество бесплатного ожидания в пути (в секундах)» приходит числом секунд.
# Показываем минутами: «600» в карточке оператор не прочитает.
_SECONDS_RE = re.compile(r'\(в секундах\)', re.IGNORECASE)

# Подпись со скобкой в конце: «Минимальная стоимость (включено 3 мин и 1 км)»,
# «Город — Аэропорт(включено 2 ч)». Скобка — уточнение, а не часть названия.
_NOTE_RE = re.compile(r'^(.*?)\s*\(([^()]*)\)\s*$')


class TariffSourceError(Exception):
    """Страница не открылась или на ней нет тарифов. Текст — для человека."""


# ── Адрес ────────────────────────────────────────────────────────────────────

def canonical_url(url):
    """Адрес страницы тарифов без хвоста — или None, если это не она.

    Хвост отбрасываем тремя способами, и каждый встречался на деле:
      * класс тарифа — в файле постановки ссылка на Усть-Каменогорск ведёт на
        `/tariff/econom/`, а данные при этом те же, что у `/tariff`;
      * строка запроса и якорь — их приносят из браузера;
      * завершающий слэш.
    Без этого один и тот же город получал бы разные адреса, и сравнение «та же
    ли это ссылка» при правке врало бы.
    """
    text = str(url or '').strip()
    if not text:
        return None
    if '://' not in text:
        text = 'https://' + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if host not in SOURCE_HOSTS:
        return None
    segments = [s for s in parts.path.split('/') if s]
    if 'tariff' not in segments:
        return None
    # Город — сегмент прямо перед «tariff»: /almaty/tariff, /ru_kz/chimkent/tariff.
    index = segments.index('tariff')
    if index == 0:
        return None
    path = '/' + '/'.join(segments[:index + 1])
    return urlunsplit(('https', host, path, '', ''))


def zone_of_url(url):
    """Код города из адреса («almaty», «chimkent») или None."""
    canonical = canonical_url(url)
    if not canonical:
        return None
    segments = [s for s in urlsplit(canonical).path.split('/') if s]
    return segments[-2] if len(segments) >= 2 else None


# ── Разбор страницы ──────────────────────────────────────────────────────────

def extract_state(html):
    """Словарь zonaltariffdescription со страницы. Бросает TariffSourceError."""
    text = html or ''
    start = text.find(_STATE_CALL)
    if start < 0:
        raise TariffSourceError('На странице нет данных о тарифах — '
                                'похоже, это не страница тарифов Яндекс Go')
    try:
        state, _end = json.JSONDecoder().raw_decode(text, start + len(_STATE_CALL))
    except ValueError:
        raise TariffSourceError('Данные о тарифах на странице не читаются')
    zonal = ((state or {}).get('initialState') or {}).get('zonaltariffdescription')
    if not isinstance(zonal, dict):
        raise TariffSourceError('На странице нет раздела с тарифами')
    return zonal


def _money(value, sign):
    """«400 $SIGN$$CURRENCY$» → «400 ₸», «265 тенге» → «265 ₸»."""
    text = str(value if value is not None else '').strip()
    text = text.replace('$SIGN$$CURRENCY$', sign).replace('$SIGN$', sign) \
               .replace('$CURRENCY$', '')
    text = re.sub(r'\s*тенге\b', ' ' + sign, text)
    # Неразрывные пробелы из вёрстки источника — обычными: в сравнении
    # отпечатков они давали бы «изменение», которого человек не видит.
    text = text.replace(' ', ' ').replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _split_label(name):
    """«Минимальная стоимость (включено 3 мин и 1 км)» → (название, уточнение)."""
    text = re.sub(r'\s+', ' ', str(name or '').replace(' ', ' ')).strip()
    match = _NOTE_RE.match(text)
    if not match or not match.group(1):
        return text, None
    note = match.group(2).strip().rstrip('!').strip()
    return match.group(1).strip(), note or None


def _seconds_to_text(value):
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    if seconds % 60 == 0:
        return '%d мин' % (seconds // 60)
    return '%d с' % seconds


def _is_free(value):
    """Бесплатная опция: «0 ₸», «не более 0 ₸»."""
    digits = re.findall(r'\d+(?:[.,]\d+)?', value or '')
    return bool(digits) and all(float(d.replace(',', '.')) == 0 for d in digits)


def _rows(prices, sign):
    """Строки цены группы: (строки цены, опции, цена маршрута или None).

    Раскладываем по КОДУ строки, а не по visual_group: у фиксированного
    маршрута и сама цена («Город — Аэропорт, 1900 ₸», код fixed_route), и
    «Далее 10 ₸/мин» (taximeter.*) лежат у Яндекса в «прочем» — рядом с
    детским креслом. По группе показа они уехали бы в опции, а маршрут остался
    бы без цены.

    Соседние строки с одинаковой подписью сливаются: «Далее по городу» у
    Яндекса — это ДВЕ строки (58 ₸/км и 27 ₸/мин), а читается одна цена.
    """
    rows, options, route = [], [], None
    for price in prices or []:
        if not isinstance(price, dict):
            continue
        label, note = _split_label(price.get('name'))
        if not label:
            continue
        code = str(price.get('id') or '')
        value = _money(price.get('price'), sign)
        if _SECONDS_RE.search(str(price.get('name') or '')):
            # «Количество бесплатного ожидания в пути (в секундах)» — это цена
            # поездки, а не опция заказа, хоть Яндекс и кладёт её в «прочее».
            minutes = _seconds_to_text(value)
            if minutes:
                rows.append({'label': 'Бесплатное ожидание в пути', 'value': minutes,
                             'note': None})
            continue
        if code == 'fixed_route':
            route = {'label': label, 'note': note, 'value': value}
            continue
        if price.get('visual_group') == 'other' and not code.startswith('taximeter.'):
            options.append({'label': label, 'value': value, 'note': note,
                            'free': _is_free(value)})
            continue
        if rows and rows[-1]['label'] == label and rows[-1]['note'] == note:
            rows[-1]['value'] = rows[-1]['value'] + ' + ' + value
            continue
        rows.append({'label': label, 'value': value, 'note': note})
    return rows, options, route


def _interval(interval, sign):
    """Период тарифа: цена по городу, опции и фиксированные маршруты."""
    rows, options, routes = [], [], []
    seen_routes = set()
    for group in interval.get('price_groups') or []:
        if not isinstance(group, dict):
            continue
        group_rows, group_options, route = _rows(group.get('prices'), sign)
        options.extend(group_options)
        if group.get('id') == 'free_route' or \
                str(group.get('name') or '').startswith('По городу'):
            rows.extend(group_rows)
            continue
        if route:
            title, note, value = route['label'], route['note'], route['value']
        else:
            title, note = _split_label(group.get('name'))
            value = None
        if not title or not (value or group_rows):
            continue
        # «В аэропорт» и «Из Актау» у Яндекса — две группы с ОДНИМ маршрутом
        # «Город — Аэропорт» по одной цене: в карточке он нужен один раз.
        key = json.dumps([title, value, group_rows], ensure_ascii=False, sort_keys=True)
        if key in seen_routes:
            continue
        seen_routes.add(key)
        routes.append({'title': title, 'note': note, 'value': value, 'rows': group_rows})
    # Одна и та же опция встречается в нескольких группах одного периода —
    # в карточке она нужна один раз.
    seen, unique = set(), []
    for option in options:
        key = (option['label'], option['value'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(option)
    return {
        'name': re.sub(r'\s+', ' ', str(interval.get('name') or '')).strip() or None,
        'title': re.sub(r'\s+', ' ', str(interval.get('title') or '')).strip() or None,
        'rows': rows,
        'options': unique,
        'routes': routes,
    }


def _phone_diff(app, phone):
    """Чем заказ по телефону отличается от заказа из приложения.

    Показывать всю цену второй раз — шум: у «Эконома» в Алматы отличий два
    (бесплатное ожидание 4 мин вместо 2 и надбавка 80 ₸), а строк двенадцать.
    """
    app_values = {row['label']: row['value'] for row in (app or {}).get('rows', [])}
    return [row for row in (phone or {}).get('rows', [])
            if app_values.get(row['label']) != row['value']]


def _from_price(interval):
    """Цена «от»: минималка, иначе первая строка цены."""
    rows = (interval or {}).get('rows') or []
    for row in rows:
        if row['label'].startswith('Минимальная стоимость'):
            return row['value']
    return rows[0]['value'] if rows else None


def parse_state(zonal):
    """Нормализованные тарифы города из zonaltariffdescription."""
    if zonal.get('isZoneUnsupported'):
        raise TariffSourceError('Яндекс Go не работает в этом городе')
    sign = ((zonal.get('currency_rules') or {}).get('sign') or '₸').strip() or '₸'

    tariffs = []
    for raw in zonal.get('max_tariffs') or []:
        if not isinstance(raw, dict) or not raw.get('class'):
            continue
        app_intervals, phone_intervals = [], []
        for interval in raw.get('intervals') or []:
            if not isinstance(interval, dict):
                continue
            parsed = _interval(interval, sign)
            if interval.get('category_type') == 'call_center':
                phone_intervals.append(parsed)
            else:
                app_intervals.append(parsed)
        if not app_intervals and not phone_intervals:
            continue
        main = app_intervals[0] if app_intervals else phone_intervals[0]
        tariffs.append({
            'class': str(raw['class']),
            'name': re.sub(r'\s+', ' ', str(raw.get('name') or raw['class'])).strip(),
            'from': _from_price(main),
            'intervals': app_intervals or phone_intervals,
            'by_phone': bool(phone_intervals),
            'phone_diff': _phone_diff(main, phone_intervals[0]) if phone_intervals
            and app_intervals else [],
        })
    if not tariffs:
        raise TariffSourceError('На странице не нашлось ни одного тарифа')

    call_center = zonal.get('callCenter') or {}
    return {
        'zone': zonal.get('zoneName') or None,
        'phone': call_center.get('formattedPhone') or call_center.get('phone') or None,
        'currency': sign,
        'tariffs': tariffs,
    }


def parse_page(html):
    """HTML страницы тарифов → нормализованные тарифы. Бросает TariffSourceError."""
    return parse_state(extract_state(html))


def fingerprint(data):
    """Отпечаток содержимого: меняется, только когда изменились сами тарифы."""
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


# ── Сеть ─────────────────────────────────────────────────────────────────────

def fetch_page(url):
    """HTML страницы тарифов. Бросает TariffSourceError с понятным текстом."""
    canonical = canonical_url(url)
    if not canonical:
        raise TariffSourceError('Это не ссылка на тарифы Яндекс Go')
    try:
        import requests
    except ImportError:                                     # pragma: no cover
        raise TariffSourceError('На сервере нет библиотеки requests')
    # Заголовок браузера — тот же, что у сверки базы знаний: без него часть
    # хостов Яндекса отвечает роботу иначе.
    from .yandex_sync import USER_AGENT
    try:
        response = requests.get(canonical, timeout=PAGE_TIMEOUT, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'ru,en;q=0.8',
        })
    except Exception as error:                              # noqa: BLE001
        raise TariffSourceError('Страница Яндекса не открылась: %s' % str(error)[:200])
    if response.status_code == 404:
        raise TariffSourceError('По этой ссылке у Яндекса страницы нет (404)')
    if response.status_code != 200:
        raise TariffSourceError('Яндекс ответил %d' % response.status_code)
    if len(response.content or b'') > MAX_PAGE_BYTES:
        raise TariffSourceError('Страница слишком велика')
    return response.text


def read_tariffs(url, *, fetch=None):
    """Скачать и разобрать. (данные, отпечаток)."""
    data = parse_page((fetch or fetch_page)(url))
    return data, fingerprint(data)
