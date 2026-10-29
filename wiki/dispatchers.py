"""Диспетчерские кабинета Fleet во вкладке «Города» (просьба владельца 29.10.2026).

Диспетчерская — это парк в кабинете Яндекс Fleet: «iTaxi» в Костанае и «iTaxi»
в Караганде — две разные диспетчерские одного парка. Водителя регистрируют
именно в диспетчерскую, поэтому на вопрос «куда зарегистрировать водителя iTaxi
из Рудного» ответ — город и диспетчерская (wiki/registration.py).

Откуда список. Своей связи с кабинетом у вики нет: куки кабинета работают
только с адреса Render и живут в разделе «Рассылки», а он раз в сутки
пересканирует все диспетчерские служебного аккаунта (89 штук) и держит их в
driver_mailing_parks — с названием и городом. Вика снимает с этого кэша свой
снимок (wiki_dispatchers, schema._REGISTRATION_STATEMENTS): сверкой вместе с
тарифами (cities.sync_due), кнопкой во вкладке и при первом открытии.

Что сверка подставляет сама (и не трогает, если правили руками):
  * парк вики — по названию, тем же разбором, каким помощник находит парк в
    вопросе (directory.match_parks): «iTaxi (Доставка)» и «iTaxi 2» — iTaxi,
    «Адал (Честный)» — Честный;
  * город, водителей которого регистрируют в эту диспетчерскую, — город из
    кабинета, а если в названии назван другой город вики, то он: «Честный
    Темиртау» записан в кабинете в Караганду, а работает на Темиртау.

Кабинет — у пространства по умолчанию («Таксопарки»): у Тез своё приложение,
и чужие диспетчерские в его вике были бы утечкой между вики.
"""

import re

from . import directory
from .schema import DEFAULT_SPACE_CODE

# Как в кабинете названы диспетчерские, если не так, как парк в вике.
# Решение владельца 29.10.2026: «Адал» по-казахски и есть «Честный» (в
# черновике «Напоминалка» оба парка пополняются одним номером), «Eki Dongelek»
# — «два колеса», то есть 2dongelek, «Жана» — Jana такси кириллицей. Подмена
# только для привязки диспетчерских: в вопросе оператора «адал» и «жаңа» —
# обычные казахские слова, там парк узнаётся по названию самой диспетчерской.
FLEET_NAME_ALIASES = (
    ('адал', 'честный'),
    ('eki dongelek', '2dongelek'),
    ('жана', 'jana'),
)

# Слова, которые ничего не называют: «Самозанятый Партнер» — это не бренд, а
# условие работы, и «куда регистрировать самозанятого из Рудного» не должно
# находить диспетчерскую «Самозанятый» в Павлодаре.
GENERIC_BRAND_WORDS = frozenset((
    'самозанятый', 'самозанятые', 'самозанятых', 'партнер', 'партнеры', 'доставка',
    'курьер', 'курьеры', 'межгород', 'такси', 'taxi', 'парк', 'таксопарк', 'вип', 'vip',
))

# Источник — кэш раздела «Рассылки» (driver_mailings/schema.py).
SOURCE_TABLE = 'driver_mailing_parks'

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


def _word(pattern):
    return re.compile(r'(?<![a-zа-я0-9])%s(?![a-zа-я0-9])' % pattern)


_ALIAS_PATTERNS = tuple((_word(re.escape(source)), target)
                        for source, target in FLEET_NAME_ALIASES)


# ─────────────────────────────────────────────────────────────────────────────
# Привязка: чистые функции — их и проверяют тесты
# ─────────────────────────────────────────────────────────────────────────────

def name_variants(name):
    """Написания названия диспетчерской: как есть и с подменой FLEET_NAME_ALIASES."""
    found = []
    for value in directory._variants(name):
        aliased = value
        for pattern, target in _ALIAS_PATTERNS:
            aliased = pattern.sub(target, aliased)
        found += [aliased, value]
    return [value for index, value in enumerate(found) if value and value not in found[:index]]


