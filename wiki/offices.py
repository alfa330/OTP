"""Офисы: адреса, карта, график работы и привязка к таксопаркам.

Физический адрес — самостоятельная запись, парки к нему привязываются.
В статье «Адреса офисов» модель была обратной — таблица на каждый парк, и один
и тот же адрес переписан в шести таблицах. Расхождения в проде это и породило:
у Костаная, Павлодара, Тараза, Атырау и Кызылорды телефон отличался от таблицы
к таблице. Поэтому связь «офис ↔ парк» несёт переопределения телефона и
графика: NULL означает «как у офиса», значение — «у этого парка иначе».

Модуль держит и чистые функции (разбор ссылки 2ГИС, нормализация графика) —
они покрыты тестами без базы: tests/test_wiki_offices.py.
"""

import json
import re

# ─────────────────────────────────────────────────────────────────────────────
# Ссылка 2ГИС
# ─────────────────────────────────────────────────────────────────────────────

# Короткая ссылка go.2gis.com отдаёт 307 с полным адресом в Location, и только
# в нём есть координаты. Разворачиваем на сервере, а не в браузере: результат
# нужен один раз при сохранении, а не при каждом открытии карточки.
_SHORT_HOSTS = ('go.2gis.com', 'go.2gis.ru', 'go.2gis.kz')
_ALLOWED_HOSTS = _SHORT_HOSTS + ('2gis.com', '2gis.ru', '2gis.kz')

_COORD_PAIR = re.compile(r'(-?\d{1,3}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})')

# Границы Казахстана с запасом: по ним разрешается спор «это lon,lat или
# lat,lon». 2ГИС пишет lon,lat, но пользователь может принести координаты из
# другого места (например, скопировать из Яндекс-карт, где порядок обратный).
_KZ_LAT = (39.0, 56.5)
_KZ_LON = (45.0, 88.5)


def _host_allowed(host):
    host = (host or '').lower().split(':')[0]
    return any(host == h or host.endswith('.' + h) for h in _ALLOWED_HOSTS)


def _in(value, bounds):
    return bounds[0] <= value <= bounds[1]


def _orient(first, second):
    """Приводит пару чисел к (lat, lon).

    2ГИС пишет lon,lat. Пара считается перевёрнутой только когда прямое чтение
    невозможно, а обратное осмысленно — иначе честные координаты вроде
    «51.17,71.40» (Астана, где оба числа лежат в допустимых диапазонах) молча
    уехали бы в другую точку.
    """
    lat, lon = second, first
    direct_ok = _in(lat, _KZ_LAT) and _in(lon, _KZ_LON)
    swapped_ok = _in(first, _KZ_LAT) and _in(second, _KZ_LON)
    if not direct_ok and swapped_ok:
        lat, lon = first, second
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 6), round(lon, 6)


def parse_map_coords(url):
    """Достаёт (lat, lon) из полной ссылки 2ГИС. None, если координат нет.

    Приоритет у параметра m — это центр карты, который 2ГИС ставит на саму
    точку. Путь берётся запасным вариантом: в ссылках на филиал координаты
    стоят последним сегментом.
    """
    if not url:
        return None

    from urllib.parse import unquote, urlparse
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in ('http', 'https') or not _host_allowed(parsed.netloc):
        return None

    # m=71.406545%2C51.173129%2F18 — координаты и зум через слеш.
    for chunk in (parsed.query or '').split('&'):
        if chunk.startswith('m='):
            found = _COORD_PAIR.search(unquote(chunk[2:]))
            if found:
                return _orient(float(found.group(1)), float(found.group(2)))

    # Последний совпадающий сегмент пути: у ссылок вида
    # /astana/branches/<id>/firm/<id>/71.406531,51.173128 нужен именно он.
    matches = _COORD_PAIR.findall(unquote(parsed.path or ''))
    if matches:
        first, second = matches[-1]
        return _orient(float(first), float(second))
    return None


