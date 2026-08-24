# -*- coding: utf-8 -*-
"""Перенос статьи «Адреса офисов» в справочник офисов.

Работает ЧЕРЕЗ API, как scripts/migrate_wiki.py: офис получает журнал, а не
только строку в таблице.

Источник — та же статья, что читают операторы: девятнадцать таблиц, по одной на
таксопарк, где один и тот же адрес переписан до шести раз. Скрипт делает
обратное преобразование — сводит строки в физические офисы и вешает на них
парки, а расхождения телефона и графика между таблицами превращает в
переопределения связи.

Совпадение адресов ищется по ссылке 2ГИС, а при её отсутствии — по набору слов
адреса: в статье один и тот же офис записан как «улица по малой Нуркен
Абдырова 4» и «улица малой Нуркен Абдырова 4», и посимвольное сравнение
завело бы два офиса вместо одного.

По умолчанию — ХОЛОСТОЙ ПРОГОН. Запись включается флагом --apply.

    python scripts/migrate_wiki_offices.py            # план
    python scripts/migrate_wiki_offices.py --apply    # перенос
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ARTICLE_SLUG = 'адреса-офисов'

DAY_CODES = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
_DAY_BY_RU = {'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6}

# Заголовки партнёрских таблиц: это не наши парки, их офисы ни к какому парку
# не привязываются.
PARTNER_HEADINGS = {
    'офисы яндекса для водителей': 'Яндекс для водителей',
    'офисы для подключения тарифа «бизнес»': 'Тариф Бизнес',
    'офисы для подключения тарифа «wolt»': 'Тариф Wolt',
}

# Слова, не участвующие в сравнении адресов: они есть везде и только сближают
# заведомо разные адреса.
ADDRESS_STOPWORDS = {
    'улица', 'ул', 'проспект', 'пр', 'дом', 'д', 'офис', 'кабинет', 'каб',
    'этаж', 'микрорайон', 'мкр', 'мкрн', 'угол', 'по', 'малой', 'бц', 'жк',
    'вход', 'со', 'стороны', 'напротив', 'рядом', 'между', 'улиц', 'улицы',
}


def log(*args):
    print(*args, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self, base_url, login, password, dry_run=True):
        self.base = base_url.rstrip('/')
        self.login_value = login
        self.password = password
        self.dry_run = dry_run
        self.token = None
        self._fake_id = 0

    def authenticate(self):
        payload = json.dumps({'login': self.login_value,
                              'password': self.password}).encode('utf-8')
        request = urllib.request.Request(
            self.base + '/api/login', data=payload,
            headers={'Content-Type': 'application/json',
                     'X-Auth-Transport': 'bearer'})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode())
        self.token = data.get('access_token')
        if not self.token:
            raise SystemExit('Не удалось получить токен: в ответе нет access_token')
        return data.get('user') or data

    def call(self, method, path, payload=None, label=''):
        if self.dry_run and method != 'GET':
            log('   [план] %s %s %s' % (method, path, label))
            self._fake_id += 1
            return {'id': -self._fake_id}

        data = (json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
                if payload is not None else None)
        request = urllib.request.Request(
            self.base + urllib.parse.quote(path, safe='/?=&'), data=data, method=method,
            headers={'Authorization': 'Bearer ' + (self.token or ''),
                     'X-Auth-Transport': 'bearer',
                     # Origin обязателен: без него enforce_api_origin_protection
                     # заворачивает любой пишущий запрос с 403.
                     'Origin': self.base,
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode('utf-8', 'replace')
                return json.loads(raw) if raw.strip().startswith(('{', '[')) else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', 'replace')[:300]
            raise RuntimeError('%s %s -> %s %s' % (method, path, error.code, detail))


# ─────────────────────────────────────────────────────────────────────────────
# Разбор статьи
# ─────────────────────────────────────────────────────────────────────────────

class ArticleTables(HTMLParser):
    """Таблицы статьи вместе с заголовком, под которым они стоят."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._heading = ''
        self._heading_tag = None
        self._in_heading = False
        self._buffer = []
        self._row = None
        self._rows = None

    def handle_starttag(self, tag, attrs):
        # Заголовок закрывается тем же тегом, которым открылся: в статье
        # «Офисы для подключения тарифа «Бизнес»» кавычки лежат в отдельных
        # <strong>, и наивный разбор оставлял от заголовка одну кавычку.
        if tag in ('h1', 'h2', 'h3', 'h4') and self._rows is None:
            self._heading_tag, self._in_heading, self._buffer = tag, True, []
        elif tag == 'strong' and self._rows is None and not self._in_heading:
            # Часть заголовков в статье оформлена жирным абзацем, а не h2.
            self._heading_tag, self._in_heading, self._buffer = tag, True, []
        elif tag == 'table':
            self._rows = []
        elif tag == 'tr' and self._rows is not None:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._buffer = []
        elif tag == 'br':
            self._buffer.append('\n')
        elif tag == 'a':
            # Ссылка 2ГИС иногда стоит подписью, а не голым адресом.
            href = dict(attrs).get('href')
            if href:
                self._buffer.append(' ' + href + ' ')

    def handle_endtag(self, tag):
        if self._in_heading and tag == self._heading_tag:
            text = self._text()
            # Обрывки вроде «»» заголовком не считаем — иначе таблица уедет
            # под чужое название и офис привяжется не к тому парку.
            if len(text) >= 6:
                self._heading = text
            self._in_heading = False
            self._heading_tag = None
        elif tag in ('td', 'th') and self._row is not None:
            self._row.append(self._text())
        elif tag == 'tr' and self._row is not None:
            if any(cell for cell in self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == 'table' and self._rows is not None:
            if self._rows:
                self.tables.append({'heading': self._heading, 'rows': self._rows})
            self._rows = None

    def handle_data(self, data):
        self._buffer.append(data)

    def _text(self):
        # Нулевой ширины пробелы в статье есть (например «БЦ Prime<U+200B>») —
        # невидимы в редакторе, но ломают и сравнение адресов, и вид названия.
        text = html.unescape(''.join(self._buffer)).replace('​', '').replace('﻿', '')
        lines = [re.sub(r'[ \t\xa0]+', ' ', line).strip(' -•\xa0')
                 for line in text.split('\n')]
        self._buffer = []
        return '\n'.join(line for line in lines if line).strip()


def parse_tables(content):
    parser = ArticleTables()
    parser.feed(content)
    return parser.tables


def columns_of(header_row):
    """Заголовок таблицы → {роль колонки: индекс}.

    Колонки в таблицах разные: у парков пять (город, телефон, адрес, график,
    обед), у Яндекса три. Читаем по названиям, а не по номерам.
    """
    mapping = {}
    for index, cell in enumerate(header_row):
        name = cell.lower()
        if 'город' in name:
            mapping['city'] = index
        elif 'адрес' in name:
            mapping['address'] = index
        elif 'телефон' in name or 'номер офиса' in name:
            mapping['phone'] = index
        elif 'график' in name or 'время работы' in name:
            mapping['schedule'] = index
        elif 'обед' in name:
            mapping['lunch'] = index
    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# Разбор ячеек
# ─────────────────────────────────────────────────────────────────────────────

_URL = re.compile(r'https?://(?:go\.)?2gis\.[a-z]+/\S+')
_TIME_RANGE = re.compile(r'(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})')


def _hhmm(hours, minutes):
    return '%02d:%s' % (int(hours), minutes)


def parse_days(text):
    """«Пн-Пт», «Сб», «Пн-Вс» → список кодов дней."""
    found = re.findall(r'(пн|вт|ср|чт|пт|сб|вс)', text.lower())
    if not found:
        return []
    if len(found) >= 2 and re.search(r'(пн|вт|ср|чт|пт|сб|вс)\s*[-–—]\s*(пн|вт|ср|чт|пт|сб|вс)',
                                     text.lower()):
        start, end = _DAY_BY_RU[found[0]], _DAY_BY_RU[found[1]]
        span = range(start, end + 1) if start <= end else list(range(start, 7)) + list(range(0, end + 1))
        return [DAY_CODES[i] for i in span]
    return [DAY_CODES[_DAY_BY_RU[day]] for day in found]


def parse_schedule(schedule_text, lunch_text):
    """Текст графика и обеда → структура {день: {...} | None}.

    Возвращает None для «ОНЛАЙН» и всего, где часов не нашлось.
    """
    if not schedule_text or 'онлайн' in schedule_text.lower():
        return None

    lunch = _TIME_RANGE.search(lunch_text or '')
    lunch_pair = ((_hhmm(lunch.group(1), lunch.group(2)), _hhmm(lunch.group(3), lunch.group(4)))
                  if lunch else None)

    # «Пн-Пт (09:00-19:00)Сб-Вс (выходной)» — в статье эти два правила склеены
    # без переноса. Без разделения строка читается как «понедельник–пятница,
    # но выходной», то есть график пропадает целиком.
    schedule_text = re.sub(
        r'(?i)(?<=[)\dа-яё])\s*(?=(?:пн|вт|ср|чт|пт|сб|вс)\s*[-–—(,]?\s*(?:\d|вых|пн|вт|ср|чт|пт|сб|вс))',
        '\n', schedule_text)

    schedule = {code: None for code in DAY_CODES}
    filled = False
    for line in schedule_text.split('\n'):
        days = parse_days(line)
        if not days:
            continue
        if 'выходн' in line.lower():
            continue  # дни и так None
        times = _TIME_RANGE.search(line)
        if not times:
            continue
        day_value = {'from': _hhmm(times.group(1), times.group(2)),
                     'to': _hhmm(times.group(3), times.group(4))}
        # Обед в статье один на всю строку, а суббота бывает короткой: в
        # Костанае она 10:00–13:00, и перерыв «13:00–14:00» пришёлся бы ровно
        # на закрытие. Ставим его только внутрь рабочих часов.
        if lunch_pair and day_value['from'] < lunch_pair[0] < lunch_pair[1] <= day_value['to']:
            day_value['break_from'], day_value['break_to'] = lunch_pair
        for code in days:
            schedule[code] = dict(day_value)
        filled = True
    return schedule if filled else None


def parse_address(cell):
    """Ячейка адреса → (адрес, ориентиры, ссылка 2ГИС)."""
    if not cell or 'онлайн' in cell.lower():
        return None, None, None

    link = None
    lines = []
    for line in cell.split('\n'):
        found = _URL.search(line)
        if found:
            link = found.group(0).rstrip('.,;)')
            line = _URL.sub('', line).strip(' -–—')
            line = re.sub(r'^2\s*гис\s*:?\s*$', '', line, flags=re.I).strip()
            line = re.sub(r'^гис\s*:?\s*$', '', line, flags=re.I).strip()
        if line:
            lines.append(line)
    if not lines:
        return None, None, link
    return lines[0], ('\n'.join(lines[1:]) or None), link


def address_tokens(address):
    # Свёртка казахских букв — общая для раздела (wiki/text.py): в статье один
    # и тот же проспект записан и «Азаттык», и «Азаттық».
    from wiki.text import fold_kazakh
    words = re.findall(r'[а-яa-z0-9]+', fold_kazakh((address or '').lower()))
    # Однобуквенные слова выбрасываем, однозначные числа — нет: «2 этаж, офис 5»
    # это половина совпадения между двумя записями одного адреса.
    return {word for word in words
            if word not in ADDRESS_STOPWORDS and (len(word) > 1 or word.isdigit())}


def same_address(left, right):
    """Совпадает ли строка адреса.

    Порог 0.6 подобран по боевым парам: «улица по малой Нуркен Абдырова 4» и
    «улица малой Нуркен Абдырова 4» дают 1.0, а «Жамбыла 172» и «Байзакова 78А»
    в одном городе — 0.0.
    """
    if not left or not right:
        return False
    overlap = len(left & right)
    return bool(overlap) and overlap / max(len(left), len(right)) >= 0.6


def same_place(left, right):
    """Запасное сравнение — по адресу вместе с ориентирами.

    Нужно там, где в одной таблице всё уместили в строку адреса, а в другой
    разнесли: Кызылорда записана как «улица Журба 96» + ориентиры и как
    «Ивана Журбы, 96, 2 этаж, офис 5. между улицами Алисова и Ескараева».
    Сравниваем по меньшему набору — короткая запись должна укладываться в
    подробную, а не наоборот.
    """
    if not left or not right:
        return False
    overlap = len(left & right)
    return overlap >= 3 and overlap / min(len(left), len(right)) >= 0.7


def normalize_phone(phone):
    digits = re.sub(r'\D', '', phone or '')
    if not digits:
        return None
    digits = digits.lstrip('8') if len(digits) == 11 and digits.startswith('8') else digits
    if len(digits) == 10:
        digits = '7' + digits
    if len(digits) == 11 and digits.startswith('7'):
        return '+7 %s %s %s %s' % (digits[1:4], digits[4:7], digits[7:9], digits[9:11])
    return phone.strip()


def city_of(cell):
    """Ячейка «Город» → (город, название офиса).

    «Алматы 2» и «Алматы Навигатор» — разные офисы одного города, и различие
    живёт в названии, а не в поле города: иначе фильтр по городу их растеряет.
    """
    text = (cell or '').strip()
    if not text or 'все города' in text.lower():
        return None, None
    base = re.split(r'\s+', text)[0].strip(',')
    return base, text


# ─────────────────────────────────────────────────────────────────────────────
# Сборка офисов
# ─────────────────────────────────────────────────────────────────────────────

def collect(tables):
    """Строки таблиц → офисы с привязками к паркам."""
    offices = []

    for table in tables:
        rows = table['rows']
        if len(rows) < 2:
            continue
        columns = columns_of(rows[0])
        if 'city' not in columns and 'address' not in columns:
            continue

        heading = re.sub(r'\s+', ' ', table['heading'] or '').strip()
        # Таблицы парков начинаются с «Адреса офисов …», партнёрские — с
        # «Офисы …». Различать по вхождению слова нельзя: в «Адреса Офисов
        # Бизнес Партнер» есть «бизнес», и парк уехал бы в партнёры.
        partner = None
        if not heading.lower().startswith('адреса'):
            for key, label in PARTNER_HEADINGS.items():
                if key.split()[-1].strip('«»') in heading.lower():
                    partner = label
                    break

        park = None
        if partner is None:
            if not heading.lower().startswith('адреса'):
                # Незнакомая таблица: лучше пропустить, чем завести парк с
                # названием случайного заголовка.
                log('   ! таблица под заголовком %r пропущена' % heading[:60])
                continue
            park = re.sub(r'^адреса\s+офисов\s*[-–—]?\s*', '', heading, flags=re.I).strip(' -–—')
            if not park or ('таксопарк' in park.lower() and len(park) < 14):
                continue  # «Адреса офисов таксопарков» — заголовок раздела, не парк

        for row in rows[1:]:
            def cell(role):
                index = columns.get(role)
                return row[index] if index is not None and index < len(row) else ''

            city, name = city_of(cell('city'))
            address, note, link = parse_address(cell('address'))
            phone = normalize_phone(cell('phone'))
            schedule = parse_schedule(cell('schedule'), cell('lunch'))
            is_online = not address

            if not any((address, phone)):
                continue

            tokens = address_tokens(address)
            full_tokens = tokens | address_tokens(note)
            match = None
            for office in offices:
                if office['is_online'] != is_online or office['partner'] != partner:
                    continue
                if link and office['map_url'] and link == office['map_url']:
                    match = office
                    break
                if office['city'] != city:
                    continue
                if is_online:
                    # У онлайн-записи нет ни адреса, ни ссылки — опознаём по
                    # городу. Телефон для этого не годится: у Петропавловска он
                    # разный в таблицах iTaxi и «Департамента», а офиса всё
                    # равно нет ни там, ни там.
                    if city:
                        match = office
                        break
                elif (same_address(tokens, office['tokens'])
                      or same_place(full_tokens, office['full_tokens'])):
                    match = office
                    break

            if match is None:
                match = {
                    'city': city, 'name': name or (park or partner or 'Офис'),
                    'address': address, 'note': note, 'map_url': link,
                    'tokens': tokens, 'full_tokens': full_tokens,
                    'is_online': is_online, 'partner': partner,
                    'phone_hint': phone, 'links': [],
                }
                offices.append(match)
            else:
                # Побеждает более подробная запись: в разных таблицах один офис
                # описан с разной детальностью, и терять ориентиры не за что.
                if link and not match['map_url']:
                    match['map_url'] = link
                if note and (not match['note'] or len(note) > len(match['note'])):
                    match['note'] = note
                if address and len(address) > len(match['address'] or ''):
                    match['address'] = address
                    match['tokens'] = match['tokens'] | tokens
                match['full_tokens'] = match['full_tokens'] | full_tokens

            if park:
                match['links'].append({'park': park, 'phone': phone, 'schedule': schedule})
            elif schedule and not match.get('schedule'):
                match['schedule'] = schedule
            if partner and phone:
                match['phone_hint'] = phone

    return [finish(office) for office in offices]


def _most_common(values):
    values = [value for value in values if value]
    if not values:
        return None
    counts = {}
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        counts.setdefault(key, [0, value])
        counts[key][0] += 1
    return max(counts.values(), key=lambda item: item[0])[1]


def finish(office):
    """Общий телефон и график — самые частые; остальное уходит в связи."""
    links = office['links']
    base_phone = _most_common([link['phone'] for link in links]) or office['phone_hint']
    base_schedule = office.get('schedule') or _most_common([link['schedule'] for link in links])

    parks = []
    for link in links:
        parks.append({
            'park': link['park'],
            'phone': link['phone'] if link['phone'] and link['phone'] != base_phone else None,
            'schedule': (link['schedule']
                         if link['schedule'] and link['schedule'] != base_schedule else None),
        })

    # Короткая часть адреса для названия: до первого разделителя, а не первые
    # 48 символов — иначе «11-й микрорайон; дом 3д; 1-й этаж нап».
    short = re.split(r'[,;(]', office['address'] or '')[0].strip()[:44].strip()
    name = office['name'] or ''
    if office['is_online']:
        title = '%s — только по телефону' % (name or (parks[0]['park'] if parks else 'Офис'))
    elif name and short and name.lower() not in short.lower():
        title = '%s — %s' % (name, short)
    else:
        title = name or short or 'Офис'

    return {
        'name': title[:255],
        'city': office['city'],
        'address': office['address'],
        'address_note': office['note'],
        'phone': base_phone,
        'map_url': office['map_url'],
        'schedule': base_schedule,
        'is_online': office['is_online'],
        'kind': 'partner' if office['partner'] else 'park',
        'partner_label': office['partner'],
        'parks': parks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Перенос
# ─────────────────────────────────────────────────────────────────────────────

def load_env(path):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _with_space(payload, space_id):
    """Дописывает пространство в тело запроса. None — сервер решит сам."""
    if space_id is None:
        return payload
    return dict(payload, space_id=space_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='записывать, а не только показывать план')
    parser.add_argument('--api', default=None, help='адрес API (по умолчанию из окружения)')
    # Справочник принадлежит пространству вики. Без параметра сервер возьмёт
    # единственное доступное учётке (так и было при первом переносе), но у
    # учётки с двумя пространствами это уже неоднозначно — и он честно ответит
    # 400 вместо того, чтобы залить 39 офисов не в ту вику.
    parser.add_argument('--space', type=int, default=None,
                        help='id пространства вики, куда переносить')
    args = parser.parse_args()

    env = load_env(os.path.join(ROOT, '.env.codex.local'))
    api_base = (args.api or os.getenv('OTP_API_BASE_URL')
                or env.get('OTP_API_BASE_URL') or 'https://otp-2-fos4.onrender.com')
    login = os.getenv('ADMIN_LOGIN') or env.get('ADMIN_LOGIN')
    password = os.getenv('ADMIN_PASSWORD') or env.get('ADMIN_PASSWORD')
    if not (login and password):
        raise SystemExit('Нужны ADMIN_LOGIN и ADMIN_PASSWORD (в окружении или .env.codex.local)')

    api = Api(api_base, login, password, dry_run=not args.apply)
    user = api.authenticate()
    log('API: %s, вошли как %s' % (api_base, user.get('login') or user.get('name') or '?'))

    article = api.call('GET', '/api/wiki/articles/' + ARTICLE_SLUG)
    content = article.get('content') or ''
    if not content:
        raise SystemExit('Статья «%s» пуста или недоступна' % ARTICLE_SLUG)

    tables = parse_tables(content)
    offices = collect(tables)
    log('Таблиц в статье: %d, офисов после сведения: %d' % (len(tables), len(offices)))

    # ── Парки ────────────────────────────────────────────────────────────
    existing_parks = {p['name'].strip().lower(): p['id']
                      for p in (api.call('GET', '/api/wiki/parks').get('items') or [])}
    needed = []
    for office in offices:
        for link in office['parks']:
            if link['park'].strip().lower() not in existing_parks and link['park'] not in needed:
                needed.append(link['park'])

    log('\nТаксопарки: есть %d, нужно создать %d' % (len(existing_parks), len(needed)))
    for name in needed:
        created = api.call('POST', '/api/wiki/parks',
                           _with_space({'name': name}, args.space), label=name)
        existing_parks[name.strip().lower()] = created.get('id')
        log('   + парк %s' % name)

    # ── Офисы ────────────────────────────────────────────────────────────
    offices_path = ('/api/wiki/offices?space_id=%d' % args.space
                    if args.space else '/api/wiki/offices')
    existing_offices = {o['name'].strip().lower(): o
                        for o in (api.call('GET', offices_path).get('items') or [])}

    log('\nОфисы:')
    created_count = skipped_count = 0
    for office in offices:
        if office['name'].strip().lower() in existing_offices:
            log('   = %s (уже есть, пропускаем)' % office['name'])
            skipped_count += 1
            continue

        payload = {
            'name': office['name'],
            'city': office['city'],
            'address': office['address'],
            'address_note': office['address_note'],
            'phone': office['phone'],
            'schedule': office['schedule'],
            'is_online': office['is_online'],
            'kind': office['kind'],
            'partner_label': office['partner_label'],
            'parks': [{'park_id': existing_parks.get(link['park'].strip().lower()),
                       'phone': link['phone'], 'schedule': link['schedule']}
                      for link in office['parks']
                      if existing_parks.get(link['park'].strip().lower())],
        }

        if office['map_url']:
            payload['map_url'] = office['map_url']
            # Разворачиваем ссылку тем же эндпоинтом, что и форма: другого
            # источника координат быть не должно.
            try:
                point = api.call('POST', '/api/wiki/offices/resolve-map',
                                 {'url': office['map_url']}, label=office['map_url'])
                if point.get('lat') is not None:
                    payload.update({'lat': point['lat'], 'lon': point['lon'],
                                    'map_resolved_url': point.get('resolved_url')})
            except RuntimeError as error:
                log('     ! ссылка не развернулась: %s' % error)

        api.call('POST', '/api/wiki/offices', _with_space(payload, args.space),
                 label=office['name'])
        created_count += 1
        parks_note = ('парков: %d' % len(payload['parks'])) if payload['parks'] else 'без парков'
        overrides = sum(1 for link in office['parks'] if link['phone'] or link['schedule'])
        log('   + %-52s %s%s%s' % (
            office['name'][:52], parks_note,
            (', своих телефонов: %d' % overrides) if overrides else '',
            ' , карта' if office['map_url'] else ''))

    log('\nИтого: создано %d, пропущено %d%s'
        % (created_count, skipped_count, '' if args.apply else ' (холостой прогон)'))
    if not args.apply:
        log('Записать: python scripts/migrate_wiki_offices.py --apply')


if __name__ == '__main__':
    main()