def auto_park(name, parks):
    """id парка вики, которому принадлежит диспетчерская, или None.

    Тот же разбор, что у вопроса помощнику (directory.match_parks): «iTaxi VIP»
    и «iTaxi» оба подходят под «iTaxi VIP», и остаётся длинное название. Два
    разных парка — это не догадка, а неизвестность: решает человек.
    """
    found = directory.match_parks({'variants': name_variants(name)}, parks)
    ids = {park['id'] for park in found}
    return found[0]['id'] if len(ids) == 1 else None


def auto_city(name, fleet_city, cities):
    """id города вики, водителей которого регистрируют в эту диспетчерскую.

    Город из кабинета — если в названии не назван ДРУГОЙ город вики:
    «Честный Темиртау» в кабинете записан в Караганду, а работает на Темиртау.
    Город кабинета ищется с разговорными формами («Петропавл»), как в вопросе.
    """
    names = [city['name'] for city in cities]
    by_name = {directory.normalize(city['name']): city['id'] for city in cities}
    own = directory.normalize(fleet_city)
    for named in directory.find_cities({'variants': directory._variants(name)}, names):
        if directory.normalize(named) != own:
            return by_name.get(directory.normalize(named))
    if own in by_name:
        return by_name[own]
    named = directory.find_cities({'variants': directory._variants(fleet_city)}, names)
    return by_name.get(directory.normalize(named[0])) if named else None


def base_name(name, city_names):
    """Название без городов: «Достойный Алматы» — «Достойный».

    Нужно диспетчерским без парка вики: «Достойный» в Астане, Шымкенте и
    Алматы — один бренд, и в карточке города он одна строка. Если кроме
    города в названии ничего нет («Усть-Каменогорск»), название остаётся.
    """
    forms = {directory.normalize(city) for city in city_names if city}
    forms |= {alias for alias, target in directory.CITY_ALIASES.items() if target in forms}
    outer = re.sub(r'\([^)]*\)', ' ', str(name or ''))
    words = [word for word in outer.split() if directory.normalize(word) not in forms]
    text = ' '.join(words).strip(' -–—,')
    return text or str(name or '').strip()


def is_generic(name):
    """Название из одних общих слов («Самозанятый Партнер») брендом не служит."""
    words = directory.normalize(re.sub(r'\([^)]*\)', ' ', str(name or ''))).split()
    return not words or all(word in GENERIC_BRAND_WORDS for word in words)


# ─────────────────────────────────────────────────────────────────────────────
# Чтение и запись — граница пространства в каждом запросе
# ─────────────────────────────────────────────────────────────────────────────

def fleet_space_id(cursor):
    """Пространство, которому принадлежит кабинет Fleet, — по умолчанию."""
    cursor.execute('SELECT id FROM wiki_spaces WHERE code = %s', (DEFAULT_SPACE_CODE,))
    row = cursor.fetchone()
    return row[0] if row else None


def _source_rows(cursor):
    """Строки кэша «Рассылок» или None, если таблицы нет (пустая база, стенд)."""
    cursor.execute('SELECT to_regclass(%s)', ('public.' + SOURCE_TABLE,))
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    # Порядок — постоянный: два первых открытия вкладки разом вставляют одни и
    # те же ключи, и в разном порядке они взяли бы блокировки крест-накрест.
    cursor.execute('SELECT park_id, name, city, checked_at FROM ' + SOURCE_TABLE +
                   ' ORDER BY park_id')
    return [row for row in cursor.fetchall() if row and row[0]]


def _space_parks(cursor, *, space_id):
    cursor.execute("SELECT id, name FROM wiki_taxi_parks WHERE space_id = %s AND status = 'active' "
                   "ORDER BY position, name", (space_id,))
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]


def _space_cities(cursor, *, space_id):
    cursor.execute("SELECT id, name FROM wiki_cities WHERE space_id = %s AND status = 'active' "
                   "ORDER BY position, name", (space_id,))
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]


