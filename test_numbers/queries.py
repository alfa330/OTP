"""SQL реестра тестовых номеров поверх готового курсора.

Транзакцией правит вызывающий (db._get_cursor). Журнал правок пишется в той же
транзакции, что и сама правка: номер без следа в журнале не появится.
"""

from parcels.queries import load_access_context  # noqa: F401 — один профиль на все разделы

from .keys import TABLE

_NOT_FIRED = "COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')"

_NUMBER_COLUMNS = """
    n.id, n.phone_key, n.phone_display, n.owner_user_id,
    o.name AS owner_name, od.name AS owner_department,
    COALESCE(o.status, 'working') IN ('fired', 'dismissal') AS owner_fired,
    n.created_by, c.name AS created_by_name, n.created_at
"""

_NUMBER_FROM = f"""
    FROM {TABLE} n
    LEFT JOIN users o ON o.id = n.owner_user_id
    LEFT JOIN departments od ON od.id = o.department_id
    LEFT JOIN users c ON c.id = n.created_by
"""


def _dicts(cursor):
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def list_numbers(cursor):
    cursor.execute(
        f"SELECT {_NUMBER_COLUMNS} {_NUMBER_FROM} "
        "ORDER BY o.name NULLS LAST, n.phone_key"
    )
    return _dicts(cursor)


def get_number(cursor, number_id):
    cursor.execute(f"SELECT {_NUMBER_COLUMNS} {_NUMBER_FROM} WHERE n.id = %s", (int(number_id),))
    rows = _dicts(cursor)
    return rows[0] if rows else None


def find_by_key(cursor, phone_key):
    cursor.execute(f"SELECT {_NUMBER_COLUMNS} {_NUMBER_FROM} WHERE n.phone_key = %s", (phone_key,))
    rows = _dicts(cursor)
    return rows[0] if rows else None


def people(cursor):
    """Действующие сотрудники для выбора владельца номера: ФИО и отдел."""
    cursor.execute(
        f"""
        SELECT u.id, u.name, d.name AS department_name
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE {_NOT_FIRED}
           AND btrim(COALESCE(u.name, '')) <> ''
         ORDER BY u.name, u.id
        """
    )
    return _dicts(cursor)


def active_person(cursor, user_id):
    """Сотрудник, которого можно сделать владельцем, или None."""
    cursor.execute(
        f"SELECT u.id, u.name FROM users u WHERE u.id = %s AND {_NOT_FIRED}",
        (int(user_id),),
    )
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1]} if row else None


def _log(cursor, kind, row, actor):
    cursor.execute(
        """
        INSERT INTO test_phone_number_events
            (kind, phone_key, phone_display, owner_user_id, owner_name, actor_user_id, actor_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (kind, row['phone_key'], row['phone_display'], row.get('owner_user_id'),
         row.get('owner_name'), actor.get('user_id'), actor.get('name')),
    )


def add_number(cursor, *, phone_key, phone_display, owner, actor):
    """Добавить номер. None — такой номер уже в реестре (его покажет вызывающий)."""
    cursor.execute(
        f"""
        INSERT INTO {TABLE} (phone_key, phone_display, owner_user_id, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (phone_key) DO NOTHING
        RETURNING id
        """,
        (phone_key, phone_display, owner['id'], actor['user_id'], actor['user_id']),
    )
    row = cursor.fetchone()
    if not row:
        return None
    created = get_number(cursor, row[0])
    _log(cursor, 'added', created, actor)
    return created


def change_owner(cursor, number_id, *, owner, actor):
    cursor.execute(
        f"""
        UPDATE {TABLE}
           SET owner_user_id = %s, updated_by = %s, updated_at = now()
         WHERE id = %s
        RETURNING id
        """,
        (owner['id'], actor['user_id'], int(number_id)),
    )
    if not cursor.fetchone():
        return None
    updated = get_number(cursor, number_id)
    _log(cursor, 'owner_changed', updated, actor)
    return updated


def remove_number(cursor, number_id, *, actor):
    existing = get_number(cursor, number_id)
    if not existing:
        return None
    cursor.execute(f"DELETE FROM {TABLE} WHERE id = %s", (int(number_id),))
    _log(cursor, 'removed', existing, actor)
    return existing
