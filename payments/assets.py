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
# `request_deleted` — заявка, из которой имущество появилось, в архиве «Удалённые» (п. 18):
# имущество живёт дальше, а ссылка на заявку открывается только администратору раздела.
_ASSET_SQL = """
    SELECT %s, cat.name, le.name, (rq.deleted_at IS NOT NULL)
      FROM payment_assets a
      LEFT JOIN payment_asset_categories cat ON cat.id = a.category_id
      LEFT JOIN payment_legal_entities le ON le.id = a.legal_entity_id
      LEFT JOIN payment_requests rq ON rq.id = a.request_id
""" % columns('a', _ASSET_FIELDS)


def _asset_row(row):
    item = row_map(_ASSET_FIELDS, row[:len(_ASSET_FIELDS)])
    item['category_name'] = row[len(_ASSET_FIELDS)]
    item['legal_entity_name'] = row[len(_ASSET_FIELDS) + 1]
    item['request_deleted'] = bool(row[len(_ASSET_FIELDS) + 2])
    item['status_label'] = workflow.ASSET_STATUS_LABELS.get(item['status'], item['status'])
    return item


def _where(*, query=None, city=None, department_id=None, responsible_user_id=None,
           status=None, category_id=None, legal_entity_id=None, request_id=None):
    """Условие отбора реестра имущества: (текст ` WHERE …` либо пусто, параметры)."""
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
    return ((' WHERE ' + ' AND '.join(clauses)) if clauses else ''), params


def status_counts(cursor, **filters):
    """Сколько имущества в каждом статусе по ТЕКУЩЕМУ отбору без учёта самого статуса —
    счётчики плиток над реестром: {'all': N, 'in_use': N, …}. Статус, которого нет, — ноль."""
    filters.pop('status', None)
    where, params = _where(**filters)
    cursor.execute('SELECT a.status, COUNT(*) FROM payment_assets a' + where + ' GROUP BY a.status', params)
    counts = {code: 0 for code, _label in workflow.ASSET_STATUSES}
    total = 0
    for status, count in cursor.fetchall():
        total += int(count or 0)
        if status in counts:
            counts[status] = int(count or 0)
    counts['all'] = total
    return counts


def list_assets(cursor, *, limit=200, offset=0, **filters):
    where, params = _where(**filters)
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


