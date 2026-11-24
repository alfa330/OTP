"""Лист «Условия выдачи коробов» Google-таблицы → строки раздела. Чистая логика: ни базы, ни сети.

Общий разбор для двух путей:

    * зеркало (thermoboxes/sync.py) — раздел сам читает лист каждые несколько
      минут и приводит таблицу к нему: с 24.11.2026 источник данных — таблица,
      правка в iCore выключена («сделать так, чтобы таблица читалась и
      обновлялась»);
    * разовый перенос (scripts/import_thermoboxes.py) — для стенда.

Лист — это таблица строк-ячеек (CSV-выгрузка листа). В нём две части рядом:
слева строки офисов («Город, Адрес, Бесплатные термокороба … Депозит»), справа
«Памятка для сотрудников» — значок, текст пункта и «Ответственные».

Строка листа сопоставляется с офисом справочника вики: город — точно, адрес —
по словам и номеру дома со свёрткой казахских букв («Азаттык» = «Азаттық»).
"""

import csv
import io
import re

from wiki.text import fold_kazakh

from . import rules

HEADERS = {
    'город': 'city',
    'адрес': 'address',
    'бесплатные термокороба': 'free_boxes',
    'термопакет': 'thermo_bags',
    'б/у короб': 'used_boxes',
    'тариф': 'tariff',
    'заказы': 'min_orders',
    'срок': 'period_days',
    'отдельное условие': 'special_condition',
    'депозит': 'deposit_tenge',
}

MEMO_OWNERS = {'кц': 'kc', 'регионы': 'regions'}

# Значок пункта памятки — эмодзи в колонке слева от текста (rules.MEMO_KIND_LABELS).
# «⚠» без вариационного селектора: в ячейке он бывает и «⚠️», и «⚠».
MEMO_EMOJI_KINDS = (
    ('⛔', 'forbidden'), ('🚫', 'forbidden'), ('💰', 'money'), ('📊', 'data'),
    ('⚠', 'warning'), ('✅', 'check'), ('❓', 'question'), ('❔', 'question'),
)

# Слова адреса, которые ничего не различают.
_NOISE = {
    'ул', 'улица', 'пр', 'пр-т', 'проспект', 'мкр', 'микрорайон', 'д', 'дом', 'офис', 'оф',
    'бц', 'угол', 'улицы', 'по', 'малой', 'этаж', 'кабинет', 'и',
}


class SheetError(ValueError):
    """Лист не разобран целиком: нет колонок, нет строк. Таблицу не трогаем."""


def fold(text):
    return fold_kazakh(' '.join(str(text or '').lower().replace('ё', 'е').split()))


def sheet_key(city, address):
    """Ключ строки листа: город и адрес так, как их пишут в листе.

    По нему зеркало узнаёт строку и в следующий раз, даже если адрес в
    справочнике вики поправят: сопоставление по справочнику делается один раз.
    """
    return fold('%s|%s' % (city, address))


def memo_kind(mark):
    return next((kind for sign, kind in MEMO_EMOJI_KINDS if sign in str(mark or '')), None)


def _address_words(text):
    """Слова адреса без мусора: «ул. Нуркена Абдирова, 4» → {нуркена, абдирова, 4}.

    Номер офиса («офис 6») отбрасывается вместе со словом «офис»: в справочнике
    его чаще всего нет, и он перевешивал бы номер дома.
    """
    text = fold(text)
    text = re.sub(r'(?:офис|оф\.?)\s*\d+', ' ', text)
    text = re.sub(r'«[^»]*»|"[^"]*"', ' ', text)
    words = re.findall(r'[0-9]+[а-яa-z]?|[а-яa-z]{2,}', text)
    return {word for word in words if word not in _NOISE}


def _house(words):
    return {word for word in words if word[0].isdigit()}


def _house_digits(words):
    return {re.match(r'\d+', word).group(0) for word in _house(words)}


def _streets(words):
    return {word for word in words if not word[0].isdigit()}