def resolve_map_link(url, fetch=None, max_hops=3):
    """Разворачивает ссылку 2ГИС до координат.

    fetch(url) -> (status_code, location) подменяется в тестах, чтобы сеть не
    требовалась. Ходим только по хостам 2ГИС и не больше max_hops раз — иначе
    поле «ссылка» превратилось бы в инструмент запросов с нашего сервера
    куда угодно (SSRF).
    """
    from urllib.parse import urlparse

    url = (url or '').strip()
    if not url:
        return {'error': 'Ссылка не указана'}

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not _host_allowed(parsed.netloc):
        return {'error': 'Нужна ссылка на 2ГИС'}

    resolved = url
    for _ in range(max_hops):
        coords = parse_map_coords(resolved)
        if coords:
            return {'lat': coords[0], 'lon': coords[1], 'resolved_url': resolved}

        status, location = (fetch or _fetch_redirect)(resolved)
        if not location or status not in (301, 302, 303, 307, 308):
            break
        target = urlparse(location)
        if not _host_allowed(target.netloc):
            break
        resolved = location

    return {'error': 'В ссылке нет координат — откройте точку в 2ГИС и скопируйте ссылку заново',
            'resolved_url': resolved if resolved != url else None}


def _fetch_redirect(url):
    import requests
    response = requests.get(
        url, allow_redirects=False, timeout=8,
        # Без правдоподобного User-Agent короткие ссылки отвечают 204 без Location.
        headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'},
    )
    return response.status_code, response.headers.get('Location')


# ─────────────────────────────────────────────────────────────────────────────
# График работы
# ─────────────────────────────────────────────────────────────────────────────

DAY_CODES = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')

_TIME = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')


def _time_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    found = _TIME.match(text)
    if not found:
        return None
    return '%02d:%s' % (int(found.group(1)), found.group(2))


def normalize_schedule(value):
    """Приводит график к виду {день: {from, to, break_from, break_to} | None}.

    None вместо дня — выходной. Полностью пустой график тоже None: у офиса
    «ОНЛАЙН» часов работы нет, и хранить семь выходных вместо этого значит
    показывать «Закрыто» там, где верно «Только по телефону».
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None

    result, has_open_day = {}, False
    for day in DAY_CODES:
        raw = value.get(day)
        if not isinstance(raw, dict):
            result[day] = None
            continue

        start, end = _time_or_none(raw.get('from')), _time_or_none(raw.get('to'))
        if not start or not end or start == end:
            result[day] = None
            continue

        day_value = {'from': start, 'to': end}
        break_from = _time_or_none(raw.get('break_from'))
        break_to = _time_or_none(raw.get('break_to'))
        # Обед только парой: одна граница без второй — это не перерыв, а опечатка.
        if break_from and break_to and break_from != break_to:
            day_value['break_from'] = break_from
            day_value['break_to'] = break_to
        result[day] = day_value
        has_open_day = True

    return result if has_open_day else None


# ─────────────────────────────────────────────────────────────────────────────
# Статус за день
# ─────────────────────────────────────────────────────────────────────────────

OFFICE_TIME_ZONE = 'Asia/Almaty'


def office_today():
    """Сегодня по времени офисов, а не сервера: Render живёт в UTC, и до трёх
    часов ночи по Алматы «сегодня» у него ещё вчерашнее."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(OFFICE_TIME_ZONE)).date()


def parse_day(value):
    """'2026-08-19' → date. None, если это не дата."""
    from datetime import date, datetime
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def schedule_state_on(schedule, day):
    """Статус офиса за день по недельному графику: 'open' | 'closed' | None.

    None — график не заполнен: у офиса «ОНЛАЙН» часов работы нет, и «Закрыт» про
    него было бы неправдой.

    Вердикт суточный, без обеда и без «открыто до 19:00» — намеренно проще
    клиентского officeStatus. Истории нужен ответ «работал ли офис в этот день»;
    минуты нужны только про сейчас, и их считает фронт (officeSchedule.js).
    """
    day = parse_day(day)
    if day is None:
        return None
    normalized = normalize_schedule(schedule)
    if not normalized:
        return None
    return 'open' if normalized.get(DAY_CODES[day.weekday()]) else 'closed'


def read_office_day(cursor, office_id, day):
    cursor.execute(
        'SELECT state, note, source, day FROM wiki_office_days '
        ' WHERE office_id = %s AND day = %s::date',
        (office_id, day),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'state': row[0], 'note': row[1], 'source': row[2],
            'recorded_on': row[3].isoformat() if row[3] else None}


