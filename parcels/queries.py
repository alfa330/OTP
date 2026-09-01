"""SQL-слой раздела «Посылки».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены wiki, crm и
call_qa.

Почему это важно именно здесь: смена статуса меняет две таблицы сразу (карточку
и её историю). Если бы каждая функция брала своё соединение, в реестре мог бы
стоять новый статус, а в истории — не остаться следа, кто его поставил. Один
курсор = одна транзакция = карточка и история всегда согласованы.
"""

import json
from datetime import date, datetime, timedelta

from . import access
from .schema import PARCEL_STATUSES

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Смещение Алматы от UTC. Render живёт в UTC, и «сегодня» у него до 06:00 по
# Алматы ещё вчерашнее — на дате приёма это видно сразу. Считаем сдвигом, а не
# ZoneInfo: у Казахстана с 01.03.2024 одна зона без перевода часов, а tzdata на
# контейнере может и отсутствовать.
_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id, city
      FROM users
     WHERE id = %(user_id)s
),
headed AS (
    SELECT d.id, d.code FROM departments d
     WHERE d.head_user_id = %(user_id)s AND d.is_active
)
SELECT
    (SELECT name          FROM me),
    (SELECT role          FROM me),
    (SELECT department_id FROM me),
    (SELECT d.code FROM departments d WHERE d.id = (SELECT department_id FROM me)),
    (SELECT city          FROM me),
    COALESCE((SELECT array_agg(id)   FROM headed), '{}'),
    COALESCE((SELECT array_agg(code) FROM headed), '{}')
"""


def load_access_context(cursor, user_id):
    """Профиль + периметр одним запросом.

    Групп здесь нет намеренно, в отличие от «Обращений»: реестр посылок общий на
    оба отдела, и членство в группе к правам ничего не добавляет.
    """
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, city, headed, headed_codes = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'city': city,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Справочник офисов — читаем вики, своего не заводим
# ─────────────────────────────────────────────────────────────────────────────
#
# Берём только НАШИ офисы: `kind = 'park'`. В справочнике рядом лежат
# партнёрские точки («Брендирование Алматы», «Офис Яндекса для водителей»,
# «Аренда Авто») — посылку водителя там не оставляют, а в списке они превращали
# бы «офис спрашиваем, только если их несколько» в «спрашиваем всегда».
#
# `no_office` отсекаем по той же причине: «Жанаозен онлайн» — это запись о том,
# что офиса в городе НЕТ. Положить в него посылку физически некуда.

# `space_id` — с 24.08.2026 справочник принадлежит ПРОСТРАНСТВУ вики
# (wiki.schema._scope_directories_to_space), и без этого условия в списке офисов
# формы стоял «Tez Taxi» в Туркестане: точка пространства «Тез», на которую
# фронт-офис Таксопарков посылку не принимает. Пространства берём у отделов
# раздела (section_space_ids), а не у смотрящего: справочник у раздела один и
# тот же для фронт-офиса, СЗоВ и глобального админа без отдела.
_OFFICES_SQL = """
    SELECT o.id, o.city, o.name, o.address, o.address_note, o.phone
      FROM wiki_offices o
     WHERE o.status = 'active'
       AND o.kind = 'park'
       AND o.no_office = FALSE
       AND o.space_id = ANY(%(spaces)s)
       AND COALESCE(NULLIF(TRIM(o.city), ''), '') <> ''
       AND {extra}
     ORDER BY o.city, o.position, o.name
