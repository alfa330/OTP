"""SQL учёта имущества (ТЗ «Закуп и оплата», пп. 10–12). Только запросы.

Имущество появляется из заявки: товар с категорией учёта «имущество,
подлежащее учёту» после получения ставится на учёт подзадачей «Постановка
имущества на учёт» (п. 10.2). Дальше карточка живёт своей жизнью: имущество
передают, перевозят, списывают — заявка к этому времени давно закрыта.

Два правила п. 12.

* **Структурно, а не текстом.** Город, подразделение, ответственный, место
  эксплуатации и инвентарный номер — отдельные колонки: по ним строится
  инвентаризация и профиль сотрудника.
* **История не перезаписывается.** Любая смена владельца, места или статуса —
  новая строка `payment_asset_moves` с «было» и «стало»; сама карточка хранит
  только текущее состояние.
"""

from . import workflow
from .sqlutil import NOW_SQL, columns, like_pattern, row_map

_ASSET_FIELDS = ('id', 'request_id', 'name', 'category_id', 'serial_number', 'inventory_number',
                 'received_on', 'cost', 'legal_entity_id', 'city', 'department_id', 'department_name',
                 'responsible_user_id', 'responsible_name', 'location', 'status', 'note',
                 'created_at', 'updated_at')
_ASSET_WRITE = ('name', 'category_id', 'serial_number', 'inventory_number', 'received_on', 'cost',
                'legal_entity_id', 'city', 'department_id', 'department_name', 'responsible_user_id',
                'responsible_name', 'location', 'status', 'note')
_ASSET_SQL = """
    SELECT %s, cat.name, le.name
      FROM payment_assets a
      LEFT JOIN payment_asset_categories cat ON cat.id = a.category_id
      LEFT JOIN payment_legal_entities le ON le.id = a.legal_entity_id
""" % columns('a', _ASSET_FIELDS)


def _asset_row(row):
    item = row_map(_ASSET_FIELDS, row[:len(_ASSET_FIELDS)])
    item['category_name'] = row[len(_ASSET_FIELDS)]
    item['legal_entity_name'] = row[len(_ASSET_FIELDS) + 1]
    item['status_label'] = workflow.ASSET_STATUS_LABELS.get(item['status'], item['status'])
    return item


def list_assets(cursor, *, query=None, city=None, department_id=None, responsible_user_id=None,
                status=None, category_id=None, legal_entity_id=None, request_id=None, limit=200, offset=0):
    clauses, params = [], {}
    if query:
        params['like'] = like_pattern(query.strip())
        clauses.append("(a.name ILIKE %(like)s OR a.inventory_number ILIKE %(like)s "
                       "OR COALESCE(a.serial_number, '') ILIKE %(like)s "
                       "OR COALESCE(a.responsible_name, '') ILIKE %(like)s)")
    for key, column, value in (
        ('city', 'a.city', city),
        ('status', 'a.status', status),
    ):
        if value:
            params[key] = value
            clauses.append('%s = %%(%s)s' % (column, key))
    for key, column, value in (
        ('department_id', 'a.department_id', department_id),
        ('responsible_user_id', 'a.responsible_user_id', responsible_user_id),
        ('category_id', 'a.category_id', category_id),
        ('legal_entity_id', 'a.legal_entity_id', legal_entity_id),
        ('request_id', 'a.request_id', request_id),
    ):
        if value:
            params[key] = int(value)
            clauses.append('%s = %%(%s)s' % (column, key))
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    sql = _ASSET_SQL.replace('SELECT ', 'SELECT COUNT(*) OVER () AS total, ', 1) + where
    sql += ' ORDER BY a.id DESC'
    if limit:
        params['limit'] = int(limit)
        params['offset'] = int(offset or 0)
        sql += ' LIMIT %(limit)s OFFSET %(offset)s'
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    total = int(rows[0][0]) if rows else 0
    return total, [_asset_row(row[1:]) for row in rows]


def for_request(cursor, request_id):
    return list_assets(cursor, request_id=request_id, limit=0)[1]


def of_user(cursor, user_id):
    """Имущество, числящееся за сотрудником сейчас, — для его профиля (п. 12).
    Списанное не показывается: за человеком оно уже не числится."""
    cursor.execute(
        _ASSET_SQL + " WHERE a.responsible_user_id = %s AND a.status <> 'written_off' ORDER BY a.id DESC",
        (int(user_id),))
    return [_asset_row(row) for row in cursor.fetchall()]


def read_asset(cursor, asset_id, *, lock=False):
    cursor.execute(_ASSET_SQL + ' WHERE a.id = %s' + (' FOR UPDATE OF a' if lock else ''), (int(asset_id),))
    row = cursor.fetchone()
    return _asset_row(row) if row else None


def inventory_taken(cursor, inventory_number, *, exclude_id=None):
    """Занят ли инвентарный номер другой карточкой (без учёта регистра)."""
    cursor.execute(
        "SELECT id, name FROM payment_assets WHERE lower(inventory_number) = lower(%s) AND id <> %s LIMIT 1",
        (str(inventory_number or '').strip(), int(exclude_id or 0)))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1]} if row else None