def set_office_day(cursor, office_id, day, state, note=None, recorded_by=None):
    """Отметка человека: «в этот день офис был закрыт (открыт), вот причина»."""
    cursor.execute(
        """
        INSERT INTO wiki_office_days (office_id, day, state, note, source, recorded_by)
        VALUES (%s, %s::date, %s, %s, 'manual', %s)
        ON CONFLICT (office_id, day) DO UPDATE
           SET state = EXCLUDED.state,
               note = EXCLUDED.note,
               source = 'manual',
               recorded_by = EXCLUDED.recorded_by,
               recorded_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
        """,
        (office_id, day, state, note, recorded_by),
    )


def clear_office_day(cursor, office_id, day):
    """Снимает отметку — день снова считается по графику."""
    cursor.execute(
        'DELETE FROM wiki_office_days WHERE office_id = %s AND day = %s::date',
        (office_id, day),
    )
    return cursor.rowcount > 0


def snapshot_offices_day(cursor, day):
    """Фиксирует статус дня по графику. Возвращает число записанных строк.

    ON CONFLICT DO NOTHING держит сразу два обещания постановки: ручная отметка
    снимку не уступает, а повторный прогон не перезаписывает историю задним
    числом. Поэтому job можно гонять повторно и вручную без последствий.
    """
    day = parse_day(day)
    if day is None:
        return 0

    cursor.execute(
        "SELECT id, schedule FROM wiki_offices "
        " WHERE status = 'active' AND NOT no_office"
    )
    written = 0
    for office_id, schedule in cursor.fetchall():
        state = schedule_state_on(schedule, day)
        if state is None:
            # График не заполнен — фиксировать нечего. Иначе офис «ОНЛАЙН»
            # каждый день попадал бы в историю закрытым.
            continue
        cursor.execute(
            """
            INSERT INTO wiki_office_days (office_id, day, state, source)
            VALUES (%s, %s::date, %s, 'auto')
            ON CONFLICT (office_id, day) DO NOTHING
            """,
            (office_id, day, state),
        )
        written += cursor.rowcount
    return written


# ─────────────────────────────────────────────────────────────────────────────
# Номера парка
#
# Номер принадлежит паре «парк + точка», где точка — офис или NULL («онлайн»,
# парк принимает только по телефону). Раньше номер был одной колонкой у связи,
# и второй номер офиса записать было некуда: их сливали в одну строку через
# слеш, а оператор потом набирал её целиком.
# ─────────────────────────────────────────────────────────────────────────────

# Потолок на точку. Не ограничение модели, а защита от вставки всего справочника
# в одно поле: столбец из тридцати номеров карточку не помогает читать.
MAX_PHONES_PER_POINT = 10


def clean_phones(values):
    """Список номеров: сжатие пробелов, отсев пустых, снятие повторов, потолок.

    Повторы снимаются по видимому тексту, а не по цифрам: «+7 707 705 08 80» и
    «8 707 705 08 80» это один номер, но записан он так, как его диктуют, и
    приводить к одной форме здесь — значит спорить с тем, кто заполнял.
    """
    result = []
    for value in values or []:
        phone = ' '.join(str(value if value is not None else '').split())[:64]
        if phone and phone not in result:
            result.append(phone)
        if len(result) >= MAX_PHONES_PER_POINT:
            break
    return result


def link_phones(link):
    """Номера из тела запроса. None — «ключа не было», трогать нечего.

    Понимает и phones (список), и старый phone (строка): офисы заводит не только
    форма — тем же телом пользуется скрипт переноса.
    """
    if isinstance(link, dict):
        if isinstance(link.get('phones'), list):
            return clean_phones(link['phones'])
        if 'phone' in link:
            return clean_phones([link.get('phone')])
    return None


def phones_by_park(cursor, park_ids):
    """{park_id: {office_id | None: [номера]}} в порядке, заданном формой."""
    if not park_ids:
        return {}
    cursor.execute(
        """
        SELECT park_id, office_id, phone FROM wiki_park_phones
         WHERE park_id = ANY(%s)
         ORDER BY park_id, office_id NULLS FIRST, position, id
        """,
        (list(park_ids),),
    )
    result = {}
    for park_id, office_id, phone in cursor.fetchall():
        result.setdefault(park_id, {}).setdefault(office_id, []).append(phone)
    return result