def match_office(city, address, offices):
    """Офис справочника для строки листа: (офис, None, пометка) или (None, причина, None).

    Совпасть обязан номер дома: улица без дома — это другой офис той же улицы.
    Сначала дом сравнивается целиком, с литерой; не нашлось — по цифрам, но
    тогда должна совпасть и улица («Жамбыла, 172» в листе — «Жамбыла, 172В»
    в справочнике). Такое совпадение помечается, чтобы человек его увидел.
    """
    if not fold(city):
        return None, 'не указан город', None
    candidates = [office for office in offices if fold(office['city']) == fold(city)]
    if not candidates:
        return None, 'в справочнике нет офиса в городе %s' % city, None
    wanted = _address_words(address)

    def pick(scored, note):
        scored.sort(key=lambda item: -item[0])
        best = [office for score, office in scored if score == scored[0][0]]
        if len(best) > 1:
            return None, 'подходят несколько офисов: %s' % ', '.join(
                '%s (id %s)' % (office.get('address'), office['id']) for office in best), None
        return best[0], None, note

    exact = []
    loose = []
    for office in candidates:
        words = _address_words(office.get('address'))
        if _house(wanted) & _house(words):
            exact.append((len(wanted & words), office))
        elif (_house_digits(wanted) & _house_digits(words)) and (_streets(wanted) & _streets(words)):
            loose.append((len(wanted & words), office))
    if exact:
        return pick(exact, None)
    if loose:
        return pick(loose, 'дом совпал без литеры')
    if len(candidates) == 1:
        return None, 'единственный офис города не совпал по адресу: %s' % candidates[0].get('address'), None
    return None, 'адрес не совпал ни с одним из %d офисов города' % len(candidates), None


def _number(text):
    digits = re.sub(r'\D', '', str(text or ''))
    return int(digits) if digits else None


def parse_value(field, text):
    """Ячейка листа → значение поля раздела (до проверки rules).

    Остаток в ячейке бывает и «11», и «11.0» (число, сохранённое таблицей с
    дробью) — это одно и то же целое; «11.5» — уже не целое, и строка не
    переносится, чтобы человек её увидел.
    """
    text = ' '.join(str(text or '').split())
    if field in ('free_boxes', 'thermo_bags', 'used_boxes'):
        whole = re.fullmatch(r'(\d+)(?:[.,]0+)?', text)
        if not whole:
            raise rules.FieldError(field, '%s: в ячейке не целое число («%s»)' % (rules.FIELD_LABELS[field], text))
        return int(whole.group(1))
    if field == 'tariff':
        for code, label in rules.TARIFF_LABELS.items():
            if fold(label) == fold(text):
                return code
        raise rules.FieldError(field, 'Тариф «%s» не из списка раздела' % text)
    if field == 'min_orders':
        return _number(text) or 0
    if field == 'period_days':
        value = _number(text)
        if value is None:
            raise rules.FieldError(field, 'Срок «%s» без числа дней' % text)
        return value
    if field == 'deposit_tenge':
        return 0 if 'нет' in fold(text) else (_number(text) or 0)
    if field == 'special_condition':
        return text or None
    raise rules.FieldError(field, 'Неизвестное поле')


def memo_text(text):
    """Текст пункта памятки из ячейки листа.

    Переносы строк в ячейке ставились под ширину Google-таблицы: «…проверьте
    соответствие условиям⏎и отправьте данные…» — это одна фраза, и на узкой
    карточке раздела перенос рвал бы её посередине. Строка, начатая со
    строчной буквы, — продолжение предыдущей; с заглавной — новое предложение,
    и перенос перед ним остаётся.
    """
    result = []
    for line in (' '.join(part.split()) for part in str(text or '').splitlines()):
        if not line:
            continue
        if result and line[0].islower():
            result[-1] = '%s %s' % (result[-1], line)
        else:
            result.append(line)
    return '\n'.join(result)


