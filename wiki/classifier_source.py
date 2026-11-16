"""«Классификатор авто» из базы знаний Яндекс Про: разбор страниц auto-list по городам.

Источник — https://pro.yandex.com/kz-ru/<город>/knowledge-base/taxi/tariffs/auto-list
(ссылку на Алматы дал владелец 16.11.2026). У КАЖДОГО ГОРОДА СВОЯ СТРАНИЦА, и
это главное, что было неверно раньше: справочник приехал из Wiki 2.0 с годами
одного Алматы и «поправкой города» (Астана +1, Туркестан −3), а настоящие
таблицы городов расходятся с алматинской не сдвигом, а построчно. Замер по
25 городам: «Комфорт» в Актау отличается от Алматы в 987 строках из ~1100,
от Караганды — в 133. Поэтому здесь нет никаких поправок: что написано на
странице города, то и лежит в данных этого города.

ЗДЕСЬ НЕТ НИ СЕТИ, НИ БАЗЫ — как в yandex_pro.py и yandex_tariffs.py. Сюда
приходит уже скачанное состояние страниц (__NEXT_DATA__), отсюда уходит
справочник формы VERSION. Скачивание и запись — wiki/classifier_store.py.

ЧТО НА СТРАНИЦЕ (props.initialProps.pageProps.data.article.text_components):
  * AccordionStart «Тариф «Комфорт»» — тариф города; внутри Table (марка и
    модель → «от 2015» / «не допускается» / «не допускается (кроме машин
    2024–2026 годов, …)») и YTextArea — условия тарифа текстом;
  * у тарифа бывает не таблица, а правило текстом: «не старше 1988 года
    выпуска, любой марки» (Эконом почти везде, Межгород в части городов);
  * верхние YTextArea — общие требования и сноска «*Зарегистрированным в
    сервисе считается…», к которой отсылают звёздочки в таблицах;
  * TariffCarClassifier — виджет Яндекса, данных в JSON у него нет.

ЛОВУШКИ ИСТОЧНИКА (все проверены на 25 живых страницах 16.11.2026):

1. AUDIENCE (allowed_ids) ЗДЕСЬ НЕ ПРИМЕНЯЕТСЯ. Сервер уже собирает страницу
   под город из адреса, а фронт Яндекса allowed_ids не читает вовсе (в его
   бандле нет ни одного упоминания). Метки при этом неполные: «Комфорт+» в
   Актау подписан аудиторией «Алматы, Астана, Атырау», но на странице Актау он
   ЕСТЬ — со своей таблицей на 1114 строк, и headless Chrome показывает его
   после гидратации. Отфильтровав по меткам, мы бы отняли у десятка городов
   «Комфорт» и «Комфорт+». Скрытые компоненты (is_hide/isHide/is_hidden)
   по-прежнему пропускаются.
2. НОВЫЕ ПРАВИЛА — ВТОРЫМ АККОРДЕОНОМ: у Уральска рядом «Тариф «Комфорт»» и
   «Тариф «Комфорт» (новые правила с 23 июля 2026 года)». Действует последний
   из вступивших в силу; ещё не вступивший уходит в next.
3. СТРОКИ С ПЕРИОДОМ: «Changan Alsvin (до 15 августа 2025)» и «… (после 15
   августа 2025)» — одна машина двумя строками. Берётся строка, период которой
   идёт сейчас.
4. СТРОКИ-ОСТАТКИ: «Другие модели — от 1999» (все, кого нет в таблице),
   «LADA (кроме указанных моделей)», «Москвич (все модели)» — правило марки.
5. ИМЕНА ПИШУТСЯ ПО-РАЗНОМУ в таблицах разных городов: «Lynk & Co 01» и
   «Lynk&Co 08», «<p>Chery Arrizo 6</p>», «Jetour Х70» с русской «Х». Одна
   машина — один ключ (_model_key), показывается самое частое написание.
6. ШАПКИ ТАБЛИЦ РАЗНЫЕ («Марка и модель / Требования», «Модель / Год
   выпуска», «Модели авто», «<p>Марка и модель</p>») — колонки берём по
   положению, не по названию.

ФОРМА СПРАВОЧНИКА (VERSION = 2):
  cars    [[марка, модель], …] — общий указатель машин всех городов;
  lists   [{rows: [[i, год, reqs?], …], other?, brands?}, …] — таблицы
          тарифов, одинаковые у разных городов лежат один раз (Межгород —
          одна таблица на 11 городов); год 0 — «не допускается», None —
          требование не разобрано (показывается его текст);
  reqs    [{text, except?}, …] — требования длиннее «от N»: текст ячейки и
          исключение [с года, по год, 'ГГГГ-ММ-ДД' — зарегистрирована не позднее];
  notes   [текст, …] — условия тарифов текстом;
  cities  [{id, name, code, url, updated, tariffs: {ключ: {list | any, notes?, next?}}}].
"""