"""


def _office_row(row):
    return {
        'id': row[0],
        'city': row[1],
        'name': row[2],
        'address': row[3],
        'address_note': row[4],
        'phone': row[5],
    }


def section_space_ids(cursor):
    """Пространства вики, справочником офисов которых пользуется раздел.

    Отделы раздела заданы жёстко (access.SECTION_DEPARTMENT_CODES), так что список
    не зависит от того, кто смотрит: посылку принимает фронт-офис, а ищет её СЗоВ,
    и офис у них один и тот же. Кешировать нельзя — границу правит конструктор
    пространств, и раздел обязан узнать об этом сразу.
    """
    from wiki import structure as wiki_structure

    return wiki_structure.space_ids_for_departments(cursor, access.SECTION_DEPARTMENT_CODES)


def _offices(cursor, extra='TRUE', params=None, *, space_ids):
    """Пустой space_ids — пустой ответ, а не «все»: отделу раздела не выдано ни
    одного пространства вики, и подставлять вместо границы «все» значит вернуть
    ту же утечку под другим именем. Форма на пустой справочник отвечает понятно
    («нет офисов в справочнике — заведите офис в разделе «Вики»»), так что
    молчаливо неверного ответа отсюда не выйдет."""
    space_ids = [int(x) for x in (space_ids or [])]
    if not space_ids:
        return []
    params = dict(params or {})
    params['spaces'] = space_ids
    cursor.execute(_OFFICES_SQL.format(extra=extra), params)
    return [_office_row(row) for row in cursor.fetchall()]


def list_offices(cursor, *, space_ids):
    """Города и офисы для выпадающих списков формы.

    Отдаём плоским списком с городом в каждой записи: сгруппировать его во фронте
    дешевле, чем держать здесь вторую форму тех же данных.
    """
    return _offices(cursor, space_ids=space_ids)


def read_office(cursor, office_id, *, space_ids):
    """Один офис — чтобы снять с него имя и адрес в карточку посылки."""
    found = _offices(cursor, 'o.id = %(id)s', {'id': int(office_id)}, space_ids=space_ids)
    return found[0] if found else None


def offices_in_city(cursor, city, *, space_ids):
    """Офисы города. По ним же решается, спрашивать офис или подставить сам."""
    return _offices(cursor, 'LOWER(TRIM(o.city)) = LOWER(TRIM(%(city)s))',
                    {'city': str(city or '')}, space_ids=space_ids)


# ─────────────────────────────────────────────────────────────────────────────
# Реестр
# ─────────────────────────────────────────────────────────────────────────────

# Поля карточки в порядке выборки. Список ОДИН: и SELECT, и разбор строки
# собираются из него, поэтому колонку нельзя добавить в запрос и забыть в
# разборе — ровно это и случилось 25.08.2026, когда `order_url` и
# `driver_park_id` дописали в середину SELECT, а читали по индексам с конца:
# статус поехал на место ссылки, а в `status_changed_at` попал id парка, и прод
# ответил «'str' object has no attribute 'isoformat'». Позиционная раскладка на
# тридцати колонках — это ошибка, которая ждёт своего часа, а не случайность.
_PARCEL_FIELDS = (
    'id', 'received_on', 'city', 'office_id', 'office_name', 'office_address',
    'driver_account_id', 'driver_name', 'driver_phone', 'driver_park',
    'driver_park_id', 'driver_license', 'driver_callsign', 'driver_car',
    'driver_synced_at', 'kind', 'description', 'sender', 'recipient',
    'order_url', 'order_number', 'status', 'status_changed_at',
    'status_changed_by', 'status_changed_by_name', 'comment',
    'created_by', 'created_by_name', 'created_at', 'updated_at',
)

_PARCEL_COLUMNS = ', '.join('p.%s' % name for name in _PARCEL_FIELDS)

# Что отдаётся строкой ISO, а не как есть: jsonify превратил бы datetime в
# RFC 1123 с английским месяцем.
_PARCEL_DATE_FIELDS = frozenset({
    'received_on', 'driver_synced_at', 'status_changed_at', 'created_at', 'updated_at',
})


def _iso(value):
    return value.isoformat() if value is not None else None


def _parcel_row(row):
    return {
        name: (_iso(value) if name in _PARCEL_DATE_FIELDS else value)
        for name, value in zip(_PARCEL_FIELDS, row)
    }


# Поля, по которым ищет оператор. Ровно восемь из ТЗ: ID курьера, телефон
# курьера, ФИО, номер заказа, отправитель, получатель, город, офис. Плюс
# описание — им пользуются, когда про водителя ничего не помнят, а «синюю
# коробку» помнят.
_SEARCH_COLUMNS = (
    'p.driver_account_id', 'p.driver_phone', 'p.driver_name', 'p.driver_callsign',
    # По заказу ищут, вставляя его id: он лежит внутри ссылки, поэтому «содержит»
    # по самой ссылке и есть поиск по заказу. `order_number` остался ради
    # записей первой версии формы — заново он не заполняется.
    'p.order_url', 'p.order_number', 'p.sender', 'p.recipient', 'p.city',
    'p.office_name', 'p.description',
)


def _search_clause(query, params):
    """Условие поиска «содержит» по всем полям сразу.

    Телефон ищем ещё и по одним цифрам: в базе он лежит как «+77719736925», а
    человек диктует «8 771 973-69-25». Без этого поиск по телефону — самый
    частый в разделе — не находил бы ничего, и это выглядело бы как «посылки
    нет».
    """
    text = str(query or '').strip()
    if not text:
        return None
    params['q'] = '%%%s%%' % text.replace('%', r'\%').replace('_', r'\_')
    clauses = ['%s ILIKE %%(q)s' % column for column in _SEARCH_COLUMNS]

    digits = ''.join(ch for ch in text if ch.isdigit())
    if len(digits) >= 5:
        # 8 707… и +7 707… — один и тот же номер: сравниваем последние девять
        # цифр, они у казахстанского номера уникальны и не зависят от формы.
        params['q_digits'] = '%%%s%%' % digits[-9:]
        clauses.append(
            "regexp_replace(COALESCE(p.driver_phone, ''), '[^0-9]', '', 'g') ILIKE %(q_digits)s"
        )
    return '(%s)' % ' OR '.join(clauses)


def _filter_clause(params, *, query=None, status=None, city=None, office_id=None,
                   manager_id=None, date_from=None, date_to=None):
    """Условие WHERE реестра. Одно на список, счётчики и выгрузку.

    Вынесено в общее место после задачи #257: раньше отбор был выписан дважды —
    в списке и в счётчиках, — и третья копия в выгрузке означала бы, что файл
    рано или поздно начнёт содержать не то, что человек видит на экране. Именно
    это обещание («в файл не попадёт ничего, чего человек не видит») и держит
    здесь один набор условий.

    Пишет в переданный `params`, а не заводит свой: вызывающий кладёт туда же
    `limit`/`offset`.
    """
    where = ['TRUE']

    search = _search_clause(query, params)
    if search:
        where.append(search)
    if status:
        params['status'] = list(status) if isinstance(status, (list, tuple)) else [status]
        where.append('p.status = ANY(%(status)s)')
    if city:
        params['city'] = str(city)
        where.append('LOWER(TRIM(p.city)) = LOWER(TRIM(%(city)s))')
    if office_id:
        params['office_id'] = int(office_id)
        where.append('p.office_id = %(office_id)s')
    if manager_id:
        params['manager_id'] = int(manager_id)
        where.append('p.created_by = %(manager_id)s')
    if date_from:
        params['date_from'] = date_from
        where.append('p.received_on >= %(date_from)s')
    if date_to:
        params['date_to'] = date_to
        where.append('p.received_on <= %(date_to)s')

    return ' AND '.join(where)


def list_parcels(cursor, *, query=None, status=None, city=None, office_id=None,
                 manager_id=None, date_from=None, date_to=None,
                 limit=50, offset=0):
    """Реестр с фильтрами ТЗ: Все/статус, Город → Офис → Дата → Менеджер.

    Периметра «чьи посылки» нет и быть не может: реестр общий, ради того его и
    заводили — оператор СЗоВ должен найти посылку, принятую чужим городом.
    Границей служит вход в раздел (access.can_open_section).
    """
    params = {'limit': max(1, min(int(limit or 50), 200)), 'offset': max(0, int(offset or 0))}
    condition = _filter_clause(
        params, query=query, status=status, city=city, office_id=office_id,
        manager_id=manager_id, date_from=date_from, date_to=date_to,
    )

    cursor.execute(
        """
        SELECT %s
          FROM parcels p
         WHERE %s
         ORDER BY p.received_on DESC, p.id DESC
         LIMIT %%(limit)s OFFSET %%(offset)s
        """ % (_PARCEL_COLUMNS, condition),
        params,
    )
    items = [_parcel_row(row) for row in cursor.fetchall()]

    cursor.execute('SELECT COUNT(*) FROM parcels p WHERE %s' % condition, params)
    total = int((cursor.fetchone() or [0])[0])
    return items, total


def status_counters(cursor, *, query=None, city=None, office_id=None,
                    manager_id=None, date_from=None, date_to=None):
    """Сколько посылок в каждом статусе при ТЕКУЩИХ фильтрах.

    Считается тем же условием, что и список, но без фильтра по статусу: иначе
    сегмент «В офисе» показывал бы «в офисе: N», а остальные — нули, и легенда
    перестала бы отвечать на вопрос «а сколько уже отдали».
    """
    params = {}
    condition = _filter_clause(
        params, query=query, city=city, office_id=office_id,
        manager_id=manager_id, date_from=date_from, date_to=date_to,
    )

    cursor.execute(
        'SELECT p.status, COUNT(*) FROM parcels p WHERE %s GROUP BY p.status' % condition,
        params,
    )
    found = {row[0]: int(row[1]) for row in cursor.fetchall()}
    counters = {code: found.get(code, 0) for code in PARCEL_STATUSES}
    counters['all'] = sum(counters.values())
    return counters


def parcels_for_export(cursor, *, query=None, status=None, city=None, office_id=None,
                       manager_id=None, date_from=None, date_to=None, limit=20000):
    """Весь отбор целиком — для выгрузки в xlsx (задача #257).

    Отдельная функция, а не `list_parcels(limit=…)`: у списка потолок страницы
    200 строк и он там намеренный (человек листает), а выгрузке нужен весь
    отбор. Условие отбора при этом ОДНО И ТО ЖЕ — `_filter_clause`, — иначе
    файл содержал бы не то, что видно на экране.

    Возвращает (строки, сколько их в реестре всего). Второе число нужно, чтобы
    честно сказать на листе «Контекст», что потолок сработал: молча обрезанный
    файл читается как полный.
    """
    params = {'limit': max(1, int(limit or 20000))}
    condition = _filter_clause(
        params, query=query, status=status, city=city, office_id=office_id,
        manager_id=manager_id, date_from=date_from, date_to=date_to,
    )

    # Порядок тот же, что в списке: человек ожидает увидеть в файле те же строки
    # в том же порядке, что и на экране.
    cursor.execute(
        """
        SELECT %s
          FROM parcels p
         WHERE %s
         ORDER BY p.received_on DESC, p.id DESC
         LIMIT %%(limit)s
        """ % (_PARCEL_COLUMNS, condition),
        params,
    )
    items = [_parcel_row(row) for row in cursor.fetchall()]

    cursor.execute('SELECT COUNT(*) FROM parcels p WHERE %s' % condition, params)
    total = int((cursor.fetchone() or [0])[0])
    return items, total


def read_parcel(cursor, parcel_id):
    cursor.execute(
        'SELECT %s FROM parcels p WHERE p.id = %%(id)s' % _PARCEL_COLUMNS,
        {'id': int(parcel_id)},
    )
    row = cursor.fetchone()
    return _parcel_row(row) if row else None


def list_managers(cursor):
    """Кто заводил посылки — для фильтра «Менеджер».

    Список строим по РЕЕСТРУ, а не по составу отдела: в фильтре не должно быть
    людей, у которых ни одной записи, иначе выпадашка на два десятка фамилий
    почти вся ведёт в пустой список.
    """
    cursor.execute(
        """
        SELECT p.created_by,
               COALESCE(u.name, p.created_by_name) AS name,
               COUNT(*) AS parcels
          FROM parcels p
          LEFT JOIN users u ON u.id = p.created_by
         WHERE p.created_by IS NOT NULL
         GROUP BY p.created_by, COALESCE(u.name, p.created_by_name)
         ORDER BY name
        """
    )
    return [{'id': row[0], 'name': row[1], 'parcels': int(row[2])}
            for row in cursor.fetchall()]


def cities_in_use(cursor):
    """Города, в которых посылки уже есть — для фильтра.

    Тоже по реестру: города справочника, где посылок не было ни разу, в фильтре
    только мешают.
    """
    cursor.execute(
        'SELECT p.city, COUNT(*) FROM parcels p GROUP BY p.city ORDER BY p.city'
    )
    return [{'city': row[0], 'parcels': int(row[1])} for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Запись
# ─────────────────────────────────────────────────────────────────────────────

def _log_event(cursor, parcel_id, kind, actor, payload=None):
    cursor.execute(
        """
        INSERT INTO parcel_events (parcel_id, kind, actor_user_id, actor_name, payload)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (int(parcel_id), kind, actor.get('user_id'), actor.get('name'),
         json.dumps(payload or {}, ensure_ascii=False)),
    )