def issued_from_stock(cursor, asset_ids):
    """Какие из этих вещей склада Helpdesk IT уже выдал по IT-заявке: {id имущества: {request_id, at}}.

    Выдача из наличия (просьба владельца 04.11.2026) карточку имущества не трогает: передачу
    сотруднику по акту оформляет учёт имущества (п. 11), и до неё вещь числится «На складе».
    Чтобы её не выдали второй раз, склад показывает, по какой заявке её уже отдали, а выдача
    такой вещи по другой заявке не проходит (routes._stock_assets). Отметка гаснет, когда вещь
    передали: в истории после выдачи есть перемещение со сменой ответственного или статуса.
    Правка описания и перенос места внутри склада её не снимают — вещь от этого не выдана.
    Выдача по заявке из архива «Удалённые» (п. 18) вещь не держит — как раньше, когда удаление
    стирало заявку вместе с её историей: иначе склад ссылался бы на заявку, которой не видно."""
    ids = sorted({int(value) for value in asset_ids or () if value})
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT DISTINCT ON (x.asset_id) x.asset_id, e.request_id, e.created_at
          FROM payment_events e
          JOIN payment_requests er ON er.id = e.request_id AND er.deleted_at IS NULL
         CROSS JOIN LATERAL (
               SELECT (item->>'id')::int AS asset_id
                 FROM jsonb_array_elements(COALESCE(e.payload->'assets', '[]'::jsonb)) item) x
         WHERE e.kind = 'it_stock' AND x.asset_id = ANY(%s)
           AND NOT EXISTS (
               SELECT 1 FROM payment_asset_moves m
                WHERE m.asset_id = x.asset_id AND m.kind <> 'registered' AND m.moved_at > e.created_at
                  AND (m.to_responsible_user_id IS DISTINCT FROM m.from_responsible_user_id
                       OR m.to_status IS DISTINCT FROM m.from_status))
         ORDER BY x.asset_id, e.id DESC
        """,
        (ids,))
    return {row[0]: {'request_id': row[1], 'at': row[2]} for row in cursor.fetchall()}


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
    _log_move(cursor, asset_id, 'registered', actor, before={},
              after=dict(fields, legal_entity_name=_entity_name(cursor, fields.get('legal_entity_id'))),
              comment='Поставлено на учёт')
    return asset_id


def _entity_name(cursor, legal_entity_id):
    """Название компании — снимком в строку истории (п. 12)."""
    if not legal_entity_id:
        return None
    cursor.execute("SELECT name FROM payment_legal_entities WHERE id = %s", (int(legal_entity_id),))
    row = cursor.fetchone()
    return row[0] if row else None


def update_details(cursor, asset_id, fields, *, actor=None, current=None):
    """Правит описательные поля карточки (название, категория, серийный и
    инвентарный номер, стоимость, дата получения, компания, примечание).
    Ответственного, место и статус меняет только `move` — с записью в историю.

    Компания-владелец — тоже владелец (п. 12: «история владельцев … должна
    сохраняться, а не перезаписываться»): её смена ложится строкой истории
    `owner` с «было» и «стало». `current` — карточка до правки."""
    cols = [col for col in ('name', 'category_id', 'serial_number', 'inventory_number', 'received_on',
                            'cost', 'legal_entity_id', 'note') if col in fields]
    if not cols:
        return
    cursor.execute(
        "UPDATE payment_assets SET %s, updated_at = %s WHERE id = %%s"
        % (', '.join('%s = %%s' % col for col in cols), NOW_SQL),
        [fields[col] for col in cols] + [int(asset_id)])
    if current is not None and 'legal_entity_id' in fields \
            and fields['legal_entity_id'] != current.get('legal_entity_id'):
        after = dict(current, legal_entity_id=fields['legal_entity_id'],
                     legal_entity_name=_entity_name(cursor, fields['legal_entity_id']))
        _log_move(cursor, asset_id, 'owner', actor, before=current, after=after)


def attachment_in_history(cursor, attachment_id):
    """Файл — акт из истории имущества: на него ссылается строка перемещения. Такой акт
    открывает каждый, кто видит имущество (routes.attachment_download)."""
    cursor.execute("SELECT EXISTS (SELECT 1 FROM payment_asset_moves WHERE attachment_id = %s)",
                   (int(attachment_id),))
    row = cursor.fetchone()
    return bool(row and row[0])


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
    # Компания при перемещении не меняется — в строке истории она стоит и «было», и «стало».
    _log_move(cursor, asset_id, 'status' if only_status else 'moved', actor, before=current,
              after=dict(current, **after), comment=comment, attachment_id=attachment_id)
    return True


def _log_move(cursor, asset_id, kind, actor, *, before, after, comment=None, attachment_id=None):
    cursor.execute(
        """
        INSERT INTO payment_asset_moves (asset_id, kind, moved_by, moved_by_name,
            from_responsible_user_id, from_responsible_name, from_department_name, from_city,
            from_location, from_status, from_legal_entity_id, from_legal_entity_name,
            to_responsible_user_id, to_responsible_name, to_department_name, to_city, to_location,
            to_status, to_legal_entity_id, to_legal_entity_name, comment, attachment_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (int(asset_id), kind, (actor or {}).get('id'), (actor or {}).get('name'),
         before.get('responsible_user_id'), before.get('responsible_name'), before.get('department_name'),
         before.get('city'), before.get('location'), before.get('status'),
         before.get('legal_entity_id'), before.get('legal_entity_name'),
         after.get('responsible_user_id'), after.get('responsible_name'), after.get('department_name'),
         after.get('city'), after.get('location'), after.get('status'),
         after.get('legal_entity_id'), after.get('legal_entity_name'), comment, attachment_id))


_MOVE_FIELDS = ('id', 'asset_id', 'kind', 'moved_at', 'moved_by', 'moved_by_name',
                'from_responsible_user_id', 'from_responsible_name', 'from_department_name', 'from_city',
                'from_location', 'from_status', 'to_responsible_user_id', 'to_responsible_name',
                'to_department_name', 'to_city', 'to_location', 'to_status', 'comment', 'attachment_id',
                'from_legal_entity_id', 'from_legal_entity_name', 'to_legal_entity_id', 'to_legal_entity_name')


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