import hashlib
import html
import json
import re
from datetime import date

from . import yandex_pro

VERSION = 2

SOURCE_URL = 'https://pro.yandex.com/kz-ru/almaty/knowledge-base/taxi/tariffs/auto-list'
PAGE_URL = 'https://pro.yandex.com/kz-ru/%s/knowledge-base/taxi/tariffs/auto-list'
COUNTRY_CODE = 'kz'

# Порядок тарифов на экране и в ответах помощника. Ключи — те же, что были в
# справочнике Wiki 2.0: по ним живут отбор «Наличия авто» (rental/tariffs.py) и
# словари помощника, переименовывать нельзя.
TARIFFS = (
    ('econom', 'Эконом'),
    ('intercity', 'Межгород'),
    ('comfort', 'Комфорт'),
    ('comfort_plus', 'Комфорт+'),
    ('business', 'Бизнес'),
    ('electro', 'Электро'),
    ('ultima', 'Ultima (Premier)'),
)

# Заголовок аккордеона → тариф. «Комфорт+» раньше «Комфорта»: иначе плюс
# поглотится. Неизвестный тариф не теряется молча — уходит в предупреждения.
_TARIFF_TITLES = (
    ('comfort_plus', re.compile(r'комфорт\s*(?:\+|плюс)')),
    ('comfort', re.compile(r'комфорт')),
    ('econom', re.compile(r'эконом')),
    ('intercity', re.compile(r'межгород')),
    ('business', re.compile(r'бизнес')),
    ('electro', re.compile(r'электро')),
    ('ultima', re.compile(r'ultima|premier|премьер')),
)

# Код города у Яндекса → id в справочнике. Id прежнего файла сохранены: на них
# ссылаются заготовка калькулятора из поиска, чип помощника (ref_city) и
# «Наличие авто» (CITY = 'almaty').
CITY_IDS = {'nur-sultan': 'astana', 'rudniy': 'rudny'}

# Порядок городов в списке: как был у прежнего справочника, новые — следом по
# величине. Город, которого здесь нет (Яндекс завёл новый), встаёт в конец.
CITY_ORDER = (
    'almaty', 'astana', 'shymkent', 'karaganda', 'aktobe', 'taraz', 'pavlodar',
    'ust-kamenogorsk', 'semey', 'uralsk', 'kostanay', 'kyzylorda', 'atyrau', 'aktau',
    'temirtau', 'turkestan', 'kokshetau', 'taldykorgan', 'rudny', 'ekibastuz',
    'petropavlovsk', 'zhanaozen', 'zhezkazgan', 'kentau', 'aksay',
)

# Запасной список городов Казахстана — на случай, если в состоянии страницы
# не окажется справочника стран. Коды — как в адресах Яндекса.
FALLBACK_CITIES = (
    ('almaty', 'Алматы'), ('nur-sultan', 'Астана'), ('shymkent', 'Шымкент'),
    ('karaganda', 'Караганда'), ('aktobe', 'Актобе'), ('taraz', 'Тараз'),
    ('pavlodar', 'Павлодар'), ('ust-kamenogorsk', 'Усть-Каменогорск'), ('semey', 'Семей'),
    ('uralsk', 'Уральск'), ('kostanay', 'Костанай'), ('kyzylorda', 'Кызылорда'),
    ('atyrau', 'Атырау'), ('aktau', 'Актау'), ('temirtau', 'Темиртау'),
    ('turkestan', 'Туркестан'), ('kokshetau', 'Кокшетау'), ('taldykorgan', 'Талдыкорган'),
    ('rudniy', 'Рудный'), ('ekibastuz', 'Экибастуз'), ('petropavlovsk', 'Петропавловск'),
    ('zhanaozen', 'Жанаозен'), ('zhezkazgan', 'Жезказган'), ('kentau', 'Кентау'),
    ('aksay', 'Аксай'),
)

