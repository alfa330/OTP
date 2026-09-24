"""Города: тарифы Яндекс Go по городу и то, что дописывают руками (задача #322).

Запись — одна на город В ПРОСТРАНСТВЕ (wiki_cities.space_id), как у офисов и
парков: у Таксопарков и у Тез свои списки городов, и правка одного не видна в
другом. Поэтому space_id — обязательный именованный аргумент у каждой функции,
которая трогает записи; кто спросил и вправе ли — решает
routes_structure.request_space. Исключение одно — ночная сверка (due_cities):
она идёт по всем пространствам, и пространство там — часть ответа, а не
условие выборки.

Что откуда:
  * тарифы города, цены заказа, опции, маршруты и номер заказа по телефону —
    со страницы Яндекса (wiki/yandex_tariffs.py), слепком в yandex_data.
    Слепок перезаписывается ТОЛЬКО когда изменилось содержимое (отпечаток
    yandex_hash), поэтому «Обновлено» в карточке — дата настоящего изменения,
    а не ночного прогона;
  * комиссия Яндекса по тарифу, требования к авто, комиссия парка, услуги
    парка, обслуживающий офис и заметка — руками в редакторе: на публичных
    страницах Яндекса этого нет (проверено по всем 24 городам постановки).

Ручные поля по тарифу (tariff_meta) привязаны к КОДУ тарифа Яндекса
(«econom», «business»), а не к названию: у Яндекса «Комфорт» внутри называется
business, а «Бизнес» — vip, и привязка по названию однажды переехала бы на
соседний тариф.
"""

import logging
import re
import time

from . import yandex_tariffs

logger = logging.getLogger(__name__)

# Города из постановки задачи #322 (файл со ссылками от Кастек Гаухар,
# 12.09.2026). Заводятся один раз в пространстве по умолчанию — см.
# schema._seed_cities. Названия — как в общем справочнике городов фронта
# (src/utils/kazakhstanCities.js): в файле было «Экбастуз», а офисы и
# карточка сотрудника пишут «Экибастуз», и город с опечаткой не нашёл бы свой
# офис.
DEFAULT_CITIES = (
    ('Аксай', 'https://taxi.yandex.ru/aksay/tariff'),
    ('Актау', 'https://taxi.yandex.ru/aktau/tariff'),
    ('Актобе', 'https://taxi.yandex.ru/aktobe/tariff'),
    ('Алматы', 'https://taxi.yandex.ru/almaty/tariff'),
    ('Астана', 'https://taxi.yandex.ru/astana/tariff'),
    ('Атырау', 'https://taxi.yandex.ru/atyrau/tariff'),
    ('Жанаозен', 'https://taxi.yandex.ru/zhanaozen/tariff'),
    ('Жезказган', 'https://taxi.yandex.ru/zhezkazgan/tariff'),
    ('Караганда', 'https://taxi.yandex.ru/karaganda/tariff'),
    ('Кентау', 'https://taxi.yandex.ru/kentau/tariff'),
    ('Костанай', 'https://taxi.yandex.ru/kostanai/tariff'),
    ('Кызылорда', 'https://taxi.yandex.ru/kyzylorda/tariff'),
    ('Павлодар', 'https://taxi.yandex.ru/pavlodar/tariff'),
    ('Петропавловск', 'https://taxi.yandex.ru/petropavlovsk/tariff'),
    ('Рудный', 'https://taxi.yandex.ru/rudny/tariff'),
    ('Семей', 'https://taxi.yandex.ru/semey/tariff'),
    ('Талдыкорган', 'https://taxi.yandex.ru/taldykorgan/tariff'),
    ('Тараз', 'https://taxi.yandex.ru/taraz/tariff'),
    ('Темиртау', 'https://taxi.yandex.ru/temirtau/tariff'),
    ('Туркестан', 'https://taxi.yandex.ru/turkestan/tariff'),
    ('Уральск', 'https://taxi.yandex.ru/uralsk/tariff'),
    ('Усть-Каменогорск', 'https://taxi.yandex.kz/ust_kamenogorsk/tariff'),
    ('Шымкент', 'https://taxi.yandex.kz/ru_kz/chimkent/tariff'),
    ('Экибастуз', 'https://taxi.yandex.ru/ekibastuz/tariff'),
)