_INSERT_FIELDS = (
    'received_on', 'city', 'office_id', 'office_name', 'office_address',
    'driver_account_id', 'driver_name', 'driver_phone', 'driver_park',
    'driver_park_id', 'driver_license', 'driver_callsign', 'driver_car',
    'driver_info', 'driver_synced_at', 'kind', 'description', 'sender',
    'recipient', 'order_url', 'order_number', 'status', 'comment',
)


def create_parcel(cursor, *, fields, actor):
    """Заводит карточку и первую запись истории.

    `status_changed_at`/`status_changed_by` заполняются сразу: ТЗ показывает их
    в реестре у каждой строки, и «—» у только что заведённой посылки читалось бы
    как «статус не поставлен», хотя он поставлен — «В офисе».
    """
    values = {name: fields.get(name) for name in _INSERT_FIELDS}
    values['status'] = values.get('status') or 'in_office'
    driver_info = values.pop('driver_info', None)

    columns = list(values.keys()) + ['driver_info', 'status_changed_at',
                                     'status_changed_by', 'status_changed_by_name']
    placeholders = ['%%(%s)s' % name for name in values] + [
        '%(driver_info)s', _NOW, '%(actor_id)s', '%(actor_name)s',
    ]
    params = dict(values)
    params['driver_info'] = json.dumps(driver_info, ensure_ascii=False) if driver_info else None
    params['actor_id'] = actor.get('user_id')
    params['actor_name'] = actor.get('name')
    params['created_by'] = actor.get('user_id')
    params['created_by_name'] = actor.get('name')

    columns += ['created_by', 'created_by_name']
    placeholders += ['%(created_by)s', '%(created_by_name)s']

    cursor.execute(
        'INSERT INTO parcels (%s) VALUES (%s) RETURNING id'
        % (', '.join(columns), ', '.join(placeholders)),
        params,
    )
    parcel_id = cursor.fetchone()[0]
    _log_event(cursor, parcel_id, 'created', actor, {'status': values['status']})
    return read_parcel(cursor, parcel_id)