# Марки из нескольких слов. Остальные марки — первое слово названия.
MULTIWORD_BRANDS = ('LADA (ВАЗ)', 'Lynk & Co', 'Alfa Romeo', 'Aston Martin', 'Great Wall',
                    'Iran Khodro', 'Land Rover', 'Seres Aito')

OTHER_MODELS = 'Другие модели'

_MONTHS = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
           'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11,
           'декабря': 12}
_DATE = (r'(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|'
         r'октября|ноября|декабря)\s+(\d{4})')

_TAG_RE = re.compile(r'<[^>]+>')
_SPACE_RE = re.compile(r'[ \t\r\f\v]+')
_INVISIBLE_RE = re.compile('[​‌‍⁠﻿]')
_BLOCK_END_RE = re.compile(r'<\s*(?:br\s*/?|/p|/li|/h[1-6]|/div|/tr)\s*>', re.I)
_LI_RE = re.compile(r'<\s*li[^>]*>', re.I)

_FROM_RE = re.compile(r'^от\s+(\d{4})(?:\s*(?:г\.|года|г))?\s*(?:\((.*?)\)?)?\s*\.?$', re.I)
_NOT_RE = re.compile(r'^не\s+допуска[её]тся\s*(?:\((.*?)\)?)?\s*\.?$', re.I)
_YEAR_ONLY_RE = re.compile(r'^(\d{4})(?:\s*(?:г\.|года|г))?\s*$')
_EXCEPT_YEARS_RE = re.compile(r'(\d{4})(?:\s*[-–—]\s*(\d{4}))?\s+год')
_EXCEPT_DATE_RE = re.compile(r'не\s+позднее\s+' + _DATE, re.I)
_PERIOD_RE = re.compile(r'\s*\((до|после|с)\s+' + _DATE + r'\)\s*$', re.I)
_NEW_RULES_RE = re.compile(r'(?:нов\w*\s+правил\w*|действу\w*)\s+с\s+' + _DATE, re.I)
_ANY_RULE_RE = re.compile(r'не\s+старше\s+(\d{4})\s+года\s+выпуска.{0,40}?любой\s+марки', re.I | re.S)
_CATCH_RE = re.compile(r'^(.*?)\s*\((кроме указанных моделей|все модели)\)\s*$', re.I)

# Буквы кириллицы, которыми в таблицах набраны латинские коды: «Jetour Х70».
_LOOKALIKE = str.maketrans('авекмнорстухАВЕКМНОРСТУХ', 'abekmhopctyxABEKMHOPCTYX')


class ClassifierSourceError(Exception):
    """Страница классификатора не того вида — текст для журнала и человека."""


# ── Текст ───────────────────────────────────────────────────────────────────

def clean_cell(value):
    """HTML ячейки → строка в одну линию: без тегов, сущностей и невидимых знаков."""
    text = html.unescape(_TAG_RE.sub(' ', str(value if value is not None else '')))
    text = _INVISIBLE_RE.sub('', text.replace('\xa0', ' '))
    return _SPACE_RE.sub(' ', text).strip()


def clean_note(value):
    """HTML абзаца → текст с переносами строк: пункты списков — «• …»."""
    text = _LI_RE.sub('\n• ', str(value or ''))
    text = _BLOCK_END_RE.sub('\n', text)
    text = html.unescape(_TAG_RE.sub('', text))
    text = _INVISIBLE_RE.sub('', text.replace('\xa0', ' '))
    lines =[_SPACE_RE.sub(' ', line).strip() for line in text.split('\n')]
    out = []
    for line in lines:
        if not line or line == '•':
            continue
        if out and out[-1] == '•':
            out[-1] = '• ' + line
            continue
        out.append(line)
    return '\n'.join(out)


