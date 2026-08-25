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

_OFFICES_SQL = """
    SELECT o.id, o.city, o.name, o.address, o.address_note, o.phone
      FROM wiki_offices o
     WHERE o.status = 'active'
       AND o.kind = 'park'
       AND o.no_office = FALSE
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


def _offices(cursor, extra='TRUE', params=None):
    cursor.execute(_OFFICES_SQL.format(extra=extra), params or {})
    return [_office_row(row) for row in cursor.fetchall()]


def list_offices(cursor):
    """Города и офисы для выпадающих списков формы.

    Отдаём плоским списком с городом в каждой записи: сгруппировать его во фронте
    дешевле, чем держать здесь вторую форму тех же данных.
    """
    return _offices(cursor)


def read_office(cursor, office_id):
    """Один офис — чтобы снять с него имя и адрес в карточку посылки."""
    found = _offices(cursor, 'o.id = %(id)s', {'id': int(office_id)})
    return found[0] if found else None


def offices_in_city(cursor, city):
    """Офисы города. По ним же решается, спрашивать офис или подставить сам."""
    return _offices(cursor, 'LOWER(TRIM(o.city)) = LOWER(TRIM(%(city)s))',
                    {'city': str(city or '')})


# ─────────────────────────────────────────────────────────────────────────────
# Реестр
# ─────────────────────────────────────────────────────────────────────────────

_PARCEL_COLUMNS = """
    p.id, p.received_on, p.city, p.office_id, p.office_name, p.office_address,
    p.driver_account_id, p.driver_name, p.driver_phone, p.driver_park,
    p.driver_license, p.driver_callsign, p.driver_car, p.driver_synced_at,
    p.kind, p.description, p.sender, p.recipient, p.order_number,
    p.status, p.status_changed_at, p.status_changed_by, p.status_changed_by_name,
    p.comment, p.created_by, p.created_by_name, p.created_at, p.updated_at
"""


def _iso(value):
    return value.isoformat() if value is not None else None


def _parcel_row(row):
    return {
        'id': row[0],
        'received_on': _iso(row[1]),
        'city': row[2],
        'office_id': row[3],
        'office_name': row[4],
        'office_address': row[5],
        'driver_account_id': row[6],
        'driver_name': row[7],
        'driver_phone': row[8],
        'driver_park': row[9],
        'driver_license': row[10],
        'driver_callsign': row[11],
        'driver_car': row[12],
        'driver_synced_at': _iso(row[13]),
        'kind': row[14],
        'description': row[15],
        'sender': row[16],
        'recipient': row[17],
        'order_number': row[18],
        'status': row[19],
        'status_changed_at': _iso(row[20]),
        'status_changed_by': row[21],
        'status_changed_by_name': row[22],
        'comment': row[23],
        'created_by': row[24],
        'created_by_name': row[25],
        'created_at': _iso(row[26]),
        'updated_at': _iso(row[27]),
    }


# Поля, по которым ищет оператор. Ровно восемь из ТЗ: ID курьера, телефон
# курьера, ФИО, номер заказа, отправитель, получатель, город, офис. Плюс
# описание — им пользуются, когда про водителя ничего не помнят, а «синюю
# коробку» помнят.
_SEARCH_COLUMNS = (
    'p.driver_account_id', 'p.driver_phone', 'p.driver_name', 'p.driver_callsign',
    'p.order_number', 'p.sender', 'p.recipient', 'p.city', 'p.office_name',
    'p.description',
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


def list_parcels(cursor, *, query=None, status=None, city=None, office_id=None,
                 manager_id=None, date_from=None, date_to=None,
                 limit=50, offset=0):
    """Реестр с фильтрами ТЗ: Все/статус, Город → Офис → Дата → Менеджер.

    Периметра «чьи посылки» нет и быть не может: реестр общий, ради того его и
    заводили — оператор СЗоВ должен найти посылку, принятую чужим городом.
    Границей служит вход в раздел (access.can_open_section).
    """
    params = {'limit': max(1, min(int(limit or 50), 200)), 'offset': max(0, int(offset or 0))}
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

    condition = ' AND '.join(where)

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
    where = ['TRUE']
    search = _search_clause(query, params)
    if search:
        where.append(search)
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

    cursor.execute(
        'SELECT p.status, COUNT(*) FROM parcels p WHERE %s GROUP BY p.status'
        % ' AND '.join(where),
        params,
    )
    found = {row[0]: int(row[1]) for row in cursor.fetchall()}
    counters = {code: found.get(code, 0) for code in PARCEL_STATUSES}
    counters['all'] = sum(counters.values())
    return counters


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
    'driver_license', 'driver_callsign', 'driver_car', 'driver_info',
    'driver_synced_at', 'kind', 'description', 'sender', 'recipient',
    'order_number', 'status', 'comment',
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
    'driver_license', 'driver_callsign', 'driver_car', 'driver_info',
    'driver_synced_at', 'kind', 'description', 'sender', 'recipient',
    'order_number', 'comment',
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