def sync_from_fleet(cursor, *, space_id):
    """Снять диспетчерские с кэша «Рассылок» в справочник пространства.

    Возвращает сводку {'status', 'total', 'linked', 'added', 'gone'}. Пустой
    источник НИЧЕГО не меняет: пустым кэш бывает только когда его очистили
    при смене аккаунта или опрос сорвался, и пометить по нему все 89
    диспетчерских ушедшими значило бы оставить вкладку без ответа на день.
    Ушедшая из кабинета диспетчерская получает status = 'gone', а не
    удаляется: ручная привязка переживёт её возвращение.
    """
    if space_id is None or space_id != fleet_space_id(cursor):
        return {'status': 'no_source'}
    rows = _source_rows(cursor)
    if rows is None:
        return {'status': 'no_source'}
    if not rows:
        return {'status': 'empty_source'}
    parks = _space_parks(cursor, space_id=space_id)
    cities = _space_cities(cursor, space_id=space_id)
    from psycopg2.extras import execute_values
    values, linked = [], 0
    for fleet_id, name, fleet_city, checked_at in rows:
        title = str(name or '').strip() or str(fleet_id)
        city_name = str(fleet_city or '').strip() or None
        park_id = auto_park(title, parks)
        linked += park_id is not None
        values.append((space_id, str(fleet_id), title[:255], city_name, park_id,
                       auto_city(title, city_name, cities), checked_at))
    inserted = execute_values(
        cursor,
        """
        INSERT INTO wiki_dispatchers AS d (space_id, fleet_park_id, name, fleet_city, park_id,
                                           city_id, fleet_checked_at, seen_at)
        VALUES %s
        ON CONFLICT (space_id, fleet_park_id) DO UPDATE SET
            name = EXCLUDED.name,
            fleet_city = EXCLUDED.fleet_city,
            park_id = CASE WHEN d.park_manual THEN d.park_id ELSE EXCLUDED.park_id END,
            city_id = CASE WHEN d.city_manual THEN d.city_id ELSE EXCLUDED.city_id END,
            fleet_checked_at = EXCLUDED.fleet_checked_at,
            seen_at = EXCLUDED.seen_at,
            status = 'active',
            updated_at = CASE
                WHEN d.name IS DISTINCT FROM EXCLUDED.name
                  OR d.fleet_city IS DISTINCT FROM EXCLUDED.fleet_city
                  OR d.status <> 'active'
                  OR (NOT d.park_manual AND d.park_id IS DISTINCT FROM EXCLUDED.park_id)
                  OR (NOT d.city_manual AND d.city_id IS DISTINCT FROM EXCLUDED.city_id)
                THEN EXCLUDED.seen_at ELSE d.updated_at END
        RETURNING (xmax = 0)
        """,
        values,
        # checked_at в кэше «Рассылок» — TIMESTAMPTZ, а время вики — Алматы без
        # пояса: без перевода «сверено в 14:47» показывало бы время по UTC.
        template="(%s, %s, %s, %s, %s, %s, (%s::timestamptz AT TIME ZONE 'Asia/Almaty'), "
                 + _NOW + ')',
        fetch=True,
    )
    cursor.execute(
        "UPDATE wiki_dispatchers SET status = 'gone', updated_at = " + _NOW +
        " WHERE space_id = %s AND status = 'active' AND fleet_park_id <> ALL(%s)",
        (space_id, [value[1] for value in values]),
    )
    gone = cursor.rowcount or 0
    return {'status': 'ok', 'total': len(values), 'linked': linked,
            'added': sum(1 for row in inserted or () if row and row[0]), 'gone': gone}


def ensure_synced(cursor, *, space_id):
    """Первое открытие после выкладки: снимка ещё нет — снять сейчас.

    Дальше снимок обновляет сверка, а не каждое открытие вкладки: копировать
    89 строк на каждый щелчок незачем. Пространство без кабинета молчит.
    """
    if space_id != fleet_space_id(cursor):
        return None
    cursor.execute('SELECT 1 FROM wiki_dispatchers WHERE space_id = %s LIMIT 1', (space_id,))
    if cursor.fetchone():
        return None
    return sync_from_fleet(cursor, space_id=space_id)


_DISPATCHER_KEYS = ('id', 'fleet_park_id', 'name', 'fleet_city', 'park_id', 'park_name',
                    'park_manual', 'city_id', 'city_name', 'city_manual', 'status',
                    'fleet_checked_at', 'seen_at', 'updated_at')


def _iso(value):
    return value.isoformat() if value is not None else None


