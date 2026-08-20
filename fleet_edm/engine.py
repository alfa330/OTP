"""Обход кабинета: из списка ID — таблица с провайдером ЭДО.

Порядок работы и почему он такой:

1. **Разбор файла.** Ищем колонку с ID водителя и, если она есть, колонку с ID
   парка. Парк — не прихоть: список контрагентов парко-зависим, те же ID с чужим
   `x-park-id` дают 200 и пустоту (проверено 20.08.2026). Обычная выгрузка из
   кабинета колонку «ID парка» содержит, поэтому в типичном случае шага 2 нет.

2. **Определение парка** для строк, где его не дали: перебор парков пачками по
   100 ID, с ранним выходом — как только все определились, перебор кончается.
   Дорого: цена растёт числом парков, а не числом строк. В отчёт пишем, сколько
   это стоило, чтобы «файл без парка» не выглядел бесплатным.

3. **Провайдер пачками.** На каждый парк: «кто из этой сотни у провайдера X».
   Семь провайдеров = максимум семь проходов по остатку, потом такой же проход по
   архиву. Провайдеры на каждом следующем парке переставляются по частоте уже
   найденного: три четверти водителей на «Бумажном документообороте», и если
   спросить про него первым, остальные проходы идут по короткому остатку.

4. **Добор карточками.** Фильтр списка молча не возвращает часть действующих
   профилей — 286 строк из 147 238 в августе и 4 из 8 800 в повторном прогоне.
   Причина неизвестна до сих пор, поэтому остаток добираем поштучно из карточки,
   где провайдер лежит значением.

5. **Контрольная сверка.** Случайная выборка результата перепроверяется ДРУГИМ
   путём — карточкой. Это не перестраховка: в августе первый (неполный) индекс
   идеально сходился со счётчиками самого кабинета, потому что счётчик считал тот
   же урезанный срез. Дефект нашла только сверка карточками.
"""

import logging
import random
import re
from collections import Counter, OrderedDict

from .client import MAX_BATCH, FleetClient, FleetError, FleetSessionExpired

# Поля, которые просим у списка. Провайдера среди них нет и быть не может — он
# приходит тем, ПО КАКОМУ фильтру строка нашлась.
LIST_PROJECTION = ['id', 'full_name', 'phone', 'work_status', 'employment_type']

ID_RE = re.compile(r'^[0-9a-f]{32}$', re.I)

# Сколько строк перепроверяем карточкой в конце. 25 — это ~30 секунд на любом
# объёме и при этом достаточно, чтобы поймать системную ошибку вроде потерянного
# архива (она бьёт не по одной строке, а по каждой десятой).
CONTROL_SAMPLE = 25

# Сколько «ничьих» строк (парк не определился) готовы искать карточкой по всем
# паркам. Каждая такая строка стоит до 86 запросов — в августе 8 недоступных
# профилей съели столько же времени, сколько весь основной сбор.
MAX_ORPHAN_CARD_LOOKUPS = 40

WORK_STATUS_LABELS = {
    'working': 'Работает',
    'not_working': 'Не работает',
    'fired': 'Уволен',
    'busy': 'Занят',
    'in_park': 'В парке',
}

EMPLOYMENT_LABELS = {
    'individual_entrepreneur': 'ИП / самозанятый',
    'self_employed': 'Самозанятый',
    'park_employee': 'Сотрудник парка',
    'private': 'Частное лицо',
}

HEADER_ALIASES = {
    'contractor_id': (
        'contractor id', 'contractor_id', 'contractorid', 'id водителя', 'ид водителя',
        'driver id', 'driver_id', 'id исполнителя', 'идентификатор водителя', 'id',
    ),
    'park_id': (
        'id парка', 'ид парка', 'park id', 'park_id', 'parkid', 'идентификатор парка',
    ),
    'park_name': ('название парка', 'парк', 'наименование парка', 'park', 'park name'),
    'full_name': ('фио', 'ф.и.о.', 'имя', 'водитель', 'full name', 'full_name'),
    'phone': ('телефон', 'номер телефона', 'phone', 'телефон водителя'),
}


class InputError(ValueError):
    """Файл не годится: об этом говорим человеку словами, а не 500-й ошибкой."""


def _normalize_header(value):
    return re.sub(r'\s+', ' ', str(value or '').strip().lower()).strip(' :')


def _chunks(items, size=MAX_BATCH):
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ── разбор входного файла ────────────────────────────────────────────────────