def set_point_phones(cursor, park_id, office_id, phones):
    """Переписывает номера одной точки парка. office_id=None — «онлайн»."""
    cursor.execute(
        'DELETE FROM wiki_park_phones '
        ' WHERE park_id = %s AND office_id IS NOT DISTINCT FROM %s',
        (park_id, office_id),
    )
    for position, phone in enumerate(clean_phones(phones)):
        cursor.execute(
            'INSERT INTO wiki_park_phones (park_id, office_id, phone, position) '
            'VALUES (%s, %s, %s, %s)',
            (park_id, office_id, phone, position),
        )


def set_park_online_phones(cursor, park_id, phones):
    """Номера парка без офиса — то, что в форме помечено «онлайн»."""
    set_point_phones(cursor, park_id, None, phones)


# ─────────────────────────────────────────────────────────────────────────────
# SQL
# ─────────────────────────────────────────────────────────────────────────────

_OFFICE_KEYS = ('id', 'slug', 'name', 'city', 'address', 'address_note', 'phone',
                'map_url', 'map_resolved_url', 'lat', 'lon', 'schedule',
                'is_online', 'all_parks', 'kind', 'partner_label', 'status', 'position',
                'no_office')

_OFFICE_COLUMNS = """
    o.id, o.slug, o.name, o.city, o.address, o.address_note, o.phone,
    o.map_url, o.map_resolved_url, o.lat, o.lon, o.schedule,
    o.is_online, o.all_parks, o.kind, o.partner_label, o.status, o.position,
    o.no_office
"""


def _office_row(row):
    # Срез, а не zip по всей строке: у list_offices за колонками офиса идут ещё
    # колонки статуса дня, и молчаливое усечение zip'ом читалось бы как ошибка.
    office = dict(zip(_OFFICE_KEYS, row[:len(_OFFICE_KEYS)]))
    office['lat'] = float(office['lat']) if office['lat'] is not None else None
    office['lon'] = float(office['lon']) if office['lon'] is not None else None
    return office


def list_offices(cursor, include_archived=False, query=None, park_id=None, city=None,
                 day=None):
    """Офисы со списком парков и переопределениями.

    Парки приезжают одним запросом на всю выборку, а не запросом на карточку:
    офисов два десятка, но раскладывать их по вкладке «парк → его офисы» фронт
    обязан уметь без похода на сервер за каждым.

    day — дата, на которую нужен статус. Приезжает тем же запросом (LEFT JOIN по
    первичному ключу), поэтому «показать статус на 17 августа» не превращается в
    запрос на офис. Нет строки за день — приедет None, и клиент честно считает по
    графику.
    """
    # Склейка, а не %-форматирование: в тексте есть литеральные '%%' для ILIKE,
    # и любой %-формат поверх них пришлось бы удваивать ещё раз.
    cursor.execute(
        'SELECT ' + _OFFICE_COLUMNS + """,
               d.state, d.note, d.source, d.day
          FROM wiki_offices o
          LEFT JOIN wiki_office_days d
                 ON d.office_id = o.id AND d.day = %(day)s::date
         WHERE (%(archived)s OR o.status = 'active')
           AND (%(city)s::text IS NULL OR o.city = %(city)s::text)
           AND (%(query)s::text IS NULL
                OR o.name ILIKE '%%' || %(query)s::text || '%%'
                OR o.city ILIKE '%%' || %(query)s::text || '%%'
                OR o.address ILIKE '%%' || %(query)s::text || '%%'
                OR o.phone ILIKE '%%' || %(query)s::text || '%%')
           AND (%(park)s::int IS NULL
                OR o.all_parks
                OR EXISTS (SELECT 1 FROM wiki_office_taxi_parks op
                            WHERE op.office_id = o.id AND op.park_id = %(park)s::int))
         ORDER BY o.position, o.city NULLS LAST, o.name
        """,
        {'archived': include_archived, 'query': query or None,
         'park': park_id, 'city': city or None, 'day': day or None},
    )
    offices = []
    for row in cursor.fetchall():
        office = _office_row(row)
        state, note, source, recorded_on = row[len(_OFFICE_KEYS):]
        office['day'] = {
            'state': state,
            'note': note,
            'source': source,
            'recorded_on': recorded_on.isoformat() if recorded_on else None,
        } if state else None
        offices.append(office)
    if not offices:
        return []

    by_id = {office['id']: office for office in offices}
    for office in offices:
        office['parks'] = []

    cursor.execute(
        """
        SELECT op.office_id, op.park_id, p.name, op.schedule, op.note,
               COALESCE((SELECT array_agg(ph.phone ORDER BY ph.position, ph.id)
                           FROM wiki_park_phones ph
                          WHERE ph.park_id = op.park_id
                            AND ph.office_id = op.office_id), '{}')
          FROM wiki_office_taxi_parks op
          JOIN wiki_taxi_parks p ON p.id = op.park_id
         WHERE op.office_id = ANY(%s) AND p.status = 'active'
         ORDER BY p.position, p.name
        """,
        (list(by_id.keys()),),
    )
    for office_id, park_id_, park_name, schedule, note, phones in cursor.fetchall():
        by_id[office_id]['parks'].append({
            'park_id': park_id_, 'name': park_name, 'phones': list(phones or []),
            'schedule': schedule, 'note': note,
        })
    return offices