def _dispatcher_row(row):
    item = dict(zip(_DISPATCHER_KEYS, row))
    for key in ('fleet_checked_at', 'seen_at', 'updated_at'):
        item[key] = _iso(item[key])
    # Парк ушёл в архив или оказался чужим — привязки нет вовсе.
    if not item['park_name']:
        item['park_id'] = None
    if not item['city_name']:
        item['city_id'] = None
    return item


# Парк и город — только живые и своего пространства: условие в JOIN, а не
# доверие к колонке. Колонка ручной правки приходит из формы.
_DISPATCHER_SELECT = """
    SELECT d.id, d.fleet_park_id, d.name, d.fleet_city, d.park_id, p.name,
           d.park_manual, d.city_id, c.name, d.city_manual, d.status,
           d.fleet_checked_at, d.seen_at, d.updated_at
      FROM wiki_dispatchers d
      LEFT JOIN wiki_taxi_parks p ON p.id = d.park_id AND p.space_id = d.space_id
                                 AND p.status = 'active'
      LEFT JOIN wiki_cities c ON c.id = d.city_id AND c.space_id = d.space_id
                             AND c.status = 'active'
"""


def list_dispatchers(cursor, *, space_id, include_gone=False):
    """Диспетчерские пространства: живые, а с include_gone — и ушедшие из кабинета."""
    cursor.execute(
        _DISPATCHER_SELECT + """
         WHERE d.space_id = %(space)s AND (%(gone)s OR d.status = 'active')
         ORDER BY COALESCE(c.name, d.fleet_city, ''), d.name, d.id
        """,
        {'space': space_id, 'gone': bool(include_gone)},
    )
    return [_dispatcher_row(row) for row in cursor.fetchall()]


def get_dispatcher(cursor, dispatcher_id, *, space_id):
    cursor.execute(_DISPATCHER_SELECT + ' WHERE d.id = %s AND d.space_id = %s',
                   (dispatcher_id, space_id))
    row = cursor.fetchone()
    return _dispatcher_row(row) if row else None


def sync_state(cursor, *, space_id):
    """Сколько диспетчерских и насколько свежие: для подписи во вкладке."""
    cursor.execute(
        "SELECT count(*) FILTER (WHERE status = 'active'), max(seen_at), max(fleet_checked_at) "
        "  FROM wiki_dispatchers WHERE space_id = %s",
        (space_id,),
    )
    row = cursor.fetchone() or (0, None, None)
    return {'count': row[0] or 0, 'synced_at': _iso(row[1]), 'fleet_checked_at': _iso(row[2])}


def update_dispatcher(cursor, dispatcher_id, *, space_id, park=None, city=None):
    """Ручная привязка: park / city — (id или None, вручную ли).

    (5, True) — привязать к парку 5 руками; (None, True) — «без парка» руками;
    (7, False) — вернуть автоматическую привязку (её считает роут тем же
    auto_park). Чужой или архивный id сохраняется как «без привязки», без
    ошибки — то же правило, что у связей «офис ↔ парк».
    """
    sets, values = [], []
    if park is not None:
        sets.append('park_id = (SELECT p.id FROM wiki_taxi_parks p WHERE p.id = %s '
                    "AND p.space_id = d.space_id AND p.status = 'active'), park_manual = %s")
        values += [park[0], bool(park[1])]
    if city is not None:
        sets.append('city_id = (SELECT c.id FROM wiki_cities c WHERE c.id = %s '
                    "AND c.space_id = d.space_id AND c.status = 'active'), city_manual = %s")
        values += [city[0], bool(city[1])]
    if not sets:
        return False
    cursor.execute('UPDATE wiki_dispatchers d SET ' + ', '.join(sets) + ', updated_at = ' + _NOW +
                   ' WHERE d.id = %s AND d.space_id = %s', values + [dispatcher_id, space_id])
    return (cursor.rowcount or 0) > 0


def auto_values(cursor, dispatcher, *, space_id):
    """(парк, город) по названию — для «вернуть как в кабинете»."""
    return (auto_park(dispatcher['name'], _space_parks(cursor, space_id=space_id)),
            auto_city(dispatcher['name'], dispatcher['fleet_city'],
                      _space_cities(cursor, space_id=space_id)))
