"""SQL-слой раздела «Термокороба».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Сохранение меняет две вещи сразу
— строку офиса и её историю, — и только один курсор гарантирует, что в
истории записано ровно то, что легло в строку.

Контекст доступа и справочник офисов — общие с «Посылками» и «Учётом воды»:
периметр отделов у разделов один (фронт-офисы и СЗоВ).
"""

import json
from datetime import date, datetime

from parcels import queries as parcels_queries

from . import rules

load_access_context = parcels_queries.load_access_context


def _iso(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Строки таблицы
# ─────────────────────────────────────────────────────────────────────────────

# Поля строки в порядке выборки. Список ОДИН: и SELECT, и разбор собираются из
# него — колонку нельзя добавить в запрос и забыть в разборе (так 25.08.2026
# поехала карточка «Посылок»).
ROW_FIELDS = (
    'id', 'office_id', 'city', 'name', 'address',
    'free_boxes', 'thermo_bags', 'used_boxes',
    'tariff', 'min_orders', 'period_days', 'deposit_tenge', 'special_condition',
    'is_active', 'version', 'updated_by_name', 'updated_at', 'created_at',
)

# Город, имя и адрес — живые из справочника; снимок — если офис из него удалили.
_LIVE = {
    'city': 'COALESCE(NULLIF(TRIM(o.city), \'\'), t.city)',
    'name': 'COALESCE(NULLIF(TRIM(o.name), \'\'), t.name)',
    'address': 'COALESCE(NULLIF(TRIM(o.address), \'\'), t.address)',
}

_ROW_COLUMNS = ', '.join(_LIVE.get(name, 't.%s' % name) for name in ROW_FIELDS)

# Справочник читается только в пространствах раздела (страж
# tests/test_wiki_directory_space.py): офис, уведённый в чужое пространство,
# живого адреса не отдаёт — строка показывает свой снимок. Параметр
# пространств поэтому первый в каждом запросе со строками.
_ROW_FROM = ('thermobox_offices t LEFT JOIN wiki_offices o '
             'ON o.id = t.office_id AND o.space_id = ANY(%s)')

_ORDER = 'ORDER BY %s, %s, t.id' % (_LIVE['city'], _LIVE['name'])


def _row(raw):
    row = dict(zip(ROW_FIELDS, raw))
    row['updated_at'] = _iso(row['updated_at'])
    row['created_at'] = _iso(row['created_at'])
    return row


def section_spaces(cursor):
    """Пространства вики раздела — один раз на запрос, дальше аргументом."""
    return [int(x) for x in (parcels_queries.section_space_ids(cursor) or [])]


def list_rows(cursor, *, spaces, include_hidden=False):
    cursor.execute(
        'SELECT %s FROM %s %s %s' % (
            _ROW_COLUMNS, _ROW_FROM, '' if include_hidden else 'WHERE t.is_active', _ORDER),
        (list(spaces),))
    return [_row(raw) for raw in cursor.fetchall()]


def read_row(cursor, row_id, *, spaces):
    cursor.execute('SELECT %s FROM %s WHERE t.id = %%s' % (_ROW_COLUMNS, _ROW_FROM),
                   (list(spaces), int(row_id)))
    raw = cursor.fetchone()
    return _row(raw) if raw else None


def lock_rows(cursor, row_ids, *, spaces):
    """Строки пачки, запертые до конца транзакции, — {id: строка}.

    Запираем в порядке id: две пачки с общими строками ждут друг друга, а не
    берут их крест-накрест (взаимная блокировка). FOR UPDATE OF t — запирается
    только своя таблица, справочник вики остаётся свободным.
    """
    ids = sorted({int(x) for x in row_ids})
    if not ids:
        return {}
    cursor.execute(
        'SELECT %s FROM %s WHERE t.id = ANY(%%s) ORDER BY t.id FOR UPDATE OF t' % (_ROW_COLUMNS, _ROW_FROM),
        (list(spaces), ids))
    return {row['id']: row for row in (_row(raw) for raw in cursor.fetchall())}


def update_row(cursor, row_id, fields, actor):
    """Пишет поля строки, поднимает версию и метку «кто и когда»."""
    names = [name for name in rules.EDITABLE_FIELDS if name in fields]
    assignments = ['%s = %%s' % name for name in names]
    params = [fields[name] for name in names]
    assignments += [
        'version = version + 1', 'updated_by = %s', 'updated_by_name = %s',
        "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')",
    ]
    params += [actor['user_id'], actor.get('name'), int(row_id)]
    cursor.execute('UPDATE thermobox_offices SET %s WHERE id = %%s' % ', '.join(assignments), params)


def set_active(cursor, row_id, active, actor):
    cursor.execute(
        "UPDATE thermobox_offices SET is_active = %s, version = version + 1, updated_by = %s, "
        "updated_by_name = %s, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') "
        "WHERE id = %s",
        (bool(active), actor['user_id'], actor.get('name'), int(row_id)))


def create_row(cursor, office, fields, actor):
    """Новая строка таблицы — офис из справочника и его условия. Возвращает id."""
    values = dict(rules.NEW_ROW_DEFAULTS)
    values.update(fields)
    names = list(rules.EDITABLE_FIELDS)
    cursor.execute(
        'INSERT INTO thermobox_offices (office_id, city, name, address, %s, updated_by, updated_by_name) '
        'VALUES (%%s, %%s, %%s, %%s, %s, %%s, %%s) RETURNING id' % (
            ', '.join(names), ', '.join(['%s'] * len(names))),
        [office['id'], office['city'], office['name'], office.get('address')]
        + [values[name] for name in names]
        + [actor['user_id'], actor.get('name')])
    return cursor.fetchone()[0]


def taken_office_ids(cursor):
    cursor.execute('SELECT office_id FROM thermobox_offices WHERE office_id IS NOT NULL')
    return {row[0] for row in cursor.fetchall()}


def directory(cursor, *, spaces):
    """Офисы фронт-офисов из справочника вики — те же, что у «Посылок» и «Воды»."""
    return parcels_queries.list_offices(cursor, space_ids=spaces)


# ─────────────────────────────────────────────────────────────────────────────
# История строки
# ─────────────────────────────────────────────────────────────────────────────

def insert_event(cursor, row_id, kind, changes, actor):
    cursor.execute(
        'INSERT INTO thermobox_events (row_id, kind, changes, actor_user_id, actor_name) '
        'VALUES (%s, %s, %s::jsonb, %s, %s)',
        (int(row_id), kind, json.dumps(changes, ensure_ascii=False), actor['user_id'], actor.get('name')))


def list_events(cursor, row_id, limit=100):
    """История строки, свежие сверху. Имя автора — текущее из users, а снимок —
    если учётку удалили."""
    cursor.execute(
        'SELECT e.id, e.kind, e.changes, COALESCE(u.name, e.actor_name), e.created_at '
        'FROM thermobox_events e LEFT JOIN users u ON u.id = e.actor_user_id '
        'WHERE e.row_id = %s ORDER BY e.created_at DESC, e.id DESC LIMIT %s',
        (int(row_id), int(limit)))
    return [{
        'id': raw[0],
        'kind': raw[1],
        'changes': _json_list(raw[2]),
        'actor_name': raw[3],
        'created_at': _iso(raw[4]),
    } for raw in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Памятка
# ─────────────────────────────────────────────────────────────────────────────

def get_memo(cursor):
    cursor.execute('SELECT title, items, updated_by_name, updated_at FROM thermobox_memo WHERE id = 1')
    raw = cursor.fetchone()
    if not raw:
        return {'title': None, 'items': [], 'updated_by_name': None, 'updated_at': None}
    return {
        'title': raw[0],
        'items': [item for item in _json_list(raw[1]) if isinstance(item, dict)],
        'updated_by_name': raw[2],
        'updated_at': _iso(raw[3]),
    }


def save_memo(cursor, memo, actor):
    cursor.execute(
        'INSERT INTO thermobox_memo (id, title, items, updated_by, updated_by_name) '
        'VALUES (1, %s, %s::jsonb, %s, %s) '
        'ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, items = EXCLUDED.items, '
        'updated_by = EXCLUDED.updated_by, updated_by_name = EXCLUDED.updated_by_name, '
        "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')",
        (memo.get('title'), json.dumps(memo['items'], ensure_ascii=False),
         actor['user_id'], actor.get('name')))
    return get_memo(cursor)