def get_office(cursor, office_id):
    cursor.execute(
        'SELECT ' + _OFFICE_COLUMNS + ' FROM wiki_offices o WHERE o.id = %s',
        (office_id,),
    )
    row = cursor.fetchone()
    return _office_row(row) if row else None


def cities(cursor):
    """Города для фильтра — из самих офисов, отдельного справочника нет."""
    cursor.execute(
        """
        SELECT city, count(*) FROM wiki_offices
         WHERE status = 'active' AND city IS NOT NULL AND city <> ''
         GROUP BY city ORDER BY city
        """
    )
    return [{'city': row[0], 'count': row[1]} for row in cursor.fetchall()]


_OFFICE_WRITABLE = ('name', 'city', 'address', 'address_note', 'phone',
                    'map_url', 'map_resolved_url', 'lat', 'lon', 'schedule',
                    'is_online', 'all_parks', 'kind', 'partner_label',
                    'status', 'position', 'slug', 'no_office')


def _schedule_param(value):
    from psycopg2.extras import Json
    return Json(value) if value is not None else None


def create_office(cursor, *, slug, name, fields, created_by):
    cursor.execute(
        """
        INSERT INTO wiki_offices (slug, name, city, address, address_note, phone,
                                  map_url, map_resolved_url, lat, lon, map_checked_at,
                                  schedule, is_online, all_parks, kind, partner_label,
                                  no_office, position, created_by)
        VALUES (%(slug)s, %(name)s, %(city)s, %(address)s, %(address_note)s, %(phone)s,
                %(map_url)s, %(map_resolved_url)s, %(lat)s, %(lon)s,
                CASE WHEN %(lat)s IS NULL THEN NULL ELSE (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') END,
                %(schedule)s, %(is_online)s, %(all_parks)s, %(kind)s, %(partner_label)s,
                %(no_office)s,
                COALESCE((SELECT max(position) + 1 FROM wiki_offices), 0), %(by)s)
        RETURNING id
        """,
        {'slug': slug, 'name': name, 'by': created_by,
         'no_office': bool(fields.get('no_office')),
         'city': fields.get('city'), 'address': fields.get('address'),
         'address_note': fields.get('address_note'), 'phone': fields.get('phone'),
         'map_url': fields.get('map_url'), 'map_resolved_url': fields.get('map_resolved_url'),
         'lat': fields.get('lat'), 'lon': fields.get('lon'),
         'schedule': _schedule_param(fields.get('schedule')),
         'is_online': bool(fields.get('is_online')),
         'all_parks': bool(fields.get('all_parks')),
         'kind': fields.get('kind') or 'park',
         'partner_label': fields.get('partner_label')},
    )
    return cursor.fetchone()[0]