def _fold(value):
    """Текст для сравнения абзацев: без регистра, пробелов и маркеров."""
    return re.sub(r'[\s•*«»"“”.,:;!—–-]+', '', str(value or '').lower())


def _iso(day, month, year):
    try:
        return date(int(year), _MONTHS[month.lower()], int(day)).isoformat()
    except (KeyError, ValueError):
        return None


# ── Имена ───────────────────────────────────────────────────────────────────

def model_key(value):
    """Ключ имени: буквы и цифры, без регистра; кириллица в КОДЕ — латиницей.

    «Lynk & Co 01» и «Lynk&Co 01» — один ключ; «Jetour Х70» с русской «Х» —
    тот же, что «Jetour X70». Русское СЛОВО («Волга», «Ода») остаётся русским:
    подменять в нём буквы значило бы портить имя, а не чинить код.
    """
    words = re.split(r'[^0-9a-zа-яё]+', str(value or '').lower())
    out = []
    for word in words:
        if not word:
            continue
        coded = bool(re.search(r'\d', word)) or len(word) <= 3
        out.append(word.translate(_LOOKALIKE) if coded else word)
    return ''.join(out)


def split_period(name):
    """«Changan Alsvin (после 15 августа 2025)» → ('Changan Alsvin', ('after', '2025-08-15'))."""
    found = _PERIOD_RE.search(name)
    if not found:
        return name, None
    when = _iso(found.group(2), found.group(3), found.group(4))
    if not when:
        return name, None
    kind = 'before' if found.group(1).lower() == 'до' else 'after'
    return name[:found.start()].strip(), (kind, when)


def period_active(period, today):
    if not period:
        return True
    kind, when = period
    day = date.fromisoformat(when)
    return today < day if kind == 'before' else today >= day


def split_brand(name, brands=MULTIWORD_BRANDS):
    """«LADA (ВАЗ) Granta» → ('LADA (ВАЗ)', 'Granta'); «Audi A4» → ('Audi', 'A4').

    Марка из нескольких слов узнаётся с любыми пробелами между знаками и без
    регистра, поэтому «Lynk&Co 08» и «Lynk & Co 01» — одна марка. Конец марки —
    граница слова: «Land Rover Defender», но не «Landwind». Слово одно — марка
    без модели.
    """
    text = str(name or '').strip()
    for brand in sorted(brands, key=len, reverse=True):
        found = _brand_pattern(brand).match(text)
        if found:
            return brand, text[found.end():].strip()
    head, _, tail = text.partition(' ')
    return head, tail.strip()


def _brand_pattern(brand):
    chars = [re.escape(char) for char in brand if not char.isspace()]
    return re.compile(r'\s*'.join(chars) + r'(?=\s|$)', re.I)


def _brand_key(value):
    return re.sub(r'[^0-9a-zа-яё]', '', str(value or '').lower())


# ── Требование из ячейки ────────────────────────────────────────────────────

def parse_requirement(text):
    """Ячейка «Требования» → {'year', 'text', 'except'}.

    year: год «от N»; 0 — «не допускается»; None — ячейка не разобрана (тогда
    показывается её текст, а сама она уходит в предупреждения разбора).
    text: исходный текст, когда в нём есть что-то сверх «от N» — оговорка про
    комплектацию или про зарегистрированные машины; иначе None.
    except: [с года, по год, 'ГГГГ-ММ-ДД'] — машины этих лет, зарегистрированные
    в сервисе не позднее даты, работать могут, хотя по правилу не подходят.
    """
    value = clean_cell(text)
    found = _FROM_RE.match(value)
    if found:
        note = (found.group(2) or '').strip()
        return {'year': int(found.group(1)), 'text': value if note else None,
                'except': _exception(note) if note else None}
    found = _NOT_RE.match(value)
    if found:
        note = (found.group(1) or '').strip()
        return {'year': 0, 'text': value if note else None,
                'except': _exception(note) if note else None}
    found = _YEAR_ONLY_RE.match(value)
    if found:
        return {'year': int(found.group(1)), 'text': None, 'except': None}
    return {'year': None, 'text': value or None, 'except': None}