# Поля, которые правит форма. Статус сюда НЕ входит: у него свой роут и своя
# запись в истории — «кто изменил статус» это отдельный вопрос ТЗ, и мешать его
# с «поправил опечатку в описании» нельзя.
_EDITABLE_FIELDS = (
    'received_on', 'city', 'office_id', 'office_name', 'office_address',
    'driver_account_id', 'driver_name', 'driver_phone', 'driver_park',
    'driver_park_id', 'driver_license', 'driver_callsign', 'driver_car',
    'driver_info', 'driver_synced_at', 'kind', 'description', 'sender',
    'recipient', 'order_url', 'order_number', 'comment',
)

# Что показываем в истории правок. Служебный снимок CRM и производные от него
# поля человеку в ленте не нужны — он видит «водитель: было → стало».
_DIFF_LABELS = {
    'received_on': 'Дата приёма',
    'city': 'Город',
    'office_name': 'Офис',
    'driver_account_id': 'ID водителя',
    'driver_name': 'ФИО водителя',
    'driver_phone': 'Телефон водителя',
    'kind': 'Тип посылки',
    'description': 'Описание',
    'sender': 'Отправитель',
    'recipient': 'Получатель',
    'order_url': 'Ссылка на заказ',
    'order_number': 'Номер заказа',
    'comment': 'Комментарий',
}


