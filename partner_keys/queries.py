# -*- coding: utf-8 -*-
"""SQL ключей партнёров и журнала вызовов. Ни Flask, ни сети.

Все запросы — по таблицам partner_keys / partner_key_calls (schema.py); из чужих
таблиц только departments и users справочно, через LEFT JOIN: удаление отдела
или учётки не должно ломать список ключей.
"""

import json
import uuid

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_KEY_COLUMNS = """
    k.id, k.name, k.key_mark, k.scopes, k.otp_role, k.department_id, d.name, d.code,
    k.created_by, creator.name, k.created_at, k.last_used_at, k.calls_count,
    k.revoked_at, k.revoked_by, revoker.name
"""
_KEY_JOINS = """
    FROM partner_keys k
    LEFT JOIN departments d ON d.id = k.department_id
    LEFT JOIN users creator ON creator.id = k.created_by
    LEFT JOIN users revoker ON revoker.id = k.revoked_by
"""
_KEY_FIELDS = ('id', 'name', 'mark', 'scopes', 'otp_role', 'department_id', 'department_name',
               'department_code', 'created_by', 'created_by_name', 'created_at',
               'last_used_at', 'calls_count', 'revoked_at', 'revoked_by', 'revoked_by_name')


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, 'isoformat') else value


def _key(row):
    item = dict(zip(_KEY_FIELDS, row))
    for field in ('created_at', 'last_used_at', 'revoked_at'):
        item[field] = _iso(item[field])
    item['scopes'] = [str(code) for code in (item['scopes'] or [])]
    item['calls_count'] = int(item['calls_count'] or 0)
    item['active'] = item['revoked_at'] is None
    return item


def find_key(cursor, key_hash):
    """Ключ по хешу — любой, в том числе отозванный: отказ по revoked_at решает auth."""
    cursor.execute('SELECT ' + _KEY_COLUMNS + _KEY_JOINS + ' WHERE k.key_hash = %s',
                   (key_hash,))
    row = cursor.fetchone()
    return _key(row) if row else None


def list_keys(cursor):
    cursor.execute('SELECT ' + _KEY_COLUMNS + _KEY_JOINS
                   + ' ORDER BY (k.revoked_at IS NOT NULL), k.created_at DESC, k.id DESC')
    return [_key(row) for row in cursor.fetchall()]


def get_key(cursor, key_id):
    cursor.execute('SELECT ' + _KEY_COLUMNS + _KEY_JOINS + ' WHERE k.id = %s', (int(key_id),))
    row = cursor.fetchone()
    return _key(row) if row else None


def create_key(cursor, *, name, key_hash, key_mark, scopes, otp_role, department_id, created_by):
    cursor.execute(
        """
        INSERT INTO partner_keys (name, key_hash, key_mark, scopes, otp_role, department_id, created_by)
        VALUES (%(name)s, %(hash)s, %(mark)s, %(scopes)s, %(role)s, %(department)s, %(by)s)
        RETURNING id
        """,
        {'name': name, 'hash': key_hash, 'mark': key_mark, 'scopes': list(scopes),
         'role': otp_role, 'department': department_id, 'by': created_by},
    )
    return int(cursor.fetchone()[0])


def set_scopes(cursor, key_id, scopes):
    """True — области записаны; False — ключа нет или он отозван."""
    cursor.execute(
        'UPDATE partner_keys SET scopes = %(scopes)s WHERE id = %(id)s AND revoked_at IS NULL '
        'RETURNING id',
        {'scopes': list(scopes), 'id': int(key_id)},
    )
    return cursor.fetchone() is not None


def revoke_key(cursor, key_id, *, by):
    """True — отозван сейчас; False — не найден или уже отозван."""
    cursor.execute(
        """
        UPDATE partner_keys
           SET revoked_at = """ + _NOW + """, revoked_by = %(by)s
         WHERE id = %(id)s AND revoked_at IS NULL
        RETURNING id
        """,
        {'id': int(key_id), 'by': by},
    )
    return cursor.fetchone() is not None


def touch_key(cursor, key_id):
    cursor.execute(
        'UPDATE partner_keys SET last_used_at = ' + _NOW
        + ', calls_count = calls_count + 1 WHERE id = %s',
        (int(key_id),),
    )


def insert_call(cursor, *, key_id, scope, endpoint, outcome, http_status=None, latency_ms=None,
                summary=None, details=None, ip_address=None, user_agent=None, request_id=None):
    cursor.execute(
        """
        INSERT INTO partner_key_calls
               (key_id, request_id, scope, endpoint, ip_address, user_agent, outcome,
                http_status, latency_ms, summary, details)
        VALUES (%(key)s, %(request)s, %(scope)s, %(endpoint)s, %(ip)s, %(agent)s, %(outcome)s,
                %(status)s, %(latency)s, %(summary)s, %(details)s)
        RETURNING id
        """,
        {'key': int(key_id), 'request': str(request_id or uuid.uuid4()),
         'scope': str(scope or '')[:32], 'endpoint': str(endpoint or '')[:120],
         'ip': (str(ip_address)[:64] if ip_address else None),
         'agent': (str(user_agent)[:300] if user_agent else None),
         'outcome': str(outcome or 'ok')[:16], 'status': http_status, 'latency': latency_ms,
         'summary': (str(summary)[:2000] if summary else None),
         'details': json.dumps(details, ensure_ascii=False) if details is not None else None},
    )
    return int(cursor.fetchone()[0])


_CALL_FIELDS = ('id', 'request_id', 'created_at', 'scope', 'endpoint', 'ip_address', 'user_agent',
                'outcome', 'http_status', 'latency_ms', 'summary', 'details')


def list_calls(cursor, *, key_id, limit, offset):
    """(строки, всего) — страница журнала ключа от новых к старым."""
    cursor.execute(
        """
        SELECT id, request_id, created_at, scope, endpoint, ip_address, user_agent,
               outcome, http_status, latency_ms, summary, details
          FROM partner_key_calls
         WHERE key_id = %(key)s
         ORDER BY created_at DESC, id DESC
         LIMIT %(limit)s OFFSET %(offset)s
        """,
        {'key': int(key_id), 'limit': int(limit), 'offset': int(offset)},
    )
    items = []
    for row in cursor.fetchall():
        item = dict(zip(_CALL_FIELDS, row))
        item['created_at'] = _iso(item['created_at'])
        item['request_id'] = str(item['request_id']) if item['request_id'] else None
        if isinstance(item['details'], str):
            try:
                item['details'] = json.loads(item['details'])
            except ValueError:
                item['details'] = None
        items.append(item)
    cursor.execute('SELECT count(*) FROM partner_key_calls WHERE key_id = %s', (int(key_id),))
    total = cursor.fetchone()
    return items, int(total[0] if total else 0)


def calls_last_day(cursor):
    """{key_id: вызовов за последние сутки} — для списка ключей."""
    cursor.execute(
        """
        SELECT key_id, count(*)
          FROM partner_key_calls
         WHERE created_at >= """ + _NOW + """ - INTERVAL '1 day'
         GROUP BY key_id
        """
    )
    return {int(key_id): int(count) for key_id, count in cursor.fetchall()}


def cleanup_calls(cursor, days):
    """Удалить записи старше срока. Возвращает число удалённых."""
    cursor.execute(
        'DELETE FROM partner_key_calls WHERE created_at < ' + _NOW
        + " - (%s * INTERVAL '1 day')",
        (int(days),),
    )
    return cursor.rowcount or 0