def read_table(table):
    """(строки офисов, памятка) из ячеек листа. Нет нужных колонок — SheetError."""
    if not table:
        raise SheetError('Лист пустой')
    header = [fold(cell) for cell in table[0]]
    columns = {}
    for index, name in enumerate(header):
        if name in HEADERS and HEADERS[name] not in columns:
            columns[HEADERS[name]] = index
    missing = [label for label, field in HEADERS.items() if field not in columns]
    if missing:
        raise SheetError('В листе нет колонок: %s' % ', '.join(missing))

    width = max(len(cells) for cells in table)
    rows = []
    above = None        # (номер, город) строки офиса строкой выше — для объединённой ячейки
    for number, cells in enumerate(table[1:], start=2):
        cells = list(cells) + [''] * (width - len(cells))
        city = cells[columns['city']].strip()
        address = ' '.join(cells[columns['address']].split())
        if not city and not address:
            continue        # пустая строка или строка одной памятки справа
        if not city and above and above[0] == number - 1:
            # Объединённая ячейка «Город» (две строки «Алматы» под одним
            # названием): выгрузка CSV оставляет город только в верхней строке.
            # Строка сразу под строкой офиса — тот же город. Оторванная строка
            # без города остаётся без него: её покажут в «не перенесено», а не
            # припишут чужому городу.
            city = above[1]
        rows.append({
            'line': number,
            'city': city,
            'address': address,
            'raw': {field: cells[index] for field, index in columns.items()},
        })
        above = (number, city) if city else None
    if not rows:
        raise SheetError('В листе нет ни одной строки офиса')

    memo = {'title': None, 'items': []}
    memo_col = next((i for i, name in enumerate(header) if 'памятка' in name), None)
    owner_col = next((i for i, name in enumerate(header) if 'ответствен' in name), None)
    if memo_col is not None:
        for cells in table[1:]:
            cells = list(cells) + [''] * (width - len(cells))
            # Пункт — в колонке справа от значка; подзаголовок — в самой колонке.
            text = (cells[memo_col + 1] if memo_col + 1 < len(cells) else '').strip()
            title = cells[memo_col].strip()
            if not text and title and not memo['title'] and len(title) > 3:
                memo['title'] = title
                continue
            if text:
                owner = cells[owner_col].strip() if owner_col is not None else ''
                memo['items'].append({
                    'text': memo_text(text),
                    'owner': MEMO_OWNERS.get(fold(owner)),
                    'kind': memo_kind(title),
                })
    return rows, memo


def read_csv_text(text):
    """(строки офисов, памятка) из CSV-выгрузки листа."""
    return read_table(list(csv.reader(io.StringIO(str(text or '').lstrip('﻿')))))


def parse_fields(row):
    """Проверенные поля строки листа. Ошибка ячейки — rules.FieldError."""
    return rules.clean_fields({field: parse_value(field, text)
                               for field, text in row['raw'].items() if field in rules.EDITABLE_FIELDS})


# ─────────────────────────────────────────────────────────────────────────────
# Зеркало: что поменять в таблице раздела, чтобы она совпала с листом
# ─────────────────────────────────────────────────────────────────────────────

def _where(row):
    return 'строка %d листа (%s, %s)' % (row['line'], row['city'] or 'город не указан', row['address'])


def _same_office_by_street(row, target):
    """Можно ли отдать строку листа строке раздела без совпадения дома (шаг 3).

    Строка раздела без ключа листа — заведена разовым переносом, листа ещё не
    видела: да («Нышанов, 14» в листе и «Нышанов 15» в справочнике). С ключом —
    только если улица та же, что в прошлой строке листа или в справочнике:
    тогда адрес в листе поправили. Другая улица — это другой офис (закрыли
    один, открыли другой в том же городе), и привязка показала бы на экране
    адрес старого офиса с цифрами нового.
    """
    if not target.get('sheet_key'):
        return True
    streets = _streets(_address_words(row['address']))
    previous = target['sheet_key'].split('|', 1)[-1]
    return any(streets & _streets(_address_words(other)) for other in (previous, target.get('address')) if other)