def parse_input(file_bytes, filename=''):
    """Возвращает (rows, meta). rows — список словарей с contractor_id и, если
    была колонка, park_id. Порядок строк исходного файла сохраняется: человек
    сверяет результат глазами по своему же файлу."""
    if not file_bytes:
        raise InputError('Файл пустой')

    name = str(filename or '').lower()
    if name.endswith('.csv'):
        table = _read_csv(file_bytes)
    else:
        table = _read_xlsx(file_bytes)

    if not table:
        raise InputError('В файле нет ни одной строки')

    header_index, columns = _detect_columns(table)
    if 'contractor_id' not in columns:
        raise InputError(
            'Не нашли колонку с ID водителя. Нужна колонка «Contractor ID» '
            '(подойдёт также «ID водителя» или «ID»).'
        )

    rows, seen_bad = [], 0
    for row_number, raw in enumerate(table[header_index + 1:], start=header_index + 2):
        contractor_id = _cell(raw, columns.get('contractor_id'))
        if not contractor_id:
            continue
        park_id = _cell(raw, columns.get('park_id'))
        entry = {
            'row_number': row_number,
            'contractor_id': contractor_id.lower(),
            'park_id': (park_id or '').lower(),
            'source_park_name': _cell(raw, columns.get('park_name')),
            'source_full_name': _cell(raw, columns.get('full_name')),
            'source_phone': _cell(raw, columns.get('phone')),
        }
        if not ID_RE.match(entry['contractor_id']):
            entry['error'] = 'ID не похож на идентификатор водителя Fleet'
            seen_bad += 1
        rows.append(entry)

    if not rows:
        raise InputError('В колонке с ID водителя нет ни одного значения')

    meta = {
        'columns': {key: value for key, value in columns.items()},
        'rows_total': len(rows),
        'rows_bad_id': seen_bad,
        'has_park_column': 'park_id' in columns,
        'unique_ids': len({row['contractor_id'] for row in rows}),
    }
    return rows, meta


def _read_xlsx(file_bytes):
    from io import BytesIO

    import openpyxl

    try:
        workbook = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as error:
        raise InputError('Не удалось открыть файл как Excel: {}'.format(error))
    try:
        sheet = workbook[workbook.sheetnames[0]]
        # Читаем шапку и данные целиком: файлы этого раздела — десятки тысяч
        # строк в одну колонку идентификаторов, это единицы мегабайт в памяти.
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _read_csv(file_bytes):
    import csv
    import io

    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            text = file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=';,\t')
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ';' if sample.count(';') > sample.count(',') else ','
        return [row for row in csv.reader(io.StringIO(text), dialect)]
    raise InputError('Не удалось прочитать CSV: неизвестная кодировка')


def _detect_columns(table):
    """Ищем шапку в первых пяти строках: у выгрузок из кабинета сверху бывает
    заголовок отчёта, а не сразу названия колонок."""
    for index, row in enumerate(table[:5]):
        columns = {}
        for position, value in enumerate(row or []):
            header = _normalize_header(value)
            if not header:
                continue
            for key, aliases in HEADER_ALIASES.items():
                if key in columns:
                    continue
                if header in aliases:
                    columns[key] = position
        if 'contractor_id' in columns:
            return index, columns
    # Шапки нет вовсе — файл «одна колонка ID», как и просили в задаче.
    first = table[0] if table else []
    if len(first) == 1 and ID_RE.match(str(first[0] or '').strip()):
        return -1, {'contractor_id': 0}
    return 0, {}


def _cell(row, position):
    if position is None or row is None or position >= len(row):
        return ''
    value = row[position]
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


# ── обход ────────────────────────────────────────────────────────────────────

class Progress:
    """Прогресс наружу. Отдельным объектом, чтобы движок ничего не знал ни про
    базу, ни про Flask — в тестах сюда приходит список."""

    def __init__(self, callback=None):
        self._callback = callback

    def __call__(self, **payload):
        if self._callback:
            try:
                self._callback(**payload)
            except Exception:
                logging.exception('Провайдер ЭДО: не удалось записать прогресс')