def _comparable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def update_parcel(cursor, parcel_id, *, fields, actor):
    """Правит карточку и пишет в историю, ЧТО именно изменилось.

    Пустой набор изменений в историю не пишется: открыть форму и закрыть её —
    не событие, а строка «изменил карточку» без содержания только зашумляет
    ленту.
    """
    before = read_parcel(cursor, parcel_id)
    if not before:
        return None

    updates = {name: fields[name] for name in _EDITABLE_FIELDS if name in fields}
    if not updates:
        return before

    driver_info = updates.pop('driver_info', 'unchanged')
    assignments = ['%s = %%(%s)s' % (name, name) for name in updates]
    params = dict(updates)
    if driver_info != 'unchanged':
        assignments.append('driver_info = %(driver_info)s')
        params['driver_info'] = json.dumps(driver_info, ensure_ascii=False) if driver_info else None
    assignments.append('updated_at = %s' % _NOW)
    params['id'] = int(parcel_id)

    cursor.execute(
        'UPDATE parcels SET %s WHERE id = %%(id)s' % ', '.join(assignments),
        params,
    )
    after = read_parcel(cursor, parcel_id)

    changes = []
    for name, label in _DIFF_LABELS.items():
        was, now = _comparable(before.get(name)), _comparable(after.get(name))
        if was != now:
            changes.append({'field': name, 'label': label, 'from': was, 'to': now})
    if changes:
        _log_event(cursor, parcel_id, 'edited', actor, {'changes': changes})
    return after


