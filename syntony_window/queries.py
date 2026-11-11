"""SQL окна Syntony поверх готового курсора. Ни Flask, ни сети.

Время — наивное время Алматы, как в остальных таблицах портала этого года
(schema._NOW). Наружу оно уходит строкой ISO без пояса.
"""

from . import rules
from .schema import _NOW

_WORKING = "COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')"

# Глава ДЕЙСТВУЮЩЕГО отдела: по нему правило QR отличает руководителя от рядового
# (назначение главой заменяет базовую роль — так считает весь портал).
_IS_HEAD = """EXISTS (SELECT 1 FROM departments hd
                      WHERE hd.head_user_id = u.id AND COALESCE(hd.is_active, TRUE))"""

_ACCESS_CONTEXT_SQL = """
    SELECT u.name, u.role, u.status, {is_head},
           (a.user_id IS NOT NULL), COALESCE(a.email, ''), COALESCE(a.link_state, '')
      FROM users u
      LEFT JOIN syntony_window_access a ON a.user_id = u.id
     WHERE u.id = %s
""".format(is_head=_IS_HEAD)


def load_access_context(cursor, user_id):
    """Профиль и строка доступа одним запросом. None — учётки нет."""
    cursor.execute(_ACCESS_CONTEXT_SQL, (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'user_id': int(user_id),
        'name': row[0] or '',
        'role': str(row[1] or '').strip().lower(),
        'status': row[2],
        'is_head': bool(row[3]),
        'granted': bool(row[4]),
        'email': row[5] or '',
        'link_state': row[6] or '',
    }


def _iso(value):
    return value.isoformat(timespec='seconds') if hasattr(value, 'isoformat') else None


_ITEM_SQL = """
    SELECT u.id, u.name, u.role, ({working}) AS working, d.name,
           a.email, a.granted_at, g.name, a.link_state, a.link_checked_at
      FROM syntony_window_access a
      JOIN users u ON u.id = a.user_id
      LEFT JOIN departments d ON d.id = u.department_id
      LEFT JOIN users g ON g.id = a.granted_by
""".format(working=_WORKING)


def _item(row):
    state = row[8] if row[8] in rules.LINK_STATES else rules.LINK_UNKNOWN
    return {
        'id': int(row[0]),
        'name': row[1] or '',
        'role': str(row[2] or '').strip().lower(),
        'working': bool(row[3]),
        'department': row[4] or '',
        'email': row[5] or '',
        'granted_at': _iso(row[6]),
        'granted_by': row[7] or '',
        'link_state': state,
        'link_checked_at': _iso(row[9]),
    }


def list_access(cursor):
    """Весь список доступа по алфавиту. Уволенные тоже: окно им уже не открыть, но
    строку видно, чтобы снять (и чтобы администратор Syntony снял связь у себя)."""
    cursor.execute(_ITEM_SQL + " ORDER BY lower(u.name), u.id")
    return [_item(row) for row in cursor.fetchall()]


def get_item(cursor, user_id):
    cursor.execute(_ITEM_SQL + " WHERE a.user_id = %s", (int(user_id),))
    row = cursor.fetchone()
    return _item(row) if row else None


def person(cursor, user_id):
    """Сотрудник для выдачи доступа: имя, работает ли, почта из карточки. None — нет учётки."""
    cursor.execute("""
        SELECT u.id, u.name, ({working}), COALESCE(u.email, '')
          FROM users u
         WHERE u.id = %s
    """.format(working=_WORKING), (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'name': row[1] or '', 'working': bool(row[2]),
            'card_email': rules.clean_email(row[3])}


def search_people(cursor, query, limit=30):
    """Кого можно добавить: работающие сотрудники, которых в списке ещё нет.

    card_email — рабочая почта из карточки сотрудника: окно предложит её как почту
    для Syntony, а человек, выдающий доступ, поправит, если учётная запись Syntony
    заведена на другой адрес.
    """
    text = ' '.join(str(query or '').split())
    params = {'limit': int(limit)}
    name_clause = ''
    if text:
        params['pattern'] = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        name_clause = 'AND (u.name ILIKE %(pattern)s OR u.login ILIKE %(pattern)s)'
    cursor.execute("""
        SELECT u.id, u.name, d.name, COALESCE(u.email, '')
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE {working}
           AND NOT EXISTS (SELECT 1 FROM syntony_window_access a WHERE a.user_id = u.id)
           {name_clause}
         ORDER BY lower(u.name), u.id
         LIMIT %(limit)s
    """.format(working=_WORKING, name_clause=name_clause), params)
    return [{'id': int(row[0]), 'name': row[1] or '', 'department': row[2] or '',
             'card_email': rules.clean_email(row[3])}
            for row in cursor.fetchall()]


def email_owner(cursor, email, except_user_id):
    """Чья это почта в списке (кроме самого человека) — имя или None.

    Одна почта — один сотрудник Syntony: двух операторов iCORE на неё не связать.
    """
    if not email:
        return None
    cursor.execute("""
        SELECT u.name
          FROM syntony_window_access a
          JOIN users u ON u.id = a.user_id
         WHERE a.email = %s AND a.user_id <> %s
         LIMIT 1
    """, (email, int(except_user_id)))
    row = cursor.fetchone()
    return (row[0] or 'другого сотрудника') if row else None


def grant(cursor, user_id, email, actor_id):
    """Добавить человека в список. False — он там уже есть (строка не тронута)."""
    cursor.execute("""
        INSERT INTO syntony_window_access (user_id, email, granted_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING
        RETURNING user_id
    """, (int(user_id), email or '', int(actor_id) if actor_id is not None else None))
    return cursor.fetchone() is not None


def set_email(cursor, user_id, email):
    """Сменить почту. Вернёт None (человека нет в списке), False (та же) или True.

    Новая почта — новая личность для Syntony: прежний ответ о связи к ней не
    относится, поэтому состояние сбрасывается в «не спрашивали».
    """
    cursor.execute("SELECT email FROM syntony_window_access WHERE user_id = %s FOR UPDATE",
                   (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    if (row[0] or '') == (email or ''):
        return False
    cursor.execute("""
        UPDATE syntony_window_access
           SET email = %s, link_state = %s, link_checked_at = NULL
         WHERE user_id = %s
    """, (email or '', rules.LINK_UNKNOWN, int(user_id)))
    return True


def revoke(cursor, user_id):
    """Убрать из списка. Вернёт почту снятой строки или None, если строки не было."""
    cursor.execute("DELETE FROM syntony_window_access WHERE user_id = %s RETURNING email",
                   (int(user_id),))
    row = cursor.fetchone()
    return (row[0] or '') if row else None


def set_link_state(cursor, user_id, state):
    """Записать, что Syntony ответила о связи. Строки нет (доступ успели снять) — молча."""
    cursor.execute("""
        UPDATE syntony_window_access
           SET link_state = %s, link_checked_at = {now}
         WHERE user_id = %s
    """.format(now=_NOW), (state, int(user_id)))
    return cursor.rowcount > 0


def log(cursor, action, *, user_id, user_name, email, actor_id, actor_name):
    cursor.execute("""
        INSERT INTO syntony_window_access_log (action, user_id, user_name, email, actor_id, actor_name)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (action, int(user_id), (user_name or '')[:255], (email or '')[:255],
          int(actor_id) if actor_id is not None else None, (actor_name or '')[:255]))