def _exception(note):
    """«кроме машин 2024-2026 годов, которые были зарегистрированы* в сервисе не
    позднее 30 июня 2026» → [2024, 2026, '2026-06-30']; «а также машины 2023 года,
    которые были зарегистрированы … не позднее 22 января 2026» → [2023, 2023, …]."""
    if 'зарегистр' not in note.lower():
        return None
    years = _EXCEPT_YEARS_RE.search(note)
    until = _EXCEPT_DATE_RE.search(note)
    if not years or not until:
        return None
    first, last = int(years.group(1)), int(years.group(2) or years.group(1))
    when = _iso(until.group(1), until.group(2), until.group(3))
    if not when:
        return None
    return [min(first, last), max(first, last), when]


# ── Страница города ─────────────────────────────────────────────────────────

def _article(state):
    article = yandex_pro._dig(state, ('props', 'initialProps', 'pageProps', 'data', 'article'))
    if not isinstance(article, dict) or not isinstance(article.get('text_components'), list):
        raise ClassifierSourceError('На странице нет статьи с компонентами — '
                                    'так выглядит заглушка или чужой адрес')
    return article


def tariff_of(title):
    """Заголовок аккордеона → (ключ тарифа или None, дата новых правил или None)."""
    plain = clean_cell(title).lower()
    found = _NEW_RULES_RE.search(plain)
    effective = _iso(found.group(1), found.group(2), found.group(3)) if found else None
    for key, pattern in _TARIFF_TITLES:
        if pattern.search(plain):
            return key, effective
    return None, effective


def kazakhstan_cities(state):
    """Города Казахстана из справочника стран в состоянии страницы.

    [{'code', 'name', 'yandex_id', 'region_id'}]; пусто — справочника нет.
    """
    countries = yandex_pro._dig(state, ('props', 'initialState', 'country', 'countries')) or []
    for country in countries:
        if str((country or {}).get('code') or '').lower() != COUNTRY_CODE:
            continue
        out, seen = [], set()
        for city in country.get('cities') or []:
            code = str(city.get('code') or '').strip().lower()
            name = str(city.get('name') or '').strip()
            if not code or not name or code in seen:
                continue
            seen.add(code)
            out.append({'code': code, 'name': name, 'yandex_id': city.get('id'),
                        'region_id': city.get('region_id')})
        return out
    return []


def parse_city_page(state, *, today):
    """Состояние страницы города → тарифы города как они есть на странице.

    {'title', 'updated', 'general': [текст], 'footnote', 'tariffs': {ключ: блок},
     'warnings': [...]}, где блок = {'rows': [(имя, требование)], 'other',
     'brands': {марка как в ячейке: требование}, 'any': год или None,
     'notes': [текст], 'next': {'from', …блок} или None}.
    """
    article = _article(state)
    warnings, general, footnote = [], [], None
    found = {}                      # ключ тарифа -> [(дата вступления, блок)]
    for component in article['text_components']:
        values = component.get('values') or {}
        kind = component.get('type')
        if yandex_pro._is_hidden(values):
            continue
        if kind == 'YTextArea':
            text = clean_note(values.get('text'))
            if not text:
                continue
            if text.startswith('*'):
                footnote = text.lstrip('*').strip()
            else:
                general.append(text)
            continue
        if kind != 'AccordionStart':
            if kind not in ('TariffCarClassifier',):
                warnings.append('Компонент %s пропущен' % kind)
            continue
        title = clean_cell(values.get('title'))
        key, effective = tariff_of(title)
        if key is None:
            warnings.append('Неизвестный тариф «%s» пропущен' % title)
            continue
        block = _read_accordion(component, title, warnings)
        found.setdefault(key, []).append((effective, block))

    tariffs = {}
    for key, blocks in found.items():
        live = [item for item in blocks if not item[0] or item[0] <= today.isoformat()]
        coming = sorted((item for item in blocks if item[0] and item[0] > today.isoformat()),
                        key=lambda item: item[0])
        if not live:
            live, coming = coming[:1], coming[1:]
        # Действует последний вступивший в силу; аккордеон без даты — самый старый.
        live.sort(key=lambda item: item[0] or '')
        current = live[-1][1]
        if len(live) > 1:
            warnings.append('Тариф %s: %d версии правил, взята «%s»' % (
                key, len(live), current['title']))
        if coming:
            current['next'] = dict(coming[0][1], **{'from': coming[0][0]})
        tariffs[key] = current
    return {
        'title': clean_cell(article.get('name')),
        'updated': clean_cell(article.get('last_update')),
        'general': _dedupe_notes(general),
        'footnote': footnote,
        'tariffs': tariffs,
        'warnings': warnings,
    }