# Пределы ручных списков. Не про базу, а про карточку: двадцать услуг парка —
# это уже не карточка города, а каталог, и такой список надо вести не здесь.
MAX_EXTRA_TARIFFS = 20
MAX_SERVICES = 20
MAX_TARIFF_META = 60

# Ночная сверка: город, сверенный меньше этого срока назад, не трогаем.
# Прогон после каждого деплоя (bot_schedule2) иначе перекачивал бы все
# страницы по нескольку раз в день.
SYNC_MAX_AGE_HOURS = 12
# Пауза между городами — вежливость к источнику: 24 запроса подряд без паузы
# выглядят как робот, и Яндекс вправе начать отвечать капчей.
SYNC_PAUSE_SECONDS = 1.0

_TARIFF_CODE_RE = re.compile(r'^[a-z0-9_]{1,40}$')


class CityFieldError(ValueError):
    """Значение поля не принято. Текст — для человека, уходит в 400."""


# ── Ручные поля ──────────────────────────────────────────────────────────────

def _text(value, limit):
    text = re.sub(r'\s+', ' ', str(value if value is not None else '')).strip()
    return text[:limit] if text else None


def parse_percent(value, label='Комиссия'):
    """«4», «4,5», «12 %» → 4.0 / 4.5 / 12.0. Пусто → None.

    Отказом, а не молчаливым None: «4.5.1» или «сто» в поле комиссии — это
    опечатка человека, и сохранить карточку без комиссии, не сказав об этом,
    значило бы показать оператору пустоту там, где её заполняли.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = str(value).strip().replace('%', '').replace(',', '.').strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            raise CityFieldError('%s — число от 0 до 100' % label)
    if not 0 <= number <= 100:
        raise CityFieldError('%s — число от 0 до 100' % label)
    return round(number, 2)


def clean_tariff_meta(value):
    """Ручные поля по тарифам Яндекса: {код: {commission, requirement, hidden}}.

    Пустая запись выбрасывается: ключ без значений — это «ничего не
    заполняли», и хранить его значило бы сравнивать пустоту с пустотой.
    """
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise CityFieldError('Поля тарифов пришли в неверном виде')
    result = {}
    for code, raw in value.items():
        code = str(code or '').strip()
        if not _TARIFF_CODE_RE.match(code) or not isinstance(raw, dict):
            continue
        entry = {
            'commission': parse_percent(raw.get('commission'), 'Комиссия тарифа'),
            'requirement': _text(raw.get('requirement'), 160),
            'hidden': bool(raw.get('hidden')),
        }
        if entry['commission'] is None and not entry['requirement'] and not entry['hidden']:
            continue
        result[code] = entry
        if len(result) >= MAX_TARIFF_META:
            break
    return result


def clean_extra_tariffs(value):
    """Тарифы, которых нет у Яндекса: [{name, commission, requirement, price}].

    Нужны не только «на всякий случай»: у пространства Тез свои тарифы, и
    ссылки на Яндекс у его городов может не быть вовсе.
    """
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise CityFieldError('Список тарифов пришёл в неверном виде')
    result = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get('name'), 80)
        if not name:
            continue
        result.append({
            'name': name,
            'commission': parse_percent(raw.get('commission'), 'Комиссия тарифа'),
            'requirement': _text(raw.get('requirement'), 160),
            'price': _text(raw.get('price'), 80),
        })
    if len(result) > MAX_EXTRA_TARIFFS:
        raise CityFieldError('Своих тарифов — не больше %d' % MAX_EXTRA_TARIFFS)
    return result


def clean_services(value):
    """Услуги парка в городе: [{title, note}]. Название обязательно."""
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise CityFieldError('Список услуг пришёл в неверном виде')
    result = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        title = _text(raw.get('title'), 80)
        if not title:
            continue
        result.append({'title': title, 'note': _text(raw.get('note'), 120)})
    if len(result) > MAX_SERVICES:
        raise CityFieldError('Услуг — не больше %d' % MAX_SERVICES)
    return result


# ── Чтение ───────────────────────────────────────────────────────────────────

# Колонки сводки. Слепка тарифов в сводке нет: на 24 города это ~140 КБ JSON
# (ответы портала не сжимаются), а для списка и карты нужны только число
# тарифов и номер заказа. Сам слепок приезжает карточкой — get_city.
_SUMMARY_COLUMNS = """
    c.id, c.name, c.yandex_url, c.yandex_zone,
    c.yandex_checked_at, c.yandex_changed_at, c.yandex_error,
    c.serving_office_id, c.park_commission, c.tariff_meta,
    c.extra_tariffs, c.services, c.note, c.status, c.position,
    c.created_at, c.updated_at,
    COALESCE(jsonb_array_length(c.yandex_data->'tariffs'), 0),
    c.yandex_data->>'phone',
    so.name, so.city,
    EXISTS (SELECT 1 FROM wiki_offices o
             WHERE o.space_id = c.space_id AND o.status = 'active'
               AND NOT o.no_office AND o.kind = 'park'
               AND lower(o.city) = lower(c.name))