def resolve(rows, client: FleetClient, *, progress=None, control_sample=CONTROL_SAMPLE,
            max_orphan_lookups=MAX_ORPHAN_CARD_LOOKUPS, rng=None):
    """Главная функция: заполняет провайдера по строкам. Возвращает словарь с
    результатом по каждому уникальному ID и статистикой прогона."""
    progress = Progress(progress) if not isinstance(progress, Progress) else progress
    rng = rng or random.Random(20260820)

    valid = [row for row in rows if not row.get('error')]
    unique = OrderedDict()
    for row in valid:
        unique.setdefault(row['contractor_id'], row.get('park_id') or '')

    progress(percent=4, note='Читаем справочник парков и провайдеров',
             rows_total=len(rows))

    parks = client.parks()
    park_names = {str(park.get('id')): _park_label(park) for park in parks}
    if not parks:
        raise FleetError('Кабинет не отдал ни одного парка')

    first_park = next(iter(unique.values()), '') or str(parks[0].get('id'))
    providers = client.edm_providers(first_park)
    if not providers:
        raise FleetError('Кабинет не отдал справочник провайдеров ЭДО')

    results = {}          # contractor_id -> запись результата
    stats = Counter()
    park_probe_requests = 0

    # ── шаг 1: у кого нет парка ──────────────────────────────────────────────
    orphans = [cid for cid, park in unique.items() if not park]
    if orphans:
        progress(percent=8, note='В файле нет ID парка — ищем водителей по паркам '
                                '({} шт.)'.format(len(orphans)))
        before = client.requests_count
        found_parks = _probe_parks(client, orphans, parks, progress)
        park_probe_requests = client.requests_count - before
        for cid, park_id in found_parks.items():
            unique[cid] = park_id
        stats['parks_probed'] = len(found_parks)

    # ── шаг 2: провайдер пачками, парк за парком ─────────────────────────────
    by_park = OrderedDict()
    for cid, park_id in unique.items():
        if park_id:
            by_park.setdefault(park_id, []).append(cid)
    # Крупные парки первыми: на них раньше набирается статистика частот
    # провайдеров, и остальные парки идут уже с хорошим порядком проходов.
    ordered_parks = sorted(by_park.items(), key=lambda item: -len(item[1]))

    seen_providers = Counter()
    leftovers = []
    done_rows = len(results)
    for index, (park_id, ids) in enumerate(ordered_parks, start=1):
        found, pending = _resolve_park(client, park_id, ids, providers, seen_providers)
        for cid, entry in found.items():
            entry['park_id'] = park_id
            results[cid] = entry
        leftovers.extend((park_id, cid) for cid in pending)
        done_rows = len(results)
        progress(
            percent=_percent(done_rows, len(unique), low=12, high=88),
            note='Парк {} из {}: определено {} из {}'.format(
                index, len(ordered_parks), done_rows, len(unique)),
            rows_resolved=done_rows,
            requests=client.requests_count,
        )

    # ── шаг 3: добор карточками ──────────────────────────────────────────────
    orphan_left = [cid for cid, park_id in unique.items()
                   if not park_id and cid not in results]
    skipped_orphans = 0
    if len(orphan_left) > max_orphan_lookups:
        skipped_orphans = len(orphan_left) - max_orphan_lookups
        orphan_left = orphan_left[:max_orphan_lookups]

    to_card = [(park_id, cid) for park_id, cid in leftovers] + \
              [('', cid) for cid in orphan_left]
    if to_card:
        progress(percent=90, note='Добираем из карточек: {} строк'.format(len(to_card)),
                 requests=client.requests_count)
        for park_id, cid in to_card:
            entry = _card_lookup(client, cid, park_id, parks)
            if entry:
                results[cid] = entry
                stats['from_card'] += 1
            else:
                stats['not_found'] += 1

    # ── шаг 4: контрольная сверка ────────────────────────────────────────────
    check = {'checked': 0, 'matched': 0, 'mismatched': []}
    from_list = [cid for cid, entry in results.items() if entry.get('source') != 'карточка']
    if from_list and control_sample:
        sample = rng.sample(from_list, min(control_sample, len(from_list)))
        progress(percent=95, note='Контрольная сверка {} строк по карточкам'.format(len(sample)),
                 requests=client.requests_count)
        for cid in sample:
            entry = results[cid]
            profile = client.driver_card(entry.get('park_id'), cid)
            if profile is None:
                continue
            card_value = FleetClient.card_provider(profile)
            check['checked'] += 1
            if card_value and card_value == entry.get('provider_name'):
                check['matched'] += 1
            elif card_value:
                check['mismatched'].append({
                    'contractor_id': cid,
                    'list': entry.get('provider_name'),
                    'card': card_value,
                })

    return {
        'results': results,
        'providers': providers,
        'park_names': park_names,
        'parks_total': len(parks),
        'check': check,
        'requests': client.requests_count,
        'park_probe_requests': park_probe_requests,
        'skipped_orphans': skipped_orphans,
        'stats': dict(stats),
        'provider_counts': dict(seen_providers),
    }


def _park_label(park):
    name = str(park.get('name') or '').strip()
    city = str(park.get('city') or '').strip()
    return '{} / {}'.format(name, city) if city else name


def _percent(done, total, low=10, high=90):
    if not total:
        return low
    return int(low + (high - low) * min(1.0, done / float(total)))