def update_office(cursor, office_id, fields):
    sets, values = [], []
    for key in _OFFICE_WRITABLE:
        if key not in fields:
            continue
        sets.append(key + ' = %s')
        values.append(_schedule_param(fields[key]) if key == 'schedule' else fields[key])
    if not sets:
        return False
    # Отметка проверки карты идёт вместе с координатами, а не отдельным полем
    # формы: единственный способ их изменить — заново развернуть ссылку.
    if 'lat' in fields:
        sets.append("map_checked_at = CASE WHEN %s::numeric IS NULL THEN NULL "
                    "ELSE (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') END")
        values.append(fields['lat'])
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(office_id)
    cursor.execute('UPDATE wiki_offices SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    return cursor.rowcount > 0


def _link_ids(links, key):
    """Порядок сохранён, повторы сняты: id из тела запроса, что пережили int()."""
    result = []
    for link in links or []:
        try:
            value = int(link.get(key))
        except (TypeError, ValueError, AttributeError):
            continue
        if value not in result:
            result.append(value)
    return result


def set_office_parks(cursor, office_id, links):
    """Переписывает привязку офиса к паркам.

    links — список {park_id, phones, schedule, note}. Переписываем целиком, а не
    доливаем: иначе снятая в форме галочка парка не удалила бы связь.

    Сносим только связи с ЖИВЫМИ парками: архивных нет в форме, и общая
    очистка тихо оторвала бы их от офиса — а парк из архива возвращают.
    """
    cursor.execute(
        """
        DELETE FROM wiki_office_taxi_parks op
         USING wiki_taxi_parks p
         WHERE op.park_id = p.id AND op.office_id = %s AND p.status = 'active'
        """,
        (office_id,),
    )
    # Номера снятого парка уходят вместе со связью, а у оставшихся не трогаются:
    # форму офиса открывают ради графика, и она номера вообще не присылает.
    cursor.execute(
        """
        DELETE FROM wiki_park_phones ph
         USING wiki_taxi_parks p
         WHERE ph.park_id = p.id AND ph.office_id = %s AND p.status = 'active'
           AND NOT (ph.park_id = ANY(%s))
        """,
        (office_id, _link_ids(links, 'park_id')),
    )
    seen = set()
    for link in links or []:
        try:
            park_id = int(link.get('park_id'))
        except (TypeError, ValueError, AttributeError):
            continue
        if park_id in seen:
            continue
        seen.add(park_id)
        cursor.execute(
            """
            INSERT INTO wiki_office_taxi_parks (office_id, park_id, schedule, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (office_id, park_id) DO UPDATE
               SET schedule = EXCLUDED.schedule,
                   note = EXCLUDED.note
            """,
            (office_id, park_id,
             _schedule_param(normalize_schedule(link.get('schedule'))),
             link.get('note') or None),
        )
        phones = link_phones(link)
        if phones is not None:
            set_point_phones(cursor, park_id, office_id, phones)


# ─────────────────────────────────────────────────────────────────────────────
# Тайлы карты
# ─────────────────────────────────────────────────────────────────────────────

TILE_MIN_ZOOM, TILE_MAX_ZOOM = 10, 18

# Больше одновременных скачиваний 2ГИС и не примет (их и режет), а нам лишние
# потоки стоили бы соединений из общего пула.
_TILE_FETCH_SLOTS = None


def tile_is_valid(z, x, y):
    if not TILE_MIN_ZOOM <= z <= TILE_MAX_ZOOM:
        return False
    limit = 2 ** z
    return 0 <= x < limit and 0 <= y < limit


def read_tile(cursor, z, x, y):
    cursor.execute('SELECT image FROM wiki_map_tiles WHERE z = %s AND x = %s AND y = %s',
                   (z, x, y))
    row = cursor.fetchone()
    return bytes(row[0]) if row else None


def store_tile(cursor, z, x, y, image):
    cursor.execute(
        'INSERT INTO wiki_map_tiles (z, x, y, image) VALUES (%s, %s, %s, %s) '
        'ON CONFLICT (z, x, y) DO NOTHING',
        (z, x, y, memoryview(image)),
    )


def fetch_tile(z, x, y, attempts=3):
    """Скачивает тайл у 2ГИС, перебирая хосты.

    Пустой ответ (204) — не ошибка сети, а отказ обслужить: повторяем с другого
    хоста. Возвращает None, если не получилось ни разу.
    """
    global _TILE_FETCH_SLOTS
    if _TILE_FETCH_SLOTS is None:
        import threading
        _TILE_FETCH_SLOTS = threading.Semaphore(4)

    import requests
    with _TILE_FETCH_SLOTS:
        for attempt in range(attempts):
            url = ('https://tile%d.maps.2gis.com/tiles?x=%d&y=%d&z=%d&v=1'
                   % ((x + y + attempt) % 4, x, y, z))
            try:
                response = requests.get(
                    url, timeout=8,
                    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                                           'Chrome/126 Safari/537.36'})
            except Exception:  # noqa: BLE001 — сеть, следующая попытка
                continue
            if response.status_code == 200 and response.content:
                return response.content
    return None