def _read_accordion(component, title, warnings):
    block = {'title': title, 'rows': [], 'other': None, 'brands': {}, 'any': None,
             'notes': [], 'next': None}
    for child in component.get('children') or []:
        values = child.get('values') or {}
        kind = child.get('type')
        if yandex_pro._is_hidden(values):
            continue
        if kind == 'Table':
            for raw in values.get('body') or []:
                if not isinstance(raw, list) or len(raw) < 2:
                    warnings.append('%s: строка таблицы без двух колонок пропущена' % title)
                    continue
                name, requirement = clean_cell(raw[0]), parse_requirement(raw[1])
                if not name:
                    continue
                if requirement['year'] is None:
                    warnings.append('%s: «%s» — требование «%s» не разобрано' % (
                        title, name, requirement['text']))
                block['rows'].append((name, requirement))
        elif kind == 'YTextArea':
            text = clean_note(values.get('text'))
            if not text:
                continue
            rule = _ANY_RULE_RE.search(text)
            if rule and block['any'] is None:
                block['any'] = int(rule.group(1))
            block['notes'].append(text)
        else:
            warnings.append('%s: компонент %s пропущен' % (title, kind))
    block['notes'] = _dedupe_notes(block['notes'])
    if block['rows'] and block['any'] is not None:
        # Таблица точнее правила: правило остаётся в условиях текстом.
        block['any'] = None
    if not block['rows'] and block['any'] is None:
        warnings.append('%s: ни таблицы, ни правила «любой марки» — только текст' % title)
    return block


def _dedupe_notes(texts):
    """Абзац, целиком лежащий в соседнем, — один раз (ловушка 1 yandex_pro.py)."""
    folded = [_fold(text) for text in texts]
    out = []
    for index, text in enumerate(texts):
        mine = folded[index]
        if not mine:
            continue
        if any(other != mine and mine in other for other in folded):
            continue
        if mine in [_fold(kept) for kept in out]:
            continue
        out.append(text)
    return out


# ── Сборка справочника ──────────────────────────────────────────────────────

def city_id_of(code):
    return CITY_IDS.get(code, code)


def city_url(code):
    return PAGE_URL % code


def _order_cities(cities):
    rank = {city_id: index for index, city_id in enumerate(CITY_ORDER)}
    return sorted(cities, key=lambda city: (rank.get(city['id'], len(rank)), city['name']))


class _Index:
    """Общий указатель машин: ключ (марка, модель) → номер, с выбором написания."""

    def __init__(self):
        self.spellings = {}          # (ключ марки, ключ модели) -> {(марка, модель): сколько}
        self.brand_spellings = {}    # ключ марки -> {написание: сколько}

    def add(self, brand, model):
        brand_key, key = _brand_key(brand), model_key(model)
        pair = (brand_key, key)
        counts = self.spellings.setdefault(pair, {})
        counts[(brand, model)] = counts.get((brand, model), 0) + 1
        spell = self.brand_spellings.setdefault(brand_key, {})
        spell[brand] = spell.get(brand, 0) + 1
        return pair

    def brand_name(self, brand_key):
        spell = self.brand_spellings.get(brand_key) or {}
        return _most_common(spell) if spell else None

    def build(self):
        """[(марка, модель)] в порядке показа и {пара ключей: номер}."""
        named = []
        for pair, counts in self.spellings.items():
            brand = self.brand_name(pair[0])
            model = _most_common({m: n for (b, m), n in counts.items()})
            named.append((brand, model, pair))
        named.sort(key=lambda item: (item[0].casefold(), _natural(item[1])))
        cars = [[brand, model] for brand, model, _ in named]
        return cars, {pair: index for index, (_, _, pair) in enumerate(named)}