def _resolve_park(client, park_id, ids, providers, seen_providers):
    """Провайдеры для водителей ОДНОГО парка. Возвращает (найденные, остаток)."""
    found, pending = {}, list(ids)
    # Порядок проходов — по уже увиденной частоте: самый популярный провайдер
    # первым закрывает большую часть списка, и следующие проходы идут по остатку.
    ordered = sorted(providers, key=lambda p: -seen_providers.get(p['id'], 0))
    for archive in (False, True):
        if not pending:
            break
        for provider in ordered:
            if not pending:
                break
            rest = []
            for chunk in _chunks(pending):
                try:
                    contractors = client.contractors(
                        park_id, contractor_ids=chunk, edm_provider=provider['id'],
                        archive=archive, projection=LIST_PROJECTION,
                    )
                except FleetSessionExpired:
                    raise
                except FleetError:
                    # Один сорвавшийся запрос — не повод терять весь парк: эти ID
                    # уйдут в остаток и доберутся карточкой.
                    logging.exception('Провайдер ЭДО: запрос по парку %s не удался', park_id)
                    rest.extend(chunk)
                    continue
                hit = {str(item.get('id')): item for item in contractors}
                for cid in chunk:
                    item = hit.get(cid)
                    if item is None:
                        rest.append(cid)
                        continue
                    found[cid] = _entry_from_list(item, provider, archive)
                    seen_providers[provider['id']] += 1
            pending = rest
    return found, pending


def _entry_from_list(item, provider, archive):
    return {
        'contractor_id': str(item.get('id') or ''),
        'provider_id': provider['id'],
        'provider_name': provider['name'],
        'full_name': str(item.get('full_name') or '').strip(),
        'phone': str(item.get('phone') or '').strip(),
        'work_status': str(item.get('work_status') or '').strip(),
        'employment_type': str(item.get('employment_type') or '').strip(),
        'source': 'архив' if archive else 'список',
    }


def _probe_parks(client, ids, parks, progress):
    """Перебор парков для строк без парка. Два круга: обычные сегменты, затем
    архив — архивного водителя список по умолчанию не отдаёт вовсе.

    Спрашиваем только id: на этом шаге нужен ответ «твой ли это парк», а ФИО и
    телефон всё равно приедут следующим шагом, вместе с провайдером.
    """
    found = {}
    pending = list(ids)
    for archive in (False, True):
        if not pending:
            break
        for index, park in enumerate(parks, start=1):
            if not pending:
                break
            park_id = str(park.get('id'))
            rest = []
            for chunk in _chunks(pending):
                try:
                    contractors = client.contractors(
                        park_id, contractor_ids=chunk, archive=archive,
                        projection=['id'],
                    )
                except FleetSessionExpired:
                    raise
                except FleetError:
                    logging.exception('Провайдер ЭДО: перебор парка %s не удался', park_id)
                    rest.extend(chunk)
                    continue
                hit = {str(item.get('id')) for item in contractors}
                for cid in chunk:
                    if cid in hit:
                        found[cid] = park_id
                    else:
                        rest.append(cid)
            pending = rest
            if index % 10 == 0:
                progress(note='Ищем парки: осталось найти {} из {}'.format(
                    len(pending), len(ids)), requests=client.requests_count)
    return found


def _card_lookup(client, contractor_id, park_id, parks):
    """Карточка водителя. Если парк неизвестен — перебираем все, но это дорого:
    один такой водитель стоит до 86 запросов."""
    candidates = [park_id] if park_id else [str(park.get('id')) for park in parks]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            profile = client.driver_card(candidate, contractor_id)
        except FleetSessionExpired:
            raise
        except FleetError:
            continue
        if not profile:
            continue
        return {
            'contractor_id': contractor_id,
            'provider_id': '',
            'provider_name': FleetClient.card_provider(profile),
            'full_name': str(profile.get('full_name') or '').strip()
                         or _card_name(profile),
            'phone': _card_phone(profile),
            'work_status': str(profile.get('work_status') or '').strip(),
            'employment_type': str(profile.get('employment_type') or '').strip(),
            'park_id': candidate,
            'source': 'карточка',
        }
    return None


def _card_name(profile):
    name = profile.get('name') or {}
    if isinstance(name, dict):
        parts = [name.get('last'), name.get('first'), name.get('middle')]
        return ' '.join(str(part).strip() for part in parts if part)
    return ''


def _card_phone(profile):
    phones = profile.get('phones')
    if isinstance(phones, list) and phones:
        first = phones[0]
        if isinstance(first, dict):
            return str(first.get('number') or first.get('phone') or '').strip()
        return str(first).strip()
    return str(profile.get('phone') or '').strip()