def set_status(cursor, parcel_id, *, status, actor, comment=None):
    """Меняет статус, штампует «когда» и «кто» и пишет строку в историю.

    Повторная установка того же статуса не считается событием: дежурный
    нажимает «В офисе» на уже лежащей посылке, и лента из одинаковых строк
    ничего не добавляет к ответу на вопрос «кому её отдали».

    Тот же статус ВМЕСТЕ с комментарием — событие, но не смена статуса:
    в историю оно идёт как «комментарий», иначе лента печатала бы
    «Статус изменён: В офисе → В офисе», то есть неправду.
    """
    before = read_parcel(cursor, parcel_id)
    if not before:
        return None
    unchanged = before['status'] == status
    if unchanged and not comment:
        return before

    cursor.execute(
        """
        UPDATE parcels
           SET status = %%(status)s,
               status_changed_at = %s,
               status_changed_by = %%(actor_id)s,
               status_changed_by_name = %%(actor_name)s,
               comment = COALESCE(%%(comment)s, comment),
               updated_at = %s
         WHERE id = %%(id)s
        """ % (_NOW, _NOW),
        {
            'status': status,
            'actor_id': actor.get('user_id'),
            'actor_name': actor.get('name'),
            'comment': comment,
            'id': int(parcel_id),
        },
    )
    if unchanged:
        _log_event(cursor, parcel_id, 'comment', actor, {'comment': comment})
    else:
        _log_event(cursor, parcel_id, 'status', actor,
                   {'from': before['status'], 'to': status, 'comment': comment})
    return read_parcel(cursor, parcel_id)


def delete_parcel(cursor, parcel_id):
    cursor.execute('DELETE FROM parcels WHERE id = %s', (int(parcel_id),))
    return cursor.rowcount > 0