def plan_mirror(sheet_rows, existing, directory):
    """План приведения таблицы раздела к листу.

    sheet_rows — строки листа (read_table); existing — все строки раздела,
    включая убранные; directory — офисы справочника, которых в таблице ещё нет.

    Строка листа узнаётся так:
      1. по ключу листа (sheet_key), запомненному в прошлый раз;
      2. по адресу среди ещё не узнанных строк раздела того же города — так
         узнаются строки, заведённые разовым переносом 02.10.2026, офис, чей
         адрес в листе переписали, и убранный офис, вернувшийся в лист;
      3. последняя не узнанная строка города в листе — с последней
         действующей строкой этого города в разделе (дом в листе и в
         справочнике разошёлся; у строки, уже видевшей лист, — только на
         той же улице);
      4. по справочнику — тогда офис заводится в таблицу.
    Не узнанная строка листа не переносится и попадает в «problems».

    Строка с ошибкой в ячейке («11,5» коробов) узнаётся как обычно, но её
    числа не трогаются: офис не должен ни пропасть из таблицы, ни показать
    чужое число из-за опечатки в листе.

    Строка раздела, которой в листе больше нет, убирается из таблицы (её
    история остаётся); вернулась в лист — возвращается. Пока в листе есть не
    узнанная строка, строки её города не убираются: может быть, это тот же
    офис с опечаткой в адресе. Город не узнан вовсе (опечатка в названии,
    строка без города) — не убирается ничего.
    """
    by_key = {}
    for row in existing:
        if row.get('sheet_key'):
            by_key.setdefault(row['sheet_key'], row)

    claimed = {}        # id строки раздела → строка листа
    matched = []        # (строка листа, строка раздела или None, офис для новой)
    problems = []
    unrecognised = []   # строки листа, которые не перенеслись: их города не убираются

    def take(row, target, office=None):
        if target is not None:
            claimed[target['id']] = row
        matched.append((row, target, office))

    def free_rows(city):
        return [item for item in existing
                if item['id'] not in claimed and fold(item.get('city')) == fold(city)]

    # 1. По ключу, запомненному в прошлый раз. Строка, повторяющая строку
    #    выше слово в слово, — вставленная копия: второй офис по ней не узнать.
    pending = []
    first_line = {}
    for row in sheet_rows:
        key = sheet_key(row['city'], row['address'])
        if row['city'] and key in first_line:
            problems.append('%s: повторяет строку %d листа' % (_where(row), first_line[key]))
            continue
        first_line.setdefault(key, row['line'])
        target = by_key.get(key) if row['city'] else None
        if target is not None and target['id'] not in claimed:
            take(row, target)
        else:
            pending.append(row)

    # 2. По адресу среди не узнанных строк раздела того же города — и
    #    убранных: офис, вернувшийся в лист, возвращается.
    rest = []
    for row in pending:
        same_city = free_rows(row['city']) if row['city'] else []
        found = None
        if same_city:
            found, _, _ = match_office(row['city'], row['address'], [
                {'id': item['id'], 'city': item.get('city'), 'address': item.get('address')}
                for item in same_city])
        if found:
            take(row, next(item for item in same_city if item['id'] == found['id']))
        else:
            rest.append(row)

    # 3. Последняя не узнанная строка города ↔ последняя действующая строка
    #    раздела того же города: дом в листе и в справочнике разошёлся
    #    («Нышанов, 14» и «Нышанов 15»). Только действующая: новая строка
    #    города — скорее новый офис, чем закрытый, вернувшийся по другому
    #    адресу. И только та же улица, если строка уже видела лист.
    still = []
    for row in rest:
        rivals = [item for item in rest if row['city'] and fold(item['city']) == fold(row['city'])]
        active = [item for item in free_rows(row['city']) if item.get('is_active', True)] if row['city'] else []
        if len(rivals) == 1 and len(active) == 1 and _same_office_by_street(row, active[0]):
            take(row, active[0])
        else:
            still.append(row)

    # 4. По справочнику — новый офис. Совпадение «дом без литеры» не заводится
    #    само: «Тауке хана, 15» и «Тауке хана, 15А» — разные офисы, а ключ листа
    #    закрепил бы ошибку навсегда. Его решает человек.
    known_cities = {fold(item.get('city')) for item in existing} | {fold(office.get('city')) for office in directory}
    taken_offices = set()
    for row in still:
        office, reason, note = match_office(row['city'], row['address'], directory)
        if office is not None and note:
            reason = ('проверьте адрес: %s с офисом справочника «%s» (id %d) — строка не перенесена'
                      % (note, office.get('address'), office['id']))
            office = None
        elif office is not None and office['id'] in taken_offices:
            reason = 'офис id %d уже выбран для другой строки листа' % office['id']
            office = None
        elif office is None and row['city'] and fold(row['city']) not in known_cities:
            reason = ('города «%s» нет ни в разделе, ни в справочнике офисов вики — опечатка '
                      'или офис ещё не заведён в вики' % row['city'])
        elif office is None and row['city'] and not any(fold(item.get('city')) == fold(row['city'])
                                                        for item in directory):
            reason = ('офис не узнан: адрес не совпал ни с одним офисом города в разделе, '
                      'а свободных офисов этого города в справочнике вики нет')
        if office is None:
            problems.append('%s: %s' % (_where(row), reason))
            unrecognised.append(row)
            continue
        taken_offices.add(office['id'])
        take(row, None, office)

    plan = {'updates': [], 'creates': [], 'keys': [], 'hide': [], 'show': [], 'problems': problems}
    for sheet_row, target, office in matched:
        key = sheet_key(sheet_row['city'], sheet_row['address'])
        try:
            fields = parse_fields(sheet_row)
        except rules.FieldError as exc:
            problems.append('%s: %s' % (_where(sheet_row), exc.message))
            fields = None
        if target is None:
            if fields is not None:
                plan['creates'].append({'office': office, 'fields': fields, 'sheet_key': key})
            continue
        if target.get('sheet_key') != key:
            plan['keys'].append({'row_id': target['id'], 'sheet_key': key})
        if not target.get('is_active', True):
            plan['show'].append(target['id'])
        if fields is not None:
            diff = rules.changes(target, fields)
            if diff:
                plan['updates'].append({'row_id': target['id'], 'city': target.get('city'),
                                        'fields': {change['field']: change['to'] for change in diff},
                                        'diff': diff})

    # Не узнанная строка листа может оказаться офисом, который иначе убрался
    # бы: опечатка в адресе («Жамбыла, 170» вместо «172») — не закрытие.
    # Пока она не узнана, строки её города остаются; город не узнан вовсе —
    # остаются все.
    held_cities = {fold(row['city']) for row in unrecognised}
    hold_all = any(not city or city not in known_cities for city in held_cities)
    gone = [row for row in existing if row['id'] not in claimed and row.get('is_active', True)]
    held = [row for row in gone if hold_all or fold(row.get('city')) in held_cities]
    held_ids = {row['id'] for row in held}
    plan['hide'] = [row['id'] for row in gone if row['id'] not in held_ids]
    if held:
        problems.append('Не убраны из раздела, хотя их нет в листе, — пока не узнаны строки выше: %s'
                        % '; '.join('%s, %s' % (row.get('city'), row.get('address')) for row in held))
    # Пропала разом бо́льшая часть офисов — это не «офис закрылся», а лист
    # переделали (сдвинули колонки, вставили строку над шапкой). Убирать
    # пол-таблицы из-за этого нельзя: оператор КЦ остался бы без адресов.
    # Один офис убирается всегда, два и больше — пока это меньше половины.
    active = sum(1 for row in existing if row.get('is_active', True))
    hide = len(plan['hide'])
    if hide >= 2 and hide * 2 >= active:
        raise SheetError('Из листа пропали %d из %d офисов — похоже, лист переделали. '
                         'Таблица раздела не тронута' % (hide, active))
    return plan


def memo_differs(current, fresh):
    """Совпадает ли памятка раздела с памяткой листа — по заголовку и пунктам."""
    current = current or {}

    def items(memo):
        return [{'text': item.get('text'), 'owner': item.get('owner') or None, 'kind': item.get('kind') or None}
                for item in (memo.get('items') or [])]

    return (current.get('title') or None) != (fresh.get('title') or None) or items(current) != items(fresh)