def create_asset(cursor, *, request_id, fields, actor):
    """Заводит карточку имущества и первую строку её истории — «поставлено на учёт»."""
    values = [fields.get(col) for col in _ASSET_WRITE]
    cursor.execute(
        "INSERT INTO payment_assets (request_id, %s, created_by) VALUES (%%s, %s, %%s) RETURNING id"
        % (', '.join(_ASSET_WRITE), ', '.join(['%s'] * len(_ASSET_WRITE))),
        [request_id] + values + [(actor or {}).get('id')])
    asset_id = cursor.fetchone()[0]
    _log_move(cursor, asset_id, 'registered', actor, before={}, after=fields,
              comment='Поставлено на учёт')
    return asset_id


def update_details(cursor, asset_id, fields):
    """Правит описательные поля карточки (название, категория, серийный и
    инвентарный номер, стоимость, дата получения, компания, примечание).
    Владельца, место и статус меняет только `move` — с записью в историю."""
    cols = [col for col in ('name', 'category_id', 'serial_number', 'inventory_number', 'received_on',
                            'cost', 'legal_entity_id', 'note') if col in fields]
    if not cols:
        return
    cursor.execute(
        "UPDATE payment_assets SET %s, updated_at = %s WHERE id = %%s"
        % (', '.join('%s = %%s' % col for col in cols), NOW_SQL),
        [fields[col] for col in cols] + [int(asset_id)])


MOVE_KEYS = ('responsible_user_id', 'responsible_name', 'department_id', 'department_name', 'city',
              'location', 'status')


def move(cursor, asset_id, *, fields, actor, comment=None, attachment_id=None):
    """Перемещение имущества: новый ответственный, подразделение, город, место
    или статус. Карточка получает новое состояние, история — строку «было → стало».
    Возвращает False, если ничего не изменилось."""
    current = read_asset(cursor, asset_id, lock=True)
    if not current:
        return False
    after = {key: fields.get(key, current.get(key)) for key in MOVE_KEYS}
    if all(after[key] == current.get(key) for key in MOVE_KEYS):
        return False
    cursor.execute(
        "UPDATE payment_assets SET %s, updated_at = %s WHERE id = %%s"
        % (', '.join('%s = %%s' % key for key in MOVE_KEYS), NOW_SQL),
        [after[key] for key in MOVE_KEYS] + [int(asset_id)])
    only_status = all(after[key] == current.get(key) for key in MOVE_KEYS if key != 'status')
    _log_move(cursor, asset_id, 'status' if only_status else 'moved', actor, before=current, after=after,
              comment=comment, attachment_id=attachment_id)
    return True


def _log_move(cursor, asset_id, kind, actor, *, before, after, comment=None, attachment_id=None):
    cursor.execute(
        """
        INSERT INTO payment_asset_moves (asset_id, kind, moved_by, moved_by_name,
            from_responsible_user_id, from_responsible_name, from_department_name, from_city,
            from_location, from_status,
            to_responsible_user_id, to_responsible_name, to_department_name, to_city, to_location,
            to_status, comment, attachment_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (int(asset_id), kind, (actor or {}).get('id'), (actor or {}).get('name'),
         before.get('responsible_user_id'), before.get('responsible_name'), before.get('department_name'),
         before.get('city'), before.get('location'), before.get('status'),
         after.get('responsible_user_id'), after.get('responsible_name'), after.get('department_name'),
         after.get('city'), after.get('location'), after.get('status'), comment, attachment_id))


_MOVE_FIELDS = ('id', 'asset_id', 'kind', 'moved_at', 'moved_by', 'moved_by_name',
                'from_responsible_user_id', 'from_responsible_name', 'from_department_name', 'from_city',
                'from_location', 'from_status', 'to_responsible_user_id', 'to_responsible_name',
                'to_department_name', 'to_city', 'to_location', 'to_status', 'comment', 'attachment_id')


def list_moves(cursor, asset_id):
    """История владельцев и мест — от новых к старым."""
    cursor.execute(
        "SELECT %s, att.file_name FROM payment_asset_moves m "
        "LEFT JOIN payment_attachments att ON att.id = m.attachment_id "
        "WHERE m.asset_id = %%s ORDER BY m.id DESC" % columns('m', _MOVE_FIELDS), (int(asset_id),))
    result = []
    for row in cursor.fetchall():
        item = row_map(_MOVE_FIELDS, row[:len(_MOVE_FIELDS)])
        item['attachment_name'] = row[len(_MOVE_FIELDS)]
        item['from_status_label'] = workflow.ASSET_STATUS_LABELS.get(item['from_status'])
        item['to_status_label'] = workflow.ASSET_STATUS_LABELS.get(item['to_status'])
        result.append(item)
    return result


def filter_values(cursor):
    """Города и подразделения, по которым уже есть имущество, — для фильтров реестра."""
    cursor.execute("SELECT DISTINCT city FROM payment_assets WHERE city IS NOT NULL ORDER BY city")
    cities = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        "SELECT DISTINCT department_id, department_name FROM payment_assets "
        "WHERE department_id IS NOT NULL ORDER BY department_name")
    departments = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
    return {'cities': cities, 'departments': departments}