def list_events(cursor, parcel_id):
    """История карточки — от старого к новому, как её читают."""
    cursor.execute(
        """
        SELECT e.id, e.kind, e.actor_user_id,
               COALESCE(u.name, e.actor_name) AS actor_name,
               e.payload, e.created_at
          FROM parcel_events e
          LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.parcel_id = %s
         ORDER BY e.id
        """,
        (int(parcel_id),),
    )
    return [{
        'id': row[0],
        'kind': row[1],
        'actor_user_id': row[2],
        'actor_name': row[3],
        'payload': row[4] or {},
        'created_at': _iso(row[5]),
    } for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Фотографии вещи
#
# Ни одно из этих имён НЕ добавляется в _PARCEL_FIELDS, _INSERT_FIELDS,
# _EDITABLE_FIELDS и _DIFF_LABELS. Фотографии — отдельная таблица и отдельный
# ключ ответа; вписанные в список колонок карточки, они дали бы `p.photos` в
# SELECT, то есть 500 на списке, чтении, выгрузке и внутри update_parcel —
# ровно ту аварию с позиционной раскладкой, что уронила прод 25.08.2026.
# ─────────────────────────────────────────────────────────────────────────────

_PHOTO_FIELDS = (
    'id', 'bucket', 'blob_path', 'thumb_blob_path', 'content_type', 'file_size',
    'width', 'height', 'thumb_width', 'thumb_height', 'original_name',
    'sort_order', 'uploaded_by', 'uploaded_by_name', 'created_at',
)

# SELECT и разбор строки собираются из одного списка — по тому же уроку, что и
# у карточки: позиционная раскладка на пятнадцати колонках не случайность, а
# ошибка, которая ждёт своего часа.
_PHOTO_COLUMNS = ', '.join(_PHOTO_FIELDS)


def _photo_row(row):
    out = dict(zip(_PHOTO_FIELDS, row))
    out['id'] = str(out['id'])
    out['created_at'] = _iso(out['created_at'])
    return out


def list_photos(cursor, parcel_id):
    """Фотографии карточки в порядке показа. Без подписи — её ставит photos.sign_urls."""
    cursor.execute(
        'SELECT %s FROM parcel_photos WHERE parcel_id = %%s ORDER BY sort_order, id'
        % _PHOTO_COLUMNS,
        (int(parcel_id),),
    )
    return [_photo_row(row) for row in cursor.fetchall()]


def lock_parcel_photos(cursor, parcel_id):
    """Блокирует карточку и отдаёт число уже привязанных снимков. None — карточки нет.

    Одним движением решаются три задачи: есть ли родитель, каким будет
    sort_order и не выберут ли лимит две вкладки разом. Последнее без
    блокировки НЕ работает: на READ COMMITTED счётчик не видит незакоммиченных
    вставок соседней транзакции, и обе вкладки при девяти снимках увидели бы
    девять, а в базе оказалось бы одиннадцать.
    """
    cursor.execute('SELECT 1 FROM parcels WHERE id = %s FOR UPDATE', (int(parcel_id),))
    if not cursor.fetchone():
        return None
    cursor.execute('SELECT COUNT(*) FROM parcel_photos WHERE parcel_id = %s',
                   (int(parcel_id),))
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def insert_photo(cursor, parcel_id, *, prepared, bucket, blob_path, thumb_blob_path,
                 sort_order, actor):
    """Строка о снимке и событие истории — ОДНИМ курсором, то есть одной транзакцией.

    payload события — только идентификатор снимка. Лента отдаётся всем
    читателям раздела (list_events), и путям в бакете там не место.
    """
    cursor.execute(
        """
        INSERT INTO parcel_photos (parcel_id, bucket, blob_path, thumb_blob_path,
                                   content_type, file_size, width, height,
                                   thumb_width, thumb_height, original_name,
                                   sort_order, uploaded_by, uploaded_by_name)
        VALUES (%(parcel_id)s, %(bucket)s, %(blob_path)s, %(thumb_blob_path)s,
                %(content_type)s, %(file_size)s, %(width)s, %(height)s,
                %(thumb_width)s, %(thumb_height)s, %(original_name)s,
                %(sort_order)s, %(uploaded_by)s, %(uploaded_by_name)s)
        RETURNING id
        """,
        {
            'parcel_id': int(parcel_id),
            'bucket': bucket,
            'blob_path': blob_path,
            'thumb_blob_path': thumb_blob_path,
            'content_type': prepared.get('content_type'),
            'file_size': prepared.get('file_size') or 0,
            'width': prepared.get('width'),
            'height': prepared.get('height'),
            'thumb_width': prepared.get('thumb_width'),
            'thumb_height': prepared.get('thumb_height'),
            'original_name': prepared.get('original_name'),
            'sort_order': int(sort_order or 0),
            'uploaded_by': actor.get('user_id'),
            'uploaded_by_name': actor.get('name'),
        },
    )
    photo_id = str(cursor.fetchone()[0])
    _log_event(cursor, parcel_id, 'photo_added', actor, {'photo_id': photo_id})
    return photo_id


def delete_photo(cursor, parcel_id, photo_id, *, actor):
    """Снимает фотографию. Возвращает пары (бакет, путь) к удалению — или None.

    Условие по ОБОИМ ключам обязательно. Без parcel_id идентификатор снимка сам
    стал бы ключом доступа: правкой своей карточки можно было бы стереть
    фотографию из чужой.
    """
    cursor.execute(
        """
        DELETE FROM parcel_photos
              WHERE id = %s AND parcel_id = %s
          RETURNING bucket, blob_path, thumb_blob_path
        """,
        (str(photo_id), int(parcel_id)),
    )
    row = cursor.fetchone()
    if not row:
        return None
    _log_event(cursor, parcel_id, 'photo_removed', actor, {'photo_id': str(photo_id)})
    refs = [(row[0], row[1])]
    if row[2]:
        refs.append((row[0], row[2]))
    return refs


def photo_blob_refs(cursor, parcel_id):
    """Все файлы карточки: снять ДО удаления, стереть ПОСЛЕ коммита.

    delete_parcel про них не знает и наружу их не отдаёт, а строки унесёт
    каскад — значит собрать адреса можно только заранее.
    """
    cursor.execute(
        'SELECT bucket, blob_path, thumb_blob_path FROM parcel_photos WHERE parcel_id = %s',
        (int(parcel_id),),
    )
    refs = []
    for bucket, blob_path, thumb_path in cursor.fetchall():
        refs.append((bucket, blob_path))
        if thumb_path:
            refs.append((bucket, thumb_path))
    return refs