def _most_common(counts):
    """Самое частое написание; при равенстве — первое по алфавиту (стабильность отпечатка)."""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _natural(value):
    """«Tiggo 2» раньше «Tiggo 10»: числа сравниваются числами."""
    return [(0, int(part), '') if part.isdigit() else (1, 0, part)
            for part in re.split(r'(\d+)', str(value).casefold()) if part]


def build(pages, *, today, checked_at=None, cities=None):
    """Справочник из разобранных страниц городов.

    pages: {код города Яндекса: состояние страницы (dict из __NEXT_DATA__)}.
    cities: [{'code', 'name', 'yandex_id'}] — список городов (kazakhstan_cities
    со страницы Алматы); нет — запасной FALLBACK_CITIES.
    Возвращает (справочник, предупреждения). Город без страницы в pages в
    справочник не попадает — решать, брать ли его из прошлого слепка, будет
    вызывающий (classifier_store.merge_missing).
    """
    known = cities or [{'code': code, 'name': name, 'yandex_id': None}
                       for code, name in FALLBACK_CITIES]
    by_code = {city['code']: city for city in known}
    warnings = []
    parsed = {}
    for code, state in pages.items():
        try:
            parsed[code] = parse_city_page(state, today=today)
        except ClassifierSourceError as error:
            warnings.append('%s: %s' % (code, error))
            continue
        for line in parsed[code]['warnings']:
            warnings.append('%s: %s' % (code, line))

    index = _Index()
    brand_names = list(MULTIWORD_BRANDS)

    def car_pair(name):
        brand, model = split_brand(name, brand_names)
        return index.add(brand, model or brand)

    # Строки по городам: имя → требование, периоды и остатки разобраны.
    staged = {}
    for code, page in parsed.items():
        for key, block in page['tariffs'].items():
            staged[(code, key)] = _stage_block(block, today, car_pair, warnings, code)
            if block.get('next'):
                staged[(code, key, 'next')] = _stage_block(block['next'], today, car_pair,
                                                           warnings, code)
    cars, numbers = index.build()

    lists, list_ids, reqs, req_ids, notes, note_ids = [], {}, [], {}, [], {}

    def req_id(requirement):
        if not requirement.get('text') and not requirement.get('except'):
            return None
        item = {'text': requirement.get('text')}
        if requirement.get('except'):
            item['except'] = list(requirement['except'])
        token = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if token not in req_ids:
            req_ids[token] = len(reqs)
            reqs.append(item)
        return req_ids[token]

    def encode(requirement):
        rid = req_id(requirement)
        return [requirement['year']] if rid is None else [requirement['year'], rid]

    def list_id(stage):
        rows = sorted([numbers[pair]] + encode(requirement)
                      for pair, requirement in stage['rows'].items())
        item = {'rows': rows}
        if stage['other'] is not None:
            item['other'] = encode(stage['other'])
        if stage['brands']:
            item['brands'] = {}
            for brand_key, (word, requirement) in sorted(stage['brands'].items()):
                own = _brand_rule_key(brand_key, index.brand_spellings)
                item['brands'][index.brand_name(own) or word] = encode(requirement)
        token = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if token not in list_ids:
            list_ids[token] = len(lists)
            lists.append(item)
        return list_ids[token]

    def note_list(texts):
        out = []
        for text in texts:
            if text not in note_ids:
                note_ids[text] = len(notes)
                notes.append(text)
            out.append(note_ids[text])
        return out

    def tariff_entry(block, stage):
        entry = {}
        if stage['rows'] or stage['other'] is not None or stage['brands']:
            entry['list'] = list_id(stage)
        elif block['any'] is not None:
            entry['any'] = block['any']
        else:
            return None
        if block['notes']:
            entry['notes'] = note_list(block['notes'])
        return entry

    city_rows = []
    order = [key for key, _ in TARIFFS]
    for code in sorted(parsed, key=lambda item: item):
        page = parsed[code]
        entry_tariffs = {}
        for key in order:
            block = page['tariffs'].get(key)
            if not block:
                continue
            entry = tariff_entry(block, staged[(code, key)])
            if entry is None:
                continue
            nxt = block.get('next')
            if nxt:
                upcoming = tariff_entry(nxt, staged[(code, key, 'next')])
                if upcoming is not None:
                    entry['next'] = dict(upcoming, **{'from': nxt['from']})
            entry_tariffs[key] = entry
        meta = by_code.get(code) or {'code': code, 'name': code, 'yandex_id': None}
        city_rows.append({'id': city_id_of(code), 'name': meta['name'], 'code': code,
                          'yandex_id': meta.get('yandex_id'), 'url': city_url(code),
                          'updated': page['updated'], 'tariffs': entry_tariffs})

    almaty = parsed.get('almaty') or next(iter(parsed.values()), None) or {}
    data = {
        'version': VERSION,
        'source': {
            'url': SOURCE_URL,
            'title': almaty.get('title') or '',
            'updated': almaty.get('updated') or '',
            'checked_at': checked_at,
            'general': almaty.get('general') or [],
            'footnote': almaty.get('footnote'),
        },
        'tariffs': [{'key': key, 'name': name} for key, name in TARIFFS],
        'cities': _order_cities(city_rows),
        'lists': lists,
        'reqs': reqs,
        'notes': notes,
        'cars': cars,
    }
    return data, warnings