def offices_by_park(cursor, park_ids):
    """Офисы каждого парка. {park_id: [{office_id, name, city, is_online, phones, ...}]}

    Зеркало list_offices: там к офису подтягиваются парки, здесь к парку —
    офисы. Связь и переопределения хранятся в одной таблице, меняется только
    сторона, с которой на неё смотрят.
    """
    if not park_ids:
        return {}
    cursor.execute(
        """
        SELECT op.park_id, o.id, o.name, o.city, o.is_online, op.schedule, op.note,
               COALESCE((SELECT array_agg(ph.phone ORDER BY ph.position, ph.id)
                           FROM wiki_park_phones ph
                          WHERE ph.park_id = op.park_id
                            AND ph.office_id = op.office_id), '{}')
          FROM wiki_office_taxi_parks op
          JOIN wiki_offices o ON o.id = op.office_id
         WHERE op.park_id = ANY(%s) AND o.status = 'active'
         ORDER BY o.position, o.city NULLS LAST, o.name
        """,
        (list(park_ids),),
    )
    result = {}
    for row in cursor.fetchall():
        park_id, office_id, name, city, is_online, schedule, note, phones = row
        result.setdefault(park_id, []).append({
            'office_id': office_id, 'name': name, 'city': city,
            'is_online': is_online, 'phones': list(phones or []),
            'schedule': schedule, 'note': note,
        })
    return result


def set_park_offices(cursor, park_id, links):
    """Переписывает офисы парка.

    links — список {office_id, phones, schedule, note}: номера этого парка в
    этом офисе и график, если он у парка свой (NULL = как у офиса).

    Сносим только связи с ЖИВЫМИ офисами: архивных нет в форме, и общая
    очистка оторвала бы их от парка молча.
    """
    cursor.execute(
        """
        DELETE FROM wiki_office_taxi_parks op
         USING wiki_offices o
         WHERE op.office_id = o.id AND op.park_id = %s AND o.status = 'active'
        """,
        (park_id,),
    )
    # Номера снятого офиса уходят вместе со связью: иначе они остались бы висеть
    # в таблице и вернулись бы, когда офис выберут заново. Номера оставшихся
    # офисов перепишет set_point_phones ниже.
    cursor.execute(
        """
        DELETE FROM wiki_park_phones ph
         USING wiki_offices o
         WHERE ph.office_id = o.id AND ph.park_id = %s AND o.status = 'active'
           AND NOT (ph.office_id = ANY(%s))
        """,
        (park_id, _link_ids(links, 'office_id')),
    )
    seen = set()
    for link in links or []:
        try:
            office_id = int(link.get('office_id'))
        except (TypeError, ValueError, AttributeError):
            continue
        if office_id in seen:
            continue
        seen.add(office_id)
        cursor.execute(
            """
            INSERT INTO wiki_office_taxi_parks (office_id, park_id, schedule, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (office_id, park_id) DO UPDATE
               SET schedule = EXCLUDED.schedule,
                   note = EXCLUDED.note
            """,
            (office_id, park_id,
             _schedule_param(normalize_schedule(link.get('schedule'))),
             link.get('note') or None),
        )
        set_point_phones(cursor, park_id, office_id, link_phones(link) or [])


def slug_is_free(cursor, slug, exclude_id=None):
    cursor.execute(
        'SELECT 1 FROM wiki_offices WHERE slug = %s AND (%s::int IS NULL OR id <> %s::int)',
        (slug, exclude_id, exclude_id),
    )
    return cursor.fetchone() is None