"""

_SUMMARY_KEYS = (
    'id', 'name', 'yandex_url', 'yandex_zone',
    'yandex_checked_at', 'yandex_changed_at', 'yandex_error',
    'serving_office_id', 'park_commission', 'tariff_meta',
    'extra_tariffs', 'services', 'note', 'status', 'position',
    'created_at', 'updated_at',
    'tariff_count', 'order_phone',
    'serving_office_name', 'serving_office_city',
    'has_office',
)

# Обслуживающий офис — только своего пространства. Условие в самом JOIN, а не
# доверие к колонке: id офиса попадает в колонку из формы, и на чужой офис
# сводка обязана ответить «офиса нет», а не его названием.
_SUMMARY_FROM = """
      FROM wiki_cities c
      LEFT JOIN wiki_offices so
             ON so.id = c.serving_office_id AND so.space_id = c.space_id
            AND so.status = 'active'
"""


def _iso(value):
    return value.isoformat() if value is not None else None


def _city_row(row):
    city = dict(zip(_SUMMARY_KEYS, row[:len(_SUMMARY_KEYS)]))
    for key in ('yandex_checked_at', 'yandex_changed_at', 'created_at', 'updated_at'):
        city[key] = _iso(city[key])
    city['park_commission'] = (float(city['park_commission'])
                               if city['park_commission'] is not None else None)
    city['tariff_meta'] = city['tariff_meta'] or {}
    city['extra_tariffs'] = city['extra_tariffs'] or []
    city['services'] = city['services'] or []
    if not city['serving_office_name']:
        # Офис ушёл в архив или оказался чужим — «обслуживает» его нет вовсе.
        city['serving_office_id'] = None
    return city


def list_cities(cursor, *, space_id):
    """Сводка по живым городам пространства — для списка, поиска и карты.

    Архива во вкладке нет (решение владельца 24.09.2026: переключатель
    «Архив» убран): архивный город возвращают, добавив его снова.
    """
    cursor.execute(
        'SELECT ' + _SUMMARY_COLUMNS + _SUMMARY_FROM + """
         WHERE c.space_id = %(space)s AND c.status = 'active'
         ORDER BY c.position, c.name
        """,
        {'space': space_id},
    )
    return [_city_row(row) for row in cursor.fetchall()]


def get_city(cursor, city_id, *, space_id):
    """Карточка города целиком: сводка + слепок тарифов Яндекса."""
    cursor.execute(
        'SELECT ' + _SUMMARY_COLUMNS + ', c.yandex_data' + _SUMMARY_FROM +
        ' WHERE c.id = %s AND c.space_id = %s',
        (city_id, space_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    city = _city_row(row)
    city['yandex_data'] = row[len(_SUMMARY_KEYS)]
    return city


def find_by_name(cursor, name, exclude_id=None, *, space_id):
    """(id, статус) записи с таким названием В ЭТОМ пространстве или None.

    Без учёта регистра и вместе с архивом: уникальный индекс архива не
    различает, а архивный город — это не «занято», а «уже был»: добавить его
    снова значит вернуть ту же карточку (routes_cities).
    """
    cursor.execute(
        'SELECT id, status FROM wiki_cities WHERE space_id = %s AND lower(name) = lower(%s) '
        ' AND (%s::int IS NULL OR id <> %s::int) LIMIT 1',
        (space_id, name, exclude_id, exclude_id),
    )
    row = cursor.fetchone()
    return (row[0], row[1]) if row else None


def own_office(cursor, office_id, *, space_id):
    """id офиса, если он живой и из этого пространства, иначе None.

    Чужой id не вызывает ошибку, а просто не сохраняется — то же правило, что
    у связей «офис ↔ парк» (offices.own_office_ids): «такого офиса тут нет»
    подтверждало бы, что где-то он есть.
    """
    if office_id is None:
        return None
    cursor.execute(
        "SELECT id FROM wiki_offices WHERE id = %s AND space_id = %s AND status = 'active'",
        (office_id, space_id),
    )
    row = cursor.fetchone()
    return row[0] if row else None


# ── Запись ───────────────────────────────────────────────────────────────────

_WRITABLE = ('name', 'yandex_url', 'serving_office_id', 'park_commission',
             'tariff_meta', 'extra_tariffs', 'services', 'note', 'status', 'position')
_JSON_FIELDS = ('tariff_meta', 'extra_tariffs', 'services')


def _param(key, value):
    if key in _JSON_FIELDS:
        from psycopg2.extras import Json
        return Json(value if value is not None else ({} if key == 'tariff_meta' else []))
    return value


def create_city(cursor, *, fields, created_by, space_id):
    columns = ['space_id', 'created_by', 'updated_by']
    values = [space_id, created_by, created_by]
    for key in _WRITABLE:
        if key in fields and key != 'position':
            columns.append(key)
            values.append(_param(key, fields[key]))
    # Новый город — в конец списка своего пространства: общий max сдвигал бы
    # первый город новой вики на двадцать пятое место.
    cursor.execute(
        'INSERT INTO wiki_cities (' + ', '.join(columns) + ', position) VALUES (' +
        ', '.join(['%s'] * len(values)) +
        ', COALESCE((SELECT max(position) + 1 FROM wiki_cities WHERE space_id = %s), 0))'
        ' RETURNING id',
        values + [space_id],
    )
    return cursor.fetchone()[0]


def update_city(cursor, city_id, fields, *, space_id, updated_by=None):
    sets, values = [], []
    for key in _WRITABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(_param(key, fields[key]))
    if not sets:
        return False
    # Ссылку сменили — старый слепок принадлежит другой странице. Держать его
    # до следующей сверки значило бы показывать тарифы Алматы под адресом
    # Астаны; поэтому слепок гасится тем же UPDATE, а свежий кладёт сверка.
    if 'yandex_url' in fields:
        sets.append("""
            yandex_data = CASE WHEN yandex_url IS NOT DISTINCT FROM %s
                               THEN yandex_data END,
            yandex_hash = CASE WHEN yandex_url IS NOT DISTINCT FROM %s
                               THEN yandex_hash END,
            yandex_zone = CASE WHEN yandex_url IS NOT DISTINCT FROM %s
                               THEN yandex_zone END,
            yandex_checked_at = CASE WHEN yandex_url IS NOT DISTINCT FROM %s
                                     THEN yandex_checked_at END,
            yandex_error = CASE WHEN yandex_url IS NOT DISTINCT FROM %s
                                THEN yandex_error END
        """)
        values.extend([fields['yandex_url']] * 5)
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    sets.append('updated_by = %s')
    values.append(updated_by)
    values.extend((city_id, space_id))
    # Порядок SET важен: CASE выше читает СТАРОЕ значение yandex_url — в
    # Postgres все выражения SET считаются по строке до изменения.
    cursor.execute('UPDATE wiki_cities SET ' + ', '.join(sets) +
                   ' WHERE id = %s AND space_id = %s', values)
    return cursor.rowcount > 0


def store_tariffs(cursor, city_id, *, data=None, digest=None, error=None, space_id):
    """Результат сверки с Яндексом. Возвращает 'changed' | 'same' | 'error'.

    Ошибка слепок НЕ стирает: Яндекс мог не ответить одну ночь, и оператор
    по-прежнему должен видеть вчерашние тарифы — с пометкой, что свежих нет.
    Неизменившийся источник не трогает ни слепок, ни дату изменения: иначе
    «Обновлено» в карточке сдвигалось бы каждую ночь без единой правки.
    """
    if error:
        cursor.execute(
            "UPDATE wiki_cities SET yandex_error = %s, "
            "yandex_checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') "
            "WHERE id = %s AND space_id = %s",
            (str(error)[:500], city_id, space_id),
        )
        return 'error'
    # Прежний отпечаток — отдельным чтением с блокировкой строки: ответ
    # «изменилось или нет» нужен и журналу, и кнопке, а RETURNING отдал бы уже
    # новое значение. FOR UPDATE — чтобы ручная кнопка и ночной обход, попав
    # на один город одновременно, не записали «изменилось» оба.
    cursor.execute('SELECT yandex_hash FROM wiki_cities '
                   'WHERE id = %s AND space_id = %s FOR UPDATE', (city_id, space_id))
    row = cursor.fetchone()
    if not row:
        return None
    changed = row[0] != digest
    from psycopg2.extras import Json
    if changed:
        cursor.execute(
            """
            UPDATE wiki_cities
               SET yandex_data = %s, yandex_hash = %s, yandex_zone = %s,
                   yandex_error = NULL,
                   yandex_changed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                   yandex_checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE id = %s AND space_id = %s
            """,
            (Json(data), digest, (data or {}).get('zone'), city_id, space_id),
        )
        return 'changed'
    cursor.execute(
        "UPDATE wiki_cities SET yandex_error = NULL, "
        "yandex_checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') "
        "WHERE id = %s AND space_id = %s",
        (city_id, space_id),
    )
    return 'same'


def sync_city(cursor, city_id, *, space_id, fetch=None):
    """Сверить один город сейчас — ручная кнопка «Обновить с Яндекса».

    Сеть внутри курсора — осознанно и ровно на одну страницу: так устроены и
    двери базы знаний Яндекс Про (routes_yandex_pro). Обход всех городов
    работает иначе — sync_due, без открытого соединения на время сети.
    """
    cursor.execute('SELECT yandex_url FROM wiki_cities WHERE id = %s AND space_id = %s',
                   (city_id, space_id))
    row = cursor.fetchone()
    if not row:
        return None
    if not row[0]:
        return {'status': 'error', 'error': 'У города нет ссылки на тарифы Яндекса'}
    try:
        data, digest = yandex_tariffs.read_tariffs(row[0], fetch=fetch)
    except yandex_tariffs.TariffSourceError as error:
        store_tariffs(cursor, city_id, error=str(error), space_id=space_id)
        return {'status': 'error', 'error': str(error)}
    status = store_tariffs(cursor, city_id, data=data, digest=digest, space_id=space_id)
    return {'status': status, 'error': None}


def due_cities(cursor, *, max_age_hours=SYNC_MAX_AGE_HOURS, limit=200):
    """Города всех пространств, которые пора сверить. Для ночного обхода.

    Пространство — в ответе, а не в условии: обход один на всю вику, а запись
    результата (store_tariffs) всё равно идёт через пространство записи.
    """
    cursor.execute(
        """
        SELECT id, space_id, yandex_url FROM wiki_cities
         WHERE status = 'active' AND yandex_url IS NOT NULL AND yandex_url <> ''
           AND (yandex_checked_at IS NULL
                OR yandex_checked_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                                       - make_interval(hours => %s))
         ORDER BY yandex_checked_at NULLS FIRST, id
         LIMIT %s
        """,
        (int(max_age_hours), int(limit)),
    )
    return [{'id': row[0], 'space_id': row[1], 'url': row[2]} for row in cursor.fetchall()]


def sync_due(db, *, fetch=None, max_age_hours=SYNC_MAX_AGE_HOURS,
             pause=SYNC_PAUSE_SECONDS, sleep=time.sleep):
    """Обход: скачать то, что пора, и записать. Курсор — только на короткие такты.

    db — объект с _get_cursor() (тот же, что у Blueprint вики). Страницы
    качаются БЕЗ открытого соединения: 24 запроса с паузой — это полминуты, и
    держать всё это время место в пуле вики нельзя.
    """
    with db._get_cursor() as cursor:
        targets = due_cities(cursor, max_age_hours=max_age_hours)

    summary = {'cities': len(targets), 'checked': 0, 'changed': 0, 'errors': 0}
    for index, target in enumerate(targets):
        if index and pause:
            sleep(pause)
        data = digest = failure = None
        try:
            data, digest = yandex_tariffs.read_tariffs(target['url'], fetch=fetch)
        except yandex_tariffs.TariffSourceError as error:
            failure = str(error)
        except Exception as error:                          # noqa: BLE001
            logger.exception('Тарифы Яндекса: %s не прочитались', target['url'])
            failure = str(error)[:200] or 'Ошибка разбора страницы'
        with db._get_cursor() as cursor:
            status = store_tariffs(cursor, target['id'], data=data, digest=digest,
                                   error=failure, space_id=target['space_id'])
        summary['checked'] += 1
        if status == 'changed':
            summary['changed'] += 1
        elif status == 'error':
            summary['errors'] += 1
    return summary