def _stage_block(block, today, car_pair, warnings, code):
    """Строки таблицы → {пара ключей: требование}, «Другие модели», правила марок.

    Строка с периодом, который сейчас не идёт, отбрасывается. Одна машина
    дважды (после свёртки написания): одинаковые требования — одна строка,
    разные — остаётся первая, а расхождение уходит в предупреждения.
    """
    stage = {'rows': {}, 'other': None, 'brands': {}}
    for name, requirement in block['rows']:
        base, period = split_period(name)
        if not period_active(period, today):
            continue
        if base.strip().lower() == OTHER_MODELS.lower():
            stage['other'] = requirement
            continue
        catch = _CATCH_RE.match(base)
        if catch:
            # Марка остатка пишется короче, чем у моделей: «LADA (кроме указанных
            # моделей)» при «LADA (ВАЗ) Granta». Своя марка находится при сборке
            # (_brand_rule_key), здесь — как написано.
            brand_word = catch.group(1).strip()
            stage['brands'][_brand_key(brand_word)] = (brand_word, requirement)
            continue
        pair = car_pair(base)
        if pair in stage['rows']:
            if stage['rows'][pair] != requirement:
                warnings.append('%s: %s — «%s» дважды с разными требованиями, взята первая' % (
                    code, block['title'], base))
            continue
        stage['rows'][pair] = requirement
    return stage


def _brand_rule_key(brand_key, known):
    """Ключ марки правила-остатка → ключ марки машин указателя.

    «lada» → «ladaваз»: марка остатка совпадает с началом ровно одной марки.
    Совпадений нет или несколько — остаётся как есть: правило всё равно
    сработает на вопрос помощника по имени марки.
    """
    if brand_key in known:
        return brand_key
    near = [key for key in known if key.startswith(brand_key)]
    return near[0] if len(near) == 1 else brand_key


def fingerprint(data):
    """Отпечаток содержимого: без времени проверки — иначе он менялся бы каждую ночь."""
    body = dict(data)
    body['source'] = {key: value for key, value in (data.get('source') or {}).items()
                      if key != 'checked_at'}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def extract_state(page):
    """HTML страницы (или уже JSON) → состояние Next.js. Ошибка — ClassifierSourceError."""
    try:
        return yandex_pro.extract_next_data(page)
    except yandex_pro.SourceError as error:
        raise ClassifierSourceError(str(error))
