"""Обход кабинета: из списка ID — таблица с провайдером ЭДО.

Экономика обхода держится на одном измерении (20.08.2026): **фильтр принимает
минимум 10 000 идентификаторов за раз**, а сотня — это ограничение размера
СТРАНИЦЫ ответа, не запроса. Значит цена запроса зависит от числа НАЙДЕННЫХ, а не
спрошенных: диспетчерская, где из списка нет никого, отвечает одним запросом, будь
в файле десять строк или десять тысяч. Второе измерение: шесть параллельных
запросов кабинет держит без роста времени ответа (590/мин против 170 в один поток),
а 429 не приходил ни разу.

Порядок работы:

1. **Разбор файла.** Ищем колонку с ID водителя и, если есть, колонку с ID парка.
   Парк — не прихоть: список парко-зависим, те же ID с чужим `x-park-id` дают 200
   и пустоту. Обычная выгрузка из кабинета колонку «ID парка» содержит.

2. **Определение парка** для строк без него: каждой диспетчерской отдаём ВЕСЬ
   остаток списка разом, диспетчерские опрашиваем параллельно. Раньше это стоило
   86 × (строк/100) запросов и было самым дорогим местом раздела; теперь — 86
   запросов плюс страница на сотню найденных.

3. **Провайдер раундами.** Раунд на провайдера, внутри раунда парки идут
   параллельно; найденные уходят из очереди, следующий раунд спрашивает про
   остаток. «Бумажный документооборот» первым — на нём три четверти водителей.
   Потом такие же раунды по архиву: это отдельный сегмент, по умолчанию список
   его не отдаёт вовсе. Парки, где остались один-два водителя, дешевле спросить
   карточками — они уходят туда сразу.

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
import threading
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor

from .client import MAX_FILTER_IDS, FleetClient, FleetError, FleetSessionExpired

# Ниже этого числа водителей в парке дешевле спросить карточки, чем ждать раундов
# по провайдерам. Арифметика: карточка — ровно один запрос на человека, а парк,
# висящий в очереди, стоит по запросу на каждый раунд, пока не дойдёт очередь до
# ЕГО провайдера (в среднем около двух, «бумажный» идёт первым). На одном-двух
# водителях карточки выигрывают и вдобавок дают ответ из первоисточника; с трёх
# выигрывает список. Порог намеренно осторожный — не «оптимизация ради цифры».
CARDS_CHEAPER_BELOW = 3

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

# До скольких строк в доборе разрешаем перебор диспетчерских. Больше — значит
# добирать нужно многих, и перебор из полезного превращается в час ожидания.
MAX_CARD_SCANS = 50

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

    # ── шаг 2: провайдер — раундами по провайдерам, парки параллельно ────────
    by_park = OrderedDict()
    for cid, park_id in unique.items():
        if park_id:
            by_park.setdefault(park_id, []).append(cid)

    # Совсем мелкие парки дешевле спросить карточками: проход по провайдерам
    # стоит до семи запросов даже там, где в парке один человек.
    tiny = {park: ids for park, ids in by_park.items() if len(ids) < CARDS_CHEAPER_BELOW}
    big = {park: ids for park, ids in by_park.items() if len(ids) >= CARDS_CHEAPER_BELOW}

    seen_providers = Counter()
    leftovers = []
    if big:
        found, pending = _resolve_by_providers(
            client, big, providers, seen_providers, progress,
            total=len(unique), done_before=len(results),
        )
        results.update(found)
        leftovers.extend(pending)
    for park_id, ids in tiny.items():
        leftovers.extend((park_id, cid) for cid in ids)

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
        # Карточки не зависят друг от друга — значит идут в те же потоки.
        # Перебор диспетчерских включаем, только когда добирать нужно немногих:
        # один такой водитель стоит до 86 запросов.
        allow_scan = len(to_card) <= MAX_CARD_SCANS
        for entry in _run_parallel(
                client, to_card,
                lambda task: _card_lookup(client, task[1], task[0], parks,
                                          allow_scan=allow_scan)):
            if entry:
                results[entry['contractor_id']] = entry
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
        def verify(cid):
            entry = results[cid]
            try:
                profile = client.driver_card(entry.get('park_id'), cid)
            except FleetSessionExpired:
                raise
            except FleetError:
                return None
            if profile is None:
                return None
            return cid, entry.get('provider_name'), FleetClient.card_provider(profile)

        for outcome in _run_parallel(client, sample, verify):
            if not outcome:
                continue
            cid, listed, card_value = outcome
            if not card_value:
                continue
            check['checked'] += 1
            if card_value == listed:
                check['matched'] += 1
            else:
                check['mismatched'].append({
                    'contractor_id': cid, 'list': listed, 'card': card_value,
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


def _run_parallel(client, tasks, worker):
    """Раскладывает задачи по потокам. Параллельность — не украшение: замерено
    20.08.2026, шесть потоков дают 590 запросов/мин против 170 в один, и медиана
    ответа при этом не растёт. Упавшая задача не роняет остальные — её строки
    просто уйдут в добор карточками."""
    if not tasks:
        return []
    workers = max(1, min(client.concurrency, len(tasks)))
    if workers == 1:
        return [worker(tasks[0])]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='fleet-edm-io') as pool:
        return list(pool.map(worker, tasks))


def _split_tasks(pending, concurrency, slice_min=1000):
    """(парк, срез ID) для одного раунда.

    Обычно на поток приходится свой парк. Но когда парков меньше, чем потоков —
    а это ровно случай «весь файл из одной диспетчерской», — режем список одного
    парка на срезы, иначе шесть потоков простаивали бы за одним.
    """
    parks = [(park, ids) for park, ids in pending.items() if ids]
    if len(parks) >= concurrency:
        return [(park, ids) for park, ids in parks]
    tasks = []
    for park, ids in parks:
        slices = max(1, min(concurrency, (len(ids) + slice_min - 1) // slice_min))
        step = (len(ids) + slices - 1) // slices
        for start in range(0, len(ids), step):
            tasks.append((park, ids[start:start + step]))
    return tasks


def _resolve_by_providers(client, by_park, providers, seen_providers, progress,
                          total, done_before=0):
    """Провайдер для всех сразу: раунд на провайдера, парки внутри раунда параллельно.

    Почему именно так. Спросить можно про десять тысяч ID за раз, и пустой ответ
    стоит один запрос. Значит цена раунда — это число НАЙДЕННЫХ, а не спрошенных:
    один запрос на парк плюс страница на каждую сотню совпадений. После раунда
    найденные уходят из очереди, и следующий провайдер спрашивается уже про
    остаток. «Бумажный документооборот» идёт первым не случайно — на нём три
    четверти водителей, и один раунд снимает большую часть работы.
    """
    pending = {park: list(ids) for park, ids in by_park.items()}
    found = {}
    lock = threading.Lock()

    def ask(task, provider, archive):
        park_id, ids = task
        try:
            items = client.contractors_all(
                park_id, contractor_ids=ids, edm_provider=provider['id'],
                archive=archive, projection=LIST_PROJECTION,
            )
        except FleetSessionExpired:
            raise
        except FleetError:
            logging.exception('Провайдер ЭДО: запрос по парку %s не удался', park_id)
            return park_id, {}
        entries = {}
        for item in items:
            cid = str(item.get('id') or '')
            if cid:
                entry = _entry_from_list(item, provider, archive)
                entry['park_id'] = park_id
                entries[cid] = entry
        return park_id, entries

    rounds = [(archive, provider) for archive in (False, True) for provider in providers]
    for archive, provider in rounds:
        tasks = _split_tasks(pending, client.concurrency)
        if not tasks:
            break
        results = _run_parallel(client, tasks, lambda task: ask(task, provider, archive))
        with lock:
            for park_id, entries in results:
                if not entries:
                    continue
                found.update(entries)
                seen_providers[provider['id']] += len(entries)
                pending[park_id] = [cid for cid in pending.get(park_id, [])
                                    if cid not in entries]
        done = done_before + len(found)
        progress(
            percent=_percent(done, total, low=12, high=88),
            note='{}{}: определено {} из {}'.format(
                provider['name'], ' (архив)' if archive else '', done, total),
            rows_resolved=done,
            requests=client.requests_count,
        )

    leftovers = [(park_id, cid) for park_id, ids in pending.items() for cid in ids]
    return found, leftovers


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
    """Перебор диспетчерских для строк без парка — параллельно и одним списком.

    Здесь выигрыш от больших пачек самый большой. Раньше каждый парк спрашивали
    сотнями: восемь тысяч «ничьих» строк — это 88 запросов НА КАЖДЫЙ из 86 парков.
    Теперь парку отдают весь список сразу, и парк, где никого нет, отвечает одним
    запросом. Цена перебора перестала зависеть от длины файла.

    Два круга: обычные сегменты, затем архив — архивного водителя список по
    умолчанию не отдаёт вовсе.
    """
    pending = set(ids)
    found = {}
    lock = threading.Lock()

    def probe(park, archive):
        park_id = str(park.get('id'))
        with lock:
            snapshot = list(pending)
        if not snapshot:
            return 0
        try:
            items = client.contractors_all(
                park_id, contractor_ids=snapshot, archive=archive, projection=['id'],
            )
        except FleetSessionExpired:
            raise
        except FleetError:
            logging.exception('Провайдер ЭДО: перебор парка %s не удался', park_id)
            return 0
        with lock:
            for item in items:
                cid = str(item.get('id') or '')
                if cid in pending:
                    pending.discard(cid)
                    found[cid] = park_id
        return len(items)

    for archive in (False, True):
        if not pending:
            break
        _run_parallel(client, list(parks), lambda park: probe(park, archive))
        progress(
            note='Ищем диспетчерские: осталось найти {} из {}'.format(len(pending), len(ids)),
            requests=client.requests_count,
        )
    return found


def _card_lookup(client, contractor_id, park_id, parks, allow_scan=True):
    """Карточка водителя.

    Парк из файла проверяем первым, но если карточки там нет — идём по остальным
    диспетчерским. Это не перестраховка: в файле заказчика парк у части строк
    указан неверно (в августовской выгрузке для этого была отдельная колонка
    «Парк совпал»), и без перебора такие строки возвращались бы пустыми.

    Перебор дорогой — до 86 запросов на одного человека, — поэтому вызывающий
    выключает его, когда добирать нужно многих.
    """
    candidates = []
    if park_id:
        candidates.append(park_id)
    if allow_scan or not park_id:
        candidates.extend(str(park.get('id')) for park in parks
                          if str(park.get('id')) != park_id)
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
            'full_name': str(profile.get('full_name') or '').strip() or _card_name(profile),
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
